"""attach / resize / 重繪（從 sessions.py 拆出，2026-08-25）。

三個頂層函式是 socket 收尾工具；AttachMixin 掛在 SessionManager 上，
需要宿主提供 `self._docker` 與 `self._row`。
"""

from __future__ import annotations

import time
from contextlib import contextmanager, suppress

import docker

from . import config
from .db import session_scope
from .errors import SessionError
from .models import Session as SessionRow


def _close_socketio(sock) -> None:
    """關掉 docker-py 給的 attach wrapper **以及它底下真正的 socket**。

    ⚠ `attach_socket()` 回傳的是 `socket.SocketIO`，而 `SocketIO.close()` 只做
      `_decref_socketios()`——**它不關底層 fd**（docker-py 7.2.0 實測）。底層要等 CPython
      GC 收掉 docker-py 內部的參照環（sock._response → connection → sock）才會消失，
      而那是不定時的。

    這個「晚幾秒」曾讓整站停擺 5 小時（ADR 0015）：dockerd 持續往那條沒人讀的連線灌容器
    輸出，208KB 的 socket 緩衝一滿，dockerd 的 attach copier 就卡在寫我們這個 fd，連鎖
    讓該容器的 stdout broadcaster 抱著 mutex 死鎖——容器輸出全凍、`docker rm` 也一起卡住。
    高輸出的 TUI 實測約 100 秒就能填滿，所以「GC 早晚會收」不是安全的假設。

    ⚠ `_sock` 必須在 `sock.close()` **之前**取：wrapper 關閉時會把它設成 None。
    """
    raw = getattr(sock, "_sock", None)
    with suppress(Exception):
        sock.close()
    if raw is not None:
        with suppress(Exception):
            raw.close()  # 真正釋放 fd；dockerd 那側隨即收到 EPIPE 並自行收乾淨


def close_attach(sock) -> None:
    """關掉 attach socket，連它專屬的 docker client 一起收。

    只 close socket 是不夠的：那個 client 的連線池裡還留著被 hijack 的連線，GC 時會去
    flush 一個早就關掉的 fd。用獨立 client 之後把 client 一併關掉，才是真的收乾淨。

    ⚠ 已知殘留：stderr 仍偶爾會印一行 `Exception ignored ... ValueError: I/O operation
    on closed file`——那是 CPython 在 GC docker-py 內部的 HTTPResponse 時發出的，屬於
    「已忽略的例外」，不會傳播、不影響請求。真正的災情（worker 崩潰 → nginx 502）來自
    **共用** client 時污染到別的請求，那個已經沒有了：改用獨立 client 後，殘留只會留在
    自己那條連線上。要完全消掉得去碰 docker-py 的內部欄位，不值得為一行 stderr 冒險。
    """
    _close_socketio(sock)
    client = getattr(sock, "_claude_pty_client", None)
    if client is not None:
        with suppress(Exception):
            client.close()


def _discard_attach(sock, client) -> None:
    """attach 途中失敗時的清理：能關的都關掉，例外一律吞掉（我們正在處理另一個例外）。

    ⚠ 與 `close_attach()` 走同一支 `_close_socketio()`——這條失敗路徑同樣不能只關 wrapper，
      否則洩漏的 fd 一樣會把 dockerd 的 broadcaster 拖死（ADR 0015）。
    """
    if sock is not None:
        _close_socketio(sock)
    with suppress(Exception):
        client.close()


