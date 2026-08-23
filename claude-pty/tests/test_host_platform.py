"""控制平面問不到 host 是什麼作業系統——那件事只有 host 講得出來。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography python tests/test_host_platform.py

preflight 有一道檢查：控制平面的 uid 與 session 容器內寫入者的 uid 對不上時要喊
（per-user 空間是 0700，對不上就一個字都寫不進去，症狀是每一場都撞 onboarding 對話）。

**但它只在 host 是 Linux 時才成立**——只有那裡的 bind mount 會原樣把 uid 帶過去；
Docker Desktop（macOS／Windows）都做 uid 對映，uid 不同是正常的。

原本的寫法是 `if sys.platform == "linux"`，而那是**錯的問題**：控制平面跑在容器裡
（ADR 0009），容器內 `sys.platform` 永遠是 linux——那道 guard 從來沒有在正式部署裡生效過。
2026-08-08 一次 redeploy 之後才發現：macOS host 每次啟動都收到一句叫他去改 APP_UID 的
假警報，而 session 明明好好的。**一條喊狼來了的訊號，比沒有訊號更糟。**

守的性質：
  🔴 判準吃的是 **host 的** 作業系統（`CLAUDE_PTY_HOST_PLATFORM`），不是容器裡的
  🔴 白名單「是不是 Linux」而不是黑名單「是不是 macOS」——Windows 自己就落在正確那側
  🔴 preflight **真的**照它決定要不要喊（判準對了但沒接上去，等於沒修）
  🔴 **兩條傳遞路徑都在**：redeploy.sh 算得出來、compose 傳得進去。漏任一邊這個修正
     會靜靜失效，而症狀跟修之前一模一樣
  🔴 `sessions.py` 不可以再出現 `sys.platform`（有人改回去就當場紅）
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="test-host-platform-")
# preflight 會在這底下 makedirs——**一定要指進 tmpdir**，否則它會去動使用者真實的
# per-user 空間（run-all.sh 有一道守衛專門在抓這件事）。
config.SPACE_HOST = config.SPACE_SELF = TMP
# 掛載存在性檢查用得到，指一個真的在的目錄，免得多冒出無關的 problem。
config.MOUNTS = {TMP: {"bind": "/x", "mode": "ro"}}

from server import sessions  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def with_platform(value):
    """把 host 判定換掉再問一次。回傳 host_is_linux() 的結果。"""
    old = config.HOST_PLATFORM
    try:
        config.HOST_PLATFORM = value
        return config.host_is_linux()
    finally:
        config.HOST_PLATFORM = old


print("== 判準吃的是 host 的作業系統，不是容器裡的 ==")
# ⚠ 這一條是那個假警報**本身**：容器內 sys.platform 是 linux（正式部署一定是），
#   而 host 是 Darwin。舊的寫法會回 True（於是喊），新的必須回 False。
check("🔴 host=Darwin → 不檢查（即使這支測試跑在 Linux 上）", with_platform("Darwin") is False)
check("host=Linux → 要檢查", with_platform("Linux") is True)
check("大小寫不敏感（uname 給的是 `Linux`，不是 `linux`）", with_platform("linux") is True)
# ⚠ 白名單的價值就在這一條：沒有人寫過 Windows，但它自己就落在對的一側。
#   黑名單（「不是 darwin 就檢查」）會在這裡回 True 而喊一句同樣的假警報。
check(
    "🔴 host=Windows（MINGW64_NT-…）→ 不檢查，而且沒有人為它寫過任何一行",
    with_platform("MINGW64_NT-10.0-19045") is False,
)

print("== preflight 真的照它決定要不要喊 ==")
# ⚠ **保證 uid 一定對不上**，不要靠「跑這支測試的人剛好不是 1001」。真的相等的話下面
#   兩條會一起變成空轉，而且是綠的——那種測試比沒有測試糟。
_old_uid = config.SESSION_UID
config.SESSION_UID = os.getuid() + 1
# ⚠ preflight 會呼叫 `user_proxy.attach_jaeger`，那會**真的去接你正式環境的網路**。
#   整段包在 suppress 裡，所以讓 from_env 直接拋就安靜跳過了（同 test_jaeger_wiring 的手法）。
import docker  # noqa: E402

_old_from_env = docker.from_env
docker.from_env = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("測試不連 docker"))
try:
    # ⚠ 判準改成「這一條在講 uid 對齊嗎」，不再是字面的 APP_UID：訊息現在分三個分支
    #   （image 查得到／image 沒 stamp／image 問不到），只有一條會提到 APP_UID。
    #   三條共用的是那段附註，拿它當錨最穩。
    NEEDLE = "host 判定為"
    _old_plat = config.HOST_PLATFORM
    _old_image_uid = sessions.image_uid

    def _run(platform, image_uid_result):
        config.HOST_PLATFORM = platform
        sessions.image_uid = lambda *a, **k: image_uid_result
        return [p for p in sessions.preflight() if NEEDLE in p]

    try:
        # (a) macOS：不管 image 回什麼都不該喊——bind mount 的 uid 在那邊本來就會被對映
        darwin = _run("Darwin", ("ok", os.getuid() + 1))
        # (b) Linux + image 讀得到真值，而且對不上 → 要喊，且要報得出三個數字
        linux_real = _run("Linux", ("ok", os.getuid() + 1))
        # (c) Linux + image 讀得到真值且三者一致 → 不該喊（避免狼來了）
        _old_sess_uid = config.SESSION_UID
        config.SESSION_UID = os.getuid()
        linux_aligned = _run("Linux", ("ok", os.getuid()))
        config.SESSION_UID = _old_sess_uid
        # (d) Linux + image 沒有 stamp → 退回舊的兩旋鈕比對，並要說「驗不到真值」
        linux_unstamped = _run("Linux", ("unstamped", None))
        # (e) Linux + image 問不到 → 要明講「這一輪沒驗過」，不可以靜靜跳過
        linux_unavail = _run("Linux", ("unavailable", None))
    finally:
        config.HOST_PLATFORM = _old_plat
        sessions.image_uid = _old_image_uid

    check("🔴 host=Darwin：preflight 一句 uid 的話都沒有", darwin == [])
    check("🔴 host=Linux 且 image 真值對不上：有喊", len(linux_real) == 1)
    # ⚠ 這條是這次改版的核心：舊版比的是 APP_UID 與 SESSION_UID **兩個旋鈕**，
    #   兩個一起設成同一個錯的數字就完全靜音。現在要以 image 裡的真值為準。
    check(
        "🔴 喊的那句把三個數字都報出來（image／APP_UID／設定值）",
        bool(linux_real) and all(str(n) in linux_real[0] for n in (os.getuid() + 1, os.getuid(), config.SESSION_UID)),
    )
    check("🔴 三者一致時不喊（不能變成狼來了）", linux_aligned == [])
    check(
        "image 沒 stamp：仍然退回舊比對，而且說得出驗不到真值",
        len(linux_unstamped) >= 1 and any("驗不到" in p for p in linux_unstamped),
    )
    check(
        "🔴 image 問不到：明講『沒有驗過』，不可以當成通過", len(linux_unavail) == 1 and "沒有驗過" in linux_unavail[0]
    )
    # 喊的時候要講得出「我憑什麼這樣判斷」，不然誤報的人無從查起——這正是修之前的處境。
    check(
        "每一個分支都說得出 host 判定的來源",
        all("host 判定為 Linux" in p for p in (linux_real + linux_unstamped + linux_unavail)),
    )
    check(
        "而且告訴人這可能是誤報、以及誰會帶對這個值",
        all("誤報" in p and "redeploy.sh" in p for p in (linux_real + linux_unstamped + linux_unavail)),
    )
finally:
    docker.from_env = _old_from_env
    config.SESSION_UID = _old_uid

print("== 兩條傳遞路徑都要在（漏一邊，這個修正靜靜失效）==")
with open(os.path.join(REPO, "deploy", "redeploy.sh"), encoding="utf-8") as f:
    redeploy = f.read()
with open(os.path.join(REPO, "deploy", "docker-compose.yml"), encoding="utf-8") as f:
    compose = f.read()
check("🔴 redeploy.sh 用 `uname -s` 算出 host 的作業系統", 'CLAUDE_PTY_HOST_PLATFORM="$(uname -s)"' in redeploy)
check(
    "🔴 而且真的 export 出去（算了不送等於沒算）",
    "export" in redeploy and "CLAUDE_PTY_HOST_PLATFORM" in redeploy.split("export", 1)[1],
)
check("🔴 compose 把它注進容器", "CLAUDE_PTY_HOST_PLATFORM: ${CLAUDE_PTY_HOST_PLATFORM:-}" in compose)
# ⚠ 只有 control 需要（preflight 只在它裡面跑）。這兩條同時擋住「順手也加給 reconciler」
#   與「貼到錯的服務底下」——後者不會有任何錯誤訊息，只是那個值永遠到不了要用它的人。
# ⚠ **不可以用 `compose.count("CLAUDE_PTY_HOST_PLATFORM:")`**：那個子字串在同一行裡出現
#   兩次（一次是 key，一次在 `${CLAUDE_PTY_HOST_PLATFORM:-}` 的插值裡），數出來是 2。
#   要數的是「有幾行在定義它」，不是「這幾個字出現幾次」。
_defs = [ln for ln in compose.splitlines() if ln.strip().startswith("CLAUDE_PTY_HOST_PLATFORM:")]
check("只定義一份（reconciler 不跑 preflight，不必給）", len(_defs) == 1)
check(
    "🔴 而且是掛在 control 底下（貼錯服務不會報錯，只是永遠到不了要用它的人）",
    compose.index("CLAUDE_PTY_HOST_PLATFORM:") < compose.index("\n  reconciler:"),
)

print("== 回歸守衛：不可以改回問容器自己 ==")
with open(os.path.join(REPO, "server", "sessions.py"), encoding="utf-8") as f:
    src = f.read()
# ⚠ 註解裡會提到這個字串（在講為什麼不能用它），所以只看**程式碼行**。
code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
check("🔴 sessions.py 的程式碼不再用 sys.platform 判斷 host", not any("sys.platform" in ln for ln in code))
check("preflight 走的是 config.host_is_linux()", "config.host_is_linux()" in src)

__import__("shutil").rmtree(TMP, ignore_errors=True)
print("== COOKIE_SECURE 提醒只在入口真的對外時才喊 ==")
# 以前不分情況都喊，於是本機開發每次啟動都收到一次——每次都喊的提醒，等到真的該喊
# 那次就沒有人在看了。判準是 nginx 綁在哪，而那件事只有 compose 知道。
_old_bind = config.BIND_ADDR
_old_behind = config.BEHIND_PROXY
_old_secure = config.COOKIE_SECURE
try:
    config.BEHIND_PROXY = True
    config.COOKIE_SECURE = False
    for addr, should_warn, why in (
        ("127.0.0.1", False, "只綁 loopback＝機器外面連不到，那個情境不存在"),
        ("::1", False, "IPv6 loopback 同理"),
        ("0.0.0.0", True, "綁全介面＝同網段連得到，要喊"),
        ("192.168.1.10", True, "綁實體位址，要喊"),
        ("", True, "不知道就當成連得到——查不到不等於通過"),
    ):
        config.BIND_ADDR = addr
        hit = any("COOKIE_SECURE=0" in p for p in sessions.preflight())
        check(f"bind={addr or '（未知）'} → {'喊' if should_warn else '不喊'}（{why}）", hit is should_warn)
    # COOKIE_SECURE=1 時任何位址都不該喊
    config.COOKIE_SECURE = True
    config.BIND_ADDR = "0.0.0.0"
    check("設了 COOKIE_SECURE=1 之後就不喊了", not any("COOKIE_SECURE=0" in p for p in sessions.preflight()))
finally:
    config.BIND_ADDR = _old_bind
    config.BEHIND_PROXY = _old_behind
    config.COOKIE_SECURE = _old_secure

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
