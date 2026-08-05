## 審查結論：Request Changes

> Critical 7 · Suggestion 6 · Nit 4 · 未驗證提問 2
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

- **A 風格**（未通過）：APP_CREDENTIAL_SHARING_ENABLED 的名稱承諾一個布林值，實際型別是 string | undefined（constants.ts:103-104），這是「名稱與行為不符」（F-010）。parseRefreshTokenResponse.ts:7 的註解宣稱「Assume that any property with a number is the expiry」，但下一行的寫法在 zod 裡根本無法表達這件事，註解主動誤導下一位維護者（F-005）。app-credential.ts:16 留了一個空的 /** */ JSDoc（F-017）。
- **B 簡潔**（未通過）：office365calendar/lib/CalendarService.ts:264 的 `tokenResponse.success &&` 在改動後已成死碼——parseRefreshTokenResponse 失敗時直接 throw，走到這一行 success 必為 true；同時原本印出 zod error 與 MS 原始回應的 console.error 被刪掉，之後這條路徑出問題會沒有任何診斷資訊（F-014）。zoho-bigin/api/add.ts:12 把 `${appConfig.slug}` 改回硬編字串，是與本 MR 目的無關的反向改動，且留下一個沒有插值的 template literal（F-015）。
- **C 安全**（未通過）：webhook 解密後的 keys 未經任何 schema 驗證就寫進 credential.key（F-009）；parseRefreshTokenResponse 會把佔位字串 "refresh_token" 寫成真正的 refresh token，破壞既有憑證（F-004）；credential sync 啟用時 minimumTokenResponseSchema 實際只驗 access_token（F-005）。另外正面確認：webhook secret 比對雖然用一般 `!==`，但這與 repo 既有慣例一致（apps/web/pages/api/cron/bookingReminder.ts:13-14 等處同樣寫法），不另外開 finding。
- **D API 慣例**（未通過）：新增的 webhook endpoint 用 app.slug 去索引以 dirName 為鍵的 appStoreMetadata，對 google-calendar、office365-calendar、msteams 等 app 一律回 404（F-003）。同一個 handler 沒有 HTTP method 檢查，且用 schema.parse 而非 repo 內既有的 safeParse + 400 慣例（apps/web/pages/api/auth/setup.ts:30 等），格式錯誤的請求會變成帶 stack trace 的 500 而不是 400（F-008）。
- **E 架構**（未通過）：refreshOAuthTokens 在兩個分支回傳型別完全不同的東西——sync 分支給原始 fetch Response，fallback 分支給各 provider 自己的形狀——而 9 個呼叫端各自假設不同形狀，等於把一個決策散在 N 個模組（F-007）。parseRefreshTokenResponse 回傳 SafeParseReturnType 而非解析後資料，也讓呼叫端很容易用錯（F-001 就是這樣發生的）。
- **F 資料取用與資料庫**（未通過）：googlecalendar/lib/CalendarService.ts:97 把 safeParse 的外層信封寫進 credential.key，等於改變了既有儲存資料的形狀，而讀端（同檔 75 行的 googleCredentialSchema.parse）沒有跟著改，是典型的 expand/migrate/contract 全部省略（F-001）。salesforce/lib/CalendarService.ts:96 對 credential 做 read-modify-write，沒有交易也沒有並行保護，而 getClient 在每次 service 建構時都會跑（F-011）。
- **G 測試**（未通過）：新增 4 個檔案、修改 9 個 token refresh 路徑，整個 MR 沒有任何測試。以 grep 確認 repo 內沒有任何 *.test.ts / *.spec.ts / *.e2e.ts 提到 app-credential、refreshOAuthTokens、parseRefreshTokenResponse 或 APP_CREDENTIAL_SHARING（F-013）。本次找到的 F-001（存錯形狀）與 F-006（傳錯 id）都是一個最小的 unit test 就會擋下來的錯誤。
- **H 非 Python 檔**（未通過）：diff 全部為非 Python 檔（37 個 .ts、turbo.json、.env.example）。本維度點名的 Vue / Dockerfile / nginx.conf / docker-compose / Alembic migration 這次都不適用，UI component 也沒有變動；TypeScript 檔案的實質問題已歸入 A–G。此處僅掛一則 Nit：turbo.json:205-207 把 CALCOM_WEBHOOK_SECRET 插在 CALENDSO_ENCRYPTION_KEY 之後，破壞了該清單的字母序（F-016）。判定為 fail 是因為確實掛著一則發現，但請注意它只是 Nit 等級。
- **I 回溯分析**（未通過）：refreshOAuthTokens 的第三個參數叫 userId 並被送成 calcomUserId，但 zoho-bigin/lib/CalendarService.ts:93 傳進去的是 credential.id——參數名對得上、值對不上，正是「跟著值走而不是跟著參數名走」要抓的東西（F-006）。另外正面確認：_utils/{encode,decode}OAuthState.ts 與 createOAuthAppCredential.ts 搬到 _utils/oauth/ 之後，以 grep 全 repo 搜尋舊路徑已無任何殘留 import，25 個呼叫端全部改乾淨了，這一項沒有問題。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 同時做了兩件事：(1) 把 _utils/{encode,decode}OAuthState.ts、createOAuthAppCredential.ts 搬進 _utils/oauth/，牽動 25 個檔案的純 import 調整；(2) 新增 credential sync 功能，實際邏輯集中在 4 個新檔案與 9 個 CalendarService/VideoApiAdapter。搬移本身是乾淨的（已確認沒有殘留舊路徑 import），但它把 diff 撐大到 40 個檔案，讓真正需要細看的那幾處（例如 googlecalendar/lib/CalendarService.ts:97 的一行）淹沒在 import 噪音裡——本次審查找到的 Critical 有數個就落在這種容易被讀過去的位置。建議把目錄搬移獨立成一個純 refactor MR 先合入。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | preflight 回報未安裝（工具可用 3/8）。相依套件漏洞與 secret 掃描本次完全沒有覆蓋，本報告對 yarn.lock 相依風險與硬編憑證不提供任何保證。 |
| opengrep | 略過 | preflight 回報未安裝，且 NCR_OPENGREP_RULES 指定的規則目錄（預設在 home 底下的 semgrep-rules）也不存在。SAST 完全沒有覆蓋，本報告中所有安全類發現都來自人工閱讀，不是工具產出。 |
| ruff | 已執行 | ruff 0.15.8 有安裝且成功執行（`ruff check .`），但輸出為「warning: No Python files found under the given path(s)」——這是一個純 TypeScript monorepo，diff 內 40 個檔案全是 .ts/.json/.example，ruff 沒有任何檔案可檢查。exit code 0 在這裡代表「無事可做」，不代表「檢查通過」。 · findings 0 |
| ty | 略過 | preflight 回報未安裝；且本 diff 無 Python 檔案，即使安裝也不適用。 |
| oxlint | 略過 | preflight 回報未安裝。這是本次掃描最大的缺口——diff 全部是 TypeScript，oxlint 是唯一能對這些檔案做確定性檢查的工具。TypeScript 型別檢查同樣沒有執行：這個 checkout 沒有 node_modules，也沒有對外網路，`tsc --noEmit` 無法執行。本報告中所有 TypeScript 相關的發現（F-002、F-005、F-007）都是純人工推導，未經編譯器驗證。 |
| codegraph | 略過 | preflight 回報未安裝。Phase 3 的呼叫路徑列舉與 caller 完整性確認全部改用 grep 完成（例如以 grep 確認 globalThis.prisma 全 repo 只有一處賦值、確認舊 _utils 路徑已無殘留 import）。grep 能證明「不存在」，但無法像 codegraph 那樣列出間接呼叫者，深層呼叫鏈可能有遺漏。 |
| gitlab-api | 略過 | 未設定 GITLAB_TOKEN，且本次為 local_branch 模式、沒有對應的 merge request。MR 標題、描述、commit 訊息與既有討論串都無法取得，意圖判定僅根據程式碼本身推得。 |
| ncr-fresh-eyes | 略過 | 執行環境沒有任何可派送 subagent 的工具（工具清單中不存在 Task/Agent，ToolSearch 查詢 select:Task,Agent,SpawnAgent 回傳無結果）。依 SKILL.md Phase 3 的規定，不得由主 agent 自行模擬 fresh eyes，故略過並在此揭露。這代表本報告缺少一次「未被 checklist 框住」的獨立閱讀，checklist 沒有點名的問題型態可能被讀過去。 |
| ncr-quality-check | 略過 | 同上，環境無法派送 subagent。報告 JSON 只通過 report_model.py 的機械驗證（結論一致性、每則 finding 有 fix、Critical security 有 POC/blast radius/treatment、九維度皆有判定），沒有經過獨立的品質複核。 |

