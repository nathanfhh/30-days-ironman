"""Tests for skills/nathan-code-review/scripts/gitlab_api.py.

The pure functions are cheap to cover and are covered exhaustively. The HTTP
layer is exercised against the in-process stub server from conftest, because
the paths worth pinning down there — a retry, a redirect that must not be
followed, a body that is not JSON — are exactly the ones a healthy GitLab will
never produce on demand.
"""

from __future__ import annotations

import json

import pytest
from conftest import Reply

MR_URL = "https://gitlab.example.com/grp/group/repo/-/merge_requests/92"


# --------------------------------------------------------------------------
# parse_mr_url
# --------------------------------------------------------------------------


class TestParseMrUrl:
    def test_splits_host_project_and_iid(self, gitlab_api):
        target = gitlab_api.parse_mr_url(MR_URL)
        assert target["host"] == "gitlab.example.com"
        assert target["project_path"] == "grp/group/repo"
        assert target["iid"] == 92
        assert target["api_base"] == "https://gitlab.example.com/api/v4"

    def test_encodes_every_reserved_character_not_just_slash(self, gitlab_api):
        """quote(safe="") is the point: hand-rolled '/'->'%2F' leaves the rest raw."""
        target = gitlab_api.parse_mr_url(
            "https://gitlab.example.com/grp/sub.group/re+po/-/merge_requests/1"
        )
        assert target["project_path_encoded"] == "grp%2Fsub.group%2Fre%2Bpo"

    @pytest.mark.parametrize(
        "suffix",
        ["", "/", "/diffs", "/commits", "/pipelines?foo=bar"],
        ids=["bare", "trailing-slash", "diffs", "commits", "query"],
    )
    def test_ignores_anything_after_the_iid(self, gitlab_api, suffix):
        assert gitlab_api.parse_mr_url(MR_URL + suffix)["iid"] == 92

    @pytest.mark.parametrize(
        ("bad_url", "because"),
        [
            ("", "empty"),
            ("   ", "blank"),
            ("gitlab.example.com/g/r/-/merge_requests/1", "no scheme"),
            ("ftp://gitlab.example.com/g/r/-/merge_requests/1", "wrong scheme"),
            ("https://gitlab.example.com/g/r/merge_requests/1", "no /-/ separator"),
            ("https://gitlab.example.com/-/merge_requests/1", "no project path"),
            ("https://gitlab.example.com/g/r/-/merge_requests/abc", "iid not a number"),
            ("https://gitlab.example.com:abc/g/r/-/merge_requests/1", "port not a number"),
            ("https://[::1/g/r/-/merge_requests/1", "urlsplit rejects the authority"),
        ],
    )
    def test_rejects_malformed_urls(self, gitlab_api, bad_url, because):
        with pytest.raises(gitlab_api.UsageError):
            gitlab_api.parse_mr_url(bad_url)

    def test_keeps_a_non_default_port(self, gitlab_api):
        target = gitlab_api.parse_mr_url(
            "https://gitlab.example.com:8443/g/r/-/merge_requests/7"
        )
        assert target["host"] == "gitlab.example.com:8443"
        assert target["api_base"] == "https://gitlab.example.com:8443/api/v4"


