## 審查結論：Request Changes

> Critical 1 · Suggestion 8 · Nit 3 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ✅ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ✅ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **B 簡潔**（未通過）：F-006：packages/ui/form/MultiEmail.tsx 與 packages/features/form-builder/Components.tsx:231-314 的 multiemail factory 幾乎逐字重複（連 inputClassName 字串都一樣），抽出後沒有取代原處。
- **C 安全**（未通過）：F-001：BLACKLISTED_GUEST_EMAILS 的比對只把黑名單那一側轉小寫、也沒有走 extractBaseEmail，與 repo 內其他三處用法不一致，這條路徑上的反濫用控制可被直接繞過；guests 陣列也沒有長度上限。
- **D API 慣例**（未通過）：F-002 / F-003 / F-004：新 endpoint 用 authedProcedure 加手寫檢查，而同檔案的 editLocation 用的是 bookingsProcedure；團隊權限判斷用了 && 而不是 ||；booking.status 與 eventType.disableGuests 都沒有檢查。
- **F 資料取用與資料庫**（未通過）：F-005：uniqueGuests 只排除「已經是 attendee」與黑名單，沒有排除輸入陣列自己的重複；而且 findFirst 讀、update 寫之間沒有交易或唯一約束保護（packages/prisma/schema.prisma:526-539 的 Attendee 沒有 (bookingId, email) 唯一鍵）。
- **G 測試**（未通過）：F-009：diff 內 0 個測試檔。新增了一個帶權限判斷的 tRPC mutation、一個共用 UI 元件與兩個 email template，而 apps/web/playwright/bookings-list.e2e.ts 是現成的落點。
- **H 非 Python 檔**（未通過）：整份 diff 都是非 Python 檔，此維度適用。F-008：AddGuestsDialog 的錯誤狀態沒有在成功送出與非 Cancel 關閉時重設。F-012：MultiEmail 對可編輯輸入用 key={index} 搭配 splice 移除。
- **I 回溯分析**（未通過）：沒有既有函式簽章被改動（新增的都是新檔案與新 export；packages/ui/index.tsx:153 的 MultiEmail 名稱先前不存在，grep 確認無衝突）。但 F-007 命中隱含輸入契約這一軸：sendAddGuestsEmails 的第二參數叫 newGuests，呼叫端傳的卻是未經過濾的原始 input。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 同時做了兩件事：(1) 新增 addGuests 功能；(2) 把 packages/features/form-builder/Components.tsx:231-314 的 multiemail factory 幾乎逐字複製成新的共用元件 packages/ui/form/MultiEmail.tsx。第 (2) 件只做了一半——原處的實作沒有被換掉，`//TODO: Make it a ui component` 也還留著，結果是 repo 裡多了一份會各自漂移的副本。要嘛在這個 MR 一併把 form-builder 換過去（那 diff 會變大但方向正確），要嘛先讓元件留在 apps/web 本地，等第三個使用者出現再抽。這個取捨應該由人決定，不該由審查代決。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 不在 PATH 上。相依套件漏洞、設定錯誤與 secret 掃描本次完全沒有執行。 |
| opengrep | 略過 | opengrep 不在 PATH 上，且 NCR_OPENGREP_RULES 指向的規則目錄（預設是 HOME 底下的 semgrep-rules）不存在——兩個條件都缺。本次 diff 全是 .ts/.tsx/.json，等於整份改動沒有任何 SAST 覆蓋。 |
| ruff | 略過 | ruff 有安裝且實際執行完成（exit 0、0 筆診斷），但整個 repo 內 0 個 .py 檔，本次 diff 也全是 .ts/.tsx/.json——沒有任何檔案進入檢查。照 scanners.md 對 oxlint「無檔可 lint」的處理原則記為 skipped：記成 ok 會等同宣稱一次乾淨的掃描，而這次根本沒有東西被掃到。 · exit code 0 · in_diff 0、outside_diff 0 |
| ty | 略過 | ty 不在 PATH 上；即使有，repo 內也沒有 Python 可檢查。 |
| oxlint | 略過 | oxlint 不在 PATH 上。這是本次工具箱裡唯一能覆蓋 .ts/.tsx 的靜態檢查器，缺席代表整份 TypeScript 改動沒有任何自動化 lint。 |
| tsc / next lint / vitest / playwright | 略過 | checkout 內沒有 node_modules，且執行環境無網路，無法安裝相依。型別檢查、單元測試與 e2e 一律沒有執行過。下面所有結論都是純讀原始碼得到的，沒有任何一項被編譯器或測試證實過。 |
| codegraph | 略過 | codegraph 未安裝。Phase 3 的呼叫路徑列舉與完整性確認全部改用 grep 於 checkout 上進行。 |
| ncr-fresh-eyes（subagent） | 略過 | 本次執行環境沒有任何可派送 subagent 的工具（Task/Agent 皆不存在，ToolSearch 也查不到）。Phase 3 第 1 步的無框架初讀因此完全沒有發生，也沒有在主流程裡自行模擬——skill 明確禁止那樣做，因為主 agent 讀完 review-dimensions.md 之後的「初讀」已經被清單塑形過了。這代表「清單沒有寫到的問題」這一類的覆蓋在本次是缺的。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派送 subagent。Phase 4 第 3 步的獨立品質複核沒有執行，這份報告只經過 report_model.py 的機械驗證（結論與 findings 一致、每條 finding 有 fix、Critical 安全項有 POC/blast radius/treatment、九個維度都有結論），沒有經過第二雙眼睛檢查用詞、證據強度與嚴重度校準。 |

