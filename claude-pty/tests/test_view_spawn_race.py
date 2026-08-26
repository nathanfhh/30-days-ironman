"""`open_view` 對「啟動較慢的 ttyd」不可以誤判成死掉（ADR 0008）。不需 docker。

    uv run --with flask --with docker --with sqlalchemy --with psutil python tests/test_view_spawn_race.py

守的是 `views._wait_ready` 的**中止條件**。`_spawn_detached` 是 double-fork：`$!` 拿到的是
`sh -c '"$@" &'` 那個子 shell，它 exec 成 ttyd 之前 argv[0] 是 `sh`。中止條件若用
`_process_alive`（要比對 argv[0]），剛 spawn 的那一瞬間就會被判成「它死了」→ 立刻換下一個
port → 把整個範圍掃完 → 報「**無可用 port**」。

**症狀完全指向 port，而原因是幾毫秒的 exec 空窗**，這正是它需要一支專屬測試的理由：
真的 ttyd 通常搶得贏那幾毫秒，所以這條路在乾淨的開發機上多半是綠的
（`test_view_lifecycle` 連跑十次紅四次，而每一次的失敗訊息都在講別的事）。

做法同 `test_mitm_relay`：**用一個啟動較慢的同名替身把那個窗口撐大**，讓它從「偶爾」
變成「每次」。替身取名 `ttyd` 是必要的而不是方便：`views._is_ours` 比對的是 argv[0] 的
basename。

⚠ 替身刻意用**帶 shebang 的 bash 腳本**：核心組出來的 argv[0] 是直譯器（`/bin/bash`），
  於是 sleep 那段期間 `_is_ours` 一律不成立。這跟 `sh -c` 那個真正的窗口是同一個形狀，
  而且撐得住幾秒，不必去賭時序。
"""

import importlib
import os
import signal
import socket
import sys
import tempfile
import time

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

