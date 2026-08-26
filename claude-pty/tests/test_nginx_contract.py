"""nginx 契約：Day 25 描述的授權行為，deploy/nginx.conf 必須真的長那個形狀。

    uv run python tests/test_nginx_contract.py

nginx 的行為沒辦法在這裡起真的 nginx 驗，但**設定檔的形狀是可以釘的**——這支守的是
「有人改 conf 時把授權鏈剪斷」這一類回歸：auth_request 不見了、error_page 不再導回、
內部端點被打開、CSP 被拿掉。每一條都對應 Day 25 的一句可驗證宣稱。

⚠ 這是**結構**測試不是行為測試：它證明指令都在、接對了名字，不證明 nginx 真的照做。
  行為那一半由部署後的煙霧測試（真 nginx + 真瀏覽器）負責，這裡不假裝涵蓋。
"""

import os
import re
import sys

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(_repo, "deploy", "nginx.conf")

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


src = open(CONF, encoding="utf-8").read()
# 去掉註解行再斷言：註解裡引用指令名（說明「為什麼不這樣做」）不該讓斷言假綠
code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))

print("== 路由 B：授權掛在連線交出去之前那一刻 ==")
check("session 路由套 auth_request", "auth_request /_auth_view;" in code)
check("子請求端點標 internal（外部打不到）", re.search(r"location = /_auth_view \{\s*\n\s*internal;", code) is not None)
check("子請求把 port 取回來給 proxy_pass 用", "auth_request_set $ttyd_port $upstream_http_x_ttyd_port;" in code)
check(
    "port 真的被用在 proxy_pass（取回來卻沒用＝路由斷掉）",
    re.search(r"proxy_pass http://\$\w+:\$ttyd_port;", code) is not None,
)

print("== 未授權 → 302 導回，不露 403 裸頁 ==")
# ⚠ 驗的是**性質**不是字面：401/403 與 5xx 都要接到那個具名 location。
#   只列 401 403 的話，auth_request 的其餘失敗（control 不在、X-Ttyd-Port 空）會漏出
#   nginx 的裸錯誤頁，而抽屜會把它當終端顯示（審查 F-012）。
_ep = re.search(r"error_page ([\d ]+)= @view_denied;", code)
check("有一條 error_page 接到具名 location", _ep is not None)
_codes = set((_ep.group(1) if _ep else "").split())
check("401/403 都接到", {"401", "403"} <= _codes)
check("🔴 5xx 也接得到（auth_request 的其餘失敗不可以漏出裸錯誤頁）", bool(_codes & {"500", "502", "503", "504"}))
check("導回首頁（302）", re.search(r"location @view_denied \{\s*\n\s*return 302 /;", code) is not None)

print("== 路由 C：流量畫面（mitmweb UI，ADR 0021）==")
check("mitm 路由套 auth_request", "auth_request /_auth_mitm;" in code)
check("子請求端點標 internal（外部打不到）", re.search(r"location = /_auth_mitm \{\s*\n\s*internal;", code) is not None)
check("取回 relay 的 port", "auth_request_set $mitm_port  $upstream_http_x_mitm_port;" in code)
check("取回這一場的 token", "auth_request_set $mitm_token $upstream_http_x_mitm_token;" in code)
# 🔴 **token 只能由 nginx 注入。** 取回來卻沒用＝mitmweb 對每一發請求回 403，而畫面上
#    看起來是「一按就跳回首頁」；而如果改成讓瀏覽器帶（`?token=`），那串明文就進了
#    網址列、瀏覽紀錄與任何一次複製貼上。
check(
    "🔴 token 由 nginx 組成 Bearer 注入（同時蓋掉 client 自己送的 Authorization）",
    'proxy_set_header Authorization "Bearer $mitm_token";' in code,
)
_mitm_pass = re.search(r"proxy_pass http://\$mitm_upstream:\$mitm_port(\S*);", code)
check("port 真的被用在 proxy_pass（取回來卻沒用＝路由斷掉）", _mitm_pass is not None)
# 🔴 proxy_pass 帶變數時，**原本的 query string 不會自動接上**。少了 $is_args$args，
#    SPA 靠 query 傳的篩選條件會整個消失——請求成功、答案卻是別的問題，畫面上看不出來。
check(
    "🔴 帶上 $is_args$args（否則 query string 被丟掉，而且是無聲的）",
    _mitm_pass is not None and _mitm_pass.group(1).endswith("$is_args$args"),
)
check(
    "🔴 尾斜線由 308 補（SPA 是路徑相對的，少了它資源會解析到 ttyd 那條路由）",
    re.search(r"location ~ \^/session/\(\?<\w+>\[A-Za-z0-9\]\+\)/mitm\$ \{\s*\n\s*return 308 ", code) is not None,
)
check("WebSocket 升級（mitmweb 的 /updates）", code.count('proxy_set_header Connection "upgrade";') >= 2)
_mitm_ep = re.search(r"error_page ([\d ]+)= @view_denied;[\s\S]*?\$mitm_port", code)
check(
    "🔴 5xx 也接得到（沒開錄製時 /api/auth/mitm 回 404，auth_request 會把它當 5xx）",
    _mitm_ep is not None and bool(set(_mitm_ep.group(1).split()) & {"500", "502", "503", "504"}),
)
# 🔴 **順序**：nginx 取第一個命中的 regex location，而 `^/session/<sid>/` 也吃得下
#    `/session/<sid>/mitm/…`。排錯的話這兩條一條都不會被走到，而且沒有任何錯誤——
#    使用者按下「流量畫面」看到的會是終端。
_i_bare = code.find("/mitm$")
_i_rest = code.find("/mitm(?<")
_i_ttyd = code.find("location ~ ^/session/(?<claude_pty_sid>")
check("兩條 mitm 路由都在", _i_bare > 0 and _i_rest > 0 and _i_ttyd > 0)
check("🔴 兩條都排在 ttyd 那條 regex **之前**（否則永遠不會被命中）", _i_bare < _i_ttyd and _i_rest < _i_ttyd)

