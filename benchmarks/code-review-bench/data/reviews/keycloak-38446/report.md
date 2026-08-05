## 審查結論：Request Changes

> Critical 1 · Suggestion 4 · Nit 5 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | — | ❌ |

- **A 風格**（未通過）：命名與函式長度沒有問題，getCredential / createRecoveryCodesCredential 的名字都與行為相符，新增的 javadoc 也沒有與簽章脫節。扣分在錯誤處理與型別表達：BackwardsCompatibilityUserStorage.java:244 的 log.error 把 IOException 整個吞掉且訊息寫成 recovery-codes（實際 TYPE 是 recovery-authn-codes），:238/:332/:334 用 raw List 讓型別資訊在編譯期就消失。見 F-009、F-010。
- **B 簡潔**（未通過）：測試 helper 從 RecoveryAuthnCodesAuthenticatorTest 整段複製過來，連 delayed-suthenticator-config 的錯字和這個測試用不到的 delay 參數一起帶走（F-006）；createRecoveryCodesCredential 同時收下 credentialModel 與 generatedCodes 兩份同一份祕密的表述，且回傳 void（F-007）。沒有發現死程式碼或多餘相依：BackwardsCompatibilityUserStorage 移除的 Collectors 與 Pbkdf2Sha512PasswordHashProviderFactory、RecoveryAuthnCodesAction 移除的 CredentialProvider 與 RecoveryAuthnCodesCredentialProviderFactory，都已逐一確認檔案內確實不再使用。
- **C 安全**（未通過）：危險操作列舉：本次可觸及的危險操作是「驗證並消耗一組 recovery code」與「寫入 recovery code credential」。寫入路徑兩條（user storage 的 updateCredential、本地的 createCredential）都有 required action 的 tamper 檢查在前（RecoveryAuthnCodesAction.java:99-104），沒問題。驗證路徑兩條：本地那條會在 RecoveryAuthnCodesCredentialProvider.java:110-111 消耗掉用過的 code；user storage 那條完全沒有消耗步驟，見 F-001。
- **D API 慣例**（不適用）：本次沒有新增或修改任何 HTTP endpoint、URL、HTTP verb 或 request/response schema，D 的六條規則（URL 用 dash、驗證 schema、verb 冪等性、authn vs authz、PII 不進 URL、batch 原子性）都沒有對應的檢查對象。真正屬於「介面契約」的問題是 CredentialInput.getChallengeResponse() 的新 payload 形狀，那是 SPI 層的架構決策，歸在 E（F-004）。
- **E 架構**（未通過）：RecoveryAuthnCodesUtils.getCredential 把 credential 的來源從單一（local）擴成兩種（user storage / local），但後續處理仍假設是 local，形成一個跨層的破口（F-002）。另外 recovery codes 在 CredentialInput 上的序列化格式被分別寫死在 server-spi-private 與 testsuite-providers 兩個模組，改一邊就要同步改另一邊，正是「一個決策硬編碼在 N 個模組」（F-004）。testsuite provider 對 CredentialInputUpdater 只實作了一半，缺 disable / delete 側（F-003）。
- **F 資料取用與資料庫**（未通過）：併發問題直接與 F-001 綁在一起：本地路徑靠 RecoveryAuthnCodesCredentialProvider.java:110-111 的 read-modify-write 消耗 code，同時登入的競態由既有的 RecoveryAuthnCodesAuthenticatorTest#test06AuthenticateRecoveryAuthnCodesSimultaneous 以 delayed-authenticator 覆蓋；user storage 路徑因為根本沒有寫回動作，所以連要保護的 read-modify-write 都不存在——新測試雖然把 delayed-authenticator 的機制整段抄過來，卻固定傳 delay=0，並沒有測同時登入。另外 BackwardsCompatibilityUserStorage.java:232 對可能回傳 null 的 getMyUser 直接解參考（F-008）。
- **G 測試**（未通過）：新測試 testRecoveryKeysSetupAndLogin（BackwardsCompatibilityUserStorageTest.java:240-268）只驗證 happy path：設定成功、credential 落在 user storage、拿第一組 code 登入成功。沒有任何負向斷言（用過的 code 再用一次、錯的 code、code 用完），assertUserHasRecoveryKeysCredentialInUserStorage 也只被以 true 呼叫過一次。既有的 OTP 測試在同一個檔案裡有 testOTPSetupAndRemoveThroughAccountMgmtAndLogin（:304）與 testDisableCredentialsInUserStorage（:341）兩條移除路徑的覆蓋，recovery codes 一條都沒有。見 F-005。
- **H 非 Python 檔**（不適用）：diff 的 8 個檔案全部是 Java。H 列舉的類別（多分支 UI 元件、Vue、Dockerfile、nginx.conf、docker-compose、Alembic migration）在這次變更中一個都沒有出現，沒有可判定的對象。這些 Java 檔已在 A、B、C、E、F、G、I 各維度依一般最佳實務審過。
- **I 回溯分析**（未通過）：簽章相容性：本次沒有修改任何既有方法的簽章（CredentialHelper.getConfigurableAuthenticatorFactory 只有縮排變動，RecoveryAuthnCodeInputLoginBean 的建構子參數不變），新增的是 CredentialHelper.createRecoveryCodesCredential 與 RecoveryAuthnCodesUtils.getCredential 兩個新方法，不會打斷既有呼叫端。以 grep 掃過 RecoveryAuthnCodesCredentialModel.TYPE 與 recovery-authn-codes 的全部讀取點，production 端共四處（RecoveryAuthnCodesCredentialProvider、RecoveryAuthnCodesFormAuthenticator、RecoveryAuthnCodeInputLoginBean、UserCredentialModel.buildFromBackupAuthnCode），該遷移的都遷移了。失分在隱含輸入契約：getCredential 的回傳值語意變了（可能來自 user storage），但兩個呼叫端沿用舊假設，見 F-002。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：主要變更（CredentialHelper、RecoveryAuthnCodesUtils、authenticator、required action、login bean、testsuite provider 與測試）確實是同一件事。但 diff 內混了數處與本次主題無關的純排版改動：CredentialHelper.java:76-85 把 getConfigurableAuthenticatorFactory 整段重新縮排、BackwardsCompatibilityUserStorage.java:61 在 javadoc 補 <p>、:95 移除 new AbstractUserAdapterFederatedStorage(session, realm,  model) 的多餘空白、BackwardsCompatibilityUserStorageTest.java:326 與 :453-454 調整既有程式碼的換行縮排。這些行不影響行為，卻讓 reviewer 要多分辨一次哪些是真的改了，建議另開一個 formatting-only 的 commit 或 MR。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | in_diff 0、outside_diff 6、diff_files_covered 0 |
| ty | 略過 | 未安裝（不在 PATH 上）。另外本次 diff 沒有 Python 檔，即使安裝也不會覆蓋到任何變更。 |
| oxlint | 略過 | 未安裝（不在 PATH 上）。本次 diff 沒有 JavaScript / TypeScript 檔。 |
| trivy | 略過 | 未安裝（不在 PATH 上），略過 dependency vulnerability、misconfiguration 與 secret 掃描。 |
| opengrep | 略過 | 未安裝，且預設的 semgrep-rules ruleset 目錄不存在，略過 SAST 掃描。 |
| codegraph | 略過 | 未安裝，無法建立符號索引。Phase 3 的呼叫端列舉與完整性確認全部改用 grep：以 RecoveryAuthnCodesCredentialModel.TYPE、recovery-authn-codes、getFederatedCredentialsStream、RecoveryAuthnCodeInputLoginBean、supportsCredentialType 逐一掃過 repo，找齊所有讀寫點。 |
| java-static-analysis | 略過 | 本次 diff 的 8 個檔案全部是 .java。執行環境內只有 ruff（Python）與 git，沒有任何 Java 靜態檢查工具，因此自動化掃描對本次變更的覆蓋率是 0，報告內所有發現都來自人工閱讀原始碼。 |
| maven-build | 略過 | 環境無法連外，Maven 相依無法下載，專案不編譯。因此本報告不對「是否能編譯通過」作任何主張；另外已確認 repo 根目錄的 pom.xml 沒有設定 checkstyle、spotless、-Werror 或 maven-enforcer，所以也不能假設建置流程會替使用者攔下 raw type 之類的警告。 |
| ncr-fresh-eyes | 略過 | 本次執行環境沒有可用的 subagent 派工工具（Agent / Task 皆不存在，已以 ToolSearch 確認），無法派出 fresh-eyes。依 SKILL.md 規定不得由主 agent 自行模擬，因此本報告缺少一次未受 checklist 框架影響的獨立閱讀，Phase 3 的發現全部來自九維度與危險操作列舉。 |
| ncr-quality-check | 略過 | 同上，無法派出 subagent。report.json 已通過 report_model.py validate，但沒有經過獨立的第三方品質檢查，四條發佈前規則（結論機械性、自足性、對事不對人、每筆發現都有修復方向）僅由撰寫者自行核對。 |

