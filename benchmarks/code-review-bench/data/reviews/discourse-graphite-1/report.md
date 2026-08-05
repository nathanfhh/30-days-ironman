## 審查結論：Request Changes

> Critical 1 · Suggestion 6 · Nit 1 · 未驗證提問 4
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ✅ | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：同一個類別裡出現兩個同名的 self.downsize（app/models/optimized_image.rb:145、149），名稱完全看不出後者會靜默覆蓋前者；"80%"、attempt = 5、10 * 1024 三個數字都沒有說明它們的來源。詳見 F-001、F-008。
- **B 簡潔**（未通過）：app/models/optimized_image.rb:145-147 的四參數 downsize 在同一次提交裡就已經是死碼（被 149 覆蓋）；uploads_controller.rb:63-70 就地覆寫使用者原檔是一個從呼叫端看不出來的破壞性副作用。詳見 F-001、F-007。
- **E 架構**（未通過）：縮圖的業務邏輯被寫在 controller 的 create_upload 裡，其他 9 個 Upload.create_for 呼叫端拿不到；10MB 這個決策同時硬編在 4 個地方而彼此沒有任何連結說明。詳見 F-003、F-004。
- **F 資料取用與資料庫**（未通過）：迴圈以 tempfile.size 作為終止條件、又把同一個值當成 filesize 寫進 Upload，但完全不檢查 OptimizedImage.downsize 的回傳值，也沒有處理 5 次用盡後仍然超標的情況。詳見 F-006、Q-002、Q-004。
- **G 測試**（未通過）：diff 只動了 3 個檔案、沒有任何測試。既有的 test/javascripts/lib/utilities-test.js.es6:52-61 會因為這次變更而失敗，spec/models/optimized_image_spec.rb 也完全沒有涵蓋 downsize / optimize。詳見 F-005。
- **H 非 Python 檔**（未通過）：diff 含 app/assets/javascripts/discourse/lib/utilities.js。前端把 site setting 驅動的大小上限換成硬編值，使 attachment 的用戶端檢查與伺服器端脫鉤，並讓錯誤訊息顯示一個管理者設定裡不存在的數字。詳見 F-002。
- **I 回溯分析**（未通過）：OptimizedImage.downsize 的簽章在這次變更中被改掉，而 app/jobs/regular/resize_emoji.rb:14 這個呼叫端沒有跟著改，會直接拋 ArgumentError。詳見 F-001。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 實際上綑綁了三件可以拆開的事：(1) 上傳時自動縮圖的新功能；(2) OptimizedImage 的 dimensions 參數重構（app/models/optimized_image.rb:141-158）；(3) 前端把 max_*_size_kb 換成硬編 10MB（app/assets/javascripts/discourse/lib/utilities.js:181、247）。(2) 正是造成 F-001 的原因，(3) 連帶改變了 attachment 的行為，而 attachment 根本不在「downsize large images」的範圍內。三件事混在一起，讓一個純功能 MR 夾帶了兩個非預期的行為變更。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。Gemfile.lock 的套件版本因此沒有經過弱點比對。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且預設的 semgrep-rules 規則目錄不存在，本次未執行 SAST 掃描。JavaScript 變更沒有經過規則比對。 |
| ruff | 略過 | ruff 有安裝且執行成功（exit 0、0 件），但本 repository 沒有任何 .py 檔案、本次 diff 也只有 .rb 與 .js，因此這個乾淨結果不構成任何覆蓋率，等同未掃描。 · exit code 0 · in_diff 0、outside_diff 0 |
| ty | 略過 | ty 未安裝（不在 PATH 上）；即使安裝也不適用，本次 diff 沒有 Python 檔案。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。本次 diff 含 app/assets/javascripts/discourse/lib/utilities.js，這是唯一可惜的缺口——JavaScript 變更沒有任何靜態檢查覆蓋，只能靠人工閱讀。 |
| rubocop | 略過 | 本 skill 未整合 Ruby linter，環境中也沒有 rubocop。Ruby 是本次 diff 的主要語言，因此 Ruby 側完全沒有工具覆蓋，所有 Ruby 結論都來自人工閱讀與 grep 導覽，並以 ruby 3.3.6 直接執行最小重現腳本驗證（見 F-001）。 |
| codegraph | 略過 | codegraph 未安裝，Phase 0 的 init 未執行。呼叫端列舉（dimension E、I）全部改用 grep 完成，完整性依賴 grep 的字面比對，動態呼叫（send、字串索引的 registry）不在覆蓋範圍內。 |
| ncr-fresh-eyes | 略過 | 本次執行環境沒有任何可派送 subagent 的工具（Task / Agent 皆不存在），因此 Phase 3 的 fresh eyes 未執行，也未在主 agent 內部模擬——SKILL.md 明確禁止模擬。本報告的所有觀察都來自已讀過 review-dimensions.md 的視角，缺少一次未被清單框住的閱讀。 |
| ncr-quality-check | 略過 | 同上，無法派送 subagent，Phase 4 step 3 的品質複核未執行。本報告只通過 report_model.py 的機械驗證，沒有第二雙眼睛檢查敘述與嚴重度。 |

