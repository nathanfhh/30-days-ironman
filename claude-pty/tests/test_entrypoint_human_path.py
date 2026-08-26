"""驗證：dev-container/entrypoint.sh 的**人類互動路徑**未被 claude-pty 的改動影響。

run script 走的是 image 內的 entrypoint.sh。這裡把 repo 最新版（含今天所有改動）
bind-mount 進去，等同於「rebuild image 之後」的情況，再用真 PTY 逐項回答選單：

  1. 選單照舊出現，且不設任何 NCR_* env 時完全走互動分支
  2. 畫面上不該出現就緒標記（那是給控制平面看的機器標記，只有 NCR_MARK=1 才印）
  3. 最終真的進到 CLI

第二段（`NCR_MITM_WEB_PASSWORD` 上線後補的，ADR 0021）走**錄製那一條分支**：
控制平面那條路會把 mitmweb 的密碼指定進去，而人自己開容器時**不設它**。
所以這裡照 run script 的形狀起容器（掛 repo 的 mitm/、帶 NCR_MITM_WEB_PORT 與
NCR_CAPTURE_HOST_DIR），錄製選 y，斷言那行帶 token 的即時畫面 URL **逐字沒變**、
token 仍是現產的 24 字元亂數。

⚠ 為什麼一定要有這一段：第一段錄製選的是 n，`start_capture` 根本不會被執行到，
  於是它守不到那個函式裡的任何一行。改動落在那裡而測試在別處，就是那種「全綠但沒測到」。

零 token：進到 Claude Code 的畫面就停手，不送任何 prompt。
"""

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pexpect

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# dev-container 建出來的 image（`dev-container/run-ncr-dev-container.sh` 的 IMAGE）。
IMAGE = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
NAME = "claude-pty-humanpath-test"

# ⚠ **從 server 端 import，不要在這裡抄一份字串**。這個標記是控制平面與 entrypoint
#   之間的約定，抄一份的話改名時這支會繼續比對舊字串——而它比對的是「不該出現」，
#   舊字串永遠不出現，所以**測試會一直綠、而且是假的**。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.sessions import DRIVER_MARKER as MARKER  # noqa: E402

for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(v, None)

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


def _sandboxed_home() -> str:
    """複製一份憑證到暫存目錄，讓容器去寫那一份，**絕不掛使用者真正的 ~/.claude**。

    ⚠ 這不是潔癖，是踩過的坑（2026-07-25）：原本直接以可寫方式掛 `~/.claude`，而這個
    測試會啟動**真的** claude CLI。CLI 起來後會去 refresh OAuth token，測試隨即把容器
    砍掉——`.credentials.json` 就被留在「token 已清空」的狀態，host 上的 Claude Code 跟著
    顯示 login expired，只能重新登入一次。

    測試對使用者環境的破壞性副作用永遠不可接受，即使機率很低：跑測試的人不會預期
    自己因此被登出，而且症狀出現時離原因已經很遠了。
    """
    tmp = tempfile.mkdtemp(prefix="claude-pty-humanpath-home-")
    atexit.register(shutil.rmtree, tmp, True)
    home = os.path.expanduser("~")
    dst = os.path.join(tmp, ".claude")
    os.makedirs(dst, exist_ok=True)
    # 只帶「能登入並進到畫面」所需的最小集合；對話歷史等其餘內容讓容器自己建。
    for name in (".credentials.json", "settings.json"):
        src = os.path.join(home, ".claude", name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dst, name))
    cfg, src_cfg = os.path.join(tmp, ".claude.json"), os.path.join(home, ".claude.json")
    if os.path.isfile(src_cfg):
        shutil.copy2(src_cfg, cfg)
    else:
        with open(cfg, "w") as f:
            f.write("{}")  # 檔案必須存在，否則 docker 會建成目錄而非檔案
    return tmp


SANDBOX = _sandboxed_home()

argv = [
    "run",
    "--rm",
    "-it",
    "--name",
    NAME,
    # 關鍵：掛 repo 的 entrypoint.sh ＝ 模擬 rebuild image 之後的人類路徑
    "-v",
    f"{REPO}/dev-container/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro",
    # 憑證是**副本**（見 _sandboxed_home）：容器內的 claude 怎麼寫都不會動到使用者的憑證
    "-v",
    f"{SANDBOX}/.claude:/home/nathan/.claude",
    "-v",
    f"{SANDBOX}/.claude.json:/home/nathan/.claude.json",
    IMAGE,
]

print("== 起容器（走 image entrypoint，不設任何 NCR_* env）==")
child = pexpect.spawn("docker", argv, encoding="utf-8", timeout=120, dimensions=(40, 140))
transcript = []
child.logfile_read = type("W", (), {"write": transcript.append, "flush": lambda self: None})()

