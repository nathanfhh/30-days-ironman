## 審查結論：Request Changes

> Critical 1 · Suggestion 2 · Nit 3 · 未驗證提問 3
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | — |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ✅ | ✅ |

- **A 風格**（未通過）：函式名與行為不符、反向命名的布林，以及變更後仍散落在 tree 裡的 DuckDB 字樣。見 F-004、F-006。
- **B 簡潔**（未通過）：新加的 enableSqlExpressions 帶著一個永遠不會成立的分支與一個被丟棄的 feature flag 讀取，新加的 (*DB).TablesList 則沒有任何呼叫點。見 F-001、F-005。
- **D API 慣例**（不適用）：這次變更沒有動 HTTP route、URL 命名、HTTP verb 語意、validation schema 或授權檢查。唯一對外可見的 API 行為差異是 expression 解析失敗時回傳的錯誤內容，已在 F-002 處理。
- **E 架構**（未通過）：SQL expression 有兩條建構入口（pkg/expr/nodes.go:123-146 的新 parser 路徑、pkg/expr/nodes.go:147-160 的舊 switch 路徑），新的 guard 只放在其中一條上。見 F-002。
- **F 資料取用與資料庫**（不適用）：沒有 schema 變更、沒有 migration、沒有 transaction、沒有跨 request 的共享狀態。sql.NewInMemoryDB()（pkg/expr/sql/db.go:24-26）每次呼叫回傳一個沒有任何欄位的空 struct（pkg/expr/sql/db.go:8-9），不持有連線也不持有狀態，因此併發與環境差異的問題在這次變更中都不存在。
- **G 測試**（未通過）：新行為完全沒有測試釘住。見 F-003。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個時機做？**：後端停用之後，前端仍然依 sqlExpressions toggle 顯示 SQL expression 選項（public/app/features/expressions/types.ts:66-76），query type schema 也仍然對外宣告 sql 這個型別（pkg/expr/query.go:31、pkg/expr/query_test.go:83-93）。也就是說 toggle 打開的使用者看得到入口、按下去必定失敗。diff 裡沒有任何 TODO、issue 連結或說明講這是暫時的還是永久的，這個取捨該由人決定，見 Q-001 與 Q-002。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次沒有做相依套件漏洞、設定錯誤與祕密掃描。這一點對本 MR 尤其值得注意：變更內容正是移除相依，而供應鏈掃描恰好是唯一沒有跑到的那一類。 |
| opengrep | 略過 | opengrep 未安裝，且設定的 Semgrep 規則目錄不存在，SAST 掃描完全沒有執行。 |
| ruff | 已執行 | ruff 有執行且成功結束（exit 1 代表有診斷，不是崩潰），但它只檢查 Python，而本次 diff 沒有任何 .py 檔。專案既有 Python 問題 10 件，全部落在 diff 之外，不列入本次。實際覆蓋率為 0：這次的 Go 變更沒有被任何靜態工具檢查過。 · in_diff 0、outside_diff 10 |
| ty | 略過 | ty 未安裝；且它是 Python 型別檢查器，對 Go 變更本來就不適用。 |
| oxlint | 略過 | oxlint 未安裝；本次 diff 也沒有 JavaScript / TypeScript 檔案。 |
| go toolchain (go build / go vet / golangci-lint) | 略過 | 執行環境沒有 Go toolchain 也沒有網路，所以 go build ./...、go vet、golangci-lint、go mod tidy 都沒有跑過。本報告對 Go 程式的所有判斷都來自閱讀原始碼與 grep，不是編譯結果。專案的 .golangci.toml 有啟用 staticcheck / gosimple / unused / ineffassign，但依照這些 linter 的判定規則，F-001 的寫法（兩個分支都回傳同一個常數、變數有被讀取、方法掛在 exported type 上）預期不會被它們攔下來——這是推論，不是驗證過的結果。 |
| codegraph | 略過 | codegraph 未安裝，Phase 3 的呼叫路徑列舉與 dimension I 全部改用 grep 完成。 |

