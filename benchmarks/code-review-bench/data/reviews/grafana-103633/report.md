## 審查結論：Request Changes

> Critical 1 · Suggestion 6 · Nit 2 · 未驗證提問 3
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ✅ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | — | ✅ |

- **A 風格**（未通過）：F-006（permissionCacheUsage 的 cache_hit 標籤與實際發生的事不符）、F-008（NoopCache 匯出、receiver 命名、缺 doc comment）。gofmt 檢查通過。
- **B 簡潔**（未通過）：F-004：getCachedIdentityPermissions 複製了 getIdentityPermissions 的 identity type switch，快取 key 的組法也散在三個地方。
- **C 安全**（未通過）：F-001（denial cache key 不含 identity type，anonymous 與 render service 共用 UID "0"）、F-002（key 空間由 request 的 name/folder 決定，無上限）、F-007（key 以未跳脫的底線串接，而 Grafana UID 允許底線）。三者都只會造成誤拒不會造成誤放，但 F-001 是跨主體共用授權判斷結果，必須先修。
- **D API 慣例**（不適用）：本次變更沒有動到任何 HTTP API：gRPC 的 CheckRequest / ListRequest / CheckResponse / ListResponse 欄位與語意皆未更動（service.go:93-197 只改內部流程），也沒有新增或修改路由、URL 或 schema。
- **F 資料取用與資料庫**（未通過）：F-005：getUserPermissions / getAnonymousPermissions 拿掉讀快取後，任何「快取無法直接放行」的 Check 都會完整打一次 permissionStore，singleflight 只能收斂同時併發的請求。
- **G 測試**（未通過）：F-003（新增的 deny 測試在拿掉 permDenialCache 後仍會通過，等於沒有測到短路邏輯）、F-009（TestService_getUserPermissions 的 cacheHit 欄位與兩個分支已成死碼）。另外 F-001 與 F-007 描述的 key 碰撞、以及 List 路徑完全不受 permDenialCache 影響這件事，都沒有測試覆蓋。
- **H 非 Python 檔**（不適用）：本次 diff 全為 Go 檔（pkg/services/authz/rbac.go、rbac/cache.go、rbac/service.go、rbac/service_test.go），不含本維度列舉的任何檔案類別（UI 元件、Vue、Dockerfile、nginx.conf、docker-compose、Alembic migration），也不含 Python。Go 原始碼已在 A–G 與 I 各維度審查，格式以 gofmt -l 確認無問題。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 同時做了三件可獨立驗證的事：(1) 服務端新增 permDenialCache 與 getCachedIdentityPermissions（service.go:116-159、342-368）；(2) 把讀快取的位置從 getUserPermissions/getAnonymousPermissions 上移（service.go:379、423）；(3) 移除 in-proc client 的 client-side 快取，改用 NoopCache（pkg/services/authz/rbac.go:101-105、239-251）。第 (3) 項影響的是所有單一 binary 部署的 Grafana，風險輪廓與前兩項不同，回滾時也只能整包回滾。建議至少讓 (3) 獨立成一個 commit 以外的 MR，或在描述中說明為何必須綁在一起。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | total 0、in_diff 0 |
| gofmt | 已執行 | unformatted_files 0、files_checked 4 |
| go vet | 略過 | 本機沒有 Go module cache 也沒有網路（GOPROXY 不可達），go vet 在解析 github.com/BurntSushi/toml 等相依時即失敗，無法取得任何診斷。本次審查沒有任何編譯期或型別檢查的覆蓋。 |
| go build | 略過 | 同上：相依套件不在本機 module cache，無法編譯。因此「這份 diff 能否編譯」未經工具驗證，只以人工閱讀確認 import 與符號引用一致（rbac.go:4 已 import context、:8 已 import time，NoopCache 的三個方法簽章可用；newRBACClient 移除後全 repo 無殘留引用）。 |
| go test | 略過 | 同上，無法執行。新增的 TestService_CacheCheck / TestService_CacheList 是以閱讀方式推導其行為，未實際跑過。 |
| golangci-lint | 略過 | 執行檔存在，但所有 Go linter 都需要先完成型別解析，相依套件不可得，因此無法產出結果。 |
| trivy | 略過 | 未安裝（preflight 確認），略過相依套件漏洞、設定錯誤與 secret 掃描。 |
| opengrep | 略過 | 未安裝（preflight 確認），且本機沒有 semgrep-rules 目錄，略過 SAST 掃描。另外 semgrep-rules 也沒有對應 Go 的規則被選取。 |
| ty | 略過 | 未安裝（preflight 確認）。本次 diff 不含 Python 檔，即使安裝也不會有可歸因的診斷。 |
| oxlint | 略過 | 未安裝（preflight 確認）。本次 diff 不含 JavaScript/TypeScript 檔。 |
| codegraph | 略過 | 未安裝（preflight 確認）。導覽與完整性確認全部改以 grep 進行，涵蓋 newRBACClient、NoopCache、permCache/permDenialCache、getIdentityPermissions 系列函式與 Service struct 的所有建構點。 |
| ncr-fresh-eyes | 略過 | 本執行環境沒有提供任何 subagent 派送工具（無 Agent / Task tool，ToolSearch 亦查無），無法派送。依 SKILL.md Phase 3 的規定不由主 agent 自行模擬，因此本報告缺少一次未被本 skill 框架形塑的獨立視角。 |
| ncr-quality-check | 略過 | 同上，無法派送。report.json 僅通過 report_model.py 的機械驗證，未經第二個 agent 的品質檢查。 |

