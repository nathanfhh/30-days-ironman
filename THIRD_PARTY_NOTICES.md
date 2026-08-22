# 第三方元件

這個 repo 以 MIT 釋出（見 [`LICENSE`](LICENSE)）。**那份 MIT 只涵蓋這裡自己寫的東西。**
下面這些是別人的作品，隨附於本 repo，各自維持原本的授權；把它們重新授權成本專案的 MIT
既不合法也沒有意義。要再散布這些檔案，請依照它們自己的條款。

## Font Awesome Free 6.7.2

- 上游：<https://github.com/FortAwesome/Font-Awesome/tree/6.7.2>
- 官方授權說明：<https://fontawesome.com/license/free>
- 授權原文（未經改寫，取自上游 6.7.2 tag）：
  [`claude-pty/server/static/vendor/fontawesome/LICENSE.txt`](claude-pty/server/static/vendor/fontawesome/LICENSE.txt)
- Copyright 2024 Fonticons, Inc.

三種授權依內容而異：

| 內容 | 授權 |
|---|---|
| 圖示（Icons） | CC BY 4.0 |
| 字型檔（Fonts） | SIL OFL 1.1 |
| 程式碼（Code，含 CSS） | MIT |

repo 內的實際位置：

| 檔案 | 屬於 |
|---|---|
| `claude-pty/server/static/vendor/fontawesome/css/all.min.css` | Code（MIT） |
| `claude-pty/server/static/vendor/fontawesome/webfonts/fa-solid-900.woff2` | Fonts（SIL OFL 1.1） |
| `claude-pty/server/static/vendor/fontawesome/webfonts/fa-regular-400.woff2` | Fonts（SIL OFL 1.1） |
| `claude-pty/server/static/vendor/fontawesome/webfonts/fa-brands-400.woff2` | Fonts（SIL OFL 1.1） |

⚠ `all.min.css` 開頭那段 `/*! Font Awesome Free 6.7.2 by @fontawesome ... */` 是上游的
attribution，**不要在壓縮或處理流程裡把它拿掉**。CC BY 4.0 要求標示來源，那段註解就是標示。

不需要在每個程式檔裡散落重複的授權註解——來源集中記在這份文件，加上 vendored 目錄裡的
授權原文，這兩處就是完整的交代。