### Critical

#### F-001 enableSqlExpressions 每條路徑都回傳 false，feature flag 的讀取結果被計算後丟棄 — `pkg/expr/reader.go:194-200`

面向 B 簡潔 · Critical

**問題**：函式的三條路徑只有一個結果：enabled := !h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions) 之後，if enabled { return false }，接著 return false。無論 toggle 開或關都回 false，所以 IsEnabledGlobally 的回傳值算完就被丟掉，featuremgmt.FlagSqlExpressions 對後端等於完全沒有作用。全 repo grep 過 FlagSqlExpressions，除了 toggles_gen.go:584 的宣告與 registry.go:1084 的註冊之外，唯一的讀取點就是這裡（前端另有 public/app/features/expressions/types.ts:73 自己讀 config.featureToggles）。

這裡真正的風險不是執行期行為——執行期行為（SQL expression 一律停用）跟 MR 的意圖是一致的——而是這段程式碼讓讀者無法分辨「作者刻意永久停用」與「作者想做 flag gating 但把邏輯寫反了」。而且這兩種讀法會導向相反的修法：後來的人如果把它當成 bug、順手把 ! 拿掉改成 return h.features.IsEnabledGlobally(...)，就會在所有底層實作都還是 stub（pkg/expr/sql/db.go:12-22 三個 method 都 return errors.New("not implemented")）的情況下把功能放回去，換來的是一條每次都失敗的路徑。一個永遠成立不了的分支加上一個被丟棄的 flag 讀取，在合併前需要先被解決成一個明確的意圖。

**證據**：
- `pkg/expr/reader.go:194-200`
- `pkg/expr/reader.go:129-132`
- `pkg/services/featuremgmt/registry.go:1083-1088`

**修復方向**：選一個意圖，然後讓程式碼只表達那一個。

若意圖是永久停用（看起來是這次的意圖）：刪掉整個 enableSqlExpressions，reader.go 的 case 直接寫

    case QueryTypeSQL:
        return eq, fmt.Errorf("sqlExpressions is not implemented")

並且順手確認 featuremgmt.FlagSqlExpressions 是否還需要留著（若留著，registry.go 的 Description 應說明目前後端未實作）。

若意圖是保留 flag gating：把函式收斂成一行，並改成 method 以符合 reader.go 其他地方的寫法（參考同檔 :160 對 FlagRecoveryThreshold 的用法）

    func (h *ExpressionQueryReader) sqlExpressionsEnabled() bool {
        return h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)
    }

但這條路要先讓 pkg/expr/sql/db.go 有真正的實作，否則 toggle 打開只是換一個失敗訊息。

<details>
<summary>Suggestion（2）</summary>

#### F-002 停用的 guard 只放在新 parser 路徑上，預設路徑靠 stub 失敗擋下來，錯誤訊息會怪到使用者的 SQL 上 — `pkg/expr/nodes.go:123-146`

面向 E 架構 · Suggestion

**問題**：buildCMDNode 有兩條建構 SQL expression 的路徑，新的 guard 只在其中一條上。

路徑 A（nodes.go:123-146）：FlagExpressionParser 開啟時走 NewExpressionQueryReader(...).ReadQuery，命中新加的 guard，回傳「sqlExpressions is not implemented」。

路徑 B（nodes.go:147-160）：FlagExpressionParser 未開啟時走 switch，case TypeSQL 直接呼叫 UnmarshalSQLCommand(rn)，完全沒有 guard。而 expressionParser 是 FeatureStageExperimental（registry.go:1126-1131），預設關閉，所以路徑 B 才是絕大多數部署的預設路徑。

先說結論避免誤會：路徑 B 並沒有讓 SQL 真的跑起來。追過去是 UnmarshalSQLCommand → NewSQLCommand（sql_command.go:30）→ sql.TablesList → NewInMemoryDB().RunCommands（parser.go:26），而 (*DB).RunCommands 現在固定 return errors.New("not implemented")（db.go:16-18），所以 NewSQLCommand 一定失敗、SQLCommand 永遠建不出來，Execute 也就不可達。功能確實是停用的。

