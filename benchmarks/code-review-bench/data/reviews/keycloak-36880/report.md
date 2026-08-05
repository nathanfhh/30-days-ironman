## 審查結論：Request Changes

> Critical 1 · Suggestion 4 · Nit 6 · 未驗證提問 3
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
| ❌ | — | ❌ |

- **A 風格**（未通過）：新增的 javadoc 有兩處把 void 的 require 方法寫成「Returns true」（F-007）；另有無意義的空白變更（F-009）。其餘命名與 V1 對齊，可讀性沒有問題。
- **B 簡潔**（未通過）：ClientPermissionsV2 從 ClientPermissions 抄過來的死碼未清（F-006），同一段 scope 比對迴圈重複三次（F-008）。
- **D API 慣例**（不適用）：本次沒有新增或修改任何 HTTP endpoint、URL、request/response schema 或 HTTP verb；改動全部在 admin API 背後的權限判斷層。API 慣例檢查沒有可檢查的對象。
- **E 架構**（未通過）：getClientsWithPermission 依賴一個從未被寫入的 resource type（F-002）；AdminPermissionsSchema.getResourceName 沒有跟著新增 CLIENTS 分支（F-003）；同一套評估邏輯在 UserPermissionsV2 與 ClientPermissionsV2 之間出現不一致的 filter（F-005）。
- **F 資料取用與資料庫**（未通過）：hasPermission 的 all-clients fallback 會把 null 傳進契約上不接受 null 的 PolicyStore.findByResource（F-004）。此外資料面沒有 schema migration、沒有 DDL、沒有多步寫入，其餘項目不適用。
- **G 測試**（未通過）：新增的整合測試品質不錯——每個案例都先驗證未授權時是 ForbiddenException / 403，授權後才驗證實際操作成功，是在檢查真實行為而不是「有回應就好」。扣分在共用狀態沒有還原（F-011）。AbstractPermissionTest 的 helper 簽章改動已用 grep 確認四個子類別全部更新完畢，沒有遺漏的呼叫端。
- **H 非 Python 檔**（不適用）：本次 diff 全部是 Java（10 個檔案）。H 點名的類別——多分支 UI 元件、Vue、Dockerfile、nginx.conf、docker-compose、Alembic migration——一個都沒有出現。Java 部分的審查由 A–G 與 I 承擔。
- **I 回溯分析**（未通過）：MgmtPermissionsV2.clients() 新增 override 之後，原本走 V1 實作的既有呼叫端改走 ClientPermissionsV2，其中一條路徑會踩到 UnsupportedOperationException（F-001）。測試 helper 的簽章遷移則已確認完整。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 實際上綁了三件可以各自審查的事：(1) Clients resource type 與 ClientPermissionsV2；(2) AbstractPermissionTest 共用 helper 由 instance 改 static 並加參數，連帶改動三個與 Clients 無關的測試類別；(3) AdminPermissions.registerListener 整段加上 ADMIN_FINE_GRAINED_AUTHZ 判斷（AdminPermissions.java:77），這會改變 V1 與「兩個 feature 都沒開」兩種部署的既有行為。第 (3) 項是行為變更而非重構，夾在大量 javadoc 與測試搬移之中很容易被讀過去，建議至少在 MR 描述裡單獨點名，或拆成獨立 commit。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），略過相依套件漏洞、misconfig 與 secret 掃描。本次沒有任何自動化的供應鏈或憑證檢查覆蓋。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且 NCR_OPENGREP_RULES 規則目錄不存在。本次沒有 SAST 覆蓋。 |
| ruff | 已執行 | in_diff 0、outside_diff 6 |
| ty | 略過 | ty 未安裝（不在 PATH 上）。即使安裝了也不適用：本次 diff 全是 Java。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。即使安裝了也不適用：本次 diff 沒有 JavaScript / TypeScript 檔案。 |
| java-toolchain | 略過 | 環境沒有可用的 Java 編譯器/靜態檢查工具，Maven 也沒有網路可取得相依，因此本次沒有編譯、沒有跑測試、也沒有任何 linter 掃過這份 diff 的 Java 程式碼。ruff 的執行結果（0 in-diff）不代表這份 diff 乾淨——它掃的是 Python，而本次 diff 一行 Python 都沒有。以下所有發現都來自人工閱讀原始碼與 grep 導覽，請以此理解其覆蓋範圍。 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號索引；Phase 3 的呼叫路徑列舉全部改用 grep 逐一確認。 |
| ncr-fresh-eyes | 略過 | 無法派工：這個執行環境沒有可建立 subagent 的工具。依 SKILL.md，主 agent 不得自行模擬 fresh eyes（此時它已讀完整份 skill，看法必然被框架塑形），因此本次審查缺少一次獨立的初讀觀察。 |
| ncr-quality-check | 略過 | 無法派工，原因同上。本報告的發佈前四項規則由撰寫者自行複核，沒有第二雙眼睛做只讀檢查。 |

