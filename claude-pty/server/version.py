"""頁尾要顯示的各模組版本與 commit。

**為什麼 commit 與打包時間一定要在 build 時烘進去**：程式是 `COPY` 進 image 的，`.git`
根本不在 build context 裡（context 是 `claude-pty/`，`.git` 在上一層），容器內也沒有 `git`
執行檔——執行期問不到自己是哪一版。由 `deploy/redeploy.sh` 算好，經 build arg 變成 image
的 env（見 deploy/Dockerfile 末段）。

⚠ **問不到就要說「未知」，不可以退回一個看起來合理的值。** 頁尾唯一的用途就是回答
  「線上跑的到底是哪一版」——猜錯比空白糟得多：空白會讓人去查，錯的值會讓人停止查。

⚠ **不可以放在 render 路徑上重算。** 這裡會跑 `ttyd --version`（subprocess）。整份結果
  第一次被要求時算一次、之後吃快取——這些值在同一個行程的生命週期內不會變。
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache

from . import config

# 問版本的逾時。只是印一行字就退出，真要超過 2 秒代表環境有鬼，那時寧可顯示「問不到」
# 也不要拖住第一個開頁面的人。
_PROBE_TIMEOUT = 2.0


def _own_version() -> str | None:
    """這個套件自己的版本。

    image 內是 `pip install .` 裝進去的，metadata 查得到；直接跑原始碼（開發、測試）時
    不一定裝過，那就退回讀 pyproject.toml。兩條都不成立就留白。
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version
    try:
        return _pkg_version("claude-pty")
    except PackageNotFoundError:
        pass
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("version"):
                    return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return None


def _git_from_worktree() -> str | None:
    """開發時（有 .git、有 git）直接問工作區，省得為了看頁尾先 build 一次 image。

    ⚠ 只在**開發**環境成立，容器內兩個條件都不滿足。
    ⚠ 髒的工作區要標 `-dirty`：不標的話頁尾會宣告一個它其實沒在跑的 commit。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        sha = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                               capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
                               check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return (f"{sha}-dirty" if dirty else sha) or None


def _ttyd_version(binary: str) -> tuple[str | None, str | None]:
    """`<binary> --version` → (版號, commit)。

    ttyd 印的是 `ttyd version 1.7.7-40e79c7`：破折號後面那一段就是它自己的 commit。
    拆成兩欄是因為頁尾要分別標示「版號」與「commit」，黏成一串讀的人分不出哪段是什麼。
    """
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=_PROBE_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, None
    lines = (out.stdout or out.stderr or "").strip().splitlines()
    if out.returncode != 0 or not lines:
        return None, None
    raw = lines[0].removeprefix("ttyd version ").strip()[:64]
    ver, _, commit = raw.partition("-")
    return ver or None, commit or None


@lru_cache(maxsize=1)
def summary() -> dict:
    """頁尾用的整包資料。第一次被要求時算，之後吃 lru_cache。

    每一列自己說得出 name / version / commit / built_at / detail，模板只負責畫、不做判斷
    ——多一顆 binary 就多一列，模板不必改。
    """
    sha = os.environ.get("CLAUDE_PTY_GIT_SHA") or _git_from_worktree()
    mods = [{
        "name": "agent-tty",
        "version": _own_version(),
        "commit": sha,
        # ⚠ 這裡放的是**建置時間**不是 commit 時間。要回答的是「這個 image 是什麼時候建
        #   出來的」——同一個 commit 可以在任何時候被重新建置，commit 時間答不了那個問題，
        #   而「線上這包是三天前建的」正是要看的事。
        "built_at": os.environ.get("CLAUDE_PTY_BUILT_AT") or None,
        "detail": ("控制平面本體。"
                   if sha else
                   "commit 未知：這個 image 不是經 deploy/redeploy.sh build 的。"),
    }]
    for binary, flavor in config.TTYD_BINS.items():
        ver, commit = _ttyd_version(binary)
        # Rust 版沒有 release、靠釘 commit 建；binary 問不到時（本機沒裝）至少還答得出
        # 「這個 image 是照哪個 commit 建的」。
        pinned = os.environ.get("CLAUDE_PTY_TTYD_RUST_REF") if binary == "ttyd-rust" else None
        mods.append({
            "name": f"ttyd（{flavor}）",
            "version": ver,
            "commit": commit or (pinned[:7] if pinned else None),
            "built_at": None,
            "detail": (f"{binary}：目前用於網頁終端的執行檔。"
                       if ver else
                       f"{binary} 未安裝於本機（正式部署位於 image 內）。"),
        })
    # ⚠ **刻意不列 Python 版本。** 頁尾要回答的是「線上跑的是哪一版**這個服務**」，
    #   而 runtime 版本回答不了那個問題——它由 base image 決定，一年動一次，放在這裡
    #   只是佔掉一格讓真正要看的東西變得更難找。要查的時候 `docker compose exec` 一行就有。
    # ⚠ **刻意不列 session image 的版本。** 那要打一次 docker inspect，而
    #   「每次 render 都問 dockerd」正是 2026-07-27 讓列表停擺 40 分鐘的那個形狀
    #   （ADR 0012）。每一場用的是哪一版 image 已經逐列顯示在 session 列表上了。
    return {"modules": mods}
