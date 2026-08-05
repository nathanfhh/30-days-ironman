## 審查結論：Request Changes

> Critical 2 · Suggestion 3 · Nit 4 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | ✅ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：新增的覆寫方法缺少 @Override（F-008），新增的 javadoc 沒有寫出方法的前提條件（F-009）。命名與註解其餘部分與周邊一致。
- **B 簡潔**（未通過）：測試檔留下兩個沒有用到的 import（F-006）。其餘沒有過度設計，抽出 setPasswordlessPolicyForExternalKey() 是四個測試共用的合理抽取。
- **D API 慣例**（不適用）：本次 diff 沒有新增或修改任何 HTTP endpoint、URL、request/response schema 或驗證 schema，全部是 authenticator 內部流程與測試。
- **E 架構**（未通過）：同一個 auth note 的判讀出現兩份實作，且 util 套件反向依賴 browser 套件（F-007）。
- **G 測試**（未通過）：reauthenticationOfUserWithoutPasskey 的名稱與實際涵蓋範圍不符，真正新開放的情境沒有被測到（F-003）；兩處以 try/fail/catch(Exception) 取代同專案既有的 assertThrows 慣例，斷言精確度下降（F-004）。新增的四個測試本身斷言品質不錯：都檢查具體 DOM 元素、具體錯誤訊息與具體 event detail，不是只確認「有回應」。
- **H 非 Python 檔**（未通過）：本 diff 全部是 Java 與 FreeMarker 影響面，依此維度的用意檢查登入頁的各個渲染分支：re-authentication 這個新開放的分支，對「realm 有開 passkeys、但本人沒有 passkey credential」的使用者會渲染出一個只能失敗的操作（F-005）。
- **I 回溯分析**（未通過）：isConditionalPasskeysEnabled 的簽章在第二個 commit 被改動，呼叫端沒有全部跟上（F-001），且新增的 user != null 條件把改動前的語意反轉（F-002）。兩者都是本維度第 1、3 軸的典型案例。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 branch 有兩個 commit、兩位作者。bba869b3d524 是標題所說的修正；3214b188de80「Add user parameter requirement to isConditionalPasskeysEnabled method」是另一位作者加上的簽章調整，與 re-authentication 修正無關，而本次兩個 Critical（F-001、F-002）都出自它。建議把這個 commit 從本 PR 拆出或撤回，讓 re-authentication 修正單獨被評估。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。本 diff 未新增任何相依套件，影響有限。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且預設規則目錄不存在，本次未執行 SAST 掃描。Java 的認證流程改動因此完全靠人工閱讀與 grep 驗證。 |
| ruff | 已執行 | in_diff 0、outside_diff 6 |
| ty | 略過 | ty 未安裝（不在 PATH 上）。本次 diff 沒有 Python 檔，即使安裝也無適用範圍。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。本次 diff 沒有 JavaScript/TypeScript 檔，即使安裝也無適用範圍。 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號圖；呼叫者列舉與影響範圍分析全部改用 grep 完成（F-001、F-002 的完整性主張都是 grep 全庫確認的）。 |
| ncr-fresh-eyes | 略過 | 本執行環境沒有可派發 subagent 的工具，無法執行「未套用檢查清單的第一次閱讀」。依 skill 規定不得由主 agent 自行模擬，故略過並在此揭露：本報告缺少這一層獨立視角。 |
| ncr-quality-check | 略過 | 同上，無法派發 subagent，報告 JSON 未經獨立覆核。report_model.py 的機械驗證有通過（結論與 findings 一致、每個 finding 都有 fix、九個維度都有判定）。 |
| javac / maven | 略過 | 審查環境無法連外，Maven 無法解析相依套件，因此無法實際編譯或執行 Arquillian 測試。F-001 的編譯失敗結論是以全庫 grep 確認「無任何無參數多載存在」推得，而非由編譯器輸出證實。 |

### Critical

#### F-001 UsernameForm 仍以無參數形式呼叫 isConditionalPasskeysEnabled()，services 模組無法編譯 — `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernameForm.java:47`

面向 I 回溯分析 · Critical

**問題**：commit 3214b188de80 把 UsernamePasswordForm.isConditionalPasskeysEnabled() 改成 isConditionalPasskeysEnabled(UserModel user)，同時更新了同一個檔案裡的兩處呼叫（UsernamePasswordForm.java:115、137），但沒有更新繼承自它的 UsernameForm。UsernameForm.java:47 仍寫 `!isConditionalPasskeysEnabled()`，而全庫已經沒有任何無參數的多載可以對應，這是 javac 的 method 解析失敗，不是執行期問題——整個 services 模組會編譯失敗，PR 內所有新測試也就不可能跑過。

