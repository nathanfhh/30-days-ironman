"""假 GitLab 上游：HTTPS ＋ 記錄每一則請求 ＋ **真的**服務 git smart HTTP。

由 `test_gitlab_upstream_e2e.py` 掛進容器裡跑，不是獨立測試。

為什麼要真的服務 git 而不是回罐頭：這支存在的理由是驗「代理補上去的憑證，上游收不收」。
一個只會記錄的 stub 驗得了標頭長什麼樣，驗不了 `git clone` 會不會成功——而 git transport
不吃 `PRIVATE-TOKEN`、只吃 Basic，這件事只有讓真的 git 跑一次才問得出來。

⚠ **必須處理 chunked 請求本體。** 代理那份設定有 `proxy_request_buffering off` ＋
  `proxy_http_version 1.1`，於是它轉給上游的 POST **沒有 Content-Length**，是 chunked。
  只讀 Content-Length 的 stub 會在 `git-upload-pack` 那一步收到空的請求本體，然後 clone
  會以一個看不懂的錯誤失敗。
"""
import http.server
import json
import os
import socketserver
import ssl
import subprocess
import urllib.parse

LOG = "/srv/requests.jsonl"
REPO_ROOT = "/srv/repos"
HTTP_BACKEND = "/usr/lib/git-core/git-http-backend"


def _record(entry):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass                       # stdout 留給啟動訊息

    # --- 請求本體：Content-Length 與 chunked 都要會讀 ---------------------------
    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            out = b""
            while True:
                size_line = self.rfile.readline().strip()
                if not size_line:
                    break
                size = int(size_line.split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()        # 收掉結尾的 CRLF
                    break
                out += self.rfile.read(size)
                self.rfile.readline()
            return out
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self):
        parts = urllib.parse.urlsplit(self.path)
        body = self._read_body()
        _record({
            "method": self.command,
            "path": parts.path,
            "query": parts.query,
            "body_len": len(body),
            # 全部小寫化：標頭名稱大小寫不敏感，測試不該因為大小寫而假紅
            "headers": {k.lower(): v for k, v in self.headers.items()},
        })

        if parts.path.startswith("/api/v4/"):
            return self._send(200, b'{"id":1,"username":"fake-gitlab"}')

        if (parts.path.endswith("/info/refs")
                or parts.path.endswith(("/git-upload-pack", "/git-receive-pack"))):
            return self._cgi(parts, body)

        self._send(404, b'{"error":"not found"}')

    do_GET = do_POST = _dispatch

    # --- git smart HTTP 走 http-backend 的 CGI 介面 -----------------------------
    def _cgi(self, parts, body: bytes):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_PROJECT_ROOT": REPO_ROOT,
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": parts.path,
            "QUERY_STRING": parts.query,
            "REQUEST_METHOD": self.command,
            "REMOTE_ADDR": self.client_address[0],
            # ⚠ 容器裡是 root，掛進來的 repo 屬於別的 uid，新版 git 會以 "dubious
            #   ownership" 拒絕動作。這裡直接放行，這是測試用的假上游。
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
        }
        if self.headers.get("Content-Type"):
            env["CONTENT_TYPE"] = self.headers["Content-Type"]
        if body:
            env["CONTENT_LENGTH"] = str(len(body))

        proc = subprocess.run([HTTP_BACKEND], input=body, capture_output=True, env=env)
        if proc.returncode != 0:
            return self._send(500, b'{"error":"http-backend failed"}')

        # CGI 輸出：標頭區、空行、本體
        head, _, payload = proc.stdout.partition(b"\r\n\r\n")
        if not _:
            head, _, payload = proc.stdout.partition(b"\n\n")
        status, headers = 200, []
        for line in head.replace(b"\r\n", b"\n").split(b"\n"):
            if not line:
                continue
            name, _, value = line.partition(b":")
            name, value = name.strip().decode(), value.strip().decode()
            if name.lower() == "status":
                status = int(value.split()[0])
            else:
                headers.append((name, value))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    open(LOG, "w").close()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("/srv/tls/server.pem", "/srv/tls/server.key")
    srv = Server(("0.0.0.0", 443), Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print("fake-gitlab ready on :443", flush=True)
    srv.serve_forever()
