## 審查結論：Request Changes

> Critical 1 · Suggestion 4 · Nit 5 · 未驗證提問 3
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ✅ | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：三個檔案把 import type 改回值匯入，其中一個匯入完全沒被使用；另外 immediateDelete 這個參數名與它實際做的事不符（F-005、F-006）。
- **B 簡潔**（未通過）：handleNewBooking 新增的 try/catch 在結構上捕捉不到任何東西（F-007）；cron 裡收集 Prisma promise 再 Promise.all 的寫法在序列 await 的迴圈中沒有帶來任何好處（F-004）。
- **E 架構**（未通過）：「標記 cancelled、交給 cron 收尾」的設計對排程時間近的 reminder 有一段確定性的破口（F-001）；取消呼叫全部沒有 await，原本的 await Promise.all 被移除（F-003）。
- **F 資料取用與資料庫**（未通過）：cron 的取消查詢缺少 method 篩選（F-002）；取消迴圈沒有逐筆錯誤隔離、失敗時已取消的記錄不會被刪除（F-004）；新欄位是可為 null 的 Boolean 且無 default，語意上有三態（F-008）。
- **G 測試**（未通過）：沒有任何測試隨這次改動加入，repo 內也 grep 不到既有的 workflowReminder 測試（F-010）。改動的是「什麼時候不要寄信」，是 happy path 上看不出壞掉的行為。
- **H 非 Python 檔**（未通過）：本次 diff 全為非 Python 檔（.ts / .tsx / .prisma / .sql）。React 元件的三個渲染分支（未驗證／已驗證／表單錯誤）都已檢視，行為正確；Prisma migration 為新增 nullable 欄位、無破壞性 DDL、與 schema 一致（Prisma 無 downgrade 機制，不適用可逆性檢查）。fail 的原因是元件改動與本 PR 主題無關（F-009）。
- **I 回溯分析**（未通過）：兩個 exported 函式的簽章都改了（新增 reminderId、referenceId 放寬為 string | null、新增 immediateDelete）。已 grep 全 repo 確認 14 個呼叫點全部更新，沒有殘留舊簽章的呼叫。但呼叫點之間對「誰負責刪除記錄」的保證不一致且沒有任何註解說明（F-006）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：packages/features/ee/workflows/components/WorkflowStepContainer.tsx:390 的 UI 條件重構與 reminder 生命週期無關（詳見 F-009）。它本身是安全的，但混在同一個 diff 裡，之後要 bisect 或 revert reminder 相關行為時會連帶動到 UI。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | preflight 回報未安裝（PATH 上找不到 trivy）。相依套件漏洞、設定錯誤與 secret 掃描本次完全沒有執行；本 PR 未改動 package.json / yarn.lock，但這不等於掃過。 |
| opengrep | 略過 | 未安裝，且 preflight 回報預設 ruleset 目錄（NCR_OPENGREP_RULES 未設定時的預設位置）不存在。本次 diff 的 .ts / .tsx 檔案沒有任何 SAST 覆蓋。 |
| ruff | 略過 | 已實際執行（exit 0），但整個 repository 沒有任何 Python 檔案，ruff 回報「No Python files found under the given path(s)」。對本次全 TypeScript / SQL / Prisma schema 的 diff 不適用。刻意不記為 ok，避免看起來像通過了一次真的掃描。 · exit code 0 |
| ty | 略過 | 未安裝；且本次 diff 沒有 Python 檔，即使安裝也無事可做。 |
| oxlint | 略過 | 未安裝。這是本次唯一能對 .ts / .tsx 做自動靜態檢查的工具，它缺席代表 TypeScript 這一側完全沒有機器把關，以下所有結論都出自人工閱讀原始碼。 |
| codegraph | 略過 | 未安裝，導覽全程改用 grep。呼叫點列舉（deleteScheduledEmailReminder 7 處、deleteScheduledSMSReminder 7 處、workflowReminder 的所有寫入點 8 處）以全 repo grep 完成，已在各 finding 的 evidence 中列出。 |
| ncr-fresh-eyes（subagent） | 略過 | 本次執行環境沒有可用的 subagent 派發工具（工具清單中沒有 Task／Agent 類工具，ToolSearch 也查不到），因此無法派出 fresh-eyes。依 SKILL.md 規定不得由主 agent 自行模擬，故略過並揭露：本報告缺少一次未被檢查表框架化的閱讀。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派發 subagent。report.json 只經過 scripts/report_model.py validate 的機械驗證，沒有經過第二方的品質檢查。 |
| yarn lint / yarn type-check（專案自帶） | 略過 | 未在本機執行：checkout 沒有 node_modules，且執行環境不得連外安裝相依套件。F-005 與 Q-002 中凡是牽涉「CI 會不會紅」的判斷，都是從設定檔推導而非實際跑出來的，已在各自的 rationale 中標明推導依據。 |

