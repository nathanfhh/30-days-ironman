#!/usr/bin/env bash
# 容器啟動入口：報告 image 資訊、確認憑證在場、決定網路能力，然後把控制權交給指令。
# 預設指令是 claude --dangerously-skip-permissions：
# 容器本身就是隔離層，煞車在牆上，不在每一次的允許提問裡。
set -euo pipefail

echo "📦 ncr-dev-container｜image built: $(cat /etc/image-build-time 2>/dev/null || echo unknown)"

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ ! -s "$HOME/.claude/.credentials.json" ]; then
    echo "⚠️  容器內沒有憑證（CLAUDE_CODE_OAUTH_TOKEN 或 ~/.claude/.credentials.json），claude 會要求登入。"
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
#   - 只錄模型 API 的 host，其餘一概不收。
#
# 網頁 UI 顯示的是記憶體裡的即時 flow（未脫敏），只綁本機、有 token。
# 落到磁碟的永遠只有脫敏版，兩者是分開的。
# ------------------------------------------------------------------------------
CAPTURE_ADDON="/home/nathan/code-review/mitm/capture_addon.py"
CAPTURE_DIR="$HOME/ncr/mitm"
CAPTURE_PROXY_HOST="127.0.0.1"
CAPTURE_PROXY_PORT="8880"
CAPTURE_WEB_PORT="8081"          # 容器內固定；host 那側由 run script 動態挑，避免多開時撞號
CAPTURE_HOSTS="api.anthropic.com"
CAPTURE_CA="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
CAPTURE_ON=0
CAPTURE_PID=""

start_capture() {
    [ "$CAPTURE_ON" = "1" ] || return 0

    if [ ! -f "$CAPTURE_ADDON" ]; then
        # fail closed：沒有脫敏 addon 就不錄，而不是錄一份沒掃過的原始流量。
        echo "⚠️  找不到脫敏 addon（${CAPTURE_ADDON}）——本場不錄。"
        echo "   （run script 要從 repo 根目錄執行，容器裡才看得到 mitm/）"
        return 0
    fi

    mkdir -p "$CAPTURE_DIR"
    local flow_file="${CAPTURE_DIR}/flows-$(date +%Y%m%dT%H%M%S).mitm"
    local token
    token=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)

    # 用 mitmweb 而不是 mitmdump，是為了那個網頁 UI——錄的當下就看得到，
    # 不必等收工後才知道錄到了什麼。
    # 不加 -w：內建的 save addon 排在我們前面，會先把未脫敏的原始 flow 寫出去。
    # store_streamed_bodies：SSE 的 body 要靠它才會在 response hook 就位。
    nohup mitmweb -q \
        --listen-host "$CAPTURE_PROXY_HOST" --listen-port "$CAPTURE_PROXY_PORT" \
        --no-web-open-browser \
        --web-host 0.0.0.0 --web-port "$CAPTURE_WEB_PORT" \
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
    # 內部流量不繞經 capture：gitlab-proxy 是審查主線（不該讓 proxy 夾在中間），
    # jaeger 走 OTLP gRPC、本來就不是錄製標的。
    export NO_PROXY="gitlab-proxy,jaeger,localhost,127.0.0.1,::1"
    export no_proxy="$NO_PROXY"

    echo "● 錄製中 → ${flow_file}"
    echo "● 即時畫面 → http://localhost:${NCR_MITM_WEB_PORT:-$CAPTURE_WEB_PORT}/?token=${token}"
    echo "  （畫面上是未脫敏的即時內容、只有本機連得到；落地的檔案是脫敏版）"
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
run_cli() {
    if [ -z "$CAPTURE_PID" ]; then
        exec "$@"
    fi
    set +e
    "$@"
    local rc=$?
    stop_capture
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
echo "錄製本場流量？（mitmproxy，只錄 ${CAPTURE_HOSTS}）"
echo "  y = 錄，落在 ~/ncr/mitm/flows-<時間>.mitm（脫敏後）"
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

start_capture
echo ""
run_cli "$@"
