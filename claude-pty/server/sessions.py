"""SessionManager：控制平面的核心（ADR 0004 / 0008）。

一 session = 一 container（ADR 0001）；dockerd 持有 PTY，此處只負責建立 / 登錄 / 終止。
**DB 是唯一仲裁者，不保留任何 in-memory 權威狀態**（ADR 0008）——registry、配額、port
全由 DB 交易仲裁，故單 worker 與多 worker 同樣正確，無需改寫。

瀏覽器看終端走 on-demand ttyd（ADR 0008；見 views.py），create 時**不**起 ttyd。
ttyd 與就緒偵測共用 docker-py 的 `attach_socket`（見下方 attach 段）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass

import docker
from sqlalchemy.exc import IntegrityError

from . import config
from . import auth as auth_mod
from .db import session_scope
from .models import (
    END_TERMINATED,
    STATUS_CREATING,
    STATUS_RUNNING,
    User,
    utcnow,
)
from .models import (
    Session as SessionRow,
)
from .models import SessionHistory

# entrypoint.sh 在 exec driver 前印出的標記（⚠ SYNC：dev-container/entrypoint.sh）。
# 有它就代表前置（選單/firewall/mitm）全數完成、driver 正要啟動——比辨識 CLI banner
# 可靠得多（banner 會隨版本改）。
DRIVER_MARKER = "__NCR_DRIVER_STARTING__"

# container 視為「session 仍在」的狀態；exited/dead/removing 視為結束。
ALIVE_STATES = frozenset({"running", "restarting", "paused", "created"})


def _close_socketio(sock) -> None:
    """關掉 docker-py 給的 attach wrapper **以及它底下真正的 socket**。

    ⚠ `attach_socket()` 回傳的是 `socket.SocketIO`，而 `SocketIO.close()` 只做
      `_decref_socketios()`——**它不關底層 fd**（docker-py 7.2.0 實測）。底層要等 CPython
      GC 收掉 docker-py 內部的參照環（sock._response → connection → sock）才會消失，
      而那是不定時的。

    這個「晚幾秒」曾讓整站停擺 5 小時（ADR 0019）：dockerd 持續往那條沒人讀的連線灌容器
    輸出，208KB 的 socket 緩衝一滿，dockerd 的 attach copier 就卡在寫我們這個 fd，連鎖
    讓該容器的 stdout broadcaster 抱著 mutex 死鎖——容器輸出全凍、`docker rm` 也一起卡住。
    高輸出的 TUI 實測約 100 秒就能填滿，所以「GC 早晚會收」不是安全的假設。

    ⚠ `_sock` 必須在 `sock.close()` **之前**取：wrapper 關閉時會把它設成 None。
    """
    raw = getattr(sock, "_sock", None)
    with suppress(Exception):
        sock.close()
    if raw is not None:
        with suppress(Exception):
            raw.close()          # 真正釋放 fd；dockerd 那側隨即收到 EPIPE 並自行收乾淨


def close_attach(sock) -> None:
    """關掉 attach socket，連它專屬的 docker client 一起收。

    只 close socket 是不夠的：那個 client 的連線池裡還留著被 hijack 的連線，GC 時會去
    flush 一個早就關掉的 fd。用獨立 client 之後把 client 一併關掉，才是真的收乾淨。

    ⚠ 已知殘留：stderr 仍偶爾會印一行 `Exception ignored ... ValueError: I/O operation
    on closed file`——那是 CPython 在 GC docker-py 內部的 HTTPResponse 時發出的，屬於
    「已忽略的例外」，不會傳播、不影響請求。真正的災情（worker 崩潰 → nginx 502）來自
    **共用** client 時污染到別的請求，那個已經沒有了：改用獨立 client 後，殘留只會留在
    自己那條連線上。要完全消掉得去碰 docker-py 的內部欄位，不值得為一行 stderr 冒險。
    """
    _close_socketio(sock)
    client = getattr(sock, "_claude_pty_client", None)
    if client is not None:
        with suppress(Exception):
            client.close()

def _discard_attach(sock, client) -> None:
    """attach 途中失敗時的清理：能關的都關掉，例外一律吞掉（我們正在處理另一個例外）。

    ⚠ 與 `close_attach()` 走同一支 `_close_socketio()`——這條失敗路徑同樣不能只關 wrapper，
      否則洩漏的 fd 一樣會把 dockerd 的 broadcaster 拖死（ADR 0019）。
    """
    if sock is not None:
        _close_socketio(sock)
    with suppress(Exception):
        client.close()


def _is_ready(logs: bytes) -> bool:
    """「就緒」的唯一定義：容器 log 出現 entrypoint 的 DRIVER_MARKER。

    ⚠ 列表與單筆查詢都必須用這一個函式。這個判定曾經只寫在 status() 裡，list() 另外
    寫了一份，於是同一個 session 在列表上顯示就緒、點進去卻不是。
    """
    return DRIVER_MARKER.encode() in logs


def stamp_ready_if_first(s, sid: str) -> int:
    """把 `ready_at` 蓋成現在，**只有第一次寫得進去**。回傳影響的列數。

    就緒是單調的：`WHERE ready_at IS NULL` 讓「檢查」與「寫入」在同一句 SQL 裡完成
    ——分成先讀再寫的話，兩個觀察者同時看到 NULL 就會各寫一次，後 commit 的覆蓋先偵測到
    的時間，量出來的啟動耗時反而變長。session 已被歸檔時這句是影響 0 列，不是錯誤。

    ⚠ **兩個觀察者是真的存在的**，這也是這支要獨立出來的原因：前台在偵測到的當下蓋
      （`SessionManager._stamp_ready`），reconciler 是背景補漏（`_stamp_ready_backstop`，
      給背景執行緒死掉的那些）。兩邊本來各寫一份一模一樣的 UPDATE，只要有一邊加了伴生
      欄位而另一邊沒跟，同一個 session 會因為「是誰先蓋的」而有不同的資料。

    ⚠ 收 `s` 而不是自己開 `session_scope`：reconciler 是在**一筆**交易裡連續蓋很多個
      sid 並累加列數，自己開 scope 會把那筆交易拆成 N 筆。交易邊界屬於呼叫端。
    """
    return s.query(SessionRow).filter(
        SessionRow.id == sid, SessionRow.ready_at.is_(None)
    ).update({SessionRow.ready_at: utcnow()}, synchronize_session=False)


@dataclass(frozen=True)
class Filters:
    """列表的篩選條件。兩張表（執行中 / 已結束）共用同一組語意。

    `None` 一律代表「不限」，不是「否」——三態的中間那一態。做成布林的話
    「沒有錄製」與「不管有沒有錄製」會塌成同一個值。

    時間範圍是**一個區間** `[since_at, until_at]`，兩端都可以省略。畫面上的「一週內」
    這種預設值由呼叫端換算成 since_at（見 app._filters_from_args）——查詢層只認絕對時刻，
    不必知道「幾天內」這種相對說法，自訂範圍與預設值因此走同一條路。
    比對哪一個時間欄由呼叫端決定：執行中的表比 created_at，已結束的表比 ended_at
    （「一週內」在已結束的語境下自然是「一週內結束的」）。

    ⚠ profile 的三項存在 `profile` 這個 JSON 欄位裡，所以是 JSON 取值比對。
      **現階段刻意不加索引**：
      sessions 只有數十列、history 數百列，全表掃在 SQLite 是微秒級。真的需要時補
      **運算式索引**即可（`CREATE INDEX ... ON sessions(json_extract(profile_json,'$.cli'))`，
      實測查詢計畫會走 `SEARCH ... USING INDEX`），那是一行 DDL，不改欄位、不搬資料。

    ⚠ **profile 裡沒有那個鍵的舊列，在「是」與「否」兩邊都查不到**（只有「不限」看得見）。
      json_extract 對缺鍵回 NULL，而 `NULL = 0` 與 `NULL = 1` 都不成立。這是刻意接受的
      語意：篩選問的是「這場的設定是什麼」，而一場根本沒有記錄該設定的 session，
      老實說就是「不知道」，硬歸到任何一邊都會說謊。
      會踩到的地方是 `session_history`（永久保存，最可能留著舊 schema 的列）；
      `_to_dict` 用 `row.profile or {}` 也說明這個欄位可以是 NULL。
      需要「把缺鍵當成 False」的話得改用 COALESCE，並且要先想清楚那對歷史資料是不是
      真的成立。
    """

    since_at: _dt.datetime | None = None
    until_at: _dt.datetime | None = None
    cli: str | None = None
    network: str | None = None
    capture: bool | None = None
    telemetry: bool | None = None

    def active(self) -> int:
        """目前生效幾個條件（畫面上的「篩選 · N」與清除鈕都靠它）。

        時間區間的兩端算**一個**條件：畫面上它就是一格「時間範圍」，自訂起迄時填了
        兩個欄位卻跳成 2，會讓人以為多套了一個看不見的條件。
        """
        n = sum(v is not None for v in (self.cli, self.network,
                                        self.capture, self.telemetry))
        return n + (self.since_at is not None or self.until_at is not None)

    def apply(self, q, model, date_col):
        """把條件套上查詢。`date_col` 是 since_days 要比對的欄位。"""
        if self.since_at is not None:
            q = q.filter(date_col >= self.since_at)
        if self.until_at is not None:
            q = q.filter(date_col <= self.until_at)
        # profile 的四項。欄位本身就是 JSON（見 models），直接取值即可。
        def field(key):
            return model.profile[key]

        if self.cli is not None:
            q = q.filter(field("cli").as_string() == self.cli)
        if self.network is not None:
            q = q.filter(field("network").as_string() == self.network)
        # ⚠ 布林一定要用 `.as_boolean()`，**不可以**拿 as_string() 去比 "true"/"false"：
        #   SQLite 的 json_extract 對 JSON 布林回**整數 0/1**，比 "false" 永遠 0 筆——
        #   畫面看起來像「一場都沒有」而不是報錯，最難發現的那種（2026-07-26 實測）。
        #   交給 SQLAlchemy 產生正確的比較（`... = 1`）。
        for key, want in (("capture", self.capture), ("telemetry", self.telemetry)):
            if want is not None:
                q = q.filter(field(key).as_boolean() == want)
        return q


def archive(sids, reason: str, actor: dict | None = None) -> int:
    """把 session 登錄搬進 `session_history` 後刪除，回傳實際歸檔的筆數（ADR 0010）。

    ⚠ 這是「session 結束」的唯一出口——任何直接 `s.delete(SessionRow)` 都會讓那段歷史
    憑空消失。搬檔與刪列在同一交易內完成，不會出現「刪了但沒留下紀錄」的中間狀態。

    `actor`：按下終止的那個人（`g.user`）。**只有人為終止才給**——reconciler 判定的
    exited / gone 沒有「誰」，硬填一個會讓歷史說謊。

    `immediate=True`：web worker 的 list() 對帳與 reconciler 會同時歸檔同一筆
    （前者判 gone、後者判 exited），沒有序列化的話兩邊都先讀到列、各寫一筆歷史，
    結束原因還取決於誰先寫。`session_history.session_id` 的 UNIQUE 是第二道保險。
    """
    sids = [sid for sid in sids if sid]
    if not sids:
        return 0
    # ⚠ 先收 ttyd 再歸檔：刪列會 cascade 掉 views，而 view 記錄是**唯一**記得那個 ttyd
    # pid 的地方。先刪列的話，若那個 ttyd 從頭到尾沒有 client 連上過（`-q` 就永遠不會
    # 觸發），它會活著卻沒有任何機制找得到——_clean_views 只走 views 列、_remove_orphans
    # 只管 container，沒有人依 port 或 process 掃描。那個 port 就此永久消失。
    # 放在這裡而不是各呼叫端：四個出口只有 list() 漏了，靠「每個呼叫端記得」擋不住。
    from .views import close_views
    for sid in sids:
        with suppress(Exception):
            close_views(sid)          # 等冪：沒有 view 就回 0
    try:
        return _archive_txn(sids, reason, actor)
    except IntegrityError:
        # UNIQUE 兜底擋下了：這批裡有人已被另一個 worker 歸檔。目標已達成，不是錯誤
        # ——尤其不該讓使用者按「終止」時看到 500。
        return 0


def _archive_txn(sids: list[str], reason: str, actor: dict | None = None) -> int:
    archived = 0
    with session_scope(immediate=True) as s:
        for sid in sids:
            row = s.get(SessionRow, sid)
            if row is None:
                continue
            s.add(SessionHistory(
                session_id=row.id,
                container_name=row.container_name,
                display_name=row.display_name,
                user_id=row.user_id,
                username=row.user.username if row.user else None,
                profile=row.profile,
                workdir=row.workdir,
                created_at=row.created_at,
                last_active_at=row.last_active_at,
                ready_at=row.ready_at,               # 沒帶就算不出「這場啟動花多久」
                cli_version=row.cli_version,         # 那場是用哪一版開起來的
                image_created_at=row.image_created_at,
                ended_at=utcnow(),
                ended_reason=reason,
                ended_by_user_id=actor["id"] if actor else None,
                ended_by_username=actor["username"] if actor else None,
            ))
            s.delete(row)      # cascade 連帶清掉其 views 記錄
            archived += 1
    return archived


def _slugify(name: str | None) -> str:
    """把使用者取的名字壓成 docker 容器名稱能接受的尾綴（`[a-zA-Z0-9][a-zA-Z0-9_.-]*`）。

    非法字元一律變 `-`，全被壓掉就回空字串＝不加尾綴。長度設限避免撞到名稱上限。
    """
    if not name:
        return ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return slug[:config.NAME_SLUG_MAX]


def parse_docker_time(raw: str | None) -> _dt.datetime | None:
    """docker 的 RFC3339 時間戳 → aware datetime；解不出來回 None。

    ⚠ **這是唯一一份。** 曾經有兩份：這裡與 `reconciler._age_seconds`，而兩份已經漂移
      ——那一份只認 `"+"` 來判斷有沒有時區偏移，於是 `-05:00` 會落到 else 分支被當成 UTC，
      整整差掉時差。目前不可達（daemon 一律回 `Z`），但 `_remove_orphans` 的寬限期就是靠
      它算的，而它解析失敗的 fallback 是「很舊」——真的錯起來會**安靜地提早把還在建立中的
      容器當孤兒刪掉**。

    兩個必須處理的細節：
      - docker 給的是**奈秒**精度（`2026-07-26T02:57:51.828567844Z`），而 `fromisoformat`
        只吃到微秒（6 位），多的要先截掉，否則整段 ValueError。
      - 時區偏移正負都有可能，不能只認 `+`。
    """
    m = re.match(r"^(.*?T[\d:]+)(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$", raw or "")
    if not m:
        return None
    frac = f".{m.group(2)[:6]}" if m.group(2) else ""
    tz = m.group(3) or "+00:00"
    try:
        return _dt.datetime.fromisoformat(
            f"{m.group(1)}{frac}{'+00:00' if tz == 'Z' else tz}")
    except ValueError:
        return None


def age_seconds(iso_ts: str) -> float:
    """docker 物件（container / network）建立至今幾秒。

    ⚠ 住在這裡而不是 reconciler：reconciler 用它判斷孤兒的寬限期，而 reconciler 已經
      import sessions，放那邊就得反向 import。

    ⚠ 解析失敗回 `inf`（＝很舊）。呼叫端一律是「夠舊才動它」，所以這個 fallback 的錯誤
      方向是**提早把還在建立中的容器當成半成品刪掉**，而且不會有任何錯誤訊息。它之所以
      還可以接受，只因為解析是**唯一一份**（`parse_docker_time`）且涵蓋 daemon 實際會給的
      格式；曾經有第二份漂掉的實作，那才是真正的風險（見 `parse_docker_time` 的說明）。
    """
    if not iso_ts:
        return 0.0
    parsed = parse_docker_time(iso_ts)
    if parsed is None:
        return float("inf")
    return (utcnow() - parsed).total_seconds()


def _is_creating_within_grace(row) -> bool:
    """`creating` 且尚在寬限期內＝container 正在起，還沒出現在 docker 是正常的。

    create() 先寫登錄列、才花數十秒起 container（restricted 要等 trivy DB + 套 iptables）。
    這段期間若被判定為 gone 而刪列，create() 回頭找不到自己的列會失敗，並反過來把剛起好的
    container 收掉（review B1，實測窗口 40s 起動 vs 30s 對帳週期）。
    """
    if row.status != STATUS_CREATING:
        return False
    return (utcnow() - row.created_at).total_seconds() < config.CREATING_GRACE


@dataclass
class Profile:
    """session 執行 profile（ADR 0006）。控制平面據此組出 entrypoint env + docker 能力。"""
    # ⚠ 預設一律引用 `config.DEFAULT_*`，**不要在這裡另寫一份字面值**。曾經兩邊各寫
    #   一份，然後 network 分岔了：dataclass 是 "unrestricted"、config 是 "restricted"
    #   ——而 config 那邊的註解白紙黑字寫著「安全預設應該是限制而非開放」。於是任何人
    #   在 server 端寫 `Profile()` 都會拿到一個可任意連外的容器，而且完全無聲
    #   （review 2026-07-25 抓到）。
    #
    # ⚠ 這個寫法依賴一條不變量：**`config.DEFAULT_*` 在 import 之後必須視為不可變**。
    #   三個地方讀它的時機其實都不同——`config` 在自己 import 時讀 env，這裡的欄位預設
    #   在 `sessions` import 時綁定（晚一步），而 `from_dict` 的 `d.get(k, config.X)`
    #   是每次呼叫才讀。它們一致的唯一原因是沒有人在中間改寫過那些常數。
    #   這件事值得明講，因為這個 codebase 確實有「import 後改 config」的習慣
    #   （測試裡的 `config.ENTRYPOINT = None` / `config.MOUNTS = {}` 就是），讀的人
    #   完全有理由以為 `DEFAULT_*` 也能那樣改。真的要改就得改成 `default_factory`。
    #   `test_profile_mapping` 有一條斷言在守這件事：`Profile()` 必須等於當下的
    #   `config.DEFAULT_*`——它比對的正是「import 時的快照」與「當下讀值」。
    cli: str = "claude"                        # 這套東西只驅動 claude 一種 CLI
    network: str = config.DEFAULT_NET          # restricted | unrestricted
    capture: bool = config.DEFAULT_CAPTURE
    telemetry: bool = config.DEFAULT_TELEMETRY
    # 模型與思考深度：`claude --model` / `--effort` 的合法別名（見 config.CLAUDE_MODELS）
    model: str = config.DEFAULT_MODEL
    effort: str = config.DEFAULT_EFFORT

    @classmethod
    def from_dict(cls, d: dict | None) -> Profile:
        d = d or {}
        return cls(
            cli=d.get("cli", "claude"),
            network=d.get("network", config.DEFAULT_NET),
            capture=_as_bool(d.get("capture"), config.DEFAULT_CAPTURE),
            telemetry=_as_bool(d.get("telemetry"), config.DEFAULT_TELEMETRY),
            model=d.get("model", config.DEFAULT_MODEL),
            effort=d.get("effort", config.DEFAULT_EFFORT),
        )

    def as_dict(self) -> dict:
        return {"cli": self.cli, "network": self.network,
                "capture": self.capture, "telemetry": self.telemetry,
                "model": self.model, "effort": self.effort}


def _stored_profile(profile: Profile) -> dict:
    """要寫進 DB 的那一份 profile：與送進容器的值同一份（build_run_kwargs 也讀它）。"""
    return profile.as_dict()


class SessionError(RuntimeError):
    pass


class SessionNotFound(SessionError):
    """找不到 session，或請求者無權存取（兩者刻意回同一種錯，不洩漏存在性）。"""


# ⚠ 這裡曾經有 `_require_credentials_mountpoint()`：憑證以前是以檔案掛進容器的，
#   巢狀 bind mount 在新版 runc（openat2 + securejoin）上落點不存在就 exit 125。
#   現在憑證走環境變數（CLAUDE_CODE_OAUTH_TOKEN，見 build_run_kwargs），整個檔案
#   掛載的問題類別連同那個函式一起消失。

_CLAUDE_BASE = {"cli": "claude", "cli_label": "Claude", "brand": "anthropic"}


def claude_credentials_state(user_id: int | None) -> dict:
    """這個人開 session 時，Claude Code 拿不拿得到登入憑證。

    憑證＝他自己貼進來的 setup-token（`claude setup-token` 的輸出，加密存 DB、開場時
    以環境變數交給容器）。**唯一來源**，控制平面不讀 host 上的任何憑證檔——「檔案在
    就順便用」是一條平常不走、出事才走、而且沒人測過的路徑。

    只有兩種狀態：已設定／未設定。token 的到期時刻**不可知**（它不揭露自己的壽命），
    所以沒有「剩 N 天」的預警——過期不會有任何預告，**症狀是開場失敗**（終端裡只會
    看到登入提示）。detail 把這件事講在前面，事到臨頭那句話就是操作指南。

    解不開（換過 SECRET_KEY）與沒設過**刻意同一種畫面**：對使用者的正確指示都是
    同一句「重新貼一次」。

    每次呼叫都重讀 DB，不快取：他剛在帳號頁貼完，招牌 15 秒內就該轉綠。
    """
    token = auth_mod.cli_token(user_id) if user_id is not None else None
    if token is None:
        return {**_CLAUDE_BASE, "ok": False, "state": "bad",
                "label": "Claude 未設定憑證", "stamps": [],
                "detail": "在 host 上執行 `claude setup-token`，把輸出貼到"
                          "帳號管理頁的「CLI 憑證」。沒有它，session 會以未登入狀態"
                          "啟動，開場只會看到登入提示。"}
    return {**_CLAUDE_BASE, "ok": True, "state": "ok",
            "label": "Claude 憑證已設定", "stamps": [],
            "detail": "token 過期不會有預告，症狀是**新開的 session 開場失敗**"
                      "（終端停在登入提示）。遇到就在 host 重跑 `claude setup-token`，"
                      "把新的貼回帳號管理頁。已在跑的 session 不受影響。"}


def credentials_state(user_id: int | None) -> dict:
    """憑證狀態（招牌徽章用）。形狀維持 {cli: state}，讀取端以 cli 為鍵。"""
    return {"claude": claude_credentials_state(user_id)}


def _guard_credentials(user_id: int | None) -> None:
    """沒設 token 就不要建 session。

    沒有憑證，claude 照樣起得來——只是登出狀態，終端裡停在登入提示，開不了場。
    在「建立」這一刻擋下來，錯誤訊息才有地方告訴人下一步是什麼；放行的話，同一個
    事實要到開了終端才發現，而那個畫面不會解釋原因。
    """
    state = claude_credentials_state(user_id)
    if state["ok"]:
        return
    raise SessionError(
        "尚未設定 Claude 憑證。請在 host 上執行 `claude setup-token`，"
        "把輸出貼到帳號管理頁的「CLI 憑證」再開。")


def ensure_system_user() -> int:
    """取得（必要時建立）預設的 system 使用者 id。

    sessions.user_id 為 NOT NULL FK，但真正的登入要到 ADR 0008 階段 4 才接上；在那之前
    所有 session 掛在這個 owner 下。password_hash 填不可用值（`!` 為 Unix 慣例的「停用」
    標記，argon2 驗證永遠不會通過），確保這個帳號無法被登入。
    """
    with session_scope() as s:
        user = s.query(User).filter_by(username=config.SYSTEM_USERNAME).one_or_none()
        if user is None:
            user = User(username=config.SYSTEM_USERNAME, password_hash="!", is_admin=True)
            s.add(user)
            s.flush()
        return user.id


def _write_json_atomic(path: str, payload: dict) -> None:
    """把 JSON 原子地放到 `path`（先寫暫存、fsync、再 replace 就位）。

    ⚠ **不可以 `open(path,"w")` 直接寫。** 讀這個檔的是容器裡的 CLI，而它對「半截 JSON」
      的反應不是報錯而是**當成全新安裝**——三道互動對話全部回來，最後那道預設停在
      「No, exit」，driver 送出的第一個 Enter 就把容器收掉。行程在 write 中途被 kill
      （OOM、重新部署）留下的半截檔，就會讓那個使用者從此每一場都這樣死。

    ⚠ 暫存檔名必須是**每次呼叫**唯一，不可以用 pid。控制平面是 threaded（gunicorn
      `--threads 8`），而 provision 跑在交易之外——同一個使用者同時開兩場 session 是完全
      正常的（配額預設 10）。兩條執行緒拿到的是同一個 pid，於是開同一個暫存檔、交錯
      寫入，然後各自 replace 就位——正好產生這個函式要防的半截檔。
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)    # 同目錄、同檔案系統 → POSIX 保證原子
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)       # 失敗不要留一地 .tmp-xxxx
        raise


