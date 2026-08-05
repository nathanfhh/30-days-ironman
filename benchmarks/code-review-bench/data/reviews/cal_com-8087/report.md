## 審查結論：Request Changes

> Critical 2 · Suggestion 2 · Nit 3 · 未驗證提問 3
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

- **A 風格**（未通過）：getCalendarCredentials 的回傳欄位名稱 calendar 已經名實不符（見 F-004）。其餘簽章變更（getCalendar、deleteEvent、getVideoAdapters 都補上了 Promise<...> 回傳型別）與命名一致。
- **B 簡潔**（未通過）：留下一行指向不存在目錄的註解掉的 import（F-006）。其餘部分沒有重複邏輯或過度設計；videoClient.ts 把 reduce 改寫成 for...of 反而更好讀。
- **D API 慣例**（不適用）：這次改動沒有新增或修改任何 HTTP endpoint、URL、HTTP verb 或 validation schema；packages/trpc/server/routers/viewer/bookings.tsx 只改了 handler 內部實作，router 的輸入 schema 未動。
- **E 架構**（未通過）：packages/app-store/index.ts 的新寫法在模組求值當下就把 28 個 import() 全部發動，等於沒有延後任何東西（F-001）。
- **G 測試**（未通過）：全 repo 只有 21 個 test 檔，這次改動的 12 個檔案沒有任何一個被測試覆蓋，PR 也沒有新增測試；packages/app-store/index.ts 那份手維護的 28 筆清單同樣沒有任何守門測試（F-005）。
- **H 非 Python 檔**（未通過）：diff 全部是 TypeScript，H 條列中的 Vue / Dockerfile / nginx.conf / docker-compose / Alembic / UI 分支渲染都不適用；以 TypeScript 的一般最佳實務檢視，videoClient.ts 的 await-in-loop 在 F-001 修正之後會變成真的序列化等待（F-007）。其餘 TypeScript 面向的問題歸入 A / B / E / I。
- **I 回溯分析**（未通過）：getCalendar 由同步改為 async 之後，三處 Array.prototype.forEach 的 callback 變成 async，錯誤傳遞契約被打斷（F-002）；handleCancelBooking 內兩個相鄰分支對同一個 pattern 採取了相反的處理（F-003）。已用 grep 逐一列舉 getCalendar（12 個呼叫端）、appStore[...]（6 個）、getCalendarCredentials（3 個）、getVideoAdapters（7 個）的全部呼叫端，除上述之外皆已正確 await。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個時機做？**：這次改動讓 booking 建立、取消、改期與付款退款這條主線上約 30 個函式變成 async，但 PR 沒有附上任何 before/after 的量測（cold start、server bundle 大小、記憶體），而且如 F-001 所述目前的寫法並沒有真的延後載入。在「代價已經付了、效益還沒被證明」的狀態下合併，之後要退回來很貴。建議先補一組數字再決定時機。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | in_diff 0、outside_diff 0 |
| ty | 略過 | ty 未安裝（不在 PATH 上）。本次 diff 全為 TypeScript，ty 本來也不適用。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。本次 diff 12 個檔案全部是 .ts/.tsx，這代表這次審查沒有任何自動化的 JavaScript/TypeScript lint 覆蓋，F-002、F-003 那類 floating promise 問題完全靠人工判讀。 |
| trivy | 略過 | trivy 未安裝（不在 PATH 上），略過相依套件漏洞、設定檔與 secret 掃描。本次 diff 沒有動到任何 lockfile 或設定檔。 |
| opengrep | 略過 | opengrep 未安裝，且 NCR_OPENGREP_RULES 指向的規則目錄不存在，兩個條件都不成立，略過 SAST 掃描。 |
| codegraph | 略過 | codegraph 未安裝，Phase 3 的呼叫路徑列舉與 dimension I 的 caller 清單全部改用 grep 逐一列舉並驗證。 |
| ncr-fresh-eyes | 略過 | 本執行環境沒有 Agent/Task 工具，無法派送任何 subagent，因此 Phase 3 的 fresh eyes 沒有執行，也沒有在主 agent 內模擬。這代表本報告的所有觀察都經過本 skill 的框架，缺少一個未被框架塑形的視角。 |
| ncr-quality-check | 略過 | 同上：沒有 Agent/Task 工具，Phase 4 step 3 的品質檢查 subagent 沒有執行，報告只通過 report_model.py 的結構驗證。 |

