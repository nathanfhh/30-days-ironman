"""ADR 0006 整合 regression：控制平面 → (bind-mount 的) entrypoint.sh → env 非互動啟動。

驗證 SessionManager.create 走「預設 profile（不覆蓋 entrypoint）」時，真的透過 image 的
entrypoint.sh、用注入的 CLAUDE_PTY_* env 跳過選單、抵達 driver 啟動——不需 token（stub claude）。

    uv run --with flask --with docker python tests/test_entrypoint_profile.py

需要 docker + dev-container 的 image。（不驗 firewall/mitm/otel 的實際生效——那需完整 apparatus。）
"""
import os
import sys
import time

for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(v, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ⚠ 這支測的是「控制平面注入的 env 讓 entrypoint 跳過選單」——**那個對接還沒做**。
#   控制平面現在送的是 `CLAUDE_PTY_*`，而 dev-container 的 entrypoint 讀的是 `NCR_*`，
#   兩邊還沒對起來。接上之前這支測的是一個不存在的介面，所以先 gate 住並講明原因
#   （run-all.sh 的紀律：跳過了什麼一定要說，靜靜略過會讓「全部通過」看起來涵蓋全部）。
_EP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "dev-container", "entrypoint.sh")
if not (os.path.isfile(_EP) and "CLAUDE_PTY_NET" in open(_EP, encoding="utf-8").read()):
    print("  SKIP  entrypoint.sh 尚未接受控制平面注入的 env（profile 對接尚未完成）")
    sys.exit(0)

STUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stub_claude.sh")
os.chmod(STUB, 0o755)

import tempfile  # noqa: E402
_tmp = tempfile.mkdtemp(prefix="claude-pty-entrypoint-")

from server import config, db  # noqa: E402
config.ENTRYPOINT = None  # 走 entrypoint.sh（不覆蓋）
# 只掛 stub claude（免 token），不掛 ~/.claude；entrypoint.sh 由 build_run_kwargs bind-mount repo 版。
config.MOUNTS = {STUB: {"bind": "/home/nathan/.local/bin/claude", "mode": "ro"}}
# ⚠ per-user 空間也要指進 tmpdir（ADR 0016）。這支的 MOUNTS **非空**，所以 create() 會真的
#   跑 provision_user_space——不隔離的話它會在使用者**真實的家目錄**底下建出 user-N/，
#   那正是 CLAUDE.md 禁止清單第五條（測試不可以碰使用者的真實檔案）。
#   後果不只是「多一個目錄」：留下的 owner.json 記的擁有者是這支測試的 system，與正式 DB 的
#   user 1（部署者自己的帳號）對不上，正式部署第一次開 session 就會被擁有者檢查擋下。2026-07-28 實測。
config.SPACE_HOST = config.SPACE_SELF = os.path.join(_tmp, "space")
config.DB_URL = f"sqlite:///{os.path.join(_tmp, 'test.db')}"  # registry 用隔離 DB（ADR 0008）

# 憑證來源 stub 進 tmpdir：這支測的不是憑證，不該因 host 上有沒有真憑證而紅綠
# （`_guard_credentials` 在 create() 的入口就會擋）。兩個來源都要指走——
# CREDENTIALS_HOST 指向假檔，HOST_HOME 指向 tmpdir 讓第二來源撲空。
import json as _json_cred   # noqa: E402
import time as _time_cred   # noqa: E402

config.CREDENTIALS_HOST = os.path.join(_tmp, ".credentials.json")
config.HOST_HOME = _tmp
with open(config.CREDENTIALS_HOST, "w", encoding="utf-8") as _f_cred:
    _json_cred.dump({"claudeAiOauth": {
        "accessToken": "x", "refreshToken": "x",
        "expiresAt": int((_time_cred.time() + 3600) * 1000),
        "refreshTokenExpiresAt": int((_time_cred.time() + 30 * 86400) * 1000),
        "subscriptionType": "max"}}, _f_cred)
db.reset_engine()
db.init_db()

import docker  # noqa: E402
from server.sessions import Profile, SessionManager  # noqa: E402

_pass = _fail = 0
def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")

D = docker.from_env()
mgr = SessionManager()

print("== 預設 profile（unrestricted）經 entrypoint.sh 非互動啟動 ==")
s = mgr.create(profile=Profile(network="unrestricted"))  # ADR 0008 起回傳 dict
logs = ""
for _ in range(80):
    logs = D.containers.get(s["container_id"]).logs().decode(errors="replace")
    if "REACHED-DRIVER-LAUNCH" in logs:
        break
    time.sleep(0.5)

check("走 entrypoint.sh（非覆蓋）——出現非互動選單回顯", "● 非互動 CLI = claude" in logs)
check("網路選單被 env 跳過（unrestricted）", "● 非互動 網路 = unrestricted" in logs)
check("未卡在互動 read、抵達 driver 啟動", "REACHED-DRIVER-LAUNCH" in logs)
check("status 回報 profile", mgr.status(s["id"])["profile"]["network"] == "unrestricted")

check("模型與思考深度以旗標傳給 driver",
      "REACHED-DRIVER-LAUNCH args=--dangerously-skip-permissions --model opus --effort high"
      in logs)

mgr.terminate(s["id"])

gone = False
try:
    D.containers.get(s["container"])
except docker.errors.NotFound:
    gone = True
check("terminate 收乾淨", gone)


db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
