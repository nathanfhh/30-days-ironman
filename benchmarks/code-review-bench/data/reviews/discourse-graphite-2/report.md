## 審查結論：Request Changes

> Critical 4 · Suggestion 4 · Nit 2 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ✅ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：新增識別字 stopNotificiationsText 拼字錯誤（F-009）。其餘命名與既有慣例一致。
- **C 安全**（未通過）：退訂連結採 session 驗證而非既有的 signed key 機制（F-006）；GET 改狀態造成的 CSRF 面向併入 F-001。TopicView 的 guardian.ensure_can_see!（lib/topic_view.rb:411）有擋住看不到的 topic，這一層沒問題。
- **D API 慣例**（未通過）：GET 帶副作用（F-001）；轉址用 301 且未沿用 redirect_to_correct_topic 的 .json 處理（F-010）。URL 使用 dash、全小寫，這一項符合慣例。
- **E 架構**（未通過）：退訂語意（第一次點擊只降到 regular）與信件、頁面文案承諾的「停止通知」不一致（F-005）。
- **F 資料取用與資料庫**（未通過）：TopicUser.find_by 回傳 nil 未防（F-002）；繞過 TopicUser.change 導致 notifications_reason_id 留白、退訂會被系統自動回復（F-004）。
- **G 測試**（未通過）：新增的 controller action、model method 與兩條路由完全沒有測試；唯一的 spec 改動只是補參數，沒有斷言新連結（F-007）。
- **H 非 Python 檔**（未通過）：本 diff 全部是非 Python 檔（Ruby / ERB / JS / hbs / SCSS / YAML），本維度整份適用。Ember 端的 topicUnsubscribe 路由在 client-side transition 下不會打到 server（F-008）。template / controller / view / route 的命名解析已對照 app/assets/javascripts/discourse/ember/resolver.js.es6:147-160 確認可以解到 templates/topic/unsubscribe.hbs，這一項沒有問題。
- **I 回溯分析**（未通過）：在共用的 perform_show_response 內加上 render :show，連帶改變了既有 topics#show 的 render 時機，canonical link 消失（F-003）。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 commit 除了功能本身，還夾帶了大量與功能無關的排版重構：app/models/topic_user.rb 的縮排與 hash literal 空白、lib/email/message_builder.rb 的換行、app/views/email/notification.html.erb 的全檔重排、app/assets/javascripts/discourse/routes/topic-from-params.js.es6 的 var→const 改寫，以及 config/routes.rb 的路由重排與 summary 路由 post_number 約束移除。這些改動讓真正有行為影響的那一行（app/controllers/topics_controller.rb:500 的 render :show）幾乎看不出來——F-003 正是藏在這裡。建議把純排版拆成獨立 commit。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。Gemfile.lock 的套件弱點狀況未經檢查。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且預設的 semgrep-rules 規則目錄不存在，本次未執行 SAST 掃描。 |
| ruff | 已執行 | in_diff 0、outside_diff 0 |
| ty | 略過 | ty 未安裝（不在 PATH 上）。另外本 diff 沒有 Python 檔，即使安裝也不適用。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。本 diff 含 5 個 .js.es6 檔，這一塊的 JavaScript lint 本次沒有工具覆蓋，僅靠人工閱讀。 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號索引；Dimension E 與 I 的呼叫者追蹤全部改用 grep 完成。 |
| ncr-fresh-eyes | 略過 | 本次執行環境沒有任何可派發 subagent 的工具（無 Agent / Task 工具），Phase 3 的 fresh eyes 無法派出。依 SKILL.md 規定不以主 agent 自行模擬，如實揭露。 |
| ncr-quality-check | 略過 | 同上，無法派發 subagent，Phase 4 step 3 的品質複核未執行。報告僅經 report_model.py 的機械驗證。 |

### Critical

#### F-001 GET 端點帶副作用：`t/:slug/:topic_id/unsubscribe` 直接改寫 notification_level — `config/routes.rb:440`

面向 D API 慣例 · Critical

