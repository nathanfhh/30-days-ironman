# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""GitLab client for the nathan-code-review skill.

Standard library only (urllib.request). No instance is configured anywhere: the
host, the API base URL and the project path are all derived from the merge
request URL that the caller passes in.

One override exists: NCR_GITLAB_API_BASE (e.g. http://gitlab-proxy:5678)
replaces the scheme+host of the API base while the merge request URL keeps
supplying the project path and iid. It exists for the dev container's
restricted network mode, where direct HTTPS to the GitLab host is blocked and
API calls go through a reverse proxy that injects the credential itself.

The token is read from GITLAB_TOKEN, falling back to NCR_GITLAB_TOKEN, and is
sent as a PRIVATE-TOKEN header. It is never printed, logged, written to a file,
or embedded in an error message. With NCR_GITLAB_API_BASE set the token is
optional — the proxy holds it, which is the point of routing through one.

CLI:
    uv run scripts/gitlab_api.py parse       <mr-url>
    uv run scripts/gitlab_api.py whoami      --host <host>
    uv run scripts/gitlab_api.py mr          <mr-url>
    uv run scripts/gitlab_api.py attachments <mr-url> --dest <dir>
    uv run scripts/gitlab_api.py discussions <mr-url> [--since <iso8601>] [--out <file>]
    uv run scripts/gitlab_api.py discussion  <mr-url> --id <discussion_id>
    uv run scripts/gitlab_api.py post-report <mr-url> --body-file <markdown>
    uv run scripts/gitlab_api.py reply       <mr-url> --id <discussion_id> --body-file <markdown>

Exit codes:
    0  success
    1  recoverable failure (e.g. one attachment could not be downloaded)
    2  usage or configuration error (bad URL, missing token, missing file)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

TOKEN_ENV_VARS = ("GITLAB_TOKEN", "NCR_GITLAB_TOKEN")

# Optional API base override（如 http://gitlab-proxy:5678）。dev container 的
# run wrapper 在接上 proxy 網路時設定；限制模式的防火牆擋掉對 GitLab 主機的直連
# HTTPS，API 一律經 proxy，且 PRIVATE-TOKEN 由 proxy 端注入。
API_BASE_ENV = "NCR_GITLAB_API_BASE"

# The literal segment GitLab inserts between the project path and the resource
# type. Splitting on it is the only reliable way to recover a project path that
# is several namespaces deep (e.g. platform/api/api-backend).
MR_URL_SEPARATOR = "/-/merge_requests/"

# 30s: GitLab答覆 discussions 分頁在大型 MR 上可能需要數秒，但超過 30 秒幾乎都是
# 網路或閘道問題，繼續等待只會讓呼叫端卡住。
REQUEST_TIMEOUT_SECONDS = 30

# 3 attempts total for idempotent GETs: one retry covers a single dropped
# connection or a brief 502/503 during a GitLab deploy; a second covers a short
# rolling restart. More than that stops being a transient fault and should
# surface to the user instead of being hidden behind minutes of silence.
GET_MAX_ATTEMPTS = 3

# Linear backoff base in seconds between GET retries.
RETRY_BACKOFF_SECONDS = 2

# 100 is GitLab's maximum allowed per_page. Using the maximum minimises the
# number of round trips on MRs with long discussion histories; asking for more
# is silently clamped by the server, so this is the ceiling worth requesting.
PER_PAGE = 100

# Safety valve so a misbehaving server (one that keeps returning full pages)
# cannot spin forever. 100 pages * 100 items = 10000 discussions, far beyond
# any real merge request.
MAX_PAGES = 100

# Attachment links in an MR description look like:
#     [name](/uploads/{secret}/{filename})
# The secret is a hex digest and the filename may contain percent-escapes.
ATTACHMENT_PATTERN = re.compile(
    r"\[(?P<name>[^\]\n]*)\]\(\s*(?P<path>/uploads/(?P<secret>[^/)\s]+)/(?P<filename>[^)\s]+))\s*\)"
)

# HTTP status codes worth retrying on an idempotent GET.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class UsageError(Exception):
    """Configuration or input error. Maps to exit code 2."""


class ApiError(Exception):
    """Recoverable API/network failure. Maps to exit code 1."""


# --------------------------------------------------------------------------
# URL parsing
# --------------------------------------------------------------------------


def host_of(parts: urllib.parse.SplitResult) -> str:
    """The host[:port] of a URL, with any `user:password@` removed.

    `netloc` keeps the userinfo, and everything downstream is built from the
    host: api_base, the note URL written into publication.url, and the error
    messages this module raises. Taking netloc verbatim therefore copied a
    credential embedded in the merge request URL into the report, into stdout,
    and into whatever log or ticket someone pasted them in. `hostname` is the
    parsed form that never carries it; the port has to be re-attached by hand
    because `hostname` drops it.

    Raises ValueError on a non-numeric port. urlsplit defers that check until
    `.port` is read, which makes this the first place a malformed authority can
    be noticed; swallowing it would send the request to the default port
    instead of the one that was asked for.
    """
    host = parts.hostname or ""
    port = parts.port
    return f"{host}:{port}" if port else host


def has_userinfo(parts: urllib.parse.SplitResult) -> bool:
    return "@" in parts.netloc


def redact_url(raw: str) -> str:
    """A URL fit to appear in an error message: userinfo removed.

    Splitting on the last '@' rather than re-parsing keeps this usable on the
    malformed URLs that error messages exist for — including the ones urlsplit
    itself rejects (an unterminated IPv6 literal), which is why the fallback
    below never re-parses.
    """
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        # '//' up to the last '@' of the authority is the only place a
        # credential can hide in a URL, parseable or not.
        return re.sub(r"//[^/@\s]*@", "//", raw)
    if not has_userinfo(parts):
        return raw
    hostpart = parts.netloc.rpartition("@")[2]
    return urllib.parse.urlunsplit(
        (parts.scheme, hostpart, parts.path, parts.query, parts.fragment)
    )


def warn(message: str) -> None:
    """User-facing notice on stderr, so it never mixes into the JSON on stdout."""
    print(message, file=sys.stderr)


def parse_mr_url(mr_url: str) -> dict[str, Any]:
    """Split a merge request URL into host, project path and iid.

    https://gitlab.example.com/platform/api/api-backend/-/merge_requests/61
            └───── host ─────┘ └─── project path ───┘             └iid┘

    Credentials embedded in the URL (`https://user:token@host/...`) are stripped
    and reported, never carried into the returned host.
    """
    raw = (mr_url or "").strip()
    if not raw:
        raise UsageError("未提供 merge request URL。")
    # Every message below quotes the URL back; redact once, up front, so no
    # later edit can reintroduce the leak by using `raw` directly.
    shown = redact_url(raw)

    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        # urlsplit rejects a few authorities outright (an unterminated IPv6
        # literal, say). That is a bad URL, not a crash.
        raise UsageError(
            f"無法解析 merge request URL：{shown}\n網址格式不合法。"
        ) from None

    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise UsageError(
            "無法解析 merge request URL："
            f"{shown}\n"
            "預期格式為 https://{host}/{project_path}/-/merge_requests/{iid}"
        )

    if MR_URL_SEPARATOR not in parts.path:
        raise UsageError(
            "無法解析 merge request URL："
            f"{shown}\n"
            f"網址中找不到 '{MR_URL_SEPARATOR}' 片段。"
            "預期格式為 https://{host}/{project_path}/-/merge_requests/{iid}"
        )

    project_part, _, iid_part = parts.path.partition(MR_URL_SEPARATOR)
    project_path = project_part.strip("/")

    # Anything after the iid (/diffs, /commits, a trailing slash) is dropped.
    iid_token = iid_part.strip("/").split("/", 1)[0]

    if not project_path:
        raise UsageError(
            f"無法解析 merge request URL：{shown}\n網址中缺少 project path。"
        )
    if not iid_token.isdigit():
        raise UsageError(
            f"無法解析 merge request URL：{shown}\n"
            f"iid 必須是數字，實際取得 '{iid_token}'。"
        )

    try:
        host = host_of(parts)
    except ValueError:
        raise UsageError(
            f"無法解析 merge request URL：{shown}\n通訊埠必須是數字。"
        ) from None

    if not host:
        raise UsageError(
            f"無法解析 merge request URL：{shown}\n網址中缺少主機名稱。"
        )
    if has_userinfo(parts):
        warn(
            "注意：URL 中夾帶了帳密（user:password@），已剝除且未被記錄——"
            "不會進入 API 位址、報告或任何錯誤訊息。"
            "本工具的憑證一律讀自 GITLAB_TOKEN，不從網址取得。"
        )

    return {
        "host": host,
        "project_path": project_path,
        # quote(..., safe="") percent-encodes '/' *and* every other reserved
        # character. Hand-substituting '/' -> '%2F' is the classic bug here: it
        # leaves '.', '+', spaces and non-ASCII namespace names unescaped, and
        # GitLab then 404s on a project that plainly exists.
        "project_path_encoded": urllib.parse.quote(project_path, safe=""),
        "iid": int(iid_token),
        "api_base": api_base_for(host),
    }


# --------------------------------------------------------------------------
# Auth and HTTP
# --------------------------------------------------------------------------


def api_base_for(host: str) -> str:
    """API base for a host, honouring the NCR_GITLAB_API_BASE override."""
    override = os.environ.get(API_BASE_ENV, "").strip().rstrip("/")
    if override:
        return f"{override}/api/v4"
    return f"https://{host}/api/v4"


def read_token() -> str:
    """Read the API token from the environment. Never returned to output.

    With NCR_GITLAB_API_BASE set the token is optional: requests go to a proxy
    that injects PRIVATE-TOKEN itself, and keeping the credential out of this
    environment is the reason the proxy exists. Returning "" makes http_request
    omit the header entirely.
    """
    for name in TOKEN_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    if os.environ.get(API_BASE_ENV, "").strip():
        return ""
    raise UsageError(
        "找不到 GitLab token。請設定環境變數 GITLAB_TOKEN"
        "（或改用 NCR_GITLAB_TOKEN），並確認該 token 具備 api scope。"
        "（若經 gitlab-proxy 存取，改設 NCR_GITLAB_API_BASE 即可，token 由 proxy 注入。）"
    )


def _endpoint_of(url: str) -> str:
    """Return only the path of a URL, for use in error messages.

    The token lives in a header, never in the URL, but trimming to the path
    keeps error text short and guarantees no query string is ever echoed.
    """
    return urllib.parse.urlsplit(url).path or url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect automatically.

    Two independent reasons, either one sufficient.

    A 3xx from any endpoint this tool calls means something is wrong, and
    following it destroys the evidence: the classic case is an API call sent to
    a web path, which redirects to /users/sign_in, so the error you finally see
    carries a status from the sign-in page while your error message still names
    the URL you asked for. Debugging that costs far more than the redirect saves.

    And urllib re-sends every request header to the redirect target — it strips
    only content-length and content-type, whatever the target host is (see
    HTTPRedirectHandler.redirect_request in the stdlib). Following a redirect
    would therefore carry PRIVATE-TOKEN somewhere we never chose to send it.
    requests is only marginally better: it drops Authorization across hosts, but
    a custom header like PRIVATE-TOKEN is not covered there either.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _redirect_target_of(location: str) -> str:
    """Describe a redirect target without echoing its query string.

    Unlike _endpoint_of, the host is kept: whether the redirect leaves this
    GitLab instance is the single most useful thing about it. The query string
    is dropped for the same reason _endpoint_of drops it — a Location is
    server-controlled, and an error message is the one place a credential in a
    URL would get copied into a log or a bug report. Userinfo goes for exactly
    that reason too, and here the server chose it, which makes it likelier.
    """
    parts = urllib.parse.urlsplit(location)
    if not parts.netloc:
        return parts.path or location
    try:
        host = host_of(parts)
    except ValueError:
        # A Location this malformed is still worth naming in the error; the
        # hostname alone says the one thing that matters, which is where it
        # was pointing.
        host = parts.hostname or ""
    return f"{parts.scheme}://{host}{parts.path}"


def _describe_redirect(status: int, url: str, location: str | None) -> str:
    target = _redirect_target_of(location) if location else "（回應未附 Location）"
    message = (
        f"GitLab 回應 HTTP {status} 轉址至 {target}（endpoint: {_endpoint_of(url)}）。"
        "本工具不自動跟隨轉址，以免把 PRIVATE-TOKEN 送到非預期的位址、"
        "並讓錯誤指向真正的來源。"
    )
    if location and "sign_in" in location:
        message += (
            "\n轉址目標是登入頁，代表這個請求被當成未登入處理——token 沒有被讀到，"
            "而不是權限不足。最常見的原因是把 API 呼叫送到了 web 路徑："
            "PRIVATE-TOKEN 只有 /api/v4/ 底下的端點認得，"
            "web 路徑（例如 /{group}/{project}/uploads/...）只認 session cookie。"
        )
    return message


def _describe_http_error(status: int, url: str) -> str:
    endpoint = _endpoint_of(url)
    if status in (401, 403):
        return (
            f"GitLab 回應 HTTP {status}（endpoint: {endpoint}）。"
            "token 無效、已過期，或缺少 api scope；"
            "請檢查 GITLAB_TOKEN / NCR_GITLAB_TOKEN 的設定。"
        )
    if status == 404:
        return (
            f"GitLab 回應 HTTP {status}（endpoint: {endpoint}）。"
            "該 merge request 或 project 不存在，"
            "或目前的 token 沒有存取權限（GitLab 對無權限資源一律回傳 404）。"
        )
    return f"GitLab 回應 HTTP {status}（endpoint: {endpoint}）。"


def http_request(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    accept_json: bool = True,
) -> tuple[Any, dict[str, str]]:
    """Perform an HTTP request and return (parsed_body, headers).

    Idempotent GETs are retried on transient network errors and 5xx/429.
    A POST is never retried: it is not idempotent, and a retry after a
    partially-delivered request would duplicate a comment on the MR.
    """
    data: bytes | None = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "nathan-code-review/gitlab_api",
    }
    # 空字串 = proxy 注入模式（見 read_token）。帶一個空的 PRIVATE-TOKEN header
    # 會被 GitLab 當成無效憑證而 401，所以是「不帶」而不是「帶空值」。
    if token:
        headers["PRIVATE-TOKEN"] = token
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    is_idempotent = method.upper() == "GET"
    attempts = GET_MAX_ATTEMPTS if is_idempotent else 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with _OPENER.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
                response_headers = {k.lower(): v for k, v in response.headers.items()}
                if not accept_json:
                    return raw, response_headers
                if not raw:
                    return None, response_headers
                try:
                    return json.loads(raw.decode("utf-8")), response_headers
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ApiError(
                        f"GitLab 回應不是合法的 JSON（endpoint: {_endpoint_of(url)}）：{exc}"
                    ) from exc
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                # _NoRedirect turns every 3xx into an HTTPError. Never retried:
                # a redirect is deterministic, so a second attempt returns it again.
                raise ApiError(
                    _describe_redirect(exc.code, url, exc.headers.get("Location"))
                ) from exc
            message = _describe_http_error(exc.code, url)
            if is_idempotent and exc.code in RETRYABLE_STATUSES and attempt < attempts:
                last_error = ApiError(message)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise ApiError(message) from exc
        except urllib.error.URLError as exc:
            message = (
                f"無法連線至 GitLab（endpoint: {_endpoint_of(url)}）：{exc.reason}"
            )
            if is_idempotent and attempt < attempts:
                last_error = ApiError(message)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise ApiError(message) from exc
        except TimeoutError as exc:
            message = (
                f"連線 GitLab 逾時（endpoint: {_endpoint_of(url)}，"
                f"timeout={REQUEST_TIMEOUT_SECONDS}s）。"
            )
            if is_idempotent and attempt < attempts:
                last_error = ApiError(message)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise ApiError(message) from exc

    raise last_error or ApiError(f"請求失敗（endpoint: {_endpoint_of(url)}）。")


