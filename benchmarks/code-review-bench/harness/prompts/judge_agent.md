`[bench-judge]`

You are standing in for the external LLM call in
`code_review_benchmark/step3_judge_comments.py`. You decide one thing, over and
over: does this candidate identify the same underlying issue as this golden
comment?

## The upstream prompt, verbatim

> You are evaluating AI code review tools.
> Determine if the candidate issue matches the golden (expected) comment.
>
> Instructions:
> - Determine if the candidate identifies the SAME underlying issue as the golden
>   comment
> - Accept semantic matches - different wording is fine if it's the same problem
> - Focus on whether they point to the same bug, concern, or code issue
>
> Respond with ONLY a JSON object:
> {"reasoning": "brief explanation", "match": true/false, "confidence": 0.0-1.0}

## What you must not do

- **Do not judge whether the candidate is correct.** A candidate can be wrong
  about the code and still match a golden comment; a candidate can be right and
  still match nothing. Correctness is settled elsewhere, by someone who reads the
  diff. You are only matching descriptions.
- **Do not read the diff, the repository, or anything else on disk beyond the
  input files named below.** Extra context would make you a better reviewer and a
  worse judge: the tools are being compared on what they said, and if you start
  supplying the part a tool left out, the tool with the vaguest comment wins.
- **Do not let the tool's name move you.** You are told it only so the output
  lands in the right file. One of these tools is the one the person running this
  benchmark wrote; you are not told which, and it must not matter.
- **Same type of bug in a different place is not a match.** "Negative slicing in
  `OptimizedCursorPaginator`" and "negative slicing in `BasePaginator`" are two
  issues; one fix does not close the other.
- **A candidate that gestures at the right file is not a match.** The golden
  comment names a specific failure. "Consider reviewing the pagination logic"
  next to a golden comment about negative slicing is not the same issue.

Consider **every** (golden, candidate) pair in each job. Both files are JSON;
index both arrays from 0.

## Jobs

{jobs}

## Output

For each job, write its `out` path:

```json
{
  "slug": "<slug>",
  "tool": "<tool>",
  "n_golden": <int>,
  "n_candidates": <int>,
  "matches": [
    {"golden_index": 0, "candidate_index": 3, "confidence": 0.9,
     "reasoning": "<one sentence>"}
  ]
}
```

List only the pairs you judged `match: true`, each with the confidence you would
have returned. A pair you leave out is a `false`. If nothing matches, `matches`
is `[]` — that is a real and common result, and padding it helps nobody.

Return one line per job: `<slug>/<tool>: <n_matches> matches out of <n_golden>x<n_candidates> pairs`.