### Critical

#### F-001 permDenialCache 的 key 不含 identity type，anonymous 與 render service 共用同一組拒絕紀錄 — `pkg/services/authz/rbac/cache.go:30-32`

面向 C 安全 · Critical

**問題**：userPermDenialCacheKey 只吃 (namespace, userUID, action, name, parent)，沒有 identity type。而 SignedInUser.getTypeAndID() 對匿名使用者回傳 (TypeAnonymous, "0")、對 render service 回傳 (TypeRenderService, "0")（identity.go:295-298），也就是這兩種完全不同的主體在 validateSubject 之後拿到的 checkReq.UserUID 都是字串 "0"（repo 自己的測試就以 Subject: "anonymous:0" 與 "render:0" 呼叫，service_test.go:803、853）。service.go:116-121 又把 denial cache 的查詢放在整個 Check 的最前面，早於任何 identity type 的分支，因此一旦匿名使用者被拒絕，同一個 (action, name, folder) 的 render service 請求會在完全不查權限的情況下直接回 Allowed=false。render service 在 getRendererPermissions 是被無條件授予 dashboards:read / folders:read / datasources:read 的萬用權限（service.go:442-450），所以這條路徑上被擋掉的正是它本來一定有的權限。反向的干擾同理成立。已確認的反證方向：permDenialCache 只會寫入 true 且只用於拒絕（service.go:117 忽略取回的值、154 只寫 true），所以這個碰撞不會造成誤放行，影響是誤拒且會在 shortCacheTTL（30 秒，service.go:32）後自癒；即使如此，讓一個主體的授權判斷結果套用到另一個主體，是不應該進入 authz 快取的結構性錯誤。

**證據**：
- `pkg/services/authz/rbac/cache.go:30-32`
- `pkg/services/authz/rbac/service.go:116-121`
- `pkg/services/authz/rbac/service.go:154`
- `pkg/services/user/identity.go:295-298`
- `pkg/services/authz/rbac/service.go:442-450`

**POC**：

````
在啟用 authz 服務的 Grafana 上開啟匿名存取（grafana.ini `[auth.anonymous] enabled = true`），並確認匿名 Viewer 對 dashboard `dash1`（位於 folder `fold1`）沒有讀取權限。

1. 以匿名身分請求該 dashboard，觸發一次被拒絕的 Check（subject=`anonymous:0`, action=`dashboards:read`, name=`dash1`, folder=`fold1`）：

```bash
curl -s -w '%{http_code}\n' \
  'http://localhost:3000/apis/dashboard.grafana.app/v1beta1/namespaces/default/dashboards/dash1'
# 403
```

   此時 service.go:154 寫入 key `default.perm_0_dashboards:read_dash1_fold1`，TTL 30 秒。

