"""Golden master 的共用場景定義：起服務、餵固定資料、把畫面開到指定狀態。

`golden_record.py`（錄）與 `golden_check.py`（比）都 import 這一支，所以「錄的是什麼」
與「比的是什麼」**只有一份定義**。兩邊各寫一份的話，遲早會出現「錄的時候是展開的、
比的時候是收合的」這種差異，而畫面上只會看到一堆看不懂的 diff。

## 這東西在守什麼

Vue 版要 1:1 還原現在這個介面。「1:1」若只靠人看，改到第三十個元件時沒有人記得原本
長什麼樣。所以先把**舊實作當成規格**錄下來（Day 26 特徵測試同一招）：aria 樹回答
「結構與可及名稱有沒有變」，網路序列回答「有沒有多打或少打 API」，截圖回答「看起來
還是不是同一個東西」。

## 不穩定源一律在**錄製端**釘死

放寬閾值是把問題藏起來：閾值放到 5% 之後，真的壞掉 4% 的那次也會是綠的。這裡釘死的
每一項都在下面 `pin_all()` 裡逐條寫明理由。

⚠ 檔名沒有 `test_` 前綴是刻意的：`run-all.sh` 的 glob 撿的是 `tests/test_*.py` 與
  `tests/e2e_*.py`，這支不是測試（它一條斷言都沒有），被撿走只會空跑。
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="golden-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "golden-master-secret"
# 抽屜只在「走 nginx」的模式下開；直連時呼叫端會改開新分頁（見 sessions.html）。
config.BEHIND_PROXY = True
# GitLab 的標記要出現在 chips 裡，這一欄才錄得到。
config.GITLAB_HOST = "gitlab.example.test"

from fake_ttyd import STUB  # noqa: E402

from server import auth, version, web  # noqa: E402
from server.app import app  # noqa: E402
from server.db import init_db, reset_engine, session_scope  # noqa: E402
from server.models import SessionHistory, Session as SessionRow, User  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

# 兩個視口。桌機是主要的比對對象（截圖只存這個），手機用來釘住 media query 之後的結構。
VIEWPORTS = {"1280x800": {"width": 1280, "height": 800}, "390x844": {"width": 390, "height": 844}}
SHOT_VIEWPORT = "1280x800"
# 這幾場在手機視口是**另一套版面**（抽屜在窄視窗下是全螢幕），只錄桌機等於沒錄到。
# 其餘場景的手機版靠 aria 與 dom 兩份快照守結構，不另外存圖（圖很貴，48 張就 8 MB）。
MOBILE_SHOT = {"drawer-open"}

# 整組資料與瀏覽器時鐘共用的「現在」。挑一個寫死的時刻，不是 utcnow()。
NOW = _dt.datetime(2026, 8, 25, 4, 0, 0, tzinfo=_dt.timezone.utc)
PASSWORD = "golden-password-1"


def pin_all() -> None:
    """把每一個會讓兩次錄製不一樣的來源釘死。逐條寫明它是什麼、為什麼會飄。"""
    # 1. 登入頁的插畫是 `random.choice(LOGIN_ART)` 選的，每次載入都可能換一張。
    #    釘成固定那一張（仍然是真的檔案，版面與載入行為都不變）。
    if web.LOGIN_ART:
        web.LOGIN_ART = [web.LOGIN_ART[0]]

    # 2. 頁尾的 build_info。它會去問工作區的 git sha（含 `-dirty`）與 `ttyd --version`，
    #    所以「有沒有未 commit 的檔案」「這台機器裝了哪一版 ttyd」都會改變畫面。
    #    ⚠ 這一項如果不釘，golden 會變成「只有錄它的那台機器、那個當下」才對得起來。
    version.summary = lambda: {
        "modules": [
            {
                "name": "claude-pty",
                "version": "0.0.0-golden",
                "commit": "0000000",
                "built_at": "2026-08-25T04:00:00+00:00",
                "detail": "控制平面本體。",
            },
            {
                "name": "ttyd（Rust）",
                "version": "1.0.0-golden",
                "commit": "1111111",
                "built_at": None,
                "detail": "ttyd-rust：目前用於網頁終端的執行檔。",
            },
        ]
    }

    # 3. 週期性的計時器不跑，見 FREEZE_TIMERS_JS。


class _FakeContainer:
    """list() 會去問 docker 對帳；回「都還在」，否則資料列會被歸檔掉。"""

    def __init__(self, name: str) -> None:
        self.name, self.status, self.id = name, "running", f"cid-{name}"

    def logs(self, **_kw) -> bytes:
        return b""


class _FakeDocker:
    class containers:
        @staticmethod
        def list(**_kw):
            return [_FakeContainer(n) for n in _LIVE_CONTAINERS]


_LIVE_CONTAINERS: list[str] = []

# 一場一列，涵蓋畫面上會出現的每一種樣子。刻意不是「四筆一樣的假資料」：golden 要守的
# 是各種狀態畫得對不對，全部長一樣的話換掉半數渲染邏輯也不會有人紅。
#
#   (sid, 名稱, 幾分鐘前建的, docker 狀態, 就緒?, profile 覆寫, gitlab_proxy)
SESSION_ROWS = [
    ("aa11bb22cc33", "重構登入流程", 3, "running", True, {"network": "unrestricted", "capture": True}, True),
    # 沒取名字：標題退回 sid（等寬字體那一種）。
    ("dd44ee55ff66", None, 12, "running", True, {"telemetry": True, "telemetry_active": True}, False),
    # container 在跑但 driver 沒就緒：燈號是 creating、狀態欄寫「啟動中」。
    ("1122334455aa", "還在開機的那一場", 41, "running", False, {"telemetry": True, "telemetry_active": False}, None),
    # 刻意超長的名字：截斷與 tooltip 那一段的版面。
    (
        "99887766aabb",
        "把整條 CI 的 trivy 快取與 gitlab 代理一起重做並補齊文件",
        180,
        "running",
        True,
        {"model": "sonnet", "effort": "medium"},
        True,
    ),
]

HISTORY_ROWS = [
    ("cafe00112233", "上週那場已經結束的", 26, 24, "terminated"),
    ("beef44556677", None, 50, 49, "exited"),
]

BASE_PROFILE = {
    "cli": "claude",
    "network": "restricted",
    "capture": False,
    "telemetry": False,
    "model": "opus",
    "effort": "high",
}


def seed() -> dict:
    """建帳號與資料。回傳幾個之後場景會用到的 id。

    ⚠ 可以重複呼叫（`--verify` 會在兩次錄製之間重 seed 一次）：先把 sqlite 檔整個刪掉再
      建，不然第二次會撞「使用者已存在」。
    """
    reset_engine()
    db_file = os.path.join(TMP, "t.db")
    if os.path.exists(db_file):
        os.remove(db_file)
    _LIVE_CONTAINERS.clear()
    init_db()
    admin = auth.create_user("golden-admin", PASSWORD, is_admin=True)
    plain = auth.create_user("golden-user", PASSWORD)
    # 空清單那一場要一個什麼都沒有的人，不能拿 admin 去做：admin 看得到所有人的 session。
    empty = auth.create_user("golden-empty", PASSWORD)
    auth.set_gitlab_pat(admin["id"], "glpat-golden-fixed-token")

    with session_scope() as s:
        # ⚠ `users.created_at` 由 `auth.create_user()` 用**真實的現在**填，而帳號清單那一欄
        #   會把它畫出來（絕對時間 ＋ 相對時間）。不釘的話 golden 只在錄它的那一分鐘內
        #   對得起來，而且相對時間會是負的（瀏覽器的時鐘釘在 NOW，比 seed 的時刻早）。
        # ⚠ 這一條是 `--verify` **抓不到**的那一類：兩次錄製在同一個行程裡共用同一次 seed，
        #   值一樣所以看起來很穩。它是被跨行程跑的 golden_check 抓出來的（2026-08-25）。
        #   `--verify` 因此改成兩次之間重新 seed 一次，讓這一類當場現形。
        for u in s.query(User).all():
            u.created_at = NOW - _dt.timedelta(days=3)
        for i, (sid, name, mins, state, ready, extra, proxied) in enumerate(SESSION_ROWS, start=1):
            created = NOW - _dt.timedelta(minutes=mins)
            profile = dict(BASE_PROFILE)
            profile.update(extra)
            _LIVE_CONTAINERS.append(f"claude-pty-{sid}")
            s.add(
                SessionRow(
                    id=sid,
                    container_id=f"cid-{i}",
                    container_name=f"claude-pty-{sid}",
                    user_id=admin["id"],
                    display_name=name,
                    workdir="/w",
                    profile=profile,
                    status=state,
                    gitlab_proxy=proxied,
                    created_at=created,
                    last_active_at=created + _dt.timedelta(minutes=1),
                    # 就緒的那幾場給一個固定的啟動耗時，「啟動」那一欄才有東西可畫。
                    ready_at=created + _dt.timedelta(seconds=4) if ready else None,
                    docker_state=state,
                    # 新鮮度：兩分鐘內是新的，超過會自己標紅。兩種都要錄到。
                    state_checked_at=NOW - _dt.timedelta(seconds=20 if i < 3 else 400),
                    cli_version="1.2.3",
                )
            )
        for i, (sid, name, mins_created, mins_ended, reason) in enumerate(HISTORY_ROWS, start=1):
            created = NOW - _dt.timedelta(hours=mins_created)
            s.add(
                SessionHistory(
                    session_id=sid,
                    container_name=f"claude-pty-{sid}",
                    user_id=admin["id"],
                    username="golden-admin",
                    display_name=name,
                    profile=dict(BASE_PROFILE),
                    workdir="/w",
                    gitlab_proxy=True,
                    created_at=created,
                    last_active_at=created + _dt.timedelta(hours=1),
                    ended_at=NOW - _dt.timedelta(hours=mins_ended),
                    ended_reason=reason,
                )
            )
    return {"admin": admin, "plain": plain, "empty": empty}


def start_server() -> str:
    """把 Flask 起在一條 daemon thread 上，回傳 base URL。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # werkzeug 的請求 log 關掉，理由同 e2e_*.py 檔頭那段（輪詢會把真正的訊息埋掉，
    # 而且 daemon thread 在 exit 時還在寫會噴一串紅字）。
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    import server.app as app_mod

    app_mod.manager._docker = _FakeDocker()
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False),
        daemon=True,
    ).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    return base


