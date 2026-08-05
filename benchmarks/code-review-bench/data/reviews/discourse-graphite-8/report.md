## 審查結論：Request Changes

> Critical 2 · Suggestion 5 · Nit 4 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ✅ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ✅ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

- **A 風格**（未通過）：見 F-007（無關排版改動混入）、F-008（findMembers 早退不再回傳 promise、destroy 的 `{ return };`、addMembers 缺分號）。命名與函式長度沒有問題；admin-group.js.es6 的 currentPage / totalPages / next / previous 都短且職責單一。
- **C 安全**（未通過）：見 F-002：/groups/:name/members.json 的 limit 與 offset 未經驗證直接進 SQL，而該 endpoint 匿名可達。其餘：沒有字串拼接 SQL（都是 AR 條件或既有的 parameterised where），沒有 hardcoded credential，沒有 eval / 系統呼叫。管理端 add_members / remove_member 在 AdminConstraint 與 Admin::AdminController 之下，authorization 分層正確。
- **D API 慣例**（未通過）：見 F-011（PUT 用在非冪等的新增操作，且公開 endpoint 回應格式直接置換）。路徑本身符合團隊慣例：URL 路徑 admin/groups/:group_id/members 全小寫、無底線、無 PII。config/routes.rb:49-50 寫在 resources :groups 區塊內而非 member/collection 內，產生的是巢狀路由 admin/groups/:group_id/members，與 controller 的 params.require(:group_id) 及前端 app/assets/javascripts/discourse/models/group.js:42,53 一致，這點已核對過。
- **F 資料取用與資料庫**（未通過）：見 F-001（唯一索引衝突 + 批次無 transaction）與 F-003（成員移除繞過 model，primary_group_id 不再清空）。相關的併發問題已想過：add_members 的迴圈逐筆寫入沒有交易保護，兩個 admin 同時新增同一人時第二個請求會撞上 index_group_users_on_group_id_and_user_id，這是 F-001 的同一條路徑。
- **G 測試**（未通過）：見 F-006。正面部分：新增的 spec 有實際斷言行為（groups.count / group.users.count / group.name），不是只檢查 200，也沒有「mock 回傳 X 再斷言 X」的空轉測試。問題在覆蓋範圍沒有跟著程式碼走。
- **H 非 Python 檔**（未通過）：見 F-004（分頁頁數算錯）、F-005（公開頁面被截斷且無分頁 UI）、F-009（TODO 隨程式碼上線）、F-010（載入與錯誤狀態缺失）。已排除的兩個疑慮：(1) app/assets/javascripts/admin/templates/group.hbs:21 的 `{{each member in members itemView="group-member"}}` 不是漏寫 `{{/each}}` —— 非 block 形式是 Ember 1.9.0 文件明載的用法（vendor/assets/javascripts/development/ember.js:8326-8342），repo 內 discourse/templates/share.hbs:13 與 components/topic-list.hbs:19 已在用；(2) group_member.hbs 的 `{{#unless automatic}}` 會正確解析到 group 而非 member —— EachView.createChildView 只塞 `view._keywords[keyword] = content`（ember.js:8217-8234），不切換 context，且 content 是 Discourse.User、isController 為 false，所以 automatic 仍走父層 controller。members 在 findMembers 回來前是 undefined 也不會爆，_assertArrayLike 被 `if (content)` 包住（ember.js:40379-40389）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：功能變更本身內聚，但同一個 commit 夾了數處與本議題無關的排版改動：app/controllers/admin/groups_controller.rb 的 ilike → ILIKE 與空行、兩個 controller 的 private/protected 方法縮排、app/assets/javascripts/discourse/templates/user-selector-autocomplete.raw.hbs 全檔重排、app/assets/javascripts/discourse/templates/components/admin-group-selector.hbs 只刪空行。這些讓 diff 難以逐行對照真正的行為變更，建議另開一個 commit。詳見 F-007。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | 已執行 `ruff check --output-format json .`，exit code 0，輸出 `[]`，並印出 warning: No Python files found under the given path(s)。這個 repo 沒有 Python 檔，diff 也沒有，所以 ruff 對本次變更的實際覆蓋率是零 —— exit 0 不代表這份 diff 通過了任何靜態檢查。 · total 0、in_diff 0 |
| oxlint | 略過 | 未安裝（preflight 確認不在 PATH）。本次 diff 有 3 個 JavaScript / ES6 檔與 4 個 Handlebars 樣板，是變更量最大的部分，完全沒有 lint 覆蓋。 |
| trivy | 略過 | 未安裝。相依套件漏洞、設定錯誤與 committed secret 掃描皆未執行。 |
| opengrep | 略過 | 未安裝，且預設規則目錄 semgrep-rules（HOME 底下）不存在（兩個條件都缺）。SAST 未執行。 |
| ty | 略過 | 未安裝。本次 diff 也沒有 Python 檔，即使安裝也無事可做。 |
| codegraph | 略過 | 未安裝，Phase 0 的符號索引沒有建立。E 與 I 的呼叫端盤點全部改以 grep 完成，已在對應 dimension note 列出實際搜尋到的結果。 |
| ncr-fresh-eyes（subagent） | 略過 | 這個執行環境沒有派發 subagent 的能力（工具清單內沒有 Agent / Task，ToolSearch 也搜不到），所以 Phase 3 的 fresh eyes 完全沒有跑。依 SKILL.md 的規定不由主 agent 自行模擬 —— 讀完 review-dimensions.md 之後再假裝一次「未被這份 skill 形塑的目光」是自欺。實務影響：這份報告的所有發現都出自九個 dimension 的檢查表，凡是檢查表沒有指名的角度都可能被漏掉。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派發 subagent。Phase 4 step 3 的獨立品質檢查沒有執行，report.json 只通過了 report_model.py 的機械驗證（結論與 findings 一致、每個 finding 有 fix、Critical security finding 有三段式 payload、九個 dimension 都有 verdict）。四條發佈規則裡「自我完備」與「對事不對人」這兩條是我自行複核的，沒有第二雙眼睛。 |
| rubocop / eslint（Ruby、JavaScript linter） | 略過 | rubocop 未安裝；環境雖有 eslint，但這個 repo 沒有 eslint 設定檔（2014 年的 Discourse 用 jshint，也未安裝），硬跑只會產生與專案慣例無關的雜訊。本次 diff 的 Ruby 與 JavaScript 沒有任何 linter 覆蓋。替代做法：對 4 個變更的 .rb 檔跑 `ruby -c`，全部 Syntax OK；分頁算式用 node 逐格驗算（見 F-004）。這兩項只證明語法可解析與單一算式的輸出，不等於 lint。 |