**問題**：這條路由是 GET，handler 卻直接寫入 topic_users.notification_level 並 save!（topics_controller.rb:105-113）。這違反 RFC 9110 對 GET 的安全性（safe method）要求，而且有兩個具體後果。

第一，沒有 CSRF 防護。application_controller.rb:21 的 protect_from_forgery 只驗證非 GET 請求，所以任何第三方網頁只要放一個 `<img src="https://forum.example.com/t/x/123/unsubscribe">`，就能在使用者不知情的情況下改掉他在該 topic 的通知等級。topic id 是連號的，不需要猜。

第二，GET 會被自動抓取。企業郵件閘道的連結掃描、Gmail/Outlook 的預先擷取、瀏覽器 prefetch 都會在使用者沒有點擊的情況下打到這個 URL。因為 handler 是兩段式 toggle（watching/tracking → regular，其餘 → muted），一次自動抓取就會靜默吃掉第一段降級。

對照組就在同一個檔案裡：同樣是改 notification_level 的既有端點全部走非 GET——config/routes.rb:450-451 的 mute/unmute 是 PUT，config/routes.rb:473 的 topics#set_notifications 是 POST。這次新加的路徑是唯一的例外。

**證據**：
- `config/routes.rb:440`
- `config/routes.rb:441`
- `app/controllers/topics_controller.rb:98`
- `app/controllers/topics_controller.rb:105`
- `app/controllers/topics_controller.rb:113`
- `app/controllers/application_controller.rb:21`
- `config/routes.rb:450`
- `config/routes.rb:473`

**POC**：

```
# 受害者已在 forum 登入（瀏覽器持有 _t / _forum_session cookie）
# 攻擊者只要讓他打開任何一個含這行 HTML 的頁面：
#   <img src="https://forum.example.com/t/any-slug/123/unsubscribe" width="1" height="1">
#
# 等價的手動重現（帶上受害者的 cookie，不需要任何 CSRF token）：
curl -i -b "_forum_session=<victim-session>" \
  https://forum.example.com/t/any-slug/123/unsubscribe
# → 200，且 topic_users.notification_level 已被改寫
# 再打一次即降到 muted：
curl -i -b "_forum_session=<victim-session>" \
  https://forum.example.com/t/any-slug/123/unsubscribe
```

**影響範圍**：任何第三方網站（或站內任何能塞入圖片標籤的內容）都能對已登入使用者逐一關閉其 topic 通知。影響限於通知偏好，不涉及資料外洩或帳號接管，但 topic id 連號使得可以整批掃過去，把目標使用者對整個站的通知全部靜音；被害者不會收到任何提示，也因為 F-004 的關係無法從 notifications_reason_id 分辨是自己改的還是被改的。PHI 不在本專案範圍內。

**風險處置**：Mitigate（降低）

**修復參考**：app/controllers/email_controller.rb:12（既有的 signed-key 退訂）、config/routes.rb:473（既有的 POST set_notifications）

**修復方向**：把 GET 保留成純粹的確認頁（只 render，不寫入），實際的變更改由前端在確認後以既有的 `POST t/:topic_id/notifications`（topics#set_notifications）送出，這條路徑本來就受 CSRF 保護。若希望使用者不必登入就能一鍵退訂，改採本 repo 既有的 signed key 模式：仿照 app/controllers/email_controller.rb:12 的 `EmailController#unsubscribe` 與 DigestUnsubscribeKey，發一組 (user, topic) 專用的一次性 key 放進信件連結，server 端驗證 key 後才寫入。兩種做法都能同時解掉 CSRF 與預先擷取。

#### F-002 `TopicUser.find_by` 可能回傳 nil，未防護即取用 notification_level — `app/controllers/topics_controller.rb:105`

面向 F 資料取用與資料庫 · Critical

**問題**：`tu = TopicUser.find_by(user_id: ..., topic_id: ...)` 在沒有對應資料列時回傳 nil，下一行就直接 `tu.notification_level`，會拋 NoMethodError 變成 500。

