## 審查結論：Request Changes

> Critical 3 · Suggestion 5 · Nit 2 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | — | ❌ |

- **A 風格**（未通過）：註解與 docstring 對程式行為做出不成立的斷言（F-008）；行尾空白與檔尾空行會讓專案 pre-commit（black + flake8）失敗（F-009）。
- **B 簡潔**（未通過）：OptimizedCursorPaginator.get_result 約 60 行逐字複製 BasePaginator.get_result，實際差異只有一條分支（F-007）。
- **C 安全**（未通過）：enable_advanced 被註解描述成管理員檢查，但兩個條件都不是（F-004）。已逐一確認這不構成跨 organization 的資料外洩：queryset 在 endpoint:48 已被 organization_id 綁死，且 OrganizationAuditPermission 要求 org:write。
- **D API 慣例**（未通過）：新增的 optimized_pagination query param 繞過既有的 AuditLogQueryParamSerializer，且大小寫敏感比對會靜默失效（F-010）。URL 命名、HTTP verb 語意、PII in URL 三項無問題。
- **E 架構**（未通過）：OptimizedCursorPaginator 繼承錯誤的基底語意——用整數 key 的 get_item_key 掛在 DateTimeField 上（F-001）。endpoint 本身仍夠薄，沒有商業邏輯下沉問題。
- **F 資料取用與資料庫**（未通過）：負 offset 直接交給 Django QuerySet 切片，會拋 ValueError 而非被「自動處理」（F-003）。SQL 皆為參數化（paginator.py:112-127 的 .extra 有帶 params），無 injection 風險。
- **G 測試**（未通過）：新增 paginator class、新增對外查詢參數、變更共用基底行為，三件事都沒有任何測試（F-006）。
- **H 非 Python 檔**（不適用）：本次 diff 只有三個 .py 檔（organization_auditlogs.py、paginator.py、cursors.py），沒有前端、Dockerfile、nginx、docker-compose 或 migration 檔。
- **I 回溯分析**（未通過）：既有函式被改動的兩處都有回溯問題：organization_auditlogs.get() 新增了對可為 None 的 member 的無條件解參照（F-002）；BasePaginator.get_result 的行為變更會傳播到所有繼承者（F-005）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該不該做？**：PR 標題與 commit message 都主張這是為了 high-volume audit log 的效能，但 diff 內沒有 benchmark、profiling、issue 連結或 EXPLAIN 佐證。現有的 DateTimePaginator 已經是 keyset 分頁（src/sentry/api/paginator.py:79-127 以 `datetime <= %s` 收斂範圍），新增的路徑反而多了一條純 offset 分支，在大 offset 情境只會更慢。要不要做這件事，需要先有「原本慢在哪」的證據。
- **該在這個 MR 做？**：這個 MR 同時改了三件不同影響範圍的事：audit log endpoint 的新參數（單一 endpoint）、BasePaginator.get_result 的 offset 處理（全站至少 15 個檔案使用的 Paginator/DateTimePaginator 共同基底）、以及 sentry/utils/cursors.py 的註解。第二項是跨全站的行為變更，應該獨立成一個 MR 並附自己的測試。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | total 0、in_diff 0、pre_existing 0 |
| git diff --check | 已執行 | trailing_whitespace 5、blank_line_at_eof 1、in_diff 6 |
| trivy | 略過 | 未安裝（preflight 回報 trivy 不在 PATH）。相依套件漏洞、設定錯誤與 secret 掃描本次未執行。 |
| opengrep | 略過 | 未安裝，且 NCR_OPENGREP_RULES 指向的預設規則目錄也不存在（兩項皆缺）。SAST 掃描本次未執行。 |
| ty | 略過 | 未安裝，Python 型別檢查未執行。替代作法：改以專案自己的 mypy 設定（pyproject.toml:46-166）判讀型別風險，見 F-002。 |
| oxlint | 略過 | 未安裝；且本次 diff 只有 .py 檔，本來就沒有 JS/TS 可掃。 |
| codegraph | 略過 | 未安裝，無法建立 symbol graph。導覽全程改用 grep（BasePaginator 子類別、DateTimePaginator 使用者、determine_access/from_rpc_auth 呼叫鏈皆以 grep 逐一確認）。 |
| ncr-fresh-eyes (subagent) | 已執行 | 流程偏差，據實揭露：本 agent 的執行環境沒有可派送 subagent 的工具，ncr-fresh-eyes 改由 orchestrator 於外部派送，且是在本報告第一版寫完之後才送達——也就是說它抵達的順序晚於 Phase 3 step 1 應有的位置。其 prompt 未攜帶九大面向清單、severity 詞彙、掃描摘要或本報告的任何內容，這一點維持原設計。回傳的 4 項主要觀察與 3 項次要觀察已全部逐條對照程式碼複驗：沒有一項構成新的 finding（皆已涵蓋於 F-003/F-005/F-006/F-008/F-009 與 Q-001），其中 2 項為既有 finding 補上了新證據（F-004、F-005），1 項的歸因被否決（見 F-005）。 · observations 4、minor 3、adopted_as_new_finding 0、strengthened_existing 2、rejected 1 |
| ncr-quality-check (subagent) | 略過 | 環境無 subagent 派送工具，Phase 4 step 3 的品質複查未執行，本報告未經第三方 agent 覆核。（ncr-fresh-eyes 已由 orchestrator 於外部補做，見上一列；quality-check 沒有比照辦理。） |

