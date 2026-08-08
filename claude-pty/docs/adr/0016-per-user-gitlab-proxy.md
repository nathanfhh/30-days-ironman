# ADR 0016：每個使用者一張網路——session 的隔離邊界，兼他的 GitLab 代理

- 狀態：已接受；已實作
- **2026-08-08 擴充**：per-user 網路從「GitLab 代理的實作細節」升格成 **session 的拓樸**。
  原本的結論（代理跟著人走）沒有改變，改變的是那張網路的**地位**：它現在無條件屬於每一個
  開 session 的人，GitLab 只是掛在上面的其中一樣東西。新增的是〈拓樸〉〈同時在線人數的
  上限〉〈隔離來自哪裡〉三節，以及「不得退回共用網路」這條硬規則。

## 背景

`gitlab-proxy/` 那顆獨立代理解掉了「憑證不進 agent 的 session」：一顆 nginx 擋在 GitLab
前面，PAT 只存在於代理裡，session 裡的東西裸打、由代理蓋章。單人單機用它就夠了。

搬進 claude-pty 之後多了一個它沒有的問題：**這裡有很多人，每個人是不同的 GitLab 身分。**
一顆共用的代理等於所有人共用一把 PAT——誰做的操作在 GitLab 上都記成同一個人，而且任何
一個人都用得到別人的權限。所以代理必須跟著「人」走。

問題是跟著人走**到哪個粒度**。第一個直覺是每一場 session 一顆（生命週期最單純：session
起它就起、session 收它就收），但那個形狀有一個調參數修不好的缺陷。

## 決策

**每個使用者一張 docker network，他所有的 session 都住在上面，而且只住在上面。**
設了 PAT 的人，那張網上再多一顆 nginx。

### 拓樸

```
network claude-pty-user-1   ┌ 使用者 1 的 session A ─┐
                            ├ 使用者 1 的 session B ─┼─► gitlab-proxy（alias）──► GitLab
                            └ 使用者 1 的 session C ─┘   一顆，握他自己的 PAT
                                    └───────────────────► jaeger（選配，控制平面接上來）

network claude-pty-user-2   ┌ 使用者 2 的 session D ─┐
                            └ 使用者 2 的 session E ─┴─► gitlab-proxy（**他自己那顆**）

（沒有任何跨使用者共用的 session 網路）
```

- session 容器**只掛一張網**，就是他主人那一張。由 `containers.create(network=…)` 在
  建立當下掛上，不是事後 `connect`
- **四種 profile 組合一律指定**（restricted／unrestricted × telemetry 開關）。沒有例外
- 每個使用者的網路上最多一顆代理，network alias 預設是 **`gitlab-proxy`**
  （`CLAUDE_PTY_GITLAB_PROXY_ALIAS`，多數部署不會動它）
- session 那一端看到的位址**由 env 注進容器**（`NCR_GITLAB_API_BASE`），
  **任何呼叫端都不該把 `http://gitlab-proxy:5678` 寫死**——寫死之後改 alias 就是靜默失效
- **網路無條件建**（不看 GitLab 開不開、不看有沒有 PAT）；**代理才有 PAT 前提**
- 兩者都在「這個人沒有活著的 session」之後回收

### 這次修掉的兩個洞

先前所有人的 session 共用一張 `claude-pty-sessions`。2026-08-07 實測（兩個使用者各開一場
`unrestricted`，在其中一顆起 TCP listener，從另一顆連過去）：**連得上，收得到 banner。**

盤點時發現第二個、而且更大的洞：`build_run_kwargs` 只在 `restricted` 或 `telemetry` 時才設
`network`，所以 **`unrestricted` 且不送 telemetry 的 session 根本沒加入任何指定網路，落在
docker 預設 `bridge`**——那張網住著這台機器上每一顆沒指定網路的容器，不只是別人的 session。

⚠ 兩個洞都不是「寫錯一行」，是**測試的形狀漏了一格**：restricted 與 telemetry 各有一條
斷言，就是沒有人測第三種組合。現在四組都釘在 `test_profile_mapping`，真實封包則由
`test_network_isolation` 用真容器、真 listener、正反雙向驗。

### 隔離來自哪裡

**網路邊界，不是容器內的防火牆。**

`restricted` 的容器裡有 iptables 擋出網，但那擋的是「出去外面」，擋不住同網段的橫向連線；
`unrestricted` 連那一層都沒有。所以隔離**不可以**建立在防火牆上——它必須是網路本身的性質，
兩種 profile 才會一致成立。

`test_network_isolation` 刻意用最陽春的容器（沒有 iptables、沒有 NET_ADMIN、沒跑
`init-firewall.sh`）來證明這件事：連那一層都沒有還是連不到，擋住封包的就只可能是網路。

