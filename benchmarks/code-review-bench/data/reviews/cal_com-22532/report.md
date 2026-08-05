## 審查結論：Approved with Comments

> Critical 0 · Suggestion 11 · Nit 4 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：migration 保留了與實際 SQL 矛盾的自動產生警告（F-013），且 SelectedCalendarRepository.findMany 拿掉了 satisfies 型別約束（F-014）。
- **B 簡潔**（未通過）：有一段寫進資料庫但沒有任何讀取端的殘留程式碼（F-002），以及兩件與主題無關的夾帶改動（F-009）。
- **D API 慣例**（未通過）：新的 deleteCache procedure 未遵循本 router 既有的 schema 檔案慣例，並以 throw new Error 取代 TRPCError（F-003）。
- **E 架構**（未通過）：connectedCalendars handler 繞過 CalendarCache.init 的 feature flag 工廠（F-001），且 packages/features 反向相依 @calcom/platform-constants（F-004）。
- **F 資料取用與資料庫**（未通過）：migration 用 DEFAULT NOW() 讓所有既有列取得部署當下的時間戳，UI 會把它當成真實的「最後更新」顯示（F-012）。新增的 groupBy 查詢本身索引路徑正常（PK 為 [credentialId, key]），無額外索引需求。
- **G 測試**（未通過）：新增一個帶授權檢查的 mutation、一個 repository 方法與一個元件，零測試（F-008）。
- **H 非 Python 檔**（未通過）：整份 diff 都是非 Python 檔（TSX/TS/SQL/JSON/Shell）。Dropdown 使用了錯誤的 Radix 原語與硬編色票（F-005）、時間格式硬編 en-US（F-006）、在明確標記唯讀的畫面上仍渲染破壞性動作（F-011）、元件回傳 null 時仍留下空白版面（F-015），以及 shell 腳本用了 BSD 專屬的 sed 語法（F-010）。
- **I 回溯分析**（未通過）：UserWithCalendars 型別加寬後，兩個外部呼叫端（apps/api/v1 與 apps/api/v2）都用 selectedCalendars: true 取整個 model，因此不會斷——這點已逐一確認。但 DisconnectIntegration 原本內建的 in-memory delegation credential 防護在換成 CredentialActionsDropdown 後消失了（F-007）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 夾帶了兩件與快取狀態 UI 無關的改動：apps/web/package.json:14 把 dev:cron 從 ts-node 換成 npx tsx，以及新增 scripts/test-gcal-webhooks.sh（Tunnelmole webhook 開發腳本）。另外 packages/app-store/googlecalendar/lib/CalendarService.ts:1023-1024 寫入 SelectedCalendar.updatedAt 的那段，是早期做法（commit 08762ca）的殘留，最終版本改用 CalendarCache.updatedAt 之後沒有一起移除。這三件事建議拆出去或刪掉，讓這個 MR 只留快取狀態這一條線。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 略過 | ruff 有裝、實際跑完、exit code 0、0 筆診斷——但這份 diff 沒有任何 .py 檔案，整個 cal.com 也是 TypeScript 專案，所以這個 0 代表「掃描範圍是空的」，不代表「乾淨」。本次變更沒有得到 ruff 的任何覆蓋。 · exit code 0 · in_diff 0、outside_diff 0 |
| ty | 略過 | ty 未安裝（不在 PATH 上）。即使安裝也不適用——本次變更沒有 Python 檔案。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。這是本次唯一能真正檢查 .ts/.tsx 的工具，因此本次變更的 11 個 TypeScript/TSX 檔案完全沒有自動化 lint 覆蓋，所有 TS 相關結論都來自人工閱讀原始碼。 |
| tsc / yarn type-check | 略過 | checkout 沒有 node_modules，環境也沒有網路，無法安裝相依套件，因此無法執行型別檢查與任何測試。所有型別相容性的判斷（例如 F-002 是否會弄壞 apps/api/v1 與 apps/api/v2）都是靠閱讀對應 repository 的 select 手動確認的。 |
| trivy | 略過 | trivy 未安裝（不在 PATH 上），略過相依套件漏洞、設定錯誤與 secret 掃描。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），略過 SAST 掃描。 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號索引。本次的呼叫者追蹤、影響範圍與「有沒有漏掉的呼叫端」全部改用 grep 逐一確認。 |
| ncr-fresh-eyes（子代理） | 略過 | 本次執行環境沒有可用的子代理派送工具，無法派出 fresh-eyes。依 skill 規定不以主流程自行模擬，改為如實揭露：這份報告的所有發現都經過 review-dimensions 的框架，缺少一次未被清單塑形的閱讀。 |
| ncr-quality-check（子代理） | 略過 | 同上，無法派送子代理，因此報告 JSON 沒有經過獨立的品質複查，只經過 report_model.py 的機械驗證。 |

