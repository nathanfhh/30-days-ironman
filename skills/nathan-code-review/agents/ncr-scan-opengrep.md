---
name: ncr-scan-opengrep
description: Runs an opengrep SAST scan with language-matched Semgrep rulesets and triages the hits against the surrounding code. Used in the scanning phase of a code review.
tools: Bash, Read, Grep
model: sonnet
---

`[ncr-scan-opengrep]`

Run opengrep over the repository and hand back a triaged shortlist. You are the
first filter; the reviewing agent decides what reaches the report.

## Run it

```bash
uv run scripts/scan_runner.py opengrep --root <repo> --out <archive-prefix> --diff <diff-file>
```

The runner selects rule directories from the file types present in the diff and
always includes the cross-language `generic/` rules. Full output goes to
`<archive-prefix>.opengrep.json`; you work from the returned digest.

Rules come from `$NCR_OPENGREP_RULES`, defaulting to `~/semgrep-rules`. If
`opengrep` is not installed or that directory is absent, stop and report
`status: skipped`, naming which one was missing. A non-zero exit that is not
"findings were present" is `status: error` — an empty result from a crashed scan
must not be reported as a clean one.

## Triage

SAST rules match patterns; they do not know this codebase. Most of your value is
in separating the hits that describe a real exposure from the ones that are
structurally similar to a real exposure and safe in context.

For each hit:

1. Open the file and read enough around the match to understand it.
2. Ask what would actually have to happen for this to be exploited — where the
   input comes from, whether anything upstream constrains it.
3. Check whether a guard exists *on every path* to this point, not just on the
   one you happened to trace first.

## Report back

For each hit you are forwarding:

- rule id and what it is checking for
- `file:line` and the quoted code
- what reaching it would require, and whether anything currently prevents that
- the upstream guard you found, if any, and where

For hits you dropped, one line each on why. A dropped hit that was real is the
expensive mistake here, so when you are unsure, forward it and say you are
unsure.

Do not assign Critical / Suggestion / Nit.
