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
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


alice = auth.create_user("alice", "alice-password-1")
bob = auth.create_user("bob", "bob-password-1")
with session_scope() as s:
    s.add(SessionRow(id="sess-alice", container_name="claude-pty-sess-alice",
                     user_id=alice["id"], status="running"))
    s.add(SessionRow(id="sess-bob", container_name="claude-pty-sess-bob",
                     user_id=bob["id"], status="running"))

ca = app.test_client()
ca.post("/api/auth/login", json={"username": "alice", "password": "alice-password-1"})

H = {"X-Requested-With": "fetch"}          # 後端要求的反 CSRF 標頭


def send(client, sid, filename, content, *, headers=H):
    data = {"file": (io.BytesIO(content), filename)}
    return client.post(f"/api/sessions/{sid}/upload",
                       data=data, content_type="multipart/form-data", headers=headers)


print("== 正常上傳：檔案落地、回容器內路徑 ==")
r = send(ca, "sess-alice", "shot.png", b"\x89PNG\r\n fake png bytes")
check("成功 → 201", r.status_code == 201)
body = r.get_json()
check("回的是**容器內**路徑（人要貼進終端，不是 host 路徑）",
      body["path"].startswith(config.DATA_BIND + "/uploads/"))
check("路徑保留副檔名", body["path"].endswith(".png"))
disk = os.path.join(config.user_space(alice["id"], host=False),
                    "persistent-data", "uploads", body["name"])
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
check("副檔名比對不分大小寫（.PNG）",
      send(ca, "sess-alice", "SHOT.PNG", b"x").status_code == 201)

print("== 閘 2：大小上限 ==")
_saved_max = config.UPLOAD_MAX_BYTES
try:
    config.UPLOAD_MAX_BYTES = 100
    check("超過上限 → 413", send(ca, "sess-alice", "big.png", b"x" * 200).status_code == 413)
    check("剛好在上限內 → 201",
          send(ca, "sess-alice", "ok.png", b"x" * 100).status_code == 201)
    check("空檔 → 400（不是靜靜收一個 0 byte 的檔）",
          send(ca, "sess-alice", "empty.png", b"").status_code == 400)
finally:
    config.UPLOAD_MAX_BYTES = _saved_max

print("== 閘 3：路徑穿越——檔名白名單化，落點永遠在 uploads/ 內 ==")
updir = os.path.realpath(os.path.join(config.user_space(alice["id"], host=False),
                                      "persistent-data", "uploads"))
for evil in ("../../etc/passwd.png", "..%2f..%2fx.png", "a/b/c.png",
             "....//....//x.png", "\x00null.png"):
    r = send(ca, "sess-alice", evil, b"x")
    if r.status_code == 201:
        landed = os.path.realpath(os.path.join(updir, r.get_json()["name"]))
        check(f"{evil!r} → 落點仍在 uploads/ 內（{r.get_json()['name']}）",
              os.path.dirname(landed) == updir)
    else:
        check(f"{evil!r} → 被擋（{r.status_code}）", r.status_code == 400)
# 穿越沒有一次成功寫到 uploads/ 外面
outside = os.path.join(os.path.dirname(updir), "passwd.png")
check("uploads/ 的上一層沒有被寫進任何東西", not os.path.exists(outside))

print("== 閘 4：反 CSRF——multipart 例外只對這個端點、且要自訂標頭 ==")
check("缺 X-Requested-With → 400（form 設不了這個標頭）",
      send(ca, "sess-alice", "shot.png", b"x", headers={}).status_code == 400)
# multipart 例外沒有外溢到別的端點：對 /api/sessions 送 multipart 仍是 415
check("multipart 例外沒外溢：POST /api/sessions 送 multipart 仍 415",
      ca.post("/api/sessions", content_type="multipart/form-data; boundary=x",
              data=b"").status_code == 415)

print("== 授權：非擁有者 404、未登入 401 ==")
check("上傳到別人的 session → 404（不洩漏存在性）",
      send(ca, "sess-bob", "shot.png", b"x").status_code == 404)
check("上傳到不存在的 session → 404",
      send(ca, "sess-nope", "shot.png", b"x").status_code == 404)
anon = app.test_client()
check("未登入 → 401", send(anon, "sess-alice", "shot.png", b"x").status_code == 401)

print("== 缺檔案欄位 ==")
check("沒有 file 欄位 → 400",
      ca.post("/api/sessions/sess-alice/upload", data={}, headers=H,
              content_type="multipart/form-data").status_code == 400)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
