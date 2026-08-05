## 審查結論：Approved with Comments

> Critical 0 · Suggestion 4 · Nit 3 · 未驗證提問 3
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
| ❌ | — | ❌ |

- **A 風格**（未通過）：AssignmentSource.queued 的預設值寫法讓欄位名稱與實際行為不符，見 F-001。其餘命名、type hint 與 docstring 一致，函式長度合理。
- **B 簡潔**（未通過）：tests/sentry/models/test_groupassignee.py 中「建 integration + ExternalIssue + GroupLink」的約 45 行前置設定，本次新增後成為第三份逐字複本，觸發 Rule of Three，見 F-006。
- **D API 慣例**（不適用）：本次 diff 沒有動到任何 HTTP endpoint、URL routing 或 request/response schema，七個檔案分別是 mixin、dataclass、celery task、util、model manager 與兩支測試，沒有 API 慣例可檢查。
- **F 資料取用與資料庫**（未通過）：AssignmentSource 跨 celery queue 邊界時的解碼失敗處理會靜默關閉迴圈保護，見 F-003。沒有 schema 變更、沒有新的多步驟寫入或 read-modify-write。
- **G 測試**（未通過）：新增測試的斷言強度不足（F-004）、命名有誤（F-005），且真正該被鎖住的兩個行為——「不同 integration 仍要往外傳」與「從 sync_group_assignee_inbound 這個實際入口進來時會被擋下」——都沒有測試（F-007）。
- **H 非 Python 檔**（不適用）：diff 內七個檔案全部是 .py，沒有前端元件、Dockerfile、nginx.conf、docker-compose 或 migration 可檢查。
- **I 回溯分析**（未通過）：should_sync 與 sync_assignee_outbound 的簽章變更對所有呼叫端與 override 都相容（已 grep 全 repo 確認），但 sync_status_outbound 的簽章多出一個沒有生產者也沒有消費者的參數，且與 ExampleIntegration 的實作不相容，見 F-002。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：assignee 方向的管線是完整的，但同一個 MR 也把 IssueSyncIntegration.sync_status_outbound 的簽章加上了 assignment_source（src/sentry/integrations/mixins/issues.py:411-418），而整個 repo 沒有任何呼叫端傳它、也沒有任何實作讀它（見 F-002）。status 方向要嘛不在本次範圍、該把這個參數拿掉，要嘛在範圍內、但只做了一半。這個取捨該由人決定，不是審查者。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且規則目錄 $NCR_OPENGREP_RULES 不存在，本次未執行 SAST 掃描。 |
| ruff | 已執行 | in_diff 0、outside_diff 206 |
| ty | 略過 | ty 未安裝（不在 PATH 上），本次未執行 Python 型別檢查；簽章相容性改以 grep 逐一列舉呼叫端與 override 來確認。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）；本次 diff 也沒有 JavaScript/TypeScript 檔案，即使安裝也無可掃描對象。 |
| codegraph | 略過 | codegraph 未安裝（不在 PATH 上），無法建立符號圖；Phase 3 的呼叫端列舉與完整性確認全部改用 grep 進行，已在各 finding 的 evidence 標明實際查到的位置。 |
| ncr-fresh-eyes (subagent) | 已執行 | 程序偏差，如實揭露：ncr-fresh-eyes 依 Phase 3 應在 dimension 清單之前執行，但本次審查環境沒有可派送 subagent 的工具（無 Agent / Task 工具），主審查者當下依規定跳過、未自行模擬。這一步事後由外部協調者派送並回傳，因此它是在九大面向審查完成之後才抵達的——順序與設計相反。減損的是它的獨立性保證：它本身確實沒有拿到檢查表、severity 詞彙、掃描摘要或既有 findings，但「先看、再套框架」的時間差已經不存在。回傳的 5 項觀察已逐一回到 checkout 驗證，全部落在既有 findings 之內（F-001、F-002、F-003、F-005、F-007），沒有產生任何新增 finding，也沒有推翻任何既有判斷；其中兩處 file:line 引用有誤，未採用（詳見 summary）。 · observations_received 5、adopted_as_new_finding 0、corroborated_existing_finding 5、citations_rejected 2 |
| ncr-quality-check (subagent) | 略過 | 同上，無法派送 subagent。其中不需要獨立視角的機械檢查已由主審查者自行執行並修正：report_model.py 驗證通過、每一條 evidence 的 file:line 已回到 checkout 逐一開啟核對（並已修正 F-004、F-005 兩處行號偏移）、每個 finding 都有 fix、發表文字中沒有審查機器上的絕對路徑、無針對個人的敘述。需要獨立閱讀者才有意義的那一項——「rationale 的每個事實斷言是否真的被所引的程式碼支持」——本次沒有第二雙眼睛做過。 |