### Critical

#### F-001 OptimizedCursorPaginator 用整數 key 的 get_item_key 掛在 -datetime 上，只要有資料就必定 500 — `src/sentry/api/paginator.py:838-840`

面向 E 架構 · Critical

**問題**：get_item_key 逐字抄自 Paginator（paginator.py:221-227），對 key 值做 math.floor / math.ceil。但 endpoint 傳進來的是 order_by="-datetime"，self.key 是 AuditLogEntry.datetime（DateTimeField），getattr 拿到的是 datetime 物件。實測 math.ceil(datetime.datetime.now()) → TypeError: must be real number, not datetime.datetime。這個 key 一定會被呼叫：build_cursor → _build_next_values 在 cursor.value 為 0（首次請求）且有結果時執行 key(results[0])（src/sentry/utils/cursors.py:121-122）。DateTimePaginator（paginator.py:230-240）存在的唯一理由就是這個轉換，新類別卻繼承 BasePaginator 而沒有補上。反證檢查：(1) 確認 OptimizedCursorPaginator 內沒有第二個 get_item_key 覆寫；(2) 確認 endpoint 沒有另外傳 key/multiplier；(3) 確認 self.paginate 的 paginator_kwargs 原樣傳給建構子（src/sentry/api/base.py:533、src/sentry/utils/pagination_factory.py:56-66），沒有中間層修正。唯一不會爆的情況是查詢結果為空（cursors.py:197 的 `if results else 0`），也就是說這條路只在「該組織一筆 audit log 都沒有」時才會回 200。value_from_cursor 直接回傳 cursor.value（整數）也是同一個錯誤的另一面：第二頁會把整數當成 timestamp 丟進 `datetime <= %s`（paginator.py:112-127 的 .extra where 條件）。

**證據**：
- `src/sentry/api/paginator.py:838-840`
- `src/sentry/api/paginator.py:842-843`
- `src/sentry/api/endpoints/organization_auditlogs.py:76-83`

**修復方向**：讓 OptimizedCursorPaginator 繼承 DateTimePaginator（或至少複用它的 get_item_key / value_from_cursor），並補一個「組織有 audit log + optimized_pagination=true → 200 且順序正確」的測試；這個測試會直接釘住本條。

#### F-002 organization_context.member 可能為 None，第 71 行無條件解參照，且這行在所有請求上都會執行 — `src/sentry/api/endpoints/organization_auditlogs.py:71`