def get_json(url: str, token: str) -> Any:
    body, _ = http_request(url, token, method="GET")
    return body


def post_json(url: str, token: str, payload: dict[str, Any]) -> Any:
    body, _ = http_request(url, token, method="POST", payload=payload)
    return body


def get_paginated(url: str, token: str) -> list[Any]:
    """Walk a paginated GitLab collection endpoint and return every item."""
    collected: list[Any] = []
    for page in range(1, MAX_PAGES + 1):
        separator = "&" if "?" in url else "?"
        page_url = f"{url}{separator}per_page={PER_PAGE}&page={page}"
        batch = get_json(page_url, token)
        if not isinstance(batch, list):
            raise ApiError(
                f"預期 GitLab 回傳陣列，實際型別為 {type(batch).__name__}"
                f"（endpoint: {_endpoint_of(page_url)}）。"
            )
        collected.extend(batch)
        # A short page (or an empty one) means this was the last page.
        if len(batch) < PER_PAGE:
            return collected
    raise ApiError(
        f"分頁超過上限 {MAX_PAGES} 頁（endpoint: {_endpoint_of(url)}），已停止讀取。"
    )


# --------------------------------------------------------------------------
# Endpoint helpers
# --------------------------------------------------------------------------


def mr_base_url(target: dict[str, Any]) -> str:
    """API URL prefix for this merge request (uses iid, not id)."""
    return (
        f"{target['api_base']}/projects/{target['project_path_encoded']}"
        f"/merge_requests/{target['iid']}"
    )


