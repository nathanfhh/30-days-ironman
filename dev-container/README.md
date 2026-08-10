# Dev Container

一個可拋棄的容器，裡面裝好 Claude Code 與審查會用到的掃描工具。用它跑 code review
的理由有兩個：**環境可重現**（掃描器版本固定，報告不會因為誰的機器而不同），
以及**邊界**（agent 拿得到的東西由你決定，而不是「我的整台電腦」）。

```
Dockerfile                  image 定義
entrypoint.sh               容器啟動後做的事（含網路能力選單）
init-firewall.sh            限制模式套用的 iptables 白名單
run-ncr-dev-container.sh    啟動 wrapper：憑證、SSH、規則怎麼進容器（偵測到 Jaeger 就配置 telemetry，送不送在啟動選單確認）
```

觀測那一掛（Jaeger、每角色時間/成本報表、場次報表頁）在 `../opentelemetry/`，
wrapper 偵測到 Jaeger 在跑就配置錄製、啟動選單再確認要不要送，細節見該資料夾的 README。

## Build

```bash
cd dev-container
docker build -t ncr-dev-container .
```

### Linux：把容器內的 uid 對上你自己

macOS / Docker Desktop 不用管這一段（bind mount 的擁有者會被對映），**原生 Linux 要**：

```bash
docker build --build-arg NCR_UID=$(id -u) -t ncr-dev-container .
```

容器內的 `nathan` 預設是 **1001**——那不是誰決定的數字，是 `ubuntu:24.04` 自己佔走了
1000（使用者名 `ubuntu`），`useradd` 只好往下拿。而 Linux 的 bind mount **不做 uid 翻譯**，
所以那個號碼要是跟你的 `id -u` 不同，容器就寫不進你掛進去的東西：

- `~/ncr`（審查報告的 archive）寫不下
- ssh-agent 的 socket connect 不了（`Permission denied`，見下方疑難排解）
- `~/.claude/.credentials.json` 是 0600，讀不到

`NCR_UID=1000` 時 build 會先把 base image 自帶的 `ubuntu` 帳號移掉再建
（不移的話 `useradd` 會直接 `UID 1000 is not unique` 失敗）。那個帳號名下只有
`/home/ubuntu` 與三個 skeleton dotfile，移掉不留孤兒。

> 用 claude-pty 開容器的人：這個值要與 `deploy/.env` 的 `APP_UID` **相同**，
> 兩邊都等於你的 `id -u`。控制平面啟動時會檢查並把三個數字報出來。

### 選配：告訴 image 你的 GitLab 在哪

```bash
docker build --build-arg GITLAB_SSH_HOST=gitlab.example.com -t ncr-dev-container .
```

這個 ARG 餵兩個地方：

1. **`known_hosts`** — build 時 `ssh-keyscan` 那台主機，把 host key 寫進 image。
2. **防火牆** — 寫進 `/etc/ncr/gitlab-ssh-host`（root 所有、0444），限制模式下
   `init-firewall.sh` 讀它，只放行通往那台主機的 SSH。

**不給就兩段都跳過**：沒有烘 host key（改用 run wrapper 掛 host 的那份），
限制模式下也不開放任何 SSH outbound。

⚠ **這個 ARG 只決定「通往哪一台」，不決定「開不開」。** 22 的放行還有另一個條件：
**ssh-agent 真的被轉發進來**。沒有 agent 的容器一個 SSH 出口都沒有，即使這個 ARG
給了——那個 port 當初進白名單就是為了服務 agent，沒有 agent 時留著它不會讓任何事情
變得可能。判準是 `/ssh/ssh_sock` 這個 socket 在不在，不是「誰啟動了這個容器」：
同一件事有兩個來源就會漂。

什麼時候需要它：CI、或者全新的機器上沒有 `~/.ssh/known_hosts` 可以掛。
一般情況不需要，run wrapper 會掛 host 上現成的那份，而且**掛載會蓋過烘進去的**。

