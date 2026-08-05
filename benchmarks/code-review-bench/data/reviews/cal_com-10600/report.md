## 審查結論：Request Changes

> Critical 1 · Suggestion 6 · Nit 6 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ✅ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

- **A 風格**（未通過）：命名與型別上的幾處鬆動：BackupCode.tsx 匯出的 function 叫 TwoFactor、backupCodes 用 useState([]) 推導成 never[]、新錯誤碼繞過既有的 errorMessages map、blob URL 沒有回收。見 F-008 ~ F-011。
- **C 安全**（未通過）：存在一條可繞過密碼驗證的登入路徑（F-001，Critical）。另有 backup code 以可逆加密而非雜湊儲存（F-002）、解密結果未做防禦性檢查（F-012）。
- **D API 慣例**（未通過）：新增的 backupCode 欄位在兩個入口都直接當字串用，沒有型別或 schema 驗證，非字串輸入會讓 handler 以 500 收場而不是 400（F-007）。URL 命名、HTTP verb、授權檢查（getServerSession + session.user.id）這幾項沒有問題。
- **E 架構**（未通過）：backup code 的生命週期不完整：沒有重新產生的入口、沒有剩餘數量提示、用完之後的錯誤訊息會誤導（F-005）。另外 onEnable() 被移到最後一步的按鈕後面，讓「2FA 已啟用」的狀態同步變成可跳過的（F-006）。
- **G 測試**（未通過）：整個 backup code 的驗證路徑（用 backup code 登入、用 backup code 關閉 2FA）沒有任何測試（F-003）；同時 diff 內留下一個永遠會通過的斷言與 FIXME 而沒有修（F-004）。
- **H 非 Python 檔**（未通過）：本次 diff 全部是非 Python 檔（TSX / TS / JSON / SQL / Prisma），因此本維度就是主戰場。多分支 UI 逐一檢視後，DisplayBackupCodes 這一支與 lost-access 切換分支各有問題：F-006、F-011、F-013。已確認 Button 預設 type="button"（packages/ui/components/button/Button.tsx:125），所以包在 <Form> 內的 Download / Copy / Close 按鈕不會誤觸表單送出。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 同時做了三件可以分開的事：(1) backup code 的產生／儲存／驗證（後端 + schema + migration）、(2) 登入與設定畫面的 lost-access 分支（前端）、(3) 兩個與 backup code 無關的順帶修改——packages/ui/components/form/inputs/Input.tsx 的 tabIndex={-1}、以及 apps/web/components/settings/EnableTwoFactorModal.tsx 把 TextField 換成 PasswordField。第 (3) 類改動落在共用 UI 元件上，會影響所有使用 PasswordField 的畫面，卻被藏在一個 2FA 功能 MR 裡；審查與回溯（git blame / revert）都會比較難。建議至少把 Input.tsx 拆成獨立 MR。決定權在人，這裡只把它標出來。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 略過 | ruff 0.15.8 已安裝，也確實執行完成（exit 0、0 筆診斷），但本次 diff 全部是 TypeScript / TSX / JSON / SQL / Prisma，沒有任何 Python 檔案。因此這個 0 不代表「掃過而乾淨」，而是「對這次變更沒有覆蓋」，不列為 ok 以免誤讀。 · exit code 0 · in_diff 0、outside_diff 0 |
| oxlint | 略過 | 未安裝（不在 PATH 上）。本次 diff 主體正是 TypeScript / TSX，等於這次審查完全沒有 JS/TS lint 覆蓋。 |
| ty | 略過 | 未安裝（不在 PATH 上）。ty 是 Python 型別檢查，對本次 TypeScript 變更本來也不適用。 |
| tsc / next build | 略過 | checkout 沒有 node_modules，執行環境也沒有網路，無法安裝相依套件，因此無法做任何型別檢查或建置驗證。本報告中所有與型別有關的判斷（例如 F-009 的 never[]）都是人工閱讀原始碼得出的，沒有編譯器背書。 |
| trivy | 略過 | 未安裝（不在 PATH 上）。相依套件漏洞、設定錯誤與 secret 掃描這次都沒有執行。 |
| opengrep | 略過 | 未安裝，且預設的 Semgrep 規則目錄也不存在。SAST 這次沒有執行——對一個改動認證流程的 MR 來說，這是這份報告最大的覆蓋缺口。 |
| codegraph | 略過 | 未安裝。Phase 3 的呼叫路徑列舉與 Dimension I 的回溯全部改以 grep 完成（已對 TwoFactorAuthAPI.disable、@components/auth/TwoFactor、backupCodes 三個符號做全庫 grep）。 |
| ncr-fresh-eyes（subagent） | 略過 | 本次執行環境沒有可派送 subagent 的工具，無法 dispatch。依 SKILL.md 的規定不自行模擬，直接揭露：這份報告缺少「未被本 skill 框架塑形」的第一眼視角。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法 dispatch。report.json 僅通過 report_model.py 的機械驗證，沒有經過獨立的品質複核。 |

