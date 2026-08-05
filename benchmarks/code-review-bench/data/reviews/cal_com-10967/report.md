## 審查結論：Request Changes

> Critical 4 · Suggestion 8 · Nit 2 · 未驗證提問 3
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
| ❌ | ✅ | ❌ |

- **A 風格**（未通過）：多處命名與實際行為不符：filter 之後仍叫 bookingCalendarReference / calendarReference 單數（handleCancelBooking.ts:418、EventManager.ts:479），mainHostDestinationCalendar 實際只是陣列第 0 個元素而非「主 host」。另有孤兒註解與 enum／字面字串混用。見 F-013、F-014。
- **B 簡潔**（未通過）：同一段「找不到 credential 就進 DB 撈並手工映射欄位」的程式碼在 EventManager.ts:344、EventManager.ts:517、handleCancelBooking.ts:431 幾乎逐字重複三次；`destinationCalendar ? [x] : y ? [y] : []` 的三元階梯散落 7 個檔案。另有迴圈內重複刪除的多餘外部 API 呼叫。見 F-008、F-011。
- **D API 慣例**（未通過）：沒有新增或變更 HTTP 路由、verb 或驗證 schema。但對外 webhook payload 的 destinationCalendar 欄位形狀由物件/null 改為陣列，且不同事件仍不一致（BOOKING_CREATED 仍送 null、BOOKING_REJECTED 改送 []），對第三方消費端是無預警的破壞性契約變更。見 F-009。
- **E 架構**（未通過）：Calendar 介面新增了必填的 credentialId 參數（packages/types/Calendar.d.ts:221），但 14 個實作中只有 Google 真的用它做 per-credential 選擇，其餘一律取 destinationCalendar[0]，多目標情境在非 Google 服務上不成立。另外 EventManager 開始自己下 prisma 查 credential，把原本由呼叫端組裝憑證的責任搬進來，且與姊妹區塊的防呆寫法不一致。見 F-006、F-007、F-010。
- **F 資料取用與資料庫**（未通過）：destinationCalendar 的形狀原地變更，沒有 expand → migrate → contract；DestinationCalendar.credentialId 在 schema 是 nullable（packages/prisma/schema.prisma:159），既有 null 資料在新邏輯下會落到 primary；booking reference 的 externalCalendarId 在部分路徑不再被寫入；迴圈內逐筆 findUnique 造成 N+1。見 F-003、F-004、F-005、F-011。
- **G 測試**（未通過）：橫跨 22 個檔案、改動 booking 建立／更新／取消三條主要路徑的重構，測試面唯一的變動是 apps/web/playwright/webhook.e2e.ts:249 把一個期望值從 null 改成 []。多目標行事曆 fan-out、空 destinationCalendar 的 Google Meet 退回路徑（正是 F-001 壞掉的地方）、legacy 無 credentialId 的 destinationCalendar，都沒有任何測試覆蓋。見 F-012。
- **I 回溯分析**（未通過）：destinationCalendar 由物件改為陣列後，既有呼叫點的隱含輸入契約被打破：EventManager.ts:119 少了 optional chaining 會直接丟 TypeError；Google 的 updateEvent/deleteEvent 改寫後的 find 條件在該分支永遠不成立，等於死碼。見 F-001、F-002。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 除了 destinationCalendar 陣列化之外，還夾帶了數項無關變更：packages/trpc/server/routers/viewer/organizations/create.handler.ts:151 的 IS_TEAM_BILLING_ENABLED 條件改寫（見 F-003）、packages/features/bookings/lib/handleNewBooking.ts:724 的 loadUsers 全面改寫（見 F-010）、orginalBookingDuration 拼字修正、"organiser" → "organizer" 字串修正。22 個檔案的跨層重構本身已經很難審，夾帶無關變更正是 F-003 這種行為反轉能夠混進來的原因。建議把無關項目拆成獨立 MR。
- **該在這個時機做？**：這是對 CalendarEvent.destinationCalendar 的原地破壞性形狀變更（packages/types/Calendar.d.ts:171），同時也改變了對外 webhook payload 的形狀（見 F-009），但沒有走 expand → migrate → contract：新舊形狀沒有並存期，消費端也沒有過渡窗口。加上幾乎沒有對應測試（見 F-012），一次進版的風險集中度偏高。這個取捨屬於人的決定，此處只是把它攤開。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | 未安裝（preflight 回報 trivy 不在 PATH），本次未進行相依套件漏洞、設定錯誤與 secret 掃描。 |
| opengrep | 略過 | 未安裝，且 NCR_OPENGREP_RULES 未設定、預設的 Semgrep 規則目錄也不存在，本次未進行 SAST 掃描。 |
| ruff | 略過 | ruff 已安裝且執行成功（exit 0），但整個 repository 沒有任何 .py 檔案（ruff 輸出 "No Python files found under the given path(s)"），等同沒有掃到任何東西。記為 skipped 而非 ok，避免讓人誤以為有一次乾淨的 Python 檢查。 · exit code 0 |
| ty | 略過 | 未安裝；本專案也沒有 Python 檔案，即使安裝亦無適用範圍。 |
| oxlint | 略過 | 未安裝。這是本次最痛的缺口：diff 全是 TypeScript/TSX，唯一可用的 linter（ruff）對 TS 完全不適用，因此本報告的 TS 靜態檢查覆蓋率為零。 |
| tsc / type-check | 略過 | checkout 沒有 node_modules 且環境無網路，無法安裝相依套件，所以無法執行 tsc、無法跑任何測試。本報告所有型別與執行期主張皆由人工閱讀原始碼推得，未經編譯器或測試驗證。 |
| codegraph | 略過 | 未安裝，Phase 3 的呼叫點列舉與完整性確認全部改以 grep 完成。 |
| ncr-fresh-eyes (subagent) | 略過 | 本執行環境沒有可派發 subagent 的工具（無 Agent/Task tool），fresh eyes 無法派出。依 SKILL.md 規定不得由主 agent 自行模擬，故此步驟缺席：本報告缺少一次未被 skill 框架塑形的獨立觀察。 |
| ncr-quality-check (subagent) | 略過 | 同上，無法派發 subagent。報告 JSON 僅通過 report_model.py 的機械驗證，未經獨立品質複查。 |

