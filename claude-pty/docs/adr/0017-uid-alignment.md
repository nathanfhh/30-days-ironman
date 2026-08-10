# ADR 0017：uid 對齊——把一個撿到的數字變成指定得了的

- 狀態：已接受；已實作

## 背景

Linux 的 bind mount **沒有 uid 翻譯層**。核心在 inode 裡存的是一個整數，容器內外同一個
號碼同一個意思；`/etc/passwd` 只把數字翻成名字給人看，權限判斷完全不查名字。

（實測：docker volume 裡以 uid 1999 寫檔，換 uid 1001 的容器來讀——0644 的讀得到、
0600 的 Permission denied、0700 的目錄連 `cd` 都不行。兩個 uid 都不在 `/etc/passwd`
裡，完全不影響判斷。）

而這套系統有**四個號碼落在同一條鏈上**：

| | 是誰 | 由誰決定 |
|---|---|---|
| ① | host 帳號（跑 `redeploy.sh`、擁有 bind source 的人） | 作業系統 |
| ② | 控制平面容器的 `app` | `deploy/Dockerfile` 的 `ARG APP_UID` |
| ③ | session 容器的 `nathan` | **（改版前）沒有人決定** |
| ⑤ | `config.SESSION_UID` | `env CLAUDE_PTY_SESSION_UID` |

### 鏈是怎麼串起來的

每一步都是「上一步建的東西，下一步要進得去」：

```
T0 部署（①）  mkdir -p -m 700 "$SPACE"                     → owner ①
     ↓ T1/T2：② 要在這個根裡面 makedirs                    ⇒ ② = ①
T2 建 session（②）  makedirs(user-N/*, 0700) + 寫種子檔     → owner ②
     ↓ T4：③ 要進去寫 transcript / capture / 審查報告        ⇒ ③ = ②
T3 注入憑證（② 呼叫，內容烙 ⑤）  /run/cpty/ 0700 + 檔 0600
     ↓ T4：③ 要讀它，讀完還要 unlink（父目錄也得是它的）      ⇒ ③ = ⑤
```

**⇒ ① = ② = ③ = ⑤**

兩種耦合要分清楚：

- **寫入耦合**（①→②）跟 mode 無關——在別人的目錄裡建子目錄需要那個目錄的 `w`，而 `w`
  預設只有 owner 有。就算 T0 改成 0755 也一樣建不進去。
- **0700 / 0600** 讓「讀」也一起鎖上。那是刻意的：`ncr/mitm/` 裡是**完整的 API 請求
  本文（prompt 全文）**，0755 在多帳號的 host 上等於發給每一個本機使用者。

### 問題

**③ 不可指定。** 改版前那行是 `useradd -m -s /bin/bash -d /home/nathan nathan`，沒帶
`-u`。它拿到什麼號碼取決於 base image 還剩什麼——`ubuntu:24.04` 自己佔了 1000
（使用者名 `ubuntu`），所以 nathan 撿到 **1001**。

那是一個沒有人決定過、卻被四個地方依賴的數字。而 Ubuntu 第一個人類帳號的慣例正好是
1000，所以**照現況部署到一台正常的 Ubuntu，大概率一開始就是壞的**。

`.env` 因此同時寫下兩句互斥的指示，相隔十行：

> ⚠ **Linux 上這個值要是 1001，不是 `id -u`。**

> **要等於 host 上那些掛進來的檔案的擁有者**…Linux 查法：`id -u`

在一台 `id -u` 是 1000 的機器上，這兩句無法同時成立。

### 而守門的那道檢查比錯了對象

```python
if config.host_is_linux() and os.getuid() != config.SESSION_UID:
```

比的是 ②（`os.getuid()`）與 ⑤——**兩個都是使用者可設定的旋鈕**。把兩個一起轉成 1002
就完全靜音，而真正決定成敗的 ③ 還是 1001。**旋鈕轉得越錯，它越安靜。**

## 決策

### 一、`ARG NCR_UID`：把 ③ 變成指定得了的

```dockerfile
ARG NCR_UID=1001
RUN … && \
    if [ "$(id -u ubuntu 2>/dev/null)" = "${NCR_UID}" ]; then userdel -r ubuntu; fi && \
    useradd -m -s /bin/bash -d /home/nathan -u "${NCR_UID}" nathan
```

**這不是新增限制，是把一個既有卻無法滿足的限制變成可滿足的。** 今天已經「需要」1001
了，差別只在沒有人告訴你，而且你改不動它。

- **預設 1001 ＝ 位元級維持現狀**：不帶這個 arg 重 build，出來的 image 與過去完全一樣。
  要換號碼的人才需要動它。
