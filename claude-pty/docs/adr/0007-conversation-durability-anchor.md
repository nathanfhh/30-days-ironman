# ADR 0007：對話續命的錨點是設定目錄的 mount，控制平面是可拋棄的即時驅動層

- 狀態：已接受

## 背景

系統有兩個常被混為一談、但壽命完全不同的「session」概念：

1. **live terminal session**：dockerd 持有的 PTY（[ADR 0001](0001-dockerd-as-session-daemon.md)）。
   container 停止 / ttyd 掛掉，這層即消失——這是原始 TTY、螢幕畫面。ADR 0003 已決定重連
   不重播這層。
2. **對話（conversation）**：CLI 增量寫在設定目錄下的 transcript（`projects/<cwd-hash>/*.jsonl`）。
   `/resume`（與 `--resume`）讀的是這個。

控制平面（登錄表、per-session container、ttyd）管的是第 1 層。第 2 層的持久化完全由
設定目錄的 mount 決定，與控制平面無關。

## 決策

**明確承認：對話續命的唯一錨點是「設定目錄以 rw 掛出 + 一致的工作目錄」，控制平面本身
是可拋棄的即時驅動便利層。**

- **續命不依賴控制平面**。只要一個 container「rw 掛對設定目錄 + 相同 cwd」，就能 `/resume`
  接回任何先前 session 的對話——無論那個 session 是誰開的、控制平面是否還記得它。
- **兩個前提，缺一不可**：(a) 設定目錄必須 **rw** 掛載（ro 則 transcript 寫不進去、
  `/resume` 看不到）；(b) 工作目錄一致（`/resume` 按 project=cwd 分桶）。
- **連 SIGKILL 都不丟對話**：CLI 逐 turn 增量寫 transcript 到 mount，`docker rm -f` 最多
  只讓最後一個 in-flight turn 不完整。
- **生命週期 bug 的風險等級因此被正確定性**：控制平面的登錄遺失、漏刪 exited container、
  重啟丟 session，丟的是「對 container 的追蹤」與「stopped container 的 disk」，屬**資源
  衛生**問題，不是資料遺失——工作永遠能 `/resume` 回來。

## 後果

- capture 的 `.mitm` 產出比照同一原則：必須 bind-mount 到 host，否則隨 container writable
  layer 消失。
- session 登錄持久化（[ADR 0008](0008-persistent-registry-and-on-demand-ttyd.md)）的價值是
  「讓控制平面重啟後能重新接管既有 container」的便利，而非「避免資料遺失」——後者本就由
  設定目錄的 mount 保證。
- 文件與 UI 若呈現「session」，需向使用者說清楚：終端畫面不跨 container 生命週期，但對話
  可經 `/resume` 續接。

## 這個機制決定了一個安全邊界（隔離的形狀）

續命需要「rw 掛出的設定目錄 + 一致的 cwd」。**如果那份設定目錄是所有 session 共用的**，
續命的代價就是使用者 A 能 `/resume` 到 B 的對話、讀寫 B 的 transcript。因此本系統把設定
目錄切成 **per-user**（見 [ADR 0014](0014-per-user-agent-state.md)）：續命模型的形狀不變
——rw 掛出的設定目錄 + 一致的 cwd——只是那份目錄從共用變成 per-user，跨使用者的 `/resume`
隨之消失，那正是目的。

⚠ 但 per-user 切的是 transcript / 設定 / skills / 錄製。**CLI 憑證另有其模型**：每個
使用者貼自己的授權 token（見 [ADR 0014](0014-per-user-agent-state.md) 與 README），
所以憑證天生就是 per-user，不共用。
