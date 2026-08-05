## 審查結論：Request Changes

> Critical 2 · Suggestion 4 · Nit 3 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ✅ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | — | ✅ |

- **A 風格**（未通過）：三處風格問題，都不影響正確性：t.Skip 訊息大小寫與 repo 內 130 處慣例不一致（F-007）、migrator import 放錯分組（F-008）、fetchIDs 先組 SQL 再檢查前置條件（F-009）。另外 xorm_store.go:536-537 等處用 x, y 當回傳值名稱，可讀性偏低，修法併入 F-001。
- **B 簡潔**（未通過）：兩個 Critical 都在這裡：六行 r.log.Error 的除錯輸出（F-001）與 cleanup ticker 從 10 分鐘改成 1 分鐘（F-002），都是開發過程留下、應該在送審前移除的鷹架。最後一個 commit 叫 lint，代表作者有做過一次收尾，但沒清到這兩處。除此之外抽象拆得乾淨：untilDoneOrCancelled 取代了原本綁死單一 SQL 字串的 executeUntilDoneOrCancelled，讓批次工作可以是多敘述的 callback，沒有過度設計。
- **D API 慣例**（不適用）：本次沒有新增或修改任何 HTTP endpoint、路由、請求或回應 schema。變更範圍是 repository 層的刪除實作、背景工作的排程間隔與測試，URL 命名、HTTP 動詞語意、認證授權、批次原子性等 API 慣例項目都無從套用。
- **F 資料取用與資料庫**（未通過）：兩件 Suggestion：SQLite 參數上限的繞道只針對單一 dialect、而且是用設定值而非實際 ID 數量判斷（F-003）；SQLite 分支用 O(n²) 的字串累加組 IN 清單（F-004）。正面的部分：ctx 有一路傳進 fetchIDs 與 deleteByIDs 的 WithDbSession，比原本只在外層 select 檢查取消更即時；deleteByIDs 對空 ids 提早返回（xorm_store.go:598-600），擋掉了 strings.Repeat(",?", -1) 會 panic 以及產生非法 IN () 的兩條路；fetchIDs 對空 condition 也有守衛，避免整表掃描。「先查後刪」之間的不一致風險作者已在 xorm_store.go:521-525 註明並接受，另見 Q-002。
- **G 測試**（未通過）：測試品質整體是進步的：斷言看的是實際資料庫狀態（分型別計數、tag 計數）而不是「有回傳東西」，t.Cleanup 補上了 annotation_tag 的清除、修掉了原本 tag 資料跨案例殘留的問題，順帶修正了舊碼 for tagID := range []int{1, 2} 實際寫入 tag_id 0 與 1 的錯誤（現在明確寫 1 與 2）。但兩件事要處理：新增的大批次案例實際上沒有讓 annotation 刪除路徑跨過參數上限（F-005）；t.Cleanup 註冊在建資料之後，失敗時會連鎖污染後續案例（F-006）。
- **H 非 Python 檔**（不適用）：本次 diff 的三個檔案全部是 Go 原始碼，確實屬於「非 Python 檔」，但本維度的檢查項目（多分支 UI 元件、Vue、Dockerfile、nginx.conf、docker-compose、Alembic migration）在這份 diff 中一項都沒有出現，沒有可判定的對象。Go 程式碼本身的風格、簡潔、安全、架構、資料存取與測試已分別在 A、B、C、E、F、G 完成審查，不在此重複。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：pkg/services/cleanup/cleanup.go:77 把整個 CleanUpService 的 ticker 從 10 分鐘改成 1 分鐘。這個檔案管的是八個彼此無關的清理工作（暫存檔、快照、儀表板版本、圖片、annotation、邀請、短網址、查詢歷史），與 annotation 分批刪除沒有任何依賴關係，也沒有對應的設定項或文件說明。它看起來是為了在開發時快速觸發 annotation 清理而調的，不屬於這個 MR 的範圍——詳見 F-002。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。本次 diff 沒有動 go.mod / go.sum 或任何設定檔，但這是人工確認的結果，不是掃描結果。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），本次未執行 SAST 掃描。變更全部是 Go，即使安裝了也需要 Go 規則集，本次環境兩者皆無。 |
| ruff | 已執行 | ruff 有執行且正常結束，但它只檢查 Python。本次 diff 的三個檔案全部是 Go（.go），ruff 完全沒有覆蓋到受審程式碼。回報的 10 件都落在 repo 內既有的 Python 工具腳本、與本次變更無關，僅計數揭露不列為發現。請把本次的 Go 程式碼視為「沒有任何靜態分析工具掃過」。 · in_diff 0、outside_diff 10 |
| ty | 略過 | ty 未安裝（不在 PATH 上）。且 ty 是 Python 型別檢查器，對本次的 Go 變更本來就不適用。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。且 oxlint 針對 JavaScript/TypeScript，對本次的 Go 變更不適用。 |
| go toolchain (go vet / go build / go test / golangci-lint) | 略過 | 本次執行環境沒有安裝 Go toolchain，也沒有對外網路，因此無法編譯、無法執行 go vet，更無法實際跑 TestIntegrationAnnotationCleanUp。所有關於「這段會不會編譯過」「測試會不會通過」的判斷都是靜態閱讀原始碼得出的，不是執行結果。F-005 的批次大小推算是照 cleanup_test.go:249-258 的分型邏輯手算的，建議合併前實際跑一次驗證。 |
| codegraph | 略過 | codegraph 未安裝，Phase 0 的 init 未執行。維度 E 與 I 的呼叫端盤點改用 grep 完成（見 dimensions.I 的 note），結果已逐項列出證據。 |
| ncr-fresh-eyes（subagent） | 略過 | 本次執行環境沒有可用的 subagent 派發工具（Agent / Task 皆不存在），因此 Phase 3 第 1 步的獨立初讀沒有執行，也沒有以主 agent 自行模擬。本報告的發現全部來自掃描與九大維度，缺少一份未被本 skill 分類框架塑形過的視角。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派發 subagent，因此 Phase 4 第 3 步的報告品質覆核沒有執行。報告只通過 report_model.py 的機械驗證，未經第二方檢查。 |

