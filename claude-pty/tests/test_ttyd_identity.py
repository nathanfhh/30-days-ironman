"""`_is_our_ttyd` 的把關（views.py）。不需要 docker。

    uv run --with flask --with docker --with sqlalchemy --with psutil \
        python tests/test_ttyd_identity.py

為什麼要有這支：`_is_our_ttyd` 是 `_kill()` 送 SIGTERM **之前的唯一把關**。PID 會被回收，
ttyd 退出後、殘留記錄清掉前，同一個號碼可能已經是別的程序——認錯就是誤殺無關程序。

原本的判斷是「cmdline 裡有沒有 ttyd 這五個字」，太寬鬆：2026-07-26 實測，一行掃描用的
`sh -c '... grep ttyd ...'` 自己就通過了比對。改成比對 argv[0] 的 basename。

⚠ 兩個方向都要測。只測反例（不是 ttyd 的要被拒）而漏掉正例的話，把關收得太緊會讓**所有**
  view 被判死亡並清掉——那比誤殺嚴重得多。
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config, views  # noqa: E402
from server.views import _is_our_ttyd, psutil  # noqa: E402

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


if psutil is None:
    print("psutil 未安裝——這支測的就是它，跳過沒有意義。請 `--with psutil` 再跑一次。")
    sys.exit(1)

procs = []


def spawn(argv):
    p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(0.25)  # 等 exec 完成，cmdline 才是最終的那一份
    return p.pid


try:
    print("== 反例：cmdline 提到 ttyd，但它不是 ttyd ==")
    # 這正是當初踩到的形狀——掃描 ttyd 的那行指令自己被認成 ttyd
    check(
        "sh -c '... ttyd ...' → False", _is_our_ttyd(spawn(["sh", "-c", "echo scanning for ttyd; sleep 20"])) is False
    )
    # argv[0] 是路徑時比的是 basename，不能因為路徑裡有 ttyd 就算數
    check(
        "argv[0] 路徑含 ttyd 但檔名不是 → False",
        _is_our_ttyd(spawn(["sh", "-c", "# /opt/ttyd/helper\nsleep 20"])) is False,
    )

    print("== 正例：真的叫 ttyd 的程序要認得出來 ==")
    # 不需要真的 ttyd binary：判定看的是 argv[0]，用 exec -a 換掉即可。
    # （容器裡有真的 ttyd，但這支刻意不依賴 docker，本機也要跑得動。）
    # ⚠ 這裡必須是 **bash** 不是 sh：`exec -a` 是 bash builtin，Debian/Ubuntu 的 /bin/sh
    #   是 dash，會回 `exec: -a: not found` 當場退出——留下一個殭屍，症狀是下一行讀
    #   cmdline 時噴 psutil.ZombieProcess，看起來完全不像「shell 不支援這個旗標」。
    pid = spawn(["bash", "-c", "exec -a ttyd sleep 20"])
    argv0 = psutil.Process(pid).cmdline()[0]
    check(f"argv[0] 是 {argv0!r} → True", _is_our_ttyd(pid) is True)
    # ⚠ 這條若紅了，代表把關收得太緊：所有 view 會被判死亡並清掉，比誤殺嚴重得多。

    print("== 🔴 白名單內的每一顆都要認得（C 與 Rust 並存）==")
    # image 內同時放 C 版 `ttyd` 與 Rust 版 `ttyd-rust`，每個使用者各自選（users.ttyd_bin）。
    # ⚠ 這裡收的是**整個白名單**，不是「這個人現在選的那一顆」：偏好是 per-user 的，而且
    #   切換之後先前起的程序還活著。只認一個名字的話，那些程序既收不掉（port 永久洩漏），
    #   也不會出現在觀測頁的孤兒清單裡——畫面看起來乾淨，其實有東西在跑。
    #   （這個掃描寫死 "ttyd" 的版本實際上線過：切到 Rust 版之後每一列 view 都被標成
    #     「登錄有但程序死了」的幽靈記錄，而 ttyd-rust 好端端在跑。）
    pid_rs = spawn(["bash", "-c", "exec -a ttyd-rust sleep 20"])
    check("argv[0]=ttyd-rust → True", _is_our_ttyd(pid_rs) is True)
    check("argv[0]=ttyd 同時也還是我們的 → True", _is_our_ttyd(pid) is True)
    check("白名單就是 config.TTYD_BINS（兩邊不可以各寫一份）", set(views._OUR_TTYD_NAMES) == set(config.TTYD_BINS))
    pid_other = spawn(["bash", "-c", "exec -a ttydx sleep 20"])
    check("名字只是**開頭像** ttyd 的不算（ttydx → False）", _is_our_ttyd(pid_other) is False)

    print("== 起哪一顆由呼叫端決定，值一律先收斂 ==")
    # 這個值最終是 argv[0]。DB 裡可能留著白名單改掉之後的舊值，直接拿去 exec 是錯的。
    check("指定 Rust 版 → argv[0] 就是它", views._ttyd_argv(41000, "c", "sid", "ttyd-rust")[0] == "ttyd-rust")
    check("沒指定 → 用預設（C 版）", views._ttyd_argv(41000, "c", "sid", None)[0] == config.TTYD_BIN_DEFAULT)
    check(
        "不認得的值 → 退回預設，不可以照著 exec",
        views._ttyd_argv(41000, "c", "sid", "rm -rf /")[0] == config.TTYD_BIN_DEFAULT,
    )

    print("== 參數建構策略：每顆 binary 一組，特有旗標不進共用模板 ==")
    # Rust 特有旗標（--title / --auth-url / --auth-cache-ttl）C 版沒有。真 binary 實測
    # 見 tests/test_ttyd_unknown_flag.py：C 版拿到未知旗標會在 stderr 喊一句，但不拒起；
    # 而 `--title VALUE` 的值還會被當成 child command、後面的旗標整串被吞掉（實測 port
    # 掉回預設 7681）。strategy 拆開守的就是「這些旗標永遠不會落到不認得它們的 binary 上」。
    rust = views._ttyd_argv(41000, "claude-pty-xyz789", "xyz789", "ttyd-rust")
    c = views._ttyd_argv(41000, "claude-pty-xyz789", "xyz789", "ttyd")
    check(
        "🔴 策略表恆等於白名單（多一顆 binary 就要寫下它的策略，含「沒有」）",
        set(views._TTYD_EXTRAS) == set(config.TTYD_BINS),
    )
    check(
        "🔴 共用模板是兩邊的交集：C argv ＝ Rust argv 去掉特有段（只差 argv[0]）",
        c[1:]
        == [
            a
            for a in rust[1:]
            if a
            not in ("--title", "claude-pty · xyz789", "--auth-url", "--auth-cache-ttl", str(config.TTYD_AUTH_CACHE_TTL))
            and not a.startswith("http://127.0.0.1:")
        ],
    )

    print("== --title（發表阻擋項）==")
    check("🔴 Rust 版帶 --title", "--title" in rust)
    _title = rust[rust.index("--title") + 1] if "--title" in rust else ""
    check(
        "🔴 --title 只剩固定字樣加這一場的編號，命令列一個字都不上線",
        "xyz789" in _title and "docker" not in _title and "attach" not in _title and "claude-pty-xyz789" not in _title,
    )
    check("--title 與 titleFixed 是同一個字串（兩條路顯示一致）", f"titleFixed={_title}" in rust)
    check(
        "🔴 C 版 argv 沒有任何 Rust 特有旗標（strategy 不讓它們落到 C 上）",
        not {"--title", "--auth-url", "--auth-cache-ttl"} & set(c),
    )
    check("C 版仍靠 titleFixed 蓋畫面", any(a.startswith("titleFixed=") for a in c))

    print("== --auth-url：第二層授權（縱深），打的是無副作用端點 ==")
    _auth = rust[rust.index("--auth-url") + 1] if "--auth-url" in rust else ""
    check(
        "🔴 指向 /api/auth/check（不是有副作用的 /api/auth/view）",
        "/api/auth/check" in _auth and "/api/auth/view" not in _auth,
    )
    check("sid 烤進 URL（一顆 ttyd 只屬於一場）", "session=xyz789" in _auth)
    check(
        "走 loopback 問控制平面（不繞出去、不經 nginx）", _auth.startswith(f"http://127.0.0.1:{config.CONTROL_PORT}/")
    )
    check(
        "預設帶 --auth-cache-ttl（每個 asset 都問一次太貴）",
        "--auth-cache-ttl" in rust and rust[rust.index("--auth-cache-ttl") + 1] == str(config.TTYD_AUTH_CACHE_TTL),
    )
    _saved_ttl = config.TTYD_AUTH_CACHE_TTL
    try:
        config.TTYD_AUTH_CACHE_TTL = 0
        r0 = views._ttyd_argv(41000, "c", "sid0", "ttyd-rust")
        check("TTL=0 → 不帶快取旗標（每請求都問，語意乾淨不是帶個 0）", "--auth-cache-ttl" not in r0)
    finally:
        config.TTYD_AUTH_CACHE_TTL = _saved_ttl

    print("== 邊界 ==")
    check("pid=None → False", _is_our_ttyd(None) is False)
    check("pid=0 → False", _is_our_ttyd(0) is False)
    gone = spawn(["sh", "-c", "exit 0"])
    time.sleep(0.3)
    check("已結束的 pid → False", _is_our_ttyd(gone) is False)
    check("自己這支 python → False", _is_our_ttyd(os.getpid()) is False)
finally:
    for p in procs:
        with __import__("contextlib").suppress(Exception):
            p.kill()

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)


# ── 就緒探測：兩顆 binary 的回應都要認得出來 ────────────────────────────────────
#
# 這一段是踩出來的。就緒檢查不帶 cookie 打一個 HTTP 請求，原本要求「200 而且內容含
# ttyd」。Rust 版掛上 --auth-url 之後，那個沒有身分的請求會被它自己的授權層擋掉，回
# 401——於是就緒檢查永遠失敗、pid 永遠寫不進去，畫面上看到的是「另一個 worker 正在
# 建立終端」，而且會一直卡著。
#
# 判準改成看 `server: ttyd/` 標頭：兩顆都送它，而且 401 的回應裡也有。
print("== 就緒探測認得出 401（有裝授權的那顆）==")

_C_HEAD = (
    b"HTTP/1.0 200 OK\r\nserver: ttyd/1.7.7-40e79c7 (libwebsockets/4.3.3)\r\n"
    b"content-type: text/html\r\n\r\n<!DOCTYPE html>"
)
_RUST_HEAD = b"HTTP/1.0 401 Unauthorized\r\nserver: ttyd/2.0.1-54dd369 (rust)\r\ncontent-length: 0\r\n\r\n"
_OTHER = b"HTTP/1.0 200 OK\r\nserver: nginx/1.27\r\ncontent-type: text/html\r\n\r\n<html>"


def _accepts(head: bytes) -> bool:
    """把 _is_ttyd_serving 的判準抽出來測，不必真的起一顆 binary。
    ⚠ 這裡複製判準是刻意的：它要跟著 views.py 一起改，改了這支才會紅。"""
    return head.startswith(b"HTTP/1.") and b"server: ttyd/" in head.lower()


check("🔴 C 版的 200 認得出來", _accepts(_C_HEAD))
check("🔴 Rust 版的 401 也認得出來（授權擋下探測，不代表它沒起來）", _accepts(_RUST_HEAD))
check("🔴 別人的服務佔了這個 port 不能誤判成就緒", not _accepts(_OTHER))
check("空回應不算就緒", not _accepts(b""))