### 這條邊界上有一個刻意的例外：jaeger

`attach_jaeger` 把**同一顆** jaeger 容器接上**每一張**使用者網路（telemetry 的前提，見下方
〈jaeger〉）。所以它是這條邊界上唯一橫跨所有人的東西，而它的查詢 API 沒有認證——任何一場
session 裡的程式碼都 `curl` 得到 `http://jaeger:16686/api/traces`，讀得到**跨使用者**的
span 中繼資料（`user.email`、`user.id`、`session.id`、模型與 token 計數）。

**這是接受的，不是漏的**，理由是這套系統從來沒有宣稱使用者之間有機密性——README 的
〈安全邊界〉開宗明義寫「開帳號給誰，就等於請他信任你」，[ADR 0013](0013-web-entry-bind-address.md)
也寫著「**仍不是租戶隔離**……只適合互信操作者」。屬性層想擋也擋不完（`user.id`、
`session.id`、`organization.id` 全都是識別性屬性，而且會再長出新的）。

⚠ **但這條邊界的另一半沒有被這個例外破壞，而那一半才是這一節在講的**：跨使用者的**橫向
連通性**仍然成立。實測（2026-08-09）：session 容器打得到 `jaeger:16686`（HTTP 200），
打不到控制平面的 `control:8000`（連線失敗）——jaeger 掛著多張網路，但它不轉送封包。
所以「一場 session 打不到另一個人的 session 容器」這條性質不受影響。

⚠ prompt 內容目前不在 span 裡（CLI 預設把 `user_prompt` 遮成 `<REDACTED>`，已實測），
但**那道遮蔽是上游 CLI 的預設值，不是這裡的任何一道防線**。上游改一次預設，同一條路就
開始運送 prompt 全文——真的要收的話，正確的形狀是在使用者網路上只放一顆 receive-only 的
collector，把查詢面留在 jaeger 自己那張網上。

### 不得退回共用網路（硬規則）

**位址池滿、網路建不出來時，正確的行為是讓 session 開不起來，並把下一步講清楚。**

唯一的「降級」選項是把人塞進一張共用的網，而那會把上面兩個洞原樣打回來，**而且是無聲地**
——畫面上一切正常，只有隔離消失了。所以 `_ensure_user_network` 的失敗語意與
`_ensure_user_proxy` **刻意相反**：代理不在是「這場少一個功能」，網路不在是「這場沒有地方
可以待」。不要為了對稱把它們統一掉。

### 為什麼不是 per-session

**nginx 的 `limit_req_zone` 是 per-instance 的。** 一個使用者開 N 場 session 就是 N 顆
nginx、N 個獨立的計數桶——設定寫 `10r/s`，對 GitLab 的實際速率是 `N×10r/s`。

也就是說**限流形同虛設，而且「同時開很多場」正是它最該發揮作用、卻最失效的情境**。

這不是把數字調小能修的：把 `10r/s` 改成 `2r/s` 只是把上限挪到 `N×2r/s`，N 仍然由使用者
決定。**那不是設定問題，是拓樸問題**——一個 per-instance 的計數器沒有辦法表達一個
跨 instance 的總量。收斂成 per-user 之後，桶才對得上「人」這個單位。

順帶的兩件事：容器數從 N 降到 1；而且跨使用者的隔離**來自網路邊界，不是防火牆規則**，
所以 `restricted` 與 `unrestricted` 兩種 profile 下都成立。

## 建立順序是硬要求，不是偏好

**`create` → `connect` → `start`。**

`init-firewall.sh` 放行的是「entrypoint 跑到那一刻的**直連網段快照**」。容器啟動**之後**
才 `network connect` 上去的網路不在那份清單裡——介面有了、路由有了，但封包被 REJECT，
**而且永遠不會好**：reconciler 補得了網路，補不了 iptables，防火牆不會重跑。

所以 `sessions.create()` 用 `containers.create()` + `start()` 而不是 `run()`，中間那一步
才有位置可以插。

### alias 只有一種寫法帶得上去

| 寫法 | 結果 |
|---|---|
| `containers.create(network=X, networking_config=…)` | **alias 被默默丟掉**（`Aliases=None`） |
| 先建在 `none` 再 `network.connect(aliases=…)` | daemon 拒絕（private mode 不能接多網路） |
| 先建在**預設 bridge** 再 connect | alias 有了，**但代理同時留在 bridge 上**——任何 bridge 容器都能用 IP 打到它，**隔離當場破掉** |
| **低階 `api.create_container(networking_config=…)`** | ✅ 只在使用者網路上、alias 生效、拿得到內嵌 DNS |

