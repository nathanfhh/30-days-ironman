"""on-demand ttyd view（ADR 0008）。

「一次觀看」的生命週期，與 session 本體（container）分離：

  開網頁看 → 臨時起 `ttyd -q ... docker attach <container>` → 回 URL
  關掉網頁 → WS 斷 → `-q`（--exit-no-conn）讓 ttyd 自行 exit → init reap

因此**沒有任何 worker 需要偵測關頁、也不需送 kill**。ttyd 以 double-fork 起、reparent 給
init：它不屬於任何 worker，任何 worker 都能憑 DB 裡的 pid 提前收（session 被 terminate 時），
殭屍由 init 收——這正是多 worker 下唯一需要處理的那一塊（ADR 0008 背景）。

port 分配由 DB 的 `views.port` UNIQUE 仲裁（跨 worker 原子），再以 readiness 檢查擋掉
「DB 沒佔但外部程序佔住」的情形。
"""

from __future__ import annotations

import datetime as _dt
import os
import signal
import socket
import subprocess
import time
from contextlib import suppress

from sqlalchemy.exc import IntegrityError

from . import config
from .db import session_scope
from .models import View, utcnow


class ViewError(RuntimeError):
    pass


# psutil 是選用的：正式部署的 image 一定有（見 deploy/Dockerfile），但本機直接跑
# `python -m server.app` 的人不一定裝。沒有它就退回 os.kill 的存活探測——功能少一點，
# 但不會讓整個模組 import 失敗。
try:
    import psutil
except ImportError:  # pragma: no cover - 取決於環境
    psutil = None


# --- 對外 API --------------------------------------------------------------------


def open_view(
    session_id: str,
    container_name: str,
    ttyd_bin: str | None = None,
    actor_user_id: int | None = None,
) -> dict:
    """為 session 開一個 on-demand 終端 view；已有存活的就沿用（點兩次不會多起一個）。

    ⚠ `actor_user_id` 是**按下去的那個人**，不是 session 的擁有者。兩者在 admin 身上會
      分岔（他開得了別人的 session），而收終端要跟著開的人走——見 `close_user_views`。
    """
    existing = _alive_view(session_id)
    if existing is not None:
        return existing

    for port in range(config.TTYD_PORT_MIN, config.TTYD_PORT_MAX + 1):
        view_id = _claim_port(session_id, port, actor_user_id)
        if view_id is _PEER:
            # 別的 worker 已搶先為「同一個 session」建立 view（session_id UNIQUE）。
            # 不該再起第二個 ttyd，等它就緒後沿用（review H1）。
            other = _await_peer_view(session_id)
            if other is not None:
                return other
            # 等不到＝對方中途掛了。換 port 一點用都沒有（撞的是 session_id 不是 port），
            # 再掃下去只會把整個範圍空轉一遍才報一個誤導的「無可用 port」。它的宣告會在
            # VIEW_CLAIM_GRACE 後被 list_views 回收，此時直接告訴呼叫端稍後再試。
            raise ViewError("另一個 worker 正在為此 session 建立終端，請稍後再試")
        if view_id is None:
            continue  # 這個 port 被別的 session 佔走 → 換下一個
        pid = None
        # ⚠ 先收斂再用：argv[0] 與記進 DB 的必須是**同一個**字串，否則標記說的是一回事、
        #   實際跑的是另一回事——那比不標更糟。
        binary = config.ttyd_bin_or_default(ttyd_bin)
        # argv 與 spawn 時刻要留著：失敗路徑上的 `_kill_spawned` 靠它們證明「這個號碼上的
        # 還是我們 spawn 的那一顆」，見 `_still_our_spawn`。
        argv = _ttyd_argv(port, container_name, session_id, binary)
        spawn_time = time.time()
        try:
            pid = _spawn_detached(argv)
            if _wait_ready(port, pid, session_id):
                with session_scope(immediate=True) as s:
                    row = s.get(View, view_id)
                    if row is None:  # 宣告被別人清掉了（不該發生）→ 當作失敗處理
                        raise ViewError("view 宣告已消失")
                    row.pid = pid
                    row.ttyd_bin = binary
                return _view_dict(view_id, session_id, port, pid, binary)
        except Exception:
            # spawn 之後任一步失敗（含寫 pid 進 DB 失敗）都必須收掉那個 ttyd，
            # 否則它會活著卻沒人記得 pid，永遠不會被回收（review H3）。
            _kill_spawned(pid, argv, spawn_time=spawn_time)
            _drop_view(view_id)
            raise
        # 起不來（多半是 port 被 DB 以外的程序佔住）→ 收乾淨，換下一個 port
        # ⚠ **這兩處都是 `_kill_spawned` 不是 `_kill`。** 這條路走得到「ttyd 還沒 exec 完」
        #   的窗口（最明確的一種：`_wait_ready` 逾時，而那顆替身／慢啟動的 ttyd 到那一刻
        #   都還沒把自己換掉），而 `_kill()` 在那個窗口裡會判定「不是我們的程序」直接回
        #   True 什麼都不做，接著下一行就把唯一的追蹤記錄刪掉。詳見 `_kill_spawned`。
        _kill_spawned(pid, argv, spawn_time=spawn_time)
        _drop_view(view_id)
    raise ViewError(f"{config.TTYD_PORT_MIN}-{config.TTYD_PORT_MAX} 無可用 port")


def close_views(session_id: str) -> int:
    """提前收掉某 session 的所有 view（session 被 terminate、或使用者明確關閉）。

    正常情況不需呼叫——關網頁時 `-q` 會讓 ttyd 自己退出。
    """
    closed = 0
    stuck: list[int] = []
    with session_scope(immediate=True) as s:
        rows = s.query(View).filter(View.session_id == session_id).all()
        for row in rows:
            if not _kill(row.pid):
                # ⚠ **不刪這一列。** 刪掉唯一的追蹤記錄等於把一個還活著的 ttyd 變成孤兒，
                #   而它正是我們要收的那個東西。留著讓 reconciler 之後還有機會，
                #   並把失敗往上報。
                stuck.append(row.pid or 0)
                continue
            s.delete(row)
            closed += 1
    if stuck:
        raise ViewError(f"收不掉 {len(stuck)} 個終端（pid {', '.join(str(p) for p in stuck)}）")
    return closed


