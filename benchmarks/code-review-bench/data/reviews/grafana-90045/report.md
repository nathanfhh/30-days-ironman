## 審查結論：Request Changes

> Critical 1 · Suggestion 6 · Nit 5 · 未驗證提問 3
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：Delete 把未加值的 logger 放進 context（F-009），另有 const/回傳值/註解漂移等小問題（F-010）。
- **B 簡潔**（未通過）：四個寫入方法各自複製一份「goroutine + 10 秒 WithTimeoutCause + errors.New」樣板（F-008）。
- **C 安全**（未通過）：Delete 把使用者可控的物件名稱寫進 Prometheus 的 kind label，造成無上限的標籤基數（F-002）。沒有發現新的輸入驗證、SQL 組字串或憑證問題；本次沒有 SAST/相依掃描工具可用，這一格只代表人工閱讀的結果。
- **D API 慣例**（不適用）：本次變更全部在 storage 層（rest.Storage / rest.Getter 等 k8s 介面的實作），沒有新增或修改任何 HTTP endpoint、URL 命名、verb 語義或驗證 schema，這一維度沒有可判定的對象。
- **E 架構**（未通過）：非同步 legacy 寫入的錯誤只進 metric、沒有 log 也沒有 panic recovery（F-003）；三處 metric 記進錯誤的 histogram（F-004）。
- **F 資料取用與資料庫**（未通過）：非同步 legacy 寫入沿用會被取消的 request context，雙寫在 mode 3 下會系統性地只寫成一半（F-001）。
- **G 測試**（未通過）：新增的 playlist mode 3 整合測試實際上跑在 mode 1（F-006）；單元測試完全沒有驗證 legacy 端（F-007）；registry 宣告方式不一致（F-011）。
- **H 非 Python 檔**（未通過）：本次 diff 全部是非 Python 檔（Go 原始碼與 go.work.sum）。go.work.sum 新增一行完整重複的項目並夾帶無關相依（F-012）；Go 原始碼本身在 A–G、I 各維度判定。
- **I 回溯分析**（未通過）：Delete / DeleteCollection 對 storage 錯誤的處理由「容忍 NotFound 後仍刪 legacy」改成「任何錯誤都直接 return」，legacy 端不再被清理（F-005）。函式簽章本身沒變，DualWriterMode3 對外仍滿足 DualWriter 介面（pkg/apiserver/rest/dualwriter.go:20-27 的編譯期斷言）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：同一個 MR 夾帶了與 mode 3 無關的改動：go.work.sum 新增 16 行（含一行完整重複的 github.com/grafana/grafana/pkg/apimachinery），以及 pkg/apiserver/rest/dualwriter_mode1_test.go:138 單獨刪掉一行 registry 宣告。這些拆出去會讓 mode 3 的實作 diff 乾淨很多。
- **該在這個時機做？**：pkg/apiserver/rest/dualwriter.go:198-220 目前只實作 mode 1 ↔ mode 2 的切換，並留著 `#TODO add support for other combinations`。也就是說設定檔把 unified_storage_mode 設成 3，SetDualWritingMode 仍會回傳 Mode1，這份 mode 3 實作在合併後無法由設定啟用（僅能靠手動改 kvstore）。實作先落地不是問題，但要清楚知道它此刻沒有真實流量會經過，本次新增的整合測試也因此不是在跑 mode 3（見 F-006）。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。本 MR 有動 go.work.sum，這正是 trivy 會看的檔案，所以相依面向本次沒有工具覆蓋。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且本機的 Semgrep 規則目錄不存在，本次未執行 SAST 掃描。 |
| ruff | 已執行 | in_diff 0、outside_diff 10 |
| ty | 略過 | ty 未安裝（不在 PATH 上）；且本次 diff 沒有 Python 檔，即使安裝也不會產生任何診斷。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）；本次 diff 沒有 JavaScript/TypeScript 檔。 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號索引；本次的呼叫端追蹤（SetDualWritingMode、NewDualWriter、updateWrapper、recordLegacyDuration/recordStorageDuration）全部改用 grep 逐一確認。 |
| go toolchain（build / vet / test） | 略過 | 審查環境沒有 Go toolchain，也沒有網路可以取得相依套件，因此本次無法編譯、無法執行 go vet、race detector 或任何測試。所有關於「會不會編譯過」「測試會不會綠」的判斷都只來自閱讀原始碼，沒有任何一項是實際跑出來的。 |
| ncr-fresh-eyes（subagent） | 略過 | 本執行環境沒有可用的 subagent 派送工具，無法派出 fresh eyes。依 skill 規定不得由主 agent 自行模擬（主 agent 此時已讀過 review-dimensions.md，看到的東西必然被 checklist 框過），因此本報告缺少「未經框架的第一眼」這一層，findings 的 source 全部是 dimension。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派出獨立的品質覆核 subagent。本報告只經過 report_model.py 的機械驗證（結論與 findings 一致、每個 finding 有 fix、每個維度有判定），沒有經過第二個人格對措辭、嚴重度校準與證據充分性的覆核。 |

