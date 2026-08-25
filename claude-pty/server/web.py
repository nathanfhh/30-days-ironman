"""管理畫面的 HTML 路由（ADR 0008 階段 6）。

三個頁面（`/`、`/login`、`/account`）現在**一律吐同一份 SPA 殼**（`dist/index.html`），
路由與畫面全在前端。所有資料都由 SPA 打 `/api/*` 取得（同一套 authn/authz gate，
不另開後門）。與 API 的差別只在未登入時的行為：API 回 401 JSON，頁面導向 /login。

⚠ 2026-08-26 拆掉 legacy 之後，這裡**沒有任何 Jinja 模板了**。`server/templates/` 整個
  目錄與 `server/static/js/app.js` 一起刪除，`_page()` 那層包裝、三個 template global
  （`asset_url` / `persist_dir` / `build_info`）也隨之退場——後兩者的值現在由
  `/api/bootstrap` 出（`app.bootstrap`），那才是 SPA 拿得到的地方。
"""

from __future__ import annotations

import os
import random

from flask import Blueprint, redirect, send_from_directory, session

from . import auth, config

web = Blueprint("web", __name__)


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


@web.get("/login")
def login_page():
    # 已登入者不該停在登入頁——gate 對本端點是放行的（它必須公開），故這裡自行判斷。
    # 與「未登入訪問管理頁 → 導向 /login」互為對稱。
    #
    # ⚠ 這一條**不能交給前端做**：SPA 要先載入、先問一次「我是誰」才知道自己已經登入了，
    #   那期間畫面上是登入表單。伺服端一句 302 沒有那個窗口。
    if session.get("uid") and auth.get_user(session["uid"]) is not None:
        return redirect("/")
    return _spa_shell()


@web.get("/")
def sessions_page():
    return _spa_shell()


@web.get("/account")
def account_page():
    return _spa_shell()


@web.get("/healthz")
def healthz():
    """給 nginx / 監控用的存活檢查（公開，不需登入）。"""
    return {"status": "ok"}


def redirect_to_login():
    return redirect("/login")
