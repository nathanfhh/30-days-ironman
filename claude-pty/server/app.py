"""Flask 控制平面（ADR 0004 / 0005 / 0008）。只綁 loopback，對外一律經 nginx。

  /api/auth/*     登入 / 登出 / 現在是誰 / nginx auth_request 掛載點
  /api/users*     帳號管理（新增使用者、改密碼）
  /api/sessions*  session 生命週期 + on-demand view

**所有 /api/* 都要過 authn gate**（ADR 0005 註記的缺口）——控制平面能建立具 NET_ADMIN
的 container、列舉/終止 session，不可只保護終端路由。
"""

from __future__ import annotations

import datetime as _dt
from contextlib import suppress
from datetime import timedelta
from functools import wraps

from flask import Flask, g, jsonify, request, session

from . import auth, config, views
from . import sessions as sessions_mod
from .db import init_db
from .sessions import (
    Profile,
    SessionError,
    SessionManager,
    SessionNotFound,
    _as_bool,
)
from .views import ViewError
from .web import redirect_to_login, web

app = Flask(__name__)
app.register_blueprint(web)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,          # 多 worker 共用同一把，cookie 才能互驗
    SESSION_COOKIE_HTTPONLY=True,          # JS 取不到，降低 XSS 竊取風險
    SESSION_COOKIE_SAMESITE="Lax",         # 跨站請求不帶 cookie（防 CSRF 的第一層）
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(days=config.SESSION_LIFETIME_DAYS),
)
init_db()  # 建表（冪等）；registry 持久化於 DB（ADR 0008），多 worker 共用同一份
manager = SessionManager()

for _problem in sessions_mod.preflight():   # 設定不對就大聲講，不要靜默降級
    print(f"[claude-pty] ⚠ {_problem}", flush=True)

# 不需登入的端點：登入 API、登入頁、靜態資源、健康檢查。其餘一律過 gate。
_PUBLIC_ENDPOINTS = {"login", "web.login_page", "web.healthz", "static"}


@app.errorhandler(SessionNotFound)
def _session_not_found(e: SessionNotFound):
    """404 的對外說法。

    ⚠ 訊息**兩種情況必須一模一樣**（不存在／不是你的），否則就洩漏了「這個 id 存在」
      （review L1）。所以這裡不分支，只講一件對兩者都成立的事。

    原本直接吐 `未知 session：<id>`——那是給程式看的字串，對人只說了「沒有」，沒說
    「為什麼沒有、我現在該做什麼」。實務上最常見的成因就一個：**那場已經結束了**
    （在終端按 Ctrl+D、或被終止，container 一停對帳器就把登錄歸檔），而使用者的畫面
    還停在結束之前。前端收到 404 會順手重拉列表（見 sessions.html 的共用 catch），
    所以這段話要能接上「你會看到那一列消失」這個結果。
    """
    return jsonify(
        error=f"{e}——它可能已經結束了（在終端按 Ctrl+D 或被終止，container 一停就會歸檔），"
              f"也可能不屬於你。對話沒有消失，建一場新的 session 就能用 /resume 接回來。"), 404


@app.errorhandler(SessionError)
def _session_error(e: SessionError):
    return jsonify(error=str(e)), 400


@app.errorhandler(ViewError)
def _view_error(e: ViewError):
    return jsonify(error=str(e)), 400


@app.errorhandler(auth.AuthError)
def _auth_error(e: auth.AuthError):
    return jsonify(error=str(e)), 400


@app.after_request
def _security_headers(resp):
    """基本瀏覽器安全標頭（review L2）。

    CSP 白名單只開實際用到的來源：字體與圖示 CSS 來自 cdnjs / Google Fonts，其餘一律自家。
    inline script/style 目前仍需 'unsafe-inline'（模板內有 <script> 與 style 屬性）——
    要收緊得先把它們外部化，屬後續工作，這裡先擋住最容易被利用的其餘方向。
    """
    resp.headers.setdefault("Content-Security-Policy", "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com",
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",          # 不給任何人嵌成 iframe（防點擊劫持）
        "base-uri 'self'",
        "form-action 'self'",
    ]))
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # HTML 頁面一律不快取。它的內容依登入狀態而異（登入者名稱、是否顯示管理區塊），
    # 而且頁內的 <script> 就是應用程式本身——被瀏覽器快取的話，改版後使用者會繼續看到
    # 舊行為，卻沒有任何跡象顯示他看的是舊的（實際踩到：修好的訊息仍然跳出來）。
    # 靜態資源不套這條：它們帶版本參數（見 web.py 的 asset_url），可以放心長期快取。
    if resp.mimetype == "text/html":
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# --- authn gate -------------------------------------------------------------------

@app.before_request
def _require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS or request.endpoint is None:
        return None

    uid = session.get("uid")
    user = auth.get_user(uid) if uid else None
    # 改密碼會遞增 password_version；舊 cookie 帶的是舊版號 → 立即失效（review H4）。
    # 沒有這道檢查，管理員重設被盜帳號的密碼後，攻擊者手上的 cookie 仍可用滿 7 天。
    if user is not None and session.get("pwv") != user["password_version"]:
        user = None
    if user is None:
        # 未登入、版號對不上（改過密碼），或那一列直接從 DB 消失了
        # ——應用層沒有刪除帳號的路徑（ADR 0010），所以最後一種只會是有人動了資料庫。
        session.clear()       # 手上的 cookie 一律作廢
        # API 回 401 讓前端自行處理；HTML 頁面導向登入頁（同一個 gate，兩種呈現）
        if request.path.startswith("/api/"):
            return jsonify(error="未登入"), 401
        return redirect_to_login()
    g.user = user
    return None