def close_user_views(user_id: int) -> tuple[int, int]:
    """收掉某位使用者開著的終端。回傳 `(收掉幾個, 失敗幾場)`。

    ⚠ **撤銷存取權時，收掉 cookie / token 是不夠的。** 那兩者要到下一次 HTTP 請求才會被
      gate 擋下，而已經升級完成的 ttyd WebSocket 不會再走 nginx 的 auth_request——連線
      活著的期間，對方手上就是一個可互動的 shell，撤銷對它完全沒有效果。

    ⚠ **住在這裡而不是 app.py，是為了讓不變式跟著操作走。** 它原本是 `app._cut_live_terminals`，
      於是「改密碼要接著切終端」變成每一個呼叫端要自己記得的事——而 `server/cli.py` 的
      `set-password` 就沒有記得（審查 F-003）：管理員從 CLI 讓一個被盜帳號退場，對方的分頁
      仍然是一個能打字的 shell。放進 `auth.change_password` 之後，第四個呼叫端也不會漏。
    ⚠ 因此**不可以** import sessions 或用 SessionManager：`sessions` 在模組層 import `auth`，
      而 `auth` 要呼叫這一支。直接查 `Session.id` 就夠了——列表本來就只讀 DB（ADR 0012）。

    ⚠ session 本身不動：這是「切斷存取」不是「終止工作」。容器繼續跑，重開網頁就會起一個
      新的 ttyd（ADR 0003：不重播，畫面由 TUI 自行重繪），代價幾乎是零。

    ⚠ **涵蓋範圍是「他擁有的」加「他開著的」**，兩者在 admin 身上會分岔：admin 開得了別人
      的 session，而那個 view 掛在別人的 session 上。三條都要收：

        1. session 的擁有者是他                  → 收（不管是誰開的）
        2. view 的 `actor_user_id` 是他          → 收（他開在別人 session 上的那些）
        3. **他是 admin → 全部都收**

      第 3 條有兩個理由，缺一條就會漏收：
        · `actor_user_id` **只記得建立那一列的人**。`views.session_id` 是 UNIQUE、一場
          只有一個 view，而 `open_view` 對已經活著的 view 是直接沿用、不改 actor。
          於是「擁有者先開了自己的終端、admin 之後去看同一場」這個很常見的順序底下，
          那一列的 actor 是擁有者——只比對 actor 的話，收不到 admin 正在看的那個畫面。
        · 這個欄位是後來才加的，之前的舊列一律是 NULL（不知道是誰開的）。
      而 admin 本來就開得了任何一場，所以「他可能正在看哪些」的誠實答案就是「全部」。
      收錯的代價
      幾乎是零（ADR 0003 不重播，重開網頁就長一個新的），漏收的代價是一個剛被收掉存取權
      的人手上還有一個能打字的 shell。往保守那一側倒。

    ⚠ **回傳 `(收掉幾個, 失敗幾場)`。** 以前這裡把每一場的例外都吞掉、只回一個數字，
      呼叫端無從分辨「三場都收掉了」與「三場都沒收掉」。切存取權的動作不可以安靜地失敗，
      所以失敗要數出來，讓上面決定怎麼講。
    """
    from .models import Session as SessionRow  # 區域 import：避免與 auth 的載入順序打架
    from .models import User as UserRow

    with session_scope() as s:
        is_admin = bool(getattr(s.get(UserRow, user_id), "is_admin", False))
        targets = {r.id for r in s.query(SessionRow.id).filter(SessionRow.user_id == user_id).all()}
        targets |= {r.session_id for r in s.query(View.session_id).filter(View.actor_user_id == user_id).all()}
        if is_admin:
            # 見上：actor 只記得建立者，admin 沿用別人開的 view 時不會留下痕跡。
            targets |= {r.session_id for r in s.query(View.session_id).all()}
    closed = failed = 0
    for sid in targets:
        try:
            closed += close_views(sid)
        except Exception:  # noqa: BLE001 — 一場收不掉不可以讓其餘的不收
            failed += 1
    return closed, failed


def list_views(session_id: str) -> list[dict]:
    """列出仍存活的 view；順手清掉已自行退出的殘留記錄（釋放 port）。

    ⚠ pid 尚未寫入（NULL）的列代表**另一個 worker 剛搶到 port、ttyd 還沒起來**的 in-flight
    宣告，不可當成死的刪掉——否則兩個 worker 會同時用到同一個 port（跨 worker race，
    2026-07-25 由階段 3 測試抓到）。只有超過寬限期仍無 pid（宣告者中途掛了）才回收。

    ⚠ 「存活」在這裡**只看 pid**，不探 port。這是每個被代理的請求都會走到的路徑
    （nginx 的 auth_request → `/api/auth/view`），多一次 TCP connect 是每張 asset、
    每次 WS upgrade 都要付的成本。要連「連得上」一起確認請用 `_alive_view()`——它在這
    之上多探一次 port，只用在 `open_view()` 那條低頻路徑。
    兩者的差別只在「程序還在、卻已經不服務」時看得出來（ttyd 走 `-q` 收尾的那一瞬間）。
    實測打不到：斷開 WS 之後 0/5/20/60ms 各重開一次，四次都拿到新的 pid、首頁都回 200
    （2026-07-26）。曾為此在這裡加過一道 port 探測，量完發現它擋掉的是 0 次，撤除。
    真的退化時的症狀是 iframe 顯示 502——看得見，不需要事先偵測。
    """
    out, dead = [], []
    cutoff = utcnow() - _dt.timedelta(seconds=config.VIEW_CLAIM_GRACE)
    with session_scope() as s:
        for row in s.query(View).filter(View.session_id == session_id).all():
            if row.pid is None:
                if row.created_at < cutoff:  # 逾期未就緒＝宣告者已死，可回收
                    dead.append(row.id)
                continue  # 寬限期內：尊重別的 worker 的 in-flight 宣告
            if _process_alive(row.pid):
                out.append(_view_dict(row.id, row.session_id, row.port, row.pid, row.ttyd_bin))
            else:
                dead.append(row.id)
    for vid in dead:
        _drop_view(vid)
    return out


# --- port 分配（DB UNIQUE 為跨 worker 仲裁）-------------------------------------

_PEER = object()  # _claim_port 的哨兵：撞的是 session_id，不是 port


def _claim_port(session_id: str, port: int, actor_user_id: int | None = None):
    """試著佔一個 port。

    回傳 view_id（搶到）／`None`（這個 port 被別的 session 佔走，換下一個）／
    `_PEER`（別的 worker 已為同一 session 建了 view——換 port 沒有意義）。

    views 表上有兩個 UNIQUE（port、session_id），光看 IntegrityError 分不出撞了哪一個；
    不分清楚就會把「同一 session 已有 view」誤當成「port 撞號」而掃完整個 port 範圍。
    """
    try:
        with session_scope() as s:
            row = View(session_id=session_id, port=port, actor_user_id=actor_user_id)
            s.add(row)
            s.flush()
            return row.id
    except IntegrityError:
        pass
    with session_scope() as s:
        if s.query(View).filter(View.session_id == session_id).count():
            return _PEER
    return None


def _await_peer_view(session_id: str, timeout: float | None = None) -> dict | None:
    """別的 worker 正在為同一 session 建 view 時，等它就緒並沿用。

    回傳 None 代表「不是這個情況」（多半只是 port 撞號），呼叫端應繼續試下一個 port。

    逾時值放在 `config.VIEW_PEER_WAIT`——它與 `VIEW_CLAIM_GRACE` 是同一個窗口的兩端
    （一個等對方就緒、一個判定對方已死），寫死在這裡的話調整時很容易只動一邊。
    """
    timeout = config.VIEW_PEER_WAIT if timeout is None else timeout
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as s:
            row = s.query(View).filter(View.session_id == session_id).one_or_none()
            if row is None:
                return None  # 不存在 → 純粹是 port 撞號
            ready = row.pid is not None and _process_alive(row.pid)
            # ⚠ `row.ttyd_bin` 一定要傳：漏了的話 ttyd_flavor 變 None，抽屜的 C/Rust
            #   標籤在**跨 worker 沿用**這條路上靜靜消失，而 _view_dict 的註解正是在
            #   保證「回的是當初起它的那一顆」（審查 F-025）。值一定拿得到——這裡只在
            #   row.pid 有值時才回，而 open_view 在同一筆交易裡寫 pid 與 ttyd_bin。
            snapshot = _view_dict(row.id, row.session_id, row.port, row.pid, row.ttyd_bin) if ready else None
        if snapshot and _port_open(snapshot["port"]):
            return snapshot
        time.sleep(0.2)
    return None


def _drop_view(view_id: int) -> None:
    with suppress(Exception), session_scope(immediate=True) as s:
        row = s.get(View, view_id)
        if row is not None:
            s.delete(row)


