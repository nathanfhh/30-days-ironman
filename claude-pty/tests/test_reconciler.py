"""Reconciler regression（ADR 0008 階段 5）。

驗證 DB ↔ dockerd 漂移都能被修回來，且不誤傷正常 session：
  - container 不存在 → 清登錄（釋放配額）
  - container exited-but-present → 先刪 container 再刪登錄（不留 stopped 孤兒）
  - 沒人認領的 claude-pty-* container → 逾寬限期後收掉
  - 寬限期內的孤兒 / 正常 running session → 不動
  - 死掉的 view 記錄 → 清掉釋放 port；in-flight 宣告不誤刪

需要 docker + dev-container 的 image。bash entrypoint（零 token）：
    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        python tests/test_reconciler.py
"""
import os
import sys
import tempfile
import time

for v in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["CLAUDE_PTY_IMAGE"] = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
os.environ["CLAUDE_PTY_ENTRYPOINT"] = "bash"
os.environ["CLAUDE_PTY_COMMAND"] = ""
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
# 打上測試標記：正式 reconciler 據此跳過這些容器。沒有它的話，測試容器帶 session label
# 卻不在正式 DB 裡，會被正式 reconciler 當孤兒收掉（ORPHAN_GRACE 之後）。
os.environ["CLAUDE_PTY_TEST_MARK"] = "1"

_tmp = tempfile.mkdtemp(prefix="claude-pty-recon-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402

from server import config, db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]

config.HOST_HOME = _tmp
db.reset_engine()
db.init_db()

# 憑證＝DB 裡的 setup-token（唯一來源，D 階段起不再讀任何 host 憑證檔）。
# 這批測試的 session 都掛在 system 使用者名下，給它種一個測試值就過得了 create() 的守門。
from server import auth as _auth_seed  # noqa: E402
from server import sessions as _sessions_seed  # noqa: E402

_auth_seed.set_cli_token(_sessions_seed.ensure_system_user(), "sk-test-setup-token")

from server import reconciler  # noqa: E402
from server.models import Session as SessionRow, View  # noqa: E402
from server.sessions import SessionManager  # noqa: E402

D = docker.from_env()
# 基準：測試開始前就存在的 session container（正式 stack 可能同時在跑）。
# 「無殘留」只該計算本次測試建立的，否則會誤報。
_PRE_EXISTING = {c.name for c in D.containers.list(all=True, filters=config.SESSION_FILTERS)}


def _leftovers():
    return [c for c in D.containers.list(all=True, filters=config.SESSION_FILTERS)
            if c.name not in _PRE_EXISTING]


