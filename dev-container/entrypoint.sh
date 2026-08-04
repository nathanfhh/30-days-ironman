#!/usr/bin/env bash
# 容器啟動入口：報告 image 資訊、確認憑證在場，然後把控制權交給指令。
# 預設指令是 claude --dangerously-skip-permissions：
# 容器本身就是隔離層，煞車在牆上，不在每一次的允許提問裡。
set -euo pipefail

echo "📦 ncr-dev-container｜image built: $(cat /etc/image-build-time 2>/dev/null || echo unknown)"

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ ! -s "$HOME/.claude/.credentials.json" ]; then
    echo "⚠️  容器內沒有憑證（CLAUDE_CODE_OAUTH_TOKEN 或 ~/.claude/.credentials.json），claude 會要求登入。"
fi

exec "$@"