### Critical

#### F-001 存在 user storage 的 recovery code 永遠不會被消耗，一組 code 可以無限次重複登入 — `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:326-340`

面向 C 安全 · Critical

**問題**：recovery code 的核心安全性質是 one-time：用過一次就失效。本地路徑有實作這件事——RecoveryAuthnCodesCredentialProvider.isValid 在比對成功後呼叫 credentialModel.removeRecoveryAuthnCode() 並 updateStoredCredential 寫回（:110-111）。這次新增的 user storage 路徑沒有對應動作。

三段證據串起來：

（1）BackwardsCompatibilityUserStorage.isValid:340 的判斷是 generatedKeys.stream().anyMatch(key -> key.equals(input.getChallengeResponse()))——12 組 code 任何一組都算通過，而且整個分支沒有任何一行修改 myUser.recoveryCodes。

（2）getCredentials:237-241 每次都用完整的原始清單重新呼叫 RecoveryAuthnCodesCredentialModel.createFromValues 建一個新 model，所以 remainingCodes 永遠是 12、allCodesUsed() 永遠是 false。連帶地 RecoveryAuthnCodeInputLoginBean 每次都取到 getNextRecoveryAuthnCode() 的第一組，登入畫面永遠只問 #1。

（3）已找過反證，沒有其他地方會替它消耗：UserCredentialManager.isValid 先跑 user storage 的 validator（:70-73），validate() 內的 toValidate.removeIf 會把通過的 input 從清單移除（:271-282），等到 :76-77 輪到本地 CredentialProvider 時清單已經空了，RecoveryAuthnCodesCredentialProvider.isValid 根本不會被呼叫。也就是說在 user storage 路徑上，消耗動作只能由 provider 自己在 isValid 內完成，而這次新增的實作沒有做。

