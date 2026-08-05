## 審查結論：Approved with Comments

> Critical 0 · Suggestion 6 · Nit 5 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | — |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | — |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| — | ❌ | ❌ |

- **A 風格**（未通過）：新增的 flexbox mixin 區塊與 mixins.scss 其餘部分的排版慣例不一致（縮排 4 空格、行尾空白、`align-items:$alignment` 缺空格、flexbox() 與 inline-flex() 的前綴排列順序不同），另有 padding 從固定值改成百分比。見 F-009、F-010。
- **B 簡潔**（未通過）：`-ms-align-items` 是不存在的屬性（F-007）；`.contents` 上的 clearfix 在成為 flex container 後已無作用（F-011）。兩者都是新增/殘留的無效宣告。
- **C 安全**（不適用）：純樣式變更，沒有輸入處理、SQL、憑證或可執行路徑；CSS 本身也未引入外部資源（無 url()、無 @import 外部網址）。沒有可評估的安全面。
- **D API 慣例**（不適用）：diff 未觸及任何 route、controller、serializer 或 API endpoint，沒有 URL、HTTP verb 或驗證 schema 相關內容。
- **E 架構**（未通過）：`@include order(2)` 掛在 `.extra-info-wrapper` 上但它不是 `.contents` 的 flex item（F-006）；`.panel` 改用 `margin-left: auto` 定位，而該技巧在此 mixin 明確要支援的 2009 box model 下無效，且移除 float 後沒有留下任何 fallback（F-002）。佈局意圖與實際生效層次對不上。
- **F 資料取用與資料庫**（不適用）：沒有資料存取、schema、migration 或並行寫入相關改動。
- **G 測試**（不適用）：這個 repository 沒有 CSS/視覺回歸測試設施（只有 test/javascripts 的 qunit 與 spec/ 的 rspec，兩者都不斷言樣式）。純樣式變更在現有測試架構下沒有可新增的有效斷言，因此本維度不適用；但也代表 F-001／F-002 這類回歸只能靠人工複查。
- **H 非 Python 檔**（未通過）：diff 全部是 SCSS。前綴集合本身有兩個缺口：align-items() 缺 `-moz-box-align`（F-003），以及把現代關鍵字未經轉換丟給舊語法屬性（F-004）；order() 對 1-based 的 `*-box-ordinal-group` 也未做保護（F-008）。
- **I 回溯分析**（未通過）：本 commit 是 2ad2ab5 被 83593fe revert 之後的重新落地，但沒有把原 commit 的 desktop/mobile header 調整一併帶回（F-001）；`.small-action-desc` 的 padding 改動也讓 mobile 端一條依賴舊 padding 的 override 失去校準（F-005）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 commit 是在重新落地 2ad2ab5（"aligning text-logos and header with flexbox"），該 commit 已於 83593fe 被 revert。但本次只重放了 common/ 底下四個檔案，2ad2ab5 同時做的 desktop/header.scss 與 mobile/header.scss 調整沒有一起回來（見 F-001）。一次 revert 的重新落地，範圍應該與原 commit 對齊，否則差異來源會變得難以追溯。
- **該在這個時機做？**：83593fe 的 revert 理由沒有寫在 commit message 裡（見 Q-001）。在不知道當初為什麼被 revert 的情況下重新落地，無法判斷「補上 prefix」是否真的處理掉了原因；而 prefix 本身並不能讓 IE9 這種完全沒有 flexbox 的瀏覽器動起來（見 F-002）。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 略過 | ruff 0.15.8 已安裝，實際執行 `ruff check --output-format json .` 回傳 `warning: No Python files found under the given path(s)` 與空陣列（exit 0）。整個 repository 沒有任何 .py 檔案，等於沒有掃到任何東西，因此記為 skipped 而非 ok——把「沒有檔案可掃」寫成 clean 會誤導。 · exit code 0 |
| stylelint / scss-lint | 略過 | 本次 diff 100% 是 SCSS，但執行環境沒有安裝任何 CSS/SCSS linter，repository 內也只有 .jshintrc、沒有 stylelint 或 scss-lint 設定檔。也就是說：這份 diff 的自動化掃描覆蓋率是零，以下所有結論都來自人工閱讀原始碼與 git 歷史。 |
| trivy | 略過 | preflight 回報未安裝，略過相依套件漏洞、misconfiguration 與 secret 掃描。 |
| opengrep | 略過 | preflight 回報未安裝，且預設的 Semgrep 規則目錄在本機不存在（兩項都缺）。略過 SAST 掃描。 |
| ty | 略過 | preflight 回報未安裝；本 repository 也無 Python 程式碼，即使安裝亦無可檢查對象。 |
| oxlint | 略過 | preflight 回報未安裝。diff 本身無 JS/TS 檔案，但本次分析有讀取 .hbs 與 .es6 來源以確認 DOM 結構，那些檔案未被任何 linter 檢查。 |
| codegraph | 略過 | preflight 回報未安裝，Phase 0 的 `codegraph init` 未執行。導覽全程改用 grep：selector 使用點、Ember component 的 classNames、mixin import 順序、RTL 編譯路徑均以文字搜尋確認。 |
| ncr-fresh-eyes (subagent) | 略過 | 本次執行環境沒有可用的 Agent/Task 工具，無法派出任何 subagent。依 SKILL.md Phase 3 的規定，不得由主 agent 自行模擬 fresh eyes，因此該步驟略過並在此揭露：這份報告缺少一次未被 skill 框架塑形的獨立閱讀。 |
| ncr-quality-check (subagent) | 略過 | 同上，無法派出 subagent。Phase 4 步驟 3 的報告品質複查未執行，findings 的措辭與 severity 只經過作者本人一次自我檢查。 |