class TestUrlCredentialsAreStripped:
    """URL 裡夾帶的帳密不得流進任何輸出欄位、報告或錯誤訊息。"""

    SECRET = "s3cr3tT0ken"
    URL = f"https://someone:{SECRET}@gitlab.example.com/grp/group/repo/-/merge_requests/92"

    def test_the_host_carries_no_credentials(self, gitlab_api):
        target = gitlab_api.parse_mr_url(self.URL)
        assert target["host"] == "gitlab.example.com"

    def test_no_output_field_contains_the_secret(self, gitlab_api):
        """The reproduced leak: netloc kept userinfo, so api_base carried it."""
        target = gitlab_api.parse_mr_url(self.URL)
        assert self.SECRET not in json.dumps(target)
        assert "someone" not in json.dumps(target)

    def test_the_note_url_written_into_the_report_is_clean(self, gitlab_api):
        """publication.url is published on the MR, where anyone can read it."""
        target = gitlab_api.parse_mr_url(self.URL)
        assert self.SECRET not in gitlab_api.note_web_url(target, 12345)

    def test_the_api_url_is_clean(self, gitlab_api):
        target = gitlab_api.parse_mr_url(self.URL)
        assert self.SECRET not in gitlab_api.mr_base_url(target)

    def test_an_error_message_does_not_echo_the_credential_back(self, gitlab_api):
        bad = f"https://someone:{self.SECRET}@gitlab.example.com/g/r/merge_requests/1"
        with pytest.raises(gitlab_api.UsageError) as excinfo:
            gitlab_api.parse_mr_url(bad)
        assert self.SECRET not in str(excinfo.value)

    def test_the_stripping_is_stated_rather_than_done_silently(self, gitlab_api, capsys):
        gitlab_api.parse_mr_url(self.URL)
        stderr = capsys.readouterr().err
        assert "已剝除" in stderr
        assert self.SECRET not in stderr

    def test_a_url_without_credentials_says_nothing(self, gitlab_api, capsys):
        gitlab_api.parse_mr_url(MR_URL)
        assert capsys.readouterr().err == ""

    def test_a_redirect_target_is_stripped_too(self, gitlab_api):
        """Server-controlled, so likelier to carry one than the URL the user typed."""
        target = gitlab_api._redirect_target_of(
            f"https://someone:{self.SECRET}@evil.example.com/collect"
        )
        assert self.SECRET not in target
        assert target == "https://evil.example.com/collect"


# --------------------------------------------------------------------------
# extract_attachments
# --------------------------------------------------------------------------


class TestExtractAttachments:
    """Regression guard for a bug that failed in two directions at once.

    The URL used to be built against the web route, where PRIVATE-TOKEN means
    nothing: GitLab redirected to /users/sign_in, and the download either died
    with a 404 that named the wrong URL, or — with a client that follows
    redirects and does not force Accept: application/json — succeeded, saving
    the sign-in page's HTML under the attachment's filename.
    """

    DESCRIPTION = "見附件 [規格.md](/uploads/deadbeef/%E8%A6%8F%E6%A0%BC.md) 說明"

    def test_builds_an_api_url_not_a_web_url(self, gitlab_api):
        target = gitlab_api.parse_mr_url(MR_URL)
        [attachment] = gitlab_api.extract_attachments(self.DESCRIPTION, target)
        assert attachment["url"] == (
            "https://gitlab.example.com/api/v4/projects/grp%2Fgroup%2Frepo"
            "/uploads/deadbeef/%E8%A6%8F%E6%A0%BC.md"
        )
        assert "/api/v4/" in attachment["url"]

    def test_leaves_the_filename_percent_encoding_alone(self, gitlab_api):
        """Decoding here would corrupt the request; GitLab wants it as written."""
        target = gitlab_api.parse_mr_url(MR_URL)
        [attachment] = gitlab_api.extract_attachments(self.DESCRIPTION, target)
        assert attachment["url"].endswith("/%E8%A6%8F%E6%A0%BC.md")
        assert attachment["name"] == "規格.md"

    def test_falls_back_to_the_filename_when_the_link_text_is_empty(self, gitlab_api):
        target = gitlab_api.parse_mr_url(MR_URL)
        [attachment] = gitlab_api.extract_attachments("[](/uploads/abc/x.pdf)", target)
        assert attachment["name"] == "x.pdf"

    def test_deduplicates_the_same_upload_linked_twice(self, gitlab_api):
        target = gitlab_api.parse_mr_url(MR_URL)
        description = "[a](/uploads/abc/x.pdf) 又提一次 [b](/uploads/abc/x.pdf)"
        assert len(gitlab_api.extract_attachments(description, target)) == 1

    def test_keeps_distinct_uploads_that_share_a_filename(self, gitlab_api):
        target = gitlab_api.parse_mr_url(MR_URL)
        description = "[a](/uploads/aaa/x.pdf) [b](/uploads/bbb/x.pdf)"
        assert len(gitlab_api.extract_attachments(description, target)) == 2

    @pytest.mark.parametrize("description", ["", None, "沒有附件", "[連結](https://example.com)"])
    def test_finds_nothing_when_there_is_nothing(self, gitlab_api, description):
        target = gitlab_api.parse_mr_url(MR_URL)
        assert gitlab_api.extract_attachments(description, target) == []


# --------------------------------------------------------------------------
# safe_filename
# --------------------------------------------------------------------------


class TestSafeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("spec.md", "spec.md"),
            ("%E8%A6%8F%E6%A0%BC.md", "規格.md"),
            ("my report (v2).md", "my report (v2).md"),
            ("a/b/c.md", "c.md"),
            (r"a\b\c.md", "c.md"),
            ("../../../etc/passwd", "passwd"),
            ("/absolute/path.md", "path.md"),
            ("sp:ace*n?ame.md", "sp_ace_n_ame.md"),
            # Every character is replaced rather than dropped, so a name made
            # only of forbidden characters still yields a usable component and
            # the fallback is not reached.
            ("***", "___"),
        ],
    )
    def test_reduces_to_one_safe_component(self, gitlab_api, raw, expected):
        assert gitlab_api.safe_filename(raw, "fallback") == expected

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "...", "///", " . "],
        ids=["empty", "spaces", "dots", "slashes", "dot-and-spaces"],
    )
    def test_uses_the_fallback_only_when_nothing_survives(self, gitlab_api, raw):
        assert gitlab_api.safe_filename(raw, "attachment-1") == "attachment-1"

    def test_cannot_escape_the_destination_directory(self, gitlab_api):
        # Assert both separators: "/" alone lets a backslash traversal pass
        # untouched on the r"..\..\windows" input.
        for raw in ["../../etc/shadow", "..%2F..%2Fetc%2Fshadow", r"..\..\windows"]:
            cleaned = gitlab_api.safe_filename(raw, "fallback")
            assert "/" not in cleaned and "\\" not in cleaned


# --------------------------------------------------------------------------
# reject_html_error_page
# --------------------------------------------------------------------------


class TestRejectHtmlErrorPage:
    """The guard against saving a login page under a requirement spec's name."""

    SIGN_IN = b"<!DOCTYPE html>\n<html><title>Sign in</title>"

    def test_blocks_on_content_type(self, gitlab_api):
        with pytest.raises(gitlab_api.ApiError):
            gitlab_api.reject_html_error_page(b"whatever", "text/html; charset=utf-8", "spec.md")

    def test_blocks_on_body_shape_even_when_the_content_type_lies(self, gitlab_api):
        with pytest.raises(gitlab_api.ApiError):
            gitlab_api.reject_html_error_page(self.SIGN_IN, "application/octet-stream", "spec.md")

    @pytest.mark.parametrize("prefix", [b"<!doctype html>", b"<!DOCTYPE HTML>", b"<html>", b"  \n<html "])
    def test_detection_is_case_and_whitespace_insensitive(self, gitlab_api, prefix):
        with pytest.raises(gitlab_api.ApiError):
            gitlab_api.reject_html_error_page(prefix + b"...", "", "spec.md")

    @pytest.mark.parametrize("filename", ["page.html", "PAGE.HTM", "report.HTML"])
    def test_allows_an_attachment_that_is_genuinely_html(self, gitlab_api, filename):
        gitlab_api.reject_html_error_page(self.SIGN_IN, "text/html", filename)

    @pytest.mark.parametrize(
        "body",
        [b"# \xe8\xa6\x8f\xe6\xa0\xbc", b"%PDF-1.7\n", b'{"a": 1}', b"", b"<p>not a document</p>"],
        ids=["markdown", "pdf", "json", "empty", "html-fragment"],
    )
    def test_allows_real_attachment_content(self, gitlab_api, body):
        gitlab_api.reject_html_error_page(body, "application/octet-stream", "spec.md")


# --------------------------------------------------------------------------
# Error message helpers
# --------------------------------------------------------------------------


class TestRedirectTargetOf:
    def test_keeps_the_host_because_leaving_the_instance_is_the_point(self, gitlab_api):
        assert (
            gitlab_api._redirect_target_of("https://evil.example.com/collect")
            == "https://evil.example.com/collect"
        )

    def test_drops_the_query_string(self, gitlab_api):
        assert (
            gitlab_api._redirect_target_of("https://gitlab.example.com/users/sign_in?token=SECRET")
            == "https://gitlab.example.com/users/sign_in"
        )
        assert "SECRET" not in gitlab_api._redirect_target_of("https://h/x?t=SECRET")

    def test_handles_a_relative_location(self, gitlab_api):
        assert gitlab_api._redirect_target_of("/users/sign_in?x=1") == "/users/sign_in"


