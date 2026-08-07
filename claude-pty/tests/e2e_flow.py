"""E2E：真瀏覽器跑完整管理流程（ADR 0008 階段 6）。

用真 Flask server + 真 docker container + 真 ttyd + 真 Chromium 走一遍：
    登入 → 建立 session → 列表出現 → 開啟終端（新分頁 xterm 可互動）
    → 關掉分頁（ttyd 因 -q 自退）→ 終止 session → 列表消失 → 登出

bash entrypoint（零 token）。需要 docker + ttyd + dev-container 的 image + playwright。
    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with playwright python tests/e2e_flow.py
（首次需 `uv run --with playwright playwright install chromium`）
"""
import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

for v in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["CLAUDE_PTY_IMAGE"] = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
os.environ["CLAUDE_PTY_ENTRYPOINT"] = "bash"
os.environ["CLAUDE_PTY_COMMAND"] = ""
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
# 打上測試標記：正式 reconciler 據此跳過這些容器。沒有它的話，測試容器帶 session label
# 卻不在正式 DB 裡，會被正式 reconciler 當孤兒收掉（ORPHAN_GRACE 之後）。
os.environ["CLAUDE_PTY_TEST_MARK"] = "1"
os.environ["CLAUDE_PTY_SECRET_KEY"] = "e2e-secret-key"