### Critical

#### F-001 取消預約改為「標記 cancelled、等 cron 收尾」之後，排程時間落在下一次 cron 執行之前的 reminder 一定會寄出 — `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:225`

面向 E 架構 · Critical

**問題**：取消預約（handleCancelBooking.ts:488）與請求改期（bookings.tsx:490）都不帶 immediateDelete，走的是 emailReminderManager.ts:225 的預設分支：只把記錄標成 cancelled: true，不對 SendGrid 送任何取消請求。實際的取消交給 cron。問題出在 cron handler 內兩個區塊的順序：scheduleEmailReminders.ts:34 的 deleteMany 會先刪掉所有 method=EMAIL 且 scheduledDate <= now 的記錄，完全不看 cancelled；scheduleEmailReminders.ts:44 的取消查詢才在後面跑。於是：預約在 C 時刻被取消、reminder 排在 S 時刻寄出、下一次 cron 在 T1 執行，若 S < T1，這筆記錄會在 T1 被第一段 deleteMany 直接刪除，SendGrid 從頭到尾沒有收到任何取消請求，信在 S 準時寄出；而且記錄已經消失，之後任何一輪 cron 都不可能補救。.github/workflows/cron-scheduleEmailReminders.yml:8 的排程是每 15 分鐘一次（0,15,30,45），所以破口是「取消動作發生在 reminder 寄出前 15 分鐘以內」——這正是使用者最常臨時取消會議的時段。破口大小等於兩次 cron 執行的間隔，即使把 cron 調到最密也不會歸零，因為機制本身是延後的。另外該 job 帶 if: env.APP_URL && env.CRON_API_KEY，self-host 若沒設定這兩個 secret，cron 根本不會跑，所有被標記 cancelled 的 reminder 全部會照常寄出。反證搜尋：grep 過全 repo 的 deleteScheduledEmailReminder 呼叫點，只有 handleNewBooking.ts:968 與 workflows.tsx:214、521 帶 immediateDelete=true，取消預約與請求改期兩條路徑上沒有任何其他地方對 SendGrid 送出取消；handleCancelBooking.ts:479 的 cancelScheduledJobs 處理的是 Zapier scheduled job，與 SendGrid 無關。

**證據**：
- `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:225`
- `packages/features/bookings/lib/handleCancelBooking.ts:488`
- `packages/trpc/server/routers/viewer/bookings.tsx:490`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:34`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:44`
- `.github/workflows/cron-scheduleEmailReminders.yml:8`

**修復方向**：兩個方向擇一或並用：(1) 在 emailReminderManager.ts 的預設分支，先無條件送出 SendGrid 的 POST SendGrid v3 scheduled_sends cancel，再標記 cancelled: true——立即取消與延後清理並不互斥，標記只是為了讓 cron 之後把記錄刪掉；(2) 把 scheduleEmailReminders.ts:34 的 deleteMany 移到取消區塊之後，並在 deleteMany 的 where 排除 cancelled: true，讓已過期但仍待取消的記錄至少還有被嘗試取消的機會。(1) 直接消滅破口，建議優先。

<details>
<summary>Suggestion（4）</summary>

#### F-002 cron 的取消查詢缺少 method: WorkflowMethods.EMAIL 篩選，會把 SMS 的 reminder 一起撈進來 — `packages/features/ee/workflows/api/scheduleEmailReminders.ts:44`

面向 F 資料取用與資料庫 · Suggestion

**問題**：同一個檔案裡其他三個 workflowReminder 查詢（第 34、82 行）都帶 method: WorkflowMethods.EMAIL，只有新增的這一個沒有。撈到的每一筆都會被當成 SendGrid batch 送去取消（第 61 行把 reminder.referenceId 直接當 batch_id），成功後刪除記錄。反證搜尋：grep 過全 repo 的 cancelled 寫入點，目前只有 emailReminderManager.ts:230 一處，smsReminderManager.ts:182 走的是直接 delete、從不寫 cancelled，所以現在還撈不到 SMS 記錄——這是一顆尚未引爆的地雷，不是正在發生的錯誤，故列 Suggestion 而非 Critical。但一旦 SMS 也採用同一套延後取消（這正是本 PR 建立的模式），這個 cron 就會拿 Twilio 的 message SID 去呼叫 SendGrid，並在失敗後把 SMS 記錄刪掉。

