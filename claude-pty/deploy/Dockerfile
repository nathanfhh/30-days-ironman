# 控制平面 image（ADR 0009）：Flask + reconciler + ttyd，經 host docker socket 操作 daemon。
#
# 需要 docker CLI 是因為 ttyd 的子命令就是 `docker attach <container>`（ADR 0002）——
# 它在這個容器內執行，透過掛進來的 socket 對 host daemon 講話，attach 到「兄弟」容器。
#
# ══ 全檔規則：**任何一個 apt 階段，第一件事都是把來源換成 https** ══════════════════
#
# Mac 的 Docker Desktop 會掛一層 proxy（`docker info` 看得到 `HTTP Proxy:
# http.docker.internal:3128`），而容器內走純 HTTP/80 一律連不出去、https/443 正常。
#
# 症狀特別難認：`apt-get update` 連不到只是**警告、退出碼 0**，真正炸的是下一句
# `apt-get install`——它因為索引是空的而回 exit 100（"Unable to locate package git"）。
# 而且 apt 預設會重試三輪、逾時很長，所以要等**十幾分鐘**才看得到那個錯
# （2026-07-27 實測：第一階段卡到 724 秒才報 exit 100）。
#
# Linux 沒有那層 proxy，http 本來就通——但 https 也通，所以這條規則對兩邊都成立，
# 不需要分支判斷。deb.debian.org 支援 https。
#
# ⚠ 這個規則**寫在這裡一次**。之前只有第二階段有，第一階段（Rust builder）是後來加的、
#   沒跟上，於是整個 build 在 Mac 上根本過不去。新增任何一個 FROM 都要一起套。

# --- 第一階段：把 Rust 版 ttyd 編出來（image 內同時放兩顆，由 env 選）------------------
#
# 為什麼要多這一階段而不是直接抓 release binary：Rust 版目前只存在於分支上，沒有 release
# 資產可下載，也就沒有「對得起 C 版那條紀律」的 sha256 可釘。**改釘 commit**——branch 名
# 是會動的，用它等於每次 build 都可能拿到不同的東西，而這顆 binary 代理的是每一位使用者的
# 互動 shell。要換版就換這個 SHA（`git rev-parse <branch>` 取得）。
#
# ⚠ builder 用 **bookworm**（glibc 2.36）而最終 image 是 trixie（2.41）：glibc 只保證
#   「舊的編、新的跑」，反過來會在啟動時報 GLIBC_2.4x not found。不要為了版本一致把
#   builder 換成更新的 suite——那個方向沒有保障。
# ⚠ 這兩個宣告在**第一個 FROM 之前**＝全域 ARG，用到的 stage 各自以「裸 ARG」繼承。
#   不這樣做的話預設值要在兩個 stage 各寫一份，而那兩份遲早會分岔（改了 builder 卻忘了
#   改最後那個 ENV，頁尾就會顯示一個沒有在跑的 commit）。
ARG TTYD_RUST_REPO=https://github.com/nathanfhh/ttyd.git
ARG TTYD_RUST_REF=a6275568a2c4f4c47aaf3d3942fb8dde3132fc6e

FROM rust:1.86-slim-bookworm AS ttyd-rust
ARG TTYD_RUST_REPO
ARG TTYD_RUST_REF
# 見檔頭的「全檔規則」。這一階段**不需要**第二階段那段 Verify-Peer=false 的 bootstrap：
# rust:slim 為了讓 cargo 抓 crates.io，image 裡本來就有 ca-certificates（實測
# /etc/ssl/certs/ca-certificates.crt 存在），所以直接走正常驗證的 https 就好。
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# 先 depth=1 拉分支再 checkout 指定 commit：這樣既省頻寬，又不接受「分支現在指到哪」。
# 抓不到那個 commit（被 force-push 蓋掉）就讓 build 直接失敗——那正是釘 SHA 要的行為。
# ⚠ 用 WORKDIR 而不是 `RUN cd`：後者是 Trivy 的 DS013，而且理由不只是風格——`cd` 的
#   作用範圍只在那一行 RUN 之內，下一個 RUN 又回到根目錄，很容易寫出「看起來接續、
#   其實沒有」的指令。
WORKDIR /src
RUN git init . \
    && git remote add origin "${TTYD_RUST_REPO}" \
    && git fetch --depth 1 origin "${TTYD_RUST_REF}" \
    && git checkout FETCH_HEAD
