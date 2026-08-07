#!/bin/bash
# 一次把所有測試跑完。
#
# 為什麼需要這支：每個測試檔都是獨立腳本（各自 `sys.exit(1 if _fails else 0)`），要跑全套
# 原本只能照 README 那張表逐條複製指令——漏跑哪一個不會有任何跡象。測試寫得再好，
# 沒有機制保證它們被跑過就等於少了一半。
#
# 用法：
#   tests/run-all.sh              # 不需要 docker 的那些（快，改完 server/ 先跑這個）
#   tests/run-all.sh --all        # 全部，含需要 docker daemon 的
#   tests/run-all.sh --e2e        # 只跑瀏覽器 e2e（需先 playwright install chromium）
#
# ⚠ 相依清單只有一份，就是 pyproject.toml 的 optional-dependencies。這裡用
#   `--with` 逐一列名是因為測試是拿 `uv run` 直接跑腳本、不是安裝這個套件——
#   要動的話兩邊一起動（deploy/Dockerfile 的註解記的是同一個教訓）。
set -uo pipefail
cd "$(dirname "$0")/.."

DEPS=(--with flask --with docker --with sqlalchemy --with argon2-cffi
      --with psutil --with cryptography
      --with websocket-client --with pexpect --with playwright)

# 需要真的 docker daemon 的（README 標 ✓ 的那些）。
# ⚠ 測試與正式 stack 共用同一個 dockerd，這幾支會呼叫 reconcile_once——它們靠
#   `_ScopedClient` 把既有容器藏起來，不要在沒讀過那段的情況下新增到這個清單。
# ⚠ 用**陣列**不要用空白分隔的字串：字串換行後 `case " $s " in *" name "*)` 對不上行尾那個
#   名字（分隔它的是 \n 不是空白），該被 gate 的測試會在 quick 模式偷跑並紅掉，而跳過清單
#   上又看不到它——找起來很久（2026-07-27 撞到，test_view_lifecycle）。
NEEDS_DOCKER=(test_session_lifecycle test_view_lifecycle
              test_reconciler test_entrypoint_human_path test_entrypoint_profile
              e2e_flow)

# 另外需要 host 上有 ttyd binary 的：這兩支會真的把 ttyd 生出來（不是在容器裡跑）。
# 沒裝的話所有 port 都起不來，會以「無可用 port」這種完全不像缺工具的訊息失敗。
NEEDS_TTYD=(test_view_lifecycle e2e_flow)
have_ttyd=0
command -v ttyd >/dev/null 2>&1 && have_ttyd=1

mode="${1:-quick}"
case "${mode}" in
  quick|--quick) want_docker=0; want_e2e=1 ;;
  --all)         want_docker=1; want_e2e=1 ;;
  --e2e)         want_docker=1; want_e2e=2 ;;   # 2 ＝只跑 e2e
  *) echo "不認得的參數：${mode}（只收 --all / --e2e）" >&2; exit 2 ;;
esac

fails=0
ran=0
skipped=()

# --- 守衛：測試不可以碰使用者的真實檔案 ---------------------------------------
#
# CLAUDE.md 禁止清單第五條。2026-07-28 被穿透過一次：test_entrypoint_profile 用線上 DB
# 且 config.MOUNTS 非空，於是 create() 真的跑了 provision_user_space，在 host 的家目錄下
# 長出 ~/claude-pty-space/user-1/——而裡面的 owner.json 記著測試的擁有者（system），與
# 正式 DB 的 user 1（部署者自己的帳號）對不上，**正式部署第一次開 session 就會被擁有者檢查擋下**。
#
# 那次不是任何一份 code review 抓到的（分類走查與三個掃描器都看不到），是實際跑完之後
# 對照家目錄才發現。所以這裡不修單一實例，而是把整類擋在門口：跑完之後那個目錄要與
# 跑之前**逐項相同**。
#
# ⚠ 只守 per-user 空間，不守 ~/.claude / ~/.claude-pty：後兩者有正式 stack 在背景寫
#   （reconciler、憑證輪替），會誤報。這個目錄則是「只有真的開 session 才會動」，
#   而測試不該開真 session 到正式空間。
SPACE_DIR="${CLAUDE_PTY_SPACE:-$HOME/claude-pty-space}"
space_snapshot() { [ -d "${SPACE_DIR}" ] && ls -A "${SPACE_DIR}" 2>/dev/null | sort || true; }
SPACE_BEFORE="$(space_snapshot)"

