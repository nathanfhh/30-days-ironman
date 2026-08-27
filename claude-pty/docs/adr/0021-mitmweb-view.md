# ADR 0021：從管理畫面直達 mitmweb UI（auth_request + relay，token 不進瀏覽器）

- 狀態：已接受；已實作（2026-08-26）；relay 內層換成容器內 socat（2026-08-27）

## 背景

session 開了流量錄製（`profile.capture`）時，容器裡跑的是 `mitmweb`，它自帶一個網頁 UI，
顯示的是**記憶體裡的即時 flow，未脫敏**（落到磁碟的永遠只有脫敏版，兩者是分開的）。
那個 UI 目前只有兩條路看得到：進到容器裡，或另外做 port-forward。

三個現況決定了這件事的形狀：

1. **UI 綁在 session 容器的 loopback。** `run_kwargs.py` 對網頁開的 session 一律送
   `NCR_MITM_WEB_BIND=127.0.0.1`，因為那些容器掛在共用的 per-user network 上，
   兄弟容器連得到 8081；而 token 又印在 `docker logs` 裡。收回 loopback 之後，
   拿到 token 也沒有用，得先進得了那顆容器。
2. **控制平面沒有 socat**（註，見下），**但有 docker CLI**（掛著 host 的 docker socket）。
   session 容器有 bash 與 python3；2026-08-27 起也有了 socat（dev-container/Dockerfile）。
   註：「控制平面沒有 socat」指的是部署環境的原始假設——relay 的外層 listener 實際上
   是 control 容器裡的 socat（見決策節），它的 binary 由 claude-pty 自己的 image 提供；
   這一點成立與否不影響這裡的推導：內層（session 容器裡）的 socat 必須由 session
   image 自己提供，不能借用控制平面的。
3. **「動態 port + auth_request」這套模式已經在跑**：`/api/auth/view` 回 `X-Ttyd-Port`，
   nginx 用 `auth_request_set` 取出來放進變數化的 `proxy_pass`（ADR 0005 路由 B、ADR 0008）。

需求：抽屜的按鈕列多一顆按鈕，點下去在**新分頁**開啟那一場的 mitmweb，
途中不要求使用者記任何 token。

## 決策

新增一條「路由 C」，把既有那一招再套一次，中間多一段 relay：

```
新分頁  GET /session/<sid>/mitm/
   │
   ▼ nginx（新 location，排在 ttyd 那條 regex 之前）
auth_request /_auth_mitm ──► Flask /api/auth/mitm?session=<sid>
   │                          · 驗 cookie + 擁有權（與終端同一套 _owned）
   │                          · 沒開錄製 → 404；沒有活著的 relay 就當場建一條
   │                          · 回 200 + X-Mitm-Port + X-Mitm-Token
   ▼
auth_request_set $mitm_port / $mitm_token
rewrite ^/session/[A-Za-z0-9]+/mitm(/.*)$ $1 break
proxy_pass  http://control:$mitm_port
proxy_set_header Authorization "Bearer $mitm_token"
   ▼
control 容器內的 relay：socat TCP-LISTEN:<port>,bind=<TTYD_BIND>,fork,reuseaddr
   │  每條連線 → EXEC:server/mitm_bridge.sh <container-id> 8081 <linger>
   │            → docker exec -i <container> socat -t <linger> STDIO TCP:127.0.0.1:8081
   ▼
session 容器 127.0.0.1:8081 的 mitmweb
```

四個關鍵事實讓它成立（全部在 mitmproxy 12.2.3 上實測過）：

- **mitmweb 的 SPA 是路徑相對的**：資源是 `./static/…`，WebSocket 由
  `location.pathname.replace(/\/$/,"") + "/updates"` 現場組。所以掛在子路徑下代理不必改它，
  但**尾斜線不可省**。
- **`web_password` 可以指定，而且明文 Bearer 就通**：帶 `Authorization: Bearer <密碼>`
  即放行（實測回 200），沒帶或帶錯回 **403**（不是 401）並附 `Server: mitmproxy <版本>`。
- **cookie secret 是每個 mitmweb 行程隨機的**：A 場發的 `mitmproxy-auth-8081` 在 B 場驗不過，
  於是落回 Bearer（nginx 每一發都注入），不會串場，也不會被舊 cookie 卡住。
- **mitmweb 送 `X-Frame-Options: DENY`**：所以是新分頁，不是 iframe。需求本來就是新分頁。

### token：HMAC 導出，不落 DB、不進瀏覽器

