"""`sessions.create()` 的呼叫形狀：create → start 拆開、絕不退回一發 `containers.run()`。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography \
        python tests/test_create_ordering.py

**不需要 docker daemon**——整個 docker client 是假的，只記錄呼叫順序。

## 為什麼要有這一支

`create` 與 `start` 之間是「把容器接上該接的東西」的唯一時間點：防火牆放行的是
entrypoint 起跑那一刻的路由快照，start 之後才接上的網路封包會被 REJECT，而且永遠
不會好（reconciler 補得了網路、補不了 iptables）。有人把它改回一發 `containers.run()`
時，這支要紅。

⚠ 這支刻意**不驗任何網路行為**。它只看呼叫序，所以不需要 daemon、不花時間、不碰使用者的
  任何東西——因此可以放進 quick 測試組，每次都跑得到。
"""
import os
import sys
import tempfile

os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
_tmp = tempfile.mkdtemp(prefix="claude-pty-createorder-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402

from server import config, db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
config.SECRET_KEY = "create-order-secret"
config.MOUNTS = {}

# 憑證來源指進 tmpdir 並放一份假憑證：這支測試驗的是**呼叫序**，不該因 host 上
# 有沒有真憑證而紅綠（`_guard_credentials` 在 create() 的入口就會擋）。兩個來源
# 都要指走——CREDENTIALS_HOST 指向假檔，HOST_HOME 指向 tmpdir 讓第二來源撲空。
import json    # noqa: E402
import time    # noqa: E402

config.CREDENTIALS_HOST = os.path.join(_tmp, ".credentials.json")
config.HOST_HOME = _tmp
with open(config.CREDENTIALS_HOST, "w", encoding="utf-8") as _f:
    json.dump({"claudeAiOauth": {
        "accessToken": "x", "refreshToken": "x",
        "expiresAt": int((time.time() + 3600) * 1000),
        "refreshTokenExpiresAt": int((time.time() + 30 * 86400) * 1000),
        "subscriptionType": "max"}}, _f)

db.reset_engine()
db.init_db()

from server import auth  # noqa: E402
from server.sessions import SessionManager  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


# --- 假的 docker：只記錄「誰被呼叫、什麼順序」 --------------------------------------

class _Rec:
    def __init__(self):
        self.calls: list[str] = []

    def log(self, what):
        self.calls.append(what)

    def index(self, what):
        return self.calls.index(what) if what in self.calls else -1


class FakeContainer:
    def __init__(self, rec, cid="sess-container-id"):
        self._rec, self.id, self.name, self.status = rec, cid, "fake", "created"
        self.attrs = {"State": {"StartedAt": "2026-07-29T00:00:00Z"}, "Created": ""}
        self.labels = {}

    def start(self):
        self._rec.log("container.start")

    def exec_run(self, *a, **kw):
        return (1, b"")


class FakeNetwork:
    def __init__(self, rec, name):
        self._rec, self.name = rec, name
        self.attrs = {"Labels": {}, "Created": "2026-07-29T00:00:00Z"}

    def connect(self, *a, **kw):
        self._rec.log("network.connect")

    def remove(self):
        pass


class FakeNetworks:
    def __init__(self, rec):
        self._rec, self._made = rec, {}

    def list(self, names=None, **kw):
        if names:
            return [self._made[n] for n in names if n in self._made]
        return list(self._made.values())

    def create(self, name, **kw):
        self._rec.log("network.create")
        self._made[name] = FakeNetwork(self._rec, name)
        return self._made[name]

    def get(self, name):
        if name in self._made:
            return self._made[name]
        raise docker.errors.NotFound(name)


class FakeContainers:
    def __init__(self, rec):
        self._rec = rec

    def create(self, image, **kw):
        # ⚠ 這是本測試的核心：`create()` 必須存在。改回 `containers.run()` 的話這裡不會被
        #   呼叫、下面的順序斷言會直接紅——那正是這支要釘的東西。
        self._rec.log("containers.create")
        return FakeContainer(self._rec)

    def run(self, *a, **kw):
        self._rec.log("containers.run")      # 改回一發 run 的話會留下這個記號
        return FakeContainer(self._rec)

    def get(self, name):
        raise docker.errors.NotFound(name)

    def list(self, **kw):
        return []


class FakeAPI:
    def __init__(self, rec):
        self._rec = rec

    def create_container(self, *a, **kw):
        self._rec.log("proxy.create")
        return {"Id": "proxy-id"}

    def put_archive(self, *a, **kw):
        self._rec.log("proxy.put_archive")
        return True

    def start(self, *a, **kw):
        self._rec.log("proxy.start")

    def resize(self, *a, **kw):
        self._rec.log("api.resize")

    # docker-py 低階 API 組參數用的三支。這裡只要回一個可放進 kwargs 的東西就好。
    def create_host_config(self, **kw):
        return dict(kw)

    def create_networking_config(self, cfg):
        return dict(cfg)

    def create_endpoint_config(self, **kw):
        return dict(kw)

    def remove_container(self, *a, **kw):
        pass

    def inspect_container(self, *a, **kw):
        return {"State": {"StartedAt": ""}}


class FakeImages:
    def get(self, *a, **kw):
        raise docker.errors.NotFound("no image")


class FakeClient:
    def __init__(self, rec):
        self.containers = FakeContainers(rec)
        self.networks = FakeNetworks(rec)
        self.api = FakeAPI(rec)
        self.images = FakeImages()


try:
    uid = auth.create_user("order-user", "create-order-pw-1")["id"]

    rec = _Rec()
    mgr = SessionManager()
    mgr._docker = FakeClient(rec)          # noqa: SLF001 — 就是要換掉它
    mgr.create(user_id=uid)

    print("== 呼叫序 ==")
    print("  " + " → ".join(rec.calls))

    print("== create 與 start 拆開，順序正確 ==")
    check("走的是 containers.create（不是 run）", "containers.create" in rec.calls)
    check("完全沒有呼叫 containers.run", "containers.run" not in rec.calls)
    check("containers.create 在 container.start 之前",
          0 <= rec.index("containers.create") < rec.index("container.start"))

finally:
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
