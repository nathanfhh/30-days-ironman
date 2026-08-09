#!/usr/bin/env bash
# 容器啟動入口：報告 image 資訊、確認憑證在場、決定網路能力，然後把控制權交給指令。
# 預設指令是 claude --dangerously-skip-permissions：
# 容器本身就是隔離層，煞車在牆上，不在每一次的允許提問裡。
set -euo pipefail

echo "📦 ncr-dev-container｜image built: $(cat /etc/image-build-time 2>/dev/null || echo unknown)"

# 憑證：網頁版把 token 放在一個檔案裡（NCR_TOKEN_FILE，由控制平面 put_archive 進來），
# 這裡把它變成一個**已開的 file descriptor** 再交給 CLI。
#
# ⚠ 為什麼不把檔案內容直接指派給那個「裸的」token 環境變數：那個值會進到環境，於是
#   （這裡刻意不把那行反例寫成可執行的樣子——test_profile_mapping 用子字串守「entrypoint
#     不准賦值它」，而一行貼著就能取消註解的危險寫法，本來就不該留在檔案裡）
#   `/proc/1/environ` 讀得到，而且 CLI 底下每一個子行程都繼承它——CLI 會開 shell，shell
#   會執行 AI 要求的任何指令。CLI 自己在 spawn 子行程前就把這幾個憑證變數從環境刪掉，
#   我們不該在外面一層又加回去。走 fd 只有一個數字進環境。
# ⚠ `rm` 排在 `exec 4<` **之後**：fd 已經開著，檔案就可以立刻從檔案系統消失，而讀取照樣
#   成立（實測驗過）。少一個躺在那裡等人 `cat` 的檔案。
# ⚠ 這一段必須跑在任何 `exec` 或子行程**之前**，fd 才傳得下去。
# ⚠ `rm` 失敗**不可以**弄死這一場。unlink 要的是父目錄的寫權限，而那是控制平面那側決定
#   的事；哪天它換了落點、權限不對，症狀會是整個容器 exit 1（`set -e`），看起來像
#   session 建不起來，而真正的原因是一行清理指令（2026-08-07 實測踩到）。所以吞掉錯誤，
#   但**要吵**：檔案還在是一個少掉的保障，不是沒事。
if [ -n "${NCR_TOKEN_FILE:-}" ] && [ -s "${NCR_TOKEN_FILE}" ]; then
    exec 4< "${NCR_TOKEN_FILE}"
    export CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR=4
    rm -f "${NCR_TOKEN_FILE}" 2>/dev/null \
        || echo "⚠️  憑證檔刪不掉（${NCR_TOKEN_FILE} 仍在容器內），fd 已經讀到、這一場照常。"
fi

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] \
   && [ -z "${CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR:-}" ] \
   && [ ! -s "$HOME/.claude/.credentials.json" ]; then
    echo "⚠️  容器內沒有憑證（環境變數、fd 或 ~/.claude/.credentials.json），claude 會要求登入。"
fi

