## 審查結論：Approved with Comments

> Critical 0 · Suggestion 2 · Nit 2 · 未驗證提問 3
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ✅ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ✅ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ✅ | ✅ |

- **A 風格**（未通過）：命名與註解都清楚：entryPointAssetsCacheMu 一看就知道保護誰，webassets.go:36 的註解與 webassets.go:37 的 TODO 都在講「為什麼」而不是複述程式碼。唯一一處是 webassets.go:70 回傳值繞了一圈全域變數，見 F-004。
- **B 簡潔**（未通過）：沒有重複邏輯，也沒有為了不存在的需求建機制；改動幅度剛好對應問題大小。Dev 模式白拿一次讀鎖見 F-003。
- **D API 慣例**（不適用）：沒有新增或修改任何 HTTP endpoint、URL、HTTP verb 或 request/response schema。GetWebAssets 的簽章（webassets.go:40）與回傳型別完全不變，本維度的七條規則都沒有對應的檢查對象。
- **F 資料取用與資料庫**（未通過）：沒有資料庫存取；共用狀態的並行問題正是本次主題。核心結論：race 確實修掉了——指標的讀寫都在 RWMutex 內，而發佈到快取的 *dtos.EntryPointAssets 在發佈之後是唯讀的（證據見 I 維度），所以「只保護指標、不保護指向的物件」這個常見漏洞在這裡不成立。扣分在 double-checked locking 少了第二次檢查，見 F-001。另註：每個 Grafana replica 各自持有一份快取，但 manifest 是 build 產物、同一版本內容相同，不構成分散式一致性問題。
- **G 測試**（未通過）：diff 沒有動任何測試檔。webassets_test.go 現有兩個測試都不經過 GetWebAssets，被修的那條路徑零覆蓋，見 F-002。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| Go toolchain（go build / go vet / go test -race） | 略過 | 本執行環境沒有 Go toolchain 也沒有網路，無法編譯、無法執行既有測試、無法跑 race detector。本次變更正是一個併發修正，所以它沒有得到任何自動化驗證——以下每一條結論都來自人工閱讀原始碼與 grep，請以此為前提評估可信度。 |
| ruff | 已執行 | 有實際執行並取得 exit code，但本次 diff 只有 Go 檔案，而 ruff 只檢查 Python，因此對受審程式碼的覆蓋率是零，不能當成「掃過了、乾淨」。10 件診斷全部落在 diff 之外的既有 Python 檔，屬專案既有問題，不列入本次。 · in_diff 0、outside_diff 10 |
| ty | 略過 | 未安裝（不在 PATH 上）。即使安裝，本次 diff 沒有 Python 檔，也不會有覆蓋。 |
| oxlint | 略過 | 未安裝（不在 PATH 上）。本次 diff 沒有 JavaScript/TypeScript 檔。 |
| trivy | 略過 | 未安裝（不在 PATH 上），略過相依套件漏洞、設定錯誤與 secret 掃描。本次 diff 沒有動 go.mod / go.sum，供應鏈面向的變動為零，但這不等於已掃描。 |
| opengrep | 略過 | 未安裝，且 NCR_OPENGREP_RULES 指向的規則目錄不存在，兩個條件都不成立，略過 SAST。 |
| codegraph | 略過 | 未安裝。Phase 3 的呼叫端列舉與影響分析全部改用 grep 逐一窮舉，結果記在 I 維度的 note 與 F-001 的 counter evidence 裡。 |
| ncr-fresh-eyes（subagent） | 略過 | 本執行環境沒有可用的 Agent／Task 工具，無法派出 subagent。SKILL.md Phase 3 明訂不得由主 agent 自行模擬 fresh eyes，因此如實略過並揭露：本報告少了一雙沒有被本 skill 分類框架塑形過的眼睛。 |
| ncr-quality-check（subagent） | 略過 | 同上，無法派出 subagent。報告 JSON 只通過 report_model.py 的機械驗證，沒有經過第二個 agent 的品質複核。 |

<details>
<summary>Suggestion（2）</summary>

#### F-001 取得寫鎖後沒有重新檢查快取：冷啟動時每個並行 request 都會各自重建一次 assets manifest — `pkg/api/webassets/webassets.go:41`

面向 F 資料取用與資料庫 · Suggestion

