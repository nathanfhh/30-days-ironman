"""管理畫面單元測試（ADR 0008 階段 6）。Flask test client，不需 docker。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        python tests/test_web.py
"""

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="claude-pty-web-")
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


auth.create_user("admin", "admin-password-1", is_admin=True)
auth.create_user("alice", "alice-password-1")
c = app.test_client()

print("== 未登入：頁面導向 /login，API 回 401（同一 gate、兩種呈現）==")
for path in ("/", "/account"):
    r = c.get(path)
    check(f"未登入 GET {path} → 302 至 /login", r.status_code == 302 and "/login" in r.headers.get("Location", ""))
r = c.get("/api/sessions")
check("未登入 GET /api/sessions → 401 JSON", r.status_code == 401)

print("== 公開資源不需登入 ==")
check("登入頁可讀", c.get("/login").status_code == 200)
check("healthz 可讀", c.get("/healthz").status_code == 200)
check("靜態 CSS 可讀", c.get("/static/css/app.css").status_code == 200)

print("== 已登入訪問 /login → 導向 /（與未登入導向 /login 對稱）==")
_c2 = app.test_client()
_c2.post("/api/auth/login", json={"username": "alice", "password": "alice-password-1"})
_r = _c2.get("/login")
check("已登入訪問 /login → 302 至 /", _r.status_code == 302 and _r.headers.get("Location", "").rstrip("/").endswith(""))
check("未登入訪問 /login → 200（登入頁本身公開）", app.test_client().get("/login").status_code == 200)

print("== 登入後三個頁面都吐得出 SPA 的殼 ==")
#
# ⚠ 這一節整個換了問法（2026-08-26 拆 legacy）。原本是抓 HTML 裡的字：登入者名稱在不在、
#   「新增使用者」對非管理員在不在、`data-behind-proxy` 是 0 還是 1。三個頁面現在吐的都是
#   **同一份 SPA 殼**，那些字一個都不在裡面，資料是 SPA 自己去 API 拿的。
#
#   所以那幾條的性質各自搬到現在的所有者，這裡只留伺服端還答得出來的部分：
#     · 三條路由通、而且吐的是殼（不是 404，也不是舊模板）→ 就在下面。
#     · 「誰是誰」「admin 區塊畫不畫」「behind_proxy 是多少」→ 值由 `/api/auth/me` 與
#       `/api/bootstrap` 出，`test_bootstrap.py` 逐欄驗；**畫面照著畫**由 golden 的
#       aria／DOM 快照與前端的 vitest 守。
#     · 「後端擋不擋得住非管理員」→ `test_admin_endpoint_gate.py`（前端 gate 只是禮貌）。
c.post("/api/auth/login", json={"username": "alice", "password": "alice-password-1"})
_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "static", "dist")
_shell = open(os.path.join(_dist, "index.html"), encoding="utf-8").read()
for _path in ("/", "/account"):
    _r = c.get(_path)
    check(f"{_path} 200", _r.status_code == 200)
    check(f"{_path} 吐的就是那份 SPA 殼（不是舊模板、也不是 404 頁）", _r.get_data(as_text=True) == _shell)
check("/login 未登入也吐同一份殼", app.test_client().get("/login").get_data(as_text=True) == _shell)
# ⚠ 殼**不可以被快取**：改版之後它指的是已經不存在的 /assets/*.js，那是一片白畫面、
#   沒有任何線索（見 web._spa_shell 的說明）。
check("殼是 no-store", c.get("/").headers.get("Cache-Control") == "no-store")

ca = app.test_client()
ca.post("/api/auth/login", json={"username": "admin", "password": "admin-password-1"})

print("== 主題 JSON：語意鍵名齊全、與 CSS 變數對得上 ==")
static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "static")
css = open(os.path.join(static_dir, "css", "app.css")).read()
css_vars = set(re.findall(r"--color-([a-z-]+):", css))
check("CSS 定義了語意色變數", len(css_vars) >= 10)
check(
    "CSS 未用色相命名的變數（如 --color-blue-500）",
    not any(re.match(r"(blue|red|green|purple|gray|grey)-?\d", v) for v in css_vars),
)