class TestErrorMessages:
    def test_401_points_at_the_token_env_vars(self, gitlab_api):
        message = gitlab_api._describe_http_error(401, "https://h/api/v4/user")
        assert "GITLAB_TOKEN" in message and "api scope" in message

    def test_404_explains_that_gitlab_hides_permission_errors(self, gitlab_api):
        # "404" alone also matches the generic fallback branch; pin the
        # explanation sentence this test is named after.
        message = gitlab_api._describe_http_error(404, "https://h/api/v4/x")
        assert "GitLab 對無權限資源一律回傳 404" in message

    def test_error_text_never_echoes_a_query_string(self, gitlab_api):
        message = gitlab_api._describe_http_error(404, "https://h/api/v4/x?private_token=SECRET")
        assert "SECRET" not in message

    def test_a_sign_in_redirect_names_the_actual_cause(self, gitlab_api):
        """The failure this explains looks like a permissions problem and is not."""
        message = gitlab_api._describe_redirect(
            302, "https://h/grp/repo/uploads/abc/x.md", "https://h/users/sign_in"
        )
        assert "登入頁" in message
        assert "/api/v4/" in message

    def test_a_non_sign_in_redirect_stays_generic(self, gitlab_api):
        message = gitlab_api._describe_redirect(301, "https://h/api/v4/x", "https://h/api/v5/x")
        assert "登入頁" not in message
        assert "https://h/api/v5/x" in message

    def test_a_redirect_without_a_location_still_produces_a_message(self, gitlab_api):
        assert "302" in gitlab_api._describe_redirect(302, "https://h/api/v4/x", None)


# --------------------------------------------------------------------------
# read_token
# --------------------------------------------------------------------------


class TestReadToken:
    def test_prefers_gitlab_token(self, gitlab_api, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "primary")
        monkeypatch.setenv("NCR_GITLAB_TOKEN", "fallback")
        assert gitlab_api.read_token() == "primary"

    def test_falls_back_to_ncr_gitlab_token(self, gitlab_api, monkeypatch):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("NCR_GITLAB_TOKEN", "fallback")
        assert gitlab_api.read_token() == "fallback"

    def test_treats_a_blank_value_as_unset(self, gitlab_api, monkeypatch):
        monkeypatch.setenv("GITLAB_TOKEN", "   ")
        monkeypatch.setenv("NCR_GITLAB_TOKEN", "fallback")
        assert gitlab_api.read_token() == "fallback"

    def test_raises_when_neither_is_set(self, gitlab_api, monkeypatch):
        for name in gitlab_api.TOKEN_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(gitlab_api.UsageError):
            gitlab_api.read_token()


# --------------------------------------------------------------------------
# http_request, against the stub server
# --------------------------------------------------------------------------


class TestHttpRequestBasics:
    def test_sends_the_token_as_a_header_and_never_in_the_url(self, gitlab_api, stub_server):
        stub_server.queue(Reply.json({"id": 1}))
        body, _ = gitlab_api.http_request(f"{stub_server.url}/api/v4/user", "s3cret")

        assert body == {"id": 1}
        [request] = stub_server.requests
        assert request.headers["private-token"] == "s3cret"
        assert "s3cret" not in request.path

    def test_returns_none_for_an_empty_body(self, gitlab_api, stub_server):
        stub_server.queue(Reply(status=200, body=b""))
        body, _ = gitlab_api.http_request(f"{stub_server.url}/api/v4/x", "t")
        assert body is None

    def test_returns_raw_bytes_when_json_is_not_expected(self, gitlab_api, stub_server):
        stub_server.queue(Reply(status=200, body=b"\x89PNG\r\n\x1a\n"))
        body, _ = gitlab_api.http_request(f"{stub_server.url}/x", "t", accept_json=False)
        assert body == b"\x89PNG\r\n\x1a\n"

    def test_a_non_json_body_is_an_error_not_a_crash(self, gitlab_api, stub_server):
        stub_server.queue(Reply(status=200, body=b"<html>not json</html>"))
        with pytest.raises(gitlab_api.ApiError, match="JSON"):
            gitlab_api.http_request(f"{stub_server.url}/api/v4/x", "t")

    def test_posts_a_json_payload(self, gitlab_api, stub_server):
        stub_server.queue(Reply.json({"id": 7}))
        gitlab_api.http_request(
            f"{stub_server.url}/api/v4/x", "t", method="POST", payload={"body": "hi"}
        )
        [request] = stub_server.requests
        assert request.method == "POST"
        assert json.loads(request.body) == {"body": "hi"}
        assert request.headers["content-type"] == "application/json"


