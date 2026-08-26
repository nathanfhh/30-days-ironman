"""restricted profile 的防火牆，放行清單有沒有涵蓋 per-user 網路上的代理（ADR 0016）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography python tests/test_restricted_proxy_reach.py

需要 docker daemon ＋ dev-container 的 image（規則要真的套進 iptables 才算數）。

## 為什麼需要這一支

代理是靠「放行所有直連網段」被涵蓋的（`init-firewall.sh` 第 6 節），**沒有任何一條
專屬規則**。而這件事的前提最近整個換過：ADR 0016 把 session 從一張共用網搬到每個
使用者自己那一張，代理所在的網段跟著換。

搬完之後沒有人問過「restricted 還連不連得到代理」。而它壞掉的樣子是：session 開得起來、
容器健康、代理也活著，只有 git 與 API 全部逾時，錯誤訊息指不到 iptables。
`test_profile_mapping` 那幾條靜態斷言看得到腳本裡有那段邏輯，看不到**規則套完之後
封包到底過不過**。

守的性質：
  🔴 套完防火牆之後，同一張網路上的代理**仍然到得了**
  🔴 而外部**確實被擋死**（負向控制）。少了這半邊，「防火牆根本沒套上」也會讓上面那條綠
  🔴 放行清單裡真的出現那張 per-user 網路的網段（而不是碰巧靠別的規則通的）
  🔴 **先套防火牆、之後才接上網路的容器，連不到代理**——放行的是 entrypoint 起跑那一刻
     的快照，之後接的網路不在清單裡，而且**永遠不會好**。`test_create_ordering` 從靜態
     層面釘著「網路要在 start 之前就位」，這裡證明那個危害是真的

⚠ 最後那條**用 IP 打，不用 alias**：它要量的是 iptables 擋不擋得住，不是 DNS 解不解得開。
  用名字打的話，任何一種解析失敗都會讓它為了錯的理由變綠。

⚠ 「快照陷阱」那顆容器接的是一張**臨時的 scratch network**，不是預設 bridge。這不是裝飾：
  `init-firewall.sh` 第 5 節要 `dig` 出 ALLOWED_DOMAINS 的 IP，解不到就 `exit 1`（fail-closed）。
  預設 bridge 上的容器繼承 daemon.json 那份 resolver 清單，而 `dig` 對回 SERVFAIL 的第一台
  **不會 failover**——內網優先的機器上白名單就解不開，防火牆整支掛掉、牆根本沒立起來，
  下面兩條斷言會一起紅而且訊息指不到 DNS（2026-08-26 撞到）。接上任何一張 user-defined
  network 就拿得到 docker 的內嵌 DNS（127.0.0.11），它會問到會回答的那一台。
  scratch 網段本來就會進快照的放行清單，**被測的性質不受影響**：`NET`（代理所在的那張）
  仍然是事後才 connect 的。
"""

import os
import subprocess
import sys
import time
from contextlib import suppress

for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
# 🛡 標成測試建的 ＋ 把命名前綴切開（同 test_user_proxy）。不可以拿掉。
os.environ["CLAUDE_PTY_TEST_MARK"] = "1"
os.environ["CLAUDE_PTY_GITLAB_HOST"] = "gitlab.example.com"
# ⚠ nginx 啟動時就要解析 upstream 的主機名，解不開直接拒絕啟動。這支從不轉發到上游
#   （只打不經上游的 /ping），所以指去哪裡都行。
os.environ["CLAUDE_PTY_GITLAB_PROXY_EXTRA_HOSTS"] = "gitlab.example.com:127.0.0.1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402

from server import config, user_proxy  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FW = os.path.join(REPO, "dev-container", "init-firewall.sh")
PAT = "glpat-RestrictedProbe0123"
UID = 1

_fails = 0


def check(label, ok, detail=""):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n         {detail}" if detail and not ok else ""))


