#!/bin/bash
# 重新部署控制平面（改了 server/ 之後跑這支）。
#
# 為什麼不直接 `docker compose up -d --build`：
#
# **已證實的部分**（2026-07-26 用 docker events 錄下來）：
#   reconciler 設了 `pid: "service:control"`，跑在 control 的 PID namespace 裡。那個
#   namespace 由 control 的 PID 1 持有，所以 **control 一停，namespace 就被拆掉，
#   reconciler 當場被連坐 SIGKILL（exit 137）**——它從頭到尾沒收到任何停止指令：
#     container kill  claude-pty-control-1
#     container die   claude-pty-control-1      exit=0
#     container stop  claude-pty-control-1
#     container die   claude-pty-reconciler-1   exit=137   ← 沒有人叫它停
#
#   共用 namespace 是刻意的：ttyd 跑在 control 容器內，reconciler 若有自己的 namespace，
#   `os.kill(pid, 0)` 一律看不到那些 pid，會把還活著的 view 記錄全部誤刪（2026-07-25 實測）。
#
# **只是觀察到、沒能重現的部分**：
#   同一天有 3 次 `up -d --build`（都是 server/ 真的改過、image 有變）中途失敗：compose 把
#   reconciler 改名成 `<hash>_claude-pty-reconciler-1` 之後要去停它，daemon 回
#   `cannot stop container: ... is not running`，compose 於是中止整個 up——control 停在
#   Created 沒被啟動，nginx 找不到後端，對外就是 502，而那個改名的容器會留下來卡住下一次。
#
#   ⚠ 但**刻意用 `--force-recreate` 連跑 6 次一次都沒重現**。所以「compose 為什麼有時候
#     處理得了、有時候處理不了」並沒有查清楚，多半是停止順序的競態（compose 正常會先停
#     依賴方，但 reconciler 是被連坐死的，不是被它停的）。這裡不假裝知道原因。
#
#   這支做的事只有一件：**在 compose 動手之前先把 reconciler 收乾淨**，讓那個窗口根本
#   不存在。代價是每次重新部署 reconciler 一定會重建（本來也會）。
#
# ⚠ 這是繞過症狀不是根治。根治是讓 reconciler 不必共用 namespace（改用別的方式判定 ttyd
#   存活），那會動到 view 生命週期的判定邏輯——那條路踩過雷（誤刪所有 view），要動之前
#   先把 tests/test_view_lifecycle.py 補到守得住。
#
# 用法：
#   deploy/redeploy.sh              # 重建並啟動（改了 server/ 之後用這個）
#   deploy/redeploy.sh --no-build   # 只重啟，不重新 build（改 nginx.conf 之類）
#   deploy/redeploy.sh --force      # 強制重建容器（config 沒變但要重來一次時）
set -euo pipefail

cd "$(dirname "$0")"

FLAGS="--build"
case "${1:-}" in
  --no-build) FLAGS="" ;;
  --force)    FLAGS="--build --force-recreate" ;;
  "")         ;;
  *)          echo "不認得的參數：${1}（只收 --no-build / --force）" >&2; exit 2 ;;
esac

# 這一版是哪個 commit——烘進 image 供頁尾顯示。
# ⚠ **只能在這裡算。** 程式是 COPY 進 image 的，`.git` 不在 build context 裡（context 是
#   claude-pty/，而 .git 在上一層），容器內也沒有 git 執行檔，執行期問不到自己是哪一版。
# ⚠ 工作區髒掉要標 `-dirty`。不標的話頁尾會宣告一個它其實沒有在跑的 commit——而頁尾唯一的
#   用途就是回答「線上是哪一版」，在那裡說謊比留白糟得多。
# ⚠ 問不到就留空，**不要退回一個看起來合理的值**。空的會讓頁尾顯示「commit 未知」並在
#   tooltip 講原因（例如這份不是從 git 工作區 build 的）。
if git rev-parse --git-dir >/dev/null 2>&1; then
  GIT_SHA="$(git rev-parse --short HEAD)"
  # ⚠ 髒不髒要對著**build context**問，不是對著這個 cwd。腳本第一件事是 `cd deploy`，
  #   所以 `git diff -- .` 只看得到 deploy/ 底下的檔案——改了 server/ 之後跑這支，image
  #   裡是新程式，頁尾卻標一個乾淨的 SHA。那正是頁尾唯一要避免的事（交叉審查
  #   2026-07-27 指出）。context 是 compose 的 `context: ..`＝claude-pty/。
  # ⚠ 用 `git status --porcelain` 不用 `git diff`：後者看不到**未追蹤**的檔案，而未追蹤的
  #   檔案照樣會被 COPY 進 image。
  # ⚠ 只問 claude-pty/，不問整個 monorepo：別的目錄改了跟這包 image 無關。
  if [ -n "$(git status --porcelain --untracked-files=normal -- .. 2>/dev/null)" ]; then
    GIT_SHA="${GIT_SHA}-dirty"
  fi
