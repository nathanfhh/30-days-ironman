# claude-pty 前端改 Vue 3：工作日誌

> 這份由 agent 代筆，逐次記錄決策、理由、實測與翻車。「我」是執行的 agent，
> 「Nathan」是拍板的人；兩者分開寫，引用時請對回這份日誌而不是憑印象。
> 分支：`frontend-vue3`（自 `a0cf644` 切出，2026-08-25）。計畫本體在競情庫 `analysis/claude-pty-vue3-plan.md`。

## 2026-08-25

### 起點：為什麼要改

Nathan 的動機三條：modern web、SPA 可測、元件化。我先盤現況再回答「難不難」，盤到的數字：

- 前端全部手寫、無建置鏈：Jinja 模板 1,909 行（sessions 1,114／account 651／login 81／base 與 masthead 163）、`app.js` 單檔 2,090 行、`app.css` 2,582 行。
- `app.js` 67 個頂層函式、52 個 `addEventListener`、**19 個 `innerHTML`**、13 個 `createElement`、9 個 `fetch`、1 個 WebSocket。drawer 用 iframe 掛 ttyd。
- API 已經是 JSON（`/api/sessions`、`/api/auth/*`、`/api/users/*`、`/api/prefs`、`/api/catalog`⋯）。模板裡仍有 50 處 Jinja 注入的 server 端狀態（account 28、sessions 13、login 9）。
- 測試：8 支 Playwright e2e（Python sync API）218 個 check；`data-testid` 定義 51 個、e2e 用到 37 個；**另有 29 個綁 DOM 結構的 CSS selector**（`#manifest .manifest__row`、`#pick-ttyd .picker__menu li`⋯）；零 `get_by_role`。
- CI 已裝 node 24（只拿來做 `app.js` 語法檢查）與 playwright chromium；Dockerfile 已是多階段（rust → python）。

我的判斷：難度中；真正的風險是時機（賽期內、文章已描述現版 UI）與 e2e 的四成綁在 DOM 上。Nathan 的判斷：node multistage 沒難度（同意，CI 與 Dockerfile 都已備好）。

### 決策（Nathan 拍板）

1. **賽期內做，做成功就公開**，不等 9/30。
2. **Jinja 注入改 API 天經地義**，每條新 endpoint 都要有測試、實作要正確。純後端、獨立 commit，可先進 main。
3. 截圖 golden **可有寬限**（字體反鋸齒層級的飄動接受）。
4. **用 TypeScript**；oxlint、格式化、Vitest 都要進 run-all.sh 與 CI。
5. BDD（behave／pytest-bdd）**不採用**：我提的理由是讀者是作者與 agent，Playwright 用 testid 加 role 寫的 e2e 本身就是行為規格，多一層 Gherkin 是翻譯不是價值。Nathan 接受。
6. **1:1 還原介面與功能**：做法是階段 4 沿用 `app.css` 一字不改，Vue 只重寫 DOM 與狀態；CSS 若同時改，截圖 golden 會全紅、分不出 bug 與樣式。
7. 舊版必須全程可用：階段 1-3 舊 JS 是運行版；階段 4 兩版並存（route 或環境變數切換）；階段 5 四關全過才刪舊版；`main` 不動。

### 階段

1. e2e 全面 testid 化（29 個 CSS selector 換掉；表單按鈕改 `get_by_role`），不動功能。
1.5 修 drawer resize 時序（見下）。
2. 錄 golden master（舊 UI）：截圖 1280×800 與 390×844、`aria_snapshot`、網路呼叫序列，存 `tests/golden/`；重跑兩次一致才算數。
3. 50 處 Jinja 注入改 API，舊 JS 改吃 API。
4. Vue 3 + Vite + TS，沿用 CSS，Vitest，Dockerfile 加 node 階段，run-all.sh 加 build，不 commit dist。
5. 拆舊、`innerHTML` 歸零、ADR、README。

每階段一個 opus agent、一個 commit、e2e 全綠才進下一階段。diff 範圍寫死在派工 prompt 裡，超出範圍要停下來回報。

### 階段 1.5 為什麼插進來：drawer resize 的時序

Nathan 回報開抽屜時 ttyd 的 resize 常常時機錯，問 Vue 的 `nextTick` 能不能解。

