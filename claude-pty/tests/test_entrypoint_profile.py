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


STUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stub_claude.sh")
os.chmod(STUB, 0o755)

import tempfile  # noqa: E402
_tmp = tempfile.mkdtemp(prefix="claude-pty-entrypoint-")

from server import config, db  # noqa: E402
config.ENTRYPOINT = None  # 走 entrypoint.sh（不覆蓋）
# 只掛 stub claude（免 token），不掛 ~/.claude；entrypoint.sh 由 build_run_kwargs bind-mount repo 版。
config.MOUNTS = {STUB: {"bind": "/home/nathan/.local/bin/claude", "mode": "ro"}}
# ⚠ per-user 空間也要指進 tmpdir（ADR 0014）。這支的 MOUNTS **非空**，所以 create() 會真的
#   跑 provision_user_space——不隔離的話它會在使用者**真實的家目錄**底下建出 user-N/，
#   那正是 CLAUDE.md 禁止清單第五條（測試不可以碰使用者的真實檔案）。
#   後果不只是「多一個目錄」：留下的 owner.json 記的擁有者是這支測試的 system，與正式 DB 的
#   user 1（部署者自己的帳號）對不上，正式部署第一次開 session 就會被擁有者檢查擋下。2026-07-28 實測。
config.SPACE_HOST = config.SPACE_SELF = os.path.join(_tmp, "space")
config.DB_URL = f"sqlite:///{os.path.join(_tmp, 'test.db')}"  # registry 用隔離 DB（ADR 0008）

config.HOST_HOME = _tmp
db.reset_engine()
db.init_db()

# 憑證＝DB 裡的 setup-token（唯一來源，D 階段起不再讀任何 host 憑證檔）。
# 這批測試的 session 都掛在 system 使用者名下，給它種一個測試值就過得了 create() 的守門。
from server import auth as _auth_seed  # noqa: E402
from server import sessions as _sessions_seed  # noqa: E402

_auth_seed.set_cli_token(_sessions_seed.ensure_system_user(), "sk-test-setup-token")

import docker  # noqa: E402
from server.sessions import DRIVER_MARKER, Profile, SessionManager  # noqa: E402

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

check("走 entrypoint.sh（非覆蓋）——出現非互動選單回顯", "● 非互動：網路 = unrestricted" in logs)
check("錄製那題也被 env 跳過（沒有它會停在 read）", "● 非互動：錄製 = n" in logs)
check("未卡在互動 read、抵達 driver 啟動", "REACHED-DRIVER-LAUNCH" in logs)
check("status 回報 profile", mgr.status(s["id"])["profile"]["network"] == "unrestricted")
check("模型與思考深度以旗標傳給 driver", "--model opus --effort high" in logs)
# 🔴 旗標**必須排在 --session-id 之後**：entrypoint 的 resolve_session_id 會掃 argv 認
#    --session-id/--resume，而模型名是使用者給的字串。一個值剛好是 `--resume` 的
#    NCR_MODEL 若排在前面就會被讀成「呼叫端自帶 session」，capture 資料夾用一個對不到
#    任何 transcript 的 id 命名，事後撈報表會撈到空的。
_argv = logs.split("REACHED-DRIVER-LAUNCH args=")[-1].splitlines()[0]
check("🔴 --model 排在 --session-id 之後（session id 先定案才 append 旗標）",
      "--session-id" in _argv and _argv.index("--session-id") < _argv.index("--model"))
# 🔴 就緒標記是 wait_ready 的訊號：要在 capture 起完之後、driver 啟動之前
check("🔴 就緒標記印在 driver 啟動之前",
      DRIVER_MARKER in logs and logs.index(DRIVER_MARKER) < logs.index("REACHED-DRIVER-LAUNCH"))

mgr.terminate(s["id"])

gone = False
try:
    D.containers.get(s["container"])
except docker.errors.NotFound:
    gone = True
check("terminate 收乾淨", gone)


# --- 🔴 條件題成對送：capture=1 少了 scope 就會停在一道永遠不來的 read -----------
#
# 這是使用者實際踩過十分鐘的坑：容器起來了、docker ps 看得到、畫面上卻只有「建立中」，
# 因為 entrypoint 正停在 `read -r -p "請選擇 [1]: "` 等一個沒有人會輸入的答案。
# 這裡**兩個方向都驗**：
#   · 正向：build_run_kwargs 一定同時給 capture 與 scope（純函式，不起容器）
#   · 反向：真的餵一個只帶 capture 的環境進容器，斷言它**確實會卡住**——沒有這一半，
#     上面那條就只是在描述現況，證不了「少了 scope 會出事」。
print("== 🔴 條件題成對送（capture ⇄ capture scope）==")
from server.sessions import build_run_kwargs  # noqa: E402

_env_cap = build_run_kwargs("c", "sidcap", Profile(capture=True), 1)["environment"]
check("capture=1 時一定帶著 scope", _env_cap.get("NCR_CAPTURE") == "1"
      and _env_cap.get("NCR_CAPTURE_SCOPE") in ("all", "model", "1", "2"))

# 反向：手動起一顆只帶 NCR_CAPTURE 的容器，它應該停在 read（讀不到就緒標記）。
_probe = D.containers.run(
    config.IMAGE, detach=True, tty=True, stdin_open=True, remove=False,
    name="claude-pty-scope-probe",
    environment={"NCR_NET": "unrestricted", "NCR_CAPTURE": "1", "NCR_MARK": "1"},
    volumes={os.path.abspath(STUB): {"bind": "/home/nathan/.local/bin/claude", "mode": "ro"}},
    entrypoint=None)
try:
    _stuck = True
    for _ in range(20):
        _plog = _probe.logs().decode(errors="replace")
        if DRIVER_MARKER in _plog:
            _stuck = False
            break
        time.sleep(0.5)
    check("🔴 少了 scope 真的會卡在 read（所以成對送不是形式）", _stuck)
    check("而且卡的地方是錄製範圍那一題（訊息看得出來）", "錄製範圍" in _plog)
finally:
    _probe.remove(force=True)


db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
