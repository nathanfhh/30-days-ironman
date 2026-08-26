"""mitmweb UI 的 on-demand relay（ADR 0021）。

    開網頁看流量 → 臨時起一顆 `socat`（listen 在 TTYD_BIND 的某個 port）
                 → 每條連線 fork 一次 `docker exec` 進 session 容器接它 loopback 的 mitmweb
    session 收掉    → `sessions.archive()` 連帶把這顆 socat 收掉

形狀刻意與 `views.py`（ttyd）一模一樣，而且**能共用的都直接用它的**：`_spawn_detached`
（double-fork，理由見那邊）、`_kill`／`_process_alive`（PID 回收的身分綁定）、`_port_open`。
那幾支各自都修過三、四個很難查的坑；複製一份等於接手維護第二份會漂的副本。

⚠ **relay 只認 container id，而那個 id 只從 DB 來。** 使用者輸入不進入這條路徑的任何一個
  位置：socat 的位址是我們組的、橋接腳本收的是位置參數（不經 shell 展開）。所以「session
  裡的東西影響 relay 的建立」這件事不成立。

⚠ **這裡不做授權。** 誰能開、誰能連，全部在 `app.auth_mitm` 一個地方判（nginx 每一發請求
  都會打它）。這個模組只回答「有沒有一條通往那顆容器的路」。
"""

from __future__ import annotations

import datetime as _dt
import os
import signal
import socket
import time
from contextlib import suppress

from sqlalchemy.exc import IntegrityError

from . import config, views
from .db import session_scope
from .models import MitmView, utcnow


class MitmViewError(RuntimeError):
    pass


# 我們自己會起的 relay 程序名（＝ `_kill` / `_process_alive` 的身分白名單）。
# ⚠ 同 views._OUR_TTYD_NAMES 的理由：這是送 SIGTERM 前的唯一把關，比對的是 argv[0] 的
#   basename 而不是「cmdline 裡有沒有這五個字」。
_OUR_RELAY_NAMES = frozenset({"socat"})


# --- 對外 API --------------------------------------------------------------------


def open_mitm_view(session_id: str, container_id: str) -> dict:
    """為 session 開一條 mitmweb relay；已有活著的就沿用（點兩次不會多起一條）。

    `container_id` 必須來自 DB（`sessions.container_id`），不可以是任何形式的使用者輸入。
    """
    if not container_id:
        raise MitmViewError("這一場沒有容器可以連（可能已經結束）")
    existing = _alive_relay(session_id)
    if existing is not None:
        return existing

    for port in range(config.MITM_PORT_MIN, config.MITM_PORT_MAX + 1):
        row_id = _claim_port(session_id, port)
        if row_id is _PEER:
            # 別的 worker 已為同一 session 建了 relay（session_id UNIQUE）。換 port 沒有
            # 意義（撞的不是 port），等它就緒後沿用，完全比照 views.open_view 的分岔。
            other = _await_peer(session_id)
            if other is not None:
                return other
            raise MitmViewError("另一個 worker 正在為此 session 建立流量畫面，請稍後再試")
        if row_id is None:
            continue  # 這個 port 被別的 session 佔走 → 換下一個
        pid = None
        try:
            pid = views._spawn_detached(_socat_argv(port, container_id))
            if _wait_ready(port, pid):
                with session_scope(immediate=True) as s:
                    row = s.get(MitmView, row_id)
                    if row is None:  # 宣告被清掉了（不該發生）→ 當失敗處理
                        raise MitmViewError("relay 宣告已消失")
                    row.pid = pid
                return _relay_dict(row_id, session_id, port, pid)
        except Exception:
            # spawn 之後任一步失敗都必須收掉那顆 socat，否則它活著卻沒有人記得 pid
            # （同 views.open_view 的 review H3）。
            _kill_spawned(pid)
            _drop(row_id)
            raise
        _kill_spawned(pid)
        _drop(row_id)
    raise MitmViewError(f"{config.MITM_PORT_MIN}-{config.MITM_PORT_MAX} 無可用 port")