cli = docker.from_env(timeout=90)
NET = user_proxy.network_name(UID)
# 只給「快照陷阱」那顆容器用，為的是拿到 docker 內嵌 DNS（見檔頭）。⚠ 刻意**不套**
# `USER_NETWORK_PREFIX`、也不打 user-network 的標籤——那是 reconciler 認領網路的依據，
# 借用它等於讓這張暫時的網被當成某個使用者的。
SCRATCH_PREFIX = "claude-pty-test-fwdns-scratch"
# 名稱帶 pid：同一台機器上兩份測試同時跑（或前一次中斷留下的網）不會在 create 時撞名；
# cleanup 則是掃整個前綴，殘留的一併收掉（Copilot review，public PR #1）。
SCRATCH = f"{SCRATCH_PREFIX}-{os.getpid()}"


def cleanup():
    with suppress(Exception):
        c = user_proxy.find(cli, UID)
        if c:
            c.remove(force=True)
    stale = []
    with suppress(Exception):
        stale = [n.name for n in cli.networks.list() if n.name.startswith(SCRATCH_PREFIX)]
    for name in (NET, *stale):
        with suppress(Exception):
            net = cli.networks.get(name)
            net.reload()
            for cid in net.attrs.get("Containers") or {}:
                with suppress(Exception):
                    net.disconnect(cid, force=True)
            net.remove()


def run(argv, timeout=300):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def proxy_ip() -> str:
    """代理在使用者那張網路上的 IP。用 IP 是為了把 DNS 排除在量測之外（見檔頭）。"""
    c = user_proxy.find(cli, UID)
    c.reload()
    return c.attrs["NetworkSettings"]["Networks"][NET]["IPAddress"]