<details>
<summary>Suggestion（4）</summary>

#### F-001 AssignmentSource.queued 的預設值在 module import 時就算完了，之後每個實例拿到的都是同一個時間戳 — `src/sentry/integrations/services/assignment_source.py:18`

面向 A 風格 · Suggestion

**問題**：`queued: datetime = timezone.now()` 是 dataclass 的 class-body 預設值，`timezone.now()` 在 class 定義（也就是 module 首次 import）當下就被呼叫一次，之後所有沒有明確帶 queued 的實例共用那一個值。from_integration（assignment_source.py:22-25）從不帶 queued，所以實務上每一個 AssignmentSource 的 queued 都是該 process import 這支 module 的時間，而不是這次指派被排入佇列的時間。這個值會經由 to_dict() 一路送進 celery kwargs（src/sentry/integrations/utils/sync.py:141），因此送出去的是一個看起來合理、實際上恆定的時間戳。已用一支等價的最小重現腳本確認：兩個相隔 1.2 秒建立的實例，queued 完全相等。同一個 repo 裡正確的寫法就在旁邊——src/sentry/models/groupassignee.py:263 的 `date_added = models.DateTimeField(default=timezone.now)` 傳的是 callable 而不是呼叫結果。之所以列為 Suggestion 而非 Critical，是因為 grep 全 repo 後確認目前沒有任何地方讀取 `.queued`（唯一提及它的是 tests/sentry/integrations/services/test_assignment_source.py:36 的 `is not None`），所以今天不會造成錯誤行為；但這個欄位一旦有第一個讀者（做 staleness 判斷、debug 追時序、或算 queue latency），拿到的會是無聲的錯誤值。附帶一提，在 class body 呼叫 `timezone.now()` 也讓這支 module 的 import 綁在 Django settings 已設定完成之後（目前的 import 路徑都在 app loading 之後，所以不會炸）。

**證據**：
- `src/sentry/integrations/services/assignment_source.py:18`
- `src/sentry/integrations/services/assignment_source.py:22-25`
- `src/sentry/integrations/utils/sync.py:141`
- `src/sentry/models/groupassignee.py:263`

**修復方向**：改成 default_factory，讓時間在建立實例時才取得：

```python
from dataclasses import asdict, dataclass, field

@dataclass(frozen=True)
class AssignmentSource:
    source_name: str
    integration_id: int
    queued: datetime = field(default_factory=timezone.now)
```

如果 queued 目前其實沒有預期的讀者，另一個更乾脆的選項是先把欄位拿掉，等真的需要時再連同用途一起加回來。

#### F-002 sync_status_outbound 新增的 assignment_source 參數沒有生產者也沒有消費者，且與 ExampleIntegration 的實作不相容 — `src/sentry/integrations/mixins/issues.py:410-422`

面向 I 回溯分析 · Suggestion

