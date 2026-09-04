# vendor

關聯圖頁面「向量」那一節用的第三方函式庫，從 npm 原封搬進來，讓它跟其餘部分一樣能離線、能用 `file://` 開，
不依賴 CDN 活著。只有 `site/data/embed.json` 存在時 build.py 才會把這兩支放進頁面。

| 檔案 | 來源 | 授權 |
|---|---|---|
| `echarts-6.1.0.min.js` | npm `echarts@6.1.0` `dist/echarts.min.js` | Apache-2.0（`LICENSE-echarts`） |
| `echarts-gl-2.1.0.min.js` | npm `echarts-gl@2.1.0` `dist/echarts-gl.min.js` | BSD-3-Clause（`LICENSE-echarts-gl`） |

升級：換掉檔案即可，build.py 會把 `vendor/*.js` 依檔名排序全部載入；`pages.yml` 的自檢寫死了 echarts 的檔名，一起改。