### Critical

#### F-001 清理流程留下六行 Error 等級的除錯 log，且會把整批 ID 印進日誌 — `pkg/services/annotations/annotationsimpl/xorm_store.go:534`

面向 B 簡潔 · Critical

**問題**：這六行都落在成功路徑上，而且是 r.log.Error。有三個地方可以確認它們是除錯鷹架而不是有意的觀測點：其一，每一行都帶 "err", err，但在 xorm_store.go:531-533 / 551-553 / 573-575 已經對 err != nil 提早 return，所以列印時 err 必然是 nil——這是複製貼上留下的痕跡；其二，訊息大小寫不一致（Annotations to clean by time / cleaned annotations by time），並且把內部 SQL 片段 cond 一起印出來；其三，同一個檔案裡唯一另一處日誌是 xorm_store.go:410 的 r.log.Info，可見這個 repository 的正常做法不是 Error。實際影響有兩層：(1) 等級錯誤——cleanup 是每個 Grafana 實例都會跑的背景工作，正常運作也會持續產生 error 等級事件，任何以 log level 為條件的告警規則都會被誤觸；(2) 量體——"ids", ids 會把整個 []int64 展開，每批最多 AnnotationCleanupJobBatchSize 筆，本 PR 測試示範的 32767 筆會變成單行數萬個數字，每批一次、每輪清理數批。與 F-002 的 1 分鐘 ticker 疊加後放大十倍。這不是風格問題，而是會直接汙染生產環境可觀測性的缺陷，合併前必須處理。

**證據**：
- `pkg/services/annotations/annotationsimpl/xorm_store.go:534`
- `pkg/services/annotations/annotationsimpl/xorm_store.go:537`
- `pkg/services/annotations/annotationsimpl/xorm_store.go:554`
- `pkg/services/annotations/annotationsimpl/xorm_store.go:557`
- `pkg/services/annotations/annotationsimpl/xorm_store.go:576`
- `pkg/services/annotations/annotationsimpl/xorm_store.go:579`

