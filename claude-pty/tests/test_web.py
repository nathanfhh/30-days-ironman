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

print("== 登入後可讀頁面 ==")
c.post("/api/auth/login", json={"username": "alice", "password": "alice-password-1"})
r = c.get("/")
html = r.get_data(as_text=True)
check("sessions 頁 200", r.status_code == 200)
check("頁面帶出登入者名稱", "alice" in html)
check("非管理員看不到「新增使用者」區塊", "新增使用者" not in c.get("/account").get_data(as_text=True))

print("== 管理員才看得到帳號管理區塊 ==")
ca = app.test_client()
ca.post("/api/auth/login", json={"username": "admin", "password": "admin-password-1"})
admin_html = ca.get("/account").get_data(as_text=True)
check("管理員看得到「新增使用者」", "新增使用者" in admin_html)
check("管理員看得到「帳號清單」", "帳號清單" in admin_html)

print("== behind_proxy 旗標正確傳到頁面（決定終端用哪種 URL）==")
_orig = config.BEHIND_PROXY
config.BEHIND_PROXY = False
check('未在 proxy 後 → data-behind-proxy="0"', 'data-behind-proxy="0"' in c.get("/").get_data(as_text=True))
config.BEHIND_PROXY = True
check('在 proxy 後 → data-behind-proxy="1"', 'data-behind-proxy="1"' in c.get("/").get_data(as_text=True))
config.BEHIND_PROXY = _orig

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
for path in ("/static/js/app.js", "/static/themes/daylight.json"):
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

print("== XSS：使用者可控的欄位進 innerHTML 前一律逸出 ==")
# 精準檢查「實際由使用者控制」的欄位，而非對所有插值做啟發式判斷——後者會把 relTime()、
# 已在內部逸出的 chips()、純數值 String(i+1) 全誤報，而會誤報的測試最終只會被忽略。
js = open(os.path.join(static_dir, "js", "app.js")).read()
check("app.js 提供 esc() 逸出工具", "function esc(" in js)
# 這些值最終都源自使用者輸入或外部資料：session id/workdir/state（建立時可帶）、
# profile 值（API 接受任意字串，非只有下拉選單那幾個）、使用者名稱、後端錯誤訊息。
TAINTED = [
    "s.id",
    "s.workdir",
    "s.state",
    "s.owner",
    "s.display_name",
    "s.container",
    "u.username",
    "ex.message",
    "e.message",
]
# ⚠ 這裡刻意**沒有** profile 的值（`p.cli` 等）。它們在 chips() 裡是先被組進一個陣列、
#   三行後才寫進模板字串的，而這份檢查是逐行看「這一行有沒有 esc」——加進來只會對
#   `out.push([p.cli, ...])` 那種純資料組裝誤報。profile 值的逸出改由下方針對 chips()
#   的專門檢查負責（連 data-tone 那個屬性位置一起釘住）。
# 只走 innerHTML 這個 sink 才需要逸出；toast()/flash()/confirm()/prompt()/alert() 都是純文字
# （toast 與 flash 都以 textContent 寫入訊息），對它們逸出反而會讓畫面出現 &amp; 之類的雜訊。
# ⚠ 新增任何「顯示訊息」的函式時務必同步這份清單，否則會得到一堆誤報——而會誤報的測試
#   最後只會被當成雜訊忽略，那比沒有測試更糟。
TEXT_SINKS = ("toast(", "flash(", "confirm(", "prompt(", "alert(", "textContent")
for tpl in ("sessions.html", "account.html"):
    body = open(os.path.join(os.path.dirname(static_dir), "templates", tpl)).read()
    for field in TAINTED:
        if field not in body:
            continue
        bad = []
        for line in body.splitlines():
            if field not in line or any(sink in line for sink in TEXT_SINKS):
                continue
            bad += [m for m in re.findall(r"\$\{([^}]*" + re.escape(field) + r"[^}]*)\}", line) if "esc(" not in m]
        check(f"{tpl}：{field} 進 innerHTML 前全數逸出（未逸出處 {bad or '無'}）", not bad)
# TEXT_SINKS 把 toast() 當成安全 sink——那個前提本身必須被驗證。若哪天 toast 改用
# innerHTML 寫訊息，白名單會讓上面所有檢查對它視而不見，等於直接變成一個漏洞。
toast_body = re.search(r"function toast\(.*?\n\}", js, re.DOTALL)
check("toast() 存在", bool(toast_body))


