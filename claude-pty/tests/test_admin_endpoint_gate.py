"""管理員限定的端點，後端真的掛著 `@admin_only`。

    uv run python tests/test_admin_endpoint_gate.py

不需要 docker、不需要瀏覽器，也不需要起服務。這是**結構**測試不是行為測試。

## 它是 `test_template_contract.py` 的遺產

那一支原本守三件事，2026-08-26 拆掉 legacy 之後前兩件的**對象消失了**：

1. 模板宣稱的 class 在 CSS 裡都要有 —— `server/templates/` 整個目錄刪了。
2. 模板內嵌的 `<script>` 語法要過 —— 同上。那條性質現在由前端六關的 `vue-tsc` 與
   `vite build` 接手，而且接得更緊（它們看得到型別與打包，`node --check` 只看得到語法）。
3. 管理頁的區塊要被 gate 住 —— 這一件**還在**，但它從一個問題裂成兩個：
   · **畫面上不畫**：現在是 Vue 的事，`frontend/src/__tests__/account.spec.ts` 有一條
     「非管理員不會打 `/api/ttyd/inspect`」在守。
   · **後端擋得住**：就是這一支。

⚠ 為什麼第 3 件的後半要單獨留一支測試，而不是「反正後端有 `@admin_only`」：
  **前端的 gate 只是禮貌，後端那一行才是門。** 前端不畫某個區塊，只代表使用者不會
  「不小心」打到那條 API；直接開 devtools 打一發是零成本的。這條測試守的是那個門
  真的在，而不是門的招牌畫得好不好看。
⚠ 端點名**從前端原始碼撈**，不是寫死在這裡：寫死的話，前端哪天改打另一條
  admin API，這裡會繼續對著一條沒有人在用的舊路徑喊綠燈。
"""

import os
import re
import sys

_fails = 0


def check(label, ok, detail=""):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail and not ok else ""))
    return ok


_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(_repo, "server", "app.py")
FRONTEND = os.path.join(_repo, "frontend", "src")

app_src = open(APP, encoding="utf-8").read()

print("== 前端打的 admin 端點，後端必須掛著 @admin_only ==")

# 掃整個前端原始碼，撈出它打的每一條 `/api/ttyd/*`。
# ⚠ 掃 `src/` 而不是只掃某一個元件：日後那段搬去別的檔案時，這裡不會靜靜地什麼都撈不到。
# ⚠ 排除 `__tests__/`：那裡面的字串是**假的**（vitest 的 mock 路由表），撈進來的話
#   「前端真的在打這條」就變成「測試檔裡提過這條」，而那兩件事可以完全無關。
found: set[str] = set()
for root, _dirs, files in os.walk(FRONTEND):
    if "__tests__" in root.split(os.sep):
        continue
    for name in files:
        if not name.endswith((".ts", ".vue")):
            continue
        text = open(os.path.join(root, name), encoding="utf-8").read()
        found |= set(re.findall(r'["\'](/api/ttyd/[a-z]+)["\']', text))

check(f"前端打的 ttyd 端點抓得出來（找到 {sorted(found) or '無'}）", bool(found))

for ep in sorted(found):
    # 兩行必須**相鄰**：`@app.get(...)` 緊接著 `@admin_only`。裝飾器的順序在 Flask 是
    # 由下往上套的，中間插進別的東西時語意會變，而「還在同一個路由上」不等於「還擋得住」。
    route = re.search(rf'@app\.get\("{re.escape(ep)}"\)\s*\n@admin_only\b', app_src)
    check(
        f"🔴 {ep} 上面就是 @admin_only（前端 gate 只是禮貌，這條才是門）",
        route is not None,
        "找不到對應的路由，或它與 @admin_only 之間插了別的東西",
    )

# 反向：這條 regex 真的抓得到「沒掛」的情況嗎。抓不到的話上面每一條都是恆真的。
_fake = '@app.get("/api/ttyd/inspect")\ndef inspect():\n    pass\n'
check(
    "🔴 而且它抓得出「沒掛 @admin_only」（同一條 regex 對假 markup 不可以命中）",
    re.search(r'@app\.get\("/api/ttyd/inspect"\)\s*\n@admin_only\b', _fake) is None,
)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