### Critical

#### F-001 帶 backupCode 時密碼驗證會被跳過：只要 totpCode 非空即可，而該 totpCode 從頭到尾不會被檢查 — `packages/features/auth/lib/next-auth-options.ts:122-128`

面向 C 安全 · Critical

**問題**：authorize() 裡密碼檢查的條件是 `if (user.password && !credentials.totpCode)`（next-auth-options.ts:122）——只要 totpCode 是非空字串，整段密碼驗證就會被略過。這個設計原本是為了 email + TOTP 的登入流程而存在，安全性由下面那段「totpCode 一定會被 totpAuthenticatorCheck 驗證」來守住。

這次新增的分支把守衛拆掉了：`if (user.twoFactorEnabled && credentials.backupCode)`（next-auth-options.ts:130）排在 `else if (user.twoFactorEnabled)` 之前，只要 backupCode 有值就走 backup code 分支，**totpCode 在這條路上完全不會被驗證**。兩者相乘的結果是：帶一個任意非空的 totpCode，加上一組有效的 backup code，就能在不知道密碼的情況下完成登入。

已找過反證，都不成立：(a) 上游沒有攔截——next-auth 的 CredentialsProvider 會把 request body 原樣交給 authorize，且 backupCode / totpCode 兩個欄位本來就宣告在 credentials 設定裡（next-auth-options.ts:104-113）；(b) checkRateLimitAndThrowError（next-auth-options.ts:109）只限速，不驗密碼；(c) 後續的 validateRole 裡雖然出現 isPasswordValid(credentials.password, ...)，但那只用來把 ADMIN 降級成 INACTIVE_ADMIN，不參與認證；(d) 前端正常流程確實會把 totpCode 清空（login.tsx:118），但前端不是信任邊界，這條路徑是直接打 next-auth 的 credentials callback endpoint（apps/web 底下的 api/auth/callback/credentials）。

對照組：apps/web/pages/api/auth/two-factor/totp/disable.ts:41-44 的密碼驗證是無條件的，沒有這個問題，可見是這次新分支特有的互動缺陷。

影響面之所以實際存在，是因為這個 MR 本身就把 backup code 設計成「會離開瀏覽器」的秘密：EnableTwoFactorModal.tsx:271-274 提供 cal-backup-codes.txt 下載、:262-269 提供複製到剪貼簿。一份放在下載資料夾的純文字檔外洩，本來只應該讓攻擊者少一個因素，現在卻等同拿到整個帳號。

**證據**：
- `packages/features/auth/lib/next-auth-options.ts:122-128`
- `packages/features/auth/lib/next-auth-options.ts:130-155`
- `apps/web/pages/auth/login.tsx:157`

**POC**：

````
前提：受害者是 identityProvider=CAL、twoFactorEnabled=true 的帳號，攻擊者持有其 email 與任一組尚未使用的 backup code（例如從外洩的 cal-backup-codes.txt 取得），但不知道密碼。

```bash
BASE=https://<host>
curl -i -X POST "$BASE/api/auth/callback/credentials" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H "Cookie: next-auth.csrf-token=<csrf-cookie>" \
  --data-urlencode 'csrfToken=<csrf-token>' \
  --data-urlencode 'email=victim@example.com' \
  --data-urlencode 'password=wrong-password' \
  --data-urlencode 'totpCode=000000' \
  --data-urlencode 'backupCode=1a2b3c4d5e' \
  --data-urlencode 'callbackUrl=/' \
  --data-urlencode 'json=true'
```

預期（修正後）：回傳 IncorrectEmailPassword。實際（本 branch）：totpCode 非空使 next-auth-options.ts:122 的密碼檢查整段跳過，backupCode 分支比對成功後直接發出 session cookie，登入完成。把 totpCode 改成空字串則會正確地要求密碼——這個對照本身就是缺陷存在的證明。
````

