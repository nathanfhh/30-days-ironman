# 供應鏈掃描：怎麼跑、這一輪掃出什麼、沒修的為什麼沒修

掃描日期 2026-08-23，Trivy 0.62.1。**原始 JSON 不進版控**（每次掃都會變、而且很大），
留下的是可重跑的指令與處置理由。

## 怎麼重跑

```bash
# 1. 檔案系統（lockfile）
trivy fs --scanners vuln --severity MEDIUM,HIGH,CRITICAL .

# 2. 兩顆實際出貨的 image。⚠ 掃 image，不是只掃 lockfile：
#    lockfile 綠不代表出貨的那顆綠，中間隔著 base image 的 OS 套件與各工具帶進來的東西。
cd dev-container && ./build.sh && cd ..
cd claude-pty && docker build -f deploy/Dockerfile -t claude-pty-control:scan . && cd ..
trivy image --scanners vuln --severity MEDIUM,HIGH,CRITICAL ncr-dev-container:latest
trivy image --scanners vuln --severity MEDIUM,HIGH,CRITICAL claude-pty-control:scan

# 3. 設定錯誤（Dockerfile／compose）。⚠ 這一項與 vuln 分開跑，兩者的 finding 不會互相涵蓋。
trivy fs --scanners misconfig --severity MEDIUM,HIGH,CRITICAL .
```

macOS 上 trivy 找不到 docker daemon 時：`export DOCKER_HOST=unix://$HOME/.docker/run/docker.sock`。

`claude-pty` image 內另有 `/app/installed-requirements.txt`，是 build 當下的落地版本，
掃出來的東西可以直接對回那一份。

## 這一輪的結果

| 目標 | 修改前 | 修改後 | 其中有修補版本可用 |
|---|---:|---:|---:|
| filesystem（lockfile） | 1 | **0** | 1 → 0 |
| `claude-pty` deploy image | 117 | **63** | 54 → **0** |
| `ncr-dev-container` image | 163 | **45** | 132 → 14 |
| 設定錯誤（misconfig，兩份 Dockerfile） | 4 | **2** | 2 → 0（另 2 筆是接受，見下） |

剩下的 45＋63 筆**逐筆列在下面**，不是只列可修的那些。設定錯誤那一項的涵蓋率有一個
掃描器自己的缺口，也寫在下面。

### 修了什麼

1. **`cryptography` 49.0.0 → 50.0.0**（CVE-2026-69247，HIGH）。`claude-pty/uv.lock`。
2. **兩顆 image 的 OS 套件做安全更新**（`apt-get upgrade`）。`claude-pty` 那 54 筆可修的
   全部出自同一個來源套件（util-linux 2.41-5 → 2.41.5-0+deb13u1，CVE-2026-53612 等）。
   base image 的重建節奏不歸我們管，所以在自己的 build 裡補一次。
3. **`claude-pty` 的 build backend 裝完就移除**（`pip uninstall -y uv setuptools wheel`）。
   `--group build` 帶進來的釘死 setuptools 只有建套件那一步要用；留在 runtime 裡不會被
   任何東西 import，卻會被掃進去——它自己還 vendor 了 wheel 與 jaraco.context，實測多出
   三筆 MEDIUM 以上。build 期的雜湊驗證不必用 runtime 的攻擊面去付。
4. **`ncr-dev-container` 清掉 uv 的下載快取、升級 npm 並清 npm cache**。
   `~/.cache/uv/archive-v0/` 是安裝過程留下的解壓檔（cryptography 48.0.1、tornado 6.5.5、
   h2、msgpack 各一份），沒有任何工具會載入它們，但掃描器看得到、照樣算 CVE。
   工具都裝完了，快取沒有用途。這一項就清掉 60 幾筆。

### 剩下的每一筆（`ncr-dev-container`，45 筆全列）

⚠ 這一節列的是**全部 45 筆**，不是只有「有修補版本可用」的那 14 筆。只列可修的那些會讓
讀者以為其餘的不存在；沒有修補版本是一個處置，不是一個可以不寫的狀態。