2. 在 30 秒內以 render service 身分（renderer 以 `render:0` 認證）觸發同一個 dashboard 的圖片渲染，例如打 render endpoint `http://localhost:3000/render/d-solo/dash1?panelId=1`，或直接讓一則含 dashboard 截圖的告警送出。

3. service.go:117 在任何權限查詢之前就命中步驟 1 留下的 key，回傳 Allowed=false；渲染失敗。等 30 秒後 key 過期即恢復，因此現象是間歇性的。
````

**影響範圍**：一個主體的否定授權判斷會被套用到另一個主體。目前可觀察到的後果是誤拒而非誤放：在任何啟用匿名存取的 Grafana 上，匿名使用者每一次被拒絕的 dashboard/folder 讀取，都會讓 render service 對同一個資源失去讀取權限最多 30 秒，連帶影響圖片渲染、告警截圖與 PDF 匯出；反向也會讓 render service 對非讀取類 action 的拒絕污染匿名使用者的判斷。作用範圍是單一 namespace 內共用該 cache 實例的所有請求（in-proc 模式為 rbac.go:88 建立的 process 內 LocalCache）。PHI 不在本次範圍內，因此不涉及病人資料成本。真正需要在合併前處理的是結構本身：authz 的快取 key 必須能唯一識別發出請求的主體，目前它不能。

**風險處置**：Mitigate（降低）

**修復參考**：pkg/services/authz/rbac/cache.go:30-32 加入 identity type，pkg/services/authz/rbac/service.go:116 傳入 checkReq.IdentityType

**修復方向**：把 identity type 併進 key，並讓它與既有的 permCache 命名慣例一致：

```go
func userPermDenialCacheKey(namespace string, idType types.IdentityType, userUID, action, name, parent string) string {
	return namespace + ".perm_denial_" + string(idType) + "_" + userUID + "_" + action + "_" + name + "_" + parent
}
```

呼叫端（service.go:116）傳入 checkReq.IdentityType。順帶把前綴從 `.perm_` 換成 `.perm_denial_`，讓 denial cache 與 permCache 在同一個底層 cache.Cache 中不再共用命名空間（cache.go:26-32、46-56）。並補一個測試：以 anonymous:0 觸發一次拒絕後，立刻以 render:0 對同一個 dashboard 做 Check，必須仍為 Allowed=true。

<details>
<summary>Suggestion（6）</summary>

#### F-002 denial cache 的 key 空間由請求端的 name/folder 決定，沒有任何上限 — `pkg/services/authz/rbac/service.go:116`

面向 C 安全 · Suggestion

**問題**：checkReq.Name 與 checkReq.ParentFolder 直接來自 req.GetName() / req.GetFolder()（service.go:226-227），完全由呼叫端決定，而這兩個值現在都被編進快取 key。授權檢查發生在資源實際被讀取之前，所以一個名稱不存在的資源同樣會走完 Check 並在 service.go:154 留下一筆新的 cache entry。變更前每個 (user, action) 只會佔用一筆 permCache entry，變更後變成每個 (user, action, name, folder) 一筆。任一登入使用者只要以任意（甚至不存在的）資源名稱連續發送請求，就能在 in-proc 模式的 process 內快取（rbac.go:88，設定只有 Expiry 與 CleanupInterval，沒有任何筆數上限）中不斷產生新的鍵。就算底層實作有筆數上限，結果也只是把真正有用的 permCache/idCache/folderCache 條目擠出去，反而讓這個 MR 想提升的命中率下降——兩種情況都需要處理。已確認的反證方向：authlib 的 cache 實作不在本機（無 module cache、無網路），無法直接讀出它是否有 eviction 上限，因此「記憶體會被耗盡到什麼程度」另列為 Q-001，此處只主張可驗證的部分，也就是 key 基數的變化與本次變更沒有加上任何上限。

**證據**：
- `pkg/services/authz/rbac/service.go:116`
- `pkg/services/authz/rbac/service.go:153-155`
- `pkg/services/authz/rbac/service.go:226-227`
- `pkg/services/authz/rbac.go:88`

