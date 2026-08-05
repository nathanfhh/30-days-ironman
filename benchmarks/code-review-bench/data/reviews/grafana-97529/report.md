## 審查結論：Approved with Comments

> Critical 0 · Suggestion 5 · Nit 3 · 未驗證提問 3
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ✅ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ✅ | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | — | ❌ |

- **B 簡潔**（未通過）：同一個初始化錯誤被記錄兩次（F-006）。
- **E 架構**（未通過）：建構函式的錯誤路徑沒有回收已啟動的資源（F-003）；NewResourceServer 沒有 context 參數，啟動期的索引建置無法取消或設上限（F-004）；本 PR 的 trace context 修正沒有套用到 bleve.BuildIndex（F-007）。
- **F 資料取用與資料庫**（未通過）：bleve cache 這個共享 map 的併發保護不完整：TotalDocs 無鎖走訪（F-001），以及鎖粒度縮小後同一 key 的 BuildIndex 失去序列化（F-002）。
- **G 測試**（未通過）：本 PR 唯一的測試變更是「停用一個測試」（F-005），沒有為新的建構期初始化行為補任何測試；watcher_test.go:127-129 為了觸發舊 lazy init 而做的 health check 已經失效卻沒有跟著調整（F-008）。
- **H 非 Python 檔**（不適用）：diff 的五個檔案全部是 Go 原始碼，H 列舉的檔案型別（Vue 元件、Dockerfile、nginx.conf、docker-compose、Alembic migration）一個都沒有出現。Go 程式碼本身併入 A–G 與 I 一起評估。
- **I 回溯分析**（未通過）：沒有任何函式簽章改變，因此不存在被打斷的 caller；但 NewResourceServer 的語意變了——它從「便宜的建構」變成「會做 DB 連線、全量索引建置與 watcher 啟動的阻塞操作」，而 8 個呼叫點（sql/server.go:77、client.go:67、apistore/restoptions.go:52 與 :87、apistore/watcher_test.go:122 與 :151、dashboard/legacy_storage.go:28、sql/test/integration_test.go:54、resource/server_test.go:49）都沒有跟著調整。其中 legacy_storage.go:28 的 NewStore 會被 v0alpha1 / v1alpha1 / v2alpha1 三個 register.go 各呼叫一次，於是三個 server 都在 API 註冊時就把 watcher goroutine 開起來。相關的具體發現見 F-004、F-008。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 PR 實際上包了四件可以各自獨立的事：(1) 初始化搬到 NewResourceServer（server.go:258-262）；(2) bleve cache 鎖粒度調整（bleve.go:137-139）；(3) tracing context 傳遞修正（search.go:173、search.go:310、sql/backend.go:126）；(4) 停用 postgres integration test（module_server_test.go:34-37）。(2) 與 (3) 和「不要 lazy init」沒有因果關係，(4) 更是把唯一覆蓋 StorageServer 啟動路徑的整合測試關掉——恰好是 (1) 改動的那條路徑。拆開會讓每一項各自可回溯、可 revert；這個判斷屬於 maintainer，這裡只把事實列出。
- **該在這個時機做？**：本 PR 把「建置全部搜尋索引」搬進啟動路徑，同時在同一個 PR 裡停用 pkg/server/module_server_test.go 中唯一以 StorageServer 為 target 的整合測試（postgres 分支）。commit 歷史顯示作者先試過 wait 1 second、再試 big timeout、最後 put delay back to 500 ms 才改成 skip，這串軌跡本身就指向啟動時序改變。時機的疑慮不在於這個改動該不該做，而在於它上線時的啟動覆蓋率比改動前低。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | in_diff 0、outside_diff 10 |
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且預設的 Semgrep rules 目錄不存在，本次未執行 SAST 掃描。 |
| ty | 略過 | ty 未安裝（不在 PATH 上）；本 diff 亦不含 Python 檔，即使安裝也不會覆蓋到。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）；本 diff 不含 JavaScript/TypeScript 檔。 |
| codegraph | 略過 | codegraph 未安裝，未建立符號索引；本次的呼叫路徑列舉（NewResourceServer 的 8 個呼叫點、&server{} 建構點、BuildIndex 與 TotalDocs 的所有進入路徑）全部改以 grep 完成，涵蓋範圍以純文字搜尋為準。 |
| go toolchain (go build / go vet / go test -race) | 略過 | 此環境沒有 Go toolchain 也沒有網路，無法編譯、無法執行 go vet、更無法用 go test -race 驗證併發問題。本 diff 五個檔案全部是 .go，而唯一可用的 linter 是 ruff（只認 Python）——換句話說，本次變更的確定性工具覆蓋率是零，F-001 與 F-002 的併發推論全部來自人工閱讀原始碼，未經 race detector 證實。 |