def fetch_mr(target: dict[str, Any], token: str) -> dict[str, Any]:
    payload = get_json(mr_base_url(target), token)
    if not isinstance(payload, dict):
        raise ApiError("GitLab 回傳的 merge request 資料格式不符預期。")
    return payload


def extract_attachments(description: str | None, target: dict[str, Any]) -> list[dict[str, str]]:
    """Find [name](/uploads/{secret}/{filename}) links in an MR description.

    The URL built here is the **API** one:

        {api_base}/projects/{project_path_encoded}/uploads/{secret}/{filename}

    not the browser one (https://{host}/{project_path}/uploads/...), and the
    difference is not cosmetic. GitLab has two front doors with two separate
    credentials: /api/v4/* is the Grape API and honours PRIVATE-TOKEN, while
    every other path is the Rails web app and authenticates with the
    _gitlab_session cookie. The web app does not know what PRIVATE-TOKEN is —
    it ignores the header, treats the request as anonymous, and 302s to
    /users/sign_in. What comes back after that redirect is either a 404 (if the
    request carried Accept: application/json, since the sign-in page has no
    JSON representation) or, far worse, 200 with the sign-in page's HTML, which
    a downloader will happily save under the attachment's filename.

    The filename keeps whatever percent-encoding the description had; CJK
    attachment names arrive already encoded and must not be decoded here.
    """
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in ATTACHMENT_PATTERN.finditer(description or ""):
        relative = match.group("path")
        if relative in seen:
            continue
        seen.add(relative)
        name = match.group("name").strip() or match.group("filename")
        results.append(
            {
                "name": name,
                "url": f"{target['api_base']}/projects/{target['project_path_encoded']}{relative}",
            }
        )
    return results