### Critical

#### F-001 BLACKLISTED_GUEST_EMAILS 比對只正規化單邊，這條路徑上的黑名單可被大小寫或 plus-address 直接繞過 — `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:70-78`

面向 C 安全 · Critical

**問題**：第 71 行把黑名單那一側 `.map((email) => email.toLowerCase())`，第 77 行卻拿未經處理的 `guest` 去 `includes()`。email 的 local part 在實務上大小寫不敏感，所以 `Blocked@Example.com` 不會等於 `blocked@example.com`，比對必然落空。repo 內另外三處用法都是兩側一起正規化：handleNewBooking.ts:742 用 `extractBaseEmail(guest).toLowerCase()` 對上 `blacklistedGuestEmails.some((e) => e.toLowerCase() === ...)`，checkIfBookerEmailIsBlocked.ts:18 也是 `guestEmail.toLowerCase() === baseEmail.toLowerCase()`。這裡少了 extractBaseEmail，連 `blocked+anything@example.com` 這種 plus-address 繞法也一併放行。

我有找過反證：(a) addGuests.schema.ts:3-6 只有 `z.string().email()`，zod 不做小寫正規化；(b) AddGuestsDialog.tsx:26-29 的前端 schema 也沒有；(c) 整條路徑上沒有任何 middleware 會先把 email 轉小寫（addGuests 用的是 authedProcedure，_router.tsx:79）。所以上游沒有任何一層擋得住。

嚴重度定在 Critical 而不是 Suggestion，是因為這不是「少做一個檢查」，而是「一個已經存在、部署方相信它有效的控制在這條新路徑上靜默失效」——控制看起來還在（程式碼讀起來像在做黑名單），實際上不會攔下任何東西，而失效本身沒有任何 log 或訊號。搭配 guests 陣列沒有長度上限（schema.ts:5 的 `z.array(z.string().email())` 沒有 `.max()`），單次呼叫就能讓系統以 organizer 的 from address 對任意數量的地址寄信。

**證據**：
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:70-78`
- `packages/features/bookings/lib/handleNewBooking.ts:736-751`
- `packages/features/bookings/lib/handleNewBooking/checkIfBookerEmailIsBlocked.ts:12-19`
- `packages/lib/extract-base-email.ts:2-6`
- `packages/trpc/server/routers/viewer/bookings/addGuests.schema.ts:3-6`

**POC**：

````
以任一在該 booking 上的 attendee 身分登入（或直接用 organizer 帳號），對自架站台送出：

```bash
curl -X POST 'https://cal.example.com/api/trpc/bookings/addGuests?batch=1' \
  -H 'Content-Type: application/json' \
  -H 'Cookie: next-auth.session-token=<session>' \
  -d '{"0":{"json":{"bookingId":123,"guests":["Blocked@Example.COM","blocked+x@example.com"]}}}'
```

前提是站台設定了 `BLACKLISTED_GUEST_EMAILS=blocked@example.com`。預期行為是兩個地址都被過濾掉、回 400 `emails_must_be_unique_valid`；實際行為是兩個都通過 addGuests.handler.ts:74-78 的過濾，被寫進 Attendee 表（:92-106），並在 :168 收到 AttendeeScheduledEmail（內含會議連結、地點與 videoCallData 的 meetingPassword，見 :149-155）。把 guests 換成 500 個地址即可觀察到沒有長度上限。
````

**影響範圍**：被站台明確列入黑名單的地址仍可被加進任何一場預約，並收到含會議連結與 video meeting password 的信；黑名單在 handleNewBooking 那條路徑上有效、在這條新路徑上無效，所以站台管理者從既有行為推不出這個缺口。加上 guests 陣列無上限，單一已登入使用者可用一次請求觸發任意數量的外寄信（每封的 from 是 organizer 名義、replyTo 指向 organizer email，organizer-add-guests-email.ts:23-25），對寄信網域的信譽是直接風險。本次不涉及 PHI（見 meta.phi_trigger），外洩內容為會議標題、時間、地點與參與者 email 等 PII。

**風險處置**：Mitigate（降低）

**修復參考**：addGuests.handler.ts:70-78 兩側一起走 extractBaseEmail(...).toLowerCase()；addGuests.schema.ts:5 加 .min(1).max(10)

**修復方向**：把兩側對齊到 repo 既有寫法，並補上長度上限：

```ts
// addGuests.handler.ts
import { extractBaseEmail } from "@calcom/lib/extract-base-email";