已依假設檢驗要求找過反證：主要流程確實是安全的——寄信時 app/mailers/user_notifications.rb:312 的 `TopicUser.change(user.id, post.topic_id, last_emailed_post_number: ...)` 會在資料列不存在時建立它（app/models/topic_user.rb:104-110），所以「收到信的人點信裡的連結」這條路一定有列。但 unsubscribe 的前置守衛只有 before_filter :ensure_logged_in（topics_controller.rb:28）與 TopicView 的可見性檢查（lib/topic_view.rb:404-412），兩者都不保證 TopicUser 存在。任何已登入使用者只要對一個自己從未造訪過的公開 topic 打開這個 URL——連結被轉貼、被 admin 手動輸入、被站內搜尋帶到——就會 500。URL 是可以直接構造的，這不是理論上的路徑。

**證據**：
- `app/controllers/topics_controller.rb:105`
- `app/controllers/topics_controller.rb:107`
- `app/models/topic_user.rb:68`

**修復方向**：先建立再改，並改用 TopicUser.change（同時解掉 F-004）：

```ruby
level = TopicUser.get(@topic_view.topic, current_user)&.notification_level
new_level = if level && level > TopicUser.notification_levels[:regular]
              TopicUser.notification_levels[:regular]
            else
              TopicUser.notification_levels[:muted]
            end
TopicUser.change(current_user.id, @topic_view.topic.id,
                 notification_level: new_level,
                 notifications_reason_id: TopicUser.notification_reasons[:user_changed])
```

`TopicUser.change` 在資料列不存在時會自行建立，nil 的分支就消失了。

#### F-003 在共用的 `perform_show_response` 加上 `render :show`，使 topics#show 的 canonical link 消失 — `app/controllers/topics_controller.rb:500`

面向 I 回溯分析 · Critical

**問題**：`render :show` 是為了讓新的 unsubscribe action 有 template 可用才加的（app/views/topics/ 下只有 show / show.rss / plain，沒有 unsubscribe），但它加在 topics#show 也在用的共用方法裡，改變了 show 的 render 時機。

Rails 4.1（Gemfile.lock:241）的 `render` 是同步的：AbstractController::Rendering#render 會立刻 `self.response_body = render_to_body(options)`，template 連同 layout 當場算完。而 topics#show 的順序是 perform_show_response（第 85 行）→ canonical_url（第 87 行）。加上 render 之後，layout 已經在第 85 行渲染完畢，第 87 行才設定 @canonical_url——太晚了。

lib/canonical_url.rb:15 是 `return '' unless url || @canonical_url`，所以 layouts/_head.html.erb:9 的 canonical_link_tag 會靜默輸出空字串。application 與 crawler 兩個 layout 都 render 了這個 partial，也就是說一般瀏覽器與爬蟲拿到的 topic 頁面都會失去 `<link rel="canonical">`。這對一個分頁 URL 很多（t/:slug/:topic_id、t/:topic_id、?page=N、summary）的論壇是實質的重複內容問題，而且沒有任何測試覆蓋（spec/ 下沒有針對 canonical link tag 的斷言），會靜悄悄地壞掉。

**證據**：
- `app/controllers/topics_controller.rb:500`
- `app/controllers/topics_controller.rb:85`
- `app/controllers/topics_controller.rb:87`
- `lib/canonical_url.rb:14`
- `app/views/layouts/_head.html.erb:9`
- `app/views/layouts/application.html.erb:8`
- `app/views/layouts/crawler.html.erb:7`

**修復方向**：不要動共用方法。給 unsubscribe 自己的 template（新增 app/views/topics/unsubscribe.html.erb，內容可以 `render template: 'topics/show'`），或讓 perform_show_response 接受要 render 的 template 名稱：

```ruby
def perform_show_response(template = nil)
  ...
  format.html do
    @description_meta = @topic_view.topic.excerpt
    store_preloaded(...)
    render(template) if template
  end
  ...
end
```

