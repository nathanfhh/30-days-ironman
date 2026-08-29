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
      --with websocket-client --with pexpect --with playwright
      # ⚠ 只有 golden_check 用得到：它要把兩張 PNG 逐像素比。同 playwright／pexpect，
      #   是只在這裡出現的測試期工具，**刻意不是**專案相依（進了 pyproject 就會跟著
      #   進正式 image，而正式環境一張圖都不需要比）。
      --with pillow
      # ⚠ 只有 test_locked_runtime 用得到，而且**刻意不是**專案相依：它是 deploy 那道閘
      #   拿來照規格評估 requirement marker 的工具，進了 pyproject 就會跟著進正式 image。
      #   同 playwright／pexpect／websocket-client，都是只在這裡出現的測試期工具。
      --with packaging)

# 需要真的 docker daemon 的（README 標 ✓ 的那些）。
# ⚠ 測試與正式 stack 共用同一個 dockerd，這幾支會呼叫 reconcile_once——它們靠
#   `_ScopedClient` 把既有容器藏起來，不要在沒讀過那段的情況下新增到這個清單。
# ⚠ 用**陣列**不要用空白分隔的字串：字串換行後 `case " $s " in *" name "*)` 對不上行尾那個
#   名字（分隔它的是 \n 不是空白），該被 gate 的測試會在 quick 模式偷跑並紅掉，而跳過清單
#   上又看不到它——找起來很久（2026-07-27 撞到，test_view_lifecycle）。
NEEDS_DOCKER=(test_session_lifecycle test_view_lifecycle
              test_reconciler test_entrypoint_human_path test_entrypoint_profile
              test_firewall_ssh_gate test_user_proxy test_network_isolation
              test_gitlab_upstream_e2e test_restricted_proxy_reach e2e_flow
              test_token_fd test_trivy_volume test_ro_socket_mount
              test_entrypoint_mitm_password test_mitm_bridge
              test_ttyd_unknown_flag)
# ⚠ 判準是「會不會真的起容器／建 volume」，不是「檔案裡有沒有出現 docker」。用假 client
#   的那幾支（test_host_platform／test_jaeger_wiring／test_trivy_db／test_ttyd_identity）
#   一個容器都不起，留在 quick 模式是對的。自我 SKIP 不能取代這道 gate：docker 在的開發機
#   上它不會 SKIP，而是安靜地在 quick 模式裡真的跑起容器。

# `fake_gitlab.py` 不是測試，是被 test_gitlab_upstream_e2e 掛進容器裡跑的假上游。
# ⚠ 它的檔名沒有 `test_` 前綴正是為了不被下面那個 glob 撿走——改名前先想清楚。

# 需要 dev-container image 已經 build 好的。
# ⚠ 這幾支遇到缺 image 時**自己** print SKIP 再 exit 0——那正是「空跑」偵測要抓的形狀
#   （test_ro_socket_mount 就是這樣在 CI 上綠著跑完的）。但缺 image 是**真的環境條件**，
#   不是設定漏了，所以正解是讓它進跳過清單、看得見，而不是紅燈。
#   ⚠ CI 不受影響：dev-container job 會先現 build 這顆 image。
# ⚠ image 名字與那幾支同一個來源（CLAUDE_PTY_IMAGE），不在這裡抄第二份——抄一份的那天，
#   gate 判的就不是測試真正要用的那顆了。
NEEDS_NCR_IMAGE=(test_token_fd test_trivy_volume test_entrypoint_mitm_password test_mitm_bridge)
have_ncr_image=0
docker image inspect "${CLAUDE_PTY_IMAGE:-ncr-dev-container}" >/dev/null 2>&1 && have_ncr_image=1

# 需要**控制平面自己那顆 image**（不是 dev-container 那顆）：裡面才有兩顆真的 ttyd binary。
# ⚠ 跟上面同一個理由進清單：缺 image 時那支會自己 print SKIP 再 exit 0，而那個形狀跟
#   「跑過而且過了」在 CI 上長得一模一樣。讓它進跳過清單、看得見。
NEEDS_CONTROL_IMAGE=(test_ttyd_unknown_flag)
have_control_image=0
docker image inspect "${CLAUDE_PTY_CONTROL_IMAGE:-claude-pty-control:latest}" >/dev/null 2>&1 && have_control_image=1