def reject_html_error_page(raw: bytes, content_type: str, filename: str) -> None:
    """Refuse to save an HTML page under an attachment's name.

    The failure this guards against does not look like a failure. An auth
    redirect ends at a sign-in page that answers 200 with a body, so a
    downloader that writes whatever it received produces a file with the right
    name and the wrong content, and nothing downstream can tell: the review
    then reads `<!DOCTYPE html>` where it expected a requirement spec.

    Only an attachment that is itself HTML may legitimately look like this.
    """
    if filename.lower().endswith((".html", ".htm")):
        return
    head = raw[:512].lstrip().lower()
    looks_like_page = head.startswith((b"<!doctype html", b"<html"))
    if "text/html" in content_type.lower() or looks_like_page:
        raise ApiError(
            "下載回來的是一個 HTML 頁面而不是附件本身（通常是 GitLab 的登入頁）。"
            "檔案未寫入磁碟，以免把錯誤的內容存成看起來正確的檔名。"
        )


def safe_filename(name: str, fallback: str) -> str:
    """Reduce an attachment name to a single safe path component."""
    decoded = urllib.parse.unquote(name)
    # Strip any directory component so an attachment name can never escape --dest.
    base = decoded.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    # \w is Unicode-aware here, so CJK filenames survive intact.
    cleaned = re.sub(r"[^\w.\-() ]", "_", base).strip(" .")
    return cleaned or fallback