<details>
<summary>Suggestion（5）</summary>

#### F-001 TotalDocs() 無鎖走訪 b.cache，而本次新增的呼叫點就緊接在 watch goroutine 啟動之後 — `pkg/storage/unified/search/bleve.go:144-152`

面向 F 資料取用與資料庫 · Suggestion

**問題**：bleve.go:144 的 TotalDocs 直接 `for _, v := range b.cache` 走訪 map，全程沒有取 cacheMu；同一個 map 由 bleve.go:137-139 在 cacheMu.Lock() 底下寫入。本次 diff 在 search.go:216 新增了 `s.search.TotalDocs()` 這個呼叫，而它上面十行（search.go:202-208）才剛啟動處理 watch 事件的 goroutine，那條路徑經 handleEvent（search.go:237）→ getOrCreateIndex（search.go:287）→ build（search.go:309）就會走到 BuildIndex 的 map 寫入。Go 的 map 不是併發安全的，讀寫重疊會直接 `fatal error: concurrent map read and map write` 中止整個行程，而且這個 fatal error 無法被 recover 攔下。需要說清楚範圍：TotalDocs 缺鎖不是本次引入的，bleve_index_metrics.go:96 的 prometheus Collect 早就會無鎖讀這個 map；但本次 diff 多開了一個觸發點，而且把索引建置從 lazy 改成啟動期由 initWorkers 條 goroutine 並行執行（search.go:177-193），重疊視窗比先前大得多。另外，這一段推論完全來自閱讀原始碼——此環境沒有 Go toolchain，無法用 `go test -race` 驗證。

**證據**：
- `pkg/storage/unified/search/bleve.go:144-152`
- `pkg/storage/unified/search/bleve.go:137-139`
- `pkg/storage/unified/resource/search.go:216`
- `pkg/storage/unified/resource/search.go:202-208`
- `pkg/storage/unified/resource/bleve_index_metrics.go:96`

**修復方向**：讓 TotalDocs 和 GetIndex 一樣取讀鎖：

```go
func (b *bleveBackend) TotalDocs() int64 {
	b.cacheMu.RLock()
	indexes := make([]*bleveIndex, 0, len(b.cache))
	for _, v := range b.cache {
		indexes = append(indexes, v)
	}
	b.cacheMu.RUnlock()

	var totalDocs int64
	for _, v := range indexes {
		c, err := v.index.DocCount()
		if err != nil {
			continue
		}
		totalDocs += int64(c)
	}
	return totalDocs
}
```

先在鎖內複製 index 清單、再到鎖外做 DocCount()，可以避免把可能較慢的 DocCount() 留在鎖內拖住 BuildIndex。

#### F-002 cacheMu 縮到只包住 map 寫入之後，同一個 key 的 BuildIndex 失去序列化 — `pkg/storage/unified/search/bleve.go:88-105`

面向 F 資料取用與資料庫 · Suggestion

