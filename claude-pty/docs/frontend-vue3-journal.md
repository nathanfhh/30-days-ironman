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
