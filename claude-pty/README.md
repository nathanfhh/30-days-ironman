# claude-pty

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
cp .env.example .env      # 填 CLAUDE_PTY_SECRET_KEY / HOST_REPO_ROOT / DOCKER_GID / APP_UID
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
- GitLab token 同理：它不進 session 容器（見下），但那擋的是**容器裡的 AI**，不是營運者
  ——它與 CLI 憑證用同一顆資料庫、同一把 `SECRET_KEY` 導出的金鑰（只是各自不同的
  導出用途，兩者的密文不能互解）

## 憑證：每個人自己的 setup-token

每個人在自己的機器上執行 `claude setup-token`，把輸出貼到帳號管理頁。控制平面加密
存放，開場時以環境變數交給那一場的 CLI——誰的 session 用誰的憑證，host 上不需要
準備任何憑證檔。

**token 過期不會有預告。** 它不揭露自己的壽命，所以畫面上只有「已設定／未設定」，
沒有「剩幾天」。症狀是**新開的 session 停在登入提示、開不了場**；遇到就重跑一次
`claude setup-token`、把新的貼回帳號頁。已經在跑的 session 不受影響。

## GitLab：每個人一顆代理，token 不進 session

**預設關閉**，在 `deploy/.env` 填 `CLAUDE_PTY_GITLAB_HOST` 才會啟用（不含 `https://`
與結尾斜線）。沒填的話這一整套完全不存在——不建任何東西，帳號頁也不會出現這一塊。

開了之後，每個在帳號頁貼了 Personal Access Token 的人，會拿到**一顆自己的 nginx 與一張
自己的 docker network**，他所有的 session 都掛在上面：

```
network claude-pty-user-{id}  ┌ 他的 session A ─┐
                              ├ 他的 session B ─┼─► gitlab-proxy（alias）──► 你的 GitLab
                              └ 他的 session C ─┘   一顆，握他自己的 token
```

session 裡的 git 與 API 呼叫**不帶任何憑證**，由那顆代理蓋章。所以：

- **token 不進 session 容器**——不在環境變數、不在掛載、`docker inspect` 也看不到。
  容器裡的 AI 用得到你的 GitLab，但拿不走鑰匙
- **誰的 session 用誰的 token**，GitLab 那一端的操作紀錄記的是本人
- clone 用**正規網址就好**：`https://…` 與 `git@…` 都會被自動改寫成走代理
  （不要把 remote 寫成代理位址——那個名字只在 session 裡解得開）
- 直接打 API 的話，位址在容器的 **`NCR_GITLAB_API_BASE`** 環境變數裡（例如
  `curl "$NCR_GITLAB_API_BASE/api/v4/user"`）。**不要寫死** `http://gitlab-proxy:5678`
  ——alias 與埠都是部署者可以改的（見 `.env.example`），寫死之後改了就是靜默失效
- 只有白名單上的 API 端點通得過，其餘一律 403

### 為什麼是「每人一顆」而不是「每場一顆」

nginx 的限流計數桶是 **per-instance** 的。一場一顆的話，一個人開 N 場就有 N 個各自獨立
的桶——`10r/s` 對 GitLab 變成 `N×10r/s`，**限流形同虛設，而且「同時開很多場」正是它最該
發揮作用卻最失效的情境**。那不是把數字調小能修的（上限仍然是 `N×`），是拓樸問題。
完整推理見 [ADR 0016](docs/adr/0016-per-user-gitlab-proxy.md)。

### 換 token 的時候會發生什麼

分界線是**那一場開場時有沒有接上代理網路**，不是「帳號現在有沒有 token」：

| 你做的事 | 開場時接上了的 session | 開場時沒接上的 session |
|---|---|---|
| 換一把新的 | 一個對帳週期內改用新的 | 沒有效果 |
| 清除 | 代理被收掉，**當場**不能用 | 沒有效果 |
| 清除之後再填一把 | **會恢復**，用新的那一把 | 沒有效果 |

網路必須在容器啟動**之前**接上（防火牆放行的是那一刻的路由快照），所以最後一欄事後補
不上——設定 token 之前開的 session 要重開一場才有 GitLab。

⚠ 最容易誤解的是第三列。外洩時的自然反應是「清掉、換一把新的」，而那**不會**把舊的
session 關在外面：它們斷一個對帳週期，然後拿到新的 token。所以：

> **輪替 token 不等於隔離一場你不信任的 session。要隔離那場，就終止那場。**

### 代理起不來的時候

沒有代理**不會**讓 session 開不起來——GitLab 不通是這場少一個功能，不是這場沒用。
但降級不等於沉默：代理連續三輪起不來時，帳號頁會直接顯示 nginx 自己說的那句話。

這條訊號存在的理由很具體：設定錯的時候（最常見的是 `CLAUDE_PTY_GITLAB_HOST` 打錯或
DNS 解不到），nginx 在啟動時解析 upstream 就會拒絕啟動，於是每輪重啟、每輪再死。
使用者看到的症狀卻只是「GitLab 連不到」，會去查自己的 token——**而答案一直只在容器
log 裡**。把那句話端到畫面上，才不會讓人往完全錯的方向查半小時。

修好之後會自動恢復，訊息也會自己消失。

### 已知限制

- **不支援 git-lfs**：LFS 的 batch API 回的是外部 href（可能直指物件儲存），nginx 改不掉。
  有用 LFS 的 repo 會靜默壞掉
- **每個「同時有 session 在跑的 GitLab 使用者」佔一張 docker network**，而位址池是
  **整台機器共用**的（每個 compose 專案都佔一格）。沒有 session 就會回收，所以消耗量
  等於**同時在線**的人數；真的用完會在建立的當下報錯並講出下一步
- session 存活期間，裡面的東西仍能透過代理做**白名單允許的任何事**。買到的是「帶不走」
  與「範圍收斂」，不是「不能濫用」

> 這與 `gitlab-proxy/`（本 repo 的另一個目錄）是**兩套東西**：那顆是單人單機用的一顆
> 共用代理，這一套是多人環境用的、每個人自己一顆。兩者不必同時跑。
> API 白名單在兩邊各有一份，改一邊記得看另一邊。

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

GitLab 代理有兩支：`test_gitlab_proxy_conf.py`（離線，驗設定產生與「token 不進 session
容器」）與 `test_user_proxy.py`（需要 docker，真的建容器、真的熱重載、真的用
`docker inspect` 確認 token 沒外露）。後者不需要連得到任何 GitLab。