### Critical

#### F-001 `OptimizedImage.downsize` 被重複定義，四參數版本靜默失效，`Jobs::ResizeEmoji` 每次執行都會拋 ArgumentError — `app/models/optimized_image.rb:145`

面向 I 回溯分析 · Critical

**問題**：這次變更保留了原本的 `def self.downsize(from, to, max_width, max_height, opts={})`（:145），又在它下面新增了 `def self.downsize(from, to, dimensions, opts={})`（:149）。Ruby 沒有多載，同一個類別裡後定義的方法會直接覆蓋前一個，所以 :145 的四參數版本從這次提交起就是死碼，`OptimizedImage.downsize` 的實際 arity 只剩 3..4。

以 ruby 3.3.6 用等價的最小類別實測，`downsize("a", "b", 100, 100, {allow_animation: true})` 會得到 `ArgumentError: wrong number of arguments (given 5, expected 3..4)`，`method(:downsize).parameters` 也確認只剩 `[[:req, :from], [:req, :to], [:req, :dimensions], [:opt, :opts]]`。

反證搜尋的結果：全 repo `grep -rn "def self.downsize"` 只有 optimized_image.rb 這兩處，沒有其他檔案或 plugin 重新打開 `class OptimizedImage`；`grep -rn "downsize"` 找到的呼叫端只有兩個——本次新增的 uploads_controller.rb:67（三參數，可以正常執行）與 app/jobs/regular/resize_emoji.rb:14（五參數，必然失敗）。後者是 Sidekiq job，由 app/models/emoji.rb:73 在 `Emoji.create_for` 尾端 enqueue，而 `Emoji.create_for` 由 app/controllers/admin/emojis_controller.rb:19 的管理者上傳自訂 emoji 觸發，是一條實際會走到的路徑。沒有任何測試涵蓋它（`grep -rn "ResizeEmoji" spec/` 無結果），所以 CI 也不會擋下來。

影響是：管理者上傳的自訂 emoji 從此不會被縮到 100x100，`enforce_square_emoji` 也一併失效；失敗發生在背景 job，前台看起來是成功的，只有 Sidekiq 的失敗佇列會留下痕跡。

**證據**：
- `app/models/optimized_image.rb:145`
- `app/models/optimized_image.rb:149`
- `app/jobs/regular/resize_emoji.rb:14`
- `app/models/emoji.rb:73`
- `app/controllers/admin/emojis_controller.rb:19`

**修復方向**：不要新增第二個定義，直接把既有的 :145 改成接受 dimensions 字串即可，再把呼叫端一起改掉：

```ruby
# app/models/optimized_image.rb
def self.downsize(from, to, dimensions, opts={})
  optimize("downsize", from, to, dimensions, opts)
end
```

```ruby
# app/jobs/regular/resize_emoji.rb:14
OptimizedImage.downsize(path, path, "100x100", opts)
```

