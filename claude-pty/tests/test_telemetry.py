"""telemetry 降級與座標誠實性（H 階段）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with cryptography python tests/test_telemetry.py

**不需要 docker daemon**——docker client 是假的。守的性質：
  🔴 探不到 jaeger 時**照開場**（不 fail-closed：telemetry 是觀察不是控制）
  🔴 探不到時**不設** OTEL env（不送去一個沒人接的地方）
  🔴 **探得到但 jaeger 沒接上這個人的網路，一樣算沒開成**（ADR 0016：探測從控制平面發出，
     證明的是控制平面那張網到得了，跟 session 那張網是兩回事）
  🔴 那個環境座標**不准說謊**：開成記 active、沒開成記「要求了但沒開成」、沒要求記 off
     ——它的用途是事後比對，記謊會污染後續所有分析
"""
import os
import socket
import sys
import tempfile
from urllib.parse import urlparse

os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
_tmp = tempfile.mkdtemp(prefix="claude-pty-telemetry-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402

from server import config, db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
config.SECRET_KEY = "telemetry-secret"
config.MOUNTS = {}

from server import auth, sessions  # noqa: E402
from server.sessions import Profile, SessionManager  # noqa: E402

db.reset_engine()
db.init_db()

_fails = 0
def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


# --- 假 docker：只實作 create() 會碰到的那幾支 ------------------------------------
#
# ⚠ **jaeger 有沒有接上這張網，是這個假 client 要模擬的第二個維度。** per-user 之後
#   「送不送得到」由兩件事共同決定：控制平面探不探得到（`_jaeger_reachable`）、以及
#   jaeger 在不在**這個使用者那張網**上。只模擬前者的話，下面那條「探得到但沒接上」
#   的測試根本測不出來。
_JAEGER_ATTACHED = True          # 由各段落切換

class _FakeContainer:
    id = "fake-container-id"
    def start(self): pass
    def exec_run(self, *a, **k): return (1, b"")

class _FakeJaeger:
    """扮演 jaeger 容器。`attrs` 是 `jaeger_on_network` 唯一會讀的東西。"""
    id = "fake-jaeger-id"
    @property
    def attrs(self):
        nets = {n: {} for n in _FAKE_NETS} if _JAEGER_ATTACHED else {}
        return {"NetworkSettings": {"Networks": nets}}

class _FakeContainers:
    def create(self, image, **kw): return _FakeContainer()
    def get(self, name, *a, **k):
        # OTEL_ENDPOINT 的 hostname 就是 jaeger 的容器名（預設 "jaeger"）。
        if name == urlparse(config.OTEL_ENDPOINT).hostname:
            return _FakeJaeger()
        raise docker.errors.NotFound(name)
    def list(self, **k): return []

class _FakeNetwork:
    def __init__(self, name): self.name = name
    def connect(self, *a, **k): pass
    def remove(self): pass

_FAKE_NETS: dict[str, _FakeNetwork] = {}

class _FakeNetworks:
    def list(self, names=None, **k):
        if names:
            return [_FAKE_NETS[n] for n in names if n in _FAKE_NETS]
        return list(_FAKE_NETS.values())
    def create(self, name, **k):
        _FAKE_NETS[name] = _FakeNetwork(name)
        return _FAKE_NETS[name]
    def get(self, name):
        if name in _FAKE_NETS:
            return _FAKE_NETS[name]
        raise docker.errors.NotFound(name)

class _FakeAPI:
    def resize(self, *a, **k): pass
    def remove_container(self, *a, **k): pass

class _FakeImages:
    def get(self, *a, **k): raise docker.errors.NotFound("x")

class _FakeClient:
    def __init__(self):
        self.containers = _FakeContainers()
        self.networks = _FakeNetworks()
        self.api = _FakeAPI()
        self.images = _FakeImages()


uid = auth.create_user("tel-user", "tel-password-1")["id"]
auth.set_cli_token(uid, "sk-test-setup-token")   # 過 create() 入口的憑證守門


def make_mgr():
    mgr = SessionManager()
    mgr._docker = _FakeClient()
    return mgr


def stored_profile(sid):
    from server.db import session_scope
    from server.models import Session as SessionRow
    with session_scope() as s:
        return dict(s.get(SessionRow, sid).profile)


# 攔 build_run_kwargs：記下它實際收到的 profile（＝真正要送進容器的那份）
_captured = {}
_orig_brk = sessions.build_run_kwargs
def _spy_brk(name, sid, profile, user_id):
    _captured[sid] = profile
    return _orig_brk(name, sid, profile, user_id)
sessions.build_run_kwargs = _spy_brk


print("== _jaeger_reachable：連得上/連不上 ==")
# 真的開一個 listener 當作「jaeger 在」
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
live_port = srv.getsockname()[1]
_saved_ep = config.OTEL_ENDPOINT
try:
    config.OTEL_ENDPOINT = f"http://127.0.0.1:{live_port}"
    check("🔴 listener 在 → 探得到", sessions._jaeger_reachable() is True)
    srv.close()
    check("🔴 listener 關掉 → 探不到（不拋錯，回 False）",
          sessions._jaeger_reachable() is False)
    config.OTEL_ENDPOINT = "http://nonexistent.invalid:4317"
    check("解析不了的 host → False（不拋錯）", sessions._jaeger_reachable() is False)
finally:
    config.OTEL_ENDPOINT = _saved_ep
    with __import__("contextlib").suppress(Exception):
        srv.close()


print("== 沒要求 telemetry：座標 off，不探測也不設 env ==")
mgr = make_mgr()
sid = mgr.create(user_id=uid, profile=Profile(telemetry=False))["id"]
prof = stored_profile(sid)
check("stored telemetry=False", prof["telemetry"] is False)
check("座標沒有 telemetry_active 這個鍵（沒要求就不談成不成）",
      "telemetry_active" not in prof)
check("build_run_kwargs 收到的 profile 也是不送",
      _captured[sid].telemetry is False)


print("== 要求 telemetry 且 jaeger 探得到：真的送、座標記 active ==")
_saved = sessions._jaeger_reachable
try:
    sessions._jaeger_reachable = lambda: True
    mgr = make_mgr()
    sid = mgr.create(user_id=uid, profile=Profile(telemetry=True))["id"]
finally:
    sessions._jaeger_reachable = _saved
prof = stored_profile(sid)
check("🔴 座標 telemetry=True（要求）", prof["telemetry"] is True)
check("🔴 座標 telemetry_active=True（真的開成）", prof["telemetry_active"] is True)
env = _orig_brk("c", sid, _captured[sid], uid)["environment"]
check("🔴 送進容器的 profile 仍要 telemetry → 設了 OTEL env",
      _captured[sid].telemetry is True and env.get("NCR_OTEL") == "1")


print("== 要求 telemetry 但 jaeger 探不到：照開場、不設 env、座標說實話 ==")
_saved = sessions._jaeger_reachable
try:
    sessions._jaeger_reachable = lambda: False
    mgr = make_mgr()
    info = mgr.create(user_id=uid, profile=Profile(telemetry=True))
    sid = info["id"]
finally:
    sessions._jaeger_reachable = _saved
check("🔴 session 照樣建起來了（不 fail-closed）", info.get("id") is not None)
prof = stored_profile(sid)
check("🔴 座標 telemetry=True 保留（使用者確實要求了）", prof["telemetry"] is True)
check("🔴 座標 telemetry_active=False（要求了但沒開成——不記謊成 on）",
      prof["telemetry_active"] is False)
# 送進容器的 profile 被降級成不送，於是不會設 OTEL env（送去沒人接的地方是錯的）
check("🔴 送進容器的 profile 已降級成不送 telemetry",
      _captured[sid].telemetry is False)
env = _orig_brk("c", sid, _captured[sid], uid)["environment"]
check("🔴 因此不設 OTEL env（NCR_OTEL 不在）", "NCR_OTEL" not in env)


print("== 探得到、但 jaeger 沒接上這個人的網路：一樣算沒開成（ADR 0016）==")
# ⚠ 這是 per-user 網路帶進來的**新的失敗模式**，而且是最難查的一種：控制平面自己那張網
#   到得了 jaeger，探測回 True，於是 OTEL env 照設——但 session 待在使用者自己那張網上，
#   jaeger 不在那裡，**一筆 trace 都送不出去，而 OTLP 是 fail-open，完全沒有錯誤訊息**。
#   畫面上還會說「有在錄」。座標說謊比沒有座標更糟：事後比對會拿一堆空的 trace 當基準。
_saved = sessions._jaeger_reachable
try:
    sessions._jaeger_reachable = lambda: True      # 控制平面探得到
    _JAEGER_ATTACHED = False                       # 但沒接上使用者那張網
    mgr = make_mgr()
    info = mgr.create(user_id=uid, profile=Profile(telemetry=True))
    sid = info["id"]
finally:
    sessions._jaeger_reachable = _saved
    _JAEGER_ATTACHED = True
check("🔴 session 照樣建起來了（不 fail-closed）", info.get("id") is not None)
prof = stored_profile(sid)
check("🔴 座標 telemetry_active=False（探得到不等於送得到）",
      prof["telemetry_active"] is False)
check("🔴 送進容器的 profile 已降級成不送",
      _captured[sid].telemetry is False)
check("🔴 不設 OTEL env（送去一個到不了的地方比不送更糟：畫面會說有在錄）",
      "NCR_OTEL" not in _orig_brk("c", sid, _captured[sid], uid)["environment"])

sessions.build_run_kwargs = _orig_brk
db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
