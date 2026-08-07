"""對帳器：DB registry ↔ dockerd 真實狀態（ADR 0008 階段 5）。

**獨立 process，不是 Flask worker**。兩個理由：
  1. 多 worker 下若每個 worker 各跑一份對帳，它們會在 DB 上互撞；單一 owner 最乾淨。
  2. 它不在 web 請求路徑上——這正好解掉 ADR 0006 review 留下的 S1（對帳持鎖阻塞請求）。

DB 只是便利/路由層，真相在 container + 那份掛進去的設定目錄（ADR 0007；ADR 0014 之後
是 per-user 的 `user-{id}/claude`），故漂移一律以 dockerd 為準修正，且修正過程不會影響
任何對話的可續性。

    uv run ... python -m server.reconciler          # 常駐，每 RECONCILE_INTERVAL 秒一輪
    uv run ... python -m server.reconciler --once   # 跑一輪就結束（測試 / cron 用）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import signal
import socket
import threading
from contextlib import suppress

import docker


from . import auth, config, gitlab_proxy, user_proxy, views
from .db import init_db, session_scope
from .models import (
    END_EXITED,
    END_GONE,
    END_IDLE,
    STATUS_EXITED,
    Lease,
    User,
    View,
    utcnow,
)
from .models import Session as SessionRow
from .sessions import (
    ALIVE_STATES,
    _is_creating_within_grace,
    _is_ready,
    age_seconds,
    archive,
    is_stale_half_built,
    stamp_ready_if_first,
)

# 「這顆容器這輪問不到」的哨兵。不能用 None——docker-py 的 remove_container 成功時
# 回的就是 None（見 reconcile_once 內 _isolated 的說明）。
STUCK = object()


def acquire_lease(name: str, owner: str, ttl: int) -> bool:
    """取得/續約互斥租約；被別人持有且未過期則回 False（review M2）。

    交易以 immediate 開啟，讓「讀租約 → 判斷 → 寫回」在 SQLite 下也是互斥的，
    否則兩個 reconciler 可能同時判定「沒人持有」。

    ⚠ 互斥靠的就是這個 immediate：漏了它，兩個 reconciler 同時看到一張過期的租約
      時會雙雙判定可接手，接著同時跑破壞性清理——正是這張租約要防的事
      （`sessions._pty_writer()` 是同一個形狀的坑）。
    """
    now = utcnow()
    with session_scope(immediate=True) as s:
        row = s.get(Lease, name)
        if row is None:
            s.add(Lease(name=name, owner=owner,
                        expires_at=now + _dt.timedelta(seconds=ttl)))
            return True
        if row.owner != owner and row.expires_at > now:
            return False                    # 別人持有中且未過期
        row.owner = owner                   # 自己續約，或接手已過期的租約
        row.expires_at = now + _dt.timedelta(seconds=ttl)
        return True


def still_leader(name: str, owner: str) -> bool:
    """租約是否仍屬於自己且未過期。

    租約只在每輪**開頭**取得，但一輪可能跑很久（大量 exited container 要逐個
    force-remove）。跑超過 TTL 時另一個實例會合法接手，而舊的仍在迴圈裡做破壞性操作
    ——兩個 reconciler 同時刪同一批東西。破壞性動作前再確認一次，過期就讓這輪停手
    （下一輪重新競爭租約）。
    """
    with session_scope() as s:
        row = s.get(Lease, name)
        return bool(row and row.owner == owner and row.expires_at > utcnow())


def reconcile_once(client: docker.DockerClient | None = None,
                   owner: str | None = None) -> dict:
    """跑一輪對帳，回傳各項處理計數。

    `owner` 有值時，每個破壞性操作前確認租約仍屬於自己（見 still_leader）。
    """
    client = client or docker.from_env(timeout=config.DOCKER_TIMEOUT)

    def _leading() -> bool:
        return owner is None or still_leader("reconciler", owner)

    stats = {"gone": 0, "exited_removed": 0, "views_cleaned": 0,
             "orphan_containers": 0, "idle_reclaimed": 0,
             "states_refreshed": 0, "ready_stamped": 0,
             "containers_stuck": 0,
             "proxies_removed": 0, "proxies_converged": 0}

    # ⚠ 這一發是**整輪唯一不可失敗**的 docker 呼叫：它一次拿回所有容器的狀態，不是
    #   per-container，所以它掛掉代表 daemon 整體有問題（那時本來也做不了任何事）。
    #   逐顆容器的呼叫全部包在 _isolated() 裡，見下。
    live = {c.name: c for c in client.containers.list(
        all=True, filters=config.SESSION_FILTERS)}

    def _isolated(label: str, fn, *a, **kw):
        """跑一個可能卡住的 docker 呼叫；壞掉只影響這一顆容器，不讓整輪陣亡（ADR 0012）。

        ⚠ 這裡要接的是 **Exception 而不是 docker.errors.APIError**。2026-07-27 那次全站
          停擺就是這個差別：一顆容器卡在 `removing`，`remove_container` 丟的是 urllib3 的
          `ReadTimeout`（不是 APIError），它一路穿出 reconcile_once、被主迴圈接住印成
          「本輪失敗」——於是**與那顆容器完全無關**的歸檔、view 清理、租約清理全部停擺
          40 分鐘。失敗回 `STUCK`，代表這顆這輪先跳過、下輪再試。

        ⚠ 失敗值是哨兵不是 `None`：`remove_container` **成功時回的就是 None**，用 None
          當失敗記號會把每一次成功都當成失敗——登錄永遠不刪、計數永遠是 0
          （這一版初稿就是這樣寫的，被既有測試當場擋下）。
        """
        try:
            return fn(*a, **kw)
        except docker.errors.NotFound:
            raise                       # 「不在了」是有意義的答案，交給呼叫端判斷
        except Exception as e:          # noqa: BLE001 — 逾時/APIError/連線中斷都算「這顆問不到」
            stats["containers_stuck"] += 1
            print(f"[reconciler] ⚠ {label} 失敗，這輪跳過這一顆（下輪再試）：{e!r}",
                  flush=True)
            return STUCK

    # --- 0) 把「最後一次問到 dockerd 的狀態」寫進 DB（ADR 0012）-----------------------
    # 列表只讀這兩欄、不自己打 docker，所以這一步就是列表的資料來源。用上面那份 live
    # map，不額外打任何 per-container 呼叫。
    now = utcnow()
    with session_scope() as s:
        for row in s.query(SessionRow).all():
            c = live.get(row.container_name)
            if c is None and _is_creating_within_grace(row):
                continue                # 還在建立中：不是「不見了」，也還沒有狀態可記
            state = c.status if c is not None else "gone"
            if row.docker_state != state or row.state_checked_at is None:
                stats["states_refreshed"] += 1
            row.docker_state = state
            row.state_checked_at = now

    # --- 1) registry → dockerd：登錄有、container 不在或已結束 -----------------------
    to_remove: list[tuple[str, str | None]] = []   # (session_id, container_id)
    with session_scope() as s:
        for row in s.query(SessionRow).all():   # sessions 只存進行中的（結束的已歸檔）
            c = live.get(row.container_name)
            if c is None:
                if _is_creating_within_grace(row):
                    continue    # 正在建立中，container 尚未出現是正常的（review B1）
                to_remove.append((row.id, None))          # container 已不存在
            elif c.status not in ALIVE_STATES:
                row.status = STATUS_EXITED
                to_remove.append((row.id, c.id))          # 已結束 → 連 stopped container 一起收
    for sid, container_id in to_remove:
        if not _leading():
            break                                         # 租約在這輪中途被接手，停手
        with suppress(Exception):
            views.close_views(sid)                        # 先收該 session 的 ttyd
        if container_id:
            # exited-but-present 的 container 不刪會累積 writable layer，且刪了登錄後
            # 就再也沒人記得它（前一輪 review 的教訓）——故先刪 container 再刪登錄。
            try:
                if _isolated(f"刪除容器 {container_id[:12]}",
                             client.api.remove_container, container_id,
                             force=True) is STUCK:
                    continue                              # 這輪刪不掉就留著，下輪再試
                stats["exited_removed"] += 1
            except docker.errors.NotFound:
                pass
        else:
            stats["gone"] += 1
        # 歸檔而非刪除：登錄離開 sessions，那段歷史留在 session_history（ADR 0010）
        with suppress(Exception):
            archive([sid], END_EXITED if container_id else END_GONE)

    # --- 2) 補上沒人記過的「就緒」時刻（ADR 0012）--------------------------------------
    stats["ready_stamped"] = _stamp_ready_backstop(live, _isolated)

    # --- 3) 清掉已自行退出的 view（釋放 port）----------------------------------------
    stats["views_cleaned"] = _clean_views()

    # --- 4) dockerd → registry：沒人認領的 container（worker 建到一半掛掉）------------
    stats["orphan_containers"] = _remove_orphans(client, live, _isolated) if _leading() else 0

    # --- 5) idle 回收（預設停用，見 config 說明）--------------------------------------
    stats["idle_reclaimed"] = _reclaim_idle(client, _isolated) if _leading() else 0

    # --- 6) per-user GitLab 代理與網路（ADR 0016）------------------------------------
    # 放在最後：它的期望狀態依賴「誰還有活著的 session」，而上面幾步剛好在收斂那件事。
    # ⚠ **關掉這個功能時也要跑**（不是 `if gitlab_enabled()` 才跑）：那時的期望狀態是
    #   「一顆代理與一張網路都不該有」，所以這一輪負責把既有的收乾淨。跳過的話，部署者
    #   拿掉 CLAUDE_PTY_GITLAB_HOST 之後，那些代理會**帶著 PAT 永遠留在機器上**，
    #   而且繼續佔著位址池——沒有任何東西會再回頭看它們。
    removed, converged = _converge_proxies(client, live, _isolated, _leading)
    stats["proxies_removed"] = removed
    stats["proxies_converged"] = converged

    return stats


def _converge_proxies(client: docker.DockerClient, live: dict, isolated,
                      leading=None) -> tuple[int, int]:
    """把 per-user GitLab 代理與網路收斂到期望狀態（ADR 0016）。回傳 (收掉幾顆, 修好幾顆)。

    期望狀態很簡單：**有活著的 session 且 PAT 可用的使用者，才該有網路與代理。**
    形狀與 k8s 的 Deployment 相同——定期看一眼，該在而不在就補，不該在就收。

    ⚠ **功能被關掉時這支照跑**，不是不呼叫（呼叫端也不該加 `if gitlab_enabled()`）。
      那時期望狀態是「一顆都不該有」，所以這一輪負責收乾淨；`active` 會是空集合，
      於是每顆代理都落進「沒人用」、每張網路都被回收，補建迴圈什麼都不做。
      跳過的話，那些代理會**帶著 PAT 永遠留在機器上**，還繼續佔著位址池。

    ⚠ **「有活著的 session」不可以只看 `live` 快照。** `live` 是輪初拿的，而代理與網路是
      即時查詢——使用者上一場剛結束、下一場正在建立時會誤判成「沒有 session」，於是收掉
      正要被接上的代理。判準要**含 DB 裡 `status=creating` 且在寬限期內的列**。

    ⚠ **PAT 讀不到時的處置要分辨三態**（`auth.gitlab_pat_state`）：
      · `"none"`（明確清除）→ **移除代理**。「我覺得外洩了」必須立刻生效。
      · `"unreadable"`（換過 `SECRET_KEY`）→ **什麼都不做**。整站的 PAT 會一起解不開，
        當成「沒設」就是把所有還在服務中的代理一起收掉。
      這兩者的差別是這整段最重要的一條規矩，**不可以退回成「讀不到就當沒設」**。

    ⚠ **設定新舊問容器自己**（`/_state`），不另存狀態：DB 會漂，label 建立後改不了。
    ⚠ 過期時**熱重載不重建**：重建會斷掉這個使用者其他 session 正在進行的 git 操作。
    """
    # 誰有活著的 session：live 快照 ∪ DB 裡還在建立寬限期內的列。
    # ⚠ 功能被關掉時這裡是**空集合**，於是下面每一顆代理都落進「沒人用」而被收掉、
    #   每一張網路都被回收，補建迴圈（`active - seen`）則什麼都不做。關閉＝收乾淨，
    #   不是「停止管理」——停止管理會讓帶著 PAT 的容器孤兒化。
    active: set[int] = set()
    if config.gitlab_enabled():
        with session_scope() as s:
            for row in s.query(SessionRow).all():
                c = live.get(row.container_name)
                if ((c is not None and c.status in ALIVE_STATES)
                        or _is_creating_within_grace(row)):
                    active.add(row.user_id)

    removed = converged = 0
    seen: set[int] = set()
    # ⚠ 每顆之前再確認一次租約還是自己的。理由與主迴圈那條 `if not _leading(): break`
    #   相同，而這一段更需要：卡住的容器每顆吃滿 DOCKER_TIMEOUT，幾顆就超過租約 TTL。
    #   重新部署讓新舊 reconciler 短暫並存時，過期的那一份會拿著輪初的快照做**依名稱**的
    #   `remove(uid)`——而那個名字此刻可能已經是新 leader 剛補建好的那一顆。
    lead = leading or (lambda: True)

    for c in user_proxy.list_all(client):
        if not lead():
            break
        if c.labels.get(config.TEST_LABEL_KEY):
            # 測試建立的：它不在**我們的** DB 裡是正常的（對稱於 _remove_orphans 那道檢查）
            continue
        uid = user_proxy.owner_of(c)
        if uid is None:
            # label 壞掉／不是數字：認不出主人就沒有人管得了它，**而它握著一把 PAT**。
            with suppress(docker.errors.NotFound):
                if isolated(f"收掉認不出主人的代理 {c.name}",
                            client.api.remove_container, c.id, force=True) is not STUCK:
                    removed += 1
            continue
        seen.add(uid)
        try:
            if uid not in active:
                # 沒有活著的 session 了。⚠ 寬限期依**代理自己**的 Created：剛建好、session
                #   還在路上的那一刻不可以收（同 _remove_orphans 的理由）。
                if age_seconds(c.attrs.get("Created", "")) < config.ORPHAN_GRACE:
                    continue
                if isolated(f"收掉沒人用的代理 {c.name}",
                            user_proxy.remove, client, uid) is not STUCK:
                    removed += 1
                continue

            state = auth.gitlab_pat_state(uid)
            if state == "none":
                # 使用者明確清除了 PAT → 立刻失效（這是安全需求，見 docstring）
                if isolated(f"使用者 {uid} 已清除 PAT，收掉代理",
                            user_proxy.remove, client, uid) is not STUCK:
                    removed += 1
                continue
            if state != "ok":
                continue                  # unreadable：什麼都不做

            if c.status == "created":
                # **半成品**：`create_container` 成功但 `put_archive`／`start` 沒走完就被
                # 中斷。它的 `/etc/nginx` 可能還是 image 的預設設定——直接 start 起來的話
                # `/_state` 會 404、`running_state` 永遠回 None，而「問不到就別亂動」那條
                # 規則會讓它**永遠卡在這裡**。
                # ⚠ 所以夠舊的 created 一律當半成品收掉，交給補建路徑重來一次。判準與
                #   `sessions._ensure_user_proxy` 共用同一支，還新就別碰（那是別人正在建）。
                # ⚠ 補的是**下一輪**，不是這一輪：上面已經 `seen.add(uid)`，而補建迴圈跑的
                #   是 `active - seen`。所以收掉到補回來之間有一個 RECONCILE_INTERVAL 的
                #   空窗。不改成本輪就補是刻意的——那要嘛得在迴圈中途改 `seen`（邊走邊改
                #   判斷依據），要嘛得把移除與補建耦合起來，兩者都比等一輪糟。
                if not is_stale_half_built(c):
                    continue
                if isolated(f"收掉半成品代理 {c.name}",
                            user_proxy.remove, client, uid) is not STUCK:
                    removed += 1
                continue
            if c.status in ("dead", "removing"):
                # ⚠ 這兩種**啟動不起來**，不可以歸到下面的「直接 start」。start 一顆 dead
                #   容器必定失敗 → 每輪 `containers_stuck` +1 → 而它永遠不會被收掉重建，
                #   於是這個人的 GitLab **永久失效直到有人手動 rm**。罕見，但這一整段存在
                #   的理由就是堵「永久且無聲的失效」，自己留一條就沒有意義了。
                if isolated(f"收掉 {c.status} 的代理 {c.name}",
                            user_proxy.remove, client, uid) is not STUCK:
                    removed += 1
                continue
            if c.status not in ALIVE_STATES:
                # exited：設定已經在它裡面，直接 start——不必再碰 PAT。
                # ⚠ **先把它上次為什麼死講出來，再重啟。** 有一整類原因是重啟救不了的，
                #   最常見的是「代理裡解不出 GitLab 的主機名」——nginx 在啟動時就要解析
                #   upstream，解不開直接 `[emerg] host not found in upstream` 拒絕啟動。
                #   那時這條分支會**每輪重啟一次、每輪再死一次**，而唯一的觀測訊號是
                #   `proxies_converged` 在跳——看起來像在收斂，其實是無聲的無窮迴圈。
                #   設定錯誤要讓人看得到，不然它會一直被當成「暫時的」。
                _note_proxy_down(c, uid, isolated)
                if isolated(f"重啟代理 {c.name}", client.api.start, c.id) is not STUCK:
                    converged += 1
                continue
            if c.status != "running":
                continue                  # restarting／paused：這輪先放著

            # 走到這裡代理是 running＝這一輪它是好的。把連續失敗計數與畫面上那句錯誤清掉。
            _note_proxy_ok(uid)

            pat = auth.gitlab_pat(uid)
            if not pat:
                continue                  # 與 state 之間的競態，下輪再看
            want = gitlab_proxy.fingerprint(pat)
            got = isolated(f"問代理 {c.name} 在跑什麼",
                           user_proxy.running_state, client, uid)
            if got is STUCK or got is None:
                continue                  # 問不到就別亂動
            # ⚠ 判 `is True`，不是 `is not STUCK`。`reload()` 有三種結局：成功回 `True`、
            #   **失敗回 `False`**（`nginx -t` 沒過／`mv` 失敗）、被 `_isolated` 攔下回
            #   `STUCK`。只排除 STUCK 的話 `False` 也會被算成「重載了」——而那正是最需要
            #   被看見的情況：設定換不上去，指紋永遠不收斂，每輪重跑一次，而唯一的觀測
            #   訊號在說「有重載」。假捷報比沒有訊號更糟。
            if got != want and isolated(f"熱重載代理 {c.name}",
                                        user_proxy.reload, client, uid, pat) is True:
                converged += 1
        except docker.errors.NotFound:
            continue                      # 目標剛好在這幾行之間被收掉了，下輪再看

    # 該有代理卻一顆都沒有的使用者：補建。
    # ⚠ 這一輪不可省——上面只走「已經存在的代理」，任何讓容器整個消失的路徑（手動 rm、
    #   建到一半失敗）都會變成永久且無聲的失效。
    for uid in sorted(active - seen):
        if not lead():
            break
        if auth.gitlab_pat_state(uid) != "ok":
            continue
        pat = auth.gitlab_pat(uid)
        if not pat:
            continue
        # ⚠ **不可以寫成 `suppress(NotFound)`**：`ImageNotFound` 是 `NotFound` 的子類，
        #   而 `_isolated` 對 `NotFound` 是**刻意 re-raise** 的。代理 image 沒拉到時，
        #   補建會每輪被無聲吞掉——而這一段存在的理由正是要堵「永久且無聲的失效」。
        # ⚠ `PoolExhausted` 不必在這裡接：`_isolated` 的 `except Exception` 會先吃掉它
        #   並印出訊息（那個訊息本身就講了該去清 network）。
        try:
            if isolated(f"補建使用者 {uid} 的代理",
                        _create_proxy, client, uid, pat) is not STUCK:
                converged += 1
        except docker.errors.ImageNotFound:
            print(f"[reconciler] ⚠ 代理 image 不在本機（{config.PROXY_IMAGE[:60]}），"
                  f"使用者 {uid} 的 GitLab 功能無法恢復——先 docker pull 它", flush=True)
        except docker.errors.NotFound:
            continue                      # session 剛好在這幾行之間沒了，下輪再看

    removed += _reap_user_networks(client, active, isolated, lead)
    return removed, converged


# 每個使用者的代理「連續」幾輪沒活著。**刻意留在記憶體、不落 DB**：
#   · reconciler 由租約保證同一時間只有一個實例，所以這個計數天生只有一個來源
#   · 它是過程量不是事實——重啟 reconciler 之後從零開始數正是**對的**行為
#   · 少一個 schema 欄位，也少一條要同步的狀態
# 落到 DB 的只有跨過門檻之後的**結論**（`users.gitlab_proxy_error`），那是給人看的。
_proxy_fails: dict[int, int] = {}


def _note_proxy_down(c, uid: int, isolated) -> None:
    """代理這一輪被發現沒活著：記一次；連續夠多次就把原因端到畫面上。

    ⚠ **為什麼不是第一次就報。** 代理偶爾重啟一輪是正常的（重新部署、daemon 抖動），
      每次都對使用者喊「你的 GitLab 壞了」就是狼來了。這條訊號要救的是另一類：設定錯了、
      而且**永遠不會自己好**——最典型的是主機名打錯，nginx 啟動時解不開 upstream 就拒絕
      啟動，於是每輪重啟、每輪再死，而 `proxies_converged` 每輪 +1 看起來還像在收斂。

    ⚠ **只取 nginx 自己的最後一行並截短。** 它的 `[emerg]` 訊息格式是
      `[emerg] … in <檔名>:<行號>`，**不含檔案內容**，所以不會漏出 PAT。整份 log 就不一定
      （日後若有人在代理裡加了會回顯設定的東西），所以不整包搬。
    ⚠ 讀不到 log 不是錯——這只是診斷，不可以因為它讓收斂停手。
    """
    n = _proxy_fails[uid] = _proxy_fails.get(uid, 0) + 1
    logs = isolated(f"讀代理 {c.name} 的 log", c.logs, tail=5)
    lines = ([] if logs is STUCK or not logs
             else logs.decode(errors="replace").strip().splitlines())
    said = lines[-1][:200] if lines else ""
    print(f"[reconciler] ⚠ 代理 {c.name} 沒活著（連續第 {n} 輪）"
          f"{'：' + said if said else ''}", flush=True)
    if n < config.PROXY_FAIL_THRESHOLD:
        return
    # 跨過門檻：把原因寫到使用者看得到的地方。沒有這一步，他看到的只有「GitLab 連不到」，
    # 然後去查 token、查網路、查 GitLab 是不是掛了——真正的答案卻一直只在容器 log 裡。
    with suppress(Exception), session_scope() as s:
        user = s.get(User, uid)
        if user is not None:
            user.gitlab_proxy_error = said or "代理起不來，而且容器沒有留下訊息"


def _note_proxy_ok(uid: int) -> None:
    """代理這一輪是好的：把連續計數與畫面上那句錯誤一起清掉。

    ⚠ **清除要無條件做**，不可以只在「這一輪剛好報過錯」時做。要判斷「先前有沒有報過」
      就得再存一份狀態，而那份狀態一旦與 DB 不同步（reconciler 一重啟就會），畫面上那句
      早就修好的錯誤會永遠留著——使用者會照著它去改一個本來就正確的設定。
    """
    _proxy_fails.pop(uid, None)
    with suppress(Exception), session_scope() as s:
        user = s.get(User, uid)
        if user is not None and user.gitlab_proxy_error is not None:
            user.gitlab_proxy_error = None


def _create_proxy(client: docker.DockerClient, user_id: int, pat: str) -> str:
    """補建一顆代理（連同它的網路）。給 `_converge_proxies` 用，讓那一行讀得下去。"""
    user_proxy.ensure_network(client, user_id)
    return user_proxy.create(client, user_id, pat)


def _reap_user_networks(client: docker.DockerClient, active: set[int], isolated,
                        leading=None) -> int:
    """收掉沒人用的 per-user 網路（釋放位址池）。

    ⚠ **寬限期依網路自己的 `Created`，不是代理的**：在「建好網路」與「建好代理」之間網路
      是空的，那時**沒有代理可以查年齡**——用代理的年齡判斷就會把剛建好的網路收掉。
    ⚠ 網路上還有容器時 docker 會拒絕移除，那是對的：交給下一輪。
    """
    reaped = 0
    lead = leading or (lambda: True)
    for net in user_proxy.list_networks(client):
        if not lead():
            break                     # 租約中途被接手就停手（同 _converge_proxies）
        # ⚠ 跳過帶測試標記的——與 `_converge_proxies`、`_remove_orphans` 那兩道檢查對稱。
        #   少了這道就是**正式的 reconciler 會去收測試建的網路**：測試那側的隔離只擋得住
        #   測試自己呼叫的那一輪，擋不住背景常駐的那一份。多半被 ORPHAN_GRACE 擋著，
        #   所以症狀是「偶爾、在慢的那次才紅」——那種 flaky 最難查。
        # ⚠ 網路的 label 要走 `attrs["Labels"]`：docker-py 的 `Network` **沒有** `.labels`
        #   （容器才有），而沒帶 label 時那個值是 `None` 不是 `{}`。
        if (net.attrs.get("Labels") or {}).get(config.TEST_LABEL_KEY):
            continue
        uid = user_proxy.owner_of(net)
        if uid is not None and uid in active:
            continue
        if age_seconds(net.attrs.get("Created", "")) < config.ORPHAN_GRACE:
            continue
        with suppress(docker.errors.NotFound, docker.errors.APIError):
            if isolated(f"收掉沒人用的網路 {net.name}", net.remove) is not STUCK:
                reaped += 1
    return reaped


def _stamp_ready_backstop(live: dict, isolated) -> int:
    """替「還沒被記過就緒」的 session 補記 ready_at。

    正常路徑是 `create()` 那條背景執行緒當場記（那最即時）；這裡撿的是它死掉的情形
    ——控制平面重啟、OOM kill 都會讓它消失。列表改成純讀 DB 之後（ADR 0012），沒有人補
    的話那些 session 會**永遠**顯示未就緒，而它們其實好好地跑著。

    只問「還沒記過的」那幾列，而且每顆都包在 isolated 裡：一顆卡住的容器不影響其他顆。
    """
    with session_scope() as s:
        pending = [(r.id, r.container_name)
                   for r in s.query(SessionRow).filter(SessionRow.ready_at.is_(None)).all()]
    stamped = 0
    for sid, name in pending:
        c = live.get(name)
        if c is None or c.status not in ALIVE_STATES:
            continue                     # 不在／已結束的不必問，上面的階段會處理它
        logs = isolated(f"讀容器 log {name}", c.logs, tail=200)
        # ⚠ 比 STUCK 不比 falsy：空 log（b""）是合法答案「還沒就緒」，不是失敗
        if logs is STUCK or not _is_ready(logs):
            continue
        with session_scope() as s:
            # 與前台的 `SessionManager._stamp_ready` 共用同一句條件式 UPDATE：就緒是
            # 單調的，只有第一次寫得進去，兩邊同時觀察到也不會互相覆蓋。
            # ⚠ 整個迴圈共用**一筆**交易（helper 收 s、不自己開 scope），所以逐顆蓋章
            #   不會變成 N 筆交易。
            stamped += stamp_ready_if_first(s, sid)
    return stamped


def _clean_views() -> int:
    """移除 ttyd 已不存在的 view 記錄。pid 尚未寫入且在寬限期內者不動——那是別的 worker
    正在進行中的 port 宣告（ADR 0008 階段 3 抓到的跨 worker race）。"""
    cutoff = utcnow() - _dt.timedelta(seconds=config.VIEW_CLAIM_GRACE)
    dead: list[int] = []
    with session_scope() as s:
        for row in s.query(View).all():
            if row.pid is None:
                if row.created_at < cutoff:
                    dead.append(row.id)
            elif not views._process_alive(row.pid):
                dead.append(row.id)
    if dead:
        with session_scope() as s:
            for vid in dead:
                obj = s.get(View, vid)
                if obj is not None:
                    s.delete(obj)
    return len(dead)


def _remove_orphans(client: docker.DockerClient, live: dict, isolated) -> int:
    """收掉 registry 沒有對應列、且帶有 session label 的 container。

    ⚠ 只認 label，不認名稱前綴——容器化部署下 compose 的基礎設施容器（control / nginx /
    reconciler 自己）名字也是 claude-pty-*，用前綴會把自己刪掉（2026-07-25 實測發生）。

    正常流程不會產生孤兒（create 先寫登錄列再起 container），但 worker 在兩者之間被
    kill 就會留下。加寬限期避免誤殺「剛建好、登錄列還沒轉正」的 container。
    """
    with session_scope() as s:
        known = {r.container_name for r in s.query(SessionRow.container_name).all()}
    removed = 0
    for name, c in live.items():
        if name in known:
            continue
        if c.labels.get(config.TEST_LABEL_KEY):
            # 測試建立的容器：它不在**我們的** DB 裡是正常的，不是孤兒。沒有這道檢查，
            # 正式 reconciler 會在測試跑到一半時把它的容器收掉（測試那側的 _ScopedClient
            # 擋的是反方向，兩邊都要有才對稱）。
            continue
        created = c.attrs.get("Created", "")
        if _age_seconds(created) < config.ORPHAN_GRACE:
            continue                                     # 可能正在建立中，下輪再看
        with suppress(docker.errors.NotFound):
            # isolated：一顆刪不掉（逾時、卡在 removing）不可以讓剩下的孤兒都不處理
            if isolated(f"刪除孤兒容器 {c.id[:12]}",
                        client.api.remove_container, c.id, force=True) is not STUCK:
                removed += 1
    return removed


def _reclaim_idle(client: docker.DockerClient, isolated) -> int:
    """回收閒置過久的 session。**預設停用**（config.IDLE_TIMEOUT_HOURS = 0）。"""
    hours = config.IDLE_TIMEOUT_HOURS
    if hours <= 0:
        return 0
    cutoff = utcnow() - _dt.timedelta(hours=hours)
    stale: list[tuple[str, str | None]] = []
    with session_scope() as s:
        for row in s.query(SessionRow).filter(SessionRow.last_active_at < cutoff).all():
            stale.append((row.id, row.container_id))
    reclaimed = 0
    for sid, container_id in stale:
        with suppress(Exception):
            views.close_views(sid)
        if container_id:
            # ⚠ **只有 NotFound 才算「已經達成目標」。** 其他 docker 錯誤（daemon 暫時不
            #   可用、資源忙碌）必須保留這一列、下一輪重試——原本這裡把 APIError 一起
            #   suppress 掉然後照樣歸檔，結果是歷史紀錄宣告「這場因閒置結束」而容器還在跑，
            #   計數也多報。上面那條主對帳路徑早就寫對了（`except APIError: continue`），
            #   這條 idle 路徑漏了（交叉審查 2026-07-26 指出）。
            try:
                if isolated(f"idle 回收刪容器 sid={sid}",
                            client.api.remove_container, container_id,
                            force=True) is STUCK:
                    continue        # 刪不掉就保留登錄下輪重試（絕不可以照樣歸檔，見上）
            except docker.errors.NotFound:
                pass
        with suppress(Exception):
            archive([sid], END_IDLE)
        reclaimed += 1
    return reclaimed


# container 建立至今幾秒。**實作在 sessions**（`sessions.age_seconds`）；
# 這裡保留舊名字當別名，呼叫端不必全部改。
_age_seconds = age_seconds


_stopping = threading.Event()


def _install_signal_handlers() -> None:
    """優雅停機：收到 SIGTERM/SIGINT 時跳出迴圈，而不是被硬砍在對帳中途。

    背景 worker 的「正式化」不是套 gunicorn（它是 WSGI HTTP 伺服器，這裡沒有 HTTP 介面
    可服務），而是：可靠的重啟策略、單一執行者的強制（見 acquire_lease），以及這裡的
    優雅停機——讓 `docker stop` 不會在 force-remove 做到一半時把程序砍掉。
    """
    def _handler(signum, _frame):
        print(f"[reconciler] 收到訊號 {signum}，完成本輪後結束", flush=True)
        _stopping.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claude-pty-reconciler", description="claude-pty registry 對帳器")
    parser.add_argument("--once", action="store_true", help="跑一輪就結束")
    parser.add_argument("--interval", type=int, default=config.RECONCILE_INTERVAL,
                        help="常駐模式的輪詢秒數")
    args = parser.parse_args(argv)

    init_db()
    _install_signal_handlers()
    # 有界 timeout：見 config.DOCKER_TIMEOUT（預設 60 秒是這個系統踩過的坑）
    client = docker.from_env(timeout=config.DOCKER_TIMEOUT)
    owner = f"{socket.gethostname()}:{os.getpid()}"
    while not _stopping.is_set():
        try:
            # 租約 TTL 給兩倍輪詢間隔：正常會在到期前續約；持有者掛掉後
            # 其他實例最多等這麼久就能接手。
            if not acquire_lease("reconciler", owner, args.interval * 2 + 10):
                if args.once:
                    print("[reconciler] 另一個實例持有租約，本輪跳過", flush=True)
                    return 0
                _stopping.wait(args.interval)   # 可被訊號立即喚醒
                continue
            stats = reconcile_once(client, owner=owner)
            if any(stats.values()):
                print(f"[reconciler] {stats}", flush=True)
        except Exception as e:  # noqa: BLE001 — 常駐程序：任一輪失敗都不可終止迴圈，下輪再試
            print(f"[reconciler] 本輪失敗：{e!r}", flush=True)
        if args.once:
            return 0
        _stopping.wait(args.interval)   # 用 Event 而非 sleep：停機訊號能立刻中斷等待
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
