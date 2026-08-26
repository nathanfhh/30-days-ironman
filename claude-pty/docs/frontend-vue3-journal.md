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

### 階段 1.5 完成：`323a573`（原 8873988，amend 補日誌）

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

⚠ 那個 2 到 3ms **不是安全邊際**，是量測本身的延遲（動畫 finished 的回呼、Promise 排程、
route handler 記時間，加起來就是這個量級）。修完之後送出是**因果上**排在停定之後，不是
「剛好晚了幾毫秒」；如果哪天有人把它讀成邊際、想靠調大它來解別的問題，那是誤讀。

寫斷言時避開一個會做出假綠的坑：不能只 `getAnimations()` 查一次。`data-open` 是下一幀才
打上去的，那一刻多半還沒有動畫在跑，拿到空陣列會讓「動畫結束＝按下去那一刻」，斷言變成
恆真。所以先等它真的出現再等 `finished`，超過二十幾幀還沒有才當成 reduced-motion。

`open_drawer` 從固定睡 700ms 改成等那一發真的到（helper，不是斷言）：送出時機現在由事實
決定，固定秒數在慢機器上會搶在它前面，那種紅燈長得像功能壞了。

**已知沒驗到的一段**：替身終端的 `fit()` 是同步且純依 `window.innerWidth` 算的，天生不會
有「字體到位後 cell 尺寸才變」這一類真 xterm 才有的晚到變化。這次的閘門對那一類也有幫助，
但替身證明不了，所以沒有寫成斷言、也沒有在 commit 訊息裡宣稱。要驗得接真的 ttyd。

主 agent 的教訓（教訓記一條：**先量再判**。我這次是讀碼推理就下了診斷，agent 用探針推翻了它；修法方向沒錯，理由錯了。Day 29 那句「量出來的比查得到的可靠」，在自己身上又應驗一次。）

### fable 快審 1／1b：一條中度發現已修（`1c`）

`html_interpolations()` 為了修誤報而只認 `${...}`，把 `el.innerHTML = "<b>" + title + "</b>"`
與 `el.innerHTML = title` 兩種形狀**放走了**（舊守衛抓得到）。判準改成「該賦值去掉字串
字面量之後仍含 `\btitle\b`」，插值／串接／直接指派三種都抓，`data-testid="toast-title"`
這種只活在字面量裡的仍不誤報。反向 case 從一條擴成三條真陽性加一條不誤報；docstring 列明
已知不涵蓋的形狀（`insertAdjacentHTML`／`outerHTML`、`[^;]*` 被 HTML entity 的分號截斷、
巢狀樣板會多報）。另外對真 `app.js` 逐一注入三種形狀做變異測試，三種都如期紅。

教訓記一筆：**修一條守衛的誤報時要兩個方向一起驗。**只顧誤報那一邊，會把真陽性一起關掉，
而畫面上兩者長得一模一樣（都是綠的）。

### fable 快審 1.5：可進下一階段，一條低度順手補進 `1c`

`drawerSettled()` 的 `getAnimations()` 沒有濾種類。面板上哪天多一個
`animation: ... infinite`（呼吸燈、脈動、旋轉的載入圖示），它的 `finished` 永遠不會
resolve，`/resize` 從此再也送不出去。這種壞法最惡劣的地方是**肇因與症狀完全不相干**：
改的是一條 CSS 裝飾，壞掉的是容器裡的 TTY 尺寸，加裝飾的人不會有任何理由懷疑到自己。

改成只等 `transitionProperty === "transform"` 的那些過渡（`transitionProperty` 只有
CSSTransition 有，CSSAnimation 給的是 `animationName`；`instanceof` 只是再確認一次）。
`close()` 那邊的 `transitionend` 早就是濾 `propertyName === "transform"` 的，同一條規矩。

補了一條斷言把它釘住：測試自己灌一個無限動畫進面板（`add_style_tag`，`app.css` 不動），
開抽屜後要照樣送得出去。拿掉濾網實跑是 `FAIL 收到 0 發`，裝回去是 `PASS 收到 1 發`。

另外兩條低度沒改，理由記著：`attachSizeSync` 裡 term 已存在時不檢查 `closing` 只是白工
（多跑一次無害的量測，不會送出，因為送出那條路自己會檢查）；e2e 那個 2 到 3ms 是量測延遲
不是安全邊際（上面已補註）。

### 階段 3 完成：Jinja 注入改 API

**只加出口，不動模板。** 這一階段的 diff 一行都沒有落在 `.html` 與 `app.js` 上。舊版此刻
仍然照舊由 Jinja 注入、照舊運作，新開的兩條 API 只是把同一批事實再開一個 JSON 出口。
階段 4 的 Vue 版才會改成吃它們。

#### 盤點：50 處注入其實只有 11 種事實

模板裡 `{{ }}` / `{% %}` 一共 50 處（account 28、sessions 13、login 9），但重複的很多
（`min_password_length` 出現三次、`gitlab_proxy_error` 三次、`credentials[default_cli]` 三次）。
去重之後是 11 種伺服端事實，其中**四種已經有 API 給得出來**：

| 事實 | 模板裡的樣子 | 來源 | 出口 |
|---|---|---|---|
| `user.username` / `user.is_admin` | 招牌的 whoami、兩頁的 `const isAdmin` | `g.user` | **既有** `/api/auth/me` |
| `gitlab_pat_set` | 帳號頁 chip／placeholder／清除鍵 | `auth.get_user()` | **既有** `/api/auth/me` 的 `gitlab_pat_configured` |
| `claude_models` | sessions 的 `CLAUDE_MODELS` | `config.CLAUDE_MODELS` | **既有** `/api/catalog` 的 `models[].slug` |
| 管理員清單／ttyd 實況 | 帳號頁兩張表 | JS 本來就在打 API | **既有** `/api/users`、`/api/users/options`、`/api/ttyd/inspect` |
| `behind_proxy` | `<html data-behind-proxy>` | `config.BEHIND_PROXY` | 新開 `/api/bootstrap` |
| `persist_dir()` | `<html data-persist-dir>` | `config.DATA_BIND` | 新開 `/api/bootstrap` |
| `build_info()` | 頁尾整排版本與建置時間 | `version.summary()` | 新開 `/api/bootstrap` |
| `art` | 登入頁左下角插畫 | `web.LOGIN_ART` | 新開 `/api/bootstrap` |
| `credentials` / `default_cli` | 招牌的 `#cred-data` 與 `data-cli` | `credentials_state(uid)` | 新開 `/api/account/bootstrap` |
| `name_max` / `username_max` / `min_password_length` | 三個表單的長度限制 | `config` | 新開 `/api/account/bootstrap` |
| `gitlab_enabled` / `gitlab_host` / `gitlab_proxy_error` | 帳號頁整個 GitLab 區塊 | `config` ＋ `auth.gitlab_proxy_error(uid)` | 新開 `/api/account/bootstrap` |

`asset_url()` 那幾條不算注入（是靜態資源網址，階段 4 由 Vite 接手）；`active` 也不算
（SPA 的 router 自己就知道現在在哪一頁）。

#### 為什麼是兩條，而且分界線是 gate 不是頁面

派工時的預設是「一條 bootstrap」。盤完之後改成兩條，理由是**登入頁也要拿東西**：
`<html>` 的兩個屬性、頁尾那排版本、左下角那張插畫，未登入者今天就看得到（頁尾在
base.html，三頁共用）。全部塞進一條需登入的 endpoint，SPA 的登入畫面會少一塊；照
「哪一頁要用」去切又會切出兩條內容重疊的東西。所以分界線取 gate 本身：

- `GET /api/bootstrap`（**公開**）：`behind_proxy`、`persist_dir`、`build`、`login_art`。
  公開是**照著登入頁現在的樣子畫的，不是放寬**：這四件今天就印在未登入者拿得到的
  `/login` 上。要收緊得先收緊登入頁，不是先收緊這一條；兩邊不一致的話，收緊的那一邊
  只會讓人以為收緊了。
- `GET /api/account/bootstrap`（**需登入**）：`default_cli`、`credentials`、`limits`、`gitlab`。
  名字取 account 是因為它回答的都是「這個帳號的處境」；招牌兩頁都有，所以**兩頁都打它**，
  它不是帳號頁專用。

也考慮過「擴充 `/api/auth/me`」，否決：那條是 SPA 殼進頁判斷「還算不算登入」的熱路徑
（計畫決策 3），把部署設定與憑證狀態掛上去等於讓一個身分查詢背著整頁的資料；而且它
在 401 後面，登入頁根本走不到。

#### 其他決定

- **已經有出口的一律不重複。** `user`、`gitlab_pat_configured`、模型清單、ttyd 選項都不
  夾帶。重複一份的代價不是流量，是兩份會分岔，而分岔的那天沒有人會發現。測試裡有反向
  的一條：`/api/auth/me` 必須真的給得出 `gitlab_pat_configured`，否則委派出去的東西沒人
  接，SPA 會少一塊而測試還是綠的。
- **`limits` 不隨權限改變形狀。** `username_max` 今天只印在帳號頁的管理員區塊裡，這裡卻
  對所有登入者都給。它是表單長度常數不是誰的資料，而形狀依角色而異會把一個 `undefined`
  分支推給每一個取用它的地方。該 gate 的是「那個區塊畫不畫」，答案在 `is_admin`。
  這一階段因此**沒有新的管理員限定資料**，403 也就無從測起。帳號頁要的管理員資料
  （清單、選項、ttyd 實況）三條既有 endpoint 都已經是 `@admin_only`。
- **`build.built_at` 提到最外層。** 它是整包的屬性、不屬於任何一個模組（base.html 也是
  這樣讀的：`modules[0].built_at` 之後單獨畫一行）。留在列裡的話前端遲早會有人把它畫成
  「claude-pty 這一列的時間」。
- **`config.DEFAULT_CLI` 補上了。** `app.js` 的註解本來就寫著「data-cli 由伺服端以
  `config.DEFAULT_CLI` 種下」，但那個常數不存在，`web.py` 自己寫死一份 `"claude"`。
  現在它真的存在，`credentials.py`、`web.py`、新 endpoint 三處讀同一個。
- **`web.login_art()` 抽出來**，登入頁與 API 共用同一個決定（各寫一份 `random.choice` 的話，
  哪天有人給其中一邊加了條件，另一邊不會跟著變）。
- 秘密照既有做法：token 與 PAT 明文、密文都不出去，`credentials` 只有狀態三態與文案。

#### 測試

新增 `tests/test_bootstrap.py`，**76 條 check**，八節：gate（公開／401）、兩條的形狀與值、
不重複既有出口（含反向）、權限（形狀不隨角色變、不夾帶別人的東西）、憑證狀態隨 DB 走且
明文不外流、GitLab 三態、**對照模板**。

最後一節是重點：把 HTML 抓下來、用正則把注入的值挖出來、跟 JSON 對。15 條逐項比
`data-behind-proxy`、`data-persist-dir`、名稱欄 `maxlength`、新增帳號欄 `maxlength`、
`MIN_PW`、`data-cli`、`#cred-data`、`isAdmin`、`gitlabEnabled`、`CLAUDE_MODELS`、頁尾模組名、
插畫來源、GitLab 主機與代理錯誤。**那一節紅了就代表兩邊已經分岔**，而分岔正是這次要防的事。
挖不到注入點時它記一筆失敗而不是靜靜跳過（`_one()`）。

`run-all.sh` **沒有動**：它的清單是 `for f in tests/test_*.py tests/e2e_*.py` 這個 glob，
新檔自動被撿走（跑的支數 36 → 37 就是證據）；`NEEDS_DOCKER` / `NEEDS_TTYD` / `NEEDS_LINUX`
那幾份是「缺什麼就跳過」的 gate，這支一個都不需要，加進去反而會讓它被跳掉。

驗證：`tests/run-all.sh`（不帶參數＝quick）跑 37 支 0 失敗，跳過 14 支、清單與基線逐字相同
（含 7 支 e2e 在內，模板與 `app.js` 沒動所以本來就不該有變化）。`ruff@0.16.3 check .` 與
CI 那條 `git ls-files -co --exclude-standard '*.py' | xargs ruff format --check` 都綠。

#### 順手修的一件事（不是這階段的產物）

`tests/test_web.py` 在 `16655d4` 之後就沒有通過 `ruff format --check`（兩行引號風格），
CI 的格式閘在這個分支上本來是紅的。它落在本階段允許的 diff 範圍內、修法是跑一次
formatter、對行為零影響，所以一起收掉了。`frontend-vue3` 那邊若也修了同一處，衝突就是
這兩行。

### fable 快審階段 3：三處一行修正

`46850d9` 審過的結論是可 merge，但點出三處，都不是形狀問題而是**同源問題**。

1. **`credentials_state()` 的字典鍵還是字面量 `"claude"`。** 上一刀宣稱「三處同源」，但那
   只做到了 `default_cli` 這一半：鍵仍然寫死，改掉 `config.DEFAULT_CLI` 之後會變成
   「用 A 當鍵、拿 B 去查」。而且原本那條測試（`set(credentials) == {config.DEFAULT_CLI}`）
   **是恆真的**，兩邊讀同一個常數，等於拿它自己比自己，看不出這件事。
   修法：鍵改讀常數；`_CLAUDE_BASE` 這個模組層常數改成 `_claude_base()`，`cli` 在呼叫時
   才讀（在 import 時抄一份的話，測試同樣驗不出分岔）。`sessions.py` 的 re-export 跟著改名。
   測試改成把常數 monkeypatch 成 `not-claude` 跑一次，四處（字典鍵、狀態裡的 `cli`、
   `default_cli`、招牌的 `data-cli`）都要跟著走。
   **變異驗證**：把鍵改回字面量，這一節如期三條紅，其中招牌那條是 `_masthead.html` 當場
   `UndefinedError`（它拿 `default_cli` 去 `credentials` 裡查，查不到就炸）。那個炸法本身
   就是答案，所以測試把它接住印成一行說明再記 FAIL，不讓它把整支測試帶走，而
   monkeypatch 用 `try/finally` 還原：不然後面幾十條會對著一個被改壞的常數跑。

2. **`/api/bootstrap` 補 `Cache-Control: no-store`**，只動這條，不放寬 `_security_headers`。
   那支對 JSON 一律不設 Cache-Control 是對的（其餘 API 是每次都問的即時狀態），為一條
   端點把整個 `/api/*` 標成不可快取，是拿全域的決定解局部的問題。這條特別的理由有二：
   它是**公開**的 GET（中間任何一層都可能想順手存一份），而它回的兩件事都會過期
   （`login_art` 每次要換一張、`build` 在改版後必須跟著變，而頁尾存在的理由正是回答
   「線上跑的到底是哪一版」）。**變異驗證**：拿掉那一行，斷言如期紅。

3. **`web.login_page()` 的死參數 `min_password_length` 刪掉**（沒碰模板）。它傳著卻沒有人
   用，而沒人用的參數不是無害的：它讓下一個人以為登入頁需要這個值，於是把它加進**公開**
   的 `/api/bootstrap`，那才是真的放寬曝光面。原地留了註解講這件事。

順手：「權限」那一節的六條 `admin_only not in acct` 查的是**頂層鍵**，同樣恆真（沒有人會
把 `users` 放在頂層，真要漏也是漏在巢狀結構裡）。改查整段 JSON 字串 `_acct_raw`。

這三處加起來的教訓是同一個：**斷言的兩邊如果來自同一份值，它就不是斷言。** 要嘛換一邊
的來源（拿 HTML 對 JSON），要嘛動一動輸入再看它會不會紅。

驗證：76 → 82 條 check、0 失敗；`tests/run-all.sh` 37 支 0 失敗，跳過清單逐字相同；
`ruff@0.16.3 check .` 與 CI 那條 format 閘都綠。

### 階段 2 完成：錄 golden master

**十二個場景**（`tests/golden_scenes.py` 的 `SCENES`，錄與比共用同一份定義，所以「錄的」
與「比的」不可能是兩個狀態）：

`login-empty`／`login-error`／`sessions-empty`／`sessions-list`／`sessions-history`／
`sessions-filters`／`sessions-rangepick`／`sessions-settings`／`sessions-toast`／
`drawer-open`／`account-user`／`account-admin`

資料是刻意鋪開的，不是四筆一樣的假資料：有名字的與沒名字的（標題退回 sid）、就緒與
「container 在跑但 driver 沒就緒」、新鮮與過期的狀態確認、四種 telemetry、restricted 與
unrestricted、GitLab 三態、一個刻意超長會被截斷的名字。全部長一樣的話，換掉半數渲染
邏輯也不會有人紅。

**每場四個檔案**：`aria.1280x800.txt`、`aria.390x844.txt`、`network.txt`、
`screen.1280x800.png`。共 **48 個檔案、3.9 MB**（其中 3.8 MB 是十二張全頁 PNG，
登入頁那張插畫就佔了 512 KB）。

#### 釘死的不穩定源（全部在錄製端，沒有一項是靠放寬閾值蓋掉的）

1. **登入頁的插畫**：`random.choice(LOGIN_ART)`，每次載入可能換一張。釘成固定那一張。
2. **頁尾的 `build_info()`**：會問工作區的 git sha（含 `-dirty`）與 `ttyd --version`。
   不釘的話 golden 只有「錄它的那台機器、那個當下」對得起來。
3. **瀏覽器時鐘**：`page.clock.set_fixed_time()`。`relTime`／`absTime`／`freshness`
   全都拿 `new Date()` 跟資料裡的時刻相減。用 `set_fixed_time` 不是 `install`：
   後者會接管所有計時器，而列表的十五秒輪詢與 toast 的關閉都靠計時器。