若希望保留舊呼叫方式，另外開一個名字不同的相容包裝（例如 `downsize_to`），而不是同名重複定義——同名重複定義在 Ruby 不會有任何警告（除非以 `-w` 執行），下一個維護者看到 :145 還在，會以為它仍然有效。

<details>
<summary>Suggestion（6）</summary>

#### F-002 前端大小上限硬編成 10MB，attachment 的用戶端檢查與 `max_attachment_size_kb` 脫鉤，錯誤訊息也會顯示管理者設定裡不存在的數字 — `app/assets/javascripts/discourse/lib/utilities.js:181`

面向 H 非 Python 檔 · Suggestion

**問題**：`validateUploadedFile` 是 image 與 attachment 共用的（utilities.js:157 依副檔名決定 `type`，兩種都走到同一段大小檢查），原本 `Discourse.SiteSettings['max_' + type + '_size_kb']` 會依 type 取對應設定，改成 `10 * 1024` 之後兩種型別都吃同一個硬編值。

伺服器端沒有跟著改：lib/validators/upload_validator.rb:83 仍然用 `SiteSetting.send("max_#{type}_size_kb")` 檢查，而本次新增的自動縮圖只涵蓋 `FileHelper.is_image?(filename)` 為真的檔案（uploads_controller.rb:64），attachment 完全不在其中。所以在預設值（max_attachment_size_kb = 3072，config/site_settings.yml:516-518）之下，一個 5MB 的 zip 會通過前端檢查、整包傳到伺服器、才在 validator 被退回。管理者把上限調得更低時，落差更大。

反方向也一樣：管理者若把 `max_image_size_kb` 設成大於 10240，使用者會在前端被 10240 這個他在後台看不到的數字擋下來（`file_too_large` 訊息會直接把 10240 印出來，config/locales/client.en.yml:1297）。

同一個畫面裡還留著另一個仍然讀設定的地方：upload-selector.js.es6:17 的 `maxSize: setting('max_attachment_size_kb')`，它決定「是否顯示本機上傳」，現在跟實際的檢查值不再一致。

這不是安全問題——伺服器端仍然是真正的邊界，前端上限本來就不能當防線；它是行為與設定的一致性問題。

**證據**：
- `app/assets/javascripts/discourse/lib/utilities.js:181`
- `app/assets/javascripts/discourse/lib/utilities.js:247`
- `app/assets/javascripts/discourse/lib/utilities.js:157`
- `app/assets/javascripts/discourse/controllers/upload-selector.js.es6:17`
- `lib/validators/upload_validator.rb:83`

**修復方向**：把上限重新綁回 site setting，並讓「10MB 是 web server 的硬上限」這件事以獨立的方式表達，而不是覆蓋掉型別相關的設定。例如：

```javascript
var maxSizeKB = Discourse.SiteSettings['max_' + type + '_size_kb'];
// 圖片超過上限時伺服器會自動縮小，所以前端只擋 web server 真正收不下的大小
// （見 config/nginx.sample.conf 的 client_max_body_size）
if (type === 'image') { maxSizeKB = Math.max(maxSizeKB, Discourse.SiteSettings.max_upload_body_size_kb); }
```

若不想新增 site setting，至少讓 attachment 分支維持原本的 `max_attachment_size_kb`，只放寬 image 分支——放寬 attachment 並沒有對應的伺服器端行為支撐它。

#### F-003 「10MB」這個決策同時硬編在 4 個地方，彼此沒有任何說明連結 — `app/assets/javascripts/discourse/lib/utilities.js:181`

面向 E 架構 · Suggestion

**問題**：config/nginx.sample.conf:61 是 `client_max_body_size 10m;`，這幾乎可以確定就是本次三個 10MB 的來源——前端擋在 web server 會回 413 之前，後端 `FileHelper.download` 也用同一個數字。這個推論是合理的，但程式碼裡完全沒有寫出來：三處註解只寫了 `// 10MB`、`// 10 MB`，等於把「這個值是什麼」重複了一次，卻沒有寫「這個值為什麼是 10」。