**修復方向**：三個方向，擇一或併用：(1) 只在資源確實存在時才寫入 denial 條目，或把 denial cache 限制在已知的 name（例如由呼叫端保證 name 來自已解析的資源）；(2) 為 denial cache 使用一個獨立且有筆數上限的 cache 實例（LRU），與 permCache/idCache 分開，讓它被塞爆時不會波及其他快取；(3) 改以 (user, action) 為單位快取「這個 action 下沒有任何權限」這件事——那是有界的，而 per-resource 的否定結果本來就是最容易被大量製造的那一種。無論選哪個，都建議加一個 metric 觀察 denial cache 的條目數。

#### F-003 新增的 deny 測試即使拿掉 permDenialCache 也會通過，沒有測到短路邏輯 — `pkg/services/authz/rbac/service_test.go:978-990`

面向 G 測試 · Suggestion

**問題**：「Should deny on explicit cache deny entry」的註解寫著「Allow access to the dashboard to prove this is not checked」，但下一行實際塞進 permCache 的是 map[string]bool{"dashboards:uid:dash1": false}（service_test.go:982）。checkPermission 判斷的是 `if scopeMap[t.scope(req.Name)]`（service.go:558），值為 false 等同於沒有權限。實際追一遍：把 permDenialCache 的短路完全移除後，流程會走到 getCachedIdentityPermissions 命中 → checkPermission 得到 false → 落到 getIdentityPermissions → setupService 建立的 fakeStore 沒有設定 userPermissions（service_test.go:1329-1345、1386-1395 回傳 nil 且不報錯）→ scopeMap 為空 → 仍然 Allowed=false。也就是 require.NoError + assert.False 兩個斷言在有無 denial cache 的情況下都成立，這個測試目前只驗證了「沒有權限就會被拒絕」，而那是既有行為。

**證據**：
- `pkg/services/authz/rbac/service_test.go:978-990`
- `pkg/services/authz/rbac/service.go:558-560`
- `pkg/services/authz/rbac/service_test.go:1352-1396`

**修復方向**：把 service_test.go:982 的值改成 true，讓 permCache 真的授予 dash1，這樣一旦短路失效測試就會失敗：

```go
s.permCache.Set(ctx, userPermCacheKey("org-12", "test-uid", "dashboards:read"),
	map[string]bool{"dashboards:uid:dash1": true})
```

同時建議補兩個案例：(a) denial 條目寫入後，改用另一個 identity type（render:0 / anonymous:0）對同一個 (action, name, folder) 做 Check，用來釘住 F-001；(b) List 路徑不受 permDenialCache 影響——目前 List 完全沒有讀寫 denial cache，這個不對稱值得有測試明講它是刻意的。

#### F-004 getCachedIdentityPermissions 複製了 identity type switch，快取 key 的組法散在三處 — `pkg/services/authz/rbac/service.go:320-340`

面向 B 簡潔 · Suggestion

**問題**：getCachedIdentityPermissions（342-368）與 getIdentityPermissions（320-340）是同一個 switch 的兩份副本，而 userPermCacheKey 現在被 getCachedIdentityPermissions:360 與 getUserPermissions:379 各算一次、anonymousPermCacheKey 被 getCachedIdentityPermissions:348 與 getAnonymousPermissions:423 各算一次。這裡有一個已經成立但沒有寫下來的隱性不變式：getIdentityPermissions 會針對 action == "folders:create" 補上 actionSets（325-328），getCachedIdentityPermissions 完全沒有這段邏輯，之所以目前沒有問題，是因為快取 key 只含 action、而寫入該 key 的 scopeMap 本來就是帶著 actionSets 查出來的結果。任何人日後把 actionSets 併進 key、或新增一個依 identity type 而異的快取前綴，就必須同時改到四個地方，而漏改的症狀是靜默的快取未命中（效能退化）或讀到別的 identity type 的資料。

**證據**：
- `pkg/services/authz/rbac/service.go:320-340`
- `pkg/services/authz/rbac/service.go:342-368`
- `pkg/services/authz/rbac/service.go:379`
- `pkg/services/authz/rbac/service.go:423`

**修復方向**：抽出單一的 key 產生函式，讓讀與寫共用：

```go
func (s *Service) permCacheKeyFor(ns types.NamespaceInfo, idType types.IdentityType, userID, action string) (string, error) {
	switch idType {
	case types.TypeAnonymous:
		return anonymousPermCacheKey(ns.Value, action), nil
	case types.TypeUser, types.TypeServiceAccount:
		ids, err := s.GetUserIdentifiers(ctx, ns, userID)
		if err != nil { return "", err }
		return userPermCacheKey(ns.Value, ids.UID, action), nil
	}
	...
}
```