4. **登入後的「歡迎回來」toast**：`toastAfterNav` 讓它出現在**每一個**登入後的場景，
   五秒後自己消失。錄到它等於把一個過場錄成規格，所以除了 toast 那一場之外一律清掉。
5. **toast 的進度條**：它的 animation **同時是倒數計時器**。錄製時停掉，否則錄到的是
   「進度條剛好走到某個百分比」。
6. **抽屜的提示輪播**：停在第一條。
7. **動畫**：context 開 `reduced_motion="reduce"`（用 `app.css` 自己維護的那條路徑，
   不是另外灌一份 `animation: none`），截圖再加 `animations="disabled"` 當第二道。
8. **`users.created_at`**：`auth.create_user()` 用真實時間填，而帳號清單會把它畫出來。

第 8 條是這一段最值得記的：**它被 `--verify` 放過了。** 原本的 `--verify` 在同一個行程
裡連錄兩次，兩次共用同一次 seed，所以「在 seed 當下用真實時間填的欄位」兩次一模一樣，
看起來很穩。它是被**跨行程**跑的 `golden_check` 抓出來的（相對時間還顯示成負的）。
修法不只是釘那個欄位，而是把 `--verify` 改成兩次之間重新 seed 一次，讓這一整類當場現形。

#### 截圖那道閘：1% 的比例形同虛設

派工給的是「像素差比例，閾值 1%」。實測發現它抓不到東西：把抽屜面板的底色**整個換掉**，
全頁只差 **0.04%**，因為那塊底色幾乎被 iframe 與標題列蓋滿。1% 的全頁比例等於允許一塊
158x158 的區域整個換掉還是綠的。

所以改成兩道，兩道都要過：比例 <= 1%（保留 Nathan 給的數字），**加上**「單一通道差
超過 32 的強差異像素數 <= 400」。反鋸齒在字緣是幾階灰，過不了那道濾網；換顏色、位移、
少一個元件則一定過得了。實測：乾淨的一輪是 **0 個**，換一個底色是 **800 個**。

#### 驗收

- `golden_record.py --verify`：連錄兩次（中間重 seed），**48 個檔案逐位一致**。
- 跨行程 `golden_check.py`：48 條全 PASS，十二張截圖全部 `差 0.00%、強差異 0 個`。
- **變異測試**（golden 不能紅就沒有價值）：改一個文字標籤 → 六條 aria 紅；只改一個
  顏色 → 只有 `drawer-open` 那一場的截圖紅（800 > 400），其餘全綠。
- `run-all.sh quick`：38 支 0 失敗（原 36 支，`golden_check` 加一支、階段 3 併進來的
  `test_bootstrap` 加一支），跳過清單與基線逐字相同。

#### 兩個刻意的取捨

- **`network.txt` 分兩段**：文件與 API 依序列出（應用邏輯決定，順序是確定的，也正是
  「Vue 版有沒有多打少打」要守的），靜態資源排序後列出（誰先回來由瀏覽器排，照原順序
  記會隨機紅，而隨機紅的 golden 最後只會被人加到忽略清單裡）。query 一律丟掉，
  `asset_url()` 的 `?v=` 是檔案 mtime 算的。
- **ttyd 替身抽成 `tests/fake_ttyd.py`**：`e2e_drawer.py` 與 `golden_scenes.py` 共用。
  複製兩份的話它們會各自漂走，而漂走之後兩邊驗的就不是同一個終端，卻沒有東西會紅。

`golden_record.py` 與 `golden_scenes.py` 都**不是測試**（一條斷言都沒有），所以 run-all.sh
是逐一列名 `tests/golden_check.py`，不靠 glob；被 glob 撿走只會空跑。

### 階段 2 補：DOM 合約屬性的第三種快照

aria 樹只記 role 與可及名稱。實測一下就知道缺口有多大：整份 golden 的 aria 檔案裡
`data-act` 與 `class` **一個字都沒有**，而模板加 `app.js` 裡光 `data-testid` 就 115 處、
`data-act` 28 處、`data-tone` 15 處。aria 蓋到的 role／aria-* 只有 61 處。

蓋不到的那些正好是**合約型**的：`data-testid` 是 e2e 的抓手、`data-act` 是 app.js 事件
委派的分派鍵、`data-tone`／`data-kind`／`data-state`／`data-stale` 是狀態的真相來源
（1b 那一刀才剛把測試從 class 搬到它們身上）。

所以每場再加一份 `dom.<vp>.txt`：帶白名單屬性的元素，一行一個，依 DOM 順序，逐字比對
不設閾值。長這樣：

```
a testid=cred-badge state=bad tip="在 host 上執行 `claude setup-token`…"
span testid=chip-mark tone=accent kind=capture tip=流量錄製：開
button testid=range-day day=2026-08-25 edge=true
```

#### 白名單怎麼定的

**只記白名單，不記 class 也不記完整 HTML。** 記整棵 DOM 的話，Vue 版多包一層 wrapper
就會整份紅，而那種 golden 一週內就會被停用；停用之後連原本守得住的那些也一起沒了。
白名單讓「多一層 div」無聲、「少一個 data-act」出聲。

排除項也寫進註解，否則看起來像漏掉：

- **動畫與過場的暫態**（`data-shown`／`data-closing`／`data-swap`／`data-animate`／
  `data-loading`／`data-pausable`）：畫面停定之後不保證是同一個值，記了就是自找不穩定。
- **內容或設定的回音**（`data-label`／`data-name`／`data-container`／`data-persist-path`／
  `data-cli`／`data-behind-proxy`／`data-sid`）：可見的部分 aria 與截圖已經蓋著了。
- **`disabled`／`aria-expanded`／`aria-selected`**：aria 快照**已經記了**（實測數到
  `[disabled]` 28 個、`[expanded]` 3 個、`[selected]` 8 個）。同一個事實兩個來源比一個
  更糟：改動時兩邊都要更新，而只更新一邊沒有人會發現。

兩個**刻意加進去**的，理由與上面對稱：

- **`aria-checked`**：實測 aria 快照裡 `[checked]` 是 **0 個**。三顆開關用的是
  `role=switch`，Playwright 沒把勾選狀態畫進去。那是真的缺口，不是重複。
- **`hidden`**：它區分得出「沒有渲染」與「渲染了但藏起來」。Vue 版把 `v-if` 寫成
  `v-show`（或反過來）正是這個差別，而 aria 只看得到前者。
- **`data-tip`**：滑過去才看得到，所以截圖蓋不到；不是 aria 名稱，所以 aria 也蓋不到。
  它一旦悄悄消失，沒有任何一道防線會出聲。

#### 驗收

- 24 個新檔案、2930 行、180 KB。golden 整體從 48 檔 3.9 MB 變成 **72 檔 4.1 MB**。
- `--verify` 連錄兩次（中間重 seed）：**72 個檔案逐位一致**。
- 跨行程 `golden_check`：**72 條全 PASS**。
- **變異測試**：把 `chips()` 裡兩處 `kind="gitlab"` 打成 `"gitlabb"`，
  結果是 **14 條紅、全部都是「DOM 合約屬性一致」**（七個有 session 列的場景 × 兩個視口），
  aria、網路、截圖一條都沒紅。這正是要的形狀：那顆 chip 顏色沒變、可及名稱沒變，
  只有合約欄位錯了，而**現在有人會出聲**。
- `run-all.sh quick` 38 支 0 失敗、跳過清單與基線逐字相同；`--e2e` 9 支全綠。

錄製端**沒有加任何新的釘死項**：這些屬性本來就是決定性的（`data-day` 靠已經釘死的
瀏覽器時鐘，`data-tone`／`kind`／`state` 靠已經釘死的 seed）。

### 階段 2b（上半）：fable 快審 e95448e 的高／中高／低三項

**高 1｜CI 平台 gate。** golden 在 macOS 錄（PingFang TC），ubuntu runner 上同一份程式碼
算繪出來的字是另一組像素，CI 的 `--all` job 一跑 golden_check 十二條截圖必紅，而那不是
回歸。錄製時多寫一份 `tests/golden/META`（ui、platform、chromium 版本、dpr、color_scheme、
視口清單），`golden_check` 只在完全相同時才比截圖。

方向很重要：**平台不同時只跳過截圖，aria／DOM／網路照比**。那三份是文字，與字體算繪
無關，跨平台完全可比；把整支 golden_check 跳掉才是把 CI 上唯一守得住介面的東西關掉。
跳過時明說（`⚠ 截圖跳過：平台不同（platform：golden='Linux x86_64' 現在='Darwin arm64'）`），
最後再印一次「這一輪沒有比截圖：12 場」。實測把 META 改成 Linux：**60 條照跑、12 條截圖
跳過、exit 0**，沒有靜靜少比。

run-all.sh 的跳過計數不受影響：它數的是「整支測試被跳過」，而 golden_check 有跑、有印
PASS／FAIL。這正是 run-all.sh 註解裡說的「跳過其中一節、其他斷言照跑」那一類正當情況。

**高 2｜network.txt 把 query 全丟掉了。** 篩選條件、`limit`／`offset`、時間範圍全在
query 裡，而那正是 Vue 版最容易做錯的地方：少帶一個參數、offset 算錯，畫面看起來還是
一張表，資料卻是另一批。改成**只對 `/static/` 丟 query**（那裡的 `?v=` 是檔案 mtime
算的），API 一律保留。現在錄得到 `GET /api/sessions/history?offset=0&limit=10`。

同時補了第三段：**場景就緒時的網址**。`?tab=past` 與篩選條件是 `replaceState` 寫進去的，
**不產生任何請求**，前兩段完全看不到。而「條件的唯一真相在網址」是這個前端的核心設計，
漏掉它等於沒守到。

**中高 3｜截圖那道閘抓不到小面積的真差異。** 前一版數的是「強差異像素的總數」，
7px 的狀態燈換色、1px 邊框挪 20 階都太小。**問題不在數量，在形狀。** 反鋸齒是沿字緣的
一兩像素細線，真改動是一整塊。所以改成三道，各接一種形狀：

| 閘 | 接的是 | 乾淨輪 | 7px 狀態燈換色 | chip 1px 邊框挪 20 階 |
|---|---|---|---|---|
| 比例 <= 1% | 大面積 | 0.00% | 0.13%（過） | 0.13%（過） |
| 不得有實心 5x5 強差異塊 | 局部一整塊 | 無 | **有 → 紅** | 無（過） |
| 強差異像素 <= 400 | 細而廣 | 0 個 | — | **2160 個 → 紅** |

實心塊用**侵蝕**（`MinFilter(5)`）判，不是連通元件標記：一來它是 C 實作的一次卷積，
2.5M 像素跑得動；二來連通元件的外接矩形會被細線騙過去（整行文字位移一像素會產生
500x8 的細長元件，外接矩形遠大於 5x5，但它並不是「一塊」）。

第三道是**實測補上的**，不是原本設計的：加完前兩道之後我拿 chip 的 1px 邊框去打，
兩道都放過，而它其實改到了畫面上每一顆 chip。侵蝕天生擋不住比 5px 細的元素，那是這個
規則的固有限制，所以要有一道用總量接住它。

因為形狀那一道擋得住反鋸齒，`STRONG_DELTA` 從 32 壓到 **8**，小幅度的真差異才抓得到。

**低 6｜`pin_all()` 的第 3 項是 no-op 佔位**，刪掉。改成兩件真的事：
- 明寫 `config.UI = "legacy"`。scaffold 併進來之後這個旗標會決定要出哪一套前端，而
  golden 錄的**永遠是 legacy**（它就是規格本身）。不明寫的話，哪天預設值翻過去，
  「看到紅就重錄」會把 Vue 版錄成規格，這條防線當場反過來替回歸背書。
  `golden_check` 開頭也印出它現在比的是哪一版。
- context 掛一段 init script，把 **>= 5 秒的 `setInterval` 一律不跑**：列表每 15 秒重抓
  （sessions.html）、抽屜提示每 6 秒換一條（app.js 的 hintTimer）。慢一點的機器上它們
  隨時可能在快照前一刻把畫面換掉。只擋長間隔，短的有正經用途，一律擋掉會讓 golden
  錄到一個實際上不存在的畫面，那比不穩定更糟。

驗收：`--verify` 連錄兩次 **73 個檔案逐位一致**；跨行程 `golden_check` 全 PASS；
`run-all.sh quick` 38 支 0 失敗、跳過清單與基線逐字相同。

### 階段 2b（下半）：補場景與 dom 白名單

**中 4｜補了六個場景**，golden 從 12 場變 **18 場**：

| 場景 | 錄到的狀態 |
|---|---|
| `sessions-filter-applied` | 套用一個條件後：網址變 `/?network=unrestricted`、清單剩一列、清除鈕可按 |
| `sessions-toast-error` | 走 app 自己的 `toastError()`（不是自己拼一個 danger toast，那條路才是失敗時真的會跑的） |
| `sessions-modal-kill` | 終止的確認對話框 |
| `sessions-modal-rename` | 重新命名的對話框 |
| `sessions-pager` | 一頁裝不下時的分頁列 |
| `sessions-no-gitlab` | 部署沒開 GitLab：gitlab 標記從 3 顆變 0 顆 |

外加 `drawer-open` 補錄 390 視口的截圖（抽屜在窄視窗下是另一套版面，只錄桌機等於沒錄到）。
其餘場景的手機版仍只靠 aria 與 dom 兩份快照守結構，不另外存圖（圖很貴）。

兩個實作上的決定：

- **重新命名不是 inline 編輯，是帶輸入框的對話框**（`app.js` 的 `dialog({input})`）。
  派工寫的是「inline 編輯態」，我照實際的樣子錄並把場景命名為 `modal-rename`。
  golden 記的是現況，不是我們以為的現況；照想像命名的話，之後有人會拿它當「應該長這樣」。
- **分頁那場是把 `PAGE_SIZE` 調小，不是多塞十幾筆假資料**。多塞的話每一個場景的清單都
  跟著變長、截圖全部要重錄，而那十幾筆對其他場景一點資訊都沒有。用 `try/finally` 還原，
  中途拋了也不會汙染後面的場景（那種汙染在 golden 裡的樣子是「不相干的場景莫名其妙全紅」）。

**中 5｜白名單再加三項**：`id`、`aria-controls`、`title`。

- `id` 與 `aria-controls` 是**成對**的契約：`aria-controls` 指的那個 id 必須真的存在。
  只記其中一半的話，Vue 版把 id 改名而 `aria-controls` 沒跟著改，這裡看起來一切正常，
  而螢幕閱讀器會指到一個不存在的東西。
- `title` 是原生 tooltip，與 `data-tip` 同理：滑過去才看得到（截圖蓋不到），也不是可及
  名稱（aria 蓋不到）。
- `inert` 本來就在白名單裡（e2e 那條「沒露臉的要退出 Tab 序」守的就是它）。

順手修一個自己挖的坑：原本 `key` 是 `a.replace(/^(data|aria)-/, "")`，把 `aria-checked`
剝成 `checked`。哪天有人加一個 `data-checked` 就撞名，而撞名之後兩件事在檔案裡長得
一模一樣。改成**只剝 `data-`**，`aria-*` 原樣保留。

**hidden vs 從 DOM 移除，現在分得出來**（這是 `v-if` 與 `v-show` 的差別，aria 看起來一樣）：

```
sessions-list   div hidden id=pager     ← 一頁裝得下：在 DOM 裡，藏起來
sessions-pager  div id=pager            ← 裝不下：同一個元素，露出來
```

#### 變異測試

| 變異 | 紅的是 | 其他 |
|---|---|---|
| `aria-controls="filter-bar"` 打成 `filter-barr` | 4 條，**全是 DOM** | aria／網路／截圖全綠 |
| `pager.hidden = X` 改成 `pager.remove()`（v-show 改 v-if） | 26 條，**全是 DOM** | aria／網路／截圖全綠 |

第二個特別值得記：**第一次的變異是無效的。** 我原本去拿掉模板裡 `<div class="pager"
id="pager" hidden>` 的 `hidden`，結果一條都沒紅——因為 `renderPager()` 每次都重設
`pager.hidden`，模板的初始屬性根本不影響最終狀態。改成真的把元素移除才是 `v-if` 的類比。
**變異測試自己也會失敗**，看到「沒紅」的第一件事是問「我的變異真的改到東西了嗎」，
而不是宣告防線有效。

#### 規模與驗收

- **18 場、110 個檔案、6.0 MB**（截圖 19 張佔 5.6 MB）。
- `--verify` 連錄兩次（中間重 seed）：**110 個檔案逐位一致**。
- 跨行程 `golden_check`：**109 條全 PASS**（36 aria ＋ 36 dom ＋ 18 network ＋ 19 截圖）。
- `run-all.sh quick` 38 支 0 失敗、跳過清單與基線逐字相同；`--e2e` 9 支全綠。

### 階段 4 前半完成：骨架與部署鏈

這一刀只做「骨架與部署鏈」：工具鏈、兩個頁面、Flask／nginx／Dockerfile／CI 的切換與閘門。
**功能刻意不完整**——帳號頁與終端抽屜留殼，理由見下面的「留了哪些殼、為什麼」。

#### 目錄與工具鏈

