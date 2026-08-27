#!/bin/sh
# 一條 TCP 連線 ⇄ session 容器內 mitmweb UI 的橋（ADR 0021）。
#
#   用法（由 socat 的 EXEC 位址呼叫，不經 shell）：
#     claude-pty 的 mitm_views 起 `socat TCP-LISTEN:<p>,fork EXEC:<本檔> <cid> <port> <linger>`
#
# ## 為什麼要有這個檔，而不是把整條指令寫進 socat 的位址
#
# socat 的 `EXEC:` **依空白切詞、沒有引號機制**，`SYSTEM:` 則會先被 socat 自己解一次
# 引號再交給 /bin/sh，兩者都塞不進一條「帶引號的內層 shell 指令」（2026-08-26 實測：
# `SYSTEM:sh -c 'echo A B'` 的引號被 socat 吃掉，變成 `sh -c echo A B`，印出空行）。
# 把指令收進檔案之後，socat 那一側就只剩「路徑 + 三個沒有空白的參數」，引號問題消失，
# 而檔案裡是一般的 shell，愛怎麼引就怎麼引。
#
# ## 為什麼是 docker exec 而不是連過去
#
# mitmweb 的 UI 綁在 session 容器的 **loopback**（run_kwargs 送 `NCR_MITM_WEB_BIND`
# 收回去的），沒有 host port，兄弟容器也連不到。那是刻意的：那個畫面顯示的是**未脫敏
# 的即時流量**。`docker exec` 是唯一進得去的路，而它由控制平面發起、只吃 DB 裡的
# container id，不吃使用者輸入。
#
# ## 內層是容器裡的另一顆 socat（2026-08-27 起；之前是 `python3 -c` 的雙向搬運）
#
#   docker exec -i <cid> socat -t <linger> STDIO TCP:127.0.0.1:<port>
#
# 換掉 python 的理由：socat 的雙向轉發是原生 C，位元組原樣過、HTTP 與 WebSocket 都吃；
# python 版存在只是因為當時 session image 沒有 socat（ADR 0021 背景第 2 點），
# 現在 image 有了（dev-container/Dockerfile），就沒有理由維護一份手寫的搬運迴圈。
#
# 三個容易搞混的點，寫清楚：
#
# 1. **`docker exec -i`（互動 stdin），但絕對不是 `-it`。** TTY 會做行規則轉換、
#    破壞 binary stream；HTTP body 與 WebSocket frame 都是 raw binary。
# 2. **socat 的 `-t` ≠ docker 的 `-t`。** docker 的 `-t` 是配置 TTY（不可用，見上）；
#    socat 的 `-t N` 是 *closewait*：stdin EOF 之後再等 N 秒讓對面把話講完。
# 3. **linger 由 socat 的 closewait 接手，語義比 python 版更好。** python 版是
#    「EOF 之後 N 秒整個殺掉」；socat 是「EOF 之後最多等 N 秒，對面先講完就先走、
#    對面不關才強制收」。已實測（2026-08-27，socat 1.8.0，同 image 的 ubuntu:24.04）：
#      - 半關閉後慢回應照常送達（-t 3 時 1.5s 的回應完整收到）；
#      - 上游收到 FIN 卻永不關時（WebSocket 那種），-t 到期整條收掉，不留在容器裡；
#      - **它不殺閒置中的活連線**（那是 `-T` inactivity timeout，不要用錯）——
#        開著分頁但沒流量的 WebSocket 不會被收掉。
set -eu

exec docker exec -i "$1" socat -t "$3" STDIO "TCP:127.0.0.1:$2"
