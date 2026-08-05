## 審查結論：Approved with Comments

> Critical 0 · Suggestion 7 · Nit 2 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | — |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ✅ | — |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| — | ❌ | ❌ |

- **A 風格**（未通過）：兩個 Nit：common/base/discourse.scss:73 的註解在這次改動後已經失準；另有五行順手把行尾空白從 ;} 改成 ; }，但同批其他同形式的行沒有跟著改。
- **B 簡潔**（未通過）：同一條 light/dark 對映規則在 32 個檔案裡逐字展開 116 次，兩個引數的關係只靠人工維持。F-002 到 F-005 這四個 finding 全部出自這個形式。
- **C 安全**（不適用）：這次改動全部是 SCSS 顏色宣告，沒有輸入處理、沒有查詢組裝、沒有憑證，也沒有任何會被執行的程式路徑，安全面向沒有可判定的對象。
- **D API 慣例**（不適用）：沒有動到任何 URL、endpoint、HTTP 動詞或驗證 schema。
- **F 資料取用與資料庫**（不適用）：沒有任何資料存取、schema 變更或共享狀態。
- **G 測試**（不適用）：這次改動沒有新增或修改測試，也沒有可斷言的行為介面。repo 既有的 spec/components/discourse_stylesheets_spec.rb 會實際編譯 desktop 與 mobile bundle，能擋下語法錯誤，但它只用預設（light）color scheme，抓不到本次真正的風險 —— dark theme 下算出來的顏色對不對。本次環境沒有 gem，這支 spec 也沒能實際跑過。
- **H 非 Python 檔**（未通過）：32 個檔案全是 SCSS。轉換規則本身正確，但唯一一處 background-color 沒有連帶處理配對的前景色（F-001），另有一處同類宣告漏轉（F-006）。
- **I 回溯分析**（未通過）：這次改的是既有宣告，回溯要問的是「light theme 是否維持原樣」。116 處裡有 5 處的第一個引數被一起改掉（歸為 F-002 到 F-005 四個 finding），light theme 的呈現因此改變，超出 commit message 宣稱的範圍。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | preflight 顯示未安裝，略過相依套件漏洞與 secret 掃描。本次 diff 只有 SCSS，沒有相依套件或設定檔變更，影響有限。 |
| opengrep | 略過 | preflight 顯示未安裝，且預設規則目錄不存在，SAST 掃描整段略過。 |
| ruff | 略過 | 工具本身可用（0.15.8），但本次 diff 沒有任何 Python 檔案，沒有可掃描的目標。 |
| ty | 略過 | preflight 顯示未安裝；本次 diff 也沒有 Python 檔案。 |
| oxlint | 略過 | preflight 顯示未安裝；本次 diff 也沒有 JavaScript/TypeScript 檔案。 |
| codegraph | 略過 | preflight 顯示未安裝，Phase 3 的呼叫關係導覽改用 grep 在完整 checkout 上進行。 |
| stylelint / scss-lint | 略過 | 審查環境沒有安裝任何 SCSS linter，這個 repo 本身也沒有 SCSS lint 設定（.codeclimate.yml 只啟用 Ruby 與 JavaScript）。結果是本次改動的 32 個檔案沒有任何自動化檢查覆蓋，全部由人工逐行比對。 |
| sass compiler | 略過 | 環境沒有 sass/sassc，也沒有安裝 gem，無法實際編譯這批 SCSS。報告中的顏色與對比數值是依 scale-color 的 HSL 定義與 WCAG 相對亮度公式手算的，不是編譯結果。 |
| rspec (spec/components/discourse_stylesheets_spec.rb) | 略過 | 這支既有的 spec 會實際編譯 desktop 與 mobile bundle，是唯一能擋下 SCSS 語法錯誤的機制，但本次環境沒有安裝 gem 相依，無法執行。 |
| ncr-fresh-eyes | 略過 | 這個執行環境沒有可派送 subagent 的工具，fresh eyes 這一步沒有執行，也沒有由主 agent 自行模擬。這代表本次審查缺少一次未被 skill 框架塑形的獨立閱讀。 |
| ncr-quality-check | 略過 | 同上，無法派送 subagent，報告 JSON 沒有經過獨立的品質複查。 |

<details>
<summary>Suggestion（7）</summary>

#### F-001 `.badge-notification` 的背景改成由 $secondary 起算，但前景仍固定 $secondary，dark theme 的對比反而變差 — `app/assets/stylesheets/common/components/badges.css.scss:239`

面向 H 非 Python 檔 · Suggestion