_tmp = tempfile.mkdtemp(prefix="claude-pty-viewrace-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config  # noqa: E402

# 專屬 port 區段：正式服務是 41000–41100、test_view_lifecycle 是 45000–45050、
# test_mitm_relay 是 45200–45210。共用的話「無殘留」那類檢查會把別人的算進來。
# ⚠ 只給**五個** port：這支要驗的其中一條是「不可以掃完整段」，範圍窄一點的話，
#   壞掉時的失敗訊息（無可用 port）來得快，也不會在 CI 上佔著五個 5 秒的逾時。
os.environ["CLAUDE_PTY_TTYD_PORT_MIN"] = "45300"
os.environ["CLAUDE_PTY_TTYD_PORT_MAX"] = "45304"
importlib.reload(config)

from server import db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()
db.init_db()

from server import sessions as sessions_mod, views  # noqa: E402
from server.models import Session as SessionRow, View as ViewRow  # noqa: E402

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


# --- 假的 ttyd --------------------------------------------------------------------
#
# 只做 `_is_ttyd_serving` 真正要看的那件事：對任何請求回一個帶 `Server: ttyd/…` 的
# HTTP 回應。**先 sleep 再 exec**，那段 sleep 就是被撐大的 exec 空窗。
_IMPL = os.path.join(_tmp, "fake_ttyd_impl.py")
with open(_IMPL, "w", encoding="utf-8") as _f:
    _f.write(
        "import socket, sys, threading\n"
        "a = sys.argv[1:]\n"
        "port = int(a[a.index('-p') + 1])\n"
        "srv = socket.socket()\n"
        "srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "srv.bind(('127.0.0.1', port))\n"
        "srv.listen(16)\n"
        "while True:\n"
        "    c, _ = srv.accept()\n"
        "    threading.Thread(target=lambda s=c: (s.recv(4096), s.sendall(\n"
        "        b'HTTP/1.1 200 OK\\r\\nServer: ttyd/1.7.7-fake\\r\\nContent-Length: 0\\r\\n\\r\\n'),\n"
        "        s.close()), daemon=True).start()\n"
    )
_BIN = os.path.join(_tmp, "bin")
os.makedirs(_BIN, exist_ok=True)
_FAKE = os.path.join(_BIN, "ttyd")
with open(_FAKE, "w", encoding="utf-8") as _f:
    # ⚠ `exec -a ttyd` 是 bash builtin（dash 沒有），同 test_ttyd_identity 的理由。
    #   沒有它的話 exec 之後 argv[0] 會是直譯器的路徑，`_kill` 永遠認不出來。
    _f.write(f'#!/bin/bash\nsleep "${{FAKE_TTYD_DELAY:-1.0}}"\nexec -a ttyd {sys.executable} {_IMPL} "$@"\n')
os.chmod(_FAKE, 0o755)
os.environ["PATH"] = _BIN + os.pathsep + os.environ["PATH"]

_spawned: list[int] = []


def free_port() -> int:
    """跟核心要一個此刻沒人用的 port。

    ⚠ **不可以在這裡寫死一個「範圍外、不會有人用」的號碼。** 實際踩到（2026-08-27）：
      45299 上蹲著一顆別的東西（另一支測試留下的 socat），於是替身 bind 失敗、當場退出，
      而 `_wait_ready` 忠實地回報「它死了」：一條**對的**斷言紅在一個與它無關的原因上，
      失敗訊息看起來就像產品碼壞了。
    ⚠ 這兩顆探針刻意不走 `open_view`，所以不必（也不該）落在 `TTYD_PORT_MIN..MAX` 裡：
      那一段要留給下面那條「用的是範圍裡的第一個 port」的斷言，被探針佔走就驗不到了。
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spawn_slow(port: int, sid: str, delay: str = "1.0") -> int:
    """起一顆「還要 delay 秒才會變成 ttyd」的替身，回它的 pid。"""
    os.environ["FAKE_TTYD_DELAY"] = delay
    pid = views._spawn_detached(views._ttyd_argv(port, f"claude-pty-{sid}", sid))
    _spawned.append(pid)
    return pid


def _pids_on_bin() -> list[int]:
    """此刻還活著的替身有哪些（不論它 exec 完了沒有）。

    ⚠ 判準是「cmdline 裡有沒有這次測試的暫存目錄」，不是「叫不叫 ttyd」。窗口裡它的
      argv 是 `/bin/bash <tmp>/bin/ttyd …`、exec 之後是 `ttyd <tmp>/fake_ttyd_impl.py …`，
      兩種形狀都要撿得到。只認 `ttyd` 這個名字的話，**漏掉的正好是要抓的那一種**。
    ⚠ 目錄是 `mkdtemp` 給的，所以這條掃描不會把別的測試或正式服務的行程算進來。
    """
    if views.psutil is None:
        return []
    out = []
    for p in views.psutil.process_iter(["pid", "cmdline"]):
        with __import__("contextlib").suppress(Exception):
            if any(_tmp in a for a in (p.info["cmdline"] or [])):
                out.append(p.info["pid"])
    return out


def seed(sid: str) -> str:
    """建一筆 session 列（FK 是開著的，view 的列一定要掛在真的 session 上）。"""
    uid = sessions_mod.ensure_system_user()
    with db.session_scope() as s:
        if s.get(SessionRow, sid) is None:
            s.add(SessionRow(id=sid, container_name=f"claude-pty-{sid}", user_id=uid, status="running", workdir="/tmp"))
    return f"claude-pty-{sid}"


try:
    print("== 前提：exec 空窗裡，存在性與身分的答案是相反的 ==")
    # 這一段不驗產品行為，驗的是「這支測試真的把窗口撐開了」。它要是綠不了，
    # 下面每一條都失去意義（測到的會是別的東西）。
    _probe_port = free_port()
    _pid = spawn_slow(_probe_port, "windowprobe", delay="3.0")
    time.sleep(0.4)  # 還在 sleep，argv[0] 仍是 bash
    check("替身還在（_pid_exists 為真）", views._pid_exists(_pid) is True)
    check("但 argv[0] 還不是 ttyd（_process_alive 為假）＝窗口確實開著", views._process_alive(_pid) is False)

    print("== 🔴 `_wait_ready` 不可以把這個窗口當成「它死了」 ==")
    # 這就是 bug 本身：中止條件用 `_process_alive` 的話，這一條會在 0.00 秒回 False。
    _t0 = time.time()
    _ok = views._wait_ready(_probe_port, _pid, "windowprobe", timeout=10.0)
    _el = time.time() - _t0
    check(f"等到它 exec 完並開始服務才算數（耗時 {_el:.1f}s）", _ok is True)
    check("而且是真的等了，不是立刻回答（＞替身的 3 秒延遲）", _el > 2.5)
    views._kill(_pid)

    print("== 🔴 核心：啟動較慢的 ttyd 要用第一個 port 就開起來，不可以報「無可用 port」 ==")
    sid = "slowstart001"
    ctr = seed(sid)
    os.environ["FAKE_TTYD_DELAY"] = "1.0"
    _t0 = time.time()
    _err = None
    v = None
    try:
        v = views.open_view(sid, ctr)
    except views.ViewError as e:
        _err = e
    _el = time.time() - _t0
    if not check(f"open_view 成功（{'ViewError: ' + str(_err) if _err else 'port ' + str(v['port'])}）", v is not None):
        # 壞掉時把它掃了幾個 port 講出來，這是「症狀指向 port、原因不在 port」的那一句。
        print(
            f"        ↑ 掃完 {config.TTYD_PORT_MIN}-{config.TTYD_PORT_MAX} 只花了 {_el:.2f}s，"
            f"每個 port 連 1 秒都沒等，這不是 port 不夠，是就緒判斷把 exec 空窗當成了死亡"
        )
    else:
        _spawned.append(v["pid"])
        check(f"用的是範圍裡的第一個 port（got {v['port']}）", v["port"] == config.TTYD_PORT_MIN)
        check("DB 記下了 pid（＝這一列不會停在 in-flight 宣告）", v["pid"] is not None)
        check("那顆行程此刻確實是我們的 ttyd", views._process_alive(v["pid"]) is True)
        check("port 真的在服務", views._port_open(v["port"]) is True)

    print("== 🔴 收程序：`_kill` 在那個窗口裡收不掉，失敗路徑必須用 `_kill_spawned` ==")
    # `_kill()` 送訊號前要確認 argv[0] 在白名單裡，而窗口裡它是 `bash`／`sh`。
    # 於是 `_kill()` 判定「不是我們的程序」，**直接回 True 什麼都不做**。
    # 呼叫端接著 `_drop_view()` 刪掉唯一的追蹤記錄，那顆行程就此沒有人記得。
    # （形狀與斷言順序同 test_mitm_relay 的 socat 版，這裡是 ttyd 版。）
    _kp = spawn_slow(free_port(), "killprobe", delay="30.0")
    time.sleep(0.4)
    check("前提：還在窗口裡（存在、但 argv[0] 還不是 ttyd）", views._pid_exists(_kp) and not views._process_alive(_kp))
    check(
        "🔴 `_kill()` 回 True 卻沒有收掉它（這就是為什麼不能只叫它）",
        views._kill(_kp) is True and views._pid_exists(_kp),
    )
    check("🔴 `_kill_spawned()` 收得掉", views._kill_spawned(_kp, grace=0.5) is True)
    check("　└ 行程真的不在了", not views._pid_exists(_kp))

    print("== 🔴 `open_view` 走完失敗路徑之後，不可以留下沒有人記得的 ttyd ==")
    # 把窗口撐到比 `_wait_ready` 的 5 秒逾時還長：替身在整個逾時期間都還沒 exec，
    # 於是 open_view 的收尾**確定**落在窗口裡。這是那條路徑唯一穩定重現的方式。
    # ⚠ port 範圍縮成一個，否則每個 port 都要付一次 5 秒逾時。
    os.environ["CLAUDE_PTY_TTYD_PORT_MIN"] = os.environ["CLAUDE_PTY_TTYD_PORT_MAX"] = str(free_port())
    importlib.reload(config)
    _sid2 = "leakprobe001"
    _ctr2 = seed(_sid2)
    os.environ["FAKE_TTYD_DELAY"] = "8.0"
    _before = set(_pids_on_bin())
    _err2 = None
    try:
        views.open_view(_sid2, _ctr2)
    except views.ViewError as e:
        _err2 = e
    check(f"前提：這一輪本來就該失敗（{_err2}）", _err2 is not None)
    _leaked = set(_pids_on_bin()) - _before
    _spawned.extend(_leaked)  # 不管綠紅都要收乾淨
    check(f"🔴 沒有留下孤兒行程（多出來 {len(_leaked)} 顆）", not _leaked)
    with db.session_scope() as _s:
        check("DB 那一列也收乾淨了", _s.query(ViewRow).filter(ViewRow.session_id == _sid2).count() == 0)

    print("== 反向：行程真的不在了，還是要回 False，而且要快 ==")
    # 中止條件放寬成「存在性」之後，最容易犯的下一個錯是「它永遠等到逾時」。
    _dead_port = free_port()
    _dead = spawn_slow(_dead_port, "deadprobe", delay="30.0")
    time.sleep(0.3)
    os.kill(_dead, signal.SIGKILL)
    time.sleep(0.3)
    check("前提：那個號碼上已經沒東西了", views._pid_exists(_dead) is False)
    _t0 = time.time()
    _ok = views._wait_ready(_dead_port, _dead, "deadprobe", timeout=5.0)
    _el = time.time() - _t0
    check("回 False", _ok is False)
    check(f"而且沒有等到逾時（{_el:.2f}s ＜ 5s）", _el < 1.0)

finally:
    print("== 清理 ==")
    for _p in _spawned:
        try:
            os.kill(_p, signal.SIGKILL)
        except OSError:
            pass
    db.reset_engine()
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{_pass} 過、{_fail} 失敗")
sys.exit(1 if _fail else 0)