| CVE | 套件 | 安裝版本 | 嚴重度 | 有修補版本 | 處置 |
|---|---|---|---:|---|---|
| CVE-2026-14257 | `brace-expansion` | 5.0.7 | HIGH | 5.0.8, 3.0.3, 2.1.3, 1.1.17 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-69152 | `brace-expansion` | 5.0.7 | HIGH | 1.1.18, 2.1.4, 3.0.6, 5.0.9 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-54272 | `ip-address` | 10.2.0 | MEDIUM | 10.2.1 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-69192 | `ip-address` | 10.2.0 | HIGH | 10.3.1 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-69198 | `ip-address` | 10.2.0 | MEDIUM | 10.2.2 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-33671 | `picomatch` | 4.0.3 | HIGH | 4.0.4, 3.0.2, 2.3.2 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-33672 | `picomatch` | 4.0.3 | MEDIUM | 4.0.4, 3.0.2, 2.3.2 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-73566 | `tar` | 7.5.19 | HIGH | 7.5.21 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-15157 | `undici` | 6.27.0 | MEDIUM | 6.28.0, 7.29.0, 8.9.0 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-16728 | `undici` | 6.27.0 | MEDIUM | 6.28.0, 7.29.0, 8.9.0 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| CVE-2026-16729 | `undici` | 6.27.0 | MEDIUM | 6.28.0, 7.29.0, 8.9.0 | npm 或 codegraph 自己捆的相依（見下方分類一、二） |
| GHSA-6v7p-g79w-8964 | `msgpack` | 1.1.2 | HIGH | 1.2.1 | 工具環境帶進來，不是任何 lockfile 的直接相依（分類三） |
| CVE-2025-47273 | `setuptools` | 70.3.0 | HIGH | 78.1.1 | 工具環境帶進來，不是任何 lockfile 的直接相依（分類三） |
| CVE-2026-59890 | `setuptools` | 70.3.0 | MEDIUM | 83.0.0 | 工具環境帶進來，不是任何 lockfile 的直接相依（分類三） |
| CVE-2026-27456 | `bsdutils` | 1:2.39.3-9ubuntu6.5 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2024-52005 | `git` | 1:2.43.0-1ubuntu7.3 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2024-52005 | `git-man` | 1:2.43.0-1ubuntu7.3 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-41256 | `jq` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-41257 | `jq` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-43895 | `jq` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-43896 | `jq` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-44777 | `jq` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-27456 | `libblkid1` | 2.39.3-9ubuntu6.5 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2025-66382 | `libexpat1` | 2.6.1-2ubuntu0.4 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-41256 | `libjq1` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-41257 | `libjq1` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-43895 | `libjq1` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-43896 | `libjq1` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-44777 | `libjq1` | 1.7.1-3ubuntu0.24.04.2 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-22185 | `liblmdb0` | 0.9.31-1build1 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-27456 | `libmount1` | 2.39.3-9ubuntu6.5 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-13757 | `libp11-kit0` | 0.25.3-4ubuntu2.1 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-27456 | `libsmartcols1` | 2.39.3-9ubuntu6.5 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-27456 | `libuuid1` | 2.39.3-9ubuntu6.5 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-27456 | `mount` | 2.39.3-9ubuntu6.5 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-55655 | `openssh-client` | 1:9.6p1-3ubuntu13.18 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-27456 | `util-linux` | 2.39.3-9ubuntu6.5 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-51400 | `vim` | 2:9.1.0016-1ubuntu7.19 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-51401 | `vim` | 2:9.1.0016-1ubuntu7.19 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-51400 | `vim-common` | 2:9.1.0016-1ubuntu7.19 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-51401 | `vim-common` | 2:9.1.0016-1ubuntu7.19 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-51400 | `vim-runtime` | 2:9.1.0016-1ubuntu7.19 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-51401 | `vim-runtime` | 2:9.1.0016-1ubuntu7.19 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-51400 | `xxd` | 2:9.1.0016-1ubuntu7.19 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |
| CVE-2026-51401 | `xxd` | 2:9.1.0016-1ubuntu7.19 | MEDIUM | **無** | Ubuntu 24.04 尚未發布修補版本（`status=affected`） |

三類，理由分開講：

**一、npm 自己捆的相依（9 筆，node-pkg）。** `brace-expansion`、`ip-address`、`tar`、`undici`
住在 `.nvm/versions/node/v24.19.0/lib/node_modules/npm/node_modules/` 裡，是 npm 的內臟。
image 已經升到 `npm@latest`（這一手把 tar 從 7.5.16 推到 7.5.19、undici 從 6.26.0 推到
6.27.0，也修掉了另外幾筆），剩下的要等 npm 自己發版。要在這裡「修好」只能去改 npm 的
node_modules，那比 CVE 本身危險。

**二、codegraph 捆的 picomatch（2 筆，node-pkg）。** 同樣是第三方工具的內臟，而那支工具
刻意不釘版本（輸出不進報告 finding，見 Dockerfile 該處說明）。