try:
    cleanup()
    print("== 佈景：一張 per-user 網路 ＋ 一顆代理 ==")
    net = user_proxy.ensure_network(cli, UID)
    user_proxy.create_or_adopt(cli, UID, PAT)
    subnet = (net.attrs.get("IPAM", {}).get("Config") or [{}])[0].get("Subnet", "")
    ready = False
    for _ in range(40):
        rc, out = run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                NET,
                "--entrypoint",
                "sh",
                "nginx:alpine",
                "-lc",
                f"wget -qO- -T 2 http://{config.PROXY_ALIAS}:{config.PROXY_PORT}/ping",
            ]
        )
        if rc == 0 and "result" in out:
            ready = True
            break
        time.sleep(0.5)
    check("代理起得來（起不來的話下面全部無效）", ready)
    if not ready:
        raise SystemExit(1)
    PIP = proxy_ip()
    print(f"     網段 {subnet}，代理 {PIP}")

    print("\n== 正例＋負例：套完防火牆，代理通、外面不通 ==")
    # ⚠ **無參數地跑** init-firewall.sh：sudoers 把它鎖成 `init-firewall.sh ""`。
    script = (
        "set +e\n"
        f"curl -s -m 4 -o /dev/null -w 'BEFORE=%{{http_code}}\\n' "
        f"http://{config.PROXY_ALIAS}:{config.PROXY_PORT}/ping\n"
        'sudo /usr/local/bin/init-firewall.sh >/tmp/fw.log 2>&1; echo "FWRC=$?"\n'
        "grep -a '直連網段' /tmp/fw.log | sed 's/^/FWNET=/'\n"
        f"curl -s -m 8 -o /dev/null -w 'AFTER_ALIAS=%{{http_code}}\\n' "
        f"http://{config.PROXY_ALIAS}:{config.PROXY_PORT}/ping\n"
        f"curl -s -m 8 -o /dev/null -w 'AFTER_IP=%{{http_code}}\\n' "
        f"http://{PIP}:{config.PROXY_PORT}/ping\n"
        "curl -s -m 8 -o /dev/null -w 'OUTSIDE=%{http_code}\\n' https://example.com/\n"
    )
    rc, out = run(
        [
            "docker",
            "run",
            "--rm",
            "--cap-add=NET_ADMIN",
            "--network",
            NET,
            "-v",
            f"{FW}:/usr/local/bin/init-firewall.sh:ro",
            "--entrypoint",
            "bash",
            config.IMAGE,
            "-lc",
            script,
        ]
    )
    kv = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln and ln[0].isupper())
    check("防火牆套用成功（rc=0）", kv.get("FWRC") == "0", out[-500:])
    check("套之前代理本來就通（不然後面量的不是防火牆）", kv.get("BEFORE") == "200", out[-300:])
    check("🔴 套完之後代理仍然通（用 alias）", kv.get("AFTER_ALIAS") == "200", out[-500:])
    check("🔴 套完之後代理仍然通（用 IP，把 DNS 排除在外）", kv.get("AFTER_IP") == "200", out[-500:])
    check("🔴 而外面被擋死（負向控制：少了它，防火牆沒套上也會全綠）", kv.get("OUTSIDE") == "000", out[-300:])
    check(
        "🔴 放行清單裡出現的就是這張 per-user 網路的網段",
        subnet and kv.get("FWNET", "").endswith(subnet),
        f"網段={subnet} 放行={kv.get('FWNET')}",
    )

    print("\n== 快照陷阱：先套防火牆、之後才接上網路 ==")
    # ⚠ 這一段證明 `init-firewall.sh` 註解裡那句話是真的：放行的是**起跑那一刻**的直連
    #   網段快照。之後才 `network connect` 的網路不在清單裡，而 reconciler 補得了網路、
    #   補不了 iptables——所以那個容器**永遠**連不到代理。`user_proxy` 那句「網路必須在
    #   start 之前就位」就是為了這件事。
    cli.networks.create(SCRATCH, driver="bridge")  # 為了 DNS，不是為了連到代理（見檔頭）
    late = cli.api.create_container(
        config.IMAGE,
        entrypoint=["sleep", "300"],
        labels={config.TEST_LABEL_DEFAULT_KEY: config.TEST_MARK},
        host_config=cli.api.create_host_config(
            cap_add=["NET_ADMIN"], binds={FW: {"bind": "/usr/local/bin/init-firewall.sh", "mode": "ro"}}
        ),
        networking_config=cli.api.create_networking_config({SCRATCH: cli.api.create_endpoint_config()}),
    )
    lid = late["Id"]
    try:
        cli.api.start(lid)  # 只在 scratch 網上，還沒接使用者那張網
        # ⚠ 失敗時要看得到 fw.log：牆掛在哪一節決定了下面那條負向斷言算不算數，而
        #   「rc=1」三個字什麼都指不到。
        rc, out = run(
            [
                "docker",
                "exec",
                lid,
                "bash",
                "-lc",
                'sudo /usr/local/bin/init-firewall.sh >/tmp/fw.log 2>&1; echo "FWRC=$?"; tail -15 /tmp/fw.log',
            ]
        )
        check("防火牆先套起來了", "FWRC=0" in out, out[-600:])
        cli.api.connect_container_to_network(lid, NET)  # 事後才接
        rc, out = run(
            [
                "docker",
                "exec",
                lid,
                "bash",
                "-lc",
                "echo ADDRS=$(ip -o -4 addr show | awk '{print $4}' | tr '\\n' ',');"
                f"curl -s -m 8 -o /dev/null -w 'LATE=%{{http_code}}\\n' "
                f"http://{PIP}:{config.PROXY_PORT}/ping",
            ]
        )
        # ⚠ 這條不是裝飾。介面沒接上的話，下一條會因為「根本沒有路由」而變綠，
        #   而那不是它要量的東西（它要量的是 iptables 擋掉了一條走得通的路）。
        octets = ".".join(PIP.split(".")[:2])  # 例：172.22
        check(
            "介面真的接上了（同網段的位址出現在容器裡）",
            "ADDRS=" in out and octets in out.split("ADDRS=")[1].split("\n")[0],
            out[-300:],
        )
        check("🔴 連不到代理：放行的是起跑那一刻的快照，事後接的網路不在清單裡", "LATE=000" in out, out[-400:])
    finally:
        with suppress(Exception):
            cli.api.remove_container(lid, force=True)

finally:
    cleanup()

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