in_list() {           # in_list <name> <item...>：名字在清單裡回 0
  local needle="$1"; shift
  local item
  for item in "$@"; do [ "${item}" = "${needle}" ] && return 0; done
  return 1
}

run_one() {
  local f="$1" base
  base="$(basename "${f}" .py)"
  # ⚠ 先判「這個模式要不要跑它」再判環境缺什麼：反過來的話 --e2e 模式下每支非 e2e 測試
  #   都會被報成「沒有 ttyd」，而那不是它被跳過的原因。
  if [ "${want_e2e}" -eq 2 ] && [[ "${base}" != e2e_* ]]; then
    skipped+=("${base}（--e2e 只跑瀏覽器測試）"); return
  fi
  if in_list "${base}" "${NEEDS_DOCKER[@]}" && [ "${want_docker}" -eq 0 ]; then
    skipped+=("${base}（需要 docker，用 --all）"); return
  fi
  if in_list "${base}" "${NEEDS_TTYD[@]}" && [ "${have_ttyd}" -eq 0 ]; then
    skipped+=("${base}（host 上沒有 ttyd binary）"); return
  fi
  printf '\n\033[1m== %s\033[0m\n' "${base}"
  ran=$((ran + 1))
  if ! uv run "${DEPS[@]}" python "${f}"; then
    fails=$((fails + 1))
    echo "   ↑ ${base} 失敗"
  fi
}

# --- 先驗 app.js 的語法 -------------------------------------------------------
#
# ⚠ 這一條放在最前面，因為它壞掉時的**症狀指向完全錯的地方**。app.js 解析失敗＝整頁沒有
#   任何 JS，於是每一支瀏覽器測試都停在「登入之後沒有跳轉」而逾時——看起來像是登入壞了。
#   2026-07-29 實測踩到：抽屜那段 HTML 註解裡寫了一個反引號，而那段在 template literal
#   裡面，字串當場被截斷。從逾時訊息完全看不出這件事，`node --check` 一秒就指到行號。
# ⚠ 沒有 node 就跳過並講出來——不可以靜靜不驗。
if command -v node >/dev/null 2>&1; then
  printf '\n\033[1m== app.js 語法\033[0m\n'
  if node --check server/static/js/app.js; then
    echo "  PASS  解析得過"
  else
    fails=$((fails + 1))
    echo "   ↑ app.js 語法錯誤——所有瀏覽器測試都會以「登入逾時」的形式失敗"
  fi
else
  skipped+=("app.js 語法檢查（host 上沒有 node）")
fi

for f in tests/test_*.py tests/e2e_*.py; do
  run_one "${f}"
done

echo
if [ ${#skipped[@]} -gt 0 ]; then
  # ⚠ 跳過了什麼一定要講出來。靜靜略過會讓「全部通過」看起來像涵蓋了全部。
  echo "跳過 ${#skipped[@]} 支："
  printf '  · %s\n' "${skipped[@]}"
fi
echo "跑了 ${ran} 支，${fails} 支失敗"

# 見上方守衛的說明。這一條**失敗就是失敗**，不是警告——被它抓到代表某支測試把真實家目錄
# 當成了自己的工作區，而那條規則沒有例外。修法是給那支測試自己的 config.SPACE_HOST /
# SPACE_SELF（tmpdir），見 tests/test_entrypoint_profile.py 的範例。
if [ "$(space_snapshot)" != "${SPACE_BEFORE}" ]; then
  echo
  echo "❌ 測試動到了使用者的真實 per-user 空間（${SPACE_DIR}）——這是 CLAUDE.md 禁止的。"
  echo "   跑之前：$(printf '%s' "${SPACE_BEFORE}" | tr '\n' ' ')"
  echo "   跑之後：$(space_snapshot | tr '\n' ' ')"
  echo "   請把那支測試的 config.SPACE_HOST / SPACE_SELF 指進 tmpdir，並清掉上面多出來的項目。"
  fails=$((fails + 1))
fi
exit $(( fails > 0 ? 1 : 0 ))
