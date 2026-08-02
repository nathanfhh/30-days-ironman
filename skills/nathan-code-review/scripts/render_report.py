# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render a nathan-code-review report JSON into the Markdown that gets published
as a GitLab merge request discussion.

The report JSON is expected to have been validated already:

    uv run scripts/report_model.py validate <report.json>

so this renderer deliberately depends on nothing outside the standard library --
no pydantic, no import of report_model. It reads the JSON with the json module
and only re-checks what rendering itself is responsible for.

The fixed frame of the document lives in ``assets/report_template.md``; every
repeating block (the nine-cell grid, the findings, the collapsed sections) is
assembled here and injected as a pre-rendered string.

All rendered text is Traditional Chinese, with technical terms left in English:
severity names, conclusion strings, tool names, file paths and identifiers.

CLI:
    uv run render_report.py <report.json> [--out <report.md>]

With no ``--out`` the Markdown is printed to stdout.

Exit codes:
    0  the Markdown was rendered
    1  the report is structurally unusable, or the rendered Markdown failed the
       self-contained check (it cited a path on the review machine)
    2  usage error: bad arguments, or a file that could not be read or written
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# Mirrors report_model.DIMENSION_TITLES. It is duplicated rather than imported
# because importing that module would drag pydantic into a renderer that must
# run anywhere; keep the two in sync when a dimension is renamed.
DIMENSION_TITLES: dict[str, str] = {
    "A": "風格",
    "B": "簡潔",
    "C": "安全",
    "D": "API 慣例",
    "E": "架構",
    "F": "資料取用與資料庫",
    "G": "測試",
    "H": "非 Python 檔",
    "I": "回溯分析",
}

# The nine-cell grid of references/report-format.md: three tables of three
# columns, A-C / D-E-F / G-H-I.
GRID_ROWS: tuple[tuple[str, ...], ...] = (("A", "B", "C"), ("D", "E", "F"), ("G", "H", "I"))

VERDICT_MARKS: dict[str, str] = {"pass": "✅", "fail": "❌", "na": "—"}
VERDICT_LABELS: dict[str, str] = {"pass": "通過", "fail": "未通過", "na": "不適用"}

# Mirrors report_model.LIVE_STATUSES: only these describe the current state of
# the branch, so only these are counted in the summary line.
LIVE_STATUSES: frozenset[str] = frozenset({"new", "reconfirmed"})
HISTORY_STATUSES: frozenset[str] = frozenset({"resolved", "withdrawn"})
STATUS_LABELS: dict[str, str] = {
    "new": "new（本輪新提出）",
    "reconfirmed": "reconfirmed（前一輪已提出，本輪仍存在）",
    "resolved": "已解決",
    "withdrawn": "已撤回",
}

SEVERITY_ORDER: tuple[str, ...] = ("Critical", "Suggestion", "Nit")

SCAN_STATUS_LABELS: dict[str, str] = {"ok": "已執行", "skipped": "略過", "error": "錯誤"}

# Risk treatments carry an English enum value in the JSON; the gloss keeps the
# published text readable in zh-TW without renaming the term.
TREATMENT_GLOSS: dict[str, str] = {
    "Accept": "接受",
    "Transfer": "移轉",
    "Avoid": "避免",
    "Mitigate": "降低",
}

INTENT_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("should_do", "該不該做？"),
    ("right_mr", "該在這個 MR 做？"),
    ("right_timing", "該在這個時機做？"),
)

# meta.round semantics from report_model.Meta: 1 is a first review, 2 or more is
# a re-review, which is the round that must answer Q1 and Q2.
RE_REVIEW_ROUND = 2

RE_REVIEW_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("Q1", "前一輪最大的不確定，現在是否有新證據"),
    ("Q2", "新的 commit 是否暴露前一輪沒看到的執行路徑"),
)

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "assets" / "report_template.md"

