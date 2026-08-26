# mitmweb UI 從管理畫面直達：可行性評估與實作規劃

日期：2026-08-26
狀態：規劃中（未實作）

## 需求

網頁開的 session 啟用流量錄製（`profile.capture`）時，終端抽屜（TerminalDrawer）的
按鈕列多一顆按鈕，點下去**在新分頁**開啟那個 session 的 mitmweb 介面，途中不該
要求使用者記任何 token。

使用者提的兩個具體想法：

1. 走 nginx + `auth_request` 授權（比照 ttyd 的 `/session/<id>/` 路由 B）。
2. mitmweb 的密碼直接指定（例如用 Claude Code 的 sessionId），資料庫不用多記欄位。

## 結論

**可行**，而且比預期順。三個關鍵事實把最難的部分消掉了：

1. **mitmweb 的 SPA 是路徑相對的**，可以直接掛在子路徑下代理（v12.2.3 已驗證原始碼）：
   - `mitmproxy/tools/web/index.html` 的資源全是 `./static/...`
   - WebSocket URL 由 SPA 現場組：`location.pathname.replace(/\/$/,"") + "/updates"`
     （`web/src/js/backends/websocket.ts`）——掛在 `/session/<sid>/mitm/` 底下，
     WS 會自動打到 `/session/<sid>/mitm/updates`
   - REST 呼叫走 `fetchApi("./flows")` 相對路徑
2. **密碼可以自己指定，且有兩條免打字的認證路**（`webaddons.py` / `app.py` v12.2.3）：
   - `--set web_password=<任意字串>`（明文可、`$` 開頭視為 argon2 hash）
   - 請求帶 `Authorization: Bearer <密碼>` 或 `?token=<密碼>` 即通過；
     通過後發 signed cookie（**cookie secret 是每個 mitmweb 行程隨機的**，
     跨 session 的 cookie 彼此驗不過 → 天然 fail-closed，不會串場）
3. **動態 port + auth_request 的整套模式已存在**：`/api/auth/view` 回
   `X-Ttyd-Port`、nginx `auth_request_set` 取出放進變數化 `proxy_pass`。
   mitmweb 版只是同一招再套一次（回 `X-Mitm-Port` + `X-Mitm-Token`）。

## 現況盤點

```
瀏覽器 ──► nginx ──┬── /  /login  /account  /api/*      → Flask 控制平面（control 容器）
                   └── /session/<sid>/                  → auth_request → ttyd（control 容器內）
                                                          ttyd spawn `docker attach <session 容器>`

session 容器（per-user network，ADR 0016）
  └── mitmweb --listen 127.0.0.1:8880（proxy）
              --web-host 127.0.0.1:8081（UI；網頁路徑由 run_kwargs.py 設 NCR_MITM_WEB_BIND 收回 loopback）
              --set web_password=<隨機 24 字元>（entrypoint.sh 產生，印在 docker logs）
```

重要事實：

- mitmweb UI 綁在 session 容器的 **loopback**，沒有任何 host port、兄弟容器也連不到
  （`run_kwargs.py:210-217` 刻意收回的），目前「要看只能進容器」。
- control 容器**沒有** socat，但有 docker CLI（掛 host docker socket）與完整 Python
  （Flask/gunicorn，1 worker × 8 threads）。
- session 容器**沒有** socat，但有 bash（`/dev/tcp` 可用）與 python3。
- `run_kwargs.py:276-277` 已留話：「mitmweb UI 不再由控制平面發布 host port（ADR 0008）；
  需要看時經 container 內部或另行 port-forward」——本規劃走的就是 relay 這條。
- 前端已知道每個 session 的 `capture` 旗標（`frontend/src/lib/sessions.ts:12`），
  抽屜按鈕列已有 pop-out（`TerminalDrawer.vue:189` `window.open(path, "_blank")`）的先例。

## 建議設計

### 資料路徑

