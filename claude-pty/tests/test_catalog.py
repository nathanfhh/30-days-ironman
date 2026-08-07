"""模型清單與驗證（/api/catalog + _check_model_effort）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        python tests/test_catalog.py

守的性質：
  🔴 malformed 輸入不可以變成 500（dict/list 對 frozenset 取雜湊會 TypeError）。
  🔴 模型清單對誰都一樣——沒有按身分過濾的分支。
  🔴 default_model 一律讀 DEFAULT_MODEL，不是「清單的第一個」。
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="claude-pty-catalog-")
os.environ["CLAUDE_PTY_DB_PATH"] = os.path.join(_tmp, "test.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config, db  # noqa: E402

config.DB_URL = f"sqlite:///{os.environ['CLAUDE_PTY_DB_PATH']}"
db.reset_engine()
db.init_db()

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


# ⚠ 兩道保險缺一不可（曾經兩件都沒做，而且用 `!= 400` 斷言：容器建失敗回 500，
#   500 也 != 400，測試照樣綠、真容器照樣被建出來）：
#     · MOUNTS 清空——擋掉掛載
#     · docker 換成會爆的替身——擋掉「容器被建出來」本身，並讓漏網的路徑當場現形
config.MOUNTS = {}


class _NoDocker:
    class containers:
        @staticmethod
        def run(*a, **kw):
            raise AssertionError("這支測試不該建立任何容器")

        @staticmethod
        def list(**kw):
            return []


from server import auth  # noqa: E402
from server.app import app  # noqa: E402
import server.app as app_mod  # noqa: E402

app_mod.manager._docker = _NoDocker()

uid = auth.create_user("catalog-tester", "catalog-password-1", is_admin=True)["id"]
c = app.test_client()
with c.session_transaction() as sess:
    sess["uid"] = uid
    sess["pwv"] = auth.get_user(uid)["password_version"]


def rejected(as_admin=True, **kw):
    """把 profile 丟進驗證，回傳 (狀態碼, 錯誤訊息)；`None` 代表**通過驗證**。

    直接呼叫 `_check_model_effort` 而不是打端點：通過的那些若走端點就會繼續往下建容器，
    而這支測試的斷言全部只關心「驗證放不放行」。負向的幾條另外用端點再驗一次（見下），
    確認這道閘真的接在 API 上、不是只有函式自己對。
    """
    with app.test_request_context():
        from flask import g
        g.user = {"id": uid, "is_admin": as_admin}
        try:
            app_mod._check_model_effort(kw)
            return None
        except app_mod.BadInput as e:
            return 400, str(e)


print("== 白名單驗證 ==")
check("合法組合通過驗證（不是「不等於 400」那種弱斷言）",
      rejected(model="opus", effort="high") is None)
check("不存在的模型 → 400", (rejected(model="nope-9") or (0,))[0] == 400)
check("不存在的 effort → 400", (rejected(model="opus", effort="turbo") or (0,))[0] == 400)

# 🔴 malformed 輸入不可以變成 500（dict/list 對 frozenset 取雜湊會 TypeError）。
#    這幾條**走真的端點**：它們一定在建容器之前就被擋下。
for bad in ({}, [], {"x": 1}, 3):
    got = c.post("/api/sessions", json={"profile": {"cli": "claude", "model": bad}})
    check(f"🔴 model={bad!r} → 400 而不是 500", got.status_code == 400)
check("🔴 effort 給非字串也是 400",
      c.post("/api/sessions", json={"profile": {"cli": "claude", "effort": []}})
      .status_code == 400)
check("這道閘真的接在端點上（不是只有函式自己對）",
      c.post("/api/sessions", json={"profile": {"cli": "claude", "model": "gpt-9-nope"}})
      .status_code == 400)

print("== /api/catalog：表單的資料來源 ==")
d = c.get("/api/catalog").get_json()
check("有 claude 的清單", "claude" in d)
check("claude 標成 static（它本來就不是抓來的）", d["claude"]["source"] == "static")
check("每顆模型都帶得出 efforts 與預設",
      all(m.get("efforts") and m.get("default_effort") for m in d["claude"]["models"]))
# 🔴 沒有選擇可沿用時要落在**設定的預設**，不是「清單的第一個」。兩者曾經是同一件事，
#    直到白名單順序（sonnet 在前）讓退路落在 sonnet 而不是 opus。
check("🔴 claude 的 default_model 是設定的預設（不是清單第一個的巧合）",
      d["claude"]["default_model"] == config.DEFAULT_MODEL == "opus")
# 🔴 選單順序＝`config.CLAUDE_MODELS` 的順序，一份就好。排列回答「有哪些、怎麼排」，
#    預設回答「沒選擇可沿用時落在哪」，兩者刻意不綁在一起。
check("🔴 claude 清單順序＝白名單順序（選單順序的 SSOT 只有一份）",
      [m["slug"] for m in d["claude"]["models"]] == list(config.CLAUDE_MODELS))
check("🔴 haiku 在清單裡而且排最後（實測 CLI 收這個別名）",
      d["claude"]["models"][-1]["slug"] == "haiku")

print("== 模型清單對誰都一樣（沒有按身分過濾的分支）==")
# 這裡沒有「限管理員的模型」。清單只有一種形狀，驗證只看白名單不看身分——
# 這兩條釘住「沒有第二種形狀」：哪天有人加回身分過濾，先在這裡現形。
plain_id = auth.create_user("catalog-plain", "catalog-password-2")["id"]
c2 = app.test_client()
with c2.session_transaction() as sess:
    sess["uid"] = plain_id
    sess["pwv"] = auth.get_user(plain_id)["password_version"]
d2 = c2.get("/api/catalog").get_json()
check("🔴 非管理員拿到的清單與管理員一字不差",
      d2["claude"] == c.get("/api/catalog").get_json()["claude"])
check("🔴 非管理員選任何白名單內的模型都通過驗證",
      all(rejected(as_admin=False, model=m) is None for m in config.CLAUDE_MODELS))

print("== 清理 ==")
db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
check("暫存 DB 已清除", not os.path.exists(_tmp))

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