### Critical

#### F-001 非同步 legacy 寫入沿用 request context，請求一結束就被取消，mode 3 的雙寫會系統性地只寫成一半 — `pkg/apiserver/rest/dualwriter_mode3.go:50-57`

面向 F 資料取用與資料庫 · Critical

**問題**：四個寫入方法都用 `go func()` 把 legacy 寫入丟到背景，goroutine 裡的 context 是 `context.WithTimeoutCause(ctx, ...)`，而 `ctx` 就是這個 HTTP 請求的 context。net/http 的合約是「ServeHTTP 回傳時，request context 即被取消」，而這裡 `return created, err` 之後 handler 立刻收尾，所以衍生出來的 context 在 legacy 的 DB 交易還沒 commit 之前就已經 Done。這條取消會一路傳到底：legacy_storage.go:115 把 ctx 交給 playlist service，最後進到 xorm_store.go:31 的 `WithTransactionalDbSession(ctx, ...)`，也就是 database/sql，而 database/sql 在 context 已取消時會直接讓查詢失敗。結果是 mode 3 名義上雙寫、實際上只有 unified storage 收到資料，legacy DB 逐筆漏掉，而且因為 F-003 連一行 log 都沒有，這件事只會表現成 dual_writer_legacy_duration_seconds{is_error="true"} 悄悄變高。

反證我找過了，兩個方向都不成立：(1) 這條路徑沒有任何地方把 context 脫鉤 —— `klog.NewContext(ctx, log)` 只是塞入 logger value，`WithTimeoutCause` 是衍生而非脫離父節點，程式碼裡沒有 `context.WithoutCancel`；(2) mode 3 目前確實不容易被啟用（見 F-006），但不是完全不可達：pkg/apiserver/rest/dualwriter.go:170 的 `toMode` 收 "3"，只要 kvstore 裡該 entity 的值是 "3"，SetDualWritingMode 就會回傳 Mode3。也就是說這不是「永遠不會發生」，而是「一旦按照本 MR 的意圖把 mode 3 打開就必然發生」。

另外要說清楚的是：mode 1 用的是同一個寫法（pkg/apiserver/rest/dualwriter_mode1.go:53-55、90-93、157-160），所以這是沿用既有 pattern 而不是憑空發明。但兩者的代價不同 —— dualwriter.go:92-94 明講 mode 1 的非同步那一側是「best effort，為了收 metric」，掉了只是少一筆數據；mode 3 的非同步那一側是 legacy SQL storage，是遷移期間的回退依據，掉了就是資料發散。同一個 pattern 在這裡的後果嚴重得多，所以在 mode 3 需要另外處理，而不是因為 mode 1 也這樣寫就算了。順帶一提，程式碼裡刻意寫了 10 秒 timeout，本身就說明作者預期這個 goroutine 會活過請求 —— 目前的父節點選擇讓那個預期落空。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3.go:50-57`
- `pkg/apiserver/rest/dualwriter_mode3.go:108-114`
- `pkg/apiserver/rest/dualwriter_mode3.go:134-141`
- `pkg/apiserver/rest/dualwriter_mode3.go:161-167`
- `pkg/registry/apis/playlist/legacy_storage.go:115`
- `pkg/services/playlist/playlistimpl/xorm_store.go:31`

**修復方向**：把 goroutine 的 context 從 request context 脫鉤，但保留其中的 value（namespace、使用者身分等，legacy_storage.go:102 的 `request.NamespaceInfoFrom(ctx, true)` 依賴它們，所以不能直接換成 `context.Background()`）。go.mod 是 go 1.22.4，`context.WithoutCancel` 可用：

```go
asyncCtx := context.WithoutCancel(ctx)
go func() {
	ctx, cancel := context.WithTimeoutCause(asyncCtx, 10*time.Second, errLegacyCreateTimeout)
	defer cancel()
	...
}()
```

四個方法（Create / Delete / Update / DeleteCollection）都要改。改完請務必補一個會真正驗證的測試：用一個已取消的 parent context 呼叫 `dw.Create`，然後同步等待 legacy mock 確實被呼叫且沒有收到 `context.Canceled`（見 F-007 對等待機制的建議）。順帶建議把 mode 1 的同一段一起處理，或至少在那裡留一行註解說明為什麼 mode 1 可以接受掉資料。

<details>
<summary>Suggestion（6）</summary>

#### F-002 Delete 把物件名稱當成 kind label 送進 Prometheus，標籤基數沒有上限 — `pkg/apiserver/rest/dualwriter_mode3.go:106`

面向 C 安全 · Suggestion

**問題**：`d.recordStorageDuration(false, mode3Str, name, method, startStorage)` 的第三個參數傳的是被刪除物件的 `name`，但 metrics.go:23 定義這個位置的 label 是 `kind`（recordStorageDuration 的參數名叫 `name` 是既有的命名誤導，實際餵給 `WithLabelValues` 的是 kind 這一格）。同一個檔案裡其他 12 處全部傳 `options.Kind`，只有這一行例外，所以這是筆誤而不是設計。

後果是 label 基數無上限：物件名稱由使用者決定（playlist UID 是隨機字串），每刪一個物件就在 client_golang 裡生出一個新的 native histogram，而且不會被回收 —— 這是 Grafana process 自己的記憶體洩漏，同時也把 /metrics 的輸出撐大、讓 Prometheus 端跟著長。

沒有列為 Critical 的理由要一併說明：pkg/apiserver/rest/dualwriter_mode1.go:155 已經有一模一樣的筆誤，而 mode 1 是目前實際會跑到的模式，所以這條 diff 帶來的邊際風險有限；真正該修的是兩處一起。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3.go:106`
- `pkg/apiserver/rest/metrics.go:18-23`
- `pkg/apiserver/rest/metrics.go:59-62`