**問題**：這是 double-checked locking 少了第二次檢查。webassets.go:41-43 以 RLock 讀出 ret；當快取還是 nil（程序剛啟動）時，所有同時進來的 goroutine 都會通過 webassets.go:45 的判斷、一起排隊搶 webassets.go:48 的寫鎖。問題在於取得寫鎖之後沒有再讀一次 entryPointAssetsCache，於是每一個 goroutine 都會完整跑一次 webassets.go:60 的 readWebAssetsFromFile（開檔 + JSON decode），再於 webassets.go:69 把彼此的結果互相覆寫。結果本身不會錯，race 也確實修掉了；但快取在最需要它的那一刻——程序剛起來、request 一次湧入——等於沒有作用，N 個並行 request 就做 N 次檔案 I/O 與 N 份配置。這個成本原本正是加快取要省掉的。

**證據**：
- `pkg/api/webassets/webassets.go:41`
- `pkg/api/webassets/webassets.go:45`
- `pkg/api/webassets/webassets.go:48`
- `pkg/api/webassets/webassets.go:60`
- `pkg/api/webassets/webassets.go:69`

**修復方向**：在 Lock() 之後、開始重建之前補上第二次檢查。條件要沿用 Dev 的例外，因為 Dev 本來就要每次重讀：

```go
entryPointAssetsCacheMu.Lock()
defer entryPointAssetsCacheMu.Unlock()
if cfg.Env != setting.Dev && entryPointAssetsCache != nil {
    return entryPointAssetsCache, nil
}
```

若想連 F-003 一併解決，另一個方向是把非 Dev 路徑改成 sync.Once 或 atomic.Pointer[dtos.EntryPointAssets]；同 repo 已有 sync.Once 的先例（pkg/api/avatar/avatar.go:58、pkg/api/plugin_proxy.go:18），差別是 sync.Once 需要額外處理「第一次就失敗、之後要不要重試」的語意，目前 webassets.go:69-70 的行為是失敗不快取、下次重試，改寫時要保留。

#### F-002 修的是 data race，但沒有附上任何併發測試，被修的路徑目前零覆蓋 — `pkg/api/webassets/webassets.go:40`

面向 G 測試 · Suggestion

**問題**：本次 diff 只動 pkg/api/webassets/webassets.go 一個檔案，webassets_test.go 沒有跟著改。現有兩個測試 TestReadWebassets（webassets_test.go:11）與 TestReadWebassetsFromCDN（webassets_test.go:89，而且開頭就是 t.Skip()）都只直接呼叫 readWebAssetsFromFile / readWebAssetsFromCDN，完全沒有經過 GetWebAssets——也就是說被修正的那條路徑、以及新加的那把鎖，現在沒有任何測試碰得到。日後有人把鎖拿掉、把 RLock/Lock 順序調換，或依 F-001 重寫成 sync.Once 時改壞，不會有任何東西擋下來。

**證據**：
- `pkg/api/webassets/webassets.go:40`
- `pkg/api/webassets/webassets_test.go:11`
- `pkg/api/webassets/webassets_test.go:89`

**修復方向**：加一個測試：用 sync.WaitGroup 開 N 個 goroutine 同時呼叫 GetWebAssets，cfg.StaticRootPath 指向一個含 build/assets-manifest.json 的暫存目錄（可直接複製 pkg/api/webassets/testdata/sample-assets-manifest.json），license 用既有 fake；測試前後把 entryPointAssetsCache 重設乾淨以免污染同 package 的其他測試。同一個測試順手斷言 manifest 只被讀取一次（例如把讀檔包成可注入的函式並計數），就能把 F-001 一起釘住。另外提醒：Makefile:16 的 GO_RACE_FLAG 是 opt-in（要 GO_RACE=1 或 .go-race-enabled-locally 才會帶 -race），這個 checkout 裡也找不到啟用它的 CI 設定，所以請在 PR 說明註明此測試需要在 -race 下執行才有偵測效果。

</details>

<details>
<summary>Nit（2）</summary>

#### F-003 Dev 模式下白取一次讀鎖 — `pkg/api/webassets/webassets.go:41`

面向 B 簡潔 · Nit

**問題**：webassets.go:41-43 不分模式一律先取 RLock 把 ret 讀出來，但 webassets.go:45 的條件是 cfg.Env != setting.Dev && ret != nil——在 Dev 模式下 ret 讀了也用不到，接著馬上又要取一次寫鎖。cfg.Env 在一個 process 的生命週期內是固定的，所以這對 Dev 是每個 request 都多做一次 lock/unlock 的固定成本，也讓讀的人多花一秒才確認「這個讀取在 Dev 下是沒有意義的」。

**證據**：
- `pkg/api/webassets/webassets.go:41`
- `pkg/api/webassets/webassets.go:45`

