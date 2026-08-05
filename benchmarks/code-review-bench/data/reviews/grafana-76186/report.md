## 審查結論：Approved with Comments

> Critical 0 · Suggestion 3 · Nit 4 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ✅ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | — |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

- **A 風格**（未通過）：更名沒有做完最後一哩：測試函式名與 logger 名稱仍帶 instrumentation（F-004、F-005）。其餘命名（MetricsMiddleware / ContextualLoggerMiddleware）與行為相符，interface{} 換成 any 也與 repo 其他檔案一致。
- **D API 慣例**（不適用）：沒有 HTTP endpoint、路由、request/response schema 或 HTTP verb 的改動；改動全在 plugin client 的內部 middleware chain。
- **E 架構**（未通過）：把 contextual logger 拆成獨立 middleware 之後，log 欄位的正確性變成依賴 middleware 在 slice 中的位置，而原本記載這個約束的註解在搬移過程中一併被刪掉（F-001）。另外順手看到 MetricsMiddleware 沿用共用實例、每次請求覆寫 next 的既有寫法（F-007）。
- **F 資料取用與資料庫**（不適用）：沒有資料庫存取、schema 變更、交易或持久化資料格式的改動。
- **G 測試**（未通過）：本次唯一的新 production 檔沒有任何測試（F-002），同目錄其他 middleware 幾乎都有；而且 plugins/log 的 fake 在新增的 FromContext 上回傳全新實例，會讓之後想補的日誌測試靜默看不到任何紀錄（F-003）。既有的 metrics_middleware_test.go 內容沒有被削弱，只是換了檔名與建構函式名。
- **H 非 Python 檔**（未通過）：diff 全部是 Go 檔，本維度即為主要適用維度（檢查表列的 Vue/Dockerfile/nginx/Alembic 項目在此不適用，改以 Go 的等價關切執行：介面實作完整性、middleware 責任邊界、格式）。gofmt 對四個受影響目錄回報 0 個未格式化檔案；發現的問題是新 middleware 的三個 stream 端點是無註解的純 pass-through（F-006）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：標題是 Chore: Renamed instrumentation middleware，但這個 MR 同時做了三件不只是更名的事：(1) 在 exported interface pkg/plugins/log/ifaces.go:23 新增 FromContext 並補上兩個實作；(2) 新增一條 middleware 並插進 production 的 middleware chain（pkg/services/pluginsintegration/pluginsintegration.go:160）；(3) 改變 Plugin Request Completed 這行 log 的組成方式，欄位從明確參數改成從 ctx 取（pkg/services/pluginsintegration/clientmiddleware/logger_middleware.go:49-58），並移除明寫的 traceID。Chore/Rename 的標題會讓 reviewer 用「掃過去就好」的力度看，而上述三項都是會改變執行期行為的改動。建議拆成「rename」與「extract contextual logger middleware」兩個 MR，或至少改標題。附帶說明兩個查證結果，讓後續讀者不必重跑：(a) logParams 裡被刪掉的 traceID 沒有真的消失——pkg/infra/tracing/tracing.go:91 註冊了一個 contextual log provider，FromContext 會把 traceID 補回來，所以那行 log 的欄位集合與改動前相同；(b) 目前 CreateMiddlewares 的排序是正確的，ContextualLoggerMiddleware 確實在 LoggerMiddleware 之前（pkg/plugins/manager/client/decorator.go:101 反轉 slice，越前面越外層）。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | 未安裝（不在 PATH 上），略過相依套件漏洞、設定錯誤與 secret 掃描。 |
| opengrep | 略過 | 未安裝，且審查機器上的預設 semgrep rules 目錄不存在，略過 SAST 掃描。 |
| ruff | 已執行 | in_diff 0、outside_diff 10 |
| ty | 略過 | 未安裝，略過 Python 型別檢查；本次 diff 也沒有 Python 檔。 |
| oxlint | 略過 | 未安裝，略過 JavaScript/TypeScript lint；本次 diff 也沒有 JS/TS 檔。 |
| codegraph | 略過 | 未安裝，無法建立 symbol graph；本次的呼叫者與實作者盤點全部改用 grep 完成（見 I 回溯分析）。 |
| gofmt | 已執行 | unformatted_files 0 |
| golangci-lint / go build / go vet | 略過 | 工具本身有裝，但本機沒有 Go module cache 且審查環境不對外連線，無法解析相依套件，任何需要編譯的檢查都跑不起來。因此「這個 rename 有沒有漏掉呼叫端」是用全 repo grep 判定的，不是用編譯器判定的（見 I 回溯分析）。 |
| ncr-fresh-eyes / ncr-quality-check（subagent） | 略過 | 本次執行環境沒有可派工的 subagent 工具（Agent/Task tool 不存在），因此 Phase 3 的 fresh-eyes 初讀與 Phase 4 的報告品質檢查都沒有執行，也沒有由主 agent 自行模擬。這代表本報告少了一層「未被檢查表框住的初讀」與一層外部校對。 |

