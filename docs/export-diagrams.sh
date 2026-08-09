#!/usr/bin/env bash
# 把 repo 裡的 .drawio 匯出成 PNG。
#
# PNG 不進版控：它是 .drawio 的衍生物，一行指令就能重生，而每改一次圖就多一版
# 好幾 MB 的二進位檔。要嵌進文章或投影片時跑這支，產物落在 diagram-png/（已 gitignore）。
#
# 需要 draw.io desktop：brew install --cask drawio
# ⚠ 它是 Electron app，跑的時候會開一個看不見的視窗——所以這支不能在沒有 GUI
#   session 的環境（純 ssh、CI runner）裡跑，會卡在 mach port 或 X display 上。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/diagram-png}"

# 縮放倍率。架構圖字小，2 倍在 BookStack 的 scaled-1680 縮圖下才讀得清楚。
SCALE="${SCALE:-2}"

# 找 drawio。PATH 上的優先（Linux / 有裝 CLI 的情況），否則用 macOS 的 app bundle。
if command -v drawio >/dev/null 2>&1; then
    DRAWIO=(drawio)
elif [ -x "/Applications/draw.io.app/Contents/MacOS/draw.io" ]; then
    DRAWIO=("/Applications/draw.io.app/Contents/MacOS/draw.io")
else
    echo "找不到 draw.io。安裝：brew install --cask drawio" >&2
    exit 1
fi

# 「檔案:頁碼:輸出檔名」。
# ⚠ 頁碼從 1 開始（draw.io v27.0.2 之前是 0-based，給錯會回 "Invalid page index"）。
#   新增頁面時記得同步這張表，否則新頁不會被匯出，而且不會有任何錯誤訊息。
JOBS=(
    "docs/skill-architecture.drawio:1:skill-architecture.png"
    "docs/review-workflow.drawio:1:review-workflow.png"
    "docs/architecture.drawio:1:architecture-1-overview.png"
    "docs/architecture.drawio:2:architecture-2-claude-pty.png"
    "dev-container/dev-container.drawio:1:dev-container-1-topology.png"
    "dev-container/dev-container.drawio:2:dev-container-2-ssh.png"
)

mkdir -p "$OUT_DIR"
rc=0

for job in "${JOBS[@]}"; do
    src="${job%%:*}"
    rest="${job#*:}"
    page="${rest%%:*}"
    name="${rest#*:}"

    if [ ! -f "$REPO_ROOT/$src" ]; then
        echo "  ✗ 找不到 $src——圖搬過位置了？請一併更新本檔的 JOBS" >&2
        rc=1
        continue
    fi

    # 匯出失敗只記下來、不中止：一張圖出問題不該讓後面的全部沒產出。
    if "${DRAWIO[@]}" -x -f png --scale "$SCALE" -p "$page" \
        -o "$OUT_DIR/$name" "$REPO_ROOT/$src" >/dev/null 2>&1 && [ -s "$OUT_DIR/$name" ]; then
        printf "  ✓ %-34s p%s → %s\n" "$src" "$page" "$name"
    else
        echo "  ✗ $src p$page 匯出失敗" >&2
        rc=1
    fi
done

echo
echo "產出目錄：$OUT_DIR"
exit "$rc"