try:
    # 1) 網路能力選單
    child.expect("網路能力")
    check("① 網路能力選單出現", True)
    child.expect(r"請選擇 \[1\]:")
    child.sendline("2")  # 選 unrestricted（測試不依賴白名單那條路）

    # 2) 流量錄製
    child.expect("錄製本場流量")
    check("② 流量錄製選單出現", True)
    child.expect(r"錄製流量\? \[y/N\]:")
    child.sendline("n")  # 不錄製（測試不依賴 mitm addon）

    # 3) telemetry 只有在 OTEL_EXPORTER_OTLP_ENDPOINT 有值時才問
    #
    # ⚠ **「沒問」的哨兵必須是 telemetry 那一段之後才印的東西。** 這裡原本用的是
    #   `網路能力：完全開放`（entrypoint.sh L426），而它印在**錄製選單之前**：上面那句
    #   `child.expect("錄製本場流量")` 早就把它連同前面的畫面一起吃掉了，所以這個分支
    #   **永遠不可能成立**。於是每一台沒設 OTEL endpoint 的機器都在這裡默默付一次 30 秒
    #   逾時，然後把逾時報成一行「照設計不問」的 SKIP（2026-08-27 在開發機上量到：
    #   那 30 秒佔了第一段的大半，而畫面上完全看不出來）。
    #   `● session id` 是 entrypoint.sh L551/L553 印的，位置在 telemetry 那一段**之後**、
    #   CLI 啟動之前，兩種寫法（自產／呼叫端指定）都吃得到，是這裡唯一站得住的哨兵。
    idx = child.expect([r"送 Jaeger\? \[Y/n\]:", "● session id", pexpect.TIMEOUT], timeout=30)
    if idx == 0:
        check("③ telemetry 選單出現（endpoint 有設時）", True)
        child.sendline("n")
    elif idx == 1:
        # 沒設 OTEL endpoint 就直接走到 session id 那一行，照設計不問。**正當的跳過**。
        print("  SKIP  ③ telemetry 選單（未設 OTEL endpoint，照設計不問）")
    else:
        # 🔴 **逾時不可以跟「照設計不問」共用同一個分支。** 兩者的意思相反：一個是
        #    「畫面上出現了該出現的東西，只是不是選單」，另一個是「三十秒內什麼都沒印」。
        #    落下去的話，容器卡在更前面（entrypoint 掛了、image 壞了）會被記成一行 SKIP，
        #    而下面 ⑤⑥⑦ 那幾條比對的是一份半截的畫面：`MARKER not in text` 與
        #    `"非互動" not in text` 這種**否定式**斷言，畫面越少越容易綠。
        #    （同一個錯在第二段的 Jaeger 那步修過了，這裡是第一段漏掉的那一個。）
        check("🔴 ③ 三十秒內連結論行都沒印出來（下面幾條會對著半截畫面比對）", False)

    # 進到 CLI：等 Claude Code 的畫面元素
    child.expect(["bypass permissions", "Claude Code", "for shortcuts"], timeout=120)
    check("④ 最終進到 CLI 畫面", True)
finally:
    with open("/tmp/claude-pty-humanpath.log", "w") as f:
        f.write("".join(transcript))  # 供人工比對畫面
    child.close(force=True)
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)

text = "".join(transcript)
check("⑤ 畫面上沒有機器用的就緒標記（沒設 NCR_MARK）", MARKER not in text)
# env-skip 的提示只在非互動時印；人類路徑不該看到
check("⑥ 沒有出現非互動模式的提示（● 非互動 …）", "非互動" not in text)
plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
# ⚠ 比對要帶項目符號。`網路能力` 這四個字在選單標題 `echo "網路能力："` 就出現過
#   （entrypoint.sh，而且第 99 行的 expect 已經吃過它），所以只比字串的話，把兩個
#   真正的結論行（`● 網路能力：完全開放 …` / `● 網路能力：限制白名單 …`）整個刪掉
#   也不會紅——操作者就此失去畫面上唯一一句「這一場實際套到哪種模式」（審查 F-021）。
check("⑦ 有印出 firewall/網路能力的結論行", "● 網路能力：" in plain)


# --- 第二段：錄製那一條分支（start_capture 真的被執行到）-------------------------
#
# 這一段用 stub claude（`tests/stub_claude.sh`）而不是真的 CLI：要看的東西全部在
# driver 啟動**之前**就印完了，換成 stub 只是讓尾巴確定、跑得快，不影響任何一條斷言。
print("\n== 起容器（錄製選 y，照 run script 的形狀；不設 NCR_MARK、不設 NCR_MITM_WEB_PASSWORD）==")
CAP_NAME = "claude-pty-humanpath-capture"
WEB_PORT = "40099"  # run script 會動態挑一個；這裡釘死一個值才驗得到 URL 逐字
# （不會佔用 host 上的這個 port，見下面 cap_argv 裡不帶 `-p` 的理由）
STUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stub_claude.sh")
os.chmod(STUB, 0o755)
subprocess.run(["docker", "rm", "-f", CAP_NAME], capture_output=True)

cap_home = tempfile.mkdtemp(prefix="claude-pty-humanpath-cap-")
atexit.register(shutil.rmtree, cap_home, True)
os.makedirs(os.path.join(cap_home, ".claude"), exist_ok=True)
with open(os.path.join(cap_home, ".claude.json"), "w") as f:
    f.write("{}")

