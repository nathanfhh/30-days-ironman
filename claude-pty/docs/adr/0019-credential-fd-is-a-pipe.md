# ADR 0019：憑證的 fd 必須是 pipe，而且要到最後一刻才建

- 狀態：已接受；已實作

## 背景

登入憑證交進 session 容器的方式（ADR 之前的做法）是：控制平面 `put_archive` 一個 0600
的檔案進去，`entrypoint.sh` 在**最前面**做

```bash
exec 4< "${NCR_TOKEN_FILE}"
export CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR=4
rm -f "${NCR_TOKEN_FILE}"
```

理由是對的：憑證的**值**不進環境，`docker inspect`、`/proc/1/environ`、子行程的環境都
看不到它。這件事到今天仍然成立。

不成立的是接在後面那個推論——「檔案 `rm` 掉了，所以容器裡也找不到它」。

## 量測

全部在 `ncr-dev-container` 裡跑，用 canary 假 token（2026-08-11）。

**一、CLI 讀憑證不會消耗我們那個 fd。** 它的讀法是 `open("/proc/self/fd/4")` 再讀，那會
另開一個 open file description，有自己的 offset：

```
原 fd offset（讀之前）= 0
經 /proc/self/fd 讀到 = 'CANARY_REGULAR_111'
原 fd offset（讀之後）= 0
直接再讀原 fd        = 'CANARY_REGULAR_111'
```

**二、同 uid 的別人拿得到，即使檔案已經 unlink。** 一個 `close_fds=True`、**沒有繼承**
fd 的同 uid 行程，從 `/proc/<pid>/fd/N` 讀到了完整內容；`readlink` 顯示 `(deleted)`。

**三、兩條啟動路徑都中。**

| 路徑 | PID 1 | 結果 |
|---|---|---|
| 錄製（`run_cli` 不 `exec`，bash 要留下來收尾 mitmproxy） | bash | `cat /proc/1/fd/4` 吐出 canary |
| 不錄製（`exec` 掉） | claude | 一樣吐出 canary——CLI 讀完**沒有** close |

第二條的 401（`Invalid bearer token`）同時證明它確實讀了那個 fd。

**四、fd 開太早，無關的行程也拿到了。** 掃錄製容器全部 `/proc/*/fd`，握著 fd 4 的有三個：
PID 1 的 bash、**mitmweb**、CLI。mitmweb 跟憑證毫無關係，它拿到只是因為改動前的 fd 開在
entrypoint 最前面（任何選單與 `start_capture` 之前），之後 fork 的每個行程都繼承。

**五、pipe 沒有這個性質。** 寫入、關掉 write 端、經 `/proc/self/fd/N` 讀一次之後，再讀是
空的。`strace` 數過：regular file 與 pipe 兩種，CLI 都只 `openat("/proc/self/fd/4")` **一次**
——換成 pipe 不影響它拿憑證。

## 決策

1. **fd 4 從 regular file 改成 anonymous pipe。** 檔案內容經 `cat` 灌進 pipe，CLI 讀完
   即 drain。
2. **建立 fd 的時機從 entrypoint 開頭挪到 `run_cli` 啟動 CLI 前的最後一刻。** firewall、
   mitmweb、telemetry 都已經起完，它們的行程從來沒有過這個 fd。
3. **錄製模式下，spawn 完 CLI 之後 PID 1 立刻 `exec 4<&-`。** 那支 bash 會活到收尾結束，
   是所有持有者裡最久的一個。
4. **`rm` 失敗改成清空內容，不是 fail-closed。** 見下。

### 為什麼不是 fail-closed

外部建議是「unlink 失敗就拒絕啟動」。不採納：2026-08-07 實測踩過，unlink 要的是**父目錄**
的寫權限，那是控制平面那側決定的落點；權限不對時 `set -e` 會讓整個容器 exit 1，症狀是
「session 建不起來」，而真正的原因是一行清理指令。用可用性換一個保障，代價不對稱。

改用 truncate：它只要**檔案本身**的寫權限，而那正好是「父目錄不可寫導致 unlink 失敗」
那個情境還具備的。憑證檔留在原地但內容為空，`cat` 得到的是空字串。

⚠ **順序是這個做法的全部。** 清空的動作必須排在 `cat` 之後、而且在同一個 subshell 裡：
process substitution 是非同步的，在外面直接 truncate 會贏過還沒讀完的 `cat`，CLI 拿到空
憑證（實測踩到，第一版就是這樣寫的）。`rm` 不受此限——unlink 不影響已開的 fd，所以它排
在前面、而且是同步的。

## 這買到什麼，沒買到什麼

**買到的**：把「一行 `cat /proc/1/fd/4`、整場都有效、純 `open()`」降級成「必須在 CLI 存活
期間去掃它的記憶體」。

> 這兩件事在 kernel 眼裡是不同等級的存取（`/proc/<pid>/fd` 走 `PTRACE_MODE_READ`、
> `/proc/<pid>/mem` 走 `PTRACE_MODE_ATTACH`），所以在有 YAMA 的硬化 host 上，後者會被擋
> 而前者不會。**這一段是推理，不是量測**——這台的 kernel 連 `yama.ptrace_scope` 都沒有，
> 驗不到。下面「沒買到的」那段才是實測。

**沒買到的**：這**不是**隔離邊界。實測同 uid 讀 `/proc/<pid>/mem` 在這個容器裡是通的
（不需要 gdb，`CapEff` 全 0 也照撈，`yama.ptrace_scope` 這個 knob 根本不存在）。CLI 跑的是
`--dangerously-skip-permissions`，AI 有任意執行權且與 CLI 同 uid——只要 token 進了 CLI 的
記憶體，它就在射程內。

### 被否決：`PR_SET_DUMPABLE=0`

它確實同時擋掉 `/proc/<pid>/mem` 與 `/proc/<pid>/fd`（兩者都 EACCES，實測）。但
**dumpable 在 `execve` 之後會被重設回 1**（實測），而 entrypoint 正是 `exec` 掉自己去跑
CLI。所以從 harness 這一側**根本套不上去**，只有 CLI 自己在行程內呼叫才有意義。不是效益
低的問題，是做不到。

## 還沒驗的

pipe 的前提是「CLI 對那個 fd 只讀一次」。strace 在一次 `-p` 的短命執行裡量到的是一次，
而那一場在第一個 API 呼叫就 401 了——**長時間互動 session 會不會在 token refresh 時重讀，
這裡沒有量到**。假如它會，症狀是「開了一段時間之後要求重新登入」。要用真憑證跑一場才
知道，屬於驗收步驟。真的撞到的話，退路是 `env` 那條（`config` 的憑證交付開關，本來就
留著當逃生口）。

## 影響

- `dev-container/entrypoint.sh`：新增 `prepare_token_fd`，`run_cli` 呼叫它並在錄製路徑
  關閉 PID 1 那份。
- `claude-pty/tests/test_token_fd.py`：新增。守 fd 的**形狀**（pipe 不是 regular file）、
  drain、rm 失敗分支、以及 close 的位置。
- 網頁那條路徑**不需要 rebuild image**：控制平面是 bind-mount repo 版的 `entrypoint.sh`。
  **run script（人的那條）要 rebuild** 才會拿到新的。
