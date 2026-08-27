"""橋接腳本 ⇄ 真的 mitmweb（ADR 0021），**需要 docker 與 build 好的 image**。

    uv run --with docker python tests/test_mitm_bridge.py

`test_mitm_relay` 用替身把 relay 的生命週期走完，**唯獨換掉了最容易出錯的那一段**：
`docker exec` 進容器、接上綁在 loopback 的 mitmweb、把兩個方向的位元組正確地搬過去。
這一支就對著真的東西驗那一段，而且不經 socat（host 上不一定有），直接餵 stdin 給腳本。

守四件會靜靜壞掉的事：

  · **半關閉**。client 送完請求把寫入端關掉時，橋接不可以把讀方向一起砍掉：砍了的話
    回應被截斷，而畫面上只是「空白的 mitmweb」。用 bash 的 `/dev/tcp` 寫時實測第一發
    請求就是空的（2026-08-26）。
  · **Bearer 明文就是 web_password**。整個設計靠這一條：nginx 注入、使用者不必知道。
  · **沒帶就要被擋**，而且是 403 不是 401（12.2.3 實測），就緒探測的判準依賴這件事。
  · **收得乾淨**。橋接跑在**使用者的 session 容器裡**，漏一個就是漏在別人家裡。
"""

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


def through_bridge(cid: str, request: str) -> str:
    """把一發 HTTP 請求經橋接腳本送進容器內的 mitmweb，回傳原始回應。

    ⚠ **寫完不可以立刻關掉 stdin。** `subprocess.run(input=...)` 會在資料寫完的那一刻
      就把管線關掉，而 `docker exec -i` 這時可能還沒把 stdin 串流接上 dockerd：接上時
      看到的只有 EOF，請求整個掉了（回應是空的，rc=0，stderr 一個字都沒有）。
      2026-08-26 實測：這樣寫穩定失敗，而 shell 的 `printf | 腳本` 只是**碰巧**贏得那場
      競賽。這是 docker exec 的性質，不是橋接的問題：**nginx 不會這樣做**，它送完請求
      是把連線留著等回應的。
      所以：寫、flush、等一下讓串流接上、再關寫入端（那才是要測的半關閉那條路）。
    """
    proc = subprocess.Popen(
        [BRIDGE, cid, WEB_PORT, "5"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proc.stdin.write(request.replace("\n", "\r\n"))
    proc.stdin.flush()
    time.sleep(0.5)
    proc.stdin.close()  # ← 半關閉：讀方向必須還活著，回應才收得完
    out = proc.stdout.read()
    proc.wait(timeout=30)
    if not out:
        # 空回應是這一支最沒有線索的失敗（腳本沒印任何東西）。把 stderr 端出來，
        # 不然下一個人只會看到「拿不到回應」而不知道斷在哪一段。
        print(f"    （橋接沒有輸出；rc={proc.returncode} stderr={proc.stderr.read().strip()[:300]!r}）")
    return out


try:
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

    print("\n== 收得乾淨：橋接跑在使用者的容器裡，不可以留下來 ==")

    def bridges() -> int:
        """容器裡還有幾個橋接行程。

        ⚠ 樣式要**錨在行首**。`grep -c "python3 -c"` 會把自己的命令列也數進去
          （`ps -eo args=` 的輸出裡就有那個 `bash -c ... grep ... 'python3 -c'`），
          於是這道檢查在完全乾淨的容器上也回 2：一個永遠紅、而且看起來像真的有殘留的
          假警報（2026-08-26 撞到）。橋接的 args 以 `python3 -c` 起頭，包裝的 bash 與
          grep 都不是，錨住行首就分得開。
        """
        out = run("docker", "exec", NAME, "bash", "-c", "ps -eo args= | grep -c '^python3 -c' || true")
        return int(out.stdout.strip() or 0)

    time.sleep(2)
    check("三發請求（HTTP/1.0，對面回完就關）跑完之後乾乾淨淨", bridges() == 0)

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
    check("🔴 client 走了之後容器裡不留東西（linger 到期就整個收掉）", bridges() == 0)
finally:
    run("docker", "rm", "-f", NAME)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