**影響範圍**：完整的帳號接管：拿到 session 後即可讀寫該使用者的行程、聯絡人 email、已連結的日曆與付款設定憑證，並可在 disable.ts 之外的路徑持續操作。若受害者是團隊／組織管理員，影響擴及該組織成員的行程資料。PHI 不在此系統範圍內（見 meta.phi_trigger），所以損害以個資與排程資料為限，沒有臨床資料成本。攻擊門檻是「email + 一組 backup code」，而本 MR 正好鼓勵使用者把 backup code 以純文字檔存到本機（EnableTwoFactorModal.tsx:271-274），所以這不是理論上的組合。

**風險處置**：Mitigate（降低）

**修復參考**：next-auth-options.ts:122-128 收斂跳過密碼的條件，並在 :130 之前拒絕同時帶 totpCode 與 backupCode 的請求

**修復方向**：把「跳過密碼」的條件收斂回原本的語意——只有在 totpCode 真的會被驗證的路徑上才允許跳過。最小修法是讓密碼檢查不要被 backupCode 路徑繞過：

```ts
// next-auth-options.ts
const usingBackupCode = Boolean(user.twoFactorEnabled && credentials.backupCode);
if (user.password && (usingBackupCode || !credentials.totpCode)) {
  const isCorrectPassword = await verifyPassword(credentials.password, user.password);
  if (!isCorrectPassword) throw new Error(ErrorCode.IncorrectEmailPassword);
}
```

更乾淨的做法是在進入 backup code 分支前先拒絕同時帶兩個第二因素的請求（`if (credentials.backupCode && credentials.totpCode) throw new Error(ErrorCode.IncorrectEmailPassword)`），讓「一次只提供一個第二因素」變成明確的前置條件，而不是靠 if/else 的順序隱含保證。兩者建議都做。

<details>
<summary>Suggestion（6）</summary>

#### F-002 backup code 以可逆對稱加密儲存而非雜湊，比對也用線性 indexOf — `apps/web/pages/api/auth/two-factor/totp/setup.ts:60-69`

面向 C 安全 · Suggestion

**問題**：backup code 是使用者出示的秘密，用途只有「比對是否相等」，從來不需要還原成明文。這裡卻用 symmetricEncrypt（AES-256，單一全站金鑰 CALENDSO_ENCRYPTION_KEY）存起來（setup.ts:67），等於任何拿到 DB dump 加金鑰、或能在 app server 上執行程式碼的人，都能一次讀出所有使用者的全部 backup code。密碼本身是雜湊的（verifyPassword），backup code 卻不是，這個落差沒有理由。

公允地說，這不是 Critical：同一把金鑰保護的 twoFactorSecret 也是可逆的，而拿到 twoFactorSecret 一樣能繞過第二因素，所以改用雜湊並不會立刻縮小現有的攻擊面。它的價值在於不再擴大——backup code 是新增的長期有效憑證，沒有理由讓它繼承 TOTP secret「必須可逆」的限制。

另外 next-auth-options.ts:143 用 Array.prototype.indexOf 比對，是逐字元短路比較，非常數時間。以 40 bits 的隨機碼加上既有的 rate limit，時間側通道實務上不可行，但改成雜湊後順手用 timingSafeEqual 幾乎沒有成本。

**證據**：
- `apps/web/pages/api/auth/two-factor/totp/setup.ts:60-69`
- `packages/features/auth/lib/next-auth-options.ts:137-144`
- `packages/lib/crypto.ts:15-24`

**修復方向**：產生時仍回傳明文給使用者一次，但只存雜湊：

```ts
// setup.ts
const backupCodes = Array.from(Array(10), () => crypto.randomBytes(5).toString("hex"));
const hashed = await Promise.all(backupCodes.map((c) => hashPassword(c))); // 沿用既有的密碼雜湊工具
// data: { backupCodes: JSON.stringify(hashed), ... }
```

驗證端改成逐一比對雜湊，並把這段邏輯抽成單一 helper（例如 packages/features/auth/lib/verifyBackupCode.ts），讓 next-auth-options.ts 與 totp/disable.ts 共用——這兩份副本目前已經開始分歧（一份把用掉的 code 標成 null，一份靠最後整批清空），抽出來可以順便消掉這個分歧。

