"""畫面啟動資料 `/api/bootstrap` 與 `/api/account/bootstrap`（前端改 SPA 的階段 3）。

這兩條的存在理由是「模板不要再靠 Jinja 注入伺服端狀態」，所以最重要的不是形狀好不好看，
而是**它給的值真的來自 config／DB，而不是某處抄了一份**。

⚠ **分界線是 gate，不是頁面**，而 2026-08-26（裁示 L4）把線往回挪過一次：版號（`build`）
  與主機路徑（`persist_dir`）從公開那條搬進要登入那條。所以這支現在有兩組對稱的斷言：
  「搬走的那些在新家、值沒變」，以及「公開那條的**完整回應字串**裡一個字都找不到它們」
  後者拿**實際的值**去找，不只找欄位名：換個鍵名放回去仍然是同一個洩漏。

Flask test client，不需 docker。
    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        python tests/test_bootstrap.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="claude-pty-bootstrap-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["CLAUDE_PTY_SECRET_KEY"] = "test-secret-key-not-for-production"

from server import auth, config, db, version, web  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()
db.init_db()

from server.app import app  # noqa: E402

app.config["TESTING"] = True
_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


# 名字取得夠特別，才驗得出「別人的資料有沒有混進我的 payload」：用 admin / alice 這種
# 常見字，比對時會被說明文字裡的同名字串蒙混過去。
ADMIN = "bootadmin"
ALICE = "bootalice"
auth.create_user(ADMIN, "bootstrap-admin-pw-1", is_admin=True)
_alice = auth.create_user(ALICE, "bootstrap-alice-pw-1")

anon = app.test_client()
ca = app.test_client()
ca.post("/api/auth/login", json={"username": ADMIN, "password": "bootstrap-admin-pw-1"})
c = app.test_client()
c.post("/api/auth/login", json={"username": ALICE, "password": "bootstrap-alice-pw-1"})


print("== gate：公開那條真的公開，要登入那條真的 401 ==")
r = anon.get("/api/bootstrap")
check("未登入 GET /api/bootstrap → 200（刻意公開，見 _PUBLIC_ENDPOINTS 的說明）", r.status_code == 200)
check("回的是 JSON", r.is_json)
# ⚠ 這條回的兩件事都會過期：login_art 每次要換一張，build 在改版後必須跟著變。
#   它又是公開的 GET，中間任何一層都可能想順手存一份。
check("公開那條帶 Cache-Control: no-store", r.headers.get("Cache-Control") == "no-store")
r401 = anon.get("/api/account/bootstrap")
check("未登入 GET /api/account/bootstrap → 401", r401.status_code == 401)
check("401 是 JSON 不是導向登入頁（/api/* 的既有語意）", r401.is_json and "error" in r401.get_json())
check("已登入 GET /api/account/bootstrap → 200", c.get("/api/account/bootstrap").status_code == 200)


print("== /api/bootstrap：形狀與值 ==")
boot = anon.get("/api/bootstrap").get_json()
check(
    f"欄位剛好兩個（得到 {sorted(boot)}）",
    set(boot) == {"behind_proxy", "login_art"},
)
check("behind_proxy 是布林不是 0/1 字串", isinstance(boot["behind_proxy"], bool))

_orig_bp = config.BEHIND_PROXY
config.BEHIND_PROXY = False
check("未在 proxy 後 → behind_proxy False", anon.get("/api/bootstrap").get_json()["behind_proxy"] is False)
config.BEHIND_PROXY = True
check("在 proxy 後 → behind_proxy True", anon.get("/api/bootstrap").get_json()["behind_proxy"] is True)
config.BEHIND_PROXY = _orig_bp

if web.LOGIN_ART:
    _arts = {f"/static/images/{a}" for a in web.LOGIN_ART}
    check(f"login_art 指向 static/images 底下真的存在的圖（{boot['login_art']}）", boot["login_art"] in _arts)
    # 「每次載入換一張」是這張圖的行為，不是啟動時定案的設定，所以這條不可以被快取。
    _draws = {anon.get("/api/bootstrap").get_json()["login_art"] for _ in range(40)}
    check(
        f"多次呼叫會重挑（{len(_draws)} 種／共 {len(_arts)} 張）",
        len(_arts) == 1 or len(_draws) > 1,
    )
else:
    check("沒有插畫時 login_art 是 null（不是空字串）", boot["login_art"] is None)

# ⚠ 公開端點的把關只有一條：**要登入才看得到的東西，一個字都不准出現在這裡。**
_pub_raw = anon.get("/api/bootstrap").get_data(as_text=True)
for leaked in ("credentials", "limits", "gitlab", "username", "is_admin", "cli_token"):
    check(f"公開 payload 不含要登入才看得到的 {leaked!r}", leaked not in _pub_raw)


print("== 公開端點不得洩漏版號與主機路徑（2026-08-26 裁示 L4）==")
#
# ⚠ 這一節**拿實際的值去找，不只找欄位名**。只找 `"build"`／`"persist_dir"` 這種鍵名的話，
#   有人把同一批值換個名字放回去（`meta`、`env`、`info`…）這裡照樣是綠的，而洩漏的東西
#   一個字都沒有少。要守的是「**那些值**不准出現」，所以判準必須是值本身。
# ⚠ 也一起找鍵名：鍵名還在代表有人把欄位加回來了，那是同一件事的另一種形狀。
_leak_raw = anon.get("/api/bootstrap").get_data(as_text=True)
_mods = version.summary()["modules"]

for _key in ("persist_dir", "build", "modules", "built_at", "commit", "version"):
    check(f"公開 payload 不含欄位名 {_key!r}", _key not in _leak_raw)

# 主機路徑：宿主機上的一個絕對路徑，本身就是偵察素材（使用者名稱、部署佈局）。
check(
    f"公開 payload 不含 persist_dir 的**值**（{config.DATA_BIND!r}）",
    bool(config.DATA_BIND) and config.DATA_BIND not in _leak_raw,
)

# 版號與 commit：逐一拿真的值去找。ttyd 的版本字串也在這一批裡（它是其中一個模組）。
# ⚠ 跳過空值：`"" in s` 恆真，留著會讓這幾條變成無條件紅（而不是無條件綠，但一樣沒在守
#   任何東西）。真的一個值都驗不到時下面那條 sanity 會紅。
_checked_values = 0
for _m in _mods:
    for _field in ("version", "commit", "built_at", "detail"):
        _val = _m.get(_field)
        if not isinstance(_val, str) or not _val:
            continue
        _checked_values += 1
        check(
            f"公開 payload 不含 {_m['name']} 的 {_field}（{_val!r}）",
            _val not in _leak_raw,
        )
    check(f"公開 payload 不含模組名 {_m['name']!r}", _m["name"] not in _leak_raw)
# ⚠ 沒有這一條的話，`version.summary()` 哪天回一份空的（或欄位全是 None），上面那個迴圈
#   會一條都不跑、一條都不紅，而這一節看起來仍然是綠的。
check(f"上面真的驗到了東西（{_checked_values} 個非空的版本字串）", _checked_values >= 2)

# 反向：搬過去的那一份**真的有人給**，否則畫面會少一塊而這裡還是綠的。
_acct_boot = c.get("/api/account/bootstrap").get_json()
check(
    "persist_dir 搬到要登入那條，而且就是 config.DATA_BIND（抽屜標題列的 SSOT）",
    _acct_boot["persist_dir"] == config.DATA_BIND,
)
check("build.modules 搬到要登入那條，就是 version.summary() 那一份", _acct_boot["build"]["modules"] == _mods)
check(
    "每一列自己說得出 name/version/commit/built_at/detail（畫面只負責畫）",
    all(set(m) >= {"name", "version", "commit", "built_at", "detail"} for m in _acct_boot["build"]["modules"]),
)
# ⚠ built_at 提到最外層是刻意的：它是**整包**的屬性。留在第一列裡的話，前端遲早會有人
#   把它畫成「claude-pty 這一列的建置時間」，而那句話對其他列都不成立。
check(
    "build.built_at 提到最外層（整包的屬性，不屬於任何一個模組）",
    _acct_boot["build"]["built_at"] == _mods[0]["built_at"],
)


print("== /api/account/bootstrap：形狀與值 ==")
acct = c.get("/api/account/bootstrap").get_json()
check(
    f"頂層欄位剛好七個（得到 {sorted(acct)}）",
    set(acct) == {"user", "default_cli", "credentials", "limits", "gitlab", "persist_dir", "build"},
)

# ── user：與 /api/auth/me 是同一個出口 ────────────────────────────────────────
#
# 合進來的理由是「冷載入不要為了同一個畫面往返兩次」（見 app.account_bootstrap 的說明）。
# 而合併最容易壞的方式是**在這裡另外拼一份**：兩份一開始長得一樣，某天 `_to_dict` 加了
# 一個欄位而這裡沒跟上，畫面就變成「有一邊怪怪的」，沒有任何東西會紅。
# ⚠ 所以這一條比的是**逐欄相同**，不是「有 user 這個鍵」。
me = c.get("/api/auth/me").get_json()["user"]
_u = acct["user"]
_only_me = {k: me[k] for k in me if k not in _u or _u[k] != me[k]}
_only_acct = {k: _u[k] for k in _u if k not in me}
check(
    f"user 與 /api/auth/me 逐欄相同（只在 me：{_only_me or '無'}；只在 bootstrap：{_only_acct or '無'}）",
    _u == me,
)
# ⚠ 這條看起來像上一條的重複，其實守的是另一件事：上一條在兩邊**都少掉**同一個欄位時
#   仍然是綠的（兩個空字典也相等）。所以這裡獨立釘住「該有的欄位真的在」。
check(
    f"user 的欄位齊全（得到 {sorted(_u)}）",
    set(_u) == {"id", "username", "is_admin", "password_version", "ttyd_bin", "gitlab_pat_configured", "created_at"},
)
# ⚠ 多一個出口就是多一個洩漏面。`_to_dict` 只給「設過沒」這個布林，明文與密文都不出去
#   （那條規矩守在 auth._to_dict 那一行），這裡再確認一次它沒有被繞過。
check("PAT 只給狀態不給值（布林）", isinstance(_u["gitlab_pat_configured"], bool))
check(
    f"整份回應裡沒有任何密文或雜湊欄位（得到 {sorted(_u)}）",
    not any(k.endswith("_enc") or "password_hash" in k for k in _u),
)

check("default_cli 讀 config.DEFAULT_CLI（不是第二份寫死的字面量）", acct["default_cli"] == config.DEFAULT_CLI)
check(
    "credentials 以 cli 為鍵（形狀與 /api/sessions 搭順風車那份相同）",
    set(acct["credentials"]) == {config.DEFAULT_CLI},
)
# ⚠ 上面那條單獨看是**恆真的**：兩邊都讀同一個常數，等於拿它自己比自己。真正要守的是
#   「四個地方（字典鍵、狀態裡的 cli、default_cli、招牌的 data-cli）同源」，所以把常數
#   換成別的值跑一次——有任何一處是寫死的 "claude"，這裡就會紅。
_orig_cli = config.DEFAULT_CLI
try:
    config.DEFAULT_CLI = "not-claude"
    _probe = c.get("/api/account/bootstrap").get_json()
    check("換掉 config.DEFAULT_CLI → default_cli 跟著走", _probe["default_cli"] == "not-claude")
    check(
        "換掉 config.DEFAULT_CLI → credentials 的鍵跟著走（鍵不是寫死的 claude）",
        set(_probe["credentials"]) == {"not-claude"},
    )
    # ⚠ 用 `.get()` 不用下標：上一條紅掉時鍵就不存在，下標會讓整支測試當場 KeyError 收攤，
    #   後面幾十條一條都跑不到，診斷從「這兩條錯了」退化成「它爆了」。
    check(
        "換掉 config.DEFAULT_CLI → 憑證狀態裡的 cli 也跟著走（不是 import 時抄的那份）",
        _probe["credentials"].get("not-claude", {}).get("cli") == "not-claude",
    )
    # ⚠ 第四處原本是 `_masthead.html` 的 `data-cli`（模板讀同一個常數）。模板刪了之後，
    #   第四處變成**招牌那個 Vue 元件有沒有真的去讀 API 給的值**——如果它自己寫死 "claude"，
    #   上面三條照樣全綠，而換掉常數之後畫面會查不到憑證狀態、徽章一片空白。
    #   這一條是靜態的（讀原始碼），因為那個查表發生在瀏覽器裡，伺服端問不到。
    _masthead = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend",
        "src",
        "components",
        "AppMasthead.vue",
    )
    _m_src = open(_masthead, encoding="utf-8").read() if os.path.isfile(_masthead) else ""
    check(
        "招牌用的是 API 給的 default_cli，不是自己寫死的字面量",
        "store.meta.defaultCli" in _m_src and '"claude"' not in _m_src and "'claude'" not in _m_src,
    )
finally:
    # ⚠ 一定要 finally：上面任何一條炸掉都不可以讓後面幾十條對著一個被改壞的常數跑。
    config.DEFAULT_CLI = _orig_cli
check("還原之後回到原值", c.get("/api/account/bootstrap").get_json()["default_cli"] == _orig_cli)
check(
    "每份憑證狀態自己說得出 cli/brand/ok/state/label/detail（畫面不挑圖示、不拼文案）",
    set(acct["credentials"][config.DEFAULT_CLI]) >= {"cli", "brand", "ok", "state", "label", "detail"},
)
check(
    "limits 三個值都來自 config",
    acct["limits"]
    == {
        "name_max": config.NAME_MAX,
        "username_max": config.USERNAME_MAX,
        "min_password_length": config.MIN_PASSWORD_LENGTH,
    },
)
check(
    f"gitlab 欄位剛好三個（得到 {sorted(acct['gitlab'])}）",
    set(acct["gitlab"]) == {"enabled", "host", "proxy_error"},
)


print("== 不重複既有出口：兩個真相來源就是遲早分岔 ==")
#
# ⚠ 這一節的判準是「**有沒有第二個真相來源**」，不是「有沒有出現這個字」。
#   `user` 現在**是**在這裡（2026-08-26 的裁示：SPA 冷載入不要為了同一個畫面往返兩次），
#   但它不是第二份，它就是 `/api/auth/me` 回的同一個 `g.user`，上面那條「逐欄相同」
#   釘住了這件事。所以這三條改成守它們真正在守的東西：
#     · user 在，但必須與 /api/auth/me 同源（上面已驗）；
#     · gitlab_pat_configured **只能活在 user 裡**，不可以在頂層再放一份；
#     · ttyd 的**選項清單**仍然只屬於 /api/prefs（user.ttyd_bin 是這個人自己的值，同源）。
_acct_raw = c.get("/api/account/bootstrap").get_data(as_text=True)
check("使用者本人在（冷載入不必再打一發 /api/auth/me）", "user" in acct)
check(
    "PAT 設過沒只活在 user 裡，頂層沒有第二份",
    "gitlab_pat_configured" in acct["user"] and "gitlab_pat_configured" not in set(acct) - {"user"},
)
check("不夾帶模型清單（那是 /api/catalog）", "models" not in _acct_raw and "claude_models" not in _acct_raw)
check("不夾帶 ttyd 的選項清單（那是 /api/prefs；user.ttyd_bin 是這個人自己的值）", "ttyd_choices" not in _acct_raw)
# 反向：委派出去的東西必須真的有人給，否則 SPA 會少一塊而這裡還是綠的。
_me = c.get("/api/auth/me").get_json()["user"]
check("/api/auth/me 給得出 gitlab_pat_configured（委派的對象真的在）", "gitlab_pat_configured" in _me)
check("/api/auth/me 給得出 is_admin（管理員區塊畫不畫靠它）", "is_admin" in _me)
check("/api/catalog 給得出模型清單", bool(ca.get("/api/catalog").get_json()["claude"]["models"]))


print("== 權限：形狀不隨角色改變，也不夾帶別人的東西 ==")
acct_admin = ca.get("/api/account/bootstrap").get_json()
check("管理員拿到 200", ca.get("/api/account/bootstrap").status_code == 200)
# ⚠ 刻意讓形狀一致（含只印在管理員區塊裡的 username_max）：它是表單長度常數，不是誰的
#   資料，而形狀依角色而異會把一個 undefined 分支推給每一個取用它的地方。真正該 gate 的
#   是「那個區塊畫不畫」，答案在 /api/auth/me 的 is_admin。
check("管理員與一般使用者的頂層欄位相同", set(acct_admin) == set(acct))
check("limits 也相同（刻意：長度常數不是誰的資料）", acct_admin["limits"] == acct["limits"])
check(f"一般使用者的 payload 不含管理員的名字（{ADMIN}）", ADMIN not in _acct_raw)
# ⚠ 查 `_acct_raw`（整段 JSON 字串）而不是 `acct`（頂層鍵）：查頂層鍵的話這六條是**恆真的**
#   ——沒有人會把 users 放在頂層，真要漏也是漏在巢狀結構裡，而那正是查不到的地方。
for admin_only in ("users", "total", "orphans", "views", "pid", "port"):
    check(
        f"payload 一個字都不夾帶管理員限定的 {admin_only!r}（在 /api/users 與 /api/ttyd/inspect）",
        admin_only not in _acct_raw,
    )


print("== 憑證狀態：跟著 DB 走，而且明文一個字都不出去 ==")
_TOKEN = "sk-ant-oat01-bootstrap-secret-value-do-not-leak"
auth.set_cli_token(_alice["id"], _TOKEN)
_st = c.get("/api/account/bootstrap").get_json()["credentials"][config.DEFAULT_CLI]
check("設了 token → ok True / state ok", _st["ok"] is True and _st["state"] == "ok")
_raw = c.get("/api/account/bootstrap").get_data(as_text=True)
check("token 明文不在回應裡", _TOKEN not in _raw)
check("token 的任何一段都不在回應裡", "sk-ant-oat01" not in _raw and "do-not-leak" not in _raw)
auth.clear_cli_token(_alice["id"])
_st = c.get("/api/account/bootstrap").get_json()["credentials"][config.DEFAULT_CLI]
check("清掉 token → ok False / state bad", _st["ok"] is False and _st["state"] == "bad")
check("未設定時 detail 講得出下一步（setup-token）", "setup-token" in _st["detail"])


print("== GitLab：總開關、主機名、代理錯誤 ==")
_orig_host = config.GITLAB_HOST
config.GITLAB_HOST = ""
_gl = c.get("/api/account/bootstrap").get_json()["gitlab"]
check("沒設主機 → enabled False", _gl["enabled"] is False)
check("沒設主機 → host 是 null 不是空字串（空字串在畫面上與「設了一個空主機」長得一樣）", _gl["host"] is None)

config.GITLAB_HOST = "gitlab.bootstrap.test"
_gl = c.get("/api/account/bootstrap").get_json()["gitlab"]
check("設了主機 → enabled True", _gl["enabled"] is True)
check("host 原樣回傳（帳號頁那段說明要印它）", _gl["host"] == "gitlab.bootstrap.test")
check("代理沒出事時 proxy_error 是 null", _gl["proxy_error"] is None)

_ERR = 'host not found in upstream "gitlab.bootstrap.test"'
with db.session_scope(immediate=True) as _s:
    from server.models import User as _User

    _s.get(_User, _alice["id"]).gitlab_proxy_error = _ERR
_gl = c.get("/api/account/bootstrap").get_json()["gitlab"]
# ⚠ 原樣回傳、不改寫：這句話是 nginx 自己說的，改寫過的版本會把人導向錯的排查方向。
check("代理連續起不來 → proxy_error 原樣回傳", _gl["proxy_error"] == _ERR)
check(
    "別人的代理錯誤不會跑到管理員身上（這是 per-user 的事實）",
    ca.get("/api/account/bootstrap").get_json()["gitlab"]["proxy_error"] is None,
)

config.GITLAB_HOST = ""
_gl = c.get("/api/account/bootstrap").get_json()["gitlab"]
# ⚠ 功能關掉時整塊 UI 都不畫（模板的 `{% if gitlab_enabled %}`），所以這裡連查都不查。
check("功能關掉後 proxy_error 一律 null（DB 裡還留著也一樣）", _gl["proxy_error"] is None)

config.GITLAB_HOST = "gitlab.bootstrap.test"
_PAT = "glpat-bootstrapSECRETvalue1234"
auth.set_gitlab_pat(_alice["id"], _PAT)
_raw = c.get("/api/account/bootstrap").get_data(as_text=True)
check("PAT 明文不在回應裡", _PAT not in _raw)
check("PAT 的任何一段都不在回應裡", "glpat-" not in _raw and "SECRET" not in _raw)
check("公開端點更不可能有", _PAT not in anon.get("/api/bootstrap").get_data(as_text=True))
auth.set_gitlab_pat(_alice["id"], "")
config.GITLAB_HOST = _orig_host


# ⚠ 這裡曾經有一整節「對照模板：API 給的值與頁面此刻印的是同一個」（約八十行，逐條把
#   `data-behind-proxy`、`maxlength`、`MIN_PW`、`#cred-data`、`isAdmin`、`CLAUDE_MODELS`、
#   登入頁插畫、GitLab 那兩個值……從渲染出來的 HTML 挖出來，跟這兩條 API 的回傳比對）。
#
#   **它是遷移期的鷹架，2026-08-26 隨模板一起退場。** 它存在的理由寫在原本的節頭：
#   「階段 4 把模板換成 Vue 之後，1:1 還原的前提就是這些值同源」——也就是說，它守的是
#   「舊畫面印的」與「新 API 給的」不可以分岔。模板刪掉之後那兩者只剩一個，沒有東西可以
#   分岔，那一節就變成拿 API 跟自己比。
#
#   接手的是誰要講清楚，否則這看起來像「刪掉一節就沒人守了」：
#     · **值對不對** → 上面那些「形狀與值」的斷言（讀 config／DB，不讀畫面）。
#     · **畫面照著畫** → `golden_check`：十八個場景的 aria 與 DOM 快照。遷移期它是照著
#       legacy 錄的、拿 Vue 版去對；`d79c87b` 之後已經重錄成 Vue 版**自身**的特徵快照，
#       守的從「與舊版等價」變成「不要回歸」。涵蓋的比逐個欄位挖 HTML 廣（版面與可及
#       名稱都在內）。

print("== 清理 ==")
db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
check("暫存 DB 已清除", not os.path.exists(_tmp))

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
