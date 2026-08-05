## 審查結論：Request Changes

> Critical 1 · Suggestion 10 · Nit 7 · 未驗證提問 5
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：命名與可讀性各一件（F-011、F-014）。多數檔案的風格是機械式重構（自訂 styled FlexBox 換成共用 Flex 元件），品質一致、沒有問題。
- **B 簡潔**（未通過）：兩處死碼（F-013、F-016），另有一處死碼併在 F-012 裡一起講（它和 culprit 被移除是同一次編輯的兩面）。ruff 的預設規則抓不到這幾種——未使用的 method 與巢狀函式不在 F401/F811 範圍，掃描結果 in_diff=0 也印證了這一點。
- **D API 慣例**（未通過）：browser reporting collector 的批次語意與欄位互斥驗證各一件（F-006、F-007）。URL 命名、HTTP verb 與 idempotency、PII 不入 URL 都符合慣例；回應時間見 Q-003。
- **E 架構**（未通過）：三件（F-003、F-009、F-010）：刪除路徑的例外被吞掉、錯誤抓取的失敗與空集合無法區分、analytics 記在 feature gate 之前。
- **F 資料取用與資料庫**（未通過）：一件 Critical（F-001，時間單位跨界未宣告，正好命中 F-6 的「值以沒人宣告過的格式跨越邊界」）與兩件 Suggestion（F-002 仰賴 dict 順序做位置配對；F-017 在留言迴圈裡逐筆遠端查詢並把失敗吞成空字串）。
- **G 測試**（未通過）：F-008：本次新增/修改的測試有三處只驗證了 mock 或放寬了原本的斷言，其中 tests/sentry/replays/tasks/test_delete_replays_bulk.py:97 改的那一行剛好是唯一一支和 PR 標題相關的測試，卻因為 delete_matched_rows 被 @patch 掉而完全沒有執行到它要保護的修正。
- **H 非 Python 檔**（未通過）：三件。F-004 與 F-005 是條件分支的覆蓋不完整：一個過濾器只在搜尋分支生效、一個 feature 分支渲染空表格；F-018 是 grouping info 面板移除 Type 列之後，五段只存在於該處的說明文字沒有去處、且兩個 switch 分支變成完全相同。其餘數十個 .tsx 的 Flex 重構逐一比對過 align/justify/gap 與被刪掉的 styled component，語意一致；唯一沒能確認的是 traceWaterfall.tsx:712 的 height:100%，見 Q-002。devservices/config.yml 新增的 tracing mode 與其 consumer/program 定義前後一致。
- **I 回溯分析**（未通過）：F-015：useTraceItemAttributeKeys 的回傳型別放寬。其餘簽章變更都逐一 grep 過呼叫端並確認已同步：get_condition_query_groups / get_groups_to_fire 新增 dcg_to_slow_conditions 參數（tests/sentry/workflow_engine/processors/test_delayed_workflow.py 已更新，且 EventRedisData.dcg_ids 與 dcg_to_groups.keys() 是同一個集合——delayed_workflow.py:166-175——所以提取共用不會產生 KeyError）；Visualize 建構子由 string[] 改為 string、Visualize.fromJSON 改回傳陣列（唯一 production 呼叫端 aggregateFields.tsx:116 已改）；useTraceItemAttributeValues 更名（traceItemSearchQueryBuilder.tsx 與 transactionNameSearchBar.tsx 已改）；MatchedRow.max_segment_id 改為 int | None（唯一消費者 _make_recording_filenames 已加防護）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該不該做？**：送審的 diff 與 PR 標題不符，這一點必須先講。標題是「Replays Self-Serve Bulk Delete System」，但 `git merge-base 49a275847631 ea188e2d736f` 回傳 49a275847631 本身，代表 base 是該功能的 squash commit `49a27584 feat(replays): Add self-serve bulk deletes (#93864)`，而不是它的 parent。這個範圍實際涵蓋 32 個已合併進 master 的獨立 PR（explore、dashboards、codecov、workflow_engine、feedback、preprod、grouping、migrations…），bulk delete 功能本身完全不在 diff 內。本次審查以實際 diff 為準，並在 open_questions 補上針對 bulk delete 系統本身的提問（Q-001、Q-005）。
- **該在這個 MR 做？**：以送審內容論，這不是一個 merge request 該有的形狀：106 個檔案、32 個彼此無關的變更（含一次 revert `668229d1 Revert "chore(autofix): Change default automation tuning from 'off' to 'low'"`、兩支 migration 被改成 no-op、數十個前端元件的 Flex 重構）。任何一項出問題都無法單獨 revert。若這是 range 取錯，修正 base 即可；若真的要一次合併，建議拆成獨立 MR。
- **該在這個時機做？**：src/sentry/migrations/0917_convert_org_saved_searches_to_views.py:14 與 0920_convert_org_saved_searches_to_views_revised.py:13 把 migration 內容改成直接 return，並指向 0921_convert_org_saved_searches_to_views_rerevised.py。已確認該檔案存在於 src/sentry/migrations/，指向沒有斷。但「修改一支已進入 migration 序列的檔案」對已經套用過它的環境是不可逆的，這類操作應該和其它 32 個變更分開、單獨評估上線時機（見 Q-004）。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且本機無 semgrep-rules 目錄，本次未執行 SAST 掃描 |
| ruff | 已執行 | in_diff 0、outside_diff 159 |
| ty | 略過 | ty 未安裝（不在 PATH 上），本次未執行 Python 型別檢查；因此 dimension I 的 signature 相容性完全以 grep 逐一驗證 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上），本次未執行 JavaScript/TypeScript lint；diff 中 60 餘個 .tsx 檔完全沒有工具覆蓋，只有人工審查 |
| codegraph | 略過 | codegraph 未安裝，結構與傳遞性查詢改用 grep 全文搜尋；caller 完整性的結論都附上實際 grep 到的 file:line |
| ncr-fresh-eyes | 略過 | 在審查 agent 內無法派工（已用 ToolSearch 查過 Agent / Task / spawn / teammate 等關鍵字，只有 SendMessage 而沒有建立 agent 的入口）。改由外部 orchestrator 代為派工，結果在本報告第一版通過驗證之後才回到手上——也就是說 fresh-eyes 沒有在 Phase 3 步驟 1 的順序上執行，它的「未受框架影響」只對它自己成立，對本報告的其餘部分不成立，這一點如實揭露。回傳的 6 條觀察已逐條回到程式碼驗證：F-005、F-014 與 Q-004 與既有發現重複，結論一致，不重複計列；另外兩條經確認後新增為 F-017、F-018；其餘一條（PR comment 的 culprit 被移除）併入 F-012。fresh-eyes 自陳有一條引用的是 diff offset 而非檔案行號，本報告採用的行號全部重新以 grep -n 對 checkout 確認過 |
| ncr-quality-check | 略過 | 同上，無法派工 subagent，本報告未經獨立的 quality-check 覆核；report_model.py validate 只保證結構與結論一致，不保證每條 evidence 的正確。作者自行複核過所有 file:line 是否指向 repo 內的真實位置 |

