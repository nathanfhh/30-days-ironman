"""`sessions.create()` 的呼叫形狀：create → start 拆開、絕不退回一發 `containers.run()`。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography \
        python tests/test_create_ordering.py

**不需要 docker daemon**——整個 docker client 是假的，只記錄呼叫順序。

## 為什麼要有這一支

**session 需要的每一張網路，都必須在 `start` 之前就位。** 防火牆放行的是 entrypoint
起跑那一刻的路由快照，start 之後才接上的網路封包會被 REJECT，而且永遠不會好
（reconciler 補得了網路、補不了 iptables）。

使用者網路是靠 `containers.create(network=…)` 在建立當下掛上的（ADR 0016），所以這支
釘四件事：

  1. 走 `containers.create` + `start`，不是一發 `containers.run()`
  2. `create` 的參數裡帶著**這個使用者自己那張網**
  3. 那張網在 `containers.create` 之前就建好了
  4. **沒有任何 `network.connect` 發生在 `container.start` 之後**

第 4 條是**上位不變量**，不是「一定要有 connect」。這支原本釘的是
`create < connect < start`——那條在網路改由 `network=` 參數帶上之後就失效了（正常路徑上
根本沒有 connect）。但它守的東西還在：日後有人要加第二張網（第二顆代理、另一個
collector），唯一放得下的位置仍然是 create 與 start 中間，挪到 start 之後就是永久且
無聲的失效。所以斷言從「必須有」改成「有的話一定在 start 之前」。

⚠ 這支刻意**不驗任何真實的網路行為**（連得到／連不到）。它只看呼叫序與參數，所以不需要
  daemon、不花時間、不碰使用者的任何東西——因此可以放進 quick 測試組，每次都跑得到。
  真正的隔離性由 `test_network_isolation.py` 用真的容器與真的 listener 驗。
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

config.HOST_HOME = _tmp

db.reset_engine()
db.init_db()

# 憑證＝DB 裡的 setup-token（唯一來源，D 階段起不再讀任何 host 憑證檔）。
# 這批測試的 session 都掛在 system 使用者名下，給它種一個測試值就過得了 create() 的守門。
from server import auth as _auth_seed  # noqa: E402
from server import sessions as _sessions_seed  # noqa: E402

_auth_seed.set_cli_token(_sessions_seed.ensure_system_user(), "sk-test-setup-token")

from server import auth, user_proxy  # noqa: E402
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
        self.create_kwargs = None      # 建立當下帶了什麼（要驗 network= 就在裡面）

    def create(self, image, **kw):
        # ⚠ 這是本測試的核心：`create()` 必須存在。改回 `containers.run()` 的話這裡不會被
        #   呼叫、下面的順序斷言會直接紅——那正是這支要釘的東西。
        self._rec.log("containers.create")
        self.create_kwargs = kw
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
    auth.set_cli_token(uid, "sk-test-setup-token")   # create() 入口先驗憑證，這裡測的是後面的順序

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

    # 這一輪沒有 GitLab（預設關閉），但**網路照建照用**——它是 session 的家，不是 GitLab
    # 的配件。這條在下面「沒有 PAT」那段還會再驗一次。
    want_net = user_proxy.network_name(uid)
    check("🔴 network 在 containers.create 的參數裡（不是事後 connect 上去的）",
          (mgr._docker.containers.create_kwargs or {}).get("network") == want_net)
    check("🔴 而且那張網在 containers.create 之前就建好了",
          0 <= rec.index("network.create") < rec.index("containers.create"))

    # --- 網路就位的時機（ADR 0016）------------------------------------------------
    #
    # 上面那段只證明 create 與 start 是拆開的。**拆開是手段，不是目的**——目的是中間那個
    # 縫隙：憑證要在那裡送進去，而任何**額外**的網路也只能在那裡接。
    #
    # ⚠ 下面那條 `network.connect` 的斷言是**否定式**的：不是「必須有 connect」（正常路徑
    #   上已經沒有了，網路改由 create 的參數帶上），而是「有的話一定在 start 之前」。
    #   有人日後加第二張網並挪到 start 之後（看起來更「自然」：容器都起來了再接網路），
    #   這條要紅。那個壞法沒有錯誤訊息，只有「連不到」，而且永遠不會好。
    print("\n== 有 PAT：代理先備好，而且任何 connect 都不得晚於 start ==")
    config.GITLAB_HOST = "gitlab.example.com"
    auth.set_gitlab_pat(uid, "glpat-OrderingTestOnly01")

    rec2 = _Rec()
    mgr2 = SessionManager()
    mgr2._docker = FakeClient(rec2)        # noqa: SLF001 — 就是要換掉它
    mgr2.create(user_id=uid)
    print("  " + " → ".join(rec2.calls))

    i_netcreate = rec2.index("network.create")
    i_create = rec2.index("containers.create")
    i_start = rec2.index("container.start")

    check("網路先建起來（容器要以它為 network 參數，得先存在）", 0 <= i_netcreate < i_create)
    check("代理容器也在 session 容器之前就備好",
          0 <= rec2.index("proxy.start") < i_create)
    check("🔴 session 容器建立當下就掛在該使用者的網路上",
          (mgr2._docker.containers.create_kwargs or {}).get("network") == want_net)
    check("🔴 **沒有任何 network.connect 晚於 container.start**"
          "（挪到 start 之後就是永久且無聲的失效）",
          all(i < i_start for i, c in enumerate(rec2.calls) if c == "network.connect"))
    check("🔴 設定用 put_archive 送進代理，而且在它啟動之前"
          "（bind mount 的話之後就換不掉，熱重載的前提沒了）",
          "proxy.put_archive" in rec2.calls
          and rec2.index("proxy.put_archive") < rec2.index("proxy.start"))

    # 沒設 PAT 的人：**不建代理，但網路照建**——他的 session 一樣需要一個住的地方，而且
    # 一樣不可以跟別人共用。
    # ⚠ 這兩條原本是反過來寫的（「一張網路都沒建」）。那是把網路當成 GitLab 的配件，
    #   而在那個形狀下，沒設 PAT 的人只能落到共用網路或預設 bridge。
    print("\n== 沒有 PAT：不建代理，但網路照建、session 照開 ==")
    auth.set_gitlab_pat(uid, "")
    rec3 = _Rec()
    mgr3 = SessionManager()
    mgr3._docker = FakeClient(rec3)        # noqa: SLF001
    mgr3.create(user_id=uid)
    print("  " + " → ".join(rec3.calls))
    check("🔴 session 照樣建起來並啟動（GitLab 不通是少一個功能，不是這場沒用）",
          "containers.create" in rec3.calls and "container.start" in rec3.calls)
    check("一顆代理都沒建", "proxy.create" not in rec3.calls)
    check("🔴 但網路照建（沒 PAT 不等於可以跟別人共用一張網）",
          "network.create" in rec3.calls)
    check("🔴 而且 session 真的掛在上面",
          (mgr3._docker.containers.create_kwargs or {}).get("network") == want_net)

    # GitLab 功能整個關掉也一樣。⚠ 這是**部署層**的開關，不是使用者的選擇——關掉之後
    # 若網路跟著消失，那個部署的每一場 session 都會落回共用網路，而它不會有任何症狀。
    print("\n== GitLab 功能整個關閉：網路仍然是 per-user ==")
    config.GITLAB_HOST = ""
    rec4 = _Rec()
    mgr4 = SessionManager()
    mgr4._docker = FakeClient(rec4)        # noqa: SLF001
    mgr4.create(user_id=uid)
    print("  " + " → ".join(rec4.calls))
    check("🔴 功能關閉也照建自己的網路", "network.create" in rec4.calls)
    check("🔴 功能關閉也照掛自己的網路",
          (mgr4._docker.containers.create_kwargs or {}).get("network") == want_net)
    check("一顆代理都沒建", "proxy.create" not in rec4.calls)

    # --- 位址池滿：**開不了場**，不是降級 -----------------------------------------
    #
    # ⚠ 這條是這次改動最重要的行為變更。位址池滿以前被吞掉當成「少一個 GitLab」，
    #   session 照開——那在網路只是代理的配件時說得通。現在網路是 session 的家，
    #   唯一的「降級」選項是把人塞進一張共用的網，而那會無聲地取消掉整個隔離設計。
    # ⚠ 也要驗**訊息本身**：使用者看到的必須是下一步，不是 docker 的原文。
    print("\n== 位址池滿：讓 session 開不起來，並講出人聽得懂的下一步 ==")

    class PoolFullNetworks(FakeNetworks):
        def create(self, name, **kw):
            raise docker.errors.APIError(
                "all predefined address pools have been fully subnetted")

    rec5 = _Rec()
    mgr5 = SessionManager()
    mgr5._docker = FakeClient(rec5)        # noqa: SLF001
    mgr5._docker.networks = PoolFullNetworks(rec5)
    _err = None
    try:
        mgr5.create(user_id=uid)
    except Exception as e:      # noqa: BLE001 — 就是要驗它拋
        _err = e
    check("🔴 位址池滿 → 直接失敗，不是靜靜降級開場",
          _err is not None and type(_err).__name__ == "SessionError")
    check("🔴 完全沒有建立容器（不可以退回任何共用網路）",
          "containers.create" not in rec5.calls)
    check("🔴 訊息講的是下一步，不是 docker 的原文",
          _err is not None and "address pools" not in str(_err)
          and "session" in str(_err) and "daemon.json" in str(_err))

finally:
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
