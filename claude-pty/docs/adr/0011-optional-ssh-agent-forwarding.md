# ADR 0011：SSH agent 轉發做成 opt-in（預設關）

- 狀態：已接受；已實作

## 背景

人直接跑容器的那條路（run script）會把 host 的 SSH agent 帶進容器（`-v
$SSH_AUTH_SOCK:/ssh/ssh_sock`），讓容器內走 SSH 的 git 操作可用。控制平面（[ADR
0004](0004-flask-control-plane.md) / [0009](0009-containerized-deployment-docker-socket.md)）
預設**不掛**——但這個決定原本只寫在註解裡，於是外顯行為變成：session 裡 `SSH_AUTH_SOCK`
有值（image 的 ENV 還在）卻連不上 agent，看起來像「差一步沒設好」，實際是「這條路根本
沒接」。

## 決策

**做成部署層的 opt-in：`CLAUDE_PTY_SSH_AUTH_SOCK` 填 host 上 agent socket 的路徑才掛，
預設空＝完全不掛。** 落點沿用 `/ssh/ssh_sock`（與 image ENV 一致）。

### 為什麼是 opt-in，不是預設開

爆炸半徑是唯一的理由：**SSH agent 能以你的身分認證任何信任那把 key 的主機**——正式機、
跳板機、任何 git host。它不是「這個系統要提供的能力」，是「這台機器的主人願不願意延伸
出去」的判斷。

而且**現行開關沒有辦法只給部分人**：它是部署層設定，agent 一掛就是每一個能建立 session
的帳號都拿得到。per-user 的狀態隔離（[ADR 0014](0014-per-user-agent-state.md)）不會改變
這條部署層共用能力。所以這個開關的語意必須是部署層的（誰部署誰負責），不是 per-session
profile——後者會讓人誤以為「只有這一場有」，但開得起這一場的人就開得起下一場。

要限縮請在 **host 端**做：另起一個只加了受限 key 的 agent，把那個 socket 指過來。控制
平面沒有能力替你篩掉 agent 裡的任何一把 key。

### 為什麼走 `mounts` 而不是 `volumes`

其他掛載都走 `volumes`（docker API 的 Binds），這一條刻意不同：

- Binds 在**來源不存在**時，dockerd 會在 host 上建一個 `root:root` 的目錄頂替。而這裡的
  來源是 agent socket——路徑打錯、或機器剛重開還沒登入時，那個目錄卡在 socket 該出現的
  位置，下次登入 gnome-keyring / ssh-agent 綁不上去，**壞掉的是 host**。
- `mounts`（`type=bind`）在來源不存在時直接讓建立 session 失敗——那正是我們要的失敗
  方向：**看得見的失敗，而不是安靜地弄壞你的機器。**

非唯讀：連 unix socket 需要寫權限，`ro` 會 EACCES。

> **勘誤（2026-08-22）**：上面這句的推論是錯的，保留原文只為存證。
> `unix(7)` 的「connect 需要 write permission」指的是 **socket inode 的 mode bits**，
> 與掛載是否唯讀無關。
>
> Docker `:ro` 設的是**掛載層**的 `MNT_READONLY`（不是 superblock 唯讀）。kernel 只在走
> `mnt_want_write()` 的寫入路徑（create／unlink／open-for-write／chmod）檢查它並回
> `EROFS`；而 socket 的 connect 走 `unix_find_bsd()` → `path_permission(&path, MAY_WRITE)`，
> 那是 inode 層的檢查，**整條路徑不經過 `mnt_want_write`**。
> （即使 superblock 真的唯讀，`sb_permission()` 也只涵蓋 `S_ISREG`／`S_ISDIR`／`S_ISLNK`，
> socket 一樣豁免。兩層都擋不住 socket IPC。）
>
> 實測（Docker named volume ＋純 Linux container）：同一個 `:ro` 掛載上，寫一般檔案回
> `EROFS`，而 socket 的 connect/send/recv 成功。真正會讓 connect 失敗的是 inode 權限
> （回 `EACCES`），也就是 Docker Desktop 那個 `root:root 0660` 的代理節點。
>
> **結論因此翻案：改為唯讀掛載（`read_only=True` / `:ro`）。**
>
> 原本反對 `:ro` 的唯一理由（會讓 socket 連不上）已證實為假，而它擋得住一個真實的破壞面：
> bind mount 與 host **共用同一個 inode**，原生 Linux 上容器對那顆 socket 下
> `chmod`／`chown` 會改到 host 那一顆，症狀是**使用者其他終端機的 ssh 全部失效**，
> 而且完全指不到容器。`:ro` 讓那條路回 `EROFS`。
> （macOS 的 Docker Desktop 換上自己的代理節點、碰不到 host 那顆，所以那裡本來就沒有這個
> 風險；但同一份腳本兩種 host 都要跑，不構成不加的理由。）
>
> ⚠ `:ro` **不是** agent 的安全邊界：列舉金鑰、簽章、轉送一項都擋不住。它擋的只有
> 「弄壞 host 上那顆 socket」。要限縮 agent 能力，只能在 host 端另起一個受限的 agent。
>
> 反例可重跑：`claude-pty/tests/test_ro_socket_mount.py`。

### 啟動自檢會講話

開啟時每次啟動提醒一次「這把 agent 等於發給每個能建 session 的帳號」。非容器化部署
另外驗路徑存在——容器化時控制平面看不到 host 路徑，硬查會誤報。

⚠ firewall 對 SSH outbound 的收斂只在 restricted profile 存在；`unrestricted` 沒有
firewall，開了轉發的話 agent 打得到任何主機的 22。

## 後果

- 預設行為不變：沒設就完全不掛，既有部署升級上來不會突然多出一個權限。
- **socket 路徑是會變的**（systemd/gnome-keyring 在 `/run/user/<uid>/…`，`ssh-agent -s`
  起的每次都不同）——會頻繁換路徑的環境不適合開這個開關。
- 沒有做 per-session 的開關（做了會給錯誤的安全感）。要真正做到「只有某些人能用 SSH」，
  需要 per-user 的授權政策與 credential routing，屬架構變更。
