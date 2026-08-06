#!/usr/bin/env bash
# 印出這一場防火牆規則的封包計數。唯讀，不改任何規則。
#
# 為什麼要獨立成一支：sudoers 只授權「固定的、不吃參數的命令」，直接授權
# `iptables` 等於把改規則的能力一起送出去。這支只做 -L -v -n（列出＋計數），
# 沒有任何參數可以傳進來。
#
# 計數的意義：容器是 --rm 的，規則從 init-firewall.sh 套用那一刻起算，
# 所以收工時的數字就是這一場的總量，不需要另外抓開場快照來相減。
set -euo pipefail
# -x（exact）不可省：只給 -v 的話，封包數大了會印成 1234K / 5M，
# 下游解析拿到的就不是數字。
echo "# generated at $(date -Iseconds)"
iptables -L OUTPUT -v -n -x
echo
iptables -L INPUT -v -n -x