- **`NCR_UID=1000` 時先移掉 base image 的 `ubuntu`**：不移就是 `useradd: UID 1000 is
  not unique`（exit 4，實測）。那個帳號名下只有 `/home/ubuntu` 與三個 skeleton dotfile
  （實測 4 個檔），`userdel -r` 不留孤兒。
- 值 stamp 成 `ENV NCR_UID` 與 `LABEL ncr.uid`。**兩個都寫**：ENV 給容器內的腳本讀、
  LABEL 給 `docker inspect` 讀，只認一種查法的話，哪天 stamp 方式改了會安靜失效。
  （實測：同一 stage 重宣告 `ARG NCR_UID`（不帶預設）不會洗掉先前的預設值，
  `--build-arg` 也會一致地傳到 `useradd`、ENV 與 LABEL 三處。）

### 二、preflight 改成驗「現實」，不是驗兩個旋鈕

新增 `sessions.image_uid()`，回 `(status, uid)`：

| status | 意思 | preflight 的反應 |
|---|---|---|
| `ok` | 讀到真值 | 三個數字有任何一個不一致就喊，**並把三個都報出來** |
| `unstamped` | image 在，但沒有 stamp（改版前 build 的） | 退回舊的兩旋鈕比對當 fallback，**並明講「驗不到真值，重 build 才有意義」** |
| `unavailable` | image 不在本機／daemon 不通 | 明講「這一輪**沒有驗過**」 |

**`unavailable` 不可以當成通過。** 這一格是整條鏈唯一的現實來源，問不到就要說問不到
——靜靜跳過會讓人以為驗過了，而那正是這份 ADR 在修的病。

`unstamped` 這個分支不是理論上的：**升級之後第一次啟動幾乎必然落在這裡**，因為既有的
image 都是改版前 build 的。

### 三、三個分支共用同一段附註

喊的時候要講得出「我憑什麼這樣判斷」（host 判定的來源、可能是誤報、誰會帶對那個值），
否則收到誤報的人無從查起——那正是 `sys.platform` 那次的處境。少掛在哪一條上，就等於
那條沒說清楚（測試釘著這件事）。

## 沒有採用的方案

| 方案 | 為什麼不 |
|---|---|
| 執行期 `--user <數字>` 覆蓋 image 的使用者 | 數字不在 `/etc/passwd` 裡 → `$HOME` 解析不到、`whoami` 失敗；更致命的是 image 的 sudoers 是給 `nathan` 的，防火牆那條 `sudo` 會失效 |
| entrypoint 以 root 起、`usermod -u` 之後再 drop | 可行且常見，但會把 session 容器從「以 nathan 啟動」改成「以 root 啟動再降權」。那是安全姿態的實質改變，代價大於它解的問題 |
| per-user 空間改用 named volume | 能把 ① ② 移出鏈（volume 由 image 初始化擁有者）。但控制平面要在裡面寫 `.claude.json` 種子與 `owner.json`，而它沒掛那個 volume，得再起一顆容器代寫；且 host 端不再直接看得到／備份得到那些狀態。留作未來選項 |
| 共用 group + setgid + 0770 | 標準的多 uid 解法。**否決理由不是機密性**——那個專用 group 的成員就是 ①②③ 三個同陣營 uid，不會外洩給其他本機使用者。真正的理由是工程面：兩顆 image 都要加 group、設 umask、tar 要帶 gid，每一處 `makedirs`／`chmod` 都要跟著改 |
| userns-remap / rootless daemon | daemon 級的 uid 平移能一次解掉，但它動到那台機器上**所有**容器 |
| POSIX ACL（`setfacl` 給 ②③ rwx） | 每一個 `makedirs` 點都要跟著補 ACL，跨平台語意不一致 |

## 後果

- **Linux 部署要三處對齊**：`APP_UID=$(id -u)`、`--build-arg NCR_UID=$(id -u)`、
  以及既有的 `CLAUDE_PTY_SPACE/user-*` 要 `chown -R`。preflight 會把三個數字報出來。
- **macOS 完全不受影響**：Docker Desktop 對 bind mount 做 uid 對映（實測：同一個 host
  目錄在 uid 1001 的容器裡顯示 owner 1001、在 1000 的容器裡顯示 owner 1000，兩邊都可寫），
  所有檢查都在 `host_is_linux()` 之後。
- **人的路徑（`run-ncr-dev-container.sh`）順帶被修好**：它讓容器內的 ③ 讀 ① 的 0600
  `~/.claude/.credentials.json`，在正常 Ubuntu 上同樣是壞的。對齊之後兩條路徑一起好。
- 鏈上還有三個成員因此自動變好，但症狀都不像 uid 問題，所以記在這裡：**ssh-agent
  socket**（③ connect 需要它的 `w`）、**run script 的憑證檔**、**`semgrep-rules` `:ro`**
  （部署者以 umask 077 clone 的話 ③ 讀不到 → A4 安靜跳過）。