def _claude_json_seed() -> dict:
    """第一次要寫進 per-user `.claude.json` 的內容。"""
    return {**config.CLAUDE_JSON_SEED,
            # 信任狀態是 per-project 的，key 就是容器內的 cwd。**用 config 的值組**，
            # 不可以寫死字面值。
            "projects": {config.WORKDIR: {"hasTrustDialogAccepted": True}}}


def provision_user_space(user_id: int, username: str) -> None:
    """備妥某個使用者的狀態空間（ADR 0016）。idempotent，每次建立 session 都會呼叫。

    **lazy 而不是建帳號時就建**：帳號早就存在了（這個功能是後來才加的），lazy 天生
    idempotent、不需要 backfill，而且「沒開過 session 的人不佔目錄」也比較乾淨。

      1. 建出要掛進去的目錄，**0700**。必須由我們建，不能讓 docker daemon 隱式
         建立——那樣在 Linux 上會是 root:root，容器內的 nathan(uid 1000) 寫不進去，
         症狀是 claude 起得來但什麼都存不下（同 trivy 快取目錄那個坑）。
         0700 是因為 `ncr/mitm/` 裡是**完整的 API 請求本文**（prompt 全文）；預設的 0755
         在多帳號的 host 上等於發給每一個本機使用者。
      2. 驗**擁有者**（見下）。
      3. 備妥 `.claude.json`：沒有就寫種子；壞掉就重寫；好的就只補缺的 WORKDIR 信任 key。

    ⚠ **擁有者標記不是形式**。目錄名是 `user-{id}`，而 id 是 DB 的 autoincrement——
      它只在**同一份 registry 的生命週期內**穩定。SQLite 檔（deploy/data/，不進版控）
      一旦遺失或重建，id 會從 1 重發，新的 user-1 就直接繼承前一個 user-1 的 transcript、
      persistent-data 與 mitm/ 裡的 prompt 全文。ADR 0010「帳號不能刪」擋得住活 DB 內的
      重用，擋不住換代。所以第一次 provision 時把 username 寫進 `owner.json`，之後每次
      比對；對不上就**拒絕開**並要人工處理——靜默地把別人的對話交出去比擋下來糟得多。

    ⚠ `.claude.json` 的三種狀態要分開處理，不能只有「有／沒有」：
      - **沒有** → 寫種子。
      - **壞掉或空的** → 也要重寫。舊版寫到一半被 kill 會留下這種檔，而「存在就跳過」
        會讓它永遠修不好——那個使用者從此每一場都撞 onboarding。
      - **好的** → 只補一件事：`projects` 裡缺當前 `config.WORKDIR` 的信任 key 就補上。
        WORKDIR 一改，**既有使用者**的檔案裡不會有新 cwd 的信任狀態，下一場全部撞信任
        對話——這不是「只有第一場會遇到」的問題。補寫是 read-modify-write，理論上會與
        容器內正在寫同一個檔的 claude 互相覆蓋，但只在「剛改過 WORKDIR」這個罕見窗口
        內才會發生，而且被覆蓋掉的是 numStartups 那類會自己長回來的東西。
    """
    if not config.MOUNTS:        # 測試隔離：不建任何東西（同 user_mounts）
        return
    root = config.user_space(user_id, host=False)
    for sub in ("claude", "persistent-data", "ncr"):
        os.makedirs(os.path.join(root, sub), mode=0o700, exist_ok=True)
    # ⚠ `makedirs(mode=...)` **只對它新建的那一層生效**，已經存在的目錄權限不會動。
    #   所以每一層都要明確 chmod——升級前用預設 0755 建出來的空間，否則會一直維持
    #   世界可讀，而 `mitm/` 裡是完整的 API 請求本文。
    for d in (root, *(os.path.join(root, x)
                      for x in ("claude", "persistent-data", "ncr"))):
        with suppress(OSError):
            os.chmod(d, 0o700)

    # ⚠ `username` 是**必要參數**，不給預設值。曾經是 `str | None = None`，而傳 None
    #   會讓下面整段擁有者驗證靜默跳過——那是這個函式最重要的一道防線，卻可以被一個
    #   省略的參數關掉，且簽章與呼叫端都看不出來。要它就一定要拿得出是誰。
    owner_path = os.path.join(root, "owner.json")
    try:
        with open(owner_path, encoding="utf-8") as f:
            owner = json.load(f)
    except FileNotFoundError:
        owner = None         # 真的還沒有人認領——這一種才可以蓋章
    except (OSError, ValueError) as e:
        # ⚠ **壞掉的標記不等於沒有標記。** 當成「還沒有擁有者」就會直接重新蓋章，
        #   把上一個人的 transcript、persistent-data 與 mitm/ 的 prompt 全文靜默
        #   交給現在這個帳號——那正是這個標記存在的理由。讀不出來就停下來問人。
        raise SessionError(
            f"{owner_path} 讀不出來（{e}）——在確認這個空間屬於誰之前不會繼續。"
            f"請人工檢查：內容還原得了就修好它，確定是要重新指派就把整個 "
            f"{root} 移走。") from e
    # ⚠ 「解析得出來」不等於「是我們寫的那個形狀」。內容是 `[]` 的話下面的
    #   `owner.get()` 會 AttributeError——那會變成 500，而不是這裡精心寫的
    #   SessionError。下面的 `.claude.json` 有這道 isinstance 護欄，這裡原本漏了。
    if owner is not None and not isinstance(owner, dict):
        raise SessionError(
            f"{owner_path} 的內容不是預期的物件（{type(owner).__name__}）——"
            f"在確認這個空間屬於誰之前不會繼續。請人工檢查後修好它，"
            f"或把整個 {root} 移走。")
    if owner is None:
        # ⚠ 「沒有標記」只有在**空間本身也是全新的**時候才可以認領。已經有 .claude.json
        #   就代表這個目錄有人用過（那個檔是第一次 provision 就會寫的），而標記卻不在
        #   ——那是升級前留下的空間，或有人手動動過。直接蓋章一樣是把別人的 transcript
        #   與 mitm 全文交出去，只是換一條路徑到達同一個壞結果。
        if os.path.exists(os.path.join(root, "claude", ".claude.json")):
            raise SessionError(
                f"{root} 裡已經有資料，卻沒有擁有者標記（owner.json）。在確認它屬於誰"
                f"之前不會繼續：確定是 {username!r} 的就手動補上標記，不是的話把整個"
                f"目錄移走。")
        _write_json_atomic(owner_path, {"user_id": user_id, "username": username})
    elif owner.get("username") != username:
        raise SessionError(
            f"{root} 是 {owner.get('username')!r} 的空間，但這個 session 的擁有者是 "
            f"{username!r}。這通常表示 registry 重建過、user id 被重新指派——"
            f"繼續下去會把別人的對話與 capture 交給現在這個帳號。請人工確認後"
            f"改名或移走那個目錄再試。")

    seed_path = os.path.join(root, "claude", ".claude.json")
    try:
        with open(seed_path, encoding="utf-8") as f:
            existing = json.load(f)
        if not isinstance(existing, dict):
            raise ValueError("不是物件")
    except (FileNotFoundError, OSError, ValueError):
        # 三種都寫種子，主體一樣所以合成一條：不存在（第一次）、空的、壞的。
        # 後兩種不是「使用者的狀態」而是上一次寫到一半的殘骸——當成狀態跳過的話，
        # 那個使用者從此每一場都撞 onboarding，而且永遠修不好。
        _write_json_atomic(seed_path, _claude_json_seed())
        return
    # ⚠ 這裡有**第四態**：內容是有效的 dict，但根本沒有 `projects` 鍵（或它不是 dict）。
    #   原本寫成 `if isinstance(projects, dict) and ...`，那個情況會靜靜地什麼都不做，
    #   信任 key 永遠補不上去。缺鍵就當成空的補進去。
    projects = existing.get("projects")
    if not isinstance(projects, dict):
        projects = existing["projects"] = {}
    if config.WORKDIR not in projects:
        projects[config.WORKDIR] = {"hasTrustDialogAccepted": True}
        _write_json_atomic(seed_path, existing)