<details>
<summary>Suggestion（3）</summary>

#### F-001 log 欄位改為依賴 middleware 順序，但記載這個約束的註解在搬移中被刪掉了 — `pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go:12`

面向 E 架構 · Suggestion

**問題**：改動前，舊檔案的型別註解明寫著這條 middleware 除了 metrics 還會 enrich context，「For those reasons, this middleware should live at the top of the middleware stack」；本次 diff 把這兩句連同 instrumentContext 一起搬走，但只有函式搬到 contextual_logger_middleware.go，那兩句約束沒有跟著搬。改動後，logger_middleware.go:58 的 Plugin Request Completed 完全靠 m.logger.FromContext(ctx) 取得 pluginId / endpoint / dsName / dsUID / uname，而這些值只有在 ContextualLoggerMiddleware 排在它之前時才存在於 ctx（decorator.go:101 的 clientFromMiddlewares 會反轉 slice，所以越前面越外層）。也就是說，一個純粹的排序調整就能讓那行 log 安靜地掉光所有 plugin 識別欄位——不會編譯失敗，不會有測試失敗，只有 log 變空。目前 pluginsintegration.go:158-161 的順序是對的，問題在於這個正確性現在沒有任何東西記載或保護。

**證據**：
- `pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go:12`
- `pkg/services/pluginsintegration/clientmiddleware/logger_middleware.go:58`
- `pkg/services/pluginsintegration/pluginsintegration.go:158`
- `pkg/plugins/manager/client/decorator.go:101`

**修復方向**：在 NewContextualLoggerMiddleware 上加回等價的註解（例如「必須排在任何會記錄 plugin 請求的 middleware 之前，否則那些 log 會失去 plugin context」），並在 CreateMiddlewares 的 slice 裡對這兩行加一行順序說明。若要更進一步，配合 F-002 的測試把「LoggerMiddleware 的 log 帶得到 pluginId」釘成回歸測試。

#### F-002 新增的 ContextualLoggerMiddleware 沒有任何測試 — `pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go:14`

面向 G 測試 · Suggestion

**問題**：這是本次唯一新增的 production 檔，而且它現在是「plugin 請求日誌裡的識別欄位從哪來」的唯一來源。同目錄的 tracing / cookies / oauthtoken / clear_auth_headers / resource_response / caching / forward_id / user_header / tracing_header 都有對應的 *_test.go，metrics 的測試也隨著更名保留了下來，只有這條新的沒有。搭配 F-001 的順序耦合，等於這條路徑上完全沒有安全網：middleware 被移位、被拿掉、或 instrumentContext 少 append 一個欄位，都不會有任何測試變紅。

**證據**：
- `pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go:14`
- `pkg/services/pluginsintegration/clientmiddleware/logger_middleware.go:58`

**修復方向**：新增 contextual_logger_middleware_test.go：用 clienttest.NewClientDecoratorTest 串上 NewContextualLoggerMiddleware，在最內層的 client 把收到的 ctx 存下來，然後照 pkg/infra/log/log_test.go:270 TestWithContextualAttributes_appendsContext 的做法，用 log.New("test").FromContext(ctx) 產生 logger 並斷言 endpoint / pluginId / dsName / dsUID / uname 都有帶到；四個端點各跑一次，並補一個 DataSourceInstanceSettings 與 User 皆為 nil 的案例。