**修復方向**：整批移除這六行。移除後三個 callback 可以直接收斂成一行，順帶解決 x, y 這組沒有語意的變數名稱，例如 CleanOrphanedAnnotationTags 的 callback 改成：

```go
return untilDoneOrCancelled(ctx, func() (int64, error) {
	cond := fmt.Sprintf(`NOT EXISTS (SELECT 1 FROM annotation a WHERE annotation_id = a.id) %s`, r.db.GetDialect().Limit(r.cfg.AnnotationCleanupJobBatchSize))
	ids, err := r.fetchIDs(ctx, "annotation_tag", cond)
	if err != nil {
		return 0, err
	}
	return r.deleteByIDs(ctx, "annotation_tag", ids)
})
```

如果確實想保留清理進度的觀測能力，改成 r.log.Debug 並且只記數量不記內容（"count", len(ids)），把 cond 與 ids 拿掉。呼叫端 pkg/services/cleanup/cleanup.go:74-80 已經有 logger.Debug 印出刪除筆數，重複程度也要一併考慮。

#### F-002 cleanup 背景工作的 ticker 從 10 分鐘改成 1 分鐘，影響八個與本 PR 無關的清理工作 — `pkg/services/cleanup/cleanup.go:77`

面向 B 簡潔 · Critical

**問題**：cleanup.go:77 由 time.NewTicker(time.Minute * 10) 改為 time.NewTicker(time.Minute * 1)。這個 ticker 驅動的不只是 annotation 清理，而是 cleanup.go:95-103 列出的八個工作：暫存檔、過期快照、過期儀表板版本、過期圖片、舊 annotation、過期使用者邀請、失效短網址、過期查詢歷史。這些工作與本 PR 要解決的「annotation 批次刪除在 MySQL 上互卡」沒有任何關係，改動也沒有伴隨設定項、文件或 CHANGELOG。

找過反證，沒有找到：grep -rn 'NewTicker' pkg/services/cleanup/ 只有這一處，沒有其他地方把頻率調回去；本次 diff 的另外兩個檔案都在 annotationsimpl 之下，沒有任何補償邏輯；也沒有新增設定讓部署端調整。

影響有兩點特別值得注意。第一，cleanup.go:89 的 const timeout = 9 * time.Minute 是照著 10 分鐘週期挑的（留 1 分鐘餘裕）；週期縮成 1 分鐘之後，單輪允許執行的時間變成週期的九倍，而 ticker channel 只有 1 的緩衝，實際結果是一輪結束後下一輪幾乎立刻開始，清理從「每 10 分鐘一次」變成近乎連續執行。第二，CleanUpService 雖然注入了 ServerLockService（cleanup.go:55），但 Run 與 clean 都沒有使用它——grep 確認整個 pkg/services/cleanup/ 只有第 29、35、55 行提到它，都是宣告與賦值。這代表多實例部署下每個實例各自跑這個迴圈，資料庫承受的清理查詢壓力會是實例數乘以十倍頻率。這正是本 PR 想避開的併發情境，卻被同一份 diff 放大了。

這一行看起來是為了在開發時不必等十分鐘才觸發 annotation 清理而調的暫時值，合併前必須還原。

**證據**：
- `pkg/services/cleanup/cleanup.go:77`
- `pkg/services/cleanup/cleanup.go:89`
- `pkg/services/cleanup/cleanup.go:95-103`

**修復方向**：把 pkg/services/cleanup/cleanup.go:77 還原成 time.NewTicker(time.Minute * 10)。如果縮短週期是刻意的產品決策而非除錯遺留，它應該獨立成另一個 MR，並且至少附上：(1) 為什麼 10 分鐘不夠；(2) 對應調整 cleanup.go:89 的 timeout，讓它小於新的週期；(3) 評估在沒有 ServerLockService 保護下、多實例同時執行的資料庫負載；(4) 若要保留彈性，改成從設定讀取（例如 [cleanup] 區段新增一個 interval 鍵，比照 pkg/setting/setting.go:710 的 cleanupjob_batchsize 寫法），而不是寫死。

<details>
<summary>Suggestion（4）</summary>