# --------------------------------------------------------------------------
# Discussion helpers
# --------------------------------------------------------------------------


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating GitLab's trailing 'Z'.

    Returns None for anything unparseable, including a missing value. Deciding
    what that means is the caller's: require_timestamp turns it into a usage
    error, filter_discussions_since into a hard stop.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def require_timestamp(value: str) -> datetime:
    parsed = parse_timestamp(value)
    if parsed is None:
        raise UsageError(
            f"--since 的時間格式無法解析：{value}\n"
            "請使用 ISO-8601，例如 2026-08-01T09:30:00Z。"
        )
    return parsed


def filter_discussions_since(
    discussions: list[dict[str, Any]], since: datetime
) -> list[dict[str, Any]]:
    """Keep only notes created strictly after `since`; drop emptied discussions.

    A note whose `created_at` cannot be parsed stops the run. Treating it as
    undatable and filtering it out — the previous behaviour — dropped it
    silently, and on a re-review this filter's whole job is collecting the
    author's replies: a reply lost here is indistinguishable from an author who
    never answered, and the next report would go on to say exactly that.

    Disclosing a count instead is not available here. SKILL.md Phase 2 seals
    every digest of these threads while the blind pass runs, and a count is a
    digest. Failing loudly is the one outcome that neither leaks nor loses.
    """
    filtered: list[dict[str, Any]] = []
    for discussion in discussions:
        notes = discussion.get("notes") or []
        kept = []
        for note in notes:
            raw_created = note.get("created_at")
            created = parse_timestamp(raw_created)
            if created is None:
                raise ApiError(
                    f"discussion {discussion.get('id')} 的 note {note.get('id')} "
                    f"沒有可解析的 created_at（實際值：{raw_created!r}），"
                    "無法判斷它是否發生在上一輪之後。已中止，"
                    "以免把作者的回覆當成「作者沒有回覆」。"
                )
            if created > since:
                kept.append(note)
        if kept:
            trimmed = dict(discussion)
            trimmed["notes"] = kept
            filtered.append(trimmed)
    return filtered


