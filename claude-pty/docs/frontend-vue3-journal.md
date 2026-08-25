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
