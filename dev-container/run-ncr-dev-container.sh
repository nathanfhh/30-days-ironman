#!/usr/bin/env bash
# 啟動 ncr-dev-container 的 wrapper：解決四件事——CLI 憑證怎麼進容器、
# git 的 SSH 憑證怎麼進容器、Opengrep 規則怎麼進容器，以及（選配）
# 偵測到 Jaeger 在跑時，把這場審查的 telemetry 錄下來。
#
# 憑證來源優先序：
#   1. CLAUDE_CODE_OAUTH_TOKEN 環境變數（設了就直接透傳進容器，不碰 Keychain）
#   2. macOS：從 Keychain 解出 OAuth 憑證，寫成 ~/.claude/.credentials.json
#      隨 ~/.claude mount 進容器（Linux 版 Claude Code 認得的檔案位置）
#   3. Linux host：~/.claude/.credentials.json 本來就存在（host 登入過即有）
# 三者皆無 → 退出，不啟動一個註定登不進去的容器。
set -euo pipefail

IMAGE=ncr-dev-container
RUN_ENV=()
RUN_MOUNTS=()
RUN_OPTS=()
CRED_FILE=""
STARTED_AGENT=0

# 單一 cleanup。⚠ bash 的 trap 是**覆蓋**不是疊加：同一個訊號註冊第二次，第一個就沒了。
# 憑證檔與 ssh-agent 兩件善後必須寫在同一個函式裡，分開註冊會讓先註冊的那件默默不執行。
cleanup() {
    [ -n "$CRED_FILE" ] && rm -f "$CRED_FILE"
    [ "$STARTED_AGENT" = "1" ] && ssh-agent -k >/dev/null 2>&1
    return 0
}
trap cleanup EXIT

if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    # 優先序 1：token 環境變數直接透傳（-e 不帶值 = 取用 host 目前的值）
    RUN_ENV+=(-e CLAUDE_CODE_OAUTH_TOKEN)
    echo "🔑 憑證來源：CLAUDE_CODE_OAUTH_TOKEN 環境變數"
elif [ "$(uname)" = "Darwin" ]; then
    # 優先序 2：macOS 把憑證鎖在 Keychain，解出成 Linux 版認得的檔案
    #（第一次執行會跳出 Keychain 授權視窗，按「允許」）
    # 目錄先建起來：全新的 host 可能還沒有 ~/.claude，重導向會直接失敗，
    # 訊息卻是「Keychain 沒有憑證」——把人指到完全錯的方向。
    mkdir -p ~/.claude
    if security find-generic-password -s "Claude Code-credentials" -w \
         > ~/.claude/.credentials.json 2>/dev/null \
       && [ -s ~/.claude/.credentials.json ]; then
        chmod 600 ~/.claude/.credentials.json
        # 憑證檔只是給容器用的明文複本，退出時由 cleanup() 刪掉（macOS 本體仍用 Keychain）
        CRED_FILE=~/.claude/.credentials.json
        echo "🔑 憑證來源：macOS Keychain（已解出至 ~/.claude/.credentials.json，退出時自動刪除）"
    else
        rm -f ~/.claude/.credentials.json
        echo "❌ Keychain 沒有 Claude Code 憑證，也沒設定 CLAUDE_CODE_OAUTH_TOKEN。" >&2
        echo "   先在 host 登入一次 claude，或 export CLAUDE_CODE_OAUTH_TOKEN 再執行。" >&2
        exit 1
    fi
else
    # 優先序 3：Linux host 的憑證檔本來就落地，只確認它在
    if [ ! -s ~/.claude/.credentials.json ]; then
        echo "❌ 找不到 ~/.claude/.credentials.json，也沒設定 CLAUDE_CODE_OAUTH_TOKEN。" >&2
        echo "   先在 host 登入一次 claude，或 export CLAUDE_CODE_OAUTH_TOKEN 再執行。" >&2
        exit 1
    fi
    echo "🔑 憑證來源：Linux host 的 ~/.claude/.credentials.json"
fi

# Opengrep 規則（A4 軌道）：opengrep binary 不內建規則，從 host 的 semgrep-rules clone 餵。
# 啟動前 best-effort 更新（離線或 pull 失敗就沿用現有版本，不擋啟動）、唯讀 mount 進容器。
# clone 不存在 → 警告後照常啟動，A4 軌道本場無規則可用。
# 路徑跟著 install.sh 對使用者宣告的 NCR_OPENGREP_RULES 走（預設 $HOME/semgrep-rules）：
# 兩邊各講一個路徑的話，照著 install.sh 設好規則的人會在這裡被判定成「找不到」。
RULES_DIR="${NCR_OPENGREP_RULES:-$HOME/semgrep-rules}"
if [ -d "$RULES_DIR/.git" ]; then
    git -C "$RULES_DIR" pull --ff-only 2>/dev/null || echo "⚠️  semgrep-rules 更新失敗，沿用現有版本"
    RUN_MOUNTS+=(-v "$RULES_DIR":/home/nathan/semgrep-rules:ro)