問題在於它是「順便」停用的，以及停用之後使用者看到什麼。路徑 B 的失敗被包成 errutil.BadRequest("sql-invalid-sql", errutil.WithPublicMessage("error reading SQL command"))——一個 400，訊息指向「你的 SQL 讀不懂」，而不是「這個功能目前不提供」。同一個功能在兩條路徑上給出兩種語意完全不同的錯誤，而預設那條給的是誤導性的那個。另外 parser.go:28 會在每次嘗試時以 Error 等級把使用者完整的 SQL 寫進 log（logger.Error("error serializing sql", ..., "sql", rawSQL, "cmd", cmd)）：這行本來是罕見的例外路徑，現在變成必經路徑，等於每一次 SQL expression 嘗試都產生一筆含使用者查詢內容的 ERROR log。

**證據**：
- `pkg/expr/nodes.go:123-146`
- `pkg/expr/nodes.go:147-160`
- `pkg/expr/sql_command.go:30-36`
- `pkg/expr/sql/parser.go:26-30`
- `pkg/services/featuremgmt/registry.go:1126-1131`

**修復方向**：把 guard 放在兩條路徑共同的上游，讓「停用」是一個明確的決定而不是相依失敗的副作用。最小的做法是在 nodes.go 的 switch 裡比照 TypeThreshold 傳入 toggles 的寫法補上：

    case TypeSQL:
        return nil, fmt.Errorf("sqlExpressions is not implemented")

或者把檢查提到 buildCMDNode 進入 switch 之前，讓兩條路徑共用同一個判斷與同一句訊息。若希望回應的 HTTP 語意也對，用 errutil.NotImplemented / errutil.BadRequest 搭配明確的 public message（例如「SQL expressions 目前未提供」），不要沿用 sql-invalid-sql。順帶把 parser.go:28 的 logger.Error 降級為 Debug 或移除，避免必經路徑持續輸出含使用者 SQL 的 ERROR log。

#### F-003 沒有任何測試釘住「SQL expression 已停用」這個新行為 — `pkg/expr/reader.go:128-138`

面向 G 測試 · Suggestion

**問題**：這次變更的核心是一個行為切換，但 diff 沒有動任何測試檔。與 SQL expression 相關的兩份既有測試在 base 版本就已經整份被跳過——sql_command_test.go 的 TestNewCommand 第一行是 t.Skip()，parser_test.go 十幾個 case 每一個第一行也都是 t.Skip()。所以 CI 目前既不會驗證 SQL expression 已經停用，也不會在有人把它意外打開時發出聲音。

這一點直接放大了 F-001：如果有一個三行的測試斷言 ReadQuery 對 QueryTypeSQL 回傳 error，那麼未來任何人把 enableSqlExpressions 的邏輯「修正」回去時，測試會立刻紅掉並強迫他面對底層還是 stub 這件事。現在沒有這道防線。

**證據**：
- `pkg/expr/reader.go:128-138`
- `pkg/expr/sql_command_test.go:8-9`
- `pkg/expr/sql/parser_test.go:9-10`

**修復方向**：在 pkg/expr 加一個不依賴外部相依、也不需要 duckdb 的測試，直接針對新 guard：

    func TestReadQuery_SQLDisabled(t *testing.T) {
        r := NewExpressionQueryReader(featuremgmt.WithFeatures())
        iter, err := jsoniter.ParseBytes(jsoniter.ConfigDefault, []byte(`{"refId":"A","type":"sql","expression":"SELECT 1"}`))
        require.NoError(t, err)
        _, err = r.ReadQuery(data.NewDataQuery(map[string]any{"refId": "A", "type": "sql"}), iter)
        require.ErrorContains(t, err, "not implemented")
    }

