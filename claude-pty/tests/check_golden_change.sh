#!/bin/bash
# golden 動了，commit 訊息就要說是哪幾場，而且要說全。
#
# 用法：
#   tests/check_golden_change.sh                 # 預設 main..HEAD
#   tests/check_golden_change.sh <base>..<head>  # 指定範圍
#   tests/check_golden_change.sh <base> <head>   # 同上，分開寫
#
# ## 為什麼要這一道
#
# `tests/golden/` 是規格。**重錄等於改規格**，而改規格最容易發生的方式不是有人蓄意，是
# 「`golden_check` 紅了 → 順手重錄一次 → 綠了 → commit」。那一連串每一步都很自然，走完
# 之後「我改壞了」就被寫成「這就是新的對的樣子」，而 diff 裡是幾十個二進位檔案，review
# 的人翻不動。
#
# 這道 gate 不阻止重錄，它只要求**重錄的人把改了哪幾場寫進 commit 訊息**。寫得出來就代表
# 他知道自己改了什麼；寫不出來，那正是要攔的那一次。
#
# ## 標記怎麼寫
#
# commit 訊息（subject 或 body）裡**一行的開頭**寫 `golden:`，後面列場景名，逗號或空白分隔：
#
#     golden: sessions-list, sessions-filters
#
# ⚠ **必須在行首**（前面只允許空白）。第一版是「這一行裡有 `golden:` 就算」，結果我自己
#   的 commit body 在解釋這道 gate 時寫了一句「不支援 `golden: all` 這種寫法」，那句散文
#   當場被當成一份宣告。噪音是小事，**假綠才是真的問題**：一句「這次沒有動
#   golden: sessions-list 那一場」會讓那一場被當成已交代。行首這條線讓「寫標記」與
#   「談論標記」分得開。
#
# 場景多的時候可以拆成好幾行、也可以散在範圍內的好幾顆 commit 裡，這支會把它們**聯集**
# 起來再比對。目錄名以外的字（例如「重錄」「全部」）會被當成不存在的場景而落在
# 「多餘的」那一段——那一段只警告不擋，因為打錯字不該擋住一次正確的重錄。
#
# ⚠ **刻意不支援 `golden: all` 這種寫法。** 這道 gate 的全部價值就在「逐場列出來」那個
#   動作上；給一個一次涵蓋全部的關鍵字，等於把它變成一句咒語。
# ⚠ `META` 也是一個合法的名字（`tests/golden/META` 是錄製環境的指紋，不是場景，但它變了
#   同樣要講）。
set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(git rev-parse --show-toplevel)"
GOLDEN_PREFIX="$(realpath --relative-to="${REPO_ROOT}" "$PWD/tests/golden" 2>/dev/null \
                 || python3 -c "import os,sys;print(os.path.relpath(sys.argv[1],sys.argv[2]))" "$PWD/tests/golden" "${REPO_ROOT}")"

case "$#" in
  0) RANGE="main..HEAD" ;;
  1) RANGE="$1" ;;
  2) RANGE="$1..$2" ;;
  *) echo "用法：$0 [<base>..<head> | <base> <head>]" >&2; exit 2 ;;
esac

# ⚠ 範圍解不開就是**紅**，不是「當成沒有變動」。解不開的最常見原因是 CI 上沒有 fetch 到
#   base（淺 clone），而那時「沒有變動」與「看不到變動」在輸出上長得一模一樣。
if ! git -C "${REPO_ROOT}" rev-list --quiet "${RANGE}" -- 2>/dev/null; then
  echo "  FAIL  解不開這個範圍：${RANGE}"
  echo "        CI 上最常見的原因是淺 clone 沒有 fetch 到 base（actions/checkout 要 fetch-depth: 0）。"
  echo "        「看不到變動」與「沒有變動」不可以長得一樣，所以這裡紅，不是放行。"
  exit 1
fi

echo "== golden 變動 gate（${RANGE}）=="

# --- 變動了哪幾場 -------------------------------------------------------------
changed="$(git -C "${REPO_ROOT}" diff --name-only "${RANGE}" -- "${GOLDEN_PREFIX}" \
           | sed -e "s|^${GOLDEN_PREFIX}/||" \
           | awk -F/ '{print $1}' \
           | sort -u)"

if [ -z "${changed}" ]; then
  echo "  PASS  golden 未動（這個範圍沒有碰到 ${GOLDEN_PREFIX}/）"
  exit 0
fi

n_changed="$(printf '%s\n' "${changed}" | wc -l | tr -d ' ')"
echo "  變動的場景（${n_changed}）："
printf '    · %s\n' ${changed}

# --- 訊息裡宣告了哪幾場 -------------------------------------------------------
# ⚠ 用 `%B`（完整訊息）不是 `%s`（只有 subject）：場景一多一定會寫進 body。
# ⚠ `^[[:space:]]*` 不可以拿掉：不釘行首的話，散文裡提到 `golden:` 也會被當成宣告，
#   而那是一條通往**假綠**的路（見檔頭的說明）。
declared="$(git -C "${REPO_ROOT}" log --format=%B "${RANGE}" \
            | sed -n 's/^[[:space:]]*[Gg]olden:[[:space:]]*//p' \
            | tr ',，、;；' ' ' \
            | tr -s '[:space:]' '\n' \
            | sed '/^$/d' \
            | sort -u)"

if [ -z "${declared}" ]; then
  echo
  echo "  FAIL  golden 動了，但這個範圍內沒有任何一顆 commit 帶 \`golden:\` 標記。"
  echo "        重錄等於改規格。請在 commit 訊息裡寫明改了哪幾場，例如："
  echo "          golden: $(printf '%s\n' ${changed} | head -2 | tr '\n' ' ' | sed 's/ $//')"
  exit 1
fi

missing="$(comm -23 <(printf '%s\n' ${changed} | sort -u) <(printf '%s\n' ${declared} | sort -u))"
extra="$(comm -13 <(printf '%s\n' ${changed} | sort -u) <(printf '%s\n' ${declared} | sort -u))"

if [ -n "${extra}" ]; then
  # 只警告不擋：打錯一個字不該擋住一次正確的重錄，但要看得見（可能是漏掉的場景名打錯了）。
  echo "  ⚠ 訊息裡有幾個名字對不上任何變動的場景（打錯字？）："
  printf '    · %s\n' ${extra}
fi

if [ -n "${missing}" ]; then
  echo
  echo "  FAIL  這幾場變動了，但 commit 訊息沒有提到："
  printf '    · %s\n' ${missing}
  echo "        把它們補進 \`golden:\` 那一行（可以分好幾行、也可以在範圍內的另一顆 commit）。"
  exit 1
fi

echo "  PASS  ${n_changed} 個變動的場景在 commit 訊息裡都交代了"
exit 0