⚠ 為什麼不預設烘：`ssh-keyscan` 是 TOFU（第一次連到就信任），而 build time 正是你
最沒有能力驗證那把 key 的時間點。被中間人就把假的 host key 永久烘進 image，外表
還看起來像「已經設定好了」。另外內網的 GitLab 在 build 機器上通常也打不到——
那種情況下這段會印警告然後跳過，不會擋住 build。

## Run

在**要審查的專案根目錄**執行：

```bash
/path/to/run-ncr-dev-container.sh
```

當前目錄會被掛進容器的 `/home/nathan/code-review`。

啟動後第一個問題是**網路能力**（見下方〈網路邊界〉），接著 wrapper 幫你處理三件事：

### 1. Claude Code 憑證

依序嘗試三個來源，都沒有就直接退出，不啟動一個註定登不進去的容器：

| 優先序 | 來源 | 說明 |
|---|---|---|
| 1 | `CLAUDE_CODE_OAUTH_TOKEN` | 設了就直接透傳，不碰 Keychain |
| 2 | macOS Keychain | 解出成 `~/.claude/.credentials.json`，**退出時自動刪除** |
| 3 | `~/.claude/.credentials.json` | Linux host 登入過就有 |

第二種第一次執行會跳出 Keychain 授權視窗，按「允許」。

### 2. git 的 SSH 憑證

> **先確認你需不需要它。** `nathan-code-review` 這個 skill 取程式碼是走 **HTTPS**
> （`git clone -c http.extraHeader="PRIVATE-TOKEN: …" https://…`，見
> `references/workspace-paths.md`），**不走 SSH**。所以只跑這個 skill 的話，
> 整節可以跳過，直接用 `NCR_NO_SSH_AGENT=1` 啟動——少開一個授權面。
>
> 會需要它的情況：你在容器裡手動做 git over SSH（push、clone 別的 repo），
> 或改用 SSH 取程式碼。下面那個爆炸半徑的警告，就是為這些情況寫的。

wrapper 把 host 的 **ssh-agent socket** 掛進容器，而不是把 `~/.ssh` 掛進去。
差別是「能力」與「秘密」：容器只能請 agent 幫忙簽章，拿不到私鑰本體；掛目錄則是
把一把沒有 scope、也沒有到期日的長效私鑰整個交出去。

host 上沒有 agent 的話，wrapper 會臨時起一個、載入預設金鑰，並在退出時關掉
（只關自己起的那個；你原本就有的 agent 不會被動到）。

⚠ **爆炸半徑跟 CLI 憑證不同。** CLI 憑證外洩，別人能拿你的訂閱去問模型；SSH agent
外洩，別人能以你的身分登入**所有信任那把 key 的主機**——內網 git、正式機、跳板機。
而容器篩不掉 agent 裡的任何一把 key。

也因為如此，限制模式下的 22 是**跟著 agent 走的**：用 `NCR_NO_SSH_AGENT=1` 啟動
（或 host 上根本沒有 agent）時，防火牆連那台 GitLab 的 22 都不會開。少一個授權面，
也少一個「以為關了其實還開著」的落差。

要限縮，在 host 端做：

```bash
# 另起一個只放受限 key 的 agent，再啟動容器
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519_gitlab_only
/path/to/run-ncr-dev-container.sh
```

完全不要轉發：

```bash
NCR_NO_SSH_AGENT=1 /path/to/run-ncr-dev-container.sh
```

`known_hosts` 也會被唯讀掛進去。沒有它的話，容器裡第一次 git over SSH 不會問你
要不要信任，而是直接 `Host key verification failed.`——非互動環境裡，需要人按 yes
的東西等於失敗。host 上沒有這個檔案時，wrapper 會印出補上的指令。

### 3. Opengrep 規則

`opengrep` 不內建規則，從 host 的 clone 餵進去：