# ------------------------------------------------------------------------------
# 網路能力
#
# 這一題在 CLI 啟動**之前**問，而且只有坐在鍵盤前的人答得到——agent 還沒開始跑，
# 沒有任何東西能替它自己選。這跟 Claude Code 的權限模式是不同層的兩件事：
# 權限模式決定「要不要問你」，這道牆決定「能不能出去」。一個被批准執行的 curl，
# 打不打得出去是另一個問題。
#
# 「完全開放」不是沒想清楚才留的後門。要做研究、要讓 WebFetch 或瀏覽器自動化真的
# 連得出去時，限制模式會擋住它們——那些場合本來就不該在限制模式下硬幹。
# 重點是這個選擇必須是人做的、是每一場都要重新做的，而且畫面上看得見選了哪個。
# ------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
# 流量錄製（mitmproxy L7 capture）
#
# 加密的流量看不到內容，要看就得在中間解開再送出去——這就是 L7 proxy 在做的事，
# 也是「中間人」這個詞的字面意思。能做得到，是因為 Claude Code 收外部 proxy、
# 也收自訂的根憑證；它大可以把憑證寫死，那這條路就永遠不通。
#
# 三道界線：
#   - CA 每一場在容器內現產（~/.mitmproxy 不持久化），炸開的範圍就是這個容器。
#   - 落地的是脫敏過的副本，addon fail-closed：脫敏出錯就整條丟掉，
#     絕不改寫成未脫敏版落地；addon 不在就整場不錄，而不是退回錄原始流量。
#   - 範圍由人在啟動時選，**預設全錄**；選「只錄模型 API」時其餘 host 直連、不走 proxy。
#     選了什麼會寫進 meta.json，報表據此揭露——端點表看起來像「連過的全部」，
#     實際上是「過濾器讓我看到的那些」，不記下來讀報表的人分不出這兩件事。
#
# 網頁 UI 顯示的是記憶體裡的即時 flow（未脫敏），只綁本機、有 token。
# 落到磁碟的永遠只有脫敏版，兩者是分開的。
# ------------------------------------------------------------------------------
# run wrapper 從它自己所在的 repo 把 mitm/ 掛到這裡（:ro）。刻意不用
# /home/nathan/code-review/mitm——那個掛載點是「被審查的專案」，不是這個 repo。
CAPTURE_ADDON="/home/nathan/ncr-mitm/capture_addon.py"
CAPTURE_DIR="$HOME/ncr/mitm"
CAPTURE_PROXY_HOST="127.0.0.1"
CAPTURE_PROXY_PORT="8880"
CAPTURE_WEB_PORT="8081"          # 容器內固定；host 那側由 run script 動態挑，避免多開時撞號
# mitmweb UI 綁在容器內的哪個介面。
#
# 預設 `0.0.0.0`：人自己開容器時，run script 用 `-p 127.0.0.1:<port>:8081` 把它發布到
# host 的 loopback，而 docker 的 port forwarding 連的是**容器內的介面**——綁容器
# loopback 的話那條路就通不了，UI 直接打不開。
#
# ⚠ 但**加入了自訂 network 的容器不一樣**：同一個網段上的兄弟容器連得到 8081，而那個
#   UI 顯示的是**未脫敏的即時流量**（token 又印在 `docker logs` 裡）。那種部署要把它
#   收回容器 loopback：`NCR_MITM_WEB_BIND=127.0.0.1`。UI 仍然開得起來（在容器內），
#   token 也照印——只是拿到 token 的人得先進得了這個容器。
CAPTURE_WEB_BIND="${NCR_MITM_WEB_BIND:-0.0.0.0}"
CAPTURE_HOSTS=""            # 空字串＝全錄；選單第二題可收斂成只錄模型 API
CAPTURE_MODEL_HOSTS="api.anthropic.com"
CAPTURE_CA="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
CAPTURE_ON=0
CAPTURE_PID=""
CAPTURE_FILE=""
CAPTURE_SESSION_DIR=""
CAPTURE_STARTED=""

# 這一場的 session id 由我們指定，不是事後去猜。
#
# 先前的做法是收工時撈「capture 開始之後才被改到的那顆 transcript」，那是猜的：
# 同時開兩個容器、或 host 上剛好也在跑，就會對到別人。改成開場自己產一個 uuid 餵給
# CLI（`--session-id`），流量、防火牆計數、transcript 三份資料從此共用同一個確定的 id。
#
# uuid 直接讀 /proc（Linux 一定有），不為了一個亂數多拉一個 python 或 uuidgen 相依。
NCR_SESSION_ID="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || true)"
NCR_INJECT_SESSION=0