#### F-003 參數上限的繞道只做了 SQLite，且用設定值而非實際 ID 筆數判斷 — `pkg/services/annotations/annotationsimpl/xorm_store.go:605-608`

面向 F 資料取用與資料庫 · Suggestion

**問題**：xorm_store.go:608 的條件是 r.db.GetDBType() == migrator.SQLite && r.cfg.AnnotationCleanupJobBatchSize > sqliteParameterLimit。有兩個問題。

第一，綁定值判斷得看實際筆數。條件用的是設定的批次大小，不是 len(ids)。批次大小設大、但某一批只撈回十筆時，仍然會走字串拼接而非參數化路徑——功能上沒錯，但把 F-004 的成本與拼接風險套用到不需要的情況上。

第二，參數上限不是 SQLite 獨有的。PostgreSQL 的 extended query protocol 以 int16 承載參數數量，單一敘述上限 65535 個綁定參數；MySQL 的 placeholder 數量同樣有上限。pkg/setting/setting.go:710 是 section.Key("cleanupjob_batchsize").MustInt64(100)，預設 100、沒有任何上限鉗制，部署端可以自由調高。一旦調到超過該 dialect 的上限，else 分支的 strings.Repeat(",?", len(ids)-1) 就會產生驅動拒絕的敘述——與本 PR 為 SQLite 修掉的正是同一類失敗，只是門檻不同。

值得強調的是這個上限是本次變更新引入的：改動前 CleanAnnotations 送出的是一句完全不帶參數的 DELETE（帶子查詢），根本沒有參數數量這個維度。找過反證：grep 確認 cleanupjob_batchsize 除了 setting.go:710 的讀取與 xorm_store.go 的三處使用外沒有其他鉗制點，dialect 層 pkg/services/sqlstore/migrator/dialect.go 的 Limit / LimitOffset 也只負責產生 LIMIT 子句，不涉及參數數量。實務上要踩到需要刻意把 cleanupjob_batchsize 調到六萬以上，因此列為 Suggestion 而非 Critical。

**證據**：
- `pkg/services/annotations/annotationsimpl/xorm_store.go:605-608`
- `pkg/services/annotations/annotationsimpl/xorm_store.go:615-618`
- `pkg/setting/setting.go:710`

**修復方向**：把上限視為 dialect 的性質而不是 SQLite 的特例，並改用實際筆數判斷。最直接的做法是在 deleteByIDs 內部再切一層子批次，讓所有 dialect 都留在參數化路徑上：

```go
func (r *xormRepositoryImpl) deleteByIDs(ctx context.Context, table string, ids []int64) (int64, error) {
	// 取各 dialect 都安全的下限；SQLite 3.32 之前是 999。
	const maxParams = 999
	var affected int64
	for start := 0; start < len(ids); start += maxParams {
		chunk := ids[start:min(start+maxParams, len(ids))]
		n, err := r.deleteChunk(ctx, table, chunk)
		affected += n
		if err != nil {
			return affected, err
		}
	}
	return affected, nil
}
```

這樣可以完全刪掉字串拼接分支，連帶解決 F-004。若要保留現有結構，至少把條件改成 len(ids) > sqliteParameterLimit，並把 sqliteParameterLimit 的註解補上「其他 dialect 也有上限，這裡只處理 SQLite」，讓下一個維護者知道邊界在哪。

#### F-004 SQLite 分支用 O(n²) 的字串累加組出 IN 清單 — `pkg/services/annotations/annotationsimpl/xorm_store.go:609-613`

面向 F 資料取用與資料庫 · Suggestion

**問題**：values = fmt.Sprintf("%s, %d", values, v) 每一圈都會把已累積的整個字串重新配置並複製一次。這條路徑上的 ids 長度最多等於 AnnotationCleanupJobBatchSize，而本 PR 的測試案例（cleanup_test.go:101）就用了 32767：最終字串約 200 KB，平均每次複製約 100 KB，一批下來搬動的位元組數在 GB 等級，而且 untilDoneOrCancelled 會讓這件事每批重複一次。這是純粹的浪費——輸出結果與用 strings.Builder 完全相同。註解說這條路徑存在是為了處理大批次，但實作方式恰好在大批次時最慢。