注意：改成雜湊後既有已發出的 backup code 會失效，需要一個 migration 策略（例如把現有 backupCodes 一律清為 NULL 並通知使用者重新產生），這一點要和 F-005 的重新產生入口一起規劃。

#### F-003 backup code 的驗證路徑完全沒有測試覆蓋 — `apps/web/playwright/login.2fa.e2e.ts:11`

面向 G 測試 · Suggestion

**問題**：這次新增的測試（login.2fa.e2e.ts:105-118）只驗證了 backup code 的「展示」——下載按鈕會觸發下載、檔名是 cal-backup-codes.txt、複製按鈕會跳 toast。真正有安全意義的三件事一件都沒測到：(1) 用 backup code 登入會成功；(2) 同一組 code 第二次使用會失敗（一次性）；(3) 用 backup code 關閉 2FA 會成功。作者自己在 :11 留下 `// TODO: add more backup code tests, e.g. login + disabling 2fa with backup`，等於已經知道缺口在哪。

已確認缺口不在別處：全庫 grep backupCodes 只在 packages/lib/test/builder.ts:192 另外出現一次（型別補洞），沒有任何單元測試或整合測試觸及 next-auth-options.ts:130-155 或 totp/disable.ts:47-65。

這條路徑是繞過第二因素的合法入口，也正是 F-001 所在的位置——一個能用 backup code 登入的測試若同時斷言「密碼錯誤時必須失敗」，F-001 在 CI 就會被擋下來。

**證據**：
- `apps/web/playwright/login.2fa.e2e.ts:11`
- `apps/web/playwright/login.2fa.e2e.ts:105-118`
- `packages/features/auth/lib/next-auth-options.ts:130-155`

**修復方向**：在 login.2fa.e2e.ts 既有的 2FA 測試後面接一個 test.step：啟用 2FA 後從 setup 回應（或直接從 DB 解密 users.backupCodes）取得第一組 code，登出，登入時點「lost_access」填入該 code，斷言登入成功；接著用同一組 code 再登入一次，斷言出現 incorrect_backup_code。

另外補一個對 authorize() 的單元測試（packages/features/auth/lib 下已有 test 慣例可循），至少涵蓋：密碼錯誤 + 有效 backupCode → 必須拋 IncorrectEmailPassword。這條斷言同時就是 F-001 的回歸測試。

#### F-004 已知永遠會通過的斷言只被加上 FIXME 而沒有修，同一檔案裡就有正確寫法 — `apps/web/playwright/login.2fa.e2e.ts:45-48`

面向 G 測試 · Suggestion

**問題**：`await expect(page.locator('[data-testid=two-factor-switch]').isChecked()).toBeTruthy()`（:48）—— isChecked() 回傳的是 Promise，而 Promise 物件恆為 truthy，所以這行不論開關實際狀態如何都會通過。作者在 :45-46 加了 `// FIXME: this passes even when switch is not checked, compare to test below which checks for data-state="checked" and works as expected`，也就是診斷已經正確，只是沒有修。

正確寫法就在同一個檔案的 :120：`await expect(page.locator('[data-testid=two-factor-switch][data-state="checked"]')).toBeVisible()`。既然這個 MR 已經動到這個檔案、也已經寫下正確版本，把假斷言留在原地的成本大於修掉它——它守的正是「2FA 有沒有真的啟用」，而這次改的就是啟用流程的最後一步（F-006）。

**證據**：
- `apps/web/playwright/login.2fa.e2e.ts:45-48`
- `apps/web/playwright/login.2fa.e2e.ts:120`

**修復方向**：把 :47-48 兩行換成與 :120 相同的寫法：

```ts
await expect(page.locator(`[data-testid=two-factor-switch][data-state="checked"]`)).toBeVisible();
```

或使用 Playwright 內建的 `await expect(page.locator('[data-testid=two-factor-switch]')).toBeChecked()`。改完後把 :45-46 的 FIXME 一併刪除。

#### F-005 backup code 沒有重新產生的入口，用完之後錯誤訊息還會誤導 — `apps/web/pages/api/auth/two-factor/totp/setup.ts:41-43`

面向 E 架構 · Suggestion

**問題**：backup code 只在 setup.ts 產生，而 setup.ts:41-43 明確擋掉 twoFactorEnabled=true 的使用者。因此一旦 2FA 啟用，就沒有任何路徑可以重新產生 backup code——唯一的辦法是先關閉 2FA（需要 TOTP 或一組 backup code）再重新啟用。