else
    # ⚠ 變數後面接全形標點時一定要加大括號。macOS 內建 bash 3.2 在 LC_CTYPE="UTF-8"
    #（Terminal 的常見預設，而且不是合法 locale 名）下，會把全形字元的首位元組當成
    #   識別字的一部分，`$RULES_DIR，` 被讀成變數 `RULES_DIR\xEF`——set -u 直接 unbound。
    #   ASCII 標點沒這問題，所以這個 bug 只在中文訊息裡出現。
    echo "⚠️  找不到 ${RULES_DIR}，本場 Opengrep（A4）無規則可用。"
    # --depth 1：只掃描用不到歷史，而 semgrep-rules 的歷史比工作目錄本身大得多。
    # 上面那行 pull --ff-only 在 shallow clone 上照樣可以更新，不會被迫 unshallow。
    echo "   取得規則：git clone --depth 1 https://github.com/semgrep/semgrep-rules.git ${RULES_DIR}"
    echo "   規則已經在別的地方：export NCR_OPENGREP_RULES=<你的 semgrep-rules 路徑> 再重跑。"
fi

# Trivy DB（A2 軌道）：trivy binary 不內建弱點資料庫，第一次掃描才去 ghcr.io 抓
#（下載約 60MB，解開後落地超過 1GB）。DB 跟 semgrep-rules 一樣由 host 供給，理由有二：
#   1. 容器用完即丟——每場重抓、重解一次是純浪費
#   2. 限制模式的白名單沒有 ghcr.io——牆內抓不到，A2 軌道會整場空轉
# 做法：啟動前在**牆外**更新一次（獨立的一次性容器，entrypoint 被繞過、不套防火牆、
# 跑完即棄），再把 cache 目錄 mount 進審查容器。更新失敗（離線、逾時）→ 沿用既有 DB
# 並警告；連既有 DB 都沒有 → 警告，A2 軌道本場會依 skill 的降級規則處理（skip + 揭露）。
#
# 用獨立的 cache、不共用 host 自己的 ~/.cache/trivy：host 若也裝著 trivy，兩邊版本不同時
# DB schema 可能不相容，隔離開來誰也不會弄壞誰。
#
# ⚠ 它是 **named volume 不是 host 目錄**（ADR 0018）。差別在擁有權：volume 首次掛載且
#   為空時，docker 用 image 裡 /home/nathan/.cache/trivy 的內容與擁有者初始化它，所以
#   host 帳號的 uid 完全不進場。用 host 目錄的話，那個目錄屬於**你**（`id -u`），而寫它
#   的是容器裡的 nathan——兩個號碼在 Linux 上不會自動一樣（見 ADR 0017）。
# ⚠ 名稱固定，claude-pty 的 compose 也掛同一顆，兩條路徑共用那 ~1.2 GB。改名等於分家。
TRIVY_CACHE_VOLUME="ncr-trivy-cache"
# --entrypoint bash：繞過 image 的互動式啟動選單，只跑更新就退出。
# timeout 給硬上限——網路半死不活時，不讓「更新 DB」變成「卡住啟動」。
if docker run --rm --entrypoint bash \
    -v "$TRIVY_CACHE_VOLUME":/home/nathan/.cache/trivy \
    "$IMAGE" -c 'timeout -k 10 180 trivy image --download-db-only' >/dev/null 2>&1; then
    echo "🗃️  Trivy DB 已更新（volume ${TRIVY_CACHE_VOLUME}）"
elif docker run --rm --entrypoint bash \
    -v "$TRIVY_CACHE_VOLUME":/home/nathan/.cache/trivy \
    "$IMAGE" -c '[ -s /home/nathan/.cache/trivy/db/trivy.db ]' >/dev/null 2>&1; then
    # ⚠ 「有沒有既有 DB」只能**進容器裡問**——volume 的內容 host 上看不到（它在
    #   /var/lib/docker 底下，macOS 更是在 VM 裡）。這裡曾經是 `[ -s "$DIR/db/trivy.db" ]`，
    #   改成 volume 之後那個判斷會永遠是 false，於是「有舊 DB」被誤報成「完全沒有」。
    echo "⚠️  Trivy DB 更新失敗，沿用既有版本"
