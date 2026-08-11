"""憑證 fd 的形狀與壽命（ADR 0019）——**需要 docker 與 build 好的 image**。

    uv run --with docker python tests/test_token_fd.py

守的是一件用眼睛看不出來的事：**fd 4 必須是 anonymous pipe，不能是 regular file。**

兩者在 `readlink` 以外的地方看起來一模一樣——CLI 都拿得到憑證、session 都開得起來、
log 也一樣乾淨。差別只在讀完之後：CLI 讀憑證的方式是 `open("/proc/self/fd/4")` 再讀，
那會另開一個 open file description，**不會**消耗我們手上這個 fd。所以 regular file 的
內容在 CLI 讀完後原封不動躺著，容器裡任何同 uid 的行程（CLI 會開 shell，shell 會跑 AI
要求的任何指令）都能 `cat /proc/<pid>/fd/4` 再讀一次，**即使檔案早就 unlink 了**。
pipe 沒有這個性質：被讀一次就 drain。

所以這支測的不是「CLI 拿不拿得到憑證」（那兩種都會過），是「拿完之後還剩不剩」。

⚠ **這支測試在 macOS 上仍然有效，但要用容器自己的檔案系統。** 從 host bind-mount 進去
的檔案，`/proc/<pid>/fd/N` 會讀不到——那是 Docker Desktop virtiofs 的假象，不是防護
生效，實測被騙過一次。所以下面一律把 token 建在容器內。
"""
import os
import subprocess
import sys