const blacklistedGuestEmails = process.env.BLACKLISTED_GUEST_EMAILS
  ? process.env.BLACKLISTED_GUEST_EMAILS.split(",").map((e) => extractBaseEmail(e).toLowerCase())
  : [];

const uniqueGuests = guests.filter(
  (guest) =>
    !booking.attendees.some((attendee) => attendee.email.toLowerCase() === guest.toLowerCase()) &&
    !blacklistedGuestEmails.includes(extractBaseEmail(guest).toLowerCase())
);
```

```ts
// addGuests.schema.ts
guests: z.array(z.string().email()).min(1).max(10),
```

更好的做法是把 handleNewBooking.ts:736-751 那段抽成一個共用 helper（例如 `filterBlacklistedGuests`），讓這兩條路徑不可能再各自漂移——目前是第三處各寫一次同一段邏輯。

<details>
<summary>Suggestion（8）</summary>

#### F-002 addGuests 用 authedProcedure 加手寫檢查，讓任何 attendee 都能改動別人的 booking；同檔案的 editLocation 走的是 bookingsProcedure — `packages/trpc/server/routers/viewer/bookings/_router.tsx:79-96`

面向 D API 慣例 · Suggestion

**問題**：util.ts:19-63 的 bookingsProcedure 是這個 router 對「誰能動這筆 booking」的既有答案：organizer（`userId: ctx.user.id`）或 COLLECTIVE 事件的共同主持人，兩者皆非就丟 UNAUTHORIZED。editLocation 用它（_router.tsx:62）。新的 addGuests 改用 authedProcedure（:79）並在 handler 內自己寫一組判斷，其中 :52 的 `isAttendee` 把授權範圍擴到「email 出現在這筆 booking 的 attendee 列表上的任何已登入使用者」。

這代表一個受邀者（不是主辦人）可以永久修改主辦人的 booking 記錄、把任意地址寫進 Attendee 表、觸發 eventManager.updateCalendarAttendees(:165) 去改主辦人日曆上的事件，並以主辦人名義發信。主辦人沒有任何否決或知情以外的手段。

這也許是刻意的產品決策——「讓與會者自己拉人進來」本身是合理需求——但它讓這個 router 對授權有了兩套互相矛盾的答案，而 diff 裡沒有任何註解或測試說明是刻意的。dimension D 的第 4 條正是這件事：authedProcedure 確立了「你是誰」，它沒有回答「你可不可以動這一筆」。

反證我查過了：:54 的檢查確實存在且會擋掉完全無關的使用者，所以這不是「沒有授權檢查」；問題是檢查的範圍。另外 :165 用的是 `ctx.user` 的 credentials 而非主辦人的，我原本以為會導致日曆更新用錯憑證，但 EventManager.updateAllCalendarEvents（packages/core/EventManager.ts:845-860）在找不到對應 credential 時會退回資料庫查，所以那條不成立，不列為 finding。

**證據**：
- `packages/trpc/server/routers/viewer/bookings/_router.tsx:79-96`
- `packages/trpc/server/routers/viewer/bookings/_router.tsx:62-77`
- `packages/trpc/server/routers/viewer/bookings/util.ts:19-63`
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:26-56`

**修復方向**：先把「attendee 可以加 guest 嗎」當成明確的產品決策寫下來。如果答案是否定的，直接改用 bookingsProcedure，handler 內的三段判斷就都可以刪掉：

```ts
// _router.tsx
addGuests: bookingsProcedure.input(ZAddGuestsInputSchema).mutation(...)
```

如果答案是肯定的，至少要 (a) 在 :46-56 上方留一段註解說明為什麼這裡刻意比 bookingsProcedure 寬，(b) 補一個 e2e case 釘住「attendee 可以、無關使用者不行」，避免下一次有人「順手統一」成 bookingsProcedure 時把功能改壞。

#### F-003 isTeamAdminOrOwner 用 && 串接，實際語意是「只有 OWNER」，team ADMIN 被排除在外 — `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:46-48`

面向 D API 慣例 · Suggestion

**問題**：變數名叫 `isTeamAdminOrOwner`，但寫的是 `isTeamAdmin(...) && isTeamOwner(...)`。查兩個函式的定義：isTeamAdmin（teams/index.ts:264-284）本身就是 `role IN (ADMIN, OWNER)`，isTeamOwner（:286-295）是 `role === OWNER`。兩者取交集等於 OWNER。所以名字承諾的是「admin 或 owner」，行為是「只有 owner」——dimension A 第 4 條說的「名字承諾的比行為多」，而且它決定的是一條授權分支。

