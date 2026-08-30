# GitLab Proxy

一顆 nginx 反向代理，替 `nathan-code-review` 這個 skill 擋在 GitLab 前面，做三件事：

1. **API 呼叫的憑證不進 session。** token 只存在於這顆容器的環境變數裡；agent 對代理
   裸打，由代理蓋上 `PRIVATE-TOKEN` 再轉給 GitLab。
   ⚠ **只有 API。** 這份設定不代理 git 的 smart HTTP，所以 `clone` 那條路仍然需要
   agent 自己帶 token（見文末「已知缺口」）。把這一條寫成「憑證不進 session」會蓋掉
   那一半。`claude-pty` 的 per-user 代理才是 API 與 clone 都收進同一道邊界的那一套。
2. **端點白名單。** 只有 skill 真的會呼叫的六條路徑放行，其餘一律 403。
3. **速率限制。** 2 req/s，避免 agent 失控時把 GitLab 打爆。

## 為什麼需要它

skill 原本的做法是把 token 放在 `GITLAB_TOKEN` 環境變數裡，讓 agent 自己帶。
那跟雙手奉上沒有差別：MR 說明裡一段 prompt injection 就能讓它拿那把 token 做
scope 內的任何事，而且事後你沒有任何 per-call 的紀錄可查。

GitLab 18.10 起有 fine-grained PAT（19.2 GA），可以把 token 的權限切細，
那一層值得搭配使用。但它解決的是「token 能做什麼」，不是「token 在誰手上」。
代理買到的是 token 買不到的三件事：**端點白名單、速率限制、每一次呼叫的紀錄**。

## 啟動

```bash
cp .env.example .env
# 編輯 .env，填入 GITLAB_HOST 與 GITLAB_TOKEN
docker compose up -d

curl http://localhost:5678/ping
# {"result": true, "data": {}}
```

改完 `nginx.conf.template` 要重啟才會生效（template 是在容器啟動時才展開的）：

```bash
docker compose restart
```

> **`GITLAB_HOST` 必須在容器啟動當下就解析得到。** nginx 是在載入設定檔時解析
> `upstream` 的主機名，解不到就直接 `[emerg] host not found in upstream` 而拒絕啟動；
> 配上 `restart: unless-stopped` 會變成無限重啟。內網 GitLab 的 DNS 若比這顆容器晚就緒，
> 症狀就是它一直起不來——先 `docker compose logs` 看這一行。

## 怎麼讓 skill 走這條

skill 的 `scripts/gitlab_api.py` 預設從 MR URL 推導出 `https://{host}/api/v4`，
但它認得 `NCR_GITLAB_API_BASE`——設了就改打這個 base：

```bash
# 走代理（token 不需要、也不應該設）
export NCR_GITLAB_API_BASE=http://127.0.0.1:5678

# 容器內的 agent 則用 hostname
export NCR_GITLAB_API_BASE=http://gitlab-proxy:5678
```

> **只給到 `host:port`，不要帶 `/api/v4`。** 那一段由 `gitlab_api.py` 自己補
> （見 `api_base_for()`），多寫一層就變成 `/api/v4/api/v4`——不在白名單裡，代理回 403。
> skill 端已經實作，本目錄只提供代理本身；行為對照 `tests/test_gitlab_api.py`
> 的 `TestApiBaseOverride`。

## 白名單清單

對照 `skills/nathan-code-review/scripts/gitlab_api.py` 的七個子指令：

| 子指令 | 方法 | 路徑 |
|---|---|---|
| `whoami` | GET | `/api/v4/user` |
| `mr` | GET | `/api/v4/projects/{id}/merge_requests/{iid}` |
| `attachments` | GET | `/api/v4/projects/{id}/uploads/{secret}/{filename}` |
| `discussions` | GET | `/api/v4/projects/{id}/merge_requests/{iid}/discussions` |
| `post-report` | POST | `/api/v4/projects/{id}/merge_requests/{iid}/discussions` |
| `discussion` | GET | `/api/v4/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}` |
| `reply` | POST | `/api/v4/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes` |

`discussions` 的 GET 與 POST 是同一條路徑，nginx 同一條 regex 只能有一個
location，所以合併成一個 block、用 `limit_except GET POST` 控方法。
六個 location 對應到這七個操作。

