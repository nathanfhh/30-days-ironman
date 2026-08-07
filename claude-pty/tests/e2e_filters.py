"""E2E：列表篩選的整條路（真瀏覽器）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with playwright python tests/e2e_filters.py
（首次需 `uv run --with playwright playwright install chromium`）

**不需要 docker**：直接把 session / 歷史列塞進一個暫時的 SQLite，再用真瀏覽器操作
篩選列。驗的是「使用者實際會做的那一串」——點開、選條件、打字找人、拉自訂區間、
清除——以及每一步之後**網址、清單、生效數三者是否一致**。

⚠ 一律以 `data-testid` 取元素，不要靠 class 或 DOM 結構。class 是給樣式用的，
  改版面就會斷；testid 是給這支測試用的契約，改了會在這裡紅，那正是我們要的。
"""
import datetime as _dt
import logging
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="e2e-filters-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "e2e-filters-secret"


from playwright.sync_api import sync_playwright  # noqa: E402

from server import auth  # noqa: E402
from server.app import app  # noqa: E402
from server.db import init_db, reset_engine, session_scope  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402
from server.models import SessionHistory  # noqa: E402
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

# 三個帳號：admin、一般使用者、以及一個退場過的（admin 改掉了他的密碼——他只是一列
# 普通帳號，歷史照樣掛在他名下）
admin = auth.create_user("e2e-admin", "e2e-password-1", is_admin=True)
plain = auth.create_user("e2e-plain", "e2e-password-1", is_admin=False)
gone = auth.create_user("e2e-retired", "e2e-password-1", is_admin=False)
auth.change_password(gone["id"], "e2e-exited-password-1", require_old=False)

now = utcnow()

def profile(cli="claude", network="restricted", capture=False, telemetry=False):
    return {"cli": cli, "network": network,
            "capture": capture, "telemetry": telemetry}


# 三筆進行中：兩筆 admin 的（一新一舊）、一筆一般使用者的
with session_scope() as s:
    for i, (uid, prof, age) in enumerate([
        (admin["id"], profile(), 0),
        (admin["id"], profile(capture=True), 3),
        (plain["id"], profile(network="unrestricted"), 20),
    ], start=1):
        s.add(SessionRow(id=f"e{i}", container_name=f"ec{i}", user_id=uid, workdir="/w",
                         profile=prof,
                         created_at=now - _dt.timedelta(days=age),
                         last_active_at=now))
# 歷史三筆：不同擁有者與結束原因。
with session_scope() as s:
    s.add(SessionHistory(session_id="eh1", container_name="ehc1", user_id=gone["id"],
                         username=gone["username"], profile=profile(),
                         workdir="/w", created_at=now - _dt.timedelta(days=2),
                         last_active_at=now - _dt.timedelta(days=2),
                         ended_at=now - _dt.timedelta(hours=6), ended_reason="exited"))
    s.add(SessionHistory(session_id="eh2", container_name="ehc2", user_id=admin["id"],
                         username=admin["username"], profile=profile(),
                         workdir="/w", created_at=now - _dt.timedelta(days=3),
                         last_active_at=now - _dt.timedelta(days=3),
                         ended_at=now - _dt.timedelta(hours=12), ended_reason="terminated"))
    s.add(SessionHistory(session_id="eh3", container_name="ehc3", user_id=plain["id"],
                         username=plain["username"], profile=profile(),
                         workdir="/w", created_at=now - _dt.timedelta(days=4),
                         last_active_at=now - _dt.timedelta(days=4),
                         ended_at=now - _dt.timedelta(days=1), ended_reason="exited"))


class _FakeContainer:
    """list() 會去問 docker 對帳。回一份「三個都還在」的假名單，否則每一列都會被歸檔。"""

    def __init__(self, name):
        self.name, self.status, self.id = name, "running", f"cid-{name}"

    def logs(self, **_kw):
        return b""


class _FakeDocker:
    class containers:
        @staticmethod
        def list(**_kw):
            return [_FakeContainer(f"ec{i}") for i in (1, 2, 3)]


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


def login(page, username):
    """登入並**確認真的離開登入頁**才返回。

    ⚠ 不可以用 `wait_for_url(f"{BASE}/**")` 等——那個 pattern 連 `/login` 自己都符合，
      會在登入請求還沒回來時就返回，接著的 goto 直接把它取消掉。症狀是後面每一條斷言
      都在登入頁上跑，而且「網址沒有 owner」這種檢查會**空過**（登入頁本來就沒有）。
    """
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.fill("#username", username)
    page.fill("#password", "e2e-password-1")
    page.click("#login-btn")
    page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=8000)