else
    echo "⚠️  Trivy DB 更新失敗且沒有既有 cache，本場 Trivy（A2）無 DB 可用。"
fi
# 不加 :ro——trivy 除了 DB 還會往同一個 cache 寫掃描的分析結果。DB 的完整性不靠唯讀，
# 靠「更新在牆外做、審查容器在牆內連不到 ghcr.io」這個順序保證。
RUN_MOUNTS+=(-v "$TRIVY_CACHE_VOLUME":/home/nathan/.cache/trivy)

# git 的 SSH 憑證：轉發 host 的 ssh-agent socket，而不是把 ~/.ssh 掛進去。
# 差別是「能力」與「秘密」——容器只能請 agent 簽章，拿不到私鑰本體；
# 掛目錄則是把長效私鑰交出去（容器內同 uid、有 shell ＝ 可讀可帶走）。
#
# ⚠ 爆炸半徑跟 CLI 憑證不同：CLI 憑證只能拿去呼叫模型，SSH agent 能以你的身分
#   認證**所有信任那把 key 的主機**——內網 git、正式機、跳板機。而容器篩不掉
#   agent 裡的任何一把 key。要限縮請在 host 端另起一個只加了受限 key 的 agent，
#   把 SSH_AUTH_SOCK 指過去再執行本腳本。
#
# 不想轉發：NCR_NO_SSH_AGENT=1 ./run-ncr-dev-container.sh
if [ -n "${NCR_NO_SSH_AGENT:-}" ]; then
    echo "🔒 已停用 SSH agent 轉發（NCR_NO_SSH_AGENT）；容器內走 SSH 的 git 操作會失敗。"
elif [ -z "${SSH_AUTH_SOCK:-}" ]; then
    # host 上沒有 agent，起一個暫時的。⚠ 只有「自己起的」才由自己收掉，
    # 見 cleanup()：host 本來就有 agent 時去 ssh-agent -k，殺掉的是使用者原本那個，
    # 他之後每一個終端機的 git 都會壞，而且不會知道是誰弄的。
    if eval "$(ssh-agent -s)" >/dev/null 2>&1; then
        STARTED_AGENT=1
        # 不指定檔名：ssh-add 會載入預設的那幾把（id_ed25519 / id_ecdsa / id_rsa …），
        # 寫死 id_rsa 會讓只有 ed25519 的人（現在的預設）安靜地拿不到金鑰。
        # ⚠ set -e：ssh-add 失敗不該讓整個腳本死掉，SSH 只是選配。
        if ssh-add >/dev/null 2>&1; then
            echo "🔐 SSH：host 沒有 agent，已臨時起一個並載入預設金鑰（退出時關閉）"
        else
            echo "⚠️  SSH：ssh-add 沒有載入任何金鑰（可能沒有預設金鑰，或需要 passphrase）。"
            echo "   容器內走 SSH 的 git 操作會失敗；先在 host 執行 ssh-add 再重跑。"
        fi
    else
        echo "⚠️  SSH：起不了 ssh-agent，本場不轉發。"
    fi