def preflight() -> list[str]:
    """啟動自檢：回傳需要提醒使用者的問題清單。

    ⚠ **這支有副作用**：它會 `makedirs` per-user 空間的根目錄（ADR 0016）。那不是「檢查」
      該做的事，但必須有人做——不先建的話 dockerd 會在 bind mount 時把它建成 root:root，
      控制平面就寫不進去。放在這裡是因為它是啟動路徑上唯一跑得夠早的地方。

    最重要的一項是 entrypoint.sh 掛載——ADR 0006 的非互動 env-skip 就在那份檔案裡。
    掛不到時 session 會退回 image 內烘的舊版 entrypoint，**跳出互動選單卡住**，而且是
    靜默降級（2026-07-25 實測踩到：容器化後 _SELF_REPO_ROOT 推導成 "/"）。
    """
    problems = []
    # session 用的 docker network。**由這裡建，不是 compose。**
    #
    # ⚠ compose 的頂層 `networks:` 只會建立「有服務引用到」的那些——而這個 network 沒有
    #   任何服務用它（用它的是控制平面另外起的 session 容器）。宣告在 compose 裡看起來
    #   有人負責，實際上不會被建出來（2026-07-29 redeploy 後實測：`docker network ls`
    #   裡就是沒有它）。而 session 容器 join 一個不存在的 network 是**直接失敗**——
    #   `failed to set up container networking: network ... not found`。
    #   也就是說要到第一場 restricted session 才會發現，而那時看起來像 session 壞了。
    # ⚠ 放在啟動路徑而不是 create() 裡：每建一場多問一次 docker 不划算，而 network 一旦
    #   建好就不會自己消失。真被手動刪掉的話，下次重啟控制平面會補回來。
    try:
        _client = docker.from_env(timeout=config.DOCKER_TIMEOUT)
        # ⚠ 精確比對：docker 的 `names` filter 是**子字串**比對，撿回來還要對名字。
        _hits = _client.networks.list(names=[config.SESSION_NETWORK])
        if not any(n.name == config.SESSION_NETWORK for n in _hits):
            try:
                _client.networks.create(config.SESSION_NETWORK, driver="bridge")
                print(f"[claude-pty] 建立 session network：{config.SESSION_NETWORK}", flush=True)
            except docker.errors.APIError as e:
                # ⚠ 「已經有了」是**成功**，不是問題。compose 裡 control 與 reconciler
                #   同時啟動，兩邊都會看到 `None` 然後都去建——敗方原本會把 APIError 交給
                #   下面那條，於是一個完全健康的部署每次重啟都印一行「session network
                #   建立失敗——restricted 與 telemetry 的 session 都會開不起來」。
                #   那句話是假的，而假警報比沒有警報更糟：真的壞掉時沒有人會認真看。
                #   （下面那段講「別在 preflight 放有副作用的東西」，講的是同一場競態。）
                # ⚠ network 的衝突訊息是 `already exists`，與 container 的
                #   `already in use` 不同。
                if "already exists" not in str(e):
                    raise
    except Exception as e:  # noqa: BLE001 — 失敗也要講清楚，不可靜默
        problems.append(
            f"session network {config.SESSION_NETWORK} 確認/建立失敗（{type(e).__name__}）"
            f"——restricted 與 telemetry 的 session 都會開不起來。"
            f"手動補：docker network create {config.SESSION_NETWORK}")
    # ⚠ **這裡刻意沒有「位址池餘裕」的預先檢查。** 曾經寫過一版：啟動時試建 N 個 network
    #   再刪掉，建不出來就警告。它有兩個問題，而且都是自找的：
    #     · `preflight()` 在 **import `server.app` 時**就跑——每一個 web worker、reconciler、
    #       以及每一支 import 它的測試都會建了又刪，白白攪動一個**全機器共用**的資源。
    #     · compose 裡 control 與 reconciler **同時啟動**，兩邊搶建同名的探測 network，
    #       接著在 finally 裡互刪對方的。
    #   真正需要的訊息在**用完的那一刻**已經有了（建 network 失敗會把「池滿」與其他
    #   錯誤講清楚）。**在事情發生時講清楚，勝過事先猜一個數字。**
    if config.ENTRYPOINT is None and not os.path.isfile(config.ENTRYPOINT_SH_SELF):
        problems.append(
            f"找不到 {config.ENTRYPOINT_SH_SELF}——session 將使用 image 內烘的 entrypoint，"
            f"若該版本沒有 CLAUDE_PTY_* env-skip 就會停在互動選單。"
            f"容器化部署請設 CLAUDE_PTY_SELF_REPO_ROOT 指向掛進來的 repo 路徑。")
    # MOUNTS 的來源是 host 路徑，由 daemon 解讀；控制平面容器化後本來就看不到它們，
    # 故只在「HOST 與 SELF 相同」（非容器化）時檢查，否則會誤報。
    if config.MOUNTS and config.HOST_HOME == config._SELF_HOME:
        for host_path in config.MOUNTS:
            if not os.path.exists(host_path):
                problems.append(f"掛載來源不存在（session 內可能缺設定/憑證）：{host_path}")
    # per-user 空間的根目錄（ADR 0016）。這一個查的是 **SELF**——控制平面得自己在裡面
    # mkdir 與寫種子檔，所以不是「daemon 看得到就好」，是「我現在就要寫得進去」。
    # 建不出來的話每一次建立 session 都會失敗，而錯誤會出現在很後面（provision 拋出），
    # 開機就講清楚比較好。
    if config.MOUNTS:
        try:
            os.makedirs(config.SPACE_SELF, mode=0o700, exist_ok=True)
            if not os.access(config.SPACE_SELF, os.W_OK):
                raise PermissionError(config.SPACE_SELF)
        except OSError as e:
            problems.append(
                f"per-user 狀態空間不可寫（{config.SPACE_SELF}）：{e}。"
                f"每個 session 的 ~/.claude 都住在這底下（ADR 0016），"
                f"不能寫就一個 session 都建不起來。容器化部署請確認該路徑已掛進控制平面"
                f"且擁有者是 APP_UID，並以 CLAUDE_PTY_SPACE_SELF 指明容器內看到的路徑。")
        # 控制平面建目錄用的是**它自己**的 uid，session 容器裡的寫入者則是 nathan
        # （`config.SESSION_UID`，實測 1001 而不是直覺的 1000——見那個常數的說明）。
        # 兩者不同時 0700 的目錄容器就進不去：transcript 寫不下、種子讀不到，症狀是
        # 每一場都撞 onboarding 對話，而最後那道預設停在「No, exit」。
        # ⚠ 只在 Linux 上檢查：macOS Docker Desktop 的 virtiofs 會做 uid 對映，host 的
        #   501 與容器裡的 1001 本來就對得起來，在那邊喊是純噪音。
        if sys.platform == "linux" and os.getuid() != config.SESSION_UID:
            problems.append(
                f"控制平面以 uid {os.getuid()} 執行，但 session 容器內的寫入者是 "
                f"nathan(uid {config.SESSION_UID})。per-user 空間是 0700，uid 對不上時"
                f"容器進不去那些目錄——症狀是每一場都撞 onboarding 對話。"
                f"請把 deploy/.env 的 APP_UID 設成 {config.SESSION_UID} 重新 build。")
    if config.PAGE_SIZE_CLAMPED is not None:
        problems.append(
            f"CLAUDE_PTY_PAGE_SIZE={config.PAGE_SIZE_CLAMPED} 不在 1–{config.MAX_PAGE_SIZE} "
            f"之內，已夾成 {config.PAGE_SIZE}。不夾的話每一張列表都會回 400"
            f"（預設頁大小會去撞 MAX_PAGE_SIZE 的上限檢查）。")
    if config.SSH_AUTH_SOCK_HOST:
        # 這不是「設錯了」而是「你開了一個很大的權限」——開著是合法的，但每次啟動都要
        # 講一次：沒有租戶隔離，這把 agent 等於發給每一個能建立 session 的帳號（ADR 0012）。
        problems.append(
            f"SSH agent 轉發已開啟（{config.SSH_AUTH_SOCK_HOST} → "
            f"{config.SSH_AUTH_SOCK_BIND}）：每個 session 都能以你的身分認證任何信任該 key "
            f"的主機，且無法只給部分使用者。不需要就清掉 CLAUDE_PTY_SSH_AUTH_SOCK。")
        # 非容器化時（HOST==SELF）順手驗一下路徑真的在——容器化的話控制平面看不到 host
        # 路徑，硬查會誤報（同下方 MOUNTS 的理由）。
        if config.HOST_HOME == config._SELF_HOME and not os.path.exists(config.SSH_AUTH_SOCK_HOST):
            problems.append(
                f"CLAUDE_PTY_SSH_AUTH_SOCK={config.SSH_AUTH_SOCK_HOST} 不存在——"
                f"建立 session 會直接失敗（bind 來源不存在）。agent 沒起來？"
                f"socket 路徑每次登入可能不同，請確認 `echo $SSH_AUTH_SOCK`。")
    if config.BEHIND_PROXY and not config.COOKIE_SECURE:
        problems.append(
            "BEHIND_PROXY=1 但 COOKIE_SECURE=0：登入 cookie 不帶 Secure，若該入口是 HTTP "
            "或經未加密網路，cookie 可被側錄重放（review H6）。上 TLS 後請設 "
            "CLAUDE_PTY_COOKIE_SECURE=1；僅本機 loopback 測試可忽略此提醒。")
    return problems