theme_dir = os.path.join(static_dir, "themes")
themes = [f for f in os.listdir(theme_dir) if f.endswith(".json")]
check(f"至少兩個可切換主題（found {len(themes)}）", len(themes) >= 2)
for name in themes:
    with open(os.path.join(theme_dir, name)) as f:
        theme = json.load(f)
    keys = set(theme.get("colors", {}))
    check(f"{name}：是合法 JSON 且有 colors", bool(keys))
    unknown = keys - css_vars
    check(f"{name}：所有鍵都對應到 CSS 變數（多餘鍵 {unknown or '無'}）", not unknown)
    for essential in ("surface", "text-primary", "accent", "signal-running"):
        check(f"{name}：含必要語意鍵 {essential}", essential in keys)
    # 反向檢查：CSS 定義的每個語意色，主題都必須覆寫。少一個的症狀很隱晦——換到亮色
    # 主題時那個顏色會靜靜沿用深色的預設值，通常要等到有人回報「這裡看不清楚」才發現。
    root_vars = set(re.findall(r"--color-([a-z-]+):", re.search(r":root\s*\{(.*?)\n\}", css, re.DOTALL).group(1)))
    check(
        f"{name}：覆寫了 CSS 定義的全部 {len(root_vars)} 個語意色（缺 {sorted(root_vars - keys) or '無'}）",
        not (root_vars - keys),
    )

# 顏色一律走語意變數：:root 之外出現寫死的 hex/rgb 就是繞過主題系統，那些地方換主題
# 時不會變色。data: URI 內的（SVG 紋理）不算。
#
# ⚠ **先把註解整段剝掉再掃，不要逐行猜。** 原本是判斷「這一行開頭是不是 `/*` 或 `*`」，
#   而這個 repo 的註解續行用 `·` 與縮排對齊，開頭兩者都不是——於是**註解裡舉例的 hex
#   會被當成規則裡的硬編顏色**。2026-07-30 真的踩到：一段解釋「accent 與 signal-warn 在
#   預設主題下幾乎同色」的註解（寫的正是「所以不要用顏色做那個區別」）把這條打紅了。
#   同一類坑這個 repo 已經踩三次（nginx conf 的 `Authorization`、firewall 的
#   `--uid-owner`、這一次），三次都出自「逐行猜哪裡是註解」。剝掉是精確的，猜不是。
_body_css = css[css.index("\n}", css.index(":root")) + 2 :]
_body_css = re.sub(r"/\*.*?\*/", "", _body_css, flags=re.DOTALL)
_hard = [
    m.group(0)
    for line in _body_css.splitlines()
    if "url(" not in line
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)", line)
]
check(f"CSS 規則內無硬編顏色（found {_hard or '無'}）", not _hard)

print("== 靜態資源可讀 ==")
for path in ("/static/themes/daylight.json",):
    check(f"{path} 可讀", c.get(path).status_code == 200)

print("== session 列表分頁（limit/offset/total 契約）==")
# 不需 docker：塞 creating 狀態的登錄（grace 期內 list() 不會把「container 還沒出現」
# 的列當成孤兒刪掉），並把 docker client 換成回空清單的替身。
from server.app import manager  # noqa: E402
from server.models import STATUS_CREATING  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402
from server.models import User as UserRow  # noqa: E402


class _NoContainers:
    class containers:
        @staticmethod
        def list(**_kw):
            return []


manager._docker = _NoContainers()
with db.session_scope() as s:
    alice_id = s.query(UserRow).filter_by(username="alice").one().id
    for n in range(25):
        s.add(
            SessionRow(
                id=f"page{n:04d}",
                container_name=f"claude-pty-page{n:04d}",
                user_id=alice_id,
                status=STATUS_CREATING,
                workdir="/w",
            )
        )

r = c.get("/api/sessions")
d = r.get_json()
check("預設帶分頁欄位 total/limit/offset", {"sessions", "total", "limit", "offset"} <= set(d))
check(f"預設不回全部（limit={d['limit']}，回 {len(d['sessions'])} 筆）", len(d["sessions"]) == d["limit"] < 25)
check(f"total 是全部筆數（got {d['total']}）", d["total"] == 25)
first_page_ids = [x["id"] for x in d["sessions"]]

d2 = c.get("/api/sessions?limit=5&offset=20").get_json()
check("limit/offset 生效", len(d2["sessions"]) == 5 and d2["offset"] == 20)
check("翻頁後不重複第一頁的內容", not set(x["id"] for x in d2["sessions"]) & set(first_page_ids))

