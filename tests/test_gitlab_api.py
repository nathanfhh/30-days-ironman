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

MR_URL = "https://gitlab.example.com/his2/group/repo/-/merge_requests/92"


# --------------------------------------------------------------------------
# parse_mr_url
# --------------------------------------------------------------------------


class TestParseMrUrl:
    def test_splits_host_project_and_iid(self, gitlab_api):
        target = gitlab_api.parse_mr_url(MR_URL)
        assert target["host"] == "gitlab.example.com"
        assert target["project_path"] == "his2/group/repo"
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
        ],
    )
    def test_rejects_malformed_urls(self, gitlab_api, bad_url, because):
        with pytest.raises(gitlab_api.UsageError):
            gitlab_api.parse_mr_url(bad_url)


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
            "https://gitlab.example.com/api/v4/projects/his2%2Fgroup%2Frepo"
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
        for raw in ["../../etc/shadow", "..%2F..%2Fetc%2Fshadow", r"..\..\windows"]:
            assert "/" not in gitlab_api.safe_filename(raw, "fallback")


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
        assert "404" in gitlab_api._describe_http_error(404, "https://h/api/v4/x")

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
        assert "逾時" in str(excinfo.value) or "無法連線" in str(excinfo.value)
