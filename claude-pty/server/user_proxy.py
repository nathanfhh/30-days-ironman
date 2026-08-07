"""per-user GitLab 憑證代理的 docker 生命週期（ADR 0016）。

**每個設了 PAT 的使用者一個網路 + 一顆 nginx**，他所有的 session 都掛在那個網路上，
以 network alias `gitlab-proxy` 找到它。

獨立成一個模組是因為**兩邊都要用**：`sessions.create()` 在建立 session 前確保網路與代理
就位，`reconciler` 負責把漂掉的狀態收斂回來。放在任何一邊都會讓另一邊反向 import。

## 這裡的規矩，每一條都是量過或踩過的

⚠ **`create` → `connect` → `start`，順序不可以動。** `init-firewall.sh` 放行的是
  「entrypoint 跑到那一刻的直連網段快照」，**之後才 `network connect` 的網路不在放行清單
  裡**——介面有了、路由有了，但封包被 REJECT，而且**永遠不會好**（reconciler 補得了網路，
  補不了 iptables，防火牆不會重跑）。這是 `sessions.create()` 那邊的責任，寫在這裡是因為
  讀這個模組的人一定會想「為什麼不乾脆事後掛」。

⚠ **同名網路並發建立時 daemon 端強制唯一**（兩發同時打，一個成功、一個收 `already
  exists`）。所以「已存在」要當成成功——多個 web worker 會同時走這條路。

⚠ **改 PAT 不換容器，用熱重載**：`put_archive` → `nginx -t` → `SIGHUP`。容器 PID 不變、
  服務不中斷；而且壞設定弄不死它（`nginx -t` 先擋，就算硬 HUP，nginx 也只是拒絕載入、
  繼續跑舊的）。**不可以** blue/green——同一個 alias 兩顆並存會 DNS round-robin，
  那不是零停機，是「一半請求用舊 PAT」。

⚠ **設定是不是最新的，問容器自己**（`/_state`），不要另外存一份狀態。存 DB 或 label 都會
  出現「記錄說是新的、實際是舊的」；而 label 建立後根本改不了，熱重載完更新不了它。
"""
from __future__ import annotations

import io
import tarfile
from contextlib import suppress

import docker

from . import config, gitlab_proxy


def network_name(user_id: int) -> str:
    return f"{config.USER_NETWORK_PREFIX}{user_id}"


def proxy_name(user_id: int) -> str:
    return f"{config.PROXY_NAME_PREFIX}{user_id}"


def _exact(client: docker.DockerClient, name: str):
    """依名稱找 network，**精確比對**。找不到回 `None`。

    ⚠ **docker 的 `names` filter 是子字串比對，不是精確比對。** 查 `claude-pty-user-1`
      會同時回 `claude-pty-user-13` 與 `claude-pty-user-1`——而且**精確的那個不一定排在
      前面**（moby 沒有文件化這個順序）。

    ⚠ 直接拿 `list(...)[0]` 的後果是**跨使用者的憑證外洩**：user-1 今天第一次開 session、
      user-13 的網路已經在，於是 user-1 的 session 被接到 **user-13 的網路**上，alias
      `gitlab-proxy` 解到他的代理、用他的 PAT。那正是這整套設計要建立的邊界。
      而 uid 互為十進位前綴是遲早的事（1 與 13、2 與 25…），帳號又不會被刪（ADR 0010），
      所以這不是邊角情境。
    """
    for n in client.networks.list(names=[name]):
        if n.name == name:
            return n
    return None


def owner_of(obj) -> int | None:
    """這顆代理／這個網路是誰的。標壞了（缺、或不是數字）回 `None`。

    ⚠ **容器與網路要用不同的取法。** docker-py 的 `Container` 有 `.labels`，
      `Network` **沒有**，只能走 `attrs["Labels"]`——而那個 dict 在網路不帶任何 label 時是
      `None` 不是 `{}`。這裡一次收乾淨，呼叫端不必記得自己在處理哪一種。
    """
    labels = getattr(obj, "labels", None)
    if labels is None:
        labels = obj.attrs.get("Labels") or {}
    raw = labels.get(config.PROXY_OWNER_LABEL, "")
    return int(raw) if raw.isdigit() else None


class PoolExhausted(RuntimeError):
    """docker 的位址池用完了，建不出新的 network。

    ⚠ 這個要與其他錯誤分開講：使用者看到「建立 session 失敗」時，該去清網路而不是去查
      GitLab。docker 自己的訊息是 `all predefined address pools have been fully subnetted`。
    """