**修復方向**：把 `name` 換成 `options.Kind`：

```go
d.recordStorageDuration(false, mode3Str, options.Kind, method, startStorage)
```

同時建議一併修掉 dualwriter_mode1.go:155 的同一個問題。若之後真的想在 metric 上區分個別物件，那應該走 log 或 trace，不要走 label。另外可以考慮把 metrics.go:54-62 兩個函式的參數名由 `name` 改成 `kind`，讓呼叫端一眼看得出該傳什麼 —— 這個命名正是這次踩到坑的原因。

#### F-003 背景 legacy 寫入的失敗只進 metric、沒有任何 log，goroutine 也沒有 panic recovery — `pkg/apiserver/rest/dualwriter_mode3.go:50-57`

面向 E 架構 · Suggestion

**問題**：改動前，legacy 寫入失敗會留下訊息，例如 `log.WithValues("object", created).Error(err, "unable to create object in legacy storage")`（舊版 Create）與 `log.Error(errLeg, "could not update object in legacy store")`（舊版 Update）。現在四個 goroutine 收到 `err` 之後只拿它算 `err != nil` 餵給 metric，錯誤本身連同它的內容一起丟掉。在 mode 3 下 legacy 寫入失敗＝兩個 store 內容不一致，而值班的人手上只會有一個布林 label，沒有物件名稱、沒有錯誤字串，無從得知是哪一筆、為什麼失敗。同一個檔案的同步路徑（例如 line 44、71、87）都有 log，只有 goroutine 裡沒有，這個落差看起來不是刻意的。

