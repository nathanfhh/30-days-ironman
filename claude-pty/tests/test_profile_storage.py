"""profile 的儲存契約：邏輯層拿到的是 dict，轉換只發生在 DB 邊界。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        python tests/test_profile_storage.py

這支測的是**契約**不是實作。它是 2026-07-28「profile_json 從 Text 改成 JSON/JSONB」那次
轉換的安全網：**先寫、先在舊實作下跑綠，再動實作**。轉換前那一版的差別只在建列的寫法
（當時是 `profile_json=json.dumps(...)`，屬性名 `profile` 還不存在），斷言本身逐條相同——
所以它證得了「舊 DB 的列沒有因為型別宣告改變而讀不到」。

守的性質：
  🔴 **舊 DB 讀得到**。正式環境那顆 SQLite 的欄位是 `TEXT`、值是 JSON 字串；改型別宣告
     之後那些列還是要照常讀出、照常被篩選到。SQLite 是動態型別，宣告改了不會動到既有
     資料——但這件事必須有測試釘住，不能靠「應該沒問題」。
  🔴 **型別不可以在來回中走樣**。bool 要還是 bool（不是 "true"/1）：篩選是拿 JSON 的
     布林去比的，一旦變成字串，`capture=False` 會查出 0 筆而畫面看起來像「一場都沒有」
     ——最難發現的那種壞法。
  🔴 **歸檔要保留 profile**。history 的欄位是另一個，搬過去時型別與內容都要一致。
  🟡 profile 缺鍵／空的列不會讓讀取炸掉（舊 schema 留下的列）。
"""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="profile-storage-")
DB = os.path.join(TMP, "t.db")
config.DB_URL = f"sqlite:///{DB}"

from server.db import init_db, reset_engine, session_scope  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402
from server.models import SessionHistory, User  # noqa: E402
from server.sessions import (  # noqa: E402
    Filters,
    Profile,
    SessionManager,
    _to_dict,
    archive,
    utcnow,
)

reset_engine()
init_db()

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


with session_scope() as s:
    s.add(User(id=1, username="alice", password_hash="x"))

NOW = utcnow()
# 這一份刻意四種型別都有：字串、布林 True、布林 False、以及模型/深度那兩個字串。
FULL = {
    "cli": "claude",
    "network": "unrestricted",
    "capture": True,
    "telemetry": False,
    "model": "sonnet",
    "effort": "xhigh",
}


def add_legacy_row(sid, prof: dict):
    """繞過 ORM，用**舊的寫法**（JSON 字串塞進 TEXT 欄）直接寫一列。

    ⚠ 這正是正式環境那顆 DB 裡既有資料的樣子。用 ORM 寫的話，改完型別宣告之後寫進去的
      就是新格式，那就測不到「舊列還讀不讀得到」——而那是這次改動唯一真正的風險。
    """
    c = sqlite3.connect(DB)
    c.execute(
        "INSERT INTO sessions (id, container_name, user_id, status, workdir,"
        " profile_json, created_at, last_active_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (sid, f"c-{sid}", 1, "running", "/w", json.dumps(prof), NOW.isoformat(sep=" "), NOW.isoformat(sep=" ")),
    )
    c.commit()
    c.close()


print("== 舊 DB 的列（TEXT 欄位 + JSON 字串）讀得到 ==")
add_legacy_row("legacy1", FULL)
with session_scope() as s:
    row = s.get(SessionRow, "legacy1")
    got = _to_dict(row)["profile"]
check("🔴 讀出來就是原本那個 dict（不是字串）", got == FULL)
check("🔴 布林還是布林，沒有變成 'true' 或 1", got["capture"] is True and got["telemetry"] is False)
check("模型與深度原樣", (got["model"], got["effort"]) == ("sonnet", "xhigh"))

print("== 舊列照樣篩得到（篩選是拿 JSON 的值去比的）==")


def live(f=None):
    with session_scope() as s:
        return SessionManager._page(s, None, filters=f).count()


check("cli=claude → 1 筆", live(Filters(cli="claude")) == 1)
check("network=unrestricted → 1 筆", live(Filters(network="unrestricted")) == 1)
check("🔴 capture=True → 1 筆（布林比對）", live(Filters(capture=True)) == 1)
check("🔴 telemetry=False → 1 筆（False 不等於「沒有這個鍵」）", live(Filters(telemetry=False)) == 1)
check("telemetry=True → 0 筆", live(Filters(telemetry=True)) == 0)

print("== 經由邏輯層寫進去的列，讀回來要一模一樣 ==")
# 邏輯層給的是 Profile 這個 dataclass；它 as_dict() 出來就是 dict，
# **不該由呼叫端自己 json.dumps**——序列化是 DB 邊界的事。
prof = Profile(cli="claude", network="restricted", capture=False, telemetry=True, model="sonnet", effort="low")
with session_scope() as s:
    s.add(
        SessionRow(
            id="new1",
            container_name="c-new1",
            user_id=1,
            workdir="/w",
            profile=prof.as_dict(),
            created_at=NOW,
            last_active_at=NOW,
        )
    )
with session_scope() as s:
    got2 = _to_dict(s.get(SessionRow, "new1"))["profile"]
check("🔴 來回不走樣", got2 == prof.as_dict())
check("🔴 布林保持型別", got2["capture"] is False and got2["telemetry"] is True)
check("新列也篩得到", live(Filters(cli="claude", telemetry=True)) == 1)

print("== 存進 DB 的與送進容器的是同一份 ==")
from server.sessions import _stored_profile  # noqa: E402

check(
    "記下來的就是 as_dict()（模型照實記）", _stored_profile(Profile(model="opus", effort="high")).get("model") == "opus"
)

print("== 歸檔：搬進 history 之後 profile 還是同一份 ==")
n = archive(["legacy1"], "terminated")
check("歸檔了 1 筆", n == 1)
rows, total = SessionManager.history()
check("history 有這一筆", total == 1)
check(
    "🔴 history 的 profile 與原本相同（型別也一樣）",
    rows[0]["profile"] == FULL and rows[0]["profile"]["capture"] is True,
)

print("== 缺鍵／空 profile 不會炸 ==")
add_legacy_row("legacy2", {})
with session_scope() as s:
    s.add(
        SessionHistory(
            session_id="h-empty",
            container_name="hc",
            user_id=1,
            username="alice",
            profile={},
            workdir="/w",
            created_at=NOW,
            last_active_at=NOW,
            ended_at=NOW,
            ended_reason="exited",
        )
    )
with session_scope() as s:
    check("空 profile 讀得出空 dict", _to_dict(s.get(SessionRow, "legacy2"))["profile"] == {})
check(
    "🟡 缺鍵的列在「是」與「否」兩邊都查不到（只有不限看得見）",
    live(Filters(capture=True)) == 0 and live(Filters(capture=False)) == 1,
)
check("不限時看得到全部（含缺鍵那筆）", live() == 2)

print("== 清理 ==")
reset_engine()
__import__("shutil").rmtree(TMP, ignore_errors=True)
check("暫存 DB 已清除", not os.path.exists(TMP))

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
