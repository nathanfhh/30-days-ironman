## 審查結論：Approved with Comments

> Critical 0 · Suggestion 2 · Nit 2 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ✅ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ✅ | — |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ✅ | ❌ |

- **B 簡潔**（未通過）：兩個入口現在跑同一組三步驟 pipeline，filter 順序卻相反（F-004）。另外確認過 interpolateVariablesInQueries 沒有因此變成死碼：LokiQueryBuilderOptions.tsx:106、alerting/unified/utils/rule-form.ts:665、expressions/ExpressionDatasource.ts:47 都還在用。
- **D API 慣例**（不適用）：diff 內沒有定義或修改任何 HTTP endpoint、URL 路徑、HTTP verb 或請求驗證 schema；改動全部落在 Loki datasource 前端的 query pipeline 內。
- **F 資料取用與資料庫**（不適用）：diff 內沒有資料庫存取、schema 變更、migration 或共享狀態的讀改寫；查詢是透過 Grafana 的 api/ds/query endpoint 交給 Grafana backend，不經過本層的任何資料庫。
- **G 測試**（未通過）：兩個新測試都把 runQuery mock 掉，剛好把第二次插值擋在斷言之外，且新引入的 request.filters 行為沒有測試（F-002）；toHaveBeenCalledTimes(5) 這個數字沒有註解說明（F-003）。
- **I 回溯分析**（未通過）：runSplitQuery 與 runShardSplitQuery 的簽章沒變，呼叫端不受影響；但兩者對「傳進來的 targets 是否已插值」這個隱含輸入契約改變了，而 shardQuerySplitting.ts:163 正好用已插值的 targets 呼叫 runSplitQuery，最後 super.query() 又假設收到的是未插值的（F-001）。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | preflight 回報未安裝，相依套件漏洞與 secret 掃描未執行；本次 diff 未動 go.mod / package.json，但這一項沒有被覆蓋這件事仍如實揭露。 |
| opengrep | 略過 | preflight 回報未安裝，且 NCR_OPENGREP_RULES 指向的規則目錄不存在，SAST 掃描完全未執行。 |
| ruff | 略過 | ruff 0.15.8 存在，但本次 diff 的四個檔案全部是 TypeScript；直接對 .ts 執行只會回 invalid-syntax（Expected one or more symbol names after import），不構成任何有效覆蓋，因此不宣稱 Python lint 結果。 · exit code 1 |
| ty | 略過 | preflight 回報未安裝；本次 diff 也沒有 Python 檔案。 |
| oxlint | 略過 | preflight 回報未安裝，JavaScript/TypeScript lint 未執行。 |
| eslint | 錯誤 | eslint 10.1.0 在 PATH 上，但 checkout 沒有 node_modules，eslint.config.js 解析不到 @emotion/eslint-plugin 而直接失敗。離線是本 skill 的硬性前提，不為了掃描安裝相依套件，因此這一項無覆蓋。 · exit code 2 |
| prettier | 已執行 | files_checked 4、violations 0 |
| tsc | 略過 | tsc 在 PATH 上，但沒有 node_modules，無法解析 @grafana/data、@grafana/runtime、rxjs、lodash 等型別；安裝相依套件需要網路且會執行受審分支控制的程式碼，因此不執行型別檢查。 |
| jest | 略過 | 同上，沒有 node_modules，無法執行本次新增與修改的測試；所有關於測試行為的判斷都是讀程式碼得到的，不是跑出來的。 |
| codegraph | 略過 | preflight 回報未安裝，呼叫圖導覽改以 grep 逐條列出所有到達 datasource.runQuery() 的路徑。 |
| ncr-fresh-eyes (subagent) | 略過 | 本次執行環境沒有 subagent 派送能力（工具清單內沒有 Agent / Task 工具），無法派出未被本 skill 塑形過的第一眼審查。依 SKILL.md 規定不由主 agent 自行模擬，因此本報告缺少 fresh-eyes 這一層獨立視角。 |
| ncr-quality-check (subagent) | 略過 | 同上，無法派出。報告 JSON 只經過 report_model.py 的機械驗證（結論一致性、每則 finding 有 fix、每個維度有結論、略過的掃描有理由），沒有經過獨立的品質複查。 |