我讀了 `attachSizeSync`／`syncSize`：iframe `load` 後每 100ms 輪詢 `window.term`，掛 `onResize`，套字級逼一次 fit，debounce 300ms 後 `POST /resize`。抽屜本身有 CSS transition（`transitionend`／400ms fallback）。

判斷：**`nextTick` 等的東西不對。**它只保證 DOM patch 刷完，不保證 layout 穩、transition 結束、iframe 內的 JS 跑完。殘留的飄動最可能是 fit 落在抽屜還在滑入的當下，量到中途寬度；抽屜停定後 iframe 尺寸再變，但 ttyd 只綁 `window.resize`，不一定再 fit。

修法（框架無關，舊 JS 就能做）：`ResizeObserver` 掛 `.drawer__frame` 驅動 fit；`POST /resize` 只在「iframe 尺寸穩定 N ms 且抽屜動畫結束（`getAnimations().finished` 或 `transitionend`）」後送最後一次帶 redraw。Vue 版接手時包成 composable，`onMounted` 起、`onUnmounted` 拆；這才是 Vue 帶來的好處，生命週期有地方掛，不是 `nextTick`。

為什麼要在階段 2 之前：不然 golden master 會把飄動錄成「正確行為」。

Nathan：不看 debug log；「就算多等幾十 ms 我也不會發覺，而且不會比現況更糟」；驗證由 agent 自己用 Playwright 做，不找他。

### 其他

- 兩份補圖用的 prompt 檔本來放在 `docs/`，Nathan 指出不可能 commit、也不能進 `.gitignore`（`.gitignore` 會下放到 public，等於公開檔名），改搬到競情庫（非 git）。
- 派工紀律沿用 sessions.py 拆分那次：一個 agent 一刀、跑測試、commit、不 push。

### 階段 1 完成：`8ea1e8f`

- 基線 `run-all.sh quick`：36 支 0 失敗、14 支 docker gate 跳過（含 e2e_flow）。改後跳過清單逐字相同；另跑 `--e2e` 全套，8 支含真 docker 的 e2e_flow 全綠。
- 換掉 142 處／73 種 selector；新增 46 個靜態 `data-testid` 與 7 個動態掛載點（picker／switch 的掛載點被 `mount.className` 吃掉 class，改在工廠函式裡 `mount.dataset.testid = mount.id`）。
- 表單與按鈕改 `get_by_role`；`#login-error:not([hidden])` 這類狀態判斷改 `expect(...).to_be_visible()`。
- **刻意不換的三類**（原則）：ttyd／xterm 自己的 DOM 沒地方補 testid；`data-act` 是 app.js 事件委派的契約不是結構；三組斷言的主詞就是 class 本身（「這個 class 不該存在」），換 testid 等於換斷言。
- 撞到一條真守衛：`test_web.py:271` 的 XSS 正則把 `toast-title` 判成「title 插進 innerHTML」。agent 沒繞（改名）也沒關守衛，改用 dataset 在 innerHTML 之外掛。正則的誤判本身留到 1b 修。
- 1b（同一 agent 接著做）：`paintDays()` 補 `data-in`／`data-edge`；GitLab 標記帶識別欄位不再靠 `i.fa-gitlab`；XSS 正則收緊成只認 `${...}` 插值並加反向 case。

### 階段 1b 完成：`16655d4`

- `paintDays()` 同時寫 `data-in`／`data-edge` 屬性（class 留給 CSS，屬性給測試，註解寫明「兩份的讀者不同」）。
- GitLab 標記：`marks` 三元組改四元組帶 `kind`，渲染成 `data-kind`，PROBE 不再靠 Font Awesome 的 class。
- XSS 守衛抽成 `html_interpolations(fn_src, var)`，只在 `${...}` 內找變數；兩條反向 case（真插值要紅、字面量不誤報）。agent 另外對真 `app.js` 做了變異測試：塞 `${title}` 進 toast 樣板守衛如期紅，證明抓取範圍仍對。守衛收緊後 `toast-title` 搬回 innerHTML 樣板，階段 1 的繞法拆掉。
- quick 36 支 0 失敗、跳過清單與基線逐字相同；`--e2e` 8 支全綠。