反證搜尋：以 `grep -rn "isConditionalPasskeysEnabled" --include=*.java` 掃過整個 repository，只有四個命中——UsernamePasswordForm.java 的 115、137、160 三行與 UsernameForm.java:47。父類別 AbstractUsernameFormAuthenticator、AbstractFormAuthenticator 以及 Authenticator 介面都沒有同名方法，也沒有 varargs 版本可以吸收零參數呼叫。缺的那一半不在這個 diff 的別處。

**證據**：
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernameForm.java:47`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:160`

**修復方向**：把 UsernameForm.java:47 改成傳入同一個 user：`if (context.getUser() != null && !isConditionalPasskeysEnabled(context.getUser()))`。不過在這個位置 `context.getUser() != null` 已經先成立，新增的 `user != null` 條件恆為真，所以這一行的行為與改動前完全相同——這正好說明 commit 3214b188de80 的簽章調整沒有帶來任何好處。更乾淨的處理是整個撤回該 commit，回到 `isConditionalPasskeysEnabled()`（見 F-002）。

#### F-002 isConditionalPasskeysEnabled 新增的 user != null 條件把判斷反轉，一般登入頁不再出現 passkeys — `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:160`

面向 I 回溯分析 · Critical

**問題**：改動前 UsernamePasswordForm.challenge 的條件是 `context.getUser() == null && webauthnAuth != null && webauthnAuth.isPasskeysEnabled()`——使用者尚未被識別時才填入 webauthn 資料。第一個 commit 的修正是把 user 條件整個拿掉，讓 re-authentication（user 已設定）也能拿到 passkeys。第二個 commit 加回的是 `user != null`，方向剛好相反：現在只有 user 已被識別時才填。

後果是 conditional UI 在「使用者尚未識別」的第一張登入頁完全消失。此時 fillContextForm 不被呼叫，WebAuthnConditionalUIAuthenticator.java:43 的 ENABLE_WEBAUTHN_CONDITIONAL_UI 屬性沒有被設定，themes/src/main/resources/theme/base/login/passkeys.ftl:2 的 `<#if enableWebAuthnConditionalUI?has_content>` 因此整段不渲染——沒有 `<form id="webauth">`、沒有 passkey 按鈕，login.ftl 的 username 欄位 autocomplete 也退回 "username" 而不是 "username webauthn"。這正是 passkeys 這個功能最主要的使用情境（免帳號、以 discoverable credential 登入）。

反證搜尋：檢查了是否有別的路徑會在 user == null 時補上這些屬性。UsernamePasswordForm.authenticate 的 user == null 分支（UsernamePasswordForm.java:103-113）只處理 loginHint / rememberMe，不碰 webauthn；OrganizationAuthenticator.java:357-361 有自己的 user == null 分支，但那只涵蓋 organization 流程，不涵蓋一般的 browser flow。缺口沒有被別處補上。

本 PR 自己的測試也否定了這個條件：PasskeysUsernamePasswordFormTest.java:195-196 與 237-238 在尚未輸入帳號的第一張登入頁上斷言 autocomplete 是 "username webauthn"、且 `//form[@id='webauth']` 存在。以目前 HEAD 的邏輯這兩處必然失敗。