後果是要調整上限得同時改 4 個地方，而且其中一個在 nginx 設定檔裡、另外兩個在 JavaScript 裡、第四個在 Ruby controller 裡，沒有任何搜尋關鍵字會把它們串起來。漏改任何一個都會產生難以診斷的落差：nginx 調大而前端沒調，使用者被前端擋住；前端調大而 nginx 沒調，使用者收到裸的 413。

utilities.js:247 的 413 分支尤其脆弱——它顯示的 `max_size_kb` 是前端自己的猜測，而真正產生 413 的是 web server，兩者一旦不同步，錯誤訊息就會說謊。

**證據**：
- `app/assets/javascripts/discourse/lib/utilities.js:181`
- `app/assets/javascripts/discourse/lib/utilities.js:247`
- `app/controllers/uploads_controller.rb:55`
- `config/nginx.sample.conf:61`

**修復方向**：把這個值變成單一來源。最小的做法是新增一個 client-visible 的 site setting（例如 `max_upload_body_size_kb`），讓前後端都從它讀，並在 config/nginx.sample.conf:61 旁邊加一行註解指向它：

```yaml
# config/site_settings.yml
max_upload_body_size_kb:
  client: true
  default: 10240   # 必須與 nginx 的 client_max_body_size 一致
```

如果暫時不想新增設定，退而求其次：在三處註解都寫清楚「此值必須與 config/nginx.sample.conf 的 client_max_body_size 一致」，讓下一個改動的人至少 grep 得到彼此。

#### F-004 自動縮圖寫在 controller 裡，其他 9 個 `Upload.create_for` 呼叫端都拿不到這個行為 — `app/controllers/uploads_controller.rb:63`

面向 E 架構 · Suggestion

**問題**：新的縮圖迴圈放在 `UploadsController#create_upload`，但真正決定「圖片太大就退件」的是 `Upload.create_for` 內部的 `Validators::UploadValidator`。`grep -rn "Upload.create_for"` 顯示（排除 script/import_scripts）另外還有 4 個產品程式碼的呼叫端：郵件收件（lib/email/receiver.rb:235）、抓取外連圖片（app/jobs/regular/pull_hotlinked_images.rb:41）、以及兩處頭像下載（app/models/user_avatar.rb:23、71）。這些路徑一樣會餵進超過 `max_image_size_kb` 的圖片，一樣會被同一個 validator 退掉，卻不會享受到自動縮小。

以 lib/email/receiver.rb:235 為例，使用者用 email 回覆並夾帶一張 4MB 照片，在預設 3072KB 之下這封信的附件會被丟掉；同一張照片從瀏覽器上傳則會被自動縮到 3MB 以內成功。同一個功能承諾在兩條入口給出相反的結果，而使用者無從得知差別在哪。

這也是 dimension E3 的典型形狀：一段真正的業務規則（「圖片超標就重複縮小直到符合」）長在 endpoint 裡，而不是長在擁有這條規則的 model 上。

**證據**：
- `app/controllers/uploads_controller.rb:63`
- `app/models/upload.rb:59`
- `lib/email/receiver.rb:235`
- `app/jobs/regular/pull_hotlinked_images.rb:41`
- `app/models/user_avatar.rb:23`
- `app/models/user_avatar.rb:71`

**修復方向**：把這段迴圈往下搬到 `Upload.create_for`，放在既有的 `FileHelper.is_image?(filename)` 區塊內、`ImageOptim` 那一步之前，讓所有入口共用：

```ruby
# app/models/upload.rb，create_for 內的 is_image? 區塊
if SiteSetting.max_image_size_kb > 0
  attempt = DOWNSIZE_ATTEMPTS
  while attempt > 0 && File.size(file.path) > SiteSetting.max_image_size_kb.kilobytes
    break unless OptimizedImage.downsize(file.path, file.path, DOWNSIZE_RATIO, allow_animation: allow_animation?)
    attempt -= 1
  end
end
```

搬過去之後 controller 那 8 行可以整段刪掉，`filesize` 也能改用搬移後才計算的值（見 F-006 / Q-002）。若判斷 email 與 hotlinked 路徑刻意不該縮圖，那也請在 controller 的註解裡把這個取捨寫下來——目前看不出來是決定還是遺漏。