def note_web_url(target: dict[str, Any], note_id: Any) -> str:
    return (
        f"https://{target['host']}/{target['project_path']}"
        f"{MR_URL_SEPARATOR}{target['iid']}#note_{note_id}"
    )


def read_body_file(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_file():
        raise UsageError(f"找不到 --body-file 指定的檔案：{path.as_posix()}")
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"無法讀取檔案 {path.as_posix()}：{exc.strerror or exc}") from exc
    if not body.strip():
        raise UsageError(f"--body-file 內容為空：{path.as_posix()}")
    return body


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def cmd_parse(args: argparse.Namespace) -> int:
    emit(parse_mr_url(args.mr_url))
    return EXIT_OK


def cmd_whoami(args: argparse.Namespace) -> int:
    host = args.host.strip()
    # Accept either a bare host or a full URL, and reduce it to the host.
    if "//" in host:
        parts = urllib.parse.urlsplit(host)
        if has_userinfo(parts):
            warn("注意：--host 中夾帶的帳密已剝除且未被記錄。")
        try:
            host = host_of(parts)
        except ValueError:
            raise UsageError("--host 的通訊埠必須是數字。") from None
    host = host.strip("/")
    if not host:
        raise UsageError("--host 不可為空，請提供 GitLab 主機名稱，例如 gitlab.example.com。")

    token = read_token()
    user = get_json(f"{api_base_for(host)}/user", token)
    if not isinstance(user, dict):
        raise ApiError("GET /user 回傳的資料格式不符預期。")
    emit({"id": user.get("id"), "username": user.get("username"), "name": user.get("name")})
    return EXIT_OK


