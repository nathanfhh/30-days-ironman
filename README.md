# 30 Days Ironman

30 天鐵人賽「AI 的駕馭之道」的程式產出物。

文章談的是怎麼把一套 Code Review 的判斷標準，從腦袋裡搬進一份 AI Agent 讀得懂的
文件；這個 repo 放的就是那份文件本身，以及它需要的腳本。

## 目錄

```
install.sh              把 skills/ 底下的 skill 連進 Claude Code
skills/
└── nathan-code-review/ Code Review Skill
```

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

## 授權

程式碼可自由取用、修改。真正有價值的不是這份 skill 本身，而是「把你的判準寫下來」
這件事——歡迎整份拿去改成你自己的樣子。