```bash
git clone --depth 1 https://github.com/semgrep/semgrep-rules.git ~/semgrep-rules
```

路徑跟 `install.sh` 宣告的 `NCR_OPENGREP_RULES` 同一個（預設 `$HOME/semgrep-rules`）。
clone 在別的地方就 `export NCR_OPENGREP_RULES=<你的路徑>` 再啟動 wrapper；
容器內的掛載點固定是 `/home/nathan/semgrep-rules`，不隨 host 路徑變。

`--depth 1`：掃描只讀工作目錄，歷史一次都用不到，而 semgrep-rules 的歷史比工作目錄
本身大得多。shallow clone 不影響下面的更新——`pull --ff-only` 照樣 fast-forward，
不會被迫 unshallow。

wrapper 啟動前會 best-effort `git pull`（離線就沿用現有版本），再唯讀掛進容器。
找不到 clone 只會警告，容器照常啟動，那一場的 SAST 軌道無規則可用。

### 4. Trivy 弱點資料庫

`trivy` 跟 opengrep 是同一種形狀：binary 不帶資料，第一次掃描才去 ghcr.io 抓
弱點 DB（下載約 60MB，解開後落地超過 1GB）。這件事不能留給審查容器自己做——
容器用完即丟（每場重抓重解一次），而且限制模式的白名單裡沒有 ghcr.io（牆內根本抓不到）。

所以 DB 也由 host 供給，而且全自動、不用像規則那樣先手動 clone：wrapper 啟動前
先在**牆外**用一個一次性容器 `trivy image --download-db-only` 更新
`~/.cache/ncr-trivy`，再把這個目錄掛進審查容器。更新失敗就沿用既有 DB；
連既有 DB 都沒有，那一場的供應鏈軌道會被 skip 並在報告中揭露。

cache 掛的是讀寫（trivy 會往同一個目錄寫掃描的分析結果）。DB 的完整性不靠唯讀，
靠順序：更新發生在防火牆之外，審查容器在牆內連不到 ghcr.io，改不了 DB 的來源。

目錄刻意不共用 host 自己的 `~/.cache/trivy`——host 若也裝著 trivy，兩邊版本不同時
DB schema 可能不相容，隔離開來誰也不會弄壞誰。

### 5. Telemetry（選配）

觀測整組（Jaeger 收集端、每角色時間/成本報表、場次報表頁）住在 `../opentelemetry/`。
wrapper 啟動時偵測到 jaeger 容器在跑、**且 `gitlab-proxy` network 存在**，才自動注入
OTEL 環境變數並開錄（只送 traces、`NCR_EXPERIMENT` 標實驗代號）。兩個條件缺一就完全不碰
——jaeger 掛在那張 network 上，沒接上網的容器連 `jaeger:4317` 也連不到，這種情況
wrapper 會明講並教你重建。啟動收集端與輸出報表的方式見 `../opentelemetry/README.md`。

### 6. 流量錄製（選配）

工具與報表住在 `../mitm/`，那邊的 README 講脫敏規則與報表怎麼讀；這裡只講容器這側
的接線。

容器啟動時會問第三題，**預設不錄**；答應了才問範圍，**範圍預設全部**：

```
錄製本場流量？（mitmproxy）
  y = 錄，落在 ~/ncr/mitm/<session-id>/（脫敏後）
  n = 不錄（預設）

錄製範圍：
  1 = 全部流量（預設） — 憑證裝進容器的系統信任庫，proxy 進關鍵路徑
  2 = 只錄模型 API     — 只收 api.anthropic.com，其餘直連不經過 proxy
```

範圍預設全部，因為這份紀錄要回答「有沒有東西走漏」——只錄自己允許的那一條，
拿來說「沒有別的」是循環論證。非互動用 `NCR_CAPTURE_SCOPE=all|model`。

