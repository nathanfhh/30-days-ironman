"""ttyd 觀測面：在聽哪個 port、有幾個人連著、誰在跑卻不在 DB 裡。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with cryptography python tests/test_ttyd_inspect.py

**不需要 docker，也不需要真的 psutil**——psutil 是假的，才控制得住「行程長什麼樣」。

## 為什麼需要這一支

`docker ps` 說 Up、DB 說那個 port 是我的，都不等於**那顆行程真的在聽、真的有人連著**。
這一頁回答的就是那三個問題，而它自己也會出錯：只要孤兒判斷寫錯，畫面上要嘛漏報
（那個 port 就此消失，沒有任何機制找得到），要嘛例行假警報（把健康的 ttyd 標成孤兒，
整頁失去可信度）。

守的性質：
  🔴 **孤兒判斷用 port 交叉比對，不是只比 pid。** 開終端有一段「ttyd 已經在跑、pid
     還沒寫回 DB」的窗口（`_claim_port` 先插 pid=NULL 的列）。只比 pid 的話那顆健康的
     ttyd 會被標成孤兒——這是**例行**假警報，不是邊角情境
  🔴 真的孤兒（聽的 port 沒有任何一列宣告）要被抓出來
  🔴 `listening` 與 `clients` 真的來自那個行程的 socket
  🔴 pid 還沒寫回來的列是 `alive=None`（**還不知道**），不是 `False`（死了）
  🔴 **沒有 psutil 時要明講 `psutil: False`**。不講的話畫面上那個空的 orphans 看起來
     就像「掃過了，很乾淨」，而那正是這一頁要抓的那種假綠燈
  🔴 `_proc_facts` 對讀不到的項目只跳過該項，不讓整頁掛掉；而且**不吞程式錯誤**
"""

import os
import sys
import tempfile

