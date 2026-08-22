"""列表篩選的 regression（sessions.Filters）。

    uv run --with flask --with docker --with sqlalchemy python tests/test_filters.py

純 DB 層，不碰 docker：直接把列塞進一個暫時的 SQLite 再查。
"""

import datetime as _dt
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config

TMP = tempfile.mkdtemp(prefix="filters-test-")
config.DB_URL = f"sqlite:///{TMP}/t.db"

from server.db import init_db, reset_engine, session_scope  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402
from server.models import SessionHistory, User  # noqa: E402
from server.sessions import Filters, SessionManager, utcnow  # noqa: E402

reset_engine()
init_db()

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def profile(cli="claude", network="restricted", capture=False, telemetry=False):
    return {"cli": cli, "network": network, "capture": capture, "telemetry": telemetry}


with session_scope() as s:
    s.add(User(id=1, username="alice", password_hash="x"))
    s.add(User(id=2, username="bob", password_hash="x"))

now = utcnow()
# 執行中：兩筆 alice、一筆 bob，profile 各不相同
with session_scope() as s:
    for i, (uid, prof, age_days) in enumerate(
        [
            (1, profile(), 0),
            (1, profile(capture=True), 3),
            (2, profile(network="unrestricted", telemetry=True), 20),
        ],
        start=1,
    ):
        s.add(
            SessionRow(
                id=f"s{i}",
                container_name=f"c{i}",
                user_id=uid,
                workdir="/w",
                profile=prof,
                created_at=now - _dt.timedelta(days=age_days),
                last_active_at=now,
            )
        )

# 已結束：刻意讓 created_at 與 ended_at 差很遠，才驗得出「日期比的是哪一欄」
with session_scope() as s:
    s.add(
        SessionHistory(
            session_id="h1",
            container_name="hc1",
            user_id=1,
            username="alice",
            profile=profile(),
            workdir="/w",
            created_at=now - _dt.timedelta(days=40),
            last_active_at=now - _dt.timedelta(days=40),
            # 40 天前開、12 小時前才結束。刻意不用「剛好 1 天」——
            # 邊界值配上查詢當下的 utcnow() 會落在界外，測到的是時鐘不是邏輯。
            ended_at=now - _dt.timedelta(hours=12),
            ended_reason="terminated",
        )
    )
    s.add(
        SessionHistory(
            session_id="h2",
            container_name="hc2",
            user_id=2,
            username="bob",
            profile=profile(capture=True),
            workdir="/w",
            created_at=now - _dt.timedelta(days=2),
            last_active_at=now - _dt.timedelta(days=2),
            ended_at=now - _dt.timedelta(days=2),
            ended_reason="exited",
        )
    )


def live(f=None):
    """執行中列表的筆數（不經 docker：直接數 _page 的結果）。"""
    with session_scope() as s:
        return SessionManager._page(s, None, filters=f).count()


def past(f=None):
    return SessionManager.history(filters=f)[1]


print("== 不限：全部都在 ==")
check("執行中 3 筆", live() == 3)
check("已結束 2 筆", past() == 2)

print("== 三態的中間那一態不可以塌成「否」 ==")
# 這是整個設計的核心：Filters 的 None 代表「不管」，False 代表「明確要沒有的」。
# 兩者塌在一起的話，「沒有錄製的」就永遠篩不出來。
check("capture=None（不限）→ 3 筆", live(Filters()) == 3)
check("capture=True → 1 筆", live(Filters(capture=True)) == 1)
check("capture=False → 2 筆", live(Filters(capture=False)) == 2)
check("True + False 兩邊加起來等於不限", live(Filters(capture=True)) + live(Filters(capture=False)) == live(Filters()))

print("== 布林走 as_boolean，不是拿字串比 'true'/'false' ==")
# ⚠ 回歸守門：同一個 JSON 布林，SQLite 的 json_extract 回整數 0/1、PostgreSQL 的 ->>
#   回文字 'false'。曾經拿 as_string() 去比 "false"，在 SQLite 上永遠 0 筆——畫面看起來
#   像「一場都沒有」，不會報錯，最難發現的那種。
check("telemetry=True → 1 筆", live(Filters(telemetry=True)) == 1)
check("telemetry=False → 2 筆（不是 0）", live(Filters(telemetry=False)) == 2)

