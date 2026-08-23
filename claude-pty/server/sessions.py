"""SessionManager：控制平面的核心（ADR 0004 / 0008）。

一 session = 一 container（ADR 0001）；dockerd 持有 PTY，此處只負責建立 / 登錄 / 終止。
**DB 是唯一仲裁者，不保留任何 in-memory 權威狀態**（ADR 0008）——registry、配額、port
全由 DB 交易仲裁，故單 worker 與多 worker 同樣正確，無需改寫。

瀏覽器看終端走 on-demand ttyd（ADR 0008；見 views.py），create 時**不**起 ttyd。
ttyd 接的是它自己 spawn 的 `docker attach` **CLI 子程序**（ADR 0002），與這裡的
`attach_socket` 是兩條各自獨立的路——後者只給伺服端內部的就緒偵測用（見下方 attach 段）。
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import re
import socket
import tarfile
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace

import docker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from . import config, crypto, gitlab_proxy, trivy_db, user_proxy
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

    這個「晚幾秒」曾讓整站停擺 5 小時（ADR 0015）：dockerd 持續往那條沒人讀的連線灌容器
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
            raw.close()  # 真正釋放 fd；dockerd 那側隨即收到 EPIPE 並自行收乾淨


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
      否則洩漏的 fd 一樣會把 dockerd 的 broadcaster 拖死（ADR 0015）。
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
    return (
        s.query(SessionRow)
        .filter(SessionRow.id == sid, SessionRow.ready_at.is_(None))
        .update({SessionRow.ready_at: utcnow()}, synchronize_session=False)
    )


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

    ⚠ profile 的四項存在 `profile` 這個 JSON 欄位裡，所以是 JSON 取值比對。
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

    def apply(self, q, model, date_col):
        """把條件套上查詢。`date_col` 是 since_at／until_at 要比對的欄位。"""
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
            close_views(sid)  # 等冪：沒有 view 就回 0
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
            s.add(
                SessionHistory(
                    session_id=row.id,
                    container_name=row.container_name,
                    display_name=row.display_name,
                    user_id=row.user_id,
                    username=row.user.username if row.user else None,
                    profile=row.profile,
                    workdir=row.workdir,
                    created_at=row.created_at,
                    last_active_at=row.last_active_at,
                    ready_at=row.ready_at,  # 沒帶就算不出「這場啟動花多久」
                    cli_version=row.cli_version,  # 那場是用哪一版開起來的
                    image_created_at=row.image_created_at,
                    # 這場當初有沒有接上 GitLab 代理（ADR 0016）。原樣搬過來——歷史的時間視角
                    # 沒有「現在能不能用」，只剩「期間曾不曾啟用」，所以這一欄自己就是答案。
                    gitlab_proxy=row.gitlab_proxy,
                    ended_at=utcnow(),
                    ended_reason=reason,
                    ended_by_user_id=actor["id"] if actor else None,
                    ended_by_username=actor["username"] if actor else None,
                )
            )
            s.delete(row)  # cascade 連帶清掉其 views 記錄
            archived += 1
    return archived


def _slugify(name: str | None) -> str:
    """把使用者取的名字壓成 docker 容器名稱能接受的尾綴（`[a-zA-Z0-9][a-zA-Z0-9_.-]*`）。

    非法字元一律變 `-`，全被壓掉就回空字串＝不加尾綴。長度設限避免撞到名稱上限。
    """
    if not name:
        return ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return slug[: config.NAME_SLUG_MAX]


def parse_docker_time(raw: str | None) -> _dt.datetime | None:
    """docker 的 RFC3339 時間戳 → aware datetime；解不出來回 None。

    ⚠ **這是唯一一份。** 曾經有兩份：這裡，以及 reconciler 自己那一份時間戳解析（後來收斂
      成共用這支，那個名字已經不在了）。而兩份**已經漂移過**——reconciler 那一份只認 `"+"`
      來判斷有沒有時區偏移，於是 `-05:00` 會落到 else 分支被當成 UTC，整整差掉時差。
      目前不可達（daemon 一律回 `Z`），但 `_remove_orphans` 的寬限期就是靠它算的，而解析
      失敗的 fallback 是「很舊」——真的錯起來會**安靜地提早把還在建立中的容器當孤兒刪掉**。
      要再寫第二份解析之前先想清楚這一段。

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
        return _dt.datetime.fromisoformat(f"{m.group(1)}{frac}{'+00:00' if tz == 'Z' else tz}")
    except ValueError:
        return None


