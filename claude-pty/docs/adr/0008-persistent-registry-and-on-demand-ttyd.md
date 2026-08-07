# ADR 0008：持久化 registry（SQLite）+ on-demand ttyd + reconciler

- 狀態：已接受

## 背景

兩個既有假設被使用情境推翻：

1. **per-session 常駐 ttyd 是浪費**——大部分時間沒人看，卻每個 session 都掛一個 ttyd
   進程要管。
2. **控制平面的 in-memory registry 是重啟脆弱點**——Flask 一 restart，還在跑的 container
   就變成「還在跑但沒人記得」的孤兒。這與 worker 數量無關，單 worker 也需要持久化。

## 決策

### 1. 持久化 registry：SQLite（WAL），DB 為唯一仲裁者

- **DB 就是 registry，也是那把鎖**：port 分配（`UNIQUE` 約束，撞約束即重試）、session
  上限計數、session 登錄，全部由 DB 仲裁；**不保留任何 in-memory 權威狀態**。
- **互斥靠 `BEGIN IMMEDIATE`**：SQLite 的預設交易是 deferred，先讀只拿 read lock、要寫
  才升級，而 WAL 下若中間有別人寫過，升級會當場回 `SQLITE_BUSY`（`busy_timeout` 對這種
  快照衝突無效）。「數一數 → 寫一列」這種讀後寫的交易一律以 `BEGIN IMMEDIATE` 開啟，
  交易一開始就取寫鎖——配額、port、租約、單一執行者 lease 都靠它。
- **只有一種方言**：資料庫就是 SQLite，設定收的是**檔案路徑**不是連線字串，啟動時就
  擋掉帶 `://` 的值並講清楚該給什麼。單機部署、檔案級鎖、備份＝複製一個檔案。SQLAlchemy
  作為 ORM 抽象層薄用，但不留「看起來可以換資料庫」的假把手。

**sessions 表（container 為王）**：`id`、`container_id/name`、`user_id`（FK）、`status`、
`created_at`、`last_active_at`、`workdir`（`/resume` 分桶依據，ADR 0007）、profile（JSON）。
**不存 ttyd pid**——ttyd 屬於「一次觀看」，不是 session 的持久狀態。

**users 表**：`username` + `password_hash`（**一律 argon2id**，絕不明文、不自刻）。此表是
[ADR 0005](0005-edge-auth-and-web-exposure.md) authn/authz 的地基。

**views 表（暫態，on-demand ttyd 用）**：`session_id`、`port`（`UNIQUE`）、`pid`、
`created_at`。與 sessions 分開——它是短命的觀看，不是 session 本體。

### 2. ttyd 改為 on-demand，`-q` 自我了斷 + double-fork 交由 init reap

- `create()` **只起 container + 寫 DB registry，不起 ttyd**。
- 使用者要看時才臨時起 `ttyd -q ... docker attach <container>`。
- **關掉網頁 → WS 斷 → `-q`（`--exit-no-conn`）讓 ttyd 自行 exit → init reap**。無需偵測
  關頁、無需任何 worker 送 kill。
- ttyd 以 **double-fork / setsid** 起，reparent 給 init：不屬於任何 worker，任何 worker
  可憑 pid `os.kill` 提前收（如 session 被 terminate），殭屍由 init 收。
- **已知 trade-off**：`-q` 是斷線即退，網路抖動也會收掉 ttyd。on-demand 模型下無妨——
  重開網頁＝再起一個新 ttyd attach 同一 container（container 持久、起 ttyd 便宜）。

### 3. reconciler 抽成獨立 process

DB 與真實狀態會漂移（container 死了 DB 還寫 running、worker 半路崩、ttyd 自退後 view
port 未釋放）。因此獨立一支 reconciler（非 Flask worker）負責：以 `docker ps` 對帳
sessions、清理已死的 view 記錄並釋放 port、執行 idle 回收。不讓每個 worker 各跑一份
（會在 DB 上互撞）；抽成單一 owner，且它不在請求路徑上。

## 後果

- 「Flask 重啟丟失還在跑的 container」的痛點消失；nginx / view 路由有 DB 作依據。
- [ADR 0007](0007-conversation-durability-anchor.md) 仍是底線：DB 只是便利/路由層，**真相
  在 container + 設定目錄 mount**；DB 漂移可由 reconciler 對帳回復，對話續命從不依賴 DB。