第二件事更硬：Go 的 panic 只能被同一個 goroutine 的 recover 接住。k8s apiserver 的 `WithPanicRecovery` middleware 保護的是 handler 那個 goroutine，這裡新開的 goroutine 不在它的保護範圍內，legacy store 或 `deleteValidation` 一旦 panic（例如 nil pointer），整個 Grafana process 會直接掛掉，而不是回一個 500。這在改動前不會發生，因為 legacy 呼叫是同步的。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3.go:50-57`
- `pkg/apiserver/rest/dualwriter_mode3.go:108-114`
- `pkg/apiserver/rest/dualwriter_mode3.go:134-141`
- `pkg/apiserver/rest/dualwriter_mode3.go:161-167`

**修復方向**：抽一個共用的 helper，同時處理 log 與 recover（也順便解掉 F-008 的重複）：

```go
func (d *DualWriterMode3) async(ctx context.Context, log klog.Logger, cause string, fn func(context.Context) error) {
	go func() {
		defer func() {
			if r := recover(); r != nil {
				log.Error(fmt.Errorf("%v", r), "panic in async legacy write", "stack", string(debug.Stack()))
			}
		}()
		ctx, cancel := context.WithTimeoutCause(context.WithoutCancel(ctx), 10*time.Second, errors.New(cause))
		defer cancel()
		if err := fn(ctx); err != nil {
			log.Error(err, "legacy write failed", "cause", cause)
		}
	}()
}
```

呼叫端只留「量時間、記 metric、回傳 error」。log 至少要帶 name/kind/method（現在的 `log` 已經有這些值），這樣兩邊不一致時才追得回是哪一筆。

#### F-004 三處 metric 記進了錯誤的 histogram：storage 失敗記成 legacy、legacy 呼叫記成 storage — `pkg/apiserver/rest/dualwriter_mode3.go:45`

面向 E 架構 · Suggestion

**問題**：line 45（Create）與 line 129（Update）在 `d.Storage.Create/Update` 失敗的分支呼叫 `d.recordLegacyDuration(true, ...)`，而且用的是 `startStorage`；也就是 unified storage 的失敗被記到 `dual_writer_legacy_duration_seconds{is_error="true"}`。line 166（DeleteCollection 的 goroutine）方向相反：它包的是 `d.Legacy.DeleteCollection`，卻呼叫 `d.recordStorageDuration`。

對照可以確認這是筆誤不是設計：同檔的成功分支（line 48、132）用 recordStorageDuration，另外三個 goroutine（line 56、113、140）用 recordLegacyDuration，pkg/apiserver/rest/dualwriter_mode2.go:61 在同樣的 storage 失敗分支也是用 recordStorageDuration。

後果是可觀測性直接反過來：mode 3 的 unified storage 寫入失敗永遠不會出現在 storage 的錯誤序列，而 legacy 的錯誤率會被灌進 unified storage 的失敗；DeleteCollection 的 legacy 耗時則完全不會出現在 legacy 序列裡。任何建在這兩個 metric 上的告警都會指錯方向 —— 而 F-001 造成的 legacy 寫入失敗，正好就是要靠這組 metric 才看得見。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3.go:45`
- `pkg/apiserver/rest/dualwriter_mode3.go:129`
- `pkg/apiserver/rest/dualwriter_mode3.go:166`

**修復方向**：line 45、129 改成 `d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)`；line 166 改成 `d.recordLegacyDuration(err != nil, mode3Str, options.Kind, method, startLegacy)`。修完建議在單元測試裡對 registry 做一次 `testutil.CollectAndCount` / `GatherAndCompare`，把「哪個 metric 應該動」釘住 —— 目前測試傳進去的 registry 從頭到尾沒有被檢查過，所以這三個筆誤不會被任何測試抓到。

#### F-005 Delete / DeleteCollection 改成 storage 一出錯就 return，legacy 端不再被清理（原本刻意容忍 NotFound） — `pkg/apiserver/rest/dualwriter_mode3.go:100-114`

面向 I 回溯分析 · Suggestion

**問題**：舊的 Delete 是這樣寫的：`if err != nil { if !apierrors.IsNotFound(err) { log; return } }` —— 刻意讓 NotFound 掉下去，接著仍然呼叫 `d.Legacy.Delete`。舊的 DeleteCollection 更寬鬆，storage 失敗只 log 不 return，一樣會往下刪 legacy。新版兩者都改成任何錯誤就 `return`，而且 `apierrors` 這個 import 也一起被移除了，所以這不是漏看，是整段換掉。

這個容忍在遷移情境下是有意義的：mode 3 之前的模式並不保證每筆資料都已經同步到 unified storage，一個只存在於 legacy 的物件，在新版之下呼叫 DELETE 會拿到 unified storage 的 NotFound、直接回 404，legacy 那一列永遠不會被刪掉，變成沒有人能透過 API 清掉的孤兒資料。DeleteCollection 的情況一樣。

