# 限制模式與 DNS：現況、為什麼這樣、以及要收掉的話有哪些路

**這一輪的決定是揭露，不是實作。** 下面第二節是評估，還沒有驗證過，不要照著抄。

## 一、現況

`dev-container` 的限制模式（`init-firewall.sh`）用 iptables 白名單收斂出站：一般的
TCP／HTTP(S) 只通 `api.anthropic.com`、直連的 docker 網段（gitlab-proxy），SSH 22 只通
build 時指定的那台 GitLab。

**DNS（53）是放行的**，因為白名單本身要靠網域解析才成立。

但要注意這條規則實際承擔的東西比字面上少。容器接到**自建 network** 時，`/etc/resolv.conf`
指向 `127.0.0.11`，查詢走 loopback 交給 Docker daemon 轉發，nat 表的 DNAT 又跑在 filter 之前，
所以封包到 `OUTPUT` 鏈時目的埠已經不是 53。實測（alpine，自建 bridge network）：

```
iptables -I OUTPUT 1 -p udp --dport 53 -j REJECT   →  解析成功，該規則計數器 0 packets
iptables -I OUTPUT 1 -d 127.0.0.11    -j REJECT   →  解析 refused，計數器 1 packet 80 bytes
```

`--dport 53` 那條只在「接 Docker 預設 bridge、`resolv.conf` 指向外部 resolver」時才會被走到。

由此得到一個必須講清楚的性質：

> DNS 的查詢名稱是一條出境通道。把資料編進 query name 送出去
> （`<資料>.attacker.example`），即使那台主機一個封包都回不來，資料已經走了。

所以限制模式**不是** Data Loss Prevention，也不是資料外洩的隔離邊界。它擋的是
「agent 直接把東西送到某個網站」這一類直來直往的路徑，擋不住刻意的低頻編碼外送。

這是已知並接受的限制。這個容器的定位是「讓一個我不完全信任的 agent 住進去，
而且它做的每一件事我都看得到」，不是「即時圍堵一個正在攻擊的內部人」。

## 二、要收掉的話，有哪兩條路

### 路 A：啟動時解析完 allowlist，然後封掉 UDP/TCP 53

agent 起來之前先把白名單網域解析成 IP 寫進規則，接著把 53 一起關掉。

- **先決條件（第一節那個實測）**：在自建 network 上，「把 53 關掉」不能寫成 `--dport 53`，
  那條規則不會被走到。要擋的是往 `127.0.0.11` 的流量，或是直接換掉 `/etc/resolv.conf`。
  這是這條路最容易寫成半套的地方：規則加了、看起來對、一個封包都沒攔到。

- **好處**：改動小，就在既有的 `init-firewall.sh` 裡多兩步，不引入新元件。
- **CNAME**：要遞迴解析到底並把鏈上每一段的 A/AAAA 都收進來，只解析最終答案會漏。
- **TTL 與 CDN 漂移**：`api.anthropic.com` 走 CDN，IP 會換。啟動時那一組 IP 撐不了
  多久，長 session 會在中途開始連不上，而症狀是 timeout 不是「被擋」，很難認。
- **故障模式**：解析失敗時要 fail closed（不放行）還是 fail open（維持 53）？
  fail closed 會讓沒網路的環境完全開不了場。
- **適用**：短工作、IP 穩定的目標。以這裡的用法（長時間互動 session、目標在 CDN 後面）
  是最容易踩到 CDN 漂移的一條。

### 路 B：受控 resolver，只回答核准的網域

容器內的 53 指到自己的一台 resolver，白名單外的查詢直接拒絕。

- **好處**：CNAME 與 TTL 都由 resolver 正常處理，CDN 漂移不是問題；
  而且它天生就是那份紀錄——誰查了什麼、什麼時候查的，全部留得下來。
- **代價**：多一個元件要跑、要設、要在它掛掉時有說法。
- **擋不住的**：核准網域**底下**的子網域仍然可以編資料
  （`<資料>.api.anthropic.com` 若被通配放行）。所以規則要是精確比對，不是後綴比對。
- **故障模式**：resolver 掛掉＝整場沒有網路，這一點要在啟動時就講清楚，
  不能讓人以為是模型 API 出問題。

### 這個場景該選哪一條

以現在的用法（單機或小型多人、長時間互動 session、目標在 CDN 後面），**路 B 比較合**。
路 A 的 CDN 漂移會在最不該出事的時候出事，而且症狀不像被防火牆擋。

### 要怎麼驗

不管選哪一條，驗收都是同一組：

1. 正常路徑仍然通（開一場、跑一次審查、`api.anthropic.com` 沒有中斷）。
2. `dig <長字串>.example.com` 這種查詢**確實被拒絕**，而且拒絕有留下紀錄。
3. 長 session 的耐久測試：跑滿一個明顯超過 CDN TTL 的時間，中途不會斷。
4. 故障注入：resolver 或解析步驟失敗時，行為與訊息符合上面寫的那一種，
   而不是安靜地退回「全通」。

第 4 條最重要。半套的 DNS 防線最糟的失效方式，就是它在失敗時安靜地讓路。
