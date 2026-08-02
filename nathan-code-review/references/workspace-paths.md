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
  (`his/abc-backend` and `lis/abc-backend`); the repo name alone collides.
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
project at `his/abc/abc-backend` archives to `$HOME/ncr/his/abc/abc-backend/`.

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
