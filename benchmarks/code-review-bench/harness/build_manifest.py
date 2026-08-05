#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build the run manifest from the upstream benchmark's own data files.

Selection is deterministic and fixed *before* any golden comment is read, so the
PR set cannot be tuned to flatter the tool under test:

- Python main line — all 10 `sentry*` PRs, no sampling.
- Every other language — the two lowest PR numbers in that repo.

The fork used for the diff is always the `augment` fork. Every tool's fork of a
given benchmark PR carries the same commits; pinning one keeps the input
byte-identical across tools and removes fork choice as a variable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DIFF_FORK_TOOL = "augment"

LANGUAGE_BY_REPO = {
    "sentry": "Python",
    "sentry-greptile": "Python",
    "grafana": "Go",
    "cal.com": "TypeScript",
    "discourse-graphite": "Ruby",
    "keycloak": "Java",
    "keycloak-greptile": "Java",
}

# How many PRs to take per source repo. Python is the main line: everything.
QUOTA = {"sentry": None, "sentry-greptile": None}
DEFAULT_QUOTA = 2

# These two PRs branch off a commit thousands of commits behind their fork's
# default branch, so merge-base is unreachable at any sane fetch depth. Both are
# single-commit PRs — verified by hand against the fork's commit log — so the
# parent of the PR head is the base.
BASE_REV_OVERRIDE = {
    "sentry-greptile-1": "refs/ncr/pr~1",
    "sentry-greptile-3": "refs/ncr/pr~1",
}


def pr_number(url: str) -> int:
    return int(url.rstrip("/").split("/")[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--full",
        action="store_true",
        help="Take every PR in every repo, ignoring the per-repo quota. The quota "
        "bounds cost; it does not shape the sample. Lifting it can only widen "
        "coverage, so it cannot be used to tune the set in the tool's favour.",
    )
    args = ap.parse_args()

    data = json.loads(Path(args.benchmark_data).read_text())

    by_repo: dict[str, list] = {}
    for golden_url, entry in data.items():
        repo = entry.get("source_repo", "unknown")
        fork = next(
            (r["pr_url"] for r in entry.get("reviews", []) if r["tool"] == DIFF_FORK_TOOL),
            None,
        )
        if not fork:
            continue
        by_repo.setdefault(repo, []).append((golden_url, entry, fork))

    manifest = []
    for repo, items in sorted(by_repo.items()):
        items.sort(key=lambda it: pr_number(it[0]))
        quota = None if args.full else QUOTA.get(repo, DEFAULT_QUOTA)
        for golden_url, entry, fork in items[:quota] if quota else items:
            fork_repo = fork.split("/pull/")[0] + ".git"
            slug = f"{repo.replace('.', '_')}-{pr_number(golden_url)}"
            manifest.append(
                {
                    "slug": slug,
                    "source_repo": repo,
                    "language": LANGUAGE_BY_REPO.get(repo, "unknown"),
                    "golden_url": golden_url,
                    "pr_title": entry.get("pr_title"),
                    "original_url": entry.get("original_url"),
                    "fork_repo": fork_repo,
                    "pr_number": 1,
                    "golden_count": len(entry.get("golden_comments", [])),
                    **({"base_rev": BASE_REV_OVERRIDE[slug]} if slug in BASE_REV_OVERRIDE else {}),
                }
            )

    Path(args.out).write_text(json.dumps(manifest, indent=2))
    print(f"{len(manifest)} PRs")
    for m in manifest:
        print(f"  {m['slug']:<28} {m['language']:<11} golden={m['golden_count']}  {m['pr_title'][:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
