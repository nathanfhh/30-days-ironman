"""per-user GitLab 憑證代理的 nginx 設定產生器（ADR 0016）。

這份設定跑在**每個使用者一顆**的 nginx 裡，掛在 `claude-pty-user-{id}` 這個 per-user
network 上、network alias 叫 `gitlab-proxy`，聽 5678。該使用者的所有 session 都掛在同一個
網路上，容器裡的 git 與 API 呼叫**裸打、不帶任何 auth**，由這裡蓋章。

⚠ **為什麼是 per-user 而不是 per-session**：nginx 的 `limit_req_zone` 是 **per-instance**
的。一個使用者開 N 場 session 就是 N 顆 nginx、N 個獨立的計數桶——`10r/s` 對 GitLab 變成
`N×10r/s`，**等於沒有限流，而且越是「同時很多」的情境它越失效**。要 per-user 的總量就
必須 per-user 一顆，這不是調參數能修的，是拓樸決定的。

⚠ **`listen 5678` 不是 `127.0.0.1:5678`**：代理是網路上的鄰居，不與 session 共享 netns，
綁 loopback 就沒有人連得到。

三類 location，各自的授權方式不同——**這是整個檔案最要緊的一件事**：

| 路徑 | 標頭 | 為什麼 |
|---|---|---|
| `/ping`、`/_state` | 無 | 不經上游，所以沒有憑證也答得出來 |
| `/api/v4/…` | `PRIVATE-TOKEN: <PAT>` | 與 `gitlab-proxy/nginx.conf.template` 同一套慣例 |
| `….git/…` | `Basic base64(oauth2:<PAT>)` | git transport **不吃** PRIVATE-TOKEN 也不吃 Bearer |

⚠ **授權標頭絕不可以設在 server 層。** 獨立版那份（`gitlab-proxy/nginx.conf.template`）
  把 `PRIVATE-TOKEN` 設在 server 層，而它的註解已經預告了這一天：「等到哪天要連 git clone
  也走這個代理，這行就得搬進各自的 location」。這裡就是那一天——繼承下去的話 git 會收到
  一個它看不懂的標頭而全部 401。所以這裡各 location 各自設。

⚠ **API 白名單是第二份**。來源是同 repo 的 `gitlab-proxy/nginx.conf.template`（獨立部署那
  一套）。加一個端點就要改兩個地方，漏了這一份的症狀是「獨立代理那條路通、網頁 session
  403」，而且兩邊各自看起來都沒問題。**兩邊都留了 SYNC 註解**，改動時請一起改。
  不合併是因為產生方式不同：那邊是 `envsubst` 展開的靜態 template（沒有控制平面），
  這邊是依 PAT 現算的——硬要共用會讓兩邊都得遷就對方的機制。

⚠ 已知**不支援 git-lfs**：LFS 的 batch API 會回外部 href（可能直指物件儲存），nginx 改不掉。
  有用 LFS 的 repo 會靜默壞掉，所以文件裡要講明，不要讓人以為它會通。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

from . import config

# PAT 允許的字元。GitLab 自己發的是 `glpat-` + base62，這裡放寬到常見 token 會用到的
# 符號集，但**刻意排除** `"`、`\`、`$` 與任何空白／換行。
# 理由不是潔癖：PAT 會被寫進 nginx 設定檔的雙引號字串裡，那三個字元分別能結束字串、
# 逃逸、以及展開成 nginx 變數——也就是把一個使用者可控的值變成設定檔注入。
# base64 過的那條（git 的 Basic）本來就只會產生 base64 字母，安全；**API 那條是原樣的**，
# 所以這道檢查是必要的，不是多餘的。
PAT_CHARSET = re.compile(r"\A[A-Za-z0-9._~+/=-]+\Z")


class PatRejected(ValueError):
    """PAT 含有不能安全寫進設定檔的字元，或長得不像一個 token。"""


def validate_pat(pat: str) -> str:
    """回傳 strip 過的 PAT；不合法就拋 `PatRejected`。

    ⚠ 例外訊息裡**不可以**帶上那個值——它是憑證，而例外訊息很容易被記進 log。
    """
    pat = (pat or "").strip()
    if not pat:
        raise PatRejected("PAT 是空的")
    if len(pat) > config.GITLAB_PAT_MAX:
        raise PatRejected(
            f"PAT 超過 {config.GITLAB_PAT_MAX} 字元——請確認貼的是 token 本身")
    if not PAT_CHARSET.match(pat):
        raise PatRejected("PAT 含有不允許的字元（只收英數與 . _ ~ + / = -）")
    return pat


def _basic(pat: str) -> str:
    """git transport 用的 Basic 憑證。

    使用者名稱固定 `oauth2`——那是 GitLab 對 PAT 的慣例值（另一個 `gitlab-ci-token` 是給
    CI job token 用的）。無認證打 git 的 `info/refs` 時 GitLab 回的是
    `401 www-authenticate: Basic realm="GitLab"`：**它自己指名要 Basic**。
    """
    return base64.b64encode(f"oauth2:{pat}".encode()).decode()


# API 白名單。⚠ SYNC: gitlab-proxy/nginx.conf.template（同 repo 的獨立部署版）。
#
# 形狀是 `(location 指令, 允許的方法, burst)`。**順序有意義**：nginx 依序比對 regex
# location，所以較長的路徑必須排在較短的前綴之前，否則後者永遠比對不到。
#
# burst 按「這條路徑有沒有重試的安全網」給，不是按端點重要性給：GET 撞到限流可以自己
# 退避重來（代價是慢幾秒），POST 撞到限流就是發不出去、沒有第二次機會。所以寫入端要
# 給餘裕，讀取端反而可以收緊。
_API_LOCATIONS: tuple[tuple[str, str, int], ...] = (
    # 確認 token 有效、知道自己是誰。整場只呼叫一次。
    ("location = /api/v4/user", "GET", 2),
    # 取回 MR 的標題、說明、來源與目標分支。整場只呼叫一次。
    ("location ~ ^/api/v4/projects/.+/merge_requests/[0-9]+$", "GET", 2),
    # 下載 MR 說明裡的附件。可能好幾個且循序下載，全篇最會連發的一條。
    ("location ~ ^/api/v4/projects/.+/uploads/[0-9a-f]+/.+$", "GET", 8),
    # ⚠ 這三條 discussions 的順序不可以動：nginx 依序比對，`…/discussions$` 排到最前面
    #   會把另外兩條**整個吃掉**（它們是它的延伸路徑），而症狀是那兩條的方法白名單靜靜
    #   失效——回覆與單串查詢都落到第一條的 `GET POST` 上。
    ("location ~ ^/api/v4/projects/.+/merge_requests/[0-9]+/discussions/[0-9a-f]+/notes$",
     "POST", 5),
    ("location ~ ^/api/v4/projects/.+/merge_requests/[0-9]+/discussions/[0-9a-f]+$",
     "GET", 3),
    ("location ~ ^/api/v4/projects/.+/merge_requests/[0-9]+/discussions$", "GET POST", 5),
)

# git smart HTTP 的三個端點。**與 API 分開**，因為授權形式不同（見模組 docstring 的表）。
_GIT_LOCATION = r"location ~ ^/.+\.git/(info/refs|git-upload-pack|git-receive-pack)$"


def ca_digest() -> str:
    """目前那份自訂 CA 的內容摘要；沒設或讀不到回空字串。

    ⚠ **為什麼指紋要含 CA 的內容，而不是只含路徑。** 續簽一次內部 CA 是路徑不變、內容變
      ——只比路徑的話 `/_state` 不會變、reconciler 不會重載，於是 nginx 抱著記憶體裡那份
      舊的 CA 繼續驗，而**症狀是每個請求 502、容器完全健康**（同 GITLAB_CA_FILE 那段講的
      那個惡劣形狀）。把內容摘進指紋，換一次 CA 下一輪就自己重載。
    ⚠ 讀不到回空字串而不是拋：這支在收斂的熱路徑上，CA 檔暫時不可讀不該讓整輪陣亡。
      「填了卻找不到」由 preflight 在啟動時喊（見 sessions.preflight）。
    """
    if not config.GITLAB_CA_FILE_SELF:
        return ""
    try:
        with open(config.GITLAB_CA_FILE_SELF, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def fingerprint(pat: str) -> str:
    """這份設定的指紋——`/_state` 回的就是它，reconciler 拿它判斷「跑著的是不是最新的」。

    **對整份算好的 conf 做，而不是只對 PAT 做。** 因此它同時涵蓋兩種過期：
      · 使用者換了 PAT
      · **我們改了這個產生器**（例如白名單加一條端點）→ 部署後所有代理自動 reload，
        不必手動重建，也不會有人記得要重建
      · **自訂 CA 換了**（路徑或內容都算，見 `ca_digest`）——續簽是內容變而路徑不變，
        只看路徑會漏掉那一種

    ⚠ **用 HMAC 而不是裸 sha256。** `/_state` 就在 per-user network 上，**session 裡的 AI
      打得到**。裸 hash 等於把「一個 secret 的 hash」交出去；以 `SECRET_KEY` 導出的金鑰
      當 key 之後，沒有伺服器金鑰的人拿到那串完全推不出東西，而控制平面照樣算得出來。

    ⚠ 自我參照：指紋要放進 conf 裡，所以不能對「含指紋的 conf」做。這裡先以**空的 state**
      渲染一次拿來算，再由 `render_conf` 用算出來的值渲染第二次。兩次渲染，成本可忽略。
    """
    body = _render(pat, state="")
    # ⚠ 金鑰**導出**而不是直接用 `SECRET_KEY`：`crypto.py` 立的規矩就是「同一把
    #   SECRET_KEY 底下，不同用途要導出不同的金鑰」。這裡查不到可達的攻擊（訊息形狀與
    #   cookie 不相容），純粹是不要在同一個 codebase 裡立了規矩又自己破例——下一個用途
    #   未必這麼幸運。
    key = hmac.new(config.SECRET_KEY.encode(), b"gitlab-proxy-state-v1",
                   hashlib.sha256).digest()
    return hmac.new(key, body, hashlib.sha256).hexdigest()[:32]


def render_conf(pat: str) -> bytes:
    """產生完整的 nginx.conf（含 `/_state`）。回傳 bytes，直接餵給 `put_archive`。

    ⚠ 產出的內容**含有明文 PAT**。它只會走進代理容器的檔案系統，不落 host 磁碟、
      不進環境變數、不出現在 `docker inspect`（ADR 0016 的整個前提就是這個）。
    """
    return _render(pat, state=fingerprint(pat))


def _render(pat: str, state: str) -> bytes:
    pat = validate_pat(pat)
    host = config.GITLAB_HOST
    # 信任錨：有掛自訂 CA 就指過去，否則沿用容器內的系統 CA（維持現狀）。
    ca_path = config.GITLAB_CA_BIND if config.GITLAB_CA_FILE else \
        "/etc/ssl/certs/ca-certificates.crt"
    # CA 的內容摘要寫成註解，只為了讓它進到指紋裡（見 ca_digest）。它是 CA 的**公開**
    # 憑證的雜湊，不是秘密；而 `/_state` 回的是整份 conf 的 HMAC，不是這一行。
    ca_note = f"# gitlab-ca: {ca_digest() or 'system'}\n"
    if not host:
        # 到不了這裡才對——呼叫端一律先問 `config.gitlab_enabled()`。真的走到了就明講，
        # 不要渲染出一份 upstream 是空字串的設定（那會變成 nginx 啟動失敗，而錯誤訊息
        # 指的是語法，指不到「沒設主機名」這個真正的原因）。
        raise PatRejected(
            "沒有設定 GitLab 主機（CLAUDE_PTY_GITLAB_HOST），無法產生代理設定")
    api_auth = f'proxy_set_header PRIVATE-TOKEN "{pat}";'
    git_auth = f'proxy_set_header Authorization "Basic {_basic(pat)}";'
    # ⚠ **每一個會 proxy_pass 的 location 都要自己再設一次這三行。**
    #   nginx 的 `proxy_set_header` 繼承規則是「本層只要定義了任何一個，就完全不繼承
    #   上層的」——而 git 那條設了 Authorization、API 那幾條設了 PRIVATE-TOKEN，於是
    #   server 層的 Host / X-Real-IP / X-Forwarded-For **整組被丟掉**，`Host` 退回預設的
    #   `$proxy_host`，也就是 upstream 區塊的名字 `gitlab`。
    #   GitLab 是依 Host 路由的，收到 `gitlab` 會 404 或導去別的 vhost，而症狀完全指不到
    #   這裡（2026-08-08 端到端測試第一次跑就抓到，見 test_gitlab_upstream_e2e）。
    #   server 層那份留著當地板，給日後任何「一個 proxy_set_header 都沒設」的 location。
    common_headers = f"""proxy_set_header Host {host};
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;"""

    api_blocks = "\n".join(
        f"""
        {loc} {{
            limit_except {methods} {{ deny all; }}
            limit_req zone=gitlab_api burst={burst} nodelay;
            {common_headers}
            {api_auth}
            proxy_pass https://gitlab;
        }}"""
        for loc, methods, burst in _API_LOCATIONS)

    return f"""# 由 claude-pty 自動產生（server/gitlab_proxy.py，ADR 0016）。不要手改。
{ca_note}worker_processes 1;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {{ worker_connections 1024; }}