def _alive_view(session_id: str) -> dict | None:
    for v in list_views(session_id):
        if _port_open(v["port"]):
            return v
    return None


# --- ttyd 進程 --------------------------------------------------------------------


def _c_extras(session_id: str) -> list[str]:
    """C 版特有參數：**沒有**。共用模板（見 _ttyd_argv）就是 C 版的全部能力——
    它是基準，Rust 版是在它之上加東西。這個函式存在是為了讓 _TTYD_EXTRAS 對
    TTYD_BINS 的每一顆都有明確條目，「沒有」是一個寫出來的決定，不是漏寫。"""
    return []


def _rust_extras(session_id: str) -> list[str]:
    """ttyd-rust 特有參數。⚠ 拆 strategy 的真正理由（真 binary 實測 2026-08-07）：
    C 版（1.7.7）對這些它沒有的旗標**不是拒起，而是靜默忽略照樣啟動**——所以塞進
    共用模板不會炸、不會有任何錯誤，只會得到一個**沒有標題遮蔽、沒有第二層授權**的
    終端。靜默的安全降級比 crash 難發現得多（正是 Day 26 在講的「宣稱大於機制」）。
    所以這些旗標只能給認得它們的 binary，且要有測試釘住 C 版拿不到。

    --title：伺服器端換掉宣告給 client 的標題——命令列一個字都不上線，標題只剩
      固定字樣加這一場的編號。C 版那個洞仍然存在、只是被共用模板裡的 titleFixed
      蓋住畫面——這是兩顆 binary 的實質差異之一，選 binary 的人應該知道自己在
      選什麼（設定畫面已寫明；README 由文件階段補）。
    --auth-url：ttyd 自己在放行前多問控制平面一次（第二層，與 nginx auth_request
      是縱深不是重複）。打的是**無副作用**的 /api/auth/check——不是 /api/auth/view，
      那支沒有存活 view 時會當場開一個，不能拿來當每個 asset 都打的驗證端點。
      sid 在 spawn 時就烤進 URL：一顆 ttyd 本來就只屬於一場。
    --auth-cache-ttl：每個 asset 與 WS 升級都是一次子請求，TTL 把它壓成每幾秒
      一次（取捨見 config.TTYD_AUTH_CACHE_TTL）。0＝不帶＝每請求都問。
    """
    extras = [
        "--title",
        f"claude-pty · {session_id}",
        # ttyd 與控制平面在同一個容器（views 由 control 自己 spawn），loopback 即達；
        # 非容器化執行時 Flask 同樣聽 CONTROL_PORT。
        "--auth-url",
        f"http://127.0.0.1:{config.CONTROL_PORT}/api/auth/check?session={session_id}",
    ]
    if config.TTYD_AUTH_CACHE_TTL > 0:
        extras += ["--auth-cache-ttl", str(config.TTYD_AUTH_CACHE_TTL)]
    return extras


# 每顆 binary 一組參數建構策略；共用部分留在 _ttyd_argv 的模板裡。
# ⚠ 鍵集合必須恆等於 config.TTYD_BINS（測試釘著）：白名單多一顆 binary，這裡就要
#   同步寫下它的策略——即使是「沒有特有參數」也要寫（見 _c_extras 的理由）。
_TTYD_EXTRAS = {"ttyd": _c_extras, "ttyd-rust": _rust_extras}


def _ttyd_argv(port: int, container_name: str, session_id: str, ttyd_bin: str | None = None) -> list[str]:
    # C 版或 Rust 版，由**開這個終端的人**的偏好決定（users.ttyd_bin，管理畫面的
    # 「設定」可切）。一律經 ttyd_bin_or_default() 收斂：這個值會變成 argv[0]，
    # 不認得的字串（白名單改過、DB 留著舊值）必須退回預設，不可以直接拿去 exec。
    binary = config.ttyd_bin_or_default(ttyd_bin)
    return [
        binary,
        *_TTYD_EXTRAS[binary](session_id),
        "-p",
        str(port),
        "-i",
        config.TTYD_BIND,  # 非容器化＝loopback；容器化＝0.0.0.0（僅內部網路，ADR 0009）
        "-b",
        f"/session/{session_id}",  # base-path，配合 nginx 子路徑路由
        "-W",  # 可寫（互動需要）
        "-q",  # 全部 client 斷線即自行退出＝關網頁自動回收（ADR 0008）
        # ttyd 預設把「完整命令 + 容器 hostname」當網頁標題。
        #
        # ⚠ **`titleFixed` 沒有解決那件事，它只是把畫面蓋掉。** 這是 client 選項：
        #   真正的標題（`docker attach --detach-keys=… claude-pty-<sid>` 加上容器
        #   hostname）**在那之前就已經送給每一個連上的 client 了**，瀏覽器只是被要求
        #   顯示別的字。所以這一行買到的是「分頁標題、瀏覽紀錄、截圖裡看不到」，
        #   買不到「沒有送出去」。
        #
        # ⚠ 真正的修法是**伺服器端**的 `--title`（Rust 版已接上，見 _rust_extras）。
        #   這一行仍然無條件留著：C 版只有它可靠（遮畫面聊勝於無）；Rust 版帶著也
        #   無妨——兩個值是同一個字串，client 端不會蓋出不一樣的東西。
        "-t",
        f"titleFixed=claude-pty · {session_id}",
        # 讓使用者選得到文字。Claude Code 的 TUI 會開啟滑鼠追蹤（實測 ?1000/?1002/?1003/
        # ?1006 全開），一開啟，拖曳就被當成應用程式的滑鼠事件送進 TUI，終端不再拿它來
        # 選取——畫面上的文字變成完全無法複製。這兩個選項讓修飾鍵可以繞過追蹤：
        #   macOS：按住 Option 拖曳
        #   其他：按住 Alt 拖曳（xterm.js 的 altClickMovesCursor 關掉才不會誤觸發移游標）
        "-t",
        "macOptionClickForcesSelection=true",
        "-t",
        "altClickMovesCursor=false",
        # 選取後自動複製到剪貼簿，省掉「選了還要再按 Cmd+C」這一步（選取本身已經很費事）
        "-t",
        "copyOnSelect=true",
        "docker",
        "attach",
        f"--detach-keys={config.DETACH_KEYS}",  # ADR 0002 的 Ctrl+P 陷阱
        container_name,
    ]


def _spawn_detached(argv: list[str]) -> int:
    """起一個不屬於任何 worker 的 ttyd，回傳它的 pid。

    以 `sh -c '"$@" & echo $!'` 達成 double-fork：sh 把 ttyd 放到背景後立刻結束，
    ttyd 因而被 reparent 給 PID 1（容器內請用 init:true 讓 tini 負責 reap）。
    任何 worker 都能憑 DB 裡的 pid 收它。

    ⚠ 刻意**不用 os.fork()**：Flask 是多執行緒的，而 fork 只複製呼叫緒——若別的緒此刻
    持有 libc / allocator / logging / SSL 的鎖，子程序在 exec 前就可能死結，父程序卡在
    waitpid（Python 官方明確警告 fork 與執行緒混用，review H7）。subprocess 走的是
    C 層的 fork+exec（_posixsubprocess），中間不執行 Python 邏輯，沒有這個風險。
    """
    pid_line = subprocess.run(
        # $0 佔位給 "sh"，$@ 才會是真正的 argv；ttyd 的 stdio 導向 /dev/null，
        # 否則它會繼承下面這條 PIPE，寫爆時會卡住。
        ["/bin/sh", "-c", '"$@" </dev/null >/dev/null 2>&1 & echo "$!"', "sh", *argv],
        capture_output=True,
        text=True,
        timeout=10,
        start_new_session=True,  # 脫離控制終端與 process group
        check=False,  # sh 的退出碼不重要，下面直接驗 pid 輸出
    ).stdout.strip()
    if not pid_line.isdigit():
        raise ViewError("ttyd 啟動失敗（無法取得 pid）")
    return int(pid_line)