### Critical

#### F-001 replay breadcrumb 與 error 以不同時間單位比較，時序合併永遠不會生效 — `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:153-155（gen_request_data 的 while 條件：error_events[error_idx]["timestamp"] < event.get("timestamp", 0)）`

面向 F 資料取用與資料庫 · Critical

**問題**：gen_request_data 的存在理由就是它 docstring 寫的「Generate log messages from events and errors in chronological order」，做法是用一個 merge 迴圈把 error 插進 breadcrumb 串流。但兩邊的 timestamp 來自不同系統、單位不同：error 來自 nodestore event payload，是 unix 秒（約 1.7e9）；breadcrumb 來自 recording segment 的 rrweb 事件頂層欄位，是 unix 毫秒（約 1.7e12）。因此 error_ts < event_ts 對任何真實資料都恆為真，所有 error 訊息會在第一筆 breadcrumb 之前一次倒完，時序完全失去意義，而送進 Seer 的 prompt 看起來仍然合法——這正是不會有人發現的失敗方式。已找過反證：diff 內、as_log_message、fetch_error_details 與 get_request_data 都沒有任何單位換算或正規化。新加的兩支測試（tests/sentry/replays/test_project_replay_summarize_breadcrumbs.py:188、:254）用 float(now.timestamp()) 當 segment 事件的 timestamp，也就是用「秒」餵一個真實資料是「毫秒」的欄位，所以測試會過。順帶一提 tests/sentry/replays/unit/test_event_parser.py:63 與 :329 這兩個既有 fixture 對同一個欄位一個寫秒、一個寫毫秒，這個單位契約從來沒被宣告過，本身就是這個 bug 的溫床。

**證據**：
- `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:153-155（gen_request_data 的 while 條件：error_events[error_idx]["timestamp"] < event.get("timestamp", 0)）`
- `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:115（ErrorEvent.timestamp 來自 nodestore payload 的 data.get("timestamp", 0.0)）`
- `src/sentry/eventstore/models.py:117-118（datetime.fromtimestamp(self.data["timestamp"])，證明 nodestore event payload 的 timestamp 是「秒」）`
- `static/app/utils/replays/replayReader.tsx:280（firstMeta.timestamp > startTimestampMs，其中 startTimestampMs = replayRecord.started_at.getTime()，證明 rrweb 事件頂層 timestamp 是「毫秒」）`
- `src/sentry/replays/usecases/ingest/event_parser.py:182（as_log_message 讀的正是 rrweb 事件頂層的 event["timestamp"]）`

**修復方向**：在 fetch_error_details 建 ErrorEvent 時就把單位收斂到毫秒：`timestamp=data.get("timestamp", 0.0) * 1000`，並在 ErrorEvent 的欄位旁註明「毫秒 epoch，與 rrweb 事件頂層 timestamp 同單位」。測試要改用真實量級的值（segment 事件用 int(now.timestamp() * 1000)、error 用 now.timestamp() - 1），這樣單位一旦再被弄錯，斷言會直接失敗而不是靜靜通過。順手把 tests/sentry/replays/unit/test_event_parser.py:63 的 fixture 也改成毫秒，讓兩處一致。

<details>
<summary>Suggestion（10）</summary>

#### F-002 fetch_error_details 用位置配對 error_ids 與 get_multi 的結果，但回傳順序沒有保證 — `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:118（for event_id, data in zip(error_ids, events.values())）`

面向 F 資料取用與資料庫 · Suggestion

**問題**：zip(error_ids, events.values()) 假設 get_multi 回傳的 dict 會照 error_ids 的順序排列，NodeStorage 沒有這個保證。第一，它會對 id_list 去重，只要 replay 的 error_ids 有重複值，events 就比 error_ids 短，zip 會截斷，最後幾筆 error 直接消失；第二，部分命中快取時回傳順序是「未命中的照原序、命中的接在後面」；第三，全命中時順序來自 cache backend 的 get_many。任一情況下 error_ids[i] 都會被貼到別筆 event 的資料上。已找過反證：`if data is not None` 的過濾發生在 zip 之後，攔不到錯位；也沒有其他地方重新對齊。目前只有 ErrorEvent["id"] 會貼錯（generate_error_log_message 只用 title/message/timestamp），所以錯位的影響是潛伏的，但去重造成的「整筆 error 遺失」是現在就會發生的。

**證據**：
- `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:118（for event_id, data in zip(error_ids, events.values())）`
- `src/sentry/nodestore/base.py:184（id_list = list(dict.fromkeys(id_list))，get_multi 會去重）`
- `src/sentry/nodestore/base.py:189-192（全部命中快取時直接 return cache_items，順序來自 cache backend 的 get_many）`
- `src/sentry/nodestore/base.py:199-205（部分命中時先放 uncached_ids 再 items.update(cache_items)，快取命中的 id 會被排到最後）`

**修復方向**：改成用 node id 反查而不是位置配對：

```python
node_ids = {event_id: Event.generate_node_id(project_id, event_id=event_id) for event_id in error_ids}
events = nodestore.backend.get_multi(list(node_ids.values()))
return [
    ErrorEvent(category="error", id=event_id, title=data.get("title", ""), ...)
    for event_id, node_id in node_ids.items()
    if (data := events.get(node_id)) is not None
]
```