#### F-005 既有 JavaScript 測試會因為這次變更而失敗，且新行為完全沒有測試 — `test/javascripts/lib/utilities-test.js.es6:52`

面向 G 測試 · Suggestion

**問題**：test/javascripts/lib/utilities-test.js.es6:52 的 `prevents files that are too big from being uploaded` 會設定 `Discourse.SiteSettings.max_image_size_kb = 5`，丟一個 `size: 10 * 1024`（即 10KB）的圖片，然後斷言 `not(validUpload([image]))` 且 bootbox 收到 `max_size_kb: 5`。改成硬編 `10 * 1024` KB 之後，10KB 的檔案遠低於 10240KB，`validateUploadedFile` 會回傳 true，這個測試的兩條斷言都會翻掉。這正是 dimension G 想抓的東西：測試已經把舊行為釘住了，行為改了而測試沒有一起改。

另一側完全沒有覆蓋。整個 diff 只動了 3 個檔案、沒有新增任何測試：spec/models/optimized_image_spec.rb 只測 `.local?` 與 `.create_for`，把 `resize` 整個 stub 掉，從來不碰 `downsize` / `optimize` / `dimensions`（因此 F-001 那個必然的 ArgumentError 在 CI 上是隱形的）；spec/controllers/uploads_controller_spec.rb 沒有任何 `create_upload` 或 `downsize` 相關案例。

這裡值得一提的是斷言品質而不只是數量：新迴圈值得測的不是「有沒有呼叫 downsize」（那只會驗證 mock），而是「一個超過 max_image_size_kb 的檔案跑完之後，實際大小落在上限以內」。

**證據**：
- `test/javascripts/lib/utilities-test.js.es6:52`
- `test/javascripts/lib/utilities-test.js.es6:55`
- `test/javascripts/lib/utilities-test.js.es6:59`
- `app/assets/javascripts/discourse/lib/utilities.js:181`

**修復方向**：1. 更新 test/javascripts/lib/utilities-test.js.es6:52-61，讓它反映新的上限來源；如果採納 F-002 改回讀 site setting，這個測試不必動。
2. 為 `OptimizedImage.downsize` 加一個 spec，直接斷言新的三參數簽章，並補一個涵蓋 `Jobs::ResizeEmoji` 的測試（它現在沒有任何 spec）：

```ruby
describe ".downsize" do
  it "passes the dimensions through to the instructions" do
    OptimizedImage.expects(:convert_with).with(includes("80%"), anything).returns(true)
    OptimizedImage.downsize("a.png", "a.png", "80%")
  end
end
```

3. 為 controller 的迴圈加一個 spec：準備一個大於 `max_image_size_kb` 的圖片檔，跑完之後斷言 `File.size(path)` 已落在上限以內，而不是斷言 mock 被呼叫過。

#### F-006 縮圖迴圈丟棄 `OptimizedImage.downsize` 的回傳值，也沒有處理 5 次用盡後仍然超標的情況 — `app/controllers/uploads_controller.rb:65`

面向 F 資料取用與資料庫 · Suggestion

**問題**：`OptimizedImage.downsize` 一路傳到 `convert_with`（app/models/optimized_image.rb:160-169），而 `convert_with` 是有回傳值的：外部指令失敗回 false，`ImageOptim` 拋例外時也 rescue 成 false。uploads_controller.rb:67 把這個值整個丟掉。

後果分兩層。第一層：一旦 convert 失敗（動畫 GIF 走的是 gifsicle 分支、格式不支援、外部工具沒裝），檔案大小完全不變，`while` 的條件永遠成立，於是同一個必定失敗的指令會被連續執行 5 次，然後帶著原尺寸的檔案往下走。5 次無效的 subprocess 不是災難，但它掩蓋了「這張圖沒被縮小」這個事實。

