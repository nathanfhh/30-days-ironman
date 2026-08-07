"""驗證：dev-container/entrypoint.sh 的**人類互動路徑**未被 claude-pty 的改動影響。

run script 走的是 image 內的 entrypoint.sh。這裡把 repo 最新版（含今天所有改動）
bind-mount 進去，等同於「rebuild image 之後」的情況，再用真 PTY 逐項回答選單：

  1. 選單照舊出現，且不設任何 NCR_* env 時完全走互動分支
  2. 畫面上不該出現就緒標記（那是給控制平面看的機器標記，只有 NCR_MARK=1 才印）
  3. 最終真的進到 CLI

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
            f.write("{}")      # 檔案必須存在，否則 docker 會建成目錄而非檔案
    return tmp


SANDBOX = _sandboxed_home()

argv = [
    "run", "--rm", "-it", "--name", NAME,
    # 關鍵：掛 repo 的 entrypoint.sh ＝ 模擬 rebuild image 之後的人類路徑
    "-v", f"{REPO}/dev-container/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro",
    # 憑證是**副本**（見 _sandboxed_home）：容器內的 claude 怎麼寫都不會動到使用者的憑證
    "-v", f"{SANDBOX}/.claude:/home/nathan/.claude",
    "-v", f"{SANDBOX}/.claude.json:/home/nathan/.claude.json",
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
    child.sendline("2")                      # 選 unrestricted（測試不依賴白名單那條路）

    # 2) 流量錄製
    child.expect("錄製本場流量")
    check("② 流量錄製選單出現", True)
    child.expect(r"錄製流量\? \[y/N\]:")
    child.sendline("n")                      # 不錄製（測試不依賴 mitm addon）

    # 3) telemetry 只有在 OTEL_EXPORTER_OTLP_ENDPOINT 有值時才問
    idx = child.expect(["送 Jaeger\\? \\[Y/n\\]:", "網路能力：完全開放", pexpect.TIMEOUT], timeout=30)
    if idx == 0:
        check("③ telemetry 選單出現（endpoint 有設時）", True)
        child.sendline("n")
    else:
        print("  SKIP  ③ telemetry 選單（未設 OTEL endpoint，照設計不問）")

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
check("⑦ 有印出 firewall/網路能力的結論行", "網路能力" in plain)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}（完整輸出：/tmp/claude-pty-humanpath.log）")
sys.exit(1 if _fails else 0)
