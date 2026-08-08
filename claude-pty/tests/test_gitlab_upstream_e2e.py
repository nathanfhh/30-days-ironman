"""GitLab 代理的**端到端**：真 git client → 真代理 → 真 TLS 上游（ADR 0016）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography python tests/test_gitlab_upstream_e2e.py

需要 docker daemon。**不需要**連得到任何真的 GitLab，上游是本地起的假貨。

⚠ **這支補的是一個零覆蓋的缺口。** 在它之前，GitLab 那條路只被驗到「設定產得對」
  （`test_gitlab_proxy_conf`，離線）與「容器建得起來、token 不在 `docker inspect` 裡」
  （`test_user_proxy`，不轉發任何請求）。**沒有任何一支測試讓一個真的請求穿過代理**，
  所以「代理補上去的憑證，上游到底收不收」從來沒有被回答過（2026-08-08 盤點發現）。
  這正是 Day 29 第二種形狀：每個零件單獨測都是綠的，而整條路沒有人走過。

守的性質：
  🔴 **`git clone` 真的成功**，而且內容對得起來。git transport 不吃 `PRIVATE-TOKEN`，
     只吃 Basic——這件事只有讓真的 git 跑一次才問得出來
  🔴 **授權標頭分流正確**：git 路徑收到 `Authorization: Basic base64(oauth2:<PAT>)` 且
     **沒有** `PRIVATE-TOKEN`；API 路徑反過來。搞混的話 git 全部 401，而症狀指不到原因
  🔴 **自訂 CA 真的被用上**：上游是內部 CA 簽的憑證，代理信它才連得上
  🔴 **`proxy_ssl_verify on` 是真的**（負向控制）：換一把沒簽過它的 CA，同一個 clone
     必須失敗。少了這條，就算哪天有人把驗證關掉，上面那些斷言照樣全綠
  🔴 **PAT 不在 session 容器裡**：client 容器的 env 與 `docker inspect` 都翻不到

⚠ 所有容器與網路都帶 `CLAUDE_PTY_TEST_MARK`，正式 reconciler 據此跳過；命名前綴也被
  config 自動切開成 `claude-pty-test-*`。
"""
import base64
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import suppress

for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
# 🛡 同 test_user_proxy：標成測試建的 ＋ 把命名前綴切開。不可以拿掉。
os.environ["CLAUDE_PTY_TEST_MARK"] = "1"

FAKE_HOST = "fake-gitlab.test"
PAT = "glpat-FaKeToKeN0123456789"
CLIENT_IMAGE = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")

