# Linux 部署驗收清單（uid 對齊 / trivy volume）

**為什麼需要這份**：ADR 0017 與 0018 處理的問題**只在 Linux 上存在**。macOS 的 Docker
Desktop 對 bind mount 做 uid 對映（實測：同一個 host 目錄，在 uid 1001 的容器裡顯示
owner 1001、在 1000 的容器裡顯示 owner 1000，兩邊都可寫），所以整條 uid 鏈在 macOS 上
**驗不到**——不是「驗過沒問題」，是「那台機器上不會發生」。

開發與測試都在 macOS 上做的。這份清單是要在一台真的 Linux 上走一次，把「機制推導」
變成「整合驗證」。

每一項都寫了**期望**與**壞掉長什麼樣**。症狀那一欄是重點：這條線上的失敗幾乎沒有一個
看起來像 uid 問題。

---

## 0. 先記下三個數字

```bash
id -u                                          # ① 你
grep '^APP_UID=' claude-pty/deploy/.env        # ② 控制平面
docker image inspect -f '{{index .Config.Labels "ncr.uid"}}' ncr-dev-container   # ③ session image
```

**三個必須相同。** 不同就先修再往下——後面每一項都建立在這個前提上。

> 第一次部署、image 還沒 build 的話，③ 會是空的或 `<no value>`，那是正常的，
> 做完 §1 再回來看。

---

## 1. Build：`NCR_UID` 真的被帶進去了

```bash
cd dev-container && ./build.sh
```

| 檢查 | 指令 | 期望 |
|---|---|---|
| 腳本有認出是 Linux | （看輸出） | `🔢 host 是 Linux → NCR_UID=<你的 id -u>` |
| LABEL 有 stamp | `docker image inspect -f '{{index .Config.Labels "ncr.uid"}}' ncr-dev-container` | ＝ `id -u` |
| ENV 有 stamp | `docker run --rm --entrypoint sh ncr-dev-container -c 'echo $NCR_UID'` | ＝ `id -u` |
| 容器內的 nathan | `docker run --rm --entrypoint sh ncr-dev-container -c 'id nathan'` | uid ＝ `id -u` |
| `id -u`＝1000 時，base 的 ubuntu 帳號被移掉 | `docker run --rm --user root --entrypoint sh ncr-dev-container -c 'id ubuntu 2>&1'` | `no such user` |
| 沒有孤兒檔 | `docker run --rm --user root --entrypoint sh ncr-dev-container -c 'find / -xdev -nouser 2>/dev/null \| head'` | 空 |

**壞掉長什麼樣**：`./build.sh` 印出 `host 是 …` 但不是 Linux → `uname -s` 回了意外的值，
那一整段就跳過了，image 會是預設的 1001。

---

## 2. 直接 `docker build` 的那條路要被擋住

這是整套設計的重點：**忘記帶 build-arg 不會失敗，只會安靜地給 1001。**

```bash
docker build -t ncr-dev-container .            # 故意用錯的方式
cd ../claude-pty/deploy && ./redeploy.sh
```

| 檢查 | 期望 |
|---|---|
| `redeploy.sh` 的反應 | **exit 1**，印出三個數字（你 / APP_UID / image），並給出下一句該打的指令 |
| 訊息有沒有教你搬既有狀態 | 有 `chown -R`、`docker volume rm ncr-trivy-cache`、清時間戳三行 |

然後照它說的重建、再跑一次，應該看到 `🔢 uid 對齊：你 / APP_UID / image 都是 <n>`。

⚠ **這一項在 `id -u` 剛好是 1001 的機器上會因為錯的理由變綠**：預設值就是 1001，
所以「忘記帶 build-arg」與「帶對了」產出同一顆 image，守衛當然不會擋。那不代表守衛有效。
要真的驗它，改用一個一定不對的值：

```bash
docker build --build-arg NCR_UID=1234 -t ncr-dev-container .
```

**壞掉長什麼樣**：redeploy 直接過了 → 那道守衛沒生效（多半是 `CLAUDE_PTY_HOST_PLATFORM`
沒被帶到，檢查 `redeploy.sh` 有沒有 export 它）。

---

## 3. per-user 狀態空間（0700，這是 uid 鏈最主要的那一環）

開一場 session，然後：

```bash
ls -ldn "${CLAUDE_PTY_SPACE:-$HOME/claude-pty-space}"/user-*/{claude,persistent-data,ncr}
```

| 檢查 | 期望 |
|---|---|
| 擁有者 | 三個目錄都是 `id -u` |
| 權限 | `700` |
| 容器內寫得進去 | `docker exec <session 容器> bash -c 'touch ~/.claude/.probe && echo ok && rm ~/.claude/.probe'` |

**壞掉長什麼樣**：**每一場都撞 onboarding 對話**，而那道對話預設停在「No, exit」——
看起來像 CLI 的問題，不像權限問題。

