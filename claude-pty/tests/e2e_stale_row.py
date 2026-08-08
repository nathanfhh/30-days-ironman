"""E2E：對一列已經過時的 session 動手，畫面要自己跟上（真瀏覽器，不需 docker）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with playwright python tests/e2e_stale_row.py
（首次需 `uv run --with playwright playwright install chromium`）

情境：使用者在終端裡按 Ctrl+D 結束了 CLI，container 跟著退出，但他的**畫面還停在
那之前**。這時他去按那一列的按鈕，後端會回：

  · **409** —— 登錄還在，但 container 已經不在（`app.open_view` 開終端前的探測）
  · **404** —— 對帳器已經把那場歸檔了，登錄本身也沒了

守的性質：
  🔴 兩種都要**自動重拉列表**。不然使用者對著一列已經作古的資料，只能一直按、一直
     收到同樣的錯誤，直到他自己想到要按重新整理——而畫面上沒有任何東西提示他該這麼做。
  🔴 這件事必須做在**共用的 catch**，不是各別 action 各做一份：驗的方式是「按不同的
     按鈕都要有」，這樣日後新增一顆按鈕時漏掉才會被抓到。
  🔴 404 的訊息要說得出「為什麼沒有了」。原本是 `未知 session：<id>`——那對人只說了
     「沒有」，沒說「它結束了、對話還在、可以 /resume 接回來」。
  🟡 其他錯誤（例如 400）**不該**觸發重拉：那不是「這一列過時了」，重拉只是白打一次 API。
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

TMP = tempfile.mkdtemp(prefix="e2e-stale-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "e2e-stale-secret"
# 抽屜只在「走 nginx」的模式下開；直連時前端會改開新分頁，那條路測不到抽屜。
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
with session_scope() as s:
    s.add(SessionRow(id="e1", container_name="ec1", user_id=admin["id"], workdir="/w",
                     profile={"cli": "claude", "network": "restricted",
                              "capture": False, "telemetry": False},
                     created_at=now - _dt.timedelta(minutes=1), last_active_at=now))


class _FakeContainer:
    """列表會去對帳；回「還在」，否則這一列會被歸檔，測試就沒有東西可以按了。"""

    def __init__(self, name):
        self.name, self.status, self.id = name, "running", f"cid-{name}"

    def logs(self, **_kw):
        return b""


class _FakeDocker:
    class containers:
        @staticmethod
        def list(**_kw):
            return [_FakeContainer("ec1")]


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

# 列表請求的計數器。斷言看的是「按下去之後有沒有多一發 GET /api/sessions」——
# 那就是「畫面跟上了」的可觀測定義，比去比對 DOM 內容穩固得多。
lists: list[float] = []


def count_list(route):
    lists.append(time.time())
    route.continue_()


def fail_with(status, payload):
    def handler(route):
        route.fulfill(status=status, content_type="application/json", body=payload)
    return handler


try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/api/sessions?*", count_list)

        page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        page.fill("#username", "e2e-admin")
        page.fill("#password", "e2e-password-1")
        page.click("#login-btn")
        page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=8000)
        page.wait_for_selector('[data-testid="row-open-e1"]', timeout=8000)
        page.wait_for_timeout(400)

        print("== 409（container 已經不在）→ 列表要自己重拉 ==")
        page.route("**/api/sessions/*/view",
                   fail_with(409, '{"error":"這個 session 的 container 已經結束了",'
                                  '"docker_state":"exited"}'))
        before = len(lists)
        page.click('[data-testid="row-open-e1"]')
        page.wait_for_timeout(1200)
        check("🔴 按下開啟之後有重新拉列表（不必等對帳器那 30 秒）", len(lists) > before)
        check("錯誤有講給使用者聽（toast 出現）",
              page.locator(".toast").count() >= 1)

        print("== 404（那場已經被歸檔）→ 同樣要重拉，而且訊息要說得出原因 ==")
        # ⚠ 刻意按**另一顆**按鈕（終止）：重拉如果是寫在「開啟」那一支裡，這裡就會漏掉。
        #   這條守的是「做在共用的 catch」，不是「開啟這顆按鈕有處理」。
        page.route("**/api/sessions/e1", fail_with(404, '{"error":"找不到"}'))
        page.wait_for_timeout(200)
        before = len(lists)
        page.click('[data-act="kill"][data-id="e1"]')
        page.wait_for_selector(".modal", timeout=4000)
        page.click('.modal [data-act="ok"]')
        page.wait_for_timeout(1200)
        check("🔴 換一顆按鈕（終止）也照樣重拉列表", len(lists) > before)

        print("== 後端 404 的說法：不能只說「沒有」 ==")
        # 直接問一次 API（不經前端），驗的是訊息本體。
        # ⚠ 要帶 `X-Requested-With`：沒有 body 的變更請求少了它會被 CSRF 閘門擋成 415
        #   （審查 F-002），那時拿到的是閘門的訊息而不是這裡要驗的 404 文案。前端的
        #   `api()` 無條件送這個標頭，所以這一發是在模仿真實呼叫端，不是在繞過檢查。
        msg = page.evaluate("""async () => {
          const r = await fetch('/api/sessions/does-not-exist-at-all', {
            method: 'DELETE', headers: {'X-Requested-With': 'fetch'}});
          return (await r.json()).error || '';
        }""")
        check("🔴 說得出「可能已經結束」", "結束" in msg)
        check("🔴 說得出對話還在、可以 /resume 接回來", "/resume" in msg)
        check("不洩漏存在性：也講了「可能不屬於你」（兩種情況同一句話）", "不屬於你" in msg)

        print("== 🟡 其他錯誤不要跟著重拉（那不是「這一列過時了」）==")
        page.unroute("**/api/sessions/*/view")
        page.route("**/api/sessions/*/view", fail_with(400, '{"error":"參數怪怪的"}'))
        page.wait_for_timeout(200)
        before = len(lists)
        page.click('[data-testid="row-open-e1"]')
        page.wait_for_timeout(1200)
        check("🟡 400 不觸發重拉（不然每個錯誤都白打一次列表 API）", len(lists) == before)

        browser.close()
finally:
    reset_engine()
    __import__("shutil").rmtree(TMP, ignore_errors=True)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