**證據**：
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:160`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:115`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:137`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:195`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:237`

**修復方向**：撤回 commit 3214b188de80，讓判斷回到 `webauthnAuth != null && webauthnAuth.isPasskeysEnabled()`（呼叫端同時回到無參數形式，F-001 也一併消失）。若確實想引入 user 相關的判斷，該判斷不是「user 是否存在」，而是「已知的 user 是否真的擁有 passwordless WebAuthn credential」，而且只能在 user 非 null 時額外收斂、不能反過來要求 user 必須存在——那是另一件事，見 F-005。

<details>
<summary>Suggestion（3）</summary>

#### F-003 reauthenticationOfUserWithoutPasskey 測的是 realm 關閉 passkeys，不是使用者沒有 passkey — `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:279`

面向 G 測試 · Suggestion

**問題**：方法名稱與上方註解（「Test user re-authentication with password when passkeys feature enabled, but passkeys is not enabled for the realm」）指向兩件不同的事，而實際 setup 只做了後者：`setWebAuthnPolicyPasskeysEnabled(Boolean.FALSE)`。使用者本身有沒有 passkey credential 從頭到尾沒有被操作，測試用的 test-user@localhost 只是剛好沒有註冊過 WebAuthn。

因此「realm 有開 passkeys、但這位使用者沒有 passkey」——也就是本 PR 真正新開放出來的那個組合——沒有任何測試涵蓋。這個組合的行為見 F-005。

**證據**：
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:279`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:283`

**修復方向**：把方法改名成能反映實際 setup 的名字（例如 reauthenticationWhenPasskeysDisabledForRealm），並另外補一個測試：realm 維持 setWebAuthnPolicyPasskeysEnabled(TRUE)，以一個沒有 WebAuthn credential 的使用者（例如 test-user@localhost）走 prompt=login，斷言該頁面上 passkey 相關元素的期望狀態。這個測試同時會把 F-005 的設計決定釘住。

#### F-004 以 try / fail / catch (Exception) 判斷元素不存在，會吞掉非預期的例外 — `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:291`

面向 G 測試 · Suggestion

**問題**：這兩段的意圖是「webauth form 不應該存在」，寫法是呼叫 driver.findElement 後接 fail()，再用 catch (Exception nsee) 當成通過。兩個問題：一是 catch 的範圍是整個 Exception，任何 StaleElementReferenceException、TimeoutException 或其他 WebDriver 層錯誤都會被當成「元素不存在」而讓測試變綠；二是當元素真的存在時，先爆的是上一行 assertThat(element, nullValue()) 的 AssertionError，測試會以「Expected null」這個與意圖無關的訊息失敗，fail() 裡那句說明反而印不出來。

同專案已經有直接對應的慣用寫法：PasskeysUsernameFormTest.java:202 用 `Assert.assertThrows(NoSuchElementException.class, () -> driver.findElement(By.xpath("//form[@id='webauth']")))`，而本 PR 在 PasskeysOrganizationAuthenticationTest 移除的正是同一個寫法。

**證據**：
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:291`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:312`

**修復方向**：改成 `Assert.assertThrows(NoSuchElementException.class, () -> driver.findElement(By.xpath("//form[@id='webauth']")))`，並移除隨之不再需要的 `import static org.junit.Assert.fail;` 與 `import static org.hamcrest.Matchers.nullValue;`（若其他行仍在用 nullValue 則保留）。

#### F-005 re-authentication 時已知使用者是誰，卻仍只用 realm 層級條件決定是否顯示 passkey 操作 — `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnConditionalUIAuthenticator.java:59`

面向 H 非 Python 檔 · Suggestion

**問題**：isPasskeysEnabled() 只看 Profile.Feature.PASSKEYS 與 realm 的 WebAuthnPolicyPasswordless.isPasskeysEnabled()，完全不看這位使用者有沒有 passwordless WebAuthn credential；conditional UI 這條路徑上 shouldShowWebAuthnAuthenticators 又被覆寫成固定 false（WebAuthnConditionalUIAuthenticator.java:65），所以也不會去查 credential。第一張登入頁不知道使用者是誰，這樣做是必然的；但 re-authentication 的前提就是 context.getUser() 已經有值，這裡本來有條件做得更準。

實際結果：realm 開了 passkeys 但本人沒有 passkey 的使用者，在 re-authentication 頁面上會看到 passkeys.ftl:32 那個「以 passkey 登入」按鈕，按下去 navigator.credentials.get() 找不到可用 credential 只能失敗。走 identity-first 流程（UsernameForm）時更明顯：此時 USERNAME_HIDDEN 已被設為 true，login-username.ftl:12 因而不渲染帳號欄位，該頁上除了那個必定失敗的 passkey 按鈕之外沒有任何憑證輸入欄位。

作者自己的意圖也指向這個方向——PasskeysUsernamePasswordFormTest.java:279 的測試就叫 reauthenticationOfUserWithoutPasskey，只是它實際變動的是 realm policy 而不是使用者的 credential（見 F-003）。

（這不是 F-002 的重述：F-002 說的是 user == null 的路徑被關掉，這一條說的是 user != null 的路徑放得太寬。兩者方向相反，會同時存在。）

**證據**：
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnConditionalUIAuthenticator.java:59`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:115`
- `themes/src/main/resources/theme/base/login/passkeys.ftl:32`
- `themes/src/main/resources/theme/base/login/login-username.ftl:12`

**修復方向**：在 user 已知時多收一層，例如把條件寫成「webauthnAuth 可用且 realm 開啟 passkeys，且（user == null 或 user.credentialManager().isConfiguredFor(WebAuthnCredentialModel.TYPE_PASSWORDLESS)）」——未識別的使用者維持現狀，已識別但沒有 passkey 的使用者則不渲染 conditional UI，UsernameForm 也就會回到原本 context.success() 直接跳過表單的行為。若團隊評估後認為「一律顯示、讓瀏覽器自己說沒有可用的 credential」才是想要的行為，請在 isConditionalPasskeysEnabled 上留一行註解說明這是刻意的，並依 F-003 補上對應測試把它釘住。

</details>

<details>
<summary>Nit（4）</summary>

#### F-006 兩個測試檔各留下一個沒有用到的 import — `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:35`

面向 B 簡潔 · Nit

**問題**：PasskeysUsernamePasswordFormTest 新增了 `import org.keycloak.models.credential.PasswordCredentialModel;`，但整個檔案除了 import 那一行之外沒有再出現這個型別（該用法留在 PasskeysUsernameFormTest，不在這個檔案）。PasskeysOrganizationAuthenticationTest 則是相反方向：本 PR 移除了兩處 `Assert.assertThrows(NoSuchElementException.class, ...)`，`import org.openqa.selenium.NoSuchElementException;` 就此變成孤兒。兩者都以全檔 grep 確認只剩 import 那一次命中。

**證據**：
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:35`
- `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysOrganizationAuthenticationTest.java:51`

