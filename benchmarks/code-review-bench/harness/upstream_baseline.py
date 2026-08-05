#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Upstream's own published scores, restricted to the PRs we ran.

Two uses. It is the comparison tools' number as their authors' judge produced it,
which is the honest thing to quote next to ours. And because upstream publishes
the same evaluations under three judge models, the spread between them is a free
measurement of how much of any gap is judge noise rather than tool quality —
worth knowing before reading anything into a three-point difference.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="upstream offline/results directory")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--tools", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    urls = {m["golden_url"] for m in json.loads(Path(args.manifest).read_text())}
    tools = args.tools.split(",")
    out: dict[str, dict] = {}

    for eval_path in sorted(Path(args.results).glob("*/evaluations.json")):
        judge = eval_path.parent.name
        ev = json.loads(eval_path.read_text())
        out[judge] = {}
        for tool in tools:
            tp = fp = fn = n = 0
            for url in urls:
                r = ev.get(url, {}).get(tool)
                if not r or r.get("skipped"):
                    continue
                tp += r.get("tp", 0)
                fp += r.get("fp", 0)
                fn += r.get("fn", 0)
                n += 1
            p = tp / (tp + fp) if (tp + fp) else 0.0
            rc = tp / (tp + fn) if (tp + fn) else 0.0
            out[judge][tool] = {
                "tp": tp, "fp": fp, "fn": fn, "prs": n,
                "precision": p, "recall": rc,
                "f1": 2 * p * rc / (p + rc) if (p + rc) else 0.0,
            }

    Path(args.out).write_text(json.dumps(out, indent=2))
    for judge, block in out.items():
        print(f"== {judge} ==")
        for tool, m in block.items():
            print(f"  {tool:<16} P={m['precision']:6.1%} R={m['recall']:6.1%} "
                  f"F1={m['f1']:6.1%}  TP={m['tp']:3} FP={m['fp']:3} FN={m['fn']:3}  n={m['prs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
