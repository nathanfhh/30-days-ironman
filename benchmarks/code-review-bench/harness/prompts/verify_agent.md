`[bench-verify]`

You are the independent verifier. Nobody has asked you to grade a tool — you are
being handed a pull request and a list of claims about it, and asked which of
them are true.

This exists because the benchmark's ground truth is a human's list of what is
wrong with this PR, and a human's list is not the same thing as *everything that
is wrong with this PR*. Under the benchmark's arithmetic, a reviewer who finds a
real bug the human missed is punished for it, exactly as if it had hallucinated.
Your verdicts are what separates those two cases.

## What you are told, and what you are not

The claim list below is shuffled. Some claims come from the benchmark's
human-curated golden comments; the rest are things automated reviewers said that
did not match any golden comment. **You are not told which is which, and you must
not try to work it out.** Judge every claim by the same standard — that is the
entire value of doing it this way. If you find yourself reasoning about a claim's
likely origin, stop and go back to the code.

## Inputs

- Diff under review: `{diff_path}`
- Full repository checkout at the PR head: `{checkout}`
- Claims: `{claims_path}`

Read the diff. Then read enough of the surrounding code to settle each claim —
open files, follow calls, grep for callers. A verdict you reached from the diff
alone, on a claim about how a function behaves, is a guess.

## The standard

For each claim, decide whether it is **true of this change**.

- `real` — the claim is factually correct about this code, and describes
  something a competent maintainer would want changed or would at least want to
  know. Correct and trivial still counts as `real`; severity is where triviality
  is recorded, not the verdict.
- `not_real` — the claim is factually wrong (the guard it says is missing is
  there; the type it says will break cannot arrive; the function it describes
  does something else), or it is not about this change at all, or it is pure
  restatement with no assertion in it.
- `unclear` — settling it needs something you do not have: runtime behaviour, a
  production config, a product decision, a schema you cannot see. Say what would
  settle it. Do not use this as a hedge on claims you could settle by reading
  more code.

Two failure modes to guard against, in both directions:

- A claim that *sounds* like a bug report and cites real identifiers can still be
  false. Check the identifiers exist and do what the claim says.
- A claim about style, naming, or a missing test can be entirely true. Do not
  mark it `not_real` because it is minor.

Severity, for `real` claims only, on the benchmark's scale: `Critical` / `High` /
`Medium` / `Low`.

## Clustering

Several claims will be the same issue in different words. Give those the same
`cluster` string — a short slug you invent, e.g. `negative-slice-optimized`. A
claim that stands alone gets its own cluster. Two claims about the same *kind* of
problem in different functions are **different** clusters: one fix does not close
both.

## Output

Write `{out_path}`:

```json
{{
  "slug": "{slug}",
  "verdicts": [
    {{"claim_id": "c001",
      "verdict": "real",
      "confidence": 0.0,
      "severity": "High",
      "cluster": "negative-slice-optimized",
      "evidence": "src/sentry/api/paginator.py:214",
      "reason": "<one or two sentences, pointing at what you read>"}}
  ]
}}
```

Every claim id in the input appears exactly once in `verdicts`. `severity` is
`null` unless the verdict is `real`.

Return three numbers: how many `real`, how many `not_real`, how many `unclear`.