```
claude-pty/frontend/
  package.json  package-lock.json      # npm；lockfile 進版控
  vite.config.ts  tsconfig.json  env.d.ts
  .oxlintrc.json  .prettierrc.json  .prettierignore
  index.html                            # 對應舊版 base.html 的 <head>
  vitest.setup.ts
  src/
    main.ts  App.vue  router.ts
    api/client.ts                       # 舊版 app.js 的 api()
    lib/  anchor  dialog  filters  range  sessions  storage  theme  time  toast
    composables/useClipTips.ts          # 舊版的 markClipped
    components/  AppFooter AppMasthead AppShell BrandMark CreatePanel DialogHost
                 FilterBar ManifestList MetricTime PasswordInput RangePicker
                 SessionChips SettingsModal SitePicker SiteSwitch TerminalDrawer ToastStack
    views/  LoginView SessionsView AccountView
    __tests__/  lib.spec.ts  components.spec.ts  views.spec.ts
```

裝到的版本（`npm ci` 的實際結果）：vue 3.5.41、vite 7.3.6、vue-router 4.6.4、pinia 3.0.4、
typescript 5.9.3、vue-tsc 3.3.11、vitest 3.2.7、@vue/test-utils 2.4.6、jsdom 26、
oxlint 1.80.0、prettier 3.9.6。build 輸出 `server/static/dist/`（gitignore）。

Pinia **只有一個 store**（`stores/site`）：身分與憑證狀態被招牌、建立表單、列表三個互不相鄰
的地方讀，而它們的更新來源是同一個（列表輪詢順風車帶回來的 `credentials`）。其餘狀態
（清單、篩選、表單）都只有一個擁有者，放進 store 只會讓「誰改了它」變難回答。

#### CSS：引用原檔，不複製

`src/main.ts` 直接 `import "../../server/static/css/app.css"`，Vite 打包成帶雜湊的
`/assets/*.css`。**不複製成第二份**——階段 4 的前提是 CSS 一字不改，複製等於同一份樣式有兩個
真相，而截圖 golden 分不出「樣式改了」與「複本沒跟上」。字體與 Font Awesome 仍由
`/static/vendor/…` 供應（原檔，不該被雜湊改名），在 `index.html` 以絕對路徑 `<link>` 進來；
Vite build 時會說「這兩個檔案在 build 期不存在，保持原樣交給 runtime 解析」，那正是要的。

#### 開關：`CLAUDE_PTY_UI=legacy|vue`

- Flask（`config.UI`，預設 legacy）：vue 模式下 `/`、`/login`、`/account` 回
  `server/static/dist/index.html`，`/assets/*` 由 `web.spa_asset` 供應（**公開端點**——登入頁
  本身就是那包 SPA，擋掉的話沒登入的人只看得到一片白）。殼一律 `no-store`：不明講的話
  `SEND_FILE_MAX_AGE_DEFAULT`（一年）會留在回應上，改版後拿到的舊殼會去要一個已經不存在的
  `/assets/*.js`——一片白畫面，沒有任何線索。
- legacy 模式的每一條路徑**一個字都沒動**：切換器只加新的分支。
- 不認得的值當 legacy，並在 `preflight` 喊出來；`UI=vue` 但 dist 不存在也喊（不喊的話症狀是
  三個頁面全部 404，看起來像路由壞掉）。

#### dist 怎麼進 nginx：COPY 進 image，不是 bind mount

`deploy/Dockerfile` 多了兩個 stage：

- `frontend`（node:24-slim）：先只複製 `package.json` 與 lockfile 再 `npm ci`（原始碼改了不必
  重裝），然後 `npm run build`，最後 `test -f …/dist/index.html`——vite 的退出碼可靠，但
  「build 成功卻沒有 index.html」（改錯 outDir）是靜悄悄的。
- `nginx`（**具名 stage，只有 `--target nginx` 會 build**）：把上一階段的 dist COPY 進
  `/usr/share/nginx/html`。compose 的 nginx 服務因此從 `image:` 改成 `build: … target: nginx`。

為什麼不 bind mount：host 上沒 build 過的目錄會被 docker 建成一個**空目錄頂替**，症狀是整站
404 而不是「你忘了 build」；而且正式站的產物該跟著 image 走版本，不該取決於部署那台機器當下
的工作區。`nginx.conf` 仍然是 mount 的（改設定不必重 build）。控制平面 image 也 COPY 同一份
dist——dev 與 e2e 走的是 in-thread Flask 那條路。

#### nginx 的頁面路由：外部片段，不寫死

`nginx.conf` 只加了兩塊：`/assets/` 直出（`expires 1y` + `immutable`，Vite 把內容雜湊寫進檔名）
與一行 `include /etc/nginx/claude-pty-ui/*.conf;`。三條頁面路由放在
`deploy/nginx-ui/{legacy,vue}/ui.conf`，由 compose 的 `CLAUDE_PTY_NGINX_UI` 決定掛哪一個。

**為什麼不照派工說的直接寫進 `nginx.conf`**：nginx 讀不到 Flask 那邊的 `CLAUDE_PTY_UI`，
直接把 `try_files` 放進主檔的話，**正式站在 legacy 模式下也會吐 SPA**——而 legacy 才是預設
（計畫的決定 3），派工第 4 點也要求「legacy 模式行為一個字不變」。兩者只能靠「掛哪一份片段」
分開。legacy 那一份**一條指令都沒有**（`test_nginx_contract` 有一條在守這件事），三條路照舊
落到 `location /` proxy 給 Flask。

`tests/test_nginx_contract.py` 補了 12 條 check：include 在不在、`/assets/` 是 root 直出而不是
proxy、長快取、兩份片段都在、legacy 片段真的是空的、vue 片段三條精確路由 + `try_files` +
`no-store`。

#### run-all.sh 與 CI

`run-all.sh` 多一段「前端六關」，順序是便宜的先擋：`npm ci` → oxlint → `prettier --check` →
`vue-tsc` → `vitest --coverage`（行覆蓋率門檻 70%）→ `vite build`。缺 node/npm 就整段跳過並
**講出來**（同 app.js 語法檢查的做法）；`frontend/package-lock.json` 不見了則是**紅燈**而不是
跳過——那不是「環境沒裝」，是 repo 壞了。

build 也要跑，因為型別過得了不代表打包得出來（outDir 寫錯、import 到 root 外面沒放行、CSS 原
檔被搬走），而產物不進版控，「沒有人 build 過」在部署之前不會有任何跡象。

CI：兩個吃 run-all.sh 的 job 都加了 npm 快取；`deploy-image` job 多兩步——`--target nginx` 單獨
build 一次（buildkit 只 build 目標依賴到的 stage，不單獨來一次的話那一段永遠沒被驗過）並確認
`index.html` 與 `assets/` 真的在裡面，以及控制平面 image 裡也有同一份 dist。

#### 1:1 的對照

DOM 結構、class、`data-testid` 逐項對照舊版模板與 `app.js`。這一刀涵蓋的頁面上，testid 一個不
少（登入 4、招牌 6、頁尾 2、sessions 28，加上 picker／switch／rangepicker／toast／modal／
settings-modal 的動態那幾組）。帳號頁那 23 個與抽屜那 12 個隨它們的元件一起留到後半。

驗證方式：寫了一支臨時的 Playwright 腳本（**不進 commit**），**同一支對 legacy 與 vue 各跑一
次**——30 條 check 在兩版都過，只有「帳號頁是殼」那一條在 legacy 下不成立（本來就該不成立）。
這比只驗 vue 版強：它證明的是「同一組抓手、同一串操作，兩版行為一致」。

過程中被這支對照抓到一條真的不一致：**看歷史時建立表單要 `hidden`，不是從 DOM 上拿掉**。
舊版是 `document.getElementById("create-panel").hidden = past`，我第一版寫成 `v-if`，於是
`#create-panel` 在歷史那一頁整個不存在——e2e 與 aria golden 是拿舊版那份來比的。已改回。

#### 踩到的坑

- **`:inert="false"` 仍然是 inert。** `inert` 不在 Vue 認得的布林屬性清單裡
  （itemscope/allowfullscreen/formnovalidate/ismap/nomodule/novalidate/readonly），所以 false
  會照字面渲染成 `inert="false"`，而 HTML 的規則是**屬性存在就是 inert**。症狀是篩選列展開了
  卻整塊點不到，而 DOM 看起來是對的。改成展開時回 `undefined`；單元測試有一條在守。
- **`since=custom` 不可以進網址。** 「這一格停在自訂範圍」是畫面狀態不是查詢條件，後端把
  `since` 當天數解析，送過去會 400。舊版本來就是用 DOM 的 `hidden` 記這件事，這一版用元件的
  local ref，並且「帶著 from/to 進來時它要自己成立」。
- 密碼錯是 **400**（`auth.AuthError` 的處理器）不是 401。401 的意思是「cookie 沒了」，由
  `api()` 統一接走導回登入頁——在登入頁上把它當成密碼錯會是另一回事。

#### SPA 化學到的兩件化簡（都刪了舊版的程式碼，不是搬過來）

- **navseg 的滑動不再需要「記住上一頁停在哪」**（舊版 `initNavSeg` + sessionStorage）。換頁不再
  整份 HTML 重來，招牌的 DOM 一直是同一份，`data-active` 一改 CSS 的 transition 自己就跑。
- **憑證徽章的翻頁動畫（`swapCred`）拿掉**：它只在**換 agent** 時才跑，而這套東西只驅動 claude
  一種 CLI，`switched` 恆為 false——留著等於留一段永遠不執行的程式碼。

順帶發現舊版 `sessions.html` 的 `const CLAUDE_MODELS = new Set({{ claude_models | tojson }})`
**宣告了但沒有任何地方用**（列表的 chip 直接讀 `p.model`）。Vue 版沒有搬它；階段 5 拆舊時
連同模板那一行一起清掉。

#### 留了哪些殼、為什麼

| 殼 | 為什麼現在做不了 |
| --- | --- |
| 帳號頁（`AccountView`） | 舊版 651 行、**28 處 Jinja 注入**，正是階段 3 要改成 API 的東西。在那些端點出現之前搬過來，等於在前端重寫一份猜出來的伺服端狀態 |
| 終端抽屜（`TerminalDrawer`） | 目前實際上打不開：`behindProxy` 沒有 API 可問，預設 false，而 false 這條路本來就是「開新分頁」。後半連同 `useTerminalSize` composable（階段 1.5 的成果）一起搬 |
| 頁尾的版本與 commit | `build_info()` 只有 Jinja 拿得到 |
| 登入頁的插畫 | `web.LOGIN_ART` 每次隨機挑一張，SPA 拿不到那份清單 |
| `name_max` / `gitlab_enabled` / `behind_proxy` / `persist_dir` | 同上，都是 Jinja 注入的伺服端事實 |

這幾項全部集中在 `stores/site.ts` 的 `META_DEFAULTS` 與 `loadMeta()`，帶著
`TODO(階段 3)`：那支端點一上線，**只有那一個函式要改**，其餘畫面一個字都不必動。

### fable 快審 4a：兩條嚴重、兩條中度、一條低度，全部修完

派工把發現分成五組。以下按「這條到底在講什麼」重排，每一條都附上**怎麼驗的**——
其中兩條的驗證方式本身就是這次最大的收穫。

#### 【嚴重】prod 的 vue 模式三個頁面全部 404

`try_files $uri /index.html;` 的最後一個參數是 URI，nginx 會做**內部轉向**：把
`/index.html` 當成一個新請求重跑一次 location 比對，而它不符合那三條精確比對，於是落到
`location /` 被 proxy 給 Flask——Flask 沒有這條路由，回 404。**設定看起來完全正確。**

改成 `try_files /index.html =404;`：最後一個參數是 `=404` 時，前面的 `/index.html` 是
**檔案路徑**（相對 root），命中就直接送檔，完全不重新比對。

驗法（這一條的重點）：`test_nginx_contract` 是**結構**測試，它證明指令都在、接對了名字，
證明不了 nginx 真的照做——而這個 bug 正好活在那個縫裡。所以起真的 nginx 用真的 curl 問，
而且**刻意不起 Flask**：`control` 指到 127.0.0.1、那個 port 上沒有東西，於是任何落到
`location /` 的請求都是 502。**502 就是失敗，200 才是「nginx 自己送出了殼」。**
起了 Flask 的話兩條路都會 200，這道閘就分不出來了。

實測（本機，真 nginx 容器）：

| 寫法 | `/` | `/login` | `/account` |
|---|---|---|---|
| `try_files $uri /index.html;`（舊） | 502 | 502 | 502 |
| `try_files /index.html =404;`（新） | 200 | 200 | 200 |
| legacy 片段（空的） | 502 | 502 | 502 ← 這才是對的（它該被 proxy 給 Flask） |

兩條 curl 步驟都進了 CI（vue 那份要 200、legacy 那份要 502）。

#### 【嚴重】1:1 的差異：拿 golden 的場景對兩版逐字比

派工列了十幾條。與其一條一條猜，我把 `golden_scenes.py` 的場景直接拿來錄 **Vue 版**
（`config.UI = "vue"`，其餘完全相同），跟 `tests/golden/` 的 legacy 規格逐字 diff。

⚠ **踩到的第一個坑：不可以 `import golden_record`。** 那支在模組層就直接
`record_into(G.GOLDEN_DIR)`——import 它等於當場把真正的 golden 覆寫掉。我覆寫了一次，
靠 `git checkout` 救回來。臨時腳本改成自己跑一遍同樣的錄製迴圈。

結果：**七個場景的 aria 快照（兩個視口，共 14 個檔案）逐字相同**——login-empty、
login-error、sessions-empty、sessions-list、sessions-history、sessions-filters、
sessions-rangepick、sessions-settings。

aria 會把空白摺疊掉，所以另外寫了一支比 `outerHTML` 的腳本（同一個場景、同一份資料、
只換 `config.UI`）。修掉的：

- `#pager-status`、`.rangepick__month`、`清除`／`確定` 等 **12 處尾巴多一個空白**。成因是
  Vue 的 `whitespace: 'condense'` 會把「文字＋換行縮排」這種**混合**文字節點摺成
  「文字＋一個空白」，而舊版是伺服端一次印出來的。修法是把文字與收尾標籤放回同一行並掛
  `<!-- prettier-ignore -->`。
- **屬性順序**：`cred-badge`（class 在 id 前）、picker 的 `aria-*` 在 `data-testid` 前、
  日期格的 `tabindex` 在 `data-edge` 前。順序對 HTML 沒有語意，但逐字比對時它是唯一還會
  亮的差異，留著只會讓真的差異被雜訊蓋住。
- **`v-if` 的註解錨點**：沒有 `v-else` 的 `v-if` 會在 DOM 上留一個空註解當錨點，而舊版那個
  `<ul>` 在沒展開過時是完全空的。改成 `v-for`（Fragment 的錨點是空白文字節點，
  `outerHTML` 看不到）。
  ⚠ 這一改帶出一個真的 bug：`v-for` 裡的 template ref 收成的是**陣列**，於是
  `searchInput.value?.focus()` 變成「對陣列呼叫 focus」，執行期 TypeError。是 vitest 的
  unhandled error 當場抓到的，改成從選單元素 query 一次。
- `#pick-range` 的 `data-move`：舊版靠它做事件委派，我改用 `@click` 之後屬性就沒了。補回。
- 其餘照派工修的：`inert` 只有點過篩選鍵才寫（舊版是 `setFiltersOpen()` 裡設的，剛進站
  身上沒有）、`#cred-badge` 首幀就在（不 v-if）、`data-drop`／`data-loading`／`data-tone`
  只在條件成立時寫、拿掉多出來的 `data-testid="pick-range"`、`.cred__brand` 補 `data-brand`、
  密碼欄補 `data-pw-toggle="1"`、使用者欄補 `autofocus`、theme-picker 掛載點回 `<span>`、
  `#toast-stack` 常駐、讀取失敗時 pager 收起來、`<title>` 隨路由（三個字串逐字照舊）、
  RouterLink 改 `custom` 自己畫 `<a>`（不要它自動掛的 `router-link-active`）。

**剩下三處差異，都不修，理由如下。**

1. **inline style 的序列化**：舊版 `style="--form-col-min:20rem"`，Vue 是
   `style="--form-col-min: 20rem;"`。Vue 的編譯器把靜態 `style` 屬性**一律解析成物件**
   （`{"--form-col-min":"20rem"}`，兩種寫法編出來一模一樣，實測過），執行期經 CSSOM 設定，
   而瀏覽器序列化時會自己加上空格與分號。改原始碼的空白完全沒有作用。computed style 相同、
   截圖相同、aria 相同。
2. **`#filter-bar` 裡 `aria-selected` 的位置**：舊版把 `pick-since-opt-any` 標成
   `aria-selected="true"`，而那一刻按鈕上寫的是「自訂範圍」。**那是舊版的過期值**——
   `renderMenu()` 只在展開時跑，選了之後選單沒有重畫。實測：

   | | 按鈕顯示 | `aria-selected=true` 落在 |
   |---|---|---|
   | legacy | 自訂範圍 | `opt-any` ← 過期 |
   | vue | 自訂範圍 | `opt-custom` ← 正確 |

   要「1:1」就得把這個錯一起搬過來。選單是 `hidden` 的，沒有人（含輔助技術）看得到它，
   所以我留著正確的那一版，**不當成差異修**。這一條要不要照舊版，請 Nathan 或 fable 裁示；
   若要照舊，那就等於要求 Vue 版在關閉的選單裡保留一份過期的可及性狀態。
3. **body 的結構**：舊版是 `.shell` + `footer` + 兩個 `<script>` + `#toast-stack`，
   Vue 版是 `#app` + `#toast-stack`（頁尾在 `#app` 裡、module script 在 head）。
   這是 SPA 的形狀本身，不是可修的差異。