### Critical

#### F-001 EventManager.create 少了 optional chaining，Google Meet 且沒有 destinationCalendar 時整個 booking 會丟 TypeError — `packages/core/EventManager.ts:118`

面向 I 回溯分析 · Critical

**問題**：改動前是 `evt.destinationCalendar?.integration !== "google_calendar"`：destinationCalendar 為 null 時 optional chaining 回 undefined，條件成立，location 退回 integrations:daily（Cal Video）。改動後拆成 `const [mainHostDestinationCalendar] = evt.destinationCalendar ?? []` 再直接讀 `.integration`，陣列為空時 mainHostDestinationCalendar 是 undefined，讀取屬性直接丟 TypeError。這條路徑是可達的：handleNewBooking.ts:1063 在 eventType 與 organizer 都沒有 destinationCalendar 時明確給 null，confirm.handler.ts / handleCancelBooking / 兩支 payment webhook 則給 []，而 eventManager.create 的五個呼叫點（handleNewBooking.ts:2114、handleConfirmation.ts:47、webhook.ts:243/332、paypal-webhook.ts:170）全都會餵進這兩種值。反證我找過了：apps/web/components/eventtype/EventSetupTab.tsx:345 對「選了 Google Meet 但沒有 Google 行事曆」只顯示一段提示文字，不是硬性阻擋；而且這行程式碼上方的註解本來就寫著 "Fallback to Cal Video if Google Meet is selected w/o a Google Cal"——它存在的唯一理由就是這個狀態會發生。也就是說，這個守衛現在正好在它要處理的情境下崩潰，使用者拿到的是 500 而不是 Cal Video。

**證據**：
- `packages/core/EventManager.ts:118`
- `packages/core/EventManager.ts:119`
- `packages/features/bookings/lib/handleNewBooking.ts:1063`
- `packages/features/bookings/lib/handleNewBooking.ts:2114`

**修復方向**：把第 119 行改回 optional chaining：`if (evt.location === MeetLocationType && mainHostDestinationCalendar?.integration !== "google_calendar")`。順帶考慮語意：陣列化之後「有沒有任何一個 destinationCalendar 是 google_calendar」可能才是正確的判斷，例如 `!evt.destinationCalendar?.some((cal) => cal.integration === "google_calendar")`；若確定只看第一個，請把第 117 行的 @NOTE 說明為什麼。另外建議補一個單元測試：destinationCalendar 為 null / [] 且 location 為 MeetLocationType 時，location 應退回 integrations:daily。

#### F-002 Google updateEvent / deleteEvent 的 fallback 條件恆不成立，更新會送出 calendarId: undefined、刪除會靜默改刪 primary — `packages/app-store/googlecalendar/lib/CalendarService.ts:254`

面向 I 回溯分析 · Critical

**問題**：兩處都寫成 `externalCalendarId ? externalCalendarId : event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId`。三元的 else 分支只在 externalCalendarId 為 falsy（undefined / null / 空字串）時才會被求值，而此時 find 的判斷式就是 `cal.externalId === undefined`；DestinationCalendar.externalId 在 schema 是必填 String（packages/prisma/schema.prisma:153），永遠不可能等於 undefined。所以整個 else 分支是死碼，結果恆為 undefined。後果分兩邊：updateEvent 直接把 `calendarId: undefined` 丟給 calendar.events.update，Google API 呼叫失敗；deleteEvent 因為下面還有 `calendarId ? calendarId : defaultCalendarId`，會靜默退回 "primary"，於是事件其實建立在指定行事曆上、刪除卻打到 primary，行事曆上留下永遠刪不掉的孤兒事件，而 cal.com 這端看起來是成功的。改動前這裡是 `event.destinationCalendar?.externalId`，是有意義的 fallback。反證找過：booking reference 的 externalCalendarId 並非總是有值——這正是 F-004 描述的路徑，以及 refactor 之前建立的舊 booking（handleCancelBooking.ts:472 的註解自己就說「For bookings made before the refactor」），所以 else 分支確實會被走到。