<details>
<summary>Suggestion（6）</summary>

#### F-001 重新落地被 revert 的 flexbox 改動時，漏掉 desktop/mobile header 的配套調整 — `app/assets/stylesheets/desktop/header.scss:11`

面向 I 回溯分析 · Suggestion

**問題**：`git show 83593fe` 顯示被 revert 的 2ad2ab5 一共動了 6 個檔案：common/base/header.scss、common/base/topic-post.scss、common/base/topic.scss、common/components/badges.css.scss，加上 desktop/header.scss 與 mobile/header.scss。本次 commit 逐字重放了前四個（並補上 prefix），但後兩個沒有回來——現在 desktop/header.scss:11 仍是 revert 後的 `padding:8px`（原本要改成 `padding: 0 8px 0 0`），mobile/header.scss:14 仍保留 `height: 39px`（原本要刪除）。這兩處正是為了配合 `.contents` 上的 `align-items: center` 才調整的：`.fa-home` 的上下 8px padding 在垂直置中之後會多撐高 flex item，而 `.title` 的固定高度會與 flex item 的 stretch/center 行為互相牽制。四個檔案照抄、兩個檔案沒抄，不像是刻意取捨。

**證據**：
- `app/assets/stylesheets/desktop/header.scss:11`
- `app/assets/stylesheets/mobile/header.scss:14`
- `app/assets/stylesheets/common/base/header.scss:14`

**修復方向**：把 2ad2ab5 對這兩個檔案的 hunk 一併帶回：`desktop/header.scss` 的 `.fa-home` 改成 `padding: 0 8px 0 0;`，`mobile/header.scss` 的 `.title` 移除 `height: 39px;`。若是刻意不帶回（例如已經在別的 commit 修過），請在 commit message 或 PR 描述說明，否則之後追這段歷史的人會以為是漏抄。指令上可以直接 `git show 2ad2ab5 -- app/assets/stylesheets/desktop/header.scss app/assets/stylesheets/mobile/header.scss` 對照。

#### F-002 `.panel` 改用 `margin-left: auto` 定位，但這個技巧在本 mixin 特地支援的舊 flexbox 語法下無效；移除 float 後也沒有留下 fallback — `app/assets/stylesheets/common/base/header.scss:35`

面向 E 架構 · Suggestion

**問題**：`flexbox()` 刻意輸出 `display: -webkit-box` 與 `display: -moz-box`，也就是 2009 版的 box model——這正是為了 README.md:40-47 列出的 Safari 5.1+、Android 4.1+、Firefox 16+ 而加的。但 2009 版沒有「auto margin 吸收剩餘空間」這個機制，`margin-left: auto` 在 `-webkit-box` / `-moz-box` 之下會被當成 0，`.panel` 不會被推到右邊；原本的 `float: right` 才是在那些瀏覽器上生效的東西，而它被刪掉了。同樣地 README.md:47 把 IE9 列為 minimum spec browser，IE9 完全不支援任何形式的 flexbox，`.contents` 會退化成普通 block，此時 `.title`（已刪 `float: left`）與 `.panel`（已刪 `float: right`）都失去定位，header 會變成上下堆疊。值得注意的是本次 diff 在 `.small-action .topic-avatar`（topic-post.scss:270）反而保留了 `float: left`，同一份 diff 內兩種處理方式不一致。（已查證反證：`.title` 在 Ember header 是 `home-logo` component 的 `classNames: ["title"]`，desktop/header.scss 與 mobile/header.scss 都沒有再給它 float；bootstrap 的 `[class*="span"] { float: left }` 只涵蓋 _header.html.erb 那個 noscript 版本的 `.title.span13`，涵蓋不到 Ember 版。）