# 另外需要 host 上有 ttyd binary 的：這兩支會真的把 ttyd 生出來（不是在容器裡跑）。
# 沒裝的話所有 port 都起不來，會以「無可用 port」這種完全不像缺工具的訊息失敗。
NEEDS_TTYD=(test_view_lifecycle e2e_flow)
have_ttyd=0
command -v ttyd >/dev/null 2>&1 && have_ttyd=1

# 只在 Linux 上驗得到的：這兩支問的性質在 macOS 上**不存在**，不是「驗過沒問題」。
#   · test_trivy_volume  —— Docker Desktop 對 bind mount 做 uid 對映（同一個目錄在 uid 1001
#     的容器裡就顯示 owner 1001，兩邊都可寫），整條 uid 鏈在 macOS 上沒有東西可驗。
#     機制推導與 Linux 實機驗收見 `docs/linux-acceptance.md`（ADR 0017 / 0018）。
#   · test_firewall_ssh_gate —— macOS 的 SSH_AUTH_SOCK 是 launchd 管的
#     `/var/run/com.apple.launchd.*/Listeners`，掛不進容器（Docker Desktop 另給
#     `/run/host-services/ssh-auth.sock`），「有 agent」那組情境根本組不起來。
# ⚠ 為什麼要有這道 gate：在它之前，macOS 上跑 `--all` **永遠是紅的**，於是這五條紅燈
#   被當成背景噪音消化掉——而 2026-08-15 就有一支真的壞掉的 fixture（假上游的 bare repo
#   沒指定 `-b main`）藏在那片紅裡沒被發現。永遠紅的燈跟沒有燈是一樣的東西。
NEEDS_LINUX=(test_trivy_volume test_firewall_ssh_gate)
is_linux=0
[ "$(uname -s)" = "Linux" ] && is_linux=1

# 需要 host 上有 claude 憑證的：這支用真 PTY 把 entrypoint 的互動選單走完，最後要看到
# Claude Code 的畫面才算數（測試會複製一份憑證進沙盒，不掛使用者真正的 ~/.claude）。
# ⚠ 沒有憑證時它不會快速失敗，而是 pexpect 一路等到逾時：2026-08-15 在 CI 上實測卡了
#   153 秒才紅，佔整套 340 秒的四成五，而畫面上只有一串正則，看不出「你少了憑證」。
# ⚠ **憑證不是只會住在檔案裡。** macOS 上 Claude Code 把它放進 keychain，`~/.claude/`
#   底下根本沒有 `.credentials.json`；於是這道 gate 在**每一台 macOS 開發機**上都判「沒有
#   憑證」，那支測試從來沒有在本機跑過（2026-08-27 在 Nathan 的機器上發現，那台的憑證
#   在 keychain：`security find-generic-password -s "Claude Code-credentials"` 命中，
#   而檔案不存在）。所以兩個地方都要問。
# ⚠ 只問**存在性**，不取值：`security find-generic-password` 不帶 `-w` 只讀 metadata，
#   不會把密文吐出來、也不會跳出授權對話框。要值是 `-w`，這裡刻意不用。
# ⚠ service 名稱 `Claude Code-credentials` 是在機器上查證過的（`-s` 與 `-l` 都命中），
#   不是猜的。
# ⚠ 非 macOS 沒有 `security` 這支指令，所以先確認它在才問；不在就只看檔案。
#   Linux（含 CI 的 runner）走的就是這條，行為與先前逐字相同。
NEEDS_CLAUDE_CRED=(test_entrypoint_human_path)
have_claude_cred=0
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || [ -f "${HOME}/.claude/.credentials.json" ]; then
  have_claude_cred=1
elif command -v security >/dev/null 2>&1 &&
     security find-generic-password -s "Claude Code-credentials" >/dev/null 2>&1; then
  # ⚠ keychain 裡的憑證**複製不進沙盒**（那要把密文讀出來，不做）。所以這一條的意思是
  #   「這台機器有登入過，entrypoint 那條路值得跑」，不是「沙盒裡會有憑證」：測試照跑，
  #   但登入後才看得到的那一條（④b）會印 SKIP。要讓它也跑，設 CLAUDE_CODE_OAUTH_TOKEN
  #   （`claude setup-token`），那個值會被 `-e` 帶進容器。
  have_claude_cred=1
fi