## 同時在線人數的上限

**一人一張網，而 docker 的位址池是整台機器共用的。** 預設能切出 **31 張** bridge network：

| 來源 | 張數 |
|---|---|
| `172.17.0.0/16` – `172.31.0.0/16`（size 16） | 15 |
| `192.168.0.0/16` 切成 size 20 | 16 |
| **合計** | **31** |

扣掉基礎設施吃掉的（docker 自己的 `bridge`、claude-pty 的 compose 專案網、這台機器上其他
compose 專案，以及升級前留下的 `claude-pty-sessions`），實務上大約是**同時 26 人在線**。
（jaeger **不佔**一格——它待在預設橋接上，見下方〈jaeger〉。）

⚠ **不要把這個數字寫死成宣稱。** 它取決於那台機器上還有多少別的東西，講死了就會變成一個
比機制還準確的說法。程式裡的錯誤訊息也是講「大約」。

⚠ 消耗量是**同時在線**人數，不是帳號數：沒有活著的 session 就會被回收。

### 怎麼放寬

在 daemon 的 `/etc/docker/daemon.json` 加一個更大的池，然後重啟 daemon：

```json
{"default-address-pools": [{"base": "10.200.0.0/14", "size": 24}]}
```

這樣是 1024 張。⚠ **真正的陷阱不是語法，是選錯網段**：那段位址一旦與公司內網或 VPN 的
路由重疊，容器會把本來要送去內網的封包留在本機，而症狀是「某些內部主機從容器裡連不到」
——完全指不到 docker 設定。挑一段確定沒有人用的。

### 用完的時候會怎樣

`user_proxy.PoolExhausted` → `sessions._ensure_user_network` 轉成 `SessionError`，
畫面直接說「開不了新的 session」、給出「關掉沒在用的 session」與「調 default-address-pools」
兩條路。**不吐 docker 的原文**（`all predefined address pools have been fully subnetted`
對使用者毫無意義），也**不退回共用網路**（見上面那條硬規則）。

## jaeger：誰需要它，誰把它接過來

jaeger **不擁有、也不借任何一張網**——它待在 docker 內建的預設橋接上
（`network_mode: bridge`，見 `opentelemetry/jaeger-compose.yaml`）。需要送 trace 的一方
負責 `docker network connect`：

- **claude-pty** — 控制平面把 jaeger 接到**每一張使用者網路**上（`user_proxy.attach_jaeger`）
- **dev-container** — run wrapper 把 jaeger 接到 `gitlab-proxy` 那張網上，再開容器

⚠ 反過來（jaeger 用 `external:` 去借別人的網）有兩個毛病：那張網必須**先存在**，於是產生
開機順序（先起對方再起 jaeger，反過來就 `network not found`）；而且它只借得到**一張**，
per-user 之後根本不夠用。

⚠ **連「自己建一張」都不做**（原本它有一張 `ncr-telemetry`，2026-08-08 移除）：位址池是
整台機器共用的，而這裡是一人一張網，所以 jaeger 那一格直接換算成「少一個人能同時在線」。
它從來不主動連任何人，只被連——自己那張網平常一個封包都沒有，不值得花掉一格。
預設橋接沒有內建 DNS 不影響：容器內用 `jaeger:4317` 找得到它，靠的是**接它過去的那張
使用者網路**的 DNS。

⚠ **接線點必須有三個**，因為網路與 jaeger 都可能在任意時刻出現：網路剛建好時
（`ensure_network`）、控制平面啟動時（`preflight`）、以及 reconciler 每一輪的差集補接
（涵蓋「jaeger 比網路晚起來」）。漏接的症狀是那個人完全沒有 trace，而 **OTLP 是 fail-open，
從頭到尾沒有任何錯誤訊息**。

⚠ **`telemetry_active` 要問兩件事**：控制平面探不探得到（`_jaeger_reachable`）、以及 jaeger
在不在**這個使用者那張網**上（`jaeger_on_network`）。只問前者的話，探測是從控制平面自己
那張網發出的，跟 session 待的那張網完全是兩回事——會得到「畫面說有在錄、實際一筆都沒有」，
而那比探測失敗更難查。

⚠ **回收網路之前要先把 jaeger 拔下來。** 掛著的容器會讓 `network.remove()` 直接失敗，
於是每一張使用者網路都變成永遠收不掉，位址池只出不進，症狀要等到「開不了 session」才浮現。
判準是「**除了 jaeger 之外**沒有別人」（`only_jaeger_left`）：真的還有 session 容器掛著時
不拔也不收，交給下一輪——先拔再發現收不掉的話，那個人的 trace 會靜靜停掉。

## 升級：正在跑的 session 不受影響