### Critical

#### F-001 add_members 重複加入既有成員會撞唯一索引直接 500，且批次已部分寫入 — `app/controllers/admin/groups_controller.rb:71-75`

面向 F 資料取用與資料庫 · Critical

**問題**：group_users 上有 UNIQUE index（index_group_users_on_group_id_and_user_id，見 app/models/group_user.rb 的 schema 註解），而 Group#add 就是 `self.users.push(user)`，has_many :through 沒有 uniq/distinct，也沒有任何 find_or_create 或存在性檢查。已加入的成員再送一次就是 INSERT 撞唯一索引 → ActiveRecord::RecordNotUnique，ApplicationController 的 rescue_from Exception 只記錄後 re-raise（application_controller.rb:73），使用者拿到的是 500 而不是可讀的錯誤。

這條路徑很容易踩到，因為前端不會清空輸入框：admin-group.js.es6:62 留著 `// TODO: should clear the input`，按第二次 Add 就會把同一批 usernames 再送一次。使用者也沒有任何提示知道某個名字已經在群組裡 —— user-selector 是通用的 username autocomplete，不會排除既有成員。

更麻煩的是迴圈逐筆 push、沒有 transaction：送 "alice,bob,carol" 時若 bob 已在群組，alice 已寫入、bob 炸掉、carol 沒被處理，request 以 500 收場，群組落在一個沒人設計過的中間狀態。

反證已找過：舊路徑不可能發生這件事 —— Group#usernames=（app/models/group.rb:248-249）先算 `additions = expected - current`，重複的名字在到達 INSERT 之前就被差集濾掉了。新路徑沒有任何等價的防護，controller、model、route 三處 grep 都沒有 rescue 或 transaction。這個 repo 自己也知道要防：Group.user_trust_level_change!（app/models/group.rb:230-231）在建立 GroupUser 前明寫 `unless GroupUser.where(group_id: id, user_id: user_id).exists?`。

**證據**：
- `app/controllers/admin/groups_controller.rb:71-75`
- `app/models/group.rb:272-274`
- `app/models/group_user.rb:18`
- `app/assets/javascripts/admin/controllers/admin-group.js.es6:62-64`
- `app/controllers/application_controller.rb:62-74`

**修復方向**：在迴圈內加存在性判斷並把整批包進交易，讓它變成冪等且 all-or-nothing：

```ruby
def add_members
  group = Group.find(params.require(:group_id).to_i)
  return can_not_modify_automatic if group.automatic

  usernames = params.require(:usernames).split(",")
  users = User.where(username_lower: usernames.map { |u| u.downcase.strip })

  Group.transaction do
    users.each do |user|
      next if GroupUser.where(group_id: group.id, user_id: user.id).exists?
      group.add(user)
    end
  end

  render json: success_json
end
```

