# site 更新 playbook：文章發佈或改稿之後怎麼做

這份文件是給「下一次」用的：Day 28–30 還沒發、前面幾天也可能改稿。
每次文章有變動，照這裡走一遍，圖上的每一格就不會靜默漂掉。

圖是散文形狀的資料：一天的摘要改了，牽動的不只 `days.json` 那一列，
還有以它為錨的承諾、引用它的導覽站、點名它的審閱註記、它指向的 repo 節點。
`build.py --refs <id>` 就是替這件事準備的。

## 0. 觸發

任一成立就跑一次：

- 某一天在 ithelp 正式發佈（`articles.json` 那一天還是 `null`）
- 已發佈的某一天改稿，而且改到**數字、規則、事故的結局、或「明天講」的預告**
- repo 改了圖上有的檔案：改名、搬家、刪除，或 ADR 新增／被取代

只改錯字或語氣不用跑。判準跟 skill 的 eval 一樣：修改前後的句子給沒讀過 context 的人看，會不會導到不同的結論？會就跑。

## 1. 單一天的更新（每次一天）

以 Day 27 為例，把 27 換成當天。

```bash
python3 site/build.py --refs d27      # 這一天在圖上牽動了什麼
```

逐項對照文章的**最終版**：

1. **網址**：`site/data/articles.json` 的 `"27"` 從 `null` 填成 ithelp 網址。
2. **`days.json` 那一列**：`title`、`sub`、`summary` 對得上最終版嗎？數字（秒數、金額、幾條規則）
   以文章為準；`summary` 用文章自己的措辭，不要加進文章沒說的結論。
3. **`repo`／`concepts` 清單**：文章新提到的檔案或 ADR，`repo.json` 有沒有那個節點？沒有就加，
   `group` 選對目錄；文章拿掉的東西，從清單移除（節點本身若沒別的天引用，`--check` 會列成 orphan）。
   判準是「那一天的原文有沒有講到這個機制」，檔名不必出現；文章沒講但 repo 有的延伸放 `repo_ext`，不要混進 `repo`。
   這一條是 2026-09-04 抽驗 144 條邊時修掉 12 條的原因：邊是手標的，最容易把「同一個主題」誤當成「那一天有講」。
   兩個常見的接錯形狀：(a) ADR 接到「提出問題」的那一天，而不是「講解法」的那一天，文章沒講解法就放 `repo_ext`；
   (b) 一個檔案有兩半（ADR 0008 的按需 ttyd 與登錄表、ADR 0010 的歷史與退出），只接到講其中一半的那一天，另一半的那一天也要接。
   測試檔的慣例：接到它守的規則所在的那一天（面板才有「守著它的測試」），寫測試的那一天（Day 12、13）另外接。
4. **`concepts.json`**：`--refs` 列出的每個機制，`desc` 裡引用這一天的句子還成立嗎？
5. **`promises.json`**：`--refs` 列出的每一條，「兌現」那一半是否真的兌現、措辭是否對上最終版。
   Day 30 的對帳表會再數一次總數，見第 3 節。
6. **`tours.json`**：導覽站的 `caption` 是一句話的引文，最容易過期；逐站重讀。
7. **`review.json`**：`--refs` 會列出文字裡點名這一天的註記與綠燈／紅燈。
   審閱寫的是「作者在 Day 27 認了帳」這類判斷，如果最終版把那段拿掉或改了立場，註記要跟著改或刪；
   作者本人可以直接改寫成自己的「已知缺口」。
8. 跑檢查與測試，看 CI：

   ```bash
   python3 site/build.py --check
   uv run pytest tests/test_site_build.py
   ```

9. **向量區段**（有 `site/data/embed.json` 才有這一節）：文章的文字變了，向量就過期了。該節末尾印著算向量時的文章 sha256 前 12 碼，
   跟現在的全文對不上就重算。重算只付改過的段落：

   ```bash
   GEMINI_API_KEY=… uv run site/embed.py --provider gemini --article 全文.md --out embeddings.json --reuse 上一份.json
   uv run site/embed_analyze.py embeddings.json          # 覆寫 site/data/embed.json
   ```

   `--reuse` 以段落原文比對，只送新的或改過的段落；換 provider、model 或維度會被拒絕。`embeddings.json` 本身
   （含向量，約 5 MB）不進 repo，留在自己手上給下一次 `--reuse`。**只改網址不用重算**，網址不進向量。