升級前開的 session 還掛在舊的共用網路上，會繼續正常運作到它們被關掉為止；**新開的**才是
隔離的。不需要遷移，也不需要停機。

舊的 `claude-pty-sessions` 沒有人會再用它，但它繼續佔著一格位址池，而 reconciler 只掃有
label 的網路、永遠不會碰它。`preflight` 看到它還在就報一行，叫人自己 `docker network rm`
——**只報不刪**：那張網上可能還掛著升級前、還在跑的 session，判斷「還有沒有人在上面」需要
的資訊比一句提醒多得多。訊息會在移除之後自己消失。

`CLAUDE_PTY_NETWORK` 同理：它已經沒有作用，但設了卻被靜靜忽略是最難查的那種，所以
`preflight` 看到它有值也報一行。

## 授權標頭：API 走 `PRIVATE-TOKEN`，git 走 Basic

兩條路徑的授權形式不同，而且**都不可以設在 server 層**：

| 路徑 | 標頭 |
|---|---|
| `/ping`、`/_state` | 無（不經上游，沒有憑證也答得出來） |
| `/api/v4/…` | `PRIVATE-TOKEN: <PAT>` |
| `….git/…` | `Basic base64(oauth2:<PAT>)` |

API 那條**刻意選 `PRIVATE-TOKEN` 而不是 `Bearer`**（兩者 GitLab 都收）。理由不是技術優劣，
是**可讀性**：`gitlab-proxy/nginx.conf.template`（同 repo 的獨立部署版）用的就是
`PRIVATE-TOKEN`，兩份用同一套慣例，讀者對照得起來。這個 repo 是連載的附件，
**看得懂是它的功能之一**，不是附帶效果。

而那份 template 的註解早就預告了這一天：

> 為什麼可以設在 server 層讓所有 location 繼承：這份設定只代理 GitLab API，每一條
> location 的授權方式都一樣。等到哪天要連 git clone 也走這個代理，這行就得搬進各自的
> location——git transport 不吃 PRIVATE-TOKEN 也不吃 Bearer，只吃 Basic，繼承下去會讓
> git 全部 401。

這次就是那一天。所以這一版把授權標頭從 server 層搬進各個 location，兩邊的檔案互相指回
對方，讓讀者看到那則預告兌現。

## 降級：沒有代理**不會**讓 session 開不起來

這條要寫死，因為它很容易在重構時被改成「開場失敗」——那看起來比較嚴謹，其實是錯的取捨。

**GitLab 不通是「這場少一個功能」，不是「這場沒用」。** 所以：

- 部署者沒設 `CLAUDE_PTY_GITLAB_HOST` → **不建代理**（網路照建），session 照開
- 使用者沒設 PAT → **不建代理**（網路照建），session 照開
- 代理建不起來（image 沒拉到、主機名解不開）→ **只警告**，session 照開，`gitlab_proxy` 記 `False`

⚠ **這一節講的是代理，不適用於網路。** 網路建不出來（位址池滿、daemon 不回應）是
**開不了場**，直接拋 `SessionError`——見上面〈不得退回共用網路〉。兩者的失敗語意刻意相反，
不要為了對稱統一掉：代理不在是少一個功能，網路不在是沒有地方可以待。

⚠ **但「降級」不等於「沉默」。** 每一條失敗路徑都要留下痕跡，而且要留在**看得到的地方**：

- 容器 log 一定有一行（`_ensure_user_proxy` 與 connect 各自的 `except`）
- 例外訊息**只印型別不印內容**——設定裡有 PAT，而例外訊息很容易被記進 log
- 代理**連續**起不來超過 `PROXY_FAIL_THRESHOLD` 輪時，把 nginx 自己說的那句話寫進
  `users.gitlab_proxy_error`，帳號頁直接顯示

沒有最後那一條的話，使用者看到的症狀是「GitLab 連不到」，於是去查 token、查網路、查
GitLab 是不是掛了——而唯一指得到真正原因的那句話（`[emerg] host not found in upstream`
＝主機名設錯）只在容器 log 裡。**降級是對的，讓人查不到原因不是。**

### 為什麼是「連續 N 輪」而不是第一次就報

代理偶爾重啟一輪是正常的（重新部署、daemon 抖動）。每次都對使用者喊「你的 GitLab 壞了」
就是狼來了，喊久了真的壞掉時沒有人會看。這條訊號要救的是另一類：**設定錯了、而且永遠不會
自己好**——nginx 在啟動時解析 upstream，解不開就拒絕啟動，於是每輪重啟、每輪再死，
而 `proxies_converged` 每輪 +1，**看起來還像在收斂**。