第二層：迴圈跑完之後不論成功與否都直接進入 uploads_controller.rb:72 的 `Upload.create_for`。若 5 次之後仍然超標（0.8^5 ≈ 線性 33%、面積 11%，多數情況夠用，但極端大圖不夠），validator 會用一般的 `upload.images.too_large` 訊息退件。使用者看到的是「圖片太大」，而不是「我們試著縮小但沒成功」，而伺服器已經為這張圖跑了 5 次 ImageMagick。

（相關但無法在此環境確認的部分見 Q-002 與 Q-004：`tempfile.size` 是否觀察得到縮小、以及 `convert_with` 的 exit code 檢查在實際部署的 shell 下是否真的有效。這兩點都會讓上述第一層更難察覺，但都需要在真實部署環境才能定案。）

**證據**：
- `app/controllers/uploads_controller.rb:65`
- `app/controllers/uploads_controller.rb:67`
- `app/controllers/uploads_controller.rb:72`
- `app/models/optimized_image.rb:160`

**修復方向**：檢查回傳值並在失敗時跳出，讓失敗至少留下紀錄：

```ruby
if tempfile && tempfile.size > 0 && SiteSetting.max_image_size_kb > 0 && FileHelper.is_image?(filename)
  attempt = DOWNSIZE_ATTEMPTS
  while attempt > 0 && File.size(tempfile.path) > SiteSetting.max_image_size_kb.kilobytes
    unless OptimizedImage.downsize(tempfile.path, tempfile.path, DOWNSIZE_RATIO, allow_animation: ...)
      Rails.logger.warn("Could not downsize #{filename} for user #{current_user.id}")
      break
    end
    attempt -= 1
  end
end
```

若希望在用盡次數後給使用者更清楚的訊息，可以在這裡就 return 一個帶說明的 error hash，而不是讓 validator 用泛用訊息接手。

#### F-007 用 `allow_animated_thumbnails` 決定原檔要不要保留動畫；設為 false 時會把動態 GIF 就地壓成第一格 — `app/controllers/uploads_controller.rb:67`

面向 B 簡潔 · Suggestion

**問題**：兩個問題疊在同一行。

其一是設定的語意錯配。`allow_animated_thumbnails`（config/site_settings.yml:574，預設 true）管的是「縮圖要不要保留動畫」——縮圖是衍生物，丟掉動畫只影響預覽。這裡處理的卻是使用者上傳的原始檔案，而且是就地覆寫（`from` 與 `to` 都是 `tempfile.path`）。用一個名字裡有 thumbnail 的設定去決定原檔的命運，讀 code 的人不會預期到這件事，管理者在後台關掉它時更不會。

其二是關掉之後的實際行為。app/models/optimized_image.rb:155 只有在 `opts[:allow_animation]` 為真時才切到 `_animated` 分支；為假時走 `downsize_instructions`，指令是 `convert #{from}[0] ... #{to}`（:130），`[0]` 表示只取第一個 frame。於是一個超過上限的動態 GIF 會被縮成單張靜態圖、直接覆蓋掉原檔，使用者沒有收到任何提示，原始檔案已經不存在了。這是 dimension B5 說的那種「從呼叫端看不出來的副作用」——`downsize` 這個名字承諾的是縮小，不是抽掉動畫。

預設值是 true，所以預設部署不會踩到；但這正是難以發現的那一類問題：只有關掉該設定的站台才會遇到，而遇到時是不可逆的。

**證據**：
- `app/controllers/uploads_controller.rb:67`
- `app/models/optimized_image.rb:155`
- `app/models/optimized_image.rb:130`
- `config/site_settings.yml:574`

**修復方向**：原檔的動畫保留與否應該和縮圖分開判斷。最小的修法是在這一行明確地永遠保留動畫，並把理由寫下來：

```ruby
# 這裡處理的是使用者的原始檔案而非縮圖，動畫必須保留；
# allow_animated_thumbnails 只用於衍生的縮圖。
OptimizedImage.downsize(tempfile.path, tempfile.path, "80%", allow_animation: true)
```

若確實希望站台能選擇不保留，請新增一個名副其實的設定（例如 `allow_animated_uploads`），並在 `downsize_instructions` 走 `[0]` 這條路時記一筆 log，讓「動畫被拿掉了」這件事至少留下痕跡。

