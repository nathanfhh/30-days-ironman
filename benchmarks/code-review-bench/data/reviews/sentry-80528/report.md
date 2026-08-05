## 審查結論：Approved with Comments

> Critical 0 · Suggestion 1 · Nit 4 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ✅ | ✅ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | — | ✅ |

- **A 風格**（未通過）：兩個新模組的公開函式都沒有 docstring，且 try_incident_threshold 這個名字沒有透露它會寫入資料庫與送 Kafka（F-005）；搬過來的 get_failure_reason docstring 有錯字且範例與實際輸出對不上（F-004）。其餘命名與型別註記可讀，import 排序符合 isort 慣例。
- **B 簡潔**（未通過）：兩個新模組各自建了一個從未使用的 logger（F-001）；get_monitor_environment_context 算出的 config 副本被丟棄（F-002）。搬移本身沒有製造重複邏輯——舊檔案的對應區塊全部刪除，grep 確認沒有留下任何一份殘影。另覆核一項拆分帶來的改善：incidents.py:62 的 `incident, _ = MonitorIncident.objects.get_or_create(...)` 在舊檔裡與 module 層級的 `gettext_lazy as _`（mark_failed.py:12）同處一個檔案，會在該函式內把 `_` 覆寫成區域變數；拆分後 `_` 只留在 incident_occurrence.py:11，這個潛在的遮蔽風險隨之消失。不是本次的缺陷，記錄為正向確認。
- **D API 慣例**（不適用）：本次 diff 不含任何 HTTP endpoint、URL route、serializer 或請求驗證 schema，沒有 API 慣例可檢查。
- **G 測試**（未通過）：邏輯搬到兩個新模組，測試整批留在 tests/sentry/monitors/logic/test_mark_failed.py（F-003）。既有測試本身不會壞：它們 patch 的是 sentry.issues.producer.produce_occurrence_to_kafka（來源模組，test_mark_failed.py:26 等六處），而 incident_occurrence.py:34-35 保留了函式內 import，因此 patch 目標仍然命中。本次未實際執行測試（見 Q-002）。
- **H 非 Python 檔**（不適用）：diff 只包含四個 .py 檔（incident_occurrence.py、incidents.py、mark_failed.py、types.py），沒有前端元件、Dockerfile、nginx.conf、docker-compose 或 migration。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | in_diff 0、outside_diff 206 |
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），本次未執行 SAST 掃描 |
| ty | 略過 | ty 未安裝（不在 PATH 上），本次未執行 Python 型別檢查；型別面的判斷改以人工閱讀 + grep 完成 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）；本次 diff 也只有 .py 檔，沒有 JavaScript/TypeScript 需要檢查 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號圖；呼叫關係與完整性（誰呼叫 mark_failed、舊符號是否還有殘留引用）全部改以 grep 逐一確認 |
| ncr-fresh-eyes | 已執行 | 本 session 無法派送 subagent，改由 orchestrator 外部派送，且在報告初稿之後才回流（順序偏離，已於 meta.target 揭露）。四點觀察逐條驗證結果：(1) 純搬移／改名、函式本文逐字一致 — 與本次獨立比對結果相符，已反映於 intent_check.right_mr；(2) 舊符號名全 repo 無殘留引用、測試只 import mark_failed — 與本次 grep 結果相符，已反映於面向 I；(3) get_failure_reason docstring 錯字 — 與 F-004 同一項（fresh eyes 標 136-143 是 diff 行號，實際檔案行號為 132/136），不新增；(4) incidents.py:62 的 `incident, _ =` 不再與 gettext_lazy as _ 同檔 — 覆核屬實（舊檔 mark_failed.py 第 12 行 import _、第 151 行覆寫 _，拆分後 _ 只留在 incident_occurrence.py:11），這是拆分帶來的改善而非缺陷，不成案。淨結果：發現清單不變 · observations 4、already_covered 3、new_findings 0 |
| ncr-quality-check | 略過 | 本 session 無法派送 subagent，報告品質複查未執行；report.json 僅通過 report_model.py 的結構驗證，四條發布前規則（結論機械推導、可自足引用、對事不對人、每則發現都有修復方向）由主 agent 自行複讀，並非等價替代 |

<details>
<summary>Suggestion（1）</summary>

#### F-002 get_monitor_environment_context 算好的 config 副本被丟棄，送出去的仍是原始 config — `src/sentry/monitors/logic/incident_occurrence.py:159`

面向 B 簡潔 · Suggestion

