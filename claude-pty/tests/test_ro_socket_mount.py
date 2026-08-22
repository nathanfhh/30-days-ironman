"""🔴 唯讀掛載擋得住檔案寫入，但擋不住 unix socket 的 IPC。

    uv run --with docker python tests/test_ro_socket_mount.py

需要 docker（要真的掛一個唯讀 volume 才算數）。

## 為什麼要有這一支

這兩個 repo 曾經有多處寫著「連 unix socket 需要寫權限，掛 `:ro` 會 EACCES，等於沒掛」。
前半句出自 `unix(7)`，是真的；後半句是錯的推論，把兩件不同的事混為一談：

  * `unix(7)` 的 write permission 指的是 **socket inode 的 mode bits**。
    connect() 走 `unix_find_bsd()` → `path_permission(&path, MAY_WRITE)`，檢查的是那個 inode。
  * Docker `:ro` 設定的是**掛載層**的 `MNT_READONLY`（**不是** superblock 唯讀）。
    kernel 只在走 `mnt_want_write()` 的寫入路徑檢查它並回 `EROFS`：create、unlink、
    open-for-write、chmod 這些。而 connect **整條路徑不經過** `mnt_want_write`，
    所以 `MNT_READONLY` 從來沒被諮詢。

    ⚠ 本測試裡 server 端是以 **rw** 掛著同一個 volume 在寫 socket 的，這件事本身就
      證明 superblock 並非唯讀 —— 所以 `sb_permission()` 的 `sb_rdonly(sb)` 恆為 false，
      那段程式碼在這裡從未被觸發。（它的 S_ISSOCK 豁免是**另一個情境**的事實：
      superblock 真的唯讀時，例如 `mount -o ro` 或 squashfs。兩層都擋不住 socket IPC，
      但作用的是哪一層要講對。）

所以 `:ro` 不會清掉 socket inode 的 write mode bit，也不會阻止 connect。
真正會讓 connect 失敗的是 inode 權限，那回的是 `EACCES` 而不是 `EROFS`。

⚠ 這一支釘的是**核心行為**，不是我們的設定。它存在的理由是：那個錯誤說法看起來很合理、
  而且引用了一句真的手冊，所以只靠 code review 擋不住——要靠一個會跑的反例。
"""

from __future__ import annotations

import sys

try:
    import docker
except ImportError:  # pragma: no cover
    print("  SKIP  沒有 docker SDK")
    sys.exit(0)

_pass = _fail = 0


def check(label: str, ok: bool) -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}")


IMAGE = "python:3.13-slim"
VOL = "ncr-ro-socket-test"

SERVER = r'''
import os, socket
p = "/sock/agent.sock"
try: os.unlink(p)
except FileNotFoundError: pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(p); os.chmod(p, 0o666); s.listen(5)
print("SERVER-READY", flush=True)
while True:
    c, _ = s.accept(); d = c.recv(1024); c.sendall(b"ACK:" + d); c.close()
'''

CLIENT = r'''
import socket, json, os
out = {}
out["mount"] = [l.strip() for l in open("/proc/mounts") if " /sock " in l][0]
try:
    open("/sock/plain.txt", "w").write("x"); out["write"] = "ok"
except OSError as e:
    out["write"] = f"errno={e.errno}"
try:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect("/sock/agent.sock"); c.sendall(b"agent-protocol-write")
    out["sock"] = c.recv(1024).decode()
except OSError as e:
    out["sock"] = f"errno={e.errno}"
print("RESULT:" + json.dumps(out))
'''

cli = docker.from_env()
try:
    cli.images.get(IMAGE)
except docker.errors.ImageNotFound:
    print(f"  SKIP  本機沒有 {IMAGE}（離線環境）")
    sys.exit(0)

with __import__("contextlib").suppress(Exception):
    cli.volumes.get(VOL).remove(force=True)
cli.volumes.create(VOL)
srv = None
try:
    srv = cli.containers.run(IMAGE, ["python", "-c", SERVER], detach=True,
                             volumes={VOL: {"bind": "/sock", "mode": "rw"}})
    import time
    for _ in range(40):                       # 等 server bind 好
        if b"SERVER-READY" in srv.logs():
            break
        time.sleep(0.25)

    out = cli.containers.run(IMAGE, ["python", "-c", CLIENT], remove=True,
                             volumes={VOL: {"bind": "/sock", "mode": "ro"}}).decode()
    import json as _json
    res = _json.loads(out.split("RESULT:", 1)[1].strip())

    # ① 掛載真的是唯讀（否則後面兩條都不算數）
    check("🔴 掛載確實是唯讀（/proc/mounts 有 ro）",
          " ro," in res["mount"] or res["mount"].endswith(" ro"))
    # ② 一般檔案寫不進去 —— 證明唯讀是有效的，不是設定沒生效
    check("🔴 一般檔案寫入被擋（EROFS=30）", res["write"] == "errno=30")
    # ③ 但 socket 照樣通 —— 這一條就是那個錯誤說法的反例
    check("🔴 同一個唯讀掛載上，unix socket 仍能 connect/send/recv",
          res["sock"] == "ACK:agent-protocol-write")
finally:
    if srv is not None:
        with __import__("contextlib").suppress(Exception):
            srv.remove(force=True)
    with __import__("contextlib").suppress(Exception):
        cli.volumes.get(VOL).remove(force=True)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
