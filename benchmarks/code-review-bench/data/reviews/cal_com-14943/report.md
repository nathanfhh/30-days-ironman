## 審查結論：Request Changes

> Critical 1 · Suggestion 4 · Nit 2 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ✅ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ✅ | ✅ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ✅ | ✅ |

- **A 風格**（未通過）：`gt: 1` 是沒有名字也沒有註解的門檻值，而且 retryCount 的語意與名稱差一（第一次嘗試失敗就變成 1）；新增的刪除行為也完全沒有留下紀錄。見 F-006、F-007。
- **F 資料取用與資料庫**（未通過）：新增的刪除條件會在事件仍有最多 7 天緩衝時永久刪掉 reminder（F-001）；retryCount 以 read-modify-write 遞增而非原子操作（F-002）；刪除條件未以 method 收斂且無 index 可用（F-003）；catch 內新增的 update 本身沒有保護，會中斷整輪迴圈（F-004）。
- **G 測試**（未通過）：新增的是會刪資料的分支，但沒有任何測試。packages/features/ee/workflows 底下目前沒有測試檔，不過 repo 內已有以 prismock 驗證 SMS lock 行為的測試，工具鏈是現成的。見 F-005。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：retryCount 加在三種 method 共用的 WorkflowReminder model 上（packages/prisma/schema.prisma:1000），但只有 SMS 這條 cron 使用。scheduleWhatsappReminders.ts 幾乎是這支 handler 的逐行複製版、同樣沒有重試上限，卻沒有一起處理；diff 與 commit message 也沒說明這是刻意的分階段導入。這不必然要在本 MR 補齊，但值得在描述裡寫清楚，見 Q-001。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 略過 | ruff 0.15.8 有安裝、對整個 repo 執行成功（exit 0、0 筆診斷），但本次 diff 只含 .ts / .sql / .prisma，ruff 不檢查其中任何一個檔案。這個 0 代表「沒有覆蓋」而不是「乾淨」，因此不記為 ok。 · exit code 0 · in_diff 0、outside_diff 0 |
| oxlint | 略過 | 未安裝（不在 PATH 上）。這是本次 diff 唯一適用的 JS/TS lint 工具，因此受審的 TypeScript 檔案沒有任何 lint 覆蓋。 · in_diff 0、outside_diff 0 |
| tsc / prisma validate | 略過 | checkout 沒有 node_modules，執行環境也沒有網路，無法安裝依賴。因此 TypeScript 型別檢查與 prisma schema 驗證都沒有執行——包含 scheduleSMSReminders.ts:60 那個 `as (PartialWorkflowReminder & { retryCount: number })[]` 型別斷言，以及 schema.prisma 與 migration.sql 是否一致，本次都只以人工比對確認（兩者皆為 Int / INTEGER NOT NULL DEFAULT 0，一致），沒有工具背書。 · in_diff 0、outside_diff 0 |
| ty | 略過 | 未安裝（不在 PATH 上）。本次 diff 沒有 Python 檔，即使安裝也不適用。 · in_diff 0、outside_diff 0 |
| trivy | 略過 | 未安裝（不在 PATH 上），略過相依套件漏洞、misconfiguration 與 secret 掃描。 |
| opengrep | 略過 | 未安裝（不在 PATH 上），且 NCR_OPENGREP_RULES 指向的規則目錄不存在。SAST 完全沒有執行。 |
| codegraph | 略過 | 未安裝，無法建立 symbol index。Phase 3 的呼叫者列舉與影響範圍分析全部改用 grep 完成（例如以 grep 確認全 repo 只有一處寫入 retryCount）。 |

### Critical

#### F-001 排程失敗兩次（約 30 分鐘）就永久刪除 reminder，而事件可能還有 7 天才發生 — `packages/features/ee/workflows/api/scheduleSMSReminders.ts:38-42`

面向 F 資料取用與資料庫 · Critical