實際影響是 team ADMIN 在自己團隊的 booking 上拿不到這個權限（除非他剛好也是 organizer 或 attendee，那另外兩個條件會接住他）。這是 fail-closed，所以不是安全風險，但它是一個功能缺口，而且是那種要等使用者回報才會被發現的缺口。

另外 :47-48 兩次都寫 `booking.eventType?.teamId ?? 0`：teamId 為 null 時（個人事件）會拿 0 去查 membership，查不到、回 false，行為正確，但用 0 當哨兵值不如直接短路。

**證據**：
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:46-48`
- `packages/lib/server/queries/teams/index.ts:264-284`
- `packages/lib/server/queries/teams/index.ts:286-295`

**修復方向**：```ts
const teamId = booking.eventType?.teamId;
// isTeamAdmin 已涵蓋 ADMIN 與 OWNER，不需要再與 isTeamOwner 交集
const isTeamAdminOrOwner = teamId ? !!(await isTeamAdmin(user.id, teamId)) : false;
```

順帶省掉一次資料庫查詢，也省掉 `?? 0` 這個哨兵值。

#### F-004 handler 沒有檢查 booking.status 與 eventType.disableGuests，前置條件比呼叫它的 UI 還寬 — `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:26-56`

面向 D API 慣例 · Suggestion

**問題**：兩個前置條件只存在於 UI，不存在於 endpoint：

**booking.status。** BookingListItem.tsx:544-554 只在 `isUpcoming && !isCancelled && isConfirmed`、或 `isBookingInPast && (isConfirmed || isPending)` 時才渲染含這個選項的 TableActions。handler 從 :26 讀 booking 到 :56 通過授權為止，完全沒有讀 `booking.status`。tRPC mutation 是可以直接呼叫的，所以對一筆 CANCELLED 或 REJECTED 的 booking 加 guest 會成功：寫進 Attendee 表、對已取消的會議寄出 `guests_added_event_type_subject` 通知，並附上 `status: "CONFIRMED"`、`method: "REQUEST"` 的 ICS（attendee-add-guests-email.ts:6-13），等於對收件人宣告一場已被取消的會議是有效的。

**eventType.disableGuests。** schema.prisma:110 有這個欄位，語意是「這個事件類型不接受 guest」；getCalEventResponses.ts:49-52 的註解確認它就是用來讓 guests 欄位消失的。addGuests 這條路徑完全沒有讀它，所以主辦人明確關掉 guest 的事件類型，事後仍可被加進 guest。

反證查過：grep 過整個 handler 與 _router.tsx 的 addGuests 區段，沒有任何 middleware 補上這兩項；authedProcedure 只做登入檢查。BookingListItem 那層的過濾也不是 trust boundary（dimension C：前端驗證不豁免後端）。

嚴重度是 Suggestion 而非 Critical：要觸發需要先通過 :54 的授權檢查，也就是必須已經是這筆 booking 的關係人，不是任意人可達。

**證據**：
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:26-56`
- `apps/web/components/booking/BookingListItem.tsx:544-554`
- `apps/web/components/booking/BookingListItem.tsx:193-200`
- `packages/prisma/schema.prisma:110`
- `packages/features/bookings/lib/getCalEventResponses.ts:49-52`

**修復方向**：在授權檢查之後、寫入之前補兩道：

```ts
if (booking.status === BookingStatus.CANCELLED || booking.status === BookingStatus.REJECTED) {
  throw new TRPCError({ code: "BAD_REQUEST", message: "booking_not_active" });
}
if (booking.eventType?.disableGuests) {
  throw new TRPCError({ code: "FORBIDDEN", message: "guests_not_allowed_for_event_type" });
}
```

`disableGuests` 為 true 時，BookingListItem.tsx:193-200 那個選單項目也應該一併不要放進 editBookingActions，否則使用者看得到卻按不動。

#### F-005 uniqueGuests 沒有排除輸入陣列自己的重複，且 read-modify-write 之間沒有交易或唯一約束 — `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:74-78`

面向 F 資料取用與資料庫 · Suggestion

**問題**：變數叫 `uniqueGuests`，但它的過濾條件只有兩個：不在既有 attendees 裡、不在黑名單裡。輸入陣列自己的重複沒有被處理，所以 `guests: ["a@b.c", "a@b.c"]` 會原封不動流到 :101 的 `createMany`，在 Attendee 表產生兩列。schema.prisma:526-539 的 Attendee 沒有 `@@unique([bookingId, email])`，資料庫不會擋。

去重目前只做在前端：AddGuestsDialog.tsx:26-29 的 `.refine` 檢查 Set size。但 addGuests.schema.ts:3-6 的 server schema 沒有這個 refine——前端不是 trust boundary，直接打 tRPC 就能繞過。