class SessionManager:
    def __init__(self) -> None:
        # ⚠ 一定要給 timeout。docker-py 預設 60 秒，而「一顆容器卡住 → 每個呼叫等滿 60 秒
        #   → gunicorn 的 thread 全被吃光」正是 2026-07-27 那次全站停擺的機制（ADR 0013）。
        self._docker = docker.from_env(timeout=config.DOCKER_TIMEOUT)

    # --- 環境快照 -------------------------------------------------------------
    #
    # 兩個都是「回頭要查得出那場跑在什麼上面」用的。共同原則：**絕不可以害 session
    # 開不起來**——查不到就留 NULL，一列少一個中繼資料，遠好過因為一個 docker 查詢
    # 失敗而讓人開不了工作階段。

    def _image_created_at(self) -> _dt.datetime | None:
        """image 的打包時刻（docker image inspect 的 .Created）。

        解析走共用的 `parse_docker_time()`（那支的說明講了奈秒精度與時區偏移的坑）。
        回 aware datetime；寫進 DB 時 UtcDateTime 會統一換算成 UTC，所以來源是什麼時區
        都不影響（見 models.UtcDateTime）。
        """
        try:
            parsed = parse_docker_time(self._docker.images.get(config.IMAGE).attrs.get("Created"))
            # docker 對「沒有這個值」回的是 0001-01-01T00:00:00Z，不是空字串——
            # `parse_docker_time` 解得出來，存進去前端會排出「0001/01/01」這種假時刻。
            # 用一個明顯早於任何 image 的下限把它擋掉。
            return parsed if parsed is not None and parsed.year >= 2000 else None
        except Exception:      # noqa: BLE001 —— 見本區塊開頭：查不到就留白，不擋建立
            return None

    def _cli_version(self, container, cli: str) -> str | None:
        """容器裡的 CLI 版本。

        問二進位檔（`claude --version`）而不是解析畫面：TUI 的排版會改版，靠比對畫面
        文字遲早會靜靜地抓錯。輸出形如 `2.1.207 (Claude Code)`，原樣存。

        ⚠ 這是**啟動當下**的快照。CLI 會在容器內自我更新，跑久了可能與這裡不同——
          它回答的是「這場是用哪一版開起來的」，不是「現在是哪一版」。

        ⚠ 用**短 timeout 的獨立 client**，不共用 self._docker。本區塊開頭那條「絕不可以
          害 session 開不起來」防的是**失敗**，防不到**卡住**：docker-py 的預設 timeout
          是 60 秒（實測），而這支跑在建立 session 的關鍵路徑上。容器剛起來、node 冷啟動
          時 `claude --version` 本來就不快；真的卡住的話使用者要對著轉圈等一分鐘，畫面上
          還沒有任何訊息說在等什麼。`except Exception` 吞得掉錯誤，吞不掉延遲。
        """
        try:
            probe = docker.from_env(timeout=config.CLI_VERSION_TIMEOUT)
            code, out = probe.containers.get(container.id).exec_run(
                [cli, "--version"], demux=False)
            if code != 0 or not out:
                return None
            return out.decode("utf-8", "replace").strip().splitlines()[0][:64] or None
        except Exception:      # noqa: BLE001 —— 見本區塊開頭
            return None

    # --- 生命週期 -------------------------------------------------------------

    def create(self, rows: int = config.DEFAULT_ROWS,
               cols: int = config.DEFAULT_COLS,
               profile: Profile | None = None,
               user_id: int | None = None,
               display_name: str | None = None) -> dict:
        profile = profile or Profile.from_dict(None)
        user_id = user_id or ensure_system_user()
        if profile.cli == "claude":
            # 沒憑證就別開，見該函式。system 帳號也一樣要有 token——它沒有例外，
            # 只是沒有人幫它貼而已。
            _guard_credentials(user_id)
        sid = uuid.uuid4().hex[:12]
        # sid 永遠在 container 名稱裡（那是 nginx 路由與 attach 的錨），使用者取的名字
        # 只是接在後面讓 `docker ps` 認得出來——不取代 sid，也不參與任何比對。
        slug = _slugify(display_name)
        name = f"claude-pty-{sid}-{slug}" if slug else f"claude-pty-{sid}"
        # ⚠ 這裡曾經算過一個 `capture` 旗標，只為了決定要不要先 mkdir capture 的落盤目錄。
        #   ADR 0016 之後那個目錄是 per-user 空間的一部分，由 provision_user_space() 無條件
        #   建出來（不分 capture 開關——少一個條件分支，也就少一個「開了錄製才發現目錄沒建」）。

        # 步驟 1：DB 交易＝配額檢查 + 佔登錄（status=creating）。這一列就是舊版 in-memory
        # `_creating` 的替代品：它同時是配額計數的依據與失敗時要補償刪除的對象。
        # immediate=True：SQLite 以 BEGIN IMMEDIATE 在交易起始就取寫鎖，讓「數 + 寫」
        # 真正互斥（review B2：deferred 交易下單一 threaded process 內就會超額）。
        with session_scope(immediate=True) as s:
            owner = s.get(User, user_id)
            if owner is None:
                raise SessionError(f"未知 user_id：{user_id}")
            # 交易外要用它驗 per-user 空間的擁有者（ADR 0016）。**在這裡取出來**——
            # 出了 session_scope 之後 owner 是 detached 的，再讀屬性會炸。
            owner_username = owner.username
            active = (s.query(SessionRow)
                       .filter(SessionRow.user_id == user_id)
                       .filter(SessionRow.status.in_(config.ACTIVE_STATUSES))
                       .count())
            if active >= config.MAX_SESSIONS:
                raise SessionError(f"session 數已達上限 {config.MAX_SESSIONS}")
            s.add(SessionRow(id=sid, container_name=name, user_id=user_id,
                             display_name=(display_name or "").strip() or None,
                             workdir=config.WORKDIR, rows=rows, cols=cols,
                             profile=_stored_profile(profile)))

        # 步驟 2：起 container（慢 I/O，在交易外做）。任一步失敗都補償刪除登錄列 +
        # 收掉可能已建立的 container——makedirs 也必須在 try 內（否則繞過補償、白佔配額）。
        container = None
        try:
            # per-user 狀態空間（ADR 0016）：要掛的目錄，以及第一次才寫的 .claude.json 種子。
            # ⚠ **不 suppress**：這些不是「有更好、沒有也還好」的東西——目錄缺了會讓
            #   dockerd 自己建（Linux 上是 root:root，容器寫不進去），種子缺了會讓第一場
            #   撞上 Bypass Permissions 對話而 driver 一按 Enter 就把容器結束掉。
            #   失敗就讓它往上拋，走下面的補償刪除，別留一個註定壞掉的 session。
            provision_user_space(user_id, owner_username)
            if config.MOUNTS:
                # trivy DB 快取要**我們**先建，不能讓 docker daemon 隱式建立：那樣在 Linux 上
                # 會是 root:root，容器內的 nathan 寫不進去，restricted 每次都卡滿 120 秒逾時。
                with suppress(OSError):
                    os.makedirs(config.TRIVY_CACHE_SELF, exist_ok=True)
            # ADR 0001：`docker run -dit`，PID 1 為目標互動程式，PTY 由 dockerd 持有。
            #
            # ⚠ **這裡是 `create` + `start` 而不是 `run`，順序是硬要求。**
            #   `init-firewall.sh` 的 step 6 放行的是「entrypoint 跑到那一刻的直連網段」，
            #   是個**快照**。容器啟動之後才 `network connect` 上去的網路不在那份清單裡
            #   ——介面有了、路由有了，但封包被 REJECT，而且**永遠不會好**（reconciler 補得了
            #   網路、補不了 iptables，防火牆不會重跑）。實測兩個方向都驗過。
            #   所以使用者網路必須在 `start` **之前**接上。
            #
            # ⚠ `create()` 不吃 `detach`（那是 `run` 專屬的），要從 kwargs 拿掉。
            run_kwargs = build_run_kwargs(name, sid, profile, user_id)
            run_kwargs.pop("detach", None)
            container = self._docker.containers.create(config.IMAGE, **run_kwargs)
            container.start()
            with suppress(docker.errors.APIError):
                self._docker.api.resize(container.id, height=rows, width=cols)  # 開機為 0x0
            with session_scope() as s:  # 步驟 3：登錄轉正
                row = s.get(SessionRow, sid)
                row.container_id = container.id
                row.status = STATUS_RUNNING
                # 環境快照。取不到就留 NULL——這兩個是「回頭查」用的，絕不能因為它們
                # 失敗而害 session 開不起來（見各自的 helper）。
                row.image_created_at = self._image_created_at()
                row.cli_version = self._cli_version(container, profile.cli)
        except Exception:
            # 依 id 清理；container 物件可能因回應中斷而拿不到 → 再依「決定性的容器名」
            # 兜一次，否則會留下帶憑證卻無人追蹤的容器（review H2）。
            if container is not None:
                with suppress(Exception):
                    self._docker.api.remove_container(container.id, force=True)
            with suppress(Exception):
                self._docker.api.remove_container(name, force=True)
            # 補償：釋放配額（刪除登錄列）。suppress 確保補償失敗不蓋掉原始例外。
            with suppress(Exception), session_scope() as s:
                row = s.get(SessionRow, sid)
                if row is not None:
                    s.delete(row)
            raise

        # 背景執行緒等它就緒：ready_at 必須記在「真的偵測到就緒的那一刻」。曾經改
        # 在列表/查詢的觀察路徑上記，結果是 GET 會寫 DB，而且第一次開列表若在建立後
        # 60 秒，啟動耗時就被記成 60 秒——量到的是「我什麼時候去看」而不是「它什麼時候
        # 好了」。
        threading.Thread(target=self._await_ready, args=(sid,), daemon=True).start()

        return self.status(sid)

    def wait_ready(self, sid: str, timeout: float | None = None) -> bool:
        """等到 TUI 可以吃按鍵為止。兩段式，取代先前的固定延遲：

        1. **確定性**：等容器 log 出現 entrypoint 的 DRIVER_MARKER——代表選單/firewall/mitm
           等前置都做完、driver 正要啟動。這比去辨識 CLI 的 banner 可靠（banner 隨版本變）。
        2. **啟發式**：driver 啟動後 TUI 還要幾秒才畫完，故再等「畫面停止更新」。版本無關，
           不依賴任何字串比對。

        ⚠ 第 2 段必須看 **PTY**，不能看 docker logs：TUI 的繪製不會進 logs（實測 marker
        之後 logs 就一個 byte 都不再增長），照 logs 判斷會在 1 秒多就宣告就緒，prompt 撞上
        還沒進 raw mode 的 TUI，只有第一個字元進得去（2026-07-25 使用者回報）。
        """
        timeout = timeout if timeout is not None else config.READY_TIMEOUT
        deadline = time.time() + timeout
        row = self._row(sid)
        cid = row["container_id"]
        if not cid:
            return False

        while time.time() < deadline:        # 階段 1：等標記
            try:
                if DRIVER_MARKER.encode() in self._docker.containers.get(cid).logs(tail=200):
                    break
            except docker.errors.NotFound:
                return False
            time.sleep(0.3)
        else:
            return False

        return self._wait_pty_quiet(sid, deadline)   # 階段 2

    def wait_until_ready(self, sid: str, timeout: float) -> dict:
        """輪詢到 session 就緒（容器 log 出現 DRIVER_MARKER）為止。

        與 wait_ready() 的差別：後者直接盯 log 與 PTY（就緒偵測執行緒用），這裡走
        status() 輪詢，回傳完整的狀態 dict 給 HTTP 層（`?wait_ready` 靠這條）。

        逾時不拋錯，回傳當下狀態即可：ready 欄位自己會說話，呼叫端可自行決定要不要再等。
        """
        deadline = time.time() + timeout
        while True:
            info = self.status(sid, with_ready=True)
            if info["ready"] or time.time() >= deadline:
                return info
            time.sleep(0.5)

    def _wait_pty_quiet(self, sid: str, deadline: float) -> bool:
        """attach 到 PTY，等畫面停止更新＝TUI 初次繪製完成。

        attach 是鏡像式的旁觀者（ADR 0002），不影響其他客戶端；也不重播歷史（ADR 0003），
        所以「連上後一直收不到 bytes」代表畫面早就靜止了，不是還沒開始畫。
        """
        last = time.time()
        saw_any = False
        try:
            with self.attached(sid, timeout=0.3) as raw:
                while time.time() < deadline:
                    try:
                        chunk = raw.recv(65536)
                        if chunk:
                            saw_any, last = True, time.time()
                            continue
                        # EOF：container 沒了。再讀下去只會 busy-spin（recv 立刻回空），
                        # 而且也等不到任何畫面了。
                        return saw_any
                    except (TimeoutError, OSError):
                        pass                      # 這一輪沒有新畫面，正常
                    idle = time.time() - last
                    if saw_any and idle >= config.READY_QUIET_SECONDS:
                        return True
                    if not saw_any and idle >= config.READY_NO_OUTPUT_GRACE:
                        return True               # 連上就一片安靜＝早就畫完了
        except SessionError:
            return False                          # container 已經不在了
        return True      # 逾時仍視為就緒：寧可放行也不要卡死呼叫端

    def _await_ready(self, sid: str) -> None:
        """背景執行緒：等 TUI 就緒，然後把 ready_at 記在偵測到的那一刻。

        UI 的「啟動耗時」來源就是這個時間戳；背景執行緒死掉的漏網由 reconciler 的
        `_stamp_ready_backstop` 補。
        """
        ready = False
        try:
            ready = self.wait_ready(sid)
        except (SessionError, OSError):
            ready = False
        finally:
            if ready:
                with suppress(Exception):
                    self._stamp_ready(sid)   # 就在偵測到的當下記，不是等誰來看

    def _stamp_ready(self, sid: str) -> None:
        """就在偵測到的當下記下就緒時刻。單調性與 reconciler 的補漏共用同一句 UPDATE
        （`stamp_ready_if_first`），交易邊界留在這裡。"""
        with session_scope() as s:
            stamp_ready_if_first(s, sid)

    def terminate(self, sid: str, actor: dict | None = None) -> None:
        """終止 session＝`docker rm -f` + 歸檔登錄（ADR 0001：生命週期 = container）。

        `actor` 是按下終止的人，會寫進歷史（見 archive）。這條路徑**一定**有人，
        與 reconciler 判定的 exited / gone 不同。

        先刪 container 成功才收登錄——非 NotFound 的失敗保留原狀，才不會變成
        「還在跑卻沒人追蹤」的孤兒。對話不受影響（ADR 0007：續命錨點在 ~/.claude mount）。

        收登錄走 `archive()`：登錄離開 `sessions`（不再計入配額、不再被對帳），
        但快照留在 `session_history`（ADR 0010）。
        """
        row = self._row(sid)
        # container 先收：archive() 會負責收 ttyd。container_id 可能是空的——create() 在
        # 「起好容器」與「把 id 寫回登錄」之間被 kill 就會留下這種列；此時退回用容器名，
        # 否則這裡會直接跳過刪除，留下一個沒人管的容器（review S2）。
        ref = row["container_id"] or row["container"]
        if ref:
            try:
                self._docker.api.remove_container(ref, force=True)
            except docker.errors.NotFound:
                pass  # 已不在＝目標已達成（terminate 等冪）
        archive([sid], END_TERMINATED, actor)

    # --- 查詢 -----------------------------------------------------------------

    def list(self, user_id: int | None = None,
             limit: int | None = None, offset: int = 0,
             filters: Filters | None = None) -> list[dict]:
        """列出 session。**這條路徑完全不碰 docker**（ADR 0013）。

        每一列回的是「最後一次真的問到 dockerd 的狀態」加上「那是什麼時候問的」
        （`docker_state` / `state_checked_at`，由 reconciler 每輪更新），前端把新鮮度
        顯示出來。

        ⚠ 這裡曾經一邊列一邊做輕量對帳（打一次 `containers.list`，未就緒的列再各打一次
          `docker logs`）。看起來只是「順手校正」，實際上是把**整張表的可用性綁在最慢的
          那顆容器上**：2026-07-27 一顆容器卡在 `removing`，daemon 對它的呼叫全部不回應，
          於是這支每 15 秒被輪詢一次的端點每次都等滿 timeout，gunicorn 的 thread 很快
          被吃光——**一顆壞掉的容器讓所有人看不到任何東西**，包括跟它無關的列。
          校正與就緒判定都移到 reconciler，那裡逐顆隔離、一顆卡住只影響那一顆。
          代價是狀態最舊差一個對帳週期——而那正是 `state_checked_at` 要誠實講出來的事。
        """
        with session_scope() as s:
            # ⚠ filters 一定要往下傳。少了它，count() 篩過而 list() 沒篩，API 會回出
            #   `total: 0` 配上兩筆資料這種自相矛盾的結果（2026-07-26 實測踩到）。
            rows = self._page(s, user_id, limit, offset, filters).all()
            return [_to_dict(row, live_state=_last_known_state(row),
                             ready=_ready_from_row(row)) for row in rows]

    def count(self, user_id: int | None = None, filters: Filters | None = None) -> int:
        """登錄筆數（分頁用）。

        ⚠ 呼叫順序曾經是有意義的（`list()` 會在當頁順手對帳掉幾列，所以要先列再數）。
          ADR 0013 之後列表不再對帳，兩者都只讀 DB，順序不影響結果。

        ⚠ 必須套用與 list() 相同的 filters：兩者不一致的話總筆數會比實際多，
          頁碼跟著算錯，最後一頁會是空白。"""
        with session_scope() as s:
            return self._page(s, user_id, filters=filters).count()

    @staticmethod
    def history(user_id: int | None = None,
                limit: int | None = None, offset: int = 0,
                filters: Filters | None = None) -> tuple[list[dict], int]:
        """已結束 session 的永久紀錄（ADR 0010），新到舊。回傳 (該頁, 總筆數)。"""
        with session_scope() as s:
            q = s.query(SessionHistory)
            if user_id is not None:
                q = q.filter(SessionHistory.user_id == user_id)
            if filters is not None:
                # ⚠ 已結束的表比 **ended_at** 不是 created_at：「一週內」在這裡問的是
                #   「一週內結束的」。用 created_at 的話，一場跨了兩週的 session 會落在
                #   「兩週前」，而使用者是在找它結束的那一天。
                q = filters.apply(q, SessionHistory, SessionHistory.ended_at)
            total = q.count()
            q = q.order_by(SessionHistory.ended_at.desc())
            if limit is not None:
                q = q.limit(limit).offset(offset)
            return [_history_to_dict(r) for r in q.all()], total

    @staticmethod
    def _page(s, user_id: int | None, limit: int | None = None, offset: int = 0,
              filters: Filters | None = None):
        """共用的查詢條件（sessions 只存進行中的；已結束的在 session_history）。"""
        q = s.query(SessionRow)
        if user_id is not None:
            q = q.filter(SessionRow.user_id == user_id)
        if filters is not None:
            # 執行中的表比 created_at：這裡的「一週內」問的是「一週內開的」
            q = filters.apply(q, SessionRow, SessionRow.created_at)
        q = q.order_by(SessionRow.created_at.desc())
        return q.limit(limit).offset(offset) if limit is not None else q

    def status(self, sid: str, with_ready: bool = False) -> dict:
        """單筆查詢。這裡**仍然**問 dockerd——問的是一顆指定的容器，呼叫端要的就是它的
        當下狀態（`?wait_ready` 的輪詢靠這條）。與列表的差別是爆炸半徑：問壞了只有這一筆
        受影響，不會讓別人的列一起看不到（ADR 0013）。

        問不到時（daemon 不回應／逾時）**不拋錯**，退回 DB 記著的最後已知狀態，並讓
        `state_checked_at` 維持舊值——呼叫端因此看得出「這筆的狀態是舊的」。
        """
        row = self._row(sid)
        state, logs, fresh = None, b"", False
        if row["container_id"]:
            try:
                container = self._docker.containers.get(row["container_id"])
                state = container.status
                fresh = True
                if with_ready:
                    logs = container.logs(tail=200)
            except docker.errors.NotFound:
                state, fresh = "gone", True
            except Exception as e:      # noqa: BLE001 — 逾時/daemon 暫時不可用都算「問不到」
                print(f"[claude-pty] ⚠ 問不到 session {sid} 的容器狀態，改用最後已知值："
                      f"{e!r}", flush=True)
        if fresh:
            row["state"] = state
        # 問不到就沿用 _row() 已經填好的最後已知狀態
        if with_ready and fresh:
            row["ready"] = _is_ready(logs)
        # 問不到 docker 時退回 DB 的 ready_at；_row() 已經算好放在 row["ready"]
        return row

    # ⚠ **這條路徑刻意不寫 DB。** 初版讓它「問到就順手更新 docker_state/state_checked_at」，
    #   結果 30 分鐘後就炸了：`/api/auth/view` 是 nginx 的 auth_request 掛載點，每開一次
    #   終端會併發打 4~5 發，而它經 `_owned()` 走的就是這支——於是每一發都變成一筆寫入
    #   交易，讀後升級成寫的併發撞在一起，回 500 `database is locked`
    #   （busy_timeout 救不了 upgrade deadlock，那正是本專案其他熱路徑用 BEGIN IMMEDIATE
    #   的理由）。那兩欄的唯一寫入者是 reconciler，新鮮度就以對帳週期為準——列表本來就
    #   誠實標著「幾點求證的」，多這一發寫入買到的東西遠小於它的代價（ADR 0013）。

    def rename(self, sid: str, display_name: str | None) -> dict:
        """改顯示名稱（container 名稱不動，理由見 app.rename_session）。"""
        with session_scope() as s:
            row = s.get(SessionRow, sid)
            if row is None:
                raise SessionNotFound(f"未知 session：{sid}")
            row.display_name = (display_name or "").strip() or None
        return self.status(sid)

    def probe_container(self, sid: str, container_name: str) -> str | None:
        """現在就去問 dockerd 這顆 container 的狀態，順手寫進 DB。

        回 `"running"` 之類的狀態字串，或 `"gone"`（container 不在了）；**問不到就回
        `None`**——呼叫端要把 None 當「不知道」而不是「壞了」，見下。

        ⚠ 這**不違反 ADR 0013**，界線在「誰觸發」：那份 ADR 禁的是**列表路徑**自己打
          docker（一顆卡在 `removing` 的容器曾讓全站停擺 40 分鐘）。這支只在使用者
          **明確按下開啟終端**時跑一次，卡住也只卡他自己那一次點擊。

        ⚠ 失敗一律 fail-open（回 None）。dockerd 忙、逾時、暫時連不上都不該變成
          「你不能開終端」——那會把一個偶發的慢，升級成功能整個不能用。

        會把結果寫進 `docker_state` / `state_checked_at`，也就是**列表顯示的那兩欄**。
        既然為了這次點擊已經問到了真相，就順手讓畫面停止說謊，不必再等對帳器那一輪
        （最久 30 秒）。寫的是同樣的欄位、同樣的來源（dockerd），不是第二種真相。

        ⚠ 上面 `status()` 那段寫著「這條路徑刻意不寫 DB」，兩者**不衝突**，差別在頻率：
          那支是 `/api/auth/view`——nginx 的 auth_request 掛載點，開一次終端就併發打 4~5 發，
          每發都寫就撞成 `database is locked`。這支是使用者按一次「開啟」跑一次，而且
          **只在狀態真的變了才開寫入交易**（下面那個 if），沒有那個量級。
        """
        try:
            # 用短 timeout 的獨立 client：self._docker 是共用的，這條路要「問不到就算了」，
            # 不能讓它把預設的 15 秒賠進一次點擊裡。
            probe = docker.from_env(timeout=config.VIEW_PROBE_TIMEOUT)
            try:
                state = probe.containers.get(container_name).status
            finally:
                with suppress(Exception):
                    probe.close()
        except docker.errors.NotFound:
            state = "gone"
        except Exception:            # noqa: BLE001 — 逾時／連不上／APIError 都算「問不到」
            return None
        with session_scope() as s:
            row = s.get(SessionRow, sid)
            # 沒變就不要寫。同一顆容器連按兩次「開啟」是很正常的操作，第二次沒有帶來
            # 任何新資訊，不必為它開一個寫入交易（見上面那條 database is locked）。
            if row is not None and row.docker_state != state:
                row.docker_state = state
                row.state_checked_at = utcnow()
        return state

    def touch(self, sid: str) -> None:
        """更新最後活動時間（idle 回收與 UI 顯示用）。"""
        with session_scope() as s:
            row = s.get(SessionRow, sid)
            if row is not None:
                row.last_active_at = utcnow()

    # --- PTY 通道 ---------------------------------------------------------------

    def attach_socket(self, sid: str):
        """回傳直連 dockerd PTY 的 raw socket。呼叫端負責 close（請用 `close_attach()`）。

        用途有二：就緒偵測（等畫面靜止）與觸發重繪。**這條路不經 nginx/Flask 授權**
        ——它只在伺服端內部使用，不對外開放。

        ⚠ **用獨立的 docker client，不共用 self._docker**。attach 會把底層的 HTTP 連線
        hijack 成 raw socket，但 docker-py 的連線池並不知道這件事——它仍把那條連線視為
        可重用。共用 client 時，另一個執行緒（例如同時在跑的 list()）可能拿到那條已被
        接管的連線，於是出現 `ValueError: I/O operation on closed file`，嚴重時整個
        gunicorn worker 崩潰，nginx 端看到的是 connection reset by peer → 502
        （2026-07-25 實測：連續 attach 時穩定重現）。

        代價是每次 attach 多花約 10ms 建立 client——attach 不是熱路徑，這個交換划算。
        """
        row = self._row(sid)
        client = docker.from_env()
        sock = None
        try:
            container = client.containers.get(row["container"])
            sock = container.attach_socket(
                params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
            )
            # 讓 close_attach() 找得到這個 client，才收得乾淨。⚠ 這行必須在 try 內：
            # 它若丟例外（socket wrapper 不接受動態屬性），client 就再也沒人關得掉。
            sock._claude_pty_client = client
            return sock
        except docker.errors.NotFound as e:
            _discard_attach(sock, client)
            raise SessionError(f"session {sid} 的 container 已不存在") from e
        except Exception:
            _discard_attach(sock, client)
            raise

    @contextmanager
    def attached(self, sid: str, timeout: float | None = None):
        """attach 到 PTY，離開 with 一定收乾淨；yield 的是底層 raw socket。

        為什麼是 context manager 而不是「呼叫 attach_socket 後自己 close」：拿到 socket
        之後、進到 try/finally 之前的每一行（`sock._sock`、`settimeout()`）都可能丟例外
        ——container 剛好在那個瞬間退出就會——那時 finally 還沒生效，socket 與它專屬的
        docker client 就一起洩漏了（交叉審查 2026-07-25 指出）。把 setup 收進 with 裡面，
        例外從哪一行冒出來都收得到。
        """
        sock = self.attach_socket(sid)
        try:
            raw = sock._sock
            if timeout is not None:
                raw.settimeout(timeout)
            yield raw
        finally:
            close_attach(sock)

    def resize(self, sid: str, rows: int, cols: int, redraw: bool = False) -> None:
        """改容器 TTY 的尺寸。

        ⚠ container 不在時要轉成 `SessionError`（→ 400），不可讓 `docker.errors.NotFound`
          原樣往上跑：app.py 沒有它的 errorhandler，那會變成一頁 HTML traceback 的 500。
          這條路上其他每一支都轉過了——`attach_socket` 轉成 SessionError、`terminate` 當成
          冪等成功吞掉、`_nudge_redraw` 直接 suppress——只有這裡漏了。畫面那邊看不出來
          （app.js 的 `.catch(() => {})` 把它吃掉），而「malformed 輸入不該變成 500」
          是這個 codebase 自己立的規矩（review M5）。
        """
        row = self._row(sid)
        # ⚠ **尺寸沒變的話，這一次 resize 不會產生任何 SIGWINCH**——核心只在尺寸真的變了
        #   才送訊號。而「開啟終端時尺寸剛好與上次相同」是常態（同一個視窗、同一個字級），
        #   那正是使用者看到的「畫面停在舊版面，要手動縮放一下才會好」。
        #
        #   這個判斷**放在伺服端**而不是交給呼叫端的 `redraw` 旗標：伺服端知道上一次的
        #   尺寸，呼叫端不知道。前端那條路要正確得先滿足一串時序——xterm fit 完了沒、
        #   debounce 開火時讀到的是不是最終值、旗標有沒有被提早清掉——任何一環沒對上就
        #   靜靜地不重繪。Mac 與 Ubuntu 都回報過（2026-07-27）。這裡不依賴那一串。
        #
        # ⚠ 判準用的是 **DB 記的上一次尺寸**：那是拿得到的最好代理，但不是真相（真相在
        #   容器的 TTY 裡，問不到）。它可能落後——例如上一次 `_nudge_redraw` 的「還原」
        #   那一步失敗。落後時這裡會少送一次，所以**保留 `redraw` 旗標當第二條路**，
        #   兩者取聯集。代價只是「尺寸真的變了又帶旗標」時多兩次 SIGWINCH——TUI 本來就
        #   會因為那次真實變化重畫，多的那次無害。
        unchanged = (row["rows"], row["cols"]) == (rows, cols)
        try:
            self._docker.api.resize(row["container"], height=rows, width=cols)
        except docker.errors.NotFound as e:
            raise SessionError(f"session {sid} 的 container 已不存在") from e
        # 記下來：讀畫面要用它把 bytes 餵進正確尺寸的終端模擬器，觸發重繪後也要還原成
        # 這個值。docker 那邊 resize 成功才寫，免得記到一個沒真的套用的尺寸。
        with session_scope() as s:
            db_row = s.get(SessionRow, sid)
            if db_row is not None:
                db_row.rows, db_row.cols = rows, cols
        if redraw or unchanged:
            self._nudge_redraw(row["container"], rows, cols)


    def _nudge_redraw(self, container: str, rows: int, cols: int) -> None:
        """強迫 TUI 把整個畫面重畫一次。

        為什麼需要：`docker resize` 只在**尺寸真的變了**的時候才會讓核心送出 SIGWINCH。
        開啟終端時尺寸剛好與上次相同（常態——同一個視窗、同一個字級）就不會有訊號，
        TUI 於是沿用它上次畫的版面；而那個版面可能是別的尺寸留下的，畫面就對不上，
        要手動按一下縮放才會好（使用者回報）。

        手法：把寬度改成 cols-1 再改回來，製造兩次貨真價實的尺寸變化。
        **不注入任何按鍵**——注入會污染使用者的輸入。

        ⚠ 這會讓容器的 TTY 尺寸短暫變動，正在看終端的人會看到一次重繪。

        ⚠ **已知的競態（未實證，機率低但存在）**：resize 這條路沒有互斥。若在下面那
          0.15 秒之內有人改字級觸發另一次 resize 把 cols 寫成新值，這裡醒來會把 PTY
          還原成**進入時讀到的舊 cols**，而 DB 記的是新的。要修的話，還原前重讀一次
          DB 的 rows/cols。

        ⚠ 還原那一次放在 `finally`，而且與縮小**分開** suppress。共用一個 suppress 的話，
          「縮小成功、還原失敗」（dockerd 抖一下、容器剛好在這 0.15 秒內結束、worker 被
          gunicorn timeout 砍掉）會讓 PTY 永久停在 cols-1，而呼叫端上面幾行剛把 DB 寫成
          cols——之後所有依 DB 尺寸做的判斷都錯一欄，而且沒有任何錯誤訊息。
          那正是這一整段在防的那種靜默失敗。
        """
        try:
            with suppress(Exception):   # 純視覺，失敗就算了，絕不可讓 resize 整支失敗
                self._docker.api.resize(container, height=rows, width=max(2, cols - 1))
                time.sleep(config.REDRAW_SETTLE_SECONDS)
        finally:
            with suppress(Exception):
                self._docker.api.resize(container, height=rows, width=cols)

    # --- 內部 -----------------------------------------------------------------

    def _row(self, sid: str) -> dict:
        """讀一列並轉成 plain dict（脫離 ORM session，避免呼叫端碰到 detached 物件）。

        `state` 先填**最後已知**的（ADR 0013）：問得到 dockerd 的呼叫端會自己蓋掉它，
        問不到的就以這個為準——預設值不該是「DB 以為的」而是「上次真的看到的」。
        """
        with session_scope() as s:
            row = s.get(SessionRow, sid)
            if row is None:
                raise SessionNotFound(f"未知 session：{sid}")
            return _to_dict(row, live_state=_last_known_state(row),
                            ready=_ready_from_row(row))


