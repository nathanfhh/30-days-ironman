#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""How far does the substitute judge sit from upstream's own judges?

Everything here rests on a subagent standing in for an OpenAI-compatible API
call. That substitution is only defensible if it lands in the same place, so it
is measured rather than asserted: the comparison tools' candidate lists are the
same bytes upstream judged, and upstream published its verdicts under three
judge models. Scoring those same lists with our judge and comparing is a direct
read on whether the stand-in is an outlier.

The number that matters is not the delta against any one judge — it is the delta
against the *spread* between upstream's own three. A stand-in that differs from
Opus-4.5 by less than Opus-4.5 differs from GPT-5.2 has not introduced a new
source of error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, help="scores/upstream_baseline.json")
    ap.add_argument("--ours", required=True, help="scores/summary.json")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    baseline = json.loads(Path(args.baseline).read_text())
    ours = json.loads(Path(args.ours).read_text())["raw"]

    judges = sorted(baseline)
    tools = [t for t in ours if any(t in baseline[j] for j in judges)]

    rows = []
    for tool in tools:
        upstream_f1 = [baseline[j][tool]["f1"] for j in judges if tool in baseline[j]]
        row = {
            "tool": tool,
            "ours_f1": ours[tool]["f1"],
            "upstream_f1": {j: baseline[j][tool]["f1"] for j in judges if tool in baseline[j]},
            "upstream_spread": max(upstream_f1) - min(upstream_f1),
            "ours_vs_upstream_mean": ours[tool]["f1"] - sum(upstream_f1) / len(upstream_f1),
        }
        rows.append(row)

    rows.sort(key=lambda r: -r["ours_f1"])
    Path(args.out).write_text(json.dumps(rows, indent=2))

    short = {j: j.split("_")[-1][:14] for j in judges}
    header = f"{'tool':<16}{'ours':>8}" + "".join(f"{short[j]:>16}" for j in judges) + f"{'spread':>9}{'delta':>8}"
    print(header)
    print("-" * len(header))
    for r in rows:
        line = f"{r['tool']:<16}{r['ours_f1']:>7.1%}"
        line += "".join(f"{r['upstream_f1'].get(j, 0):>16.1%}" for j in judges)
        line += f"{r['upstream_spread']:>9.1%}{r['ours_vs_upstream_mean']:>+8.1%}"
        print(line)

    max_delta = max(abs(r["ours_vs_upstream_mean"]) for r in rows)
    max_spread = max(r["upstream_spread"] for r in rows)
    print(
        f"\nlargest |ours - upstream mean| = {max_delta:.1%}; "
        f"largest spread among upstream's own judges = {max_spread:.1%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