答 y 之後，entrypoint 在容器內起一個 mitmweb，把 `HTTPS_PROXY` 指過去、用
`NODE_EXTRA_CA_CERTS` 讓 Claude Code 信任它現產的根憑證，並印出檔案位置與
即時畫面的網址（帶一次性 token）。非互動環境用 `NCR_CAPTURE=1`。

幾個接線上的決定：

- **不開新的 mount。** capture 寫進 `~/ncr/mitm/`，沿用報告 archive 那個既有的
  bind mount。容器是 `--rm` 的，不掛出來就跟著容器一起消失。
- **即時畫面的 host port 動態挑**（40000–40100 找一個沒被占用的），固定 8081 的話
  同時開兩個容器第二個就起不來。這個 port **一律發布**，即使這一場選了不錄——
  published port 是 `docker run` 的啟動參數，事後加不上去，而要不要錄是進容器
  之後才問的。只綁 `127.0.0.1`，沒開錄製時後面沒有東西在聽。
- **零 firewall 改動。** proxy 綁 loopback（無條件放行），上游是白名單裡的
  `api.anthropic.com`，即時畫面從 host 進來時來源落在已放行的 docker 網段。
  限制模式下照樣錄得到。
- **CA 不持久化**，每一場現產一把，炸開的範圍就是這一個容器。
- **全錄時 CA 進系統信任庫**（`trust-mitm-ca.sh`，唯讀、不吃參數、sudoers 比照
  init-firewall 鎖成 `""`）。只餵環境變數只覆蓋得到預先想得到的客戶端；要錄到
  沒預料到的那個（curl、Go binary），就得往上一層。代價是整台機器都信那張憑證，
  所以它只活在用完即丟的容器裡。
- **`NO_PROXY` 只留 loopback 與 `jaeger`**。jaeger 排除是刻意的：OTLP 走明文
  HTTP/2 不是 TLS、protobuf 也脫敏不了，而且把觀測管道穿過被觀測的東西，proxy
  一抖就連「剛才發生什麼」都失去。只錄模型 API 時另外把 `gitlab-proxy` 也排除。
- **開了錄製就不 `exec`。** `exec` 會讓 CLI 接管 PID 1，CLI 一退出容器立刻拆掉，
  背景的 mitmproxy 被 SIGKILL、收尾來不及跑，最後那幾條 flow 就沒了。改成前景跑
  CLI、結束後送 SIGTERM 等它善終，再帶原本的退出碼離開。

- **addon 從 wrapper 自己所在的 repo 掛進去**（`mitm/` → `/home/nathan/ncr-mitm`，唯讀），
  不是從 `$PWD`。`$PWD` 掛的是**被審查的那個專案**，拿它找 addon 等於要求每個被審查的
  專案自己帶一份。掛目錄而不是烘進 image，改脫敏規則免 rebuild。

addon 不在就整場不錄，而不是退回錄未脫敏的原始流量。

## 網路邊界

容器啟動時會問一次：

```
網路能力：
  1 = 限制（白名單） — 只通 api.anthropic.com、直連的 docker 網段（gitlab-proxy），
                       SSH 22 只通 build 時指定的那台 GitLab（預設）
  2 = 完全開放       — 不套用任何 iptables 規則
```

