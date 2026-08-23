"""SECRET_KEY 落地的併發正確性（review 2026-07-26）。

**這支一定要多行程跑，單一行程證明不了任何事。**

`config._load_or_create_secret()` 的不變量是兩條，缺一不可：
  1. 原子——讀到的必定是完整內容（不可以有人讀到寫到一半的空字串）
  2. 互斥——只有一個 worker 寫的金鑰會成為那一把，其餘改讀既有檔

第 2 條曾經只寫在註解裡（「輸的那方改讀既有檔」），程式裡沒有那條分支：`O_EXCL` 加在
帶 pid 的暫存檔上（本來就不可能撞），接著無條件 `os.replace()` 覆蓋目的檔。
四個行程同時起來時各自覆蓋對方、各自回傳自己寫的那一把——實測 40/40 輪分岔。

分岔的症狀不是「壞掉」而是**「時好時壞」**：A worker 簽的 cookie 到 B worker 驗不過，
使用者這次請求登入著、下次就被踢回登入頁。而且輸的那把金鑰已經不在磁碟上，
重啟之後連 A 自己發的 cookie 也一起死。

    uv run --with flask --with docker --with sqlalchemy python tests/test_secret_key.py
"""

import multiprocessing as mp
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def _worker(home, q, barrier):
    """在子行程裡以 `home` 當 HOME 取一次金鑰。

    ⚠ 必須在 import config **之前**改掉 HOME：那個路徑是在 import 時求值的。
      也因此每個子行程都要重新 import（spawn 會給一個乾淨的直譯器，正好）。
    """
    os.environ["HOME"] = home
    os.environ.pop("CLAUDE_PTY_SECRET_KEY", None)  # 有 env 就走不到檔案那條路
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    barrier.wait()  # 四個行程對齊起跑線，把競態窗口撐到最大
    from server import config

    q.put(config.SECRET_KEY)


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)  # fork 會沿用父行程已 import 的 config
    N_PROCS, ROUNDS = 4, 12
    print(f"== {N_PROCS} 個行程同時啟動 × {ROUNDS} 輪，每一輪都必須拿到同一把金鑰 ==")
    diverged = 0
    for i in range(ROUNDS):
        home = tempfile.mkdtemp(prefix=f"claude-pty-secret-{i}-")
        try:
            q, barrier = mp.Queue(), mp.Barrier(N_PROCS)
            procs = [mp.Process(target=_worker, args=(home, q, barrier)) for _ in range(N_PROCS)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=60)
            keys = {q.get(timeout=10) for _ in procs}
            with open(os.path.join(home, ".claude-pty", "secret.key")) as f:
                on_disk = f.read().strip()
            leftovers = [n for n in os.listdir(os.path.join(home, ".claude-pty")) if n.startswith("secret.key.")]
            if len(keys) != 1 or on_disk not in keys or leftovers:
                diverged += 1
                if diverged == 1:
                    print(
                        f"  第 {i} 輪：記憶體裡有 {len(keys)} 把不同的金鑰、"
                        f"磁碟上是{'其中一把' if on_disk in keys else '別的'}"
                        f"、殘留暫存檔 {leftovers}"
                    )
        finally:
            shutil.rmtree(home, ignore_errors=True)
    check(f"{ROUNDS} 輪全部只有一把金鑰（分岔 {diverged} 輪）", diverged == 0)

    print("== 已經有檔案時直接沿用，不覆蓋（重啟不該把所有人登出）==")
    home = tempfile.mkdtemp(prefix="claude-pty-secret-existing-")
    try:
        os.makedirs(os.path.join(home, ".claude-pty"))
        path = os.path.join(home, ".claude-pty", "secret.key")
        with open(path, "w") as f:
            f.write("pre-existing-key-must-survive")
        os.environ["HOME"] = home
        os.environ.pop("CLAUDE_PTY_SECRET_KEY", None)
        q, barrier = mp.Queue(), mp.Barrier(1)
        p = mp.Process(target=_worker, args=(home, q, barrier))
        p.start()
        p.join(timeout=60)
        got = q.get(timeout=10)
        with open(path) as f:
            after = f.read().strip()
        check("回傳既有金鑰", got == "pre-existing-key-must-survive")
        check("磁碟上的檔案原封不動", after == "pre-existing-key-must-survive")
    finally:
        shutil.rmtree(home, ignore_errors=True)

    print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
    sys.exit(1 if _fails else 0)