start_capture() {
    [ "$CAPTURE_ON" = "1" ] || return 0

    if [ ! -f "$CAPTURE_ADDON" ]; then
        # fail closed：沒有脫敏 addon 就不錄，而不是錄一份沒掃過的原始流量。
        echo "⚠️  找不到脫敏 addon（${CAPTURE_ADDON}）——本場不錄。"
        echo "   （run wrapper 應該把它所在 repo 的 mitm/ 掛到 /home/nathan/ncr-mitm）"
        return 0
    fi

    # 一場一個資料夾，資料夾名就是 session id。流量、防火牆計數、環境三份東西
    # 住在一起，不必靠檔名前綴去配對；產不出 uuid 時退回時間戳當目錄名。
    CAPTURE_SESSION_DIR="${CAPTURE_DIR}/${NCR_SESSION_ID:-$(date +%Y%m%dT%H%M%S)}"
    mkdir -p "$CAPTURE_SESSION_DIR"
    CAPTURE_FILE="${CAPTURE_SESSION_DIR}/flows.mitm"
    CAPTURE_STARTED=$(date -Iseconds)
    local flow_file="$CAPTURE_FILE"
    local token
    # 不在尾巴再接一個 head：提前關掉管線會讓上游收到 SIGPIPE，在 pipefail 下
    # 整個 command substitution 回 141。取夠長的亂數再用參數展開切長度。
    token=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
    token="${token:0:24}"

    # 用 mitmweb 而不是 mitmdump，是為了那個網頁 UI——錄的當下就看得到，
    # 不必等收工後才知道錄到了什麼。
    # 不加 -w：內建的 save addon 排在我們前面，會先把未脫敏的原始 flow 寫出去。
    # store_streamed_bodies：SSE 的 body 要靠它才會在 response hook 就位。
    nohup mitmweb -q \
        --listen-host "$CAPTURE_PROXY_HOST" --listen-port "$CAPTURE_PROXY_PORT" \
        --no-web-open-browser \
        --web-host "$CAPTURE_WEB_BIND" --web-port "$CAPTURE_WEB_PORT" \
        --set web_password="$token" \
        --set store_streamed_bodies=true \
        -s "$CAPTURE_ADDON" \
        --set capture_out="$flow_file" \
        --set capture_hosts="$CAPTURE_HOSTS" \
        > /tmp/mitm-capture.log 2>&1 &
    CAPTURE_PID=$!

    # 首跑要現產 CA，給到 20 秒。中途 proxy 就死掉（例如 port 被占）就別空等。
    local ready=0
    for _ in $(seq 1 40); do
        if [ -f "$CAPTURE_CA" ] && timeout 1 bash -c \
                "exec 3<>/dev/tcp/${CAPTURE_PROXY_HOST}/${CAPTURE_PROXY_PORT}" 2>/dev/null; then
            ready=1; break
        fi
        kill -0 "$CAPTURE_PID" 2>/dev/null || break
        sleep 0.5
    done
    if [ "$ready" != "1" ]; then
        # 優雅降級：錄不成不該讓整場開不了工。
        echo "⚠️  mitmproxy 沒起來，本場不錄，CLI 照常啟動（細節：/tmp/mitm-capture.log）"
        kill "$CAPTURE_PID" 2>/dev/null || true
        CAPTURE_PID=""
        CAPTURE_FILE=""
        rm -f "$flow_file" 2>/dev/null || true   # 清掉啟動時建的 0-byte 殘檔
        return 0
    fi

    export HTTPS_PROXY="http://${CAPTURE_PROXY_HOST}:${CAPTURE_PROXY_PORT}"
    export HTTP_PROXY="$HTTPS_PROXY"
    export https_proxy="$HTTPS_PROXY" http_proxy="$HTTPS_PROXY"   # curl/wget 只認小寫
    # Node 不吃作業系統的信任庫，它自己帶一份根憑證清單——所以要單獨餵給它。
    # 這也是為什麼不必把 CA 裝進 OS：只有這個 process 信，範圍最小。
    export NODE_EXTRA_CA_CERTS="$CAPTURE_CA"
    # 非 Node 的客戶端（Bash tool 裡的 curl/git、python requests）走同一個 proxy，
    # 也會收到 mitm 簽的憑證。給它們「系統 CA ＋ mitm CA」串起來的 bundle——
    # 不能只指 mitm CA，那會變成「只」信它，連正常直連的 TLS 都會壞。
    local bundle=/tmp/mitm-ca-bundle.crt
    if cat /etc/ssl/certs/ca-certificates.crt "$CAPTURE_CA" > "$bundle" 2>/dev/null; then
        export SSL_CERT_FILE="$bundle" REQUESTS_CA_BUNDLE="$bundle" \
               CURL_CA_BUNDLE="$bundle" GIT_SSL_CAINFO="$bundle"
    fi
    if [ -z "$CAPTURE_HOSTS" ]; then
        # 全錄：連我沒預料到的客戶端也要能被錄到，那就只能讓它們信任系統信任庫。
        if sudo /usr/local/bin/trust-mitm-ca.sh 2>/dev/null; then
            echo "● CA 已裝進容器的系統信任庫（用完即丟，憑證不持久化）"
        else
            echo "⚠️  CA 裝不進系統信任庫，只有吃環境變數的客戶端會信任它（image 需重建）"
        fi
        # jaeger 仍排除：OTLP 走 gRPC，HTTP proxy 夾在中間只會讓 telemetry 送不出去，
        # 錄也錄不到有用的東西。這個例外會寫進 meta.json，報表據此揭露。
        export NO_PROXY="jaeger,localhost,127.0.0.1,::1"
    else
        # 只錄模型 API：內部流量不必繞經 proxy，proxy 也就不在工作的關鍵路徑上。
        export NO_PROXY="gitlab-proxy,jaeger,localhost,127.0.0.1,::1"
    fi
    export no_proxy="$NO_PROXY"

    # 印 host 視角的路徑（run wrapper 傳進來的）。直接跑容器、沒有 wrapper 時才退回
    # 容器內路徑——那種情況下看的人本來就在容器裡。
    local shown="${NCR_CAPTURE_HOST_DIR:-$CAPTURE_DIR}/$(basename "$CAPTURE_SESSION_DIR")/"
    echo "● 錄製中 → ${shown}flows.mitm"
    echo "● 即時畫面 → http://localhost:${NCR_MITM_WEB_PORT:-$CAPTURE_WEB_PORT}/?token=${token}"
    echo "  （畫面上是未脫敏的即時內容，host 側只綁本機且要 token；落地的是脫敏版）"
}