**證據**：
- `app/assets/stylesheets/common/base/header.scss:35`
- `app/assets/stylesheets/common/base/header.scss:14`
- `app/assets/stylesheets/common/foundation/mixins.scss:100`
- `app/assets/stylesheets/common/base/topic-post.scss:270`
- `README.md:44`

**修復方向**：把 float 當成 fallback 留著就好——依 CSS Flexbox 規格，float 對 flex item 完全不生效，所以在支援 flexbox 的瀏覽器上一行成本都沒有：`.title { float: left; }` 與 `.panel { float: right; margin-left: auto; ... }`。若不想留 float，就要為舊語法補上對應的 pack 屬性（`-webkit-box-pack: end` / `-moz-box-pack: end`），但那會影響容器內所有 item，需要重新安排；留 float 是這裡成本最低且行為最可預測的作法。

#### F-003 `align-items()` mixin 缺少 `-moz-box-align`，`-moz-box` fallback 拿不到對齊 — `app/assets/stylesheets/common/foundation/mixins.scss:117`

面向 H 非 Python 檔 · Suggestion

**問題**：同一組 mixin 裡，`flexbox()` 輸出了 `display: -moz-box`（mixins.scss:102），`order()` 也輸出了 `-moz-box-ordinal-group`（mixins.scss:127），可見 `-moz-box` 這條路徑是被刻意支援的。但 `align-items()`（mixins.scss:117-123）只列了 `-webkit-box-align`、`-webkit-align-items`、`-ms-flex-align`、`-ms-align-items`、`align-items`，獨缺 `-moz-box-align`。結果是：在只認得 `-moz-box` 的 Firefox（README.md:44 宣告支援 Firefox 16+，而不帶前綴的 `display: flex` 要到 Firefox 22 才預設開啟）上，`.d-header .contents` 與 `.small-action` 會變成水平 box 但完全沒有垂直置中，`.badge-wrapper.bullet` 也拿不到 baseline 對齊。commit 標題寫的是 "all prefixes"，這一格是漏的。

**證據**：
- `app/assets/stylesheets/common/foundation/mixins.scss:117`
- `app/assets/stylesheets/common/foundation/mixins.scss:102`
- `app/assets/stylesheets/common/foundation/mixins.scss:127`

**修復方向**：在 `align-items()` 內補上一行 `-moz-box-align: $alignment;`，位置放在 `-webkit-box-align` 之後、`-webkit-align-items` 之前，與 `order()` 內 `-webkit-box-ordinal-group` / `-moz-box-ordinal-group` 成對出現的排法一致。若決定不支援 `-moz-box`（也是合理選擇），則應該反過來把 `flexbox()` 的 `display: -moz-box` 與 `order()` 的 `-moz-box-ordinal-group` 一起拿掉，讓三個 mixin 對「支援到哪裡」的答案一致。

#### F-004 `align-items()` 把現代關鍵字原封不動轉給舊語法屬性，`flex-start` / `flex-end` 會靜默失效 — `app/assets/stylesheets/common/foundation/mixins.scss:117`

面向 H 非 Python 檔 · Suggestion

**問題**：`-webkit-box-align`（2009 版）與 `-ms-flex-align`（IE10）接受的值是 `start | end | center | baseline | stretch`，而現代 `align-items` 用的是 `flex-start | flex-end | center | baseline | stretch`。mixin 直接 `$alignment` 透傳，代表 `@include align-items(flex-start)` 會產生 `-webkit-box-align: flex-start` 與 `-ms-flex-align: flex-start` 這兩條無效宣告——瀏覽器會直接丟棄，不會報錯。本次 diff 的兩個呼叫點剛好是 `center`（header.scss:18、topic-post.scss:264）與 `baseline`（badges.css.scss:60），這三個值在新舊語法都合法，所以現在沒有實際 bug；但這是一個放進共用 mixin 檔的公開介面，下一個用 `flex-start` 的人不會得到任何提示。

**證據**：
- `app/assets/stylesheets/common/foundation/mixins.scss:117`
- `app/assets/stylesheets/common/foundation/mixins.scss:118`
- `app/assets/stylesheets/common/foundation/mixins.scss:120`

