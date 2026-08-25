"""啟動前檢查（從 sessions.py 拆出）。

preflight 是純對外 API（本套件內沒有人呼叫它），image_uid 是它的工具。
"""

from __future__ import annotations

import os
import socket
import uuid
from contextlib import suppress

import docker

from . import config, user_proxy


def image_uid(client: docker.DockerClient | None = None) -> tuple[str, int | None]:
    """問那顆 session image：它裡面的 `nathan` 到底是幾號。

    回 `(status, uid)`，status 三選一：
      - `"ok"`         → 讀到了，`uid` 是真值
      - `"unstamped"`  → image 在，但沒有 `NCR_UID` 標記（改版前 build 的那些）
      - `"unavailable"`→ image 不在本機，或 daemon 問不到

    ⚠ **這是整條 uid 鏈上唯一的「現實」。** `APP_UID` 與 `CLAUDE_PTY_SESSION_UID` 都是
      旋鈕：兩個一起設錯，就沒有任何人會反對（那正是舊版檢查的破口——它比的是兩個
      旋鈕彼此，不是旋鈕跟現實）。所以判斷一律以這裡讀回來的值為準。

    LABEL 與 ENV 兩個都讀：build 時兩邊都有 stamp，讀得到哪個算哪個——只認一種查法的話，
    哪天 stamp 的方式改了，這支會安靜地退化成 `unstamped`。
    """
    try:
        c = client or docker.from_env(timeout=config.DOCKER_TIMEOUT)
        attrs = c.images.get(config.IMAGE).attrs
    except Exception:  # noqa: BLE001 — image 不在／daemon 不通都算查不到
        return ("unavailable", None)
    cfg = attrs.get("Config") or {}
    raw = (cfg.get("Labels") or {}).get("ncr.uid")
    if not raw:  # None 或空字串都要往下找 ENV，不然空 LABEL 會蓋掉它
        for kv in cfg.get("Env") or []:
            if kv.startswith("NCR_UID="):
                raw = kv.split("=", 1)[1]
                break
    if raw is None or not str(raw).strip():
        return ("unstamped", None)
    try:
        return ("ok", int(str(raw).strip()))
    except ValueError:
        # stamp 壞掉（build-arg 被塞了非數字）。當成沒有 stamp，別讓一個爛值冒充現實。
        return ("unstamped", None)