另外把 admin-group.js.es6:62 的 TODO 補完（成功後 `this.set("usernames", null)`），讓連按兩次不會重送同一批（見 F-009）。

#### F-002 公開的 /groups/:name/members.json 直接把未驗證的 limit / offset 餵進 SQL — `app/controllers/groups_controller.rb:22-26`

面向 C 安全 · Critical

**問題**：`limit = (params[:limit] || 50).to_i` 與 `offset = params[:offset].to_i` 沒有任何上界、下界或型別檢查就進了 `.limit(limit).offset(offset)`。兩個方向都有問題：

**上界缺失。** GroupsController 繼承 ApplicationController 且沒有 ensure_logged_in，find_group 唯一的守門是 `guardian.ensure_can_see!`，而 `can_see_group?` 只要 `group.visible?` 就放行（lib/guardian.rb:121），visible 的 DB 預設是 true。trust_level_0 是 automatic group，其成員定義是 `SELECT u.id FROM users u`（app/models/group.rb:126）—— 站上每一個使用者。所以任何匿名訪客一個請求就能把整站使用者清單（username、name、avatar_template、last_seen_at，見 GroupUserSerializer）撈完，同時讓 app 去序列化任意大的結果集。這條 action 沒有掛任何 RateLimiter。

**下界缺失。** 負數會原樣傳進 PostgreSQL：`OFFSET -1` 與 `LIMIT -1` 都會讓 PG 直接報錯（OFFSET must not be negative），變成 ActiveRecord::StatementInvalid → 500。前端有夾（app/assets/javascripts/discourse/models/group.js:23 的 Math.max(offset, 0)），但那是 client，不是 trust boundary。

反證已找過：舊版只對 automatic group 套 limit/offset，且預設 200；本次把它擴到所有 group 且仍然沒有上限，等於把原本只影響 automatic group 的問題推廣到全部，同時新增了非 automatic group 的負數 offset 路徑。整份 GroupsController 與 ApplicationController 都 grep 過，沒有 rate limit、沒有 max page size 常數。

風險處置選 Mitigate 而非 Avoid：分頁本身是對的，需要的是把輸入夾住。

**證據**：
- `app/controllers/groups_controller.rb:22-26`
- `lib/guardian.rb:120-122`
- `app/models/group.rb:125-127`
- `app/serializers/group_user_serializer.rb:1-3`
- `db/migrate/20140422195623_add_visibile_to_groups.rb:3`

**POC**：

```
上界：`curl -s 'https://forum.example.com/groups/trust_level_0/members.json?limit=1000000' | head -c 400` —— 無需任何 cookie 或 API key，回傳站上全部使用者的 username / name / avatar_template / last_seen_at。下界：`curl -sS -w '\n%{http_code}\n' 'https://forum.example.com/groups/trust_level_0/members.json?offset=-1' | tail -1` 應得到 500（PostgreSQL: OFFSET must not be negative）。
```

**影響範圍**：匿名的全站使用者名單匯出：一個請求即可取得所有帳號的 username、顯示名稱、頭像 URL 與最後上線時間，可直接餵給釣魚名單、憑證填充的帳號字典，或用 last_seen_at 做活躍度側寫。同一參數也是資源耗盡的槓桿 —— 單一請求即可讓 DB 與 app 序列化整張 users 表，重複發送即為低成本 DoS。負數 offset 則是穩定可觸發的 500。本次變更不觸及 PHI（見 meta.phi_trigger），所以沒有病歷面的成本。

**風險處置**：Mitigate（降低）

**修復參考**：app/controllers/groups_controller.rb:22-23 加上 clamp，見 fix 欄位的程式碼

**修復方向**：在 controller 夾住兩個參數，並用 counter cache 取代整表 count：

```ruby
MAX_MEMBERS_PER_PAGE = 100

def members
  group = find_group(:group_id)

  limit  = (params[:limit] || 50).to_i.clamp(1, MAX_MEMBERS_PER_PAGE)
  offset = [params[:offset].to_i, 0].max

  members = group.users.order(:username_lower).limit(limit).offset(offset)

  render json: {
    members: serialize_data(members, GroupUserSerializer),
    meta: { total: group.users.count, limit: limit, offset: offset }
  }
end
```

夾完之後前端 app/assets/javascripts/discourse/models/group.js:23 的 Math.max/Math.min 就只是 UX 上的貼心，不再是唯一防線。若要進一步降低整站使用者列舉的風險，可考慮對 automatic group 的 members endpoint 要求登入。

