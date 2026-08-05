## 審查結論：Request Changes

> Critical 1 · Suggestion 7 · Nit 6 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

- **A 風格**（未通過）：class docstring 新增的敘述與實際預設行為相反，且新增參數未進 :param 區塊（F-008）；新增的 CLI 旗標對 0 與負值沒有防護（F-012）。
- **B 簡潔**（未通過）：重構過程留下未被呼叫的 _create_process_for_shard（F-004）、恆為 False 的防禦分支（F-010）、以及會讓 terminate() 被跳過的提前 break（F-009）。
- **D API 慣例**（不適用）：diff 內沒有 HTTP endpoint、URL、request/response schema 或驗證層改動。
- **E 架構**（未通過）：spans.buffer.max-flush-segments 從全域上限變成每 process 上限（F-002）；類別預設從單一 process 變成每 shard 一個 process（F-003）；metrics tag 的 cardinality 與語意（F-007）；SpanFlusher 只取用傳入 buffer 的 assigned_shards 卻要求傳整個 buffer（F-011）。
- **F 資料取用與資料庫**（未通過）：F-001：hang 偵測後舊 process 不會被 kill，新舊兩個 process 會同時對同一組 shard 做非破壞性的 flush_segments，導致 buffered-segments 重複產出。另有 F-014：submit() 的記憶體檢查對每個 buffer 各查一次整個 Redis cluster 的 INFO。
- **G 測試**（未通過）：新增測試只驗證帳面數字、沒有驗證多 process 下資料不漏（F-006）；既有測試新增的等待實際上不會等待（F-005）；改動最多的 _ensure_processes_alive 沒有任何測試覆蓋。
- **H 非 Python 檔**（未通過）：diff 中唯一的非 Python 檔是 CLAUDE.md，新增段落與本次變更無關且結尾多了一個空行（F-013）。Vue / Dockerfile / nginx.conf / docker-compose / Alembic migration 皆不在本次 diff 中。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：diff 中的 CLAUDE.md:449-457 新增了一段 hasattr / isinstance 的通用 Python 慣例示例，與 span flusher 多 process 化沒有關係。這個判斷屬於維護者：若刻意順手加入，建議在 PR 描述說明；否則拆成獨立 PR，之後追這條慣例的來源才有脈絡。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），略過相依套件漏洞、設定錯誤與 secret 掃描。 |
| opengrep | 略過 | opengrep 未安裝，且預設的 Semgrep rules 目錄不存在，略過 SAST 掃描。 |
| ruff | 已執行 | in_diff 0、outside_diff 159 |
| ty | 略過 | ty 未安裝（不在 PATH 上），略過 Python 型別檢查。本次的型別相關判斷（例如 SpawnProcess 與 multiprocessing.Process 的繼承關係）改以直接執行 Python 3.13 確認。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。本次 diff 也沒有 JavaScript / TypeScript 檔案。 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號索引；呼叫端列舉與「是否還有未遷移的引用」全部改以 grep 逐一確認。 |
| ncr-fresh-eyes | 略過 | 本審查流程內無法派出 subagent，fresh-eyes 改由外部協調端派出，並在本報告第一版（Phase 4 之後）才回傳，因此執行順序偏離 skill 規定的 Phase 3 step 1——這一版的 A–I 檢查表是在沒有 fresh-eyes 輸入的情況下先完成的。回傳的 8 項觀察已逐一對照原始碼複驗（其 file:line 有數處是 diff 位移而非檔案行號，已重新定位）：7 項與既有 findings 重疊，1 項為新增（F-014）。ncr-quality-check 仍未執行。 |
| ncr-quality-check | 略過 | 同上，無法派出 subagent 做報告品質稽核。report_model.py 的機械驗證仍有執行並通過，但「findings 是否與程式碼相符」少了一次獨立複查。 |

### Critical

#### F-001 hang 偵測後舊 flusher process 不會被 kill，卻已補上新 process，兩者會同時 flush 同一組 shard — `src/sentry/spans/consumers/process/flusher.py:253-259`

面向 F 資料取用與資料庫 · Critical