def preflight() -> tuple[list[str], list[str]]:
    """啟動自檢：回傳 `(提醒, 致命)` 兩份清單。

    ⚠ **兩者的差別是「服務該不該起來」，不是嚴重度的形容詞。**
      提醒＝有這個問題服務仍然做得了事（例如 uid 對不上只影響某些情境）；
      致命＝起來了也一定做不了事（例如 HOST_REPO_ROOT 設錯，每一次建 session 都會
      在 docker 的 `mounts denied` 500 上失敗）。後者只印不停等於沒有人會看：訊息在
      docker log 裡一秒被沖走，而健康檢查照樣綠燈。

    ⚠ **這支有副作用**：它會 `makedirs` per-user 空間的根目錄（ADR 0014）。那不是「檢查」
      該做的事，但必須有人做——不先建的話 dockerd 會在 bind mount 時把它建成 root:root，
      控制平面就寫不進去。放在這裡是因為它是啟動路徑上唯一跑得夠早的地方。

    最重要的一項是 entrypoint.sh 掛載——ADR 0006 的非互動 env-skip 就在那份檔案裡。
    掛不到時 session 會退回 image 內烘的舊版 entrypoint，**跳出互動選單卡住**，而且是
    靜默降級（2026-07-25 實測踩到：容器化後 _SELF_REPO_ROOT 推導成 "/"）。
    """
    problems = []
    # ⚠ `fatal` 與 `problems` 的差別是**會不會讓服務起來**。
    #   這個系統原本的立場是「大聲講，不要靜默降級」——但只印不停等於沒有人會看：
    #   訊息在 docker log 裡一秒就被沖走，而服務照樣顯示健康。對於「起得來但一定
    #   做不了事」的設定錯誤，正確的行為是**當場停掉**，讓部署的人立刻知道。
    fatal = []
    # ⚠ **這裡不再建任何共用的 session network。** session 住在**它主人那一張**上
    #   （`claude-pty-user-{id}`），由 `create()` 在建容器之前 `ensure_network` 建出來——
    #   那是 per-user 的，開機時根本不知道等一下會有誰來開場，先建不了。
    #
    # ⚠ 這裡曾經建 `claude-pty-sessions` 給所有人共用。它退役了（ADR 0016），但**升級前
    #   的機器上那張網還在，而且繼續佔著一格位址池**——reconciler 只掃有 label 的網路，
    #   永遠不會碰它。整台機器只有 31 格，一格是真的成本，所以講出來讓人清掉。
    #   訊息會在他清掉之後自己消失：這不是狼來了，是一件真的還沒做完的事。
    # ⚠ **只報不刪。** 自動刪是有副作用的動作，而那張網上可能還掛著升級前開的、還在跑的
    #   session（它們會繼續用它直到被關掉）。判斷「還有沒有人在上面」需要的資訊比一句
    #   提醒多得多，交給人。
    if config.LEGACY_NETWORK_ENV:
        # 一個被靜靜忽略的旋鈕是最難查的那種：設了、重啟了、什麼都沒變，而且沒有訊息。
        problems.append(
            f"CLAUDE_PTY_NETWORK（目前是 {config.LEGACY_NETWORK_ENV}）**已經沒有作用**"
            f"——session 現在住在每個使用者自己的網路上（ADR 0016）。請從 .env 移除。"
        )
    with suppress(Exception):  # noqa: BLE001 — 查不到就別報，這只是提醒不是檢查
        _c = docker.from_env(timeout=config.DOCKER_TIMEOUT)
        # ⚠ 精確比對：docker 的 `names` filter 是**子字串**比對，撿回來還要對名字。
        if any(
            n.name == config.LEGACY_SESSION_NETWORK for n in _c.networks.list(names=[config.LEGACY_SESSION_NETWORK])
        ):
            problems.append(
                f"舊的共用 session network {config.LEGACY_SESSION_NETWORK} 還在。"
                f"已經沒有人會用它，但它佔著一格位址池（整台機器只有 31 格）。"
                f"確認沒有 session 還掛在上面之後移除："
                f"docker network rm {config.LEGACY_SESSION_NETWORK}"
            )
    # Telemetry 的接線：**jaeger 不歸我們管，但「它到不到得了」是我們的問題。**
    #
    # 規約是「需要 jaeger 的那一方，把 jaeger 接到自己的網路上」（見 user_proxy.attach_jaeger）。
    # 開機這一輪要接兩種：
    #   · **所有既有的使用者網路** — 涵蓋「jaeger 比那些網路晚起來」。新建的那些由
    #     `ensure_network` 當場接，reconciler 每輪再兜一次底。
    #   · **控制平面自己那幾張**   — `_jaeger_reachable()` 從這裡發出探測
    #
    # ⚠ 兩種**都要**。只接使用者網路的話探測會失敗 → 控制平面判定「送不到」→ 根本不設
    #   OTEL env，於是 session 明明到得了卻不送。**探測與現實脫節，比探測失敗更難查。**
    # ⚠ jaeger 不在就安靜跳過：它是選配設施，不是缺陷。整段包在 suppress 裡——這是錦上
    #   添花，任何失敗都不該影響控制平面啟動。
    with suppress(Exception):  # noqa: BLE001 — 見上
        _c = docker.from_env(timeout=config.DOCKER_TIMEOUT)
        _want = {n.name for n in user_proxy.list_networks(_c)}
        with suppress(Exception):  # 沒跑在容器裡（本機開發）就只接使用者網路
            _me = _c.containers.get(socket.gethostname())
            _want |= set(_me.attrs["NetworkSettings"]["Networks"])
        user_proxy.attach_jaeger(_c, sorted(_want))

    # ⚠ **這裡刻意沒有「位址池餘裕」的預先檢查。** 曾經寫過一版：啟動時試建 N 個 network
    #   再刪掉，建不出來就警告。它有兩個問題，而且都是自找的：
    #     · `preflight()` 在 **import `server.app` 時**就跑——每一個 web worker、reconciler、
    #       以及每一支 import 它的測試都會建了又刪，白白攪動一個**全機器共用**的資源。
    #     · compose 裡 control 與 reconciler **同時啟動**，兩邊搶建同名的探測 network，
    #       接著在 finally 裡互刪對方的。
    #   真正需要的訊息在**用完的那一刻**已經有了（建 network 失敗會把「池滿」與其他
    #   錯誤講清楚）。**在事情發生時講清楚，勝過事先猜一個數字。**
    if config.ENTRYPOINT is None and not os.path.isfile(config.ENTRYPOINT_SH_SELF):
        problems.append(
            f"找不到 {config.ENTRYPOINT_SH_SELF}——session 將使用 image 內烘的 entrypoint，"
            f"若該版本沒有 CLAUDE_PTY_* env-skip 就會停在互動選單。"
            f"容器化部署請設 CLAUDE_PTY_SELF_REPO_ROOT 指向掛進來的 repo 路徑。"
        )
    # ⚠ **HOST_REPO_ROOT 設錯的話，這裡不喊就要等到有人按「建立 session」才炸。**
    #   而且炸的樣子是 docker 的 500：
    #     mounts denied: The path /repo/dev-container/entrypoint.sh is not shared from the host
    #   `os.path.exists()` 驗不到這件事：compose 把 repo 掛在 `${HOST_REPO_ROOT}`，所以
    #   **控制平面容器裡那個路徑一定存在**，即使 host 上根本沒有。查得到真相的只有 daemon。
    #
    #   問法：compose 的設計是把 repo 掛成**同一個路徑**（來源＝目的，見 ADR 0009），
    #   所以只要問 daemon「我自己那個掛載的來源是什麼」，跟目的一比就知道。
    #   不相等＝`.env` 的 HOST_REPO_ROOT 沒設或設錯，而 session 容器會拿那個值當來源。
    if config.MOUNTS:
        try:
            _c = docker.from_env(timeout=config.DOCKER_TIMEOUT)
            _me = _c.containers.get(socket.gethostname())
            _mine = {m.get("Destination"): m.get("Source") for m in (_me.attrs.get("Mounts") or [])}
        except Exception:  # noqa: BLE001 — 問不到就跳過；docker 不通有別的地方會喊
            _mine = {}
        _src = _mine.get(config.HOST_REPO_ROOT)
        if _src and _src != config.HOST_REPO_ROOT:
            # **致命**：這個設定錯了，每一次建 session 都會失敗，服務起來也做不了事。
            fatal.append(
                f"HOST_REPO_ROOT 設錯了：容器裡看到的是 {config.HOST_REPO_ROOT}，"
                f"但 daemon 那側的來源是 {_src}。這兩個必須相同（repo 掛成同一個路徑，"
                f"ADR 0009）。**現在這樣建 session 一定會失敗**，而且錯誤會出現在 docker "
                f"的 500 裡（mounts denied），不會指回這裡。"
                f"請在 deploy/.env 設 HOST_REPO_ROOT={_src} 再重新部署。"
            )
    # MOUNTS 的來源是 host 路徑，由 daemon 解讀；控制平面容器化後本來就看不到它們，
    # 故只在「HOST 與 SELF 相同」（非容器化）時檢查，否則會誤報。
    # ⚠ MOUNTS 的 key **不一定是路徑**：trivy 的 cache 是 named volume（ADR 0018），
    #   key 是 volume 名。拿 `os.path.exists()` 去問一個 volume 名永遠是 False，於是
    #   非容器化部署每次啟動都收到一句「掛載來源不存在」的假警報。只查看起來是絕對
    #   路徑的那些——volume 由 docker 自己負責存在，不需要我們檢查。
    if config.MOUNTS and config.HOST_HOME == config._SELF_HOME:
        for src in config.MOUNTS:
            if not os.path.isabs(src):
                continue  # named volume，不是路徑
            if not os.path.exists(src):
                problems.append(f"掛載來源不存在（session 內可能缺設定/憑證）：{src}")
    # per-user 空間的根目錄（ADR 0014）。這一個查的是 **SELF**——控制平面得自己在裡面
    # mkdir 與寫種子檔，所以不是「daemon 看得到就好」，是「我現在就要寫得進去」。
    # 建不出來的話每一次建立 session 都會失敗，而錯誤會出現在很後面（provision 拋出），
    # 開機就講清楚比較好。
    if config.MOUNTS:
        try:
            os.makedirs(config.SPACE_SELF, mode=0o700, exist_ok=True)
            # ⚠ **不要只問 `os.access(W_OK)`。** 它有兩個具體的失效方式：
            #   · 建立目錄項目需要的是 `W_OK|X_OK`，少了 X_OK 的目錄 W_OK 仍為真，
            #     而 `mkdir` 會 EACCES；
            #   · `os.access` 用的是 **real** uid/gid，而控制平面在容器裡以 APP_UID 執行，
            #     兩者不同時它回答的是另一個身分的問題。
            #   問「現在這個行程能不能在這裡建東西」的唯一誠實方法，是去建一個然後刪掉。
            #   每次啟動寫一次的代價可以忽略，而它換到的是這道 fatal 真的驗過了一件事。
            _probe = os.path.join(config.SPACE_SELF, f".preflight-{os.getpid()}-{uuid.uuid4().hex}")
            try:
                os.mkdir(_probe, 0o700)
            finally:
                # 一定要清掉：這是探測不是狀態。用 rmdir 而不是 rmtree，探測目錄是空的，
                # 真要有東西在裡面代表撞名了，那時候寧可留著讓人看見也不要遞迴刪。
                with suppress(OSError):
                    os.rmdir(_probe)
                # ⚠ 上面那個 finally 涵蓋不了「行程在 mkdir 與 rmdir 之間被 SIGKILL」。
                #   那時候會留下一顆空目錄，而且每被硬砍一次就多一顆。順手掃掉前幾次的
                #   殘骸，這道探測才不會自己變成它要驗的那個目錄裡的垃圾。
                with suppress(OSError):
                    for _stale in os.listdir(config.SPACE_SELF):
                        if _stale.startswith(".preflight-") and _stale != os.path.basename(_probe):
                            with suppress(OSError):
                                os.rmdir(os.path.join(config.SPACE_SELF, _stale))
        except OSError as e:
            # **致命**，理由跟隔壁的 HOST_REPO_ROOT 一模一樣：這個設定錯了，**每一次**建
            # session 都會失敗。它原本只進 `problems`，於是服務以健康的樣子起來、首頁正常、
            # 直到有人按下「建立 session」才炸——而那時錯誤是 provision 拋出來的 OSError，
            # 指不回這裡。同一句話在這個檔案裡已經寫過一次（「只印警告不停下等於沒有」），
            # 這一格是漏掉的那個。
            fatal.append(
                f"per-user 狀態空間不可寫（{config.SPACE_SELF}）：{e}。"
                f"每個 session 的 ~/.claude 都住在這底下（ADR 0014），"
                f"**現在這樣一個 session 都建不起來**。容器化部署請確認該路徑已掛進控制平面"
                f"且擁有者是 APP_UID，並以 CLAUDE_PTY_SPACE_SELF 指明容器內看到的路徑。"
            )
        # 控制平面建目錄用的是**它自己**的 uid，session 容器裡的寫入者則是 nathan
        # （`config.SESSION_UID`，實測 1001 而不是直覺的 1000——見那個常數的說明）。
        # 兩者不同時 0700 的目錄容器就進不去：transcript 寫不下、種子讀不到，症狀是
        # 每一場都撞 onboarding 對話，而最後那道預設停在「No, exit」。
        # ⚠ **只在 host 是 Linux 時檢查**（`config.host_is_linux()`）：只有那裡的 bind mount
        #   會原樣把 uid 帶過去；Docker Desktop（macOS／Windows）都做 uid 對映，在那邊喊是
        #   純噪音。
        # ⚠ 這裡原本寫的是 `sys.platform == "linux"`，而那是**錯的問題**：控制平面跑在容器裡
        #   （ADR 0009），容器內 `sys.platform` 永遠是 linux——那道 guard 從來沒有在正式部署
        #   裡生效過，於是 macOS host 每次啟動都收到這句假警報。問的必須是 host 的作業系統，
        #   而那件事只有 host 講得出來（見 config.HOST_PLATFORM）。
        # ⚠ 比對的對象是 **image 裡的真值**，不是 `config.SESSION_UID`。後者是旋鈕，而
        #   `os.getuid()` 也是旋鈕（APP_UID）——舊版拿這兩個互比，把兩個一起設成同一個
        #   錯的數字就完全靜音，而真正決定成敗的第三個數字從來沒被問過。
        if config.host_is_linux():
            # ⚠ 這段附註不是客套。喊的時候要講得出「我憑什麼這樣判斷」，否則收到誤報的
            #   人無從查起——那正是修之前的處境（容器內問 sys.platform，macOS 每次啟動
            #   都被喊一次）。**三個分支共用同一段**，少掛在哪一條上就等於那條沒說清楚。
            _hint = (
                f"（host 判定為 "
                f"{config.HOST_PLATFORM or '未指明，退回容器內的判斷——那不一定準'}；"
                f"你的 host 不是 Linux 的話這是誤報，"
                f"deploy/redeploy.sh 會自動帶對這個值）"
            )
            _status, _real = image_uid()
            if _status == "unavailable":
                # ⚠ 查不到**不等於通過**。這一格是整條鏈唯一的現實來源，問不到就要說
                #   問不到——靜靜跳過會讓人以為驗過了。
                problems.append(
                    f"無法查證 image「{config.IMAGE}」裡的 uid（image 不在本機或 daemon "
                    f"問不到），所以這一輪**沒有驗過** uid 是否對齊。"
                    f"先把 image build 出來再重啟控制平面。{_hint}"
                )
            elif _status == "unstamped":
                # 改版前 build 的 image。退回舊的兩旋鈕比對當 fallback——它擋得住一部分
                # 情況，總比什麼都不檢查好，但要明講它驗不到真值。
                if os.getuid() != config.SESSION_UID:
                    problems.append(
                        f"控制平面以 uid {os.getuid()} 執行，但設定說 session 的寫入者是 "
                        f"{config.SESSION_UID}。per-user 空間是 0700，對不上時容器進不去"
                        f"——症狀是每一場都撞 onboarding 對話。{_hint}"
                    )
                problems.append(
                    f"image「{config.IMAGE}」沒有 NCR_UID 標記（改版前 build 的）。"
                    f"這一輪只比對得了設定值彼此，**驗不到 image 裡的真實 uid**。"
                    f"重 build 一次（`--build-arg NCR_UID=$(id -u)`）之後這道檢查才有意義。"
                    f"{_hint}"
                )
            elif _real != os.getuid() or _real != config.SESSION_UID:
                problems.append(
                    f"uid 沒有對齊：image「{config.IMAGE}」裡的 nathan 是 **{_real}**、"
                    f"控制平面以 **{os.getuid()}** 執行（APP_UID）、設定值 "
                    f"CLAUDE_PTY_SESSION_UID 是 **{config.SESSION_UID}**。"
                    f"三者必須相同——per-user 空間是 0700、憑證檔是 0600，"
                    f"對不上的症狀是每一場撞 onboarding 對話、終端停在登入提示、"
                    f"restricted 卡滿逾時，沒有一個看起來像 uid 問題。"
                    f"做法：`APP_UID={os.getuid()}` 與 image 的 "
                    f"`--build-arg NCR_UID={os.getuid()}` 對齊（Linux 上請用 `id -u`），"
                    f"並把既有的 {config.SPACE_SELF}/user-* 一併 chown。{_hint}"
                )
    if config.UI_INVALID is not None:
        problems.append(
            f"CLAUDE_PTY_UI={config.UI_INVALID!r} 不是 {'/'.join(config.UI_CHOICES)}，已當成 "
            f"legacy。靜靜降級的話，「我明明設了 vue」與「vue 版壞了」在畫面上長得一模一樣。"
        )
    if config.UI == "vue" and not os.path.isfile(os.path.join(config.DIST_DIR, "index.html")):
        problems.append(
            f"CLAUDE_PTY_UI=vue 但 {config.DIST_DIR}/index.html 不存在——前端還沒 build。"
            f"跑 `cd frontend && npm ci && npm run build`（或用 deploy/Dockerfile 的 node 階段）。"
            f"沒有它的話三個頁面都會回 404，而那看起來像路由壞掉。"
        )
    if config.PAGE_SIZE_CLAMPED is not None:
        problems.append(
            f"CLAUDE_PTY_PAGE_SIZE={config.PAGE_SIZE_CLAMPED} 不在 1–{config.MAX_PAGE_SIZE} "
            f"之內，已夾成 {config.PAGE_SIZE}。不夾的話每一張列表都會回 400"
            f"（預設頁大小會去撞 MAX_PAGE_SIZE 的上限檢查）。"
        )
    if config.SSH_AUTH_SOCK_HOST:
        # 這不是「設錯了」而是「你開了一個很大的權限」——開著是合法的，但每次啟動都要
        # 講一次：沒有租戶隔離，這把 agent 等於發給每一個能建立 session 的帳號（ADR 0011）。
        problems.append(
            f"SSH agent 轉發已開啟（{config.SSH_AUTH_SOCK_HOST} → "
            f"{config.SSH_AUTH_SOCK_BIND}）：每個 session 都能以你的身分認證任何信任該 key "
            f"的主機，且無法只給部分使用者。不需要就清掉 CLAUDE_PTY_SSH_AUTH_SOCK。"
        )
        # 非容器化時（HOST==SELF）順手驗一下路徑真的在——容器化的話控制平面看不到 host
        # 路徑，硬查會誤報（同下方 MOUNTS 的理由）。
        if config.HOST_HOME == config._SELF_HOME and not os.path.exists(config.SSH_AUTH_SOCK_HOST):
            problems.append(
                f"CLAUDE_PTY_SSH_AUTH_SOCK={config.SSH_AUTH_SOCK_HOST} 不存在——"
                f"建立 session 會直接失敗（bind 來源不存在）。agent 沒起來？"
                f"socket 路徑每次登入可能不同，請確認 `echo $SSH_AUTH_SOCK`。"
            )
    # ⚠ **只綁 loopback 時不喊。** 這道提醒防的是「cookie 走未加密網路被側錄重放」，
    #   而入口只有本機連得到時那個情境不存在。以前不分情況都喊，於是本機開發每次啟動
    #   都收到一次——每次都喊的提醒，等到真的該喊那次就沒有人在看了（那正是這一整輪
    #   在修的同一種病：訊號與事實對不上）。
    #   查不到 bind 位址時仍然喊：不知道不等於安全。
    if config.BEHIND_PROXY and not config.COOKIE_SECURE and not config.entry_is_loopback_only():
        problems.append(
            f"BEHIND_PROXY=1 但 COOKIE_SECURE=0，而入口綁在 "
            f"{config.BIND_ADDR or '（未知，不是經 compose 起的）'}：登入 cookie 不帶 "
            f"Secure，若該入口是 HTTP 或經未加密網路，cookie 可被側錄重放（review H6）。"
            f"上 TLS 後請設 CLAUDE_PTY_COOKIE_SECURE=1。"
        )
    # 自訂 CA（內部憑證簽的 GitLab）。**填了卻找不到要在這裡喊。**
    #
    # ⚠ 不喊的話它會退化成一個沒有任何訊號的失敗：代理照樣建起來、容器健康、chip 綠燈，
    #   但每一個 git / API 呼叫都在 TLS 那關 502——而 `users.gitlab_proxy_error` 那條訊號
    #   是靠「代理沒活著」觸發的（見 reconciler._note_proxy_down），**它不會亮**。
    #   真正的原因只在容器的 error_log 裡，而使用者只看得到「GitLab 連不到」。
    # ⚠ 而且**絕不可以靜靜退回系統 CA**：那會變成「設定了、重啟了、什麼都沒變」，
    #   與這個功能要解決的問題是同一種。
    # ⚠ 查 *_SELF：這是「控制平面現在讀不讀得到」，不是「daemon 待會兒掛不掛得到」
    #   ——容器化部署下兩者是不同路徑（ADR 0009）。SELF 沒另外設時它就等於 HOST。
    if config.GITLAB_CA_FILE:
        if not config.gitlab_enabled():
            problems.append(
                f"設了 CLAUDE_PTY_GITLAB_CA_FILE={config.GITLAB_CA_FILE}，但沒設 "
                f"CLAUDE_PTY_GITLAB_HOST——GitLab 代理整個功能是關的，這個 CA 不會有人用。"
            )
        elif not os.path.isfile(config.GITLAB_CA_FILE_SELF):
            problems.append(
                f"CLAUDE_PTY_GITLAB_CA_FILE 指向的檔案不存在：{config.GITLAB_CA_FILE_SELF}。"
                f"代理會照樣建起來、容器也健康，但每個 git / API 呼叫都會在 TLS 驗證失敗"
                f"（502），而畫面上的代理狀態是綠的、不會有任何錯誤訊息。"
                f"容器化部署請另外以 CLAUDE_PTY_GITLAB_CA_FILE_SELF 指明控制平面看得到的路徑。"
            )
    return problems, fatal