受影響的是 testsuite-providers 這個測試用 provider，不會被打包進 production 發行版。但它是這個 repo 內唯一一份「user storage 如何支援 recovery codes」的範例實作，第三方寫 provider 時會照抄；而 testRecoveryKeysSetupAndLogin 又剛好斷言 enterRecoveryCodes(..., 0, recoveryKeys)——期待的 code 編號固定為 0，這條斷言之所以會過，正是因為計數器永遠不前進。等於用一個綠燈測試把壞掉的語意固定下來。

**證據**：
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:326-340`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:230-252`
- `model/storage/src/main/java/org/keycloak/credential/UserCredentialManager.java:59-80`
- `services/src/main/java/org/keycloak/credential/RecoveryAuthnCodesCredentialProvider.java:99-119`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:240-268`

**POC**：

````
在 BackwardsCompatibilityUserStorageTest 內延伸 testRecoveryKeysSetupAndLogin 即可重現，不需要外部工具：

```java
configureBrowserFlowWithRecoveryAuthnCodes(testingClient, 0);
String userId = addUserAndResetPassword("otp1", "pass");
List<String> recoveryKeys = setupRecoveryKeysForUserWithRequiredAction(userId, true);
TestAppHelper helper = new TestAppHelper(oauth, loginPage, appPage);

// 第一次：用 recoveryKeys.get(0) 登入 —— 成功（預期）
helper.startLogin("otp1", "pass");
enterRecoveryCodes(enterRecoveryAuthnCodePage, driver, 0, recoveryKeys);
enterRecoveryAuthnCodePage.clickSignInButton();
appPage.assertCurrent();
helper.logout();

// 第二次：再用同一組 recoveryKeys.get(0) —— 目前仍然成功，這就是問題
helper.startLogin("otp1", "pass");
enterRecoveryAuthnCodePage.assertCurrent();
// 畫面要求的編號仍是 #1：getRecoveryAuthnCodeToEnterNumber() 回傳 0
Assert.assertEquals(0, enterRecoveryAuthnCodePage.getRecoveryAuthnCodeToEnterNumber());
enterRecoveryAuthnCodePage.enterRecoveryAuthnCode(recoveryKeys.get(0));
enterRecoveryAuthnCodePage.clickSignInButton();
appPage.assertCurrent();   // 正確行為應該是留在錯誤畫面
```
````

**影響範圍**：誠實界定範圍：受影響的程式碼在 testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers 之下，只在測試環境載入，不會進入任何 production 發行版，所以對既有部署沒有直接曝險（PHI 不在範圍內）。實際損失有兩層：其一，這是 repo 內唯一一份 user storage 支援 recovery codes 的參考實作，第三方 provider 作者會以它為範本，把「code 不消耗」的語意複製到真正面對使用者的系統上——屆時一組外洩的 recovery code 就是一把長期有效的 2FA 繞過鑰匙，且因為登入畫面永遠問同一個編號，攻擊者只需要取得 12 組中的第 1 組。其二，本 PR 唯一的功能證據就是這條測試，測試綠燈但語意是錯的，等於把「已驗證」的標籤貼在未驗證的行為上，之後真的有人回頭查時會比完全沒有測試更難發現。

**風險處置**：Mitigate（降低）

**修復參考**：見本筆 fix：在 BackwardsCompatibilityUserStorage.isValid 內消耗 code 並寫回，同時補上「重複使用同一組 code 應失敗」的斷言。

**修復方向**：兩件事一起做：

1. 讓 BackwardsCompatibilityUserStorage 真的消耗 code。最小改法是在 isValid 的 recovery-authn-codes 分支比對成功後，把該組 code 從清單移除並寫回 myUser.recoveryCodes.setCredentialData(...)，例如：

```java
List<String> generatedKeys = JsonSerialization.readValue(
        storedRecoveryKeys.getCredentialData(), new TypeReference<List<String>>() {});