**證據**：
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:44`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:61`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:34`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:82`

**修復方向**：where 補上 method: WorkflowMethods.EMAIL，同時加 referenceId: { not: null } —— referenceId 為 null 時 batch_id 會是 null，SendGrid 必定回錯，而且會連帶觸發 F-004 的整批中斷。

#### F-003 取消 reminder 的呼叫一律沒有 await，原本收尾用的 await Promise.all 被一併移除 — `packages/features/bookings/lib/handleCancelBooking.ts:485`

面向 E 架構 · Suggestion

**問題**：五處都是在 forEach 裡呼叫 async function 而不 await，回傳的 Promise 沒有人持有。改動前這件事被兩層機制蓋住：handleCancelBooking 把 prisma.workflowReminder.deleteMany 收進 remindersToDelete 陣列，最後 await Promise.all(prismaPromises.concat(apiDeletes))；bookings.tsx 則有一行 await Promise.all(remindersToDelete)。這次把 DB 寫入搬進 deleteScheduled* 函式之後，兩處的 await 都被刪掉了（handleCancelBooking.ts:495 現在只剩 attendeeDeletes 與 bookingReferenceDeletes），等於整條取消路徑變成 fire-and-forget。後果有兩個：一是失敗完全不可觀察——兩個函式內部的 catch 只做 console.log，外面又沒人等，任何一次 DB 更新失敗都會安靜地留下一筆不會被取消的 reminder；二是 handler 可能在 cancelled: true 落地前就回應 200，在 serverless 部署（Next.js API route）上 request 結束後 runtime 可能被凍結。反證搜尋：handleCancelBooking 在 forEach 之後還有 await Promise.all(...) 與 await sendCancelledEmails(evt)，bookings.tsx 之後也還有多個 await，實務上這些 Promise 多半來得及完成，所以不把它列為 Critical；但「多半來得及」不是設計，而且它讓錯誤永遠不會浮現。

**證據**：
- `packages/features/bookings/lib/handleCancelBooking.ts:485`
- `packages/features/bookings/lib/handleCancelBooking.ts:495`
- `packages/trpc/server/routers/viewer/bookings.tsx:489`
- `packages/features/bookings/lib/handleNewBooking.ts:966`
- `packages/trpc/server/routers/viewer/workflows.tsx:377`
- `packages/trpc/server/routers/viewer/workflows.tsx:575`

**修復方向**：改成等待：await Promise.all(booking.workflowReminders.map((reminder) => reminder.method === WorkflowMethods.EMAIL ? deleteScheduledEmailReminder(reminder.id, reminder.referenceId) : deleteScheduledSMSReminder(reminder.id, reminder.referenceId)))。同時建議讓 deleteScheduled* 在內部 catch 之後把錯誤往外拋或回傳結果，否則即使 await 了，呼叫端仍然看不到失敗。

#### F-004 cron 取消迴圈沒有逐筆錯誤隔離，一次失敗會讓剩餘的取消與所有記錄刪除一起失效 — `packages/features/ee/workflows/api/scheduleEmailReminders.ts:53`

面向 F 資料取用與資料庫 · Suggestion

**問題**：try 包在整個迴圈外面（第 53 行），迴圈內第 57 行的 await client.request 一旦拋出，控制流直接跳到 catch，剩下的 reminder 這一輪完全不處理。更關鍵的是刪除記錄的 Promise.all 在迴圈之後（第 74 行）：拋出時它根本不會執行，所以在失敗點之前已經成功送出取消請求的那幾筆，記錄不會被刪掉——下一輪 cron 會對同一批 batch_id 再送一次取消。同檔案第 105 行起的排程迴圈是把 try/catch 放在迴圈內逐筆包住的，這個新迴圈沒有跟上同一個寫法。另外，把 Prisma 的 delete 收進陣列再 Promise.all，在一個本來就序列 await SendGrid 的迴圈裡沒有帶來任何平行化好處，只換來「失敗時全部丟失」這個副作用。

**證據**：
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:53`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:56`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:66`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:74`

**修復方向**：把 try/catch 移進迴圈逐筆包住，並在同一輪迭代裡直接 await prisma.workflowReminder.delete(...)；或成功一筆就把 id 收進陣列，迴圈結束後用一次 deleteMany({ where: { id: { in: ids } } })。前者較簡單，也讓「取消成功」與「刪掉記錄」保持成對。