**三、msgpack 與 setuptools（3 筆，python-pkg）。** msgpack 這條**試過修**：加
`--with "msgpack>=1.2.1"` 之後 build 直接失敗，mitmproxy 12.2.3 的相依範圍不允許。而
mitmproxy 的版本是為了 capture 檔案格式相容釘住的，動它要重驗既有的 capture 讀不讀得回，
不是這一輪的範圍。setuptools 隨工具環境帶進來，不是任何一份 lockfile 裡的直接相依。

**四、Ubuntu 的 OS 套件（31 筆）。** 全部 `status=affected`、全部沒有修補版本：這顆 image
已經在 build 時跑過 `apt-get upgrade`，所以它拿到的就是 Ubuntu 24.04 目前發布的最新版。
要修得等上游發套件，這裡做不了事。**這 31 筆會隨 Ubuntu 發版而變動**，重跑指令在上面。

### `claude-pty` 控制平面 image（63 筆全列，可修 0）

這顆的可修項目已經歸零（54 筆全出自 util-linux，`apt-get upgrade` 一次解決）。剩下的
63 筆全部沒有修補版本，Debian trixie 尚未發布：

| CVE | 套件 | 安裝版本 | 嚴重度 | 有修補版本 | 處置 |
|---|---|---|---:|---|---|
| CVE-2026-3184 | `bsdutils` | 1:2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-41991 | `gzip` | 1.13-1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-41992 | `gzip` | 1.13-1 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-54369 | `libacl1` | 2.3.2-2+b1 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-54370 | `libacl1` | 2.3.2-2+b1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-54371 | `libattr1` | 1:2.5.2-3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-3184 | `libblkid1` | 2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-42250 | `libbz2-1.0` | 1.0.8-6 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-5435 | `libc-bin` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-5450 | `libc-bin` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-5928 | `libc-bin` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-6238 | `libc-bin` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-6368 | `libc-bin` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-6791 | `libc-bin` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-5435 | `libc6` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-5450 | `libc6` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-5928 | `libc6` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-6238 | `libc6` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-6368 | `libc6` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-6791 | `libc6` | 2.41-12+deb13u3 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-3184 | `liblastlog2-2` | 2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-3184 | `libmount1` | 2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2025-69720 | `libncursesw6` | 6.5+20250216-2 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-54411 | `libpam-modules` | 1.7.0-5 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-54411 | `libpam-modules-bin` | 1.7.0-5 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-54411 | `libpam-runtime` | 1.7.0-5 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-54411 | `libpam0g` | 1.7.0-5 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-3184 | `libsmartcols1` | 2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-11822 | `libsqlite3-0` | 3.46.1-7+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-11824 | `libsqlite3-0` | 3.46.1-7+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-50812 | `libsqlite3-0` | 3.46.1-7+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-50813 | `libsqlite3-0` | 3.46.1-7+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-14456 | `libssl3t64` | 3.5.6-1~deb13u2 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=fix_deferred`） |
| CVE-2026-15059 | `libsystemd0` | 257.13-1~deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-16742 | `libsystemd0` | 257.13-1~deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2025-69720 | `libtinfo6` | 6.5+20250216-2 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-15059 | `libudev1` | 257.13-1~deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-16742 | `libudev1` | 257.13-1~deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-3184 | `libuuid1` | 2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-3184 | `login` | 1:4.16.0-2+really2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-3184 | `mount` | 2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2025-69720 | `ncurses-base` | 6.5+20250216-2 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2025-69720 | `ncurses-bin` | 6.5+20250216-2 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-14456 | `openssl` | 3.5.6-1~deb13u2 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=fix_deferred`） |
| CVE-2026-14456 | `openssl-provider-legacy` | 3.5.6-1~deb13u2 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=fix_deferred`） |
| CVE-2025-15649 | `perl-base` | 5.40.1-6 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-12087 | `perl-base` | 5.40.1-6 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-13221 | `perl-base` | 5.40.1-6 | CRITICAL | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-42496 | `perl-base` | 5.40.1-6 | CRITICAL | **無** | Debian trixie 尚未發布修補版本（`status=fix_deferred`） |
| CVE-2026-42497 | `perl-base` | 5.40.1-6 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=fix_deferred`） |
| CVE-2026-48959 | `perl-base` | 5.40.1-6 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-48961 | `perl-base` | 5.40.1-6 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-48962 | `perl-base` | 5.40.1-6 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-57432 | `perl-base` | 5.40.1-6 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-57433 | `perl-base` | 5.40.1-6 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-7010 | `perl-base` | 5.40.1-6 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-8376 | `perl-base` | 5.40.1-6 | CRITICAL | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-9538 | `perl-base` | 5.40.1-6 | HIGH | **無** | Debian trixie 尚未發布修補版本（`status=fix_deferred`） |
| CVE-2026-18477 | `tar` | 1.35+dfsg-3.1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-18508 | `tar` | 1.35+dfsg-3.1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-5704 | `tar` | 1.35+dfsg-3.1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-3184 | `util-linux` | 2.41.5-0+deb13u1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |
| CVE-2026-27171 | `zlib1g` | 1:1.3.dfsg+really1.3.1-1+b1 | MEDIUM | **無** | Debian trixie 尚未發布修補版本（`status=affected`） |

