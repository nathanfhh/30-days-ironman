"""E2E：終端抽屜的尺寸同步（真瀏覽器）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with playwright python tests/e2e_drawer.py
（首次需 `uv run --with playwright playwright install chromium`）

**不需要 docker、也不需要 ttyd**。抽屜真正依賴 ttyd 的地方只有一件事：iframe 裡有一個
同源的 `window.term`，帶 cols/rows/options.fontSize/onResize。這裡用 `page.route` 在
`/session/<sid>/` 回一份極簡替身，於是「開抽屜 → 量尺寸 → POST /resize」整條路都能在
沒有容器的情況下走完，而**被驗的是父頁面自己的邏輯**——那才是出過錯的地方。

釘住的是同一個 bug 反覆出現的那幾個面向（使用者回報：「開第一個關掉再開一個還是有
大小錯誤」）：

  1. 開啟時**一定**帶 redraw——尺寸與上次相同時 docker 不會送 SIGWINCH，TUI 會沿用
     上一個版面。這是「關掉再開」會壞的直接原因，所以第二次開也要照樣帶。
  2. redraw 是**黏著的**：送出失敗（抽屜剛開時 session 可能還在 creating）不能把旗標
     清掉，否則重繪就永遠沒有第二次機會。
  3. 尺寸在**送出的當下**才讀，不是排程時就抓走——連按縮放時抓走的會是中途值。
  4. 抽屜關了就不再送。

⚠ 一律以 `data-testid` 取元素。
"""

import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="e2e-drawer-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "e2e-drawer-secret"
# 抽屜只在「走 nginx」的模式下開；直連時呼叫端會改開新分頁（見 sessions.html）。
config.BEHIND_PROXY = True

from playwright.sync_api import sync_playwright  # noqa: E402

from server import auth  # noqa: E402
from server.app import app  # noqa: E402
from server.db import init_db, reset_engine, session_scope  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402
from server.sessions import utcnow  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


reset_engine()
init_db()
admin = auth.create_user("e2e-admin", "e2e-password-1", is_admin=True)

now = utcnow()
PROFILE = {"cli": "claude", "network": "restricted", "capture": False, "telemetry": False}
with session_scope() as s:
    for i in (1, 2):
        s.add(
            SessionRow(
                id=f"e{i}",
                container_name=f"ec{i}",
                user_id=admin["id"],
                workdir="/w",
                profile=PROFILE,
                created_at=now,
                last_active_at=now,
            )
        )


class _FakeContainer:
    """list() 會去問 docker 對帳；回「兩個都還在」，否則兩列都會被歸檔。"""

    def __init__(self, name):
        self.name, self.status, self.id = name, "running", f"cid-{name}"

    def logs(self, **_kw):
        return b""


class _FakeDocker:
    class containers:
        @staticmethod
        def list(**_kw):
            return [_FakeContainer(f"ec{i}") for i in (1, 2)]


import server.app as app_mod  # noqa: E402

app_mod.manager._docker = _FakeDocker()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
# ⚠ 把 werkzeug 的**請求** log 關掉（保留 WARNING 以上）。兩個理由，第二個才是重點：
#   · 每一發請求印一行 `127.0.0.1 - - [...] "GET /api/sessions"`，而列表每 15 秒輪詢一次
#     ——真正的失敗訊息會被埋在裡面。
#   · 🔴 **它是 `ValueError: I/O operation on closed file.` 的來源**：Flask 跑在 daemon thread，
#     腳本 `sys.exit()` 時 Python 關掉 stdout/stderr，而那條 thread 可能還在寫最後一筆請求
#     log（關瀏覽器時常有 in-flight 的輪詢）。daemon thread 的未捕捉例外不影響 exit code，
#     所以測試結果是可信的——但那串紅字每次都要重新判斷一遍「這是不是真的壞了」。
# ⚠ 只降到 WARNING 不是 ERROR：werkzeug 真的有話要說時（例如 port 被佔）還是要看得到。
logging.getLogger("werkzeug").setLevel(logging.WARNING)
threading.Thread(
    target=lambda: app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False),
    daemon=True,
).start()
for _ in range(50):
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            break
    time.sleep(0.1)


