"""Server 端寫死的常數（ADR 0004：image / workdir / command / 資源限制全由後端固定，
前端不得指定）。前端只選得了 profile 的幾個面向，值一律走白名單。"""

import os
import sys
from contextlib import suppress

# 目標 image：`dev-container/` 建出來的那一顆（見它的 run script）。session 容器就是
# 那個環境——所以「人自己開容器」與「網頁開一場」跑的是同一份工具鏈與同一份 entrypoint。
# 以 env 覆寫方便本機煙霧測試（CLAUDE_PTY_IMAGE）。
IMAGE = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")

# ENTRYPOINT：預設 None＝走 image 的 entrypoint.sh（ADR 0006），由控制平面用 env 非互動答選單，
# firewall/mitm/otel 的 how 留在 entrypoint.sh + init-firewall.sh 這個 SSOT。
# CLAUDE_PTY_ENTRYPOINT 有值＝escape hatch：覆蓋 entrypoint（如 bash 測試），跳過 entrypoint.sh、
# 不注入 profile env。COMMAND 僅在覆蓋時有意義（entrypoint.sh 不吃 "$@"）。
ENTRYPOINT = os.environ.get("CLAUDE_PTY_ENTRYPOINT")  # None → image entrypoint.sh
COMMAND = os.environ.get("CLAUDE_PTY_COMMAND", "").split()

