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
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


src = open(CONF, encoding="utf-8").read()
# 去掉註解行再斷言：註解裡引用指令名（說明「為什麼不這樣做」）不該讓斷言假綠
code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))

print("== 路由 B：授權掛在連線交出去之前那一刻 ==")
check("session 路由套 auth_request", "auth_request /_auth_view;" in code)
check("子請求端點標 internal（外部打不到）",
      re.search(r"location = /_auth_view \{\s*\n\s*internal;", code) is not None)
check("子請求把 port 取回來給 proxy_pass 用",
      "auth_request_set $ttyd_port $upstream_http_x_ttyd_port;" in code)
check("port 真的被用在 proxy_pass（取回來卻沒用＝路由斷掉）",
      re.search(r"proxy_pass http://\$\w+:\$ttyd_port;", code) is not None)

print("== 未授權 → 302 導回，不露 403 裸頁 ==")
# ⚠ 驗的是**性質**不是字面：401/403 與 5xx 都要接到那個具名 location。
#   只列 401 403 的話，auth_request 的其餘失敗（control 不在、X-Ttyd-Port 空）會漏出
#   nginx 的裸錯誤頁，而抽屜會把它當終端顯示（審查 F-012）。
_ep = re.search(r"error_page ([\d ]+)= @view_denied;", code)
check("有一條 error_page 接到具名 location", _ep is not None)
_codes = set((_ep.group(1) if _ep else "").split())
check("401/403 都接到", {"401", "403"} <= _codes)
check("🔴 5xx 也接得到（auth_request 的其餘失敗不可以漏出裸錯誤頁）",
      bool(_codes & {"500", "502", "503", "504"}))
check("導回首頁（302）",
      re.search(r"location @view_denied \{\s*\n\s*return 302 /;", code) is not None)

print("== 內部端點對外一律 404（不承認存在）==")
for ep in ("/api/auth/view", "/api/auth/check"):
    check(f"{ep} 對外 404",
          re.search(rf"location = {re.escape(ep)} \{{ return 404; \}}", code) is not None)

print("== CSP：終端只給同源嵌（抽屜是 same-origin iframe）==")
check("frame-ancestors 'self'（不可以是 DENY/'none'，抽屜會變空白）",
      'add_header Content-Security-Policy "frame-ancestors \'self\'" always;' in code)

print("== 登入限流（argon2 很貴，公開端點不限流會被錯密碼打爆）==")
check("login 有獨立限流 zone", "limit_req_zone" in code and "rate=10r/m" in code)
# 🔴 **宣告一個 zone 不等於用它。** 這裡原本只驗宣告，於是刪掉 nginx.conf 的
#    `limit_req zone=claude_pty_login ...` 那一行，argon2 登入就完全不限流——正是這段標題
#    講的那件事——而兩條斷言照樣全綠（審查 F-011）。`limit_req_status` 單獨存在也是沒有
#    作用的。用同一份「擷取 location 區塊」的手法（下面 upload 那條已經在用）驗它真的在
#    login 的 location 裡面，而不是檔案裡某處。
_login = re.search(r"location = /api/auth/login \{([^}]*)\}", code)
check("login 有自己的 location", _login is not None)
check("🔴 限流真的套在那個 location 裡（宣告 zone 不等於用它）",
      _login is not None and "limit_req zone=claude_pty_login" in _login.group(1))
check("429 不是 503（讓打的人知道是被限流，不是伺服器掛了）",
      _login is not None and "limit_req_status 429;" in _login.group(1))

print("== 上傳：body 上限只放寬在那一條，不是全站 ==")
check("全站上限仍是小的（4m）", "client_max_body_size 4m;" in code)
_up = re.search(r"location ~ \^/api/sessions/\[A-Za-z0-9\]\+/upload\$ \{([^}]*)\}", code)
check("upload 有自己的 location", _up is not None)
check("上限放寬在裡面（12m，略大於 Flask 端的 10MB＋multipart 開銷）",
      _up is not None and "client_max_body_size 12m;" in _up.group(1))

print("== session 編號不可猜 ==")
# 路由只認 [A-Za-z0-9]+；sid 本體是 uuid4 的 hex 截 12 碼（48 bits 隨機）。
# 這裡釘住「隨機來源沒有被換成可預測的東西」——流水號或時間戳都會讓
# 「拿不到清單就猜編號」變成可行攻擊（授權那層仍會擋，但存在性就先洩漏了）。
check("nginx 路由收 [A-Za-z0-9]+ 的 sid",
      re.search(r"location ~ \^/session/\(\?<\w+>\[A-Za-z0-9\]\+\)/", code) is not None)
_sessions_src = open(os.path.join(_repo, "server", "sessions.py"), encoding="utf-8").read()
check("sid 來自 uuid4（不是流水號/時間戳）", "uuid.uuid4().hex[:12]" in _sessions_src)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