**修復方向**：把模式判斷提到讀鎖前面，讓 Dev 完全不進快取路徑：

```go
if cfg.Env != setting.Dev {
    entryPointAssetsCacheMu.RLock()
    ret := entryPointAssetsCache
    entryPointAssetsCacheMu.RUnlock()
    if ret != nil {
        return ret, nil
    }
}
```

#### F-004 return entryPointAssetsCache, err 可以直接回 result — `pkg/api/webassets/webassets.go:69`

面向 A 風格 · Nit

**問題**：webassets.go:69 剛把 result 寫進 entryPointAssetsCache，webassets.go:70 又把同一個全域變數讀回來當回傳值。行為上是對的（寫鎖此時還握著），但讀的人得先往上確認「這一行真的還在鎖的範圍內」才能放心，而 result 是同一個值、不需要這層確認。少一次全域變數的讀取，之後真要處理 webassets.go:37 的 TODO 時也少一處要改。

**證據**：
- `pkg/api/webassets/webassets.go:69`
- `pkg/api/webassets/webassets.go:70`

**修復方向**：改成 `return result, err`。

</details>

<details>
<summary>未驗證提問（3）</summary>

#### Q-001 Dev 模式下每個 request 都握著獨占鎖做一次開檔加 JSON decode，這個序列化成本對 dev server 可以接受嗎？

面向 F 資料取用與資料庫

**背景**：webassets.go:45 的快速路徑對 Dev 一律不成立，所以 Dev 的每一個 request 都會走到 webassets.go:48 的 Lock()，並在鎖內執行 webassets.go:60 的 readWebAssetsFromFile。改動前這段完全沒有鎖，所有 request 並行跑（那也正是被修掉的 race）。序列化是修正的必要代價，本身不是錯；但代價多大取決於 manifest 大小與 dev 的請求併發度，而這個審查環境沒有 Go toolchain 也沒有網路，無法編譯、無法量測，所以不給嚴重度。

**如何確認**：在 dev 設定下對首頁與 frontend settings 這兩個 endpoint 做一次並行壓測，或針對 GetWebAssets 寫個 go test -bench，比較改動前後的 p99 延遲。若成本顯著，Dev 路徑可以改成不進全域鎖、每次自己重建一份回傳（Dev 本來就不共用快取，不需要互斥）。

#### Q-002 webassets.go:54 那行寫死的 cdn := "" 是否有啟用計畫？若有，在獨占鎖內對外做 HTTP 請求可以接受嗎？

面向 E 架構

**背景**：已確認 webassets.go:54 的 cdn 是寫死的空字串，因此 webassets.go:56 的 readWebAssetsFromCDN 目前不可能從 GetWebAssets 走到——正因為這條路徑是死的，這裡不列為 finding。但它使用的是呼叫端傳進來的 request context（webassets.go:85-90 的 http.NewRequestWithContext + http.DefaultClient），一旦有人把那行常數換成真的 CDN URL，就會變成在全域獨占鎖內對外發 HTTP 請求，而且逾時與取消由某一個使用者的 request 生命週期決定：那個使用者一斷線，整批等鎖的 request 都會拿到失敗。這是本次加鎖之後才成立的耦合，值得在啟用前先講清楚。

**如何確認**：作者說明這行常數是暫時的實驗殘留還是有啟用路線圖。若要啟用，該路徑應改用獨立的 context（context.WithTimeout(context.Background(), ...)）與帶逾時的 http.Client，並把網路 I/O 移到鎖外面再以單次交換寫入快取。

#### Q-003 grafana-enterprise 或其他不在本 repo 的進入點，會不會在開始服務流量前先呼叫一次 GetWebAssets 把快取暖起來？

面向 F 資料取用與資料庫

**背景**：這是 F-001 的反證搜尋剩下的缺口。F-001 的前提是「冷啟動時會有多個 request 同時打進來」。在本 repo 內已窮舉：entryPointAssetsCache 只出現在 webassets.go，GetWebAssets 只有 pkg/api/index.go:82、pkg/api/frontendsettings.go:62、pkg/middleware/recovery.go:141 三個呼叫端，全部在 HTTP handler 內，沒有啟動時的暖機呼叫。但 grafana-enterprise 是另一個 repository，這個環境讀不到，所以無法宣稱窮舉完整。

**如何確認**：在 enterprise repo grep 一次 GetWebAssets。若那邊有啟動時的暖機呼叫，F-001 的實際影響會小很多（但補上第二次檢查依然是對的，因為那個保證不在這個 repo 裡，也沒有任何東西擋著它消失）。

</details>
