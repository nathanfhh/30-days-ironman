"""檔案上傳（貼圖）：唯一一條使用者能往伺服器寫東西的路，所以把關要密。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with cryptography python tests/test_upload.py

守的性質（每一條對應 app.upload_file 的一道閘）：
  🔴 副檔名白名單、大小上限、**路徑穿越**（三道各自獨立成立）
  🔴 反 CSRF：multipart 例外只對這個端點開，且仍要求 form 設不了的自訂標頭
  🔴 授權：非擁有者回 404（不洩漏存在性），未登入回 401
  🔴 落點在他自己的 persistent-data/uploads，回的是**容器內**路徑（人要貼進終端）
"""

import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="upload-test-")
os.environ["CLAUDE_PTY_DB_PATH"] = os.path.join(TMP, "t.db")
# 空間指進 tmpdir：上傳會真的寫檔，絕不能落在使用者真實家目錄。
os.environ["CLAUDE_PTY_SPACE"] = os.path.join(TMP, "space")
os.environ["CLAUDE_PTY_SPACE_SELF"] = os.path.join(TMP, "space")

from server import config, db  # noqa: E402

config.DB_URL = f"sqlite:///{os.environ['CLAUDE_PTY_DB_PATH']}"
config.SECRET_KEY = "upload-test-secret"

from server import auth  # noqa: E402
from server.app import app  # noqa: E402
from server.db import session_scope  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402

db.reset_engine()
db.init_db()

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


alice = auth.create_user("alice", "alice-password-1")
bob = auth.create_user("bob", "bob-password-1")
with session_scope() as s:
    s.add(SessionRow(id="sess-alice", container_name="claude-pty-sess-alice", user_id=alice["id"], status="running"))
    s.add(SessionRow(id="sess-bob", container_name="claude-pty-sess-bob", user_id=bob["id"], status="running"))

ca = app.test_client()
ca.post("/api/auth/login", json={"username": "alice", "password": "alice-password-1"})

H = {"X-Requested-With": "fetch"}  # 後端要求的反 CSRF 標頭


def send(client, sid, filename, content, *, headers=H):
    data = {"file": (io.BytesIO(content), filename)}
    return client.post(f"/api/sessions/{sid}/upload", data=data, content_type="multipart/form-data", headers=headers)


print("== 正常上傳：檔案落地、回容器內路徑 ==")
r = send(ca, "sess-alice", "shot.png", b"\x89PNG\r\n fake png bytes")
check("成功 → 201", r.status_code == 201)
body = r.get_json()
check("回的是**容器內**路徑（人要貼進終端，不是 host 路徑）", body["path"].startswith(config.DATA_BIND + "/uploads/"))
check("路徑保留副檔名", body["path"].endswith(".png"))
disk = os.path.join(config.user_space(alice["id"], host=False), "persistent-data", "uploads", body["name"])
check("檔案真的寫到他自己的 persistent-data/uploads", os.path.isfile(disk))
check("內容原封不動", open(disk, "rb").read() == b"\x89PNG\r\n fake png bytes")

print("== 檔名加時間戳前綴：連貼兩張 image.png 不互相覆蓋 ==")
r1 = send(ca, "sess-alice", "image.png", b"first")
r2 = send(ca, "sess-alice", "image.png", b"second")
check("兩次都成功", r1.status_code == 201 and r2.status_code == 201)
check("落點不同名（沒覆蓋）", r1.get_json()["name"] != r2.get_json()["name"])

print("== 閘 1：副檔名白名單 ==")
for fn in ("evil.sh", "payload.exe", "noext", "archive.zip"):
    check(f"{fn} → 400", send(ca, "sess-alice", fn, b"x").status_code == 400)
check(".md 收（白名單內）", send(ca, "sess-alice", "notes.md", b"# hi").status_code == 201)
check("副檔名比對不分大小寫（.PNG）", send(ca, "sess-alice", "SHOT.PNG", b"x").status_code == 201)

print("== 閘 2：大小上限 ==")
_saved_max = config.UPLOAD_MAX_BYTES
try:
    config.UPLOAD_MAX_BYTES = 100
    check("超過上限 → 413", send(ca, "sess-alice", "big.png", b"x" * 200).status_code == 413)
    check("剛好在上限內 → 201", send(ca, "sess-alice", "ok.png", b"x" * 100).status_code == 201)
    check("空檔 → 400（不是靜靜收一個 0 byte 的檔）", send(ca, "sess-alice", "empty.png", b"").status_code == 400)
finally:
    config.UPLOAD_MAX_BYTES = _saved_max

print("== 閘 3：路徑穿越——檔名白名單化，落點永遠在 uploads/ 內 ==")
updir = os.path.realpath(os.path.join(config.user_space(alice["id"], host=False), "persistent-data", "uploads"))
for evil in ("../../etc/passwd.png", "..%2f..%2fx.png", "a/b/c.png", "....//....//x.png", "\x00null.png"):
    r = send(ca, "sess-alice", evil, b"x")
    if r.status_code == 201:
        landed = os.path.realpath(os.path.join(updir, r.get_json()["name"]))
        check(f"{evil!r} → 落點仍在 uploads/ 內（{r.get_json()['name']}）", os.path.dirname(landed) == updir)
    else:
        check(f"{evil!r} → 被擋（{r.status_code}）", r.status_code == 400)
# 穿越沒有一次成功寫到 uploads/ 外面
outside = os.path.join(os.path.dirname(updir), "passwd.png")
check("uploads/ 的上一層沒有被寫進任何東西", not os.path.exists(outside))