⚠ 連續計數**留在 reconciler 的記憶體裡**，不落 DB：租約保證同一時間只有一個 reconciler，
所以它天生只有一個來源；而它是過程量不是事實，重啟之後從零開始數正是對的。落到 DB 的只有
跨過門檻之後的**結論**。

⚠ `users.gitlab_proxy_error` 是**診斷麵包屑，不是權威狀態**。這與「設定的新舊一律問容器
自己」那條規矩不衝突，但界線要講清楚：**沒有任何判斷會讀這一欄**，它只把一句話搬到人看得
到的地方，所以就算過時也不會讓系統做錯決定。代理恢復的那一輪會清掉它——而且清除是
**無條件**的，不去判斷「先前有沒有報過」（那需要第二份狀態，一旦與 DB 不同步，畫面上那句
早就修好的錯誤會永遠留著，使用者會照著它去改一個本來就正確的設定）。

## 設定的新舊：問容器自己

代理提供 `GET /_state`，回一段指紋；reconciler 用
`docker exec <proxy> wget -qO- 127.0.0.1:5678/_state` 讀它，不一致就熱重載。

```
指紋 = HMAC-SHA256(由 SECRET_KEY 導出的金鑰, 把 state 留空所渲染出的完整 conf)[:32]
```

- **對整份 conf 做**（不是只對 PAT），所以它同時涵蓋「使用者換 PAT」與「**我們改了 conf
  產生器**」——後者讓白名單加一條端點之後，部署完所有代理自動 reload，不必有人記得
- **用 HMAC 不是裸 sha256**：`/_state` 就在使用者網路上，**session 裡的 AI 打得到**。
  裸 hash 等於把一個 secret 的 hash 交出去
- **不存 DB、不存 label**：DB 會出現「記錄說是新的、實際是舊的」；label 建立後根本改不了，
  熱重載完更新不了它

換 PAT 走**熱重載**（暫存檔 → `nginx -t -c` → `mv` → `SIGHUP`），不重建容器：重建會斷掉
這個使用者**其他** session 正在進行的 git 操作。也**不做 blue/green**——同一個 alias 兩顆
並存會 DNS round-robin，那不是零停機，是「一半請求用舊 PAT」。

⚠ **驗證必須在蓋上去之前。** 先寫再驗的話，`-t` 失敗時磁碟上留的是一份沒通過驗證的
`nginx.conf`，而「exited → 直接 start，設定已經在它裡面」那條捷徑會拿它冷啟動而起不來。
**「壞設定弄不死它」只在 HUP 這條路成立，冷啟動不成立。**

## PAT 讀不到時：三態，不可以合併

| `gitlab_pat_state` | DB 的樣子 | reconciler |
|---|---|---|
| `ok` | 有值且解得開 | 確保代理在、設定最新 |
| `none` | `NULL`（使用者**明確清除**） | **移除代理**——「我覺得外洩了」要立刻生效 |
| `unreadable` | 有值但解不開（**換過 `SECRET_KEY`**） | **什麼都不做** |

⚠ 「讀不到就不刪任何還能用的東西」這條規則本身是對的：換一次 `SECRET_KEY` 會讓**所有人**
的 PAT 一起解不開，拿它當期望狀態就是把所有還在服務的代理一起收掉。但同一條規則會讓
「清除 PAT」不再立即生效——而那是安全需求。**兩者的衝突只能靠分辨解決，不能選一邊。**

⚠ 第三種情況：欄位有值但被手動改壞。它會落在 `unreadable`，代理帶著舊 PAT 服務到 session
結束。方向保守（不會誤刪還能用的東西），**這是想過之後接受的，不是漏判**。

## 輪替語意：分界線在「這一場當初有沒有接上網路」

**不是**「帳號現在有沒有 PAT」。這條講得太粗就會變成錯的，所以寫死成一刀：

- **開場時沒接上代理網路的場** → 永遠沒有 GitLab。事後補 PAT 也救不了（網路必須在 `start`
  之前接，防火牆放行的是那一刻的路由快照）。要新的 session。
- **開場時接上了的場** → 此後**一直跟著這個帳號目前的 PAT 走**。網路還在、alias 還在，
  誰來應答 `gitlab-proxy` 就用誰的憑證。

於是：

| 動作 | 對「開場時接上了」的場 | 對「開場時沒接上」的場 |
|---|---|---|
| 換一把新的 PAT | 一個對帳週期內**改用新的** | 沒有效果 |
| 清掉 PAT | 代理被收掉，**立刻**不能用 | 沒有效果 |
| 清掉之後**再填回一把** | **會恢復**，並使用新填的那一把 | 沒有效果 |

