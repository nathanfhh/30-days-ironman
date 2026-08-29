"""C 版 ttyd 拿到它不認得的旗標會怎樣，**需要 docker 與 build 好的 control image**。

    uv run python tests/test_ttyd_unknown_flag.py

為什麼要有這支：`views._TTYD_EXTRAS` 替每顆 binary 分開組參數，理由一直只寫在註解裡，
而且寫錯了（原本說 C 版「靜默忽略照跑」）。這支對著**真的 C binary**（1.7.7）量出實情：

1. 未知旗標**有喊**：`getopt_long` 在第二輪解析時 `opterr` 是 1，stderr 會出現
   `ttyd: unrecognized option: …`。所以不是無聲。
2. 但**喊完照樣起來**：`case '?'` 只有 `break`（src/server.c），不是 `return -1`，
   所以警告不等於拒起。
3. **帶值的旗標更糟**。C 版不知道 `--title` 吃一個值，`calc_command_start()` 因此
   停在那個值上，ttyd 把它當成要執行的 **child command**，後面每一個真正的旗標
   （`-p` / `-i`）都被吞進那個 command，一個都沒被解析——結果是 port 掉回預設 7681、
   介面沒綁。不是「終端照常開，只是少一層保護」。

而 `_spawn_detached` 把 ttyd 的 stdio 全部導向 /dev/null（繼承那條 PIPE 寫爆會卡住），
所以第 1 點那行警告在這套系統裡沒有任何人讀得到。三件事合起來就是拆 strategy 的理由：
不能靠「塞錯會壞」防呆，要靠「旗標永遠不落到不認得它的 binary 上」（見 _TTYD_EXTRAS）。

⚠ 這支跑的是真 binary，不是我們自己寫的字串。fork 哪天改了 getopt 的處理，這裡會紅。
"""

import os
import re
import shlex
import subprocess
import sys

IMAGE = os.environ.get("CLAUDE_PTY_CONTROL_IMAGE", "claude-pty-control:latest")

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def run(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


if run("docker", "version").returncode != 0:
    print("SKIP：docker 不可用")
    sys.exit(0)
if run("docker", "image", "inspect", IMAGE).returncode != 0:
    print(f"SKIP：找不到 image {IMAGE}（先 docker compose build control）")
    sys.exit(0)


def ttyd(*args: str, secs: int = 2) -> str:
    """在 image 裡跑真的 C 版 ttyd，回傳合併後的輸出。

    ttyd 起來就不會自己結束，所以用 `timeout` 收；它印完啟動日誌才被砍，那些日誌
    正是我們要的證據（`start command:` 與 lws 的 vhost 行）。
    """
    cmd = "timeout %d ttyd %s 2>&1" % (secs, " ".join(shlex.quote(a) for a in args))
    p = run("docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c", cmd)
    return p.stdout + p.stderr


def vhost(out: str) -> str:
    """從 lws 的 `[vh|1|default|iface|iface|port]` 抽出「介面與 port」那一段。"""
    m = re.search(r"\[vh\|1\|default\|([^\]]*)\]", out)
    return m.group(1) if m else "<找不到 vhost 行>"


print("== 前提：image 裡的 C 版 ttyd 起得來 ==")
_ver = ttyd("--version", secs=5)
check("拿得到版本（沒有這行，下面兩段的證據都不算數）", "ttyd version" in _ver)
print(f"    {_ver.strip().splitlines()[0] if _ver.strip() else '<無輸出>'}")

print("== A：不帶值的未知旗標 → 有喊，但不拒起 ==")
_a = ttyd("--nonexistent-flag", "-p", "7999", "-i", "lo", "bash")
check("🔴 stderr 有喊（不是靜默忽略）", "unrecognized option" in _a)
check("🔴 但沒有因此退出，server 照樣起來", "start command: bash" in _a)
check("其餘旗標仍照常解析（綁到我們指定的 lo:7999）", vhost(_a).endswith("7999"))
print(f"    vhost = {vhost(_a)}")

print("== B：帶值的未知旗標（--title VALUE）→ 值被當成 child command ==")
_b = ttyd("--title", "FOO", "-p", "7999", "-i", "lo", "bash")
check("🔴 一樣有喊", "unrecognized option: title" in _b)
check("🔴 值被當成要執行的 child command", "start command: FOO" in _b)
check("🔴 後面的旗標整串被吞進那個 command（一個都沒被解析）", "start command: FOO -p 7999" in _b)
check("🔴 於是 port 掉回預設 7681、介面沒綁", vhost(_b).endswith("7681") and "lo" not in vhost(_b))
print(f"    vhost = {vhost(_b)}")

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