### Critical

#### F-001 Google Calendar token refresh 把 safeParse 的外層信封當成 credential.key 寫進資料庫 — `packages/app-store/googlecalendar/lib/CalendarService.ts:97`

面向 F 資料取用與資料庫 · Critical

**問題**：改動前這一行是 `const key = googleCredentialSchema.parse(googleCredentials)`，parse() 回傳的是解析後的物件。改動後換成 `const key = parseRefreshTokenResponse(googleCredentials, googleCredentialSchema)`，而 parseRefreshTokenResponse 最後一行回傳的是 `refreshTokenResponse`，也就是 safeParse() 的結果（parseRefreshTokenResponse.ts:29），形狀是 `{ success: true, data: {...} }`。這個信封被原封不動寫進 `prisma.credential.update({ data: { key } })`。

下一次同一位使用者的 Google Calendar 被使用時，同檔 75 行的 `googleCredentialSchema.parse(credential.key)` 會拿到 `{success, data}` 而不是 `{scope, token_type, expiry_date, access_token, refresh_token}`，直接丟 ZodError。使用者的 Google Calendar 從此壞掉，必須重新授權。

反證檢查：其他四個呼叫端都正確處理了回傳值——zoomvideo/lib/VideoApiAdapter.ts 用 `parsedToken.success` / `parsedToken.data`，office365calendar/lib/CalendarService.ts:264 用 `tokenResponse.success && tokenResponse.data`，salesforce/lib/CalendarService.ts:90-93 用 `accessTokenParsed.success` / `.data`。googlecalendar 是唯一一個直接使用回傳值的，所以這不是「parseRefreshTokenResponse 設計成回傳資料」，而是這一個呼叫端用錯了。另外確認這條路徑與 credential sharing 是否啟用無關：parseRefreshTokenResponse 的兩個分支都 return safeParse 結果，所以所有 Google Calendar 使用者在第一次 token refresh 之後就會中招。

**證據**：
- `packages/app-store/googlecalendar/lib/CalendarService.ts:97`
- `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:29`
- `packages/app-store/googlecalendar/lib/CalendarService.ts:75`
- `packages/app-store/googlecalendar/lib/googleCredentialSchema.ts:3-9`

**修復方向**：取出 `.data` 再寫入，並保留驗證失敗的處理：

```ts
const parsed = parseRefreshTokenResponse(googleCredentials, googleCredentialSchema);
await prisma.credential.update({
  where: { id: credential.id },
  data: { key: parsed.data },
});
```

更根本的修法是讓 parseRefreshTokenResponse 直接回傳解析後的資料（它反正在失敗時已經 throw 了），這樣就不會有呼叫端拿錯層的空間——見 F-007 的建議。

#### F-002 Salesforce CalendarService 使用了未 import 的 prisma，production 會在每次建構 service 時炸掉 — `packages/app-store/salesforce/lib/CalendarService.ts:96`

面向 E 架構 · Critical

**問題**：新增的 `await prisma.credential.update(...)`（96 行）用到了 `prisma`，但這個檔案的 import 區塊（1-20 行）從頭到尾沒有 `import prisma from "@calcom/prisma"`。以 grep 確認整個檔案中 `prisma` 只出現這一次，就是這個使用點。

反證檢查（這一步讓結論變得更嚴重，不是更輕）：packages/prisma/index.ts:6-9 有一個 `declare global { var prisma: ... }`，所以 TypeScript 型別層面上這個裸 `prisma` 是找得到宣告的，`tsc` 很可能不會報錯——這也解釋了 head commit 訊息「Type fix」為什麼沒有攔下它。但執行期的賦值在 packages/prisma/index.ts:58-60：

```ts
if (process.env.NODE_ENV !== "production") {
  globalThis.prisma = prisma;
}
```

以 grep 確認全 repo 只有這一處對 globalThis.prisma / global.prisma 賦值。也就是說：開發與測試環境因為這個 global 被設起來而「剛好會動」，production 因為條件不成立，`globalThis.prisma` 是 undefined，執行到 96 行會丟 TypeError（讀取 undefined 的 credential）。

