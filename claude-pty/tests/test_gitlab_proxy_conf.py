"""per-user GitLab 代理的**離線**性質（ADR 0016）：設定產生、PAT 邊界、session 那一端。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography python tests/test_gitlab_proxy_conf.py

完全不碰 docker daemon（真的建容器那些在 test_user_proxy.py）。

守的性質：
  🔴 **PAT 不進 session 容器**——環境變數、掛載、labels、command 全部找不到它。
     這是整套設計的前提：容器裡的 AI 用得到 GitLab，但拿不走鑰匙。
  🔴 授權標頭**不在 server 層**。放上去會被所有 location 繼承，git 那條會收到它看不懂的
     標頭而全部 401。這是獨立版那份 template 的註解預告過的坑。
  🔴 PAT 的字元集擋在入口。它會被寫進 nginx 設定的雙引號字串裡——`"`、`\\`、`$` 分別能
     結束字串、逃逸、展開成變數，也就是設定檔注入。
  🔴 沒設 GitLab 主機時整個功能關閉，而且**不留半套**（不改寫 git URL、不注入 API base）。
  🔴 git URL 改寫涵蓋 https 與**兩種 SSH 寫法**，且 https 那條錨死結尾斜線
     （少了它，`https://<你的 host>.evil.example/…` 會被導進代理並蓋上真的 PAT）。
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="glproxy-test-")
os.environ["CLAUDE_PTY_DB_PATH"] = os.path.join(TMP, "t.db")
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
# 這支測試自己決定 GitLab 主機，不受跑測試那台機器的 .env 影響。
os.environ["CLAUDE_PTY_GITLAB_HOST"] = "gitlab.example.com"

from server import config  # noqa: E402

config.DB_PATH = os.environ["CLAUDE_PTY_DB_PATH"]
config.DB_URL = f"sqlite:///{config.DB_PATH}"
config.SECRET_KEY = "gitlab-proxy-test-secret"
config.HOST_HOME = os.path.join(TMP, "home")

from server import auth, crypto, db, gitlab_proxy, sessions, user_proxy  # noqa: E402
from server.db import session_scope  # noqa: E402
from server.models import User  # noqa: E402

db.reset_engine()
db.init_db()

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


PAT = "glpat-TestOnly1234567890"
uid = auth.create_user("gl-user", "gl-password-1")["id"]


# ---------------------------------------------------------------- PAT 的入口驗證
print("== PAT 的字元集與長度擋在入口 ==")
check("正常的 PAT 收得下", gitlab_proxy.validate_pat(PAT) == PAT)
check("前後空白剝掉（從剪貼簿貼來常帶換行）", gitlab_proxy.validate_pat(f"  {PAT}\n") == PAT)
for bad, why in [
    ('glpat-a"b', "雙引號能結束 nginx 設定裡的字串"),
    ("glpat-a\\b", "反斜線能逃逸"),
    ("glpat-a$b", "$ 會被 nginx 展開成變數"),
    ("glpat-a b", "空白"),
    ("glpat-a\nb", "換行（塞進 HTTP 標頭就是標頭注入）"),
    ("", "空值"),
]:
    try:
        gitlab_proxy.validate_pat(bad)
        check(f"🔴 應該拒絕：{why}", False)
    except gitlab_proxy.PatRejected as e:
        # ⚠ 例外訊息不可以帶上那個值——它是憑證，而例外訊息很容易被記進 log。
        check(f"🔴 拒絕 {why}，而且訊息不含那個值", bad not in str(e) or not bad)

try:
    gitlab_proxy.validate_pat("g" * (config.GITLAB_PAT_MAX + 1))
    check("🔴 超長要拒絕（貼錯東西時當場講，不要加密存起來）", False)
except gitlab_proxy.PatRejected:
    check("🔴 超長要拒絕（貼錯東西時當場講，不要加密存起來）", True)

# 入口＝auth.set_gitlab_pat，不是只有產生設定時才擋。只擋產生端的話，畸形的值會被加密
# 存起來、設定頁顯示「已設定」，然後每顆代理都靜靜地建不起來，而沒有任何地方會說原因。
try:
    auth.set_gitlab_pat(uid, 'glpat-bad"value')
    check("🔴 set_gitlab_pat 要在入口就擋掉畸形的值", False)
except auth.AuthError as e:
    check("🔴 set_gitlab_pat 要在入口就擋掉畸形的值", 'glpat-bad"value' not in str(e))


# ---------------------------------------------------------------- 儲存與三態
print("\n== 加密入庫、三態、以及與 CLI token 的金鑰隔離 ==")
check("沒設過＝none", auth.gitlab_pat_state(uid) == "none")
auth.set_gitlab_pat(uid, f"  {PAT}\n")
with session_scope() as s:
    enc = s.get(User, uid).gitlab_pat_enc
check("DB 裡是密文，讀不出明文", enc is not None and PAT not in enc)
check("解密回原值（空白已剝）", auth.gitlab_pat(uid) == PAT)
check("有值且解得開＝ok", auth.gitlab_pat_state(uid) == "ok")
check(
    "🔴 拿 CLI_TOKEN 的金鑰解不開 PAT 的密文（用途分離）", crypto.decrypt(enc, purpose=crypto.Purpose.CLI_TOKEN) is None
)
check(
    "🔴 API 出口只給狀態不給值",
    auth.get_user(uid)["gitlab_pat_configured"] is True and PAT not in str(auth.get_user(uid)),
)

# 換金鑰＝所有人一起解不開。這時**不可以**當成「使用者清掉了」——那會讓 reconciler
# 把所有還在服務中的代理一起收掉。三態存在的唯一理由就是分辨這件事。
_real_key = config.SECRET_KEY
config.SECRET_KEY = "a-completely-different-secret"
check("🔴 換過 SECRET_KEY＝unreadable，**不是** none", auth.gitlab_pat_state(uid) == "unreadable")
check("unreadable 時 gitlab_pat() 回 None（拿不到值來用）", auth.gitlab_pat(uid) is None)
check(
    "unreadable 時畫面說「未設定」而不是「已設定」（不要讓人去查一把正常的 token）",
    auth.get_user(uid)["gitlab_pat_configured"] is False,
)
config.SECRET_KEY = _real_key
check("金鑰換回來就恢復 ok", auth.gitlab_pat_state(uid) == "ok")

# 空字串＝清除，而且清除必須與 unreadable 分得開（前者要立刻收代理，後者什麼都不能做）
auth.set_gitlab_pat(uid, "   ")
check("🔴 空字串＝清除 → none（清除要立刻生效）", auth.gitlab_pat_state(uid) == "none")
auth.set_gitlab_pat(uid, PAT)


# ---------------------------------------------------------------- 設定產生
print("\n== 產生出來的 nginx.conf ==")
conf = gitlab_proxy.render_conf(PAT).decode()


def _directives(text: str) -> str:
    """只留真正的指令，把 `#` 註解整行剝掉。

    ⚠ 「這份設定裡**沒有** X」這類斷言一定要用這一份。產生器的註解本身就在解釋為什麼
      不用某個指令（「刻意不套 limit_req」「鍵不用 $binary_remote_addr」），拿原文比對會
      被自己的說明打成紅燈——而那是假訊號，會讓人去改一份其實正確的設定。
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))


