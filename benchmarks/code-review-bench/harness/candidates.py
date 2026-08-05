#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""One definition of "what did this tool claim", shared by every downstream stage.

The awkward case is our own tool's `open_questions`. The skill files an unsettled
concern there deliberately, with no severity, precisely because it is not an
assertion — and under the benchmark's arithmetic every extra candidate is a
straight subtraction from precision, so dropping them from the candidate list
flatters us by construction. But a competing tool that hedged the same way would
have had the hedge extracted as an issue, so *keeping* them is the comparable
choice.

Rather than pick the answer that suits us, both are computed. Open questions are
appended after the issues and their start index is recorded, so a single judging
pass supports two scores:

    strict   every candidate, open questions included — the headline
    lenient  issues only

The gap between them is itself a result: it is what the skill's habit of not
asserting what it cannot settle is worth under this metric.
"""

from __future__ import annotations

import json
from pathlib import Path

OUR_TOOL = "nathan-code-review"


def candidate_texts(data_root: Path, slug: str, tool: str) -> tuple[list[str], int]:
    """Return (candidates, n_issues). For every tool but ours, n_issues == len()."""
    if tool != OUR_TOOL:
        path = data_root / "prs" / slug / "candidates" / f"{tool}.json"
        texts = json.loads(path.read_text()) if path.exists() else []
        return texts, len(texts)

    path = data_root / "reviews" / slug / "candidates.json"
    if not path.exists():
        return [], 0
    blob = json.loads(path.read_text())
    issues = [i["text"] for i in blob.get("issues", []) if i.get("text")]
    questions = [q["text"] for q in blob.get("open_questions", []) if q.get("text")]
    return issues + questions, len(issues)