然後 unsubscribe 呼叫 `perform_show_response(:show)`、show 維持 `perform_show_response`（沿用隱式 render）。另外建議補一個 request spec 斷言 topic 頁面含 `link[rel=canonical]`，避免同類回歸再發生。

#### F-004 繞過 `TopicUser.change` 直接 save!，notifications_reason_id 留白，退訂會被系統自動回復 — `app/controllers/topics_controller.rb:105`

面向 F 資料取用與資料庫 · Critical

**問題**：所有既有改動 notification_level 的路徑都走 `TopicUser.change`（app/models/topic_notifier.rb:38、app/controllers/topics_controller.rb:332 的 set_notifications），這個方法在 topic_user.rb:87-89 會一併補上 `notifications_changed_at` 與 `notifications_reason_id = user_changed`。unsubscribe 直接 `tu.notification_level = ...; tu.save!` 把這兩件事都跳過了，代價不只是欄位空白：

1. **退訂會被自動回復。** topic_user.rb:160-165 的 update_last_read SQL 是「當 `tu.notifications_reason_id is null` 且累計閱讀時間超過 auto_track_topics_after（config/site_settings.yml:285，預設 240000ms = 4 分鐘）就把 notification_level 設回 tracking」。這條 SQL 由 app/models/post_timing.rb:133 呼叫，也就是使用者之後只要再看這個 topic 幾分鐘，剛剛的退訂就自己回到 tracking。同樣地 app/services/tracked_topics_updater.rb:9 會在使用者調整 auto-track 偏好時，把所有 reason 為 null 的資料列一次覆寫掉。
2. **其他分頁不會同步。** topic_user.rb:112 的 `MessageBus.publish("/topic/#{topic_id}", { notification_level_change: ... })` 也被跳過，使用者其他開著同一 topic 的分頁仍顯示舊等級。
3. **無法追溯。** notifications_reason_id 留 null 之後，這筆變更與「使用者從沒碰過」在資料上完全無法區分，也讓 F-001 的 CSRF 更難事後稽核。

已找過反證：這條路徑上沒有其他地方補寫 notifications_reason_id，grep `notifications_reason_id` 的全部寫入點（topic_user.rb:41/46/89、category_user.rb:62、topic_notifier.rb:39）都不在此流程內。

**證據**：
- `app/controllers/topics_controller.rb:105`
- `app/controllers/topics_controller.rb:113`
- `app/models/topic_user.rb:87`
- `app/models/topic_user.rb:88`
- `app/models/topic_user.rb:160`
- `app/models/topic_user.rb:161`
- `app/services/tracked_topics_updater.rb:9`
- `app/models/post_timing.rb:133`

**修復方向**：改用 TopicUser.change 並明確帶上 reason（合併 F-002 的修法）：

```ruby
TopicUser.change(current_user.id, @topic_view.topic.id,
                 notification_level: new_level,
                 notifications_reason_id: TopicUser.notification_reasons[:user_changed])
```

這樣 notifications_changed_at、MessageBus 廣播與資料列建立都一次到位，也讓退訂不會被 update_last_read / TrackedTopicsUpdater 蓋掉。

<details>
<summary>Suggestion（4）</summary>

#### F-005 第一次點擊只降到 regular，仍會收到通知，與信件與確認頁的文案不符 — `app/controllers/topics_controller.rb:107`

面向 E 架構 · Suggestion

**問題**：handler 是兩段式：watching/tracking（> regular）→ regular，其餘 → muted。但 app/services/post_alerter.rb:105 只有在 `notification_level == muted` 時才略過建立通知，regular 等級下被回覆、被 @提及、被引用都還是會通知並寄信。

問題是文案沒有配合這個語意。信件寫的是 config/locales/server.en.yml:1855「To stop receiving notifications about this particular topic, [click here]」，點進去的頁面寫的是 config/locales/client.en.yml:985「You will stop receiving notifications for <strong>{{title}}</strong>.」——兩句話都承諾「停止」。而會收到這封信的人絕大多數正是 watching/tracking 的使用者，也就是第一次點擊必然落在「只降級、沒有停止」的那一邊。使用者會在頁面被告知已經停止，然後繼續收到通知。