### 階段 1.5 派工（同一 agent）

要求先用 Playwright 重現（攔 `POST /resize`、對 `term.cols/rows`、記錄送出時間點相對抽屜動畫結束；可暫時把 transition 拉長放大時序），再修，再連跑 3 次。

### 階段 1.5 完成：`8873988`

**重現的結果推翻了派工時的診斷。** 我（agent）寫了一支逐幀取樣的探針，開抽屜後每個
`requestAnimationFrame` 記下 `frame.clientWidth/clientHeight`、`contentWindow.innerWidth/innerHeight`、
`getBoundingClientRect().left`、`term.cols/rows`，常態 240ms 跑三次、把 transition 拉長到
800ms 再跑兩次。五次的結論一致：

```
+  11ms  client=1295x834  inner=1295x834  term=None    rectLeft=1441
+ 158ms  client=1295x834  inner=1295x834  term=154x49  rectLeft=1279
+ 846ms  client=1295x834  inner=1295x834  term=154x49  rectLeft=145
```

滑入期間**版面尺寸一格都沒動**，只有 rect 的 left 在跑。因為滑入用的是
`transform: translateX()`，而 transform 不影響版面尺寸；`app.css:2224` 本來就寫著這件事
（「用 transform 而不是 right/width：改位置屬性會讓 iframe 裡的終端每一幀重排」）。
所以「fit 量到中途寬度」並沒有發生，五次的最後一發也都與最終 `term.cols/rows` 一致。

第二個量到的事實：**只用 CSS 把面板從 90vw 改成 50vw（完全不碰視窗），iframe 內部確實
收到了 resize，也正確重送了一發。** 既有的事件鏈沒有斷，ResizeObserver 是補強不是救命繩。

**真正站得住的問題是順序**：送出的時機純由 300ms debounce 決定，與抽屜動畫誰先誰後沒有
任何人管。拉長到 800ms 就現形，那一發在動畫結束前 577ms 就送掉了。常態 240ms 下它剛好
落在動畫之後幾十毫秒，那是巧合不是設計，reduced-motion、慢機器、或有人調長動畫都會翻盤。

修法（方向照派工，理由換成量出來的那個）：

- `ResizeObserver` 掛 `.drawer__frame`，盒子一變就往 iframe 丟 resize 逼 fit，並記下
  `lastBoxAt`。註解誠實寫明 Chromium 目前會補發 resize、但那是實作不是規格。
- 送出前兩道閘：`getAnimations().finished` 全數 resolve（沒有動畫就是 `Promise.all([])`，
  reduced-motion 直接過；動畫被取消時 `finished` 會 reject，`.catch` 收成「不必再等」），
  且 `performance.now() - lastBoxAt >= 150`。
- 每次排程給一個 token，等閘門期間有新變化就作廢舊的那一發，「只送最後一次」維持成立。
- `close()` 時 `frameRO?.disconnect()`。
- 保留黏著 redraw 旗標、`healGlyphScale`、字級邏輯、5 秒放棄輪詢、`sizeDebug`。CSS 沒動。
  `sendSize` 從 `setTimeout` 裡抽成具名函式，內容一字未改（diff 大部分是這個位移）。

三次結果：修之前把 `app.js` 還原成 `16655d4` 實跑，新斷言 `FAIL 動畫結束後 -577ms 才送出`；
修之後連跑三次 `PASS 2ms / 3ms / 3ms`，整支 0 fail。quick 36 支 0 失敗、跳過清單與基線
逐字相同；`--e2e` 8 支全綠。

寫斷言時避開一個會做出假綠的坑：不能只 `getAnimations()` 查一次。`data-open` 是下一幀才
打上去的，那一刻多半還沒有動畫在跑，拿到空陣列會讓「動畫結束＝按下去那一刻」，斷言變成
恆真。所以先等它真的出現再等 `finished`，超過二十幾幀還沒有才當成 reduced-motion。

`open_drawer` 從固定睡 700ms 改成等那一發真的到（helper，不是斷言）：送出時機現在由事實
決定，固定秒數在慢機器上會搶在它前面，那種紅燈長得像功能壞了。

