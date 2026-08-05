## 審查結論：Request Changes

> Critical 1 · Suggestion 6 · Nit 1 · 未驗證提問 3
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ✅ | ✅ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

- **A 風格**（未通過）：新增的 docstring 與註解主張了與程式碼相矛盾的安全性前提（A-001），另有 11 行行尾空白會讓本 repo 自己的 pre-commit CI 失敗（A-002）。函式長度、型別註記、命名本身沒有問題。
- **B 簡潔**（未通過）：invalidate_upsampling_cache 是無人呼叫的死程式碼（B-001）；events_stats 內同一個轉換被複製成三份，外加一個沒有作用的別名變數（B-002）。
- **F 資料取用與資料庫**（未通過）：upsampled_count() 拿掉 ifNull 造成既有資料形狀的消費端行為改變（F-001，Critical）；新增的 60 秒共享快取讓一個組態決策在多個 process 之間變得不一致且無法失效（F-002）。
- **G 測試**（未通過）：diff 沒有新增或修改任何測試。既有測試在結構上也接不到這兩個行為變更，詳見 G-001。
- **H 非 Python 檔**（未通過）：diff 在 repo 根目錄新增了一個 sentry-repo 的 gitlink（mode 160000），而且沒有對應的 .gitmodules，看起來是誤加的巢狀 clone。詳見 H-001。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該不該做？**：commit message 宣稱要避免「expensive repeated option lookups」，但這個前提在本 repo 內找不到支撐：options.get 本身已經先走 process 內的 local cache，才會走 network cache，最後才回資料庫（src/sentry/options/store.py:98-150）。也就是說熱路徑上原本是一次 dict 查詢，改完後變成一次 django cache 的 get（正式環境是 memcached/redis 的網路往返）。commit message 與 diff 都沒有附上任何量測。另外三項變更裡有兩項（events_stats 的搬移、discover.py 拿掉 ifNull）根本不是效能變更。
- **該在這個 MR 做？**：同一個 commit 裡混了四件性質不同的事：(1) upsampled_count() 聚合語意的行為變更（拿掉 ifNull）、(2) 新增快取層、(3) events_stats 內一段不改變行為的搬移、(4) 一個明顯是誤加的 sentry-repo gitlink。其中 (1) 是資料正確性變更，不是效能最佳化，被包在標題為 performance optimizations 的 commit 裡很容易被略過，建議拆成獨立 MR 單獨審。
- **該在這個時機做？**：上一個 commit 4cb317c5（#94376）才剛把這個功能合進來，而 issues.client_error_sampling.project_allowlist 的預設值仍是空陣列（src/sentry/options/defaults.py:3464-3468），代表 rollout 還沒開始、也還沒有流量可以量測。在沒有流量的階段先做效能最佳化，同時把前一個 PR 刻意加上的 null 保護拿掉，時機上剛好相反：第一批進 allowlist 的專案，其查詢區間內必然包含加入 allowlist 之前、沒有 sample_weight 的既有事件。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且審查機上的 Semgrep 規則目錄不存在，本次未執行 SAST 掃描 |
| ruff | 已執行 | in_diff 0、outside_diff 160 |
| ty | 略過 | ty 未安裝（不在 PATH 上），本次未執行 Python 型別檢查 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）；本次 diff 也沒有 JavaScript/TypeScript 檔案 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號圖；Phase 3 的呼叫端列舉與完整性確認全部改用 grep 完成（見各 finding 的 evidence） |
| ncr-fresh-eyes | 已執行 | 流程偏差，如實揭露：本審查的執行環境沒有可派送 subagent 的工具（Task/Agent tool 不存在），fresh eyes 無法從審查者自身的 context 派出，改由外部的協調者代為派送，且抵達時間晚於 Phase 3 的其餘步驟——也就是說它並非在九大面向之前先讀，而是在報告初稿完成後才進來。SKILL.md 要求 fresh eyes 的 prompt 不帶類別清單、severity 詞彙、掃描摘要與既有發現，這一點由協調者確認成立；但「先於結構化分析」這個順序沒有滿足，因此它實際發揮的是複核而非破框的作用，讀者評估本報告的獨立性時應把這一點計入。其回報的每一項都已由審查者重新對照程式碼驗證（見 F-002 與 G-001 的說明），採納與否的依據寫在對應 finding 內。另註：協調者回報在閱讀期間出現了一段未經任務要求的 MCP github server 工具說明；該段文字不屬於本次受審材料（不在 diff、不在 repo 內、無 file:line 可指），對本審查的範圍、severity 與結論皆無影響，在此一併記錄。 · observations 7、adopted_as_new_finding 0、already_filed 6、rejected_or_not_filed 2 |
| ncr-quality-check | 略過 | 審查者的執行環境沒有可派送 subagent 的工具（Task/Agent tool 不存在），Phase 4 step 3 的報告品質複核完全沒有執行，也沒有由外部代跑。本報告在品質面只通過 report_model.py 的機械驗證（結論與 findings 一致、每則 finding 都有 fix、九個面向都有裁決、略過的掃描都有理由），沒有經過第二雙眼睛檢查語氣、重複、以及 severity 校準是否過重或過輕。 |