<details>
<summary>Suggestion（5）</summary>

#### F-003 remove_member 繞過 model，被移除的成員仍保留該群組為 primary_group — `app/controllers/admin/groups_controller.rb:90`

面向 F 資料取用與資料庫 · Suggestion

**問題**：舊的移除路徑是 `group.usernames = ...` → after_save :destroy_deletions，而 destroy_deletions 除了 `gu.destroy` 之外還做了一件事：`User.where('id = ? AND primary_group_id = ?', gu.user_id, gu.group_id).update_all 'primary_group_id = NULL'`（app/models/group.rb:291）。新的 remove_member 只呼叫 `group.users.delete(user_id)`，沒有這段清理。

後果是可觀察的：使用者被移出群組後，users.primary_group_id 仍指著那個群組，而 PostSerializer#primary_group_name（app/serializers/post_serializer.rb:97-100）只檢查 `object.user.primary_group_id` 存在、再從 TopicView 撈群組名 —— 群組本身還在，所以那個人的貼文會繼續掛著他已經不屬於的群組 flair。這是資料一致性的回歸，不是新功能的取捨。

同時，controller 直接對 association 動手，也跳過了 model 已經提供的 Group#remove（app/models/group.rb:276）—— 對照組是同一個 controller 的 add_members 走 Group#add，兩邊不對稱。順帶一提 `group.users.delete(user_id)` 傳入非成員的 id 時，AR 會先 find 該筆而丟出 RecordNotFound（→ 404），不會落到下面的 `render_json_error` 分支，錯誤語意也對不上。

反證已找過：Group#remove 本身也沒有清 primary_group_id，GroupUser 沒有任何 destroy callback（app/models/group_user.rb 只有兩行 belongs_to），app/ 與 lib/ 下 grep primary_group_id 只有 admin/users_controller.rb:135 的設定端與 group.rb:291 這一處清理端 —— 也就是說本次之後，清理 primary_group_id 的程式碼在 app 內已經沒有任何可達路徑。至於 user_count counter cache 是否也一併失準，見 Q-001。

**證據**：
- `app/controllers/admin/groups_controller.rb:90`
- `app/models/group.rb:276-278`
- `app/models/group.rb:287-295`
- `app/serializers/post_serializer.rb:97-100`
- `app/models/group_user.rb:2`

**修復方向**：讓 controller 走 model，並把 primary_group_id 的清理搬進 Group#remove，使它成為唯一的移除入口：

```ruby
# app/models/group.rb
def remove(user)
  group_users.where(user: user).each(&:destroy)
  User.where(id: user.id, primary_group_id: id).update_all(primary_group_id: nil)
end

# app/controllers/admin/groups_controller.rb
def remove_member
  group = Group.find(params.require(:group_id).to_i)
  return can_not_modify_automatic if group.automatic

  user = User.find(params.require(:user_id).to_i)
  group.remove(user)
  render json: success_json
end
```

改完之後 Group#usernames= 與 after_save :destroy_deletions 就真的沒有呼叫端了（只剩 spec/models/user_spec.rb 與 spec/models/group_spec.rb），可以在同一個 MR 或後續一併移除。

#### F-004 totalPages 用 floor + 1，成員數剛好是 limit 整數倍時會多出一頁空白 — `app/assets/javascripts/admin/controllers/admin-group.js.es6:11-14`

面向 H 非 Python 檔 · Suggestion

**問題**：`Math.floor(user_count / limit) + 1` 在 user_count 是 limit 整數倍時多算一頁。用 node 逐格驗算（limit = 50）：

| user_count | offset | currentPage/totalPages |
|---|---|---|
| 49 | 0 | 1/1 ✅ |
| 50 | 0 | 1/2 ❌ 應為 1/1 |
| 50 | 50 | 2/2（整頁空白） |
| 100 | 50 | 2/3 ❌ 應為 2/2 |

實際走一遍：50 人的群組首頁顯示「1/2」，showingLast 為 false 所以 next 是可按的；按下去 offset 變成 min(0+50, 50) = 50，findMembers 的夾值 `Math.min(user_count, Math.max(offset, 0))` = 50 原樣送出，server 回一個空的 members 陣列與 meta.offset = 50，畫面變成「2/2」加一片空白。使用者沒有做錯任何事。

反證已找過：夾值不在別處補償 —— group.js:23 只防負數與超過 user_count，剛好等於 user_count 的 offset 會通過；server 端 app/controllers/groups_controller.rb:26 對 offset = total 也是合法查詢，回空集合。整條路徑上沒有第二個地方修正這個 off-by-one。

