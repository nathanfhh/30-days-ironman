## 審查結論：Request Changes

> Critical 3 · Suggestion 7 · Nit 4 · 未驗證提問 2
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

- **A 風格**（未通過）：packages/trpc/server/routers/viewer/slots.ts:142 的變數叫 end，內容卻是 slotStartTime（F-004）；packages/lib/slots.ts:212-213 用 override.start.toString() 再交給 dayjs 解析（N-013）。兩者都是「名字或寫法與實際行為不符」，讀的人會被誤導。
- **B 簡潔**（未通過）：dayjs(date.start).add(utcOffset, "minutes") 在 slots.ts:110-123 同一個函式內重算五次（N-011）；另外 slots.ts:178-179、500、591-592 有與本次主題無關的空行增刪（N-014）。可用性判斷本身在 getSlots 與 checkIfIsAvailable 重複了一次，見 E。
- **E 架構**（未通過）：同一個「這個時段可不可以預約」的決策現在同時存在於 packages/lib/slots.ts（以「當日分鐘數」表示）與 packages/trpc/server/routers/viewer/slots.ts:102-151（以 Dayjs 絕對時刻表示），兩邊用不同的資料表示法各算一次，必須永遠一致才不會出錯——F-001 就是它們不一致時的後果。另外 organizerTimeZone 在產生 slot 與過濾 slot 兩階段的定義不同（F-010）。
- **F 資料取用與資料庫**（未通過）：F-002 讓有 date override 的日期完全跳過 busy 檢查，是預約資料完整性問題；F-003 是時區換算跨午夜後整天可預約時段消失；F-006 混用 local 模式與 utc 模式的 dayjs 做字串日期比對，結果依伺服器時區而異，正是 F 第 6 條所說「一個環境同意不等於驗證」的形態。
- **G 測試**（未通過）：新增的測試（apps/web/test/lib/getSchedule.test.ts:788-804）把邀請者換成 +6:00 之後斷言「時段完全一樣」，但組織者仍然是 Asia/Kolkata、查詢視窗也刻意對齊成同一段 UTC 區間，等於在驗證一個本來就不該變的量；真正被修的「組織者時區 → 邀請者時區的位移」沒有被覆蓋。checkIfIsAvailable 新增的兩段分支（date override、working hours）完全沒有測試，F-001 這種等級的迴歸現有測試抓不到（測試資料只有單一 working hours 區段，見 getSchedule.test.ts:92-106）。
- **H 非 Python 檔**（未通過）：diff 全部是 TypeScript，本維度適用。問題集中在 JS/TS 語意：Dayjs 物件用 === 比較（F-005）、TimeRange.timeZone 宣告成 optional 但呼叫端當必填用（F-008）、Array.prototype.find 被當布林用且 callback 沒有明確 return（N-012）。這三項本來應由 oxlint / tsc 攔下，但兩者在本次環境都沒有執行。
- **I 回溯分析**（未通過）：簽章相容性本身沒問題：checkIfIsAvailable 新增的四個參數都是 optional，TimeRange.timeZone 也是 optional，grep 過的三個呼叫點（slots.ts:488、513、581）與 getSlots 的四個呼叫端（trpc、兩個 TeamAvailabilityTimes、slots.test.ts）都不會編譯失敗。但「前置條件在各呼叫點不一致」這條不過：slots.ts:581 的呼叫點沒有帶 workingHours 與 dateOverrides，同一份 slot 在不同路徑上被套用不同的可用性規則（F-007）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 標題只說「date override 的時區」，但實際上包含兩件性質不同的事：(1) packages/lib/slots.ts 的 offset 換算修正，(2) 在 packages/trpc/server/routers/viewer/slots.ts:102-151 新增一整層 date override + working hours 的二次過濾。(2) 不是時區問題，而是在既有 getSlots 之後再加一道可用性判斷，行為影響遠大於標題所示，而且本次三個 Critical 全部出自 (2)。建議把 (2) 拆成獨立 MR，讓它自己帶測試與說明。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | 未安裝（不在 PATH 上）。本次沒有做相依套件漏洞、設定錯誤與 secret 掃描。 |
| opengrep | 略過 | 未安裝（不在 PATH 上），且 NCR_OPENGREP_RULES 指向的 semgrep rules 目錄不存在。本次沒有做 SAST 掃描。 |
| ruff | 略過 | ruff 已安裝並成功執行（exit 0，0 筆診斷），但本次 diff 全部是 TypeScript（.ts），ruff 只檢查 Python。這個 0 筆等於「沒有東西在掃描範圍內」，不是「掃過而且乾淨」，因此記為 skipped 以免被讀成覆蓋率。 · exit code 0 · in_diff 0、outside_diff 0 |
| ty | 略過 | 未安裝（不在 PATH 上）。同時本次是 TypeScript 專案，ty 本來也不適用。 |
| oxlint | 略過 | 未安裝（不在 PATH 上）。這是本次唯一能涵蓋 TypeScript 的 linter，它缺席代表本次變更沒有經過任何自動化 JS/TS 靜態檢查——例如 array-callback-return（見 N-012）本來應該由它攔下。 |
| tsc / TypeScript 型別檢查 | 略過 | checkout 內沒有 node_modules，執行環境也沒有網路可以安裝相依，因此完全沒有跑型別檢查。本報告中所有與型別有關的判斷（例如 F-008 的 timeZone optional）都是人工讀 .d.ts 得出的，沒有編譯器背書。 |
| jest / vitest（既有測試） | 略過 | 同樣因為沒有 node_modules 與網路，apps/web/test/lib/getSchedule.test.ts 與 slots.test.ts 都無法執行。本報告沒有任何一項結論來自實際跑過測試。 |
| codegraph | 略過 | 未安裝（不在 PATH 上）。呼叫關係與完整性（例如 checkIfIsAvailable 的所有呼叫點、getSlots 的所有呼叫端）全部改用 grep 逐一列舉。 |
| ncr-fresh-eyes（subagent） | 略過 | 這個執行環境沒有可派工的 Agent/Task 工具，無法派出 subagent。依 SKILL.md 的規定不自行模擬，如實記錄：本次沒有經過未被 checklist 框住的第一眼閱讀，findings 全部來自 dimension 檢查。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派出 subagent。本報告沒有經過獨立的品質複查，只經過 report_model.py 的機械驗證。 |