**修復方向**：刪掉這兩行 import。若 F-004 改用 assertThrows，NoSuchElementException 在 PasskeysUsernamePasswordFormTest 反而會需要被 import，順序上先處理 F-004 再清這裡比較不會來回。

#### F-007 同一個 auth note 出現兩份判讀實作，且 util 套件反向依賴 browser 套件 — `services/src/main/java/org/keycloak/authentication/authenticators/util/AuthenticatorUtils.java:35`

面向 E 架構 · Nit

**問題**：AuthenticatorUtils 新增的 setupReauthenticationInUsernamePasswordFormError 自己讀一次 USER_SET_BEFORE_USERNAME_PASSWORD_AUTH 並 Boolean.parseBoolean，而 AbstractUsernameFormAuthenticator.java:257 的 isUserAlreadySetBeforeUsernamePasswordAuth 做的是一模一樣的事，而且仍被 getUser()（:139）與 getDefaultChallengeMessage()（:250）使用。同一個 note 的語意現在有兩個地方定義，未來要改判讀方式（例如改成三態）就得同時改兩處。

連帶的是相依方向：為了這個靜態方法，常數被從 protected 放寬成 public（AbstractUsernameFormAuthenticator.java:58），並讓 authenticators.util 反過來 import authenticators.browser——而 browser 本來就 import util（例如 AbstractUsernameFormAuthenticator 使用 AuthenticatorUtils.dummyHash）。兩個套件從此互相依賴，是可以編譯但會逐漸難以拆解的形狀。

**證據**：
- `services/src/main/java/org/keycloak/authentication/authenticators/util/AuthenticatorUtils.java:35`
- `services/src/main/java/org/keycloak/authentication/authenticators/util/AuthenticatorUtils.java:125`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/AbstractUsernameFormAuthenticator.java:257`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/AbstractUsernameFormAuthenticator.java:58`

**修復方向**：兩個方向都可以：把常數移到不依賴任何一邊的位置（例如 org.keycloak.models.Constants 或 authenticators.util 自己），讓 browser 反過來引用它；或是保留常數位置，把 AbstractUsernameFormAuthenticator.isUserAlreadySetBeforeUsernamePasswordAuth 改成委派給新的 util 方法所使用的同一個判斷式，讓判讀邏輯只有一份。

#### F-008 新增的覆寫方法 shouldShowWebAuthnAuthenticators 缺少 @Override — `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnConditionalUIAuthenticator.java:65`

面向 A 風格 · Nit

**問題**：同一個檔案裡 fillContextForm（:41）與 createErrorResponse（:47）都標了 @Override，新加的 shouldShowWebAuthnAuthenticators 沒有。這不只是排版一致性：這個方法的整個作用就是覆寫父類別的同名方法讓它回傳 false，一旦父類別未來改了簽章（例如參數換成 AuthenticationFlowContext 以外的型別），沒有 @Override 的版本會安靜地變成一個沒有人呼叫的新方法，conditional UI 就會退回顯示 authenticator 清單，而編譯器不會有任何抱怨。