# 收工時把這一場的環境寫在 capture 旁邊。
#
# 為什麼要寫：capture 本身不知道自己錄的是哪一場、在什麼網路模式下錄的，多錄幾場
# 之後只能靠檔名的時間戳去猜。報表要能自己把這些接起來，就得有人在收工時留下來。
#
# 防火牆計數也在這裡收。容器是 --rm 的，規則從套用那一刻起算，所以收工時的數字
# 就是這一場的總量：放行了多少、擋掉了多少，各條規則分開記。**擋掉的那一條才是
# 重點**——沒送出去的東西不會出現在任何 L7 紀錄裡，只有這裡看得到。
write_capture_sidecar() {
    [ -n "$CAPTURE_FILE" ] && [ -f "$CAPTURE_FILE" ] || return 0
    local dir="$CAPTURE_SESSION_DIR"

    if [ "$mode" = "restricted" ]; then
        if sudo /usr/local/bin/firewall-counters.sh > "${dir}/firewall.txt" 2>/dev/null; then
            local rejected
            rejected=$(awk '/REJECT|DROP/ {sum += $1} END {print sum + 0}' \
                       "${dir}/firewall.txt")
            echo "● 防火牆這一場擋下 ${rejected} 個封包（明細：firewall.txt）"
        else
            # 舊 image 沒有這支腳本或 sudoers 沒授權時，如實留白，不要假裝有量到。
            rm -f "${dir}/firewall.txt" 2>/dev/null || true
            echo "⚠️  取不到防火牆計數（image 需重建以取得 firewall-counters.sh）"
        fi
    fi

    # session id 是開場指定給 CLI 的那一個，不是猜的。產不出 uuid 的環境（沒有
    # /proc）會留空，那時寧可空著也不要瞎填一個對不上的。
    local sid="$NCR_SESSION_ID"

    # capture_hosts 一定要記：報表的端點表看起來像「這一場連過的全部」，
    # 但它其實是「過濾器讓我看到的那些」。不記，讀報表的人無從分辨。
    printf '{\n  "capture": "%s",\n  "started": "%s",\n  "ended": "%s",\n  "network": "%s",\n  "telemetry": "%s",\n  "capture_hosts": "%s",\n  "session_id": "%s"\n}\n' \
        "$(basename "$CAPTURE_FILE")" "$CAPTURE_STARTED" "$(date -Iseconds)" \
        "$mode" "${OTEL_EXPORTER_OTLP_ENDPOINT:+on}" "${CAPTURE_HOSTS:-（全部，jaeger 除外）}" "${sid:-}" \
        > "${dir}/meta.json"
}