**修復方向**：在 mixin 內做一次值映射，例如：`$legacy: if($alignment == flex-start, start, if($alignment == flex-end, end, $alignment));`，然後把 `$legacy` 給 `-webkit-box-align` / `-moz-box-align` / `-ms-flex-align`，`$alignment` 給其餘。若不想加邏輯，至少在 mixin 上方加一行註解寫明「只接受 center / baseline / stretch，flex-start / flex-end 尚未支援」，讓限制是明示的而不是靠踩到才知道。

#### F-005 `.small-action-desc` 的 4em 左 padding 被移除，但 mobile 端依賴該值的 `margin-left: -40px` 沒有跟著調整 — `app/assets/stylesheets/common/base/topic-post.scss:279`

面向 I 回溯分析 · Suggestion

**問題**：`.small-action-desc` 的 padding 從 `0.5em 0 0.5em 4em` 改成 `0 1.5%`。舊的 `4em`（在 `font-size: 0.9em` 之下約 50px）是用來讓文字避開浮動的 `.topic-avatar`，而 mobile/topic-post.scss:521 的 `.small-action .custom-message { margin-left: -40px; }` 顯然是為了抵消那 50px、把 custom message 拉回左側對齊而寫的。改動後左 padding 變成 `1.5%`：以手機約 360px 的容器寬度計算只剩約 5px，`-40px` 的負 margin 會把 `.custom-message` 推到 `.small-action-desc` 內容區左緣之外約 35px，也就是壓到 `.topic-avatar` 的區域上。這條 override 位於 mobile/ 目錄、不在本次 diff 內，所以不會在 review diff 時被看見。（已查證反證：grep 過 `small-action` 的全部樣式位置——desktop/topic-post.scss:591、730、mobile/topic-post.scss:516、525、530——沒有任何一處在別的地方補回左側 padding 或取消這條負 margin。）

**證據**：
- `app/assets/stylesheets/common/base/topic-post.scss:279`
- `app/assets/stylesheets/mobile/topic-post.scss:521`

**修復方向**：把 mobile/topic-post.scss:520-522 的 `.custom-message { margin-left: -40px; }` 一併處理：在新的 flex 佈局下 `.small-action-desc` 已經是 flex item、不再需要為頭像預留左 padding，這條負 margin 應該直接刪除；若 mobile 上仍希望 custom message 往左靠齊頭像欄，改成明確的 `margin-left: -1.5%`（與新的 padding 對稱）比留著寫死的 `-40px` 好追。同時建議在真機或 devtools 的窄視窗下確認一次 time-gap 與 custom-message 的排版。

#### F-006 `.extra-info-wrapper` 上的 `@include order(2)` 不會生效——它不是 `.contents` 的 flex item — `app/assets/stylesheets/common/base/topic.scss:30`

面向 E 架構 · Suggestion

**問題**：`order` 只對 flex item（flex container 的直接子元素）有效。header.hbs 裡 `.contents` 的直接子元素是 `{{home-logo}}`（component 有 `classNames: ["title"]`，所以確實渲染成 `div.title`）、`div.panel`，以及 `{{header-extra-info}}`。但 `header-extra-info` component 沒有設定 `tagName` 或 `classNames`，Ember 會替它產生一層 `div.ember-view` 外框，`.extra-info-wrapper` 是寫在該 component 的 template 第一行（header-extra-info.hbs:1）——也就是 flex container 的孫節點。因此 topic.scss:30 的 `order: 2` 落在一個非 flex item 上，整組宣告（含 `-ms-flex-order`、`-webkit-box-ordinal-group`）都被忽略。目前畫面看起來是對的，但那是因為 source order 剛好是 title → extra-info → panel，而 `.panel` 有 `order: 3` 會排到最後；換句話說順序是「碰巧」正確的，不是這行 CSS 保證的。

**證據**：
- `app/assets/stylesheets/common/base/topic.scss:30`
- `app/assets/javascripts/discourse/templates/header.hbs:2`
- `app/assets/javascripts/discourse/templates/components/header-extra-info.hbs:1`
- `app/assets/javascripts/discourse/components/header-extra-info.js.es6:1`