http {{
    # ⚠ 不記 access log：URL 裡有 repo 路徑，而這顆容器是 per-user 的憑證載體，
    #   能少留一份紀錄就少一份。錯誤仍然進 error_log。
    access_log off;

    # ⚠ 這兩個 zone 現在是**真的** per-user 總量——per-session 的形狀下每場一顆 nginx，
    #   zone 各自獨立，等於沒有限流（見模組 docstring）。
    # ⚠ 鍵用常數（`$server_name`）而不是 `$binary_remote_addr`：這顆代理只服務一個人，
    #   而他的每一場 session 是不同的來源 IP——用來源 IP 當鍵會讓「同時開 5 場」又變成
    #   5 個桶，退回原本的問題。
    limit_req_zone $server_name zone=gitlab_api:1m rate=10r/s;
    limit_req_status 429;
    # git 用**連線數**而不是請求速率：一次 clone 是少數幾條**長**連線（info/refs 的 GET →
    # upload-pack 的 POST，循序），用 req-rate 限它只會在正常操作時噴 429，而 429 對 git
    # 是一個看不懂的失敗。
    # ⚠ 8 是保守放寬值，**沒有實際量過並發連線數**（submodule 的 `--jobs N` 會拉高）。
    #   上線後看 error_log 再收緊。超限回 503，git 顯示 `error: 503`，還算讀得懂。
    limit_conn_zone $server_name zone=gitlab_git:1m;
    limit_conn_status 503;

    upstream gitlab {{ server {host}:443; }}

    # 上游 TLS 驗證。⚠ 這四行漏掉，代理對 GitLab 就是不驗憑證——你把憑證從 agent 手上
    # 收走，卻在代理這一段自己開了一個中間人的門。
    # ⚠ **永遠不會有 `proxy_ssl_verify off`**，內部 CA 的解法是把那個 CA 給它（見
    #   config.GITLAB_CA_FILE），不是不驗。
    proxy_ssl_verify on;
    proxy_ssl_trusted_certificate {ca_path};
    proxy_ssl_server_name on;
    proxy_ssl_name {host};

    server {{
        listen {config.PROXY_PORT};
        server_name localhost;

        # ⚠ server 層**只放沒有授權語意的東西**。授權標頭一旦放在這裡就會被所有 location
        #   繼承，git 那條會收到它看不懂的標頭而全部 401（獨立版那份 template 的註解
        #   預告過這件事）。
        # ⚠ **但這三行不會被下面那些 location 繼承**（它們各自設了授權標頭，而 nginx 的
        #   繼承是整組取代不是逐項合併），所以每個 location 都自己再設一次。這裡留著只是
        #   給未來「完全沒設 proxy_set_header」的 location 當地板。見上方 common_headers。
        proxy_set_header Host {host};
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        proxy_connect_timeout 10s;
        proxy_send_timeout 10s;
        proxy_read_timeout 30s;
        client_max_body_size 10m;

        # ⚠ GitLab 對「URL 少了 .git、專案改名、大小寫不符」會回**絕對 URL 的 301**，
        #   而 git 會老實跟著跳——跳出代理直連 GitLab，然後撞上防火牆而**莫名逾時**
        #   （不是錯誤訊息，是卡住）。這一行把它改寫回代理自己。漏了會非常難查。
        proxy_redirect https://{host}/ /;

        # 健康檢查。⚠ **不經上游**，所以沒有 PAT 也答得出來——這是刻意的：它回答的是
        # 「代理在不在」，不是「憑證對不對」。
        location = /ping {{
            default_type application/json;
            return 200 '{{"result": true, "data": {{}}}}';
        }}

        # 這顆代理現在跑的是哪一份設定。reconciler 用
        # `docker exec <proxy> wget -qO- 127.0.0.1:{config.PROXY_PORT}/_state` 讀它，
        # 判斷要不要熱重載。**容器自己回報**，控制平面不另存狀態——沒有「DB 說是新的、
        # 實際是舊的」這種漂移。
        # ⚠ 值是 HMAC 不是裸 hash，因為這條路徑 session 裡的 AI 也打得到（見 fingerprint）。
        location = /_state {{
            default_type text/plain;
            return 200 "{state}";
        }}

        # git smart HTTP。⚠ 只有 Basic 能用（見模組 docstring 的表）。
        # ⚠ `service=` 這個 query 參數**不能當閘門**：`limit_except GET` 擋不住
        #   「用 service=git-receive-pack 去問 refs」，而真正的 push 是
        #   POST /git-receive-pack——所以推不推得動交給 PAT 的 scope 管，不在這裡擋。
        {_GIT_LOCATION} {{
            {common_headers}
            {git_auth}
            client_max_body_size 0;          # 預設 10m 會讓稍大的 push 收到 413
            proxy_request_buffering off;     # 否則整包 packfile 先落到代理磁碟再轉發
            proxy_buffering off;             # clone 時 pack 邊產邊送，不要在代理堆起來
            proxy_http_version 1.1;          # request_buffering off 要真的 pass-through 必須 1.1
            proxy_set_header Connection "";
            proxy_read_timeout 3600s;        # 大 repo 的 upload-pack 第一個 byte 可能很久
            # ⚠ 刻意**不**套 limit_req（見上方 zone 的說明），改用連線數上限。
            limit_conn gitlab_git 8;
            proxy_pass https://gitlab;
        }}

        # API 白名單。⚠ SYNC: gitlab-proxy/nginx.conf.template
{api_blocks}

        # 白名單模式的地板：沒列到的就是不准。**這一段必須留在最後**，排到前面去的話
        # 整份白名單就形同虛設。
        location / {{
            default_type application/json;
            return 403 '{{"error": "Forbidden: endpoint not whitelisted"}}';
        }}
    }}
}}
""".encode()