⚠ **最後一列是危險的那一側，要講明。** 清掉 PAT 時代理被收，但**網路還在**（只有沒有活
session 的網路才會被回收），填回去之後補建迴圈會把代理放回同一個網路——那些場在一個
對帳週期內**全部重新武裝，而且拿到的是新的 PAT**。

外洩情境的自然反應正是「清掉、換一把新的」，而那**不會**把舊場關在外面。

**維持這個行為，不改。** 考慮過的替代是「清 PAT 時連網路一起收」，但那會**永久**廢掉這個
人所有正在跑的 session 的 GitLab，而且救不回來。對最常見的情況——PAT 到期、例行換一把
——那個代價完全不成比例。取而代之的是把界線寫成一句可以照做的準則：

> **輪替 PAT 不等於隔離一場你不信任的 session。要隔離那場，就終止那場。**

PAT 輪替解決的是「憑證本身可能外流」，它換掉的是鑰匙；而「我不信任那個 session 裡正在跑
的東西」是另一個問題，鑰匙換了它照樣拿到新的。兩者用不同的工具，後者的工具是終止。

## 要讀兩個事實，不是一個

- `sessions.gitlab_proxy` ＝**這場當初有沒有接上代理的網路**。不可變。
- 擁有者**現在**還有沒有 PAT ＝ 那條路的另一端還在不在。

只看前者，使用者清掉 PAT 之後畫面會一直說「本場可用」而 git 全部失敗。只看後者，事後
補 PAT 會讓畫面對著一場根本沒接上網路的 session 說「可用」。

**不去翻那個快照欄位**：翻了之後使用者把 PAT 填回來就得翻回去，而「哪些場當初接上了網路」
沒有辦法事後重建，翻不準。

歷史紀錄的時間視角不同：session 結束後已經沒有「現在是否可用」，所以歸檔時把快照原樣搬到
`session_history.gitlab_proxy`。欄位上線前的舊歷史是 `NULL`，**不畫**——把不知道畫成暗燈
是在謊稱「確定未啟用」。

### 畫出來長什麼樣（已實作）

列表那排純圖示標記（網路 · 錄製 · telemetry）後面多一顆 `fa-brands fa-gitlab`，
語意全靠 tone 與 tooltip（`sessions.html` 的 `chips()`）：

| `gitlab_proxy` | `gitlab_pat_set` | tone | 說法 |
|---|---|---|---|
| `true` | `true` | accent | 本場可用 |
| `true` | `false` | **warn** | 當初接上了，但你現在沒有 token → git 會失敗；填回去一個對帳週期內恢復 |
| `false` | 不看 | off | 本場沒有——開場時沒接上，事後補 token 救不了，要開新的一場 |
| `null` | — | **不畫** | 欄位上線前的舊列，是「不知道」 |

歷史那張表只讀一欄：`true` → accent「期間曾啟用」、`false` → off、`null` → 不畫。

⚠ **整顆標記由部署層的總開關 gate**（`web.sessions_page()` 把 `config.gitlab_enabled()`
給模板）。沒有這道 gate 的話，**沒設 `CLAUDE_PTY_GITLAB_HOST` 的部署**——也就是預設——
每一場的 `gitlab_proxy` 都是 `False`，於是整欄長出灰色 GitLab 圖示，對著使用者講一件那台
機器上根本不存在的事。這是部署層的事實，不從列表 API 的每一列去推。

⚠ 危險的是 `true` + 沒有 token 那一格：它最容易被寫成 accent（「有接上啊」），而那正是
使用者清掉 token 之後畫面說「可用」、git 全部失敗的那個情境。`e2e_gitlab_chip` 釘著它，
連同「`null` 不畫」與「功能關掉時一顆都沒有」。

## 收斂：代理是期望狀態

reconciler 每輪把它當 k8s 的 Deployment 看待——該在而不在就補，不該在就收：

- 有活著的 session 且 `ok` → 網路在、代理在跑、設定最新（過期就熱重載）
- 代理退出了 → `start`（設定還在它裡面，不必碰 PAT）
- **該有卻一顆都沒有 → 補建。這一輪不可省**：任何讓容器整個消失的路徑（手動 rm、建到
  一半失敗）否則都會變成永久且無聲的失效
- `none` → 移除代理
- 沒有活著的 session → 移除代理與網路（釋放位址池）
- **功能被關掉**（`CLAUDE_PTY_GITLAB_HOST` 拿掉）→ 期望狀態變成「一顆都不該有」，
  所以收斂**照跑**，把既有的收乾淨。⚠ 這一條容易漏：直覺會寫成「關掉就跳過這整段」，
  但那樣那些代理會**帶著 PAT 永遠留在機器上**，還繼續佔著位址池，而且再也沒有任何東西
  會回頭看它們。**關閉＝收乾淨，不是停止管理。**