**修復方向**：兩種都可以：(a) 讓 component 自己就是那個元素——在 header-extra-info.js.es6 加 `classNames: ['extra-info-wrapper']`，並移除 header-extra-info.hbs 最外層的 `<div class="extra-info-wrapper">`（內層縮排跟著往前）；(b) 若不想動 component，就把 order 移到 `.d-header .contents > .ember-view` 這類實際的 flex item 上。若判斷順序本來就由 source order 保證、不需要 order，則直接刪掉 topic.scss:30 這行，避免留下一條看起來有作用但沒有的宣告。

</details>

<details>
<summary>Nit（5）</summary>

#### F-007 `-ms-align-items` 不是實際存在的屬性 — `app/assets/stylesheets/common/foundation/mixins.scss:121`

面向 B 簡潔 · Nit

**問題**：IE 對 align-items 的前綴版本只有 IE10 的 `-ms-flex-align`（已列在 mixins.scss:120），IE11 起支援不帶前綴的 `align-items`。`-ms-align-items` 從未被任何 IE 版本實作，也不在任何 W3C 草案裡，這一行對每個使用 `align-items()` 的 selector 都會多產生一條永遠不會生效的宣告。

**證據**：
- `app/assets/stylesheets/common/foundation/mixins.scss:121`

**修復方向**：刪除 mixins.scss:121 這一行。

#### F-008 `order()` 把值直接映到 1-based 的 `*-box-ordinal-group`，`@include order(0)` 會產生無效宣告 — `app/assets/stylesheets/common/foundation/mixins.scss:125`

面向 H 非 Python 檔 · Nit

**問題**：`-webkit-box-ordinal-group` / `-moz-box-ordinal-group` 的合法值是「大於 0 的整數」，預設 1；而現代 `order` 的預設值是 0、允許 0 與負數。mixin 直接透傳 `$val`，所以 `@include order(0)` 會輸出 `-webkit-box-ordinal-group: 0`，屬於無效值、宣告被丟棄。本次的兩個呼叫點是 `order(2)`（topic.scss:30）與 `order(3)`（header.scss:39），都 ≥ 1，且未指定 order 的 item 在兩種語法下都排在它們前面（現代 order 預設 0 < 2、舊語法 ordinal-group 預設 1 < 2），所以目前相對順序一致、沒有實際問題。

**證據**：
- `app/assets/stylesheets/common/foundation/mixins.scss:125`
- `app/assets/stylesheets/common/foundation/mixins.scss:126`
- `app/assets/stylesheets/common/foundation/mixins.scss:130`

**修復方向**：在 mixin 上方加註解說明 `$val` 必須 ≥ 1，或直接在內部做偏移：`-webkit-box-ordinal-group: $val + 1;`（同時 `-moz-`），讓呼叫端可以用 0-based 的心智模型而不會踩到。兩種做法選一種即可，重點是讓這個 1-based / 0-based 的落差在檔案裡是寫明的。

#### F-009 新增的 flexbox mixin 區塊排版與檔案其餘部分不一致 — `app/assets/stylesheets/common/foundation/mixins.scss:108`

面向 A 風格 · Nit

**問題**：四處小落差：(1) `align-items()` 內部用 4 空格縮排，mixins.scss 其餘 mixin 一律 2 空格；(2) mixins.scss:120 `-ms-flex-align: $alignment;` 行尾有兩個多餘空白；(3) mixins.scss:122 `align-items:$alignment;` 冒號後缺空白，同 mixin 內其他行都有；(4) `flexbox()` 的前綴排列是 webkit-box → moz-box → ms-flexbox → webkit-flex → 標準，`inline-flex()` 卻是 webkit-inline-box → webkit-inline-flex → moz-inline-box → ms-inline-flexbox → 標準，兩個對應的 mixin 排法不同。第 (4) 點功能上沒有差異（不同 vendor 前綴之間不會互相覆蓋，webkit 內部也仍是新的在後），純粹是讀的時候要多想一次。

**證據**：
- `app/assets/stylesheets/common/foundation/mixins.scss:108`
- `app/assets/stylesheets/common/foundation/mixins.scss:117`
- `app/assets/stylesheets/common/foundation/mixins.scss:120`
- `app/assets/stylesheets/common/foundation/mixins.scss:122`

**修復方向**：把 `align-items()` 縮排改成 2 空格、刪掉行尾空白、`align-items: $alignment;` 補上空白，並把 `inline-flex()` 的順序調成與 `flexbox()` 對齊（`-webkit-inline-box` → `-moz-inline-box` → `-ms-inline-flexbox` → `-webkit-inline-flex` → `inline-flex`）。這個 repo 目前沒有 stylelint，所以這類一致性只能靠 review 抓；順帶一提，加一份 .stylelintrc 會比每次人工挑要划算。

