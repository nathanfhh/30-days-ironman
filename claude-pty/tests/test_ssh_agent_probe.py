"""🔴 `ssh-add -l` 的三種結束碼要講三種話。

    uv run python tests/test_ssh_agent_probe.py

不需要 docker，也不需要真的 ssh-agent：拿一支假的 `ssh-add` 放進 PATH 最前面，
讓它回指定的結束碼，再看 run wrapper 印出什麼。

## 為什麼要有這一支

`ssh-add(1)` 明定三種結束碼：

    0 = 袋子裡有金鑰
    1 = 連得上 agent，但袋子是空的
    2 = **連不到 agent**

run wrapper 的註解本來就寫著這三種，但程式碼只分了兩類（`if ssh-add -l; then … else …`），
把 1 與 2 併成同一句「agent 裡沒有任何金鑰，去跑 ssh-add」。

⚠ **錯得很難查**：socket 失效、權限不對、`SSH_AUTH_SOCK` 指到一個已經消失的路徑
（重開機或換登入 session 之後很常見）——這些全都是 2，卻會被告知「袋子是空的」。
使用者照著去跑 `ssh-add`，那個指令也連不上，於是卡在一個與真正原因無關的地方。

⚠ 這一支釘的是**訊息要能指路**，不是字面。所以斷言的是「三種情況的輸出彼此不同、
而且 2 的那一則帶出了 ssh-add 自己的原始訊息」——原始訊息才是指得到原因的那句話。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠ 兩個 repo 的 run wrapper 檔名不同（公開版 run-ncr-…、閉源版 run-claude-code-…）。
#   自動找，不要寫死——寫死的那一邊會安靜地 SKIP，而 SKIP 看起來跟通過一樣。
_DC = os.path.join(_HERE, "..", "..", "dev-container")
_WRAPPER = next((os.path.join(_DC, n) for n in
                 ("run-ncr-dev-container.sh", "run-claude-code-dev-container.sh")
                 if os.path.isfile(os.path.join(_DC, n))), "")

_pass = _fail = 0


def check(label: str, ok: bool) -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}")


if not _WRAPPER:
    print("  SKIP  找不到 dev-container 的 run wrapper")
    sys.exit(0)

SRC = open(_WRAPPER, encoding="utf-8").read()

# 抽出那個判斷區塊來跑。
# ⚠ 用**原始碼裡的那一段**，不是在這裡重寫一份等價的邏輯——重寫的話這支測的是我自己
#   寫的副本，wrapper 改了它也不會紅，那正是這種測試最常見的失效方式。
_START = "_ssh_add_err=\"$(ssh-add -l 2>&1 >/dev/null)\""
if _START not in SRC:
    print("  FAIL  找不到 ssh-add 判斷區塊（wrapper 改了形狀？）")
    sys.exit(1)
_i = SRC.index(_START)
_j = SRC.index("\n  fi\n", _i) if "\n  fi\n" in SRC[_i:_i + 2000] else SRC.index("\n    fi\n", _i)
BLOCK = SRC[_i:_j] + "\n    fi\n"
BLOCK = "\n".join(ln[4:] if ln.startswith("    ") else ln.lstrip()
                  for ln in BLOCK.splitlines())


def run_with(rc: int, stderr_msg: str) -> str:
    """把假的 ssh-add 放進 PATH 最前面，跑那一段，回傳它印的東西。"""
    with tempfile.TemporaryDirectory() as d:
        fake = os.path.join(d, "ssh-add")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/bash\n[ -n "{stderr_msg}" ] && echo "{stderr_msg}" >&2\nexit {rc}\n')
        os.chmod(fake, 0o755)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"],
                   SSH_AUTH_SOCK="/tmp/probe.sock")
        r = subprocess.run(["bash", "-c", BLOCK], env=env,
                           capture_output=True, text=True, timeout=20)
        return r.stdout + r.stderr


print("== 三種結束碼要走三條不同的路 ==")
out0 = run_with(0, "")
out1 = run_with(1, "The agent has no identities.")
out2 = run_with(2, "Error connecting to agent: No such file or directory")

check("0（有金鑰）不抱怨", "⚠️" not in out0)
check("1（袋子空的）說的是「沒有任何金鑰」",
      "沒有任何金鑰" in out1)
check("🔴 2（連不到）**不會**被說成「沒有任何金鑰」",
      "沒有任何金鑰" not in out2)
check("🔴 2 明講是連不到 agent", "連不到" in out2)
check("🔴 2 帶出 ssh-add 自己的原始訊息（那句話才指得到原因）",
      "Error connecting to agent" in out2)
check("🔴 1 與 2 的輸出確實不同（併成同一句正是這支要防的事）",
      out1.strip() != out2.strip())
check("2 也把結束碼講出來（rc=2）", "rc=2" in out2)

print()
print("== 原始碼層級：不可以退回「非 0 就當成沒金鑰」==")
# ⚠ 這一條擋的是**回歸的具體寫法**。行為斷言在上面，這裡釘的是那個誘人的簡寫
#   （`if ssh-add -l; then … else …`）不要再出現。
# ⚠ 釘的是「rc 有被存下來、而且 1 是自己一條分支」。不比對完整的 if/elif 形狀——
#   那會讓任何合理的重構都紅，而重構不是回歸。真正的回歸是「非 0 全部併成一句」，
#   那種寫法一定不會出現 `-eq 1`。
check("🔴 wrapper 把 rc 存下來（而不是直接 if ssh-add -l）",
      "_ssh_add_rc=$?" in SRC)
check("🔴 rc=1 是自己一條分支（非 0 全部併一句的話不會有這個）",
      '_ssh_add_rc" -eq 1' in SRC)
check("🔴 原始錯誤訊息有被捕捉下來（2 的那則要帶出它）",
      "_ssh_add_err=" in SRC)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