### Critical

#### F-001 checkIfIsAvailable 的 working hours 檢查量詞相反，一天有兩段（或跨 UTC 午夜）的班表會讓全部時段消失 — `packages/trpc/server/routers/viewer/slots.ts:138-151`

面向 E 架構 · Critical

**問題**：這段寫的是「只要**存在某一段** working hour 不包含這個 slot，就判定 slot 在工作時間外」，但正確的判準是「**沒有任何一段** working hour 包含這個 slot」。workingHours 是陣列，同一天可以有多筆：(1) 使用者在同一個星期幾設定兩段時間（例如 09:00-12:00 與 13:00-17:00），getAvailabilityFromSchedule（packages/lib/availability.ts:25-52）會產生兩筆 Availability，getWorkingHours 就會產生兩筆 days 相同的 WorkingHours；(2) 組織者的當地工作時間跨越 UTC 午夜時，packages/lib/availability.ts 的 overflow 分支（103-121 行）會再推一筆 days 位移一天的 WorkingHours，週一到週五的班表兩筆的 days 必然重疊。以 (1) 為例，10:00 的 slot 落在第一段內、卻落在第二段的 startTime 之前，find 在第二段回傳 true，於是第 150 行 return false，整天每一個 slot 都被砍掉。以 (2) 為例，America/Los_Angeles 的組織者設定週一到週五 09:00-18:00，getWorkingHours 產生 {days:[1..5], 960, 1439} 與 {days:[2..6], 0, 60}，任何星期二到星期五的 slot 都會被第二筆判為超出而被砍。反證檢查：getSlots 產生 slot 時用的是同一份 workingHours，所以這一層只會減少而不會補回 slot，上游沒有任何東西擋住這個結果；既有測試（apps/web/test/lib/getSchedule.test.ts:92-106）的測試資料只有單一一段 working hours、且 Asia/Kolkata 的 09:30-18:00 換算成 UTC 是 240-750 不會 overflow，所以測試綠燈不構成反證。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:138-151`
- `packages/lib/availability.ts:62-128`
- `packages/core/getUserAvailability.ts:210`

**修復方向**：把量詞倒過來，改成「沒有任何一段涵蓋就淘汰」，並且用 slotEndTime 算 end（見 F-004）：

```ts
const slotStartMinutes = slotStartTime.hour() * 60 + slotStartTime.minute();
const slotEndMinutes = slotEndTime.hour() * 60 + slotEndTime.minute();
const isWithinWorkingHours = workingHours.some((workingHour) => {
  if (!workingHour.days.includes(slotStartTime.day())) return false;
  return slotStartMinutes >= workingHour.startTime && slotEndMinutes <= workingHour.endTime;
});
if (workingHours.length && !isWithinWorkingHours) {
  // slot is outside of working hours
  return false;
}
```

另外請一併補上「slot 跨越 UTC 午夜」時 slotEndMinutes 會小於 slotStartMinutes 的情況（此時應改以絕對時刻或允許 endTime 溢位到 1440 以上比較）。並補一個測試：同一個星期幾兩段 working hours，斷言中間時段仍然可預約。

#### F-002 有 date override 的日期會提早 return true，完全跳過 busy 檢查，可能造成重複預約 — `packages/trpc/server/routers/viewer/slots.ts:102-135`

面向 F 資料取用與資料庫 · Critical

**問題**：第 113 行只要有任一筆 date override 的日期與 slot 同一天，就把 dateOverrideExist 設為 true。如果這個 slot 又落在該 override 的區間內，第 105-128 行的 if 不成立，控制流會走到第 133-135 行 `if (dateOverrideExist) { return true; }` —— 直接回傳可用，第 153 行開始的 `busy.every(...)` 整段被跳過。busy 來自 getBusyTimes：已存在的 booking 與外部行事曆的忙碌時段（packages/core/getUserAvailability.ts:143-152）。也就是說，在任何設有 date override 的日子，已經被預約走或行事曆上已佔用的時段仍會被回報為可預約。反證檢查：呼叫端沒有任何補償，slots.ts:488、513、581 三個呼叫點都直接把 checkIfIsAvailable 的布林結果當最終判斷；後續只剩 isTimeWithinBounds（slots.ts:592）做期間界線過濾，不看 busy。date override 的語意是「這一天改用這段時間」，不是「這一天忽略既有行程」，所以這不可能是刻意設計。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:102-135`
- `packages/trpc/server/routers/viewer/slots.ts:153-192`

