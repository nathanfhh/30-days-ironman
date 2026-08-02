"""Shared fixtures for the skill script tests.

Two problems this file exists to solve.

The scripts under `skills/*/scripts/` are PEP 723 single files, not an
installed package: they are meant to be run as `uv run <script>`, and they
deliberately have no `__init__.py` anywhere near them. `load_script` imports one
by path so a test can call its functions directly.

And `http_request` can only be exercised properly against a server that
misbehaves on demand — 500 then 200, a redirect that must not be followed, a
body that is not JSON, a response that never arrives. `stub_server` is that
server. Everything it does is local and in-process; no test here touches a real
GitLab.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "skills" / "nathan-code-review" / "scripts"


def load_script(name: str) -> ModuleType:
    """Import a PEP 723 script by path, under a name that cannot collide.

    The module is registered in sys.modules because dataclasses and pydantic
    both resolve annotations by looking their own module up there; skipping the
    registration makes `from __future__ import annotations` fail at class
    creation with a confusing NameError.
    """
    path = SCRIPTS / f"{name}.py"
    module_name = f"_ncr_script_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"無法載入腳本：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def gitlab_api() -> ModuleType:
    return load_script("gitlab_api")


@pytest.fixture(scope="session")
def report_model() -> ModuleType:
    return load_script("report_model")


@pytest.fixture(scope="session")
def render_report() -> ModuleType:
    return load_script("render_report")


@pytest.fixture(scope="session")
def scan_runner() -> ModuleType:
    return load_script("scan_runner")


# --------------------------------------------------------------------------
# Stub HTTP server
# --------------------------------------------------------------------------


@dataclass
class Reply:
    """One canned response. `delay` is what makes a timeout testable."""

    status: int = 200
    body: bytes = b"{}"
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0

    @classmethod
    def json(cls, payload: object, status: int = 200) -> Reply:
        return cls(
            status=status,
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        """Silence the default stderr access log; it drowns pytest output."""

    def handle_one_request(self) -> None:
        """Swallow the disconnect a timeout test necessarily causes.

        When the client gives up waiting, this thread is still inside a sleep
        and will write to a socket nobody is reading. That is the scenario
        under test, not a failure, so it must not print a traceback.
        """
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.requests.append(  # type: ignore[attr-defined]
            Request(
                method=self.command,
                path=self.path,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=body,
            )
        )

        queue = self.server.replies  # type: ignore[attr-defined]
        reply = queue.popleft() if queue else Reply()
        if reply.delay:
            time.sleep(reply.delay)

        self.send_response(reply.status)
        for key, value in reply.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        if reply.body:
            self.wfile.write(reply.body)

    do_GET = _serve
    do_POST = _serve


class StubServer:
    def __init__(self) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.replies = deque()  # type: ignore[attr-defined]
        self._httpd.requests = []  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list[Request]:
        return self._httpd.requests  # type: ignore[attr-defined]

    def queue(self, *replies: Reply) -> None:
        """Serve these replies in order; anything beyond them gets a bare 200."""
        self._httpd.replies.extend(replies)  # type: ignore[attr-defined]

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def stub_server():
    server = StubServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def fast_retries(gitlab_api, monkeypatch):
    """Collapse the retry backoff so the retry tests finish in milliseconds.

    Without this, three attempts at the real 2s linear backoff cost 6 seconds
    per test, which is enough to make people stop running the suite.
    """
    monkeypatch.setattr(gitlab_api, "RETRY_BACKOFF_SECONDS", 0)