**證據**：
- `packages/app-store/googlecalendar/lib/CalendarService.ts:254`
- `packages/app-store/googlecalendar/lib/CalendarService.ts:256`
- `packages/app-store/googlecalendar/lib/CalendarService.ts:315`
- `packages/app-store/googlecalendar/lib/CalendarService.ts:317`

**修復方向**：陣列化之後這裡要找的是「這個 credential 對應的行事曆」，不是拿 externalCalendarId 去比對它自己。建議與 createEvent 一致，改用 credentialId 比對，並保留原本的第一順位 fallback，例如：`const selectedCalendar = externalCalendarId || event.destinationCalendar?.find((cal) => cal.credentialId === this.credential.id)?.externalId || event.destinationCalendar?.[0]?.externalId;`（credentialId 需要像 createEvent 一樣從介面傳入或從 service 的 credential 取得）。updateEvent 也應該補上 "primary" 的最終 fallback，避免送出 undefined。

#### F-003 organizations/create.handler.ts 的 IS_TEAM_BILLING_ENABLED 判斷被反轉，且與本 MR 主題無關 — `packages/trpc/server/routers/viewer/organizations/create.handler.ts:151`

面向 F 資料取用與資料庫 · Critical

**問題**：原本是 `...(!IS_TEAM_BILLING_ENABLED && { slug })`：billing 關閉（多見於 self-hosted）時直接給定 slug；billing 開啟時不給 slug，改在 metadata 放 requestedSlug，等付款流程完成再落地。commit db92960（"fix change from main spread operator"）把 `&&` 改寫成三元時把 `!` 弄丟了，變成 `...(IS_TEAM_BILLING_ENABLED ? { slug } : {})`。結果整個反過來：billing 開啟時 slug 與 requestedSlug 同時被寫入（付款前就佔用了 slug，繞過原本的等待機制）；billing 關閉時 slug 與 requestedSlug 都不寫，建立出來的 organization 沒有 slug，也就沒有 requestedSlug 可供後續補救。metadata 那一行的 `!` 本來就不存在、改寫後語意不變，兩行放在一起看更能確認第 151 行是筆誤而非有意設計。旁證：packages/trpc/server/routers/viewer/teams/create.handler.ts:69 同樣用 `...(isOrgChildTeam ? { slug } : {})` 的三元寫法，可見團隊確實在做這種形式轉換，只是這一處把條件寫反了。這個檔案與 destinationCalendar 陣列化毫無關係。

**證據**：
- `packages/trpc/server/routers/viewer/organizations/create.handler.ts:151`
- `packages/trpc/server/routers/viewer/organizations/create.handler.ts:153`

**修復方向**：改回 `...(!IS_TEAM_BILLING_ENABLED ? { slug } : {})`，第 153 行維持不變。更好的做法是把這個修正（連同其他無關變更）從本 MR 拆出去獨立提交，讓它有機會被單獨審查與回滾。

#### F-004 destinationCalendar 沒有 credentialId 時，事件會被建到 primary，且 booking reference 不再記錄 externalCalendarId — `packages/core/EventManager.ts:380`

面向 F 資料取用與資料庫 · Critical

**問題**：createAllCalendarEvents 的兩個分支對 externalId 的處理不一致：有 credentialId 時是 `createEvent(credential, event, destination.externalId)`（EventManager.ts:370），沒有 credentialId 時卻是 `createEvent(c, event)`（EventManager.ts:380），第三個參數漏了。CalendarManager.createEvent 把該參數原樣放進 EventResult.externalId，而 EventManager.ts:169 現在改用 `externalCalendarId: isCalendarType ? result.externalId : undefined` 來寫 booking reference——改動前這裡取的是 `evt.destinationCalendar?.externalId`，即使在這條分支也有值。所以只要 destinationCalendar.credentialId 為 null，這筆 booking 的 reference 就會少掉 externalCalendarId。這不是假設性的：credentialId 在 schema 是 `Int?`（packages/prisma/schema.prisma:159），而且程式碼自己就為此分岔（EventManager.ts:341 的 `if (destination.credentialId)`）——如果它永遠有值，這個 else 分支根本不會存在。同一批資料在 Google 端還會再中一次：GoogleCalendarService.createEvent 現在用 `find((cal) => cal.credentialId === credentialId)` 選行事曆（CalendarService.ts:147），credentialId 為 null 時比對不到，`selectedCalendar || "primary"` 讓事件建到 primary，而不是使用者設定的 destinationCalendar.externalId（改動前是直接用 externalId）。兩者疊起來的結果是：事件建錯行事曆 → reference 沒有 externalCalendarId → 之後的更新／刪除又落回 F-002 的 primary 路徑，錯誤會一路傳遞下去。

