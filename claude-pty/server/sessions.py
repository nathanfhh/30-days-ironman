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
from contextlib import suppress
from dataclasses import replace

import docker
from sqlalchemy.exc import IntegrityError

from . import config, trivy_db, user_proxy
from .db import session_scope
from .models import (
    END_TERMINATED,
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
from .attach import AttachMixin, _close_socketio, _discard_attach, close_attach  # noqa: F401
from .constants import ALIVE_STATES, DRIVER_MARKER  # noqa: F401
from .credentials import (  # noqa: F401
    _claude_base,
    _guard_credentials,
    _put_cli_token,
    claude_credentials_state,
    credentials_state,
)
from .errors import SessionError, SessionNotFound  # noqa: F401
from .jaeger import _jaeger_reachable  # noqa: F401
from .preflight import image_uid, preflight  # noqa: F401
from .query import (  # noqa: F401
    Filters,
    QueryMixin,
    _is_creating_within_grace,
    _is_ready,
    age_seconds,
    is_stale_half_built,
    parse_docker_time,
    stamp_ready_if_first,
    _history_to_dict,
    _last_known_state,
    _ready_from_row,
    _to_dict,
)
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


def archive(sids, reason: str, actor: dict | None = None) -> int:
    """把 session 登錄搬進 `session_history` 後刪除，回傳實際歸檔的筆數（ADR 0010）。

    ⚠ 這是「session 結束」的唯一出口——任何直接 `s.delete(SessionRow)` 都會讓那段歷史
    憑空消失。搬檔與刪列在同一交易內完成，不會出現「刪了但沒留下紀錄」的中間狀態。

    `actor`：按下終止的那個人（`g.user`）。**只有人為終止才給**——reconciler 判定的
    exited / gone 沒有「誰」，硬填一個會讓歷史說謊。

    `immediate=True`：web worker 的 list() 對帳與 reconciler 會同時歸檔同一筆
    （前者判 gone、後者判 exited），沒有序列化的話兩邊都先讀到列、各寫一筆歷史，
    結束原因還取決於誰先寫。`session_history.session_id` 的 UNIQUE 是第二道保險。

    ⚠ **先收通道再歸檔，而且收不掉的不可以歸檔。** 刪列會 cascade 掉 views 與
      mitm_views，而那些記錄是**唯一**記得 pid／process group 的地方——cleanup 失敗
      還硬刪列的話，殘留的 ttyd／socat（連同它握著的 WebSocket）就變成永久孤兒：
      `_clean_views` 只走 DB 列、`_remove_orphans` 只管 container，沒有人依 port 或
      process 掃描。所以 close 失敗的 sid **不進** `_archive_txn`（session 列與
      tracking row 都留下，供 reconciler／下一次操作重試），收成功（或本來就沒有）
      的才歸檔；有被擋下的就拋出，讓呼叫端講出「沒有歸檔成功」——reconciler 的兩個
      呼叫端本來就 suppress 例外（下一輪會再試），不受影響。

    ⚠ close 的兩支都**自己開交易**，必須在 `session_scope` 之外呼叫（`database is
      locked` 的既有教訓，見 auth.change_password 同一段註解）。
    """
    sids = [sid for sid in sids if sid]
    if not sids:
        return 0
    from .mitm_views import MitmViewError as _MitmViewError
    from .mitm_views import close_mitm_views
    from .views import close_views

    archivable: list[str] = []
    blocked: list[tuple[str, Exception]] = []
    for sid in sids:
        errors: list[Exception] = []
        # 各自 try：ttyd 收不掉不能跳過 relay 的清理，反之亦然。
        try:
            close_views(sid)  # 等冪：沒有 view 就回 0
        except Exception as exc:  # noqa: BLE001 — 一條收不掉不能擋掉其餘通道的收尾
            errors.append(exc)
        try:
            close_mitm_views(sid)  # 等冪：沒有 relay 就回 0
        except Exception as exc:  # noqa: BLE001 — 同上
            errors.append(exc)
        if errors:
            blocked.append((sid, errors[0]))
        else:
            archivable.append(sid)
    archived = 0
    if archivable:
        try:
            archived = _archive_txn(archivable, reason, actor)
        except IntegrityError:
            # UNIQUE 兜底擋下了：這批裡有人已被另一個 worker 歸檔。目標已達成，不是錯誤
            # ——尤其不該讓使用者按「終止」時看到 500。
            return 0
    if blocked:
        first_sid, first_exc = blocked[0]
        raise _MitmViewError(
            f"{len(blocked)} 場的通道收不掉，先不歸檔（{first_sid}：{first_exc}）；該場的登錄已保留，處理乾淨後再試一次"
        ) from first_exc
    return archived


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


# ⚠ 這裡曾經有 `_require_credentials_mountpoint()`：憑證以前是以檔案**掛**進容器的，
#   巢狀 bind mount 在新版 runc（openat2 + securejoin）上落點不存在就 exit 125。
#   現在憑證由 `_put_cli_token` 用 put_archive 送進容器自己的 writable layer——是檔案
#   沒錯，但不是 mount，所以那整個問題類別連同那個函式一起消失。
#   （中間曾經改走環境變數；那條路解掉了掛載問題，卻讓值出現在 `docker inspect` 與每一個
#     子行程的環境裡。現在的做法兩個都避開，見 config.SESSION_TOKEN_FILE。）


class SessionManager(AttachMixin, QueryMixin):
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
        user_proxy.ensure_user_network(self._docker, user_id)

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
            has_proxy = user_proxy.ensure_user_proxy(self._docker, user_id)

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

    def touch(self, sid: str) -> None:
        """更新最後活動時間（idle 回收與 UI 顯示用）。"""
        with session_scope(immediate=True) as s:
            row = s.get(SessionRow, sid)
            if row is not None:
                row.last_active_at = utcnow()

    # --- PTY 通道 ---------------------------------------------------------------

    # --- 內部 -----------------------------------------------------------------
