#!/bin/bash
# 建 dev-container image，並且**替你把 uid 那個 build-arg 帶對**。
#
# 為什麼需要這支：`NCR_UID` 在 Linux 上實質只有一個合理值（`id -u`），但
# **Dockerfile 自己算不出來**——build 跑在 daemon 那一側，`RUN` 裡的 `id -u` 是 build
# 容器的 root（實測回 0），host 的環境變數也不會進去。知道答案的是 shell，不是
# Dockerfile，所以那個值一定得在外面展開、從外面遞進去。
#
# 而直接 `docker build` **不會失敗**，只會在 Linux 上安靜地給你 1001——症狀要等到開場
# 之後才出現（每一場撞 onboarding 對話、終端停在登入提示、restricted 卡滿逾時），
# 而且沒有一個看起來像 uid 問題。所以把正確的做法變成預設，比寫在文件裡可靠。
#
#   ./build.sh                                   # 就這樣
#   ./build.sh --build-arg GITLAB_SSH_HOST=…     # 其餘參數原樣透傳
#   ./build.sh --load                            # 若 active builder 是 docker-container，要顯式載回本機 daemon
#   ./build.sh --builder extdns --load          # daemon 的 DNS 是純內網時（這台就是）：換能出外網的 builder 再載回
#   NCR_IMAGE=my-tag ./build.sh                  # 換 tag
#
# 完整推導見 claude-pty/docs/adr/0017-uid-alignment.md。
#
# Note: image 會不會出現在 `docker images`，取決於目前使用的 builder driver，不是這支 script
# 本身。若 active buildx builder 是 `docker` driver，build 完通常會直接進本機 Docker image
# store；若是 `docker-container` driver（常見於自建 buildx builder），產物預設只留在 builder
# cache，需要額外帶 `--load`（載回本機 daemon）或 `--push`。
set -euo pipefail
cd "$(dirname "$0")"

IMAGE="${NCR_IMAGE:-ncr-dev-container}"
ARGS=()

# ⚠ 只在 host 是 Linux 時帶。macOS / Windows 的 Docker Desktop 對 bind mount 做 uid
#   對映（實測：同一個 host 目錄，在 uid 1001 的容器裡顯示 owner 1001、在 1000 的容器裡
#   顯示 owner 1000，兩邊都可寫），跟著設只會讓 image 偏離預設卻沒有任何好處。
if [ "$(uname -s)" = "Linux" ]; then
    UID_VAL="$(id -u)"
    ARGS+=(--build-arg "NCR_UID=${UID_VAL}")
    echo "🔢 host 是 Linux → NCR_UID=${UID_VAL}（＝你的 id -u）"
    echo "   ⚠ deploy/.env 的 APP_UID 也要是 ${UID_VAL}，兩邊對不上控制平面會擋下來。"
else
    echo "🔢 host 是 $(uname -s) → 不帶 NCR_UID（Docker Desktop 會做 uid 對映，用預設即可）"
fi

set -x
docker build "${ARGS[@]+"${ARGS[@]}"}" "$@" -t "${IMAGE}" .