**修復方向**：拿掉第 133-135 行的提早 return，讓 date override 的分支只負責「這個 slot 在不在 override 區間內」，不在就 return false、在就繼續往下跑 busy 檢查：

```ts
// 只保留「不在 override 區間內 → 不可用」
if (dateOverrides.some((date) => isSlotOutsideOverride(date, slotStartTime, slotEndTime, organizerTimeZone))) {
  return false;
}
// 不論當天有沒有 override，都必須通過 busy 檢查
return busy.every((busyTime) => { /* 既有邏輯 */ });
```

並補一個測試：某天同時有 date override 與一筆落在 override 區間內的 booking，斷言該時段不出現在結果中。

#### F-003 時區位移讓 date override 跨過邀請者當地午夜時 startTime > endTime，該日整天沒有任何可預約時段 — `packages/lib/slots.ts:210-246`

面向 F 資料取用與資料庫 · Critical

**問題**：新的換算把 override 的起訖各自轉成邀請者當地的「當日分鐘數」（hour()*60 + minute()），這個表示法沒有辦法表達跨日。具體情境：組織者時區 UTC，在日期 D 設定 09:00-17:00 的 date override，getUserAvailability 會存成 D 09:00Z / D 17:00Z（getUserAvailability.ts:215-224）；邀請者在 Asia/Shanghai（+480）。第 206 行的 activeOverrides 用絕對時刻比對，這筆 override 會被歸到邀請者的 D 日；offset = 480 - 0 = 480；startTime 變成 (D 17:00Z).hour()*60 = 1020，endTime 變成 (D+1 01:00Z).hour()*60 = 60。第 228-243 行已經先把當天正常的 working hours 從 computedLocalAvailability 移除，第 245 行接著推入這筆 {startTime:1020, endTime:60}。到 buildSlots 時，第 68 行的 `if (start >= end) continue` 讓這筆被整個丟掉，ranges 只剩一個空陣列，內層迴圈一次都不跑（第 85-100 行），結果是這一天回傳 0 個 slot。反證檢查：activeOverrides 只用 override.start 判定歸屬哪一天（第 206-208 行），所以被截掉的 17:00Z-01:00Z 尾段也不會出現在隔天，沒有其他地方補回來；沒有任何 wrap 處理或 guard。修改前同樣輸入會得到 {540, 1020}——時間是錯的，但至少有時段；改完之後變成整天空白，是行為上的退步。