class AttachMixin:
    """attach / resize / 重繪（從 SessionManager 拆出，2026-08-25）。需要宿主提供 `self._docker` 與 `self._row`。"""

    def attach_socket(self, sid: str):
        """回傳直連 dockerd PTY 的 raw socket。呼叫端負責 close（請用 `close_attach()`）。

        唯一的用途是**就緒偵測**（連上去等畫面靜止，見 `_wait_pty_quiet`）。**這條路不經
        nginx/Flask 授權**——它只在伺服端內部使用，不對外開放；瀏覽器那條終端走的是 ttyd
        自己的 `docker attach` 子程序，與這裡無關。（觸發重繪早就改成送兩次 resize 了，
        見 `_nudge_redraw`——不從這裡注入任何按鍵。）

        ⚠ **這個 client 刻意不給 timeout**，是這個 codebase 裡唯一的例外（ADR 0012 的
        「所有 docker client 給有界 timeout」在這裡不適用）：attach 會把底層 HTTP 連線
        hijack 成 raw socket，client 的 timeout 會直接變成那條串流的 `recv` 逾時——而
        「一直收不到 bytes」正是就緒偵測要的答案，不是失敗。逾時由呼叫端在 socket 上設
        （`attached(timeout=…)` → `raw.settimeout()`），尺度也不同（0.3 秒一輪）。

        ⚠ **用獨立的 docker client，不共用 self._docker**。attach 會把底層的 HTTP 連線
        hijack 成 raw socket，但 docker-py 的連線池並不知道這件事——它仍把那條連線視為
        可重用。共用 client 時，另一個執行緒（例如同時在跑的 list()）可能拿到那條已被
        接管的連線，於是出現 `ValueError: I/O operation on closed file`，嚴重時整個
        gunicorn worker 崩潰，nginx 端看到的是 connection reset by peer → 502
        （2026-07-25 實測：連續 attach 時穩定重現）。

        代價是每次 attach 多花約 10ms 建立 client——attach 不是熱路徑，這個交換划算。
        """
        row = self._row(sid)
        client = docker.from_env()
        sock = None
        try:
            container = client.containers.get(row["container"])
            sock = container.attach_socket(params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
            # 讓 close_attach() 找得到這個 client，才收得乾淨。⚠ 這行必須在 try 內：
            # 它若丟例外（socket wrapper 不接受動態屬性），client 就再也沒人關得掉。
            sock._claude_pty_client = client
            return sock
        except docker.errors.NotFound as e:
            _discard_attach(sock, client)
            raise SessionError(f"session {sid} 的 container 已不存在") from e
        except Exception:
            _discard_attach(sock, client)
            raise

    @contextmanager
    def attached(self, sid: str, timeout: float | None = None):
        """attach 到 PTY，離開 with 一定收乾淨；yield 的是底層 raw socket。

        為什麼是 context manager 而不是「呼叫 attach_socket 後自己 close」：拿到 socket
        之後、進到 try/finally 之前的每一行（`sock._sock`、`settimeout()`）都可能丟例外
        ——container 剛好在那個瞬間退出就會——那時 finally 還沒生效，socket 與它專屬的
        docker client 就一起洩漏了（交叉審查 2026-07-25 指出）。把 setup 收進 with 裡面，
        例外從哪一行冒出來都收得到。
        """
        sock = self.attach_socket(sid)
        try:
            raw = sock._sock
            if timeout is not None:
                raw.settimeout(timeout)
            yield raw
        finally:
            close_attach(sock)

    def resize(self, sid: str, rows: int, cols: int, redraw: bool = False) -> None:
        """改容器 TTY 的尺寸。

        ⚠ container 不在時要轉成 `SessionError`（→ 400），不可讓 `docker.errors.NotFound`
          原樣往上跑：app.py 沒有它的 errorhandler，那會變成一頁 HTML traceback 的 500。
          這條路上其他每一支都轉過了——`attach_socket` 轉成 SessionError、`terminate` 當成
          冪等成功吞掉、`_nudge_redraw` 直接 suppress——只有這裡漏了。畫面那邊看不出來
          （app.js 的 `.catch(() => {})` 把它吃掉），而「malformed 輸入不該變成 500」
          是這個 codebase 自己立的規矩（review M5）。
        """
        row = self._row(sid)
        # ⚠ **尺寸沒變的話，這一次 resize 不會產生任何 SIGWINCH**——核心只在尺寸真的變了
        #   才送訊號。而「開啟終端時尺寸剛好與上次相同」是常態（同一個視窗、同一個字級），
        #   那正是使用者看到的「畫面停在舊版面，要手動縮放一下才會好」。
        #
        #   這個判斷**放在伺服端**而不是交給呼叫端的 `redraw` 旗標：伺服端知道上一次的
        #   尺寸，呼叫端不知道。前端那條路要正確得先滿足一串時序——xterm fit 完了沒、
        #   debounce 開火時讀到的是不是最終值、旗標有沒有被提早清掉——任何一環沒對上就
        #   靜靜地不重繪。Mac 與 Ubuntu 都回報過（2026-07-27）。這裡不依賴那一串。
        #
        # ⚠ 判準用的是 **DB 記的上一次尺寸**：那是拿得到的最好代理，但不是真相（真相在
        #   容器的 TTY 裡，問不到）。它可能落後——例如上一次 `_nudge_redraw` 的「還原」
        #   那一步失敗。落後時這裡會少送一次，所以**保留 `redraw` 旗標當第二條路**，
        #   兩者取聯集。代價只是「尺寸真的變了又帶旗標」時多兩次 SIGWINCH——TUI 本來就
        #   會因為那次真實變化重畫，多的那次無害。
        unchanged = (row["rows"], row["cols"]) == (rows, cols)
        try:
            self._docker.api.resize(row["container"], height=rows, width=cols)
        except docker.errors.NotFound as e:
            raise SessionError(f"session {sid} 的 container 已不存在") from e
        # 記下來：下一次要判斷「尺寸有沒有變」靠它（見上面那段），觸發重繪後也要還原成
        # 這個值。docker 那邊 resize 成功才寫，免得記到一個沒真的套用的尺寸。
        # ⚠ 這裡原本還寫著「讀畫面要用它把 bytes 餵進正確尺寸的終端模擬器」——那是一個
        #   已經拆掉的功能留下的殘影，而且方向與 ADR 0003 相反（伺服端不維護螢幕狀態、
        #   不引入 pyte，重繪交給 TUI 自己）。不要照著那句話把終端模擬器加回來。
        # immediate：這筆會寫（見 db.py 的判準；F-024 那段點名的清單本來就含 resize）。
        # docker 那邊的 resize 已經在上面做完了，這個交易體只剩 get + 兩個賦值。
        with session_scope(immediate=True) as s:
            db_row = s.get(SessionRow, sid)
            if db_row is not None:
                db_row.rows, db_row.cols = rows, cols
        if redraw or unchanged:
            self._nudge_redraw(row["container"], rows, cols)

    def _nudge_redraw(self, container: str, rows: int, cols: int) -> None:
        """強迫 TUI 把整個畫面重畫一次。

        為什麼需要：`docker resize` 只在**尺寸真的變了**的時候才會讓核心送出 SIGWINCH。
        開啟終端時尺寸剛好與上次相同（常態——同一個視窗、同一個字級）就不會有訊號，
        TUI 於是沿用它上次畫的版面；而那個版面可能是別的尺寸留下的，畫面就對不上，
        要手動按一下縮放才會好（使用者回報）。

        手法：把寬度改成 cols-1 再改回來，製造兩次貨真價實的尺寸變化。
        **不注入任何按鍵**——注入會污染使用者的輸入。

        ⚠ 這會讓容器的 TTY 尺寸短暫變動，正在看終端的人會看到一次重繪。

        ⚠ **已知的競態（未實證，機率低但存在）**：resize 這條路沒有互斥。若在下面那
          0.15 秒之內有人改字級觸發另一次 resize 把 cols 寫成新值，這裡醒來會把 PTY
          還原成**進入時讀到的舊 cols**，而 DB 記的是新的。要修的話，還原前重讀一次
          DB 的 rows/cols。

        ⚠ 還原那一次放在 `finally`，而且與縮小**分開** suppress。共用一個 suppress 的話，
          「縮小成功、還原失敗」（dockerd 抖一下、容器剛好在這 0.15 秒內結束、worker 被
          gunicorn timeout 砍掉）會讓 PTY 永久停在 cols-1，而呼叫端上面幾行剛把 DB 寫成
          cols——之後所有依 DB 尺寸做的判斷都錯一欄，而且沒有任何錯誤訊息。
          那正是這一整段在防的那種靜默失敗。
        """
        try:
            with suppress(Exception):  # 純視覺，失敗就算了，絕不可讓 resize 整支失敗
                self._docker.api.resize(container, height=rows, width=max(2, cols - 1))
                time.sleep(config.REDRAW_SETTLE_SECONDS)
        finally:
            with suppress(Exception):
                self._docker.api.resize(container, height=rows, width=cols)

    def _wait_pty_quiet(self, sid: str, deadline: float) -> bool:
        """attach 到 PTY，等畫面停止更新＝TUI 初次繪製完成。

        attach 是鏡像式的旁觀者（ADR 0002），不影響其他客戶端；也不重播歷史（ADR 0003），
        所以「連上後一直收不到 bytes」代表畫面早就靜止了，不是還沒開始畫。
        """
        last = time.time()
        saw_any = False
        try:
            with self.attached(sid, timeout=0.3) as raw:
                while time.time() < deadline:
                    try:
                        chunk = raw.recv(65536)
                        if chunk:
                            saw_any, last = True, time.time()
                            continue
                        # EOF：container 沒了。再讀下去只會 busy-spin（recv 立刻回空），
                        # 而且也等不到任何畫面了。
                        return saw_any
                    except (TimeoutError, OSError):
                        pass  # 這一輪沒有新畫面，正常
                    idle = time.time() - last
                    if saw_any and idle >= config.READY_QUIET_SECONDS:
                        return True
                    if not saw_any and idle >= config.READY_NO_OUTPUT_GRACE:
                        return True  # 連上就一片安靜＝早就畫完了
        except SessionError:
            return False  # container 已經不在了
        return True  # 逾時仍視為就緒：寧可放行也不要卡死呼叫端