def age_seconds(iso_ts: str) -> float:
    """docker 物件（container / network）建立至今幾秒。

    ⚠ 住在這裡而不是 reconciler：reconciler 用它判斷孤兒的寬限期、`_ensure_user_proxy`
      用它分辨「別的 worker 正在建」與「上次建到一半留下的」，而 reconciler 已經 import
      sessions，放那邊就得反向 import。

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


def is_stale_half_built(container) -> bool:
    """這顆 `created` 的代理容器是不是「上次建到一半留下的」，而不是「別人正在建」。

    `created` 有兩種來源、外觀完全一樣：`create_container` 完成但 `put_archive` 還沒跑
    （`/etc/nginx` 還是 image 的預設設定），或 `put_archive` 完成但 `start` 還沒跑。
    分不出來，所以一律當半成品收掉重建——**但只有夠舊的才收**。還新的話那是別的 worker
    正在建（同一時間兩場 session 是常態），碰它就是把人家建到一半的容器刪掉。

    ⚠ **判準只有這一份。** 兩個地方會問這個問題，而且問的是同一件事：
      `sessions._ensure_user_proxy`（建 session 時撞到）與 `reconciler._converge_proxies`
      （背景巡邏撞到）。各寫一份的話，只要有一邊加了條件而另一邊沒跟，同一顆容器會被
      兩條路做出相反的結論。

    ⚠ **只抽判準，不抽時序**：sessions 收掉之後**當場**重建，reconciler 是**下一輪**才補。
      那個差異是刻意的，見 reconciler 那段的說明，不要一起收斂。

    ⚠ 住在 sessions.py 是因為它依賴 `age_seconds`，而 `user_proxy` **不能** import
      sessions（sessions 在模組層 import user_proxy，反向會是循環）。
    """
    if container.status != "created":
        return False
    return age_seconds(container.attrs.get("Created", "")) >= config.ORPHAN_GRACE


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
    cli: str = "claude"  # 這套東西只驅動 claude 一種 CLI
    network: str = config.DEFAULT_NET  # restricted | unrestricted
    capture: bool = config.DEFAULT_CAPTURE
    telemetry: bool = config.DEFAULT_TELEMETRY
    # 模型與思考深度：`claude --model` / `--effort` 的合法別名（見 config.CLAUDE_MODELS）
    model: str = config.DEFAULT_MODEL
    effort: str = config.DEFAULT_EFFORT
    # 憑證怎麼交給 CLI：fd（預設，值不進環境）或 env（官方文件寫過的退路）。
    # 這不是偏好題，是 fd 那條壞掉時的逃生口——見 config.TOKEN_DELIVERIES。
    token_delivery: str = config.DEFAULT_TOKEN_DELIVERY

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
            token_delivery=d.get("token_delivery", config.DEFAULT_TOKEN_DELIVERY),
        )

    def as_dict(self) -> dict:
        return {
            "cli": self.cli,
            "network": self.network,
            "capture": self.capture,
            "telemetry": self.telemetry,
            "model": self.model,
            "effort": self.effort,
            "token_delivery": self.token_delivery,
        }


def _stored_profile(profile: Profile) -> dict:
    """要寫進 DB 的那一份 profile：與送進容器的值同一份（build_run_kwargs 也讀它）。"""
    return profile.as_dict()


class SessionError(RuntimeError):
    pass


class SessionNotFound(SessionError):
    """找不到 session，或請求者無權存取（兩者刻意回同一種錯，不洩漏存在性）。"""


# ⚠ 這裡曾經有 `_require_credentials_mountpoint()`：憑證以前是以檔案**掛**進容器的，
#   巢狀 bind mount 在新版 runc（openat2 + securejoin）上落點不存在就 exit 125。
#   現在憑證由 `_put_cli_token` 用 put_archive 送進容器自己的 writable layer——是檔案
#   沒錯，但不是 mount，所以那整個問題類別連同那個函式一起消失。
#   （中間曾經改走環境變數；那條路解掉了掛載問題，卻讓值出現在 `docker inspect` 與每一個
#     子行程的環境裡。現在的做法兩個都避開，見 config.SESSION_TOKEN_FILE。）


def _put_cli_token(container, user_id: int, delivery: str) -> bool:
    """把這個人的 CLI 憑證寫進容器自己的 writable layer，回傳有沒有真的寫。

    `delivery == "env"` 時**什麼都不做**：那條路的值已經在 build_run_kwargs 放進環境了，
    這裡再送一份只會讓同一個秘密多躺一個地方。

    **不經環境變數**，理由見 `config.SESSION_TOKEN_FILE`。tar 裡直接帶 uid 與 0600：
    entrypoint 以 `config.SESSION_UID` 執行，root 寫的檔它讀不到。

    ⚠ **失敗一律降級，不中斷建立。** 拿不到憑證的終端會停在登入提示，那是使用者看得懂
      的失敗；為此讓整場開不起來不成比例。
    ⚠ 例外只印型別不印訊息——put_archive 的錯誤訊息可能回夾 payload。
    """
    if delivery != "fd":
        return False
    token = auth_mod.cli_token(user_id)
    if not token:
        return False
    data = token.encode()
    stem = os.path.basename(config.SESSION_TOKEN_DIR)  # cpty
    parent = os.path.dirname(config.SESSION_TOKEN_DIR)  # /run

    # ⚠ **目錄要一起送，而且要設成他的。** entrypoint 讀完就 `rm`，而 unlink 要的是父目錄
    #   的寫權限——檔案 0600 給對了人也沒用，`/run` 是 root 的。見 config 那段的實測。
    d = tarfile.TarInfo(stem)
    d.type, d.mode = tarfile.DIRTYPE, 0o700
    d.uid = d.gid = config.SESSION_UID

    f = tarfile.TarInfo(f"{stem}/{os.path.basename(config.SESSION_TOKEN_FILE)}")
    f.size, f.mode = len(data), 0o600
    f.uid = f.gid = config.SESSION_UID

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.addfile(d)
        tar.addfile(f, io.BytesIO(data))
    try:
        container.put_archive(parent, buf.getvalue())
        return True
    except Exception as e:  # noqa: BLE001 — 見 docstring
        print(f"[claude-pty] ⚠ 憑證送不進容器（{type(e).__name__}）：終端會停在登入提示", flush=True)
        return False


_CLAUDE_BASE = {"cli": "claude", "brand": "anthropic"}


def claude_credentials_state(user_id: int | None) -> dict:
    """這個人開 session 時，Claude Code 拿不拿得到登入憑證。

    憑證＝他自己貼進來的 setup-token（`claude setup-token` 的輸出，加密存 DB、開場時
    交給那一場；**預設走檔案描述符、不進環境變數**，env 只是逃生口，見
    `config.TOKEN_DELIVERIES`）。**唯一來源**，控制平面不讀 host 上的任何憑證檔——
    「檔案在就順便用」是一條平常不走、出事才走、而且沒人測過的路徑。

    只有兩種狀態：已設定／未設定。token 的到期時刻**不可知**（它不揭露自己的壽命），
    所以沒有「剩 N 天」的預警——過期不會有任何預告，**症狀是開場失敗**（終端裡只會
    看到登入提示）。detail 把這件事講在前面，事到臨頭那句話就是操作指南。
    ⚠ 這裡曾經回一個永遠是空陣列的 `stamps`，形狀是留給「到期時刻」用的。setup-token
      不揭露壽命之後那個能力就沒了，而空欄位讓前端跑一個永遠不會執行的迴圈——那個
      「未來也許會用到」的形狀留了很久，一直是死的。要再加預警請先確認拿得到時刻。

    解不開（換過 SECRET_KEY）與沒設過**刻意同一種畫面**：對使用者的正確指示都是
    同一句「重新貼一次」。

    每次呼叫都重讀 DB，不快取：他剛在帳號頁貼完，招牌 15 秒內就該轉綠。
    """
    token = auth_mod.cli_token(user_id) if user_id is not None else None
    if token is None:
        return {
            **_CLAUDE_BASE,
            "ok": False,
            "state": "bad",
            "label": "Claude 未設定憑證",
            "detail": "在 host 上執行 `claude setup-token`，把輸出貼到"
            "帳號管理頁的「CLI 憑證」。沒有它，session 會以未登入狀態"
            "啟動，開場只會看到登入提示。",
        }
    return {
        **_CLAUDE_BASE,
        "ok": True,
        "state": "ok",
        "label": "Claude 憑證已設定",
        "detail": "token 過期不會有預告，症狀是新開的 session 開場失敗"
        "（終端停在登入提示）。遇到就在 host 重跑 `claude setup-token`，"
        "把新的貼回帳號管理頁。已在跑的 session 不受影響。",
    }


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
        "尚未設定 Claude 憑證。請在 host 上執行 `claude setup-token`，把輸出貼到帳號管理頁的「CLI 憑證」再開。"
    )


def _jaeger_reachable() -> bool:
    """OTEL_ENDPOINT 的 host:port 此刻連得上嗎（TCP connect）。

    只回答「連得上」——連得上不保證 collector 健康，但**連不上**幾乎一定代表 trace
    送出去會石沉大海（OTLP 是 fail-open，claude 不會報錯）。控制平面據此決定：
      · 連得上  → 照設 OTEL env，session 真的送 trace
      · 連不上  → **不設** OTEL env（不送去一個沒人接的地方），但 session 照開
    這不是 fail-closed：telemetry 是觀察不是控制，不能為了它讓人開不了場。

    ⚠ 回傳只影響「送不送 + 座標記什麼」，不影響 session 起不起得來。任何例外都當
      「連不上」——探測本身壞掉不該比 jaeger 不在更嚴重。
    """
    from urllib.parse import urlparse

    try:
        u = urlparse(config.OTEL_ENDPOINT)
        host, port = u.hostname, u.port or 4317
        if not host:
            return False
        with socket.create_connection((host, port), timeout=config.JAEGER_PROBE_TIMEOUT):
            return True
    except Exception:  # noqa: BLE001 — 探測壞掉＝當成連不上，見 docstring
        return False


def ensure_system_user() -> int:
    """取得（必要時建立）預設的 system 使用者 id。

    sessions.user_id 為 NOT NULL FK，但真正的登入要到 ADR 0008 階段 4 才接上；在那之前
    所有 session 掛在這個 owner 下。password_hash 填不可用值（`!` 為 Unix 慣例的「停用」
    標記，argon2 驗證永遠不會通過），確保這個帳號無法被登入。
    """
    # ⚠ `immediate=True`：這是典型的「檢查再動作」（查有沒有 → 沒有就插），而 db.py 的
    #   模組 docstring 把那條規則寫成絕對的。它原本用預設的 deferred，是全樹唯一的反例
    #   （審查 F-037）——兩條執行緒同時走到會雙雙判定「不存在」，第二個插入撞 username
    #   UNIQUE 拋 IntegrityError，而 app.py 沒有它的 errorhandler → 500 HTML traceback。
    #   不會真的建出兩個 system 帳號（UNIQUE 擋住了），所以是錯誤呈現問題；但留著它，
    #   下一個人就有理由相信那條規則只是建議。
    with session_scope(immediate=True) as s:
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
        os.replace(tmp, path)  # 同目錄、同檔案系統 → POSIX 保證原子
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)  # 失敗不要留一地 .tmp-xxxx
        raise


def _claude_json_seed() -> dict:
    """第一次要寫進 per-user `.claude.json` 的內容。"""
    return {
        **config.CLAUDE_JSON_SEED,
        # 信任狀態是 per-project 的，key 就是容器內的 cwd。**用 config 的值組**，
        # 不可以寫死字面值。
        "projects": {config.WORKDIR: {"hasTrustDialogAccepted": True}},
    }


def provision_user_space(user_id: int, username: str) -> None:
    """備妥某個使用者的狀態空間（ADR 0014）。idempotent，每次建立 session 都會呼叫。

    **lazy 而不是建帳號時就建**：帳號早就存在了（這個功能是後來才加的），lazy 天生
    idempotent、不需要 backfill，而且「沒開過 session 的人不佔目錄」也比較乾淨。

      1. 建出要掛進去的目錄，**0700**。必須由我們建，不能讓 docker daemon 隱式
         建立——那樣在 Linux 上會是 root:root，容器內那個使用者（`config.SESSION_UID`，
         實測是 1001 不是直覺的 1000，見那個常數的說明）寫不進去，
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
    if not config.MOUNTS:  # 測試隔離：不建任何東西（同 user_mounts）
        return
    root = config.user_space(user_id, host=False)
    for sub in ("claude", "persistent-data", "ncr"):
        os.makedirs(os.path.join(root, sub), mode=0o700, exist_ok=True)
    # ⚠ `persistent-data/uploads` 由**控制平面**建，不讓上傳那條路徑臨時 `makedirs`。
    #
    # ⚠ 而且**不可以用 `makedirs` 建它**。上面那四層是掛載點本身、容器換不掉；`uploads`
    #   不是——它住在 `persistent-data/` 底下，而那一層是 session 容器的 rw 掛載，容器
    #   可以把它刪掉換成一條指向別處的連結。`makedirs` 會跟著連結走，於是控制平面（APP_UID
    #   的身分）就在對方指定的任意位置建出一個 0700 目錄；接著那圈 `chmod` 也會跟著走。
    #   這正是 app._open_uploads_dir 那支函式在防的事，在這裡自己踩一遍就白做了。
    #   所以走 mkdirat：拿 persistent-data 的 fd 當錨，開的時候 O_NOFOLLOW，
    #   權限用 fchmod 對著 fd 設，全程不經過字串路徑。
    _pd_fd = os.open(os.path.join(root, "persistent-data"), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with suppress(FileExistsError):
            os.mkdir("uploads", 0o700, dir_fd=_pd_fd)
        try:
            _up_fd = os.open("uploads", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=_pd_fd)
        except OSError as e:
            # 開不起來＝它不是一個正常目錄（被換成連結或普通檔）。這是**拒絕開場**的理由，
            # 不是可以繞過的雜訊：繞過就等於接受一個已經被動過手腳的空間。
            raise SessionError(
                f"使用者空間裡的 persistent-data/uploads 不是一個正常目錄（{e}）。"
                f"這通常代表容器內有東西把它換掉了，先人工檢查再開場。"
            ) from e
        try:
            os.fchmod(_up_fd, 0o700)
        finally:
            os.close(_up_fd)
    finally:
        os.close(_pd_fd)
    # ⚠ `makedirs(mode=...)` **只對它新建的那一層生效**，已經存在的目錄權限不會動。
    #   所以每一層都要明確 chmod——升級前用預設 0755 建出來的空間，否則會一直維持
    #   世界可讀，而 `mitm/` 裡是完整的 API 請求本文。
    # ⚠ 這一圈**只涵蓋掛載點本身那四層**。`persistent-data/uploads` 不在裡面，因為
    #   `os.chmod` 跟著連結走，而那一層是容器換得掉的（見上）——它的權限在上面用
    #   `fchmod` 對著已經驗過的 fd 設好了。
    for d in (root, *(os.path.join(root, x) for x in ("claude", "persistent-data", "ncr"))):
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
        owner = None  # 真的還沒有人認領——這一種才可以蓋章
    except (OSError, ValueError) as e:
        # ⚠ **壞掉的標記不等於沒有標記。** 當成「還沒有擁有者」就會直接重新蓋章，
        #   把上一個人的 transcript、persistent-data 與 mitm/ 的 prompt 全文靜默
        #   交給現在這個帳號——那正是這個標記存在的理由。讀不出來就停下來問人。
        raise SessionError(
            f"{owner_path} 讀不出來（{e}）——在確認這個空間屬於誰之前不會繼續。"
            f"請人工檢查：內容還原得了就修好它，確定是要重新指派就把整個 "
            f"{root} 移走。"
        ) from e
    # ⚠ 「解析得出來」不等於「是我們寫的那個形狀」。內容是 `[]` 的話下面的
    #   `owner.get()` 會 AttributeError——那會變成 500，而不是這裡精心寫的
    #   SessionError。下面的 `.claude.json` 有這道 isinstance 護欄，這裡原本漏了。
    if owner is not None and not isinstance(owner, dict):
        raise SessionError(
            f"{owner_path} 的內容不是預期的物件（{type(owner).__name__}）——"
            f"在確認這個空間屬於誰之前不會繼續。請人工檢查後修好它，"
            f"或把整個 {root} 移走。"
        )
    if owner is None:
        # ⚠ 「沒有標記」只有在**空間本身也是全新的**時候才可以認領。已經有 .claude.json
        #   就代表這個目錄有人用過（那個檔是第一次 provision 就會寫的），而標記卻不在
        #   ——那是升級前留下的空間，或有人手動動過。直接蓋章一樣是把別人的 transcript
        #   與 mitm 全文交出去，只是換一條路徑到達同一個壞結果。
        if os.path.exists(os.path.join(root, "claude", ".claude.json")):
            raise SessionError(
                f"{root} 裡已經有資料，卻沒有擁有者標記（owner.json）。在確認它屬於誰"
                f"之前不會繼續：確定是 {username!r} 的就手動補上標記，不是的話把整個"
                f"目錄移走。"
            )
        _write_json_atomic(owner_path, {"user_id": user_id, "username": username})
    elif owner.get("username") != username:
        raise SessionError(
            f"{root} 是 {owner.get('username')!r} 的空間，但這個 session 的擁有者是 "
            f"{username!r}。這通常表示 registry 重建過、user id 被重新指派——"
            f"繼續下去會把別人的對話與 capture 交給現在這個帳號。請人工確認後"
            f"改名或移走那個目錄再試。"
        )

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


def image_uid(client: docker.DockerClient | None = None) -> tuple[str, int | None]:
    """問那顆 session image：它裡面的 `nathan` 到底是幾號。

    回 `(status, uid)`，status 三選一：
      - `"ok"`         → 讀到了，`uid` 是真值
      - `"unstamped"`  → image 在，但沒有 `NCR_UID` 標記（改版前 build 的那些）
      - `"unavailable"`→ image 不在本機，或 daemon 問不到

    ⚠ **這是整條 uid 鏈上唯一的「現實」。** `APP_UID` 與 `CLAUDE_PTY_SESSION_UID` 都是
      旋鈕：兩個一起設錯，就沒有任何人會反對（那正是舊版檢查的破口——它比的是兩個
      旋鈕彼此，不是旋鈕跟現實）。所以判斷一律以這裡讀回來的值為準。

    LABEL 與 ENV 兩個都讀：build 時兩邊都有 stamp，讀得到哪個算哪個——只認一種查法的話，
    哪天 stamp 的方式改了，這支會安靜地退化成 `unstamped`。
    """
    try:
        c = client or docker.from_env(timeout=config.DOCKER_TIMEOUT)
        attrs = c.images.get(config.IMAGE).attrs
    except Exception:  # noqa: BLE001 — image 不在／daemon 不通都算查不到
        return ("unavailable", None)
    cfg = attrs.get("Config") or {}
    raw = (cfg.get("Labels") or {}).get("ncr.uid")
    if not raw:  # None 或空字串都要往下找 ENV，不然空 LABEL 會蓋掉它
        for kv in cfg.get("Env") or []:
            if kv.startswith("NCR_UID="):
                raw = kv.split("=", 1)[1]
                break
    if raw is None or not str(raw).strip():
        return ("unstamped", None)
    try:
        return ("ok", int(str(raw).strip()))
    except ValueError:
        # stamp 壞掉（build-arg 被塞了非數字）。當成沒有 stamp，別讓一個爛值冒充現實。
        return ("unstamped", None)


def preflight() -> tuple[list[str], list[str]]:
    """啟動自檢：回傳 `(提醒, 致命)` 兩份清單。

    ⚠ **兩者的差別是「服務該不該起來」，不是嚴重度的形容詞。**
      提醒＝有這個問題服務仍然做得了事（例如 uid 對不上只影響某些情境）；
      致命＝起來了也一定做不了事（例如 HOST_REPO_ROOT 設錯，每一次建 session 都會
      在 docker 的 `mounts denied` 500 上失敗）。後者只印不停等於沒有人會看：訊息在
      docker log 裡一秒被沖走，而健康檢查照樣綠燈。

    ⚠ **這支有副作用**：它會 `makedirs` per-user 空間的根目錄（ADR 0014）。那不是「檢查」
      該做的事，但必須有人做——不先建的話 dockerd 會在 bind mount 時把它建成 root:root，
      控制平面就寫不進去。放在這裡是因為它是啟動路徑上唯一跑得夠早的地方。

    最重要的一項是 entrypoint.sh 掛載——ADR 0006 的非互動 env-skip 就在那份檔案裡。
    掛不到時 session 會退回 image 內烘的舊版 entrypoint，**跳出互動選單卡住**，而且是
    靜默降級（2026-07-25 實測踩到：容器化後 _SELF_REPO_ROOT 推導成 "/"）。
    """
    problems = []
    # ⚠ `fatal` 與 `problems` 的差別是**會不會讓服務起來**。
    #   這個系統原本的立場是「大聲講，不要靜默降級」——但只印不停等於沒有人會看：
    #   訊息在 docker log 裡一秒就被沖走，而服務照樣顯示健康。對於「起得來但一定
    #   做不了事」的設定錯誤，正確的行為是**當場停掉**，讓部署的人立刻知道。
    fatal = []
    # ⚠ **這裡不再建任何共用的 session network。** session 住在**它主人那一張**上
    #   （`claude-pty-user-{id}`），由 `create()` 在建容器之前 `ensure_network` 建出來——
    #   那是 per-user 的，開機時根本不知道等一下會有誰來開場，先建不了。
    #
    # ⚠ 這裡曾經建 `claude-pty-sessions` 給所有人共用。它退役了（ADR 0016），但**升級前
    #   的機器上那張網還在，而且繼續佔著一格位址池**——reconciler 只掃有 label 的網路，
    #   永遠不會碰它。整台機器只有 31 格，一格是真的成本，所以講出來讓人清掉。
    #   訊息會在他清掉之後自己消失：這不是狼來了，是一件真的還沒做完的事。
    # ⚠ **只報不刪。** 自動刪是有副作用的動作，而那張網上可能還掛著升級前開的、還在跑的
    #   session（它們會繼續用它直到被關掉）。判斷「還有沒有人在上面」需要的資訊比一句
    #   提醒多得多，交給人。
    if config.LEGACY_NETWORK_ENV:
        # 一個被靜靜忽略的旋鈕是最難查的那種：設了、重啟了、什麼都沒變，而且沒有訊息。
        problems.append(
            f"CLAUDE_PTY_NETWORK（目前是 {config.LEGACY_NETWORK_ENV}）**已經沒有作用**"
            f"——session 現在住在每個使用者自己的網路上（ADR 0016）。請從 .env 移除。"
        )
    with suppress(Exception):  # noqa: BLE001 — 查不到就別報，這只是提醒不是檢查
        _c = docker.from_env(timeout=config.DOCKER_TIMEOUT)
        # ⚠ 精確比對：docker 的 `names` filter 是**子字串**比對，撿回來還要對名字。
        if any(
            n.name == config.LEGACY_SESSION_NETWORK for n in _c.networks.list(names=[config.LEGACY_SESSION_NETWORK])
        ):
            problems.append(
                f"舊的共用 session network {config.LEGACY_SESSION_NETWORK} 還在。"
                f"已經沒有人會用它，但它佔著一格位址池（整台機器只有 31 格）。"
                f"確認沒有 session 還掛在上面之後移除："
                f"docker network rm {config.LEGACY_SESSION_NETWORK}"
            )
    # Telemetry 的接線：**jaeger 不歸我們管，但「它到不到得了」是我們的問題。**
    #
    # 規約是「需要 jaeger 的那一方，把 jaeger 接到自己的網路上」（見 user_proxy.attach_jaeger）。
    # 開機這一輪要接兩種：
    #   · **所有既有的使用者網路** — 涵蓋「jaeger 比那些網路晚起來」。新建的那些由
    #     `ensure_network` 當場接，reconciler 每輪再兜一次底。
    #   · **控制平面自己那幾張**   — `_jaeger_reachable()` 從這裡發出探測
    #
    # ⚠ 兩種**都要**。只接使用者網路的話探測會失敗 → 控制平面判定「送不到」→ 根本不設
    #   OTEL env，於是 session 明明到得了卻不送。**探測與現實脫節，比探測失敗更難查。**
    # ⚠ jaeger 不在就安靜跳過：它是選配設施，不是缺陷。整段包在 suppress 裡——這是錦上
    #   添花，任何失敗都不該影響控制平面啟動。
    with suppress(Exception):  # noqa: BLE001 — 見上
        _c = docker.from_env(timeout=config.DOCKER_TIMEOUT)
        _want = {n.name for n in user_proxy.list_networks(_c)}
        with suppress(Exception):  # 沒跑在容器裡（本機開發）就只接使用者網路
            _me = _c.containers.get(socket.gethostname())
            _want |= set(_me.attrs["NetworkSettings"]["Networks"])
        user_proxy.attach_jaeger(_c, sorted(_want))

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
            f"容器化部署請設 CLAUDE_PTY_SELF_REPO_ROOT 指向掛進來的 repo 路徑。"
        )
    # ⚠ **HOST_REPO_ROOT 設錯的話，這裡不喊就要等到有人按「建立 session」才炸。**
    #   而且炸的樣子是 docker 的 500：
    #     mounts denied: The path /repo/dev-container/entrypoint.sh is not shared from the host
    #   `os.path.exists()` 驗不到這件事：compose 把 repo 掛在 `${HOST_REPO_ROOT}`，所以
    #   **控制平面容器裡那個路徑一定存在**，即使 host 上根本沒有。查得到真相的只有 daemon。
    #
    #   問法：compose 的設計是把 repo 掛成**同一個路徑**（來源＝目的，見 ADR 0009），
    #   所以只要問 daemon「我自己那個掛載的來源是什麼」，跟目的一比就知道。
    #   不相等＝`.env` 的 HOST_REPO_ROOT 沒設或設錯，而 session 容器會拿那個值當來源。
    if config.MOUNTS:
        try:
            _c = docker.from_env(timeout=config.DOCKER_TIMEOUT)
            _me = _c.containers.get(socket.gethostname())
            _mine = {m.get("Destination"): m.get("Source") for m in (_me.attrs.get("Mounts") or [])}
        except Exception:  # noqa: BLE001 — 問不到就跳過；docker 不通有別的地方會喊
            _mine = {}
        _src = _mine.get(config.HOST_REPO_ROOT)
        if _src and _src != config.HOST_REPO_ROOT:
            # **致命**：這個設定錯了，每一次建 session 都會失敗，服務起來也做不了事。
            fatal.append(
                f"HOST_REPO_ROOT 設錯了：容器裡看到的是 {config.HOST_REPO_ROOT}，"
                f"但 daemon 那側的來源是 {_src}。這兩個必須相同（repo 掛成同一個路徑，"
                f"ADR 0009）。**現在這樣建 session 一定會失敗**，而且錯誤會出現在 docker "
                f"的 500 裡（mounts denied），不會指回這裡。"
                f"請在 deploy/.env 設 HOST_REPO_ROOT={_src} 再重新部署。"
            )
    # MOUNTS 的來源是 host 路徑，由 daemon 解讀；控制平面容器化後本來就看不到它們，
    # 故只在「HOST 與 SELF 相同」（非容器化）時檢查，否則會誤報。
    # ⚠ MOUNTS 的 key **不一定是路徑**：trivy 的 cache 是 named volume（ADR 0018），
    #   key 是 volume 名。拿 `os.path.exists()` 去問一個 volume 名永遠是 False，於是
    #   非容器化部署每次啟動都收到一句「掛載來源不存在」的假警報。只查看起來是絕對
    #   路徑的那些——volume 由 docker 自己負責存在，不需要我們檢查。
    if config.MOUNTS and config.HOST_HOME == config._SELF_HOME:
        for src in config.MOUNTS:
            if not os.path.isabs(src):
                continue  # named volume，不是路徑
            if not os.path.exists(src):
                problems.append(f"掛載來源不存在（session 內可能缺設定/憑證）：{src}")
    # per-user 空間的根目錄（ADR 0014）。這一個查的是 **SELF**——控制平面得自己在裡面
    # mkdir 與寫種子檔，所以不是「daemon 看得到就好」，是「我現在就要寫得進去」。
    # 建不出來的話每一次建立 session 都會失敗，而錯誤會出現在很後面（provision 拋出），
    # 開機就講清楚比較好。
    if config.MOUNTS:
        try:
            os.makedirs(config.SPACE_SELF, mode=0o700, exist_ok=True)
            if not os.access(config.SPACE_SELF, os.W_OK):
                raise PermissionError(config.SPACE_SELF)
        except OSError as e:
            # **致命**，理由跟隔壁的 HOST_REPO_ROOT 一模一樣：這個設定錯了，**每一次**建
            # session 都會失敗。它原本只進 `problems`，於是服務以健康的樣子起來、首頁正常、
            # 直到有人按下「建立 session」才炸——而那時錯誤是 provision 拋出來的 OSError，
            # 指不回這裡。同一句話在這個檔案裡已經寫過一次（「只印警告不停下等於沒有」），
            # 這一格是漏掉的那個。
            fatal.append(
                f"per-user 狀態空間不可寫（{config.SPACE_SELF}）：{e}。"
                f"每個 session 的 ~/.claude 都住在這底下（ADR 0014），"
                f"**現在這樣一個 session 都建不起來**。容器化部署請確認該路徑已掛進控制平面"
                f"且擁有者是 APP_UID，並以 CLAUDE_PTY_SPACE_SELF 指明容器內看到的路徑。"
            )
        # 控制平面建目錄用的是**它自己**的 uid，session 容器裡的寫入者則是 nathan
        # （`config.SESSION_UID`，實測 1001 而不是直覺的 1000——見那個常數的說明）。
        # 兩者不同時 0700 的目錄容器就進不去：transcript 寫不下、種子讀不到，症狀是
        # 每一場都撞 onboarding 對話，而最後那道預設停在「No, exit」。
        # ⚠ **只在 host 是 Linux 時檢查**（`config.host_is_linux()`）：只有那裡的 bind mount
        #   會原樣把 uid 帶過去；Docker Desktop（macOS／Windows）都做 uid 對映，在那邊喊是
        #   純噪音。
        # ⚠ 這裡原本寫的是 `sys.platform == "linux"`，而那是**錯的問題**：控制平面跑在容器裡
        #   （ADR 0009），容器內 `sys.platform` 永遠是 linux——那道 guard 從來沒有在正式部署
        #   裡生效過，於是 macOS host 每次啟動都收到這句假警報。問的必須是 host 的作業系統，
        #   而那件事只有 host 講得出來（見 config.HOST_PLATFORM）。
        # ⚠ 比對的對象是 **image 裡的真值**，不是 `config.SESSION_UID`。後者是旋鈕，而
        #   `os.getuid()` 也是旋鈕（APP_UID）——舊版拿這兩個互比，把兩個一起設成同一個
        #   錯的數字就完全靜音，而真正決定成敗的第三個數字從來沒被問過。
        if config.host_is_linux():
            # ⚠ 這段附註不是客套。喊的時候要講得出「我憑什麼這樣判斷」，否則收到誤報的
            #   人無從查起——那正是修之前的處境（容器內問 sys.platform，macOS 每次啟動
            #   都被喊一次）。**三個分支共用同一段**，少掛在哪一條上就等於那條沒說清楚。
            _hint = (
                f"（host 判定為 "
                f"{config.HOST_PLATFORM or '未指明，退回容器內的判斷——那不一定準'}；"
                f"你的 host 不是 Linux 的話這是誤報，"
                f"deploy/redeploy.sh 會自動帶對這個值）"
            )
            _status, _real = image_uid()
            if _status == "unavailable":
                # ⚠ 查不到**不等於通過**。這一格是整條鏈唯一的現實來源，問不到就要說
                #   問不到——靜靜跳過會讓人以為驗過了。
                problems.append(
                    f"無法查證 image「{config.IMAGE}」裡的 uid（image 不在本機或 daemon "
                    f"問不到），所以這一輪**沒有驗過** uid 是否對齊。"
                    f"先把 image build 出來再重啟控制平面。{_hint}"
                )
            elif _status == "unstamped":
                # 改版前 build 的 image。退回舊的兩旋鈕比對當 fallback——它擋得住一部分
                # 情況，總比什麼都不檢查好，但要明講它驗不到真值。
                if os.getuid() != config.SESSION_UID:
                    problems.append(
                        f"控制平面以 uid {os.getuid()} 執行，但設定說 session 的寫入者是 "
                        f"{config.SESSION_UID}。per-user 空間是 0700，對不上時容器進不去"
                        f"——症狀是每一場都撞 onboarding 對話。{_hint}"
                    )
                problems.append(
                    f"image「{config.IMAGE}」沒有 NCR_UID 標記（改版前 build 的）。"
                    f"這一輪只比對得了設定值彼此，**驗不到 image 裡的真實 uid**。"
                    f"重 build 一次（`--build-arg NCR_UID=$(id -u)`）之後這道檢查才有意義。"
                    f"{_hint}"
                )
            elif _real != os.getuid() or _real != config.SESSION_UID:
                problems.append(
                    f"uid 沒有對齊：image「{config.IMAGE}」裡的 nathan 是 **{_real}**、"
                    f"控制平面以 **{os.getuid()}** 執行（APP_UID）、設定值 "
                    f"CLAUDE_PTY_SESSION_UID 是 **{config.SESSION_UID}**。"
                    f"三者必須相同——per-user 空間是 0700、憑證檔是 0600，"
                    f"對不上的症狀是每一場撞 onboarding 對話、終端停在登入提示、"
                    f"restricted 卡滿逾時，沒有一個看起來像 uid 問題。"
                    f"做法：`APP_UID={os.getuid()}` 與 image 的 "
                    f"`--build-arg NCR_UID={os.getuid()}` 對齊（Linux 上請用 `id -u`），"
                    f"並把既有的 {config.SPACE_SELF}/user-* 一併 chown。{_hint}"
                )
    if config.PAGE_SIZE_CLAMPED is not None:
        problems.append(
            f"CLAUDE_PTY_PAGE_SIZE={config.PAGE_SIZE_CLAMPED} 不在 1–{config.MAX_PAGE_SIZE} "
            f"之內，已夾成 {config.PAGE_SIZE}。不夾的話每一張列表都會回 400"
            f"（預設頁大小會去撞 MAX_PAGE_SIZE 的上限檢查）。"
        )
    if config.SSH_AUTH_SOCK_HOST:
        # 這不是「設錯了」而是「你開了一個很大的權限」——開著是合法的，但每次啟動都要
        # 講一次：沒有租戶隔離，這把 agent 等於發給每一個能建立 session 的帳號（ADR 0011）。
        problems.append(
            f"SSH agent 轉發已開啟（{config.SSH_AUTH_SOCK_HOST} → "
            f"{config.SSH_AUTH_SOCK_BIND}）：每個 session 都能以你的身分認證任何信任該 key "
            f"的主機，且無法只給部分使用者。不需要就清掉 CLAUDE_PTY_SSH_AUTH_SOCK。"
        )
        # 非容器化時（HOST==SELF）順手驗一下路徑真的在——容器化的話控制平面看不到 host
        # 路徑，硬查會誤報（同下方 MOUNTS 的理由）。
        if config.HOST_HOME == config._SELF_HOME and not os.path.exists(config.SSH_AUTH_SOCK_HOST):
            problems.append(
                f"CLAUDE_PTY_SSH_AUTH_SOCK={config.SSH_AUTH_SOCK_HOST} 不存在——"
                f"建立 session 會直接失敗（bind 來源不存在）。agent 沒起來？"
                f"socket 路徑每次登入可能不同，請確認 `echo $SSH_AUTH_SOCK`。"
            )
    # ⚠ **只綁 loopback 時不喊。** 這道提醒防的是「cookie 走未加密網路被側錄重放」，
    #   而入口只有本機連得到時那個情境不存在。以前不分情況都喊，於是本機開發每次啟動
    #   都收到一次——每次都喊的提醒，等到真的該喊那次就沒有人在看了（那正是這一整輪
    #   在修的同一種病：訊號與事實對不上）。
    #   查不到 bind 位址時仍然喊：不知道不等於安全。
    if config.BEHIND_PROXY and not config.COOKIE_SECURE and not config.entry_is_loopback_only():
        problems.append(
            f"BEHIND_PROXY=1 但 COOKIE_SECURE=0，而入口綁在 "
            f"{config.BIND_ADDR or '（未知，不是經 compose 起的）'}：登入 cookie 不帶 "
            f"Secure，若該入口是 HTTP 或經未加密網路，cookie 可被側錄重放（review H6）。"
            f"上 TLS 後請設 CLAUDE_PTY_COOKIE_SECURE=1。"
        )
    # 自訂 CA（內部憑證簽的 GitLab）。**填了卻找不到要在這裡喊。**
    #
    # ⚠ 不喊的話它會退化成一個沒有任何訊號的失敗：代理照樣建起來、容器健康、chip 綠燈，
    #   但每一個 git / API 呼叫都在 TLS 那關 502——而 `users.gitlab_proxy_error` 那條訊號
    #   是靠「代理沒活著」觸發的（見 reconciler._note_proxy_down），**它不會亮**。
    #   真正的原因只在容器的 error_log 裡，而使用者只看得到「GitLab 連不到」。
    # ⚠ 而且**絕不可以靜靜退回系統 CA**：那會變成「設定了、重啟了、什麼都沒變」，
    #   與這個功能要解決的問題是同一種。
    # ⚠ 查 *_SELF：這是「控制平面現在讀不讀得到」，不是「daemon 待會兒掛不掛得到」
    #   ——容器化部署下兩者是不同路徑（ADR 0009）。SELF 沒另外設時它就等於 HOST。
    if config.GITLAB_CA_FILE:
        if not config.gitlab_enabled():
            problems.append(
                f"設了 CLAUDE_PTY_GITLAB_CA_FILE={config.GITLAB_CA_FILE}，但沒設 "
                f"CLAUDE_PTY_GITLAB_HOST——GitLab 代理整個功能是關的，這個 CA 不會有人用。"
            )
        elif not os.path.isfile(config.GITLAB_CA_FILE_SELF):
            problems.append(
                f"CLAUDE_PTY_GITLAB_CA_FILE 指向的檔案不存在：{config.GITLAB_CA_FILE_SELF}。"
                f"代理會照樣建起來、容器也健康，但每個 git / API 呼叫都會在 TLS 驗證失敗"
                f"（502），而畫面上的代理狀態是綠的、不會有任何錯誤訊息。"
                f"容器化部署請另外以 CLAUDE_PTY_GITLAB_CA_FILE_SELF 指明控制平面看得到的路徑。"
            )
    return problems, fatal