### Critical

#### F-001 upsampled_count() 拿掉 ifNull(sample_weight, 1)，會讓沒有 sample weight 的錯誤事件被少算 — `src/sentry/search/events/datasets/discover.py:1046-1050`

面向 F 資料取用與資料庫 · Critical

**問題**：變更把 sum(ifNull(sample_weight, 1)) 改成 sum(sample_weight)。這個 ifNull 不是多餘的防禦，是上一個 commit 4cb317c5（#94376）刻意寫進去的。反證方向找過三個地方，都無法支持「sample_weight 必然存在」這個前提：（一）grep 全 repo，sample_weight 只出現在 discover.py:1048 這一處聚合，沒有任何其他地方補做 null 處理，也就是說這個保護被拿掉之後沒有替代品；（二）寫入端 _derive_client_error_sampling_rate 只有在事件的 contexts.error_sampling.client_sample_rate 存在且落在 0 < r <= 1 時才會設定 job['data']['sample_rate']（event_manager.py:785-787），所以同一個 allowlist 專案裡，只要 SDK 沒送 error_sampling context、或送了超出範圍的值，該事件就沒有 sample weight；（三）本 repo 自己的平行邏輯已經替這件事表態了——_get_error_weighted_times_seen 在拿不到 sample_rate 時明確 return 1（event_manager.py:1560-1565），正是 ifNull(..., 1) 的同一個約定。也就是說改完之後，Group.times_seen 的加權路徑與 events-stats 圖表的加權路徑對同一批事件會給出不同答案。實際影響：專案剛加進 allowlist 時，查詢區間內加入之前的所有既有事件都沒有 sample weight，圖表上的錯誤數會直接少算；而且是靜默的，不會有例外、不會有告警，讀圖的人只會看到錯誤變少。註解 error_upsampling.py:85-86 主張「資料庫 schema 保證 sample_weight 對 allowlist 專案的所有事件都存在」，寫入端的程式碼並不支持這個主張。

**證據**：
- `src/sentry/search/events/datasets/discover.py:1046-1050`
- `src/sentry/event_manager.py:774-798`
- `src/sentry/event_manager.py:1560-1565`
- `src/sentry/api/helpers/error_upsampling.py:85-86`

**修復方向**：把 discover.py:1048 還原成 [Function("sum", [Function("ifNull", [Column("sample_weight"), 1])])]，並移除 error_upsampling.py:85-86 與 discover.py:1044-1045 那兩段宣稱不需要 null 檢查的註解。如果團隊真的想證明 ifNull 是多餘的，該提出的證據是 Snuba errors storage 的 schema 定義（sample_weight 是 non-nullable 且 default 為 1），以及 allowlist 專案加入之前既有事件的回填計畫——在拿到這兩樣之前，保留 ifNull 是唯一不會靜默出錯的選擇。