**證據**：
- `packages/lib/slots.ts:210-246`
- `packages/lib/slots.ts:60-84`
- `packages/core/getUserAvailability.ts:215-224`

**修復方向**：不要在跨日時把 override 折進單日分鐘數。可行方向：換算後若 endTime <= startTime，就拆成兩段推入 computedLocalAvailability（當日 startTime→1439，以及隔天 0→endTime，隔天那段留給下一次 getSlots 迭代處理），或改成在 activeOverrides 過濾時就用邀請者當地的日界線切割 override 區間。最低限度也要在 packages/lib/slots.ts:225 之後加上防護，避免產出 start >= end 的區段被 buildSlots 靜默吞掉。請一併補測試：組織者 UTC、邀請者 Asia/Shanghai、override 09:00-17:00，斷言當天有時段而不是空陣列。

<details>
<summary>Suggestion（7）</summary>

#### F-004 變數 end 的算式與 start 完全相同，slot 的結束時間實際上沒有被檢查 — `packages/trpc/server/routers/viewer/slots.ts:141-143`

面向 A 風格 · Suggestion

**問題**：第 141 行與第 142 行是同一個算式 `slotStartTime.hour() * 60 + slotStartTime.minute()`，只是變數名一個叫 start 一個叫 end。第 143 行的 `end > workingHour.endTime` 因此等價於 `start > workingHour.endTime`，slot 的結束時間從頭到尾沒有參與判斷：一個在 endTime 前一分鐘開始、卻會延伸到 endTime 之後的 slot 不會被攔下。函式裡明明已經算好 slotEndTime（第 99 行），看起來是複製貼上時漏改。變數名說了一件事、程式做另一件事，是這裡最主要的成本。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:141-143`

**修復方向**：改成 `const end = slotEndTime.hour() * 60 + slotEndTime.minute();`。注意這會讓跨 UTC 午夜的 slot 得到比 start 小的 end，請與 F-001 的修正一起處理跨日情況。

#### F-005 用 === 比較兩個 Dayjs 物件，這個分支永遠不會成立 — `packages/trpc/server/routers/viewer/slots.ts:114-116`

面向 H 非 Python 檔 · Suggestion

**問題**：`dayjs(date.start).add(...)` 與 `dayjs(date.end).add(...)` 各自建立一個新的 Dayjs 實例，`===` 比較的是物件參考，兩個不同實例永遠不相等，所以第 115 行的 return true 是死碼。從上下文看，這一段想處理的是「start 與 end 相同、代表這一天整天不可預約」的 override。因為分支不成立，這種 override 會往下走到第 117-125 行：若 slot 剛好跨過那個瞬間，三個條件都不成立、find 回傳 undefined，接著 dateOverrideExist 為 true 讓函式在第 134 行回傳可用——與「整天不可預約」的意圖相反。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:114-116`

**修復方向**：改用 dayjs 的比較方法，並順手把重複算式抽出來（見 N-011）：

```ts
const overrideStart = dayjs.utc(date.start).add(utcOffset, "minutes");
const overrideEnd = dayjs.utc(date.end).add(utcOffset, "minutes");
if (overrideStart.isSame(overrideEnd)) {
  return true; // 整天不可預約
}
```

#### F-006 date override 的日期比對混用 local 模式與 utc 模式的 dayjs，結果隨伺服器時區而變 — `packages/trpc/server/routers/viewer/slots.ts:109-112`

面向 F 資料取用與資料庫 · Suggestion

