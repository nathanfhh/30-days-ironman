# ADR 0022：skill 與 agents 由 provision 開場時鋪進 per-user 空間

- 狀態：已接受；已實作（2026-08-28）

## 背景

ADR 0014 把 CLI 的整份狀態切成 per-user：`~/.claude` 不再是 host 那一份，而是
`${CLAUDE_PTY_SPACE}/user-{id}/claude/`。那次盤點列的是「transcript / settings / skills」。

**但沒有任何程式碼把 skill 放進去。** 到 2026-08-28 為止，`dev-container/` 與
`claude-pty/server/` 全域搜 `skills`／`agents` 找不到任何安裝點。網頁這條路徑能跑
nathan-code-review，唯一的原因是有人在 2026-08-17 手動把整個 skill 目錄複製進
`user-1/claude/skills/`——而 `user-2` 從來沒有過，那個帳號開的 session 是**完全沒有
skill** 的，且沒有任何訊號說它缺了什麼。

TUI 那條路徑不必處理這件事：run script 把 host 的 `~/.claude` 整個掛進容器，
`install.sh` 事先連好的 skill 與 agents 就一起進去了。per-user 空間把那份 host 目錄
換掉之後，這個順帶效果就沒了，而沒有人補上替代品。

### 更難發現的那一半：`agents/`

Claude Code 認的 subagent 定義在 **`~/.claude/agents/`**，不是
`~/.claude/skills/<name>/agents/`。`install.sh` 因此是分兩步做的：skill 連一次、
`agents/*.md` 再連一次。ADR 0014 的盤點清單裡沒有 agents，所以就算後來有人補上
skill 的複製，也很可能只補一半。

只鋪 skill 不鋪 agents **不會報錯**。`SKILL.md` 明寫「那些 agent 沒安裝的話，改用
general-purpose subagent 並帶對應的 prompt」，於是：

- 掃描照跑、報告照出、結論一樣——功能上看不出差別；
- 但五個角色在 transcript 裡的 `agentType` 全部是 `general-purpose`，
  telemetry / cost / session 三份報表都把它們塌成一列，「誰花了多少時間與錢」這個
  問題從此答不出來。

實測（session `b3031f2b`，2026-08-28）：5 個 subagent，`description` 分別是 Trivy scan
／Opengrep scan／Fresh eyes read／Lint scan／Report quality check，`agentType` 五個都是
`general-purpose`。

## 決策

**`provision_user_space` 每次開場時，把 repo 的 `skills/` 整棵樹複製進
`user-{id}/claude/skills/`，並把每個 skill 的 `agents/*.md` 另外複製一份到
`user-{id}/claude/agents/`。**

- **複製，不是掛載。** ADR 0014 的立場是「要嘛整份狀態都在 per-user 目錄、要嘛都不在」，
  複製守得住這條線。掛載的版本要把 `:ro` bind mount 疊進 `claude/`——那是**巢狀
  bind mount**，落點得先存在，而 ADR 0014 與 `run_kwargs.py` 都各自為這個坑留過疤。
- **每次開場重鋪，所以安裝與自我修復是同一件事。** repo 改一行、下一場就吃得到
  （維持這個 repo「沒有暫存區」的哲學）；使用者在容器裡把 skill 改壞了，下一場自己會好。
- **先刪後複製，不就地覆寫。** 覆寫留得下上一版多出來的檔案（skill 改名、reference 刪掉），
  而那些殘檔會被模型照樣讀進去。
- **來源不存在＝什麼都不做，不是錯。** 有人只想用這顆容器跑別的東西。

## 為什麼不是就地 `copytree`

`claude/` 是 session 容器的 rw 掛載。**掛載點本身容器換不掉，它裡面的東西容器換得掉**
——容器可以把 `claude/skills` 刪掉、換成一條指向別處的 symlink，然後控制平面就會以
自己的身分照著那條連結去別的地方建目錄、寫檔案。

所以 `skills` 與 `agents` 這兩層走 `mkdir` + `O_NOFOLLOW` 開 fd 驗過是真目錄，才動樹。
不是目錄就**拒絕開場**，不繞過：繞過等於接受一個已經被動過手腳的空間。