<details>
<summary>Suggestion（2）</summary>

#### F-001 兩個入口補上的 applyTemplateVariables 沒有取代下游那一次，同一個 query 會被插值 2～3 次 — `public/app/plugins/datasource/loki/querySplitting.ts:296`

面向 I 回溯分析 · Suggestion

**問題**：把所有會抵達 backend 的路徑列出來：(1) datasource.query() → runQuery()；(2) datasource.query() → runSplitQuery() → runSplitGroupedQueries() → datasource.runQuery()；(3) datasource.query() → runShardSplitQuery() → splitQueriesByStreamShard() → runSplitQuery() → runSplitGroupedQueries() → datasource.runQuery()。三條路徑最後都經過 LokiDatasource.runQuery()（datasource.ts:350）的 super.query()，而 DataSourceWithBackend.query() 會對每個 target 再呼叫一次 applyTemplateVariables（DataSourceWithBackend.ts:185）——沒有任何一條路徑會跳過它。所以路徑 (2) 從 1 次變成 2 次，路徑 (3) 從 2 次變成 3 次（runShardSplitQuery 一次、每個 shard sub-request 進 runSplitQuery 各一次、super.query() 再一次）。

applyTemplateVariables 不是 idempotent 的。它內部呼叫 addAdHocFilters()（datasource.ts:1127），而 addLabelToQuery() 在 labelType 未知、query 帶 parser 且 stream selector 已有 matcher 時，會走到 addFilterAsLabelFilter()（modifyQuery.ts:511）——那條路徑無條件 append 一段 `| key="value"` 形式的 label filter，完全沒有去重；對照 addFilterToStreamSelector()（modifyQuery.ts:494）是有 labelExists() 去重的。因此在 request.filters 非空時，sum(count_over_time({app="foo"} | logfmt [5m])) 搭配 ad hoc filter level=error，經過幾次插值就會累積幾份 level 的 label filter。第二個面向是 templateSrv.replace() 會對已經替換完的字串再跑一次，變數值本身若含有 $name 形式的片段，就會在第二次被當成變數解析。

這個倍數其實已經寫在測試裡：shardQuerySplitting.test.ts:112 把 toHaveBeenCalledTimes(1) 改成 (5)，而 5 = 1（runShardSplitQuery）+ 4（四個 shard sub-request 各進一次 runSplitQuery），還不含被 mock 掉的 runQuery 內那一層。

就結果正確性而言，重複的 label filter 在 LogQL 是等冪的、回傳資料不變，所以這不是 merge blocker；但送給 Loki 的 query 字串會隨 shard 數膨脹，query inspector 看到的東西也不再是實際語意的最小形式。

**證據**：
- `public/app/plugins/datasource/loki/querySplitting.ts:296`
- `public/app/plugins/datasource/loki/shardQuerySplitting.ts:49`
- `public/app/plugins/datasource/loki/shardQuerySplitting.ts:163`
- `public/app/plugins/datasource/loki/datasource.ts:350`
- `packages/grafana-runtime/src/utils/DataSourceWithBackend.ts:185`
- `public/app/plugins/datasource/loki/datasource.ts:1127`
- `public/app/plugins/datasource/loki/modifyQuery.ts:511`
- `public/app/plugins/datasource/loki/modifyQuery.ts:494`
- `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:112`

**修復方向**：讓插值只發生一次，兩個方向擇一：(a) 分割階段其實只需要「插值後的值」來做決策（querySupportsSplitting / isLogsQuery / calculateStep(query.step) / getSelectorForShardValues），可以算出插值後的 expr 與 step 供分組與 shard 值查詢使用，而 requests 內實際送出的 targets 維持原始版本，由 super.query() 做唯一一次插值；(b) 若要保留現在的寫法，就得讓下游知道 target 已插值——例如在 runSplitQuery 中偵測 request 來自 runShardSplitQuery 時略過該 map，並在 sub-request 上帶一個旗標讓 DataSourceWithBackend 跳過重複套用。無論走哪一條，都補一個帶 request.filters 的測試把「ad hoc filter 在最終 expr 只出現一次」釘住。