# --locked：用 repo 裡的 Cargo.lock，不讓 build 期自己解析出別的版本組合。
# 前端不用另外 build——build.rs 直接讀 C 版就有的 src/html.h（單一來源）。
WORKDIR /src/rust
RUN cargo build --release --locked && /src/rust/target/release/ttyd --version
# ⚠ 真 binary 的能力斷言（發表阻擋項 #17 的兌現）：控制平面靠 --title 把命令列擋在瀏覽器
#   之外、靠 --auth-url 掛第二層授權。這三支旗標若哪天 fork 改掉，**build 當場失敗**，
#   而不是等到執行時靜默少一層。這是「用真 binary 驗旗標真的被接受」，不是讀 --version。
RUN for flag in --title --auth-url --auth-cache-ttl; do         /src/rust/target/release/ttyd --help 2>&1 | grep -q -- "$flag"         || { echo "FATAL: Rust ttyd 缺旗標 $flag（fork 版本不對？）" >&2; exit 1; };     done

# --- 第二階段：把 Vue 版前端 build 出來（階段 4）----------------------------------
#
# ⚠ 產物**不進版控**（`claude-pty/.gitignore` 只加了 `server/static/dist/` 一行），所以它
#   一定要在 build 期產生。少了這一階段，三個頁面會全部 404——
#   而那看起來像路由壞掉，不像「你忘了 build 前端」。
# ⚠ 用 `npm ci` 不是 `npm install`：ci 只照 lockfile 裝，裝不出來就直接失敗。install 會
#   為了「解得開」而自己挑另一組版本，於是 image 裡跑的與 lockfile 講的可以是兩組——那正是
#   下面 python 那段 `uv lock --check → --require-hashes` 在防的同一件事，只是換個生態系。
# ⚠ 這一階段**不需要**檔頭那條 https 規則：node:24-slim 不裝任何 apt 套件。
FROM node:24-slim AS frontend
WORKDIR /build/frontend
# 先只複製兩份清單：原始碼改了不必重裝相依（npm ci 是這整包裡第二貴的一步）。
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# ⚠ `app.css` 是**原檔**、住在 Vite root 的外面（`src/main.ts` 直接 import 它）。
#   階段 4 的前提是 CSS 一字不改，所以這裡帶原檔進來，不複製成第二份真相。
COPY server/static/css/app.css /build/server/static/css/app.css
# outDir 是 `../server/static/dist`（見 frontend/vite.config.ts），所以產物落在 /build/server/。
# ⚠ 明確驗一次產物在不在：vite 失敗時的退出碼是可靠的，但「build 成功卻沒有 index.html」
#   （改錯 outDir）是靜悄悄的，而症狀要到部署之後才看得見。
RUN npm run build && test -f /build/server/static/dist/index.html

# --- 第三階段：nginx（**只有 `--target nginx` 才會 build**）------------------------
#
# 為什麼 dist 用 COPY 進 image 而不是 bind mount：mount 的話，host 上沒 build 過的目錄會被
# docker 建成一個**空目錄**頂替，症狀是整站 404 而不是「你忘了 build」；而且正式站的產物
# 就該跟著 image 走版本，不該取決於部署那台機器當下的工作區。
# nginx.conf 仍然是 mount 的（改設定不必重 build，見 docker-compose.yml）。
FROM nginx:alpine AS nginx
COPY --from=frontend /build/server/static/dist /usr/share/nginx/html

FROM python:3.13-slim

