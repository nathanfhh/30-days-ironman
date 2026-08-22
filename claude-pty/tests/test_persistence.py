"""階段 1 regression：持久層地基（ADR 0008）。

驗證 DB 能勝任「跨 worker 唯一仲裁者」的角色：
  - 建表 / CRUD / FK 關聯
  - views.port UNIQUE 撞約束會擋（＝跨 worker 搶 port 的原子性來源）
  - SQLite 開了 WAL + foreign_keys（多 worker 前提）
  - cascade：session 刪除連帶清 views

不需 docker。跑法：
    uv run --with flask --with docker --with sqlalchemy python tests/test_persistence.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="claude-pty-test-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from server import config, db  # noqa: E402
from server.models import STATUS_RUNNING, Session, User, View  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


print("== 建表 ==")
db.init_db()
with db.get_engine().connect() as conn:
    tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
check("users / sessions / views 三張表建立", {"users", "sessions", "views"} <= tables)

print("== SQLite pragma（多 worker 前提）==")
with db.get_engine().connect() as conn:
    journal = conn.execute(text("PRAGMA journal_mode")).scalar()
    fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
check(f"journal_mode = WAL（got {journal}）", str(journal).lower() == "wal")
check(f"foreign_keys 已開（got {fk}）", int(fk) == 1)

print("== CRUD + FK 關聯 ==")
with db.session_scope() as s:
    u = User(username="alice", password_hash="$argon2id$dummy")
    s.add(u)
    s.flush()
    uid = u.id
    s.add(
        Session(
            id="sess0001",
            container_name="claude-pty-sess0001",
            user_id=uid,
            status=STATUS_RUNNING,
            workdir="/home/nathan/code-review",
        )
    )
with db.session_scope() as s:
    sess = s.get(Session, "sess0001")
    check("session 寫入且可讀回", sess is not None and sess.container_name == "claude-pty-sess0001")
    check("FK 關聯到 user", sess.user.username == "alice")
    check("status / workdir 正確", sess.status == STATUS_RUNNING and "code-review" in sess.workdir)
    check(
        "created_at / last_active_at 有值且帶時區",
        sess.created_at.tzinfo is not None and sess.last_active_at.tzinfo is not None,
    )

print("== username UNIQUE ==")
dup_blocked = False
try:
    with db.session_scope() as s:
        s.add(User(username="alice", password_hash="x"))
except IntegrityError:
    dup_blocked = True
check("重複 username 被 UNIQUE 擋下", dup_blocked)

print("== views 的兩個 UNIQUE：port（跨 worker 搶 port）與 session_id（一 session 一 view）==")
with db.session_scope() as s:  # 另一個 session，供「不同 session 可各有 view」使用
    s.add(Session(id="sess0002", container_name="claude-pty-sess0002", user_id=uid, status=STATUS_RUNNING))
    s.add(View(session_id="sess0001", port=41000, pid=1234))

port_taken = False
try:  # 另一個 worker 同時挑到同一 port
    with db.session_scope() as s:
        s.add(View(session_id="sess0002", port=41000, pid=5678))
except IntegrityError:
    port_taken = True
check("同一 port 第二次 INSERT 被擋（撞了就換下一個）", port_taken)

# H1：只有 port UNIQUE 時，兩個 worker 可為同一 session 各起一個 ttyd，其中一個
# 永遠等不到 client（-q 不觸發）而長生不死。session_id UNIQUE 才擋得住。
dup_session = False
try:
    with db.session_scope() as s:
        s.add(View(session_id="sess0001", port=41002, pid=9999))
except IntegrityError:
    dup_session = True
check("同一 session 的第二個 view 被擋（review H1）", dup_session)

with db.session_scope() as s:  # 不同 session + 不同 port 應可並存
    s.add(View(session_id="sess0002", port=41001, pid=5678))
with db.session_scope() as s:
    check("不同 session 各自的 view 可並存", s.query(View).count() == 2)

print("== cascade：刪 session 連帶清 views ==")
with db.session_scope() as s:
    s.delete(s.get(Session, "sess0001"))
with db.session_scope() as s:
    check("session 已刪", s.get(Session, "sess0001") is None)
    check("其 views 一併清除（不留孤兒 port 記錄）", s.query(View).filter_by(session_id="sess0001").count() == 0)
    check("其他 session 的 view 不受影響", s.query(View).filter_by(session_id="sess0002").count() == 1)

print("== 歸檔：session 結束後歷史永久留存（ADR 0010）==")
from server.models import SessionHistory  # noqa: E402
from server.sessions import archive  # noqa: E402

check("歸檔 1 筆", archive(["sess0002"], "terminated") == 1)
with db.session_scope() as s:
    check("登錄已離開 sessions（不再計入配額、不再被對帳）", s.get(Session, "sess0002") is None)
    h = s.query(SessionHistory).filter_by(session_id="sess0002").one()
    check(
        "歷史留下了：容器名、擁有者、結束原因",
        h.container_name == "claude-pty-sess0002" and h.username == "alice" and h.ended_reason == "terminated",
    )
    check("建立時間沿用原始值（不是歸檔當下）", h.created_at is not None)
check("重複歸檔回 0（列已經不在）", archive(["sess0002"], "terminated") == 0)
with db.session_scope() as s:
    # 數過才算驗到：只斷言「回 0」的話，併發下真的寫進第二筆也照樣通過
    check("歷史仍只有一筆", s.query(SessionHistory).filter_by(session_id="sess0002").count() == 1)

print("== 併發歸檔同一筆：只會留下一筆歷史（review C3）==")
import threading  # noqa: E402

with db.session_scope() as s:
    s.add(Session(id="race0001", container_name="claude-pty-race0001", user_id=uid, status=STATUS_RUNNING))
_errors, _results = [], []


def _racer():
    try:
        _results.append(archive(["race0001"], "gone"))
    except Exception as e:  # noqa: BLE001 - 測試要看見任何例外
        _errors.append(e)


_threads = [threading.Thread(target=_racer) for _ in range(4)]
for t in _threads:
    t.start()
for t in _threads:
    t.join()
check(f"四條緒同時歸檔，沒有任何例外冒出來（got {_errors}）", not _errors)
check(f"只有一條真的歸檔成功（got {_results}）", sum(_results) == 1)
with db.session_scope() as s:
    check(
        "歷史只有一筆（UNIQUE + BEGIN IMMEDIATE 擋住了重複）",
        s.query(SessionHistory).filter_by(session_id="race0001").count() == 1,
    )
    check("登錄已消失", s.get(Session, "race0001") is None)

print("== 帳號刪除後歷史仍讀得出來（user_id SET NULL + username 快照）==")
with db.session_scope() as s:
    s.delete(s.query(User).filter_by(username="alice").one())
with db.session_scope() as s:
    h = s.query(SessionHistory).filter_by(session_id="sess0002").one_or_none()
    check("歷史沒有被 cascade 掉", h is not None)
    check("user_id 置空但 username 快照還在", h.user_id is None and h.username == "alice")

print("== 清理 ==")
db.reset_engine()
import shutil  # noqa: E402

shutil.rmtree(_tmp, ignore_errors=True)
check("暫存 DB 已清除", not os.path.exists(_tmp))

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