**問題**：這是本次 116 處轉換裡唯一一處落在 background-color 上、而配對前景色沒有一起處理的宣告。這條規則的 color 寫死是 $secondary（badges.css.scss:235，&[href] 在 :241 又設一次），不隨主題切換。改動後 dark theme 的背景變成 scale-color($secondary, $lightness: 30%)，與文字色同樣衍生自 $secondary，兩者只差一次 30% 的 lightness scale —— 不論某個主題的 $secondary 實際是什麼顏色，它們都必然靠得很近，這是運算式結構決定的，不是特定主題的巧合。以典型 dark scheme（$primary #dddddd、$secondary #222222）試算：改動前背景 scale-color($primary, 70%) ≈ #f5f5f5，對 #222222 的對比 14.57:1；改動後背景 ≈ #646464，對比掉到 2.70:1，低於 WCAG AA 的 4.5:1。已確認確實有不覆寫背景的使用點會吃到這個值：topic map 的 .topic-links .badge-notification（desktop/topic-post.scss:401、mobile/topic-post.scss:274）與 mobile header 的 icon badge（mobile/header.scss:21，該處還特地把 color 再設成 $secondary）。帶 .new-posts / .unread-posts / .new-topic / .clicks 的變體另有自己的背景，不受影響。light theme 這一支的值沒有變（本來就是 1.88:1 的既有狀況，不屬於本次改動）。

**證據**：
- `app/assets/stylesheets/common/components/badges.css.scss:239`
- `app/assets/stylesheets/common/components/badges.css.scss:235`
- `app/assets/stylesheets/common/components/badges.css.scss:241`
- `app/assets/stylesheets/desktop/topic-post.scss:401`
- `app/assets/stylesheets/mobile/header.scss:21`

**修復方向**：兩個方向擇一。(1) 讓前景一起鏡射：badges.css.scss:235 與 :241 都改成 color: dark-light-choose($secondary, $primary);，這樣 light theme 維持白字淺底、dark theme 變成亮字深底。(2) 這一行不轉換，維持原本的 background-color: scale-color($primary, $lightness: 70%); —— dark theme 下 $primary 本來就是亮色，算出來就是「淺底深字」的正確結果，這一處原本沒有壞。

#### F-002 `.embedded-posts .topic-meta-data h5 a` 的 light theme 顏色被一起改掉（30% → 70%），兩個引數疑似寫反 — `app/assets/stylesheets/desktop/topic-post.scss:291`

面向 I 回溯分析 · Suggestion

**問題**：這次改動的規則是「第一個引數原封不動保留 scale-color($primary, $lightness: X%)，第二個引數補上 scale-color($secondary, $lightness: 100-X%)」，diff 裡 116 處有 111 處都遵守。這一行的第一個引數從 30% 變成 70%，而第二個引數是 30% —— 兩個數字剛好對調，形狀就是引數寫反。後果是 light theme（預設主題）下展開回覆區塊裡的作者名連結，從接近 $primary 的深色變成偏淺的灰色，對比明顯下降；而這個 commit 宣稱處理的只有 dark theme。已對整個 repo 的 .scss 搜尋過 topic-meta-data，desktop 這一條沒有更後面的 color 覆寫（mobile/topic-post.scss:123 的同名選擇器只設 margin-left），所以這個變化會實際生效。

**證據**：
- `app/assets/stylesheets/desktop/topic-post.scss:291`

**修復方向**：改回 dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%))。若 70% 是刻意的設計調整，請拆成獨立 commit 並在訊息裡說明，不要混在機械轉換裡 —— 混在一起的話之後沒有人能從 diff 分辨哪些值是刻意改的。

#### F-003 mobile `.custom-message-length` 的 light theme 顏色被改掉，且與 desktop 同名規則方向相反 — `app/assets/stylesheets/mobile/modal.scss:102`

面向 I 回溯分析 · Suggestion

**問題**：mobile 這一行原本是 scale-color($primary, $lightness: 70%)，轉換後第一個引數變成 30%。desktop 的同名選擇器（desktop/modal.scss:94）在同一個 diff 裡轉成 70% / 30%，兩邊現在剛好相反：light theme 下 mobile 的字數提示會比原本深很多、也比 desktop 深很多；dark theme 下則反過來比 desktop 淺很多。兩個檔案的這個選擇器在改動前是同一個值，改動後才分岔。

**證據**：
- `app/assets/stylesheets/mobile/modal.scss:102`
- `app/assets/stylesheets/desktop/modal.scss:94`

**修復方向**：改成 dark-light-choose(scale-color($primary, $lightness: 70%), scale-color($secondary, $lightness: 30%))，與 desktop/modal.scss:94 對齊。

#### F-004 mobile `.topic-map h3` 的 light theme 顏色被改掉，與同區塊的 h4 變成同色 — `app/assets/stylesheets/mobile/topic-post.scss:182`

