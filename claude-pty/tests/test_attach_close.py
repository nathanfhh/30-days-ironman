"""attach socket 的收尾是否真的釋放 fd（純單元、零 token、不碰 docker）。

    uv run --with docker --with sqlalchemy python tests/test_attach_close.py

為什麼值得一支獨立測試：這個失敗是**靜默且延遲**的。`close_attach()` 曾經只關 docker-py
給的 `SocketIO` wrapper，而 `SocketIO.close()` 不關底層 fd（只做 `_decref_socketios()`）
——當下沒有任何錯誤，fd 要等 CPython GC 收掉 docker-py 內部的參照環才消失。那段空窗期
dockerd 仍往那條沒人讀的連線灌容器輸出，208KB 緩衝一滿就把該容器的 stdout broadcaster
鎖死：輸出全凍、`docker rm` 也卡住（2026-07-30 實測 5 小時，ADR 0015）。

所以這裡驗的不是「close 有沒有被呼叫」，而是**底層 fd 有沒有真的關掉**——用 socketpair
＋真的 `socket.SocketIO` 複製 docker-py 7.2.0 的回傳形狀，斷言關完之後 fd 不可用。
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.sessions import _discard_attach, close_attach  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def fake_attach_sock(with_client=True):
    """複製 docker-py `attach_socket()` 的回傳形狀。

    真品是 `response.raw._fp.fp.raw`，型別為 `socket.SocketIO`，`_sock` 指向底層 socket；
    我們的 `attached()` 正是靠 `sock._sock` 取出 raw socket 來讀寫。
    """
    a, b = socket.socketpair()
    sio = socket.SocketIO(a, "rwb")
    if with_client:
        sio._claude_pty_client = _FakeClient()
    return sio, a, b


class _FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def fd_dead(sock) -> bool:
    """底層 socket 真的關了嗎？（關了就不能再送）"""
    try:
        sock.send(b"x")
    except OSError:
        return True
    return False


print("\n--- 前提：SocketIO.close() 本身不關底層 fd（這就是這支測試存在的理由）---")
sio, raw, peer = fake_attach_sock(with_client=False)
sio.close()
check("🔴 只關 wrapper 的話 fd 還活著（前提成立，否則本測試無效）", not fd_dead(raw))
raw.close()
peer.close()

print("\n--- close_attach() ---")
sio, raw, peer = fake_attach_sock()
close_attach(sio)
check("🔴 底層 fd 真的關掉了", fd_dead(raw))
check("專屬的 docker client 也一起收了", sio._claude_pty_client.closed)
peer.close()

print("\n--- _discard_attach()（attach 途中失敗的清理路徑）---")
sio, raw, peer = fake_attach_sock(with_client=False)
client = _FakeClient()
_discard_attach(sio, client)
check("🔴 底層 fd 真的關掉了", fd_dead(raw))
check("client 也收了", client.closed)
peer.close()

print("\n--- 邊界：重複呼叫、以及沒有 _sock 的物件都不可以拋錯 ---")
sio, raw, peer = fake_attach_sock()
close_attach(sio)
try:
    close_attach(sio)          # 第二次（reconciler 與請求路徑可能都收同一個）
    check("重複 close_attach() 不拋錯", True)
except Exception as e:  # noqa: BLE001
    check(f"重複 close_attach() 拋了 {type(e).__name__}", False)
peer.close()


class _NoSock:
    """docker-py 換實作、或直接回傳 raw socket 時（沒有 `_sock`）也不能炸。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


bare = _NoSock()
try:
    close_attach(bare)
    check("沒有 _sock 屬性時仍走完（並關掉 wrapper 本身）", bare.closed)
except Exception as e:  # noqa: BLE001
    check(f"沒有 _sock 屬性時拋了 {type(e).__name__}", False)

try:
    _discard_attach(None, _FakeClient())
    check("sock=None 的失敗路徑不拋錯", True)
except Exception as e:  # noqa: BLE001
    check(f"sock=None 時拋了 {type(e).__name__}", False)

print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("done")