```
新分頁  GET /session/<sid>/mitm/
   │
   ▼ nginx（新 location，排在 ttyd 那條 regex 之前）
auth_request /_auth_mitm ──► Flask /api/auth/mitm?session=<sid>
   │                          · 驗 cookie + 擁有權（照抄 auth_view 的 _owned）
   │                          · capture 關→404；容器不在/沒有錄製→403/導回
   │                          · 沒有活著的 relay 就建一個（見下）
   │                          · 回 200 + X-Mitm-Port + X-Mitm-Token
   ▼
auth_request_set $mitm_port / $mitm_token
proxy_pass http://control:$mitm_port$rest_path      ← 去掉 /session/<sid>/mitm 前綴
proxy_set_header Authorization "Bearer $mitm_token"  ← token 全程不進瀏覽器
   ▼
control 容器內的 relay（loopback port，綁 127.0.0.1）
   │  每個 TCP 連線 = `docker exec -i <session 容器> bash -c 'exec 3<>/dev/tcp/127.0.0.1/8081; cat >&3 & cat <&3'`
   ▼
session 容器 127.0.0.1:8081 的 mitmweb
```

### relay 的形狀

比照 `views.py`：一個「mitm view」= DB 一列 + 一個 listen 在 127.0.0.1 的 port、
port 由 `views.port` 的 UNIQUE 仲裁（現成機制，跨 worker 原子）。relay 本體兩個選項：

- **選項 A：add socat 到 control image**，每 session spawn
  `socat TCP-LISTEN:<port>,bind=127.0.0.1,fork EXEC:'docker exec -i <ctr> bash -c ...'`。
  最少新程式碼，多一顆二進位（比照 ttyd 的 sha256 驗證流程處理）。
- **選項 B：Flask 行程內的 Python relay**（listener thread + 每連線 thread 對
  `docker exec` 的 stdio pump）。不加二進位，但泵迴圈要自己寫對（半關閉、WS 長連線）。

建議 A：socat 的 fork/exec 模型已經是 battle-tested 的東西，我們只要管「起、收」。
生命週期跟隨 `views.archive()`——session 回收時連 ttyd 一起收。

### token 方案：HMAC 衍生，不落 DB、不進瀏覽器

```
mitm_web_password(sid) = HMAC_SHA256(FLASK_SECRET_KEY, f"mitm:{sid}")[:24] 的 base64url
```

- control 建容器時用新 env `NCR_MITM_WEB_PASSWORD` 帶進去；
- `/api/auth/mitm` 用同一個公式當場重算 → `X-Mitm-Token` 回給 nginx → Bearer 注入。
- **DB 一個欄位都不用加**（達成使用者「現成的資料庫不用多紀錄」的目標）。

**為什麼不直接用 Claude Code 的 sessionId（`NCR_SESSION_ID`）**——使用者提的方向，
評估後建議改成 HMAC，理由：

1. `NCR_SESSION_ID` 目前是 **entrypoint 在容器內自己產的**（`entrypoint.sh:101`
   讀 `/proc` uuid），control 平面不知道它。要用它就得反過來由 control 餵進容器，
   entrypoint 與 run_kwargs 兩邊都要動，還得處理「人自己開容器」路徑的相容。
2. sessionId 是**可枚舉的**：capture 落盤目錄名就是 sessionId
   （`~/ncr/mitm/<sessionId>/`），而 `ncr/` 根是 per-user 共用掛載——同一個使用者開的
   任何一顆 session 容器裡的 agent 都能 `ls` 出全部 sessionId。UI 顯示的是
   **未脫敏**即時流量，萬一哪天某條路徑讓兄弟容器碰得到 8081，「token＝sessionId」
   等於把全部場次的未脫敏流量一次交出去。HMAC 派生的 token 沒有這個性質：
   知道一場的 token 推不出別場的，洩漏半徑小得多。
3. HMAC 一樣免 DB、一樣確定性，還能靠換 secret 一次作廢全部。

（保留選項：若真要用 sessionId，入口改成 `NCR_SESSION_ID="${NCR_SESSION_ID:-$(…uuid)}"`
一行即可，但上面的洩漏半徑問題要自己吞。）

### entrypoint.sh 的更動（一行）

```bash
token="${NCR_MITM_WEB_PASSWORD:-$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')}"
token="${token:0:24}"
```

人自己開容器那條路徑不設這個 env → 行為與今天完全一致（隨機、印 logs）。
⚠ 觸到 dev-container ⟷ claude-pty 的耦合區，改完記得比照 SSOT 慣例檢查
`tests/test_entrypoint_*`。