class SessionManager:
    def __init__(self) -> None:
        # ⚠ 一定要給 timeout。docker-py 預設 60 秒，而「一顆容器卡住 → 每個呼叫等滿 60 秒
        #   → gunicorn 的 thread 全被吃光」正是 2026-07-27 那次全站停擺的機制（ADR 0012）。
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
        except Exception:  # noqa: BLE001 —— 見本區塊開頭：查不到就留白，不擋建立
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
            code, out = probe.containers.get(container.id).exec_run([cli, "--version"], demux=False)
            if code != 0 or not out:
                return None
            return out.decode("utf-8", "replace").strip().splitlines()[0][:64] or None
        except Exception:  # noqa: BLE001 —— 見本區塊開頭
            return None

    # --- 生命週期 -------------------------------------------------------------

    def create(
        self,
        rows: int = config.DEFAULT_ROWS,
        cols: int = config.DEFAULT_COLS,
        profile: Profile | None = None,
        user_id: int | None = None,
        display_name: str | None = None,
    ) -> dict:
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
        #   ADR 0014 之後那個目錄是 per-user 空間的一部分，由 provision_user_space() 無條件
        #   建出來（不分 capture 開關——少一個條件分支，也就少一個「開了錄製才發現目錄沒建」）。

        # 這個使用者的網路（ADR 0016）。**每一場都要，不分 profile、不分有沒有設 PAT**
        # ——它是 session 的家，容器要以它為 `network` 參數建立。
        #
        # ⚠ **在交易之前做，而且失敗就直接拋。** 兩個理由：
        #   · 位址池滿是**開不了場**，不是「少一個功能」。這裡沒有 DB 列要補償刪除，
        #     錯誤最短、最乾淨。
        #   · 下面的 telemetry 判定要問「jaeger 在不在這張網上」，網路得先存在——而那個
        #     答案要寫進 step 1 的登錄列裡，所以不能等到 step 2 才建。
        # ⚠ 代價是：配額已滿的人也會先建出網路。無害（他有 session 在跑，網路本來就該在），
        #   真的變成孤兒時 reconciler 過了寬限期會收掉。
        user_net_name = user_proxy.network_name(user_id)
        self._ensure_user_network(user_id)

        # telemetry：**在這裡判斷 trace 送不送得到，並據此決定送不送 + 座標記什麼**。
        # 送不到就降級——不設 OTEL env（下面傳給 build_run_kwargs 的 profile 關掉 telemetry），
        # 但 session 照開（不 fail-closed：觀察不能擋工作）。座標則兩者都記：
        #   telemetry（＝使用者要求了什麼）不動；另記 telemetry_active（＝實際有沒有開成）。
        # 這樣歷史列表能誠實區分三態：要求且開成 / 要求但沒開成 / 沒要求——那個座標的
        # 用途是事後比對，記謊會污染後續所有分析（見 sessions.html 的 chip 措辭）。
        #
        # ⚠ **兩個條件都要問，缺一不可。** `_jaeger_reachable()` 是從**控制平面**發出的
        #   探測，證明的是「控制平面自己那張網到得了 jaeger」；per-user 之後，那跟 session
        #   要待的那張網完全是兩回事。只憑探測就設 OTEL env 的話，會得到「畫面說有在錄、
        #   實際一筆都沒有」——而 OTLP 是 fail-open，沒有任何錯誤訊息會告訴你這件事。
        #   反過來只問「有沒有接上」也不夠：接上了但 jaeger 自己死掉（badger 壞掉時它照樣
        #   `Up`）一樣送不到。
        run_profile = profile
        telemetry_active = False
        if profile.telemetry:
            telemetry_active = _jaeger_reachable() and user_proxy.jaeger_on_network(self._docker, user_net_name)
            if not telemetry_active:
                run_profile = replace(profile, telemetry=False)  # 不送，但照開場
        stored_profile = _stored_profile(profile)
        if profile.telemetry:
            stored_profile["telemetry_active"] = telemetry_active

        # 步驟 1：DB 交易＝配額檢查 + 佔登錄（status=creating）。這一列就是舊版 in-memory
        # `_creating` 的替代品：它同時是配額計數的依據與失敗時要補償刪除的對象。
        # immediate=True：SQLite 以 BEGIN IMMEDIATE 在交易起始就取寫鎖，讓「數 + 寫」
        # 真正互斥（review B2：deferred 交易下單一 threaded process 內就會超額）。
        with session_scope(immediate=True) as s:
            owner = s.get(User, user_id)
            if owner is None:
                raise SessionError(f"未知 user_id：{user_id}")
            # 交易外要用它驗 per-user 空間的擁有者（ADR 0014）。**在這裡取出來**——
            # 出了 session_scope 之後 owner 是 detached 的，再讀屬性會炸。
            owner_username = owner.username
            active = (
                s.query(SessionRow)
                .filter(SessionRow.user_id == user_id)
                .filter(SessionRow.status.in_(config.ACTIVE_STATUSES))
                .count()
            )
            if active >= config.MAX_SESSIONS:
                raise SessionError(f"session 數已達上限 {config.MAX_SESSIONS}")
            s.add(
                SessionRow(
                    id=sid,
                    container_name=name,
                    user_id=user_id,
                    display_name=(display_name or "").strip() or None,
                    workdir=config.WORKDIR,
                    rows=rows,
                    cols=cols,
                    profile=stored_profile,
                )
            )

        # 步驟 2：起 container（慢 I/O，在交易外做）。任一步失敗都補償刪除登錄列 +
        # 收掉可能已建立的 container——makedirs 也必須在 try 內（否則繞過補償、白佔配額）。
        container = None
        try:
            # per-user 狀態空間（ADR 0014）：要掛的目錄，以及第一次才寫的 .claude.json 種子。
            # ⚠ **不 suppress**：這些不是「有更好、沒有也還好」的東西——目錄缺了會讓
            #   dockerd 自己建（Linux 上是 root:root，容器寫不進去），種子缺了會讓第一場
            #   撞上 Bypass Permissions 對話而 driver 一按 Enter 就把容器結束掉。
            #   失敗就讓它往上拋，走下面的補償刪除，別留一個註定壞掉的 session。
            provision_user_space(user_id, owner_username)
            if config.MOUNTS:
                # ⚠ 這裡曾經 `makedirs(TRIVY_CACHE_SELF)`——那是 cache 還是 host 目錄
                #   bind mount 的時代，為了不讓 dockerd 把它建成 root:root。改成 named
                #   volume（ADR 0018）之後那件事由 docker 自己處理，而且**擁有者是從
                #   image 的 /home/nathan/.cache/trivy 複製過來的**，本來就對。
                # DB 本身的更新（見 server/trivy_db.py）。**要在建容器之前**：restricted
                # 的 session 一起來就套 iptables，那之後牆內抓不到 ghcr.io。
                # ⚠ 整段包在 suppress 裡，而且 update() 自己也承諾不拋——它是選配設施，
                #   任何失敗都只降級、不擋開場。沒有 DB 的 A2 由 skill 走它的降級規則
                #   （跳過並揭露），那比「開不了場」好。
                # ⚠ 但**結果一定要印出來**：靜靜跳過的話，「DB 三天沒更新」跟「剛更新完」
                #   在畫面上長得一模一樣，而那正是這支存在的理由。
                with suppress(Exception):  # noqa: BLE001 — 見上
                    _db = trivy_db.update()
                    print(f"[sessions] trivy DB：{_db['status']} — {_db['detail']}", flush=True)
            # 這個使用者的 GitLab 代理（ADR 0016）。**要在建容器之前**：session 一起來就
            # 可能立刻打 API，代理得先在網路上待命。網路本身在交易之前就建好了（見上）。
            #
            # 沒設 PAT 的人**不建代理**（但網路照建、session 照開）：建一顆沒憑證的代理
            # 只會把錯誤從「連不到」變成 401，而 401 更難懂（使用者會以為 token 錯了，
            # 其實是根本沒設）。
            has_proxy = self._ensure_user_proxy(user_id)

            # ADR 0001：`docker run -dit`，PID 1 為目標互動程式，PTY 由 dockerd 持有。
            #
            # ⚠ **這裡是 `create` + `start` 而不是 `run`，順序是硬要求。**
            #   `init-firewall.sh` 的 step 6 放行的是「entrypoint 跑到那一刻的直連網段」，
            #   是個**快照**。容器啟動之後才 `network connect` 上去的網路不在那份清單裡
            #   ——介面有了、路由有了，但封包被 REJECT，而且**永遠不會好**（reconciler 補得了
            #   網路、補不了 iptables，防火牆不會重跑）。實測兩個方向都驗過。
            #
            #   使用者網路是靠 `build_run_kwargs` 放進 `network=` 參數、在**建立當下**就掛上
            #   的，比事後 connect 更早，這條自然滿足。**但 create/start 仍然不可以合併回
            #   一發 `run()`**：中間這個縫隙還住著 `_put_cli_token`（憑證只能在容器存在之後、
            #   entrypoint 跑起來之前送進去），而且日後要加第二張網也只剩這個位置放得下。
            #   `test_create_ordering` 釘著這件事。
            #
            # ⚠ `create()` 不吃 `detach`（那是 `run` 專屬的），要從 kwargs 拿掉。
            # 用 run_profile（telemetry 探不到 jaeger 時已被關掉）——不是原 profile。
            run_kwargs = build_run_kwargs(name, sid, run_profile, user_id)
            run_kwargs.pop("detach", None)
            container = self._docker.containers.create(config.IMAGE, **run_kwargs)
            # ⚠ **憑證要在 start 之前送進去，而且只能在 create 之後。** put_archive 需要
            #   一顆已經存在的容器，而 entrypoint 一跑起來就會去讀它。中間這個縫隙是唯一
            #   的窗口。失敗不中斷：拿不到憑證的終端會停在登入提示，那是誠實的失敗畫面。
            _put_cli_token(container, user_id, run_profile.token_delivery)
            container.start()
            with suppress(docker.errors.APIError):
                self._docker.api.resize(container.id, height=rows, width=cols)  # 開機為 0x0
            # 環境快照。這兩個各要一次 docker 往返（`_image_created_at` 吃 DOCKER_TIMEOUT
            # 15s，`_cli_version` 在容器裡跑一次 `--version`、吃 CLI_VERSION_TIMEOUT 5s）。
            # ⚠ **必須算在交易外。** 它們原本寫在下面那個 scope 裡，deferred 時代那是免費的
            #   ——SELECT 只拿 WAL 的讀快照，不擋任何人。改成 immediate 之後同一段程式碼變成
            #   「抱著全域寫鎖等 docker」：最壞 20 秒，期間全站每一筆寫（touch、開終端、
            #   登入、對帳）都在排隊，而排超過 busy_timeout 就是一片 `database is locked`
            #   ——把原本只發生在建立路徑的錯誤放大成全站的。dockerd 變慢的時候尤其明顯，
            #   而這個 repo 記過 dockerd 卡住 40 分鐘的場面。
            # 取不到就留 NULL——這兩個是「回頭查」用的，絕不能因為它們失敗而害 session
            # 開不起來（見各自的 helper）。
            image_created_at = self._image_created_at()
            cli_version = self._cli_version(container, profile.cli)
            # ⚠ **`immediate=True` 不是為了互斥，是因為這筆交易會寫。** 這裡先 `s.get` 再改
            #   欄位——WAL 下的 deferred 交易在升級寫鎖時，只要中間有別人 commit 過就**當場**
            #   回 SQLITE_BUSY，`busy_timeout` 對快照衝突無效（見 db.py 開頭那段）。
            #   2026-08-11 在真實部署上撞到：`POST /api/sessions` 回 500 `database is
            #   locked`，而下面那個 `except` 的補償把**已經 start 起來的容器拆掉**——使用者
            #   看到的不是「重試一下」，是開場失敗。同形狀的幾處一併改了，別再退回
            #   deferred：test_mutex_semantics 有一條靜態檢查守著。
            # ⚠ 連帶的紀律：**immediate 的交易體內不要放慢動作。** 拿鎖的時刻從 commit 提前
            #   到 BEGIN，交易體有多長，全站就被擋多久（見上面那段搬走的理由）。
            with session_scope(immediate=True) as s:  # 步驟 3：登錄轉正
                row = s.get(SessionRow, sid)
                # ⚠ **這一列可能已經不在了。** 使用者（或 admin）在建立中的那數十秒內按
                #   終止 → terminate() → archive() 把列刪掉，這裡就會 AttributeError，
                #   走完下面的補償之後原樣往上拋——而 app.py 沒有它的 errorhandler，
                #   對外就是一頁 HTML traceback 的 500（審查 F-035）。同一個檔案的
                #   rename / touch / probe_container / resize 都有這道防護，只有這裡漏了，
                #   而 resize 的 docstring 正是為了這件事寫的（review M5）。
                #   轉成 SessionNotFound：補償照走，錯誤走既有的 404 出口。
                if row is None:
                    raise SessionNotFound(f"未知 session：{sid}")
                row.container_id = container.id
                row.status = STATUS_RUNNING
                row.image_created_at = image_created_at  # 交易外先算好，見上
                row.cli_version = cli_version
                # 這一場開場時，網路上有沒有一顆代理在待命（ADR 0016）。畫面照這一欄講
                # 「有沒有路」，不照「這個帳號現在有沒有設 PAT」講——後者中途會變。
                # ⚠ 這一欄記的是**開場那一刻**的事實。代理事後被補起來的話，正在跑的
                #   session 其實用得到它（同一張網、同一個 alias），只是這一欄不會回頭改
                #   ——保守的方向：寧可畫面說沒有而實際有，不要反過來。
                row.gitlab_proxy = has_proxy
        except Exception:
            # ⚠ **代理不在這裡收**：它是 per-user 的，不屬於這一場——這一場失敗不代表使用者
            #   的其他 session 不需要它。沒人用的代理由 reconciler 依「這個使用者還有沒有
            #   活著的 session」回收。
            # 依 id 清理；container 物件可能因回應中斷而拿不到 → 再依「決定性的容器名」
            # 兜一次，否則會留下帶憑證卻無人追蹤的容器（review H2）。
            if container is not None:
                with suppress(Exception):
                    self._docker.api.remove_container(container.id, force=True)
            with suppress(Exception):
                self._docker.api.remove_container(name, force=True)
            # 補償：釋放配額（刪除登錄列）。suppress 確保補償失敗不蓋掉原始例外。
            # ⚠ 補償也是寫交易（見 db.py 的判準）。它撞 BUSY 會被 suppress 吞掉，登錄列
            #   就留著＝那個人的配額被無聲佔住，要等 reconciler 過寬限期才歸檔。
            with suppress(Exception), session_scope(immediate=True) as s:
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

    def _ensure_user_network(self, user_id: int):
        """確保這個使用者的網路存在。**建不出來就讓 session 開不起來。**

        ⚠ **這一支與 `_ensure_user_proxy` 的失敗語意刻意相反，不要「順手統一」。**
          代理不在＝這場少一個功能（降級照開）；網路不在＝這場**沒有地方可以待**。
          唯一的替代方案是把他塞進一張共用的網，而那會無聲地取消掉整個隔離設計
          （ADR 0016：任何情況下都不得退回共用網路）。所以這裡拋，那裡不拋。

        ⚠ 位址池滿要講**人聽得懂的下一步**，不要把 docker 的原文丟出去
          （`all predefined address pools have been fully subnetted` 對使用者毫無意義）。
          人數上限講**約略值**：真正的數字取決於這台機器上還有多少別的 compose 專案，
          講死了就會變成一個比機制還準確的宣稱。
        """
        try:
            return user_proxy.ensure_network(self._docker, user_id)
        except user_proxy.PoolExhausted as e:
            print(f"[claude-pty] ⚠ {e}", flush=True)
            raise SessionError(
                "這台機器的 docker 位址池用完了，開不了新的 session。"
                "目前每位使用者佔一張網路，預設上限大約是同時 26 人在線。"
                "請關掉沒在用的 session，或請管理員在 daemon.json 調整 "
                "default-address-pools（做法見 README）。"
            ) from e
        except Exception as e:
            # 其他失敗（daemon 不回應、label 衝突…）同樣是開不了場，但原因不明確——
            # 只講型別，不把可能夾帶設定內容的原始訊息端到畫面上。
            raise SessionError(
                f"建立你的 session 網路失敗（{type(e).__name__}），這場開不起來。"
                f"稍後再試一次；持續失敗請找管理員看控制平面的 log。"
            ) from e

    def _ensure_user_proxy(self, user_id: int) -> bool:
        """確保這個使用者的 GitLab 代理就位。回傳「網路上現在有沒有一顆代理」。

        **網路不歸這裡管**（`_ensure_user_network` 在交易之前就建好了，而且它是無條件的）。
        這一支只負責網路上的那顆 nginx。

        ⚠ **任何失敗都只警告，不往上拋。** GitLab 不通是「這場少一個功能」，不是「這場
          沒用」——為了它讓整個 session 開不起來是錯的取捨。失敗的原因多半是外部的
          （image 沒拉到、GitLab 的主機名解不開讓 nginx 拒絕啟動）。
          ⚠ 與 `_ensure_user_network` 的相反語意是刻意的，見那支的說明。
        ⚠ 代理已經在跑但設定過期時**熱重載**，不重建：重建會斷掉這個使用者**其他** session
          正在進行的 git 操作。
        ⚠ 失敗時要**確保沒有留下半顆**：`create` 成功但 `start` 失敗會留下一顆 `created`
          狀態、且設定裡已經有 PAT 的容器。
        """
        if not config.gitlab_enabled():
            return False  # 部署者沒設 GitLab 主機＝整個功能關閉
        if auth_mod.gitlab_pat_state(user_id) != "ok":
            return False
        pat = auth_mod.gitlab_pat(user_id)
        if not pat:
            return False  # 三態與明文之間的競態（剛好被清掉），視同沒設
        # 「本次呼叫親手建出來的那一顆」——補償只清得掉它，見下面 except 那段。
        mine: str | None = None
        try:
            existing = user_proxy.find(self._docker, user_id)
            if existing is None:
                cid, won = user_proxy.create_or_adopt(self._docker, user_id, pat)
                mine = cid if won else None  # 撞名撿到別人的 → 不是我的，別記
            elif existing.status == "created":
                # ⚠ **`created` 不可以直接 start。** 它有兩種來源，而外觀完全一樣：
                #   · `create_container` 完成、`put_archive` 還沒跑 → `/etc/nginx` 是
                #     **image 的預設設定**
                #   · `put_archive` 完成、`start` 還沒跑 → 設定是對的
                #   start 第一種的後果是**永久的殭屍**：nginx 用預設設定開在 80，容器狀態
                #   變成 `running` 看起來很健康，但 `gitlab-proxy:5678` 連不上；而
                #   reconciler 此後只會走 running 分支、`/_state` 問不到，依「問不到就別
                #   亂動」永遠不修。要等這個人**下次再開一場**才會被救回來。
                #
                # ⚠ 判準與 reconciler 共用同一支（`is_stale_half_built`）：**夠舊**才當
                #   半成品收掉重建。還新的話那是**別的 worker 正在建**（同一時間兩場
                #   session 是常態），碰它就是把人家建到一半的容器刪掉——那正是
                #   `create_or_adopt` 吸收 409 要防的事，在這裡自己再造一次就沒有意義了。
                if is_stale_half_built(existing):
                    user_proxy.remove(self._docker, user_id)
                    cid, won = user_proxy.create_or_adopt(self._docker, user_id, pat)
                    mine = cid if won else None
            elif existing.status != "running":
                # exited：設定已經在它裡面，直接 start——不必再碰 PAT。
                self._docker.api.start(existing.id)
            elif not user_proxy.ca_mount_matches(existing):
                # ⚠ **自訂 CA 換了就只能重建**：CA 是 bind mount，而掛載是建立容器時決定
                #   的，熱重載換不掉。這裡若退回走下面那條 reload，送進去的新 conf 會指向
                #   一個**沒有掛進來**的路徑，`nginx -t` 當場不過、每一輪重試一次，而代理
                #   看起來完全健康——正是這個功能要避免的那種安靜失敗。
                # ⚠ 這裡**當場重建**，reconciler 那條是**下一輪**才補。差異是刻意的，
                #   同 `is_stale_half_built` 那組的取捨：這條路上有人正在等他的 session。
                user_proxy.remove(self._docker, user_id)
                cid, won = user_proxy.create_or_adopt(self._docker, user_id, pat)
                mine = cid if won else None
            elif user_proxy.running_state(self._docker, user_id) != gitlab_proxy.fingerprint(pat):
                user_proxy.reload(self._docker, user_id, pat)
            return True
        except Exception as e:  # noqa: BLE001 — 見 docstring：一律降級不中斷
            # ⚠ 例外訊息可能夾帶設定內容（因而夾帶 PAT）——只印型別。
            print(
                f"[claude-pty] ⚠ 使用者 {user_id} 的 GitLab 代理無法就緒"
                f"（{type(e).__name__}）：新開的 session 沒有 git / API 代理",
                flush=True,
            )
            with suppress(Exception):
                # ⚠ 「半顆」的判準是**「是不是我這次建的」**，不是「問不問得到 /_state」，
                #   也不是年齡。
                #   · 用 `/_state` 問不到當判準 → 會把「健康的 running 代理、但 exec 剛好
                #     失敗」也算進去，而觸發這條補償的例外（daemon 抖動）與 exec 失敗高度
                #     相關。那樣會 force-remove 一顆正在服務**這個人其他 session** 的代理。
                #   · 用年齡當判準 → **自己**留下的半顆要等滿 ORPHAN_GRACE 才被 reconciler
                #     收，而且擋不住「另一個 worker 正在建、還停在 created」那顆。
                #   「是不是我建的」既精確又即時。
                # ⚠ `create_or_adopt` 內部失敗（put_archive／start）會自己收乾淨，所以那條
                #   路徑不靠這裡——這裡守的是「建好之後、回到這裡之前」才出事的情況。
                if mine is not None:
                    half = user_proxy.find(self._docker, user_id)
                    if half is not None and half.id == mine and half.status != "running":
                        user_proxy.remove(self._docker, user_id)
            return False

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

        while time.time() < deadline:  # 階段 1：等標記
            try:
                if DRIVER_MARKER.encode() in self._docker.containers.get(cid).logs(tail=200):
                    break
            except docker.errors.NotFound:
                return False
            time.sleep(0.3)
        else:
            return False

        return self._wait_pty_quiet(sid, deadline)  # 階段 2

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
                        pass  # 這一輪沒有新畫面，正常
                    idle = time.time() - last
                    if saw_any and idle >= config.READY_QUIET_SECONDS:
                        return True
                    if not saw_any and idle >= config.READY_NO_OUTPUT_GRACE:
                        return True  # 連上就一片安靜＝早就畫完了
        except SessionError:
            return False  # container 已經不在了
        return True  # 逾時仍視為就緒：寧可放行也不要卡死呼叫端

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
                    self._stamp_ready(sid)  # 就在偵測到的當下記，不是等誰來看

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

    def list(
        self, user_id: int | None = None, limit: int | None = None, offset: int = 0, filters: Filters | None = None
    ) -> list[dict]:
        """列出 session。**這條路徑完全不碰 docker**（ADR 0012）。

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
            return [_to_dict(row, live_state=_last_known_state(row), ready=_ready_from_row(row)) for row in rows]

    def count(self, user_id: int | None = None, filters: Filters | None = None) -> int:
        """登錄筆數（分頁用）。

        ⚠ 呼叫順序曾經是有意義的（`list()` 會在當頁順手對帳掉幾列，所以要先列再數）。
          ADR 0012 之後列表不再對帳，兩者都只讀 DB，順序不影響結果。

        ⚠ 必須套用與 list() 相同的 filters：兩者不一致的話總筆數會比實際多，
          頁碼跟著算錯，最後一頁會是空白。"""
        with session_scope() as s:
            return self._page(s, user_id, filters=filters).count()

    @staticmethod
    def history(
        user_id: int | None = None, limit: int | None = None, offset: int = 0, filters: Filters | None = None
    ) -> tuple[list[dict], int]:
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
    def _page(s, user_id: int | None, limit: int | None = None, offset: int = 0, filters: Filters | None = None):
        """共用的查詢條件（sessions 只存進行中的；已結束的在 session_history）。"""
        q = s.query(SessionRow)
        if user_id is not None:
            q = q.filter(SessionRow.user_id == user_id)
        if filters is not None:
            # 執行中的表比 created_at：這裡的「一週內」問的是「一週內開的」
            q = filters.apply(q, SessionRow, SessionRow.created_at)
        q = q.order_by(SessionRow.created_at.desc())
        # ⚠ **eager load 擁有者。** `_to_dict` 每一列會碰兩次 `row.user`（owner 與
        #   gitlab_pat_set），而 models 的那個 relationship 沒有設 lazy=，預設就是
        #   `lazy="select"`——所以「走已經載進來的 row.user」那句註解只做到一半：它避掉了
        #   「在列表交易裡開巢狀交易」，N+1 還在（一頁 20 筆、20 個不同使用者＝20 發查詢，
        #   審查 F-036）。joinedload 讓它變成一次 JOIN。
        q = q.options(joinedload(SessionRow.user))
        return q.limit(limit).offset(offset) if limit is not None else q

    def peek(self, sid: str) -> dict:
        """純 DB 讀的單筆查詢：不問 dockerd、不寫任何東西。

        給**熱路徑上只需要 DB 事實**的呼叫端用（auth_check 的擁有權判定：容器此刻
        的狀態不改變「這場是不是他的」）。要當下容器狀態的呼叫端走 status()。
        """
        return self._row(sid)

    def status(self, sid: str, with_ready: bool = False) -> dict:
        """單筆查詢。這裡**仍然**問 dockerd——問的是一顆指定的容器，呼叫端要的就是它的
        當下狀態（`?wait_ready` 的輪詢靠這條）。與列表的差別是爆炸半徑：問壞了只有這一筆
        受影響，不會讓別人的列一起看不到（ADR 0012）。

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
            except Exception as e:  # noqa: BLE001 — 逾時/daemon 暫時不可用都算「問不到」
                print(f"[claude-pty] ⚠ 問不到 session {sid} 的容器狀態，改用最後已知值：{e!r}", flush=True)
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
    #   誠實標著「幾點求證的」，多這一發寫入買到的東西遠小於它的代價（ADR 0012）。

    def rename(self, sid: str, display_name: str | None) -> dict:
        """改顯示名稱（container 名稱不動，理由見 app.rename_session）。"""
        with session_scope(immediate=True) as s:
            row = s.get(SessionRow, sid)
            if row is None:
                raise SessionNotFound(f"未知 session：{sid}")
            row.display_name = (display_name or "").strip() or None
        return self.status(sid)

    def probe_container(self, sid: str, container_name: str) -> str | None:
        """現在就去問 dockerd 這顆 container 的狀態，順手寫進 DB。

        回 `"running"` 之類的狀態字串，或 `"gone"`（container 不在了）；**問不到就回
        `None`**——呼叫端要把 None 當「不知道」而不是「壞了」，見下。

        ⚠ 這**不違反 ADR 0012**，界線在「誰觸發」：那份 ADR 禁的是**列表路徑**自己打
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
        except Exception:  # noqa: BLE001 — 逾時／連不上／APIError 都算「問不到」
            return None
        with session_scope(immediate=True) as s:
            row = s.get(SessionRow, sid)
            # 沒變就不要寫。同一顆容器連按兩次「開啟」是很正常的操作，第二次沒有帶來
            # 任何新資訊。
            # ⚠ 措辭修正：這個 scope 現在是 immediate，所以「沒變」那條路**還是取了寫鎖**
            #   （拿鎖的時刻在 BEGIN，不在 commit）。留著這個 if 的理由變成「少一次 commit
            #   與 fsync」，不再是「不開寫入交易」。真正省下的量級沒有以前寫的那麼大——
            #   但交易體是 µs 級的 get＋比較，docker 探測在交易外，所以實害趨近零。
            if row is not None and row.docker_state != state:
                row.docker_state = state
                row.state_checked_at = utcnow()
        return state

    def touch(self, sid: str) -> None:
        """更新最後活動時間（idle 回收與 UI 顯示用）。"""
        with session_scope(immediate=True) as s:
            row = s.get(SessionRow, sid)
            if row is not None:
                row.last_active_at = utcnow()

    # --- PTY 通道 ---------------------------------------------------------------

    def attach_socket(self, sid: str):
        """回傳直連 dockerd PTY 的 raw socket。呼叫端負責 close（請用 `close_attach()`）。

        唯一的用途是**就緒偵測**（連上去等畫面靜止，見 `_wait_pty_quiet`）。**這條路不經
        nginx/Flask 授權**——它只在伺服端內部使用，不對外開放；瀏覽器那條終端走的是 ttyd
        自己的 `docker attach` 子程序，與這裡無關。（觸發重繪早就改成送兩次 resize 了，
        見 `_nudge_redraw`——不從這裡注入任何按鍵。）

        ⚠ **這個 client 刻意不給 timeout**，是這個 codebase 裡唯一的例外（ADR 0012 的
        「所有 docker client 給有界 timeout」在這裡不適用）：attach 會把底層 HTTP 連線
        hijack 成 raw socket，client 的 timeout 會直接變成那條串流的 `recv` 逾時——而
        「一直收不到 bytes」正是就緒偵測要的答案，不是失敗。逾時由呼叫端在 socket 上設
        （`attached(timeout=…)` → `raw.settimeout()`），尺度也不同（0.3 秒一輪）。

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
            sock = container.attach_socket(params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
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
        # 記下來：下一次要判斷「尺寸有沒有變」靠它（見上面那段），觸發重繪後也要還原成
        # 這個值。docker 那邊 resize 成功才寫，免得記到一個沒真的套用的尺寸。
        # ⚠ 這裡原本還寫著「讀畫面要用它把 bytes 餵進正確尺寸的終端模擬器」——那是一個
        #   已經拆掉的功能留下的殘影，而且方向與 ADR 0003 相反（伺服端不維護螢幕狀態、
        #   不引入 pyte，重繪交給 TUI 自己）。不要照著那句話把終端模擬器加回來。
        # immediate：這筆會寫（見 db.py 的判準；F-024 那段點名的清單本來就含 resize）。
        # docker 那邊的 resize 已經在上面做完了，這個交易體只剩 get + 兩個賦值。
        with session_scope(immediate=True) as s:
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
            with suppress(Exception):  # 純視覺，失敗就算了，絕不可讓 resize 整支失敗
                self._docker.api.resize(container, height=rows, width=max(2, cols - 1))
                time.sleep(config.REDRAW_SETTLE_SECONDS)
        finally:
            with suppress(Exception):
                self._docker.api.resize(container, height=rows, width=cols)

    # --- 內部 -----------------------------------------------------------------

    def _row(self, sid: str) -> dict:
        """讀一列並轉成 plain dict（脫離 ORM session，避免呼叫端碰到 detached 物件）。

        `state` 先填**最後已知**的（ADR 0012）：問得到 dockerd 的呼叫端會自己蓋掉它，
        問不到的就以這個為準——預設值不該是「DB 以為的」而是「上次真的看到的」。
        """
        with session_scope() as s:
            row = s.get(SessionRow, sid)
            if row is None:
                raise SessionNotFound(f"未知 session：{sid}")
            return _to_dict(row, live_state=_last_known_state(row), ready=_ready_from_row(row))


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
      由 reconciler 補（ADR 0012）。
    """
    return row.ready_at is not None


def _to_dict(row: SessionRow, live_state: str | None = None, ready: bool | None = None) -> dict:
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
        "image_created_at": (row.image_created_at.isoformat() if row.image_created_at else None),
        "created_at": row.created_at.isoformat(),
        "last_active_at": row.last_active_at.isoformat(),
        # `state` 是什麼時候跟 dockerd 求證來的（ADR 0012）。**None＝從來沒問到過**，
        # 前端要照實說「尚未確認」——把沒問到過畫成「剛剛確認」是這個欄位存在的反面。
        "state_checked_at": (row.state_checked_at.isoformat() if row.state_checked_at else None),
        # GitLab 代理（ADR 0016）。**兩個事實一起給，因為單獨任一個都會說謊**：
        #   · gitlab_proxy      ＝這場**當初**有沒有接上代理網路。不可變（網路必須在容器
        #                         start 之前接），所以事後補 token 救不了已經在跑的場。
        #   · gitlab_pat_set    ＝擁有者**現在**還有沒有 token，也就是那條路的另一端在不在。
        # 只看前者：使用者清掉 token 之後畫面會一直說「可用」而 git 全部失敗。
        # 只看後者：事後補 token 會讓畫面對著一場根本沒接上網路的 session 說「可用」。
        # ⚠ gitlab_proxy 為 None＝這個欄位上線前建立的舊列，是「不知道」。呼叫端不可以
        #   把它畫成「未啟用」——那是在謊稱一件沒有人查證過的事。
        # ⚠ 走**已經載進來的** `row.user`，不要呼叫 `auth.gitlab_pat_state(row.user_id)`：
        #   後者每一列會自己開一次 `session_scope`，而 `_to_dict` 是在列表的交易**裡面**
        #   逐列跑的——那是 N+1，而且是在 SQLite 上對同一個檔案開巢狀交易。這條路上的
        #   「順手多問一次 DB」正是先前把控制平面打成 `database is locked` 的形狀。
        #   `is_readable` 不把明文交出去，答案與三態的 `ok` 一致。
        #   ⚠ 「已經載進來的」是靠 `_page()` 的 `joinedload` 保證的，不是自動的——那個
        #     relationship 預設是 lazy=select，少了 joinedload 這裡每一列仍會各發一次
        #     SELECT（審查 F-036）。改動查詢那一側時要一起看。
        "gitlab_proxy": row.gitlab_proxy,
        "gitlab_pat_set": bool(
            config.gitlab_enabled()
            and row.user is not None
            and crypto.is_readable(row.user.gitlab_pat_enc, purpose=crypto.Purpose.GITLAB_PAT)
        ),
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
        "image_created_at": (row.image_created_at.isoformat() if row.image_created_at else None),
        # 期間**曾不曾**啟用 GitLab 代理（ADR 0016）。歷史只有這一個事實——session 都結束
        # 了，沒有「現在能不能用」可言，所以這裡不像執行中那樣需要配一個 gitlab_pat_set。
        # None＝欄位上線前的舊紀錄：不知道，不要畫成「未啟用」。
        "gitlab_proxy": row.gitlab_proxy,
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

    `user_id` 決定 per-user 狀態空間掛哪一份（ADR 0014）。**只收 id、不查 DB**——這支要
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

    # SSH agent 轉發（opt-in，ADR 0011）。在 escape hatch 之前處理：它是**部署層**的能力，
    # 不隨 profile 或 entrypoint 變——「這台開了轉發」就是每個 session 都有。
    #
    # ⚠ 這一條走 `mounts` 而不是 `volumes`，兩者對「來源不存在」的行為不同：
    #   volumes（Binds）會讓 dockerd **在 host 上建一個 root:root 的目錄**頂替，而這裡的
    #   來源是 agent socket——路徑打錯、或機器剛重開還沒登入時，那個目錄會卡在 socket 該
    #   出現的位置，下次登入 gnome-keyring/ssh-agent 就綁不上去，**壞掉的是 host**。
    #   mounts（type=bind）在來源不存在時直接讓 containers.run 失敗，錯誤看得見、
    #   host 不被動到。代價是這場 session 建不起來——那正是我們要的失敗方向。
    if config.SSH_AUTH_SOCK_HOST:
        kwargs["mounts"] = [
            docker.types.Mount(
                target=config.SSH_AUTH_SOCK_BIND,
                source=config.SSH_AUTH_SOCK_HOST,
                # ⚠ read_only=True（2026-08-22 起）。:ro **不會**讓 socket 連不上
                #   （connect 走 path_permission(MAY_WRITE)，是 inode 檢查，不經過
                #    mnt_want_write，所以 MNT_READONLY 沒被諮詢；反例見
                #    tests/test_ro_socket_mount.py）。
                #   它擋的是「弄壞 host 那顆 socket」：bind mount 與 host 共用同一個 inode，
                #   原生 Linux 上容器對它 chmod／chown 會改到 host 那一顆，症狀是使用者
                #   其他終端機的 ssh 全部失效、且指不到容器。
                #   ⚠ 這不是 agent 的安全邊界——簽章、列舉金鑰、轉送一項都擋不住。
                type="bind",
                read_only=True,
            )
        ]

    # **網路：無條件指定成這個使用者自己那張**（ADR 0016）。
    #
    # ⚠ **這一行必須在 escape hatch 的 return 之前**，理由與上面的 SSH mount 完全相同：
    #   它是**部署層／隔離層**的性質，不隨 profile 或 entrypoint 變。它原本寫在下面正常
    #   路徑那一段，於是走 `CLAUDE_PTY_ENTRYPOINT` 的容器**完全沒有 network 參數、落在
    #   docker 預設 `bridge`**（審查 F-004）——而那張網住著這台機器上每一顆沒指定網路的
    #   容器，正是 ADR 0016 稱為「比它要取代的共用網路還糟」的那個形狀。
    # ⚠ 下面那一段的註解寫著「不可以再退回條件式」，而**提早 return 就是條件式的另一種
    #   寫法**——第一次修那個洞時只看了 `if`，沒看 return。
    # ⚠ 仍然是**純函式**：`network_name()` 只是字串組裝，不碰 docker。網路要真的存在是
    #   `create()` 的責任（它在建容器之前 `ensure_network`）。
    kwargs["network"] = user_proxy.network_name(user_id)

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
    # 其實沒有規則。不在（或只是空殼）→ 不掛，skill 的 A4 gate 不過、自動跳過（優雅降級）。
    # 準備方式：在 host 上 `git clone` 一份規則庫到 `$HOME/semgrep-rules`（或以
    # `CLAUDE_PTY_SEMGREP_RULES` / `NCR_OPENGREP_RULES` 指到別處）。
    # 存在性查 *_SELF、掛載用 host 路徑（ADR 0009）。
    if os.path.isdir(os.path.join(config.SEMGREP_RULES_SELF, ".git")):
        volumes[config.SEMGREP_RULES_HOST] = {"bind": config.SEMGREP_RULES_BIND, "mode": "ro"}

    # ⚠ 這裡曾經有 `_symlink_overlays()`：把 host `~/.claude` 底下那些指向 repo 的 symlink
    #   逐一 :ro 疊回容器內同一個路徑，好讓 statusline 與 symlink 形式的 skill 在 session
    #   裡看得到。ADR 0014 之後 host 的 `~/.claude` 根本不進 session（狀態是 per-user 的
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
        # per-user 狀態空間（ADR 0014）。**這個 env 是整個機制的關鍵**：
        #   CLAUDE_CONFIG_DIR → transcript / settings / skills / .claude.json 全部改看
        #   這個目錄（實測：設了之後 host 的 ~/.claude 一次都不會被開）。不設的話
        #   .claude.json 會落在容器 writable layer，換一顆容器就沒了。
        "CLAUDE_CONFIG_DIR": config.CLAUDE_CONFIG_BIND,
    }

    env.update(_gitlab_env())

    # 登入憑證：這個人貼進來的 setup-token。**這裡只放路徑，不放值**——值由 create()
    # 在 create 與 start 之間用 `put_archive` 送進容器（見 config.SESSION_TOKEN_FILE
    # 的說明，以及 _put_cli_token）。不掛任何 host 憑證檔（模型欄位 cli_token_enc
    # 那段講了為什麼不留後路）。
    # create() 的 _guard_credentials 已經擋過「沒設」，這裡拿不到只剩競態（guard 之後
    # 才被清掉）——照樣什麼都不放，讓終端停在登入提示，那是誠實的失敗畫面。
    #
    # ⚠ 兩條路，per-session 選（見 config.TOKEN_DELIVERIES）：`fd` 這裡只放**路徑**，
    #   值由 create() 用 put_archive 送進去；`env` 是把值直接放進環境的退路。
    _token = auth_mod.cli_token(user_id)
    if _token:
        if profile.token_delivery == "env":
            env["CLAUDE_CODE_OAUTH_TOKEN"] = _token
        else:
            env["NCR_TOKEN_FILE"] = config.SESSION_TOKEN_FILE

    # 模型與思考深度：entrypoint.sh 把它翻成 `--model` / `--effort`，這裡只放進 env。
    env["NCR_MODEL"] = profile.model
    env["NCR_EFFORT"] = profile.effort

    # 第二層：docker 能力（env 給不了）。
    #
    # **網路：無條件指定成這個使用者自己那張**（ADR 0016）。四種 profile 組合都設，沒有
    # 例外——它是 session 的家，不是某個功能的配件。
    #
    # ⚠ 這裡曾經只在 `restricted` 或 `telemetry` 時設 network，於是 **unrestricted 且不送
    #   telemetry 的 session 落在 docker 預設 `bridge`**。那張網住著這台機器上每一顆沒指定
    #   網路的容器，不只是別人的 session——比它要取代的共用網路還糟。而且它是**沉默的**：
    #   容器起得來、網路也通，看不出自己在一張公共的網上（2026-08-07 盤點時發現，當時
    #   一條測試都沒蓋到這個組合）。所以這一行**不可以再退回條件式**。
    # ⚠ network 的指定**已經移到 escape hatch 之前**（見那裡的說明）——留在這裡的話走
    #   `CLAUDE_PTY_ENTRYPOINT` 的容器會落在預設 bridge（審查 F-004）。
    if profile.network == "restricted":
        kwargs["cap_add"] = ["NET_ADMIN"]  # init-firewall.sh 需要
    # ⚠ 這裡只看 profile.telemetry——是**純函式**，不探 jaeger。可達性的判斷與降級在
    #   create()：它探不到（或 jaeger 沒接上這個人的網路）就把傳進來的 run_profile 的
    #   telemetry 關掉，所以走到這裡時 telemetry=True 已經代表「真的送得到」。把探測放這裡
    #   會讓這支變成有 I/O 的函式，而 test_profile_mapping 正是靠它是純的、
    #   Profile(telemetry=True) 一定設 env。
    if profile.telemetry:
        env["NCR_OTEL"] = "1"
        env.update(_otel_env(sid))
    if profile.capture:
        # 存在性查 *_SELF、掛載用 host 路徑（同上，ADR 0009）
        if os.path.isdir(config.CLAUDE_MITM_SELF):  # redact addon 在才掛（否則 entrypoint fail-closed 跳過）
            volumes[config.CLAUDE_MITM_HOST] = {"bind": config.MITM_ADDON_BIND, "mode": "ro"}
        # capture 的落盤目錄已由 user_mounts() 掛成 per-user（ADR 0014）——它裡面是**完整的
        # API 請求本文**（prompt 全文），比 transcript 更敏感，共用一個目錄是先前盤點時
        # 最容易漏掉的那一項。掛載本身無條件（不分 capture 開關），少一個條件分支。
        # mitmweb UI 不再由控制平面發布 host port（ADR 0008：ttyd/port 屬 on-demand view 範疇）；
        # 需要看 mitmweb 時經 container 內部或另行 port-forward。

    kwargs["volumes"] = volumes
    kwargs["environment"] = env
    return kwargs


def _gitlab_env() -> dict:
    """讓 session 裡的 git 與 API 呼叫自己走上代理（ADR 0016）。純函式，不碰 docker。

    部署者沒設 GitLab 主機時回空 dict——什麼都不注入，session 完全不知道有這回事。

    ⚠ **沒有 URL 改寫，per-user 代理在實務上等於不能用。** 每個人、每份既有 repo、每一段
      複製貼上的指令，寫的都是 `https://<你的 gitlab>/x/y.git`，而那個位址在 session 裡是
      **直接失敗的**（防火牆不放行直連 443，那正是設計要的）。沒有改寫的話，使用者得手動
      把每一個 remote 換成代理位址——而他第一次遇到的症狀是 `Failed to connect`，完全看不
      出要去改 URL。

    ⚠ 用 **`GIT_CONFIG_*` 環境變數**而不是寫 `~/.gitconfig`：後者要嘛動到兩條路徑共用的
      `entrypoint.sh`（人自己開容器那條會被牽連），要嘛落進 per-user 空間變成一份會跟著
      漂的檔案。env 只影響網頁開的 session，人的路徑一個字都不會變。

    ⚠ **不分 profile、也不看有沒有 PAT。** 沒有代理時，改寫的結果是「連不到代理」而不是
      「連不到 GitLab」——兩者都失敗，但前者的訊息裡有 `gitlab-proxy` 這個字，使用者一搜
      就找得到答案（去設定頁填 PAT）。

    ⚠ **SSH 的兩種寫法也要改寫，不是只有 https。** 網頁開的 session 裡 SSH agent 預設不掛
      （ADR 0011）、防火牆也不放行 22，所以 `git@host:group/repo.git` 原本是**必定失敗**
      的，而症狀（`Permission denied (publickey)`）完全指不到「該用 https」。

    ⚠ `insteadOf` 是**多值鍵**，同一個 key 可以給多個值；`GIT_CONFIG_KEY_n` 重複同一個
      key 名稱就是這個意思。

    ⚠ scp-like 的 `git@host:` 結尾**必須是冒號**，不是斜線：`git@host:group/repo.git`
      改寫後要成為 `<代理>/group/repo.git`。寫成 `git@host/` 不會有任何錯誤訊息，只是
      靜靜不改寫。

    ⚠ https 那條結尾的斜線**不可以拿掉**：沒有它就變成前綴比對，
      `https://<你的 gitlab>.evil.example/…` 會被改寫成走代理——冒牌主機的請求被導進去，
      而代理會替它蓋上真的 PAT。
    """
    if not config.gitlab_enabled():
        return {}
    base = config.PROXY_BASE_URL
    key = f"url.{base}/.insteadOf"
    env = {
        # 容器裡看到的 API base。呼叫端（curl、任何腳本）不必把 `gitlab-proxy:5678` 寫死。
        # ⚠ git **不吃**這個變數，它走的是下面那組 GIT_CONFIG；反過來 curl 也不吃 git 的
        #   設定。兩者各有一條路，這是刻意的。
        "NCR_GITLAB_API_BASE": base,
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": key,
        "GIT_CONFIG_VALUE_0": f"https://{config.GITLAB_HOST}/",
        "GIT_CONFIG_KEY_1": key,
        "GIT_CONFIG_VALUE_1": f"git@{config.GITLAB_SSH_HOST}:",
        "GIT_CONFIG_KEY_2": key,
        "GIT_CONFIG_VALUE_2": f"ssh://git@{config.GITLAB_SSH_HOST}/",
    }
    # ⚠ 已知的小落差：`dev-container/entrypoint.sh` 有一處把 alias 寫死在 `NO_PROXY` 裡
    #   （只錄模型 API 的那個錄製範圍）。改了 `CLAUDE_PTY_GITLAB_PROXY_ALIAS` 之後那一處
    #   不會跟著改，代理的流量會多繞一次 mitm。**只影響錄製時的路徑，不影響能不能通**，
    #   所以留著不動；真要改 alias 的人請一併看那一行。
    return env


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