### Critical

#### F-001 MgmtPermissionsV2.clients() 改指向 V2 之後，token exchange 會丟 UnsupportedOperationException — `services/src/main/java/org/keycloak/services/resources/admin/permissions/MgmtPermissionsV2.java:63-68`

面向 I 回溯分析 · Critical

**問題**：這個 MR 新增 MgmtPermissionsV2.clients() override（MgmtPermissionsV2.java:63-68），於是在 ADMIN_FINE_GRAINED_AUTHZ_V2 開啟時，clients() 從 V1 的 ClientPermissions 換成了 ClientPermissionsV2，而 ClientPermissionsV2.canExchangeTo() 是直接 throw new UnsupportedOperationException("Not supported in V2")。

可達性已逐段確認：AbstractTokenExchangeProvider.exchangeClientToClient() 在 audience 是另一個 client 時會呼叫 AdminPermissions.management(session, realm).clients().canExchangeTo(client, targetClient, token)（AbstractTokenExchangeProvider.java:276），而 exchangeClientToClient 是由 V1TokenExchangeProvider.java:154 進來的。V1TokenExchangeProviderFactory.isSupported() 只看 Profile.Feature.TOKEN_EXCHANGE（V1TokenExchangeProviderFactory.java:60）。

關鍵在於 TOKEN_EXCHANGE 是獨立的 feature key：Profile.java:76 定義為 TOKEN_EXCHANGE("Token Exchange Service", Type.PREVIEW, 1)，unversioned key 是 "token-exchange"，沒有宣告任何 dependency，和 "admin-fine-grained-authz" 是完全不同的 feature 群組。Profile.configure() 只保證「同一個 unversioned key 底下最多一個版本生效」（features.put(f, f == enabledFeature)），不會阻止 token-exchange 與 admin-fine-grained-authz:v2 同時開啟，verifyConfig() 也不會擋。所以 `--features=token-exchange,admin-fine-grained-authz:v2` 是一組合法設定。

已找過反證：ClientResource 上另外兩個會呼叫 V2 拋例外方法的入口（isPermissionsEnabled / resource / getPermissions / setPermissionsEnabled）都先做 ProfileHelper.requireFeature(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ)（ClientResource.java:706、739），在 V2 之下根本進不去；作者也已經注意到 AdminPermissions.registerListener 這條路而補上了 feature 判斷。但 token exchange 這條沒有任何 FGAP 相關的 guard。至於 TOKEN_EXCHANGE_STANDARD_V2 / FEDERATED_V2 / SUBJECT_IMPERSONATION_V2，它們宣告 dependency 為 ADMIN_FINE_GRAINED_AUTHZ（Profile.java:77-79），確實無法與 V2 並存——但 V1 的 token-exchange 可以。

本 MR 之前 MgmtPermissionsV2 沒有 override clients()，會落到 MgmtPermissions.clients()（MgmtPermissions.java:221-225）回傳 V1 實作並正常做出判斷，所以這是本次引入的回歸。實際結果是 token endpoint 冒出未被攔截的 RuntimeException（HTTP 500），而不是 403 或放行。