if (!generatedKeys.remove(input.getChallengeResponse())) {
    return false;
}
storedRecoveryKeys.setCredentialData(JsonSerialization.writeValueAsString(generatedKeys));
return true;
```

並讓 getCredentials 依剩下的清單建 model，使 remainingCodes 會隨使用下降、allCodesUsed() 在用完後成立。

2. 在 BackwardsCompatibilityUserStorageTest 補一條斷言：同一組 code 第二次登入必須失敗，並確認畫面要求的 code 編號會往前推進。這條斷言才是「user storage 版 recovery codes 真的能用」的證據；沒有它，目前的測試證明的只是「輸入某個字串可以登入」。

如果團隊的判斷是 one-time 語意本來就該由各 provider 自行負責、範例 provider 不打算示範，那也請在 CredentialInputUpdater#updateCredential 或 CredentialInputValidator#isValid 的 javadoc 上把這個責任寫清楚，並在 BackwardsCompatibilityUserStorage 加註解說明這裡刻意不實作。

<details>
<summary>Suggestion（4）</summary>

#### F-002 getCredential 可能回傳 user storage 的 credential，但兩個呼叫端仍當成本地 credential 處理 — `server-spi/src/main/java/org/keycloak/models/utils/RecoveryAuthnCodesUtils.java:56-62`

面向 I 回溯分析 · Suggestion

**問題**：RecoveryAuthnCodesUtils.getCredential 現在會優先回傳 getFederatedCredentialsStream() 的結果，也就是由 user storage provider 自行建構的 CredentialModel。但 RecoveryAuthnCodesFormAuthenticator:86-87 拿到它之後呼叫的是 authenticatedUser.credentialManager().removeStoredCredentialById(model.getId())——這條路徑（UserCredentialManager:113-117 → getStoreForUser:298-305）只會操作 Keycloak 自己的 local storage 或 federated storage 資料表，永遠不會回頭通知 user storage provider。傳進去的 id 卻是 provider 自己的 id，兩者不在同一個命名空間。

以本次新增的 provider 為例更明確：getCredentials 用 RecoveryAuthnCodesCredentialModel.createFromValues 建 model，而 createFromValues（:58-85）從頭到尾沒有呼叫 setId，所以回傳的 model 的 id 是 null——updateCredential:195 特地用 KeycloakModelUtils.generateId() 產生的 id 在這裡被丟掉了。作者在同一個 diff 裡替 OTP 補上 newOTP.setId(...)（:179），顯然知道 id 有用，recovery codes 這邊卻沒接上。

已找過反證：這條路徑目前打不到，因為要先 allCodesUsed() 成立，而 F-001 說明了 user storage 的 model 永遠不會用完。所以這是潛伏缺陷而非現行故障，嚴重度定在 Suggestion。但 F-001 一旦照建議修好、計數器開始前進，這一行就會變成實際會走到的靜默 no-op。

**證據**：
- `server-spi/src/main/java/org/keycloak/models/utils/RecoveryAuthnCodesUtils.java:56-62`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/RecoveryAuthnCodesFormAuthenticator.java:80-92`
- `model/storage/src/main/java/org/keycloak/credential/UserCredentialManager.java:113-117`
- `model/storage/src/main/java/org/keycloak/credential/UserCredentialManager.java:298-305`
- `server-spi/src/main/java/org/keycloak/models/credential/RecoveryAuthnCodesCredentialModel.java:58-85`

**修復方向**：在 RecoveryAuthnCodesFormAuthenticator 內先區分 credential 的來源再決定清除方式：來自 local storage 的才走 removeStoredCredentialById，來自 user storage 的應改為 credentialManager().disableCredentialType(RecoveryAuthnCodesCredentialModel.TYPE)（這條會轉發到 provider 的 disableCredentialType，見 UserCredentialManager:172-188），或明確不做清除、只保留 addRequiredAction 那一步並加註解說明原因。

同時建議讓 RecoveryAuthnCodesCredentialModel.createFromValues 接受並設定 id，或在 BackwardsCompatibilityUserStorage.getCredentials 建完 model 後補上 model.setId(myUser.recoveryCodes.getId())——目前 id 為 null 也會讓 CredentialDeleteHelper.java:64 的 credentialId.equals(c.getId()) 永遠比對不到（見 F-003）。

#### F-003 testsuite provider 對新 credential type 只實作了寫入側，credential 無法被刪除或停用 — `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:216-224`

面向 E 架構 · Suggestion

**問題**：這次替 recovery-authn-codes 擴充了 supportsCredentialType（:112-114）、updateCredential（:193-201）、isValid（:326-340）、isConfiguredFor（:275-276）與 getCredentials（:230-252），但 CredentialInputUpdater 的另外兩個方法沒有跟上：disableCredentialType（:216-224）仍然只處理 OTP，其他型別走到 log.infof("Unsupported to disable...") 就結束；getDisableableCredentialTypesStream（:254-264）也只會回報 CredentialModel.OTP。

把它接到實際的刪除流程上看後果：CredentialDeleteHelper.removeCredential 對 federated 使用者先試 getFederatedCredentialsStream().filter(c -> credentialId.equals(c.getId()))（:64）——因為 getCredentials 回傳的 model id 是 null（見 F-002），這個比對永遠不成立；接著落到 credentialId.endsWith("-id") 的相容分支（:73-78），呼叫 disableCredentialType("recovery-authn-codes")，而 provider 對這個型別什麼都不做。結果是 REST 回 204、使用者以為刪掉了，credential 其實還在 user storage 裡。