我沒有在 diff 裡找到補上這段語義的地方，也沒有找到任何說明為什麼可以拿掉；如果這是刻意的（例如認定進 mode 3 之前資料一定已經同步完），那它需要一行註解，因為讀者從程式碼看不出來。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3.go:100-114`
- `pkg/apiserver/rest/dualwriter_mode3.go:152-167`

**修復方向**：兩個選項，擇一並在程式碼裡講清楚：

1. 保留原本的行為 —— 把 `apierrors` 加回來，NotFound 時不 return，仍然派出 legacy 的刪除：

```go
res, async, err := d.Storage.Delete(ctx, name, deleteValidation, options)
if err != nil && !apierrors.IsNotFound(err) {
	log.Error(err, "unable to delete object in storage")
	d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
	return res, async, err
}
```

2. 若確認 mode 3 的前提是資料已完全同步、unified storage 的 NotFound 就代表真的不存在，那就在 Delete 上方補一行註解寫明這個前提，並在 F-006 修好之後用整合測試把它蓋住。

#### F-006 新增的三個 playlist 「mode 3」整合測試實際上跑在 mode 1，本次的正式碼一行都沒被涵蓋 — `pkg/tests/apis/playlist/playlist_test.go:132`

面向 G 測試 · Suggestion

**問題**：我把整條路走了一遍：測試設定的 `DualWriterDesiredModes` 只會被 testinfra.go:389-395 寫進 ini 的 `unified_storage_mode` 區段，config.go 讀回來後由 helper.go:173 交給 `SetDualWritingMode`。而 SetDualWritingMode（dualwriter.go:176-222）的邏輯是：kvstore 沒有這個 entity（全新測試實例必然如此）→ `!valid || !ok` 成立 → `currentMode = Mode1` 並寫回 "1"；接著只有 `desiredMode == Mode2 && currentMode == Mode1` 和 `desiredMode == Mode1 && currentMode == Mode2` 兩個轉換分支，desiredMode 是 Mode3 時兩個都不成立，函式回傳 **Mode1**。line 220 的 `#TODO add support for other combinations of desired and current modes` 就是這件事的自白。

所以 helper.go:183 拿到的是 Mode1，`NewDualWriter` 走 `case Mode1`，建出來的是 DualWriterMode1。三個新測試會通過 —— 因為 doPlaylistTests 的斷言在 mode 1 下也成立 —— 但它們沒有執行過 dualwriter_mode3.go 的任何一行。這正是本 MR 最需要被測到的部分（F-001 的 context 取消、F-005 的刪除語義），而目前它是綠燈的假覆蓋。

反證我找過了：grep 全 repo 的 `Mode3`，正式碼裡唯一產生 DualWriterMode3 的入口是 dualwriter.go:117-119 的 `case Mode3`，而它只在 `currentMode == Mode3` 時成立；能讓 currentMode 變成 Mode3 的只有 dualwriter.go:181 從 kvstore 讀到字串 "3"，但沒有任何程式碼會寫入 "3"（`kvs.Set` 只在 line 192/204/214 被呼叫，寫的都是 Mode1/Mode2）。測試也沒有預先塞 kvstore。

**證據**：
- `pkg/tests/apis/playlist/playlist_test.go:132`
- `pkg/tests/apis/playlist/playlist_test.go:191`
- `pkg/tests/apis/playlist/playlist_test.go:287`
- `pkg/apiserver/rest/dualwriter.go:188-222`
- `pkg/services/apiserver/builder/helper.go:169-183`
- `pkg/tests/testinfra/testinfra.go:389-395`

**修復方向**：兩步：

1. 在 `SetDualWritingMode` 補上通往 Mode3 的轉換（至少 `desiredMode == Mode3 && currentMode == Mode2` 這一段，以及對應的 gate 條件），或者在整合測試裡先把 kvstore 種成 "3" 再啟動實例。
2. 讓測試自己證明模式是對的，而不是靠設定推論 —— 例如在 `doPlaylistTests` 之前斷言實際生效的 mode，或在 helper 上加一個查詢當前 mode 的入口。目前這三個 t.Run 的名字宣稱的事情，程式碼裡沒有任何一處在檢查。

在 (1) 完成之前，建議先把這三個新測試標成 `t.Skip("mode 3 not reachable until SetDualWritingMode supports it")`，並在 skip 訊息裡指回 dualwriter.go:220 的 TODO —— 名字寫著 mode 3 但跑的是 mode 1 的綠燈測試，比沒有測試更容易誤導後面的人。

#### F-007 mode 3 的單元測試完全沒有驗證 legacy 端的非同步寫入，而那正是 mode 3 的定義 — `pkg/apiserver/rest/dualwriter_mode3_test.go:17`

面向 G 測試 · Suggestion

**問題**：六個 Test 都是同一個形狀：設定 mock、呼叫 dw.X、比對回傳值等於 `exampleObj`。沒有任何一處呼叫 `m.AssertExpectations(t)`、`m.AssertCalled(...)` 或 `m.AssertNumberOfCalls(...)`，所以「legacy 有沒有被寫」從頭到尾沒有被斷言過。TestMode3_Delete 與 TestMode3_DeleteCollection 的 testCase struct 甚至沒有 setupLegacyFn 欄位。

這一點在改動之後特別要緊：legacy 寫入現在跑在 goroutine 裡，測試主體不等它，`t.Run` 的 closure 回傳時它可能還沒被排程。也就是說即使補上 AssertCalled，在目前的結構下也會 flaky —— 需要一個明確的同步點。而 F-001 描述的失效（legacy 寫入因為 context 被取消而完全沒發生）在單元測試裡剛好看不見，因為測試傳的是 `context.Background()`，永遠不會被取消。這組測試無法區分「legacy 寫成功」和「legacy 根本沒被呼叫」，這也解釋了為什麼 F-001/F-004/F-005 都能在測試全綠的情況下存在。