_tmp = tempfile.mkdtemp(prefix="claude-pty-glup-")
os.environ["CLAUDE_PTY_DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["CLAUDE_PTY_GITLAB_HOST"] = FAKE_HOST
os.environ["CLAUDE_PTY_GITLAB_CA_FILE"] = os.path.join(_tmp, "tls", "ca.pem")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docker  # noqa: E402

from server import config, user_proxy  # noqa: E402

config.GITLAB_HOST = FAKE_HOST
config.GITLAB_SSH_HOST = FAKE_HOST
config.GITLAB_CA_FILE = os.path.join(_tmp, "tls", "ca.pem")

from server import sessions  # noqa: E402

_fails = 0
def check(label, ok, detail=""):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n         {detail}" if detail and not ok else ""))


def sh(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


# --- 憑證：內部 CA 簽一張給 FAKE_HOST -------------------------------------------
#
# ⚠ 順帶把 `CLAUDE_PTY_GITLAB_CA_FILE` 這條路走過一遍。在這支之前，自訂 CA 只有離線的
#   設定檔測試守著（「conf 裡有沒有指到那個路徑」），沒有人問過「指過去之後 TLS 通不通」。
def make_certs(dirpath, ca_cn="claude-pty test CA"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    os.makedirs(dirpath, exist_ok=True)
    now = _dt.datetime.now(_dt.timezone.utc)

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ca_cn)])
    ca_cert = (x509.CertificateBuilder()
               .subject_name(ca_name).issuer_name(ca_name)
               .public_key(ca_key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - _dt.timedelta(days=1))
               .not_valid_after(now + _dt.timedelta(days=2))
               .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
               .sign(ca_key, hashes.SHA256()))

    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv_cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, FAKE_HOST)]))
                .issuer_name(ca_name)
                .public_key(srv_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - _dt.timedelta(days=1))
                .not_valid_after(now + _dt.timedelta(days=2))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(FAKE_HOST)]),
                               critical=False)
                .sign(ca_key, hashes.SHA256()))

    enc = serialization.Encoding.PEM
    with open(os.path.join(dirpath, "ca.pem"), "wb") as f:
        f.write(ca_cert.public_bytes(enc))
    with open(os.path.join(dirpath, "server.pem"), "wb") as f:
        f.write(srv_cert.public_bytes(enc))
    with open(os.path.join(dirpath, "server.key"), "wb") as f:
        f.write(srv_key.private_bytes(
            enc, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    os.chmod(os.path.join(dirpath, "server.key"), 0o644)   # 容器裡是 root，但保險


TLS = os.path.join(_tmp, "tls")
make_certs(TLS)
# 負向控制用：另一把完全無關的 CA（沒簽過上面那張伺服器憑證）
OTHER = os.path.join(_tmp, "tls-other")
make_certs(OTHER, ca_cn="unrelated CA")


# --- 要被 clone 的 repo ----------------------------------------------------------
REPOS = os.path.join(_tmp, "repos")
WORK = os.path.join(_tmp, "work")
os.makedirs(os.path.join(REPOS, "grp"), exist_ok=True)
sh("git", "init", "--quiet", "--bare", os.path.join(REPOS, "grp", "repo.git"))
os.makedirs(WORK)
sh("git", "init", "--quiet", "-b", "main", WORK)
with open(os.path.join(WORK, "HELLO.txt"), "w", encoding="utf-8") as f:
    f.write("proxied-clone-ok\n")
for cmd in (("git", "add", "HELLO.txt"),
            ("git", "-c", "user.email=t@example.com", "-c", "user.name=t",
             "commit", "--quiet", "-m", "seed"),
            ("git", "push", "--quiet", os.path.join(REPOS, "grp", "repo.git"), "main")):
    r = sh(*cmd, cwd=WORK)
    if r.returncode != 0:
        print("種 repo 失敗：", r.stderr.strip()[:200])
        sys.exit(1)
shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_gitlab.py"),
            os.path.join(_tmp, "fake_gitlab.py"))


# --- docker 場景 -----------------------------------------------------------------
cli = docker.from_env(timeout=60)
UID = 1
NET = user_proxy.network_name(UID)
UP_NAME = "claude-pty-test-fake-gitlab"
made = []

def cleanup():
    for name in (UP_NAME,):
        with suppress(Exception):
            cli.containers.get(name).remove(force=True)
    with suppress(Exception):
        c = user_proxy.find(cli, UID)
        if c:
            c.remove(force=True)
    with suppress(Exception):
        net = cli.networks.get(NET)
        for cid in (net.attrs.get("Containers") or {}):
            with suppress(Exception):
                net.disconnect(cid, force=True)
        net.remove()
    shutil.rmtree(_tmp, ignore_errors=True)


def start_upstream():
    """假上游，掛在使用者網路上，alias ＝ 設定裡的 GitLab 主機名。

    ⚠ **alias 要在建立當下就給**（低階 API 的 `networking_config`），而且這顆要比代理
      先起來：nginx 在**啟動時**就解析 `upstream` 的主機名，解不開會直接
      `[emerg] host not found in upstream` 拒絕啟動。上層 `containers.run(network=…)`
      會把 alias 默默丟掉（`user_proxy.create_or_adopt` 的註解記的是同一個坑）。
    """
    return cli.api.create_container(
        CLIENT_IMAGE, name=UP_NAME, user="0:0",
        entrypoint=["python3", "/srv/fake_gitlab.py"],
        labels={config.TEST_LABEL_DEFAULT_KEY: config.TEST_MARK},
        host_config=cli.api.create_host_config(
            network_mode=NET,
            binds={_tmp: {"bind": "/srv", "mode": "rw"}}),
        networking_config=cli.api.create_networking_config(
            {NET: cli.api.create_endpoint_config(aliases=[FAKE_HOST])}))


def run_client(script: str, env: dict):
    """在同一張網路上跑一個一次性 client 容器，回傳 (exit, stdout+stderr)。"""
    c = cli.api.create_container(
        CLIENT_IMAGE, entrypoint=["sh", "-lc", script], environment=env,
        labels={config.TEST_LABEL_DEFAULT_KEY: config.TEST_MARK},
        host_config=cli.api.create_host_config(network_mode=NET))
    cid = c["Id"]
    try:
        cli.api.start(cid)
        rc = cli.api.wait(cid, timeout=180)["StatusCode"]
        out = cli.api.logs(cid, stdout=True, stderr=True).decode(errors="replace")
        return rc, out, cid
    finally:
        pass          # 容器留著讓 inspect 檢查，最後統一收


def upstream_log():
    p = os.path.join(_tmp, "requests.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


try:
    print("== 場景：上游（TLS，內部 CA 簽）→ 代理（真設定）→ client（真 git）==")
    user_proxy.ensure_network(cli, UID)
    up = start_upstream()
    cli.api.start(up["Id"])
    # 等它真的在聽（不是等固定秒數：`docker ps` 說 Up 不代表 443 有人聽）
    ready = False
    for _ in range(60):
        logs = cli.api.logs(up["Id"], stdout=True, stderr=True).decode(errors="replace")
        if "fake-gitlab ready" in logs:
            ready = True
            break
        time.sleep(0.5)
    check("🔴 假上游起得來並在 :443 聽（起不來的話下面全部無效）", ready,
          cli.api.logs(up["Id"], stdout=True, stderr=True).decode(errors="replace")[-400:])
    if not ready:
        raise SystemExit(1)

    _, created = user_proxy.create_or_adopt(cli, UID, PAT)
    check("代理建起來了（走的是正式那條 create_or_adopt）", created)

    genv = sessions._gitlab_env()
    check("env 產得出 insteadOf 三條（沒有的話 client 會直連上游而不是走代理）",
          genv.get("GIT_CONFIG_COUNT") == "3")

    # nginx 起來要一點時間；用 /ping 探（它不經上游，所以只回答「代理在不在」）
    up_ok = False
    for _ in range(40):
        rc, out, cid = run_client(
            f"curl -sf -m 3 -o /dev/null -w '%{{http_code}}' {config.PROXY_BASE_URL}/ping", {})
        with suppress(Exception):
            cli.api.remove_container(cid, force=True)
        if rc == 0 and "200" in out:
            up_ok = True
            break
        time.sleep(0.5)
    check("🔴 代理在（/ping 回 200，這條不經上游、沒有 PAT 也答得出來）", up_ok)

    print("\n== 正向：真的 clone ==")
    clone_env = dict(genv)
    rc, out, cid = run_client(
        "set -e; "
        f"git clone --quiet git@{FAKE_HOST}:grp/repo.git /tmp/out 2>&1; "
        "echo '---CONTENT---'; cat /tmp/out/HELLO.txt; "
        "echo '---REMOTE---'; git -C /tmp/out remote get-url origin",
        clone_env)
    clone_cid = cid
    check("🔴 `git clone` 成功（整條路：insteadOf → 代理 → TLS 上游 → packfile 回來）",
          rc == 0, out[-600:])
    check("🔴 拉回來的內容是對的（成功不等於拿到東西）",
          "proxied-clone-ok" in out, out[-400:])
    check("遠端存的是改寫後的網址（證明走的真是代理，不是直連）",
          f"{config.PROXY_BASE_URL}/grp/repo.git" in out, out[-300:])

    print("\n== 上游收到什麼：授權標頭必須分流正確 ==")
    log = upstream_log()
    git_reqs = [r for r in log if ".git/" in r["path"]]
    check("上游確實收到 git 請求（收不到＝根本沒穿過代理）", bool(git_reqs),
          f"共 {len(log)} 筆：{[r['path'] for r in log][:5]}")

    want_basic = "Basic " + base64.b64encode(f"oauth2:{PAT}".encode()).decode()
    if git_reqs:
        check("🔴 git 路徑帶 `Authorization: Basic base64(oauth2:<PAT>)`",
              all(r["headers"].get("authorization") == want_basic for r in git_reqs),
              str([r["headers"].get("authorization") for r in git_reqs])[:200])
        check("🔴 git 路徑**沒有** PRIVATE-TOKEN（授權標頭放 server 層的話會全部 401）",
              all("private-token" not in r["headers"] for r in git_reqs))
        check("Host 標頭被改寫成上游的主機名",
              all(r["headers"].get("host") == FAKE_HOST for r in git_reqs),
              str([r["headers"].get("host") for r in git_reqs])[:120])
        check("clone 走的是 upload-pack 那兩步（info/refs 的 GET ＋ POST）",
              any(r["path"].endswith("/info/refs") for r in git_reqs)
              and any(r["path"].endswith("/git-upload-pack") and r["method"] == "POST"
                      for r in git_reqs))
        posts = [r for r in git_reqs if r["method"] == "POST"]
        check("🔴 POST 的請求本體有到上游（proxy_request_buffering off ⇒ chunked，"
              "上游讀不到就會是空的）",
              bool(posts) and all(r["body_len"] > 0 for r in posts),
              str([(r["path"], r["body_len"]) for r in posts]))

    print("\n== API 路徑：換另一種授權 ==")
    rc, out, cid = run_client(
        f"curl -sS -m 10 -o /dev/null -w '%{{http_code}}' "
        f"{config.PROXY_BASE_URL}/api/v4/user", dict(genv))
    with suppress(Exception):
        cli.api.remove_container(cid, force=True)
    check("🔴 `/api/v4/user` 通到上游（200）", "200" in out, out[-200:])
    api_reqs = [r for r in upstream_log() if r["path"].startswith("/api/v4/")]
    if api_reqs:
        check("🔴 API 路徑帶 `PRIVATE-TOKEN: <PAT>`",
              all(r["headers"].get("private-token") == PAT for r in api_reqs))
        check("🔴 API 路徑**沒有** Authorization（兩種授權不可以互相污染）",
              all("authorization" not in r["headers"] for r in api_reqs))
        check("🔴 API 路徑的 Host 也要是上游主機名（同 git 那條，見下方說明）",
              all(r["headers"].get("host") == FAKE_HOST for r in api_reqs),
              str([r["headers"].get("host") for r in api_reqs])[:120])

    print("\n== 白名單地板：沒列到的端點不准 ==")
    rc, out, cid = run_client(
        f"curl -sS -m 10 -o /dev/null -w '%{{http_code}}' "
        f"{config.PROXY_BASE_URL}/api/v4/projects", dict(genv))
    with suppress(Exception):
        cli.api.remove_container(cid, force=True)
    check("🔴 未白名單的 API 回 403，而且**沒有**打到上游",
          "403" in out and not any(r["path"] == "/api/v4/projects" for r in upstream_log()),
          out[-200:])

    print("\n== PAT 不在 client 容器裡 ==")
    insp = json.dumps(cli.api.inspect_container(clone_cid), ensure_ascii=False)
    check("🔴 `docker inspect` 整包翻不到 PAT", PAT not in insp)
    rc, out, cid = run_client("env; echo ---; cat /proc/1/environ | tr '\\0' '\\n'", dict(genv))
    with suppress(Exception):
        cli.api.remove_container(cid, force=True)
    check("🔴 容器內的環境變數翻不到 PAT（憑證只活在代理的檔案系統裡）", PAT not in out)
    with suppress(Exception):
        cli.api.remove_container(clone_cid, force=True)

    print("\n== 負向控制：把 CA 換成沒簽過它的那一把，同一個 clone 必須失敗 ==")
    # ⚠ 沒有這一段，就算哪天有人把 `proxy_ssl_verify` 關掉，上面每一條照樣全綠。
    #   驗證「該通的通」永遠要配一條「該不通的不通」，否則量的是裝置不是性質。
    before = len([r for r in upstream_log() if ".git/" in r["path"]])
    with suppress(Exception):
        user_proxy.find(cli, UID).remove(force=True)
    config.GITLAB_CA_FILE = os.path.join(OTHER, "ca.pem")
    user_proxy.create_or_adopt(cli, UID, PAT)
    ok = False
    for _ in range(40):
        rc, out, cid = run_client(
            f"curl -sf -m 3 -o /dev/null -w '%{{http_code}}' {config.PROXY_BASE_URL}/ping", {})
        with suppress(Exception):
            cli.api.remove_container(cid, force=True)
        if rc == 0 and "200" in out:
            ok = True
            break
        time.sleep(0.5)
    check("換 CA 之後代理仍然起得來（要失敗的是 TLS，不是 nginx）", ok)
    rc, out, cid = run_client(
        f"git clone --quiet git@{FAKE_HOST}:grp/repo.git /tmp/out2 2>&1", dict(genv))
    with suppress(Exception):
        cli.api.remove_container(cid, force=True)
    check("🔴 clone 失敗（proxy_ssl_verify on 是真的在驗，不是擺著好看）", rc != 0,
          out[-300:])
    after = [r for r in upstream_log() if ".git/" in r["path"]]
    check("🔴 而且請求**根本沒到上游**（在 TLS 那關就被擋下，不是上游拒絕）",
          len(after) == before, f"{before} → {len(after)}")

finally:
    cleanup()

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