#### F-003 刪除 recording blob 的例外被靜靜吞掉，replay 仍然會被標記為已刪除 — `src/sentry/replays/usecases/delete.py:73-75（delete_replay_recordings：with ThreadPoolExecutor(...) as pool: pool.map(...)，回傳的 generator 從未被消費）`

面向 E 架構 · Suggestion

**問題**：危險操作列舉的結果：這個 PR 標題所指的 bulk delete 系統，抵達「刪除 blob」的路徑只有 delete_matched_rows → delete_replay_recordings → _delete_if_exists 一條，而這條路徑上沒有任何 guard 檢查刪除是否成功。Executor.map 會立刻 submit 所有 future 並回傳一個 generator；因為沒有人 iterate 它，除了 NotFound 以外的任何例外（storage 5xx、權限錯誤、逾時）都只會留在 future 裡，隨著 with 區塊結束被丟棄。接著 delete_matched_rows 仍然呼叫 delete_replays 送出 archive event，於是使用者看到「已刪除」，錄影檔卻留在 storage 裡。這是既有程式碼、不是這次 diff 引入的（本次只在同一支函式群裡的 _make_recording_filenames 加了 null 防護），但它就在被修改的函式旁邊，而且是這個 self-serve delete 功能的核心保證，所以列出來。

**證據**：
- `src/sentry/replays/usecases/delete.py:73-75（delete_replay_recordings：with ThreadPoolExecutor(...) as pool: pool.map(...)，回傳的 generator 從未被消費）`
- `src/sentry/replays/usecases/delete.py:78-83（_delete_if_exists 只吞 NotFound，其餘例外會留在 future 裡）`
- `src/sentry/replays/usecases/delete.py:54-62（delete_matched_rows：跑完所有 delete_replay_recordings 之後無條件呼叫 delete_replays 送出 archive event）`

**修復方向**：把 map 換成會傳播例外的形式：

```python
def delete_replay_recordings(project_id: int, row: MatchedRow) -> None:
    with cf.ThreadPoolExecutor(max_workers=100) as pool:
        for _ in pool.map(_delete_if_exists, _make_recording_filenames(project_id, row)):
            pass
```

真的要容忍部分失敗的話，至少收集失敗數，並在 delete_matched_rows 決定是否送出 archive event 之前檢查，不要讓「刪 blob」與「標記已刪除」無條件連動。

#### F-004 HIDDEN_ATTRIBUTES 只在有搜尋字串時才生效，預設畫面仍然顯示這些欄位 — `static/app/views/performance/newTraceDetails/traceDrawer/details/span/eapSections/attributes.tsx:59-70`

面向 H 非 Python 檔 · Suggestion

**問題**：useMemo 內是 `const sorted = sortAttributes(attributes); if (!searchQuery.trim()) { return sorted; }`，早退發生在 filter 之前。新加的 `!HIDDEN_ATTRIBUTES.includes(attribute.name)` 條件被放在後面那個 filter 裡，所以只有使用者輸入搜尋字串時才會隱藏 is_segment / project_id / received——而「沒有搜尋字串」正是這個面板的預設狀態，也是絕大多數使用者看到的狀態。已找過反證：sortAttributes 沒有做任何過濾，元件內（:135、:139）也沒有第二層 filter。

**證據**：
- `static/app/views/performance/newTraceDetails/traceDrawer/details/span/eapSections/attributes.tsx:59-70`
- `static/app/views/performance/newTraceDetails/traceDrawer/details/span/eapSections/attributes.tsx:34（const HIDDEN_ATTRIBUTES = ['is_segment', 'project_id', 'received']）`

**修復方向**：把隱藏條件提到早退之前：

```tsx
const visible = sortAttributes(attributes).filter(
  attribute => !HIDDEN_ATTRIBUTES.includes(attribute.name)
);
if (!searchQuery.trim()) {
  return visible;
}
return visible.filter(attribute =>
  attribute.name.toLowerCase().trim().includes(searchQuery.toLowerCase().trim())
);
```

#### F-005 新的 TableWidgetVisualization 分支渲染空表格，而它的 feature flag 在 repo 內找不到註冊點 — `static/app/views/dashboards/widgetCard/chart.tsx:164-174（flag 開啟時傳入 columns={[]} 與 tableData={{data: [], meta: {fields: {}, units: {}}}}）`

面向 H 非 Python 檔 · Suggestion

**問題**：兩個問題疊在同一段。第一，flag 開啟時渲染的是硬寫死的空 columns 與空 tableData，同一個 map 迴圈裡的 result 被完全丟掉，畫面會是一張沒有標題也沒有資料的表——這不是降級，是明確的錯誤畫面。第二，`use-table-widget-visualization` 這個字串在整個 repo 只出現這一次，後端沒有任何地方註冊它，所以 organization.features 永遠不會包含它：這條分支目前無法被觸發，也無法被 roll out。合起來的意思是這段接線現在既不能用也沒被測到（新增的 tableWidgetVisualization.spec.tsx 測的是元件本身，不是這個接線）。

**證據**：
- `static/app/views/dashboards/widgetCard/chart.tsx:164-174（flag 開啟時傳入 columns={[]} 與 tableData={{data: [], meta: {fields: {}, units: {}}}}）`
- `static/app/views/dashboards/widgetCard/chart.tsx:176-190（else 分支的 StyledSimpleTableChart 才有 result.data / result.meta）`
- `grep -rn "use-table-widget-visualization" 全 repo 只有 chart.tsx:164 一處命中，src/sentry/features/ 底下沒有任何註冊`

**修復方向**：如果這是刻意的鷹架，加上 TODO 說明什麼時候補資料，或先不要進主線；如果要留下，兩件事都要補：在 src/sentry/features/temporary.py 註冊對應的 flag（並讓前端字串與註冊名一致），以及把 result 真的接上去——tableData={{data: result.data, meta: result.meta}}、columns 由 fields/fieldAliases 推導，再加一個 widgetCard 層級的測試涵蓋 flag 開啟時的渲染。

