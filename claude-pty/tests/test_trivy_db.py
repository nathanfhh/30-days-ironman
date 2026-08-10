"""trivy DB 更新的 regression（假 docker client，不真的下載）。

    uv run --with flask --with docker --with sqlalchemy python tests/test_trivy_db.py

這支守的核心命題：**它永遠不擋開場**。六種結果全部要回一個 dict，一個都不准拋——
因為呼叫它的地方是 `sessions.create()` 的熱路徑，而 A2 沒有 DB 是可降級的，開不了場不是。
"""
import datetime as _dt
import os
import sys
import tempfile

# ⚠ 自己的 DB：不隔離就會寫進使用者**真實的** claude-pty.db（租約住在 DB 裡）。
# ⚠ **真正生效的是下面那行 `config.DB_URL = ...` 加 `reset_engine()`**，不是這個 env
#   ——config 讀的是 `CLAUDE_PTY_DB_PATH`（檔案路徑），沒有 `CLAUDE_PTY_DB_URL` 這個東西。
#   這裡設 env 只是與其他測試檔的寫法一致；**刪掉下面那兩行就會寫進真的 DB**。
_tmp = tempfile.mkdtemp(prefix="claude-pty-trivydb-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402

from server import config, db, leases, trivy_db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()
db.init_db()

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


# --- 假 docker client -------------------------------------------------------------

class _FakeContainers:
    def __init__(self, outer):
        self._outer = outer

    def run(self, image, **kw):
        self._outer.calls.append({"image": image, **kw})
        if self._outer.raises is not None:
            raise self._outer.raises
        return b""


class FakeClient:
    def __init__(self, raises=None):
        self.calls = []
        self.raises = raises
        self.containers = _FakeContainers(self)


def _container_error(exit_status=1):
    return docker.errors.ContainerError(
        container=None, exit_status=exit_status, command="trivy",
        image=config.IMAGE, stderr=b"boom")


# --- 假的 cache 目錄 ---------------------------------------------------------------

# ⚠ cache 是 named volume，控制平面**看不到它的內容**（ADR 0018），所以這裡完全不造
#   假的 cache 目錄——能被測的只有控制平面自己持有的那個時間戳。
_stampdir = tempfile.mkdtemp(prefix="claude-pty-trivystamp-")
config.TRIVY_DB_STAMP = os.path.join(_stampdir, "trivy-db-updated-at")


def set_stamp(when: _dt.datetime | None):
    """把「上次更新成功」設成某個時間；None = 從來沒成功過。"""
    if when is None:
        if os.path.exists(config.TRIVY_DB_STAMP):
            os.remove(config.TRIVY_DB_STAMP)
        return
    with open(config.TRIVY_DB_STAMP, "w", encoding="utf-8") as f:
        f.write(when.isoformat())
    ts = when.timestamp()
    os.utime(config.TRIVY_DB_STAMP, (ts, ts))


def clear_lease():
    leases.release_lease(trivy_db.LEASE_NAME, "someone-else")
    leases.release_lease(trivy_db.LEASE_NAME, trivy_db._owner())


_recent = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)
_old = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=48)

print("== 開關：關掉就什麼都不做 ==")
config.TRIVY_DB_UPDATE = False
c = FakeClient()
r = trivy_db.update(c)
check("status=disabled", r["status"] == "disabled")
check("🔴 一顆容器都沒起", c.calls == [])
config.TRIVY_DB_UPDATE = True

print("\n== 節流：距上次成功更新還在間隔內就不起容器 ==")
set_stamp(_recent)
clear_lease()
c = FakeClient()
r = trivy_db.update(c)
check("status=fresh", r["status"] == "fresh")
check("🔴 沒起容器（這就是節流的全部價值）", c.calls == [])