```
mitm_web_password(sid) = base64url(HMAC_SHA256(SECRET_KEY, b"mitm-web-password-v1:" + sid))[:24]
```

控制平面建容器時用新的 env `NCR_MITM_WEB_PASSWORD` 送進去（只在 capture 為真時送），
`/api/auth/mitm` 用同一個公式當場重算，交給 nginx 組成 Bearer。兩端各算各的、算出同一串，
**DB 一個欄位都不用加**。

不用 Claude Code 的 `NCR_SESSION_ID`（那是原本的想法），兩個理由：

1. 它是 entrypoint **在容器內自己產的**（讀 `/proc` 的 uuid），控制平面不知道它。
   要用就得反過來由控制平面餵進去，entrypoint 與 run_kwargs 兩邊都要動，
   還要處理「人自己開容器」那條路徑的相容。
2. 它是**可枚舉的**：capture 的落盤目錄名就是 sessionId，而 `ncr/` 根是 per-user 共用掛載，
   同一個人開的任何一顆容器裡的 agent 都 `ls` 得出全部場次的 id。這個 UI 顯示的是未脫敏的
   即時流量，「token ＝ sessionId」等於一旦哪天有條路讓兄弟容器碰得到 8081，
   全部場次一次交出去。HMAC 導出沒有這個性質：知道一場的推不出別場的。

回 base64url 截 24 字元不是隨手訂的長度與編碼：字母表裡沒有 `$`（mitmweb 會把 `$` 開頭的
`web_password` 當 argon2 hash 去驗，而我們要它做明文比對），沒有空白、引號與控制字元
（這一串會變成 shell 的 env 與 HTTP header 的值），長度與 entrypoint 的 `${token:0:24}` 對齊。

換掉 `SECRET_KEY` 等於一次作廢全部還開著的 session 的這個密碼。那是刻意的；
cookie 本來就會跟著 `SECRET_KEY` 一起失效，所以不是新增的失效模式。

### relay：socat + 一支橋接腳本

一條 relay ＝ DB 一列（`mitm_views`）加一顆 `socat`。port 由該表 `port` 的 UNIQUE 仲裁
（跨 worker 原子），生命週期掛在 `sessions.archive()` 上。程序的起、收、身分比對全部複用
`views.py` 既有的那幾支（`_spawn_detached` 的 double-fork、`_kill` 的「等到它真的消失才算數」、
`_is_ours` 的 argv[0] basename 比對），只把身分白名單參數化，ttyd 的呼叫端一字未改。

**收 relay 比收 ttyd 更非做不可**：ttyd 帶 `-q`，最後一個 client 斷線就自退；
socat 是 `TCP-LISTEN,fork`，沒有 client 時照樣在那裡聽著，永遠不會自己結束，
而 `views.inspect_ttyd` 那種對帳頁只掃 ttyd 的名字，看不到它。
`reconciler._clean_views` 也一併掃 `mitm_views`：那些 socat 是控制平面的子孫程序，
control 容器一重建就全沒了，DB 卻還留著幾列佔著 port。

## 與原規劃不同的五處（實作時實測推翻）

### 1. relay 綁 `config.TTYD_BIND`，不是 127.0.0.1

規劃寫的是綁 control 容器的 loopback。**錯**：nginx 是另一個容器，它走 `control:<port>`
連過來，loopback 到不了。照 ttyd 的做法綁 `TTYD_BIND`（容器化＝0.0.0.0，只在內部網路上；
非容器化＝loopback，那時 nginx 也在同一台），就緒探測則一律從 127.0.0.1 探
（`views._probe_host`）。

### 2. 前綴要用 `rewrite … break` 去掉，不可以拼進 `proxy_pass`

規劃寫的是 `proxy_pass http://$up:$port$rest;`（把剩下的路徑拼進去）。那有兩個問題，
第一個規劃已經點到、第二個是量出來的：

- **query string 不會自動接上。** proxy_pass 帶變數時 nginx 用的是我們組出來的那個 URI，
  原本的 query 不在裡面。mitmweb 的 SPA 靠 query 傳篩選條件，少了它那些請求會變成
  「沒有條件」：回得出東西，只是答非所問，畫面上完全看不出來。
- **🔴 已經 escape 的字元會被拆掉。** `$rest` 是從 `$uri` 擷取的，而 `$uri` 是**已經解碼**
  的，nginx 又把變數拼出來的 URI **原樣**放進請求行。於是 `/a%20b` 送出去會變成
  `GET /a b HTTP/1.1`，請求行裡一個裸空白，那不是合法的請求行。
  2026-08-26 用一個把原始請求行吐回來的 echo upstream 對照量到的。