另外，`#cred-data` 那個 `<script type="application/json">` 照舊版建出來了（Vue 的樣板
編譯器不吐 `<script>`，所以是 onMounted 時建的）。⚠ **這一版沒有任何讀者**——憑證狀態走
`/api/account/bootstrap` 與列表的順風車。它純粹是為了 DOM 一致而存在的相容節點，
**階段 5 拆舊時要連同模板那一行一起刪**。

#### `sessions-toast` 這一場對 Vue 版跑不起來（harness 的耦合，不是 1:1 的差異）

`scene_sessions_toast` 直接 `page.evaluate("() => toast(...)")`——那是 `app.js` 的**全域
函式**，Vue 版沒有這個全域。這不是畫面差異，是場景伸手進了某一版的內部實作。

我**沒有**動 `golden_scenes.py`（那是階段 2 的規格，動它等於改規格），也沒有為了它在
production bundle 上掛一個 `window.toast`。toast 元件本身的行為改用真實互動驗
（`e2e_vue_smoke` 裡「終止 → 取消 → 已取消」那一條）。要讓這一場對兩版都成立，得由場景
改成用 UI 動作觸發並重錄——那是 lane B 的決定。

#### 【中】兩個環境變數收成一個

`CLAUDE_PTY_NGINX_UI` 拿掉，compose 直接掛 `./nginx-ui/${CLAUDE_PTY_UI:-legacy}`。
「兩個一定要一起改」的設定遲早會有人只改一個，而兩種漏法都不報錯：只改 control 是正式站
仍吐舊模板，只改 nginx 是 `/api/*` 照舊能動、畫面卻換了版。收成一個之後那個錯誤形狀就
不存在了。打錯字會掛到不存在的目錄（docker 建個空的頂替）＝落回 legacy，而 preflight
會喊一行——降級是安全的，但不會是無聲的。

#### 【中】trivy 掃前端相依

`run-all.sh` 加一關（沒裝 trivy 就跳過並講出來）。⚠ **「沒有目標」不等於「乾淨」**（repo
既有的紀律）：trivy 掃不到任何相依清單時一樣 exit 0、報告是空的。所以除了「有沒有漏洞」，
還驗「它真的把 lockfile 當成 npm 目標解析了」。目前 1 個 npm 目標、0 筆 MEDIUM 以上。

#### 【低】`/assets/` 回了兩份 Cache-Control、nginx 直出的殼少了安全標頭

`expires 1y` 自己就會送一個 `Cache-Control`，再 `add_header` 一條就是同一個標頭回兩份。
合成一條 `public, max-age=31536000, immutable`（實測：改前兩份，改後一份）。

那三條頁面路由不經 Flask，`_security_headers` 完全沒有機會跑——不補的話，切到 vue 版等於
把 CSP、nosniff、Referrer-Policy、X-Frame-Options 一起關掉，而畫面上完全看不出來。
值逐字照 `server/app.py`，契約測試會比對兩邊是否分岔。

#### 臨時腳本進 commit

那支 30 條的對照腳本收成 `tests/e2e_vue_smoke.py`（審查說沒進 commit 無法覆核）。
它的價值在於**同一支腳本對兩版都跑得過**：每一條斷言只用 `data-testid` 與網址，把
`config.UI` 換成 `legacy` 再跑，除了「帳號頁是殼」以外全部要過。開發時就是這樣抓到
「看歷史時建立表單，舊版是 `hidden`、我寫成了 `v-if`＝節點整個消失」的。

### 階段 4 後半（一）：四件小事

**1. CI format gate。** `uvx ruff@0.16.3 format` 抓到三支：`e2e_drawer.py`（派工指名的）
加上我自己的 `golden_check.py` 與 `golden_scenes.py`。本機 venv 的 ruff 版本與 CI 釘的
0.16.3 不同，所以我先前那幾刀在本機是綠的、在 CI 是紅的。三支一起修，之後一律用
`uvx ruff@0.16.3` 驗。

**2. `golden_record.py` 的模組層會覆寫 golden。** 錄製那一整段原本掛在模組層，於是
`import golden_record` 就會當場把 `tests/golden/` 刪掉重錄一次。那不是理論風險：另一條線
只是想拿 `record_into` 用，規格就沒了，而且**沒有任何錯誤訊息** ——下一次 `golden_check`
還是綠的，因為規格剛剛被現況覆寫過。包進 `main()` 加 `if __name__ == "__main__"`，
實測 import 前後 golden 的指紋相同。

順手把 `config.UI = "legacy"` 從 `pin_all()` 搬進 `golden_record.main()`。**錄**的永遠是
legacy（規格本身），但**比**的時候要比當下在測的那一版：`CLAUDE_PTY_UI=vue` 跑
`golden_check` 就是拿 Vue 版去對規格，那正是這整套東西存在的理由。寫在共用的地方會讓
vue 模式變成「legacy 跟自己比」，永遠是綠的。

同一個道理，`META` 的 `ui=` 那一行**不列入平台比較**。它是說明「錄的是哪一版」，不是
gate；當成環境指紋的話，vue 模式下每一張截圖都會被判成「平台不同」而跳過，等於把最該
比的那一次比對關掉。

**3. legacy 的 a11y bug：選單收起來之後選中狀態是過期的。** `renderMenu()` 只在 `open()`
裡跑，所以選完之後那份收起來的 DOM 還停在上一次展開的樣子：`aria-selected` 指著舊的值、
`data-active` 的游標也還在舊那一列。畫面上完全看不出來（按鈕文字是 `renderButton()` 另外
畫的，它是對的），但螢幕閱讀器唸的是這份 DOM，下一次展開的第一幀也是它。

修在 `pick()` 裡就地改屬性，**不呼叫 `renderMenu()` 重畫** ——重建 innerHTML 會把搜尋框的
游標與 IME 選字一起沖掉（同檔 `paintDays` 記的是同一個教訓）。

**這是 legacy 的 bug，Vue 版是對的，所以規格往正確那邊修。** golden 重錄之後：

```
修之前  li testid=pick-since-opt-any    active=true    ← 已經選了「自訂範圍」
修之後  li testid=pick-since-opt-custom active=true aria-selected=true
```

順帶把 `aria-selected` 加進 DOM 白名單。先前排除它的理由是「aria 快照已經記了
`[selected]`」，但那句話只對**可見**的元素成立 ——picker 的選單收起來之後就不在 aria 樹裡，
而收起來的那份 DOM 正是選中狀態最容易過期的地方。可見的那些重複一次無害，隱藏的那些
只有 dom.txt 看得到。

**4. 兩個 toast 場景改用真的 UI 動作。** 原本是 `page.evaluate` 去呼叫全域的 `toast()` 與
`toastError()`，那等於把場景綁在 legacy 的實作上：Vue 版沒有那個全域，這兩場會在「還沒
開始比」的地方就炸掉，而炸掉的原因與介面像不像一點關係都沒有。

- `sessions-toast`：終止 → 取消（`toast("已取消", "info")`）。
- `sessions-toast-error`：攔 `DELETE /api/sessions/*` 回 409，再真的按下終止並確認。
  錯誤 toast 的文字是前端的錯誤處理自己拼的，不是我們餵進去的字串。

驗收：`--verify` 110 檔逐位一致；跨行程 `golden_check` 全 PASS；`run-all.sh quick`
**39 支** 0 失敗（多的那支是 scaffold 帶進來的 `e2e_vue_smoke`，dist 已 build 所以它跑得起來），
跳過清單與基線逐字相同。

### 階段 4 後半（二）：讓測試對 vue 模式跑，與第一份紅燈清單

**`run-all.sh --ui vue`。** 模式（quick／--all／--e2e）與「測哪一版前端」是兩個獨立的維度，
所以參數分開收，`--ui` 透傳成 `CLAUDE_PTY_UI` 給每一支測試。預設永遠是 legacy：兩版並存
期間 legacy 那條路的行為必須一個字都不變，預設值一翻，所有既有的紅綠就不再是在講同一件事。

加了一道 dist 保險絲：前端六關的最後一關就是 build，正常情況跑到測試迴圈時 dist 已經在了，
但那一段有兩條會被整段跳過的路（`--e2e` 模式、host 沒有 npm）。legacy 模式下跳過沒差
（只有 `e2e_vue_smoke` 要 dist），**vue 模式下缺 dist 等於每一支瀏覽器測試都跳過，那一輪
什麼都沒測到而畫面上是綠的**。

#### 我修的（測試耦合 legacy）

1. **`id` 白名單太寬。** SPA 的掛載點 `<div id="app">` 變成 dom.txt 的第一行，於是十八場
   每一場的第一行都紅在同一件事上。那正是我在 `golden_scenes` 檔頭寫過的「多包一層
   wrapper 就整份紅」，而我自己加 `id` 的時候又把它放了回來。改成**只記被
   `aria-controls`／`aria-labelledby`／`aria-describedby`／`for` 指到的 id** ——那些才是
   契約（指過去必須指得到），其餘的 id 是實作細節。
2. **靜態資源跨版比對沒有意義。** legacy 載 `app.js`／`app.css`，Vue 載
   `assets/index-<hash>.js`，兩份清單本來就不可能一樣。跨版時略過那一段；同一版之內
   仍然守得住「少載了一個檔案」。
3. **文件請求同理。** SPA 換頁是 router 在前端做的，不會再跟伺服器要一次 HTML。
   跨版時略過，換頁到底有沒有到對的地方由「場景就緒時的網址」那一段守著（跨版全綠）。
4. **`golden_check` 會被一場開不起來的場景整支打斷。** vue 模式下抽屜那場一逾時，
   後面三場就完全沒有比到，而輸出裡「那三場過了」與「那三場根本沒跑」長得一模一樣。
   每一場包 try，開不起來就記成那一場紅並繼續。修完之後多出六條紅 ——那六條**本來就在**，
   只是先前看不見。
5. **網路的差異改印集合差**，不是第一行差異：少打一發會讓後面每一行都對不齊，
   「第一行差異」只會指到位移，看不出真正多了什麼、少了什麼。

#### 一個判斷（可以推翻）

`UI_EXTRA_CALLS`：Vue 版依設計會多打 `/api/bootstrap` 與 `/api/account/bootstrap`
（階段 3 把 Jinja 注入改成 API 的直接結果）。這**不是容忍額度，是規格的一部分**，所以
列成一份窄而且吵的清單：只有這兩條，而且 `golden_check` 每次都把它印出來。階段 5 把
golden 重錄成 Vue 版之後，這份清單應該整個消失。

#### 給 lane C 的紅燈清單（我沒有碰 frontend/src）

| # | 差異 | 證據 | 影響 |
|---|---|---|---|
| A1 | AccountView 還沒搬 | `[data-testid="token-state"]`、`pw-form` 逾時 | e2e_account 全紅、e2e_flow 停在改密碼、golden 兩場開不起來 |
| A2 | TerminalDrawer 還沒搬 | `drawer-pending` 停在「還沒搬到這一版」 | e2e_drawer 全紅、golden 一場開不起來 |
| A3 | picker 選完之後 `data-active` 沒更新 | golden `opt-any active=false` vs vue `active=true` | 2 場 DOM |
| A4 | 對話框開著時，觸發它的那顆列按鈕沒有 disabled | aria `[disabled]` 少了；截圖 x1003..1095 y918..978 | 2 場 aria ＋ 截圖 |
| A5 | 頁尾 `<time>` 與「（N 秒前）」之間少一個空白 | 整行 215px → 211px，兩邊都置中於 636 | **15 場截圖**（頁尾在每一頁） |
| A6 | 每頁多打兩發 `GET /api/auth/me` | 登入頁 1 發、登入後每頁 2 發，共 28 發 | 15 場網路 |

A3 與我今天在 legacy 修的是**同一個 bug**（`renderMenu()` 只在展開時跑），修法可以直接抄。
A5 是十五場截圖唯一的原因：差異只有一條 216x13 的帶狀，文字一模一樣，只是整行窄了 4px。
A6 我沒有放進 `UI_EXTRA_CALLS` ——「每頁兩發」看起來像重複抓取，那是要問的事，不是要
容忍的事。確認是刻意的再放進去。

#### CI

新增 `claude-pty-vue` job，`--ui vue` 跑 quick ＋ golden。**現在是 `continue-on-error`**：
Vue 還沒補完，這個 job 必定紅，它的用途是看得見還差多少，不是擋 merge。什麼時候拿掉：
階段 5 四關全過、這個 job 自然全綠的第一天 ——留著它而忘記拿掉，等於養一個永遠不會擋
任何東西的綠勾。截圖在 CI 上會被平台 gate 跳過（golden 錄在 macOS、runner 是 Linux），
比的是 aria／DOM／API 那三份，跳過會明講。

驗收：legacy `run-all.sh quick` **39 支 0 失敗**、跳過清單與基線逐字相同；
legacy `golden_check` 全綠；vue 模式 52 條 PASS、38 條紅（就是上面那六項）。

### 階段 4 後半：帳號頁與終端抽屜

兩個殼都填起來了。驗法與前半一樣：**拿 golden 的十八個場景錄 Vue 版，跟 legacy 的規格逐字
比**（`config.UI` 在 `pin_all()` **之後**才改成 vue——它會把值釘回 legacy，在它之前設會被
蓋掉，於是「錄 vue」其實錄的是 legacy，全綠但毫無意義）。

lane B 在 `2c0aff2` 補了 DOM 快照，這一輪因此比前半好驗得多：**aria 看不到的差異（hidden
vs 從 DOM 移除、title、data-tip）現在有東西守**，而那正是 1:1 最容易漏的一層。

#### 結果

十八個場景，兩個跑不起來（見下），其餘十六個：

| | aria（兩視口） | dom（兩視口） |
|---|---|---|
| 十六個場景 | **全部逐字相同** | 全部只差一行：`div id=app` |

`div id=app` 是 SPA 的掛載點，每個場景都多這一行。要消掉只能把 Vue 掛到 `document.body`
（mount 時會清空容器，等於連 body 裡的 script 一起拆），為了一行 DOM 換一個不建議的掛法
不值得——而且階段 5 拆舊之後 golden 會從 Vue 版重錄，那一行就是新規格的一部分。
這與 4a 已經裁示過的「body 結構」是同一件事。

#### 被 golden 抓到的一條真差異

`sessions-modal-kill` 與 `sessions-modal-rename` 的 aria 一開始是紅的：

```
-  - button " 終止" [disabled]
+  - button " 終止"
```

舊版 `manifest` 的 click handler 第一件事是 `btn.disabled = true`，最後在 `finally` 還原
——而 **`await dialog(...)` 就在那中間**，所以確認對話框開著的時候，那一列的動作鍵是停用
的。我第一版沒有這個狀態：對話框開著時還能再按一次同一顆鍵。

修法是把「進行中的那一顆」提到 `SessionsView`（`busyAction`＝`<act>-<id>`），三個動作共用
同一個 `withBusy()` 包裝，`finally` 還原。修完兩個場景的 aria 就乾淨了。

**這條沒有任何人會用眼睛看到**（對話框蓋在上面），是 aria 快照抓到的。

#### 被單元測試抓到的一條真 bug

`TerminalDrawer` 的 `onBeforeUnmount` 用了 `CSS.escape`（照抄舊版）——**jsdom 沒有這個
函式**，於是整個 beforeUnmount 拋出，元件拆不掉、抽屜留在畫面上，而 Vue 只印一行
`[Vue warn] Unhandled error during execution of beforeUnmount hook`，畫面沒有任何跡象。

真瀏覽器裡不會發生，但「拆除鉤子拋出就拆不掉」這個形狀值得防：改成有才用，沒有就退回原
字串（sid 是 uuid4 的 hex，本來就沒有需要逸出的字元）。

#### `useTerminalSize`：階段 1.5 的成果搬成 composable

兩道閘、token、debounce、`healGlyphScale`、字級的夾取與持久化全部逐條搬過來。**Vue 真正
帶來的好處是拆除有地方掛**（`onBeforeUnmount` 收 ResizeObserver 與計時器），不是
`nextTick`——後者只保證 DOM patch 刷完，不保證 layout 穩、transition 結束、iframe 內的 JS
跑完，等的東西根本不對（這件事在階段 1.5 就寫過，搬過來之後更明顯）。

九支 vitest 守它，其中三條是「看畫面永遠看不出來」的那種：

- 抽屜還在滑入就不送（第一道閘）；
- **無限動畫不可以把 /resize 永遠卡住**——`getAnimations()` 回的是元素上所有的動畫，哪天
  有人加一條 `animation: … infinite`，它的 `finished` 永遠不會 resolve；
- token：連按時只送最後一次，而且尺寸是**送出的當下**才讀。

⚠ 寫這幾支時踩到一個測試自己的坑：宿主元件沒有 unmount，composable 排的 debounce 與遞迴
輪詢會在**下一支**測試裡醒來、打到那一支剛換上的 fetch 替身，於是計數莫名其妙變成 3。
`afterEach` 一律拆掉。看到「數字比預期多」的第一件事是問「多出來的是不是上一支留下的」。

#### 帳號頁：六個面板拆成六個元件

順序與舊版逐塊對應。幾個照搬的紀律：

- **管理員那三塊整塊 gate**，不是把裡面的按鈕停用：區塊本身若渲染出來，一般使用者會看到
  一張永遠載入失敗的表格，而且知道有這個東西存在。測試連「不該去打那幾條 API」也驗。