code = _directives(conf)

check("PAT 有進設定檔（它就是要在這裡蓋章）", PAT in conf)
check("上游是設定的主機，且沒有任何別的主機名", f"server {config.GITLAB_HOST}:443;" in conf)

# 🔴 授權標頭不可以在 server 層。判準：`server {` 到第一個 `location` 之間那一段。
server_head = code.split("server {", 1)[1].split("location", 1)[0]
check("🔴 server 層沒有 PRIVATE-TOKEN（會被所有 location 繼承，git 全部 401）", "PRIVATE-TOKEN" not in server_head)
check("🔴 server 層沒有 Authorization", "Authorization" not in server_head)


def _block(marker: str) -> str:
    """從 location 標記取到它的結尾大括號（這幾個 block 內部沒有巢狀 `}`，夠用了）。

    走 `code` 而不是 `conf`：block 裡的斷言多半是「不該有什麼」，見 `_directives`。
    """
    body = code.split(marker, 1)[1]
    return body[: body.index("\n        }")]


git_block = _block(r"location ~ ^/.+\.git/")
api_block = _block("location = /api/v4/user")
ping_block = _block("location = /ping")
state_block = _block("location = /_state")

check(
    "🔴 git 走 Basic（transport 不吃 PRIVATE-TOKEN 也不吃 Bearer）",
    'Authorization "Basic ' in git_block and "PRIVATE-TOKEN" not in git_block,
)
check(
    "🔴 API 走 PRIVATE-TOKEN（與獨立版那份 template 同一套慣例）",
    "PRIVATE-TOKEN" in api_block and "Basic" not in api_block,
)
import base64  # noqa: E402