兩個問題一次解決：`rewrite ^/session/[A-Za-z0-9]+/mitm(/.*)$ $1 break;` 加上一個
**沒有 URI 部分**的 `proxy_pass`。rewrite 之後 nginx 送的是它自己重新 escape 過的 URI
（`/a%20b` 原樣過去），而 rewrite 的替換字串裡沒有 `?`，所以原本的 query 會自動帶著走
（因此**不可以**再加 `$is_args$args`，加了就是重複一份）。改後量到的請求行：
`/flows?a=1&b=2`、`/a%20b`、`/a%20b?q=x%20y`、`/%E4%B8%AD%E6%96%87?f=%23x` 全部正確。

尾斜線則用一條獨立的 `location ... /mitm$ { return 308 …/mitm/; }`，
而不是在代理那條裡用 `if`。

### 3. 要執行的東西收進 `server/mitm_bridge.sh`，socat 的位址不放帶引號的指令

規劃直接把 `docker exec -i <cid> bash -c '…'` 寫進 socat 的 `EXEC:`。那行不通：
socat 的 `EXEC:` **依空白切詞、沒有引號機制**；`SYSTEM:` 更糟，socat 會先自己解一次引號
再交給 `/bin/sh`（實測 `SYSTEM:sh -c 'echo A B'` 的引號被吃掉，變成 `sh -c echo A B`，印出空行）。

收進檔案之後，socat 那一側只剩「路徑 + 三個沒有空白的參數」，引號問題整個消失，
而檔案裡是一般的 shell，愛怎麼引就怎麼引。腳本住在 `server/` 底下隨套件走，
容器內外用同一個路徑推導方式。

### 4. 橋接的內層是 python3，不是 bash 的 `/dev/tcp`（2026-08-27 已由容器內 socat 取代）

`bash -c 'exec 3<>/dev/tcp/…; cat <&0 >&3 & cat <&3'` 沒有**半關閉**：client 送完請求把
寫入端關掉時（curl 之類會這樣做），要嘛把讀方向一起砍掉（實測第一發請求回應就是空的），
要嘛永遠不收。python 分得開這兩件事：stdin EOF 只 `shutdown(SHUT_WR)`，讀繼續。

另加一個 `MITM_LINGER`（預設 10 秒）上限：半關閉之後對面若不主動關（WebSocket 就是這種），
主緒會永遠停在 `recv` 上，而**那個行程跑在使用者的 session 容器裡**。
實測三條閒置長連線關掉之後不收，容器裡就多三顆 python3。

> 這一節留下的理由：python 版的半關閉與 linger 語義正是 socat 版接手時要對齊的規格。
> 取代的細節見文末「2026-08-27：relay 內層換成 session 容器裡的 socat」。

### 5. WebSocket 那條要 `proxy_set_header Host $http_host;`

規劃裡沒有這一條，是**用真瀏覽器打出來的**（2026-08-26，隔離的 nginx ＋ socat ＋ 真 mitmweb）。

nginx 全站設的是 `proxy_set_header Host $host;`，而 `$host` 依定義**不含 port**。
mitmweb 跑在 tornado 上，`WebSocketHandler.check_origin` 拿瀏覽器送的 `Origin`
（一定帶 port，例如 `http://example:8080`）比對請求的 `Host`。抹掉 port 就永遠對不上，
**每一次 WS 握手都被回 403**。

症狀特別惡劣：頁面照樣載入、標題是 mitmproxy、靜態資源全部 200、畫面看起來完全正常，
只是那個流量清單永遠不會更新。同一發握手把 Host 換成帶 port 的立刻變成
`101 Switching Protocols`。ttyd 那條路由不受影響：它預設不做 origin 檢查。

## 為什麼是新的 `mitm_views` 表，而不是在 `views` 加一個 kind 欄位

同一張表才是真正一勞永逸的解：`port` 的 UNIQUE 自動跨兩種東西仲裁，不會有「兩邊各自合法地
宣告同一個 port」的問題。但它要把既有的 `uq_views_session` 改成 `(session_id, kind)`，
而輕量升級（`db._add_missing_columns`）**加不了也改不了約束**，那是一次 alembic 引入。

