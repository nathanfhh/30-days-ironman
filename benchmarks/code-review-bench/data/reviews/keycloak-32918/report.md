## 審查結論：Approved with Comments

> Critical 0 · Suggestion 6 · Nit 3 · 未驗證提問 2
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
| ❌ | ✅ | ❌ |

- **A 風格**（未通過）：F-008：新增的常數 IDP_LOGIN_SUFFIX 與方法 cacheKeyForLogin，跟同一個 class 內既有的 IDP_*_KEY_SUFFIX / cacheKeyIdp* 命名不一致。其餘部分沒有問題——registerIDPLoginInvalidationOnUpdate 的 javadoc（InfinispanIdentityProviderStorageProvider.java:391-402）把兩個「不失效」的條件寫得很清楚，是這次 diff 裡最好讀的一段；方法長度、註解都在合理範圍。
- **B 簡潔**（未通過）：F-009：getLoginPredicate() 在同一個方法內被重建四次，加上一個多餘的 static import。另外 getForLogin（InfinispanIdentityProviderStorageProvider.java:214-255）與 getByOrganization（:168-211）有約 30 行幾乎逐字重複，只差在 delegate 呼叫與 searchKey 的算法——依團隊的 Rule of Three（第三次才抽），這是第二次，本輪不列為 finding；第三個同型別的清單快取出現時應該一起抽成共用的 helper。
- **D API 慣例**（不適用）：diff 內沒有 HTTP endpoint、路由、request/response schema 或 HTTP 動詞的定義。受影響的 IdentityProviderStorageProvider 是 server-spi 的 Java 介面，它的相容性影響歸在維度 I。
- **E 架構**（未通過）：F-004：getForLogin 的四個呼叫端中只有兩個補上 isEnabled() 重新檢查，等於把 provider 的契約攤給呼叫端各自負責。層次切分本身是對的——快取放在 model/infinispan、查詢邏輯留在 model/jpa、SPI 契約在 server-spi，沒有把商業邏輯下放到儲存層。
- **F 資料取用與資料庫**（未通過）：F-002（快取失效判斷兩邊的 isEnabled 基準不一致）與 F-007（remove() 每次多打一次 DB）。分散式面向已逐一確認過並無問題：realmCache.registerInvalidation 會產生 CacheKeyInvalidatedEvent 並跨節點廣播（model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/RealmCacheSession.java:237-239、:352-357），新的 login key 因此在叢集上會被正確清除；realm 被刪除時 InRealmPredicate 會清掉所有 InRealm 物件，而 IdentityProviderListQuery 有實作 InRealm，所以 removeAll()（InfinispanIdentityProviderStorageProvider.java:116-120）不另外處理 login key 是正確的。
- **G 測試**（未通過）：F-005（cleanup 用了字面值 "alias"）與 F-006（registerIDPLoginInvalidation 的失效分支沒有測試）。斷言品質本身是好的：測的是實際的快取內容與筆數，不是「有回傳東西就算過」。步驟 1–4 的期望值（5/5/10 → 6/5/11 → 5/6/11）我依 enabled = i % 2 == 0、i >= 10 才設 BROKER_PUBLIC 的建構條件逐一對過，算術正確。
- **I 回溯分析**（未通過）：F-001（getForLogin 的快取路徑與 fallback 路徑回傳結果不等價）與 F-003（getLoginPredicate() 的語意變更外溢到既有呼叫端）。簽名相容性本身沒有問題：getForLogin 是覆寫既有的 default method，getLoginPredicate() 的簽名未變；兩條 finding 都出在語意而非簽名。呼叫端已用 grep 完整列出並逐一追過——getLoginPredicate() 在這次改動前只有 1 個非測試呼叫端，getForLogin() 有 4 個。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 除了快取本身，還一併改了 server-spi 共用 predicate 的語意（server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:254，見 F-003）與登入頁 bean 的過濾條件（services/src/main/java/org/keycloak/organization/forms/login/freemarker/model/OrganizationAwareIdentityProviderBean.java:75、:80，見 F-004）。後者是快取帶進來的必要修正，前者不是——getLoginPredicate() 的新條件只有 invalidation 判斷需要，卻同時改變了 IdentityProviderBean.federatedProviderPredicate() 這個與快取無關的呼叫端，而且沒有對應測試。是否要在同一個 MR 內做這件事，屬於維護者的決定。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | 未安裝（preflight 確認 PATH 上沒有 trivy），略過相依套件漏洞、設定錯誤與 secret 掃描。本次 diff 未改動任何相依宣告或設定檔，影響有限，但仍未取得確定性佐證。 |
| opengrep | 略過 | 未安裝，且預設的 semgrep-rules 規則目錄不存在（preflight 對兩者都回報缺少），略過 SAST 掃描。 |
| ruff | 略過 | 已安裝（0.15.8）並實際執行於 model/ 與 server-spi/，輸出為 'warning: No Python files found under the given path(s)'。這次 diff 全部是 Java，ruff 沒有可分析的對象，因此記為 skipped 而非 ok——不能宣稱通過一場從未跑到程式碼的檢查。 · exit code 0 |
| ty | 略過 | 未安裝；且本次 diff 沒有 Python 檔案，即使安裝也不適用。 |
| oxlint | 略過 | 未安裝；且本次 diff 沒有 JavaScript/TypeScript 檔案。 |
| codegraph | 略過 | 未安裝，無法建立符號索引。Phase 3 的呼叫路徑列舉全部改用 grep 完成（getLoginPredicate 2 處呼叫端、getForLogin 4 處呼叫端、identityProviders().removeAll() 1 處呼叫端皆已逐一追過）。 |
| Java static analysis（spotbugs / pmd / semgrep java rules） | 略過 | 本 skill 沒有配置任何 Java 靜態分析工具，執行環境也沒有安裝。這份 diff 是 100% Java，因此**沒有任何確定性掃描覆蓋到受審程式碼**，所有結論都來自人工閱讀與 grep 導覽。讀者評估這份報告的可信度時應把這一點算進去。 |
| ncr-fresh-eyes（subagent） | 略過 | 本次執行環境沒有可用的 Agent/Task 工具，無法派出 subagent。依 SKILL.md Phase 3 的規定不得由主 agent 自行模擬（此時主 agent 已讀過 skill 全文，框架已經套上），因此本輪缺少未受 checklist 框架影響的第一眼觀察。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派出 subagent，本報告未經獨立的品質檢查；四項發佈規則（結論機械推導、自足性、對事不對人、每條 finding 都帶修復方向）由主 agent 自檢。 |