⚠ **「有活著的 session」不可以只看輪初的容器快照**，要含 DB 裡還在建立寬限期內的列
——否則「上一場剛結束、下一場正在建立」會被判成「沒有 session」而收掉正要被接上的代理。

⚠ **網路的寬限期依網路自己的建立時間**，不是代理的：在「建好網路」與「建好代理」之間
網路是空的，那時沒有代理可以查年齡。

### 併發與半成品

代理是 per-user 的**一顆**，而建立它的路徑有兩條（web worker 建 session 時、reconciler
補建），worker 本身又有多個。三條規矩：

⚠ **撞名稱衝突要當成「別人搶先建好了」**。不接的後果不是「這場沒有代理」而已——例外會冒到
建立端的補償，而它看到勝方那顆停在 `created` 就會 force-remove，**敗方把勝方的代理刪掉**。

⚠ **`created` 不可以直接 `start`。** 它有兩種來源、外觀相同：設定還沒送進去（是 image 的
預設）、或已經送完。start 前者會得到一顆**永久的殭屍**——nginx 用預設設定開在 80，容器
`running` 看起來很健康，但 5678 連不上；此後 reconciler 只走 running 分支、`/_state`
問不到，依「問不到就別亂動」永遠不修。兩條路徑一律：夠舊才收掉重建，還新就別碰。

⚠ **補償只能收「自己這次建的那一顆」。** 用「問不問得到 `/_state`」當判準會誤殺一顆正在
服務**這個人其他 session** 的健康代理（觸發補償的 daemon 抖動與 exec 失敗高度相關）；
用年齡當判準則擋不住別人正在建的那顆。

## 設定：網域一律由部署者填，沒有預設值

`CLAUDE_PTY_GITLAB_HOST` **預設是空字串，代表整個功能關閉**——一顆代理都不建、不改寫
git URL、設定頁不收 PAT。

刻意不給一個「看起來像真的」的預設值：那樣沒設定的部署會真的去建代理、對著別人的主機打，
而回來的錯誤指不到原因。空字串是唯一誠實的「沒設定」。

三個旋鈕：上游主機（`CLAUDE_PTY_GITLAB_HOST`）、SSH 主機（`_GITLAB_SSH_HOST`，只用於
URL 改寫，預設沿用上游）、session 那端看到的 API base（由 `_GITLAB_PROXY_ALIAS` 與
`_PORT` 組出來，以 `NCR_GITLAB_API_BASE` 注進容器，呼叫端不必寫死）。

## git URL 改寫

沒有它，per-user 代理在實務上等於不能用：每份既有 repo、每段複製貼上的指令寫的都是正規
網址，而那個位址在 session 裡是直接失敗的（防火牆不放行直連 443，那正是設計要的）。
使用者第一次遇到的症狀是 `Failed to connect`，完全看不出要去改 URL。

走 `GIT_CONFIG_*` 環境變數而不是寫 `~/.gitconfig`：後者要嘛動到兩條路徑共用的
`entrypoint.sh`，要嘛落進 per-user 空間變成一份會漂的檔案。

三條 `insteadOf`（`insteadOf` 是多值鍵）：`https://host/`、`git@host:`、`ssh://git@host/`。
SSH 的兩種也要改，因為 session 裡 SSH agent 預設不掛、防火牆也不放行 22，那條原本是必定
失敗的，而症狀（`Permission denied (publickey)`）完全指不到「該用 https」。

⚠ 兩個結尾字元是硬要求：https 那條**結尾的斜線不可以拿掉**（沒有它就變前綴比對，
`https://<你的 host>.evil.example/…` 會被導進代理並蓋上真的 PAT）；scp-like 那條
**結尾必須是冒號**（寫成斜線不會報錯，只是靜靜不改寫）。

## 沒買到什麼

- **位址池是有限的**：每個「同時有 session 在跑的 GitLab 使用者」佔一個 docker network，
  **而且是整台機器共用**（每個 compose 專案都佔一格）。對策：沒有 session 就回收網路，
  消耗＝**同時在線**的使用者數；用完時在建立的當下報錯，訊息會把「池滿」與其他錯誤分開
  並講出下一步；真的不夠就改 `daemon.json` 的 `default-address-pools`。
  ⚠ **刻意沒有做啟動時的預先探測**：那要在啟動路徑上攪動一個全機器共用的資源，而且多個
  worker 會互相搶同名的探測網路。**在事情發生時講清楚，勝過事先猜一個數字。**
- **不支援 git-lfs**：LFS 的 batch API 會回外部 href（可能直指物件儲存），nginx 改不掉。
  有用 LFS 的 repo 會靜默壞掉。
