"""docker run 參數的組裝（從 sessions.py 拆出）。

build_run_kwargs 維持純函式（不碰 DB、不碰檔案系統），目錄有沒有備妥是 provision 的事。
Profile 是它的輸入；_gitlab_env / _otel_env 是它的兩段環境變數。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import docker

from . import config, crypto, user_proxy
from . import auth as auth_mod


@dataclass
class Profile:
    """session 執行 profile（ADR 0006）。控制平面據此組出 entrypoint env + docker 能力。"""

    # ⚠ 預設一律引用 `config.DEFAULT_*`，**不要在這裡另寫一份字面值**。曾經兩邊各寫
    #   一份，然後 network 分岔了：dataclass 是 "unrestricted"、config 是 "restricted"
    #   ——而 config 那邊的註解白紙黑字寫著「安全預設應該是限制而非開放」。於是任何人
    #   在 server 端寫 `Profile()` 都會拿到一個可任意連外的容器，而且完全無聲
    #   （review 2026-07-25 抓到）。
    #
    # ⚠ 這個寫法依賴一條不變量：**`config.DEFAULT_*` 在 import 之後必須視為不可變**。
    #   三個地方讀它的時機其實都不同——`config` 在自己 import 時讀 env，這裡的欄位預設
    #   在 `sessions` import 時綁定（晚一步），而 `from_dict` 的 `d.get(k, config.X)`
    #   是每次呼叫才讀。它們一致的唯一原因是沒有人在中間改寫過那些常數。
    #   這件事值得明講，因為這個 codebase 確實有「import 後改 config」的習慣
    #   （測試裡的 `config.ENTRYPOINT = None` / `config.MOUNTS = {}` 就是），讀的人
    #   完全有理由以為 `DEFAULT_*` 也能那樣改。真的要改就得改成 `default_factory`。
    #   `test_profile_mapping` 有一條斷言在守這件事：`Profile()` 必須等於當下的
    #   `config.DEFAULT_*`——它比對的正是「import 時的快照」與「當下讀值」。
    cli: str = "claude"  # 這套東西只驅動 claude 一種 CLI
    network: str = config.DEFAULT_NET  # restricted | unrestricted
    capture: bool = config.DEFAULT_CAPTURE
    telemetry: bool = config.DEFAULT_TELEMETRY
    # 模型與思考深度：`claude --model` / `--effort` 的合法別名（見 config.CLAUDE_MODELS）
    model: str = config.DEFAULT_MODEL
    effort: str = config.DEFAULT_EFFORT
    # 憑證怎麼交給 CLI：fd（預設，值不進環境）或 env（官方文件寫過的退路）。
    # 這不是偏好題，是 fd 那條壞掉時的逃生口——見 config.TOKEN_DELIVERIES。
    token_delivery: str = config.DEFAULT_TOKEN_DELIVERY

    @classmethod
    def from_dict(cls, d: dict | None) -> Profile:
        d = d or {}
        return cls(
            cli=d.get("cli", "claude"),
            network=d.get("network", config.DEFAULT_NET),
            capture=_as_bool(d.get("capture"), config.DEFAULT_CAPTURE),
            telemetry=_as_bool(d.get("telemetry"), config.DEFAULT_TELEMETRY),
            model=d.get("model", config.DEFAULT_MODEL),
            effort=d.get("effort", config.DEFAULT_EFFORT),
            token_delivery=d.get("token_delivery", config.DEFAULT_TOKEN_DELIVERY),
        )

    def as_dict(self) -> dict:
        return {
            "cli": self.cli,
            "network": self.network,
            "capture": self.capture,
            "telemetry": self.telemetry,
            "model": self.model,
            "effort": self.effort,
            "token_delivery": self.token_delivery,
        }


def _stored_profile(profile: Profile) -> dict:
    """要寫進 DB 的那一份 profile：與送進容器的值同一份（build_run_kwargs 也讀它）。"""
    return profile.as_dict()


def build_run_kwargs(name: str, sid: str, profile: Profile, user_id: int) -> dict:
    """據 profile 組出 containers.run 的參數（ADR 0006）。純函式，不碰 docker daemon（可單元測試）。

    兩種路徑：
      - CLAUDE_PTY_ENTRYPOINT 覆蓋（escape hatch，如 bash 測試）→ 覆蓋 entrypoint，跳過 entrypoint.sh
        與 profile（選單無意義）。
      - 預設 None → 走 image 的 entrypoint.sh，用 env 非互動答選單（第一層），並補 docker 能力（第二層，
        env 給不了的 cap_add / network / mount）。

    `user_id` 決定 per-user 狀態空間掛哪一份（ADR 0014）。**只收 id、不查 DB**——這支要
    維持純函式才單元測試得動；目錄有沒有備妥是 `provision_user_space()` 的事（create 會先叫）。
    """
    kwargs: dict = {
        "name": name,
        "detach": True,
        "tty": True,
        "stdin_open": True,
        # 供 reconciler 辨識「這是我們管的 session container」——不可靠名稱前綴（見 config）
        "labels": {
            config.SESSION_LABEL_KEY: config.SESSION_LABEL_VALUE,
            # 測試建立的容器多打一個標記，正式 reconciler 據此跳過（見 _remove_orphans）
            **({config.TEST_LABEL_DEFAULT_KEY: config.TEST_MARK} if config.TEST_MARK else {}),
        },
        "mem_limit": config.MEM_LIMIT,
        "nano_cpus": config.NANO_CPUS,
        "pids_limit": config.PIDS_LIMIT,
    }
    volumes = {**config.MOUNTS, **config.user_mounts(user_id)}

    # SSH agent 轉發（opt-in，ADR 0011）。在 escape hatch 之前處理：它是**部署層**的能力，
    # 不隨 profile 或 entrypoint 變——「這台開了轉發」就是每個 session 都有。
    #
    # ⚠ 這一條走 `mounts` 而不是 `volumes`，兩者對「來源不存在」的行為不同：
    #   volumes（Binds）會讓 dockerd **在 host 上建一個 root:root 的目錄**頂替，而這裡的
    #   來源是 agent socket——路徑打錯、或機器剛重開還沒登入時，那個目錄會卡在 socket 該
    #   出現的位置，下次登入 gnome-keyring/ssh-agent 就綁不上去，**壞掉的是 host**。
    #   mounts（type=bind）在來源不存在時直接讓 containers.run 失敗，錯誤看得見、
    #   host 不被動到。代價是這場 session 建不起來——那正是我們要的失敗方向。
    if config.SSH_AUTH_SOCK_HOST:
        kwargs["mounts"] = [
            docker.types.Mount(
                target=config.SSH_AUTH_SOCK_BIND,
                source=config.SSH_AUTH_SOCK_HOST,
                # ⚠ read_only=True（2026-08-22 起）。:ro **不會**讓 socket 連不上
                #   （connect 走 path_permission(MAY_WRITE)，是 inode 檢查，不經過
                #    mnt_want_write，所以 MNT_READONLY 沒被諮詢；反例見
                #    tests/test_ro_socket_mount.py）。
                #   它擋的是「弄壞 host 那顆 socket」：bind mount 與 host 共用同一個 inode，
                #   原生 Linux 上容器對它 chmod／chown 會改到 host 那一顆，症狀是使用者
                #   其他終端機的 ssh 全部失效、且指不到容器。
                #   ⚠ 這不是 agent 的安全邊界——簽章、列舉金鑰、轉送一項都擋不住。
                type="bind",
                read_only=True,
            )
        ]

    # **網路：無條件指定成這個使用者自己那張**（ADR 0016）。
    #
    # ⚠ **這一行必須在 escape hatch 的 return 之前**，理由與上面的 SSH mount 完全相同：
    #   它是**部署層／隔離層**的性質，不隨 profile 或 entrypoint 變。它原本寫在下面正常
    #   路徑那一段，於是走 `CLAUDE_PTY_ENTRYPOINT` 的容器**完全沒有 network 參數、落在
    #   docker 預設 `bridge`**（審查 F-004）——而那張網住著這台機器上每一顆沒指定網路的
    #   容器，正是 ADR 0016 稱為「比它要取代的共用網路還糟」的那個形狀。
    # ⚠ 下面那一段的註解寫著「不可以再退回條件式」，而**提早 return 就是條件式的另一種
    #   寫法**——第一次修那個洞時只看了 `if`，沒看 return。
    # ⚠ 仍然是**純函式**：`network_name()` 只是字串組裝，不碰 docker。網路要真的存在是
    #   `create()` 的責任（它在建容器之前 `ensure_network`）。
    kwargs["network"] = user_proxy.network_name(user_id)

    if config.ENTRYPOINT is not None:  # escape hatch
        kwargs["entrypoint"] = config.ENTRYPOINT
        if config.COMMAND:
            kwargs["command"] = config.COMMAND
        kwargs["volumes"] = volumes
        return kwargs

    # --- 正常路徑：走 entrypoint.sh ---
    # bind-mount repo 的 entrypoint.sh，保證 env-skip 邏輯一定在（免每次 rebuild image）。
    # 存在性用 *_SELF（控制平面自己讀得到的），掛載用 host 路徑（daemon 解讀）——控制平面
    # 容器化後兩者不同，混用會靜默略過掛載或掛出空目錄（ADR 0009）。
    if os.path.isfile(config.ENTRYPOINT_SH_SELF):
        volumes[config.ENTRYPOINT_SH] = {"bind": "/usr/local/bin/entrypoint.sh", "mode": "ro"}

    # init-firewall.sh 同理：改政策不必重新 build image。
    # ⚠ **一定要 :ro**。sudoers 白名單的是**路徑**（`nathan ALL=(root) NOPASSWD:
    #   /usr/local/bin/init-firewall.sh`），所以那個路徑上的內容就是 root 會執行的程式碼
    #   ——可寫等於把 root 交出去。
    if os.path.isfile(config.INIT_FIREWALL_SH_SELF):
        volumes[config.INIT_FIREWALL_SH] = {"bind": config.INIT_FIREWALL_BIND, "mode": "ro"}

    # semgrep-rules（A4 SAST 軌道）：比照 run script 以 :ro 共用掛入（規則庫沒有 per-user
    # 的意義）。判準也與 run script 相同——要有 `.git` 才算真的 clone：compose/daemon 在
    # 來源缺席時會以 root 建出**空目錄**頂替，只驗 isdir 會把那個空殼掛進去、看起來像掛了
    # 其實沒有規則。不在（或只是空殼）→ 不掛，skill 的 A4 gate 不過、自動跳過（優雅降級）。
    # 準備方式：在 host 上 `git clone` 一份規則庫到 `$HOME/semgrep-rules`（或以
    # `CLAUDE_PTY_SEMGREP_RULES` / `NCR_OPENGREP_RULES` 指到別處）。
    # 存在性查 *_SELF、掛載用 host 路徑（ADR 0009）。
    if os.path.isdir(os.path.join(config.SEMGREP_RULES_SELF, ".git")):
        volumes[config.SEMGREP_RULES_HOST] = {"bind": config.SEMGREP_RULES_BIND, "mode": "ro"}

    # ⚠ 這裡曾經有 `_symlink_overlays()`：把 host `~/.claude` 底下那些指向 repo 的 symlink
    #   逐一 :ro 疊回容器內同一個路徑，好讓 statusline 與 symlink 形式的 skill 在 session
    #   裡看得到。ADR 0014 之後 host 的 `~/.claude` 根本不進 session（狀態是 per-user 的
    #   全新空間），這件事沒有對象了。
    #
    #   順帶拆掉一顆地雷：那個做法要 runc 願意在一個 **dangling symlink** 上建 mountpoint，
    #   而新版 runc（openat2 + securejoin）已經收緊——run script 第 75–82 行記著同樣的三段
    #   在 2026-07-26 全部移除，症狀是**間歇性**起不來（`securejoin.OpenInRoot ... openat2:
    #   invalid argument`）。這邊之所以沒爆，只因為 mountpoint 本來就存在於掛進去的
    #   `~/.claude` 裡；改成 per-user 空目錄後那個前提就沒了。

    # 第一層：env 非互動答選單。
    #
    # ⚠ **名稱一律用 `NCR_*`，那是 entrypoint.sh 認得的前綴**（它是兩條路徑共用的
    #   SSOT，見 ADR 0006）。這裡曾經用自己的 `CLAUDE_PTY_*` 前綴，那等於要求
    #   entrypoint 為了網頁這條路徑再認一組同義的變數——多一組就多一個會漂的對照表，
    #   而漂掉的症狀是「選單沒被跳過、容器停在 read 等一個永遠不來的輸入」。
    # ⚠ **條件題要成對送**：`NCR_CAPTURE=1` 時 entrypoint 會接著問錄製範圍，沒帶
    #   `NCR_CAPTURE_SCOPE` 就會停在那道 read。所以下面兩者一起給、不可只給前者。
    # ⚠ **這裡刻意沒有 subagent 深度上限**（曾經送過 `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`）。
    #   那個 env 存在的理由是「兩條路徑行為一致」，而人自己開容器時沒有它——送了反而
    #   製造它當初要消除的差異。日後真要一個上限，**加在 entrypoint**（一個地方、兩條
    #   路徑都吃得到），不要在這裡重新發明。
    env: dict = {
        "NCR_NET": profile.network,
        "NCR_CAPTURE": "1" if profile.capture else "0",
        # 成對送：見上方警告。值域是 entrypoint 的 all|1|model|2；這裡固定「全錄」，
        # 與它的預設一致——網頁這條路徑沒有人可以回答那道題。
        "NCR_CAPTURE_SCOPE": "all",
        # 請 entrypoint 印出就緒標記（DRIVER_MARKER）。人自己開容器時不設它，畫面上
        # 就不會多出一行機器用的字——那條路徑的零偏差由 test_entrypoint_human_path 守。
        "NCR_MARK": "1",
        # mitmweb 的 UI 收回容器 loopback。
        # ⚠ **這條只對網頁開的 session 成立**：它們掛在共用的 session network 上，
        #   而那個 UI 顯示的是**未脫敏的即時流量**、token 又印在 `docker logs` 裡
        #   （控制平面讀得到 log，同網段的兄弟容器連得到 8081）。綁回 loopback 之後，
        #   token 拿到也沒用——要先進得了這顆容器。
        # ⚠ 人自己開容器時不設它（預設 0.0.0.0），run script 的 `-p` 才轉得進去；
        #   docker 的 port forwarding 連的是容器內的介面，綁 loopback 會讓 UI 打不開。
        "NCR_MITM_WEB_BIND": "127.0.0.1",
        # per-user 狀態空間（ADR 0014）。**這個 env 是整個機制的關鍵**：
        #   CLAUDE_CONFIG_DIR → transcript / settings / skills / .claude.json 全部改看
        #   這個目錄（實測：設了之後 host 的 ~/.claude 一次都不會被開）。不設的話
        #   .claude.json 會落在容器 writable layer，換一顆容器就沒了。
        "CLAUDE_CONFIG_DIR": config.CLAUDE_CONFIG_BIND,
    }

    env.update(_gitlab_env())

    # 登入憑證：這個人貼進來的 setup-token。**這裡只放路徑，不放值**——值由 create()
    # 在 create 與 start 之間用 `put_archive` 送進容器（見 config.SESSION_TOKEN_FILE
    # 的說明，以及 _put_cli_token）。不掛任何 host 憑證檔（模型欄位 cli_token_enc
    # 那段講了為什麼不留後路）。
    # create() 的 _guard_credentials 已經擋過「沒設」，這裡拿不到只剩競態（guard 之後
    # 才被清掉）——照樣什麼都不放，讓終端停在登入提示，那是誠實的失敗畫面。
    #
    # ⚠ 兩條路，per-session 選（見 config.TOKEN_DELIVERIES）：`fd` 這裡只放**路徑**，
    #   值由 create() 用 put_archive 送進去；`env` 是把值直接放進環境的退路。
    _token = auth_mod.cli_token(user_id)
    if _token:
        if profile.token_delivery == "env":
            env["CLAUDE_CODE_OAUTH_TOKEN"] = _token
        else:
            env["NCR_TOKEN_FILE"] = config.SESSION_TOKEN_FILE

    # 模型與思考深度：entrypoint.sh 把它翻成 `--model` / `--effort`，這裡只放進 env。
    env["NCR_MODEL"] = profile.model
    env["NCR_EFFORT"] = profile.effort

    # 第二層：docker 能力（env 給不了）。
    #
    # **網路：無條件指定成這個使用者自己那張**（ADR 0016）。四種 profile 組合都設，沒有
    # 例外——它是 session 的家，不是某個功能的配件。
    #
    # ⚠ 這裡曾經只在 `restricted` 或 `telemetry` 時設 network，於是 **unrestricted 且不送
    #   telemetry 的 session 落在 docker 預設 `bridge`**。那張網住著這台機器上每一顆沒指定
    #   網路的容器，不只是別人的 session——比它要取代的共用網路還糟。而且它是**沉默的**：
    #   容器起得來、網路也通，看不出自己在一張公共的網上（2026-08-07 盤點時發現，當時
    #   一條測試都沒蓋到這個組合）。所以這一行**不可以再退回條件式**。
    # ⚠ network 的指定**已經移到 escape hatch 之前**（見那裡的說明）——留在這裡的話走
    #   `CLAUDE_PTY_ENTRYPOINT` 的容器會落在預設 bridge（審查 F-004）。
    if profile.network == "restricted":
        kwargs["cap_add"] = ["NET_ADMIN"]  # init-firewall.sh 需要
    # ⚠ 這裡只看 profile.telemetry——是**純函式**，不探 jaeger。可達性的判斷與降級在
    #   create()：它探不到（或 jaeger 沒接上這個人的網路）就把傳進來的 run_profile 的
    #   telemetry 關掉，所以走到這裡時 telemetry=True 已經代表「真的送得到」。把探測放這裡
    #   會讓這支變成有 I/O 的函式，而 test_profile_mapping 正是靠它是純的、
    #   Profile(telemetry=True) 一定設 env。
    if profile.telemetry:
        env["NCR_OTEL"] = "1"
        env.update(_otel_env(sid))
    if profile.capture:
        # 存在性查 *_SELF、掛載用 host 路徑（同上，ADR 0009）
        if os.path.isdir(config.CLAUDE_MITM_SELF):  # redact addon 在才掛（否則 entrypoint fail-closed 跳過）
            volumes[config.CLAUDE_MITM_HOST] = {"bind": config.MITM_ADDON_BIND, "mode": "ro"}
        # mitmweb 的網頁密碼（ADR 0021）：由 SECRET_KEY 對 sid 導出，兩端各算各的算出同一串，
        # `/api/auth/mitm` 之後重算並交給 nginx 以 Bearer 注入——DB 一個欄位都不用加。
        #
        # ⚠ **只跟 capture 成對送。** capture 關著時 entrypoint 根本不會走到 start_capture，
        #   送了就是一封死信：沒有任何行為，卻讓「這個 env 代表什麼」多一種說法。
        #   test_profile_mapping 兩個方向都釘著（開著時在、關著時整份 env 裡沒有這個鍵）。
        # ⚠ 它與 `NCR_MITM_WEB_BIND` 是兩件事，不要合併：bind 是**每一場都送**的
        #   （網頁開的 session 一律把 UI 收回 loopback），password 只在錄製時有意義。
        env["NCR_MITM_WEB_PASSWORD"] = crypto.mitm_web_password(sid)
        # capture 的落盤目錄已由 user_mounts() 掛成 per-user（ADR 0014）——它裡面是**完整的
        # API 請求本文**（prompt 全文），比 transcript 更敏感，共用一個目錄是先前盤點時
        # 最容易漏掉的那一項。掛載本身無條件（不分 capture 開關），少一個條件分支。
        # mitmweb UI 不再由控制平面發布 host port（ADR 0008：ttyd/port 屬 on-demand view 範疇）；
        # 需要看 mitmweb 時經 container 內部或另行 port-forward。

    kwargs["volumes"] = volumes
    kwargs["environment"] = env
    return kwargs


def _gitlab_env() -> dict:
    """讓 session 裡的 git 與 API 呼叫自己走上代理（ADR 0016）。純函式，不碰 docker。

    部署者沒設 GitLab 主機時回空 dict——什麼都不注入，session 完全不知道有這回事。

    ⚠ **沒有 URL 改寫，per-user 代理在實務上等於不能用。** 每個人、每份既有 repo、每一段
      複製貼上的指令，寫的都是 `https://<你的 gitlab>/x/y.git`，而那個位址在 session 裡是
      **直接失敗的**（防火牆不放行直連 443，那正是設計要的）。沒有改寫的話，使用者得手動
      把每一個 remote 換成代理位址——而他第一次遇到的症狀是 `Failed to connect`，完全看不
      出要去改 URL。

    ⚠ 用 **`GIT_CONFIG_*` 環境變數**而不是寫 `~/.gitconfig`：後者要嘛動到兩條路徑共用的
      `entrypoint.sh`（人自己開容器那條會被牽連），要嘛落進 per-user 空間變成一份會跟著
      漂的檔案。env 只影響網頁開的 session，人的路徑一個字都不會變。

    ⚠ **不分 profile、也不看有沒有 PAT。** 沒有代理時，改寫的結果是「連不到代理」而不是
      「連不到 GitLab」——兩者都失敗，但前者的訊息裡有 `gitlab-proxy` 這個字，使用者一搜
      就找得到答案（去設定頁填 PAT）。

    ⚠ **SSH 的兩種寫法也要改寫，不是只有 https。** 網頁開的 session 裡 SSH agent 預設不掛
      （ADR 0011）、防火牆也不放行 22，所以 `git@host:group/repo.git` 原本是**必定失敗**
      的，而症狀（`Permission denied (publickey)`）完全指不到「該用 https」。

    ⚠ `insteadOf` 是**多值鍵**，同一個 key 可以給多個值；`GIT_CONFIG_KEY_n` 重複同一個
      key 名稱就是這個意思。

    ⚠ scp-like 的 `git@host:` 結尾**必須是冒號**，不是斜線：`git@host:group/repo.git`
      改寫後要成為 `<代理>/group/repo.git`。寫成 `git@host/` 不會有任何錯誤訊息，只是
      靜靜不改寫。

    ⚠ https 那條結尾的斜線**不可以拿掉**：沒有它就變成前綴比對，
      `https://<你的 gitlab>.evil.example/…` 會被改寫成走代理——冒牌主機的請求被導進去，
      而代理會替它蓋上真的 PAT。
    """
    if not config.gitlab_enabled():
        return {}
    base = config.PROXY_BASE_URL
    key = f"url.{base}/.insteadOf"
    env = {
        # 容器裡看到的 API base。呼叫端（curl、任何腳本）不必把 `gitlab-proxy:5678` 寫死。
        # ⚠ git **不吃**這個變數，它走的是下面那組 GIT_CONFIG；反過來 curl 也不吃 git 的
        #   設定。兩者各有一條路，這是刻意的。
        "NCR_GITLAB_API_BASE": base,
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": key,
        "GIT_CONFIG_VALUE_0": f"https://{config.GITLAB_HOST}/",
        "GIT_CONFIG_KEY_1": key,
        "GIT_CONFIG_VALUE_1": f"git@{config.GITLAB_SSH_HOST}:",
        "GIT_CONFIG_KEY_2": key,
        "GIT_CONFIG_VALUE_2": f"ssh://git@{config.GITLAB_SSH_HOST}/",
    }
    # ⚠ 已知的小落差：`dev-container/entrypoint.sh` 有一處把 alias 寫死在 `NO_PROXY` 裡
    #   （只錄模型 API 的那個錄製範圍）。改了 `CLAUDE_PTY_GITLAB_PROXY_ALIAS` 之後那一處
    #   不會跟著改，代理的流量會多繞一次 mitm。**只影響錄製時的路徑，不影響能不能通**，
    #   所以留著不動；真要改 alias 的人請一併看那一行。
    return env


def _otel_env(sid: str) -> dict:
    """OTEL export 到 Jaeger 的 env，逐項對齊 run script 的 TELEMETRY_ENV（僅換掉 review 專用 resource attr）。
    entrypoint.sh 的 telemetry 選單僅在 OTEL_EXPORTER_OTLP_ENDPOINT 有值時出現，故僅 telemetry 開時才設。
    ⚠ CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1 是啟用「trace」（而非只有 metrics）的開關——缺它 claude 照跑
      照打 API 卻不吐 trace（2026-07-24 live 驗證踩到）。與 run script TELEMETRY_ENV 逐項同步，勿再抄子集。"""
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
        "OTEL_EXPORTER_OTLP_ENDPOINT": config.OTEL_ENDPOINT,
        "OTEL_LOG_TOOL_DETAILS": "1",
        "OTEL_RESOURCE_ATTRIBUTES": f"host.env=claude-pty,session.id={sid}",
    }


def _as_bool(v: object, default: bool) -> bool:
    """穩健布林解析：None→default，bool 原樣，字串走白名單（"1"/"true"/"yes"/"on"→True，其餘一律
    False，含亂碼），其餘型別 bool(v)。避免 bool("false")==True 的坑。"""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)