**證據**：
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/MgmtPermissionsV2.java:63-68`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:147-150`
- `services/src/main/java/org/keycloak/protocol/oidc/tokenexchange/AbstractTokenExchangeProvider.java:276`
- `services/src/main/java/org/keycloak/protocol/oidc/tokenexchange/V1TokenExchangeProvider.java:154`
- `services/src/main/java/org/keycloak/protocol/oidc/tokenexchange/V1TokenExchangeProviderFactory.java:60`
- `common/src/main/java/org/keycloak/common/Profile.java:76`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/MgmtPermissions.java:221-225`

**修復方向**：三個方向擇一：

1. 讓 ClientPermissionsV2.canExchangeTo(...) 委派回 V1 行為（super.canExchangeTo(...)），因為 token exchange 的授權模型目前仍由 V1 的 client resource / TOKEN_EXCHANGE scope 承擔，V2 並沒有取代它。
2. 若確定 V2 之下不該支援 token exchange，就在 AbstractTokenExchangeProvider.exchangeClientToClient() 呼叫 canExchangeTo 之前顯式判斷，回 403（CorsErrorResponseException / ACCESS_DENIED）而不是讓例外冒出去。
3. 在 Profile.Feature 上把 TOKEN_EXCHANGE 與 ADMIN_FINE_GRAINED_AUTHZ_V2 宣告成互斥，讓錯誤的組合在啟動時就失敗，而不是在執行期。

無論選哪個，建議補一個測試：同時開 token-exchange 與 admin-fine-grained-authz:v2，對另一個 audience client 發一次 token exchange 請求，斷言回應不是 5xx。

<details>
<summary>Suggestion（4）</summary>

#### F-002 getClientsWithPermission 依賴的 resource type 在 V2 從來沒被寫入，回傳值永遠不是 client id — `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:125-145`

面向 E 架構 · Suggestion

**問題**：ClientPermissionsV2.getClientsWithPermission() 用 resourceStore.findByType(server, CLIENTS_RESOURCE_TYPE, ...) 找資源，再把 resource.getName() 當成 client id 收集起來（ClientPermissionsV2.java:138-142）。但 V2 底下單一 client 的 Resource 是在 AdminPermissionsSchema.getOrCreateResource() 建立的，那裡只做 resourceStore.create(resourceServer, name, owner) 加 updateScopes，從頭到尾沒有呼叫過 setType（AdminPermissionsSchema.java:103-111）。全 repo grep 下來，AdminPermissionsSchema 裡唯一一次 resource.setType() 在 init() 的 all-resource 建立處（AdminPermissionsSchema.java:250），而那個 resource 的 name 就是字面的 "Clients"。

所以 findByType(server, "Clients") 只會撈到 all-clients 那一筆，getClientsWithPermission 的回傳值不是空集合就是 {"Clients"}，永遠不會是真正的 client id。對照 V1：ClientPermissions.initialize() 明確做了 resource.setType("Client")（ClientPermissions.java:132），取用時再把 "client.resource." 前綴切掉（ClientPermissions.java:682），兩邊配套是完整的。

唯一的消費者 AvailableRoleMappingResource.getRoleIdsWithPermissions() 拿到之後直接 realm.getClientById(cid).getRolesStream()，沒有 null 檢查（AvailableRoleMappingResource.java:229-230），"Clients" 查不到 client 就是 NPE。

已找過反證，結論是目前打不到：getRoleIdsWithPermissions 的五個呼叫點（AvailableRoleMappingResource.java:77、114、151、190、221）全部位於 `!Profile.isFeatureEnabled(ADMIN_FINE_GRAINED_AUTHZ)` 的 else 分支，也就是需要 V1 開啟；而 ADMIN_FINE_GRAINED_AUTHZ 與 ADMIN_FINE_GRAINED_AUTHZ_V2 是同一個 unversioned key 的兩個版本（Profile.java:56、58），Profile.configure() 的 features.put(f, f == enabledFeature) 保證同時只有一個生效，ClientPermissionsV2 又只在 V2 之下被建立。因此這個 override 現階段是不可達的。這正是它值得被指出的原因：它不會壞，也不會被任何測試覆蓋，等到 V1 移除、那些 guard 翻成 V2 的那天才會爆。

**證據**：
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:125-145`
- `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:103-111`
- `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:246-252`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissions.java:132`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissions.java:680-682`
- `rest/admin-ui-ext/src/main/java/org/keycloak/admin/ui/rest/AvailableRoleMappingResource.java:227-231`

**修復方向**：兩個方向：

1. 若打算讓它真的可用，就在 AdminPermissionsSchema.getOrCreateResource() 建立單一物件 resource 時補上 resource.setType(resourceType)，讓 findByType 撈得到；同時在 getClientsWithPermission 裡排除 name 等於 resource type 字面值的那一筆 all-clients resource，並在 AvailableRoleMappingResource.getRoleIdsWithPermissions() 加上 null 過濾（`.map(realm::getClientById).filter(Objects::nonNull)`）。
2. 若這一版還不打算支援，就先讓它 throw new UnsupportedOperationException("Not supported in V2")，和同檔案其他尚未支援的方法一致，或至少加註解說明它是等 V1 退場後才會啟用的預留實作。目前這種「寫了、但語意是錯的、而且沒人呼叫得到」是最難維護的狀態。

#### F-003 新增 CLIENTS resource type 但 getResourceName 沒有跟著加分支，admin console 會顯示 null — `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:304-326`

面向 E 架構 · Suggestion

**問題**：AdminPermissionsSchema.getResourceName(session, policy, resource) 是 permission 詳細頁把 Resource 轉成人看得懂的名字的唯一來源——PolicyResourceService.getResources() 用它填 representation.setDisplayName()（PolicyResourceService.java:218）。這個方法目前只有 USERS 分支：all-users resource 回 "All users"，單一 user resource 查 username（AdminPermissionsSchema.java:310-322）。