# Vue 版的 e2e 需要前端**已經 build 過**（`server/static/dist/`，不進版控）。
# ⚠ 存在與否要在**用到的當下**才問，不是在這裡先算一次：上面那段前端六關的最後一關就是
#   build，它跑在這幾行之後——先算的話永遠是「還沒 build」。
# ⚠ 沒有 node 的機器整段前端會被跳過，那時 dist 真的不存在。那不是「測試壞了」，是環境
#   缺一個工具，所以進跳過清單、看得見（同 ttyd、同 playwright 的處置）。
# ⚠ **每一支瀏覽器測試都要 dist**，不是只有 e2e_vue_smoke。legacy 拆掉之後畫面只剩這一份，
#   八支 e2e 與 golden_check 全部吃它；只列一支的話，其餘那幾支在缺 dist 時會以一串看不懂的
#   逾時失敗，而真正的原因（沒 build）不會出現在跳過清單上（完整審查 L3）。
NEEDS_DIST=(e2e_account e2e_chips e2e_drawer e2e_filters e2e_flow e2e_gitlab_chip
            e2e_settings e2e_stale_row e2e_vue_smoke golden_check)

# 瀏覽器 e2e 需要 playwright 真的把 chromium 下載下來。沒下載的話每一支 e2e 都會吐一段
# 「Executable doesn't exist」的 traceback——看起來像測試壞了，其實是少一個安裝步驟。
# ⚠ 不可以只判「快取目錄在不在」：playwright 升版之後要的是**另一個 build 編號**，舊的那幾顆
#   還留在快取裡，於是目錄在、要的那顆不在（2026-08-15 實際踩到：快取有 1208/1217/1223/1228，
#   而當時的 playwright 要 1234）。所以問 playwright 自己要哪一顆，再去看那一顆在不在。
have_browser=0
_pw_dir="$(uv run "${DEPS[@]}" python -m playwright install --dry-run chromium-headless-shell 2>/dev/null \
           | awk '/Install location:/ {print $3; exit}')"
[ -n "${_pw_dir}" ] && [ -d "${_pw_dir}" ] && have_browser=1

# --- 參數 -------------------------------------------------------------------
#
# ⚠ 這裡曾經有一個 `--ui legacy|vue`（兩版並存期間用的）。**2026-08-26 legacy 拆除之後
#   它就消失了**：只剩一份前端，瀏覽器測試與 golden 一律對它跑，沒有第二條路可以切。
mode="${1:-quick}"
case "${mode}" in
  quick|--quick) want_docker=0; want_e2e=1 ;;
  --all)         want_docker=1; want_e2e=1 ;;
  --e2e)         want_docker=1; want_e2e=2 ;;   # 2 ＝只跑 e2e
  *) echo "不認得的參數：${mode}（只收 --all / --e2e）" >&2; exit 2 ;;
esac

