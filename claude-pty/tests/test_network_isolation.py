"""跨使用者的網路隔離（ADR 0016）。**這一支是那個性質唯一的實證。**

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography python tests/test_network_isolation.py

**需要 docker daemon。** 它會建兩張真的網路、起三顆真的容器、開真的 TCP listener。

## 為什麼不能只靠單元測試

`test_profile_mapping` 驗的是「`network` 參數等於這個人的網路名」，`test_create_ordering`
驗的是「那張網在容器建立當下就掛上」。兩者都只看**我們送給 docker 的字串**——如果對
docker 的行為理解錯了（例如以為同名前綴的網路是分開的、或以為 bridge 網路預設不互通），
兩支都會全綠而隔離根本不存在。這一支問的是封包本身。

## 手法：三個假陰性陷阱，每一個都踩過

1. ⚠ **不要用 `ping`。** 這些 image 裡根本沒有那個指令，`ping: not found` 看起來跟
   「不通」一模一樣。
2. ⚠ **一定要自己起一個真的 listener。** 打一個沒有人在聽的 port 一定失敗，那證明不了
   任何事。實測時就是對著 8081 打了半天，而 ttyd 根本不在容器裡跑。
3. ⚠ **先驗正例，再驗反例。** 同一張網連得上是這支測試的**前提**：它不成立就代表探測
   手法壞了（listener 沒起來、nc 參數錯、時序太快），此時反例全綠是假的。所以正例失敗
   要當場講「測試裝置壞了」，不可以讓它安靜地把反例襯托成成功。
4. ⚠ **用 IP 探測，不要用容器名。** 名字解不開只證明 DNS 有 scope，不證明封包到不了；
   兩張網真的被合併、但 DNS 仍分開時，名字探測會給出假的綠燈。

## 隔離的來源

**是網路邊界，不是容器內的防火牆。** 所以這支刻意用最陽春的容器（沒有 iptables、沒有
entrypoint、`unrestricted` 等價）——連那一層都沒有還隔離得了，才證明邊界是真的。
"""
import os
import sys
import tempfile
import time

for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
          "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
# 測試建的東西要標起來，正式 reconciler 才會跳過（也讓網路名落在 test 前綴，
# 不會撞到正式 stack 的 claude-pty-user-N）。
os.environ["CLAUDE_PTY_TEST_MARK"] = "1"

