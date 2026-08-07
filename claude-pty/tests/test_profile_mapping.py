"""ADR 0006 第二層 regression：profile → containers.run 參數的映射（純函式，不碰 docker）。

    uv run --with flask --with docker python tests/test_profile_mapping.py
"""
import os
import sys
import tempfile

# ⚠ 自己的 DB：不設就會連上使用者**真實的** claude-pty.db。
_tmp = tempfile.mkdtemp(prefix="claude-pty-profmap-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config, db  # noqa: E402
from server.sessions import Profile, build_run_kwargs  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()
db.init_db()
config.MOUNTS = {}  # 隔離：不讓共用掛載干擾 volumes 斷言（per-user 的那組也吃這個開關）
_UID = 7            # per-user 空間用的假 user id（ADR 0016）

_pass = _fail = 0
def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


print("== 預設 profile：**安全預設**（限制出網 / 不錄 / 不送）走 entrypoint.sh ==")
config.ENTRYPOINT = None
kw = build_run_kwargs("c", "sid1", Profile(), _UID)
env = kw.get("environment", {})
# ⚠ `Profile()` 的預設必須等同 `config.DEFAULT_*`。這兩份曾經各寫各的，然後 network
#   分岔了：dataclass 是 unrestricted、config 是 restricted，於是 server 端任何一個
#   `Profile()` 都會拿到可任意連外的容器，無聲無息（review 2026-07-25）。
#   這條斷言就是要讓「有人又在 dataclass 裡寫死一個字面值」立刻現形。
check("Profile() 的預設 == config.DEFAULT_*（不是另一份字面值）",
      (Profile().cli, Profile().network, Profile().capture,
       Profile().telemetry, Profile().model, Profile().effort)
      == ("claude", config.DEFAULT_NET, config.DEFAULT_CAPTURE,
          config.DEFAULT_TELEMETRY, config.DEFAULT_MODEL, config.DEFAULT_EFFORT))
check("預設是**限制出網**（安全預設，要放行必須是明確的選擇）",
      Profile().network == "restricted")
check("不覆蓋 entrypoint（走 image entrypoint.sh）", "entrypoint" not in kw)
# 精確比對整份 env（不是子集）：多送一個變數給 entrypoint.sh 就等於多開一條它會反應的
# 分支，那必須是有意識的決定而不是順手加的。新增變數時請一起更新這裡。
# 🔴 精確比對整份 env（不是子集）：多送一個變數給 entrypoint 就等於多開一條它會反應的
#    分支，那必須是有意識的決定。**subagent 深度上限刻意不在裡面**——人自己開容器時
#    沒有它，送了就是製造差異（見 build_run_kwargs 的說明）。
check("env 帶 NET/CAPTURE/SCOPE/MARK + 模型設定", env == {
    "NCR_NET": "restricted", "NCR_CAPTURE": "0", "NCR_CAPTURE_SCOPE": "all",
    # 只有 MARK 有值時 entrypoint 才印就緒標記——人自己開容器不會設它
    "NCR_MARK": "1",
    # mitmweb UI 收回容器 loopback：網頁 session 在共用網段上，兄弟容器不該連得到
    # 那個顯示未脫敏流量的畫面（人自己開容器時不設，run script 的 -p 才轉得進去）
    "NCR_MITM_WEB_BIND": "127.0.0.1",
    # per-user 狀態空間（ADR 0016）。這兩個**不是**給 entrypoint.sh 的，是給 CLI 本身的，
    # 所以不隨 profile 變、每一場都在。
    # 少了 CLAUDE_CONFIG_DIR，.claude.json 會落在容器 writable layer，換一顆容器就沒了。
    "CLAUDE_CONFIG_DIR": "/home/nathan/.claude",
    "CLAUDE_SECURESTORAGE_CONFIG_DIR": "/home/nathan/.claude-creds",
    "NCR_MODEL": "opus", "NCR_EFFORT": "high"})
# restricted 是預設，所以這裡要有 firewall 需要的能力（下面 restricted 段落再驗一次細節）
check("預設帶 cap_add=[NET_ADMIN]（restricted 套 iptables 需要）",
      kw.get("cap_add") == ["NET_ADMIN"])
check("預設接上 session network",
      kw.get("network") == config.SESSION_NETWORK)
check("無 ports", "ports" not in kw)
check("bind-mount repo entrypoint.sh（freshness）", config.ENTRYPOINT_SH in kw["volumes"])

print("== semgrep-rules（A4）：判準是 .git，不是 isdir ==")
# compose/daemon 在來源缺席時會以 root 建出**空目錄**頂替——只驗 isdir 會把那個空殼
# 掛進去，看起來像掛了其實沒有規則。所以空殼要不掛，真的 clone（有 .git）才掛。
import tempfile as _tf_sg  # noqa: E402
_sg = _tf_sg.mkdtemp(prefix="claude-pty-semgrep-")
_saved_sg = (config.SEMGREP_RULES_SELF, config.SEMGREP_RULES_HOST)
try:
    config.SEMGREP_RULES_SELF = config.SEMGREP_RULES_HOST = _sg
    check("空目錄（root 頂替出來的殼）不掛",
          _sg not in build_run_kwargs("c", "sidSG", Profile(), _UID)["volumes"])
    os.makedirs(os.path.join(_sg, ".git"))
    check("真的 clone（有 .git）→ :ro 掛到 ~/semgrep-rules（與 run script 同落點）",
          build_run_kwargs("c", "sidSG", Profile(), _UID)["volumes"].get(_sg)
          == {"bind": "/home/nathan/semgrep-rules", "mode": "ro"})
finally:
    config.SEMGREP_RULES_SELF, config.SEMGREP_RULES_HOST = _saved_sg
    __import__("shutil").rmtree(_sg, ignore_errors=True)

print("== restricted：cap_add NET_ADMIN + network ==")
kw = build_run_kwargs("c", "sid2", Profile(network="restricted"), _UID)
check("cap_add=[NET_ADMIN]", kw.get("cap_add") == ["NET_ADMIN"])
check("network=session network", kw.get("network") == config.SESSION_NETWORK)
check("env NCR_NET=restricted", kw["environment"]["NCR_NET"] == "restricted")

print("== telemetry：OTEL env + NCR_OTEL + network 到 jaeger ==")
kw = build_run_kwargs("c", "sidT", Profile(telemetry=True), _UID)
env = kw["environment"]
check("NCR_OTEL=1（跳過選單、保留 telemetry）", env.get("NCR_OTEL") == "1")
# ⚠ 這個 flag 是「trace（非只有 metrics）」的開關，缺它 claude 不吐 trace（live 驗證踩到）。
check("CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1（啟用 trace）", env.get("CLAUDE_CODE_ENHANCED_TELEMETRY_BETA") == "1")
check("OTEL endpoint 指向 jaeger", env.get("OTEL_EXPORTER_OTLP_ENDPOINT") == config.OTEL_ENDPOINT)
check("resource attr 帶 session.id", "session.id=sidT" in env.get("OTEL_RESOURCE_ATTRIBUTES", ""))
check("network 到得了 jaeger", kw.get("network") == config.SESSION_NETWORK)

print("== capture：mount addon + host 落盤（ADR 0008 後不再由 create 發布 mitm port）==")
kw = build_run_kwargs("c", "sidC", Profile(capture=True), _UID)
env = kw["environment"]
check("env NCR_CAPTURE=1", env["NCR_CAPTURE"] == "1")
# 🔴 條件題要成對送：capture=1 時 entrypoint 會接著問錄製範圍，沒帶 scope 就停在那道
#    read——容器卡在啟動，而畫面上只看得到「一直在建立中」。
check("🔴 capture 開著時一定帶 NCR_CAPTURE_SCOPE（否則容器卡在 read）",
      env.get("NCR_CAPTURE_SCOPE") in ("all", "model", "1", "2"))
# ADR 0007 的落盤要求還在，但落點改成 per-user（ADR 0016）——見下方「per-user 狀態空間」
# 段落。這裡 MOUNTS 是空的（測試隔離），所以連 addon 都不會掛，只驗 env 與 port。
# ADR 0008：port 屬 on-demand view 範疇，create 不再發布 host port
check("create 不再發布 host port", "ports" not in kw)

print("== 穩健布林解析（字串 'false' 不該變 True）==")
from server.sessions import Profile as _P  # noqa: E402
check("profile capture='false' → False", _P.from_dict({"capture": "false"}).capture is False)
check("profile capture='true'  → True", _P.from_dict({"capture": "true"}).capture is True)
check("profile telemetry=0（int）→ False", _P.from_dict({"telemetry": 0}).telemetry is False)

print("== 模型與思考深度 → entrypoint 的 env ==")
# entrypoint.sh 把這組 env 翻成 `--model/--effort`，嚴格加法（env 沒設就不加參數）。
# ⚠ 測試值一律挑最便宜的 sonnet。這支是純函式測試（build_run_kwargs 不碰 docker、也不
#   啟動任何 CLI），寫什麼都不花錢——但測試檔會被當成範例抄，留著貴的型號等於在邀請
#   別人開一個貴的 session。
kw = build_run_kwargs("c", "sidM", Profile(model="sonnet", effort="xhigh"), _UID)
check("帶入 NCR_MODEL", kw["environment"].get("NCR_MODEL") == "sonnet")
check("帶入 NCR_EFFORT", kw["environment"].get("NCR_EFFORT") == "xhigh")

check("預設為 opus / high",
      (_P.from_dict(None).model, _P.from_dict(None).effort) == ("opus", "high"))
check("as_dict 往返保留 model/effort",
      _P.from_dict({"model": "sonnet", "effort": "max"}).as_dict()["effort"] == "max")

print("== SSH agent 轉發：預設關，設了才掛（opt-in，ADR 0012）==")
# 預設關這件事要有斷言守著：它掛的是「能以你的身分認證任何主機」的東西，
# 哪天有人手滑給 SSH_AUTH_SOCK_HOST 一個預設值，這裡要立刻紅。
check("預設不掛（連 mounts 這個 key 都不出現）",
      not config.SSH_AUTH_SOCK_HOST and "mounts" not in build_run_kwargs("c", "sidS0", Profile(), _UID))

config.SSH_AUTH_SOCK_HOST = "/run/user/1234/keyring/ssh"
try:
    kw = build_run_kwargs("c", "sidS1", Profile(), _UID)
    m = (kw.get("mounts") or [{}])[0]
    check("掛到 /ssh/ssh_sock（image 的 SSH_AUTH_SOCK 指向這裡，與 run script 對齊）",
          m.get("Target") == config.SSH_AUTH_SOCK_BIND == "/ssh/ssh_sock")
    check("來源是 host 路徑（由 daemon 解讀，ADR 0009）",
          m.get("Source") == "/run/user/1234/keyring/ssh")
    # ⚠ 這兩條是這個功能的實質內容，不是形式：
    #   type=bind 走 Mounts → 來源不存在時 dockerd 報錯；若退回 volumes(Binds)，dockerd 會
    #   在 host 上建一個 root:root 目錄頂替，把 host 自己的 agent socket 位置佔掉。
    check("type=bind（來源不存在要報錯，不可以讓 dockerd 在 host 上建目錄頂替）",
          m.get("Type") == "bind")
    #   連 unix socket 需要寫權限，唯讀掛會 EACCES ——掛了等於沒掛。
    check("非唯讀（連 socket 需要寫權限）", m.get("ReadOnly") is False)

    # 部署層能力：不隨 profile 或 entrypoint 變。escape hatch 也要有，否則
    # 「bash 進去手動 git push」這條路徑會跟正常 session 行為不一致。
    config.ENTRYPOINT = "bash"
    check("escape hatch（bash）也照掛", "mounts" in build_run_kwargs("c", "sidS2", Profile(), _UID))
    config.ENTRYPOINT = None
    check("escape hatch 還原後照掛（與 profile 其他欄位無關）",
          "mounts" in build_run_kwargs("c", "sidS3", Profile(capture=True), _UID))
finally:
    config.SSH_AUTH_SOCK_HOST = ""     # 還原，後面的段落不該看到它

check("還原後又回到不掛", "mounts" not in build_run_kwargs("c", "sidS4", Profile(), _UID))

print("== escape hatch：CLAUDE_PTY_ENTRYPOINT 覆蓋 → 跳過 entrypoint.sh 與 profile ==")
config.ENTRYPOINT = "bash"
config.COMMAND = []
kw = build_run_kwargs("c", "sidB", Profile(network="restricted", capture=True), _UID)
check("覆蓋 entrypoint=bash", kw.get("entrypoint") == "bash")
check("無 profile env（選單無意義）", "environment" not in kw)
check("無 cap_add（不套 profile 的能力）", "cap_add" not in kw)
config.ENTRYPOINT = None  # 還原

print("\n== docker 時間戳只有一份解析（review 2026-07-26）==")
# ⚠ 曾經有兩份實作（sessions._image_created_at 與 reconciler._age_seconds），而且已經漂移：
#   後者只認 "+" 判斷有沒有時區偏移，於是 "-05:00" 會落到 else 分支被當成 UTC。目前不可達
#   （daemon 一律回 Z），但 _remove_orphans 的寬限期就是靠它算的，而解析失敗的 fallback 是
#   「很舊」——錯的方向是安靜地提早把還在建立中的容器當孤兒刪掉。
import datetime as _dt3  # noqa: E402

from server.sessions import parse_docker_time  # noqa: E402

UTC = _dt3.timezone.utc
for label, raw, want in [
    ("奈秒精度 + Z", "2026-07-26T02:57:51.828567844Z",
     _dt3.datetime(2026, 7, 26, 2, 57, 51, 828567, tzinfo=UTC)),
    ("正偏移", "2026-07-26T02:57:51.828567844+08:00",
     _dt3.datetime(2026, 7, 26, 2, 57, 51, 828567,
                   tzinfo=_dt3.timezone(_dt3.timedelta(hours=8)))),
    ("**負偏移**（舊實作會靜靜當成 UTC，整整差掉時差）",
     "2026-07-26T02:57:51.828567844-05:00",
     _dt3.datetime(2026, 7, 26, 2, 57, 51, 828567,
                   tzinfo=_dt3.timezone(_dt3.timedelta(hours=-5)))),
    ("沒有小數位", "2026-07-26T02:57:51Z",
     _dt3.datetime(2026, 7, 26, 2, 57, 51, tzinfo=UTC)),
    ("完全沒有時區＝當 UTC", "2026-07-26T02:57:51",
     _dt3.datetime(2026, 7, 26, 2, 57, 51, tzinfo=UTC)),
]:
    got = parse_docker_time(raw)
    check(f"{label}：{raw}",
          got is not None and got == want and got.utcoffset() == want.utcoffset())
for bad in (None, "", "not-a-time", "2026-13-45T99:99:99Z"):
    check(f"解不出來回 None：{bad!r}", parse_docker_time(bad) is None)

print("\n== per-user 狀態空間（ADR 0016）==")
# ⚠ 這裡原本是 `_symlink_overlays` 的回歸測試（把 host ~/.claude 底下的 symlink 逐一疊回
#   容器內）。ADR 0016 之後 host 的 ~/.claude 完全不進 session，那個函式連同它那顆 runc
#   地雷（在 dangling symlink 上建 mountpoint，新版 runc 會間歇性拒絕）一起退場。
import tempfile  # noqa: E402

from server.sessions import provision_user_space  # noqa: E402

_space = tempfile.mkdtemp(prefix="claude-pty-space-")
_saved = (config.MOUNTS, config.SPACE_HOST, config.SPACE_SELF)
try:
    config.MOUNTS = {"/shared": {"bind": "/shared", "mode": "rw"}}   # 非空才會有 per-user 掛載
    config.SPACE_HOST = config.SPACE_SELF = _space
    kw = build_run_kwargs("c", "sidU", Profile(), _UID)
    vols, env = kw["volumes"], kw["environment"]
    root = os.path.join(_space, "user-7")

    check("狀態目錄掛成容器的 ~/.claude（rw）",
          vols.get(os.path.join(root, "claude"))
          == {"bind": "/home/nathan/.claude", "mode": "rw"})
    # 這兩個 env 是整個機制的關鍵：少了 CLAUDE_CONFIG_DIR，.claude.json 會落在容器
    # writable layer，換一顆容器就沒了（而且完全無聲）。
    check("env 指定 CLAUDE_CONFIG_DIR 指向那個目錄",
          env.get("CLAUDE_CONFIG_DIR") == "/home/nathan/.claude")
    check("env 把憑證目錄**獨立**指開（才能一邊 per-user、一邊共用）",
          env.get("CLAUDE_SECURESTORAGE_CONFIG_DIR") == "/home/nathan/.claude-creds")
    check("持久化空間掛在 ~/persistent-data",
          vols.get(os.path.join(root, "persistent-data"))
          == {"bind": "/home/nathan/persistent-data", "mode": "rw"})
    # ⚠ 這條守的是一個具體的坑，不是風格：落點若在 cwd 底下，cwd 就永遠不是空的，
    #   而 `git clone <url> .` 對非空目錄是直接失敗（在真 image 裡驗過），錯誤訊息
    #   還完全指不到原因。2026-07-29 討論過這個位置，結論是留在 cwd 外面。
    check("🔴 而且**不在 cwd 底下**（cwd 要維持空的，否則 git clone . 會壞）",
          not config.DATA_BIND.startswith(config.WORKDIR.rstrip("/") + "/"))
    # capture 的 .mitm 裡是**完整的 API 請求本文**（prompt 全文），比 transcript 更敏感；
    # 審查報告則是個人的歷史。兩者都住在同一個根底下，掛**一次**就好——
    # 另外再掛 mitm 會變成巢狀 bind mount（落點要先存在，少一個子目錄就啟動失敗）。
    check("capture 與報告的根 per-user（裡面是 prompt 全文與個人的審查紀錄）",
          vols.get(os.path.join(root, "ncr")) == {"bind": config.NCR_HOME_BIND, "mode": "rw"})
    check("🔴 沒有把 mitm 另外掛一次（那會是巢狀掛載）",
          not any(v["bind"].startswith(config.NCR_HOME_BIND + "/") for v in vols.values()))
    check("capture 的落點是那個根底下的 mitm（與 entrypoint 的 CAPTURE_DIR 一致）",
          config.MITM_BIND == config.NCR_HOME_BIND + "/mitm")
    check("host 的 ~/.claude 完全不在掛載裡（狀態層已隔離）",
          not any(v["bind"] == "/home/nathan/.claude" and k != os.path.join(root, "claude")
                  for k, v in vols.items()))
    check("共用掛載仍然在（trivy 那類不該被 per-user 化）",
          vols.get("/shared") == {"bind": "/shared", "mode": "rw"})

    print("\n-- provision_user_space：種子與落點 --")
    provision_user_space(_UID, "seeder")
    import json as _json
    seed = _json.load(open(os.path.join(root, "claude", ".claude.json"), encoding="utf-8"))
    # 三道對話各對應一個 key。最惡劣的是 bypass 那道：預設停在「No, exit」，driver 送出的
    # 第一個 Enter 就是把容器結束掉——所以少一個 key 不是「多按一次」，是 session 直接死。
    check("種子關掉 onboarding 精靈", seed.get("hasCompletedOnboarding") is True)
    check("種子關掉 Bypass Permissions 對話（預設停在 No, exit）",
          seed.get("bypassPermissionsModeAccepted") is True)
    check("種子關掉信任對話，而且 key 是 config.WORKDIR 不是寫死字面值",
          seed.get("projects", {}).get(config.WORKDIR, {}).get("hasTrustDialogAccepted") is True)
    check("要掛的目錄都建出來了（不能讓 dockerd 隱式建成 root:root）",
          all(os.path.isdir(os.path.join(root, d))
              for d in ("claude", "persistent-data", "ncr")))

    # 使用者跑過之後那個檔就是他的狀態（projects、numStartups、快取…），再次呼叫不可以蓋掉
    _seed_file = os.path.join(root, "claude", ".claude.json")
    _mine = {"mine": True, "projects": {config.WORKDIR: {"hasTrustDialogAccepted": True}}}
    with open(_seed_file, "w", encoding="utf-8") as f:
        _json.dump(_mine, f)
    provision_user_space(_UID, "seeder")
    check("第二次呼叫不覆蓋既有的 .claude.json（那是使用者的狀態）",
          _json.load(open(_seed_file, encoding="utf-8")) == _mine)

    # ⚠ 半截檔**不是**「使用者的狀態」，是上一次寫到一半被 kill 的殘骸。當成狀態跳過的話
    #   那個使用者從此每一場都撞 onboarding，而且永遠修不好——最後那道對話預設停在
    #   「No, exit」，driver 送出的第一個 Enter 就把容器收掉。
    with open(_seed_file, "w", encoding="utf-8") as f:
        f.write('{"hasCompletedOnboarding": tr')      # 寫到一半
    provision_user_space(_UID, "seeder")
    check("壞掉的 .claude.json 會被重寫（不是「存在就跳過」）",
          _json.load(open(_seed_file, encoding="utf-8")).get("hasCompletedOnboarding") is True)
    with open(_seed_file, "w", encoding="utf-8"):
        pass                                           # 0 byte
    provision_user_space(_UID, "seeder")
    check("空的 .claude.json 也會被重寫",
          _json.load(open(_seed_file, encoding="utf-8")).get("bypassPermissionsModeAccepted")
          is True)

    # WORKDIR 改掉時，**既有使用者**的檔案裡不會有新 cwd 的信任 key ——下一場全部撞信任
    # 對話。所以要補寫，而且只補缺的那一個 key，不動其他狀態。
    _saved_wd = config.WORKDIR
    try:
        with open(_seed_file, "w", encoding="utf-8") as f:
            _json.dump({"numStartups": 42, "projects": {"/old/cwd": {"x": 1}}}, f)
        config.WORKDIR = "/home/nathan/new-cwd"
        provision_user_space(_UID, "seeder")
        after = _json.load(open(_seed_file, encoding="utf-8"))
        check("WORKDIR 改了會補上新 cwd 的信任 key（不只影響第一場）",
              after["projects"]["/home/nathan/new-cwd"]["hasTrustDialogAccepted"] is True)
        check("補寫只加不改：既有狀態與舊 cwd 都留著",
              after["numStartups"] == 42 and after["projects"]["/old/cwd"] == {"x": 1})
    finally:
        config.WORKDIR = _saved_wd

    # ⚠ 目錄名是 user-{id}，而 id 是 DB 的 autoincrement——registry 重建過就會重新指派。
    #   沒有這道檢查的話，新的 user-7 會直接繼承前一個 user-7 的 transcript 與 mitm 全文。
    print("\n-- 擁有者標記：registry 換代時不可以靜默交出別人的空間 --")
    # ⚠ 用**乾淨的 uid**：上面那個 _UID 的空間已經被無名 provision 建出資料了，而那正好是
    #   「有資料卻沒有標記」——下面第三段就在驗它會被擋。兩件事不能混在同一個目錄上測。
    _OWNED, root2 = 8, os.path.join(_space, "user-8")
    provision_user_space(_OWNED, "alice")
    check("第一次帶 username 會寫下擁有者",
          _json.load(open(os.path.join(root2, "owner.json"), encoding="utf-8"))["username"]
          == "alice")
    provision_user_space(_OWNED, "alice")
    check("同一個人再開照常放行", True)
    _denied = False
    try:
        provision_user_space(_OWNED, "bob")
    except Exception as e:      # noqa: BLE001 —— 這裡就是要驗它拒絕
        _denied = "alice" in str(e)
    check("換人就拒絕，而且訊息說得出是誰的空間", _denied)
    # 「有資料、沒標記」也不可以認領——那是升級前留下的空間，或有人手動動過。
    # 直接蓋章一樣是把別人的 transcript 與 mitm 全文交出去，只是換一條路徑到同一個壞結果。
    _refused_unmarked = False
    _u3 = os.path.join(_space, "user-11")
    os.makedirs(os.path.join(_u3, "claude"), exist_ok=True)
    with open(os.path.join(_u3, "claude", ".claude.json"), "w", encoding="utf-8") as f:
        f.write("{}")                            # 有資料，但沒有 owner.json（升級前的空間）
    try:
        provision_user_space(11, "carol")
    except Exception as e:      # noqa: BLE001
        _refused_unmarked = "owner.json" in str(e)
    check("有資料卻沒有標記→也要停下來問人（升級前的空間走這條）", _refused_unmarked)
    # ⚠ 壞掉的標記**不等於**沒有標記。當成「還沒有人認領」就會直接重新蓋章，把上一個人的
    #   transcript 與 mitm 全文靜默交出去——那正是這個標記存在的理由。
    with open(os.path.join(root2, "owner.json"), "w", encoding="utf-8") as f:
        f.write('{"username": "ali')          # 寫到一半
    _refused = False
    try:
        provision_user_space(_OWNED, "bob")
    except Exception as e:      # noqa: BLE001 —— 就是要驗它不放行
        _refused = "owner.json" in str(e)
    check("壞掉的擁有者標記→停下來問人，不可以重新蓋章", _refused)
    check("而且沒有被偷偷覆寫成新的擁有者",
          open(os.path.join(root2, "owner.json"), encoding="utf-8").read() == '{"username": "ali')
    check("目錄是 0700（ncr/mitm 裡是 prompt 全文，不可以世界可讀）",
          (os.stat(root).st_mode & 0o777) == 0o700
          and (os.stat(os.path.join(root, "ncr")).st_mode & 0o777) == 0o700)

    config.MOUNTS = {}
    check("MOUNTS 清空時 per-user 掛載也一起消失（測試隔離）",
          config.user_mounts(_UID) == {})
    _u2 = os.path.join(_space, "user-99")
    provision_user_space(99, "seeder")
    check("MOUNTS 清空時 provision 完全不做事（不在 host 上長目錄）",
          not os.path.exists(_u2))
finally:
    config.MOUNTS, config.SPACE_HOST, config.SPACE_SELF = _saved
    __import__("shutil").rmtree(_space, ignore_errors=True)

print("\n== run script 的行為必須零偏差（ADR 0016 的硬約束）==")
# ⚠ ADR 0016 把「run script 沒有受影響」列為硬約束，但那原本只靠讀 diff 推論。這裡把它
#   釘成測試：兩條路徑**共用同一份 entrypoint.sh**（ADR 0006 的 SSOT），所以只要那份檔案
#   裡沒有 per-user 的概念，人類跑 run script 的那條路就不可能被這次改動碰到。
_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ep = os.path.join(_repo, "dev-container", "entrypoint.sh")
_run = os.path.join(_repo, "dev-container", "run-ncr-dev-container.sh")
if os.path.isfile(_ep) and os.path.isfile(_run):
    _ep_src = open(_ep, encoding="utf-8").read()
    _run_src = open(_run, encoding="utf-8").read()
    # 兩個新 env 由控制平面直接 `-e` 給容器，**不經過 entrypoint**。它一旦開始讀這兩個
    # 變數，人類路徑就會跟著改變行為——那就是「有偏差」了。
    check("entrypoint.sh 完全不碰 CLAUDE_CONFIG_DIR（否則人類路徑會被牽連）",
          "CLAUDE_CONFIG_DIR" not in _ep_src)
    check("entrypoint.sh 完全不碰 CLAUDE_SECURESTORAGE_CONFIG_DIR",
          "CLAUDE_SECURESTORAGE_CONFIG_DIR" not in _ep_src)
    # run script 掛的仍然是 host 上**正式的那一份**，不是 per-user 空間。
    check("run script 仍掛 host 正式的 ~/.claude", "-v ~/.claude:" in _run_src)
    check("run script 仍掛 host 正式的 ~/.claude.json", "-v ~/.claude.json:" in _run_src)
    check("run script 沒有沾到 per-user 空間", "claude-pty-space" not in _run_src)

    # --- firewall profile 的零偏差 ---------------------------------------
    # ⚠ 這一段**還沒接上**。控制平面已經會把 `firewall-profile-web` 以 :ro 掛進 session
    #   （見 config.FIREWALL_PROFILE_BIND 與 build_run_kwargs），但 dev-container 的
    #   `init-firewall.sh` 目前還不讀那個檔——所以網頁 session 現在跑的是與人的路徑
    #   完全相同的那套規則。要釘的性質有兩個，接上之後這裡就補起來：
    #     · 預設是 host（沒有那個檔＝人的路徑，行為逐字不變）
    #     · profile 讀**檔案**不是環境變數（env 是容器內那個使用者可控的）
    _fw = os.path.join(_repo, "dev-container", "init-firewall.sh")
    _fw_src = open(_fw, encoding="utf-8").read()
    if "FW_PROFILE" in _fw_src:
        check("profile 讀的是檔案不是環境變數",
              config.FIREWALL_PROFILE_BIND in _fw_src and "FW_PROFILE=${" not in _fw_src)
    else:
        print("  SKIP  init-firewall.sh 尚未讀 profile 檔（接上之前這幾條無從驗起）")
    check("run script 完全不提 firewall profile（它靠預設值，不需要知道這件事）",
          "firewall-profile" not in _run_src)
else:
    print("  SKIP  找不到 dev-container/（只有 claude-pty 被單獨 checkout 時會這樣）")

db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
