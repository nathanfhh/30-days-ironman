FROM ubuntu:24.04

# apt 走 HTTPS：部分 Docker Desktop 代理環境下，容器內純 HTTP/80 會連線失敗。
# base image 沒有 ca-certificates，先以暫時關閉 TLS 驗證的方式 bootstrap
#（套件完整性仍由 InRelease 的 GPG 簽章保護），裝完隨即恢復正常驗證。
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources && \
    apt-get -o Acquire::https::Verify-Peer=false update && \
    DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::https::Verify-Peer=false install -y ca-certificates && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        curl git jq tzdata shellcheck vim && \
    ln -sf /usr/share/zoneinfo/Asia/Taipei /etc/localtime && \
    echo "Asia/Taipei" > /etc/timezone && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -m -s /bin/bash -d /home/nathan nathan

# UTF-8 locale：POSIX locale 下 bash readline 會丟棄多位元組輸入（中文打不出來），
# opengrep 的 frozen Python 也會用 ascii codec 讀規則檔而炸掉。
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

USER nathan

# 四個 curl 安裝：Claude Code、uv（Python 工具鏈）、nvm（Node 版本管理）、rtk（token 節省 proxy）
RUN curl -fsSL https://claude.ai/install.sh | bash && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.6/install.sh | bash && \
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

RUN uv tool install ruff && ruff --version

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

# CodeGraph：程式碼知識圖譜 / 導航輔助（blast radius、caller 矩陣）。
# 不 pin：輸出不進報告 finding，無可重現性需求。
RUN curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh && \
    codegraph --version

ENV TZ=Asia/Taipei \
    FORCE_COLOR=1 \
    TERM=xterm-256color \
    COLORTERM=truecolor

WORKDIR /home/nathan/code-review

# entrypoint 放最後：改 script 只會 invalidate 這幾層，不會 bust 上面的工具安裝 cache。
# 最後一層寫入 build time 給 entrypoint 啟動時顯示。
USER root
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && \
    date -Iseconds > /etc/image-build-time
USER nathan

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["claude", "--dangerously-skip-permissions"]