這行位在 `getClient()` 裡，而 `getClient()` 由 constructor 無條件呼叫（57 行 `this.conn = this.getClient(credential).then(...)`），所以是「每一次建立 SalesforceCalendarService 都會走到」的路徑，不是邊角情況。而且它是在 constructor 裡起的 promise，rejection 也沒有被接住。這正好是「一個環境同意不等於驗證過」的典型案例。

**證據**：
- `packages/app-store/salesforce/lib/CalendarService.ts:96`
- `packages/app-store/salesforce/lib/CalendarService.ts:1-20`
- `packages/prisma/index.ts:6-9`
- `packages/prisma/index.ts:58-60`

**修復方向**：補上 import：

```ts
import prisma from "@calcom/prisma";
```

（可對照同 PR 中 packages/app-store/zohocrm/lib/CalendarService.ts:7 與 googlecalendar/lib/CalendarService.ts:11 的寫法。）另外建議一併處理 constructor 裡未接住的 promise rejection，見 F-011。

#### F-003 credential sync webhook 用 app.slug 索引以 dirName 為鍵的 appStoreMetadata，Google Calendar 等 app 一律 404 — `apps/web/pages/api/webhook/app-credential.ts:50`

面向 D API 慣例 · Critical

**問題**：webhook 先用 `prisma.app.findUnique({ where: { slug: reqBody.appSlug } })` 從 DB 取到 app，再用 `appStoreMetadata[app.slug as keyof typeof appStoreMetadata]` 去拿 metadata。問題是 appStoreMetadata 的鍵是 **dirName**，不是 slug。

證據：apps.metadata.generated.ts:98 的鍵是 `googlecalendar`，而 googlecalendar/_metadata.ts 裡 `slug: "google-calendar"`、`dirName: "googlecalendar"`。同樣的落差存在於 office365calendar（slug `office365-calendar`）、office365video（slug `msteams`）等一批舊 app。packages/prisma/seed-app-store.ts:171-178 的註解也明講 slug 與 dirName 是兩個獨立欄位、只有透過 App-Store CLI 建立的新 app 兩者才相同。

所以對 google-calendar 而言 `appStoreMetadata["google-calendar"]` 是 undefined，handler 走到 53 行回 404「App not found. Ensure that you have the correct app slug」——而使用者送的 slug 其實是對的（DB 查詢已經成功了），錯誤訊息還會把人引導到錯誤的方向。

反證檢查：這不是「metadata 檔案漏了設定」。同 repo 的 _appRegistry.ts:17 就是用 `appStoreMetadata[app.dirName]` 索引的，而 packages/app-store/utils.ts:123 已經有現成的 `getAppFromSlug(slug)` 專門處理 slug 查找。這個 webhook 是唯一用 slug 去索引 appStoreMetadata 的地方。

這一點特別要緊，因為 refreshOAuthTokens 在 googlecalendar/lib/CalendarService.ts:91 就是用 `"google-calendar"` 這個 slug 呼叫的——整個 credential sync 迴路對 Google Calendar 從一開始就接不起來。

**證據**：
- `apps/web/pages/api/webhook/app-credential.ts:50`
- `packages/app-store/apps.metadata.generated.ts:98`
- `packages/app-store/googlecalendar/_metadata.ts:18-20`
- `packages/app-store/utils.ts:123-125`
- `packages/app-store/_appRegistry.ts:17`

**POC**：

````
在啟用 credential sync 的部署上執行（CAL_URL 換成自己的站台）：

```bash
curl -X POST "$CAL_URL/api/webhook/app-credential" \
  -H "calcom-webhook-secret: $CALCOM_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"userId": 1, "appSlug": "google-calendar", "keys": "<symmetricEncrypt 後的 keys>"}'
```

預期：200 與 credential 建立/更新。實際：404 `{"message":"App not found. Ensure that you have the correct app slug"}`。把 appSlug 換成 `zoho-bigin`（slug 與 dirName 相同）則會成功，可據此確認落差來自鍵的選擇而非 payload。
````

**影響範圍**：不是資料外洩，是功能性失效加上誤導性錯誤訊息：self-hoster 無法為 Google Calendar、Office 365 Calendar、MS Teams 等主要 app 同步憑證，而 404 訊息會讓他們一直去檢查自己送的 slug（那是對的），排查方向被帶偏。PHI 不在範圍，沒有 PHI 成本。

**風險處置**：Mitigate（降低）

**修復參考**：改用 packages/app-store/utils.ts:123 的 getAppFromSlug()，或改以 dirName 索引。

**修復方向**：改用既有的 helper，不要自行索引：

```ts
import { getAppFromSlug } from "@calcom/app-store/utils";

const appMetadata = getAppFromSlug(app.slug);
```

或改以 dirName 索引（`prisma.app.findUnique` 的 select 加上 `dirName: true`，再用 `appStoreMetadata[app.dirName]`）。無論選哪一種，都應該補一個測試涵蓋 slug ≠ dirName 的 app（google-calendar 是最好的例子）。

#### F-004 parseRefreshTokenResponse 會把字串 "refresh_token" 當成真正的 refresh token 寫回憑證 — `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:25-27`

面向 C 安全 · Critical

**問題**：parseRefreshTokenResponse 在回傳前有這一段（25-27 行）：

```ts
if (!refreshTokenResponse.data.refresh_token) {
  refreshTokenResponse.data.refresh_token = "refresh_token";
}
```

它把字面字串 `"refresh_token"` 填進 refresh_token 欄位。這個值不是任何 provider 認得的 token，它唯一的效果是讓後續的 schema 檢查通過。

這條路徑實際會走到，而且證據就在這個 diff 裡：office365calendar/lib/CalendarService.ts:51-57 的 `refreshTokenResponseSchema` 明確寫 `refresh_token: z.string().optional()`，也就是作者自己承認 MS 的回應可能不帶 refresh_token。一旦不帶，這裡就會填入佔位字串，接著 264 行 `o365AuthCredentials = { ...o365AuthCredentials, ...(tokenResponse.success && tokenResponse.data) }` 把它展開，蓋掉原本存著的真 refresh token，然後 265-272 行寫回資料庫。原本可用的憑證被永久破壞，使用者必須重新授權，而且過程沒有任何錯誤訊息。