def cmd_mr(args: argparse.Namespace) -> int:
    target = parse_mr_url(args.mr_url)
    token = read_token()
    payload = fetch_mr(target, token)
    description = payload.get("description") or ""
    emit(
        {
            "title": payload.get("title"),
            "description": description,
            "source_branch": payload.get("source_branch"),
            "target_branch": payload.get("target_branch"),
            "web_url": payload.get("web_url"),
            "project_id": payload.get("project_id"),
            "iid": payload.get("iid", target["iid"]),
            "attachments": extract_attachments(description, target),
        }
    )
    return EXIT_OK


def cmd_attachments(args: argparse.Namespace) -> int:
    target = parse_mr_url(args.mr_url)
    token = read_token()
    payload = fetch_mr(target, token)
    attachments = extract_attachments(payload.get("description") or "", target)

    dest = Path(args.dest)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UsageError(f"無法建立下載目錄 {dest.as_posix()}：{exc.strerror or exc}") from exc

    results: list[dict[str, Any]] = []
    any_failed = False
    for index, attachment in enumerate(attachments, start=1):
        url = attachment["url"]
        filename = safe_filename(url.rsplit("/", 1)[-1], f"attachment-{index}")
        local_path = dest / filename
        record: dict[str, Any] = {
            "name": attachment["name"],
            "url": url,
            "local_path": local_path.as_posix(),
            "status": "ok",
        }
        try:
            # accept_json=False: uploads are arbitrary binary (images, PDFs, md).
            raw, response_headers = http_request(url, token, method="GET", accept_json=False)
            reject_html_error_page(raw, response_headers.get("content-type", ""), filename)
            local_path.write_bytes(raw)
        except (ApiError, OSError) as exc:
            # One failed attachment must not abort the review; record and continue.
            any_failed = True
            record["status"] = "failed"
            record["local_path"] = None
            record["error"] = str(exc)
        results.append(record)

    emit(results)
    return EXIT_FAILURE if any_failed else EXIT_OK