else
  GIT_SHA=""
  echo "⚠ 不在 git 工作區，頁尾的 commit 會顯示「未知」。"
fi
# 建置時間：**每次 build 都重取**。它回答的是「線上這包是什麼時候建出來的」——同一個
# commit 可以在任何時候被重新打包，所以不能拿 commit 時間頂替。
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# host 的作業系統。**控制平面在容器裡問不到這件事**——`sys.platform` 永遠是 linux，講的是
# 容器不是 host（ADR 0009 的「路徑解耦」是同一類問題，只是那時只認出了路徑）。它決定
# preflight 的 APP_UID 檢查要不要喊：只有 Linux 的 bind mount 會原樣把 uid 帶過去。
# ⚠ 用 `uname -s` 不用 `$OSTYPE`：後者是 bash 專屬，而這支是 `#!/bin/bash` 沒錯，但值的
#   形式（`darwin24` 之類）也沒有標準。`uname -s` 到處都有、輸出穩定。
# ⚠ shell 的環境變數**優先於 `.env`**，所以這一行會蓋掉使用者在 .env 裡手填的值。那是對的
#   方向：`uname` 問到的是事實，手填的可能是上一台機器留下來的。
CLAUDE_PTY_HOST_PLATFORM="$(uname -s)"
export GIT_SHA BUILT_AT CLAUDE_PTY_HOST_PLATFORM

# 有人正開著終端的話先講一聲：重建會拆掉 control 的 PID namespace，裡面的 ttyd 全部跟著死。
# session 容器本身不受影響（它們是獨立容器），使用者重開網頁就會起一個新的 ttyd。
LIVE="$(docker compose exec -T control python - <<'PY' 2>/dev/null || true
import sys
sys.path.insert(0, "/app")
from server.db import session_scope
from server.models import View
with session_scope() as s:
    print(s.query(View).count())
PY
)"
# ⚠ 只留數字。問不到（首次部署時 control 根本還沒起來、或它正好掛了）會是空字串，
#   那種情況要**直接往下做**——把「查不到」當成「有終端開著」的話，第一次部署就會停在
#   一個沒有意義的確認提示前面。
LIVE="$(printf '%s' "${LIVE}" | tr -dc '0-9')"
if [ -n "${LIVE}" ] && [ "${LIVE}" != "0" ]; then
  echo "⚠ 目前有 ${LIVE} 個開著的終端，重建會把它們斷線（session 本身繼續跑，重開網頁即可）。"
  printf '要繼續嗎？[y/N] '
  read -r ans
  case "${ans}" in [yY]*) ;; *) echo "已取消。"; exit 1 ;; esac
fi