check(
    "Basic 的使用者名稱是 GitLab 對 PAT 的慣例值 oauth2",
    base64.b64encode(f"oauth2:{PAT}".encode()).decode() in git_block,
)

check(
    "🔴 /ping 不經上游、不帶憑證（它答的是「代理在不在」，不是「憑證對不對」）",
    "proxy_pass" not in ping_block and PAT not in ping_block,
)
check(
    "🔴 /_state 不經上游、不帶憑證（session 裡的 AI 打得到這條）",
    "proxy_pass" not in state_block and PAT not in state_block,
)

check(
    "上游 TLS 有驗（少了就是自己在代理這一段開了中間人的門）",
    "proxy_ssl_verify on;" in conf and "proxy_ssl_trusted_certificate" in conf,
)
check(
    "301 改寫回代理自己（不然 git 會跟著跳出代理然後莫名卡住）",
    f"proxy_redirect https://{config.GITLAB_HOST}/ /;" in conf,
)
check(
    "白名單的地板在最後一個 location（排前面整份白名單就形同虛設）",
    conf.rindex("location /") > conf.rindex("location = /api/v4/user"),
)
check(
    "git 用連線數上限而不是 req rate（clone 是少數幾條長連線，429 對 git 讀不懂）",
    "limit_conn gitlab_git" in git_block and "limit_req" not in git_block,
)
check(
    "限流的鍵是常數不是來源 IP（每場 session 是不同 IP，用 IP 就退回 per-session）",
    "limit_req_zone $server_name" in code and "$binary_remote_addr" not in code,
)
check("不記 access log（URL 裡有 repo 路徑，而這顆容器是憑證載體）", "access_log off;" in code)

# discussions 那三條的順序：nginx 依序比對 regex location，`…/discussions$` 排到最前面
# 會把另外兩條整個吃掉，而症狀是它們的方法白名單靜靜失效。
i_notes = conf.index("/discussions/[0-9a-f]+/notes$")
i_one = conf.index("/discussions/[0-9a-f]+$")
i_list = conf.index("/discussions$")
check("🔴 discussions 三條的順序：notes → 單串 → 列表", i_notes < i_one < i_list)


# ---------------------------------------------------------------- 指紋
print("\n== 指紋（reconciler 判斷「跑著的是不是最新的」）==")
fp = gitlab_proxy.fingerprint(PAT)
check("穩定（同輸入同輸出）", fp == gitlab_proxy.fingerprint(PAT))
check("換 PAT 就變", fp != gitlab_proxy.fingerprint(PAT + "x"))
check("🔴 指紋本身出現在設定裡（/_state 回的就是它）", fp in conf)
check(
    "🔴 不是 PAT 的裸 hash——換一把 SECRET_KEY 就換一個值（HMAC，見 fingerprint）",
    (
        lambda: (
            setattr(config, "SECRET_KEY", "another-secret"),
            gitlab_proxy.fingerprint(PAT) != fp,
            setattr(config, "SECRET_KEY", _real_key),
        )[1]
    )(),
)
# 對整份 conf 算而不是只對 PAT 算：所以「我們改了產生器」也會讓指紋變，部署完所有代理
# 自動 reload，不必有人記得要重建。
_saved = gitlab_proxy._API_LOCATIONS
gitlab_proxy._API_LOCATIONS = (*_saved, ("location = /api/v4/version", "GET", 1))
check("🔴 改了產生器（白名單多一條）指紋也要變——否則舊代理永遠不會被 reload", gitlab_proxy.fingerprint(PAT) != fp)
gitlab_proxy._API_LOCATIONS = _saved