**證據**：
- `packages/core/EventManager.ts:380`
- `packages/core/EventManager.ts:370`
- `packages/core/EventManager.ts:169`
- `packages/app-store/googlecalendar/lib/CalendarService.ts:147`
- `packages/prisma/schema.prisma:159`

**修復方向**：兩件事。(1) EventManager.ts:380 補上 externalId：`await createEvent(c, event, destination.externalId)`。(2) GoogleCalendarService.createEvent 的選擇邏輯加上 credentialId 為空時的退路，例如先用 credentialId 比對，比不到再退回 `calEventRaw.destinationCalendar?.[0]?.externalId`，最後才是 "primary"；這樣既支援多目標，又不會讓既有資料悄悄改變行為。另外建議寫一個資料檢查（或一次性 migration）把 DestinationCalendar.credentialId 為 null 的既有列補齊，讓新邏輯有一致的前提——參見 Q-003。

<details>
<summary>Suggestion（8）</summary>

#### F-005 evt.destinationCalendar?.push(...) 在 null 時靜默不執行，團隊成員的行事曆會被丟掉 — `packages/features/bookings/lib/handleNewBooking.ts:1063`

面向 F 資料取用與資料庫 · Suggestion

**問題**：evt.destinationCalendar 在 eventType 與 organizer 都沒有設定 destinationCalendar 時是 null（handleNewBooking.ts:1063 的三元階梯最後一項）。緊接著第 1078 行用 `evt.destinationCalendar?.push(...teamDestinationCalendars)` 把團隊成員的行事曆推進去——optional chaining 在 null 時整句是 no-op，沒有例外、沒有 log，teamDestinationCalendars 就消失了。這正好命中本 MR 想修的情境：一個 COLLECTIVE 團隊活動，活動本身沒設「加入行事曆」、organizer 也沒設個人 destinationCalendar，但成員各自有——這種設定下團隊成員的行事曆依然拿不到事件，流程會落回 createAllCalendarEvents 的「用第一個已連結行事曆」fallback（EventManager.ts:384），也就是修完之後行為沒變。另一個側面是形狀不一致：這是全 codebase 唯一還用 null 當空值的地方，其他 7 個建構點（confirm.handler.ts:175、handleCancelBooking.ts:251/528、webhook.ts:119/229、paypal-webhook.ts:152、editLocation.handler.ts:85 等）一律用 []。

**證據**：
- `packages/features/bookings/lib/handleNewBooking.ts:1063`
- `packages/features/bookings/lib/handleNewBooking.ts:1077`
- `packages/features/bookings/lib/handleNewBooking.ts:1078`

**修復方向**：把第 1063 行的最後一項從 `: null` 改成 `: []`，並把第 1078 行改成不依賴 optional chaining 的寫法，例如在建構 evt 之前先算好完整陣列：`const destinationCalendars = [ ...(eventType.destinationCalendar ? [eventType.destinationCalendar] : organizerUser.destinationCalendar ? [organizerUser.destinationCalendar] : []), ...(isTeamEventType && eventType.schedulingType === "COLLECTIVE" ? teamDestinationCalendars : []) ];` 再指派給 evt.destinationCalendar。這樣同時解掉「null 靜默吞掉」與「全 repo 空值形狀不一致」兩件事。

#### F-006 Calendar.createEvent 新增的 credentialId 只有 Google 實作，其餘 13 個 CalendarService 仍固定取 destinationCalendar[0] — `packages/types/Calendar.d.ts:221`

面向 E 架構 · Suggestion

**問題**：介面把 credentialId 宣告為必填第二參數，但 repo 內共有 14 個 createEvent 實作（app-store 底下十三個 CalendarService.ts，加上 packages/lib/CalendarService.ts 的 BaseCalendarService），只有 GoogleCalendarService 讀取它。TypeScript 允許實作少宣告參數，所以編譯不會擋。實際後果是多目標 fan-out 只有 Google 成立：EventManager 會為 N 個 destinationCalendar 各叫一次 createEvent，但 Office365 每一次都用 `event.destinationCalendar[0].externalId` 去組 Graph 的 `me/calendars/{externalId}/events` 路徑（CalendarService.ts:75），而 fetcher 綁的是各自的 credential——第二個 host 的 token 去打第一個 host 的 calendar id，得到的是 404/403；Lark（CalendarService.ts:129）與 CalDAV（packages/lib/CalendarService.ts:156）同樣只認第 0 個。也就是說，一個雙 Office365 host 的 COLLECTIVE 活動，新功能不僅沒生效，還會多產生一次失敗的外部呼叫。

**證據**：
- `packages/types/Calendar.d.ts:221`
- `packages/app-store/googlecalendar/lib/CalendarService.ts:147`
- `packages/app-store/office365calendar/lib/CalendarService.ts:73`
- `packages/app-store/larkcalendar/lib/CalendarService.ts:128`
- `packages/lib/CalendarService.ts:156`

