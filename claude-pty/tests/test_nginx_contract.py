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
check("401/403 都接到具名 location", "error_page 401 403 = @view_denied;" in code)
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
check("429 不是 503（讓打的人知道是被限流，不是伺服器掛了）",
      "limit_req_status 429;" in code)

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