這個 MR 加了 CLIENTS resource type，但沒有加對應分支，於是 Clients 類型的 permission 會落到最後一行 `return resource.getDisplayName()`。而 FGAP 的 resource 是用 resourceStore.create(resourceServer, name, owner) 建立的（AdminPermissionsSchema.java:104），只有 name 沒有 displayName，所以回傳的是 null。結果是：all-clients 顯示 null 而不是「All clients」，單一 client 顯示 null 而不是 clientId（resource 的 name 是 client 的 UUID，對使用者沒有意義）。USERS 分支存在的理由就是這件事。

這一項屬於「這個 diff 裡缺了一塊」的主張，已在同一個 diff 內找過補在別處的可能：diff 的 10 個檔案裡沒有任何一處處理 Clients 的顯示名稱，js/ 目錄也沒有被動到。

**證據**：
- `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:304-326`
- `services/src/main/java/org/keycloak/authorization/admin/PolicyResourceService.java:218`
- `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:104`

**修復方向**：在 getResourceName() 的 USERS 分支後面補上對稱的 CLIENTS 分支：

```java
if (CLIENTS.getType().equals(resourceType)) {
    if (resource.getName().equals(CLIENTS_RESOURCE_TYPE)) {
        return "All clients";
    }
    ClientModel client = session.clients().getClientById(session.getContext().getRealm(), resource.getName());
    if (client == null) {
        throw new ModelIllegalStateException("Client not found for resource [" + resource.getId() + "]");
    }
    return client.getClientId();
}
```

順帶一提，這裡的 if / throw 結構已經要被複製第二次了，可以考慮抽成 `resolveDisplayName(resourceType, resource)` 之類的 dispatch，避免第三個 resource type 進來時再抄一次。

#### F-004 all-clients fallback 會把 null 傳進契約上不接受 null 的 PolicyStore.findByResource — `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:215-222`

面向 F 資料取用與資料庫 · Suggestion

**問題**：hasPermission(ClientModel, String) 在找不到單一 client 的 resource 時，會 fallback 去拿 all-clients resource，然後直接把它交給 policyStore.findByResource：

```java
resource = AdminPermissionsSchema.SCHEMA.getResourceTypeResource(session, server, CLIENTS_RESOURCE_TYPE);
if (authz.getStoreFactory().getPolicyStore().findByResource(server, resource).isEmpty()) {
```
（ClientPermissionsV2.java:217-219）

getResourceTypeResource() 有三條回傳 null 的路徑（AdminPermissionsSchema.java:117-133），其中一條是「這個 resource server 裡根本沒有名為 Clients 的 resource」。而 PolicyStore.findByResource 的 javadoc 明寫 resource「Cannot be null」（PolicyStore.java:112），JPA 實作第一件事就是 resource.getId()（JPAPolicyStore.java:215）——傳 null 就是 NPE。

這條路徑在既有 realm 上是真的會走到的：AdminPermissionsSchema.init() 在 admin-permissions client 已存在時直接 return（AdminPermissionsSchema.java:226-228），而它只有兩個呼叫點——realm 把 adminPermissionsEnabled 從 false 翻成 true 的那一刻（JpaClientProviderFactory.java:73），以及 realm import（RealmManager.java:572）。也就是說，在這個 MR 之前就已經開啟 FGAP V2 的 realm，resource server 裡沒有 Clients resource，也沒有 configure / map-roles-client-scope / map-roles-composite 這三個新 scope，升級之後不會補上。這種 realm 上每一次 canManage(client) / canView(client) 都會走進上面那三行。

這裡刻意只把「缺 null 檢查」列為發現：升級路徑該不該補 schema 是產品決策（V2 仍是 EXPERIMENTAL），列在 Q-001。但無論那個決策怎麼定，把 null 送進一個契約上不收 null 的方法都應該修。UserPermissionsV2.java:119-124 有一模一樣的形狀，修的時候可以一併處理。

**證據**：
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:215-222`
- `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:116-134`
- `server-spi-private/src/main/java/org/keycloak/authorization/store/PolicyStore.java:110-115`
- `model/jpa/src/main/java/org/keycloak/authorization/jpa/store/JPAPolicyStore.java:211-216`
- `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:222-228`
- `model/jpa/src/main/java/org/keycloak/models/jpa/JpaClientProviderFactory.java:66-77`

**修復方向**：在 fallback 之後補一個 null 檢查，讓「resource type resource 不存在」直接判定為無權限而不是 NPE：

```java
resource = AdminPermissionsSchema.SCHEMA.getResourceTypeResource(session, server, AdminPermissionsSchema.CLIENTS_RESOURCE_TYPE);