**問題**：flusher.py:53 用 multiprocessing.get_context("spawn")，所以 flusher.py:110 產生的物件型別是 multiprocessing.context.SpawnProcess，其 MRO 是 SpawnProcess -> BaseProcess -> object，並不繼承 multiprocessing.Process（也就是 multiprocessing.context.Process）。因此 flusher.py:254 的 isinstance(process, multiprocessing.Process) 在 production 路徑上恆為 False（以 setup.cfg:24 要求的 Python 3.13 實測確認）。變更前這一段是無條件呼叫 self.process.kill()；現在 kill 永遠不會執行，但 flusher.py:259 仍照樣為同一組 shard 建立新 process，而 flusher.py:124 直接把 self.processes[process_index] 換成新的物件，舊 process 的 handle 就此遺失，之後再也無法被 kill 或 join。舊 process 雖然是 daemon，但共用的 stopped 仍是 0，所以它會繼續跑 flusher.py:166 的 while 迴圈。SpansBuffer.flush_segments（buffer.py:378-456）是以 zrangebyscore 做的非破壞性讀取，真正的刪除在產出之後的 done_flush_segments（buffer.py:523-541），因此兩個 process 讀同一組 span-buf:q:{shard} 會把同一批 segment 各自產一次到 buffered-segments topic。這條路徑與 --flusher-processes 的值無關：即使沿用預設 1，_ensure_processes_alive 一樣會在每次 submit() 時執行（flusher.py:267），所以合併當下就會生效。cause 為 no_process_* 時 process 已死、kill 本來就沒有意義；真正受影響的是 cause == "hang"，而那正是這個變更標題所說的 health monitoring 要處理的情況。反證檢查：grep 全 repo 後，沒有其他地方會 kill 或 terminate 這個被遺失的 process——terminate()（flusher.py:318）只設 stopped 旗標，join()（flusher.py:337-347）只走 self.processes 裡的新 process，而且同一個 isinstance 判斷在 flusher.py:346 也恆為 False（該行在變更前即存在，本身不是這次引入的）。另外舊 process 與新 process 共用同一個 process_healthy_since[process_index]（flusher.py:116-117），舊 process 若只是變慢而非完全卡死，它的心跳會讓管理端誤以為一切正常，重複產出就不會有第二次告警。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:253-259`
- `src/sentry/spans/consumers/process/flusher.py:53`
- `src/sentry/spans/consumers/process/flusher.py:110-125`
- `src/sentry/spans/consumers/process/flusher.py:166-205`
- `src/sentry/spans/buffer.py:378-456`
- `src/sentry/spans/buffer.py:523-541`

**修復方向**：把型別判斷換成涵蓋 spawn context 的判斷，或直接回到變更前的無條件 kill()，讓 thread 路徑由已經加上的 AttributeError 吸收：

```python
try:
    process.kill()  # threading.Thread 沒有 kill()，由 AttributeError 接住
except (ValueError, AttributeError):
    pass  # Process already closed, ignore