print("\n== 超過間隔：才會真的去更新 ==")
set_stamp(_old)
clear_lease()
c = FakeClient()
r = trivy_db.update(c)
check("status=ok", r["status"] == "ok")
check("起了一顆容器", len(c.calls) == 1)
kw = c.calls[0] if c.calls else {}
check("用的是 session 的 image", kw.get("image") == config.IMAGE)
check("entrypoint 換成 bash（繞過啟動選單）", kw.get("entrypoint") == "bash")
check("命令帶了 timeout 硬上限，且與設定一致",
      f"timeout -k 10 {config.TRIVY_DB_TIMEOUT}" in " ".join(kw.get("command") or []))
check("跑的是 --download-db-only",
      "--download-db-only" in " ".join(kw.get("command") or []))
# ⚠ key 是 **volume 名**不是 host 路徑：host 路徑會把 cache 的擁有權綁回部署者的 uid，
#   那正是 ADR 0018 要拆掉的耦合。
check("cache 掛的是 named volume，落點固定",
      (kw.get("volumes") or {}).get(config.TRIVY_CACHE_VOLUME, {}).get("bind")
      == "/home/nathan/.cache/trivy")
check("🔴 沒有任何 host 路徑出現在 volumes 裡",
      not any(str(k).startswith("/") for k in (kw.get("volumes") or {})))
check("用完即棄（remove=True）", kw.get("remove") is True)
# ⚠ 這條是真的踩得到：帶了 session label 的話，reconciler 的孤兒清理會把這顆
#   「有 label 卻不在 DB 裡」的容器當成孤兒。--rm 很快就走，但那是在賭時序。
check("🔴 沒有帶任何 label（不能進 reconciler 的視野）",
      not kw.get("labels"))

print("\n== 從來沒更新過：要去更新，不是當成新鮮 ==")
set_stamp(None)
clear_lease()
c = FakeClient()
r = trivy_db.update(c)
check("🔴 沒有時間戳就去更新（fail-safe 的方向要對）",
      r["status"] == "ok" and len(c.calls) == 1)
check("🔴 更新成功要寫下時間戳（否則每一場都會重跑一次）",
      os.path.exists(config.TRIVY_DB_STAMP))
c2 = FakeClient()
check("🔴 而且下一次就會被節流掉", trivy_db.update(c2)["status"] == "fresh" and c2.calls == [])

print("\n== 租約：別人持有時跳過，而且不等 ==")
set_stamp(_old)
clear_lease()
leases.acquire_lease(trivy_db.LEASE_NAME, "someone-else", 300)
c = FakeClient()
r = trivy_db.update(c)
check("status=skipped", r["status"] == "skipped")
check("🔴 沒起第二顆容器（否則就是重複下載 103 MiB）", c.calls == [])
clear_lease()

print("\n== 租約用完要還：下一次要能繼續 ==")
# ⚠ 兩次之間要把時間戳撥回去，否則第二次會被**節流**擋掉而回 fresh——那樣這條就不是在
#   測租約了，是在測節流，而且會是綠的。兩個機制要分開測。
set_stamp(_old)
clear_lease()
trivy_db.update(FakeClient())
set_stamp(_old)
c2 = FakeClient()
r2 = trivy_db.update(c2)
check("🔴 同一個 process 連續兩次都做得成（沒有被自己的租約卡住）",
      r2["status"] == "ok" and len(c2.calls) == 1)

print("\n== 更新失敗：有舊 DB → stale，沒有 → missing，兩者都不拋 ==")
set_stamp(_old)
clear_lease()
r = trivy_db.update(FakeClient(raises=_container_error(7)))
check("有既有 DB → stale", r["status"] == "stale")
check("訊息帶得出 exit code", "7" in r["detail"])

set_stamp(None)
clear_lease()
r = trivy_db.update(FakeClient(raises=_container_error(7)))
check("沒有既有 DB → missing", r["status"] == "missing")

print("\n== docker 本身出問題：error，而且不拋 ==")
set_stamp(_old)
clear_lease()
r = trivy_db.update(FakeClient(raises=docker.errors.DockerException("daemon down")))
check("status=error", r["status"] == "error")
check("訊息說得出是什麼錯", "DockerException" in r["detail"])