#### F-006 browser report 批次中只要有一筆不合法就整批 422，已驗證的報告一併丟棄 — `src/sentry/issues/endpoints/browser_reporting_collector.py:110-119（for report in raw_data: 驗證失敗立即 return 422）`

面向 D API 慣例 · Suggestion

**問題**：Browser Reporting API 是瀏覽器主動批次上報，一個 POST 裡的多筆 report 來自不同時間點、不同瀏覽器行為，彼此沒有交易關係。現在的寫法是「第一筆不合法就整批拒收」，於是一筆來自舊版瀏覽器、欄位形狀不同的 report 會讓同批其他完全合法的 report 全部收不到；而 Reporting API 會重試整批，attempts 遞增，同一批會一直失敗到被瀏覽器丟棄。這比舊行為（BrowserReport(**report) 直接 TypeError → 500）好，但沒有走到位。更關鍵的是這個 endpoint 的 docstring 寫明它存在的目的是「收集真實世界資料，看瀏覽器到底送什麼」，把不認得的資料整批退掉，正好會系統性地漏掉最想觀察的那一群。

**證據**：
- `src/sentry/issues/endpoints/browser_reporting_collector.py:110-119（for report in raw_data: 驗證失敗立即 return 422）`
- `src/sentry/issues/endpoints/browser_reporting_collector.py:120-130（通過驗證的 report 要等整批都合法才會進 metrics.incr）`

**修復方向**：改成逐筆處理：合法的照樣 metrics.incr，不合法的記一個 browser_reporting.invalid_report 的 metric 加上 logger.warning，最後一律回 200（或回 200 附帶 accepted/rejected 計數）。這樣既保留驗證訊號，又不會讓單一壞資料毀掉整批。

#### F-007 age / timestamp 的互斥驗證用真值判斷，且兩者皆缺席時完全不會被擋下 — `src/sentry/issues/endpoints/browser_reporting_collector.py:50-54（validate_timestamp：if self.initial_data.get("age"): raise）`

面向 D API 慣例 · Suggestion

**問題**：兩個問題。其一，`if self.initial_data.get("age")` 是真值判斷而不是存在判斷：age: 0（報告產生後立刻送出，這是常見情況）或 timestamp: 0 都是 falsy，所以同時帶著 timestamp 與 age: 0 的報告會通過互斥檢查。其二，也比較嚴重：DRF 的 validate_<field> 只有在該欄位出現在輸入裡才會被呼叫，而兩個欄位都是 required=False，所以「age 與 timestamp 都不存在」的報告完全不會觸發任何驗證、直接通過——但序列化器上方註解引用的兩份 spec（Working Draft 與 Editor's Draft）都要求兩者擇一必須存在。另外 validate_timestamp 的 docstring 寫成「Validate that age is absent, but timestamp is present」，讀起來像在描述前置條件，而不是它實際做的檢查。

**證據**：
- `src/sentry/issues/endpoints/browser_reporting_collector.py:50-54（validate_timestamp：if self.initial_data.get("age"): raise）`
- `src/sentry/issues/endpoints/browser_reporting_collector.py:56-60（validate_age：if self.initial_data.get("timestamp"): raise）`
- `src/sentry/issues/endpoints/browser_reporting_collector.py:47-48（age 與 timestamp 都是 required=False）`
- `tests/sentry/api/endpoints/test_browser_reporting_collector.py:164（test_mixed_fields 只涵蓋「兩者都在」）`

**修復方向**：把互斥與必要性一起搬到 serializer 層級的 validate()，用存在判斷而不是真值判斷：

```python
def validate(self, attrs):
    has_age = "age" in self.initial_data
    has_timestamp = "timestamp" in self.initial_data
    if has_age == has_timestamp:
        raise serializers.ValidationError("Exactly one of `age` or `timestamp` is required")
    return attrs
```

並補一筆「兩者皆缺」與一筆「timestamp 存在、age=0」的測試。docstring 改寫成描述檢查本身。

#### F-008 本次新增/修改的測試有三處只驗證了 mock 或放寬了既有斷言 — `tests/sentry/replays/tasks/test_delete_replays_bulk.py:97（max_segment_id 由 0 改成 None，但同一支測試用 @patch 把 delete_matched_rows 換掉了）`

面向 G 測試 · Suggestion

**問題**：第一項最值得談：max_segment_id: 0 → None 是為了保護 delete.py:88-90 新加的 null 防護，但那支測試把 delete_matched_rows 整個 @patch 掉，只斷言 mock 被呼叫時帶了哪個 dict——被修改的那行程式碼在測試裡一次都沒有執行。這正是「設定 mock 回傳 X 然後斷言 X 回來了」的形狀。第二、三項是覆蓋率倒退：BrowserReportsJSONParser 存在的唯一理由是處理 application/reports+json，改完之後只剩 test_rejects_invalid_content_type 這支反向測試碰得到 parser_classes，正向路徑不再有任何測試送出真實瀏覽器會用的 content type；同時原本斷言 browser_report_received 有被記錄的那條也一起消失了。

**證據**：
- `tests/sentry/replays/tasks/test_delete_replays_bulk.py:97（max_segment_id 由 0 改成 None，但同一支測試用 @patch 把 delete_matched_rows 換掉了）`
- `src/sentry/replays/usecases/delete.py:88-90（這次要保護的 null 防護在該測試中一次都沒有執行）`
- `tests/sentry/api/endpoints/test_browser_reporting_collector.py:66（test_basic 取代了 test_logs_request_data_if_option_enabled，拿掉了 logger.info 斷言與 content_type="application/reports+json"）`
- `tests/sentry/api/endpoints/test_browser_reporting_collector.py:88（test_handles_multiple_reports_both_specs 同樣改用預設 content type）`

**修復方向**：(1) 針對 _make_recording_filenames 直接寫單元測試：assert _make_recording_filenames(1, {"max_segment_id": None, ...}) == []，並補一筆 max_segment_id=0 應該回傳 segment 0 檔名的案例——0 與 None 的區別正是這次修的東西。(2) test_basic 與 test_handles_multiple_reports_both_specs 把 content_type="application/reports+json" 加回去，另外保留一支用預設 JSON content type 的案例，兩條路徑都要有。(3) 把 logger.info 的斷言加回 test_basic。

