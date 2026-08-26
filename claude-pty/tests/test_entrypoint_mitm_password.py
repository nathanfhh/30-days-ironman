"""`NCR_MITM_WEB_PASSWORD` 的兩個方向（ADR 0021），**需要 docker 與 build 好的 image**。

    uv run --with docker python tests/test_entrypoint_mitm_password.py

網頁那條路要能把 token 當 Bearer 注入代理，前提是「控制平面算出來的那一串」與
「mitmweb 實際收下的那一串」是同一個。這件事橫跨兩個 repo（claude-pty 的 crypto.py
與 dev-container 的 entrypoint.sh），而**兩邊都不會因為對不上而報錯**：mitmweb 照樣
起得來、UI 照樣在那裡，只是每一發請求都被它回 401，而 nginx 把 401 接成 302 導回首頁。
症狀是「按了那顆按鈕就跳回列表」，看起來像授權沒過，完全不像密碼對不上。

所以這裡對著**真的容器**問 mitmweb 自己的 argv，不是比對我們自己寫的字串。

同時守反向那一半：**人自己開容器時逐字不變**：現產亂數、而且那行帶 token 的 URL
照印。少了它，上面那條就只是在描述現況，證不了「這個 env 是控制平面專用的」。
"""

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
IMAGE = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
ENTRYPOINT = os.path.join(REPO, "dev-container", "entrypoint.sh")
ADDON_DIR = os.path.join(REPO, "mitm")
STUB = os.path.join(HERE, "stub_claude.sh")

# ⚠ 從 server 端 import，不要在這裡抄一份字串（同 test_entrypoint_human_path 的理由）。
sys.path.insert(0, os.path.dirname(HERE))
from server.sessions import DRIVER_MARKER  # noqa: E402

# 控制平面會送的那種值：24 字元、base64url 字母表、不以 `$` 開頭。
# ⚠ 不從 crypto.mitm_web_password 取：那支的正確性是 test_secret_key 的事；這裡要驗的是
#   「entrypoint 收到什麼就用什麼」，用一個一眼認得出來的字面值反而看得清楚。
GIVEN = "TESTmitmPASSWORD00000abc"

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def run(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


if run("docker", "version").returncode != 0:
    print("SKIP：docker 不可用")
    sys.exit(0)
if run("docker", "image", "inspect", IMAGE).returncode != 0:
    print(f"SKIP：找不到 image {IMAGE}（先跑 dev-container/build.sh）")
    sys.exit(0)
if not os.path.isfile(os.path.join(ADDON_DIR, "capture_addon.py")):
    print("SKIP：找不到脫敏 addon（entrypoint 會 fail-closed 不錄）")
    sys.exit(0)

os.chmod(STUB, 0o755)


def boot(name: str, env: dict, needle: str, timeout: float = 90.0) -> tuple[str, bool]:
    """起一顆真的容器（capture 開著），等 log 出現 needle。回傳 (log, 有沒有等到)。"""
    run("docker", "rm", "-f", name)
    argv = ["docker", "run", "-d", "-t", "--name", name]
    for k, v in env.items():
        argv += ["-e", f"{k}={v}"]
    argv += [
        "-v",
        f"{ENTRYPOINT}:/usr/local/bin/entrypoint.sh:ro",  # ＝ rebuild image 之後的行為
        "-v",
        f"{ADDON_DIR}:/home/nathan/ncr-mitm:ro",  # 沒有它 start_capture 會 fail-closed
        "-v",
        f"{STUB}:/home/nathan/.local/bin/claude:ro",  # 免 token
        IMAGE,
    ]
    r = run(*argv, timeout=120)
    if r.returncode != 0:
        return r.stderr, False
    deadline = time.time() + timeout
    log = ""
    while time.time() < deadline:
        log = run("docker", "logs", name).stdout.replace("\r", "")
        if needle in log:
            return log, True
        time.sleep(1.0)
    return log, False


# 問 mitmweb 自己：把每個行程的 argv 攤平，撿出 `--set web_password=…` 的那一項。
# （`ps` 在這顆 image 裡不保證有；/proc 一定有。）
_READ_ARGV = r"""for f in /proc/[0-9]*/cmdline; do tr '\0' '\n' < "$f" 2>/dev/null; done \
  | grep '^web_password=' || true"""

NAME_A = "claude-pty-mitmpw-given"
NAME_B = "claude-pty-mitmpw-random"
BASE_ENV = {
    "NCR_NET": "unrestricted",
    "NCR_CAPTURE": "1",
    "NCR_CAPTURE_SCOPE": "all",
    "NCR_MITM_WEB_BIND": "127.0.0.1",
}

try:
    print(f"== image {IMAGE} ==")

    print("\n== 控制平面那條路：env 給什麼，mitmweb 就收什麼 ==")
    log_a, ok_a = boot(NAME_A, {**BASE_ENV, "NCR_MARK": "1", "NCR_MITM_WEB_PASSWORD": GIVEN}, DRIVER_MARKER)
    check("容器起得來且抵達就緒標記", ok_a)
    # 直接問 mitmweb 自己的 argv：比對我們寫的字串等於自己出題自己改答案。
    cmdline = run(
        "docker",
        "exec",
        NAME_A,
        "bash",
        "-c",
        _READ_ARGV,
    ).stdout.strip()
    check(
        "🔴 mitmweb 實際收到的就是 env 給的那一串（讀 /proc 的 argv，不是比對我們自己寫的）",
        cmdline == f"web_password={GIVEN}",
    )
    # NCR_MARK 有值＝控制平面那條路：token 由 nginx 以 Bearer 注入，使用者不必知道，
    # 而 `docker logs` 是控制平面讀得到的，少印一行就少一個外洩點。
    check("🔴 這條路不把帶 token 的 URL 印進 docker logs", "即時畫面" not in log_a)
    check("　└ 而且 log 裡整串 token 一次都沒出現", GIVEN not in log_a)
    check("錄製本身照常開始（不是靠不錄來達成上面兩條）", "● 錄製中 →" in log_a)

    print("\n== 人自己開容器：沒設就逐字回到舊行為（現產、印出來）==")
    log_b, ok_b = boot(NAME_B, BASE_ENV, "● 即時畫面")
    check("印出帶 token 的即時畫面 URL", ok_b)
    m = re.search(r"即時畫面 → http://localhost:\d+/\?token=(\S+)", log_b)
    check("URL 的形狀沒變（host:port/?token=…）", m is not None)
    got = m.group(1) if m else ""
    check("token 是現產的 24 字元亂數", len(got) == 24 and got.isalnum())
    check("🔴 不是控制平面那一串（沒設 env 就不該沾到它）", got != GIVEN)
finally:
    run("docker", "rm", "-f", NAME_A)
    run("docker", "rm", "-f", NAME_B)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