第二層是併發：:26 的 findFirst 讀出 attendees、:74 在記憶體裡比對、:92 才寫入，中間沒有交易也沒有唯一約束。兩個同時送出相同 email 的請求都會通過 :76 的比對，然後各寫一列。這條路徑現在的觸發者不只 organizer（見 F-002），同一場會議可能有多個人同時操作。

重複的 attendee 之後會一路帶進 :108-120 的 attendeesList、evt.attendees，最後在 email-manager.ts:539-547 讓同一個地址收到兩封信；`seatsShowAttendees` 開啟時也會在頁面上重複顯示。

**證據**：
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:74-78`
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:92-106`
- `packages/trpc/server/routers/viewer/bookings/addGuests.schema.ts:3-6`
- `apps/web/components/dialog/AddGuestsDialog.tsx:26-29`
- `packages/prisma/schema.prisma:526-539`

**修復方向**：server schema 補上與前端相同的 refine，並在寫入層加上唯一約束：

```ts
// addGuests.schema.ts
guests: z
  .array(z.string().email())
  .min(1)
  .max(10)
  .refine((emails) => new Set(emails.map((e) => e.toLowerCase())).size === emails.length, {
    message: "emails_must_be_unique_valid",
  }),
```

併發那一層真正的修法是資料庫層：在 Attendee 上加 `@@unique([bookingId, email])`（需要一支先清理既有重複資料的 migration），或把 :26 的讀取與 :92 的寫入包進 `prisma.$transaction`。若判斷這個 race 的實務發生率不值得一支 migration，至少在 :74 上方留一句註解說明是刻意接受。

#### F-006 MultiEmail 是 form-builder multiemail factory 的逐字複製，抽出來了卻沒有取代原處，變成兩份會各自漂移的副本 — `packages/ui/form/MultiEmail.tsx:12-96`

面向 B 簡潔 · Suggestion

**問題**：兩份實作放在一起看幾乎逐行相同：同一個 `value = value || []` 開頭、同一串 `inputClassName`（`dark:placeholder:text-muted focus:border-emphasis ...` 一字不差）、同樣的 `<ul>/<li key={index}>` 結構、同樣的 `Tooltip content="Remove email"` 與 `Icon name="x" width={12}`、同樣的兩顆 Button 分支。差異只有 `htmlFor`（guests → emails）、`data-testid`（add-another-guest → add-another-email、add-guests → add-emails）與 className 的 `mb-1` / `my-2` 微調。

最能說明問題的是 Components.tsx:233 那行 `//TODO: Make it a ui component`——這個 MR 做了那件事的前半段（建立 packages/ui/form/MultiEmail.tsx，並在 index.tsx:153 export），卻沒有做後半段（把 form-builder 換過去、刪掉 TODO）。結果 repo 現在有兩份同樣的 multi-email 實作、兩組不同的 data-testid，而 TODO 還在原地，讀起來像沒人動過。下一個修 multi-email bug 的人只會改到其中一份。

KISS > DRY 在這裡不構成豁免：兩份都存在的狀態既不 simple 也不 DRY，它是兩者都輸的那個選項。

**證據**：
- `packages/ui/form/MultiEmail.tsx:12-96`
- `packages/features/form-builder/Components.tsx:231-314`
- `packages/features/form-builder/Components.tsx:233`
- `packages/ui/index.tsx:153`

**修復方向**：二選一，不要停在中間：

1. **抽取到底**（建議）——把 Components.tsx:231-314 的 factory 換成引用新元件，保留原本的 `data-testid`（add-another-guest / add-guests）以免打壞既有 e2e，把 testid 做成 prop：
   ```tsx
   multiemail: {
     propsType: propsTypes.multiemail,
     factory: (props) => <MultiEmail {...props} testIdPrefix="guest" />,
   },
   ```
   然後刪掉 :233 的 TODO。
2. **先不抽**——把元件留在 `apps/web/components/dialog/` 底下，等第三個使用者出現再談抽取（Rule of Three），並在 MR 描述說明為什麼暫時接受這份重複。

#### F-007 sendAddGuestsEmails 的參數叫 newGuests，呼叫端傳的卻是未過濾的原始輸入，導致既有 attendee 可能收到「新預約成立」的信 — `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:168`

面向 I 回溯分析 · Suggestion

**問題**：email-manager.ts:525 的簽章是 `(calEvent: CalendarEvent, newGuests: string[])`，:539-547 用它決定每個 attendee 收哪一種信：在 `newGuests` 裡的收 `AttendeeScheduledEmail`（新預約確認），不在的收 `AttendeeAddGuestsEmail`（有人被加進來了）。這個分支的正確性完全取決於 `newGuests` 真的只包含這次新加的人。

handler :168 傳的是 `guests`，也就是 input 的原始陣列，不是 :74-78 過濾後的 `uniqueGuests`。兩者在一種情況下會不同：使用者送出一個已經是 attendee 的 email。:76 會把它從 `uniqueGuests` 剔除（不會重複建立），但它仍留在 `guests` 裡，於是 :541 的 `newGuests.includes(...)` 命中，那位早就在這場會議裡的既有 attendee 會收到一封「你的預約已確認」的 AttendeeScheduledEmail——內容看起來像一場新成立的預約，而他其實什麼都沒變。