<details>
<summary>Suggestion（6）</summary>

#### F-001 getForLogin 的快取路徑與 fallback 路徑回傳的結果不等價（排序與去重都不同） — `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:244`

面向 I 回溯分析 · Suggestion

**問題**：加上這個 override 之前，getForLogin 走的是 server-spi 的 default 實作 → InfinispanIdentityProviderStorageProvider.getAllStream() → JpaIdentityProviderStorageProvider.getAllStream()，而後者結尾是 query.orderBy(builder.asc(idp.get(ALIAS)))（model/jpa/...:289），所以登入頁拿到的 IDP 一直是依 alias 排序的。加上快取後，命中快取的那條路徑先 collect(Collectors.toSet())，再把 model 放進 HashSet（InfinispanIdentityProviderStorageProvider.java:244），回傳順序變成 hash 順序；而兩條 fallback（:218 的 isInvalid、:249 的 cached id 查不到）仍然是 alias 順序。呼叫端只用 IDP_COMPARATOR_INSTANCE 依 guiOrder 排序，而 OrderedModelComparator 在 guiOrder 為 null 時對所有 IDP 都回傳 10000（server-spi/.../OrderedModel.java:55），stable sort 會原樣保留輸入順序——guiOrder 沒設是預設情況，所以登入頁按鈕的排列會因為「這次有沒有命中快取」而不同。同一個 Set 也讓去重行為分歧：default getForLogin 對 FetchMode.ALL 是 Stream.concat 兩段查詢且沒有去重（server-spi/.../IdentityProviderStorageProvider.java:165-183），當 organizationId 為 null 時，一個 realm-level 但 config 帶 BROKER_PUBLIC=true 的 provider 會同時符合兩段而重複出現，fallback 路徑會回傳兩次，快取路徑則只有一次。