**證據**：
- `app/assets/javascripts/admin/controllers/admin-group.js.es6:11-14`
- `app/assets/javascripts/admin/controllers/admin-group.js.es6:6-9`
- `app/assets/javascripts/admin/templates/group.hbs:16-18`

**修復方向**：改用 ceil，並讓 user_count 為 0 的情況自然落在同一條算式上：

```javascript
totalPages: function() {
  return Math.ceil(this.get("user_count") / this.get("limit"));
}.property("limit", "user_count"),
```

ceil(0/50) = 0、ceil(50/50) = 1、ceil(51/50) = 2，三個邊界都對，原本的 `if (user_count == 0) return 0` 可以一起拿掉。currentPage 的 `Math.floor(offset / limit) + 1` 本身沒問題，維持即可。

#### F-005 公開群組頁的成員清單被截到 50 筆，但那個畫面沒有任何分頁 UI — `app/controllers/groups_controller.rb:22`

面向 H 非 Python 檔 · Suggestion

**問題**：分頁 UI（next / previous、currentPage/totalPages）只加在 admin 的 app/assets/javascripts/admin/templates/group.hbs:16-18。公開的 /groups/:name/members 走的是 discourse/routes/group-members.js.es6 與 discourse/templates/group/members.hbs，那個樣板只有一張表格，沒有翻頁控制、沒有 load more、也沒有 infinite scroll。

但 findMembers 現在一律帶 `limit: 50`（group.js:10 的 model 預設）送出，server 端也一律套用（groups_controller.rb:22，舊版只對 automatic group 套）。也就是說一個 300 人的公開群組，變更前這個頁面顯示 300 人，變更後顯示前 50 人，而且畫面上沒有任何線索告訴訪客還有 250 人，也沒有辦法看到他們。

反證已找過：group/members.hbs 全檔看過，沒有分頁元素；discourse/controllers/group/ 底下只有 index.js.es6 與 post.js.es6，沒有 members controller 提供 next/previous action；ShowFooter mixin 也不含載入更多的行為。這條截斷路徑上沒有補救。

**證據**：
- `app/controllers/groups_controller.rb:22`
- `app/assets/javascripts/discourse/models/group.js:10`
- `app/assets/javascripts/discourse/routes/group-members.js.es6:8-12`
- `app/assets/javascripts/discourse/templates/group/members.hbs:1-22`

**修復方向**：兩條路擇一：

1. 把 admin 的分頁搬到公開頁 —— 新增 discourse/controllers/group/members.js.es6，把 admin-group.js.es6:6-17 的 currentPage / totalPages / showingFirst / showingLast 與 next / previous action 抽成共用的 mixin，樣板加上同一組控制項。
2. 若公開頁短期內不打算做分頁，讓它明確要一個大的 limit（例如在 route 內 `model.setProperties({ limit: 200 })` 再 findMembers），並在 UI 上標示「顯示前 N 位，共 M 位」，至少不要無聲截斷。

選 1 比較好，因為 F-002 的修法會替 limit 加上上界，屆時方案 2 的大 limit 會被夾掉。

#### F-006 測試沒有跟著程式碼走：pending spec 仍描述舊格式，新行為與被刪掉的既有覆蓋都沒補上 — `spec/controllers/groups_controller_spec.rb:72-85`

面向 G 測試 · Suggestion

**問題**：四件事：

**1. pending 的分頁測試正是這個 MR 在修的東西，卻沒有被啟用，而且現在寫錯了。** groups_controller_spec.rb:72 的 `pending "ensures that membership can be paginated"` 上面掛著 `# Pending until we fix group truncation`，而本次 diff 移除的就是那段 truncation 的 TODO 註解。它應該被解除 pending。但它的斷言 `JSON.parse(response.body).map { |m| m['username'] }` 假設 response 是 array，新格式是 `{members:, meta:}`，直接解除 pending 會失敗。留著一個描述舊契約的 pending 測試，比沒有測試更容易誤導下一個人。

**2. 新的回應格式完全沒有測試。** 沒有任何 spec 斷言 meta.total / meta.limit / meta.offset 的值，也沒有斷言 offset 生效後回的是第二頁的人。這正是前端 group.js:31-36 依賴的三個欄位。

**3. 被刪掉的覆蓋沒有等價替代。** 舊的 `succeeds silently when adding non-existent users` 驗證了不存在的 username 會被安靜略過，而這個行為在新的 add_members（admin/groups_controller.rb:72 的 `if user = User.find_by_username(username)`）原封不動保留著，卻沒有測試守著了。F-001 描述的重複加入路徑也沒有測試。