# CLI 收工後收掉 mitmproxy：SIGTERM 走正常關閉（跑完 addon 的 done()、把檔案 flush
# 並關起來）→ 最多等 5 秒 → 還活著才 SIGKILL。
stop_capture() {
    [ -n "$CAPTURE_PID" ] && kill -0 "$CAPTURE_PID" 2>/dev/null || return 0
    echo "收尾 mitmproxy capture..."
    kill -TERM "$CAPTURE_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$CAPTURE_PID" 2>/dev/null || return 0
        sleep 0.25
    done
    kill -KILL "$CAPTURE_PID" 2>/dev/null || true
}

# 沒開錄製就維持 exec（行為與加這段之前完全一樣）。開了就不能 exec：
# exec 讓 CLI 接管 PID 1，CLI 一退出容器立刻拆掉，背景的 mitmproxy 被 SIGKILL，
# addon 的 done() 來不及跑，最後那幾條 flow 就沒了。
# 只有在「跑的確實是 claude、而且呼叫端沒有自己指定 session」時才注入。
# --resume / --continue 帶著既有的 session 進來，硬塞一個新 id 會直接衝突。
#
# 名字比對收成 `claude` 或 `*/claude`：`*claude` 會連 `myclaude` 一起吃掉，
# 對那些名字硬塞旗標只會讓它起不來。
inject_session_id() {
    local arg
    [ -n "$NCR_SESSION_ID" ] || return 1
    case "$1" in claude|*/claude) ;; *) return 1 ;; esac
    for arg in "$@"; do
        # `=value` 的寫法也要認。只比對裸旗標的話，`--resume=abc` 不匹配，
        # 我們照樣塞一個 --session-id 進去，CLI 收到兩個衝突的 session 旗標。
        case "$arg" in
            --session-id|--session-id=*|--resume|--resume=*|-r|-r=*|\
            --continue|--continue=*|-c|--fork-session) return 1 ;;
        esac
    done
    return 0
}

# 這一場的 session id 定案。**必須在錄製開始之前跑**——capture 的資料夾就是拿它
# 命名的，等到啟動 CLI 才決定就來不及了。
#
# 呼叫端自己帶了 session 時，附檔要記那一顆，不能記我們生成卻沒被用到的 uuid。
# 記錯的後果不是少一個欄位，是 meta.json 宣告了一個對不到任何 transcript 的 id，
# 事後拿它去撈成本或場次報表會撈到空的，而且看起來像資料遺失。
resolve_session_id() {
    local prev=""
    if inject_session_id "$@"; then
        NCR_INJECT_SESSION=1
        return
    fi
    NCR_INJECT_SESSION=0
    for arg in "$@"; do
        case "$arg" in
            --session-id=*|--resume=*) NCR_SESSION_ID="${arg#*=}"; return ;;
        esac
        case "$prev" in
            --session-id|--resume|-r)
                case "$arg" in -*) ;; *) NCR_SESSION_ID="$arg"; return ;; esac ;;
        esac
        prev="$arg"
    done
    # --continue 或不帶值的 --resume：id 由 CLI 自己挑，我們無從得知。留空，
    # 資料夾退回用時間戳命名。
    NCR_SESSION_ID=""
}