def _conf_tar(conf: bytes, name: str = "nginx.conf") -> bytes:
    """把 nginx.conf 包成 `put_archive` 吃的 tar。

    ⚠ tar 裡的 metadata 要自己設。`put_archive` 會**照著 tar 裡的 uid/gid/mode 落地**，
      而 `TarInfo` 的預設是 uid/gid 0（正好）但 **mode 是 0**——不明寫的話檔案會變成沒有
      任何權限位元，nginx 以 root 讀得到、但那是一個會讓人查很久的怪狀態。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(conf)
        info.mode = 0o444          # 唯讀：這顆容器裡沒有任何人需要改它
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        tar.addfile(info, io.BytesIO(conf))
    return buf.getvalue()


def _test_label() -> dict:
    """測試建出來的東西要標起來，正式 reconciler 才會跳過。

    ⚠ 用 `TEST_LABEL_DEFAULT_KEY`（寫端）而不是 `TEST_LABEL_KEY`（讀端）——後者會被測試
      暫時改掉，跟著用會讓建出來的東西不帶真正的測試標記，同一台機器上的**正式**
      reconciler 就不會跳過它。標記的是「這是測試建的」這個事實，不該隨著「這一輪誰在看」
      而變。
    """
    return {config.TEST_LABEL_DEFAULT_KEY: config.TEST_MARK} if config.TEST_MARK else {}


# --- 網路 ---------------------------------------------------------------------

def ensure_network(client: docker.DockerClient, user_id: int):
    """確保這個使用者的網路存在，回傳它。

    ⚠ 「已存在」當成功再查一次——多個 worker 會同時走這條路（見模組 docstring）。
    ⚠ 位址池滿要拋 `PoolExhausted` 而不是原始的 `APIError`，呼叫端才講得出正確的下一步。
    """
    name = network_name(user_id)
    existing = _exact(client, name)
    if existing:
        return existing
    try:
        return client.networks.create(
            name, driver="bridge",
            labels={config.NETWORK_LABEL_KEY: config.NETWORK_LABEL_VALUE,
                    config.PROXY_OWNER_LABEL: str(user_id),
                    **_test_label()})
    except docker.errors.APIError as e:
        msg = str(e)
        if "already exists" in msg:
            # 另一個 worker 搶先建好了——那正是我們要的結果
            found = _exact(client, name)
            if found:
                return found
            raise
        if "address pools" in msg:
            raise PoolExhausted(
                f"docker 的位址池已用完，建不出 {name}。這是**整台機器**共用的資源"
                f"（每個 compose 專案各佔一格），清掉沒在用的 network，"
                f"或在 daemon.json 調 default-address-pools。") from e
        raise


def remove_network(client: docker.DockerClient, user_id: int) -> bool:
    """收掉這個使用者的網路。還有容器掛著時 docker 會拒絕——那是對的，交給呼叫端下輪再試。"""
    try:
        client.networks.get(network_name(user_id)).remove()
        return True
    except docker.errors.NotFound:
        return False


def list_networks(client: docker.DockerClient) -> list:
    return client.networks.list(filters=config.NETWORK_FILTERS)


# --- 代理容器 -----------------------------------------------------------------

def find(client: docker.DockerClient, user_id: int):
    """這個使用者的代理容器；沒有就 `None`。"""
    try:
        return client.containers.get(proxy_name(user_id))
    except docker.errors.NotFound:
        return None


def list_all(client: docker.DockerClient) -> list:
    """所有 per-user 代理（含已停止的）。"""
    return client.containers.list(all=True, filters=config.PROXY_FILTERS)


def create(client: docker.DockerClient, user_id: int, pat: str) -> str:
    """建一顆代理並啟動，回傳 container id。撞名時回傳既有那顆（見 `create_or_adopt`）。"""
    cid, _ = create_or_adopt(client, user_id, pat)
    return cid


def create_or_adopt(client: docker.DockerClient, user_id: int,
                    pat: str) -> tuple[str, bool]:
    """建一顆代理並啟動。回傳 `(container id, 是不是本次建的)`。呼叫端要先確保網路存在。

    ⚠ 撞到名稱衝突（另一個 worker 搶先建好）時**回傳既有那顆的 id 與 `False`**，不重建
      也不拋——理由見下面 `except APIError` 那段，那是一條會讓敗方刪掉勝方容器的路。

    ⚠ **第二個回傳值是清理路徑的判準，不是裝飾。** 呼叫端失敗要收拾殘局時只能收
      「自己建的那一顆」；`False` 代表這顆是別人的，碰它就是把人家建到一半的東西刪掉。

    ⚠ 這裡是 PAT 離開資料庫的其中一條路：明文 → nginx.conf → `put_archive` → 容器的檔案
      系統。不落 host 磁碟（沒有 bind mount）、不進環境變數、`docker inspect` 也看不到。
      **另一條是 `reload()`**（換 PAT 時同樣把含 PAT 的設定送進容器）——要稽核 PAT 的
      流向請兩條一起看。
    ⚠ 設定**不可以**用 bind mount 掛進去：那樣之後就換不掉了（`docker cp` 會撞
      `device or resource busy`），而熱重載的整個前提就是換得掉。
    """
    net = network_name(user_id)
    # ⚠ **要用低階 API**，因為只有它能在建立時就帶上 network alias。三種寫法都試過：
    #   · `containers.create(network=X, networking_config=...)` → **alias 被默默丟掉**
    #     （`Aliases=None`），而症狀是 session 解析不到 `gitlab-proxy`。
    #   · 先建在 `none` 再 `network.connect(aliases=...)` → daemon 直接拒絕
    #     （"cannot be connected to multiple networks with one of the networks in
    #     private mode"）。
    #   · 先建在**預設 bridge** 再 connect → alias 有了，但代理**同時留在預設 bridge 上**，
    #     於是任何 bridge 上的容器都能用 IP 打到它——跨使用者隔離當場破掉。不可用。
    #   低階 `create_container(networking_config=...)` 三個問題都沒有：只在使用者網路上、
    #   alias 生效、`/etc/resolv.conf` 是 127.0.0.11（docker 內嵌 DNS，alias 才解析得了）。
    labels = {
        config.PROXY_LABEL_KEY: config.PROXY_LABEL_VALUE,
        config.PROXY_OWNER_LABEL: str(user_id),
        **_test_label(),
    }
    try:
        resp = client.api.create_container(
            config.PROXY_IMAGE,
            name=proxy_name(user_id),
            labels=labels,
            # ⚠ 不設 restart policy：孤兒永生比孤兒更難處理。回收由 reconciler 負責。
            host_config=client.api.create_host_config(
                network_mode=net,
                mem_limit=config.PROXY_MEM_LIMIT,
                pids_limit=config.PROXY_PIDS_LIMIT,
                # 逃生口，預設空的——見 config.PROXY_EXTRA_HOSTS 的說明。
                extra_hosts=config.PROXY_EXTRA_HOSTS or None),
            # session 就是靠這個名字找到它的。
            networking_config=client.api.create_networking_config({
                net: client.api.create_endpoint_config(aliases=[config.PROXY_ALIAS])}),
        )
    except docker.errors.APIError as e:
        # ⚠ **名稱衝突＝另一個 worker 搶先建好了**，與 `ensure_network` 那邊的
        #   `already exists` 是同一類競態（多個 web worker 會同時走 `_ensure_user_proxy`；
        #   使用者剛設好 PAT 就連開兩場是最容易撞上的時機）。
        #
        #   不接的後果不是「這場沒有代理」而已，而是**敗方會把勝方的代理刪掉**：例外會
        #   冒到 `sessions._ensure_user_proxy` 的補償，那裡若以狀態當判準，勝方那顆此刻
        #   正停在 `created`（還沒 `put_archive`／`start`），於是被 force-remove。
        #   兩場都拿不到 GitLab，要等 reconciler 下一輪才補回來。
        #
        # ⚠ 判 `status_code` 不判訊息字串：`POST /containers/create` 的 409 只有「名稱已被
        #   使用」一種意思，而它的訊息（`already in use by container`）與 network 的
        #   `already exists` 不同字樣，跟著抄字串比對會漏。
        if e.status_code != 409:
            raise
        won = find(client, user_id)
        if won is None:
            raise            # 撞名的對象轉眼又不見了：情況不對，別假裝成功
        return won.id, False     # ← `False`＝這顆不是我建的，呼叫端清理時不可以碰
    cid = resp["Id"]
    # ⚠ **建出來之後的每一步失敗，都由這裡自己收拾。** `create_container` 成功而
    #   `put_archive`／`start` 失敗會留下一顆 `created` 狀態、設定裡可能已經有 PAT 的容器；
    #   而例外是在**回傳之前**拋的，呼叫端拿不到 id，也就無從判斷「這顆是不是我的」。
    #   把清理留給呼叫端就只能退回「看狀態」的判準，而那條會誤刪別的 worker 正在建的那顆。
    #   誰建的誰清，責任才對得起來。
    try:
        client.api.put_archive(cid, "/etc/nginx", _conf_tar(gitlab_proxy.render_conf(pat)))
        client.api.start(cid)
    except Exception:
        with suppress(Exception):        # 清理盡力而為，不可以蓋掉原始例外
            client.api.remove_container(cid, force=True)
        raise
    return cid, True


def remove(client: docker.DockerClient, user_id: int) -> bool:
    """收掉這個使用者的代理。已經不在也算成功（等冪）。"""
    try:
        client.api.remove_container(proxy_name(user_id), force=True)
        return True
    except docker.errors.NotFound:
        return False


# --- 設定的新舊 ---------------------------------------------------------------

def running_state(client: docker.DockerClient, user_id: int) -> str | None:
    """問**容器自己**現在跑的是哪一份設定；問不到回 `None`。

    ⚠ 不要改成讀 DB 或 label：那會多出一份可能說謊的狀態，而 label 建立後改不了
      （熱重載完更新不了它），DB 則會出現「記錄說是新的、實際是舊的」。
    """
    c = find(client, user_id)
    if c is None or c.status != "running":
        return None
    code, out = c.exec_run(
        ["wget", "-qO-", f"http://127.0.0.1:{config.PROXY_PORT}/_state"])
    return out.decode(errors="replace").strip() if code == 0 else None


def reload(client: docker.DockerClient, user_id: int, pat: str) -> bool:
    """換設定並熱重載。回傳有沒有真的重載成功。

    **先落到暫存檔 → `nginx -t -c` 驗它 → 過了才蓋上去 → `SIGHUP`**：容器不重建、
    IP 與 alias 不變、session 無感。

    ⚠ **`nginx -t` 沒過就什麼都不做**，尤其不可以把它當成「已更新」——下一輪會再試一次。
      （就算硬 HUP 也不會出事：nginx 拒絕載入壞設定、繼續跑舊的。但那樣就沒有人知道設定
      其實沒換成功，於是每一輪都白做一次。）

    ⚠ **驗證要在蓋上去之前，不可以先寫再驗。** 先寫的話，`-t` 失敗時磁碟上留的是一份
      **沒通過驗證**的 `nginx.conf`——這顆容器現在還活著沒事（跑的是記憶體裡的舊設定），
      但只要它之後被停掉再啟動（reconciler 與 `_ensure_user_proxy` 都有「exited → 直接
      start，設定已經在它裡面」這條捷徑），就會拿那份壞設定冷啟動而起不來。
      **「壞設定弄不死它」只在 HUP 這條路成立，冷啟動不成立。**
      失敗側是真實路徑不是假想：主機名解不出來時 `-t` 會回
      `[emerg] host not found in upstream`。
    """
    c = find(client, user_id)
    if c is None or c.status != "running":
        return False
    staged = "/etc/nginx/nginx.conf.next"
    c.put_archive("/etc/nginx", _conf_tar(gitlab_proxy.render_conf(pat), "nginx.conf.next"))
    code, out = c.exec_run(["nginx", "-t", "-c", staged])
    if code != 0:
        # 沒過就把暫存檔收掉，別留一份看起來像「下一版」的垃圾在容器裡
        c.exec_run(["rm", "-f", staged])
        # ⚠ **一定要留下痕跡。** 這條會反覆失敗（例如代理裡解不出 GitLab 的主機名），
        #   而指紋永遠不會收斂，於是每一輪都重跑一次。靜靜 `return False` 的話，唯一
        #   看得到的訊號是 reconciler 的重載計數——而假捷報比沒有訊號更糟。
        # ⚠ 只取 nginx 自己的錯誤輸出並截短：設定檔內容含 PAT，不可以整段印出來。
        #   `nginx -t` 的訊息格式是 `[emerg] … in <檔名>:<行號>`，不含檔案內容。
        msg = out.decode(errors="replace").strip().replace("\n", " ")[:160]
        print(f"[claude-pty] ⚠ 代理 {c.name} 的新設定沒通過 nginx -t，這次不換：{msg}",
              flush=True)
        return False
    # ⚠ `mv` 而不是再 `put_archive` 一次：同一份內容驗過就是驗過，重送一次等於讓「驗的」
    #   與「用的」變成兩份可能不同的東西。同檔案系統的 rename 也是原子的，nginx 不會讀到
    #   寫到一半的設定。
    code, out = c.exec_run(["mv", staged, "/etc/nginx/nginx.conf"])
    if code != 0:
        print(f"[claude-pty] ⚠ 代理 {c.name} 的設定換不上去："
              f"{out.decode(errors='replace').strip()[:120]}", flush=True)
        return False
    # nginx 在 HUP 時是**重新開啟這個路徑**，不是沿用啟動時的 fd，所以上面的 `mv`
    # 換掉檔案不會讓它讀到舊 inode。
    client.api.kill(c.id, signal="HUP")
    return True