面向 I 回溯分析 · Critical

**問題**：RpcUserOrganizationContext.member 的型別是 RpcOrganizationMember | None，欄位註解明寫「member can be None when the given user_id does not have membership with the given organization」（model.py:344-345）。第 71 行寫在 `if use_optimized` 之外，所以連原本的 DateTimePaginator 路徑也會先跑到它——這不是新功能才有的問題，是既有路徑的 regression。可達路徑（逐段確認）：org auth token 認證時 request.user 被設為 AnonymousUser（src/sentry/api/authentication.py:492-498 傳 user=None，transform_auth 於 :167-168 換成 AnonymousUser）；determine_access 因為 request.auth 存在且 user 未認證而走 from_rpc_auth（src/sentry/api/permissions.py:170-180）；from_rpc_auth 對同組織的 token 直接授予 settings.SENTRY_SCOPES（access.py:1183-1194），其中含 org:write，於是 OrganizationAuditPermission（src/sentry/api/bases/organization.py:110-111）通過；而 convert_args 以 request.user.id（AnonymousUser 為 None）取得 context（organization.py:282-291），member 必為 None → AttributeError → 未處理例外 → 500。反證檢查：唯一能短路的是 `request.user.is_superuser`，AnonymousUser 的 is_superuser 是 False，擋不住；active superuser 反而沒事，因為 RpcUser.is_superuser 為 True 會短路（src/sentry/users/services/user/model.py:48）。另一條獨立證據：專案的 mypy 對 union-attr 只在 pyproject.toml:113-166 的模組清單內停用，清單裡有 sentry.api.paginator 但沒有 sentry.api.endpoints.organization_auditlogs，而 mypy 的 files = ["."]（pyproject.toml:54），所以這一行在 CI 就會直接報 union-attr。

**證據**：
- `src/sentry/api/endpoints/organization_auditlogs.py:71`
- `src/sentry/organizations/services/organization/model.py:344-346`
- `src/sentry/auth/access.py:1178-1194`
- `pyproject.toml:113-166`

**修復方向**：把這行改成 `enable_advanced = request.user.is_superuser or bool(organization_context.member and organization_context.member.has_global_access)`，並移進 `if request.GET.get("optimized_pagination") == "true":` 之內，讓非 optimized 路徑完全不受影響；同時補一個以 org auth token 呼叫此 endpoint 的 regression test。

#### F-003 負 offset 直接丟進 QuerySet 切片：Django 會拋 ValueError，不是註解說的「自動處理」 — `src/sentry/api/paginator.py:874-882`

面向 F 資料取用與資料庫 · Critical

**問題**：Django 的 QuerySet.__getitem__ 對負的 start 或 stop 一律 `raise ValueError("Negative indexing is not supported.")`（本專案鎖定 django==5.2.1，requirements-frozen.txt:36）。Endpoint.paginate 只攔 BadPaginationError（src/sentry/api/base.py:540-541），ValueError 會一路冒到 500。觸發成本極低：Cursor.from_string 對 offset 沒有任何下限檢查（cursors.py:51-60），對這個 audit logs endpoint 送出 query string `?optimized_pagination=true&cursor=0:-5:0` 就會走進 paginator.py:877 的分支。同一份檔案裡 codebase 自己的立場是相反的——OffsetPaginator 於 :287、GenericOffsetPaginator 於 :351、:701 都明確 `raise BadPaginationError("Pagination offset cannot be negative")`，而 tests/sentry/api/test_paginator.py:143-152 正是在釘這個行為。這個 PR 等於在另一條路上把既有約定反過來，卻沒有討論。反證檢查：(1) 確認 build_queryset 回傳的仍是 QuerySet（.extra() 不改型別，paginator.py:112-127），沒有支援負切片的 wrapper；(2) 確認這不是越權讀取——queryset 在 organization_auditlogs.py:48 已被 organization_id 綁死，就算 Django 允許負切片也只會落在同一個組織的資料範圍內，所以這是可用性缺陷而非資料外洩；(3) 確認 stop 為正也救不了，Django 只要 start < 0 就拒絕。

