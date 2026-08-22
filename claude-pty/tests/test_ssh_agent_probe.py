"""🔴 `ssh-add -l` 的三種結束碼要講三種話——而且要在 `set -e` 底下還講得出來。

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

## 為什麼要在 `set -euo pipefail` 底下跑

這一支的第一版**是假綠的**：它用 `bash -c BLOCK` 跑，沒有帶上 wrapper 開頭那行
`set -euo pipefail`，也沒有看 subprocess 的結束碼。而第一版的 wrapper 寫的是

    _ssh_add_err="$(ssh-add -l 2>&1 >/dev/null)"; _ssh_add_rc=$?

賦值的結束碼會繼承 command substitution，所以在 `set -e` 下 `ssh-add` 回 1／2 時
bash 就地退出——**三條分支一條都走不到，而且整個 wrapper 死在那裡、容器根本不會啟動**。
少了那行 `set`，測試看到的是一個現實中不存在的執行環境，於是 10 條全綠。

所以這一支現在做兩件以前沒做的事：

1. **在 errexit／nounset／pipefail 底下跑**——與正式 wrapper 同一個契約。
2. **斷言 subprocess 的結束碼是 0**——「有沒有提前退出」本身就是要測的性質，
   而提前退出時 stdout 是空的，只比對字串的斷言會以「字串不在裡面」的形式通過
   （`"沒有任何金鑰" not in out2` 在什麼都沒印的時候也成立）。

⚠ 這一支釘的是**訊息要能指路**，不是字面。所以斷言的是「三種情況的輸出彼此不同、
而且 2 的那一則帶出了 ssh-add 自己的原始訊息」——原始訊息才是指得到原因的那句話。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠ 兩個 repo 的 run wrapper 檔名不同（公開版 run-ncr-…、閉源版 run-claude-code-…）。
#   自動找，不要寫死——寫死的那一邊會安靜地 SKIP，而 SKIP 看起來跟通過一樣。
_DC = os.path.join(_HERE, "..", "..", "dev-container")
_CANDIDATES = ("run-ncr-dev-container.sh", "run-claude-code-dev-container.sh")
_WRAPPER = next((os.path.join(_DC, n) for n in _CANDIDATES if os.path.isfile(os.path.join(_DC, n))), "")

_pass = _fail = 0


def check(label: str, ok: bool) -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}")


# ⚠ 找不到 wrapper **不是** SKIP。SKIP 的正當理由是「這台機器缺某個外部條件」
#   （沒有 docker、沒有網路）；而 wrapper 是這個 repo 自己的檔案，它不見了代表
#   **受測主體消失**——那是最該紅的情況，卻會以綠色的 SKIP 溜過去。
if not _WRAPPER:
    print(f"  FAIL  受測主體不存在：{_DC} 底下找不到 {' 或 '.join(_CANDIDATES)}")
    sys.exit(1)

SRC = pathlib.Path(_WRAPPER).read_text(encoding="utf-8")

# 抽出那個判斷區塊來跑。
# ⚠ 用**原始碼裡的那一段**，不是在這裡重寫一份等價的邏輯——重寫的話這支測的是我自己
#   寫的副本，wrapper 改了它也不會紅，那正是這種測試最常見的失效方式。
# ⚠ 起點釘在 `_ssh_add_rc=0`（而不是 ssh-add 那一行）：rc 的預先歸零是安全捕捉寫法的
#   一部分，漏掉它的話 rc=0 那條路會撞上 nounset。
_LINES = SRC.splitlines()
_start = next((i for i, ln in enumerate(_LINES) if ln.strip() == "_ssh_add_rc=0"), -1)
if _start < 0:
    print("  FAIL  找不到 ssh-add 判斷區塊的起點 `_ssh_add_rc=0`（wrapper 改了形狀？）")
    sys.exit(1)
_indent = len(_LINES[_start]) - len(_LINES[_start].lstrip())
# 終點：同一個縮排層級上的 `fi`。用縮排比對而不是寫死 "\n  fi\n"／"\n    fi\n"——
# 兩個 repo 的縮排本來就不同，寫死字串等於在其中一邊碰運氣。
_end = next(
    (
        i
        for i in range(_start + 1, len(_LINES))
        if _LINES[i].strip() == "fi" and len(_LINES[i]) - len(_LINES[i].lstrip()) == _indent
    ),
    -1,
)
if _end < 0:
    print("  FAIL  找不到區塊結尾的 `fi`（wrapper 改了形狀？）")
    sys.exit(1)
BLOCK = "\n".join(ln[_indent:] if ln.startswith(" " * _indent) else ln.lstrip() for ln in _LINES[_start : _end + 1])
if "ssh-add -l" not in BLOCK:
    print("  FAIL  抽出來的區塊裡沒有 `ssh-add -l`（抽錯段了）")
    sys.exit(1)

# ⚠ **一律在 `set -euo pipefail` 底下跑**，不管被測的那支 wrapper 自己有沒有開。
#   公開版有、閉源版目前沒有——但這是同一段共用邏輯，它必須在較嚴的契約下正確，
#   否則哪天閉源那支補上 `set -e`，壞掉的方式會是「容器不啟動」而不是任何測試變紅。
STRICT = "set -euo pipefail"


def run_with(rc: int, stderr_msg: str) -> tuple[int, str]:
    """把假的 ssh-add 放進 PATH 最前面，在嚴格模式下跑那一段。

    回傳 (bash 的結束碼, 它印出來的東西)。**結束碼要一起回**：提前退出時 stdout 是
    空的，而「某某字串不在輸出裡」這種斷言在空輸出上一律成立——只比字串的話，
    整段從來沒執行也會全綠。
    """
    with tempfile.TemporaryDirectory() as d:
        fake = os.path.join(d, "ssh-add")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/bash\n[ -n "{stderr_msg}" ] && echo "{stderr_msg}" >&2\nexit {rc}\n')
        os.chmod(fake, 0o755)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], SSH_AUTH_SOCK="/tmp/probe.sock")
        r = subprocess.run(
            ["bash", "-c", STRICT + "\n" + BLOCK], env=env, check=False, capture_output=True, text=True, timeout=20
        )
        return r.returncode, r.stdout + r.stderr


print(f"== 受測主體：{os.path.basename(_WRAPPER)}（在 `{STRICT}` 底下）==")
rc0, out0 = run_with(0, "")
rc1, out1 = run_with(1, "The agent has no identities.")
rc2, out2 = run_with(2, "Error connecting to agent: No such file or directory")
# 非預期的碼也要走到 else，而不是把腳本炸掉。
rc5, out5 = run_with(5, "Something else went wrong")

print()
print("== 先確認整段真的跑完了（第一版就是在這裡假綠的）==")
check("🔴 rc=0 時不提前退出（bash 結束碼 0）", rc0 == 0)
check("🔴 rc=1 時不提前退出——`set -e` 曾經在這裡直接殺掉整個 wrapper", rc1 == 0)
check("🔴 rc=2 時不提前退出", rc2 == 0)
check("🔴 非預期的 rc=5 也不提前退出", rc5 == 0)

print()
print("== 三種結束碼要走三條不同的路 ==")
check("0（有金鑰）不抱怨", "⚠️" not in out0)
check("1（袋子空的）說的是「沒有任何金鑰」", "沒有任何金鑰" in out1)
check("🔴 2（連不到）**不會**被說成「沒有任何金鑰」", "沒有任何金鑰" not in out2 and out2.strip() != "")
check("🔴 2 明講是連不到 agent", "連不到" in out2)
check("🔴 2 帶出 ssh-add 自己的原始訊息（那句話才指得到原因）", "Error connecting to agent" in out2)
check("🔴 1 與 2 的輸出確實不同（併成同一句正是這支要防的事）", out1.strip() != out2.strip() and out1.strip() != "")
check("2 也把結束碼講出來（rc=2）", "rc=2" in out2)
check("非預期的 rc=5 也帶出 rc 與原始訊息", "rc=5" in out5 and "Something else went wrong" in out5)

print()
print("== 原始碼層級：不可以退回會被 errexit 殺掉的捕捉寫法 ==")
# ⚠ 只看**非註解行**。整份檔案做字串比對是這種斷言最典型的失效方式：這一段的註解本身
#   就在解釋「不要用 `set +e`」，於是 `"set +e" not in SRC` 會被自己的說明文字弄紅
#   （實測就是這樣紅的）。反過來也一樣糟——把錯誤寫法留在註解裡當範例，
#   「必須出現」的那幾條就會被註解餵飽而永遠通過。
SRC_CODE = "\n".join(ln for ln in SRC.splitlines() if not ln.lstrip().startswith("#"))
# ⚠ 這一條擋的是**回歸的具體寫法**。行為斷言在上面，這裡釘的是那兩個誘人的簡寫
#   （`if ssh-add -l; then … else …`、`…"; _ssh_add_rc=$?`）不要再出現。
# ⚠ 釘的是「rc 有預先歸零、用 `||` 捕捉、而且 1 是自己一條分支」。不比對完整的 if/elif
#   形狀——那會讓任何合理的重構都紅，而重構不是回歸。
check("🔴 rc 預先歸零（少了它 rc=0 那條路會撞上 nounset）", "_ssh_add_rc=0" in SRC_CODE)
check("🔴 用 `|| _ssh_add_rc=$?` 捕捉（賦值本身的結束碼在 set -e 下會殺掉腳本）", "|| _ssh_add_rc=$?" in SRC_CODE)
check(
    '🔴 不可以再出現 `…"; _ssh_add_rc=$?`（那正是被 errexit 殺掉的那一版）',
    '>/dev/null)"; _ssh_add_rc=$?' not in SRC_CODE,
)
check("🔴 沒有用 `set +e` 把 errexit 整段關掉（那是把一行的例外擴大成整段沒保護）", "set +e" not in SRC_CODE)
check("🔴 rc=1 是自己一條分支（非 0 全部併一句的話不會有這個）", '_ssh_add_rc" -eq 1' in SRC_CODE)
check("🔴 原始錯誤訊息有被捕捉下來（2 的那則要帶出它）", "_ssh_add_err=" in SRC_CODE)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