</details>

<details>
<summary>Nit（1）</summary>

#### F-008 `"80%"` 與 `attempt = 5` 是沒有說明的魔術數字 — `app/controllers/uploads_controller.rb:65`

面向 A 風格 · Nit

**問題**：這兩個數字一起決定了功能的上限：每次縮到線性 80%，最多 5 次，也就是最小可以縮到原尺寸的 0.8^5 ≈ 33%（面積約 11%）。這是一個實質的產品決策——它決定了「多大的圖還救得回來」——卻只以兩個裸值出現，沒有名字也沒有註解。下一個想調整的人得自己推導出這個乘冪關係。

同一行的 `"80%"` 還有一個額外的閱讀成本：它是一個字串，會被原封不動地拼進 ImageMagick 或 gifsicle 的參數（見 Q-001），從呼叫端看不出這個字串的合法格式由誰定義。

**證據**：
- `app/controllers/uploads_controller.rb:65`
- `app/controllers/uploads_controller.rb:67`

**修復方向**：抽成具名常數，並在旁邊寫下推導：

```ruby
# 每次把長寬縮到 80%（面積約 64%），最多 5 次；
# 也就是最小可縮到原始尺寸的 0.8^5 ≈ 33%。超過這個倍率的圖片會被退件。
DOWNSIZE_RATIO = "80%".freeze
DOWNSIZE_ATTEMPTS = 5
```

放在 `UploadsController` 或（若採納 F-004 搬進 model）`Upload` 上都可以，重點是讓這兩個值的關係寫在同一個地方。

</details>

<details>
<summary>未驗證提問（4）</summary>

#### Q-001 動畫分支收到的 `"80%"`，gifsicle 的 `--resize-fit` 接受嗎？

面向 F 資料取用與資料庫

**背景**：`allow_animation` 為真且來源是 .gif 時，app/models/optimized_image.rb:155 會切到 `downsize_instructions_animated`，它轉呼叫 `resize_instructions_animated`（:137-139），組出的指令是 `gifsicle #{from} --colors=256 --resize-fit #{dimensions} --optimize=3 --output #{to}`（:120）。在這次變更之前，唯一的 downsize 呼叫端是 app/jobs/regular/resize_emoji.rb:14 的 `100, 100`，經過舊的 `dimensions()` 之後永遠是 `WxH` 形式；`"80%"` 是本次新引進的、第一個非 WxH 的值。gifsicle 的 geometry 語法與 ImageMagick 不同（百分比縮放在 gifsicle 是 `--scale`），因此有相當理由懷疑 `--resize-fit 80%` 會被拒絕。但本審查環境沒有安裝 gifsicle 也沒有 ImageMagick（`which gifsicle convert magick` 皆無輸出），無法實測，repository 內也沒有任何既有用法可以佐證或推翻，所以不列為 finding。若成立，超過上限的動態 GIF 會在 allow_animated_thumbnails 為真時完全無法被縮小（並被 F-006 描述的無聲失敗掩蓋）。

**如何確認**：在有 gifsicle 的環境執行 `gifsicle in.gif --resize-fit 80% --output out.gif` 並看 exit code；或直接查該版本 gifsicle 的 man page 確認 `--resize-fit` 是否接受百分比。若不接受，動畫分支應改用 `--scale 0.8`，或在呼叫端就把百分比換算成實際的 WxH。

#### Q-002 `tempfile.size` 觀察得到 convert / ImageOptim 改寫後的新大小嗎？

面向 F 資料取用與資料庫

