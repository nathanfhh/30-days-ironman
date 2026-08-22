"""🔴 唯讀掛載擋得住檔案寫入與 chmod，但擋不住 unix socket 的 IPC。

    uv run --with docker python tests/test_ro_socket_mount.py

需要 docker（要真的掛一個唯讀 volume 才算數），也需要 `python:3.13-slim`。
⚠ **兩者缺一都是紅燈，不是 SKIP**——理由見下面「為什麼不自我 SKIP」。

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

## 為什麼不自我 SKIP

第一版缺 docker SDK 或缺 image 時 `print("  SKIP …"); sys.exit(0)`。**CI 上實測就是這樣
綠的**（run 32579472171，`--all` 的 log 只有一行「SKIP 本機沒有 python:3.13-slim」）：
子行程 exit 0 → `run-all.sh` 把它算成「跑過」→ 不會進跳過清單 → workflow 的「跳過上限 1」
那道 gate 看不到它。於是新增的 chmod／mode 斷言**從來沒有在 CI 上執行過**，
而畫面上是一片綠。

自我 SKIP 的正當範圍很窄：`run-all.sh` 已經用 `NEEDS_DOCKER` 決定了「這個模式要不要跑
docker 測試」。走到這裡代表**呼叫端已經宣告 docker 可用**，那麼缺 SDK、連不到 daemon、
缺 image 都是設定漏了，不是環境限制——要紅。image 由 workflow 預拉（與代理 image 同一個
做法），本機缺就照著訊息 `docker pull` 一次。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

_pass = _fail = 0


def check(label: str, ok: bool) -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}")


def die(msg: str) -> None:
    """缺前提時的收場：紅燈 + 指得到修法的一句話。**不是 SKIP。**"""
    print(f"  FAIL  {msg}")
    sys.exit(1)


try:
    import docker
except ImportError:
    die(
        "缺 docker SDK：uv run --with docker python tests/test_ro_socket_mount.py\n"
        "        （經 run-all.sh 跑時代表 NEEDS_DOCKER 那道 gate 已經放行，所以是設定漏了）"
    )

IMAGE = "python:3.13-slim"
# ⚠ **每次執行一個唯一名字。** 固定名字時，同一台機器上兩個執行（或前一場沒收乾淨）
#   會互相刪對方的 volume——而症狀是隨機的、看起來像 docker 壞掉。
#   只用 [a-zA-Z0-9][a-zA-Z0-9_.-] 這組字元，那是 docker volume 的命名規則。
VOL = f"ncr-ro-socket-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
# 這一場產生的東西都貼上這個 label。**不是**拿來做全域清理的——那會誤刪同時在跑的另一場；
# 是給人用的：`docker ps -a --filter label=ncr.test=ro-socket-mount` 一眼看得出殘留是誰的。
LABELS = {"ncr.test": "ro-socket-mount", "ncr.test.run": VOL}

SERVER = r"""
import os, socket
p = "/sock/agent.sock"
try: os.unlink(p)
except FileNotFoundError: pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(p); os.chmod(p, 0o666); s.listen(5)
print("SERVER-READY", flush=True)
while True:
    c, _ = s.accept(); d = c.recv(1024); c.sendall(b"ACK:" + d); c.close()