`mkdir` 與 `O_NOFOLLOW` 是一組，缺一不可：`mkdir` 對「這個名字已經是一條 symlink」回的是
EEXIST、**不會跟著連結走**，而 `O_NOFOLLOW`（配 `O_DIRECTORY`）把那條 EEXIST 兜起來，
名字被佔住時拒絕開。所以**不可以改寫成 `makedirs(..., exist_ok=True)`**：它吞掉 EEXIST
之後還會多做一次 `os.path.isdir()`，而那是解析連結的檢查，指向目錄的連結會被判成「已經
有了，沒事」，下一步就照著走出去。另外，判斷「它是不是被換掉了」**只能看 open 有沒有
失敗，不可以拿 errno 分型別**：同一個 `O_NOFOLLOW|O_DIRECTORY` 打在 symlink 上，macOS 回
ENOTDIR、Linux 回 ELOOP。

### ⚠ 已被取代（2026-08-28）：`/proc/self/fd/<fd>` 當前綴

初版是把驗過的 fd 轉成 `/proc/self/fd/<fd>` 字串再交給 `shutil.copytree`。**當初的理由
仍然成立，值得留著**：那是核心維護的 magic symlink，每次 syscall 都解析回那個 fd 指的
inode，所以驗過什麼就寫進什麼；而且**不可以先 `os.path.realpath()`**：realpath 會把它攤平
成一個字串路徑，之後每個 syscall 都重走一次名稱解析，O_NOFOLLOW 驗過的結果當場作廢：
容器只要在驗證後把 `skills` rename 掉再補一條 symlink，寫入就落到別處，而且不必競速；
rmdir 的版本更陰：realpath 回 `.../skills (deleted)`，於是靜靜建出一個叫那個名字的目錄，
skill 永遠沒鋪上且不報錯。這一版初稿就是這樣寫錯的，2026-08-28 的驗收實測抓到。

**它被換掉的原因是 `/proc` 只有 Linux 有。** macOS 上根本沒有那個檔案系統，於是
`OSError: [Errno 30] Read-only file system: '/proc'` 讓四支測試同時紅，其中兩支
（`test_upload`、`test_profile_mapping`）與這個功能完全無關，是被拖紅的。

### 現在的做法：staging + rename，fd 全程不落地成路徑

```python
staging = tempfile.mkdtemp(dir=root, prefix=".skills-")   # root＝<space>/user-N/
shutil.copytree(src, os.path.join(staging, name), symlinks=True)
shutil.rmtree(name, dir_fd=skills_fd, ignore_errors=True)
os.rename(os.path.join(staging, name), name, dst_dir_fd=skills_fd)
```

三個前提，缺一個這段就不安全：

1. **`shutil.rmtree` 從 Python 3.11 起支援 `dir_fd=`**（pyproject 的
   `requires-python = ">=3.11"` 正好在線上），macOS 上 `shutil._use_fd_functions` 也是
   True，內部走的就是 fd 相對的 `_rmtree_safe_fd`，它逐層 `O_NOFOLLOW` 開、再比對
   st_dev/st_ino。**不要自己寫一支 `_rmtree_at`**，自己寫只會少幾道。
2. **`os.rename(..., dst_dir_fd=)` 的目的端最後一個路徑元件不解析 symlink。** 目的地是
   symlink 而來源是目錄 → ENOTDIR；目的地是非空目錄 → ENOTEMPTY。**兩種都是失敗，
   不是誤寫到別處**（2026-08-28 實測，`/etc` 完好）。這就是取代 `/proc/self/fd` 的關鍵。
3. **暫存目錄放在 `root`（`<space>/user-N/`）而不是 `claude/` 底下。** `config.user_mounts()`
   只把 `claude/`、`persistent-data/`、`ncr/` 這三層掛進容器，**root 那一層容器碰不到**，
   所以那裡是可信地面，可以放心用字串路徑組樹。**改 `user_mounts()` 要回來看這裡。**

名字用 `tempfile.mkdtemp` 不用 pid（同 `_write_json_atomic` 的警告：控制平面是 threaded，
同一個使用者同時開兩場是正常的）。`sync_skills_and_agents` 開頭會順手清掉 `root` 底下的
舊 `.skills-*`：硬 kill（OOM、重新部署）會留。

所有 `OSError`（rename 的 ENOTDIR／ENOTEMPTY／EEXIST、mkdtemp 與 copytree 的失敗）與
`RecursionError`（容器可以在目的地造一棵萬層深的樹，把遞迴版本的 rmtree 爆掉；3.13 的
`_rmtree_safe_fd` 已改成堆疊式，3.11／3.12 還是遞迴）都轉成 `SessionError`。不接的話
使用者拿到的是 500 HTML traceback，`app.py` 只有 `SessionError` 的 errorhandler。