**證據**：
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:244`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:218`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:249`
- `model/jpa/src/main/java/org/keycloak/models/jpa/JpaIdentityProviderStorageProvider.java:289`
- `server-spi/src/main/java/org/keycloak/models/OrderedModel.java:55`
- `services/src/main/java/org/keycloak/forms/login/freemarker/model/IdentityProviderBean.java:238`

**修復方向**：讓三個 return 產出同一個順序、同一份去重結果。最直接的做法是在回傳前統一排序，例如把三處的結尾都改成 .sorted(Comparator.comparing(IdentityProviderModel::getAlias, Comparator.nullsLast(Comparator.naturalOrder()))) 之後再回傳 Stream；fallback 那兩條也順便用 .distinct() 收掉重複。若刻意不保證順序，請在 getForLogin 的 javadoc（server-spi/.../IdentityProviderStorageProvider.java:150-164）寫明「回傳順序未定義」，讓呼叫端知道不能依賴它。

#### F-002 update() 的 login cache 失效判斷，原值與新值不是用同一套 isEnabled() 比較，存在漏失效路徑 — `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:91`

面向 F 資料取用與資料庫 · Suggestion

**問題**：update() 的 original 來自 getById(model.getInternalId())（:91），而 getById 回傳的是 createOrganizationAwareIdentityProviderModel 包出來的匿名子類（:432-446），它覆寫的 isEnabled() 在 IDP 綁了組織時會再看 OrganizationProvider.isEnabled()（等同 realm.isOrganizationsEnabled()）與 org.isEnabled()。updated 則是呼叫端傳進來的原始 model，isEnabled() 就是欄位值。getLoginPredicate() 的第一個 filter 正是 IdentityProviderModel::isEnabled（server-spi/...:220），所以 :405 與 :409 這兩個比較的兩邊基準不同。同一個檔案裡 remove() 用的是 idpDelegate.getByAlias(alias)（:100），拿到的是未包裝的 raw model——兩個失效入口對「什麼算 login IDP」的定義並不一致，而快取集合是由 JPA 查詢（只看 raw 欄位）決定的，所以 raw 那一邊才是與快取一致的定義。
具體的漏失效路徑：組織 O 被停用時，綁在 O 上、BROKER_PUBLIC=true 且 enabled 的 IDP X 仍然留在 ORG_ONLY / ALL 的快取集合裡（JPA 查詢不看組織狀態）。此時把 X 改成 hideOnLogin=true，或把 BROKER_PUBLIC 拿掉：original 因為包裝後 isEnabled() 是 false 而 predicate 為 false，updated 也是 false，:405 直接 return，三個 login cache 都不會失效。之後 O 重新啟用，登入頁讀到的還是舊集合，X 會重新出現在登入頁上——BROKER_PUBLIC 被拿掉的那個情形，等於把組織的私有 broker 顯示在公開登入頁。已確認沒有其他機制會順手清掉這個 entry：RealmCacheManager.realmUpdated() 只加 realm 自己的 key（model/infinispan/.../RealmCacheManager.java:56-59），InRealmPredicate 的整批清除只在 realm 被刪除時觸發（:61-65）。

**證據**：
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:91`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:405`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:409`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:432`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:100`
- `server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:220`