### 速率限制怎麼給

共用一個 zone、**2 req/s**，各 location 自己調 burst。

2 這個數字來自實際用量：一整場審查是 whoami 1 次、取 MR 1 次、附件 0~5 次、
中間分析好幾分鐘、發報告 1 次、偶爾回覆 1 次——總共 5~20 次，持續速率遠低於
1 r/s。設 10 r/s 等於沒設限：正常操作碰不到，而跑進迴圈的 agent 在那底下可以打一整天。

burst 則是按「**這條路徑有沒有重試的安全網**」給，不是按端點重要性：

| location | 方法 | burst | 理由 |
|---|---|---:|---|
| `/user` | GET | 2 | 整場一次 |
| `/merge_requests/{iid}` | GET | 2 | 整場一次 |
| `/uploads/...` | GET | 8 | 附件可能好幾個、循序下載，最會連發 |
| `/discussions` | GET+POST | 5 | GET 分頁會連發，POST 發報告也走這條 |
| `/discussions/{id}` | GET | 3 | 已知 id 的單串查詢 |
| `/discussions/{id}/notes` | POST | 5 | **沒有重試安全網** |

`gitlab_api.py` 只對冪等的 GET 重試 429（3 次、線性退避），POST 永遠不重試——
重試一個發留言的 POST 會在 MR 上留下兩則。所以 GET 撞到限流只是慢幾秒，
POST 撞到就是報告發不出去。寫入端要給餘裕，讀取端反而可以收緊。

⚠ 限流的 key 是 `$binary_remote_addr`，而這個情境下所有請求都來自同一個容器，
所以它限的是**這台機器**，不是**這個使用者**。單人用沒差；多人共用時要每人一顆代理，
額度才會是真的 per-user。

**加端點時**：這份 template 與 skill 的 `references/gitlab-api.md` 是兩份要手動
同步的清單。漏掉其中一邊的症狀是「本機直連跑得動、走代理 403」。

## 已知缺口：git clone 還是需要 token

skill 的 diff 不是從 API 來的，是從 clone 算出來的：

```bash
git clone --filter=blob:none \
  -c http.extraHeader="PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://{host}/{project_path}.git" ...
```

**這條路徑沒有走代理**，所以 `GITLAB_TOKEN` 目前仍然得存在於 agent 的環境裡，
「憑證不進 session」這句話目前只兌現了 API 那一半。

要補上的話，代理得多一條 git smart HTTP 的 location，而且**不能沿用 server 層
那個 `PRIVATE-TOKEN`**：git transport 只認 Basic。

```nginx
location ~ ^/.+\.git/(info/refs|git-upload-pack|git-receive-pack)$ {
    # base64("oauth2:<token>")，而且要在 location 內單獨設，不能繼承
    proxy_set_header Authorization "Basic ${GITLAB_GIT_BASIC}";
    proxy_set_header PRIVATE-TOKEN "";
    client_max_body_size 0;          # 預設 10m，大一點的 push 會 413
    proxy_request_buffering off;     # 否則整包 packfile 先落地再轉發
    proxy_buffering off;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_read_timeout 3600s;        # 大 repo 的 upload-pack 第一個 byte 可能很久
    proxy_pass https://gitlab;
}
```

啟用它同時要改 skill 端的 clone URL（指向代理、拿掉 `http.extraHeader`），
所以這裡先不預設開啟。

另外，git-lfs 沒辦法這樣代理。上面那個 git 的 location 只收 `info/refs`／
`git-upload-pack`／`git-receive-pack` 三個端點，LFS 的 `….git/info/lfs/objects/batch`
對不上，會落到地板 `location /` 拿 403，也就是**明確失敗**，不是靜默壞掉。而且就算
把它也加進白名單還是不夠：LFS 的 batch API 會回外部 href，可能直指物件儲存，nginx 改不掉。

## 這還不是終點

token 放在容器的環境變數裡，`docker inspect` 與 `/proc/1/environ` 都看得到。
對「不要讓 agent 直接持有憑證」這個目標來說夠用，但如果要做到多人共用、
每人一把自己的 token，就需要把憑證加密存起來、每人一顆代理——那是另一個故事。