if (resource == null || authz.getStoreFactory().getPolicyStore().findByResource(server, resource).isEmpty()) {
    return false;
}
```

同樣的一行也建議補進 UserPermissionsV2.hasPermission()。

#### F-005 ClientPermissionsV2.hasPermission 少了 UserPermissionsV2 特意加上的 resourceId 過濾 — `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:224-232`

面向 E 架構 · Suggestion

**問題**：兩個 V2 evaluator 的 hasPermission 幾乎是同一段程式，但比對迴圈不一樣。UserPermissionsV2 在展開 scope 之前先確認這筆 Permission 屬於它送去評估的那個 resource：

```java
for (Permission permission : permissions) {
    if (permission.getResourceId().equals(resource.getId())) {
        for (String scope : permission.getScopes()) { ... }
    }
}
```
（UserPermissionsV2.java:134-142）

ClientPermissionsV2 沒有這一層（ClientPermissionsV2.java:226-232）。

這個過濾不是隨手寫的：git 追下來它是 commit c2acddc「Update FGAP v2 to not grant permissions of all users when permission is granted only for a single user / Closes #36838」為了修一個實際回報的越權問題而加的，而且在最新的 bf355f8（Closes #37081）重寫 fallback 之後仍然保留。新的 Clients 版本等於複製了修補之前的形狀。

我沒有把它列成 Critical，因為找不到能證明它會改變結果的路徑：DecisionPermissionCollector.grantPermission() 在 resource != null 時是用 permission.getResource() 建 Permission 的，回傳集合裡每一筆的 resourceId 都等於送進去的那個 resource，這個 if 看起來恆真。既然無法證明有洞，就不給 Critical——但兩份幾乎相同、只在一個安全相關的過濾上不一致的 evaluator，本身就是會被繼續往下複製的問題。

**證據**：
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:224-232`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/UserPermissionsV2.java:134-142`

**修復方向**：擇一並留下痕跡：

1. 把同樣的 `if (permission.getResourceId().equals(resource.getId()))` 補進 ClientPermissionsV2.hasPermission(ClientModel, String)，讓兩份實作一致；或
2. 若已確認這個判斷恆真、Users 那邊也可以拿掉，就在同一個 PR 裡兩邊一起移除，並在 commit message 引用 #36838 說明為什麼現在不需要了。

第 1 個成本較低、風險較小。真正要避免的是兩邊長期不一致而沒有任何說明。

</details>

<details>
<summary>Nit（6）</summary>

#### F-006 ClientPermissionsV2 從 V1 抄過來的死碼未清 — `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:262-272`

面向 B 簡潔 · Nit

**問題**：新檔案裡有幾樣東西是從 ClientPermissions 一起帶過來但用不到的：

- private EvaluationContext getEvaluationContext(ClientModel, AccessToken)（ClientPermissionsV2.java:262-272）全檔案沒有任何呼叫點。它原本是給 canExchangeTo 用的，而 canExchangeTo 在 V2 直接 throw。
- logger（ClientPermissionsV2.java:51）宣告後從未使用（全檔 "logger" 只出現這一次）。
- import org.keycloak.authorization.model.Scope（第 27 行）在檔案裡只出現這一次，是純粹未使用的 import。
- static import AdminPermissionManagement.TOKEN_EXCHANGE（第 46 行）同樣只出現這一次。
- 拿掉 getEvaluationContext 之後，ClientModelIdentity、DefaultEvaluationContext、EvaluationContext 三個 import 也會一起變成未使用。

順帶說明：這幾項不會被自動檢查抓到——repo 根目錄的 pom.xml 沒有 checkstyle / spotless / formatter 外掛，.editorconfig 對 *.java 也只設了 insert_final_newline 與 import 規則。所以只能靠 review 指出。

**證據**：
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:262-272`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:51`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:27`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:46`

**修復方向**：刪掉 getEvaluationContext()、logger 欄位，以及 Scope、TOKEN_EXCHANGE、ClientModelIdentity、DefaultEvaluationContext、EvaluationContext 這幾個 import。若之後 canExchangeTo 要改成委派 V1（見 F-001），getEvaluationContext 也是由 super 提供，這裡仍然不需要自己一份。

#### F-007 兩處新增的 javadoc 把 void 的 require 方法寫成「Returns true」 — `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionEvaluator.java:95-98`

面向 A 風格 · Nit

**問題**：這次補的 javadoc 整體很有價值——把每個方法在 V1 / V2 各自吃哪些角色與 scope 講清楚了，是這份 diff 裡讀起來最有幫助的部分。但有兩處寫反了：

```java
/**
 * Returns {@code true} if {@link #canView()} returns {@code true}.
 */
