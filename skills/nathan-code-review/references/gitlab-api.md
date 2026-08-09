# GitLab API

`scripts/gitlab_api.py` implements everything here. Prefer calling it over
composing requests by hand — several of the details below are ones a language
model reliably gets wrong.

```bash
uv run scripts/gitlab_api.py parse       <mr-url>
uv run scripts/gitlab_api.py whoami      --host <host>
uv run scripts/gitlab_api.py mr          <mr-url>
uv run scripts/gitlab_api.py attachments <mr-url> --dest <dir>
uv run scripts/gitlab_api.py discussions <mr-url> [--since <iso8601>] [--out <file>]
uv run scripts/gitlab_api.py discussion  <mr-url> --id <discussion_id>
uv run scripts/gitlab_api.py post-report <mr-url> --body-file <markdown>
uv run scripts/gitlab_api.py reply       <mr-url> --id <discussion_id> --body-file <markdown>
```

## Host, base URL, and auth

The host comes from the merge request URL itself, so there is no instance to
configure: `baseUrl = https://{host}/api/v4`.

**Proxy override.** When `NCR_GITLAB_API_BASE` is set (e.g.
`http://gitlab-proxy:5678`, exported automatically by the dev container's run
wrapper when the proxy network is attached), the API base becomes
`{NCR_GITLAB_API_BASE}/api/v4` while the merge request URL still supplies the
project path and iid. This is the only way GitLab API calls work in the
container's restricted network mode — the firewall blocks direct HTTPS to the
GitLab host, and the proxy injects `PRIVATE-TOKEN` itself. The proxy exposes
only the endpoints listed below; anything else returns 403, which is a
whitelist decision, not an auth failure.

The token is read from `GITLAB_TOKEN`, falling back to `NCR_GITLAB_TOKEN`, and
sent as a `PRIVATE-TOKEN` header. With `NCR_GITLAB_API_BASE` set the token is
optional and normally absent — the proxy holds it, which is the point. If
neither a token nor the override is set, stop and tell the user in zh-TW which
variable to set and that the token needs `api` scope. Do not attempt to
continue in a degraded mode — every step of `mr` mode depends on it.

The token is never written to a file, never echoed, and never appears in the
report.

**Credentials in the URL are stripped, not used.** A merge request URL of the
form `https://user:token@host/...` has its userinfo removed before anything is
built from the host, and the removal is stated on stderr. Otherwise the
credential would travel into the API base, into `publication.url` in the report,
and into every error message quoting the URL back.

## Parsing the URL

```
https://gitlab.example.com/platform/api/api-backend/-/merge_requests/61
        └──── host ─────┘ └── project path ──┘                └iid┘
```

`project_path` must be percent-encoded before it goes into a URL path. Encode it
with `urllib.parse.quote(path, safe="")` — hand-substituting `/` for `%2F` is
where this goes wrong, because it silently leaves other characters unescaped.
Alternatively use the numeric project id from the MR response.

**`id` versus `iid`.** Every merge request has two numbers. `id` is unique across
the whole instance — a large number that means nothing to a human. `iid` is the
per-project number starting from 1, and it is the one at the end of the URL. The
endpoints below all take `iid`.

## Endpoints

### Confirm the token, and who you are

`GET /user`

Run before anything else. Establishes that the token works and which account
would be attributed for any comment posted later.

### Fetch the merge request

`GET /projects/:id/merge_requests/:merge_request_iid`

Fields taken from the response: `title`, `description`, `source_branch`,
`target_branch`, `web_url`, `project_id`.

The diff is **not** taken from the API. It is computed from the clone — see
`workspace-paths.md`.

### Rebuilding the tree when the clone is blocked

Only for the restricted-network and proxy cases in `workspace-paths.md`. These
are the fallback for a diff that cannot be computed locally; they are not the
normal path, because a tree assembled from them is a partial one.

`GET /projects/:id/repository/compare?from={target_branch}&to={source_branch}`

Returns `diffs[]`, each with `old_path`, `new_path`, and a unified `diff`. Set
`straight=false` (the default) so the comparison is against the merge base, which
is what `git diff --merge-base` would have given.

`GET /projects/:id/repository/files/:file_path/raw?ref={source_branch}`

One file, in full. `file_path` is percent-encoded whole, `/` included — the same
`quote(path, safe="")` rule as `project_path`. Fetch every file the compare
touched, plus the ones a finding needs as context; a diff hunk on its own cannot
show a guard that sits ten lines above it.

`GET /projects/:id/repository/tree?ref={branch}&path={dir}&recursive=true`

Lists what exists, for deciding what else to pull. Paginated like everything
else.

Mark the result `rebuilt-from-API` and disclose the limits — `workspace-paths.md`
lists which of them bite.

### Download attachments

`GET /projects/:id/uploads/:secret/:filename`

Attachments in the MR description appear as `[name](/uploads/{secret}/{filename})`.
They are usually the requirement spec or screenshots, which makes them the
material for judging whether the implementation actually covers what was asked
for — often the only place that question can be answered at all.

Download each one and handle it by extension: `.md` / `.txt` read against the
diff for requirement coverage, images viewed. A single attachment that fails to
download is noted in the report and the review continues; it is not fatal.

### Discussions

`GET  /projects/:id/merge_requests/:merge_request_iid/discussions`
`POST /projects/:id/merge_requests/:merge_request_iid/discussions`
`GET  /projects/:id/merge_requests/:merge_request_iid/discussions/:discussion_id`
`POST /projects/:id/merge_requests/:merge_request_iid/discussions/:discussion_id/notes`

The report is published as a **discussion** — a thread that can be replied to and
resolved. The response gives back a `discussion_id`, and the root note gives a
`created_at`; both are written into the report's `publication` block, and they
are the two handles the next round needs: the index for retrieving the author's
replies, and the cutoff time T.

On a re-review, list the discussions and take replies made after T. When the
`discussion_id` is already known, fetching the single discussion is the cheaper
call — it returns that thread's replies instead of everything on the MR.

`--out <file>` writes the discussions to a file and prints **only that path** —
no count, no timestamp. That is what `ncr-fetch-threads` runs during a re-review,
so that the replies can be fetched while the blind pass is still sealed; a count
is the smallest digest of them and is withheld for the same reason the text is.

A note whose `created_at` cannot be parsed stops the command rather than being
filtered out: on a re-review a dropped note is an author reply that vanishes, and
"no reply" is what the report would then say.

Replying to the author uses the last endpoint.

### Individual notes

`GET  /projects/:id/merge_requests/:merge_request_iid/notes`
`POST /projects/:id/merge_requests/:merge_request_iid/notes`

For one-off remarks. **The report is never published this way** — posting one
hands back a note id, not a `discussion_id`, and that handle is what the next
round needs in order to find the author's replies.

### Notes versus discussions

A note is the atom; a discussion is the container. Every note lives inside some
discussion, without exception — including the system-generated ones ("added 1
commit"). What differs is the container:

- **Individual note discussion** — holds one note. The UI's "Comment" button.
- **Thread** — holds a root note plus replies. The UI's "Start thread". Can be
  replied to and resolved, and `POST …/discussions` returns its `discussion_id`
  straight away. A variant carries a `position` anchoring it to a
  specific line of the diff; that is the inline comment familiar from code
  review.

## Writes require a human

The heaviest rule in this skill. Every outward-facing, irreversible action —
posting the report, replying to an author, any `POST` — goes through the user
first. Show the draft in the conversation, wait for approval, then send.

You never call a write endpoint on your own initiative, and approval for one post
is not approval for the next one.