對照組就在同一個檔案：OTP 在 user storage 的移除路徑有 testOTPSetupAndRemoveThroughAccountMgmtAndLogin（:304-339）與 testDisableCredentialsInUserStorage（:341-367）兩條測試覆蓋，recovery codes 一條都沒有。

已確認這不會弄壞既有測試：testDisableCredentialsInUserStorage:359 的 Assert.assertNames(userRep.getDisableableCredentialTypes(), OTPCredentialModel.TYPE) 是精確集合比對，而 RecoveryAuthnCodesCredentialProvider 並沒有實作 CredentialInputUpdater，所以類別層級新加的 @EnableFeature(RECOVERY_CODES) 不會讓這個集合多出成員。

**證據**：
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:216-224`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:254-264`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:230-252`
- `services/src/main/java/org/keycloak/authentication/requiredactions/util/CredentialDeleteHelper.java:60-78`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:304-339`

**修復方向**：把 CredentialInputUpdater 的實作補完整：

```java
@Override
public void disableCredentialType(RealmModel realm, UserModel user, String credentialType) {
    MyUser myUser = getMyUser(user);
    if (myUser == null) return;
    if (isOTPType(credentialType)) {
        myUser.otp = null;
    } else if (RecoveryAuthnCodesCredentialModel.TYPE.equals(credentialType)) {
        myUser.recoveryCodes = null;
    } else {
        log.infof("Unsupported to disable credential of type: %s", credentialType);
    }
}
```

getDisableableCredentialTypesStream 同樣在 myUser.recoveryCodes != null 時加入 RecoveryAuthnCodesCredentialModel.TYPE，並讓 getCredentials 回傳的 model 帶上 id。動到 getDisableableCredentialTypesStream 後記得同步更新 testDisableCredentialsInUserStorage:359 的精確集合斷言，並補一條 recovery codes 版本的移除測試。

若團隊決定 recovery codes 在 user storage 就是不可刪除，那請在 supportsCredentialType 附近寫下這個決定與理由，讓下一個維護者不用重新推導。

#### F-004 recovery codes 在 CredentialInput 上的 payload 格式沒有文件，同一個欄位在寫入與驗證時是兩種形狀 — `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:112-131`

面向 E 架構 · Suggestion

**問題**：createRecoveryCodesCredential:119-123 把 12 組原始 code 用 JsonSerialization.writeValueAsString 序列化成一個 JSON 陣列字串，塞進 UserCredentialModel 的 challengeResponse。但驗證時，同一個 type 的 challengeResponse 是單一組原始 code（UserCredentialModel.buildFromBackupAuthnCode，server-spi/.../UserCredentialModel.java:135）。也就是說對 recovery-authn-codes 這個型別而言，CredentialInput.getChallengeResponse() 在 updateCredential 與 isValid 兩個情境下攜帶完全不同的資料形狀，而這件事沒有寫在任何 javadoc 裡——createRecoveryCodesCredential 的註解只有一行「Create RecoveryCodes credential either in userStorage or local storage」。

這個約定目前由兩個模組各自的 ad-hoc 呼叫維持：寫入端在 server-spi-private 的 CredentialHelper，讀取端在 testsuite-providers 的 BackwardsCompatibilityUserStorage:197 與 :334。任何第三方要支援這個功能，只能靠反推 testsuite provider 的原始碼來猜格式；格式若要調整，也必須兩處同步改。

已找過反證，確認現在不會弄壞任何人：內建的 federation provider 都不宣稱支援這個型別——LDAPStorageProvider:904 走 getSupportedCredentialTypes()、KerberosFederationProvider:156 只認 KERBEROS 與 PASSWORD、SSSDFederationProvider:178 與 IpatuuraUserStorageProvider:155 同理。在此之前 updateCredential 也從來不會帶著 recovery-authn-codes 被呼叫。所以這是往前看的介面設計問題，不是既有相容性破壞。

**證據**：
- `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:112-131`
- `server-spi/src/main/java/org/keycloak/models/UserCredentialModel.java:135`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:193-201`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:326-340`

**修復方向**：把格式變成明示的契約，二選一：

（a）在 CredentialHelper.createRecoveryCodesCredential 的 javadoc 明確寫出「對 recovery-authn-codes 型別，updateCredential 收到的 challengeResponse 是全部原始 code 的 JSON 陣列；isValid 收到的是單一組原始 code」，並在 RecoveryAuthnCodesUtils 提供一組共用的 serialize/deserialize helper，讓寫入端與 provider 端共用同一份程式碼，而不是各寫各的 JsonSerialization 呼叫。

（b）更乾淨的作法是不要重用 challengeResponse 的多義性——改用專屬的 CredentialInput 型別（例如帶 List<String> getCodes() 的子類別），讓型別系統本身表達差異，provider 端也不必解析 JSON。