**證據**：
- `src/sentry/api/paginator.py:874-882`
- `src/sentry/api/paginator.py:826`
- `src/sentry/utils/cursors.py:51-60`
- `src/sentry/api/paginator.py:287`

**修復方向**：移除 paginator.py:874-882 這條分支。若真的需要反向翻頁，用 codebase 既有的 cursor.is_prev 機制；若要防禦性處理，在 paginator 入口把 offset < 0 明確轉成 BadPaginationError（會被 base.py:540 轉成 400），與 OffsetPaginator 的既有做法一致，而不是讓它掉進 ORM。

<details>
<summary>Suggestion（5）</summary>

#### F-004 enable_advanced 不是它註解宣稱的「authorized administrators」判定，實務上幾乎恆真 — `src/sentry/api/endpoints/organization_auditlogs.py:69`

面向 C 安全 · Suggestion

**問題**：兩個條件都不是管理員判定。(1) organization_context.member.has_global_access 讀到的是 OrganizationMember 的原始 model 欄位——serialize_member 於 serial.py:47 原樣複製進 RPC 物件，而該欄位的 default 是 True（organizationmember.py:216）。全 repo 搜尋 `has_global_access=False` / `has_global_access = False` 沒有任何命中，也就是說沒有程式會把它翻成 False，實務上對每個 member 都是 True。這與 codebase 真正用來做授權判斷的那個 has_global_access 不是同一個東西：後者是 Access 上的計算屬性（auth/access.py:611-613 的 `bool(member.organization.flags.allow_joinleave) or roles.get(member.role).is_global`，以及 access.py:421-426 的同名屬性），會把組織的 allow_joinleave 旗標與角色一起算進去。這裡用錯了那一個。(2) request.user.is_superuser 讀的是 User 資料表旗標，不是 sentry 的 is_active_superuser(request)——同一個 endpoint 的權限類別 OrganizationAuditPermission 兩行之外用的正是後者（organization.py:122-123），sentry 刻意區分「帳號是 superuser」與「目前處於已提權的 superuser session」。合起來，這個 gate 對任何能走到這個 handler 的人幾乎恆為 True，`optimized_pagination=true` 實際上就等於直接切換 paginator。為什麼仍是 Suggestion 而不是 Critical——這裡把兩個問題分開，兩者可以同時成立：「誰通得過這個 gate」的答案是「幾乎所有走到這裡的人」，這一點成立；但「通過之後多拿到什麼」的答案是「什麼都沒有多拿到」。真正的 gate 在上游而且不在本 diff 的變更範圍內：OrganizationAuditPermission 要求 org:write（organization.py:110-111），在 enable_advanced 被計算之前就已經執行；而 queryset 在 organization_auditlogs.py:48 已被 organization_id 綁死。所以能通過 enable_advanced 的人，本來就有權讀到這個 paginator 能回傳的每一列。缺陷在於「宣稱的存取控制與實際條件不符」，不在於實際擴大了任何人看得到的資料。

**證據**：
- `src/sentry/api/endpoints/organization_auditlogs.py:69`
- `src/sentry/api/endpoints/organization_auditlogs.py:71`
- `src/sentry/models/organizationmember.py:216`
- `src/sentry/organizations/services/organization/serial.py:47`
- `src/sentry/auth/access.py:611-613`
- `src/sentry/api/bases/organization.py:122-123`

**修復方向**：若目的是漸進式開關，用 feature flag（sentry 已有 features.has(...)）而不是權限旗標；若目的真的是限管理員，改用 is_active_superuser(request) 搭配明確的 scope 檢查（例如 request.access.has_scope("org:admin")），或至少改讀 Access 上的計算屬性而不是 RPC member 的原始欄位；並把註解改寫成實際成立的條件。

