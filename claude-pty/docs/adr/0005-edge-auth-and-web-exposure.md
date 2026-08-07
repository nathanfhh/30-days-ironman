# ADR 0005：nginx 單一入口 + 兩條路由 + per-session ttyd on loopback

- 狀態：已接受；已實作

## 背景

[ADR 0002](0002-terminal-channel-ttyd.md) 決定瀏覽器通道用 ttyd 包 `docker attach`；
[ADR 0004](0004-flask-control-plane.md) 讓 Flask 成為控制平面。本 ADR 收斂「多 session
的對外曝露方式」與「驗證拓撲」。

## 決策

### nginx 為唯一對外入口，兩條路由

外部只看到 nginx 一個入口，所有 ttyd port 藏在 loopback（或內部網路）後：

- **路由 A（控制平面）**：`/api/*` → Flask。建立 / 列出 / 終止 session 的 HTTP API。
- **路由 B（終端機）**：`/session/<id>/` → 先 `auth_request` 子請求問 Flask「這個人能不能
  開這一場」，**通過才** proxy（WebSocket）到該 session 的 ttyd。

授權判斷與終端連線因此解耦：nginx 負責攔截與轉發，Flask 是唯一的授權決策者，ttyd 只在
通過後才被碰到。

**所有 `/api/*` 都 enforce 登入**（僅 `login` 例外）：Flask `before_request` gate；
session 路由額外檢查擁有權，非擁有者回「未知 session」而非 403，不洩漏存在性；管理端點
加 `admin_only`。

### 一 session = 一 ttyd（port-per-ttyd）

- 每個 session 起一個**專屬 ttyd**，綁內部位址的動態 port；nginx 以 `/session/<id>/`
  路由到該 port，ttyd 以 `-b/--base-path` 掛在子路徑下。
- **否決 `ttyd --url-arg`（單 ttyd 多 container）**：container 名由 client 經 URL 決定
  ＝command injection 溫床、且無隔離（單進程一崩全崩）。

### 驗證分層，authn 先於 authz

- **authn**：`auth_request` 問 Flask 這個人登入了沒。
- **authz**：sub-request 帶目標 session_id，Flask 檢查擁有權才放行——防止一個合法登入
  attach 到別人的 container。
- **第二層（縱深）**：Rust 版 ttyd 以 `--auth-url` 在自己放行每個請求前再問一次控制平面
  （見 README 的兩顆 binary 差異、`server/views.py`）。與 nginx 那層問同一個事實、走
  不同的路——nginx 那層被繞過或設錯時，終端自己還有一道。

### 終端資料流不經 Flask（重要邊界）

授權（auth_request / auth-url）走 Flask，但**終端的 byte 流是 nginx → ttyd →
`docker attach`，完全不經過 Flask**。這讓 Flask 維持短請求形狀、長壽連線不佔 worker。
安全模型因此是：**進得了終端**要先過 nginx + Flask 的授權；**碰得到 dockerd** 則由 OS 層
的 docker socket 權限把關（見 [ADR 0009](0009-containerized-deployment-docker-socket.md)）。

## 已評估但暫緩：Flask 自代理 WebSocket、砍掉 ttyd

替代方案：Flask 直接托 xterm.js 頁面並把 WS 橋接到底層 attach，消除 per-session ttyd
進程池與 port 管理。

**結論：demo 便宜，維護尾巴貴，暫緩。** 核心橋接很小（byte pump + resize），但 ttyd
已磨平多年的維護尾巴會全部變成自己的責任：背壓 / flow control（大量重繪時 buffer 脹，
ttyd 有 PAUSE/RESUME）、worker 模型（長壽 WS 破壞 Flask 的短請求形狀）、UTF-8 跨 read
切斷的紀律、teardown / fd 洩漏 / reconnect race、IME / 貼上的頁面組裝。ttyd 路線的代價
換成「管 N 個 ttyd 進程 + port 池 + nginx 動態路由」，但那是一次性、有界的維運工，不是
持續的協定維護。收斂門檻很明確：當 port 池的維運成本超過自刻背壓/worker 的成本時再做。

## 後果

- 對外單一入口、單一 TLS 終點；授權決策留在 Flask，不外洩給前端。
- 每 session 獨立 ttyd＝故障隔離、生命週期與 container 對齊。
- 維運成本：需管理 per-session 的 ttyd 進程與 port，以及 nginx 的動態路由。此成本是
  「暫緩 Flask-WS-proxy」的已知取捨。