**修復方向**：讓 registerIDPLoginInvalidationOnUpdate 的兩邊都用未經 organization 包裝的值比較——最小改法是另外向 delegate 取一次原值（IdentityProviderModel original = idpDelegate.getById(model.getInternalId())），只用它做 login predicate 判斷，registerIDPInvalidation 仍沿用現在的 getById 結果。注意不能用 new IdentityProviderModel(original) 剝殼：複製建構子是 this.enabled = model.isEnabled()（server-spi/.../IdentityProviderModel.java:114），會把覆寫後的值抄進去。若不想多一次查詢，另一個選項是在拿不準時保守失效（把 :405 的提前 return 拿掉），代價只是多幾次 DB 重建。

#### F-003 getLoginPredicate() 的語意變更外溢到登入頁的 federated broker 過濾，且與 getLoginSearchOptions() 不再對稱 — `server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:254`

面向 I 回溯分析 · Suggestion

**問題**：新增的那一行讓 getLoginPredicate() 開始檢查 organizationId 與 BROKER_PUBLIC。這個 predicate 在這次改動前只有一個非測試呼叫端：IdentityProviderBean.federatedProviderPredicate()（services/...:226），它用在使用者已經有 federated identity 時，決定登入頁要顯示哪些已綁定的 broker（:209-216）。而 FreeMarkerLoginFormsProvider:484 只在 Profile.Feature.ORGANIZATION 開啟「且」realm.isOrganizationsEnabled() 為真時才換成 OrganizationAwareIdentityProviderBean（它自己覆寫了這個 predicate，語意本來就已含 broker-public 條件）。也就是說 base bean 這條路正好是在「realm 沒有啟用組織」時執行——而那正是仍可能存在 organizationId 不為 null 的 IDP 的情境（組織曾經啟用過後來關掉，IDP 資料列上的 organizationId 不會被清掉）。結果是：使用者已經綁定、但沒有 BROKER_PUBLIC 的組織 broker，會從登入頁消失，該使用者失去這個登入管道。此外 getLoginPredicate() 與它的查詢端孿生 getLoginSearchOptions()（:248-250）現在不再對稱——後者只回 ENABLED / LINK_ONLY / HIDE_ON_LOGIN；enum 自己的 javadoc（:216-217）也寫著「包含所有判斷 provider 是否可用於登入的欄位」，而 organizationId 與 BROKER_PUBLIC 都不是這個 enum 的欄位。

**證據**：
- `server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:254`
- `server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:248`
- `server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:216`
- `services/src/main/java/org/keycloak/forms/login/freemarker/model/IdentityProviderBean.java:226`
- `services/src/main/java/org/keycloak/forms/login/freemarker/model/IdentityProviderBean.java:209`
- `services/src/main/java/org/keycloak/forms/login/freemarker/FreeMarkerLoginFormsProvider.java:484`

**修復方向**：這個條件是為了讓 invalidation 判斷對齊 getForLogin 實際查的條件才加的，把它留在需要它的地方即可：在 InfinispanIdentityProviderStorageProvider 內定義一個私有 predicate，例如 private static final Predicate<IdentityProviderModel> LOGIN_IDP = LoginFilter.getLoginPredicate().and(idp -> idp.getOrganizationId() == null || Boolean.parseBoolean(idp.getConfig().get(OrganizationModel.BROKER_PUBLIC)));，讓 LoginFilter 維持原語意。若確定要改共用 predicate，請一併更新 :216-217 的 enum javadoc、說明它與 getLoginSearchOptions() 為何不再對稱，並補一個覆蓋 federatedProviderPredicate() 的測試把預期行為釘住。

#### F-004 getForLogin 的四個呼叫端只有兩個補上 isEnabled() 重新檢查，契約被攤給呼叫端負責 — `services/src/main/java/org/keycloak/organization/forms/login/freemarker/model/OrganizationAwareIdentityProviderBean.java:75`

面向 E 架構 · Suggestion