else
    # 轉發現成的 agent 之前先確認它裡面有東西。macOS 的 launchd agent 是**永遠都在**的
    # ——SSH_AUTH_SOCK 一定有值，socket 一定連得上，但沒 ssh-add 過就是空的。
    # 只看「有沒有 agent」會讓這裡印出 🔐 成功訊息，然後容器裡的 git 才發現沒有金鑰。
    #
    # ⚠ 只警告，不自動 ssh-add：這個 agent 是使用者的，載入的金鑰在腳本退出後還留著。
    #   而且上面那段「另起一個只加受限 key 的 agent」正是預期用法之一，
    #   自動補上預設金鑰會安靜地破壞掉那個限縮。（自己起的 agent 才由自己載入金鑰。）
    #   ssh-add -l 的退出碼有三種，`ssh-add(1)` 明定：0=有金鑰，1=連得上但袋子是空的，
    #   2=**連不到 agent**。
    # ⚠ 把 1 與 2 併成同一句是錯的，而且錯得很難查：socket 失效、權限不對、
    #   SSH_AUTH_SOCK 指到一個已經消失的路徑——這些全都是 2，卻會被告知「袋子是空的、
    #   去跑 ssh-add」。照做之後 ssh-add 也連不上，於是使用者卡在一個與真正原因無關的指令上。
    #   （這一行的上一版註解就寫著三種碼的差別，程式卻只分了兩類。）
    # ⚠ **不可以寫成 `_ssh_add_err="$(…)"; _ssh_add_rc=$?`。** 賦值的結束碼會繼承
    #   command substitution，在 `set -e` 下 ssh-add 回 1／2 就地退出——下面三條分支
    #   一條都走不到。而且**不只是分支失效：整個 wrapper 死在這一行，容器根本不會啟動**，
    #   受害的正好是這幾句訊息要幫的那些人（袋子是空的、socket 失效）。
    #   實測（fake ssh-add）：rc=1 → 腳本結束碼 1、一個字都沒印；rc=2 → 結束碼 2。
    # ⚠ 也不要改用 `set +e … set -e` 把 errexit 關掉再開：那會讓中間每一個指令一起失去
    #   保護，為了一行的例外付整段的代價。`|| _ssh_add_rc=$?` 只豁免這一個指令。
    _ssh_add_rc=0
    _ssh_add_err="$(ssh-add -l 2>&1 >/dev/null)" || _ssh_add_rc=$?
    if [ "$_ssh_add_rc" -eq 0 ]; then
        echo "🔐 SSH：轉發 host 現有的 agent（${SSH_AUTH_SOCK}）"
    elif [ "$_ssh_add_rc" -eq 1 ]; then
        echo "⚠️  SSH：host 的 agent 連得上，但裡面沒有任何金鑰（${SSH_AUTH_SOCK}）。"
        # ⚠ 這裡不舉例任何檔名。上面 ssh-add 那段的理由同樣適用於**訊息**：
        #   寫 id_ed25519 會讓只有 id_rsa 的人照抄後失敗，反之亦然。
        #   不帶參數的 ssh-add 本來就會載入預設的那幾把，那才是正確的通用指令。
        echo "   先在 host 執行 ssh-add（不必指定檔名，會載入預設金鑰）再重跑本腳本。"
    else
        # 2（或其他非預期的碼）＝**連不到 agent**，跟「有沒有金鑰」無關。
        # 原始錯誤訊息一定要帶出來：那句話才指得到真正的原因。
        echo "⚠️  SSH：連不到 host 的 agent（${SSH_AUTH_SOCK}），rc=${_ssh_add_rc}。"
        echo "   ssh-add 說：${_ssh_add_err:-（沒有訊息）}"
        echo "   常見原因：SSH_AUTH_SOCK 指到已經消失的路徑（重開機／換登入 session）、"
        echo "   socket 權限不對，或那個 agent 已經結束。重新登入或另起一個 agent 再試。"
    fi
fi