- 憑證欄**永遠是空的**（存進去不吐回來），「設過沒」只能靠 placeholder 講。
- 存／清之後**整頁重載**：徽章、chip、按鈕三處狀態同源重畫。SPA 其實可以只重抓 bootstrap，
  但那是階段 5 的最佳化；現在的重點是行為與舊版一致，而重載這條路不可能漏掉任何一處。
- 改密碼與重設密碼**收不乾淨時不報成功**（後端回 200 加實情），而且失敗那條要多留時間讓
  人讀完再跳。這兩條各有一支測試釘著。
- 權限說明的 `**粗體**` 自己拆成片段用 `v-for` 畫，不走 `v-html`——舊版是 `esc()` 之後才
  replace，這一版連那一步都不必（Vue 的插值本來就會逸出）。

密碼欄一律走 `PasswordInput`：舊版是 `enhancePasswordFields()` 掃過去包起來的，包完會多一個
`.pw` 外框與一顆「看一眼」按鈕，**而那顆按鈕在 aria 樹裡看得到**。`dialog({input:{type:
"password"}})` 那條（管理員重設他人密碼）也一樣，所以 `DialogHost` 跟著改。

#### 兩個場景仍然跑不起來（harness 的耦合，不是畫面差異）

> 這一段寫於階段 4 後半（一）合併之前。**該問題已在 `962a9b2` 解決**：兩個場景都改成用真的
> UI 動作觸發（終止→取消、攔 DELETE 回 409 再確認），不再呼叫任何一版的全域函式。

`sessions-toast` 與 `sessions-toast-error` 直接 `page.evaluate("() => toast(...)")` /
`toastError(...)`——那是 `app.js` 的**全域函式**，Vue 版沒有這個全域。這不是 1:1 的差異，
是場景伸手進了某一版的內部實作。

我沒有動 `golden_scenes.py`（那是階段 2 的規格），也沒有為了它在 production bundle 上掛一個
`window.toast`。toast 元件的行為改用真實互動驗：`e2e_vue_smoke` 的「終止 → 取消 → 已取消」
與 vitest 的 toast 測試。要讓這兩場對兩版都成立，得由場景改成用 UI 動作觸發並重錄。

#### `e2e_vue_smoke` 擴到 50 條

補了抽屜（iframe 指到單一入口那條路徑而不是跨 origin 的直連網址、標題列講得出是哪一顆
ttyd、背景 inert、字級讀得到、關掉之後節點真的被拆、inert 收回來）與帳號頁（三塊面板、
管理員的清單與 ttyd 實況、憑證兩態、改密碼的即時驗證、四個密碼欄都有「看一眼」）。

ttyd 用 `page.route` 的替身，**不 import `golden_scenes` 的 `install_drawer_routes`**：
那支模組一 import 就會建暫時的 DB、改 config，而這一支自己已經有一份。

⚠ 又踩到一個 Playwright 的坑：`wait_for_selector('[data-testid=x][hidden]')` 的預設是「等它
**可見**」，而一個 hidden 的元素永遠不會可見，於是那一行必定逾時。要用 `state="hidden"`。

#### 驗收

`run-all.sh quick` 39 支 0 失敗、跳過 14 支與基線逐字相同；前端七關全過；vitest 103 支、
行覆蓋率 87.5%（門檻 70%）；`e2e_vue_smoke` 50 條全過；十六個 golden 場景 aria 逐字相同、
dom 只差 SPA 的掛載點那一行。

### vue-phase1 在 vue 模式下抓到的三項：A3 / A5 / A6

`--ui vue` 這個維度（`b8f415f`）之後，golden 第一次真的拿 Vue 版去對規格。三項紅燈：

#### A3｜選單收起來之後，「游標」指著舊的那一列

`data-active` 是照鍵盤游標 `active` 畫的，而 `active` 只在展開與按方向鍵時才動——**用滑鼠
選完之後它還停在展開當下的那一列**。畫面上看不出來（按鈕的文字是另外畫的，它是對的），
但那份 DOM 就是下一次展開的第一幀，螢幕閱讀器唸的也是它。

legacy 在 `962a9b2` 修的是同一個 bug（`pick()` 裡就地改屬性，不重畫整份選單——重建
innerHTML 會把搜尋框的游標與 IME 選字一起沖掉），golden 已往正確那邊重錄。Vue 版照做：
`pick()` 把 `active` 落到選中那一格。

⚠ 用 `visible` 的索引，不是 `options` 的：畫面上畫的是過濾後的那一份，拿全清單的索引去比，
在有搜尋字串時會指到別的一列。順手把「清單換一批」時的處置從「歸零」改成「落到選中的
那一個」——歸零會讓游標跑到清單頭上，而那一格通常不是選中的那一格。

#### A5｜頁尾少一個空白，十五場截圖全紅

舊版模板 `</time>` 與 `<span class="footer__rel">` 之間是換行，渲染成**一個空白**，整行
215px。Vue 的 `whitespace: "condense"` 會把「只有空白＋換行」的文字節點整個摺掉，於是整行
變成 211px——**十五場截圖全紅，而且紅的位置在頁尾**，看起來像是別的東西壞了。

用 `{{ " " }}` 明寫，它不會被摺。

同一個機制的**反面**也咬了一次，在帳號頁：prettier 把 `</code>` 斷到行尾，而下一行以全形
逗號開頭的文字節點**不是**「只有空白」，所以 condense 只把換行摺成一個空白、不會拿掉——
畫面上是 `setup-token ，把輸出`，整段跟著位移（0.44%、6754 個強差異像素）。用
`</code\n>，` 這個寫法（prettier 自己用來避免多出空白的那一招）。

**兩個方向要一起記**：標籤之間的換行會被拿掉（該有的空白不見了），標籤與文字之間的換行會
被留成一個空白（不該有的空白冒出來）。

#### A6｜每頁多打 `GET /api/auth/me`（登入頁 1 發、登入後每頁 2 發）

先回答派工的問題：**不是 router guard 與 store 各打一次**，是兩個不同的來源：

1. **guard 在登入頁也問了一次**——而那一頁本來就是給沒登入的人看的，那一發必定 401。
   白花一趟往返、在 console 留一行紅字，而 401 的統一處理是「導回登入頁」，在登入頁上
   等於什麼都沒做。「已登入者不該停在登入頁」不必靠它：伺服器在吐這個殼**之前**就會先
   導走（`web.login_page`），而 SPA 內部從別的頁走過來時身分已經在記憶體裡。
2. **登入成功之後又問了一次**——而 `POST /api/auth/login` 的回應**本身就帶著身分**
   （`{user: …}`，與 `/api/auth/me` 同一個來源）。直接收下即可。

兩處修完，**十六個場景的網路序列與 golden 逐字相同**。

剩下兩場（account-user／account-admin）各還有一發，成因是**那兩場是唯一會
`page.goto("/account")` 的**——冷載入一個要登入的頁面時，SPA 記憶體裡沒有身分，而伺服器
已經不再把它印進 HTML。那一發**結構上消不掉**，而且與 `UI_EXTRA_CALLS` 裡那兩條 bootstrap
是同一個性質（階段 3 刻意把模板注入改成 API）。兩條路可以收掉它，都不在這一刀的範圍：
把 `GET /api/auth/me` 加進那份白名單，或由 lane B 把 `user` 併進
`/api/account/bootstrap`（它本來就是「這個帳號的處境」那條，併進去之後冷載入會少一趟）。
**我沒有自己動白名單**——那是規格的一部分，不是容忍額度。

#### 驗收

`--ui vue` 的 `--e2e`：10 支跑了 1 支失敗（golden_check 的那兩行 `/api/auth/me`），
**九支瀏覽器 e2e 全綠**。golden 本身：aria、DOM 合約、截圖全部通過，網路序列 16/18。
legacy 那邊 `run-all.sh quick` 39 支 0 失敗、跳過 14 支與基線逐字相同。

### 階段 4 後半（三）：`user` 併進 `/api/account/bootstrap`

Nathan 的裁示：剩下那兩發 `/api/auth/me`（帳號頁冷載入）**不進白名單**，改成把 `user`
併進 `/api/account/bootstrap`，冷載入只打 bootstrap 一發。`/api/auth/me` 保留不動 ——
登入之後想單獨重新確認身分的路徑仍然需要它。

做法只有一行：`user=g.user`。`g.user` 就是 `/api/auth/me` 回的同一個物件（`auth._to_dict`
產生的），**不是在這裡另外拼一份** ——拼一份就是第二個真相來源，而分岔的那天沒有人會
發現，畫面只是「有一邊怪怪的」。PAT 設過沒（`gitlab_pat_configured`）因此順著它一起出來，
出口仍然只有 `_to_dict` 那一行。

#### 兩條斷言，各擋一種形狀

```python
check("user 與 /api/auth/me 逐欄相同（只在 me：… ；只在 bootstrap：…）", _u == me)
check("user 的欄位齊全（得到 …）", set(_u) == {id, username, is_admin, …})
```

第二條看起來像第一條的重複，其實守的是另一件事：**兩邊都少掉同一個欄位時第一條仍然是
綠的**（兩個相同的字典永遠相等）。變異測試逐一驗過：

| 變異 | 逐欄相同 | 欄位齊全 |
|---|---|---|
| 在 bootstrap 自己拼一份、少 `ttyd_bin` | **紅**（訊息直接指名 `{'ttyd_bin': 'ttyd'}`） | 紅 |
| `_to_dict` 拿掉 `created_at`（兩邊一起少） | 綠（如預期的盲點） | **紅** |

另外補了兩條洩漏面的：多一個出口就是多一個洩漏面，所以再確認一次 PAT 只給布林、
整份 `user` 裡沒有任何 `*_enc` 或 `password_hash`。

#### 三條既有斷言要改，但不是一律改掉

`test_bootstrap.py` 有一節「不重複既有出口」，其中三條在守的正好是相反的性質。逐條判：

- `不夾帶使用者本人` → 裁示直接推翻它，改成正面的「使用者本人在（冷載入不必再打一發）」。
- `不夾帶 PAT 設過沒` → 改成「**只活在 `user` 裡，頂層沒有第二份**」。原本的判準是「有沒有
  出現這個字」，而現在真正要守的是「有沒有第二個真相來源」，那才是這一節的立場。
- `不夾帶 ttyd 選項` → 原本查的是 `"ttyd" not in raw`，太寬：`user.ttyd_bin` 是**這個人
  自己的值**（同源），而 `/api/prefs` 該獨佔的是**選項清單**。改成查 `ttyd_choices`。

驗收：`run-all.sh quick` 39 支 0 失敗、跳過清單與基線逐字相同。

### 冷載入少一趟：身分改從 `/api/account/bootstrap` 來

A6 收尾之後只剩兩場紅（`account-user`／`account-admin`，唯一會 `page.goto("/account")` 的
兩場）：冷載入一個要登入的頁面時，SPA 記憶體裡沒有身分，而伺服器已經不再把它印進 HTML。

裁示是**把 `user` 併進 `/api/account/bootstrap`**（後端由 vue-phase1 做，形狀與
`/api/auth/me` 相同）。那條端點本來就是「這個帳號的處境」，who am I 併進去之後冷載入從
**三趟往返（`/api/bootstrap` ＋ `/api/auth/me` ＋ `/api/account/bootstrap`）變成兩趟**，
而且剩下的兩趟都已經在 `UI_EXTRA_CALLS` 裡——網路序列因此會全綠。

前端這一側：`fetchAccountMeta()` 打一次、把 meta 與憑證填好、把回應交回去；`loadIdentity()`
從那份回應取 `user`。登入那條路不受影響（`POST /api/auth/login` 的回應本身就帶身分，
上一刀已經改成直接收下）。

#### 相容路徑：會退回 `/api/auth/me`，但會喊

兩條線的 commit 合併順序不保證。回應裡沒有 `user` 時退回去問 `/api/auth/me`，並在 console
喊一聲說明為什麼多了那一發——**降級是安全的，但不可以是無聲的**（同 `config.UI` 對不認得
的值的處置）。不喊的話，症狀會是「golden 的網路序列莫名其妙多一行」，而肇因是合併順序，
那要查很久。

⚠ **這條相容路徑有明確的死期**：`/api/account/bootstrap` 帶 `user` 之後它就是死的，
階段 5 拆舊時連同註解一起刪。單元測試同時釘住兩條路（帶 user 時只打一發、不帶時退回並喊）。

⚠ 這一刀**沒有實跑 `--ui vue`**：後端還沒併進來，跑了只會驗到相容路徑。等 ff 之後再跑。

### 階段 4 後半收尾：vue 模式歸零

| 指令 | 結果 |
|---|---|
| `run-all.sh --e2e --ui vue` | **10 支 0 失敗**（八支 e2e ＋ e2e_vue_smoke ＋ golden_check） |
| 其中 `golden_check`（vue） | **109 條全 PASS**：36 aria ＋ 36 dom ＋ 18 network ＋ 19 截圖 |
| `run-all.sh quick --ui vue` | 39 支 0 失敗，跳過 14 支 |
| `run-all.sh quick`（legacy） | 39 支 0 失敗，跳過清單與階段 1 基線逐字相同 |
| `run-all.sh --e2e`（legacy） | 10 支 0 失敗 |

**四關全過**，而且截圖那一關是在錄 golden 的同一台機器上比的，所以 A5（頁尾那個空白）
確實修好了，不是被平台 gate 跳過去的。

#### 收尾時抓到兩個我自己的坑

**一｜dist 保險絲只問「在不在」，不問「新不新」。**

第一輪 `--e2e --ui vue` 有兩條紅：帳號頁仍打 `/api/auth/me`。差一點就當成 Vue 版的 bug
回報出去 —— 實際上是 `--e2e` 模式會跳過前端六關（build 在那裡面），而我的保險絲看到
`dist/index.html` 存在就放行，那份 dist 的原始碼**比工作區舊了兩個 commit**。
`find frontend/src -newer server/static/dist/index.html` 一問就現形。

改成「不在**或比原始碼舊**就 build」。這與 `run-all.sh` 開頭清 `__pycache__` 的理由是
同一個：**測試必須對應現在的原始碼**，而「build 產物悄悄落後」沒有任何跡象。
差別只在一個是 `.pyc` 一個是 `dist/`。

教訓與先前那次變異測試無效是同一條：**看到紅燈，第一件事是確認「我測到的真的是我以為
的那份東西嗎」**，而不是直接把它歸因給被測物。

**二｜`--ui vue` 套得太廣。**

`quick --ui vue` 有三支紅：`test_bootstrap`、`test_web`、`test_gitlab_proxy_conf`。
它們是**伺服端渲染的契約測試** —— 逐條比對「模板注入的值」與「bootstrap API 回的值」
是不是同一個（`data-behind-proxy`、`maxlength`、`MIN_PW`…）。vue 模式下 Flask 出的是
SPA 外殼，那些注入點根本不存在。

紅的原因是「這些測試不適用於這個模式」，不是「有東西壞了」。兩者混在同一輪，紅燈就
不再是訊號。改成 **`--ui` 只套在 `e2e_*` 與 `golden_check` 上**，其餘一律 legacy，
而且執行時把模式印在標題上（`== e2e_account（--ui vue）`），看得出哪幾支跟著走。
階段 5 拆掉 legacy 之後那些測試會跟著模板退場，這條分流也就不必要了。

#### CI

`claude-pty-vue` 的 `continue-on-error` 拿掉了，它現在擋門。同時補一道**跳過上限**：
這個 job 一旦擋門，「安靜地跳過」就是它最可能的假綠燈形狀 —— 瀏覽器沒裝或 dist 沒
build 的話，run-all.sh 會把每一支瀏覽器測試都跳過並回 exit 0，那一輪什麼都沒測到而
綠勾長得跟真的一樣。上限取 15（14 支 docker ＋ claude 憑證那支），實測這一輪是 14。

⚠ 仍然要記著：**這個 job 全綠不等於四關全過。** 截圖在 CI 上被平台 gate 跳過（golden
錄在 macOS、runner 是 Linux），它比的是 aria／DOM／API 那三份。動到版面之後請在本機
跑一次 `./tests/run-all.sh --e2e --ui vue`，那裡才驗得到視覺那一關。

### 階段 5 第一部分：拆 legacy

**淨減 4,800 行**（27 檔 +299/-5102）。刪掉的：`server/static/js/app.js`（2,090 行）、
`server/templates/` 五份（1,909 行）、`deploy/nginx-ui/legacy/`、`tests/test_template_contract.py`。

`server/web.py` 從 210 行收到 110 行：三條頁面路由現在一律 `_spa_shell()`，`_page()` 那層
包裝與三個 template global（`asset_url` / `persist_dir` / `build_info`）一起退場。後兩者的值
現在由 `/api/bootstrap` 出，那才是 SPA 拿得到的地方。`config.UI` 切換器、compose 的
`${CLAUDE_PTY_UI:-legacy}`、`.env.example` 那一段、preflight 的降級診斷、`run-all.sh` 的
`--ui` 分流與 app.js 語法檢查，全部一起走。

#### 逐條判，不是逐條刪

派工說「守的性質若仍成立就改成對 config／API 驗，純守模板的刪」。這一段是整個第一部分
最花時間、也最容易做錯的地方：**看到紅燈就刪，等於把測試覆蓋當成一個要清掉的障礙。**
所以每一條都先問「它守的到底是什麼」，再問「那件事現在還存在嗎、由誰負責」。

