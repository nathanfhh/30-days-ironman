#!/usr/bin/env bash
# 啟動 ncr-dev-container 的 wrapper：解決兩件事——憑證怎麼進容器、Opengrep 規則怎麼進容器。
#
# 憑證來源優先序：
#   1. CLAUDE_CODE_OAUTH_TOKEN 環境變數（設了就直接透傳進容器，不碰 Keychain）
#   2. macOS：從 Keychain 解出 OAuth 憑證，寫成 ~/.claude/.credentials.json
#      隨 ~/.claude mount 進容器（Linux 版 Claude Code 認得的檔案位置）
#   3. Linux host：~/.claude/.credentials.json 本來就存在（host 登入過即有）
# 三者皆無 → 退出，不啟動一個註定登不進去的容器。
set -euo pipefail

IMAGE=ncr-dev-container
RUN_ENV=()
RUN_MOUNTS=()
CRED_FILE=""

if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    # 優先序 1：token 環境變數直接透傳（-e 不帶值 = 取用 host 目前的值）
    RUN_ENV+=(-e CLAUDE_CODE_OAUTH_TOKEN)
elif [ "$(uname)" = "Darwin" ]; then
    # 優先序 2：macOS 把憑證鎖在 Keychain，解出成 Linux 版認得的檔案
    #（第一次執行會跳出 Keychain 授權視窗，按「允許」）
    if security find-generic-password -s "Claude Code-credentials" -w \
         > ~/.claude/.credentials.json 2>/dev/null \
       && [ -s ~/.claude/.credentials.json ]; then
        chmod 600 ~/.claude/.credentials.json
        CRED_FILE=~/.claude/.credentials.json
        # 憑證檔只是給容器用的明文複本，退出時刪掉（macOS 本體仍用 Keychain）
        trap '[ -n "$CRED_FILE" ] && rm -f "$CRED_FILE"' EXIT
    else
        rm -f ~/.claude/.credentials.json
        echo "❌ Keychain 沒有 Claude Code 憑證，也沒設定 CLAUDE_CODE_OAUTH_TOKEN。" >&2
        echo "   先在 host 登入一次 claude，或 export CLAUDE_CODE_OAUTH_TOKEN 再執行。" >&2
        exit 1
    fi
else
    # 優先序 3：Linux host 的憑證檔本來就落地，只確認它在
    if [ ! -s ~/.claude/.credentials.json ]; then
        echo "❌ 找不到 ~/.claude/.credentials.json，也沒設定 CLAUDE_CODE_OAUTH_TOKEN。" >&2
        echo "   先在 host 登入一次 claude，或 export CLAUDE_CODE_OAUTH_TOKEN 再執行。" >&2
        exit 1
    fi
fi

# Opengrep 規則（A4 軌道）：opengrep binary 不內建規則，從 host 的 semgrep-rules clone 餵。
# 啟動前 best-effort 更新（離線或 pull 失敗就沿用現有版本，不擋啟動）、唯讀 mount 進容器。
# clone 不存在 → 警告後照常啟動，A4 軌道本場無規則可用。
RULES_DIR="$HOME/Projects/semgrep-rules"
if [ -d "$RULES_DIR/.git" ]; then
    git -C "$RULES_DIR" pull --ff-only 2>/dev/null || echo "⚠️  semgrep-rules 更新失敗，沿用現有版本"
    RUN_MOUNTS+=(-v "$RULES_DIR":/home/nathan/semgrep-rules:ro)
else
    echo "⚠️  找不到 $RULES_DIR，本場 Opengrep（A4）無規則可用。"
    echo "   取得規則：git clone https://github.com/semgrep/semgrep-rules.git $RULES_DIR"
fi

# 把「現在所在的資料夾」掛進容器的工作目錄：在要審查的專案根目錄執行本腳本。
#（陣列展開用 ${arr[@]+...} 寫法：macOS 內建 bash 3.2 在 set -u 下，空陣列直接展開會炸）
docker run --rm -it \
    ${RUN_ENV[@]+"${RUN_ENV[@]}"} \
    ${RUN_MOUNTS[@]+"${RUN_MOUNTS[@]}"} \
    -v ~/.claude:/home/nathan/.claude \
    -v ~/.claude.json:/home/nathan/.claude.json \
    -v "$PWD":/home/nathan/code-review \
    "$IMAGE" "$@"
