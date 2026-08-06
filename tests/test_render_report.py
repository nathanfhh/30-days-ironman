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
            "覆蓋 \"1\"/2/None/\"9\" 四個案例",
        ],
        ids=[
            "repo-relative",
            "prose-path",
            "url",
            "sample",
            "test-path",
            "bare-segment",
            "quoted-values",
        ],
    )
    def test_leaves_legitimate_references_alone(self, find_local_paths, text):
        assert find_local_paths(text, {}) == []

    @pytest.mark.parametrize(
        "text",
        [
            "curl -X POST 'https://host/api/v1/orders?id=1' -H 'X-Token: x'",
            "路由應為 /user-profile/list，而不是 /userProfile/list",
            "設定檔 /etc/nginx/nginx.conf 沒有設定 client_max_body_size",
            "掛載點 /var/lib/postgresql/data 沒有持久化",
            "前端路由 /home/dashboard 沒有 auth guard",
        ],
        ids=["api-route", "url-fix", "etc-config", "container-mount", "app-route"],
    )
    def test_paths_belonging_to_the_reviewed_system_are_not_blocked(
        self, find_local_paths, text
    ):
        """The reproduced bug: the gate rejected evidence this skill asks for.

        An API route in a POC and a URL fix are paths inside the system under
        review, not on the review machine. Blocking them stopped a correct
        report from being published at all — a far worse outcome than the
        prose false positives the old greedy pattern was tuned to catch.
        """
        assert find_local_paths(text, {}) == []

    def test_reports_each_distinct_path_once(self, find_local_paths):
        markdown = "/tmp/a/b 出現兩次：/tmp/a/b，另外還有 /tmp/c/d"
        assert sorted(v.path for v in find_local_paths(markdown, {})) == ["/tmp/a/b", "/tmp/c/d"]

    def test_names_the_report_field_a_path_came_from(self, find_local_paths):
        report = {"findings": [{"id": "F-001", "fix": "改成 /tmp/ncr/x/y 底下的檔案"}]}
        [violation] = find_local_paths("改成 /tmp/ncr/x/y 底下的檔案", report)
        assert violation.path == "/tmp/ncr/x/y"
        assert "findings" in violation.location

    def test_a_quoted_local_path_is_still_caught(self, find_local_paths):
        """Narrowing the pattern must not open a hole for the quoted form."""
        assert find_local_paths('報告寫著 "/tmp/ncr/x/y" 這個位置', {}) != []


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
        # Pin the grid row itself (G pass / H na / I pass). Asserting a bare "—"
        # proves nothing: every finding heading carries an em dash already.
        assert "| ✅ | — | ✅ |" in markdown
        assert "diff 內沒有非 Python 檔" in markdown

    def test_the_skill_version_is_recorded(self, markdown):
        assert "2026.08.02.02" in markdown

    def test_a_poc_citing_an_api_route_survives_the_path_gate(self, render_report, report):
        """End to end: the C-1 report that could not be published at all."""
        from conftest import SCRIPTS

        report["findings"][0]["security"]["poc"] = (
            "curl -X POST 'https://host/api/v1/patients/1/export'"
        )
        template = (SCRIPTS.parent / "assets" / "report_template.md").read_text(encoding="utf-8")
        markdown = render_report.render(report, template)

        assert render_report.find_local_paths(markdown, report) == []
        assert "/api/v1/patients/1/export" in markdown

    def test_a_report_naming_the_review_machine_is_still_refused(
        self, render_report, report
    ):
        from conftest import SCRIPTS

        report["findings"][0]["fix"] = "見 /Users/nathan/ncr/scan.json 的完整輸出"
        template = (SCRIPTS.parent / "assets" / "report_template.md").read_text(encoding="utf-8")
        markdown = render_report.render(report, template)

        violations = render_report.find_local_paths(markdown, report)
        assert [v.path for v in violations] == ["/Users/nathan/ncr/scan.json"]


