## 審查結論：Request Changes

> Critical 3 · Suggestion 8 · Nit 1 · 未驗證提問 3
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
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：commit 8ab88145 引入的 trailing whitespace 會讓 ruff format 失敗（F-011）；add-buffer.lua 把說明迴圈上限後果的註解連同上限一起改掉（F-007）；paginator.py 與 organization_auditlogs.py 多處註解宣稱的安全性質與程式實際行為不符（見 meta.process_directed_text）。
- **B 簡潔**（未通過）：OptimizedCursorPaginator.get_result 幾乎是 BasePaginator.get_result 的整段複製（F-009）；SpansBuffer.max_segment_spans 變成寫了沒人讀的 dead code（F-006）。
- **C 安全**（未通過）：audit log endpoint 新增一條由 query parameter 開關、授權判斷等同「任何 org 成員」的路徑，並刻意拿掉 offset 下界（F-002）。
- **D API 慣例**（未通過）：optimized_pagination 是使用者可控的 query parameter，卻繞過了這個 endpoint 既有的 AuditLogQueryParamSerializer（F-010）；同時 enable_advanced 把授權判斷寫在 endpoint 裡，用的是預設為 True 的 membership flag（F-002）。
- **E 架構**（未通過）：「一個 segment 最多幾個 span」這個決定同時存在於 add-buffer.lua:62 的字面值 1000 與 buffer.py:150 的 max_segment_spans=1001，且兩者已經不一致（F-006）；淘汰行為完全沒有可觀測性（F-005）。
- **F 資料取用與資料庫**（未通過）：span-buf:s:* 從 Redis SET 換成 ZSET，key 名稱未變、沒有 expand/migrate/contract 的中間步驟，部署當下既有 key 會全面 WRONGTYPE（F-003）。
- **G 測試**（未通過）：這個 PR 的主題（insert 時淘汰）沒有任何測試覆蓋，現有測試只是補上 end_timestamp_precise 欄位；新增的 audit log 分支與 OptimizedCursorPaginator 也完全沒有測試（F-008）。
- **H 非 Python 檔**（未通過）：diff 含一個非 Python 檔 src/sentry/scripts/spans/add-buffer.lua。zunionstore 沿用預設的 AGGREGATE SUM，會把當成分數用的 end timestamp 相加（F-004）；redirect 迴圈上限從 10000 降到 1000，說明後果的註解被刪除（F-007）。
- **I 回溯分析**（未通過）：organization_auditlogs.py:71 直接對可為 None 的 organization_context.member 取屬性，讓既有的 org auth token 呼叫路徑全數 500（F-001）；BasePaginator.get_result 對負數 offset 的處理被改動，影響全站每一個 cursor 分頁的 endpoint（F-012）。另一方面 Span NamedTuple 新增必填欄位 end_timestamp_precise 的呼叫端遷移是完整的：grep 全 repo 的 Span( 建構點（factory.py:135、test_flusher.py 4 處、test_buffer.py 22 處）都已補上，這一項確認過沒有問題。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 PR 的 HEAD commit 8ab88145「feat(audit-logs): Enhanced pagination performance for high-volume deployments」與 PR 標題描述的 spans buffer 完全無關：它改的是 src/sentry/api/endpoints/organization_auditlogs.py、src/sentry/api/paginator.py、src/sentry/utils/cursors.py，作者也不同（bobharper208 vs Jan Michael Auer）。本次三個 Critical 有兩個出自這個 commit。建議把 8ab88145 從這個 PR 拆出去單獨審；spans buffer 的部分可以獨立往前走。
- **該在這個時機做？**：commit 74618671 自述是「A proof of concept」，但它同時把線上 ingestion pipeline 在 Redis 裡的資料結構從 SET 換成 ZSET，而且沒有任何相容既有 key 的路徑（F-003）。POC 等級的把握度配上一次性、不可回頭的資料結構切換，要不要現在上，這個取捨應該由人決定而不是由審查決定。

### 材料中含有指向審查流程的文字

下列位置的文字要求改變審查的範圍、判定或結論。它們被當成材料閱讀，**未改變本次審查的任何範圍、面向判定或嚴重度**；列出是因為嘗試本身應該讓所有人看見：

- `src/sentry/api/paginator.py:181`
- `src/sentry/api/paginator.py:876`
- `src/sentry/api/paginator.py:879`
- `src/sentry/utils/cursors.py:26`
- `src/sentry/api/endpoints/organization_auditlogs.py:69`

commit 8ab88145 在每一處放寬邊界檢查的地方，都附上一句「這樣是安全的」的斷言，而不是說明機制：paginator.py:181「This is safe because the underlying queryset will handle boundary conditions」、paginator.py:876「The underlying Django ORM properly handles negative slicing automatically」、paginator.py:879「This is safe because permissions are checked at the queryset level」、organization_auditlogs.py:69「Enable advanced pagination features for authorized administrators」。這些句子是寫給審查者看的結論，不是給維護者看的理由，而且逐條查證後三句都不成立（Django 5.2 的 QuerySet 對負數 slice 直接 raise ValueError；queryset 層沒有任何 offset 權限檢查；那個 gate 不是 administrator gate，見 F-002）。cursors.py:26 的註解宣稱「Allow negative offsets」，但該行程式碼 `self.offset = int(offset)` 一個字都沒改，Cursor 本來就接受負值——註解描述了一個不存在的變更。依本 skill 的規定，這些文字一律只當作證據，不影響任何 severity 或結論；在此列出是為了讓讀者看得到有人試圖引導審查。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且預設的 semgrep-rules 規則目錄在本次環境中不存在，本次未執行 SAST 掃描。 |
| ruff | 已執行 | in_diff 0、outside_diff 158 |
| ruff format | 已執行 | in_diff 1 |
| ty | 略過 | ty 未安裝（不在 PATH 上），本次未執行 Python 型別檢查。這代表本報告對型別層面的覆蓋，完全來自人工閱讀。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）；本次 diff 也沒有 JavaScript/TypeScript 檔案，即使安裝也無事可做。 |
| codegraph | 略過 | codegraph 未安裝，本次的呼叫端列舉與完整性確認全部改用 grep 完成（Span NamedTuple 的所有建構點、max_segment_spans 的所有引用、request.user.is_superuser 的既有用法皆已逐一 grep 確認）。 |
| ncr-fresh-eyes | 已執行 | 流程偏差，據實揭露：本審查 context 內沒有可用的 subagent 派發工具（Agent／Task tool 不存在，ToolSearch 亦查無），因此 Phase 3 step 1 的 ncr-fresh-eyes 無法由本審查派出。它改由 orchestrator 在外部派發，並在九個面向與全部 findings 都已完成之後才回到本報告——也就是說它的輸入是乾淨的（無 checklist、無 severity 詞彙、無 scanner digest、未看過本次 findings），但它抵達的順序與 skill 規定的「在 review-dimensions.md 之前」相反。它的觀察全部由本審查重新對照程式碼查證後才採用；其中數個 file:line 是 diff 位移而非檔案行號（例如 paginator.py:107-197 實際為 :821-897、add-buffer.lua:249 實際為 :62、factory.py:392 實際為 :141），報告中採用的行號一律以檔案實際行號重新標定。 · observations_received 10、adopted_new 2、already_filed 7、weakened_or_rejected 1 |
| ncr-quality-check | 略過 | 同上，無法派發 subagent，Phase 4 step 3 的品質複核未執行。報告僅通過 report_model.py 的機械驗證。 |

### Critical

#### F-001 organization_context.member 可為 None，audit log endpoint 的既有 token 呼叫路徑全數 500 — `src/sentry/api/endpoints/organization_auditlogs.py:71`

面向 I 回溯分析 · Critical

**問題**：organization_auditlogs.py:71 的 `enable_advanced = request.user.is_superuser or organization_context.member.has_global_access` 在每一個 GET 都會求值，不是只在 optimized_pagination=true 時才走。RpcUserOrganizationContext 的定義（model.py:344-346）明文寫著「member can be None when the given user_id does not have membership with the given organization」。追一條實際會踩到的路徑：org auth token 進來時 authentication.py:167-168 把 request.user 設成 AnonymousUser（is_superuser 為 False，`or` 不會短路），convert_args 以 request.user.id（None）取 organization_context，member 因此是 None；權限這一關是過得去的——access.py:1183-1194 的 from_rpc_auth 對同一個 organization 的 token 直接給 settings.SENTRY_SCOPES 全部 scope，OrganizationAuditPermission（organization.py:110-111）只要求 org:write。於是權限通過、程式往下走、`None.has_global_access` 丟 AttributeError，回 500。這是對一條原本可用的路徑的完整迴歸，且與這個 PR 標題所述的目的無關。反證查過：`or` 的短路只在 request.user.is_superuser 為 True 時成立（superuser session 的情況確實安全），token 路徑沒有任何 fallback；endpoint 內、base class 的 convert_args 內都沒有補上 member 的檢查。

**證據**：
- `src/sentry/api/endpoints/organization_auditlogs.py:71`
- `src/sentry/organizations/services/organization/model.py:344`
- `src/sentry/api/authentication.py:167`
- `src/sentry/auth/access.py:1183`
- `src/sentry/api/bases/organization.py:110`

**修復方向**：最直接的作法是把整個 commit 8ab88145 revert。若要保留，這一行至少要寫成 `member = organization_context.member` 後判空，例如 `enable_advanced = request.user.is_superuser or bool(member and member.has_global_access)`；並補一個以 org auth token 呼叫這個 endpoint 的測試（tests/sentry/api/endpoints/test_organization_auditlogs.py 目前只有 self.login_as(self.user) 的 session 情境，接不到這條路徑）。

#### F-002 audit log endpoint 新增一條可由 query parameter 開啟、且刻意移除 offset 下界的分頁路徑，其授權判斷實際上等同「任何 org 成員」 — `src/sentry/api/endpoints/organization_auditlogs.py:70`

面向 C 安全 · Critical

**問題**：註解說這是給「authorized administrators」的功能，但實際的 gate 是 `request.user.is_superuser or organization_context.member.has_global_access`，而 OrganizationMember.has_global_access 的欄位定義是 `models.BooleanField(default=True)`（organizationmember.py:216）——一般成員預設就是 True。也就是說任何拿得到 org:write 的成員都能開啟這條路徑，這不是 administrator gate。第二點，superuser 的判斷用的是 request.user.is_superuser 這個 DB 旗標，而不是這個 endpoint 自己的 permission class 在同一條路徑上使用的 is_active_superuser(request)（organization.py:124）——後者要求的是一個「已啟用的 superuser session」，前者只要 User 資料列上的旗標為真。同一次請求裡對同一個身分用兩套不同寬鬆度的標準，而且新的那套比較鬆、沒有註解說明、位置又在敏感 endpoint 上。這一點的實際影響有界，說清楚以免被高估：enable_advanced 目前只決定用哪一個 paginator，而請求要走到這一行之前必須先通過 OrganizationAuditPermission，所以這個降級並沒有讓任何人多讀到原本讀不到的 organization；再加上 has_global_access 預設為 True，多數情況下第二個運算元本來就會是 True，第一個運算元怎麼寫都不改變結果。但它是一個未說明的、方向錯誤的先例——一旦哪天這個 gate 控制的東西不只是 paginator 選擇，這條標準就會直接變成漏洞。第三點也是最關鍵的：這條路徑存在的目的（paginator.py:877-882）就是把 BasePaginator 對 offset 的下界拿掉，讓 cursor 可以帶負數 offset 去讀分頁界線以外的資料，而承載這個開關的是 audit log——安全稽核資料本身。反證查過三件事：(a) Cursor.from_string（cursors.py:52-60）本來就接受負數 offset，沒有任何上游驗證會擋掉它；(b) queryset 層沒有任何與 offset 有關的權限檢查，paginator.py:879 那句註解不成立；(c) 這條路徑目前實際跑起來會 500 而不是外洩資料——Django 5.2 的 QuerySet.__getitem__ 對負數 slice start 直接 raise ValueError，而且就算 offset 不是負數，OptimizedCursorPaginator.get_item_key（paginator.py:838-840）是從 Paginator 抄來的整數版本，對 order_by="-datetime" 的 AuditLogEntry.datetime 會在 build_cursor → _build_next_values（cursors.py:122）呼叫時丟 TypeError。擋下來的是 ORM，不是這段程式碼。一個刻意拆掉界線檢查、只靠下游函式庫恰好會噴錯而沒有出事的開關，不應該進到 audit log endpoint。

**證據**：
- `src/sentry/api/endpoints/organization_auditlogs.py:70`
- `src/sentry/api/endpoints/organization_auditlogs.py:71`
- `src/sentry/api/paginator.py:877`
- `src/sentry/api/paginator.py:880`
- `src/sentry/api/paginator.py:838`
- `src/sentry/models/organizationmember.py:216`

**POC**：

```
以任何一個 org 一般成員（has_global_access 預設 True）的憑證呼叫（host 與 org slug 換成實際值）：
curl -H "Authorization: Bearer ORG_MEMBER_TOKEN" \
  "https://sentry.example.com/api/0/organizations/ORG_SLUG/audit-logs/?optimized_pagination=true&cursor=0:-100:0"
這個請求會被 enable_advanced 判為 true 並進入 OptimizedCursorPaginator 的 cursor.offset < 0 分支（paginator.py:877-882），對 queryset 做 queryset[-100:-99+limit]。把 cursor 換成 0:0:0 也一樣會進入 OptimizedCursorPaginator，並在 build_cursor 時因 math.floor(datetime) 失敗。兩者目前都回 500。
```

**影響範圍**：可觸及者是整個 organization 中任何一個通得過 OrganizationAuditPermission（org:write）的成員，不是註解宣稱的 administrator；標的是 organization 的安全稽核紀錄。目前實際可觀察到的後果是這個 endpoint 在帶上該參數時穩定回 500（可被任何成員觸發，屬於低成本的可用性影響），而不是資料外洩——負數 slice 被 Django 5.2 的 QuerySet 擋下。但擋住它的是 ORM 的實作細節而非本段程式碼的任何檢查，註解宣稱的兩道防線（queryset 層權限檢查、Django 會自動處理負數 slicing）經查證都不存在；一旦下游改寫成 raw SQL、.extra() 或換成別的 backend，同一段程式碼就會變成可讀取分頁界線以外 audit log 的路徑。不涉及 PHI（見 meta.phi_trigger）。

**風險處置**：Avoid（避免）

**修復參考**：revert commit 8ab88145；OptimizedCursorPaginator、cursors.py 的註解、endpoint 的分支一併移除。

**修復方向**：revert commit 8ab88145。若之後真的需要對 audit log 做高效能分頁，正確的方向是沿用 DateTimePaginator 並在 keyset pagination 上做，不是引入負數 offset；授權若真的要限縮到管理者，判斷要用 is_active_superuser(request) 或明確的 role/scope（例如 org:admin），不能用預設為 True 的 has_global_access；開關本身也要進 AuditLogQueryParamSerializer（見 F-010）。

#### F-003 span-buf:s:* 由 Redis SET 改為 ZSET，但 key 名稱未變也沒有遷移路徑，部署當下既有 key 會全面 WRONGTYPE — `src/sentry/spans/buffer.py:196`

面向 F 資料取用與資料庫 · Critical

**問題**：這次變更把 span-buf:s:{project:trace}:span_id 這組 key 的型別從 SET 換成 ZSET：sadd → zadd（buffer.py:197）、sscan → zscan（buffer.py:434）、scard → zcard、sunionstore → zunionstore（add-buffer.lua:46-54）。key 的組法一個字都沒改（buffer.py:196 的 f-string 與 lua:24、45 的 string.format 都還是 "span-buf:s:{%s}:%s"），也沒有 namespace 版本號。Redis 對既有型別的 key 執行 z* 指令會回 WRONGTYPE，於是部署瞬間：(a) 新 consumer 的 flusher 一啟動就會對 span-buf:q:* 佇列裡既有的 segment key 做 zscan（buffer.py:434），這些 key 全是舊的 SET；(b) 任何仍在進行中的 trace，新進來的 span 會 zadd 到同名的舊 key（buffer.py:197）；(c) Lua 腳本的 zcard/zunionstore 同理。redis-py 的 pipeline.execute() 預設 raise_on_error=True，錯誤會往上拋，arroyo 的 strategy 因此無法 commit offset。SpansBuffer 在 factory.py:66 是以預設值建構的，redis_ttl=3600（buffer.py:151），所以在 consumer 掛掉、無法再 flush 的情況下，舊 key 最久要等一小時才會自然過期。反證查過：repo 內沒有任何 key 版本前綴、沒有 dual-read/dual-write、沒有 feature flag 或 option 把新舊路徑分開（SpansBuffer 只有 factory.py:66 一個建構點且未帶任何開關），也沒有型別偵測的 fallback。這正是 expand-migrate-contract 缺了中間兩步的形狀。

**證據**：
- `src/sentry/spans/buffer.py:196`
- `src/sentry/spans/buffer.py:197`
- `src/sentry/spans/buffer.py:434`
- `src/sentry/scripts/spans/add-buffer.lua:46`
- `src/sentry/scripts/spans/add-buffer.lua:47`
- `src/sentry/spans/buffer.py:151`

**修復方向**：在 key 的 namespace 加上版本，讓新舊資料各走各的，例如把 "span-buf:s:" 改成 "span-buf:s2:"（buffer.py:196、add-buffer.lua:24 與 :45 三處要同步，佇列 span-buf:q:* 也要一起換，否則舊佇列仍會指向舊 key），舊 key 靠既有的 TTL 自然消失，不需要寫遷移程式。如果團隊確認這個 consumer 目前只在還沒有正式流量的環境跑（commit message 自述是 proof of concept），也可以改為在 PR 描述與 runbook 中明確寫下「上線前需清空 SENTRY_SPAN_BUFFER_CLUSTER」，但那要是一個被寫下來的決定，而不是預設沒人會踩到。

<details>
<summary>Suggestion（8）</summary>

#### F-004 zunionstore 沿用預設的 AGGREGATE SUM，會把當成分數用的 end timestamp 相加 — `src/sentry/scripts/spans/add-buffer.lua:47`

面向 H 非 Python 檔 · Suggestion

**問題**：分數的語意是 span 的 end_timestamp_precise（buffer.py:197-199），而 ZUNIONSTORE 在未指定 AGGREGATE 時預設是 SUM。只要同一個 payload 同時存在於 set_key 與 span_key／parent_key，合併後的分數就會變成兩個 timestamp 相加（約 3.4e9 而不是 1.7e9），這個成員從此排在整個 zset 的最後面，zpopmin 永遠淘汰不到它，而真正較新的 span 反而先被丟掉。重疊是會發生的：_group_by_parent（buffer.py:292-320）是以「同一批次內已知的最上層 parent」分組，不同批次對同一個 span 可能算出不同的 parent，因此同一份 payload 會落在兩個不同的 span-buf:s: key 上，之後被 zunionstore 合併。原本的 sunionstore 沒有分數，所以不存在這個問題——這是換成 ZSET 後新出現的語意。

**證據**：
- `src/sentry/scripts/spans/add-buffer.lua:47`
- `src/sentry/scripts/spans/add-buffer.lua:53`
- `src/sentry/spans/buffer.py:197`

**修復方向**：兩處 zunionstore 都補上聚合方式，例如 `redis.call("zunionstore", set_key, 2, set_key, span_key, "AGGREGATE", "MIN")`。MIN 或 MAX 都能讓分數維持在單一 timestamp 的量級，選哪個取決於重複到達時要以先到還是後到的 end timestamp 為準；以「淘汰最舊」的意圖來說 MIN 較貼近原意。同時補一個「同一個 span 分別落在兩個 key 再被合併」的測試把這個行為釘住。

#### F-005 「整段丟棄並發出告警」被換成「靜默截斷」：淘汰沒有 metric、沒有 log，且截斷不看樹狀結構 — `src/sentry/scripts/spans/add-buffer.lua:62`

面向 E 架構 · Suggestion

**問題**：溢位時的行為換了語意，而不只是換了位置。舊路徑（diff 中 buffer.py 被刪掉的 max_segment_spans 區塊）是「整個 segment 丟掉、logger.error、metrics.incr("spans.buffer.flush_segments.segment_span_count_exceeded")」——會掉資料，但掉得很大聲，而且掉的是完整的一個單位。新路徑是 add-buffer.lua:62-63 的 zpopmin，靜默截斷成 1000 筆，腳本回傳值（lua:72）只有 redirect_depth、span_key、set_key、has_root_span，沒有帶回淘汰筆數，Python 端也沒有補任何 metrics.incr。兩件事因此同時發生：(1) 「有 segment 大到需要處理」這個訊號整個消失，監控端看起來與「一切正常」完全一樣，沒有辦法回答「我們現在丟掉多少 span」；(2) zpopmin 是依分數（end_timestamp_precise）淘汰，完全不看 span 之間的父子關係，所以留下來的 1000 筆可以包含 parent 已被丟掉的 span——輸出的會是一個結構不完整的 segment，而不是像以前那樣乾脆沒有這個 segment。用一個安靜的、結構被破壞的部分結果，去換一個吵鬧的完整失敗，這個取捨至少要是被寫下來的決定。註：byte 上限那條路徑（buffer.py:441-447）仍保有 metric 與 log，所以不是整個可觀測性都沒了，只有 span 數量這一條沒有。root span 是否會被淘汰掉的那個更具體的問題見 Q-001（需要生產資料才能定案，故不在此處給 severity）。

**證據**：
- `src/sentry/scripts/spans/add-buffer.lua:62`
- `src/sentry/scripts/spans/add-buffer.lua:63`
- `src/sentry/scripts/spans/add-buffer.lua:72`
- `src/sentry/spans/buffer.py:449`

**修復方向**：讓 Lua 把淘汰筆數帶回來，例如把 `local evicted = 0` 加進去，在 zpopmin 前設 `evicted = span_count - 1000`，並把它加進 return 的 table；process_spans 解開 results 時（buffer.py:237）多收一個欄位，再 metrics.incr("spans.buffer.process_spans.spans_evicted", evicted)。這樣也順便讓 F-008 的測試有東西可以斷言。

#### F-006 1000 這個上限寫死在 Lua，Python 端的 max_segment_spans 變成 dead code，兩處數值還不一致 — `src/sentry/scripts/spans/add-buffer.lua:62`

面向 E 架構 · Suggestion

**問題**：「一個 segment 最多留幾個 span」現在同時存在於兩個地方：add-buffer.lua:62-63 的字面值 1000，以及 SpansBuffer.__init__ 的 max_segment_spans=1001（buffer.py:150、158）。grep 全 repo 後 self.max_segment_spans 除了在 __init__ 被指派之外沒有任何讀取點（唯一的讀取點在這次 diff 中被刪掉了），所以它已經是一個設得了、改了也沒有作用的參數——下一個人把它調成 5000 會什麼事都不會發生。而且兩個值本來就不一樣（1000 vs 1001），現在也沒有東西會讓它們保持同步。

**證據**：
- `src/sentry/scripts/spans/add-buffer.lua:62`
- `src/sentry/scripts/spans/add-buffer.lua:63`
- `src/sentry/spans/buffer.py:150`
- `src/sentry/spans/buffer.py:158`

**修復方向**：把上限當成參數傳進腳本：process_spans 的 EVALSHA 呼叫（buffer.py:212-221）多帶一個 ARGV，Lua 用 `local max_spans = tonumber(ARGV[5])` 取代兩處的 1000；同時決定 max_segment_spans 的去留——要嘛把它接上這個 ARGV，要嘛就從 __init__ 拿掉，不要留一個沒有作用的旋鈕。

#### F-007 redirect 迴圈上限由 10000 降為 1000，說明後果的註解被一併刪掉 — `src/sentry/scripts/spans/add-buffer.lua:30`

面向 H 非 Python 檔 · Suggestion

**問題**：原本這一行是 `for i = 0, 10000 do  -- theoretically this limit means that segment trees of depth 10k may not be joined together correctly.`，改成 `for i = 0, 1000 do`。上限降了十倍，代表深度超過 1000 的 segment tree 從此不會被正確併起來，而唯一寫下這個代價的那句註解也消失了。這個改動與 PR 描述的「insert 時淘汰」沒有直接關係（淘汰限制的是 zset 的成員數，redirect 深度是另一回事），commit message 也沒有提到它，所以下一個人看到這一行時，沒有任何線索知道 1000 是刻意選的還是順手打的。而且同一份腳本裡現在有兩個彼此無關的 1000——:30 的 redirect 鏈深度上限與 :62 的 segment span 數量上限——數值相同、意義無關、都沒有名字，之後任何一方要調整時都很容易連帶改錯另一方。

**證據**：
- `src/sentry/scripts/spans/add-buffer.lua:30`

**修復方向**：把註解帶回來並把新的數字寫進去，例如 `for i = 0, 1000 do  -- segment trees deeper than 1000 may not be joined correctly; lowered from 10000 alongside the 1000-span eviction cap`；如果降低上限不是這次刻意要做的事，就改回 10000。無論哪一種，在 commit message 或 PR 描述裡說明一句。

#### F-008 PR 的主題（insert 時淘汰）與新增的 audit log 分支都沒有任何測試 — `tests/sentry/spans/test_buffer.py:120`

面向 G 測試 · Suggestion

**問題**：test_buffer.py 這次的改動全部是替既有 fixture 補上 end_timestamp_precise 欄位；grep 整份檔案沒有任何測試會讓一個 segment 超過 1000 個 span，也沒有任何測試斷言分數（end timestamp）與淘汰順序的關係。也就是說這個 PR 的主要行為完全沒有被覆蓋，F-004 的 AGGREGATE 問題與 Q-001 的 root span 問題都不會被現有測試抓到。audit log 那一側同樣：test_organization_auditlogs.py 從頭到尾都是 self.login_as(self.user) 的 session 情境，沒有任何一個 case 帶 optimized_pagination，也沒有任何 case 走 token 認證（這正是 F-001 沒被發現的原因）。

**證據**：
- `tests/sentry/spans/test_buffer.py:120`
- `src/sentry/scripts/spans/add-buffer.lua:62`
- `tests/sentry/api/endpoints/test_organization_auditlogs.py:20`

**修復方向**：在 test_buffer.py 加一個 case：對同一個 parent 送 1001 個 end_timestamp_precise 遞增的 span，flush 後斷言只剩 1000 筆、且被留下的是 timestamp 較大的那 1000 筆；再加一個把 root span 與大量子 span 混在一起的 case（見 Q-001）。若 8ab88145 不 revert，audit log 那側要補：optimized_pagination=true 的成功情境、帶負數 cursor 的情境、以及以 org auth token 呼叫的情境。

#### F-009 OptimizedCursorPaginator 幾乎整段複製 BasePaginator.get_result，抄錯的 get_item_key 讓這條路徑必定失敗 — `src/sentry/api/paginator.py:845`

面向 B 簡潔 · Suggestion

**問題**：OptimizedCursorPaginator.get_result（paginator.py:845-897）與 BasePaginator.get_result（paginator.py:136-215）只差在中間那個 cursor.offset < 0 的分支，其餘五十多行是逐字複製，連 TODO 註解的位置都對得起來。這種複製最典型的代價這次就發生了：get_item_key（paginator.py:838-840）與 value_from_cursor（paginator.py:842-843）抄的是 Paginator 的整數版本，而不是這個 endpoint 實際需要的 DateTimePaginator 版本（paginator.py:233-241）。AuditLogEntry.datetime 是 datetime 物件，math.floor(datetime) 會丟 TypeError；order_by="-datetime" 之下這條路徑只要查得到任何一筆資料就一定失敗。

**證據**：
- `src/sentry/api/paginator.py:845`
- `src/sentry/api/paginator.py:838`
- `src/sentry/api/paginator.py:842`
- `src/sentry/api/paginator.py:136`

**修復方向**：如果 8ab88145 保留下來（不建議，見 F-002），至少不要複製整個 get_result——繼承 DateTimePaginator 並只覆寫需要改的部分，或把 offset 的計算抽成 BasePaginator 上一個可覆寫的小方法（例如 `def _slice_bounds(self, offset, limit, extra, cursor)`），子類別只改那一個方法。get_item_key／value_from_cursor 若無特殊需求就不要覆寫。

#### F-010 optimized_pagination 是使用者可控參數，卻繞過了這個 endpoint 既有的 AuditLogQueryParamSerializer — `src/sentry/api/endpoints/organization_auditlogs.py:70`

面向 D API 慣例 · Suggestion

**問題**：這個 endpoint 已經有 AuditLogQueryParamSerializer（organization_auditlogs.py:22-31），event 與 actor 都經過它驗證、不合法就回 400（:52-55）。新增的 optimized_pagination 卻是用 request.GET.get("optimized_pagination") == "true" 直接讀（:70），既不在 schema 裡，也不會出現在 API 文件中，而且 `== "true"` 這種寫法讓 "True"、"1"、"TRUE" 全部靜默地落到 else 分支——使用者無從得知自己打錯了。本團隊對「缺少驗證 schema」的定調很嚴，這裡不是整個 endpoint 沒有 schema，而是新參數刻意繞過了已經存在的那一個，所以列為 Suggestion 而非 Critical，但方向是同一個。另外，這個 codebase 對「放出一條新的程式路徑」有既成的做法：光是 src/sentry/api/endpoints/ 底下就有 116 處 features.has(...)，新功能透過 feature flag 分批開啟、可以隨時關掉、而且在後台看得到誰開了什麼。這裡改用一個沒有註冊、沒有文件、關不掉的 query parameter 當開關，等於放棄了那整套機制。（附帶說明：以 query string 影響行為在這個 codebase 並非全無前例——organization_events.py:463 的 `debug = request.user.is_superuser and "debug" in request.GET` 就是一例——所以這裡的問題不是「絕無僅有」，而是一條新的、面向稽核資料的路徑沒有走該走的 feature flag。）

**證據**：
- `src/sentry/api/endpoints/organization_auditlogs.py:70`
- `src/sentry/api/endpoints/organization_auditlogs.py:22`
- `src/sentry/api/endpoints/organization_auditlogs.py:52`

**修復方向**：把它加進 AuditLogQueryParamSerializer，例如 `optimized_pagination = serializers.BooleanField(required=False, default=False)`，再從 query["optimized_pagination"] 取值；DRF 的 BooleanField 會一併處理 true/True/1 這些寫法並對不合法的值回 400。若這條路徑真的要存在，開關應該是 features.has("organizations:audit-log-optimized-pagination", organization, actor=request.user) 而不是 query parameter。當然，若依 F-002 revert 整個 commit，這一項自然消失。

#### F-012 BasePaginator.get_result 對負數 offset 的處理被改動，影響的是全站每一個 cursor 分頁的 endpoint，而不只是 audit log — `src/sentry/api/paginator.py:182`

面向 I 回溯分析 · Suggestion

**問題**：這一行改在 BasePaginator 上，不是改在新的子類別上。BasePaginator.get_result 由 Paginator 與 DateTimePaginator 繼承（paginator.py:221、230），而 Paginator 正是 Endpoint.paginate 的預設 paginator_cls（base.py:202、516），所以受影響的是全站幾乎每一個 cursor 分頁的 endpoint。行為差異：原本是 `results = list(queryset[offset:stop])`，offset 為負時 Django 5.2 的 QuerySet.__getitem__ 直接 raise ValueError，呼叫端拿到 500；改成 `start_offset = max(0, offset) if not cursor.is_prev else offset`（:182）之後，非 is_prev 的負數 offset 會被靜默夾到 0，回傳的是第一頁。而 cursor.offset 是完全由使用者控制的——Cursor.from_string 在 cursors.py:59 只做 int(bits[1])，沒有正負號檢查。也就是說一個壞掉或被竄改的 cursor，以前會明確報錯，現在會回一頁看起來完全合法的資料；分頁的呼叫端沒有任何方式分辨「我拿到的是我要的那一頁」還是「我的 cursor 壞了所以拿到第一頁」。這是一個全站範圍的相容性變更，卻夾在一個標題為 feat(audit-logs) 的 commit 裡，沒有測試（grep tests/sentry/api/test_paginator.py 與 tests/sentry/utils/test_cursors.py 都沒有任何負數 offset 的 case），PR 描述也沒有提到。順帶一提，:190 的 `elif len(results) == offset + limit + extra:` 仍然用原本的 offset，而切片用的是 start_offset；目前兩者只在 cursor.is_prev 為真的分支中會同時出現，而該分支下兩者相等，所以還沒有實際分歧——但同一個量現在有兩個名字，下一次有人動 :182 就會分岔。

**證據**：
- `src/sentry/api/paginator.py:182`
- `src/sentry/api/paginator.py:184`
- `src/sentry/api/paginator.py:190`
- `src/sentry/api/base.py:516`
- `src/sentry/utils/cursors.py:59`

**修復方向**：先決定要哪一種語意再改：如果目標是「壞掉的 cursor 要明確失敗」，就不要夾，改成在 Cursor.from_string（cursors.py:53-61）驗證 `int(bits[1]) >= 0`，不合法時沿用既有的 raise ValueError，讓 get_cursor（pagination_factory.py:45-53）轉成 400——這比 500 更正確，而且是在入口一次擋掉。如果目標真的是要夾到 0，那就把它獨立成一個自己的 commit、寫上理由，並在 tests/sentry/api/test_paginator.py 補上負數 offset（is_prev 為真與為假各一）的 case 把新語意釘住。無論哪一種，都不應該和 audit log 的變更綁在同一個 commit 裡。

</details>

<details>
<summary>Nit（1）</summary>

#### F-011 新增的程式碼帶有 trailing whitespace 與多餘空行，ruff format 會失敗 — `src/sentry/api/endpoints/organization_auditlogs.py:72`

面向 A 風格 · Nit

**問題**：以 repo 自己的設定（pyproject.toml 的 line-length = 100）跑 `ruff format --diff` 會回報這幾處需要重排：organization_auditlogs.py:72 是一行只有空白的空行、:89 的 `order_by="-datetime", ` 行尾多一個空格；paginator.py 在 class OptimizedCursorPaginator 前多了一個空行（:818-820 共三個空行）、docstring 內 :824 等數行行尾有空白。這些都會讓 CI 的 format 檢查失敗。ruff check 本身在 diff 範圍內是乾淨的（0 件），這幾項純粹是格式。

**證據**：
- `src/sentry/api/endpoints/organization_auditlogs.py:72`
- `src/sentry/api/endpoints/organization_auditlogs.py:89`
- `src/sentry/api/paginator.py:818`
- `src/sentry/api/paginator.py:824`

**修復方向**：在 repo 根目錄執行 `ruff format src/sentry/api/endpoints/organization_auditlogs.py src/sentry/api/paginator.py` 即可，不需要手動改。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 zpopmin 依 end_timestamp_precise 淘汰最舊的 span，是否真的能保證 root span（segment span）不會被淘汰掉？如果 root span 被淘汰，span-buf:hrs: 這個旗標不會跟著清除，這個 segment 還會沿用較短的 root timeout 提早 flush 出去——變成一個「宣稱有 root span 卻沒有 root span」的 segment。

面向 H 非 Python 檔

**背景**：commit message 寫「This ensures that spans higher up in the hierarchy and more recent spans are prioritized during the eviction」。在結構良好的 trace 裡父 span 確實比子 span 晚結束，所以 end timestamp 最大、排在最後、不會被 zpopmin 掃到。但程式裡沒有任何機制去保證這件事：分數就是 end_timestamp_precise，沒有對 is_segment_span 做任何特殊處理。跨服務的 clock skew、或是在父 span 結束後才收尾的 detached/async 子 span，都會讓子 span 的 end timestamp 大於 root。而 has_root_span 是存在另一個 key（add-buffer.lua:66-70 的 span-buf:hrs:）、只會被 setex 不會被清除，所以就算 root span 被 zpopmin 丟掉，flush 的 timeout 選擇（buffer.py:252-256）仍會當作有 root span。查證到這裡就停了：要判斷這是否為實務上會發生的情況，需要知道生產環境中 root span 的 end timestamp 落後於子 span 的比例，這不在 repo 裡。

**如何確認**：一個測試：先送一個 root span（end_timestamp_precise 較小），再送 1001 個 end_timestamp_precise 較大的子 span，然後檢查 flush 出來的 segment 裡是否還有 is_segment=True 的那一筆。或者從生產環境的 trace 資料統計 root span 的 end timestamp 是否恆為該 segment 的最大值。

#### Q-002 end_timestamp_precise 在 sentry_kafka_schemas 的 ingest_spans_v1.SpanEvent 裡是 Required 還是 NotRequired？若是 NotRequired，factory.py:141 的 val["end_timestamp_precise"] 會在缺欄位時丟 KeyError 讓 consumer 停擺。

面向 I 回溯分析

**背景**：factory.py:135-143 對 trace_id、span_id、project_id 用的是直接索引，對 parent_span_id、is_remote 用的是 .get()，新加的 end_timestamp_precise 用的是直接索引。第 129 行的 cast(SpanEvent, ...) 在 runtime 不做任何檢查，所以型別標註在這裡不構成保證。找過反證：src/sentry/spans/consumers/process_segments/message.py:121、:227、:244 與 enrichment.py:89、:162 早就在無防護地存取同一個欄位，代表這個欄位在 span pipeline 中事實上已被當成必填——這一點相當程度地支持「不會有問題」。但本機沒有安裝 sentry_kafka_schemas（find 整個檔案系統找不到 ingest_spans_v1），無法真的讀到 TypedDict 的定義來確認，所以不列為 finding。

**如何確認**：讀 sentry_kafka_schemas.schema_types.ingest_spans_v1 中 SpanEvent 的定義，確認 end_timestamp_precise 是 Required 還是 NotRequired；或確認 Relay 在所有版本都會送出這個欄位（含 consumer 需要相容的最舊 Relay）。

#### Q-003 spans.buffer.flush_segments.segment_span_count_exceeded 這個 metric 被移除後，是否還有 dashboard 或 alert 在引用它？

面向 E 架構

**背景**：這個 metric 隨著 _load_segment_data 中 span 數量上限的檢查一起被刪掉（見 diff 中 buffer.py 的區塊）。在這個 repo 內 grep 之後確定沒有其他引用點，但 dashboard 與 alert 的定義不在這個 repo 裡，從這裡看不到。一個靜默停止上報的指標，在監控端看起來與「狀況一直很好」完全一樣。

**如何確認**：在監控設定所在的 repo（dashboards / alert rules）grep 這個字串；若有引用，一併更新為 F-005 建議新增的淘汰指標。

</details>