print("== 字串類條件 ==")
check("cli=claude → 3 筆", live(Filters(cli="claude")) == 3)
check("network=unrestricted → 1 筆", live(Filters(network="unrestricted")) == 1)

print("== 多個條件是 AND，不是 OR ==")
check("restricted + 有錄製 → 1 筆", live(Filters(network="restricted", capture=True)) == 1)
# 那一筆有錄製沒錯，但它沒有 telemetry——OR 的話會錯誤地算進來
check("有錄製 + 有 telemetry → 0 筆", live(Filters(capture=True, telemetry=True)) == 0)

print("== 日期區間：兩張表比的是**不同欄位** ==")
# 執行中的表比 created_at（「一週內開的」）
check("執行中 since=7 → 2 筆", live(Filters(since_at=now - _dt.timedelta(days=7))) == 2)
check("執行中 since=30 → 3 筆", live(Filters(since_at=now - _dt.timedelta(days=30))) == 3)
# ⚠ 已結束的表比 ended_at（「一週內結束的」）。h1 是 40 天前開、昨天結束的——
#   比 created_at 的話它會落在「40 天前」，而使用者是在找它**結束**的那一天。
check(
    "已結束 since=7 → 2 筆（含 40 天前開、12 小時前才結束的那筆）",
    past(Filters(since_at=now - _dt.timedelta(days=7))) == 2,
)
check(
    "已結束 since=1 → 只剩 12 小時前結束的那筆（兩天前的被排除）",
    past(Filters(since_at=now - _dt.timedelta(days=1))) == 1,
)

# ⚠ 這裡曾經有三條 `Filters.active()` 的斷言，標題寫「畫面上的『篩選 · N』與清除鈕靠它」
#   ——那是假的。那個數字**在前端算**（見 sessions.html 的 paintFilters），後端那支從來
#   沒有任何呼叫端，只有這三條測試在餵它。連同它一起刪掉了；「起迄兩端算一格」那條規則
#   的正本在前端那段註解裡。

print("== count 與 list 必須同條件（否則頁碼會錯）==")
f = Filters(capture=False)
with session_scope() as s:
    page = SessionManager._page(s, None, limit=1, offset=0, filters=f).all()
check("list 拿第一頁 1 筆", len(page) == 1)
check("count 回的是**篩過**的總數 2，不是全部 3", live(f) == 2)

print("== 從 HTTP 端點驗一次：total 與回傳筆數必須一致 ==")
# ⚠ 這一組是回歸守門，而它必須守**當初真的發生的那件事**：`list()` 收了 filters 卻沒往下
#   傳，於是 API 回出 `total: 0` 配上兩筆資料。先前這裡只斷言「list() 有把 filters 交給
#   _page」——那擋得住同一個寫法的重現，卻擋不住「forward 了但 list 與 count 用了不同條件」
#   或「日後多一個呼叫端」。改成打真正的端點、比對 total 與實際回傳的筆數。
#   （這也是唯一能同時涵蓋 app._filters_from_args 解析的層級。）
import server.app as app_mod  # noqa: E402
from server import auth as auth_mod  # noqa: E402

app_mod.app.config["TESTING"] = True


# list() 會去問 docker 對帳。換成「看得到我們塞的那三個容器」的假 client，
# 否則每一列都會被判 gone 而歸檔，測到的就不是篩選了。
class _FakeContainer:
    def __init__(self, name):
        self.name, self.status, self.id = name, "running", f"cid-{name}"

    def logs(self, **_kw):
        return b""  # list() 會讀 log 判 ready；這裡不測那個


class _FakeDocker:
    class containers:
        @staticmethod
        def list(**_kw):
            return [_FakeContainer(f"c{i}") for i in (1, 2, 3)]


app_mod.manager._docker = _FakeDocker()

USERS = {
    1: {"id": 1, "username": "alice", "is_admin": True, "password_version": 1},
    2: {"id": 2, "username": "bob", "is_admin": False, "password_version": 1},
}
# 走**真正的** authn gate（不是塞一個 before_request——那會註冊在 gate 之後而被 401 擋下，
# 也就測不到 gate 本身）。只換掉查帳號那一步，其餘照原路徑跑。
auth_mod.get_user = lambda uid: USERS.get(uid)
app_mod.auth.get_user = auth_mod.get_user

client = app_mod.app.test_client()


def login_as(uid):
    with client.session_transaction() as sess:
        sess["uid"] = uid
        sess["pwv"] = USERS[uid]["password_version"]