**問題**：這條 cron 每 15 分鐘執行一次（cron-scheduleSMSReminders.yml:8），而 `scheduled: false` 的 WorkflowReminder 只在「送出時間超過 7 天之後」時才會被建立（smsReminderManager.ts:228-240）。也就是說，被這支 handler 撿到的 row，距離真正要送出還有最多 7 天、約 670 次執行機會。新的機制把可用次數壓到 2 次：第一次失敗 retryCount=1、第二次失敗 retryCount=2，下一輪開頭的 deleteMany（:38-42）就把整筆刪掉，而且該分支沒有任何 scheduledDate 條件，所以刪除與「是否已到期」無關。

問題在於會讓 retryCount 遞增的兩條路徑，絕大多數是暫時性或環境性的失敗，不是「這筆資料壞掉了」：(1) twilio.scheduleSMS 的第一行就是 createTwilioClient()（twilioProvider.ts:88），TWILIO_SID / TWILIO_TOKEN / TWILIO_MESSAGING_SID 少任何一個就直接 throw（:11-16），落進新的 catch → retryCount + 1。環境變數設定失誤或憑證輪替失敗的 30 分鐘內，7 天窗口內所有待排程 SMS reminder 會被清空。(2) Twilio API 短暫故障、或 DB 短暫不可用，同樣落在 catch。(3) 使用者或 team 被設為 SMSLockState.LOCKED 時 scheduleSMS 回傳 undefined（:90-94），走新增的 else → retryCount + 1；等 admin 透過 setSMSLockState 解鎖時，reminder 已經不存在了。

反證檢查（是否有東西會把刪掉的 reminder 補回來）：以 grep 掃過全 repo，`workflowReminder.create` 只出現在 emailReminderManager.ts、smsReminderManager.ts、whatsappReminderManager.ts，全部由 booking 建立／變更或 workflow 編輯觸發，沒有任何 cron 或補償流程會重建。所以一旦刪除，除非該 booking 被重新排程或該 workflow 被重新編輯，這則提醒就永遠不會送出，使用者與 attendee 都不會收到任何通知，log 裡也沒有任何一行說明刪了什麼。

**證據**：
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:38-42`
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:178-187`
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:189-198`
- `packages/features/ee/workflows/lib/reminders/providers/twilioProvider.ts:11-16`
- `packages/features/ee/workflows/lib/reminders/providers/twilioProvider.ts:88-94`
- `packages/features/ee/workflows/lib/reminders/smsReminderManager.ts:228-240`
- `.github/workflows/cron-scheduleSMSReminders.yml:8`

**修復方向**：把「停止重試」和「刪除資料」拆開，不要用刪除當作退避手段：
1. deleteMany 維持原本只刪 `method: SMS 且 scheduledDate <= now` 的語意（見 F-003 的拆法）；
2. 在 findMany 的 where 加上 `retryCount: { lt: MAX_SMS_SCHEDULE_ATTEMPTS }`，讓超過次數的 row 不再被撿取，row 本身留到過期後由原本的條件清掉——這樣既停止重試，也保留可觀測與補救的機會；
3. 更貼近實際的做法是讓重試預算跟「距離 scheduledDate 還剩多久」掛勾，而不是固定兩次（例如仍有 24 小時以上就繼續重試）；
4. 無論採哪一種，放棄前至少要 log 出 reminder id 與最後一次的 error，否則遺失沒有任何痕跡。

<details>
<summary>Suggestion（4）</summary>

#### F-002 retryCount 以 read-modify-write 遞增，兩輪重疊時會少算 — `packages/features/ee/workflows/api/scheduleSMSReminders.ts:48-60`

面向 F 資料取用與資料庫 · Suggestion

**問題**：`reminder.retryCount + 1` 用的是迴圈開始前那一次 findMany 讀出來的舊值。這支 handler 對每一筆 reminder 都要打 Twilio、查 profile、算 bookerUrl，一批資料多的時候單次執行超過 15 分鐘並非不可能；而觸發它的 GitHub Actions workflow 沒有設定 `concurrency`，上一輪還沒結束下一輪照樣送出請求。兩輪重疊時後寫入的那次會把另一次的 +1 蓋掉，計數偏低。Prisma 有原子操作可以讓這整段推理不必存在。

**證據**：
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:48-60`
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:184`
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:195`
- `.github/workflows/cron-scheduleSMSReminders.yml:1-23`