配合消耗邏輯（next-auth-options.ts:146 把用掉的 code 設成 null 後整包重新加密），會出現兩個具體的壞狀況：

1. **剩餘數量不可見。** 使用者不知道自己還剩幾組，UI 也沒有任何提示（two-factor-auth.tsx 只有一個開關）。
2. **用完之後的錯誤訊息是錯的。** 十組全用完後 user.backupCodes 仍是一個非空的密文字串（內容是 `[null,null,...]`），所以 `if (!user.backupCodes)`（next-auth-options.ts:135）永遠不成立，MissingBackupCodes 不會觸發；使用者拿到的是 incorrect_backup_code（「Backup code is incorrect.」），而真正的狀況是「已經用完了」。這會把一個可解釋的狀態變成一個看起來像打錯字的狀態。

已找過反證：全庫 grep backupCodes 沒有其他產生或重置的入口；settings 頁面（two-factor-auth.tsx）也只掛了 Enable / Disable 兩個 modal。

**證據**：
- `apps/web/pages/api/auth/two-factor/totp/setup.ts:41-43`
- `packages/features/auth/lib/next-auth-options.ts:135-155`
- `apps/web/pages/settings/security/two-factor-auth.tsx:62-73`

**修復方向**：兩件事分開處理：

1. 短期先讓錯誤訊息正確——判斷解密後陣列裡是否還有非 null 元素，全空時回 MissingBackupCodes：

```ts
const backupCodes: (string | null)[] = JSON.parse(symmetricDecrypt(...));
if (backupCodes.every((c) => c === null)) throw new Error(ErrorCode.MissingBackupCodes);
```
（totp/disable.ts:57-63 同一處也要改。）

2. 中期補一個 regenerate 入口：在 apps/web/pages/api/auth/two-factor/totp/ 底下新增一支 backup-codes.ts（POST），要求密碼 + 一個有效的第二因素，重新產生十組並覆寫；在 settings/security/two-factor-auth.tsx 加上按鈕與剩餘數量顯示。這同時也是 F-002 改用雜湊時，讓既有使用者換發的必要出口。

#### F-006 onEnable() 被移到最後一顆按鈕後面，用 Esc／點擊外部關閉對話框會讓畫面與伺服器狀態不一致 — `apps/web/components/settings/EnableTwoFactorModal.tsx:134-136`

面向 H 非 Python 檔 · Suggestion

**問題**：改動前 handleEnable 成功後立刻呼叫 onEnable()；改動後改成 `setStep(SetupStep.DisplayBackupCodes)`（:135），onEnable() 只剩下 DisplayBackupCodes 那一步的 Close 按鈕會呼叫（:262）。問題是這個對話框還有別的關閉方式：DialogContent 直接用 Radix 的 DialogPrimitive.Content（packages/ui/components/dialog/Dialog.tsx:86-89），沒有攔截 onEscapeKeyDown 或 onPointerDownOutside，所以按 Esc 或點擊遮罩都會關閉並觸發 onOpenChange。

父層的 onOpenChange 只做 `setEnableModalOpen(!enableModalOpen)`（two-factor-auth.tsx:64），不會呼叫 utils.viewer.me.invalidate()。結果是：伺服器端 2FA 已經啟用，但畫面上的 Switch 仍顯示未啟用。

第二層問題是狀態殘留：resetState()（:70-74）只在 Cancel 與 Close 兩顆按鈕上呼叫，Esc 關閉不會觸發，而元件不會 unmount（Dialog 只是 open=false），因此 step 停在 DisplayBackupCodes。使用者接著再點 Switch（快取仍認為未啟用，所以開的是 Enable modal）會直接看到上一輪的 backup codes，而不是從頭開始。

已找過反證：確認 Dialog.tsx 沒有 `modal={false}` 之外的關閉攔截，也確認 two-factor-auth.tsx 的 onOpenChange 沒有另外呼叫 invalidate。狀態最終可以靠再按一次 Close 收斂，所以不是 Critical，但這是一個使用者一定會踩到的分支。

**證據**：
- `apps/web/components/settings/EnableTwoFactorModal.tsx:134-136`
- `apps/web/components/settings/EnableTwoFactorModal.tsx:256-265`
- `apps/web/components/settings/EnableTwoFactorModal.tsx:70-74`
- `apps/web/pages/settings/security/two-factor-auth.tsx:62-70`

