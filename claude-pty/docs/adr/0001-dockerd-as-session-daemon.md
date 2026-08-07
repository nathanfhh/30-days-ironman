# ADR 0001：以 dockerd 持有的 PTY 作為持久 session 核心（attach，而非 exec）

- 狀態：已接受

## 背景

核心需求是「人從瀏覽器與容器內的互動程式（Claude Code）雙向溝通，斷線不死、可重連、
可多重 attach」，且機制必須 application-agnostic——同一套機制跑 `bash`、`vim`、`top`
都要完美（驗收標準：`docker exec -it <c> bash` 等級的互動保真）。

關鍵洞察：`docker run -dit` 建立的 PTY 由 **dockerd 持有**，與任何客戶端連線無關——
dockerd 本身就是一個久經考驗的 session daemon，不需要自建。

## 決策

1. **一個持久 session = 一個 container**：以 `docker run -dit` 啟動，目標互動程式為
   PID 1，其 PTY 由 dockerd 持有。
2. **所有客戶端一律走 `docker attach`（API 或 CLI），不走 `docker exec`**。attach 連接
   的是 dockerd 持有的長壽 stream；exec 產生的程序 stdio 綁在發起連線上，斷線即死，
   不符持久需求。
3. **session 生命週期 = container 生命週期**：終止 session 即 `docker stop && docker rm`。
   不另建生命週期管理層。

## 依據（spike 實測，全數通過）

- 多條 attach socket 同時讀寫同一 session，彼此鏡像（含輸入回顯與執行結果）。
- attach 全部斷開後，container 內程序繼續執行；重連後可續操作。
- `Ctrl+P`（0x10）、`Ctrl+C`（0x03）等 raw byte 經 attach 原樣穿透，signal 語義正確。
- UTF-8 中文輸入經 raw socket 直達無失真。

## 後果

- 免去自建 daemon、attach 協定、broadcast 的全部工程量；瀏覽器只是 attach 的客戶端。
- 綁定 Docker：session 的存在以 dockerd 運行為前提。
- 重連後的畫面恢復需另行處理（見 [ADR 0003](0003-no-server-side-replay-on-reconnect.md)）。