#### F-003 TestLogger.FromContext 回傳全新的 TestLogger，讓經由 FromContext 產生的 log 無法被斷言 — `pkg/plugins/log/fake.go:46`

面向 G 測試 · Suggestion

**問題**：production 端的 LoggerMiddleware 已經改成 m.logger.FromContext(ctx).Info(...)。若有人注入 TestLogger 來驗證日誌，Info 會被記在 FromContext 當場 new 出來的另一個實例上，測試手上那個 TestLogger 的 InfoLogs.Calls 永遠是 0。失敗模式是「什麼都沒看到」而不是「斷言失敗」，很容易被誤讀成「這段程式沒有記 log」。fake 存在的目的就是累積紀錄供斷言，New(...) 沿用 return NewTestLogger() 的既有寫法情有可原（呼叫端本來就不多），但 FromContext 現在正好落在唯一一條 production 日誌路徑上。

**證據**：
- `pkg/plugins/log/fake.go:46`
- `pkg/services/pluginsintegration/clientmiddleware/logger_middleware.go:58`

**修復方向**：讓 FromContext 直接 return f（context 對 fake 沒有意義，保留紀錄才有意義）；若想保留「回傳新實例」的語意，就讓新實例共用同一組 Logs 指標，使斷言仍看得到呼叫。

</details>

<details>
<summary>Nit（4）</summary>

#### F-004 測試檔已更名，測試函式名還是 TestInstrumentationMiddleware — `pkg/services/pluginsintegration/clientmiddleware/metrics_middleware_test.go:21`

面向 A 風格 · Nit

**問題**：檔案已改成 metrics_middleware_test.go、受測型別已改成 MetricsMiddleware，只有測試函式名還留著舊名字。這是 grep InstrumentationMiddleware 在整個 repo 唯一還會命中的地方，之後有人想確認更名是否完成時會被它絆一下。

**證據**：
- `pkg/services/pluginsintegration/clientmiddleware/metrics_middleware_test.go:21`

**修復方向**：改成 TestMetricsMiddleware。

#### F-005 logger 名稱仍是 plugin.instrumentation — `pkg/services/pluginsintegration/pluginsintegration.go:161`

面向 A 風格 · Nit

**問題**：更名之後，instrumentation 這個字在這條 middleware 上已經沒有對應的型別了，但傳給 NewLoggerMiddleware 的 logger 名稱還是 plugin.instrumentation，而它會出現在每一行 Plugin Request Completed 的 logger= 欄位。這個字串是對外可見的介面，改它會影響既有的 log 查詢、dashboard 與告警規則，所以是取捨而不是單純的疏漏——但取捨需要被寫下來。

**證據**：
- `pkg/services/pluginsintegration/pluginsintegration.go:161`

**修復方向**：兩條路擇一並留下痕跡：改成 plugin.logger（或類似名稱）並在 PR 描述註明這會改變 logger= 欄位、需要同步既有查詢；或維持不動並在該行加一行註解說明名稱是為了相容既有 log 查詢而保留。

#### F-006 ContextualLoggerMiddleware 的三個 stream 端點是無註解的純 pass-through — `pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go:59`

面向 H 非 Python 檔 · Nit

**問題**：SubscribeStream / PublishStream / RunStream 沒有呼叫 instrumentContext，所以這三條路徑下游的 log 拿不到 pluginId 與 endpoint。這不是本次引入的行為（舊的 instrumentContext 也只在四個端點被呼叫，utils.go 甚至沒有 stream 端點的常數），但拆成獨立 middleware 之後，「這條 middleware 唯一的職責就是加 contextual logger」與「其中三個方法什麼都不加」的落差就直接寫在同一個檔案裡了；同目錄的 TracingMiddleware 是七個端點全包。下一個讀的人無從分辨這是刻意還是漏掉。

**證據**：
- `pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go:59`
- `pkg/services/pluginsintegration/clientmiddleware/tracing_middleware.go:115`