<details>
<summary>Suggestion（11）</summary>

#### F-001 connectedCalendars handler 直接 new CalendarCacheRepository()，繞過 calendar-cache feature flag — `packages/trpc/server/routers/viewer/calendars/connectedCalendars.handler.ts:28`

面向 E 架構 · Suggestion

**問題**：這個 codebase 取得 calendar cache repository 的唯一正規入口是 CalendarCache.init()／initFromCredentialId()，它會先問 FeaturesRepository.checkIfFeatureIsEnabledGlobally("calendar-cache")，關閉時回傳 CalendarCacheRepositoryMock。CalendarService.ts:463、616、981 三處都是這樣拿的。這裡改成直接 new 具體類別，等於把 flag 從這條路徑上拿掉：無論 flag 開關，每一次 connectedCalendars 查詢都會多打一次 calendarCache.groupBy。連帶的證據是這個 MR 在 mock 上補了 getCacheStatusByCredentialIds()（回傳 []），但因為 handler 不走工廠，那段 mock 永遠不會被執行——補了一個到不了的分支，正好說明入口選錯了。影響面不小：grep 顯示 trpc.viewer.calendars.connectedCalendars 有 8 個呼叫端，包含 Booker 的行事曆疊加（packages/features/bookings/Booker/components/hooks/useCalendars.ts:63）、onboarding、event type advanced tab，其中大多數根本不需要快取狀態。

**證據**：
- `packages/trpc/server/routers/viewer/calendars/connectedCalendars.handler.ts:28`
- `packages/features/calendar-cache/calendar-cache.ts:22-28`
- `packages/features/calendar-cache/calendar-cache.repository.mock.ts:23-26`

**修復方向**：改成走工廠，讓 flag 與 mock 都生效：

```ts
import { CalendarCache } from "@calcom/features/calendar-cache/calendar-cache";

const cacheRepository = await CalendarCache.init(null);
const cacheStatuses = await cacheRepository.getCacheStatusByCredentialIds(credentialIds);
```

若想再省一次查詢，可以只在真的需要顯示快取狀態的呼叫端（設定頁）帶入一個 input flag，Booker／onboarding 這類路徑就完全不查。

#### F-002 SelectedCalendar.updatedAt 的寫入與相關欄位是前一版做法的殘留，沒有任何讀取端 — `packages/app-store/googlecalendar/lib/CalendarService.ts:1023-1024`

面向 B 簡潔 · Suggestion

**問題**：grep 過整個 repo：SelectedCalendar.updatedAt 與新加進 select 的 googleChannelId，除了這裡寫入之外沒有任何地方讀取；UI 顯示的時間戳全部來自 CalendarCache.updatedAt（connectedCalendars.handler.ts:29 → CredentialActionsDropdown.tsx:89）。updateManyByCredentialId 也只有 CalendarService.ts:1024 這一個呼叫端。從 commit 歷史看得出來這是 08762ca「update SelectedCalendar.updatedAt when Google webhooks trigger cache refresh」的做法，在 57d4a0b 改走 connectedCalendars handler 之後就沒有用了。留著的代價是實際的：fetchAvailabilityAndSetCache 是 Google webhook 的處理路徑（packages/app-store/googlecalendar/api/webhook.ts:46），每一次 push notification 都會對該 credential 底下「全部」的 SelectedCalendar 列做一次 UPDATE——包含這次根本沒被刷新的 per-eventType 列。同樣的，findUnlockedUserForSession 是每一個已登入 tRPC 請求都會跑的 session middleware（packages/trpc/server/middlewares/sessionMiddleware.ts:29），在那裡多撈兩個沒人用的欄位是純成本。另外 data 傳空物件 {} 依賴 Prisma 對 @updatedAt 的隱含行為，讀的人看不出這行在做什麼——見 Q-001。