class _ScopedContainers:
    """containers.list 只回報本測試建立的容器，其餘一律直通。"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def list(self, **kw):
        return [c for c in self._real.list(**kw) if c.name not in _PRE_EXISTING]


class _ScopedClient:
    """🛡 交給 reconciler 的 client：把測試前就存在的 container 藏起來。

    本測試會把「帶 session label 但不在本測試 DB」的 container 當孤兒清掉
    （ORPHAN_GRACE=0 那段）。同一台機器上若有正式 stack 在跑，它的 session **全部**
    符合這個條件——2026-07-25 實測，兩個使用者正在用的 session 就這樣被殺掉。
    """

    def __init__(self, real):
        self._real = real
        self.containers = _ScopedContainers(real.containers)

    def __getattr__(self, name):
        return getattr(self._real, name)


_fails = 0
def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok

def exists(name):
    try:
        D.containers.get(name)
        return True
    except docker.errors.NotFound:
        return False

SAFE = _ScopedClient(D)   # 只有本測試建立的容器對 reconciler 可見
mgr = SessionManager()
keep = orphan = None


def _passthrough_isolated(label, fn, *a, **kw):
    """與 reconcile_once 內那支同語意的隔離器：壞掉回 STUCK 哨兵，不往外拋。

    直接測 `_reclaim_idle` / `_remove_orphans` 這種內部函式時要傳它進去——傳一個會
    往外拋的假貨，測到的就不是正式路徑的行為。
    """
    try:
        return fn(*a, **kw)
    except docker.errors.NotFound:
        raise
    except Exception:
        return reconciler.STUCK

try:
    print("== 正常 running session 不被誤傷 ==")
    keep = mgr.create()
    time.sleep(1.5)
    stats = reconciler.reconcile_once(SAFE)
    check("running session 的登錄仍在", any(x["id"] == keep["id"] for x in mgr.list()))
    check("其 container 仍在", exists(keep["container"]))

    print("== container 不存在 → 清登錄（釋放配額）==")
    s2 = mgr.create()
    time.sleep(1.0)
    D.containers.get(s2["container"]).remove(force=True)
    stats = reconciler.reconcile_once(SAFE)
    check("計入 gone", stats["gone"] >= 1)
    with db.session_scope() as s:
        check("登錄已刪", s.get(SessionRow, s2["id"]) is None)

    print("== container exited-but-present → 先刪 container 再刪登錄 ==")
    s3 = mgr.create()
    time.sleep(1.0)
    name3 = s3["container"]
    D.containers.get(name3).kill()          # 殺 PID 1 → exited 但仍存在
    time.sleep(0.5)
    check("前置條件：container 為 exited 且仍在", D.containers.get(name3).status == "exited")
    stats = reconciler.reconcile_once(SAFE)
    check("計入 exited_removed", stats["exited_removed"] >= 1)
    check("stopped container 已被移除（不累積 writable layer）", not exists(name3))
    with db.session_scope() as s:
        check("其登錄已刪", s.get(SessionRow, s3["id"]) is None)

    print("== 沒人認領的 container：寬限期內不動、逾期才收 ==")
    # 必須帶 session label：reconciler 只認 label 不認名稱前綴（否則會把基礎設施容器
    # 一起刪掉，2026-07-25 實測踩到）。沒 label 的容器本就不該被它碰。
    orphan = D.containers.run(config.IMAGE, entrypoint="bash", name="claude-pty-orphantest",
                              detach=True, tty=True, stdin_open=True,
                              labels={config.SESSION_LABEL_KEY: config.SESSION_LABEL_VALUE})
    time.sleep(1.0)
    _orig_grace = config.ORPHAN_GRACE
    config.ORPHAN_GRACE = 3600              # 很長 → 應視為「可能正在建立中」
    stats = reconciler.reconcile_once(SAFE)
    check("寬限期內的孤兒不被誤殺", exists("claude-pty-orphantest"))
    config.ORPHAN_GRACE = 0                 # 立即視為孤兒
    stats = reconciler.reconcile_once(SAFE)
    check("逾寬限期的孤兒被收掉", not exists("claude-pty-orphantest"))
    check("計入 orphan_containers", stats["orphan_containers"] >= 1)
    orphan = None
    config.ORPHAN_GRACE = _orig_grace
    check("正常 session 不受孤兒清理影響", exists(keep["container"]))

    print("== view 記錄：死的清掉、in-flight 宣告不誤刪 ==")
    # 兩筆宣告必須分屬不同 session：views.session_id 是 UNIQUE（一 session 一 view，
    # 否則兩個 worker 會為同一 session 各起一個 ttyd，其中一個永遠等不到 client 而長生不死）。
    keep2 = mgr.create()
    time.sleep(1.0)
    with db.session_scope() as s:
        s.add(View(session_id=keep["id"], port=45999, pid=999999))    # pid 幾乎不可能存在
        s.add(View(session_id=keep2["id"], port=45998, pid=None))     # in-flight 宣告
    stats = reconciler.reconcile_once(SAFE)
    with db.session_scope() as s:
        check("死掉的 view 記錄已清（port 釋放）",
              s.query(View).filter_by(port=45999).count() == 0)
        check("in-flight 宣告（寬限期內）未被誤刪",
              s.query(View).filter_by(port=45998).count() == 1)

    print("== idle 回收預設停用（headless 長跑不該被誤殺）==")
    check("IDLE_TIMEOUT_HOURS 預設為 0＝停用", config.IDLE_TIMEOUT_HOURS == 0)
    stats = reconciler.reconcile_once(SAFE)
    check("停用時不回收任何 session", stats["idle_reclaimed"] == 0)
    check("keep session 仍在", exists(keep["container"]))

    import datetime as _dt2  # noqa: E402

    from server.models import utcnow  # noqa: E402
    _sysuid = __import__("server.sessions", fromlist=["x"]).ensure_system_user()

    print("== 🔴 一顆卡住的容器不可以讓整輪陣亡（2026-07-27 實際停擺 40 分鐘）==")
    # 那次的形狀：一顆容器卡在 `removing`，daemon 對它的呼叫全部不回應。
    # `remove_container` 丟的是 urllib3 的 **ReadTimeout**——不是 docker.errors.APIError，
    # 所以它一路穿出 reconcile_once，被主迴圈接住印成「本輪失敗」。後果不是「那顆沒清掉」
    # 而是**整輪什麼都沒做**：另一場早就結束的 session 因此在 DB 裡掛著 running 40 分鐘。
    from requests.exceptions import ReadTimeout  # noqa: E402  （docker-py 底層就是它）

    class _OneContainerWedged:
        """指定的那顆容器怎麼問都不回應（逾時）；其餘一切正常。"""
        def __init__(self, real, wedged_id):
            self._real, self._wedged = real, wedged_id
            self.containers = real.containers
            self.api = self

        def __getattr__(self, name):
            return getattr(self._real, name)

        def remove_container(self, cid, **kw):
            if cid.startswith(self._wedged) or self._wedged.startswith(cid):
                raise ReadTimeout("Read timed out.（測試注入：卡在 removing 的容器）")
            return self._real.api.remove_container(cid, **kw)

    stuck = mgr.create()
    victim = mgr.create()       # 與卡住那顆毫無關係的一場，它必須照樣被收拾
    time.sleep(1.2)
    stuck_cid = D.containers.get(stuck["container"]).id
    D.containers.get(stuck["container"]).kill()          # → exited，會走到 remove 那條
    D.containers.get(victim["container"]).remove(force=True)   # → gone，該被歸檔
    time.sleep(0.5)
    wedged_client = _OneContainerWedged(SAFE, stuck_cid)
    blew_up = False
    try:
        stats = reconciler.reconcile_once(wedged_client)
    except Exception as e:      # noqa: BLE001 — 就是要證明它不會炸出來
        blew_up = True
        print(f"     （拋出 {e!r}）")
    check("整輪沒有被那顆卡住的容器炸掉", not blew_up)
    if not blew_up:
        check(f"有把它記成卡住的一顆（stats {stats.get('containers_stuck')}）",
              stats.get("containers_stuck", 0) >= 1)
        with db.session_scope() as s:
            check("🔴 與它無關的那一場照樣被歸檔（這才是那 40 分鐘真正的損失）",
                  s.get(SessionRow, victim["id"]) is None)
            check("卡住的那一場留在登錄裡，下輪再試（不可以宣告一個沒發生的結束）",
                  s.get(SessionRow, stuck["id"]) is not None)
    # 收拾：這次用正常 client，它就刪得掉了
    with __import__("contextlib").suppress(Exception):
        mgr.terminate(stuck["id"])

    print("== 狀態與求證時刻要寫進 DB（列表只讀這兩欄，ADR 0013）==")
    fresh = mgr.create()
    time.sleep(1.2)
    with db.session_scope() as s:
        s.get(SessionRow, fresh["id"]).state_checked_at = None      # 假裝從沒問到過
        s.get(SessionRow, fresh["id"]).docker_state = None
    reconciler.reconcile_once(SAFE)
    with db.session_scope() as s:
        row = s.get(SessionRow, fresh["id"])
        check("docker_state 記成 running", row.docker_state == "running")
        age = (utcnow() - row.state_checked_at).total_seconds() if row.state_checked_at else 1e9
        check(f"state_checked_at 是剛剛（{age:.1f}s）", age < 30)
    # 列表要看得到這兩欄——沒接上去的話前端永遠顯示「未確認」
    listed = next((x for x in mgr.list() if x["id"] == fresh["id"]), {})
    check("list() 回得出 state_checked_at", bool(listed.get("state_checked_at")))
    check("list() 的 state 來自最後求證的值", listed.get("state") == "running")

    print("== ready 補記：建立時那條執行緒死掉也要有人補（ADR 0013）==")
    # 列表改成純讀 DB 之後，沒有人補 ready_at 的話這些 session 會**永遠**顯示未就緒，
    # 而它們其實好好地跑著。bash entrypoint 不會自己印 marker，這裡讓它印出來。
    from server.sessions import DRIVER_MARKER  # noqa: E402
    with db.session_scope() as s:
        s.get(SessionRow, fresh["id"]).ready_at = None
    # 沒有注入 API 了，直接對 PTY 寫一行（attached 是就緒偵測本來就在用的讀寫通道）
    with mgr.attached(fresh["id"]) as raw:
        raw.sendall(f"echo {DRIVER_MARKER}\r".encode())
    time.sleep(1.0)
    stats = reconciler.reconcile_once(SAFE)
    with db.session_scope() as s:
        check(f"補記了 ready_at（stats {stats.get('ready_stamped')}）",
              s.get(SessionRow, fresh["id"]).ready_at is not None)
    check("list() 因此顯示就緒",
          next((x["ready"] for x in mgr.list() if x["id"] == fresh["id"]), False))

    print("== idle 回收：docker 刪不掉就不可以歸檔（review 2026-07-26）==")
    # ⚠ 原本 APIError 被 suppress 掉之後照樣歸檔＋計數，結果是歷史宣告「這場因閒置結束」
    #   而容器還在跑——權威狀態與實際資源分裂，而且原始失敗完全不可觀測。
    #   上面那條主對帳路徑早就寫對了（`except APIError: continue`），idle 這條漏了。
    class _RemoveFails:
        """一個「刪不掉」的 docker daemon：只有 remove_container 會失敗，其餘照常。"""
        def __init__(self, real):
            self._real = real
            self.api = self
        def __getattr__(self, name):
            return getattr(self._real, name)
        def remove_container(self, *a, **kw):
            raise docker.errors.APIError("daemon 暫時不可用（測試注入）")

    from server.models import SessionHistory  # noqa: E402
    with db.session_scope() as s:
        s.add(SessionRow(id="idlefailone1", container_name="claude-pty-idlefailone1",
                         user_id=_sysuid, workdir="/tmp", container_id="deadbeef",
                         last_active_at=utcnow() - _dt2.timedelta(hours=48)))
    _saved_idle = config.IDLE_TIMEOUT_HOURS
    config.IDLE_TIMEOUT_HOURS = 1
    try:
        # isolated 用真的那一支（reconcile_once 內部那個），行為才與正式路徑一致
        n = reconciler._reclaim_idle(_RemoveFails(SAFE), _passthrough_isolated)
    finally:
        config.IDLE_TIMEOUT_HOURS = _saved_idle
    check(f"刪不掉就不計入回收數（實際 {n}）", n == 0)
    with db.session_scope() as s:
        still_live = s.get(SessionRow, "idlefailone1") is not None
        archived = any(h.session_id == "idlefailone1"
                       for h in s.query(SessionHistory).all())
    check("登錄保留下來，下一輪可以重試", still_live)
    check("**沒有**寫進歷史（不可以宣告一個沒發生的結束）", not archived)

finally:
    print("== 清理 ==")
    with __import__("contextlib").suppress(Exception):
        for x in mgr.list():
            mgr.terminate(x["id"])
    for name in ("claude-pty-orphantest",):
        with __import__("contextlib").suppress(Exception):
            D.containers.get(name).remove(force=True)
    leftover = _leftovers()
    check("測試結束無殘留 container", len(leftover) == 0)
    db.reset_engine()
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