void requireView();

/**
 * Returns {@code true} if {@link #canViewClientScopes()} returns {@code true}.
 */
void requireViewClientScopes();
```

兩個都是 void 而且語意是「不通過就丟例外」。同一個檔案裡其他所有 require* 的寫法都是「Throws ForbiddenException if X returns false」（例如 requireManage、requireConfigure、requireView(ClientModel)），只有這兩個不一致。javadoc 是這次唯一的行為說明來源，寫反會直接誤導下一個實作者。

**證據**：
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionEvaluator.java:95-98`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionEvaluator.java:100-103`

**修復方向**：改成與相鄰項目一致：

```java
/**
 * Throws ForbiddenException if {@link #canView()} returns {@code false}.
 */
void requireView();

/**
 * Throws ForbiddenException if {@link #canViewClientScopes()} returns {@code false}.
 */
void requireViewClientScopes();
```

另外整份 javadoc 的 {@link} 寫法混用了全名（{@link org.keycloak.authorization.AdminPermissionsSchema#MANAGE}）與簡名（{@link AdminRoles#QUERY_CLIENTS}）；既然已經為此加了 AdminRoles 的 import，統一用簡名會讀得比較順。

#### F-008 同一段 scope 比對迴圈在 ClientPermissionsV2 出現三次 — `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:224-234`

面向 B 簡潔 · Nit

**問題**：hasPermission(ClientModel, String)、hasPermission(String)、hasGrantedPermission(Resource, String) 三個方法的後半段是同一件事：evaluatePermission 之後雙層迴圈比對 scope 名稱。已經是第三次，過了 Rule of Three。

另外前兩個都寫了 `List<String> expectedScopes = Arrays.asList(scope);` 再 `expectedScopes.contains(s)`，但參數只有一個 scope——第三個方法就直接用 `scope.equals(s)`。這個 List 是從 UserPermissionsV2 的 varargs 版本抄過來的殘留，在單一 scope 的情境下只是多繞一圈。

還有一處不對稱：hasPermission(String) 用 `resourceStore.findByName(server, CLIENTS_RESOURCE_TYPE, server.getId())` 直接撈 all-clients resource（ClientPermissionsV2.java:245），而 hasPermission(ClientModel, String) 走的是 AdminPermissionsSchema.SCHEMA.getResourceTypeResource(...)（第 217 行）。後者才是 schema 提供的入口，也是 UserPermissionsV2 用的那個。

**證據**：
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:224-234`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:237-260`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:274-286`

**修復方向**：抽成一個共用私有方法，把「拿到 resource 之後怎麼判定」收斂到一處：

```java
private boolean evaluate(Resource resource, ResourceServer server, String... scopes) {
    Collection<Permission> permissions =
            root.evaluatePermission(new ResourcePermission(resource, resource.getScopes(), server), server);
    List<String> expected = Arrays.asList(scopes);
    for (Permission permission : permissions) {
        if (permission.getScopes().stream().anyMatch(expected::contains)) {
            return true;
        }
    }
    return false;
}
```

三個呼叫端各自只保留「怎麼取得 resource」的部分。順手把 hasPermission(String) 的 all-clients 查詢也改用 getResourceTypeResource()，與另一條路徑一致。

#### F-009 與本次功能無關的空白與排版變更混在 diff 裡 — `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:53`

面向 A 風格 · Nit

**問題**：AdminPermissionsSchema.java:53 原本是空行，這次變成四個空白的 trailing whitespace，和這個 MR 要做的事無關。

AdminPermissions.registerListener 那一段除了新增 Profile 判斷之外，還順手把 `(ClientModel)role.getContainer()` 之類的 cast 全部加了空格、整段重新縮排。這些排版變更本身沒問題，但它們讓那一段的 diff 看起來像「整段重寫」，而真正的行為變更只有最外層多包了一個 if——正是最需要 reviewer 注意、卻最容易被排版雜訊蓋掉的一行。

這兩項都不會被自動檢查攔下：.editorconfig 只對 *.js / *.tsx / *.adoc 設定 trim_trailing_whitespace，*.java 段落沒有這一條；pom.xml 裡也沒有 checkstyle / spotless / formatter 外掛。所以純粹是 diff 可讀性的建議。