d3 = c.get("/api/sessions?limit=5&offset=100").get_json()
check("offset 超出範圍回空清單但 total 仍正確", d3["sessions"] == [] and d3["total"] == 25)

for bad in ("limit=0", "limit=abc", "limit=101", "offset=-1"):
    check(f"非法分頁參數 {bad} → 400", c.get(f"/api/sessions?{bad}").status_code == 400)

print("== 歷史紀錄端點（ADR 0010）==")
from server.sessions import archive  # noqa: E402

check("歸檔 3 筆", archive([f"page{n:04d}" for n in range(3)], "terminated") == 3)
h = c.get("/api/sessions/history").get_json()
check("/api/sessions/history 不被當成 sid（路由順序）", "sessions" in h)
check(f"回傳已結束的 3 筆（got {h['total']}）", h["total"] == 3)
check("帶得出結束時間與原因", all(x["ended_at"] and x["ended_reason"] == "terminated" for x in h["sessions"]))
check("歷史支援分頁", len(c.get("/api/sessions/history?limit=2").get_json()["sessions"]) == 2)
check("進行中的列表已不含歸檔的那幾筆", c.get("/api/sessions").get_json()["total"] == 22)

# 收尾也走 archive：直接 s.delete(SessionRow) 會示範一個「繞過唯一出口」的寫法，
# 而那正是這批改動要杜絕的（review N3）
archive([f"page{n:04d}" for n in range(3, 25)], "gone")

print("== 退場（admin 改掉他的密碼）不動他的 session（ADR 0010）==")
# 刪除會沿 FK cascade 掉 sessions 登錄——容器還在跑卻沒人追蹤，歷史也沒經過 archive
# 就消失。退場走「改密碼」沒有這個問題：存取被切斷，工作繼續，紀錄完整。
with db.session_scope() as s:
    s.add(
        SessionRow(
            id="keepalive1",
            container_name="claude-pty-keepalive1",
            user_id=alice_id,
            status=STATUS_CREATING,
            workdir="/w",
        )
    )
r = ca.post(f"/api/users/{alice_id}/password", json={"new_password": "alice-exited-pw-1"})
check("退場成功（admin 代改密碼 → 204）", r.status_code == 204)
with db.session_scope() as s:
    check("他的 session 登錄仍在（沒被 cascade 掉）", s.get(SessionRow, "keepalive1") is not None)
check("帳號仍在名冊上（留痕）", any(u["id"] == alice_id for u in ca.get("/api/users").get_json()["users"]))
archive(["keepalive1"], "gone")  # 收尾走唯一出口

print("== XSS：畫面不准有 v-html 或手寫的 innerHTML ==")
#
# ⚠ 這一整節在 2026-08-26 換了守法，因為**被守的東西換了**。
#
#   舊的守法是：逐行掃 `app.js` 與兩份模板，找「使用者可控的欄位進了 `${}` 卻沒經過
#   `esc()`」。那是必要的，因為 legacy 的每一列都是自己用樣板字串拼 HTML 再塞 innerHTML。
#
#   Vue 版沒有那一步：`{{ }}` 與 `v-bind` 一律經過框架逸出，**除非**有人寫 `v-html`
#   或自己去碰 `innerHTML`。所以現在要守的不是「有沒有逸出」，而是「**有沒有繞過逸出**」。
#   這是更好守的形狀：舊的要維護一份會漂的 TAINTED 清單（漏列一個欄位就是無聲的洞），
#   新的只有兩個出口，而且兩個都不該出現。
#
# ⚠ **oxlint 接不住這一條。** `frontend/.oxlintrc.json` 只開了 typescript／unicorn／oxc
#   三個 plugin，沒有 vue plugin，`v-html` 對它是一個普通屬性。所以這道守衛留在這裡。
# ⚠ 判準是**用法**不是**出現過這個字**：`v-html` 要跟著 `=`，`innerHTML` 要跟著賦值。
#   前端原始碼裡有好幾處註解在講「舊版怎麼用 innerHTML」「這裡刻意不走 v-html」，
#   抓字串的話它們全都會誤報，而會誤報的守衛最後只會被關掉。
# ⚠ `__tests__/` 排除在外：vitest 用 `document.body.innerHTML = ""` 清場，那是測試的
#   收尾動作，不是畫面在拼 HTML。
_FRONT = os.path.join(os.path.dirname(os.path.dirname(static_dir)), "frontend", "src")
_BYPASS = re.compile(r"v-html\s*=|\.(inner|outer)HTML\s*=|insertAdjacentHTML\s*\(")
_hits = []
for _root, _dirs, _files in os.walk(_FRONT):
    if "__tests__" in _root.split(os.sep):
        continue
    for _name in _files:
        if not _name.endswith((".vue", ".ts")):
            continue
        _fp = os.path.join(_root, _name)
        for _i, _line in enumerate(open(_fp, encoding="utf-8").read().splitlines(), 1):
            if _BYPASS.search(_line):
                _hits.append(f"{os.path.relpath(_fp, _FRONT)}:{_i}")