def close_mitm_views(session_id: str) -> int:
    """收掉某 session 的 relay。等冪（沒有就回 0）。

    ⚠ **這一支是必要的，不像 ttyd 有 `-q` 可以自退。** socat 是 `TCP-LISTEN,fork`：
      沒有任何 client 時它照樣在那裡聽著，永遠不會自己結束。session 收掉之後沒有人再
      呼叫它，而那個 port 會一直被佔住，`views.inspect_ttyd` 那種對帳頁也看不到它
      （它只掃 ttyd 的名字）。所以生命週期必須明確地掛在 archive 上。
    """
    closed = 0
    stuck: list[int] = []
    with session_scope(immediate=True) as s:
        for row in s.query(MitmView).filter(MitmView.session_id == session_id).all():
            if not _kill(row.pid):
                # 同 views.close_views：**不刪這一列**。刪掉唯一的追蹤記錄等於把一顆還活著
                # 的 socat 變成孤兒，而它正是我們要收的那個東西。
                stuck.append(row.pid or 0)
                continue
            s.delete(row)
            closed += 1
    if stuck:
        raise MitmViewError(f"收不掉 {len(stuck)} 條流量畫面通道（pid {', '.join(str(p) for p in stuck)}）")
    return closed


def list_mitm_views(session_id: str) -> list[dict]:
    """列出仍存活的 relay；順手清掉已死的殘留記錄（釋放 port）。

    ⚠ pid 尚未寫入（NULL）的列是**另一個 worker 剛搶到 port、socat 還沒起來**的 in-flight
      宣告，不可當死的刪掉（同 views.list_views 在 2026-07-25 抓到的跨 worker race）。
      只有超過寬限期仍無 pid 才回收。
    """
    out, dead = [], []
    cutoff = utcnow() - _dt.timedelta(seconds=config.VIEW_CLAIM_GRACE)
    with session_scope() as s:
        for row in s.query(MitmView).filter(MitmView.session_id == session_id).all():
            if row.pid is None:
                if row.created_at < cutoff:
                    dead.append(row.id)
                continue
            if _process_alive(row.pid):
                out.append(_relay_dict(row.id, row.session_id, row.port, row.pid))
            else:
                dead.append(row.id)
    for rid in dead:
        _drop(rid)
    return out


# --- port 分配（DB UNIQUE 為跨 worker 仲裁）-------------------------------------

_PEER = object()  # _claim_port 的哨兵：撞的是 session_id，不是 port


def _claim_port(session_id: str, port: int):
    """試著佔一個 port。回傳 row id／`None`（該 port 被別場佔走）／`_PEER`（同場已有）。

    兩個 UNIQUE（port、session_id）光看 IntegrityError 分不出撞了哪一個；不分清楚就會把
    「同一場已有 relay」誤當成「port 撞號」而白掃完整個範圍（views._claim_port 的教訓）。
    """
    try:
        with session_scope() as s:
            row = MitmView(session_id=session_id, port=port)
            s.add(row)
            s.flush()
            return row.id
    except IntegrityError:
        pass
    with session_scope() as s:
        if s.query(MitmView).filter(MitmView.session_id == session_id).count():
            return _PEER
    return None


def _await_peer(session_id: str, timeout: float | None = None) -> dict | None:
    timeout = config.VIEW_PEER_WAIT if timeout is None else timeout
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as s:
            row = s.query(MitmView).filter(MitmView.session_id == session_id).one_or_none()
            if row is None:
                return None  # 不存在 → 純粹是 port 撞號
            snapshot = (
                _relay_dict(row.id, row.session_id, row.port, row.pid)
                if row.pid is not None and _process_alive(row.pid)
                else None
            )
        if snapshot and views._port_open(snapshot["port"]):
            return snapshot
        time.sleep(0.2)
    return None


