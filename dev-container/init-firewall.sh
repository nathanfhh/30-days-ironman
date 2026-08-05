#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

# 容器的網路邊界：預設拒絕，白名單放行。
#
# 改寫自 Anthropic 官方 devcontainer 的 init-firewall.sh：
#   https://github.com/anthropics/claude-code/blob/main/.devcontainer/init-firewall.sh
# 步驟 1~3（保留 Docker 內部 DNS 的 NAT 規則再 flush）幾乎原樣沿用，那段是整份腳本
# 最容易漏、漏了最難查的地方。其餘的白名單內容、SSH 收斂、驗證方式都改過，理由寫在各段。
#
# 官方那份的目標是「讓 devcontainer 還能開發」，所以 GitHub 全網段、npm registry、
# VSCode marketplace 都放行；這份的目標是「讓 agent 只能做審查」，所以預設只通模型 API。
# 差別不在誰比較嚴，在**預設值想達成什麼**。
#
# 需要 --cap-add=NET_ADMIN（run wrapper 已帶）。由 entrypoint.sh 以 sudo 呼叫，
# 而 sudoers 只允許**無參數**的呼叫——原因見 Dockerfile 裡那段註解。

# ------------------------------------------------------------------------------
# 白名單。要加東西就改這裡，然後 rebuild image。
#
# 刻意不放行的兩個，說明比清單本身重要：
#   registry.npmjs.org — 放行它，agent 就能在牆內 npm install。工具版本在 Dockerfile
#                        裡 pin 死是為了讓報告可重現；能隨手裝東西，那個保證就沒了。
#   github.com / api.github.com — 官方放行整個 GitHub IP 段（要另外裝 aggregate 工具去
#                        彙整 CIDR）。審查用不到，而它同時也是最方便的資料外送出口。
#
# ⚠ 沒放行 statsig.anthropic.com / sentry.io，所以 Claude Code 的用量回報與錯誤回報
#   會靜默失敗。實測不影響任何功能，這是刻意的。
# ------------------------------------------------------------------------------
ALLOWED_DOMAINS=(
    "api.anthropic.com"
)

# GitLab 的 SSH 主機名。來自 build 時寫死的檔案，**不是**環境變數。
#
# ⚠ 這個區分是重點：容器裡的 agent 以 nathan 身分執行，env 是它寫得到的東西。
#   政策的來源如果是 env，等於讓被關的人自己挑監獄。這個檔案由 root 在 build 時寫入、
#   0444、agent 改不動也蓋不掉（bind mount 覆蓋需要 CAP_SYS_ADMIN，容器只有 NET_ADMIN）。
#   同理，這支腳本刻意**不吃任何位置參數**——參數是呼叫端控制的輸入面。
GITLAB_SSH_HOST=""
[ -r /etc/ncr/gitlab-ssh-host ] && GITLAB_SSH_HOST=$(cat /etc/ncr/gitlab-ssh-host)

# ------------------------------------------------------------------------------
# 1. flush 之前，先把 Docker 內部 DNS 的 NAT 規則撈出來
#
# 容器的 /etc/resolv.conf 指向 127.0.0.11，但那個位址上沒有任何東西在 listen——
# 是 nat 表把它轉到 Docker daemon 開的真實 port。所以 `iptables -t nat -F` 一下去，
# 容器就從「連得到但被擋」變成「連網域名稱都解不出來」，而錯誤訊息長得像網路壞掉，
# 不像防火牆生效。這一段照抄官方，不要自作聰明。
# ------------------------------------------------------------------------------
DOCKER_DNS_RULES=$(iptables-save -t nat | grep "127\.0\.0\.11" || true)

# 2. 清空
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X
iptables -t mangle -F
iptables -t mangle -X
ipset destroy allowed-domains 2>/dev/null || true