"""

CLIENT = r"""
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
"""

try:
    cli = docker.from_env()
    cli.ping()
except Exception as exc:  # noqa: BLE001
    die(
        f"連不到 docker daemon（{type(exc).__name__}: {exc}）。先把 docker 起來再跑。\n"
        "        這裡**不 SKIP**：exit 0 的自我 SKIP 會被 run-all.sh 算成「跑過而且過了」。"
    )

try:
    cli.images.get(IMAGE)
except docker.errors.ImageNotFound:
    die(
        f"本機沒有 {IMAGE}。這是**紅燈不是 SKIP**：exit 0 的自我 SKIP 會被 run-all.sh "
        f"算成「跑過」，於是下面每一條斷言都沒執行、CI 卻是綠的（實際發生過）。"
        f"修法：docker pull {IMAGE}（CI 由 workflow 預拉）。"
    )

# teardown 失敗要看得見。`suppress(Exception)` 會讓每一次執行都留下一顆新的 container
# 與一顆新的 volume，而測試照樣全綠——直到磁碟滿了才有人發現，那時已經沒有線索指回這裡。
_teardown_errors: list[str] = []


def teardown(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        _teardown_errors.append(f"{label} → {type(exc).__name__}: {exc}")


cli.volumes.create(VOL, labels=LABELS)
srv = None
try:
    srv = cli.containers.run(
        IMAGE, ["python", "-c", SERVER], detach=True, labels=LABELS, volumes={VOL: {"bind": "/sock", "mode": "rw"}}
    )
    # ⚠ 等不到就**明確失敗**。跑完迴圈繼續往下的話，client 會爆出一個
    #   「連不上 socket」的錯——那看起來像本測試要驗的性質不成立，
    #   實際上只是 server 還沒起來。假失敗比沒有測試更糟。
    _ready = False
    for _ in range(40):  # 等 server bind 好（最多 10 秒）
        if b"SERVER-READY" in srv.logs():
            _ready = True
            break
        time.sleep(0.25)
    check("🔴 server 在期限內就緒（沒有的話下面每一條都不算數）", _ready)
    if not _ready:
        print("  server log:", srv.logs().decode(errors="replace")[-300:])
    else:
        out = cli.containers.run(
            IMAGE, ["python", "-c", CLIENT], remove=True, labels=LABELS, volumes={VOL: {"bind": "/sock", "mode": "ro"}}
        ).decode()
        res = json.loads(out.split("RESULT:", 1)[1].strip())

        # ① 掛載真的是唯讀（否則後面兩條都不算數）
        check("🔴 掛載確實是唯讀（/proc/mounts 有 ro）", " ro," in res["mount"] or res["mount"].endswith(" ro"))
        # ② 一般檔案寫不進去 —— 證明唯讀是有效的，不是設定沒生效
        check("🔴 一般檔案寫入被擋（EROFS=30）", res["write"] == "errno=30")
        # ③ 但 socket 照樣通 —— 這一條就是那個錯誤說法的反例
        check("🔴 同一個唯讀掛載上，unix socket 仍能 connect/send/recv", res["sock"] == "ACK:agent-protocol-write")
        # ④⑤ **改成唯讀掛載真正買到的東西。** 少了這兩條，這支測試只證明了「:ro 沒有壞事」，
        #     沒有證明「:ro 有做事」——而後者才是做這個決定的理由。
        #     bind mount 與 host 共用同一個 inode，所以容器裡的 chmod 改的是 host 那一顆；
        #     原生 Linux 上那會弄壞使用者其他終端機的 ssh。
        check("🔴 唯讀掛載擋下 chmod（EROFS=30）", res["chmod"] == "errno=30")
        check("🔴 而且 socket 的 mode 真的沒被改動", res["mode_before"] == res["mode_after"])
except Exception as exc:  # noqa: BLE001
    # ⚠ 本體丟例外時**不要讓它直接往外拋**。拋出去的話 `finally` 雖然仍然會跑，
    #   但下面那段「資源沒收乾淨」的揭露、以及最後的 summary 都不會印——只剩一段
    #   traceback，而殘留的 container／volume 沒有任何人提起。
    #   主因仍然是這一條（排在最前面），teardown 的問題排在它後面附帶揭露。
    import traceback

    check(f"🔴 測試本體爆掉：{type(exc).__name__}: {exc}", False)
    traceback.print_exc()
finally:
    # ⚠ 不做「啟動前盲刪同名資源」的那一套：名字是這次執行獨有的，同名只可能是別人的。
    #   也不做 label 全域清理——同時在跑的另一場會被一起刪掉。
    if srv is not None:
        teardown(f"container {srv.id[:12]}", lambda: srv.remove(force=True))
    teardown(f"volume {VOL}", lambda: cli.volumes.get(VOL).remove(force=True))

if _teardown_errors:
    print()
    print("== 收尾 ==")
    for err in _teardown_errors:
        # ⚠ 本體通過但沒收乾淨，**本場仍然算失敗**：綠燈代表「這次執行沒有留下問題」，
        #   而留了一顆殭屍 container 就是問題。本體已經紅的話這幾條只是附帶揭露，
        #   排在後面，主因仍然是上面那些。
        check(f"🔴 資源沒收乾淨：{err}", False)
    print(
        f"  ⚠ 手動確認：docker ps -a --filter label=ncr.test.run={VOL}"
        f" / docker volume ls --filter label=ncr.test.run={VOL}"
    )

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