<details>
<summary>Suggestion（6）</summary>

#### F-002 60 秒 allowlist 快取：key 不包含 allowlist 本身、無法被失效，且它想省下的查詢本來就已經被快取了 — `src/sentry/api/helpers/error_upsampling.py:27-40`

面向 F 資料取用與資料庫 · Suggestion

**問題**：三個獨立的問題疊在同一段程式碼上。（一）效能前提不成立：options.get 並不是每次都打資料庫，OptionsStore.get 先呼叫 get_cache（store.py:123-150），而 get_cache 的第一步就是 get_local_cache（store.py:152-185）——process 內的 dict 查詢；只有在它未命中時才會走 network cache，最後才回 store。改動之後，熱路徑上原本的一次 dict 查詢被換成一次 django cache 的 get，而正式環境的 default cache 是 memcached/redis，也就是一次網路往返。這個「最佳化」在延遲上很可能是負向的。（二）快取無法被失效：cache key 是 organization.id 加上 hash(tuple(sorted(project_ids)))，也就是每一個出現過的 project 組合各一把 key。新增的 invalidate_upsampling_cache 需要呼叫端事先知道確切的 project_ids 組合才能刪對 key，但 allowlist 變動時沒有人握有「歷史上被查詢過的所有 project 組合」這份清單，所以這個函式在設計上就無法真的做到它 docstring 宣稱的「cache consistency across the system」。（三）快取語意本身有洞：key 沒有包含 allowlist option 的值，所以判斷結果與決定該結果的輸入之間沒有綁定關係，只能靠 60 秒 TTL 自然過期。附帶一個 key 組成方式的問題：用 Python 內建 hash() 當 cache key 的組成部分，在本 repo 是獨一無二的寫法。grep src/sentry 下所有 cache_key 的組法，慣例只有兩種——直接把可讀的欄位串起來（例如 tasks/post_process.py:79 的 f"servicehooks:1:{project_id}"、users/models/userip.py:54 的 f"userip.log:{user.id}:{ip_address}"），或在需要摘要時明確用 md5_text(...).hexdigest()（options/manager.py:159、ratelimits/redis.py:55、sentry_metrics/indexer/cache.py:65）——除了本檔的第 27 與 73 行之外，沒有其他地方拿 hash() 進 cache key。這使得 key 無法從 Redis/memcached 端反查對應哪一組專案，排查時看不出這把 key 屬於誰。這裡要明確排除一個相鄰但不成立的說法：hash() 的碰撞不構成實際風險。CPython 的 int 雜湊等於其值本身、tuple 雜湊不受 PYTHONHASHSEED 影響（已用不同 PYTHONHASHSEED 的子行程實測，int tuple 輸出一致、str tuple 才會變動），輸出為 64-bit，依生日界要在同一個 organization 內累積約 2^32 種相異的 project 組合才會有五成碰撞機率，不會發生；因此本報告不把碰撞列為 finding，只把可讀性與可排查性列在這裡。實際影響是：issues.client_error_sampling.project_allowlist 帶有 FLAG_AUTOMATOR_MODIFIABLE，是這個功能 rollout 的開關；把專案加進或移出 allowlist 之後，最長 60 秒內、且每個 process 各自不同步地，圖表會繼續給出舊的計數口徑。這裡不列為 Critical，因為影響有時限（60 秒）且會自行收斂，不會造成永久性錯誤資料或崩潰；但它是一個沒有量測支撐、卻用正確性換來的變更。

**證據**：
- `src/sentry/api/helpers/error_upsampling.py:27-40`
- `src/sentry/api/helpers/error_upsampling.py:67-74`
- `src/sentry/options/store.py:98-150`
- `src/sentry/options/defaults.py:3464-3468`
- `src/sentry/conf/server.py:2106`