**問題**：改動前 cacheMu.Lock() 涵蓋整個 BuildIndex，等於順帶把「同一個 key 同時被建置兩次」序列化掉；改動後鎖只包住 bleve.go:137-139 的 map 寫入，這層保護就沒了。getOrCreateIndex（search.go:287）自己的 TODO 註解已經寫明「We want to block while building the index and return the same index for the key」——它從來沒有 per-key 的守衛，先前是靠 BuildIndex 的粗鎖擋著。可以並行抵達同一 key 的路徑至少有三條：searchSupport.Search 的主索引（search.go:148）、同一函式的 federate 迴圈（search.go:160）、以及 watch goroutine 的 handleEvent（search.go:237）；gRPC Search 本身就是任意併發的。後果分兩種：size > FileThreshold 時兩條 goroutine 會同時對同一個 dir 呼叫 bleve.New（bleve.go:96-98），第二個會踩到既有目錄；memory-only 時則是整份索引被重建兩次、最後一個寫入蓋掉前一個，而先前拿到舊 index 的呼叫端會繼續寫進一個已經不在 cache 裡的物件。另外 bleve.go:102 與 :105 的 IndexTenants 指標也會被重複累加。

**證據**：
- `pkg/storage/unified/search/bleve.go:88-105`
- `pkg/storage/unified/search/bleve.go:137-139`
- `pkg/storage/unified/resource/search.go:287-306`
- `pkg/storage/unified/resource/search.go:148`
- `pkg/storage/unified/resource/search.go:160`
- `pkg/storage/unified/resource/search.go:237`

**修復方向**：把 per-key 的互斥補回來，而不是回頭鎖整個函式。可行的做法是在 bleveBackend 加一個 key → *sync.Mutex（或 singleflight.Group）的表，BuildIndex 先取該 key 的鎖、取得後再查一次 cache（double-check），命中就直接回傳既有 index，沒命中才真的建置；這樣既保住這次想要的「不同 key 可以並行」，也不會讓同一個 key 重複建置。golang.org/x/sync/singleflight 已經在 grafana 的相依裡，用 `singleflight.Group.Do(key.String(), ...)` 包住 build 是最小改動。

#### F-003 NewResourceServer 的錯誤路徑沒有呼叫 cancel()，也沒有回收 Init 已經啟動的 goroutine 與索引 — `pkg/storage/unified/resource/server.go:229-231`

面向 E 架構 · Suggestion

**問題**：server.go:229 用 context.WithCancel 建了 ctx/cancel 並存進 s，但新加的 server.go:258-261 在 Init 失敗時直接 `return nil, err`，cancel 沒有被呼叫。這不只是漏掉一個 cancel：s.Init（server.go:296-316）是分階段的，lifecycle.Init 成功、search.init 成功、卻在 initWatcher 失敗時，search.init 早就在 search.go:207 起了一條 `for { v := <-events }` 的 goroutine——而且它掛在 search.go:202 的 context.Background() 上，連 s.cancel 都關不掉它。於是一次失敗的建構會留下：一條永遠停不下來的事件處理 goroutine、已經建好的 bleve 索引（記憶體或磁碟檔案句柄），以及一個沒人取消的 context。呼叫端拿到的是 nil server，不可能再呼叫 Stop() 收尾（server.go 的 Stop 是 *server 的方法）。改動前這些資源只有在第一個請求進來時才會被建立，建構失敗不會留下任何東西。

**證據**：
- `pkg/storage/unified/resource/server.go:229-231`
- `pkg/storage/unified/resource/server.go:258-261`
- `pkg/storage/unified/resource/server.go:296-316`
- `pkg/storage/unified/resource/search.go:202-208`

**修復方向**：兩件事分開處理。(1) 在建構函式所有錯誤返回前呼叫 cancel()，包含 server.go:254 既有的那個分支：

```go
if err := s.Init(ctx); err != nil {
	cancel()
	return nil, err
}
```

(2) 讓 searchSupport 的 watch goroutine 掛在可取消的 context 上，而不是 search.go:202 的 context.Background()（那行的 `// new context?` 註解顯示作者自己也不確定），並在 handleEvent 迴圈裡改成 `select { case v, ok := <-events: ...; case <-ctx.Done(): return }`，讓 cancel() 真的能把它收掉。

#### F-004 NewResourceServer 沒有 context 參數，搬進建構期的索引建置因此無法取消也無法設上限 — `pkg/storage/unified/resource/server.go:182`

面向 E 架構 · Suggestion

