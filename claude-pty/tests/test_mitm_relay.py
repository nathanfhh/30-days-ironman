"""mitmweb relay 的生命週期與契約（ADR 0021）。不需 docker。

    uv run --with flask --with docker --with sqlalchemy --with psutil python tests/test_mitm_relay.py

relay 的真本事（socat + docker exec + mitmweb）由 test_mitm_bridge 對著真容器驗；
**這一支換掉 socat**，用一個同名的替身，好在幾秒內把那些「只在多 worker 或收尾時才發生、
在真環境要人手動製造」的路走完：port 仲裁、起收、archive 連帶收、殘留列回收。

替身取名 `socat` 是必要的而不是方便：`views._is_ours` 比對的是 argv[0] 的 basename，
那是 `_kill()` 送 SIGTERM 前的唯一把關。取別的名字的話，這支測到的就是另一條路。
"""

import importlib
import os
import socket
import sys
import tempfile
import time

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

_tmp = tempfile.mkdtemp(prefix="claude-pty-mitmrelay-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config  # noqa: E402

# ⚠ **先讀預設值，再覆寫。** 下面那條「兩個範圍不可以重疊」驗的是**出貨的預設**，
#   而這支測試自己要用一個專屬區段跑（免得「無殘留」那類檢查把正式服務的算進來）。
#   一旦覆寫下去，config 裡就再也讀不到預設了；抄一份字面值又會在有人改 config
#   的那天繼續比對舊的數字（那正是這條斷言要抓的事）。所以順序是：import、拍快照、
#   覆寫、reload。
_DEFAULT_TTYD_PORTS = range(config.TTYD_PORT_MIN, config.TTYD_PORT_MAX + 1)
_DEFAULT_MITM_PORTS = range(config.MITM_PORT_MIN, config.MITM_PORT_MAX + 1)

# 專屬 port 區段：與正式服務的那一段分開。
os.environ["CLAUDE_PTY_MITM_PORT_MIN"] = "45200"
os.environ["CLAUDE_PTY_MITM_PORT_MAX"] = "45210"
importlib.reload(config)

from server import db  # noqa: E402

config.DB_URL = os.environ["CLAUDE_PTY_DB_URL"]
db.reset_engine()
db.init_db()

from server import mitm_views, sessions as sessions_mod, views  # noqa: E402
from server.models import MitmView, Session as SessionRow  # noqa: E402

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


# --- 假的 socat -------------------------------------------------------------------
#
# 只做 relay 對外的形狀：listen 在指定的 port、對任何連線回一個帶 `Server: mitmproxy`
# 的 HTTP 回應（＝ `_is_mitmweb_serving` 的判準），並把收到的 argv 寫下來供斷言。
# **不模擬 docker exec 那一段**：那一段由 test_mitm_bridge 對真的容器驗。
#
# ⚠ **必須用 `exec -a socat` 換掉 argv[0]，不可以只把檔名取作 socat。** 帶 shebang 的
#   腳本被 exec 時，核心組出來的 argv[0] 是**直譯器**（`#!/usr/bin/env python3` → `python3`），
#   於是 `views._is_ours` 的 basename 比對永遠不成立、`_process_alive` 一律回 False，
#   症狀是 `_wait_ready` 立刻逾時、整個範圍掃完報「無可用 port」，看起來完全像 port 的
#   問題（2026-08-26 實際踩到）。同 test_ttyd_identity 的手法，也同它那個 bash 的理由：
#   `exec -a` 是 bash builtin，dash 沒有。
_IMPL = os.path.join(_tmp, "fake_socat_impl.py")
with open(_IMPL, "w", encoding="utf-8") as _f:
    _f.write(
        "import os, socket, sys, threading\n"
        "open(os.environ['FAKE_SOCAT_ARGV'], 'a').write(repr(sys.argv[1:]) + '\\n')\n"
        # `FAKE_SOCAT_FORK_CHILD=1`：開場先起一個真 child 行程，模擬 listener 已經 fork
        # 出去、手上握著 WebSocket 的 child socat。Popen 的第一個元素是 argv[0]（"socat"，
        # 白名單認得的形狀），executable 才是真 binary；child 與本體同 pgid（不 setsid），
        # 那是 group 清理要證明收得到的對象。
        "if os.environ.get('FAKE_SOCAT_FORK_CHILD') == '1':\n"
        "    import subprocess\n"
        "    subprocess.Popen(['socat', '-c', 'import time; time.sleep(120)'],\n"
        "                     executable=sys.executable)\n"
        "port = int(sys.argv[1].split('TCP-LISTEN:')[1].split(',')[0])\n"
        "srv = socket.socket()\n"
        "srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "srv.bind(('127.0.0.1', port))\n"
        "srv.listen(16)\n"
        # `FAKE_SOCAT_UPSTREAM=dead` ＝ listener 起得來，但每條連線立刻被關掉，不回任何
        # 位元組。那正是真 socat 在「docker exec 進去 connect 127.0.0.1:8081 被 refuse」時
        # 對外呈現的樣子（EXEC 的那一端非 0 退出，socat 就收掉這條連線）。
        "dead = os.environ.get('FAKE_SOCAT_UPSTREAM') == 'dead'\n"
        "while True:\n"
        "    c, _ = srv.accept()\n"
        "    if dead:\n"
        "        c.close()\n"
        "        continue\n"
        "    threading.Thread(target=lambda s=c: (s.recv(4096), s.sendall(\n"
        "        b'HTTP/1.1 403 Forbidden\\r\\nServer: mitmproxy 12.2.3\\r\\n\\r\\n'), s.close()),\n"
        "        daemon=True).start()\n"
    )
_FAKE = os.path.join(_tmp, "bin", "socat")
os.makedirs(os.path.dirname(_FAKE), exist_ok=True)
with open(_FAKE, "w", encoding="utf-8") as _f:
    _f.write(f"#!/bin/bash\nexport PYTHONHOME='{sys.base_prefix}'\nexec -a socat {sys.executable} {_IMPL} \"$@\"\n")
os.chmod(_FAKE, 0o755)
_ARGV_LOG = os.path.join(_tmp, "argv.log")
os.environ["FAKE_SOCAT_ARGV"] = _ARGV_LOG
os.environ["PATH"] = os.path.dirname(_FAKE) + os.pathsep + os.environ["PATH"]

# --- 假的 docker ------------------------------------------------------------------
#
# `open_mitm_view` 在進 port 迴圈**之前**會先問一次「容器裡的 mitmweb 接得上嗎」，做法是
# `docker exec <cid> python3 -c …`。這支測試沒有 docker，所以給它一個同名替身，退出碼由
# `FAKE_DOCKER_EXIT` 決定：0 ＝上游活著（下面絕大多數情境要的），非 0 ＝接不上。
#
# ⚠ 用替身而不是把 `_mitmweb_reachable` 換掉（monkeypatch），是為了讓那一支**真的被執行**：
#   它組 argv 的方式（不經 shell、container id 只當一個位置參數）也是這支測試該守的東西。
_FAKE_DOCKER = os.path.join(_tmp, "bin", "docker")
with open(_FAKE_DOCKER, "w", encoding="utf-8") as _f:
    _f.write('#!/bin/bash\necho "$@" >> "${FAKE_DOCKER_ARGV}"\nexit "${FAKE_DOCKER_EXIT:-0}"\n')
os.chmod(_FAKE_DOCKER, 0o755)
_DOCKER_LOG = os.path.join(_tmp, "docker.log")
os.environ["FAKE_DOCKER_ARGV"] = _DOCKER_LOG
os.environ["FAKE_DOCKER_EXIT"] = "0"


def socat_spawns() -> int:
    """替身 socat 到目前為止總共被起了幾次（＝ open_mitm_view 試過幾個 port）。

    ⚠ 記在檔案裡而不是數行程：要問的是「試了幾次」，而失敗的那幾顆早就被收掉了，
      事後數行程一律得到 0，那條斷言就永遠是綠的。
    """
    if not os.path.exists(_ARGV_LOG):
        return 0
    return len([ln for ln in open(_ARGV_LOG, encoding="utf-8").read().splitlines() if ln.strip()])


def live_fake_socats() -> int:
    """此刻還活著的替身 socat 有幾顆（不論 exec 完了沒有）。

    ⚠ 判準是 cmdline 裡有沒有這次測試的暫存目錄，不是「叫不叫 socat」：exec 之前它的
      argv[0] 是 `/bin/bash`，只認名字的話**漏掉的正好是要抓的那一種**。
    """
    if views.psutil is None:
        return 0
    n = 0
    for p in views.psutil.process_iter(["cmdline"]):
        with __import__("contextlib").suppress(Exception):
            if any(_tmp in a for a in (p.info["cmdline"] or [])):
                n += 1
    return n


def seed(sid: str) -> str:
    """建一筆 session 列（FK 是開著的，relay 的列一定要掛在真的 session 上）。"""
    uid = sessions_mod.ensure_system_user()
    with db.session_scope() as s:
        if s.get(SessionRow, sid) is None:
            s.add(SessionRow(id=sid, container_name=f"ctr-{sid}", user_id=uid, status="running", workdir="/tmp"))
    return f"cid{sid}"


def alive_rows(sid: str) -> list[MitmView]:
    with db.session_scope() as s:
        return list(s.query(MitmView).filter(MitmView.session_id == sid).all())


def _group_has_member(pgid: int | None) -> bool:
    """這個 pgid 底下現在還有我們認得的成員嗎（複用 production 的成員判斷）。

    psutil 不在（＝production 走「無從佐證」的降級路徑）就回 True：這一節的斷言會被
    上游的 `if _group_has_member(...)` 分岔帶到「略過」那一側，不會假綠。
    """
    if views.psutil is None:
        return True
    return bool(views._group_ours_members(pgid, mitm_views._OUR_RELAY_NAMES, config.MITM_BRIDGE))


try:
    print("== 契約：兩個 port 範圍不可以重疊（兩張表各自仲裁，跨表不會擋）==")
    # 🔴 比的是**整段對整段**，而且兩段都從 config 讀（見上面拍快照那段）。
    #    先前這裡寫的是四個字面值的端點比對：既抄了一份會漂的數字，也只驗到端點，
    #    把 MITM_PORT_MIN 改成 41050（整段落在 ttyd 範圍**裡面**）照樣全綠。
    check(
        f"預設 {config.TTYD_PORT_MIN}–{config.TTYD_PORT_MAX}（ttyd）與 "
        f"{_DEFAULT_MITM_PORTS.start}–{_DEFAULT_MITM_PORTS.stop - 1}（relay）不相交",
        set(_DEFAULT_TTYD_PORTS).isdisjoint(_DEFAULT_MITM_PORTS),
    )

    print("== 契約：socat 的位址不可以有空白（EXEC 依空白切詞、沒有引號機制）==")
    argv = mitm_views._socat_argv(45999, "deadbeefcafe")
    check("argv 共 3 個元素", len(argv) == 3)
    check("argv[0] 是 socat（＝ _is_ours 的白名單）", argv[0] == "socat")
    # 🔴 EXEC 那一段自己會再被切一次，所以「橋接腳本路徑 + 三個參數」各自都不可以有空白。
    _exec = argv[2].removeprefix("EXEC:")
    check("🔴 EXEC 段切出來剛好 4 個詞（路徑 + cid + port + linger）", len(_exec.split()) == 4)
    check("　└ 每一個詞都不含空白", all(" " not in w for w in _exec.split()))
    check("bind 用 config.TTYD_BIND（nginx 是別的容器，綁 loopback 它到不了）", f"bind={config.TTYD_BIND}" in argv[1])
    check("帶 reuseaddr（否則關掉再開會撞 TIME_WAIT）", "reuseaddr" in argv[1])

    print("== 契約：橋接腳本必須存在且可執行（socat 的 EXEC 走 execvp，不經 shell）==")
    check("檔案在", os.path.isfile(config.MITM_BRIDGE))
    check(
        "🔴 有執行位元（沒有的話 socat 每條連線都失敗，而 listener 照樣起得來）", os.access(config.MITM_BRIDGE, os.X_OK)
    )

    print("== 契約：橋接的內層是容器裡的 socat（docker exec -i，無 TTY，無 python 搬運）==")
    # 資料路徑的文字契約：control socat → docker exec -i → session 內 socat → 127.0.0.1:8081。
    # 這幾條不跑真容器（真容器那半在 test_mitm_bridge），只在這裡釘住腳本長什麼樣，
    # 免得哪天有人把 -it 加回去（TTY 會破壞 binary stream），或把 python 版搬運請回來。
    _bridge_src = open(config.MITM_BRIDGE, encoding="utf-8").read()
    # 只比對真正的指令行，註解不算數：把 # 起頭的行整行剃掉再看。
    _bridge_code = "\n".join(ln for ln in _bridge_src.splitlines() if not ln.lstrip().startswith("#"))
    check("🔴 用 `docker exec -i`（互動 stdin）", "docker exec -i " in _bridge_code)
    check("🔴 沒有 `-it`（TTY 會做行規則轉換，HTTP/WebSocket 的 binary 會被吃掉）", "exec -it" not in _bridge_code)
    check(
        "🔴 內層是容器裡的 socat（STDIO ⇄ TCP），不是手寫搬運",
        "socat -t " in _bridge_code and "STDIO" in _bridge_code,
    )
    check("🔴 上游仍是容器 loopback 的 mitmweb（127.0.0.1）", "TCP:127.0.0.1:" in _bridge_code)
    check("🔴 python 搬運真的走了（python3 -c 不再出現）", "python3 -c" not in _bridge_code)

    print("== 契約：容器內的 mitmweb port 與 entrypoint 的 CAPTURE_WEB_PORT 零偏差 ==")
    _ep = open(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "dev-container",
            "entrypoint.sh",
        ),
        encoding="utf-8",
    ).read()
    _decl = [ln for ln in _ep.splitlines() if ln.startswith("CAPTURE_WEB_PORT=")]
    check("🔴 entrypoint 找得到 CAPTURE_WEB_PORT 的定義（找不到＝這條測試失效了）", len(_decl) == 1)
    check(
        "🔴 兩邊逐字一致（對不上＝relay 連進去被 refuse，畫面 502，完全不像 port 寫錯）",
        bool(_decl) and _decl[0].split("=")[1].split("#")[0].strip().strip('"') == str(config.MITM_WEB_PORT),
    )

    print("== 起：真的把 relay 起來，而且會驗明正身才寫 pid ==")
    cid = seed("relay0001")
    r = mitm_views.open_mitm_view("relay0001", cid)
    check("回得出 port 與 pid", isinstance(r["port"], int) and isinstance(r["pid"], int))
    check("port 落在設定的範圍內", config.MITM_PORT_MIN <= r["port"] <= config.MITM_PORT_MAX)
    check("對外路徑帶尾斜線（SPA 是路徑相對的，少了它資源會解析錯）", r["path"] == "/session/relay0001/mitm/")
    check("那個 port 真的有人在聽", views._port_open(r["port"]))
    check(
        "🔴 就緒探測認的是 mitmweb 的 server 標頭（不是「port 開著就算」）", mitm_views._is_mitmweb_serving(r["port"])
    )
    check("DB 裡剛好一列，pid 已寫回", len(alive_rows("relay0001")) == 1 and alive_rows("relay0001")[0].pid == r["pid"])
    _got = [ln for ln in open(_ARGV_LOG, encoding="utf-8").read().splitlines() if str(r["port"]) in ln]
    check("socat 收到的就是我們組的那三個參數", len(_got) == 1 and "EXEC:" in _got[0] and cid in _got[0])

    print("== 🔴 DB 記錄了 process group，而且**在 readiness 之前**就寫 ==")
    # pgid 的用途：cleanup 要打整個 group（listener + fork children + docker exec client）。
    # 寫入時機是關鍵：若等 readiness 之後才寫，worker 死在等待中的話，那顆 socat 是
    # 「沒有人記得 pgid」的孤兒——reconciler 只能靠 leader pid 收，children 收不到。
    _row = alive_rows("relay0001")[0]
    check("🔴 process_group_id 已寫進 DB", isinstance(_row.process_group_id, int) and _row.process_group_id > 0)
    check("🔴 ready 已標成 True（peek 只認 ready 的列）", _row.ready is True)
    check(
        "🔴 記的 pgid 就是 listener 現在的 pgid（同一個 group，children 都在裡面）",
        _row.process_group_id == views._process_group_id(r["pid"]),
    )

    print("== 點兩次不會多起一條（沿用既有的）==")
    r2 = mitm_views.open_mitm_view("relay0001", cid)
    check("回同一條", r2["port"] == r["port"] and r2["pid"] == r["pid"])
    check("DB 仍然只有一列", len(alive_rows("relay0001")) == 1)

    print("== port 仲裁：另一場拿到的是**別的** port ==")
    cid_b = seed("relay0002")
    rb = mitm_views.open_mitm_view("relay0002", cid_b)
    check("兩場的 port 不同", rb["port"] != r["port"])
    check("兩顆行程都活著", mitm_views._process_alive(r["pid"]) and mitm_views._process_alive(rb["pid"]))

    print("== 收：archive 連帶把 relay 收掉（socat 沒有 `-q`，不收就永遠聽著）==")
    _port_a, _pid_a = r["port"], r["pid"]
    sessions_mod.archive(["relay0001"], "terminated")
    deadline = time.time() + 10
    while time.time() < deadline and mitm_views._process_alive(_pid_a):
        time.sleep(0.2)
    check("🔴 relay 的行程真的不在了（不是只刪了 DB 那一列）", not mitm_views._process_alive(_pid_a))
    check("DB 那一列也走了", alive_rows("relay0001") == [])
    with socket.socket() as _s:
        _s.settimeout(1.0)
        check("port 已經釋放（連不上）", _s.connect_ex(("127.0.0.1", _port_a)) != 0)
    check("另一場不受影響", mitm_views._process_alive(rb["pid"]))

    print("== 🔴 只殺 listener 時 fork child 仍活著；完整 cleanup 連 child 一起收 ==")
    # fake socat 開場先起一個真 child 行程（argv[0] 也是 socat），模擬「每條連線 fork 一個
    # child socat」：child 與 listener 同 group——那正是實際部署的形狀，也是 group 清理
    # 存在的理由。psutil 不在的環境驗不了 group 成員，這一節直接略過（test_auth 對真
    # 容器的那半會補上）。
    os.environ["FAKE_SOCAT_FORK_CHILD"] = "1"
    rc_child = seed("relaychild")
    r_child = mitm_views.open_mitm_view("relaychild", rc_child)
    _child_row = alive_rows("relaychild")[0]
    _child_pid = _child_row.pid
    time.sleep(1.0)  # 等 child 行程穩定
    if _group_has_member(_child_row.process_group_id):
        # 先只殺 leader（不經過 group 的路徑）：child 必須還活著。
        os.kill(_child_pid, views.signal.SIGTERM)
        deadline = time.time() + 5
        while time.time() < deadline and mitm_views._pid_exists(_child_pid):
            time.sleep(0.1)
        check("🔴 外力只收 listener 之後，DB 列還在（group 還有成員）", alive_rows("relaychild") != [])
        check(
            "🔴 group 裡仍有我們認得的成員（child 沒有跟著 leader 死）", _group_has_member(_child_row.process_group_id)
        )
        # 完整 cleanup（close_mitm_views → _kill_row → killpg）把 child 一起收掉。
        mitm_views.close_mitm_views("relaychild")
        deadline = time.time() + 10
        while time.time() < deadline and _group_has_member(_child_row.process_group_id):
            time.sleep(0.2)
        check(
            "🔴 close_mitm_views 收掉的是整個 group（child 也走乾淨）",
            not _group_has_member(_child_row.process_group_id),
        )
        check("　└ DB 那一列也刪了（收乾淨才准刪）", alive_rows("relaychild") == [])
    else:
        check("🔴 group 裡仍有我們認得的成員（child 沒有跟著 leader 死）", False)
    os.environ.pop("FAKE_SOCAT_FORK_CHILD", None)

    print("== 殘留列回收：行程被外力收掉時，那一列不可以永遠佔著 port ==")
    _views_kill = views._kill(rb["pid"], mitm_views._OUR_RELAY_NAMES)
    check("先用外力收掉它", _views_kill and not mitm_views._process_alive(rb["pid"]))
    check("list_mitm_views 會把死掉的那一列清走（釋放 port）", mitm_views.list_mitm_views("relay0002") == [])
    check("DB 裡真的沒有了", alive_rows("relay0002") == [])

    print("== 🔴 peek_mitm_view 是純查詢：不刪列、不殺行程 ==")
    rc_peek = seed("relaypeek")
    rp = mitm_views.open_mitm_view("relaypeek", rc_peek)
    check("peek 回得到可用的 relay", (mitm_views.peek_mitm_view("relaypeek") or {}).get("port") == rp["port"])
    check("peek 之後 DB 仍在、行程仍活", len(alive_rows("relaypeek")) == 1 and mitm_views._process_alive(rp["pid"]))
    # 把 ready 弄成 False（＝還在等就緒或 worker 死在半路）：peek 不可以把它當可用。
    with db.session_scope(immediate=True) as _s:
        _s.get(mitm_views.MitmView, alive_rows("relaypeek")[0].id).ready = False
    check("🔴 ready=False 的列不是可用 relay（就算程序還活著）", mitm_views.peek_mitm_view("relaypeek") is None)
    mitm_views.close_mitm_views("relaypeek")

    print("== 🔴 cleanup 失敗時，tracking row 必須保留（刪了＝永久孤兒）==")
    rc_stuck = seed("relaystuck")
    rs = mitm_views.open_mitm_view("relaystuck", rc_stuck)
    _stuck_row = alive_rows("relaystuck")[0]
    _orig_kpg = views._kill_process_group
    views._kill_process_group = lambda *a, **k: False  # 模擬「送訊號了但收不掉」
    try:
        _raised = None
        try:
            mitm_views.close_mitm_views("relaystuck")
        except mitm_views.MitmViewError as e:
            _raised = e
        check("🔴 close_mitm_views 對收不掉的 relay 拋出（不是靜靜成功）", _raised is not None)
        check("🔴 tracking row 還在（唯一的追蹤記錄，刪了＝沒人收得住那顆 socat）", alive_rows("relaystuck") != [])
        check("🔴 行程當然也還在", mitm_views._process_alive(rs["pid"]))
        # archive 同一個語義：blocked 的 sid 不歸檔，session 與 tracking row 都留下。
        _archived_exc = None
        try:
            sessions_mod.archive(["relaystuck"], "terminated")
        except Exception as e:
            _archived_exc = e
        check("🔴 cleanup 失敗 → archive 拒絕歸檔（拋出）", _archived_exc is not None)
        from server.models import Session as _SessionRow

        with db.session_scope() as _s:
            check("🔴 session 列還在（不可被 FK cascade 靜默帶走）", _s.get(_SessionRow, "relaystuck") is not None)
        check("🔴 mitm tracking row 也還在（供 reconciler 重試）", alive_rows("relaystuck") != [])
        # 恢復之後再收，才走得完：等冪的重試路徑。
        views._kill_process_group = _orig_kpg
        mitm_views.close_mitm_views("relaystuck")
        check("　└ 重試之後收乾淨", alive_rows("relaystuck") == [] and not mitm_views._process_alive(rs["pid"]))
    finally:
        views._kill_process_group = _orig_kpg

    print("== 🔴 spawn 之後、exec 成 socat 之前，那個行程也要收得掉 ==")
    # `_spawn_detached` 是 double-fork：`$!` 拿到的 pid 一開始是 `sh` 的，要等它 exec 完
    # argv[0] 才會變成 socat。`_kill()` 在送訊號前會比對 argv[0]，所以在那個窗口裡它會
    # 判定「不是我們的程序」直接回 True 什麼都不做，那顆行程就活著而沒有人記得它。
    # 這裡把窗口撐大（先睡再 exec）來釘住 `_kill_spawned` 有處理這件事。
    _slow = os.path.join(_tmp, "bin", "slow-socat")
    with open(_slow, "w", encoding="utf-8") as _f:
        _f.write("#!/bin/bash\nsleep 3\nexec -a socat sleep 60\n")
    os.chmod(_slow, 0o755)
    _slow_pid = views._spawn_detached([_slow])
    time.sleep(0.4)  # 還在 sleep，argv[0] 仍是 bash
    check(
        "窗口確實存在：行程在，但 `_process_alive` 認不得它",
        mitm_views._pid_exists(_slow_pid) and not mitm_views._process_alive(_slow_pid),
    )
    check(
        "🔴 `_kill()` 在這個窗口裡什麼都收不掉（所以不能只用它）",
        mitm_views._kill(_slow_pid) and mitm_views._pid_exists(_slow_pid),
    )
    check("🔴 `_kill_spawned()` 收得掉", mitm_views._kill_spawned(_slow_pid, grace=0.2))
    check("　└ 行程真的不在了", not mitm_views._pid_exists(_slow_pid))

    print("== 🔴 上游接不上時，不可以把整個 port 範圍每一個都試一遍（PR #3 Copilot）==")
    # 容器 running、`capture` 為真，但容器裡的 mitmweb 沒在服務（crash 或還在啟動）時，
    # 原本每一個 port 都會起一顆 socat 並等滿 `_wait_ready` 的逾時才換下一個
    # （socat 是 `TCP-LISTEN,fork`，父程序不會自己結束，只有逾時能離開那一圈）。
    # 2026-08-27 用三個 port 實測 60.6 秒（每個 20.2 秒），外推到出貨的 101 個 port ＝
    # **34 分鐘**的同步 `auth_mitm` 請求佔住一條 Flask 執行緒，外加 **12,928 次 docker exec**。
    _orig_ready = config.MITM_READY_TIMEOUT
    config.MITM_READY_TIMEOUT = 2.0  # 只縮短「一次逾時」，要驗的是次數不是秒數
    _sid3 = "relay0003"
    _cid3 = seed(_sid3)
    try:
        print("  -- (a) 起 relay 之前先探一次上游：探不到就不該進 port 迴圈 --")
        os.environ["FAKE_DOCKER_EXIT"] = "1"  # 容器裡的 mitmweb 接不上
        _n0, _t0, _raised = socat_spawns(), time.time(), None
        try:
            mitm_views.open_mitm_view(_sid3, _cid3)
        except mitm_views.MitmViewError as e:
            _raised = e
        _el = time.time() - _t0
        check(f"回 MitmNotReadyError 而不是別的（{_raised}）", isinstance(_raised, mitm_views.MitmNotReadyError))
        check("🔴 一顆 socat 都沒起（＝根本沒進 port 迴圈）", socat_spawns() == _n0)
        check(f"🔴 而且很快（{_el:.2f}s）", _el < 5)
        check("沒有留下任何列", alive_rows(_sid3) == [])
        # 訊息本身也是交付物：原本這條路回的是「41200-41300 無可用 port」，
        # 而 port 一個都沒少，跟 views 那個 exec 空窗是同一種「症狀指向 port」的誤導。
        check("錯誤訊息講的是 mitmweb 沒回應，不是「無可用 port」", "無可用 port" not in str(_raised))
        check("探測真的走了 docker exec（不是被 monkeypatch 掉）", "exec" in open(_DOCKER_LOG, encoding="utf-8").read())

        print("  -- (b) 探測過了、relay 也起得來，但上游在那之後才壞：只該試一個 port --")
        os.environ["FAKE_DOCKER_EXIT"] = "0"  # 探測那一發過得了
        os.environ["FAKE_SOCAT_UPSTREAM"] = "dead"  # 但 relay 接不到上游
        _n0, _t0, _raised = socat_spawns(), time.time(), None
        try:
            mitm_views.open_mitm_view(_sid3, _cid3)
        except mitm_views.MitmViewError as e:
            _raised = e
        _el = time.time() - _t0
        _tried = socat_spawns() - _n0
        check(f"回 MitmNotReadyError（{_raised}）", isinstance(_raised, mitm_views.MitmNotReadyError))
        check(f"🔴 只試了一個 port（試了 {_tried} 個）", _tried == 1)
        check(f"🔴 耗時是一次逾時的量級，不是範圍長度乘以逾時（{_el:.2f}s）", _el < 3 * config.MITM_READY_TIMEOUT)
        check("沒有留下任何列", alive_rows(_sid3) == [])
        check("🔴 沒有留下 socat（起了就要收掉，不然那個 port 永遠被佔著）", live_fake_socats() == 0)

        print("  -- (c) 反向：port 被**別的**服務佔住時，還是要換下一個 port --")
        # ⚠ 這一條守的是上面那個分岔**沒有收過頭**。判準若寫成 `views._port_open(port)`，
        #   別的服務佔住那個 port 時（我們的 socat 其實 bind 失敗已經退出）會被誤判成
        #   「上游壞掉」而整個放棄，但那正是該換下一個 port 的情況（ADR 0021 的安全網）。
        os.environ.pop("FAKE_SOCAT_UPSTREAM", None)
        _squat = socket.socket()
        _squat.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _squat.bind(("127.0.0.1", config.MITM_PORT_MIN))
        _squat.listen(64)
        # ⚠ **占位的那個 socket 必須真的 accept，backlog 也不能小。** 第一版寫的是
        #   `listen(1)` 而且沒有人收：`_port_open` 與 `_is_mitmweb_serving` 各連一次就把
        #   accept 佇列塞滿，之後的 connect 被拒，於是那個 port 從外面看起來是**關著的**。
        #   結果這一條在「判準只看 port 開著」的突變下照樣綠，也就是它其實沒有在驗那件事
        #   （2026-08-27 突變驗證抓到，這正是突變驗證要抓的東西）。
        _squat_stop = False

        def _drain():
            while not _squat_stop:
                try:
                    _c, _ = _squat.accept()
                except OSError:
                    return
                _c.close()

        _squat_thread = __import__("threading").Thread(target=_drain, daemon=True)
        _squat_thread.start()
        try:
            check(
                f"前提：被佔的那個 port 從外面看是開著的（{config.MITM_PORT_MIN}）",
                views._port_open(config.MITM_PORT_MIN),
            )
            _r3 = mitm_views.open_mitm_view(_sid3, _cid3)
            check(f"🔴 還是開起來了，而且跳過被佔的那個（got {_r3['port']}）", _r3["port"] != config.MITM_PORT_MIN)
            check("那個 port 真的有我們的 relay 在聽", views._port_open(_r3["port"]))
        except mitm_views.MitmViewError as e:
            check(f"🔴 不該失敗（port 被占是該換下一個，不是放棄）：{e}", False)
        finally:
            _squat_stop = True
            _squat.close()
            mitm_views.close_mitm_views(_sid3)
    finally:
        config.MITM_READY_TIMEOUT = _orig_ready
        os.environ["FAKE_DOCKER_EXIT"] = "0"
        os.environ.pop("FAKE_SOCAT_UPSTREAM", None)

    print("== 收一場沒有 relay 的：等冪，回 0 ==")
    check("close_mitm_views 對沒有 relay 的 session 回 0", mitm_views.close_mitm_views("relay0002") == 0)

    print("== 沒有容器 id 就不建（不是靜靜地起一條連不到任何東西的 relay）==")
    _raised = False
    try:
        mitm_views.open_mitm_view("relay0002", "")
    except mitm_views.MitmViewError:
        _raised = True
    check("空的 container id → MitmViewError", _raised)
    check("而且沒有留下任何列", alive_rows("relay0002") == [])
finally:
    # ⚠ relay0003 也要在這裡收。上游那一節中途拋例外時（例如有人把修法改壞了）內層的
    #   收尾不會跑到，那顆替身 socat 會留下來聽著 45200，**下一次跑這支測試會被它毒到**：
    #   新起的 socat bind 失敗，而就緒探測連上的是上一輪的殘留，於是紅在完全無關的地方。
    for _sid in ("relay0001", "relay0002", "relay0003"):
        with __import__("contextlib").suppress(Exception):
            mitm_views.close_mitm_views(_sid)
    db.reset_engine()
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