**問題**：快取路徑的每個 id 都經過 session.identityProviders().getById(id)（InfinispanIdentityProviderStorageProvider.java:246），拿回來的是 organization-aware 包裝後的 model，isEnabled() 可能因為所屬組織被停用而變成 false——即使它還留在快取集合裡。這次在兩個呼叫端補上了 .filter(idp -> idp.isEnabled() ...) 並附註「re-check isEnabled as idp might have been wrapped」（:75、:80），但另外兩個呼叫端沒有：OrganizationAwareIdentityProviderBean:58 與 IdentityProviderBean:238。我把這兩條路徑追到底：它們都用 FetchMode.REALM_ONLY，而 default getForLogin 對 REALM_ONLY 會把 ORGANIZATION_ID 釘成 null（server-spi/...:170），JPA 把空值翻成 isNull（model/jpa/...:249），所以集合裡不會有綁組織的 IDP，包裝後的 isEnabled() 就是欄位值——目前確實是安全的。問題是這個「安全」沒有寫在任何地方，而 getForLogin 的 javadoc（server-spi/...:151-152）明講回傳的是「enabled、非 link-only、非 hidden」的 IDP，實作現在會違反這句話，只靠呼叫端各自補救。下一個呼叫端出現時，沒有任何東西會提醒它要補這個 filter。

**證據**：
- `services/src/main/java/org/keycloak/organization/forms/login/freemarker/model/OrganizationAwareIdentityProviderBean.java:75`
- `services/src/main/java/org/keycloak/organization/forms/login/freemarker/model/OrganizationAwareIdentityProviderBean.java:80`
- `services/src/main/java/org/keycloak/organization/forms/login/freemarker/model/OrganizationAwareIdentityProviderBean.java:58`
- `services/src/main/java/org/keycloak/forms/login/freemarker/model/IdentityProviderBean.java:238`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:246`
- `server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:151`

**修復方向**：把過濾收回 provider：在 InfinispanIdentityProviderStorageProvider.getForLogin 的三個 return 統一接上 .filter(IdentityProviderModel::isEnabled)，然後移除 OrganizationAwareIdentityProviderBean:75、:80 的重複檢查與註解。若刻意要讓呼叫端負責，請把理由與前提（哪些 FetchMode 需要重新檢查、為什麼 REALM_ONLY 不用）寫進 getForLogin 的 javadoc。

#### F-005 測試的 cleanup 用了字面值 "alias"，這個測試建立的 21 個 identity provider 不會被清掉 — `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:381`

面向 G 測試 · Suggestion

**問題**：迴圈裡建立的 alias 是 "idp-alias-" + i（:374），但登記 cleanup 的是 testRealm().identityProviders().get("alias")::remove——寫死的字串 "alias"（:381，:425 同樣）。同一個 base class 裡的正確寫法就在 AbstractOrganizationTest:143：get(broker.getAlias())::remove。TestCleanup.addCleanup 會把 close() 丟出的例外整個吞掉（TestCleanup.java:65-72 的 catch (Exception ex) { // ignore }），所以刪不到的 404 不會有任何聲音，20 個 idp-alias-* 加上後面的 idp-alias-20 全部留在 test realm 裡。AbstractKeycloakTest 只有在 isImportAfterEachMethod() 為真時才會在每個 method 後重建 realm（:235-240），這個 class 沒有覆寫它，所以 realm 是整個 class 共用的，殘留會帶到後面的 test method。而這個測試自己斷言的是絕對數字（assertEquals(5, ...)、assertEquals(11, ...)），對 realm 內殘留的 provider 沒有免疫力。（:303 的同一個錯字是既有的，不在這次 diff 內。）

**證據**：
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:381`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:425`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/admin/AbstractOrganizationTest.java:143`
- `testsuite/integration-arquillian/tests/base/src/main/java/org/keycloak/testsuite/util/TestCleanup.java:65`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AbstractKeycloakTest.java:235`

**修復方向**：把 alias 存進 final 變數再登記：final String alias = "idp-alias-" + i; ... getCleanup().addCleanup(testRealm().identityProviders().get(alias)::remove);，:425 的 idp-alias-20 同理。順手把 :303 的既有錯字一起修掉，這個 class 內的兩個測試才不會互相污染（見 Q-002）。

#### F-006 registerIDPLoginInvalidation 的「應該失效」分支完全沒有測試覆蓋 — `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:421`

面向 G 測試 · Suggestion

**問題**：新測試的步驟 1 建立的是 hideOnLogin=true 的 IDP（:421），刪掉的是 idp-alias-1（:428，i % 2 == 1 所以 enabled=false）——兩個都走 getLoginPredicate() 為 false 的分支，斷言都是「快取還在」。步驟 2、3、4 驗的全部是 registerIDPLoginInvalidationOnUpdate（update 路徑）。也就是說 create()（:84）與 remove()（:111）在「這個 IDP 確實可用於登入」時應該讓三個 login cache 失效的那一半，測試完全沒有覆蓋：把 :384 的 if 條件改成 if (false)，這個測試依然會全綠。

**證據**：
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:421`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:428`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:384`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:84`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:111`

