## 審查結論：Request Changes

> Critical 1 · Suggestion 9 · Nit 1 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

- **A 風格**（未通過）：updateDevice 的命名、註解與參數型別和實際行為對不上（F-011）。其餘新增的程式碼長度、命名與可讀性沒有問題。
- **B 簡潔**（未通過）：「匿名裝置 30 天」這個決定現在同時寫死在四個地方、兩種名字（F-007）。
- **C 安全**（未通過）：新增的上限控制有兩條可以繞過的路徑：local cache 旁路（F-001，Critical）與未帶 device id 的請求（F-002）。沒有發現未參數化 SQL、硬編憑證或 eval 類問題——updateDevice 與 CreateOrUpdateDevice 的查詢都是參數化的。
- **D API 慣例**（未通過）：ErrDeviceLimitReached 不是 errutil 錯誤，違反同層 authn client 的一致慣例，使用者拿到的是沒有原因的 401 或一次導向 /login（F-004）。這次沒有新增 HTTP route，URL 命名、動詞語意與授權檢查不適用。
- **E 架構**（未通過）：AnonStore 的建構被搬出 wire DI 而 wire.go 的註冊沒有跟著更新（F-005）；匿名認證從非同步 tagging 改成同步阻塞資料庫（F-006）。
- **F 資料取用與資料庫**（未通過）：CountDevices 與 INSERT 之間沒有交易或鎖，上限在併發與多實例下會被超過（F-003）。已確認 anon_device 上有 updated_at 索引（migrations.go:24），所以 CountDevices 不是全表掃描；也已確認 MySQL 連線帶 clientFoundRows=true（pkg/services/sqlstore/sqlstore.go:287），因此 updateDevice 的 RowsAffected 是 matched rows，不會因為欄位值沒變而誤判成 0。
- **G 測試**（未通過）：新測試只覆蓋被拒絕的路徑，沒有斷言這條路徑真正的功能——上限已滿時既有裝置仍要能更新（F-010）。
- **H 非 Python 檔**（未通過）：非 Go 檔案共三類問題：新設定沒有寫進 conf/defaults.ini、conf/sample.ini 與 docs（F-008），前端新欄位無人使用且型別被推導成 undefined（F-009）。這次沒有 Dockerfile、nginx.conf、docker-compose 或 migration 變更。
- **I 回溯分析**（未通過）：三個既有函式的契約被改動：ProvideAnonDBStore 多了一個 int64 參數、ProvideAnonymousDeviceService 的第三個參數從 anonstore.AnonStore 換成 db.DB、TagDevice 從「永遠回 nil」變成會回傳錯誤。前兩者的 Go 呼叫端已全數更新（database_test.go:16/53/74、impl_test.go:116/151），但 pkg/server/wire.go:374-375 的 DI 註冊沒有（F-005）。第三者最重要：TagDevice 的回傳值以前是死的，現在 client.go:44 依賴它做認證決策，而 tagDeviceUI 在 cache 命中時會回 nil 的既有行為並沒有跟著調整——這正是 F-001。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 同時做了三件可以分開的事：(1) 新增 device_limit 設定與 anonstore 的強制執行邏輯；(2) 把匿名 tagging 從背景 goroutine 改成在認證路徑上同步執行（pkg/services/anonymous/anonimpl/client.go:44-50）；(3) 把 AnonStore 的建構從 wire DI 搬進 ProvideAnonymousDeviceService（pkg/services/anonymous/anonimpl/impl.go:36-45）。第 (2) 項會改變所有既有匿名部署的行為——包含完全沒有設定 device_limit 的那些——卻是被夾帶在一個標題只講「add configurable device limit」的 MR 裡。第 (3) 項則是被第 (1) 項的 wire 限制逼出來的副作用。建議至少把 (2) 拆成獨立 commit 並在描述中點名，好讓 reviewer 與後續 bisect 看得見它。這只是提醒，決定權在人。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。preflight.py 已確認。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且 NCR_OPENGREP_RULES 指向的 semgrep-rules 目錄不存在，本次未執行 SAST 掃描。此外這次 diff 以 Go 為主，semgrep-rules 的 go 規則集也不在既有的副檔名對應表中。 |
| ruff | 已執行 | in_diff 0、outside_diff 10 |
| ty | 略過 | ty 未安裝（不在 PATH 上），略過 Python 型別檢查。本次 diff 沒有 Python 檔，即使執行也不會有可歸屬的結果。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上），因此 diff 中的兩個 .ts 檔沒有經過 JavaScript/TypeScript lint。F-009 的型別問題是人工閱讀 tsconfig.json 與 config.ts 得出的，不是掃描器提供的。 |
| codegraph | 略過 | codegraph 未安裝，Phase 0 的 init 未執行。本次的呼叫者列舉與完整性確認（dimension E、I 與關鍵操作路徑列舉）全部改用 grep 完成，因此第二、第三跳的間接呼叫者可能有遺漏。 |
| go build / go vet / wire gen | 略過 | 本機沒有 Go toolchain 也沒有網路，無法編譯、無法跑 go vet，也無法執行 .drone.yml:3770-3771 的 wire gen。因此所有關於「是否編得過」的判斷都只能從原始碼推導；見 Q-001。 |
| ncr-fresh-eyes | 略過 | 本次執行環境沒有可用的 subagent 派工工具（Agent/Task 工具不存在，ToolSearch 也查不到），因此 Phase 3 的第一步 fresh eyes 未執行。依 SKILL.md 的規定不得由主 agent 自行模擬——寫這份報告的人已經讀完九個 dimension，模擬出來的東西正好失去 fresh eyes 存在的理由。這代表本報告缺少一次未被 checklist 框住的閱讀。 |
| ncr-quality-check | 略過 | 同上，無法派出 subagent，Phase 4 步驟 3 的品質檢查未執行。報告只通過了 report_model.py 的機械驗證。 |

