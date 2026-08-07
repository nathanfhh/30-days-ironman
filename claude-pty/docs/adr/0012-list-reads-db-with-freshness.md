# ADR 0012：列表只讀 DB，並把「幾點跟 dockerd 求證的」講出來

- 狀態：已接受；已實作

## 背景：一顆容器卡住，全站停擺

實際發生過。一顆容器的 `docker rm -f` 送出的 SIGKILL 生效了，但**移除**卡在 `removing`
狀態將近 40 分鐘。期間 daemon 對這一顆容器的 `inspect` / `logs` / `top` 一律不回應
（per-container 鎖），而 `docker ps` 秒回（它讀的是 daemon 記憶體裡的清單）。

後果遠大於「那一顆收不掉」：

- **reconciler 每輪陣亡**。`remove_container` 丟的是 urllib3 的 `ReadTimeout`，而那條
  路徑只接 `docker.errors.APIError`，例外一路穿出、被主迴圈接成「本輪失敗」——與那顆
  容器毫無關係的歸檔、view 清理、租約清理全部停擺。
- **列表 API 每次卡滿 timeout**。當時 `list()` 會為「還沒就緒過」的列各打一次
  `docker logs`；docker-py 預設 timeout 60 秒，而這支端點每 15 秒被輪詢一次 → gunicorn
  的 thread 很快被吃光 → 對使用者而言是整個後端死掉。

一顆容器的故障，變成所有人都看不到任何東西。

## 決策

**列表路徑完全不碰 docker；狀態由 reconciler 在背景更新進 DB，並記下求證時刻，前端把
新鮮度顯示出來。** 四件事一起做才成立：

1. **`sessions` 表新增 `docker_state` / `state_checked_at`**：「最後一次真的問到 dockerd
   的狀態」與「那是什麼時候」。兩欄 nullable，NULL＝從來沒問到過。
2. **reconciler 逐顆隔離**：每個 per-container 的 docker 呼叫都包起來，**接 `Exception`
   而不是 `docker.errors.APIError`**——那個差別就是上面那 40 分鐘。失敗回哨兵、這一顆
   這輪跳過、下輪再試，其餘的事照做。（失敗值刻意不是 `None`：`remove_container` 成功時
   回的就是 `None`，用 None 當失敗記號等於把每次成功都當失敗。）每輪唯一不隔離的是那一發
   `containers.list()`：它掛掉代表 daemon 整體有問題，那時本來也做不了任何事。
3. **前端顯示新鮮度，舊了自己標出來**：每列狀態旁寫「N 分鐘前確認」，超過對帳週期的
   數倍轉警示色，語意是「對帳可能卡住」不是「你的 session 有問題」。**沒問到過顯示
   「未確認」**，不可以畫成「剛剛確認」——把不知道的事畫成最有信心的樣子，正是這個欄位
   要消滅的東西。
4. **所有 docker client 給有界 timeout**（取代 docker-py 的預設 60）——這不解決連坐，只
   決定單一次意外的代價。

## 取捨

- **列表顯示的狀態最舊會差一個對帳週期。** 這正是 `state_checked_at` 要誠實講出來的事。
  反面設計（永遠即時、但一顆壞掉就全部看不到）已經實測過，代價是 40 分鐘。
- **單筆查詢 `GET /api/sessions/<id>` 仍然問 docker**（`?wait_ready` 的輪詢靠它）：爆炸
  半徑不同，問壞了只有這一筆受影響，且問不到時退回最後已知狀態而不是拋錯。
  ⚠ 它**不寫 DB**：那兩欄的唯一寫入者是 reconciler。曾讓它「問到就順手更新」，結果併發
  的讀後寫交易撞成 `database is locked`。
- **沒有把「卡住的容器」自動修好**：清不掉就留著、下輪再試並計數。自動去 kill 需要 host
  權限，也不是控制平面該做的事。
