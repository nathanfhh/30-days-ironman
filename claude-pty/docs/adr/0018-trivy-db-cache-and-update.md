# ADR 0018：trivy DB——誰負責更新它，以及那份快取住在哪

- 狀態：已接受；已實作

## 背景

A2 軌道（trivy 掃描）要有漏洞 DB 才做得了事，而 DB 要從 mirror.gcr.io 抓
（約 104 MiB，解開後超過 1 GB）。這件事有兩個獨立的問題，這份 ADR 一起處理。

### 問題一：網頁這條路徑從來沒有更新過 DB

`dev-container/entrypoint.sh` 裡 `trivy` 的出現次數是 **0**。也就是說：

- **run script**：每次啟動前，在容器起來之前、另一顆一次性容器裡更新一次（牆外）。
- **網頁**：只掛快取，**沒有任何更新動作**。全靠「這台機器上曾經有人跑過 run script」。

而 restricted profile 的白名單沒有 ghcr.io / mirror.gcr.io，session 自己在牆內也抓不到。
所以純網頁使用者的 A2 軌道，用的是那份快取當下的內容——可能很舊，而且**沒有任何訊號**：
trivy 照樣跑完、照樣回報，只是資料是三天前的。

更糟的是 `config.MOUNTS` 那段註解一度寫著：

> entrypoint.sh 在套 iptables 之前**必須**等 DB 更新跑完

**那個機制不存在。** 註解描述了一個沒有人實作的世界，而它讀起來完全像已經做完的事。

### 問題二：快取是 host 目錄，於是它也在 uid 對齊那條鏈上

ADR 0017 講的那條鏈（① host 帳號＝② 控制平面＝③ session 的 nathan）之所以要對齊，是
因為 bind mount 在 Linux 上不做 uid 翻譯。trivy 的快取原本是 `~/.cache/ncr-trivy`，
由控制平面 `makedirs`、由 session 容器寫入——第三個要對齊的東西。

## 決策一：更新搬進控制平面，不是 entrypoint

```
讀自己寫的時間戳（TRIVY_DB_STAMP）
  還在節流間隔內 → fresh，連容器都不起
  超過           → acquire_lease("trivy-db")
                     拿不到 → skipped（有人正在更新），**不等待**
                     拿到   → 一次性容器（bash entrypoint、牆外、timeout 與 run script 對齊）
                                成功        → ok（寫下時間戳）
                                失敗有舊 DB → stale（警告，照常開場）
                                失敗無舊 DB → missing（警告，A2 由 skill 自行降級）
docker 掛掉／逾時 → error
```

**六種結果全部回 dict，一個都不拋。** 它是選配設施，不是開場的前提：沒有 DB 的 A2 走
skill 自己的降級規則（跳過並揭露），那比「開不了場」好得多。但**結果一定要印出來**，
否則「DB 三天沒更新」跟「剛更新完」在畫面上長得一模一樣——那正是這份 ADR 在修的病。

### 為什麼不放 entrypoint（那裡才是註解說的地方）

實測（2026-08-10）兩個併發的 `trivy image --download-db-only` 打同一份快取：

- **不會壞。** 兩邊 exit 0，產出的 DB 拿去掃 `requests==2.19.1` 回 5 筆，與已知良好的
  DB 逐筆相同。靠的是「下載到暫存、完成後 atomic rename」——快取目錄裡**沒有任何鎖檔**。
- **但兩邊都真的下載了 103.85 MiB，沒有去重。**

所以放在每一場的 entrypoint，N 場同時開就是 N 份下載。放控制平面是單一入口，一個租約
就串行化得掉。代價是與 run script 的邏輯各寫一份，那是划算的交換。

租約原語因此從 `reconciler.py` 抽到 `leases.py`——reconciler 已經 import sessions，
sessions 再回頭 import reconciler 就是循環。

## 決策二：快取改用 named volume

```yaml
volumes:
  trivy-cache:
    name: ncr-trivy-cache      # 固定名稱，不吃 compose 的 project 前綴
```

```dockerfile
USER nathan
RUN mkdir -p /home/nathan/.cache/trivy
```

**volume 只要在掛載時仍為空，docker 就用該 image 那個路徑的內容與擁有者初始化它。** host
帳號的 uid 完全不進場，trivy 因此離開 ADR 0017 那條鏈。名稱固定是為了讓 run script 掛
得到同一顆——兩條路徑繼續共用那 ~1.2 GB，改名等於分家。

### Dockerfile 那一行是承重的，不是整潔

image 裡沒有那個路徑的話，volume 會被建成 **root:root 0755**。實測：以 uid 1999 的容器掛
一顆這樣的 volume，一個字都寫不進去（而 trivy 除了 DB 還要往同一份快取寫掃描結果）。

