#!/usr/bin/env bash
# 安裝 nathan-code-review：建立兩處 symlink，讓 Claude Code 找得到 skill 與 ncr-* subagents。
# 重複執行是安全的：既有的 symlink 會被更新，非 symlink 的既有檔案則會停下來要你自己處理。
set -euo pipefail

SKILL_NAME="nathan-code-review"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SKILLS_DIR="$CLAUDE_DIR/skills"
AGENTS_DIR="$CLAUDE_DIR/agents"

link() {
  local target=$1 linkpath=$2
  if [ -e "$linkpath" ] && [ ! -L "$linkpath" ]; then
    echo "  ✗ $linkpath 已存在且不是 symlink，請自行處理後重跑" >&2
    return 1
  fi
  ln -sfn "$target" "$linkpath"
  echo "  ✓ $linkpath"
}

echo "來源：$SRC"
mkdir -p "$SKILLS_DIR" "$AGENTS_DIR"

echo "安裝 skill："
link "$SRC" "$SKILLS_DIR/$SKILL_NAME"

echo "安裝 ncr-* subagents："
shopt -s nullglob
agents=("$SRC"/agents/ncr-*.md)
if [ ${#agents[@]} -eq 0 ]; then
  echo "  ✗ 找不到 agents/ncr-*.md" >&2
  exit 1
fi
for a in "${agents[@]}"; do
  link "$a" "$AGENTS_DIR/$(basename "$a")"
done

echo
echo "完成。接著在 Claude Code 中執行 /reload-skills 讓它重新載入。"
echo
echo "使用前請確認："
echo "  GITLAB_TOKEN          GitLab API token（scope: api）"
echo "  NCR_OPENGREP_RULES    Semgrep rules 目錄，預設 \$HOME/semgrep-rules"
echo
echo "盤點目前環境的工具與憑證："
echo "  uv run $SRC/scripts/preflight.py --human"