_tmp = tempfile.mkdtemp(prefix="claude-pty-netiso-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402

from server import config, db, user_proxy  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
config.HOST_HOME = _tmp
db.reset_engine()
db.init_db()

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


D = docker.from_env()

# 探測用的 image。挑代理那顆（`nginx:alpine`）是因為**這套東西本來就要有它**——
# per-user 代理就是用它建的，所以不會為了測試多拉一顆 image。busybox 的 nc 就在裡面。
PROBE_IMAGE = config.PROXY_IMAGE
PORT = 9999
BANNER = "claude-pty-isolation-probe"

# 兩個假使用者。用大一點的 id，避免與同機其他測試的暫存 DB 撞名。
UID_A, UID_B = 9001, 9002
made_networks: list = []
made_containers: list = []


def _spawn(name: str, net_name: str):
    """在指定網路上起一顆容器，裡面掛一個會回 banner 的 TCP listener。

    ⚠ `nc -l -k` 才會服務多次連線；沒有 `-k` 的話第一個連線結束 listener 就退場，
      第二次探測會得到「不通」——而那是測試自己造成的假陰性。
    ⚠ 用 `sh -c` 包起來並在最後 `sleep`，容器才不會在 nc 退出時整顆結束。
    """
    c = D.containers.run(
        PROBE_IMAGE, name=name, network=net_name, detach=True,
        # ⚠ 覆蓋 entrypoint：nginx 那顆 image 的預設會去跑 nginx，不是我們要的。
        entrypoint=["/bin/sh", "-c"],
        command=[f"while true; do echo {BANNER} | nc -l -p {PORT}; done"],
        labels={config.SESSION_LABEL_KEY: "netiso-probe",
                config.TEST_LABEL_DEFAULT_KEY: "1"},
        mem_limit="64m", pids_limit=32)
    made_containers.append(c)
    return c


def _ip_on(container, net_name: str) -> str:
    container.reload()
    return container.attrs["NetworkSettings"]["Networks"][net_name]["IPAddress"]


def _can_reach(src, ip: str) -> bool:
    """從 `src` 容器打 `ip:PORT`，收得到 banner 才算通。

    ⚠ **判準是「收到 banner」，不是 exit code。** `nc` 的退出碼在不同 busybox 版本上
      對「連上但沒資料」與「連不上」不一定分得開；banner 是我們自己種進去的，收到它
      就代表雙向都通了。
    ⚠ `-w 2` 的逾時要夠短（不然反例要等很久），也要夠長（同網段一次 TCP 握手綽綽有餘）。
    """
    code, out = src.exec_run(["/bin/sh", "-c", f"nc -w 2 {ip} {PORT} 2>&1 || true"])
    return BANNER in out.decode(errors="replace")


try:
    print("== 建立兩個使用者的網路 ==")
    net_a = user_proxy.ensure_network(D, UID_A)
    net_b = user_proxy.ensure_network(D, UID_B)
    made_networks += [net_a, net_b]
    name_a, name_b = user_proxy.network_name(UID_A), user_proxy.network_name(UID_B)
    check("兩張網路名字不同（前綴 + uid）", name_a != name_b)
    check("都帶著擁有者標記（reconciler 靠它認人）",
          user_proxy.owner_of(net_a) == UID_A and user_proxy.owner_of(net_b) == UID_B)

    print("\n== 起三顆容器：A 的兩顆、B 的一顆 ==")
    a1 = _spawn("claude-pty-netiso-a1", name_a)
    a2 = _spawn("claude-pty-netiso-a2", name_a)
    b1 = _spawn("claude-pty-netiso-b1", name_b)
    # listener 要一點時間才真的 bind 上去。這裡不是在等「網路通」，是在等 nc 起來——
    # 少了它，正例會偶發性地紅，而那種 flaky 最難查。
    time.sleep(1.5)
    ip_a1, ip_a2, ip_b1 = (_ip_on(a1, name_a), _ip_on(a2, name_a), _ip_on(b1, name_b))
    print(f"  a1={ip_a1}  a2={ip_a2}  b1={ip_b1}")

    print("\n== 正例：同一個使用者的 session 之間看得到（這是測試裝置的自我檢查）==")
    # ⚠ 這兩條若失敗，下面的反例全部不算數——不通的原因是探測手法壞了，不是隔離成立。
    ok_same = check("🔴 a1 → a2 收得到 banner", _can_reach(a1, ip_a2))
    ok_same &= check("🔴 a2 → a1 收得到 banner（雙向）", _can_reach(a2, ip_a1))
    if not ok_same:
        print("  ⚠ 正例不通＝**測試裝置壞了**（listener 沒起來／nc 參數不對／等太短），"
              "下面的反例不具意義，不要當成隔離成立。")

    print("\n== 反例：跨使用者連不到，兩個方向都測 ==")
    check("🔴 a1 → b1 連不到（跨使用者）", not _can_reach(a1, ip_b1))
    check("🔴 b1 → a1 連不到（反方向也要）", not _can_reach(b1, ip_a1))
    check("🔴 a2 → b1 也連不到（不是只有第一顆被擋）", not _can_reach(a2, ip_b1))

    print("\n== 隔離來自網路邊界，不是容器內的防火牆 ==")
    # 這三顆容器裡沒有 iptables、沒有 NET_ADMIN、沒有跑 init-firewall.sh——等價於
    # `unrestricted` profile。連那一層都沒有還是連不到，證明擋住封包的是網路本身。
    code, _ = a1.exec_run(["/bin/sh", "-c", "command -v iptables"])
    check("探測容器裡根本沒有 iptables（所以上面擋住的不可能是它）", code != 0)

    print("\n== 每顆容器只掛一張網（多掛一張就是多一條跨界的路）==")
    # ⚠ 這條守的是一個具體的壞法：先建在預設 bridge 再 connect。那樣 alias 有了、
    #   看起來也對，但容器**同時留在 bridge 上**，任何 bridge 上的容器都能用 IP 打到它。
    #   ADR 0016 的表格記著這件事。
    for c, want in ((a1, name_a), (a2, name_a), (b1, name_b)):
        c.reload()
        nets = set(c.attrs["NetworkSettings"]["Networks"])
        check(f"🔴 {c.name} 只在 {want} 上（實際 {sorted(nets)}）", nets == {want})

    print("\n== 回收：jaeger 掛著也要收得掉，但不可以搶在 session 還在的時候收 ==")
    # ⚠ 這一段釘的是 2026-08-07 寫這支測試時**當場踩到**的 bug：`ensure_network` 會把
    #   jaeger 接上每一張使用者網路，而**掛著的容器會讓 `network.remove()` 直接失敗**。
    #   於是每一張使用者網路都變成永遠收不掉——位址池只出不進，而症狀要等到某天
    #   「開不了 session」才出現，那時已經完全看不出源頭。
    #   它是被清理階段的「無殘留」檢查抓到的，不是任何一條斷言主動測出來的，所以補這一段。
    check("網路上還有 session 容器時 → 不是「只剩 jaeger」（這一輪不可以收）",
          not user_proxy.only_jaeger_left(net_a))
    for c in (a1, a2):
        c.remove(force=True)
    check("🔴 容器都收掉之後 → 判定成「只剩 jaeger」（可以收了）",
          user_proxy.only_jaeger_left(net_a))
    # 不拔 jaeger 就直接收，會失敗——這條證明 detach 那一步不是裝飾。
    _refused = False
    try:
        net_a.remove()
    except Exception:      # noqa: BLE001 — 就是要驗它拒絕
        _refused = True
    check("🔴 jaeger 還掛著時 remove 會被拒絕（所以 detach 是必要的一步，不是保險）",
          _refused)
    user_proxy.detach_jaeger(D, name_a)
    net_a.remove()
    made_networks.remove(net_a)
    made_containers = [c for c in made_containers if c not in (a1, a2)]
    check("🔴 拔掉 jaeger 之後收得掉（位址池真的還得回去）",
          not any(x.name == name_a for x in D.networks.list(names=[name_a])))

finally:
    print("\n== 清理 ==")
    for c in made_containers:
        with __import__("contextlib").suppress(Exception):
            c.remove(force=True)
    # ⚠ 網路要在容器**之後**收：還有容器掛著時 docker 會拒絕移除。
    # ⚠ **jaeger 也算「掛著的容器」。** `ensure_network` 會把它接上來，不拔掉的話
    #   `remove()` 一定失敗，網路就留在機器上繼續佔位址池。正式路徑走的是
    #   `reconciler._reap_user_networks` 裡的同一組呼叫（`only_jaeger_left` → `detach_jaeger`）
    #   ——那個 bug 就是被這裡的清理檢查抓出來的，所以這兩行不是測試的權宜之計。
    for n in made_networks:
        with __import__("contextlib").suppress(Exception):
            user_proxy.detach_jaeger(D, n.name)
        with __import__("contextlib").suppress(Exception):
            n.remove()
    left = [n for n in (user_proxy.network_name(UID_A), user_proxy.network_name(UID_B))
            if any(x.name == n for x in D.networks.list(names=[n]))]
    check("測試結束無殘留 network（位址池是全機器共用的，不可以漏收）", not left)
    db.reset_engine()
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
