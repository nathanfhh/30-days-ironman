"""憑證的 regression：setup-token 是唯一來源（存取、注入、守門、狀態）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with cryptography python tests/test_credentials.py

守的性質：
  🔴 憑證只有一個來源——這個人貼進來的 setup-token（加密存 DB）。**沒有讀 host
     憑證檔的後路**：那是一條平常不走、出事才走、而且沒人測過的路徑。
  🔴 明文不落地：DB 裡是密文，API 不吐回，畫面只有「已設定／未設定」。
  🔴 沒設就在 create() 入口擋下，錯誤訊息講得出下一步。

⚠ 舊版此檔測的是「讀 host 憑證檔」的整套判定（days_left 預警、快照 stale、雙來源
  優先序、ro/rw 掛載模式）。那套機制整組退場，逐條處置記在檔尾的對照表。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="cred-test-")
os.environ["CLAUDE_PTY_DB_PATH"] = os.path.join(TMP, "t.db")
config.DB_PATH = os.environ["CLAUDE_PTY_DB_PATH"]
config.DB_URL = f"sqlite:///{config.DB_PATH}"
config.SECRET_KEY = "cred-test-secret"
# HOST_HOME 指進 tmpdir：這支測試絕不可讀到使用者的真實家目錄。下面還會另外
# **釘住**「就算那裡有一份憑證檔也沒人去讀」。
config.HOST_HOME = os.path.join(TMP, "home")

from server import auth, crypto, db  # noqa: E402
from server.db import session_scope  # noqa: E402
from server.models import User  # noqa: E402
from server.sessions import (  # noqa: E402
    Profile,
    SessionError,
    SessionManager,
    _guard_credentials,
    build_run_kwargs,
    claude_credentials_state,
    credentials_state,
)

db.reset_engine()
db.init_db()

_pass = _fail = 0
def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


uid = auth.create_user("cred-user", "cred-password-1")["id"]
TOKEN = "sk-ant-oat01-test-token-value"

print("== 沒設 token：狀態說得夠白，create() 入口就擋 ==")
st = claude_credentials_state(uid)
check("ok=False / state=bad", st["ok"] is False and st["state"] == "bad")
check("label 講明是哪個 agent、什麼問題", st["label"] == "Claude 未設定憑證")
check("detail 講得出下一步（setup-token → 帳號頁）",
      "setup-token" in st["detail"] and "帳號管理" in st["detail"])
check("detail 講得出後果（未登入啟動、停在登入提示）",
      "未登入" in st["detail"] and "登入提示" in st["detail"])
try:
    _guard_credentials(uid)
    check("_guard_credentials 沒 token 要 raise", False)
except SessionError as e:
    check("_guard_credentials 沒 token raise SessionError", True)
    check("錯誤訊息指向 setup-token 與帳號頁",
          "setup-token" in str(e) and "帳號管理" in str(e))

print("== 守門接在 create() 的入口上（不是只有函式自己對）==")
# 不打 docker：守門在任何容器動作**之前**就要擋下，所以 _docker 沒被碰到才是對的。
class _Boom:
    def __getattr__(self, name):
        raise AssertionError("guard 之前不該碰 docker")
mgr = SessionManager.__new__(SessionManager)
mgr._docker = _Boom()
try:
    mgr.create(user_id=uid)
    check("create() 沒 token 要 raise", False)
except SessionError:
    check("create() 沒 token 在入口就 raise（沒碰到 docker）", True)

print("== 設 token：加密入庫、狀態翻綠、env 注入 ==")
auth.set_cli_token(uid, "  " + TOKEN + "\n")   # 前後空白要被剝掉（終端複製常帶）
with session_scope() as s:
    enc = s.get(User, uid).cli_token_enc
check("DB 裡存的是密文，讀不出明文", enc is not None and TOKEN not in enc)
check("解密回原值（空白已剝）", crypto.decrypt(enc) == TOKEN)
check("auth.cli_token 回明文", auth.cli_token(uid) == TOKEN)
st = claude_credentials_state(uid)
check("ok=True / state=ok", st["ok"] is True and st["state"] == "ok")
check("label＝已設定（沒有天數——token 的到期不可知，沒得預警）",
      st["label"] == "Claude 憑證已設定")
check("detail 預告失效的症狀（開場失敗）與處置（重跑 setup-token 再貼）",
      "開場失敗" in st["detail"] and "setup-token" in st["detail"])
_guard_credentials(uid)     # 不該 raise；raise 的話這支測試直接掛掉
check("_guard_credentials 放行", True)

env = build_run_kwargs("c", "sid1", Profile(), uid).get("environment", {})
check("🔴 env 注入 CLAUDE_CODE_OAUTH_TOKEN（憑證交給 CLI 的唯一管道）",
      env.get("CLAUDE_CODE_OAUTH_TOKEN") == TOKEN)

print("== 🔴 沒有讀 host 憑證檔的後路 ==")
# 在 HOST_HOME 放一份「看起來完全有效」的舊式憑證檔：狀態、守門、掛載**全部**不理它。
live = os.path.join(config.HOST_HOME, ".claude", ".credentials.json")
os.makedirs(os.path.dirname(live), exist_ok=True)
with open(live, "w") as f:
    json.dump({"claudeAiOauth": {"accessToken": "x", "refreshToken": "x"}}, f)
auth.clear_cli_token(uid)
st = claude_credentials_state(uid)
check("🔴 host 上有憑證檔，狀態照樣是未設定（不讀它）", st["ok"] is False)
try:
    _guard_credentials(uid)
    check("🔴 守門也不理那個檔", False)
except SessionError:
    check("🔴 守門也不理那個檔", True)
_saved_mounts = config.MOUNTS
try:
    config.MOUNTS = {"/shared": {"bind": "/shared", "mode": "rw"}}
    kw = build_run_kwargs("c", "sid2", Profile(), uid)
    check("🔴 volumes 沒有任何 .credentials 掛載",
          all(".credentials" not in v.get("bind", "")
              for v in kw["volumes"].values()) and live not in kw["volumes"])
    check("🔴 env 也沒有半個憑證（沒設就是沒設，不是空字串）",
          "CLAUDE_CODE_OAUTH_TOKEN" not in kw.get("environment", {}))
finally:
    config.MOUNTS = _saved_mounts

print("== token 的輸入驗證：擋的是「貼錯東西」，不是猜格式 ==")
for bad, why in [(123, "非字串"), (None, "None"), ("", "空字串"), ("   \n", "只有空白"),
                 ("a b", "帶空白（多半整段輸出都貼進來了）"),
                 ("line1\nline2", "多行"), ("tok\ten", "控制字元"),
                 ("x" * 4097, "長度爆表")]:
    try:
        auth.set_cli_token(uid, bad)
        check(f"{why} → AuthError", False)
    except auth.AuthError:
        check(f"{why} → AuthError", True)
check("被擋下之後狀態仍是未設定（不會寫進去一半）",
      claude_credentials_state(uid)["ok"] is False)
# 不驗前綴：token 格式是上游的事，隨版本會變。單行可見字元就收。
auth.set_cli_token(uid, "some-future-token-format")
check("不寫死前綴（格式是上游的事）", auth.cli_token(uid) == "some-future-token-format")

print("== 清除與換金鑰的降級 ==")
auth.clear_cli_token(uid)
check("清除後回 None", auth.cli_token(uid) is None)
auth.set_cli_token(uid, TOKEN)
_saved_key = config.SECRET_KEY
try:
    config.SECRET_KEY = "rotated-secret"
    check("換 SECRET_KEY → 解不開一律當「沒設」（畫面引導重貼，不是 500）",
          auth.cli_token(uid) is None and claude_credentials_state(uid)["ok"] is False)
finally:
    config.SECRET_KEY = _saved_key
check("金鑰換回來，舊密文又解得開（沒被覆寫）", auth.cli_token(uid) == TOKEN)

print("== credentials_state()：形狀是 {cli: state} ==")
both = credentials_state(uid)
check("只有 claude 一把鑰匙", set(both) == {"claude"})
check("brand 正確（畫面的品牌標誌靠它）", both["claude"]["brand"] == "anthropic")
check("stamps 是空清單（沒有時刻可標，但形狀與畫面契約不變）",
      both["claude"]["stamps"] == [])

# ── 舊斷言對照表（閘 4：說得出每一條是「對象消失」還是「換人守」）─────────────────
# 換人守：
#   ·「沒憑證擋下 + 訊息講下一步」→ 本檔前兩段（訊息從 refresh 腳本改指 setup-token）
#   ·「壞值不炸、當成沒設」→「清除與換金鑰的降級」段（crypto.decrypt 回 None）
#   ·「形狀 {cli: state}／brand」→ 最末段原樣保留
#   ·「畫面說有 vs 容器吃到」的一致性 → 狀態與注入讀同一個欄位（cli_token），由
#     「設 token」段的 state=ok + env 注入同值釘住
# 對象消失（讀檔機制整組退場，含其輸入面）：
#   · days_left 門檻預警／stamps 時刻——token 到期不可知，能力失去，README 記症狀
#   · 快照 stale／雙來源優先序／ro-rw 掛載模式／空檔＝沒有／壞 JSON 指路徑——
#     沒有檔案就沒有這些狀態；「不讀檔」本身升格為 🔴 斷言
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