@app.before_request
def _require_json_for_writes():
    """狀態變更請求必須是 application/json。

    HTML `<form>` 只能送 urlencoded / multipart / text-plain，無法送 JSON content-type，
    故此檢查可擋掉「表單型」CSRF；配合 SameSite=Lax 形成兩道（review M4）。

    ⚠ **不可拿 `request.content_length` 當前置條件**（原本是這樣寫的）。沒有任何欄位的
      `<form method=post>` 送出的正是 `Content-Length: 0` 的 urlencoded——條件短路，整條
      檢查跳過，於是這個：

          <form method="POST" action="http://127.0.0.1:8080/api/sessions"></form>

      就以受害者的身分建出一個 session 容器。同一手法也打得到 /api/auth/logout。
      「沒有 body」不等於「不必檢查 content-type」。

    ⚠ SameSite=Lax **補不上這個洞**：它是 **site** 級的，不分 port，也涵蓋兄弟子網域。
      以現在 `127.0.0.1:8080` 的部署而言，localhost 上任何其他 port 的頁面都是同一個
      site，cookie 照帶（Claude review 2026-07-26 實證）。

    放行的條件因此收成兩種：
      - `is_json`——正常的呼叫端。
      - **完全沒有 body、也沒有 Content-Type**——前端 `api()` 在沒有 body 時就是這樣送的
        （DELETE /api/sessions/<sid> 等）。`<form>` 一定會帶 Content-Type，所以擋得到。
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE") \
            and request.path.startswith("/api/") \
            and not request.is_json \
            and (request.content_type or request.get_data()):
        return jsonify(error="請以 application/json 送出"), 415
    return None


# --- 輸入驗證（review M5：malformed 輸入原本會變成 500）--------------------------

_ENUMS = {
    "cli": ("claude",),        # 這套東西只驅動 claude 一種 CLI
    "network": ("restricted", "unrestricted"),
}

# 模型與思考深度的白名單：值是 `claude --model` / `--effort` 的別名（實測 v2.1.207 的
# --help）。
# ⚠ 這是唯一的白名單：Profile 只負責帶值，不做驗證。放寬這裡就等於放行到 CLI 參數。
# ⚠ 順序有意義：它就是選單的排列。退路用 default_model，排列以最常用的在前。
# SSOT 在 config（畫面也要用同一份，而 web.py 不能 import app）
_CLAUDE_MODELS = config.CLAUDE_MODELS
_CLAUDE_EFFORTS = config.CLAUDE_EFFORTS


def _body() -> dict:
    """取 JSON body。沒有 body ＝ `{}`；有 body 但不是合法的 JSON 物件一律 400。

    ⚠ **不可只靠 `get_json(silent=True) is None` 分岔**（原本是這樣寫的）：它對「沒有
      body」與「JSON 語法壞掉」回同一個 `None`，兩者一起塌成 `{}`，於是一個少了括號的
      body 會被靜靜當成空物件去跑預設動作——`POST /api/sessions` 就建出一個帶預設
      profile 的容器，呼叫端還拿到 201。這是最難查的那一種：沒有錯誤、
      有一個容器，但它跑的不是你要的東西。這個 docstring 原本就寫著要避免這件事。
    """
    if not request.get_data():
        return {}
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadInput("body 必須是 JSON 物件")
    return data


def _reject_unknown(body: dict, allowed: set[str]) -> None:
    """未知欄位一律 400。

    ⚠ 這是 `POST /api/users` 早就在做的事，session 這邊卻沒有
      ——於是打錯欄位名（少一個字母）會**靜靜建出一個用預設值的容器**，
      呼叫端拿到 201 而完全沒有跡象（交叉審查 2026-07-26 指出）。
    """
    unknown = set(body) - allowed
    if unknown:
        raise BadInput(f"不支援的欄位：{'、'.join(sorted(unknown))}")


def _clean_display_name(body: dict) -> str | None:
    """session 顯示名稱的**唯一**驗證點（create 與 rename 共用）。

    ⚠ 這四行原本在兩個 handler 裡各寫一份、逐字相同。create 給的名字會參與 container
      命名（`sessions._slugify`）、rename 只改顯示名，但「什麼算合法的名字」是同一個問題
      ——兩份定義漂移的時候不會有任何跡象，也沒有測試在比對它們一致。
    """
    name = body.get("name")
    if name is not None and not isinstance(name, str):
        raise BadInput("name 必須是字串")
    if name and len(name) > config.NAME_MAX:
        raise BadInput(f"name 超過上限（{config.NAME_MAX} 字元）")
    return name


class BadInput(ValueError):
    pass


@app.errorhandler(BadInput)
def _bad_input(e: BadInput):
    return jsonify(error=str(e)), 400


def _int_in(body: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = body.get(key, default)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise BadInput(f"{key} 必須是整數") from None
    if not lo <= val <= hi:
        raise BadInput(f"{key} 必須介於 {lo}–{hi}")
    return val


def _strict_bool(body: dict, key: str, default: bool) -> bool:
    """權限邊界專用的布林解析：只收真正的 JSON boolean。

    與 `sessions._as_bool` 的寬鬆解析刻意分開。後者服務的是 profile 那種「猜錯只是
    多錄一份流量」的旗標；這裡決定的是誰是管理員——`{"is_admin": "yes"}` 必須是 400，
    不能是「靜靜地建出一個管理員」，`"tru"`（拼錯）也不該靜靜地變成普通使用者。
    """
    if key not in body:
        return default
    value = body[key]
    if not isinstance(value, bool):
        raise BadInput(f"{key} 必須是 true 或 false")
    return value


# profile 收得下的鍵。⚠ `model`/`effort` **不在 `_ENUMS` 裡**（它們依 CLI 分流驗，見
# `_check_model_effort`），但仍然是合法欄位——漏掉它們會讓每一個帶模型的請求都被當成
# 「不支援的欄位」擋掉，而錯誤訊息指向欄位名、完全不像驗證分流的問題（本輪測試抓到）。
_PROFILE_BOOLS = ("capture", "telemetry")
_PROFILE_KEYS = set(_ENUMS) | set(_PROFILE_BOOLS) | {"model", "effort"}


def _clean_profile(raw) -> dict | None:
    """驗 profile。未知鍵 400、列舉走白名單、兩個布林**嚴格解析**。

    ⚠ `capture` / `telemetry` 這裡不可以用 `sessions._as_bool` 的寬鬆解析。它把任何不在
      白名單裡的字串一律當成 False，於是 `{"capture": "treu"}`（拼錯）會靜靜地建出一個
      **沒有錄製**的 session，而呼叫端以為錄了——回頭要那份流量紀錄時才發現沒有，
      而容器早就收掉了。這與 `_strict_bool` 擋 `{"is_admin": "yes"}` 是同一個理由：
      猜錯的代價不是「多錄一份流量」，是「你以為有、其實沒有」。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BadInput("profile 必須是物件")
    unknown = set(raw) - _PROFILE_KEYS
    if unknown:
        raise BadInput(f"不支援的 profile 欄位：{'、'.join(sorted(unknown))}")
    for key, allowed in _ENUMS.items():
        if key in raw and raw[key] not in allowed:
            raise BadInput(f"profile.{key} 只能是 {' / '.join(allowed)}")
    _check_model_effort(raw)
    for key in _PROFILE_BOOLS:
        if key in raw and not isinstance(raw[key], bool):
            raise BadInput(f"profile.{key} 必須是 true 或 false")
    return raw