```

若要保留明確的型別判斷，用 isinstance(process, multiprocessing.process.BaseProcess)（SpawnProcess 是它的子類別），不要用 multiprocessing.Process。建議再加一步：kill 之後以 process.join(timeout=...) 確認舊 process 真的結束，成功後才呼叫 _create_process_for_shards，避免新舊 process 短暫並存；flusher.py:346 join() 裡同樣的 isinstance 判斷也一併換掉，否則 terminate() 在兩種模式下都不會執行。

<details>
<summary>Suggestion（7）</summary>

#### F-002 每個 flusher process 各自換算 max_segments_per_shard，spans.buffer.max-flush-segments 從全域上限變成每 process 上限 — `src/sentry/spans/buffer.py:382-384`

面向 E 架構 · Suggestion

**問題**：flush_segments 以 math.ceil(max_flush_segments / len(self.assigned_shards)) 決定每個 shard 一輪抓多少 segment（buffer.py:382-384），這個式子預設 buffer 持有整個 consumer 的 shard。變更前確實如此：只有一個 SpansBuffer，持有全部 S 個 shard，一輪最多約 max_flush_segments 個 segment（預設 500，defaults.py:2718-2722）。變更後 flusher.py:60-66 把 shard 分成 N 份，flusher.py:93 為每一份各建一個 SpansBuffer，於是每個 buffer 只看到 S/N 個 shard，per-shard 上限變成 ceil(N × max_flush_segments / S)，N 個 process 加總約為 N × max_flush_segments。同一個數字也是 buffer.py:411 判定 any_shard_at_limit 的門檻，所以 backpressure 的觸發點會跟著位移。N=1 時完全不變，但把 N 調大正是這個變更的目的。反證檢查：flush_segments 內沒有其他全域上限，max_segments_per_shard 只由 max_flush_segments 與 len(self.assigned_shards) 決定；options 也沒有另一個 per-consumer 的節流設定。

**證據**：
- `src/sentry/spans/buffer.py:382-384`
- `src/sentry/spans/buffer.py:405-412`
- `src/sentry/spans/consumers/process/flusher.py:60-66`
- `src/sentry/spans/consumers/process/flusher.py:93`
- `src/sentry/options/defaults.py:2716-2722`

**修復方向**：讓上限對整個 consumer 而非單一 process 成立。最小改法是在建立 shard buffer 時把預算切開，例如給 SpansBuffer 一個 max_flush_segments_override 參數，由 SpanFlusher 傳入 ceil(max_flush_segments / num_processes)；或者維持現狀但明確承認語意已變，在 spans.buffer.max-flush-segments 的註解（defaults.py:2717）與 --flusher-processes 的 help 文字裡寫清楚「此值為每個 flusher process 的上限」，讓調參的人不會用舊的心智模型估算 Kafka 產出量。

#### F-003 SpanFlusher 的類別預設從「一個 process」變成「每個 shard 一個 process」 — `src/sentry/spans/consumers/process/flusher.py:47`

面向 E 架構 · Suggestion

**問題**：max_processes 的預設是 None，而 flusher.py:51 是 self.max_processes = max_processes or len(buffer.assigned_shards)，所以不帶參數建構 SpanFlusher 會得到「每個 assigned shard 一個 process」；變更前不論幾個 shard 都只有一個 process。部署中的 process-spans consumer 不受影響，因為 CLI 端在 consumers/__init__.py:435 把預設釘在 1。反證檢查：grep 全 repo，直接建構 SpanFlusher 的只有 src/sentry/spans/consumers/process/factory.py:71（永遠帶 max_processes=）與 tests/sentry/spans/consumers/process/test_flusher.py:29（只有 1 個 shard），所以今天沒有呼叫端會踩到。問題在於類別層級最「不指定」的用法現在是資源用量最高的設定，與 CLI 預設方向相反，之後新增呼叫端很容易在沒有意識到的情況下開出 32 個 process。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:47`
- `src/sentry/spans/consumers/process/flusher.py:51`
- `src/sentry/spans/consumers/process/flusher.py:60`
- `src/sentry/consumers/__init__.py:432-437`

**修復方向**：把類別預設改成 1（self.max_processes = max_processes or 1），讓「開多個 process」永遠是明示的決定，與 --flusher-processes 的預設一致；或把 max_processes 改成必填參數，由呼叫端負責決定。兩種做法都能讓類別預設與部署預設對齊。

#### F-004 _create_process_for_shard（單數）是沒有呼叫端的死碼，且它的名字暗示了一個不存在的能力 — `src/sentry/spans/consumers/process/flusher.py:127-132`

面向 B 簡潔 · Suggestion

