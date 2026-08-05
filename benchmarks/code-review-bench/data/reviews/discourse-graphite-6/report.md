## 審查結論：Request Changes

> Critical 1 · Suggestion 3 · Nit 2 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ✅ | ✅ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ✅ | ✅ |

- **A 風格**（未通過）：website_name（user_serializer.rb:137-151）用了 rescue modifier 吞掉所有 StandardError，且單一 elsif 條件裡塞了巢狀三元運算子，需要讀好幾次才看得懂。詳見 F-005、F-006。另一方面 models/user.js.es6 把錯誤的 @property websiteName 修正為 profileBackground，是這次變更順手改對的一處文件。
- **B 簡潔**（未通過）：同一個 URI(website.to_s) 在 website_name 內最多解析兩次、split('.') 最多重算六次（user_serializer.rb:138-149），且 include_website_name 是一段永遠不會被呼叫的死碼。詳見 F-002、F-006。
- **C 安全**（未通過）：新增的 website_name 沒有納入 untrusted_attributes，繞過了 TL0 使用者對匿名訪客的欄位限制（F-001，Critical）；同網域判斷的 label 數啟發式在 com.br / co.uk 這類雙層公共後綴上會誤判成同組織（F-004）。
- **D API 慣例**（未通過）：include_website_name 少了問號（user_serializer.rb:153），不符合 ActiveModel::Serializers 0.8.3 的 include_<attr>? 契約，也不符合本 repo 內 100 處同類方法的慣例，導致該 attribute 無條件出現在每一份 user payload 上。詳見 F-002。
- **G 測試**（未通過）：新增的三個 example 都有實際斷言（比對回傳字串而非只檢查 present），品質沒問題；但同一個 spec 檔第 11 行的 untrusted_attributes 清單沒有一起補上 website_name，且註解宣稱要處理的 www.example.com vs forum.example.com 分支完全沒有測試。詳見 F-003。另註：環境沒有安裝 gem 也沒有網路，rspec 無法執行，這些測試「會不會過」本次沒有實測。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），preflight 已確認。相依套件漏洞、設定錯誤與 secret 掃描本次完全沒有執行，Gemfile.lock 的套件版本沒有被檢查過。 |
| opengrep | 略過 | opengrep 未安裝，且預設規則目錄（$HOME 下的 semgrep-rules）不存在，兩個前提都缺。SAST 沒有執行，本報告的安全類發現全部來自人工閱讀，不是工具佐證。 |
| ruff | 已執行 | 已執行、exit code 0、零告警，但這個結果沒有覆蓋意義：本 repo 一個 .py 檔都沒有，ruff 實際上沒有分析到任何檔案，更不用說 diff 內的 .rb / .js.es6 / .hbs。請不要把這一列讀成「靜態檢查通過」。 · in_diff 0、outside_diff 0 |
| ty | 略過 | ty 未安裝（不在 PATH 上）。本 repo 也沒有任何 .py 檔，即使安裝也無事可做。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。這是本次唯一能覆蓋 diff 中 .js.es6 檔案的 linter，因此 app/assets/javascripts/discourse/controllers/user.js.es6 沒有得到任何靜態檢查。 |
| rubocop | 略過 | 本 repo 沒有 .rubocop.yml，環境也沒有安裝任何 gem、沒有網路可安裝。diff 主體 app/serializers/user_serializer.rb 與 spec/serializers/user_serializer_spec.rb 因此沒有經過 Ruby linter，也無法執行 rspec 驗證新增測試會通過。 |
| ncr-fresh-eyes（subagent） | 略過 | 本次執行環境沒有可派工 subagent 的工具，無法派出 Phase 3 的 fresh-eyes。依 skill 規定不得由主 agent 自行模擬，因此這一輪缺少一次「未被本 skill 的分類與嚴重度詞彙塑形過」的獨立閱讀，可能漏掉不落在九個 dimension 命名範圍內的問題。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派工。Phase 4 的報告品質複檢沒有執行，本報告只通過 report_model.py 的機械驗證，沒有第二雙眼睛檢查措辭、嚴重度校準與四項發布規則。 |
| codegraph | 略過 | codegraph 未安裝，Phase 0 的符號索引沒有建立。呼叫關係與完整性（誰還在用 websiteName、誰在用 UserSerializer）全部改用 grep 逐一確認。 |