# ---------------------------------------------------------------- session 那一端
print("\n== session 容器：拿得到代理，拿不到鑰匙 ==")
kwargs = sessions.build_run_kwargs("c1", "sid1", sessions.Profile(), uid)
blob = repr(kwargs)
check("🔴 PAT 不在 session 容器的任何參數裡（env / volumes / labels / command）", PAT not in blob)
check("🔴 密文也不在（連加密過的都不遞出去）", (enc or "") not in blob)

env = kwargs["environment"]
base = f"http://{config.PROXY_ALIAS}:{config.PROXY_PORT}"
check("容器拿得到 API base（呼叫端不必把 gitlab-proxy:5678 寫死）", env["NCR_GITLAB_API_BASE"] == base)
check(
    "git URL 改寫：三條 insteadOf 指向代理",
    env["GIT_CONFIG_COUNT"] == "3" and {env[f"GIT_CONFIG_KEY_{i}"] for i in range(3)} == {f"url.{base}/.insteadOf"},
)
values = {env[f"GIT_CONFIG_VALUE_{i}"] for i in range(3)}
check(
    "🔴 https 那條**結尾有斜線**（沒有就變前綴比對，"
    "https://gitlab.example.com.evil.example/… 會被導進代理並蓋上真的 PAT）",
    "https://gitlab.example.com/" in values,
)
check(
    "🔴 scp-like 那條**結尾是冒號**不是斜線（寫成 / 不會報錯，只是靜靜不改寫，"
    "而症狀是 Permission denied (publickey)，指不到設定寫錯）",
    "git@gitlab.example.com:" in values,
)
check("🔴 ssh:// 那條也要改寫", "ssh://git@gitlab.example.com/" in values)

# SSH 主機名可以與 HTTPS 分開設（有些組織把 SSH 掛在別的名字）。
_saved_ssh = config.GITLAB_SSH_HOST
config.GITLAB_SSH_HOST = "git.example.com"
v = sessions._gitlab_env()
check(
    "SSH 主機獨立設定時，只有 SSH 那兩條跟著換",
    v["GIT_CONFIG_VALUE_0"] == "https://gitlab.example.com/"
    and v["GIT_CONFIG_VALUE_1"] == "git@git.example.com:"
    and v["GIT_CONFIG_VALUE_2"] == "ssh://git@git.example.com/",
)
config.GITLAB_SSH_HOST = _saved_ssh


# ---------------------------------------------------------------- 沒設主機＝整組關閉
print("\n== 部署者沒設 GitLab 主機：整個功能關閉，不留半套 ==")
_saved_host = config.GITLAB_HOST
config.GITLAB_HOST = ""
check("gitlab_enabled() 是 False", config.gitlab_enabled() is False)
check("🔴 不注入 API base、也不改寫 git URL（半套的改寫會把 git 導向一個不存在的代理）", sessions._gitlab_env() == {})
off_env = sessions.build_run_kwargs("c2", "sid2", sessions.Profile(), uid)["environment"]
check("build_run_kwargs 完全不提 GitLab", not any("GITLAB" in k or "GIT_CONFIG" in k for k in off_env))
try:
    gitlab_proxy.render_conf(PAT)
    check("🔴 沒有主機名時不可以渲染出設定（upstream 會是空字串，nginx 只會報語法錯，指不到真正的原因）", False)
except gitlab_proxy.PatRejected as e:
    check("🔴 沒有主機名時明確拒絕渲染，訊息講得出要設哪個變數", "CLAUDE_PTY_GITLAB_HOST" in str(e))
config.GITLAB_HOST = _saved_host


# ---------------------------------------------------------------- 命名
print("\n== 命名：決定性，而且測試與正式切得開 ==")
check(
    "代理與網路的名字由 user id 決定（不必先查 label 就找得到）",
    user_proxy.proxy_name(7).endswith("7") and user_proxy.network_name(7).endswith("7"),
)
check("兩者不同名", user_proxy.proxy_name(7) != user_proxy.network_name(7))
# 🛡 測試與正式共用同一顆 dockerd，而名字由 DB 的 user id 組出來——測試用全新的暫存 DB
#   （id 從 1 發），必然撞上正式 stack 的 user-1。撞上的後果包含「刪掉正在服務的代理」。
check(
    "🔴 CLAUDE_PTY_TEST_MARK 會把命名空間切開",
    "test" in config.PROXY_NAME_PREFIX if config.TEST_MARK else "test" not in config.PROXY_NAME_PREFIX,
)