**證據**：
- `pkg/services/annotations/annotationsimpl/xorm_store.go:609-613`

**修復方向**：採用 F-003 的分塊方案可以直接讓這段程式消失。若要維持現有結構，改用 strings.Builder 或 strings.Join 即可：

```go
parts := make([]string, len(ids))
for i, v := range ids {
	parts[i] = strconv.FormatInt(v, 10)
}
sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, strings.Join(parts, ", "))
```

strconv 與 strings 都已經在這個檔案的 import 清單中（xorm_store.go:8-9），不需要新增相依。

#### F-005 新增的大批次測試沒有讓 annotation 刪除路徑跨過參數上限，實際守住迴歸的是 annotation_tag 路徑 — `pkg/services/annotations/annotationsimpl/cleanup_test.go:97-111`

面向 G 測試 · Suggestion

**問題**：案例名稱是 should not fail if batch size is larger than SQLITE_MAX_VARIABLE_NUMBER for SQLite >= 3.32.0，設定 createAnnotationsNum: 40003、annotationCleanupJobBatchSize: 32767、三種型別各 MaxCount: 1。照 cleanup_test.go:249-258 的分型邏輯（i%3==0 為 alert、i%3==1 為 API、其餘為 dashboard），40003 筆會被切成 13335 / 13334 / 13334。

CleanAnnotations 是每種型別各跑一次，條件為 xorm_store.go:549 的 LimitOffset(32767, 1)，也就是 LIMIT 32767 OFFSET 1。所以單批最多只會撈回 13334 個 ID——遠低於 SQLite >= 3.32.0 的 32766 上限。換句話說，即使把 deleteByIDs 的 SQLite 繞道整段拿掉，annotation 的刪除仍然會走參數化路徑、仍然會通過。

真正超過上限的是 tag 清理：40000 筆 annotation 被刪後留下 80000 筆孤兒 annotation_tag，CleanOrphanedAnnotationTags 用的是 Limit(32767)（xorm_store.go:571），前兩批各撈回 32767 個 ID，這才是唯一跨過 32766 的地方。測試會失敗、迴歸有被守住，但守住的路徑與案例名稱所指的不是同一條，而且這件事在測試碼裡完全看不出來。

連帶的脆弱性在於：只要有人日後把 createAnnotationsNum 調小到 16383 以下（仍然是個「很大」的數字），孤兒 tag 就不足 32767 筆，這個案例會安靜地退化成不再測試任何東西。同時 40003 筆 annotation 加 80006 筆 tag 的建置成本不低——透過 InsertMulti 以 500 筆為單位共 241 個敘述，在 GRAFANA_TEST_DB=mysql / postgres 的整合測試環境下每一個都是一次來回。

**證據**：
- `pkg/services/annotations/annotationsimpl/cleanup_test.go:97-111`
- `pkg/services/annotations/annotationsimpl/cleanup_test.go:249-258`
- `pkg/services/annotations/annotationsimpl/xorm_store.go:549`

**修復方向**：讓超限批次確實落在 annotation 刪除路徑上，並把意圖寫進測試。最省的做法是讓單一型別就超過上限，而不是把資料平均分給三種型別——例如給 createTestAnnotations 一個「全部建成 alert annotation」的選項，然後只設定 AlertingAnnotationCleanupSetting，資料量壓到 32767 + MaxCount + 少量餘裕即可，比目前的 40003 更小且更精準。

若要保留現況的三型別分佈，請至少把註解補上，說明超限批次來自 annotation_tag 的孤兒清理而非 annotation 本身，並在 createAnnotationsNum 旁註明它與 32766 的關係（需要 createAnnotationsNum - 3 > 2 × 16383），避免日後調小數字時無聲失效。另外建議新增一個 batch size 略小於上限的對照案例，確認參數化路徑同樣正確——目前兩條分支只有一條被測到。

#### F-006 t.Cleanup 註冊在建立測試資料之後，任一子測試失敗就會連鎖污染後續案例 — `pkg/services/annotations/annotationsimpl/cleanup_test.go:116-128`