**`claude/agents/` 底下的個別檔案要對著 fd 寫，不可以 `shutil.copyfile`。** 那支函式開
目的地是 `open(dst, "wb")`——跟著目的地的 symlink 走：truncate 連結指到的檔，連結留著。
容器在 `claude/agents/` 放一條 `ncr-fresh-eyes.md → ../../../user-2/owner.json`，
下一場 provision 就以控制平面的身分把**別人的** `owner.json` 蓋成 markdown，那個使用者
從此永久開不了場；同一手也指得到 registry 的 SQLite。**沒有競速視窗，一次就成。**
現在的做法是先 `unlink`（連結就是這樣被拆掉的，而不是被寫穿）再
`O_CREAT|O_EXCL|O_NOFOLLOW` 建新檔，被搶著補連結進來時 `O_EXCL` 會擋下。

這與 `persistent-data/uploads` 是同一套規矩（見 `provision.py` 的註解）。換一個目錄名
不會換一個結論。

## 後果

- 新帳號第一次開場就有 skill 與 agents，不必有人記得手動複製。
- 開場多幾十毫秒（複製一棵幾百 KB 的樹）。
- **覆寫的範圍只到「repo 裡有的那些名字」，兩邊都不做 prune。** `claude/skills/<name>`
  是整棵先刪後複製，但 repo 移除掉的 skill 目錄會留在使用者空間裡；`claude/agents/` 只逐檔
  蓋掉同名的 `.md`，使用者自己加的、以及 repo 移除掉的 agent 都會留存。不清空是刻意的
  ——那個目錄使用者放得進自己的東西，開場順手掃掉別人的檔案不是 provision 該有的權力。
  代價是「repo 刪掉一個 agent」不會傳播出去，要人工清。
- 只掃 `skills/` 底下的**第一層目錄**，散檔略過；兩個 skill 有同名 agent 檔的話後鋪的
  蓋前鋪的（與 `install.sh` 的 symlink 行為一致，都是先到先得的相反）。
- **同一個使用者同時開兩場還是會打架，但視窗變窄了。** 兩條路徑各自有自己的暫存目錄
  （`tempfile.mkdtemp`），複製那一段互不干擾；打架只剩最後的 `rmtree` + `rename` 兩步，
  而輸的那條現在拿到的是講得清楚的 `SessionError`（rename 撞上另一條剛放好的樹 →
  ENOTEMPTY），不再是裸的 `FileNotFoundError`（500）。**沒根治**：仍然會在**執行中**的
  容器腳下把 `claude/skills/<name>` 換掉，而配額允許同時 10 場，所以這不是理論情境。
  要根治就是加一把 per-user 的鎖。
- 已經跑過的舊場次救不回 `agentType`，但救得回角色：`subagents/*.meta.json` 的
  `description` 與 `toolUseId` 都在，`opentelemetry/` 的報表據此把角色還原出來
  （`cost-report.py` 的 `role_label()`）。

## 驗證

`tests/test_provision_skills.py`：原本三條都對應一個**不會報錯**的壞掉方式：agents 有沒有
單獨落到 `claude/agents/`、重鋪會不會留下殘檔、`claude/skills` 被換成 symlink 時會不會
照著寫出去。

2026-08-28 換掉 `/proc/self/fd` 之後再加六組，因為**上面那三條在舊做法上也全過，證明不了
新做法賣的東西**：

- `claude/skills/<name>` **自己**被換成 symlink（舊那條蓋的是 `claude/skills` 那一層，
  被 `_open_child_dir` 擋在更前面，根本走不到 `_replace_tree`）→ `SessionError`，
  而且外部目錄不被寫也不被刪。
- 樹**裡面**（`<name>/references`）被塞一條指向外部目錄的 symlink → 外部目錄整個沒動。
- **fd 驗過之後 `claude/skills` 被整個抽換**（用 copytree 當掛鉤注入）→ 寫入仍落在當初
  驗過的那個 inode，新補上的 symlink 指的地方一個字都沒有。這條是唯一測得到
  `dir_fd=`／`dst_dir_fd=` 的形狀：拿掉它們改成字串路徑，只有這一節會紅。
- 目的地是 40 層深樹 → 刪得掉；再把 `RecursionError` 直接注進 rmtree → `SessionError`
  而不是 500（40 層在 3.13 上刪得動，所以那一半證明不了「爆掉時接得住」）。
- 來源含 dangling symlink → 目的地是原樣的斷連結（`copytree(symlinks=True)` 的語義）。
- `.skills-*` 暫存目錄有被清掉，**含中途拋例外那條路**。
