# agent-tty（claude-pty）

多人共用的網頁終端控制平面：每個人登入後開自己的 session（一場 = 一顆 container），
在瀏覽器裡直接操作容器內的 Claude Code。斷線、關瀏覽器、換一台電腦，回來還是同一場
——PTY 由 dockerd 持有，網頁只是一張隨開隨丟的臉。

## 架構一覽

```
瀏覽器 ── nginx ──┬── Flask 控制平面 ──── docker socket ──── session 容器（一場一顆）
                  │      （帳號/授權/生命週期；SQLite 是唯一的狀態仲裁者）
                  └── ttyd（on-demand，開網頁才起）──── docker attach ──── 同一顆容器
```

兩條資料路徑刻意分開：控制流（開場、終止、帳號）走 Flask；終端資料流走
nginx → ttyd → `docker attach`，**不經過 Flask**。授權掛在 nginx 把連線交給 ttyd
之前那一刻（`auth_request`），ttyd 端還有第二層（`--auth-url`，見下）。

設計決策的完整記錄在 [`docs/adr/`](docs/adr/)。

## 部署

```bash
cd deploy
cp .env.example .env      # 填 SECRET_KEY / HOST_REPO_ROOT / DOCKER_GID / APP_UID
./redeploy.sh             # build + 起 control / reconciler / nginx
docker compose exec control python -m server.cli create-admin alice   # 第一個管理員
```

之後改了 `server/` 就再跑一次 `./redeploy.sh`；只改 nginx.conf 之類用 `--no-build`。

### 要部署在哪裡

一句話版本：**專用的機器，或至少一台專用的 VM。**

- 控制平面掛著 docker socket——這是刻意的、被記錄的決定（[ADR 0009](docs/adr/)），
  代價也寫在同一份決定裡：**「容器化買到的是部署一致性，不是安全邊界。」**
  拿得到那個 socket 就等於拿到 host root，所以這台機器上不該有你輸不起的其他東西
- VM 多買一層真的邊界：session 逃逸或控制平面被拿下，爆炸半徑停在 VM
- rootless docker 是**還沒做、會去做**的方向：它能把「socket ≈ host root」降級成
  「socket ≈ 那個使用者」。列在這裡是誠實標示現況，不是宣稱
- 兩個**不算邊界**的東西，別誤當保護：
  - 「控制平面程式碼保持精簡」是紀律不是機制——它降低出錯機率，擋不住已經出的錯
  - docker socket proxy 只能按 API 類別過濾，而這套系統**本來就要**建容器、掛目錄，
    proxy 擋不住「建一顆帶特權掛載的容器」這種在放行類別內的濫用

## 安全邊界：先講清楚，再談功能

**開帳號給誰，就等於請他信任你。** 這套系統的營運者（拿得到部署機、`.env`、資料庫
的人）在每一個使用者的信任鏈上，這件事沒有架構解得掉，只能講明：

- 使用者貼進來的 CLI 憑證加密存在同一顆資料庫，金鑰由**同一把 `SECRET_KEY`** 導出
  ——拿到 `.env` 加資料庫就能解開全部。換掉 `SECRET_KEY` 則所有人的憑證一起解不開
  （會降級成「未設定」請人重貼，但不會有人自己發現，得由你去通知）
- **管理員可以拿走任何人的帳號**：代改密碼＝對方全面登出、開著的終端被切斷、登不
  回來——那正是這套系統唯一的退場機制，也同時是一個要交給管理員的權力
- session 之間有隔離（一場一容器、per-user 狀態空間、擁有權檢查），但**隔離不等於
  營運者碰不到**。per-user 空間就在部署機的檔案系統上，transcript 與流量錄製都在裡面

## 憑證：每個人自己的 setup-token

每個人在自己的機器上執行 `claude setup-token`，把輸出貼到帳號管理頁。控制平面加密
存放，開場時以環境變數交給那一場的 CLI——誰的 session 用誰的憑證，host 上不需要
準備任何憑證檔。

**token 過期不會有預告。** 它不揭露自己的壽命，所以畫面上只有「已設定／未設定」，
沒有「剩幾天」。症狀是**新開的 session 停在登入提示、開不了場**；遇到就重跑一次
`claude setup-token`、把新的貼回帳號頁。已經在跑的 session 不受影響。

## 終端程式：兩顆 ttyd，差異不是快慢

