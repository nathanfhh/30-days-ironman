"""管理畫面的 HTML 路由（ADR 0008 階段 6）。

只負責吐頁面骨架；所有資料都由頁面上的 JS 打 `/api/*` 取得（同一套 authn/authz gate，
不另開後門）。與 API 的差別只在未登入時的行為：API 回 401 JSON，頁面導向 /login。
"""

from __future__ import annotations

import contextlib
import os
import random

from flask import Blueprint, g, redirect, render_template, session, url_for

from . import auth, config, version
from . import sessions as sessions_mod

web = Blueprint("web", __name__)


def _asset_version() -> str:
    """靜態資源的版本戳＝所有 static 檔案裡最新的 mtime。

    加在 URL 上讓瀏覽器能安心長期快取，同時**改檔就換網址**——不必叫使用者按
    Cmd+Shift+R，也不會發生「CSS 改好了但看到的還是舊版」這種只有開發者知道的坑。
    容器化後 static 是 COPY 進 image 的，這個值在啟動時算一次就固定了。
    """
    latest = 0.0
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    for root, _dirs, files in os.walk(static_dir):
        for name in files:
            with contextlib.suppress(OSError):
                latest = max(latest, os.path.getmtime(os.path.join(root, name)))
    return str(int(latest))


ASSET_VERSION = _asset_version()


@web.app_template_global()
def asset_url(filename: str) -> str:
    """帶版本戳的靜態資源網址。模板一律用它，不要直接 url_for('static', ...)。"""
    return url_for("static", filename=filename, v=ASSET_VERSION)


@web.app_template_global()
def persist_dir() -> str:
    """session 內唯一寫了會留下來的目錄（容器內路徑）。

    做成 template global 而不是 `_page()` 的參數：用它的是抽屜的標題列，而抽屜由
    `app.js` 建立、每一頁都可能開——參數化的話漏掉哪一頁就會顯示空字串。
    值來自 `config.DATA_BIND`（SSOT），不要在 JS 或模板裡重打一次路徑。
    """
    return config.DATA_BIND


@web.app_template_global()
def build_info() -> dict:
    """頁尾要顯示的各模組版本與 commit（見 server/version.py）。

    做成 template global 而不是塞進 `_page()` 的參數：登入頁不走 `_page()`，但它同樣
    繼承 base.html，也同樣需要頁尾——參數化的話那一頁會靜靜地少一塊。
    """
    return version.summary()


# 登入頁左下角的插畫。啟動時掃一次即可——static 是 COPY 進 image 的，執行期不會多出新檔，
# 每次 request 重掃只是白花 I/O。放新圖需要 rebuild（與其他靜態資源一致）。
_ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "images")
try:
    LOGIN_ART = sorted(f for f in os.listdir(_ART_DIR) if f.endswith((".webp", ".png", ".svg")))
except OSError:
    LOGIN_ART = []  # 沒有這個目錄就不顯示插畫，登入功能不受影響


def login_art() -> str | None:
    """這次要顯示哪一張插畫的檔名；沒有圖就 `None`。

    **每次呼叫重挑一張**：「每次載入換一張」是這張圖的行為，不是啟動時定案的設定。
    抽出來是為了讓登入頁與 `/api/bootstrap` 共用同一個決定：兩邊各寫一份 `random.choice`
    的話，哪天有人給其中一邊加了條件（例如「沒有圖時改畫別的」），另一邊不會跟著變，
    而畫面上看不出兩者已經是兩套規則。
    """
    return random.choice(LOGIN_ART) if LOGIN_ART else None


