"""C 版 ttyd 拿到它不認得的旗標會怎樣，**需要 host 上有 ttyd binary**（不需要 docker）。

    uv run python tests/test_ttyd_unknown_flag.py

為什麼要有這支：`views._TTYD_EXTRAS` 替每顆 binary 分開組參數，理由一直只寫在註解裡，
而且寫錯了（原本說 C 版「靜默忽略照跑」）。這支對著**真的 C binary**（1.7.7）量出實情：

1. 未知旗標**有喊**：`getopt_long` 在第二輪解析時 `opterr` 是 1，stderr 會出現
   `unrecognized option`。所以不是無聲。
2. 但**喊完照樣起來**：`case '?'` 只有 `break`（src/server.c），不是 `return -1`，
   所以警告不等於拒起，而且它真的開始聽我們指定的 port。
3. **帶值的旗標更歪**。C 版不知道 `--title` 吃一個值，`calc_command_start()` 因此
   停在那個值上，ttyd 把它當成要執行的 **child command**，後面每一個真正的旗標
   （這裡是 `-p`）都被吞進那個 command，一個都沒被解析——**我們指定的 port 沒有人在聽**。
   不是「終端照常開，只是少一層保護」。

而 `_spawn_detached` 把 ttyd 的 stdio 全部導向 /dev/null（繼承那條 PIPE 寫爆會卡住），
所以第 1 點那行警告在這套系統裡沒有任何人讀得到。三件事合起來就是拆 strategy 的理由：
不能靠「塞錯會壞」防呆，要靠「旗標永遠不落到不認得它的 binary 上」（見 _TTYD_EXTRAS）。

⚠ 斷言只押可攜的那一半。`-p` 被吞掉之後 ttyd 會退回它的預設 7681（2026-08-29 在
  claude-pty-control image 裡實測到 `[vh|1|default||7681]`），但 7681 在跑測試的機器上
  可能被別人佔著，所以這裡只把它印出來、不當斷言；硬押的是「指定的 port 沒有人在聽」。
⚠ getopt 的訊息格式**跨平台不同**（glibc 是 `unrecognized option: title`，macOS 的
  BSD 版是 ``unrecognized option `--title'``），所以比對的是「同一行裡同時有
  `unrecognized option` 與旗標名」，不逐字比對整句。
⚠ 刻意不進 `NEEDS_DOCKER`：它只是在 host 上跑一支 binary 兩秒，一個容器都不起。
  CI 的 dev-container job 會裝與 deploy/Dockerfile 同版本同雜湊的 C 版 ttyd，
  所以那個 job 上這支是**真的跑**，不是跳過。
"""

import shutil
import socket
import subprocess
import sys
import time

TTYD = shutil.which("ttyd")
DEFAULT_PORT = 7681  # ttyd 自己的預設，也就是旗標被吞掉之後它會退回的那個

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


if TTYD is None:
    print("SKIP：host 上沒有 ttyd binary")
    sys.exit(0)


def free_port() -> int:
    """借一個當下沒人用的 port。ttyd 綁不上去會直接退出，那會讓下面的斷言錯得莫名其妙。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_ttyd(*args: str, probe: int | None = None, secs: float = 1.5) -> tuple[str, bool, bool]:
    """跑真的 ttyd，回傳 (輸出, 指定的 port 有沒有人聽, 預設 port 有沒有人聽)。

    ttyd 起來就不會自己結束，所以用 Popen 起、量完再收——**不用 `timeout(1)`**，
    macOS 預設沒有那支。
    """
    p = subprocess.Popen([TTYD, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    hit = dflt = False
    try:
        time.sleep(secs)
        if probe is not None:
            hit = listening(probe)
            dflt = listening(DEFAULT_PORT)
    finally:
        p.terminate()
        try:
            out = p.communicate(timeout=3)[0]
        except subprocess.TimeoutExpired:
            p.kill()
            out = p.communicate()[0]
    return out or "", hit, dflt


def warned_about(out: str, flag: str) -> bool:
    """同一行裡同時出現 unrecognized option 與那支旗標的名字（避開跨平台的措辭差異）。"""
    return any("unrecognized option" in ln and flag in ln for ln in out.splitlines())


print(f"== 前提：host 上的 C 版 ttyd（{TTYD}）==")
_ver = subprocess.run([TTYD, "--version"], capture_output=True, text=True, timeout=10).stdout
check("拿得到版本（沒有這行，下面兩段的證據都不算數）", "ttyd version" in _ver)
print(f"    {_ver.strip() or '<無輸出>'}")

_port = free_port()

print("== A：不帶值的未知旗標 → 有喊，但不拒起 ==")
_a, _a_hit, _ = run_ttyd("--nonexistent-flag", "-p", str(_port), "bash", probe=_port)
check("🔴 有喊（不是靜默忽略）", warned_about(_a, "nonexistent-flag"))
check("🔴 但沒有因此退出，server 照樣起來", "start command: bash" in _a)
check(f"🔴 而且其餘旗標照常解析：{_port} 真的有人在聽", _a_hit)

print("== B：帶值的未知旗標（--title VALUE）→ 值被當成 child command ==")
_b, _b_hit, _b_dflt = run_ttyd("--title", "FOO", "-p", str(_port), "bash", probe=_port)
check("🔴 一樣有喊", warned_about(_b, "title"))
check("🔴 值被當成要執行的 child command", "start command: FOO" in _b)
check("🔴 後面的旗標整串被吞進那個 command（一個都沒被解析）", f"start command: FOO -p {_port}" in _b)
check(f"🔴 於是 {_port} 沒有人在聽——不是「照常開、只是少一層保護」", not _b_hit)
print(f"    （附帶觀察，不當斷言）預設 port {DEFAULT_PORT} 有人在聽：{_b_dflt}")

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