**問題**：這個函式先 `config = monitor.config.copy()`，再把副本裡的 schedule_type 換成 get_schedule_type_display() 的可讀名稱（models.py:343-344 回傳 ScheduleType 的名字字串），然後 return 的 dict 裡 "config" 填的卻是 `monitor_environment.monitor.config`——原件，不是副本。結果是那三行計算完全沒有作用，實際送進 issue occurrence 的 contexts.monitor.config.schedule_type 仍然是資料庫裡的整數（models.py:55 定義它是 integer）。這不是推測：tests/sentry/monitors/logic/test_mark_failed.py:119 就斷言了 `"schedule_type": 2`，把目前這個行為釘住了。ruff / F841 抓不到，因為 config 這個區域變數確實有被讀寫，只是結果被丟掉。這是搬移前就存在的問題（本 diff 中該函式逐字未改），不是本次引入；但這次把它搬進一個新檔案，等於由這個 MR 接手，而現在的樣子會讓下一個讀者以為 schedule_type 已經被轉成可讀字串。

**證據**：
- `src/sentry/monitors/logic/incident_occurrence.py:159`
- `src/sentry/monitors/logic/incident_occurrence.py:160`
- `src/sentry/monitors/logic/incident_occurrence.py:162`
- `src/sentry/monitors/logic/incident_occurrence.py:168`
- `src/sentry/monitors/models.py:343`
- `tests/sentry/monitors/logic/test_mark_failed.py:119`

**修復方向**：兩條路，挑一條把矛盾消掉：(1) 認定原意是要送可讀字串，就把 return 裡改成 "config": config，並同步把 test_mark_failed.py:119 的斷言從 2 改成 "interval"——但要先確認沒有下游（前端 monitor context 顯示、alert rule）依賴整數值；(2) 認定就是要送原始 config，那就把 160-162 那三行整組刪掉，讓程式碼不再宣告一個它不打算兌現的意圖。若不想在這個純搬移的 MR 裡動行為，開一張 follow-up 並在函式上留一行註解說明現況，也算收斂。

</details>

<details>
<summary>Nit（4）</summary>

#### F-001 兩個新模組各建了一個從未使用的 logger — `src/sentry/monitors/logic/incidents.py:3`

面向 B 簡潔 · Nit

**問題**：兩個新檔案都寫了 `import logging` 與 `logger = logging.getLogger(__name__)`，但 grep 這兩個檔案的全文，logger 除了被賦值那一行之外沒有任何使用處。這是從 mark_failed.py 一起帶過來的樣板——mark_failed.py:11 的 logger 在搬移前就已經是未使用狀態，搬移後也還在。ruff 不會報：logger 是模組層級變數，不是未使用的 import（logging 確實被 getLogger 用到了），所以只能靠人看。留著的成本是下一個維護者會以為這個模組有 log 而去找 log。

**證據**：
- `src/sentry/monitors/logic/incidents.py:3`
- `src/sentry/monitors/logic/incidents.py:11`
- `src/sentry/monitors/logic/incident_occurrence.py:3`
- `src/sentry/monitors/logic/incident_occurrence.py:25`

**修復方向**：兩個新檔案刪掉 `import logging` 與 `logger = ...` 兩行即可；若這次拆分之後打算在 incidents.py 補上 incident 建立 / 解除的 log（那其實是有價值的），就把 logger 留著並在同一個 MR 加上第一筆 logger.info，不要留一個空殼。順手也可以把 mark_failed.py:3,11 那組既有的一起清掉。

#### F-003 程式碼拆成三個模組，測試整批留在 test_mark_failed.py — `tests/sentry/monitors/logic/test_mark_failed.py:11`

面向 G 測試 · Nit

**問題**：tests/sentry/monitors/logic/ 底下現在只有 test_mark_failed.py 與 test_mark_ok.py，沒有 test_incidents.py / test_incident_occurrence.py。但 test_mark_failed.py 裡絕大多數的斷言（threshold 判定、MonitorIncident 建立、occurrence payload 內容，例如 114-130 行那組 contexts 斷言）驗的其實已經是 incidents.py 與 incident_occurrence.py 的行為。測試不會壞——它只 import mark_failed，patch 的又是 sentry.issues.producer 這個來源模組——所以這不是 blocker；但檔名與被測對象從這個 MR 起就對不上了，下次有人要改 occurrence payload 會先找不到測試在哪。

**證據**：
- `tests/sentry/monitors/logic/test_mark_failed.py:11`
- `src/sentry/monitors/logic/incidents.py:14`
- `src/sentry/monitors/logic/incident_occurrence.py:28`

**修復方向**：若認為 mark_failed 仍是這條路徑唯一的對外入口、測試刻意維持 end-to-end，就在 test_mark_failed.py 頂部加一行註解說明「本檔同時覆蓋 logic/incidents.py 與 logic/incident_occurrence.py」，讓對應關係留在檔案裡；若打算讓測試跟著模組走，就把 occurrence payload 相關的 case 移到 tests/sentry/monitors/logic/test_incident_occurrence.py，threshold 相關的移到 test_incidents.py，跟這次的拆分同一個節奏做完。

#### F-004 get_failure_reason 的 docstring 有錯字，且第三個範例對不上實際輸出 — `src/sentry/monitors/logic/incident_occurrence.py:132`