每個終端由一顆 ttyd 程序服務。這套系統帶兩顆、由每個使用者自選（帳號選單 →
設定 → 終端程式；只影響之後開的終端，正在跑的不會被換掉）：

| | C 版（上游） | Rust 版（fork） |
|---|---|---|
| 來源 | [tsl0922/ttyd](https://github.com/tsl0922/ttyd) release 1.7.7，binary 以 SHA-256 釘死 | [nathanfhh/ttyd](https://github.com/nathanfhh/ttyd) 的 Rust 重寫，釘 commit（見 `deploy/Dockerfile` 的 `TTYD_RUST_REF`） |
| 網頁標題 | **只有畫面遮蔽**（`titleFixed`，client 選項）：瀏覽器被要求顯示別的字，但**真正的標題——完整命令列加容器主機名——在那之前就已經送給每一個連上的 client** | **伺服器端換掉**（`--title`）：宣告出去的標題只剩固定字樣加該場編號，**命令列一個字都不上線** |
| 第二層授權 | 無（只有 nginx 的 `auth_request` 那一層） | `--auth-url`：ttyd 自己在放行每個請求前，再問一次控制平面「這個人能不能看這一場」（與 nginx 那層是縱深，不是重複） |

**標題那一列是重點。** 分頁標題會進瀏覽紀錄、工作階段同步、截圖。C 版的 `titleFixed`
做到的是「畫面上看不到」，不是「沒有送出去」——選 C 版，這個洩漏面就存在，只是被
蓋住。選 binary 的人應該知道自己在選什麼。

兩件容易想錯的事，寫在這裡省得下一個人重試：

- **C 版對它沒有的旗標是靜默忽略，不是拒絕啟動。** 把 `--title` 塞給 C 版不會炸，
  終端照常開，只是那道保護根本沒生效、也沒有任何錯誤。所以「哪顆 binary 拿到哪些
  旗標」由控制平面的參數策略保證（`server/views.py` 的 `_TTYD_EXTRAS`），不是靠
  「塞錯會壞」防呆。
- **差異在 build 時就被釘死。** `deploy/Dockerfile` 在編出 Rust 版之後、搬進 image
  之後，各對真的 binary 斷言一次：Rust 版的 `--help` 必須列出
  `--title` / `--auth-url` / `--auth-cache-ttl`（缺任一支 build 直接失敗），
  C 版必須沒有 `--title`。fork 哪天改了旗標，會在 build 當場現形，不會等到
  執行時靜默少一層。

### Build 時間

Rust 版沒有預編 binary 可下載：image 的第一階段用 `cargo build --release --locked`
從釘死的 commit 現編。**第一次 build 會久**（Rust 編譯，數分鐘起跳）；之後只要
`TTYD_RUST_REF` 不變，這一層會命中 Docker cache，幾乎不花時間。

## Telemetry（Jaeger）

開場時可以選擇把該場 CLI 的 trace 送到 Jaeger（OTLP gRPC，endpoint 預設
`http://jaeger:4317`，`CLAUDE_PTY_OTEL_ENDPOINT` 可改）。

- **Jaeger 的服務定義不在這個 stack 裡**——它以 external network 引用 session
  network，所以順序是：先起這個 stack（控制平面會把 network 建好），再起 Jaeger。
  反過來 Jaeger 起不來，而 OTLP 是 fail-open，trace 會靜默消失
- **探不到就降級，不擋開場**：建立 session 時控制平面先探一次 Jaeger，探不到就
  不設 telemetry 環境變數（不送去一個沒人接的地方），session 照開——telemetry
  是觀察不是控制，不能為了它讓人開不了場
- **紀錄說實話**：歷史列表的座標分三態——真的在送／要求了但沒開成（建立時探不到，
  已降級）／不送。那個座標的用途是事後比對，所以不准說謊

## 貼圖 / 檔案上傳

PTY 是字元流，圖片拖不進終端。補法：終端抽屜的迴紋針鈕（或直接在終端裡貼上圖片）
會把檔案上傳到你的持久化目錄，容器內路徑自動進剪貼簿，貼給 AI 讓它自己讀。這是
唯一一條使用者能往伺服器寫東西的路，副檔名白名單、大小上限、路徑穿越防護都收在
那一個端點上。

## 測試

```bash
tests/run-all.sh          # 快速組（不需要 docker）
tests/run-all.sh --all    # 全部（需要 docker；ttyd 在 PATH 上則含真終端測試）
```
