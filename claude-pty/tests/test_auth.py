"""認證 / 授權 regression（ADR 0005 authn+authz、ADR 0008 階段 4）。

用 Flask test client，不需 docker。
    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        python tests/test_auth.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="claude-pty-auth-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["CLAUDE_PTY_SECRET_KEY"] = "test-secret-key-not-for-production"

from server import auth, config, db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()
db.init_db()

from server.app import app  # noqa: E402

app.config["TESTING"] = True

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


print("== 密碼以 argon2id 雜湊，絕不明文 ==")
admin = auth.create_user("admin", "admin-password-1", is_admin=True)
alice = auth.create_user("alice", "alice-password-1")
with db.session_scope() as s:
    from server.models import User

    row = s.query(User).filter_by(username="alice").one()
    stored = row.password_hash
check("雜湊為 argon2id 格式", stored.startswith("$argon2id$"))
check("雜湊不含明文密碼", "alice-password-1" not in stored)
check("同密碼兩次雜湊不同（有 salt）", auth.hash_password("same-pw-12345") != auth.hash_password("same-pw-12345"))

print("== 認證：錯密碼 / 不存在帳號 / system 帳號都不可登入 ==")


def _fails_auth(u, p):
    try:
        auth.authenticate(u, p)
        return False
    except auth.AuthError:
        return True


check("正確帳密可通過", auth.authenticate("alice", "alice-password-1")["id"] == alice["id"])
check("錯密碼被拒", _fails_auth("alice", "wrong-password"))
check("不存在的帳號被拒", _fails_auth("nobody", "whatever-123"))
from server.sessions import ensure_system_user  # noqa: E402

ensure_system_user()
check("system 帳號（hash='!'）無法登入", _fails_auth(config.SYSTEM_USERNAME, "!"))
short_rejected = False
try:
    auth.create_user("shorty", "123")
except auth.AuthError:
    short_rejected = True
check("密碼過短被拒（建立帳號時）", short_rejected)

print("== authn gate：未登入不能碰任何 /api/* ==")
c = app.test_client()
for method, path in [
    ("get", "/api/sessions"),
    ("post", "/api/sessions"),
    ("get", "/api/auth/me"),
    ("get", "/api/users"),
    ("get", "/api/auth/view?session=x"),
]:
    r = getattr(c, method)(path)
    check(f"未登入 {method.upper()} {path} → 401", r.status_code == 401)

print("== 登入 / 登出流程 ==")
r = c.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
check("錯密碼登入 → 400", r.status_code == 400)
r = c.post("/api/auth/login", json={"username": "alice", "password": "alice-password-1"})
check("正確登入 → 200", r.status_code == 200 and r.get_json()["user"]["username"] == "alice")
r = c.get("/api/auth/me")
check("登入後 /me 回自己", r.status_code == 200 and r.get_json()["user"]["username"] == "alice")
r = c.get("/api/sessions")
check("登入後可讀 /api/sessions", r.status_code == 200)
c.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})
check("登出後 /me → 401", c.get("/api/auth/me").status_code == 401)

print("== authz：一般使用者不得碰管理端點 ==")
c.post("/api/auth/login", json={"username": "alice", "password": "alice-password-1"})
check("一般使用者 GET /api/users → 403", c.get("/api/users").status_code == 403)
check(
    "一般使用者 POST /api/users → 403",
    c.post("/api/users", json={"username": "x", "password": "12345678"}).status_code == 403,
)

check(
    "一般使用者 POST /api/users/<uid>/password（代改他人密碼）→ 403",
    c.post(f"/api/users/{admin['id']}/password", json={"new_password": "hijacked-password"}).status_code == 403,
)
# 🔴 事後改權限的路徑**整條不存在**——不是 403，是根本沒有這個端點。權限只在建立
#    時決定；沒有提權/降權，就沒有「一般使用者把自己提上去」這個攻擊面。這裡釘的是
#    「路由真的不在」：哪天有人把 PATCH /api/users/<uid> 加回來，這兩條會先紅。
check(
    "PATCH /api/users/<uid> 不存在（一般使用者，405/404 而非 403）",
    c.patch(f"/api/users/{admin['id']}", json={"is_admin": True}).status_code in (404, 405),
)
check("被擋下之後 alice 仍不是管理員", not next(u for u in auth.list_users() if u["username"] == "alice")["is_admin"])

print("== authz：不得存取別人的 session（回 404 不洩漏存在性）==")
from server.models import Session as SessionRow  # noqa: E402

with db.session_scope() as s:  # 直接塞一筆屬於 admin 的 session
    s.add(SessionRow(id="otherses1", container_name="claude-pty-otherses1", user_id=admin["id"], status="running"))
r = c.get("/api/sessions/otherses1")
check("讀別人的 session → 404（不洩漏存在性，review L1）", r.status_code == 404)
check("回應不洩漏擁有者資訊", "admin" not in r.get_data(as_text=True))
r = c.delete("/api/sessions/otherses1", headers={"X-Requested-With": "fetch"})
check("刪別人的 session 被擋", r.status_code == 404)
# 讀與刪之外的每一條寫入路徑也要各自擋——授權是 per-endpoint 的，漏掉一個就是漏掉
# 一整條可以對別人的 TTY 下指令的路
for label, call in [
    ("改尺寸 /resize", lambda: c.post("/api/sessions/otherses1/resize", json={"rows": 10, "cols": 10})),
    ("開終端 /view", lambda: c.post("/api/sessions/otherses1/view", json={})),
    ("列終端 /view", lambda: c.get("/api/sessions/otherses1/view")),
    # 關掉別人的 view 不會殺掉 session，但會把對方瀏覽器裡的終端從中斷掉。開與列都測了
    # 卻獨漏關，正是重構時最容易把 `_owned()` 拿掉而沒人發現的那一條。
    ("關終端 /view", lambda: c.delete("/api/sessions/otherses1/view", headers={"X-Requested-With": "fetch"})),
    ("改名 PATCH", lambda: c.patch("/api/sessions/otherses1", json={"name": "hijack"})),
]:
    check(f"對別人的 session {label} → 404", call().status_code == 404)
with db.session_scope() as s:
    check("別人的 session 仍在（未被誤刪）", s.get(SessionRow, "otherses1") is not None)
r = c.get("/api/sessions")
check("列表只看得到自己的（不含別人的）", all(x["id"] != "otherses1" for x in r.get_json()["sessions"]))

print("== admin 可看全部、可管理帳號 ==")
ca = app.test_client()
ca.post("/api/auth/login", json={"username": "admin", "password": "admin-password-1"})
check("admin 可讀 /api/users", ca.get("/api/users").status_code == 200)
r = ca.post("/api/users", json={"username": "bob", "password": "bob-password-1"})
check("admin 可新增使用者", r.status_code == 201)
r = ca.post("/api/users", json={"username": "bob", "password": "bob-password-1"})
check("重複使用者名稱被拒", r.status_code == 400)
r = ca.post("/api/users", json={"username": "short", "password": "123"})
check("密碼過短被拒（API 層）", r.status_code == 400)

print("== profile 的列舉白名單（_ENUMS 是唯一的驗證關口）==")
# `SessionManager.create(profile=...)` 完全不驗，任何字串都會被塞進 env 送進容器——
# HTTP 層這道檢查就是唯一的關口，而它先前一條測試都沒有（review 2026-07-25 指出）。
for field, bad in [("cli", "bash"), ("network", "wide-open"), ("model", "nope-9"), ("effort", "nope")]:
    r = ca.post("/api/sessions", json={"profile": {field: bad}})
    body = r.get_json() or {}
    check(
        f"profile.{field}={bad!r} → 400 且訊息列出合法值",
        r.status_code == 400 and field in body.get("error", "") and "只能是" in body.get("error", ""),
    )

print("== 改密碼 ==")


def _session_cookie(client):
    """取出這個 client 目前持有的 session cookie **值**。

    要驗的是「這一串字串還能不能用」，不是「這個 client 物件還能不能用」——後者是前者
    的代理指標，而代理指標會在實作換法時給出錯誤的答案（見下面 H4 那段）。
    """
    for key, ck in client._cookies.items():
        if getattr(ck, "key", None) == "session" or (isinstance(key, tuple) and "session" in key):
            return ck.value
    return None


stolen = _session_cookie(c)  # 改密碼**之前**就被複製走的那一張
r = c.post("/api/users/me/password", json={"old_password": "wrong", "new_password": "alice-new-password"})
check("舊密碼錯 → 400", r.status_code == 400)
r = c.post("/api/users/me/password", json={"old_password": "alice-password-1", "new_password": "alice-new-password"})
check("舊密碼正確 → 204", r.status_code == 204)
check("舊密碼已失效", _fails_auth("alice", "alice-password-1"))
check("新密碼可登入", auth.authenticate("alice", "alice-new-password")["id"] == alice["id"])

# H4：改密碼要讓**先前簽發的 cookie** 全部失效——那才是這條 review 要的性質（有人偷走
# cookie，換密碼就是要把他踢出去）。
#
# ⚠ 用「把偷到的那串字重播回去」來驗，而不是「這個 client 物件還能不能用」：後者是
#   代理指標，實作換法時會給出錯誤的答案。這裡兩者現在剛好同時成立，但要驗的性質
#   始終是前者——那張被複製走的 cookie 不可以再用。
thief = app.test_client()
thief.set_cookie("session", stolen, domain="localhost")
check("改密碼後，先前簽發的那張 cookie 重播回去 → 401（review H4）", thief.get("/api/auth/me").status_code == 401)
# 🔴 **按下送出的這一台也一樣被踢下線，沒有特例。** 改密碼的語意是「這個帳號現在
#    連著的東西全部斷掉」；留一個例外就讓那句話變成說一套做一套，而它換到的只是
#    少按幾個鍵。
check("🔴 按下送出的這一台也被登出（不留特例）", c.get("/api/auth/me").status_code == 401)
c.post("/api/auth/login", json={"username": "alice", "password": "alice-new-password"})
check("以新密碼重新登入後恢復", c.get("/api/auth/me").status_code == 200)

print("== 退場＝管理員改掉他的密碼（沒有「停用」這個狀態）==")
# 停用能做到的三件事，改密碼全部做得到：登不進來（他不知道新密碼）、既有 cookie 全滅
# （版號遞增）、開著的終端被切（呼叫端收 view）。可逆性也還在：把新密碼告訴他。
# 這一段驗的就是這個等價性——它是拔掉 is_active 的前提，不成立就不能拔。
bob_id = next(u["id"] for u in ca.get("/api/users").get_json()["users"] if u["username"] == "bob")
cb = app.test_client()
check(
    "退場前可登入",
    cb.post("/api/auth/login", json={"username": "bob", "password": "bob-password-1"}).status_code == 200,
)
r = ca.post(f"/api/users/{bob_id}/password", json={"new_password": "exit-password-1"})
check("admin 改掉他的密碼 → 204", r.status_code == 204)
check("🔴 舊密碼登不進去（他不知道新密碼＝回不來）", _fails_auth("bob", "bob-password-1"))
check("🔴 既有 cookie 立即失效", cb.get("/api/auth/me").status_code == 401)
check("可逆：把新密碼告訴他，就回來了", auth.authenticate("bob", "exit-password-1")["id"] == bob_id)

print("== nginx auth_request 端點 ==")
# auth_view 是給 nginx auth_request 用的，一律回 403（nginx 據此擋下並導回）——
# 與一般 API 的 404 不同，因為 nginx 的 auth_request 只認 2xx/401/403。
r = c.get("/api/auth/view?session=otherses1")
check("非擁有者 → 403（nginx 據此擋下）", r.status_code == 403)
r = c.get("/api/auth/view?session=nonexistent")
check("不存在的 session → 403", r.status_code == 403)

print("== /api/auth/check：ttyd --auth-url 的第二層，純判定零副作用 ==")
# 這支被每個 asset 與 WS 升級各打一次。斷言三件事：判定對、不開 view、不碰 dockerd。
from server import views as _views_probe  # noqa: E402

_opened: list = []
_orig_open = _views_probe.open_view
_views_probe.open_view = lambda *a, **k: _opened.append(a) or {"port": 1}
_app_manager = __import__("server.app", fromlist=["manager"]).manager
_orig_docker = getattr(_app_manager, "_docker", None)


class _NoDocker:
    def __getattr__(self, name):
        raise AssertionError("auth_check 不可以碰 dockerd")


_app_manager._docker = _NoDocker()
try:
    check("非擁有者 → 403", c.get("/api/auth/check?session=otherses1").status_code == 403)
    check(
        "不存在 → 403（不是 404——消費者是只認 2xx/401/403 的守門者）",
        c.get("/api/auth/check?session=nonexistent").status_code == 403,
    )
    ca_chk = app.test_client()
    ca_chk.post("/api/auth/login", json={"username": "admin", "password": "admin-password-1"})
    check("擁有者 → 204", ca_chk.get("/api/auth/check?session=otherses1").status_code == 204)
    check(
        "未登入 → 401（authn 由 gate 做，這支不豁免）",
        app.test_client().get("/api/auth/check?session=otherses1").status_code == 401,
    )
    check("🔴 全程沒有開任何 view（副作用零——那是 /api/auth/view 的事）", _opened == [])
finally:
    _views_probe.open_view = _orig_open
    _app_manager._docker = _orig_docker

print("== 權限只在建立時決定（沒有提權/降權端點）==")
# 「最後一位管理員」不再需要專屬防線：能拿走管理員身分的兩條路（降權、停用）都不存在
# 了。admin 改掉另一位 admin 的密碼不會動到 is_admin——帳號還是管理員，只是換了鑰匙，
# 而鑰匙在動手的那位 admin 手上。這裡釘住「改密碼不動權限」，防線才真的閉合。
ca2 = app.test_client()
ca2.post("/api/auth/login", json={"username": "admin", "password": "admin-password-1"})
admin2 = auth.create_user("admin2", "admin2-password", is_admin=True)
ca2.post(f"/api/users/{admin2['id']}/password", json={"new_password": "admin2-rotated-1"})
check("🔴 admin 代改另一位 admin 的密碼，不會動到 is_admin", auth.get_user(admin2["id"])["is_admin"] is True)

print("== 建立帳號的輸入驗證（權限邊界不接受型別猜測）==")
victim = auth.create_user("victim", "victim-password")
for bad in ({"is_admin": "yes"}, {"is_admin": 1}, {"is_admin": "tru"}, {"role": "admin"}):
    r = ca2.post(
        "/api/users", json={"username": f"strict-{abs(hash(str(bad))) % 9999}", "password": "strict-password-1", **bad}
    )
    check(f"POST /api/users {bad} → 400", r.status_code == 400)
r = ca2.post("/api/users", json={"username": "strict-ok", "password": "strict-password-1", "is_admin": False})
check("POST /api/users 收真正的 boolean", r.status_code == 201 and r.get_json()["user"]["is_admin"] is False)

print("== 帳號沒有刪除路徑（ADR 0010：退場是改掉密碼，不是刪除）==")
check(
    "DELETE /api/users/<uid> 不存在（405 或 404，總之不是 2xx）",
    ca2.delete(f"/api/users/{victim['id']}").status_code >= 400,
)
check("auth 模組不再提供 delete_user", not hasattr(auth, "delete_user"))
check("被拒之後帳號仍在", any(u["username"] == "victim" for u in auth.list_users()))
r = ca2.patch(
    f"/api/users/{[u for u in auth.list_users() if u['username'] == 'admin'][0]['id']}", json={"is_admin": False}
)
check("PATCH /api/users/<uid> 連 admin 也打不到（路由不存在）", r.status_code in (404, 405))

print("== 公開的 login 端點不可被畸形輸入打成 500（review 2026-07-26）==")
# ⚠ 這條**未登入就打得到**。任何 500 都等於「誰都能在日誌裡刷 traceback」，而 argon2 拿到
#   非字串會在 `.encode()` 拋 AttributeError——truthy 的那些（1 / True / [1] / {...} / 3.14）
#   `password or ""` 擋不掉，會原樣送進去。username 早就修過同一個洞，隔壁那行漏了。
anon = app.test_client()
for bad in (1, True, [1], {"a": 1}, 3.14, [], {}, 0, None):
    r = anon.post("/api/auth/login", json={"username": "nobody", "password": bad})
    check(f"login password={bad!r} → 400（不是 500）", r.status_code == 400)
for bad in (1, True, [1], {"a": 1}, 3.14):
    r = anon.post("/api/auth/login", json={"username": bad, "password": "x"})
    check(f"login username={bad!r} → 400（不是 500）", r.status_code == 400)
check(
    "正常帳密仍然登得進去（防禦沒有擋到好人）",
    anon.post("/api/auth/login", json={"username": "alice", "password": "alice-new-password"}).status_code == 200,
)

print("== 改自己的密碼：非字串的舊密碼也不可以是 500 ==")
cme = app.test_client()
cme.post("/api/auth/login", json={"username": "alice", "password": "alice-new-password"})
for bad in (1, True, [1], {"a": 1}):
    r = cme.post("/api/users/me/password", json={"old_password": bad, "new_password": "whatever-long-enough"})
    check(f"old_password={bad!r} → 400（不是 500）", r.status_code == 400)

print("== system 帳號必須維持無法登入（不可以幫它設密碼）==")
# system 的 password_hash 是不可用值 `!`，那是它登不進來的唯一保障。它 is_admin=True、
# 出現在 /api/users 清單上，admin 點得到那顆重設密碼——設一個真密碼就等於憑空多一個
# 管理員，而 `_count_usable_admins` 還會把它排除在外，「至少留一位管理員」也跟著失準。
from server import sessions as _sessions_mod  # noqa: E402

_sys_uid = _sessions_mod.ensure_system_user()
r = ca2.post(f"/api/users/{_sys_uid}/password", json={"new_password": "hijacked-password-1"})
check("admin 代改 system 密碼 → 400", r.status_code == 400)
try:
    auth.authenticate(config.SYSTEM_USERNAME, "hijacked-password-1")
    still_locked = False
except auth.AuthError:
    still_locked = True
check("擋下之後 system 真的還是登不進來", still_locked)

print("== 表單型 CSRF：沒有 body 的 <form> POST 也要被擋（review 2026-07-26）==")
# ⚠ `<form method=post>` 沒有任何欄位時送的是 Content-Length: 0 的 urlencoded。閘門原本拿
#   `request.content_length` 當前置條件，於是那一發整條檢查被跳過＝以受害者的身分建出一個
#   session。SameSite=Lax 補不上：它是 **site** 級的，不分 port，localhost 上任何其他 port
#   都算同一個 site。
cme2 = app.test_client()
cme2.post("/api/auth/login", json={"username": "alice", "password": "alice-new-password"})
FORM_SHAPES = [
    ("無欄位的表單", "application/x-www-form-urlencoded", b""),
    ("有欄位的表單", "application/x-www-form-urlencoded", b"a=1"),
    ("enctype=text/plain 且無 body", "text/plain", b""),
    ("enctype=multipart 且無 body", "multipart/form-data; boundary=x", b""),
]
for label, ctype, data in FORM_SHAPES:
    check(
        f"{label} 打 /api/sessions → 415",
        cme2.post("/api/sessions", headers={"Content-Type": ctype}, data=data).status_code == 415,
    )
    # ⚠ logout 要用**用完即丟**的 client。閘門若失效，這一發是真的會把人登出的——共用
    #   cme2 的話，後面每一條斷言都會變成 401 而跟著紅，失敗訊息就不再指向單一原因。
    #   （這個連鎖本身正是這個 finding 的症狀，但測試該讓人一眼看出壞的是哪一件事。）
    throwaway = app.test_client()
    throwaway.post("/api/auth/login", json={"username": "alice", "password": "alice-new-password"})
    check(
        f"{label} 打 /api/auth/logout → 415",
        throwaway.post("/api/auth/logout", headers={"Content-Type": ctype}, data=data).status_code == 415,
    )
    check(f"{label} 被擋下之後，那個 client 仍然是登入狀態", throwaway.get("/api/auth/me").status_code == 200)
print("== 沒有 body 的 fetch 也打得進來，所以那條放行要加一道 form 設不了的標頭 ==")
# ⚠ 這裡原本只有一條斷言：「無 body 無 Content-Type 的 DELETE 沒被 415 誤傷」——它把
#   **正是這個洞**的形狀寫成了合法路徑（審查 F-002）。`<form>` 送不出「無 body 無
#   Content-Type」沒錯，但這個送得出來，而且會帶 cookie：
#       fetch(url, {method: "POST", mode: "no-cors", credentials: "include"})
#   於是同一台機器上任何其他 port 的頁面（Lax 之下同一個 site）就能以受害者的身分建出
#   一個帶 NET_ADMIN、掛著他憑證的容器。
# ⚠ 關掉它的是 `X-Requested-With`：不在 CORS 安全列表裡 → no-cors 送不出去，
#   `<form>` 也設不了。三條一起測，缺一條這個洞就補不完整。
check(
    "🔴 無 body 無標頭的 POST /api/sessions → 415（不是 201 建出容器）", cme2.post("/api/sessions").status_code == 415
)
check(
    "🔴 無 body 無標頭的 DELETE 也擋（同一條放行，不是只擋 POST）",
    cme2.delete("/api/sessions/does-not-exist").status_code == 415,
)
# 前端 api() 無條件送這個標頭，那條合法路徑不可以被誤傷
check(
    "帶了 X-Requested-With 的無 body DELETE 照樣通行（走到 authz 回 404）",
    cme2.delete("/api/sessions/does-not-exist", headers={"X-Requested-With": "fetch"}).status_code == 404,
)

print("== 壞掉的 JSON body 不可以被當成 {} 去跑預設動作 ==")
check(
    "語法壞掉的 JSON → 400（不是靜靜建出一個預設 session）",
    cme2.post("/api/sessions", headers={"Content-Type": "application/json"}, data=b'{"name": "hi"').status_code == 400,
)
check(
    "body 是 JSON null → 400",
    cme2.post("/api/sessions", headers={"Content-Type": "application/json"}, data=b"null").status_code == 400,
)

print("== session 端點也要拒絕未知欄位 / 嚴格布林（與 /api/users 同一套）==")
for bad, why in (
    ({"nmae": "整理報告"}, "name 拼錯"),
    ({"profile": {"captur": True}}, "profile 鍵拼錯"),
    ({"profile": {"capture": "treu"}}, "布林值拼錯"),
    ({"profile": {"capture": 1}}, "布林給整數"),
):
    check(f"{why} → 400（不是建出一個設定錯的容器）", cme2.post("/api/sessions", json=bad).status_code == 400)
# rename 的擁有權檢查排在驗證**之前**（那是對的順序：不該讓別人靠錯誤訊息探測 sid
# 存不存在），所以要驗未知欄位就得先有一列真的屬於自己的 session。
check(
    "rename 不存在的 sid → 404（authz 先於驗證，不洩漏存在性）",
    cme2.patch("/api/sessions/does-not-exist", json={"nmae": "x"}).status_code == 404,
)
from server.models import Session as _SessionRow  # noqa: E402

_alice_id = [u for u in auth.list_users() if u["username"] == "alice"][0]["id"]
with db.session_scope() as s:
    s.add(_SessionRow(id="renametest01", container_name="claude-pty-renametest01", user_id=_alice_id, workdir="/tmp"))
check("rename 的未知欄位 → 400", cme2.patch("/api/sessions/renametest01", json={"nmae": "x"}).status_code == 400)
check("rename 合法欄位仍然可用", cme2.patch("/api/sessions/renametest01", json={"name": "新名字"}).status_code == 200)

print("== 撤銷存取權時，已經連上的終端也要斷（review 2026-07-26）==")
# ⚠ cookie / token 失效要到**下一次 HTTP 請求**才擋得住，而已經升級完成的 ttyd WebSocket
#   不會再走 nginx 的 auth_request——連線活著的期間，對方手上就是一個可互動的 shell。
#   曾有一條「停用帳號」路徑早就想到這件事並收掉 view；「重設密碼」與「改自己的密碼」漏了，
#   而後者的典型情境正是「我懷疑被盜了」。
# ⚠ 這一段原本 stub 掉 `app.manager.list`，因為收終端的實作走的是它。收終端後來搬進
#   `auth.change_password`（不變式跟著操作走，否則 cli.py 那條路徑漏掉——審查 F-003），
#   而新實作直接查 `Session` 表。**stub 綁在協作者上，換一個實作就測不到東西了**，
#   所以改成建真的登錄列：這樣測的是「這個人的 session 有沒有被收」這個行為本身，
#   不管它是誰去查出來的。
from server import views as _views_mod  # noqa: E402

_closed: list[str] = []
_orig_close = _views_mod.close_views
_views_mod.close_views = lambda sid: _closed.append(sid) or 1
try:
    victim_id = [u for u in auth.list_users() if u["username"] == "victim"][0]["id"]
    alice_id = [u for u in auth.list_users() if u["username"] == "alice"][0]["id"]
    with db.session_scope() as s:
        s.add(_SessionRow(id="cutvictim01", container_name="claude-pty-cutvictim01", user_id=victim_id, workdir="/tmp"))
    _closed.clear()
    r = ca2.post(f"/api/users/{victim_id}/password", json={"new_password": "reset-by-admin-1"})
    check("admin 代改密碼成功", r.status_code == 204)
    check("→ 有收掉那個人開著的終端（不然重設完對方還握著 shell）", _closed == ["cutvictim01"])

    _closed.clear()
    cme3 = app.test_client()
    cme3.post("/api/auth/login", json={"username": "alice", "password": "alice-new-password"})
    r = cme3.post(
        "/api/users/me/password", json={"old_password": "alice-new-password", "new_password": "alice-newer-pw-1"}
    )
    check("改自己的密碼成功", r.status_code == 204)
    # alice 名下有 renametest01（上一段建的），所以這裡預期收到它。
    check("→ 也要收掉終端（換密碼的理由通常就是懷疑被盜）", _closed == ["renametest01"])
    # 🔴 **沒有「這一台除外」的特例。** 改密碼＝這個帳號現在連著的東西全部斷掉。
    #    先前這裡是「操作中的這一台不被登出（cookie 版本有續上）」——那個例外換到的
    #    只是少按幾個鍵，卻讓「全部失效」這句話變成說一套做一套。
    check("🔴 操作中的這一台也被登出（不留特例）", cme3.get("/api/auth/me").status_code == 401)

finally:
    _views_mod.close_views = _orig_close

print("== 偏好設定：要用哪一顆 ttyd（per-user，會變成 argv[0]）==")
# ⚠ 這個值最終是 exec 的第一個參數。端點若收下白名單以外的字串，等於把 argv[0] 交給
#   呼叫端——所以「不合法就 400」是安全性質，不是輸入驗證的禮貌。
cpref = app.test_client()
cpref.post("/api/auth/login", json={"username": "alice", "password": "alice-newer-pw-1"})
r = cpref.get("/api/prefs")
check("預設是 C 版（沒設過）", r.status_code == 200 and r.get_json()["ttyd_bin"] == "ttyd")
check(
    "選項由後端給（前端不複製一份白名單）", {c["value"] for c in r.get_json()["ttyd_choices"]} == set(config.TTYD_BINS)
)
r = cpref.patch("/api/prefs", json={"ttyd_bin": "ttyd-rust"})
check("切成 Rust 版 → 200", r.status_code == 200 and r.get_json()["ttyd_bin"] == "ttyd-rust")
check("重新讀回來還在（存進 DB 不是只存在記憶體）", cpref.get("/api/prefs").get_json()["ttyd_bin"] == "ttyd-rust")
check(
    "/api/auth/me 也帶著它（前端開終端前不必再問一次）",
    cpref.get("/api/auth/me").get_json()["user"]["ttyd_bin"] == "ttyd-rust",
)
for bad in ("/bin/sh", "ttyd; rm -rf /", "", None, 1, ["ttyd"]):
    r = cpref.patch("/api/prefs", json={"ttyd_bin": bad})
    check(f"不合法的值 {bad!r} → 400", r.status_code == 400)
check(
    "未知欄位 → 400（不默默忽略，「設了沒生效」最難查）",
    cpref.patch("/api/prefs", json={"ttyd_bin": "ttyd", "x": 1}).status_code == 400,
)
check(
    "壞掉的 body → 400 而不是 500",
    cpref.patch("/api/prefs", data="not json", content_type="application/json").status_code == 400,
)
check("擋下之後值真的沒被改掉", cpref.get("/api/prefs").get_json()["ttyd_bin"] == "ttyd-rust")
r = app.test_client().patch("/api/prefs", json={"ttyd_bin": "ttyd"})
check("未登入 → 401", r.status_code == 401)

print("== 收終端：view 記得是誰開的（不是 session 的擁有者）==")
# 為什麼要有這一段：admin 開得了別人的 session，而 view 以前只掛在 session 上。
# 於是「改掉一位 admin 的密碼」收不掉他正開在別人 session 上的終端——他手上那個分頁
# 仍然是一個能打字的 shell。actor_user_id 補的就是這一格。
from server import views as _views  # noqa: E402
from server.db import session_scope as _scope  # noqa: E402
from server.models import Session as _Sess  # noqa: E402
from server.models import View as _View  # noqa: E402

_boss = auth.create_user("revoke-admin", "revoke-admin-pw-1", is_admin=True)
_victim = auth.create_user("revoke-owner", "revoke-owner-pw-1")
with _scope() as _s:
    _s.add(_Sess(id="rv-sess", container_name="c-rv", user_id=_victim["id"], status="running"))
    # admin 開在**別人** session 上的 view
    _s.add(_View(session_id="rv-sess", port=61001, pid=None, actor_user_id=_boss["id"]))

_closed, _failed = _views.close_user_views(_boss["id"])
with _scope() as _s:
    _left = _s.query(_View).filter(_View.session_id == "rv-sess").count()
check("改 admin 的密碼會收掉他開在別人 session 上的終端", _left == 0)
check("回傳是 (收掉幾個, 失敗幾場)", isinstance(_closed, int) and _failed == 0)

print("== 收終端：actor 未知（舊資料）對 admin 採保守處置 ==")
with _scope() as _s:
    _s.add(_View(session_id="rv-sess", port=61002, pid=None, actor_user_id=None))
_c2, _f2 = _views.close_user_views(_boss["id"])
with _scope() as _s:
    _left2 = _s.query(_View).filter(_View.session_id == "rv-sess").count()
check("actor 是 NULL 的舊列，改 admin 密碼時照樣收（寧可多收）", _left2 == 0)

# ⚠ 這一條是獨立審查抓到的：actor 只記得**建立**那一列的人，而 open_view 對已經活著的
#   view 是直接沿用、不改 actor。所以「擁有者先開、admin 後看同一場」時 actor 是擁有者，
#   只比對 actor 的話收不到 admin 正在看的那個畫面。
with _scope() as _s:
    _s.add(_View(session_id="rv-sess", port=61010, pid=None, actor_user_id=_victim["id"]))
_views.close_user_views(_boss["id"])
with _scope() as _s:
    _left_seq = _s.query(_View).filter(_View.session_id == "rv-sess").count()
check("擁有者先開、admin 後看：改 admin 密碼照樣收得掉（actor 停在擁有者身上）", _left_seq == 0)

with _scope() as _s:
    _s.add(_View(session_id="rv-sess", port=61003, pid=None, actor_user_id=None))
# 「不誤收」要換一個他完全沒有份的 session 才驗得到（rv-sess 的擁有者就是他本人，
# 那一列本來就會被第 1 條規則收掉，證明不了什麼）。
with _scope() as _s:
    _s.add(_Sess(id="rv-other", container_name="c-rv2", user_id=_boss["id"], status="running"))
    _s.add(_View(session_id="rv-other", port=61004, pid=None, actor_user_id=None))
_views.close_user_views(_victim["id"])
with _scope() as _s:
    _stay = _s.query(_View).filter(_View.session_id == "rv-other").count()
check("非 admin 改密碼不會去收別人 session 上 actor 未知的終端", _stay == 1)

print("== kill 送不出去時，那一列不可以被刪掉 ==")
# 獨立審查抓到的（F-5）：_kill 以前吞掉 PermissionError，而 close_views 照樣 delete 那一列
# 並計入「已收」。結果是程序還活著、DB 記錄沒了，之後再也沒有人會去收它。
_stuck_user = auth.create_user("kill-stuck", "kill-stuck-pw-1")
with _scope() as _s:
    _s.add(_Sess(id="rv-stuck", container_name="c-stuck", user_id=_stuck_user["id"], status="running"))
    _s.add(_View(session_id="rv-stuck", port=61020, pid=999001, actor_user_id=_stuck_user["id"]))
_orig_kill = _views._kill
_views._kill = lambda pid: False  # 模擬 SIGTERM 送不出去
try:
    _c, _f = _views.close_user_views(_stuck_user["id"])
    with _scope() as _s:
        _still = _s.query(_View).filter(_View.session_id == "rv-stuck").count()
    check("kill 失敗時那一列留著（不會變成沒人追的孤兒）", _still == 1)
    check("而且算成失敗，不是「已收」", _f == 1 and _c == 0)
finally:
    _views._kill = _orig_kill
with _scope() as _s:
    _s.query(_View).filter(_View.session_id == "rv-stuck").delete()

print("== 收終端失敗：不可以回 204、也不可以只印成功 ==")
_boom = auth.create_user("revoke-boom", "revoke-boom-pw-1")
_orig_close = _views.close_views


def _explode(_sid):
    raise RuntimeError("kill 失敗（注入）")


with _scope() as _s:
    _s.add(_Sess(id="rv-boom", container_name="c-boom", user_id=_boom["id"], status="running"))
    _s.add(_View(session_id="rv-boom", port=61005, pid=None, actor_user_id=_boom["id"]))
_views.close_views = _explode
try:
    _r = auth.change_password(_boom["id"], "revoke-boom-pw-2", require_old=False)
    check("收終端炸掉時，改密碼本身仍然成功（密碼回不去了）", _r["password_version"] >= 1)
    check("但失敗有被數出來，不是靜靜吞掉", _r["views_failed"] == 1)
    check("收掉的數量誠實回報", _r["views_closed"] == 0)
    _ca = app.test_client()
    _ca.post("/api/auth/login", json={"username": "admin", "password": "admin-password-1"})
    _resp = _ca.post(f"/api/users/{_boom['id']}/password", json={"new_password": "revoke-boom-pw-3"})
    check("管理員改密碼的 API 不回 204（那是假成功）", _resp.status_code != 204)
    check("而是回 200 加上實情", _resp.status_code == 200 and _resp.get_json()["views_failed"] == 1)
    check("回應講得出密碼確實改掉了", _resp.get_json()["password_changed"] is True)
    check("訊息講得出是幾場沒收掉", "1 場" in _resp.get_json()["warning"])

    # 另一種失敗：連「有幾場要收」都查不到（close_user_views 整個拋出來）。
    # 這比「N 場收不掉」更糟，訊息不可以用同一句話帶過。
    def _explode_all(_uid):
        raise RuntimeError("查不動（注入）")

    _orig_cuv = _views.close_user_views
    _views.close_user_views = _explode_all
    try:
        _r2 = auth.change_password(_boom["id"], "revoke-boom-pw-4", require_old=False)
        check("整個查不動時 views_failed 是 -1（不是 0，也不是某個場數）", _r2["views_failed"] == -1)
        check("帶得出原因", "查不動" in _r2.get("views_error", ""))
        _resp2 = _ca.post(f"/api/users/{_boom['id']}/password", json={"new_password": "revoke-boom-pw-5"})
        check("API 仍然不回 204", _resp2.status_code == 200)
        _w = _resp2.get_json()["warning"]
        check("訊息跟「N 場收不掉」不一樣（講出連幾場都不知道）", "都沒查到" in _w)
    finally:
        _views.close_user_views = _orig_cuv
finally:
    _views.close_views = _orig_close

print("== 清理 ==")
db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
check("暫存 DB 已清除", not os.path.exists(_tmp))

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