def html_interpolations(fn_src: str, var: str) -> list[str]:
    r"""fn_src 的 innerHTML 賦值裡，**插值**（`${...}`）中出現 var 的那些。

    ⚠ 只認插值，不認「這段字串裡有沒有出現這幾個字」。原本寫的是
      `innerHTML\s*[+]?=\s*[^;]*\btitle\b`，它會把樣板裡任何含 title 的**字面量**
      一起判成漏洞：`data-testid="toast-title"` 就這樣紅過一次（`-` 不是 word
      character，所以 `\btitle\b` 照樣命中）。
    ⚠ 為什麼要修而不是把那顆 testid 改名：一條會對正確程式碼喊狼來了的守衛，下場是被
      繞過或刪掉，不是被修好。而繞過之後它還掛在那裡，看起來仍像有人在守。
    ⚠ `[^}]*` 對付不了巢狀樣板，這與同檔上面那圈模板掃描是同一個取捨（見 206-207 行的
      說明）：寧可對巢狀漏看，也不要製造誤報。
    """
    out = []
    for chunk in re.findall(r"innerHTML\s*[+]?=\s*([^;]*)", fn_src, re.DOTALL):
        out += [m for m in re.findall(r"\$\{[^}]*\}", chunk) if re.search(rf"\b{var}\b", m)]
    return out


if toast_body:
    src = toast_body.group(0)
    # 標題與內文都必須走 textContent。比對變數名而非固定字串：toast 的參數日後可能
    # 改名，但「使用者可控的字串只能經由 textContent 進 DOM」這條規則不會變。
    for var in ("title", "body"):
        check(
            f"toast() 以 textContent 寫入 {var}（TEXT_SINKS 白名單的前提）",
            re.search(rf"\.textContent = {var}\b", src) is not None,
        )
        leaked = html_interpolations(src, var)
        check(f"toast() 不把 {var} 塞進 innerHTML（插值處 {leaked or '無'}）", not leaked)

# 🔴 收緊之後這條守衛還抓不抓得到？拿兩段假 toast 餵同一支偵測器：一段真的漏、一段
#    只是字面量裡出現那個字。兩個方向都要對，否則「收緊」等於「關掉」。
_LEAKY = "function toast(title) {\n  el.innerHTML = `<div>${title}</div>`;\n}"
check(
    f"🔴 而且它真的抓得到（把 ${{title}} 插進 innerHTML 要命中：{html_interpolations(_LEAKY, 'title')}）",
    html_interpolations(_LEAKY, "title") == ["${title}"],
)
_LITERAL = 'function toast(title) {\n  el.innerHTML = `<div data-testid="toast-title"></div>`;\n}'
check(
    '🟡 而且不對字面量誤報（data-testid="toast-title" 不是插值）',
    html_interpolations(_LITERAL, "title") == [],
)

# chips() 是唯一把 profile 值寫進 HTML 的地方，確認它內部有逸出
sessions_tpl = open(os.path.join(os.path.dirname(static_dir), "templates", "sessions.html")).read()
chips_body = re.search(r"function chips\([^)]*\)\s*\{.*?\n  \}", sessions_tpl, re.S)
check("chips() 內部對 profile 值逸出", bool(chips_body) and "esc(t)" in chips_body.group(0))
# ⚠ 屬性位置也要看。`data-tone` 一度直接插值——它原本只放 "owner"/"accent" 兩個字面
#   常數，後來其中一個改成 `cli-${p.cli}`（API 收進來的值）卻沒補 esc，而上面那條
#   「內部有逸出」照樣通過，因為 esc(t) 還在（review 2026-07-25）。逐個插值點檢查。
tone_slots = re.findall(r'data-tone="\$\{([^}]*)\}"', chips_body.group(0) if chips_body else "")
check(
    f"chips() 的每個 data-tone 插值都經過 esc（{tone_slots}）",
    bool(tone_slots) and all("esc(" in slot for slot in tone_slots),
)

# 上面那圈 TAINTED 掃的是**模板**；app.js 自己組 innerHTML 的地方不在範圍內。
# ⚠ 這是一份**清單**，不是只釘住抽屜一個：這四個函式都把使用者可控的字串寫進樣板字串。
#   新增第五個 sink 時請一併加進來——只釘一個、註解卻寫得像補完了，比沒有更糟。
# 作法與上面模板那圈一致：**列出實際受汙染的變數名**，逐行看有沒有裸插值。
# 不改成「掃出每個 ${...} 都要有 esc」是刻意的——`${wide ? " modal__box--screen" : ""}`
# 這種純布林插值、以及巢狀樣板都會誤報，而誤報的測試最後只會被忽略（同 206-207 行）。
JS_SINKS = {
    # 下拉選單：選項的 value/label/hint。profile 的選項值來自 API，不是只有寫死那幾個。
    "renderMenu": ["o.value", "o.label", "o.hint"],
    # 送入面板與終端抽屜：label 是使用者自己取的 session 名稱
    "terminalDrawer": ["label", "sid"],
    # 對話框：標題、內文、預填值、placeholder 都由呼叫端給，內容含使用者資料
    "dialog": ["title", "body", "input.value", "input.placeholder"],
}
# 已經逸出、或根本不是把值寫進 HTML 的插值：
#   esc(                 — 這份程式的 HTML 逸出器
#   CSS.escape(          — 寫進 querySelector 的選擇器用的是**另一套**規則，不是 HTML
#   encodeURIComponent(  — 網址路徑片段，同理
#   `a === b`            — 布林比較的結果（如 aria-selected），不可能夾帶輸入
# ⚠ 三個編碼器各有各的用途，不可互換：把 esc() 用在網址上、或 encodeURIComponent 用在
#   HTML 上，都是「看起來有逸出、實際上編錯」。這裡只認「有用其中一個」，用對地方是
#   人的責任——所以新增 sink 時請看一眼那個插值到底進了哪裡。
_JS_SAFE = re.compile(r"\besc\(|\bCSS\.escape\(|\bencodeURIComponent\(|^[\w.]+\s*[=!]==?\s*[\w.]+$")
for fn, fields in JS_SINKS.items():
    body_m = re.search(rf"function {fn}\(.*?\n\}}", js, re.DOTALL)
    if not check(f"{fn}() 找得到（sink 清單的前提）", bool(body_m)):
        continue
    src = body_m.group(0)
    for field in fields:
        bad = [m for m in re.findall(r"\$\{([^}]*" + re.escape(field) + r"[^}]*)\}", src) if not _JS_SAFE.search(m)]
        check(f"{fn}()：{field} 進 HTML 前逸出（未逸出處 {bad or '無'}）", not bad)