def _last_known_state(row: SessionRow) -> str:
    """這一列**最後一次問到 dockerd** 的狀態；沒問到過就退回 DB 自己的狀態。

    還在建立寬限期內、且從沒問到過的列回 `creating`——那是「container 還沒出現」的正常
    狀態，不是「不知道」（review B1 的同一個判斷，只是資料來源換成了 DB）。
    """
    if row.docker_state:
        return row.docker_state
    if _is_creating_within_grace(row):
        return STATUS_CREATING
    return row.status


def _ready_from_row(row: SessionRow) -> bool:
    """就緒＝**曾經**觀察到 TUI 起來（ready_at 有值）。

    ⚠ 與 `_is_ready()` 是同一個定義的兩種資料來源，不是兩套規則：`ready_at` 只在
      `_is_ready()` 成立的那一刻被寫進去（`_stamp_ready`，條件式 UPDATE、寫進去不會變
      回 NULL），所以這裡讀 DB 等價於當時問過 docker logs。就緒是單調的，唯一的差別是
      「什麼時候被觀察到」——正常路徑由 create 的背景執行緒當場記下，那條執行緒死掉時
      由 reconciler 補（ADR 0013）。
    """
    return row.ready_at is not None


def _to_dict(row: SessionRow, live_state: str | None = None,
             ready: bool | None = None) -> dict:
    return {
        "id": row.id,
        "container": row.container_name,
        "container_id": row.container_id,
        "user_id": row.user_id,
        # admin 看得到所有人的 session，沒有名字就分不出這筆是誰開的
        "owner": row.user.username if row.user else None,
        "display_name": row.display_name,
        "status": row.status,
        "state": live_state if live_state is not None else row.status,
        "rows": row.rows,
        "cols": row.cols,
        # 時間指標：ready_at - created_at ＝啟動耗時；now - created_at ＝已執行多久
        "ready_at": row.ready_at.isoformat() if row.ready_at else None,
        # ⚠ 這裡**刻意不回 workdir**：它每一列都是同一個 config.WORKDIR，對「當下狀態」
        #   而言是零資訊。歷史快照才留（見 _history_to_dict）——那裡它回答的是「這場當初
        #   跑在哪個 cwd」，日後 CLAUDE_PTY_WORKDIR 改了才問得到。
        "profile": row.profile or {},
        # 環境快照：這場是用哪一版 CLI、哪一天打包的 image 開起來的
        "cli_version": row.cli_version,
        "image_created_at": (row.image_created_at.isoformat()
                             if row.image_created_at else None),
        "created_at": row.created_at.isoformat(),
        "last_active_at": row.last_active_at.isoformat(),
        # `state` 是什麼時候跟 dockerd 求證來的（ADR 0013）。**None＝從來沒問到過**，
        # 前端要照實說「尚未確認」——把沒問到過畫成「剛剛確認」是這個欄位存在的反面。
        "state_checked_at": (row.state_checked_at.isoformat()
                             if row.state_checked_at else None),
        **({"ready": ready} if ready is not None else {}),
    }


