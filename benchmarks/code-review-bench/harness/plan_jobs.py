#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Print the judge job list for one tool, so the orchestrator can hand it to an agent.

Judging is batched per tool, never per PR-with-all-tools: a judge that sees five
tools' wording for the same golden comment is anchored by whichever phrased it
best, and that anchoring would not fall evenly across tools. Batching along the
other axis — one tool, several unrelated PRs — has no such effect, and it is
applied identically to every tool including ours.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from candidates import OUR_TOOL, candidate_texts


def candidates_path(data_root: Path, slug: str, tool: str) -> Path:
    if tool == OUR_TOOL:
        return data_root / "reviews" / slug / "candidates.json"
    return data_root / "prs" / slug / "candidates" / f"{tool}.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--tool", required=True)
    ap.add_argument("--batch", type=int, default=5)
    args = ap.parse_args()

    data_root = Path(args.data).resolve()
    manifest = json.loads((data_root / "manifest.json").read_text())

    jobs = []
    for entry in manifest:
        slug = entry["slug"]
        gpath = data_root / "prs" / slug / "golden.json"
        cpath = candidates_path(data_root, slug, args.tool)
        texts, n_issues = candidate_texts(data_root, slug, args.tool)
        n_c = len(texts)
        if n_c == 0:
            continue  # nothing to judge; the scorer already treats this as zero-candidate
        n_g = len(json.loads(gpath.read_text())["comments"])
        out = data_root / "judgments" / slug / f"{args.tool}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            continue
        jobs.append(
            {
                "slug": slug,
                "tool": args.tool,
                "golden": str(gpath),
                "candidates": str(cpath),
                "candidates_field": (
                    "issues[].text followed by open_questions[].text, concatenated in that "
                    f"order — indices 0..{n_issues - 1} are issues, {n_issues}.. are open questions"
                    if args.tool == OUR_TOOL
                    else "(the array itself)"
                ),
                "n_golden": n_g,
                "n_candidates": n_c,
                "out": str(out),
            }
        )

    for i in range(0, len(jobs), args.batch):
        print(f"----- BATCH {i // args.batch + 1} -----")
        print(json.dumps(jobs[i : i + args.batch], indent=2))
    if not jobs:
        print("(no jobs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