**修復方向**：短期：在介面上把 credentialId 標為明確的契約並在每個實作補上「比對 credentialId，比不到就退回 [0]」的共用 helper（例如在 packages/lib 加一個 `pickDestinationCalendar(event, credentialId)`），至少讓行為一致且可預期。若這一輪只打算支援 Google，請在 packages/types/Calendar.d.ts:221 的介面上用註解寫清楚「目前僅 GoogleCalendarService 實作 per-credential 選擇，其餘實作忽略此參數並使用第一個 destinationCalendar」，並在 EventManager 對非 Google 的 credential 避免重複 fan-out，免得產生註定失敗的呼叫。

#### F-007 updateAllCalendarEvents 在 DB 也撈不到 credential 時仍把 undefined 丟進 updateEvent，與姊妹區塊的防呆不一致 — `packages/core/EventManager.ts:517`

面向 E 架構 · Suggestion

**問題**：新增的 DB 補撈區塊只在 `credentialFromDB && credentialFromDB.app?.slug` 都成立時才指派 credential（EventManager.ts:529）。credential 不存在、或 credential 沒有對應的 app slug 時，credential 維持 undefined，第 542 行仍無條件 `result.push(updateEvent(credential, event, bookingRefUid, calenderExternalId))`。updateEvent 內部會對它呼叫 getCalendar 並讀取 credential.appName，於是丟出 TypeError；由於這裡是 push 未 await 的 promise，而函式結尾是 `return Promise.all(result)`（return 的 rejection 不會被同一個 try/catch 攔下），這個錯誤會直接往上冒，繞過本函式底下那個專門用來降級回傳的 catch。對照組就在同一個檔案：createAllCalendarEvents 對完全相同的情境有 `if (credential) { ... }` 保護（EventManager.ts:369）。兩段幾乎逐字複製的程式碼只有一段有防呆，這種不一致比兩段都沒有更容易被忽略。

**證據**：
- `packages/core/EventManager.ts:517`
- `packages/core/EventManager.ts:529`
- `packages/core/EventManager.ts:542`
- `packages/core/EventManager.ts:369`

**修復方向**：把第 542 行包進 `if (credential)`，並在 else 分支留下 log（例如 `log.error("updateAllCalendarEvents: credential not found", { credentialId: reference.credentialId })`），讓「憑證消失」變成看得見的事件而不是無聲失敗。若希望維持回傳筆數一致，可以在找不到 credential 時 push 一個 `{ success: false, ... }` 的 EventResult，與 catch 區塊的降級回傳保持同一種形狀。

#### F-008 handleCancelBooking 把週期性取消的整段刪除邏輯搬進了 per-reference 迴圈，刪除呼叫會乘上 reference 數量 — `packages/features/bookings/lib/handleCancelBooking.ts:418`

面向 B 簡潔 · Suggestion

**問題**：第 418 行從 `.find()` 改成 `.filter()` 之後，外面套了 `for (const reference of bookingCalendarReference)`（第 423 行）。問題是迴圈裡第 449 行那段週期性取消邏輯本來就已經自己掃過「使用者的所有行事曆 credential × 所有 updatedBookings」，它跟當前這一筆 reference 完全無關（它用的是 updBooking.references 自己找出來的 bookingRef）。現在它被包進外層迴圈，一筆 booking 有 N 個 calendar reference，就會把同一組刪除呼叫重複執行 N 次。這正是本 MR 想支援的多行事曆情境（N ≥ 2），所以新功能一啟用就會踩到。實務上多半不會造成資料錯誤（Google 對已刪除事件回 410，程式碼有處理），但會產生 N 倍的第三方 API 呼叫與 N 倍的 apiDeletes 項目，在 rate limit 與延遲上都是實際成本。

**證據**：
- `packages/features/bookings/lib/handleCancelBooking.ts:418`
- `packages/features/bookings/lib/handleCancelBooking.ts:423`
- `packages/features/bookings/lib/handleCancelBooking.ts:449`

**修復方向**：把週期性取消那段（第 444–468 行的 `if (bookingToDelete.eventType?.recurringEvent && bookingToDelete.recurringEventId && allRemainingBookings) { ... }` 整塊）移到 per-reference 迴圈之外，在進迴圈前判斷一次；迴圈內只保留 else 分支「刪除這一筆 reference 對應的事件」。這樣兩種情境的責任邊界也會比較清楚。

#### F-009 對外 webhook payload 的 destinationCalendar 形狀變更沒有版本或公告，且各事件之間仍不一致 — `apps/web/playwright/webhook.e2e.ts:249`

面向 D API 慣例 · Suggestion

