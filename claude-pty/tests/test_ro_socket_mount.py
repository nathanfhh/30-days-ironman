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

import os
import sys
import uuid

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
# ⚠ **每次執行一個唯一名字。** 固定名字時，同一台機器上兩個執行（或前一場沒收乾淨）
#   會互相刪對方的 volume——而症狀是隨機的、看起來像 docker 壞掉。
#   只用 [a-zA-Z0-9][a-zA-Z0-9_.-] 這組字元，那是 docker volume 的命名規則。
VOL = f"ncr-ro-socket-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"

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
# 這一組才是「改成唯讀掛載」真正買到的保護：bind mount 與 host 共用同一個 inode，
# 容器對它 chmod 就是改到 host 那一顆。
out["mode_before"] = oct(os.stat("/sock/agent.sock").st_mode)
try:
    os.chmod("/sock/agent.sock", 0o777); out["chmod"] = "ok"
except OSError as e:
    out["chmod"] = f"errno={e.errno}"
out["mode_after"] = oct(os.stat("/sock/agent.sock").st_mode)
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

# ⚠ 這裡**不做**「先刪掉同名的再建」。名字是這次執行獨有的，同名只可能是別人的；
#   盲刪就是把別人正在用的東西砍掉。
cli.volumes.create(VOL)
srv = None
try:
    srv = cli.containers.run(IMAGE, ["python", "-c", SERVER], detach=True,
                             volumes={VOL: {"bind": "/sock", "mode": "rw"}})
    import time
    # ⚠ 等不到就**明確失敗**。跑完迴圈繼續往下的話，client 會爆出一個
    #   「連不上 socket」的錯——那看起來像本測試要驗的性質不成立，
    #   實際上只是 server 還沒起來。假失敗比沒有測試更糟。
    _ready = False
    for _ in range(40):                       # 等 server bind 好（最多 10 秒）
        if b"SERVER-READY" in srv.logs():
            _ready = True
            break
        time.sleep(0.25)
    check("🔴 server 在期限內就緒（沒有的話下面每一條都不算數）", _ready)
    if not _ready:
        print("  server log:", srv.logs().decode(errors="replace")[-300:])
        raise SystemExit(1)

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
    # ④⑤ **改成唯讀掛載真正買到的東西。** 少了這兩條，這支測試只證明了「:ro 沒有壞事」，
    #     沒有證明「:ro 有做事」——而後者才是做這個決定的理由。
    #     bind mount 與 host 共用同一個 inode，所以容器裡的 chmod 改的是 host 那一顆；
    #     原生 Linux 上那會弄壞使用者其他終端機的 ssh。
    check("🔴 唯讀掛載擋下 chmod（EROFS=30）", res["chmod"] == "errno=30")
    check("🔴 而且 socket 的 mode 真的沒被改動",
          res["mode_before"] == res["mode_after"])
finally:
    if srv is not None:
        with __import__("contextlib").suppress(Exception):
            srv.remove(force=True)
    with __import__("contextlib").suppress(Exception):
        cli.volumes.get(VOL).remove(force=True)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