**修復方向**：把「同步狀態」與「關閉對話框」解耦。最小修法是在 handleEnable 成功時就通知父層，讓 Close 只負責關閉：

```ts
if (response.status === 200) {
  setStep(SetupStep.DisplayBackupCodes);
  onEnable();          // 立刻 invalidate，Switch 馬上反映真實狀態
  return;
}
```
並把 :262 的 Close 改成只做 `resetState(); onOpenChange();`。

同時讓 resetState() 涵蓋 backupCodes / backupCodesUrl（見 F-011），並在父層的 onOpenChange 或以 `useEffect(() => { if (!open) resetState(); }, [open])` 確保任何關閉方式都會重置。

#### F-007 backupCode 未經型別驗證就當字串使用，非字串輸入會變成 500 而不是 400 — `apps/web/pages/api/auth/two-factor/totp/disable.ts:47`

面向 D API 慣例 · Suggestion

**問題**：disable.ts:60 直接呼叫 `req.body.backupCode.replaceAll("-", "")`。req.body 是未驗證的 JSON，`{"backupCode": 12345}` 或 `{"backupCode": ["x"]}` 都能通過 :47 的 truthy 檢查，然後在 :60 因為 replaceAll 不存在而拋 TypeError——Next.js API route 沒有 try/catch，使用者拿到的是 500 而不是一個帶 ErrorCode 的 400。next-auth-options.ts:143 是同一個形狀。

這個端點原本的 password / code 欄位也是直接讀 req.body，所以「沒有 schema」本身是既有債；但這次新增的欄位多了一個字串方法呼叫，把「型別不對」從無害變成會炸的，屬於本次引入的行為。

影響有限（需要已登入的 session，只是 500 而非資料外洩），所以不是 Critical；但錯誤碼失真會讓前端的 DisableTwoFactorModal.tsx:60-72 那串 body.error 判斷全部落到 something_went_wrong。

**證據**：
- `apps/web/pages/api/auth/two-factor/totp/disable.ts:47`
- `apps/web/pages/api/auth/two-factor/totp/disable.ts:60`
- `packages/features/auth/lib/next-auth-options.ts:143`

**修復方向**：在兩個入口都先確認型別，最省事的寫法：

```ts
const backupCode = typeof req.body.backupCode === "string" ? req.body.backupCode : "";
if (user.twoFactorEnabled && backupCode) { ... backupCode.replaceAll("-", "") ... }
```

更符合慣例的做法是替這個 handler 補一個 zod schema（`z.object({ password: z.string().optional(), code: z.string().optional(), backupCode: z.string().optional() })`），解析失敗直接回 400。專案內已大量使用 zod，不需要新增相依。

</details>

<details>
<summary>Nit（6）</summary>

#### F-008 BackupCode.tsx 匯出的 function 名字叫 TwoFactor — `apps/web/components/auth/BackupCode.tsx:7`

面向 A 風格 · Nit

**問題**：`export default function TwoFactor({ center = true })` 出現在 BackupCode.tsx 裡，顯然是從 TwoFactor.tsx 複製後忘了改名。因為是 default export，import 端（login.tsx:34、DisableTwoFactorModal.tsx:8）看起來都正常，所以不會有編譯錯誤——但 React DevTools、error boundary 的 component stack 與任何 stack trace 都會顯示成 TwoFactor，之後排查 backup code 的畫面問題時會直接誤導。

**證據**：
- `apps/web/components/auth/BackupCode.tsx:7`

**修復方向**：改成 `export default function BackupCode({ center = true })`。

#### F-009 useState([]) 讓 backupCodes 推導成 never[]，型別失去保護作用 — `apps/web/components/settings/EnableTwoFactorModal.tsx:63`

面向 A 風格 · Nit

**問題**：`const [backupCodes, setBackupCodes] = useState([])` 沒有型別參數，TypeScript 會推導成 never[]。之所以還能編譯，是因為 setBackupCodes(body.backupCodes) 的來源是 `await response.json()`（型別 any），而 map 的 callback 參數型別 never 又可以指派給 formatBackupCode 的 string 參數。整條路徑都成立，但沒有任何一步真的檢查過型別——這正是 tsc 在此環境無法執行（見 scans 的 tsc 項）也不會有人發現的那種洞。