# 3. 只還原 Docker DNS，其餘不還原
if [ -n "$DOCKER_DNS_RULES" ]; then
    echo "還原 Docker DNS 規則..."
    iptables -t nat -N DOCKER_OUTPUT 2>/dev/null || true
    iptables -t nat -N DOCKER_POSTROUTING 2>/dev/null || true
    echo "$DOCKER_DNS_RULES" | xargs -L 1 iptables -t nat
else
    echo "沒有 Docker DNS 規則需要還原"
fi

# 4. 地基：DNS 與 loopback
#    ⚠ 官方在這裡還有一條 blanket 的 `--dport 22 -j ACCEPT`（SSH 通往任何主機）。
#      這份沒有，理由見 4b。
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A INPUT  -p udp --sport 53 -j ACCEPT
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# ------------------------------------------------------------------------------
# 4b. SSH 只通 GitLab 那一台
#
# run wrapper 會把 host 的 ssh-agent socket 轉發進來。交出去的是「簽章的能力」而不是
# 私鑰本體，這點沒錯——但那個能力**沒有範圍限制**：它能對任何一台連得到、且認得那把
# 公鑰的主機簽章。而多數人的 SSH 金鑰是複用的（同一把進 GitLab、也進其他伺服器）。
#
# 所以範圍不能在 SSH 那一層畫，只能在網路這一層畫：只放行我們真的會 push/pull 的那台。
#
# 已知限制（與下面的 ipset 相同）：boot 時解析一次。IP 換了就得重開容器。
# 解析失敗只警告不中斷——git over SSH 這回合不能用，但審查本身照跑（優雅降級）。
# ------------------------------------------------------------------------------
if [ -n "$GITLAB_SSH_HOST" ]; then
    gitlab_ips=$(dig +short +tries=2 +time=3 A "$GITLAB_SSH_HOST" \
                 | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)
    if [ -z "$gitlab_ips" ]; then
        echo "⚠️  解析不到 $GITLAB_SSH_HOST — 本回合 git over SSH 不可用"
    else
        while read -r gip; do
            [ -z "$gip" ] && continue
            echo "放行 SSH(22) → $GITLAB_SSH_HOST ($gip)"
            iptables -A OUTPUT -p tcp -d "$gip" --dport 22 -j ACCEPT
            iptables -A INPUT  -p tcp -s "$gip" --sport 22 -m state --state ESTABLISHED -j ACCEPT
        done <<< "$gitlab_ips"
    fi
else
    echo "未設定 GITLAB_SSH_HOST（build 時未帶 --build-arg）— 不開放任何 SSH outbound"
fi

# 5. 解析白名單網域，放進 ipset
#
#    已知限制：ipset 是**開機當下的快照**。CDN 前置的網域（api.anthropic.com 走
#    Cloudflare）TTL 很短，長時間 session 中途換 IP 的話請求會被 REJECT，
#    只能重開容器重新解析。這是刻意接受的代價——動態跟隨 DNS 就等於把白名單的
#    控制權交給 DNS 回應。
ipset create allowed-domains hash:net

for domain in "${ALLOWED_DOMAINS[@]}"; do
    echo "解析 $domain..."
    ips=$(dig +short +tries=2 +time=3 A "$domain" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)
    if [ -z "$ips" ]; then
        echo "錯誤：解析不到 $domain"
        exit 1
    fi
    while read -r ip; do
        echo "  -> $ip"
        ipset add allowed-domains "$ip" -exist
    done <<< "$ips"
done