### Critical

#### F-001 裝置上限被 local cache 旁路：被拒的裝置只要重送一次請求就會通過 — `pkg/services/anonymous/anonimpl/impl.go:83`

面向 C 安全 · Critical

**問題**：tagDeviceUI 是先寫 cache 再寫資料庫，而且寫入失敗時不會把 cache key 收回：impl.go:87 的 localCache.SetDefault 在 impl.go:93 的 CreateOrUpdateDevice 之前執行，之後只有 return err。於是一台新裝置在上限已滿時的兩次請求會得到相反的結果——第一次 cache miss，寫入 cache，CreateOrUpdateDevice 走到 database.go:116 的 updateDevice 找不到列，回 ErrDeviceLimitReached，client.go:45-47 讓 Authenticate 失敗；第二次（同一個 X-Grafana-Device-Id、同一個實例、29 分鐘內）在 impl.go:83 直接命中 cache 回 nil，TagDevice 回 nil，Authenticate 照常發出匿名 identity。這台裝置從頭到尾沒有被寫進 anon_device，所以後續的 CountDevices、usage stats 與 admin 統計都看不到它。反證檢查：grep 全 repo 的 localCache 使用點只有 impl.go:83 的 Get 與 impl.go:87 的 SetDefault（另加測試裡一處 SetDefault），沒有任何 Delete；untagDevice（impl.go:100-115）只刪資料庫不動 cache；authn broker 對 client 錯誤只是累積後回傳（pkg/services/authn/authnimpl/service.go:197-222），不會抑制下一次請求。也就是說沒有任何上游把這條路徑擋住。同一個「先寫 cache 後寫 DB」的順序在改動前無害（回傳值是死的），是這次讓 TagDevice 的回傳值變成認證依據，才把它變成上限的漏洞。附帶後果：請求 ctx 被取消而寫入中斷時同樣如此，該裝置 29 分鐘內既不被記錄也不被計數。

**證據**：
- `pkg/services/anonymous/anonimpl/impl.go:83`
- `pkg/services/anonymous/anonimpl/impl.go:87`
- `pkg/services/anonymous/anonimpl/impl.go:93`
- `pkg/services/anonymous/anonimpl/impl.go:42`
- `pkg/services/anonymous/anonimpl/client.go:44`
- `pkg/services/anonymous/anonimpl/anonstore/database.go:116`