class TestSummary:
    """`summary` 有欄位卻沒被渲染過，等於報告寫了沒人看得到。"""

    @pytest.fixture
    def render(self, render_report):
        from conftest import SCRIPTS

        template = (SCRIPTS.parent / "assets" / "report_template.md").read_text(encoding="utf-8")
        return lambda report: render_report.render(report, template)

    @pytest.fixture
    def base(self):
        return {
            "meta": {
                "skill_version": "2026.08.06.01",
                "reviewed_at": "2026-08-06T10:00:00+0800",
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
            "dimensions": {d: {"verdict": "pass"} for d in "ABCDEFGHI"},
            "findings": [],
            "open_questions": [],
            "conclusion": "Approved",
        }

    def test_it_appears_in_the_published_markdown(self, render, base):
        base["summary"] = "整體結構清楚，只有錯誤處理的部分值得再看一次。"
        assert "整體結構清楚，只有錯誤處理的部分值得再看一次。" in render(base)

    def test_it_leads_the_overview_section(self, render, base):
        """Above the grid: the sentence frames the nine cells, not the reverse."""
        base["summary"] = "本次沒有阻擋合併的問題。"
        markdown = render(base)
        assert markdown.index("本次沒有阻擋合併的問題。") < markdown.index("| A 風格")

    def test_an_absent_summary_leaves_no_placeholder_behind(self, render, base):
        markdown = render(base)
        assert "{summary}" not in markdown
        assert "### 總評\n\n| A 風格" in markdown


class TestHistorySections:
    """已解決與已撤回是兩件事：一件作者修好了，一件審查自己收回。"""

    @pytest.fixture
    def render(self, render_report):
        from conftest import SCRIPTS

        template = (SCRIPTS.parent / "assets" / "report_template.md").read_text(encoding="utf-8")
        return lambda report: render_report.render(report, template)

    @pytest.fixture
    def report(self):
        def finding(finding_id, status):
            return {
                "id": finding_id,
                "dimension": "A",
                "severity": "Suggestion",
                "status": status,
                "title": f"{finding_id} 的標題",
                "evidence": ["app/x.py:1"],
                "rationale": "理由",
                "fix": "修法",
                "source": "dimension",
            }

        return {
            "meta": {
                "skill_version": "2026.08.06.01",
                "reviewed_at": "2026-08-06T10:00:00+0800",
                "round": 2,
                "mode": "local_branch",
                "target": "feature/x",
                "phi_trigger": {"triggered": False},
            },
            "intent_check": {
                "should_do": {"verdict": "ok"},
                "right_mr": {"verdict": "ok"},
                "right_timing": {"verdict": "ok"},
            },
            "rereview": {
                "q1_new_evidence": "無新證據",
                "q2_new_paths": "新的 commit 沒有暴露新的執行路徑",
            },
            "dimensions": {d: {"verdict": "pass"} for d in "ABCDEFGHI"},
            "findings": [finding("F-001", "resolved"), finding("F-002", "withdrawn")],
            "open_questions": [],
            "conclusion": "Approved",
        }

    def test_the_two_get_their_own_blocks(self, render, report):
        markdown = render(report)
        assert "<summary>已解決（1）</summary>" in markdown
        assert "<summary>已撤回（1）</summary>" in markdown

    def test_a_withdrawal_is_not_counted_as_a_repair(self, render, report):
        """The bug: both folded into 已解決, so every retraction read as a fix."""
        markdown = render(report)
        assert "<summary>已解決（2）</summary>" not in markdown
        resolved = markdown.split("<summary>已解決（1）</summary>")[1].split("</details>")[0]
        assert "F-001" in resolved
        assert "F-002" not in resolved

    def test_a_block_with_nothing_in_it_is_omitted(self, render, report):
        report["findings"] = [report["findings"][0]]
        markdown = render(report)
        assert "已解決" in markdown
        assert "已撤回" not in markdown


class TestProcessDirectedTextSection:
    @pytest.fixture
    def render(self, render_report):
        from conftest import SCRIPTS

        template = (SCRIPTS.parent / "assets" / "report_template.md").read_text(
            encoding="utf-8"
        )
        return lambda report: render_report.render(report, template)

    @pytest.fixture
    def base(self):
        return {
            "meta": {
                "skill_version": "2026.08.02.05",
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
            "dimensions": {d: {"verdict": "pass"} for d in "ABCDEFGHI"},
            "findings": [],
            "open_questions": [],
            "conclusion": "Approved",
        }

    def test_nothing_is_rendered_when_none_was_found(self, render, base):
        assert "指向審查流程" not in render(base)

    def test_the_section_names_every_location(self, render, base):
        base["meta"]["process_directed_text"] = {
            "detected": True,
            "evidence": ["app/api/guest_export.py:12", "app/api/guest_export.py:19"],
            "note": "說明欄與註解皆要求略過資安面向。",
        }
        markdown = render(base)
        assert "app/api/guest_export.py:12" in markdown
        assert "app/api/guest_export.py:19" in markdown
        assert "說明欄與註解皆要求略過資安面向。" in markdown

    def test_it_is_not_folded_away(self, render, base):
        """A reader judging how far to trust the report must see it unprompted."""
        base["meta"]["process_directed_text"] = {
            "detected": True,
            "evidence": ["app/api/guest_export.py:12"],
        }
        markdown = render(base)
        assert "指向審查流程" in markdown.split("<details>")[0]

    def test_it_states_that_the_review_was_unchanged(self, render, base):
        """Listing the attempt without that sentence reads as a concession."""
        base["meta"]["process_directed_text"] = {
            "detected": True,
            "evidence": ["a/b.py:1"],
        }
        assert "未改變" in render(base)