| 原本的斷言 | 判斷 | 去處 |
|---|---|---|
| `test_template_contract`：模板 class 在 CSS 裡都有 | **對象消失** | 刪 |
| 同上：模板內嵌 `<script>` 語法過 | **對象消失** | 刪（性質由 `vue-tsc` 與 `vite build` 接手，而且接得更緊） |
| 同上：ttyd 那節被 `{% if is_admin %}` 包住 | **裂成兩半** | 畫面那半 → 前端 vitest；**後端那半 → 新的 `test_admin_endpoint_gate.py`** |
| `test_web`：TAINTED 掃模板／app.js 的 `${}` 有沒有 `esc()` | **形狀換了** | 改成掃 `frontend/src` 有無 `v-html`／`innerHTML` 寫入 |
| `test_web`：抽屜只吃同源 `view.path` | **還在，搬家了** | 來源從 `sessions.html` 換成 `SessionsView.vue` |
| `test_web`：三頁都有頁尾 | **裂成兩半** | 值 → `/api/bootstrap`；「每頁都畫得出來」→ golden 的 aria（**18/18 場都有**，比原本三頁多） |
| `test_web`：`data-behind-proxy` 是 0 還是 1 | **搬到 API** | `test_bootstrap` 已逐欄驗；這裡改驗三條路由吐的是同一份殼且 `no-store` |
| `test_bootstrap`：對照模板那一整節（約 80 行） | **遷移期的鷹架** | 刪，接手的是 `golden_check`（拿 Vue 版對照 legacy 錄的規格，正是「1:1 還原」本身） |
| 同上：`data-cli` 四處同源的第四處 | **還在，換了位置** | 改驗「招牌那個 Vue 元件真的讀 API 給的 `defaultCli`，不是寫死 `"claude"`」 |
| `test_gitlab_proxy_conf`：帳號頁說得出是哪一台 | **搬到 API** | 驗 `/api/account/bootstrap` 的 `gitlab.host` |
| 同上：畫面上不出現 PAT | **搬到 API，而且更嚴** | 舊的只看一頁 HTML，新的看畫面拿得到的**每一份**資料 |
| 同上：畫面講得出輪替語意那條準則 | **還在，搬家了** | 改驗 `GitlabPatPanel.vue` 的文案（同一種靜態檢查，換成現在的所有者） |
| `test_nginx_contract`：legacy 片段一條指令都沒有 | **對象消失** | 刪（vue 片段那幾條照舊） |
| `e2e_settings`：picker 掛載點不准帶 class | **機制消失** | 刪。**查證過才刪**：`grep -rn 'className\s*=' frontend/src/` 一個結果都沒有，Vue 版沒有「掛載點被吃掉 class」這個東西 |
| CI：legacy 片段不改變舊路（掛空片段驗 502） | **對象消失** | 刪 |

新增的 `tests/test_admin_endpoint_gate.py` 值得單獨講：它守的是**前端 gate 只是禮貌，
後端那一行才是門**。端點名從 `frontend/src` 撈（排除 `__tests__/`，那裡面的字串是 vitest
的 mock 路由表，撈進來會把「前端真的在打」變成「測試檔裡提過」），再驗後端那條路由上面
緊接著就是 `@admin_only`。附一條反向 case 確認 regex 抓得出「沒掛」的情況。

#### `innerHTML` 歸零

`grep -rn innerHTML server/ frontend/src/` 的結果只剩**註解**（三個 Vue 元件在講舊版怎麼做的、
一條 CSS 註解）與 `__tests__/` 裡的 `document.body.innerHTML = ""`（vitest 清場）。
沒有任何一處是畫面在拼 HTML。

守它的是 `test_web` 那道新的靜態守衛。**oxlint 接不住這一條**：`frontend/.oxlintrc.json`
只開了 typescript／unicorn／oxc 三個 plugin，沒有 vue plugin，`v-html` 對它只是一個普通屬性。
判準是**用法**不是**出現過這個字**（`v-html` 要跟著 `=`、`innerHTML` 要跟著賦值），
否則那幾條講舊版的註解會全部誤報，而會誤報的守衛最後只會被關掉。附了正反兩條 case。

#### CI

`claude-pty-vue` 併回 `claude-pty`：兩版並存期間它是必要的，現在兩個 job 跑的是同一件事。
併回來的 job 要裝 playwright（八支 e2e 加 golden_check 都吃它）。

跳過上限的註解**改正了**（fable 快審 4b 低 3）：我先前寫「14 支 docker ＋ claude 憑證那支」，
那是錯的 —— `test_entrypoint_human_path` 同時需要 docker，quick 模式下它是被 docker 那道
gate 擋掉的，已經算在 14 裡面。第 15 支是**前端相依掃描**（trivy 沒裝在 runner 上）。

#### 驗收

`run-all.sh quick` **39 支 0 失敗**、跳過 14 支（本機有 trivy）；`--e2e` **10 支 0 失敗**
（含 golden_check 109 條）；`ruff@0.16.3` check 與 format 全過。

golden 仍是**從 legacy 錄的那一份**，`golden_check` 靠 META 的 `ui=legacy` 知道自己在跨版
比對。`golden_scenes.CURRENT_UI = "vue"` 是常數不是設定值；重錄成 Vue 版是第二部分的事，
在那之前**不要跑不帶 `--verify` 的 `golden_record.py`**（跑了就是把現況覆寫成規格），
那條警告寫在 `golden_record.main()` 的註解裡。

### 階段 5 前端側：把「為了 1:1 才存在的東西」全部拆掉

舊版還沒刪，但 Vue 版這邊為了與它對齊而背的幾樣東西已經可以還了。fable 快審 4b 的一條中度
（除錯碼進了正式碼）也在這一刀。

#### 我漏掉的除錯碼，以及它為什麼漏得掉

`TerminalDrawer.vue`、`SessionsView.vue`、`views.spec.ts` 裡各有 `console.log("DBG…")`——
那是查「抽屜關不掉」那個 bug 時加的，我以為清乾淨了。

**漏掉的機制值得記**：我的批次修改腳本是「連續幾個 replace，最後寫一次檔」。中間有一個
assert 失敗時，**前面已經成功的 replace 全部跟著丟掉**，而我看到後來那一次跑出 `ok` 就
當成整批都套用了。那次失敗的是 `CSS.escape` 那一段，於是 DBG 那幾行原地不動。

修法不是「下次小心一點」：oxlint 開 `no-console`（放行 `warn`／`error`）。忘了拿掉的除錯
輸出從此在 lint 就紅。

⚠ `useTerminalSize` 那一支 `console.log` **是刻意留的**：它是要靠 localStorage 明確打開的
診斷開關（照搬舊版的 `sizeDebug`），平時一個字都不印。那一行單獨放行，不是把規則放寬——
「忘了拿掉的輸出」與「明確打開的診斷」不是同一件事。

#### 三樣相容品，死期到了

1. **`loadIdentity()` 的相容路徑**（回應沒有 `user` 就退回 `/api/auth/me` 並喊一聲）。它
   存在的理由只有一個：兩條線的合併順序不保證。後端進來之後它就是死的。
   順手把「收下身分」搬進 `fetchAccountMeta()` 本身——呼叫端就不必記得「存完 PAT 之後
   `user.gitlab_pat_configured` 也變了」，那種要靠人記得的事遲早會漏掉一處。
2. **`#cred-data` 那個 `<script type="application/json">`**。它在這一版**從來沒有讀者**，
   純粹是為了 DOM 與舊版一致而由 `onMounted` 建出來的。舊版模板那一行由 vue-phase1 一起刪。
3. **`FilterBar` 的 `toggled`**。階段 4 為了 1:1 照抄了舊版的一個小洞：`filterBar.inert`
   只在 `setFiltersOpen()` 裡設，而那支只有點了篩選鍵才會跑——所以剛進站、篩選列收著的
   時候身上沒有 `inert`，鍵盤使用者 Tab 得進一塊看不見的區域。現在改成**收合就 inert**，
   那個 prop 整個拿掉。golden 由 vue-phase1 從 Vue 版重錄。

#### 存或清憑證：重抓一發，不整頁重載

舊版是 `setTimeout(() => location.reload(), 900)`。在一個每頁都要重跑 Jinja 的架構下，那是
最可靠的做法——徽章、chip、按鈕三處狀態同源重畫，不可能漏掉任何一處。

SPA 不必付那個代價：`/api/account/bootstrap` 一發就把憑證、限制、身分全帶回來，而那三處
本來就是照它畫的。⚠ 但**欄位要自己清**：舊版靠整頁重載順便清掉，不重載就得明寫——不清的話
畫面會停在「已經存進去了，但輸入框裡還留著剛剛那把 token」，而那是最不該留在畫面上的東西。
測試同時釘住三件事：有重抓、**沒有** reload、欄位清空。

#### 驗收

vitest 106 支、行覆蓋率 87.6%（門檻 70%，派工要求不低於 87%）；oxlint 的 `no-console` 對
著現在的樹是綠的（拿掉那一行單獨放行就會紅，驗過）；legacy 的 `run-all.sh quick` 39 支
0 失敗、跳過 14 支與基線逐字相同。

⚠ **這一刀沒有跑 `--ui vue`**：vue 模式全套由 vue-phase1 統一跑（兩邊同時改會互相蓋掉），
而且第 3、4 點會讓 golden 需要從 Vue 版重錄，那也是他們那一側的動作。

### 階段 5 完成：golden 從 Vue 版重錄成新規格

#### 重錄前的差異＝這次的規格變更紀錄

重錄之前先跑一次跨版比對，把差異完整記下來。**掀開 strip 機制之後**的完整清單
（用 `CURRENT_UI="legacy"` 強制嚴格比對量的，那份改動沒有 commit）：

| 類別 | 差異 | 場數 | 判斷 |
|---|---|---|---|
| DOM | `#filter-bar` 收合時多了 `inert` | 22 | **改善**。lane C 的 a11y 修正：收起來的篩選列不該留在 Tab 序裡 |
| 網路 | `+GET /api/bootstrap`、`+GET /api/account/bootstrap` | 20 / 18 | **設計**。階段 3 把 Jinja 注入改成 API 的直接結果 |
| 網路 | `-GET /static/js/app.js`、`-app.css` → `+/assets/index-<hash>.{js,css}` | 各 18 | **必然**。手寫的兩支換成打包產物 |
| 網路 | `+/assets/SessionsView-<hash>.js`、`+AppShell…js`、`+AccountView-<hash>.js` | 16/16/2 | **新能力**。路由層 code splitting；`AccountView` 只在帳號頁載入（2 場），那是真的契約 |
| 網路 | `-GET /`（換頁那一發文件請求） | 16 | **必然**。SPA 的換頁是 router 在前端做的 |
| 截圖 | 無 | 0 | **像素級 1:1**。十九張全頁截圖對著 legacy 錄的規格 0.00%，不是落在容忍額度內 |

截圖零差異這件事值得單獨講：那代表 A5（頁尾少一個空白）與其餘視覺修正之後，Vue 版
**逐像素**等於舊畫面。1:1 不是形容詞。

#### 重錄時抓到兩個新的不穩定源

**一｜Vite 的檔名帶內容雜湊。** `index-DhIKEuyr.js` 每次 build 都不一樣，錄進 golden 的話
下一次 build 就紅，而紅的原因與介面毫無關係。**`--verify` 抓不到這一類**：兩次錄製共用
同一份 build，雜湊當然一樣（與階段 2 的 `users.created_at` 完全同一個形狀）。

修法是正規化成 `-<hash>`，**不是整段丟掉**：哪幾個 chunk 會載入是真的契約 ——
`AccountView-<hash>.js` 只在帳號頁載入，哪天有人把它靜態 import 進 `AppShell`，這裡就會紅。

第一版的正規化**做過頭**了：它把 `/static/images/01-circuit-board-transparent.webp` 也當成
雜湊抹掉，於是「登入頁用的是哪一張插畫」不見了 —— 而那正是 `pin_all()` 特地釘死的東西。
收斂成只套在 `/assets/` 上。

**二｜字型是按需抓的。** `account-admin` 第一次錄有 `fa-brands-400.woff2`、第二次沒有：
瀏覽器只有在真的要畫到某個字面時才去要那份 woff2，而「那一刻有沒有在快照之前發生」會飄。
這一次**是 `--verify` 抓到的**（跨行程那輪剛好兩邊都有），與上一項恰好互補：兩種驗證各自
看得到對方看不到的東西。

修法是把需求講明：叫每一個宣告過的 font face 都載入，再等 `fonts.ready`。代價是
「這一場用到了 brands」變成「這一頁宣告了 brands」，那個損失可以接受 —— 畫面上真的有那顆
圖示由截圖與 aria 守著，而一個會隨機紅的 golden 最後會被整支關掉。

#### 拆掉的跨版機制

`UI_EXTRA_CALLS`、`strip_expected_extra_calls`、`strip_assets`、`strip_navigations`、
`CURRENT_UI`、`golden_ui()`、META 的 `ui=` 欄位、`golden_check` 的跨版分支，全部一起走。
META 那一欄拿掉而不是改成 `vue`：永遠只有一個值的欄位只會讓人以為還有第二版可以比。

#### 驗收

- `--verify` 連錄兩次（中間重 seed）：**110 個檔案逐位一致**。
- 跨行程 `golden_check`：**109 條全 PASS**。
- **rebuild 之後再跑一次**：仍然全綠（雜湊正規化的實測，不是推論）。
- 變異測試：

| 變異 | 紅的是 |
|---|---|
| 把「篩選」改成「篩選條件」 | 28 條 aria ＋ 4 條截圖 |
| `kind: "gitlab"` 打成 `"gitlabb"` | 24 條 DOM（aria／網路／截圖全綠） |

⚠ 這兩個變異**第一次都是無效的**：第一次改的字串不在 production 原始碼裡、第二次改到了
`__tests__/lib.spec.ts`（不進 build）。兩次都是「沒紅」，而沒紅的第一個解釋永遠是
「我的變異真的改到東西了嗎」。這已經是這個專案第三次記到同一條。

- `run-all.sh quick` 39 支 0 失敗；`--e2e` 10 支 0 失敗；`ruff@0.16.3` 全過。
- golden：18 場、110 檔、6.1 MB。

#### 文件

`docs/adr/0020-frontend-vue3.md`（背景、決策、後果）：三份快照各守什麼、三道截圖閘各接
哪種形狀與實測數字、截圖只能同平台比的限制、「重錄等於改規格」、以及**已知不涵蓋的**
三件事（互動時序歸 e2e、元件內部分支歸 vitest、`dom.txt` 只記白名單）。

README：架構一覽補前端與 build 步驟、測試指令補 `--e2e` 與 golden 三條、覆蓋率段落補上
前端的 vitest 門檻並寫明**兩邊的數字不要合著看**（後端那個是診斷，前端那個是 gate）。

### 完整審查修正

判定可交付之後的收尾。M1 的前端側給 lane C，其餘主樹側在這一刀。

**M1（後端側）｜`web.py` 的 `/login` 註解在說一件不成立的事。**
原本寫「這一條**不能交給前端做**：SPA 要先載入、先問一次我是誰才知道自己已經登入了」。
去查證之後那句話反過來才對：正式部署的 `/login` 由 nginx 直接吐 `index.html`
（`nginx-ui/vue/ui.conf` 的 `location = /login`），**請求根本不會走到 Flask**，那個 302
只在 dev、e2e、以及 nginx 片段沒掛上的軟著陸情況下生效。prod 那條路上「已登入者不該停在
登入頁」是 SPA 守衛的事。註解改成照實說，並寫明它覆蓋的是哪幾種情況。

⚠ **`frontend/src/router.ts:48-49` 有一句對稱的錯**（「伺服器在吐這個殼之前就會先導走，
見 `web.login_page`」），那在 prod 同樣不成立。那是 M1 的前端側，歸 lane C。

**L1｜`/assets/` 的 `add_header … always`。** 沒有 `always` 時 add_header 只套在 2xx／3xx；
加了它連 **404 也會帶著 `immutable, max-age=31536000`**。改版之後舊的殼會去要一個已經不存在
的 `index-<舊雜湊>.js`，那一發 404 被快取一年，**清了快取才救得回來**，而症狀是一片白畫面、
看不出跟快取有關。拿掉 `always`，`test_nginx_contract` 補一條斷言把它釘住。

**L3｜`NEEDS_DIST` 只列了一支。** legacy 拆掉之後八支 e2e 與 golden_check 全部吃 dist，
只列 `e2e_vue_smoke` 的話，其餘那幾支在缺 dist 時會以一串看不懂的逾時失敗，而真正的原因
不會出現在跳過清單上。改成全部列進去。

stale 判準的 `find` 清單補 `server/static/css/app.css`：**SPA 的樣式不是打包進 bundle 的**，
`frontend/index.html` 直接引用 `/static/css/app.css`。它一改畫面就變，而 `frontend/` 底下
一個字都沒動 —— 少了這一項，golden 會拿一份舊畫面去比新樣式。改完實測：`touch` 那個檔案
之後跑 `--e2e`，確實會先補一次 build。