**POC**：

```
設定 [auth.anonymous] enabled = true 與 device_limit = 1，並先讓 anon_device 有一筆 30 天內的紀錄（例如用瀏覽器開一次 Grafana）。然後對同一個未知 device id 連打兩次：

  curl -i -H 'X-Grafana-Device-Id: bypass-me' http://grafana:3000/api/search
  curl -i -H 'X-Grafana-Device-Id: bypass-me' http://grafana:3000/api/search

第一次得到 401 Unauthorized，第二次得到 200 與正常的搜尋結果。接著 select device_id from anon_device 會發現 bypass-me 不在裡面。
```

**影響範圍**：任何匿名用戶端只要對同一個 device id 重送一次請求即可取得匿名存取權，因此 device_limit 實際上只擋得住「只嘗試一次」的用戶端，設定的上限值不成立；被放行的裝置不會寫入 anon_device，對 CountDevices、usage stats（impl.go:70）與 admin 統計（pkg/api/admin.go:68）全部隱形，管理員無法從任何既有畫面察覺。受影響的是所有設定了 device_limit 的匿名部署。本次變更不觸及病歷或臨床資料，PHI 成本為零。

**風險處置**：Mitigate（降低）

**修復參考**：pkg/services/anonymous/anonimpl/impl.go:87

**修復方向**：把 a.localCache.SetDefault(key, struct{}{}) 移到 CreateOrUpdateDevice 成功之後；若要保留先寫 cache 以避免併發重複寫入，則在錯誤分支補上 a.localCache.Delete(key) 再 return err。兩種寫法都要配一個測試：limit 已滿時，對同一個 deviceID 連續呼叫兩次 TagDevice，兩次都必須回 ErrDeviceLimitReached。

<details>
<summary>Suggestion（9）</summary>

#### F-002 沒有帶 X-Grafana-Device-Id 的匿名請求完全不受 device_limit 約束 — `pkg/services/anonymous/anonimpl/impl.go:120`

面向 C 安全 · Suggestion

**問題**：把「拒絕匿名登入」這個新的危險操作的所有到達路徑列出來，共四條，只有一條會經過上限檢查：(1) impl.go:120 —— header 不存在時 TagDevice 直接 return nil，Authenticate 照常發 identity；(2) impl.go:128 —— network.GetIPFromAddress 失敗時同樣 return nil；(3) tagDeviceUI 的 cache 命中（見 F-001）；(4) 正常路徑，會經過 CountDevices 與 updateDevice。前端只在 backend_srv 的 fetch 上帶這個 header（backend_srv.ts:159-162），最初的 HTML document 請求並不帶，因此就算上限已滿，頁面本身仍會載入，只有之後的 API 呼叫會 401；而任何 curl、腳本或自製用戶端只要不帶 header 就完全不進入計數。這是既有 device tagging 設計（原本只服務 usage stats）被拿來承載強制執行後必然的缺口，不是打字錯誤，但 device_limit 的語意必須寫清楚。

**證據**：
- `pkg/services/anonymous/anonimpl/impl.go:120`
- `pkg/services/anonymous/anonimpl/impl.go:128`
- `pkg/services/anonymous/anonimpl/client.go:44`
- `public/app/core/services/backend_srv.ts:159`

**修復方向**：兩條路擇一：(a) 若上限要真的成立，在 cfg.AnonymousDeviceLimit > 0 時對沒有 device id 的匿名請求也做決策——例如一律拒絕，或以 client IP + user agent 雜湊出 fallback device id；(b) 若接受這個範圍，就在 docs 與 conf/defaults.ini 的註解中明講 device_limit 只涵蓋會帶 X-Grafana-Device-Id 的請求（實務上等於 Grafana UI session），不涵蓋 API 用戶端。無論選哪一條，都要和 F-008 的文件一起處理。

#### F-003 CountDevices 與 INSERT 之間沒有交易或鎖，上限在併發與多實例下會被超過 — `pkg/services/anonymous/anonimpl/anonstore/database.go:109`

面向 F 資料取用與資料庫 · Suggestion