def rows(page):
    """畫面上實際有幾筆資料列（不含表頭）。"""
    return page.locator('[data-testid="manifest"] .manifest__row:not(.manifest__row--head)').count()


def open_filters(page):
    if page.locator('[data-testid="filter-bar"]').is_hidden():
        page.click('[data-testid="filter-toggle"]')
    page.wait_for_selector('[data-testid="filter-bar"]', state="visible")


def pick(page, mount, value):
    """展開某一格並選一個值。value 空字串＝「不限」。"""
    page.click(f'[data-testid="{mount}-button"]')
    page.click(f'[data-testid="{mount}-opt-{value or "any"}"]')
    page.wait_for_timeout(450)      # 讓 refresh 打完（列表是非同步重畫的）


try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  timezone_id="Asia/Taipei")
        page = ctx.new_page()

        print("== 收合時就看得出有沒有套用條件 ==")
        login(page, "e2e-admin")
        page.wait_for_timeout(600)
        check("預設是收合的", page.locator('[data-testid="filter-bar"]').is_hidden())
        check("沒有條件時按鈕上沒有數字",
              page.inner_text('[data-testid="filter-toggle"]').strip() == "篩選")
        check("三筆都在", rows(page) == 3)

        print("== 選一個條件：網址、清單、生效數三者要一致 ==")
        open_filters(page)
        pick(page, "pick-fnet", "unrestricted")
        check("網址帶上 network=unrestricted", "network=unrestricted" in page.url)
        check("清單剩一筆", rows(page) == 1)
        check("按鈕顯示「篩選 · 1」",
              "1" in page.inner_text('[data-testid="filter-toggle"]'))
        check("摘要說 1 個條件生效中",
              "1" in page.inner_text('[data-testid="filter-summary"]'))

        print("== 三態：不限 / 有 / 沒有是三件事 ==")
        pick(page, "pick-fnet", "")                 # 先放掉 network
        pick(page, "pick-fcap", "1")
        check("有錄製 → 1 筆", rows(page) == 1)
        pick(page, "pick-fcap", "0")
        check("沒錄製 → 2 筆（不是 0）", rows(page) == 2)
        pick(page, "pick-fcap", "")
        check("不限 → 回到 3 筆", rows(page) == 3)

        page.keyboard.press("Escape")

        # （owner 篩選整組拔掉了：搜尋式 picker 目前沒有消費者，相關互動測試隨之退場。
        #   「篩選那格不存在」在最下面的非 admin 段一併釘住——它現在對誰都不存在。）

        print("== 自訂時間區間：兩個月並排的區間選擇器 ==")
        pick(page, "pick-since", "custom")
        check("選了自訂才出現起迄那格",
              page.locator('[data-testid="filter-range"]').is_visible())
        # 位置也要對：時間範圍在最後，起迄緊接在它後面（不是跑到另一塊區域）
        order = page.eval_on_selector_all(
            '.filters__grid .field:not([hidden])',
            "els => els.map(e => e.querySelector('.label').textContent.trim())")
        check(f"時間範圍後面直接接起迄：{order[-2:]}",
              order[-2:] == ["時間範圍", "起迄"])
        check("還沒選過時按鈕上是提示不是值",
              "指定區間" in page.inner_text('[data-testid="range-trigger"]'))

        page.click('[data-testid="range-trigger"]')
        page.wait_for_selector('[data-testid="range-panel"]', state="visible")
        check("面板整個在視窗內（fixed 定位算錯的話會被切掉一半）",
              page.evaluate("""() => {
                const r = document.querySelector('[data-testid="range-panel"]')
                            .getBoundingClientRect();
                return r.left >= 0 && r.top >= 0
                    && r.right <= innerWidth && r.bottom <= innerHeight;
              }"""))
        check("兩個月並排（各 42 格）",
              page.locator('.rangepick__cal').count() == 2
              and page.locator('.rangepick__day').count() == 84)

        # ⚠ 點日期不可以把面板關掉。點擊處理器會重建 innerHTML，若外部點擊判定跑在冒泡
        #   階段，等事件走到 document 時被點的節點已經不在 DOM 裡，`contains()` 回 false
        #   就會誤判成「點在外面」——真瀏覽器實測過這個 bug（2026-07-26）。
        days = page.locator('.rangepick__day:not(.is-other)')
        days.nth(5).click()
        page.wait_for_timeout(200)
        check("點了一天之後面板還開著",
              page.locator('[data-testid="range-panel"]').is_visible())
        days.nth(18).hover()
        page.wait_for_timeout(250)
        check("還沒定終點時，滑過的那段會即時預覽",
              page.locator('.rangepick__day.is-in').count() > 0)
        days.nth(18).click()
        page.wait_for_timeout(200)
        check("兩端各標一格", page.locator('.rangepick__day.is-edge').count() == 2)
        check("還沒按確定就不該動到網址（半截的區間不查詢）", "from=" not in page.url)
        page.click('[data-testid="range-ok"]')
        page.wait_for_timeout(600)
        check("按確定才送出", "from=" in page.url and "to=" in page.url)
        check("時區偏移有編碼進網址（+ 會被解成空白）",
              "%2B" in page.url or "-0" in page.url)
        check("按鈕顯示選好的區間",
              "→" in page.inner_text('[data-testid="range-trigger"]'))
        check("起迄只算**一個**條件（畫面上就是一格「時間範圍」）",
              "1" in page.inner_text('[data-testid="filter-toggle"]'))

        print("== 用面板上方的輸入框直接指定，結果要與點選一致 ==")
        page.click('[data-testid="range-trigger"]')
        page.wait_for_selector('[data-testid="range-panel"]', state="visible")
        local = _dt.timezone(_dt.timedelta(hours=8))
        page.fill('[data-testid="range-from-date"]',
                  (now - _dt.timedelta(days=4)).astimezone(local).strftime("%Y-%m-%d"))
        page.fill('[data-testid="range-to-date"]',
                  (now - _dt.timedelta(days=1)).astimezone(local).strftime("%Y-%m-%d"))
        page.click('[data-testid="range-ok"]')
        page.wait_for_timeout(600)
        check("四天前到一天前 → 只剩中間那筆", rows(page) == 1)

        print("== 預設值與自訂範圍互斥 ==")
        pick(page, "pick-since", "30")
        check("改回預設值時 from/to 一併清掉（後端不接受兩者並存）",
              "from=" not in page.url and "to=" not in page.url)
        check("起迄那格收起來",
              page.locator('[data-testid="filter-range"]').is_hidden())

        print("== 清除全部 ==")
        pick(page, "pick-fcap", "1")
        check("先疊兩個條件", "2" in page.inner_text('[data-testid="filter-toggle"]'))
        page.click('[data-testid="filter-clear"]')
        page.wait_for_timeout(500)
        check("網址清乾淨（連裸問號都沒有）", "?" not in page.url or page.url.endswith("?") is False)
        check("按鈕回到沒有數字",
              page.inner_text('[data-testid="filter-toggle"]').strip() == "篩選")
        check("清除鈕變回停用", page.locator('[data-testid="filter-clear"]').is_disabled())
        check("清單回到三筆", rows(page) == 3)

        print("== 條件跟著網址走：重新整理後畫面要對得上 ==")
        page.goto(f"{BASE}/?network=restricted&capture=1", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        check("帶條件進來會自動展開篩選列（收合著會以為看到的是全部）",
              page.locator('[data-testid="filter-bar"]').is_visible())
        check("picker 停在網址說的值",
              "限制" in page.inner_text('[data-testid="pick-fnet-button"]'))
        check("清單是篩過的", rows(page) == 1)

        print("== 非 admin：殘留的 ?owner= 書籤不作廢、也繞不過授權 ==")
        # owner 篩選已整組拔除：那一格對誰都不存在，後端把 ?owner= 當一般未知參數忽略
        # （授權在 list 那層由 user_id 綁死，本來就不是靠篩選）。舊書籤照樣能用。
        ctx.clear_cookies()          # logout 是 POST，用 goto 打不到；清 cookie 等效且直接
        login(page, "e2e-plain")
        page.goto(f"{BASE}/?owner={admin['id']}&cli=claude", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        check("其餘條件照樣生效", "cli=claude" in page.url)
        open_filters(page)
        check("使用者那一格不存在（對 admin 也一樣——整組拔掉了）",
              page.locator('[data-testid="field-owner"]').count() == 0)
        check("只看得到自己的那一筆（?owner= 繞不過授權）", rows(page) == 1)

        browser.close()
finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'done' if not _fails else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