再補一個 featuremgmt.WithFeatures(featuremgmt.FlagSqlExpressions) 開啟的版本，斷言結果相同——那個測試會把 F-001 的意圖直接寫成可執行的形式。另外建議順手在 parser_test.go 的 t.Skip() 上補一行 t.Skip("...") 說明為什麼跳過，讓下一個人知道那不是忘了刪。

</details>

<details>
<summary>Nit（3）</summary>

#### F-004 enableSqlExpressions：名稱承諾的事情與行為不符，且用了反向命名的布林 — `pkg/expr/reader.go:194-196`

面向 A 風格 · Nit

**問題**：三個小地方疊在一起，讓這段程式碼比它該有的樣子難讀，也是 F-001 之所以看起來像 bug 而不是像決定的原因。

其一，名字是動詞 enableSqlExpressions（「啟用 SQL expressions」），但函式什麼都沒有啟用，它只是回報一個布林；命名上應該是 sqlExpressionsEnabled。其二，enabled := !h.features.IsEnabledGlobally(...) 把一個叫 enabled 的變數綁到 flag 的否定上，於是呼叫端的 if !enabled 變成雙重否定，讀者要在腦中翻兩次才知道條件成立時代表什麼。其三，它接收 *ExpressionQueryReader 當自由函式的參數，而同一支檔案裡同類型的檢查是直接寫成 h.features.IsEnabledGlobally(featuremgmt.FlagRecoveryThreshold)（:160），沒有再包一層；要包的話也該是 method。

**證據**：
- `pkg/expr/reader.go:194-196`
- `pkg/expr/reader.go:129-130`
- `pkg/expr/reader.go:160`

**修復方向**：若 F-001 選擇保留這個函式，改成 method 並讓布林正向：

    func (h *ExpressionQueryReader) sqlExpressionsEnabled() bool {
        return h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)
    }

呼叫端寫成 if !h.sqlExpressionsEnabled() { ... }。若 F-001 選擇永久停用，這個函式整個刪掉，問題自然消失。

#### F-005 (*DB).TablesList 沒有任何呼叫點，而且與同 package 的 sql.TablesList 同名 — `pkg/expr/sql/db.go:12-14`

面向 B 簡潔 · Nit

**問題**：新加的 stub 有三個 method，但 grep 全 repo 之後只有兩個真的被呼叫：RunCommands（parser.go:26）與 QueryFramesInto（sql_command.go:100）。(*DB).TablesList 沒有任何呼叫端。它的存在應該是為了對齊被移除的 go-duck 的 method set，但 go-duck 的 TablesList 在 base 版本也沒有被用到——實際使用的一直是 package-level 的 sql.TablesList（parser.go:22），也就是說現在同一個 package 裡有兩個叫 TablesList 的東西，一個是實作、一個是永遠不會被叫到的空殼。下一個維護者很容易點錯一個。

**證據**：
- `pkg/expr/sql/db.go:12-14`
- `pkg/expr/sql/parser.go:22-23`

**修復方向**：直接刪掉 (*DB).TablesList。若刻意要保留 go-duck 的完整 method set 當作未來替換實作的介面契約，就把這個意圖寫下來——在 db.go 頂端加一行註解說明這個 struct 是 go-duck 的臨時替身、method 形狀要保持一致，並附上追蹤 issue（見 Q-001）。沒有那行註解的話，它讀起來就只是被遺留下來的死程式碼。

#### F-006 DuckDB 已經從 tree 裡移除，但註解、feature toggle 說明與前端文案還在講 DuckDB — `pkg/expr/query.go:30`

面向 A 風格 · Nit

**問題**：commit message 明確寫著「remove duckdb ref」，go.mod 也確實拔掉了 github.com/scottlepp/go-duck，但三處面向讀者的文字沒有跟著調整：pkg/expr/query.go:30 的註解仍是「// SQL query via DuckDB」；feature toggle 的 Description 仍是「Enables using SQL and DuckDB functions as Expressions.」（registry.go:1084，並且同步產生在 toggles_gen.go:583）；前端 expression picker 的說明仍是「Transform data using SQL. Supports Aggregate/Analytics functions from DuckDB」。其中 feature toggle 的 Description 與前端文案是使用者直接看得到的，現在描述的是一個 tree 裡已經不存在、而且暫時也不會執行的能力。