**問題**：這是一個典型的 check-then-act：database.go:110 的 CountDevices 開一個 session 讀計數，database.go:149-151 的 INSERT 開另一個 session 寫入，中間沒有交易、沒有 SELECT ... FOR UPDATE、沒有可以擋住的唯一性約束（device_id 的唯一索引只防同一台裝置重複，不防總數超過），也沒有 serverlock。同一實例的併發請求各自讀到 count == limit-1 就會各插一筆；HA 部署下多個 Grafana 實例更是各讀各的。這個部署形態是被預期的——同一個檔案的 Run（impl.go:169）就是用 serverLock.LockAndExecute 來協調多實例的清理工作。再加上每個實例各持有一份 29 分鐘的 localcache（impl.go:42），超收量會隨實例數放大。實際超收幅度是環境相依的，見 Q-002；這裡只主張可從程式碼證實的部分：沒有任何併發保護。

**證據**：
- `pkg/services/anonymous/anonimpl/anonstore/database.go:109`
- `pkg/services/anonymous/anonimpl/anonstore/database.go:110`
- `pkg/services/anonymous/anonimpl/anonstore/database.go:151`
- `pkg/services/anonymous/anonimpl/impl.go:42`
- `pkg/services/anonymous/anonimpl/impl.go:169`

**修復方向**：把 count 與 insert 收進同一個交易：用 sqlStore.WithTransactionalDbSession 包住整段，或改寫成單一 SQL 讓資料庫仲裁，例如 INSERT INTO anon_device (...) SELECT ?, ?, ?, ?, ? WHERE (SELECT COUNT(*) FROM anon_device WHERE updated_at BETWEEN ? AND ?) < ?，再依 RowsAffected 決定要不要回 ErrDeviceLimitReached。若刻意接受近似上限，請在 docs 與 defaults.ini 註明 device_limit 是 best-effort 而非硬上限。

#### F-004 ErrDeviceLimitReached 不是 errutil 錯誤，撞到上限的使用者只會拿到沒有原因的 401 或被導去 /login — `pkg/services/anonymous/anonimpl/anonstore/database.go:18`

面向 D API 慣例 · Suggestion

**問題**：ErrDeviceLimitReached 是 fmt.Errorf("device limit reached")，沒有 errutil 的 status code、errorID 與 public message。它從 Anonymous.Authenticate 回傳後被 authn broker 累積（authnimpl/service.go:207），落到 contexthandler.go:124 的 LookupTokenErr，最後 API 請求走 middleware/auth.go:39 的 WriteErrOrFallback(401, "Unauthorized", err)——因為不是 errutil.Error，只會用 fallback 訊息；瀏覽器請求則走 middleware/auth.go:50 被導向 /login。對一台只開匿名存取、沒有其他登入方式的實例來說，使用者會被丟到一個他用不了的登入頁，而且完全看不到原因，維運端也只有一行 warn log。同層的每一個 authn client 都用 errutil：api_key.go:22-25、basic.go:12、form.go:12、jwt.go:27-32 皆是 errutil.Xxx(id, errutil.WithPublicMessage(...))。

**證據**：
- `pkg/services/anonymous/anonimpl/anonstore/database.go:18`
- `pkg/services/anonymous/anonimpl/client.go:46`
- `pkg/services/contexthandler/contexthandler.go:124`
- `pkg/middleware/auth.go:39`
- `pkg/middleware/auth.go:50`
- `pkg/services/authn/clients/api_key.go:22`

**修復方向**：改成 var ErrDeviceLimitReached = errutil.Unauthorized("anonymous.device-limit-reached", errutil.WithPublicMessage("Anonymous device limit reached"))，回傳處用 .Errorf(...) 產生實例；client.go:45 的 errors.Is 對 errutil.Base 仍然成立（errutil.Base 有實作 Is），但請一併補測試釘住這一點。若不想讓匿名使用者知道上限存在，也可以刻意回一個中性的 public message，但那應該是明確的決定而不是預設行為。

#### F-005 AnonStore 的建構被搬出 wire DI，pkg/server/wire.go 的註冊變成過時且與新簽章不相容 — `pkg/services/anonymous/anonimpl/impl.go:37`