**問題**：第 110 行的 `dayjs(date.start)` 是 local 模式，`.format("YYYY MM DD")` 會用執行程序的本地時區輸出年月日；第 111 行的 `slotStartTime` 來自第 100 行的 `time.utc()`，是 utc 模式，`.format` 用 UTC 輸出。兩個字串在伺服器 TZ 為 UTC 時一致，在其他 TZ 下就會在一天的頭尾各差一天，導致 date override 被判定套用到錯誤的日期（連帶讓 F-002 的提早 return 落在錯的日子）。同一函式的其他比較（isBefore / isSame / isAfter）比的是絕對時刻，不受模式影響，所以問題只出在這兩個 format 字串上——但也因此不會有任何一個現有測試在 CI（TZ=UTC）上失敗。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:109-112`
- `packages/trpc/server/routers/viewer/slots.ts:100`

**修復方向**：統一成 UTC 模式：`dayjs.utc(date.start).add(utcOffset, "minutes").format("YYYY MM DD")`。更穩妥的做法是不要比字串，改用 `.isSame(slotStartTime, "day")`（兩邊都在 utc 模式下）。另外建議在 jest/CI 設定固定 `TZ=UTC` 之外，額外跑一輪非 UTC 的 TZ，否則這類問題只會在部署環境現形。

#### F-007 第三個 checkIfIsAvailable 呼叫點沒有帶 workingHours 與 dateOverrides，同一批 slot 在不同路徑套用不同規則 — `packages/trpc/server/routers/viewer/slots.ts:581-586`

面向 I 回溯分析 · Suggestion

**問題**：grep 出的三個呼叫點中，前兩個用 `...schedule` / `...userSchedule` 展開 userAvailability 元素，因此連同 workingHours 與 dateOverrides 一起傳進去；第三個（selectedSlots / seats 路徑）只傳 `busy` 與 organizerTimeZone，兩個新參數落回預設值 `[]`，本次新增的整段邏輯在這條路徑上完全不生效。第三個呼叫點上方第 579 行才剛取出 `userSchedule`，卻只用它拿 timeZone，看起來是遺漏而非刻意。這個不一致本身不會讓程式壞掉（新參數都是 optional），但它讓「一個 slot 可不可預約」的答案取決於它走到哪一條路徑，之後修 F-001/F-002 時很容易只修到其中一邊。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:581-586`
- `packages/trpc/server/routers/viewer/slots.ts:488-493`
- `packages/trpc/server/routers/viewer/slots.ts:513-518`

**修復方向**：如果新的過濾應該一體適用，就把 userSchedule 一併展開：

```ts
return checkIfIsAvailable({
  time: slot.time,
  busy,
  ...availabilityCheckProps,
  workingHours: userSchedule?.workingHours,
  dateOverrides: userSchedule?.dateOverrides,
  organizerTimeZone: userSchedule?.timeZone,
});
```

如果是刻意不套用（例如已被 reserve 的 slot 不需要再驗工作時間），請在該呼叫點加一行註解說明理由，否則下一個維護者會當成 bug 修掉。

#### F-008 TimeRange.timeZone 宣告為 optional，但 packages/lib/slots.ts 當成必填使用，缺值時會靜默退回伺服器時區 — `packages/types/schedule.d.ts:1-6`

面向 H 非 Python 檔 · Suggestion

**問題**：schedule.d.ts 把 timeZone 加成 `timeZone?: string`，但 slots.ts:212 直接 `dayjs(...).tz(override.timeZone)`。dayjs timezone plugin 的 `.tz()` 在收到 undefined 時會退回預設時區（未呼叫 setDefault 時即為執行環境的本地時區），不會拋錯——也就是說少傳 timeZone 的呼叫端會得到一個看起來正常、實際上用伺服器時區算出來的 offset。目前 grep 到的四個 getSlots 呼叫端裡，只有 packages/trpc/server/routers/viewer/slots.ts:410-416 會傳 dateOverrides，而它一定會補上 timeZone，所以現在沒有實際壞掉；但 TeamAvailabilityTimes.tsx 這類呼叫端未來只要開始傳 dateOverrides 就會踩到，型別也不會攔。

**證據**：
- `packages/types/schedule.d.ts:1-6`
- `packages/lib/slots.ts:212`
- `packages/features/ee/teams/components/TeamAvailabilityTimes.tsx:35-41`

**修復方向**：兩選一。(a) 在 GetSlots 的 dateOverrides 型別上把 timeZone 收成必填（例如 `dateOverrides?: (DateOverride & { timeZone: string })[]`），讓型別檢查在呼叫端就擋下；(b) 保留 optional，但在 slots.ts:212 明確退回 getSlots 本來就有的必填參數：`dayjs(override.start).tz(override.timeZone ?? organizerTimeZone).utcOffset()`。(b) 較小且立刻可行。

#### F-009 新增的測試沒有覆蓋到這次修正的行為，checkIfIsAvailable 的兩段新分支則完全沒有測試 — `apps/web/test/lib/getSchedule.test.ts:788-804`

面向 G 測試 · Suggestion