**證據**：
- `packages/app-store/googlecalendar/lib/CalendarService.ts:1023-1024`
- `packages/lib/server/repository/selectedCalendar.ts:400-405`
- `packages/lib/getConnectedDestinationCalendars.ts:20-29`
- `packages/lib/server/repository/user.ts:899-900`

**修復方向**：把整條殘留拆掉：刪除 CalendarService.ts:1023-1024 這兩行、刪除 SelectedCalendarRepository.updateManyByCredentialId、把 getConnectedDestinationCalendars.ts 的 UserWithCalendars 還原成只 Pick externalId / integration / eventTypeId，並移除 user.ts:892-893 的兩個 select 欄位。若之後真的需要「這個行事曆連線最後同步時間」，正確的來源已經是 CalendarCache.updatedAt。

#### F-003 deleteCache procedure 未遵循 calendars router 的 schema 與錯誤慣例 — `packages/trpc/server/routers/viewer/calendars/_router.tsx:28-33`

面向 D API 慣例 · Suggestion

**問題**：同一個 router 內另外兩個 procedure 都是同一個形狀：schema 放在 <name>.schema.ts，匯出 Z<Name>InputSchema 與 T<Name>InputSchema，handler 用 TrpcSessionUser + T<Name>InputSchema 標型，並在檔頭的 CalendarsRouterHandlerCache 型別裡登記。deleteCache 三項都沒做：zod schema 內嵌在 _router.tsx、handler 自己重寫了一份 inline input 型別、CalendarsRouterHandlerCache 沒有新增條目。錯誤處理也不同：這裡是 throw new Error("Credential not found or access denied")，而同 router 的 setDestinationCalendar.handler.ts:77 用的是 TRPCError。差別不是風格而已——tRPC 會把非 TRPCError 的例外包成 INTERNAL_SERVER_ERROR，於是「查無此 credential／不是你的」會在 HTTP 層以 500 呈現，監控與錯誤率看板會把使用者的正常誤操作算成伺服器故障。

**證據**：
- `packages/trpc/server/routers/viewer/calendars/_router.tsx:28-33`
- `packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:2-11`
- `packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:25`
- `packages/trpc/server/routers/viewer/calendars/connectedCalendars.schema.ts:1-12`
- `packages/trpc/server/routers/viewer/calendars/setDestinationCalendar.handler.ts:77`

**修復方向**：新增 deleteCache.schema.ts：

```ts
import { z } from "zod";
export const ZDeleteCacheInputSchema = z.object({ credentialId: z.number().int().positive() });
export type TDeleteCacheInputSchema = z.infer<typeof ZDeleteCacheInputSchema>;
```

_router.tsx 改引用它並在 CalendarsRouterHandlerCache 補上 deleteCache；handler 的 input 型別改成 TDeleteCacheInputSchema；錯誤改成 `throw new TRPCError({ code: "NOT_FOUND", message: "Credential not found or access denied" })`。順帶一提，schema 加上 .int().positive() 也能在入口就擋掉負數的 in-memory delegation credential id。

#### F-004 packages/features 反向相依 @calcom/platform-constants，且該相依未宣告 — `packages/features/apps/components/CredentialActionsDropdown.tsx:6`

面向 E 架構 · Suggestion

