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
except ImportError:      # pragma: no cover - 取決於環境
    psutil = None


# --- 對外 API --------------------------------------------------------------------

def open_view(session_id: str, container_name: str, ttyd_bin: str | None = None) -> dict:
    """為 session 開一個 on-demand 終端 view；已有存活的就沿用（點兩次不會多起一個）。"""
    existing = _alive_view(session_id)
    if existing is not None:
        return existing

    for port in range(config.TTYD_PORT_MIN, config.TTYD_PORT_MAX + 1):
        view_id = _claim_port(session_id, port)
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
            continue                       # 這個 port 被別的 session 佔走 → 換下一個
        pid = None
        # ⚠ 先收斂再用：argv[0] 與記進 DB 的必須是**同一個**字串，否則標記說的是一回事、
        #   實際跑的是另一回事——那比不標更糟。
        binary = config.ttyd_bin_or_default(ttyd_bin)
        try:
            pid = _spawn_detached(
                _ttyd_argv(port, container_name, session_id, binary))
            if _wait_ready(port, pid, session_id):
                with session_scope() as s:
                    row = s.get(View, view_id)
                    if row is None:          # 宣告被別人清掉了（不該發生）→ 當作失敗處理
                        raise ViewError("view 宣告已消失")
                    row.pid = pid
                    row.ttyd_bin = binary
                return _view_dict(view_id, session_id, port, pid, binary)
        except Exception:
            # spawn 之後任一步失敗（含寫 pid 進 DB 失敗）都必須收掉那個 ttyd，
            # 否則它會活著卻沒人記得 pid，永遠不會被回收（review H3）。
            _kill(pid)
            _drop_view(view_id)
            raise
        # 起不來（多半是 port 被 DB 以外的程序佔住）→ 收乾淨，換下一個 port
        _kill(pid)
        _drop_view(view_id)
    raise ViewError(f"{config.TTYD_PORT_MIN}-{config.TTYD_PORT_MAX} 無可用 port")


def close_views(session_id: str) -> int:
    """提前收掉某 session 的所有 view（session 被 terminate、或使用者明確關閉）。

    正常情況不需呼叫——關網頁時 `-q` 會讓 ttyd 自己退出。
    """
    closed = 0
    with session_scope() as s:
        rows = s.query(View).filter(View.session_id == session_id).all()
        for row in rows:
            if row.pid:
                _kill(row.pid)
            s.delete(row)
            closed += 1
    return closed


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
                if row.created_at < cutoff:   # 逾期未就緒＝宣告者已死，可回收
                    dead.append(row.id)
                continue                       # 寬限期內：尊重別的 worker 的 in-flight 宣告
            if _process_alive(row.pid):
                out.append(_view_dict(row.id, row.session_id, row.port, row.pid,
                                      row.ttyd_bin))
            else:
                dead.append(row.id)
    for vid in dead:
        _drop_view(vid)
    return out


# --- port 分配（DB UNIQUE 為跨 worker 仲裁）-------------------------------------

_PEER = object()   # _claim_port 的哨兵：撞的是 session_id，不是 port


def _claim_port(session_id: str, port: int):
    """試著佔一個 port。

    回傳 view_id（搶到）／`None`（這個 port 被別的 session 佔走，換下一個）／
    `_PEER`（別的 worker 已為同一 session 建了 view——換 port 沒有意義）。

    views 表上有兩個 UNIQUE（port、session_id），光看 IntegrityError 分不出撞了哪一個；
    不分清楚就會把「同一 session 已有 view」誤當成「port 撞號」而掃完整個 port 範圍。
    """
    try:
        with session_scope() as s:
            row = View(session_id=session_id, port=port)
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
                return None                     # 不存在 → 純粹是 port 撞號
            ready = row.pid is not None and _process_alive(row.pid)
            snapshot = _view_dict(row.id, row.session_id, row.port, row.pid) if ready else None
        if snapshot and _port_open(snapshot["port"]):
            return snapshot
        time.sleep(0.2)
    return None


def _drop_view(view_id: int) -> None:
    with suppress(Exception), session_scope() as s:
        row = s.get(View, view_id)
        if row is not None:
            s.delete(row)


def _alive_view(session_id: str) -> dict | None:
    for v in list_views(session_id):
        if _port_open(v["port"]):
            return v
    return None


# --- ttyd 進程 --------------------------------------------------------------------