⚠ `fix_deferred` 那 6 筆是 Debian 明確標示「知道、但這個版本不修」的，與 `affected`
（尚未評估或尚未發布）不同，但對我們的處置而言結果一樣：現在沒有東西可以裝上去。

### 設定錯誤掃描（misconfig）

⚠ **這一項的涵蓋率有缺口，先講清楚再看結果。** trivy 0.62.1 內建的 policy 有一顆跑不起來：

```
ERROR [rego] rule="deny" file_path=".../docker/policies/latest_tag.rego"
      err="latest_tag.rego:55: eval_conflict_error: object keys must be unique"
```

那是 **DS001（base image 用了 `:latest`）** 的檢查，它在這一輪**沒有被評估過**。所以
下面那句「其餘通過」的正確讀法是「其餘*被跑到的*規則通過」。（我們的 Dockerfile 用的是
`ubuntu:24.04`、`python:3.13-slim`、`rust:slim`，沒有 `:latest`，但那是我自己看的，
不是掃描器驗的。）

| 檔案 | 規則 | 嚴重度 | 內容 | 處置 |
|---|---|---|---|---|
| `dev-container/Dockerfile` | DS029 | HIGH | `apt-get` 少了 `--no-install-recommends` | **接受**。2026-08-23 在乾淨的 ubuntu:24.04 上量過：加了它這一串從 100 個套件降到 77 個，少掉的包含 `less`（git 的 pager）、`patch`、`openssl`、`xxd`、`nftables`。這顆容器裡住著一個會執行任意指令的 agent，換不過來。理由寫在該行上方 |
| `dev-container/Dockerfile` | DS031 | CRITICAL | `ENV SSH_AUTH_SOCK` 被判定為 secret | **誤判，接受**。值是 socket **路徑**不是憑證；整條 agent 轉發的設計就是「交出去的是簽章能力、私鑰留在 host」。規則認的是變數名稱不是值 |
| `claude-pty/deploy/Dockerfile` | DS013 ×2 | MEDIUM | builder 階段用 `RUN cd` 換目錄 | **已修**，改成 `WORKDIR`。改完 final image 的 digest 一個位元都沒變（那兩行只在 builder 階段），可重現驗證 |

### 可達性：這裡只講位置，不宣稱不可達

⚠ **上面每一筆的「處置」欄講的是「能不能修」，不是「打不打得到」。** 這兩件事分開講，
因為第二件我沒有證據：要說一個 CVE 不可達，得拿得出 call path，而這一輪沒有做那個分析。
下面只講一件查得到的事——它們在哪一顆 image 裡。

| 位置 | 筆數 | 這顆 image 是什麼 |
|---|---:|---|
| `ncr-dev-container`（session 容器） | 45 | 定位就是「讓不完全信任的 agent 住進去」（ADR 0006、Day 17-24 那條線）。它不對外聽 port |
| `claude-pty` 控制平面 image | 63 | 對外的那一顆（經 nginx）。**可修項目已歸零**，剩下的全部沒有修補版本可裝 |
| filesystem（lockfile） | 0 | 乾淨 |

「session 容器裡的東西比較不要緊」這句話**只在一個前提下成立**：那顆容器裡本來就跑著一個
能執行任意指令的 agent，所以它的威脅模型不是「防止裡面的人拿到 shell」。這不等於那些 CVE
沒有影響，只是它們影響不到一個原本就假設會失守的地方以外。

### 沒有做的事

沒有降 severity、沒有加全域 skip、沒有 `.trivyignore`、沒有刪 lockfile。
報告要綠得靠真的修掉，不是靠改題目。