def _drop(row_id: int) -> None:
    with suppress(Exception), session_scope(immediate=True) as s:
        row = s.get(MitmView, row_id)
        if row is not None:
            s.delete(row)


def _alive_relay(session_id: str) -> dict | None:
    for r in list_mitm_views(session_id):
        if views._port_open(r["port"]):
            return r
    return None


# --- socat 程序 -------------------------------------------------------------------


def _socat_argv(port: int, container_id: str) -> list[str]:
    """relay 的命令列。

    ⚠ **每一個參數都不含空白，這是刻意的。** socat 的 `EXEC:` 依空白切詞、沒有引號機制
      （`SYSTEM:` 更糟：socat 會先自己解一次引號再交給 sh，實測會把內層的引號吃掉）。
      所以要執行的東西收進 `mitm_bridge.sh`，這裡只留「路徑 + 三個沒有空白的參數」。

    ⚠ `bind` 用 **`config.TTYD_BIND`**，不是 127.0.0.1。nginx 是**另一個容器**，它走
      `control:<port>` 連過來，綁 control 容器的 loopback 的話那條路根本到不了
      （容器化時 TTYD_BIND 是 0.0.0.0，只在內部網路上；非容器化時是 loopback，那時
      nginx 也在同一台）。就緒探測則一律從 127.0.0.1 探（見 views._probe_host）。

    ⚠ `reuseaddr`：relay 收掉後那個 port 會在 TIME_WAIT 停留，沒有它的話「關掉再開」
      會在同一個 port 上綁失敗，然後整個範圍往後挪一格，看起來像 port 洩漏。
    """
    return [
        "socat",
        f"TCP-LISTEN:{port},bind={config.TTYD_BIND},fork,reuseaddr",
        f"EXEC:{config.MITM_BRIDGE} {container_id} {config.MITM_WEB_PORT} {config.MITM_LINGER}",
    ]


def _kill(pid: int | None) -> bool:
    return views._kill(pid, _OUR_RELAY_NAMES)


def _kill_spawned(pid: int | None, grace: float = 1.0) -> bool:
    """收掉「我們剛 spawn、但可能還沒 exec 成 socat」的那個行程。

    ⚠ **這條路上不可以只呼叫 `_kill()`。** 它在送訊號之前會先確認 argv[0] 是 socat，
      而 `_spawn_detached` 是 double-fork：`$!` 拿到的 pid 一開始是 `sh` 的。在那個窗口裡
      `_kill()` 會判定「不是我們的程序」而**直接回 True 什麼都不做**，於是那顆行程活著、
      DB 那一列被 `_drop` 掉，再也沒有人記得它。症狀與 `views.open_view` 的 review H3
      是同一種：port 就此消失，而畫面上、log 裡都沒有任何跡象。

    做法：先給它 `grace` 秒把 exec 走完（走完就交給 `_kill()` 那一整套「等到它真的從行程表
    上消失才算數」）；到時間還沒變成 socat，就直接對這個號碼送訊號。

    ⚠ 直接送訊號的那一段有一個窄窗口：`sh` 剛好在這幾毫秒退出、而號碼被別人接手。
      這個 pid 是我們自己在幾百毫秒前從 `$!` 拿到的，接手的機率極低；而另一側的代價是
      **確定**漏掉一顆沒有人記得的行程。往「收得掉」那一側倒，理由同 `views._kill` 那段
      「psutil 保護的是等待判斷、不是訊號投遞」的取捨。
    """
    if not pid:
        return True
    deadline = time.time() + grace
    while time.time() < deadline:
        if not _pid_exists(pid):
            return True  # 它自己走了
        if _process_alive(pid):
            return _kill(pid)  # 已經是 socat，走完整的那一套
        time.sleep(0.05)
    if not _pid_exists(pid):
        return True
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with suppress(OSError):
            os.kill(pid, sig)
        time.sleep(0.1)
        if not _pid_exists(pid):
            return True
    return False