**修復方向**：最直接的做法是整段移除快取，讓 is_errors_query_for_error_upsampled_projects 回到原本的兩行（_are_all_projects_error_upsampled 之後接 _should_apply_sample_weight_transform），並一併移除 invalidate_upsampling_cache。如果後續真的量到 options.get 是瓶頸，比較站得住腳的做法是把 allowlist 轉成 set 之後放進 request 層級（而非跨 request 共享）的快取，例如掛在 request 物件上，這樣同一個 request 內多次呼叫只算一次、又不會產生跨 process 的不一致；或是在 key 裡納入 allowlist 的內容雜湊，讓組態一變 key 就跟著變，就不需要外部失效機制。若最後仍決定保留跨 request 的快取，key 請改用本 repo 既有的兩種慣例之一：直接串接可讀欄位（例如 f"error_upsampling_eligible:{organization.id}:{','.join(map(str, sorted(project_ids)))}"），或在長度成為問題時用 md5_text(...).hexdigest()，不要用內建 hash()——不是因為會碰撞，而是因為那把 key 事後無法反查對應哪一組專案。

#### B-001 invalidate_upsampling_cache 是死程式碼，全 repo 沒有任何呼叫端 — `src/sentry/api/helpers/error_upsampling.py:67-74`

面向 B 簡潔 · Suggestion

**問題**：grep 全 repo（--include=*.py）搜尋 invalidate_upsampling_cache，只命中定義本身這一處，src/ 與 tests/ 都沒有呼叫端，也沒有被任何 signal、receiver 或 options 的變更 hook 掛上。它的 docstring 寫「This should be called when the allowlist configuration changes」，但沒有人這樣做，所以 F-002 描述的快取不一致實際上是一直存在的，不是「等接上 hook 就好」。另一個層面是：即使有人想接上去，也接不了——見 F-002（呼叫端需要確切的 project_ids 組合才能刪對 key）。這符合 B.4 的情境：功能被生成、被取代、然後留在原地。

**證據**：
- `src/sentry/api/helpers/error_upsampling.py:67-74`

**修復方向**：隨 F-002 一起把這個函式刪掉。如果團隊決定保留快取，那就必須先解決 key 設計問題（把 allowlist 內容納入 key，或改成 request 層級快取），此時這個函式也不再需要。無論走哪條路，都不要留一個沒有呼叫端、且在現行 key 設計下無法正確使用的公開函式。

#### B-002 _get_event_stats 內的欄位轉換被複製成三份，行為完全沒變，只是多了三個要一起維護的地方 — `src/sentry/api/endpoints/organization_events_stats.py:226`

面向 B 簡潔 · Suggestion

**問題**：原本 transform_query_columns_for_error_upsampling 在分支之前呼叫一次，涵蓋全部路徑；改完之後同一行被複製到三個分支裡。逐一追過四個 return 路徑（236 的 rpc top events、253 的非 rpc top events、279 的 rpc timeseries、298 的標準 timeseries），確認它們都仍然會拿到轉換後的欄位，所以這次沒有漏掉任何路徑——這一點特別去反證過，不成立。問題在於這是純粹的成本：三份等價程式碼取代一份，而且下一個在這個函式裡加分支的人只要忘記補第四份，就會出現靜默的計數錯誤，而型別檢查與測試都攔不到。同時 upsampling_enabled = should_upsample（第 226 行）是一個沒有任何作用的別名。註解也對不上程式碼：第 219 行說「This cached result ensures consistent behavior」，但這裡沒有任何快取；第 225 行說「allows for better query optimization and caching」，搬移欄位轉換的位置不會產生任何查詢最佳化；第 231 行說「ensures we use the most current schema assumptions」，但 query_columns 與 upsampling_enabled 在這兩點之間都沒有改變，所以「最新」與「較早」是同一份值。

**證據**：
- `src/sentry/api/endpoints/organization_events_stats.py:226`
- `src/sentry/api/endpoints/organization_events_stats.py:230-233`
- `src/sentry/api/endpoints/organization_events_stats.py:275-278`
- `src/sentry/api/endpoints/organization_events_stats.py:295-297`

