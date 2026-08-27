"""橋接腳本 ⇄ 真的 mitmweb（ADR 0021），**需要 docker 與 build 好的 image**。

    uv run --with docker python tests/test_mitm_bridge.py

`test_mitm_relay` 用替身把 relay 的生命週期走完，**唯獨換掉了最容易出錯的那一段**：
`docker exec` 進容器、接上綁在 loopback 的 mitmweb、把兩個方向的位元組正確地搬過去。
這一支就對著真的東西驗那一段，而且不經外層 socat（host 上不一定有），直接餵 stdin 給腳本。

2026-08-27 起橋接的內層從 `python3 -c` 搬運換成**容器裡的 socat**
（`docker exec -i <cid> socat -t <linger> STDIO TCP:127.0.0.1:<port>`），所以這支測試也
要求 image 裡真的有 socat——舊的 image（沒有 socat）會在**第一條檢查**就紅，
而不是在後面以「拿不到回應」這種完全不像缺工具的樣子失敗。

守六件會靜靜壞掉的事：

  · **半關閉**。client 送完請求把寫入端關掉時，橋接不可以把讀方向一起砍掉：砍了的話
    回應被截斷，而畫面上只是「空白的 mitmweb」。socat 的 closewait（`-t`）接手了
    原本 python 的 linger，行為由 socat 保證，但這一條照測——它守的是整條鏈。
  · **Bearer 明文就是 web_password**。整個設計靠這一條：nginx 注入、使用者不必知道。
  · **沒帶就要被擋**，而且是 403 不是 401（12.2.3 實測），就緒探測的判準依賴這件事。
  · **WebSocket 握手過得了 origin 檢查，而且 binary frame 雙向都通**。
    帶 `Origin` 與之相符的 `Host`，tornado 的 `check_origin` 才放行（ADR 0021 第 5 處
    修正守的是 nginx 那一層的 Host 標頭；這裡守的是橋接本身不吃 binary）。
  · **上游不在時正確失敗**：內層 socat connect refuse → 非 0 退出，鏈路逐段收乾淨。
  · **收得乾淨**。橋接跑在**使用者的 session 容器裡**，漏一個就是漏在別人家裡。
"""

import base64
import os
import subprocess
import sys
import time