反證檢查：這段有沒有可能只在某個安全的分支跑？沒有——這個 if 位在 parseRefreshTokenResponse 的主體，兩個 schema 分支匯流之後，與 APP_CREDENTIAL_SHARING_ENABLED 無關，所有五個呼叫端都會經過。啟用 credential sync 時情況更糟：minimumTokenResponseSchema 會把 refresh_token 整個 strip 掉（見 F-005），於是這個佔位字串變成必然而非偶然。

**證據**：
- `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:25-27`
- `packages/app-store/office365calendar/lib/CalendarService.ts:56`
- `packages/app-store/office365calendar/lib/CalendarService.ts:264-272`

**POC**：

````
在一個已連接 Office 365 Calendar 的帳號上，讓 token 過期並觸發 refresh，同時讓 MS 的回應不含 refresh_token（可由 schema 的 `.optional()` 得知這是預期內的情況）。之後查資料庫：

```sql
SELECT key->>'refresh_token' FROM "Credential" WHERE "userId" = <id> AND "appId" = 'office365-calendar';
```

預期看到原本的 refresh token，實際會看到字串 `refresh_token`。此後任何 refresh 都會被 Microsoft 拒絕。
````

**影響範圍**：受影響使用者的 Office 365 Calendar（以及其他 refresh_token 為 optional 的 provider）憑證被靜默破壞，必須重新走一次 OAuth 授權；期間該使用者的行事曆可用性檢查與訂位建立都會失敗。啟用 credential sync 後範圍擴大到所有 provider（見 F-005）。這是可用性與資料完整性的損害，不是憑證外洩。PHI 不在範圍。

**風險處置**：Mitigate（降低）

**修復參考**：移除 parseRefreshTokenResponse.ts:21-23，改由呼叫端以 `?? 舊值` 明確沿用，寫法參考 salesforce/lib/CalendarService.ts:98。

**修復方向**：拿掉這段補值。缺少 refresh_token 是一個要由呼叫端決定如何處理的狀態（多數 OAuth provider 在 refresh 時本來就不重發 refresh token，正確做法是沿用舊的），不是可以用假值填平的東西。若目的是「provider 沒回傳就沿用舊的」，應該由呼叫端明確地做，例如 office365 那邊：

```ts
const parsed = parseRefreshTokenResponse(responseJson, refreshTokenResponseSchema);
o365AuthCredentials = {
  ...o365AuthCredentials,
  ...parsed.data,
  refresh_token: parsed.data.refresh_token ?? o365AuthCredentials.refresh_token,
};
```

（salesforce/lib/CalendarService.ts:99 已經是這個寫法，可以直接對照。）

#### F-005 minimumTokenResponseSchema 的兩個 computed key 都算成 "[object Object]"，實際只驗 access_token 並丟掉其餘欄位 — `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:5-11`

面向 E 架構 · Critical

**問題**：這個 schema 的寫法是：

```ts
const minimumTokenResponseSchema = z.object({
  access_token: z.string(),
  //   Assume that any property with a number is the expiry
  [z.string().toString()]: z.number(),
  //   Allow other properties in the token response
  [z.string().optional().toString()]: z.unknown().optional(),
});
```

物件字面量的 computed key 會把運算結果轉成字串。`z.string()` 回傳的是一個 ZodString 實例，zod v3（apps/web/package.json:132 為 `^3.22.2`）的 ZodType 沒有覆寫 toString，所以走 Object.prototype.toString，結果是字串 `"[object Object]"`。已用 node 實測 `({[{}.toString()]:1})` 得到 `{"[object Object]":1}`。兩個 computed key 算出同一個字串，第二個直接覆蓋第一個。

實際得到的 schema 是 `{ access_token: string, "[object Object]": unknown | undefined }`——註解宣稱的「任何 number 型別的屬性就是 expiry」在 zod 裡根本不是這樣表達的（要的是 catchall 或 passthrough + 明確欄位），這個寫法無論 toString 回傳什麼都不可能成立，因為 computed key 永遠是一個固定字串而不是一個 pattern。

後果有兩層。第一，驗證形同虛設：credential sync 啟用時，token 回應只有 access_token 被檢查。第二，也是更嚴重的——z.object 預設會 **strip 未宣告的鍵**，所以 safeParse 之後的 `data` 只剩 `{ access_token }`，expiry、refresh_token、scope 全部消失。expiry 消失代表憑證永遠被判定為已過期（例如 office365calendar/lib/CalendarService.ts:239-241 的 isExpired 對 falsy expiryDate 直接回 true），每一次呼叫都重新 refresh；refresh_token 消失則必然觸發 F-004 的佔位字串覆寫。

反證檢查：註解說「Allow other properties in the token response」——有沒有可能 zod 預設就是保留未知鍵？沒有，z.object 的預設策略是 strip，要保留必須明確用 `.passthrough()`。這個 schema 沒有。

**證據**：
- `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:5-11`
- `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:13-19`

**修復方向**：用 zod 真正表達「access_token 必填、其餘欄位放行」的方式，例如：

```ts
const minimumTokenResponseSchema = z
  .object({ access_token: z.string() })
  .passthrough();
```

如果確實需要「找出那個代表 expiry 的數字欄位」，那是 schema 之外的邏輯，應該由各 provider 自己的 schema 宣告欄位名（各家叫法不同：expires_in、expiry_date、expiresIn），不要試圖用一個泛用 schema 去猜。無論採哪種，這裡都需要一個 unit test 斷言解析後 expiry 與 refresh_token 有被保留下來。

#### F-006 zoho-bigin 把 credential.id 當成 userId 傳給 refreshOAuthTokens — `packages/app-store/zoho-bigin/lib/CalendarService.ts:93`

面向 I 回溯分析 · Critical

**問題**：refreshOAuthTokens 的簽章是 `(refreshFunction, appSlug: string, userId: number | null)`，而 userId 的用途是明確的：refreshOAuthTokens.ts:11 把它送成 `calcomUserId: userId.toString()`，也就是 self-hoster 的 sync endpoint 用來決定「要回傳哪一位使用者的 token」的身分鍵。

zoho-bigin 這邊傳進去的是 `credentialId`，而 CalendarService.ts:53 明確寫著 `const credentialId = credential.id;`——是 Credential 資料表的主鍵，不是 User 的 id。其他八個呼叫端傳的都是 `credential.userId`（googlecalendar:92、hubspot:188、larkcalendar:81、office365calendar:260、office365video:74、webex:77、zohocrm:215、zoomvideo:93）。

型別上兩者都是 number，所以編譯器不會有意見；參數名對得上、值對不上，這正是要跟著值走而不是跟著參數名走的地方。