#### F-009 fetch_error_details 吞掉所有例外回傳空陣列，且對 error_ids 的數量沒有上限 — `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:105-123（try / except Exception → capture_exception → return []）`

面向 E 架構 · Suggestion

**問題**：兩點。其一，nodestore 掛掉、逾時、或 payload 解析失敗，都會回傳 []，而 [] 同時也是「這個 replay 本來就沒有 error」的正常結果。呼叫端沒有任何方式分辨，於是使用者拿到一份看起來完整、實際上少了整個 error 脈絡的摘要，而且不會有任何提示——這是 F-001 之外第二個「壞掉的樣子和正常一模一樣」的地方。sentry_sdk.capture_exception 會留下紀錄，但那是給工程師事後看的，不影響這次回應。其二，error_ids 直接來自 Snuba 的 replay 聚合結果，一個長 session 可以關聯到大量 error，這裡沒有上限就整包丟進 nodestore.get_multi，對一個同步 GET endpoint 來說是不受控的扇出（見 Q-003）。

**證據**：
- `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:105-123（try / except Exception → capture_exception → return []）`
- `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:84（error_ids 直接取自 Snuba 回應，沒有截斷）`
- `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:94（fetch_error_details(project_id=project.id, error_ids=error_ids)）`

**修復方向**：縮小 except 範圍（只吞真正預期的 nodestore 例外），並讓呼叫端知道發生了什麼——例如回傳 tuple[list[ErrorEvent], bool]，或讓例外往上冒由 endpoint 決定，至少在回應裡標記 error context 不完整。另外對 error_ids 設一個明確上限（例如 error_ids[:100]）並把截斷的理由寫進註解，跟 fetch_segments_metadata 已經有分頁是同一個道理。

#### F-012 Suspect Issues 留言的 culprit 被移除、換成常常是空字串的 environment，並留下兩個死掉的 format_comment_subtitle — `src/sentry/integrations/source_code_management/commit_context.py:143（新模板 "* ‼️ [**{title}**]({url}){environment}\n" 沒有 subtitle 欄位）`

面向 B 簡潔 · Suggestion

**問題**：帶進這段變更的 commit 是 `fd4ab7b5 feat(scm): add environment infomation to suspect commit prs (#93025)`，標題只說「加入 environment」，但實際上同時把 culprit 從每一則 merged PR 留言裡拿掉了：舊模板是 `- ‼️ **{title}** \`{subtitle}\` [View Issue]({url})`，新模板沒有 subtitle 欄位。而換上來的 environment 在 get_environment_info 取不到 recommended event、或該 event 沒有 environment 時就是空字串，所以對一部分 issue 來說是「culprit 沒了，也沒有東西補上」。這是使用者看得到的輸出退化，卻沒有出現在 commit 標題裡。連帶的結果是 format_comment_subtitle 在 GitHub 與 GitLab 兩邊都成了死碼（grep -rn "format_comment_subtitle" --include=*.py 只剩兩處定義），而且基底類別沒有把它列為 abstractmethod，所以留著也不是為了滿足介面。

**證據**：
- `src/sentry/integrations/source_code_management/commit_context.py:143（新模板 "* ‼️ [**{title}**]({url}){environment}\n" 沒有 subtitle 欄位）`
- `src/sentry/integrations/github/integration.py:394 與 src/sentry/integrations/gitlab/integration.py:240（format_comment_subtitle 仍然定義著，但已無任何呼叫端）`
- `src/sentry/integrations/source_code_management/commit_context.py:494（基底類別 PRCommentWorkflow 並未把它宣告成 abstractmethod）`
- `src/sentry/integrations/source_code_management/commit_context.py:584-596（get_environment_info 在取不到 recommended event 或 environment 時回傳空字串）`

**修復方向**：先確認移除 culprit 是刻意的產品決定——如果是，把它寫進 commit 說明或 PR 描述，別讓它藏在一個講 environment 的變更裡；如果不是，模板改成 `* ‼️ [**{title}**]({url}) `{subtitle}`{environment}` 把兩者都留下。無論哪一種，github/integration.py:394 與 gitlab/integration.py:240 的 format_comment_subtitle 都應該一起刪掉，不要留沒有呼叫端的 staticmethod 讓下一個人猜它為什麼在。

#### F-017 get_environment_info 在留言迴圈裡每則 issue 多打一次 Snuba，且用空白 except 把失敗吞成空字串 — `src/sentry/integrations/source_code_management/commit_context.py:584-596（try: issue.get_recommended_event() … except Exception as e: logger.info(..., extra={"issue_id": issue.id, "error": e}); return ""）`

面向 F 資料取用與資料庫 · Suggestion

**問題**：fresh-eyes 把這裡列為「值得再看一眼的直覺」，追下去之後兩點都成立。其一是成本：get_recommended_event 會對 Snuba 下查詢，取不到推薦事件時還會 fallback 到 get_latest_event 再查一次，而 get_comment_body 是在一個最多 5 筆 issue 的 list comprehension 裡逐一呼叫它，再加上 recommended_event.get_environment()——這條路徑原本一次遠端查詢都沒有，現在每則 Suspect Issues 留言最多會多出 5 到 10 次。因為 pr_comment_workflow 是背景 task，延遲不是使用者直接感受得到的，所以不到 Critical，但它是每個 merged PR 都會跑一次的路徑，量會累積。其二是失敗處理：blanket `except Exception` 把任何 Snuba 逾時、retention 邊界、序列化錯誤都變成空字串，和「這個 issue 本來就沒有 environment」完全無法區分——又是一個「壞掉的樣子和正常一模一樣」。另外 extra={"error": e} 傳的是例外物件本身，structured logging 拿到的會是 repr，追查時不如 str(e) 或 exc_info=True 有用。