**4. remove_member 的兩個測試沒有斷言 user_count，且 HTTP 動詞用錯。** :115 用 `xhr :put, :remove_member`，但 config/routes.rb:49 註冊的是 delete；同一個 context 的另一個測試（:125）用的是正確的 `xhr :delete`。controller spec 的路徑產生不校驗動詞，所以它會過，但它記錄下來的是一份錯誤的 API 契約。

**證據**：
- `spec/controllers/groups_controller_spec.rb:72-85`
- `spec/controllers/admin/groups_controller_spec.rb:112-129`
- `spec/controllers/admin/groups_controller_spec.rb:91-108`
- `spec/controllers/admin/groups_controller_spec.rb:115`

**修復方向**：在 spec/controllers/groups_controller_spec.rb 把 pending 換成實際的分頁測試，並斷言新格式：

```ruby
it "paginates membership" do
  5.times { group.add(Fabricate(:user)) }
  usernames = group.users.order(:username_lower).pluck(:username)

  xhr :get, :members, group_id: group.name, limit: 3
  json = JSON.parse(response.body)
  json["members"].map { |m| m["username"] }.should eq(usernames[0..2])
  json["meta"].should eq({ "total" => 5, "limit" => 3, "offset" => 0 })

  xhr :get, :members, group_id: group.name, limit: 3, offset: 3
  JSON.parse(response.body)["members"].map { |m| m["username"] }.should eq(usernames[3..4])
end
```

在 admin spec 補三個案例：加入不存在的 username 會被略過且不影響其他人、加入既有成員（F-001 的情境）、remove_member 之後 `group.reload.user_count` 應為 0（同時守住 F-003 與 Q-001）。並把 :115 的 `xhr :put` 改成 `xhr :delete`。

#### F-011 PUT 用在非冪等的成員新增，且公開 endpoint 的回應格式直接置換而非並存 — `config/routes.rb:50`

面向 D API 慣例 · Suggestion

**問題**：**動詞與冪等語意不符。** `put "members" => "groups#add_members"` 對同一個 URI 送兩次相同的 body，結果不同 —— 第一次成功、第二次撞唯一索引 500（F-001）。PUT 在 RFC 9110 下代表「用這份表述取代目標資源」，重送必須無害。這個 action 語意上是「把這些人加進集合」，該用 POST；若真的要 PUT 的語意，body 應該是完整的成員名單、由 server 算差集（也就是舊的 usernames= 做的事）。修掉 F-001 的重複判斷後 500 會消失，但「PUT 表示取代、實際做的是附加」這個誤導仍在。

**回應格式直接置換。** /groups/:name/members.json 從裸 array 變成 `{members: [...], meta: {...}}`。這是公開 endpoint（F-002 已確認匿名可達），也就是 Discourse 站台的第三方腳本、機器人與整合服務可能已經在讀的形狀。同一個 MR 內的呼叫端只有 group.js 一處、已經改好；問題是 repo 外的讀者無從得知。安全的順序是 expand → migrate → contract：新形狀先與舊形狀並存（例如以 `?paginated=true` 或新路徑提供），舊讀者遷移完再移除。

反證已找過：repo 內 grep `/members` 與 findMembers 的呼叫端全部盤過（見 dimension I 的 note），沒有遺漏的內部消費者；所以這條純粹是對外相容性，不是內部壞掉。也因此嚴重度停在 Suggestion 而非 Critical。

**證據**：
- `config/routes.rb:50`
- `app/controllers/admin/groups_controller.rb:65-82`
- `app/controllers/groups_controller.rb:28-35`
- `app/assets/javascripts/discourse/models/group.js:53-55`

**修復方向**：動詞改成 POST：

```ruby
# config/routes.rb
post   "members" => "groups#add_members"
delete "members" => "groups#remove_member"
```

並同步 app/assets/javascripts/discourse/models/group.js:54 的 `type: "PUT"` 與 spec 的 `xhr :put, :add_members`。

回應格式若這個專案的政策允許直接改（Discourse 當時對未公告的 endpoint 常這麼做），至少在 MR 描述與 release note 標明是 breaking change；若要保守，讓 members 在沒有 limit/offset 參數時維持回傳 array，有參數時才回 `{members:, meta:}`。

</details>

<details>
<summary>Nit（4）</summary>

#### F-007 與本議題無關的排版改動混進同一個 commit — `app/controllers/admin/groups_controller.rb:6`

面向 A 風格 · Nit

**問題**：diff 裡有五處與成員管理無關的純格式改動：index 的 `ilike` → `ILIKE` 加空行、Admin::GroupsController 的 protected 區塊改成縮排、GroupsController 的 private 區塊同樣改成縮排、user-selector-autocomplete.raw.hbs 整份 `<a>` 重排、admin-group-selector.hbs 只刪掉頭尾空行。