print("== 內部端點對外一律 404（不承認存在）==")
for ep in ("/api/auth/view", "/api/auth/check", "/api/auth/mitm"):
    check(f"{ep} 對外 404", re.search(rf"location = {re.escape(ep)} \{{ return 404; \}}", code) is not None)

print("== CSP：終端只給同源嵌（抽屜是 same-origin iframe）==")
check(
    "frame-ancestors 'self'（不可以是 DENY/'none'，抽屜會變空白）",
    "add_header Content-Security-Policy \"frame-ancestors 'self'\" always;" in code,
)

print("== 登入限流（argon2 很貴，公開端點不限流會被錯密碼打爆）==")
check("login 有獨立限流 zone", "limit_req_zone" in code and "rate=10r/m" in code)
# 🔴 **宣告一個 zone 不等於用它。** 這裡原本只驗宣告，於是刪掉 nginx.conf 的
#    `limit_req zone=claude_pty_login ...` 那一行，argon2 登入就完全不限流——正是這段標題
#    講的那件事——而兩條斷言照樣全綠（審查 F-011）。`limit_req_status` 單獨存在也是沒有
#    作用的。用同一份「擷取 location 區塊」的手法（下面 upload 那條已經在用）驗它真的在
#    login 的 location 裡面，而不是檔案裡某處。
_login = re.search(r"location = /api/auth/login \{([^}]*)\}", code)
check("login 有自己的 location", _login is not None)
check(
    "🔴 限流真的套在那個 location 裡（宣告 zone 不等於用它）",
    _login is not None and "limit_req zone=claude_pty_login" in _login.group(1),
)
check(
    "429 不是 503（讓打的人知道是被限流，不是伺服器掛了）",
    _login is not None and "limit_req_status 429;" in _login.group(1),
)

print("== 上傳：body 上限只放寬在那一條，不是全站 ==")
check("全站上限仍是小的（4m）", "client_max_body_size 4m;" in code)
_up = re.search(r"location ~ \^/api/sessions/\[A-Za-z0-9\]\+/upload\$ \{([^}]*)\}", code)
check("upload 有自己的 location", _up is not None)
check(
    "上限放寬在裡面（12m，略大於 Flask 端的 10MB＋multipart 開銷）",
    _up is not None and "client_max_body_size 12m;" in _up.group(1),
)

print("== 前端：頁面路由不寫死在主檔，assets 直出且長快取 ==")
# ⚠ 這一節原本守的是「切換器只加分支、不動舊路」。切換器沒了（legacy 於 2026-08-26 拆除），
#   但 include 這條仍然要守：那份片段裡有一整套「為什麼是 `location =` 而不是前綴、
#   為什麼 `try_files` 是錯的」的說明，搬進主檔只會讓它多七十行不相干的東西。
#   而 glob 沒命中不是錯誤，所以掛載掉了 nginx 照樣起得來、三條路由落回 `location /`
#   去 proxy 給 Flask（Flask 也吐同一份殼），那是刻意留的軟著陸。
check(
    "頁面路由由外部檔案帶進來（glob 沒命中就落到 location /）",
    "include /etc/nginx/claude-pty-ui/*.conf;" in code,
)
_assets = re.search(r"location /assets/ \{([^}]*)\}", code)
check("/assets/ 有自己的 location（不 proxy 給 Flask）", _assets is not None)
check(
    "從檔案系統直出（root），不是 proxy_pass",
    _assets is not None and "root /usr/share/nginx/html;" in _assets.group(1) and "proxy_pass" not in _assets.group(1),
)
check(
    "長快取（Vite 把內容雜湊寫進檔名，改版就換檔名）",
    _assets is not None and "max-age=31536000" in _assets.group(1) and "immutable" in _assets.group(1),
)
# 🔴 **不可以有 `always`。** 沒有它時 add_header 只套在 2xx／3xx 上；加了它連 404 也會帶著
#    `immutable, max-age=31536000`。改版之後舊的殼會去要一個已經不存在的
#    `index-<舊雜湊>.js`，那一發 404 被快取一年，**清了快取才救得回來**，而症狀是一片白
#    畫面、看不出跟快取有關（完整審查 L1）。
check(
    "🔴 快取標頭沒有 always（404 不可以帶著 immutable 被快取一年）",
    _assets is not None and "always" not in _assets.group(1),
)
# 🔴 `expires` 自己就會送一個 Cache-Control。兩個一起用＝同一個標頭回兩份，而「聽哪一份」
#    是實作決定的——一個要快取一年的資源不該讓那件事變成問題。
check(
    "🔴 沒有 expires（它會與 add_header 各送一份 Cache-Control）",
    _assets is not None and "expires" not in _assets.group(1),
)