面向 I 回溯分析 · Suggestion

**問題**：mobile 的 .topic-map h3 原本是 scale-color($primary, $lightness: 20%)，轉換後變成 50% / 50%，與緊接在後面的 h4（mobile/topic-post.scss:190，同樣是 50% / 50%）完全一樣，topic map 標題與副標之間的深淺層次消失。desktop 的對應規則（desktop/topic-post.scss:325）保留了 20%、轉成 20% / 80%，所以改動後兩個平台也不再一致。

**證據**：
- `app/assets/stylesheets/mobile/topic-post.scss:182`
- `app/assets/stylesheets/mobile/topic-post.scss:190`
- `app/assets/stylesheets/desktop/topic-post.scss:325`

**修復方向**：改成 dark-light-choose(scale-color($primary, $lightness: 20%), scale-color($secondary, $lightness: 80%))，與 desktop/topic-post.scss:325 一致。

#### F-005 `.group-member-info .name` 的 light theme 顏色被改掉，與同層的 `.title` 變成同色 — `app/assets/stylesheets/desktop/user.scss:522`

面向 I 回溯分析 · Suggestion

**問題**：desktop 與 mobile 兩個檔案的 .name 原本都是 scale-color($primary, $lightness: 30%)，比同一層 .title 的 50% 深一階；轉換後兩處 .name 都變成 50% / 50%，和 .title（desktop/user.scss:527、mobile/user.scss:503，同樣 50% / 50%）完全一樣。群組成員列表裡名稱與頭銜的視覺層次因此消失，light theme 與 dark theme 都受影響。兩個檔案同時出現同樣的偏差，比較像是同一次複製貼上帶過去的，而不是兩次獨立的設計決定。

**證據**：
- `app/assets/stylesheets/desktop/user.scss:522`
- `app/assets/stylesheets/mobile/user.scss:497`
- `app/assets/stylesheets/desktop/user.scss:527`
- `app/assets/stylesheets/mobile/user.scss:503`

**修復方向**：兩處 .name 都改成 dark-light-choose(scale-color($primary, $lightness: 30%), scale-color($secondary, $lightness: 70%))。

#### F-006 `.topic-list.categories .category .badge-notification` 漏做轉換 — `app/assets/stylesheets/common/base/_topic-list.scss:115`

面向 H 非 Python 檔 · Suggestion

**問題**：對 HEAD 上所有 .scss 搜尋 scale-color($primary 之後，這是唯一一處還是裸寫法的顏色宣告（其餘只剩 common/base/discourse.scss:73 的一行註解）。同一個檔案裡另外六處都轉了，而且同名的 .badge-notification 在 common/base/header.scss:320 這次也一起轉了，所以不像刻意排除。留著的話 dark theme 下這個分類計數會被往白色推，與本次改動要修的問題完全相同。已確認這個補漏沒有出現在 diff 的其他地方。

**證據**：
- `app/assets/stylesheets/common/base/_topic-list.scss:115`
- `app/assets/stylesheets/common/base/header.scss:320`

**修復方向**：比照同檔案其他處改成 dark-light-choose(scale-color($primary, $lightness: 50%), scale-color($secondary, $lightness: 50%))。

#### F-007 同一條 light/dark 對映規則被逐字展開 116 次，建議抽成一個 function — `app/assets/stylesheets/common/foundation/variables.scss:46`

面向 B 簡潔 · Suggestion

**問題**：這次改動把同一條規則 —— light 用 scale-color($primary, X%)、dark 用 scale-color($secondary, 100-X%) —— 在 32 個檔案裡展開了 116 次，每一處都要靠人工維持兩個引數加起來是 100 的關係。F-002 到 F-005 那五處偏差正是這樣產生的：規則本身沒問題，是抄寫的地方太多而且沒有任何一個位置可以集中檢查。這條規則只有一個自由參數，用一個 function 就能完整表達，也讓後續 review 只需要看一個數字而不是一整條運算式。

**證據**：
- `app/assets/stylesheets/common/foundation/variables.scss:46`
- `app/assets/stylesheets/common/base/_topic-list.scss:55`
- `app/assets/stylesheets/desktop/topic-post.scss:291`

**修復方向**：在 common/foundation/variables.scss 的 dark-light-choose 旁邊加一個 helper：

```scss
@function dark-light-scale($lightness) {
  @return dark-light-choose(
    scale-color($primary, $lightness: $lightness),
    scale-color($secondary, $lightness: 100% - $lightness)
  );
}
```

呼叫點就變成 `color: dark-light-scale(50%);`。兩個引數的關係只存在一個地方，diff 也會從 116 行難以逐一核對的長運算式縮成掃一眼就能看完的短行。若不想在這個 commit 一併做，至少把它排成緊接著的後續。

</details>

<details>
<summary>Nit（2）</summary>

#### F-008 註解裡引用的預設值在這次改動後已經失準 — `app/assets/stylesheets/common/base/discourse.scss:73`

面向 A 風格 · Nit

**問題**：註解寫「the default for table cells in topic list is scale-color($primary, $lightness: 50%)」，用來解釋下面 coldmap 三段為什麼取 70/60/50。但 topic list 的 td 在這次改動後已經是 dark-light-choose(...)（common/base/_topic-list.scss:55），註解指向的寫法在 repo 裡已經不存在了。之後看到的人會照著註解把舊寫法補回去。

**證據**：
- `app/assets/stylesheets/common/base/discourse.scss:73`
- `app/assets/stylesheets/common/base/_topic-list.scss:55`

**修復方向**：把註解同步成 dark-light-choose(scale-color($primary, $lightness: 50%), scale-color($secondary, $lightness: 50%))，或改成直接指向 common/base/_topic-list.scss 的那條規則，讓它不會再隨值飄掉。

#### F-009 轉換時順手改了行尾空白，同一批改動裡沒有統一 — `app/assets/stylesheets/desktop/queued-posts.scss:14`

面向 A 風格 · Nit

**問題**：五處單行規則在轉換時把 ...;} 改成 ...; }（desktop/queued-posts.scss:14、desktop/topic-list.scss:54、desktop/topic-post.scss:113 與 :294、:941），但同樣形式的 common/base/_topic-list.scss:51 維持 ;}。這些空白調整和本次的顏色修正無關，卻會讓之後 git blame 這幾行時指到這個 commit。