取捨：先分表，並讓兩個 port 範圍**不重疊**（ttyd 41000–41100、relay 41200–41300）。
範圍重疊時晚起的那個會綁不上，但就緒探測會擋下來、換下一個 port，所以那是安全網不是設計。
日後真的要引入 alembic 時，這兩張表合併是一個乾淨的第一個 migration。

## 安全分析

- **攻擊面邊界沒有變大。** mitmweb 在容器內仍綁 loopback；兄弟容器、host 網路都碰不到 8081。
  新增的可達路徑只有「通過 nginx auth_request（網站 cookie ＋ 擁有權）的瀏覽器」。
- **token 不出現在**：瀏覽器 URL（Bearer 由 nginx 注入，且會蓋掉 client 自己送的
  `Authorization`）、前端 JS、DB、`docker logs`（entrypoint 那行帶 token 的 URL
  已改成只在人路徑印）。
- **但 token 仍在 mitmweb 的 argv 裡**（`--set web_password=…`），容器內的 agent 從
  `/proc/*/cmdline` 讀得到。這是**現狀就如此**，不是這次引入的；那個 agent 反正錄的是
  自己的流量，危害不變。
- **relay 不吃使用者輸入。** 它只認 container id，而那個 id 只從 DB 取；socat 的位址是我們組的，
  橋接腳本收的是位置參數，不經 shell 展開。session 裡的內容影響不了 relay 的建立。
- **mitmweb 的 UI 本身等於 RCE 面**（command bar 能 `script.run`），這正是它 11.1.2 起強制
  密碼的原因。我們等於把「容器 loopback」這道防線換成「nginx auth_request ＋ Bearer」雙層，
  強度不低於現狀。
- **就緒探測要驗明正身才寫 pid**。nginx 會對這條路徑注入這一場的密碼；port 上若是別的服務
  （我們的 socat 其實綁失敗正在退出），我們就是把那串密碼直接遞給它。所以探測看的是
  `Server: mitmproxy`，不是「port 開著就算」。

## 明確不做

- 不嵌 iframe（mitmweb 送 `X-Frame-Options: DENY`；新分頁已滿足需求）。
- 不為了這個動 `NCR_MITM_WEB_BIND`（loopback 維持，relay 是唯一新通道）。
- 不新增 DB 欄位放 token（HMAC 導出）。
- 不碰 host port publishing（ADR 0008 不翻案）。
- **開發部署（`BEHIND_PROXY=0`）不支援。** 那時沒有 nginx，`/api/auth/mitm` 不生 relay
  （照抄 `auth_view` 的 gate），而抽屜本身也只在 `behindProxy` 時才開。

## 已知代價

1. **每條 TCP 連線一次 `docker exec`**（數百毫秒起跳）。mitmweb 首頁約五到八個靜態資源
   （有 keepalive 就複用），WebSocket 是一條長連線。量很小，首次載入會有感知延遲，可接受。
   不行的話再換「常駐 exec ＋ 自寫多工」，那是另一個量級的複雜度。
2. **容器先死、relay 還在** → 下次連線 exec 失敗 → 502 → `@view_denied` 導回首頁。可接受。
3. **control 重啟** → relay 全沒，DB 留下殘列。`/api/auth/mitm` 看不到活著的就當場重建
   （`auth_view` 的既有語義），殘列由 `reconciler._clean_views` 回收。
4. mitmweb 的 cookie 是 `Path=/`，所以那兩個 cookie 會跟著送到本站的每一條路徑。
   無害（Flask 不認得就忽略），但值得知道。
5. **起 relay 之前多一次 `docker exec` 的探測**（見下一節）。對真容器實測 0.13 秒，
   而且一條 relay 只付一次：之後 `list_mitm_views` 查得到活著的就直接沿用，不會再走到那一關。

## 上游沒在服務時，不可以把整段 port 掃一遍（2026-08-27，PR #3 Copilot 指出）

容器是 running、`profile.capture` 也是真，**不代表容器裡的 mitmweb 此刻在服務**：它可能
crash 了，也可能 session 剛建好還在啟動。原本的 `open_mitm_view` 分不出「這個 port 不能用」
與「上游接不上」，於是後者會走完前者的流程：每一個 port 起一顆 socat，每一顆都等滿
`_wait_ready` 的 20 秒逾時才換下一個。

socat 是 `TCP-LISTEN,fork`：**父程序不會自己結束**，所以那一圈只有逾時能離開，
`_pid_exists` 這個中止條件永遠不會成立。

