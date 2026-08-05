#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Score the benchmark run — raw first, then corrected.

`raw` reproduces `step3_judge_comments.py`'s arithmetic exactly, including the
part of it that is easy to misread: the precision numerator is the number of
*golden comments matched*, not the number of candidates that matched something.
Two candidates hitting the same golden comment therefore add 1 to the numerator
and 2 to the denominator. That is upstream's definition, and reproducing it is
the only way our numbers sit on the same axis as their leaderboard.

`corrected` answers the question the raw score cannot: the golden comments are
one human's list, so a candidate that matches nothing might be noise — or might
be a real defect that human did not write down. An independent verifier, blind to
which claims came from the humans and which from the tools, rules on every
unmatched candidate *and on every golden comment*, and the ground truth is rebuilt
from what survives:

    valid golden          golden comments the verifier confirmed
    discovered issues     clusters of unmatched candidates confirmed real,
                          credited to every tool that raised one
    expanded ground truth valid golden ∪ discovered issues

Both corrections are applied to every tool identically. Crediting our own tool's
extra findings while leaving the comparison tools scored against the narrow
ground truth would be the single easiest way to fake a win here.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from candidates import candidate_texts



# The manifest labels a PR with its source repo's language, which is wrong for
# five of the fifty: two "Go" grafana PRs are pure TypeScript, a "Java" keycloak
# PR is 44 .properties files, and the mislabelled sentry-greptile-5 carries more
# TypeScript than Python. Grouping by repo would report those under a language
# nobody reviewed, so the language a PR is scored under is derived from what its
# diff actually contains.
_EXT_LANG = {
    "go": "Go", "ts": "TypeScript", "tsx": "TypeScript", "js": "TypeScript",
    "es6": "TypeScript", "java": "Java", "rb": "Ruby", "py": "Python",
    "scss": "SCSS/CSS", "css": "SCSS/CSS", "properties": "i18n/config",
}


def diff_language(diff_path: Path) -> str | None:
    """Dominant source language of a diff, ignoring config and template files."""
    if not diff_path.exists():
        return None
    counts: dict[str, int] = {}
    for line in diff_path.read_text(errors="replace").splitlines():
        if not line.startswith("+++") or "/dev/null" in line or "." not in line:
            continue
        lang = _EXT_LANG.get(line.rsplit(".", 1)[-1].strip())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0


def score_raw(data_root: Path, slug: str, tool: str, lenient: bool = False) -> dict:
    """One (PR, tool) cell, by upstream's arithmetic.

    `lenient` drops our tool's `open_questions` from the candidate list. See
    `candidates.py` for why both are reported rather than one being chosen.
    """
    golden = json.loads((data_root / "prs" / slug / "golden.json").read_text())["comments"]
    candidates, n_issues = candidate_texts(data_root, slug, tool)
    if lenient:
        candidates = candidates[:n_issues]
    jpath = data_root / "judgments" / slug / f"{tool}.json"
    matches = json.loads(jpath.read_text())["matches"] if jpath.exists() else []

    # Upstream keeps only the highest-confidence match per golden comment, and
    # marks a candidate matched the moment it wins any golden comment.
    best: dict[int, dict] = {}
    matched_candidates: set[int] = set()
    for m in matches:
        gi, ci = m["golden_index"], m["candidate_index"]
        if not (0 <= gi < len(golden) and 0 <= ci < len(candidates)):
            continue
        matched_candidates.add(ci)
        if m.get("confidence", 0) > best.get(gi, {}).get("confidence", -1):
            best[gi] = m

    tp = len(best)
    total_c = len(candidates)
    total_g = len(golden)
    return {
        "slug": slug,
        "tool": tool,
        "tp": tp,
        "fp": total_c - len(matched_candidates),
        "fn": total_g - tp,
        "total_candidates": total_c,
        "total_golden": total_g,
        "matched_golden": sorted(best),
        "matched_candidates": sorted(matched_candidates),
        "unmatched_candidates": [i for i in range(total_c) if i not in matched_candidates],
        "precision": tp / total_c if total_c else 0.0,
        "recall": tp / total_g if total_g else 0.0,
    }