**修復方向**：還原成分支前呼叫一次：刪掉第 226 行的別名，保留 should_upsample，並把 `if should_upsample: final_columns = transform_query_columns_for_error_upsampling(query_columns)` 放回 `if top_events > 0:` 之前，同時刪掉三處複製與那幾段描述不存在行為的註解。

#### A-001 新增的 docstring 與註解宣稱了幾項與程式碼相矛盾的前提，會誤導下一個維護者 — `src/sentry/api/helpers/error_upsampling.py:49-53`

面向 A 風格 · Suggestion

**問題**：四處都是同一類問題：文件敘述的世界與程式碼的世界不一致，而且矛盾的方向都是把風險說成沒有風險。（一）_are_all_projects_error_upsampled 新增的 NOTE 說「This function reads the allowlist configuration fresh each time... This is intentional to ensure we always have the latest configuration state」，但它唯一的呼叫端（同檔案第 30-38 行）剛好在同一個 diff 裡加上了 60 秒快取，讓「always have the latest」不再成立。下一個維護者若先讀到這段 NOTE，會以為組態變更是即時生效的。（二）第 23-25 行的「This is safe because allowlist changes are infrequent」把一個沒有量測、也沒有失效機制的取捨寫成既定結論。（三）第 85-86 行的「We rely on the database schema to ensure sample_weight exists for all events in allowlisted projects, so no additional null checks are needed here」與寫入端矛盾，證據見 F-001。（四）discover.py:1044-1045 重複同一個主張。依 A.7 的精神，簽章或行為改變後留下會說謊的文件，比沒有文件更糟——差別在於這幾處不是簽章而是安全性前提，所以列 Suggestion 而非 Critical。

**證據**：
- `src/sentry/api/helpers/error_upsampling.py:49-53`
- `src/sentry/api/helpers/error_upsampling.py:23-25`
- `src/sentry/api/helpers/error_upsampling.py:85-86`
- `src/sentry/search/events/datasets/discover.py:1044-1045`

**修復方向**：隨對應的程式碼修正一起處理：F-001 還原 ifNull 之後刪掉 (三)(四)；F-002 移除快取之後，(一) 的 NOTE 就重新成立、(二) 可整段刪除。若團隊選擇保留快取，那 (一) 必須改寫成明確描述「呼叫端有 60 秒快取，本函式本身不快取」，而不是留一句會被讀成「整條路徑都是即時的」的敘述。

#### G-001 兩項行為變更都沒有測試，而既有測試在結構上也接不到它們 — `tests/sentry/api/helpers/test_error_upsampling.py:7-12`

面向 G 測試 · Suggestion

**問題**：diff 沒有動到任何測試檔。逐一確認既有測試為什麼接不到：（一）test_error_upsampling.py 的 import 清單只匯入了三個私有 helper 與 transform 函式，完全沒有測 is_errors_query_for_error_upsampled_projects 這個公開入口，所以新增的快取分支（第 29-38 行）測試覆蓋率是零。（二）events-stats 的四個 upsampling endpoint 測試每個只發一次 request，而 BaseTestCase._pre_setup 每個測試都會 cache.clear()（testutils/cases.py:391），所以快取造成的過期讀取在 CI 裡不可能出現——測試會全綠，問題只在正式環境浮現。（三）F-001 的少算同樣測不到：setUp 存入的兩筆事件都帶了 contexts.error_sampling.client_sample_rate = 0.1，沒有任何一筆是「allowlist 專案但沒有 sample weight」的事件，而那正是 ifNull 唯一會發揮作用的情況。附帶一提，settings.CACHES 的預設值是 DummyCache（conf/server.py:2106），測試環境則被覆寫成 LocMemCache（testutils/pytest/sentry.py:191-194），所以快取行為在不同環境下差異很大，更需要顯式測試而不是靠既有測試順帶覆蓋。這裡要更正一個容易寫錯、而且會讓本 finding 變成錯誤指控的說法：events-stats 的 endpoint 測試並非「完全沒有碰 upsampling」。tests/sentry/api/endpoints/test_organization_events_stats.py 這個路徑在本 repo 不存在（find tests -name 'test_organization_events_stats*' 只找到 tests/snuba/api/endpoints/ 底下的四個檔案），真正的測試在 tests/snuba/api/endpoints/test_organization_events_stats.py，而且它確實有四個 upsampling 測試（第 3604、3629、3654、3699 行）。本 finding 主張的不是「沒有人測過 upsampling」，而是「這四個測試在結構上接不到本次新增的兩個行為變更」，理由如上述 (二)(三)。

