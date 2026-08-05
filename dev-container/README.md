# Dev Container

一個可拋棄的容器，裡面裝好 Claude Code 與審查會用到的掃描工具。用它跑 code review
的理由有兩個：**環境可重現**（掃描器版本固定，報告不會因為誰的機器而不同），
以及**邊界**（agent 拿得到的東西由你決定，而不是「我的整台電腦」）。

```
Dockerfile                  image 定義
entrypoint.sh               容器啟動後做的事
run-ncr-dev-container.sh    啟動 wrapper：憑證、SSH、規則怎麼進容器
```

## Build

```bash
cd dev-container
docker build -t ncr-dev-container .
```

### 選配：把 GitLab 的 host key 烘進 image

```bash
docker build --build-arg GITLAB_SSH_HOST=gitlab.example.com -t ncr-dev-container .
```

**不給這個 ARG 就整段跳過**，什麼都不會發生，這是預設。給了才會在 build 時
`ssh-keyscan` 那台主機，把 host key 寫進 image 的 `~/.ssh/known_hosts`。

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

當前目錄會被掛進容器的 `/home/nathan/code-review`。wrapper 幫你處理三件事：

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
git clone https://github.com/semgrep/semgrep-rules.git ~/Projects/semgrep-rules
```

wrapper 啟動前會 best-effort `git pull`（離線就沿用現有版本），再唯讀掛進容器。
找不到 clone 只會警告，容器照常啟動，那一場的 SAST 軌道無規則可用。

## 疑難排解

| 症狀 | 原因 | 處理 |
|---|---|---|
| `Host key verification failed.` | 容器裡沒有 known_hosts | host 上執行 `ssh-keyscan -t rsa,ed25519 <host> >> ~/.ssh/known_hosts` 後重跑 |
| `Error connecting to agent: No such file or directory` | image 的 `SSH_AUTH_SOCK` 有值，但 socket 沒掛進來 | 這是「這條路沒接」不是「設定壞了」。檢查 host 的 `$SSH_AUTH_SOCK`，或你是不是設了 `NCR_NO_SSH_AGENT` |
| `ssh-add -l` 說 `The agent has no identities` | agent 在跑但袋子是空的 | host 上先 `ssh-add`，再啟動容器 |
| git 認證失敗但 agent 有 key | 那把 key 沒有註冊到 GitLab | 到 GitLab 的 SSH Keys 頁面確認 |
| `❌ Keychain 沒有 Claude Code 憑證` | host 沒登入過 | 先在 host 跑一次 `claude` 登入，或 `export CLAUDE_CODE_OAUTH_TOKEN` |

## 這個容器不做的事

- **不隔離網路。** 容器出得去任何地方。網路邊界是另一個題目。
- **不保管任何長期憑證。** 憑證都是啟動時借進來、退出時還回去。
- **不是沙盒。** `--dangerously-skip-permissions` 是預設，agent 在容器裡是自由的；
  邊界畫在容器外面，不在裡面。