---

## 4. CLI 憑證（0600，注入容器的那份）

在帳號頁貼一把 CLI token，再開一場。

| 檢查 | 期望 |
|---|---|
| 終端起來之後 | 直接可用，**不是**停在登入提示 |

**壞掉長什麼樣**：終端停在登入提示。它的成因與 §3 不同——tar 裡烙的 uid
（`CLAUDE_PTY_SESSION_UID`）與 image 裡 nathan 的真實 uid 對不上，容器讀不到自己的憑證。
`docker exec <容器> ls -ln /run/cpty/` 可以看到那個檔屬於誰。

---

## 5. trivy cache（named volume，ADR 0018）

```bash
docker volume inspect ncr-trivy-cache >/dev/null && echo "volume 在"
docker run --rm --user root --entrypoint sh -v ncr-trivy-cache:/c ncr-dev-container -c 'stat -c "%u %g %a" /c'
```

| 檢查 | 期望 |
|---|---|
| volume 的擁有者 | ＝ image 的 `NCR_UID`（**不是 root/0**） |
| 容器內寫得進去 | `docker run --rm --entrypoint sh -v ncr-trivy-cache:/c ncr-dev-container -c 'touch /c/.p && echo ok'` |
| 兩條路徑共用同一顆 | run script 起一次、網頁開一場，`docker volume ls` 只有 `ncr-trivy-cache` 一顆（**沒有** `claude-pty_…` 前綴的第二顆） |

**壞掉長什麼樣**：restricted profile 的 session **每次卡滿逾時**（trivy 想下載卻寫不進
cache，而牆內又連不到 mirror）。看起來像網路問題。

⚠ 若 volume 的擁有者是 root：**多半是它先被一顆「沒有 `/home/nathan/.cache/trivy` 這個
路徑」的 image 掛過，而且期間有東西以 root 寫進去**。空著的話下次由正確的 image 掛就會
自癒；已經有內容就只能 `docker volume rm ncr-trivy-cache` 重來。

---

## 6. trivy DB 的更新確實發生了

```bash
docker compose exec control python -c "
from server import trivy_db; print(trivy_db.update())"
```

| 檢查 | 期望 |
|---|---|
| 第一次 | `status` 是 `ok`（或 `fresh`，若剛更新過） |
| 時間戳 | `docker compose exec control ls -l /data/trivy-db-updated-at` 存在 |
| 第二次立刻再跑 | `fresh`，而且**不起容器** |
| 建 session 的 log | `docker compose logs control \| grep 'trivy DB'` 有一行 |

**壞掉長什麼樣**：狀態一直是 `error` 或 `missing`。`missing` 代表從沒成功過——
先確認這台連得到 `mirror.gcr.io`。

---

## 7. ssh-agent（只有你要在容器裡做 git over SSH 才需要）

`.env` 設了 `CLAUDE_PTY_SSH_AUTH_SOCK` 之後開一場：

```bash
docker exec <session 容器> ssh-add -l
```

**壞掉長什麼樣**：`Error connecting to agent: Permission denied`。socket 屬於 ①、
connect 的是 ③，uid 對不上就是這個。**完全不像 uid 問題**。

---

## 8. 人的那條路（run script）

在一個要審查的專案目錄裡跑 `run-ncr-dev-container.sh`：

| 檢查 | 期望 |
|---|---|
| 憑證 | 不會抱怨讀不到 `~/.claude/.credentials.json`（它是 0600、屬於 ①） |
| trivy | 印 `🗃️ Trivy DB 已更新（volume ncr-trivy-cache）` |
| 報告落地 | 容器內 `touch ~/ncr/.probe` 成功 |

> 這條路在對齊之前**同樣是壞的**（0600 的憑證檔 ① 擁有、容器裡是 ③ 在讀），
> 只是它從來沒在 Linux 上被跑過。對齊之後兩條路一起好。

---

## 9. 換 uid 的遷移（只有你真的換過才需要）

```bash
chown -R "$(id -u)":"$(id -g)" "${CLAUDE_PTY_SPACE:-$HOME/claude-pty-space}"
docker volume rm ncr-trivy-cache
docker compose exec control rm -f /data/trivy-db-updated-at
```

⚠ **第三行不能省。** 只砍 volume 不清時間戳的話，接下來 6 小時內控制平面會一路回報
`fresh`、連容器都不起——而新 volume 是空的、restricted 在牆內又抓不到，**A2 就這樣無聲
地沒有 DB**。那正是這套機制要治的病。

---

## 回報

跑完把這幾樣貼回來就夠判斷了：

1. §0 的三個數字
2. §2 那道守衛的實際輸出（擋下來的那次）
3. §5 volume 的 `stat` 結果
4. §6 兩次 `update()` 的 status
5. 任何一項「壞掉長什麼樣」真的發生了 —— 連同你當下看到的症狀，那比 log 有用