def _process_alive(pid: int | None) -> bool:
    return views._process_alive(pid, _OUR_RELAY_NAMES)


def _is_mitmweb_serving(port: int) -> bool:
    """確認該 port 上的服務真的是「經 relay 接到的 mitmweb」。

    ⚠ **只檢查「port 開著」不夠，而且這裡的代價比終端那邊高得多。** nginx 會對這條路徑
      注入 `Authorization: Bearer <這一場的密碼>`：port 上若是別的服務（我們的 socat 其實
      綁失敗正在退出），我們就是把那串密碼直接遞給它。所以要驗明正身才寫 pid 進 DB。

    ⚠ 判準看 `server` 標頭、狀態碼放寬到「回得出 HTTP 就算」：這道探測**不帶授權**，
      mitmweb 對它回 403（2026-08-26 實測 12.2.3，不是 401）。用「200」當判準的話永遠
      過不了。403 同時證明兩件事：服務起來了，而且它的授權層是活的。
    """
    try:
        with socket.create_connection((views._probe_host(), port), timeout=3.0) as sock:
            sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            head = sock.recv(4096)
    except OSError:
        return False
    if not head.startswith(b"HTTP/1."):
        return False
    return b"server: mitmproxy" in head.lower()


def _pid_exists(pid: int) -> bool:
    """這個號碼上還有東西嗎：**只問存在性，不問身分**。見 `_wait_ready`。"""
    try:
        os.kill(pid, 0)  # 只探測存在性，不送信號
    except (ProcessLookupError, OSError):
        return False
    return True


def _wait_ready(port: int, pid: int, timeout: float = 20.0) -> bool:
    """等 relay 就緒。它先死掉、逾時、或該 port 上是別的服務都回 False。

    ⚠ **中止條件是「那個號碼上沒東西了」，不是 `_process_alive`（它還要比對 argv[0]）。**
      `_spawn_detached` 是 double-fork：`$!` 拿到的 pid 一開始是 `sh` 的，要等它 exec 成
      socat 之後 argv[0] 才對得上。拿身分當中止條件的話，剛 spawn 的那一瞬間會被判成
      「它死了」→ 立刻換下一個 port → 把整個範圍掃完 → 報「無可用 port」。**症狀完全
      指向 port**，而真正的原因是幾毫秒的 exec 空窗（2026-08-26 用一個啟動較慢的替身
      socat 撞出來；真 binary 也有同一個窗口，只是通常搶得贏）。
      身分比對該待的地方是 `_kill()`（送 SIGTERM 之前），那裡它是必要的；這裡它只會製造
      偽陰性：「這個 port 上服務的是不是我們要的東西」由下面 `_is_mitmweb_serving` 回答，
      那是比 argv 更直接的證據。

    ⚠ 逾時比 ttyd 那邊（5 秒）寬得多：這一發探測要**整條路走完**：socat fork、
      `docker exec` 起一個行程（實測數百毫秒起跳）、python 連上容器 loopback、mitmweb 回應。
      而 mitmweb 本身也可能還在啟動（session 剛建好時）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_exists(pid):
            return False
        if views._port_open(port) and _is_mitmweb_serving(port):
            return True
        time.sleep(0.3)
    return False


def _relay_dict(row_id: int, session_id: str, port: int, pid: int | None) -> dict:
    return {
        "mitm_view_id": row_id,
        "session_id": session_id,
        "port": port,
        "pid": pid,
        # nginx 對外路徑（ADR 0021）。**尾斜線不可省**：mitmweb 的 SPA 是路徑相對的
        # （`./static/…`），少了它 `/session/<sid>/mitm` 會把資源解析到 `/session/<sid>/`。
        "path": f"/session/{session_id}/mitm/",
    }