**背景**：迴圈的終止條件（uploads_controller.rb:66）與寫進 `Upload` 的 filesize（:72）都用 `tempfile.size`，而 `tempfile` 是一個開著的 `Tempfile`，檔案內容是被外部行程改掉的。`Tempfile#size` 在檔案未關閉時走的是開啟中 fd 的 fstat。以 ruby 3.3.6 實測兩種改寫方式：外部行程「就地截斷重寫」時 fd 看得到新大小（1000 → 100，正確）；外部行程「寫到暫存檔再 rename 蓋過去」時 fd 仍然回報舊大小（100，而 `File.size(path)` 是 10）。ImageMagick 的 convert 屬於前者，但 `convert_with` 之後還會呼叫 `ImageOptim.new.optimize_image!(to)`（app/models/optimized_image.rb:164），image_optim 0.20.2（Gemfile.lock:127）的 in-place 取代是否使用 rename，無法從這個 checkout 判斷——gem 原始碼不在 repository 內，本環境也沒有安裝。若它使用 rename，則迴圈永遠看不到縮小、會固定跑滿 5 次，而且寫進 `uploads.filesize` 的會是縮小前的舊值，validator 也會據此把一個已經合規的檔案退掉。

**如何確認**：閱讀 image_optim 0.20.2 的 `ImageOptim#optimize_image!` / `ImagePath#replace` 是否以 `File.rename` 收尾；或在有 ImageMagick 的環境準備一張大於 max_image_size_kb 的 JPEG，跑一次這段迴圈並同時印出 `tempfile.size` 與 `File.size(tempfile.path)`。無論結論如何，把兩處 `tempfile.size` 改成 `File.size(tempfile.path)` 都能讓這個問題消失。

#### Q-003 在沒有 10MB 請求體上限的部署上，這個迴圈的資源放大可以接受嗎？

面向 C 安全

**背景**：multipart 上傳這條路徑，應用層沒有任何大小上限：uploads_controller.rb:64-70 只檢查「比 max_image_size_kb 大」就進迴圈，不檢查「大多少」。一個 500MB 的 PNG 會讓同一個請求連續啟動最多 5 次 ImageMagick（記憶體用量約與像素數成正比）加上 5 次 ImageOptim，而且是在 `Scheduler::Defer` 的執行緒裡同步跑。變更前這條路徑也已經會對原檔跑一次 `convert -auto-orient`（app/models/upload.rb:162）與一次 ImageOptim，所以放大倍率大約是 5 倍而不是從零開始。附帶的 config/nginx.sample.conf:61 有 `client_max_body_size 10m`，若部署照抄這份範例，請求根本進不到 Rails，風險被上游擋掉——這也正是本次沒有把它列為 finding 的原因。但那只是一份 sample，Discourse 也支援其他反向代理與自訂設定，本審查無從得知目標環境實際的上限。

**如何確認**：確認目標部署的反向代理是否強制 `client_max_body_size`（或等價設定）在 10MB 左右。若不保證，應在進入迴圈前加一道明確的上界檢查（例如超過 `10.megabytes` 直接退件），讓應用層不再依賴上游設定。

#### Q-004 `convert_with` 的 exit code 檢查在實際部署的 shell 下真的有效嗎？

面向 F 資料取用與資料庫

**背景**：app/models/optimized_image.rb:161-162 用 backtick 執行指令、把輸出以 `&>` 重導向到空裝置，接著 `return false if $?.exitstatus != 0`。`&>` 是 bash 專有語法；當系統的預設 shell 是 dash（Debian/Ubuntu 預設）時，它會被解析成「背景執行 `&`」加上「一個沒有指令的重導向」。在本審查機器上以 dash 實測確認了兩件事：輸出並沒有被丟掉，而且 backtick 執行一個必定失敗的指令時 `$?.exitstatus` 仍然是 0（改寫成標準的 `> ... 2>&1` 形式則正確回 1，作為對照）。也就是說在 dash 上這個失敗偵測是完全失效的。這段程式碼本身不是這次變更寫的，屬於既有問題；但本次新增的迴圈是第一段真正依賴「downsize 有沒有成功」的邏輯（見 F-006），所以這個既有缺陷從這次變更起才開始有實際後果。本審查無法得知目標部署的預設 shell 指向什麼。

**如何確認**：在部署映像檔內確認預設 shell 是 bash 還是 dash；或直接把 `convert_with` 改成不依賴 shell 的 `system(*instructions, out: File::NULL, err: File::NULL)`，讓 exit code 的語意不再隨環境改變。

</details>
