#!/usr/bin/env bash
# 把 skills/ 底下的 skill 安裝進 Claude Code：建立 symlink，不複製檔案，
# 所以在這個 repo 裡改一行，下一次對話就吃得到。
#
#   ./install.sh                      安裝 skills/ 底下全部
#   ./install.sh nathan-code-review   只裝指定的一個
#
# 重複執行是安全的：既有的 symlink 會被更新（包含指向舊路徑的斷鏈），
# 非 symlink 的既有檔案則會停下來要你自己處理。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$ROOT/skills"
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

install_skill() {
  local name=$1 src="$SKILLS_SRC/$1"

  if [ ! -f "$src/SKILL.md" ]; then
    echo "  ✗ $name 不是一個 skill（找不到 SKILL.md）" >&2
    return 1
  fi

  echo "$name"
  link "$src" "$SKILLS_DIR/$name"

  # Agents are a harness-level concern rather than skill content, so they are
  # linked separately into the agents directory. A skill with no agents/
  # directory is perfectly normal.
  shopt -s nullglob
  local agents=("$src"/agents/*.md)
  shopt -u nullglob
  for a in "${agents[@]}"; do
    link "$a" "$AGENTS_DIR/$(basename "$a")"
  done
}

if [ ! -d "$SKILLS_SRC" ]; then
  echo "找不到 $SKILLS_SRC" >&2
  exit 1
fi

mkdir -p "$SKILLS_DIR" "$AGENTS_DIR"

if [ $# -gt 0 ]; then
  targets=("$@")
else
  shopt -s nullglob
  targets=()
  for d in "$SKILLS_SRC"/*/; do targets+=("$(basename "$d")"); done
  shopt -u nullglob
fi

if [ ${#targets[@]} -eq 0 ]; then
  echo "$SKILLS_SRC 底下沒有 skill" >&2
  exit 1
fi

echo "來源：$SKILLS_SRC"
echo
for name in "${targets[@]}"; do
  install_skill "$name"
  echo
done

echo "完成。接著在 Claude Code 中執行 /reload-skills 讓它重新載入。"
echo
echo "nathan-code-review 使用前請確認："
echo "  GITLAB_TOKEN          GitLab API token（scope: api）"
echo "  NCR_OPENGREP_RULES    Semgrep rules 目錄，預設 \$HOME/semgrep-rules"
echo
echo "盤點目前環境的工具與憑證："
echo "  uv run $SKILLS_SRC/nathan-code-review/scripts/preflight.py --human"