def _page(template: str, active: str, **extra):
    # 憑證狀態在伺服端就算好：招牌上的徽章第一次繪製就是正確的，不會先閃一個
    # 預設樣式再被 JS 改成警示。帳號管理頁沒有輪詢，也只有這條路能拿到它。
    return render_template(
        template,
        user=g.user,
        active=active,
        behind_proxy=config.BEHIND_PROXY,
        min_password_length=config.MIN_PASSWORD_LENGTH,
        name_max=config.NAME_MAX,
        username_max=config.USERNAME_MAX,
        credentials=sessions_mod.credentials_state(g.user["id"]),
        # 招牌徽章的鍵：這套東西只驅動一種 CLI（SSOT 在 config，`/api/account/bootstrap`
        # 與 credentials_state 讀的是同一個常數，三份字面量遲早分岔）。
        default_cli=config.DEFAULT_CLI,
        **extra,
    )


@web.get("/login")
def login_page():
    # 已登入者不該停在登入頁——gate 對本端點是放行的（它必須公開），故這裡自行判斷。
    # 與「未登入訪問管理頁 → 導向 /login」互為對稱。
    if session.get("uid") and auth.get_user(session["uid"]) is not None:
        return redirect("/")
    return render_template(
        "login.html",
        behind_proxy=config.BEHIND_PROXY,
        # ⚠ **這裡刻意不給 `min_password_length`。** 登入頁本來就不檢查密碼長度（見
        #   login.html 的說明：長度是「建立／變更密碼」時的政策，搬到登入頁的話，調高
        #   政策之後所有舊帳號都會按不下送出鈕，而後端明明還驗得過他們的密碼）。
        #   這個參數以前傳著卻沒有人用，而沒人用的參數不是無害的：它讓下一個人以為登入
        #   頁需要這個值，於是把它加進**公開**的 /api/bootstrap，那才是真的放寬曝光面。
        # 每次載入隨機挑一張——在伺服端選，頁面第一次繪製就是最終畫面，
        # 不會出現「先空著、JS 載入後才蹦出一張圖」的跳動。
        art=login_art(),
    )


@web.get("/")
def sessions_page():
    # claude 的模型白名單在伺服端就給，列表的 chip 直接照它畫。
    return _page(
        "sessions.html",
        active="sessions",
        claude_models=list(config.CLAUDE_MODELS),
        # 這套部署有沒有開 GitLab 代理（ADR 0016）。列表的 GitLab 標記靠它 gate。
        # ⚠ **一定要 gate**：功能關掉時每一場的 `gitlab_proxy` 都是 False，不擋的話
        #   每一列都會掛一顆灰色 GitLab 圖示，對著使用者講一件這台機器上根本不存在
        #   的事。這是部署層的事實，不是 per-session 的，所以在伺服端算好給模板，
        #   不從列表 API 的每一列推。
        gitlab_enabled=config.gitlab_enabled(),
    )


@web.get("/account")
def account_page():
    # GitLab 那一塊的兩個事實都在伺服端算好：這套部署有沒有開這個功能，以及這個人設過沒。
    # ⚠ 只給**狀態**，永遠不給值（連密文都不出去）——見 auth._to_dict。
    return _page(
        "account.html",
        active="account",
        gitlab_enabled=config.gitlab_enabled(),
        gitlab_host=config.GITLAB_HOST,
        # ⚠ `get_user` 是 `dict | None`——直接下標在那一列剛好消失時會變成
        #   500 HTML 錯誤頁，而 gate 對同一件事的答案是導回登入頁（審查 F-032）。
        gitlab_pat_set=(auth.get_user(g.user["id"]) or {}).get("gitlab_pat_configured", False),
        # 代理**連續**起不來時 nginx 說的那句話（診斷麵包屑，見
        # auth.gitlab_proxy_error）。沒有它，使用者只會看到「GitLab 連不到」，
        # 然後往 token／網路／GitLab 是不是掛了這些錯的方向查。
        gitlab_proxy_error=auth.gitlab_proxy_error(g.user["id"]),
    )


@web.get("/healthz")
def healthz():
    """給 nginx / 監控用的存活檢查（公開，不需登入）。"""
    return {"status": "ok"}


def redirect_to_login():
    return redirect("/login")