# --- 先清掉 bytecode 快取 -----------------------------------------------------
#
# ⚠ CPython 判斷 `.pyc` 有沒有過期用的是 **(mtime, size)**。「改一行、跑測試、改回去」
#   這種迴圈（變異測試、二分搜 bug）很容易讓兩者都不變——把一行**搬位置**檔案大小就一樣，
#   而還原若發生在同一秒內 mtime 也一樣。於是測試會對著一份**不存在於磁碟上的程式碼**跑，
#   而且沒有任何跡象：原始碼看起來是對的，測試結果卻是另一回事（2026-08-09 實際踩到，
#   查了三輪才想到）。
# ⚠ 成本是每次多重新編譯一次（不到一秒），買到的是「測試結果一定對應現在的原始碼」。
find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true

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
  if [ "${want_e2e}" -eq 2 ] && [[ "${base}" != e2e_* ]] && [[ "${base}" != golden_* ]]; then
    skipped+=("${base}（--e2e 只跑瀏覽器測試）"); return
  fi
  if in_list "${base}" "${NEEDS_DOCKER[@]}" && [ "${want_docker}" -eq 0 ]; then
    skipped+=("${base}（需要 docker，用 --all）"); return
  fi
  if in_list "${base}" "${NEEDS_TTYD[@]}" && [ "${have_ttyd}" -eq 0 ]; then
    skipped+=("${base}（host 上沒有 ttyd binary）"); return
  fi
  if in_list "${base}" "${NEEDS_LINUX[@]}" && [ "${is_linux}" -eq 0 ]; then
    skipped+=("${base}（$(uname -s) 上驗不到這條性質，見 docs/linux-acceptance.md）"); return
  fi
  # ⚠ 排在平台判斷**之後**：平台限制更根本——macOS 上這條性質 build 了 image 也還是
  #   驗不到，先報「缺 image」會把人送去做一件白做的事。
  if in_list "${base}" "${NEEDS_NCR_IMAGE[@]}" && [ "${have_ncr_image}" -eq 0 ]; then
    skipped+=("${base}（沒有 ${CLAUDE_PTY_IMAGE:-ncr-dev-container} image，先跑 dev-container/build.sh）"); return
  fi
  if in_list "${base}" "${NEEDS_CONTROL_IMAGE[@]}" && [ "${have_control_image}" -eq 0 ]; then
    skipped+=("${base}（沒有 ${CLAUDE_PTY_CONTROL_IMAGE:-claude-pty-control:latest} image，先跑 docker compose -f deploy/docker-compose.yml build control）"); return
  fi
  if in_list "${base}" "${NEEDS_CLAUDE_CRED[@]}" && [ "${have_claude_cred}" -eq 0 ]; then
    # ⚠ 訊息要講得出「哪幾個地方都問過了」。原本只寫「沒有 claude 憑證」，而在 macOS 上
    #   憑證通常在 keychain 而不是檔案裡，讀的人會以為自己沒登入、跑去重登一次也沒用。
    skipped+=("${base}（沒有 claude 憑證：\$CLAUDE_CODE_OAUTH_TOKEN 未設、~/.claude/.credentials.json 不存在、keychain 也沒有 Claude Code-credentials）"); return
  fi
  if in_list "${base}" "${NEEDS_DIST[@]}" && [ ! -f server/static/dist/index.html ]; then
    skipped+=("${base}（前端還沒 build：server/static/dist/ 不存在，裝 node 24 讓上面那幾關跑）"); return
  fi
  # golden_check 也開真的瀏覽器（它就是拿畫面跟 tests/golden/ 錄下來的比），同一道 gate。
  if { [[ "${base}" == e2e_* ]] || [[ "${base}" == golden_* ]]; } && [ "${have_browser}" -eq 0 ]; then
    skipped+=("${base}（playwright 缺這版的瀏覽器：playwright install chromium-headless-shell）"); return
  fi
  printf '\n\033[1m== %s\033[0m\n' "${base}"
  ran=$((ran + 1))
  # ⚠ 邊印邊收（tee）：跑完之後還看得到它印了什麼——下面那道「空跑」檢查需要。
  #   直接 `> file` 的話跑很久的測試會整段沒有畫面。
  # ⚠ **`-u` 不是裝飾。** 接上 pipe 之後 stdout 不再是 TTY，CPython 會從行緩衝切成
  #   塊緩衝（8KB）——實測：三行間隔一秒的輸出，不加 -u 時會在行程結束那一刻**一次
  #   全部吐出來**。跑五分鐘的整合測試因此整段沒有畫面，看起來像卡死。
  #   這是 tee 帶進來的回歸，不是原本就有的：沒有 pipe 時 stdout 是 TTY，本來就是行緩衝。
  local out; out="$(mktemp)"
  if ! uv run "${DEPS[@]}" python -u "${f}" 2>&1 | tee "${out}"; then
    fails=$((fails + 1))
    echo "   ↑ ${base} 失敗"
  # --- 空跑：exit 0 但一條斷言都沒跑 -------------------------------------------
  #
  # ⚠ 這是假綠燈的第二種形狀，比「安靜地跳過」更難發現：測試在**自己內部** print 一行
  #   SKIP 再 `sys.exit(0)`，於是 run-all.sh 看到的是「跑完而且過了」，它不會進跳過清單，
  #   CI 那道「跳過上限 1」的 gate 也看不到。2026-08-22 實際發生：test_ro_socket_mount
  #   在 CI 上一路 SKIP（run 32579472171 的 log），而它存在的理由正是那幾條 chmod／mode
  #   斷言——它們從來沒有在 CI 上跑過，畫面卻一直是綠的。
  # ⚠ 判準是「有沒有印出任何 PASS/FAIL」，不是「有沒有出現 SKIP 這個字」。有幾支測試
  #   會跳過**其中一節**、其他斷言照跑（telemetry 選單、沒帶 build arg 的那條規則），
  #   那是正當的，抓字串會把它們一起弄紅——製造噪音的 gate 最後會被當成背景消化掉。
  # ⚠ 放在 run-all.sh 而不是 CI 的 log 剖析：這裡知道剛跑的是哪一支，不必去猜區塊標題
  #   的形狀（實測猜錯過——測試自己印的小節標題長得跟區塊標題一樣）。本機跑也吃得到。
  elif ! grep -qE '^[[:space:]]+(PASS|FAIL)[[:space:]]' "${out}"; then
    fails=$((fails + 1))
    echo "   ↑ ${base} 空跑：exit 0，但一條 PASS/FAIL 都沒印出來"
    echo "     這會被算成「跑過而且過了」。要嘛讓它明確失敗，要嘛加進上面的 NEEDS_* 清單"
    echo "     （那樣才會出現在跳過清單上，被 CI 的跳過上限管到）。"
  fi
  rm -f "${out}"
}

