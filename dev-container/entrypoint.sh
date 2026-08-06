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
        # 非互動環境（CI、腳本）用 NCR_OTEL（1|0）跳過選單，同 NCR_NET 的姿勢。
        if [ -n "${NCR_OTEL:-}" ]; then
            case "$NCR_OTEL" in
                1) otel_choice=y ;;
                0) otel_choice=n ;;
                *) otel_choice="$NCR_OTEL" ;;
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

echo ""
exec "$@"
