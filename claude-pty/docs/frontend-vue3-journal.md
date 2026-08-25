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