# ── ttyd 的替身 ──────────────────────────────────────────────────────────────
# 只做父頁面真正會碰到的那幾件事。字寬/行高取等寬字的常見比例，讓「改字級」真的會改
# 變欄列數——測「送出的是哪一個尺寸」需要這個因果關係，寫死的數字驗不出東西。
#
# 畫布那一段模的是 xterm 真正的行為（2026-07-27 在真的 ttyd 上量過每一條）：
#   * backing store 只在**重新量字**時才依當下的 dpr 重建；
#   * 同值指派 fontSize 會被忽略，所以「先跳開再回來」是唯一叫得動它的方式；
#   * 改字級**不會**順便重新 fit（欄列數不變）——fit 是 window resize 才跑的。
# `__scale` 是「建立畫布時用的 dpr」，把它設成 dpr 的兩倍就重現了使用者遇到的畫面：
# 字被畫成一半大小，要手動按一下 +/- px 才會對。
STUB = """<!doctype html><meta charset="utf-8"><body style="margin:0;background:#111">
<div class="xterm"><div class="xterm-screen">
<canvas class="xterm-link-layer"></canvas><canvas></canvas></div></div>
<script>
const CB = [];
let font = 14;
window.__remeasures = 0;                 // 重新量字的次數（驗「沒事別亂動」）
window.term = {
  options: {
    get fontSize() { return font; },
    set fontSize(v) { if (v === font) return; font = v; remeasure(); },
  },
  cols: 0, rows: 0,
  onResize(cb) { CB.push(cb); },
};
function paint(scale) {
  document.querySelectorAll(".xterm canvas").forEach((c) => {
    c.style.width = window.innerWidth + "px";
    c.style.height = window.innerHeight + "px";
    c.width  = Math.round(window.innerWidth  * scale);
    c.height = Math.round(window.innerHeight * scale);
  });
}
function remeasure() { window.__remeasures++; paint(window.devicePixelRatio || 1); }
function fit() {
  const cols = Math.max(2, Math.floor(window.innerWidth  / (font * 0.6)));
  const rows = Math.max(1, Math.floor(window.innerHeight / (font * 1.2)));
  paint(window.devicePixelRatio || 1);   // fit 之後畫布一定是對的
  if (cols === window.term.cols && rows === window.term.rows) return;
  window.term.cols = cols; window.term.rows = rows;
  CB.forEach((cb) => cb({ cols, rows }));
}
window.addEventListener("resize", fit);
fit();
paint(__SCALE__);                        // 起始狀態：畫布與 CSS 尺寸脫節
__FONT_AFTER__                           // 見下方 stub_font_after 的說明（預設空字串）
</script>"""

posts = []  # 收到的每一發 /resize（依序）
post_at = []  # 每一發送到的牆鐘時刻（ms）。與瀏覽器的 Date.now() 是同一支時鐘，
# 所以「送出」與「動畫結束」這兩個時間點可以直接比大小（見最後那段時序測試）。
fail_next = 0  # 還要讓幾發失敗（驗「旗標黏著」）
stub_scale = "2"  # iframe 起始的畫布倍率；"window.devicePixelRatio" ＝一開始就是好的


view_flavor = "Rust"


def route_view(route, request):
    sid = request.url.rstrip("/").split("/")[-2]
    body = {"path": f"/session/{sid}/", "direct_url": "http://127.0.0.1:41999/"}
    # 這個 view 是哪一顆 ttyd 起的（真後端從 DB 給）。`view_flavor = None` 模擬舊記錄。
    if view_flavor is not None:
        body["ttyd_bin"] = "ttyd-rust" if view_flavor == "Rust" else "ttyd"
        body["ttyd_flavor"] = view_flavor
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


# ttyd 真正的開場順序：**先**用它自己的預設字級 fit，**之後**才把字級套成存下來的值，
# 而套字級不會順便重新 fit。結果是 `options.fontSize` 已經是新的、`cols/rows` 還是舊的。
# 把它模出來要直接指派模組變數 `font`（繞過 setter，setter 會 remeasure），這正是
# 「有一個字級沒經過我們、也沒觸發任何回呼」的形狀。預設空字串＝不模擬。
stub_font_after = ""


def route_session(route, _request):
    route.fulfill(
        status=200,
        content_type="text/html; charset=utf-8",
        body=STUB.replace("__SCALE__", stub_scale).replace("__FONT_AFTER__", stub_font_after),
    )


def route_resize(route, request):
    global fail_next
    posts.append(json.loads(request.post_data))
    post_at.append(time.time() * 1000)
    if fail_next > 0:
        fail_next -= 1
        route.fulfill(status=500, content_type="application/json", body='{"error":"session 還在 creating"}')
    else:
        route.fulfill(status=204, body="")


def term_size(page):
    """iframe 裡 xterm 當下的實際尺寸——比對用的基準真相。"""
    return page.evaluate(
        "() => { const t = document.querySelector('[data-testid=\"drawer-frame\"]')"
        ".contentWindow.term; return { cols: t.cols, rows: t.rows,"
        " font: t.options.fontSize }; }"
    )