**問題**：resource.NewResourceServer（server.go:182）不收 context，它自己用 context.Background() 造一個（server.go:229）並拿去做 Init。這在改動前無所謂——建構期不做事；改動後這個 ctx 要承載的是 search.init 裡對每個 namespace/resource 的全量索引建置（search.go:177-193，透過 ListIterator 以 `Limit: 1000000000000` 掃全表）。結果是呼叫端交出去的取消訊號完全接不上：sql.NewResourceServer（sql/server.go:22）明明收了一個 ctx，卻在 sql/server.go:77 呼叫 resource.NewResourceServer(opts) 時把它丟掉，而那個 ctx 正是 dskit BasicService 的 start context（sql/service.go:113）。也就是說模組啟動被取消、或上層想給啟動加一個 deadline，都到不了正在跑的索引建置。這裡只主張「取消訊號接不上」這個機制事實；至於實際啟動會不會因此超時，見 Q-002。

**證據**：
- `pkg/storage/unified/resource/server.go:182`
- `pkg/storage/unified/resource/server.go:229-231`
- `pkg/storage/unified/resource/server.go:258-261`
- `pkg/storage/unified/sql/server.go:22-24`
- `pkg/storage/unified/sql/server.go:77`
- `pkg/storage/unified/sql/service.go:113`
- `pkg/storage/unified/resource/search.go:177-193`

**修復方向**：把 context 沿著呼叫鏈補進去：`func NewResourceServer(ctx context.Context, opts ResourceServerOptions) (ResourceServer, error)`，在 server.go:229 用 `context.WithCancel(claims.WithClaims(ctx, ...))` 承接呼叫端的 ctx，並讓 sql/server.go:77 把手上的 ctx 傳下去。若不想改簽章，退而求其次是在 ResourceServerOptions 上加一個 InitTimeout（預設值明確寫出），在 server.go:258 以 context.WithTimeout 包住 Init，讓啟動期的索引建置至少有一個可設定的上限。

#### F-005 本 PR 唯一的測試變更是把 postgres 整合測試停掉，而它覆蓋的正是本 PR 改動的啟動路徑 — `pkg/server/module_server_test.go:34-37`

面向 G 測試 · Suggestion

**問題**：TestIntegrationWillRunInstrumentationServerWhenTargetHasNoHttpServer 以 modules.StorageServer 為 target 起一個 module server，再打 /metrics 確認 instrumentation server 有起來——這條路徑會經過 sql/service.go 的 start，也就是本 PR 讓它變成阻塞式初始化的那條路。module_server_test.go:31-33 已經跳過 sqlite3，這次再加上 postgres（:34-37），這個測試就只剩 mysql 會實際執行。而測試裡等待伺服器起來的方式是 module_server_test.go:55 的 `time.Sleep(500 * time.Millisecond)` 這個固定睡眠：本 PR 把「建立所有搜尋索引」搬到 Run() 之前必須完成的階段，這個固定 500ms 與新的啟動時序之間本來就是競態關係。commit 歷史（wait 1 second before querying metrics → try with big timeout, see if fixes CI → put delay back to 500 ms → skips postgres integration test）顯示作者確實先往時序方向試過才改成 skip。把測試停掉不會讓時序問題消失，只會讓它下次以別的形式出現，而且是在覆蓋率已經降到單一資料庫的情況下。

**證據**：
- `pkg/server/module_server_test.go:34-37`
- `pkg/server/module_server_test.go:31-33`
- `pkg/server/module_server_test.go:55`
- `pkg/storage/unified/resource/server.go:258-261`

**修復方向**：先把固定睡眠換成輪詢，讓測試不再依賴一個猜出來的延遲——例如 `require.Eventually(t, func() bool { res, err := client.Get(...); return err == nil && res.StatusCode == 200 }, 30*time.Second, 200*time.Millisecond)`，再看 postgres 是否仍然失敗。如果換成輪詢之後 postgres 就過了，skip 可以整段拿掉；如果仍然失敗，那就是真的 bug，此時 skip 至少要附上一個 issue 連結（現在的 `// TODO - fix this test for postgres` 沒有任何可追蹤的出口），並在 PR 描述裡點名「本次合併後 StorageServer 啟動路徑只剩 mysql 有整合測試覆蓋」。