10. 一天一個 commit，訊息寫清楚是「發佈」還是「改稿」，以及改了哪幾格。push 到 main 後 `pages.yml` 會自動部署。
    同時在下面第 6 節的更新紀錄補一列。

### 交給 AI 做的話

貼這一段（把 Day 與網址換掉，全文貼在後面）：

> Day 27 已發佈：<網址>。全文如下。請照 `site/PLAYBOOK.md` 第 1 節執行：
> 先跑 `python3 site/build.py --refs d27`，逐項對照最終版，改 `site/data/`，
> 跑 `--check` 與測試，最後列出你改了哪幾格、哪幾格判斷不用改、以及理由。
> 附上重算過的 embeddings.json，請跑 embed_analyze 更新 site/data/embed.json，並在第 6 節補一列。

向量那一半的分工：**金鑰在作者手上，所以 `embed.py` 由作者跑**（全文 md ＋ `--reuse` 上一份），
產出的 embeddings.json 連同當天的全文一起交給 AI；`embed_analyze.py`、`site/data/` 的修改、測試與 commit 由 AI 做。
沒有新的 embeddings.json 時，AI 只會動關聯圖，向量區段會停在舊的文章 sha，那是預期中的過渡狀態，不是錯。

它回報的「不用改」也要看一眼：跟 Day 13 那面鏡子一樣，改 skill 的人腦中有作者意圖，
判「文件講清楚了」最容易鬆。

## 2. Day 27–30 各自要盯的地方

這四天目前在圖上的內容來自草稿，下面是草稿裡最可能在最終版變動的東西。

### Day 27｜把「不做」變成結構

- 2026-09-04 收到改稿版，同日取得網址並填入 `articles.json`：只有兩處實質變動，開頭補 fork 連結、「反例」那節多一段 GPT-6 Astra 系統卡的外部對照。
  已對進 `d27.summary` 與新機制 `c-capability-owes-structure`；五條規矩、pipe fd、四道閘的數字沒變，承諾與審閱註記不用動。
- 草稿的核心是「四條不做＋一條評估中」，最後收成「五條規矩，三條由結構背著、兩條靠人記得」。
  **這個數字是圖上 `c-structure-not-discipline`、`c-hitl` 與紅燈 #1 的依據**；最終版若改成別的數字或
  把 HITL 做成機制了，三處要一起改。
- 「CLI token 從 env 改成 anonymous pipe」對到 `r-adr-0019`、`r-test-token-fd`；「圖片上傳補四道閘」對到
  `r-test-upload`。若最終版拿掉上傳那一段，`d26 → d27` 那條承諾要改。
- `r-pty-user-proxy` 的 git-lfs 缺口與 `r-adr-0011` 的共用 ssh-agent 是草稿明講的；確認最終版沒有解掉。

### Day 28｜收得掉，也長得回來才算平台

- 數字最多的一天：寬限期 30／120／300 秒、docker-py 60 秒 timeout × 15 秒輪詢 = 4 條 thread、
  `--workers 1 --threads 8`、208 KB、40 分鐘。這些散在 `c-40min`、`c-fd-leak`、`c-reconcile` 與 `d28.summary`。
- 「每人一顆代理是拓撲問題不是參數問題」那段被 `review.json` 的 `r-nginx-template` 註記引用來評單機版；
  最終版若改了說法，註記跟著改。
- 對到的測試：`r-test-attach-close`（第一條斷言先驗前提）、`r-test-reconciler`。

### Day 29｜假綠燈五形狀

- 承諾最多的一天（六條指向它）。五形狀的**編號與歸類**是 `c-false-green`、五條 `dXX → d29` 承諾、
  導覽「假綠燈五形狀」六站 caption 的共同依據。最終版若調整形狀的順序或名稱，這些要整批改。
- 「跨使用者網路沒隔離 → ADR 0016」對到 `r-adr-0016`、`r-test-network-isolation`；確認最終版仍是這個結局。
- 文末「Vue 3 遷移三種證據問題又來一次」對到 `r-adr-0020`、`r-vue-journal`、`r-golden`；草稿說是「寫完後」補的，
  最終版可能搬到別天。