**問題**：抽象方法 IssueSyncIntegration.sync_status_outbound 多了 `assignment_source: AssignmentSource | None = None`，但已對全 repo grep 過 `sync_status_outbound`：唯一的呼叫端 src/sentry/integrations/tasks/sync_status_outbound.py:44-46 只傳三個位置參數，且該 task 本身也沒有接收 assignment_source 的入口；四個實作（jira、jira_server、vsts、example）沒有一個讀它。也就是說這個參數目前既無生產者也無消費者。同時，ExampleIntegration.sync_status_outbound 的簽章是 `(self, external_issue, is_resolved, project_id)`，連 `**kwargs` 都沒有（其餘三個實作靠 `**kwargs` 才吞得下去），所以抽象契約宣告的參數，這個實作結構上收不到。今天不會壞，因為沒有人傳；但這正是它的問題——契約寫了一件實作做不到的事，第一個照著簽章傳參數的人才會發現。與 assignee 方向對照更明顯：assignee 那邊 abstract 簽章反而沒有具名 assignment_source（issues.py:396-403），呼叫端卻真的傳了（tasks/sync_assignee_outbound.py:59-61），靠 `**kwargs` 接住後同樣沒人使用。兩個方向的處理方式剛好相反，讀的人無從判斷哪一個是刻意的。

**證據**：
- `src/sentry/integrations/mixins/issues.py:410-422`
- `src/sentry/integrations/tasks/sync_status_outbound.py:43-46`
- `src/sentry/integrations/example/integration.py:161`
- `src/sentry/integrations/jira/integration.py:990`
- `src/sentry/integrations/jira_server/integration.py:1090`
- `src/sentry/integrations/vsts/issues.py:266-267`

**修復方向**：二選一，並在 MR 描述裡說明選了哪一個：（a）status 方向不在本次範圍——把 issues.py:411-418 的 assignment_source 參數移除，等實際接線時再加；（b）status 方向在範圍內——把 sync_status_outbound task 的簽章補上 `assignment_source_dict`，由 kick_off_status_syncs / status 變更的來源一路帶下來，並在 tasks/sync_status_outbound.py:43 改成 `should_sync("outbound_status", parsed_assignment_source)`，同時替 ExampleIntegration.sync_status_outbound 補上 `**kwargs` 或具名參數。另外建議順手把 assignee 方向對齊：既然 tasks/sync_assignee_outbound.py:60 已經具名傳 `assignment_source=`，abstract 簽章（issues.py:396-403）就該把它寫進去，而不是留給 `**kwargs`。

#### F-003 from_dict 解析失敗時回傳 None，等於無聲關閉迴圈保護，且沒有留下任何 log 或 metric — `src/sentry/integrations/services/assignment_source.py:30-35`

面向 F 資料取用與資料庫 · Suggestion

**問題**：`from_dict` 用 `cls(**input_dict)` 包在 `except (ValueError, TypeError): return None` 裡。呼叫端 tasks/sync_assignee_outbound.py:53-55 拿到 None 之後，直接把它當成「這次指派沒有來源」傳進 should_sync；而 issues.py:390 的 guard 是 `if sync_source and ...`，None 會讓整個 guard 跳過，outbound sync 照常送出。換句話說，解析失敗的後果不是 fail loud，而是這次的迴圈保護悄悄消失，且沒有 logger、沒有 metrics.incr，事後完全無從得知發生過。這個失敗路徑不是假想的：source_name 與 integration_id 都沒有預設值，`cls(**input_dict)` 對缺欄位、多欄位都會丟 TypeError，而 celery kwargs 是在 enqueue 當下就序列化好的——rolling deploy 期間，新版本送出的 payload 會被舊 worker 取走、舊 payload 也會被新 worker 取走，任何一次 dataclass 欄位增減都落在這個窗口裡。作者會寫這個 try/except，本身就說明這個情境被預期到了；問題在於它選擇的失敗模式是「靜默降級成沒有保護」。另外 dataclass 不做型別驗證，`{"source_name": "x", "integration_id": "123"}` 會成功建立實例，但 issues.py:390 的 `"123" == 123` 恆為 False，同樣是保護失效卻回報成功。

