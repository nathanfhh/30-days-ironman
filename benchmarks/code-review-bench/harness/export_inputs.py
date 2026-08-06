#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Export golden comments and comparison tools' candidates for the selected PRs.

The comparison tools' candidate lists are taken verbatim from the upstream
benchmark's own `results/<judge-model>/candidates.json` — the output of its
step 2 extraction. Re-extracting them here would change their input, and the
whole point is that every tool goes into the judge through the same door.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# The comparison set, chosen from the upstream Opus-4.5 leaderboard before any
# of our own numbers existed:
#   cubic-v2       — F1 leader
#   augment        — highest-F1 tool with a public benchmark writeup of its own
#   greptile-v4-1  — widely deployed, mid-table
#   coderabbit     — the most widely installed commercial reviewer
#   claude-code    — same model family as the tool under test; the control that
#                    separates "the skill" from "the model"
COMPARISON_TOOLS = ["cubic-v2", "augment", "greptile-v4-1", "coderabbit", "claude-code"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-data", required=True)
    ap.add_argument("--candidates", required=True, help="upstream results/<model>/candidates.json")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.benchmark_data).read_text())
    cands = json.loads(Path(args.candidates).read_text())
    manifest = json.loads(Path(args.manifest).read_text())
    out_root = Path(args.out)

    missing = []
    for entry in manifest:
        url = entry["golden_url"]
        if url not in data:
            raise SystemExit(
                f"{entry['slug']}: golden_url {url} is not in {args.benchmark_data}. "
                "The manifest was built from a different benchmark_data.json than "
                "the one passed here — rebuild the manifest or pass the matching file."
            )
        pr = data[url]
        out_dir = out_root / entry["slug"]
        (out_dir / "candidates").mkdir(parents=True, exist_ok=True)

        (out_dir / "golden.json").write_text(
            json.dumps(
                {
                    "slug": entry["slug"],
                    "golden_url": url,
                    "pr_title": pr.get("pr_title"),
                    "language": entry["language"],
                    "comments": pr.get("golden_comments", []),
                },
                indent=2,
            )
        )

        for tool in COMPARISON_TOOLS:
            tool_cands = cands.get(url, {}).get(tool)
            if tool_cands is None:
                missing.append((entry["slug"], tool))
                tool_cands = []
            (out_dir / "candidates" / f"{tool}.json").write_text(
                json.dumps([c["text"] for c in tool_cands if c.get("text")], indent=2)
            )

    print(f"exported {len(manifest)} PRs x {len(COMPARISON_TOOLS)} comparison tools")
    if missing:
        print("MISSING candidate lists (will score as zero-candidate reviews):")
        for slug, tool in missing:
            print(f"  {slug} / {tool}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