### nginx 的更動

```nginx
location = /_auth_mitm {
    internal;
    proxy_pass http://claude_pty_control/api/auth/mitm?session=$claude_pty_session_id;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
}

# 一定要在現有 `^/session/(?<sid>)/` regex **之前**（nginx regex location 取第一個命中）
location ~ ^/session/(?<claude_pty_mitm_sid>[A-Za-z0-9]+)/mitm(?<claude_pty_mitm_rest>/.*)?$ {
    set $claude_pty_session_id $claude_pty_mitm_sid;
    auth_request /_auth_mitm;
    auth_request_set $mitm_port  $upstream_http_x_mitm_port;
    auth_request_set $mitm_token $upstream_http_x_mitm_token;

    error_page 401 403 500 502 503 504 = @view_denied;

    resolver 127.0.0.11 valid=10s ipv6=off;
    set $mitm_upstream control;
    proxy_pass http://$mitm_upstream:$mitm_port$claude_pty_mitm_rest;
    proxy_set_header Authorization "Bearer $mitm_token";

    # mitmweb /updates 是 WebSocket
    proxy_http_version 1.1;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

注意事項：

- **trailing slash**：SPA 的相對路徑以頁面 URL 為準，`/session/<sid>/mitm`（無尾斜線）
  會讓 `./static/...` 解析到 `/session/<sid>/static/`。在這條 location 裡對
  `rest` 為空的情況 `return 308 /session/<sid>/mitm/;`。
- mitmweb 自带 `X-Frame-Options: DENY`——**所以用新分頁，不要 iframe**；
  需求本來就是新分頁，正好。
- mitmweb 的 CSP `connect-src 'self' ws:` 在同源代理下沒問題。
- cookie 名 `mitmproxy-auth-8081` 所有 session 共用、Path=/，但每個 mitmweb 行程的
  cookie secret 不同 → A 場發的 cookie 在 B 場驗不過 → 落回 Bearer（nginx 每次都有注入）
  → **不會串場、不會被舊 cookie 卡住**。這個性質值得寫一條測試釘住。
- `/api/auth/mitm` 比照 `/api/auth/view` 在 nginx 擋 `404`，且 Flask 內同樣
  gate 在 `BEHIND_PROXY`（開發部署不生 relay 的 GET 副作用問題照抄既有處理）。

### Flask 的更動（`server/`）

- `crypto.py` 或 `config.py` 附近：`mitm_web_password(sid)`（HMAC，見上）。
- `run_kwargs.py`：`profile.capture` 為真時 env 加 `NCR_MITM_WEB_PASSWORD`。
  ⚠ 只跟 capture 成對送；capture 關時 entrypoint 根本不啟動 mitmweb，送了是死信。
- `views.py`（或新 `mitm_views.py`）：`open_mitm_view(sid, container)` /
  archive 時連帶收。建議獨立模組但複用 `views.port` 的 UNIQUE 仲裁。
- `app.py`：`/api/auth/mitm`——結構照抄 `auth_view()`，多回一個 `X-Mitm-Token`。
  capture 關的 session 回 404（不承認有這東西，比照現有慣例）。

### 前端的更動

- `TerminalDrawer.vue` 按鈕列（icon-btn cluster，pop-out 旁邊）加一顆
  「流量錄製介面」按鈕：`v-if="session.capture"`，
  `@click="globalThis.open(`${path}mitm/`, '_blank', 'noopener')"`。
- 對應 `drawer.spec.ts` / `components.spec.ts` 補案例（capture 開/關各一）。

## 安全分析

- **攻擊面邊界沒有變大**：mitmweb 在容器內仍綁 loopback；兄弟容器、host 網路都碰不到
  8081。新增的可達路徑只有「通過 nginx auth_request（網站 cookie + 擁有權）的瀏覽器」。
- **token 不出現在**：瀏覽器 URL（ Bearer 由 nginx 注入）、前端 JS、DB、docker logs
  （entrypoint 現在會印隨機 token 的那行，網頁路徑下變成多餘，順手把印 token 那段
  gate 在「人路徑」）。⚠ 但 token 仍在 `mitmweb` 的 **argv**
  （`--set web_password=...`）——容器內的 agent 從 `/proc/*/cmdline` 讀得到。
  這是**現狀就如此**，不是這次引入的；該 agent 反正能錄自己的流量，危害不變。
- **單人部署前提下**（見 auto-memory：實際只有 Nathan 一個使用者），
  「跨使用者的 cookie/網段攻擊」不成立；設計上仍按多租戶做。
- mitmweb 的 UI 本身等於 RCE 面（command bar 能 `script.run`）——這正是它
  11.1.2 起強制密碼的原因。我們等於把「容器 loopback」的防線換成
  「nginx auth_request + Bearer」雙層，強度不低於現狀。
- `docker exec` bridge 是控制平面發起的，**session 內容物不影響 relay 的建立**；
  relay 只認 container id（來自 DB），不吃使用者輸入 → 沒有注入面。

## 風險與開放問題

1. **每個 TCP 連線一次 `docker exec`**：mitmweb SPA 首頁約 5–8 個靜態資源
   （有 keepalive 的話複用），WS 一條長連線。量很小，但 `docker exec` 的啟動成本
   （~數十 ms）會讓首次載入有感知延遲。可接受；若不行再換常駐 exec + 自寫多工，
   那是選項 B 的複雜度。
2. **WS 長連線與 relay 清理**：分頁關了 exec 才斷。archive session 時 relay 行程
   要一起收（`views.archive()` 已有收 ttyd 的位置）。容器先死、relay 還在 →
   下次連線 exec 失敗 → 回 502 → `@view_denied` 導回首頁，行為可接受。
3. **control 重啟**：relay 是子行程，control 一沒全沒。比照 ttyd：
   `/api/auth/mitm` 沒看到活著的就**當場重建**（auth_view 的既有語義）。
4. **開發部署（BEHIND_PROXY=0）**：沒有 nginx，`/api/auth/mitm` 不該有生 relay 的
   副作用 GET（照抄 auth_view 的 gate）；前端按鈕改用既有
   `POST /api/sessions/<sid>/view` 類的顯式端點，或直接不顯示按鈕。
   → 決策點：開發部署要不要支援？建議**不支援**（文件寫清楚，按鈕在
   BEHIND_PROXY=0 時隱藏），把複雜度留給正式部署。
5. **relay port 與 ttyd port 撞號**：複用 `views.port` UNIQUE 仲裁就自動解決，
   不要自己 socket 綁 0 再記憶（TOCTOU + 跨 worker 不可見，views.py:12 的教训）。
6. **測試**：`tests/` 要加——HMAC 派生確定性、環境注入成對出現
   （capture=off 時**沒有** `NCR_MITM_WEB_PASSWORD`）、auth_mitm 的 403/404/200
   三分支、nginx conf 的 location 順序（文字檢查即可，現有沒有這種測試就加一個
   簡單的）。entrypoint 的人路徑零偏差由 test_entrypoint_human_path 守，
   更動後必跑。

## 實作順序建議

1. `entrypoint.sh` 一行（接受 `NCR_MITM_WEB_PASSWORD` 覆寫）+ entrypoint 測試
2. `run_kwargs.py` 注入 env + `test_profile_mapping`
3. `crypto/config` HMAC 派生 + 測試
4. relay（socat 進 control image + `mitm_views.py` 起收）+ 測試
5. `app.py /api/auth/mitm` + 測試
6. `deploy/nginx.conf` + 人工驗證（`nginx -t`、記得 `--force-recreate`，
   檔頭那個截斷鬼故事還沒結案）
7. `TerminalDrawer.vue` 按鈕 + 前端測試
8. 文件：本檔轉 ADR、CLAUDE.md 的耦合清單補這一條、version bump（skill 規則不適用
   claude-pty，但 repo 內 ADR 慣例要）

## 明確不做

- 不嵌入 iframe（mitmweb 送 `X-Frame-Options: DENY`，新分頁已滿足需求）。
- 不為了這個動 `NCR_MITM_WEB_BIND`（loopback 維持，relay 是唯一新通道）。
- 不新增 DB 欄位（token HMAC 派生）。
- 不碰 host port publishing（`run_kwargs.py:276` 的 ADR 不翻案）。