# ------------------------------------------------------------------------------
# 6. 放行所有直連網段
#
# gitlab-proxy 就是靠這條通的，不需要為它另開白名單——**但前提是容器有接上它那張
# network**。run wrapper 會在 gitlab-proxy 存在時自動 `--network gitlab-proxy`。
#
# ⚠ 沒接上的話這條涵蓋不到它：容器在預設 bridge（172.17.0.0/16），proxy 在自己那張
#   network（172.19.x），封包走 default route，不在下面撈到的清單裡，最後被第 8 節
#   REJECT。實測過——症狀是 proxy 明明在跑卻連不到，而 `docker ps` 一切正常。
#
# ⚠ 這一節的放行是**全協定全埠**，而且排在第 8 節的 REJECT 之前。所以只要目標落在
#   直連網段，第 4b 節那個「SSH 只通 GitLab 那一台」的收斂就等於不存在——例如
#   `--network host`，或 GitLab 本身也跑成同網段的容器。目前的拓樸下不會發生，
#   但改動網路組態時要記得這兩節的優先序。
#
# ⚠ 官方那份是「拿 default route 的 gateway 推一個 /24」。差別不在鬆緊，在正確性：
#   多接一張 docker network 它就漏掉了，而網段如果不是 /24 也會算錯。這裡改成列出
#   實際的直連網段逐條放行。
# ⚠ 讀 `ip route` 而不是 `ip addr scope global`：docker 介面的 scope 隨版本不一定是 global。
# ⚠ 這是「容器啟動那一刻」的快照。容器起來之後才 `docker network connect` 上去的網路
#   不在清單裡——介面有了、路由有了，封包卻被 REJECT，而且不會自己好。
# ------------------------------------------------------------------------------
connected_subnets=$(ip -o -4 route show | awk '$1 != "default" && $1 ~ /\// {print $1}')
if [ -z "$connected_subnets" ]; then
    echo "⚠️  偵測不到任何直連網段 — gitlab-proxy 將無法連線"
fi
while read -r net; do
    [ -z "$net" ] && continue
    echo "放行直連網段：$net"
    iptables -A INPUT  -s "$net" -j ACCEPT
    iptables -A OUTPUT -d "$net" -j ACCEPT
done <<< "$connected_subnets"

# 7. 預設 DROP，再放行 established 與白名單
#
# ⚠ 這整份只碰 iptables（IPv4），沒有碰 ip6tables。前提是容器沒有 IPv6——實測預設
#   bridge 上確實沒有（無 global v6 位址、無 v6 預設路由），所以目前這道牆是完整的。
#   但這是**前提不是保證**：哪天把 docker network 開了 IPv6，OUTPUT 就有半邊沒有牆。
#   好消息是第 9 節會抓到（curl 走 v6 連上 example.com → 判定「防火牆沒有生效」→ exit 1
#   → entrypoint fail closed），所以是啟動失敗而不是靜默漏水。真要支援 v6 就得把
#   下面每一條規則在 ip6tables 再寫一次，並且 ipset 另建一個 hash:net family inet6。
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP

iptables -A INPUT  -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

iptables -A OUTPUT -m set --match-set allowed-domains dst -j ACCEPT

# 8. 其餘 REJECT。用 REJECT 而不是 DROP：讓被擋的程式立刻收到錯誤，
#    而不是卡在 timeout——agent 撞牆時你要它馬上知道，不是等 30 秒。
iptables -A OUTPUT -j REJECT --reject-with icmp-admin-prohibited

# ------------------------------------------------------------------------------
# 9. 自我驗證：不相信自己剛剛寫的規則
#
# 兩個方向都要測。只測「該擋的有沒有擋住」會漏掉「不小心把全部都擋掉」——
# 那種情況下 agent 從第一次呼叫模型就開始失敗，而錯誤訊息不會說是防火牆。
# curl 的 exit 0 涵蓋任何 HTTP 回應：Cloudflare 回 403 也證明連線建立了。
# ------------------------------------------------------------------------------
if curl --connect-timeout 5 -sS -o /dev/null https://example.com 2>/dev/null; then
    echo "錯誤：example.com 連得到 — 防火牆沒有生效"
    exit 1
fi
for domain in "${ALLOWED_DOMAINS[@]}"; do
    if ! curl --connect-timeout 5 -sS -o /dev/null "https://${domain}/" 2>/dev/null; then
        echo "錯誤：${domain} 連不到 — 白名單沒生效，agent 會從第一次呼叫就失敗"
        exit 1
    fi
done

echo "防火牆已驗證。"