**修復方向**：改成 `data: { retryCount: { increment: 1 } }`（兩處：:184 與 :195），由資料庫端做遞增。

#### F-003 新的刪除分支沒有 method 限制，也讓整個 deleteMany 用不到 index — `packages/features/ee/workflows/api/scheduleSMSReminders.ts:29-45`

面向 F 資料取用與資料庫 · Suggestion

**問題**：這支 handler 的其他三個 query 都帶 `method: WorkflowMethods.SMS`，只有新增的 OR 分支沒有：它會刪掉「任何 method、任何 scheduled 狀態、任何 scheduledDate」只要 retryCount > 1 的 row。今天不會誤刪——以 grep 掃過全 repo，寫入 retryCount 的地方只有這個檔案（其餘出現位置是 schema、migration，以及 office365 CalendarService 內同名的區域變數），所以 EMAIL / WHATSAPP 的 row 恆為 0。但這個安全性來自「目前只有一個 writer」，不是來自 query 本身。scheduleWhatsappReminders.ts:22-31 幾乎是這支 handler 的逐行複製版，一旦它也採用同一個欄位，這支 SMS cron 就會開始靜默刪除 WHATSAPP 的 reminder。

同時，OR 把兩個不相關的述詞放在一起：WorkflowReminder 有 @@index([method, scheduled, scheduledDate]) 但沒有 retryCount 的 index（schema.prisma:1002-1006），所以第二個分支沒有索引可走，整個 deleteMany 每 15 分鐘掃一次全表。實際成本取決於資料量，見 Q-002。

**證據**：
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:29-45`
- `packages/features/ee/workflows/api/scheduleWhatsappReminders.ts:22-31`
- `packages/prisma/schema.prisma:1002-1006`

**修復方向**：拆成兩個各自帶 `method: WorkflowMethods.SMS` 的 deleteMany（或在 OR 的第二個分支補上 method），讓每個條件都能對到既有的 composite index；若採 F-001 的建議改用「不再撿取」而非刪除，這個分支會直接消失。

#### F-004 catch 內新增的 update 本身沒有保護，丟出例外會讓整輪剩下的 reminder 全部跳過 — `packages/features/ee/workflows/api/scheduleSMSReminders.ts:189-198`

面向 F 資料取用與資料庫 · Suggestion

**問題**：改動之前 catch 裡只有 console.log，任何一筆 reminder 失敗都不會影響同一輪的其他筆。現在 catch 裡多了一個沒有保護的 `await prisma.workflowReminder.update`。這個 update 自己也可能 throw：最典型的是 row 已被其他流程（例如 booking 取消時的 deleteScheduledSMSReminder）刪掉造成的 P2025，或者 catch 本來就是資料庫不可用觸發的——那麼同一個資料庫上的 update 也一樣會失敗。例外會直接離開 for 迴圈，由 defaultHandler 轉成 500（defaultHandler.ts:17-22），這一輪剩下的 reminder 全部不處理。等於把「單筆失敗」升級成「整輪失敗」。

**證據**：
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:189-198`
- `packages/lib/server/defaultHandler.ts:17-22`

**修復方向**：用 `updateMany`（找不到 row 時回傳 count 0 而不是 throw），或把這個 update 自己包一層 try/catch；另外把 `console.log(error)` 移到 update 之前，確保原始錯誤一定會被記錄下來。

#### F-005 新增的刪除路徑沒有任何測試 — `packages/features/ee/workflows/api/scheduleSMSReminders.ts:29-45`

面向 G 測試 · Suggestion

**問題**：這次新增的是會永久刪除資料的分支，而 packages/features/ee/workflows 底下目前沒有任何測試檔（以 find 確認）。門檻條件（`gt: 1` 對應「兩次嘗試」）正是最容易在後續重構中被改成 off-by-one 而沒人察覺的東西。repo 內已經有用 prismock 驗證 SMS lock 行為的測試（workflow-notifications.test.ts:177），代表工具鏈是現成的，不需要為此建立新的測試基礎設施。

**證據**：
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:29-45`
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:178-198`
- `packages/features/bookings/lib/handleNewBooking/test/workflow-notifications.test.ts:177`