另外 dualwriter_mode3_test.go:80-133（TestMode3_Get）這種「mock 回傳 exampleObj，然後斷言拿到 exampleObj」的寫法，驗證的是 mock 而不是受測程式碼 —— TestMode3_Get、TestMode3_List 幾乎整個是這個形狀。至少 TestMode3_Create 有比較實質的 `acc.GetResourceVersion()` 斷言，可以當成其他幾個的參考。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3_test.go:17`
- `pkg/apiserver/rest/dualwriter_mode3_test.go:80`
- `pkg/apiserver/rest/dualwriter_mode3_test.go:135`
- `pkg/apiserver/rest/dualwriter_mode3_test.go:189`
- `pkg/apiserver/rest/dualwriter_mode3_test.go:243`
- `pkg/apiserver/rest/dualwriter_mode3_test.go:297`

**修復方向**：給非同步寫入一個可等待的同步點，然後真的斷言。最小改法是讓 mock 在被呼叫時關掉一個 channel：

```go
legacyCalled := make(chan struct{})
m.On("Create", mock.Anything, mock.Anything, mock.Anything, mock.Anything).
	Run(func(args mock.Arguments) { close(legacyCalled) }).
	Return(exampleObj, nil)
// ...
select {
case <-legacyCalled:
case <-time.After(2 * time.Second):
	t.Fatal("legacy write never happened")
}
m.AssertExpectations(t)
```

更乾淨的做法是讓 DualWriterMode3 持有一個可注入的 `func(func())`（正式環境是 `go f()`，測試環境是同步執行），這樣連 channel 都不需要。另外請補一個「parent context 已取消時 legacy 仍然寫得進去」的案例，那是 F-001 的回歸測試。

</details>

<details>
<summary>Nit（5）</summary>

#### F-008 四個寫入方法各自複製一份 goroutine + 10 秒 timeout 樣板 — `pkg/apiserver/rest/dualwriter_mode3.go:50-57`

面向 B 簡潔 · Nit

**問題**：同一段「開 goroutine → WithTimeoutCause(ctx, time.Second*10, errors.New(...)) → defer cancel → 呼叫 legacy → 記 metric」重複四次，只有 cause 字串和呼叫的方法不同。四次已經過了 Rule of Three。實際代價不是抽象的：這份重複已經產生了三個不一致 —— line 166 呼叫錯 record 函式（F-004）、line 134-138 的 `defer cancel()` 位置與其他三處不同、`time.Second*10` 這個門檻散在四個地方，要調整就得改四次且很容易漏。errors.New 每次呼叫都重新配置一個永遠不變的 error 值也是同一個問題的副作用。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3.go:50-57`
- `pkg/apiserver/rest/dualwriter_mode3.go:108-114`
- `pkg/apiserver/rest/dualwriter_mode3.go:134-141`
- `pkg/apiserver/rest/dualwriter_mode3.go:161-167`

**修復方向**：抽成 F-003 fix 裡那個 helper，四處共用；順手把 cause 提成套件層級的 `var errLegacyCreateTimeout = errors.New("legacy create timeout")` 等，並把 10 秒抽成具名常數（例如 `const legacyWriteTimeout = 10 * time.Second`），讓下次調整只需要動一個地方。

#### F-009 Delete 把未加值的 d.Log 放進 context，name / kind / method 三個欄位在下游全部消失 — `pkg/apiserver/rest/dualwriter_mode3.go:96-97`

面向 A 風格 · Nit

