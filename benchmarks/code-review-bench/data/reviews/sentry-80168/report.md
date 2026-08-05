## 審查結論：Request Changes

> Critical 1 · Suggestion 2 · Nit 4 · 未驗證提問 4
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
| ❌ | — | ❌ |

- **A 風格**（未通過）：F-001（簽章改了但 docstring 沒改）、F-004（已經過時的 TODO）、F-005（參數名蓋掉 builtin type）、F-007（builtins.type 的規避寫法缺註解）。ruff 對本次 diff 全數通過，這四項都是 linter 看不到的語意層問題。F-005 與 F-007 兩者其實是同一個名稱衝突的兩端：model 有個欄位叫 `type`，於是生產程式碼要繞開它、測試又再拿它當參數名。
- **B 簡潔**（未通過）：F-006：測試裡留下兩處沒有作用的殘留（只呼叫 super 的 setUp override、沒有任何 caller 傳值的 detector_type 參數）。正面的部分：把重複鍵偵測那段 for-loop 換成 dict 回傳型別，是用型別讓錯誤狀態變成不可表達，而不是加一段偵測程式碼，方向是對的。
- **D API 慣例**（不適用）：本次 diff 沒有任何 HTTP endpoint、URL、序列化 schema 或 API 版本變更。改動範圍是 Django model 的 property、一個內部 abstract base class 與其測試。
- **G 測試**（未通過）：F-002：測試看起來覆蓋了新 hook 的每一條路徑，實際上對 IssueOccurrence 的斷言全部是空的。這正是 dimension G 第 3 條反例的變體——不是「mock 回什麼就斷言什麼」，而是「斷言的等值運算只比對一個寫死的欄位」。
- **H 非 Python 檔**（不適用）：diff 只含 4 個 .py 檔（src/sentry/incidents/grouptype.py、src/sentry/workflow_engine/models/detector.py、src/sentry/workflow_engine/processors/detector.py、tests/sentry/workflow_engine/processors/test_detector.py），沒有非 Python 檔案。
- **I 回溯分析**（未通過）：F-003：改變 MetricAlertDetectorHandler 的基底類別使它變成無法實例化的抽象類別，但它仍掛在 MetricAlertFire.detector_handler 上。其餘簽章變更已逐一追過：DetectorHandler.evaluate 的 list→dict 只有三個 subclass（MetricAlertDetectorHandler、MockDetectorHandler、MockDetectorStateHandler），三個都在本 diff 內處理；process_detectors 的回傳型別變更在 src/ 底下無 caller，只有測試。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | diff_findings 0、preexisting_project_wide 206 |
| trivy | 略過 | trivy 未安裝（preflight 回報 available=false），略過相依套件漏洞、misconfiguration 與 secret 掃描。本次 diff 未動任何相依宣告或設定檔，影響有限，但仍如實揭露。 |
| opengrep | 略過 | opengrep 未安裝，且 NCR_OPENGREP_RULES 指向的 Semgrep 規則目錄也不存在（兩者皆缺）。SAST 掃描未執行，dimension C 的判定完全來自人工閱讀。 |
| ty | 略過 | ty 未安裝，略過 Python 型別檢查。這對本次審查有實質影響：新增的 abstract method 與 list→dict 的回傳型別變更正是型別檢查最容易抓到的一類問題，本報告改以人工逐一追 subclass 與 caller 補上（見 dimension I）。 |
| oxlint | 略過 | oxlint 未安裝；本次 diff 也沒有任何 JavaScript / TypeScript 檔案，即使安裝也無可掃描的對象。 |
| codegraph | 略過 | codegraph 未安裝，符號圖未建立。caller / subclass 的列舉改以 grep 逐一完成（process_detectors、detector_handler、StatefulDetectorHandler、DetectorHandler[ 四組查詢），結論寫在 dimension I。 |
| ncr-fresh-eyes (subagent) | 已執行 | **執行順序有偏差，如實揭露**：審查 agent 的執行環境沒有可派發 subagent 的工具（無 Agent / Task tool），第一輪（見 report.stage1.json）因此在沒有 fresh eyes 的情況下完成。fresh eyes 事後由 orchestrator 從外部派出，在本報告完成後才送達——也就是說它違反了 SKILL.md Phase 3 step 1「在九大面向之前」的順序，但仍滿足了該步驟真正的要求：它的 prompt 不含任何面向清單、severity 詞彙、掃描摘要或本報告的既有發現。所以它是一次未被本 skill 框架塑形的閱讀，只是來得太晚，無法影響第一輪的取材方向。四項觀察已逐一回到程式碼驗證（含 file:line，確認是檔案行號而非 diff 偏移量）：兩項與既有發現重複（F-003 / F-004、F-002），一項成為新的 F-007，一項成為 Q-004。 · observations_received 4、adopted_as_new_findings 1、adopted_as_open_questions 1、already_filed 2 |
| ncr-quality-check (subagent) | 略過 | 審查 agent 的環境無法派發 subagent，Phase 4 step 3 的獨立品質複查未執行（fresh eyes 由 orchestrator 補派，quality check 沒有）。報告的自我檢查（每個 finding 有 fix、Critical 與結論一致、九格全部有判定、無本機路徑）改由 report_model.py validate 與 render_report.py 的自足性檢查機械完成，但那兩者檢查的是結構，不是論證品質。 |

### Critical

#### F-001 evaluate 的回傳型別由 list 改為 dict，docstring 仍寫「returns a list」 — `src/sentry/workflow_engine/processors/detector.py:226-233`

面向 A 風格 · Critical

**問題**：StatefulDetectorHandler.evaluate 的簽章在本次變更由 `-> list[DetectorEvaluationResult]` 改成 `-> dict[DetectorGroupKey, DetectorEvaluationResult]`，函式尾端也確實改成 `return results`（dict）。但 docstring 第一句仍是「Evaluates a given data packet and returns a list of `DetectorEvaluationResult`」，沒有跟著改。

本團隊對「簽章改了但 docstring 或 type hint 沒跟著改」的定位是 Critical，理由不是排版：文件現在會對下一個 caller 說錯話。這裡的錯法還特別安靜——dict 和 list 都能 `for x in results:` 跑得下去，只是前者迭代出來的是 group key 字串而不是 DetectorEvaluationResult。一個照 docstring 寫 `for result in handler.evaluate(packet): result.priority` 的實作者，會在 runtime 拿到 AttributeError，或更糟，在 group key 是 None 的單群組情境下拿到別的行為。

DetectorHandler 是對外的 abstract base class，會有本 repo 以外的實作者讀這段 docstring，所以受眾不是假設性的。type hint 本身是正確的，這降低了實際踩到的機率，但沒有改變 docstring 現在在說謊這件事。

**證據**：
- `src/sentry/workflow_engine/processors/detector.py:226-233`
- `src/sentry/workflow_engine/processors/detector.py:244`

**修復方向**：把 docstring 改成描述實際回傳值，例如：

```python
    def evaluate(
        self, data_packet: DataPacket[T]
    ) -> dict[DetectorGroupKey, DetectorEvaluationResult]:
        """
        Evaluates a given data packet and returns the results keyed by group key.
        There will be one entry for each group key in the packet, unless the
        evaluation is skipped due to various rules.
        """
```

順帶一提，`DetectorHandler.evaluate`（同檔 :125-129）這個 abstract method 本身沒有 docstring，實作者實際上更可能讀到的是它。把回傳值的契約（「一個 group key 一筆，key 必須等於 result.group_key」）寫在 abstract method 上會比寫在 StatefulDetectorHandler 上更有用。

<details>
<summary>Suggestion（2）</summary>

#### F-002 測試對 IssueOccurrence 的斷言是空的——__eq__ 只比 id，而 mock 對所有 occurrence 寫死同一個 id — `src/sentry/issues/issue_occurrence.py:181-183`

面向 G 測試 · Suggestion

**問題**：`IssueOccurrence.__eq__` 的實作是 `return self.id == other.id`（src/sentry/issues/issue_occurrence.py:181-183），只比一個欄位。而 `build_mock_occurrence_and_event` 對每一個產生出來的 occurrence 都寫死同一個 id 字串 `"eb4b0acffadb4d098d48cb14165ab578"`（tests/…:252）。

兩者相乘的結果是：**任何兩個由這個 helper 產生的 IssueOccurrence 都相等**，不管 fingerprint、group_key、priority、project_id、detection_time 差多少。所以本次新增的整組斷言——也就是這個 PR 最主要的新行為——實際上沒有驗證 occurrence 的任何內容。

這不是推論，diff 裡就有兩個現成的症狀在證明它：

1. `TestEvaluateGroupKeyValue.test_dedupe`（:536-548）用 `build_mock_occurrence_and_event(handler, "val1", 6, …)` 建期望值，但受測呼叫傳進去的 group_key 是 `"group_key"`（:548）。期望的 fingerprint 是 `["{id}:val1"]`，實際產生的是 `["{id}:group_key"]`，完全不同——測試照樣通過。
2. `test_state_results_multi_group`（:190-192）用 value `6` 為 group_2 建期望值，但 packet 裡 group_2 的值是 `10`（:177）。同樣不會被抓到，另一個原因是 helper 的 body 從頭到尾沒有讀 `value`（:244-275，建出來的 IssueOccurrence 與 event_data 都不依賴它）。把八個明確呼叫點列出來會更清楚：:151、:180、:191、:428、:446、:490、:510、:537 全部傳同一個字面量 `6`，而各測試 packet 裡真正驅動判定的值是 6、8、10、100。也就是說新增的 `build_occurrence_and_event_data(group_key, value, new_status)` 三個引數裡，`value` 從頭到尾沒有被任何測試驗證過，而呼叫端寫著一個看起來有意義、其實是裝飾用的數字。

再加上 :206-222 的 `assert_has_calls(..., any_order=True)` 既不檢查呼叫次數，兩個期望 call 又因為 occurrence 互相相等而可以任意對調，「哪個 group key 產生了哪個 occurrence」這件事在多群組測試裡是零覆蓋。

代價是實質的：現在若有人把 `build_occurrence_and_event_data(group_key, value, …)` 的引數順序寫反、或把 fingerprint 建錯，整組測試依然全綠。

**證據**：
- `src/sentry/issues/issue_occurrence.py:181-183`
- `tests/sentry/workflow_engine/processors/test_detector.py:252`
- `tests/sentry/workflow_engine/processors/test_detector.py:536-548`
- `tests/sentry/workflow_engine/processors/test_detector.py:190-192`
- `tests/sentry/workflow_engine/processors/test_detector.py:206-222`

**修復方向**：讓 mock 的 occurrence 的 `id` 帶進區別性，等值比較就會恢復意義。最小改法：

```python
def build_mock_occurrence_and_event(
    handler, group_key, value, new_status
):
    assert handler.detector.group_type is not None
    occurrence = IssueOccurrence(
        id=f"{handler.detector.id}:{group_key}:{value}:{new_status}",
        ...
        evidence_data={"group_key": group_key, "value": value},
        ...
    )
```

這樣 test_dedupe 的 `"val1"` / `"group_key"` 不一致會立刻紅掉（順手修掉即可），test_state_results_multi_group 的 `6` 也要改成 `10`。

另外建議兩點：`assert_has_calls` 改成先 `assert mock_produce_occurrence_to_kafka.call_count == 2` 再比對，避免多送一次不被發現；以及至少留一個測試直接對 fingerprint 斷言（`assert results["group_1"].result.fingerprint == [f"{detector.id}:group_1"]`），這樣即使日後 `IssueOccurrence.__eq__` 又變了，這條路徑仍有實質保護。

#### F-003 MetricAlertDetectorHandler 變成無法實例化的抽象類別，卻仍註冊在 MetricAlertFire.detector_handler 上 — `src/sentry/incidents/grouptype.py:11-12`

面向 I 回溯分析 · Suggestion

**問題**：變更前，`MetricAlertDetectorHandler(DetectorHandler[QuerySubscriptionUpdate])` 實作了 `evaluate` 並回傳 `[]`，是一個可以實例化的 no-op handler。變更後它改成 `StatefulDetectorHandler[QuerySubscriptionUpdate]` 且 body 只有 `pass`，於是繼承了四個未實作的 abstract member：`counter_names`、`get_dedupe_value`、`get_group_key_values`，以及本次新增的 `build_occurrence_and_event_data`。

DetectorHandler 繼承 `abc.ABC`，所以 metaclass 是 ABCMeta，這個類別現在無法實例化。而 `Detector.detector_handler`（src/sentry/workflow_engine/models/detector.py:86）的最後一行正是 `return group_type.detector_handler(self)`——對任何 `type == "metric_alert_fire"` 的 Detector 存取這個 property，會得到 `TypeError: Can't instantiate abstract class MetricAlertDetectorHandler with abstract methods build_occurrence_and_event_data, counter_names, get_dedupe_value, get_group_key_values`，而不是原本的 handler 或 None。MetricAlertFire 在啟動時會被註冊（sentry.runner.initializer 呼叫 import_grouptype，逐一匯入各 app 的 grouptype 模組），所以這個 slug 是真的存在於 registry 裡。

**為什麼不是 Critical——反證的搜尋結果：**

1. `process_detectors` 是唯一會走到 `detector.detector_handler` 的呼叫點，而它在 `src/` 底下沒有任何 production caller。grep `workflow_engine.processors` 的結果只有：模組自身的 `__init__.py` 匯出、兩處 TYPE_CHECKING 匯入，以及測試。也就是說今天沒有任何路徑會實例化它。
2. 型別檢查也不會擋下來。sentry 的 mypy 設定是 `files = ["."]`（pyproject.toml:57），照理說 `type-abstract` 這條規則會抓到把抽象類別指派給 `type[X]` 的變數；但 `GroupType.detector_handler` 的宣告型別是 `type[DetectorHandler] | None`（src/sentry/issues/grouptype.py:161），union 會讓 mypy 跳過這項檢查。我用一支獨立的最小重現檔（不在受審 repo 內、不需網路）確認過：`x: type[A] = B` 會報 `Can only assign concrete classes to a variable of type "type[A]"  [type-abstract]`，但把宣告改成 dataclass 欄位上的 `type[A] | None` 之後 mypy 回報 Success。所以 CI 不會紅。

結論：這不是立即故障，是一顆留在 registry 裡的地雷——等到 metric alert 的 detector 真的被建立、或 process_detectors 接上消費端的那一刻才會爆，而那時的 traceback 會指向 models/detector.py，離真正的原因（grouptype.py 的 `pass`）有一段距離。

**證據**：
- `src/sentry/incidents/grouptype.py:11-12`
- `src/sentry/incidents/grouptype.py:27`
- `src/sentry/workflow_engine/models/detector.py:86`
- `src/sentry/workflow_engine/processors/detector.py:166-170`

**修復方向**：兩條路，選一條並在程式碼裡說清楚：

**A. 若這個類別現在就該是不可用的佔位**——把註冊也一起拿掉，讓 `MetricAlertFire.detector_handler` 暫時留 `None`。`Detector.detector_handler` 已經有處理這個情況的分支（models/detector.py:76-85，會 log「Registered grouptype for detector has no detector_handler」並回傳 None），失敗方式會從 TypeError 變成一行明確的 error log。

**B. 若希望它保持可實例化**——補上四個 abstract member 的最小實作，例如 `counter_names = []`、`get_dedupe_value` 回傳 `data_packet.packet["timestamp"]`、`get_group_key_values` 回傳 `{None: ...}`、`build_occurrence_and_event_data` 先 `raise NotImplementedError("metric alert occurrences not implemented yet")`。這樣未實作的部分仍然會炸，但炸在正確的位置，訊息也說得出原因。

另外，第 10 行的註解 `# TODO: This will be a stateful detector when we build that abstraction` 應該一併處理（見 F-004）。

</details>

<details>
<summary>Nit（4）</summary>

#### F-004 已經被這次變更完成的 TODO 註解沒有移除 — `src/sentry/incidents/grouptype.py:10`

面向 A 風格 · Nit

**問題**：`# TODO: This will be a stateful detector when we build that abstraction` 就掛在 `class MetricAlertDetectorHandler(StatefulDetectorHandler[...])` 上方。抽象已經建好了，這個類別現在就是 stateful detector，這行 TODO 描述的未來已經是現在。留著的成本是下一個人會花時間確認「所以到底做了沒」。

**證據**：
- `src/sentry/incidents/grouptype.py:10`

**修復方向**：刪掉這行。若要保留一個 TODO，改成描述真正還沒做的事，例如 `# TODO: Implement counter_names / get_dedupe_value / get_group_key_values / build_occurrence_and_event_data for metric alerts`——這也正好會把 F-003 指出的缺口寫在最容易被看到的地方。

#### F-005 測試 helper 的參數名 `type` 蓋掉 builtin — `tests/sentry/workflow_engine/processors/test_detector.py:86-92`

面向 A 風格 · Nit

**問題**：`def create_detector_and_conditions(self, type: str | None = None)` 在函式範圍內把 builtin `type` 蓋掉。這裡函式很短、沒有用到 builtin，所以不會出錯；但它同時也讀不太順——`type` 在這個 codebase 裡同時是 Detector 的欄位名、GroupType 的概念、和 builtin，呼叫端 `self.create_detector_and_conditions(type=self.handler_state_type.slug)` 要多想一秒才知道指的是 detector 的 slug。同一個檔案裡的 `build_handler(self, detector=None, detector_type=None)`（:101-102）已經用了比較清楚的命名。

**證據**：
- `tests/sentry/workflow_engine/processors/test_detector.py:86-92`

**修復方向**：改名為 `detector_type`，和同檔 `build_handler` 的參數一致：`def create_detector_and_conditions(self, detector_type: str | None = None)`，內部 `type=detector_type` 傳給 `self.create_detector`。呼叫端只有 :147 和 :176 兩處。

#### F-006 測試重構後留下兩處沒有作用的殘留 — `tests/sentry/workflow_engine/processors/test_detector.py:125-126`

面向 B 簡潔 · Nit

**問題**：這次把 `StatefulDetectorHandlerTestMixin` 的內容併進新的 `BaseDetectorHandlerTest`，搬移過程留下兩個沒有效果的東西：

1. `TestProcessDetectors.setUp`（:125-126）現在只有 `super().setUp()` 一行，等同於不覆寫。
2. `build_handler(self, detector=None, detector_type=None)` 的 `detector_type` 參數，檔案裡十處呼叫（:329、:338、:350、:381、:425、:442、:487、:507、:534、:582）全部是無參數的 `self.build_handler()`，沒有任何 caller 傳值。

兩者都不會造成錯誤，但都是「看起來有意圖、其實沒有」的程式碼，讀的人得先確認一次才敢動。

**證據**：
- `tests/sentry/workflow_engine/processors/test_detector.py:125-126`
- `tests/sentry/workflow_engine/processors/test_detector.py:101-106`

**修復方向**：刪掉 `TestProcessDetectors.setUp`。`detector_type` 參數若是為了之後要用，留著並加一行註解說明用途；否則一併刪掉，`build_handler` 簡化為 `def build_handler(self, detector: Detector | None = None) -> MockDetectorStateHandler:`，內部呼叫 `self.create_detector_and_conditions()`。

#### F-007 `builtins.type[GroupType]` 這個規避寫法是對的，但沒有註解說明，下一個人很可能會把它「簡化」回去 — `src/sentry/workflow_engine/models/detector.py:3`

面向 A 風格 · Nit

**問題**：`import builtins`（:3）在這個檔案裡只為了一件事存在：把 `group_type` 的回傳型別寫成 `builtins.type[GroupType] | None`（:59）。

這個寫法是正確且必要的，不是多餘的裝飾。`Detector` 這個 model 有一個欄位就叫 `type`（:43，`type = models.CharField(max_length=200)`），所以在 class body 的名稱解析範圍內，`type` 指的是那個欄位而不是 builtin；直接寫 `-> type[GroupType] | None` 會被型別檢查器解讀成「對一個 CharField 做下標」。本 PR 的第二個 commit（標題就叫 "fix typing"）唯一的內容正是把 `type[GroupType]` 改成 `builtins.type[GroupType]`，所以這是作者實際踩到、實際修掉的問題。

問題在於現在讀起來像個沒必要的迂迴。`import builtins` 在 Python 程式碼裡罕見到會讓人停下來，而讓它變成必要的那個原因（同名欄位）在 16 行之外，兩者之間沒有任何線索相連。下一次有人做 import 清理或「簡化型別註解」，把它改回 `type[GroupType]` 是很自然的動作——而且改完之後 CI 會紅，所以不是無聲失敗，只是白白繞一圈重新發現同一件事。這正是 dimension A 第 8 條說的情況：需要的不是重寫，是一句說明為什麼。

**證據**：
- `src/sentry/workflow_engine/models/detector.py:3`
- `src/sentry/workflow_engine/models/detector.py:43`
- `src/sentry/workflow_engine/models/detector.py:59`

**修復方向**：在該 property 上方加一行註解，把原因和位置一起講掉：

```python
    @property
    def group_type(self) -> builtins.type[GroupType] | None:
        # `builtins.type` rather than `type`: this model has a `type` field (see above),
        # which shadows the builtin inside the class body.
        return grouptype.registry.get_by_slug(self.type)
```

（另一種等效寫法是把註解放在 `import builtins` 旁邊，但放在使用處比較不會在下次 import 排序時被沖散。）

</details>

<details>
<summary>未驗證提問（4）</summary>

#### Q-001 MetricAlertDetectorHandler 留成 `pass` 是刻意的佔位（等 metric alert 的 stateful 實作），還是這次改基底類別時漏掉的？

面向 I 回溯分析

**背景**：F-003 已經確認它現在無法實例化、而且沒有任何 production 路徑會踩到。但「該不該現在就補上實作」不是從程式碼能讀出來的：可能作者刻意讓它保持空白，等後續 PR 一次補齊；也可能是換基底類別時只想著型別對得上，沒注意到 abstract member 變多了。這個判斷會決定 F-003 該走 fix 的 A 案還是 B 案。

**如何確認**：作者說明後續是否已有補上 metric alert stateful 實作的 PR；或 repo 內是否有記錄這個 workflow_engine 遷移順序的 issue / RFC。

#### Q-002 `Detector.project_id` 仍然是寫死的 `1`；在真正的 detector 上線之前，有什麼機制擋住 occurrence 被寫到 project 1？

面向 F 資料取用與資料庫

**背景**：src/sentry/workflow_engine/models/detector.py:53-56 的 `project_id` property 帶著 `# XXX: Temporary property until we add project_id to the model` 直接 `return 1`，這是既有狀況、不是本次引入的。但這次變更把「產生 IssueOccurrence 並送進 Kafka」這條路接通了（processors/detector.py:299-301 → :60-62 → :82），而 occurrence 的 project_id 幾乎一定會來自 `handler.detector.project_id`——測試裡的 mock 就是這樣寫的（tests/…:253）。也就是說這個佔位值現在離「真的送出跨 project 的資料」只差一個具體 handler 的實作。我在這個 checkout 裡找不到任何 feature flag、rollout gate 或 assert 擋住這條路。

**如何確認**：確認 Detector 加上真正 project_id 欄位的 migration 是否排在任何具體 StatefulDetectorHandler 實作之前；或指出目前有哪個 flag / kill switch 擋住 process_detectors 的 production 呼叫端。

#### Q-003 occurrence 在 `commit_state_updates()` 之前就送進 Kafka；若 commit 失敗，下一個 data packet 會不會重複產生同一個 occurrence？

面向 F 資料取用與資料庫

**背景**：process_detectors（src/sentry/workflow_engine/processors/detector.py:58-68）的順序是：evaluate → 逐筆 create_issue_occurrence_from_result（送 Kafka）→ 最後才 handler.commit_state_updates()。狀態沒寫成功而訊息已經送出時，dedupe_value 與 DetectorState 都不會前進，同一個 packet 重放會再送一次。這個順序在本次變更之前就存在（前一個 PR 已經讓 StatusChangeMessage 走同一條路），所以不算本次引入；但本次把 IssueOccurrence 也放上這條路之後，重複的後果從「重複送一次 resolve」變成「可能重複開 issue」。IssueOccurrence 的 fingerprint 由 build_fingerprint 決定且是穩定的，issue platform 端有可能靠 fingerprint 收斂，但我在這個 checkout 裡沒有追到能證實這一點的消費端程式碼，所以不敢當成結論。

**如何確認**：確認 issues 消費端（sentry.issues.occurrence_consumer / process_message）對相同 fingerprint 的重複 occurrence 是收斂成同一個 group 還是各自成案；或確認 workflow_engine 之後是否會把 Kafka 產出移到 commit 之後（或包進同一個 transaction.on_commit）。

#### Q-004 拿掉重複 group key 的偵測與那行 `logger.error("Duplicate detector state group keys found")`，是刻意把責任移交給 handler，還是型別改成 dict 之後的順手清理？如果是刻意的，這個新契約有打算寫下來嗎？

面向 I 回溯分析

**背景**：變更前 process_detectors 會走訪 list、發現重複的 group_key 就 log error 並跳過（連同測試 test_state_results_multi_group_dupe 一起在本次被刪除）。變更後 evaluate 回傳 dict，process_detectors 結構上看不到重複，那段程式碼確實已經沒有東西可以觸發——所以「檢查被拿掉了」這個說法本身是可以被反證的：在新契約下它是死碼。

真正剩下的是責任的轉移。去重現在發生在各 handler 建 dict 的那一刻，而且是無聲的：任何 `{r.group_key: r for r in ...}` 形式的實作都會讓後者覆蓋前者，沒有 log。DetectorHandler.evaluate 是 abstract method 且沒有 docstring（src/sentry/workflow_engine/processors/detector.py:125-129），所以「key 必須唯一、且必須等於 result.group_key」這個新要求目前沒有寫在任何地方。這是不是作者要的取捨，從程式碼讀不出來。

**如何確認**：作者說明重複 group key 現在算不算異常狀況：若算，該由誰偵測、在哪裡 log；若不算（就是讓 dict 自然收斂），把這一點寫進 DetectorHandler.evaluate 的 docstring 即可，正好和 F-001 的修復方向合併處理。

</details>