或者更小的改動：讓 getUserPermissions / getAnonymousPermissions 接受一個 cacheOnly bool，由它們自己決定要不要落到 store，這樣 switch 就只剩一份。另外，請在 getCachedIdentityPermissions 上方補一行註解說明「快取只用來放行、不用來拒絕」這個設計前提，那是整個 Check 流程正確性的核心，目前只存在於程式碼的形狀裡。

#### F-005 拿掉 getUserPermissions 的讀快取後，每一次無法由快取直接放行的 Check 都會完整查一次資料庫 — `pkg/services/authz/rbac/service.go:139-144`

面向 F 資料取用與資料庫 · Suggestion

**問題**：變更前 getUserPermissions 開頭會讀 permCache，命中就直接回傳，不論結果是允許還是拒絕；變更後那段讀取被移除（service.go:379 之後直接進 sf.Do），getAnonymousPermissions 同理（423）。因此 Check 只要走到 139 行，就一定會執行 getUserBasicRole + getUserTeams + permissionStore.GetUserPermissions 一整輪。singleflight（380）只收斂同一瞬間併發的相同 key，序列化抵達的請求每一個都會打到 store。permDenialCache 只能吸收「同一個 (user, action, name, folder) 的第二次以後」，第一次仍然是一次完整查詢；而 List 路徑（180-192）根本沒有 denial cache，所以只要 permCache 沒命中就一定查庫。一個會逐一檢查大量資源的 UI，資料庫查詢量會隨「被拒絕的相異資源數」線性成長。這是作者刻意的取捨（commit 訊息就寫著 "fetch perms if not in either of the caches"），問題不在方向而在於這個 MR 沒有留下任何可以觀察這個成本的手段。

**證據**：
- `pkg/services/authz/rbac/service.go:139-144`
- `pkg/services/authz/rbac/service.go:379-410`
- `pkg/services/authz/rbac/service.go:423-432`
- `pkg/services/authz/rbac/service.go:180-192`

**修復方向**：至少讓它可被量測：在 permissionCacheUsage 上加一個維度（或新增一個 counter）區分「permCache 命中但未放行、因此仍落到 store」這一類，否則上線後只會看到一個下降的 cache hit rate 卻無法歸因（這一點與 F-006 是同一處程式碼）。若量測後確認負載無法接受，可考慮以 (user, action) 為單位快取「本次查庫的結果不含任何可用權限」，取代 per-resource 的否定快取——那同時也解掉 F-002 的無界問題。另外建議在 MR 描述中記錄 before/after 的 permissionStore 查詢率，讓下一個維護者知道這個取捨被量過。

#### F-006 permissionCacheUsage 的 cache_hit 標籤與實際發生的事不符，且混用了兩個不同的快取 — `pkg/services/authz/rbac/service.go:117-121`

面向 A 風格 · Suggestion

**問題**：service.go:123 的 getCachedIdentityPermissions 命中、但 checkPermission 判定為不允許時，控制流會跳出 131-135 的 if，落到 137 行的 permissionCacheUsage.WithLabelValues("false", ...)。也就是一次真正的快取命中會被記成 cache_hit="false"。同一個 metric 在 118 行還被 permDenialCache 的命中遞增了一次，於是 cache_hit="true" 混合了兩個語意不同的快取（權限集合快取與否定結果快取），cache_hit="false" 則同時包含「permCache 未命中」與「permCache 命中但沒放行」。這個 metric 是判斷本次 MR 是否達成目的的唯一訊號，而它現在正好在最需要區分的地方失真：這個 MR 想改善的就是「命中但被拒絕」這一類請求的行為。

**證據**：
- `pkg/services/authz/rbac/service.go:117-121`
- `pkg/services/authz/rbac/service.go:131-137`
- `pkg/services/authz/rbac/metrics.go:29-37`