**精確的規則（實測）是「掛載時仍為空就會被該 image 初始化」，不是「只有第一次掛載」。**
所以被錯的 image 掛過而沒寫東西，下一次由正確的 image 掛還會被修好；但只要有任何東西在
root 擁有的狀態下被寫進去，volume 就**永久**卡在 root，而且無聲。

`tests/test_trivy_volume.py` 把正反兩面都釘住了，而且它在**改版前的 image 上是紅的**
——那就是這行 `mkdir` 的價值證明。

### ⚠ 因此：控制平面**不可以**掛這顆 volume

這是最反直覺、也最容易被「順手優化」掉的一條。

原本想把 volume 掛進控制平面，好讓它讀 `db/metadata.json` 判斷鮮度。控制平面的 image
沒有 `/home/nathan/.cache/trivy`，而它比任何 session 都早啟動——那顆 volume 於是會以
root:root 空著。

**它不必然致命**（實測：只要還空著，下一次由 session image 掛就會被修好），但它多開了
一個會永久卡死的機會：只要有任何東西在 root 擁有的狀態下被寫進去，就再也修不回來，
而且無聲。**不掛它也達得到目的，那就不要拿這個賭注換一顆容器的啟動時間。**

所以鮮度判斷改成**控制平面自己寫的時間戳**（`TRIVY_DB_STAMP`，放在 `/data`，與 SQLite
同一個掛出來的目錄）。它只是「要不要費事起一顆容器」的節流器——真正的鮮度判斷仍然在
容器裡由 trivy 自己做（`--download-db-only` 該 no-op 就 no-op）。所以這支保守一點
（讀不到就去更新）永遠是安全的，只多花一次容器啟動。

同理，「有沒有既有 DB」也只能從時間戳推，判準刻意保守：曾經成功過就當還有（→ `stale`），
從沒成功過才敢說沒有（→ `missing`）。寧可把「其實沒有」報成 stale，也不要把「其實有」
報成 missing——後者會讓人白跑一趟去救一個沒壞的東西。

## 後果與遷移

- **部署順序是硬要求：先 rebuild image、後 redeploy 控制平面。** 反過來的話，控制平面
  已經指向 volume 而 image 還沒有那個路徑 → 空 volume 沒東西可初始化 → root:0755，
  接著第一次 trivy 更新就以 root 寫進去 → **從此永久卡住**。
  **這一條 macOS 也會中**：它不是 uid 問題，是 volume 初始化問題。
- **既有的 `~/.cache/ncr-trivy`（約 1.2 GB）變成孤兒。** 可以直接刪，或搬進 volume 省掉
  一次重抓：

  ```bash
  docker run --rm -v "$HOME/.cache/ncr-trivy":/old \
      -v ncr-trivy-cache:/home/nathan/.cache/trivy ncr-dev-container \
      bash -c 'cp -a /old/. /home/nathan/.cache/trivy/'
  ```

  ⚠ 這顆容器要用 **session 的 image**（它有那個路徑、屬於 nathan），volume 才會被正確
  初始化。用 alpine 之類的做這件事就是上面那個毒化情境。
- **換 `NCR_UID` 之後要 `docker volume rm ncr-trivy-cache`**：volume 一旦有內容就不會再
  被初始化，擁有者停在舊的 uid，新 uid 寫不進去。
- `preflight` 對 `MOUNTS` 的 key 跑 `os.path.exists()` 時要**跳過非絕對路徑**：volume 名
  恆 False，不跳的話非容器化部署每次啟動都收到一句假的「掛載來源不存在」。
- run script 判斷「有沒有既有 DB」也要**進容器裡問**：volume 的內容 host 上看不到
  （在 `/var/lib/docker` 底下，macOS 更在 VM 裡）。原本的 `[ -s "$DIR/db/trivy.db" ]`
  改成 volume 之後會永遠是 false，於是「有舊 DB」被誤報成「完全沒有」。

## 沒有採用

| 方案 | 為什麼不 |
|---|---|
| 在 entrypoint 更新（註解原本描述的） | N 場同時開就是 N 份 104 MiB 下載，而且沒有地方掛租約 |
| 把 volume 也掛進控制平面以讀 metadata | 多開一個「volume 永久卡在 root」的機會，換到的只是省一顆容器的啟動，見上 |
| 維持 host 目錄，只補更新 | 可行，而且改動小很多。但 trivy 會繼續留在 uid 對齊那條鏈上，多一個部署時要對齊的東西——既然 image 那行 `mkdir` 只有一行，就一併拆掉 |