def _check_model_effort(raw: dict) -> None:
    """模型與思考深度的白名單。"""
    # ⚠ 型別要**先**收掉。下面每一個 `in` 的右值都是 frozenset / dict，`in` 會對左值取
    #   雜湊，而 dict / list 是 unhashable —— 直接 TypeError → 500。這是 review M5
    #   「malformed 輸入原本會變成 500」那道疤的原地復發，只是換了一個欄位；一般帳號送
    #   `{"profile":{"model":{}}}` 就重現得了。tuple 的白名單（_ENUMS）走 __eq__ 不會炸，
    #   所以問題只出在這兩個欄位。
    for key in ("model", "effort"):
        if key in raw and not isinstance(raw[key], str):
            raise BadInput(f"profile.{key} 必須是字串")
    if "model" in raw and raw["model"] not in _CLAUDE_MODELS:
        raise BadInput(f"profile.model 只能是 {' / '.join(_CLAUDE_MODELS)}")
    if "effort" in raw and raw["effort"] not in _CLAUDE_EFFORTS:
        raise BadInput(f"profile.effort 只能是 {' / '.join(_CLAUDE_EFFORTS)}")


@app.get("/api/catalog")
def get_catalog():
    """建立表單要用的模型清單（寫死在 config，見 CLAUDE_MODELS 的說明）。

    `default_model`＝沒有選擇可沿用時該落在哪一顆。**不可以用「清單的第一個」代替**：
    清單的順序只是選單的排列（世代新→舊），預設是另一個獨立的決定（見 config）。
    """
    return jsonify(
        claude={"models": [{"slug": m, "display_name": m.capitalize(),
                            "efforts": list(_CLAUDE_EFFORTS),
                            "default_effort": config.DEFAULT_EFFORT}
                           for m in _CLAUDE_MODELS],
                "default_model": config.DEFAULT_MODEL,
                "source": "static", "fetched_at": None})


def _tri_bool(args, key: str) -> bool | None:
    """三態旗標：缺席／空字串＝不限，"1"＝是，"0"＝否。

    ⚠ 不可用 `key in args` 之類的存在性判斷代替：畫面上把條件清成「不限」時送的是空值，
      而「不限」與「否」必須是兩件事——塌成布林之後就再也篩不出「沒有錄製的」。
    """
    raw = args.get(key)
    if raw is None or raw == "":
        return None
    if raw not in ("0", "1"):
        raise BadInput(f"{key} 只能是 0 / 1（或不給＝不限）")
    return raw == "1"


