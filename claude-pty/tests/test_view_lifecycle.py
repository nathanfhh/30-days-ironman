"""on-demand ttyd view regression（ADR 0008 階段 3）。

核心命題：**關掉網頁 → ttyd 自己消失**，沒有任何 worker 需要偵測或送 kill。
  - port 由 DB 的 views.port UNIQUE 仲裁（跨 worker 原子）
  - ttyd 以 double-fork 起 → 不是 worker 的子程序（PPID=1）→ 殭屍由 init reap
  - `-q`（--exit-no-conn）：WS 斷線即自行退出
  - session terminate 會提前收掉殘留 view

需要 docker + ttyd + dev-container 的 image。用 bash entrypoint 跑（零 token）：
    uv run --with flask --with docker --with sqlalchemy --with websocket-client \
        python tests/test_view_lifecycle.py
"""

import os
import subprocess
import sys
import tempfile
import time

for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["CLAUDE_PTY_IMAGE"] = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
os.environ["CLAUDE_PTY_ENTRYPOINT"] = "bash"
os.environ["CLAUDE_PTY_COMMAND"] = ""
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
# 打上測試標記：正式 reconciler 據此跳過這些容器。沒有它的話，測試容器帶 session label
# 卻不在正式 DB 裡，會被正式 reconciler 當孤兒收掉（ORPHAN_GRACE 之後）。
os.environ["CLAUDE_PTY_TEST_MARK"] = "1"
# 專屬 port 區段：正式服務用 41000–41100，共用會讓「無殘留 ttyd」的檢查把別人的
# ttyd 算進來而誤報——誤報的測試最終就是被忽略。
os.environ["CLAUDE_PTY_TTYD_PORT_MIN"] = "45000"
os.environ["CLAUDE_PTY_TTYD_PORT_MAX"] = "45050"