# The published note is read by people with no access to the review machine, so
# an absolute local path in it is meaningless at best and tells them where the
# reviewer's files live at worst. Rule 2 of references/report-format.md: the
# report may cite only what a reader can reach -- repository-relative paths, the
# merge request, its commits. These patterns are what the rendered Markdown is
# scanned for before anything is written out.
LOCAL_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    # POSIX absolute path. The lookbehind keeps URLs ("https://host/x") and
    # repo-relative paths ("app/api/x.py") out; requiring two non-empty segments
    # keeps a bare "/packages/" written in prose out.
    re.compile(r"(?<![\w:/.~\\-])/[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+[A-Za-z0-9_./+-]*"),
    # $HOME / ~ paths: machine-local too, even without a leading slash.
    re.compile(r"(?<![\w])(?:~|\$HOME)/[A-Za-z0-9_.+-]+[A-Za-z0-9_./+-]*"),
    # Windows drive letter, e.g. C:\work\repo or D:/work/repo.
    re.compile(r"(?<![\w])[A-Za-z]:[\\/][A-Za-z0-9_.+-]+[A-Za-z0-9_./\\+-]*"),
)


class ReportError(Exception):
    """The report cannot be rendered as it stands."""


class Violation(NamedTuple):
    """One machine-local path found in the rendered Markdown."""

    location: str
    path: str


