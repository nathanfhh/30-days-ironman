"""`_kill` 必須等到 ttyd **真的停了**才回報成功（views.py）。不需要 docker。

    uv run --with flask --with docker --with sqlalchemy --with psutil \
        python tests/test_ttyd_kill.py

為什麼要有這支：舊版 `_kill` 送完 SIGTERM 就 `return True`，而「訊號送出去了」跟「它停了」
是兩件事。ttyd 忽略 SIGTERM、被 SIGSTOP 停住、或卡在不可中斷的 I/O 時，`os.kill` 一樣不會
拋例外——於是 `close_views` 刪掉唯一那一列、API 回報「終端都已失效」，而那個 WebSocket
還連著。**收存取權的動作最不能有的失效方式，就是它在失敗時看起來成功**：真的失敗還有人
會去看，假的成功沒有。

⚠ 這支測的是真的 subprocess，不是 mock。用 mock 測不出這件事——被 mock 掉的正是
  「訊號送出去之後那個程序到底怎麼了」，而那就是問題本身。
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config, views  # noqa: E402
from server.views import _gone, _kill, psutil  # noqa: E402

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


if psutil is None:
    print("psutil 未安裝——身分綁定那幾條測不了。請 `--with psutil` 再跑一次。")
    sys.exit(1)

procs: dict[int, subprocess.Popen] = {}


def spawn(shell: str) -> int:
    """起一個 argv[0] 是 `ttyd` 的程序（`_is_our_ttyd` 才會放行 `_kill` 動它）。

    ⚠ 這裡**必須把 argv[0] 檢查當成硬失敗**，不能只是等等看。寫這支的時候踩到：
      `exec -a ttyd bash -c "sleep 30"` 的內層 bash 會把單一指令優化成 `exec sleep`，
      argv[0] 被換成 `sleep`——於是 `_is_our_ttyd` 回 False、`_kill` 直接 return True
      而**一個訊號都沒送**，測試看起來只是「回來時它還在」，完全不像是自己的設定錯了。
    """
    p = subprocess.Popen(["bash", "-c", shell], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs[p.pid] = p
    argv0 = None
    for _ in range(80):
        time.sleep(0.05)
        try:
            cmd = psutil.Process(p.pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        argv0 = cmd[0] if cmd else None
        if argv0 == "ttyd":
            return p.pid
    raise AssertionError(f"起不出 argv[0]=ttyd 的程序（實際是 {argv0!r}），這支測試的前提就不成立")


def reaped_gone(pid: int) -> bool:
    """`_kill` 回來之後，那個程序是不是真的沒了。

    ⚠ 要先 `wait()` 收屍才問得準，而那件事**只發生在測試裡**：production 的 ttyd 是
      double-fork、reparent 給 init 的（見 views 檔頭），死了立刻被 init 收走。這支測試的
      父行程是 python 自己，不 wait 的話它會停在 zombie，而 `os.kill(pid, 0)` 對殭屍照樣
      成功——「它還在」與「它死了但沒人收屍」看起來一模一樣。
      （`_gone` 帶 psutil 身分時分得出來，靠的正是 STATUS_ZOMBIE 那一格。）
    """
    p = procs.get(pid)
    if p is not None:
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            return False
    return _gone(pid, None)


# 把兩個上限調短：測的是「有沒有等」與「等不到會不會升級」，不是等多久。
config.VIEW_TERM_GRACE = 0.8
config.VIEW_KILL_GRACE = 1.5

# 乖乖的 ttyd：argv[0]=ttyd 的 sleep，SIGTERM 是預設處置（直接死）。
GOOD = "exec -a ttyd sleep 30"
# 賴著不走的 ttyd：內層 bash 有 trap 與迴圈，所以不會被優化成 exec，argv[0] 保得住。
STUBBORN = "exec -a ttyd bash -c 'trap \"\" TERM; while :; do sleep 0.2; done'"

try:
    print("== 正常的 ttyd：收到 SIGTERM 就走 ==")
    pid = spawn(GOOD)
    t0 = time.monotonic()
    ok = _kill(pid)
    elapsed = time.monotonic() - t0
    check("_kill 回 True", ok is True)
    check(f"沒有白等滿 grace（{elapsed:.2f}s < 0.8s）", elapsed < 0.8)
    check("回來的時候它已經不在了（不是「等一下就會不在」）", reaped_gone(pid) is True)

    print("== 🔴 忽略 SIGTERM 的：必須升級 SIGKILL，不可以回報成功就走 ==")
    # 這正是舊版會說謊的那一格：訊號送得出去，程序照樣活著。
    pid = spawn(STUBBORN)
    os.kill(pid, 15)
    time.sleep(0.3)
    check("先確認情境成立：送了 SIGTERM 它還在", _gone(pid, None) is False)
    t0 = time.monotonic()
    ok = _kill(pid)
    elapsed = time.monotonic() - t0
    check("_kill 回 True（升級之後真的收掉了）", ok is True)
    check("有等過 TERM grace 才升級（不是立刻 SIGKILL）", elapsed >= 0.8)
    check("回來的時候它已經不在了", reaped_gone(pid) is True)

    print("== 🔴 被 SIGSTOP 停住的：TERM 遞不到，SIGKILL 才收得掉 ==")
    pid = spawn(GOOD)
    os.kill(pid, 19)  # SIGSTOP
    time.sleep(0.2)
    check("_kill 回 True", _kill(pid) is True)
    check("回來的時候它已經不在了", reaped_gone(pid) is True)

    print("== 🔴 收不掉就要說收不掉（不可以回 True）==")

    class _NeverGone:
        """永遠帶不走的身分。真實對應：卡在不可中斷 I/O（D state）的程序。"""

        def is_running(self):
            return True

        def status(self):
            return "running"

    pid = spawn(GOOD)
    t0 = time.monotonic()
    check("等不到它消失 → _await_gone 回 False", views._await_gone(pid, _NeverGone(), 0.3) is False)
    check("而且它真的等了那 0.3 秒（不是立刻放棄）", time.monotonic() - t0 >= 0.3)
    _kill(pid)

    print("== 🔴 等的是「原本那個程序」，不是「這個號碼」==")

    # PID 會被回收。只看存在性的話，等待期間有人接手同一個號碼就會一路等到逾時，
    # 然後把 SIGKILL 送給一個無關的程序。psutil 的 Process 記著 create_time，
    # is_running() 分得出「號碼還在但已經換人」。
    class _Reused:
        def is_running(self):
            return False

        def status(self):
            return "running"

    live = spawn(GOOD)
    check("身分已經換人 → 視為已消失（不會空等到逾時）", _gone(live, _Reused()) is True)
    check("同一個號碼、不帶身分去問 → 還在（證明上一條不是因為它本來就死了）", _gone(live, None) is False)
    _kill(live)

    print("== 沒有 pid / 不是我們的 ttyd：不動它 ==")
    check("pid 是 None → True（沒有東西要收）", _kill(None) is True)
    other = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL)
    procs[other.pid] = other
    time.sleep(0.3)
    check("argv[0] 不是白名單裡的 → True 且不送訊號", _kill(other.pid) is True)
    check("它還活著（沒有被誤殺）", other.poll() is None)

finally:
    for p in procs.values():
        try:
            os.kill(p.pid, 9)
        except OSError:
            pass
        try:
            p.wait(timeout=2)
        except Exception:
            pass

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