**問題**：新測試把邀請者時區換成 Asia/Dhaka（+6:00），同時把查詢視窗也改成對齊同一段 UTC 區間，然後斷言時段與 UTC 版本「完全一樣」，註解也直說 it should return the same。這驗證的是「換個時區表示法不影響 UTC 結果」，而本次真正修的是組織者時區與邀請者時區之間的位移——組織者始終是 Asia/Kolkata，位移量沒有被改變過，所以就算把 packages/lib/slots.ts 的新算式改回舊算式，這個測試多半仍然會綠。另一方面，checkIfIsAvailable 新增的 date override 分支與 working hours 分支一行都沒有測試；測試資料（getSchedule.test.ts:92-106）只有單一一段 working hours，結構上也不可能觸發 F-001。

**證據**：
- `apps/web/test/lib/getSchedule.test.ts:788-804`
- `apps/web/test/lib/getSchedule.test.ts:92-106`
- `packages/trpc/server/routers/viewer/slots.ts:102-151`

**修復方向**：補三個案例：(1) 組織者與邀請者時區不同、且 override 換算後會落在不同的當地時刻——斷言時段落在換算後的位置而不是原本的 UTC 位置；(2) 同一個星期幾設定兩段 working hours，斷言中間的時段仍然可預約（對應 F-001）；(3) 某天同時有 date override 與一筆落在 override 內的既有 booking，斷言該時段不可預約（對應 F-002）。這三個都能在現有的 createBookingScenario 框架內完成。

#### F-010 organizerTimeZone 在產生 slot 與過濾 slot 兩個階段用了不同的定義 — `packages/trpc/server/routers/viewer/slots.ts:439-440`

面向 E 架構 · Suggestion

**問題**：第 439-440 行把 organizerTimeZone 提取成一個常數，優先序是 `eventType.timeZone || eventType?.schedule?.timeZone || userAvailability?.[0]?.timeZone`，並在第 456 行傳給 getTimeSlots；但第 492 與 517 行傳給 checkIfIsAvailable 的卻是每位使用者自己的 `schedule.timeZone`（即 getUserAvailability 回傳的 `schedule?.timeZone || eventType?.timeZone || user.timeZone`，優先序也不同）。只要 eventType.timeZone 有值，兩個階段就會用不同的時區做換算：第一階段依 eventType 時區產生 slot，第二階段依使用者時區判斷 date override 是否涵蓋該 slot，位移量不一致就會誤砍或誤放時段。既然這次剛好把常數提取出來了，正是統一的時機。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:439-440`
- `packages/trpc/server/routers/viewer/slots.ts:456`
- `packages/trpc/server/routers/viewer/slots.ts:492`
- `packages/trpc/server/routers/viewer/slots.ts:517`

**修復方向**：決定一個唯一的「組織者時區」定義並在兩個階段共用。若語意上以 event type 為準，就把第 492/517/585 行都改成傳第 439 行的 organizerTimeZone；若語意上以每位 host 為準，就讓 getTimeSlots 也吃各自的時區（多使用者時需要拆開處理）。無論選哪一個，請在 organizerTimeZone 宣告處加一行註解寫明選了哪一種語意。

</details>

<details>
<summary>Nit（4）</summary>

#### N-011 同一個 dayjs 換算式在一個函式內重複建構五次 — `packages/trpc/server/routers/viewer/slots.ts:110-123`

面向 B 簡潔 · Nit

**問題**：`dayjs(date.start).add(utcOffset, "minutes")` 在 slots.ts:110、114、118、119 出現四次，`dayjs(date.end).add(...)` 兩次；packages/lib/slots.ts:218-223 同樣把 `dayjs(override.start).utc().add(offset, "minute")` 寫了兩次、`override.end` 版本兩次。每次都建立新的 Dayjs 實例，除了可讀性之外，這是在每個 slot × 每個 override 的雙層迴圈裡執行的，數量不小。更重要的是重複的算式一旦其中一處改了、其他處沒改，就會出現靜默的不一致。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:110-123`
- `packages/lib/slots.ts:218-223`

**修復方向**：在 callback 開頭各抽一個 const（`overrideStart` / `overrideEnd`）出來重用，下面全部改用它們。

#### N-012 Array.prototype.find 被當布林條件用，且 callback 沒有明確的 return — `packages/trpc/server/routers/viewer/slots.ts:106-128`

面向 H 非 Python 檔 · Nit

