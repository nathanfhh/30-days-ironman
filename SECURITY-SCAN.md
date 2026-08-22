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

### 沒修的 14 筆，以及為什麼

| CVE | 套件 | 嚴重度 | 有修補版本 | 為什麼不修 |
|---|---|---|---|---|
| CVE-2026-14257 | brace-expansion 5.0.7 | HIGH | 5.0.8 | npm 自己捆的相依 |
| CVE-2026-69152 | brace-expansion 5.0.7 | HIGH | 5.0.9 | 同上 |
| CVE-2026-69192 | ip-address 10.2.0 | HIGH | 10.3.1 | 同上 |
| CVE-2026-54272 | ip-address 10.2.0 | MEDIUM | 10.2.1 | 同上 |
| CVE-2026-69198 | ip-address 10.2.0 | MEDIUM | 10.2.2 | 同上 |
| CVE-2026-73566 | tar 7.5.19 | HIGH | 7.5.21 | 同上 |
| CVE-2026-15157 | undici 6.27.0 | MEDIUM | 6.28.0 | 同上 |
| CVE-2026-16728 | undici 6.27.0 | MEDIUM | 6.28.0 | 同上 |
| CVE-2026-16729 | undici 6.27.0 | MEDIUM | 6.28.0 | 同上 |
| CVE-2026-33671 | picomatch 4.0.3 | HIGH | 4.0.4 | codegraph 自己捆的相依 |
| CVE-2026-33672 | picomatch 4.0.3 | MEDIUM | 4.0.4 | 同上 |
| GHSA-6v7p-g79w-8964 | msgpack 1.1.2 | HIGH | 1.2.1 | mitmproxy 12.2.3 的相依範圍不允許 |
| CVE-2025-47273 | setuptools 70.3.0 | HIGH | 78.1.1 | 隨工具環境帶進來，非直接相依 |
| CVE-2026-59890 | setuptools 70.3.0 | MEDIUM | 83.0.0 | 同上 |

三類，理由分開講：

**一、npm 自己捆的相依（9 筆）。** `brace-expansion`、`ip-address`、`tar`、`undici` 住在
`.nvm/versions/node/v24.19.0/lib/node_modules/npm/node_modules/` 裡，是 npm 的內臟。
image 已經升到 `npm@latest`（這一手把 tar 從 7.5.16 推到 7.5.19、undici 從 6.26.0 推到
6.27.0，也修掉了另外幾筆），剩下的要等 npm 自己發版。要在這裡「修好」只能去改 npm 的
node_modules，那比 CVE 本身危險。

**二、codegraph 捆的 picomatch（2 筆）。** 同樣是第三方工具的內臟，而那支工具刻意不釘版本
（輸出不進報告 finding，見 Dockerfile 該處說明）。

**三、msgpack 與 setuptools（3 筆）。** msgpack 這條**試過修**：加 `--with "msgpack>=1.2.1"`
之後 build 直接失敗，mitmproxy 12.2.3 的相依範圍不允許。而 mitmproxy 的版本是為了 capture
檔案格式相容釘住的，動它要重驗既有的 capture 讀不讀得回，不是這一輪的範圍。
setuptools 隨工具環境帶進來，不是任何一份 lockfile 裡的直接相依。

### 可達性

這 14 筆全部在 **session 容器**裡，而那顆容器的定位就是「讓不完全信任的 agent 住進去」
（ADR 0006 的安全輪廓、Day 17-24 那條線）。它們不在控制平面（`claude-pty` image 的可修
項目已經歸零），也不在對外聽 port 的那條路上。

### 沒有做的事

沒有降 severity、沒有加全域 skip、沒有 `.trivyignore`、沒有刪 lockfile。
報告要綠得靠真的修掉，不是靠改題目。