def score_corrected(data_root: Path, slug: str, tools: list[str], raw: dict[str, dict]) -> dict | None:  # noqa: C901
    """Rebuild the ground truth for one PR from the blind verifier's verdicts."""
    vpath = data_root / "calibration" / f"{slug}.verdicts.json"
    mpath = data_root / "calibration" / f"{slug}.map.json"
    if not (vpath.exists() and mpath.exists()):
        return None

    verdicts = {v["claim_id"]: v for v in json.loads(vpath.read_text())["verdicts"]}
    claim_map = json.loads(mpath.read_text())["claims"]

    valid_golden: set[int] = set()
    invalid_golden: set[int] = set()
    # cluster -> tools that raised a confirmed-real claim in it
    discovered: dict[str, set[str]] = defaultdict(set)
    unclear = {"golden": 0, "candidate": 0}

    for claim in claim_map:
        v = verdicts.get(claim["claim_id"])
        if v is None:
            continue
        if claim["source"] == "golden":
            if v["verdict"] == "real":
                valid_golden.add(claim["golden_index"])
            elif v["verdict"] == "not_real":
                invalid_golden.add(claim["golden_index"])
            else:
                unclear["golden"] += 1
                valid_golden.add(claim["golden_index"])  # unsettled: leave it standing
        else:
            if v["verdict"] == "real":
                discovered[v.get("cluster") or claim["claim_id"]].add(claim["tool"])
            elif v["verdict"] != "not_real":
                unclear["candidate"] += 1

    expanded_gt = len(valid_golden) + len(discovered)

    cells = {}
    for tool in tools:
        r = raw[tool]
        # A golden comment the verifier struck down stops counting either way.
        tp_golden = len([gi for gi in r["matched_golden"] if gi not in invalid_golden])
        extra = len([c for c, raisers in discovered.items() if tool in raisers])
        numerator = tp_golden + extra
        total_c = r["total_candidates"]

        # Leave-one-out recall. The expanded ground truth is built from what the
        # tools found, so a verbose tool writes most of its own exam: scoring it
        # against a target it alone defined measures verbosity, not recall. So
        # for each tool the target is rebuilt without the clusters only that tool
        # raised — every tool is then measured against issues somebody else also
        # found, which is a target none of them controls.
        loo_clusters = [c for c, raisers in discovered.items() if raisers - {tool}]
        loo_gt = len(valid_golden) + len(loo_clusters)
        loo_hits = tp_golden + len([c for c in loo_clusters if tool in discovered[c]])

        cells[tool] = {
            "slug": slug,
            "tool": tool,
            "tp": numerator,
            "tp_from_golden": tp_golden,
            "tp_from_discovered": extra,
            "fp": total_c - numerator,
            "fn": expanded_gt - numerator,
            "total_candidates": total_c,
            "expanded_ground_truth": expanded_gt,
            "precision": numerator / total_c if total_c else 0.0,
            "recall": numerator / expanded_gt if expanded_gt else 0.0,
            "loo_tp": loo_hits,
            "loo_fn": loo_gt - loo_hits,
            "loo_ground_truth": loo_gt,
            "loo_recall": loo_hits / loo_gt if loo_gt else 0.0,
            # Fully symmetric view: the human ground truth minus the comments the
            # verifier struck down. No tool influences this target, so it is the
            # one recall number that cannot be gamed by saying more.
            "golden_tp": tp_golden,
            "golden_fn": len(valid_golden) - tp_golden,
            "valid_golden": len(valid_golden),
        }

    return {
        "cells": cells,
        "valid_golden": len(valid_golden),
        "invalid_golden": len(invalid_golden),
        "discovered_clusters": len(discovered),
        "unclear": unclear,
        "discovered_detail": {c: sorted(t) for c, t in discovered.items()},
    }


def aggregate(cells: list[dict]) -> dict:
    tp = sum(c["tp"] for c in cells)
    fp = sum(c["fp"] for c in cells)
    fn = sum(c["fn"] for c in cells)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1(precision, recall),
        "prs": len(cells),
        **_loo(cells, precision),
        **_golden(cells, precision),
    }


def _golden(cells: list[dict], precision: float) -> dict:
    """Recall against the validated human ground truth alone."""
    if not cells or "golden_tp" not in cells[0]:
        return {}
    tp = sum(c["golden_tp"] for c in cells)
    fn = sum(c["golden_fn"] for c in cells)
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {"golden_tp": tp, "golden_fn": fn, "golden_recall": recall, "golden_f1": f1(precision, recall)}


def _loo(cells: list[dict], precision: float) -> dict:
    """Leave-one-out recall, aggregated, plus the F1 it implies."""
    if not cells or "loo_tp" not in cells[0]:
        return {}
    tp = sum(c["loo_tp"] for c in cells)
    fn = sum(c["loo_fn"] for c in cells)
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {"loo_tp": tp, "loo_fn": fn, "loo_recall": recall, "loo_f1": f1(precision, recall)}