# docker CLI（只要 client，不要 daemon——daemon 是 host 的）+ ttyd
# ttyd 從 GitHub release 取靜態 binary；arch 動態選（Apple Silicon / x86 皆可）。
ARG TTYD_VERSION=1.7.7
# ⚠ **binary 一定要驗過才給執行。** 這一段原本是 `curl → chmod +x → ttyd --version`，
#   而 `--version` 只證明「它跑得起來」，不證明「它是我們審過的那一顆」。同一份 Dockerfile
#   對 docker-ce-cli 做得很嚴（釘 GPG keyring，連 Verify-Peer=false 那段都特地說明
#   「套件完整性仍由 InRelease 的簽章保護」），ttyd 卻是一顆沒有任何佐證的下載檔——而它
#   正是代理每一位使用者互動 shell 的那個程序，又跑在掛著 docker.sock 的控制平面裡。
#   固定版本與 HTTPS 擋得住意外升版與中間人，擋不住上游資產被換掉。
#
#   雜湊**寫進版本控制**，不是 build 時從同一個位置抓 SHA256SUMS 再比對（那等於讓攻擊者
#   同時提供題目與答案）。取得方式：2026-07-26 由 release 的 SHA256SUMS 取得，並與實際
#   下載的檔案、以及當時線上 image 內已在執行的那顆 binary 三方對照一致。
#   升版時要一起換這兩行——換了版本卻沒換雜湊，build 會直接失敗，這正是要的行為。
ARG TTYD_SHA256_AMD64=8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55
ARG TTYD_SHA256_ARM64=b38acadd89d1d396a0f5649aa52c539edbad07f4bc7348b27b4f4b7219dd4165
# apt 走 HTTPS——理由見檔頭的「全檔規則」。
# 這一階段（python:3.13-slim）與 builder 的差別：它**沒有** ca-certificates，所以要先以
# Verify-Peer=false 把它 bootstrap 起來（套件完整性仍由 InRelease 的 GPG 簽章保護，TLS 只是
# 傳輸層），隨即恢復正常驗證。此坑同見 dev-container/Dockerfile。
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates \
    && apt-get update \
    # ⚠ base image 的套件會隨時間長出 CVE，而 python:3.13-slim 的重建節奏不歸我們管。
    #   Trivy 掃 image 時抓到 util-linux 那一組（CVE-2026-53612/13/14/15 等 54 筆
    #   MEDIUM 以上）在 base 裡是 2.41-5，而 Debian 已經出了 2.41.5-0+deb13u1。
    #   在這裡做一次安全更新，掃到的就是實際出貨的那一顆。
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
        https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && case "$(dpkg --print-architecture)" in \
         amd64) TTYD_ARCH=x86_64;  TTYD_SHA="${TTYD_SHA256_AMD64}" ;; \
         arm64) TTYD_ARCH=aarch64; TTYD_SHA="${TTYD_SHA256_ARM64}" ;; \
         *) echo "unsupported arch" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.${TTYD_ARCH}" \
        -o /usr/local/bin/ttyd \
    && echo "${TTYD_SHA}  /usr/local/bin/ttyd" | sha256sum --check --strict - \
    && chmod +x /usr/local/bin/ttyd \
    && ttyd --version \
    # ⚠ curl 也要 purge：它只在 build 期用來抓 keyring 與 ttyd，而這顆容器掛著
    #   docker.sock（審查 F-030）。執行期沒有任何東西呼叫它——redeploy.sh 的健康檢查
    #   跑在 host 上，不在容器內。
    && apt-get purge -y gnupg curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Rust 版擺在旁邊，**檔名不同、預設不啟用**。選哪一顆是使用者的偏好、存在 DB
# （`users.ttyd_bin`，管理畫面「設定」可切；白名單見 config.TTYD_BINS）。
# 兩顆同時在 image 裡才切得動：切換即時生效，不必重新 build、也不必重啟。
COPY --from=ttyd-rust /src/rust/target/release/ttyd /usr/local/bin/ttyd-rust
RUN ttyd-rust --version
# ⚠ 兩 binary 契約在 build 時就釘死（真 binary 實測 2026-08-07）：
#   · Rust 版有 --title / --auth-url（上一階段已斷言，這裡再確認搬進來的是對的那顆）
#   · C 版**沒有**這些旗標，而且它對沒有的旗標是**靜默忽略照跑**（不是拒起）——所以
#     絕不能靠「塞了會壞」來防呆，必須靠 server 端 strategy 不把旗標交給它
#     （views._TTYD_EXTRAS）。這裡斷言 C 版 help 確實沒有，作為那個 strategy 的地基。
RUN ttyd-rust --help 2>&1 | grep -q -- --title     || { echo "FATAL: 搬進來的 ttyd-rust 沒有 --title" >&2; exit 1; }
RUN if ttyd --help 2>&1 | grep -q -- --title; then         echo "FATAL: C 版 ttyd 竟然有 --title，兩 binary 差異的前提不成立" >&2; exit 1;     fi

