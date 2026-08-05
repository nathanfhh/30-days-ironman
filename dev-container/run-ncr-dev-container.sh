#!/usr/bin/env bash
# 啟動 ncr-dev-container 的 wrapper：解決三件事——CLI 憑證怎麼進容器、
# git 的 SSH 憑證怎麼進容器、Opengrep 規則怎麼進容器。
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
RULES_DIR="$HOME/Projects/semgrep-rules"
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
fi

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
    #   ssh-add -l：0=有金鑰，1=連得上但空的，2=連不上。
    if ssh-add -l >/dev/null 2>&1; then
        echo "🔐 SSH：轉發 host 現有的 agent（${SSH_AUTH_SOCK}）"
    else
        echo "⚠️  SSH：host 的 agent 裡沒有任何金鑰（${SSH_AUTH_SOCK}），容器內走 SSH 的 git 操作會失敗。"
        # ⚠ 這裡不舉例任何檔名。上面 ssh-add 那段的理由同樣適用於**訊息**：
        #   寫 id_ed25519 會讓只有 id_rsa 的人照抄後失敗，反之亦然。
        #   不帶參數的 ssh-add 本來就會載入預設的那幾把，那才是正確的通用指令。
        echo "   先在 host 執行 ssh-add（不必指定檔名，會載入預設金鑰）再重跑本腳本。"
    fi
fi

# socket 掛載。⚠ 不能加 :ro——連 unix socket 需要寫權限，唯讀會 EACCES，掛了等於沒掛。
# ⚠ 路徑不能寫死：systemd/gnome-keyring 起的在 /run/user/<uid>/…，
#   ssh-agent -s 起的在 /tmp/ssh-XXXX/agent.<pid>，每次都不一樣，只能靠 $SSH_AUTH_SOCK。
if [ -z "${NCR_NO_SSH_AGENT:-}" ] && [ -S "${SSH_AUTH_SOCK:-}" ]; then
    RUN_MOUNTS+=(-v "$SSH_AUTH_SOCK":/ssh/ssh_sock)
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
    echo "🔌 已接上 gitlab-proxy network（容器內可用 http://gitlab-proxy:5678）"
fi

# 把「現在所在的資料夾」掛進容器的工作目錄：在要審查的專案根目錄執行本腳本。
#（陣列展開用 ${arr[@]+...} 寫法：macOS 內建 bash 3.2 在 set -u 下，空陣列直接展開會炸）
#
# --cap-add=NET_ADMIN：entrypoint 要套 iptables 規則。只給這一個 capability，不是
# --privileged——差別是後者連掛載、載入核心模組、存取任意裝置都一起送出去。
# 選「完全開放」時這個 capability 用不到，但它是啟動參數、不能事後才加，所以一律帶著。
docker run --rm -it \
    --cap-add=NET_ADMIN \
    ${RUN_ENV[@]+"${RUN_ENV[@]}"} \
    ${RUN_MOUNTS[@]+"${RUN_MOUNTS[@]}"} \
    ${RUN_OPTS[@]+"${RUN_OPTS[@]}"} \
    -v ~/.claude:/home/nathan/.claude \
    -v ~/.claude.json:/home/nathan/.claude.json \
    -v "$PWD":/home/nathan/code-review \
    "$IMAGE" "$@"