check(f"前端沒有任何 v-html／innerHTML 的寫入（找到 {_hits or '無'}）", not _hits)
# 反向：這條 regex 真的抓得到嗎。抓不到的話上面那條是恆真的。
check(
    "🔴 而且它真的抓得到（對三種寫法都要命中）",
    all(_BYPASS.search(x) for x in ('<p v-html="s"/>', "el.innerHTML = s;", "el.insertAdjacentHTML('beforeend', s)")),
)
check(
    "🟡 而且不對註解誤報（講到 v-html 與 innerHTML 的那些說明不算）",
    not _BYPASS.search("// 拆成片段用 v-for 畫，不走 v-html：舊版是 esc(text) 之後才 replace"),
)

print("== 終端抽屜只在 nginx 後面開，且只吃同源路徑 ==")
# 抽屜的 iframe 若指向 POST /view 回的 direct_url（127.0.0.1:41xxx），會被本站 CSP 的
# `default-src 'self'` 直接擋掉——一片空白、只有 console 有錯。這條就是把那個理由機械化。
# ⚠ e2e_flow.py 是直接跑 Flask、沒有 nginx，behindProxy() 為 false，走的是 window.open
#   那條**備援**路徑；抽屜這條主要路徑在 e2e 裡零覆蓋，所以這裡至少用靜態檢查釘住。
# ⚠ 來源從 `sessions.html` 換成 `SessionsView.vue`（2026-08-26 拆 legacy）。守的性質一模一樣，
#   只是開抽屜那段程式碼搬家了。
_view = os.path.join(os.path.dirname(os.path.dirname(static_dir)), "frontend", "src", "views", "SessionsView.vue")
if check("找得到 SessionsView.vue", os.path.isfile(_view)):
    _src = open(_view, encoding="utf-8").read()
    check("抽屜只在走 nginx 時開", "store.meta.behindProxy && !wantsTab" in _src)
    check(
        "抽屜拿的是同源的 view.path，不是跨 origin 的 direct_url",
        re.search(r"drawer[^\n]*\bview\.path\b", _src) is not None
        or re.search(r"path:\s*view\.path", _src) is not None,
    )
    # Vue 版用的是 `globalThis.open`（oxlint 的 unicorn 規則要求），語意與 window.open 相同。
    check(
        "直連模式仍退回開新分頁（那個模式沒有同源路徑可用）",
        re.search(r"\b(window|globalThis)\.open\(", _src) is not None,
    )

print("== 頁尾：線上跑的是哪一版（review 2026-07-27）==")
from server import version as _version_mod  # noqa: E402