# ⚠ 這裡曾經有一道「app.js 語法檢查」（`node --check server/static/js/app.js`）。
#   那個檔案在 2026-08-26 隨 legacy 一起刪了，這道 gate 也跟著退場。它守的性質現在由前端
#   六關的 `vue-tsc` 與 `vite build` 接手，而且接得更緊：那兩關看得到型別與打包，
#   `node --check` 只看得到語法。

# --- 前端（Vue 版）的工具鏈 ---------------------------------------------------
#
# 六關，順序是「便宜的先擋」：安裝 → lint → 格式 → 型別 → 單元測試（含覆蓋率門檻）→ build。
#
# ⚠ 為什麼 build 也要跑：型別過得了不代表打包得出來（outDir 寫錯、import 到 root 外面沒放行、
#   CSS 原檔被搬走），而那些只有 `vite build` 會紅。產物不進版控，所以「沒有人 build 過」
#   這件事在部署之前不會有任何跡象。
# ⚠ `npm ci` 不是 `npm install`：ci 只照 lockfile 裝，裝不出來就直接失敗（同 deploy/Dockerfile）。
# ⚠ 沒有 node/npm 就整段跳過**並講出來**——不可以靜靜不驗（同 ttyd、同 playwright 的處置）。
front_gate() {          # front_gate <說明> <指令...>
  local label="$1"; shift
  printf '\n\033[1m== %s\033[0m\n' "${label}"
  if (cd frontend && "$@"); then
    echo "  PASS  ${label}"
  else
    fails=$((fails + 1))
    echo "   ↑ ${label} 失敗"
  fi
}

if [ ! -d frontend ]; then
  skipped+=("前端工具鏈（沒有 frontend/ 目錄）")
elif [ "${want_e2e}" -eq 2 ]; then
  skipped+=("前端工具鏈（--e2e 只跑瀏覽器測試）")
elif ! command -v npm >/dev/null 2>&1; then
  skipped+=("前端工具鏈（host 上沒有 npm，裝 node 24）")
elif [ ! -f frontend/package-lock.json ]; then
  # lockfile 不見了不是「環境沒裝」，是 repo 壞了——這條要紅，不是跳過。
  fails=$((fails + 1))
  echo "   ↑ frontend/package-lock.json 不見了：npm ci 沒有它就跑不了，而 npm install 會自己挑版本"
else
  front_gate "前端相依（npm ci）" npm ci --no-audit --no-fund
  front_gate "前端 lint（oxlint）" npm run --silent lint
  front_gate "前端格式（prettier --check）" npm run --silent format:check
  front_gate "前端型別（vue-tsc）" npm run --silent typecheck
  front_gate "前端單元測試（vitest，行覆蓋率門檻 70%）" npm run --silent test:coverage
  front_gate "前端 build（vite）" npm run --silent build

  # --- 供應鏈：前端的相依也要掃 -----------------------------------------------
  #
  # ⚠ python 那邊的相依早就在掃（deploy 的 image 掃描），而前端一口氣加了 200 多個
  #   套件——那些程式碼會被打包進 `/assets/*.js`，直接在使用者的瀏覽器裡執行。
  #   只掃後端等於掃了一半。
  # ⚠ **「沒有目標」不等於「乾淨」**（repo 既有的紀律，見 skills 的 scanners.md）：
  #   trivy 掃不到任何相依清單時一樣 exit 0、報告是空的，而那個空白什麼都沒證明。
  #   所以除了「有沒有漏洞」，還要驗「它真的把 lockfile 當成 npm 目標解析了」。
  # ⚠ 沒裝 trivy 就跳過並講出來（同 node、同 playwright 的做法）。
  if ! command -v trivy >/dev/null 2>&1; then
    skipped+=("前端相依掃描（host 上沒有 trivy）")
  else
    printf '\n\033[1m== 前端相依掃描（trivy）\033[0m\n'
    _trivy_out="$(mktemp)"
    if trivy fs --scanners vuln --severity CRITICAL,HIGH,MEDIUM --format json --quiet \
         frontend/package-lock.json > "${_trivy_out}" 2>/dev/null \
       && python3 - "${_trivy_out}" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