面向 E 架構 · Suggestion

**問題**：ProvideAnonymousDeviceService 的第三個參數從 anonstore.AnonStore 改成 db.DB（impl.go:37），並在函式內自己呼叫 anonstore.ProvideAnonDBStore(sqlStore, cfg.AnonymousDeviceLimit)（impl.go:43）。這帶來兩個可驗證的後果。第一，pkg/server/wire.go:374-375 仍然註冊著 ProvideAnonDBStore 與 wire.Bind(new(anonstore.AnonStore), new(*anonstore.AnonDBStore))，但 ProvideAnonDBStore 現在需要一個 int64，而整個 wire set 裡沒有任何 provider 提供 int64（grep wire.go 與 wireexts_oss.go 找不到 wire.Value 或任何回傳 int64 的 provider）；同時 grep 全 repo，對 anonstore.AnonStore 的引用在 pkg/services/anonymous/ 之外只剩 wire.go:375 這一行，代表這個 provider 已經沒有消費者。wire_gen.go 是 .gitignore:192 排除的產物，由 CI 的 wire gen -tags oss ./pkg/server 產生（.drone.yml:3770-3771），所以這個註冊是「死掉的設定」還是「codegen 直接失敗」取決於 google/wire 對未使用 provider 的處理，見 Q-001；無論答案為何，這兩行都已經不再描述真實的相依關係。第二，AnonStore 這個介面失去了替換點——AnonDeviceService 現在只能吃真的 db.DB，測試也因此從注入 store 改成傳整個 test DB（impl_test.go:116-117、151-152），未來要為這個 service 寫不需要資料庫的單元測試就沒有接縫可用。

**證據**：
- `pkg/services/anonymous/anonimpl/impl.go:37`
- `pkg/services/anonymous/anonimpl/impl.go:43`
- `pkg/server/wire.go:374`
- `pkg/server/wire.go:375`
- `pkg/services/anonymous/anonimpl/anonstore/database.go:52`

**修復方向**：優先做法是保留 DI：把 device limit 用具名型別或 *setting.Cfg 傳給 provider，例如 func ProvideAnonDBStore(sqlStore db.DB, cfg *setting.Cfg) *AnonDBStore { return &AnonDBStore{..., deviceLimit: cfg.AnonymousDeviceLimit} }，wire 就能自行解析（*setting.Cfg 已在 set 內），ProvideAnonymousDeviceService 也能維持接 anonstore.AnonStore。若確定要在 service 內部建構 store，則必須同時把 pkg/server/wire.go:374-375 這兩行移除，並在本地跑一次 wire gen -tags oss ./pkg/server 確認 codegen 通過。

#### F-006 匿名認證從背景 tagging 改成在請求路徑上同步等待資料庫 — `pkg/services/anonymous/anonimpl/client.go:44`

面向 E 架構 · Suggestion

**問題**：改動前 TagDevice 跑在一個 goroutine 裡，用 context.Background() 加 2 分鐘 timeout，並包了 recover()（diff 中被刪除的 client.go 區塊）。改動後它在 Authenticate 內同步執行，且改用請求本身的 ctx。三個後果都可從程式碼推出：(a) 每一次 cache miss 的匿名請求——新裝置，或每個實例每 29 分鐘一次——都會在認證路徑上等一次資料庫往返，設了 device_limit 之後還多一次 SELECT COUNT(*)（database.go:110）；(b) 用戶端斷線導致 ctx 取消時寫入會中斷，而 cache 已經先設好（見 F-001），該裝置在 29 分鐘內既不記錄也不計數；(c) 這個代價對完全沒有設定 device_limit 的既有部署也照樣付出，而 MR 標題並沒有告訴他們行為會改變。強制執行本來就必須同步（不阻塞就無法拒絕），問題在於它被無條件套用到所有匿名部署。

**證據**：
- `pkg/services/anonymous/anonimpl/client.go:44`
- `pkg/services/anonymous/anonimpl/impl.go:93`
- `pkg/services/anonymous/anonimpl/anonstore/database.go:110`