# socket 掛載。**加 :ro**（2026-08-22 起）。
#
# ⚠ 這裡曾經寫著「不能加 :ro，連 unix socket 需要寫權限，唯讀會 EACCES，掛了等於沒掛」。
#   那是錯的，已實測推翻：Docker :ro 設的是**掛載層**的 MNT_READONLY，kernel 只在走
#   mnt_want_write() 的寫入路徑（create / unlink / open-for-write / chmod）檢查它並回
#   EROFS；而 socket 的 connect 走 unix_find_bsd() -> path_permission(MAY_WRITE)，那是
#   inode mode bits 的檢查，整條路徑不經過 mnt_want_write。所以 :ro 的掛載上 socket
#   照樣 connect/send/recv。反例可重跑：claude-pty/tests/test_ro_socket_mount.py。
#
# 加 :ro 擋掉的是這個：**bind mount 與 host 共用同一個 inode**，所以容器裡對這顆 socket
#   下 chmod／chown 改到的是 host 那一顆。原生 Linux（真 bind mount）上，容器裡的 agent
#   把它的權限改壞，症狀是**使用者其他終端機的 ssh 全部失效**，而且完全指不到容器。
#   :ro 讓那條路回 EROFS。
#   ⚠ macOS 的 Docker Desktop 不受影響（它換上自己的代理節點，碰不到 host 那顆），
#     但那不是不加的理由：同一份腳本兩種 host 都要跑。
#
# ⚠ :ro **不是** agent 的安全邊界：列舉金鑰、簽章、轉送一項都擋不住。它擋的只有
#   「弄壞 host 上那顆 socket」。要限縮 agent 能力只能在 host 端另起一個受限的 agent。
# ⚠ 路徑不能寫死：systemd/gnome-keyring 起的在 /run/user/<uid>/…，
#   ssh-agent -s 起的在 /tmp/ssh-XXXX/agent.<pid>，每次都不一樣，只能靠 $SSH_AUTH_SOCK。
if [ -z "${NCR_NO_SSH_AGENT:-}" ] && [ -S "${SSH_AUTH_SOCK:-}" ]; then
    RUN_MOUNTS+=(-v "$SSH_AUTH_SOCK":/ssh/ssh_sock:ro)
    # ⚠ socket 是 bind mount 裡的特例。目錄與一般檔案（~/.claude 那些）Docker Desktop 會
    #   把擁有者對映成容器內的 nathan，但 unix socket 過不了 virtiofs——Docker Desktop 改成
    #   在容器裡放一個自己代理的 socket 節點，而那個節點是 root:root 0660。容器跑 uid 1001，
    #   結果是掛得好好的卻 `Error connecting to agent: Permission denied`。
    #   補 gid 0 讓 group 的 rw 生效即可，不必為了 chown 讓整個容器從 root 起。
    #
    #   gid 0 不是 root：uid 仍是 1001，不帶任何 capability，也拿不到 setuid 執行檔
    #   （su/passwd 要密碼）。實測這個 image：group root 可寫但 other 不可寫的檔案 **0 個**，
    #   多讀到的只有 3 個 dpkg/apt 空鎖檔。代價實質為零。
    #   ⚠ 這個結論綁在 base image 上。有些 image（OpenShift 慣例）會把 /etc 或應用目錄設成
    #     group root 可寫，那裡加 gid 0 就等於送出寫入權。換 base image 要重驗：
    #       docker run --rm --entrypoint bash $IMAGE -c \
    #         'find / -xdev -group 0 -perm -g+w ! -perm -o+w ! -type l'
    #   ⚠ 原生 Linux Docker 不適用這條：那裡 socket 帶的是 host 自己的 uid 且通常 0600，
    #     uid 對不上時 group 補不回來。所以下面把它跳過——但只在「確定是原生」時跳過。
    #
    # 判斷的方向很重要。兩種錯法的代價不對等：
    #   該加沒加 → SSH 靜默不通（容器照樣起來，只有 git over SSH 會爆，難歸因）
    #   不該加卻加 → 多一個 gid 0，實測可寫檔案 0 個
    # 所以判不出來時一律**照加**（fail-open）。
    #
    # ⚠ 不能只用 `uname = Darwin` 當判準。「macOS ⇒ VM 型 Docker」成立（macOS 跑不了原生
    #   Linux container），但這個 gate 用到的是它的逆命題「非 macOS ⇒ 原生」，而那句是假的：
    #   WSL2 + Docker Desktop（uname 回 Linux）與 Docker Desktop for Linux 都是 VM 型，
    #   socket 一樣是 root:root 0660。用 uname 當唯一判準會把這兩種人推進靜默失敗。
    #
    # 所以 uname 只當**快路徑**：macOS 直接加，不多花一次 docker info；
    # 只有落在 Linux 時才去問 daemon 是誰。
    SKIP_GID0=0
    if [ "$(uname)" = "Linux" ]; then
        DAEMON_OS="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || true)"
        # ⚠ 必須是「確定認出一個非 Docker Desktop 的 daemon」才跳過。docker info 失敗會回
        #   空字串（daemon 還沒起、權限不足、逾時），那是「問不到」不是「原生 Linux」——
        #   把空字串當成原生就會在 fail-open 的反方向失敗，正是這段要避免的事。
        case "$DAEMON_OS" in
            "" | *"Docker Desktop"*) ;;
            # 原生 Linux Docker：socket 是 host uid，補 group 沒有用，不加。
            # ⚠ 這不代表那裡就通了——uid 對不上時失敗點還在，只是要等容器裡 git 才看得出來。
            *) SKIP_GID0=1 ;;
        esac
    fi
    [ "$SKIP_GID0" = "1" ] || RUN_OPTS+=(--group-add 0)
fi

# known_hosts 唯讀掛進去。容器裡第一次 git over SSH 會停在 host key 驗證的互動提示，
# 而這是個非互動環境——結果不是問你要不要信任，是直接失敗。
# 用 host 現成的檔案，不把任何主機的 public key 烘進 image。
if [ -f ~/.ssh/known_hosts ]; then
    RUN_MOUNTS+=(-v ~/.ssh/known_hosts:/home/nathan/.ssh/known_hosts:ro)
else
    echo "⚠️  找不到 ~/.ssh/known_hosts，容器內第一次連 GitLab 會因 host key 未知而失敗。"
    echo "   先在 host 執行一次：ssh-keyscan -t rsa,ed25519 <your-gitlab-host> >> ~/.ssh/known_hosts"