class TestHttpRequestRedirects:
    """A redirect is never followed. See _NoRedirect for why."""

    def test_a_redirect_raises_instead_of_being_followed(self, gitlab_api, stub_server):
        stub_server.queue(
            Reply(status=302, body=b"", headers={"Location": f"{stub_server.url}/users/sign_in"})
        )
        with pytest.raises(gitlab_api.ApiError, match="302"):
            gitlab_api.http_request(f"{stub_server.url}/grp/repo/uploads/a/x.md", "t")

        # One request, not two: the token was never sent to the redirect target.
        assert len(stub_server.requests) == 1

    def test_a_redirect_is_not_retried(self, gitlab_api, stub_server, fast_retries):
        stub_server.queue(*[Reply(status=302, headers={"Location": "/elsewhere"})] * 3)
        with pytest.raises(gitlab_api.ApiError):
            gitlab_api.http_request(f"{stub_server.url}/api/v4/x", "t")
        assert len(stub_server.requests) == 1

    def test_the_sign_in_case_is_explained_end_to_end(self, gitlab_api, stub_server):
        stub_server.queue(Reply(status=302, headers={"Location": "https://h/users/sign_in?a=b"}))
        with pytest.raises(gitlab_api.ApiError) as excinfo:
            gitlab_api.http_request(f"{stub_server.url}/grp/repo/uploads/a/x.md", "t")
        assert "登入頁" in str(excinfo.value)


class TestHttpRequestRetries:
    @pytest.mark.parametrize("status", sorted({429, 500, 502, 503, 504}))
    def test_retries_a_get_on_a_transient_status(self, gitlab_api, stub_server, fast_retries, status):
        stub_server.queue(Reply(status=status, body=b"{}"), Reply.json({"ok": True}))
        body, _ = gitlab_api.http_request(f"{stub_server.url}/api/v4/x", "t")
        assert body == {"ok": True}
        assert len(stub_server.requests) == 2

    def test_gives_up_after_the_attempt_limit(self, gitlab_api, stub_server, fast_retries):
        stub_server.queue(*[Reply(status=503, body=b"{}")] * gitlab_api.GET_MAX_ATTEMPTS)
        with pytest.raises(gitlab_api.ApiError, match="503"):
            gitlab_api.http_request(f"{stub_server.url}/api/v4/x", "t")
        assert len(stub_server.requests) == gitlab_api.GET_MAX_ATTEMPTS

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_does_not_retry_a_permanent_failure(self, gitlab_api, stub_server, fast_retries, status):
        stub_server.queue(Reply(status=status, body=b"{}"))
        with pytest.raises(gitlab_api.ApiError):
            gitlab_api.http_request(f"{stub_server.url}/api/v4/x", "t")
        assert len(stub_server.requests) == 1

    def test_never_retries_a_post(self, gitlab_api, stub_server, fast_retries):
        """A retried POST would duplicate a comment on the merge request."""
        stub_server.queue(Reply(status=503, body=b"{}"), Reply.json({"id": 1}))
        with pytest.raises(gitlab_api.ApiError):
            gitlab_api.http_request(
                f"{stub_server.url}/api/v4/x", "t", method="POST", payload={"body": "hi"}
            )
        assert len(stub_server.requests) == 1


class TestHttpRequestTimeout:
    def test_a_timeout_becomes_an_api_error(self, gitlab_api, stub_server, monkeypatch):
        monkeypatch.setattr(gitlab_api, "REQUEST_TIMEOUT_SECONDS", 0.2)
        monkeypatch.setattr(gitlab_api, "GET_MAX_ATTEMPTS", 1)
        stub_server.queue(Reply(status=200, body=b"{}", delay=2.0))

        with pytest.raises(gitlab_api.ApiError) as excinfo:
            gitlab_api.http_request(f"{stub_server.url}/api/v4/x", "t")
        # A 2s-delayed reply against a 0.2s timeout is a real socket timeout;
        # accepting "無法連線" too would let the URLError branch pass a test
        # named after timeouts.
        assert "逾時" in str(excinfo.value)