**問題**：grep 全 repo（含 tests/）搜尋 _create_process_for_shard，只有 flusher.py:127 的定義本身，沒有任何呼叫端。它與 _create_process_for_shards（複數，flusher.py:86）只差一層 shard -> process_index 的查找，留在檔案裡會讓下一位維護者以為存在「只重啟某一個 shard」的能力；實際上它會重啟整個 process，連同該 process 底下其他 shard 一起中斷。從 commit 歷史看（3a5f8346 remove dead code、828412e3 remove more dead code、079e3dfa remove self.buffer）作者已經在清理重構殘留，這一個應該是漏網的。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:127-132`
- `src/sentry/spans/consumers/process/flusher.py:86-125`

**修復方向**：直接刪除 flusher.py:127-132。若之後真的需要「由 shard 反查 process」的能力，改成一個只回傳 process_index 的查表函式（例如 _process_index_for_shard(shard) -> int | None），名字不要讓人以為可以單獨重啟一個 shard。

#### F-005 test_basic 新增的 time.sleep(0.1) 不會產生任何等待，因為 time.sleep 在同一個測試開頭已被換成 no-op — `tests/sentry/spans/consumers/process/test_consumer.py:15`

面向 G 測試 · Suggestion

**問題**：test_consumer.py:15 以 monkeypatch.setattr("time.sleep", lambda _: None) 替換 time module 上的 sleep 屬性。test_consumer.py:62 寫的是 time.sleep(0.1)，屬性在呼叫當下才解析，取得的就是被替換掉的 no-op，所以這一行不會等待，上一行的註解「Give flusher threads time to process after drift change」與實際行為不符，測試對 flusher thread 的競態沒有變得更穩。同目錄的 test_flusher.py 正好示範了可行的寫法：它在 module 頂端 from time import sleep（test_flusher.py:2），在 monkeypatch（test_flusher.py:20）之前就把原始函式綁進 module namespace，因此 test_flusher.py:79 的 sleep(0.1) 是真的會睡。反證檢查：test_consumer.py 全檔沒有其他地方還原 time.sleep，也沒有 undo 這個 monkeypatch。

**證據**：
- `tests/sentry/spans/consumers/process/test_consumer.py:15`
- `tests/sentry/spans/consumers/process/test_consumer.py:60-62`
- `tests/sentry/spans/consumers/process/test_flusher.py:2`
- `tests/sentry/spans/consumers/process/test_flusher.py:20`
- `tests/sentry/spans/consumers/process/test_flusher.py:79`

**修復方向**：改用與 test_flusher.py 一致的寫法——在 module 頂端 from time import sleep，測試裡呼叫 sleep(0.1)。更穩的做法是不要用固定睡眠，改成輪詢直到條件成立或逾時，例如：

```python
from time import monotonic, sleep

deadline = monotonic() + 5
while not messages and monotonic() < deadline:
    sleep(0.01)
assert messages
```

#### F-006 test_flusher_processes_limit 只驗證帳面數字，沒有驗證「兩個 process 共用四個 shard 仍然不漏資料」 — `tests/sentry/spans/consumers/process/test_consumer.py:84-123`

面向 G 測試 · Suggestion

**問題**：這個測試建立 4 個 partition、限制 2 個 process，然後斷言 len(flusher.processes) == 2、max_processes == 2、num_processes == 2，以及 process_to_shards_map 裡的 shard 總數是 4。這些全部是建構子剛剛寫進去的欄位，等於在驗證 min() 與一行 round-robin 迴圈；messages 這個 list 從頭到尾沒有被斷言過，所以「shard 被拆到不同 process 之後，segment 仍然都會被 flush 出來」——也就是本次變更真正的風險——完全沒有被覆蓋。另外，改動幅度最大的 _ensure_processes_alive（flusher.py:218-259）沒有任何測試碰到，F-001 那個問題正是因此不會被測試攔下。

**證據**：
- `tests/sentry/spans/consumers/process/test_consumer.py:84-123`
- `tests/sentry/spans/consumers/process/test_consumer.py:113-121`
- `src/sentry/spans/consumers/process/flusher.py:218-259`

**修復方向**：沿用 test_basic 的模式：對 4 個 partition 各送一筆 span，推進 fac._flusher.current_drift.value，等待後斷言 messages 裡出現 4 個 segment 且各自的 segment_id / trace_id 正確，確認沒有 shard 被漏掉、也沒有重複。另外補一個直接針對 _ensure_processes_alive 的測試：把某個 process 的 process_healthy_since 往回撥超過 max-unhealthy-seconds，呼叫 submit()，斷言舊 process 已不再 is_alive()、且 self.processes 裡該 index 換成了新物件。

#### F-007 新增的 shard metrics tag 是不受控的 cardinality，tag key 不一致，且 flusher_unhealthy 的計數語意被放大 — `src/sentry/spans/consumers/process/flusher.py:144`

面向 E 架構 · Suggestion

**問題**：shard_tag 是把該 process 負責的 shard 逗號串接而成（flusher.py:144），接著被當成 metrics tag 值用在 spans.buffer.flusher.produce（flusher.py:185）、spans.buffer.segment_size_bytes（flusher.py:192-196）與 spans.buffer.flusher.wait_produce（flusher.py:199）。這個字串的內容取決於 Kafka partition assignment 與 --flusher-processes，每次 rebalance 都可能產生一組沒出現過的組合，tag 值的數量因此不受控；segment_size_bytes 又是每個 segment 都發一次的 timing，成本更明顯。同一段程式的 tag key 也不一致：flusher.py:185 與 192-196 用 shard，flusher.py:199 用 shards，兩者的值卻是同一個 shard 清單，查詢端得同時記住兩個名字。另外 flusher.py:242-245 把 spans.buffer.flusher_unhealthy 改成對該 process 底下每個 shard 各 incr 一次，而 _ensure_processes_alive 在每次 submit() 都會執行，因此同一次不健康狀態的計數會放大成 shard 數倍，既有以此為基礎的 dashboard 或告警閾值會失準。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:144`
- `src/sentry/spans/consumers/process/flusher.py:185`
- `src/sentry/spans/consumers/process/flusher.py:192-196`
- `src/sentry/spans/consumers/process/flusher.py:199`
- `src/sentry/spans/consumers/process/flusher.py:242-245`