print("== 閘 3b：符號連結——uploads/ 這一層被換掉時不可以跟著走 ==")
# 為什麼要單獨驗：舊版的判斷是「realpath(dest) == realpath(updir) + name」，而 uploads/
# 本身被換成連結時**兩邊會解析到同一個地方**，比對必過。這不需要搶時間差，是換完之後
# 每一次都成立的確定結果——所以下面每一條都先把連結種好、再送一發，沒有 race。
_alice_root = config.user_space(alice["id"], host=False)
_data = os.path.join(_alice_root, "persistent-data")
_uploads = os.path.join(_data, "uploads")
_outside = os.path.join(TMP, "outside")
os.makedirs(_outside, exist_ok=True)


def _swap_to_symlink(path, target):
    """把 path 換成指向 target 的連結（模擬 session 容器在自己的 rw 掛載裡動手腳）。"""
    if os.path.islink(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    os.symlink(target, path)


def _restore(path):
    if os.path.islink(path):
        os.unlink(path)
    os.makedirs(path, mode=0o700, exist_ok=True)


_swap_to_symlink(_uploads, _outside)
r = send(ca, "sess-alice", "escape.png", b"pwned")
check("uploads/ 是連結 → 被擋（不是 201）", r.status_code != 201)
check("連結指向的目錄沒有被寫進東西", not os.listdir(_outside))
_restore(_uploads)

print("== 閘 3b：上一層（persistent-data/）被換成連結也要擋 ==")
_outside2 = os.path.join(TMP, "outside2")
os.makedirs(_outside2, exist_ok=True)
_swap_to_symlink(_data, _outside2)
r = send(ca, "sess-alice", "escape2.png", b"pwned")
check("persistent-data/ 是連結 → 被擋", r.status_code != 201)
check("連結指向的目錄沒有被寫進東西", not os.listdir(_outside2))
if os.path.islink(_data):
    os.unlink(_data)
os.makedirs(_uploads, mode=0o700, exist_ok=True)

print("== 閘 3b：指向另一位使用者的空間 ==")
_bob_uploads = os.path.join(config.user_space(bob["id"], host=False), "persistent-data", "uploads")
os.makedirs(_bob_uploads, mode=0o700, exist_ok=True)
_swap_to_symlink(_uploads, _bob_uploads)
r = send(ca, "sess-alice", "crossuser.png", b"x")
check("uploads/ 指向 bob 的空間 → 被擋", r.status_code != 201)
check("bob 的 uploads/ 沒有被寫進東西", not os.listdir(_bob_uploads))
_restore(_uploads)

print("== 閘 3b：指向容器裡的掛載點（/data、/app）==")
for target in (config.DATA_BIND, "/app"):
    _probe = os.path.join(TMP, "probe" + target.replace("/", "_"))
    os.makedirs(_probe, exist_ok=True)
    _swap_to_symlink(_uploads, _probe)  # 用可寫的替身模擬那兩個掛載點
    r = send(ca, "sess-alice", "mount.png", b"x")
    check(f"uploads/ 指向 {target} 這類掛載點 → 被擋", r.status_code != 201)
    check(f"{target} 的替身沒有被寫進東西", not os.listdir(_probe))
    _restore(_uploads)

print("== 閘 3b：檔案本身被搶先種成連結（O_NOFOLLOW 這一層）==")
# 連結種在 uploads/ 裡、名字剛好是我們要寫的那一個。O_EXCL 本來就會撞 EEXIST，
# 但 O_NOFOLLOW 讓「即使 O_EXCL 被拿掉也不會跟著走」這件事有測試守著。
_victim = os.path.join(TMP, "victim.txt")
io.open(_victim, "w").write("original")
_planted = os.path.join(_uploads, "planted.png")
os.symlink(_victim, _planted)
r = send(ca, "sess-alice", "planted.png", b"overwritten")
check("種好的連結沒有被跟過去（受害檔內容不變）", io.open(_victim).read() == "original")
check("上傳本身仍然成功（換一個名字落地）", r.status_code == 201)
os.unlink(_planted)

print("== 閘 3b：uploads/ 被換成一般檔案 ==")
if os.path.isdir(_uploads) and not os.path.islink(_uploads):
    shutil.rmtree(_uploads)
io.open(_uploads, "w").write("not a directory")
r = send(ca, "sess-alice", "notdir.png", b"x")
check("uploads/ 是普通檔案 → 被擋", r.status_code != 201)
os.unlink(_uploads)
_restore(_uploads)
check("擋完之後正常上傳仍然可用", send(ca, "sess-alice", "after.png", b"ok").status_code == 201)

print("== 閘 4：反 CSRF——multipart 例外只對這個端點、且要自訂標頭 ==")
check(
    "缺 X-Requested-With → 400（form 設不了這個標頭）",
    send(ca, "sess-alice", "shot.png", b"x", headers={}).status_code == 400,
)
# multipart 例外沒有外溢到別的端點：對 /api/sessions 送 multipart 仍是 415
check(
    "multipart 例外沒外溢：POST /api/sessions 送 multipart 仍 415",
    ca.post("/api/sessions", content_type="multipart/form-data; boundary=x", data=b"").status_code == 415,
)

print("== 授權：非擁有者 404、未登入 401 ==")
check("上傳到別人的 session → 404（不洩漏存在性）", send(ca, "sess-bob", "shot.png", b"x").status_code == 404)
check("上傳到不存在的 session → 404", send(ca, "sess-nope", "shot.png", b"x").status_code == 404)
anon = app.test_client()
check("未登入 → 401", send(anon, "sess-alice", "shot.png", b"x").status_code == 401)

print("== 缺檔案欄位 ==")
check(
    "沒有 file 欄位 → 400",
    ca.post("/api/sessions/sess-alice/upload", data={}, headers=H, content_type="multipart/form-data").status_code
    == 400,
)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