class TestApiBaseOverride:
    """NCR_GITLAB_API_BASE：dev container 限制模式下經 gitlab-proxy 的那條路。

    釘住兩件事：base 換人時 project path/iid 仍來自 MR URL；token 只有在
    override 在場時才可缺席，而缺席時 PRIVATE-TOKEN header 必須整個不帶——
    帶空值會被 GitLab 當成無效憑證 401。
    """

    MR = "https://gitlab.example.com/platform/api/api-backend/-/merge_requests/61"

    def test_override_replaces_scheme_and_host_only(self, gitlab_api, monkeypatch):
        monkeypatch.setenv("NCR_GITLAB_API_BASE", "http://gitlab-proxy:5678/")
        target = gitlab_api.parse_mr_url(self.MR)
        assert target["api_base"] == "http://gitlab-proxy:5678/api/v4"
        # path 與 iid 仍來自 MR URL，不受 override 影響
        assert target["project_path_encoded"] == "platform%2Fapi%2Fapi-backend"
        assert target["iid"] == 61

    def test_without_override_base_is_derived_from_the_url(self, gitlab_api, monkeypatch):
        monkeypatch.delenv("NCR_GITLAB_API_BASE", raising=False)
        target = gitlab_api.parse_mr_url(self.MR)
        assert target["api_base"] == "https://gitlab.example.com/api/v4"

    def test_token_is_optional_only_when_override_is_set(self, gitlab_api, monkeypatch):
        for var in gitlab_api.TOKEN_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("NCR_GITLAB_API_BASE", "http://gitlab-proxy:5678")
        assert gitlab_api.read_token() == ""

        monkeypatch.delenv("NCR_GITLAB_API_BASE")
        with pytest.raises(gitlab_api.UsageError):
            gitlab_api.read_token()

    def test_empty_token_sends_no_private_token_header(self, gitlab_api, stub_server):
        stub_server.queue(Reply.json({"ok": True}))
        gitlab_api.http_request(f"{stub_server.url}/api/v4/user", "")
        assert "private-token" not in stub_server.requests[-1].headers

    def test_real_token_still_sends_the_header(self, gitlab_api, stub_server):
        stub_server.queue(Reply.json({"ok": True}))
        gitlab_api.http_request(f"{stub_server.url}/api/v4/user", "secret-t")
        assert stub_server.requests[-1].headers.get("private-token") == "secret-t"

    def test_a_bare_host_needs_no_url(self, gitlab_api, monkeypatch):
        monkeypatch.delenv("NCR_GITLAB_API_BASE", raising=False)
        assert gitlab_api.api_base_for("gitlab.example.com") == (
            "https://gitlab.example.com/api/v4"
        )

    @pytest.mark.parametrize(
        "override",
        ["http://gitlab-proxy:5678", "http://gitlab-proxy:5678/", "http://gitlab-proxy:5678///"],
        ids=["bare", "one-slash", "many-slashes"],
    )
    def test_trailing_slashes_never_reach_the_url(self, gitlab_api, monkeypatch, override):
        """`.../api/v4` with a doubled slash 404s on GitLab rather than failing loudly."""
        monkeypatch.setenv("NCR_GITLAB_API_BASE", override)
        assert gitlab_api.api_base_for("h") == "http://gitlab-proxy:5678/api/v4"

    def test_a_blank_override_is_treated_as_absent(self, gitlab_api, monkeypatch):
        monkeypatch.setenv("NCR_GITLAB_API_BASE", "   ")
        assert gitlab_api.api_base_for("gitlab.example.com").startswith("https://gitlab")


# --------------------------------------------------------------------------
# Timestamps and the re-review cutoff
# --------------------------------------------------------------------------