面向 G 測試 · Suggestion

**問題**：子測試的順序是：先 createTestAnnotations（116）、再兩行 assertAnnotationCount / assertAnnotationTagCount（117-118），最後才 t.Cleanup（120）。這三個前置步驟內部用的都是 require，失敗時會 t.FailNow() 進而 runtime.Goexit；此時第 120 行還沒執行，該子測試的清除函式從未被註冊，資料就留在表裡。

下一個子測試的 createTestAnnotations 會以 ID: int64(i + 1)（cleanup_test.go:241）寫入明確主鍵，撞上殘留資料就是主鍵衝突，後續每個案例都跟著失敗。原本一個清楚的失敗會被放大成一串看不出源頭的錯誤。

找過反證，確認沒有其他機制會把表清乾淨：db.InitTestDB 只在 cleanup_test.go:22 呼叫一次、位於 for 迴圈之外，而它的截斷動作（pkg/services/sqlstore/sqlstore.go:711 與 739 的 TruncateDBTables）只在呼叫當下發生；迴圈內沒有任何其他清除點。這一點在改動前不成立——舊碼的資料只建一次、且沒有明確指定 ID。

**證據**：
- `pkg/services/annotations/annotationsimpl/cleanup_test.go:116-128`
- `pkg/services/annotations/annotationsimpl/cleanup_test.go:241`
- `pkg/services/annotations/annotationsimpl/cleanup_test.go:22`

**修復方向**：把 t.Cleanup 移到子測試函式的第一行，讓它在任何可能失敗的操作之前就完成註冊：

```go
t.Run(test.name, func(t *testing.T) {
	t.Cleanup(func() {
		err := fakeSQL.WithDbSession(context.Background(), func(session *db.Session) error {
			_, deleteAnnotationErr := session.Exec("DELETE FROM annotation")
			_, deleteAnnotationTagErr := session.Exec("DELETE FROM annotation_tag")
			return errors.Join(deleteAnnotationErr, deleteAnnotationTagErr)
		})
		assert.NoError(t, err)
	})

	createTestAnnotations(t, fakeSQL, test.createAnnotationsNum, test.createOldAnnotationsNum)
	...
})
```

這個順序也讓「清除涵蓋本子測試的所有寫入」這件事在閱讀上更明確。

</details>

<details>
<summary>Nit（3）</summary>

#### F-007 t.Skip 訊息大小寫與 repo 內既有慣例不一致 — `pkg/services/annotations/annotationsimpl/cleanup_test.go:20`

面向 A 風格 · Nit

**問題**：兩處都寫成 t.Skip("Skipping integration test")。以 grep 統計 pkg/services/ 底下的整合測試跳過訊息：小寫的 skipping integration test 有 130 處，大寫 Skipping 只有 2 處——而這 2 處正是本次新增的。慣例的價值在一致，這種訊息也常被 CI 的日誌過濾規則用字串比對抓取。

**證據**：
- `pkg/services/annotations/annotationsimpl/cleanup_test.go:20`
- `pkg/services/annotations/annotationsimpl/cleanup_test.go:153`

**修復方向**：兩處都改成 t.Skip("skipping integration test")，與其餘 130 處對齊。

#### F-008 migrator import 放進了子套件分組，與同檔案的 sqlstore import 分開 — `pkg/services/annotations/annotationsimpl/xorm_store.go:12-21`

面向 A 風格 · Nit

**問題**：新增的 github.com/grafana/grafana/pkg/services/sqlstore/migrator 被放在第一個 grafana 分組，與 annotations/accesscontrol 並列；但 github.com/grafana/grafana/pkg/services/sqlstore 本來就在下面第二個分組（xorm_store.go:20）。同一個套件樹被拆到兩個分組，本套件其他檔案（例如 annotations.go:5-14）的分組方式也不是這樣。

**證據**：
- `pkg/services/annotations/annotationsimpl/xorm_store.go:12-21`

**修復方向**：把 "github.com/grafana/grafana/pkg/services/sqlstore/migrator" 移到第二個分組，緊接在 "github.com/grafana/grafana/pkg/services/sqlstore" 之後，讓 sqlstore 相關的 import 待在一起。