#### F-006 immediateDelete 這個分支不刪除任何資料，而七個呼叫點對「誰負責清掉記錄」的假設各不相同且無註解 — `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:197`

面向 I 回溯分析 · Suggestion

**問題**：第一，名稱與行為不符。函式的三個分支中，!referenceId 分支（:203）刪除記錄、預設分支（:225）更新 cancelled，唯獨叫做 immediateDelete 的分支（:213）只送 SendGrid 取消然後 return，是三者中唯一什麼都不刪的。第二，呼叫點之間的保證不一致，而且要靠讀者自己推導：workflows.tsx:214（刪除 workflow）與 :521（刪除 step）之後分別有 workflow.deleteMany 與 workflowStep.delete，記錄會經由 schema.prisma:643 的 onDelete: Cascade 連帶消失，所以不刪是對的；但 handleNewBooking.ts:968 沒有這層 cascade——改期時舊 booking 只是被標成 CANCELLED、不會被刪除，那些記錄要一直留到 scheduleEmailReminders.ts:34 的 deleteMany 在 scheduledDate 過期後才清掉，中間會帶著 scheduled: true、cancelled: null 的狀態存在。三種生命週期共用同一個參數，沒有任何一行註解說明。第三，對照組：deleteScheduledSMSReminder（smsReminderManager.ts:177）沒有這個參數，一律立即取消並刪除記錄，兩個 manager 的契約因此不對稱。這個不一致我判斷是「刻意但沒寫下來」而非 bug（cascade 的存在是真的，已逐一驗證），但沒寫下來本身就是下一個維護者會踩的坑。

**證據**：
- `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:197`
- `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:213`
- `packages/features/bookings/lib/handleNewBooking.ts:968`
- `packages/trpc/server/routers/viewer/workflows.tsx:214`
- `packages/trpc/server/routers/viewer/workflows.tsx:521`
- `packages/trpc/server/routers/viewer/bookings.tsx:490`
- `packages/prisma/schema.prisma:643`
- `packages/features/ee/workflows/lib/reminders/smsReminderManager.ts:177`

**修復方向**：把參數改成描述行為的名稱（例如 cancelAtProviderNow），或乾脆拆成兩個 exported 函式（cancelScheduledEmailReminder / deleteScheduledEmailReminder），讓呼叫端從名字就看得出記錄會不會消失；並在函式上方用註解列出三個分支各自留下什麼狀態、由誰負責清理，特別點名 handleNewBooking 改期這條路徑依賴 cron 的過期清理。

</details>

<details>
<summary>Nit（5）</summary>

#### F-005 三個檔案把 import type 改回值匯入，其中 workflows.tsx 的 Prisma 完全沒有被使用 — `packages/features/bookings/lib/handleNewBooking.ts:1`

面向 A 風格 · Nit

**問題**：packages/config/eslint-preset.js:22 與 :35 把 unused-imports/no-unused-imports 與 @typescript-eslint/consistent-type-imports（prefer: type-imports）都設成 error，這是這個 repo 明確寫下來的慣例。handleNewBooking.ts 的 App、Credential、EventTypeCustomInput、Prisma 全部只出現在型別位置（:79 App["categories"]、:90 Credential 標註、:349 EventTypeCustomInput[]、:73/:776 Prisma.*），原本是 import type，這次被併進值匯入。workflows.tsx 更直接：PrismaPromise 只用於 :342 的型別註記，而新加進來的 Prisma（:9）在整個檔案裡一次都沒被使用，同時那一行也少了尾逗號。scheduleEmailReminders.ts:10 新增的 Prisma 與 WorkflowReminder 同樣只用於 :54 的型別註記，而且改從 @calcom/prisma/client 匯入，與同檔 :2 的 @prisma/client 來源不一致。反證搜尋：我確認過這不會擋 CI——只有 apps/web 有 lint script（packages/features 與 packages/trpc 的 package.json 都沒有 lint，turbo run lint 因此掃不到這些檔案），而 packages/tsconfig/base.json:13 的 noUnusedLocals 是 false，yarn type-check 也不會報未使用匯入。所以這是慣例偏離，不是 build 失敗，故列 Nit。