**證據**：
- `src/sentry/integrations/source_code_management/commit_context.py:584-596（try: issue.get_recommended_event() … except Exception as e: logger.info(..., extra={"issue_id": issue.id, "error": e}); return ""）`
- `src/sentry/models/group.py:870-891（get_recommended_event 取不到推薦事件時再 fallback 到 get_latest_event，等於第二次查詢）`
- `src/sentry/models/group.py:274-299（底層 get_recommended_event 對 Dataset.Events / Dataset.IssuePlatform 下 Snuba 查詢，不是便宜的 DB 讀取）`
- `src/sentry/integrations/github/integration.py:404-414（get_comment_body 的 list comprehension 逐一 issue 呼叫 get_environment_info）`
- `src/sentry/integrations/source_code_management/commit_context.py:572（get_top_5_issues_by_count 的 set_limit(5)）`

**修復方向**：把 5 筆 issue 的 environment 一次查完再組留言（get_top_5_issues_by_count 已經有 group_id 清單，可以一次帶條件查詢），而不是在 list comprehension 裡逐筆遠端呼叫。except 收斂成預期的 Snuba/retention 例外並改用 logger.warning + exc_info=True；若查詢失敗，考慮讓整則留言退回沒有 environment 的舊格式並記一個 metric，而不是讓個別 issue 靜靜少一段。

</details>

<details>
<summary>Nit（7）</summary>

#### F-010 preprod assemble 的 analytics 記在 feature gate 之前，會把 404 的請求也算進去 — `src/sentry/preprod/api/endpoints/organization_preprod_artifact_assemble.py:81-86（analytics.record("preprod_artifact.api.assemble", ...)）`

面向 E 架構 · Nit

**問題**：analytics.record 放在 feature flag 檢查之前，所以沒有開通 organizations:preprod-artifact-assemble 的 organization 打進來、拿到 404 的請求，一樣會被記成一次 assemble 事件。這個 event 之後多半會被拿來看功能採用率，混進去的 404 會讓數字失真，而且失真的方向（未開通的組織越多、數字越漂亮）恰好與判讀方向相反。

**證據**：
- `src/sentry/preprod/api/endpoints/organization_preprod_artifact_assemble.py:81-86（analytics.record("preprod_artifact.api.assemble", ...)）`
- `src/sentry/preprod/api/endpoints/organization_preprod_artifact_assemble.py:88-92（緊接著才是 features.has(...)，不通過就回 404）`

**修復方向**：把 analytics.record 移到 features.has 檢查之後、start_span 之前。如果確實想量測「被拒絕的呼叫」，就分開記一個帶 feature_enabled 屬性的事件，讓兩者在資料裡可分。

#### F-011 disable_error_fetching 反向命名，且 get_request_data 就地改動呼叫端傳進來的 list — `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:87-94（disable_error_fetching = request.query_params.get("enable_error_context", "true").lower() == "false"）`

面向 A 風格 · Nit

**問題**：第一，query param 叫 enable_error_context，變數卻叫 disable_error_fetching，中間還隔著一次 == "false" 的反轉，讀到 if disable_error_fetching: 時要在腦中做兩次否定才知道發生什麼；而且 param 名（context）和變數名（fetching）指的東西也不一樣。第二，get_request_data 對傳進來的 error_events 做就地 sort，函式名字只承諾「取得請求資料」，看不出它會改動參數——review-dimensions B-5 說的隱形副作用。目前唯一呼叫端不在乎順序被改，所以只是 Nit。

**證據**：
- `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:87-94（disable_error_fetching = request.query_params.get("enable_error_context", "true").lower() == "false"）`
- `src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:138（get_request_data 內 error_events.sort(key=...)）`

**修復方向**：改成正向命名並與 param 對齊：`enable_error_context = request.query_params.get("enable_error_context", "true").lower() != "false"`，然後 `error_events = fetch_error_details(...) if enable_error_context else []`。sort 改成 `sorted(error_events, key=...)` 產生新 list。

#### F-013 移除 ParameterizationRegexExperiment 後，parametrize_w_experiments 裡的 _handle_regex_match 成為死碼 — `src/sentry/grouping/parameterization.py:335-343（parametrize_w_experiments 內的巢狀 def _handle_regex_match）`

面向 B 簡潔 · Nit

**問題**：這次把 ParameterizationRegexExperiment 整個拿掉，isinstance 分支也隨之簡化成單一路徑，於是 parametrize_w_experiments 裡宣告的 _handle_regex_match 沒有任何呼叫端。ruff 抓不到這個——F 系列規則不涵蓋未使用的巢狀函式，scan 結果 in_diff=0 也印證了這點。同名的另一份在 parameterize_all（:310）裡仍然有用，所以刪錯邊會壞掉，值得標清楚是哪一份。

**證據**：
- `src/sentry/grouping/parameterization.py:335-343（parametrize_w_experiments 內的巢狀 def _handle_regex_match）`
- `src/sentry/grouping/parameterization.py:349（唯一會用到它的 else 分支已被刪除，現在只剩 content = experiment.run(content, _incr_counter)）`
- `src/sentry/grouping/parameterization.py:310,320（同名函式在 parameterize_all 內另有一份，那一份仍在使用）`
- `src/sentry/grouping/parameterization.py:268（ParameterizationExperiment = ParameterizationCallableExperiment，別名現在只等於單一 class）`

**修復方向**：刪掉 parameterization.py:335-343 的那份 _handle_regex_match（保留 :310 那份）。順帶可以把 :268 的別名也移除，讓型別直接寫實名，少一層間接。

#### F-014 PR comment 模板結尾多一個換行，和 join("\n") 疊出空行與尾端空白 — `src/sentry/integrations/source_code_management/commit_context.py:143（MERGED_PR_SINGLE_ISSUE_TEMPLATE = "* ‼️ [**{title}**]({url}){environment}\n"）`

面向 A 風格 · Nit

**問題**：模板自己帶了尾端 \n，外面又用 "\n".join(...)，於是每兩筆 issue 之間會多一個空行，最後一筆之後也多一個。在 Markdown 裡這會把 tight list 變成 loose list（每個項目被包進 <p>，行距變大），並在 <sub> 之前留下多餘空白。測試被更新成接受這個結果，所以是被固定下來的行為而不是意外——但從程式碼看不出這是刻意要的視覺效果。GitLab 那邊更明顯：comment body 直接以換行結尾。