# ── ttyd 與 view 的替身（抽屜那一場要用）─────────────────────────────────────
def install_drawer_routes(page) -> None:
    page.route(
        "**/api/sessions/*/view",
        lambda route, request: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"path": "/session/%s/", "direct_url": "http://127.0.0.1:41999/",'
            ' "ttyd_bin": "ttyd-rust", "ttyd_flavor": "Rust"}' % request.url.rstrip("/").split("/")[-2],
        ),
    )
    page.route(
        "**/session/**",
        lambda route, _r: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            # 起始畫布倍率就給對的，畫面不會有「先錯一下再被修好」的中間態。
            body=STUB.replace("__SCALE__", "window.devicePixelRatio").replace("__FONT_AFTER__", ""),
        ),
    )
    page.route("**/api/sessions/*/resize", lambda route, _r: route.fulfill(status=204, body=""))


# ── 場景 ─────────────────────────────────────────────────────────────────────
#
# 每一個都是 (名字, 說明, 函式)。函式收到 page 與 base，負責把畫面開到那個狀態並在
# **完全靜止**時返回。不可以在裡面做斷言，這一支不是測試。


def _login(page, base: str, username: str) -> None:
    page.goto(f"{base}/login", wait_until="domcontentloaded")
    page.fill('[data-testid="login-username"]', username)
    page.fill('[data-testid="login-password"]', PASSWORD)
    page.get_by_role("button", name="進入控制台").click()
    page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=8000)
    # ⚠ 進度條的 animation **同時是倒數計時器**（見 app.css 的 toast-countdown）。錄製時
    #   把它停掉，toast 才停得在一個確定的狀態；不停的話錄到的是「進度條剛好走到某個
    #   百分比」，那個百分比每次都不一樣。停掉之後它也不會自己關（animationend 不來），
    #   所以下面 _settle 要負責把不該入鏡的那些清掉。
    page.add_style_tag(content=".toast__bar { animation: none !important; }")