實測（三個 port，假 socat 起得來但每條連線立刻關掉，＝ EXEC 那端非 0 退出時的樣子）：
60.6 秒，**每個 port 20.2 秒**。外推到出貨的 41200-41300（101 個）：

| | 修前 | 修後 |
| --- | --- | --- |
| 最壞耗時 | **34 分鐘**（同步請求，佔住一條 Flask 執行緒） | 一次逾時（預設 20 秒），探測擋掉時 < 1 秒 |
| `docker exec` 次數 | **12,928**（每圈兩次：`_port_open` 與 `_is_mitmweb_serving`，每條連線 fork 一次橋接） | 1（探測）＋ 失敗那一個 port 的量 |
| 錯誤訊息 | 「41200-41300 無可用 port」（**port 一個都沒少**） | 「容器裡的 mitmweb 沒有回應，請稍後再試」 |

兩道修法，缺一不可：

1. **進 port 迴圈之前先探一次上游**：`docker exec <cid> python3 -c '<connect 127.0.0.1:8081>'`，
   接不上就直接 `MitmNotReadyError`，一顆 socat 都不起。探不動（docker 不在、容器剛好消失、
   exec 逾時）一律當成「沒就緒」：往「不起」那一側倒的代價是使用者多按一次，往另一側倒
   的代價是那 34 分鐘。
   ⚠ 這一關**只驗 TCP 接得上，不驗身分**。驗身分要帶 Bearer，那等於把這一場的密碼多送進
   容器一次；而它要回答的只是「值不值得起 relay」，真正的驗明正身仍在 `_is_mitmweb_serving`
   （relay 起來之後，從控制平面這一側問）。
2. **迴圈裡分辨兩種失敗**：socat 沒站起來 → 換下一個 port（原行為）；socat 站起來了、
   是上游接不上 → 立刻收掉並回報，不再換 port。
   ⚠ 判準是 `_process_alive(pid)`（＝那個號碼上是**我們的 socat**）**而不是**
   `views._port_open(port)`。port 上若是別的服務，我們的 socat 其實 bind 失敗已經退出了，
   那時 `_port_open` 仍為真；只看它會把「port 被占」誤判成上游壞掉而整個放棄，
   但那正是該換下一個 port 的情況（上面「port 範圍不重疊」那道安全網）。
   這一條是突變驗證抓出來的：第一版的測試用 `listen(1)` 且沒有人 accept，兩次探測就把
   accept 佇列塞滿、port 從外面看起來是關著的，於是那條斷言在錯的實作下照樣綠。

`/api/auth/mitm` 對這種情形回 **503**（暫時性）而不是 403。使用者看到的畫面沒有差別
（`error_page 401 403 500 502 503 504` 一律接到 `@view_denied`），分開的價值在我們自己
這一側：access log 裡「還沒好、等一下再來」不該長成跟「不准看」一樣的數字。

`_wait_ready` 的逾時值一併從簽章搬到 `config.MITM_READY_TIMEOUT`（預設仍是 20 秒）：
修好之後**這個數字就是最壞情況的全部**（先前要乘以 101），是最壞情況的數字就該看得見、
調得動。

## 2026-08-27：relay 內層換成 session 容器裡的 socat

原本橋接的內層是 `docker exec -i <cid> python3 -c '<雙向搬運>'`——那並不是偏好，是
**當時 session image 沒有 socat**（背景第 2 點）之下的權宜：半關閉要自己用
`shutdown(SHUT_WR)` 分出來、WebSocket 不關時要靠 `MITM_LINGER` 計時器整個殺掉。
image 補上 socat 之後（dev-container/Dockerfile 的 apt 那一串），內層換成：

```
control 容器內的外層 socat（TCP-LISTEN,fork）
  → EXEC:server/mitm_bridge.sh <cid> <port> <linger>
    → docker exec -i <cid>           （互動 stdin，**不是 -it**：TTY 會破壞 binary stream）
      → session 容器裡的 socat -t <linger> STDIO TCP:127.0.0.1:<port>
        → 127.0.0.1:8081 的 mitmweb
```

三個執行細節，全部實測過（socat 1.8.0，ubuntu:24.04，同 session image 的基底）：