**修復方向**：補上 endpointSubscribeStream / endpointPublishStream / endpointRunStream 常數並一併 instrument；若是刻意不做（例如 stream 的生命週期與單次請求不同），就在這三個方法上方加一行註解說明原因。

#### F-007 既有寫法（非本次引入）：MetricsMiddleware 全域共用單一實例，每次請求覆寫 next — `pkg/services/pluginsintegration/clientmiddleware/metrics_middleware.go:76`

面向 E 架構 · Nit

**問題**：NewMetricsMiddleware 在 closure 外建立一個 imw，closure 內做的是 imw.next = next 再回傳同一個指標。而 decorator.go:29 起的每個方法都會在「每一次請求」呼叫 clientFromMiddlewares 重新組 chain，也就是每一次請求都會寫一次 imw.next——併發請求下這是對同一個欄位的無保護讀寫，go test -race 會判定為 data race（而且下游 middleware 每次都是新實例，寫入的值確實不同）。這段程式本次只有更名、行為沒變，所以不是這個 MR 造成的；但本次新增的 ContextualLoggerMiddleware 正好示範了正確做法（每次 CreateClientMiddleware 都建新 struct），兩者放在同一個目錄對照，順手修掉的成本很低。

**證據**：
- `pkg/services/pluginsintegration/clientmiddleware/metrics_middleware.go:76`
- `pkg/plugins/manager/client/decorator.go:29`
- `pkg/plugins/manager/client/decorator.go:101`

**修復方向**：把 prometheus 註冊（只能做一次）與 middleware 實例（每次都該是新的）拆開：在 closure 外只保留 newMetricsMiddleware 產生的 imw（其 pluginMetrics 與 pluginRegistry 只需建立一次），closure 內改為回傳 &MetricsMiddleware{pluginMetrics: imw.pluginMetrics, pluginRegistry: imw.pluginRegistry, next: next}。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 pkg/plugins/log.Logger 是 exported interface，本次在上面新增了 FromContext。這個 repo 以外（例如 Grafana Enterprise 或其他 downstream）是否還有別的實作，會因此在編譯期壞掉？

面向 I 回溯分析

**背景**：在這份 checkout 內已經確認乾淨：以 method set 反查（grep 所有具備 `Warn(msg string, ctx ...any)` 的型別）只找到 pkg/plugins/log/logger.go 的 grafanaInfraLogWrapper 與 pkg/plugins/log/fake.go 的 TestLogger，兩者本次都補上了 FromContext；pkg/infra/log/logtest 的 Fake 實作的是另一個 interface，不會誤中。但在 exported interface 上加方法對 repo 外的實作者一律是 breaking change，而這個 checkout 看不到 Enterprise 或其他 downstream。

**如何確認**：在 Enterprise repo（以及任何 vendor 這個 package 的地方）grep 對 plugins/log.Logger 的實作與賦值，或直接跑一次把本分支接進去的 Enterprise build。

#### Q-002 有沒有任何路徑會讓同一條 ctx 兩次通過 plugin client decorator？若有，Plugin Request Completed 這行會出現重複且順序在前的 endpoint / pluginId，指到外層那次呼叫的 plugin。

面向 E 架構

**背景**：pkg/infra/log/log.go:285 的 WithContextualAttributes 是 append 語意——第二次呼叫會把新的參數接在既有參數後面。改動前 LoggerMiddleware 用明寫參數組 log，不受這個累加影響；改動後改用 FromContext(ctx)，就會把累加後的全部欄位一起吐出來。已查過 OSS 內最可能的巢狀點：pkg/expr/nodes.go:274 與 :349 的 dataService.QueryData 走的是 wire 注入的同一個 decorator，但它們是從 HTTP 請求的 ctx 平行發出的，不是從「已經過 decorator 的 ctx」再發一次，所以構不成巢狀。無法排除 Enterprise 或其他未讀到的路徑，因此不列為 finding。

**如何確認**：確認是否有任何 middleware、datasource 或服務會拿 plugin client 呼叫途中的 ctx 再呼叫一次 plugin client（recorded queries、caching、代理型 datasource 是最可能的候選）；若確定沒有，這個問題就可以關掉。

</details>
