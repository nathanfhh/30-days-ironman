# Re-review: the anti-anchoring protocol

A second review is not a sequel to the first one. It is a fresh review that
happens to have history available.

That distinction is the whole point of this file. If last round's conclusions
reach you before you have looked at the code, they reshape what you attend to:
you inherit the previous reading's momentum, you re-confirm what you already
said, and the paths that reading missed stay missed. You cannot ask a model that
has read the previous report to pretend it has not — that freedom is fake once
the text is in context.

So the sequence is **blind first, compare second**, and the blindness has to be
real.

## What is sealed

During the blind pass you may not read:

- the previous report JSON, or any rendered version of it;
- the discussion threads fetched by `ncr-fetch-threads`;
- any summary of either.

`ncr-fetch-threads` runs in parallel with the Phase 2 scanners and writes its
output to a file in the working directory. **You do not open that file yet.**

The threads are sealed alongside the report for one reason: author replies quote
the findings they are replying to. "F-003 我改了", "C-2 我不同意，因為……" — reading
the replies is reading the previous report through a side door. New information
the author brought is genuinely valuable, and you will get it at the compare
step, before any severity is finalised.

## Blind pass

Review the **entire MR diff**, as if this merge request had never been looked at.
Not just the commits added since last time — the whole change, from scratch,
through the normal Phase 2 and Phase 3 flow.

Number what you find with temporary IDs: `B-1`, `B-2`, … These are scratch labels
and never appear in the report.

## Compare

Now unseal both the previous report and the threads file, together.

### Renumber

Official IDs are `F-001`, `F-002`, … allocated per merge request, monotonically,
never reused. For each blind finding:

- **Matches a previous finding** → keep the previous `F-` number and set
  `status: reconfirmed`. The author has been referring to that number; it has to
  keep meaning the same thing.
- **New** → allocate the next unused number, `status: new`.

For each previous finding the blind pass did not surface:

- **It is genuinely addressed** → `status: resolved`. It stays in the report as
  history rather than vanishing.
- **It was withdrawn after pushback** → `status: withdrawn`, and the reasoning
  is already in `pushback[]`.

Only `new` and `reconfirmed` findings count toward the conclusion.

### Answer both questions in the report

These are not private notes. Both get answered explicitly in the report, because
they are what makes the re-review worth reading rather than a diff of two lists:

- **Q1** (`rereview.q1_new_evidence`) — the biggest uncertainty in the previous
  round: is there new evidence now?
- **Q2** (`rereview.q2_new_paths`) — do the new commits expose an execution path
  the previous round never saw?

They are fields rather than prose, and `report_model.py` rejects a round-2 report
without them — a requirement with nowhere specific to be missing from is a
requirement that quietly stops being met.

### Cutoff time T

`ncr-fetch-threads` needs a cutoff so it collects replies made *after* the last
report, not the entire history. T is the `created_at` of the root note of the
discussion the previous report was published as — both values are stored in the
previous report's `publication` block.

If `publication` is absent (a report generated but never published), there is no
cutoff and no threads to fetch: treat the previous report as history and skip the
fetch.