反證檢查：有沒有可能 zoho-bigin 的路徑刻意要用 credential id？沒有依據——refreshOAuthTokens 內部沒有任何以 appSlug 區分語意的分支，欄位名 `calcomUserId` 也只有一種解讀。而且同一個檔案在 102-107 行用 credentialId 去做 `prisma.credential.update({ where: { id: credentialId } })`，用途完全不同，可見這是取值時拿錯了變數。

後果分兩種，取決於 self-hoster 的 endpoint：查不到對應 user 時整個 zoho-bigin 的 credential sync 直接失效；若該 endpoint 只是照著這個整數查使用者，那麼因為 credential id 與 user id 來自不同資料表、數值範圍必然重疊，它會拿到**另一位使用者**的 token 並寫進這位使用者的 credential。後者是跨使用者的憑證混用。前者是確定會發生的，後者取決於對方實作，所以嚴重度按前者定，後者在下面的 blast radius 說明。

**證據**：
- `packages/app-store/zoho-bigin/lib/CalendarService.ts:93`
- `packages/app-store/zoho-bigin/lib/CalendarService.ts:53`
- `packages/app-store/_utils/oauth/refreshOAuthTokens.ts:3`
- `packages/app-store/_utils/oauth/refreshOAuthTokens.ts:11`

**POC**：

````
在啟用 credential sync 的部署上，讓一個已連接 Zoho Bigin 的帳號觸發 token refresh（把 credential.key 的 expiryDate 設成過去時間即可），然後觀察送到 CALCOM_CREDENTIAL_SYNC_ENDPOINT 的請求 body：

```
calcomUserId=<credential.id>&appSlug=zoho-bigin
```

把它與 `SELECT id, "userId" FROM "Credential" WHERE id = <credential.id>` 的結果比對，即可看到送出的是 credential id 而非 userId。對照同一部署上 zohocrm 的請求（走 credential.userId）可確認差異。
````

**影響範圍**：確定發生的部分：zoho-bigin 的 credential sync 對所有使用者失效。條件性但更嚴重的部分：若 self-hoster 的 sync endpoint 直接以收到的整數查使用者，由於 Credential.id 與 User.id 是兩個獨立遞增序列、數值範圍必然重疊，Cal.com 會拿到另一位使用者的 Zoho Bigin access token 並寫入這位使用者的 credential，之後以錯誤身分讀寫該使用者的 CRM 資料。這是跨租戶的憑證與資料混用。PHI 不在範圍。

**風險處置**：Mitigate（降低）

**修復參考**：zoho-bigin/lib/CalendarService.ts:93 改傳 credential.userId，並把 userId 一路傳進 refreshAccessToken。

**修復方向**：改傳 `credential.userId`：

```ts
// biginAuth 內已有 credential，直接沿用
const tokenInfo = await refreshOAuthTokens(
  async () => await axios.post(accountsUrl, qs.stringify(formData), { ... }),
  "zoho-bigin",
  credential.userId
);
```

這需要把 userId 一併傳進 `refreshAccessToken(credentialId, credentialKey)`（目前只收 credentialId），或改成整個 credential 傳進去。順帶建議把 refreshOAuthTokens 的第三個參數改名成更難傳錯的形式（例如收一個 `{ userId }` 物件），讓下一次傳錯時型別檢查能擋下來。

#### F-007 refreshOAuthTokens 兩個分支回傳的形狀不同，啟用 credential sync 後 google / zoho-bigin / zohocrm 會丟 TypeError — `packages/app-store/_utils/oauth/refreshOAuthTokens.ts:5-19`

面向 E 架構 · Critical

**問題**：refreshOAuthTokens 的 sync 分支回傳的是 `fetch()` 的原始 `Response` 物件（沒有 `.json()`、沒有解析），fallback 分支回傳的則是 `refreshFunction()` 的結果，而那是各 provider 各自的形狀。呼叫端因此分成兩類：

- 拿 Response 當 Response 用的（office365calendar 走 handleErrorsJson、office365video 走 handleErrorsJson、webex 走 handleWebexResponse、zoomvideo 走 handleZoomResponse、larkcalendar 走 handleLarkError）——這些在 sync 分支下可以運作。
- 期待 provider 形狀的：googlecalendar/lib/CalendarService.ts:94 讀 `res?.data`（googleapis 的 GaxiosResponse 形狀）、zoho-bigin:96 與 zohocrm:217 讀 `tokenInfo.data.error` / `zohoCrmTokenInfo.data.error`（axios 形狀）。`Response` 沒有 `.data` 屬性，所以 sync 啟用時：google 這邊 `token` 是 undefined，下一行 `token.access_token` 丟 TypeError；zoho 兩家 `tokenInfo.data` 是 undefined，`.error` 同樣丟 TypeError。

因為 refreshFunction 宣告成 `() => any`、回傳值也沒有型別標註，TypeScript 完全不會提示這個落差。

反證檢查：有沒有可能這些 provider 不會走到 sync 分支？不會——分支條件是 `APP_CREDENTIAL_SHARING_ENABLED && CALCOM_CREDENTIAL_SYNC_ENDPOINT && userId`，與 appSlug 無關；三個呼叫端都有傳 userId（zoho-bigin 傳的值是錯的，見 F-006，但仍是 truthy）。也就是說只要 self-hoster 打開這個功能，這三個 app 的 token refresh 就一定壞掉。

這一則歸在架構維度而不是單一 bug，是因為根因是介面設計：一個函式在兩條路徑上回傳語意不同的東西，卻沒有任何型別把差異釘住，於是每個呼叫端各自賭一種形狀。

**證據**：
- `packages/app-store/_utils/oauth/refreshOAuthTokens.ts:5-19`
- `packages/app-store/googlecalendar/lib/CalendarService.ts:86-94`
- `packages/app-store/zoho-bigin/lib/CalendarService.ts:96`
- `packages/app-store/zohocrm/lib/CalendarService.ts:217`

**修復方向**：讓 refreshOAuthTokens 在兩個分支回傳同一種型別。最小改法是 sync 分支也交回一個 `Response`，並讓所有呼叫端統一經過 `Response`：

```ts
const refreshOAuthTokens = async <T>(
  refreshFunction: () => Promise<Response>,
  appSlug: string,
  userId: number | null
): Promise<Response> => { ... }
```