**修復方向**：tag 值改用低 cardinality 的維度，例如 process_index，或乾脆不打 shard tag；需要知道是哪些 shard 時保留 flusher.py:146 的 sentry_sdk.set_tag 或改用 log 即可，那裡不受 metrics 的 cardinality 預算限制。tag key 統一成 shards（或統一成 shard），不要在相鄰三行用兩個名字。flusher_unhealthy 若要保留辨識能力，改回一次 incr 並帶上 shards tag，或在 PR 描述明確標註計數語意變更，讓 dashboard 與 alert 一併調整。

#### F-008 class docstring 新增的敘述與實際預設行為相反，且新增的 max_processes 沒有寫進 :param 區塊 — `src/sentry/spans/consumers/process/flusher.py:30-41`

面向 A 風格 · Suggestion

**問題**：docstring 新增的「Creates one process per shard for parallel processing」（flusher.py:32）只有在 max_processes 為 None 時成立。實際部署的 process-spans consumer 走的是 --flusher-processes 的預設值 1（consumers/__init__.py:435），也就是「一個 process 跑全部 shard」，與這句話描述的相反；讀 docstring 的人會對這個類別的資源行為建立錯誤的預期。同時 :param 區塊（flusher.py:39-40）只列了 topic（這個參數其實不存在，變更前即如此）與 produce_to_pipe，這次新增的 max_processes 沒有補上。這裡沒有依「簽章變更 + 過時 docstring」升到 Critical，是因為既有參數的契約沒有改變，誤導的是類別層級的散文描述，而不是呼叫端該傳什麼。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:30-41`
- `src/sentry/spans/consumers/process/flusher.py:47`
- `src/sentry/spans/consumers/process/flusher.py:51`
- `src/sentry/consumers/__init__.py:433-437`

**修復方向**：把那句改成描述實際規則，例如「Spawns up to `max_processes` worker processes and distributes the buffer's assigned shards across them round-robin; `max_processes=None` means one process per shard.」，並在 :param 區塊補上 max_processes（順帶移除已不存在的 :param topic:）。

</details>

<details>
<summary>Nit（6）</summary>

#### F-009 join() 在 deadline 到期時 break，會讓其餘 process 連 terminate() 都被跳過 — `src/sentry/spans/consumers/process/flusher.py:337-347`

面向 B 簡潔 · Nit

**問題**：內層的 while 迴圈本身已經帶了 deadline 檢查（flusher.py:343），deadline 到期時它會立刻結束、然後執行 flusher.py:346-347 的 terminate()。外層在 flusher.py:338-341 額外加的檢查不是「跳過等待」而是 break 整個迴圈，結果是清單中剩下的所有 process 都不會被 terminate()。實務影響有限——process 是 daemon 且 stopped 已在 flusher.py:331 設起來，最終仍會自行結束——但這與變更前「等待結束後一定會 terminate」的語意不同，也和同一段程式碼的意圖不一致。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:337-347`

**修復方向**：把外層的提前 break 改成 continue（跳過等待但仍執行 terminate），或直接刪掉外層檢查、交給內層的 while 條件處理；terminate 的型別判斷同時依 F-001 一併換成 multiprocessing.process.BaseProcess。

#### F-010 _ensure_processes_alive 開頭的 `if not process: continue` 永遠不會成立 — `src/sentry/spans/consumers/process/flusher.py:221-223`

面向 B 簡潔 · Nit

