"""Session 生命週期 regression（ADR 0008 階段 2：DB 仲裁版）。

承接前兩輪 review 釘下的語意，改寫到 DB 架構：
  🔴 交易失敗不留孤兒、不白佔配額（前身＝C1 makedirs 洩漏 `_creating`／port；
     現在 in-flight 由 DB 的 status=creating 列代表，補償＝刪列）
  🔴 container 消失 → list() 對帳清掉登錄（否則永久佔配額）
  🟡 terminate 真的移除 container + 移除登錄、且等冪
  🟢 配額由 DB 計數擋下（不再是 in-memory 計數）
  🟢 registry 跨「新 manager 實例」存活（＝多 worker / 重啟韌性的最小證明）

已刪除的舊斷言：常駐 ttyd 相關（關分頁不殺 ttyd、reaper 收 ttyd、respawn）——ADR 0008
改為 on-demand ttyd，那些語意移到階段 3 的 view 測試。

需要 docker + dev-container 的 image。用 bash entrypoint 跑（零 token）：
    uv run --with flask --with docker --with sqlalchemy python tests/test_session_lifecycle.py
"""

import os
import sys
import tempfile
import time

for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["CLAUDE_PTY_IMAGE"] = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
os.environ["CLAUDE_PTY_ENTRYPOINT"] = "bash"
os.environ["CLAUDE_PTY_COMMAND"] = ""
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"  # bash 測試不掛 ~/.claude，保持隔離
# 打上測試標記：正式 reconciler 據此跳過這些容器。沒有它的話，測試容器帶 session label
# 卻不在正式 DB 裡，會被正式 reconciler 當孤兒收掉（ORPHAN_GRACE 之後）。
os.environ["CLAUDE_PTY_TEST_MARK"] = "1"

_tmp = tempfile.mkdtemp(prefix="claude-pty-lifecycle-")
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

from server.sessions import DRIVER_MARKER, Profile, SessionManager  # noqa: E402

D = docker.from_env()
# 基準：測試開始前就存在的 session container（正式 stack 可能同時在跑）。
# 「無殘留」只該計算本次測試建立的，否則會誤報。
_PRE_EXISTING = {c.name for c in D.containers.list(all=True, filters=config.SESSION_FILTERS)}


def _leftovers():
    return [c for c in D.containers.list(all=True, filters=config.SESSION_FILTERS) if c.name not in _PRE_EXISTING]


_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


mgr = SessionManager()

