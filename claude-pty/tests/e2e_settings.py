"""E2E：身分下拉裡的「設定」對話框（真瀏覽器）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography --with playwright python tests/e2e_settings.py

**不需要 docker**。

## 為什麼有這一支

這個對話框原本**零測試覆蓋**，而它是前端改動最密集的地方之一：登出從招牌收進這個下拉、
GitLab PAT 欄位從這裡搬去 account.html——每一次搬完都在 CSS 裡留下沒人用的規則，而沒有
任何東西會紅。2026-08-08 清理時一次刪掉四組（`.settings__state` / `__more` / `__hint` /
`__control--row`），只能靠人手動開一次畫面確認沒刪錯。

那次還撞到一個**靜態掃描永遠抓不到**的東西：`.settings__control { width: 100% }` 掛在
picker 的掛載點上，而 `createPicker` 開頭就 `mount.className = "picker"` 把它蓋掉了——
markup 裡有、`grep` 找得到、**執行期卻不存在**，那條規則從來沒有生效過。

## 所以這支的紀律（要改它的人請照著做）

**「這個樣式有生效」一律用量的——computed style 或實際尺寸，不准只斷言 DOM 裡有那個字串。**
斷言 class 名稱存在，正是上面那個 bug 能躲那麼久的原因：它在 markup 裡確實存在。

`locator(...).count() == 0` 只用在**反方向**（「這個東西不該存在」）。那個方向沒有
「靜態上看起來被守著」的問題——數不到就是真的沒有。
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

TMP = tempfile.mkdtemp(prefix="e2e-settings-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "e2e-settings-secret"

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
PW = "e2e-settings-password"
auth.create_user("settings-user", PW, is_admin=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
# 關掉 werkzeug 的請求 log，理由同 e2e_account.py 檔頭那段（真正的失敗會被輪詢埋掉，
# 而且 daemon thread 在 exit 時還在寫會噴一串紅字）。
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

try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="Asia/Taipei")
        page = ctx.new_page()
        page.goto(f"{BASE}/login")
        page.fill('[data-testid="login-username"]', "settings-user")
        page.fill('[data-testid="login-password"]', PW)
        page.get_by_role("button", name="進入控制台").click()
        page.wait_for_selector('[data-testid="account-btn"]')

        print("== 招牌：登出已經收進下拉，不再是常駐膠囊 ==")
        # 反方向才用 count()：這顆元素不該存在。
        check("招牌上沒有常駐的登出膠囊", page.locator(".masthead__logout").count() == 0)
        # ⚠ 正方向用**量的**：`--control-h` 那組對齊選擇器列了哪些 class 是會漂的
        #   （拿掉 .masthead__logout 時就要同步改），而漂掉之後畫面上是「有一顆比別人高」
        #   ——DOM 裡完全看不出來。所以量高度，不是斷言 class 在。
        heights = page.eval_on_selector_all(
            ".masthead .navseg, .masthead .cred, .masthead .whoami, .masthead #theme-picker .picker__button",
            "els => els.map(e => Math.round(e.getBoundingClientRect().height))",
        )
        # ⚠ 誠實標註這條的靈敏度：`.whoami` 的內距加起來剛好也是 38px，所以**只把它**
        #   從對齊清單裡拿掉不會被抓到（實測）。拿掉 `.cred` 就會（量到 23px）。它守的是
        #   「這一排看起來對齊」這個結果，不是「那條規則列了誰」。
        check(f"招牌上每一顆膠囊同高，都是 38px（量到 {heights}）", len(heights) >= 3 and set(heights) == {38})

        print("== 開啟設定對話框 ==")
        page.click('[data-testid="account-btn"]')
        page.wait_for_selector('[data-testid="menu-settings"]')
        page.click('[data-testid="menu-settings"]')
        page.wait_for_selector('[data-testid="settings-modal"]')
        check("對話框開得起來", page.is_visible('[data-testid="settings-modal"]'))
        check("「終端程式」那一列在", page.inner_text('[data-testid="settings-label"]').strip() == "終端程式")
        check("說明文字看得見", page.locator('[data-testid="settings-note"]').first.is_visible())

        print("== 版面：量出來的，不是斷言 class 在 ==")
        # picker 要等 /api/prefs 回來才建——直接問 is_visible 會抓到還沒建好的瞬間。
        page.wait_for_selector('[data-testid="pick-ttyd-button"]', state="visible")
        row_w = page.eval_on_selector(
            '[data-testid="settings-row"]', "e => Math.round(e.getBoundingClientRect().width)"
        )
        mount_w = page.eval_on_selector('[data-testid="pick-ttyd"]', "e => Math.round(e.getBoundingClientRect().width)")
        # ⚠ 這一條就是 `.settings__control` 那個 bug 的守門人。那條規則寫的是 width:100%，
        #   而它從來沒生效過——版面之所以正確，靠的是 `.settings__row` 的 flex column 撐滿。
        #   量寬度才分得出「規則有效」與「剛好也對」；斷言 class 在只會兩種都綠。
        check(f"picker 掛載點撐滿整列（row {row_w}px / mount {mount_w}px）", mount_w > 0 and mount_w == row_w)
        # ⚠ **不要**在這裡斷言「掛載點執行期的 class 是 picker」。寫過，然後發現它
        #   **不可能紅**：掛載點的 markup 上已經沒有別的 class 了，所以 `className =` 與
        #   `classList.add` 兩種寫法的結果一模一樣。一條不會紅的斷言比沒有斷言更糟。
        #   真正要守的不變式在下面那段（原始碼層級），那個才驗得出來。
        # 對話框本身要真的浮在內容之上，而 z-index 數字只在同一個堆疊脈絡裡可比——
        # 所以問瀏覽器「這個點上最上層是誰」，同 e2e_drawer 的 toast 那條。
        box = page.locator('[data-testid="modal-box"]').bounding_box()
        top = page.evaluate(
            "([x, y]) => { const e = document.elementFromPoint(x, y);"
            "  return e ? (e.closest('[data-testid=modal-box]') ? 'modal' : e.tagName) : 'none'; }",
            [box["x"] + box["width"] / 2, box["y"] + 12],
        )
        check(f"對話框真的浮在最上層（那個點上是 {top}）", top == "modal")

        print("== 已經沒人用的那四組 class 不該有元素掛著 ==")
        # 這一組刻意用 count()：問的是「不該存在」，那個方向數不到就是真的沒有。
        for cls in ("settings__state", "settings__more", "settings__hint", "settings__control--row"):
            check(f"沒有 .{cls}", page.locator(f".{cls}").count() == 0)

        print("== 切換終端程式：存得下去，而且只影響之後開的終端 ==")
        page.click('[data-testid="pick-ttyd-button"]')
        # 選項以 role=option 認，不靠 `li` 這個標籤或 picker 的 class
        options = page.locator('[data-testid="pick-ttyd-menu"]').get_by_role("option")
        options.first.wait_for()
        opts = options.all_inner_texts()
        opts = [t.strip() for t in opts]
        check(f"兩顆 ttyd 都在選單裡（{opts}）", len(opts) == len(config.TTYD_BINS))
        options.last.click()
        # 存到 DB 才算數——畫面上換了字但沒存下去是這種偏好設定最典型的假成功。
        for _ in range(50):
            if auth.get_user(1)["ttyd_bin"] != config.TTYD_BIN_DEFAULT:
                break
            time.sleep(0.1)
        saved = auth.get_user(1)["ttyd_bin"]
        check(f"選的那一顆真的存進 DB（{saved}）", saved in config.TTYD_BINS and saved != config.TTYD_BIN_DEFAULT)

        # ⚠ 這裡曾經有一條「picker 掛載點不准帶 class」的原始碼層斷言。它守的是 legacy
        #   `createPicker` 的一個具體 bug：那個函式開頭是 `mount.className = "picker"`，
        #   會**把掛載點原本的 class 整個吃掉**，於是寫在掛載點上的樣式規則從來沒有生效過，
        #   而且不會報錯、grep 還找得到它（`.settings__control { width: 100% }` 就這樣睡了很久）。
        #
        #   2026-08-26 拆掉 legacy 之後**那個機制不存在了**：picker 是一個 Vue 元件，沒有
        #   「掛載點」這個東西可以被吃掉。查證過再刪的，不是看到紅燈就拿掉：
        #   `grep -rn 'className\s*=' frontend/src/`（排除 __tests__）**一個結果都沒有**。
        #   性質消失，斷言才跟著消失。

        print("== Esc 關得掉（焦點不在對話框裡也要收到）==")
        page.keyboard.press("Escape")
        page.wait_for_selector('[data-testid="settings-modal"]', state="detached")
        check("按 Esc 之後對話框收掉了", page.locator('[data-testid="settings-modal"]').count() == 0)

        browser.close()
finally:
    __import__("shutil").rmtree(TMP, ignore_errors=True)

print("\ndone" if not _fails else f"\n{_fails} FAILED")
sys.exit(1 if _fails else 0)