_tmp = tempfile.mkdtemp(prefix="claude-pty-e2e-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'e2e.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from server import config, db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()
db.init_db()

from server import auth  # noqa: E402
from server.app import app  # noqa: E402
from server.sessions import SessionManager  # noqa: E402

D = docker.from_env()
# 基準：測試開始前就存在的 session container（正式 stack 可能同時在跑）。
# 「無殘留」只該計算本次測試建立的，否則會誤報。
_PRE_EXISTING = {c.name for c in D.containers.list(all=True, filters=config.SESSION_FILTERS)}


def _leftovers():
    return [c for c in D.containers.list(all=True, filters=config.SESSION_FILTERS)
            if c.name not in _PRE_EXISTING]
_fails = 0
def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
_e2e_user = auth.create_user("e2e-user", "e2e-password-1", is_admin=True)
# 憑證守門在 create() 入口（D 階段起）：這個人要能開 session 就得先有 token。
# 本測試驗端到端流程，不是憑證，種一個測試值即可。
auth.set_cli_token(_e2e_user["id"], "sk-test-setup-token")

# 在背景緒跑 Flask（werkzeug 開發伺服器足夠；E2E 驗的是流程不是效能）
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

mgr = SessionManager()
sid = None

try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 860})
        page = ctx.new_page()

        print("== 未登入時被導向登入頁 ==")
        page.goto(f"{BASE}/", wait_until="domcontentloaded")
        check("導向 /login", page.url.endswith("/login"))
        # ⚠ 這條原本比對 "claude"，但產品早就改名成 agent-tty（內部識別字仍是 claude-pty）
        #   ——斷言沒跟著改，於是這支 e2e 一直是紅的。改成比對現在真正的品牌字樣。
        check("登入頁顯示品牌", "agent-tty" in page.inner_text(".gate__mark").lower())

        print("== 錯誤帳密顯示錯誤訊息、不放行 ==")
        page.fill("#username", "e2e-user")
        page.fill("#password", "wrong-password")
        page.click("button[type=submit]")
        page.wait_for_selector("#login-error:not([hidden])", timeout=5000)
        check("顯示錯誤提示", "錯誤" in page.inner_text("#login-error"))
        check("仍停在登入頁", page.url.endswith("/login"))

        print("== 正確帳密登入 ==")
        page.fill("#password", "e2e-password-1")
        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE}/", timeout=8000)
        check("進入控制台", page.url.rstrip("/") == BASE)
        # 用 text_content 而非 inner_text：後者會套用 CSS text-transform（.label 是
        # uppercase），拿到的會是 E2E-USER
        check("顯示登入者", "e2e-user" in page.text_content(".masthead"))

        print("== 建立 session（走真 docker）==")
        page.wait_for_selector("#manifest .empty, #manifest .manifest__row", timeout=8000)
        # 明確選 unrestricted：表單預設是 restricted（安全預設），但那需要 NET_ADMIN +
        # gitlab-proxy network——E2E 驗的是 UI 流程，
        # firewall 本身另有 live 驗證，不該讓它拖慢並增加環境相依。
        # 網路能力是二元的，用開關而非下拉：點一下即從 restricted 切到 unrestricted
        page.click("#pick-network .switch__control")
        check("開關切換後 aria-checked 為 true",
              page.get_attribute("#pick-network .switch__control", "aria-checked") == "true")
        check("開關的標籤反映新狀態",
              "完全開放" in page.text_content("#pick-network .switch__label"))
        page.click("#create-btn")
        page.wait_for_selector(".manifest__row", timeout=60000)
        # 表頭也掛 .manifest__row（為了與資料列共用 grid 定義），數資料列要排除它
        rows = page.query_selector_all(".manifest__row:not(.manifest__row--head)")
        check("列表出現一筆 session", len(rows) == 1)
        check("列表有表頭", page.query_selector(".manifest__row--head") is not None)
        sid = page.inner_text(".manifest__id").strip()
        check("session id 顯示為 12 位 hex", len(sid) == 12 and all(c in "0123456789abcdef" for c in sid))
        # 這裡跑的是 bash entrypoint，不會印 DRIVER_MARKER（那是 entrypoint.sh 要進 CLI
        # 前才印的），所以 UI 應該誠實顯示「啟動中」——container 在跑 ≠ CLI 已經可用。
        check("driver 未就緒時燈號為啟動中（container 在跑 ≠ CLI 可用）",
              page.get_attribute(".lamp", "data-state") == "creating")
        check("狀態欄標示啟動中", "啟動中" in page.text_content(".manifest__status"))
        check("container 實際存在", D.containers.get(f"claude-pty-{sid}").status == "running")
        check("顯示 profile chips", len(page.query_selector_all(".chip")) >= 2)

        print("== 開啟終端：新分頁載入 ttyd 且可互動 ==")
        with ctx.expect_page(timeout=30000) as tab_info:
            page.click('button[data-act="open"]')
        term = tab_info.value
        term.wait_for_load_state("domcontentloaded")
        term.wait_for_selector(".xterm-screen, canvas", timeout=20000)
        check("終端分頁載入 xterm", term.query_selector(".xterm-screen, canvas") is not None)
        term.click("body")
        term.keyboard.type("echo E2E-TERMINAL-OK")
        term.keyboard.press("Enter")
        # xterm.js 以 canvas 算繪，畫面文字不在 DOM 裡，讀 DOM 驗不到。改從伺服器端確認
        # 按鍵確實抵達 container——這反而是更強的證明：涵蓋 瀏覽器→ttyd→docker attach→PTY
        # 全程，而非只看畫面。
        container = D.containers.get(f"claude-pty-{sid}")
        found = False
        for _ in range(40):
            if b"E2E-TERMINAL-OK" in container.logs(tail=40):
                found = True
                break
            time.sleep(0.25)
        check("瀏覽器按鍵抵達容器 TTY（全程往返）", found)

        print("== 關掉終端分頁 → ttyd 因 -q 自退（DB 記錄被回收）==")
        from server import views as views_mod
        live = views_mod.list_views(sid)
        check("關閉前有存活的 view", len(live) == 1)
        pid = live[0]["pid"]
        term.close()
        gone = False
        for _ in range(40):
            if not views_mod._process_alive(pid):
                gone = True
                break
            time.sleep(0.25)
        check("關分頁後 ttyd 自行退出", gone)
        check("view 記錄已清、port 釋放", views_mod.list_views(sid) == [])
        check("session 本體不受影響（container 仍在）",
              D.containers.get(f"claude-pty-{sid}").status == "running")

        print("== 切換主題：JSON 套用到 CSS 變數 ==")
        before = page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--color-accent').trim()")
        page.click("#theme-picker .picker__button")
        page.click('#theme-picker .picker__option[data-value="daylight"]')
        page.wait_for_timeout(500)
        after = page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--color-accent').trim()")
        check(f"強調色由 {before} 變為 {after}", before != after and after)
        page.click("#theme-picker .picker__button")
        page.click('#theme-picker .picker__option[data-value="instrument"]')

        print("== 帳號頁：改密碼 ==")
        # 先把「改密碼之前」的登入狀態複製到另一個 context——那就是「另一台裝置」。
        # ⚠ 一定要在改密碼**之前**存，改完之後這一張已經被伺服器續期，拿它去驗撤銷
        #   會驗到一個永遠會過的東西（那正是這條斷言曾經在守代理指標的原因）。
        ctx_stale = browser.new_context(storage_state=ctx.storage_state())
        page.click('a[href="/account"]')
        page.wait_for_selector("#pw-form", timeout=8000)
        page.fill("#old-pw", "e2e-password-1")
        page.fill("#new-pw", "e2e-password-2")
        # 兩次輸入不一致時按鈕必須是關的——不然「重複確認」等於沒做
        page.fill("#confirm-pw", "e2e-password-X")
        check("確認密碼不符 → 送出鈕停用", page.is_disabled("#pw-btn"))
        check("並提示不一致", "不一致" in page.text_content("#pw-hint"))
        page.fill("#confirm-pw", "e2e-password-2")
        check("兩次一致 → 送出鈕啟用", page.is_enabled("#pw-btn"))
        # 帳號清單要趁改密碼**之前**查——改完會跳轉登入頁，跳轉後這頁就不在了
        check("管理員看得到帳號清單", page.query_selector("#roster-body") is not None)
        page.click("#pw-btn")
        # 操作結果以右上角 toast 呈現。改密碼的成功文案是「請重新登入」——因為 D 階段起
        # 改密碼＝這個帳號連著的東西全部斷掉，前端跟著在 1200ms 後送回登入頁。
        page.wait_for_selector(".toast[data-level='success']", timeout=8000)
        check("顯示成功 toast（文案叫人重新登入）",
              "請重新登入" in page.inner_text(".toast__title"))
        check("toast 有倒數進度條（不需使用者手動關）",
              page.query_selector(".toast__bar") is not None)
        pw_ok = False
        try:
            auth.authenticate("e2e-user", "e2e-password-2")
            pw_ok = True
        except auth.AuthError:
            pass
        check("新密碼實際生效", pw_ok)

        print("== 🔴 改密碼＝這個帳號連著的東西全部斷掉，包含操作中的這一台 ==")
        # D 階段起**沒有「這一台除外」的特例**。改密碼後本機也被登出、送回登入頁；
        # 改密碼前複製走的舊 cookie 當然也失效。兩邊都驗——別台用改密碼**前**存下的
        # cookie（當下這張已被清掉，拿它驗會驗到一個必然失效的東西，那是代理指標）。
        page.wait_for_url(f"{BASE}/login", timeout=8000)
        check("🔴 操作中的這一台也被送回登入頁（不留特例）", page.url.endswith("/login"))
        stale = ctx_stale.new_page()
        stale.goto(f"{BASE}/", wait_until="domcontentloaded")
        check("改密碼前複製走的舊 cookie 也失效（被導回登入頁）", stale.url.endswith("/login"))
        stale.close()
        ctx_stale.close()

        print("== 終止 session（先用新密碼重新登入——上一步把這台也登出了）==")
        page.fill("#username", "e2e-user")
        page.fill("#password", "e2e-password-2")
        page.click("#login-btn")
        page.wait_for_selector(".manifest__row", timeout=8000)
        page.click('button[data-act="kill"]')
        page.wait_for_selector(".modal", timeout=5000)
        check("終止採用自訂對話框（非原生 confirm）",
              "終止 Session" in page.text_content(".modal__title"))
        page.click('.modal button[data-act="ok"]')
        page.wait_for_selector("#manifest .empty", timeout=30000)
        check("列表已清空", page.query_selector(".manifest__row") is None)
        removed = False
        try:
            D.containers.get(f"claude-pty-{sid}")
        except docker.errors.NotFound:
            removed = True
        check("container 已移除", removed)
        sid = None

        print("== 登出 ==")
        # 登出收進身分下拉了（原本是招牌上一顆常駐按鈕）
        page.click("#account-btn")
        page.wait_for_selector('[data-testid="menu-logout"]', state="visible", timeout=4000)
        page.click('[data-testid="menu-logout"]')
        page.wait_for_url(f"{BASE}/login", timeout=8000)
        check("回到登入頁", page.url.endswith("/login"))
        page.goto(f"{BASE}/", wait_until="domcontentloaded")
        check("登出後無法再進控制台", page.url.endswith("/login"))

        browser.close()

finally:
    print("== 清理 ==")
    with __import__("contextlib").suppress(Exception):
        for x in mgr.list():
            mgr.terminate(x["id"])
    leftover = _leftovers()
    check("測試結束無殘留 container", len(leftover) == 0)
    stray = subprocess.run(["pgrep", "-f", "ttyd -p 41"], capture_output=True, text=True)
    check("測試結束無殘留 ttyd", not stray.stdout.strip())
    db.reset_engine()
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