**證據**：
- `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:53`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/AdminPermissions.java:77-95`

**修復方向**：把 AdminPermissionsSchema.java:53 還原成空行。AdminPermissions.java 的排版整理若要保留，建議拆成獨立 commit（「reformat」），讓帶行為變更的那個 commit 只剩下新增的 Profile 判斷，review 與日後 git blame 都會清楚很多。

#### F-010 ClientPermissionsV2 的可見性與同層的 UserPermissionsV2、父類別不一致 — `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:49`

面向 A 風格 · Nit

**問題**：ClientPermissionsV2 宣告成 `public class` 且建構子是 `public`，但它的直接對照組 UserPermissionsV2 是 package-private class 加 package-private 建構子（UserPermissionsV2.java:40-42），它的父類別 ClientPermissions 也是 package-private（ClientPermissions.java:59），唯一的建立者 MgmtPermissionsV2 就在同一個 package 裡。這個 package 的設計顯然是「實作全部關在裡面，外界只透過 ClientPermissionEvaluator / AdminPermissionEvaluator 介面取用」，多出來的 public 沒有對應的需求，只是擴大了往後不好收回的表面。

**證據**：
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:49`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:53`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/UserPermissionsV2.java:40-42`
- `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissions.java:59`

**修復方向**：把 class 與建構子都降成 package-private，與 UserPermissionsV2 一致：`class ClientPermissionsV2 extends ClientPermissions { ClientPermissionsV2(...) { ... } }`。編譯不會受影響，MgmtPermissionsV2 在同一個 package。

#### F-011 PermissionClientTest 的測試改動共用 realm 狀態但沒有還原 — `tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/PermissionClientTest.java:78-82`

面向 G 測試 · Nit

**問題**：@InjectRealm 的 lifecycle 預設是 LifeCycle.CLASS，所以 PermissionClientTest 這五個測試共用同一個 realm 與同一個 myclient。而 onAfter() 只清掉 scope permissions（PermissionClientTest.java:78-82），下列改動都沒有還原：

- testManageOnlyOneClient 把 myclient 改名成 "somethingNew"（第 166 行），並把一個 default client scope 移除後改掛成 optional（第 175-178 行）——這是不對稱的操作，跑完之後 myclient 的 scope 配置和起始狀態不同。
- testMapRolesAndCompositesOnlyOneClient 在 myclient 上建了 myclient-role 與 myclient-subRole 兩個 client role（第 334-340 行），沒有刪除。

目前的斷言剛好還撐得住，但這讓同一個類別內的測試變成有順序相依：testConfigureOnlyOneClient 會去拿 clientResource.getDefaultClientScopes().get(0)，拿到什麼取決於 testManageOnlyOneClient 有沒有先跑過。這類相依只會在有人加測試或 JUnit 換順序時才浮現，而且浮現時很難查。

同一份 diff 裡其實已經有正確做法：createUserPolicy / createClientPolicy 都用 realm.cleanup().add(...) 註冊還原動作（AbstractPermissionTest.java:117-133）。

**證據**：
- `tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/PermissionClientTest.java:78-82`
- `tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/PermissionClientTest.java:166`
- `tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/PermissionClientTest.java:175-178`
- `tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/PermissionClientTest.java:334-340`
- `tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/AbstractPermissionTest.java:117-133`

**修復方向**：把有副作用的操作也接上同一套機制：建立 client role 之後用 `realm.cleanup().add(r -> r.clients().get(myclient.getId()).roles().deleteRole("myclient-role"))` 註冊刪除；改名與 client scope 的調整同理（或在 testManageOnlyOneClient 結尾把 default client scope 加回去、名稱改回原值）。最省事的替代方案是在這個類別上把 realm 改成 @InjectRealm(lifecycle = LifeCycle.METHOD)，代價是每個測試重建 realm 會變慢，可以視實際耗時取捨。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 已經開啟 ADMIN_FINE_GRAINED_AUTHZ_V2 的既有 realm，升級到含這個變更的版本之後，要怎麼拿到新的 Clients resource 與 configure / map-roles-client-scope / map-roles-composite 三個 scope？

面向 F 資料取用與資料庫

