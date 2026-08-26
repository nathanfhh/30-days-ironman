"""mitmweb 網頁密碼的導出性質（ADR 0021）。純函式，不需 docker。

    uv run --with flask --with docker --with sqlalchemy python tests/test_mitm_token.py

這一串同時要滿足四件互相牽制的事，少一件都會壞在很難查的地方：

  · **兩端各算各的要算出同一個**：控制平面建容器時送進去，`/api/auth/mitm` 之後重算。
    不確定性的話症狀是「按了按鈕就跳回首頁」（mitmweb 回 403、nginx 接成 302）。
  · **不同場次互不相通**：這個 UI 顯示的是未脫敏的即時流量。
  · **不可以 `$` 開頭**：mitmweb 把那種值當 argon2 hash 驗（v12.2.3），而我們要明文比對。
  · **字母表要能安全地穿過 env 與 HTTP header**：它會變成 shell 的環境變數與 Bearer 值。
"""

import os
import string
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="claude-pty-mitmtoken-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["CLAUDE_PTY_SECRET_KEY"] = "test-secret-key-not-for-production"

from server import config  # noqa: E402
from server.crypto import mitm_web_password  # noqa: E402

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


SIDS = [f"{i:012x}" for i in range(500)]

print("== 確定性：同一場、同一把金鑰，永遠是同一串 ==")
check("同一個 sid 連算兩次相同", mitm_web_password("aa11bb22cc33") == mitm_web_password("aa11bb22cc33"))

print("== 場次之間互不相通 ==")
tokens = [mitm_web_password(s) for s in SIDS]
check(f"{len(SIDS)} 個 sid 導出 {len(set(tokens))} 個相異的值", len(set(tokens)) == len(SIDS))
# 🔴 不是「看起來不一樣」而已：sid 只差一個字元時也必須完全無關。截斷式的錯誤實作
#    （例如直接拿 sid 補到 24 字元）在上面那條會全綠，這裡才會紅。
check(
    "🔴 相鄰 sid 的導出值沒有共同前綴（不是把 sid 抄過去補長）",
    os.path.commonprefix([mitm_web_password("aa11bb22cc33"), mitm_web_password("aa11bb22cc34")]) == "",
)
check("🔴 導出值裡不含 sid 本身", all(sid not in tok for sid, tok in zip(SIDS, tokens, strict=True)))

print("== 字母表：穿得過 mitmweb 的 argon2 判斷、env 與 HTTP header ==")
ALLOWED = set(string.ascii_letters + string.digits + "-_")
check("長度一律 24", {len(t) for t in tokens} == {24})
check("🔴 沒有任何一個以 `$` 開頭（mitmweb 會把那種值當 argon2 hash 去驗）", not any(t.startswith("$") for t in tokens))
check("字元全在 base64url 字母表內（沒有空白／引號／控制字元）", all(set(t) <= ALLOWED for t in tokens))

print("== 換掉 SECRET_KEY ＝ 一次作廢全部（這是這個設計買到的東西）==")
_before = mitm_web_password("aa11bb22cc33")
_orig = config.SECRET_KEY
try:
    config.SECRET_KEY = "another-secret-key-entirely"
    check("🔴 換金鑰之後同一場導出不同值", mitm_web_password("aa11bb22cc33") != _before)
finally:
    config.SECRET_KEY = _orig
check("換回來就一致（每次呼叫都重新導出，沒有快取住舊的）", mitm_web_password("aa11bb22cc33") == _before)

__import__("shutil").rmtree(_tmp, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