def _spawn_detached_with_group(argv: list[str]) -> tuple[int, int | None]:
    """同 `_spawn_detached`，另把 process group id 一起帶回來。

    ⚠ 為什麼 group id 是這樣拿的：double-fork 的那顆 `sh` 自己帶 `start_new_session=True`
      ——它是新 session／group 的 leader（pgid ＝ 它的 pid），隨即退出；背景 child 沒有
      job control，**繼承 sh 的 pgid**，sh 死後被 reparent 給 init 但 group 不變。
      所以從外面問 child 的 pgid，拿到的正是「這次 spawn 全部成員」的 group id——
      之後 `killpg` 一發就涵蓋 listener、它 fork 的 children、與 bridge script 起的
      `docker exec` client（沒有人 setsid 的話，整棵子孫樹都在同一個 group）。

    ⚠ pgid 問不到（程序剛好退出、號碼被回收）就回 `None`；呼叫端把 None 存進 DB，
      清理時降級回單 pid 路徑——行為與沒有這個欄位的舊版相同，不會更糟。
    """
    pid = _spawn_detached(argv)
    try:
        return pid, os.getpgid(pid)
    except OSError:  # 含 ProcessLookupError：那個號碼已經不在了
        return pid, None


# 我們自己會起的 ttyd 檔名（＝白名單本身）。**每個名字都收**，不是只收「這個人現在選的
# 那一顆」：偏好是 per-user 的，而且切換之後先前起的程序還活著——只認一個名字的話，那些
# 程序不會被認成我們的，於是 `_kill()` 收不掉、`_process_alive()` 判它們死掉而清掉登錄列。
# ⚠ 而**沒有任何機制會發現它們**：`_clean_views` 只走 views 列、`_remove_orphans` 只管
#   container，沒有人依 port 或 process 掃描。那個 port 就此永久洩漏，畫面上看不出來、
#   log 也不會說——這條白名單是唯一的把關。
_OUR_TTYD_NAMES = frozenset(config.TTYD_BINS)


def _is_ours(pid: int | None, names: frozenset[str]) -> bool:
    """確認該 pid 真的是我們起的那種程序（`names` ＝ 允許的 argv[0] basename）。

    PID 會被回收再利用：ttyd 退出後、殘留記錄清掉前，同一個號碼可能已是別的程序。
    只憑「pid 存在」就送 SIGTERM 會誤殺無關程序（review H8）。故先比對 cmdline。

    ⚠ 沒有 psutil 時必須回 True 而非 False——「無從佐證」被當成「不是我們的程序」的話，
    所有 view 會立刻被判死亡並清掉，那比誤殺嚴重得多。

    ⚠ 比對的對象是 **`_OUR_TTYD_NAMES`**（見上），不是寫死的字串 "ttyd"。image 內同時放著
      C 版（`ttyd`）與 Rust 版（`ttyd-rust`），切過去之後若這裡還在比 "ttyd"，每一個 view
      都會被判成「不是我們的程序」→ 全部被清掉。寫死那個字串是這個函式最容易犯的錯，
      而且症狀（終端一開就消失）看起來完全不像身分比對的問題。

    ⚠ 比對的是 **argv[0] 的 basename**，不是「cmdline 裡有沒有 ttyd 這五個字」。
      後者太寬鬆：任何指令列提到 ttyd 的程序都會被認成我們的 ttyd，而這個函式正是
      `_kill()` 送 SIGTERM 前的唯一把關——PID 被回收後接手的若剛好是這種程序就會被誤殺。
      2026-07-26 實測踩到：一行掃描用的 `sh -c '... grep ttyd ...'` 自己就通過了比對。

    改用 psutil 而非自己讀 /proc：後者要手刻 `/proc/<pid>/cmdline` 的 NUL 切分，
    而且 macOS 沒有 /proc（本機跑測試時只能一律回 True）。psutil 兩個平台都問得到。
    """
    if not pid:
        return False
    if psutil is None:
        return True  # 沒裝 psutil：無從佐證，沿用「僅憑 pid 存在」的舊行為
    try:
        argv = psutil.Process(pid).cmdline()
    except psutil.NoSuchProcess:
        return False  # 程序已不存在
    except (psutil.AccessDenied, OSError):
        return True  # 問不到（權限等）：無法佐證，不因此誤判為死
    return bool(argv) and os.path.basename(argv[0]) in names


def _is_our_ttyd(pid: int | None) -> bool:
    """`_is_ours` 綁在 ttyd 白名單上的那一版（這個模組唯一會起的東西）。

    ⚠ 參數化出去的是**名字集合**，不是「要不要檢查」：`mitm_views` 起的是 socat，
      它同樣需要這一整套把關（PID 回收、psutil 缺席時的降級），只是白名單不同。
      複製一份的話，兩份會各自漂：這裡修過的三個坑（basename 而非子字串、
      問不到時回 True、沒有 psutil 時回 True）在另一份不會自動成立。
    """
    return _is_ours(pid, _OUR_TTYD_NAMES)