IMAGE = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
ENTRYPOINT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "dev-container", "entrypoint.sh")
CANARY = "CANARY_TOKEN_FD_REGRESSION"

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def run(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def in_container(script, user=None):
    """跑一段 bash，掛 repo 版 entrypoint.sh（＝模擬 rebuild 之後的行為）。"""
    argv = ["docker", "run", "--rm",
            "-v", f"{os.path.abspath(ENTRYPOINT)}:/usr/local/bin/entrypoint.sh:ro"]
    if user:
        argv += ["--user", user]
    argv += ["--entrypoint", "bash", IMAGE, "-c", script]
    return run(*argv, timeout=120)


if run("docker", "version").returncode != 0:
    print("SKIP：docker 不可用")
    sys.exit(0)
if run("docker", "image", "inspect", IMAGE).returncode != 0:
    print(f"SKIP：找不到 image {IMAGE}（先 build）")
    sys.exit(0)

# 只抽 prepare_token_fd 出來單獨跑：不必起整個 entrypoint（那會撞選單），而且函式邊界
# 就是這支要守的東西。切到函式結尾為止——切太多會把後面的選單一起抓進來。
#
# 🔴 **`set -euo pipefail` 不可以拿掉。** 真正的 entrypoint 就是這樣跑的，而這支要守的
#    2026-08-07 失敗模式（rm 失敗 → set -e → 整個容器 exit 1）**只在 set -e 下存在**。
#    裸 bash 裡把 `|| rm_failed=1` 改壞也不會死，於是「沒有把整場弄死」那條會恆綠——
#    測試在守一個它自己關掉了的東西。
_EXTRACT = r"""
set -euo pipefail
src=/usr/local/bin/entrypoint.sh
awk '/^prepare_token_fd\(\) \{/,/^\}$/' "$src" > /tmp/fn.sh
. /tmp/fn.sh
"""

print(f"== image {IMAGE} ==")

print("\n== 正常路徑：憑證檔在、父目錄可寫 ==")
r = in_container(_EXTRACT + r"""
mkdir -p ~/cpty && chmod 700 ~/cpty
printf '%s\n' "$CANARY" > ~/cpty/token && chmod 600 ~/cpty/token
NCR_TOKEN_FILE=~/cpty/token
prepare_token_fd
echo "LINK=$(readlink /proc/self/fd/4)"
echo "ENV=${CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR:-unset}"
echo "READ1=$(cat /proc/self/fd/4)"
echo "READ2=$(timeout 1 cat <&4)"
echo "FILE=$(ls ~/cpty/token 2>&1 | tail -1)"
""".replace("$CANARY", CANARY))
out = r.stdout
check("🔴 fd 4 是 anonymous pipe，不是 regular file",
      "LINK=pipe:[" in out)
check("🔴 不是 `(deleted)` 的 regular file（＝舊做法的特徵）",
      "(deleted)" not in out)
check("環境變數指到 4", "ENV=4" in out)
check("CLI 那一側讀得到憑證", f"READ1={CANARY}" in out)
check("🔴 讀過一次之後就沒了（pipe 被 drain）", "READ2=" in out and f"READ2={CANARY}" not in out)
check("憑證檔已經從檔案系統消失", "No such file" in out)

print("\n== rm 失敗（父目錄不可寫）＝ 2026-08-07 那個情境 ==")
# 父目錄 root 所有、不給 nathan 寫；檔案本身仍是 nathan 的 0600。
# unlink 要父目錄的寫權限（沒有），truncate 只要檔案本身的（有）。
# ⚠ 內層腳本寫成檔案再 su，不要塞進 `su -c '…'`：抽函式那行 awk 自己就帶單引號，
#   巢狀之後會被外層的引號吃掉，症狀是整個分支莫名其妙全紅。
r = in_container(r"""
mkdir -p /td && chmod 755 /td
printf '%s\n' "$CANARY" > /td/tok && chown nathan:nathan /td/tok && chmod 600 /td/tok
cat > /tmp/inner.sh <<'INNER'
set -euo pipefail
src=/usr/local/bin/entrypoint.sh
awk '/^prepare_token_fd\(\) \{/,/^\}$/' "$src" > /tmp/fn.sh
. /tmp/fn.sh
NCR_TOKEN_FILE=/td/tok
prepare_token_fd
# 🔴 這個 marker 是「沒被 set -e 帶走」的證據。RC=$? 不行——那量到的是上一個 echo。
echo "SURVIVED=1"
echo "LINK=$(readlink /proc/self/fd/4)"
echo "READ1=$(cat /proc/self/fd/4)"
INNER
chmod 755 /tmp/inner.sh
su nathan -c 'bash /tmp/inner.sh'
echo "SU_RC=$?"
echo "SIZE=$(stat -c %s /td/tok)"
echo "LEFT=[$(cat /td/tok)]"
""".replace("$CANARY", CANARY), user="root")
out = r.stdout
# 🔴 兩個都要：marker 證明函式沒把 set -e 觸發掉，su 的結束碼證明整段沒有非 0 收場。
check("🔴 沒有把整場弄死（rm 失敗不等於 exit 1）",
      "SURVIVED=1" in out and "SU_RC=0" in out)
check("CLI 仍然拿得到憑證", f"READ1={CANARY}" in out)
check("fd 4 一樣是 pipe", "LINK=pipe:[" in out)
# 🔴 這一條是這個分支的重點：檔案刪不掉，但**內容不准留著**。
#    順序錯了就會兩頭空——在 subshell 外面 truncate 會贏過還沒讀完的 cat，
#    CLI 拿到空的憑證（實測踩過）。所以上面那條與這一條要一起看。
check("🔴 檔案還在，但內容已經清空", "SIZE=0" in out and "LEFT=[]" in out)
check("🔴 而且有吵（不是無聲降級）", "憑證檔刪不掉" in (out + r.stderr))

print("\n== 沒有憑證：什麼都不該發生 ==")
r = in_container(_EXTRACT + r"""
prepare_token_fd
# 🔴 同上：marker 而不是 RC=$?。set -e 下函式回非 0 → shell 當場死 → 這行印不出來。
echo "SURVIVED=1"
echo "ENV=${CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR:-unset}"
echo "LINK=$(readlink /proc/self/fd/4 2>/dev/null || echo none)"
""")
out = r.stdout
check("環境變數沒被設起來", "ENV=unset" in out)
check("fd 4 沒有被開", "LINK=none" in out)
check("🔴 沒有憑證不是錯誤（不能讓沒貼 token 的人開不了場）",
      "SURVIVED=1" in out and r.returncode == 0)

print("\n== run_cli：錄製模式下 PID 1 不准留著憑證 fd ==")
# 錄製那條路不 exec（bash 要留下來收尾 mitmproxy），所以 PID 1 會活整場。
# 它如果還握著 fd 4，`/proc/1/fd/4` 就是一條讀得到憑證的路——比 CLI 自己那份更久。
r = in_container(r"""
src=/usr/local/bin/entrypoint.sh
grep -q 'exec 4<&-' "$src" && echo HAS_CLOSE
awk '/^run_cli\(\) \{/,/^\}$/' "$src" > /tmp/rc.sh
# close 必須排在背景啟動 CLI **之後**（早了 CLI 就繼承不到）
awk '/"\$@" <&0 &/{seen=1} /exec 4<&-/{if(seen)print "ORDER_OK"}' /tmp/rc.sh
""")
out = r.stdout
check("🔴 run_cli 有把 PID 1 那份 fd 關掉", "HAS_CLOSE" in out)
check("🔴 而且排在背景啟動 CLI 之後（早了 CLI 會拿不到憑證）", "ORDER_OK" in out)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