def _ttyd_argv(port: int, container_name: str, session_id: str,
               ttyd_bin: str | None = None) -> list[str]:
    return [
        # C 版或 Rust 版，由**開這個終端的人**的偏好決定（users.ttyd_bin，管理畫面的
        # 「設定」可切）。一律經 ttyd_bin_or_default() 收斂：這個值會變成 argv[0]，
        # 不認得的字串（白名單改過、DB 留著舊值）必須退回預設，不可以直接拿去 exec。
        config.ttyd_bin_or_default(ttyd_bin),
        "-p", str(port),
        "-i", config.TTYD_BIND,        # 非容器化＝loopback；容器化＝0.0.0.0（僅內部網路，ADR 0009）
        "-b", f"/session/{session_id}",  # base-path，配合 nginx 子路徑路由
        "-W",                          # 可寫（互動需要）
        "-q",                          # 全部 client 斷線即自行退出＝關網頁自動回收（ADR 0008）
        # ttyd 預設把「完整命令 + 容器 hostname」當網頁標題。
        #
        # ⚠ **`titleFixed` 沒有解決那件事，它只是把畫面蓋掉。** 這是 client 選項：
        #   真正的標題（`docker attach --detach-keys=… claude-pty-<sid>` 加上容器
        #   hostname）**在那之前就已經送給每一個連上的 client 了**，瀏覽器只是被要求
        #   顯示別的字。所以這一行買到的是「分頁標題、瀏覽紀錄、截圖裡看不到」，
        #   買不到「沒有送出去」。
        #
        # ⚠ 真正的修法是**伺服器端**的 `--title`：它直接換掉宣告出去的標題，命令列
        #   一個字都不上線。那是 Rust 版才有的選項，C 版沒有——所以這是兩顆 binary
        #   的實質差異之一，不是「快一點慢一點」而已。接上之後這一行仍然留著
        #   （C 版仍靠它遮），但 Rust 版那條路要改走 `--title`。
        "-t", f"titleFixed=agent-tty · {session_id}",
        # 讓使用者選得到文字。Claude Code 的 TUI 會開啟滑鼠追蹤（實測 ?1000/?1002/?1003/
        # ?1006 全開），一開啟，拖曳就被當成應用程式的滑鼠事件送進 TUI，終端不再拿它來
        # 選取——畫面上的文字變成完全無法複製。這兩個選項讓修飾鍵可以繞過追蹤：
        #   macOS：按住 Option 拖曳
        #   其他：按住 Alt 拖曳（xterm.js 的 altClickMovesCursor 關掉才不會誤觸發移游標）
        "-t", "macOptionClickForcesSelection=true",
        "-t", "altClickMovesCursor=false",
        # 選取後自動複製到剪貼簿，省掉「選了還要再按 Cmd+C」這一步（選取本身已經很費事）
        "-t", "copyOnSelect=true",
        "docker", "attach",
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
        capture_output=True, text=True, timeout=10,
        start_new_session=True,          # 脫離控制終端與 process group
        check=False,                     # sh 的退出碼不重要，下面直接驗 pid 輸出
    ).stdout.strip()
    if not pid_line.isdigit():
        raise ViewError("ttyd 啟動失敗（無法取得 pid）")
    return int(pid_line)


# 我們自己會起的 ttyd 檔名（＝白名單本身）。**每個名字都收**，不是只收「這個人現在選的
# 那一顆」：偏好是 per-user 的，而且切換之後先前起的程序還活著——只認一個名字的話，那些程序
# 既不會被認成我們的（收不掉、port 永久洩漏），也不會出現在觀測頁的孤兒清單裡
# （畫面看起來乾淨，其實有東西在跑）。
_OUR_TTYD_NAMES = frozenset(config.TTYD_BINS)