### Day 30｜從 Code Review 到 Agent 治理

- 草稿裡有 `{{DAY26_URL}}`、`{{DAY27_URL}}`、`{{DAY29_URL}}` 佔位符；最終版發出來後，`--check --strict` 會確認
  資料裡沒有任何 `{{…}}`。
- 「跨日承諾 25 條、明天講 27 次」是對帳表的引言；圖上追到的是 20 條。最終版若給了完整清單，
  把缺的補進 `promises.json`，引言的數字改掉。
- 成本更新那段（單次成本翻倍：Fable、流程變長、tokenizer）對到 `d01 → d30`、`d15 → d30` 兩條承諾。
- Harness／Sandbox 的區分是 `c-harness-sandbox`；孫子兵法那張表「沒有一列講 Sandbox」是 `c-suntzu` 的第二句。
- 這一天沒有自己寫的程式，圖上只回指 README、架構圖與 SECURITY-SCAN.md；最終版若點名別的檔案再加，不要為了填格子硬接。

## 3. Day 30 發完之後的最終版

```bash
python3 site/build.py --check --strict     # 任何 null 網址或 {{…}} 都會拒絕 build
uv run pytest tests/test_site_build.py
```

然後：

1. `site/README.md` 與本文件裡「連載中」的說法改成過去式。
2. `pages.yml` 的 build 那行加上 `--strict`，讓 CI 從此擋住漏填。
3. 把 `promises.json` 對回 Day 30 自己的對帳表，總數對得上就把頁面上「追到的 20 條」改成實際數字。
4. `review.json`：決定要保留外部審閱、改寫成作者的已知缺口，還是拿掉。三種都可以，但要在同一個 commit 決定，
   不要留一半。
5. 覆蓋矩陣看一眼：只有 Day 1、2 整列空白；其他任何一天變成空欄都是資料掉了。

## 4. repo 端變動時

- 檔案改名或搬家：改 `repo.json` 的 `path`，`id` 不要動（id 是所有引用的鍵）。
- 檔案刪除：從 `repo.json` 移除節點，`--check` 的 dangling 會列出還在引用它的天，逐一處理。
- ADR 被新 ADR 取代：舊節點保留，`desc` 加一句「被 ADR 00xx 取代」，新 ADR 加節點並接到相關的天；
  跟 Day 16 講的癒痕同一個道理，刪掉會弄丟記憶。
- ttyd 那一組的矩陣列照 fork 實際動到的目錄分（`git diff --stat upstream/main main`：目前只有 rust/ 與 README）。
  fork 若動到別的目錄，`repo.json` 加節點、`template.html` 的 `GROUPS` 加一列。
- 新的測試守住了圖上某個事故：加進 `repo.json`（`group` 用 `test` 或 `ptytest`），接到那個事故所在的天，
  面板的「守著它的測試」就會出現。

## 5. 不要做的事

- 不要在 `summary` 裡寫文章沒有下的結論；判斷放 `review.json`，並帶署名。
- 不要為了讓 `--strict` 過而填一個猜的網址；`null` 是誠實的狀態，猜的網址是假綠燈。
- 不要改 id 來「整理」命名；一個 id 改名要動五個檔案，而測試只抓得到懸空，抓不到「接錯了但存在」。

## 6. 更新紀錄

哪一天、什麼時候、動了什麼、向量有沒有跟著重算。這張表是給下一次判斷「向量區段是不是舊的」用的。

| 日期 | Day | 發佈／改稿 | 動了什麼 | 向量 |
|---|---|---|---|---|
| 2026-09-04 | 27 | 改稿 | 開頭補 fork 連結；「反例」多一段 GPT-6 Astra 系統卡對照。`d27.summary`、新機制 `c-capability-owes-structure` | 2026-09-04 全文重算（gemini-embedding-001，768 維，419 段） |
| 2026-09-04 | 2、11、12、26、27 | 填網址 | `articles.json` | 不用 |
| 2026-09-04 | — | 換模型 | 向量從 gemini-embedding-001 換成 gemini-embedding-2（同 768 維、同切法）；`embed_compare.py` 的比較記在 README | 全文重算，之後的 `--reuse` 要拿 embedding-2 那份 |

