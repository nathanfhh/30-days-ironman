FROM ubuntu:24.04

# 預設 shell 換成 bash 並開 pipefail。這一行是為了下面六處 `curl ... | sh`。
#
# ⚠ 沒有 pipefail 時，`curl X | sh` 的結束碼取自 **sh**，不是 curl。curl 失敗會輸出
#   空字串，sh 讀到空輸入正常結束 → 整條 pipeline 回 0 → RUN 成功 → **layer 被 cache**。
#   實際踩過：一次暫時性 DNS 失敗（`curl: (6) Could not resolve host: astral.sh`），
#   那層照樣 DONE，uv 根本沒裝進去，直到三層之後才以 `uv: not found` 爆出來。
#
#   更糟的排列是失敗發生在 **chain 的最後一個**（rtk）——後面沒有任何步驟用到它，
#   image 會成功 build、成功出貨，少一個工具，要到 runtime 才發現。
#
#   而且壞掉的 layer 會進 cache：之後每次 build 都撈同一顆壞層，重跑救不了，
#   必須 --no-cache 才能脫身。
#
# ⚠ 這行會讓其後**所有** RUN 改用 bash（原本是 /bin/sh → dash）。bash 是超集，
#   既有指令不受影響；但之後新增 RUN 時要知道自己拿到的是 bash。
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# 容器內 `nathan` 的 uid。**Linux 上這個值必須等於部署者的 `id -u`**，而 claude-pty 的
# APP_UID 也要等於同一個數字——三者對不上就會在很後面炸開（每一場撞 onboarding 對話、
# 終端停在登入提示、restricted 卡滿逾時），而且沒有一個症狀看起來像 uid 問題。
# 完整推導見 claude-pty/docs/adr（uid 對齊那份）。
#
# ⚠ 原本這裡是 `useradd … nathan`（不指定 uid），拿到什麼號碼**取決於 base image 還剩
#   什麼**——ubuntu:24.04 自己佔了 1000（使用者名 `ubuntu`），所以 nathan 實測是 1001。
#   那是一個沒有人決定過、卻被四個地方依賴的數字。釘成 build-arg 不是新增限制，是把
#   一個既有卻無法指定的限制變成可指定的。
#
# ⚠ 預設 1001 ＝ **維持現狀的位元級相容**：不帶這個 arg 重 build，出來的 image 與過去
#   完全一樣。要換號碼的人才需要動它。
#
# ⚠ NCR_UID=1000 時會撞到 base image 自帶的 `ubuntu`（實測 `useradd: UID 1000 is not
#   unique`, exit 4）。下面那行條件式會先把它移掉——image 裡屬於 1000 的只有
#   /home/ubuntu 與三個 skeleton dotfile（實測 4 個檔），userdel -r 不留孤兒。
ARG NCR_UID=1001