### Critical

#### F-001 appStore 的 28 個 import() 在模組求值當下就全部發動，沒有任何東西被延後載入 — `packages/app-store/index.ts:1`

面向 E 架構 · Critical

**問題**：`import("./applecalendar")` 是一個「呼叫」，不是一個「描述」。物件字面值被求值時，28 個屬性值會依序被計算，也就是 28 個動態 import 全部在 `@calcom/app-store` 第一次被 import 的那一刻就啟動。改動前是 28 個 static import，改動後是 28 個立即啟動的 promise —— 載入的總量一模一樣，只是從同步搬到了 microtask。第一次 booking 只用到 googlecalendar 時，salesforce、hubspot、zohocrm、stripepayment 等 27 個模組仍然會被載入與求值。

第二個後果比較隱蔽：這 28 個 promise 只有被實際用到的那一個會被 await（六個 `appStore[...]` 呼叫端見 evidence）。其餘 27 個是沒有任何 handler 觀察的 floating promise，任何一個 app 模組在求值期間拋錯，都會變成 unhandled rejection —— 而改動前同樣的失敗會在 import 當下同步拋出，落在該次 request 的錯誤處理裡。錯誤浮現的位置從「某條路由 500」搬到了「行程外的 unhandled rejection」（實際會不會終止 process 取決於 runtime 的 unhandledRejection 政策，見 Q-001）。

這件事的分量在於它不可逆：合併之後，12 個檔案、約 30 個函式的 async 傳染已經付出去了，而換來的東西並不存在。已檢查過反證：diff 內沒有任何地方把這些 import 包成延後求值的形式；package.json 的 `sideEffects: false` 也不會阻止物件字面值求值。

**證據**：
- `packages/app-store/index.ts:1`
- `packages/app-store/index.ts:3`
- `packages/app-store/index.ts:31`
- `packages/app-store/_utils/getCalendar.ts:15`
- `packages/core/videoClient.ts:26`

**修復方向**：把值改成 thunk，讓 import 在被索引時才發動。呼叫端已經全部是 `await appStore[key]`，只要多一組括號：

```ts
// packages/app-store/index.ts
const appStore = {
  applecalendar: () => import("./applecalendar"),
  caldavcalendar: () => import("./caldavcalendar"),
  // ...
};
```

```ts
// packages/app-store/_utils/getCalendar.ts:15
const calendarApp = await appStore[calendarType.split("_").join("") as keyof typeof appStore]();
```

六個呼叫端（getCalendar.ts:15、videoClient.ts:26、deletePayment.ts:16、handlePayment.ts:26、handleCancelBooking.ts:589、bookings.tsx:967）都是同一種改法。注意 key 可能不存在於 appStore（`dirName` 來自 DB），所以呼叫前要先取出再判斷：`const loader = appStore[key]; const app = loader ? await loader() : undefined;`，否則 `undefined()` 會直接 TypeError（現行 `await undefined` 則是安全地得到 undefined）。如果希望同一個 app 只載入一次，thunk 外面再包一層 memo 即可。

#### F-002 三處 forEach 的 callback 被改成 async，外層 try/catch 對這段程式碼已經失效 — `packages/app-store/vital/lib/reschedule.ts:125`

面向 I 回溯分析 · Critical

**問題**：`Array.prototype.forEach` 會把 callback 的回傳值丟掉。callback 原本是同步的，所以 `getCalendar(...)` 內部同步拋出的例外會直接冒泡出 forEach —— 在 vital/wipemycalother 落進 reschedule.ts:124 的 try/catch 被 log 下來，在 bookings.tsx 則會讓整個 tRPC mutation 失敗、由 client 看見。改成 `async (bookingRef) => {}` 之後，同一個例外變成一個被 forEach 丟棄的 rejected promise：try/catch 對這段 callback 完全不會觸發，mutation 也會照常回傳成功。

這不是理論上的路徑。`getCalendar` 會 `new CalendarService(credential)`，而 GoogleCalendarService 的 constructor 在 CalendarService.ts:38 同步執行 `googleCredentialSchema.parse(credential.key)`；只要 credential.key 的形狀不符（被撤銷、被改寫、舊格式的資料列），就是一個同步的 ZodError。改動前這個錯誤會被記錄下來，改動後它靜靜消失，而使用者收到的是「改期成功」——舊的行事曆事件卻還在。