**證據**：
- `app/controllers/topics_controller.rb:107`
- `app/controllers/topics_controller.rb:110`
- `config/locales/server.en.yml:1855`
- `config/locales/client.en.yml:985`
- `app/services/post_alerter.rb:105`

**修復方向**：二選一，把語意與文案對齊。若要保留兩段式降級，把 client.en.yml 的 stop_notifications 改成描述實際結果（例如「已將 <strong>{{title}}</strong> 的通知等級調整為 Normal，你只會在被回覆或被提及時收到通知」），並讓 server.en.yml 的連結文字改為「調整這個主題的通知」；若要維持「停止」的承諾，就把 handler 一律設成 muted，把降級的選擇留給頁面上既有的 topic-notifications-button。

#### F-006 退訂連結要求已登入，未登入的收信人會被靜默丟回首頁；未沿用既有的 signed key 機制 — `app/controllers/topics_controller.rb:28`

面向 C 安全 · Suggestion

**問題**：unsubscribe 被加進 before_filter :ensure_logged_in 的清單（topics_controller.rb:28）。未登入時 ensure_logged_in 拋 Discourse::NotLoggedIn，而 application_controller.rb:97-105 對非 JSON 的 GET 的處理是 `redirect_to path("/")`（第 103 行）——沒有訊息、沒有 return URL、沒有任何說明。收信人在手機郵件 app 點退訂，最可能的結果就是被丟到論壇首頁，完全不知道發生了什麼，也不知道退訂沒有生效。

這個 repo 對「信裡的退訂連結」本來就有另一套做法：config/routes.rb:202 的 `email/unsubscribe/:key` 走 app/controllers/email_controller.rb:12，用 DigestUnsubscribeKey 換出使用者，不需要登入，而且會在 key 與當前登入者不符時擋下（email_controller.rb:15-19）。新功能沒有沿用它，於是同一個產品裡出現兩種語意不同的退訂連結。

**證據**：
- `app/controllers/topics_controller.rb:28`
- `app/controllers/application_controller.rb:97`
- `app/controllers/application_controller.rb:103`
- `app/controllers/email_controller.rb:12`
- `config/routes.rb:202`

**修復方向**：沿用既有的 signed key 模式：為 (user, topic) 產生一次性 key，路由做成 `t/:topic_id/unsubscribe/:key`（或擴充現有的 UnsubscribeKey 模型），server 端以 key 決定使用者，不依賴 session。這同時能解掉 F-001 的 CSRF 面向。若短期內仍要保留 session 版本，至少在 ensure_logged_in 失敗時帶著 return URL 導向登入頁並顯示訊息，而不是無聲丟回 `/`。

#### F-007 新增的 action、model method 與路由完全沒有測試；唯一的 spec 改動沒有斷言新行為 — `spec/components/email/message_builder_spec.rb:172`

面向 G 測試 · Suggestion

**問題**：這次唯一的 spec 改動是在既有的 message_builder_spec 補上 `unsubscribe_url` 這個參數（第 172 行）——補的是「不補就會壞」的輸入，沒有任何一行斷言新的 per-topic 連結真的出現在信件內容裡。既有的斷言（第 179 行）仍然只檢查 user_preferences_url。

沒有被覆蓋的行為包括：TopicsController#unsubscribe 的兩段式 toggle、TopicUser 不存在時的行為（F-002）、slug 不符時的 301 轉址、Topic#unsubscribe_url 的字串組成、以及兩條新路由是否被正確 recognize。spec/controllers/topics_controller_spec.rb 與 spec/models/topic_spec.rb 都已存在，加測試沒有基礎建設成本。

**證據**：
- `spec/components/email/message_builder_spec.rb:172`
- `spec/components/email/message_builder_spec.rb:179`
- `app/controllers/topics_controller.rb:98`
- `app/models/topic.rb:719`

