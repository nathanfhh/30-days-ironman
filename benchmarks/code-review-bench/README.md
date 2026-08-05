# nathan-code-review on Code Review Bench

Running this repo's review skill against [withmartian/code-review-benchmark][crb]
— the offline half — and, while we are in there, checking the benchmark's own
ground truth with the same instrument.

[crb]: https://github.com/withmartian/code-review-benchmark

## What this is

The upstream benchmark ships 50 pull requests from five OSS projects, each with a
set of human-curated **golden comments**, and an LLM pipeline that scores a
tool's review against them: extract issues from the review, judge each one
against each golden comment, count TP / FP / FN.

We do three things with it:

1. **Run `nathan-code-review` on a subset** and score it with that pipeline, so
   the number sits on the same axis as the published leaderboard.
2. **Re-judge five comparison tools on the same PRs with the same judge**, so the
   comparison is not confounded by which judge model produced which leaderboard.
3. **Audit the ground truth.** Every candidate the judge matched to nothing, and
   every golden comment, goes to an independent verifier that reads the actual
   diff — blind to which claims came from the humans and which from the tools.
   The corrected score is computed from what survives.

Point 3 is the reason this exists. Upstream's own methodology names the problem
("the gold set caps measurement at human performance… existing benchmarks
structurally cannot measure superhuman performance and will actively punish it")
and proposes adversarial validation as the fix — but the shipped offline scorer
does not do it. A tool that finds a real bug the annotator missed is scored
identically to one that hallucinated.

## No external LLM is called

Upstream's pipeline makes three OpenAI-compatible calls: extract (step 2), dedup
(step 2.5), judge (step 3). All three are replaced by subagents carrying the
upstream prompt verbatim, plus the constraints the batching makes necessary. The
prompts live in `harness/prompts/`, and each one quotes the upstream text it
stands in for.

Nothing here talks to an LLM API, to GitLab, or to the GitHub REST API. PR
content comes from git over the session's proxy.

## Fairness, and how it is enforced

The whole exercise is worthless if the tool under test gets an advantage, so each
one is pinned down explicitly:

| Risk | What holds it down |
|---|---|
| Reviewing a different diff than the tools did | The diff is the `base..head` of *the same fork PR* every tool reviewed, pulled from `code-review-benchmark/<repo>__<project>__augment__PR<n>__<date>`, not the upstream PR |
| Picking flattering PRs | Selection is fixed in `build_manifest.py` before any golden comment is read: all 10 Python PRs, then the two lowest PR numbers per other repo |
| Peeking at the answers | Review agents are barred from `data/` beyond their own diff and output dir, and from the network |
| A judge that likes our phrasing | One judge prompt, one judge model, every tool. Judges never learn which tool is ours and never read the diff |
| Correcting only our own false positives | The verifier rules on every tool's unmatched candidates and on the golden comments, in one shuffled anonymous pool per PR |
| Our review being in zh-TW against English ground truth | Extraction emits English and keeps the zh-TW original beside it — otherwise the score measures the language barrier |
| Crediting our findings but not theirs | A confirmed-real cluster is credited to *every* tool that raised it |

Known and disclosed rather than fixed:

- **Contamination.** These PRs are public and old. Our reviewer's model may have
  seen them, the benchmark repo, or its golden comments. Every tool in the
  comparison has the same exposure in principle, but not necessarily the same
  amount, and nothing here measures it.
- **Model era.** The comparison tools' reviews were collected in Jan–Mar 2026
  with whatever models they shipped then. Ours runs on today's. The `claude-code`
  row is the closest thing to a control for that.
- **Scanners.** Only `ruff` is installed; `trivy`, `opengrep`, `ty`, `oxlint`,
  `codegraph` are not. The skill's scan phase runs degraded, and every report
  discloses it.
- **No dedup.** Upstream's step 2.5 is not run, for any tool, matching the
  published `evaluations.json` baseline.

## Layout

```
harness/
  build_manifest.py   PR selection (deterministic)
  fetch_pr.py         base..head diff for each fork PR, via git only
  export_inputs.py    golden comments + comparison tools' candidate lists
  plan_jobs.py        judge job batches, one tool at a time
  build_claims.py     the blind claim pool per PR
  score.py            raw (upstream arithmetic) + corrected metrics
  prompts/            the four agent prompts that replace the LLM calls
data/
  manifest.json       the 19 PRs under test
  prs/<slug>/         diff.patch, meta.json, golden.json, candidates/<tool>.json
  reviews/<slug>/     our report.json, report.md, candidates.json
  judgments/<slug>/   <tool>.json — the judge's match list
  calibration/        <slug>.claims.json (blind), .map.json (key), .verdicts.json
scores/               raw_cells.csv, corrected_cells.csv, summary.json,
                      ground_truth_audit.json
REPORT.md             the writeup
```

## Reproducing

```bash
cd benchmarks/code-review-bench
git clone --depth 1 https://github.com/withmartian/code-review-benchmark.git /tmp/crb

uv run harness/build_manifest.py \
  --benchmark-data /tmp/crb/offline/results/benchmark_data.json \
  --out data/manifest.json
uv run harness/fetch_pr.py --manifest data/manifest.json --out data/prs --workdir /tmp/clones
uv run harness/export_inputs.py \
  --benchmark-data /tmp/crb/offline/results/benchmark_data.json \
  --candidates /tmp/crb/offline/results/anthropic_claude-opus-4-5-20251101/candidates.json \
  --manifest data/manifest.json --out data/prs
```

The four agent stages (review → extract → judge → verify) are dispatched by the
orchestrating session using `harness/prompts/*.md`; `plan_jobs.py` and
`build_claims.py` produce their inputs. Then:

```bash
uv run harness/score.py --data data \
  --tools nathan-code-review,cubic-v2,augment,greptile-v4-1,coderabbit,claude-code \
  --out scores
```