# apt 走 HTTPS：部分 Docker Desktop 代理環境下，容器內純 HTTP/80 會連線失敗。
# base image 沒有 ca-certificates，先以暫時關閉 TLS 驗證的方式 bootstrap
#（套件完整性仍由 InRelease 的 GPG 簽章保護），裝完隨即恢復正常驗證。
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources && \
    apt-get -o Acquire::https::Verify-Peer=false update && \
    DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::https::Verify-Peer=false install -y ca-certificates && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        curl git jq tzdata shellcheck vim openssh-client \
        sudo iptables ipset iproute2 dnsutils && \
    ln -sf /usr/share/zoneinfo/Asia/Taipei /etc/localtime && \
    echo "Asia/Taipei" > /etc/timezone && \
    rm -rf /var/lib/apt/lists/* && \
    if [ "$(id -u ubuntu 2>/dev/null)" = "${NCR_UID}" ]; then userdel -r ubuntu; fi && \
    useradd -m -s /bin/bash -d /home/nathan -u "${NCR_UID}" nathan

# sudoers：nathan 只能跑 init-firewall.sh，而且**只能無參數地跑**。
#
# ⚠ 那個 `""` 不是裝飾。sudoers 的語義是「命令後面沒有列參數 ＝ 任何參數都允許」，
#   所以少寫它就等於這樣：
#       nathan ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh
#   而只要腳本會把參數用進白名單，容器裡的 agent 就能自己擴大自己的牢房——
#   更糟的是腳本的自我驗證仍然會通過，畫面照樣印出「防火牆已驗證」。
#   本份的 init-firewall.sh 因此刻意不吃任何位置參數，sudoers 這端也把參數鎖成空，
#   兩道一起關。
#
#   build 時跑 visudo -c 驗語法：sudoers 寫壞不會讓 build 失敗，會等到你 sudo 的那一刻
#   才變成「a password is required」——那時人已經在容器裡了。
#
# 第二條授權是一支唯讀的計數查詢。它只跑 iptables -L -v -n、不吃參數，
#   所以拿不到改規則的能力；收工時用它把「這一場擋了多少、放行了多少」留下來。
RUN printf '%s\n' \
      'nathan ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh ""' \
      'nathan ALL=(root) NOPASSWD: /usr/local/bin/firewall-counters.sh ""' \
      'nathan ALL=(root) NOPASSWD: /usr/local/bin/trust-mitm-ca.sh ""' \
      > /etc/sudoers.d/nathan-firewall && \
    chmod 0440 /etc/sudoers.d/nathan-firewall && \
    visudo -c -f /etc/sudoers.d/nathan-firewall

# UTF-8 locale：POSIX locale 下 bash readline 會丟棄多位元組輸入（中文打不出來），
# opengrep 的 frozen Python 也會用 ascii codec 讀規則檔而炸掉。
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

USER nathan

# 四個 curl 安裝：Claude Code、uv（Python 工具鏈）、nvm（Node 版本管理）、rtk（token 節省 proxy）
RUN curl -fsSL https://claude.ai/install.sh | bash && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.6/install.sh | bash && \
    curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh

ENV NVM_DIR="/home/nathan/.nvm" \
    PATH="/home/nathan/.venv/bin:/home/nathan/.local/bin:${PATH}"

# node 也 symlink 進 ~/.local/bin：nvm 只在互動 shell 載入，非互動環境（如 AI 的指令執行）
# 拿不到 node，而 oxlint 的 CLI 進入點是 JS（#!/usr/bin/env node），沒有 node 就跑不動。
RUN . "$NVM_DIR/nvm.sh" && nvm install 24 && \
    mkdir -p /home/nathan/.local/bin && \
    ln -sf "$(which node)" /home/nathan/.local/bin/node

# oxlint：前端靜態分析（A1 軌道）。pin 版本維持審查報告可重現性。
# 全域安裝，review clone 不需 npm install 即可用。
RUN . "$NVM_DIR/nvm.sh" && npm install -g oxlint@1.77.0 && \
    ln -sf "$(. "$NVM_DIR/nvm.sh" && which oxlint)" /home/nathan/.local/bin/oxlint && \
    oxlint --version

# Python 3.13 via uv，裝進 venv（uv 管理的直譯器拒絕直接裝套件）。
# venv 的 bin 排在 PATH 最前，python3 就會解析到帶 site-packages 的直譯器。
RUN uv python install 3.13 && \
    uv venv --python 3.13 /home/nathan/.venv

# pydantic：結構化 review 報告 schema 的驗證層。pin 版本，驗證行為不隨 build 時間飄移。
RUN uv pip install --python /home/nathan/.venv/bin/python pydantic==2.13.4 && \
    python3 -c "import pydantic; print('pydantic', pydantic.__version__)"

# ruff：Python linter（A1 軌道）。pin 版本——不 pin 的話，同一份 code 在不同時間
# build 的 image 底下會給出不同的診斷，審查報告就變得不可重現。
RUN uv tool install ruff==0.16.1 && ruff --version

# ty：Python type checker（A1 軌道）。仍在 0.0.x、規則月月變，必須 pin。
RUN uv tool install ty==0.0.66 && ty --version

# Trivy：供應鏈 / CVE 掃描（A2 軌道，兼任 secret 掃描）。
RUN curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
    | sh -s -- -b /home/nathan/.local/bin && \
    trivy --version

# Opengrep：SAST 掃描引擎（A4 軌道）。Semgrep CE 的社群 fork，規則格式相容。pin 版本。
# arch 由 uname -m 動態選，同時支援 x86_64 與 Apple Silicon。
RUN OPENGREP_VERSION=v1.26.0 && \
    case "$(uname -m)" in \
        x86_64)  OG_ARCH=x86 ;; \
        aarch64) OG_ARCH=aarch64 ;; \
        *) echo "unsupported arch for opengrep: $(uname -m)" >&2; exit 1 ;; \
    esac && \
    curl -sfL "https://github.com/opengrep/opengrep/releases/download/${OPENGREP_VERSION}/opengrep_manylinux_${OG_ARCH}" \
    -o /home/nathan/.local/bin/opengrep && \
    chmod +x /home/nathan/.local/bin/opengrep && \
    opengrep --version

# mitmproxy：錄本場 Claude Code ↔ api.anthropic.com 的 L7 流量（entrypoint 選單開關，預設不錄）。
# 走 explicit proxy（HTTPS_PROXY 環境變數）而不是 transparent proxy，所以不需要 iptables
# REDIRECT 那一套：proxy 綁 loopback（firewall 無條件放行 lo），上游是白名單裡的
# api.anthropic.com——跟限制模式天生相容，零 firewall 改動。
# pin 版本：.mitm 的檔案格式會隨大版本破壞相容，釘住才能保證舊的 capture 讀得回、
# addon 的行為不飄。查最新版：https://pypi.org/project/mitmproxy/
RUN uv tool install mitmproxy==12.2.3 && mitmdump --version

# CodeGraph：程式碼知識圖譜 / 導航輔助（blast radius、caller 矩陣）。
# 不 pin：輸出不進報告 finding，無可重現性需求。
RUN curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh && \
    codegraph --version

ENV TZ=Asia/Taipei \
    FORCE_COLOR=1 \
    TERM=xterm-256color \
    COLORTERM=truecolor \
    SSH_AUTH_SOCK=/ssh/ssh_sock

# run wrapper 會把 host 的 ssh-agent socket 掛到 /ssh/ssh_sock；上面那個 ENV 讓容器裡的
# git／ssh 不用任何設定就找得到它。
# ⚠ 沒掛載時這個變數仍然有值，`ssh-add -l` 會回「Error connecting to agent: No such
#   file or directory」。那是「這條路沒接」，不是「設定壞了」——兩者的外顯訊息一樣。
# known_hosts 有兩層來源，runtime 優先：
#
#   1.（預設）run wrapper 把 host 的 ~/.ssh/known_hosts 唯讀掛到同一個路徑。
#      信任決策留在 host——使用者本來就連過那台 GitLab、TOFU 早就做完了。
#   2.（選配）build 時 ssh-keyscan 烘進來，給「沒有 host known_hosts」的環境（CI、
#      全新機器）當備援：
#          docker build --build-arg GITLAB_SSH_HOST=gitlab.example.com .
#      掛載會蓋過這一份，所以兩者同時存在時以 host 的為準。
#
# ⚠ 為什麼不預設烘：ssh-keyscan 是 TOFU（第一次連就信任），而 build time 正是你
#   最沒有能力驗證那把 key 的時間點。被中間人就把假的 host key 烘進 image，之後
#   每一次連線都信任它，外表還看起來像「已經設定好了」。內網 GitLab 更麻煩——
#   build 機器不在內網時 keyscan 根本抓不到。所以這裡失敗只警告、不擋 build。
#
# ⚠ 真正該避免的反射動作是 StrictHostKeyChecking=no。那不是設定 known_hosts，
#   那是把中間人偵測整個關掉。
ARG GITLAB_SSH_HOST=""
RUN mkdir -p /home/nathan/.ssh && chmod 700 /home/nathan/.ssh && \
    if [ -n "$GITLAB_SSH_HOST" ]; then \
        if ssh-keyscan -t rsa,ed25519,ecdsa "$GITLAB_SSH_HOST" \
             > /home/nathan/.ssh/known_hosts 2>/dev/null \
           && [ -s /home/nathan/.ssh/known_hosts ]; then \
            chmod 600 /home/nathan/.ssh/known_hosts; \
            echo "known_hosts: 已收錄 $GITLAB_SSH_HOST ✓"; \
        else \
            rm -f /home/nathan/.ssh/known_hosts; \
            echo "⚠️  build 時連不到 ${GITLAB_SSH_HOST}，未烘入 known_hosts；改由 run wrapper 掛載 host 的那份。"; \
        fi; \
    fi

WORKDIR /home/nathan/code-review

# entrypoint 放最後：改 script 只會 invalidate 這幾層，不會 bust 上面的工具安裝 cache。
# 最後一層寫入 build time 給 entrypoint 啟動時顯示。
USER root
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY init-firewall.sh /usr/local/bin/init-firewall.sh
COPY firewall-counters.sh /usr/local/bin/firewall-counters.sh
COPY trust-mitm-ca.sh /usr/local/bin/trust-mitm-ca.sh
# GITLAB_SSH_HOST 同時餵兩個地方：上面的 known_hosts，以及這裡給防火牆讀的檔案。
#
# ⚠ 為什麼是檔案而不是環境變數：容器裡的 agent 以 nathan 執行，env 是它寫得到的東西；
#   政策的來源如果是 env，等於讓被關的人自己挑監獄。這個檔案由 root 在 build 時寫入、
#   0444，agent 改不動也蓋不掉（bind mount 覆蓋要 CAP_SYS_ADMIN，容器只有 NET_ADMIN）。
#   沒帶這個 build-arg 就是空檔案 → 防火牆一條 SSH outbound 都不開。
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/init-firewall.sh \
        /usr/local/bin/firewall-counters.sh /usr/local/bin/trust-mitm-ca.sh && \
    mkdir -p /etc/ncr && \
    printf '%s' "$GITLAB_SSH_HOST" > /etc/ncr/gitlab-ssh-host && \
    chmod 0444 /etc/ncr/gitlab-ssh-host && \
    date -Iseconds > /etc/image-build-time

# 把 build 時決定的 uid 烙進 image，讓**外面問得到**。
# ⚠ 這不是裝飾：claude-pty 的 preflight 靠它驗「設定值有沒有跟現實一致」。少了它，
#   那道檢查只能比兩個都由使用者設定的旋鈕，一起轉錯就完全靜音（那正是它要防的事）。
# ENV 與 LABEL 兩個都寫：ENV 給容器內的腳本讀，LABEL 給 docker inspect 讀，
# 兩邊都拿得到才不會因為查法不同而失效。
ARG NCR_UID
ENV NCR_UID=${NCR_UID}
LABEL ncr.uid=${NCR_UID}

USER nathan

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["claude", "--dangerously-skip-permissions"]