### Critical

#### F-001 website_name 未納入 untrusted_attributes，繞過 TL0 使用者對匿名訪客的欄位限制 — `app/serializers/user_serializer.rb:43`

面向 C 安全 · Critical

**問題**：UserSerializer 的 untrusted_attributes（user_serializer.rb:105-111）把 :website 列進去，由 user_serializer.rb:25-32 產生的 include_website? 在 scope.restrict_user_fields?(object) 成立時回傳 false。該判斷是 lib/guardian/user_guardian.rb:58 的 `user.trust_level == TrustLevel[0] && anonymous?`——也就是「TL0 帳號被匿名訪客觀看」這個典型的洗連結情境。這次新增的 :website_name（user_serializer.rb:43）是從同一份 object.user_profile.website 推導出來的資料，卻沒有加進 untrusted_attributes，本身的 include_website_name（user_serializer.rb:153）又因為少了問號而從不被呼叫（見 F-002），因此這條路徑上完全沒有守門。已逐一確認每一條到達點：users_controller.rb:33 的 users/:username.json 端點（匿名可存取）、users_controller.rb:100、session_controller.rb:262 都是同一個 UserSerializer 與同一個 guardian scope，沒有任何一條另外擋住 website_name。前端 user.hbs:66 的 {{#if model.website_name}} 只看 website_name，所以在 website 被遮蔽、website_name 沒被遮蔽時，畫面會渲染出 <a href="">spam.example.com</a>——網域文字照樣曝光給匿名訪客，連結卻指向空字串，同時是安全退化與顯示瑕疵。

**證據**：
- `app/serializers/user_serializer.rb:43`
- `app/serializers/user_serializer.rb:105`
- `app/serializers/user_serializer.rb:109`
- `app/serializers/user_serializer.rb:25`
- `app/serializers/user_serializer.rb:137`
- `lib/guardian/user_guardian.rb:58`
- `app/controllers/users_controller.rb:33`
- `app/assets/javascripts/discourse/templates/user/user.hbs:66`

**POC**：

````
在任一 Discourse instance 上：(1) 用 TL0 帳號在偏好設定頁的 website 欄位填 http://spam.example.com/promo；(2) 完全不帶 cookie 以匿名身分請求該使用者的 profile JSON：

```bash
curl -s https://forum.example.org/users/tl0spammer.json | jq '.user | {website, website_name}'
```

預期（修正前）：`website` 這個 key 不存在（已被 untrusted_attributes 遮蔽），但 `website_name` 回傳 "spam.example.com"。以瀏覽器匿名開啟同一位使用者的 profile 頁也會看到該網域文字。
````

**影響範圍**：任何 TL0 帳號自行填寫的網站網域，會對未登入訪客與爬蟲曝光在 profile 頁面上，正是 restrict_user_fields? 這道防線要擋掉的洗連結行為；當該網域與 instance 網域同源時連完整路徑一併曝光。影響範圍是全站所有 TL0 使用者的 profile，觸發條件只是「匿名開啟該頁」，不需要任何權限或前置條件。連結本身因為 href={{model.website}} 為空而不可點、也不會傳遞 SEO 權重，所以損害限於可見文字層級，不是可點擊的外連。本變更不涉及 PHI。

**風險處置**：Mitigate（降低）

**修復參考**：app/serializers/user_serializer.rb:105-111

**修復方向**：把 :website_name 加進 user_serializer.rb:105 的 untrusted_attributes 清單，並刪掉 user_serializer.rb:153-155 手寫的 include_website_name。untrusted_attributes 產生的方法本身就同時做了 `return false if scope.restrict_user_fields?(object)` 與 `send(attr).present?`，一次補齊 TL0 遮蔽與空值省略兩件事：

```ruby
  untrusted_attributes :bio_raw,
                       :bio_cooked,
                       :bio_excerpt,
                       :location,
                       :website,
                       :website_name,
                       :profile_background,
                       :card_background
```

同時把 spec/serializers/user_serializer_spec.rb:11 的清單補上 website_name，讓這個退化有回歸測試守住。

<details>
<summary>Suggestion（3）</summary>

#### F-002 include_website_name 少了問號，ActiveModel::Serializers 永遠不會呼叫它 — `app/serializers/user_serializer.rb:153`

面向 D API 慣例 · Suggestion

**問題**：ActiveModel::Serializers 0.8.3（Gemfile.lock:22）查的是 include_<attr>? 這個帶問號的方法名。這件事在本檔案內就有三處自證：user_serializer.rb:9、18、28 的 staff_attributes / private_attributes / untrusted_attributes 三個 helper 都是 define_method "include_#{attr}?"；同一個 diff 裡緊接著的 include_card_image_badge_id?（user_serializer.rb:161）也帶問號。全 repo 的 app/、lib/、plugins/ 底下 def include_xxx? 有 100 處，不帶問號的只有 4 處，其中 3 處（category_serializer.rb:47、topic_view_serializer.rb:218、invited_user_serializer.rb:48）是既有問題、不屬於本次變更。後果是這個守門條件是死碼：website_name 會無條件出現在每一份 user payload，使用者沒填網站時以 null 送出，而作者寫這段的用意顯然是要省略它。

**證據**：
- `app/serializers/user_serializer.rb:153`
- `app/serializers/user_serializer.rb:161`
- `app/serializers/user_serializer.rb:9`
- `app/serializers/user_serializer.rb:18`
- `app/serializers/user_serializer.rb:28`
- `Gemfile.lock:22`

**修復方向**：最小修法是補上問號改成 `def include_website_name?`。但更貼合本檔案慣例、也順帶解掉 F-001 的做法是完全刪掉這三行，改把 :website_name 放進 user_serializer.rb:105 的 untrusted_attributes——它產生的 include_website_name? 已經包含 `send(attr).present?`，語意與作者原本寫的完全一致，還多加上 TL0 遮蔽。

#### F-003 測試沒有覆蓋 untrusted 清單與註解宣稱要處理的子網域分支 — `spec/serializers/user_serializer_spec.rb:11`

面向 G 測試 · Suggestion

**問題**：作者正好改的就是這個 spec 檔，但漏了三塊：(1) 第 11 行的 untrusted_attributes 清單沒有加上 website_name，因此 F-001 那條退化完全沒有回歸測試擋著——這個 example 存在的目的就是防止有人新增欄位時忘記遮蔽，卻在新增欄位時沒被想起來。(2) user_serializer.rb:144-146 的 elsif 分支（註解寫的 www.example.com == forum.example.com，也就是「兩邊都有子網域、母網域相同」）是整個方法裡邏輯最多的一段，三個新增 example 分別覆蓋了「完全不同網域」「完全相同」「website 是 instance 的母網域」，唯獨沒有覆蓋它。(3) 沒有 example 驗證使用者沒填網站時 website_name 這個 key 應該不存在（F-002 的行為）。新增的三個 example 本身斷言品質是好的：比對的是實際回傳字串，不是 present 或 not_nil。

**證據**：
- `spec/serializers/user_serializer_spec.rb:11`
- `spec/serializers/user_serializer_spec.rb:75`
- `app/serializers/user_serializer.rb:144`
- `app/serializers/user_serializer.rb:153`

**修復方向**：在 spec/serializers/user_serializer_spec.rb:11 的清單補上 website_name；在「has a website name」context 內補一個 elsif 分支的 example，例如把 user.user_profile.website 設為 'http://www.example.com/user'、Discourse.stubs(:current_hostname).returns('forums.example.com')，斷言得到 'www.example.com/user'；再補一個空 website 的 example 斷言 `expect(json).not_to have_key(:website_name)`。

#### F-004 以「label 數相同 + 去掉第一段後相等」判斷同組織，在 com.br / co.uk 這類雙層公共後綴上會誤判 — `app/serializers/user_serializer.rb:144`

面向 C 安全 · Suggestion

**問題**：user_serializer.rb:144-146 的判斷是：兩個 host 的 label 數相同且大於 2，就把各自去掉第一段後的字串拿來比對，相等即視為同組織並顯示完整路徑。這個啟發式假設「倒數兩段 = 可註冊網域」，但雙層公共後綴（com.br、co.uk、com.au、com.tw、co.jp）打破這個假設。舉例：instance 架在 forum.com.br，任一使用者填 spam.com.br/promo，兩邊都是 3 段、去掉第一段後都是 'com.br'，於是被判定為同組織，回傳 'spam.com.br/promo'——一個毫無關係的網域拿到了本來只保留給站方自家網域的「顯示完整路徑」待遇，等於讓外部連結在 profile 上多曝光一整段路徑文字。這是純邏輯推導、不需要執行即可確認，且方向恰好與 F-001 相同：讓外站網址比預期顯示得更多。註：這個 repo 沒有 public_suffix 或同類 gem（Gemfile.lock 查無），所以無法直接改用可註冊網域比對。

**證據**：
- `app/serializers/user_serializer.rb:144`
- `app/serializers/user_serializer.rb:146`

**修復方向**：兩個方向擇一。保守做法是拿掉 label 數啟發式，只保留兩個確定安全的情境——host 完全相等，或 instance host 是 website host 的子網域（現有 else 分支的 ends_with? 檢查）——其餘一律只顯示 host；這會少掉 www.example.com vs forum.example.com 這個便利情境，但不會誤判。要保留該情境的話，需要引進 public_suffix gem，改以 PublicSuffix.domain(host) 取得可註冊網域再比對：

```ruby
  same_org = PublicSuffix.domain(website_host) == PublicSuffix.domain(discourse_host)
```

無論選哪一個，都請一併補上 F-003 提到的該分支測試，並加上 com.br 這類反例的 example。

</details>

<details>
<summary>Nit（2）</summary>

#### F-005 rescue modifier 吞掉所有 StandardError，含空白的網址從此完全不顯示 — `app/serializers/user_serializer.rb:138`

面向 A 風格 · Nit

**問題**：`URI(website.to_s).host rescue nil`（user_serializer.rb:138）的 rescue modifier 會攔下所有 StandardError，不只是 URI::InvalidURIError。實測（ruby -ruri）確認：URI("http://example.com/a b") 會丟 URI::InvalidURIError，於是 website_name 回傳 nil，user.hbs:66 的 {{#if model.website_name}} 為假，整個地球圖示與網站連結區塊都不顯示。變更前的前端寫法 website.split("/")[2] 對同一個字串會得到 "example.com" 並正常顯示。app/models/user_profile.rb 對 website 欄位沒有任何格式驗證，前端 preferences.hbs:158 也只是普通文字輸入框，所以含空白的值確實存得進來。影響很小（要填出這種網址才會遇到），但確實是這次變更帶來的行為退化。另外附帶確認：這裡 rescue 之後有 `return if website_host.nil?`，所以後面 141-149 行沒有包 rescue 的 URI(website.to_s) 呼叫不會再丟例外——這點不是問題。

**證據**：
- `app/serializers/user_serializer.rb:138`
- `app/assets/javascripts/discourse/templates/user/user.hbs:66`

**修復方向**：把 rescue 收斂到預期的例外，並讓解析結果只算一次（與 F-006 同一處修改）：

```ruby
  def website_name
    uri = begin
      URI(website.to_s)
    rescue URI::InvalidURIError
      nil
    end
    return if uri.nil? || uri.host.nil?
    ...
  end
```

若希望維持舊行為不退化，可在 rescue 分支回退成 website.to_s.split("/")[2]。

#### F-006 website_name 內重複解析與重複切分，單行巢狀三元運算子難讀 — `app/serializers/user_serializer.rb:138`

面向 B 簡潔 · Nit

**問題**：同一個 URI(website.to_s) 在一次呼叫中最多解析兩次（138 行取 host、143/146/149 行取 path），split('.') 最多重算六次（144 行三次、146 行兩次以上）。146 行是一個 133 字元、把「同組織判斷」與「回傳值選擇」壓在一起的巢狀三元運算子，接在一個同樣複合的 elsif 條件後面，需要來回讀好幾次才能確定哪個條件對應哪個結果。這裡不是要求重寫成別的演算法，而是這段的判斷規則本身（見 F-004）之後很可能還要調整，現在的寫法會讓下一次修改難以確認有沒有改對。

**證據**：
- `app/serializers/user_serializer.rb:138`
- `app/serializers/user_serializer.rb:143`
- `app/serializers/user_serializer.rb:144`
- `app/serializers/user_serializer.rb:146`
- `app/serializers/user_serializer.rb:149`

**修復方向**：把解析結果與 label 陣列各存一次區域變數，並把「是否同組織」抽成一個具名述詞，讓三個分支各自只剩一行回傳：

```ruby
  def website_name
    uri = parsed_website
    return if uri.nil? || uri.host.nil?
    same_domain_family?(uri.host) ? "#{uri.host}#{uri.path}" : uri.host
  end
```

同組織判斷的細節（完全相等、母網域、同母網域的兄弟子網域）收進 same_domain_family? 內，F-004 的修正也落在同一個地方。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 Discourse.current_hostname 有沒有可能帶上 port（例如 localhost:3000）？若會，所有比對都會落到「不同網域」分支，這個功能在非標準 port 的部署上等於沒作用。

面向 C 安全

**背景**：lib/discourse.rb:178-184 的 current_hostname 在 SiteSetting.force_hostname 為空時回傳 RailsMultisite::ConnectionManagement.current_hostname。lib/pretty_text.rb:131 與 app/views/common/_discourse_javascript.html.erb:37 使用同一個值時都特地接了 .replace(/:[\d]*$/,"") 把 port 去掉，這暗示該值在某些情境下確實可能含 port。而 URI(...).host 永遠不含 port，兩邊格式不對等。環境沒有安裝 gem、也沒有網路，無法讀 rails_multisite 原始碼或實際跑起來確認，因此不下判斷。

**如何確認**：在一個跑在非 80/443 port 的 dev instance 上印出 Discourse.current_hostname；或直接讀 rails_multisite gem 的 ConnectionManagement.current_hostname 實作。若確認會帶 port，修法是比照 pretty_text.rb 先 sub(/:\d+\z/, '') 正規化後再比對。

#### Q-002 「website 是 instance 的子網域」這個反向情境（website 填 blog.example.com、instance 架在 example.com）刻意只顯示 host 不顯示路徑，還是漏掉的？

面向 C 安全

**背景**：此情境下兩個 host 的 label 數不同（3 vs 2），會落到 user_serializer.rb:147-149 的 else，而該行檢查的是 discourse_host.ends_with?("." + website_host)，也就是「instance 是 website 的子網域」，方向相反，結果為 false，只回傳 host。正向情境（website example.com、instance forums.example.com）則有測試覆蓋且會顯示完整路徑。程式碼與 commit message「if website domain is same as instance domain」都沒有說明這個不對稱是不是刻意的。

**如何確認**：作者說明產品意圖：「同網域家族」是否應該雙向對稱。若是，else 分支要同時檢查 website_host.ends_with?("." + discourse_host)，並補上對應測試。

</details>