這些改動本身無害，但它們讓 review 這份 diff 的人必須逐行分辨哪些是行為變更、哪些不是 —— 例如 groups_controller.rb 的 find_group 在 diff 上看起來被整段改寫，實際上只有縮排變了。commit message 是 "FIX: proper handling of group memberships"，日後 git blame 到這幾行時也對不上。

**證據**：
- `app/controllers/admin/groups_controller.rb:6`
- `app/controllers/admin/groups_controller.rb:99-102`
- `app/controllers/groups_controller.rb:38-45`
- `app/assets/javascripts/discourse/templates/user-selector-autocomplete.raw.hbs:1-24`
- `app/assets/javascripts/discourse/templates/components/admin-group-selector.hbs:1`

**修復方向**：把排版改動抽成獨立 commit（例如 "style: indent private/protected sections in groups controllers"），本 commit 只留行為變更。若專案要求 squash，至少在 MR 描述裡列出哪幾個檔案是純格式。

#### F-008 findMembers 早退不再回傳 promise，另有兩處分號寫法會誤導讀者 — `app/assets/javascripts/discourse/models/group.js:21`

面向 A 風格 · Nit

**問題**：**回傳型別不一致。** findMembers 的正常路徑回傳 Discourse.ajax 的 promise，name 為空時 `return ;` 回傳 undefined；舊版這裡是 `Ember.RSVP.resolve([])`，型別是一致的。現有 5 個呼叫端都沒有對回傳值 `.then`（已逐一確認），所以今天不會炸，但一個名字叫 findMembers、有時回 promise 有時回 undefined 的函式，下一個人接手時很容易寫出 `findMembers().then(...)`。

**兩處分號。** `if (!this.get('id')) { return };`（:82）的分號落在 block 後面，是一個空語句，讀起來像 `return;` 但其實不是；addMembers 的 `})` 後面（:59）漏了逗號前的分號，靠 ASI 補。都不會出錯，但都是讀者要停下來確認一次的寫法。

**證據**：
- `app/assets/javascripts/discourse/models/group.js:21`
- `app/assets/javascripts/discourse/models/group.js:82`
- `app/assets/javascripts/discourse/models/group.js:59-60`

**修復方向**：```javascript
findMembers: function() {
  if (Em.isEmpty(this.get('name'))) { return Ember.RSVP.resolve(); }
  // ...
},

destroy: function() {
  if (!this.get('id')) { return; }
  // ...
}
```

addMembers 的 `.then(...)` 後補上分號。

#### F-009 `// TODO: should clear the input` 隨程式碼上線，使用者按第二次 Add 會重送同一批人 — `app/assets/javascripts/admin/controllers/admin-group.js.es6:61-65`

面向 H 非 Python 檔 · Nit

**問題**：addMembers action 送出後不清空 `usernames`，user-selector 的輸入內容留在畫面上。除了 UX 上看不出「送出成功了嗎」之外，這正是 F-001 那條 500 路徑最容易被踩到的方式 —— 按兩次就是把同一批 usernames 送兩次。

另外 addMembers 沒有等 promise（`this.get("model").addMembers(...)` 沒有回傳也沒有 then），所以按鈕不會 disable、失敗也不會有任何提示。

**證據**：
- `app/assets/javascripts/admin/controllers/admin-group.js.es6:61-65`
- `app/assets/javascripts/admin/templates/group.hbs:27-28`

**修復方向**：把 TODO 補完，順帶接上結果：

```javascript
addMembers: function() {
  var self = this, usernames = this.get("usernames");
  if (Em.isEmpty(usernames)) { return; }
  return this.get("model").addMembers(usernames).then(function() {
    self.set("usernames", null);
  }, function(e) {
    bootbox.alert($.parseJSON(e.responseText).errors);
  });
}
```

#### F-010 findMembers 改成 setupController 內的 fire-and-forget，載入中與失敗狀態都消失了 — `app/assets/javascripts/admin/routes/admin_group_route.js:11-14`

面向 H 非 Python 檔 · Nit

**問題**：兩個 route 原本都在 afterModel 內 `return model.findMembers().then(...)`，Ember 會等這個 promise resolve 才完成 transition，期間顯示 loading route，失敗則進 error route。改成在 setupController 內裸呼叫 `model.findMembers()` 之後，回傳值被丟掉：畫面會先渲染成員數 0、清單空白，資料回來再跳一次；請求失敗時 promise rejection 沒有人接，畫面就永遠停在空清單，使用者無從分辨「這個群組沒有人」與「載入失敗」。

