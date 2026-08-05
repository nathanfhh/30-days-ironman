## 審查結論：Approved with Comments

> Critical 0 · Suggestion 4 · Nit 3 · 未驗證提問 2
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
| ❌ | ✅ | ❌ |

- **A 風格**（未通過）：命名 typo 與 trailing whitespace，見 F-006。
- **B 簡潔**（未通過）：PR 內含兩處與此 bug 無關的清理改動，見 F-005。刪除的 groupMatchesSearchOrIsPathElement 已確認是 private static 且全 repo 無呼叫者，刪除本身安全。
- **F 資料取用與資料庫**（未通過）：同一個「group 被並行刪除 → getGroupById 回 null」的競態，在 GroupAdapter 的三個 getSubGroupsStream overload 與 GroupUtils.populateGroupHierarchyFromSubGroups 中仍未防護，見 F-001、F-003。
- **G 測試**（未通過）：新測試方向正確，但沒有 join reader thread 就 assert（存在漏判窗口），且斷言內容無法辨識失敗原因，見 F-004、F-007。
- **I 回溯分析**（未通過）：GroupAdapter.getSubGroupsCount() 的回傳行為改變後，與 GroupModel.java:296 上「Never returns null」的 javadoc 契約矛盾，見 F-002。已列舉唯一的 in-tree 呼叫者（GroupUtils.populateSubGroupCount）確認目前不會立即 NPE。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：PR 夾帶了兩處與這個 NPE 無關的改動：CachedGroup.getRealm() 補 @Override（CachedGroup.java:61），以及刪除 GroupUtils 裡已無人呼叫的 private static groupMatchesSearchOrIsPathElement。兩者本身都是好改動，但 PR-CHECKLIST.md:11 明文寫著「The PR does not contain changes not relevant to the GitHub Issue」，是否要留在同一個 PR 由 maintainer 決定。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | preflight 顯示未安裝，本次略過相依套件漏洞與 secret 掃描。這個 diff 沒有動 pom.xml 或任何相依宣告，但仍屬未覆蓋。 |
| opengrep | 略過 | preflight 顯示未安裝，SAST 完全沒有執行。本報告中的所有結論都來自人工閱讀，沒有任何自動化 SAST 覆蓋。 |
| ruff | 略過 | ruff 0.15.8 已安裝並實際執行（exit 0），但輸出是「No Python files found under the given path(s)」——本次 diff 全部是 Java，ruff 對它沒有任何適用性。這筆記錄等同零覆蓋，不代表通過檢查。 · exit code 0 |
| ty | 略過 | preflight 顯示未安裝；且本次 diff 沒有 Python 檔，即使安裝也不適用。 |
| oxlint | 略過 | preflight 顯示未安裝；且本次 diff 沒有 JavaScript/TypeScript 檔，即使安裝也不適用。 |
| maven (compile / test) | 略過 | 審查環境沒有 Maven repository 的網路存取，無法 compile、無法跑 GroupTest，也無法用編譯器驗證任何簽章或型別假設。整份報告的 Java 相關結論都是靜態閱讀 checkout 內原始碼得出的。另註：repo 根 pom.xml 沒有設定 checkstyle / spotless / formatter-maven-plugin / PMD，因此本報告不主張任何風格問題會被 build 擋下。 |
| codegraph | 略過 | preflight 顯示未安裝，呼叫關係與 caller 列舉改用 grep 在完整 checkout 上進行（getSubGroupsCount、getSubGroupsStream、subGroupCount 三組關鍵字全域搜尋）。 |

<details>
<summary>Suggestion（4）</summary>

#### F-001 同一個 class 內三個 getSubGroupsStream() overload 仍是未防護的 modelSupplier.get()，修復不完整 — `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:256`

面向 F 資料取用與資料庫 · Suggestion

