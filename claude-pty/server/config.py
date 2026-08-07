"""Server 端寫死的常數（ADR 0004：image / workdir / command / 資源限制全由後端固定，
前端不得指定）。前端只選得了 profile 的幾個面向，值一律走白名單。"""

import os
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
# 縮短不能解決連坐（那要靠「列表只讀 DB」+ 逐顆隔離，ADR 0013），但它決定了單一次
# 意外的代價：15 秒對正常操作綽綽有餘（建立容器不走這條，它有自己的長 timeout）。
DOCKER_TIMEOUT = float(os.environ.get("CLAUDE_PTY_DOCKER_TIMEOUT", "15"))

# session 容器要加入的 docker network（restricted 需要它才有直連網段可放行；telemetry 需要
# 它才到得了 jaeger）。
# ⚠ 用 `.strip() or` 而不是 `get(k, default)`：compose 傳的是 `${VAR:-<預設>}`，若哪天
#   改成 `${VAR:-}` 就會傳空字串進來，get() 會原樣回空字串。
#   這樣寫也讓**預設值只有這一處**，compose 不必再抄一份。
SESSION_NETWORK = os.environ.get("CLAUDE_PTY_NETWORK", "").strip() or "claude-pty-sessions"
# jaeger 也要在上面，否則 telemetry 靜默斷掉（OTLP 是 fail-open，不會有任何錯誤）。
# ⚠ jaeger 定義在 **dev-container/jaeger-compose.yaml**，不在 deploy/docker-compose.yml
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
#   host 的一個共用目錄。ADR 0016 之後那是 per-user 的（space 底下的 user-{id}/mitm，
#   見 user_mounts）——那個目錄裡是**完整的 API 請求本文**，共用它是先前盤點時最容易
#   漏掉的一項。容器內的落點仍是同一個路徑，見下方 MITM_BIND。

# --- SSH agent 轉發：預設關，由部署者明確開啟（ADR 0012）------------------------------
#
# 為什麼不像 CLI 憑證（~/.claude）那樣預設掛：兩者的爆炸半徑不同。
#   - CLI 憑證只能用來呼叫那家 AI 供應商的 API。而且共用它本來就是這個系統的前提——
#     ADR 0007 的對話續命錨點就是那份共用的 ~/.claude，拿掉它整個產品不成立。
#   - SSH agent 可以拿去認證**任何主機**（內網 git、正式機、任何信任那把 key 的地方），
#     而且是以「你」的身分。開了它就等於把那個能力發給每一個能建立 session 的人
#     ——沒有租戶隔離（architecture.md §7），這件事沒有辦法只給一個人。
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

# ⚠ CLI 憑證掛得理所當然，不表示它安全：**任何能開 session 的人都拿得到它們**（同上，
#   沒有租戶隔離）。把帳號開給誰，等於把這些憑證交給誰——這是開帳號時就要做的信任判斷，
#   不是可以事後補救的東西。

# trivy 漏洞 DB 的快取目錄（與 run script 掛同一份，見下方 MOUNTS）。
# HOST 版供掛載（daemon 解讀）、SELF 版供控制平面自己 mkdir（ADR 0009）。
# ⚠ 必須由我們先建立：讓 docker daemon 隱式建立的話，Linux 上會是 root:root，容器內那個
# 使用者（SESSION_UID）寫不進去 → trivy 下載失敗 → restricted 每次卡滿 120 秒逾時，
# 比不掛還糟。
# ⚠ 預設值**與 dev-container 的 run script 同一個目錄**（它也是 `$HOME/.cache/ncr-trivy`）：
#   兩條路徑共用同一份 DB，人先跑過一次容器之後，網頁開的第一場就直接命中快取。
#   指到別的地方不會壞，只是每一場都要重抓約 1GB。
TRIVY_CACHE_HOST = os.environ.get(
    "CLAUDE_PTY_TRIVY_CACHE", os.path.join(HOST_HOME, ".cache", "ncr-trivy"))