**背景**：AdminPermissionsSchema.init() 在 admin-permissions client 已存在時直接 return（AdminPermissionsSchema.java:226-228），而它只在 adminPermissionsEnabled 由 false 翻成 true 時（JpaClientProviderFactory.java:73）或 realm import 時（RealmManager.java:572）被呼叫。全 repo grep 沒有找到任何會為既有 realm 補上新 schema 元素的路徑。這代表在此之前就啟用 V2 的 realm，升級後 resource server 裡只有 Users 的 5 個 scope 與 Users all-resource：建立 Clients permission 會在 AdminPermissionsSchema.getScope() 撞上 ModelValidationException（"Scope [configure] does not exist."），權限評估則會走進 F-004 描述的 null 路徑。ADMIN_FINE_GRAINED_AUTHZ_V2 目前是 Type.EXPERIMENTAL（Profile.java:58），Keycloak 對 experimental feature 通常不保證升級路徑，所以這也可能是刻意接受的——但這是產品決策，不該由 reviewer 代為認定，因此不給 severity。

**如何確認**：維護者對 experimental feature 的升級政策說明；或者一個測試：realm 先在舊 schema 下啟用 adminPermissionsEnabled、再套用新的 SCHEMA 定義，斷言 Clients permission 可以建立且 canManage(client) 不會拋例外。若政策是「不保證」，在 MR 描述或 release note 註明「既有 FGAP V2 realm 需重新建立」即可。

#### Q-002 當某個 client 已經有自己的 Resource（因為另有一筆針對它的 permission），評估它時會不會把掛在「其他物件」上、但 scope 相同的 permission 一起算進來？

面向 C 安全

**背景**：DefaultPolicyEvaluator 在 resource 相關的 policy 查完之後，會再做一次 `policyStore.findByScopes(resourceServer, null, scopes, policyConsumer)`（DefaultPolicyEvaluator.java:87），第二個參數是 null，也就是不限定 resource。而 FGAP 的 schema 是把所有 resource type 的 scope 攤平成同一組建在 resource server 上的（AdminPermissionsSchema.java:237-244，註解寫明「there is no way how to map scopes to the resourceType」），所以 manage / view / map-roles 這幾個 Scope 實體是 Users 與 Clients 共用的。DecisionPermissionCollector 在 isScopePermission(policy) 分支累加 grantedScopes 時，只比對 scope，沒有要求 policy 的 resources 含有當前 resource。從程式面看不出有東西阻止跨物件的授予，但也無法只靠靜態閱讀確定 deny 路徑與 resourceGranted 的交互作用不會把它擋掉。這不是本 MR 引入的——同樣的形狀在已經上線的 Users resource type 就存在——但新增 Clients 讓可跨越的物件種類變多了，值得在合併前釐清。若成立會是 Critical，因此刻意不在此給 severity。

**如何確認**：一個整合測試：對 clientA 建一筆 VIEW permission（讓 clientA 產生自己的 Resource），另對 clientB 建一筆 MANAGE permission，然後斷言該 admin 對 clientA 的 update 仍然是 403。同樣的形狀也可以跨 resource type 測一次（對某個 user 給 manage、對某個 client 給 view，確認 client 的 manage 沒有被帶出來）。

#### Q-003 V2 之下刪除 client / role / group 時，FGAP 留下的 Resource 與 scope permission 由誰清理？

面向 E 架構

**背景**：這個 MR 把 AdminPermissions.registerListener 的整段內容包進 `if (Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ))`（AdminPermissions.java:77）。這個判斷是必要的——沒有它，V2 之下刪 client 會呼叫到 ClientPermissionsV2.setPermissionsEnabled() 並拋 UnsupportedOperationException。ADMIN_FINE_GRAINED_AUTHZ 與 V2 是同一 feature key 的兩個版本、只能擇一生效，所以這個 guard 在 V2 之下等於整段停用。問題是：V2 沒有等價的清理。ClientApplicationSynchronizer 只刪被移除 client 自己的 resource server 並把它從 client policy 裡拿掉（ClientApplicationSynchronizer.java:49-74），不會動 admin-permissions client 裡那筆以 client UUID 為名的 Resource；UserSynchronizer.removeUserResources 是用 findByOwner 比對（UserSynchronizer.java:53-70），而 FGAP resource 的 owner 是 admin-permissions client，也對不上。結果是刪掉的 client 會在 permission 清單裡留下一筆孤兒 Resource。另外一個附帶影響：兩個 feature 都沒開時，這個 listener 現在也完全不執行，過去為「曾經啟用過 V1、後來關掉」的 realm 做的殘留清理跟著沒了。兩者都需要維護者確認是刻意還是遺漏，所以不給 severity。

**如何確認**：一個測試：在 V2 之下對某個 client 建立 Clients permission，刪除該 client，然後斷言 admin-permissions client 的 resource 清單裡不再有那筆 Resource、對應的 scope permission 也不存在。若確認 V2 的清理是另一個 issue 追蹤，在 AdminPermissions.java:77 那個 if 上留一行註解指向它即可。

</details>