**修復方向**：把同步與否綁在設定上：cfg.AnonymousDeviceLimit > 0 時走現在的同步路徑，否則維持原本的背景 goroutine tagging。另外，即使在同步路徑上，寫入本身也不該被用戶端斷線打斷——用 context.WithoutCancel(ctx)（Go 1.21 已有，CI 用的就是 golang:1.21.3）保留 trace 與 deadline 以外的值，或沿用原本的 context.Background() 加 timeout。

#### F-007 「匿名裝置 30 天」這個決定現在寫死在四個地方、兩種名字，統計視窗與強制執行視窗會各自漂移 — `pkg/services/anonymous/anonimpl/anonstore/database.go:16`

面向 B 簡潔 · Suggestion

**問題**：這次改動把 api 套件的 thirtyDays 改名成 anonymousDeviceExpiration（commit 訊息寫 refactored const to make it clearer with expiration），又在 anonstore 新增一份同值同名的常數，但 impl.go:25 的 thirtyDays 與 pkg/api/admin.go:67 的區域變數 thirtyDays 都沒有動。結果是同一個「30 天」散在四個檔案、三個套件、兩種名字。這不只是重複：anonstore 的那份決定「哪些裝置算進上限」（database.go:110），impl.go:70 的 usage stat 與 pkg/api/admin.go:68 的 admin 裝置統計用另一份決定「畫面上顯示幾台」，api/api.go:71 的 ListDevices 又用第三份決定「列表列出哪些」。改其中一處而漏掉其他處，管理員看到的數字就會和實際被強制執行的上限不一致，而且不會有任何錯誤或警告。這次改名做到一半，正好把四份中的兩份換成新名字，讓下一個人更難用 grep 找齊。

**證據**：
- `pkg/services/anonymous/anonimpl/anonstore/database.go:16`
- `pkg/services/anonymous/anonimpl/api/api.go:18`
- `pkg/services/anonymous/anonimpl/impl.go:25`
- `pkg/api/admin.go:67`

**修復方向**：在 pkg/services/anonymous（或 anonstore）匯出單一常數，例如 const AnonDeviceExpiration = 30 * 24 * time.Hour，然後讓 anonstore/database.go、anonimpl/api/api.go、anonimpl/impl.go 與 pkg/api/admin.go 全部引用它，刪掉其餘三份。順帶一提 impl.go:27 的 keepFor（61 天）和它是不同的決定（清理 vs 計數），保持分開是對的，但值得加一行註解說明兩者的關係。

#### F-008 新的 device_limit 設定沒有寫進 conf/defaults.ini、conf/sample.ini 或設定文件 — `pkg/setting/setting.go:1654`

面向 H 非 Python 檔 · Suggestion

**問題**：setting.go:1654 新增了 anonSection.Key("device_limit").MustInt64(0)，但三份使用者會看的清單都沒有跟著更新：conf/defaults.ini 的 [auth.anonymous] 區段（581-592 行）只到 hide_version，conf/sample.ini（570-581 行）同樣，docs 的認證頁面（66-77 行）也同樣。Grafana 的 conf/defaults.ini 同時是預設值來源與設定項的參考清單，沒有列出的 key 對使用者而言等於不存在。這個設定的預設語意還特別容易誤解——0 代表不限制而不是「不允許任何裝置」（database.go:109 的 if s.deviceLimit > 0），若有人照直覺設成 0 想關閉匿名存取，得到的會是完全相反的結果。功能上不會壞（MustInt64(0) 自帶預設值），所以不是 Critical。

**證據**：
- `pkg/setting/setting.go:1654`
- `conf/defaults.ini:581`
- `conf/sample.ini:570`
- `docs/sources/setup-grafana/configure-security/configure-authentication/grafana/index.md:66`

**修復方向**：在三處的 [auth.anonymous] 區段各加一段，內容需要說明：計數單位是「30 天內活躍的裝置」而不是同時在線數、預設 0 代表不限制，以及 F-002 談到的涵蓋範圍（只約束帶 X-Grafana-Device-Id 的請求）。例如 conf/defaults.ini：

  # limit the number of anonymous devices that can be active within the last 30 days
  # 0 means no limit
  device_limit = 0

