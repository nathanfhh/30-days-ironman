"""mitmweb relay 的生命週期與契約（ADR 0021）。不需 docker。

    uv run --with flask --with docker --with sqlalchemy --with psutil python tests/test_mitm_relay.py

relay 的真本事（socat + docker exec + mitmweb）由 test_mitm_bridge 對著真容器驗；
**這一支換掉 socat**，用一個同名的替身，好在幾秒內把那些「只在多 worker 或收尾時才發生、
在真環境要人手動製造」的路走完：port 仲裁、起收、archive 連帶收、殘留列回收。

替身取名 `socat` 是必要的而不是方便：`views._is_ours` 比對的是 argv[0] 的 basename，
那是 `_kill()` 送 SIGTERM 前的唯一把關。取別的名字的話，這支測到的就是另一條路。
"""

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
# 專屬 port 區段：與正式服務的 41200–41300 分開，免得「無殘留」那類檢查把別人的算進來。
os.environ["CLAUDE_PTY_MITM_PORT_MIN"] = "45200"
os.environ["CLAUDE_PTY_MITM_PORT_MAX"] = "45210"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config, db  # noqa: E402

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
# **不模擬 docker exec 那一段**——那一段由 test_mitm_bridge 對真的容器驗。
#
# ⚠ **必須用 `exec -a socat` 換掉 argv[0]，不可以只把檔名取作 socat。** 帶 shebang 的
#   腳本被 exec 時，核心組出來的 argv[0] 是**直譯器**（`#!/usr/bin/env python3` → `python3`），
#   於是 `views._is_ours` 的 basename 比對永遠不成立、`_process_alive` 一律回 False，
#   症狀是 `_wait_ready` 立刻逾時、整個範圍掃完報「無可用 port」——看起來完全像 port 的
#   問題（2026-08-26 實際踩到）。同 test_ttyd_identity 的手法，也同它那個 bash 的理由：
#   `exec -a` 是 bash builtin，dash 沒有。
_IMPL = os.path.join(_tmp, "fake_socat_impl.py")
with open(_IMPL, "w", encoding="utf-8") as _f:
    _f.write(
        "import os, socket, sys, threading\n"
        "open(os.environ['FAKE_SOCAT_ARGV'], 'a').write(repr(sys.argv[1:]) + '\\n')\n"
        "port = int(sys.argv[1].split('TCP-LISTEN:')[1].split(',')[0])\n"
        "srv = socket.socket()\n"
        "srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "srv.bind(('127.0.0.1', port))\n"
        "srv.listen(16)\n"
        "while True:\n"
        "    c, _ = srv.accept()\n"
        "    threading.Thread(target=lambda s=c: (s.recv(4096), s.sendall(\n"
        "        b'HTTP/1.1 403 Forbidden\\r\\nServer: mitmproxy 12.2.3\\r\\n\\r\\n'), s.close()),\n"
        "        daemon=True).start()\n"
    )
_FAKE = os.path.join(_tmp, "bin", "socat")
os.makedirs(os.path.dirname(_FAKE), exist_ok=True)
with open(_FAKE, "w", encoding="utf-8") as _f:
    _f.write(f'#!/bin/bash\nexec -a socat {sys.executable} {_IMPL} "$@"\n')
os.chmod(_FAKE, 0o755)
_ARGV_LOG = os.path.join(_tmp, "argv.log")
os.environ["FAKE_SOCAT_ARGV"] = _ARGV_LOG
os.environ["PATH"] = os.path.dirname(_FAKE) + os.pathsep + os.environ["PATH"]


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


try:
    print("== 契約：兩個 port 範圍不可以重疊（兩張表各自仲裁，跨表不會擋）==")
    # 這條看的是**預設值**，不是這支測試臨時設的區段。
    _d_ttyd = range(41000, 41101)
    check(
        "預設 41000–41100（ttyd）與 41200–41300（relay）不相交",
        41200 not in _d_ttyd and 41300 not in _d_ttyd and 41000 not in range(41200, 41301),
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

    print("== 殘留列回收：行程被外力收掉時，那一列不可以永遠佔著 port ==")
    _views_kill = views._kill(rb["pid"], mitm_views._OUR_RELAY_NAMES)
    check("先用外力收掉它", _views_kill and not mitm_views._process_alive(rb["pid"]))
    check("list_mitm_views 會把死掉的那一列清走（釋放 port）", mitm_views.list_mitm_views("relay0002") == [])
    check("DB 裡真的沒有了", alive_rows("relay0002") == [])

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
    for _sid in ("relay0001", "relay0002"):
        with __import__("contextlib").suppress(Exception):
            mitm_views.close_mitm_views(_sid)
    db.reset_engine()
    __import__("shutil").rmtree(_tmp, ignore_errors=True)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
