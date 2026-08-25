"""唯讀查詢與序列化（從 sessions.py 拆出，2026-08-25）。

四個頂層函式把 DB row 轉成對外 dict；QueryMixin 掛在 SessionManager 上，
需要宿主提供 `self._docker`。
"""

from __future__ import annotations

import datetime as _dt
from contextlib import suppress
from dataclasses import dataclass

import docker
from sqlalchemy.orm import joinedload

from . import config, crypto
from .constants import DRIVER_MARKER
from .db import session_scope
from .errors import SessionNotFound
from .models import STATUS_CREATING, SessionHistory, utcnow
from .models import Session as SessionRow


def _is_ready(logs: bytes) -> bool:
    """「就緒」的唯一定義：容器 log 出現 entrypoint 的 DRIVER_MARKER。

    ⚠ 列表與單筆查詢都必須用這一個函式。這個判定曾經只寫在 status() 裡，list() 另外
    寫了一份，於是同一個 session 在列表上顯示就緒、點進去卻不是。
    """
    return DRIVER_MARKER.encode() in logs


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


def _is_creating_within_grace(row) -> bool:
    """`creating` 且尚在寬限期內＝container 正在起，還沒出現在 docker 是正常的。

    create() 先寫登錄列、才花數十秒起 container（restricted 要等 trivy DB + 套 iptables）。
    這段期間若被判定為 gone 而刪列，create() 回頭找不到自己的列會失敗，並反過來把剛起好的
    container 收掉（review B1，實測窗口 40s 起動 vs 30s 對帳週期）。
    """
    if row.status != STATUS_CREATING:
        return False
    return (utcnow() - row.created_at).total_seconds() < config.CREATING_GRACE


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


class QueryMixin:
    """唯讀查詢（從 SessionManager 拆出，2026-08-25）。需要宿主提供 `self._docker`。"""

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