這正是 dimension I 第 3 條的形狀：值滿足了簽章（都是 string[]），卻違反了函式對這個參數的實際假設。反證我查過：:80 的 `uniqueGuests.length === 0` 只在「全部都被過濾掉」時擋下請求，只要有一個新 email 混在裡面，整批 `guests` 就會照原樣傳下去。前端也不會擋——AddGuestsDialog 完全不知道哪些人已經是 attendee。

**證據**：
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:168`
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:74-78`
- `packages/emails/email-manager.ts:525`
- `packages/emails/email-manager.ts:539-547`

**修復方向**：傳過濾後的陣列，並讓型別把這件事講清楚：

```ts
// addGuests.handler.ts:168
await sendAddGuestsEmails(evt, uniqueGuests);
```

順帶一提，:169-171 的 `catch (err) { console.log("Error sending AddGuestsEmails"); }` 把 `err` 丟掉了——這是從 editLocation.handler.ts:139-141 複製過來的既有慣例，所以不另外列為 finding，但既然這個檔案是新寫的，把 `err` 帶進 log 幾乎不花成本：`console.error("Error sending AddGuestsEmails", err)`。

#### F-008 AddGuestsDialog 的 isInvalidEmail 只在按 Cancel 時重設，成功送出與從右上角關閉都會把紅色錯誤留到下次開啟 — `apps/web/components/dialog/AddGuestsDialog.tsx:33`

面向 H 非 Python 檔 · Suggestion

**問題**：`isInvalidEmail` 有三條離開 dialog 的路徑，只有一條重設它：

- Cancel 按鈕（:89-93）：`setMultiEmailValue([""])` + `setIsInvalidEmail(false)` + 關閉。正確。
- 送出成功（:36-41）：重設了 `multiEmailValue`，沒有重設 `isInvalidEmail`。
- `onOpenChange`（:61）直接接 `setIsOpenDialog`，所以右上角 X、按 Esc、點 overlay 關閉都不會重設任何狀態。

這個元件掛在 BookingListItem.tsx:357-361，是常駐渲染、只靠 `isOpenDialog` 控制顯示，不會在關閉時 unmount，所以 state 會一直留著。實際重現：輸入一個格式錯誤的 email → 按 Add → 出現「Emails must be unique and valid」→ 改成正確的 email → 按 Add → 成功、dialog 關閉 → 再開一次同一筆 booking 的這個 dialog → 欄位是空的，但紅色錯誤訊息還在。

同一條路徑上 `multiEmailValue` 在 X 關閉時也不會重設，所以打到一半的 email 會殘留到下一次開啟。

**證據**：
- `apps/web/components/dialog/AddGuestsDialog.tsx:33`
- `apps/web/components/dialog/AddGuestsDialog.tsx:36-41`
- `apps/web/components/dialog/AddGuestsDialog.tsx:61`
- `apps/web/components/dialog/AddGuestsDialog.tsx:88-97`
- `apps/web/components/booking/BookingListItem.tsx:357-361`

**修復方向**：把重設集中到一個地方，讓三條路徑共用：

```tsx
const resetAndClose = () => {
  setMultiEmailValue([""]);
  setIsInvalidEmail(false);
  setIsOpenDialog(false);
};

<Dialog open={isOpenDialog} onOpenChange={(open) => (open ? setIsOpenDialog(true) : resetAndClose())}>
```

然後 onSuccess 與 Cancel 都改叫 `resetAndClose()`。另外 handleAdd（:53-57）在驗證通過那一支也該補 `setIsInvalidEmail(false)`，否則使用者修好之後、送出失敗回到 dialog 時，錯誤訊息會停留在上一輪的狀態。

#### F-009 整個 MR 沒有任何測試，但已經先鋪好了三個 data-testid — `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:46-56`

面向 G 測試 · Suggestion

**問題**：diff 的 12 個檔案裡沒有一個是測試。這個 MR 新增的是：一組四路分支的授權判斷（handler:46-56：team owner / organizer / attendee / 其他）、一段有兩層過濾的去重邏輯（:74-78）、一個共用 UI 元件、以及兩個會寄出真實郵件的 email template。這些都是行為會靜默改變的東西。

諷刺的是測試的鉤子已經寫好了——AddGuestsDialog.tsx:98 的 `data-testid="add_members"`、MultiEmail.tsx:38 的 `add-another-email`、:62 的 `add-emails`——但沒有任何測試在用它們。data-testid 存在的唯一理由就是被測試選取，三個都沒有使用者，這是很強的訊號說測試本來在計畫裡、後來沒寫。