**證據**：
- `packages/features/bookings/lib/handleNewBooking.ts:1`
- `packages/trpc/server/routers/viewer/workflows.tsx:2`
- `packages/trpc/server/routers/viewer/workflows.tsx:9`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:10`
- `packages/config/eslint-preset.js:35`

**修復方向**：還原成 import type { App, Credential, EventTypeCustomInput, Prisma } from "@prisma/client" 並另起一行值匯入 enum；workflows.tsx 移除未使用的 Prisma、把 PrismaPromise 改回 import type 並補尾逗號；scheduleEmailReminders.ts:10 改成 import type，來源統一為 @prisma/client。

#### F-007 handleNewBooking 新增的 try/catch 在結構上捕捉不到任何東西 — `packages/features/bookings/lib/handleNewBooking.ts:964`

面向 B 簡潔 · Nit

**問題**：兩個理由各自獨立成立。其一，forEach 內的呼叫沒有 await，回傳的 Promise 在 try 區塊結束後才可能 reject，屆時 catch 早已離開作用範圍。其二，deleteScheduledEmailReminder 與 deleteScheduledSMSReminder 內部各自有一個包住整個函式主體的 try/catch（emailReminderManager.ts:202、smsReminderManager.ts:178），只做 console.log、不會往外拋。所以 :974 的 log.error 是永遠不會執行的一行程式碼，卻讓讀者以為這段有錯誤處理——這比沒有處理更容易誤導。

**證據**：
- `packages/features/bookings/lib/handleNewBooking.ts:964`
- `packages/features/bookings/lib/handleNewBooking.ts:973`
- `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:202`
- `packages/features/ee/workflows/lib/reminders/smsReminderManager.ts:178`

**修復方向**：與 F-003 同一個修法：改成 await Promise.all(...map(...)) 讓 try/catch 真的有作用；如果不打算等待，就把這個 try/catch 移除，讓「這裡是 fire-and-forget」變成明顯的事實。

#### F-008 cancelled 欄位可為 null 且沒有 default，一個布林旗標變成三態 — `packages/prisma/schema.prisma:644`

面向 F 資料取用與資料庫 · Nit

**問題**：schema 宣告成 Boolean?，migration 的 ADD COLUMN 也沒有 DEFAULT，所以既有資料一律是 NULL，之後新建的 reminder 也是 NULL。目前唯一的讀取點只查 cancelled: true（scheduleEmailReminders.ts:46），NULL 與 false 行為相同，所以現在不會出錯。但語意上「未取消」有兩個值，而 Prisma 的 cancelled: false 不會匹配 NULL：日後任何人寫出 where: { cancelled: false } 都會安靜地漏掉全部既有記錄，而且漏得沒有任何錯誤訊息。這正是 expand / migrate / contract 裡「backfill」那一步被跳過的典型樣子。

**證據**：
- `packages/prisma/schema.prisma:644`
- `packages/prisma/migrations/20230217230604_add_cancelled_to_workflow_reminder/migration.sql:2`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:46`

**修復方向**：改成 cancelled Boolean @default(false)，migration 寫 ALTER TABLE "WorkflowReminder" ADD COLUMN "cancelled" BOOLEAN NOT NULL DEFAULT false（新增欄位帶 default 對既有列是即時填值，不需要另外 backfill）。若刻意要保留 nullable，請在 schema 該行加註解寫明 NULL 代表什麼。

#### F-009 WorkflowStepContainer.tsx 的條件重構與本 PR 的 reminder 生命週期主題無關 — `packages/features/ee/workflows/components/WorkflowStepContainer.tsx:390`

面向 H 非 Python 檔 · Nit

**問題**：這段把外層條件從 (isPhoneNumberNeeded || isSenderIdNeeded) 收斂成 isPhoneNumberNeeded，並把原本巢狀在裡面的內容整段往上提。行為上我逐一確認過是安全的：被搬動的內容本來就整段包在 isPhoneNumberNeeded 判斷裡，舊條件唯一的額外效果是在「需要 sender id 但不需要電話號碼」時多渲染一個空的灰色方框；sender id 的輸入欄位位於下方另一個獨立的 div（:463 起），不在這次搬動的範圍內，因此不受影響。三個渲染分支（已驗證顯示 Badge、未驗證顯示驗證碼欄位、表單錯誤顯示紅字）都仍然存在。問題只在範圍：它與 workflow reminder 的取消／刪除完全無關，混在同一個 diff 裡會讓之後針對 reminder 行為做 bisect 或 revert 時連帶動到 UI。

**證據**：
- `packages/features/ee/workflows/components/WorkflowStepContainer.tsx:390`
- `packages/features/ee/workflows/components/WorkflowStepContainer.tsx:463`