**問題**：grep 過全 repo：@calcom/platform-constants 的引用全部落在 packages/platform/** 底下，這個新檔案是 packages/features 裡唯一一個引用它的。方向是反的——platform 建立在 features 之上，不是相反。而且 packages/features/package.json 的 dependencies 沒有列 @calcom/platform-constants，目前能解析純粹是靠 yarn workspace 的 hoisting，任何改變 hoisting 或改用嚴格解析的建置設定都會讓它斷掉，而且斷的位置離改動很遠。同一個 package 裡判斷 Google Calendar 的既有寫法就是字串常量本身，例如 packages/features/eventtypes/components/Locations.tsx:579、packages/features/auth/lib/next-auth-options.ts:650。

**證據**：
- `packages/features/apps/components/CredentialActionsDropdown.tsx:6`
- `packages/features/package.json:9-29`

**修復方向**：兩種都可以，選一種：(a) 直接沿用 packages/features 現行寫法，把 import 拿掉、比對 `integrationType === "google_calendar"`；(b) 若不想留裸字串，把常量提到 @calcom/lib（features 已宣告相依）再從兩邊引用。不建議的是保留現狀又不補 package.json——那會讓一個能動的建置在未來某次設定調整時無聲斷掉。

#### F-005 Dropdown 用 DropdownMenuItem 當靜態標題、用 <hr> 當分隔線，並硬編色票 — `packages/features/apps/components/CredentialActionsDropdown.tsx:84-92`

面向 H 非 Python 檔 · Suggestion

**問題**：「Cache Status / Last updated: …」是純資訊，卻包在 DropdownMenuItem 裡。Radix 的 Item 是可聚焦、可選取的項目：鍵盤上下鍵會停在這裡，Enter 或點擊會觸發選取並關閉選單，而它什麼也不做——對只用鍵盤操作的人來說，選單裡多了一格沒有作用的停留點。packages/ui 已經匯出正確的原語：DropdownMenuLabel（Dropdown.tsx:55）給標題、DropdownMenuSeparator（Dropdown.tsx:184）給分隔線，這裡卻用了一個裸 <hr className="my-1" />。色票同理：text-gray-900 / text-gray-500 是 Tailwind 原生色階，packages/features 底下 text-emphasis / text-subtle 這類語意 token 有 357 處、text-gray-* 只有 34 處，語意 token 才是這個 codebase 的慣例。而且 text-xs text-gray-500 dark:text-white 在深色模式下會把次要說明文字染成純白，和上一行的標題完全同色，原本想表達的層次消失。

**證據**：
- `packages/features/apps/components/CredentialActionsDropdown.tsx:84-92`
- `packages/features/apps/components/CredentialActionsDropdown.tsx:112`
- `packages/ui/components/dropdown/Dropdown.tsx:55`
- `packages/ui/components/dropdown/Dropdown.tsx:184`

**修復方向**：把靜態區塊換成 `<DropdownMenuLabel>` 並移除外層 `<DropdownMenuItem>`，把 `<hr className="my-1" />` 換成 `<DropdownMenuSeparator />`；顏色改成 `text-emphasis`（標題）與 `text-subtle`（時間），兩者本身就已處理深淺色模式，不需要再寫 dark: 變體。

#### F-006 時間戳硬編 en-US 格式，忽略同一個元件已取得的使用者語系 — `packages/features/apps/components/CredentialActionsDropdown.tsx:88-92`

面向 H 非 Python 檔 · Suggestion

**問題**：這個元件第 37 行已經 `const { t } = useLocale()`，翻譯字串走 i18n，但緊接著的日期卻用 `new Intl.DateTimeFormat("en-US", …)` 寫死。結果是 cal.com 支援的所有非英語語系使用者，看到的都是美式的 M/D/YY 與 12 小時 AM/PM——在同一行 i18n 過的文案旁邊。useLocale 的回傳型別（useLocale.ts:10-14）本來就含 i18n 實例，語系是現成的，不需要額外接線。

**證據**：
- `packages/features/apps/components/CredentialActionsDropdown.tsx:88-92`
- `packages/features/apps/components/CredentialActionsDropdown.tsx:37`
- `packages/lib/hooks/useLocale.ts:10-14`

**修復方向**：把 i18n 一起解構出來並交給 Intl：

```tsx
const { t, i18n } = useLocale();
// ...
new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "short" }).format(new Date(cacheUpdatedAt))
```

若希望同時尊重使用者設定檔的 12/24 小時制與時區，用 codebase 既有的 dayjs 包裝會更一致。

#### F-007 換掉 DisconnectIntegration 的同時，遺失了 in-memory delegation credential 的第二道防護 — `packages/features/apps/components/DisconnectIntegration.tsx:43`

面向 I 回溯分析 · Suggestion

**問題**：把「是否可以中斷連線」這件事列出所有到達路徑之後才看得出來。舊的 DisconnectIntegration 有兩道判斷：呼叫端的 `!connectedCalendar.delegationCredentialId`，以及元件自身的 `disableDisconnect = isDelegationCredential({ credentialId })`（DisconnectIntegration.tsx:43、49，判準是 credentialId < 0，也就是 in-memory delegation credential）。新的 CredentialActionsDropdown 只剩下前者（`canDisconnect = !delegationCredentialId && !disableConnectionModification`）。這道差異會真的漏出去，因為 getConnectedCalendars 回傳的形狀不只一種：CalendarManager.ts:89-97 的「No primary calendar found」提早返回，物件裡根本沒有 delegationCredentialId 這個欄位。走到那條路徑的 delegation credential，`!undefined` 為真 → canDisconnect 為真 → 「Remove app」這次是可以按的（過去是渲染出來但 disabled）。按下去之後 handleDeleteCredential.ts:49-67 用負數 id 查 credential 會查不到並丟 "Credential not found"，前端只顯示一句泛用的錯誤 toast。後果有界（不會誤刪任何東西），但這是一個「看起來可以做、按了必定失敗」的按鈕，而且原本是防好的。

**證據**：
- `packages/features/apps/components/DisconnectIntegration.tsx:43`
- `packages/features/apps/components/CredentialActionsDropdown.tsx:68`
- `packages/lib/CalendarManager.ts:89-97`
- `packages/features/credentials/handleDeleteCredential.ts:49-67`

**修復方向**：把第二道防護搬進新元件，讓它不依賴呼叫端傳什麼：

```tsx
import { isInMemoryDelegationCredential } from "@calcom/lib/delegationCredential/clientAndServer";
// ...
const canDisconnect =
  !delegationCredentialId &&
  !disableConnectionModification &&
  !isInMemoryDelegationCredential({ credentialId });
```

同一個判斷也建議套在 hasCache 上——負數 credentialId 不可能有 CalendarCache 列（calendar-cache.repository.ts:35-42 的 assertCalendarHasDbCredential 會擋掉），提早排除可以讓意圖更明確。

#### F-008 新增的 mutation、repository 方法與元件都沒有測試，授權檢查也在其中 — `packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:17-26`

面向 G 測試 · Suggestion

**問題**：這個 MR 沒有新增任何測試檔。慣例是有的、而且就在旁邊：同 router 的 setDestinationCalendar 有 setDestinationCalendar.handler.test.ts，同 package 的 calendar cache repository 有 calendar-cache.repository.test.ts。最需要被釘住的是 deleteCache.handler.ts:17-26 的擁有權檢查——它是這條破壞性路徑上唯一的授權關卡，而授權關卡正是那種「重構時被順手改掉、沒有測試就沒人發現」的東西。getCacheStatusByCredentialIds 的 groupBy + _max 映射也值得一個測試：目前沒有任何地方驗證過「同一個 credential 有多列快取時取到的是最新那一列」，以及「沒有快取列的 credential 不會出現在結果裡」。

**證據**：
- `packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:17-26`
- `packages/features/calendar-cache/calendar-cache.repository.ts:173-186`
- `packages/trpc/server/routers/viewer/calendars/setDestinationCalendar.handler.test.ts`
- `packages/features/calendar-cache/calendar-cache.repository.test.ts`

**修復方向**：至少補三個：(1) deleteCache handler——傳入他人的 credentialId 必須丟錯且不刪任何列，傳入自己的則刪光該 credential 的列；(2) getCacheStatusByCredentialIds——多列取 _max、無列時 credentialId 不出現在回傳中；(3) CredentialActionsDropdown 的早退條件（canDisconnect 與 hasCache 都為 false 時回傳 null）。前兩個可以直接掛在上面兩個既有測試檔的模式上。

#### F-009 夾帶無關改動，其中 dev:cron 改用 npx tsx 會在執行時從 registry 下載未鎖版的套件 — `apps/web/package.json:14`

面向 B 簡潔 · Suggestion

**問題**：兩件改動與「快取狀態 UI」沒有關係：dev:cron 從 ts-node 換成 npx tsx，以及新增 scripts/test-gcal-webhooks.sh（Tunnelmole webhook 開發腳本）。ts-node 換 tsx 本身可以理解，但換法有問題：ts-node 是 apps/web 的 devDependency（package.json:196，^10.9.1），而 tsx 在整個 repo 的任何 package.json 裡都不存在。`npx tsx` 因此不會解析到本地套件，而是在執行當下向 npm registry 下載一份未鎖定版本的 tsx——需要網路、無法重現、不受 lockfile 保護，離線或 registry 受限的開發環境會直接失敗。這和 repo 其他地方一律走 lockfile 的做法不一致。

**證據**：
- `apps/web/package.json:14`
- `apps/web/package.json:196`
- `scripts/test-gcal-webhooks.sh:1`

**修復方向**：把 tsx 加進 apps/web 的 devDependencies 並直接呼叫 `tsx cron-tester.ts`（yarn 會解析到本地的 bin），或維持 ts-node 不動。另外建議把 dev:cron 與 scripts/test-gcal-webhooks.sh 拆成獨立的 MR——它們是開發工具，和這條使用者可見的功能各自有各自的審查重點。

#### F-010 test-gcal-webhooks.sh 用了 BSD/macOS 專屬的 sed -i '' 語法，在 Linux 上會失敗 — `scripts/test-gcal-webhooks.sh:68`

面向 H 非 Python 檔 · Suggestion

**問題**：`sed -i '' -E "s|…|" "$ENV_FILE"` 是 BSD sed 的寫法（-i 需要一個獨立的備份後綴參數）。在 GNU sed 上（Linux 開發機、容器、CI）那個空字串會被當成 script 讀進去，指令直接報錯，於是 GOOGLE_WEBHOOK_URL 不會被寫入——但腳本下一行仍然印出「✅ Updated GOOGLE_WEBHOOK_URL …」，使用者會以為成功了。這個 repo 其他腳本用的是 GNU 形式（deploy/install.sh:6-7 的 `sed -i 's|…|'`），所以不是「本 repo 只支援 macOS」。另外 ENV_FILE 寫成相對路徑 "../.env"（第 5 行），腳本必須從 scripts/ 目錄執行才會指到 repo 根目錄的 .env，從別處執行會安靜地建立並寫入一個錯誤位置的 .env。

**證據**：
- `scripts/test-gcal-webhooks.sh:68`
- `scripts/test-gcal-webhooks.sh:5`
- `deploy/install.sh:6-7`

**修復方向**：改成兩邊都能動的寫法，例如 `sed -E "s|^GOOGLE_WEBHOOK_URL=.*|GOOGLE_WEBHOOK_URL=$TUNNEL_URL|" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"`；並把路徑改成相對於腳本自身：`ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"`。同時建議在 sed 失敗時讓腳本中止（`set -euo pipefail`），避免印出與事實相反的成功訊息。

#### F-011 在明確標記 disableConnectionModification 的畫面上，破壞性的「刪除快取」仍然顯示 — `packages/features/apps/components/CredentialActionsDropdown.tsx:68-73`

面向 H 非 Python 檔 · Suggestion

**問題**：disableConnectionModification 目前只有一個傳入 true 的呼叫端：event type 的 Advanced 分頁（EventAdvancedTab.tsx:375）。改動前，該旗標為真時整個 actions 區塊完全不渲染，那個畫面上沒有任何連線相關的操作。改動後，`hasCache` 這條分支不受 canDisconnect 約束（CredentialActionsDropdown.tsx:69-73 的早退只在兩者皆為 false 時觸發），所以在同一個畫面上「Remove app」被隱藏、但「Delete cached data」照樣出現而且可以按。使用者在一個編輯 event type 的分頁上，可以清掉整個 credential 底下所有行事曆的可用性快取，影響範圍超出他當下在看的東西。旗標的字面意思是「不要在這裡讓人改動這個連線」，刪除該連線的快取顯然落在這個範圍內。

**證據**：
- `packages/features/apps/components/CredentialActionsDropdown.tsx:68-73`
- `packages/features/eventtypes/components/tabs/advanced/EventAdvancedTab.tsx:375`
- `packages/platform/atoms/selected-calendars/wrappers/SelectedCalendarsSettingsWebWrapper.tsx:69-78`

**修復方向**：把 disableConnectionModification 一併套到快取分支上，例如 `const hasCache = isGoogleCalendar && !!cacheUpdatedAt && !disableConnectionModification;`；若確實希望唯讀畫面上仍看得到「最後更新時間」，那就把資訊與破壞性動作分開——保留 DropdownMenuLabel 的時間顯示，只隱藏 delete_cached_data 那一項。

</details>

<details>
<summary>Nit（4）</summary>

#### F-012 migration 讓所有既有快取列的 updatedAt 等於部署時間，UI 會把它當成真實的最後更新顯示 — `packages/prisma/migrations/20250715160635_add_calendar_cache_updated_at/migration.sql:9`

面向 F 資料取用與資料庫 · Nit

**問題**：`ADD COLUMN "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT NOW()` 會讓每一列既有的 CalendarCache 都拿到 migration 執行當下的時間。上線後的第一段時間內，UI 對所有還沒被重新寫入的快取都會顯示「Last updated: 部署時間」——一個被當作事實呈現、但和實際寫入時間無關的值。這正是這個功能想解決的問題（使用者想知道快取有多舊），所以初始值錯誤特別可惜。既有資料其實有辦法還原：expiresAt 是寫入時間加上固定的 CACHING_TIME（repository.ts:14-16，30 天），所以 expiresAt - 30 天就是原本的寫入時間。

**證據**：
- `packages/prisma/migrations/20250715160635_add_calendar_cache_updated_at/migration.sql:9`
- `packages/features/apps/components/CredentialActionsDropdown.tsx:87-92`
- `packages/features/calendar-cache/calendar-cache.repository.ts:14-16`

**修復方向**：在同一支 migration 後面補一行 backfill：

```sql
UPDATE "CalendarCache" SET "updatedAt" = "expiresAt" - INTERVAL '30 days';
```

若不想在 migration 裡掃全表，另一個選擇是把欄位設為 nullable，UI 在值為 null 時不顯示時間而顯示「尚未記錄」，等下一次快取寫入自然補齊。

#### F-013 migration 保留了與實際 SQL 互相矛盾的自動產生警告，且檔名時間戳早於目標分支上已存在的 migration — `packages/prisma/migrations/20250715160635_add_calendar_cache_updated_at/migration.sql:1-9`

面向 A 風格 · Nit

**問題**：檔案開頭留著 Prisma 自動產生的警告：「Added the required column `updatedAt` to the `CalendarCache` table without a default value. This is not possible if the table is not empty.」但下面第 9 行的 SQL 明明加了 DEFAULT NOW()，而且第 8 行還有一句手寫註解說明就是為了安全處理既有列才加預設值。同一個檔案裡兩段註解互相打架，下一個維護者若先讀到上面那段，會以為這支 migration 在非空表上跑不起來。另外目標分支上已經存在 20250716135157_team_booking_page_cache_feature_flag，比這支新增的 20250715160635 還晚，所以合併後 migration 目錄不再是單調遞增；已經套用過較晚那支的環境，在本機 `prisma migrate dev` 時容易被判定為歷史不一致。

**證據**：
- `packages/prisma/migrations/20250715160635_add_calendar_cache_updated_at/migration.sql:1-9`
- `packages/prisma/migrations/20250716135157_team_booking_page_cache_feature_flag`

**修復方向**：刪掉開頭那段自動產生的 Warnings 區塊（或改寫成「刻意加上 DEFAULT NOW() 以便安全處理既有列」），只留手寫那句；並用 `prisma migrate dev --create-only` 以現在的時間重新產生目錄名，讓它排在 20250716135157 之後。

#### F-014 SelectedCalendarRepository.findMany 拿掉了 satisfies 型別約束，與本次功能無關 — `packages/lib/server/repository/selectedCalendar.ts:259-260`

面向 A 風格 · Nit

**問題**：原本 `const args = { where, select, orderBy } satisfies Prisma.SelectedCalendarFindManyArgs;` 的作用是在編譯期確認這三個欄位真的組得出一個合法的 findMany 參數；改成直接內聯物件之後，這個檢查沒有了，錯誤會延後到執行期才由 Prisma 丟出。這行和快取狀態功能沒有關係，看起來是為了繞過某個型別錯誤而拿掉的——但若真有型別錯誤，那個錯誤本身才是需要處理的東西。

**證據**：
- `packages/lib/server/repository/selectedCalendar.ts:259-260`

**修復方向**：還原 satisfies 寫法；如果它確實會報錯，請在 MR 描述或程式碼註解裡寫明是哪一個型別對不上，讓下一個人知道這裡放寬過。

#### F-015 CredentialActionsDropdown 回傳 null 時，外層仍留下一個 w-32 的空欄位 — `packages/platform/atoms/selected-calendars/wrappers/SelectedCalendarsSettingsWebWrapper.tsx:69-78`

面向 H 非 Python 檔 · Nit

**問題**：改動前，`<div className="flex w-32 justify-end">` 和裡面的按鈕一起被條件包住，沒有操作時整塊不渲染。改動後 div 永遠渲染，只有內層元件會回傳 null（CredentialActionsDropdown.tsx:71-73）。結果是在 delegation credential 或唯讀情境下，AppListCard／Alert 的右側會固定留下一塊 8rem 寬的空白，把標題與描述往左擠。

**證據**：
- `packages/platform/atoms/selected-calendars/wrappers/SelectedCalendarsSettingsWebWrapper.tsx:69-78`
- `packages/platform/atoms/selected-calendars/wrappers/SelectedCalendarsSettingsWebWrapper.tsx:121-130`
- `packages/features/apps/components/CredentialActionsDropdown.tsx:71-73`

**修復方向**：把版面容器搬進元件內（在早退之後才渲染 `<div className="flex w-32 justify-end">`），呼叫端只放 `<CredentialActionsDropdown … />`。這樣「沒有動作就不佔位」的規則跟著元件走，兩個呼叫端都自動正確。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 `prisma.selectedCalendar.updateMany({ where: { credentialId }, data: {} })` 在本專案的 Prisma 5.x 上，是否真的會產生 `SET "updatedAt" = now()`？還是會被視為空更新而報錯或無效？

面向 F 資料取用與資料庫

**背景**：CalendarService.ts:1024 傳的 data 是空物件 {}，完全仰賴 Prisma 對 @updatedAt 欄位的隱含補寫行為。SelectedCalendar.updatedAt 確實標了 @updatedAt（schema.prisma:860），所以行為上「應該」會寫入；但整個 repo 沒有第二個 updateMany 傳空 data 的例子可以對照，也沒有測試覆蓋這一行。這個 checkout 沒有 node_modules、環境沒有網路，無法安裝相依或執行測試來驗證，因此不給它嚴重度。附帶說明：這條路徑在 Google webhook 上（webhook.ts:46），若 Prisma 選擇丟出驗證錯誤，錯的是整個 webhook 回應而不只是這一行。若採納 F-002 直接移除這段程式碼，這個問題就一併消失。

**如何確認**：在有相依套件的環境跑 packages/app-store/googlecalendar/lib/__tests__/CalendarService.test.ts:664 那個 fetchAvailabilityAndSetCache 測試，並以 DEBUG="prisma:query" 觀察實際送出的 SQL；或在 psql 上直接對照 updateMany 前後 SelectedCalendar.updatedAt 是否改變。

#### Q-002 「Delete cached data」的產品意圖是「立刻強制重新整理」還是「單純清空」？目前的實作只做後者。

面向 E 架構

**背景**：deleteCache.handler.ts:28-30 只刪 CalendarCache 列，不動 SelectedCalendar.googleChannelId，也不觸發任何重新抓取。對照組是 CalendarService.ts:955 的 unwatchCalendar：它刪掉快取之後會呼叫 fetchAvailabilityAndSetCache 把剩下的行事曆補回來。所以按下這個按鈕之後，dropdown 裡的快取狀態會直接消失，要等 Google 下一次 push notification 或排程才會重新出現，期間可用性查詢會退回即時打 Google API。如果使用者的心智模型是「重新整理」，這個行為會讓人以為壞掉了；如果是「清掉這份可能有問題的快取」，那目前的行為就是對的。這是產品意圖問題，不是可以從程式碼斷定的，所以不給嚴重度。

**如何確認**：作者或 PM 說明這個按鈕面向的情境。若是「強制重新整理」，handler 應該在刪除後呼叫 CalendarCache.initFromCredentialId(credentialId) 對應的 service 重新抓一次，並把文案從 delete_cached_data 改成 refresh 語意。

</details>