**問題**：第 96 行剛做好 `log := d.Log.WithValues("name", name, "kind", options.Kind, "method", method)`，第 97 行卻寫 `ctx = klog.NewContext(ctx, d.Log)` —— 放進 context 的是沒加值的 `d.Log`。同檔的 Create（line 39）、Get（line 66）、List（line 82）、Update（line 123）、DeleteCollection（line 150）全部放的是 `log`，只有 Delete 例外。後果是任何從 context 取 logger 的下游程式碼（legacy store、unified store）在刪除路徑上都少了 name/kind/method，而刪除正好是最需要知道「哪一筆」的操作。方法內自己用的 `log` 沒受影響，所以這個問題只在跨層時才看得出來，肉眼複查很難抓到。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3.go:96-97`

**修復方向**：改成 `ctx = klog.NewContext(ctx, log)`。

#### F-010 幾處小的可讀性問題：method 應為 const、Create 回傳必為 nil 的 err、const 宣告位置、註解與實作不同步 — `pkg/apiserver/rest/dualwriter_mode3.go:33`

面向 A 風格 · Nit

**問題**：四件小事，都不影響行為：(1) 六個方法都寫 `var method = "create"`，這些是不會變的字面值，Go 的慣例是 `const method = "create"`。(2) line 59 `return created, err` —— 走到這裡 `err` 必為 nil（line 43 的分支已經 return 掉非 nil 的情況），寫成 `return created, nil` 讀者才不用回頭確認；Delete、Update 的結尾也有同樣的情形，但那兩處 `async`/`err` 的組合較不明顯，可一併整理。(3) `const mode3Str = "3"` 夾在 `Mode()` 和 `Create()` 中間（line 33），照慣例應該和其他宣告一起放在檔案頂端 import 之後。(4) 兩處註解已經和實作對不上：line 35 說 Create「writes to LegacyStorage and Storage」，實際順序相反且 legacy 是非同步；line 119 說 Update「writes first to Storage and then to LegacyStorage」，順序沒錯但沒提到第二段是非同步且不影響回傳結果 —— 而「回傳成功不代表 legacy 已寫入」正是這個改動最需要讓下一位讀者知道的事。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode3.go:33`
- `pkg/apiserver/rest/dualwriter_mode3.go:37`
- `pkg/apiserver/rest/dualwriter_mode3.go:59`
- `pkg/apiserver/rest/dualwriter_mode3.go:35`
- `pkg/apiserver/rest/dualwriter_mode3.go:119`

**修復方向**：(1) `var method =` 改 `const method =`。(2) line 59 改 `return created, nil`。(3) `mode3Str` 移到 import 之後與型別宣告之前。(4) 兩處註解改寫，明確寫出「先寫 Storage 並立刻回傳，legacy 寫入在背景非同步進行，其失敗不會反映在回傳值上」。

#### F-011 測試用的 prometheus registry 宣告方式在三個檔案間不一致 — `pkg/apiserver/rest/dualwriter_mode1_test.go:27`

面向 G 測試 · Nit

**問題**：dualwriter_mode1_test.go:27 有套件層級的 `var p = prometheus.NewRegistry()`。本次 diff 從 TestMode1_Get 拿掉了區域的 `p := prometheus.NewRegistry()`（改用套件層級那個），但同一檔案其他測試與新寫的 dualwriter_mode3_test.go:119（TestMode3_Get）仍然各自宣告區域的 `p`，遮蔽了套件層級的變數。行為上沒差別（metrics.go:18-39 的 histogram 本來就是套件層級全域，換 registry 也換不掉共用狀態），但同一組測試裡三種寫法並存，下一個人會以為區域那份是有意義的隔離。另外 mode1_test 的這一行修改和 mode 3 沒有關係，是這個 MR 夾帶的（見 intent_check）。

**證據**：
- `pkg/apiserver/rest/dualwriter_mode1_test.go:27`
- `pkg/apiserver/rest/dualwriter_mode1_test.go:138`
- `pkg/apiserver/rest/dualwriter_mode3_test.go:119`

**修復方向**：選一種寫法貫徹到底。既然 registry 換不掉全域 histogram，最簡單的是全部用套件層級的 `p`，把 dualwriter_mode3_test.go:119 和 mode1_test 其餘幾處的區域宣告一併刪掉；若之後真的需要每個測試獨立的 metric 狀態，那要改的是 metrics.go 讓 histogram 隨 registry 建立，而不是在測試裡各建各的 registry。

#### F-012 go.work.sum 新增一行完全重複的項目，並夾帶與本次變更無關的相依 — `go.work.sum:404`

面向 H 非 Python 檔 · Nit

**問題**：新增的 16 行裡，`github.com/grafana/grafana/pkg/apimachinery v0.0.0-20240701135906-559738ce6ae1/go.mod h1:DkxMin+qOh1Fgkxfbt+CUfBqqsCQJMG9op8Os/irBPA=` 這一行在檔案裡出現了兩次（原本就有一份，這次又加了一份完全相同的）。其餘新增的項目 —— grafana-azure-sdk-go/v2、prometheus-alertmanager、otel exporters、genproto —— 和 dual writer mode 3 沒有關係，看起來是在某個非乾淨狀態下跑 `go work sync` 帶出來的殘留。這不會讓建置失敗（go.work.sum 是雜湊清單，重複只是冗餘），但會讓之後 bisect 或看這個 commit 的人要花時間確認這些相依變動是不是刻意的。commit 訊息「Update dependencies」也沒說明為什麼需要它們。