# ---------------------------------------------------------------- HTTP 那一層
print("\n== 端點與畫面 ==")
from server.app import app  # noqa: E402

app.config["TESTING"] = True
auth.change_password(uid, "gl-password-2", old_password="gl-password-1")

with app.test_client() as cli:
    cli.post("/api/auth/login", json={"username": "gl-user", "password": "gl-password-2"})

    r = cli.put("/api/users/me/gitlab-pat", json={"pat": PAT})
    check("PUT 設定成功（204）", r.status_code == 204)
    check("   真的存進去了", auth.gitlab_pat(uid) == PAT)

    r = cli.put("/api/users/me/gitlab-pat", json={"pat": ""})
    check(
        "🔴 空字串＝清除，走同一條 PUT（不另開 DELETE，兩個入口只會讓人猜）",
        r.status_code == 204 and auth.gitlab_pat_state(uid) == "none",
    )

    r = cli.put("/api/users/me/gitlab-pat", json={"pat": 'glpat-bad"v'})
    check(
        "🔴 畸形的值回 400，而且訊息裡沒有那個值",
        r.status_code == 400 and 'glpat-bad"v' not in r.get_data(as_text=True),
    )

    r = cli.put("/api/users/me/gitlab-pat", json={"pat": PAT, "extra": 1})
    check("不認得的欄位被擋（與其他端點同一套）", r.status_code == 400)

    cli.put("/api/users/me/gitlab-pat", json={"pat": PAT})
    # ⚠ 這三條原本讀的是 `/account` 渲染出來的 HTML。2026-08-26 拆掉 legacy 之後那條路吐的是
    #   SPA 的殼，裡面一個字都沒有。三條守的性質**都還在**，只是各自搬到它現在的所有者：
    #     · 「說得出是哪一台」→ 值由 `/api/account/bootstrap` 給，畫面只是把它印出來。
    #     · 「PAT 不出現」→ 這是**安全**性質，而且搬到 API 之後守得更嚴：舊的只看一頁 HTML，
    #       現在看的是畫面拿得到的每一份資料。
    #     · 「輪替語意那句話」→ 文案現在在 Vue 元件裡，那就去那裡驗（同
    #       test_admin_endpoint_gate.py 的做法：性質留著，來源換成現在的所有者）。
    _acct = cli.get("/api/account/bootstrap").get_json()
    check(
        "說得出是哪一台 GitLab（值由 /api/account/bootstrap 給，畫面只負責印）",
        _acct["gitlab"]["host"] == config.GITLAB_HOST,
    )
    _payloads = cli.get("/api/account/bootstrap").get_data(as_text=True) + cli.get("/api/auth/me").get_data(
        as_text=True
    )
    check(
        "🔴 畫面拿得到的資料裡沒有 PAT 本身（明文與密文都不出去）",
        PAT not in _payloads and (enc or "") not in _payloads,
    )
    _panel = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend",
        "src",
        "components",
        "account",
        "GitlabPatPanel.vue",
    )
    check(
        "🔴 畫面講得出輪替語意那條準則（最容易被誤解的一件事）",
        os.path.isfile(_panel) and "要隔離那場，就終止那場" in open(_panel, encoding="utf-8").read(),
    )

    # 🔴 列表要能讀到**兩個**事實。單獨任一個都會說謊：只看快照，使用者清掉 token 之後
    #    畫面會一直說「可用」而 git 全部失敗；只看帳號現況，事後補 token 會讓畫面對著一場
    #    根本沒接上網路的 session 說「可用」。
    from server.sessions import _to_dict  # noqa: E402
    from server.models import Session as _Row  # noqa: E402

    with session_scope() as s:
        s.add(_Row(id="glsid1", container_name="c-glsid1", user_id=uid, workdir="", profile={}, gitlab_proxy=True))
    with session_scope() as s:
        d = _to_dict(s.get(_Row, "glsid1"))
    check("🔴 列表給得出「這場當初有沒有接上」", d["gitlab_proxy"] is True)
    check("🔴 也給得出「擁有者現在還有沒有 token」", d["gitlab_pat_set"] is True)
    auth.set_gitlab_pat(uid, "")
    with session_scope() as s:
        d = _to_dict(s.get(_Row, "glsid1"))
    check(
        "🔴 清掉 token 之後：快照仍是 True（那是不可變的事實），"
        "但 pat_set 翻成 False——畫面靠這個組合才說得出「路還在、鑰匙沒了」",
        d["gitlab_proxy"] is True and d["gitlab_pat_set"] is False,
    )
    auth.set_gitlab_pat(uid, PAT)

    # 部署者沒設 GitLab 主機：端點不收、畫面不畫。
    # 收下來的話會存進 DB、畫面顯示「已設定」，而沒有任何東西會用它——那是騙人的。
    config.GITLAB_HOST = ""
    r = cli.put("/api/users/me/gitlab-pat", json={"pat": PAT})
    check(
        "🔴 沒設 GitLab 主機時端點回 400，訊息講得出要設哪個變數",
        r.status_code == 400 and "CLAUDE_PTY_GITLAB_HOST" in r.get_data(as_text=True),
    )
    check("🔴 沒設 GitLab 主機時帳號頁不畫這一塊", "GitLab 憑證" not in cli.get("/account").get_data(as_text=True))
    config.GITLAB_HOST = _saved_host

