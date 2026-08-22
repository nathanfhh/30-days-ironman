"""Tests for skills/nathan-code-review/scripts/report_model.py.

This module is the one thing standing between a reviewing agent under pressure
and a report that says "Approved" underneath a Critical. Every invariant it
claims to enforce is asserted here from both sides: that a valid report passes,
and that the specific violation is actually rejected. An invariant that is only
tested from the happy side is not tested.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _finding(**overrides) -> dict:
    finding = {
        "id": "F-001",
        "dimension": "F",
        "severity": "Suggestion",
        "status": "new",
        "title": "標題",
        "evidence": ["app/api/account.py:34"],
        "rationale": "理由",
        "fix": "修復方向",
        "source": "dimension",
    }
    finding.update(overrides)
    return finding


def _report(**overrides) -> dict:
    report = {
        "meta": {
            "skill_version": "2026.08.02.01",
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
    report.update(overrides)
    return report


@pytest.fixture
def build(report_model):
    """Construct a report, letting pydantic raise on anything invalid."""
    return report_model.NcrReport.model_validate


# --------------------------------------------------------------------------
# The conclusion is derived, never chosen
# --------------------------------------------------------------------------


class TestConclusionIsMechanical:
    @pytest.mark.parametrize(
        ("severities", "conclusion"),
        [
            ([], "Approved"),
            (["Nit"], "Approved"),
            (["Nit", "Nit"], "Approved"),
            (["Suggestion"], "Approved with Comments"),
            (["Nit", "Suggestion"], "Approved with Comments"),
            (["Critical"], "Request Changes"),
            (["Nit", "Suggestion", "Critical"], "Request Changes"),
        ],
    )
    def test_the_matching_conclusion_is_accepted(self, build, severities, conclusion):
        findings = [
            _finding(id=f"F-{i:03d}", severity=s)
            for i, s in enumerate(severities, start=1)
        ]
        build(_report(findings=findings, conclusion=conclusion))

    @pytest.mark.parametrize(
        ("severities", "wrong_conclusion"),
        [
            (["Critical"], "Approved"),
            (["Critical"], "Approved with Comments"),
            (["Suggestion"], "Approved"),
            (["Suggestion"], "Request Changes"),
            ([], "Request Changes"),
            (["Nit"], "Approved with Comments"),
        ],
    )
    def test_a_mismatched_conclusion_is_rejected(
        self, build, severities, wrong_conclusion
    ):
        findings = [
            _finding(id=f"F-{i:03d}", severity=s)
            for i, s in enumerate(severities, start=1)
        ]
        with pytest.raises(ValidationError):
            build(_report(findings=findings, conclusion=wrong_conclusion))

    @pytest.mark.parametrize("status", ["resolved", "withdrawn"])
    def test_a_dead_critical_no_longer_blocks(self, build, status):
        """resolved/withdrawn findings stay in the report as history only."""
        build(
            _report(
                findings=[_finding(severity="Critical", status=status)],
                conclusion="Approved",
            )
        )

    @pytest.mark.parametrize("status", ["new", "reconfirmed"])
    def test_a_live_critical_still_blocks(self, build, status):
        with pytest.raises(ValidationError):
            build(
                _report(
                    findings=[_finding(severity="Critical", status=status)],
                    conclusion="Approved",
                )
            )

    def test_open_questions_never_move_the_conclusion(self, build):
        """That they carry no severity is the whole point of them."""
        build(
            _report(
                open_questions=[
                    {
                        "id": "Q-001",
                        "question": "這個欄位型別是什麼？",
                        "context": "兩支 repository 寫法不同",
                        "what_would_settle_it": "查 syscolumns",
                    }
                ],
                conclusion="Approved",
            )
        )

    def test_an_open_question_cannot_smuggle_in_a_severity(self, build):
        with pytest.raises(ValidationError):
            build(
                _report(
                    open_questions=[
                        {
                            "id": "Q-001",
                            "question": "問題",
                            "context": "背景",
                            "what_would_settle_it": "如何確認",
                            "severity": "Critical",
                        }
                    ]
                )
            )


# --------------------------------------------------------------------------
# A finding has to be actionable
# --------------------------------------------------------------------------


class TestFindingsMustBeActionable:
    @pytest.mark.parametrize("missing", ["fix", "rationale", "title"])
    def test_a_finding_missing_a_required_field_is_rejected(self, build, missing):
        finding = _finding()
        del finding[missing]
        with pytest.raises(ValidationError):
            build(_report(findings=[finding], conclusion="Approved with Comments"))

    @pytest.mark.parametrize("blank", ["", "   ", "\n"])
    def test_a_blank_fix_does_not_count_as_a_fix(self, build, blank):
        with pytest.raises(ValidationError):
            build(
                _report(
                    findings=[_finding(fix=blank)], conclusion="Approved with Comments"
                )
            )

    def test_a_finding_needs_at_least_one_piece_of_evidence(self, build):
        with pytest.raises(ValidationError):
            build(
                _report(
                    findings=[_finding(evidence=[])],
                    conclusion="Approved with Comments",
                )
            )

    def test_finding_ids_must_be_unique(self, build):
        findings = [_finding(id="F-001"), _finding(id="F-001", title="另一條")]
        with pytest.raises(ValidationError):
            build(_report(findings=findings, conclusion="Approved with Comments"))

    def test_open_question_ids_must_be_unique(self, build):
        question = {
            "id": "Q-001",
            "question": "問題",
            "context": "背景",
            "what_would_settle_it": "如何確認",
        }
        with pytest.raises(ValidationError):
            build(_report(open_questions=[question, dict(question)]))


# --------------------------------------------------------------------------
# A Critical security finding carries its payload
# --------------------------------------------------------------------------


SECURITY = {
    "poc": "curl -X POST https://host/api/x -d \"id=1' OR '1'='1\"",
    "blast_radius": "可讀取全院病人的病歷號與身分證字號",
    "treatment": "Mitigate",
}


class TestCriticalSecurityPayload:
    def test_a_critical_security_finding_without_a_payload_is_rejected(self, build):
        with pytest.raises(ValidationError):
            build(
                _report(
                    findings=[_finding(dimension="C", severity="Critical")],
                    conclusion="Request Changes",
                )
            )

    def test_a_critical_security_finding_with_a_payload_is_accepted(self, build):
        build(
            _report(
                findings=[
                    _finding(dimension="C", severity="Critical", security=SECURITY)
                ],
                conclusion="Request Changes",
            )
        )

    @pytest.mark.parametrize("missing", ["poc", "blast_radius", "treatment"])
    def test_each_part_of_the_payload_is_required(self, build, missing):
        security = dict(SECURITY)
        del security[missing]
        with pytest.raises(ValidationError):
            build(
                _report(
                    findings=[
                        _finding(dimension="C", severity="Critical", security=security)
                    ],
                    conclusion="Request Changes",
                )
            )

    def test_the_payload_is_only_demanded_of_critical_security(self, build):
        """A Critical elsewhere, or a Suggestion in C, needs no POC."""
        build(
            _report(
                findings=[_finding(dimension="F", severity="Critical")],
                conclusion="Request Changes",
            )
        )
        build(
            _report(
                findings=[_finding(dimension="C", severity="Suggestion")],
                conclusion="Approved with Comments",
            )
        )


# --------------------------------------------------------------------------
# Dimensions, PHI, re-review
# --------------------------------------------------------------------------


class TestDimensions:
    def test_every_dimension_needs_a_verdict(self, build):
        dimensions = {d: {"verdict": "pass"} for d in "ABCDEFGH"}  # I missing
        with pytest.raises(ValidationError):
            build(_report(dimensions=dimensions))

    def test_na_must_say_why(self, build):
        dimensions = {d: {"verdict": "pass"} for d in "ABCDEFGHI"}
        dimensions["H"] = {"verdict": "na"}
        with pytest.raises(ValidationError):
            build(_report(dimensions=dimensions))

    def test_na_with_a_note_is_fine(self, build):
        dimensions = {d: {"verdict": "pass"} for d in "ABCDEFGHI"}
        dimensions["H"] = {"verdict": "na", "note": "diff 內沒有非 Python 檔"}
        build(_report(dimensions=dimensions))


class TestPhiTrigger:
    def test_a_triggered_phi_flag_must_cite_evidence(self, build):
        meta = _report()["meta"] | {"phi_trigger": {"triggered": True, "evidence": []}}
        with pytest.raises(ValidationError):
            build(_report(meta=meta))

    def test_a_triggered_phi_flag_with_evidence_is_accepted(self, build):
        meta = _report()["meta"] | {
            "phi_trigger": {
                "triggered": True,
                "evidence": ["app/schema/patient.py:7"],
                "note": "輸出含身分證字號",
            }
        }
        build(_report(meta=meta))


class TestProcessDirectedText:
    """Text aimed at the review process has to be named, not just resisted."""

    def test_absent_is_valid_and_defaults_to_not_detected(self, build, report_model):
        report = build(_report())
        assert report.meta.process_directed_text.detected is False

    def test_detected_requires_a_locatable_reference(self, build):
        meta = _report()["meta"] | {
            "process_directed_text": {"detected": True, "evidence": []}
        }
        with pytest.raises(ValidationError):
            build(_report(meta=meta))

    def test_detected_with_evidence_is_accepted(self, build):
        meta = _report()["meta"] | {
            "process_directed_text": {
                "detected": True,
                "evidence": ["app/api/guest_export.py:12"],
                "note": "註解要求略過資安面向",
            }
        }
        build(_report(meta=meta))

    def test_recording_it_does_not_move_the_conclusion(self, build):
        """It is a disclosure, not a finding — it must not act like one."""
        meta = _report()["meta"] | {
            "process_directed_text": {"detected": True, "evidence": ["a/b.py:1"]}
        }
        build(_report(meta=meta, conclusion="Approved"))


class TestScans:
    @pytest.mark.parametrize("status", ["skipped", "error"])
    def test_a_scan_that_did_not_run_must_state_why(self, build, status):
        with pytest.raises(ValidationError):
            build(_report(scans=[{"tool": "trivy", "status": status}]))

    @pytest.mark.parametrize("status", ["skipped", "error"])
    def test_a_reason_satisfies_it(self, build, status):
        build(
            _report(
                scans=[{"tool": "trivy", "status": status, "skipped_reason": "未安裝"}]
            )
        )

    def test_an_ok_scan_needs_no_reason(self, build):
        build(_report(scans=[{"tool": "ruff", "status": "ok", "exit_code": 0}]))


class TestReReview:
    def test_round_two_requires_the_blind_pass_flag(self, build):
        meta = _report()["meta"] | {"round": 2, "blind_pass": False}
        with pytest.raises(ValidationError):
            build(_report(meta=meta))

    def test_round_two_requires_the_rereview_block(self, build):
        meta = _report()["meta"] | {"round": 2, "blind_pass": True}
        with pytest.raises(ValidationError):
            build(_report(meta=meta))

    def test_round_two_with_both_is_accepted(self, build):
        meta = _report()["meta"] | {"round": 2, "blind_pass": True}
        build(
            _report(
                meta=meta,
                rereview={
                    "q1_new_evidence": "附件已重新下載核對",
                    "q2_new_paths": "新增 Redis 標記讀寫一條路徑",
                },
            )
        )


class TestModeInvariants:
    def test_mr_mode_requires_the_mr_block(self, build):
        meta = _report()["meta"] | {"mode": "mr"}
        with pytest.raises(ValidationError):
            build(_report(meta=meta))

    def test_unknown_fields_are_rejected(self, build):
        """extra="forbid" is what stops a typo'd key from being silently ignored."""
        with pytest.raises(ValidationError):
            build(_report(conclusionn="Approved"))