print("== 終端抽屜只在 nginx 後面開，且只吃同源路徑 ==")
# 抽屜的 iframe 若指向 POST /view 回的 direct_url（127.0.0.1:41xxx），會被本站 CSP 的
# `default-src 'self'` 直接擋掉——一片空白、只有 console 有錯。這條就是把那個理由機械化。
# ⚠ e2e_flow.py 是直接跑 Flask、沒有 nginx，behindProxy() 為 false，走的是 window.open
#   那條**備援**路徑；抽屜這條主要路徑在 e2e 裡零覆蓋，所以這裡至少用靜態檢查釘住。
_open_branch = re.search(r'act === "open"\)\s*\{(.*?)\n      \} else if', sessions_tpl, re.DOTALL)
if check("找得到「開啟終端」那段分支", bool(_open_branch)):
    _src = _open_branch.group(1)
    check("抽屜只在 behindProxy() 時開", "behindProxy() && !wantsTab" in _src)
    check(
        "抽屜拿的是同源的 view.path，不是跨 origin 的 direct_url",
        re.search(r"terminalDrawer\(\{[^}]*path:\s*view\.path", _src) is not None
        and not re.search(r"terminalDrawer\(\{[^}]*direct_url", _src),
    )
    check("直連模式仍退回開新分頁（那個模式沒有同源路徑可用）", "window.open(" in _src)

print("== 頁尾：線上跑的是哪一版（review 2026-07-27）==")
from server import version as _version_mod  # noqa: E402

# ⚠ **登入頁也要有。** 它不走 web._page()，所以任何「塞進 _page 參數」的做法都會讓那一頁
#   靜靜地少一塊——頁尾正是 build_info() 做成 template global 而不是參數的唯一理由。
_cf = app.test_client()  # 這一段自己開一個登入好的 client（`c` 在前面的 gate 測試裡被登出過）
_cf.post("/api/auth/login", json={"username": "admin", "password": "admin-password-1"})
for path, need_login in (("/login", False), ("/", True), ("/account", True)):
    cli = _cf if need_login else app.test_client()
    html = cli.get(path).get_data(as_text=True)
    check(f"{path} 有頁尾", 'data-testid="footer"' in html)
    check(f"{path} 列出 claude-pty 本體", "claude-pty" in html)
    check(f"{path} 列出兩顆 ttyd", "ttyd（C）" in html and "ttyd（Rust）" in html)

# ⚠ 問不到就必須**說「未知」**，不可以印一個看起來合理的值。頁尾唯一的用途就是回答
#   「線上在跑哪一版」——空白會讓人去查，錯的值會讓人停止查。
_saved_env = (os.environ.get("CLAUDE_PTY_GIT_SHA"), os.environ.get("CLAUDE_PTY_BUILT_AT"))
try:
    os.environ["CLAUDE_PTY_GIT_SHA"] = "deadbee"
    os.environ["CLAUDE_PTY_BUILT_AT"] = "2026-07-27T12:00:00+08:00"
    _version_mod.summary.cache_clear()
    html = _cf.get("/").get_data(as_text=True)
    check("build arg 有給時，commit 顯示出來", "deadbee" in html)
    # ⚠ 這裡放的是**建置時間**不是 commit 時間：同一個 commit 可以在任何時候被重新打包，
    #   而要回答的是「線上這包是什麼時候做出來的」。格式化交給瀏覽器（伺服端時區是 UTC）。
    check("建置時間以 datetime 屬性交給瀏覽器格式化", 'datetime="2026-07-27T12:00:00+08:00"' in html)
    check("頁尾寫明那是「建置於」而不是 commit 時刻", "建置於" in html)

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