- **session 存活期間，裡面的東西仍能透過代理做白名單允許的任何事。** 買到的是「帶不走」
  與「範圍收斂」，不是「不能濫用」。

## 沒有採用的

- **維持 per-session**：限流結構上做不到 per-user 總量（見上）。它唯一的優勢是不消耗任何
  docker 網路——那是真的優勢，代價是上面那些。
- **共用網路 + 靜態 IP + iptables 放行特定對象**：不吃位址池，但同 alias 會 round-robin，
  而且跨使用者的隔離要靠**每個 session 自己的防火牆**——`unrestricted` 根本沒有防火牆。
  **用兩層機械模擬網路邊界天然就有的性質，而且只在一種 profile 下成立。**
- **blue/green 換容器**：同一個 alias 兩顆並存就是 round-robin，那不是零停機。
- **把指紋存進 DB 或 label**：多一份會說謊的狀態；label 更是建立後改不了。
- **加 docker healthcheck**：沒有任何東西會消費 health（沒有 orchestrator、restart policy
  刻意不設），而 reconciler 每輪探 `/_state` 本身就兼存活探測。

## 上游 TLS：驗，而且只能靠給它正確的 CA

代理對 GitLab 是 `proxy_ssl_verify on`，信任錨預設是容器內的系統 CA。

**內部 CA 簽的 GitLab 因此預設不能用**，而失敗的形狀是這整個 ADR 裡最惡劣的一種：

- 容器**是健康的**（nginx 起得來、`/ping` 與 `/_state` 都答得出來），
- 所以 reconciler 的存活判斷認為它好好的，`users.gitlab_proxy_error` **不會亮**
  ——那條訊號偵測的是「代理沒活著」（見〈為什麼是「連續 N 輪」〉），
- 而每一個 git 與 API 呼叫都在 TLS 那關失敗回 502。

也就是說：**畫面全綠、功能全掛，而專門為「設定錯了而且永遠不會自己好」設計的那條訊號
守不到它。** 這是「一條訊號存在，但它守的不是這個失敗」的實例。

### 決定

`CLAUDE_PTY_GITLAB_CA_FILE` 收 host 上那份 CA（PEM）的絕對路徑，唯讀掛進每一顆代理，
`proxy_ssl_trusted_certificate` 指過去。不設＝維持現狀（系統 CA）。

**沒有關掉驗證的開關，而且不會有。** 這顆容器存在的唯一理由是保管別人的 PAT；對上游
不驗憑證，等於把「PAT 不進 session」買到的東西在代理到 GitLab 這一段原樣送給任何一個
中間人。`proxy_ssl_verify off` 會讓事情「動起來」，然後在沒有任何訊號的情況下一直錯下去。

### 三件配套，少一件這個功能就是半殘的

1. **啟動自檢要喊。** 填了路徑卻找不到檔案時，`sessions.preflight` 直接報，並講出症狀
   （502、而狀態是綠的）。**不可以靜靜退回系統 CA**——那會變成「設定了、重啟了、什麼
   都沒變」，與這個功能要解決的問題同一種。
2. **換路徑要重建容器。** CA 是 bind mount，而掛載是 `create` 時決定的——熱重載換不掉。
   所以收斂時要比對「這顆代理實際掛的 CA」與「現在的設定」（`user_proxy.ca_mount_matches`），
   不一致就重建。少了它，改設定永遠不會生效。
   ⚠ 兩條路徑的時序刻意不同，同〈併發與半成品〉的取捨：`sessions` 那邊**當場重建**
   （有人正在等他的 session），reconciler 是**下一輪**才補。
3. **續簽要能收斂。** 續簽是路徑不變、內容變——只比路徑會完全漏掉，而 nginx 會抱著記憶體
   裡那份舊的 CA 繼續驗。所以 CA 的**內容摘要**進了設定指紋（`gitlab_proxy.ca_digest`），
   換一次 CA 下一輪就自己熱重載。那一行摘要寫在 conf 的註解裡，是公開憑證的雜湊、不是秘密。

## 兩份白名單

API 白名單同時存在於 `server/gitlab_proxy.py` 與 `gitlab-proxy/nginx.conf.template`
（獨立部署那一套）。加一個端點要改兩個地方，漏了任一份的症狀是「一條路通、另一條 403」。

**兩邊都留了 SYNC 註解**，所以不論從哪一邊動手都會被提醒。

不合併是因為兩者的產生方式不同：獨立版是 `envsubst` 展開的靜態 template（沒有控制平面），
這一版是 Python 依 PAT 現算的。硬要共用會讓兩邊都得遷就對方的機制。