**證據**：
- `src/sentry/integrations/source_code_management/commit_context.py:143（MERGED_PR_SINGLE_ISSUE_TEMPLATE = "* ‼️ [**{title}**]({url}){environment}\n"）`
- `src/sentry/integrations/github/integration.py:404（issue_list = "\n".join([...])）`
- `tests/sentry/integrations/github/tasks/test_pr_comment.py:372-378（期望值裡每兩筆之間一個空行，最後一筆之後兩個空行）`

**修復方向**：二選一，把換行的責任放在一個地方：模板拿掉尾端 \n（維持由 join 負責），或模板保留 \n 而外面改成 "".join(...)。如果確實想要項目之間有空行，在模板旁加一行註解說明是為了 Markdown 的 loose list，否則下一個人會把它當成手滑修掉。

#### F-015 useTraceItemAttributeKeys 的回傳契約變寬，且 retry: false 在改寫中被丟掉 — `static/app/views/explore/hooks/useTraceItemAttributeKeys.tsx:50-54（useApiQuery 換成 useQuery，原本的 staleTime: 0 / refetchOnWindowFocus: false / retry: false 都沒有帶過來）`

面向 I 回溯分析 · Nit

**問題**：改寫前 attributes 一定是一個 TagCollection（至少是 {}），改寫後在尚未取得資料或發生錯誤時會是 undefined。已追過唯一的呼叫端 traceItemAttributeContext.tsx：它用 {...numberAttributes, ...Object.fromEntries(measurements)} 展開，展開 undefined 在 JS 是安全的，所以不會壞掉——但型別契約確實變寬了，下一個直接寫 attributes[key] 的呼叫端就會踩到。另外 staleTime 的預設本來就是 0、refetchOnWindowFocus 有全域預設，都可以省略，但 retry: false 沒有全域預設，改寫後這支查詢失敗時會重試三次；attribute keys 是輸入框的輔助資料，重試三次沒有帶來什麼、只是延後了 UI 的失敗狀態。新回傳的 error 目前沒有任何消費者。

**證據**：
- `static/app/views/explore/hooks/useTraceItemAttributeKeys.tsx:50-54（useApiQuery 換成 useQuery，原本的 staleTime: 0 / refetchOnWindowFocus: false / retry: false 都沒有帶過來）`
- `static/app/views/explore/hooks/useTraceItemAttributeKeys.tsx:56-61（return { attributes: isFetching ? previous : data, error, isLoading: isFetching }）`
- `static/app/views/explore/contexts/traceItemAttributeContext.tsx:44,52（唯一的 production 呼叫端）`
- `static/app/utils/queryClient.tsx:23（refetchOnWindowFocus: false 已是全域預設，省略無妨）`

**修復方向**：在 useQuery 補回 retry: false；回傳處改成 `attributes: (isFetching ? previous : data) ?? {}` 讓型別維持 TagCollection；error 要嘛讓 TraceItemAttributeProvider 真的用起來（例如在輸入框顯示載入失敗），要嘛先不要回傳。

#### F-016 sentry.preprod 的 star import 沒有 __all__，會把 analytics 模組本身帶進命名空間 — `src/sentry/preprod/__init__.py:1（from .analytics import *  # NOQA）`

面向 B 簡潔 · Nit

**問題**：analytics.py 沒有定義 __all__，所以 import * 會把所有不以底線開頭的名字都拉進來，包含它自己 import 的 analytics 模組。結果是 sentry.preprod.analytics 這個名字同時可以指子模組、也可以指 sentry.analytics，取決於 import 順序——這種歧義在測試裡 patch 路徑時最容易咬人。這個 pattern 存在的理由（讓 event class 在 import sentry.preprod 時完成註冊）本身沒問題，只是實作方式太寬。

**證據**：
- `src/sentry/preprod/__init__.py:1（from .analytics import *  # NOQA）`
- `src/sentry/preprod/analytics.py:1（from sentry import analytics，且檔案未定義 __all__）`

**修復方向**：在 analytics.py 加上 `__all__ = ["PreprodArtifactApiAssembleEvent"]`，或把 __init__.py 改成明確的 `from .analytics import PreprodArtifactApiAssembleEvent  # noqa: F401`。

#### F-018 移除 Grouping Information 的 Type 列之後，五種 variant 的說明文字沒有去處，兩個 switch 分支變成完全相同 — `static/app/components/events/groupingInfo/groupingVariant.tsx:101-121（五個 case 原本各自 push 一列帶 QuestionTooltip 的 Type，現在全部移除）`

面向 H 非 Python 檔 · Nit

**問題**：fresh-eyes 提出這一條時說「可能是刻意去重複」，查證後確認是刻意的——commit 標題就寫著 Remove redundant Type row。而「redundant」只對了一半：variant.type 這個值確實可以從 renderTitle 的 By {variant.description} 推得，但每個 case 各自帶的那段解釋（例如 CUSTOM_FINGERPRINT 的「Overrides the default grouping by a custom fingerprinting rule」、SALTED_COMPONENT 的「Uses a complex grouping algorithm taking event data and a fingerprint into account」）在檔案裡沒有第二個出處，renderTitle 的 tooltip 只講 contributing 與否。grep 過整個檔案，variant.type 現在只出現在 :101 的 switch 判斷，畫面上不再顯示。這個面板是拿來 debug「為什麼這個 event 這樣分組」的，說明文字正是它的價值所在。附帶一個維護面的結果：CUSTOM_FINGERPRINT 與 BUILT_IN_FINGERPRINT 兩個 case 現在逐字相同，唯一區分它們的就是被刪掉的那段 tooltip。

**證據**：
- `static/app/components/events/groupingInfo/groupingVariant.tsx:101-121（五個 case 原本各自 push 一列帶 QuestionTooltip 的 Type，現在全部移除）`
- `static/app/components/events/groupingInfo/groupingVariant.tsx:108-113（CUSTOM_FINGERPRINT 與 BUILT_IN_FINGERPRINT 兩個 case 現在都只剩 addFingerprintInfo(data, variant); break;，內容完全相同）`
- `static/app/components/events/groupingInfo/groupingVariant.tsx:179-205（renderTitle 只顯示 variant.description 與「Contributing / Non-contributing variant」的 tooltip）`
- `commit 02695f9c ref(issues): Remove redundant Type row from Event Grouping Information table (#93892)`