**修復方向**：在 spec/controllers/topics_controller_spec.rb 補一組 `describe '#unsubscribe'`：分別覆蓋 watching → regular、regular → muted、TopicUser 不存在、slug 不符要 301、未登入的行為；在 spec/models/topic_spec.rb 補一行 `expect(topic.unsubscribe_url).to eq("#{topic.url}/unsubscribe")`；在 message_builder_spec 既有的 context 補一句 `expect(message_with_unsubscribe.body).to match(%r{t/1234/unsubscribe})`，讓「信裡真的有這個連結」被釘住。

#### F-008 Ember 端的 topicUnsubscribe 路由無條件顯示「已停止通知」，client-side transition 時 server 從未被呼叫 — `app/assets/javascripts/discourse/routes/topic-unsubscribe.js.es6:4`

面向 H 非 Python 檔 · Suggestion

**問題**：退訂的寫入完全發生在 Rails 端；Ember 的 topicUnsubscribe 路由只負責顯示結果。它的 model hook 呼叫 PostStream.loadTopicView(params.id)，而該方法（post-stream.js.es6:811-825）是 `PreloadStore.getAndRemove("topic_" + topicId, ...)`——整頁載入時讀得到 server 在 perform_show_response 裡塞的 preload，這條路是通的。

但站內轉場時 preload 不存在，fallback 是 `Discourse.ajax("/t/<id>.json")`，打的是一般的 topic JSON，不是 unsubscribe 端點。而 lib/click-track.js.es6:90-100 對站內連結會走 `Discourse.URL.routeTo` 做 client-side transition。也就是說：只要有人把退訂 URL 貼在文章裡，另一個使用者在站內點下去，畫面會顯示「You will stop receiving notifications for …」，但 server 端什麼都沒發生。頁面在說謊。

附帶一提，即使是整頁載入的正常流程，這個 template 也不區分成功與失敗——沒有 loading、empty 或 error 狀態，model hook 的 promise reject 時使用者只會看到 Ember 預設的錯誤路由。

**證據**：
- `app/assets/javascripts/discourse/routes/topic-unsubscribe.js.es6:4`
- `app/assets/javascripts/discourse/routes/topic-unsubscribe.js.es6:6`
- `app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs:3`
- `app/assets/javascripts/discourse/models/post-stream.js.es6:811`
- `app/assets/javascripts/discourse/lib/click-track.js.es6:90`

**修復方向**：讓頁面顯示的內容取決於 server 實際回報的狀態，而不是「進到這個路由」這件事本身。最直接的做法是在 TopicViewSerializer 帶回變更後的 notification_level（或一個 `unsubscribed` 旗標），template 依它決定文案；若採用 F-001 的建議把寫入改成明確的 POST，這個路由就自然變成「先確認、再送出、再顯示結果」，三種狀態都有地方放。另外補一個 model hook 失敗時的錯誤訊息。

</details>

<details>
<summary>Nit（2）</summary>

#### F-009 `stopNotificiationsText` 拼字錯誤（Notificiations） — `app/assets/javascripts/discourse/controllers/topic-unsubscribe.js.es6:5`

面向 A 風格 · Nit

**問題**：「Notificiations」多了一個 i。controller 與 template 兩邊拼法一致所以功能正常，但這是新引入的公開 property 名稱，一旦有 theme 或 plugin 覆寫它就固定下來了，之後要改名的成本會比現在高。

**證據**：
- `app/assets/javascripts/discourse/controllers/topic-unsubscribe.js.es6:5`
- `app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs:3`

**修復方向**：兩處一起改成 `stopNotificationsText`。趁還沒有任何外部使用者最省事。

#### F-010 轉址用 301，且未沿用 `redirect_to_correct_topic` 對 JSON 請求的處理 — `app/controllers/topics_controller.rb:101`

面向 D API 慣例 · Nit

**問題**：兩點都很小，但都是既有 helper 已經處理過的事情。

