"""redeploy.sh 的 preflight：session image 不在時要當場擋下來。

    uv run python tests/test_redeploy_preflight.py

守的性質：
  🔴 image 不在 → 非零退出，訊息帶得出可複製的 build 指令
  🔴 **所有平台都查**（這一段以前整個包在 Linux 判斷裡，macOS 完全沒有檢查，
     於是第一次部署看起來成功，直到按下「建立 session」才發現 image 不存在）
  🔴 --build-session-image 是明確 opt-in，不預設（那是好幾 GB、好幾分鐘的 build）
  🟡 真的要略過有一條寫出來的路，而且會講清楚代價
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SH = os.path.join(os.path.dirname(HERE), "deploy", "redeploy.sh")

# ⚠ **這支腳本在 image 檢查之前就會真的做事**（建 ${SPACE} 與 ~/.claude-pty、跑可寫檢查），
#   通過那一關之後還會 `docker compose rm -sf reconciler` 與 `up -d --build`。所以測試
#   一定要把環境整個架空。
#
#   這不是理論上的風險：第一版的這支測試**真的把開發機上正在跑的部署重佈了一次**
#   （control 與 reconciler 被重建）。帶 skip 旗標的那一條通過 image 關卡之後就一路走到
#   compose，而它斷言的訊息在 compose 之前就印了，所以測試「過」，副作用照發生。
#
#   兩件事一起做：HOME 與 CLAUDE_PTY_SPACE 指到 tmpdir；PATH 最前面插一個假的 `docker`，
#   一律非零退出——image inspect 查不到（正是要測的情境），compose 那幾句也全部短路。
TMP = tempfile.mkdtemp(prefix="preflight-test-")
_BIN = os.path.join(TMP, "bin")
os.makedirs(_BIN, exist_ok=True)
_STUB = os.path.join(_BIN, "docker")
with open(_STUB, "w", encoding="utf-8") as _f:
    _f.write('#!/bin/sh\necho "stub docker: $*" >&2\nexit 1\n')
os.chmod(_STUB, os.stat(_STUB).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def run(*args, **env):
    e = dict(
        os.environ,
        CLAUDE_PTY_IMAGE="ncr-preflight-does-not-exist-42",
        HOME=TMP,
        CLAUDE_PTY_SPACE=os.path.join(TMP, "space"),
        PATH=_BIN + os.pathsep + os.environ.get("PATH", ""),
        **env,
    )
    return subprocess.run(
        ["bash", SH, *args],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(SH),
        env=e,
        timeout=120,
    )


print("== 語法 ==")
check("bash -n 過", subprocess.run(["bash", "-n", SH]).returncode == 0)

print("== image 不在時當場擋下來 ==")
r = run()
out = r.stdout + r.stderr
check("非零退出（不是起完控制平面才發現）", r.returncode != 0)
check("講得出是哪個 image 找不到", "ncr-preflight-does-not-exist-42" in out)
check("給了可複製的 build 指令", "dev-container" in out and "build.sh" in out)
check("也講了 opt-in 的做法", "--build-session-image" in out)
check("擋在 docker compose 之前（沒有動到任何容器）", "Recreated" not in out and "Started" not in out)

print("== 沒有 deploy/.env 也要跑得動（第一次部署就是這個樣子）==")
# CI 上沒有 .env（它不進版控），而腳本開了 set -euo pipefail：`sed ... .env` 在檔案不存在
# 時回非零，於是整支會**靜靜 exit 1、一個字都不印**。這是第一次部署的情境，也是最需要
# 看到訊息的那一次。本機有 .env 所以看不出來，是 CI 把它抓出來的。
_ENV = os.path.join(os.path.dirname(SH), ".env")
_env_backup = None
if os.path.exists(_ENV):
    _env_backup = _ENV + ".preflight-test-bak"
    os.rename(_ENV, _env_backup)
try:
    r = run()
    out = r.stdout + r.stderr
    check("沒有 .env 時仍然走得到 image 檢查（不是靜靜退出）", "ncr-preflight-does-not-exist-42" in out)
    check("而且有輸出（靜默失敗最難查）", len(out.strip()) > 0)
finally:
    if _env_backup:
        os.rename(_env_backup, _ENV)

print("== 這一段不是只有 Linux 才跑 ==")
# 以前整段包在 `if [ "$CLAUDE_PTY_HOST_PLATFORM" = "Linux" ]` 裡，所以 macOS 完全沒有檢查。
#
# ⚠ 這一條**用讀原始碼驗，不用注入環境變數驗**。腳本刻意寫死
#   `CLAUDE_PTY_HOST_PLATFORM="$(uname -s)"`（那是事實來源，不可以被塞——
#   test_host_platform.py 有一條專門守這件事），所以注入根本不會生效：
#   兩條「Darwin/Linux 都要擋」的測試會一起走同一條路、一起假通過。
#   真正要守的是**結構**：image 檢查在平台判斷之外。
_src = open(SH, encoding="utf-8").read()
_img_at = _src.index("找不到 session image")
_plat_at = _src.index('if [ "${CLAUDE_PTY_HOST_PLATFORM}" = "Linux" ]')
check("image 檢查排在平台判斷**之前**（所以不分平台都會跑）", _img_at < _plat_at)
check(
    "而且不在那個 if 區塊裡（uid 檢查才是 Linux 限定）",
    "找不到 session image" not in _src[_plat_at:],
)

print("== 略過的那條路要寫出來，而且要講代價 ==")
r = run(CLAUDE_PTY_SKIP_SESSION_IMAGE_CHECK="1", CLAUDE_PTY_HOST_PLATFORM="Darwin")
out = r.stdout + r.stderr
check("帶了略過旗標就不擋在這一關", "這次部署開不了 session" in out)

print("== 不認得的參數 ==")
r = run("--nope")
check("未知參數 → exit 2", r.returncode == 2)
check("錯誤訊息列得出收哪些", "--build-session-image" in (r.stdout + r.stderr))

print("== 測試自己不可以碰到真實環境 ==")
# ⚠ 這一組守的是「測試不會動到跑測試的人」。斷言不能寫成「真實家目錄裡沒有那個目錄」
#   ——它本來就可能存在（開發機上有真的部署），那樣分不出「本來就有」與「我建的」。
#   要驗的是兩件可以直接觀察的事：落點在 tmpdir、而且沒有任何一次呼叫到真的 docker。
check("腳本的落點在 tmpdir 裡", os.path.isdir(os.path.join(TMP, "space")))
_probe = run(CLAUDE_PTY_SKIP_SESSION_IMAGE_CHECK="1", CLAUDE_PTY_HOST_PLATFORM="Darwin")
_probe_out = _probe.stdout + _probe.stderr
check("略過 image 關卡之後打到的是 stub docker，不是真的 daemon", "stub docker:" in _probe_out)
check("因此走不到 compose（stub 一律非零，腳本在那裡就停）", _probe.returncode != 0)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