IMAGE = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(os.path.dirname(HERE), "server", "mitm_bridge.sh")
NAME = "claude-pty-mitmbridge-test"
PASSWORD = "TESTmitmPASSWORD00000abc"
WEB_PORT = "8081"

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def run(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


if run("docker", "version").returncode != 0:
    print("SKIP：docker 不可用")
    sys.exit(0)
if run("docker", "image", "inspect", IMAGE).returncode != 0:
    print(f"SKIP：找不到 image {IMAGE}（先跑 dev-container/build.sh）")
    sys.exit(0)


def through_bridge(cid: str, request: str, linger: str = "5") -> str:
    """把一發 HTTP 請求經橋接腳本送進容器內的 mitmweb，回傳原始回應（文字模式）。

    ⚠ **寫完不可以立刻關掉 stdin。** `subprocess.run(input=...)` 會在資料寫完的那一刻
      就把管線關掉，而 `docker exec -i` 這時可能還沒把 stdin 串流接上 dockerd：接上時
      看到的只有 EOF，請求整個掉了（回應是空的，rc=0，stderr 一個字都沒有）。
      2026-08-26 實測：這樣寫穩定失敗，而 shell 的 `printf | 腳本` 只是**碰巧**贏得那場
      競賽。這是 docker exec 的性質，不是橋接的問題：**nginx 不會這樣做**，它送完請求
      是把連線留著等回應的。
      所以：寫、flush、等一下讓串流接上、再關寫入端（那才是要測的半關閉那條路）。
    """
    proc = subprocess.Popen(
        [BRIDGE, cid, WEB_PORT, linger],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proc.stdin.write(request.replace("\n", "\r\n"))
    proc.stdin.flush()
    time.sleep(0.5)
    proc.stdin.close()  # ← 半關閉：讀方向必須還活著，回應才收得完（socat closewait）
    out = proc.stdout.read()
    proc.wait(timeout=30)
    if not out:
        # 空回應是這一支最沒有線索的失敗（腳本沒印任何東西）。把 stderr 端出來，
        # 不然下一個人只會看到「拿不到回應」而不知道斷在哪一段。
        print(f"    （橋接沒有輸出；rc={proc.returncode} stderr={proc.stderr.read().strip()[:300]!r}）")
    return out


def bridges() -> int:
    """容器裡還有幾個橋接行程（內層 socat）。

    ⚠ 樣式要**錨在行首**。`grep -c "socat -t"` 會把自己的命令列也數進去
      （`ps -eo args=` 的輸出裡就有那個 `bash -c ... grep ... 'socat -t'`），
      於是這道檢查在完全乾淨的容器上也會回非零：一個永遠紅、而且看起來像真的有
      殘留的假警報（2026-08-26 在 `python3 -c` 版撞到，同一個坑，換了名字還是同一個）。
      內層 socat 的 args 以 `socat -t` 起頭，包裝的 bash 與 grep 都不是，錨住行首就分得開。
    """
    out = run("docker", "exec", NAME, "bash", "-c", "ps -eo args= | grep -c '^socat -t' || true")
    return int(out.stdout.strip() or 0)


try:
    print("== 前提：session image 裡真的有可執行的 socat ==")
    # 內層 `socat -t <linger> STDIO TCP:…` 跑在**這個 image** 裡；舊 image 沒有它的話，
    # 每一條連線的 docker exec 都會以 127 失敗，對外只是「mitmweb 畫面 502」。
    # 在這裡一次講清楚，省得往下查半小時才發現是 image 沒 rebuild。
    r = run("docker", "run", "--rm", "--entrypoint", "socat", IMAGE, "-V", timeout=60)
    check(
        f"🔴 image {IMAGE} 裡 socat 存在且可執行（重新 build 過了嗎）",
        r.returncode == 0 and "socat version" in r.stdout,
    )

    run("docker", "rm", "-f", NAME)
    print("== 起一顆跑著真 mitmweb 的容器（web_password 由外面指定）==")
    r = run(
        "docker",
        "run",
        "-d",
        "--name",
        NAME,
        "--entrypoint",
        "bash",
        IMAGE,
        "-c",
        # 與 entrypoint.sh 的 start_capture 同一組關鍵旗標：UI 綁容器 loopback、密碼指定。
        f"mitmweb -q --listen-host 127.0.0.1 --listen-port 8880 --no-web-open-browser "
        f"--web-host 127.0.0.1 --web-port {WEB_PORT} --set web_password={PASSWORD} "
        f"> /tmp/mitmweb.log 2>&1",
        timeout=120,
    )
    check("容器起得來", r.returncode == 0)
    cid = run("docker", "inspect", "-f", "{{.Id}}", NAME).stdout.strip()

    ready = False
    for _ in range(60):
        probe = run("docker", "exec", NAME, "bash", "-c", f"exec 3<>/dev/tcp/127.0.0.1/{WEB_PORT}")
        if probe.returncode == 0:
            ready = True
            break
        time.sleep(1)
    check("mitmweb 的 UI 在容器 loopback 上聽著", ready)
    # 這一條是整個設計的前提：兄弟容器與 host 都碰不到它，`docker exec` 是唯一的路。
    check(
        "🔴 UI 沒有發布到 host（run_kwargs 收回 loopback 的那件事還成立）",
        f":{WEB_PORT}" not in run("docker", "port", NAME).stdout,
    )

    print("\n== 帶 Bearer：明文的 web_password 就是通行證 ==")
    ok_body = through_bridge(cid, f"GET / HTTP/1.0\nHost: localhost\nAuthorization: Bearer {PASSWORD}\n\n")
    check("拿得到回應（半關閉之後讀方向仍然活著）", ok_body.startswith("HTTP/1."))
    check("🔴 200（Bearer 明文比對成立，整個設計靠這一條）", " 200 " in ok_body.splitlines()[0])
    check("回應來自 mitmproxy", "server: mitmproxy" in ok_body.lower())
    # 新分頁而不是 iframe，是因為它自己送這個標頭，這裡把那個前提釘住。
    check("🔴 帶著 X-Frame-Options: DENY（所以只能新分頁，不可以 iframe）", "x-frame-options: deny" in ok_body.lower())

    print("\n== 沒帶／帶錯：要被擋，而且是 403 ==")
    no_auth = through_bridge(cid, "GET / HTTP/1.0\nHost: localhost\n\n")
    check("🔴 沒帶授權 → 403（不是 401；就緒探測的判準依賴這件事）", " 403 " in no_auth.splitlines()[0])
    check("　└ 但仍然回得出 HTTP 與 server 標頭（探測認得出它是 mitmweb）", "server: mitmproxy" in no_auth.lower())
    bad = through_bridge(cid, "GET / HTTP/1.0\nHost: localhost\nAuthorization: Bearer wrong-token-here\n\n")
    check("🔴 錯的 token → 403（不是靜靜放行）", " 403 " in bad.splitlines()[0])

    print("\n== WebSocket：握手 101、binary frame 雙向、收得乾淨 ==")
    # 模擬瀏覽器的 /updates 長連線。兩個前提都從 ADR 0021 來：
    #   · tornado 的 check_origin 比對 Origin 與 Host（都要帶 port，所以都用 localhost:8081）；
    #   · 這條鏈必須原樣過 binary：WS upgrade 之後就是 frame，不再是 HTTP 文字。
    ws = subprocess.Popen(
        [BRIDGE, cid, WEB_PORT, "5"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    ws_key = base64.b64encode(b"0123456789abcdef").decode()
    handshake = (
        "GET /updates HTTP/1.1\r\n"
        f"Host: localhost:{WEB_PORT}\r\n"
        f"Origin: http://localhost:{WEB_PORT}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Authorization: Bearer {PASSWORD}\r\n\r\n"
    ).encode()
    ws.stdin.write(handshake)
    ws.stdin.flush()
    time.sleep(1.5)  # docker exec 接通 + mitmweb 握手

    import select

    def read_available(proc: subprocess.Popen, seconds: float) -> bytes:
        """在非阻塞視窗內盡量讀；讀到什麼是什麼（WS 沒有固定長度可以 read()）。"""
        buf = b""
        deadline = time.time() + seconds
        while time.time() < deadline:
            r, _, _ = select.select([proc.stdout], [], [], max(0.0, deadline - time.time()))
            if not r:
                break
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            buf += chunk
        return buf

    head = read_available(ws, 4)
    check("🔴 WS 握手 101（origin 檢查過了、橋接原樣送 handshake）", b"101" in head.split(b"\r\n", 1)[0])

    if b"101" in head:
        # client → server → client 的 binary 回環：masked ping frame，tornado 會自動回 pong。
        # 這一發把「橋接吃不吃 binary 上行」也一起驗了（文字握手過不代表 frame 過）。
        ws.stdin.write(b"\x89\x80\x00\x00\x00\x00")  # FIN|PING, masked, len 0
        ws.stdin.flush()
        pong = read_available(ws, 4)
        check("🔴 ping 進得去、pong 回得來（WS 的雙向 binary 真通，不只是握手）", b"\x8a" in pong)
        check("WS 連線還活著（沒有被 closewait 或別的什麼誤殺）", ws.poll() is None)

    # client 關掉（＝ 使用者把分頁關掉）：內層 socat stdin EOF → 對 mitmweb SHUT_WR
    # → tornado 關 → 整條鏈退出。容器裡不可以留下東西。
    ws.kill()
    ws.wait(timeout=10)
    deadline = time.time() + 15
    while time.time() < deadline and bridges() != 0:
        time.sleep(0.5)
    check("🔴 WS client 走了之後容器裡不留東西（socat closewait 收掉它）", bridges() == 0)

    print("\n== 收得乾淨：橋接跑在使用者的容器裡，不可以留下來 ==")
    time.sleep(2)
    check("前面幾發請求（HTTP/1.0，對面回完就關）跑完之後乾乾淨淨", bridges() == 0)

    # 🔴 上面那條在「樣式根本抓不到東西」時也會綠。所以先製造一個**真的還開著**的橋接
    #    （HTTP/1.1 keep-alive，mitmweb 不會主動關），確認數得到，再放手看它收不收。
    #    這一段同時就是**關掉分頁**那條路：client 一走，容器裡不可以留下東西。
    live = subprocess.Popen(
        [BRIDGE, cid, WEB_PORT, "3"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    live.stdin.write(f"GET / HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {PASSWORD}\r\n\r\n")
    live.stdin.flush()
    time.sleep(3)
    check("🔴 連線還開著時數得到它（證明上面那條不是抓不到東西才綠的）", bridges() == 1)
    live.kill()  # ＝ 使用者把分頁關掉
    live.wait(timeout=10)
    deadline = time.time() + 15
    while time.time() < deadline and bridges() != 0:
        time.sleep(0.5)
    check("🔴 client 走了之後容器裡不留東西（closewait 到期前就收完）", bridges() == 0)

    print("\n== 上游不在：connect refuse 要正確失敗、逐段收乾淨 ==")
    # 把容器裡的 mitmweb 殺掉再來一發。內層 socat 連不上 127.0.0.1:8081 → 非 0 退出 →
    # docker exec 跟著非 0 → 橋接整條退掉，而且不可以在容器裡留下半條 socat。
    run("docker", "exec", NAME, "bash", "-c", "pkill -f 'mitmweb' || true")
    time.sleep(1)
    gone = subprocess.run(
        [BRIDGE, cid, WEB_PORT, "3"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    check("🔴 上游沒在聽時橋接非 0 退出（不是靜靜回空）", gone.returncode != 0)
    check("　└ 也沒有吐出任何上游位元組", gone.stdout == b"")
    time.sleep(2)
    check("🔴 失敗的那條也收乾淨了", bridges() == 0)
finally:
    run("docker", "rm", "-f", NAME)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