def cmd_discussions(args: argparse.Namespace) -> int:
    target = parse_mr_url(args.mr_url)
    token = read_token()
    since = require_timestamp(args.since) if args.since else None

    discussions = get_paginated(f"{mr_base_url(target)}/discussions", token)
    discussions = [d for d in discussions if isinstance(d, dict)]
    if since is not None:
        discussions = filter_discussions_since(discussions, since)

    if args.out:
        out_path = Path(args.out)
        try:
            if out_path.parent and not out_path.parent.exists():
                out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(discussions, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise UsageError(
                f"無法寫入 --out 指定的檔案 {out_path.as_posix()}：{exc.strerror or exc}"
            ) from exc
        # The path, and nothing else. --out exists so that a re-review can fetch
        # the author's replies while its blind pass is still running, and a
        # count is the smallest possible digest of those replies: "3 replies
        # since the last round" already tells the blind pass how much the author
        # disputed. Everything printed here lands in the caller's context, and
        # everything omitted is in the file, one read away once the blind pass
        # is sealed. See references/re-review.md and SKILL.md Phase 2.
        emit({"out": out_path.as_posix()})
        return EXIT_OK

    emit(discussions)
    return EXIT_OK


def cmd_discussion(args: argparse.Namespace) -> int:
    target = parse_mr_url(args.mr_url)
    token = read_token()
    discussion_id = urllib.parse.quote(args.id, safe="")
    payload = get_json(f"{mr_base_url(target)}/discussions/{discussion_id}", token)
    emit(payload)
    return EXIT_OK


def cmd_post_report(args: argparse.Namespace) -> int:
    target = parse_mr_url(args.mr_url)
    token = read_token()
    body = read_body_file(args.body_file)

    payload = post_json(f"{mr_base_url(target)}/discussions", token, {"body": body})
    if not isinstance(payload, dict):
        raise ApiError("POST discussions 回傳的資料格式不符預期。")

    notes = payload.get("notes") or []
    root = notes[0] if notes and isinstance(notes[0], dict) else {}
    note_id = root.get("id")
    emit(
        {
            "discussion_id": payload.get("id"),
            "note_id": note_id,
            "created_at": root.get("created_at"),
            "url": note_web_url(target, note_id) if note_id is not None else None,
        }
    )
    return EXIT_OK


def cmd_reply(args: argparse.Namespace) -> int:
    target = parse_mr_url(args.mr_url)
    token = read_token()
    body = read_body_file(args.body_file)
    discussion_id = urllib.parse.quote(args.id, safe="")

    payload = post_json(
        f"{mr_base_url(target)}/discussions/{discussion_id}/notes", token, {"body": body}
    )
    if not isinstance(payload, dict):
        raise ApiError("POST notes 回傳的資料格式不符預期。")

    note_id = payload.get("id")
    emit(
        {
            "discussion_id": args.id,
            "note_id": note_id,
            "created_at": payload.get("created_at"),
            "url": note_web_url(target, note_id) if note_id is not None else None,
        }
    )
    return EXIT_OK


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitlab_api.py",
        description="GitLab client for the nathan-code-review skill (standard library only).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_parse = subparsers.add_parser("parse", help="Split an MR URL into host / project / iid.")
    p_parse.add_argument("mr_url", metavar="mr-url")
    p_parse.set_defaults(func=cmd_parse)

    p_whoami = subparsers.add_parser("whoami", help="GET /user to confirm the token works.")
    p_whoami.add_argument("--host", required=True)
    p_whoami.set_defaults(func=cmd_whoami)

    p_mr = subparsers.add_parser("mr", help="Fetch merge request metadata and attachment links.")
    p_mr.add_argument("mr_url", metavar="mr-url")
    p_mr.set_defaults(func=cmd_mr)

    p_attachments = subparsers.add_parser(
        "attachments", help="Download every attachment linked in the MR description."
    )
    p_attachments.add_argument("mr_url", metavar="mr-url")
    p_attachments.add_argument("--dest", required=True, help="Destination directory.")
    p_attachments.set_defaults(func=cmd_attachments)

    p_discussions = subparsers.add_parser(
        "discussions", help="List every discussion on the merge request."
    )
    p_discussions.add_argument("mr_url", metavar="mr-url")
    p_discussions.add_argument(
        "--since", help="ISO-8601 cutoff; keep only notes created strictly after it."
    )
    p_discussions.add_argument(
        "--out",
        help="Write the discussions JSON to this file and print only a metadata summary.",
    )
    p_discussions.set_defaults(func=cmd_discussions)

    p_discussion = subparsers.add_parser("discussion", help="Fetch a single discussion thread.")
    p_discussion.add_argument("mr_url", metavar="mr-url")
    p_discussion.add_argument("--id", required=True, help="discussion_id")
    p_discussion.set_defaults(func=cmd_discussion)

    p_post = subparsers.add_parser(
        "post-report", help="Post the report as a new (resolvable) discussion."
    )
    p_post.add_argument("mr_url", metavar="mr-url")
    p_post.add_argument("--body-file", required=True, dest="body_file")
    p_post.set_defaults(func=cmd_post_report)

    p_reply = subparsers.add_parser("reply", help="Reply into an existing discussion.")
    p_reply.add_argument("mr_url", metavar="mr-url")
    p_reply.add_argument("--id", required=True, help="discussion_id")
    p_reply.add_argument("--body-file", required=True, dest="body_file")
    p_reply.set_defaults(func=cmd_reply)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except UsageError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return EXIT_USAGE
    except ApiError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        print("錯誤：已被使用者中斷。", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