無論走哪一條，BackwardsCompatibilityUserStorage 內的解析都應改用共用 helper，這樣格式只會有一個定義點。

#### F-005 新測試只覆蓋 happy path，關鍵行為（重複使用、錯誤 code、移除）都沒有斷言 — `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:240-268`

面向 G 測試 · Suggestion

**問題**：testRecoveryKeysSetupAndLogin 走完「設定 → 斷言 credential 在 user storage → 用第一組 code 登入 → appPage.assertCurrent()」就結束。缺的是這個功能真正該保證的行為：

1. 用過的 code 第二次不能用（這正是 F-001 的內容，也是這條測試現在無法察覺的原因）。
2. 錯誤的 code 會被拒絕——既有的 OTP 測試在 :225-230 就有「先送錯的 OTP、確認 getInputError() 非 null」這一段，recovery codes 版本沒有對應段落。
3. 移除 / 停用路徑完全沒覆蓋，而 OTP 在同一個檔案有 :304-339 與 :341-367 兩條。

輔助方法也顯示測試設計還沒收斂：assertUserHasRecoveryKeysCredentialInUserStorage(boolean)（:456-464）只被以 true 呼叫過一次，參數形同虛設；setupRecoveryKeysForUserWithRequiredAction 的 logoutOtherSessions 參數（:410）也永遠是 true，:419-421 那個 if 分支不會被執行到。

此外 appPage.assertCurrent() 只確認頁面到位，並沒有像既有 RecoveryAuthnCodesAuthenticatorTest 那樣以 events.expectLogin()...assertEvent() 驗證真的產生了登入事件與正確的 credential type detail。

**證據**：
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:240-268`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:456-464`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:410-436`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:304-339`

**修復方向**：在 testRecoveryKeysSetupAndLogin 之後補上（或拆成獨立測試）：

1. 重複使用同一組 code 必須失敗，並確認畫面要求的 code 編號會前進——這條會直接暴露 F-001。
2. 送一組不存在的 code，斷言 enterRecoveryAuthnCodePage.getFeedbackText() 或錯誤欄位有值、且仍停留在輸入頁。
3. 仿照 testOTPSetupAndRemoveThroughAccountMgmtAndLogin，補一條透過 account REST 移除 recovery codes 的測試，配合 F-003 的修正。
4. 讓 assertUserHasRecoveryKeysCredentialInUserStorage(false) 至少被用到一次（移除後的驗證正好是自然的使用點），並讓 logoutOtherSessions 參數有兩種取值，否則就把它拿掉。
5. 加上 events.expectLogin().detail(Details.CREDENTIAL_TYPE, RecoveryAuthnCodesCredentialModel.TYPE) 之類的事件斷言，讓測試檢查的是行為而不只是「頁面出現了」。

</details>

<details>
<summary>Nit（5）</summary>

#### F-006 測試 helper 從 RecoveryAuthnCodesAuthenticatorTest 整段複製，連錯字與用不到的參數一起帶過來 — `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:123-145`

面向 B 簡潔 · Nit

**問題**：configureBrowserFlowWithRecoveryAuthnCodes 與 enterRecoveryCodes 幾乎逐字複製自 RecoveryAuthnCodesAuthenticatorTest，帶來三個附帶問題：

1. :135 的 config.setAlias("delayed-suthenticator-config") 是原始碼裡的錯字（suthenticator → authenticator），複製之後變成兩份。alias 只是設定名稱，不會壞掉，但錯字擴散讓之後想搜尋或修正的人多找一處。

2. long delay 參數在這個測試裡永遠是 0（:243），而它在原本的檔案裡存在的理由是 test06AuthenticateRecoveryAuthnCodesSimultaneous 要製造同時登入的競態。連帶地 :134 那個 REQUIRED 的 delayed-authenticator execution 在這裡完全沒有作用，只是讓 flow 多一層。

3. 依團隊的 Rule of Three，這是第二次出現，還沒到該抽出的門檻，所以這裡列為 Nit 而不是 Suggestion。但既然已經有兩份，值得順手處理掉多餘的部分。