#### F-010 `.small-action-desc` 的左右間距改用百分比，會隨容器寬度浮動 — `app/assets/stylesheets/common/base/topic-post.scss:280`

面向 A 風格 · Nit

**問題**：`padding: 0 1.5%` 的百分比是相對容器（`.small-action`）的寬度解析的。desktop/topic-post.scss:730 把 `.small-action` 固定成 `width: 755px`，所以桌機是約 11px；但 mobile 沒有這個寬度限制，實際值會跟著視窗寬度變動，窄螢幕上大約只剩 5px。同一個檔案裡其他間距（`margin: 15px 0px 5px`、`margin-right: 0.8em`）都用固定值，這裡混入百分比會讓「這個 gutter 到底多寬」變成要先知道容器寬度才能回答。

**證據**：
- `app/assets/stylesheets/common/base/topic-post.scss:280`
- `app/assets/stylesheets/desktop/topic-post.scss:730`

**修復方向**：改成固定值，例如 `padding: 0 10px;` 或與鄰近規則一致的 em 值（`padding: 0 0.8em;`）。若這個百分比是刻意要在窄螢幕收窄，請加一行註解說明，否則下一個人很可能以為是隨手寫的。

#### F-011 `.contents` 成為 flex container 後，template 上的 `clearfix` class 已無作用 — `app/assets/javascripts/discourse/templates/header.hbs:2`

面向 B 簡潔 · Nit

**問題**：helpers.scss:53 的 `.clearfix` 是靠 `&:before` / `&:after` 加 `clear: both` 來包住浮動子元素。`.contents` 現在是 flex container，其子元素的 float 依規格不生效、也就沒有東西需要 clear；而那兩個 pseudo-element 反而會各自成為一個 flex item（`display: table` 在 flex item 上被 blockify，內容只有一個空白因而寬高為 0）。實際畫面不受影響，但這個 class 已經名不副實，且多出兩個看不見的 flex item 會讓之後除錯 `order` 或 `justify-content` 時多繞一圈。

**證據**：
- `app/assets/javascripts/discourse/templates/header.hbs:2`
- `app/assets/stylesheets/common/foundation/helpers.scss:53`
- `app/assets/stylesheets/common/base/header.scss:14`

**修復方向**：把 header.hbs:2 的 `class='contents clearfix'` 改成 `class='contents'`。同一個檔案 header.hbs:6 的 `.panel clearfix` 要留著——`.panel` 不是 flex container，裡面的 `.icons > li { float: left }` 仍然需要被清除。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 83593fe 當初 revert 2ad2ab5 的實際原因是什麼？「補上所有 prefix」是否真的處理掉了那個原因？

面向 I 回溯分析

**背景**：`git log -1 --format=%B 83593fe` 只有預設的 "This reverts commit 2ad2ab5..."，沒有任何理由；repository 內也沒有對應的 issue 連結或後續說明。本次 commit 標題 "(all prefixes)" 暗示作者認為原因是舊瀏覽器沒有前綴，但這無法從程式碼本身證實。這件事會影響判斷：如果 revert 是因為某個完全不支援 flexbox 的瀏覽器（例如 README.md:47 列為 minimum spec 的 IE9），那補前綴並不會解決，F-002 提到的 float fallback 才會。

**如何確認**：當初做 revert 的人（Sam）或原作者說明 revert 的觸發情境——是哪個瀏覽器、哪個畫面壞掉。或是 PR/issue 討論串上的紀錄。

#### Q-002 IE10 的 `-ms-flexbox` 實作是否會讓 flex item 上的 `margin-left: auto` 吸收剩餘空間，把 `.panel` 推到右側？

面向 H 非 Python 檔

**背景**：README.md:44 明確支援 IE10+，而 `.panel`（header.scss:35-40）現在完全依賴 `margin-left: auto` 定位。IE10 實作的是 2012 年的中間版草案，auto margin 在該版本的行為與最終規格不完全一致，這是已知有分歧的區域。F-002 只針對 2009 版（`-webkit-box` / `-moz-box`）下結論，因為那一版沒有 auto margin 機制是明確的；IE10 這一格無法只靠讀原始碼判定。

**如何確認**：在真實 IE10（或 IE11 的 IE10 文件模式，雖然不完全等價）上開啟 header 看 `.panel` 是否貼右；或參考 caniuse 的 flexbox known issues 對 IE10 auto margin 的記載。若答案是否，則 F-002 建議的 `float: right` fallback 同時也修掉 IE10。

</details>