def hit(qs):
    r = client.get(f"/api/sessions{qs}")
    assert r.status_code == 200, (qs, r.status_code, r.data[:200])
    return r.get_json()


login_as(1)  # alice（admin，看得到全部）
for qs, label in (
    ("", "無條件"),
    ("?cli=claude", "cli=claude"),
    ("?capture=1", "有錄製"),
    ("?capture=0", "沒錄製"),
    ("?capture=1&telemetry=1", "有錄製+有 telemetry"),
):
    d = hit(qs)
    check(f"{label:14} total={d['total']}，與實際回傳 {len(d['sessions'])} 筆一致", d["total"] == len(d["sessions"]))

check(
    "篩過的總數確實比不篩少（否則上面整組會因為『反正全都回』而假通過）", hit("?capture=1")["total"] < hit("")["total"]
)

# ADR 0012：列表不再自己問 docker，改回報「最後一次跟 dockerd 求證的時刻」。前端靠這個
# 欄位顯示新鮮度——欄位掉了的話畫面會整片變成「未確認」，而那看起來像資料壞掉。
check(
    "每一列都帶著 state_checked_at 欄位（可以是 null，但鍵必須在）",
    all("state_checked_at" in row for row in hit("")["sessions"]),
)

print("== 壞的篩選參數要回 400，不是默默忽略 ==")
for qs in ("?capture=yes", "?cli=bogus", "?cli=nope", "?since=0", "?since=abc"):
    r = client.get(f"/api/sessions{qs}")
    check(f"{qs:16} → {r.status_code}", r.status_code == 400)

print("== 自訂時間區間（from / to）==")
# 畫面上的「一週內」與「自訂範圍」在查詢層是同一個東西（絕對區間），這裡驗 API 這一層。
# ⚠ 時區偏移的 `+` 在 query string 裡會被解讀成空白，必須編成 %2B。
#   瀏覽器的 URLSearchParams 會自動處理，手寫的 client（curl、文件範例）不會。
from urllib.parse import quote  # noqa: E402


def iso(d):
    return quote(d.astimezone(_dt.timezone(_dt.timedelta(hours=8))).isoformat(), safe="")


# s1 今天、s2 三天前、s3 二十天前
check("from=四天前 → 只剩兩筆（今天與三天前）", hit(f"?from={iso(now - _dt.timedelta(days=4))}")["total"] == 2)
check("to=四天前 → 只剩最舊那筆", hit(f"?to={iso(now - _dt.timedelta(days=4))}")["total"] == 1)
check(
    "from+to 夾出中間那一筆",
    hit(f"?from={iso(now - _dt.timedelta(days=5))}&to={iso(now - _dt.timedelta(days=1))}")["total"] == 1,
)
r = client.get(f"/api/sessions?since=7&from={iso(now)}")
check("since 與 from 併用 → 400（兩種區間語意擇一）", r.status_code == 400)
r = client.get(f"/api/sessions?from={iso(now)}&to={iso(now - _dt.timedelta(days=1))}")
check("from 晚於 to → 400", r.status_code == 400)
check("時間格式不對 → 400", client.get("/api/sessions?from=not-a-time").status_code == 400)
# ⚠ 不帶時區的話後端無從得知那是哪一區的 14:30，差 8 小時的區間會靜靜查錯——要明確拒絕
r = client.get("/api/sessions?from=2026-07-26T14:30:00")
check("不帶時區偏移 → 400（不猜時區）", r.status_code == 400)
check("錯誤訊息點出要帶時區", "時區" in r.get_json()["error"])

print("== 沒有 owner 篩選，但授權那層照樣綁死 ==")
# owner 篩選整組拔掉了（Filters 沒有 owner_id、API 不認 ?owner=）。這裡守住兩件事：
# 1) 授權不是靠篩選——非 admin 在 list/history 那層本來就被 user_id 綁死；
# 2) ?owner= 這種殘留書籤不會讓整個連結作廢（未知參數一律忽略，與其他未知參數一致）。
check("Filters 不再有 owner_id 欄位", not hasattr(Filters(), "owner_id"))
login_as(2)  # bob（一般使用者，只有 s3）
check("非 admin 只看得到自己的那一筆", hit("")["total"] == 1)
check("殘留的 ?owner= 書籤不作廢、也繞不過授權（仍只有自己的）", hit("?owner=1")["total"] == 1)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
