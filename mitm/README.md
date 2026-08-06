# mitm｜錄下自己跟模型之間的流量

加密的流量看不到內容。要看就得在中間解開再送出去——那正是「中間人」的字面意思，
也是 L7 proxy 在做的事。

這件事做得到，是因為 Claude Code 收外部 proxy（`HTTPS_PROXY`），也收自訂的根憑證
（`NODE_EXTRA_CA_CERTS`）。它大可以把憑證寫死在程式裡，那這條路就永遠不通。

```
redact.py          落盤前的脫敏規則：抹掉 secret，保留 content
capture_addon.py   mitmproxy addon，把每條 flow 的脫敏副本寫成一顆 .mitm
wire_report.py     從 .mitm 算出線上實際流過什麼，輸出單頁 dashboard
```

## 錄

dev container 啟動時會問一題，預設不錄：

```
錄製本場流量？（mitmproxy，只錄 api.anthropic.com）
  y = 錄，落在 ~/ncr/mitm/flows-<時間>.mitm（脫敏後）
  n = 不錄（預設）
```

答 y 之後畫面會印兩行：檔案落在哪，以及即時畫面的網址（帶一次性 token）。
非互動環境用 `NCR_CAPTURE=1` 跳過選單。

addon 由 run wrapper 從**它自己所在的 repo** 掛進容器（`mitm/` → `/home/nathan/ncr-mitm`，
唯讀），跟你在哪個專案目錄下執行無關。

錄製有三道界線：

- **CA 每一場現產**，`~/.mitmproxy` 不持久化，炸開的範圍就是這一個容器。
- **落地的是脫敏副本**，addon fail-closed：脫敏出錯就整條丟掉，絕不改寫成未脫敏版
  落地；addon 不在就整場不錄，而不是退回錄原始流量。
- **只錄模型 API 的 host**，其餘一概不收。

即時畫面顯示的是記憶體裡的原始 flow（未脫敏），只綁 `127.0.0.1`、有 token；
落到磁碟的永遠是脫敏版。兩者是分開的。

> ⚠️ 脫敏拿掉的是 **secret**，不是 **content**。整份 system prompt、送進去的程式碼
> 都還在檔案裡——那正是錄它的目的，但那些東西本身可能就是機敏材料。
> 自由文字裡的 secret 樣式只是盡力而為：換個 key 名稱（`AKIA…`、`ghp_…`、PEM 區塊）
> 就抓不到。`~/ncr/mitm/` 請當機敏目錄看待（`chmod 700`、定期清）。

## 讀

```bash
uv run mitm/wire_report.py ~/ncr/mitm/flows-20260806T101500.mitm --open
uv run mitm/wire_report.py <capture> --json > wire.json
```

單頁四塊：上下行總量、累積上傳曲線（疊一層「其中是重送的」）、端點清單、
每一發請求的 cache 行為。

**它刻意不做帳單、不做每角色歸因、不畫甘特**——那些 `opentelemetry/` 已經有了，
而且來源更準（transcript 的 usage 逐請求精確，trace 的父子鏈才認得出誰是誰）。
重複做只會讓人問「我該信哪一份」。計價要用到牌價時，直接 import
`opentelemetry/cost-report.py`，不抄第二份。

留在這裡的，是只有 L7 答得出來的四件事：

| | 為什麼 trace 給不出來 |
|---|---|
| 線上實際傳了多少 byte | trace 記 token，不記 byte。帳單便宜不等於頻寬省 |
| 其中有多少是重送的 | 要比對 request body 才算得出來 |
| 除了模型 API 還有誰在講話 | trace 只記錄程式願意送出來的東西，capture 記錄的是事實 |
| cache 斷點下在哪 | 命中率 trace 看得到，斷點策略只有 body 有 |

「重送」的比對對象是**同一條對話的前一發**，不是時間上相鄰的那一發——subagent 會
交錯進來，拿時間相鄰的兩發去比會得到一個沒有意義的數字。對話用 `messages[0]` 認，
不依賴客戶端配合送出任何識別。

byte 數取的是 `raw_content`，也就是沒有解壓的原樣，因為要回答的是「傳了多少」而不是
「內容有多長」。報表底下會說明這一場的請求有沒有壓縮，決定這裡的數字能不能直接跟
網卡上的量對帳。

## 版本綁在檔案上

`.mitm` 裡帶著 flow format 的版本號，**讀它的 mitmproxy 不能比寫它的舊**。舊版遇到
新格式是直接拋例外，不是盡力讀到哪算哪：

```
ValueError: mitmproxy 9.0.1 cannot read files with flow format version 21,
please update mitmproxy.
```

容器裡是 12.2.3（寫出來的是 v21），host 上如果還留著舊版的 `mitmweb`，拿它去開就是
上面這個錯。所以 Dockerfile 把版本 pin 住，`wire_report.py` 的相依下限也釘在同一個
版本；用 `uv run` 跑報表會拿到它自己的環境，不會誤用到 host 上那支。

要在 host 上用圖形介面翻 capture，先確認版本：

```bash
mitmweb --version          # 要 >= 容器裡那個版本
uvx mitmproxy@12.2.3 mitmweb -r ~/ncr/mitm/flows-<時間>.mitm   # 或直接指定版本開
```

## 只做 SSE

Claude Code 跟模型之間走的是 SSE，這裡就只做 SSE。

WebSocket 沒做，不是漏掉。`.mitm` 是一串一寫定就不再改的紀錄，而 WebSocket 要等連線
關閉才寫得出完整的一筆——「一條連線一筆紀錄」跟「紀錄反映還開著的連線的最新狀態」
不可能同時成立。那條路徑上即時與脫敏只能挑一個，而這個工具用不到它。

## 手動錄（不用 dev container）

```bash
mitmweb -q --listen-host 127.0.0.1 --listen-port 8880 \
    --set store_streamed_bodies=true \
    -s mitm/capture_addon.py \
    --set capture_out=./flows.mitm \
    --set capture_hosts=api.anthropic.com

HTTPS_PROXY=http://127.0.0.1:8880 \
NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem \
    claude
```

`store_streamed_bodies=true` 是必要的：沒有它，SSE 的 body 在 response hook 的當下
還沒就位，錄出來的回應是空的。**不要**加 `-w`：內建的存檔 addon 排在前面，會先把
未脫敏的原始 flow 寫出去。