面向 A 風格 · Nit

**問題**：第 132 行寫的是「Builds a humam readible string」，humam / readible 兩個字都拼錯。第 136 行的範例輸出 "A failed check-in was detected" 在程式裡不存在：單筆的字串來自 SINGULAR_HUMAN_FAILURE_MAP（123-127 行），只有 error / missed / timeout 三種，沒有 failed 這一種。這是搬過來的既有文字，本次一字未改；但既然整段被搬進一個以它為主題的新檔案，這裡是最省成本的修正時機，而一個舉錯例子的 docstring 會讓讀者以為有第四條分支要找。

**證據**：
- `src/sentry/monitors/logic/incident_occurrence.py:132`
- `src/sentry/monitors/logic/incident_occurrence.py:136`
- `src/sentry/monitors/logic/incident_occurrence.py:123`

**修復方向**：132 行改成 "Builds a human readable string from a list of failed check-ins."；136 行的範例換成實際會輸出的字串，例如 "A missed check-in was detected"。

#### F-005 try_incident_threshold / create_incident_occurrence 沒有 docstring，函式名也沒有透露副作用 — `src/sentry/monitors/logic/incidents.py:14`

面向 A 風格 · Nit

**問題**：try_incident_threshold 這個名字讀起來像是「試著判斷有沒有達到 threshold」，實際上它會把 MonitorEnvironment.status 寫成 ERROR 並 save（incidents.py:55-56）、get_or_create 一筆 MonitorIncident、對每一筆 check-in 送出 issue occurrence 到 Kafka（95 行），最後再 send 一個 signal。這正是 mark_failed.py:19 那段 docstring 所謂的「trigger side effects」，但拆檔之後那段說明留在 mark_failed.py，新模組這一側沒有任何文字承接。既然這次特地把它拆成獨立模組作為對外入口，讓函式自己講清楚寫了什麼，成本很低。

**證據**：
- `src/sentry/monitors/logic/incidents.py:14`
- `src/sentry/monitors/logic/incidents.py:55`
- `src/sentry/monitors/logic/incidents.py:56`
- `src/sentry/monitors/logic/incidents.py:95`
- `src/sentry/monitors/logic/mark_failed.py:19`

**修復方向**：在 try_incident_threshold 與 create_incident_occurrence 各補一段 docstring，明確寫出副作用與回傳值意義，例如 try_incident_threshold 寫「Given a failing check-in that has passed the environment update, decide whether it reaches the incident threshold; on the way it flips MonitorEnvironment.status to ERROR, opens or reuses a MonitorIncident, emits an issue occurrence per failing check-in, and fires monitor_environment_failed. Returns True when an incident state was reached.」。若願意連名字一起調整，mark_incident_for_threshold 之類會比 try_ 更誠實地表達它會寫入。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 sentry 這個 repo 之外，是否還有程式碼從舊路徑 import mark_failed_threshold / create_issue_platform_occurrence / SimpleCheckIn？

面向 I 回溯分析

**背景**：在這份 checkout 內 grep 過全 repo：mark_failed_threshold 已經 0 筆命中，SimpleCheckIn 只剩 types.py 與兩個新模組，create_issue_platform_occurrence 的命中全部屬於不相干的 sentry.uptime.issue_platform。就這個 repo 而言搬移是完整的。但這三個符號都是被直接從模組層級 import 的一般函式／型別，沒有任何 __all__ 或 deprecation shim 擋著；本次只能看到這一個 repo，無法確認 getsentry 或其他私有相依是否引用了舊路徑。這個問題不是「有沒有壞」的猜測，而是搜尋範圍本身有邊界。

**如何確認**：在 sentry 之外的相依 repo（getsentry 等）對這三個名稱各跑一次 grep；或確認 CI 的跨 repo 檢查有涵蓋這次的模組搬移。任一結果為 0 命中即可結案。

#### Q-002 tests/sentry/monitors/logic/test_mark_failed.py 這一批測試在這次搬移後實際跑起來是全綠的嗎？

面向 G 測試

**背景**：本次審查環境沒有 sentry 測試所需的 postgres / kafka / redis 等服務，測試沒有實際執行。可以靜態確認的部分已經確認完：測試只 import mark_failed（signature 未變）；六處 @patch 指向 sentry.issues.producer.produce_occurrence_to_kafka 這個來源模組，而 incident_occurrence.py:34-35 保留了函式內 import，所以 patch 仍會命中；搬移前後的函式本文逐字比對一致。剩下的是「靜態看起來沒問題」與「真的跑過」之間的差距。

**如何確認**：CI 上 tests/sentry/monitors/ 的執行結果；或在具備服務相依的環境跑 `pytest tests/sentry/monitors/logic/ tests/sentry/monitors/clock_tasks/`。

</details>