已檢查過反證：packages/config/eslint-preset.js:15-31 沒有開啟 `@typescript-eslint/no-floating-promises` 或 `no-misused-promises`，CI 不會攔下這個 pattern；作者本人在同一份 diff 的 handleCancelBooking.ts:477-483 已經把完全相同的 pattern 改寫成 for...of，所以正確形狀在這次改動裡是有先例的。

**證據**：
- `packages/app-store/vital/lib/reschedule.ts:125`
- `packages/app-store/vital/lib/reschedule.ts:124`
- `packages/app-store/wipemycalother/lib/reschedule.ts:125`
- `packages/trpc/server/routers/viewer/bookings.tsx:553`
- `packages/app-store/googlecalendar/lib/CalendarService.ts:38`

**修復方向**：三處都改成 for...of 並逐一 await（要序列）或 `await Promise.all(map(...))`（要並行），讓錯誤重新回到 try/catch 內：

```ts
try {
  for (const bookingRef of bookingRefsFiltered) {
    if (!bookingRef.uid) continue;
    if (bookingRef.type.endsWith("_calendar")) {
      const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
      await calendar?.deleteEvent(bookingRef.uid, builder.calendarEvent);
    } else if (bookingRef.type.endsWith("_video")) {
      await deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
    }
  }
} catch (error) {
  if (error instanceof Error) logger.error(error.message);
}
```

bookings.tsx:553 同一份改寫（該處的 deleteEvent 多帶一個 `bookingRef.externalCalendarId`）。另外建議順手在 packages/config/eslint-preset.js 打開 `@typescript-eslint/no-misused-promises`，這個規則正是為了攔截 forEach + async。

<details>
<summary>Suggestion（2）</summary>

#### F-003 handleCancelBooking 兩個相鄰分支對同一個 pattern 做了相反的處理，recurring 分支的刪除結果收不進 apiDeletes — `packages/features/bookings/lib/handleCancelBooking.ts:460`

面向 I 回溯分析 · Suggestion

**問題**：這份 diff 把 else 分支（handleCancelBooking.ts:477-483，非 recurring、舊資料的路徑）從 `.forEach((credential) => ...)` 改寫成 for...of，這是對的。但 15 行之上的 recurring 分支（:460）維持 `.forEach(async (credential) => ...)`，而且在 callback 內把結果 push 進 apiDeletes（:467）。apiDeletes 在 :652 由 `await Promise.all(prismaPromises.concat(apiDeletes))` 收掉；因為 forEach 不等待 callback，那些 push 幾乎必然發生在 :652 讀取 apiDeletes 之後，等於 recurring event 的行事曆刪除既沒有被等待也沒有被觀察。順帶一提 :467 push 進去的是已經 await 完的值而不是 promise，就算時序對了 Promise.all 也拿不到任何東西。

這個問題在改動前就存在（原本的 callback 已經是 async），所以不列為 regression；但這份 diff 正好動到 :461 那一行，而且把正確寫法放在 15 行之外，留下兩個相鄰、對同一件事採取相反做法的分支——下一個維護者只會更困惑。已確認 apiDeletes 的兩個消費點（:617 的 `await apiDeletes` 其實是 await 一個陣列、等同 no-op，與 :652）。

**證據**：
- `packages/features/bookings/lib/handleCancelBooking.ts:460`
- `packages/features/bookings/lib/handleCancelBooking.ts:467`
- `packages/features/bookings/lib/handleCancelBooking.ts:477`
- `packages/features/bookings/lib/handleCancelBooking.ts:652`

**修復方向**：把 recurring 分支改成與 else 分支一致的 for...of，並且 push promise 而不是已解析的值：

```ts
const calendarCredentials = bookingToDelete.user.credentials.filter((c) =>
  c.type.endsWith("_calendar")
);
for (const credential of calendarCredentials) {
  const calendar = await getCalendar(credential);
  for (const updBooking of updatedBookings) {
    const bookingRef = updBooking.references.find((ref) => ref.type.includes("_calendar"));
    if (!bookingRef) continue;
    const { uid, externalCalendarId } = bookingRef;
    apiDeletes.push(calendar?.deleteEvent(uid, evt, externalCalendarId) as Promise<unknown>);
  }
}
```