**修復方向**：拆成獨立 commit（最低限度）或獨立 PR，讓這個 diff 只剩下 reminder 生命週期的改動。

#### F-010 取消語意跨七個呼叫點被改寫，沒有任何測試涵蓋 — `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:197`

面向 G 測試 · Nit

**問題**：grep 過 *.test.ts / *.spec.ts / *.e2e.ts，repo 內沒有任何涵蓋 workflowReminder 或 deleteScheduled* 的測試，apps/web/playwright 底下也沒有 workflow 相關的 e2e。這次改的是「什麼時候不要寄信」：預約流程本身照常通過，信照樣寄出去，happy path 上沒有任何訊號。列為 Nit 是因為這個模組本來就沒有測試基礎建設，要求本 PR 從零建立並不合比例；但這個行為的驗證成本會全部轉嫁到 production。

**證據**：
- `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:197`
- `packages/features/ee/workflows/lib/reminders/smsReminderManager.ts:177`
- `packages/features/ee/workflows/api/scheduleEmailReminders.ts:44`

**修復方向**：至少補一個整合測試：建立一筆 method=EMAIL、scheduled=true、referenceId 非空的 WorkflowReminder，跑取消預約流程，斷言該記錄的 cancelled 為 true；再呼叫一次 cron handler，斷言 SendGrid client 收到帶該 referenceId 的 cancel 請求且記錄被刪除。SendGrid client 以 mock 取代，但斷言要落在「送出了什麼請求」而不是「mock 回傳了什麼」。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 SendGrid 對「已經取消過的 batch」與「即將寄出的 batch」再送一次 POST SendGrid v3 scheduled_sends cancel，分別回什麼？

面向 F 資料取用與資料庫

**背景**：F-004 的重試行為完全取決於這個。scheduleEmailReminders.ts:56-64 對每一筆 cancelled 記錄送出取消，成功才刪記錄；若某一筆因為任何原因取消成功但記錄沒被刪掉（正是 F-004 描述的情境），下一輪會對同一個 batch_id 再送一次。如果 SendGrid 對重複取消回 4xx，那一筆就會永久卡住整個迴圈，之後所有被標記 cancelled 的 reminder 都不會再被取消。本機無法連外，無法驗證。

**如何確認**：SendGrid「Cancel Scheduled Sends」API 文件中關於重複取消與臨近寄送時間的規定；或在 staging 對一個已取消的 batch_id 再送一次取消、觀察 HTTP 狀態碼。

#### Q-002 Prisma.Prisma__WorkflowReminderClient 在 @prisma/client 4.8 產生出來的型別裡，真的掛在 Prisma namespace 底下嗎？

面向 H 非 Python 檔

**背景**：scheduleEmailReminders.ts:54 用了這個產生式的內部型別，是全 repo 唯一一處使用 Prisma__ 前綴型別的地方（已 grep 確認）。packages/prisma/client/index.d.ts 只是 re-export node_modules/.prisma/client，而這個 checkout 沒有安裝相依套件、執行環境也不得連外安裝，因此無法確認它是宣告在 namespace 內還是頂層。.github/workflows/check-types.yml 是 required check，若不在 namespace 內會直接編譯失敗。

**如何確認**：在有安裝相依套件的環境跑一次 yarn type-check；或直接改用公開型別 Prisma.PrismaPromise<WorkflowReminder>[]，就不再依賴產生器的內部命名（順帶也解決這個疑問）。

#### Q-003 production 與 self-host 實際上是用什麼排程觸發 apps/web/pages/api/cron/workflows/scheduleEmailReminders.ts？頻率多少？

面向 E 架構

**背景**：repo 裡唯一找得到的觸發來源是 .github/workflows/cron-scheduleEmailReminders.yml:8 的每 15 分鐘一次，而且那個 job 帶 if: env.APP_URL && env.CRON_API_KEY，secret 未設定時整個 step 會被跳過。這個頻率決定 F-001 破口的大小（破口存在與否不受影響——延後機制本身保證有破口，頻率只決定它有多寬）；GitHub Actions 的 schedule 在平台高負載時還會延遲數分鐘到數十分鐘，實際間隔可能遠大於 15 分鐘。

**如何確認**：production 部署的排程設定（是否改由其他 scheduler 觸發、頻率為何），以及 self-host 文件中對這兩個 secret 的說明；若 self-host 確實可能完全不跑 cron，F-001 在該情境下是無條件失效而非有破口。

</details>
