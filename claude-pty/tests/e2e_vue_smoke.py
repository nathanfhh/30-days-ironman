"""E2E：登入與 session 清單走一遍真瀏覽器。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography --with playwright python tests/e2e_vue_smoke.py

**不需要 docker、也不需要 ttyd**：資料直接塞進一個暫時的 SQLite，容器狀態用假的 client。

## 這支在守什麼

golden（`golden_check.py`）比的是**畫面長什麼樣**；這一支比的是**點下去會發生什麼事**：
登入的成功與失敗兩條路、清單畫得出來、篩選寫進網址而且清單真的被篩、頁籤換得動且重整
之後還在、登出回得去。golden 對這些是盲的——它錄的是靜止的一幀。

## ⚠ 每一條斷言都只用 `data-testid` 與網址，不碰任何一版的內部實作

那是刻意的。兩版並存期間，同一支腳本把 `CLAUDE_PTY_UI` 換成 `legacy` 再跑一次就是一次
免費的對照，而那次對照當場抓到一個真的不一致（看歷史時建立表單，舊版是 `hidden`、
Vue 版寫成了 `v-if`＝節點整個消失）。

legacy 已於 2026-08-26 拆除，那個對照跑不了了。但**寫法照舊**：只認 testid 與網址的
斷言，下一次換框架時同樣是免費的對照，而綁在實作上的斷言那時只能整批重寫。

⚠ 需要 `server/static/dist/` 已經 build 好（`cd frontend && npm run build`）。
  `run-all.sh` 會先跑前端那幾關再跑這支，所以照著它跑就不必自己 build；沒有 dist 時
  這支會直接說出來，不會用一串看不懂的逾時來表達。
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
# ⚠ 這裡曾經要先設 `CLAUDE_PTY_UI=vue`（兩版並存期間的切換器）。2026-08-26 legacy 拆除
#   之後前端只剩一份，那個環境變數與 `config.UI` 都不存在了。

from server import config  # noqa: E402

# 沒有 build 過就直接講出來。少了這一段，症狀是每一條斷言都以逾時失敗，而畫面上只有一串
# selector，看不出「你少了一個 build」。
_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "static", "dist")
if not os.path.isfile(os.path.join(_DIST, "index.html")):
    print(f"  FAIL  前端還沒 build（{_DIST}/index.html 不存在）")
    print("        跑 `cd frontend && npm ci && npm run build`，或直接用 tests/run-all.sh")
    sys.exit(1)

TMP = tempfile.mkdtemp(prefix="vue-smoke-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "vue-smoke-secret"
# 抽屜只在「走 nginx」的模式下開；直連時呼叫端會改開新分頁（見 SessionsView.onOpen）。
config.BEHIND_PROXY = True

from playwright.sync_api import sync_playwright  # noqa: E402

from server import auth  # noqa: E402
from server.app import app  # noqa: E402
from server.db import init_db, reset_engine, session_scope  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402
from server.models import SessionHistory  # noqa: E402
from server.sessions import utcnow  # noqa: E402

_fails = 0
_checks = []


def check(label, ok):
    global _fails
    _checks.append(label)
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


reset_engine()
init_db()
admin = auth.create_user("vue-admin", "vue-password-1", is_admin=True)
now = utcnow()


def profile(**kw):
    base = {
        "cli": "claude",
        "network": "restricted",
        "capture": False,
        "telemetry": False,
        "model": "opus",
        "effort": "high",
    }
    base.update(kw)
    return base


with session_scope() as s:
    for i, (prof, age) in enumerate([(profile(), 0), (profile(capture=True, network="unrestricted"), 3)], start=1):
        s.add(
            SessionRow(
                id=f"v{i}",
                container_name=f"vc{i}",
                user_id=admin["id"],
                workdir="/w",
                profile=prof,
                created_at=now - _dt.timedelta(days=age),
                last_active_at=now,
            )
        )
    s.add(
        SessionHistory(
            session_id="vh1",
            container_name="vhc1",
            user_id=admin["id"],
            username=admin["username"],
            profile=profile(),
            workdir="/w",
            created_at=now - _dt.timedelta(days=2),
            last_active_at=now - _dt.timedelta(days=2),
            ended_at=now - _dt.timedelta(hours=6),
            ended_reason="exited",
        )
    )


class _FakeContainer:
    def __init__(self, name):
        self.name, self.status, self.id = name, "running", f"cid-{name}"

    def logs(self, **_kw):
        return b""


class _FakeDocker:
    class containers:
        @staticmethod
        def list(**_kw):
            return [_FakeContainer(f"vc{i}") for i in (1, 2)]


import server.app as app_mod  # noqa: E402

app_mod.manager._docker = _FakeDocker()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
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

print(f"== Vue 版煙霧測試（{BASE}）==")

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    # ⚠ 只收**真的 JS 例外**（pageerror）。console.error 會收到「fetch 回了 401/400」那種
    #   瀏覽器自己印的訊息，而那兩發正是這支測試刻意製造的（未登入進站、故意打錯密碼）。
    page.on("pageerror", lambda e: errors.append(str(e)))
    console_errors = []
    page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)

    # --- 未登入進 / 要被導回 /login ---
    page.goto(f"{BASE}/", wait_until="networkidle")
    check("未登入進 / 被導回登入頁", page.url.endswith("/login"))
    check("登入頁是 SPA 畫的（brand-mark 在）", page.locator('[data-testid="brand-mark"]').count() == 1)
    check("送出鈕一開始是停用的", page.locator("#login-btn").is_disabled())

    # --- 錯密碼 ---
    page.fill('[data-testid="login-username"]', "vue-admin")
    page.fill('[data-testid="login-password"]', "wrong-password")
    page.get_by_role("button", name="進入控制台").click()
    page.wait_for_selector('[data-testid="login-error"]:not([hidden])', timeout=5000)
    check(
        "錯密碼把後端原文畫在 notice 上",
        "不正確" in page.locator('[data-testid="login-error"]').inner_text()
        or len(page.locator('[data-testid="login-error"]').inner_text()) > 0,
    )
    check("失敗後仍在登入頁", page.url.endswith("/login"))

    # --- 正確密碼 ---
    page.fill('[data-testid="login-password"]', "vue-password-1")
    page.get_by_role("button", name="進入控制台").click()
    page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=8000)
    check("登入成功換到 /", page.url.rstrip("/").endswith(str(PORT)))
    page.wait_for_selector('[data-testid="session-row"]', timeout=8000)

    check("招牌在", page.locator('[data-testid="masthead"]').count() == 1)
    check("身分顯示登入者", "vue-admin" in page.locator('[data-testid="account-btn"]').inner_text())
    check(
        "憑證徽章畫出來了（未設定 → bad）",
        page.locator('[data-testid="cred-badge"]').get_attribute("data-state") == "bad",
    )
    check("兩列 session", page.locator('[data-testid="session-row"]').count() == 2)
    check("表頭在", page.locator('[data-testid="manifest-head"]').count() == 1)
    check("admin 看得到 owner chip", "vue-admin" in page.locator('[data-testid="chips-cell"]').first.inner_text())
    check("建立表單在", page.locator("#create-panel").count() == 1)
    check(
        "模型選單載到後端那一份",
        page.locator('[data-testid="pick-model-button"]').inner_text().strip().startswith("Opus"),
    )

    # --- 篩選 ---
    page.click('[data-testid="filter-toggle"]')
    page.wait_for_selector('[data-testid="filter-bar"]', state="visible")
    check("篩選列展開", page.locator('[data-testid="filter-bar"]').is_visible())
    page.click('[data-testid="pick-fnet-button"]')
    page.click('[data-testid="pick-fnet-opt-unrestricted"]')
    page.wait_for_timeout(600)
    check("條件寫進網址", "network=unrestricted" in page.url)
    check("清單被篩過（剩一列）", page.locator('[data-testid="session-row"]').count() == 1)
    check("生效數掛在按鈕上", "1" in page.locator("#filter-count").inner_text())
    page.click('[data-testid="filter-clear"]')
    page.wait_for_timeout(600)
    check("清除之後網址乾淨", "network=" not in page.url)
    check("清單回到兩列", page.locator('[data-testid="session-row"]').count() == 2)

    # --- 頁籤 ---
    page.click('[data-testid="tab-past"]')
    page.wait_for_timeout(800)
    check("切到已結束：網址記住", "tab=past" in page.url)
    check("已結束有一列", page.locator('[data-testid="session-row"]').count() == 1)
    # 舊版是 `hidden`（節點還在），Vue 版照抄——golden 拿舊版那份來比
    check(
        "建立表單收起來（hidden，節點仍在）",
        page.locator("#create-panel").count() == 1 and page.locator("#create-panel").is_hidden(),
    )
    check("結束原因畫出來", "自行結束" in page.locator('[data-testid="session-status"]').first.inner_text())

    # --- 重新整理維持狀態（網址就是真相）---
    page.reload(wait_until="networkidle")
    page.wait_for_selector('[data-testid="session-row"]', timeout=8000)
    check("重整後仍在已結束那一張", page.locator('[data-testid="tab-past"]').get_attribute("aria-selected") == "true")

    # --- 樣式真的載到（app.css 原檔）---
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    check(f"app.css 有生效（body 背景 {bg} 不是預設白）", bg not in ("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)"))

    # --- 帳號頁的殼 ---
    page.click('[data-seg="account"]')
    page.wait_for_timeout(500)
    check("帳號頁走 SPA 路由（沒有整頁重載）", page.url.endswith("/account"))

    # --- 抽屜（ttyd 用替身，不需要 docker）---
    #
    # ⚠ 這裡**不 import golden_scenes 的 install_drawer_routes**：那支模組一 import 就會建暫時
    #   的 DB、改 config，而這一支自己已經有一份。替身只要做到「window.term 存在」就夠——
    #   尺寸同步的兩道閘本身由 vitest 逐條驗（見 frontend 的 terminal-size.spec.ts）。
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_selector('[data-testid="session-row"]', timeout=8000)
    page.route(
        "**/api/sessions/*/view",
        lambda route, request: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"path": "/session/%s/", "direct_url": "http://127.0.0.1:41999/",'
            ' "ttyd_flavor": "Rust"}' % request.url.rstrip("/").split("/")[-2],
        ),
    )
    page.route(
        "**/session/**",
        lambda route, _r: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body="<!doctype html><meta charset=utf-8><body>"
            "<script>window.term={cols:80,rows:24,options:{fontSize:14},"
            "onResize(cb){this._cb=cb}};</script>",
        ),
    )
    page.route("**/api/sessions/*/resize", lambda route, _r: route.fulfill(status=204, body=""))

    page.locator('[data-act="open"]').first.click()
    page.wait_for_selector('[data-testid="drawer"]', timeout=8000)
    check("抽屜開得起來", page.locator('[data-testid="drawer"]').count() == 1)
    check(
        "iframe 指到單一入口那條路徑（不是跨 origin 的直連網址）",
        "/session/" in (page.locator('[data-testid="drawer-frame"]').get_attribute("src") or ""),
    )
    check("標題列講得出是哪一顆 ttyd", page.locator('[data-testid="drawer-bin"]').inner_text().strip() == "Rust")
    check(
        "背景退出 Tab 序（aria-modal 不影響 Tab 順序，inert 才是）",
        page.evaluate("() => document.querySelector('.shell').inert") is True,
    )
    # ⚠ 用 state="hidden" 而不是選擇器帶 [hidden]：後者的預設是「等它**可見**」，而一個
    #   hidden 的元素永遠不會可見，於是這一行必定逾時（第一次寫就是這樣紅的）。
    page.wait_for_selector('[data-testid="drawer-pending"]', state="hidden", timeout=8000)
    check("連上之後「連線中」收起來", page.locator('[data-testid="drawer-pending"]').is_hidden())
    page.wait_for_function(
        "() => /^\\d+px$/.test(document.querySelector('[data-testid=drawer-font-value]').textContent.trim())",
        timeout=8000,
    )
    check("字級讀得到並畫出來", page.locator('[data-testid="drawer-font-value"]').inner_text().strip().endswith("px"))
    page.click('[data-testid="drawer-close"]')
    page.wait_for_selector('[data-testid="drawer"]', state="detached", timeout=5000)
    check("關掉之後節點真的被拆掉", page.locator('[data-testid="drawer"]').count() == 0)
    check("背景的 inert 也收回來", page.evaluate("() => document.querySelector('.shell').inert") is False)

    # --- 帳號頁（管理員）---
    page.goto(f"{BASE}/account", wait_until="networkidle")
    page.wait_for_selector('[data-testid="roster-table"]', timeout=8000)
    check(
        "帳號頁三塊自己的面板都在",
        page.locator('[data-testid="token-state"]').count() == 1
        and page.locator('[data-testid="pw-form"]').count() == 1,
    )
    check("管理員看得到帳號清單", page.locator('[data-testid="roster-name"]').count() >= 1)
    check("ttyd 實況那張表也在", page.locator('[data-testid="ttyd-views"]').count() == 1)
    check(
        "憑證未設定時 chip 是紅的",
        page.locator('[data-testid="token-state"] .chip').get_attribute("data-tone") == "error",
    )
    check("未設定時清除鍵收起來", page.locator('[data-testid="token-clear"]').is_hidden())
    check("儲存鍵要等貼了東西才可按", page.locator('[data-testid="token-save"]').is_disabled())
    page.fill('[data-testid="cli-token"]', "sk-ant-oat-fake")
    check("貼了就可按", page.locator('[data-testid="token-save"]').is_enabled())
    # 改密碼的即時驗證（長度與一致性）
    page.fill('[data-testid="old-pw"]', "vue-password-1")
    page.fill('[data-testid="new-pw"]', "short")
    check("太短會講出來", "至少" in page.locator('[data-testid="pw-hint"]').inner_text())
    page.fill('[data-testid="new-pw"]', "vue-password-2")
    page.fill('[data-testid="confirm-pw"]', "vue-password-3")
    check("不一致會講出來", "不一致" in page.locator('[data-testid="pw-hint"]').inner_text())
    check("不一致時送出鍵是停用的", page.locator('[data-testid="pw-btn"]').is_disabled())
    page.fill('[data-testid="confirm-pw"]', "vue-password-2")
    check("兩次一致就可按", page.locator('[data-testid="pw-btn"]').is_enabled())
    check("密碼欄都有「看一眼」（舊版是 enhancePasswordFields 包的）", page.locator(".pw__toggle").count() >= 4)

    # --- 登出 ---
    page.click('[data-testid="account-btn"]')
    page.click('[data-testid="menu-logout"]')
    page.wait_for_function("() => location.pathname.startsWith('/login')", timeout=5000)
    check("登出回登入頁", page.url.endswith("/login"))

    check(f"沒有未捕捉的 JS 例外（{errors}）", not errors)
    unexpected = [e for e in console_errors if "401" not in e and "400" not in e]
    check(f"console 只剩預期中的 401/400（{unexpected}）", not unexpected)
    browser.close()

print(f"\n{len(_checks) - _fails} passed, {_fails} failed")
sys.exit(1 if _fails else 0)