os.environ["CLAUDE_PTY_NO_MOUNTS"] = "1"
_tmp = tempfile.mkdtemp(prefix="claude-pty-inspect-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config, db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
config.SECRET_KEY = "inspect-secret"
config.MOUNTS = {}

from server import auth, views  # noqa: E402
from server.models import STATUS_RUNNING, Session, View  # noqa: E402

db.reset_engine()
db.init_db()

_fails = 0


def check(label, ok, detail=""):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n         {detail}" if detail and not ok else ""))


# --- 假 psutil ------------------------------------------------------------------
#
# ⚠ 用假的而不是真的，是因為這支要驗的是**判斷邏輯**，而真 psutil 給的東西取決於這台
#   機器此刻在跑什麼——那會讓測試變成「今天剛好沒有別的 ttyd」才綠。


class _Err(Exception):
    pass


class _Conn:
    def __init__(self, status, ip=None, port=None):
        self.status = status
        self.laddr = type("A", (), {"ip": ip, "port": port})()


class _Cpu:
    user = 1.25
    system = 0.5


class _Mem:
    rss = 4096
    vms = 8192


class _Ctx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


class _Proc:
    """一顆假的 ttyd 行程。`fail` 用來模擬「這一項問不到」。"""

    def __init__(self, pid, bin_name="ttyd", listen_port=None, clients=0, fail=()):
        self.pid = pid
        self._bin = bin_name
        self._listen = listen_port
        self._clients = clients
        self._fail = set(fail)
        self.info = {"pid": pid, "cmdline": [f"/usr/local/bin/{bin_name}", "-q"]}

    def _maybe(self, name):
        if name in self._fail:
            raise _Err(name)
        # ⚠ `_Err` 是 psutil 的錯誤類別（見 _Fake.Error）——也就是 _proc_facts **設計要吞掉**
        #   的那一種。只有它的話，檔頭那句「不吞程式錯誤」沒有任何東西在守：psutil 版本不對
        #   而 `net_connections` 不存在時會是 AttributeError，那要傳播出來而不是被吸收
        #   （審查 F-031）。`bug:<項目>` 這個模式就是拿來丟那一種的。
        if f"bug:{name}" in self._fail:
            raise TypeError(f"模擬的程式錯誤（{name}）——這一種不可以被吞掉")

    def cmdline(self):
        self._maybe("cmdline")
        return self.info["cmdline"]

    def oneshot(self):
        self._maybe("oneshot")
        return _Ctx()

    def cpu_times(self):
        return _Cpu()

    def memory_info(self):
        return _Mem()

    def status(self):
        return "sleeping"

    def create_time(self):
        return 1_700_000_000.0

    def memory_percent(self):
        return 0.125

    def num_threads(self):
        return 3

    def num_fds(self):
        return 12

    def cpu_percent(self, _iv):
        return 0.0

    def net_connections(self, kind="tcp"):
        self._maybe("net_connections")
        out = []
        if self._listen is not None:
            out.append(_Conn(_Fake.CONN_LISTEN, "0.0.0.0", self._listen))
        out += [_Conn(_Fake.CONN_ESTABLISHED) for _ in range(self._clients)]
        return out


class _Fake:
    CONN_LISTEN = "LISTEN"
    CONN_ESTABLISHED = "ESTABLISHED"
    Error = _Err
    procs: list = []

    @staticmethod
    def process_iter(_attrs=None):
        return list(_Fake.procs)


_real_psutil = views.psutil
views.psutil = _Fake


def with_procs(*procs):
    _Fake.procs = list(procs)


# --- 佈景：一個使用者、兩場 session、兩個 view ---------------------------------------
uid = auth.create_user("inspect-user", "inspect-password-1")["id"]
with db.session_scope() as s:
    s.add(
        Session(
            id="sessAAAA",
            container_name="claude-pty-sessAAAA",
            user_id=uid,
            status=STATUS_RUNNING,
            display_name="第一場",
        )
    )
    s.add(
        Session(
            id="sessBBBB",
            container_name="claude-pty-sessBBBB",
            user_id=uid,
            status=STATUS_RUNNING,
            display_name="第二場",
        )
    )
with db.session_scope() as s:
    s.add(View(session_id="sessAAAA", port=41000, pid=1111, ttyd_bin="rust"))
    # ⚠ pid=None：這就是「已經在跑、pid 還沒寫回來」那個 in-flight 的列
    s.add(View(session_id="sessBBBB", port=41001, pid=None, ttyd_bin="c"))


print("== 基本：DB 那一半 ==")
with_procs(_Proc(1111, "ttyd-rust", listen_port=41000, clients=2))
res = views.inspect_ttyd("rust")
rows = {r["port"]: r for r in res["views"]}
check("兩列都在", set(rows) == {41000, 41001})
check("帶得出擁有者與 session 名稱", rows[41000]["owner"] == "inspect-user" and rows[41000]["session_name"] == "第一場")
check(
    "🔴 pid 還沒寫回來的列是 alive=None（還不知道），不是 False（死了）",
    rows[41001]["alive"] is None,
    repr(rows[41001]["alive"]),
)
check("有 pid 且行程在 → alive=True", rows[41000]["alive"] is True)
check(
    "每一列帶當初起它的那顆 ttyd（不是現在的偏好）",
    rows[41000]["ttyd_bin"] == "rust" and rows[41001]["ttyd_bin"] == "c",
)


print("\n== listening 與 clients 真的來自那個行程的 socket ==")
proc = rows[41000]["proc"]
check("🔴 listening 是實際在聽的位址", proc["listening"] == ["0.0.0.0:41000"], repr(proc))
check("🔴 clients＝已建立的連線數（現在有幾個人開著這個終端）", proc["clients"] == 2)
check("執行檔名取的是 argv[0] 的 basename", proc["bin"] == "ttyd-rust")
check("量測值帶得出來（rss / threads / fds）", proc["rss"] == 4096 and proc["threads"] == 3 and proc["fds"] == 12)
# ⚠ 沒有人連著的時候，`clients` 要是 **0**，不可以是「缺這個鍵」。缺鍵在畫面上會呈現成
#   「不知道」，而「沒有人在看」跟「不知道有沒有人在看」是完全不同的兩件事——前者解釋了
#   ttyd 為什麼還活著（`-q` 還沒觸發），後者什麼都沒解釋。
with_procs(_Proc(1111, "ttyd-rust", listen_port=41000, clients=0))
idle = {r["port"]: r for r in views.inspect_ttyd("rust")["views"]}[41000]["proc"]
check("🔴 沒人連著時 clients 是 0，不是缺這個鍵", "clients" in idle and idle["clients"] == 0, repr(idle))


print("\n== 孤兒：port 交叉比對，不是只比 pid ==")
# 9999 不在 DB 的 pid 集合裡，但它聽的 41001 **已經被那個 in-flight 的列宣告**。
with_procs(_Proc(1111, "ttyd-rust", listen_port=41000, clients=1), _Proc(9999, "ttyd", listen_port=41001, clients=0))
res = views.inspect_ttyd("rust")
check("🔴 pid 對不上、但 port 被宣告了 → 不是孤兒（正在被領養）", res["orphans"] == [], str(res["orphans"])[:200])

# 換一個沒有人宣告的 port：這才是真的孤兒
with_procs(_Proc(1111, "ttyd-rust", listen_port=41000, clients=1), _Proc(8888, "ttyd", listen_port=47777, clients=0))
res = views.inspect_ttyd("rust")
check(
    "🔴 聽的 port 沒有任何一列宣告 → 真的孤兒，要抓出來",
    [o["pid"] for o in res["orphans"]] == [8888],
    str(res["orphans"])[:200],
)
check("孤兒也帶得出它在聽哪裡", res["orphans"][0]["proc"]["listening"] == ["0.0.0.0:47777"])

# 完全問不到 socket 的行程：不能因為「不知道它聽哪裡」就當成不是孤兒
with_procs(_Proc(7777, "ttyd", listen_port=41000, fail=("net_connections",)))
res = views.inspect_ttyd("rust")
check(
    "🔴 問不到 socket 的陌生行程仍算孤兒（不知道≠安全）",
    [o["pid"] for o in res["orphans"]] == [7777],
    str(res["orphans"])[:200],
)

# 不是我們的 ttyd（執行檔名不在白名單）：完全不該進來
with_procs(_Proc(1111, "ttyd-rust", listen_port=41000), _Proc(6666, "nginx", listen_port=47778))
res = views.inspect_ttyd("rust")
check("別人的行程不會被掃進來（只認 config.TTYD_BINS 那幾個檔名）", res["orphans"] == [], str(res["orphans"])[:200])


print("\n== 部分讀不到：跳過該項，不讓整頁掛掉 ==")
# ⚠ 查列一律**按 port 查，不要用索引**：`views` 是照 created_at DESC 排的，`[0]` 是最後
#   建立的那一列（這裡是 pid=None 的那個 in-flight 列，它的 proc 本來就是 None）。
#   寫這支的時候用索引錯了兩次，兩次的症狀都是 NoneType。
with_procs(_Proc(1111, "ttyd-rust", listen_port=41000, clients=1, fail=("oneshot",)))
res = views.inspect_ttyd("rust")
p = {r["port"]: r for r in res["views"]}[41000]["proc"]
check("oneshot 那組讀不到 → 少那幾個鍵，但不拋", p is not None and "rss" not in p, repr(p))
check(
    "讀得到的那些照樣有（listening / clients 不受影響）",
    p.get("listening") == ["0.0.0.0:41000"] and p.get("clients") == 1,
)


print("\n== 沒有 psutil：只回 DB 那一半，而且要明講 ==")
views.psutil = None
res = views.inspect_ttyd("rust")
check("🔴 psutil=False（不講的話空的 orphans 看起來像「掃過了，很乾淨」）", res["psutil"] is False)
check("🔴 每一列的 alive 是 None 而不是 False（無從佐證≠死了）", all(r["alive"] is None for r in res["views"]))
check("proc 一律 None", all(r["proc"] is None for r in res["views"]))
check("orphans 是空的，但上面那個 psutil=False 已經說明了原因", res["orphans"] == [])
views.psutil = _Fake


print("== 🔴 程式錯誤要傳播，不可以跟 psutil 的「問不到」一起被吞掉 ==")
# _proc_facts 只 suppress psutil.Error 與 OSError。吞 Exception 的話，psutil 版本不對
# （例如 net_connections 改名）只會讓畫面少兩列、log 一片安靜——那正是那種版本 bug 可以
# 靜靜活好幾個月的機制。檔頭承諾了「不吞程式錯誤」，在此之前沒有任何斷言在守它：`fail=`
# 唯一丟得出來的 _Err 就是 psutil 的錯誤類別，也就是設計上**該**被吞的那一種（審查 F-031）。
views.psutil = _Fake
_bug_proc = _Proc(9999, "ttyd", listen_port=41000, fail=("bug:net_connections",))
_propagated = False
try:
    views._proc_facts(_bug_proc)
except TypeError:
    _propagated = True
check("🔴 TypeError 傳播出來（不是被當成「這一項問不到」吸收掉）", _propagated)
# 對照組：同一個項目丟 psutil 的錯誤時要照原樣被吞掉，其餘欄位仍然算得出來。
# 少了它，把 suppress 收得太緊（連 psutil.Error 都不吞）也會讓上面那條變綠。
_facts = views._proc_facts(_Proc(9998, "ttyd", listen_port=41000, fail=("net_connections",)))
check(
    "對照組：psutil 的「問不到」照樣被吞（少那一項，不影響其餘）",
    _facts is not None and "listening" not in _facts and _facts.get("pid") == 9998,
)

views.psutil = _real_psutil
db.reset_engine()
__import__("shutil").rmtree(_tmp, ignore_errors=True)
print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