**證據**：
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnConditionalUIAuthenticator.java:65`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnAuthenticator.java:128`

**修復方向**：加上 @Override。

#### F-009 新的 shouldShowWebAuthnAuthenticators 擴充點有隱含前提，javadoc 沒有寫出來 — `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnAuthenticator.java:124`

面向 A 風格 · Nit

**問題**：原本 fillContextForm 的分支條件就寫在現場（`if (user != null)`），user 非 null 與進入分支是同一件事。抽成可覆寫的 shouldShowWebAuthnAuthenticators 之後，分支條件與 user 的狀態被拆成兩件事，但分支內容（WebAuthnAuthenticator.java:103）仍直接把 user 交給 WebAuthnAuthenticatorsBean，而該建構子第一行就是 user.credentialManager()（WebAuthnAuthenticatorsBean.java:41），沒有 null 檢查。也就是說任何回傳 true 但 user 為 null 的覆寫都會 NPE。

新增的 javadoc（:124-127）只說明了「什麼時候該顯示」，沒有說「回傳 true 就必須保證 context.getUser() 非 null」。目前全庫只有兩個實作——基底的 `context.getUser() != null` 與 conditional UI 的固定 false——都不違反這個前提，所以這不是現存缺陷，是留給下一位擴充者的地雷。

**證據**：
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnAuthenticator.java:124`
- `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnAuthenticator.java:103`
- `services/src/main/java/org/keycloak/forms/login/freemarker/model/WebAuthnAuthenticatorsBean.java:41`

**修復方向**：在 javadoc 補一句前提（回傳 true 的實作必須保證 context.getUser() 非 null），或更穩健地把條件寫成 `if (user != null && shouldShowWebAuthnAuthenticators(context))`，讓 null 檢查留在呼叫端而不是依賴覆寫者的自律。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 WebAuthnConditionalUIAuthenticator 是否被視為 Keycloak 之外的擴充可以直接使用的 API？把 shouldShowWebAuthnAuthenticators 固定成 false 之後，fillContextForm 對「使用者已識別但沒有 credential」的情況不再回傳 null，這個行為變化會不會影響 repository 之外的使用者？

面向 E 架構

**背景**：改動前 fillContextForm 在 user 非 null 且 authenticators 清單為空時回傳 null（WebAuthnAuthenticator.java:104-107），WebAuthnAuthenticator.authenticate（:79-82）就是靠這個 null 決定不送出 challenge。覆寫成固定 false 之後這條路對 conditional UI 永遠不會發生。以 grep 掃過整個 repository，WebAuthnConditionalUIAuthenticator 只在 UsernamePasswordForm.java:50 與 OrganizationAuthenticator.java:77 被建構，兩處都直接呼叫 fillContextForm 並忽略回傳值，所以 repository 內沒有受影響者。但這個類別是 public、位在 org.keycloak.authentication.authenticators.browser，外部 provider 是否有人繼承或直接使用，從這裡無法列舉。

**如何確認**：由熟悉這塊的維護者確認 WebAuthnConditionalUIAuthenticator 屬於 internal 實作細節（不受相容性承諾約束），或在類別上補一個標記說明；若屬於公開擴充點，則需要在 release note 中說明這個行為變化。

#### Q-002 prompt=login 之外的 re-authentication 觸發路徑（max_age、required action 觸發的重新認證、account console 的 re-auth）是否也走到同一段 UsernamePasswordForm.authenticate（context.getUser() 已設定）？

面向 G 測試

**背景**：本 PR 新增的四個測試全部以 oauth.loginForm().prompt(OIDCLoginProtocol.PROMPT_VALUE_LOGIN) 觸發 re-authentication。修正本身是寫在 UsernamePasswordForm / UsernameForm 這一層，只要其他觸發方式最後也落在同一個 authenticator 且 user 已設定，就會一併被修好；但這件事沒有被任何測試釘住，而 authentication flow 的組態是 realm 可調的（本 PR 的測試就用 switchExecutionInBrowserFormToProvider 換過 execution）。在無法編譯與執行測試的環境下，只靠靜態閱讀不足以斷言涵蓋範圍。

**如何確認**：在既有測試類別中加一個以 max_age=0 觸發 re-authentication 的變體，斷言與 prompt=login 版本相同的頁面狀態；或由作者說明 issue #41242 / #41008 描述的重現情境是否只有 prompt=login。

</details>