run_cli() {
    if [ "$NCR_INJECT_SESSION" = "1" ]; then
        set -- "$1" --session-id "$NCR_SESSION_ID" "${@:2}"
    fi
    if [ -z "$CAPTURE_PID" ]; then
        exec "$@"
    fi
    # 背景跑 CLI ＋ trap：開錄之後 PID 1 是這支 bash，而 bash 身為 PID 1 在沒有
    # trap 的情況下會忽略 SIGTERM。`docker stop` 因此在寬限期後直接 SIGKILL 全滅，
    # 收尾整段跳過——meta.json 與 firewall.txt 一定不會有。接住它，把訊號轉給 CLI，
    # 讓正常收尾在 docker stop 這條路徑上也走得到。
    set +e
    # `<&0` 不可省。非互動 shell 沒有 job control，背景命令的 stdin 依 POSIX 會被
    # 指到 /dev/null——CLI 從此一個按鍵都收不到，畫面看起來就是終端死了。
    # 用 `-p` 測不出來（headless 不讀 stdin），要互動才會現形。
    "$@" <&0 &
    local child=$!
    trap 'kill -TERM "$child" 2>/dev/null' TERM INT
    wait "$child"
    local rc=$?
    trap - TERM INT
    stop_capture
    write_capture_sidecar
    exit "$rc"
}

echo ""
echo "網路能力："
echo "  1 = 限制（白名單） — 只通 api.anthropic.com、直連的 docker 網段（gitlab-proxy），"
echo "                       SSH 22 只通 build 時指定的那台 GitLab（預設）"
echo "  2 = 完全開放       — 不套用任何 iptables 規則"
echo ""

# 非互動環境（CI、腳本）用 NCR_NET 跳過選單。沒設就一定要有人回答。
if [ -n "${NCR_NET:-}" ]; then
    case "$NCR_NET" in
        restricted)   choice=1 ;;
        unrestricted) choice=2 ;;
        *)            choice="$NCR_NET" ;;
    esac
    echo "● 非互動：網路 = ${NCR_NET}"
else
    read -r -p "請選擇 [1]: " choice
fi
choice="${choice:-1}"

case "$choice" in
    2) mode="unrestricted" ;;
    1) mode="restricted" ;;
    # 看不懂的輸入一律落到比較嚴的那邊，並且說出來。
    # 靜默當成「開放」就是把手滑變成沒有牆。
    *) echo "無效輸入「${choice}」，套用預設（限制白名單）"; mode="restricted" ;;
esac

if [ "$mode" = "unrestricted" ]; then
    echo "● 網路能力：完全開放 — 未套用任何規則"
else
    echo "套用 firewall 中..."
    # 無參數呼叫：sudoers 只允許這一種形式（見 Dockerfile）。
    if ! sudo /usr/local/bin/init-firewall.sh > /tmp/firewall.log 2>&1; then
        echo "❌ Firewall 啟用失敗，不啟動 CLI："
        cat /tmp/firewall.log
        # fail closed：牆沒起來就不要放 agent 進來。
        # 「規則套用失敗所以先開著跑」是這類腳本最常見的錯誤結尾。
        exit 1
    fi
    echo "● 網路能力：限制白名單 — firewall 已生效（細節：/tmp/firewall.log）"
fi

echo ""
echo "錄製本場流量？（mitmproxy）"
echo "  y = 錄，落在 ~/ncr/mitm/<session-id>/（脫敏後）"
echo "  n = 不錄（預設）"
echo ""
# 非互動環境用 NCR_CAPTURE（1|0|y|n）跳過選單。看不懂的輸入落到保守側（不錄）
# 並且說出來——把 NCR_CAPTURE=true 靜默當成「錄」，就是在沒有人同意的情況下開錄。
# 預設是不錄，理由同上：錄製要有人明確答應，不是預設行為。
if [ -n "${NCR_CAPTURE:-}" ]; then
    case "$NCR_CAPTURE" in
        1|y|Y) cap_choice=y ;;
        0|n|N) cap_choice=n ;;
        *)     echo "無效輸入「${NCR_CAPTURE}」，本場不錄"; cap_choice=n ;;
    esac
    echo "● 非互動：錄製 = ${cap_choice}"
