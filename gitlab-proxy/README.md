# GitLab Proxy

一顆 nginx 反向代理，替 `nathan-code-review` 這個 skill 擋在 GitLab 前面，做三件事：

1. **憑證不進 session。** token 只存在於這顆容器的環境變數裡；agent 對代理裸打，
   由代理蓋上 `PRIVATE-TOKEN` 再轉給 GitLab。
2. **端點白名單。** 只有 skill 真的會呼叫的六條路徑放行，其餘一律 403。
3. **速率限制。** 10 req/s，避免 agent 失控時把 GitLab 打爆。

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

skill 的 `scripts/gitlab_api.py` 是從 MR URL 推導出 `https://{host}/api/v4`，
所以要讓它改打代理，得把 base URL 指過來。最小的改法是加一個環境變數：

```bash
# 走代理（token 不需要、也不應該設）
export NCR_GITLAB_API_BASE=http://127.0.0.1:5678/api/v4

# 容器內的 agent 則用 hostname
export NCR_GITLAB_API_BASE=http://gitlab-proxy:5678/api/v4
```

> 這一段需要對 `gitlab_api.py` 做對應的修改；本目錄只提供代理本身。

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

另外，git-lfs 沒辦法這樣代理：LFS 的 batch API 會回外部 href，可能直指物件儲存，
nginx 改不掉。有用 LFS 的 repo 會靜默壞掉。

## 這還不是終點

token 放在容器的環境變數裡，`docker inspect` 與 `/proc/1/environ` 都看得到。
對「不要讓 agent 直接持有憑證」這個目標來說夠用，但如果要做到多人共用、
每人一把自己的 token，就需要把憑證加密存起來、每人一顆代理——那是另一個故事。