然後把 googlecalendar / zoho-bigin / zohocrm 三處改成先把 provider 回應正規化成 Response（或反過來，讓 sync 分支 `await response.json()` 之後包成各家期待的形狀）。無論選哪一邊，關鍵是把 `() => any` 換成具體型別，讓下一個接上這個 helper 的 app 在編譯期就知道要拿到什麼。

<details>
<summary>Suggestion（6）</summary>

#### F-008 webhook 缺 HTTP method 檢查，且用 schema.parse 與未保護的解密／JSON.parse，壞請求會變成帶 stack trace 的 500 — `apps/web/pages/api/webhook/app-credential.ts:17`

面向 D API 慣例 · Suggestion

**問題**：handler 沒有檢查 `req.method`，任何動詞都會走進來；GET 沒有 body，31 行的 `appCredentialWebhookRequestBodySchema.parse(req.body)` 會直接丟 ZodError。同樣地 57-59 行的 `symmetricDecrypt(...)` 與外層的 `JSON.parse(...)` 對於被竄改或格式錯誤的 ciphertext 都會 throw。整個 handler 沒有 try/catch，這些例外會冒到 Next.js，變成 500 加 stack trace，而不是 400 / 405。

repo 內既有的慣例是 safeParse 後自行回 400（apps/web/pages/api/auth/setup.ts:30、forgot-password.ts:14、recorded-daily-video.ts:69），以及在多個 app-store handler 用 `if (req.method !== "GET") return res.status(405)`。這個新 endpoint 兩者都沒跟上。

嚴重度定在 Suggestion 而不是 Critical：呼叫者必須先通過 webhook secret 檢查（20-25 行）才會走到這些行，所以未經授權的人打不到；影響限於錯誤語意與 log 噪音，不是可被利用的洞。

**證據**：
- `apps/web/pages/api/webhook/app-credential.ts:17`
- `apps/web/pages/api/webhook/app-credential.ts:31`
- `apps/web/pages/api/webhook/app-credential.ts:57-59`
- `apps/web/pages/api/auth/setup.ts:30`

**修復方向**：補上 method 檢查並改用 safeParse，把解密與 JSON.parse 包進錯誤處理：

```ts
if (req.method !== "POST") return res.status(405).json({ message: "Method not allowed" });

const parsed = appCredentialWebhookRequestBodySchema.safeParse(req.body);
if (!parsed.success) return res.status(400).json({ message: parsed.error.message });

let keys;
try {
  keys = JSON.parse(symmetricDecrypt(parsed.data.keys, process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY || ""));
} catch {
  return res.status(400).json({ message: "Could not decrypt keys" });
}
```

或直接改用 repo 既有的 `defaultHandler` / `defaultResponder`（@calcom/lib/server），apps/web/pages/api 內已有 8 處在用。

#### F-009 解密後的 keys 未經任何 schema 驗證就寫進 credential.key — `apps/web/pages/api/webhook/app-credential.ts:57-59`

面向 C 安全 · Suggestion

**問題**：`keys` 是 `JSON.parse(symmetricDecrypt(...))` 的結果，型別是 any，內容完全沒有驗證就寫進 `prisma.credential.update({ data: { key: keys } })`（73-80 行）與 `prisma.credential.create({ data: { key: keys, ... } })`（83-89 行）。

請求體的 zod schema（9-15 行）只驗到 `keys: z.string()`，也就是「這是一個字串」，解密之後的結構沒有人看。之後讀取這個 credential 的是各 app 自己的 schema（例如 googleCredentialSchema），錯誤會延遲到那個時候才爆，而且爆在離現場很遠的地方。

這裡不算 Critical，是因為呼叫者已經通過 webhook secret 且持有加密金鑰——能走到這一行的已經是被信任的一方，所以這是資料完整性問題而非權限問題。但「缺少驗證 schema」在這個團隊的 API 慣例裡是明確要處理的項目，而且憑證是敏感資料，寫入前的形狀檢查值得補。

**證據**：
- `apps/web/pages/api/webhook/app-credential.ts:57-59`
- `apps/web/pages/api/webhook/app-credential.ts:78`
- `apps/web/pages/api/webhook/app-credential.ts:85`

**修復方向**：在寫入前用該 app 已有的 key schema 驗證。app-store 有 `getParsedAppKeysFromSlug` 之類的既有工具可參考；最直接的做法是查出 app 的 keys schema 後：

```ts
const keySchema = appKeysSchemas[app.slug];
const parsedKeys = keySchema.safeParse(JSON.parse(decrypted));
if (!parsedKeys.success) {
  return res.status(400).json({ message: "Decrypted keys do not match the app's key schema" });
}
```

若目前沒有統一的 per-app key schema 註冊表，至少先驗到「是一個物件且含有 access_token」這一層，並在註解裡說明為什麼只驗到這個程度。

#### F-010 APP_CREDENTIAL_SHARING_ENABLED 名稱是布林語意，實際值是加密金鑰的字串 — `packages/lib/constants.ts:103-104`

面向 A 風格 · Suggestion

**問題**：```ts
export const APP_CREDENTIAL_SHARING_ENABLED =
  process.env.CALCOM_WEBHOOK_SECRET && process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY;
```

`&&` 回傳的是最後一個運算元，所以這個常數的型別是 `string | undefined`，值在啟用時就是 `CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY` 的內容本身。名稱以 `_ENABLED` 結尾，讀的人會當成布林。

目前三個使用點都只做 truthiness 判斷，所以行為正確；問題在於下一個人。任何把它印進 log、放進錯誤訊息、或序列化進 API 回應的動作，都會直接把加密金鑰的明文帶出去，而從名稱完全看不出這件事。這也是 constants.ts 這個檔案特別值得小心的地方——它被大量模組 import。

（另外確認過：`CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY` 沒有 NEXT_PUBLIC_ 前綴，Next.js 在 client bundle 會替換成 undefined，所以不會經由前端外洩。這一點沒有問題。）

**證據**：
- `packages/lib/constants.ts:103-104`
- `apps/web/pages/api/webhook/app-credential.ts:19`
- `packages/app-store/_utils/oauth/refreshOAuthTokens.ts:5`

**修復方向**：強制轉成布林：

```ts
export const APP_CREDENTIAL_SHARING_ENABLED = !!(
  process.env.CALCOM_WEBHOOK_SECRET && process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY
);
```

#### F-011 Salesforce 每次建構 service 都無條件重新 refresh token，且之後仍用舊的 access_token 建連線 — `packages/app-store/salesforce/lib/CalendarService.ts:57`