def table(title: str, rows: list[tuple], header: tuple) -> str:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    out = [title, "-" * (sum(widths) + 2 * len(widths))]
    out.append("  ".join(str(h).ljust(w) for h, w in zip(header, widths)))
    for r in rows:
        out.append("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--tools", required=True, help="comma-separated, ours included")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data_root = Path(args.data)
    tools = args.tools.split(",")
    manifest = json.loads((data_root / "manifest.json").read_text())
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    raw_cells: list[dict] = []
    corr_cells: list[dict] = []
    gt_notes: list[dict] = []

    lenient_cells: list[dict] = []

    for entry in manifest:
        slug = entry["slug"]
        entry["diff_language"] = diff_language(data_root / "prs" / slug / "diff.patch")
        raw = {t: score_raw(data_root, slug, t) for t in tools}
        for t in tools:
            cell = score_raw(data_root, slug, t, lenient=True)
            cell["language"] = entry.get("diff_language") or entry["language"]
            lenient_cells.append(cell)
        for t in tools:
            raw[t]["language"] = entry.get("diff_language") or entry["language"]
            raw_cells.append(raw[t])
        corrected = score_corrected(data_root, slug, tools, raw)
        if corrected:
            for t in tools:
                cell = corrected["cells"][t]
                cell["language"] = entry.get("diff_language") or entry["language"]
                corr_cells.append(cell)
            gt_notes.append(
                {
                    "slug": slug,
                    "language": entry.get("diff_language") or entry["language"],
                    "golden_total": raw[tools[0]]["total_golden"],
                    "valid_golden": corrected["valid_golden"],
                    "invalid_golden": corrected["invalid_golden"],
                    "discovered_clusters": corrected["discovered_clusters"],
                    "unclear": corrected["unclear"],
                    "discovered_detail": corrected["discovered_detail"],
                }
            )

    def write_csv(name: str, cells: list[dict], fields: list[str]):
        with open(out_root / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(cells)

    write_csv(
        "raw_cells.csv",
        raw_cells,
        ["slug", "language", "tool", "tp", "fp", "fn", "total_candidates", "total_golden", "precision", "recall"],
    )
    if corr_cells:
        write_csv(
            "corrected_cells.csv",
            corr_cells,
            ["slug", "language", "tool", "tp", "tp_from_golden", "tp_from_discovered", "fp", "fn",
             "total_candidates", "expanded_ground_truth", "precision", "recall",
             "loo_tp", "loo_fn", "loo_ground_truth", "loo_recall",
             "golden_tp", "golden_fn", "valid_golden"],
        )
    write_csv(
        "raw_cells_lenient.csv",
        lenient_cells,
        ["slug", "language", "tool", "tp", "fp", "fn", "total_candidates", "total_golden", "precision", "recall"],
    )
    (out_root / "ground_truth_audit.json").write_text(json.dumps(gt_notes, indent=2))

    summary = {
        "raw": {}, "raw_lenient": {}, "corrected": {},
        "raw_by_language": {}, "corrected_by_language": {},
    }
    for tool in tools:
        summary["raw"][tool] = aggregate([c for c in raw_cells if c["tool"] == tool])
        summary["raw_lenient"][tool] = aggregate([c for c in lenient_cells if c["tool"] == tool])
        if corr_cells:
            summary["corrected"][tool] = aggregate([c for c in corr_cells if c["tool"] == tool])
        for lang in sorted({c["language"] for c in raw_cells}):
            summary["raw_by_language"].setdefault(lang, {})[tool] = aggregate(
                [c for c in raw_cells if c["tool"] == tool and c["language"] == lang]
            )
            if corr_cells:
                summary["corrected_by_language"].setdefault(lang, {})[tool] = aggregate(
                    [c for c in corr_cells if c["tool"] == tool and c["language"] == lang]
                )
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2))

    def rows(block: dict) -> list[tuple]:
        rs = [
            (t, f"{m['precision']:.1%}", f"{m['recall']:.1%}", f"{m['f1']:.1%}", m["tp"], m["fp"], m["fn"], m["prs"])
            for t, m in block.items()
        ]
        return sorted(rs, key=lambda r: -float(r[3].rstrip("%")))

    header = ("tool", "precision", "recall", "F1", "TP", "FP", "FN", "PRs")
    print(table("RAW (upstream arithmetic, our judge)", rows(summary["raw"]), header))
    print()
    print(table("RAW, lenient (our open_questions excluded)", rows(summary["raw_lenient"]), header))
    if summary["corrected"]:
        print()
        print(table("CORRECTED (blind-verified ground truth)", rows(summary["corrected"]), header))
        print()
        loo_rows = sorted(
            (
                (t, f"{m['precision']:.1%}", f"{m['loo_recall']:.1%}", f"{m['loo_f1']:.1%}",
                 m["loo_tp"], m["fp"], m["loo_fn"], m["prs"])
                for t, m in summary["corrected"].items()
            ),
            key=lambda r: -float(r[3].rstrip("%")),
        )
        print(table(
            "CORRECTED, leave-one-out recall (target excludes each tool's solo finds)",
            loo_rows, header,
        ))
        print()
        g_rows = sorted(
            (
                (t, f"{m['precision']:.1%}", f"{m['golden_recall']:.1%}", f"{m['golden_f1']:.1%}",
                 m["golden_tp"], m["fp"], m["golden_fn"], m["prs"])
                for t, m in summary["corrected"].items()
            ),
            key=lambda r: -float(r[3].rstrip("%")),
        )
        print(table(
            "CORRECTED, recall vs validated golden only (target no tool influences)",
            g_rows, header,
        ))
    print(f"\nwrote {out_root}/summary.json, raw_cells.csv, corrected_cells.csv, ground_truth_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