print("\n== 失敗之後租約也要還（不能把後面的人鎖死）==")
set_stamp(_old)
clear_lease()
trivy_db.update(FakeClient(raises=_container_error()))
set_stamp(_old)          # 同上：把節流排除掉，這條測的是租約
c = FakeClient()
r = trivy_db.update(c)
check("🔴 前一次失敗不會讓下一次被判成 skipped", r["status"] == "ok")

print("\n== 租約層自己拋出來，也不能讓 update() 拋 ==")
# ⚠ 這一段是審查抓到的漏洞：「永遠不拋」寫在四個地方，但**唯一還會拋的那層**
#   （acquire/release 走 SQLite BEGIN IMMEDIATE，busy_timeout 用盡就 OperationalError）
#   原本在 try 之外，而測試只驗過 docker 層的例外。
_real_acquire, _real_release = trivy_db.acquire_lease, trivy_db.release_lease
try:
    set_stamp(_old)
    trivy_db.acquire_lease = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("database is locked"))
    r = trivy_db.update(FakeClient())
    check("🔴 取租約拋出 → 回 error，不往外拋", r["status"] == "error")
    check("訊息說得出是取租約失敗", "取租約" in r["detail"])

    trivy_db.acquire_lease = _real_acquire
    trivy_db.release_lease = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("database is locked"))
    set_stamp(_old)
    clear_lease_direct = _real_release
    clear_lease_direct(trivy_db.LEASE_NAME, "someone-else")
    clear_lease_direct(trivy_db.LEASE_NAME, trivy_db._owner())
    c = FakeClient()
    r = trivy_db.update(c)
    # ⚠ 重點不只是「不拋」，是**已經算好的 ok 不可以被 finally 吃掉**。
    check("🔴 還租約拋出 → 仍然回得到 ok（結果沒有被 finally 取代）",
          r["status"] == "ok" and len(c.calls) == 1)
finally:
    trivy_db.acquire_lease, trivy_db.release_lease = _real_acquire, _real_release
    clear_lease()

print("\n== preflight 不可以拿路徑檢查去問一個 volume 名 ==")
# ⚠ 真的會咬人：MOUNTS 的 key 現在混著「host 路徑」與「volume 名」，而 preflight 對
#   非容器化部署會逐個 os.path.exists()。volume 名恆 False → 每次啟動都喊一句
#   「掛載來源不存在」，而那是假的。假警報喊久了，真警報就沒人看。
from server import sessions  # noqa: E402

# ⚠ preflight 會呼叫 `user_proxy.attach_jaeger`，那會**真的去接你正式環境的網路**。
#   整段包在 suppress 裡，所以讓 from_env 直接拋就安靜跳過了（同 test_host_platform）。
_old_from_env = docker.from_env
docker.from_env = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("測試不連 docker"))
_old_mounts = config.MOUNTS
_old_host_home, _old_self_home = config.HOST_HOME, config._SELF_HOME
_old_space = config.SPACE_SELF
_probe_dir = tempfile.mkdtemp(prefix="claude-pty-probe-")
try:
    # 模擬**非容器化**（HOST 與 SELF 相同），那是唯一會跑這道檢查的情境
    config.HOST_HOME = config._SELF_HOME = os.path.expanduser("~")
    config.SPACE_SELF = _probe_dir
    config.MOUNTS = {
        "ncr-trivy-cache": {"bind": "/home/nathan/.cache/trivy", "mode": "rw"},
        _probe_dir: {"bind": "/x", "mode": "rw"},                 # 存在的路徑
        os.path.join(_probe_dir, "nope"): {"bind": "/y", "mode": "rw"},  # 不存在的路徑
    }
    msgs = [m for m in sessions.preflight() if "掛載來源不存在" in m]
finally:
    docker.from_env = _old_from_env
    config.MOUNTS = _old_mounts
    config.HOST_HOME, config._SELF_HOME = _old_host_home, _old_self_home
    config.SPACE_SELF = _old_space

check("🔴 volume 名不會被誤報成『掛載來源不存在』",
      not any("ncr-trivy-cache" in m for m in msgs))
check("但真的不存在的 host 路徑仍然要喊（不能因此把檢查關掉）",
      any("nope" in m for m in msgs))

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
