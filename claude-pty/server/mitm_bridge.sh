#!/bin/sh
# 一條 TCP 連線 ⇄ session 容器內 mitmweb UI 的橋（ADR 0021）。
#
#   用法（由 socat 的 EXEC 位址呼叫，不經 shell）：
#     claude-pty 的 mitm_views 起 `socat TCP-LISTEN:<p>,fork EXEC:<本檔> <cid> <port> <linger>`
#
# ## 為什麼要有這個檔，而不是把整條指令寫進 socat 的位址
#
# socat 的 `EXEC:` **依空白切詞、沒有引號機制**，`SYSTEM:` 則會先被 socat 自己解一次
# 引號再交給 /bin/sh——兩者都塞不進一條「帶引號的內層 shell 指令」（2026-08-26 實測：
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
# ## 為什麼內層是 python3 而不是 bash 的 /dev/tcp
#
# `bash -c 'exec 3<>/dev/tcp/…; cat <&0 >&3 & cat <&3'` 這種寫法沒有**半關閉**：
# client 送完請求把寫入端關掉時（curl 之類會這樣做），要嘛把讀方向一起砍掉（回應被截斷，
# 實測第一發請求就空的），要嘛永遠不收。python 分得開這兩件事——stdin EOF 只 shutdown
# 寫入端，讀繼續。session 容器裡本來就有 python3（mitmproxy 就是 python）。
set -eu

exec docker exec -i "$1" python3 -c '
import os, socket, sys, threading

LINGER = float(sys.argv[2])
sock = socket.create_connection(("127.0.0.1", int(sys.argv[1])))


def upstream():
    """client → mitmweb。"""
    try:
        while True:
            chunk = os.read(0, 65536)
            if not chunk:
                break
            sock.sendall(chunk)
    except OSError:
        pass
    try:
        # 半關閉：只收掉寫入端，讀的那一半留著把回應收完。
        sock.shutdown(socket.SHUT_WR)
    except OSError:
        pass
    # 但不可以無上限地等：對面若不主動關（WebSocket 就是這種），主緒會永遠停在 recv 上，
    # 而這個行程跑在使用者的 session 容器裡。時間到就整個收掉。
    timer = threading.Timer(LINGER, os._exit, (0,))
    timer.daemon = True
    timer.start()


threading.Thread(target=upstream, daemon=True).start()
try:
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        os.write(1, chunk)
except OSError:
    pass
' "$2" "$3"
