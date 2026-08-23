"""preflight 的「起得來但一定做不了事」那一格必須是 fatal，不是 warning。不需要 docker。

    uv run --with flask --with docker --with sqlalchemy --with psutil \
        python tests/test_preflight_fatal.py

為什麼要有這支：`preflight()` 回的是 `(problems, fatal)` 兩袋。掉進 `problems` 的只會被印
一行，服務照樣起來、健康檢查照樣綠；掉進 `fatal` 的會讓 `app.py` 直接 `SystemExit(1)`。
分類錯的代價是**很晚才炸而且指不回來**：使用者按下「建立 session」才失敗，錯誤是 provision
拋出來的 OSError，看起來像 docker 出問題。

⚠ 這支同時守著兩件事，缺一條就等於沒修：
  (a) 該進 fatal 的真的在 fatal 裡；
  (b) `app.py` 拿到非空的 fatal 真的會停下來——**分類對了但沒接上去，等於沒修**。
"""

import ast
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="test-preflight-fatal-")
config.SPACE_HOST = config.SPACE_SELF = TMP
config.MOUNTS = {TMP: {"bind": "/x", "mode": "ro"}}

import docker  # noqa: E402

# preflight 會去問 docker（HOST_REPO_ROOT 那道、以及 attach_jaeger）。讓它問不到就好，
# 那兩段都包在 suppress 裡。同 test_host_platform 的手法。
docker.from_env = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("測試不連 docker"))

from server import sessions  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


NEEDLE = "per-user 狀態空間不可寫"

print("== 可寫的時候不該喊 ==")
probs, fatal = sessions.preflight()
check("不在 problems", not any(NEEDLE in p for p in probs))
check("不在 fatal", not any(NEEDLE in f for f in fatal))

print("== 🔴 不可寫 → fatal，而且不可以只是 warning ==")
# 用一個「建得出來但寫不進去」的路徑：mkdir 成功、os.access(W_OK) 失敗。
LOCKED = os.path.join(TMP, "locked")
os.makedirs(LOCKED, exist_ok=True)
os.chmod(LOCKED, 0o500)
_old = config.SPACE_SELF
config.SPACE_SELF = os.path.join(LOCKED, "space")
try:
    probs, fatal = sessions.preflight()
    check("🔴 出現在 fatal 裡", any(NEEDLE in f for f in fatal))
    check("🔴 **不**在 problems 裡（不是兩邊都放）", not any(NEEDLE in p for p in probs))
    msg = next((f for f in fatal if NEEDLE in f), "")
    check("訊息講得出症狀（一個 session 都建不起來）", "建不起來" in msg)
    check("訊息講得出去哪裡修（CLAUDE_PTY_SPACE_SELF）", "CLAUDE_PTY_SPACE_SELF" in msg)
finally:
    config.SPACE_SELF = _old
    os.chmod(LOCKED, 0o700)

print("== 🔴 app.py 拿到非空 fatal 真的會停下來 ==")
# ⚠ 這一條不能用「跑起來看看」測：那要真的 import app，會連 DB、起 Flask、動到正式環境。
#   改成讀原始碼的結構——問的是「fatal 非空的分支裡有沒有 SystemExit」，不是字串比對。
src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "app.py")
tree = ast.parse(open(src, encoding="utf-8").read())


def _exits(node) -> bool:
    return any(
        isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
        and n.exc.func.id == "SystemExit"
        for n in ast.walk(node)
    )


guards = [n for n in ast.walk(tree) if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "_fatal"]
check("找得到 `if _fatal:` 這個分支", len(guards) == 1)
check("🔴 該分支裡會 raise SystemExit（不是只印一行）", bool(guards) and _exits(guards[0]))

# preflight 的回傳形狀本身也守一下：兩袋，不是一袋。
check(
    "preflight 回的是 (problems, fatal) 兩袋",
    isinstance(sessions.preflight(), tuple) and len(sessions.preflight()) == 2,
)

print(f"\n{'FAILED' if _fails else 'OK'}：{_fails} 條失敗")
sys.exit(1 if _fails else 0)