**證據**：
- `tests/sentry/api/helpers/test_error_upsampling.py:7-12`
- `tests/snuba/api/endpoints/test_organization_events_stats.py:3604-3627`
- `src/sentry/testutils/cases.py:387-393`
- `src/sentry/conf/server.py:2106`

**修復方向**：至少補三個測試：(1) 在 events-stats 測試裡存一筆不帶 error_sampling context 的錯誤事件，斷言它在 upsampled 查詢中仍然計為 1 而不是 0——這個測試在目前的 diff 下會失敗，正是 F-001 的迴歸測試；(2) 直接測 is_errors_query_for_error_upsampled_projects，在同一個測試內改變 mock 的 allowlist 後再呼叫一次，斷言回傳值有跟著改變——這個測試會揭露 F-002 的過期讀取；(3) 若保留快取，補一個 invalidate_upsampling_cache 的測試，這個測試寫下去就會直接暴露 B-001 描述的 key 設計問題。

#### H-001 diff 在 repo 根目錄新增了一個沒有 .gitmodules 的 sentry-repo gitlink，看起來是誤加的巢狀 clone — `sentry-repo:1`

面向 H 非 Python 檔 · Suggestion

**問題**：diff 的第一個 hunk 以 mode 160000 新增了 sentry-repo 這個項目，指向 commit a5d290951def84afdcc4c88d2f1f20023fc36e2a。git ls-files -s sentry-repo 確認它確實以 gitlink 形式進了 index，而 repo 根目錄沒有 .gitmodules 檔案（cat .gitmodules 回報不存在）。一個沒有在 .gitmodules 登記的 gitlink 是半成品狀態：git submodule 相關指令看不到它，clone 之後只會得到一個空目錄，而遍歷整個工作樹的工具（打包、靜態檔收集、lint 的檔案探索）會遇到一個內容與 index 不符的路徑。這與 PR 標題描述的 upsampling 效能最佳化沒有任何關係，最合理的解釋是本機有一份巢狀 clone 被誤 add 進來。

**證據**：
- `sentry-repo:1`

**修復方向**：從 index 移除即可：`git rm --cached sentry-repo`，然後把該路徑加進 .gitignore（或直接移出工作目錄）以免再次被 add。如果確實有意引入 submodule，那需要另外一個 MR，並且附上 .gitmodules 的登記項與引入理由。

</details>

<details>
<summary>Nit（1）</summary>

#### A-002 11 行行尾空白，會讓本 repo 自己的 pre-commit CI 失敗 — `src/sentry/api/helpers/error_upsampling.py:22`

面向 A 風格 · Nit

**問題**：grep ' \+$' 在 error_upsampling.py 命中 8 行、organization_events_stats.py 命中 3 行（第 22、28、33、36、39、49、51、84 與 223、234、278 行）。這不只是風格：.pre-commit-config.yaml 在 pre-commit 階段會跑 black 與 flake8，而 .github/workflows/pre-commit.yml 的觸發條件包含 pull_request，也就是每個 PR 都會跑。black 會把這些空白格式化掉（因此 --check 會失敗），flake8 的 W291/W293 也沒有被 setup.cfg:95 的 extend-ignore 排除（該行只排除 E203,E501,E402,E731 與一批 B 開頭規則）。另外第 51 行的行尾空白落在 docstring 的句子中間（'...between calls if the '），black 不會碰 docstring 內容，所以那一行需要手動修。順帶說明：本次用 ruff 掃描全專案的結果是 diff 行內 0 件、專案既有問題 160 件，ruff 沒有攔到這幾行是因為它跑的是本 skill 的預設規則集，不是 sentry 的 flake8 設定。

