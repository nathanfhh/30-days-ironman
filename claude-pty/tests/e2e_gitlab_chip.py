"""E2E：session 列表的 GitLab 標記要讀兩個事實（真瀏覽器，不需 docker）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography --with playwright \
        python tests/e2e_gitlab_chip.py
（首次需 `uv run --with playwright playwright install chromium`）

ADR 0016 花最多篇幅在防的不是「有沒有畫」，是**畫錯**。三種畫錯各自的後果不同，所以
三條都要釘：

  🔴 `gitlab_proxy=true` 但擁有者**現在沒有 token** → 必須是 **warn**，不是 accent。
     寫成 accent 是最自然的錯（「當初有接上啊」），而它的後果正是使用者清掉 token 之後
     畫面一直說「本場可用」、git 卻全部失敗——那比不顯示更糟。
  🔴 `gitlab_proxy=null`（欄位上線前的舊列）→ **一顆都不畫**。畫成暗燈等於謊稱
     「確定未啟用」，而事實是「不知道」。
  🔴 **部署沒開 GitLab**（`CLAUDE_PTY_GITLAB_HOST` 空的，也就是預設）→ 整欄一顆都不畫。
     沒有這道 gate 的話每一列的 `gitlab_proxy` 都是 False，於是所有人都看到一顆灰色
     GitLab 圖示，講一件那台機器上根本不存在的事。

為什麼要真瀏覽器而不是驗 API：這三條全部發生在 `chips()` 裡，而 API 那兩個欄位早就
對了（`sessions._to_dict`）。錯的話 API 測試一條都不會紅。

⚠ 這支**不碰 docker、也不建真的 session**：直接把列插進暫存 DB。列表路徑本來就完全不
  問 dockerd（ADR 0012），所以這是完整的覆蓋，不是抄捷徑。
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

TMP = tempfile.mkdtemp(prefix="e2e-gitlab-chip-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "e2e-gitlab-chip-secret"
# ⚠ 這一行就是「這套部署有沒有開 GitLab」。`gitlab_enabled()` 是每次呼叫才讀，所以測到
#   一半改它就能驗那道 gate（見最後一段）。
config.GITLAB_HOST = "gitlab.example.com"

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
# 看這一頁的人是 admin：他看得到所有人的 session，一頁就湊得齊四種狀態。
admin = auth.create_user("e2e-gl-admin", "e2e-password-1", is_admin=True)
other = auth.create_user("e2e-gl-other", "e2e-password-2")
# admin 有 token、other 沒有——「當初接上了但現在沒 token」那一格要靠 other 才做得出來。
auth.set_gitlab_pat(admin["id"], "glpat-e2e-fake-token-value")

now = utcnow()
# created_at 遞減，列表照 created_at DESC 排，所以順序就是下面這個順序。
ROWS = [
    # (sid, owner, gitlab_proxy, 期望 tone)
    ("g1", admin["id"], True, "accent"),  # 接上了 + 有 token → 可用
    ("g2", admin["id"], False, "off"),  # 沒接上 → 這場永遠沒有
    ("g3", admin["id"], None, None),  # 欄位上線前的舊列 → 不畫
    ("g4", other["id"], True, "warn"),  # 接上了但擁有者現在沒 token → 路斷了
]
PROFILE = {
    "cli": "claude",
    "network": "restricted",
    "capture": False,
    "telemetry": False,
    "model": "opus",
    "effort": "high",
}
# 歷史那張表：擁有者是**沒有 token 的那個人**，而且已經結束。
# 它要畫成 accent（期間曾啟用），**不可以**是 warn——歷史沒有「現在能不能用」可言，
# 去讀 token 狀態的話這一列會變 warn，而那在時間軸上根本沒有意義。
HIST = [
    ("h1", other["id"], True, "accent"),
    ("h2", other["id"], None, None),
]
with session_scope() as s:
    for i, (sid, uid, proxied, _tone) in enumerate(ROWS, start=1):
        s.add(
            SessionRow(
                id=sid,
                container_name=f"gc{i}",
                user_id=uid,
                workdir="/w",
                profile=dict(PROFILE),
                gitlab_proxy=proxied,
                created_at=now - _dt.timedelta(minutes=i),
                last_active_at=now,
            )
        )
    for i, (sid, uid, proxied, _tone) in enumerate(HIST, start=1):
        s.add(
            SessionHistory(
                session_id=sid,
                container_name=f"gh{i}",
                user_id=uid,
                username="e2e-gl-other",
                profile=dict(PROFILE),
                workdir="/w",
                gitlab_proxy=proxied,
                created_at=now - _dt.timedelta(hours=i + 1),
                last_active_at=now - _dt.timedelta(hours=i),
                ended_at=now - _dt.timedelta(minutes=i),
                ended_reason="terminated",
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
            return [_FakeContainer(f"gc{i}") for i in range(1, len(ROWS) + 1)]


import server.app as app_mod  # noqa: E402

app_mod.manager._docker = _FakeDocker()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
# 見 e2e_chips.py 的說明：關掉 werkzeug 的請求 log，否則真正的失敗訊息會被輪詢埋掉，
# 而且它是關瀏覽器時那串 `I/O operation on closed file` 的來源。
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

# 每一列的 GitLab 標記（沒有就回 null）。
# ⚠ 認的是 **data-kind** 不是 data-tone：tone 是共用的（網路也用 accent、錄製也用 off），
#   拿它當選擇器會撿到隔壁那顆，而且撿錯時斷言照樣可能是綠的。
# ⚠ 也不認圖示 class（原本是 `i.fa-gitlab`）：那是 Font Awesome 的實作細節，換一顆圖示
#   或改用 SVG 就靜靜地一列都撿不到，而「一顆都沒畫」正好是這支測試的其中一種預期結果，
#   於是抓手斷掉會偽裝成綠燈。kind 是 chips() 明講的語意欄位，換圖示不會動到它。
PROBE = """
() => [...document.querySelectorAll('[data-testid=chips-cell]')].map(cell => {
  const el = cell.querySelector('[data-testid=chip-mark][data-kind=gitlab]');
  return el ? { tone: el.dataset.tone || null, tip: el.dataset.tip || null } : null;
})
"""

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.fill('[data-testid="login-username"]', "e2e-gl-admin")
    page.fill('[data-testid="login-password"]', "e2e-password-1")
    page.get_by_role("button", name="進入控制台").click()
    page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=8000)
    page.wait_for_selector('[data-testid="chips-cell"] [data-testid="chip-mark"]', timeout=8000)

    print("== 四種狀態各畫成什麼 ==")
    got = page.evaluate(PROBE)
    check(f"四列都畫出來了（拿到 {len(got)} 列）", len(got) == len(ROWS))
    if len(got) == len(ROWS):
        a, b, c, d = got
        check("接上了 + 有 token → accent（本場可用）", a is not None and a["tone"] == "accent")
        check("沒接上 → off", b is not None and b["tone"] == "off")
        check("🔴 gitlab_proxy=null 一顆都不畫（畫成暗燈＝謊稱「確定未啟用」）", c is None)
        check("🔴 接上了但擁有者現在沒 token → **warn**，不是 accent", d is not None and d["tone"] == "warn")
        # tone 對了還不夠：使用者要看得懂下一步。warn 那顆必須說得出「去填回去」，
        # off 那顆必須說得出「這場救不了、要開新的」——兩者的處置完全不同，
        # 講反了會讓人對著一場永遠不會好的 session 一直填 token。
        check("🔴 warn 的說法指得到下一步（填回 token 會恢復）", d is not None and "填回去" in (d["tip"] or ""))
        check("🔴 off 的說法講明這場救不了（要開新的一場）", b is not None and "開新的一場" in (b["tip"] or ""))
        check("accent 的說法是「本場可用」", a is not None and "本場可用" in (a["tip"] or ""))

    print("== 歷史那張表只讀一個事實（沒有「現在能不能用」可言）==")
    page.click('[data-testid="tab-past"]')
    page.wait_for_function("() => document.querySelectorAll('[data-testid=chips-cell]').length === 2", timeout=8000)
    hist = page.evaluate(PROBE)
    check(f"兩列歷史都畫出來了（拿到 {len(hist)} 列）", len(hist) == len(HIST))
    if len(hist) == len(HIST):
        h_on, h_unknown = hist
        # 🔴 這一列的擁有者**現在沒有 token**。歷史若跟著讀 token 狀態，它會變 warn
        #    ——而「這場結束之後那個人有沒有 token」跟「它當時通不通」毫無關係。
        check(
            "🔴 已結束 + 期間曾啟用 → accent（不因為擁有者現在沒 token 就變 warn）",
            h_on is not None and h_on["tone"] == "accent",
        )
        check("說法是時間軸的（曾啟用），不是「現在可用」", h_on is not None and "期間曾啟用" in (h_on["tip"] or ""))
        check("🔴 舊歷史的 null 一樣一顆都不畫", h_unknown is None)
    page.click('[data-testid="tab-live"]')
    page.wait_for_function("() => document.querySelectorAll('[data-testid=chips-cell]').length === 4", timeout=8000)

    print("== 部署沒開 GitLab：整欄一顆都不畫 ==")
    # ⚠ 這道 gate 在**伺服端**（web.sessions_page 把 gitlab_enabled 給模板），所以要重新
    #   載入整頁才吃得到新值——不是重拉列表 API 就好。
    config.GITLAB_HOST = ""
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector('[data-testid="chips-cell"] [data-testid="chip-mark"]', timeout=8000)
    off = page.evaluate(PROBE)
    check("🔴 功能關掉時一顆 GitLab 標記都沒有（否則每一列都在講一件不存在的事）", all(x is None for x in off))
    # 對照組：其他標記還在。上面那條若因為「整排標記都不見了」而綠燈，等於沒測到東西。
    other_marks = page.evaluate(
        "() => document.querySelectorAll('[data-testid=chips-cell] [data-testid=chip-mark]').length"
    )
    check("🔴 而且不是整排標記都消失了（網路／錄製／telemetry 還在）", other_marks > 0)

    browser.close()

reset_engine()
__import__("shutil").rmtree(TMP, ignore_errors=True)
print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