其一，301 是永久轉址，瀏覽器會無限期快取。對一個會改變狀態的端點（見 F-001）發永久轉址，語意上更不妥；topics#show 用 301 是因為它是純讀取。

其二，既有的 redirect_to_correct_topic（第 451-460 行）會在 `request.format.json?` 時補上 `.json` 再轉址，新寫的這一行沒有。所以 `t/wrong-slug/123/unsubscribe.json` 這種請求會被轉到 HTML 版本，呼叫端拿到的不是它要的格式。

**證據**：
- `app/controllers/topics_controller.rb:101`
- `app/controllers/topics_controller.rb:102`
- `app/controllers/topics_controller.rb:451`
- `app/controllers/topics_controller.rb:454`

**修復方向**：改用 302，並讓轉址走一個與 redirect_to_correct_topic 同樣處理 format 的小 helper，例如 `url = @topic_view.topic.unsubscribe_url; url << ".json" if request.format.json?; redirect_to url`。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 `add_unsubscribe_link: true` 現在隱含要求同時傳入 `unsubscribe_url`，這個新的呼叫契約要不要有明確的守衛？

面向 D API 慣例

**背景**：config/locales/server.en.yml:1855 在 unsubscribe_link 這個 key 裡加了 `%{unsubscribe_url}` 插值。lib/email/message_builder.rb:67 與 :90 都會用 template_args 展開它，而 template_args 只是 opts 的 merge（message_builder.rb:24-28），沒有任何檢查。少了這個 key，I18n 會拋 MissingInterpolationArgument，寄信直接失敗。已 grep 過整個 repo：站內唯一設定 add_unsubscribe_link 的地方是 app/mailers/user_notifications.rb:294，而 unsubscribe_url 就在下一行由同一個方法補上（:295），連 mailing_list_notify 這種不傳它的呼叫端也一樣安全，所以「repo 內會壞」這個說法不成立，因此沒有列為 finding。剩下的問題是 repo 外：Discourse 是有 plugin 生態的產品，任何自行 build MessageBuilder 並帶 add_unsubscribe_link 的 plugin，升級到這個 commit 之後會在寄信時炸掉，而且是非 en locale 不會炸、en locale 才炸的那種不對稱失敗。

**如何確認**：確認 plugin 是否被視為這個介面的呼叫端。若是，在 MessageBuilder#initialize 對 add_unsubscribe_link 為真時補一個 `@template_args[:unsubscribe_url] ||= ...` 的預設值（或明確 raise ArgumentError 說明缺什麼），並加一個 spec 覆蓋「只給 add_unsubscribe_link、不給 unsubscribe_url」的情形。

#### Q-002 dropdown-button 改成「title 為空就不輸出 `<h4 class='title'>`」之後，既有使用者的版面有沒有位移？

面向 H 非 Python 檔

**背景**：app/assets/javascripts/discourse/components/dropdown-button.js.es6:27-30 現在只在 title 為 truthy 時 push `<h4>`。既有元件裡 notifications-button.js.es6:6 與 pinned-button.js.es6:6 都寫死 `title: ''`，也就是說它們原本每次都會產生一個空的 `<h4 class='title'></h4>`，現在不會了。這個改動的動機（不要在 unsubscribe 頁面上顯示空標題）是合理的，而且比原本輸出 `<h4>undefined</h4>` 好。但影響範圍是全站所有 dropdown button，不只是這個新頁面。另外 dropdown-button.js.es6:5 的 rerenderTriggers 只列了 text 與 longDescription，沒有 title，所以 title 若是非同步取得（badge-button.js.es6:4 把它 alias 到 badge.displayDescription），現在會從「先顯示空標題、之後填內容」變成「一開始整個元素不存在」。這兩件事都需要在瀏覽器裡看才能確定，靜態閱讀無法判斷是否真的造成視覺回歸，所以不列為 finding。

**如何確認**：在瀏覽器實際比對改動前後的 topic 通知下拉、置頂按鈕與 badge 頁面截圖；若確認有位移，把 'title' 加進 rerenderTriggers 並調整對應 SCSS。

</details>