docs 那頁同時補上「超過上限時匿名請求會被拒絕」的行為描述。

#### F-009 前端的 anonymousDeviceLimit 沒有任何消費者，而且型別被推導成 undefined — `packages/grafana-runtime/src/config.ts:97`

面向 H 非 Python 檔 · Suggestion

**問題**：config.ts:97 寫的是 anonymousDeviceLimit = undefined; ——沒有型別註記。tsconfig.json:8 的 strict 是 true，所以 strictNullChecks 開著，這個屬性會被推導成 undefined 而不是介面宣告的 number | undefined。它仍然滿足 implements GrafanaConfig（undefined 可指派給 number | undefined），所以現在編得過；但任何未來想用它的程式碼，例如 config.anonymousDeviceLimit > 0，都會直接在編譯期失敗，必須加 cast。而 grep 整個 public/ 與 packages/ 只找得到這兩行宣告——這次沒有任何前端程式碼讀它。另一方面後端的 AnonymousDeviceLimit int64 沒有 omitempty（frontend_settings.go:195），JSON 永遠會帶一個數字，undefined 在實務上不會出現，所以連 | undefined 都不是誠實的型別。附帶一提，這個欄位會隨 frontend settings API 回應（pkg/api/frontendsettings.go:198）一起送給包含匿名使用者在內的所有呼叫者，等於把設定的上限值公開出去；若那不是刻意的，值得一併考慮。（同檔 config.ts:166 的 tokenExpirationDayLimit: undefined; 有同樣的毛病，那是既有的，不算在這次。）

**證據**：
- `packages/grafana-runtime/src/config.ts:97`
- `packages/grafana-data/src/types/config.ts:200`
- `pkg/api/dtos/frontend_settings.go:195`
- `tsconfig.json:8`

**修復方向**：最小修法是把型別寫出來：anonymousDeviceLimit: number | undefined = undefined;；更貼近後端事實的是 anonymousDeviceLimit = 0;（並把 grafana-data 的介面改成 number）。若這一輪前端還用不到它，也可以考慮把這兩個 .ts 檔連同 DTO 欄位留到真的有 UI 消費時再一起加——目前它是三個檔案的維護成本換零個使用者。

#### F-010 新測試只覆蓋被拒絕的路徑，沒有斷言「上限已滿時既有裝置仍可更新」 — `pkg/services/anonymous/anonimpl/anonstore/database_test.go:51`

面向 G 測試 · Suggestion

**問題**：TestIntegrationBeyondDeviceLimit 建了一台裝置、把 limit 設成 1，再用第二個 device id 呼叫，斷言 ErrDeviceLimitReached。這確認了拒絕，但 database.go:108 的註解寫的是 if device limit is reached, only update devices——這條路徑真正的功能是「既有裝置仍然要能更新 client_ip / user_agent / updated_at」，而這一半完全沒有被斷言。可驗證的說法是：把 updateDevice 裡整段 UPDATE 拿掉、只留 return ErrDeviceLimitReached，這個測試仍然會通過，也就是它現在鎖不住這個功能。同樣沒有測試涵蓋 client.go:44-50 新增的錯誤分流（ErrDeviceLimitReached 要讓認證失敗、其他錯誤只記 log 並放行），而那是這次唯一改變使用者可見行為的地方。

**證據**：
- `pkg/services/anonymous/anonimpl/anonstore/database_test.go:51`
- `pkg/services/anonymous/anonimpl/anonstore/database_test.go:69`
- `pkg/services/anonymous/anonimpl/anonstore/database.go:108`

**修復方向**：在同一個測試補上正向斷言：limit 已滿時，用第一台裝置的 device id 帶著新的 ClientIP/UserAgent 再呼叫一次 CreateOrUpdateDevice，require.NoError，然後 ListDevices 回來的那筆必須是新值、且總數仍然是 1。另外為 Anonymous.Authenticate 加一個表格測試，用一個可設定回傳值的 fake anonymous.Service（pkg/services/anonymous/anontest/fake.go 已有），分別回 ErrDeviceLimitReached 與一個普通錯誤，斷言前者回 (nil, err)、後者仍然回出 identity。