**問題**：CalendarEvent 直接就是 webhook payload 的來源，destinationCalendar 是其中對外可見的欄位——webhook.e2e.ts 三處快照都把它列在期望值裡即為證明。這次把型別由 `DestinationCalendar | null` 改成 `DestinationCalendar[] | null`，任何寫了 `payload.destinationCalendar.externalId` 的第三方整合都會在部署當下同時失效，而 MR 內沒有版本欄位、沒有相容期、也沒有 changelog。更麻煩的是改完之後三種形狀並存：BOOKING_REJECTED 送 `[]`（第 249 行，本次改動），BOOKING_CREATED 與 BOOKING_REQUESTED 仍送 `null`（第 119、373 行，未改動，因為 handleNewBooking 那條路徑用 null——見 F-005），而有設定時送陣列。消費端要同時處理 null、[] 與陣列三種情況，卻沒有任何文件說明。

**證據**：
- `apps/web/playwright/webhook.e2e.ts:249`
- `apps/web/playwright/webhook.e2e.ts:119`
- `apps/web/playwright/webhook.e2e.ts:373`
- `packages/types/Calendar.d.ts:171`

**修復方向**：先把 F-005 的 null 統一成 []，讓形狀只剩「陣列」一種。接著在 MR 描述與 release note 明確標記這是 webhook payload 的 breaking change，並列出受影響的 trigger；若專案有 webhook 版本機制，優先考慮讓舊版 payload 繼續送單一物件（取陣列第 0 個），新版才送陣列，給消費端一個過渡窗口。

#### F-010 loadUsers 被整段改寫：與本 MR 無關、悄悄拿掉 organization select、並把錯誤訊息吞成通用 500 — `packages/features/bookings/lib/handleNewBooking.ts:724`

面向 E 架構 · Suggestion

**問題**：三件事疊在一起。(1) 這段改寫與 destinationCalendar 陣列化沒有關係，卻是整個 MR 中結構變動最大的區塊之一，增加了審查成本。(2) 動態預約分支的 prisma select 少了原本的 `organization: { select: { slug: true } }`（改動前存在，現在只剩 userSelect.select + credentials + metadata），但 IsFixedAwareUser 的型別宣告仍然要求 `organization: { slug: string }`（第 355 行）；之所以編譯不炸，是因為第 870 行用 `users as IsFixedAwareUser[]` 硬轉。我在 packages/features/bookings、packages/core、packages/lib、packages/emails 底下 grep 過 `organization.slug` / `organization?.slug`，目前沒有消費端，所以不是立即的執行期錯誤——但型別在說謊，下一個依這個型別寫 `user.organization.slug` 的人會拿到 undefined。順帶一提，原本 `credentials: true` 後面那句 `// Don't leak to client` 註解也被刪掉了。(3) 新的 try/catch 把 `new Error("dynamicUserList is not properly defined or empty.")` 轉成 statusCode 500、message "Unable to load users"，原始訊息在回應與例外裡都消失；同一個 catch 也會把 HttpError 一律改寫成 400，例如原本的 404 語意會被降級。

**證據**：
- `packages/features/bookings/lib/handleNewBooking.ts:724`
- `packages/features/bookings/lib/handleNewBooking.ts:736`
- `packages/features/bookings/lib/handleNewBooking.ts:355`
- `packages/features/bookings/lib/handleNewBooking.ts:870`

**修復方向**：建議把整段 loadUsers 改寫拆成獨立 MR。若要留在本 MR：把 `organization: { select: { slug: true } }` 加回動態分支的 select（或反過來，從 IsFixedAwareUser 拿掉 organization 並移除第 870 行的 as 轉型，讓型別誠實反映查詢結果）；把 `// Don't leak to client` 註解補回；catch 區塊改成 `if (error instanceof HttpError) throw error;`，讓既有的 status code 與訊息原樣傳遞，只有真正未預期的錯誤才轉 500，並把原始 message 記進 log。

#### F-011 「找不到 credential 就進 DB 撈」的區塊重複三次，且都放在迴圈內逐筆 findUnique（N+1） — `packages/core/EventManager.ts:344`

面向 B 簡潔 · Suggestion

**問題**：同一段邏輯（先在 this.calendarCredentials / user.credentials 找，找不到就 prisma.credential.findUnique，再手工把 8 個欄位映射成 CredentialWithAppName）在這個 MR 內出現三次；前兩處連欄位順序都一樣，只有變數名不同。依 Rule of Three，第三次出現就該抽出來。三處都位於 per-destination / per-reference 迴圈內，每一筆缺席的 credential 就是一次獨立 round trip——這是典型的 N+1，而且發生在預約建立／取消這種對延遲敏感的同步路徑上。手工映射還有靜默漏欄位的風險：第三處（handleCancelBooking.ts:431）直接把 prisma 回傳的 Credential 指派給 calendarCredential，沒有 app.slug；前兩處則在 `credentialFromDB.app?.slug` 為空時整筆放棄，連 log 都沒有。

**證據**：
- `packages/core/EventManager.ts:344`
- `packages/core/EventManager.ts:517`
- `packages/features/bookings/lib/handleCancelBooking.ts:431`

