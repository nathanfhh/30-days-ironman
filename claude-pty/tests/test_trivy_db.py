"""trivy DB 更新的 regression（假 docker client，不真的下載）。

    uv run --with flask --with docker --with sqlalchemy python tests/test_trivy_db.py

這支守的核心命題：**它永遠不擋開場**。六種結果全部要回一個 dict，一個都不准拋——
因為呼叫它的地方是 `sessions.create()` 的熱路徑，而 A2 沒有 DB 是可降級的，開不了場不是。
"""
import datetime as _dt
import json
import os
import sys
import tempfile

# ⚠ 自己的 DB：不設就會連上使用者**真實的** claude-pty.db（租約寫在 DB 裡）。
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

_cache = tempfile.mkdtemp(prefix="claude-pty-trivycache-")
config.TRIVY_CACHE_SELF = _cache
config.TRIVY_CACHE_HOST = _cache
_dbdir = os.path.join(_cache, "db")
os.makedirs(_dbdir, exist_ok=True)


def set_metadata(next_update: _dt.datetime | None):
    p = os.path.join(_dbdir, "metadata.json")
    if next_update is None:
        if os.path.exists(p):
            os.remove(p)
        return
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"NextUpdate": next_update.isoformat().replace("+00:00", "Z")}, f)


def set_db(present: bool):
    p = os.path.join(_dbdir, "trivy.db")
    if present:
        with open(p, "wb") as f:
            f.write(b"x" * 16)
    elif os.path.exists(p):
        os.remove(p)


def clear_lease():
    leases.release_lease(trivy_db.LEASE_NAME, "someone-else")
    leases.release_lease(trivy_db.LEASE_NAME, trivy_db._owner())


_future = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=6)
_past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=6)

print("== 開關：關掉就什麼都不做 ==")
config.TRIVY_DB_UPDATE = False
c = FakeClient()
r = trivy_db.update(c)
check("status=disabled", r["status"] == "disabled")
check("🔴 一顆容器都沒起", c.calls == [])
config.TRIVY_DB_UPDATE = True

print("\n== 新鮮度短路：還沒到期就不起容器 ==")
set_metadata(_future)
set_db(True)
clear_lease()
c = FakeClient()
r = trivy_db.update(c)
check("status=fresh", r["status"] == "fresh")
check("🔴 沒起容器（這就是短路的全部價值）", c.calls == [])

print("\n== 過期：才會真的去更新 ==")
set_metadata(_past)
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
check("cache 掛在容器內的固定落點",
      (kw.get("volumes") or {}).get(config.TRIVY_CACHE_HOST, {}).get("bind")
      == "/home/nathan/.cache/trivy")
check("用完即棄（remove=True）", kw.get("remove") is True)
# ⚠ 這條是真的踩得到：帶了 session label 的話，reconciler 的孤兒清理會把這顆
#   「有 label 卻不在 DB 裡」的容器當成孤兒。--rm 很快就走，但那是在賭時序。
check("🔴 沒有帶任何 label（不能進 reconciler 的視野）",
      not kw.get("labels"))

print("\n== metadata 讀不到：當成過期，不是當成新鮮 ==")
set_metadata(None)
clear_lease()
c = FakeClient()
r = trivy_db.update(c)
check("🔴 讀不到就去更新（fail-safe 的方向要對）", r["status"] == "ok" and len(c.calls) == 1)

print("\n== 租約：別人持有時跳過，而且不等 ==")
set_metadata(_past)
clear_lease()
leases.acquire_lease(trivy_db.LEASE_NAME, "someone-else", 300)
c = FakeClient()
r = trivy_db.update(c)
check("status=skipped", r["status"] == "skipped")
check("🔴 沒起第二顆容器（否則就是重複下載 103 MiB）", c.calls == [])
clear_lease()

print("\n== 租約用完要還：下一次要能繼續 ==")
set_metadata(_past)
clear_lease()
c = FakeClient()
trivy_db.update(c)
c2 = FakeClient()
r2 = trivy_db.update(c2)
check("🔴 同一個 process 連續兩次都做得成（沒有被自己的租約卡住）",
      r2["status"] == "ok" and len(c2.calls) == 1)

print("\n== 更新失敗：有舊 DB → stale，沒有 → missing，兩者都不拋 ==")
set_metadata(_past)
set_db(True)
clear_lease()
r = trivy_db.update(FakeClient(raises=_container_error(7)))
check("有既有 DB → stale", r["status"] == "stale")
check("訊息帶得出 exit code", "7" in r["detail"])

set_db(False)
clear_lease()
r = trivy_db.update(FakeClient(raises=_container_error(7)))
check("沒有既有 DB → missing", r["status"] == "missing")

print("\n== docker 本身出問題：error，而且不拋 ==")
set_metadata(_past)
clear_lease()
r = trivy_db.update(FakeClient(raises=docker.errors.DockerException("daemon down")))
check("status=error", r["status"] == "error")
check("訊息說得出是什麼錯", "DockerException" in r["detail"])

print("\n== 失敗之後租約也要還（不能把後面的人鎖死）==")
set_metadata(_past)
set_db(True)
clear_lease()
trivy_db.update(FakeClient(raises=_container_error()))
c = FakeClient()
r = trivy_db.update(c)
check("🔴 前一次失敗不會讓下一次被判成 skipped", r["status"] == "ok")

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