**修復方向**：在步驟 1 之後補兩段對稱的案例：(a) 建立一個 enabled、非 link-only、非 hidden 的 realm-level IDP，斷言三個 login cache 都被清掉；(b) 重新查詢把快取建回來後，刪掉一個確實可登入的 IDP（例如 idp-alias-0），同樣斷言三個 key 都變成 null。

</details>

<details>
<summary>Nit（3）</summary>

#### F-007 remove() 現在每次都多打一次資料庫，即使快取裡就有這個 IDP — `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:100`

面向 F 資料取用與資料庫 · Nit

**問題**：IdentityProviderModel storedIdp = idpDelegate.getByAlias(alias); 被提到 if 之前（:100），所以不論 isInvalid(cacheKey) 成不成立都會查一次資料庫；改動前只有 isInvalid 成立時才查（原本寫在 :103 的參數位置）。同時 else 分支仍然從快取拿 cached.getIdentityProvider() 餵給 registerIDPInvalidation（:105-108），而 login 失效用的是 storedIdp（:111）——同一個方法裡對同一個 IDP 有兩個來源，兩者在快取過期時可能不一致。刪除 IDP 是低頻的 admin 操作，效能成本可以接受，但兩個來源會讓之後讀這段的人多花時間確認它們等價。

**證據**：
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:100`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:101`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:111`

**修復方向**：既然 storedIdp 一定會取到，就讓兩個失效都用它：把 if/else 收成 if (storedIdp != null) { registerIDPInvalidation(storedIdp); }，順帶把原本 alias 不存在時 registerIDPInvalidation(null) 會 NPE 的邊角一併收掉。若要保留避免多打 DB 的行為，就把 getByAlias 留在 isInvalid 分支內，另外用 cached 的值做 login 失效判斷，並在註解說明兩者為何等價。

#### F-008 新增的常數與方法命名跟同一個 class 內的鄰居不一致 — `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:47`

面向 A 風格 · Nit

**問題**：旁邊三個常數是 IDP_COUNT_KEY_SUFFIX / IDP_ALIAS_KEY_SUFFIX / IDP_ORG_ID_KEY_SUFFIX（:44-46），新增的叫 IDP_LOGIN_SUFFIX（:47），少了 KEY。方法也一樣：cacheKeyIdpCount / cacheKeyIdpAlias / cacheKeyIdpMapperAliasName / cacheKeyOrgId（:61-75）旁邊放的是 cacheKeyForLogin（:77）。兩者都能用，但下一個要加第五個 cache key 的人會不知道該跟哪一組慣例。

**證據**：
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:47`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:44`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:77`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:61`

**修復方向**：改成 IDP_LOGIN_KEY_SUFFIX 與 cacheKeyIdpLogin。後者是 public 且已被測試 static import（testsuite/.../OrganizationCacheTest.java:23），改名時要同步更新該 import 與三處使用點。

#### F-009 getLoginPredicate() 每次呼叫都重新組出一條 predicate 鏈，一次 update 呼叫四次；static import 也是多餘的 — `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:405`

面向 B 簡潔 · Nit

**問題**：getLoginPredicate() 每次都重新 Stream.of(values()).map(LoginFilter::getFilter).reduce(Predicate::and) 組裝一條新的 predicate 鏈（server-spi/...:252-256），而 registerIDPLoginInvalidationOnUpdate 在兩行條件裡叫了四次（:405 兩次、:409 兩次）。成本本身不大，但讀起來像是四個各自獨立的判斷，實際上是同一條。另外 :40 的 static import 是多餘的——這個 class 本身就 implements IdentityProviderStorageProvider，直接寫 LoginFilter.getLoginPredicate() 就能用。

**證據**：
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:405`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:409`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:40`
- `server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:252`