def canvas_state(page):
    """iframe 裡每一張畫布的 backing store 有沒有跟 CSS 尺寸對上。

    ⚠ `.xterm canvas` 是 ttyd／xterm.js 自己的 DOM（上面那個假頁面照著它長），
      不是我們的模板，沒有地方可以在上面補 data-testid，所以這裡照原樣認 class。
    """
    return page.evaluate(
        "() => { const f = document.querySelector('[data-testid=\"drawer-frame\"]');"
        " const w = f.contentWindow, dpr = w.devicePixelRatio || 1;"
        " return { remeasures: w.__remeasures, dpr,"
        "   canvases: [...f.contentDocument.querySelectorAll('.xterm canvas')].map("
        "     (c) => ({ backing: c.width, css: parseFloat(c.style.width),"
        "               ok: Math.abs(c.width - parseFloat(c.style.width) * dpr) <= 1 })) }; }"
    )


def open_drawer(page, sid):
    before = len(posts)
    page.click(f'[data-testid="row-open-{sid}"]')
    page.wait_for_selector('[data-testid="drawer"]')
    # pending 收起來＝iframe 已載入且父頁面認得它，attachSizeSync 這時才會開始
    page.wait_for_selector('[data-testid="drawer-pending"]', state="hidden", timeout=8000)
    # ⚠ 不睡一個固定的秒數。送出的時機由「抽屜停定 ＋ iframe 盒子不再變」決定，不是一個
    #   常數；睡 700ms 在慢一點的機器上會剛好搶在它前面，而那種紅燈長得像功能壞了。
    #   等它真的到，沒到就讓後面的斷言去說話（這裡不拋）。
    for _ in range(80):
        if len(posts) > before:
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(250)  # 若還會有第二發，讓它也到齊再斷言