如果不想在這個 PR 擴大範圍，至少加一行註解指出這裡是已知待修，並開一張 issue；但兩個分支寫法不一致本身就值得在合併前拉齊。

#### F-004 getCalendarCredentials 仍是同步函式，但回傳物件的 calendar 欄位現在是一個 Promise — `packages/core/CalendarManager.ts:23`

面向 A 風格 · Suggestion

**問題**：getCalendar 變成 async 之後，CalendarManager.ts:28 的 `const calendar = getCalendar(credential)` 得到的是 `Promise<Calendar | null>`，卻仍然以 `calendar` 這個名字放進回傳物件。函式簽章沒有變成 async，型別別名 `ReturnType<typeof getCalendarCredentials>`（:124 還在用）也照舊，從外面完全看不出這個欄位已經換了語意。

目前沒有壞掉：三個呼叫端（packages/trpc/server/routers/viewer.tsx:340、viewer.tsx:415、apps/web/pages/api/availability/calendar.ts:83）都是把結果原封不動交給 getConnectedCalendars，而 getConnectedCalendars 在 :48 有正確 await，且 await 位在 try 內，rejection 會被接住。這一點已逐一 grep 確認過。

問題在於這是一個等著被踩的接口：欄位叫 calendar，型別是 Promise，任何人寫 `if (item.calendar)` 都會拿到永遠為真的 promise 並靜靜走錯分支——而 `Calendar | null` 的舊語意正是設計成用真假值判斷的（:51 的 `if (!calendar)` 就是這樣寫的）。

**證據**：
- `packages/core/CalendarManager.ts:23`
- `packages/core/CalendarManager.ts:28`
- `packages/core/CalendarManager.ts:48`
- `packages/core/CalendarManager.ts:124`

**修復方向**：兩個方向擇一。（a）讓 getCalendarCredentials 也變成 async，在裡面收斂：

```ts
export const getCalendarCredentials = async (credentials: Array<CredentialPayload>) =>
  Promise.all(
    getApps(credentials)
      .filter((app) => app.type.endsWith("_calendar"))
      .flatMap((app) =>
        app.credentials.flatMap((credential) =>
          app.variant === "calendar"
            ? [getCalendar(credential).then((calendar) => ({ integration: app, credential, calendar }))]
            : []
        )
      )
  );
```

三個呼叫端補上 await 即可。（b）成本更低：欄位改名為 `calendarPromise`，讓型別在名字上就講清楚，getConnectedCalendars:48 改成 `const calendar = await item.calendarPromise;`。

</details>

<details>
<summary>Nit（3）</summary>

#### F-005 手維護的 28 筆 app 清單沒有守門測試 — `packages/app-store/index.ts:1`

面向 G 測試 · Nit

**問題**：packages/app-store/index.ts 的 28 筆是人工維護的，而隔壁 apps.server.generated.ts 是 `yarn app-store:build` 產生的；兩份清單描述的是同一組 app，但只有其中一份會自動跟著目錄走。本次改動沒有動到成員（已用 `git show ba9688a04a83:packages/app-store/index.ts` 比對過 key 集合，28 個完全相同），所以不是 regression；但整份 diff 把這份清單重寫了一次，正是加上守門測試最便宜的時機。目前 repo 內 21 個 test 檔沒有一個覆蓋到本次改動的 12 個檔案。

**證據**：
- `packages/app-store/index.ts:1`
- `packages/app-store/apps.server.generated.ts:5`

**修復方向**：加一個小測試把清單釘住，例如比對 `Object.keys(appStore)` 與 appStoreMetadata（packages/app-store/appStoreMetaData.ts）中屬於 calendar/video/payment/crm/analytics 類別的 dirName 集合，或直接比對 packages/app-store/ 下的目錄。長期一點的方向是讓 index.ts 也由 app-store-cli 產生（packages/app-store-cli/src/build.ts:310 已經在產 apps.server.generated.ts），手維護的那一份就可以移除。

#### F-006 註解掉的 example 條目指向不存在的目錄 — `packages/app-store/index.ts:2`

面向 B 簡潔 · Nit

**問題**：`// example: import("./example"),` 指向的 packages/app-store/example 不存在；改動前的版本註解掉的是 `./_example`，而 packages/app-store/_example 同樣不存在（已確認兩個目錄都沒有）。這行註解在改寫時被一併翻譯成新語法，等於把一段已經失效的死程式碼又維護了一次。

