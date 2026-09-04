# site：駕馭之道關聯圖

把三十篇連載、反覆出現的機制與事故，以及 repo 裡真正落地的檔案、ADR 與測試接成一張互動圖。
文章回答「為什麼這樣選」，repo 回答「怎麼做的」，這一頁回答「哪一天對到哪裡」。

```
site/
├── template.html      頁面本體（CSS + JS），只有一個 /*DATA*/ 記號
├── build.py           標準庫產生器：把 data/*.json 灌進 template → dist/index.html
├── data/
│   ├── days.json      30 天：層次、標題、摘要、連到哪些 repo 節點與機制
│   ├── concepts.json  機制、事故、觀點、已知缺口
│   ├── repo.json      repo 節點：路徑、所屬目錄、一句話說明
│   ├── promises.json  跨日承諾：哪天埋下、哪天兌現
│   ├── articles.json  每一天的 ithelp 網址；未發佈是 null
│   ├── tours.json     導覽：一串節點加每站一句話
│   ├── review.json    外部審閱的觀察與綠燈／紅燈；有署名，可以改寫或刪除
│   └── embed.json     （選用）向量區段的資料：三維座標、天×天相似度、每對日子最相近的段落；由 embed_analyze.py 產生
├── embed.py           把全文切段、連同節點文字送 embedding API，輸出 embeddings.json（含向量，不進 repo）
├── embed_analyze.py   embeddings.json → data/embed.json + 手標邊的健檢報告（需要 numpy/umap，只在重算時跑）
├── vendor/            ECharts 與 ECharts GL；只有 data/embed.json 存在時頁面才會載入
└── dist/              產物，不進版控
```

## 跑起來

```bash
python3 site/build.py --open          # 產生 site/dist/index.html 並開瀏覽器
python3 site/build.py --check         # 另外列出：懸空引用、佔位符、還是 null 的網址、沒被任何一天引用的 repo 節點
python3 site/build.py --refs d27      # 這一天在圖上牽動了什麼：repo 節點、機制、承諾、導覽站、審閱註記
uv run pytest tests/test_site_build.py
```

產物直接用 `file://` 開也能動；唯一的外部 `<script>` 是同目錄 `vendor/` 的 ECharts，而且只在有向量資料時才載。Google Fonts 載不到時退回系統字型。

圖可以平移（拖曳空白處）、縮放（⌘／Ctrl＋滾輪、雙指、右上角按鈕）；機制與 repo 節點可以拖著走，邊會跟著，
Day 那一排固定不動，因為它的 x 就是天數、上面的層次色帶也靠它。搜尋框對注音等 IME 只在選字完成後才查。

## embedding（選用）

`site/embed.py` 把文章切成 chunk、連同圖上 201 個節點的文字一起送到付費 embedding API，輸出一個 JSON：

```bash
uv run site/embed.py --provider fake   --article article.md --dry-run   # 只看切法與數量，不連網
OPENAI_API_KEY=… uv run site/embed.py --provider openai --article article.md
GEMINI_API_KEY=… uv run site/embed.py --provider gemini --article article.md
```

兩家都走 MRL（俄羅斯娃娃）降維，預設 OpenAI 512 維、Gemini 768 維，`--dims` 可改；向量一律正規化。
輸出裡沒有金鑰，只有 `meta`（模型、維度、文章 sha256）、`chunks`（id、天、標題、偏移、原文、向量）、`nodes`（id、kind、向量）。
選一家就固定一家：不同模型的向量不在同一個空間。改稿後加 `--reuse 上一份.json`，只有改過的段落會再送出去。

要比兩個模型（例如 gemini-embedding-001 對 gemini-embedding-2）：同一份全文各跑一次，再交給比較腳本。
它以手標的邊與每天的摘要當基準（哪個模型把節點所連的天排得更前面），另外報兩個模型彼此差多少：

```bash
GEMINI_API_KEY=… uv run site/embed.py --provider gemini-2 --article 全文.md --out embeddings-2.json
uv run site/embed_compare.py embeddings.json embeddings-2.json
```

```bash
uv run site/embed_analyze.py embeddings.json --audit audit.json   # 寫 site/data/embed.json，印出邊的健檢
python3 site/build.py                                             # 這時頁面多出「換一個量尺：向量」一節，並複製 vendor/
```

向量區段跟關聯圖在同一頁（`#vectors`）：四百多段的三維散點（UMAP／PCA 可切）、30×30 的天相似度熱圖（點格子看是哪兩段最像）、
最相近的 25 對日子與每一天的最近鄰。點雲裡的點或熱圖上的天，點下去就回到上面關聯圖的那一天；在關聯圖選了誰，點雲就只亮它連到的那幾天。
天與天的相似度是每天段落向量的平均之間的 cosine，不經降維；三維圖只是插圖。圖表用 `vendor/` 裡的 ECharts，區段捲到眼前才初始化。
沒有 `embed.json` 時這一節不存在，頁面也不載任何外部 script。目前資料：Gemini gemini-embedding-2，768 維，419 段（2026-09-04 用 `embed_compare.py` 對照過 001，兩者對「哪兩天像」的看法相關 0.95，換成 2 是為了對比度更寬與模型壽命）。

## 發佈

`.github/workflows/pages.yml`：`site/**` 有變動就 build，push 到 `main` 才部署到 GitHub Pages。
第一次要到 Settings → Pages 把 Source 設成 **GitHub Actions**。

## 文章發佈或改稿之後

照 [`PLAYBOOK.md`](PLAYBOOK.md) 走：`--refs` 列出牽動的格子，逐項對照最終版，一天一個 commit。
Day 27–30 各自要盯的地方也寫在那裡。

## 最終版要做的事（Day 30 發完之後）

1. 把 `articles.json` 裡剩下的 `null` 填成真正的網址。
2. 跑 `python3 site/build.py --check --strict`，它會列出所有仍是 `null` 的日子與任何 `{{…}}` 佔位符，
   有一個就拒絕 build。
3. 想讓 CI 也擋，把 `pages.yml` 那行 build 加上 `--strict`。

在那之前，`--strict` 一定會紅，這是預期的：連載還沒發完，不是 site 壞了。

## 改資料時

- 節點 id 是引用的鍵：`days.json` 的 `repo`／`concepts`、`promises.json`、`tours.json`、
  `review.json` 都拿它指東西。改名要一起改，`tests/test_site_build.py` 會抓懸空引用。
- 每個 repo 節點至少要被一天引用，否則它在圖上會漂到中間、沒有任何邊。
- `days.json` 的 `repo` 是文章那一天真的講到的機制所在的檔案；`repo_ext` 是文章沒講、但 repo 裡有的延伸
  （網頁版的流量畫面、image 自掃）。圖上畫成點線、面板另列一節，不讓圖宣稱文章講了它沒講的事。
- `review.json` 是外部審閱寫的，不是 repo 的自述；`about` 是那一節開頭的說明，`byline` 是面板註記的署名。作者可以改成自己的「已知缺口」清單，或整個換掉。
- 網址列的 `#<node-id>` 會直接打開那個節點，文章裡可以這樣深連結，例如 `…/#c-two-quotes`。