_ui_dir = os.path.join(_repo, "deploy", "nginx-ui")
_vue = os.path.join(_ui_dir, "vue", "ui.conf")
check("vue 的片段存在", os.path.isfile(_vue))
# ⚠ 這裡曾經有三條在守 `nginx-ui/legacy/ui.conf`（存在、而且**一條指令都沒有**）。
#   那三條守的是「兩版並存期間 legacy 行為一個字不變」，而 legacy 於 2026-08-26 拆除，
#   那個目錄也一起刪了，性質本身不再存在。下面「vue 的片段長什麼樣」那幾條照舊。
if os.path.isfile(_vue):
    _vue_code = "\n".join(
        ln for ln in open(_vue, encoding="utf-8").read().splitlines() if not ln.lstrip().startswith("#")
    )
    for _page in ("/", "/login", "/account"):
        # 精確比對（`location =`）：前綴比對會把 /static/* 的字體與圖示一起吃掉
        check(
            f"vue 片段有 {_page} 的精確路由",
            re.search(rf"location = {re.escape(_page)} \{{", _vue_code) is not None,
        )
    # 🔴 **這一條抓的是「三個頁面全部 404」。** `try_files $uri /index.html;` 的最後一個
    #    參數是 URI，nginx 會做**內部轉向**：把 `/index.html` 當成新請求重跑一次 location
    #    比對，而它不符合上面三條精確比對，於是落到 `location /` 被 proxy 給 Flask——
    #    Flask 沒有這條路由，回 404。正解是最後一個參數用 `=404`，讓 `/index.html` 被當成
    #    **檔案路徑**直接送出，完全不重新比對。
    check("三條都直接送檔，不做內部轉向", _vue_code.count("try_files /index.html =404;") == 3)
    check(
        "🔴 沒有留下會內部轉向的寫法（那會讓三個頁面全部 404）",
        "try_files $uri /index.html" not in _vue_code,
    )
    # 這三條路不經 Flask，`_security_headers` 那支 after_request 完全沒機會跑。
    _app_src = open(os.path.join(_repo, "server", "app.py"), encoding="utf-8").read()
    for _hdr, _needle in (
        ("Content-Security-Policy", "frame-ancestors 'none'"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "same-origin"),
        ("X-Frame-Options", "DENY"),
    ):
        check(
            f"🔴 nginx 直出的殼也要有 {_hdr}（不經 Flask＝那支 after_request 不會跑）",
            _vue_code.count(f'add_header {_hdr} "') == 3 and _needle in _vue_code,
        )
        # 值不可以與 Flask 那份分岔：兩條路對同一個頁面該給同一組標頭。
        check(f"　└ 值與 server/app.py 對得上（{_needle}）", _needle in _app_src)
    check(
        "🔴 每一條 add_header 都帶 always（預設只對 2xx/3xx 生效）",
        _vue_code.count("add_header") == _vue_code.count("always;"),
    )
    # 殼被快取的話，改版後拿到的舊殼會去要一個已經不存在的 /assets/*.js——一片白畫面
    check("🔴 SPA 的殼不可快取（no-store）", _vue_code.count('add_header Cache-Control "no-store" always;') == 3)

print("== session 編號不可猜 ==")
# 路由只認 [A-Za-z0-9]+；sid 本體是 uuid4 的 hex 截 12 碼（48 bits 隨機）。
# 這裡釘住「隨機來源沒有被換成可預測的東西」——流水號或時間戳都會讓
# 「拿不到清單就猜編號」變成可行攻擊（授權那層仍會擋，但存在性就先洩漏了）。
check(
    "nginx 路由收 [A-Za-z0-9]+ 的 sid",
    re.search(r"location ~ \^/session/\(\?<\w+>\[A-Za-z0-9\]\+\)/", code) is not None,
)
_sessions_src = open(os.path.join(_repo, "server", "sessions.py"), encoding="utf-8").read()
check("sid 來自 uuid4（不是流水號/時間戳）", "uuid.uuid4().hex[:12]" in _sessions_src)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