else
    read -r -p "錄製流量? [y/N]: " cap_choice
fi
case "${cap_choice:-n}" in
    [Yy]*) CAPTURE_ON=1 ;;
    *)     CAPTURE_ON=0; echo "● 本場不錄流量" ;;
esac

# 要錄，才問範圍。
#
# 預設全錄，因為這份紀錄的用途之一是回答「有沒有東西走漏」——一份只錄了自己
# 允許的那一條的紀錄，拿來說「沒有別的」是循環論證。
#
# 代價也在這裡講明：全錄要讓所有客戶端都信任 mitm 的憑證，所以憑證會裝進系統
# 信任庫（範圍是整台機器，但這台機器用完即丟、憑證每場現產）；而且 proxy 從此
# 在工作的關鍵路徑上，它掛了這一場就跟著掛。
if [ "$CAPTURE_ON" = "1" ]; then
    echo ""
    echo "錄製範圍："
    echo "  1 = 全部流量（預設） — 憑證裝進容器的系統信任庫，proxy 進關鍵路徑"
    echo "  2 = 只錄模型 API     — 只收 ${CAPTURE_MODEL_HOSTS}，其餘直連不經過 proxy"
    echo ""
    if [ -n "${NCR_CAPTURE_SCOPE:-}" ]; then
        case "$NCR_CAPTURE_SCOPE" in
            all|1)   scope_choice=1 ;;
            model|2) scope_choice=2 ;;
            *)       echo "無效輸入「${NCR_CAPTURE_SCOPE}」，套用預設（全部）"; scope_choice=1 ;;
        esac
        echo "● 非互動：錄製範圍 = ${scope_choice}"
    else
        read -r -p "請選擇 [1]: " scope_choice
    fi
    case "${scope_choice:-1}" in
        2) CAPTURE_HOSTS="$CAPTURE_MODEL_HOSTS"
           echo "● 錄製範圍：只錄 ${CAPTURE_HOSTS}" ;;
        *) CAPTURE_HOSTS=""
           echo "● 錄製範圍：全部流量" ;;
    esac
fi

# ------------------------------------------------------------------------------
# Telemetry → Jaeger
#
# 只在 run script 已配置（Jaeger 容器在跑＝OTLP endpoint 已注入 env）時才問；
# 未配置就沒有可送的對象，不出這題。問之前先探一次 endpoint 真的通不通——
# 牆已經套用，測的就是這一場實際會走的路徑；5 秒不通就直接不送、也不問，
# 免得答了 y 卻整場默默送不出去（OTLP 匯出 fail-open，不會有人告訴你）。
# 通了才問。跟網路能力同一個道理：要不要被記錄，是坐在鍵盤前的人每一場
# 重新做的選擇，不是環境替你決定的預設。
# ------------------------------------------------------------------------------
disable_telemetry() {
    unset CLAUDE_CODE_ENABLE_TELEMETRY CLAUDE_CODE_ENHANCED_TELEMETRY_BETA \
          OTEL_TRACES_EXPORTER OTEL_METRICS_EXPORTER OTEL_LOGS_EXPORTER \
          OTEL_EXPORTER_OTLP_PROTOCOL OTEL_EXPORTER_OTLP_ENDPOINT \
          OTEL_LOG_TOOL_DETAILS OTEL_RESOURCE_ATTRIBUTES
}
if [ -n "${OTEL_EXPORTER_OTLP_ENDPOINT:-}" ]; then
    _ep="${OTEL_EXPORTER_OTLP_ENDPOINT#*://}"; _ep="${_ep%%/*}"
    _host="${_ep%%:*}"; _port="${_ep##*:}"
    [ "$_port" = "$_host" ] && _port=4317
    if ! timeout 5 bash -c "exec 3<>/dev/tcp/${_host}/${_port}" 2>/dev/null; then
        echo ""
        echo "⚠️  Jaeger（${_host}:${_port}）5 秒內不通 → 本場不送 telemetry"
        disable_telemetry
    else
        echo ""
        echo "送 telemetry trace 到 Jaeger？（${OTEL_EXPORTER_OTLP_ENDPOINT}，已探通）"
        echo "  y = 送（預設）"
        echo "  n = 本場不送"
        echo ""
        # 非互動環境（CI、腳本）用 NCR_OTEL（1|0|y|n）跳過選單，同 NCR_NET 的姿勢：
        # 看不懂的輸入一律落到保守側（不送），並且說出來。這裡的保守側是「不送」——
        # 把 NCR_OTEL=true、NCR_OTEL=off、或尾巴多一個空白靜默當成「送」，
        # 就是在沒有人同意的情況下開錄。
        if [ -n "${NCR_OTEL:-}" ]; then
            case "$NCR_OTEL" in
                1|y|Y) otel_choice=y ;;
                0|n|N) otel_choice=n ;;
                *)     echo "無效輸入「${NCR_OTEL}」，本場不送 telemetry"; otel_choice=n ;;
            esac
            echo "● 非互動：telemetry = ${otel_choice}"
        else
            read -r -p "送 Jaeger? [Y/n]: " otel_choice
        fi
        otel_choice="${otel_choice:-y}"
        case "$otel_choice" in
            [Nn]*) disable_telemetry; echo "● 本場不送 telemetry" ;;
            *)     echo "● telemetry → Jaeger" ;;
        esac
    fi