**證據**：
- `go.work.sum:404`
- `go.work.sum:408`

**修復方向**：在乾淨的 base 上重跑一次 `go work sync`（或 `go mod tidy` 後再 sync）重新產生 go.work.sum，只保留這次真正需要的變動；如果重跑後這些項目仍然出現，就在 MR 描述裡寫一句它們從何而來。重複那一行直接刪掉即可。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 Update 不再用 updateWrapper 包住 objInfo，改成把原始 objInfo 直接交給 Legacy.Update，兩個 store 最後寫入的內容還會一致嗎？

面向 F 資料取用與資料庫

**背景**：改動前的 Update 會先 `d.Storage.Get` 拿舊物件、算出 `updated`，再用 `&updateWrapper{upstream: objInfo, updated: obj}` 呼叫 legacy —— updateWrapper.UpdatedObject（pkg/apiserver/rest/dualwriter.go:142-144）直接忽略 oldObj、回傳已經寫進 unified storage 的那個物件，等於強制兩邊寫一樣的東西。現在 goroutine 把原始 objInfo 交給 legacy，legacy store 會用它自己的舊物件重新跑一次 `UpdatedObject`。對單純的 PUT（defaultUpdatedObjectInfo 直接回傳 client 送來的物件）兩者結果相同；對 PATCH 或帶 transformer 的更新，legacy 端是基於 legacy 的舊狀態計算，結果可能與 unified storage 那一份不同。mode 1（dualwriter_mode1.go:250）與 mode 2（dualwriter_mode2.go:298、313）都還留著 updateWrapper，所以 mode 3 是這三者裡唯一拿掉的。我在 diff 與 MR 內容裡沒找到說明，也無法在這個環境編譯或執行測試來實測兩邊的差異。

**如何確認**：作者說明這是刻意的以及理由；或寫一個整合測試，對同一個物件送 PATCH（例如 merge-patch 只改 spec 的一個欄位），之後分別從 unified storage 與 legacy DB 讀回來比對內容。若確實刻意，請在 Update 上方補註解說明為什麼 mode 3 不需要 updateWrapper。

#### Q-002 goroutine 與 apiserver 的回應序列化同時持有 obj / options 指標，會不會構成 data race？

面向 F 資料取用與資料庫

**背景**：Create 的 goroutine（dualwriter_mode3.go:55）拿 `obj` 呼叫 Legacy.Create，同一時間 apiserver 正在把 `created` 序列化成 HTTP 回應；Update 的 goroutine（line 139）也共用 `objInfo` 與 `options`。以 genericregistry.Store 的實作來說 `created` 通常是解碼出來的新物件、與 `obj` 不同指標，所以多半沒事 —— 但這取決於實際的 Storage 實作，而且改動前 legacy 呼叫是同步的、不存在這個併發窗口。我在這個環境沒有 Go toolchain，無法用 `go test -race` 驗證。

**如何確認**：在 CI 上以 `-race` 跑 pkg/apiserver/rest 與 pkg/tests/apis/playlist 的整合測試（且要在 F-006 修好、真的跑得到 mode 3 之後）。若確認有 race，goroutine 應改為傳入 `obj.DeepCopyObject()`，做法可參照 dualwriter_mode1.go:51 的 `createdCopy := created.DeepCopyObject()`。

#### Q-003 `options.Kind` 在真實請求上是否幾乎都是空字串，讓所有 metric 都塞在 kind="" 這一條序列裡？

面向 E 架構

**背景**：六個方法都用 `options.Kind`（來自 metav1.CreateOptions / DeleteOptions 等的 TypeMeta）當 metric 的 kind label。k8s client 送出的 options 物件通常不會填 TypeMeta，所以這個值很可能是空的。單元測試看不出來，因為測試是自己手動塞 `TypeMeta{Kind: "foo"}` 進去的（dualwriter_mode3_test.go:146、154、254、261）。這是 mode 1 / mode 2 既有的寫法，本次只是沿用，所以不列為本次的發現，但如果整組 dual writer metric 的 kind label 其實一直是空的，那 F-002 與 F-004 的修法就要一併重新考慮該從哪裡取 kind。

**如何確認**：在跑得起來的環境對 playlist 打一次 create/delete，然後看 /metrics 上 `grafana_dual_writer_storage_duration_seconds` 的 kind label 實際是什麼。若確實為空，改從 request context 取 `request.RequestInfoFrom(ctx).Resource`，或由建構 DualWriterMode3 時傳入的 GroupResource 取得。

</details>