#### F-002 兩個新測試 mock 掉 runQuery，剛好把「最終送出的 query」擋在斷言之外；新引入的 request.filters 行為沒有測試 — `public/app/plugins/datasource/loki/querySplitting.test.ts:75`

面向 G 測試 · Suggestion

**問題**：兩個新測試都用 jest.spyOn(datasource, 'runQuery').mockReturnValue(...) 攔在 runQuery 這一層，而 F-001 講的第二次 applyTemplateVariables 正好發生在 runQuery 內部的 super.query()。結果是測試能證明「分割前有插值」，卻剛好看不到最終送到 Grafana 的 api/ds/query endpoint 的 expr 是什麼、被插值了幾次——也就是這個改動最需要被釘住的那一段。

另外，這個 diff 把 request.filters 補成 applyTemplateVariables 的第三個參數（shardQuerySplitting.ts:52、querySplitting.ts:299）。改動前 shard 路徑呼叫的是 interpolateVariablesInQueries(request.targets, request.scopedVars)，只有兩個參數、不帶 adhocFilters，所以「在分割階段就套用 ad hoc filter」是這個 MR 帶進來的新行為；它會直接影響 getSelectorForShardValues() 拿到的 selector，也就是 fetchLabelValues('__stream_shard__', { streamSelector }) 查的範圍。兩個新測試都沒有設定 request.filters，這條新行為目前零覆蓋。

**證據**：
- `public/app/plugins/datasource/loki/querySplitting.test.ts:75`
- `public/app/plugins/datasource/loki/querySplitting.test.ts:83`
- `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:84`
- `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:92`
- `public/app/plugins/datasource/loki/shardQuerySplitting.ts:52`
- `public/app/plugins/datasource/loki/querySplitting.ts:299`

**修復方向**：補一個帶 filters 的 case：createRequest([{ expr: 'sum(count_over_time({app="foo"} | logfmt [5m]))', refId: 'A' }], { filters: [{ key: 'level', operator: '=', value: 'error' }] })，並把 spy 下移一層（改 mock getBackendSrv().fetch，或保留 runQuery spy 但在斷言裡再手動跑一次 datasource.applyTemplateVariables 模擬 super.query()），斷言最終 expr 裡 `| level=` 只出現一次。shard 路徑再加一個斷言：fetchLabelValues 收到的 streamSelector 含有 ad hoc filter。

</details>

<details>
<summary>Nit（2）</summary>

#### F-003 toHaveBeenCalledTimes(5) 沒有說明 5 是怎麼算出來的 — `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:112`

面向 G 測試 · Nit

**問題**：同一個檔案裡其他次數斷言都有註解交代算式（例如「5 shards, 3 groups + empty shard group, 4 requests * 3 days, 3 chunks, 3 requests = 12 requests」）。這一行從 1 改成 5，卻沒有任何說明；而 5 恰好就是 F-001 描述的倍數（1 次入口 + 4 個 sub-request），是這次改動最值得被說清楚的一個數字。沒有註解的話，下一個人看到它變動只會照著把數字改掉。

**證據**：
- `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:112`
- `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:80`
- `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:106`

**修復方向**：比照鄰近斷言加一行註解，例如 `// 1 in runShardSplitQuery + 1 per shard sub-request (4 requests)`；若採納 F-001 的 (a) 方向，這個數字會回到 1，註解也可以順勢說明為什麼。

#### F-004 兩個入口現在跑同一組三步驟 pipeline，filter 順序卻是相反的 — `public/app/plugins/datasource/loki/querySplitting.ts:296`

面向 B 簡潔 · Nit