這是刻意的取捨（換取 transition 不被 API 擋住）還是疏漏，從 diff 看不出來；但無論哪一種，錯誤狀態都需要有人接。

**證據**：
- `app/assets/javascripts/admin/routes/admin_group_route.js:11-14`
- `app/assets/javascripts/discourse/routes/group-members.js.es6:8-12`
- `app/assets/javascripts/admin/templates/group.hbs:19-22`

**修復方向**：保留非阻塞的做法，但把狀態顯式化：在 controller 加 `loading` 旗標，findMembers 前後切換，並接上 rejection；樣板用 `{{#if loading}}` 顯示 spinner、`{{#if loadFailed}}` 顯示重試。或是回到 afterModel 阻塞 transition，讓 Ember 內建的 loading / error route 接手。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 `group.users.delete(user_id)`（app/controllers/admin/groups_controller.rb:90）之後，groups.user_count 這個 counter cache 還準嗎？

面向 F 資料取用與資料庫

**背景**：GroupUser 的 `belongs_to :group, counter_cache: "user_count"`（app/models/group_user.rb:2）是靠 AR 的 create / destroy callback 維護的。Rails 4.1.8（Gemfile.lock:30）在 has_many :through 上呼叫 #delete、且該 association 沒有宣告 :dependent 時，慣例上會以 delete_all 清掉 join record，那條路徑不跑 callback —— 若成立，每次從 admin 移除一位成員，user_count 就會多算一個，而它同時是 BasicGroupSerializer 的輸出（app/serializers/basic_group_serializer.rb:2）、admin 清單的數字、以及本次新增的分頁總頁數來源（app/assets/javascripts/discourse/models/group.js:32）。

這個 repo 本身有兩處旁證：Group.refresh_automatic_group! 在用 `GroupUser.where(id: ids).delete_all` 之後特地補一句 `Group.reset_counters(group.id, :group_users)` 並註解「we want to ensure consistency」（app/models/group.rb:111、141）；而舊的移除路徑走 destroy_deletions 的 `gu.destroy`（app/models/group.rb:289），callback 會正常觸發。

沒有列為 finding，是因為結論取決於 Rails 4.1.8 的 association 內部行為，而這個環境沒有安裝 gem、也沒有網路，我無法把 activerecord 4.1.8 的 delete_records 實際讀出來或跑起來。F-003 只記錄我能從這份程式碼直接證實的部分（primary_group_id 不再被清空）。

**如何確認**：在裝好 gem 的環境跑一個 spec：建一個群組、加兩個人、打 `xhr :delete, :remove_member`，然後斷言 `group.reload.user_count == 1`。或直接看 `activerecord-4.1.8/lib/active_record/associations/has_many_through_association.rb` 的 delete_records，確認 method 為 nil 時走的是 `scope.delete_all` 還是 `scope.destroy_all`。這個 spec 無論結果如何都值得補進 F-006 列的測試清單。

#### Q-002 `.groups, .badges { .form-horizontal { ... } }` 這段搬家，會不會讓 badges 以外的頁面掉樣式？

面向 H 非 Python 檔

**背景**：diff 把 label 粗體、`& > div` 上邊距、input/textarea/select 寬度 350px、checkbox 寬度 20px 這四條規則從一個既有的 .form-horizontal 區塊搬到新的 `.groups, .badges` 之下。git 的 hunk header 標成 `section.details`，看起來像是從一個共用的容器裡被抽走。

實際讀檔後確認 hunk header 是誤導：那個區塊的父選擇器是 `.badges`（app/assets/stylesheets/common/admin/admin_base.scss:375、400），不是 section.details。所以搬家後 .badges 仍然吃得到同一組規則，只是多了 .groups。grep 全 repo 的 `form-horizontal` 使用者（badges-show.hbs、group.hbs、user/about.hbs、user/username.hbs、user/email.hbs、user/preferences.hbs、preferences/card-badge.hbs、user/badge-title.hbs、site_settings_category.hbs）也確認：user/* 與 preferences/* 那幾個本來就不在 .badges 之下，本來就吃不到這些規則，所以沒有掉東西。

留在這裡而不是結案，是因為 SCSS 的最終串接結果還受 admin_base.scss 之外的檔案影響（common/ 與 desktop/ 底下還有其他 .form-horizontal 宣告的可能性我只用 grep 掃過原始檔，沒有編譯過），而這個環境沒有安裝 sass 可以編出實際 CSS 比對。

**如何確認**：在裝好 gem 的環境對 base 與 head 各編譯一次 admin_base.scss，diff 產出的 CSS；或直接開 admin 的 badges 與 users 表單畫面目視比對。

</details>