fi

# gitlab-proxy：有跑就把容器接上那張 network。
#
# ⚠ 不接的話，防火牆的「放行直連網段」涵蓋不到它。容器在預設 bridge（172.17.0.0/16），
#   proxy 在自己那張 network（172.19.x），封包走 default route → 不在直連網段清單裡 →
#   落到 init-firewall.sh 最後那條 REJECT。症狀是 proxy 明明在跑卻連不到，
#   而 `docker ps` 看起來一切正常。
#   實測：接上之後直連網段變成 172.19.0.0/16，IP 與 hostname 兩種都通，
#   而 example.com 仍然被擋——牆沒有因此變鬆。
#
# ⚠ 必須先檢查 network 在不在：docker run 對不存在的 network 會直接報錯退出。
#   沒跑 gitlab-proxy 的人（多數讀者）不該因為這行而啟動不了容器。
if docker network inspect gitlab-proxy >/dev/null 2>&1; then
    RUN_OPTS+=(--network gitlab-proxy)
    # 告訴 skill 走 proxy：gitlab_api.py 認這個變數，API base 換成 proxy、
    # PRIVATE-TOKEN 由 proxy 端注入——所以容器內不需要（也刻意不轉發）GITLAB_TOKEN。
    # 沒有這個變數的話，限制模式下 skill 會直連 GitLab 的 443 而被防火牆 REJECT。
    RUN_ENV+=(-e NCR_GITLAB_API_BASE=http://gitlab-proxy:5678)
    echo "🔌 已接上 gitlab-proxy network（API 走 http://gitlab-proxy:5678，token 由 proxy 注入）"
fi

# entrypoint 的網路能力選單支援 NCR_NET 跳過、telemetry 選單支援 NCR_OTEL 跳過
#（CI / 腳本用），但 env 不會自己穿過 docker run——host 有設就轉發進去，
# 沒設就維持互動選單。
[ -n "${NCR_NET:-}" ] && RUN_ENV+=(-e NCR_NET)
[ -n "${NCR_OTEL:-}" ] && RUN_ENV+=(-e NCR_OTEL)

# skill 進容器的方式。install.sh 連進 ~/.claude/skills 的是 symlink，指向 host 上
# 這個 repo 的絕對路徑；~/.claude 掛進容器後那些 symlink 是斷的——目標只存在於 host。
# 解法不是改成複製（symlink 是「repo 改一行、下一場就吃到」的前提），而是把目標目錄
# 用**同一個絕對路徑**掛進容器：路徑一致，symlink 原地復活。agents/*.md 的 symlink
# 指進同一個目錄，跟著一起活。
# 只掛屬於本 repo 的目標：~/.claude/skills 裡可能還有使用者自己的其他 skill，
# 那些不是審查需要的東西，不該順手帶進被審環境。
# ⚠ 唯讀：skill 是規則本體，容器裡的 agent 不該能改寫自己要遵守的規則。
# ⚠ 但這道鎖只蓋到「symlink 指向的那個目錄」。symlink 本身、以及 ~/.claude 底下其餘的
#   東西（settings.json、agents/…）都在下面那個讀寫掛載裡——容器內改得動，而且會落回
#   host。也就是說：規則檔本身動不了，但「指到哪一份規則」動得了。這個邊界刻意不在這裡
#   補，由上層隔離（例如把整場跑在 pty 容器裡）承接；正式版的行為也是這樣，兩邊一致。
_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for _link in "$HOME"/.claude/skills/*; do
    [ -L "$_link" ] || continue
    _target="$(readlink "$_link")"
    case "$_target" in
        "$_REPO_ROOT"/*) [ -d "$_target" ] && RUN_MOUNTS+=(-v "$_target":"$_target":ro) ;;
    esac
done

# Telemetry → Jaeger（traces）。只在 jaeger 容器活著時開啟：OTLP 匯出本身是非同步、
# fail-open，設了沒人收也不會弄壞 claude，但 gating 免掉重試噪音，而且啟動時就把
# 「這場有沒有在錄」講清楚。
#
# ⚠ **Jaeger 不擁有也不借任何一張網（`network_mode: bridge`），要送 trace 的人負責把它
#   接過來**——見 opentelemetry/jaeger-compose.yaml 檔頭的網路規約。這條路徑送 trace 的容器待在
#   gitlab-proxy 那張網上（上面 --network 那段），所以這裡把 jaeger 也接上去，接完
#   容器內就能用 http://jaeger:4317 直達，防火牆不用動。
# ⚠ **一定要在 docker run 之前接。** 限制模式放行的是 entrypoint 起跑那一刻的直連網段
#   快照，容器起來之後才接的網路封包會被 REJECT，而且永遠不會好。
# ⚠ 已經接過就會回「already exists」，那是成功不是錯誤——所以吞掉輸出、看後面那道
#   驗證的結果，不看這一行的 exit code。
#
# 只送 traces：span 上就帶著 token 數，足夠做每角色歸因；metrics/logs 設 none，
# 不送 Jaeger 收不了的資料。
TELEMETRY_ENV=()
if [ "$(docker inspect -f '{{.State.Running}}' jaeger 2>/dev/null)" = "true" ]; then
    if docker network inspect gitlab-proxy >/dev/null 2>&1; then
        docker network connect gitlab-proxy jaeger >/dev/null 2>&1 || true
    fi
    # 真的接上了嗎。**問實際狀態，不看上面那行的 exit code**——它在「已經接過」時也會
    # 非零，而那是成功。接不上就走下面的 else，把原因講出來而不是靜靜不錄。
    if docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
           jaeger 2>/dev/null | grep -qw gitlab-proxy; then
        # Resource attributes：黏在該場所有 span 上的標籤，事後分析就按它們切。
        # skill.version 自動抓——每筆 trace 記著「哪一版 skill 產生的」，是前後比對的錨。
        # experiment 由使用者指定，一場標一組實驗代號：
        #   NCR_EXPERIMENT=before-xxx ./run-ncr-dev-container.sh
        # 版本讀容器實際掛進去的那份（~/.claude/skills/…，install.sh 連的 symlink）；
        # fallback 讀 repo 相對路徑，涵蓋還沒跑 install.sh 的 fresh checkout。
        # 兩處都沒有 → unknown，照樣開錄，只是版本標籤沒有值。
        SKILL_MD=""
        for _cand in \
            "$HOME/.claude/skills/nathan-code-review/SKILL.md" \
            "$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)/skills/nathan-code-review/SKILL.md"; do
            [ -f "$_cand" ] && { SKILL_MD="$_cand"; break; }
        done
        SKILL_VER="$(grep -m1 -E '^version:' "$SKILL_MD" 2>/dev/null | grep -oE '[0-9]{4}\.[0-9.]+' || echo unknown)"
        RES="skill.version=${SKILL_VER},experiment=${NCR_EXPERIMENT:-none},reviewer=${USER:-unknown},host.env=devcontainer"
        TELEMETRY_ENV=(
            -e CLAUDE_CODE_ENABLE_TELEMETRY=1
            # spans 要這個 beta 旗標才會發（2.1.222 實測：不開的話 Jaeger 一筆 trace 都收不到）
            -e CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
            -e OTEL_TRACES_EXPORTER=otlp
            -e OTEL_METRICS_EXPORTER=none
            -e OTEL_LOGS_EXPORTER=none
            -e OTEL_EXPORTER_OTLP_PROTOCOL=grpc
            -e OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317
            # 沒有這個，tool 呼叫的參數（含 Agent 派遣的 prompt 首行 [ncr-*] tag）不進 telemetry
            -e OTEL_LOG_TOOL_DETAILS=1
            -e "OTEL_RESOURCE_ATTRIBUTES=${RES}"
        )
        echo "📊 Telemetry 已配置 → Jaeger（jaeger:4317）· UI http://localhost:16686 · 送不送由啟動選單決定"
        echo "   resource: ${RES}"
    else
        echo "⚠️  jaeger 在跑，但接不到 gitlab-proxy network → 本場不錄。"
        echo "   這條路徑的容器待在 gitlab-proxy 那張網上，jaeger 必須也接上去才收得到。"
        echo "   先確認那張網在（沒有就 docker network create gitlab-proxy），再手動接："
        echo "   docker network connect gitlab-proxy jaeger"
    fi
else
    echo "ℹ️  Jaeger 未啟動 → 本場不錄 telemetry（要錄：docker compose -f opentelemetry/jaeger-compose.yaml up -d）"
fi

# 審查報告的 archive（workspace-paths.md 說它是 permanent 的）寫在 $HOME/ncr。
# 容器是 --rm 的：不掛出來，報告就跟著容器一起消失，re-review 的歷史對照也永遠
# 找不到上一輪——「永久」必須由 host 兌現，容器兌現不了。
mkdir -p "$HOME/ncr"
RUN_MOUNTS+=(-v "$HOME/ncr":/home/nathan/ncr)

# mitmproxy 的即時畫面：容器內固定 8081，host 那側動態挑一個沒被占用的。
# 固定 8081 的話，同時開兩個容器第二個就起不來。
#
# 這個 port 一律發布，即使這一場最後選了不錄——published port 是 docker run 的
# 啟動參數，事後加不上去，而要不要錄是進容器之後才問的。只綁 127.0.0.1，
# 沒開錄製時那個 port 後面沒有東西在聽。
MITM_WEB_HOST_PORT=8081
if command -v python3 >/dev/null 2>&1; then
    _picked=$(python3 - <<'PY'
import socket
for port in range(40000, 40101):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
PY
)
    if [ -n "$_picked" ]; then
        MITM_WEB_HOST_PORT="$_picked"
    else
        echo "⚠️  40000–40100 都被占用，mitmproxy 畫面沿用 8081（多開時可能撞號）"
    fi
else
    echo "⚠️  沒有 python3，mitmproxy 畫面沿用固定的 8081（多開時可能撞號）"
fi
RUN_OPTS+=(-p "127.0.0.1:${MITM_WEB_HOST_PORT}:8081")
RUN_ENV+=(-e "NCR_MITM_WEB_PORT=${MITM_WEB_HOST_PORT}")
# capture 檔的 host 路徑。容器印自己的 /home/nathan/... 沒有用——host 是 mac 的話
# 那個路徑根本不存在，看到也開不起來。同 UI 網址，一律印 host 視角。
RUN_ENV+=(-e "NCR_CAPTURE_HOST_DIR=${HOME}/ncr/mitm")
[ -n "${NCR_CAPTURE:-}" ] && RUN_ENV+=(-e "NCR_CAPTURE=${NCR_CAPTURE}")
# 錄製範圍也要能跳過選單。少轉發這一個，非互動環境會停在第二題等一個
# 永遠不會來的輸入——不是報錯，是安靜地掛在那裡。
[ -n "${NCR_CAPTURE_SCOPE:-}" ] && RUN_ENV+=(-e "NCR_CAPTURE_SCOPE=${NCR_CAPTURE_SCOPE}")

# 脫敏 addon 從**這支腳本所在的 repo** 掛進去，不能靠 $PWD——$PWD 掛的是「被審查的
# 那個專案」，而這支腳本是在那個專案的根目錄執行的。用 $PWD 找 addon，等於要求
# 每個被審查的專案自己帶一份，那當然找不到。
# 掛 :ro，而且是掛目錄不是烘進 image：改脫敏規則免 rebuild。
if [ -d "$_REPO_ROOT/mitm" ]; then
    RUN_MOUNTS+=(-v "$_REPO_ROOT/mitm":/home/nathan/ncr-mitm:ro)
    echo "🕵️  流量錄製可用（要不要錄由啟動選單決定）· 即時畫面 host port ${MITM_WEB_HOST_PORT}"
else
    echo "⚠️  找不到 ${_REPO_ROOT}/mitm，本場無法錄製流量（entrypoint 會 fail-closed 跳過）"
fi

# ~/.claude.json（CLI 的設定檔）不存在時先建成空檔。bind mount 的來源不存在時，
# Docker 會替你建一個**目錄**，之後 host 上的 claude 就再也讀不到自己的設定，
# 而且沒有人會告訴你是誰弄的。
[ -e ~/.claude.json ] || touch ~/.claude.json

# 把「現在所在的資料夾」掛進容器的工作目錄：在要審查的專案根目錄執行本腳本。
#（陣列展開用 ${arr[@]+...} 寫法：macOS 內建 bash 3.2 在 set -u 下，空陣列直接展開會炸）
#
# --cap-add=NET_ADMIN：entrypoint 要套 iptables 規則。只給這一個 capability，不是
# --privileged——差別是後者連掛載、載入核心模組、存取任意裝置都一起送出去。
# 選「完全開放」時這個 capability 用不到，但它是啟動參數、不能事後才加，所以一律帶著。
docker run --rm -it \
    --cap-add=NET_ADMIN \
    ${RUN_ENV[@]+"${RUN_ENV[@]}"} \
    ${TELEMETRY_ENV[@]+"${TELEMETRY_ENV[@]}"} \
    ${RUN_MOUNTS[@]+"${RUN_MOUNTS[@]}"} \
    ${RUN_OPTS[@]+"${RUN_OPTS[@]}"} \
    -v ~/.claude:/home/nathan/.claude \
    -v ~/.claude.json:/home/nathan/.claude.json \
    -v "$PWD":/home/nathan/code-review \
    "$IMAGE" "$@"