**證據**：
- `src/sentry/integrations/services/assignment_source.py:30-35`
- `src/sentry/integrations/tasks/sync_assignee_outbound.py:53-56`
- `src/sentry/integrations/mixins/issues.py:390-391`

**修復方向**：至少讓失敗可見，並把型別收斂：

```python
logger = logging.getLogger(__name__)

@classmethod
def from_dict(cls, input_dict: dict[str, Any]) -> AssignmentSource | None:
    try:
        source = cls(**input_dict)
    except (ValueError, TypeError):
        logger.warning(
            "assignment_source.parse_failed", extra={"keys": sorted(input_dict)}
        )
        return None
    if not isinstance(source.integration_id, int):
        logger.warning("assignment_source.bad_integration_id", extra={"keys": sorted(input_dict)})
        return None
    return source
```

若要進一步縮小 rolling deploy 的窗口，可以在 `cls(**input_dict)` 之前先過濾掉未知 key（`{k: v for k, v in input_dict.items() if k in {f.name for f in fields(cls)}}`），讓新增欄位不會讓舊 worker 整批失去保護。

#### F-007 迴圈保護只測了「同一個 integration 會被擋下」，沒有測「不同 integration 仍要往外傳」，也沒有從實際入口 sync_group_assignee_inbound 測過 — `tests/sentry/models/test_groupassignee.py:179-236`

面向 G 測試 · Suggestion

**問題**：已對 tests/ 全目錄 grep `AssignmentSource|assignment_source`，只命中兩支檔案並逐行讀過，所以以下缺口是確認過的而非推測。（1）issues.py:387-389 的註解明說 guard「should still allow other integrations to propagate changes outward」，但沒有任何測試涵蓋這一半：只要有人把 issues.py:390 的條件寫錯成「只要有 sync_source 就擋」，現有測試全數仍會通過，而所有跨系統傳播會整批消失。這正是最需要被鎖住、也最容易無聲壞掉的那一半。（2）新測試是在 GroupAssignee.objects.assign(assignment_source=...) 這一層驗證的，但真正會產生 assignment_source 的入口是 sync_group_assignee_inbound（utils/sync.py:96-116）；既有的 test_assignee_sync_inbound_assign / test_assignee_sync_inbound_deassign（test_groupassignee.py:289-388）沒有 mock sync_assignee_outbound，也沒有斷言 outbound 有沒有被觸發，所以「webhook 進來 → 不再回送同一個 integration」這條端到端行為目前沒有任何測試守著。（3）deassign 方向的 guard（groupassignee.py:238-240）同樣沒有對應的 matching-source 測試。

**證據**：
- `tests/sentry/models/test_groupassignee.py:179-236`
- `src/sentry/integrations/mixins/issues.py:387-391`
- `src/sentry/integrations/utils/sync.py:96-116`
- `tests/sentry/models/test_groupassignee.py:289-388`

**修復方向**：補三個測試：（a）建立兩個 provider 不同的 integration，各自 link 到同一個 group，帶 A 的 assignment_source 呼叫 assign，斷言 A 的 sync_assignee_outbound 沒被呼叫、B 的有被呼叫；（b）把 test_assignee_sync_outbound_unassign 複製一份改成帶 matching source 的 deassign，斷言 assert_not_called()；（c）在既有的 test_assignee_sync_inbound_assign 上加 `@mock.patch.object(ExampleIntegration, "sync_assignee_outbound")`，用 self.tasks() 包住 sync_group_assignee_inbound，斷言 outbound 沒被觸發——這一條是本次 MR 真正要修的行為，值得用端到端的形式鎖住。

</details>

<details>
<summary>Nit（3）</summary>

#### F-004 test_to_dict 對 queued 只斷言 is not None，剛好放過了 F-001 的預設值缺陷 — `tests/sentry/integrations/services/test_assignment_source.py:29-38`