# ── 自訂 CA：內部憑證簽的 GitLab ───────────────────────────────────────────────
#
# ⚠ 這一段守的失敗**完全沒有訊號**：CA 不對時代理照樣建得起來、容器健康、畫面上的 chip
#   是綠的，但每個 git / API 呼叫都在 TLS 那關 502。`users.gitlab_proxy_error` 是靠
#   「代理沒活著」觸發的，所以它不會亮（見 reconciler._note_proxy_down）。
print("\n== 自訂 CA ==")
_saved_ca = (config.GITLAB_CA_FILE, config.GITLAB_CA_FILE_SELF)
_ca_dir = tempfile.mkdtemp(prefix="claude-pty-ca-")
_ca = os.path.join(_ca_dir, "internal-ca.pem")
with open(_ca, "w") as f:
    f.write("-----BEGIN CERTIFICATE-----\nQUFB\n-----END CERTIFICATE-----\n")
try:
    # 1) 沒設＝維持現狀。這條要先驗，否則後面全部都證明不了「預設沒被動到」。
    config.GITLAB_CA_FILE = config.GITLAB_CA_FILE_SELF = ""
    _plain = gitlab_proxy.render_conf(PAT).decode()
    check(
        "🔴 沒設 CA → 信任錨仍是系統 CA（預設行為一個字都沒變）",
        "proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;" in _plain,
    )
    check("🔴 沒設 CA → 一個 volume 都不掛", user_proxy.ca_binds() == {})

    # 2) 有設＝conf 指向落點、容器掛唯讀。
    config.GITLAB_CA_FILE = "/host/somewhere/internal-ca.pem"
    config.GITLAB_CA_FILE_SELF = _ca
    _conf = gitlab_proxy.render_conf(PAT).decode()
    check("🔴 有設 CA → 信任錨指向容器內的落點", f"proxy_ssl_trusted_certificate {config.GITLAB_CA_BIND};" in _conf)
    check(
        "🔴 有設 CA → 掛載是 host 路徑 → 落點，而且是唯讀",
        user_proxy.ca_binds() == {"/host/somewhere/internal-ca.pem": {"bind": config.GITLAB_CA_BIND, "mode": "ro"}},
    )
    # 🔴 **永遠不准出現關掉驗證的開關。** 這條不是風格檢查：這顆容器存在的唯一理由是
    #    保管別人的 PAT，對上游不驗憑證等於把「憑證不進 session」買到的東西原樣送給
    #    任何一個中間人。內部 CA 的解法是把 CA 給它，不是不驗。
    # ⚠ 比對前先把註解行剝掉：conf 裡**刻意**留了一句註解寫著「永遠不會有
    #    `proxy_ssl_verify off`」，而那句話本身就含有那個字串。這條要驗的是「有沒有這條
    #    **指令**」，不是「檔案裡有沒有出現這幾個字」——第一版就是被自己的註解絆倒的。
    # ⚠ 變數名不可以叫 `_directives`——那是本檔 :134 的 helper 函式，而 `with` 不是 scope，
    #   在這裡指派會把它**永久**蓋成一個字串。目前 :393 之後沒有人再呼叫它所以不會壞，
    #   但下一個在後面追加、呼叫 _directives(...) 的測試會拿到
    #   `TypeError: 'str' object is not callable`，而錯誤訊息完全指不到這裡（審查 F-019）。
    _ssl_lines = "\n".join(ln for ln in _conf.splitlines() if not ln.strip().startswith("#"))
    check(
        "🔴 產生出來的 conf 永遠是 proxy_ssl_verify on，沒有 off 那條指令",
        "proxy_ssl_verify on;" in _ssl_lines and "proxy_ssl_verify off" not in _ssl_lines,
    )
    # 🔴 helper 還活著——這一條就是上面那個坑的守衛：它被蓋掉的話這裡當場 TypeError。
    check(
        "🔴 _directives 仍然是函式（區域變數不可以蓋掉同名 helper）",
        callable(_directives) and "proxy_ssl_verify on;" in _directives(_conf),
    )

    # 3) 續簽（路徑不變、內容變）也要收斂——只比路徑會漏掉這一種。
    _fp_before = gitlab_proxy.fingerprint(PAT)
    with open(_ca, "w") as f:
        f.write("-----BEGIN CERTIFICATE-----\nQkJC\n-----END CERTIFICATE-----\n")
    check(
        "🔴 CA 續簽（路徑不變、內容變）指紋要變，否則 nginx 抱著舊的 CA 不放",
        gitlab_proxy.fingerprint(PAT) != _fp_before,
    )

    # 4) 掛載比對：這是「改了 CA 卻沒作用」的守門人。CA 是 volume，換不掉，只能重建。
    class _FakeC:
        def __init__(self, mounts):
            self.attrs = {"Mounts": mounts}

    _match = [
        {"Type": "bind", "Source": "/host/somewhere/internal-ca.pem", "Destination": config.GITLAB_CA_BIND, "RW": False}
    ]
    check("🔴 掛的就是現在設定的那一個 → 不必重建", user_proxy.ca_mount_matches(_FakeC(_match)))
    check(
        "🔴 掛的是**別的** CA → 判定不符（要重建，熱重載換不掉掛載）",
        not user_proxy.ca_mount_matches(_FakeC([{**_match[0], "Source": "/host/somewhere/old-ca.pem"}])),
    )
    check("🔴 完全沒掛（設定是後來才加的）→ 判定不符", not user_proxy.ca_mount_matches(_FakeC([])))
    config.GITLAB_CA_FILE = config.GITLAB_CA_FILE_SELF = ""
    check(
        "🔴 反過來：設定拿掉了但容器還掛著 → 也要判定不符（否則舊 CA 永遠留著）",
        not user_proxy.ca_mount_matches(_FakeC(_match)),
    )

    # 5) preflight：填了卻找不到要在啟動時就喊，不可以靜靜退回系統 CA。
    config.GITLAB_CA_FILE = config.GITLAB_CA_FILE_SELF = os.path.join(_ca_dir, "nope.pem")
    _probs = " ".join(sessions.preflight()[0])
    check(
        "🔴 CA 檔不存在 → preflight 出聲，而且講得出症狀（502／狀態是綠的）", "nope.pem" in _probs and "502" in _probs
    )
    config.GITLAB_CA_FILE = config.GITLAB_CA_FILE_SELF = _ca
    check("🔴 檔案在就不要吵", not any("GITLAB_CA_FILE" in p for p in sessions.preflight()[0]))
finally:
    config.GITLAB_CA_FILE, config.GITLAB_CA_FILE_SELF = _saved_ca
    shutil.rmtree(_ca_dir, ignore_errors=True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