class SafeDict(dict):
    """A format mapping that leaves an unknown placeholder visibly intact.

    ``str.format_map`` raises KeyError on a placeholder the mapping does not
    cover. During publishing that would turn a template typo into a crash that
    loses the whole rendered report; leaving ``{some_key}`` standing in the
    output instead makes the same mistake obvious to whoever reviews the
    Markdown before it is posted, while everything else still renders.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# --------------------------------------------------------------------------
# Small typed accessors -- the JSON was validated elsewhere, but a renderer that
# blows up with a TypeError halfway through publishing is worse than one that
# says exactly which block is unusable.
# --------------------------------------------------------------------------


def _as_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportError(f"{label} 缺少或格式不正確：預期為物件")
    return value


def _as_list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReportError(f"{label} 格式不正確：預期為陣列")
    return value


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required_text(container: dict[str, Any], key: str, label: str) -> str:
    text = _as_text(container.get(key))
    if not text:
        raise ReportError(f"{label} 缺少必要欄位：{key}")
    return text


def _code(text: str) -> str:
    """Wrap a path or identifier in backticks, widening the fence if needed."""
    if "`" not in text:
        return f"`{text}`"
    longest = max(len(run) for run in re.findall(r"`+", text))
    fence = "`" * (longest + 1)
    return f"{fence} {text} {fence}"


def _fence(text: str) -> str:
    """Fence a block of code, using more backticks than the content contains."""
    runs = re.findall(r"`+", text)
    # Three backticks is the markdown minimum; grow past any run inside the text.
    width = max(3, (max(len(run) for run in runs) + 1) if runs else 3)
    fence = "`" * width
    return f"{fence}\n{text}\n{fence}"


# --------------------------------------------------------------------------
# Block builders
# --------------------------------------------------------------------------


def build_dimension_grid(dimensions: dict[str, Any]) -> str:
    """Three markdown tables of three centre-aligned cells each."""
    missing = [letter for letter in DIMENSION_TITLES if letter not in dimensions]
    if missing:
        raise ReportError(f"dimensions 缺少面向判定：{'、'.join(missing)}")

    tables: list[str] = []
    for row in GRID_ROWS:
        headers = " | ".join(f"{letter} {DIMENSION_TITLES[letter]}" for letter in row)
        aligns = "|".join([":--:"] * len(row))
        marks: list[str] = []
        for letter in row:
            entry = _as_dict(dimensions[letter], f"dimensions.{letter}")
            verdict = _as_text(entry.get("verdict"))
            if verdict not in VERDICT_MARKS:
                raise ReportError(f"dimensions.{letter}.verdict 不是合法值：{verdict!r}")
            marks.append(VERDICT_MARKS[verdict])
        tables.append(f"| {headers} |\n|{aligns}|\n| " + " | ".join(marks) + " |")
    return "\n\n".join(tables)


def build_dimension_notes(dimensions: dict[str, Any]) -> str:
    """List the note behind every dimension that did not pass."""
    lines: list[str] = []
    for letter, title in DIMENSION_TITLES.items():
        entry = _as_dict(dimensions[letter], f"dimensions.{letter}")
        verdict = _as_text(entry.get("verdict"))
        if verdict == "pass":
            continue
        note = _as_text(entry.get("note")) or "（未附說明）"
        label = VERDICT_LABELS.get(verdict, verdict)
        lines.append(f"- **{letter} {title}**（{label}）：{note}")
    return "\n".join(lines)


def build_rereview_section(report: dict[str, Any], round_number: int) -> str:
    """The Q1 / Q2 answers a re-review owes its readers.

    `report_model.py` requires the `rereview` block from round 2 onward, so on a
    re-review these answers are read straight out of it rather than recovered
    from prose. A round-1 report has nothing to say here.
    """
    if round_number < RE_REVIEW_ROUND:
        return ""

    block = report.get("rereview")
    if not isinstance(block, dict):
        # Only reachable if the report skipped validation; say what is missing
        # rather than silently dropping a section the protocol requires.
        return (
            "### 再次審查\n\n"
            "> 這份報告缺少 rereview 區塊，Q1 / Q2 未作答。\n"
        )

    answers = (
        (RE_REVIEW_QUESTIONS[0], _as_text(block.get("q1_new_evidence"))),
        (RE_REVIEW_QUESTIONS[1], _as_text(block.get("q2_new_paths"))),
    )
    blocks = [
        f"**{marker} — {gloss}**\n\n{answer.strip()}"
        for (marker, gloss), answer in answers
        if answer.strip()
    ]
    if not blocks:
        return ""
    return "### 再次審查\n\n" + "\n\n".join(blocks) + "\n"


def build_intent_section(intent_check: dict[str, Any]) -> str:
    """Rendered only when at least one Phase 0.5 answer was a doubt."""
    doubts: list[str] = []
    for key, question in INTENT_QUESTIONS:
        answer = intent_check.get(key)
        if not isinstance(answer, dict):
            continue
        if _as_text(answer.get("verdict")) != "doubt":
            continue
        note = _as_text(answer.get("note")) or "（未附說明）"
        doubts.append(f"- **{question}**：{note}")
    if not doubts:
        return ""
    return (
        "### 意圖確認\n\n"
        "以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：\n\n"
        + "\n".join(doubts)
        + "\n"
    )


def build_scan_table(scans: list[Any]) -> str:
    """Always rendered: disclosing what did not run is part of the report.

    ``artifact`` is deliberately not rendered -- it points at a file on the
    review machine, which rule 2 keeps out of the published text.
    """
    if not scans:
        return "本輪沒有任何掃描紀錄。"

    rows = ["| 工具 | 狀態 | 說明 |", "|---|---|---|"]
    for index, raw in enumerate(scans):
        scan = _as_dict(raw, f"scans[{index}]")
        tool = _as_text(scan.get("tool")) or "（未命名工具）"
        status = _as_text(scan.get("status"))
        label = SCAN_STATUS_LABELS.get(status, status or "（未知）")

        details: list[str] = []
        # report_model requires a reason whenever the status is skipped or error;
        # it is the whole point of this table, so it leads the cell.
        reason = _as_text(scan.get("skipped_reason"))
        if reason:
            details.append(reason)
        exit_code = scan.get("exit_code")
        if isinstance(exit_code, int) and status != "ok":
            details.append(f"exit code {exit_code}")
        counts = scan.get("counts")
        if isinstance(counts, dict) and counts:
            details.append("、".join(f"{k} {v}" for k, v in counts.items()))
        if not details:
            details.append("—")
        rows.append(f"| {tool} | {label} | {' · '.join(details)} |")
    return "\n".join(rows)


def _security_block(security: dict[str, Any]) -> list[str]:
    """POC / blast radius / treatment -- the payload a Critical C finding owes."""
    lines: list[str] = []
    poc = _as_text(security.get("poc"))
    if poc:
        lines.append("**POC**：\n\n" + _fence(poc))
    blast = _as_text(security.get("blast_radius"))
    if blast:
        lines.append(f"**影響範圍**：{blast}")
    treatment = _as_text(security.get("treatment"))
    if treatment:
        gloss = TREATMENT_GLOSS.get(treatment)
        suffix = f"（{gloss}）" if gloss else ""
        lines.append(f"**風險處置**：{treatment}{suffix}")
    reference = _as_text(security.get("fix_reference"))
    if reference:
        lines.append(f"**修復參考**：{reference}")
    return lines


def build_finding_block(finding: dict[str, Any]) -> str:
    """One finding, fully expanded: heading, rationale, evidence, fix."""
    finding_id = _required_text(finding, "id", "findings[]")
    title = _required_text(finding, "title", f"finding {finding_id}")
    evidence = [_as_text(item) for item in _as_list(finding.get("evidence"), "evidence")]
    evidence = [item for item in evidence if item]
    if not evidence:
        raise ReportError(f"finding {finding_id} 沒有任何 evidence，無法發佈")

    dimension = _as_text(finding.get("dimension"))
    dimension_title = DIMENSION_TITLES.get(dimension, "")
    severity = _as_text(finding.get("severity"))
    status = _as_text(finding.get("status")) or "new"

    parts: list[str] = [f"#### {finding_id} {title} — {_code(evidence[0])}"]

    accepted_risk = _as_text(finding.get("accepted_risk"))
    if accepted_risk:
        # An accepted risk stays visible but is not a blocker; say so where the
        # reader cannot miss it, above the rationale.
        parts.append(
            f"> **已接受風險**：{accepted_risk}\n"
            "> 這一項仍然列出以保持可見，但不作為阻擋合併的理由。"
        )

    meta_bits = [f"面向 {dimension} {dimension_title}".strip(), severity]
    if status == "reconfirmed":
        meta_bits.append(STATUS_LABELS[status])
    parts.append(" · ".join(bit for bit in meta_bits if bit))

    rationale = _as_text(finding.get("rationale"))
    if rationale:
        parts.append(f"**問題**：{rationale}")

    parts.append("**證據**：\n" + "\n".join(f"- {_code(item)}" for item in evidence))

    security = finding.get("security")
    if isinstance(security, dict) and security:
        parts.extend(_security_block(security))

    fix = _as_text(finding.get("fix"))
    if fix:
        parts.append(f"**修復方向**：{fix}")

    return "\n\n".join(parts)


def build_critical_section(criticals: list[dict[str, Any]]) -> str:
    if not criticals:
        return ""
    blocks = [build_finding_block(finding) for finding in criticals]
    return "### Critical\n\n" + "\n\n".join(blocks) + "\n"


def build_open_question_block(question: dict[str, Any]) -> str:
    question_id = _required_text(question, "id", "open_questions[]")
    text = _required_text(question, "question", f"open_question {question_id}")
    parts = [f"#### {question_id} {text}"]

    dimension = _as_text(question.get("dimension"))
    if dimension:
        parts.append(f"面向 {dimension} {DIMENSION_TITLES.get(dimension, '')}".strip())

    context = _as_text(question.get("context"))
    if context:
        parts.append(f"**背景**：{context}")
    settle = _as_text(question.get("what_would_settle_it"))
    if settle:
        parts.append(f"**如何確認**：{settle}")
    return "\n\n".join(parts)


def build_history_block(
    findings: list[dict[str, Any]], pushback: list[Any]
) -> str:
    """resolved / withdrawn findings: history, never counted as live."""
    responses: dict[str, str] = {}
    for index, raw in enumerate(pushback):
        entry = _as_dict(raw, f"pushback[{index}]")
        finding_id = _as_text(entry.get("finding_id"))
        response = _as_text(entry.get("response"))
        if finding_id and response:
            responses.setdefault(finding_id, response)

    lines: list[str] = []
    for finding in findings:
        finding_id = _required_text(finding, "id", "findings[]")
        title = _required_text(finding, "title", f"finding {finding_id}")
        status = _as_text(finding.get("status"))
        label = STATUS_LABELS.get(status, status)
        severity = _as_text(finding.get("severity"))
        lines.append(f"- **{finding_id}** {title}（{severity} · {label}）")
        response = responses.get(finding_id)
        if response:
            lines.append(f"  - 討論結果：{response}")
    return "\n".join(lines)


def _details(label: str, count: int, body: str) -> str:
    return f"<details>\n<summary>{label}（{count}）</summary>\n\n{body}\n\n</details>"


def build_collapsed_sections(
    by_severity: dict[str, list[dict[str, Any]]],
    open_questions: list[dict[str, Any]],
    history: list[dict[str, Any]],
    pushback: list[Any],
) -> str:
    """Suggestion / Nit / 未驗證提問 / 已解決, each folded away and counted.

    A block with no items is omitted rather than rendered empty.
    """
    blocks: list[str] = []

    for severity in ("Suggestion", "Nit"):
        items = by_severity.get(severity, [])
        if not items:
            continue
        body = "\n\n".join(build_finding_block(finding) for finding in items)
        blocks.append(_details(severity, len(items), body))

    if open_questions:
        body = "\n\n".join(build_open_question_block(q) for q in open_questions)
        blocks.append(_details("未驗證提問", len(open_questions), body))

    if history:
        blocks.append(_details("已解決", len(history), build_history_block(history, pushback)))

    return "\n\n".join(blocks)


# --------------------------------------------------------------------------
# Self-contained check
# --------------------------------------------------------------------------


def _walk_strings(value: Any, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Yield (label, text) for every string in the report, for attribution."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            name = index
            if isinstance(item, dict):
                # Prefer the human-facing identifier over the list index.
                name = item.get("id") or item.get("tool") or index
            yield from _walk_strings(item, f"{prefix}[{name}]")
    elif isinstance(value, str):
        yield prefix, value


def find_local_paths(markdown: str, report: dict[str, Any]) -> list[Violation]:
    """Scan the rendered Markdown for paths that only exist on this machine.

    The rendered text is what gets posted publicly, so it is what is scanned;
    the report JSON is only consulted afterwards, to name the finding the
    offending path came from.
    """
    hits: list[str] = []
    for pattern in LOCAL_PATH_PATTERNS:
        for match in pattern.finditer(markdown):
            found = match.group(0)
            if found not in hits:
                hits.append(found)
    if not hits:
        return []

    sources = list(_walk_strings(report))
    violations: list[Violation] = []
    for found in hits:
        locations = [label for label, text in sources if found in text]
        location = locations[0] if locations else "（無法對應到報告欄位）"
        violations.append(Violation(location=location, path=found))
    return violations


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _collapse_blank_lines(text: str) -> str:
    """Optional sections are injected as empty strings; tidy up after them."""
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n") + "\n"


def render(report: dict[str, Any], template: str) -> str:
    meta = _as_dict(report.get("meta"), "meta")
    dimensions = _as_dict(report.get("dimensions"), "dimensions")
    intent_check = _as_dict(report.get("intent_check"), "intent_check")
    scans = _as_list(report.get("scans"), "scans")
    pushback = _as_list(report.get("pushback"), "pushback")

    raw_findings = _as_list(report.get("findings"), "findings")
    findings = [
        _as_dict(item, f"findings[{index}]") for index, item in enumerate(raw_findings)
    ]
    raw_questions = _as_list(report.get("open_questions"), "open_questions")
    open_questions = [
        _as_dict(item, f"open_questions[{index}]")
        for index, item in enumerate(raw_questions)
    ]

    round_number = meta.get("round")
    if not isinstance(round_number, int) or round_number < 1:
        raise ReportError("meta.round 缺少或不是有效的輪次")

    # A finding with no explicit status is "new" (report_model.Finding default).
    live = [
        f for f in findings if (_as_text(f.get("status")) or "new") in LIVE_STATUSES
    ]
    history = [
        f for f in findings if (_as_text(f.get("status")) or "new") in HISTORY_STATUSES
    ]

    by_severity: dict[str, list[dict[str, Any]]] = {name: [] for name in SEVERITY_ORDER}
    for finding in live:
        severity = _as_text(finding.get("severity"))
        by_severity.setdefault(severity, []).append(finding)

    values = SafeDict(
        conclusion=_required_text(report, "conclusion", "報告"),
        critical_count=len(by_severity.get("Critical", [])),
        suggestion_count=len(by_severity.get("Suggestion", [])),
        nit_count=len(by_severity.get("Nit", [])),
        open_question_count=len(open_questions),
        skill_version=_required_text(meta, "skill_version", "meta"),
        round=round_number,
        dimension_grid=build_dimension_grid(dimensions),
        dimension_notes=build_dimension_notes(dimensions),
        rereview_section=build_rereview_section(report, round_number),
        intent_section=build_intent_section(intent_check),
        scan_table=build_scan_table(scans),
        critical_section=build_critical_section(by_severity.get("Critical", [])),
        collapsed_sections=build_collapsed_sections(
            by_severity, open_questions, history, pushback
        ),
    )
    return _collapse_blank_lines(template.format_map(values))


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ReportError("報告的最外層必須是 JSON 物件")
    return data


def load_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="render_report.py",
        description="Render a nathan-code-review report JSON into publishable Markdown.",
    )
    parser.add_argument("report", help="path to the report JSON")
    parser.add_argument(
        "--out",
        default=None,
        help="write the Markdown here instead of printing it to stdout",
    )
    parser.add_argument(
        "--template",
        default=str(TEMPLATE_PATH),
        help="override the frame template (defaults to assets/report_template.md)",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    template_path = Path(args.template)

    try:
        template = load_template(template_path)
    except OSError as exc:
        print(f"讀不到樣板檔：{template_path.as_posix()}（{exc.strerror}）", file=sys.stderr)
        return 2

    try:
        report = load_report(report_path)
    except FileNotFoundError:
        print(f"報告檔案不存在：{report_path.as_posix()}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"讀不到報告檔：{report_path.as_posix()}（{exc.strerror}）", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"報告不是合法的 JSON：{exc}", file=sys.stderr)
        return 1
    except ReportError as exc:
        print(f"報告結構無法轉成 Markdown：{exc}", file=sys.stderr)
        return 1

    try:
        markdown = render(report, template)
    except ReportError as exc:
        print(f"報告結構無法轉成 Markdown：{exc}", file=sys.stderr)
        return 1

    violations = find_local_paths(markdown, report)
    if violations:
        # Nothing is written when this fires: a report that leaks the review
        # machine's filesystem is not fit to publish, and half-writing it would
        # invite someone to paste it anyway.
        print(
            "報告含有審查機器上的本機路徑，不能發佈（見 report-format.md 規則 2：報告必須自足）：",
            file=sys.stderr,
        )
        for violation in violations:
            print(f"  - {violation.location}：{violation.path}", file=sys.stderr)
        print(
            "請改成 repository 相對路徑（例如 app/api/account.py:34），或直接刪掉該路徑後重新產生。",
            file=sys.stderr,
        )
        return 1

    if args.out:
        out_path = Path(args.out)
        try:
            out_path.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            print(f"寫不進輸出檔：{out_path.as_posix()}（{exc.strerror}）", file=sys.stderr)
            return 2
        print(f"已輸出 Markdown：{out_path.as_posix()}", file=sys.stderr)
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