**修復方向**：抽一個共用函式，例如 `packages/core/CalendarManager.ts` 裡的 `getCredentialsByIds(ids: number[]): Promise<CredentialWithAppName[]>`，用 `prisma.credential.findMany({ where: { id: { in: ids } }, include: { app: { select: { slug: true } } } })` 一次撈完，再讓三個呼叫點從 Map 取用。同時把「撈不到 / 沒有 app.slug」的情況統一記 log，不要靜默略過。

#### F-012 跨 22 個檔案的行為重構，測試面只有一個期望值從 null 改成 [] — `apps/web/playwright/webhook.e2e.ts:249`

面向 G 測試 · Suggestion

**問題**：這次改動觸及 booking 建立、更新、取消三條主路徑與 5 個行事曆整合，但整個 diff 裡唯一的測試變動是 webhook.e2e.ts 第 249 行把一個快照期望值由 null 改成 []——那是被動跟隨實作，不是驗證新行為。沒有任何測試覆蓋：多個 destinationCalendar 時 createAllCalendarEvents 是否真的對每個目標各建一次事件、booking reference 的 externalCalendarId/credentialId 是否寫對、destinationCalendar 為空時 Google Meet 是否仍退回 Cal Video（F-001 壞掉的正是這裡，有測試就會被擋下）、credentialId 為 null 的既有 destinationCalendar 走哪條路（F-004）。repo 已有 vitest（vitest.config.ts、vitest.workspace.ts）與 packages/lib/test/builder.ts 這類 CalendarEvent builder，寫這類單元測試的基礎設施是現成的。

**證據**：
- `apps/web/playwright/webhook.e2e.ts:249`
- `packages/core/EventManager.ts:338`
- `packages/features/bookings/lib/handleNewBooking.ts:1077`

**修復方向**：至少補三個 vitest 案例，都可以用 packages/lib/test/builder.ts 的 buildCalendarEvent 加上 mock 的 CalendarService：(1) destinationCalendar 有兩個元素、各自帶不同 credentialId 時，createAllCalendarEvents 應呼叫 createEvent 兩次，且 referencesToCreate 兩筆的 externalCalendarId 分別等於各自的 externalId；(2) destinationCalendar 為 null 或 [] 且 location 為 MeetLocationType 時，EventManager.create 不應丟例外且 location 應為 integrations:daily；(3) destinationCalendar 的元素 credentialId 為 null 時，GoogleCalendarService.createEvent 使用的 calendarId 應為該元素的 externalId 而非 "primary"。

</details>

<details>
<summary>Nit（2）</summary>

#### F-013 命名跟不上形狀變更：單數名稱裝著陣列，mainHostDestinationCalendar 其實只是第 0 個元素 — `packages/features/bookings/lib/handleCancelBooking.ts:418`

面向 A 風格 · Nit

**問題**：`bookingCalendarReference` 改成 `.filter()` 之後是陣列，名稱仍是單數（handleCancelBooking.ts:418），下面又寫 `if (bookingCalendarReference.length > 0)`，讀起來像在檢查一個物件的長度。`calendarReference: PartialReference[]`（EventManager.ts:479）同樣是單數名。`mainHostDestinationCalendar` 這個名字承諾的是「主辦者的行事曆」，實際上只是 `destinationCalendar[0]`——在 COLLECTIVE 活動裡第 0 個是 eventType 的行事曆而不是任何 host 的，名字與內容不符會誤導後續維護者以為這裡已經處理過「誰是主 host」。

**證據**：
- `packages/features/bookings/lib/handleCancelBooking.ts:418`
- `packages/core/EventManager.ts:479`
- `packages/core/EventManager.ts:118`
- `packages/app-store/office365calendar/lib/CalendarService.ts:73`

**修復方向**：改成複數：`bookingCalendarReferences`、`calendarReferences`。`mainHostDestinationCalendar` 若語意就是「第一個」，改叫 `firstDestinationCalendar` 或 `primaryDestinationCalendar` 並在解構處加一行註解說明為什麼取第一個就夠；若語意真的是主 host，則應該明確用 credentialId 或 userId 去找，而不是靠陣列順序。

#### F-014 孤兒註解與 enum／字面字串混用 — `packages/app-store/googlecalendar/lib/CalendarService.ts:145`

面向 A 風格 · Nit

**問題**：(1) CalendarService.ts:145 的 `// Find in calEventRaw.destinationCalendar the one with the same credentialId` 後面跟著一行空行才是程式碼，而且這句只是重述下一行在做什麼；真正值得寫的是「為什麼」——為什麼要用 credentialId 比對、比不到時為何退回 primary。(2) handleNewBooking.ts:688 把 `eventType.schedulingType === SchedulingType.COLLECTIVE || === ROUND_ROBIN` 改成字面字串陣列 `["COLLECTIVE", "ROUND_ROBIN"].includes(...)`，但同一個檔案第 805 行仍在用 `SchedulingType.ROUND_ROBIN`；同檔兩種寫法並存，且字面字串失去了 enum 改名時的編譯期保護。(3) packages/lib/CalendarService.ts:509 把 `const [mainHostDestinationCalendar] = event?.destinationCalendar ?? []` 放在 reduce 的 callback 內，每一圈都重算一次同樣的值。