**問題**：runSplitQuery 是 filter(!hide) → filter(expr) → map(applyTemplateVariables)，runShardSplitQuery 是 filter(expr) → filter(!hide) → map(applyTemplateVariables)。語意相同，改動後兩段程式碼只差在前兩個 filter 的順序。這種「幾乎一樣但不完全一樣」是之後最容易只改到一邊的形狀——例如若採納 F-001 把 filter 移到插值之後，兩邊必須同步調整。

**證據**：
- `public/app/plugins/datasource/loki/querySplitting.ts:296`
- `public/app/plugins/datasource/loki/shardQuerySplitting.ts:49`

**修復方向**：把順序統一（建議都用 filter(!hide) → filter(expr) → map，先丟掉不會執行的 query 再做較貴的 filter）。以目前只有兩處來說，統一順序就夠了；若之後出現第三個入口，再抽成 querySplitting.ts 匯出的 prepareSplittingTargets(datasource, request) helper。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 兩個新測試把 templateSrv.replace mock 成會把 $__auto 換成 5m，並據此斷言 expr === 'count_over_time({a="b"}[5m])'。真實的 applyTemplateVariables 在正式環境也會把 $__auto 換掉嗎？

面向 G 測試

**背景**：LokiDatasource.applyTemplateVariables（datasource.ts:1113）刻意把 __auto、__interval、__interval_ms、__range、__range_s、__range_ms 從 scopedVars 解構掉，註解寫明是要留給 backend 插值；而它只把 __interval 與 __interval_ms 以 passthrough 形式（value: '$__interval'）加回 variables，__auto 沒有加回去。因此 $__auto 會不會在前端被替換，取決於 templateSrv 自己的 variable index（Scenes 情境）裡有沒有這個變數。我在這個 repo 搜到的 __auto 只有 dashboard-scene/utils/utils.ts:249-273 的 $__auto_interval_ 轉換，沒有找到會讓 templateSrv.replace 解析 $__auto 的註冊點，但也無法從靜態程式碼排除 Scenes 執行期會提供它。順帶一提，這個機制對 $__range 是有利的：因為 __range 同樣被留給 backend，querySupportsSplitting() 裡的 isQueryWithRangeVariable() 在插值之後仍然看得到 $__range，那道保護沒有被這次改動繞過。

**如何確認**：用真實的 TemplateSrv，在 Scenes dashboard 與 Explore 兩種情境各跑一次 applyTemplateVariables，看回傳的 expr 裡 $__auto 有沒有被替換。若沒有，測試斷言值應改成 count_over_time({a="b"}[$__auto])，並改用一個普通的 dashboard 變數（例如 $range）來示範「執行前已插值」這件事。

#### Q-002 分割後的 request 現在帶著插值過的 targets 進入 trackGroupedQueries，其中 legendFormat 是未經 obfuscate 就上報的欄位——analytics 這邊預期收到的是插值前還是插值後的值？

面向 C 安全

**背景**：runSplitQuery 是用插值後的 queries 組出 requests（querySplitting.ts:296-347），最後傳給 trackGroupedQueries（querySplitting.ts:353）→ trackQuery（tracking.ts:152-192）。expr 這一側沒有問題：obfuscated_query 走 obfuscate()（queryUtils.ts:137）會把 String / Identifier / Number 節點洗掉。但 legend: query.legendFormat（tracking.ts:180）是原樣送出的，而 applyTemplateVariables 也會插值 legendFormat（datasource.ts:1133）。改動前這裡拿到的是作者寫的 {{level}} $env，改動後是解析過的值。trackQuery 只在 app === CoreApp.Explore 時上報，而我無法從這個 repo 判斷 Explore 的 request 實際帶什麼樣的 scopedVars、以及 legend 欄位在 telemetry schema 上的既有約定。

**如何確認**：確認 grafana_explore_loki_query_executed 的 legend 欄位預期是插值前還是插值後的值。若應維持插值前，把原始 targets 一併保留在 LokiGroupedRequest 上（或改讓 trackGroupedQueries 從 originalRequest.targets 取 legendFormat）即可，改動很小。

</details>