**已知沒驗到的一段**：替身終端的 `fit()` 是同步且純依 `window.innerWidth` 算的，天生不會
有「字體到位後 cell 尺寸才變」這一類真 xterm 才有的晚到變化。這次的閘門對那一類也有幫助，
但替身證明不了，所以沒有寫成斷言、也沒有在 commit 訊息裡宣稱。要驗得接真的 ttyd。

### 階段 1.5 完成：`8873988`，而且我的診斷被量出來是錯的

- 我原本的判斷是「fit 落在抽屜滑入中途，量到中途寬度」。agent 寫了逐幀探針（每個 rAF 記 iframe 的 clientWidth／innerWidth／rect.left／term.cols），常態 240ms 三次、拉長到 800ms 兩次，**五次版面尺寸一格都沒動，只有 rect.left 在跑**。原因 `app.css:2224` 自己就寫著：滑入用 `transform: translateX()`，transform 不影響版面尺寸。「中途寬度」這件事不存在。
- 站得住的問題是**順序**：送出時機純由 300ms debounce 決定，跟動畫誰先誰後沒人管。拉到 800ms 就現形：那一發在動畫結束前 577ms 就送了。常態 240ms 下剛好落在動畫之後幾十毫秒，是巧合不是設計；reduced-motion、慢機器、有人調長動畫都會翻盤。
- 另量到：只用 CSS 把面板 90vw 改 50vw（不碰視窗），iframe 內確實收到 resize 並重送一發。既有事件鏈沒斷，ResizeObserver 是補強不是救命繩。
- 修法：ResizeObserver 掛 `.drawer__frame` 逼 fit 並記 `lastBoxAt`；送出前兩道閘：`getAnimations().finished` 全 resolve（無動畫即 `Promise.all([])`，reduced-motion 直接過；被取消時 `.catch` 收成不必再等）且距上次盒子變化 ≥150ms；每次排程帶 token，閘門期間有新變化就作廢舊的；`close()` 時 disconnect。黏著 redraw 旗標、healGlyphScale、字級、5 秒放棄、sizeDebug 全保留；CSS 未動。
- 新 e2e 斷言：測試自己把 transition 拉到 800ms（`add_style_tag`，量完移除），比對最後一發送出時刻與動畫結束時刻。修前 `-577ms`，修後三次 `+2ms／+3ms／+3ms`。避開了一個假綠：`data-open` 下一幀才打上，那一刻 `getAnimations()` 多半是空陣列，若就此算「動畫結束」斷言恆真；所以先等動畫真的出現再等 finished。
- 沒宣稱的：替身終端的 fit 是同步依 innerWidth 算，驗不到真 xterm「字體到位後 cell 尺寸才變」那一類；閘門對它應該有幫助，但沒有斷言。
- 教訓記一條：**先量再判**。我這次是讀碼推理就下了診斷，agent 用探針推翻了它；修法方向沒錯，理由錯了。Day 29 那句「量出來的比查得到的可靠」，在自己身上又應驗一次。

### fable 快審 1／1b

一條中度：`html_interpolations()` 只認 `${...}`，串接與直接指派兩種漏法反而看不見，「收緊」實際是收窄。排 1c 修。其餘六項（斷言未改弱、testid 等價、XSS 面、paintDays 語意、diff 落點、實跑 quick）查過成立。

### fable 快審 1／1b：一條中度發現已修（`1c`）

`html_interpolations()` 為了修誤報而只認 `${...}`，把 `el.innerHTML = "<b>" + title + "</b>"`
與 `el.innerHTML = title` 兩種形狀**放走了**（舊守衛抓得到）。判準改成「該賦值去掉字串
字面量之後仍含 `\btitle\b`」，插值／串接／直接指派三種都抓，`data-testid="toast-title"`
這種只活在字面量裡的仍不誤報。反向 case 從一條擴成三條真陽性加一條不誤報；docstring 列明
已知不涵蓋的形狀（`insertAdjacentHTML`／`outerHTML`、`[^;]*` 被 HTML entity 的分號截斷、
巢狀樣板會多報）。另外對真 `app.js` 逐一注入三種形狀做變異測試，三種都如期紅。

教訓記一筆：**修一條守衛的誤報時要兩個方向一起驗。**只顧誤報那一邊，會把真陽性一起關掉，
而畫面上兩者長得一模一樣（都是綠的）。