</details>

<details>
<summary>Nit（3）</summary>

#### F-006 同一個初始化錯誤被記錄兩次，訊息字串完全相同 — `pkg/storage/unified/resource/server.go:260`

面向 B 簡潔 · Nit

**問題**：server.go:314 在 once.Do 內部已經記了 `s.log.Error("error initializing resource server", "error", s.initErr)`，新增的 server.go:260 又記了一次一模一樣的訊息與同一個 error。看 log 的人會看到兩筆重複的 ERROR，難以判斷是同一次失敗還是兩次不同的失敗。

**證據**：
- `pkg/storage/unified/resource/server.go:260`
- `pkg/storage/unified/resource/server.go:314`

**修復方向**：刪掉 server.go:259-261 裡的 log，只留下回傳：

```go
if err := s.Init(ctx); err != nil {
	cancel()
	return nil, err
}
```

記錄的責任留在 Init 一處即可（或反過來，把 :314 的 log 拿掉、只在建構端記一次），重點是同一個錯誤只出現一次。

#### F-007 本 PR 的 trace context 修正沒有套到 bleve.BuildIndex，那段 span 因此接不到子節點 — `pkg/storage/unified/search/bleve.go:88`

面向 E 架構 · Nit

**問題**：本 PR 在三個地方把 `_, span := tracer.Start(ctx, ...)` 改成 `ctx, span := ...`（search.go:173、search.go:310、sql/backend.go:126），目的就是修 trace 傳遞。但 bleve.go:88 的 BuildIndex 仍然是 `_, span := b.tracer.Start(ctx, ...)`，而這個函式正是本次修改過的函式（鎖粒度那段就在它裡面）。實際效果是 BuildIndex 這個 span 永遠是葉節點：真正的工作發生在 builder 回呼裡，而那個回呼（search.go:326-331）用的是 searchSupport.build 的 ctx，所以 ListIterator 的 span 會掛到 `unified_search.Build` 底下、成為 `unified_search.bleve.BuildIndex` 的兄弟而不是子節點。看 trace 的人會以為 BuildIndex 幾乎不花時間。

**證據**：
- `pkg/storage/unified/search/bleve.go:88`
- `pkg/storage/unified/resource/search.go:173`
- `pkg/storage/unified/resource/search.go:310`
- `pkg/storage/unified/sql/backend.go:126`
- `pkg/storage/unified/resource/search.go:326-331`

**修復方向**：把 bleve.go:88 一併改成 `ctx, span := b.tracer.Start(ctx, tracingPrexfixBleve+"BuildIndex")`，並把這個 ctx 傳給 builder 回呼（例如把 builder 的簽章擴成 `func(ctx context.Context, index resource.ResourceIndex) (int64, error)`，或在 SearchBackend 的介面文件裡註明回呼要沿用傳入的 ctx），讓 ListIterator 的 span 真的掛在 BuildIndex 底下。

#### F-008 watcher_test.go 裡「發一次 health check 讓 server 完成初始化」的做法已經失效，註解也隨之過時 — `pkg/storage/unified/apistore/watcher_test.go:122-129`

面向 G 測試 · Nit

**問題**：watcher_test.go:127-129 寫著 `// Issue a health check to ensure the server is initialized`，然後呼叫 server.IsHealthy 來觸發舊的 lazy init。本 PR 已經把 IsHealthy 裡的 `if err := s.Init(ctx); err != nil` 拿掉（server.go 的 IsHealthy 現在只剩 `return s.diagnostics.IsHealthy(ctx, req)`），初始化改由 NewResourceServer 完成。這幾行現在既不做它宣稱的事，註解也在誤導下一個讀者——測試不會失敗，所以它會一直留著。

**證據**：
- `pkg/storage/unified/apistore/watcher_test.go:122-129`
- `pkg/storage/unified/resource/server.go:934-936`

