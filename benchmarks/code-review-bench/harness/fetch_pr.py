#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fetch the exact diff each benchmark tool reviewed.

withmartian/code-review-benchmark forks every benchmark PR once per tool into
`code-review-benchmark/<repo>__<project>__<tool>__PR<N>__<date>`. The tools
reviewed *that fork's* PR #1, not the upstream PR — so the upstream diff is the
wrong input to hand our reviewer. We fetch the fork instead, and we fetch the
same fork for every run so the diff is byte-identical across tools.

Only git is used: the environment's git proxy serves public GitHub repos, while
the GitHub REST API is not reachable.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

FETCH_DEPTH = 60  # deep enough for merge-base on every benchmark PR seen so far


def run(cmd: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def fetch_pr_diff(repo_url: str, pr_number: int, workdir: Path, base_rev: str | None = None) -> dict:
    """Clone just enough of `repo_url` to produce the PR's base..head diff.

    `base_rev` overrides the merge-base search. Two of the synthetic Sentry PRs
    branch off a commit thousands of commits behind the fork's default branch,
    so a merge-base would need most of Sentry's history; both are single-commit
    PRs, and their base is pinned to `refs/ncr/pr~1` in the manifest instead.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    if not (workdir / ".git").exists():
        run(["git", "init", "-q", "."], workdir)
        run(["git", "remote", "add", "origin", repo_url], workdir)

    head = run(
        ["git", "fetch", "--depth", str(FETCH_DEPTH), "origin", f"refs/pull/{pr_number}/head:refs/ncr/pr"],
        workdir,
    )
    if head.returncode != 0:
        return {"error": f"fetch pr head failed: {head.stderr.strip()[:400]}"}

    if base_rev:
        rev = run(["git", "rev-parse", base_rev], workdir)
        if rev.returncode != 0:
            return {"error": f"base_rev {base_rev} not resolvable: {rev.stderr.strip()[:200]}"}
        mb = rev
    else:
        base = run(["git", "fetch", "--depth", str(FETCH_DEPTH), "origin", "HEAD:refs/ncr/base"], workdir)
        if base.returncode != 0:
            return {"error": f"fetch base failed: {base.stderr.strip()[:400]}"}
        mb = run(["git", "merge-base", "refs/ncr/base", "refs/ncr/pr"], workdir)
    if not base_rev and (mb.returncode != 0 or not mb.stdout.strip()):
        # Shallow histories can miss the merge base; deepen once and retry.
        run(["git", "fetch", "--deepen", "200", "origin", f"refs/pull/{pr_number}/head:refs/ncr/pr"], workdir)
        run(["git", "fetch", "--deepen", "200", "origin", "HEAD:refs/ncr/base"], workdir)
        mb = run(["git", "merge-base", "refs/ncr/base", "refs/ncr/pr"], workdir)
        if mb.returncode != 0 or not mb.stdout.strip():
            return {"error": "merge-base not found within fetch depth"}

    base_sha = mb.stdout.strip()
    head_sha = run(["git", "rev-parse", "refs/ncr/pr"], workdir).stdout.strip()

    diff = run(["git", "diff", "--unified=8", base_sha, head_sha], workdir)
    stat = run(["git", "diff", "--stat", base_sha, head_sha], workdir)
    names = run(["git", "diff", "--name-status", base_sha, head_sha], workdir)
    log = run(["git", "log", "--format=%h %s", f"{base_sha}..{head_sha}"], workdir)

    return {
        "repo_url": repo_url,
        "pr_number": pr_number,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "diff": diff.stdout,
        "stat": stat.stdout,
        "name_status": names.stdout,
        "commits": log.stdout,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, help="JSON list of {slug, fork_repo, pr_number}")
    ap.add_argument("--out", required=True, help="output directory for per-PR diffs")
    ap.add_argument("--workdir", required=True, help="scratch directory for git clones")
    ap.add_argument("--only", help="comma-separated slugs to fetch")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    wanted = set(args.only.split(",")) if args.only else None
    out_root = Path(args.out)
    work_root = Path(args.workdir)

    for entry in manifest:
        slug = entry["slug"]
        if wanted and slug not in wanted:
            continue
        out_dir = out_root / slug
        if (out_dir / "diff.patch").exists():
            print(f"skip  {slug} (already fetched)")
            continue

        result = fetch_pr_diff(
            entry["fork_repo"], entry["pr_number"], work_root / slug, entry.get("base_rev")
        )
        if "error" in result:
            print(f"FAIL  {slug}: {result['error']}", file=sys.stderr)
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "diff.patch").write_text(result.pop("diff"))
        (out_dir / "meta.json").write_text(json.dumps({**entry, **result}, indent=2))
        lines = result["stat"].strip().splitlines()
        print(f"ok    {slug}: {lines[-1].strip() if lines else 'empty diff'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