class TestParseTimestamp:
    @pytest.mark.parametrize(
        ("value", "expected_hour"),
        [
            ("2026-08-01T09:30:00Z", 9),
            ("2026-08-01T09:30:00+00:00", 9),
            ("2026-08-01T09:30:00.123Z", 9),
            ("2026-08-01T17:30:00+08:00", 17),
        ],
        ids=["trailing-z", "explicit-utc", "fractional", "offset"],
    )
    def test_accepts_the_shapes_gitlab_emits(self, gitlab_api, value, expected_hour):
        parsed = gitlab_api.parse_timestamp(value)
        assert parsed is not None
        assert parsed.hour == expected_hour
        assert parsed.tzinfo is not None

    def test_a_naive_timestamp_is_read_as_utc(self, gitlab_api):
        """Left naive it would raise on the first comparison against a cutoff."""
        from datetime import UTC

        assert gitlab_api.parse_timestamp("2026-08-01T09:30:00").tzinfo is UTC

    @pytest.mark.parametrize(
        "value", [None, "", "   ", "not-a-date", "2026-13-45T99:99:99Z"],
        ids=["none", "empty", "blank", "prose", "out-of-range"],
    )
    def test_returns_none_for_anything_unparseable(self, gitlab_api, value):
        assert gitlab_api.parse_timestamp(value) is None

    def test_require_timestamp_turns_that_into_a_usage_error(self, gitlab_api):
        with pytest.raises(gitlab_api.UsageError) as excinfo:
            gitlab_api.require_timestamp("yesterday")
        assert "ISO-8601" in str(excinfo.value)


class TestFilterDiscussionsSince:
    CUTOFF = "2026-08-01T12:00:00Z"

    @staticmethod
    def _discussion(discussion_id, *notes):
        return {
            "id": discussion_id,
            "notes": [
                {"id": note_id, "created_at": created, "body": "內容"}
                for note_id, created in notes
            ],
        }

    @pytest.fixture
    def cutoff(self, gitlab_api):
        return gitlab_api.require_timestamp(self.CUTOFF)

    def test_keeps_only_notes_after_the_cutoff(self, gitlab_api, cutoff):
        discussions = [
            self._discussion(
                "d1",
                (1, "2026-08-01T11:00:00Z"),
                (2, "2026-08-01T13:00:00Z"),
            )
        ]
        [kept] = gitlab_api.filter_discussions_since(discussions, cutoff)
        assert [n["id"] for n in kept["notes"]] == [2]

    def test_the_boundary_is_strict(self, gitlab_api, cutoff):
        """A note posted at exactly T is the previous round's own report."""
        discussions = [self._discussion("d1", (1, self.CUTOFF))]
        assert gitlab_api.filter_discussions_since(discussions, cutoff) == []

    def test_a_discussion_left_with_no_notes_is_dropped_entirely(self, gitlab_api, cutoff):
        discussions = [
            self._discussion("d1", (1, "2026-08-01T09:00:00Z")),
            self._discussion("d2", (2, "2026-08-01T15:00:00Z")),
        ]
        kept = gitlab_api.filter_discussions_since(discussions, cutoff)
        assert [d["id"] for d in kept] == ["d2"]

    def test_the_original_discussion_is_not_mutated(self, gitlab_api, cutoff):
        """The caller writes the untrimmed list to --out; trimming in place would lose it."""
        discussions = [
            self._discussion("d1", (1, "2026-08-01T09:00:00Z"), (2, "2026-08-01T15:00:00Z"))
        ]
        gitlab_api.filter_discussions_since(discussions, cutoff)
        assert len(discussions[0]["notes"]) == 2

    def test_a_timezone_offset_is_compared_correctly_not_lexically(self, gitlab_api, cutoff):
        """11:00+08:00 is 03:00Z — before the cutoff, despite sorting after it."""
        discussions = [self._discussion("d1", (1, "2026-08-01T11:00:00+08:00"))]
        assert gitlab_api.filter_discussions_since(discussions, cutoff) == []

    @pytest.mark.parametrize(
        "created", [None, "", "not-a-date"], ids=["missing", "empty", "prose"]
    )
    def test_an_undatable_note_stops_the_run_instead_of_vanishing(
        self, gitlab_api, cutoff, created
    ):
        """Silently dropping it is an author reply that disappears.

        On a re-review this filter collects the author's replies. A note that
        cannot be placed in time used to be filtered out along with the ones
        genuinely before the cutoff, and the next report would then say the
        author never answered.
        """
        discussions = [{"id": "d1", "notes": [{"id": 7, "created_at": created}]}]
        with pytest.raises(gitlab_api.ApiError) as excinfo:
            gitlab_api.filter_discussions_since(discussions, cutoff)
        assert "created_at" in str(excinfo.value)

    def test_a_discussion_with_no_notes_at_all_is_not_an_error(self, gitlab_api, cutoff):
        assert gitlab_api.filter_discussions_since([{"id": "d1", "notes": []}], cutoff) == []
        assert gitlab_api.filter_discussions_since([{"id": "d2"}], cutoff) == []