**證據**：
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:123-145`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:134-135`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:476-483`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/forms/RecoveryAuthnCodesAuthenticatorTest.java:123-148`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/forms/RecoveryAuthnCodesAuthenticatorTest.java:299-306`

**修復方向**：最小處理：把這個測試用不到的部分刪掉——移除 delay 參數與 delayed-authenticator 那段 addAuthenticatorExecution，讓 flow 只留 UsernamePasswordForm 與 RecoveryAuthnCodesForm；順手把 "delayed-suthenticator-config" 的錯字修掉（如果決定保留這段的話，兩個檔案一起改）。

若之後出現第三個使用者，再把 configureBrowserFlowWithRecoveryAuthnCodes 與 enterRecoveryCodes 抽到 org.keycloak.testsuite.util 底下的共用 helper，屆時 delay 再以參數形式保留。

#### F-007 createRecoveryCodesCredential 同時收下同一份祕密的兩種表述，且不告訴呼叫端寫到哪裡去了 — `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:112-131`

面向 B 簡潔 · Nit

**問題**：這個方法的參數同時有 RecoveryAuthnCodesCredentialModel credentialModel（內含 hash 過的 code）與 List<String> generatedCodes（原始 code），而前者本來就是由後者透過 RecoveryAuthnCodesCredentialModel.createFromValues 產生的（RecoveryAuthnCodesAction.java:110）。兩份表述並存代表呼叫端有可能傳進不一致的組合，而方法本身沒有任何檢查。

另外三個小點：

1. 回傳型別是 void，呼叫端無從得知 credential 最後落在 user storage 還是本地資料庫；同檔案的 createOTPCredential（:92-110）走的是同一個 if/else 結構，卻回傳 boolean。事件記錄或後續行為若想區分兩者，現在沒有依據。

2. :116 的 recoveryCodeCredentialProvider 在方法開頭就取得，但只有 else 分支（:129）會用到；user storage 接手時這次 getProvider 呼叫是白做的。

3. :116 用字串常值 "keycloak-recovery-authn-codes" 而非 RecoveryAuthnCodesCredentialProviderFactory.PROVIDER_ID。這一點有正當理由——CredentialHelper 在 server-spi-private，該常數在 services 模組，方向上不能反向依賴，且同檔案的 createOTPCredential:93 也是用 "keycloak-otp" 字串——所以只是提醒，不必然要改。

**證據**：
- `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:112-131`
- `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:87-110`
- `services/src/main/java/org/keycloak/authentication/requiredactions/RecoveryAuthnCodesAction.java:110-116`

**修復方向**：1. 只傳 generatedCodes，讓方法內部自己呼叫 RecoveryAuthnCodesCredentialModel.createFromValues 建出本地用的 model，消除兩份表述不一致的可能；或反過來只傳 credentialModel，並在 model 上提供取得原始 code 的途徑。

2. 讓方法回傳 boolean（true 表示由 user storage 建立），與 createOTPCredential 對齊。

3. 把 session.getProvider(...) 移進 else 分支內。

4. 若想消除字串常值，可考慮把 PROVIDER_ID 下移到 server-spi 或 server-spi-private 的常數類別，讓兩個模組共用；不做也可以，但值得加一行註解說明為何這裡是字串。

#### F-008 getCredentials 對可能回傳 null 的 getMyUser 直接解參考，與同檔案其他方法不一致 — `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:230-235`

面向 F 資料取用與資料庫 · Nit

**問題**：getMyUser（:226-228）是 users.get(...)，對不在 map 裡的使用者會回傳 null。同一個類別內，getDisableableCredentialTypesStream:259 寫 if (myUser != null && ...)、isConfiguredFor:271 寫 if (myUser == null) return false、isValid:286 也寫 if (myUser == null) return false——三個方法都防了。新增的 getCredentials:232-235 沒有防，var myUser = getMyUser(user) 之後直接 myUser.recoveryCodes。

這個方法的呼叫頻率並不低：RecoveryAuthnCodesUtils.getCredential 會在每次渲染 recovery code 登入表單與每次驗證後呼叫它，CredentialDeleteHelper:64 也會呼叫。實際觸發 null 需要一個 UserModel 存在但 map 內已無對應項目（例如使用者被移除後 user cache 仍持有 adapter），機率不高，所以列為 Nit——但同一個類別內三個方法防、一個不防，本身就是會誤導後續維護者的不一致。

**證據**：
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:230-235`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:226-228`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:254-264`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:267-281`

**修復方向**：與相鄰方法對齊即可：

```java
@Override
public Stream<CredentialModel> getCredentials(RealmModel realm, UserModel user) {
    MyUser myUser = getMyUser(user);
    if (myUser == null) return Stream.empty();
    ...
}
```

順帶建議把 var 換成明確的 MyUser 型別，與同檔案其他方法的寫法一致。

#### F-009 以 raw List 反序列化，型別資訊在編譯期就丟失 — `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:237-241`

面向 A 風格 · Nit

**問題**：:238 的 JsonSerialization.readValue(..., List.class) 與 :332 的 List generatedKeys 都是 raw type。後果有二：:238 的結果直接傳給 createFromValues(List<String>, ...)，實際元素型別完全沒有被檢查過，JSON 內若出現非字串元素會延後到 hashRawCode 才炸；:340 的 anyMatch(key -> key.equals(...)) 中的 key 是 Object，讀者無法從程式碼看出比對的是什麼。

這只是可讀性與型別表達的問題，不是缺陷：已確認 repo 根目錄的 pom.xml 沒有設定 checkstyle、spotless、maven-enforcer 或 -Werror，所以建置不會因為 unchecked 警告失敗，這裡不是「編譯器會擋下來」。

**證據**：
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:237-241`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:332-340`

**修復方向**：改用具名的型別參考，讓格式在程式碼上自我說明：

```java
private static final TypeReference<List<String>> CODES = new TypeReference<>() {};
...
List<String> generatedKeys = JsonSerialization.readValue(
        storedRecoveryKeys.getCredentialData(), CODES);