落點也是現成的：apps/web/playwright/ 底下已經有 bookings-list.e2e.ts。F-001 到 F-005 那幾條裡，至少 F-003（team ADMIN 拿不到權限）與 F-005（重複 email）是一個 e2e case 就會當場撞出來的。

嚴重度是 Suggestion 而非 Critical：cal.com 的 e2e 需要完整環境，不是每個 PR 都必然附測試，這是團隊慣例的問題而非硬性阻擋條件。

**證據**：
- `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:46-56`
- `apps/web/components/dialog/AddGuestsDialog.tsx:98`
- `packages/ui/form/MultiEmail.tsx:38`
- `packages/ui/form/MultiEmail.tsx:62`
- `apps/web/playwright/bookings-list.e2e.ts:1`

**修復方向**：以 apps/web/playwright/bookings-list.e2e.ts 為落點補三個 case，用已經存在的 testid：

1. organizer 開啟 `add_members` → 用 `add-emails` / `add-another-email` 加兩個地址 → 斷言 attendee 列表變成 N+2，且兩封信各寄給誰（不是只斷言 mutation 有回應）。
2. 送出一個重複的 email → 斷言 attendee 數量不變、回 400（這條會直接抓到 F-005）。
3. 一個與該 booking 完全無關的使用者呼叫 `viewer.bookings.addGuests` → 斷言 FORBIDDEN。

如果團隊決定這個 MR 不附 e2e，至少在描述裡寫明哪一個後續 issue 會補，讓 testid 不會無限期懸空。

</details>

<details>
<summary>Nit（3）</summary>

#### F-010 AddGuestsDialog 內宣告的 ZAddGuestsInputSchema 與 server 端同名 export 撞名但語意不同，且每次 render 重建 — `apps/web/components/dialog/AddGuestsDialog.tsx:26-29`

面向 A 風格 · Nit

**問題**：同一個名字 `ZAddGuestsInputSchema` 指向兩個不同的東西：server 端（addGuests.schema.ts:3-6）是 `z.object({ bookingId, guests })`，dialog 內（:26-29）是 `z.array(z.string().email()).refine(...)`。兩者都叫 input schema，形狀卻不同，之後任何人想「統一用同一個 schema」時都會先踩一次。

它還宣告在元件函式本體內（:26），所以每次 render 都會重建一個 zod schema——單一 dialog 的成本可以忽略，但這是沒有理由的：schema 不依賴任何 props 或 state。

**證據**：
- `apps/web/components/dialog/AddGuestsDialog.tsx:26-29`
- `packages/trpc/server/routers/viewer/bookings/addGuests.schema.ts:3-6`

**修復方向**：移到模組層級並改個講得清楚的名字：

```tsx
const guestEmailsSchema = z
  .array(z.string().email())
  .refine((emails) => new Set(emails).size === emails.length);

export const AddGuestsDialog = (props: IAddGuestsDialog) => { ... };
```

更好的方向是等 F-005 把 refine 補進 addGuests.schema.ts 之後，前端直接引用 `ZAddGuestsInputSchema.shape.guests`，兩邊就不可能再各自漂移。

#### F-011 packages/ui 內的元件反過來 import 自己套件的 barrel，同目錄的 AddressInput 用的是相對路徑 — `packages/ui/form/MultiEmail.tsx:2`

面向 E 架構 · Nit

**問題**：MultiEmail.tsx:2 寫 `import { Button, EmailField, Icon, Tooltip } from "@calcom/ui"`，而 `@calcom/ui` 就是 packages/ui/index.tsx，index.tsx:153 又 export 了 MultiEmail——形成 index → MultiEmailLazy → MultiEmail → index 的循環。同目錄的 AddressInput.tsx:1-3 走的是相對路徑（`from ".."` 與 `from "../components/form"`），這是這個目錄的既有慣例。

實務上大概不會爆炸，因為 MultiEmailLazy.tsx:3 用 `next/dynamic` 把 import 延後了，循環在 module evaluation 時被打斷。但這正是它值得改的理由：它現在能動是靠一層 lazy 的巧合，哪天有人把 MultiEmail 改成直接 export（或某個 bundler 的處理方式變了），循環就會變成執行期的 undefined。

另外 MultiEmailLazy.tsx:3 把 PhoneInputLazy.tsx:3 的註解 `/** These are like 40kb that not every user needs */` 一起抄過來了，但這個元件不到 100 行、沒有任何第三方相依（PhoneInput 依賴的是 react-phone-input-2，那才是 40kb 的來源）。這句註解在這裡是錯的資訊。

**證據**：
- `packages/ui/form/MultiEmail.tsx:2`
- `packages/ui/form/AddressInput.tsx:1-3`
- `packages/ui/index.tsx:153`
- `packages/ui/form/MultiEmailLazy.tsx:3`

**修復方向**：改成與 AddressInput 一致的相對匯入，並修掉那句註解：