**問題**：這次修的 getSubGroupsCount() 就緊接在三個 getSubGroupsStream() overload 下面（第 256、262、268 行），三者都是同一個寫法 `modelSupplier.get().getSubGroupsStream(...)`，也都會在 group 被並行刪除時拿到 null 而 NPE——原因完全一樣：modelSupplier 是 LazyModel(this::getGroupModel)，而 getGroupModel() 走的是 cacheSession.getGroupDelegate().getGroupById(...)，直接查 DB，資料列不在就回 null（GroupAdapter.java:298-300）。

已做過反證搜尋：(1) isUpdated() 分支不是保護傘——它在 invalidated 時查不到會丟 IllegalStateException（GroupAdapter.java:70-76），走不到這一行的正是「本地 cache entry 還在、DB 資料列已消失」這個情境，也就是本 PR 要修的那一個；(2) cached.getSubGroups()/getRoleMappings() 這條路徑是安全的，因為 DefaultLazyLoader.get() 已經有 `source == null ? fallback.get() : ...`（DefaultLazyLoader.java:52-54），所以無參數的 getSubGroupsStream() 不受影響，但三個有參數的 overload 沒有經過 LazyLoader；(3) 呼叫端沒有上游擋板——GroupResource.getSubGroups() 在第 180 行直接 `group.getSubGroupsStream(search, exact, -1, -1)`，group 是請求開始時取得的 adapter，刪除發生在那之後，null 檢查（GroupsResource.getGroupById）已經過了。

也就是說 admin API 的 GET `admin/realms/{realm}/groups/{group-id}/children` endpoint（GroupResource.getSubGroups）在同樣的競態下仍會回 500。

**證據**：
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:256`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:262`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:268`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:274`
- `services/src/main/java/org/keycloak/services/resources/admin/GroupResource.java:180`

**修復方向**：把同一個防護套到三個 overload 上，或者更好——在 GroupAdapter 內抽一個 private helper 統一處理，避免下次再漏一個。例如：

```java
private GroupModel requireModelOrNull() {
    return modelSupplier.get();
}

@Override
public Stream<GroupModel> getSubGroupsStream(String search, Boolean exact, Integer firstResult, Integer maxResults) {
    if (isUpdated()) return updated.getSubGroupsStream(search, exact, firstResult, maxResults);
    GroupModel model = modelSupplier.get();
    return model == null ? Stream.empty() : model.getSubGroupsStream(search, exact, firstResult, maxResults);
}
```

回傳 Stream.empty() 與無參數版在同樣情境下的行為一致（LazyLoader fallback 給空集合），也符合 GroupModel.java:235/244/269 上「Never returns null」的 javadoc。若認為這三個 overload 不在本 issue 範圍內，至少在 PR 描述或 issue 上留一筆，讓它不會就這樣消失。

#### F-002 getSubGroupsCount() 改為可能回傳 null，與 GroupModel 上「Never returns null」的 javadoc 契約矛盾 — `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:274`

面向 I 回溯分析 · Suggestion

**問題**：GroupModel.getSubGroupsCount() 的 javadoc 第 296 行寫的是「@return The number of groups beneath this group. Never returns {@code null}.」。這次改動讓 infinispan 的實作在 group 被並行刪除時回傳 null，等於實作與 SPI 上公開的契約直接矛盾——而 GroupModel 是 server-spi 的公開介面，外部 provider 與未來的呼叫端都會照這段 javadoc 寫程式。

已做過反證搜尋：全 repo grep getSubGroupsCount / subGroupCount 之後，in-tree 的唯一呼叫者是 GroupUtils.populateSubGroupCount()，它把值直接餵給 GroupRepresentation.setSubGroupCount(Long)（GroupUtils.java:90），是 boxed Long，所以「今天不會馬上炸」——這也是我沒有把它列為 Critical 的原因。但任何一個之後寫 `group.getSubGroupsCount() > 0` 的呼叫端就會 unboxing NPE，而 javadoc 明白告訴他不用檢查 null。