**證據**：
- `apps/web/components/settings/EnableTwoFactorModal.tsx:63`
- `apps/web/components/settings/EnableTwoFactorModal.tsx:95-100`
- `apps/web/components/settings/EnableTwoFactorModal.tsx:196-200`

**修復方向**：明確標註：`const [backupCodes, setBackupCodes] = useState<string[]>([]);`。順帶把 setup 回應也收斂一下，例如 `const body = (await response.json()) as { backupCodes: string[]; dataUri: string; secret: string; error?: string };`

#### F-010 新增的兩個錯誤碼繞過既有的 errorMessages map，並讓渲染變成巢狀三元式 — `apps/web/pages/auth/login.tsx:70-77`

面向 A 風格 · Nit

**問題**：檔案裡本來就有 `errorMessages: { [key: string]: string }`（:70-77）作為 ErrorCode → 文案的單一對照表，最後由 `errorMessages[res.error] || t("something_went_wrong")` 統一查表（:158）。這次新增的 IncorrectBackupCode 與 MissingBackupCodes 卻改用兩行 else if 硬接（:156-157），等於同一個檔案裡出現兩套對照機制，下一個加錯誤碼的人不知道該往哪邊加。

:216 的 `twoFactorRequired ? !twoFactorLostAccess ? <TwoFactor center /> : <BackupCode center /> : null` 是無括號的巢狀三元式，比原本的 `twoFactorRequired && <TwoFactor center />` 難讀一個層級。

**證據**：
- `apps/web/pages/auth/login.tsx:70-77`
- `apps/web/pages/auth/login.tsx:155-157`
- `apps/web/pages/auth/login.tsx:216`

**修復方向**：把兩個新錯誤碼加進 errorMessages，刪掉 :156-157 兩行 else if：

```ts
[ErrorCode.IncorrectBackupCode]: t("incorrect_backup_code"),
[ErrorCode.MissingBackupCodes]: t("missing_backup_codes"),
```

:216 拆成明確的兩層：

```tsx
{twoFactorRequired && (twoFactorLostAccess ? <BackupCode center /> : <TwoFactor center />)}
```

#### F-011 createObjectURL 產生的 blob URL 只在重新產生時回收，元件卸載時會留下 — `apps/web/components/settings/EnableTwoFactorModal.tsx:96-100`

面向 H 非 Python 檔 · Nit

**問題**：`if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl); setBackupCodesUrl(URL.createObjectURL(textBlob));`（:99-100）只處理了「再次 setup 時回收前一個」，最後一個 URL 沒有任何地方回收——resetState()（:70-74）不碰 backupCodesUrl，元件也沒有 cleanup effect。這個 blob 會活到分頁關閉為止。內容還是使用者的十組 backup code 明文，留在記憶體裡沒有必要。

**證據**：
- `apps/web/components/settings/EnableTwoFactorModal.tsx:96-100`
- `apps/web/components/settings/EnableTwoFactorModal.tsx:70-74`

**修復方向**：加一個 cleanup effect，並讓 resetState 一併清掉：

```ts
useEffect(() => () => { if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl); }, [backupCodesUrl]);

const resetState = () => {
  setPassword("");
  setBackupCodes([]);
  if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl);
  setBackupCodesUrl("");
  setErrorMessage(null);
  setStep(SetupStep.ConfirmPassword);
};
```

#### F-012 JSON.parse(symmetricDecrypt(...)) 沒有防禦性檢查，比旁邊的 TOTP 分支寬鬆 — `apps/web/pages/api/auth/two-factor/totp/disable.ts:57`

面向 C 安全 · Nit

**問題**：symmetricDecrypt 在金鑰換過、密文被截斷或欄位被手動改動時會直接拋例外（crypto.ts:30-40 的 createDecipheriv / final），JSON.parse 也會。兩處都沒有包 try/catch，也沒有驗證解析結果是不是陣列。

值得注意的是同一個檔案下方的 TOTP 分支（disable.ts:84-91）就有做這件事——解密後檢查 `secret.length !== 32` 並記錄一筆可辨識的 console.error 才繼續。新分支沒有對應的檢查，等於在同一支 handler 裡採用兩種嚴謹度。金鑰輪替是最可能觸發的情境，屆時使用者只會拿到一個沒有 ErrorCode 的 500，log 裡也沒有線索。

**證據**：
- `apps/web/pages/api/auth/two-factor/totp/disable.ts:57`
- `packages/features/auth/lib/next-auth-options.ts:137-139`
- `apps/web/pages/api/auth/two-factor/totp/disable.ts:84-91`