**證據**：
- `app/assets/stylesheets/desktop/queued-posts.scss:14`
- `app/assets/stylesheets/desktop/topic-list.scss:54`
- `app/assets/stylesheets/desktop/topic-post.scss:113`
- `app/assets/stylesheets/common/base/_topic-list.scss:51`

**修復方向**：統一成其中一種寫法，或把純空白的調整從這次改動裡拿掉，讓這批 diff 只剩顏色本身的變化。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 mobile 編輯器 disabled 狀態下，dark theme 的背景與文字色會不會撞在一起？

面向 H 非 Python 檔

**背景**：.wmd-input:disabled 與 #reply-title:disabled 的背景這次從 scale-color($primary, $lightness: 75%) 改成 dark-light-choose(..., scale-color($secondary, $lightness: 25%))（mobile/compose.scss:172、:168）。但同一個元素的文字色在 mobile/compose.scss:175 是 darken($primary, 40%)，這一行不隨主題切換、也不在本次改動範圍內。改動前 dark theme 的背景是由 $primary（亮色）算出來的淺底，改動後變成由 $secondary 算出來的深底，文字卻沒跟著調整。與 F-001 不同的是，這裡前景與背景衍生自不同的變數，會不會真的撞在一起取決於各主題實際設定的 $primary / $secondary，不是由運算式結構保證的，因此沒有給嚴重度。

**如何確認**：用專案實際使用的 dark color scheme 編譯後量一次 .wmd-input:disabled 的前景／背景對比，或直接截一張 dark theme 下編輯器 disabled 狀態的畫面。若確認會撞，順手把 mobile/compose.scss:175 的 darken($primary, 40%) 也改成主題感知的寫法。

#### Q-002 反方向的同類寫法（只在 dark theme 成立的 scale-color($secondary, ...)）是刻意留到之後處理，還是漏掉了？

面向 H 非 Python 檔

**背景**：這次把 scale-color($primary, ...) 全面包成 dark-light-choose，但 mobile/compose.scss:33 的 #file-uploading { color: scale-color($secondary, $lightness: 50%); } 是完全對稱的反例 —— light theme 下 $secondary 是接近白色的底色，往白色再 scale 還是白，等於白字配 dark-light-diff($primary, $secondary, 90%, -60%) 的淺底。它就在這次改到的 #draft-status（mobile/compose.scss:36）正上方三行，同一個規則區塊裡。mobile/topic-list.scss:68 的 .highlighted { background-color: scale-color($tertiary, $lightness: 85%); } 也類似 —— desktop 的對應規則（desktop/topic-list.scss:59）用的是 dark-light-diff($tertiary, $secondary, 85%, -65%)。兩處都不在本次 diff 內，屬於既有狀況，所以沒有給嚴重度。

**如何確認**：作者說明這次 sweep 的範圍是否刻意只限 $primary 起算的顏色。如果是，把剩下兩類（$secondary 起算、$tertiary 起算）記成後續項目即可；如果不是，這兩處可以一起收進來。

</details>