fi

resolve_session_id "$@"
if [ -n "$NCR_SESSION_ID" ]; then
    if [ "$NCR_INJECT_SESSION" = "1" ]; then
        echo "● session id：${NCR_SESSION_ID}"
    else
        echo "● session id（呼叫端指定）：${NCR_SESSION_ID}"
    fi
fi

# 模型與思考深度：非互動呼叫端（CI、腳本、控制平面）用 NCR_MODEL / NCR_EFFORT 指定，
# 翻成 CLI 的 `--model` / `--effort`。沒設就一個字都不加——人自己開容器時 argv 與
# 以前逐字元相同。
#
# ⚠ **一定要在 `resolve_session_id "$@"` 之後才 append**（上面那一行）。那支會**掃描
#   argv 的內容**認 `--session-id` / `--resume`，而模型名是使用者給的字串：一個值剛好
#   是 `--resume` 的 `NCR_MODEL` 會被它讀成「呼叫端自帶 session」，於是這一場的
#   capture 資料夾用了一個不存在的 id 命名，事後拿它去對 transcript 會對到空的。
#   先定案 session id、再加旗標，那條掃描就永遠看不到我們加的東西。
# ⚠ 值不做白名單：合法別名隨 CLI 版本變，寫死一份在這裡只會過期。給錯的話 CLI 自己
#   會拒絕並印出它認得的值——那比我們猜一份清單準。
if [ -n "${NCR_MODEL:-}" ]; then
    set -- "$@" --model "$NCR_MODEL"
    echo "● 非互動：模型 = ${NCR_MODEL}"
fi
if [ -n "${NCR_EFFORT:-}" ]; then
    set -- "$@" --effort "$NCR_EFFORT"
    echo "● 非互動：思考深度 = ${NCR_EFFORT}"
fi

start_capture

# 就緒標記：**只有非互動呼叫端要它**（NCR_MARK=1）。人自己開容器時不設，畫面上
# 就不會多出一行機器用的字。
#
# ⚠ **位置就是這裡：`start_capture` 之後、`run_cli` 之前。** 呼叫端拿這個標記當
#   「可以開始互動了」的訊號——放早了（例如挪到選單結束處）它會在防火牆還沒套完、
#   mitmproxy 還在起的時候就放行，那時送進去的按鍵會被吞掉，而畫面上完全看不出來。
#   放晚了（挪進 run_cli）就永遠不會印，因為那支要嘛 exec 掉、要嘛在前景等 CLI。
if [ "${NCR_MARK:-}" = "1" ]; then
    echo "__NCR_DRIVER_STARTING__"
fi

echo ""
run_cli "$@"
