# Workspace and archive paths

Two kinds of storage, with different lifetimes. Getting them mixed up is how a
report ends up quoting a path that only exists on the review machine.

## Working directory — disposable

```
/tmp/ncr/{group}-{repo}-mr{iid}/
├── repo/          the clone
└── ...            attachments, digests, scratch
```

- **Why `{iid}` and not one directory per repository?** Because two merge
  requests on the same project get reviewed at the same time, and each needs its
  own branch checked out. Sharing a directory means two reviews fighting over
  `git checkout`. One MR, one directory.
- **Why `{group}`?** The same repository name lives under different groups
  (`alpha/api-backend` and `beta/api-backend`); the repo name alone collides.
- **Why `/tmp`?** The clone is a work copy, used and discarded. `/tmp` is cleared
  on reboot, so there is no cleanup mechanism to build or forget to run.

Nothing under `/tmp` may ever be cited in a published report.

### Getting the code

```bash
git clone --filter=blob:none \
  -c http.extraHeader="PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "https://{host}/{project_path}.git" \
  "/tmp/ncr/{group}-{repo}-mr{iid}/repo"
```

`--filter=blob:none` keeps the full commit graph — `--merge-base` needs it — while
fetching file contents lazily. Passing the token with `-c` keeps it out of the
clone's `.git/config`.

If the directory already exists (a re-review), do not re-clone:

```bash
git -C <repo> fetch --filter=blob:none origin
git -C <repo> checkout -B {source_branch} origin/{source_branch}
```

Then the diff, computed locally rather than fetched from the API:

```bash
git -C <repo> diff --merge-base origin/{target_branch} {source_branch}
```

### When the clone is blocked

Two environments cannot clone over HTTPS, and both are normal rather than
broken:

- **Restricted network.** The dev container's firewall drops outbound 443 to the
  GitLab host; only the proxy is reachable.
- **Proxy mode.** `NCR_GITLAB_API_BASE` is set and no token is present — the
  proxy holds the credential, so there is nothing here for `git` to authenticate
  with.

Detect it, do not push through: one `git clone` attempt that fails on connection
or authentication is the signal. Then rebuild the tree from the API instead.

```bash
# the diff, from the compare endpoint rather than from git
uv run scripts/gitlab_api.py mr <mr-url>          # source_branch, target_branch

# then, per file the diff touches, fetch the file itself and its neighbours
# (see references/gitlab-api.md for the compare and repository-files endpoints)
```

Write the fetched files under the same working directory, at their repository
paths, so every path in the report stays repo-relative. Fetch each changed file
in full — a diff hunk alone cannot answer "is there a guard further up this
function".

**Mark the tree, and disclose it.** A tree rebuilt this way is not the
repository: it holds the changed files and whatever context was pulled in
alongside them, and nothing else. Record it as `rebuilt-from-API` and say so in
the report, because three things silently change:

- `git diff --merge-base` is unavailable; the diff came from the API instead.
- Whole-project scans cover only what was fetched. `ty` cannot resolve types it
  never saw, and dimension I's "who else calls this" cannot be answered from
  this tree — say so rather than reporting a clean result.
- The vuln scan usually has no target at all, because lockfiles were not among
  the fetched files. `scanners.md` covers what trivy's empty report means here:
  "no target" is not "clean", and the report must not read as though it were.

Then build the symbol graph, synchronously — it takes well under a second:

```bash
codegraph init <repo>
```

The index lands in `<repo>/.codegraph/` and is discarded with the clone. Exclude
it from scanning; see `scanners.md`.

## Archive — permanent

Earlier reports are how the next round knows whether this is a first review or a
re-review, so they outlive `/tmp`:

```
$HOME/ncr/{group}/{subgroup}/{repo}/
```

Mirror the project path exactly as GitLab has it, however many levels deep. A
project at `platform/api/api-backend` archives to `$HOME/ncr/platform/api/api-backend/`.

### Filenames

One scan, one prefix:

```
mr{iid}_from_{source_branch}_to_{target_branch}_{YYYY-mm-dd_HHMM}
```

Branch names routinely contain `/` (`feature/two-campus-support`). **Replace `/`
with `-` before composing the filename**, or the path turns into directories you
did not mean to create.

| Suffix | Contents |
|---|---|
| `.json` | the report — the one that matters |
| `.trivy.json` | raw trivy output |
| `.opengrep.json` | raw opengrep output |
| `.lint.json` | raw ruff / ty / oxlint output |

```
mr4_from_feature-two-campus-support_to_main_2026-07-23_2039.json
mr4_from_feature-two-campus-support_to_main_2026-07-23_2039.trivy.json
mr4_from_feature-two-campus-support_to_main_2026-07-23_2039.opengrep.json
mr4_from_feature-two-campus-support_to_main_2026-07-23_2039.lint.json
```

### Local mode

A `local_branch` review has no MR, so it archives under a reserved directory with
its own prefix:

```
$HOME/ncr/_local/{repo}/local_{branch}_{YYYY-mm-dd_HHMM}.json
```

Same `/` → `-` substitution on the branch name. `local_files` mode archives
nothing at all.

## Version

The skill's version is `YYYY.mm.dd.NN` — date plus a same-day serial, e.g.
`2026.08.03.01`. It lives in the SKILL.md frontmatter and is copied into
`meta.skill_version` on every report, so a later analysis can tell which version
produced which review.
