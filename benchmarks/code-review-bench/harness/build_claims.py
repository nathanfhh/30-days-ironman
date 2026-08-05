#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build the blind claim pool the independent verifier rules on.

For one PR the pool holds:

- every golden comment, and
- every candidate that the judge matched to no golden comment, from every tool.

They go in stripped of their origin and in an order that has nothing to do with
it, so the verifier cannot tell a human's comment from a tool's. That is the
whole mechanism: the same reader, applying the same standard, to the ground truth
and to the things the ground truth rejected. Anything less and "the golden set
missed something" is just our own tool's opinion of itself.

The order is a stable hash, not a random shuffle — rerunning must produce the
same pool, or the verdicts stop lining up with the claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from candidates import candidate_texts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--tools", required=True)
    ap.add_argument("--slug", required=True)
    args = ap.parse_args()

    data_root = Path(args.data)
    slug = args.slug
    tools = args.tools.split(",")

    golden = json.loads((data_root / "prs" / slug / "golden.json").read_text())["comments"]

    pool = [
        {"source": "golden", "golden_index": i, "tool": None, "text": g["comment"]}
        for i, g in enumerate(golden)
    ]

    for tool in tools:
        cands, _ = candidate_texts(data_root, slug, tool)
        jpath = data_root / "judgments" / slug / f"{tool}.json"
        if not jpath.exists():
            continue
        matched = {m["candidate_index"] for m in json.loads(jpath.read_text())["matches"]}
        for i, text in enumerate(cands):
            if i not in matched:
                pool.append(
                    {"source": "candidate", "golden_index": None, "tool": tool,
                     "candidate_index": i, "text": text}
                )

    pool.sort(key=lambda c: hashlib.sha256(f"{slug}:{c['text']}".encode()).hexdigest())
    for n, claim in enumerate(pool, 1):
        claim["claim_id"] = f"c{n:03d}"

    out_dir = data_root / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{slug}.map.json").write_text(json.dumps({"slug": slug, "claims": pool}, indent=2))
    (out_dir / f"{slug}.claims.json").write_text(
        json.dumps(
            {"slug": slug, "claims": [{"claim_id": c["claim_id"], "text": c["text"]} for c in pool]},
            indent=2,
        )
    )

    n_golden = sum(1 for c in pool if c["source"] == "golden")
    print(f"{slug}: {len(pool)} claims ({n_golden} golden + {len(pool) - n_golden} unmatched candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