#### F-009 fetchIDs 先組出 SELECT 才檢查 condition 是否為空 — `pkg/services/annotations/annotationsimpl/xorm_store.go:584-590`

面向 A 風格 · Nit

**問題**：第 585 行先 sql := fmt.Sprintf(`SELECT id FROM %s`, table)，第 586-588 行才檢查 condition == "" 並提早返回。守衛存在、行為正確，但把前置條件放在建構動作之後，讀起來像是「先做再檢查」，也讓這個守衛的重要性（避免整表掃描後全表刪除）不那麼醒目。

**證據**：
- `pkg/services/annotations/annotationsimpl/xorm_store.go:584-590`

**修復方向**：把守衛提到函式第一行，例如：

```go
func (r *xormRepositoryImpl) fetchIDs(ctx context.Context, table, condition string) ([]int64, error) {
	if condition == "" {
		return nil, fmt.Errorf("condition must be supplied; cannot fetch IDs from entire table")
	}
	sql := fmt.Sprintf(`SELECT id FROM %s WHERE %s`, table, condition)
	...
}
```

順帶把兩次 Sprintf 併成一次。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 整個重構的前提是「單敘述批次子查詢在 MySQL 上會與併發寫入互卡」，但這件事在本分支裡沒有任何可重現的憑據。這個 deadlock 是在哪裡觀察到的？

面向 F 資料取用與資料庫

**背景**：xorm_store.go:521 的註解寫的是 seem to deadlock with concurrent inserts on MySQL——用詞本身就是推測。本次新增的測試（cleanup_test.go:97-111）測的是批次大小與參數上限，沒有任何併發寫入的情境，所以沒有測試會因為改回單敘述寫法而失敗。本次環境沒有 Go toolchain 也沒有 MySQL，無法自行驗證。這件事影響的不是這個 PR 對不對，而是這個設計決策日後守不守得住：一個沒有測試、只靠一句 seem to 的註解支撐的結構，很容易在下一次「這裡好像可以簡化成一句 SQL」的重構中被還原。

**如何確認**：在註解或 PR 描述裡附上事件來源——issue 編號、MySQL 錯誤日誌，或 SHOW ENGINE INNODB STATUS 的 LATEST DETECTED DEADLOCK 區段；更理想的是補一個在 GRAFANA_TEST_DB=mysql 下併發插入與清理的整合測試，讓這個約束變成機械可檢的。

#### Q-002 untilDoneOrCancelled 以「本批 affected == 0」作為結束條件；在多實例併發清理下，這會不會讓單輪提早結束、使積壓無法收斂？

面向 F 資料取用與資料庫

**背景**：untilDoneOrCancelled（xorm_store.go:643-661）在 batchWork 回傳 0 時就結束迴圈。fetchIDs 與 deleteByIDs 走的是兩個不同的 session（xorm_store.go:590-593 與 621-628），兩者之間若有另一個執行者刪掉了同一批 ID，deleteByIDs 會回報 0，即使符合條件的資料還有很多，本輪也會就此結束。xorm_store.go:523-524 的註解預期的是併發 insert 造成的少刪，這裡是另一條路徑。這件事之所以不是純理論：CleanUpService 注入了 ServerLockService 卻沒有使用（cleanup.go:55，grep 確認 Run 與 clean 都沒呼叫），多實例部署下每個實例都各自跑清理迴圈；F-002 的 1 分鐘 ticker 又讓碰撞窗口變密。但要斷言「積壓不會收斂」需要知道真實環境的 annotation 產生速率與實例數，本次無法取得，因此不給 severity。

**如何確認**：在接近生產規模的 annotation 產生速率下、以多個實例做一次 soak test，觀察每輪結束後仍符合清理條件的殘留筆數是否隨時間下降；或是為每輪清理加上一個殘留筆數的 metric，讓收斂與否可以被長期觀測。也可以直接改成用 len(ids) == 0 而非 affected == 0 當結束條件，讓提早結束只發生在「真的沒有東西可撈」的時候。

</details>