**修復方向**：把兩個相同的 case 合併成 `case A: case B: addFingerprintInfo(data, variant); break;`，讓「它們現在沒有差別」這件事在程式碼裡是明說的而不是巧合。如果那五段解釋還有價值，把它們掛到 renderTitle 既有的 Tooltip 上（依 variant.type 查一張說明表），這樣既拿掉重複的 Type 列，也不會弄丟只有這裡才有的資訊。

</details>

<details>
<summary>未驗證提問（5）</summary>

#### Q-001 當 max_segment_id 為 NULL 時直接不刪任何 blob，會不會在 storage 裡留下孤兒錄影檔？

面向 F 資料取用與資料庫

**背景**：src/sentry/replays/usecases/delete.py:88-90 新增的防護在 max_segment_id 為 None 時回傳空的檔名清單，delete_matched_rows 接著照樣送出 archive event。判斷這是否安全，要知道 replay 資料列的 segment_id 何時會是 NULL。已查到的部分：fetch_rows_matching_pattern（delete.py:121-155）用 max(segment_id) 聚合，而且 where 條件把 timestamp 限制在 [start, end) 之間（:146-147），所以 max 只涵蓋視窗內的資料列。如果一個 replay 在視窗內只有 segment_id 為 NULL 的資料列（例如 error 關聯或 archive 列），視窗外卻有真正的 segment，那麼 max_segment_id 會是 NULL、blob 不會被刪、replay 卻被標記為已刪除。無法從 repo 靜態判定 replays 這張表在什麼情況會寫入 NULL segment_id。

**如何確認**：對 ClickHouse replays 表跑一次查詢，看有多少 replay_id 存在 segment_id 為 NULL 的資料列、以及這些 replay 是否同時存在 segment_id 非 NULL 的資料列；或由 replay ingest 的作者確認 NULL segment_id 只會出現在完全沒有錄影檔的 replay 上。

#### Q-002 traceWaterfall 的外層容器從 height: 100% 改成 flex={1} 之後，版面在實際頁面上還一樣嗎？

面向 H 非 Python 檔

**背景**：static/app/views/performance/newTraceDetails/traceWaterfall.tsx:712 現在是 <Flex direction="column" flex={1}>，取代的是原本 display:flex; flex-grow:1; flex-direction:column; height:100% 的 styled div。前三項有對上，height:100% 沒有。在 flex 容器內 flex:1 通常足以取代 height:100%，但這取決於祖先鏈本身是否撐滿高度；同一個變更裡 traceTabsAndVitals.tsx 也做了類似替換。這是純視覺行為，靜態閱讀無法確認。

**如何確認**：在 trace detail 頁面實際渲染一次（含 drawer left / drawer right / drawer bottom 三種 layout），對照改動前後的 waterfall 容器高度；或確認 Flex 元件在 flex 屬性存在時是否也會設定 height。

#### Q-003 project_replay_summarize_breadcrumbs 這個 GET 多加一次 Snuba 查詢與一次 nodestore 批次讀取之後，回應時間還能接受嗎？

面向 D API 慣例

**背景**：src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:67-94 在原本的 segment 讀取與 Seer 呼叫之外，新增了 query_replay_instance（Snuba）與 nodestore.backend.get_multi 兩次同步 I/O，且兩者都在 paginate 之前、無條件執行（除非 enable_error_context=false）。review-dimensions D-6 的目標是 150ms，但這個 endpoint 本來就要同步等 Seer 回應（程式碼裡的 XXX 註解自承 request 不是 streaming），基準線已經遠高於 150ms。無法從 repo 判斷新增的兩次 I/O 佔比多少。

**如何確認**：在 staging 對一個有數十筆 error 的 replay 量測 p50/p95，並拆出 query_replay_instance 與 get_multi 各自的 span 時間；若 get_multi 佔比顯著，F-009 提到的 error_ids 上限就從建議變成必要。

#### Q-004 把 0917 與 0920 兩支 migration 改成 no-op，對已經成功套用過它們的環境是否真的無害？

面向 E 架構

**背景**：src/sentry/migrations/0917_convert_org_saved_searches_to_views.py:14 與 0920_convert_org_saved_searches_to_views_revised.py:13 的註解都寫「This migration had an error and was never run」。已確認接手的 0921_convert_org_saved_searches_to_views_rerevised.py 確實存在於 src/sentry/migrations/，指向沒有斷；也確認 Django 不會重跑已記錄為 applied 的 migration，所以對這些環境改內容不會產生新的寫入。但「從來沒有被跑過」是一個關於所有部署（含 self-hosted）的全稱陳述，而 tests/sentry/migrations/test_0917_convert_org_saved_searches_to_views.py 在同一個變更裡被刪除，等於也移除了驗證舊行為的手段。

**如何確認**：確認 SaaS 與 self-hosted 的 django_migrations 表中 0917/0920 的狀態（未套用，或套用但因錯誤而未寫入資料），以及 0921 對兩種狀態是否都是冪等的。

#### Q-005 送審的 diff 範圍是否正確？

**背景**：任務描述說 49a275847631 是 merge base，但在 checkout 裡 `git merge-base 49a275847631 ea188e2d736f` 回傳 49a275847631 本身，而 `git log --oneline 49a275847631..ea188e2d736f` 有 32 個 commit。49a275847631 的訊息是 `feat(replays): Add self-serve bulk deletes (#93864)`，也就是 PR 標題所指的那個變更本身；它的內容完全不在 diff 裡。看起來 base 取成了該 PR 的 squash commit 而不是它的 parent。本報告以實際 diff 為準完成審查。

**如何確認**：由提供 diff 的一方確認要審的是哪一個範圍。若目標是 #93864，正確範圍是 `git diff 49a275847631^ 49a275847631`，需要重跑一次審查；本次針對 bulk delete 系統本身的觀察（F-003、Q-001）在那個範圍下仍然適用。

</details>