```tsx
// packages/ui/form/MultiEmail.tsx
import { Icon, Tooltip } from "..";
import { Button } from "../components/button";
import { EmailField } from "../components/form";
```

如果這個元件其實沒有 lazy 的必要（它沒有重相依），也可以直接刪掉 MultiEmailLazy.tsx，讓 index.tsx:153 指向 `./form/MultiEmail`。

#### F-012 MultiEmail 對可編輯的 input 清單用 key={index}，搭配 splice 移除會讓 DOM 狀態錯位 — `packages/ui/form/MultiEmail.tsx:26`

面向 H 非 Python 檔 · Nit

**問題**：`<li key={index}>`（:26）配上 `updatedValue.splice(index, 1)`（:39-41）：刪掉中間那一筆時，後面每一筆的 index 都往前移一格，React 因此認為「第 i 個 li 還是同一個 li，只是內容變了」，會沿用原本的 DOM 節點。因為 EmailField 的 value 是受控的（:19 `value={field}`），顯示的文字會是對的，所以這不是資料錯誤；錯位的是 DOM 綁著的非受控狀態——游標位置、輸入法組字中的內容、瀏覽器的驗證氣泡、`:focus`。使用者刪掉中間一列時會看到焦點跳到別列。

這是從 Components.tsx:249 一起複製過來的既有寫法，所以不是這個 MR 引入的新問題；但既然是新檔案，順手用穩定的 key 幾乎不花成本。列 Nit 而不是 Suggestion，因為症狀限於互動細節，不影響送出的資料。

**證據**：
- `packages/ui/form/MultiEmail.tsx:26`
- `packages/ui/form/MultiEmail.tsx:38-42`
- `packages/features/form-builder/Components.tsx:249`

**修復方向**：改用不隨位置變動的 key。最小的做法是讓 value 從 `string[]` 變成 `{ id: string; email: string }[]`；如果不想動型別，退而求其次可以用 `key={`${index}-${field}`}`，至少在內容不同時能區分。並且無論如何都不要沿用 index 當 key 又同時支援中間刪除。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 對 seats event（eventType.seatsPerTimeSlot 不為 null）直接新增 Attendee 而不建立對應的 BookingSeat，會不會破壞取消座位或 seatsShowAttendees 的流程？

面向 F 資料取用與資料庫

**背景**：addGuests.handler.ts:92-106 直接對 booking.attendees 做 createMany，沒有碰 BookingSeat；schema.prisma:534 的 `bookingSeat BookingSeat?` 是可選的，所以資料庫允許。我先查了會不會超賣：不會——packages/trpc/server/routers/viewer/slots/util.ts:549-555 算剩餘座位用的是 `_count: { select: { seatsReferences: true } }` 而不是 attendees，所以沒有 BookingSeat 的 attendee 不佔容量，這條反證成立、不列為 finding。但反過來的問題我沒能settle：packages/features/bookings/lib/handleSeats/cancel/cancelAttendeeSeat.ts:52 是靠 bookingSeat 來取消個別座位的，一個沒有座位的 attendee 走到那條路徑會如何，需要實際跑起來才知道。而 evt 裡又確實帶了 seatsPerTimeSlot / seatsShowAttendees（handler:145-146），代表作者知道 seats event 會經過這裡。

**如何確認**：在有 seats 的事件上跑一次 apps/web/playwright/booking-seats.e2e.ts 的情境，然後對該 booking 呼叫 addGuests，再嘗試 (a) 讓新 guest 從 /booking/[uid] 取消自己的座位、(b) 開啟 seatsShowAttendees 檢視與會者列表。或者請作者說明 seats event 是否刻意不在這個功能的範圍內——若是，handler 應該明確擋掉 `booking.eventType?.seatsPerTimeSlot`。

#### Q-002 新加入的 guest 沒有被寫回 booking.responses.guests，事後 reschedule 或編輯這筆 booking 時會不會把他們洗掉？

面向 I 回溯分析

**背景**：handler 只新增 Attendee 列（:92-106），沒有更新 booking.responses。packages/features/bookings/lib/getCalEventResponses.ts:49-52 的註解說「若 guests 欄位被隱藏就不要從 attendees 反推 guests」，反過來讀意味著平常是會從 attendees 反推的——若真是如此，responses 與 attendees 的不一致會被自動吸收，這條就不成立。但那段程式碼我只讀到反推的意圖，沒有讀到完整的 reschedule 寫回路徑（handleNewBooking.ts 在重排時如何重建 attendees），而那條路徑長到無法在這次審查裡窮盡，環境也跑不起來。

**如何確認**：跑一次完整流程：建立 booking → 用 addGuests 加兩個 guest → 由 organizer 對這筆 booking 送出 reschedule → 檢查新 booking 的 attendees 是否仍包含那兩個 guest。或者由熟悉 booking responses 生命週期的維護者直接回答 responses.guests 是否為權威來源。

</details>