def _gone(pid: int, proc: "psutil.Process | None") -> bool:
    """這個 pid 上「原本那個程序」是不是已經不在了。

    ⚠ 問的是**原本那個**，不是「這個號碼上現在有沒有東西」。PID 會被回收，等待期間
      剛好有人接手同一個號碼的話，只看存在性會永遠等下去（或更糟：等到逾時，然後把
      SIGKILL 送給一個無關的程序）。psutil 的 `Process` 記著 create_time，`is_running()`
      比對得出「號碼還在但已經換人」，這是這裡用它而不用 `os.kill(pid, 0)` 的唯一理由。

    沒有 psutil 時退回存在性探測。它認不出號碼被回收，但**仍然比舊行為誠實**：
    舊行為是連問都不問就回報收掉了。
    """
    if proc is not None:
        try:
            if not proc.is_running():
                return True
            # zombie＝已經死了、只是還沒被 init reap。對「它還會不會服務」而言就是不在了。
            return proc.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True
        except (psutil.AccessDenied, OSError):
            return False  # 問不到＝不能宣告它走了
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _await_gone(pid: int, proc: "psutil.Process | None", timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if _gone(pid, proc):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _kill(pid: int | None, names: frozenset[str] | None = None) -> bool:
    """收掉這顆 ttyd（`names` 換成別的白名單就能收別種，見 mitm_views）。
    **回傳「這個 pid 上原本那個程序已經不在了」**。

    ⚠ 以前這裡整段吞掉例外、也不回報。於是 `close_views` 對一個 `PermissionError` 的
      ttyd 照樣刪掉 DB 那一列並計入「已收」——程序還活著、記錄沒了、之後再也沒有人會
      去收它。切存取權的動作不可以安靜地失敗，所以失敗要說得出來。

    ⚠ **而只送 SIGTERM 就回報成功，是同一個錯換一個位置。**「訊號送出去了」跟「它停了」
      是兩件事：ttyd 忽略 SIGTERM、卡在不可中斷的 I/O、或正被 SIGSTOP 停住的時候，
      `os.kill` 一樣不拋例外。舊版於是回 True，`close_views` 刪掉唯一那一列，而那個
      WebSocket 還連著——**收存取權的動作看起來成功、實際上沒有**，比失敗更糟，因為
      沒有人會再去看它。所以這裡要等到它真的從行程表上消失才算數：

        SIGTERM → 等 VIEW_TERM_GRACE → 還在就 SIGKILL → 等 VIEW_KILL_GRACE → 還在就回 False

    ⚠ 這條路徑跑在「按下改密碼」的同步請求裡，所以兩個等待值都必須短（預設 3＋2 秒）。
      它們是**上限不是延遲**：ttyd 正常收到 SIGTERM 就走，等待迴圈第一輪就結束。

    ⚠ **psutil 的身分綁定保護的是「等待判斷」，不是「訊號投遞」。** 這兩件事要分開講，
      因為把它講成同一件會變成新的過滿宣稱：
        · 保護到的：送完訊號之後每一次「它還在嗎」，問的都是同一個程序。號碼被回收時
          `is_running()` 會回 False 而不是讓我們空等到逾時、然後把 SIGKILL 送給接手的人。
        · **沒有保護到的**：`_is_our_ttyd()` 通過之後、`os.kill()` 送出去之前，那個程序
          仍有可能已經退出而號碼被別人接手。`psutil.Process` 不會讓 `os.kill` 變成原子的。
          窗口是兩行 Python，而且要撞上必須「剛好在這幾微秒退出」加「號碼剛好被重用」，
          但它確實存在，代價是誤殺容器裡的另一個程序。
      要真的關掉它，Linux 上的做法是 `os.pidfd_open()` 拿到一個綁死那個程序的 handle、
      重驗身分之後才 `signal.pidfd_send_signal()`。**這裡刻意沒有做**：pidfd 只有 Linux 有，
      而開發與測試都在 macOS 上跑，等於在收終端這條路上放一段本機永遠不會被執行到的分支。
      一段沒被跑過的殺程序邏輯，比一個寫下來的窄競態危險。要做的話，前提是測試也搬到 Linux。
    """
    if not pid:
        return True  # 沒有 pid＝沒有東西要收
    if not _is_ours(pid, names or _OUR_TTYD_NAMES):
        return True  # 不是我們的程序（已退出、號碼被回收）＝沒事
    # 先綁住身分再送訊號：之後每一次「它還在嗎」問的都是這一個程序，不是這個號碼。
    # （這一步保護的是**等待**，不是訊號投遞本身，見上面那段。）
    proc = None
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return True
        except (psutil.AccessDenied, OSError):
            proc = None  # 問不到身分，退回存在性探測（見 _gone）
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True  # 它剛好自己退了，結果一樣
    except (PermissionError, OSError):
        return False  # 送不出去：它可能還活著，不可以當成收掉了
    if _await_gone(pid, proc, config.VIEW_TERM_GRACE):
        return True
    # 沒走。升級——SIGKILL 是核心直接處理的，程序沒有機會忽略它。
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return _await_gone(pid, proc, config.VIEW_KILL_GRACE)


def _still_our_spawn(
    pid: int,
    names: frozenset[str],
    argv: list[str] | None,
    spawn_time: float | None,
) -> bool:
    """這個號碼上的，還是我們 spawn 的那一顆嗎？

    只有 `_kill_spawned` 的**最後那一段**會問：那一段要對一個「名字還認不出來」的號碼
    送訊號，而 `_kill()` 那道 argv[0] 白名單在那裡剛好不成立（它還沒 exec 完），所以
    白名單以外得另外拿一份證據出來。

    ⚠ **為什麼這道證據是必要的（而不是防禦性程式設計）。** `_wait_ready` 的中止條件改成
      存在性之後，「socat／ttyd bind 失敗、立刻退出」這條路不再當場中止，而是**等滿整個
      逾時**（ttyd 5 秒、relay 20 秒）才落到這裡。那段時間足夠讓那個號碼被別的程序接手，
      而接手的那顆與我們毫無關係。修法前的行為是對它直接 SIGTERM／SIGKILL。

    三種證據，任一成立就算是我們的：

      1. **cmdline 的尾段就是我們的 argv。** `_spawn_detached` 是
         `/bin/sh -c '"$@" …' sh <argv>`，`$!` 拿到的那顆在 exec 之前逐字繼承這一串，
         所以 `cmdline[-(len(argv)-1):] == argv[1:]`。
         ⚠ 比的是 **`argv[1:]`**，不是整串 argv：exec 之後 argv[0] 會變成解析過的路徑
           （`ttyd` → `/usr/local/bin/ttyd`；替身腳本更是變成 `/bin/bash <路徑>`），
           整串比對在**那個形狀上永遠不成立**，而那正好是最常落到這裡的形狀。
           `argv[1:]` 帶著 port、session id 與容器名，撞名的機率可以忽略。
      2. **argv[0] 的 basename 在白名單裡**——它在我們讀 cmdline 的這一瞬間 exec 完了。
      3. **`create_time()` ≥ spawn 的時刻**，且**只在 cmdline 問不到的時候**才輪到它。
         ⚠ 這條很弱，弱到不可以拿它當主要判準：號碼被接手時，接手的那顆也一定是在我們
           spawn 之後才建立的，所以它對「接手」這件事幾乎一律成立。它排除得掉的只有
           「這個號碼上是一顆比我們還老的程序」（＝`$!` 給的數字根本不對）。放在最後
           是因為 cmdline 問不到時（AccessDenied，多半正是別人的程序）沒有更好的東西。

    問不到就往「是我們的」倒（沒有 psutil、呼叫端沒給 argv、psutil 拒答）：那是修法前的
    行為，這道證據要收的是**問得到而且答案是否定的**那一種，不是把不確定也一起收掉。
    """
    if psutil is None or argv is None:
        return True  # 無從佐證：沿用舊行為（見上）
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return True
    try:
        cmdline = proc.cmdline()
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        cmdline = []
    if cmdline:
        if len(argv) >= 2 and cmdline[-(len(argv) - 1) :] == list(argv[1:]):
            return True  # 還沒 exec 的那顆（或 exec 完但 argv[0] 換了形狀）
        return os.path.basename(cmdline[0]) in names  # 剛剛 exec 完
    if spawn_time is None:
        return True
    try:
        return proc.create_time() >= spawn_time
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return True


def _kill_spawned(
    pid: int | None,
    argv: list[str] | None = None,
    names: frozenset[str] | None = None,
    grace: float = 1.0,
    spawn_time: float | None = None,
) -> bool:
    """收掉「我們剛 spawn、但可能還沒 exec 成目標程序」的那個行程。

    `argv` 是**交給 `_spawn_detached` 的那一串**，`spawn_time` 是呼叫它之前的時刻；
    兩者都只餵給 `_still_our_spawn`（見那支的三種證據）。

    ⚠ **spawn 之後的失敗路徑上不可以只呼叫 `_kill()`。** 它在送訊號之前會先確認 argv[0]
      在白名單裡，而 `_spawn_detached` 是 double-fork：`$!` 拿到的 pid 一開始是 `sh` 的。
      在那個窗口裡 `_kill()` 會判定「不是我們的程序」而**直接回 True 什麼都不做**，
      接著呼叫端 `_drop_view()` 刪掉唯一的追蹤記錄，那顆行程隨後 exec 起來、活著、
      而再也沒有人記得它（review H3 的形狀）。

      **而沒有任何機制會發現它**：`_clean_views` 只走 DB 列、`_remove_orphans` 只管
      container。它唯一會現身的地方是 `inspect_ttyd` 的孤兒清單，也就是有人剛好去看
      那一頁的時候。那個 port 就此消失，畫面上與 log 裡都沒有跡象。

    做法：先給它 `grace` 秒把 exec 走完（走完就交給 `_kill()` 那一整套「等到它真的從
    行程表上消失才算數」）；到時間還沒變成我們認得的名字，就**先驗明正身再**對這個號碼
    送訊號。

    ⚠ **那道驗明正身是後來補的，因為窗口變寬了。** 這一段原本的理由是「pid 是我們幾百
      毫秒前從 `$!` 拿到的，被接手的機率極低」。`_wait_ready` 的中止條件改成存在性之後
      那句話不再成立：socat／ttyd bind 失敗、立刻退出時，這條路會**等滿整個逾時**
      （ttyd `TTYD_READY_TIMEOUT` 5 秒、relay `MITM_READY_TIMEOUT` 20 秒）才走到這裡，
      那個號碼早就可能是別人的了。所以送訊號前多問一句 `_still_our_spawn`；問不到的
      那幾種情形仍然往「收得掉」那一側倒（理由同 `_kill` 那段「psutil 保護的是等待判斷、
      不是訊號投遞」），但**問得到而且答案是否定**的時候，記一行 log 放過它。

    ⚠ 放過它的代價要說清楚：那顆若其實是我們的（證據判錯），它會變成沒有人記得的孤兒。
      這個方向是刻意的——誤殺別人的程序沒有上限，漏掉一顆孤兒有 log 可查。

    ⚠ 本體住在這裡、`mitm_views._kill_spawned` 只換白名單來委派：那一支是 2026-08-26 為
      socat 寫的，而 ttyd 這條路的 `open_view` 有一模一樣的失敗路徑卻只呼叫 `_kill()`。
      兩邊共用同一份，才不會下次只修好一邊。
    """
    if not pid:
        return True
    names = names or _OUR_TTYD_NAMES
    deadline = time.time() + grace
    while time.time() < deadline:
        if not _pid_exists(pid):
            return True  # 它自己走了
        if _process_alive(pid, names):
            return _kill(pid, names)  # 已經 exec 完了，走完整的那一套
        time.sleep(0.05)
    if not _pid_exists(pid):
        return True
    if not _still_our_spawn(pid, names, argv, spawn_time):
        # 放過它。這一行是這種情形唯一的痕跡，所以要把判斷得出來的東西都寫進去。
        print(
            f"[claude-pty] ⚠ pid {pid} 上的程序已經不是我們 spawn 的那一顆（號碼被接手），"
            f"不送訊號；原本要收的是：{' '.join(argv or [])}",
            flush=True,
        )
        return True
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with suppress(OSError):
            os.kill(pid, sig)
        time.sleep(0.1)
        if not _pid_exists(pid):
            return True
    return False


def _pid_exists(pid: int | None) -> bool:
    """這個號碼上還有東西嗎：**只問存在性，不問身分**。

    與 `_process_alive` 的差別只有那一道身分比對，而那道比對在兩種場合下是相反的東西：
      · 送訊號之前（`_kill`）：**必要**。PID 會被回收，認錯就是誤殺無關程序。
      · 剛 spawn 完、等它就緒時（`_wait_ready`）：**只會製造偽陰性**。`_spawn_detached`
        是 double-fork，`$!` 拿到的 pid 在 exec 完成之前 argv[0] 還是 `sh`。

    ⚠ 這一支住在 views 而不是 mitm_views，是因為 mitm_views 的每一支同類函式都直接用
      views 的（`_spawn_detached`／`_kill`／`_port_open`／`_probe_host`）：那幾支各自都
      修過幾個很難查的坑，複製一份等於接手維護第二份會漂的副本。這一支原本只長在
      mitm_views 裡，而 ttyd 這條路要的是**同一個**東西，不是它的副本。
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)  # 只探測存在性，不送信號
    except (ProcessLookupError, OSError):
        return False
    return True


def _process_alive(pid: int | None, names: frozenset[str] | None = None) -> bool:
    if not _pid_exists(pid):
        return False
    # 存在還不夠：得確認是我們的程序而非被回收的號碼（review H8）
    return _is_ours(pid, names or _OUR_TTYD_NAMES)


def _process_group_id(pid: int | None) -> int | None:
    """這個 pid 現在的 pgid。拿不到（已死、無 psutil）就 None——呼叫端降級回單 pid 收法。"""
    if not pid:
        return None
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _group_ours_members(pgid: int, names: frozenset[str], bridge_hint: str) -> list:
    """這個 group 裡現在還活著的、我們認得的成員。psutil 不在就回空（＝無從佐證）。

    成員認得的兩種證據（任一成立就算）：
      · argv[0] 的 basename 在白名單裡——socat 的 fork children 都是這個形狀。
      · cmdline 裡含有 bridge script 的路徑——bridge script 的 sh 包裝與它 fork 出去的
        `docker exec` client 認不得白名單（前者 argv[0] 是 sh，後者是 docker），但它們
        的位置參數裡帶著我們組的腳本路徑，那是比名字更直接的出身證據
        （路徑不含空白，整段比對是安全的）。
    ⚠ 只認得 socat、不認得 docker exec client 的話有個死角：socat 全死、只剩 client 的
      group 會被判「不是我們的」而放過。那是刻意往安全側倒（同 `_still_our_spawn` 的
      取捨：誤殺沒有上限，漏掉的有下輪 cleanup），而且 socat 死了 client 的管線跟著斷，
      實務上它自己就會走。
    """
    if psutil is None:
        return []
    out = []
    # ⚠ pgid 不能放進 process_iter 的 attrs，psutil 也沒有 pgid() 方法（psutil 的
    #   Process 不暴露 process group）——用 os.getpgid 問，它在兩個平台都有。
    #   慢一點，但這條路徑只在收尾時跑，行程表也不過幾百顆。
    for proc in psutil.process_iter(["pid", "cmdline"]):
        with suppress(psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            if os.getpgid(proc.info["pid"]) != pgid:
                continue
            argv = proc.info.get("cmdline") or []
            # ⚠ 三種形狀都要認得，缺一個就會把「listener 已死、children 還握著 WS」
            #   那個**正是本機制存在理由**的場面誤判成「我們的都不在了」：
            #     1. listener 本體：argv[0] 就是 socat／ttyd。
            #     2. fork 出來、還沒 exec 的 bridge：argv[0] 是 mitm_bridge.sh 的路徑
            #        （比子字串而不是整串相等：它也可能出現在 socat 的 `EXEC:…` 那一格）。
            #     3. bridge `exec` 之後：argv 變成 `docker exec -i <cid> socat …`，
            #        argv[0] 是 docker、bridge 路徑也不見了，**只剩下某一格是 socat**。
            #   範圍已經被 pgid 限死，所以「任一格的 basename 在白名單裡」不會誤傷別人。
            #   （Copilot 在 PR #4 指出第 3 種收不到；它建議的子字串版只補得到第 2 種。）
            hit = any(os.path.basename(a) in names for a in argv) or any(bridge_hint in a for a in argv)
            if hit:
                out.append(proc)
    return out


def _kill_process_group(
    pid: int | None,
    process_group_id: int | None,
    names: frozenset[str] | None = None,
    bridge_hint: str | None = None,
) -> bool:
    """以**整個 process group**收掉一顆 view/relay；回傳「我們的成員都不在了」。

    ⚠ **為什麼非 group 不可。** listener（`socat TCP-LISTEN,fork`）死掉不代表它的
      fork children 死了——每個 child 手上握著一條已經升級的 WebSocket。只打 listener
      pid 的話，「撤銷存取權」之後那些 WebSocket 繼續通：cookie 版號管不到它們，
      授權又不會回頭再問一次。同一個 group 一發 `killpg` 就涵蓋 listener、全部
      children、與 bridge script 起的 `docker exec` client（成員繼承 pgid，見
      `_spawn_detached_with_group` 那段）。

    ⚠ **送 killpg 前後的身分把關，與 `_kill` 的 argv[0] 把關是同一件事。** PGID 與 pid
      一樣是可回收的號碼：整組退出後，新的程序群可能拿到同一個號。所以：
        · 送 SIGTERM 前先確認 group 裡**現在**還有我們認得的成員；
        · 每一級訊號之後、下一級之前**再驗一次**——grace 期間整組死光、號碼被別人
          接手的話，下一發就不送了。寧可少殺（有下輪 cleanup），不誤殺。

    ⚠ 沒有 pgid（spawn 早期失敗、舊列、pgid 問不到）→ 降級回 `_kill(pid)` 的單 pid
      路徑：行為與沒有這個機制的舊版逐字相同。**降級不可以炸**，舊列才收得掉。
    """
    names = names or _OUR_TTYD_NAMES
    pgid = process_group_id or _process_group_id(pid)
    if not pgid:
        return _kill(pid, names)
    bridge_hint = bridge_hint or config.MITM_BRIDGE

    def ours_gone() -> bool:
        return not _group_ours_members(pgid, names, bridge_hint)

    if not _group_ours_members(pgid, names, bridge_hint):
        # group 裡已沒有我們認得的成員：要嘛早收完了，要嘛號碼被別人接手（絕不能對它
        # killpg）。用單 pid 那一套收尾確認——它自己的 argv[0] 把關原樣保留。
        return _kill(pid, names)
    for sig, grace in ((signal.SIGTERM, config.VIEW_TERM_GRACE), (signal.SIGKILL, config.VIEW_KILL_GRACE)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True  # 整組剛好自己走完
        except (PermissionError, OSError):
            return False  # 送不出去：可能有成員還活著，不可以當成收掉了
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if ours_gone():
                return True
            time.sleep(0.05)
        # 進下一級訊號前再驗一次（PGID 重用防護，見上）。
        if ours_gone():
            return True
    return ours_gone()


def _probe_host() -> str:
    """就緒探測的目標。ttyd 綁 0.0.0.0 時，控制平面自己仍從 loopback 探得到；
    綁特定位址時就用那個位址。"""
    return "127.0.0.1" if config.TTYD_BIND in ("0.0.0.0", "::") else config.TTYD_BIND


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((_probe_host(), port)) == 0


def _is_ttyd_serving(port: int, session_id: str) -> bool:
    """確認該 port 上的服務真的是「我們這個 session 的 ttyd」。

    只檢查「port 開著」不夠：那個 port 可能早被無關的服務佔住（我們的 ttyd 其實綁失敗
    正在退出），readiness 會誤判成功、之後 nginx 就把使用者導到別人的服務上（review M6）。
    ttyd 以 -b /session/<sid> 掛在子路徑下，故用該路徑取回應來驗明正身。

    ⚠ **身分看 `server` 標頭，不看首頁內容；狀態碼放寬到「回得出 HTTP 就算」。**
      這道探測不帶 cookie，而 Rust 版掛著 `--auth-url`——它會照規矩把這個沒有身分的
      請求擋下來，回 `401` 加 `server: ttyd/…`。用「200 且內容含 ttyd」當判準的話，
      **有裝授權的那顆永遠通不過就緒檢查**，pid 寫不進去，那一列就永遠停在
      「另一個 worker 正在建立」，而畫面上看到的是終端開不起來。
      401 不是壞消息，它同時證明了兩件事：服務起來了，而且它的授權層是活的。
    """
    try:
        with socket.create_connection((_probe_host(), port), timeout=1.0) as sock:
            sock.sendall(f"GET /session/{session_id}/ HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
            head = sock.recv(4096)
    except OSError:
        return False
    if not head.startswith(b"HTTP/1."):
        return False
    # 兩顆 binary 都會送 `server: ttyd/<版本>`，那是比首頁內容更穩的識別：
    # 它在 401 的回應裡也在，而首頁內容只有放行時才有。
    return b"server: ttyd/" in head.lower()


def _wait_ready(port: int, pid: int, session_id: str, timeout: float | None = None) -> bool:
    """等 ttyd 就緒；它先死掉、逾時、或該 port 上是別的服務都回 False。

    ⚠ 逾時值住在 `config.TTYD_READY_TIMEOUT`（預設仍是 5 秒）而不是寫死在簽章裡，
      理由與命名都比照 `mitm_views._wait_ready` 的 `MITM_READY_TIMEOUT`：這是失敗路徑上
      每個 port 各付一次的那個數字，該看得見、調得動。

    ⚠ **中止條件是「那個號碼上沒東西了」（`_pid_exists`），不是 `_process_alive`
      （它還要比對 argv[0]）。** `_spawn_detached` 是 double-fork：`$!` 拿到的是
      `sh -c '"$@" &'` 那個子 shell，要等它 exec 成 ttyd 之後 argv[0] 才對得上。
      拿身分當中止條件的話，剛 spawn 的那一瞬間會被判成「它死了」→ 立刻換下一個 port
      → 把整個範圍掃完 → 報「無可用 port」。**症狀完全指向 port**，而真正的原因是幾毫秒
      的 exec 空窗。

      2026-08-27 在本機用一個啟動較慢的 ttyd 替身（先 sleep 再 `exec -a ttyd`）穩定重現：
      `open_view` 在 0.1 秒內掃完整段報「無可用 port」。真 binary 也有同一個窗口，只是
      通常搶得贏。`test_view_lifecycle` 連跑十次會紅四次，探針錄到的正是
      `WAIT_ABORT port=45000 t=0.000s argv=/bin/sh -c "$@" … sh ttyd -p 45000 …`。

      身分比對該待的地方是 `_kill()`（送 SIGTERM 之前），那裡它是必要的；這裡它只會製造
      偽陰性：「這個 port 上服務的是不是我們要的東西」由下面 `_is_ttyd_serving` 回答，
      那是比 argv 更直接的證據，而且它連 session_id 都驗了，比 argv[0] 還嚴。

    ⚠ `mitm_views._wait_ready` 早一天就修過同一個坑（ADR 0021），這裡是把同一份判斷
      補回 ttyd 這條路，用的是同一支 `_pid_exists`。
    """
    timeout = config.TTYD_READY_TIMEOUT if timeout is None else timeout
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_exists(pid):
            return False
        if _port_open(port) and _is_ttyd_serving(port, session_id):
            return True
        time.sleep(0.1)
    return False


def _view_dict(view_id: int, session_id: str, port: int, pid: int | None, ttyd_bin: str | None = None) -> dict:
    return {
        "view_id": view_id,
        "session_id": session_id,
        "port": port,
        "pid": pid,
        # 這個終端**實際**是哪一顆 ttyd 在服務（C / Rust）。畫面拿它在抽屜標題列標一個
        # tag——兩顆是同一個 UI，出問題時「你看到的是哪一版」是第一個要問的問題。
        # ⚠ 沿用既有 view 時回的是**當初起它的那顆**，不是這個人現在的偏好：改偏好不會
        #   換掉已經在跑的 ttyd（見 app.set_prefs），推出來的值會在最需要它的時候騙人。
        "ttyd_bin": ttyd_bin,
        "ttyd_flavor": config.TTYD_BINS.get(ttyd_bin or "") or None,
        # nginx 對外路徑（ADR 0005 路由 B）；nginx 以 auth_request 取得 port 後動態 proxy
        "path": f"/session/{session_id}/",
        # 無 nginx 時的直連位址（開發用）
        "direct_url": f"http://{config.TTYD_HOST}:{port}/session/{session_id}/",
    }


# --- ttyd 觀測（唯讀）-------------------------------------------------------------


def inspect_ttyd(current_bin: str | None = None) -> dict:
    """此刻所有 ttyd 的實況（管理用，唯讀）。

    `current_bin`＝**看這一頁的人現在選的那一顆**（per-user，見 config.TTYD_BINS）。
    它與每個 view 實際起的那一顆是兩件事：切換偏好之後，先前起的程序還活著，所以每一列
    另外帶 `ttyd_bin`（那一列**當初**用的）與 `proc.bin`（那個程序**現在**的執行檔名）。

    回 `{"views": [...], "orphans": [...], "psutil": bool, "bin": str}`。

    **兩個方向的對帳才是重點**，數量本身意義不大：
      - `orphans`：程序在跑、DB 卻沒有對應的 view。這種 ttyd 沒有任何機制找得到
        （`_clean_views` 只走 DB 列、reconciler 的孤兒清理只管 container），而它若從頭到尾
        沒有 client 連上過，`-q` 也永遠不會觸發。**那個 port 就此消失。**
      - view 的 `alive=False`：DB 有列、程序已死。它佔著 `uq_views_port`，該 session
        下次開終端會拿不到 port。

    ⚠ 只看得到**本容器內**的 ttyd。它們是控制平面的子孫程序（double-fork 後 reparent 給
      PID 1），所以這支必須跑在 control 裡；reconciler 共用同一個 PID namespace 也看得到。
    """
    rows = []
    with session_scope() as s:
        # v.session 是既有的 relationship（models.View.session），不必自己再查一次。
        for v in s.query(View).order_by(View.created_at.desc()).all():
            sess = v.session
            rows.append(
                {
                    "view_id": v.id,
                    "session_id": v.session_id,
                    "port": v.port,
                    "pid": v.pid,
                    "created_at": v.created_at.isoformat(),
                    "ttyd_bin": v.ttyd_bin,
                    "owner": sess.user.username if sess and sess.user else None,
                    "session_name": sess.display_name if sess else None,
                }
            )

    if psutil is None:
        # ⚠ 沒有 psutil 就只能回 DB 那一半，而且要**明講**。不講的話畫面上那個空的
        #   `orphans` 看起來就像「掃過了，很乾淨」——那正是這一頁要抓的那種假綠燈。
        return {
            "views": [{**r, "alive": None, "proc": None} for r in rows],
            "orphans": [],
            "psutil": False,
            "bin": config.ttyd_bin_or_default(current_bin),
        }

    procs = {}
    for p in psutil.process_iter(["pid", "cmdline"]):
        argv = p.info.get("cmdline") or []
        if argv and os.path.basename(argv[0]) in _OUR_TTYD_NAMES:
            procs[p.info["pid"]] = p

    # cpu_percent 第一次呼叫一律回 0.0（要兩個取樣點才算得出來）。與其每個程序各等一次
    # interval，不如先替所有程序建立基準，共用一次短暫的等待：N 個程序也只等一次。
    for p in procs.values():
        with suppress(Exception):  # noqa: BLE001 — 建基準失敗只是少一個數字
            p.cpu_percent(None)

    known = {r["pid"] for r in rows if r["pid"]}
    for r in rows:
        p = procs.get(r["pid"])
        # pid 還沒寫回來的列（open_view 進行中）不是「死的」，是**還不知道**。
        r["alive"] = None if r["pid"] is None else p is not None
        r["proc"] = _proc_facts(p)

    # ⚠ **port 交叉比對，不是只比 pid。** 開終端的那一瞬間有一段「ttyd 已經在跑、但 pid
    #   還沒寫回 DB」的窗口：`_claim_port` 先插一列 pid=NULL，`_spawn_detached` 起 ttyd，
    #   pid 要等 `_wait_ready` 回來才寫。只比對 pid 的話，那個健康的 ttyd 會被標成孤兒，
    #   而這一頁存在的理由就是揪出對不上的東西——例行的假警報會讓整頁失去可信度。
    #   改用「它聽的 port 有沒有被某一列宣告」來認領：port 是 UNIQUE 的，而 in-flight 的
    #   列**已經**帶著 port 了，比 pid 更早可用。
    claimed_ports = {r["port"] for r in rows}
    orphans = []
    for pid, p in procs.items():
        if pid in known:
            continue
        facts = _proc_facts(p)
        listening = set()
        for addr in (facts or {}).get("listening", []):
            with suppress(ValueError):
                listening.add(int(addr.rsplit(":", 1)[-1]))
        if listening & claimed_ports:
            continue  # 有列宣告了它的 port＝正在被領養，不是孤兒
        orphans.append({"pid": pid, "proc": facts})

    return {"views": rows, "orphans": orphans, "psutil": True, "bin": config.ttyd_bin_or_default(current_bin)}


def _proc_facts(p) -> dict | None:
    """一個 ttyd 程序的量測值。任何一項讀不到就略過該項，不讓整頁掛掉。"""
    if p is None:
        return None
    out: dict = {"pid": p.pid}
    with suppress(psutil.Error, OSError):
        argv = p.cmdline()
        if argv:
            out["bin"] = os.path.basename(argv[0])
    # ⚠ 只吞 `psutil.Error` 與 `OSError`，**不要吞 Exception**。`psutil.Error` 已涵蓋
    #   NoSuchProcess / AccessDenied / ZombieProcess，也就是「這個程序問不到」的全部情況。
    #   吞 Exception 會連 AttributeError / TypeError 這種**程式錯誤**一起吃掉——psutil 版本
    #   不對而 `net_connections` 不存在時，畫面上只是少了兩列、log 裡一片安靜，而那正是
    #   那種版本 bug 能靜靜存在好幾個月的機制。
    with suppress(psutil.Error, OSError):
        with p.oneshot():  # 一次系統呼叫餵飽下面所有查詢
            cpu, mem = p.cpu_times(), p.memory_info()
            out.update(
                {
                    "status": p.status(),
                    "started_at": _dt.datetime.fromtimestamp(p.create_time(), _dt.UTC).isoformat(),
                    "cpu_user": round(cpu.user, 3),
                    "cpu_system": round(cpu.system, 3),
                    "rss": mem.rss,
                    "vms": mem.vms,
                    "mem_percent": round(p.memory_percent(), 2),
                    "threads": p.num_threads(),
                    "fds": p.num_fds(),
                }
            )
    with suppress(psutil.Error, OSError):
        out["cpu_percent"] = round(p.cpu_percent(None), 1)  # 基準已在上面建立
    with suppress(psutil.Error, OSError):
        conns = p.net_connections(kind="tcp")
        listen = [c for c in conns if c.status == psutil.CONN_LISTEN]
        out.update(
            {
                # 實際在聽的位址：拿它跟 DB 記的 port 對照，不必自己 TCP 連過去試
                "listening": [f"{c.laddr.ip}:{c.laddr.port}" for c in listen],
                # 已建立的連線數＝現在有幾個人正開著這個終端。ttyd 帶 `-q`（最後一個 client
                # 斷線就自退），所以這個數字同時解釋了它為什麼還活著。
                "clients": sum(1 for c in conns if c.status == psutil.CONN_ESTABLISHED),
            }
        )
    return out or None