# --- 路徑：控制平面「自己看到的」vs「傳給 daemon 的 host 路徑」必須分開（ADR 0009）------
#
# 控制平面本身容器化後，bind mount 的來源路徑是**由 docker daemon 在 host 上解讀**的，
# 不是控制平面容器內的路徑。若沿用 `expanduser("~")` / `__file__` 推導，進了容器會變成
# 容器內路徑（/app/...），daemon 拿去 host 找 → 掛載失敗或靜默建出空目錄（憑證掛不進去、
# claude 變登出狀態）。故：
#   _SELF_*  ＝ 控制平面自己讀得到的路徑（用於 os.path.isfile/isdir 的存在性檢查）
#   HOST_*   ＝ 傳給 daemon 的 host 路徑（用於 volumes 的 key）
# 非容器化部署時兩者相同，env 不必設；容器化部署時以 CLAUDE_PTY_HOST_* 指定 host 路徑。
# 容器化後程式碼被 COPY 到 /app，由 __file__ 推導會得到 "/"，找不到 dev-container/。
# 故允許以 env 指定「控制平面內看得到 repo 的位置」（compose 會把 repo 掛在該路徑）。
_SELF_REPO_ROOT = os.environ.get(
    "CLAUDE_PTY_SELF_REPO_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SELF_HOME = os.path.expanduser("~")

HOST_REPO_ROOT = os.environ.get("CLAUDE_PTY_HOST_REPO_ROOT", _SELF_REPO_ROOT)
HOST_HOME = os.environ.get("CLAUDE_PTY_HOST_HOME", _SELF_HOME)

# 路徑不是控制平面唯一看不到的 host 事實，**作業系統是第二件**（ADR 0009）。
# 值就是 host 的 `uname -s`（`Linux` / `Darwin` / `MINGW64_NT-…`），空＝不知道。
# 由 `deploy/redeploy.sh` 算好、compose 注進來（**只給 control**，preflight 只在它裡面跑）。
HOST_PLATFORM = os.environ.get("CLAUDE_PTY_HOST_PLATFORM", "").strip()


def host_is_linux() -> bool:
    """host 的 bind mount 會不會**原樣把 uid 帶過去**（＝uid 對不上就真的寫不進去）。

    用途只有一個：決定 preflight 的 `APP_UID` 檢查要不要喊。只有 Linux 的 bind mount 會
    原樣帶 uid；Docker Desktop（macOS／Windows）都做 uid 對映，那裡 uid 不同是正常的。

    ⚠ **不可以改回 `sys.platform`。** 容器化部署（ADR 0009，也就是正式的那個形狀）下它
      永遠是 `linux`——講的是**容器**不是 host。原本那道 `if sys.platform == "linux"` 的
      用意正是「macOS 上別喊」，但它**從來沒有在正式部署裡生效過**：2026-08-08 一次
      redeploy 之後才發現，macOS host 每次啟動都收到一句叫他去改 APP_UID 的假警報，
      而 session 明明好好的。一條喊狼來了的訊號，比沒有訊號更糟。

    ⚠ 判準是**白名單**「host 是不是 Linux」而不是黑名單「是不是 macOS」：Windows 的
      Docker Desktop 同樣做 uid 對映，白名單讓它自己落在正確的一側，不必列舉，
      也不會在多一種 host 時漏掉。

    ⚠ 不知道（沒設）時退回 `sys.platform`：非容器化執行時那**就是** host 的真相；容器化
      而沒設的人維持舊行為。誤報是安全的那個方向——漏報的代價是「真 Linux host 上每一場
      session 都撞 onboarding 對話，而且完全沒有訊號」。
    """
    return (HOST_PLATFORM or sys.platform).lower().startswith("linux")

# 走 entrypoint.sh 時 bind-mount repo 的 entrypoint.sh，保證 ADR 0006 的 env-skip 邏輯一定在
# （免每次 rebuild image；比照 run script mount skills/addon 的 freshness 哲學）。
# SELF 版供存在性檢查、HOST 版供實際掛載。
ENTRYPOINT_SH_SELF = os.path.join(_SELF_REPO_ROOT, "dev-container", "entrypoint.sh")
ENTRYPOINT_SH = os.path.join(HOST_REPO_ROOT, "dev-container", "entrypoint.sh")

# --- session 執行 profile 預設（ADR 0006）---------------------------------------
# 預設 restricted（白名單出網），與 run script 的預設一致——安全預設應該是「限制」而非
# 「開放」，要放行是明確的選擇。代價：需要 NET_ADMIN 與 session network 存在；啟動
# 多花的時間中，套 iptables 只有約 0.5 秒，trivy DB 命中快取（見 MOUNTS）時也是 0 秒——
# 未命中才要重抓約 1GB 的 DB，實測多花 36 秒。不錄流量 / 不送 telemetry 維持關閉，
# 那兩者需要額外 apparatus（mitm addon / jaeger）且非每場都想要。
DEFAULT_NET = os.environ.get("CLAUDE_PTY_DEFAULT_NET", "restricted")        # restricted | unrestricted
DEFAULT_CAPTURE = os.environ.get("CLAUDE_PTY_DEFAULT_CAPTURE", "0") == "1"
DEFAULT_TELEMETRY = os.environ.get("CLAUDE_PTY_DEFAULT_TELEMETRY", "0") == "1"
# 模型與思考深度（只對 cli=claude 生效）。值就是 `claude --model` / `--effort` 的合法別名
# ——實測 v2.1.207 的 --help：model 收 sonnet/opus/fable（或完整名稱如 claude-fable-5），
# haiku 也收（--help 的例子沒列，2026-07-31 實跑 `claude -p --model haiku` 確認，
# modelUsage 回 claude-haiku-4-5-20251001）——**別名的合法性以實跑為準，不以 --help 的
# 例子為準**，那份例子只是舉例不是白名單。
# effort 收 low/medium/high/xhigh/max。預設取 opus + high：這個系統跑的是需要長時間自主
# 工作的 session，省算力的那端不是它的用途。
# claude 的模型與思考深度白名單（`claude --model` / `--effort` 的合法別名，實測 v2.1.207）。
# ⚠ **這是唯一一份**：app 用它驗輸入與組 /api/catalog。曾經只住在 app.py，而 web.py
#   不能 import app（循環），所以放 config——低層共用常數的家。
# ⚠ **這個 tuple 的順序就是選單的順序**（app.get_catalog 照它組清單，picker 照清單畫）。
#   排法是「世代新→舊、能力強→輕」：fable / opus / sonnet / haiku。
#   ⚠ 因此**第一顆不等於預設**（預設是下面的 DEFAULT_MODEL＝opus）。這是刻意的：選單
#     順序回答「有哪些、怎麼排」，預設回答「沒選擇可沿用時落在哪」，兩者不該綁在一起
#     ——真正要防的是「切回 claude 時落到清單第一個」那個 bug，守它的是 default_model
#     一律讀 DEFAULT_MODEL（見 app.get_catalog 與 test_catalog）。
CLAUDE_MODELS = ("fable", "opus", "sonnet", "haiku")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

DEFAULT_MODEL = os.environ.get("CLAUDE_PTY_DEFAULT_MODEL", "opus")
DEFAULT_EFFORT = os.environ.get("CLAUDE_PTY_DEFAULT_EFFORT", "high")

# 第二層 docker 能力所需的基礎設施參照（env 給不了的部分，ADR 0006）。
# 所有走 docker API 的呼叫的上限（秒）。docker-py 預設是 **60**，而那個值在這裡是錯的：
# 一顆卡住的容器（實測 2026-07-27：卡在 `removing` 40 分鐘，daemon 對它的 inspect/logs
# 一律不回應）會讓每一次呼叫等滿 60 秒——reconciler 整輪陣亡、web 的 thread 被吃光。
# 縮短不能解決連坐（那要靠「列表只讀 DB」+ 逐顆隔離，ADR 0012），但它決定了單一次
# 意外的代價：15 秒對正常操作綽綽有餘（建立容器不走這條，它有自己的長 timeout）。
DOCKER_TIMEOUT = float(os.environ.get("CLAUDE_PTY_DOCKER_TIMEOUT", "15"))

# --- session 容器的網路歸屬（ADR 0016）---------------------------------------------
#
# **每個使用者一張網，他的 session 全部住在上面**（名字見下方 `USER_NETWORK_PREFIX`）。
# 沒有任何跨使用者共用的 session 網路——這是隔離的來源。
#
# ⚠ 這裡曾經有 `SESSION_NETWORK`（`claude-pty-sessions`），所有人的 session 共用一張。
#   那張網上跨使用者是**互通的**：restricted 的容器內有 iptables 擋出網，擋不住同網段
#   互連；unrestricted 連那層都沒有。2026-08-07 實測（兩個使用者各開一場，在其中一顆起
#   listener、從另一顆連過去）確認收得到。而且更糟的是 `build_run_kwargs` 只在
#   restricted 或 telemetry 時設 network，**unrestricted 且不送 telemetry 的 session 落在
#   docker 預設 `bridge`**——那張網住著這台機器上每一顆沒指定網路的容器，不只是別人的
#   session。所以現在**四種 profile 組合一律指定 network**，見 build_run_kwargs。
#
# ⚠ **任何情況下都不得退回共用網路。** 位址池滿時正確的行為是讓 session 開不起來並把
#   下一步講清楚（見 user_proxy.PoolExhausted），不是找一張共用的網把它塞進去——那會
#   把上面那個洞無聲地打回來。
LEGACY_SESSION_NETWORK = "claude-pty-sessions"
# `CLAUDE_PTY_NETWORK` 已經沒有作用。**留著只為了偵測「有人還在設它」**：一個被靜靜
# 忽略的旋鈕是最難查的那種（設定了、重啟了、什麼都沒變，而且沒有任何訊息）。
# preflight 看到它有值就報一行，見 sessions.preflight。
LEGACY_NETWORK_ENV = os.environ.get("CLAUDE_PTY_NETWORK", "").strip()
# jaeger 要在**每一張使用者網路**上，否則那些 session 的 telemetry 靜默斷掉（OTLP 是
# fail-open，不會有任何錯誤）。接線由控制平面負責，見 user_proxy.attach_jaeger。
# ⚠ jaeger 定義在 **opentelemetry/jaeger-compose.yaml**，不在 deploy/docker-compose.yml
#   裡；它是**人的路徑也在用**的共用 infra。改那個檔要一併驗人的路徑。
OTEL_ENDPOINT = os.environ.get("CLAUDE_PTY_OTEL_ENDPOINT", "http://jaeger:4317")
# 脫敏 addon：repo 的 `mitm/`（claude-pty 的同 repo 兄弟目錄），用相對路徑、不依賴
# 環境絕對路徑。
# ⚠ **來源與落點都要與 entrypoint 對齊**：它寫死了 addon 在容器內的位置
#   （`/home/nathan/ncr-mitm/capture_addon.py`），找不到就 **fail-closed 不錄**——
#   而那是安靜的：session 照樣起得來，只是一個 flow 都沒有。落點見 MITM_ADDON_BIND。
CLAUDE_MITM_HOST = os.environ.get(
    "CLAUDE_PTY_MITM_ADDON", os.path.join(HOST_REPO_ROOT, "mitm")
)
CLAUDE_MITM_SELF = os.path.join(_SELF_REPO_ROOT, "mitm")   # 存在性檢查用
# 容器內的落點。⚠ 與 dev-container/entrypoint.sh 的 CAPTURE_ADDON 同一個路徑——
# 對不上就是「錄了個寂寞」，而且沒有任何錯誤訊息。
MITM_ADDON_BIND = "/home/nathan/ncr-mitm"
# ⚠ 這裡曾經有 MITM_OUTPUT_DIR / MITM_OUTPUT_DIR_SELF：capture 的 .mitm 共用落在
#   host 的一個共用目錄。ADR 0014 之後那是 per-user 的（space 底下的 `user-{id}/ncr/mitm`
#   ——掛的是 `ncr/` 那個**根**，不是 mitm 本身，見 user_mounts 與 NCR_HOME_BIND）
#   ——那個目錄裡是**完整的 API 請求本文**，共用它是先前盤點時最容易漏掉的一項。
#   容器內的落點仍是同一個路徑，見下方 MITM_BIND。

# --- SSH agent 轉發：預設關，由部署者明確開啟（ADR 0011）------------------------------
#
# 為什麼 CLI 憑證可以預設就有、SSH agent 卻要人明確打開：兩者的爆炸半徑不同，
# 而且**歸屬不同**。
#   - CLI 憑證是**每個人自己的**：他貼進網頁的 token，加密存在資料庫、開場時只送進
#     他自己那一場。用途也只有一個，呼叫那家 AI 供應商的 API。
#   - SSH agent 是**部署層共用的一把能力**：它可以認證任何一台信任那把 key 的主機
#     （內網 git、正式機、跳板機），而且是以「你」的身分。開了它，每一個能建立
#     session 的人都拿得到同一把，沒有辦法只給其中一個人。
#
# 所以是 opt-in：`CLAUDE_PTY_SSH_AUTH_SOCK` 填 **host 上** agent socket 的路徑（由 daemon
# 解讀，ADR 0009），不填就完全不掛。掛的落點是 /ssh/ssh_sock，image 的 ENV SSH_AUTH_SOCK
# 本來就指向那裡（見 dev-container/Dockerfile），與 run script 對齊。
#
# ⚠ 這不是「安全地分享一把 key」的機制，只是把 host 的取捨原樣延伸進 session。要限縮
#   請在 host 端做（另起一個只加了受限 key 的 agent，把那個 socket 指過來），控制平面
#   沒有能力替你篩掉 agent 裡的任何一把 key。
SSH_AUTH_SOCK_HOST = os.environ.get("CLAUDE_PTY_SSH_AUTH_SOCK", "").strip()
# 容器內的落點。與 image 的 ENV 同值——改這裡就要改 image，兩邊不對上等於沒掛。
SSH_AUTH_SOCK_BIND = "/ssh/ssh_sock"

# ⚠ CLI 憑證雖然是 per-user 的，仍然不表示「營運者碰不到」：它們用同一把由 SECRET_KEY
#   導出的金鑰加密，拿到設定檔加資料庫就全部解得開，而管理員還能代改任何人的密碼。
#   把帳號開給誰，等於請他信任你——這是開帳號時就要做的判斷，不是事後補得回來的。

# [癒痕] trivy cache 曾經是 host 目錄的 bind mount（`~/.cache/ncr-trivy`，HOST/SELF 兩個
# 常數 + 控制平面先 mkdir）。ADR 0018 改成 named volume，那兩個常數與那次 mkdir 一起退役。
# 留這段是因為刪掉會弄丟一個反直覺的理由：**當時「必須由我們先建」是對的**——bind mount
# 的來源不存在時 dockerd 會建成 root:root，容器內的 nathan 寫不進去。改成 volume 之後
# 那件事由 docker 用 image 裡的路徑與擁有者初始化，所以不再需要，也**不可以**再加回來。

# semgrep-rules（A4 SAST 軌道的規則 repo）：host 維護一份 clone，:ro **共用**掛進每個
# session（比照 run script，規則庫沒有 per-user 的意義）。HOST 版供掛載（daemon 解讀）、
# SELF 版供控制平面檢查存在性（ADR 0009；compose 以同一路徑 :ro 掛給控制平面）。
# 掛載判準在 sessions.build_run_kwargs：要有 `.git` 才算真的 clone——daemon/compose 在
# 來源缺席時會以 root 建出**空目錄**頂替，只驗 isdir 會把空殼掛進去。
# 這是「人要自己準備」的路徑之一（`git clone` 一份 semgrep-rules 即可）。
# ⚠ 預設值**與 dev-container 的 run script 同一條**：它讀 `NCR_OPENGREP_RULES`，預設
#   `$HOME/semgrep-rules`。兩邊指到不同地方的話，人的路徑掃得到規則、網頁開的 session
#   掃不到，而症狀是「A4 軌道自動跳過」這種安靜的缺席。
SEMGREP_RULES_HOST = os.environ.get(
    "CLAUDE_PTY_SEMGREP_RULES",
    os.environ.get("NCR_OPENGREP_RULES", os.path.join(HOST_HOME, "semgrep-rules")))
SEMGREP_RULES_SELF = os.environ.get(
    "CLAUDE_PTY_SEMGREP_RULES_SELF",
    os.environ.get("NCR_OPENGREP_RULES", os.path.join(_SELF_HOME, "semgrep-rules")))
SEMGREP_RULES_BIND = "/home/nathan/semgrep-rules"

# trivy DB 的更新（見 server/trivy_db.py）。
# ⚠ 這是**網頁這條路徑原本完全沒有的東西**：entrypoint.sh 裡 trivy 出現 0 次，所以從
#   網頁開的 session 從來沒更新過 DB，全靠「這台機器上曾經有人跑過 run script」。
# 關掉它的意思是「我自己負責維護那份 cache」——不是「不需要 DB」。關了之後 A2 用的是
# 那份 cache 當下的內容，可能很舊，而且沒有任何人會提醒你。
TRIVY_DB_UPDATE = os.environ.get("CLAUDE_PTY_TRIVY_DB_UPDATE", "1").strip() not in (
    "", "0", "false", "no", "off")
# 那顆一次性更新容器的硬上限（秒）。**與 run script 的 `timeout -k 10 180` 對齊**：
# 兩條路徑對「等多久算太久」講的應該是同一件事。網路半死不活時，不讓「更新 DB」變成
# 「卡住開場」。
TRIVY_DB_TIMEOUT = int(os.environ.get("CLAUDE_PTY_TRIVY_DB_TIMEOUT", "180"))

# cache 改用 **named volume**（ADR 0018）：volume 首次掛載且為空時，docker 用 image 裡
# 該路徑的內容與擁有者初始化它，host 的 uid 完全不進場——trivy 因此離開 uid 對齊那條鏈
#（ADR 0017）。名字固定、不吃 compose 的 project 前綴，人的路徑（run script）才掛得到
# 同一份，兩條路徑繼續共用那 ~1.2 GB。
TRIVY_CACHE_VOLUME = os.environ.get("CLAUDE_PTY_TRIVY_CACHE_VOLUME", "ncr-trivy-cache")

# 「上次更新成功是什麼時候」的時間戳，由控制平面自己持有。
# ⚠ **為什麼不去讀 volume 裡的 metadata.json**：那要把 volume 掛進控制平面，而控制平面的
#   image **沒有** /home/nathan/.cache/trivy 這個路徑。實測出來的規則是「**掛載時仍為空**
#   就會被該 image 的內容與擁有者初始化」——所以控制平面掛了、只要沒寫東西，volume 還救
#   得回來；但只要有任何東西在 root 擁有的狀態下被寫進去，它就**永久**卡在 root:root，
#   而且無聲。我們不需要掛它就達得到目的，那就不要多開這個機會。
#   所以寧可自己記一個時間戳——它只是「要不要費事起一顆容器」的節流器，真正的鮮度判斷
#   仍然在容器裡由 trivy 自己做（`--download-db-only` 該 no-op 就 no-op）。
#   路徑跟著 DB 走，定義在 `DB_PATH` 旁邊（那個常數在本檔案更下面才成立）。
# 節流間隔（秒）。trivy 上游的 DB 每 6 小時更新一次，比它更密集地去問只是白起容器。
TRIVY_DB_MIN_INTERVAL = int(os.environ.get("CLAUDE_PTY_TRIVY_DB_MIN_INTERVAL", str(6 * 3600)))

# --- ttyd 綁定位址（ADR 0009）------------------------------------------------------
# 非容器化：綁 127.0.0.1，nginx 在同一台 host。
# 控制平面容器化：ttyd 跑在控制平面容器內，nginx 是同網路的兄弟容器，故要綁 0.0.0.0
# ——這些 port 完全不發布到 host，只存在於內部 docker network，ADR 0005「不對外曝露」
# 的性質不但保留且更徹底。
# image 內放著兩顆 ttyd：C 版（上游 1.7.7 的 release binary）與同版號的 Rust 重寫
# （從分支編出來，見 deploy/Dockerfile）。**選哪一顆是使用者的偏好、存在 DB**
# （`users.ttyd_bin`），從管理畫面的「設定」切換，不是環境變數——env 的話要改檔又要重啟，
# 而這個開關的用途正是「開兩個終端當場比一比」。
#
# 這裡只定義**白名單與預設**：值一律要落在白名單內才收（它會變成 argv[0]，不可以讓
# 任何字串直接進去），而 `views._OUR_TTYD_NAMES` 也以它為準——那組名字是送 SIGTERM
# 前唯一的身分把關，比錯的後果是「所有 view 被判死」或「誤殺無關程序」。
TTYD_BINS = {"ttyd": "C", "ttyd-rust": "Rust"}
TTYD_BIN_DEFAULT = "ttyd"
# 兩顆 binary 的**能力差異寫在 views._TTYD_EXTRAS**（每顆一組參數建構策略）：
# Rust 版有伺服器端 --title 與 --auth-url／--auth-cache-ttl，C 版沒有。判斷一律
# keyed on binary 名，不拿顯示標籤（上面的 "C"/"Rust"）當依據——標籤是給畫面看的。

# ttyd-rust 的 --auth-url 放行快取秒數（0＝不快取，每個請求都問控制平面一次）。
# 每個靜態檔案與 WS 升級都是一次 auth 子請求，快取把它壓成每 TTL 一次。
# 取捨：TTL 內撤銷（改密碼收終端）對**新請求**的生效最多晚這麼多秒；已升級的
# WebSocket 本來就不受任何 TTL 影響（授權只發生在連線交出去之前）。
TTYD_AUTH_CACHE_TTL = int(os.environ.get("CLAUDE_PTY_TTYD_AUTH_CACHE_TTL", "2"))

def ttyd_bin_or_default(value: str | None) -> str:
    """把存下來的偏好收斂成一個合法的 binary 名稱。

    None（沒設過）與不認得的值（白名單改過、DB 裡留著舊值）都退回預設——**不可以**直接
    拿 DB 裡的字串去 exec。
    """
    return value if value in TTYD_BINS else TTYD_BIN_DEFAULT

TTYD_BIND = os.environ.get("CLAUDE_PTY_TTYD_BIND", "127.0.0.1")
# 供 nginx / 管理畫面組 URL 用；容器化時設為控制平面的容器名。
TTYD_HOST = os.environ.get("CLAUDE_PTY_TTYD_HOST", "127.0.0.1")

# --- per-user 的 agent 狀態空間（ADR 0014）------------------------------------------
#
# 每個使用者一個目錄，以 CLAUDE_CONFIG_DIR 把 CLI 的整份狀態（transcript、settings、
# skills、.claude.json）指過去。host 的 ~/.claude **不再進 session**。
#
# ⚠ 預設**不放 ~/Documents 底下**：macOS 上 iCloud Drive 常同步該目錄（執行期狀態會被
#   送上雲，而且檔案可能被 evict 成佔位 stub，容器讀到的就不是真內容），另有 TCC 授權與
#   備份工具的干擾。這是高頻寫入的狀態目錄，不該落在會被同步的路徑下。
SPACE_HOST = os.environ.get("CLAUDE_PTY_SPACE", os.path.join(HOST_HOME, "claude-pty-space"))
# 控制平面自己看得到的同一個空間（要在裡面 mkdir 與寫種子檔）。容器化後 $HOME 是 /home/app，
# 與 host 路徑不同，故 compose 會明講（ADR 0009）。
SPACE_SELF = os.environ.get("CLAUDE_PTY_SPACE_SELF", SPACE_HOST)

# 容器內的落點。CLAUDE_CONFIG_DIR 用的就是預設路徑 `/home/nathan/.claude`——不是為了省事，
# 而是「設定目錄」與「家目錄下的 .claude」本來就該是同一個；差別只在它現在來自 per-user
# 的 host 目錄。⚠ 這個 env **必須設**：`.claude.json` 的位置是
# `CLAUDE_CONFIG_DIR || homedir()`，不設的話它會落在容器 writable layer，換一個容器就沒了。
# session 容器內那個使用者的 uid。**不是 1000**——`dev-container/Dockerfile` 的 `useradd`
# 沒指定 uid，而 ubuntu:24.04 的 base image 已經佔走 1000（`ubuntu`），所以 nathan 落在 1001。
# 這個數字有實質後果：per-user 空間是 0700 的，控制平面（APP_UID）建目錄、session 容器
# 寫 transcript，**兩邊 uid 必須相同**，否則容器一個字都寫不進去。
# macOS Docker Desktop 走 virtiofs 的 uid 對映，不受影響；Linux 上 APP_UID 要設成這個值。
SESSION_UID = int(os.environ.get("CLAUDE_PTY_SESSION_UID", "1001"))

# 登入憑證交進容器的落點。**這條路徑刻意不在任何 bind mount 底下**——它是容器自己的
# writable layer，靠 `put_archive` 在 create 與 start 之間寫進去（同 gitlab 代理送
# nginx.conf 的手法），所以 host 磁碟上從來沒有這個檔。
#
# ⚠ 為什麼不用環境變數（原本的做法）：env 會出現在 `docker inspect` 的 `Env` 陣列、
#   `/proc/1/environ`，而且**每一個子行程都繼承它**——CLI 會開 shell，shell 會跑 AI 要求
#   的任何指令，每一層都帶著這個值。CLI 自己在 spawn 子行程前就把這幾個憑證變數從環境
#   刪掉，我們卻在外面一層又加回去。
# ⚠ entrypoint.sh 讀完會立刻 `rm`，只留一個已開的 fd（實測：檔案不在了照樣讀得到）。
#   所以容器內能看到這個檔的窗口是毫秒級，之後連容器裡也找不到它。
# ⚠ **它必須待在一個 session 使用者自己擁有的目錄裡，不能直接放 `/run` 底下。**
#   unlink 要的是**父目錄**的寫權限，不是檔案本身的——檔案給 0600 且屬於他也沒用，
#   `/run` 是 root 的 0755，他刪不掉。實測症狀是 `rm: Permission denied`，而 entrypoint
#   有 `set -e`，於是整個容器 exit 1，看起來像 session 建不起來（2026-08-07 踩到）。
#   所以 `_put_cli_token` 的 tar 會連同 `cpty/` 這個目錄一起送，並且把它設成他的。
SESSION_TOKEN_DIR = "/run/cpty"
SESSION_TOKEN_FILE = SESSION_TOKEN_DIR + "/token"

# 憑證怎麼交給 CLI。**兩條路都留著，per-session 可選。**
#
#   fd  — 走上面那個檔案 + file descriptor（預設）。憑證不進環境，`docker inspect`、
#         `/proc/1/environ`、子行程的環境都看不到值。
#   env — 直接放進 `CLAUDE_CODE_OAUTH_TOKEN` 環境變數（原本的做法）。
#
# ⚠ **為什麼安全的那條不是唯一一條**：fd 依賴的
#   `CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR` **沒有寫進官方文件**——它存在、Anthropic
#   自家的網頁版也在用（issue #57925 的環境資訊裡就有），但那是內部實作，一次版本升級
#   就可能消失或改名，而症狀會是「所有新 session 都要求登入」。env 那條是文件寫過的路。
#   押在一個沒有文件的機制上，就該留一條有文件的退路，而且要讓人**當場切得回去**，
#   不是等我改完程式再重新部署。
# ⚠ 這個開關**不是**安全與否的偏好題，是「新的那條還通不通」的逃生口。預設一律 fd；
#   選 env 的時機只有一個：fd 那條壞了。畫面上的文案要這樣寫，不要寫成「兩種風格」。
TOKEN_DELIVERIES = ("fd", "env")
DEFAULT_TOKEN_DELIVERY = os.environ.get("CLAUDE_PTY_TOKEN_DELIVERY", "fd")
if DEFAULT_TOKEN_DELIVERY not in TOKEN_DELIVERIES:      # 打錯字不可以靜靜落到危險的那邊
    raise SystemExit(f"CLAUDE_PTY_TOKEN_DELIVERY 只能是 {' / '.join(TOKEN_DELIVERIES)}"
                     f"（拿到 {DEFAULT_TOKEN_DELIVERY!r}）")

CLAUDE_CONFIG_BIND = "/home/nathan/.claude"
# 容器內那個「寫了要留著」的根目錄。**dev-container 的兩條路徑都落在它底下**：
#   · capture 的 .mitm  → `<根>/mitm`（entrypoint.sh 的 CAPTURE_DIR）
#   · 審查報告的 archive → `<根>/{group}/{repo}/…`（skill 的 workspace-paths）
# run script 把 host 的 `$HOME/ncr` 掛到這裡；網頁開的 session 則是 per-user
# （見 user_mounts）——同一個容器內路徑，不同的 host 來源。
# ⚠ 這個字串要與 entrypoint.sh 和 skill 三方一致。對不上的症狀是安靜的：
#   東西照寫，只是寫進 writable layer，容器一收就沒了。
# ⚠ **只掛這一個根，不要把 `mitm/` 另外掛一次**：那會變成巢狀 bind mount，而巢狀掛載
#   要求落點先存在（virtiofs 上尤其），少一個子目錄就是啟動失敗。entrypoint 自己會
#   mkdir capture 的子目錄。
NCR_HOME_BIND = "/home/nathan/ncr"
# capture 的落點（entrypoint.sh 的 CAPTURE_DIR）。**衍生值，不要另外掛。**
MITM_BIND = f"{NCR_HOME_BIND}/mitm"
# 使用者自己的持久化空間。session 內唯一「寫了會留下來」的地方（cwd 是 writable layer，
# 容器一收就沒）。⚠ 目前**沒有任何大小限制**，見 ADR 0014 的「暫不做磁碟配額」。
#
# ⚠ 曾經是 `/data`，2026-07-29 改成這裡，兩個理由：
#   1. `/data` 在**控制平面**那邊已經是 registry（SQLite）的落點——同一個字串在兩個容器裡
#      是兩個完全不同的東西。
#   2. 名字要自己說得出它是什麼。放在 $HOME 底下，與其他掛進來的目錄同一層。
# ⚠ **不要改成掛進 WORKDIR 底下**（`code-review/persistent-data` 之類）。看起來比較好發現，
#   代價是 cwd 從此不是空的：`git clone <url> .` 會直接 `destination path '.' already exists`
#   （在真 image 裡驗過），而錯誤訊息完全指不到原因；cwd 裡的批次刪除也會掃到它。
DATA_BIND = "/home/nathan/persistent-data"

# `.claude.json` 的最小種子（ADR 0014）。全新空間的第一場會連撞三道互動對話，而
# `_is_ready()` 只看「畫面靜止」——對話框畫面一樣靜止，於是初始 prompt 被打進選單裡。
# 最惡劣的是 bypass 那道：預設選項停在「No, exit」，送出的第一個 Enter 就是結束容器。
#
# ⚠ 這是 CLI 的私有格式（實測 2.1.220），升版可能改名，而症狀只出現在「某個使用者的
#   第一場」，極難聯想。image 換版時照 ADR 0014 的方法論煙測一次：**要用 entrypoint 的
#   真實 argv**（`--dangerously-skip-permissions`，少了它看不到 bypass 那道），
#   **比對字串不可以含空白**（TUI 用游標移動排版，字之間沒有真的空白字元）。
CLAUDE_JSON_SEED = {
    "hasCompletedOnboarding": True,
    "theme": "dark",
    "autoUpdates": False,
    "bypassPermissionsModeAccepted": True,
}

MOUNTS = {} if os.environ.get("CLAUDE_PTY_NO_MOUNTS") else {
    # 這裡只留**所有使用者共用**的掛載。per-user 的那些由 user_mounts() 依 user_id 組出來
    # ——它們吃同一個 CLAUDE_PTY_NO_MOUNTS 開關（見該函式），測試才隔離得掉。
    #
    # trivy 漏洞 DB 的持久化快取（與 run script 掛**同一顆 named volume**，共用同一份）。
    # ⚠ 這不是效能微調：沒有這個掛載，每開一個 session 都是全新的空 cache，得重新抓、
    #   解開約 1GB 的 DB——實測整整 36 秒。而 restricted profile 更慘：firewall 的白名單
    #   沒有 ghcr.io，牆內根本抓不到，A2 軌道會整場空轉。
    # ⚠ **更新不是在 entrypoint 做的。** 這段註解一度寫著「entrypoint.sh 在套 iptables
    #   之前必須等 DB 更新跑完」——那個機制從來不存在（`entrypoint.sh` 裡 trivy 出現
    #   0 次）。實際負責更新的是 `server/trivy_db.py`，由控制平面在建 session **之前**
    #   於一顆一次性容器裡跑，牆外、有租約串行化。放那裡而不是 entrypoint 的理由，
    #   見那支模組的說明（併發不會壞，但會重複下載）。
    # ⚠ key 是 **volume 名稱**，不是 host 路徑（ADR 0018）。docker-py 的 volumes 參數
    #   兩者同格式；下游任何「把 key 當路徑用」的程式碼都要能分辨（見 preflight）。
    TRIVY_CACHE_VOLUME: {"bind": "/home/nathan/.cache/trivy", "mode": "rw"},
}

# 「這一輪是測試」的標記。用途：打在容器 label 上讓正式 reconciler 跳過測試建的容器，
# 見 TEST_LABEL_DEFAULT_KEY 那段。
TEST_MARK = os.environ.get("CLAUDE_PTY_TEST_MARK")   # 測試設為 "1"，正式部署不設

# --- per-user 網路與 GitLab 代理（ADR 0016）-----------------------------------------
#
# 每個**開過 session 的**使用者有一張自己的 docker network（`claude-pty-user-{id}`），
# 他所有的 session 都住在上面。設了 PAT 的人，那張網上還會多一顆 nginx（network alias
# 固定叫 `gitlab-proxy`）：容器裡的 git 與 API 呼叫裸打不帶 auth，由代理蓋上**他自己的**
# PAT。
#
# ⚠ **網路不綁 GitLab。** 網路是 session 的家，代理只是掛在上面的其中一樣東西——
#   GitLab 功能整個關掉、或這個人沒設 PAT，網路照建。反過來寫（沒 PAT 就不建網路）
#   會讓那些人的 session 沒有網路可加入，退回共用網路或預設 bridge，隔離當場破掉。
# ⚠ **為什麼代理是 per-user 而不是 per-session**：nginx 的 `limit_req_zone` 是 per-instance，
#   一個人開 N 場就是 N 顆 nginx、N 個獨立的計數桶，`10r/s` 對 GitLab 變成 `N×10r/s`
#   ——等於沒有限流，而且越是「同時很多」的情境越失效。那不是調參數修得好的，是拓樸
#   問題。收斂成 per-user 之後，桶才對得上「人」這個單位。
# ⚠ 跨使用者的隔離**來自網路邊界，不是防火牆規則**，所以 `unrestricted` profile 也成立。
# ⚠ **一人一張網，而位址池是整台機器共用的**（預設 31 張，見 README 的「同時在線人數的
#   上限」）。這是這個拓樸的代價，而它是有意識付的：用完的時候 session 開不起來並講出
#   下一步，勝過偷偷把人塞回共用網路。

# 這套東西要代理的 GitLab 主機名（不含 https:// 與結尾斜線）。
#
# ⚠ **預設是空字串，代表整個功能關閉**：一顆代理都不會建，設定頁會說「部署者尚未設定
#   GitLab 主機」，git URL 也不會被改寫。這是刻意的——預設值若是一個看起來像真的網域，
#   沒設定的部署會真的去建代理、對著別人的主機打，然後回一堆指不到原因的錯。
#   要用就在 `.env` 明確填上自己的（`deploy/.env.example` 有說明）。
GITLAB_HOST = os.environ.get("CLAUDE_PTY_GITLAB_HOST", "").strip()
# git over SSH 的主機名，**只**用於把 `git@host:` / `ssh://git@host/` 改寫成走代理。
# 多數部署與上面同一個，所以預設沿用；分成兩個旋鈕是因為有些組織把 SSH 掛在別的名字
# （`git.example.com` 對 `gitlab.example.com`），那時只改一個會讓 SSH 形式靜靜不改寫。
GITLAB_SSH_HOST = os.environ.get("CLAUDE_PTY_GITLAB_SSH_HOST", "").strip() or GITLAB_HOST

# session 在 per-user 網路上用來找代理的名字與埠——也就是**容器裡看到的 API base**
# （`http://gitlab-proxy:5678`）。以 env 注進 session（見 sessions.build_run_kwargs 的
# `NCR_GITLAB_API_BASE`），呼叫端不必把它寫死。
# ⚠ 改了 alias 就等於改了容器裡那個名字，所有寫死 `gitlab-proxy` 的呼叫端都要跟著改，
#   而症狀是安靜的（DNS 解不到 → 連線失敗，看不出是名字換了）。
PROXY_ALIAS = os.environ.get("CLAUDE_PTY_GITLAB_PROXY_ALIAS", "").strip() or "gitlab-proxy"
PROXY_PORT = int(os.environ.get("CLAUDE_PTY_GITLAB_PROXY_PORT", "5678"))
# 容器裡看到的 API base。衍生值，**不要另外開一個 env**——兩個來源就會漂。
PROXY_BASE_URL = f"http://{PROXY_ALIAS}:{PROXY_PORT}"

# 代理容器的 image。與 `gitlab-proxy/docker-compose.yml`（獨立版那套）用同一顆，
# 兩邊的 nginx 行為才對得起來。
# ⚠ 用 `.strip() or` 而不是 `os.environ.get(k, default)`：compose 傳的是
#   `${CLAUDE_PTY_GITLAB_PROXY_IMAGE:-}`——沒設時它給的是**空字串**而不是「沒有這個變數」，
#   `get()` 會原樣回空字串，於是 image 名稱變成 ""。這在 compose 那種寫法下是預設會踩到
#   的，不是邊角情境。
# ⚠ 要**可重現**的部署請在 `.env` 釘 digest（`nginx@sha256:…`）。這裡不預設釘一個，
#   是因為釘死的 digest 會隨時間變成「沒有人知道為什麼是這一顆」的常數。
PROXY_IMAGE = (os.environ.get("CLAUDE_PTY_GITLAB_PROXY_IMAGE", "").strip()
               or "nginx:alpine")
# 一顆只轉發 HTTP 的 nginx，資源給得比 session 小得多。**要給**：沒有上限的話一個失控的
# clone 就能把 host 的記憶體吃光，而它不是使用者看得到的東西。
# ⚠ 不可以太小：git clone 大 repo 時 nginx 仍有 socket buffer 與 TLS 狀態。
PROXY_MEM_LIMIT = os.environ.get("CLAUDE_PTY_GITLAB_PROXY_MEM_LIMIT", "128m")
PROXY_PIDS_LIMIT = int(os.environ.get("CLAUDE_PTY_GITLAB_PROXY_PIDS_LIMIT", "64"))

# 代理容器與網路的命名。**決定性**——收的時候不必先查 label 就找得到。
PROXY_NAME_PREFIX = "claude-pty-gitlab-u"
USER_NETWORK_PREFIX = "claude-pty-user-"
if TEST_MARK:
    # 🛡 **測試與正式 stack 共用同一顆 dockerd，而 docker 的名稱是整台機器共用的命名
    #   空間。** 這兩個名字由 **DB 的 user id** 組出來，而測試用的是全新的暫存 DB
    #   （id 從 1 發）——必然撞上正式 stack 的 user-1。三種撞法各自的後果：
    #     · `containers.get(name)`   → 撿到**正式的**那一顆，接著被當成自己的來熱重載
    #     · `networks.create(name)`  → 409 Conflict，補建路徑整條走不下去
    #     · `remove_container(name)` → 刪掉**正在服務**的代理
    #   ⚠ label 的 allow-list（測試的 scoped client）擋得住**發現**（list），擋不住
    #     **按名字定址**的操作——那是兩個不同的面，必須分別關掉，只做一邊等於沒做。
    #   依 TEST_MARK 自動切開而不是要各測試自己設：漏設是靜默的，而代價是刪掉別人的東西。
    PROXY_NAME_PREFIX = "claude-pty-test-gitlab-u"
    USER_NETWORK_PREFIX = "claude-pty-test-user-"

# 代理自己的 label。⚠ **絕不可以用 SESSION_LABEL_KEY/VALUE**：那會讓它進到 reconciler 的
# `live` map，而它在 registry 裡沒有對應列 → ORPHAN_GRACE 之後被 `_remove_orphans` 當孤兒
# 刪掉，中途還會被狀態刷新迴圈寫進別人的 docker_state。
PROXY_LABEL_KEY = "claude-pty.gitlab-proxy"
PROXY_LABEL_VALUE = "1"
PROXY_OWNER_LABEL = "claude-pty.owner"          # 值是 user_id，收斂時用來對應使用者
PROXY_FILTERS = {"label": f"{PROXY_LABEL_KEY}={PROXY_LABEL_VALUE}"}
# per-user 網路也標起來，才掃得到「該回收的空網路」（不然要靠名字前綴猜）。
NETWORK_LABEL_KEY = "claude-pty.user-network"
NETWORK_LABEL_VALUE = "1"
NETWORK_FILTERS = {"label": f"{NETWORK_LABEL_KEY}={NETWORK_LABEL_VALUE}"}

# 代理容器的額外 `/etc/hosts` 條目，格式 `name:ip`，逗號分隔。**預設空的。**
#
# 逃生口，不是正常部署會用到的東西。情境：開發時用 SSH 隧道把 GitLab 轉到自己機器的
# 127.0.0.1:443，並在 host 的 `/etc/hosts` 指過去讓自己的 curl／git 走得通。
# **Docker Desktop 的 DNS 會把 host 的 /etc/hosts 帶進容器**，於是代理也把 GitLab 解析成
# `127.0.0.1`＝它自己的 loopback＝沒有人在聽，每個請求都是 502。對策是明確告訴代理該連
# 去哪：`CLAUDE_PTY_GITLAB_PROXY_EXTRA_HOSTS=gitlab.example.com:host-gateway`
# （`host-gateway` 是 docker 認得的特殊值，指向 host 本身）。
#
# ⚠ **這是一個能讓代理指向任意主機的旋鈕。** 它只由部署者設定（環境變數，使用者碰不到），
#   但設錯就是把所有人的 PAT 送到別的地方去。不需要就別設。
PROXY_EXTRA_HOSTS = {
    kv.split(":", 1)[0].strip(): kv.split(":", 1)[1].strip()
    for kv in os.environ.get("CLAUDE_PTY_GITLAB_PROXY_EXTRA_HOSTS", "").split(",")
    if ":" in kv
}

# PAT 的長度上限。用途不是安全（字元集才是），而是**清楚**：貼錯東西（整份檔案、一段
# JSON）時直接在入口回 400，不要加密存起來再讓人納悶為什麼 session 裡不能用。
# GitLab 自己發的 PAT 是 26 字元左右；留大幅餘裕給其他形式的 token。
GITLAB_PAT_MAX = int(os.environ.get("CLAUDE_PTY_GITLAB_PAT_MAX", "512"))

# --- 自訂 CA：內部憑證簽的 GitLab -----------------------------------------------
#
# 代理對上游是 `proxy_ssl_verify on`，信任錨預設是容器內的系統 CA
# （`/etc/ssl/certs/ca-certificates.crt`）。**內部 CA 簽的 GitLab 不在那份裡面**，
# 於是每一個請求都在 TLS 這一關失敗。
#
# ⚠ **失敗的形狀很惡劣，所以要知道自己在找什麼**：容器**是健康的**（nginx 跑得好好的），
#   只是每個請求回 502。而 `users.gitlab_proxy_error` 那條訊號是靠「代理沒活著」觸發的
#   （見 reconciler._note_proxy_down），所以它**不會亮**——畫面全綠、git 全掛，真正的
#   原因只在容器的 error_log 裡。preflight 會在啟動時先喊一次，見 sessions.preflight。
#
# 填 **host 上** CA 檔（PEM）的絕對路徑，由 daemon 解讀（ADR 0009）。不填＝維持現狀，
# 用系統 CA。SELF 版供控制平面自己做存在性檢查。
GITLAB_CA_FILE = os.environ.get("CLAUDE_PTY_GITLAB_CA_FILE", "").strip()
GITLAB_CA_FILE_SELF = os.environ.get(
    "CLAUDE_PTY_GITLAB_CA_FILE_SELF", "").strip() or GITLAB_CA_FILE
# 容器內的落點。⚠ 放 `/etc/nginx/` **之外**：那個目錄是 `put_archive` 在寫的
#   （nginx.conf 與熱重載的暫存檔），把一個 bind mount 的檔案混進去只會讓兩種寫入機制
#   在同一個目錄上打架。
GITLAB_CA_BIND = "/etc/ssl/gitlab-ca.crt"

# ⚠ **這裡永遠不會有「關掉 TLS 驗證」的開關，那是刻意的。**
#   這顆容器存在的唯一理由是保管別人的 PAT——對上游不驗憑證，等於把「憑證不進 session」
#   買到的東西，在代理到 GitLab 這一段原樣送給任何一個中間人。內部 CA 的正確解法是
#   **把那個 CA 給它**（上面那個變數），不是不驗。
#   覺得「先 `proxy_ssl_verify off` 讓它動起來、之後再說」的人請停在這裡：那個「之後」
#   不會來，而它壞掉的時候沒有任何訊號。

# 代理**連續**起不來幾輪之後，才把錯誤訊息端到畫面上（`users.gitlab_proxy_error`）。
# ⚠ 不是 1：代理偶爾重啟一輪是正常的（重新部署、daemon 抖動），每一次都對使用者喊
#   「你的 GitLab 壞了」就是狼來了——喊久了真的壞掉時沒有人會看。
# ⚠ 也不能太大：這條訊號要救的是「設定錯了、而且永遠不會自己好」那一類（最典型的是主機名
#   打錯 → nginx 啟動時解不開 upstream → 每輪重啟、每輪再死）。3 輪 ≈ 90 秒，夠濾掉抖動，
#   又不會讓人對著完全錯的方向查半小時。
PROXY_FAIL_THRESHOLD = int(os.environ.get("CLAUDE_PTY_GITLAB_PROXY_FAIL_THRESHOLD", "3"))


def gitlab_enabled() -> bool:
    """部署者有沒有設定 GitLab 主機——**整個功能的總開關**。

    沒設就不建網路、不建代理、不改寫 git URL、設定頁不收 PAT。每個入口各自判斷會漂，
    收斂成一支：呼叫端問這個，不要自己 `if config.GITLAB_HOST`。
    """
    return bool(GITLAB_HOST)


# init-firewall.sh 也跟著 bind-mount（比照 entrypoint.sh）：改政策不必重新 build image。
# ⚠ 一律 :ro——sudoers 白名單的是**路徑**，所以那個路徑的內容就是 root 執行的程式碼。
INIT_FIREWALL_SH_SELF = os.path.join(_SELF_REPO_ROOT, "dev-container", "init-firewall.sh")
INIT_FIREWALL_SH = os.path.join(HOST_REPO_ROOT, "dev-container", "init-firewall.sh")
INIT_FIREWALL_BIND = "/usr/local/bin/init-firewall.sh"


def user_space(user_id: int, *, host: bool = True) -> str:
    """某個使用者的空間根目錄。

    用 **id 不是 username**：帳號不能刪（ADR 0010，退場是改掉密碼），username 不可回收但也不是
    穩定的錨（未來若允許改名，目錄會跟著漂）。id 是 FK、是真正穩定的那一個。

    `host=True` 回傳要交給 docker daemon 的路徑，`False` 回傳控制平面自己讀寫的路徑
    ——容器化部署下兩者不同（ADR 0009）。
    """
    return os.path.join(SPACE_HOST if host else SPACE_SELF, f"user-{user_id}")


def user_mounts(user_id: int) -> dict:
    """某個使用者的 per-user 掛載（ADR 0014）。key 是 **host 路徑**（daemon 解讀）。

    ⚠ 與 MOUNTS 吃**同一個** CLAUDE_PTY_NO_MOUNTS 開關。少了這一條，六個用該旗標保持
      隔離的測試檔會開始在 host 上長出 user-N 目錄，而它們正是為了「絕不碰使用者真實
      檔案」而存在的（CLAUDE.md 禁止清單第五條）。

    """
    if not MOUNTS:
        return {}
    root = user_space(user_id)
    return {
        os.path.join(root, "claude"): {"bind": CLAUDE_CONFIG_BIND, "mode": "rw"},
        os.path.join(root, "persistent-data"): {"bind": DATA_BIND, "mode": "rw"},
        # capture 與審查報告都寫在這個根底下（見 NCR_HOME_BIND）。**per-user**：
        # capture 裡是完整的 API 請求本文、報告是個人的審查紀錄，兩者都不該共用。
        os.path.join(root, "ncr"): {"bind": NCR_HOME_BIND, "mode": "rw"},
    }

# --- 檔案上傳（貼圖）----------------------------------------------------------------
#
# 這是**唯一一條使用者能往伺服器寫東西的路**（PTY 是字元流，二進位資料過不去，所以
# 另開一條：人上傳 → 檔案落在他的 persistent-data → 回容器內路徑 → 人自己貼給 AI）。
# 正因為唯一，三道防護都在這裡集中：副檔名白名單、大小上限、路徑穿越（見 app.upload_file）。
#
# 白名單走「收什麼」不走「擋什麼」：用途是給容器裡的 AI 讀（圖與文件），不是給網頁
# 回放——伺服器永遠不會把這些檔案再 serve 出去，所以風險面在「寫」不在「讀」。
UPLOAD_EXTS = frozenset(
    e.strip().lower().lstrip(".")
    for e in os.environ.get("CLAUDE_PTY_UPLOAD_EXTS",
                            "png,jpg,jpeg,gif,webp,pdf,txt,md").split(",")
    if e.strip())
# 單檔上限。⚠ 三個地方要同向：這裡（逐檔驗）、MAX_CONTENT_LENGTH（Flask 整包上限，
# app.py 設為此值加 multipart 開銷）、deploy/nginx.conf 的 client_max_body_size
# （upload 那條 location）。nginx 那道要**略大於**這裡，否則使用者撞到的是 nginx 的
# 413 HTML 頁而不是我們講得清楚的 JSON。
UPLOAD_MAX_BYTES = int(os.environ.get("CLAUDE_PTY_UPLOAD_MAX_BYTES", str(10 * 2**20)))

# spawned container 的資源限制（ADR 0004 安全輪廓）。不掛 docker socket 是「不寫」即達成。
MEM_LIMIT = os.environ.get("CLAUDE_PTY_MEM_LIMIT", "4g")
NANO_CPUS = int(float(os.environ.get("CLAUDE_PTY_CPUS", "2")) * 1_000_000_000)
PIDS_LIMIT = int(os.environ.get("CLAUDE_PTY_PIDS_LIMIT", "512"))

# 使用者自取的 session 名稱：原始字串長度上限，以及接到 container 名稱尾巴的 slug 長度上限。
NAME_MAX = int(os.environ.get("CLAUDE_PTY_NAME_MAX", "25"))
NAME_SLUG_MAX = int(os.environ.get("CLAUDE_PTY_NAME_SLUG_MAX", "24"))

# 使用者名稱長度上限。沒有上限時 505 字元的名字會進資料庫，然後在帳號清單、篩選下拉、
# 稽核紀錄裡把版面推爆——而帳號**不能刪**（ADR 0010），建錯就永遠留著。
USERNAME_MAX = int(os.environ.get("CLAUDE_PTY_USERNAME_MAX", "32"))

# 每人 session 上限（ADR 0004）。個人單機用先給一個寬鬆值。配額以 DB 計數仲裁（ADR 0008）。
MAX_SESSIONS = int(os.environ.get("CLAUDE_PTY_MAX_SESSIONS", "10"))

# session 列表分頁。admin 看得到所有人的 session，筆數不受 MAX_SESSIONS 限制，
# 沒有分頁就會一次撈全部並全量渲染。
PAGE_SIZE = int(os.environ.get("CLAUDE_PTY_PAGE_SIZE", "10"))
MAX_PAGE_SIZE = max(1, int(os.environ.get("CLAUDE_PTY_MAX_PAGE_SIZE", "100")))
# ⚠ 預設頁大小**必須**落在 1–MAX_PAGE_SIZE 之內。端點是拿 PAGE_SIZE 當 `limit` 的預設值
#   再送進範圍檢查的，超出上限時「沒帶 limit 的請求」會自己撞上自己的上限——每一張列表
#   （session、歷史、帳號）一律 400，而錯誤訊息在講一個呼叫端根本沒傳的參數，等於是
#   把設定錯誤偽裝成 API 錯誤。夾回來，並在啟動診斷裡說清楚（見 sessions.preflight）。
PAGE_SIZE_CLAMPED = None if 1 <= PAGE_SIZE <= MAX_PAGE_SIZE else PAGE_SIZE
PAGE_SIZE = min(max(1, PAGE_SIZE), MAX_PAGE_SIZE)

# 計入配額的 session 狀態（creating 也算——它已佔住一個名額，等同舊版的 in-flight slot）。
ACTIVE_STATUSES = ("creating", "running")

# session container 的識別 label（ADR 0009）。
# ⚠ 絕不可用名稱前綴 `claude-pty-` 來辨識 session container——容器化部署後，compose 專案
# 名 `claude-pty` 會讓基礎設施容器也叫 claude-pty-control-1 / -nginx-1 / -reconciler-1，
# 全部符合該前綴。reconciler 的孤兒清理會把自己和 nginx 一起刪掉（2026-07-25 實測發生）。
# label 由我們建立時明確打上，基礎設施容器不會有，故不可能誤傷。
SESSION_LABEL_KEY = "claude-pty.managed"
SESSION_LABEL_VALUE = "session"
SESSION_FILTERS = {"label": f"{SESSION_LABEL_KEY}={SESSION_LABEL_VALUE}"}

# 測試建立的 container 會多打一個 label，讓**正式的** reconciler 認得出來並跳過。
# 沒有它的話反向誤傷成立：測試容器帶 session label、卻不在正式 DB 裡，正式 reconciler
# 會把它們當孤兒收掉（測試那側已有 _ScopedClient 擋住同向的誤傷，這是對稱的另一半）。
# ⚠ 兩個名字指同一個 label，用途不同，**不要合併**：
#   · TEST_LABEL_KEY         — **讀**的時候用（reconciler 判斷「要不要跳過這顆」）。
#     測試會暫時把它改掉，好讓 reconciler 願意處理測試自己建的容器。
#   · TEST_LABEL_DEFAULT_KEY — **寫**的時候用（建容器時打標記）。它永遠是真正的那個 key。
#   建立時若跟著用被改過的值，重建出來的容器就不帶真正的測試標記，同一台機器上的
#   **正式** reconciler 不會跳過它 → 測試中途被收掉。標記的是「這是測試容器」這個事實，
#   不該隨著「這一輪誰在看」而變。
TEST_LABEL_DEFAULT_KEY = "claude-pty.test"
TEST_LABEL_KEY = TEST_LABEL_DEFAULT_KEY
# ⚠ `TEST_MARK` **定義在本檔上方**。不要在這裡再賦值一次：兩份會漂掉。

# container 內的工作目錄（image WORKDIR）。寫進 registry 供 UI 顯示，也是 /resume 的分桶
# 依據——同 cwd 才看得到彼此的對話（ADR 0007）。
WORKDIR = os.environ.get("CLAUDE_PTY_WORKDIR", "/home/nathan/code-review")

# 無登入情境（CLI / 測試）下 session 的預設 owner；此帳號的 password_hash 為不可用值，
# 無法登入（ADR 0008）。
SYSTEM_USERNAME = "system"

# 密碼最短長度。個人用系統，不強制複雜度規則（長度是最有效的單一指標）。
MIN_PASSWORD_LENGTH = int(os.environ.get("CLAUDE_PTY_MIN_PASSWORD_LENGTH", "8"))

# on-demand ttyd view 的 loopback port 範圍（ADR 0008；分配由 DB 的 views.port UNIQUE 仲裁）。
TTYD_PORT_MIN = int(os.environ.get("CLAUDE_PTY_TTYD_PORT_MIN", "41000"))
TTYD_PORT_MAX = int(os.environ.get("CLAUDE_PTY_TTYD_PORT_MAX", "41100"))

# --- reconciler（ADR 0008 階段 5）------------------------------------------------
RECONCILE_INTERVAL = int(os.environ.get("CLAUDE_PTY_RECONCILE_INTERVAL", "30"))

# 孤兒 container（registry 無對應列）的寬限秒數：避免誤殺「剛建好、登錄尚未轉正」的容器。
ORPHAN_GRACE = int(os.environ.get("CLAUDE_PTY_ORPHAN_GRACE", "120"))

# `creating` 狀態的寬限秒數。create() 先寫登錄列、才花時間起 container（restricted profile
# trivy DB 未命中快取時要重抓約 1GB，可達數十秒）。這段期間「DB 有列但 docker 還沒有容器」是**正常**
# 狀態，不可判定為 gone 而刪列——否則 create 回頭找不到自己的列會失敗，還會把剛起好的
# container 一起收掉（2026-07-25 review B1）。逾期仍在 creating 才視為建立者已死。
CREATING_GRACE = int(os.environ.get("CLAUDE_PTY_CREATING_GRACE", "300"))

# idle session 回收時數。**預設 0＝停用**，這是刻意的：
# 本系統的主要用途是「長跑、偶爾回頭看」（ADR 0008 背景），而 last_active_at 只在
# 開 view 或改尺寸時更新——一個正在自主工作好幾小時的 session，在這個量測下看起來完全
# 「閒置」。以它為準回收會殺掉正在幹活的 session。要啟用前請先想清楚活躍度該怎麼量。
IDLE_TIMEOUT_HOURS = float(os.environ.get("CLAUDE_PTY_IDLE_TIMEOUT_HOURS", "0"))

# view 宣告（已搶到 port、pid 尚未寫入）的寬限秒數。寬限期內其他 worker 必須尊重該宣告，
# 不可當成殘留回收——否則兩個 worker 會搶到同一 port（跨 worker race）。逾期仍無 pid
# 代表宣告者中途掛了，可安全回收。
VIEW_CLAIM_GRACE = int(os.environ.get("CLAUDE_PTY_VIEW_CLAIM_GRACE", "30"))
# 別的 worker 已為同一 session 搶到宣告時，這邊要等它就緒多久（views._await_peer_view）。
# ⚠ 與 VIEW_CLAIM_GRACE 是**同一個窗口的兩端**：一個在等對方就緒、一個在判定對方已死。
#   這個值必須明顯小於它，否則會等到一半，對方的宣告就先被 list_views 當成逾期回收了。
#   分開寫在兩個檔案裡的話，調整時很容易只動一邊（本區其餘每一個等待值都在這裡，
#   這支是漏網的那一個）。
VIEW_PEER_WAIT = float(os.environ.get("CLAUDE_PTY_VIEW_PEER_WAIT", "6"))

# ADR 0002 的 Ctrl+P 陷阱：docker attach CLI 預設 detach 序列會扣住 Ctrl+P，必改。
DETACH_KEYS = "ctrl-x,ctrl-x"

# 初始終端尺寸（client 未指定時）。
DEFAULT_ROWS = 40
DEFAULT_COLS = 140

# 就緒偵測（sessions.wait_ready 兩段式）。
# 等待 driver 就緒的上限；逾時仍視為就緒（寧可放行也不要卡死呼叫端）。
READY_TIMEOUT = float(os.environ.get("CLAUDE_PTY_READY_TIMEOUT", "120"))
# 「畫面停止更新」多久算 TUI 畫完（階段 2 的啟發式判準，看的是 PTY 不是 docker logs）。
READY_QUIET_SECONDS = float(os.environ.get("CLAUDE_PTY_READY_QUIET", "1.5"))
# attach 上去卻一個 byte 都收不到時，等多久判定「畫面早就靜止了」。
READY_NO_OUTPUT_GRACE = float(os.environ.get("CLAUDE_PTY_READY_NO_OUTPUT_GRACE", "5"))
# 觸發重繪時「改小」與「改回」之間的停頓（SessionManager._nudge_redraw）。
# 太短的話兩次 SIGWINCH 會在 TUI 還沒反應時就抵銷掉，等於沒送；0.15 實測可行。
REDRAW_SETTLE_SECONDS = float(os.environ.get("CLAUDE_PTY_REDRAW_SETTLE", "0.15"))

# 問容器內 CLI 版本的逾時（SessionManager._cli_version）。它跑在**建立 session 的關鍵
# 路徑**上，而 docker-py 的預設是 60 秒——真的卡住時使用者要對著轉圈等一分鐘，畫面上
# 還沒有任何訊息說在等什麼。這只是一個中繼資料，等不到就留 NULL。
CLI_VERSION_TIMEOUT = float(os.environ.get("CLAUDE_PTY_CLI_VERSION_TIMEOUT", "5"))

# telemetry 探測的 TCP 逾時（秒）。短——這是「開場前順手探一下」，不是可靠性偵測；
# 探不到就降級照開場（見 sessions.create），不值得為它讓建立 session 卡住。
# ⚠ 這個值原本住在 sessions.py，是全樹唯一一個在 config 之外自己讀 os.environ 的可調值，
#   而下方 VIEW_PEER_WAIT 那段正好寫著「本區其餘每一個等待值都在這裡」——那句話當時是
#   假的（審查 F-028）。搬過來讓它變成真的。
JAEGER_PROBE_TIMEOUT = float(os.environ.get("CLAUDE_PTY_JAEGER_PROBE_TIMEOUT", "0.6"))

# 開終端前確認 container 還活著的逾時（SessionManager.probe_container）。這一問跑在
# 使用者按下「開啟」的當下，所以要短：問不到就當作不知道、照常開（fail-open），
# 寧可偶爾開到一個已經死掉的終端，也不要讓 dockerd 慢的時候整個功能不能用。
VIEW_PROBE_TIMEOUT = float(os.environ.get("CLAUDE_PTY_VIEW_PROBE_TIMEOUT", "3"))


# 持久化 registry（ADR 0008）。資料庫**就是 SQLite**，不支援第二種方言——單機部署、
# 檔案級鎖、備份＝複製一個檔案。所以設定收的是**檔案路徑**，不是連線字串：
#   CLAUDE_PTY_DB_PATH=/path/to/claude-pty.db
# 互斥語意（配額、port、租約）由 db.session_scope(immediate=True) 的 BEGIN IMMEDIATE
# 保證，見 server/db.py 的模組說明。
DB_PATH = os.environ.get(
    "CLAUDE_PTY_DB_PATH", os.path.join(_SELF_HOME, ".claude-pty", "claude-pty.db"))
if "://" in DB_PATH:
    # 這裡曾經收整條 SQLAlchemy URL。現在只有一種方言，收 URL 只是留一個「看起來可以
    # 換資料庫」的假把手——啟動就擋下來，並講清楚該給什麼。
    raise RuntimeError(
        f"CLAUDE_PTY_DB_PATH 收的是 SQLite 檔案路徑，不是連線字串（拿到：{DB_PATH}）。"
        "這套東西的資料庫就是 SQLite。")
DB_URL = f"sqlite:///{DB_PATH}"

# trivy DB「上次更新成功」的時間戳。放在 DB 旁邊是因為那個目錄本來就是**掛出來的、
# 可寫的、會留著的**——三個條件缺一不可（見上方 TRIVY_DB_MIN_INTERVAL 那段的說明，
# 那裡解釋了為什麼不去讀 volume 裡的 metadata.json）。
TRIVY_DB_STAMP = os.path.join(os.path.dirname(DB_PATH), "trivy-db-updated-at")


def _load_or_create_secret() -> str:
    """簽章 cookie 用的 SECRET_KEY。

    **多 worker 必須共用同一把鑰匙**，否則 A worker 發的 cookie 到 B worker 驗不過；
    也必須跨重啟穩定，否則一重啟所有人被登出。故：env 優先，其次持久化到檔案。

    落地要同時滿足兩個條件，缺一不可：

      1. **原子**——讀到的必定是完整內容。所以先寫暫存檔再讓它就位，不可直接 O_EXCL
         建檔後才寫入：另一個 worker 會在「已建檔、尚未寫入」的瞬間讀到空字串，然後用
         空金鑰啟動（review M1）。
      2. **互斥**——只有一個 worker 的金鑰會成為那一把，輸的改讀既有檔。

    ⚠ 這裡曾經只做到第 1 點：`os.O_EXCL` 加在 `<path>.<pid>.tmp` 上（帶 pid 的名字本來
      就不可能撞），接著無條件 `os.replace()` 覆蓋目的檔——**沒有任何一方會「輸」**。
      註解白紙黑字寫著「輸的那方改讀既有檔」，程式裡卻沒有那條分支。四個行程同時起來時
      各自寫掉對方的金鑰、各自回傳自己寫的那一把（實測 40/40 輪分岔，2026-07-26），
      症狀是 cookie 在 worker 之間擲骰子：這次請求登入著、下次就被踢回登入頁。

      改用 `os.link()` 讓「就位」這一步本身帶互斥：目的檔已存在就 FileExistsError，
      那一方回頭去讀既有的（修正版同一組壓力測試 0/40 分岔）。
      硬連結在同一個目錄內、同一個檔案系統上，POSIX 保證它是原子的。
    """
    env = os.environ.get("CLAUDE_PTY_SECRET_KEY")
    if env:
        return env
    path = os.path.join(_SELF_HOME, ".claude-pty", "secret.key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for _ in range(50):
        try:
            with open(path) as f:
                key = f.read().strip()
            if key:
                return key
        except FileNotFoundError:
            pass
        import secrets
        tmp = f"{path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(48))
                f.flush()
                os.fsync(f.fileno())
            with suppress(FileExistsError):
                os.link(tmp, path)   # 原子且互斥；已經有人放好了就走上面的讀取分支
        finally:
            os.unlink(tmp)           # link 成功與否都要收，硬連結已經讓 path 指向同一顆 inode
    raise RuntimeError(f"無法建立或讀取 SECRET_KEY：{path}")


SECRET_KEY = _load_or_create_secret()

# 登入 cookie 的安全屬性（ADR 0005/0008）。SECURE 預設關閉以便 http://localhost 開發；
# 對外經 nginx 上 TLS 時應設 CLAUDE_PTY_COOKIE_SECURE=1。
COOKIE_SECURE = os.environ.get("CLAUDE_PTY_COOKIE_SECURE", "0") == "1"

# 是否位於 nginx 之後。決定管理畫面「開啟終端」要用單一入口路徑（/session/<id>/）
# 還是 loopback 直連位址（開發時無 nginx）。部署經 nginx 時設 CLAUDE_PTY_BEHIND_PROXY=1。
BEHIND_PROXY = os.environ.get("CLAUDE_PTY_BEHIND_PROXY", "0") == "1"
SESSION_LIFETIME_DAYS = int(os.environ.get("CLAUDE_PTY_SESSION_DAYS", "7"))

# Flask 控制平面只綁 loopback（ADR 0005：對外一律經 nginx）。
CONTROL_HOST = os.environ.get("CLAUDE_PTY_CONTROL_HOST", "127.0.0.1")
CONTROL_PORT = int(os.environ.get("CLAUDE_PTY_CONTROL_PORT", "8000"))