**修復方向**：包起來並驗證形狀，錯誤時給明確的 ErrorCode：

```ts
let backupCodes: (string | null)[];
try {
  backupCodes = JSON.parse(symmetricDecrypt(user.backupCodes, process.env.CALENDSO_ENCRYPTION_KEY));
  if (!Array.isArray(backupCodes)) throw new Error("not an array");
} catch (e) {
  console.error(`Backup codes for user ${user.id} could not be decrypted`, e);
  return res.status(500).json({ error: ErrorCode.InternalServerError });
}
```
next-auth-options.ts:137-139 同樣處理（該處改成 throw new Error(ErrorCode.InternalServerError)）。

#### F-013 backup code 輸入框的 minLength/maxLength 允許長度合法但格式錯誤的值 — `apps/web/components/auth/BackupCode.tsx:23-32`

面向 H 非 Python 檔 · Nit

**問題**：合法的 backup code 只有兩種形狀：10 個 hex 字元，或帶一個 dash 的 11 字元（XXXXX-XXXXX）。用 minLength={10} / maxLength={11} 表達這件事，會同時放行 11 個沒有 dash 的字元、10 個含 dash 的字元，以及任何非 hex 內容。後端 next-auth-options.ts:143 只做 replaceAll("-", "") 後 indexOf，所以這些值都只會得到一個籠統的「Backup code is incorrect.」，使用者不知道自己是打錯字還是格式錯。

另外 :19 的 `<Label className="mt-4">` 沒有 htmlFor，而 TextField 又傳了 `label=""`（:25），等於這個輸入框沒有被正確關聯的標籤，讀屏軟體只會唸到空字串。

**證據**：
- `apps/web/components/auth/BackupCode.tsx:23-32`

**修復方向**：改用 pattern 表達真正的規則，並把標籤交給 TextField：

```tsx
<TextField
  id="backup-code"
  label={t("backup_code")}
  defaultValue=""
  placeholder="XXXXX-XXXXX"
  pattern="[0-9a-fA-F]{5}-?[0-9a-fA-F]{5}"
  required
  {...methods.register("backupCode")}
/>
```
並移除上方獨立的 `<Label>`（改用 TextField 的 label，說明文字保留在 <p> 即可）。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 apps/web/components/security/ 底下那份 2FA modal 副本是不是已經是死碼？如果不是，它為什麼不需要跟著這次一起改？

面向 E 架構

**背景**：apps/web/components/security/{EnableTwoFactorModal,DisableTwoFactorModal,TwoFactorAuthAPI,TwoFactorAuthSection}.tsx 與這次修改的 apps/web/components/settings/ 那組幾乎是同一份流程的兩個副本。全庫 grep 顯示只有 security/TwoFactorAuthSection.tsx 會 import security 版的兩個 modal，而 TwoFactorAuthSection 本身沒有任何 import 端（唯一的命中就是它自己的定義與 export），因此在這個 checkout 內看起來是死碼。之所以不直接開單，是因為「沒有 import 端」只能證明這個 repo 內沒有靜態引用；若有任何動態載入或 repo 外的引用把它渲染出來，走那條路的使用者會在完全不知情的情況下啟用 2FA 而拿不到任何 backup code——那就是一個真正的功能缺口，而不是死碼。

**如何確認**：由熟悉這塊的維護者確認 components/security/ 是否已被 components/settings/ 取代。若是，這個 MR 可以順手刪除該目錄（或另開清理 MR）；若否，需要把 backup code 的 UI 改動同步過去。

#### Q-002 使用 backup code 登入成功後，要不要主動通知使用者（email）或留下稽核紀錄，並提示重新產生？

面向 C 安全

**背景**：next-auth-options.ts:145-155 在驗證成功後只做一件事：把該組 code 標成 null 並寫回。沒有寄信、沒有寫任何 audit log、也沒有在登入後提示剩餘數量或引導重新產生。backup code 是繞過第二因素的憑證，被盜用時受害者目前沒有任何察覺管道。這屬於產品與安全政策的取捨（通知頻率、是否強制換發），不是能從程式碼判定對錯的事，因此不給 severity。

**如何確認**：團隊對「帳號復原事件是否通知使用者」的既有政策；若已有類似的安全事件通知機制（例如密碼變更通知），比照辦理即可。

</details>