#### F-005 改動 BasePaginator.get_result 會影響所有繼承者，且只修了一半：is_prev 路徑仍把負 offset 原樣送進切片 — `src/sentry/api/paginator.py:179-184`

面向 I 回溯分析 · Suggestion

**問題**：BasePaginator.get_result 是全站分頁的共同實作：src/sentry 內有 131 處 self.paginate( 呼叫（114 個檔案），其中 82 處有指定 paginator_cls、其餘採用預設值 Paginator；扣掉 paginator.py 內另外 9 個自行覆寫 get_result 的類別後，約 60 處呼叫最終會落在這個方法上。改這裡等於一次改掉全站分頁行為。

這條的關鍵是歸因，已對 merge base 逐行核對過：74618671bff8 版本的這一行是 `results = list(queryset[offset:stop])`，offset 在 is_prev 與非 is_prev 兩條路上都沒有 clamp。也就是說「負 offset 會炸」是本 PR 之前就存在的問題，不是這次引入的；本 PR 把非 is_prev 那一半 clamp 掉，反而是縮小了受影響面。依 references/scanners.md 的 diff attribution 規則，既有問題不計在這位作者頭上，所以這條是 Suggestion 而不是 Critical。

真正屬於本 PR 的問題是這個修正只做了一半，而且沒說：(a) cursor.is_prev 的分支仍原樣把 client 提供的負 offset 送進 queryset 切片（Cursor.from_string 於 cursors.py:51-60 對 offset 完全沒有正負檢查，get_cursor_from_request/paginate 於 base.py:505-541 也沒有），所以 `?cursor=<value>:-100:1` 這種 prev cursor 在任何走 BasePaginator 的 endpoint 上依然是未處理的 ValueError；(b) 下方 :191 的 `elif len(results) == offset + limit + extra` 仍用未 clamp 的 offset，與上方的 start_offset 不同步；(c) 把無效輸入從「明確報錯」改成「靜默當成第一頁」，之後從 log 追不回來，而 codebase 其他 paginator 的既有選擇是明確報 400（paginator.py:287、:351、:701）。本 diff 沒有動 tests/sentry/api/test_paginator.py，既有的 test_negative_offset（test_paginator.py:143-152）測的是 OffsetPaginator，不受影響，所以不會有任何測試失敗來提醒這件事。

**證據**：
- `src/sentry/api/paginator.py:179-184`
- `src/sentry/api/paginator.py:186-192`
- `src/sentry/api/paginator.py:221-240`
- `src/sentry/utils/cursors.py:51-60`
- `src/sentry/api/base.py:505-541`

**修復方向**：把這段從本 PR 拆出去獨立處理。若要修，就一次修完：在 get_result 入口對 offset < 0 拋 BadPaginationError（涵蓋 is_prev 兩條路，會被 base.py:540 轉成 400），或更上游在 Cursor.from_string 就拒絕負 offset；並在 tests/sentry/api/test_paginator.py 的 PaginatorTest / DateTimePaginatorTest 補上 prev cursor 帶負 offset 的測試。

#### F-006 新 paginator、新查詢參數、共用基底行為變更，三者都沒有任何測試 — `tests/sentry/api/test_paginator.py:1`

面向 G 測試 · Suggestion

**問題**：diff 只動 src/ 下的三個檔案，tests/sentry/api/test_paginator.py 與 tests/sentry/api/endpoints/test_organization_auditlogs.py 都沒有跟著改。而現有的 endpoint 測試全部以 self.login_as(self.user) 進行（test_organization_auditlogs.py:21），這個 user 是 org owner、member 必定存在，所以 F-002 的無 membership 路徑完全沒有覆蓋；也沒有任何測試會帶 optimized_pagination=true，所以 F-001 那個必然的 TypeError 在整個測試套件裡是隱形的。這正是「測試沒寫」的具體代價：三個 Critical 之中有兩個，只要有一個最基本的 happy-path 測試就會當場現形。

**證據**：
- `tests/sentry/api/test_paginator.py:1`
- `tests/sentry/api/endpoints/test_organization_auditlogs.py:21`

**修復方向**：至少補三個測試：(a) 組織有 audit log + optimized_pagination=true → 200 且 rows 順序與 DateTimePaginator 一致；(b) cursor=0:-5:0 → 400 而非 500；(c) 以 org auth token 呼叫（無 membership）→ 不會 500。前兩個放 tests/sentry/api/test_paginator.py，第三個放 endpoint 測試。

#### F-007 OptimizedCursorPaginator.get_result 幾乎整段複製 BasePaginator.get_result — `src/sentry/api/paginator.py:845-912`

面向 B 簡潔 · Suggestion

**問題**：約 60 行與基底類別逐字重複：cursor 預設值、limit clamp、build_queryset、hits 三分支、extra 計算、is_prev 的結果修剪與 reverse、build_cursor 呼叫、post_query_filter。真正不同的只有 :877-882 那條負 offset 分支。這代表基底類別之後修的任何 bug 都不會傳到這裡——而這件事其實已經發生了：F-001 的 get_item_key 就是抄錯來源（抄了 Paginator 而不是 DateTimePaginator）留下的。

**證據**：
- `src/sentry/api/paginator.py:845-912`
- `src/sentry/api/paginator.py:136-215`

**修復方向**：只覆寫真正需要不同的部分，其餘走 super().get_result(...)；若切片邏輯必須可替換，在 BasePaginator 抽一個 _slice(queryset, offset, limit, extra) 之類的 hook 讓子類別覆寫，而不是整段複製。

#### F-008 註解與 docstring 對安全性與 ORM 行為下了與程式不符的結論 — `src/sentry/api/paginator.py:179-181`

面向 A 風格 · Suggestion

**問題**：五處敘述逐一對照都不成立。(1)「This is safe because the underlying queryset will handle boundary conditions」（paginator.py:181）與「The underlying Django ORM properly handles negative slicing automatically」（paginator.py:876）和 Django 的實際行為相反，見 F-003。(2)「This is safe because permissions are checked at the queryset level」（paginator.py:879）把「查詢被 organization_id 限縮」講成權限保證——那是資料範圍，不是授權檢查。(3)「Enable advanced pagination features for authorized administrators」（organization_auditlogs.py:69）與實際判定條件不符，見 F-004。(4) cursors.py:26-27 說 Cursor 為此放寬了負 offset，但底下的 `self.offset = int(offset)` 一個字元都沒改，Cursor 本來就沒有限制過——這兩行是描述一個不存在的變更。(5) docstring（paginator.py:823-829）列的三項「advanced features」，以目前的實作沒有一項成立。這類註解比沒有註解更貴：下一個維護者會照著它跳過驗證，而這正是本次三個 Critical 能一路留到現在的原因。

**證據**：
- `src/sentry/api/paginator.py:179-181`
- `src/sentry/api/paginator.py:874-879`
- `src/sentry/api/paginator.py:823-829`
- `src/sentry/api/endpoints/organization_auditlogs.py:68-69`
- `src/sentry/utils/cursors.py:26-27`

**修復方向**：刪掉這些安全性斷言，或改寫成實際成立的敘述（例如直接寫「offset 為負時 Django 會拋 ValueError，因此在此 clamp」）；cursors.py:26-27 沒有對應的程式變更，整段移除。

</details>

<details>
<summary>Nit（2）</summary>

#### F-009 行尾空白、檔尾空行、連續三個空行，專案 pre-commit 會擋下 — `src/sentry/api/endpoints/organization_auditlogs.py:72`

面向 A 風格 · Nit

**問題**：`git diff --check 74618671bff8 8ab88145113d` 對本 diff 報 5 處 trailing whitespace 與 1 處 new blank line at EOF。專案 pre-commit 掛了 black 與 flake8（.pre-commit-config.yaml:26-49），setup.cfg 的 [flake8] extend-ignore 只忽略 E203,E501,E402,E731 與部分 B 規則，沒有忽略 W291/W293/W391/E303，所以這些都會擋；black --check 也會因為行尾空白而失敗。另外 paginator.py:818-820 是三個連續空行，PEP 8 頂層定義之間是兩個。

**證據**：
- `src/sentry/api/endpoints/organization_auditlogs.py:72`
- `src/sentry/api/endpoints/organization_auditlogs.py:89`
- `src/sentry/api/paginator.py:824`
- `src/sentry/api/paginator.py:827`
- `src/sentry/api/paginator.py:912`
- `src/sentry/api/paginator.py:818-820`

**修復方向**：在變更的檔案上跑一次 `pre-commit run --files src/sentry/api/endpoints/organization_auditlogs.py src/sentry/api/paginator.py src/sentry/utils/cursors.py`。

#### F-010 optimized_pagination 繞過既有的 query param serializer，且大小寫敏感比對會靜默失效 — `src/sentry/api/endpoints/organization_auditlogs.py:70`

面向 D API 慣例 · Nit

**問題**：這個 endpoint 已經有 AuditLogQueryParamSerializer 在做 query param 驗證（:22-31，於 :52 套用），新參數卻直接讀 request.GET 繞過它。`== "true"` 是大小寫敏感的字串比較，所以 ?optimized_pagination=True / =1 / =yes 都會靜默地不生效，呼叫端收不到任何錯誤訊息，只會拿到「功能沒開」的正常回應——這種靜默失敗在 debug 時特別花時間。另外這個參數在 api-docs 與 endpoint 本身都沒有任何說明。

**證據**：
- `src/sentry/api/endpoints/organization_auditlogs.py:70`
- `src/sentry/api/endpoints/organization_auditlogs.py:22-31`
- `src/sentry/api/endpoints/organization_auditlogs.py:52-57`

**修復方向**：在 AuditLogQueryParamSerializer 加一個 `optimized_pagination = serializers.BooleanField(required=False, default=False)`（DRF BooleanField 已處理 true/True/1/yes 等寫法），改從 validated_data 讀值，讓非法輸入走既有的 400 路徑。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 這次「效能最佳化」實際要解的是哪一個慢查詢？

面向 F 資料取用與資料庫

**背景**：PR 標題與 commit message 都主張是為了 high-volume / enterprise 的 audit log 效能，但 diff 內沒有 benchmark、profiling、issue 連結或 EXPLAIN。既有的 DateTimePaginator 已經是 keyset 分頁（build_queryset 於 paginator.py:79-127 用 `datetime <= %s` 收斂範圍，再以小 offset 微調），本來就不會做全表掃描；新增的路徑反而是純 offset 切片，在大 offset 時只會更慢。從 repo 內找不到能判定「原本慢在哪」的依據，所以無法對「這個方向是否正確」下定論，也就不給 severity。

**如何確認**：作者提供對應的 issue、profiling 結果或該查詢的 EXPLAIN ANALYZE，指出目前 DateTimePaginator 在哪一種查詢形狀（資料量、offset 深度、篩選條件）上出問題。

#### Q-002 負 offset 分支預期的呼叫方式與回傳是什麼？

面向 F 資料取用與資料庫

**背景**：註解同時說它提供「efficient reverse pagination」與「access to data beyond normal pagination bounds」（paginator.py:826、:875-878），但這是兩件不同的事：「往回翻頁」codebase 既有的做法是 cursor.is_prev，「從尾端取」則需要反轉排序而不是負切片。F-003 已確認目前的實作兩者都達不到（Django 直接拒絕），但無法從程式碼判定作者原本想要哪一種，因此不對「該怎麼改才對」下結論。

**如何確認**：作者給出一個具體的 cursor 字串與期待的回傳內容，或直接補上表達該行為的測試。

</details>