results = data.get("Results") or []
npm = [r for r in results if r.get("Type") == "npm"]
if not npm:
    print("  FAIL  trivy 沒有把 package-lock.json 當成 npm 目標解析——空報告證明不了任何事")
    sys.exit(1)
vulns = [v for r in npm for v in (r.get("Vulnerabilities") or [])]
if vulns:
    print(f"  FAIL  {len(vulns)} 筆 MEDIUM 以上：")
    for v in vulns[:20]:
        print(f"          {v['VulnerabilityID']}  {v['PkgName']} {v.get('InstalledVersion')}"
              f"  → {v.get('FixedVersion') or '尚無修正版'}  [{v['Severity']}]")
    sys.exit(1)
print(f"  PASS  {len(npm)} 個 npm 目標、0 筆 MEDIUM 以上")
PYEOF
    then
      :
    else
      fails=$((fails + 1))
      echo "   ↑ 前端相依掃描失敗"
    fi
    rm -f "${_trivy_out}"
  fi
fi

# dist 的保險絲：跑到這裡不在、或**比原始碼舊**，就補 build 一次。
#
# 正常情況上面那段前端六關的最後一關就是 build，所以到這裡 dist 已經是新的。但它有兩條
# 會被整段跳過的路：`--e2e` 模式（那一段自己會跳），以及 host 上沒有 npm。
# **缺 dist 等於每一支瀏覽器測試都跳過，那一輪什麼都沒測到而畫面上是綠的**
# （legacy 拆掉之後前端只剩這一份，所有瀏覽器測試都吃它）。
#
# ⚠ **判準是「新不新」不是「在不在」。** 第一版只問存不存在，於是
#   `./tests/run-all.sh --e2e --ui vue` 拿一份**上一次 build 的 dist** 去測，而那份
#   dist 的原始碼比工作區舊了兩個 commit。症狀是 golden 的網路序列多一發
#   `/api/auth/me`，我差一點把它當成 Vue 版的 bug 回報出去（2026-08-26 實際發生）。
#   這與這個檔案開頭清 `__pycache__` 的理由是同一個：**測試必須對應現在的原始碼**，
#   而「build 產物悄悄落後」沒有任何跡象。
# ⚠ 只在真的需要時 build：build 一秒多，但每次都跑會讓「跑一次測試」多一個副作用。
# ⚠ 沒有 npm 就照既有風格跳過**並講出來**，不可以靜靜地讓後面每一支都以「缺 dist」跳過。
_dist_stale=0
if [ ! -f server/static/dist/index.html ]; then
  _dist_stale=1
# ⚠ `server/static/css/app.css` 也要列進來：SPA 的樣式**不是**打包進 bundle 的，是
#   `index.html` 直接引用 `/static/css/app.css`（見 frontend/index.html）。它一改，畫面就變，
#   而 `frontend/` 底下一個字都沒動 —— 少了這一項，golden 會拿一份舊畫面去比新樣式
#   （完整審查 L3）。
elif [ -n "$(find frontend/src frontend/index.html frontend/package.json frontend/vite.config.ts \
              server/static/css/app.css \
              -newer server/static/dist/index.html -print -quit 2>/dev/null)" ]; then
  _dist_stale=1
fi
if [ "${_dist_stale}" -eq 1 ]; then
  if command -v npm >/dev/null 2>&1 && [ -f frontend/package-lock.json ]; then
    printf '\n\033[1m== dist 不在或比原始碼舊，先 build 一次 ==\033[0m\n'
    if (cd frontend && npm ci --no-audit --no-fund >/dev/null 2>&1 && npm run --silent build); then
      echo "  PASS  dist 是對應現在這份原始碼的了"
    else
      fails=$((fails + 1))
      echo "   ↑ 前端 build 失敗，vue 模式的瀏覽器測試會全部跳過"
    fi
  else
    skipped+=("前端 dist（host 上沒有 npm 或缺 lockfile，裝 node 24）")
  fi
fi

# ⚠ `golden_check.py` 要逐一列名，不能靠 glob：同一個目錄下的 `golden_record.py`（錄）
#   與 `golden_scenes.py`（場景定義）都不是測試，一條斷言都沒有，被撿走只會空跑。
#   `fake_ttyd.py`／`fake_gitlab.py` 同理，它們的檔名本來就避開了上面兩個 glob。
for f in tests/test_*.py tests/e2e_*.py tests/golden_check.py; do
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
