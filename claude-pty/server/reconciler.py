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


from . import config, views
from .db import init_db, session_scope
from .models import (
    END_EXITED,
    END_GONE,
    END_IDLE,
    STATUS_EXITED,
    Lease,
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
             "containers_stuck": 0}

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

    return stats


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
