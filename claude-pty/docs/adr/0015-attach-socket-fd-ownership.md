# ADR 0015：attach socket 必須自己關掉底層 fd，不能等 GC

- 狀態：已接受；已實作

## 背景

一個 session 在網頁終端上完全凍住：畫面靜止、`docker attach` 與 `docker logs` 收不到
新輸出，但 `docker exec` 進得去、容器 `Up`、CPU 0%、沒有 OOM。凍結持續 5 小時以上且
**不會自癒**，`docker rm -f` 也掛住。

完整活體解剖排除了一串看似合理的嫌疑（kernel、containerd/shim/console、CLI 本身、ttyd
的 C/Rust 差異——逐行比對兩個實作，事故相關軸完全等價，兩者都只是同一條斷流的下游）。

真兇是**我們自己**：`close_attach()` 只呼叫了 docker-py wrapper 的 `close()`。
`attach_socket()` 回傳的是 `socket.SocketIO`，而 `SocketIO.close()` 只做
`_decref_socketios()`——**它不關底層 fd**。那個 fd 要等 CPython GC 收掉 docker-py 內部的
參照環才會消失，而那是不定時的。

### 為什麼一個沒關的 fd 會讓整顆容器凍住

洩漏的 attach 連線沒人讀 → socket 緩衝（208KB）填滿 → dockerd 那側卡在寫這個 fd →
停止消費它的 `BytesPipe` → `BytesPipe` 累積到上限 → 讀 fifo 的 goroutine 卡在
`BytesPipe.Write`，**而它此刻正握著 broadcaster 的 mutex**。於是該容器的輸出全停、所有新
attach 卡在等同一把鎖、`docker rm` 走的清理路徑也要那把鎖一併死鎖。

高輸出的 TUI 約 100 秒就填滿緩衝；低輸出的多半在 GC 收掉 fd 之前就沒事——**這是一場 GC
與輸出速率的賽跑**，也解釋了「為什麼平常不會發生」。moby 那側確實有韌性缺陷（一個不讀
的客戶端可以讓容器輸出永凍且不可回收），但**觸發源是我們的 fd 洩漏**。修我們這一行，
這條鏈就從第一步斷掉。

## 決策

1. **attach socket 的 fd 由我們自己關，不依賴 GC。** 新增 helper：先取 `sock._sock`，
   再關 wrapper，最後關底層 socket。正常路徑與失敗路徑都走它。
   - ⚠ `_sock` **必須在 `sock.close()` 之前取**：wrapper 關閉時會把它設成 None。
   - 關掉底層 fd 會讓 dockerd 那側立刻收到 EPIPE，它自己的清理路徑隨即把 BytesPipe 關掉、
     從 broadcaster 驅逐，凍結鏈無從開始。
2. **回歸測試釘住「fd 真的關了」而不是「close 被呼叫了」**（`tests/test_attach_close.py`）：
   用 socketpair ＋ 真的 `socket.SocketIO` 複製 docker-py 的回傳形狀，斷言關完之後底層
   socket 不可再 `send()`。第一條先驗證前提（只關 wrapper 時 fd 仍活著）——前提不成立時
   這支測試等於沒在測東西，必須當場看得出來。

## 後果

- 這類凍結從源頭消失，不再依賴「GC 夠快」這個不可控前提。修正很小、沒有效能代價——本來
  就要關的東西真的關掉而已。
- 代價：`_sock` 是 CPython `SocketIO` 的內部欄位，若日後 docker-py 換成別的回傳型別，
  `getattr(sock, "_sock", None)` 會拿到 None、退回舊行為而**不會報錯**。升級 docker-py 時
  要重跑 `tests/test_attach_close.py` 並確認前提那一條仍 reflect 真實回傳型別。
- ⚠ **不要對凍結的容器下 `docker rm`——那會把傷害升級**：rm 卡在清理路徑等那把已被抱走
  的 mutex，而它此刻握著 container lock，從此該容器的 `inspect`／`stop`／`rm` 全部掛住，
  連 `containers.list()`（逐顆 inspect）都開始逾時。凍結時的正解只有 `docker restart`
  （writable layer 保留，對話錨點是掛進去的設定目錄，`/resume` 接得回來）或不動它。
  reconciler 逃過一劫是因為它逐顆容器的呼叫接的是 `Exception`（[ADR 0012](0012-list-reads-db-with-freshness.md)），
  這個設計在此得到印證。