WORKDIR /app
# ⚠ 相依**只有一份清單**，就是 pyproject.toml。這裡曾經另外寫死一串 pip install，
#   結果兩份分岔：psutil 與 gunicorn 只活在 image，pyproject 沒有。兩個後果——
#     1. 本機 `uv run` 永遠沒有 psutil，於是 _is_our_ttyd 一律走「無從佐證」的降級路徑，
#        真正的把關邏輯在本機測不到。
#     2. CVE 掃描掃的是由 pyproject 產生的 uv.lock，而實際跑的是另一串；gunicorn 是
#        對外聽 port 的東西，卻從來沒被任何一次掃描涵蓋過。
#   程式碼要在安裝之前 COPY 進來——要建的就是這個套件本身。
#
# ⚠ 而「同一份清單」還不夠，要**同一組版本**。`pip install ".[server]"` 是每次 build
#   重新解析一次，於是 Trivy 掃的 lockfile 與 image 裡實際裝的可以是兩組不同的版本
#   ——掃出來的綠燈證明不了出貨的那顆。所以改成由 uv.lock 釘死。
COPY pyproject.toml uv.lock ./
COPY server/ ./server/
# ⚠ 三步走：
#   1. `uv lock --check` 是那道閘：**lockfile 與 pyproject 對不上就直接失敗**。
#      不能只靠 `uv export --frozen`——`--frozen` 的意思是「不要更新 lockfile」，
#      它會安靜地照舊的鎖檔輸出，新加的相依連提都不提（實測 exit 0）。少了這一行，
#      改了 pyproject 卻忘記 `uv lock` 的人會 build 出一顆缺東西的 image。
#   2. `uv export` 把 uv.lock 攤成帶雜湊的 requirements。
#   3. `pip install --require-hashes` 只裝那些版本與雜湊。少了 --require-hashes 的話
#      pip 仍可能為了相容自己再解一次，那就又分岔了。
#   最後 `--no-deps .` 裝這個套件本身（相依已經裝齊，不要讓它再解一次）。
RUN pip install --no-cache-dir uv==0.7.3 \
    && uv lock --check \
    && uv export --frozen --no-dev --extra server --group build --no-emit-project \
         -o /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    # ⚠ `--no-build-isolation`：沒有它的話 pip 會另開一個隔離環境、**從 PyPI 重新抓一份
    #   沒釘版本也沒有雜湊的 setuptools** 來建這個套件，於是「由 lockfile 釘死」對相依
    #   成立、對 build backend 不成立。`--group build` 把釘死的 setuptools 帶進上一步，
    #   這裡就直接用它。
    && pip install --no-cache-dir --no-build-isolation --no-deps . \
    # ⚠ build backend 裝完就移掉。它只有建這個套件那一步要用，留在 runtime 裡不會被
    #   任何東西 import，卻會被 Trivy 算進去——setuptools 自己還 vendor 了 wheel 與
    #   jaraco.context，實測多出三筆 MEDIUM 以上。`--group build` 換來的雜湊驗證是
    #   build 期的事，不必用 runtime 的攻擊面去付。
    && pip uninstall -y uv setuptools wheel \
    && rm -f /tmp/requirements.txt \
    && pip freeze > /app/installed-requirements.txt \
    && python -c "import gunicorn, psutil, flask, sqlalchemy, argon2"

# SPA 的產物。**dev 與 e2e 走這條**（in-thread Flask serve dist，見 server/web.py 的
# `_spa_shell`／`spa_asset`）；正式站是 nginx 直出上面那個 stage 的同一份產物。
# ⚠ 放在 pip install **之後**：這個套件沒有宣告 package-data（見 pyproject 的
#   `[tool.setuptools.packages.find]`），所以執行期讀的一律是 `/app/server`，不是
#   site-packages 那份。放在後面只是為了不讓「改前端」把 pip 那一層的快取一起弄掉。
# ⚠ 這一份是 dev 與 e2e 那條路要的：正式部署由 nginx 直出 dist（見 deploy/nginx.conf），
#   而 Flask 自己 serve 頁面時（沒有 nginx）也要拿得到同一份產物。
COPY --from=frontend /build/server/static/dist /app/server/static/dist

# registry / secret key 的落腳處；compose 以 volume 掛入使其跨重啟持久。
ENV CLAUDE_PTY_CONTROL_HOST=0.0.0.0