try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, timezone_id="Asia/Taipei")
        page = ctx.new_page()
        page.route("**/api/sessions/*/view", route_view)
        page.route("**/api/sessions/*/resize", route_resize)
        page.route("**/session/**", route_session)

        page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        page.fill('[data-testid="login-username"]', "e2e-admin")
        page.fill('[data-testid="login-password"]', "e2e-password-1")
        page.get_by_role("button", name="進入控制台").click()
        page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=8000)
        page.wait_for_timeout(600)

        print("== 第一次開啟：尺寸要送出去，而且帶 redraw ==")
        posts.clear()
        open_drawer(page, "e1")
        check("有送出 /resize（不是靜靜地什麼都沒做）", len(posts) >= 1)
        first = posts[0] if posts else {}
        check(f"帶 redraw=true（尺寸沒變時 SIGWINCH 不會發，TUI 會沿用舊版面）：{first}", first.get("redraw") is True)
        live = term_size(page)
        check(
            f"送的是 xterm 當下的尺寸 {live['cols']}×{live['rows']}，不是建立時的 140×40",
            first.get("cols") == live["cols"] and first.get("rows") == live["rows"],
        )
        check(
            "欄列數是真的量出來的（不是 0 或 80×24 這種預設值）",
            first.get("cols", 0) > 40 and first.get("rows", 0) > 10,
        )

        print("== 畫布與 CSS 尺寸脫節時要自己修好（不用手動按 +/- px）==")
        # 使用者回報：「我現在都是要刻意去觸發 +- px 那個大小才會是對的」。
        # 真因是畫布的 backing store 開在別的 dpr 上，xterm 依 1 倍去畫、畫進 2 倍的
        # 緩衝區，每個字只佔顯示上的一半。只有重新量字治得好——所以父頁面要自己做。
        cv = canvas_state(page)
        check(
            f"畫布已經修好（dpr={cv['dpr']}，{cv['canvases']}）",
            bool(cv["canvases"]) and all(c["ok"] for c in cv["canvases"]),
        )
        # 一次修復＝兩次重量字（跳開 +1、再還原）。多於 2 就是每次 fit 都在瞎修——
        # 修好之後 glyphScaleBroken() 應該就不成立了，不該有第三次。
        # ⚠ **0 次現在也是合格的**（2026-07-31）：開啟時一律會逼一次 fit（見 app.js 裡
        #   attachSizeSync 那個 else 分支的說明），而 fit 之後畫布本來就是對的——於是
        #   glyphScaleBroken() 多半根本不成立，那個字級跳開再還原的修復就不必跑。
        #   這條原本釘死 `== 2`，那是把「修好了」與「用哪一種手段修的」綁在一起；
        #   真正要守的是上面那條（畫布最後是對的）＋這裡的上限（沒有反覆瞎修）。
        check(f"沒有反覆瞎修（重量字 {cv['remeasures']} 次；0＝fit 就修好了、2＝跳一次字級）", cv["remeasures"] <= 2)
        check(f"沒有動到使用者的字級（{live['font']}px）", term_size(page)["font"] == live["font"])
        check(f"也沒有因此多送一發 /resize（收到 {len(posts)} 發）", len(posts) == 1)

        print("== 畫布本來就正常時不要瞎動 ==")
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")
        stub_scale = "window.devicePixelRatio"
        posts.clear()
        open_drawer(page, "e1")
        healthy = canvas_state(page)
        check(f"完全沒有重量字級（實際 {healthy['remeasures']} 次）", healthy["remeasures"] == 0)
        check("畫布仍然是對的", all(c["ok"] for c in healthy["canvases"]))
        stub_scale = "2"  # 抽屜留著開，下一段接手關它（與原本的流程一致）

        print("== 關掉再開一個：第二次照樣要帶 redraw（使用者回報的那個 bug）==")
        posts.clear()
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")
        page.wait_for_timeout(400)
        check("關閉之後不再送尺寸", len(posts) == 0)
        open_drawer(page, "e2")
        check("第二次開也有送", len(posts) >= 1)
        check(f"而且一樣帶 redraw=true：{posts[0] if posts else {}}", bool(posts) and posts[0].get("redraw") is True)
        check(
            "送去的是第二場 session",
            "/e2/" in page.frame_locator('[data-testid="drawer-frame"]').owner.get_attribute("src"),
        )

        print("== 連按縮放：只送最後一次，而且送的是最終尺寸 ==")
        posts.clear()
        before = term_size(page)
        # 同一個 tick 內按三下：debounce 一定會把它們併成一發，於是「尺寸在排程時就
        # 抓走」與「送出當下才讀」兩種寫法會給出不同的數字。
        page.evaluate("""() => {
          const b = document.querySelector('[data-testid="drawer-font-dec"]');
          b.click(); b.click(); b.click();
        }""")
        page.wait_for_timeout(700)
        after = term_size(page)
        check(f"字級真的連退三級 {before['font']} → {after['font']}", after["font"] == before["font"] - 3)
        check(f"併成一發送出（收到 {len(posts)} 發）", len(posts) == 1)
        check(
            f"送的是最終尺寸 {after['cols']}×{after['rows']}，不是中途值",
            bool(posts) and posts[0].get("cols") == after["cols"] and posts[0].get("rows") == after["rows"],
        )
        check("這一發不帶 redraw（本來就有真正的尺寸變化）", bool(posts) and posts[0].get("redraw") is False)

        print("== 字級記得住：關掉再開沿用上次調的 ==")
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")
        posts.clear()
        open_drawer(page, "e1")
        reopened = term_size(page)
        check(f"沿用上次的字級 {reopened['font']}px", reopened["font"] == after["font"])
        check("畫面上的數字對得上", page.inner_text('[data-testid="drawer-font-value"]') == f"{reopened['font']}px")
        check(
            f"送出的尺寸是**套用字級之後**的 {reopened['cols']}×{reopened['rows']}",
            bool(posts) and posts[-1].get("cols") == reopened["cols"] and posts[-1].get("rows") == reopened["rows"],
        )

        print("== 到界時把該側停用（按了沒反應等於壞掉）==")
        page.evaluate("""() => {
          const b = document.querySelector('[data-testid="drawer-font-dec"]');
          for (let i = 0; i < 40; i++) b.click();
        }""")
        page.wait_for_timeout(700)
        check("縮到下限時縮小鍵停用", page.locator('[data-testid="drawer-font-dec"]').is_disabled())
        check("此時放大鍵仍可用", page.locator('[data-testid="drawer-font-inc"]').is_enabled())
        check("字級停在 8px（不是繼續往下掉）", term_size(page)["font"] == 8)

        print("== 送出失敗時 redraw 旗標要黏著（抽屜剛開時 session 可能還在 creating）==")
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")
        posts.clear()
        fail_next = 1  # 開啟時的那一發打回 500
        open_drawer(page, "e2")
        check(f"第一發帶 redraw 但失敗了：{posts[0] if posts else {}}", bool(posts) and posts[0].get("redraw") is True)
        page.click('[data-testid="drawer-font-inc"]')
        page.wait_for_timeout(700)
        check(
            "失敗之後補送的那一發仍然帶 redraw=true（旗標沒有被提早清掉）",
            len(posts) >= 2 and posts[1].get("redraw") is True,
        )
        page.click('[data-testid="drawer-font-inc"]')
        page.wait_for_timeout(700)
        check("成功之後才清掉，後續不再重複要求重繪", len(posts) >= 3 and posts[2].get("redraw") is False)

        print("== 抽屜關了就別再送 ==")
        posts.clear()
        # 同一個 tick 內先改字級再關閉：一定落在 300ms 的 debounce 之內
        page.evaluate("""() => {
          document.querySelector('[data-testid="drawer-font-inc"]').click();
          document.querySelector('[data-testid="drawer-close"]').click();
        }""")
        page.wait_for_timeout(900)
        check(f"排程中的那一發被取消（收到 {len(posts)} 發）", len(posts) == 0)

        print("== 標題列要說出哪個目錄留得住 ==")
        # 容器一收，cwd 底下的東西就沒了，而終端裡沒有任何線索。這條提示是使用者唯一
        # 看得到的說明，所以它「在不在」與「路徑對不對」都要釘住。
        # ⚠ 比對的是 `config.DATA_BIND` 不是字面路徑：路徑改過一次（`/data` → 現在這個），
        #   寫死的話測試會變成「兩個地方都要記得改」，而不是守著同一個真相。
        open_drawer(page, "e1")  # 上一段把抽屜關掉了，這裡要自己開回來
        persist = page.locator('[data-testid="drawer-persist"]')
        check("🔴 提示在（不是只有滑鼠那條）", persist.count() == 1)
        check(
            f"🔴 顯示的就是後端給的落點（{config.DATA_BIND}）",
            config.DATA_BIND in (persist.inner_text() if persist.count() else ""),
        )
        check("說得出「其他地方會消失」，不是只報一個路徑", "消失" in (persist.get_attribute("data-tip") or ""))

        print("== 點路徑＝複製，而且要有 toast，且 toast 必須疊在抽屜之上 ==")
        # ⚠ z-index 對不對**不可以用讀 CSS 數字來證明**：數字只在同一個堆疊脈絡裡才可比。
        #   這裡量的是「那個點上最上層的元素是誰」——瀏覽器自己算完的結果。
        ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        # 🔴 **直接點路徑本身**也要複製，不是只有點到那顆圖示才算。整條提示是一顆 button、
        #    `<code>` 在它裡面，所以 `closest("[data-act]")` 會找到外層——但那是「剛好成立」，
        #    有人日後把路徑拆出去或在它上面攔 click 就會靜靜失效。這裡直接點 code 釘住它。
        page.evaluate("() => navigator.clipboard.writeText('__before__')")
        persist.locator("code").click()
        page.wait_for_selector('[data-testid="toast"]', state="visible", timeout=4000)
        check(
            f"🔴 點路徑本身就複製到了（{page.evaluate('navigator.clipboard.readText()')}）",
            page.evaluate("navigator.clipboard.readText()") == config.DATA_BIND,
        )
        # 🔴 **一次點擊只准產生一則 toast。**
        #
        # ⚠ 這條守的是「同一顆元素被兩個 handler 各接一次」——那不是假想：`ed96517` 加了
        #   一支全域 `[data-copy]` 委派，而這顆按鈕當時正拿 `data-copy` 當自己的資料欄
        #   （它有自己的 `copy-persist` 分支）。於是一次點擊吐兩則標題不同的 toast、剪貼簿
        #   寫兩次，而 **markup 與那兩支程式碼分開看都是對的**，靜態上完全看不出來。
        # ⚠ 現在只有一個 handler 接得到它（資料欄已改名 `data-persist-path`），但**「現在
        #   只有一個」不等於「不會再有第二個」**——下次有人再發明一個掃得到它的全域機制，
        #   這條就會紅。等 800ms 而不是立刻數：第二則若是非同步來的，立刻數會漏掉。
        page.wait_for_timeout(800)
        _n = page.locator('[data-testid="toast"]').count()
        check(f"🔴 一次點擊只有一則 toast（實際 {_n} 則）", _n == 1)
        # 收掉這一則，下面要驗的是「再點一次」自己那一則
        page.locator('[data-testid="toast-close"]').first.click()
        page.wait_for_selector('[data-testid="toast"]', state="detached", timeout=4000)
        persist.click()
        page.wait_for_selector('[data-testid="toast"]', state="visible", timeout=4000)
        toast_el = page.locator('[data-testid="toast"]').first
        check("🔴 有 toast", toast_el.is_visible())
        check("toast 說得出複製了什麼", config.DATA_BIND in toast_el.inner_text())
        copied = page.evaluate("navigator.clipboard.readText()")
        check(f"🔴 剪貼簿真的是那個路徑（{copied}）", copied == config.DATA_BIND)
        box = toast_el.bounding_box()
        top = page.evaluate(
            "([x, y]) => { const e = document.elementFromPoint(x, y);"
            "  return e ? (e.closest('[data-testid=toast]') ? 'toast'"
            "            : e.closest('[data-testid=drawer]') ? 'drawer' : e.tagName) : 'none'; }",
            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
        )
        check(f"🔴 toast 那個點上最上層的是 toast 本身而不是抽屜（{top}）", top == "toast")
        # 抽屜把 .shell 設成 inert；toast 掛在 document.body 而不是 .shell 裡，所以仍點得到。
        check("toast 沒有被 inert 掃到（關閉鍵可以按）", page.locator('[data-testid="toast-close"]').first.is_enabled())

        print("== 提示是輪播：同時只露一條，而且會換 ==")
        # ⚠ 先把滑鼠移開、焦點放掉：輪播**依設計**在 hover/focus 時暫停（裡面有可點的
        #   複製鍵），而上一段剛剛點過它。不做這件事會等到逾時，然後看起來像「輪播壞了」。
        page.mouse.move(5, 5)
        page.evaluate("document.activeElement && document.activeElement.blur()")
        # 兩條提示各自有 testid（drawer-persist / drawer-mouse），這裡數的是「有 testid 的那些」
        hints = page.locator('[data-testid="drawer-hints"] [data-testid]')
        check(f"兩條提示都在 DOM 裡（{hints.count()} 條）", hints.count() == 2)
        shown = page.locator('[data-testid="drawer-hints"] [data-testid][data-on="true"]')
        check("🔴 同時只有一條露臉", shown.count() == 1)
        first = shown.first.get_attribute("data-testid")
        # ⚠ 等的是「換人了」而不是固定睡 6 秒：睡太短會偶爾紅、睡太久拖慢整套。
        # ⚠ 要接住逾時再斷言，不可以讓它直接拋：`wait_for_function` 逾時會炸出 traceback，
        #   整支腳本當場中止，後面幾段都不跑，而畫面上看不出是「輪播不動」還是別的壞了。
        #   （`check(..., True)` 在這個 repo 是「沒拋例外就代表成功」的慣用寫法，但前提是
        #   例外真的會被接住——這裡原本沒接，等於把紅燈變成崩潰。）
        rotated = True
        try:
            page.wait_for_function(
                "(prev) => { const el = document.querySelector("
                "  '[data-testid=drawer-hints] [data-testid][data-on=true]');"
                "  return el && el.dataset.testid !== prev; }",
                arg=first,
                timeout=9000,
            )
        except Exception:  # noqa: BLE001 — 逾時就是「沒換」，那正是要斷言的事
            rotated = False
        check("🔴 過一陣子會換成另一條（輪播真的在跑）", rotated)
        check("換完之後仍然只有一條露臉", shown.count() == 1)
        # 沒露臉的不可以還在 Tab 序裡——否則鍵盤使用者會 Tab 到一顆看不見的複製鍵
        hidden_focusable = page.evaluate(
            "[...document.querySelectorAll('[data-testid=drawer-hints] [data-testid]')]"
            "  .filter(h => h.dataset.on !== 'true' && !h.inert).length"
        )
        check(f"🔴 沒露臉的都退出 Tab 序（{hidden_focusable} 個漏網）", hidden_focusable == 0)
        page.click('[data-testid="drawer-close"]')  # 下一段要從沒有抽屜的狀態開始
        page.wait_for_selector('[data-testid="drawer"]', state="detached")

        print("== 標題列要說出這是哪一顆 ttyd（C / Rust）==")
        # 兩顆是同一個 UI，肉眼分不出來——而出問題時「你看到的是哪一版」正是第一個要問的。
        open_drawer(page, "e1")
        bin_tag = page.locator('[data-testid="drawer-bin"]')
        check("🔴 標記在，而且寫的是後端給的那一顆", bin_tag.count() == 1 and bin_tag.inner_text().strip() == "Rust")
        check(
            "說得出它是什麼、怎麼換（tooltip）",
            "ttyd" in (bin_tag.get_attribute("data-tip") or "") and "設定" in (bin_tag.get_attribute("data-tip") or ""),
        )
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")

        # 🟡 舊的 view 記錄沒有這個欄位（輕量升級只加欄位，既有列是 NULL）。
        #    不知道就**不要猜**——尤其不可以拿「這個人現在的偏好」頂替：改偏好不會換掉
        #    已經在跑的 ttyd，那個推論剛好會在最需要它的時候騙人。
        globals()["view_flavor"] = None
        open_drawer(page, "e2")
        check("🟡 後端沒給就不顯示（不猜）", page.locator('[data-testid="drawer-bin"]').count() == 0)
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")
        globals()["view_flavor"] = "Rust"

        print("== 開啟時：ttyd 用自己的字級排過版，送出的必須是**使用者字級**的格數 ==")
        # 🔴 這是使用者回報「打開通常大小是錯的」的那條路（2026-07-31 量到）。
        #    ttyd 先用它自己的預設字級 fit，之後才把字級套成存下來的 18px 且**不重新 fit**。
        #    父頁面若信任 `term.cols/rows`，送出去的就是「另一個字級的」格數：
        #    實測 iframe 904×650 送出 112×42（13px 的格數），畫面卻用 18px 畫，
        #    `.xterm-screen` 撐到 1165×840、超出框 29%，TUI 下半截看不見。
        # ⚠ **不可以拿 `term.cols` 當期望值**——沒重新 fit 的話它跟送出的值一樣是錯的，
        #    兩個一起錯會讓斷言全綠（既有那條「送的是 xterm 當下的尺寸」正是這個盲點）。
        #    所以期望值要從 iframe 的實際大小 + 實際字級**獨立算**出來。
        # （上一段結束時抽屜已經關了，這裡不必再關一次）
        globals()["stub_font_after"] = "font = 18;"  # 套了字級但沒重 fit（＝ttyd 的行為）
        page.evaluate("() => localStorage.setItem('claude-pty:term-font', '18')")
        posts.clear()
        open_drawer(page, "e1")
        page.wait_for_timeout(700)
        geom = page.evaluate("""() => {
          const w = document.querySelector('[data-testid="drawer-frame"]').contentWindow;
          return { w: w.innerWidth, h: w.innerHeight, font: w.term.options.fontSize,
                   cols: w.term.cols, rows: w.term.rows };
        }""")
        # 與替身 fit() 內同一個算式（等寬字的常見比例）——獨立於 term.cols 算出來
        want_cols = max(2, int(geom["w"] // (geom["font"] * 0.6)))
        want_rows = max(1, int(geom["h"] // (geom["font"] * 1.2)))
        stale_cols = max(2, int(geom["w"] // (14 * 0.6)))  # 替身預設字級排出來的（錯的那個）
        sent = posts[0] if posts else {}
        check(f"存的字級真的套上去了（{geom['font']}px）", geom["font"] == 18)
        check(
            f"🔴 送出的是 18px 的格數 {want_cols}×{want_rows}（實收 {sent.get('cols')}×{sent.get('rows')}）",
            sent.get("cols") == want_cols and sent.get("rows") == want_rows,
        )
        check(f"🔴 **不是** ttyd 自己排版時的 {stale_cols} 欄（那是另一個字級的格數）", sent.get("cols") != stale_cols)
        check(
            "🔴 xterm 自己也被重新 fit 過（不只是我們送對數字，畫面也要跟著對）",
            geom["cols"] == want_cols and geom["rows"] == want_rows,
        )
        check(f"仍然只送一發（重 fit 與開啟那發要併起來，收到 {len(posts)} 發）", len(posts) == 1)
        check(f"redraw 旗標沒有因此掉了：{sent}", sent.get("redraw") is True)
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")
        globals()["stub_font_after"] = ""
        page.evaluate("() => localStorage.removeItem('claude-pty:term-font')")

        print("== 🔴 送出的時機：抽屜停定之後，不是滑到一半 ==")
        # 這一條守的是**順序**，不是數字。
        #
        # 送出的時機原本完全由一個 300ms 的 debounce 決定，而抽屜的滑入是 CSS 裡的 240ms
        # transition，兩者誰先誰後沒有任何人管：prefers-reduced-motion、慢一點的機器、
        # 或哪天有人把動畫調長，都會換一個答案。把動畫拉長到 800ms 就看得出來：修之前
        # 那一發在 +335ms 就送掉了，那時面板還滑到一半（2026-08-25 用 Playwright 逐幀量的）。
        #
        # ⚠ 這裡**不驗**「送出的格數是中途值」，因為那件事並沒有發生：滑入用的是
        #   `transform: translateX()`，不影響版面尺寸，實測整段動畫期間 iframe 的
        #   clientWidth/innerWidth 從第一幀到最後一幀都是 1295×834。要守的是
        #   「UI 停定之後才告訴 PTY」這個順序本身，不是一個被誤診出來的數字。
        # ⚠ 動畫時長只在測試裡改（add_style_tag），`app.css` 一行沒動；量完就把那條規則
        #   移掉，後面的段落不受影響。
        slow = page.add_style_tag(
            content=".drawer__panel { transition: transform 800ms linear !important; }"
        )
        posts.clear()
        post_at.clear()
        page.click('[data-testid="row-open-e1"]')
        page.wait_for_selector('[data-testid="drawer"]')
        # 面板的滑入真的跑完的時刻。
        # ⚠ 不可以只 `getAnimations()` 一次就算數：`data-open` 是下一幀才打上去的，這一刻
        #   多半還沒有任何動畫在跑，拿到空陣列會讓這條斷言變成恆真（動畫結束＝按下去的
        #   那一刻，當然比送出早）。所以要等到它真的出現，再等它 finished。
        anim_end = page.evaluate("""() => new Promise((resolve) => {
          const panel = document.querySelector('.drawer__panel');
          let frames = 0;
          const poll = () => {
            const anims = panel.getAnimations();
            if (anims.length) {
              Promise.all(anims.map((a) => a.finished.catch(() => {}))).then(() => resolve(Date.now()));
              return;
            }
            // 等了二十幾幀還是沒有動畫：prefers-reduced-motion 把 transition 關掉了，
            // 那就是「一開始就停定」，據實回報而不是假裝有等過。
            if (++frames > 24) return resolve(Date.now());
            requestAnimationFrame(poll);
          };
          poll();
        })""")
        for _ in range(80):
            if posts:
                break
            page.wait_for_timeout(50)
        page.wait_for_timeout(400)
        live = term_size(page)
        last = posts[-1] if posts else {}
        sent_at = post_at[-1] if post_at else None
        check(f"有送出（收到 {len(posts)} 發）", len(posts) >= 1)
        check(
            "🔴 最後一發是在抽屜停定**之後**才送的"
            f"（動畫結束後 {round(sent_at - anim_end) if sent_at else '沒送'}ms 才送出）",
            sent_at is not None and sent_at >= anim_end,
        )
        check(
            f"送出的就是最終尺寸 {live['cols']}×{live['rows']}（實收 {last.get('cols')}×{last.get('rows')}）",
            last.get("cols") == live["cols"] and last.get("rows") == live["rows"],
        )
        check(f"等歸等，還是只送一發（收到 {len(posts)} 發）", len(posts) == 1)
        check(f"redraw 旗標沒有被這道閘吃掉：{last}", last.get("redraw") is True)
        slow.evaluate("el => el.remove()")
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")

        print("== 🔴 面板上有無限動畫時，尺寸照樣送得出去 ==")
        # 這條守的是 `drawerSettled()` 的濾網。`getAnimations()` 回的是元素上**所有**動畫，
        # 面板上只要有一個 `animation: ... infinite`（呼吸燈、脈動、旋轉的載入圖示都算），
        # 它的 `finished` 永遠不會 resolve，於是 /resize 從此再也送不出去。
        #
        # ⚠ 這種壞法最惡劣的地方是**肇因與症狀完全不相干**：改的是一條 CSS 裝飾，壞掉的是
        #   容器裡的 TTY 尺寸。沒有這條斷言的話，加裝飾的人不會有任何理由懷疑到自己。
        # ⚠ 用 add_style_tag 灌一個無限動畫進去，`app.css` 一行沒動，量完就移除。
        spin = page.add_style_tag(
            content="@keyframes e2e-pulse { from { opacity: 1 } to { opacity: 0.985 } }"
            " .drawer__panel { animation: e2e-pulse 1s linear infinite !important; }"
        )
        posts.clear()
        open_drawer(page, "e1")
        live = term_size(page)
        sent = posts[-1] if posts else {}
        check(f"🔴 有無限動畫也照樣送得出去（收到 {len(posts)} 發）", len(posts) >= 1)
        check(
            f"而且送的還是最終尺寸 {live['cols']}×{live['rows']}（實收 {sent.get('cols')}×{sent.get('rows')}）",
            sent.get("cols") == live["cols"] and sent.get("rows") == live["rows"],
        )
        spin.evaluate("el => el.remove()")
        page.click('[data-testid="drawer-close"]')
        page.wait_for_selector('[data-testid="drawer"]', state="detached")

        print("== 同時只留一個抽屜 ==")
        open_drawer(page, "e1")
        page.evaluate("""() => document.querySelector('[data-testid=shell]').inert = false""")
        page.click('[data-testid="row-open-e2"]', force=True)
        page.wait_for_selector('[data-testid="drawer"]')
        page.wait_for_timeout(700)
        check(
            "開第二個時第一個要收掉（不然兩個 iframe 同時連著同一場）",
            page.locator('[data-testid="drawer"]').count() == 1,
        )

        browser.close()
finally:
    import shutil

    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'done' if not _fails else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
