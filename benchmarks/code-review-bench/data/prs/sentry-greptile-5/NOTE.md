# sentry-greptile-5 — the title does not describe the diff

The reviewer flagged this PR as mislabelled, and it is. The fork PR is titled
"Replays Self-Serve Bulk Delete System", but `base..head` spans 32 unrelated
merged master commits and the bulk-delete work is a small part of it.

The diff is nevertheless the right input, and was not mis-derived here:

- the comparison tools' own candidate lists for this PR describe *this* diff
  (augment names `use-table-widget-visualization`, `zip(error_ids,
  events.values())`, `_truncate_title` — all present in it), so every tool saw
  the same thing;
- the golden comments describe this diff too ("Detector validator uses wrong key
  when updating type", the `zip(error_ids, ...)` ordering bug), not the
  bulk-delete feature the title names;
- upstream already knows: `benchmark_data.json` carries
  `az_comment: "there is no such PR, it is a mix of many PRs"` for it.

So it stays in the run, scored like the rest. What it costs the benchmark is
recall realism: three golden comments against a 106-file, ~8700-line diff is a
ground truth covering a sliver of what a reviewer is being asked to read, and
every unmatched-but-real finding on the other 100-odd files lands as a false
positive.