面向 G 測試 · Nit

**問題**：test_to_dict（29-38 行）中第 36 行的 `assert result.get("queued") is not None` 屬於「只確認有東西回來」而不是「確認行為正確」的斷言：不論 queued 是本次建立的時間、module import 時間，還是一個固定常數，它都會通過。這支測試是唯一碰到 queued 的地方，而 F-001 的缺陷正好落在它的盲區裡——如果它斷言的是「兩個先後建立的實例 queued 不相等」或「queued 落在測試開始之後」，這個缺陷在 PR 送出前就會被擋下。

**證據**：
- `tests/sentry/integrations/services/test_assignment_source.py:29-38`
- `tests/sentry/integrations/services/test_assignment_source.py:36`
- `src/sentry/integrations/services/assignment_source.py:18`

**修復方向**：把斷言改成能區分正確與錯誤實作的形式，例如：

```python
def test_queued_is_per_instance(self):
    before = timezone.now()
    first = AssignmentSource(source_name="foo", integration_id=1)
    second = AssignmentSource(source_name="bar", integration_id=2)
    assert first.queued >= before
    assert first.queued != second.queued
```

（此測試會在 F-001 修好之前失敗，正是它該有的效果。）

#### F-005 兩個測試方法的名稱與實際內容不符：拼字錯誤與「array」用詞 — `tests/sentry/integrations/services/test_assignment_source.py:13`

面向 G 測試 · Nit

**問題**：第 13 行的 `test_from_dict_inalid_data` 少了一個 v，應為 `invalid`；第 8 行的 `test_from_dict_empty_array` 測的輸入是第 9 行的 `data: dict[str, Any] = {}`，是空 dict 不是 array。測試名稱是後續 debug 時第一個被讀到的東西，名稱與內容不符會讓人往錯的方向找。

**證據**：
- `tests/sentry/integrations/services/test_assignment_source.py:13`
- `tests/sentry/integrations/services/test_assignment_source.py:8-9`

**修復方向**：更名為 `test_from_dict_invalid_data` 與 `test_from_dict_empty_dict`。

#### F-006 測試前置設定出現第三份逐字複本，觸發 Rule of Three — `tests/sentry/models/test_groupassignee.py:120-148`

面向 B 簡潔 · Nit

**問題**：「create_integration（含五個 sync config key）+ ExternalIssue.objects.create + GroupLink.objects.create」這段約 30-45 行的前置設定，在本次新增 test_assignee_sync_outbound_assign_with_matching_source_integration 之後成為第三份逐字相同的複本（同檔案還有第四、第五份在 inbound 測試中，第 294-319 與 351-376 行）。Rule of Three 說第三次出現才抽出，這次剛好踩到；同時它也是一個實質風險：五個 config key 的字典必須在每一份複本裡保持一致，之後任何一份漏改都會讓對應測試變成 vacuous pass。

**證據**：
- `tests/sentry/models/test_groupassignee.py:120-148`
- `tests/sentry/models/test_groupassignee.py:179-209`
- `tests/sentry/models/test_groupassignee.py:238-267`

**修復方向**：在 GroupAssigneeTestCase 上抽一個 helper，例如：

```python
def _link_example_integration(self, group, external_key="APP-123"):
    integration = self.create_integration(
        organization=group.organization,
        external_id="123456",
        provider="example",
        oi_params={"config": {k: True for k in (
            "sync_comments", "sync_status_outbound", "sync_status_inbound",
            "sync_assignee_outbound", "sync_assignee_inbound",
        )}},
    )
    external_issue = ExternalIssue.objects.create(
        organization_id=group.organization.id,
        integration_id=integration.id,
        key=external_key,
    )
    GroupLink.objects.create(
        group_id=group.id,
        project_id=group.project_id,
        linked_type=GroupLink.LinkedType.issue,
        linked_id=external_issue.id,
        relationship=GroupLink.Relationship.references,
    )
    return integration, external_issue
```

