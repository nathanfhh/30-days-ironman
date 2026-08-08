"""jaeger 接線：三個接線點、回收互鎖、compose 契約。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with cryptography python tests/test_jaeger_wiring.py

**不需要 docker daemon**——docker client 是假的。

⚠ **這支存在的理由只有一句：OTLP 是 fail-open。** 接錯、漏接、接了又被拔掉，全都沒有
  任何錯誤訊息；症狀是「那個人完全沒有 trace」，而畫面上還說有在錄。它不像壞掉，像沒人用。
  所以這一整條路只能靠斷言守，靠人手動看是守不住的——手動驗過的那一刻是真的，
  保固期到下一次有人動這幾行為止（2026-08-07 才發現整條路一條測試都沒有）。

守的性質：
  🔴 `attach_jaeger` 只接沒接過的；一張接不上不影響其他張；jaeger 不在就安靜回 0
  🔴 **三個接線點都真的會接**：`ensure_network`（網路剛建好）、`preflight`（控制平面啟動）、
     reconciler（每輪兜底，涵蓋 jaeger 比網路晚起來）
  🔴 `preflight` **兩種網路都接**：使用者的 ＋ 控制平面自己的。只接前者的話探測會失敗，
     控制平面判定「送不到」而根本不設 OTEL env——session 明明到得了卻不送
  🔴 **回收互鎖**：jaeger 掛著會讓 `network.remove()` 直接失敗，所以判準是「除了 jaeger
     沒有別人」；還有 session 在就不准拔 jaeger（拔了那個人的 trace 會靜靜停掉）
  🔴 jaeger 那份 compose **不宣告任何 network**——宣告了就默默佔掉 31 格裡的一格，
     而每一格都換算成「少一個人能同時在線」
"""
import os
import re
import socket as _socket
import sys
import tempfile

