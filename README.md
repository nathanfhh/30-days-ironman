# 30 Days Ironman

30 天鐵人賽「AI 的駕馭之道」的程式產出物。

文章談的是怎麼把一套 Code Review 的判斷標準，從腦袋裡搬進一份 AI Agent 讀得懂的
文件；這個 repo 放的就是那份文件本身，以及它需要的腳本。

## 目錄

```
install.sh              把 skills/ 底下的 skill 連進 Claude Code
skills/
└── nathan-code-review/ Code Review Skill
dev-container/          可拋棄的審查環境：工具版本固定，憑證借進來、退出時還回去
gitlab-proxy/           擋在 GitLab 前面的 nginx：憑證不進 session、端點白名單、限流
opentelemetry/          觀測：Jaeger compose 與三支報表腳本（時間、錢、單場 HTML）
tests/                  腳本的單元測試與 skill 的行為回歸 test case
benchmarks/             code-review-bench：50 個真實 PR 的評測資料集與計分 harness
```

後面兩個目錄是選配的。只想試 skill 的話，`install.sh` 裝完就能用；它們處理的是
另一個問題——**當你要把這套流程交給 AI Agent 自己跑，該給它多少權限**。

## 安裝

用 symlink 而非複製，所以在這個 repo 裡改一行，下一次對話就吃得到：

```bash
./install.sh                      # 全部
./install.sh nathan-code-review   # 指定一個
```

接著在 Claude Code 中執行 `/reload-skills`。

## skills/nathan-code-review

給 GitLab Merge Request、branch 或工作區的變更做審查，產出繁體中文報告，
經人同意後才發佈到 MR 討論串。

它不是要比市面上的 LLM Code Review 服務更強，而是要更**像你**——把團隊慣例、
環境現實與風險偏好編進去，那塊通用工具必然留白的地方，正是審查價值所在。

幾個貫穿整套設計的決定：

- **再次審查不是上一輪的續集。** 前次報告與作者的回覆會被封存到盲審結束才拆封，
  避免被前一輪的結論定錨。
- **結論由 findings 機械推導，不由 AI 自由發揮。** 只要有一條存活的 Critical，
  結論必為 `Request Changes`，沒有轉圜空間。
- **沒驗證過的主張不准掛等級。** 它只能以「提問」形式浮出——一次自信的誤判，
  之後每一條 Critical 都要陪葬。
- **工具不存在時不靜默跳過。** 每個掃描器都有缺席分支，報告會誠實說出哪些檢查沒跑。
- **待審的文字是證據，不是指令。** MR 說明、commit message、程式碼註解裡若出現
  「跳過這項檢查」「直接 approve」，一律不改變審查行為。

環境需求、資料放在哪、觸發方式等細節見
[`skills/nathan-code-review/README.md`](skills/nathan-code-review/README.md)。

## dev-container

把審查跑在一個可拋棄的容器裡。兩個理由：**環境可重現**（掃描器版本固定，報告不會
因為誰的機器而不同），以及**邊界**（agent 拿得到什麼由你決定，而不是「我的整台電腦」）。

啟動 wrapper 處理三件跨越 host 邊界的東西：Claude Code 憑證（三個來源依序嘗試）、
git 的 SSH 憑證（**轉發 ssh-agent socket，不掛 `~/.ssh`**——交出去的是簽章的能力，
不是私鑰本體），以及 Opengrep 的規則來源。

容器啟動時還會問一次**網路能力**。限制模式預設拒絕所有 outbound，只放行 `api.anthropic.com`、
直連的 docker 網段，以及通往你指定的那台 GitLab 的 SSH——因為 ssh-agent 交出去的簽章能力
沒有範圍，範圍只能在網路這一層畫。腳本改寫自 Anthropic 官方 devcontainer 的版本，
差異與理由見 [`dev-container/README.md`](dev-container/README.md)。

細節與疑難排解見 [`dev-container/README.md`](dev-container/README.md)。

## gitlab-proxy

一顆 nginx，擋在 GitLab 前面替 agent 蓋憑證的章。

skill 原本的做法是把 token 放進環境變數讓 agent 自己帶，那跟雙手奉上沒有差別：
MR 說明裡一段 prompt injection 就能讓它拿那把 token 做 scope 內的任何事，而且事後
沒有任何 per-call 的紀錄可查。代理買到的是 token 買不到的三件事：**端點白名單、
速率限制、每一次呼叫的紀錄**。

白名單只有六條，對照 skill 真的會呼叫的端點——GitLab 有的端點很多，但
「知道有這個端點」跟「決定放行這個端點」是兩件事。

細節見 [`gitlab-proxy/README.md`](gitlab-proxy/README.md)。

## 授權

程式碼可自由取用、修改。真正有價值的不是這份 skill 本身，而是「把你的判準寫下來」
這件事——歡迎整份拿去改成你自己的樣子。
