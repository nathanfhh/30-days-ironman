"""SQLite 單一互斥路徑（BEGIN IMMEDIATE）真的把該互斥的東西鎖住。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography \
        python tests/test_mutex_semantics.py

**不需要 docker daemon**——docker client 是假的。

## 為什麼要有這一支

這套東西的互斥只有一條路：`session_scope(immediate=True)` 的 BEGIN IMMEDIATE。
沒有第二套機制，所以「該互斥的東西」每一項都要有測試釘住，不能靠
「反正只有一個 worker 應該不會撞」：

  🔴 配額計算——「SELECT COUNT 再 INSERT」在 deferred 交易下不可序列化，兩個執行緒
     可以同時通過檢查（review B2：單一 threaded process 內就會發生，不只多 worker）。
  🔴 reconciler 的單一執行者租約——兩個 reconciler 同時看到過期租約時，
     不可以雙雙判定可接手（那正是這張租約要防的事）。

（view 的 port UNIQUE 與「一場一 view」由 DB 約束仲裁，釘在 test_persistence。
四項合起來就是互斥語意的完整清單。）
"""
import datetime as _dt
import os
import sys
import tempfile
import threading
import time

os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
_tmp = tempfile.mkdtemp(prefix="claude-pty-mutex-")
os.environ["CLAUDE_PTY_DB_PATH"] = os.path.join(_tmp, "test.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config, db  # noqa: E402

config.DB_URL = f"sqlite:///{os.environ['CLAUDE_PTY_DB_PATH']}"
config.SECRET_KEY = "mutex-secret"
config.MOUNTS = {}

config.HOST_HOME = _tmp

db.reset_engine()
db.init_db()

# 憑證＝DB 裡的 setup-token（唯一來源，D 階段起不再讀任何 host 憑證檔）。
# 這批測試的 session 都掛在 system 使用者名下，給它種一個測試值就過得了 create() 的守門。
from server import auth as _auth_seed  # noqa: E402
from server import sessions as _sessions_seed  # noqa: E402

_auth_seed.set_cli_token(_sessions_seed.ensure_system_user(), "sk-test-setup-token")

from server import auth, reconciler  # noqa: E402
from server.sessions import SessionError, SessionManager  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


# --- 假 docker：只要 create() 走得完，不記細節（那是 test_create_ordering 的事）--------

class _FakeContainer:
    id = "cid-fake"

    @staticmethod
    def start():
        pass


class _FakeContainers:
    @staticmethod
    def create(*a, **kw):
        # 故意慢一拍：把「兩個執行緒同時在配額檢查與登錄之間」的窗口拉開。
        # 配額若不是在同一筆 immediate 交易裡數＋寫，這裡就是超賣的溫床。
        time.sleep(0.05)
        return _FakeContainer()

    @staticmethod
    def list(**kw):
        return []

    @staticmethod
    def get(*a, **kw):
        # 背景的就緒偵測執行緒會來問容器；當它已經不在，讓那條執行緒安靜收場。
        import docker
        raise docker.errors.NotFound("gone")


class _FakeNetwork:
    name = "fake-user-net"

    @staticmethod
    def connect(*a, **kw):
        pass


class _FakeNetworks:
    """使用者網路（ADR 0016）。**這裡一定要有東西回**。

    ⚠ create() 在配額交易**之前**就要先把使用者的網路準備好，而那一步失敗時拋的也是
      `SessionError`。少了這個假物件，兩條執行緒都會在網路那一關就死掉，然後被下面的
      `except SessionError` 記成「被配額擋下」——於是這支測試會報「兩個都被擋」，
      看起來像互斥壞了，其實配額那段根本沒跑到（2026-08-07 加 per-user 網路時踩到）。
    """

    @staticmethod
    def list(names=None, **kw):
        return [_FakeNetwork()] if names else []

    @staticmethod
    def create(name, **kw):
        return _FakeNetwork()

    @staticmethod
    def get(name):
        return _FakeNetwork()


class _FakeAPI:
    @staticmethod
    def resize(*a, **kw):
        pass

    @staticmethod
    def remove_container(*a, **kw):
        pass


class _FakeClient:
    containers = _FakeContainers()
    networks = _FakeNetworks()
    api = _FakeAPI()


print("== 配額：兩個執行緒搶最後一個名額，只能有一個成功 ==")
uid = auth.create_user("mutex-user", "mutex-password-1")["id"]
auth.set_cli_token(uid, "sk-test-setup-token")   # 憑證守門在 create() 入口，配額才是本段主角
_orig_max = config.MAX_SESSIONS
config.MAX_SESSIONS = 1
results: list[str] = []


def try_create():
    mgr = SessionManager()
    mgr._docker = _FakeClient()          # noqa: SLF001 — 就是要換掉它
    try:
        mgr.create(user_id=uid)
        results.append("ok")
    except SessionError:
        results.append("quota")
    except Exception as e:               # noqa: BLE001 — 其他例外要現形，不可以吞成綠燈
        # ⚠ **訊息要一起帶出來，只有型別名不夠。**
        #   這條斷言 2026-08-07 紅過至少三次，每次都被當成「配額競態偶發」而略過——而它
        #   從來不是競態，是樹在半成品狀態下 `create()` 直接壞掉（見下方 boom 的判讀）。
        #   當時失敗行只印得出 `boom:AttributeError`，看起來像雜訊；真正的那句
        #   「module 'server.config' has no attribute 'SESSION_NETWORK'」被丟掉了。
        #   多印這一段，下一個人不必再查一次就知道該去看哪裡。
        results.append(f"boom:{type(e).__name__}: {e}"[:160])


ts = [threading.Thread(target=try_create) for _ in range(2)]
for t in ts:
    t.start()
for t in ts:
    t.join(30)
# ⚠ **看到 `boom:` 不要當成偶發，它幾乎一定是「樹壞了」而不是「競態」。** 而且 pair 的
#   形狀直接告訴你壞在哪一段——`_guard_credentials` 跑在配額交易**之前**，
#   `build_run_kwargs` 跑在**之後**：
#
#     ['boom:X', 'boom:X']   兩條都死 → 炸點在**配額檢查之前**（憑證守門那一段）。
#                            2026-08-07 實例：crypto 的 purpose 重構做到一半，
#                            `auth.cli_token` 這個呼叫端還沒補上 purpose → TypeError。
#     ['boom:X', 'quota']    只有搶到名額的那條死 → 炸點在**配額檢查之後**。
#                            2026-08-07 實例：`config.SESSION_NETWORK` 被刪掉了，而
#                            `sessions.py` 還留著 9 處引用 → AttributeError（ed96517）。
#
#   兩次都不是競態，兩次都是半成品的樹。真的競態長什麼樣：`['ok', 'ok']`（配額沒擋住）
#   或 `['quota', 'quota']`（互相擋掉）——**那兩種才是這條斷言真正要抓的東西**。
check(f"🔴 恰好一個成功、一個被配額擋下（{sorted(results)}）",
      sorted(results) == ["ok", "quota"])
config.MAX_SESSIONS = _orig_max

print("== reconciler 租約：一次只有一個執行者 ==")
check("A 先取得租約", reconciler.acquire_lease("mutex-lease", "A", ttl=60) is True)
check("🔴 B 在租約有效期內拿不到", reconciler.acquire_lease("mutex-lease", "B", ttl=60) is False)
check("A 自己可以續約", reconciler.acquire_lease("mutex-lease", "A", ttl=60) is True)
check("still_leader：A 是、B 不是",
      reconciler.still_leader("mutex-lease", "A") and not reconciler.still_leader("mutex-lease", "B"))

print("== 過期的租約可以被接手，而且只被一個接手者拿走 ==")
# 直接把到期時間改到過去，不真的等 TTL。
from server.db import session_scope  # noqa: E402
from server.models import Lease  # noqa: E402

with session_scope() as s:
    s.get(Lease, "mutex-lease").expires_at = (
        _dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=1))

grabbed: list[str] = []


def contend(owner):
    if reconciler.acquire_lease("mutex-lease", owner, ttl=60):
        grabbed.append(owner)


cs = [threading.Thread(target=contend, args=(o,)) for o in ("B", "C")]
for t in cs:
    t.start()
for t in cs:
    t.join(30)
check(f"🔴 恰好一個接手者拿到（{sorted(grabbed)}）", len(grabbed) == 1)
check("原持有者已經不是 leader", not reconciler.still_leader("mutex-lease", "A"))

db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