**修復方向**：把兩件事拆開記錄。例如把 metrics.go:29-37 的標籤擴成 []string{"cache", "outcome", "action"}，並在三個位置分別記 ("denial", "hit", action)、("permission", "hit_allowed", action)、("permission", "hit_denied", action)、("permission", "miss", action)；或維持現有 metric 但新增一個獨立的 denial cache counter，並把 137 行移進 else 分支，只在 getCachedIdentityPermissions 真的回傳錯誤時才遞增。改標籤結構會影響既有的 dashboard 與告警，請一併確認有沒有下游查詢在使用 iam_authz_direct_db_service_permission_cache_usage。

#### F-007 denial cache key 以未跳脫的底線串接 name 與 parent，而 Grafana UID 允許底線 — `pkg/services/authz/rbac/cache.go:30-32`

面向 C 安全 · Suggestion

**問題**：userPermDenialCacheKey 以 "_" 串接 action、name、parent，而 pkg/util/shortid_generator.go:33 的 validUIDCharPattern 是 `a-zA-Z0-9\-\_`，底線是合法的 UID 字元（repo 自己的測試就在用 dashboards:uid:some_dashboard，service_test.go:345）。因此 (name="a_b", parent="c") 與 (name="a", parent="b_c") 會產生完全相同的 key，一個資源的拒絕紀錄會套用到另一個資源上最多 30 秒。已確認的反證方向：這只會造成誤拒不會造成誤放（denial cache 只寫 true 且只用於拒絕）；而 denial key 與 permCache key 之間的碰撞不會回傳錯誤答案，因為兩個 cacheWrap 的型別參數不同（cache.go:46-56），json.Unmarshal 失敗會被當成未命中（cache.go:70-74）。真正會發生的是 denial 對 denial 的碰撞，需要一個名稱剛好可以在不同位置切開的巧合，因此列為 Suggestion 而非 Critical。

**證據**：
- `pkg/services/authz/rbac/cache.go:30-32`
- `pkg/util/shortid_generator.go:33-34`
- `pkg/services/authz/rbac/service_test.go:345`

**修復方向**：改用不可能出現在 UID 中的分隔字元，或直接以長度前綴消除歧義：

```go
func userPermDenialCacheKey(namespace string, idType types.IdentityType, userUID, action, name, parent string) string {
	return namespace + ".perm_denial|" + string(idType) + "|" + userUID + "|" + action + "|" + name + "|" + parent
}
```

（`|` 不在 validUIDCharPattern 內。）這一項與 F-001 改的是同一個函式，建議一起處理。

</details>

<details>
<summary>Nit（2）</summary>

#### F-008 NoopCache 不必匯出，receiver 名稱是舊實作的殘留，且缺少說明為何需要它的註解 — `pkg/services/authz/rbac.go:239-251`

面向 A 風格 · Nit

**問題**：NoopCache 是匯出型別，但 grep 全 repo 只有 rbac.go:103 這一個使用點，套件外沒有任何引用。三個方法的 receiver 都叫 lc，那是被它取代的 local cache 留下的名字，讀起來會讓人以為還有一個 local cache。匯出型別按 Go 慣例需要 doc comment，目前沒有——而這裡恰恰是最需要一行說明的地方：讀者看到「刻意不快取」時，第一個問題會是為什麼，答案（in-proc channel 後面的 Service 已經自己持有快取，client 端再放一層會讓陳舊時間疊加）不在程式碼裡。

**證據**：
- `pkg/services/authz/rbac.go:239-251`
- `pkg/services/authz/rbac.go:101-105`

**修復方向**：改成未匯出並補註解：

```go
// noopCache disables the authz client's own result cache. The in-proc client
// talks to a Service that already caches permissions itself (see
// rbac.NewService); caching again on the client would stack a second staleness
// window on top of it.
type noopCache struct{}

func (c *noopCache) Get(ctx context.Context, key string) ([]byte, error) { return nil, cache.ErrNotFound }
```

若 authzlib 本身已提供 no-op 實作，直接用它會更好；本機沒有 authlib 原始碼可確認，這一點順帶查一下。

#### F-009 TestService_getUserPermissions 的 cacheHit 欄位與兩個分支已成死碼 — `pkg/services/authz/rbac/service_test.go:337`

面向 G 測試 · Nit

