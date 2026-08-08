# ADR 0004：Flask 作為控制平面 / session manager

- 狀態：已接受

## 背景

[ADR 0001–0003](0001-dockerd-as-session-daemon.md) 確立了「dockerd 持有持久 session、
attach 為唯一通道、重連依賴 TUI 自身重繪」的核心。但仍缺一個**擁有權威、面向外部請求
的後端**：決定何時 spawn container、用哪個 image / 工作目錄 / 啟動命令、以及 session
的列出與終止。安全需求（後端限定 container/image/workdir/command、每人 session 上限、
閒置回收）都需要這樣一個集中強制點。

## 決策

**以一個 Python Flask 服務作為控制平面（session manager）。** 它是唯一有權操作 dockerd
的角色。對外驗證由邊緣層負責（見 [ADR 0005](0005-edge-auth-and-web-exposure.md)）。

### 職責

- `create`：`docker run -dit` 起一個 container（互動程式為 PID 1），回傳 `session_id`，
  維護 `session_id ↔ container` 登錄。啟動的是**裸的互動 CLI**——這套系統要的正是一個
  完整互動 TUI 的行為（可重連、人隨時介入），所以它就是把終端交給人，不做任何一次性
  的批次驅動。
- `list` / `status`：列出與查詢 session 狀態。
- `terminate`：`docker stop && docker rm`（＝session 生命週期，同 ADR 0001）。
- `attach` 的協調：為瀏覽器通道（經 on-demand ttyd，見 ADR 0005 / 0008）提供接點。

### 用 docker-py，不拼 shell 字串

- 一律使用 **docker-py（Python Docker SDK）** 的 API / argv 陣列操作 container，
  **絕不** `os.system(f"docker ... {user_input}")` 之類的 shell 字串插值——不讓任何
  使用者資料進入命令解析。
- image、工作目錄、mount、啟動命令全為 server 端寫死的常數。

### spawned container 的安全輪廓

- 容器內的 CLI 以非 root 執行。
- **不得**把 host 的 docker socket mount 進 spawned container（否則 session 可反向控制
  host）。這條紅線在容器化部署後更形重要（見 [ADR 0009](0009-containerized-deployment-docker-socket.md)）。
- 套用資源限制（`--memory` / `--cpus` / `--pids-limit`）與每人 session 上限。cap 與網路
  輪廓依 profile 按需收斂（見 [ADR 0006](0006-session-runtime-profile.md)、
  [ADR 0016](0016-per-user-gitlab-proxy.md)）。
- 閒置回收的機制做在 reconciler 裡（`_reclaim_idle`），但**預設停用**
  （`CLAUDE_PTY_IDLE_TIMEOUT_HOURS=0`）。原因是這套東西的主要用途正是「長跑、偶爾回頭看」，
  而 `last_active_at` 只在開 view 或改尺寸時更新——一個自主工作好幾小時的 session，在這個
  量測下看起來完全閒置，照它回收就是殺掉正在幹活的那些。要開之前得先想清楚活躍度怎麼量，
  所以它是一個**留著的能力**，不是現行的安全輪廓的一部分。

## 後果

- 所有安全與生命週期強制點集中於 Flask，前端只是它授權後的下游。
- Flask 維持「短請求」形狀（spawn 完回 id 即結束）；長壽的 byte 流留在 ttyd / dockerd，
  不佔 Flask worker。
