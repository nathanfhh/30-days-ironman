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
import re
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

# ---- 從本檔拆出去的模組（2026-08-25）。這裡 re-export 讓既有的 `from .sessions import X`
#      與 `sessions.X` 全部不用改；新程式請直接 import 來源模組。
from .constants import ALIVE_STATES, DRIVER_MARKER  # noqa: F401
from .credentials import (  # noqa: F401
    _CLAUDE_BASE,
    _guard_credentials,
    _put_cli_token,
    claude_credentials_state,
    credentials_state,
)
from .errors import SessionError, SessionNotFound  # noqa: F401
from .jaeger import _jaeger_reachable  # noqa: F401
from .preflight import image_uid, preflight  # noqa: F401
from .provision import (  # noqa: F401
    _claude_json_seed,
    _write_json_atomic,
    ensure_system_user,
    provision_user_space,
)
from .run_kwargs import (  # noqa: F401
    Profile,
    _as_bool,
    _gitlab_env,
    _otel_env,
    _stored_profile,
    build_run_kwargs,
)


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


# ⚠ 這裡曾經有 `_require_credentials_mountpoint()`：憑證以前是以檔案**掛**進容器的，
#   巢狀 bind mount 在新版 runc（openat2 + securejoin）上落點不存在就 exit 125。
#   現在憑證由 `_put_cli_token` 用 put_archive 送進容器自己的 writable layer——是檔案
#   沒錯，但不是 mount，所以那整個問題類別連同那個函式一起消失。
#   （中間曾經改走環境變數；那條路解掉了掛載問題，卻讓值出現在 `docker inspect` 與每一個
#     子行程的環境裡。現在的做法兩個都避開，見 config.SESSION_TOKEN_FILE。）


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