**證據**：
- `pkg/expr/query.go:30`
- `pkg/services/featuremgmt/registry.go:1084`
- `public/app/features/expressions/types.ts:69`

**修復方向**：至少更新使用者看得到的兩處。registry.go:1084 的 Description 改成不綁定實作引擎、並反映目前狀態的說法（例如「Enables using SQL as Expressions.（backend implementation temporarily disabled）」），改完記得依 Grafana 慣例重跑 toggle 產生器讓 toggles_gen.go 同步。前端 types.ts:69 的 description 拿掉 DuckDB 字樣。pkg/expr/query.go:30 的註解改成「// SQL query」即可。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 這次停用是永久的，還是等替代的 SQL 引擎落地後由 sqlExpressions toggle 重新開啟？

面向 E 架構

**背景**：pkg/expr/sql/db.go 新增的 DB 完整保留了被移除的 go-duck 的 method 形狀（TablesList / RunCommands / QueryFramesInto / NewInMemoryDB），這很像是刻意留下的替換接縫；但 diff 裡沒有任何 TODO、issue 連結或註解說明這件事，commit message 也只寫「disable sql expressions」「remove duckdb ref」。無法只從程式碼分辨這是「暫時拿掉相依、之後換一個引擎接回來」還是「這個功能就此收掉」，而這個答案會直接決定 F-001 該往哪個方向修、以及 F-005 的空殼 method 該刪還是該留。

**如何確認**：作者在 MR 描述或 commit message 裡說明後續計畫；或在 pkg/expr/sql/db.go 頂端加一行 TODO 指向追蹤 issue。

#### Q-002 後端停用之後，前端與 query type schema 仍然對外宣告 sql 這個 expression type，可以接受嗎？

面向 E 架構

**背景**：public/app/features/expressions/types.ts:66-76 仍然依 config.featureToggles?.sqlExpressions 決定是否在 expression picker 顯示 SQL 選項，pkg/expr/query_test.go:83-93 產生的 query type schema 也仍然包含 sql 與它的範例（"SELECT * FROM A limit 1"）。也就是說把 sqlExpressions toggle 打開的使用者仍然看得到入口，但每一次查詢都會失敗。這是刻意接受（experimental toggle 本來就可能壞掉）還是應該一併把前端關掉，屬於產品決定，程式碼本身讀不出來。

**如何確認**：前端 / 產品 owner 確認 experimental toggle 打開後回傳錯誤是否可接受；若不可接受，需要一個對應的前端變更把 sql 從選項中移除。

#### Q-003 go.mod / go.sum / go.work.sum 的裁剪在真正 build 的時候是乾淨的嗎？

面向 H 非 Python 檔

**背景**：審查環境沒有 Go toolchain 也沒有網路，所以 go build ./...、go vet、go mod tidy、golangci-lint 一個都沒有跑過，對相依裁剪的判斷全部來自文字比對。已經確認的部分：全 repo grep 不到任何殘留的 go-duck / duckdb import；go.work.sum 移除的 Azure azblob v1.3.2 與 google/go-replayers/* 在 pkg/storage/unified/resource/go.sum 裡仍有對應 hash，而 workspace 內唯一宣告 azblob 的就是該 module。沒有確認的部分：go.work.sum 的其餘增減是否恰好等於 make update-workspace 的輸出，以及 go.sum 裡被移除 h1 的項目（araddon/dateparse、gotest.tools/v3 v3.5.1 等）是否真的沒有其他 module 需要。

**如何確認**：CI 的 build / lint / go mod tidy check job 綠燈；或在有 Go toolchain 的環境重跑 make update-workspace 並確認 diff 為空。

</details>