def _iso_or_none(raw, key: str):
    """把 ISO 8601 字串轉成 aware datetime；空的回 None。

    畫面送的是 `datetime-local` 的值（`2026-07-26T14:30:00`，**沒有時區**）——那是
    使用者當地的牆上時間。這裡把不帶時區的一律當成 UTC 會整整差掉時差，所以由畫面
    負責補上偏移量再送（見 sessions.html 的 localToIso）。這裡只接受帶時區的，
    不帶的就明講——默默猜一個時區是「查出來的區間跟你選的不一樣」這種最難查的錯。
    """
    if raw in (None, ""):
        return None
    try:
        parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        # 最常見的原因是時區偏移的 `+` 沒編碼——它在 query string 裡會被解讀成空白。
        # 講出來，否則對方會盯著一個看起來完全正確的字串查半天。
        raise BadInput(
            f"{key} 不是合法的時間格式（需 ISO 8601，例 2026-07-26T14:30:00+08:00）。"
            f"若是手寫網址，注意時區的 + 要編成 %2B") from None
    if parsed.tzinfo is None:
        raise BadInput(f"{key} 必須帶時區偏移（例 2026-07-26T14:30:00+08:00）")
    return parsed


def _enum_or_none(args, key: str) -> str | None:
    raw = args.get(key)
    if raw is None or raw == "":
        return None
    if raw not in _ENUMS[key]:
        raise BadInput(f"{key} 只能是 {' / '.join(_ENUMS[key])}（或不給＝不限）")
    return raw


def _filters_from_args() -> sessions_mod.Filters:
    """把 query string 轉成 Filters。兩張列表共用同一組參數名。

    """
    args = request.args
    # 時間範圍有兩種寫法，但**只認一種同時存在**：
    #   `since=<天數>`      畫面上的「一週內」這種預設值
    #   `from` / `to`（ISO） 自訂起迄，任一端可省略
    # 兩者混用沒有一個誠實的解釋（「一週內」又「從三月到四月」是什麼意思？），回 400。
    raw_since, raw_from, raw_to = args.get("since"), args.get("from"), args.get("to")
    has_preset = raw_since not in (None, "")
    has_range = raw_from not in (None, "") or raw_to not in (None, "")
    if has_preset and has_range:
        raise BadInput("since 與 from/to 不可同時使用（預設區間與自訂區間擇一）")

    since_at = until_at = None
    if has_preset:
        # 上限 3650 天：再久就等於「不限」，而畫面只提供 1 / 7 / 30
        days = _int_in({"since": raw_since}, "since", 1, 1, 3650)
        since_at = sessions_mod.utcnow() - timedelta(days=days)
    else:
        since_at = _iso_or_none(raw_from, "from")
        until_at = _iso_or_none(raw_to, "to")
        if since_at and until_at and since_at > until_at:
            raise BadInput("from 不能晚於 to")

    return sessions_mod.Filters(
        since_at=since_at,
        until_at=until_at,
        cli=_enum_or_none(args, "cli"),
        network=_enum_or_none(args, "network"),
        capture=_tri_bool(args, "capture"),
        telemetry=_tri_bool(args, "telemetry"),
    )


def admin_only(fn):
    @wraps(fn)
    def _wrapped(*a, **kw):
        if not g.user["is_admin"]:
            return jsonify(error="需要管理員權限"), 403
        return fn(*a, **kw)
    return _wrapped


def _owned(sid: str) -> dict:
    """取得 session 並確認擁有權（ADR 0005 authz）。

    非擁有者一律回 404 而非 403——不洩漏「這個 session 是否存在」。
    """
    s = manager.status(sid)
    if s["user_id"] != g.user["id"] and not g.user["is_admin"]:
        raise SessionNotFound(f"未知 session：{sid}")
    return s


# --- 認證 -------------------------------------------------------------------------

@app.post("/api/auth/login")
def login():
    body = _body()
    user = auth.authenticate(body.get("username"), body.get("password"))
    session.clear()
    session["uid"] = user["id"]
    session["pwv"] = user["password_version"]
    session.permanent = True
    return jsonify(user=user)


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return "", 204


@app.get("/api/auth/me")
def whoami():
    return jsonify(user=g.user)


@app.get("/api/auth/view")
def auth_view():
    """nginx `auth_request` 掛載點（ADR 0005 路由 B）。

    nginx 收到 /session/<id>/ 時先打這裡：驗 cookie（authn）+ 擁有權（authz），
    通過就回 200 並以 `X-Ttyd-Port` 告訴 nginx 要 proxy 到哪個 loopback port。
    """
    sid = request.args.get("session", "")
    try:
        session_info = _owned(sid)
    except SessionError:
        return "", 403
    alive = views.list_views(sid)
    if not alive:
        # 沒有存活的 view 時**當場重建**，而不是回 403。
        # 原因：ttyd 帶 `-q`（所有 client 斷線即自退），而「重新整理」正好會先關掉
        # WebSocket → ttyd 自殺 → 重整後的請求就找不到 view，使用者被踢回首頁。
        # 在這裡重建讓重整變成無感；終端內容不受影響（container 一直在跑，重新
        # docker attach 即可，畫面由 TUI 自行重繪——ADR 0003）。
        try:
            view = views.open_view(sid, session_info["container"],
                                   g.user.get("ttyd_bin"))
        except ViewError:
            return "", 403
        manager.touch(sid)
    else:
        view = alive[0]
    resp = app.make_response(("", 200))
    resp.headers["X-Ttyd-Port"] = str(view["port"])
    return resp