- **`MITM_LINGER` 從「python 計時器硬殺」變成 socat 的 `-t`（closewait）**：stdin EOF
  之後最多再讓對面講 N 秒，對面先講完就先走。語義比舊的更好——慢回應在 N 秒內
  照常完整送達（1.5s 的回應在 `-t 3` 下完整收到），只有對面真的不關才到期強收。
  預設 closewait 只有 **0.5 秒**，所以這個值不能不給；給了也不會誤殺閒置中的活
  WebSocket——會殺閒置連線的是 `-T`（inactivity timeout），與 `-t` 只差一個字母，
  寫錯方向的話「開著分頁但沒流量」的連線 10 秒就斷，畫面看起來永遠不更新。
  （socat `-t` 與 docker `-t` 的命名撞車也得當心：後者是 TTY，絕對不能用。）
- **連線不在時的逐段回收**：瀏覽器關掉 → 外層 socat 的 fork child 半關閉 EXEC 側
  → `docker exec` stdin EOF → 內層 socat 對 mitmweb SHUT_WR → tornado 關 → 內層
  socat、docker exec、外層 child 依序退出。任何一段卡住，closewait 兜底。
- **上游 8081 不在時**：內層 socat connect refuse → 非 0 退出 → `docker exec` 跟著
  非 0，外層 child 收掉這條連線。外層 listener parent 不受影響。

**Relay 的生命週期不因這次更動而改變**：

- 瀏覽器關閉／WS 關閉只回收**那一條連線**的 child 鏈（外層 child、docker exec、內層
  socat）；外層 `TCP-LISTEN` parent 繼續聽著，直到 session archive、明確 cleanup、
  reconciler 掃殘列、或 control 容器重建。
- session archive 時 `close_mitm_views` 仍收整條：外層 parent、所有 fork children，
  以及（經由上面的逐段回收）尚未結束的 docker exec 與內層 socat。
- mitmweb 本身照樣跟著 session 容器活；`NCR_MITM_WEB_BIND=127.0.0.1` 與
  per-user network 的隔離模型**都沒有動**。

## 已知未做（fable 完整審點出來、這次刻意不處理的）

- **relay 沒有一支「真 socat」的整合測試。** `test_mitm_relay` 用的是同名替身（`exec -a socat`），
  真的 socat 只在隔離環境用手驗過（真 nginx.conf ＋ 真 socat ＋ 真橋接 ＋ 真 mitmweb ＋ 瀏覽器，
  2026-08-26）。要補的話得在 host 上裝 socat，並比照 `NEEDS_TTYD` 加一道 gate；
  在沒有 socat 的機器上那支會以「無可用 port」這種完全不像缺工具的訊息失敗。
- **`db._warn_missing_constraints` 的誤報疑慮沒有處理。** 這次實際量過：全新資料庫上
  `views` 與 `mitm_views` 都拿得到兩條 UNIQUE autoindex，那支不會對新表發出警告
  （`PRAGMA index_list` 各回 2 條 UNIQUE）。既有部署的 `views` 少一條那件事是舊的、
  與本次無關，仍然會照舊警告。這一項留著沒動，因為沒有重現出誤報。

## 測試

| 守什麼 | 在哪 |
| --- | --- |
| env 覆寫的兩個方向（給了就用給的、沒給就現產且照印） | `tests/test_entrypoint_mitm_password.py`（真容器，讀 mitmweb 的 `/proc` argv） |
| token 的導出性質（確定性、跨場不同、字母表、換金鑰即作廢） | `tests/test_mitm_token.py` |
| 只跟 capture 成對送 | `tests/test_profile_mapping.py` |
| relay 生命週期（port 仲裁、起收、archive 連帶收、殘列回收、位址不含空白、橋接指令契約：`docker exec -i`、無 TTY、內層是容器裡的 socat） | `tests/test_mitm_relay.py`（假 socat） |
| 上游沒在服務時不掃整段 port（探測擋下、迴圈只試一個、port 被占仍要換下一個） | `tests/test_mitm_relay.py`（假 socat ＋ 假 docker） |
| 橋接對真 mitmweb（image 有 socat、半關閉、Bearer、403、WS 握手+雙向 frame、上游不在時非 0 收乾淨、不留行程） | `tests/test_mitm_bridge.py`（真容器，不經外層 socat） |
| `/api/auth/mitm` 的 200/404/403/401/503 與 BEHIND_PROXY gate | `tests/test_auth.py` |
| nginx 的 location 順序、rewrite 去前綴、Bearer 注入、`Host $http_host`、對外 404 | `tests/test_nginx_contract.py` |
| 按鈕只在有錄製時出現、開的是 `mitm/` 子路徑 | `frontend/src/__tests__/drawer.spec.ts`、`tests/golden/drawer-open/` |
