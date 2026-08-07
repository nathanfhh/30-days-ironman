"""E2E：帳號管理頁的清單與分頁（真瀏覽器）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with playwright python tests/e2e_account.py

**不需要 docker**。驗的是管理員實際會做的那一串：翻頁、建一個新帳號之後看得到他、
以及每一列只有「重設密碼」一顆動作鈕（停用/提權/降權都不存在——退場＝改掉密碼）。

⚠ 可見性一律問 `is_visible()`，不要問 `hidden` 屬性。作者樣式（`.pager{display:flex}`
  之類）的特異性比 UA 樣式表的 `[hidden]{display:none}` 高，屬性是 true 而畫面上還在
  ——這個坑已經踩過一次（篩選列的 `.field`）。
"""
import logging
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="e2e-account-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "e2e-account-secret"

from playwright.sync_api import sync_playwright  # noqa: E402

from server import auth  # noqa: E402
from server.app import app  # noqa: E402
from server.db import init_db, reset_engine  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


reset_engine()
init_db()
PW = "e2e-password-1"
# 排序在最前面（管理員自己）、中段一堆、最後一個排最遠——三個位置各驗一件事
auth.create_user("aaa-boss", PW, is_admin=True)
for i in range(24):
    auth.create_user(f"u{i:02d}", PW)
auth.create_user("zzz-off", PW)
TOTAL = len(auth.list_users())


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


def names(page):
    return page.eval_on_selector_all(
        '[data-testid="roster"] tr td:nth-child(2)',
        "els => els.map(e => e.textContent.trim())")


def status(page):
    return page.inner_text('[data-testid="roster-status"]').replace("\n", "")


try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000},
                                  timezone_id="Asia/Taipei")
        page = ctx.new_page()

        page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        page.fill("#username", "aaa-boss")
        page.fill("#password", PW)
        page.click("#login-btn")
        page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=8000)
        page.goto(f"{BASE}/account", wait_until="domcontentloaded")
        page.wait_for_timeout(800)

        print("== 第一頁 ==")
        check("分頁列真的看得見（不是只有 hidden 屬性被拿掉）",
              page.locator('[data-testid="roster-pager"]').is_visible())
        first = names(page)
        check(f"一頁 {config.PAGE_SIZE} 筆（收到 {len(first)}）",
              len(first) == config.PAGE_SIZE)
        check(f"狀態寫得出總數：{status(page)}",
              f"共{TOTAL}筆" in status(page).replace(" ", ""))
        check("上一頁是停用的（已經在第一頁了）",
              page.locator('[data-testid="roster-prev"]').is_disabled())
        check("下一頁可以按", page.locator('[data-testid="roster-next"]').is_enabled())
        # 🔴 動作鈕只有「重設密碼」。停用/復用/提權/降權的按鈕**整個介面都不存在**
        #    ——退場＝改掉他的密碼。哪天有人把這些鈕加回來，先在這裡現形。
        for act in ("disable", "enable", "promote", "demote"):
            check(f"整頁沒有 data-act={act} 的按鈕",
                  page.locator(f'button[data-act="{act}"]').count() == 0)
        check("每一列都有「重設密碼」（含自己那列——那是改自己密碼的另一條入口）",
              page.locator('button[data-act="reset"]').count() == len(first))

        print("== 翻到下一頁 ==")
        page.click('[data-testid="roster-next"]')
        page.wait_for_timeout(600)
        second = names(page)
        check(f"換了一批人（{second[0]} 起）", second and second[0] not in first)
        check("沒有和上一頁重複", not set(first) & set(second))
        check(f"頁碼跟著走：{status(page)}",
              status(page).replace(" ", "").startswith(f"{config.PAGE_SIZE + 1}–"))
        check("上一頁變成可以按", page.locator('[data-testid="roster-prev"]').is_enabled())

        print("== 翻到最後一頁：一個都不能漏 ==")
        seen = set(first) | set(second)
        while page.locator('[data-testid="roster-next"]').is_enabled():
            page.click('[data-testid="roster-next"]')
            page.wait_for_timeout(500)
            seen |= set(names(page))
        check(f"翻完蒐集到全部 {TOTAL} 個帳號，沒有漏人", len(seen) == TOTAL)
        check("排最遠的 zzz-off 在最後一頁", "zzz-off" in names(page))
        check("下一頁在最後一頁是停用的",
              page.locator('[data-testid="roster-next"]').is_disabled())

        print("== 回上一頁 ==")
        page.click('[data-testid="roster-prev"]')
        page.wait_for_timeout(600)
        check("退得回去，而且不是空的", len(names(page)) == config.PAGE_SIZE)

        print("== 建立新帳號：要看得到他，即使他排在別頁 ==")
        # 名字刻意排在最後——不跳頁的話，建完會停在目前這頁，畫面上完全看不到他，
        # 跟建立失敗長得一模一樣
        page.fill("#new-user", "zzz-newbie")
        page.fill("#new-user-pw", PW)
        page.click('#user-form button[type="submit"]')
        page.wait_for_timeout(1200)
        check("新帳號出現在畫面上（清單已翻到他那一頁）", "zzz-newbie" in names(page))
        check(f"總數加一：{status(page)}",
              f"共{TOTAL + 1}筆" in status(page).replace(" ", ""))

        print("== 長名字要被截斷，不能把表格推爆 ==")
        # 後端的長度上限只管得住碼位與東亞寬字元；字形寬度是字體的事、伺服器量不到
        # （U+FDF5 這種阿拉伯連字算一欄卻畫得像四欄）。所以版面這一層要自己站得住。
        table_w = page.eval_on_selector(".roster", "el => el.clientWidth")
        page.fill("#new-user", "z" * 32)
        page.fill("#new-user-pw", PW)
        page.click('#user-form button[type="submit"]')
        page.wait_for_timeout(1200)
        cell = page.locator('.roster__name', has_text="zzzzzzzz").first
        box = cell.evaluate("el => ({ scroll: el.scrollWidth, client: el.clientWidth })")
        check(f"名字被截斷（內容 {box['scroll']}px > 格寬 {box['client']}px）",
              box["scroll"] > box["client"])
        check("表格沒有被撐寬",
              page.eval_on_selector(".roster", "el => el.clientWidth") <= table_w)
        check("完整名稱仍拿得到（放在 title，滑上去看得見）",
              cell.get_attribute("title") == "z" * 32)

        print("== 只有一頁時整條分頁列收起來 ==")
        # 端點每次請求才讀 config.PAGE_SIZE，所以改了之後重整就生效。
        # ⚠ 只能設到 MAX_PAGE_SIZE 以內：超過的話端點會拿這個預設值去撞自己的上限檢查，
        #   整張列表回 400（config 在載入時會夾，但這裡是直接改變數，繞過了那道夾）。
        config.PAGE_SIZE = config.MAX_PAGE_SIZE
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        check("一頁裝得下時分頁列不佔版面",
              page.locator('[data-testid="roster-pager"]').is_hidden())
        # ⚠ 不要寫死人數。這一輪測試自己也會建帳號，寫死的常數會在加測試案例時變成
        #   「測試錯了」而不是「程式錯了」——那種紅燈最後總是被順手改掉數字。
        #   也不要從分頁列讀：它這時是隱藏的，inner_text 會回空字串。直接問資料庫。
        expected = len(auth.list_users())
        check(f"但人一個都沒少（資料庫裡共 {expected} 個）", len(names(page)) == expected)

        browser.close()
finally:
    import shutil

    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'done' if not _fails else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