def _settle(page, keep_toasts: bool = False) -> None:
    """等到畫面真的不動了。

    ⚠ 不是睡一個固定秒數。列表是非同步畫的、picker 要等 /api/prefs、字體要載入完才排得對，
      而這幾件事在不同機器上快慢不同。等「沒有網路活動」加「字體就緒」才是等到事實。
    ⚠ 登入成功會彈一則「歡迎回來」（LoginView 的 `toast()`），也就是**每一個登入後的
      場景**右上角都會有它，五秒後自己消失。錄到它等於把一個過場錄成規格，所以除了
      toast 那一場之外一律清掉。清的時機在 networkidle **之後**：太早清的話它還沒出現。
    """
    page.wait_for_load_state("networkidle")
    # ⚠ 字型是**按需**抓的：瀏覽器只有在真的要畫到某個字面時才去要那一份 woff2，而「那一刻
    #   有沒有在快照之前發生」會飄。實際踩到過（2026-08-26）：`account-admin` 第一次錄有
    #   `fa-brands-400.woff2`、第二次沒有，而 `--verify` 是唯一抓得到的地方（跨行程那次
    #   剛好兩邊都有）。
    # ⚠ 所以把需求**講明**：叫每一個宣告過的 font face 都載入，再等 `fonts.ready`。
    #   代價是每一場都會抓齊全部字型，於是「這一場用到了 brands」這個訊號變成「這一頁宣告了
    #   brands」——那個損失是可以接受的，因為「畫面上真的有那顆圖示」本來就由截圖與 aria 守著，
    #   而一個會隨機紅的 golden 最後會被整支關掉。
    page.evaluate(
        "async () => { if (!document.fonts) return;"
        "  await Promise.all([...document.fonts].map((f) => f.load().catch(() => {})));"
        "  await document.fonts.ready; }"
    )
    # 上面那幾發要求也算網路活動，等它們落地再取樣。
    page.wait_for_load_state("networkidle")
    if not keep_toasts:
        page.evaluate("() => document.querySelectorAll('[data-testid=toast]').forEach((t) => t.remove())")
    page.wait_for_timeout(150)


def scene_login_empty(page, base):
    page.goto(f"{base}/login", wait_until="domcontentloaded")
    _settle(page)


def scene_login_error(page, base):
    page.goto(f"{base}/login", wait_until="domcontentloaded")
    page.fill('[data-testid="login-username"]', "golden-user")
    page.fill('[data-testid="login-password"]', "definitely-wrong")
    page.get_by_role("button", name="進入控制台").click()
    page.locator('[data-testid="login-error"]').wait_for(state="visible", timeout=5000)
    _settle(page)