面向 F 資料取用與資料庫 · Suggestion

**問題**：新增的 refresh 區塊放在 `getClient()` 裡，而 `getClient()` 由 constructor 無條件呼叫（56-57 行）。這裡沒有任何過期判斷——對照同 PR 的其他 app 都有 isExpired / isTokenValid 的守門（office365calendar:235-241、zoho-bigin:55-56、zoomvideo:63）——所以每一次建立 SalesforceCalendarService（每次查可用時段、每次建立/更新/刪除行程）都會多打一次 Salesforce 的 token endpoint 並多寫一次資料庫。Salesforce 對 token 請求有頻率限制，這在流量上來時會變成問題。

第二個問題是這次 refresh 對當下這個請求毫無作用：96-99 行把新 token 寫進資料庫，但 101-108 行建立 jsforce.Connection 時用的仍是 `credentialKey.access_token`，也就是**舊的** access token（`credentialKey` 在 73 行就從 credential.key 取出，之後沒有被更新）。

第三，這是一個 read-modify-write：讀 credential.key、向外部換 token、寫回 credential，中間沒有交易也沒有並行保護。同一位使用者的兩個並行請求會各自 refresh 並互相覆蓋。

（constructor 裡 `this.getClient(credential).then(c => c)` 產生的 promise 也沒有 catch，一旦 refresh 失敗會是 unhandled rejection。）

**證據**：
- `packages/app-store/salesforce/lib/CalendarService.ts:57`
- `packages/app-store/salesforce/lib/CalendarService.ts:75-84`
- `packages/app-store/salesforce/lib/CalendarService.ts:96-99`
- `packages/app-store/salesforce/lib/CalendarService.ts:101-108`

**修復方向**：先加過期判斷，只在需要時 refresh；refresh 後用新 token 建連線：

```ts
const credentialKey = credential.key as unknown as ExtendedTokenResponse;
let accessToken = credentialKey.access_token;

if (isExpired(credentialKey)) {
  const parsed = parseRefreshTokenResponse(await response.json(), salesforceTokenSchema);
  accessToken = parsed.data.access_token;
  await prisma.credential.update({ ... });
}

return new jsforce.Connection({ ..., accessToken });
```

並在 constructor 對 `this.conn` 補上 rejection 處理。

#### F-012 Salesforce 用 response.statusText !== "OK" 判斷成敗，應該用 response.ok — `packages/app-store/salesforce/lib/CalendarService.ts:86`

面向 D API 慣例 · Suggestion

**問題**：```ts
if (response.statusText !== "OK") throw new HttpError({ statusCode: 400, message: response.statusText });
```

`statusText` 是 HTTP/1.1 的 reason phrase，不是可靠的成功指標：HTTP/2 規格移除了 reason phrase，Node 的 undici（Next.js 的 fetch 實作）在 HTTP/2 連線下會回傳空字串。Salesforce 的 token endpoint 支援 HTTP/2，所以這個判斷有機會在一個成功的 200 回應上丟出 400，而且錯誤訊息會是空字串，排查時看不出任何線索。反過來，任何 statusText 恰好是 "OK" 的非 2xx 回應也會被放行。

定為 Suggestion 而非 Critical：實際是否觸發取決於部署環境協商到的 HTTP 版本，這一點無法在這個 checkout 裡確認；但無論觸發與否，這個判斷方式本身是錯的，且有標準的正確寫法。

**證據**：
- `packages/app-store/salesforce/lib/CalendarService.ts:86`

**修復方向**：```ts
if (!response.ok) {
  throw new HttpError({ statusCode: response.status, message: await response.text() });
}
```

`response.ok` 是 `status` 落在 200-299 的標準判斷，且把 provider 回傳的錯誤內容一起帶出來會讓排查容易得多。

#### F-013 整個 MR 沒有任何測試，而找到的多個 Critical 是最小 unit test 就會擋下的 — `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:1`

面向 G 測試 · Suggestion

**問題**：新增 4 個檔案（含一個對外的 webhook endpoint 與兩個被 9 個 app 共用的 helper）、修改 9 條 token refresh 路徑，diff 內沒有任何測試檔。以 grep 搜尋整個 repo 的 *.test.ts / *.spec.ts / *.e2e.ts，沒有任何一個提到 app-credential、refreshOAuthTokens、parseRefreshTokenResponse 或 APP_CREDENTIAL_SHARING。

這一則不是形式要求。本次找到的 Critical 中至少三個會被非常初階的測試攔下來：F-001（斷言寫回的 credential.key 形狀）、F-005（斷言解析後 expiry 有保留）、F-006（斷言送往 sync endpoint 的 calcomUserId 等於 credential.userId）。這些 helper 是純函式或可注入的，測試成本很低。

**證據**：
- `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:1`
- `packages/app-store/_utils/oauth/refreshOAuthTokens.ts:1`
- `apps/web/pages/api/webhook/app-credential.ts:1`

**修復方向**：至少補三支：

1. `parseRefreshTokenResponse` — 給一個含 access_token、expires_in、refresh_token 的回應，斷言回傳的 data 三個欄位都在；再給一個缺 refresh_token 的回應，斷言不會被填成佔位字串。
2. `refreshOAuthTokens` — 以 stub fetch 驗證 sync 分支送出的 body 是 `calcomUserId=<userId>`，並驗證兩個分支回傳型別一致。
3. `app-credential` handler — 用 next-test-api-route-handler 之類的工具覆蓋 slug ≠ dirName 的 app（google-calendar）、錯誤 secret、非 POST 動詞三種情況。

</details>

<details>
<summary>Nit（4）</summary>

#### F-014 office365 的 tokenResponse.success 判斷已成死碼，且移除了原本的診斷輸出 — `packages/app-store/office365calendar/lib/CalendarService.ts:264`

面向 B 簡潔 · Nit

**問題**：parseRefreshTokenResponse 在 `!success` 時直接 throw（21-23 行），所以能執行到 264 行的 `tokenResponse.success && tokenResponse.data`，success 必為 true——這個 `&&` 永遠是恆真的短路，只是讓讀的人以為還有另一條路。

同時這次改動刪掉了原本在解析失敗時印出 zod error 與 MS 原始回應的 console.error。行為從「記錄下來、沿用舊憑證繼續」變成「丟一個訊息固定為 Invalid refreshed tokens were returned 的例外」，而例外裡不含 MS 實際回了什麼。之後這條路徑出問題會沒有任何可用的線索。