**問題**：兩處都只在乎「有沒有」而不在乎「是哪一個」，語意上應該用 `.some()`；用 `.find()` 還多一個陷阱——若元素本身是 falsy 值，find 找到了也會被判為沒找到。另外兩個 callback 都只在 if 成立時 return true，其餘路徑落到函式結尾隱式回傳 undefined，這是 ESLint `array-callback-return` 會報的形態（本次環境沒有 oxlint 可以攔，見掃描狀況）。同時第 113 行在 find 的 predicate 裡設定外層變數 dateOverrideExist，是 predicate 帶副作用，讀者不容易預期。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:106-128`
- `packages/trpc/server/routers/viewer/slots.ts:139-148`

**修復方向**：改用 `.some()`，並讓每個分支都明確 `return true` / `return false`。dateOverrideExist 建議先用一次獨立的 `dateOverrides.some((d) => isSameDay(d, slotStartTime))` 算出來，不要藏在另一個 predicate 的副作用裡。

#### N-013 override.start.toString() 先轉字串再交給 dayjs 解析，多餘且依賴非標準的字串格式 — `packages/lib/slots.ts:212-213`

面向 A 風格 · Nit

**問題**：override.start 已經是 Date，dayjs 可以直接吃。`Date.prototype.toString()` 產生的是 `Thu Apr 20 2023 09:00:00 GMT+0000 (Coordinated Universal Time)` 這種非 ISO 格式，dayjs 的內建 parser 比對不到，會退回 `new Date(string)`，而該格式的解析在規範上是實作定義的。同一段程式在第 218-223 行又直接用 `dayjs(override.start)`，兩種寫法並存也讓人以為兩者有語意差別。

**證據**：
- `packages/lib/slots.ts:212-213`

**修復方向**：改成 `dayjs(override.start).tz(override.timeZone)` 與 `dayjs(override.start).tz(timeZone)`，與下方保持一致。

#### N-014 與主題無關的空行增刪讓 diff 多出雜訊 — `packages/trpc/server/routers/viewer/slots.ts:178-179`

面向 B 簡潔 · Nit

**問題**：diff 裡有三處純空行變動（busy.every 內移除一行空行、fixed host 區塊後新增一行空行、selectedSlots 區塊後移除一行空行），與時區修正沒有關係。單看無害，但會讓之後對這個檔案做 git blame 或 bisect 時多出一次無意義的命中。

**證據**：
- `packages/trpc/server/routers/viewer/slots.ts:178-179`
- `packages/trpc/server/routers/viewer/slots.ts:500`
- `packages/trpc/server/routers/viewer/slots.ts:591-592`

**修復方向**：把這三行還原，讓 diff 只剩下與 date override 時區有關的變更。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 checkIfIsAvailable 新增的這層 date override / working hours 過濾，是為了修哪一個具體情境？

面向 E 架構

**背景**：getSlots（packages/lib/slots.ts:135-257）產生 slot 時用的就是同一份 workingHours 與 dateOverrides，因此 checkIfIsAvailable 這一層只可能刪掉 slot、不可能補回 slot。從程式碼本身看不出它是在補一個 getSlots 漏掉的情境，還是純粹的防禦性重複檢查。這個答案會直接影響 F-001 的修法：若是防禦性，最乾淨的處置可能是整段移除而不是修正量詞。沒有 MR 描述、沒有關聯 issue，本次也無法執行測試來觀察兩層的差異。

**如何確認**：作者說明這段是針對哪一個回報的錯誤加的；或一個能重現「getSlots 產出了某個 slot、但它實際上不該可預約」的測試案例。

#### Q-002 `dayjs.tz(date.start, organizerTimeZone)` 傳入 Date 物件時，plugin 是把它當成該時區的牆上時間還是絕對時刻？在 DST 轉換當天兩者會給出不同的 utcOffset。

面向 F 資料取用與資料庫

**背景**：packages/trpc/server/routers/viewer/slots.ts:107 用這個結果乘以 -1 當作把組織者牆上時間換算成 UTC 的位移量。非 DST 轉換日兩種解讀得到同一個 offset，所以一般情況下結論相同；但正好落在轉換日的 date override 會差一小時。這個 checkout 沒有 node_modules，無法讀 dayjs timezone plugin 的實作來確認，也無法執行任何程式驗證，因此不填任何 severity。

**如何確認**：在有相依的環境跑一個單元測試：組織者時區取 America/New_York，date override 設在 DST 轉換當天，比對 utcOffset 與換算後的時刻是否符合預期；或直接讀 node_modules/dayjs/plugin/timezone.js 中 `dayjs.tz` 對非字串輸入的處理。

</details>