def scene_sessions_empty(page, base):
    _login(page, base, "golden-empty")
    page.locator('[data-testid="manifest-empty"]').wait_for(timeout=8000)
    _settle(page)


def scene_sessions_list(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    _settle(page)


def scene_sessions_history(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    page.click('[data-testid="tab-past"]')
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    _settle(page)


def scene_sessions_filters(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    page.click('[data-testid="filter-toggle"]')
    page.locator('[data-testid="filter-bar"]').wait_for(state="visible", timeout=4000)
    _settle(page)


def scene_sessions_rangepick(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    page.click('[data-testid="filter-toggle"]')
    page.locator('[data-testid="filter-bar"]').wait_for(state="visible", timeout=4000)
    page.click('[data-testid="pick-since-button"]')
    page.click('[data-testid="pick-since-opt-custom"]')
    page.locator('[data-testid="range-trigger"]').wait_for(state="visible", timeout=4000)
    page.click('[data-testid="range-trigger"]')
    page.locator('[data-testid="range-panel"]').wait_for(state="visible", timeout=4000)
    _settle(page)


def scene_sessions_settings(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    page.click('[data-testid="account-btn"]')
    page.click('[data-testid="menu-settings"]')
    page.locator('[data-testid="settings-modal"]').wait_for(timeout=4000)
    # picker 要等 /api/prefs 回來才建，先等它在，否則錄到的是一個空的掛載點。
    page.locator('[data-testid="pick-ttyd-button"]').wait_for(state="visible", timeout=4000)
    _settle(page)


def scene_sessions_toast(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    # 先把登入那則「歡迎回來」清掉：錄的是 toast 這個元件長什麼樣，不是「剛好還沒消失
    # 的那則過場」。
    page.evaluate("() => document.querySelectorAll('[data-testid=toast]').forEach((t) => t.remove())")
    # ⚠ 用**真的 UI 動作**把 toast 叫出來，不是 `page.evaluate` 去呼叫全域的 `toast()`。
    #   呼叫全域函式等於把場景綁在 legacy 的實作上：Vue 版沒有那個全域，這一場會在
    #   「還沒開始比」的地方就炸掉，而炸掉的原因與介面像不像一點關係都沒有。
    #   終止 → 取消是使用者真的走得到的一條路，兩版都必須走得通。
    page.click(f'button[data-act="kill"][data-id="{SESSION_ROWS[0][0]}"]')
    page.locator('[data-testid="modal"]').wait_for(timeout=4000)
    page.click('[data-testid="modal"] [data-act="cancel"]')
    page.locator('[data-testid="toast"]').wait_for(state="visible", timeout=4000)
    _settle(page, keep_toasts=True)


def scene_drawer_open(page, base):
    install_drawer_routes(page)
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    page.click(f'[data-testid="row-open-{SESSION_ROWS[0][0]}"]')
    page.locator('[data-testid="drawer"]').wait_for(timeout=8000)
    page.locator('[data-testid="drawer-pending"]').wait_for(state="hidden", timeout=8000)
    # 抽屜的提示是輪播（每幾秒換一條），停在第一條才錄得穩。
    page.evaluate(
        "() => document.querySelectorAll('[data-testid=drawer-hints] [data-testid]')"
        ".forEach((h, i) => { h.dataset.on = String(i === 0); h.inert = i !== 0; })"
    )
    _settle(page)


def scene_account_user(page, base):
    _login(page, base, "golden-user")
    page.goto(f"{base}/account", wait_until="domcontentloaded")
    page.locator('[data-testid="pw-form"]').wait_for(timeout=8000)
    _settle(page)


def scene_account_admin(page, base):
    _login(page, base, "golden-admin")
    page.goto(f"{base}/account", wait_until="domcontentloaded")
    page.locator('[data-testid="roster"]').wait_for(timeout=8000)
    # roster 是 JS 拉回來畫的，等到真的有一列才算就緒。
    page.wait_for_function(
        "() => document.querySelectorAll('[data-testid=roster] [data-testid=roster-name]').length > 0",
        timeout=8000,
    )
    _settle(page)


def scene_sessions_filter_applied(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    page.click('[data-testid="filter-toggle"]')
    page.locator('[data-testid="filter-bar"]').wait_for(state="visible", timeout=4000)
    page.click('[data-testid="pick-fnet-button"]')
    page.click('[data-testid="pick-fnet-opt-unrestricted"]')
    # 條件生效之後清單會重畫。等「只剩一列」而不是睡一段時間：那才是這一場的定義。
    page.wait_for_function("() => document.querySelectorAll('[data-testid=session-row]').length === 1", timeout=8000)
    _settle(page)


def scene_sessions_toast_error(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    page.evaluate("() => document.querySelectorAll('[data-testid=toast]').forEach((t) => t.remove())")
    # 同上：走真的 UI 動作。讓 DELETE 回 409，再真的按下終止並確認，錯誤 toast 就是
    # 使用者會看到的那一則（前端的錯誤處理自己拼的，不是我們餵進去的字串）。
    page.route(
        "**/api/sessions/*",
        lambda route, request: (
            route.fulfill(
                status=409,
                content_type="application/json",
                body='{"error":"這個 session 的 container 已經結束了"}',
            )
            if request.method == "DELETE"
            else route.fallback()
        ),
    )
    page.click(f'button[data-act="kill"][data-id="{SESSION_ROWS[0][0]}"]')
    page.locator('[data-testid="modal"]').wait_for(timeout=4000)
    page.click('[data-testid="modal"] [data-act="ok"]')
    page.locator('[data-testid="toast"][data-level="danger"]').wait_for(state="visible", timeout=4000)
    _settle(page, keep_toasts=True)


def scene_sessions_modal_kill(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    page.click(f'button[data-act="kill"][data-id="{SESSION_ROWS[0][0]}"]')
    page.locator('[data-testid="modal"]').wait_for(timeout=4000)
    _settle(page)


def scene_sessions_modal_rename(page, base):
    _login(page, base, "golden-admin")
    page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
    # ⚠ 重新命名**不是** inline 編輯，是一個帶輸入框的對話框（app.js 的 dialog({input})）。
    #   場景名字照實叫 modal，不要照著想像命名——golden 記的是現況，不是我們以為的現況。
    page.click(f'button[data-act="rename"][data-id="{SESSION_ROWS[0][0]}"]')
    page.locator('[data-testid="modal"]').wait_for(timeout=4000)
    _settle(page)


def scene_sessions_pager(page, base):
    """一頁裝不下時的分頁列。"""
    # ⚠ 把 PAGE_SIZE 調小，而不是多塞十幾筆假資料進 seed：多塞的話**每一個**場景的清單
    #   都跟著變長，截圖全部要重錄，而那十幾筆對其他場景一點資訊都沒有。
    # ⚠ try/finally：中途拋了也要還原，否則後面每一場都會用到一個被改過的分頁大小，
    #   而那種汙染在 golden 裡的樣子是「不相干的場景莫名其妙全紅」。
    real = config.PAGE_SIZE
    config.PAGE_SIZE = 2
    try:
        _login(page, base, "golden-admin")
        page.locator('[data-testid="pager-status"]').wait_for(timeout=8000)
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=session-row]').length === 2", timeout=8000
        )
        _settle(page)
    finally:
        config.PAGE_SIZE = real


def scene_sessions_no_gitlab(page, base):
    """部署沒開 GitLab：整欄標記一顆都不畫。"""
    # 這道 gate 在伺服端（web.sessions_page 把 gitlab_enabled 給模板），所以要在載入頁面
    # **之前**改，而且改完整頁重載才吃得到。
    real = config.GITLAB_HOST
    config.GITLAB_HOST = ""
    try:
        _login(page, base, "golden-admin")
        page.locator('[data-testid="session-row"]').first.wait_for(timeout=8000)
        _settle(page)
    finally:
        config.GITLAB_HOST = real


SCENES = [
    ("login-empty", "登入頁，什麼都還沒填", scene_login_empty),
    ("login-error", "登入頁，帳密錯誤的提示", scene_login_error),
    ("sessions-empty", "工作階段，清單是空的", scene_sessions_empty),
    ("sessions-list", "工作階段，多筆含各種 chips 與狀態", scene_sessions_list),
    ("sessions-history", "工作階段，已結束那張表", scene_sessions_history),
    ("sessions-filters", "工作階段，篩選列展開", scene_sessions_filters),
    ("sessions-rangepick", "工作階段，起迄的日期面板開著", scene_sessions_rangepick),
    ("sessions-settings", "工作階段，設定對話框開著", scene_sessions_settings),
    ("sessions-toast", "工作階段，右上角有一則 toast", scene_sessions_toast),
    ("sessions-filter-applied", "工作階段，套用了一個篩選條件", scene_sessions_filter_applied),
    ("sessions-toast-error", "工作階段，失敗的 toast（danger）", scene_sessions_toast_error),
    ("sessions-modal-kill", "工作階段，終止的確認對話框", scene_sessions_modal_kill),
    ("sessions-modal-rename", "工作階段，重新命名的對話框（帶輸入框）", scene_sessions_modal_rename),
    ("sessions-pager", "工作階段，一頁裝不下時的分頁列", scene_sessions_pager),
    ("sessions-no-gitlab", "工作階段，部署沒開 GitLab", scene_sessions_no_gitlab),
    ("drawer-open", "終端抽屜開著（ttyd 用替身）", scene_drawer_open),
    ("account-user", "帳號頁，一般使用者看到的", scene_account_user),
    ("account-admin", "帳號頁，管理員看到的（含帳號清單）", scene_account_admin),
]


FREEZE_TIMERS_JS = """
// 週期性的東西一律不跑：列表每 15 秒重抓一次（sessions.html），抽屜的提示每 6 秒換一條
// （app.js 的 hintTimer）。錄製與比對都要求畫面停在一個**確定**的狀態，而這兩個計時器
// 隨時可能在快照的前一刻把畫面換掉。慢一點的機器上尤其容易跨過去，而那種紅燈看起來
// 像功能壞了。
// ⚠ 只擋長間隔（>= 5 秒）。短的 setInterval 有正經用途，一律擋掉會把功能弄壞，
//   而那會讓 golden 錄到一個實際上不存在的畫面——比不穩定更糟。
(() => {
  const real = window.setInterval;
  window.setInterval = function (fn, ms, ...rest) {
    if (typeof ms === "number" && ms >= 5000) return 0;
    return real.call(this, fn, ms, ...rest);
  };
})();
"""


def new_context(browser, viewport_key: str):
    """每一場都開一個乾淨的 context：登入狀態、localStorage、主題都不會互相汙染。"""
    ctx = browser.new_context(
        viewport=VIEWPORTS[viewport_key],
        timezone_id="Asia/Taipei",
        locale="zh-TW",
        # 動畫關掉：app.css 自己就有 prefers-reduced-motion 的規則，用它比自己灌一份
        # `animation: none` 好——那是這個專案已經維護著的路徑，不是我另外發明的。
        reduced_motion="reduce",
        # 截圖要逐位可比，就不能讓螢幕的 dpr 混進來。
        device_scale_factor=1,
        color_scheme="dark",
    )
    ctx.add_init_script(FREEZE_TIMERS_JS)
    return ctx


def prepare_page(page) -> list:
    """把時鐘釘死並開始收網路。回傳那個會被塞滿的 list。"""
    # ⚠ 一定要在第一次 goto **之前**。relTime()／absTime()／freshness() 全都拿
    #   `new Date()` 跟資料裡的時刻相減，時鐘不釘死的話「3 分鐘前」會隨著錄製當下改變。
    # ⚠ 用 set_fixed_time 不是 install：後者會接管所有計時器，而列表的 15 秒輪詢與
    #   toast 的關閉都靠計時器，接管之後畫面會停在一個永遠不會前進的狀態。
    page.clock.set_fixed_time(NOW)
    reqs: list = []
    page.on("request", lambda r: reqs.append((r.method, r.url)))
    return reqs


# 各段的標題。只有一份定義，比對時是逐字比的，改字要連 golden 一起重錄。
ASSETS_HEADER = "# 靜態資源（排序後、去掉 query，見 golden_scenes.network_text 的說明）"
URL_HEADER = "# 場景就緒時的網址（replaceState 寫進去的條件不會產生請求）"


# Vite 把內容雜湊寫進檔名（`index-DhIKEuyr.js`），**每次 build 都不一樣**。
#
# ⚠ 不正規化的話 golden 會在下一次 build 就紅，而紅的原因與介面一點關係都沒有。
#   `--verify` 也**抓不到**這一類：兩次錄製共用同一份 build，雜湊當然一樣（同
#   `users.created_at` 那次的形狀，見階段 2 的紀錄）。
# ⚠ 只套在 `/assets/` 上，見 network_text 裡那段說明。
# ⚠ 但**不是整段丟掉**：哪幾個 chunk 會載入是真的契約 —— 路由層的 code splitting 讓
#   `AccountView-*.js` 只在帳號頁載入，哪天有人把它靜態 import 進 AppShell，這裡就會紅。
_HASHED = re.compile(r"-[A-Za-z0-9_-]{8,}(\.[a-z]+)$")


def _unhash(path: str) -> str:
    return _HASHED.sub(r"-<hash>\1", path)


def network_text(page, reqs: list, base: str) -> str:
    """把收到的請求整理成可比對的文字。

    ⚠ 分成三段是刻意的：
      · **文件與 API** 依序列出，**連 query 一起**。這一段才是要守的東西：篩選條件、
        `limit`／`offset`、時間範圍全在 query 裡，而那正是 Vue 版最容易做錯的地方
        （少帶一個參數、把 offset 算錯，畫面看起來還是一張表，資料卻是另一批）。
      · **靜態資源**排序後列出，**query 丟掉**。順序是瀏覽器排的，同一份頁面兩次載入
        誰先回來不歸我們管；照原順序記會隨機紅，而隨機紅的 golden 最後只會被人加到
        忽略清單裡。query 丟掉是因為 `asset_url()` 帶的 `?v=` 是檔案 mtime 算的，
        每次 checkout 之後都不一樣。排序之後仍然守得住「少載了一個檔案」。
      · **場景就緒時的網址**。`?tab=past` 與篩選條件是用 `replaceState` 寫進網址的，
        **不會產生任何請求**，所以前兩段完全看不到它們。而「條件的唯一真相在網址」
        正是這個前端的核心設計（見 sessions.html 的註解），漏掉它等於沒有守到。
    """
    docs, assets = [], []
    for method, url in reqs:
        if not url.startswith(base):
            continue
        rest = url[len(base) :] or "/"
        path = rest.split("?", 1)[0] or "/"
        # ⚠ `/assets/` 也是靜態資源：那是 Vite 打包出來的產物（`index-<hash>.js`），
        #   檔名帶內容雜湊、每次 build 都不一樣。不歸類的話它會落進「文件與 API」那一段，
        #   而那一段是逐字比的。
        if path.startswith(("/static/", "/assets/")):
            # ⚠ 只有 `/assets/` 要正規化。`/static/` 底下是人取的檔名，
            #   `01-circuit-board-transparent.webp` 被當成雜湊抹掉的話，「登入頁用的是哪一張
            #   插畫」就不見了 —— 而那正是 pin_all() 特地釘死的東西（2026-08-26 第一次重錄
            #   時實際踩到）。
            assets.append(f"{method} {_unhash(path) if path.startswith('/assets/') else path}")
        else:
            docs.append(f"{method} {rest}")
    here = page.evaluate("() => location.pathname + location.search")
    out = ["# 文件與 API（依序，含 query）"]
    out += docs
    out += ["", ASSETS_HEADER]
    out += sorted(set(assets))
    out += ["", URL_HEADER, here]
    return "\n".join(out) + "\n"


# ── DOM 快照：aria 記不到的那一整類合約 ──────────────────────────────────────
#
# aria 樹只記 role 與可及名稱。實測（2026-08-25）：整份 golden 的 aria 檔案裡
# `data-act` / `class` 一個字都沒有，而模板與 app.js 裡光 `data-testid` 就 115 處、
# `data-act` 28 處。那些正好是**合約型**的東西：testid 是 e2e 的抓手、act 是 app.js
# 事件委派的分派鍵、tone/kind/state/stale 是狀態的真相來源。
#
# ⚠ **只記白名單內的屬性，不記 class、不記完整 HTML。** 記整棵 DOM 的話，Vue 版多包
#   一層 wrapper 就會整份紅——那種 golden 一週內就會被人停用，停用之後連原本守得住的
#   那些也一起沒了。白名單讓「多一層 div」無聲、「少一個 data-act」出聲。
#
# 排除項也要講清楚，否則看起來像漏掉：
#   · 動畫與過場的暫態（data-shown / data-closing / data-swap / data-swapping /
#     data-animate / data-loading / data-drop / data-pausable）：它們在畫面停定之後
#     不一定是同一個值，記了就是自找不穩定。
#   · 內容或設定的回音（data-label / data-name / data-container / data-persist-path /
#     data-cli / data-behind-proxy / data-for / data-theme / data-sid）：可見的部分
#     aria 與截圖已經蓋著了，這裡再記一份只是同一件事寫兩遍。
#   · `disabled` / `aria-expanded`：**aria 快照已經記了**（`[disabled]` / `[expanded]`）。
#     同一個事實兩個來源比一個更糟：改動時兩邊都要更新，而只更新一邊沒有人會發現。
#   · `aria-selected` **兩邊都記**，這是刻意的例外。aria 快照只看得見**可見**的元素，而
#     picker 的選單收起來之後就不在 aria 樹裡了，收起來的那份 DOM 正是選中狀態最容易
#     過期的地方（2026-08-26 抓到一個：選完之後 renderMenu() 不會再跑，aria-selected
#     停在上一個值）。可見的那些重複一次無害，隱藏的那些只有這裡看得到。
#   · `aria-checked` **有記**，因為實測 aria 快照裡一個 `[checked]` 都沒有（開關那三顆
#     用的是 role=switch，Playwright 沒有把它的勾選狀態畫進去）。那是真的缺口。
#   · `hidden` **有記**：它區分得出「沒有渲染」與「渲染了但藏起來」，而 Vue 版把
#     `v-if` 寫成 `v-show`（或反過來）正是這個差別，aria 只看得到前者。
DOM_ATTRS = [
    # 身分：誰是誰、按下去會觸發什麼
    "data-testid",
    "data-act",
    "data-id",
    "data-seg",
    "data-edit",
    "data-move",
    "data-day",
    "data-value",
    # 狀態：畫面此刻在說什麼
    "data-tone",
    "data-kind",
    "data-state",
    "data-stale",
    "data-level",
    "data-on",
    "data-in",
    "data-edge",
    "data-active",
    "data-open",
    "data-disabled",
    "data-empty",
    "aria-checked",
    "aria-selected",
    "hidden",
    "inert",
    # id 與 aria-controls 是**成對**的契約：aria-controls 指的那個 id 必須真的存在。
    # ⚠ 但 id **只記被指到的那些**（見 _DOM_JS 的 referenced）。無條件記所有 id 的話，
    #   SPA 的掛載點 `<div id="app">` 會變成 dom.txt 的第一行，於是每一場的第一行都
    #   `golden='div testid=shell' vs 現在='div id=app'`，十八場全紅——而那不是回歸，
    #   是 Vue 版必然會有的一層外殼。那正是我在這個檔頭寫過的「多包一層 wrapper 就整份紅」，
    #   我自己加 id 的時候又把它放了回來（2026-08-26 對 vue 模式跑第一輪時現形）。
    # 只記其中一半的話，Vue 版把 id 改名而 aria-controls 沒跟著改，這裡看起來一切正常，
    # 而螢幕閱讀器會指到一個不存在的東西。
    "id",
    "aria-controls",
    # title：原生 tooltip。與 data-tip 同理，滑過去才看得到（截圖蓋不到），
    # 也不是可及名稱（aria 蓋不到）。
    "title",
    # 提示文字：滑過去才看得到，所以截圖蓋不到；不是 aria 名稱，所以 aria 也蓋不到。
    # 它一旦悄悄消失，沒有任何一道防線會出聲。
    "data-tip",
]

_DOM_JS = r"""(attrs) => {
  // 被 aria-controls / aria-labelledby / aria-describedby / label[for] 指到的 id。
  // 只有這些 id 是**契約**（指過去必須指得到）；其餘的 id 是實作細節，記了只會讓
  // 「多包一層有 id 的外殼」變成整份紅。
  const referenced = new Set();
  const REFS = ["aria-controls", "aria-labelledby", "aria-describedby", "for"];
  for (const el of document.querySelectorAll("[" + REFS.join("],[") + "]")) {
    for (const a of REFS) {
      const v = el.getAttribute(a);
      if (v) v.split(/\s+/).forEach((x) => x && referenced.add(x));
    }
  }
  const out = [];
  for (const el of document.querySelectorAll("*")) {
    const parts = [];
    for (const a of attrs) {
      // inert 是 property，反映到同名屬性；直接問 property 比較可靠。
      if (a === "inert") { if (el.inert) parts.push("inert"); continue; }
      if (a === "id" && !referenced.has(el.id)) continue;
      if (!el.hasAttribute(a)) continue;
      // 只剝 data- 前綴。aria-* 原樣保留：剝掉的話 aria-checked 會變成 checked，
      // 哪天有人加一個 data-checked 就撞名了，而撞名之後兩件事在檔案裡長得一模一樣。
      const key = a.replace(/^data-/, "");
      // 空值的布林屬性（hidden）只印名字；值裡的空白壓成單一空白，保證一元素一行。
      const v = el.getAttribute(a).replace(/\s+/g, " ").trim();
      if (v === "") { parts.push(key); continue; }
      parts.push(v.includes(" ") ? `${key}="${v}"` : `${key}=${v}`);
    }
    if (parts.length) out.push(`${el.tagName.toLowerCase()} ${parts.join(" ")}`);
  }
  return out.join("\n");
}"""


def dom_text(page) -> str:
    """帶白名單屬性的元素，一行一個，依 DOM 順序。逐字比對，不設閾值。"""
    return page.evaluate(_DOM_JS, DOM_ATTRS).rstrip("\n") + "\n"


# ── 錄製環境的指紋 ───────────────────────────────────────────────────────────
#
# 截圖是**平台相依**的：golden 在 macOS 錄（字體是 PingFang TC），同一份程式碼在
# ubuntu runner 上算繪出來的字完全是另一組像素。沒有這道 gate 的話，CI 的 `--all`
# job 一跑 golden_check，十二條截圖必紅——而那不是回歸，是兩台機器的字體不一樣。
#
# ⚠ 這道 gate 的方向要對：**平台不同時只跳過截圖，aria／dom／network 照比**。
#   那三份是文字，與字體無關，跨平台完全可比；把整支 golden_check 跳掉才是把
#   CI 上唯一守得住介面的東西關掉。
# ⚠ 跳過時要**明說**，不可以靜靜地少比三十六條。看不見的跳過就是假綠燈。
META_NAME = "META"


def meta_text(browser) -> str:
    import platform

    # ⚠ 這裡曾經有一行 `ui=`（legacy／vue），兩版並存期間 golden_check 靠它知道自己在跨版
    #   比對。legacy 於 2026-08-26 拆除、golden 也重錄成 Vue 版之後，那一行永遠只有一個值，
    #   留著只會讓人以為還有第二版可以比（同 config.py 拿掉 CLAUDE_PTY_UI 的理由）。
    return (
        f"platform={platform.system()} {platform.machine()}\n"
        f"chromium={browser.version}\n"
        f"device_scale_factor=1\n"
        f"color_scheme=dark\n"
        f"viewports={','.join(VIEWPORTS)}\n"
    )


def meta_path() -> str:
    return os.path.join(GOLDEN_DIR, META_NAME)


def screenshot_comparable(browser) -> tuple[bool, str]:
    """現在這台機器能不能拿來比截圖？回 (能不能, 說明)。"""
    try:
        want = open(meta_path(), encoding="utf-8").read()
    except OSError:
        return False, "golden 裡沒有 META（用舊版錄的，重錄一次就有）"
    now = meta_text(browser)
    wl, nl = dict(_kv(want)), dict(_kv(now))
    keys = set(wl) | set(nl)
    diff = [f"{k}：golden={wl.get(k)!r} 現在={nl.get(k)!r}" for k in sorted(keys) if wl.get(k) != nl.get(k)]
    return (False, "；".join(diff)) if diff else (True, "")


def _kv(text: str):
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            yield k, v


def scene_dir(name: str) -> str:
    return os.path.join(GOLDEN_DIR, name)


def cleanup() -> None:
    reset_engine()
    __import__("shutil").rmtree(TMP, ignore_errors=True)
