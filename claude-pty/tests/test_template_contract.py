"""模板契約：宣稱的樣式真的存在、內嵌的 JS 真的解析得過、管理頁真的被 gate 住。

    uv run python tests/test_template_contract.py

不需要 docker、不需要瀏覽器，也不需要起服務。這是**結構**測試不是行為測試。

## 為什麼需要這一支

三個洞，每一個都不會在任何既有測試裡變紅：

1. **模板寫了一個 CSS 裡不存在的 class。** 畫面照樣渲染，只是沒有樣式——而在一個
   `data-tone="danger"` 用來表示「這裡出事了」的介面裡，樣式缺席等於警示消失。
   2026-08-08 寫 ttyd 那一節時當場犯了三個（`panel__head`、`.muted`、以及一個我以為
   不存在其實存在的），三個都是「搜尋找得到、執行時不存在」那一族。
2. **模板裡的 `<script>` 語法錯誤。** `run-all.sh` 只驗 `static/js/app.js`，驗不到
   模板內嵌的那些。而它壞掉的症狀跟 app.js 壞掉一樣難查：整頁沒有 JS，看起來像後端沒回應。
3. **管理頁面的區塊沒有被 `{% if user.is_admin %}` 包住。** 後端那條 API 有
   `@admin_only`，但區塊本身若對一般使用者也渲染，他會看到一張永遠載入失敗的表格，
   而且知道有這個東西存在。

⚠ `fa-*` 是 Font Awesome 的，不在 app.css 裡，一律排除。
"""

import os
import re
import subprocess
import sys
import tempfile
from glob import glob

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = sorted(glob(os.path.join(_ROOT, "server", "templates", "*.html")))
CSS = os.path.join(_ROOT, "server", "static", "css", "app.css")
APP = os.path.join(_ROOT, "server", "app.py")

_fails = 0


def check(label, ok, detail=""):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n         {detail}" if detail and not ok else ""))


def read(p):
    return open(p, encoding="utf-8").read()


print("== 模板宣稱的 class，CSS 裡都要有 ==")
# 定義端：`.foo` 出現在 CSS 的任何位置（含組合選擇器）都算定義過。
defined = set(re.findall(r"\.([A-Za-z][\w-]*)", read(CSS)))
check("CSS 解析得到類別（解不到的話下面每一條都會假綠）", len(defined) > 50, str(len(defined)))
for path in TEMPLATES:
    src, used = read(path), set()
    # ⚠ 連 JS 樣板字串裡的 `class="..."` 一起掃。這一節大半的 markup 是 JS 產生的，
    #   只掃靜態 HTML 的話正好漏掉最容易寫錯的那一半。
    for m in re.finditer(r'class="([^"{}]+)"', src):
        used.update(m.group(1).split())
    missing = sorted(c for c in used - defined if not c.startswith("fa-"))
    check(f"{os.path.basename(path)}（用了 {len(used)} 個）", not missing, "CSS 裡找不到：" + "、".join(missing))


print("\n== 模板內嵌的 <script> 要解析得過 ==")
# ⚠ 沒有 node 就跳過並**講出來**。靜靜略過會讓「全部通過」看起來涵蓋了這一項。
if subprocess.run(["which", "node"], capture_output=True).returncode != 0:
    print("  SKIP  host 上沒有 node，這一節沒有被驗證（不是通過）")
else:
    for path in TEMPLATES:
        blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", read(path), re.S)
        if not blocks:
            continue
        # Jinja 先換掉再驗語法：`{{ x }}` 是值、`{% %}` 與 `{# #}` 不產生 JS。
        # 換成常數只夠驗**語法**，驗不了「if 分支各自產生的 JS 是否都合法」——那一半
        # 這裡涵蓋不到，不假裝涵蓋。
        js = "\n".join(blocks)
        js = re.sub(r"\{#.*?#\}", "", js, flags=re.S)
        js = re.sub(r"\{\{.*?\}\}", "0", js, flags=re.S)
        js = re.sub(r"\{%.*?%\}", "", js, flags=re.S)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
        tmp.write(js)
        tmp.close()
        r = subprocess.run(["node", "--check", tmp.name], capture_output=True, text=True)
        os.unlink(tmp.name)
        check(
            f"{os.path.basename(path)} 的內嵌 script",
            r.returncode == 0,
            (r.stderr.strip().splitlines() or [""])[-1][:160],
        )


print("\n== ttyd 實況那一節：admin 限定，而且前後端對得上 ==")
account = read(TEMPLATES and [p for p in TEMPLATES if p.endswith("account.html")][0])
app = read(APP)

check("區塊在（testid 是 e2e 的抓手）", 'data-testid="ttyd-views"' in account)


# ⚠ **真的 render 一次，不要用字串位置算 Jinja 巢狀。**
#   第一版寫成「`{% if user.is_admin %}` 的位置 < 面板的位置 < 最後一個 `{% endif %}`」，
#   而這份模板的 `<script>` 裡另有一組 `{% if gitlab_enabled %}…{% endif %}`，於是
#   `rfind` 撈到的永遠是那一個。把 admin 的 endif 整個刪掉，那條斷言照樣是綠的
#   （2026-08-08 變異測試抓到）。位置算術會在有人插入一段之後靜靜失效，而失效的方向
#   是**放行**——這正是這條斷言最不該出錯的方向。
def render_account(is_admin: bool) -> str:
    from jinja2 import DictLoader, Environment

    env = Environment(
        loader=DictLoader(
            {
                # base 與 masthead 換成最小樁：這一節要驗的是 account.html 自己的分支，
                # 不是版面。樁裡保留 block 名稱，繼承鏈才接得起來。
                "base.html": "{% block body %}{% endblock %}{% block scripts %}{% endblock %}",
                "_masthead.html": "",
                "account.html": account,
            }
        ),
        autoescape=True,
    )
    return env.get_template("account.html").render(
        user={"id": 1, "username": "u", "is_admin": is_admin},
        active="account",
        behind_proxy=False,
        min_password_length=8,
        name_max=64,
        username_max=32,
        credentials={},
        default_cli="claude",
        gitlab_enabled=True,
        gitlab_host="gitlab.example.com",
        gitlab_pat_set=False,
        gitlab_proxy_error=None,
    )


try:
    as_admin, as_user = render_account(True), render_account(False)
except Exception as e:  # noqa: BLE001 — render 不起來就是這一節失效，要講出來
    check("模板 render 得起來（render 不了就驗不了下面兩條）", False, f"{type(e).__name__}: {e}")
else:
    check("管理員看得到這一節", 'data-testid="ttyd-views"' in as_admin)
    check(
        "🔴 一般使用者**看不到**這一節（後端有 @admin_only，但畫面不該先洩漏它存在）",
        'data-testid="ttyd-views"' not in as_user,
    )
    check(
        "順帶：帳號清單也只給管理員（同一個 gate，一起守）",
        'data-testid="roster"' in as_admin and 'data-testid="roster"' not in as_user,
    )

m = re.search(r'api\("(/api/ttyd/[a-z]+)"\)', account)
check("前端打的端點抓得出來", m is not None)
if m:
    ep = m.group(1)
    route = re.search(rf'@app\.get\("{re.escape(ep)}"\)\s*\n@admin_only\b', app)
    check(
        f"🔴 後端有 {ep} 且掛著 @admin_only（前端 gate 只是禮貌，這條才是門）",
        route is not None,
        "找不到對應的路由，或它上面沒有 @admin_only",
    )

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
