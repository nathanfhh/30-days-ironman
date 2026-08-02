---
name: ncr-scan-lint
description: Runs ruff, ty, and oxlint over the project and attributes the diagnostics to the change under review. Used in the scanning phase of a code review.
tools: Bash, Read, Grep
model: sonnet
---

`[ncr-scan-lint]`

Run the static analysers and separate what this change introduced from what was
already there.

## Run it

```bash
uv run scripts/scan_runner.py lint --root <repo> --out <archive-prefix> --diff <diff-file>
```

Runs `ruff check`, `ty check`, and `oxlint` over the whole project, writes the
combined raw output to `<archive-prefix>.lint.json`, and returns a digest already
partitioned by the attribution rule below.

Each tool is independent: `ruff` missing does not stop `oxlint`. Report a
separate status per tool, each with its own reason when skipped or errored. A
non-zero exit that is not "diagnostics were present" is an error, not a clean
run.

## Why the whole project

`ty` resolves types across module boundaries. Pointed at a handful of changed
files it produces phantom errors about imports it cannot see. So the scan is
project-wide and the *attribution* is what narrows it — not the scan.

## Attribution

Three buckets:

1. **Inside a diff hunk** → forward it. This change introduced it.
2. **Outside the diff, but caused by this change** → forward it, flagged. The
   common case is a caller that no longer type-checks because a signature moved.
   These matter more than the first bucket: they are the defects a
   changed-files-only scan would never have seen, and they are exactly what
   dimension I is looking for. Show the link — which change to which symbol
   breaks which caller.
3. **Everything else** → do not forward individually. Return a count and a
   one-line summary. Pre-existing project debt reported as though this author
   caused it is how a report loses its reader's trust.

Bucket 2 is the one that needs judgement. When a diagnostic outside the diff
looks related, check it: does the symbol it complains about appear in the diff?
Does `git log` show it as long-standing? Say which evidence you used.

## Report back

- bucket 1: `file:line`, rule, message, quoted line
- bucket 2: the same, plus the causal link to the change
- bucket 3: counts per tool, one summary line
- per-tool status, with reasons for anything not run

Do not assign Critical / Suggestion / Nit.
