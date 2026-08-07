"""帳號清單：分頁、停用的帳號不會消失、以及「不可對自己動手」那道門。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil python tests/test_user_roster.py

一律打**真正的 HTTP 端點**，不是直接呼叫 auth 的函式——分頁與權限檢查都在那一層，
測底下那層等於沒測到使用者會遇到的東西。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="roster-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "roster-secret"

from server import auth  # noqa: E402
from server.app import app  # noqa: E402
from server.db import init_db, reset_engine  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


reset_engine()
init_db()
PW = "roster-password-1"
# 名字刻意讓排序把管理員排在最前、停用的排在最後——這樣「最後一頁」才驗得到停用的那個
boss = auth.create_user("aaa-boss", PW, is_admin=True)
mate = auth.create_user("aab-mate", PW, is_admin=True)
for i in range(24):
    auth.create_user(f"u{i:02d}", PW)
off = auth.create_user("zzz-off", PW)
auth.set_active(off["id"], False)
auth.create_user("plain", PW)
# ⚠ 數出來，不要寫死。手寫的常數會在多加一個帳號時變成「測試錯了」而不是「程式錯了」，
#   而那種紅燈很容易被當成雜訊直接改掉數字——真正的分頁 bug 就是這樣混過去的。
TOTAL = len(auth.list_users())

admin = app.test_client()
admin.post("/api/auth/login", json={"username": "aaa-boss", "password": PW})
plain = app.test_client()
plain.post("/api/auth/login", json={"username": "plain", "password": PW})


print("== 分頁 ==")
d = admin.get("/api/users").get_json()
check(f"預設一頁 {config.PAGE_SIZE} 筆", len(d["users"]) == config.PAGE_SIZE)
check(f"回報總筆數 {TOTAL}（不是這一頁的長度）", d["total"] == TOTAL)
check("回應帶著 limit / offset（前端不自己猜頁大小）",
      d["limit"] == config.PAGE_SIZE and d["offset"] == 0)
check("依使用者名稱排序", [u["username"] for u in d["users"]] ==
      sorted(u["username"] for u in d["users"]))

seen, offset = [], 0
while True:
    page = admin.get(f"/api/users?offset={offset}").get_json()
    if not page["users"]:
        break
    seen += [u["username"] for u in page["users"]]
    offset += page["limit"]
check(f"翻完所有頁剛好蒐集到 {TOTAL} 個帳號，沒有重複也沒有漏",
      len(seen) == TOTAL and len(set(seen)) == TOTAL)

over = admin.get("/api/users?offset=999").get_json()
check("越界的 offset 回空的一頁，不是錯誤", over["users"] == [])
check("越界時 total 照樣是真的總數（前端要靠它把頁碼算回來）", over["total"] == TOTAL)

print("== 分頁參數是被驗過的，不是照單全收 ==")
check("limit=abc → 400", admin.get("/api/users?limit=abc").status_code == 400)
check("limit=0 → 400", admin.get("/api/users?limit=0").status_code == 400)
check(f"limit 超過上限 {config.MAX_PAGE_SIZE} → 400",
      admin.get(f"/api/users?limit={config.MAX_PAGE_SIZE + 1}").status_code == 400)
check("offset=-1 → 400", admin.get("/api/users?offset=-1").status_code == 400)

print("== 停用的帳號不會從清單上消失 ==")
# 停用是這個系統唯一的退場方式（沒有刪除，ADR 0010）。清單上看不到他，就沒有人能
# 把他復用回來，他過去開的 session 歷史也再也篩不出來。
last = admin.get(f"/api/users?offset={(TOTAL - 1) // config.PAGE_SIZE * config.PAGE_SIZE}")
names = [u["username"] for u in last.get_json()["users"]]
check(f"已停用的 zzz-off 在最後一頁上：{names[-3:]}", "zzz-off" in names)
row = next(u for u in last.get_json()["users"] if u["username"] == "zzz-off")
check("而且標得出來（is_active=false）", row["is_active"] is False)

print("== 下拉選單要的是完整名單，不是第一頁 ==")
opt = admin.get("/api/users/options").get_json()
check(f"/api/users/options 回全部 {TOTAL} 筆（沒有被分頁截斷）", len(opt["users"]) == TOTAL)
check("停用的也在裡面", any(not u["is_active"] for u in opt["users"]))
check("只回畫一個選項需要的欄位",
      set(opt["users"][0]) == {"id", "username", "is_admin", "is_active"})
check("不含密碼雜湊之類的東西",
      not any(k in opt["users"][0] for k in ("password_hash", "password_version")))
check("一般使用者拿不到（帳號名單是管理員的東西）",
      plain.get("/api/users/options").status_code == 403)
check("一般使用者也拿不到分頁的那條", plain.get("/api/users").status_code == 403)

print("== 不可對自己動手（畫面上沒有那顆按鈕，後端也要擋）==")
r = admin.patch(f"/api/users/{boss['id']}", json={"is_active": False})
check(f"停用自己 → 400：{r.get_json().get('error')}", r.status_code == 400)
check("而且真的沒被停用（不是回了 400 卻已經寫進去）",
      auth.get_user(boss["id"])["is_active"] is True)
r = admin.patch(f"/api/users/{boss['id']}", json={"is_admin": False})
check(f"降自己的權 → 400：{r.get_json().get('error')}", r.status_code == 400)
check("權限也沒真的變", auth.get_user(boss["id"])["is_admin"] is True)
r = admin.patch(f"/api/users/{boss['id']}", json={"is_admin": True})
check("提自己的權也擋（自我授權同樣不行）", r.status_code == 400)

print("== 使用者名稱：型別、長度、字元 ==")
# 都是探索性測試（2026-07-26）實際打出來的：非字串會讓 `raw.strip()` 拋
# AttributeError，回應是 500 的 HTML 錯誤頁、日誌留一整段 traceback。
for bad in (123, True, ["a"], {"a": 1}, None):
    r = admin.post("/api/users", json={"username": bad, "password": PW})
    check(f"username={bad!r} → 400 而不是 500", r.status_code == 400)
check("505 字元的名字擋下來（帳號不能刪，進去了就永遠在清單上）",
      admin.post("/api/users", json={"username": "z" * 505, "password": PW}).status_code == 400)
check(f"剛好 {config.USERNAME_MAX} 字元可以（界內不要誤殺）",
      admin.post("/api/users",
                 json={"username": "a" * config.USERNAME_MAX, "password": PW}).status_code == 201)
check("含換行/Tab 的名字擋下來（清單上與含空白的分不出來）",
      admin.post("/api/users", json={"username": "a\nb\tc", "password": PW}).status_code == 400)
check("中文名字要能建（不要無謂地擋掉非 ASCII）",
      admin.post("/api/users", json={"username": "測試員", "password": PW}).status_code == 201)
check("前後空白會被修掉而不是拒絕",
      admin.post("/api/users", json={"username": "  spaced  ", "password": PW}).status_code == 201
      and any(u["username"] == "spaced" for u in auth.list_users()))

print("== 名稱唯一性不分大小寫 ==")
check("建 Casey → 201",
      admin.post("/api/users", json={"username": "Casey", "password": PW}).status_code == 201)
r = admin.post("/api/users", json={"username": "casey", "password": PW})
check(f"再建 casey → 400：{r.get_json().get('error')}", r.status_code == 400)
check("CASEY 也擋",
      admin.post("/api/users", json={"username": "CASEY", "password": PW}).status_code == 400)
check("原本那個仍然登得進去（登入維持精確比對，沒有動到既有行為）",
      app.test_client().post("/api/auth/login",
                             json={"username": "Casey", "password": PW}).status_code == 200)

print("== 大小寫唯一性：**兩個方向**都要擋 ==")
# ⚠ 這一組非測不可。第一版寫成 `func.lower(User.username) == name.lower()`，左邊交給
#   SQLite 執行，而 SQLite 的 lower() 只處理 ASCII——於是「先建大寫再建小寫」整個穿過去，
#   而「先建小寫再建大寫」擋得下來。**只測一個方向會以為修好了**（對抗性測試 2026-07-26）。
for first, second in [("Über", "über"), ("über2", "Über2"), ("Ärger", "ärger"),
                      ("Админ", "админ"), ("Casey", "casey")]:
    admin.post("/api/users", json={"username": first, "password": PW})
    r = admin.post("/api/users", json={"username": second, "password": PW})
    check(f"先 {first} 再 {second} → 400", r.status_code == 400)

print("== 視覺上相同的名字不可以並存 ==")
admin.post("/api/users", json={"username": "café", "password": PW})   # NFD
check("NFC 的 café 與 NFD 的 café 是同一個人",
      admin.post("/api/users", json={"username": "café", "password": PW}).status_code == 400)
admin.post("/api/users", json={"username": "dupe", "password": PW})
check("全形 ｄｕｐｅ 與半形 dupe 是同一個人",
      admin.post("/api/users",
                 json={"username": "ｄｕｐｅ", "password": PW}).status_code == 400)

print("== 印得出來卻看不見的字元 ==")
# isprintable() 是 True、isspace() 是 False，所以一般的檢查穿得過去，但清單上
# `admin` 與 `adminㅤ` 肉眼完全相同。
admin.post("/api/users", json={"username": "shadow", "password": PW})
for ch, label in [("ㅤ", "HANGUL FILLER"), ("⠀", "BRAILLE BLANK"),
                  ("ᅟ", "CHOSEONG FILLER"), ("ᅠ", "JUNGSEONG FILLER"),
                  ("᠎", "MONGOLIAN VOWEL SEP")]:
    r = admin.post("/api/users", json={"username": "shadow" + ch, "password": PW})
    check(f"shadow + {label}（U+{ord(ch):04X}）→ 400", r.status_code == 400)

print("== Default_Ignorable：規則而不是幾個字元的黑名單 ==")
# 上一版是自己列的五個字元，對抗性測試當場又找出七個穿得過去的（2026-07-26）。
# ⚠ U+FFA0 特別重要：它的 NFKC **就是** U+1160，而 U+1160 本來就在名單裡——檢查做在
#   正規化之前，名單於是漏掉了自己映射過去的那個字元。
admin.post("/api/users", json={"username": "ghost", "password": PW})
for cp in (0x00AD, 0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180E, 0x200B,
           0x202E, 0x2060, 0x3164, 0xFE00, 0xFE0F, 0xFEFF, 0xFFA0, 0xE0061):
    r = admin.post("/api/users", json={"username": "ghost" + chr(cp), "password": PW})
    check(f"ghost + U+{cp:04X} → 400", r.status_code == 400)
check("U+2800 BRAILLE BLANK 也擋（它不屬於 Default_Ignorable，另外列）",
      admin.post("/api/users", json={"username": "ghost⠀", "password": PW}).status_code == 400)
# 訊息只列使用者真的打進去的碼位。聯集的話，打一個 U+3164 會被回報成「U+1160、U+3164」
# （前者是後者的 NFKC）——正確但看了會愣住，而錯誤訊息要讓人知道該改掉哪一個字。
msg = admin.post("/api/users",
                 json={"username": "ghostㅤ", "password": PW}).get_json()["error"]
check(f"打一個看不見的字元就只列一個碼位：{msg}",
      "U+3164" in msg and "U+1160" not in msg)
check("正常的組合字沒被誤殺（NFKC 之後是單一碼位）",
      admin.post("/api/users", json={"username": "Beyoncé", "password": PW}).status_code == 201)

print("== 併發建帳號不可以噴 500 ==")
# create_user 是「讀全表 → 寫一列」。SQLite 預設的 deferred 交易在 WAL 底下升級寫鎖時
# 會**當場**回 SQLITE_BUSY（busy_timeout 等的是鎖不是快照衝突），實測 4 併發 × 20 輪有
# 12.5% 回 500 `database is locked`（對抗性測試 2026-07-26）。
import threading  # noqa: E402

codes = []
codes_lock = threading.Lock()


def _spawn(i):
    cl = app.test_client()
    cl.post("/api/auth/login", json={"username": "aaa-boss", "password": PW})
    code = cl.post("/api/users", json={"username": f"race{i:02d}", "password": PW}).status_code
    with codes_lock:
        codes.append(code)


for rnd in range(8):
    threads = [threading.Thread(target=_spawn, args=(rnd * 4 + k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
check(f"32 次併發建立沒有任何 5xx（收到 {sorted(set(codes))}）",
      all(code < 500 for code in codes))
check("而且每一個都真的建起來了", sum(c == 201 for c in codes) == 32)

print("== 長度上限管的是欄寬不是碼位 ==")
check("32 個中文（64 欄）擋下來——不然帳號清單會被撐爆",
      admin.post("/api/users", json={"username": "中" * 32, "password": PW}).status_code == 400)
check("16 個中文（剛好 32 欄）可以",
      admin.post("/api/users", json={"username": "中" * 16, "password": PW}).status_code == 201)

print("== 未登入就打得到的端點不可以被非字串打成 500 ==")
for bad in (123, True, ["a"], {"a": 1}, None):
    r = app.test_client().post("/api/auth/login", json={"username": bad, "password": "x"})
    check(f"login username={bad!r} → 400", r.status_code == 400)

print("== 改自己的密碼不該把自己踢下線 ==")
# 換密碼會遞增 password_version，所有既有 cookie 失效——包含按下送出的這一台。他剛用
# 舊密碼證明過自己是本人，踢掉他換不到任何安全性，而畫面上的提示還寫著「你在**其他
# 裝置**的登入已失效」。
me = app.test_client()
auth.create_user("selfpw", PW)
me.post("/api/auth/login", json={"username": "selfpw", "password": PW})
check("改之前是登入狀態", me.get("/api/auth/me").status_code == 200)
r = me.post("/api/users/me/password", json={"old_password": PW, "new_password": PW + "x"})
check("改密碼 → 204", r.status_code == 204)
# 🔴 改密碼＝這個帳號現在連著的東西全部斷掉，**包含按下送出的這一台**。
check("🔴 改完這一台就登出了（API 回 401）", me.get("/api/auth/me").status_code == 401)
check("🔴 網頁也被送回登入頁", me.get("/").status_code in (302, 401))
other = app.test_client()
check("但別台的舊密碼已經不能用",
      other.post("/api/auth/login", json={"username": "selfpw", "password": PW}).status_code == 400)
check("新密碼可以",
      other.post("/api/auth/login",
                 json={"username": "selfpw", "password": PW + "x"}).status_code == 200)

print("== 但對別人是可以的，別把門關死了 ==")
check("停用另一位管理員 → 200",
      admin.patch(f"/api/users/{mate['id']}", json={"is_active": False}).status_code == 200)
check("他真的被停用了", auth.get_user(mate["id"])["is_active"] is False)
check("復用回來 → 200",
      admin.patch(f"/api/users/{mate['id']}", json={"is_active": True}).status_code == 200)
check("自己被擋之後仍然登得進去（沒有把自己鎖在門外）",
      admin.get("/api/auth/me").status_code == 200)

import shutil  # noqa: E402

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'done' if not _fails else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