**問題**：self.processes 的值只會是 SpawnProcess 或 threading.Thread，兩者都沒有定義 __bool__ 或 __len__，所以 not process 恆為 False（以 Python 3.13 實測確認），這個保護分支永遠不會執行。self.processes 也不可能被塞進 None——唯一的寫入點是 flusher.py:124，寫的是 make_process(...) 的回傳值。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:221-223`
- `src/sentry/spans/consumers/process/flusher.py:123-124`

**修復方向**：刪除 flusher.py:222-223。若真的想防「某個 process_index 尚未建立」的情況，改成以 process_to_shards_map 為迭代來源、用 self.processes.get(process_index) 取值再判斷 is None，語意才會真的成立。

#### F-011 SpanFlusher 只從傳入的 buffer 取 assigned_shards，隨即丟棄並自建 N 個 SpansBuffer — `src/sentry/spans/consumers/process/flusher.py:45`

面向 E 架構 · Nit

**問題**：factory.py:67 建立一個涵蓋全部 shard 的 SpansBuffer 傳進 SpanFlusher，但 SpanFlusher 只讀它的 assigned_shards（flusher.py:51、64），之後在 flusher.py:93 自己建 N 個新的 SpansBuffer，傳進來的那個物件在 flusher 內就再也沒被使用過——簽章要求一個 buffer，實際需要的只是一份 shard 清單。副作用是 parent process 除了 factory 那一個之外還多出 N 個 SpansBuffer，各自持有自己的 client cached_property（buffer.py:161-163），也就是多 N 條 Redis 連線；而且每次 process 重啟都會在 flusher.py:93、125 再換掉一個，造成連線抽換。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:45`
- `src/sentry/spans/consumers/process/flusher.py:51`
- `src/sentry/spans/consumers/process/flusher.py:60-66`
- `src/sentry/spans/consumers/process/flusher.py:93`
- `src/sentry/spans/consumers/process/factory.py:67-76`
- `src/sentry/spans/buffer.py:161-163`

**修復方向**：把建構子參數改成 assigned_shards: list[int]，讓「這個類別只需要 shard 清單」變成簽章上的事實（factory.py:71 改傳 [p.index for p in partitions]）；或反過來，由呼叫端建好切分後的 SpansBuffer 再傳進來。若維持現狀，至少在 _create_process_for_shards 重啟時重用既有的 self.buffers[process_index]，不要每次都新建。

#### F-012 --flusher-processes 對 0 與負值沒有防護：0 的效果與使用者意圖相反，負值以 KeyError 收場 — `src/sentry/consumers/__init__.py:432-437`

面向 A 風格 · Nit

**問題**：click option 只宣告 type=int，沒有下界。傳 --flusher-processes 0 時，flusher.py:51 的 max_processes or len(buffer.assigned_shards) 因為 0 是 falsy 而落回「每個 shard 一個 process」，與輸入 0 的意圖正好相反、而且是資源用量最高的那一端。傳負值時 flusher.py:60 的 num_processes 會是負數，flusher.py:61-63 的 {i: [] for i in range(-1)} 是空 dict，接著 flusher.py:65 的 i % -1 得到 0，在 flusher.py:66 以 KeyError: 0 中止，錯誤訊息完全看不出是參數給錯。

**證據**：
- `src/sentry/consumers/__init__.py:432-437`
- `src/sentry/spans/consumers/process/flusher.py:51`
- `src/sentry/spans/consumers/process/flusher.py:60-66`

**修復方向**：在 click option 上加下界：type=click.IntRange(min=1)，讓錯誤在解析參數時就被擋下並給出清楚訊息；同時把 flusher.py:51 改成 max_processes if max_processes is not None else len(buffer.assigned_shards)，讓 0 與 None 不再被混為一談。

#### F-013 CLAUDE.md 新增的 hasattr / isinstance 段落與本次 span buffer 變更無關，且結尾多了一個空行 — `CLAUDE.md:449-458`

面向 H 非 Python 檔 · Nit

**問題**：這次 diff 除了 span buffer 相關檔案外，還在 CLAUDE.md 的 Python 慣例區塊加了一段 hasattr / isinstance 的示例（CLAUDE.md:449-457）。建議本身沒有問題，但它與 span flusher 多 process 化沒有關係，混在同一個變更裡會讓之後用 git log 追這條慣例是何時、為何加入的人找不到脈絡。另外這段在 CLAUDE.md:458 留下一個空行才接上 CLAUDE.md:459 的 code fence；變更前這個 code block 是以 CLAUDE.md:447 的 organizations.prefetch_related('projects') 直接收在 fence 前，中間沒有空行。