# 以非 root 執行。掛著 docker socket 的容器**本來就等於 host root**（ADR 0009 已明確
# 接受），所以這不是安全邊界的質變，而是縱深防禦：程序不是 uid 0，就動不了 root 擁有
# 的檔案、綁不了 <1024 的 port、拿不到預設 capability。
#
# 兩件事必須配合，否則會壞（都實測過）：
#   1. **docker socket 的群組**：socket 是 `srw-rw---- root:root`，非 root 要讀它就得屬於
#      該 gid。compose 用 group_add 補上（macOS Docker Desktop 是 0；Linux 多半是 docker
#      群組的 gid，見 compose 的 DOCKER_GID）。
#   2. **/data 的擁有者**：compose 掛的是 **bind mount**（`./data:/data`）不是 named
#      volume，所以下面那道 chown 只管 image 內的空目錄——**掛上來之後看到的是 host 上
#      `deploy/data/` 的擁有者**，image 這邊改不動它。那個目錄要屬於 APP_UID，否則 SQLite
#      一個字都寫不進去。`deploy/redeploy.sh` 每次會先 mkdir 並檢查可寫，不可寫就停下來
#      講原因（最常見的是先前被 dockerd 建成 root），所以正常不需要手動處理。
# ⚠ uid 要與 host 上那些掛進來的目錄的擁有者一致，否則寫不進去：bind mount 的
#   deploy/data（SQLite）與 per-user 空間（0700）都吃這個。compose 從 .env 的 APP_UID
#   帶進來（Linux 填 `id -u`），見 ADR 0009。
ARG APP_UID=1000
RUN useradd -m -u ${APP_UID} app \
    && mkdir -p /data \
    && chown -R app:app /data /app
USER app

# --- 這一版是哪個 commit（頁尾要顯示）------------------------------------------------
#
# ⚠ **必須放在整份 Dockerfile 的最後面。** ARG 的值一變，它以下的每一層都會失效——放在
#   前面的話「換一個 commit」等於整包重編（Rust ttyd 那一階段五分鐘起跳）。放在這裡的話
#   失效的只有這一層 ENV，幾乎不花時間。
#
# ⚠ 為什麼要用 build arg 而不是執行期去問：程式是 `COPY` 進來的，`.git` 根本不在 build
#   context 裡（context 是 claude-pty/，而 .git 在上一層），容器內也沒有 git 執行檔。
#   執行期問不到自己是哪一版，只能在 build 時烘。
#
# ⚠ 預設留空**不要填一個假值**。空的會讓頁尾顯示「commit 未知」並在 tooltip 講明原因；
#   填一個看起來合理的預設只會讓人以為自己知道線上在跑什麼。值由 deploy/redeploy.sh 算
#   （含工作區髒掉時的 `-dirty` 標記），經 compose 的 build args 傳進來。
ARG GIT_SHA=
# ⚠ 這裡要的是**建置時間**不是 commit 時間。要回答的是「線上這包是什麼時候做出來的」——
#   同一個 commit 可以在任何時候被重新建置，commit 時間答不了那個問題。
ARG BUILT_AT=
# ⚠ 裸 ARG＝繼承全域那份。ARG **不跨 stage**：少了這一行，下面的 ENV 會是空字串，
#   而頁尾就少掉 Rust 版那顆的 commit——空得無聲無息。
ARG TTYD_RUST_REF
ENV CLAUDE_PTY_GIT_SHA=${GIT_SHA} \
    CLAUDE_PTY_BUILT_AT=${BUILT_AT} \
    CLAUDE_PTY_TTYD_RUST_REF=${TTYD_RUST_REF}

EXPOSE 8000
# 正式服務用 gunicorn，不用 Flask 內建開發伺服器（它自己每次啟動都會警告）。
#   --workers 1  目前量級一個就夠；要加時直接調——registry/配額/port 全由 DB 仲裁
#                （ADR 0008），多 worker 本來就成立，不需改任何程式。
#   --threads 8  單 worker 內以執行緒處理併發請求（docker API 等待多為 I/O bound）。
#   --timeout    建立 session 會等 container 起來（restricted 約 40 秒），預設 30 秒會被砍。
CMD ["gunicorn", "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "8", "--timeout", "180", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "server.app:app"]