**證據**：
- `packages/app-store/office365calendar/lib/CalendarService.ts:264`
- `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:21-23`

**修復方向**：把 `...(tokenResponse.success && tokenResponse.data)` 簡化成 `...tokenResponse.data`，並考慮在 parseRefreshTokenResponse 丟出的 Error 裡帶上 `refreshTokenResponse.error` 的內容：

```ts
if (!refreshTokenResponse.success) {
  throw new Error(`Invalid refreshed tokens were returned: ${refreshTokenResponse.error.message}`);
}
```

#### F-015 zoho-bigin/api/add.ts 把 ${appConfig.slug} 改回硬編字串，與本 MR 目的無關 — `packages/app-store/zoho-bigin/api/add.ts:12`

面向 B 簡潔 · Nit

**問題**：這一行的 redirectUri 從讀 config 改成寫死：原本是 `` WEBAPP_URL + `…integrations/${appConfig.slug}/callback` ``，改成 `` WEBAPP_URL + `…integrations/zoho-bigin/callback` ``。

這是一次反向改動。`appConfig` 仍然在同檔被 import 與使用（10 行的 getAppKeysFromSlug、19 行的 scope），所以不是為了移除相依。改完之後如果哪天 config.json 的 slug 變了，這裡不會跟著變，而 OAuth redirect_uri 不一致的錯誤訊息通常很難對應回原因。

另外改完後的字串已經沒有任何插值，卻仍用 template literal，一般 lint 設定會提示改用一般引號。

這個改動與 credential sync 沒有關係，在一個已經有 40 個檔案的 diff 裡屬於夾帶。

**證據**：
- `packages/app-store/zoho-bigin/api/add.ts:12`

**修復方向**：還原成從 `appConfig.slug` 取值的寫法。若確實有理由要寫死（例如 slug 曾經改過而 callback 路由沒跟著改），請留一行註解說明，否則下一個人會直接把它改回去。

#### F-016 turbo.json 的 globalEnv 清單插入位置破壞了字母序 — `turbo.json:205-207`

面向 H 非 Python 檔 · Nit

**問題**：新增的四個變數有三個放對了位置，但 `CALCOM_WEBHOOK_SECRET` 被插在 `CALENDSO_ENCRYPTION_KEY` 之後：

```
"CALCOM_WEBHOOK_HEADER_NAME",
"CALENDSO_ENCRYPTION_KEY",
"CALCOM_WEBHOOK_SECRET",
```

這份清單其餘部分都是字母序，維持順序的價值在於下一個人要加變數時能一眼找到位置、也比較不會重複加。

**證據**：
- `turbo.json:205-207`

**修復方向**：把 `CALCOM_WEBHOOK_SECRET` 移到 `CALCOM_WEBHOOK_HEADER_NAME` 之後、`CALENDSO_ENCRYPTION_KEY` 之前。

#### F-017 webhook handler 上方留了一個空的 JSDoc 區塊 — `apps/web/pages/api/webhook/app-credential.ts:16`

面向 A 風格 · Nit

**問題**：`/** */` 是一個空的 JSDoc，沒有任何內容。這個 handler 是本 MR 唯一的對外入口，它需要的正好是一段說明：誰會呼叫它、payload 怎麼加密、失敗時回什麼。空著的註解比沒有註解更容易被之後的人忽略。

**證據**：
- `apps/web/pages/api/webhook/app-credential.ts:16`

**修復方向**：補完或刪掉。建議補完，例如：

```ts
/**
 * Credential sync webhook. 由 self-hoster 的母系統呼叫，把已取得的 app OAuth
 * credential 寫入對應 Cal.com 使用者。
 * - 驗證：CALCOM_WEBHOOK_HEADER_NAME 指定的 header 必須等於 CALCOM_WEBHOOK_SECRET
 * - keys 欄位須以 CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY 做 AES256 加密
 */
```

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 Cal.com 對外呼叫 CALCOM_CREDENTIAL_SYNC_ENDPOINT 時完全沒有帶任何認證，而反方向的 webhook 卻要求 shared secret。這個不對稱是刻意的（假設該 endpoint 位於內網或由 self-hoster 自行加保護），還是漏了？

面向 C 安全

**背景**：refreshOAuthTokens.ts:8-14 只送出 `calcomUserId` 與 `appSlug`，沒有 Authorization header、沒有簽章、沒有共用密鑰。任何能打到那個 endpoint 的人都可以用任意 calcomUserId 去索取 token。程式碼註解（refreshOAuthTokens.ts:6-7）寫著「Customize the payload based on what your endpoint requires」，讀起來像是刻意留給 self-hoster 自行擴充的樣板；但 .env.example:233-244 新增的說明段落沒有提到這個 endpoint 需要自行加上認證，也沒有說它必須不對外開放。無法從這個 checkout 判斷作者的意圖，所以不給 severity。

**如何確認**：作者回答這個 endpoint 的預期部署位置（內網／公網），以及是否打算在 .env.example 或文件中明示「此 endpoint 必須自行加上認證且不應對外開放」。若答案是「應該帶認證」，就是一個要補的 Critical；若是「假設內網」，則需要把這個假設寫進 .env.example。

#### Q-002 CALCOM_WEBHOOK_HEADER_NAME 必須是小寫，否則整個 webhook 會靜默回 403。除了 .env.example 的一行註解之外，這件事有在使用者看得到的文件裡說明嗎？

面向 H 非 Python 檔

**背景**：app-credential.ts:25 用 `req.headers[process.env.CALCOM_WEBHOOK_HEADER_NAME || "calcom-webhook-secret"]` 取值。Next.js 的 req.headers 一律是小寫鍵，所以 self-hoster 若把這個變數設成 `Calcom-Webhook-Secret`，查表得到 undefined，比對失敗回 403「Invalid webhook secret」——錯誤訊息會把人引導去檢查 secret 的值，而真正的原因是 header 名稱的大小寫。目前唯一的提示是 .env.example:238 的「Should be in lowercase」。這個 checkout 裡沒有任何 .md 提到 credential sync（已 grep 確認），無法判斷 cal.com 的文件站是否另有說明，所以不給 severity。

**如何確認**：確認 cal.com 文件站的 self-hosting 章節是否會一併更新。若不會，最省事的做法是在程式碼裡直接 `.toLowerCase()`，讓大小寫不再是使用者要記住的事。

</details>