**L7｜五處過期註解**：Dockerfile 的「legacy 模式下它只是躺著」、compose 的同一句、
nginx.conf 的「legacy 從來不要求 /assets/*」、`golden_record.py` 檔頭的「（legacy）介面」、
`test_bootstrap.py` 的「照著 legacy 錄下來的」。最後那一條順手把 `d79c87b` 之後的狀態寫清楚
（守的從「與舊版等價」變成「不要回歸」）。

**L8｜README。** `--e2e` 的支數 8 改 10（九支 e2e ＋ golden_check）；golden 那三條指令補上
完整的 `--with` 清單。補的時候踩到一件事並寫進 README：**先塞進一個變數再 `uv run $DEPS …`
在 zsh 是壞的** —— bash 會做單字分割、zsh 不會，貼過去只會得到一句
`For more information, try '--help'`，而那個錯誤看起來完全不像引號問題。所以三條都逐字寫全，
而且是**真的貼進終端跑過一次**才寫進去的。

**trivy｜control image 的 3 筆 CRITICAL。**
先查 `deploy/Dockerfile`：**已經有 `apt-get upgrade -y`**（line 128，旁邊還有一段講 base
image 的 CVE 節奏不歸我們管）。所以照指示重 build 再掃一次：

```
docker build --no-cache -f deploy/Dockerfile -t claude-pty-control:rescan .
trivy image --scanners vuln --severity CRITICAL claude-pty-control:rescan
```

結果 **3 筆，全部是 `perl-base 5.40.1-6`**（CVE-2026-8376、CVE-2026-13221、CVE-2026-42496），
而三筆的 `FixedVersion` 都是**空的** —— Debian 上游還沒有修補版本。

所以這三筆不是「政策漏了」而是「現在無藥可用」：`apt-get upgrade` 已經在做它能做的事，
再多做一次也不會變。**沒有新增任何東西**（那是既有政策），只記錄這個事實與量測方式，
下次 base image 更新之後重掃即可。

**驗收**：`run-all.sh quick` 39 支 0 失敗；`test_nginx_contract` 42 條全過；
`ruff@0.16.3` check 與 format 全過。

### 完整審查 M1：已登入者冷載入 `/login` 會停在登入表單

`web.login_page` 裡那句「已登入就 302 回 `/`」在**正式部署上不會發生**：nginx 直接把
`index.html` 從磁碟吐出來（`deploy/nginx-ui/vue/ui.conf` 的 `location = /login`），根本不經
過 Flask。而守衛先前刻意跳過登入頁的身分探測，理由是「那一頁本來就是給沒登入的人看的，
那一發必定 401」。

**那個理由漏掉了一種人**：已經登入、然後直接輸入 `/login` 的。舊版對他是伺服端一句 302，
新版讓他停在登入表單前面。

修法就是守衛對 `/login` 也探測一次，拿到身分就 `{ path: "/" }`。三件事值得記：

1. **探測的 401 是答案，不是錯誤。** `api()` 收到 401 的統一處理是「導回登入頁」——在一個
   *正在前往* `/login` 的導覽中間再開一次導覽，會把當下這次打斷。所以探測那一發要關掉那個
   統一處理（`handleUnauthorized: false`），由守衛自己照「有沒有拿到身分」決定去哪。
2. **`await` 讓那個窗口不存在。** `web.py` 的註解說「SPA 要先載入、先問一次才知道自己已經
   登入，那期間畫面上是登入表單」——實際上不會：守衛是 `await`，探測沒回來之前**沒有任何
   頁面被畫出來**，已登入者看到的是一瞬間空白然後直接到 `/`。
3. **守衛抽成具名的 `authGuard` 匯出。** 它是這個 SPA 對「你是誰、該去哪」唯一的判斷，值得
   單獨測；測試那邊自己建的 router 掛的是**同一支**，不是照抄一份會漂走的複本。

順手：`index.html` 與 `main.ts` 兩處過期註解（一處還指著早就實作完的 TODO(階段 3)，一處
還在講「與 legacy 版互跳」）。

#### 假後端也要照真的那樣有狀態

改完之後四支登入頁的測試紅了——它們的假表無條件回身分，於是守衛把每一支都導去 `/`。那不是
測試壞了，是假後端沒有模型：真的伺服器在登入**之前** `/api/account/bootstrap` 是 401。
改成有狀態（`POST /api/auth/login` 把旗標翻過來）之後，測試才分得出「沒登入停在這裡」與
「已登入被導走」——而這一版新加的正是後者。

#### ⚠ golden 需要重錄，但我沒有動它

這一刀讓**每一場**的網路序列多一發 `GET /api/account/bootstrap`（每一場都走 `_login`，
而登入頁現在會探測一次）。golden_check 因此 18 場全紅，**而且只紅在「網路呼叫一致」這一條**
——aria、DOM 合約、截圖全部通過。

那是**規格的變更**，不是回歸。但 golden 自己的紀律寫得很清楚：「看到紅燈就順手重錄，等於把
『我改壞了』寫成『這就是新的對的樣子』，這道防線當場消失」。所以我沒有重錄，把差異、成因與
「只差那一行」一起交出去，由派工方決定。

### 最後一刀：M1 之後重錄 golden

#### 規格變更

lane C 的 M1（`ebaa344`）讓守衛在進 `/login` 之前也探測一次身分，於是每一場多一發。
重錄前先跑一次 `golden_check` 記下現況：**18 條網路紅，其餘 91 條全綠**（aria、DOM、
截圖一條都沒動）。重錄之後的 diff 逐字驗過：

```
新增：GET /api/account/bootstrap × 18
移除：無
```

**18 行新增、0 行移除**，位置固定在序列開頭那一段：

```
GET /login
GET /api/bootstrap
GET /api/account/bootstrap   ← 新增的那一發（守衛的身分探測）
POST /api/auth/login
GET /api/account/bootstrap   ← 登入後原本就有的那一發
```

未登入時這一發回 **401，而那是預期的答案不是錯誤** —— 守衛要的就是「你是誰」，401 就是
「沒有人」。它不走全域的 401 處理（見 store 的 `probe`），否則會在一個正在前往登入頁的
導航裡再導一次。

#### 驗收

`--verify` 兩次逐位一致（110 檔）、跨行程 `golden_check` 全綠、**rebuild 之後再跑一次仍綠**。

#### `router.ts` 的過期註解

派工要我改，但**去看的時候已經被 lane C 在 `ebaa344` 一起修好了**，而且寫得比原本要求的
更完整（明說「伺服端那句 302 在正式部署上不會發生：nginx 直接把 index.html 從磁碟吐出來」）。
`grep -rn "伺服器在吐這個殼之前\|先導走" frontend/src/` 零結果。**沒有做多餘的改動。**

### 交付

| 項目 | 數字 |
|---|---|
| `run-all.sh --all`（含 docker） | **50 支 0 失敗**，跳過 3 支 |
| `run-all.sh --e2e` | **10 支 0 失敗**（九支 e2e ＋ golden_check，109 條） |
| 跳過的那 3 支 | claude 憑證（不該進 CI）、`test_firewall_ssh_gate` 與 `test_trivy_volume`（macOS 上驗不到，見 `docs/linux-acceptance.md`） |
| 後端 coverage | `server/` 整體 **84%**（2,972 語句、470 沒跑到） |
| 前端 vitest | **108 條全過**，行覆蓋 **88.32%**（門檻 70%，是 gate） |
| 前端 lint／typecheck／prettier | 全過 |
| `ruff@0.16.3` check／format | 全過 |
| golden | **18 場、110 檔、6.1 MB** |
| 測試檔數 | 53（`test_*.py` ＋ `e2e_*.py` ＋ `golden_check.py`） |
| 這條分支的 commit | **41 顆**（33 顆非 merge） |
| 淨行數 | 211 檔 **+25,433 / -4,759** |

兩個數字要一起看才不會誤讀：**淨增兩萬行**裡有 6 MB／110 個 golden 檔案（那是規格資產，
不是程式碼），而**刪掉的四千七百行**是 legacy 的模板與 `app.js`。真正的「手寫前端程式碼」
是變少的：舊版 `app.js` 2,090 行 ＋ 模板 1,909 行，換成一組有型別、有元件邊界、有單元
測試的 Vue 原始碼。

⚠ 覆蓋率那兩個數字**不要合著看**：後端 84% 是**診斷**（回答「哪些路徑從來沒被走過」，
不當 gate），前端 88.32% 是**門檻**（低於 70% 就紅）。

### L4：版號登入後才給

#### 裁示與它改掉的前提

`/api/bootstrap` 是公開的，原本回四件事。當初寫下的理由留在 `server/app.py` 的
`_PUBLIC_ENDPOINTS` 旁邊：「它回的四件事今天就印在未登入者拿得到的 /login 上（頁尾在
base.html，三頁共用）。**要收緊得先收緊登入頁，不是先收緊這一條**。」

L4 就是去收登入頁。所以這一刀改的不是那段論證，是它的**前提**：

| 欄位 | 從前 | 現在 |
|---|---|---|
| `behind_proxy` | 公開 | 公開（不變） |
| `login_art` | 公開 | 公開（不變） |
| `persist_dir` | 公開 | **`/api/account/bootstrap`（要登入）** |
| `build`（modules、built_at） | 公開 | **`/api/account/bootstrap`（要登入）** |

搬的那兩個是**主機的內部事實**：宿主機上的一個絕對路徑，以及「這台在跑哪一版
claude-pty、哪一版 ttyd」。對未登入的人它們沒有任何用途，只有一個效果：把「該打哪一個
已知漏洞」直接印在門口。

⚠ **兩邊一起收才是真的收。** 只把 API 關掉、畫面照舊去要，是把一個 404／401 寫進頁尾；
只把畫面拿掉、API 照舊公開，是「讓人以為收緊了」，而 `curl` 兩秒就拆穿。所以同一刀動了
四個地方：端點、store、頁尾元件、以及 golden。

#### 分界線仍然是 gate，不是主詞

搬進 `/api/account/bootstrap` 之後，那條端點裡混了兩種東西：原本的「**這個帳號**的處境」
（憑證、限制值、GitLab），和新來的「**這台機器**的事實」（版號、路徑）。看起來不整齊，
但**照 gate 切是對的**：它們要的都是「先證明你是誰」，而這條端點就是那道 gate 之後的
第一發，登入後的每一頁本來就都會打它。照主詞切的話 `build` 會自己佔第三條端點，代價是
登入後的每一頁為了分類的整齊多一次往返。原地留了註解講這件事。

#### 登入頁的頁尾：整段不畫，不是留一個空殼

派工給了兩個選項（只顯示品牌／整段不畫），要求照 legacy 未登入時的視覺挑最接近的一個。
去翻了 legacy 的 `base.html`（`06472a0^`）：**頁尾裡只有那一排版本膠囊與建置時間，沒有
品牌、沒有其他任何內容**。於是：

- 「只顯示品牌」是**新增**一個舊版沒有的東西，離 legacy 更遠。
- 「留一個空的 `<footer>`」也不行：`.footer` 有 `border-top: 1px solid var(--color-border)`
  與上下 margin，空的話登入表單下方會浮出一條沒有內容的橫線，那同樣是 legacy 沒有的
  樣子，而且看起來像壞掉。

兩個選項裡「整段不畫」是**沒有多出東西**的那一個，所以選它（`AppFooter.vue` 的 `v-if`）。

⚠ 判斷條件寫成 `modules.length > 0 || built`，不是只看 `modules`：`built_at` 可以單獨
缺席（build arg 沒給就是 null），那時仍然要畫出那一排膠囊。**「答不出來」與「不給」是
兩件事**：前者留白並在 tooltip 講原因（`版本未知` / `commit 未知`，那是頁尾原本就有的
立場），後者連頁尾都沒有，不會有人以為系統答不出自己的版本。

#### 登出：SPA 換頁不會讓 store 消失

差點漏掉的一處。登出走的是 `router.push("/login")`（SPA 內換頁），**不是整頁跳轉**，所以
store 活著、`meta` 裡那份版號與主機路徑會原封不動跟著人回到登入頁，頁尾照樣印出來。那正
是這一刀要收的東西，只是換了一條路徑洩漏。`logout()` 因此把「要登入才給的那些」還原成
預設值，公開的兩個（`behindProxy` / `loginArt`）留著：它們本來就不需要身分。

#### `applyMetaToRoot()` 要打兩次

`data-persist-dir` 寫在 `<html>` 上，抽屜（runtime 才建）從那裡讀。從前它在進站那一發
`loadPublicMeta()` 就寫得上去；現在那個值要等 `/api/account/bootstrap`，所以
`fetchAccountMeta()` 裡要**再寫一次**。漏掉的話抽屜標題列會少一整行，而且不會有任何錯誤，
golden 的 `drawer-open` 那一場是唯一會紅的地方。

#### 反向斷言：拿**值**去找，不是找欄位名

`tests/test_bootstrap.py` 新增一節，把公開端點的**完整回應字串**抓下來，逐一拿真的值去找：

- `config.DATA_BIND` 的值（`/home/nathan/persistent-data`）；
- `version.summary()` 每一個模組的 `version` / `commit` / `built_at` / `detail` / `name`
  （ttyd 的版本字串 `1.7.7` 就在這一批裡）；
- 外加欄位名 `persist_dir` / `build` / `modules` / `built_at` / `commit` / `version`。

⚠ **只找欄位名是守不住的**：有人把同一批值換個鍵名放回去（`meta`、`env`、`info`…），
找鍵名的斷言照樣全綠，而洩漏一個字都沒有少。要守的是「那些值不准出現」，判準就必須是
值本身。

⚠ 迴圈裡跳過空值（`"" in s` 恆真），所以補了一條 sanity：「上面真的驗到了東西（N 個非空
的版本字串）」。沒有它的話，`version.summary()` 哪天回一份空的，那個迴圈會一條都不跑、
一條都不紅，而這一節看起來仍然是綠的。

#### 兩處假後端因此對不上真的

改完之後兩支測試紅了，兩支都不是「測試壞了」，是**假後端沒有跟上契約**：

- `tests/test_web.py` 的頁尾那一節拿的是**未登入**的 client，值搬走之後它拿不到 `build`。
  改成登入過的 client，而且**另開一支新的**：那一節前面有幾節動過密碼，`password_version`
  一跳舊 cookie 當場作廢，沿用的話錯誤訊息會長得像「bootstrap 少了 build 欄位」，完全指
  錯方向。
- `frontend/src/__tests__/account.spec.ts` 有一份手寫的 `/api/account/bootstrap` 假回應
  少給了新的兩個欄位，於是 `fetchAccountMeta` 在拆 `d.build.modules` 時拋、外層的 catch
  把它吞掉，症狀是「憑證 chip 沒轉綠」。補齊欄位並在原地留了註解講這個症狀怎麼來的。

#### golden：重錄了 login-empty 與 login-error 兩場

`golden_record.py` **不支援單場重錄**，所以是全錄（110 檔），但 diff 只准落在受影響的場景，
逐條列出來：

| 檔案 | 變動 |
|---|---|
| `login-empty/aria.1280x800.txt` | −8（整個 `contentinfo`） |
| `login-empty/aria.390x844.txt` | −8 |
| `login-empty/dom.1280x800.txt` | −3（`footer testid=footer` 與兩個 `footer-mod`） |
| `login-empty/dom.390x844.txt` | −3 |
| `login-empty/screen.1280x800.png` | 全頁高度 956 → 800 |
| `login-error/aria.1280x800.txt` | −8 |
| `login-error/aria.390x844.txt` | −8 |
| `login-error/dom.1280x800.txt` | −3 |
| `login-error/dom.390x844.txt` | −3 |
| `login-error/screen.1280x800.png` | 全頁高度 956 → 800 |

**10 個檔案、44 行刪除、0 行新增**，其餘 16 場與 `META` 一個位元都沒動。

⚠ **`network.txt` 兩場都沒變**，而那是這次唯一值得單獨講的一條：畫面少了頁尾，網路序列
卻一模一樣：`GET /api/bootstrap` 照打（`login_art` 還在那條），守衛的 `GET
/api/account/bootstrap` 探測也照打（它回 401，而 401 就是答案）。**少的是回應裡的欄位，
不是一發請求**，所以 golden 的網路那一份完全看不到這次改動。這也是為什麼另外三份非有
不可。

⚠ 登入後的十六場**全綠**，那是這次最有價值的一條反向證據：頁尾在登入之後畫出來的樣子
一個像素都沒變，證明搬家搬對了地方、而不是把頁尾弄壞了。

#### 一個**先前就存在**的截圖不穩定源（沒有動它）

`golden_record.py --verify`（連錄兩次逐位比對）在 `login-error/screen.1280x800.png` 上
紅了：**8 個像素、單一色版差 1**，位置在主要按鈕的圓角邊緣。

先查它是不是這一刀弄出來的：把改動 stash 掉、重 build、連錄六次，**基準線上一樣會飄**
（同一顆按鈕的圓角，2 個像素、單一色版差 1）。所以這是 chromium 圓角反鋸齒的算繪雜訊，
**先前就在**，`--verify` 只比兩次錄製，先前那一次是擲銅板擲贏了。

沒有動它，理由兩條：

1. **釘不了。** 它不是「哪個來源在飄」（那種可以在 `pin_all()` 釘死），是同一份輸入算出
   兩組差 1 的像素。要壓掉它只能改 CSS 或放寬閾值，而放寬閾值正是 ADR 0020 明文拒絕的
   那條路。
2. **`golden_check` 不受影響。** 三道閘（比例 ≤1%、無實心 5x5 塊、強差異 ≤400）對 ±1 的
   反鋸齒雜訊全部無感：實測重錄後 `golden_check` 跨行程連跑兩次，十九張截圖都是
   **0.00%、強差異 0 個**。受影響的只有 `--verify` 那個逐位比對。

寫在這裡是為了讓下一個看到 `--verify` 紅燈的人知道：**那不是你剛弄壞的**，也不要為了讓
它綠而去放寬任何閾值。

#### 交付

| 項目 | 結果 |
|---|---|
| `./tests/run-all.sh`（quick） | **39 支 0 失敗**（跳過 14 支：需 docker） |
| `./tests/run-all.sh --e2e` | **10 支 0 失敗**（含 golden_check，截圖真的比） |
| `golden_check`（跨行程再跑一次） | 全綠，十九張截圖 0.00% |
| 前端 vitest | **116 條全過**（原 108 ＋ 新增 8），行覆蓋 **88.7%** |
| 前端 lint／typecheck／prettier | 全過 |
| `ruff@0.16.3` check／format | 全過 |
| golden | 10 檔變動、44 行刪除、0 行新增 |

新增的八條測試守什麼：

- 公開那條只填得起兩個欄位，另外三個 store 欄位必須停在預設值；
- 版號與主機路徑跟著 `/api/account/bootstrap` 回來，**並補寫 `<html>` 的 `data-persist-dir`**；
- 登出要把登入後才拿到的 meta 一起清掉，公開的兩個留著；
- `AppFooter` 沒有 build 資訊時整段不畫（`html()` 就是一句 `<!--v-if-->`）、有就照舊畫、
  只有 `built_at` 時仍然畫；
- 整合層兩條：登入頁沒有頁尾（而且**仍然打了** `/api/account/bootstrap`，不給是因為 401，
  不是因為前端沒問）、登入成功之後頁尾才出現。

⚠ README 沒有動：全文搜過 `bootstrap` / `persist_dir` / `login_art` / `頁尾`，**零命中**，
那份文件從來沒有列過這兩條端點的欄位。ADR 0020 的後果節加了「已收緊」那一段。

---

### 08-26 Nathan 實測回報兩個 bug

用瀏覽器把畫面實際點過一遍，兩個都不是測試抓得到的形狀。

#### bug 1：登入態在別的分頁失效之後，401 只跳 toast、人不回登入頁

**現象。** 分頁 A 開著工作階段頁，分頁 B 改密碼（後端讓這個帳號的其他 session 全部作廢）。
A 這時已經動不了了，但畫面上什麼都不會說：人還留在原頁，再按什麼都是 401，等於卡死。

⚠ **那則「未登入」toast 不是輪詢發的**，這一點第一版的紀錄寫錯了。十五秒一次的自動刷新走
`refresh(false, true)`，而 `SessionsView.vue:105` 是 `if (!auto) toastError("列表讀取", e)`：
自動那條**刻意不出聲**（每十五秒彈一則錯誤只會變成背景噪音）。所以失效之後畫面是**安靜地**
停在一份舊資料上，一個字都沒有；使用者是自己按了重新整理、或按了某一列的動作鍵，才從那條
手動路徑拿到「列表讀取失敗／未登入」。這個差別很重要：安靜地卡住比跳一則沒用的 toast 更難
察覺，也讓「導回登入頁」這件事更不能少。

**根因不在「有沒有 push」。** `main.ts` 的 401 處理器確實 `router.push("/login")` 了，彈回來
的是守衛：SPA 換頁時 store 是活的，`store.user` 還在、`identityLoaded` 還是 true，於是
`authGuard` 走到「已登入者不該停在登入頁」那一條，回 `{ path: "/" }` 把這次導覽原地彈掉。
舊版 `app.js` 是 `location.href = "/login"`，整頁重載連記憶體一起清，沒有這個問題；改成
SPA 導向時只搬了「導覽」那一半，「清身分」那一半沒有跟上。

**修法。** `stores/site.ts` 加 `dropIdentity()`（清身分與憑證、把登入後才給的 meta 還原、
`identityLoaded` 翻回 false），401 處理器整段搬進新檔 `lib/unauthorized.ts`：清身分 → 只導
一次（同時飛的請求會一起 401）→ 發一則「登入已失效，請重新登入」。toast 用 `toast()` 不是
`toastAfterNav()`：後者寄在 sessionStorage 等**整份 app 重新載入**時 `drainPendingToast()`
去取，而這裡是 SPA 內換頁，`main.ts` 不會再跑一次。各呼叫端那些「◯◯失敗／未登入」由
`toastError()` 統一吞掉（401 不是這個動作失敗，是登入沒了）。

順帶修了同一個坑的另一處：`AppMasthead` 登出失敗那條路也只 push 不清身分，於是「登出失敗
也要照樣離開」這句註解對 500 那種失敗並不成立。

**攔截條件維持 `res.status === 401` 一個數字，沒有擴大。** 401 是「我不知道你是誰了」，403
是「我知道你是誰，但你不能做這件事」；把 403 也送去登入頁，等於叫一個登入狀態完全正常的人
重新登入一次，而他再登入一次也還是不能做那件事。判準也不可以退化成比對錯誤訊息的字串：後端
的中文隨時會改，而且撞字很容易發生。有一條測試守這件事，它用一發 body 寫著「未登入」的 403
擋掉字串判斷、再用一發訊息寫著「需要管理員權限」的 401 證明處理器真的掛著；把 `client.ts`
改成 `401 || 403`、或改成比對訊息，兩種寫法都會讓它變紅（兩種都實際跑過）。

**為什麼 golden 與 e2e 都沒抓到。** 誠實講：**這個形狀本來就不在它們的守備範圍裡**。golden
錄的是靜止的一幀，它連「按下去會發生什麼」都不看；e2e 看得到動作，但每一支都是在一個
cookie 從頭有效到尾的情境裡跑完的，而「**登入態在中途消失**」這件事沒有任何一支製造過。
現在 `e2e_vue_smoke` 補了一段：登入 → `context.clear_cookies()` → 按重新整理 → 斷言 URL
變成 `/login`、登入表單真的在、通知是「登入已失效」而且畫面上只有那一則。

#### bug 2：工作階段 ↔ 帳號管理切換時，thumb 沒有滑動動畫

**現象。** 兩格之間切換，底色膠囊是**瞬移**過去的，不是滑。

**根因分四層，只修最外面那層是不夠的**，這一段值得完整記下來，因為前三次都是「看起來對了」
才被瀏覽器打回票。

第一層：`.navseg__thumb` 的 `transition` 在 CSS 裡是掛在 `[data-animate]` 上的（舊版
`initNavSeg` 在換頁時把 `thumb.dataset.animate = "1"` 加上去），而 Vue 版從來沒有加過那個
屬性。`AppMasthead` 第 11 行的註解寫著「換頁不再整份 HTML 重來，招牌的 DOM 一直是同一份，
`data-active` 一改 CSS 的 transition 自己就跑」。

第二層：**上面那句註解是錯的。** `AppShell`（招牌的家）掛在**每一個 view 裡面**
（`SessionsView` 與 `AccountView` 各自 `<AppShell>`），所以換頁時整個招牌連同 thumb 都是
新節點。用瀏覽器量到的：換頁前在 thumb 上做記號，換頁後那個記號不見了，不是同一個 DOM
節點。新節點一出生就在目的地那一格，`transform` 從來沒有變過，補上 `data-animate` 也沒有
任何東西可以過渡。所以做法必須跟舊版 `initNavSeg` 一樣：**第一幀先畫在上一格**，下一影格
才交還給真正的那一格。記「上一格」的變數要放在**非 `setup` 的 `<script>` 區塊**裡：
`<script setup>` 的內容會被編譯進 `setup()` 本體，寫在那裡的 `let` 是每個實例各自一份，換頁
重掛就歸零（第一版就是這樣寫的，單元測試當場抓到）。刻意不用 sessionStorage：那份記錄會
活過整頁重載，於是「直接開 /account」也會動一下，正是 2026-07-25 那次事故要避免的首幀動畫。

第三層：**加了 `watch(activeSeg)` 順手更新「上一格」是個陷阱。** 換頁時舊招牌還沒被拆掉，
它的 `activeSeg` 會**先**變成新的那一格、那條 watch 先跑，於是「上一格」在新招牌 setup 之前
就被改成了目的地，`animateFrom` 一律算成 null。單元測試分頭掛兩次招牌看不到這一段（沒有
「兩個招牌同時活著跨過一次路由變化」的時刻），是瀏覽器量出來的：`transform` 直接 0 → 130px、
`getAnimations()` 空的、一個 `transitionrun` 都沒有。現在有一條 vitest 專門守它，做法是刻意
讓舊招牌活過 `router.push` 再拆（把那一行加回去，那一條會紅）。

第四層：**首幀畫在上一格，還是不會滑**。過渡是拿「變更前樣式」與「變更後樣式」比出來的，
而變更前樣式要有一次樣式計算把它定下來。換頁這條路上招牌是在**同一個 task** 裡插進 DOM 的
（router 的導覽解決之後 Vue 就掛），下一件事就是那個 `requestAnimationFrame`，中間**一次
繪製都沒有**，瀏覽器眼中 thumb 從來沒有在上一格待過，`transform` 只是換了個初值。修法是在
rAF 裡先讀一次 `offsetWidth` 強迫當場算一次版面，上一格那個位置才成為變更前樣式。

**CSS 一個字都沒改**（這是整個改版的前提）。`prefers-reduced-motion: reduce` 時整段不做，
重用 `lib/theme` 的 `prefersReducedMotion()`。

**為什麼 golden 與 e2e 都沒抓到。** 同樣誠實講：**動畫的時間軸不在 golden 的守備範圍裡**。
golden 比的是靜止的一幀，而 thumb 的起點與終點在兩種寫法下**完全相同**：會動與不會動，
錄下來的畫面一模一樣。`data-animate` 也不在 DOM 快照的屬性白名單裡（`DOM_ATTRS`），所以連
「有沒有這個屬性」都看不到。e2e 那邊則是每一條斷言都只認 `data-testid` 與網址，本來就不碰
樣式與動畫。**golden 沒有重錄，一個檔案都沒動**：十八場都是 `page.goto` 冷載入，模組層級
的「上一格」在重載時歸零，`data-active` 與改動前逐字相同。

#### 瀏覽器實測（不是只有測試綠）

| 量的東西 | 結果 |
|---|---|
| 清 cookie 後按重新整理 | URL `→ /login`、標題「登入 · claude-pty」、登入表單可見、session 列歸零 |
| 那一刻畫面上的通知 | **恰好一則**：「登入已失效，請重新登入」；`sessionStorage` 的寄放區是空的 |
| 頁尾 | 不在（登入後才給的 meta 一起還原了，裁示 L4） |
| `/` → `/account` 的 thumb | `transform` 0 → 52px(50ms) → 120px(170ms) → 130px；`transitionrun` 與 `transitionend` 各一次 |
| `/account` → `/` 的 thumb | 130px → 77px(50ms) → 8px(170ms) → 0；同樣各一次過渡事件 |
| 冷載入 `/account` | 第一幀就在 130px，整段零個 `transitionrun` |
| `prefers-reduced-motion: reduce` | `data-animate` 不在、`transition-duration` 是 `0s`、零個過渡事件、位置照樣正確 |

#### 交付

| 項目 | 結果 |
|---|---|
| 前端 vitest | **190 條全過**（原 175 ＋ 新增 15），行覆蓋 **94.69%**（門檻 90） |
| 前端 lint／格式／型別／build | 全過 |
| `./tests/run-all.sh`（quick） | **39 支 0 失敗**（跳過 14 支：需 docker） |
| `./tests/run-all.sh --e2e` | **10 支 0 失敗**，`e2e_vue_smoke` 52 → 57 條 |
| golden | **沒有重錄，零檔案變動** |

⚠ **一個順手撿到的既有問題**（當下沒修，理由是它不在這兩個 bug 的範圍內；已在下面那顆收尾
commit 一併修掉）：`AppMasthead.doLogout()` 用 `toastAfterNav("已登出", …)` 寄一則跨頁通知，
然後 `router.push("/login")`。但登出是 SPA 內換頁，`drainPendingToast()` 只在 `main.ts` 進站
時跑一次，那一則會一直躺在 `sessionStorage` 裡，直到**下一次整頁重載**才冒出來（在一個完全
無關的時機）。發現的方式很偶然：寫 401 的測試時，從 `sessionStorage` 裡撿到上一條測試留下
來的 `{"title":"已登出"}`。

### fable 快審 f5f7f26..ba518e9：無阻擋，五項收尾

上面那兩顆 commit 送審，結論是**無阻擋**（沒有要退回重做的），但列出五項要收。全部修在同
一顆 commit 裡，理由是它們共用一個主題：**「401 導回登入頁」這條規格的邊界，比第一版想的
還要多幾個入口**。

#### 1（中度）遲到的 401 會再彈一次、再 push 一次

`lib/unauthorized.ts` 的旗子在 `push().finally` 就放下，而它擋的只有「同一輪的並行」。真正
會漏的是**導覽完成之後才回來**的那幾發：輪詢在被導走的前一刻剛送出、AccountView 最慢的那條
還在路上。它們回來時旗子早就沒了，於是使用者已經站在登入頁上，又被彈一則一模一樣的「登入
已失效」，還被 push 到自己已經站著的路由（vue-router 會為此出 warning）。

修法是在處理器最前面加一關：`router.currentRoute.value.path === "/login"` 就整段 return。
**兩道關卡各擋一種**，少哪一道都會漏：旗子擋同一輪（那時 push 還沒 resolve、`currentRoute`
還停在來源頁，過得了這一關），路徑檢查擋遲到的。

⚠ 這一關要放在 `dropIdentity()` **之前**，而且是這樣才對，不只是順手：登入的回應收下身分
（`adoptIdentity`）到 `push("/")` 完成之間，人還站在 `/login` 上。這中間若有別的請求 401
（上一輪還沒回來的那一發），清身分會把剛收下的那份洗掉，使用者按了「進入控制台」卻留在原
地。有一條測試專門守這個窗口。

#### 2（低）登出失敗那條路對 401 做了第二次

`doLogout()` 的 catch 無條件 `dropIdentity()` ＋ `push("/login")`。但 401 是登出最常見的失敗
原因，而它早就被 `api()` 交給全域處理器了：身分清了、通知發了、`/login` 也導了。這裡再走一
次就是 push 同一個路由第二次。改成 401 直接 return，其餘（500、網路斷）才自己走完。

#### 3（低，但違反規格）抽屜的檔案上傳不走那條路

`TerminalDrawer` 的 `uploadFile` 是手寫 `fetch`（multipart 的 boundary 要交給瀏覽器組，不能
走 `api()`）。於是它的 401 只會變成一句「上傳失敗／未登入」，人繼續留在一個什麼都做不了的
畫面上。**「401 一律導回登入頁」是全站的規格，不是 `api()` 這個函式的性質**，這是這一項真正
的重點。

`api/client.ts` 新匯出一個 `notifyUnauthorized()`（匯出的是「通知」不是處理器本身，呼叫端不
該拿得到 handler 去存起來或換掉它），`uploadFile` 收到 401 就叫它、並丟 `ApiError(…, 401)`，
於是 `toastError` 認得出來、把「上傳失敗」那一則吞掉。順帶把那個 catch 從手寫 `toast()` 換成
`toastError("上傳", ex, { duration: 8000 })`：`toastError` 多收一個 `opts`，就是為了讓本來手
寫的呼叫端搬得過來而不必改掉它的 duration（那 8 秒是刻意的，要讀的是後端說的原因）。`body`
不開放覆寫，它就是後端原文。

#### 4 登出成功那則改用 `toast()`

就是上一節末尾那個「順手撿到但沒修」的。SPA 內換頁沒有人會去 `drainPendingToast()`。

⚠ 改完之後 `toastAfterNav` / `drainPendingToast` **零生產呼叫端**：legacy 互跳隨 legacy 拆
掉，登出改直接發，改密碼那條整頁跳轉選擇先顯示夠久再跳。機制留著沒刪（「離開 SPA 的跳轉」
這個形狀還在，而且 `main.ts` 那一行的成本是一次 sessionStorage 讀取），但**要不要拆是下一個
決定，不是這顆 commit 自己能決定的**。相關的三處註解一併對齊事實：`main.ts` 的
`drainPendingToast()`、`ToastStack` 那條 `immediate: true` 的理由（它舉的例子沒了，但守的
性質沒變），以及 `golden_scenes.py` 裡一句寫著「登入成功會用 toastAfterNav」的舊註解
（LoginView 用的是 `toast()`，那句從來就不對）。

#### 5 上一節的兩處紀錄不準

「根因分三層」改成四層（原文自己就寫了四段）；bug 1 的現象把那則「列表讀取失敗／未登入」
錯記成輪詢發的，已改正（見上面那個 ⚠：輪詢那條刻意不出聲，畫面是安靜地卡住的）。

#### 每一項都做了 mutation 驗證

不是「加了測試就算」。把修法逐條拿掉，對應的測試要紅：

| 拿掉什麼 | 紅的是 |
|---|---|
| `lib/unauthorized.ts` 的 `path === "/login"` 早退 | 「遲到的 401 不再 toast、不再 push」與「站在 /login 上的 401 不可以把剛收下的身分洗掉」兩條 |
| `uploadFile` 的 `notifyUnauthorized()` | 「上傳收到 401 走全站那條路」 |
| `doLogout` 的 401 早退 | 「登出收到 401 就交給全域處理器，不再自己 push 一次」 |
| 把登出那則改回 `toastAfterNav` | 「登出成功那則用 toast 不是 toastAfterNav」 |

#### 交付

| 項目 | 結果 |
|---|---|
| 前端 vitest | **195 條全過**（190 ＋ 新增 5），行覆蓋 **94.7%**（門檻 90） |
| 前端 lint／格式／型別／build | 全過 |
| `./tests/run-all.sh`（quick） | **39 支 0 失敗** |
| `./tests/run-all.sh --e2e` | **10 支 0 失敗** |
| golden | 仍然**沒有重錄，零檔案變動** |