class TestIntentDoubtNeedsNote:
    """A 'doubt' verdict with no note is a flag without a reason — rejected."""

    def test_doubt_without_a_note_is_rejected(self, build):
        intent = _report()["intent_check"] | {"should_do": {"verdict": "doubt"}}
        with pytest.raises(ValidationError):
            build(_report(intent_check=intent))

    def test_doubt_with_a_note_is_accepted(self, build):
        intent = _report()["intent_check"] | {
            "should_do": {
                "verdict": "doubt",
                "note": "退款邏輯是否該進這個 MR 待作者說明",
            }
        }
        build(_report(intent_check=intent))


class TestPushbackTargetsKnownFindings:
    """pushback referencing a finding id that does not exist is rejected."""

    @staticmethod
    def _pushback(finding_id: str) -> dict:
        return {
            "finding_id": finding_id,
            "author_quote": "這個我不同意，因為內網用不到",
            "review": {
                "new_information": "無新事證",
                "author_proximity": "作者較熟部署環境",
                "grounded_in_evidence": "程式碼層面的疑慮仍在",
                "rule_or_preference": "規則",
                "rule_versus_context": "內網不豁免授權檢查",
                "knock_on_effects": "撤回會使同類缺陷失去先例",
            },
            "outcome": "hold",
            "response": "維持原判定，理由如上",
            "appended_at": "2026-08-06T12:00:00+08:00",
        }

    def test_pushback_on_an_unknown_id_is_rejected(self, build):
        findings = [_finding(id="F-001", severity="Suggestion")]
        with pytest.raises(ValidationError, match="unknown finding"):
            build(
                _report(
                    findings=findings,
                    conclusion="Approved with Comments",
                    pushback=[self._pushback("F-999")],
                )
            )

    def test_pushback_on_a_known_id_is_accepted(self, build):
        findings = [_finding(id="F-001", severity="Suggestion")]
        build(
            _report(
                findings=findings,
                conclusion="Approved with Comments",
                pushback=[self._pushback("F-001")],
            )
        )