os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
_tmp = tempfile.mkdtemp(prefix="claude-pty-jaeger-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

_TESTS = os.path.dirname(os.path.abspath(__file__))
_CPTY = os.path.dirname(_TESTS)
_REPO = os.path.dirname(_CPTY)
sys.path.insert(0, _CPTY)
import docker  # noqa: E402

from server import config, db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
config.SECRET_KEY = "jaeger-wiring-secret"
config.MOUNTS = {}

from server import reconciler, sessions, user_proxy  # noqa: E402

db.reset_engine()
db.init_db()

_fails = 0
def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


JAEGER = user_proxy.jaeger_name()          # OTEL_ENDPOINT 的 hostname，預設 "jaeger"


# --- 假 docker ----------------------------------------------------------------
#
# ⚠ jaeger 的 `attrs` 要**跟著 connect 一起變**。寫成固定值的話「已經接過就不再接」那條
#   斷言會恆真（第二次呼叫時它看到的還是第一次之前的狀態），那是一條永遠不會紅的斷言。

class _FakeJaeger:
    id = "fake-jaeger-id"
    def __init__(self):
        self.on: set[str] = set()
    @property
    def attrs(self):
        return {"NetworkSettings": {"Networks": {n: {} for n in self.on}}}


class _FakeNetwork:
    """一張網路。

    ⚠ `Containers` 在 `reload()` 之前是空的——**這是 docker-py 真實的行為**
      （`networks.list()` 回來的物件不帶容器清單）。故意照著模擬：`only_jaeger_left`
      少了那行 `reload()` 的話，會對一張還有 session 掛著的網路回 True，
      於是 jaeger 被拔掉、`remove()` 再失敗。這裡讓那個錯誤有辦法變紅。
    """
    def __init__(self, name, labels=None, created="", containers=None, connect_fails=False):
        self.name = name
        self._labels = labels or {}
        self._created = created
        self._containers = containers or {}      # {id: name}
        self._reloaded = False
        self._connect_fails = connect_fails
        self.disconnected: list[str] = []
        self.removed = False

    @property
    def attrs(self):
        cs = ({f"id-{n}": {"Name": n} for n in self._containers}
              if self._reloaded else {})
        return {"Labels": self._labels or None, "Created": self._created,
                "Containers": cs}

    def reload(self):
        self._reloaded = True

    def connect(self, cid, **kw):
        if self._connect_fails:
            raise docker.errors.APIError(f"connect 故意失敗：{self.name}")
        _JG.on.add(self.name)
        self._containers[JAEGER] = JAEGER

    def disconnect(self, name, **kw):
        self.disconnected.append(name)
        _JG.on.discard(self.name)
        self._containers.pop(name, None)

    def remove(self):
        # 真的 docker：網路上還掛著容器就拒絕移除。這條就是回收互鎖存在的原因。
        if self._containers:
            raise docker.errors.APIError(
                f"network {self.name} has active endpoints")
        self.removed = True
        _NETS.pop(self.name, None)


_JG = _FakeJaeger()
_NETS: dict[str, _FakeNetwork] = {}
_SELF_NAME = "control-plane-fake"
_SELF_NETS = {"claude-pty_default": {}}


class _FakeSelf:
    """控制平面自己那顆容器（`preflight` 靠 hostname 找到它）。"""
    attrs = {"NetworkSettings": {"Networks": _SELF_NETS}}


class _FakeContainers:
    jaeger_present = True
    def get(self, name, *a, **k):
        if name == JAEGER:
            if not self.jaeger_present:
                raise docker.errors.NotFound(name)
            return _JG
        if name == _SELF_NAME:
            return _FakeSelf()
        raise docker.errors.NotFound(name)
    def list(self, **k): return []


class _FakeNetworks:
    def list(self, names=None, filters=None, **k):
        if names:
            return [_NETS[n] for n in names if n in _NETS]
        if filters and config.NETWORK_LABEL_KEY in str(filters):
            return [n for n in _NETS.values()
                    if config.NETWORK_LABEL_KEY in (n._labels or {})]
        return list(_NETS.values())
    def create(self, name, **k):
        _NETS[name] = _FakeNetwork(name, labels=k.get("labels") or {})
        return _NETS[name]
    def get(self, name):
        if name in _NETS:
            return _NETS[name]
        raise docker.errors.NotFound(name)


class _FakeAPI:
    def remove_container(self, *a, **k): pass


class _FakeClient:
    def __init__(self):
        self.containers = _FakeContainers()
        self.networks = _FakeNetworks()
        self.api = _FakeAPI()


def reset(nets=()):
    _NETS.clear()
    _JG.on.clear()
    for n in nets:
        _NETS[n.name] = n
    c = _FakeClient()
    c.containers.jaeger_present = True
    return c


def user_net(uid, **kw):
    return _FakeNetwork(user_proxy.network_name(uid),
                        labels={config.NETWORK_LABEL_KEY: config.NETWORK_LABEL_VALUE,
                                config.PROXY_OWNER_LABEL: str(uid)}, **kw)


# --- attach_jaeger 本身 --------------------------------------------------------

print("== attach_jaeger：接、不重接、best-effort ==")
c = reset([user_net(1), user_net(2)])
n1, n2 = user_proxy.network_name(1), user_proxy.network_name(2)
check("🔴 兩張都沒接過 → 接了兩張", user_proxy.attach_jaeger(c, [n1, n2]) == 2)
check("🔴 jaeger 真的在這兩張上（回傳數字對、實際沒接上是最糟的假綠）",
      _JG.on == {n1, n2})
check("🔴 再呼叫一次 → 0（已經接過的不重接）",
      user_proxy.attach_jaeger(c, [n1, n2]) == 0)

c = reset([user_net(1), user_net(2)])
user_proxy.attach_jaeger(c, [n1])
check("🔴 只接過一張時，第二次只補另一張",
      user_proxy.attach_jaeger(c, [n1, n2]) == 1 and _JG.on == {n1, n2})

c = reset([user_net(1, connect_fails=True), user_net(2)])
check("🔴 一張接不上，另一張照接（best-effort，不整批放棄）",
      user_proxy.attach_jaeger(c, [n1, n2]) == 1 and _JG.on == {n2})

c = reset([user_net(1)])
c.containers.jaeger_present = False
check("🔴 jaeger 不在 → 回 0 且不拋（它是選配設施，不是缺陷）",
      user_proxy.attach_jaeger(c, [n1]) == 0)

c = reset([user_net(1)])
_saved_ep = config.OTEL_ENDPOINT
try:
    config.OTEL_ENDPOINT = "not-a-url"
    check("OTEL_ENDPOINT 解不出 hostname → 回 0，不拋",
          user_proxy.attach_jaeger(c, [n1]) == 0)
finally:
    config.OTEL_ENDPOINT = _saved_ep

c = reset([user_net(1)])
check("要接的網路根本不存在 → 回 0，不拋（其他張仍要能接）",
      user_proxy.attach_jaeger(c, ["claude-pty-user-999"]) == 0)


# --- 接線點 1：網路剛建好 --------------------------------------------------------

print("== 接線點 1：ensure_network 新建當下就接 ==")
c = reset()
net = user_proxy.ensure_network(c, 7)
n7 = user_proxy.network_name(7)
check("🔴 新建的網路，jaeger 當場接上（否則第一場 session 完全沒有 trace）",
      n7 in _JG.on)
check("網路帶得了主人的 label（回收才認得出是誰的）",
      user_proxy.owner_of(net) == 7)

# 已存在時**不重接**：每次呼叫都接的話，每開一場 session 就多一次 inspect。
# 漏接的情況由 preflight 與 reconciler 那兩道掃描兜底（見 attach_jaeger 的 docstring）。
_JG.on.discard(n7)
_NETS[n7]._containers.pop(JAEGER, None)
user_proxy.ensure_network(c, 7)
check("🔴 網路已存在 → 不再接一次（兜底交給 preflight／reconciler）",
      n7 not in _JG.on)


# --- 接線點 2：控制平面啟動 ------------------------------------------------------

print("== 接線點 2：preflight 兩種網路都接 ==")
_calls: list[list[str]] = []
_real_attach = user_proxy.attach_jaeger
_real_from_env = docker.from_env
_real_hostname = _socket.gethostname
try:
    user_proxy.attach_jaeger = lambda cl, names: _calls.append(list(names)) or 0
    c = reset([user_net(3), user_net(4)])
    docker.from_env = lambda *a, **k: c
    _socket.gethostname = lambda: _SELF_NAME
    sessions.preflight()
finally:
    user_proxy.attach_jaeger = _real_attach
    docker.from_env = _real_from_env
    _socket.gethostname = _real_hostname

asked = set(_calls[-1]) if _calls else set()
check("🔴 preflight 有接（開機時 jaeger 可能早就在跑，沒人回頭補就永遠漏）", bool(_calls))
check("🔴 每一張使用者網路都在名單裡",
      {user_proxy.network_name(3), user_proxy.network_name(4)} <= asked)
check("🔴 控制平面自己那張也在名單裡（少了它，_jaeger_reachable 會探不到，"
      "於是連到得了的 session 都不設 OTEL env）",
      "claude-pty_default" in asked)


# --- 接線點 3：reconciler 每輪兜底 ------------------------------------------------

print("== 接線點 3：reconciler 每輪兜底（jaeger 比網路晚起來）==")
def _iso(label, fn, *a, **kw):
    return fn(*a, **kw)

_calls.clear()
try:
    user_proxy.attach_jaeger = lambda cl, names: _calls.append(list(names)) or 0
    c = reset([user_net(5)])          # 沒有 Created ⇒ 還在寬限期內，這輪不會被收
    reconciler._converge_proxies(c, {}, _iso, leading=lambda: True)
finally:
    user_proxy.attach_jaeger = _real_attach
check("🔴 lead 時每輪接一次（新建與開機都是事件驅動，jaeger 在兩者之間重啟沒人補）",
      bool(_calls) and user_proxy.network_name(5) in _calls[-1])

_calls.clear()
try:
    user_proxy.attach_jaeger = lambda cl, names: _calls.append(list(names)) or 0
    c = reset([user_net(5)])
    reconciler._converge_proxies(c, {}, _iso, leading=lambda: False)
finally:
    user_proxy.attach_jaeger = _real_attach
check("🔴 租約不在自己手上就不接（兩個 reconciler 並存時只有 leader 動手）",
      not _calls)


# --- 回收互鎖 -------------------------------------------------------------------

print("== 回收互鎖：jaeger 掛著會讓 network.remove() 失敗 ==")
OLD = "2020-01-01T00:00:00.000000000Z"      # 早就過了 ORPHAN_GRACE

c = reset([user_net(6, created=OLD)])
n6 = user_proxy.network_name(6)
user_proxy.attach_jaeger(c, [n6])           # 只有 jaeger 掛著
check("🔴 只剩 jaeger → only_jaeger_left 為真", user_proxy.only_jaeger_left(_NETS[n6]))
reaped = reconciler._reap_user_networks(c, set(), _iso, leading=lambda: True)
check("🔴 沒人用的網路真的被收掉（收不掉的話位址池只出不進）", reaped == 1)
check("🔴 收之前先把 jaeger 拔下來（沒拔的話 remove 直接失敗）",
      JAEGER in _NETS.get(n6, _FakeNetwork("x")).disconnected or n6 not in _NETS)

# 還有 session 掛著：不准拔、不准收。
c = reset([user_net(6, created=OLD, containers={"session-abc": "session-abc"})])
user_proxy.attach_jaeger(c, [n6])
net6 = _NETS[n6]
check("🔴 還有 session 在 → only_jaeger_left 為假",
      not user_proxy.only_jaeger_left(net6))
reaped = reconciler._reap_user_networks(c, set(), _iso, leading=lambda: True)
check("🔴 一張都沒收（上面還有人在用）", reaped == 0)
check("🔴 而且**沒有**把 jaeger 拔掉——拔了那個人的 trace 會靜靜停掉，"
      "要等 reconciler 下一輪才補回來",
      net6.disconnected == [] and n6 in _JG.on)

# 有活著 session 的使用者，網路一律不動（跟 GitLab 開不開、有沒有 PAT 無關）。
c = reset([user_net(6, created=OLD)])
user_proxy.attach_jaeger(c, [n6])
reaped = reconciler._reap_user_networks(c, {6}, _iso, leading=lambda: True)
check("🔴 主人還有活著的 session → 不收", reaped == 0 and n6 in _NETS)

# detach 之後真的不在上面了（jaeger_on_network 是 telemetry 座標的依據）
c = reset([user_net(8)])
n8 = user_proxy.network_name(8)
user_proxy.attach_jaeger(c, [n8])
check("jaeger_on_network：接上後為真", user_proxy.jaeger_on_network(c, n8))
user_proxy.detach_jaeger(c, n8)
check("🔴 jaeger_on_network：拔掉後為假（座標據此判定要不要設 OTEL env）",
      not user_proxy.jaeger_on_network(c, n8))


# --- compose 契約 ---------------------------------------------------------------

print("== compose 契約：jaeger 不佔位址池的那一格 ==")
COMPOSE = os.path.join(_REPO, "opentelemetry", "jaeger-compose.yaml")
check("compose 檔在（找不到就不是跳過，是這條契約沒有人在守）",
      os.path.isfile(COMPOSE))
if os.path.isfile(COMPOSE):
    raw = open(COMPOSE, encoding="utf-8").read()
    # 去掉註解行再斷言：檔頭那段說明**引用**了 `external: true`、`<專案>_default`
    # 來講「為什麼不這樣做」，帶著註解比對會拿說明當成設定。
    code = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))
    check("🔴 用 network_mode: bridge（待在預設橋接，那張本來就存在、不佔配額）",
          re.search(r"^\s*network_mode:\s*bridge\s*$", code, re.M) is not None)
    check("🔴 沒有任何 networks: 宣告——有的話 compose 會建一張 <專案>_default，"
          "而整台機器只有 31 張，每一張都是「少一個人能同時在線」",
          re.search(r"^\s*networks:", code, re.M) is None)
    check("🔴 容器名釘死（attach_jaeger 靠 OTEL_ENDPOINT 的 hostname 找它，"
          "名字漂掉就 NotFound → 安靜回 0 → 全站沒有 trace）",
          re.search(rf"^\s*container_name:\s*{re.escape(JAEGER)}\s*$", code, re.M)
          is not None)


db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