# ⚠ 這一節原本逐頁抓渲染出來的 HTML（`/login`、`/`、`/account` 各一次），驗「三頁都有頁尾」。
#   2026-08-26 之後三頁吐的都是同一份 SPA 殼，HTML 裡什麼都沒有，那個驗法失去對象。
#   性質分成兩半，各自搬到現在的所有者：
#     · **值**（有哪幾個模組、版本、commit）→ 由 `/api/account/bootstrap` 出，就在下面驗。
#     · **每一頁都畫得出來** → 由 golden 的 aria 快照守著：十六個**登入後**的場景每一場
#       都有頁尾，涵蓋兩個管理頁與各種對話框開著的狀態。
#
# ⚠ **2026-08-26（裁示 L4）之後這一節用的是登入過的 client。** 版號搬進
#   `/api/account/bootstrap` 了，而且**登入頁的頁尾不再顯示版本**，所以 login-empty 與
#   login-error 兩場的 aria 裡沒有頁尾，那是規格，不是回歸。「公開那條一個字都不給」由
#   `tests/test_bootstrap.py` 的反向斷言守著。
# ⚠ 這裡開一支**新的** client 重新登入，不沿用上面的 `_c2`：中間有幾節動過密碼
#   （`password_version` 一跳，舊 cookie 當場作廢），沿用的話這裡拿到的是 401，而錯誤
#   訊息會長得像「bootstrap 少了 build 欄位」，完全指錯方向。
_footer_c = app.test_client()
auth.create_user("footerpeek", "footerpeek-password-1")
_footer_c.post("/api/auth/login", json={"username": "footerpeek", "password": "footerpeek-password-1"})
_boot = _footer_c.get("/api/account/bootstrap").get_json()
_mods = _boot["build"]["modules"]
check("account bootstrap 給得出頁尾要畫的東西（登入後才給）", bool(_mods))
check("列出 claude-pty 本體", any(m["name"] == "claude-pty" for m in _mods))
check(
    "列出兩顆 ttyd",
    {"ttyd（C）", "ttyd（Rust）"} <= {m["name"] for m in _mods},
)

# ⚠ 問不到就必須**說「未知」**，不可以印一個看起來合理的值。頁尾唯一的用途就是回答
#   「線上在跑哪一版」——空白會讓人去查，錯的值會讓人停止查。
_saved_env = (os.environ.get("CLAUDE_PTY_GIT_SHA"), os.environ.get("CLAUDE_PTY_BUILT_AT"))
try:
    os.environ["CLAUDE_PTY_GIT_SHA"] = "deadbee"
    os.environ["CLAUDE_PTY_BUILT_AT"] = "2026-07-27T12:00:00+08:00"
    _version_mod.summary.cache_clear()
    # 同上：改問 API。畫面把這兩個值印成什麼樣子是 Vue 的事（golden 的 aria 逐字守著）。
    _b = _footer_c.get("/api/account/bootstrap").get_json()["build"]
    _own_mod = {m["name"]: m for m in _b["modules"]}["claude-pty"]
    check("build arg 有給時，commit 出得來", _own_mod["commit"] == "deadbee")
    # ⚠ 這裡放的是**建置時間**不是 commit 時間：同一個 commit 可以在任何時候被重新打包，
    #   而要回答的是「線上這包是什麼時候做出來的」。給的是原始的 ISO 字串，格式化交給
    #   瀏覽器（伺服端時區是 UTC，排出來的時間不屬於任何人）。
    check("建置時間以原始 ISO 字串交出去，不在伺服端排版", _b["built_at"] == "2026-07-27T12:00:00+08:00")

    os.environ["CLAUDE_PTY_GIT_SHA"] = ""
    os.environ["CLAUDE_PTY_BUILT_AT"] = ""
    _version_mod.summary.cache_clear()
    _own = {m["name"]: m for m in _version_mod.summary()["modules"]}["claude-pty"]
    # 開發環境有 .git 時會退回問工作區（刻意的便利，容器裡兩個條件都不成立）。
    # 要守的是「值必須有來源」——不是 build arg 就是工作區，不可以是編出來的。
    import subprocess as _sp

    try:
        _head = _sp.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except (OSError, _sp.SubprocessError):
        _head = None
    check(
        f"沒有 build arg 時 commit 只能來自工作區或留空（得到 {_own['commit']!r}）",
        _own["commit"] is None or (_head is not None and _own["commit"].startswith(_head)),
    )
finally:
    for k, v in zip(("CLAUDE_PTY_GIT_SHA", "CLAUDE_PTY_BUILT_AT"), _saved_env):
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    _version_mod.summary.cache_clear()

# ⚠ summary() 會跑 subprocess（每顆 ttyd 一次 `--version`）。**不可以每次 render 重算**
#   ——那是每次換頁多開幾個行程。lru_cache 就是那道保證，改掉它要有意識。
check("summary() 有快取（不會每次 render 重跑 subprocess）", hasattr(_version_mod.summary, "cache_clear"))
_version_mod.summary()
_hits_before = _version_mod.summary.cache_info().hits
_version_mod.summary()
check("第二次呼叫吃的是快取", _version_mod.summary.cache_info().hits == _hits_before + 1)

print("== 清理 ==")
db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
check("暫存 DB 已清除", not os.path.exists(_tmp))

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