本次至少讓新增的那一份改用 helper，既有的可以之後再逐步收斂。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 這個 guard 只擋得住「同一個 integration 的單跳回彈」。當一個 organization 有兩個以上的 issue-sync integration 同時 link 到同一個 group 時，A → Sentry → B → Sentry → A 的來回是否也會被擋下？如果不會，實際觸發這次修復的那個迴圈屬於哪一種？

面向 E 架構

**背景**：issues.py:387-389 的註解刻意保留「其他 integration 仍要往外傳」的行為，因此 A 的 webhook 進來時仍會往 B 送，B 的 webhook 回來時 sync_source 是 B、不等於 A，於是又往 A 送。這條來回是否終止，取決於 GroupAssignee.assign 在指派對象沒有變化時會不會 no-op——groupassignee.py:160-168 的 `.exclude(**{assignee_type_attr: assigned_to_id}).update(...)` 在指派未變時回傳 0，affected 為 False，確實不會再往外送。所以在「每一輪都解析到同一個 Sentry user」的前提下會收斂。但這個前提取決於各 provider 的 email → user 對應（utils/sync.py:103 的 get_many_by_email + get_user_id）是否穩定，我無法從這份 checkout 判定；也因此無法反推原本被觀察到的迴圈到底是哪一條路徑，以及這個 guard 是否命中它。這一項刻意不給 severity。

**如何確認**：MR 描述或 issue 裡對原始迴圈的實際重現步驟（哪兩個系統、哪個欄位在來回震盪）；或是一個雙 integration 的端到端測試，跑滿數輪後斷言 outbound 呼叫次數收斂。

#### Q-002 在 rolling deploy 期間，新版程式送出的 sync_assignee_outbound 任務多帶了 assignment_source_dict 這個 kwarg，被尚未更新的 worker 取走時會直接 TypeError；現有的 retry 設定是否足以撐過部署窗口？

面向 F 資料取用與資料庫

**背景**：src/sentry/integrations/tasks/sync_assignee_outbound.py:30-35 新增了帶預設值的 assignment_source_dict，而 celery kwargs 在 enqueue 當下（utils/sync.py:136-145）就已序列化。舊 worker 的函式簽章不接受這個 kwarg，會在呼叫時丟 TypeError。該 task 的 @retry 排除清單（同檔 22-29 行）不含 TypeError，所以會進入重試：max_retries=5、default_retry_delay=300，約 25 分鐘的容忍窗口。這個窗口是否涵蓋 Sentry 實際的部署時間、以及 web 與 worker 的部署先後順序，都不是這份 checkout 能回答的；repo 內也沒有找到關於 task kwarg 演進的成文慣例可以比對。這一項刻意不給 severity。

**如何確認**：團隊對 celery task 新增 kwarg 的既有慣例（是否要求先部署 worker、或先加一輪只接不送的版本），或部署流程中 worker 與 web 的先後順序與實際耗時。

#### Q-003 AssignmentSource 放在 src/sentry/integrations/services/ 是否是它該在的位置？

面向 E 架構

**背景**：該目錄其餘內容是 hybrid-cloud 的 RPC service 層（例如 sentry.integrations.services.integration 提供 integration_service 與 RpcIntegration），而 AssignmentSource 是一個沒有 RPC 語意的純 frozen dataclass，並且被 src/sentry/models/groupassignee.py:15 這個 region silo model 在 module 層級直接 import。放在 services/ 之下可能會讓後續維護者預期它遵循 RPC model 的序列化與版本慣例（例如以 pydantic RpcModel 為基底），而它並不是。這比較像是命名與歸屬的取捨而非缺陷，我沒有找到能判定對錯的成文規則，所以不給 severity。

**如何確認**：ecosystem team 對 integrations/services/ 這個 namespace 的定義；或 repo 內是否已有非 RPC 的純資料型別放在同一層的先例。

</details>
