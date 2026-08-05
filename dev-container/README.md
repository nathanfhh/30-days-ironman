# Dev Container

一個可拋棄的容器，裡面裝好 Claude Code 與審查會用到的掃描工具。用它跑 code review
的理由有兩個：**環境可重現**（掃描器版本固定，報告不會因為誰的機器而不同），
以及**邊界**（agent 拿得到的東西由你決定，而不是「我的整台電腦」）。

```
Dockerfile                  image 定義
entrypoint.sh               容器啟動後做的事（含網路能力選單）
init-firewall.sh            限制模式套用的 iptables 白名單
run-ncr-dev-container.sh    啟動 wrapper：憑證、SSH、規則怎麼進容器
```

## Build

```bash
cd dev-container
docker build -t ncr-dev-container .
```

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

wrapper 把 host 的 **ssh-agent socket** 掛進容器，而不是把 `~/.ssh` 掛進去。
差別是「能力」與「秘密」：容器只能請 agent 幫忙簽章，拿不到私鑰本體；掛目錄則是
把一把沒有 scope、也沒有到期日的長效私鑰整個交出去。

host 上沒有 agent 的話，wrapper 會臨時起一個、載入預設金鑰，並在退出時關掉
（只關自己起的那個；你原本就有的 agent 不會被動到）。

⚠ **爆炸半徑跟 CLI 憑證不同。** CLI 憑證外洩，別人能拿你的訂閱去問模型；SSH agent
外洩，別人能以你的身分登入**所有信任那把 key 的主機**——內網 git、正式機、跳板機。
而容器篩不掉 agent 裡的任何一把 key。

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
git clone --depth 1 https://github.com/semgrep/semgrep-rules.git ~/Projects/semgrep-rules
```

`--depth 1`：掃描只讀工作目錄，歷史一次都用不到，而 semgrep-rules 的歷史比工作目錄
本身大得多。shallow clone 不影響下面的更新——`pull --ff-only` 照樣 fast-forward，
不會被迫 unshallow。

wrapper 啟動前會 best-effort `git pull`（離線就沿用現有版本），再唯讀掛進容器。
找不到 clone 只會警告，容器照常啟動，那一場的 SAST 軌道無規則可用。

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
| `Error connecting to agent: Permission denied` | socket 掛進來了，但 Docker Desktop 代理出來的 socket 節點是 `root:root 0660`，而容器跑 uid 1001 | wrapper 掛 socket 時會一併補 `--group-add 0`。還是出現代表你是自己下 `docker run`，補上這個參數 |
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
- **管不到伺服器端替你做的事。** 見〈網路邊界〉的已知限制。
