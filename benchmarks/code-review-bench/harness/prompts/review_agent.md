`[ncr-bench-review]`

You are running the **nathan-code-review** skill against one pull request, as a
benchmark run. Follow the skill; do not invent a shortcut for it.

## Inputs

- Skill root: `/home/user/30-days-ironman-PRIVATE/skills/nathan-code-review`
- Repository checkout (already at the PR head): `{checkout}`
- Precomputed diff of the change under review: `{diff_path}`
  It is `git diff {base_sha} {head_sha}` inside that checkout. `{base_sha}` is
  the merge base, so this is exactly the change the PR proposes.
- PR title: `{pr_title}`
- Write your output to: `{out_dir}`

## Mode

This is `local_branch` mode, round 1, on a repository you cannot push to and a
forge you cannot reach. So:

- Phase 0: run `uv run scripts/preflight.py` from the skill root and record what
  is present. There is no GitLab token and no MR — `mr` is `null`.
- Phase 1: round 1. No archive, no re-review protocol.
- Phase 4 step 4 and 5 (asking to publish, publishing): **do not**. Stop after
  the report JSON validates and the Markdown is rendered.

Everything else in the pipeline applies in full.

## Rules that matter for this run

1. **You are not allowed to look for the answer.** Somewhere on this machine
   there is a benchmark directory holding human-written "golden comments" for
   this PR. Do not open, grep, or list anything under
   `/home/user/30-days-ironman-PRIVATE/benchmarks/code-review-bench/data`
   other than the two input paths named above and your own output directory. Do
   not fetch anything from the network. A review that peeked is worthless as a
   measurement, and nobody downstream would be able to tell.
2. **Fresh eyes first.** Before you read `references/review-dimensions.md`, try
   to dispatch the `ncr-fresh-eyes` subagent on the diff (Agent tool,
   `subagent_type: ncr-fresh-eyes`, or a general-purpose subagent carrying
   `agents/ncr-fresh-eyes.md` as its prompt). If you cannot dispatch a subagent
   at all, skip it and say so in `meta` — do not simulate it yourself, and do
   not quietly drop it.
3. **Scanners.** Only `ruff` and `git` are installed here; `trivy`, `opengrep`,
   `ty`, `oxlint` and `codegraph` are not. Run what exists, record every absent
   tool in `scans[]` with a reason, and navigate with `grep` where the skill
   would have used CodeGraph.
4. **The assertion gate is the point.** Any finding that says something is
   missing or will break must survive a search for its disproof — is the missing
   piece elsewhere in this diff, is the breaking path already blocked upstream.
   What survives gets a severity; what cannot be settled goes to
   `open_questions` with no severity. This is being measured against a human
   reviewer, so a confident wrong finding costs more than a missing one.
5. **Read the surrounding code.** The checkout is complete. A finding about a
   function you only saw three lines of in the diff is a guess.

## Output

Write to `{out_dir}`:

- `report.json` — the skill's report JSON, validated:
  `uv run scripts/report_model.py validate {out_dir}/report.json`
  Re-run until it passes.
- `report.md` — `uv run scripts/render_report.py {out_dir}/report.json --out {out_dir}/report.md`

Then dispatch `ncr-quality-check` over the JSON if you can, apply its
corrections, and re-validate. If you cannot dispatch it, note that in `meta`.

## What to return

Six lines at most: the conclusion string, the counts by severity, whether fresh
eyes and quality-check ran, and anything about this PR that a reader of the
benchmark results would need to know. Your findings live in the files — do not
repeat them to me.
