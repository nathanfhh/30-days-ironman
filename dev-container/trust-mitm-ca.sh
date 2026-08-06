#!/usr/bin/env bash
# 把這一場現產的 mitmproxy 根憑證裝進系統信任庫。
#
# 為什麼需要：餵 NODE_EXTRA_CA_CERTS 和一份 bundle 給 curl/python，覆蓋的是
# 「我預先想得到的客戶端」。要錄到全部流量，連我沒想到的那個程式也得信任它，
# 而那些程式讀的是 /etc/ssl/certs。
#
# 代價很明確：裝進去之後，這台機器上所有吃系統信任的程式都信這張憑證。所以它
# 只能發生在用完即丟的容器裡，而且憑證是每一場現產、不持久化——炸開的範圍就是
# 這一個容器的這一次。
#
# 路徑寫死、不吃參數：sudoers 只授權固定形狀的命令。
set -euo pipefail
SRC="/home/nathan/.mitmproxy/mitmproxy-ca-cert.pem"
DST="/usr/local/share/ca-certificates/mitmproxy.crt"
[ -f "$SRC" ] || { echo "找不到 $SRC" >&2; exit 1; }
install -m 0644 "$SRC" "$DST"
update-ca-certificates > /dev/null
