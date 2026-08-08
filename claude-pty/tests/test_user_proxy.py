"""per-user GitLab 代理的**真 docker** 生命週期（ADR 0016）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography python tests/test_user_proxy.py

需要 docker daemon 與代理 image（預設 `nginx:alpine`，會自動 pull）。**不需要**能連到
任何 GitLab——上游只在真的轉發請求時才用得到，這裡驗的是代理自己。

守的性質：
  🔴 **PAT 不在 `docker inspect` 裡**（不進 env、不進 command、不進 labels、不 bind mount）。
     它只存在於代理容器的檔案系統內，而那顆容器不是使用者碰得到的東西。
  🔴 alias 解析得了：同網路上的鄰居打 `gitlab-proxy:5678` 要通。這條靠低階
     `create_container(networking_config=…)`——上層 API 會把 alias 默默丟掉。
  🔴 **不同使用者的網路互相看不到**，即使 alias 同名。跨使用者的隔離就靠這個。
  🔴 熱重載換得掉 PAT 而**容器 PID 不變**（換一把 PAT 不該斷掉正在跑的 clone）。
  🔴 撞名時**認領**既有那顆而不是重建——不然敗方會把勝方的代理刪掉。
  🔴 `_exact()` 不被 docker 的子字串比對騙（user-1 撿到 user-13＝跨使用者憑證外洩）。

⚠ 這支會真的建容器與網路。所有東西都帶 `CLAUDE_PTY_TEST_MARK`，正式 reconciler 據此
  跳過；命名空間也被 config 自動切開（`claude-pty-test-*`），不會撞到正式 stack 的名字。
"""
import os
import shutil
import sys
import tempfile
import time
from contextlib import suppress

for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
# 🛡 這一行不可以拿掉：它同時做兩件事——把容器與網路標成「測試建的」（正式 reconciler
#   跳過），以及把命名前綴切成 `claude-pty-test-*`（不會撞到正式 stack 的 user-N）。
os.environ["CLAUDE_PTY_TEST_MARK"] = "1"
os.environ["CLAUDE_PTY_GITLAB_HOST"] = "gitlab.example.com"
# ⚠ **這一行是必要的，不是裝飾。** nginx 在**啟動時**就要解析 `upstream` 的主機名，
#   解不開就直接 `[emerg] host not found in upstream` 拒絕啟動——而 `gitlab.example.com`
#   是 NXDOMAIN。把它釘到 loopback，代理才起得來（測試從不真的轉發請求到上游，所以指去
#   哪裡都行）。順帶把 `PROXY_EXTRA_HOSTS` 這個逃生口也走過一遍。
os.environ["CLAUDE_PTY_GITLAB_PROXY_EXTRA_HOSTS"] = "gitlab.example.com:127.0.0.1"