@app.get("/api/auth/check")
def auth_check():
    """ttyd-rust `--auth-url` 掛載點（第二層授權，與 nginx auth_request 是縱深）。

    **純判定、零副作用**：驗 cookie（authn 由 gate 做完）＋ 擁有權，然後回 204/403，
    不開 view、不回報 port、不碰 dockerd、不寫 DB。這支會被**每個 asset 與 WS 升級**
    各打一次（fork 端有 --auth-cache-ttl 壓頻率），熱路徑上任何副作用都會被放大——
    /api/auth/view 沒有存活 view 時會當場開一個，所以不能共用那支。

    授權答案只需要 DB 的擁有權事實，所以走 manager.peek（純 DB 讀）：不問 dockerd
    ——容器此刻的狀態不改變「這場是不是他的」。

    一律 403 不回 404：這支的消費者是 ttyd/nginx 這類只認 2xx/401/403 的守門者。
    對外它被 nginx 擋成 404（同 /api/auth/view 的理由，見 deploy/nginx.conf）。
    """
    sid = request.args.get("session", "")
    try:
        row = manager.peek(sid)
    except SessionError:
        return "", 403
    if row["user_id"] != g.user["id"] and not g.user["is_admin"]:
        return "", 403
    return "", 204


@app.get("/api/prefs")
def get_prefs():
    """這個人的偏好設定 + 每一項的合法選項（畫面直接照它畫，不必在前端複製白名單）。"""
    return jsonify(ttyd_bin=config.ttyd_bin_or_default(g.user.get("ttyd_bin")),
                   ttyd_choices=[{"value": k, "label": v} for k, v in config.TTYD_BINS.items()])