try:
    print("== 建立：container 起來、登錄寫進 DB、PTY 通道可用 ==")
    s = mgr.create()
    sid = s["id"]
    time.sleep(1.5)
    check("回傳 status=running", s["status"] == "running")
    check("container 實際在跑", D.containers.get(s["container"]).status == "running")
    check("registry 有這筆", any(x["id"] == sid for x in mgr.list()))
    with mgr.attached(sid, timeout=0.5) as raw:
        raw.sendall(b"echo REGISTRY-OK\r")
        got = b""
        end = time.time() + 6
        while time.time() < end:
            try:
                chunk = raw.recv(65536)
            except (TimeoutError, OSError):
                continue
            if not chunk:
                break
            got += chunk
            if b"REGISTRY-OK" in got:
                break
    check("attach 通道可讀寫（ttyd 與就緒偵測共用的那條）", b"REGISTRY-OK" in got)

    print("== 持久性：換一個 manager 實例仍看得到（多 worker / 重啟韌性）==")
    mgr2 = SessionManager()  # 模擬另一個 worker：全新實例、零 in-memory 狀態
    check("新實例從 DB 讀得到同一 session", any(x["id"] == sid for x in mgr2.list()))
    check("新實例可查 status", mgr2.status(sid)["container"] == s["container"])

    print("== 🔴 list() 完全不碰 docker（ADR 0012）==")

    # 曾經是「順手校正一下」：列表自己打 containers.list，未就緒的列再各打一次
    # docker logs。代價是**整張表的可用性綁在最慢的那顆容器上**——2026-07-27 一顆容器
    # 卡在 removing，這支每 15 秒被輪詢一次的端點每次都等滿 timeout，thread 被吃光，
    # 所有人看不到任何東西。校正移到 reconciler（逐顆隔離），這裡只讀 DB。
    class _Bomb:
        """任何一次 docker 呼叫都直接炸。list() 若還有碰 docker，這裡就會紅。"""

        def __getattr__(self, name):
            raise AssertionError(f"list() 不該碰 docker，卻用了 client.{name}")

    _real_client = mgr._docker
    mgr._docker = _Bomb()
    try:
        rows = mgr.list()
        check("docker 完全不可用時，列表照樣列得出來", any(x["id"] == sid for x in rows))
        me = next(x for x in rows if x["id"] == sid)
        check("每一列帶著『這是幾點跟 dockerd 求證的』", "state_checked_at" in me)
    except AssertionError as e:
        check(f"list() 不該碰 docker（{e}）", False)
    finally:
        mgr._docker = _real_client

    print("== 🔴 status() 不可以寫 DB（它在 nginx auth_request 的熱路徑上）==")
    # 2026-07-27 上線 30 分鐘就炸的那個：status() 曾經「問到 docker 就順手更新
    # docker_state/state_checked_at」。而 `/api/auth/view`（nginx 的 auth_request 掛載點）
    # 每開一次終端會併發打 4~5 發，每一發都經 _owned() → status()，於是全部變成寫入交易，
    # 讀後升級成寫的併發撞在一起 → 500 `database is locked`（busy_timeout 對 upgrade
    # deadlock 無效）。那兩欄的唯一寫入者是 reconciler。
    from server.models import Session as _SessRow  # noqa: E402

    def _stamps():
        with db.session_scope() as _s:
            r = _s.get(_SessRow, sid)
            return (r.docker_state, r.state_checked_at, r.ready_at, r.rows, r.cols)

    _before = _stamps()
    mgr.status(sid, with_ready=True)
    mgr.status(sid, with_ready=True)
    check("連問兩次，DB 一個欄位都沒被動到", _stamps() == _before)

    print("== container 外部消失 → 由 reconciler 清登錄（不白佔配額）==")
    s2 = mgr.create()
    time.sleep(1.0)
    D.containers.get(s2["container"]).remove(force=True)
    # ⚠ 清登錄現在是 reconciler 的職責（列表不再自己對帳）。這裡只驗「列表不會因此壞掉」，
    #   清理本身由 test_reconciler 驗——那支有 _ScopedClient，不會誤傷正式 stack 的容器。
    check(
        "列表仍列得出來（狀態是最後已知值，新鮮度由 state_checked_at 表達）",
        any(x["id"] == s2["id"] for x in mgr.list()),
    )
    mgr.terminate(s2["id"])
    check("terminate 之後登錄才移除", not any(x["id"] == s2["id"] for x in mgr.list()))

    print("== terminate：移除 container + 移除登錄、等冪 ==")
    s3 = mgr.create()
    time.sleep(1.0)
    name3 = s3["container"]
    mgr.terminate(s3["id"])
    gone = False
    try:
        D.containers.get(name3)
    except docker.errors.NotFound:
        gone = True
    check("container 已移除", gone)
    check("登錄已移除", not any(x["id"] == s3["id"] for x in mgr.list()))
    # ADR 0010：離開 sessions 不等於消失——那段歷史必須留得住
    _rows, _total = mgr.history()
    check("終止後留下歷史紀錄", any(x["id"] == s3["id"] and x["ended_reason"] == "terminated" for x in _rows))
    check("歷史帶得出擁有者與 profile", all(x["owner"] and isinstance(x["profile"], dict) for x in _rows))
    check(f"history 回報總數（got {_total}）", _total >= 1)
    dup_ok = False
    try:
        mgr.terminate(s3["id"])
    except Exception as e:
        dup_ok = "未知 session" in str(e)
    check("重複 terminate 拋未知 session（等冪、不炸）", dup_ok)

    print("== 🔴 交易失敗不留孤兒 container、不白佔配額（前身 C1）==")
    from server.models import Session as SessionRow

    with db.session_scope() as _s:
        rows_before = _s.query(SessionRow).count()
    ct_before = len(_leftovers())
    # ⚠ 這裡原本注入的是 capture 落盤目錄的 makedirs 失敗。ADR 0014 之後那個目錄變成
    #   per-user 空間的一部分，改由 provision_user_space() 建——失敗注入也跟著搬過來，
    #   順便補上 provision 的失敗路徑（它跑在 create 的關鍵路徑上，之前沒有測試蓋到）。
    _f = tempfile.NamedTemporaryFile(delete=False)  # 空間根指向**檔案** → makedirs 必拋
    _f.close()
    _orig = (config.MOUNTS, config.SPACE_SELF, config.SPACE_HOST)
    # provision 與 user_mounts 都吃 MOUNTS 這個開關，要非空才會真的做事
    config.MOUNTS = {"/shared": {"bind": "/shared", "mode": "rw"}}
    config.SPACE_SELF = config.SPACE_HOST = _f.name
    failed = False
    try:
        mgr.create(profile=Profile(capture=True))
    except Exception:
        failed = True
    config.MOUNTS, config.SPACE_SELF, config.SPACE_HOST = _orig
    os.unlink(_f.name)
    check("per-user 空間建不出來時 create 拋錯（不靜默——空間壞掉的 session 一定是壞的）", failed)
    with db.session_scope() as _s:
        rows_after = _s.query(SessionRow).count()
    check("失敗後未殘留 creating 登錄（配額不被白佔）", rows_after == rows_before)
    check("失敗後未留孤兒 container", len(_leftovers()) == ct_before)

    print("== 🟢 配額由 DB 計數擋下 ==")
    _orig_max = config.MAX_SESSIONS
    with db.session_scope() as _s:
        cur = _s.query(SessionRow).count()
    config.MAX_SESSIONS = cur  # 已達上限
    quota_blocked = False
    try:
        mgr.create()
    except Exception as e:
        quota_blocked = "上限" in str(e)
    config.MAX_SESSIONS = _orig_max
    check("達上限時 create 被 DB 計數擋下", quota_blocked)

    print("== container 不在時 resize 要回 SessionError，不是未捕捉的 500（review 2026-07-26）==")
    # ⚠ app.py 沒有 docker.errors.NotFound 的 errorhandler，讓它原樣往上跑就是一頁 HTML
    #   traceback 的 500。這條路上其他每一支都轉過了（attach_socket → SessionError、
    #   terminate 當成冪等成功、_nudge_redraw 直接 suppress），只有 resize 漏了。
    #   畫面看不出來（app.js 的 `.catch(() => {})` 吃掉），而「malformed 輸入不該變成
    #   500」是這個 codebase 自己立的規矩。
    from server.sessions import SessionError as _SessErr  # noqa: E402
    from server.sessions import ensure_system_user  # noqa: E402

    _uid = ensure_system_user()
    with db.session_scope() as _s:
        _s.add(
            SessionRow(
                id="ghostresize1",
                container_name="claude-pty-ghostresize1",
                user_id=_uid,
                workdir="/tmp",
                status="running",
            )
        )
    resize_err = None
    try:
        mgr.resize("ghostresize1", 30, 100)
    except Exception as e:  # noqa: BLE001 - 要看的就是「拋出來的是哪一種」
        resize_err = e
    check(f"拋的是 SessionError（實際 {type(resize_err).__name__}）", isinstance(resize_err, _SessErr))
    check("不是 docker 的 NotFound 直接往外跑", not isinstance(resize_err, docker.errors.NotFound))

    print("== 尺寸沒變時也要 nudge，不能只信呼叫端的 redraw 旗標（review 2026-07-27）==")

    # ⚠ `docker resize` 只在尺寸**真的變了**時才讓核心送 SIGWINCH。而「開啟終端時尺寸剛好
    #   與上次相同」是常態（同一個視窗、同一個字級），那時 TUI 沿用舊版面——使用者看到的
    #   就是「畫面是舊的，手動縮放一下才好」。前端那條旗標要正確得先滿足一串時序
    #   （xterm fit 完了沒、debounce 讀到的是不是最終值、旗標有沒有被提早清掉），任何一環
    #   沒對上就靜靜不重繪，Mac 與 Ubuntu 都回報過。所以判斷放在**伺服端**：它知道上一次
    #   的尺寸，呼叫端不知道。
    class _ResizeSpy:
        """只記 resize 呼叫，不真的碰 docker。"""

        def __init__(self):
            self.calls = []
            self.api = self

        def resize(self, _c, height=None, width=None):
            self.calls.append((height, width))

    probe2 = SessionManager()
    spy = _ResizeSpy()
    probe2._docker = spy
    with db.session_scope() as _s:
        _s.add(
            SessionRow(
                id="resizespy001",
                container_name="claude-pty-resizespy001",
                user_id=_uid,
                workdir="/tmp",
                status="running",
                rows=40,
                cols=140,
            )
        )

    spy.calls.clear()
    probe2.resize("resizespy001", 50, 160)  # 尺寸真的變了、沒帶旗標
    check(f"尺寸有變＋無旗標 → 只有一次 resize，不 nudge（實際 {spy.calls}）", spy.calls == [(50, 160)])

    spy.calls.clear()
    probe2.resize("resizespy001", 50, 160)  # 與 DB 記的相同、沒帶旗標
    check(f"**尺寸沒變**＋無旗標 → 仍要 nudge（實際 {spy.calls}）", spy.calls == [(50, 160), (50, 159), (50, 160)])

    spy.calls.clear()
    probe2.resize("resizespy001", 60, 180, redraw=True)  # 尺寸有變但明確要求重繪
    check(f"尺寸有變＋帶旗標 → 照樣 nudge（實際 {spy.calls}）", spy.calls == [(60, 180), (60, 179), (60, 180)])

    with db.session_scope() as _s:
        _r = _s.get(SessionRow, "resizespy001")
        check("DB 記下最後套用的尺寸", (_r.rows, _r.cols) == (60, 180))

    print("== 列表的 ready 一律讀 DB，一次 docker logs 都不打（ADR 0012）==")
    # 沿革：這條原本釘的是「**已經**就緒過的列不再打 docker logs」——那時未就緒的列
    # 仍然每次都問，因為 ready_at 可能因背景執行緒逾時而永遠是 NULL。
    # 2026-07-27 之後連那條也不打了：一顆卡住的容器讓那一發 logs 等滿 timeout，
    # 每 15 秒被輪詢一次的端點於是吃光所有 thread，**全部人看不到任何東西**。
    # ready 是單調的（ready_at 由 `WHERE ready_at IS NULL` 條件式寫入，寫進去不會變回
    # NULL），所以讀 DB 與問 log 是同一個答案的兩種來源，差別只在「何時被觀察到」。
    # ⚠ 原本那條「NULL 的列要有人去問」沒有消失，只是換了地方：改由 reconciler 逐顆
    #   隔離地補記（test_reconciler 的「ready 補記」段）。少了那一段，這裡的短路會讓
    #   背景執行緒死掉的 session **永遠**顯示未就緒——兩支測試是一組的。
    from server.models import utcnow as _utcnow  # noqa: E402

    class _CountingContainer:
        def __init__(self, name):
            self.name, self.status, self.id, self.calls = name, "running", "fake", 0

        def logs(self, **kw):
            self.calls += 1
            return DRIVER_MARKER.encode()

    class _FakeDocker:
        def __init__(self, containers):
            self._c, self.containers = containers, self

        def list(self, **kw):
            return list(self._c)

    # ⚠ 這兩列掛在**專用的假使用者**底下，並且只查他的——不可以清空 sessions 表或不帶
    #   user_id 查詢：list() 會把「假 docker 裡看不到的」真實 session 當成 gone 歸檔掉，
    #   於是最後的清理找不到那些容器，留下殘留（第一次寫成那樣，清理那條當場就紅了）。
    from server.auth import create_user as _create_user  # noqa: E402

    _probe_uid = _create_user("readyprobe", "ready-probe-password-1")["id"]
    with db.session_scope() as _s:
        _s.add(
            SessionRow(
                id="readystamped",
                container_name="claude-pty-readystamped",
                user_id=_probe_uid,
                workdir="/tmp",
                status="running",
                ready_at=_utcnow(),
            )
        )
        _s.add(
            SessionRow(
                id="neverreadyx0",
                container_name="claude-pty-neverreadyx0",
                user_id=_probe_uid,
                workdir="/tmp",
                status="running",
                ready_at=None,
            )
        )
    stamped = _CountingContainer("claude-pty-readystamped")
    never = _CountingContainer("claude-pty-neverreadyx0")
    probe = SessionManager()
    probe._docker = _FakeDocker([stamped, never])
    rows = {r["id"]: r for r in probe.list(user_id=_probe_uid)}
    check("已就緒過的列：一次 docker logs 都沒打", stamped.calls == 0)
    check("沒就緒過的列：**也**一次都沒打（那一發正是拖垮整張表的那個）", never.calls == 0)
    check("ready 直接來自 ready_at：記過的是 True", rows["readystamped"]["ready"] is True)
    check("沒記過的是 False（不是猜『大概好了』——補記是 reconciler 的事）", rows["neverreadyx0"]["ready"] is False)

finally:
    print("== 清理 ==")
    for x in mgr.list():
        with __import__("contextlib").suppress(Exception):
            mgr.terminate(x["id"])
    leftover = _leftovers()
    check("測試結束無殘留 container", len(leftover) == 0)
    db.reset_engine()
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