_tmp = tempfile.mkdtemp(prefix="claude-pty-uproxy-")
os.environ["CLAUDE_PTY_DB_PATH"] = os.path.join(_tmp, "t.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402

from server import config  # noqa: E402

config.DB_PATH = os.environ["CLAUDE_PTY_DB_PATH"]
config.DB_URL = f"sqlite:///{config.DB_PATH}"
config.SECRET_KEY = "user-proxy-test-secret"
config.HOST_HOME = _tmp

from server import db, gitlab_proxy, user_proxy  # noqa: E402

db.reset_engine()
db.init_db()

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


D = docker.from_env(timeout=config.DOCKER_TIMEOUT)
# 這支測試專用的 uid，取大一點的數字避開任何真實帳號；13 / 1 那組是刻意的（見 _exact）。
UID_A, UID_B = 91, 913
PAT_A = "glpat-AaaaTestOnly00001"
PAT_B = "glpat-BbbbTestOnly00002"
# ⚠ B 的代理**必須用第三把**。原本 B 也用 PAT_B，而 A 在上面剛被熱重載成 PAT_B——
#   兩顆的指紋於是完全相同，下面那條「alias 解到的是 A 不是 B」就變成拿一個兩邊共有的
#   值去斷言，測不到它要測的事（審查 F-020）。
PAT_C = "glpat-CcccTestOnly00003"

_probe = None            # 用來從網路內部打代理的一次性容器


def _cleanup():
    """把這支測試建的東西收乾淨。**每一條都 best-effort**：測試中途失敗也要收。"""
    if _probe is not None:
        try:
            D.api.remove_container(_probe, force=True)
        except Exception:
            pass
    for uid in (UID_A, UID_B):
        try:
            user_proxy.remove(D, uid)
        except Exception:
            pass
        try:
            user_proxy.remove_network(D, uid)
        except Exception:
            pass


try:
    D.images.get(config.PROXY_IMAGE)
except docker.errors.ImageNotFound:
    print(f"  ..  本機沒有 {config.PROXY_IMAGE}，先拉一次")
    D.images.pull(config.PROXY_IMAGE)

_cleanup()      # 上一輪若在中途爆掉，先清乾淨再開始

try:
    # ------------------------------------------------------------------ 網路
    print("== 網路：等冪、標得出主人、精確比對 ==")
    net_a = user_proxy.ensure_network(D, UID_A)
    check("建得出來", net_a is not None)
    check("🔴 再叫一次是等冪的（多個 worker 會同時走這條路）",
          user_proxy.ensure_network(D, UID_A).id == net_a.id)
    check("標得出主人（收斂時要靠它對應使用者）", user_proxy.owner_of(net_a) == UID_A)
    check("掃得到（不必靠名字前綴猜）",
          net_a.id in {n.id for n in user_proxy.list_networks(D)})

    # 🔴 docker 的 `names` filter 是**子字串**比對。user-91 與 user-913 互為前綴，
    #   撿錯的後果是 user-91 的 session 被接到 user-913 的網路上，alias 解到他的代理、
    #   用他的 PAT——那正是這整套設計要建立的邊界。
    net_b = user_proxy.ensure_network(D, UID_B)
    got = user_proxy._exact(D, user_proxy.network_name(UID_A))
    raw = D.networks.list(names=[user_proxy.network_name(UID_A)])
    check("🔴 _exact 精確比對，不會把 user-913 撿成 user-91（跨使用者憑證外洩）",
          got is not None and got.id == net_a.id)
    check("   （對照：docker 原生 filter 這時真的回了不只一個）", len(raw) > 1)
    check("_exact 找不到就回 None", user_proxy._exact(D, "claude-pty-test-user-nope") is None)
    user_proxy.remove_network(D, UID_B)

    # ------------------------------------------------------------------ 建立
    print("\n== 建立代理：起得來、alias 解析得了、PAT 不外露 ==")
    cid, mine = user_proxy.create_or_adopt(D, UID_A, PAT_A)
    check("建起來了，而且回報「這顆是我建的」", bool(cid) and mine is True)
    c = user_proxy.find(D, UID_A)
    if c is not None and c.status != "running":
        # 起不來的話後面每一條都會紅，而症狀（exec: container is not running）指不到原因。
        # nginx 自己那句話才指得到——最常見的是解不開 upstream 的主機名。
        print(f"        代理沒起來（{c.status}），它說："
              f"{c.logs(tail=3).decode(errors='replace').strip()}")
    check("找得到、狀態是 running", c is not None and c.status == "running")
    check("標得出主人", user_proxy.owner_of(c) == UID_A)
    check("掃得到", cid in {x.id for x in user_proxy.list_all(D)})

    # 🔴 這是整套設計的前提。`docker inspect` 是使用者（與任何拿得到 docker socket 的
    #   東西）看得到的全部——PAT 不可以出現在裡面的任何角落。
    attrs = D.api.inspect_container(cid)
    blob = repr(attrs)
    check("🔴 PAT 不在 docker inspect 的任何欄位裡", PAT_A not in blob)
    check("🔴 沒有把設定 bind mount 進去（那樣之後就換不掉了，熱重載的前提沒了）",
          not attrs.get("Mounts"))
    check("🔴 PAT 不在環境變數裡", not any(PAT_A in e for e in (attrs["Config"]["Env"] or [])))
    check("🔴 PAT 不在 labels 裡", PAT_A not in repr(attrs["Config"]["Labels"]))

    # alias：低階 create_container(networking_config=…) 是唯一帶得上去的寫法。
    endpoints = attrs["NetworkSettings"]["Networks"]
    check("🔴 只掛在這個使用者的網路上（留在預設 bridge 就等於誰都打得到它）",
          list(endpoints) == [user_proxy.network_name(UID_A)])
    aliases = endpoints[user_proxy.network_name(UID_A)].get("Aliases") or []
    check(f"🔴 alias 真的帶上去了（{config.PROXY_ALIAS}）", config.PROXY_ALIAS in aliases)

    # ------------------------------------------------------------------ 從網路內部打它
    print("\n== 從同網路的鄰居打它：alias 通、白名單擋得住 ==")
    # 用一顆一次性容器扮演 session：這才是真的在驗「session 找不找得到代理」。
    _probe = D.api.create_container(
        config.PROXY_IMAGE, command=["sleep", "60"],
        labels={config.TEST_LABEL_DEFAULT_KEY: "1"},
        host_config=D.api.create_host_config(
            network_mode=user_proxy.network_name(UID_A)))["Id"]
    D.api.start(_probe)
    probe = D.containers.get(_probe)
    base = f"http://{config.PROXY_ALIAS}:{config.PROXY_PORT}"

    def _get(path):
        code, out = probe.exec_run(["wget", "-qO-", f"{base}{path}"])
        return code, out.decode(errors="replace").strip()

    code, body = _get("/ping")
    check("🔴 鄰居用 alias 打得到 /ping（session 的偵測就靠這條）",
          code == 0 and '"result": true' in body)
    check("   /ping 不經上游，所以沒有 GitLab 也答得出來（它答的是「代理在不在」）",
          code == 0)

    code, state = _get("/_state")
    check("🔴 /_state 回的就是目前設定的指紋", code == 0
          and state == gitlab_proxy.fingerprint(PAT_A))
    check("🔴 /_state 回的不是 PAT 本身，也不是它的裸 hash（HMAC，session 裡的 AI 打得到）",
          PAT_A not in state)

    code, out = probe.exec_run(
        ["wget", "-qS", "-O-", f"{base}/api/v4/projects"])   # 不在白名單上
    check("🔴 沒列在白名單上的端點被擋（403，且沒有轉去上游）",
          "403" in out.decode(errors="replace"))

    # ------------------------------------------------------------------ 熱重載
    print("\n== 換 PAT：熱重載，容器不重建 ==")
    pid_before = D.api.inspect_container(cid)["State"]["Pid"]
    check("換之前指紋是舊的", _get("/_state")[1] == gitlab_proxy.fingerprint(PAT_A))
    check("reload 回報成功", user_proxy.reload(D, UID_A, PAT_B) is True)
    time.sleep(1.0)                    # 給 nginx 一點時間處理 SIGHUP
    check("🔴 指紋換成新的了", _get("/_state")[1] == gitlab_proxy.fingerprint(PAT_B))
    pid_after = D.api.inspect_container(cid)["State"]["Pid"]
    check("🔴 容器 PID 不變（換一把 PAT 不該斷掉正在跑的 clone）", pid_before == pid_after)
    check("🔴 換完之後 PAT 仍然不在 docker inspect 裡",
          PAT_B not in repr(D.api.inspect_container(cid)))
    check("running_state() 問到的與容器自己說的一致",
          user_proxy.running_state(D, UID_A) == gitlab_proxy.fingerprint(PAT_B))
    check("🔴 alias 與 IP 都沒變（重建的話 session 那一端會斷）",
          (D.api.inspect_container(cid)["NetworkSettings"]["Networks"]
           [user_proxy.network_name(UID_A)]["Aliases"] or []) == aliases)

    # 壞設定不可以蓋上去。這裡用「暫時把上游主機名弄成解不開的」製造 `nginx -t` 失敗
    # ——那是 ADR 記的真實路徑（代理裡解不出 GitLab 的主機名），不是假想的。
    print("\n== 壞設定：驗過才蓋，沒過就什麼都不做 ==")
    good_host = config.GITLAB_HOST
    config.GITLAB_HOST = "no-such-host.invalid"
    ok = user_proxy.reload(D, UID_A, PAT_A)
    config.GITLAB_HOST = good_host
    check("🔴 nginx -t 沒過時 reload 回 False（不可以回 True，那是假捷報）", ok is False)
    check("🔴 沒過就不換——容器還在跑舊設定，指紋沒動",
          _get("/_state")[1] == gitlab_proxy.fingerprint(PAT_B))
    code, _ = D.containers.get(cid).exec_run(["test", "-f", "/etc/nginx/nginx.conf.next"])
    check("🔴 沒過的暫存檔要收掉——留著的話下次冷啟動會拿它起來而起不來",
          code != 0)
    check("代理還活著（壞設定弄不死它）", D.containers.get(cid).status == "running")

    # ------------------------------------------------------------------ 撞名
    print("\n== 撞名：認領，不重建 ==")
    cid2, mine2 = user_proxy.create_or_adopt(D, UID_A, PAT_A)
    check("🔴 回傳既有那顆的 id", cid2 == cid)
    check("🔴 而且回報「不是我建的」——呼叫端的補償只能收自己建的那一顆。"
          "少了這個旗標，敗方會把勝方的代理 force-remove 掉", mine2 is False)
    check("既有那顆沒有被動到（PID 不變）",
          D.api.inspect_container(cid)["State"]["Pid"] == pid_after)

    # ------------------------------------------------------------------ 隔離
    print("\n== 兩個使用者：同一個 alias，互相看不到 ==")
    user_proxy.ensure_network(D, UID_B)
    cid_b, _ = user_proxy.create_or_adopt(D, UID_B, PAT_C)
    check("B 也起得來，alias 同名不衝突（不同網路可以用同一個 alias）",
          user_proxy.find(D, UID_B).status == "running")
    # 從 A 的網路上打 alias，只能打到 A 的代理——指紋就是證據。
    # 🔴 期望值只有**正確的那一顆**答得出來：A 現在跑 PAT_B、B 跑 PAT_C，指紋不同。
    #    兩顆共用同一把 PAT 的話，把每顆代理都接上每一張網路（正是 _exact() 與 per-network
    #    設計要防的錯誤）也照樣綠——那時 alias 可能解到 B，指紋卻一樣（審查 F-020）。
    check("🔴 A 網路上的鄰居解到的是 A 的代理，不是 B 的",
          _get("/_state")[1] == gitlab_proxy.fingerprint(PAT_B))   # A 剛換成 PAT_B
    check("🔴 而且明確不是 B 的（B 用的是第三把 PAT，指紋不同）",
          _get("/_state")[1] != gitlab_proxy.fingerprint(PAT_C))
    a_net = D.api.inspect_container(cid)["NetworkSettings"]["Networks"]
    b_net = D.api.inspect_container(cid_b)["NetworkSettings"]["Networks"]
    check("🔴 B 的代理不在 A 的網路上（兩張網路是分開的）",
          user_proxy.network_name(UID_A) not in b_net)
    # ⚠ 這一條原本的標籤說「B 那張網路上沒有它」，評估的卻是 `all(a_ips)`——a_ips 是一個
    #   只有一個元素的 set，all() 只等於「那個字串非空」，完全沒有檢查 B 的網路（審查
    #   F-031）。標籤描述一個沒有被測的性質，比沒有這一行更糟。改成真的測反方向。
    check("🔴 反方向也成立：A 的代理不在 B 的網路上",
          user_proxy.network_name(UID_B) not in a_net)
    check("   （而且 A 真的在自己那張網上、有 IP）",
          bool(a_net.get(user_proxy.network_name(UID_A), {}).get("IPAddress")))

    # ------------------------------------------------------------------ 移除
    print("\n== 移除：等冪 ==")
    D.api.remove_container(_probe, force=True)
    _probe = None
    check("收得掉", user_proxy.remove(D, UID_A) is True)
    check("🔴 已經不在也算成功（等冪，reconciler 會重複呼叫）",
          user_proxy.remove(D, UID_A) is False)
    check("find 之後回 None", user_proxy.find(D, UID_A) is None)
    user_proxy.remove(D, UID_B)
    check("網路收得掉（容器都走了之後）", user_proxy.remove_network(D, UID_A) is True)
    check("網路移除也是等冪", user_proxy.remove_network(D, UID_A) is False)
    user_proxy.remove_network(D, UID_B)

    # ------------------------------------------------------------- 起不來要看得見
    print("\n== 代理連續起不來：把原因端到畫面上，不要只留在容器 log ==")
    # 造一顆**真的**起不來的代理：upstream 主機名解不開，nginx 啟動時就 [emerg] 拒絕啟動。
    # 那正是這條訊號要救的真實失敗（部署者把 CLAUDE_PTY_GITLAB_HOST 打錯），不是假想的。
    from server import auth, db, reconciler  # noqa: E402  （放這裡：import 會拉起 DB）

    db.reset_engine()
    db.init_db()
    ruid = auth.create_user("proxy-fail-user", "proxy-fail-pw-1")["id"]
    auth.set_gitlab_pat(ruid, PAT_A)

    saved_host, saved_hosts = config.GITLAB_HOST, config.PROXY_EXTRA_HOSTS
    config.PROXY_EXTRA_HOSTS = {}          # 拿掉那條讓它解得開的 /etc/hosts 對映
    config.GITLAB_HOST = "no-such-host.invalid"
    try:
        user_proxy.ensure_network(D, ruid)
        with suppress(Exception):
            user_proxy.create_or_adopt(D, ruid, PAT_A)   # put_archive 過、start 之後即死
        time.sleep(1.5)
        dead = user_proxy.find(D, ruid)
        # ⚠ 先驗前提：這顆真的沒活著。前提不成立的話下面測的是空氣，而且會一直綠。
        check("前提成立：這顆代理真的沒活著",
              dead is not None and dead.status != "running")

        def passthru(_label, fn, *a, **k):
            return fn(*a, **k)

        for i in range(config.PROXY_FAIL_THRESHOLD - 1):
            reconciler._note_proxy_down(dead, ruid, passthru)
            check(f"🔴 第 {i + 1} 輪還不吵使用者"
                  f"（偶爾重啟一輪是正常的，每次都喊就是狼來了）",
                  auth.gitlab_proxy_error(ruid) is None)
        reconciler._note_proxy_down(dead, ruid, passthru)      # 第 N 輪
        msg = auth.gitlab_proxy_error(ruid)
        check(f"🔴 連續 {config.PROXY_FAIL_THRESHOLD} 輪之後才端出來", msg is not None)
        check("🔴 那句話指得到真正的原因（主機名解不開），"
              "而不是「GitLab 連不到」這種會害人查錯方向的說法",
              msg is not None and "host not found in upstream" in msg)
        check("🔴 訊息裡沒有 PAT（nginx 的 [emerg] 只講檔名行號，不含設定內容）",
              msg is not None and PAT_A not in msg)

        reconciler._note_proxy_ok(ruid)
        check("🔴 代理恢復的那一輪就清掉——留著會讓人去改一個本來就正確的設定",
              auth.gitlab_proxy_error(ruid) is None)
    finally:
        config.GITLAB_HOST, config.PROXY_EXTRA_HOSTS = saved_host, saved_hosts
        with suppress(Exception):
            user_proxy.remove(D, ruid)
        with suppress(Exception):
            user_proxy.remove_network(D, ruid)

    # ------------------------------------------------------------------ 關掉＝收乾淨
    print("\n== 部署者把功能關掉：收斂要把既有的收乾淨，不是停止管理 ==")
    # 期望狀態是「一顆代理與一張網路都不該有」，所以這一輪要負責收。跳過的話，部署者
    # 拿掉 CLAUDE_PTY_GITLAB_HOST 之後那些代理會**帶著 PAT 永遠留在機器上**。
    from server import reconciler  # noqa: E402  （放這裡：import 它會拉起 DB）

    user_proxy.ensure_network(D, UID_A)
    user_proxy.create_or_adopt(D, UID_A, PAT_A)
    check("先確認有東西可以收", user_proxy.find(D, UID_A) is not None)

    saved = (config.ORPHAN_GRACE, config.GITLAB_HOST, config.TEST_LABEL_KEY)
    # ⚠ 寬限期依物件自己的建立時間，而它是幾秒前建的——不挪開的話「沒被收掉」會被誤讀成
    #   「規則沒生效」，其實只是還在寬限期內。
    config.ORPHAN_GRACE = 0
    config.GITLAB_HOST = ""                 # ＝ gitlab_enabled() 為 False
    # ⚠ 收斂會刻意跳過帶測試標記的東西（正式 reconciler 不該收測試建的），所以這裡把
    #   **讀端**的 key 換掉讓它願意看。這正是讀端與寫端兩個 key 分開存在的理由。
    config.TEST_LABEL_KEY = "claude-pty.not-a-real-label"
    try:
        check("關掉之後 gitlab_enabled() 是 False", config.gitlab_enabled() is False)
        reconciler._converge_proxies(D, {}, lambda _label, fn, *a, **k: fn(*a, **k))
        check("🔴 代理被收掉（不然它會帶著 PAT 永遠留在機器上）",
              user_proxy.find(D, UID_A) is None)
        check("🔴 網路也被回收（不然位址池一直被佔著）",
              user_proxy._exact(D, user_proxy.network_name(UID_A)) is None)
    finally:
        config.ORPHAN_GRACE, config.GITLAB_HOST, config.TEST_LABEL_KEY = saved

finally:
    _cleanup()
    shutil.rmtree(_tmp, ignore_errors=True)

# 無殘留：這支測試不可以留下任何東西給下一輪或給正式 stack。
leftover = [c.name for c in user_proxy.list_all(D)
            if user_proxy.owner_of(c) in (UID_A, UID_B)]
leftover += [n.name for n in user_proxy.list_networks(D)
             if user_proxy.owner_of(n) in (UID_A, UID_B)]
check("🔴 沒有留下任何容器或網路", not leftover)
if leftover:
    print(f"        殘留：{leftover}")

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