另一個不一致點：同一個 class 的無參數 getSubGroupsStream()（第 237 行）在完全相同的情境下經由 DefaultLazyLoader 的 fallback 得到空集合，count 是 0。也就是說 group 消失時，同一個 adapter 的兩條路徑一條給 0、一條給 null。

**證據**：
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:274`
- `server-spi/src/main/java/org/keycloak/models/GroupModel.java:296`
- `services/src/main/java/org/keycloak/utils/GroupUtils.java:90`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:237`

**修復方向**：兩條路擇一，但要選一條：

(1) 傾向這個——回傳 0L 而不是 null，與同 class 的 getSubGroupsStream() fallback 行為一致，也保住 SPI 契約：
```java
GroupModel model = modelSupplier.get();
return model == null ? 0L : model.getSubGroupsCount();
```

(2) 如果刻意要讓呼叫端能分辨「group 已不存在」與「沒有子群組」，那就必須同步修改 server-spi/src/main/java/org/keycloak/models/GroupModel.java:296 的 javadoc，明確寫出什麼情況會回 null、呼叫端該怎麼處理，並確認 admin console 端（js/apps/admin-ui/src/groups/components/GroupTree.tsx:204、js/apps/admin-ui/src/components/group/GroupPickerDialog.tsx:324）以 `subGroupCount !== 0` 判斷時，null 被當成「有子群組」是可接受的。

#### F-003 GroupUtils.populateGroupHierarchyFromSubGroups() 對 parentModel 沒有 null 檢查，同一個競態仍會 NPE — `services/src/main/java/org/keycloak/utils/GroupUtils.java:47`

面向 F 資料取用與資料庫 · Suggestion

**問題**：populateGroupHierarchyFromSubGroups() 在第 47 行 `session.groups().getGroupById(realm, currGroup.getParentId())` 取得 parentModel，之後在第 57 行 `toRepresentation(groupEvaluator, parentModel, full)`（會走到 ModelToRepresentation.toRepresentation() 的 `group.getId()`）與第 60 行 `populateSubGroupCount(parentModel, parent)`（會走到 `group.getSubGroupsCount()`）直接解參考，中間沒有任何 null 檢查。

已做過反證搜尋：RealmCacheSession.getGroupById() 在 cache miss 且 delegate 查不到時，第 973 行明確 `if (model == null) return null`，在 invalidations 命中時第 979 行也是直接回傳 delegate 的結果（同樣可能是 null）。所以在叢集中 invalidation event 先到、DB 資料列已刪除的情況下，parentModel 就是 null。而這正是本 PR 承認會發生的情境——第 41 行的 populateSubGroupCount(group, currGroup) 就是這次修好的那條路徑，同一個函式往下四行的 parent 版本沒有一起處理。

這條路徑是 GET `admin/realms/{realm}/groups` endpoint（GroupsResource.getGroups）在 populateHierarchy=true（預設值）且回傳的 group 有 parent 時走的，也就是搜尋子群組的常見情形。本 PR 的新測試只建立 top-level group（parentId 為 null），不會經過這段迴圈，所以測不到。