</details>

<details>
<summary>Nit（1）</summary>

#### F-011 updateDevice 的命名、註解與參數型別和它實際的行為對不上 — `pkg/services/anonymous/anonimpl/anonstore/database.go:72`

面向 A 風格 · Nit

**問題**：三個小地方。(1) 函式叫 updateDevice，但 database.go:96 在沒有更新到任何列時回的是 ErrDeviceLimitReached——一個以「更新」命名的函式回傳「額度已滿」，讀者要往回追到 CreateOrUpdateDevice 才知道為什麼「找不到列」等於「超過上限」。(2) database.go:72 的註解寫 updates a device if it exists and has been updated between the given times，但這個函式沒有任何時間參數，視窗是它自己用 anonymousDeviceExpiration 算出來的（database.go:81）；註解也沒提到它會回 ErrDeviceLimitReached。(3) database.go:80 與 84 用 []interface{}，而同一個檔案其他地方（database.go:59、120、150）一律用 []any。

**證據**：
- `pkg/services/anonymous/anonimpl/anonstore/database.go:72`
- `pkg/services/anonymous/anonimpl/anonstore/database.go:80`
- `pkg/services/anonymous/anonimpl/anonstore/database.go:96`

**修復方向**：註解改成描述真正的視窗來源與回傳語意，例如 // updateDevice is used when the device limit is reached: it only refreshes a device that is already active within anonymousDeviceExpiration, and returns ErrDeviceLimitReached when there is no such row.；把兩處 []interface{} 換成 []any 對齊檔案其餘部分。若想更乾淨，可以讓它回 (rowsAffected int64, err error)，由 CreateOrUpdateDevice 決定 0 列該對應什麼錯誤——這樣函式名稱與行為就一致了。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 在 pkg/server/wire.go:374-375 沒有更新的情況下，CI 的 wire gen -tags oss ./pkg/server 是否仍然能成功產生 wire_gen.go？

面向 E 架構

**背景**：可從原始碼確認的部分已寫在 F-005：ProvideAnonDBStore 的簽章多了一個 int64，wire set 內沒有任何 provider 提供 int64；而 grep 全 repo，anonstore.AnonStore 在 pkg/services/anonymous/ 之外只剩 wire.go:375 那一行 wire.Bind 引用，代表這個 provider 已無消費者。剩下的關鍵是 google/wire v0.5.0 對「set 內存在但無人使用的 provider」是回報錯誤還是靜默忽略——若是前者，這是一個 CI build failure；若是後者，就只是死註冊。本機沒有 Go toolchain、沒有 wire 執行檔也沒有網路，無法實測，所以這一點不給 severity。

**如何確認**：在有網路的環境重現 .drone.yml:3770-3771 的步驟：go install github.com/google/wire/cmd/wire@v0.5.0 && wire gen -tags oss ./pkg/server，看它是否回報 unused provider 或 no provider found for int64。或者直接看這個 PR 的 CI 是否綠燈。

#### Q-002 在多實例 HA 部署下，device_limit 實際會超收多少？這個誤差對這個設定要解決的問題是否可接受？

面向 F 資料取用與資料庫

**背景**：F-003 只主張可從程式碼證實的事實：count 與 insert 之間沒有交易保護。但超收的幅度取決於實例數、匿名流量的到達率、每個實例各自那份 29 分鐘 localcache（impl.go:42）的命中分布，以及資料庫的一致性設定與延遲——這些都不是從這個 checkout 能判定的，本機也重現不了多實例環境。依 review-dimensions.md 的 F.6，環境相依而此處無法檢驗的行為留在這裡而不是變成一個帶 severity 的斷言。

**如何確認**：在 staging 用 N>1 個實例、device_limit=K，以並發匿名請求打進來，量測 anon_device 最終筆數相對 K 的偏差；或者由設計者說明這個設定被定位成 hard limit（授權/計費用途）還是 best-effort（容量保護用途）——若是後者，F-003 只需要文件而不需要交易。

</details>