**修復方向**：補三個 case 就足以釘住語意：(1) retryCount 達到門檻的 row 會被停止處理／刪除；(2) 未達門檻的 row 不受影響；(3) method 不是 SMS 的 row 不會被這支 cron 動到（同時也把 F-003 的行為釘死）。斷言要看實際的資料庫狀態，不要只斷言 res.status === 200。

</details>

<details>
<summary>Nit（2）</summary>

#### F-006 `gt: 1` 是沒有名字的門檻值，而 retryCount 的名稱與語意差一 — `packages/features/ee/workflows/api/scheduleSMSReminders.ts:38-42`

面向 A 風格 · Nit

**問題**：讀者要自己推導：第一次嘗試失敗時 retryCount 就變成 1，所以 `gt: 1` 實際代表「允許兩次嘗試」。欄位叫 retryCount，但第一次嘗試並不是 retry，語意上比較接近 failureCount，兩者差一——這正是之後有人「順手修正」成 `gte: 1` 或 `gt: 2` 而沒人發現的地方。

**證據**：
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:38-42`
- `packages/prisma/schema.prisma:1000`

**修復方向**：抽成具名常數並用相等語意表達，例如 `const MAX_SMS_SCHEDULE_ATTEMPTS = 2;` 搭配 `retryCount: { gte: MAX_SMS_SCHEDULE_ATTEMPTS }`，或至少加一行註解說明「retryCount 在第一次失敗後即為 1」。

#### F-007 刪除是靜默的，事後無法回答「哪些提醒被放棄了」 — `packages/features/ee/workflows/api/scheduleSMSReminders.ts:29-45`

面向 A 風格 · Nit

**問題**：deleteMany 只回傳筆數而且這裡連筆數都沒接，所以「某位使用者的提醒為什麼沒送出」在事後完全無跡可循。唯一的線索是 :198 的 `console.log`（既有程式碼，不歸這次改動），但它不含 reminder id、也沒有走 repo 內其他地方使用的 logger（twilioProvider.ts:9 有 `logger.getSubLogger({ prefix: ["[twilioProvider]"] })` 的既有慣例）。

**證據**：
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:29-45`
- `packages/features/ee/workflows/api/scheduleSMSReminders.ts:198`
- `packages/features/ee/workflows/lib/reminders/providers/twilioProvider.ts:9`

**修復方向**：刪除前先以 findMany 取出要刪的 id 並記錄，或改用 F-001 建議的「不再撿取」做法並在跨過門檻的那一次寫一筆 warn；同時把 :198 的訊息補上 `reminder.id`。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 WhatsApp 與 Email 兩支 cron 是刻意不納入這次範圍，還是漏了？

面向 E 架構

**背景**：retryCount 加在三種 method 共用的 WorkflowReminder model 上，但只有 SMS 使用。scheduleWhatsappReminders.ts:22-52 與這支 handler 幾乎逐行相同、同樣會無上限重試；scheduleEmailReminders.ts 走 getAllUnscheduledReminders 也沒有上限。diff、commit message 與分支名稱都沒有交代這是分階段導入還是遺漏，從程式碼本身無法判定。

**如何確認**：PR 描述或對應 issue 說明是否為刻意的 SMS 先行；或作者確認是否已有 WhatsApp/Email 的 follow-up。

#### Q-002 這個每 15 分鐘一次的 deleteMany 在 production 的實際成本是多少？

面向 F 資料取用與資料庫

**背景**：OR 的第二個分支（retryCount > 1）沒有可用的 index（schema.prisma:1002-1006 只有 bookingUid、workflowStepId、seatReferenceId、[method, scheduled, scheduledDate]、[cancelled, scheduledDate]），因此整個 deleteMany 無法只靠既有 composite index 完成。這個環境沒有資料庫、也沒有 production 的資料量，無法判斷這是可忽略的成本還是每 15 分鐘一次的全表掃描。

**如何確認**：在 production（或同等資料量的 staging）對這個 where 條件跑一次 EXPLAIN ANALYZE，或提供 WorkflowReminder 的 row count 量級。

</details>
