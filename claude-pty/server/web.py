"""管理畫面的 HTML 路由（ADR 0008 階段 6）。

只負責吐頁面骨架；所有資料都由頁面上的 JS 打 `/api/*` 取得（同一套 authn/authz gate，
不另開後門）。與 API 的差別只在未登入時的行為：API 回 401 JSON，頁面導向 /login。
"""

from __future__ import annotations

import contextlib
import os
import random

from flask import Blueprint, g, redirect, render_template, send_from_directory, session, url_for

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


# --- Vue 版（階段 4）：同樣三條網址，回的是 SPA 的殼 -------------------------------
#
# ⚠ **這條路只在 dev 與 e2e 用。** 正式部署由 nginx 直接 serve `server/static/dist/`
#   （`/assets/` 長快取、三條頁面路由 try_files 回 index.html，見 deploy/nginx.conf 與
#   它旁邊的 nginx-ui/）。留這條的理由是 e2e 跑的是 in-thread Flask、沒有 nginx——
#   兩邊都要走得通，不然「測試綠了但部署是另一條路」。
#
# ⚠ 兩種 UI 的 gate **完全相同**：`/` 與 `/account` 不在 `_PUBLIC_ENDPOINTS` 裡，未登入
#   照樣被導回 `/login`。SPA 自己進頁再打一次 `/api/auth/me` 是為了拿身分，不是授權——
#   授權永遠在後端（每一支 `/api/*` 都過同一道 gate）。


def _spa_shell():
    """SPA 的殼。

    ⚠ Cache-Control 要**明確**設成 no-store。`SEND_FILE_MAX_AGE_DEFAULT` 是一年（給帶版本戳
      的 static 用的），而 `_security_headers` 用的是 `setdefault`——不自己設的話，
      `send_from_directory` 先寫上的 max-age=31536000 會留著，改版後使用者會拿到舊的殼，
      而殼裡指的是**已經不存在的** /assets/*.js（那是一片白畫面，沒有任何線索）。
      HTML 一律 no-store 是這個 codebase 既有的規矩，這裡只是把它兌現到新的路上。
    """
    resp = send_from_directory(config.DIST_DIR, "index.html", max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@web.get("/assets/<path:filename>")
def spa_asset(filename: str):
    """SPA 的 JS/CSS。檔名帶內容雜湊（Vite 產的），所以可以放心長期快取。

    ⚠ 這一條**必須公開**（見 app._PUBLIC_ENDPOINTS）：登入頁本身就是 SPA，資源拿不到的話
      沒登入的人只看得到一片白。它只吐 build 產物，不含任何使用者資料。
    """
    return send_from_directory(os.path.join(config.DIST_DIR, "assets"), filename, max_age=31536000)


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
        # 招牌徽章的鍵：這套東西只驅動 claude 一種 CLI。
        default_cli="claude",
        **extra,
    )


@web.get("/login")
def login_page():
    # 已登入者不該停在登入頁——gate 對本端點是放行的（它必須公開），故這裡自行判斷。
    # 與「未登入訪問管理頁 → 導向 /login」互為對稱。
    if session.get("uid") and auth.get_user(session["uid"]) is not None:
        return redirect("/")
    if config.UI == "vue":
        return _spa_shell()
    return render_template(
        "login.html",
        behind_proxy=config.BEHIND_PROXY,
        min_password_length=config.MIN_PASSWORD_LENGTH,
        # 每次載入隨機挑一張——在伺服端選，頁面第一次繪製就是最終畫面，
        # 不會出現「先空著、JS 載入後才蹦出一張圖」的跳動。
        art=random.choice(LOGIN_ART) if LOGIN_ART else None,
    )


@web.get("/")
def sessions_page():
    if config.UI == "vue":
        return _spa_shell()
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
    if config.UI == "vue":
        return _spa_shell()
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
