`[bench-extract]`

You are standing in for the external LLM call in
`code_review_benchmark/step2_extract_comments.py`. Your job is mechanical
normalisation, not judgement: turn a review into the flat list of distinct
issues it asserts. You do not decide whether any of them is correct.

## The upstream prompt, verbatim

> You are analyzing an AI code review comment to extract individual issues
> mentioned.
>
> The comment may discuss multiple distinct problems. Extract each separate
> issue as a standalone item.
>
> Instructions:
> - Extract each distinct code issue, bug, or concern mentioned
> - Each issue should be a single, specific problem (not a general observation)
> - Ignore meta-commentary like "I found 2 issues" - extract the actual issues
> - Ignore sign-offs, greetings, or formatting instructions
> - If the comment contains no actionable code review issues, return an empty list

## Two things this run adds

1. **The review is in Traditional Chinese; the golden comments and every other
   tool's candidates are in English.** Emit each issue in English, and keep the
   original zh-TW sentence beside it. Translation is part of normalisation here —
   scoring a zh-TW review against English ground truth without it would measure
   the language barrier rather than the review.
2. **`open_questions` are not issues.** The skill files unverified concerns
   separately, without a severity, precisely because they are not assertions. A
   competing tool that hedged the same way would have had the hedge extracted as
   an issue, so leave them out of `issues[]` and list them under
   `open_questions[]` instead — the scorer needs to be able to count them both
   ways.

Findings whose `status` is not live (already resolved, withdrawn) are also not
issues.

## Input

For each review directory listed below, read `report.json`.

{targets}

## Output

For each review, write `candidates.json` next to its `report.json`:

```json
{{
  "slug": "<slug>",
  "issues": [
    {{"text": "<English, one specific problem>",
      "zh": "<the original sentence>",
      "finding_id": "<F-00n or null>",
      "severity": "<Critical|Suggestion|Nit>"}}
  ],
  "open_questions": [
    {{"text": "<English>", "zh": "<original>"}}
  ]
}}
```

One entry per distinct issue. A finding that names two independent problems
becomes two entries; a finding restated in the summary and again in the detail
becomes one. Do not merge two problems just because they sit in the same file.

Return only the per-slug counts (`issues` / `open_questions`). Nothing else.