# bind mount 的來源目錄**一定要我們先建**。不存在的話 dockerd 會替你建，而它是 root——
# 控制平面以 APP_UID 執行，於是一個字都寫不進去：per-user 空間建不出來，每一次建立 session
# 都失敗（2026-07-29 實測踩到，錯誤出現在很後面，看起來像應用層的 bug）。
# README 的第 1 步有寫要手動建，但「記得照文件做」不是防線——這裡無條件補一次。
# ⚠ **優先序要與 compose 一致：shell 環境變數優先於 .env。** 這裡原本只讀 .env，
#   而 compose 對 `${CLAUDE_PTY_SPACE:-…}` 是 env 優先——使用者 export 過的話，
#   腳本會去建 .env 指的那個目錄並對它做可寫檢查（通過），compose 卻掛 env 指的
#   那個（不存在）→ dockerd 以 root 建出來，正是下面整段註解在防的症狀，而防線
#   從這裡被繞過（審查 F-022）。同一支腳本上面才剛為 HOST_PLATFORM 寫下這個優先序。
SPACE_FROM_FILE="$(sed -n 's/^[[:space:]]*CLAUDE_PTY_SPACE=//p' .env 2>/dev/null | tail -1 | tr -d '"'\''')"
SPACE="${CLAUDE_PTY_SPACE:-${SPACE_FROM_FILE}}"
SPACE="${SPACE:-${HOME}/claude-pty-space}"
# ⚠ `~` 在 .env 裡不會被展開（compose 不展、Python 也不展），照著建會真的長出一個名字叫
#   `~` 的目錄，而且完全無聲。與其建錯，不如在這裡停下來。
case "${SPACE}" in
  /*) ;;
  *) echo "✗ .env 的 CLAUDE_PTY_SPACE 必須是絕對路徑（現在是「${SPACE}」）。" >&2; exit 1 ;;
esac
# 只在**建立時**給 700；已經存在的不動它的權限——那是使用者的目錄，不是這支腳本的。
mkdir -p -m 700 "${SPACE}" "${HOME}/.claude-pty"
mkdir -p data
for d in "${SPACE}" "${HOME}/.claude-pty" data; do
  if [ ! -w "${d}" ]; then
    # `ls -ld | awk` 而不是 `stat`：GNU 與 BSD 的 stat 旗標不同，這支兩個平台都要能跑。
    echo "✗ ${d} 不可寫（擁有者是 $(ls -ld "${d}" | awk '{print $3}')）。" >&2
    echo "  多半是先前 dockerd 替你建的（root）。移除或改擁有者後再跑一次。" >&2
    exit 1
  fi
done

# --- session image 的 uid 對不對得上（ADR 0017）------------------------------------
#
# 三個數字要相同：① 你（`id -u`）、② APP_UID、③ session image 裡的 nathan。
# ③ 是 build 時烤進去的，而**直接 `docker build` 不會失敗**，只會安靜地給預設值 1001。
#
# ⚠ 為什麼擋在這裡而不是只靠 preflight：preflight 是**控制平面起來之後**才喊，那時你
#   已經離開鍵盤了；而這裡你正在打指令，訊息能直接告訴你下一句該打什麼。preflight 那道
#   留著當第二層（它涵蓋「沒經過這支腳本」的部署）。
# ⚠ 只在 host 是 Linux 時檢查——Docker Desktop 做 uid 對映，在那邊喊是純噪音。
if [ "${CLAUDE_PTY_HOST_PLATFORM}" = "Linux" ]; then
  SESSION_IMAGE="${CLAUDE_PTY_IMAGE:-ncr-dev-container}"
  IMG_UID="$(docker image inspect -f '{{index .Config.Labels "ncr.uid"}}' \
             "${SESSION_IMAGE}" 2>/dev/null || true)"
  MY_UID="$(id -u)"
  APP_UID_EFF="${APP_UID:-$(sed -n 's/^[[:space:]]*APP_UID=//p' .env 2>/dev/null | tail -1)}"
  if [ -z "${IMG_UID}" ] || [ "${IMG_UID}" = "<no value>" ]; then
    # image 不在、或是改版前 build 的（沒有 stamp）。不擋——你可能正要第一次部署，
    # 而 session image 不是控制平面起得來的前提。但要講清楚它沒被驗過。
    echo "⚠ 沒能查證 session image「${SESSION_IMAGE}」裡的 uid（image 不在，或是加上"
    echo "  NCR_UID 標記之前 build 的）。要驗得到請重建：dev-container/build.sh"
  elif [ "${IMG_UID}" != "${MY_UID}" ] || [ "${APP_UID_EFF}" != "${MY_UID}" ]; then
    echo "✗ uid 沒有對齊，先修好再部署（Linux 的 bind mount 不做 uid 翻譯）：" >&2
    echo "    你（id -u）                 ${MY_UID}" >&2
    echo "    .env 的 APP_UID             ${APP_UID_EFF:-（未設）}" >&2
    echo "    image ${SESSION_IMAGE} 的 nathan   ${IMG_UID}" >&2
    echo "  三個要相同。做法：" >&2
    echo "    在 .env 設 APP_UID=${MY_UID}" >&2
    echo "    重建 session image：  ../../dev-container/build.sh" >&2
    echo "  已經用舊 uid 開過場的話，那些狀態目錄也要一起搬：" >&2
    echo "    chown -R ${MY_UID}:$(id -g) \"${SPACE}\"" >&2
    echo "    docker volume rm ncr-trivy-cache" >&2
    echo "    docker compose exec control rm -f /data/trivy-db-updated-at" >&2
    exit 1
  else
    echo "🔢 uid 對齊：你 / APP_UID / image 都是 ${MY_UID}"
  fi
fi

# ⚠ 先收 reconciler，理由見檔頭。-s 送停止訊號、-f 不再問一次。
# 這裡不用 `|| true`：rm 失敗代表狀態比預期更亂，那時候繼續 up 只會把問題蓋掉。
docker compose rm -sf reconciler >/dev/null

# 保險：撿掉之前失敗的 up 留下的改名殘留容器（`<hash>_claude-pty-reconciler-1`）。
# 它們不在 compose 的管轄範圍內，compose 自己清不掉。
STALE="$(docker ps -aq --filter 'name=_claude-pty-reconciler-1' || true)"
if [ -n "${STALE}" ]; then
  echo "清掉先前失敗留下的殘留容器…"
  docker rm -f ${STALE} >/dev/null
fi


# ⚠ **不要帶服務名**。只 up control 的話 reconciler 不會被重建，它會停在死掉的狀態
# （它的 namespace 目標已經換人了）。
# shellcheck disable=SC2086
docker compose up -d ${FLAGS}

echo "等待控制平面就緒…"
for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/login || true)"
  if [ "${code}" = "200" ]; then
    echo "✓ 已就緒（登入頁 200）"
    docker compose ps --format '  {{.Name}}\t{{.Status}}'
    exit 0
  fi
  sleep 1
done

echo "✗ 30 秒內沒有就緒。目前狀態："
docker compose ps
echo
echo "看 log：docker compose logs --tail=50 control"
exit 1
