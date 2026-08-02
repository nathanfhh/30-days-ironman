"""Tests for skills/nathan-code-review/scripts/render_report.py.

The rendered Markdown is what gets posted onto a merge request, so the two
things worth pinning down are what it must never contain — a path that exists
only on the review machine — and that the parts a reader acts on first
(conclusion, grid, Criticals) survive rendering intact.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def find_local_paths(render_report):
    return render_report.find_local_paths


class TestLocalPathDetection:
    """Rule 2 of report-format.md: the report may cite only what a reader can reach."""

    @pytest.mark.parametrize(
        "path",
        [
            "/tmp/ncr/his2-repo-mr92/repo",
            "/home/nathan/ncr/report.json",
            "/Users/nathan/work/repo/app.py",
            "/var/folders/x1/tmpabc/report.md",
            "~/ncr/his2/repo",
            "$HOME/ncr/report.json",
            r"C:\work\repo\app.py",
            "D:/work/repo/app.py",
        ],
    )
    def test_catches_machine_local_paths(self, find_local_paths, path):
        violations = find_local_paths(f"報告內容提到 {path} 這個位置", {})
        assert [v.path for v in violations], f"未攔截：{path}"

    @pytest.mark.parametrize(
        "text",
        [
            "`app/api/account.py:34`",
            "app/logic/process.py 第 99 行",
            "https://gitlab.example.com/grp/repo/-/merge_requests/92",
            "見 config.yaml.sample:45",
            "test/test_anesthesia.py",
            "套件註冊表路徑含有 /packages/ 片段",
        ],
        ids=["repo-relative", "prose-path", "url", "sample", "test-path", "bare-segment"],
    )
    def test_leaves_legitimate_references_alone(self, find_local_paths, text):
        assert find_local_paths(text, {}) == []

    def test_reports_each_distinct_path_once(self, find_local_paths):
        markdown = "/tmp/a/b 出現兩次：/tmp/a/b，另外還有 /tmp/c/d"
        assert sorted(v.path for v in find_local_paths(markdown, {})) == ["/tmp/a/b", "/tmp/c/d"]

    def test_names_the_report_field_a_path_came_from(self, find_local_paths):
        report = {"findings": [{"id": "F-001", "fix": "改成 /tmp/ncr/x/y 底下的檔案"}]}
        [violation] = find_local_paths("改成 /tmp/ncr/x/y 底下的檔案", report)
        assert violation.path == "/tmp/ncr/x/y"
        assert "findings" in violation.location

    def test_quoted_values_containing_slashes_are_a_known_false_positive(self, find_local_paths):
        """Documented, not fixed, and deliberately so.

        Prose like `覆蓋 "1"/2/None/"9"` trips the POSIX-path pattern. Tightening
        the lookbehind to exclude quotes would let a genuinely quoted "/tmp/x/y"
        through, and this is a pre-publication gate: a false positive costs one
        rewrite, a false negative leaks a reviewer's filesystem to the whole
        merge request. The asymmetry is the reason the pattern stays greedy.
        """
        assert find_local_paths('覆蓋 "1"/2/None/"9" 四個案例', {}) != []


class TestRendering:
    @pytest.fixture
    def report(self):
        return {
            "meta": {
                "skill_version": "2026.08.02.02",
                "reviewed_at": "2026-08-02T19:41:43+0800",
                "round": 1,
                "mode": "local_branch",
                "target": "feature/x",
                "phi_trigger": {"triggered": False},
            },
            "intent_check": {
                "should_do": {"verdict": "ok"},
                "right_mr": {"verdict": "ok"},
                "right_timing": {"verdict": "ok"},
            },
            "dimensions": {
                **{d: {"verdict": "pass"} for d in "ABCDEFGI"},
                "H": {"verdict": "na", "note": "diff 內沒有非 Python 檔"},
            },
            "findings": [
                {
                    "id": "F-001",
                    "dimension": "C",
                    "severity": "Critical",
                    "status": "new",
                    "title": "未參數化的 SQL",
                    "evidence": ["app/api/account.py:34"],
                    "rationale": "字串拼接進 WHERE 子句",
                    "fix": "改用 bind parameter",
                    "source": "dimension",
                    "security": {
                        "poc": "curl 'https://host/a?id=1%27+OR+%271%27%3D%271'",
                        "blast_radius": "可讀取全院病人的病歷號",
                        "treatment": "Mitigate",
                    },
                },
                {
                    "id": "F-002",
                    "dimension": "A",
                    "severity": "Nit",
                    "status": "new",
                    "title": "命名可以更精確",
                    "evidence": ["app/x.py:1"],
                    "rationale": "理由",
                    "fix": "改個名字",
                    "source": "dimension",
                },
            ],
            "open_questions": [],
            "conclusion": "Request Changes",
        }

    @pytest.fixture
    def markdown(self, render_report, report):
        from conftest import SCRIPTS

        template = (SCRIPTS.parent / "assets" / "report_template.md").read_text(encoding="utf-8")
        return render_report.render(report, template)

    def test_the_conclusion_leads(self, markdown):
        assert markdown.lstrip().startswith("## 審查結論：Request Changes")

    def test_the_counts_are_summarised(self, markdown):
        assert "Critical 1" in markdown and "Nit 1" in markdown

    def test_a_critical_is_never_folded_away(self, markdown):
        """Blockers have to be visible without expanding anything."""
        head = markdown.split("<details>")[0]
        assert "未參數化的 SQL" in head

    def test_a_nit_is_folded(self, markdown):
        assert "<details>" in markdown
        assert "命名可以更精確" not in markdown.split("<details>")[0]

    def test_a_critical_security_finding_shows_its_payload(self, markdown):
        assert "curl" in markdown
        assert "可讀取全院病人的病歷號" in markdown

    def test_an_na_dimension_renders_as_a_dash_with_its_reason(self, markdown):
        assert "—" in markdown
        assert "diff 內沒有非 Python 檔" in markdown

    def test_the_skill_version_is_recorded(self, markdown):
        assert "2026.08.02.02" in markdown