def _history_to_dict(row: SessionHistory) -> dict:
    return {
        "id": row.session_id,
        "container": row.container_name,
        "display_name": row.display_name,
        "owner": row.username,
        "user_id": row.user_id,
        "profile": row.profile or {},
        "workdir": row.workdir,
        "created_at": row.created_at.isoformat(),
        "last_active_at": row.last_active_at.isoformat(),
        "ready_at": row.ready_at.isoformat() if row.ready_at else None,
        "cli_version": row.cli_version,
        "image_created_at": (row.image_created_at.isoformat()
                             if row.image_created_at else None),
        "ended_at": row.ended_at.isoformat(),
        "ended_reason": row.ended_reason,
        # 誰按的終止。NULL＝不是人為（exited / gone）或舊紀錄。
        "ended_by": row.ended_by_username,
        "ended_by_user_id": row.ended_by_user_id,
    }


def build_run_kwargs(name: str, sid: str, profile: Profile, user_id: int) -> dict:
    """據 profile 組出 containers.run 的參數（ADR 0006）。純函式，不碰 docker daemon（可單元測試）。

    兩種路徑：
      - CLAUDE_PTY_ENTRYPOINT 覆蓋（escape hatch，如 bash 測試）→ 覆蓋 entrypoint，跳過 entrypoint.sh
        與 profile（選單無意義）。
      - 預設 None → 走 image 的 entrypoint.sh，用 env 非互動答選單（第一層），並補 docker 能力（第二層，
        env 給不了的 cap_add / network / mount）。

    `user_id` 決定 per-user 狀態空間掛哪一份（ADR 0016）。**只收 id、不查 DB**——這支要
    維持純函式才單元測試得動；目錄有沒有備妥是 `provision_user_space()` 的事（create 會先叫）。
    """
    kwargs: dict = {
        "name": name,
        "detach": True,
        "tty": True,
        "stdin_open": True,
        # 供 reconciler 辨識「這是我們管的 session container」——不可靠名稱前綴（見 config）
        "labels": {
            config.SESSION_LABEL_KEY: config.SESSION_LABEL_VALUE,
            # 測試建立的容器多打一個標記，正式 reconciler 據此跳過（見 _remove_orphans）
            **({config.TEST_LABEL_DEFAULT_KEY: config.TEST_MARK} if config.TEST_MARK else {}),
        },
        "mem_limit": config.MEM_LIMIT,
        "nano_cpus": config.NANO_CPUS,
        "pids_limit": config.PIDS_LIMIT,
    }
    volumes = {**config.MOUNTS, **config.user_mounts(user_id)}

    # SSH agent 轉發（opt-in，ADR 0012）。在 escape hatch 之前處理：它是**部署層**的能力，
    # 不隨 profile 或 entrypoint 變——「這台開了轉發」就是每個 session 都有。
    #
    # ⚠ 這一條走 `mounts` 而不是 `volumes`，兩者對「來源不存在」的行為不同：
    #   volumes（Binds）會讓 dockerd **在 host 上建一個 root:root 的目錄**頂替，而這裡的
    #   來源是 agent socket——路徑打錯、或機器剛重開還沒登入時，那個目錄會卡在 socket 該
    #   出現的位置，下次登入 gnome-keyring/ssh-agent 就綁不上去，**壞掉的是 host**。
    #   mounts（type=bind）在來源不存在時直接讓 containers.run 失敗，錯誤看得見、
    #   host 不被動到。代價是這場 session 建不起來——那正是我們要的失敗方向。
    if config.SSH_AUTH_SOCK_HOST:
        kwargs["mounts"] = [docker.types.Mount(
            target=config.SSH_AUTH_SOCK_BIND, source=config.SSH_AUTH_SOCK_HOST,
            type="bind", read_only=False)]     # 連 unix socket 需要寫權限，ro 會 EACCES

    if config.ENTRYPOINT is not None:  # escape hatch
        kwargs["entrypoint"] = config.ENTRYPOINT
        if config.COMMAND:
            kwargs["command"] = config.COMMAND
        kwargs["volumes"] = volumes
        return kwargs

    # --- 正常路徑：走 entrypoint.sh ---
    # bind-mount repo 的 entrypoint.sh，保證 env-skip 邏輯一定在（免每次 rebuild image）。
    # 存在性用 *_SELF（控制平面自己讀得到的），掛載用 host 路徑（daemon 解讀）——控制平面
    # 容器化後兩者不同，混用會靜默略過掛載或掛出空目錄（ADR 0009）。
    if os.path.isfile(config.ENTRYPOINT_SH_SELF):
        volumes[config.ENTRYPOINT_SH] = {"bind": "/usr/local/bin/entrypoint.sh", "mode": "ro"}

    # init-firewall.sh 同理：改政策不必重新 build image。
    # ⚠ **一定要 :ro**。sudoers 白名單的是**路徑**（`nathan ALL=(root) NOPASSWD:
    #   /usr/local/bin/init-firewall.sh`），所以那個路徑上的內容就是 root 會執行的程式碼
    #   ——可寫等於把 root 交出去。
    if os.path.isfile(config.INIT_FIREWALL_SH_SELF):
        volumes[config.INIT_FIREWALL_SH] = {"bind": config.INIT_FIREWALL_BIND, "mode": "ro"}

    # semgrep-rules（A4 SAST 軌道）：比照 run script 以 :ro 共用掛入（規則庫沒有 per-user
    # 的意義）。判準也與 run script 相同——要有 `.git` 才算真的 clone：compose/daemon 在
    # 來源缺席時會以 root 建出**空目錄**頂替，只驗 isdir 會把那個空殼掛進去、看起來像掛了
    # 其實沒有規則。不在（或只是空殼）→ 不掛，skill 的 A4 gate 不過、自動跳過（優雅降級，
    # 準備方式見 docs/host-paths.md）。存在性查 *_SELF、掛載用 host 路徑（ADR 0009）。
    if os.path.isdir(os.path.join(config.SEMGREP_RULES_SELF, ".git")):
        volumes[config.SEMGREP_RULES_HOST] = {"bind": config.SEMGREP_RULES_BIND, "mode": "ro"}

    # ⚠ 這裡曾經有 `_symlink_overlays()`：把 host `~/.claude` 底下那些指向 repo 的 symlink
    #   逐一 :ro 疊回容器內同一個路徑，好讓 statusline 與 symlink 形式的 skill 在 session
    #   裡看得到。ADR 0016 之後 host 的 `~/.claude` 根本不進 session（狀態是 per-user 的
    #   全新空間），這件事沒有對象了。
    #
    #   順帶拆掉一顆地雷：那個做法要 runc 願意在一個 **dangling symlink** 上建 mountpoint，
    #   而新版 runc（openat2 + securejoin）已經收緊——run script 第 75–82 行記著同樣的三段
    #   在 2026-07-26 全部移除，症狀是**間歇性**起不來（`securejoin.OpenInRoot ... openat2:
    #   invalid argument`）。這邊之所以沒爆，只因為 mountpoint 本來就存在於掛進去的
    #   `~/.claude` 裡；改成 per-user 空目錄後那個前提就沒了。

    # 第一層：env 非互動答選單。
    #
    # ⚠ **名稱一律用 `NCR_*`，那是 entrypoint.sh 認得的前綴**（它是兩條路徑共用的
    #   SSOT，見 ADR 0006）。這裡曾經用自己的 `CLAUDE_PTY_*` 前綴，那等於要求
    #   entrypoint 為了網頁這條路徑再認一組同義的變數——多一組就多一個會漂的對照表，
    #   而漂掉的症狀是「選單沒被跳過、容器停在 read 等一個永遠不來的輸入」。
    # ⚠ **條件題要成對送**：`NCR_CAPTURE=1` 時 entrypoint 會接著問錄製範圍，沒帶
    #   `NCR_CAPTURE_SCOPE` 就會停在那道 read。所以下面兩者一起給、不可只給前者。
    # ⚠ **這裡刻意沒有 subagent 深度上限**（曾經送過 `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`）。
    #   那個 env 存在的理由是「兩條路徑行為一致」，而人自己開容器時沒有它——送了反而
    #   製造它當初要消除的差異。日後真要一個上限，**加在 entrypoint**（一個地方、兩條
    #   路徑都吃得到），不要在這裡重新發明。
    env: dict = {
        "NCR_NET": profile.network,
        "NCR_CAPTURE": "1" if profile.capture else "0",
        # 成對送：見上方警告。值域是 entrypoint 的 all|1|model|2；這裡固定「全錄」，
        # 與它的預設一致——網頁這條路徑沒有人可以回答那道題。
        "NCR_CAPTURE_SCOPE": "all",
        # 請 entrypoint 印出就緒標記（DRIVER_MARKER）。人自己開容器時不設它，畫面上
        # 就不會多出一行機器用的字——那條路徑的零偏差由 test_entrypoint_human_path 守。
        "NCR_MARK": "1",
        # mitmweb 的 UI 收回容器 loopback。
        # ⚠ **這條只對網頁開的 session 成立**：它們掛在共用的 session network 上，
        #   而那個 UI 顯示的是**未脫敏的即時流量**、token 又印在 `docker logs` 裡
        #   （控制平面讀得到 log，同網段的兄弟容器連得到 8081）。綁回 loopback 之後，
        #   token 拿到也沒用——要先進得了這顆容器。
        # ⚠ 人自己開容器時不設它（預設 0.0.0.0），run script 的 `-p` 才轉得進去；
        #   docker 的 port forwarding 連的是容器內的介面，綁 loopback 會讓 UI 打不開。
        "NCR_MITM_WEB_BIND": "127.0.0.1",
        # per-user 狀態空間（ADR 0016）。**這個 env 是整個機制的關鍵**：
        #   CLAUDE_CONFIG_DIR → transcript / settings / skills / .claude.json 全部改看
        #   這個目錄（實測：設了之後 host 的 ~/.claude 一次都不會被開）。不設的話
        #   .claude.json 會落在容器 writable layer，換一顆容器就沒了。
        "CLAUDE_CONFIG_DIR": config.CLAUDE_CONFIG_BIND,
    }

    # 登入憑證：這個人貼進來的 setup-token，以環境變數交給 CLI。**唯一來源**——不掛
    # 任何 host 憑證檔（模型欄位 cli_token_enc 那段講了為什麼不留後路）。
    # create() 的 _guard_credentials 已經擋過「沒設」，這裡拿不到只剩競態（guard 之後
    # 才被清掉）——照樣不注入，讓終端停在登入提示，那是誠實的失敗畫面。
    token = auth_mod.cli_token(user_id)
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token

    # 模型與思考深度：entrypoint.sh 把它翻成 `--model` / `--effort`，這裡只放進 env。
    env["NCR_MODEL"] = profile.model
    env["NCR_EFFORT"] = profile.effort

    # 第二層：docker 能力（env 給不了）。
    if profile.network == "restricted":
        kwargs["cap_add"] = ["NET_ADMIN"]              # init-firewall.sh 需要
        kwargs["network"] = config.SESSION_NETWORK
    if profile.telemetry:
        env["NCR_OTEL"] = "1"
        env.update(_otel_env(sid))
        kwargs.setdefault("network", config.SESSION_NETWORK)       # 到得了 jaeger
    if profile.capture:
        # 存在性查 *_SELF、掛載用 host 路徑（同上，ADR 0009）
        if os.path.isdir(config.CLAUDE_MITM_SELF):     # redact addon 在才掛（否則 entrypoint fail-closed 跳過）
            volumes[config.CLAUDE_MITM_HOST] = {"bind": config.MITM_ADDON_BIND, "mode": "ro"}
        # capture 的落盤目錄已由 user_mounts() 掛成 per-user（ADR 0016）——它裡面是**完整的
        # API 請求本文**（prompt 全文），比 transcript 更敏感，共用一個目錄是先前盤點時
        # 最容易漏掉的那一項。掛載本身無條件（不分 capture 開關），少一個條件分支。
        # mitmweb UI 不再由控制平面發布 host port（ADR 0008：ttyd/port 屬 on-demand view 範疇）；
        # 需要看 mitmweb 時經 container 內部或另行 port-forward。

    kwargs["volumes"] = volumes
    kwargs["environment"] = env
    return kwargs


def _otel_env(sid: str) -> dict:
    """OTEL export 到 Jaeger 的 env，逐項對齊 run script 的 TELEMETRY_ENV（僅換掉 review 專用 resource attr）。
    entrypoint.sh 的 telemetry 選單僅在 OTEL_EXPORTER_OTLP_ENDPOINT 有值時出現，故僅 telemetry 開時才設。
    ⚠ CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1 是啟用「trace」（而非只有 metrics）的開關——缺它 claude 照跑
      照打 API 卻不吐 trace（2026-07-24 live 驗證踩到）。與 run script TELEMETRY_ENV 逐項同步，勿再抄子集。"""
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
        "OTEL_EXPORTER_OTLP_ENDPOINT": config.OTEL_ENDPOINT,
        "OTEL_LOG_TOOL_DETAILS": "1",
        "OTEL_RESOURCE_ATTRIBUTES": f"host.env=claude-pty,session.id={sid}",
    }


def _as_bool(v: object, default: bool) -> bool:
    """穩健布林解析：None→default，bool 原樣，字串走白名單（"1"/"true"/"yes"/"on"→True，其餘一律
    False，含亂碼），其餘型別 bool(v)。避免 bool("false")==True 的坑。"""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)