**修復方向**：在 registerIDPLoginInvalidationOnUpdate 開頭取一次 Predicate<IdentityProviderModel> isLoginIdp = LoginFilter.getLoginPredicate();，兩個條件改用 isLoginIdp.test(original) / isLoginIdp.test(updated)，並移除 :40 的 static import。若採納 F-003 的建議改用 provider 私有的 predicate，可以直接宣告成 private static final 一次組好。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 在組織數量很多的 realm 上，把所有組織的 login IDP 清單塞進同一個 cache entry，重建與失效的成本可以接受嗎？

面向 F 資料取用與資料庫

**背景**：cacheKeyForLogin(realm, mode)（InfinispanIdentityProviderStorageProvider.java:77-79）一個 realm 只有三個 key，組織是放在 IdentityProviderListQuery.searchKeys 的 map key 上（:223、model/infinispan/.../idp/IdentityProviderListQuery.java:31-45）。每個新組織的第一次查詢會走 cache.invalidateObject(cacheKey) → 複製整份既有 map → 重新 addRevisioned（:236-240），是 O(已快取組織數) 的重建；而任何一個可登入 IDP 的 create / update / remove 都會把三個 mode 的 entry 整個清掉（:385-387、:415-417），也就是所有組織的清單一起失效。既有的 getByOrganization 用同樣的結構，但它的 searchKey 只是分頁組合（first.max），數量有界；這裡的 searchKey 是組織 id，沒有上界。這是規模相依的成本，從這份 diff 與這台機器上判斷不了，因此不給嚴重度。

**如何確認**：在 N（例如 1000）個組織、每個組織數個 public broker 的 realm 上量測登入頁的 p99 與 login cache 的重建次數；或由作者說明是否評估過 per-organization 的 cache key（例如 realm.id + ".idp.login." + mode + "." + orgId），以及為何選擇目前這個結構。

#### Q-002 以目前的 JUnit method 執行順序，testCacheIDPByOrg 洩漏的 identity provider 會不會讓 testCacheIDPForLogin 的絕對數字斷言直接失敗？

面向 G 測試

**背景**：testCacheIDPByOrg 也有 F-005 的同一個 cleanup 錯字（OrganizationCacheTest.java:303），會留下 10 個 org-idp-*。組織被刪除時 JpaOrganizationProvider.remove 會對每個關聯的 IDP 呼叫 removeIdentityProvider（model/jpa/src/main/java/org/keycloak/organization/jpa/JpaOrganizationProvider.java:141、:358-374），把 organizationId 與 BROKER_PUBLIC 清掉，於是它們變成 realm-level、enabled、未隱藏的 provider——正好會被 getForLogin(FetchMode.REALM_ONLY, null) 算進去，而 testCacheIDPForLogin 斷言的是 assertEquals(5, ...)（:404）。兩個 method 的實際執行先後由 JUnit 4 的 MethodSorters.DEFAULT（method 名稱的 hash）決定，從原始碼看不出來，而這次審查的環境無法執行 Arquillian 測試套件，所以不能斷言它會失敗，也不能斷言它安全。

**如何確認**：在本機跑一次完整的 OrganizationCacheTest class（而不是單獨跑 testCacheIDPForLogin），確認是否穩定通過；或在修掉 F-005 之後重跑，確認兩者的差異。

</details>