def _is_our_ttyd(pid: int | None) -> bool:
    """確認該 pid 真的是我們起的 ttyd。

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
        return True           # 沒裝 psutil：無從佐證，沿用「僅憑 pid 存在」的舊行為
    try:
        argv = psutil.Process(pid).cmdline()
    except psutil.NoSuchProcess:
        return False          # 程序已不存在
    except (psutil.AccessDenied, OSError):
        return True           # 問不到（權限等）：無法佐證，不因此誤判為死
    return bool(argv) and os.path.basename(argv[0]) in _OUR_TTYD_NAMES


def _kill(pid: int | None) -> None:
    if pid and _is_our_ttyd(pid):
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, 15)  # SIGTERM；不需 wait——它不是我們的子程序，由 init reap


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)      # 只探測存在性，不送信號
    except (ProcessLookupError, OSError):
        return False
    return _is_our_ttyd(pid)  # 存在還不夠：得確認是我們的 ttyd 而非被回收的號碼（review H8）


def inspect_ttyd(current_bin: str | None = None) -> dict:
    """此刻所有 ttyd 的實況（管理用，唯讀）。

    `current_bin`＝**看這一頁的人現在選的那一顆**（per-user，見 config.TTYD_BINS）。
    它與每個程序實際跑的 `proc.bin` 是兩件事：切換偏好之後，先前起的程序還活著。

    回 `{"views": [...], "orphans": [...], "psutil": bool}`。

    **兩個方向的對帳才是重點**，數量本身意義不大：
      - `orphans`：程序在跑、DB 卻沒有對應的 view。這種 ttyd 沒有任何機制找得到——
        `_clean_views` 只走 DB 列、reconciler 的 `_remove_orphans` 只管 container，
        而它若從頭到尾沒有 client 連上過，`-q` 也永遠不會觸發。那個 port 就此消失。
      - view 的 `alive=False`：DB 有列、程序已死。它佔著 `uq_views_port`，
        該 session 下次開終端會拿不到 port。

    ⚠ 只看得到**本容器內**的 ttyd。它們是控制平面的子孫程序（double-fork 後 reparent
      給 PID 1），所以這支必須跑在 control 裡；reconciler 因為共用 PID namespace 也看得到。
    """
    rows = []
    with session_scope() as s:
        # v.session 是既有的 relationship（models.View.session），不必自己再查一次，
        # 也不必為此多 import 一個名字。
        for v in s.query(View).order_by(View.created_at.desc()).all():
            sess = v.session
            rows.append({
                "view_id": v.id, "session_id": v.session_id, "port": v.port,
                "pid": v.pid, "created_at": v.created_at.isoformat(),
                "owner": sess.user.username if sess and sess.user else None,
                "session_name": sess.display_name if sess else None,
            })

    if psutil is None:
        # 沒有 psutil 就只能回 DB 那一半，並且**明講**——不要讓畫面上的空 orphans
        # 看起來像「掃過了，很乾淨」。
        return {"views": [{**r, "alive": None, "proc": None} for r in rows],
                "orphans": [], "psutil": False,
                "bin": config.ttyd_bin_or_default(current_bin)}

    procs = {}
    for p in psutil.process_iter(["pid", "cmdline"]):
        argv = p.info.get("cmdline") or []
        if argv and os.path.basename(argv[0]) in _OUR_TTYD_NAMES:
            procs[p.info["pid"]] = p

    # cpu_percent 第一次呼叫一律回 0.0（它要兩個取樣點才算得出來）。與其每個程序各等
    # 一次 interval，不如所有程序先取基準、共用一次短暫的等待——N 個程序也只等一次。
    for p in procs.values():
        with suppress(Exception):
            p.cpu_percent(None)
    if procs:
        time.sleep(config.TTYD_CPU_SAMPLE_SECONDS)

    known = {r["pid"] for r in rows if r["pid"]}
    for r in rows:
        p = procs.get(r["pid"])
        # pid 還沒寫回來的列（open_view 進行中）不是「死的」，是**還不知道**。
        r["alive"] = None if r["pid"] is None else p is not None
        r["proc"] = _proc_facts(p)

    # ⚠ 開終端的那個瞬間會有一段「ttyd 已經在跑、但 pid 還沒寫回 DB」的窗口：
    #   _claim_port 先插一列 pid=NULL，_spawn_detached 起 ttyd，pid 要等 _wait_ready
    #   回來才寫。只比對 pid 的話，那個健康的 ttyd 會被標成紅色的「孤兒程序」，
    #   tooltip 還宣稱「那個 port 就此消失」——一個例行的假警報，而這一頁存在的理由
    #   就是揪出對不上的東西，例行假警報會讓整頁失去可信度。
    #   list_views() 早就有處理這個窗口的機制（pid is None + VIEW_CLAIM_GRACE）。
    #   這裡改用「它聽的 port 有沒有被某一列宣告」來認領：port 是 UNIQUE 的，
    #   而 in-flight 的列**已經**帶著 port 了，比 pid 更早可用。
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
            continue          # 有列宣告了它的 port＝正在被領養，不是孤兒
        orphans.append({"pid": pid, "proc": facts})
    # `bin`＝**現在設定要起哪一顆**（每個程序實際是哪一顆在 proc.bin，兩者可能不同：
    # 切換設定之後舊版起的程序還活著）。
    return {"views": rows, "orphans": orphans, "psutil": True,
            "bin": config.ttyd_bin_or_default(current_bin)}


def _proc_facts(p) -> dict | None:
    """一個 ttyd 程序的量測值。任何一項讀不到就略過該項，不讓整頁掛掉。

    ⚠ 每一組量測**整組寫入或整組不寫**（先收在區域變數，成功才 update）。
      一個一個往 out 塞的話，中途 raise（例如某些程序問不到 num_fds）會留下
      「threads 有、fds 沒有」這種半套資料，而畫面那邊是用一個鍵去決定要不要畫整行的
      ——結果會印出 `5 / undefined`、`VMS undefined B`。那比不顯示還糟：它看起來像個量到的值。
    """
    if p is None:
        return None
    out: dict = {}
    # 這個程序**實際**是哪一顆 binary（C 版 `ttyd` / Rust 版 `ttyd-rust`）。切換設定之後
    # 舊版起的程序還會活一陣子，畫面要看得出「這一列跟現在的設定不同」——只顯示設定值
    # 會讓那些殘留看起來像是新設定起的。
    with suppress(psutil.Error, OSError):
        argv = p.cmdline()
        if argv:
            out["bin"] = os.path.basename(argv[0])
    # ⚠ 只吞 psutil.Error 與 OSError，**不要吞 Exception**。`psutil.Error` 已涵蓋
    #   NoSuchProcess / AccessDenied / ZombieProcess，也就是「這個程序問不到」的全部情況。
    #   吞 Exception 會連 AttributeError / TypeError 這種**程式錯誤**一起吃掉——
    #   psutil 版本不對而 `net_connections` 不存在時，畫面上只是少了兩列、log 裡一片安靜，
    #   正是那個版本 bug 能靜靜存在的機制。
    with suppress(psutil.Error, OSError):
        with p.oneshot():       # 一次系統呼叫餵飽下面所有查詢
            cpu, mem = p.cpu_times(), p.memory_info()
            group = {
                "status": p.status(),
                "started_at": _dt.datetime.fromtimestamp(
                    p.create_time(), _dt.UTC).isoformat(),
                "cpu_user": round(cpu.user, 3),
                "cpu_system": round(cpu.system, 3),
                "rss": mem.rss,
                "vms": mem.vms,
                "mem_percent": round(p.memory_percent(), 2),
                "threads": p.num_threads(),
                "fds": p.num_fds(),
            }
        out.update(group)
    with suppress(psutil.Error, OSError):
        out["cpu_percent"] = round(p.cpu_percent(None), 1)   # 基準已在上面建立
    with suppress(psutil.Error, OSError):
        conns = p.net_connections(kind="tcp")
        listen = [c for c in conns if c.status == psutil.CONN_LISTEN]
        out.update({
            # 實際在聽的位址：拿它跟 DB 記的 port 對照，不必自己 TCP 連過去試
            "listening": [f"{c.laddr.ip}:{c.laddr.port}" for c in listen],
            # 已建立的連線數＝現在有幾個人正開著這個終端。ttyd 帶 -q（最後一個 client
            # 斷線就自退），所以這個數字同時解釋了它為什麼還活著。
            "clients": sum(1 for c in conns if c.status == psutil.CONN_ESTABLISHED),
        })
    return out or None


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
    """
    try:
        with socket.create_connection((_probe_host(), port), timeout=1.0) as sock:
            sock.sendall(
                f"GET /session/{session_id}/ HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
            head = sock.recv(4096)
    except OSError:
        return False
    # ttyd 的首頁標題固定為 "ttyd - Terminal"；200 且含此字樣才算是我們的服務
    return head.startswith(b"HTTP/1.") and b" 200 " in head[:20] and b"ttyd" in head


def _wait_ready(port: int, pid: int, session_id: str, timeout: float = 5.0) -> bool:
    """等 ttyd 就緒；它先死掉、逾時、或該 port 上是別的服務都回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _process_alive(pid):
            return False
        if _port_open(port) and _is_ttyd_serving(port, session_id):
            return True
        time.sleep(0.1)
    return False


def _view_dict(view_id: int, session_id: str, port: int, pid: int | None,
               ttyd_bin: str | None = None) -> dict:
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