**證據**：
- `packages/app-store/index.ts:2`

**修復方向**：直接刪掉這一行。真的需要一個範例入口時，app-store-cli 產生的 template 才是可靠的來源。

#### F-007 getVideoAdapters 在迴圈內 await，F-001 修好之後會變成真的序列載入 — `packages/core/videoClient.ts:21`

面向 H 非 Python 檔 · Nit

**問題**：reduce 改寫成 for...of 讓這段好讀很多，這是進步。不過 `await appStore[appName]` 放在迴圈內：今天因為所有 promise 早在模組求值時就啟動了（F-001），每次 await 都是立即完成，所以沒有代價；一旦 F-001 改成 thunk，同一段程式碼就會變成一個一個依序載入 app 模組。呼叫端多半只傳一個 credential（videoClient.ts:52、111、144、162、185、206），只有 getBusyVideoTimes（:39）會傳多筆，影響有限，所以列為 Nit。

**證據**：
- `packages/core/videoClient.ts:21`
- `packages/core/videoClient.ts:26`

**修復方向**：改成先平行取模組再組裝：

```ts
const modules = await Promise.all(
  withCredentials.map((cred) => appStore[cred.type.split("_").join("") as keyof typeof appStore])
);
return modules.flatMap((app, i) =>
  app && "lib" in app && "VideoApiAdapter" in app.lib
    ? [(app.lib.VideoApiAdapter as VideoApiAdapterFactory)(withCredentials[i])]
    : []
);
```

若採用 F-001 的 thunk 版本，map 內改成呼叫 loader 即可。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 在 cal.com 實際部署的 runtime（Next.js server / Vercel serverless）上，一個沒有被任何人 await 的 rejected promise 會被吞掉、被記錄，還是會終止 process？

面向 E 架構

**背景**：F-001 已經確認 28 個 import() 中有 27 個在任何一次 request 內都不會被 await。剩下的問題是後果有多大：Node 15 之後 `--unhandled-rejections` 的預設值是 throw（等同 uncaught exception），但 Next.js 在某些版本會自行註冊 process 級的 unhandledRejection handler，Vercel 的 runtime 也可能另有設定。本機沒有安裝 node_modules，也不能連外，無法實測，所以不把它併進 F-001 的嚴重度理由裡。

**如何確認**：在 staging 讓其中一個 app 模組在求值時故意拋錯（例如在 packages/app-store/giphy/index.ts 頂層 throw），觀察 server 是整個掛掉還是只有該路由受影響；或直接確認部署時的 Node 版本與 `--unhandled-rejections` 旗標、以及所用 Next.js 版本是否註冊了 handler。

#### Q-002 這次改動對 cold start 時間與 server bundle 大小的實際影響是多少？

面向 E 架構

**背景**：PR 標題與 commit message 都只說明做法，沒有給出任何量測。以 webpack 的 server build 來說，每個 `import()` 會切成獨立 chunk，但 chunk 檔仍然全部包進 serverless function，所以 function 大小預期不變；模組求值的工作量也沒有減少，只是從同步搬到 microtask。也就是說，即使不考慮 F-001，這個改法可量測的效益是什麼並不清楚。

**如何確認**：在 merge base（ba9688a04a83）與這個分支上各跑一次 `next build`，比較 `.next/server` 的大小與 booking 相關 API route 的 cold start p50/p95；把數字附進 PR 描述。

#### Q-003 全 repo 的 `tsc --noEmit` 在這個分支上是否仍然通過？特別是六個 `await appStore[...]` 位置上那個 28 個 module namespace 組成的 union 型別。

面向 I 回溯分析

**背景**：本機沒有 node_modules，oxlint 與 ty 都未安裝（見掃描區），唯一跑得動的 ruff 對 TypeScript 沒有意義，所以整份 diff 沒有經過任何型別檢查或 JS/TS lint。人工判讀上，`await appStore[k]` 得到的是 28 個 module namespace 的 union，接著靠 `"lib" in app` 做 narrowing；這在型別上成立，但 union 規模與 `in` narrowing 組合起來是 TypeScript 已知的編譯效能敏感點。

**如何確認**：在 CI 上跑 `yarn type-check` 與 `yarn lint`，並比較 merge base 與本分支的 tsc 編譯時間（`tsc --extendedDiagnostics`）。

</details>