@app.patch("/api/prefs")
def set_prefs():
    """改偏好。body 目前只收 `ttyd_bin`。

    ⚠ `ttyd_bin` **只影響之後開的終端**：已經在跑的 ttyd 不會被換掉（換掉等於把正在看的
      畫面斷線）。關掉終端讓它自退、或終止該 session 之後，下一次開的就是新選的那一顆。
    ⚠ 值只收白名單內的：ttyd_bin 最終會變成 argv[0]。未知欄位一律 400，不默默忽略——
      「我設了但沒生效」比「你設錯了」難查得多（同 session 端點的既有紀律）。
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="需要 JSON 物件"), 400
    known = {"ttyd_bin"}
    unknown = set(body) - known
    if unknown:
        return jsonify(error=f"不認得的欄位：{', '.join(sorted(unknown))}"), 400
    if not set(body) & known:
        # 空物件不是「什麼都不改」而是「大概打錯了」——照既有紀律講出來，不要靜靜成功。
        return jsonify(error=f"至少要給一個欄位：{' / '.join(sorted(known))}"), 400
    # ⚠ **先把每一個欄位都驗完，再動任何一個**（欄位再多回來也一樣）：一邊驗一邊寫，
    #   「跟你說設錯了，其實一半設成功」比「設了沒生效」更難查。
    if "ttyd_bin" in body:
        value = body["ttyd_bin"]
        # ⚠ 先驗型別再查白名單：`x in dict` 會去 hash x，而 list/dict 不可 hash——直接查的話
        #   `{"ttyd_bin": ["ttyd"]}` 是 TypeError → 500。這條端點改得了 argv[0]，被畸形輸入
        #   打成 500 是不能接受的（同 login 端點那條既有紀律，測試抓到過）。
        if not isinstance(value, str) or value not in config.TTYD_BINS:
            return jsonify(error=f"ttyd_bin 只能是 {' / '.join(config.TTYD_BINS)}"), 400
    # --- 驗完了，這裡開始才會寫入 ---
    if "ttyd_bin" in body:
        auth.set_ttyd_bin(g.user["id"], body["ttyd_bin"])
    user = auth.get_user(g.user["id"])
    return jsonify(ttyd_bin=user["ttyd_bin"])


# --- 帳號管理 ----------------------------------------------------------------------

@app.get("/api/users")
@admin_only
def list_users():
    """?limit=&offset= 分頁，回傳形狀與 /api/sessions 一致。

    ⚠ 這條是**分頁**的，不要拿它去餵「要看到每一個人」的用途：超過一頁的人會靜靜地
      不見，而畫面上看不出少了誰。完整名單走 /api/users/options（欄位砍到最少）。
    """
    limit = _int_in(request.args, "limit", config.PAGE_SIZE, 1, config.MAX_PAGE_SIZE)
    offset = _int_in(request.args, "offset", 0, 0, 1_000_000)
    users, total = auth.page_users(limit, offset)
    return jsonify(users=users, total=total, limit=limit, offset=offset)


@app.get("/api/users/options")
@admin_only
def list_user_options():
    """帳號的**完整**名單，欄位只有 id 與 username。

    唯一的消費者是帳號頁「建立帳號後翻到他那一頁」（清單依名字排序又分頁，新帳號
    多半不在目前這一頁——建完看不到，跟建立失敗長得一模一樣）。算頁碼需要「他排第
    幾」，而那只有完整名單答得出來；分頁的 /api/users 給不了這個答案。

    刻意不分頁：這條的職責就是「任何一個人都找得到位置」。代價是回應隨帳號數線性
    成長——欄位砍到兩個就是為了讓代價夠小；真的大到有感時，該做的是後端搜尋，不是
    偷偷截斷。路由不會和 `/api/users/<int:uid>` 打架：那條的轉換器是 int。
    """
    return jsonify(users=[{"id": u["id"], "username": u["username"]}
                          for u in auth.list_users()])


@app.post("/api/users")
@admin_only
def create_user():
    """建立帳號。body: {"username", "password", "is_admin"?}

    `is_admin` 走嚴格解析（`_strict_bool`）：這是權限邊界，"true"/1/[] 這種
    「看起來像 true」的值一律 400，不做型別猜測。

    ⚠ 這是**唯一**能把帳號設成管理員的地方——沒有事後提權/降權的端點（見下方退場
      說明）。設錯了的救法：再建一個對的，然後把設錯那個的密碼改掉讓它退場。
    """
    body = _body()
    unknown = set(body) - {"username", "password", "is_admin"}
    if unknown:
        raise BadInput(f"不支援的欄位：{'、'.join(sorted(unknown))}")
    user = auth.create_user(
        body.get("username", ""), body.get("password", ""),
        is_admin=_strict_bool(body, "is_admin", False),
    )
    return jsonify(user=user), 201


def _cut_live_terminals(uid: int) -> None:
    """把某位使用者所有開著的終端 view 收掉。

    ⚠ **撤銷存取權時，收掉 cookie / token 是不夠的。** 那兩者要到**下一次 HTTP 請求**
      才會被 gate 擋下，而已經升級完成的 ttyd WebSocket 不會再走 nginx 的 auth_request
      ——連線活著的期間，對方手上就是一個可互動的 shell，撤銷對它完全沒有效果。

    ⚠ session 本身不動：這是「切斷存取」，不是「終止工作」。容器繼續跑，重開網頁就會起
      一個新的 ttyd（ADR 0003：不重播，畫面由 TUI 自行重繪），代價幾乎是零。

    ⚠ **涵蓋範圍是「他擁有的 session」，不是「他開著的終端」**——這兩者在 admin 身上會
      分岔：admin 開得了別人的 session，而 view 是掛在 session 上、不記得是誰在看。所以
      「改掉一位 admin 的密碼」目前收不掉他正開著的、屬於別人的終端。要補得先讓 view
      記錄開啟者，那是 schema 變更；在那之前這是已知的缺口，不要以為改密碼等於全斷。
    """
    for s in manager.list(user_id=uid):
        with suppress(Exception):
            views.close_views(s["id"])


# ⚠ **沒有刪除帳號的端點，也沒有「停用」，這是刻意的**（ADR 0010）。
#   刪除是不可逆的，還會沿 FK cascade 掉他的 `sessions` 登錄——容器仍在跑卻沒人追蹤，
#   那段歷史也沒經過 archive 就消失了。稽核價值正是這個系統保留 `session_history` 的
#   理由，開一條能抹掉歸屬的路徑與它直接衝突。代價是使用者名稱不可回收（UNIQUE）。
#   退場的做法是**管理員改掉他的密碼**（admin_change_password）：cookie 全滅、終端全收、
#   新密碼他不知道，效果與停用相同而少維護一個狀態——詳見 auth.py 尾段的說明。


@app.post("/api/users/me/password")
def change_own_password():
    """改自己的密碼。**這個帳號現在連著的東西，全部斷掉——包含操作中的這一台。**

    改密碼會遞增 password_version，所有既有 cookie 當場失效。**不為「按下送出的這一台」
    留特例**：多一個例外就多一件要記得的事，而它換到的只是少按幾個鍵。要繼續用就重新
    登入，那本來就是剛才那個動作的意思。

    ⚠ **但 cookie 不是真正的問題。** 版本號對一條**已經升級完成的 WebSocket 沒有任何
      效果**——授權只發生在 nginx 把連線交給 ttyd 之前，之後那條線就是一條線，不會再有
      人回頭問它還算不算數。所以就算把 cookie 全部作廢，一個已經打開的終端分頁**還是一個
      能打字的 shell**。換密碼的理由如果是「我懷疑被盜了」，不收那條線等於沒做。
      收的動作在 `_cut_live_terminals`。

    ⚠ 不必分辨「哪一條連線屬於哪一台裝置」——伺服端本來也分不出來，而既然登入狀態
      全部作廢了，終端就全部收。全部登出與全部切斷是同一個決定的兩半。
      容器不受影響：重新登入、重開抽屜，接回的還是同一場。
    """
    body = _body()
    _reject_unknown(body, {"old_password", "new_password"})
    auth.change_password(g.user["id"], body.get("new_password", ""),
                         old_password=body.get("old_password"), require_old=True)
    _cut_live_terminals(g.user["id"])
    # ⚠ 這一張 cookie 也作廢：session 清掉，下一個請求就會被 gate 送回登入頁。
    #   不清的話它會帶著舊版號活到下一次請求才被擋，中間那段是說一套做一套。
    session.clear()
    return "", 204


@app.put("/api/users/me/token")
def set_own_token():
    """存自己的 CLI 憑證（`claude setup-token` 的輸出）。body: {"token": "..."}

    PUT 語意：重貼就是整個換掉，沒有「部分更新」。存進去的值**不再吐回來**——GET 只有
    「已設定／未設定」（見 credentials_state），要用新的就再貼一次。
    """
    body = _body()
    _reject_unknown(body, {"token"})
    auth.set_cli_token(g.user["id"], body.get("token"))
    return "", 204


@app.delete("/api/users/me/token")
def clear_own_token():
    """清掉自己的 CLI 憑證。之後開新 session 會被擋（引導重新設定）；已在跑的不受影響。"""
    auth.clear_cli_token(g.user["id"])
    return "", 204


@app.post("/api/users/<int:uid>/password")
@admin_only
def admin_change_password(uid: int):
    """管理員代改密碼（免舊密碼）。三種情境共用這一條：忘記密碼、帳號被盜、**退場**。

    退場＝改掉他的密碼，就這樣。cookie 因版號遞增全部失效、下面那行把他開著的終端
    切斷、新密碼他不知道——三件合起來等價於「停用」，而少維護一個狀態（見 auth.py
    尾段）。要讓他回來，把新密碼告訴他即可。

    ⚠ 只換掉密碼而不切斷已連上的終端，等於重設完之後對方還握著 shell——cookie 的
      版號管不到一條已經升級完成的 WebSocket（見 _cut_live_terminals）。
    """
    body = _body()
    _reject_unknown(body, {"new_password"})
    auth.change_password(uid, body.get("new_password", ""), require_old=False)
    _cut_live_terminals(uid)
    return "", 204


# --- session ----------------------------------------------------------------------

@app.post("/api/sessions")
def create_session():
    """建立 session。body: {"name"?, "rows"?, "cols"?, "profile"?}
    profile（ADR 0006）：{"network": "restricted|unrestricted",
    "capture": bool, "telemetry": bool, "model"?, "effort"?}，未給則用 server 預設。"""
    body = _body()
    _reject_unknown(body, {"name", "rows", "cols", "profile"})
    session_info = manager.create(
        rows=_int_in(body, "rows", config.DEFAULT_ROWS, 1, 500),
        cols=_int_in(body, "cols", config.DEFAULT_COLS, 1, 1000),
        profile=Profile.from_dict(_clean_profile(body.get("profile"))),
        user_id=g.user["id"],
        display_name=_clean_display_name(body),
    )
    return jsonify(session_info), 201


@app.get("/api/sessions")
def list_sessions():
    """?limit=&offset= 分頁。admin 看全部，一般使用者只看自己的。

    `total` 刻意在 list 之後才算：list 會把已消失的 container 從登錄清掉，先算會多報。

    `credentials` 搭列表的順風車回去（見 list_history 的同一個決定）。
    """
    uid = None if g.user["is_admin"] else g.user["id"]
    limit = _int_in(request.args, "limit", config.PAGE_SIZE, 1, config.MAX_PAGE_SIZE)
    offset = _int_in(request.args, "offset", 0, 0, 1_000_000)
    filters = _filters_from_args()
    items = manager.list(user_id=uid, limit=limit, offset=offset, filters=filters)
    # ⚠ count 要吃同一組 filters，否則總筆數多報、頁碼算錯、最後一頁是空白
    return jsonify(sessions=items, total=manager.count(user_id=uid, filters=filters),
                   limit=limit, offset=offset,
                   credentials=sessions_mod.credentials_state(g.user["id"]))


@app.get("/api/sessions/history")
def list_history():
    """已結束 session 的永久紀錄（ADR 0010）。分頁同 /api/sessions。

    與 `/api/sessions/<sid>` 不會打架：werkzeug 依規則的「複雜度」排序而非註冊順序，
    純靜態規則永遠贏過帶轉換器的，所以這個 handler 放哪裡都會優先命中。

    ⚠ `credentials` 兩個列表端點都要帶：管理畫面每 15 秒只打「目前這一頁」對應的那一個，
    只加在 /api/sessions 的話，切到歷史紀錄分頁後憑證徽章就從此停在載入當下的狀態。
    順著列表回去而不是另開一支端點，是為了不多一個輪詢計時器與一份錯誤處理。
    """
    uid = None if g.user["is_admin"] else g.user["id"]
    limit = _int_in(request.args, "limit", config.PAGE_SIZE, 1, config.MAX_PAGE_SIZE)
    offset = _int_in(request.args, "offset", 0, 0, 1_000_000)
    items, total = manager.history(user_id=uid, limit=limit, offset=offset,
                                   filters=_filters_from_args())
    return jsonify(sessions=items, total=total, limit=limit, offset=offset,
                   credentials=sessions_mod.credentials_state(g.user["id"]))


@app.get("/api/sessions/<sid>")
def get_session(sid: str):
    """單一 session 的完整狀態，含 `ready`。

    `?wait_ready=<秒>` 會阻塞到就緒（或逾時）才回應——**等待是這支端點的參數，不是
    另一支端點**：「就緒」是 session 的一個狀態欄位，不是一個獨立的動詞；拆成
    /ready 只會讓同一份事實有兩個來源。逾時不算錯誤，照樣回傳當下狀態，ready 欄位
    自己會說話。

    ⚠ 上限是 `config.WAIT_READY_MAX`（180 秒），不是隨手取的 600。這個參數會把請求整段
      釘在 gunicorn 的一條執行緒上（`--threads 8`），八個就能把控制平面佔滿——連 nginx 的
      `auth_request` → `/api/auth/view` 都排不進去，等於所有人開著的終端一起失效。
      所以預算收成「整個請求」而不是「每次輪詢」。
    """
    _owned(sid)
    wait = request.args.get("wait_ready")
    if wait is None:
        return jsonify(manager.status(sid, with_ready=True))
    seconds = _int_in(request.args, "wait_ready", 0, 0, int(config.WAIT_READY_MAX))
    return jsonify(manager.wait_until_ready(sid, seconds))


@app.patch("/api/sessions/<sid>")
def rename_session(sid: str):
    """改顯示名稱。body: {"name": "..."}；空字串或 null＝取消命名，改回顯示 sid。

    只動顯示名稱，**不改 container 名稱**：`docker rename` 與 DB 更新之間有個窗口，
    reconciler 若剛好在那時對帳，會因為找不到舊名而把這個 session 判成消失並刪掉登錄。
    """
    _owned(sid)
    body = _body()
    _reject_unknown(body, {"name"})
    return jsonify(manager.rename(sid, _clean_display_name(body)))


@app.delete("/api/sessions/<sid>")
def delete_session(sid: str):
    _owned(sid)
    # 帶上是誰按的：admin 終止得了別人的 session，而本系統不做租戶隔離——
    # 沒有這個線索，「我的 session 為什麼不見了」永遠查不到（見 SessionHistory）。
    manager.terminate(sid, actor=g.user)
    return "", 204


@app.post("/api/sessions/<sid>/view")
def open_view(sid: str):
    """開一個 on-demand 終端 view（ADR 0008）：起 ttyd -q 並回傳 URL。

    已有存活的 view 就沿用（點兩次不會多起一個）。關掉網頁後 ttyd 會因 `-q` 自行退出，
    無需呼叫端做任何回收。
    """
    s = _owned(sid)
    # ⚠ 開之前先確認 container 還在。少了這一步，對一顆已經死掉的 container 起 ttyd 是
    #   **會成功的**（ttyd 照樣綁 port），使用者拿到的是一片黑畫面加一行英文
    #   `Press ⏎ to Reconnect`，而列表還會繼續說它「執行中」直到對帳器那一輪（最久 30 秒）。
    #   在終端裡按 Ctrl+D 結束 CLI 之後再開一次，撞到的就是這個。
    #   問不到（dockerd 忙／逾時）回 None＝照常開，見 probe_container 的 fail-open。
    state = manager.probe_container(sid, s["container"])
    if state is not None and state not in sessions_mod.ALIVE_STATES:
        # 409 不是 404：session 的**登錄**還在（歷史、/resume 的錨點都在），只是那顆
        # container 不在了。訊息要說得出「所以我現在該做什麼」。
        return jsonify(
            error="這個 session 的 container 已經結束了（可能在終端裡按了 Ctrl+D 或被終止），"
                  "沒有終端可以開。對話沒有消失——建一場新的 session 再用 /resume 接回來。",
            docker_state=state), 409
    view = views.open_view(sid, s["container"], g.user.get("ttyd_bin"))
    manager.touch(sid)
    return jsonify(view), 201


@app.get("/api/sessions/<sid>/view")
def list_session_views(sid: str):
    _owned(sid)
    return jsonify(views=views.list_views(sid))


@app.delete("/api/sessions/<sid>/view")
def close_session_views(sid: str):
    """提前收掉 view（正常關網頁不需要——ttyd 會自己退）。"""
    _owned(sid)
    return jsonify(closed=views.close_views(sid))


@app.post("/api/sessions/<sid>/resize")
def resize_session(sid: str):
    _owned(sid)
    body = _body()
    # redraw：套完尺寸後再強迫 TUI 整個重畫一次（見 SessionManager._nudge_redraw）。
    # 開啟終端時尺寸常常與上次相同，那樣不會有 SIGWINCH，TUI 就沿用上一次的版面。
    manager.resize(sid, _int_in(body, "rows", config.DEFAULT_ROWS, 1, 500),
                   _int_in(body, "cols", config.DEFAULT_COLS, 1, 1000),
                   redraw=_as_bool(body.get("redraw"), False))
    return "", 204


def main() -> None:
    """本機開發用。正式部署走 gunicorn（見 deploy/Dockerfile）：
        gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 8 server.app:app
    """
    print("[claude-pty] ⚠ 使用 Flask 開發伺服器；正式部署請用 gunicorn", flush=True)
    app.run(host=config.CONTROL_HOST, port=config.CONTROL_PORT, threaded=True)


if __name__ == "__main__":
    main()