限制模式跑的是 `init-firewall.sh`，改寫自
[Anthropic 官方 devcontainer 的同名腳本](https://github.com/anthropics/claude-code/blob/main/.devcontainer/init-firewall.sh)。
保留 Docker 內部 DNS 的那一段幾乎原樣沿用；白名單內容、SSH 收斂與驗證方式都改過。

| | 官方版 | 這一份 |
|---|---|---|
| 白名單 | GitHub 全 IP 段 + `registry.npmjs.org` + sentry + statsig + VSCode marketplace + `api.anthropic.com` | 只有 `api.anthropic.com` |
| SSH 22 | 放行到任何主機 | 只放行 `dig` 解出來的那台 GitLab |
| 直連網段 | 從 default route 推一個 `/24` | 讀 `ip route` 列出實際的直連網段 |
| 驗證 | `example.com` 不通 + `api.github.com` 通 | `example.com` 不通 + **每一個**白名單網域都要通 |
| 開關 | 沒有，一律套用 | 啟動時由人選，`NCR_NET=restricted\|unrestricted` 可跳過選單 |

官方那份的目標是「讓 devcontainer 還能開發」，所以 GitHub、npm、VSCode 全部放行；
這一份的目標是「讓 agent 只能做審查」，所以預設只通模型 API。放行 `registry.npmjs.org`
就等於 agent 能在牆內 `npm install`，而工具版本 pin 在 Dockerfile 裡是為了讓報告可重現。

### 三個容易寫錯的地方

**Docker 的內部 DNS 是 NAT 規則，不是服務。** 容器的 `/etc/resolv.conf` 指向
`127.0.0.11`，那個位址上沒有任何東西在 listen——是 nat 表把它轉到 Docker daemon 開的
真實 port。所以 `iptables -t nat -F` 一下去，容器就從「連得到但被擋」變成「連網域名稱
都解不出來」，而錯誤訊息長得像網路壞掉。腳本第 1~3 步就是為了這件事：flush 之前先撈出
那幾條規則，flush 之後只還原它們。

**規則套完要自我驗證，而且要測兩個方向。** 只測「該擋的有沒有擋住」，會漏掉「不小心把
全部都擋掉」——那種情況下 agent 從第一次呼叫模型就開始失敗，而錯誤訊息不會說是防火牆。
腳本第 9 步兩個方向都測，任一不符就 `exit 1`，entrypoint 收到失敗就不啟動 CLI。

**sudoers 要連參數一起鎖。** `nathan` 能 `sudo` 跑 `init-firewall.sh`，否則 entrypoint
套不了規則。但 sudoers 的語義是「命令後面沒有列參數 ＝ 任何參數都允許」，所以這樣寫是
有洞的：

```
nathan ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh
```

只要腳本會把位置參數用進白名單，容器裡的 agent 就能 `sudo init-firewall.sh
attacker.example.com` 把任意網域加進去、重建整道牆，**而且自我驗證還會通過**
（`example.com` 仍不通、白名單網域都通），畫面照樣印出「防火牆已驗證」。

這份因此兩道一起關：腳本不吃任何位置參數，sudoers 也把參數鎖成空（`... init-firewall.sh ""`）。
同樣的道理，GitLab 主機名走的是 build 時寫死的 `/etc/ncr/gitlab-ssh-host` 而不是環境變數
——env 是容器裡的 `nathan` 寫得到的東西，政策的來源如果是 env，等於讓被關的人自己挑監獄。

### 已知限制

- **ipset 是開機當下的快照。** `api.anthropic.com` 走 CDN、TTL 很短，長時間 session
  中途換 IP 的話請求會被 REJECT，只能重開容器。動態跟隨 DNS 就等於把白名單的控制權
  交給 DNS 回應，所以這是刻意接受的代價。
- **`docker network connect` 上去的網路不在放行清單裡。** 第 6 步是容器啟動那一刻的
  快照，之後才接的網路介面有了、路由有了，封包卻被 REJECT，而且不會自己好。
- **這道牆封不住「你允許連的那個對象，替你連出去」。** `api.anthropic.com` 必須開著，
  而伺服器端執行的東西（WebSearch、綁在帳號上的 Connector）就從那條路出去。
  iptables 管不到，控制點在帳號設定。

## 疑難排解

| 症狀 | 原因 | 處理 |
|---|---|---|
| `Host key verification failed.` | 容器裡沒有 known_hosts | host 上執行 `ssh-keyscan -t rsa,ed25519 <host> >> ~/.ssh/known_hosts` 後重跑 |
| `Error connecting to agent: No such file or directory` | image 的 `SSH_AUTH_SOCK` 有值，但 socket 沒掛進來 | 這是「這條路沒接」不是「設定壞了」。檢查 host 的 `$SSH_AUTH_SOCK`，或你是不是設了 `NCR_NO_SSH_AGENT` |
| 容器裡 `$SSH_AUTH_SOCK` 是**空字串** | image 比 Dockerfile 舊。這個變數是 image 的 ENV，改了 Dockerfile 不重 build 就不會生效 | `docker build -t ncr-dev-container .`。啟動時印的 `image built:` 時間比你改 Dockerfile 的時間早就是這個情況 |
| `Error connecting to agent: Permission denied`（Docker Desktop：macOS / WSL2 / Docker Desktop for Linux） | socket 掛進來了，但 Docker Desktop 代理出來的 socket 節點是 `root:root 0660`，而容器跑 uid 1001 | wrapper 只要沒有確定認出「原生 Linux Docker」就會補 `--group-add 0`（判不出來時也補）。還是出現代表你是自己下 `docker run`，補上這個參數 |
| `Error connecting to agent: Permission denied`（原生 Linux Docker） | socket 帶的是 **host 自己的 uid** 且通常 0600，跟容器內的 uid 1001 對不上。補 `--group-add 0` 在這裡沒有用（group 補不回 uid），wrapper 也因此刻意不加 | 真因是 uid 不符，只能讓兩邊對上：`docker build --build-arg NCR_UID=$(id -u) …` 重 build（見上方 Build 那節）。不想處理就 `NCR_NO_SSH_AGENT=1` 關掉轉發，git 改走 HTTPS。放寬 socket 權限不是解法——那等於把 agent 開給機器上所有人 |
| `ssh-add -l` 說 `The agent has no identities` | agent 在跑但袋子是空的。macOS 的 launchd agent **永遠都在**，所以「有 agent」不等於「有金鑰」 | host 上先 `ssh-add`，再啟動容器。wrapper 會在轉發前先檢查並警告，但不會替你載入——那個 agent 是你的，而且可能是刻意只放了受限 key |
| git 認證失敗但 agent 有 key | 那把 key 沒有註冊到 GitLab | 到 GitLab 的 SSH Keys 頁面確認 |
| `❌ Keychain 沒有 Claude Code 憑證` | host 沒登入過 | 先在 host 跑一次 `claude` 登入，或 `export CLAUDE_CODE_OAUTH_TOKEN` |
| `❌ Firewall 啟用失敗` 然後容器結束 | 規則沒套成功。fail closed，不會讓 agent 在沒有牆的情況下跑 | 看 `/tmp/firewall.log`。最常見是忘了 `--cap-add=NET_ADMIN`（自己下 `docker run` 時），或白名單網域解析不到 |
| 限制模式下 WebFetch 還是連得出去 | 那個請求不是從這個 netns 出去的 | 不是設定問題，見上方〈已知限制〉最後一條 |
| 限制模式下 `git push` 失敗 | build 時沒帶 `--build-arg GITLAB_SSH_HOST` | 帶了重 build，或該場選「2 完全開放」 |

## 這個容器不做的事

- **不保管任何長期憑證。** 憑證都是啟動時借進來、退出時還回去。
- **不是沙盒。** `--dangerously-skip-permissions` 是預設，agent 在容器裡是自由的；
  邊界畫在容器外面，不在裡面。
- **擋不住 agent 改「自己要遵守的規則指到哪」。** skill 目錄是唯讀掛進去的，規則檔本身
  改不動；但 `~/.claude` 是讀寫掛載，裡面的 symlink 與 `settings.json` 容器內動得了、
  而且會落回 host。這一層刻意不在這裡補，由上層隔離承接——正式版行為也是這樣。
- **管不到伺服器端替你做的事。** 見〈網路邊界〉的已知限制。