**證據**：
- `src/sentry/api/helpers/error_upsampling.py:22`
- `src/sentry/api/helpers/error_upsampling.py:51`
- `src/sentry/api/endpoints/organization_events_stats.py:223`
- `src/sentry/api/endpoints/organization_events_stats.py:234`

**修復方向**：在本機跑一次 `pre-commit run --files src/sentry/api/helpers/error_upsampling.py src/sentry/api/endpoints/organization_events_stats.py src/sentry/search/events/datasets/discover.py`，black 會處理程式碼區塊的部分；docstring 第 51 行的行尾空白手動刪掉。若 F-002 與 B-002 依建議還原，這些行大部分會直接消失。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 Snuba errors storage 裡的 sample_weight 欄位到底是 Nullable 還是 non-nullable、預設值是什麼？這決定了 F-001 的失效樣態是「整個時間桶變成 NULL」還是「該事件被算成 0」。

面向 F 資料取用與資料庫

**背景**：sample_weight 的 schema 定義在 getsentry/snuba，不在本 repo 內；grep 全 repo 只找得到 discover.py:1048 的聚合與相關註解，沒有任何 schema 或 migration 定義。ClickHouse 的 sum() 會跳過 NULL，若欄位為 Nullable 且該列為 NULL，整體行為是「該列不貢獻」；若欄位為 non-nullable 且預設 0，該列貢獻 0。兩種結果都是相對於原本 ifNull(..., 1) 的迴歸，所以這個未定項不影響 F-001 的成立與嚴重度，只影響它在圖表上長什麼樣子。

**如何確認**：getsentry/snuba 中 errors storage 的欄位定義（sample_weight 的型別與 default），或在 staging 對一個 allowlist 專案同時跑 sum(sample_weight) 與 sum(ifNull(sample_weight, 1)) 比對兩者差異。

#### Q-002 促成這次快取的量測是什麼？是否有 profile 或 metrics 顯示 options.get('issues.client_error_sampling.project_allowlist') 在 events-stats 路徑上是熱點？

面向 F 資料取用與資料庫

**背景**：commit message 與 diff 都以「expensive repeated option lookups during high-traffic periods」作為理由，但 src/sentry/options/store.py:98-150 顯示 options.get 已經先走 process 內 local cache。同時 issues.client_error_sampling.project_allowlist 的預設值是空陣列（options/defaults.py:3464-3468），代表 rollout 尚未開始、目前應該沒有可以觀察到的高流量。從 repo 內無法判斷是否存在 MR 之外的量測資料。

**如何確認**：一份指出該 options.get 為熱點的 profile 或 dashboard 連結；若沒有，F-002 的建議（移除快取）就沒有需要權衡的另一面。

#### Q-003 在 getsentry 正式環境，settings.CACHES['default'] 實際掛的是哪個 backend？這決定了新增的快取在正式環境到底有沒有生效。

面向 F 資料取用與資料庫

**背景**：src/sentry/conf/server.py:2106 的預設是 DummyCache——在這個 backend 下 cache.set 是 no-op、cache.get 永遠回 None，這段快取等於完全沒有作用（行為正確但零效益）。測試環境被覆寫為 LocMemCache（src/sentry/testutils/pytest/sentry.py:191-194）。正式環境的覆寫發生在本 repo 之外的部署設定裡，從這個 checkout 看不到。這個未定項會影響 F-002 的實際影響範圍（若正式環境也是 DummyCache，過期讀取根本不會發生，但整段程式碼也就純粹是無用的複雜度）。

**如何確認**：正式環境部署設定中的 CACHES 定義，或在正式環境對這個 cache key 做一次 get/set 的驗證。

</details>