**證據**：
- `services/src/main/java/org/keycloak/utils/GroupUtils.java:47`
- `services/src/main/java/org/keycloak/utils/GroupUtils.java:57`
- `services/src/main/java/org/keycloak/utils/GroupUtils.java:60`
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/RealmCacheSession.java:973`

**修復方向**：在第 47 行取得 parentModel 之後補一個 null 檢查，把已消失的父群組視為「這棵樹到此為止」：

```java
GroupModel parentModel = session.groups().getGroupById(realm, currGroup.getParentId());
if (parentModel == null) {
    // parent was removed concurrently; keep what we already have for this subtree
    break;
}
```

（model/jpa/src/main/java/org/keycloak/models/jpa/GroupAdapter.java:184 已經有同樣的處理方式與註解「In concurrent tests, the group might be deleted in another thread, therefore, skip those null values.」，可以沿用同一個模式。）如果認為不屬於本 issue 範圍，請開一張 follow-up issue，不要讓它跟著這次修復一起被視為已解決。

#### F-004 新測試在 assert 前沒有等待 reader thread，且刪除迴圈拋例外時該 thread 不會結束 — `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:139`

面向 G 測試 · Suggestion

**問題**：兩個問題出在同一段：

(1) 沒有 join。第 155 行 `deletedAll.set(true)` 之後，主執行緒立刻在第 157 行 assert。此時 reader thread 很可能還卡在最後一次 HTTP 請求裡——如果那次請求正好回 500，例外會在斷言跑完之後才被加進 caughtExceptions，測試照樣綠燈。CopyOnWriteArrayList 解決的是可見性，不是時序。這是一支專門為了守住這個 NPE 而寫的迴歸測試，卻有一個結構性的漏判窗口。

(2) thread 不會結束。deletedAll 只在第 155 行被設為 true，而它前面的刪除迴圈（第 152-154 行）沒有 try/finally。只要任何一次 `remove()` 拋出例外（測試失敗、伺服器暫時性錯誤都算），deletedAll 永遠是 false，這個非 daemon thread 就會無限迴圈對伺服器發請求，同時擋住 JVM 結束。同一個 class 的其他測試也會受到影響。

另外，tests/base 底下目前只有這一個檔案用到裸 `new Thread(`（全域 grep 確認），所以沒有現成的 helper 可沿用，但也代表這裡值得寫得保守一點。

**證據**：
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:139`
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:152`
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:155`
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:157`

**修復方向**：把 thread 收進 try/finally，並在斷言前 join：

```java
Thread reader = new Thread(() -> { ... });
reader.setDaemon(true);
reader.setName("group-reader");
reader.start();
try {
    groupUuids.forEach(id -> managedRealm.admin().groups().group(id).remove());
} finally {
    deletedAll.set(true);
    reader.join(TimeUnit.SECONDS.toMillis(30));
}
assertThat(caughtExceptions, Matchers.empty());
```

setDaemon(true) 讓最壞情況只是浪費資源而不是卡死 build；finally + join 讓斷言看到的是完整的例外清單。

</details>

<details>
<summary>Nit（3）</summary>

#### F-005 PR 夾帶兩處與此 NPE 無關的改動 — `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/entities/CachedGroup.java:61`

面向 B 簡潔 · Nit

**問題**：CachedGroup.getRealm() 補上 @Override（它實作的是 InRealm.getRealm()）、以及刪除 GroupUtils 裡的 private static groupMatchesSearchOrIsPathElement，兩者都與「並行刪除造成 NPE」無關。兩個改動本身都沒問題——已確認 groupMatchesSearchOrIsPathElement 是 private static 且全 repo 沒有任何呼叫者，刪掉是安全的；它用到的 StringUtil 在 server-spi 的同一個 package org.keycloak.utils 底下，所以也沒有留下孤兒 import。

只是 PR-CHECKLIST.md:11 寫的是「The PR does not contain changes not relevant to the GitHub Issue」，混在一起會讓之後 git blame 這個修復時多繞路。

**證據**：
- `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/entities/CachedGroup.java:61`
- `services/src/main/java/org/keycloak/utils/GroupUtils.java:101`
- `PR-CHECKLIST.md:11`

**修復方向**：若 maintainer 在意 checklist 的這一條，把這兩個 cleanup 拆成獨立的小 PR；若不在意，至少在 PR 描述裡註明它們是順手清理、與 issue 無關，讓 reviewer 不用去確認它們是不是修復的一部分。

#### F-006 變數名 typo groupUuuids，以及新增的空白行帶有 trailing whitespace — `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:117`

面向 A 風格 · Nit

**問題**：groupUuuids 是三個 u（第 121、129、152 行），下一個讀到的人會停下來確認是不是別的變數。第 117 行是新增的空白行但帶有 4 個尾隨空白。

這兩點純粹是可讀性：repo 根 pom.xml 沒有設定 checkstyle、spotless、formatter-maven-plugin 或 PMD，所以 build 不會擋下任何一項，這裡不主張它們會導致 CI 失敗。

**證據**：
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:117`
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:121`
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:129`
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:152`

**修復方向**：改名為 groupIds 或 groupUuids，並移除第 117 行的尾隨空白。

#### F-007 斷言只檢查「沒有例外」，失敗時無法分辨是不是這個 NPE，也沒有驗證回傳內容 — `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:143`

面向 G 測試 · Nit

**問題**：第 143 行呼叫 groups(null, 0, Integer.MAX_VALUE, true) 之後把回傳的 List<GroupRepresentation> 整個丟掉，第 157 行只斷言「沒有任何例外」。伺服器端 NPE 會變成 500，admin client 拋出的是 InternalServerErrorException，而它的 toString() 只有「HTTP 500 Internal Server Error」——測試紅了以後，從失敗訊息看不出到底是這個 NPE、還是任何其他 500。這正是 assertion quality 的問題：測的是「有沒有東西回來」，不是「行為對不對」。

另外 Integer.MAX_VALUE 當 max 會讓每次輪詢都要求整個 realm 的所有 group，在 CI 上是不必要的負擔。

**證據**：
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:143`
- `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:157`

**修復方向**：(1) 讓失敗訊息可診斷，例如把例外的 class 與 message 一併帶進斷言：`assertThat(caughtExceptions.stream().map(Throwable::toString).toList(), Matchers.empty());`，或改成收集 response body。(2) 順手驗證行為而不只是「沒炸」：對至少一次回傳結果斷言每個 GroupRepresentation 的 getSubGroupCount() 不為 null（這同時會把 F-002 的 null 契約問題釘在測試裡）。(3) max 用一個實際大於 100 的常數（例如 200）取代 Integer.MAX_VALUE。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 createMultiDeleteMultiReadMulti 在 CI 上真的能重現原本的 NPE 嗎？還是它會不分修復前後都綠燈？

面向 G 測試

**背景**：這支測試沒有任何同步點保證 reader thread 的請求會落在「cache entry 仍在、DB 資料列已刪」的那個窗口內；它靠的是刪除迴圈跑 100 次期間 reader 一直輪詢的機率。審查環境沒有 Maven 網路存取，無法 compile 也無法執行 GroupTest，所以無從觀察。這個問題和 F-004（沒有 join）是兩件事：就算補上 join，重現率仍然是未知數。

**如何確認**：在 revert 掉 GroupAdapter.java:274-275 修改（也就是保留原本的 getGroupModel().getSubGroupsCount()）的情況下，把這支測試連跑 20-50 次，統計失敗率。如果失敗率不夠高，就需要加入明確的同步點（例如用 CountDownLatch 讓 reader 在第一次讀完後才開始刪除，或用 runOnServer 直接對 cache 製造 stale entry）。

#### Q-002 這支測試建立的 100 個「Test Group N」是否會影響 GroupTest 內其他測試方法？

面向 G 測試

**背景**：managedRealm 由 @InjectRealm 注入，同一個 class 的測試方法共用。正常路徑下 100 個 group 都會在測試尾端被刪掉，但一旦刪除迴圈中途失敗（見 F-004），殘留的 group 會留在 realm 裡，而同 class 有多個測試會列出或搜尋 group。審查環境無法執行測試框架，也無法確認 @InjectRealm 的生命週期是 per-class 還是 per-method、以及測試方法之間是否有隔離。

**如何確認**：確認 org.keycloak.testframework 的 @InjectRealm 預設 lifecycle（per-class 或 per-method），以及 tests/base 是否對測試方法啟用平行執行。若是 per-class 且循序執行，加上 F-004 的 try/finally 之後風險就可以接受；若是平行執行，這支測試需要自己的 realm。

</details>