cap_argv = [
    "run",
    "--rm",
    "-it",
    "--name",
    CAP_NAME,
    # 這兩項照 dev-container/run-ncr-dev-container.sh：告訴 entrypoint host 視角的
    # port 與落盤目錄。它們只影響**印出來的那行字**，而那正是這一段要比對的東西。
    #
    # ⚠ **刻意不帶 `-p`。** run script 會把 8081 發布到 host，但這一段驗的是
    #   「那行 URL 印對了沒有」，不是「轉發通不通」，而 `-p` 會讓這支測試佔一個
    #   寫死的 host port：那台機器上剛好有人在用 40099（或兩份測試同時跑）的話，
    #   容器起不來，而失敗訊息會是 docker 的 port 已被占用，看起來與 entrypoint
    #   一點關係都沒有。不轉發就沒有這個外部相依。
    "-e",
    f"NCR_MITM_WEB_PORT={WEB_PORT}",
    "-e",
    f"NCR_CAPTURE_HOST_DIR={cap_home}/ncr/mitm",
    "-v",
    f"{REPO}/dev-container/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro",
    # 沒有脫敏 addon 的話 start_capture 會 fail-closed 直接不錄，這一段就白跑了
    "-v",
    f"{REPO}/mitm:/home/nathan/ncr-mitm:ro",
    "-v",
    f"{STUB}:/home/nathan/.local/bin/claude:ro",
    "-v",
    f"{cap_home}/.claude:/home/nathan/.claude",
    "-v",
    f"{cap_home}/.claude.json:/home/nathan/.claude.json",
    IMAGE,
]

cap_child = pexpect.spawn("docker", cap_argv, encoding="utf-8", timeout=180, dimensions=(40, 140))
cap_lines: list[str] = []
cap_child.logfile_read = type("W", (), {"write": cap_lines.append, "flush": lambda self: None})()
try:
    cap_child.expect(r"請選擇 \[1\]:")
    cap_child.sendline("2")  # 網路能力：完全開放（這一段不驗防火牆）
    cap_child.expect(r"錄製流量\? \[y/N\]:")
    cap_child.sendline("y")  # ← 與第一段的差別就在這裡
    cap_child.expect(r"請選擇 \[1\]:")
    cap_child.sendline("1")  # 錄製範圍：全部流量
    idx = cap_child.expect([r"送 Jaeger\? \[Y/n\]:", "REACHED-DRIVER-LAUNCH", pexpect.TIMEOUT], timeout=120)
    if idx == 0:
        cap_child.sendline("n")
        cap_child.expect("REACHED-DRIVER-LAUNCH", timeout=150)
    elif idx == 2:
        # 🔴 **逾時不可以靜默落下。** 落下去的話下面那幾條會對著一份**半截的**畫面比對，
        #    而「還沒印到」與「印錯了」在那些斷言上長得一模一樣：容器根本沒走到 driver
        #    啟動，測試卻可能因為前面幾行剛好都在而全綠。
        check("🔴 ⑦½ 等到 driver 啟動（逾時＝下面幾條比對的是半截畫面）", False)
finally:
    cap_child.close(force=True)
    subprocess.run(["docker", "rm", "-f", CAP_NAME], capture_output=True)

cap_text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", "".join(cap_lines)).replace("\r", "")
with open("/tmp/claude-pty-humanpath-capture.log", "w") as f:
    f.write(cap_text)

check("⑧ 錄製真的開起來了（不是 fail-closed 跳過，否則下面幾條驗不到東西）", "● 錄製中 →" in cap_text)
# 🔴 **逐字**比對那兩行。控制平面那條路把它們 gate 在 NCR_MARK 上了，人這條路一個字都不能變。
_m = re.search(r"● 即時畫面 → http://localhost:(\d+)/\?token=(\S+)", cap_text)
check("🔴 ⑨ 帶 token 的即時畫面 URL 還在，形狀沒變", _m is not None)
check("　└ port 是 run script 傳進來的那個（NCR_MITM_WEB_PORT）", _m is not None and _m.group(1) == WEB_PORT)
_tok = _m.group(2) if _m else ""
# 沒有設 NCR_MITM_WEB_PASSWORD，所以這裡必須是 entrypoint 自己現產的那一串。
check("🔴 ⑩ token 仍是現產的 24 字元英數亂數", len(_tok) == 24 and _tok.isalnum())
check(
    "🔴 ⑪ 後面那句說明也逐字沒變",
    "  （畫面上是未脫敏的即時內容，host 側只綁本機且要 token；落地的是脫敏版）" in cap_text,
)
check("⑫ 這條路上仍然沒有就緒標記（沒設 NCR_MARK）", MARKER not in cap_text)
check("⑬ 沒有出現非互動模式的提示", "非互動" not in cap_text)

_verdict = "done" if _fails == 0 else f"{_fails} FAILED"
print(f"\n{_verdict}（完整輸出：/tmp/claude-pty-humanpath.log、/tmp/claude-pty-humanpath-capture.log）")
sys.exit(1 if _fails else 0)