TRIVY_CACHE_SELF = os.environ.get(
    "CLAUDE_PTY_TRIVY_CACHE_SELF", os.path.join(_SELF_HOME, ".cache", "ncr-trivy"))

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

# --- per-user 的 agent 狀態空間（ADR 0016）------------------------------------------
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
# 容器一收就沒）。⚠ 目前**沒有任何大小限制**，見 ADR 0016 的「暫不做磁碟配額」。
#
# ⚠ 曾經是 `/data`，2026-07-29 改成這裡，兩個理由：
#   1. `/data` 在**控制平面**那邊已經是 registry（SQLite）的落點——同一個字串在兩個容器裡
#      是兩個完全不同的東西。
#   2. 名字要自己說得出它是什麼。放在 $HOME 底下，與其他掛進來的目錄同一層。
# ⚠ **不要改成掛進 WORKDIR 底下**（`code-review/persistent-data` 之類）。看起來比較好發現，
#   代價是 cwd 從此不是空的：`git clone <url> .` 會直接 `destination path '.' already exists`
#   （在真 image 裡驗過），而錯誤訊息完全指不到原因；cwd 裡的批次刪除也會掃到它。
DATA_BIND = "/home/nathan/persistent-data"

# `.claude.json` 的最小種子（ADR 0016）。全新空間的第一場會連撞三道互動對話，而
# `_is_ready()` 只看「畫面靜止」——對話框畫面一樣靜止，於是初始 prompt 被打進選單裡。
# 最惡劣的是 bypass 那道：預設選項停在「No, exit」，送出的第一個 Enter 就是結束容器。
#
# ⚠ 這是 CLI 的私有格式（實測 2.1.220），升版可能改名，而症狀只出現在「某個使用者的
#   第一場」，極難聯想。image 換版時照 ADR 0016 的方法論煙測一次：**要用 entrypoint 的
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
    # trivy 漏洞 DB 的持久化快取（與 run script 掛同一個 host 目錄，共用同一份）。
    # ⚠ 這不是效能微調，是 restricted profile 的啟動時間關鍵：entrypoint.sh 在套 iptables
    #   之前**必須**等 DB 更新跑完（firewall 一鎖網路就會切斷半截下載）。沒有這個掛載，
    #   每開一個 session 都是全新的空 cache，得重新抓解壓後約 1GB 的 DB——實測整整 36 秒，
    #   而 firewall 本身只花 0.5 秒。掛上之後 `--download-db-only` 會檢查鮮度並直接 no-op。
    TRIVY_CACHE_HOST: {"bind": "/home/nathan/.cache/trivy", "mode": "rw"},
}

# 「這一輪是測試」的標記。用途：打在容器 label 上讓正式 reconciler 跳過測試建的容器，
# 見 TEST_LABEL_DEFAULT_KEY 那段。
TEST_MARK = os.environ.get("CLAUDE_PTY_TEST_MARK")   # 測試設為 "1"，正式部署不設





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
    """某個使用者的 per-user 掛載（ADR 0016）。key 是 **host 路徑**（daemon 解讀）。

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

# `GET /api/sessions/<sid>?wait_ready=<秒>` 的上限。
# ⚠ 這個參數會讓請求**整段阻塞**在 gunicorn 的一條執行緒上（--threads 8），而且每 0.5 秒
#   輪詢一次 docker inspect + logs。上限給到 600 秒的話，八個這種請求就能把整個控制平面
#   佔滿——連 nginx 的 auth_request → /api/auth/view 都排不進去，等於所有人開著的終端
#   一起失效。上限要跟 READY_TIMEOUT（TUI 就緒的實際等待上限）同量級，不是一個隨手取的大數。
WAIT_READY_MAX = float(os.environ.get("CLAUDE_PTY_WAIT_READY_MAX", "180"))
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