**證據**：
- `packages/app-store/googlecalendar/lib/CalendarService.ts:145`
- `packages/features/bookings/lib/handleNewBooking.ts:688`
- `packages/features/bookings/lib/handleNewBooking.ts:805`
- `packages/lib/CalendarService.ts:509`

**修復方向**：(1) 刪掉重述型註解與多餘空行，若要留就改寫成解釋原因的一句話。(2) 統一使用 `SchedulingType` enum：`!!eventType.schedulingType && [SchedulingType.COLLECTIVE, SchedulingType.ROUND_ROBIN].includes(eventType.schedulingType)`。(3) 把第 509 行的解構移到 reduce 之外。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 新增的三處 `prisma.credential.findUnique({ where: { id } })` 直接以 id 取出 credential（內含 OAuth key），沒有任何 userId / teamId 範圍限制。這個「跨使用者取用憑證」的放寬是本次多行事曆功能刻意需要的，還是順手寫成的？如果是刻意的，界線應該畫在哪裡？

面向 C 安全

**背景**：位置在 packages/core/EventManager.ts:344、packages/core/EventManager.ts:517、packages/features/bookings/lib/handleCancelBooking.ts:431。改動前這三處都只在呼叫端組好的集合（this.calendarCredentials 或 bookingToDelete.user.credentials）內尋找，等於天然被限制在該使用者／該 EventManager 的憑證範圍內；改動後任何存在於 booking reference 或 destinationCalendar 上的 credentialId 都能被取出使用。我追過 id 的來源：它們來自 DB 內的 BookingReference.credentialId 與 DestinationCalendar.credentialId，不是請求 body 可以直接指定的欄位，所以我無法證明存在可被利用的路徑——因此不列為 finding。但我也無法證明所有寫入這兩個欄位的路徑都經過授權檢查。

**如何確認**：列出所有會寫入 BookingReference.credentialId 與 DestinationCalendar.credentialId 的程式路徑，確認每一條都只能寫入呼叫者有權使用的 credential id；或者直接在這三處查詢加上範圍條件（例如 `where: { id, OR: [{ userId: { in: teamUserIds } }, { teamId }] }`），讓授權界線寫在查詢裡而不是依賴上游。

#### Q-002 當 eventType.destinationCalendar 與某位團隊成員的 destinationCalendar 指向同一個行事曆時，會不會在同一個行事曆上建出兩筆重複事件？

面向 F 資料取用與資料庫

**背景**：packages/features/bookings/lib/handleNewBooking.ts:1077 把 teamDestinationCalendars 直接 push 進 evt.destinationCalendar，沒有任何去重；packages/core/EventManager.ts:339 的 createAllCalendarEvents 也是逐筆處理，不比對重複。COLLECTIVE 活動的擁有者本身通常也是成員之一，活動層級的「加入行事曆」設定又常常指向擁有者自己的行事曆，所以兩者相同並不罕見。我沒有能執行的環境（沒有 node_modules、沒有網路、無法跑測試），也找不到任何去重程式碼，但也不能排除 Google 端以 iCalUID 做了冪等處理而不會真的重複。

**如何確認**：用一個 COLLECTIVE 活動實測：活動的 destinationCalendar 與其中一位成員的 destinationCalendar 設成同一個 externalId，預約後看該行事曆上是一筆還是兩筆事件、以及 BookingReference 產生幾筆。若確認會重複，在組完 evt.destinationCalendar 之後依 (integration, externalId) 去重即可。

#### Q-003 正式環境的 DestinationCalendar 資料表裡，credentialId 為 null 的列有多少？這決定了 F-004 的實際影響範圍。

面向 F 資料取用與資料庫

**背景**：packages/prisma/schema.prisma:159 的 credentialId 是 `Int?`，而 packages/trpc/server/routers/loggedInViewer/setDestinationCalendar.handler.ts:25 現在一定會寫入 credentialId（撈不到還會直接丟 BAD_REQUEST），所以新資料應該都有值。舊資料則不一定——程式碼在 EventManager.ts:341 與 handleCancelBooking.ts:426 都還為 credentialId 為空的情況保留了分支，可見這個狀態被預期存在。這是典型的環境相依問題：我只能從 schema 與程式碼分支推斷它可能存在，無法從這台機器查證正式環境的實際資料分布。

**如何確認**：在正式資料庫跑一次 `SELECT count(*) FROM "DestinationCalendar" WHERE "credentialId" IS NULL;`。若不為零，F-004 就是會立刻影響既有使用者的問題，應在合併前補一次回填 migration；若為零，F-004 的修正仍該做（保護 schema 允許的狀態），但可以不必阻擋上線。

</details>