return generatedKeys.contains(input.getChallengeResponse());
```

:238 同樣改成 CODES。若採用 F-004 建議的共用 serialize/deserialize helper，這兩處會一併被收斂掉。

#### F-010 例外被吞掉、log 訊息寫錯 credential type 名稱，加上幾處多餘空白 — `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:243-245`

面向 A 風格 · Nit

**問題**：四處小問題，各自都不影響行為，但都會讓之後除錯或閱讀多花力氣：

1. :244 的 log.error("Could not deserialize  credential of type: recovery-codes") 沒有把 IOException e 傳進去，出問題時 stack trace 直接消失；訊息裡的型別名稱寫成 recovery-codes，而 RecoveryAuthnCodesCredentialModel.TYPE 實際是 recovery-authn-codes，照字串搜尋會找不到對應程式碼。另外「deserialize」與「credential」之間有多餘空白。同一個檔案的 :336 就有正確寫法可以參照。

2. :341 的 }  else { 多了一個空白。

3. CredentialHelper.java:119 的 recoveryCodesJson =  JsonSerialization... 在等號後多了一個空白。

4. BackwardsCompatibilityUserStorageTest.java:481 用完整套件名寫 org.junit.Assert.assertEquals(...)。檔案內已 import org.keycloak.testsuite.Assert，它繼承自 org.junit.Assert，直接寫 Assert.assertEquals(...) 即可，與同檔案其他斷言的寫法一致。

**證據**：
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:243-245`
- `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:341`
- `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:119`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:481`

**修復方向**：1. 改成 log.error("Could not deserialize credential of type: " + RecoveryAuthnCodesCredentialModel.TYPE, e)，把例外與正確的型別常數都帶上。
2、3. 移除多餘空白。
4. 改用已 import 的 Assert.assertEquals(...)。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 當 user storage 接手建立 recovery codes 時，使用者原本存在本地資料庫的 recovery-authn-codes credential 不會被清除，這是刻意保留還是漏掉的？

面向 F 資料取用與資料庫

**背景**：createRecoveryCodesCredential（server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:125-130）在 updateCredential 回傳 true 時就結束，不會走 else 分支。而只有 else 分支呼叫的 RecoveryAuthnCodesCredentialProvider.createCredential（services/src/main/java/org/keycloak/credential/RecoveryAuthnCodesCredentialProvider.java:39-42）會先刪掉既有的本地 credential 再建新的。所以一個先在本地有 recovery codes、之後其 user storage provider 開始支援這個型別的使用者，會同時存在兩份 credential。登入時 RecoveryAuthnCodesUtils.getCredential 優先取 user storage 那份，不會用錯；但本地那份仍會出現在 getStoredCredentialsStream 與 account console 的 credential 清單裡。無法在此判定為缺陷的原因是：同檔案的 createOTPCredential:97-105 是完全相同的結構，這個模式在 OTP 上早就存在，可能是團隊已知並接受的行為，也可能只是沒人踩過。

**如何確認**：作者確認 OTP 那條路徑是否曾經遇過孤兒 credential，或是否有既有 issue 記錄；若確認是已接受的行為，在 createRecoveryCodesCredential 加一行註解說明即可。若不是，則在 userStorageCreated 為 true 時一併清掉本地同型別的 credential，並補一條「provider 從不支援變成支援後重設 recovery codes」的測試。

#### Q-002 user storage provider 回報一組已經用完的 recovery codes credential 為 isConfiguredFor = true 時，登入表單會不會直接 500？

面向 E 架構

**背景**：RecoveryAuthnCodeInputLoginBean（services/src/main/java/org/keycloak/forms/login/freemarker/model/RecoveryAuthnCodeInputLoginBean.java:17-21）連續呼叫兩次 .get()：credentialModelOpt.get() 與 getNextRecoveryAuthnCode().get()。後者在 allCodesUsed() 時回傳 Optional.empty()（RecoveryAuthnCodesCredentialModel.java:36-41），會拋 NoSuchElementException。本地路徑不會走到，因為 RecoveryAuthnCodesFormAuthenticator:85-92 在 code 用完時會刪掉 credential 並加上 required action，之後 configuredFor 就是 false。但 user storage 路徑的 isConfiguredFor 完全由 provider 決定（UserCredentialManager.java:250-262），provider 只要回報「存在」就會讓表單被渲染。本次新增的 BackwardsCompatibilityUserStorage 因為 code 永遠不會用完（見 F-001），打不到這條路徑，所以無法用現有程式碼證實或證偽。

**如何確認**：先修 F-001 讓 code 真的會被消耗，然後加一條測試：把 12 組 code 全部用完，再嘗試登入，觀察是回到 required action 重新產生 codes（正確）還是 500。若確認會 500，則 RecoveryAuthnCodeInputLoginBean 需要處理 Optional.empty()，並在 CredentialInputValidator.isConfiguredFor 的 javadoc 上寫清楚「已耗盡的 credential 應回報 false」這個責任。

</details>
