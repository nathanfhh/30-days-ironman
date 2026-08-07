# ADR 0014：per-user 的 agent 狀態空間——用 `CLAUDE_CONFIG_DIR` 換掉共用的設定目錄

- 狀態：已接受；**已實作**

## 背景

[ADR 0007](0007-conversation-durability-anchor.md) 把「對話續命的錨點」定在設定目錄的
mount。如果那份目錄是**共用**的，續命的代價就是使用者 A 能 `/resume` 到 B 的對話、讀寫
B 的 transcript。這份 ADR 就是把狀態層切成 per-user 的那次變更。

動手前先盤點：設定目錄（transcript / settings / skills）、`.claude.json`（onboarding
狀態、專案清單）、錄製產出（`.mitm` 裡有 prompt 全文，比 transcript 更敏感）——都含使用者
資料，都要隔離。而 `config.WORKDIR` 本來就沒掛（它是容器 writable layer，terminate 就
消失），所以 cwd 不必額外處理，只需**維持不動**（`/resume` 按 cwd 分桶，同一人的前後
兩場要接得上）。

## 決策

**每個使用者一個 host 空間，以 `CLAUDE_CONFIG_DIR` 把 CLI 的整份狀態指過去。**

```
${CLAUDE_PTY_SPACE}/user-{userId}/
├── claude/            → /home/nathan/.claude       （rw；含 .claude.json）
├── persistent-data/   → /home/nathan/persistent-data（rw；給使用者自己放東西）
└── mitm/              → 錄製產出                    （rw）
```

```
CLAUDE_CONFIG_DIR=/home/nathan/.claude
```

空間根目錄預設 **`~/claude-pty-space`**，以 `CLAUDE_PTY_SPACE` 覆寫。host 原本的設定目錄
**完全不再進 session**。

⚠ **刻意不放在 `~/Documents` 底下**（原始構想是那裡）。macOS 上那個位置有三個麻煩：
iCloud Drive 常同步 Documents——執行期狀態會被送上雲，而且檔案可能被 evict 成 stub
（容器讀到的是佔位檔）；TCC 對 Documents 有額外授權要求；備份工具會去掃它。這是高頻
寫入的執行期狀態目錄，不該落在被同步的路徑下。

### 為什麼是 `CLAUDE_CONFIG_DIR`，而不是直接把 per-user 目錄掛到 `~/.claude`

`.claude.json` 的位置是 `CLAUDE_CONFIG_DIR || homedir()`。不設這個 env、只把目錄掛到
`~/.claude` 的話，`.claude.json` 會落在容器 writable layer，換一顆容器就沒了——而且是
無聲的。設了 CLAUDE_CONFIG_DIR，整份狀態（含 `.claude.json`）才真的改看 per-user 目錄。

### 「全有全無」是特性，不是限制

要嘛整份狀態都在 per-user 目錄、要嘛都不在——不做「transcript per-user 但 skills 共用」
這種混搭。混搭會製造「這個檔到底算誰的」的長期困惑，而且 `/resume` 的分桶依賴整份狀態
一致。

### 憑證是另一個模型：每人自己的 setup-token

per-user 空間切的是 transcript / 設定 / skills / 錄製。**憑證不走這條**——每個使用者在
自己的機器上執行 `claude setup-token`，把輸出貼到帳號管理頁，控制平面加密存 DB
（金鑰由 `SECRET_KEY` 導出），開場時以環境變數交給那一場的 CLI。所以憑證天生 per-user，
host 上不需要準備任何憑證檔（也不去讀——那種「檔案在就順便用」的後路是一條平常不走、
出事才走、沒人測過的路徑，見 README 與 `server/models.py` 的欄位註解）。

### 方法論：探測 TUI 的兩條規則（都是踩出來的）

- session container 的 stdin 是 TTY，很多 CLI 子命令（`--help`、`--version`）在 TTY 下的
  行為與非 TTY 不同——**驗證要在跟正式環境一樣 stdin 是 TTY 的條件下做**，否則會全部
  通過卻在正式環境每次失敗（見 [ADR 0006](0006-session-runtime-profile.md) 的 probe 教訓）。
- 空的 / 壞的種子檔要能被重寫，不是「存在就跳過」——否則第一次啟動撞上的 onboarding
  對話會讓 CLI 一按 Enter 就結束容器。

## 後果

- 跨使用者的 `/resume` 消失，那正是目的。續命模型的形狀不變（rw 掛出的設定目錄 + 一致
  的 cwd），只是那份目錄從共用變成 per-user。
- **暫不做磁碟配額**：per-user 目錄不會隨帳號退場而清除，也沒有大小限制。個人／小團隊
  規模無感；要做是明確的維運決策，不是預設行為。
- 控制平面要能在 per-user 空間裡 mkdir 與寫種子檔，所以那個根目錄要能被控制平面
  （`APP_UID`）與 session 容器內的使用者**兩邊**寫（見 [ADR 0009](0009-containerized-deployment-docker-socket.md)
  的 uid 對齊）。
