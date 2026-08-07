# ADR 0006：session 執行 profile——以 env 非互動驅動 entrypoint.sh，保留 SSOT

- 狀態：已接受；已實作

## 背景

`dev-container/entrypoint.sh`（人直接跑容器時用的那條路）把幾個執行期能力藏在互動選單
後：CLI 選擇、網路能力（限制出網的 iptables 白名單）、流量錄製（mitmproxy）、telemetry
（OTEL → Jaeger）。控制平面（[ADR 0004](0004-flask-control-plane.md)）要自動開 session，
不能停在互動選單前等鍵盤。

要補回這些能力有兩條路：**A** 讓 entrypoint.sh 支援非互動模式、控制平面把選擇餵進去；
**B** 在控制平面（Python）重刻一遍 cap_add / network / mitm 編排 / firewall 呼叫。B 會把
entrypoint.sh 那套充滿 scar 知識的邏輯複製到 Python，兩邊得同步維護——違反 SSOT 紀律。

## 決策

**採路線 A，分兩層。**

### 第一層：entrypoint.sh 增加 env 驅動的非互動模式

- 每個原本 `read -r -p` 的選單改為：**對應 env 有設 → 用它並跳過 `read`；未設 → 維持原
  互動 `read`**。人直接跑容器的路徑因此**完全不變**（未設 env 即現行行為）。
- 選擇用 **environment variable** 傳遞，不用 command-line args——argv 會與 CLI 自身參數
  衝突，而 env 是這個 entrypoint 既有的設定通道，且「未設 → 退回互動」語意乾淨。
- 實作約束：env-skip 必須**嚴格加法**（未設 env 時逐字元等同互動行為），否則會動到人
  直接跑容器的工作流。

**模型與思考深度不出選單**：它們走同一套「有值才動作」，但刻意沒有互動選單——只在 env
有值時把 `--model` / `--effort` 加進 argv，沒設就一個字都不加。理由是這兩題與前面幾題
性質不同：前面幾題決定容器**怎麼被建出來**，模型與思考深度是 CLI 自己的執行參數，
CLI 有預設、進去也能隨時改。

> **不 probe「這一版有沒有這些旗標」——這條刻意不做，記在這裡免得有人加回來。** 曾經想
> 在 exec 前跑一次 `--help`、沒有那個旗標就降級略過，防的是「旗標改名 → 每場 session
> 一建立就死」。它的帳是這樣結的：session container 的 stdin 是 TTY，而 `--help` 在 TTY
> 下不返回 → probe 在正式環境**每次都失敗**、旗標從來沒被加上去，而降級路徑讓一切看
> 起來正常；外加把整個容器啟動卡住。為假想的失敗模式付出真實複雜度的典型——旗標真的
> 消失時症狀是 session 建立失敗，那是看得見的，不需要事先偵測。

值的合法性不靠 shell 驗，靠**結構上注入不進來**：值以帶引號的陣列元素 append，
`run_driver` 是 `exec "$@"`，任意字串只會變成恰好一個 argv 元素——沒有 word splitting、
塞不進第二個旗標。控制平面的 enum 白名單是唯一的值檢查。
⚠ 這道保證來自 shell 的引號紀律——哪天有人為了「支援完整模型名稱」把 append 改成字串
拼接，它就沒了。

### 第二層：控制平面補上 docker 層能力（env 給不了的部分）

`build_run_kwargs` 依 profile 補上：防火牆需要的 `cap_add=["NET_ADMIN"]` + session
network；錄製要 mount 的 addon；telemetry 的 OTEL env。env 只能「答選單」；`NET_ADMIN`
這類能力必須由控制平面在 docker 層授予——兩層缺一不可。

### API 面：per-session profile

create 端點接受 `profile`：`{"network": "restricted|unrestricted", "capture": bool,
"telemetry": bool, "model"?, "effort"?}`，控制平面據此組出「docker flags + env」。

## 後果

- **SSOT 保住**：firewall/mitm/otel 的 how 只在 entrypoint.sh + init-firewall.sh 一處，
  控制平面只決定 what（profile）並授予 docker 能力。
- **跨關注點改動**：需編輯 `dev-container/entrypoint.sh`（與人直接跑容器的路徑共用同一
  檔），故 env-skip 的「嚴格加法、未設即原行為」是硬約束。
- **權限輪廓上升**：restricted profile 需 `NET_ADMIN`，由控制平面 per-profile 授予（非
  全域預設），符合最小權限精神。