_tmp = tempfile.mkdtemp(prefix="claude-pty-view-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402
import websocket  # noqa: E402

from server import config, db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()
db.init_db()

# 憑證守門在 create() 入口（D 階段起）。這支的 session 掛在 system 使用者名下，
# 給它種一個測試 token 就過得了守門——本測試驗的是 view 生命週期，不是憑證。
from server import auth as _auth_seed  # noqa: E402
from server import sessions as _sessions_seed  # noqa: E402

_auth_seed.set_cli_token(_sessions_seed.ensure_system_user(), "sk-test-setup-token")

from server import views  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402
from server.models import View  # noqa: E402
from server.sessions import SessionManager  # noqa: E402

D = docker.from_env()
# 基準：測試開始前就存在的 session container（正式 stack 可能同時在跑）。
# 「無殘留」只該計算本次測試建立的，否則會誤報。
_PRE_EXISTING = {c.name for c in D.containers.list(all=True, filters=config.SESSION_FILTERS)}
_MINE: set[str] = set()  # 本次測試親手建立的 session id


def _leftovers():
    """本次測試自己留下來的 session container。

    ⚠ 判斷依據必須是「它屬於本次建立的哪一個 session」。原本只用「測試開始前不存在」，
      而那不等於「是我建的」：正式 stack 或同時在跑的其他測試會在中途建出新的 session，
      那不是我們的殘留，卻會讓這條斷言紅在完全無關的事情上——2026-07-26 就這樣誤報過
      一次（另一個 agent 正在做探索性測試）。註解本來就寫著這個意思，只是實作沒跟上。
    """
    return [
        c
        for c in D.containers.list(all=True, filters=config.SESSION_FILTERS)
        if c.name not in _PRE_EXISTING and any(sid in c.name for sid in _MINE)
    ]


_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def ppid_of(pid):
    r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    return int(r.stdout.strip()) if r.stdout.strip() else None


def wait_gone(pid, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if not views._process_alive(pid):
            return True
        time.sleep(0.25)
    return False


print("== ttyd argv：不把 container 身分洩漏到網頁標題 ==")
# ttyd 預設標題是「完整命令 + 容器 hostname」，會把 container id 與 attach 參數
# 帶進分頁標題／瀏覽紀錄／截圖。必須用 titleFixed 蓋掉。
_argv = views._ttyd_argv(40000, "claude-pty-abc123", "abc123")
_title = next((a for a in _argv if a.startswith("titleFixed=")), None)
check(f"有固定標題（got {_title}）", _title is not None)
check("標題只含我們自己的 sid，不含 container 名", _title and "claude-pty-abc123" not in _title)

mgr = SessionManager()
sess = None

try:
    sess = mgr.create()
    sid = sess["id"]
    _MINE.add(sid)  # 收尾時只看這些，見 _leftovers
    time.sleep(1.5)

    print("== 開 view：起 ttyd、DB 記錄、URL 可達 ==")
    v = views.open_view(sid, sess["container"])
    check("回傳 port 在設定範圍內", config.TTYD_PORT_MIN <= v["port"] <= config.TTYD_PORT_MAX)
    check("回傳 nginx 路徑", v["path"] == f"/session/{sid}/")
    check("ttyd 程序存活", views._process_alive(v["pid"]))
    check("port 已在監聽", views._port_open(v["port"]))
    with db.session_scope() as s:
        check("DB 有 view 記錄且 port 一致", s.query(View).filter_by(session_id=sid).one().port == v["port"])

    print("== double-fork：ttyd 不是 worker 的子程序（PPID=1，殭屍由 init reap）==")
    ppid = ppid_of(v["pid"])
    check(f"PPID 為 1（got {ppid}）＝已 reparent 給 init", ppid == 1)
    check("不是本 process 的子程序", ppid != os.getpid())

    print("== 記下這個 view 是哪一顆 ttyd 起的（畫面要在抽屜標題列標出來）==")
    # ⚠ 這一欄的意義是「**實際**跑的是哪一顆」，不是「這個人現在的偏好」——所以下面
    #   第二段特地換了偏好再問一次：沿用既有 view 時必須還是回當初那一顆。
    check("預設起的是 C 版（config.TTYD_BIN_DEFAULT）", v["ttyd_bin"] == "ttyd")
    check("同時給人看得懂的名字", v["ttyd_flavor"] == config.TTYD_BINS["ttyd"])
    with db.session_scope() as s:
        check("DB 也記著（不是只在回應裡算一次）", s.query(View).filter_by(session_id=sid).one().ttyd_bin == "ttyd")

    print("== 點兩次不會多起一個 ttyd（沿用存活的 view）==")
    v2 = views.open_view(sid, sess["container"], "ttyd-rust")  # 偏好改了也不換掉在跑的
    check("回傳同一個 view", v2["view_id"] == v["view_id"] and v2["port"] == v["port"])
    check(
        "🔴 沿用時回的是**當初起它的那一顆**，不是新偏好",
        v2["ttyd_bin"] == "ttyd" and v2["ttyd_flavor"] == config.TTYD_BINS["ttyd"],
    )
    with db.session_scope() as s:
        check("DB 仍只有一筆 view", s.query(View).filter_by(session_id=sid).count() == 1)

    print("== 🔴 核心：關掉網頁 → ttyd 因 -q 自行退出（無需任何 worker 介入）==")
    ws = websocket.create_connection(f"ws://127.0.0.1:{v['port']}/session/{sid}/ws", subprotocols=["tty"])
    ws.send(b"\x00" + b'{"columns":80,"rows":24}')  # 建立 terminal
    time.sleep(1.0)
    check("WS 連線期間 ttyd 仍活著", views._process_alive(v["pid"]))
    ws.close()  # ＝關掉瀏覽器分頁
    check("關頁後 ttyd 自行退出（-q 生效）", wait_gone(v["pid"]))
    check("無殭屍殘留（PPID=1 由 init reap）", ppid_of(v["pid"]) is None)

    print("== 自退後 list_views 清掉殘留記錄、釋放 port ==")
    check("list_views 回傳空（已清理）", views.list_views(sid) == [])
    with db.session_scope() as s:
        check("DB 記錄已刪（port 可再分配）", s.query(View).filter_by(session_id=sid).count() == 0)

    print("== port 被『別的 session』佔走時換下一個（跨 worker 仲裁）==")
    # pid=NULL ＝另一個 worker「剛搶到 port、ttyd 還沒起來」的 in-flight 宣告。
    # 寬限期內必須尊重它（不可當殘留刪掉），否則兩邊會用到同一個 port。
    # ⚠ 必須用「別的 session」來模擬：views.session_id 也是 UNIQUE，拿同一個 sid 佔位
    # 撞到的是 session_id 而非 port，那是下一段測的另一回事。
    taken = config.TTYD_PORT_MIN
    with db.session_scope() as s:
        s.add(
            SessionRow(
                id="peerfake",
                container_name="claude-pty-peerfake",
                user_id=s.query(SessionRow).filter_by(id=sid).one().user_id,
                status="creating",
            )
        )
        s.add(View(session_id="peerfake", port=taken, pid=None))
    v3 = views.open_view(sid, sess["container"])
    check(f"尊重 in-flight 宣告，跳過 {taken} 改用 {v3['port']}", v3["port"] != taken)
    check("新 ttyd 正常起來", views._process_alive(v3["pid"]) and views._port_open(v3["port"]))
    with db.session_scope() as s:
        check("in-flight 宣告未被誤刪", s.query(View).filter_by(port=taken).count() == 1)

    print("== 同一 session 已有 in-flight 宣告：不再起第二個 ttyd（review H1）==")
    # 撞的是 session_id，換 port 一點用都沒有。曾經因為分不出兩種 UNIQUE 衝突，
    # 這裡會把整個 port 範圍每個都等 6 秒 peer（100 × 6s = 10 分鐘）才報「無可用 port」。
    views.close_views(sid)
    with db.session_scope() as s:
        s.add(View(session_id=sid, port=config.TTYD_PORT_MIN + 50, pid=None))
    _t0 = time.time()
    try:
        views.open_view(sid, sess["container"])
        check("同 session 的 in-flight 宣告不該讓 open_view 起第二個 ttyd", False)
    except views.ViewError as e:
        check(f"明確報錯而非硬起（{e}）", "無可用 port" not in str(e))
    _elapsed = time.time() - _t0
    check(f"不空掃整個 port 範圍（耗時 {_elapsed:.1f}s）", _elapsed < 30)
    with db.session_scope() as s:
        s.query(View).filter_by(session_id=sid).delete()
    v3 = views.open_view(sid, sess["container"])  # 宣告清掉後應能正常開

    print("== 逾期未就緒的宣告（宣告者已死）會被回收 ==")
    import datetime as _dtm

    with db.session_scope() as s:
        stale = s.query(View).filter_by(port=taken).one()
        stale.created_at = views.utcnow() - _dtm.timedelta(seconds=config.VIEW_CLAIM_GRACE + 60)
    views.list_views("peerfake")  # 觸發清理（宣告屬於 peerfake，不是 sid）
    with db.session_scope() as s:
        check("逾期宣告已回收（port 可再分配）", s.query(View).filter_by(port=taken).count() == 0)

    print("== terminate session 會提前收掉 view ==")
    pid3 = v3["pid"]
    mgr.terminate(sid)
    sess = None
    check("view 的 ttyd 已被收掉", wait_gone(pid3))
    with db.session_scope() as s:
        # 這裡驗的是「terminate 有收乾淨」，不是 cascade——archive() 已先 close_views。
        # 真正驗 cascade 的是 test_persistence 的「刪 session 連帶清 views」。
        check("terminate 後不留任何 view 記錄", s.query(View).count() == 0)

finally:
    print("== 清理 ==")
    with __import__("contextlib").suppress(Exception):
        for x in mgr.list():
            mgr.terminate(x["id"])
    leftover = _leftovers()
    check("測試結束無殘留 container", len(leftover) == 0)
    stray = subprocess.run(  # 只找本測試 port 區段的，別把正式服務算進來
        ["pgrep", "-f", "ttyd -p 450[0-9][0-9]"], capture_output=True, text=True
    )
    check("測試結束無殘留 ttyd", stray.returncode != 0 or not stray.stdout.strip())
    db.reset_engine()
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