**證據**：
- `CLAUDE.md:449-458`

**修復方向**：把 CLAUDE.md 的這一段拆成獨立的變更，或在 PR 描述裡說明為什麼順手一起加；並移除 CLAUDE.md:458 這個新增的空行，讓 code block 的結尾維持變更前的形式。

#### F-014 submit() 的 Redis 記憶體檢查改成逐 buffer 呼叫，等於把同一份 cluster-wide INFO 重複查 N 次 — `src/sentry/spans/consumers/process/flusher.py:297-303`

面向 F 資料取用與資料庫 · Nit

**問題**：SpansBuffer.get_memory_info（buffer.py:375-376）直接回傳 iter_cluster_memory_usage(self.client)，而後者（memory.py:82-105）是對整個 Redis cluster 呼叫一次 cluster.info() 再逐節點 yield——它完全不看 assigned_shards。變更前只有一個 buffer，所以一次 submit() 查一次；現在 flusher.py:299-301 改成對 self.buffers.values() 逐一 extend，N 個 flusher process group 就會把同一份 cluster-wide INFO 抓 N 次再加總。反證檢查（兩點都成立，所以只是 Nit 而非更高）：第一，used 與 available 同時被放大 N 倍，flusher.py:304 的 used / available 比值不變，backpressure 判斷本身沒有錯；第二，這整段被 flusher.py:298 的 if max_memory_percentage < 1.0 包住，而 spans.buffer.max-memory-percentage 的預設就是 1.0（defaults.py:2723-2729），所以預設設定下這段根本不會執行，只有把該 option 調低的環境才會付出這個成本。

**證據**：
- `src/sentry/spans/consumers/process/flusher.py:297-303`
- `src/sentry/spans/buffer.py:375-376`
- `src/sentry/processing/backpressure/memory.py:82-105`
- `src/sentry/options/defaults.py:2723-2729`

**修復方向**：cluster 記憶體是全域資訊，不需要依 buffer 重複查。取任一個 buffer 問一次即可，例如：

```python
buffer = next(iter(self.buffers.values()), None)
memory_infos = list(buffer.get_memory_info()) if buffer is not None else []
```

或更清楚地把這個查詢從 SpansBuffer 拉出來，直接呼叫 iter_cluster_memory_usage(get_redis_client())，讓「這是 cluster 層級的量測、與 shard 無關」在呼叫端就看得出來。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 把 --flusher-processes 調到 N 之後，Kafka producer 與 Redis 的連線數會變成約 N 倍；目標叢集有這個餘裕嗎？打算把 N 設到多少？

面向 E 架構

**背景**：每個 flusher process 在 src/sentry/spans/consumers/process/flusher.py:155-158 各自建立一個 KafkaProducer，parent process 也會多出 N 個 SpansBuffer、各自持有一條 Redis 連線（src/sentry/spans/buffer.py:161-163）。這個 repo 裡看不到 process-spans consumer 實際的 replica 數，也看不到 Kafka / Redis 叢集的連線預算，所以「N 倍連線是否安全」在這裡無法判定，不適合掛上嚴重度。

**如何確認**：process-spans consumer 的部署設定（replica 數與預計傳入的 --flusher-processes 值），加上目標 Kafka cluster 與 Redis 的現行連線數與上限（Kafka broker 的連線配額、Redis 的 maxclients 與目前使用量）。

#### Q-002 tests/sentry/spans/consumers/process/test_consumer.py 的兩個測試在 CI 上是否穩定？

面向 G 測試

**背景**：兩個測試都改成 @pytest.mark.django_db(transaction=True) 並依賴真實的 thread 與 Redis；test_basic 又把 time.sleep 換成 no-op（test_consumer.py:15），使 flusher thread 變成 busy loop，而 F-005 指出它新增的等待其實不會等待。這台機器沒有可用的 Redis / Kafka，也沒有 sentry 的測試相依環境，無法執行測試套件，因此「是否 flaky」只能推測，不能當成結論。

**如何確認**：在 CI 上重複執行 tests/sentry/spans/consumers/process/（例如 pytest-repeat 連跑 50 次）觀察是否出現間歇性失敗，特別是 test_basic 最後的 (msg,) = messages 這一行。

</details>