**問題**：唯一設定 cacheHit: true 的案例在本次 diff 中被移除，剩下的兩個案例都是 false。於是 367-369 的 permCache.Set 永遠不會執行，388-392 中 `require.Equal(t, 1, store.calls)` 那一臂也永遠到不了，testCase 的 cacheHit 欄位變成純粹的雜訊，並且會讓下一個讀者誤以為這裡還有快取命中的覆蓋。

**證據**：
- `pkg/services/authz/rbac/service_test.go:337`
- `pkg/services/authz/rbac/service_test.go:367-369`
- `pkg/services/authz/rbac/service_test.go:388-392`

**修復方向**：刪掉 cacheHit 欄位與兩處分支，只留 `require.Equal(t, 3, store.calls)`；或者更好的做法是改寫一個對應新結構的案例——測 getCachedIdentityPermissions 在 permCache 命中時只呼叫一次 store（取 user id），這樣原本那個案例想守住的行為就搬到了它現在真正所在的位置。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 authlib 的 cache.NewLocalCache 有沒有條目數上限？rbac.go:88 設定的 CleanupInterval: 10 * time.Minute 是否代表 30 秒就過期的 denial 條目最長會在記憶體中滯留 10 分鐘？

面向 C 安全

**背景**：F-002 主張 denial cache 的 key 空間由請求端決定且本次變更沒有加上任何上限，這部分只依賴本 repo 的程式碼即可確認。但它會不會真的造成記憶體耗盡，取決於 github.com/grafana/authlib@v0.0.0-20250325095148 的 cache 實作是否有 eviction 上限、以及過期條目的回收時機。本機沒有 Go module cache 也沒有網路，該套件的原始碼無法取得，因此沒有把記憶體耗盡當成 F-002 的主張。

**如何確認**：讀 github.com/grafana/authlib/cache 的 LocalCache 實作，確認 cache.Config 是否有筆數上限欄位、以及 CleanupInterval 的語意；或在測試環境以大量相異且必定被拒絕的資源名稱持續打 Check，觀察該 process 的 RSS 與 cache 條目數。

#### Q-002 in-proc client 拿掉 client-side 結果快取（改用 NoopCache）之後，單一 binary 的 Grafana 在 authorizer 熱路徑上的延遲是否仍在預算內？

面向 F 資料取用與資料庫

**背景**：rbac.go:101-105 讓 in-proc client 不再快取 Check 結果，因此每一次授權都會穿過 inprocgrpc channel 重新進入 Service.Check。搭配 F-005（快取無法直接放行時一定查庫），單次請求的成本上限比變更前高。這是刻意的設計（commit "remove the use of client side cache for in-proc authz client"），方向也合理——兩層快取會讓陳舊時間疊加——但 diff 與 commit 訊息中都沒有任何量測數據，而 in-proc 模式涵蓋的是所有非 cloud 部署。

**如何確認**：在單一 binary 的 Grafana 上做變更前後的對照量測：authorizer 的 Check p50/p99 延遲，以及 permissionStore 的每秒查詢數，在一個會大量檢查 dashboard 權限的情境（例如 dashboard 清單頁）下比較。

#### Q-003 denial cache 預期要在權限異動時被失效，還是 30 秒的 shortCacheTTL 就是完整的契約？

面向 F 資料取用與資料庫

**背景**：grep 全 repo，permCache 與 permDenialCache 都只有 Get / Set，沒有任何 Delete 或失效路徑（service.go:349、361、406、430、117、154 是全部的使用點）。也就是新授予的權限只靠 TTL 到期生效。追過一遍後可以確定這次沒有把陳舊時間變長：寫入 denial 條目時（service.go:154）用的 permissions 是剛從 store 查回來的（getUserPermissions 已不再讀 permCache），所以最壞情況仍是 teamCache/basicRoleCache 的 30 秒加上一層 30 秒，與變更前 permCache 的疊加相同。因此這不是一個 finding。但 denial cache 把同樣的策略推進到「單一資源」這個粒度，而那正是使用者最容易察覺的一種（剛被授權卻仍然 403），值得團隊明確表態。

**如何確認**：團隊給出權限授予到生效的預期傳播時間，並確認 30 秒是刻意選的上限而非預設值；若需要更快，就要有一條在權限異動時主動失效 permCache 與 permDenialCache 的路徑。

</details>