**修復方向**：把 watcher_test.go:127-129 這三行刪掉；NewResourceServer 回傳成功就代表已初始化，`require.NoError(t, err)` 已經涵蓋。若想保留對 health endpoint 的檢查，把註解改成陳述實際意圖（例如 `// sanity check the diagnostics endpoint`）再留著。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 postgres 上的 TestIntegrationWillRunInstrumentationServerWhenTargetHasNoHttpServer 究竟為什麼只在 Drone 失敗？是本 PR 的啟動時序改變造成的，還是 postgres 環境本身的差異？

面向 G 測試

**背景**：module_server_test.go:36 的 skip 訊息寫「test not working with postgres in Drone. Works locally.」。依 F 維度的原則，「作者在本機跑過而且會過」證明的是它在那台機器上會過，不構成這個斷言的反證；而本機與 CI 之間的差異（DB 連線延遲、資料量、CPU 配額、poll interval 的實際表現）恰好都會影響 module_server_test.go:55 那個固定 500ms 睡眠與新啟動時序之間的競態。此環境沒有 Go toolchain、沒有 postgres、也沒有網路，無法重現任一邊，所以無法判定失敗是時序造成還是別的原因。

**如何確認**：在 Drone 的 postgres job 上把 skip 拿掉、把 module_server_test.go:55 的 time.Sleep 換成 require.Eventually 輪詢後重跑：若通過，失敗就是啟動時序造成的、skip 可以移除；若仍失敗，附上該次失敗的完整測試輸出與 module server 的啟動 log，就能區分是 instrumentation server 沒起來，還是索引建置在 postgres 上真的卡住。

#### Q-002 在一個有實際資料量的 Grafana 實例上，把全量索引建置搬到 NewResourceServer 之後，啟動時間是否會超過 dskit 模組的啟動容忍度，或讓使用者看到明顯變長的無服務期間？

面向 E 架構

**背景**：機制面已經確認：search.init（search.go:177-193）會對 GetResourceStats 回傳的每一組 namespace/resource 送出一個 build，每個 build 透過 ListIterator 以 `Limit: 1000000000000` 掃描該資源的全部資料（search.go:326-331），並行度只由 cfg.IndexWorkers 決定；而這一切現在都發生在 NewResourceServer 回傳之前（server.go:258），沒有 timeout 也接不到呼叫端的取消訊號（F-004）。但「這會花多久」取決於資料量、資料庫與磁碟，此環境無法編譯也無法執行，因此無法把機制推進成一個關於後果的斷言。

**如何確認**：在一個具代表性資料量的環境（或既有的 load test 環境）上量測開啟 unifiedStorageSearch 前後 sql/service.go start 到 running 的時間，並對照 dskit 模組的啟動 timeout 設定；同時看 IndexMetrics.IndexCreationTime 這個既有指標在該環境的實際分佈——search.go:218 已經在記錄它，資料應該拿得到。

#### Q-003 把 initWatcher 移到 search.init 之後（server.go:303-311），對於索引建置期間寫入的事件是否會造成遺漏？

面向 E 架構

**背景**：已經檢查過能想到的反證：server 層 broadcaster 唯一的消費者是 Watch RPC（server.go:774），而 Watch 只能在 NewResourceServer 回傳之後才被呼叫，所以 broadcaster 晚一點啟動並不會讓任何既有訂閱者漏事件；s.mostRecentRV 也只在 Watch 內部使用（server.go:811、:852），同樣不受影響。至於搜尋索引自己的事件視窗——ListIterator 快照與 search.go:203 WatchWriteEvents 訂閱之間的空隙——是 search.init 內部既有的順序，本 PR 沒有動它。剩下無法在此環境確認的是：sql backend 的 poller 從 listLatestRVs（sql/backend.go:560）取得的起點，在索引建置花掉數分鐘之後是否仍然涵蓋期間的所有寫入。

**如何確認**：一個整合測試：在索引建置進行中持續寫入資源，等 NewResourceServer 回傳後查詢搜尋索引與 Watch stream，確認期間的寫入都到齊；或由熟悉 sql backend poller 保留視窗的維護者直接說明 listLatestRVs 的起點在長時間建置後是否仍然安全。

</details>
