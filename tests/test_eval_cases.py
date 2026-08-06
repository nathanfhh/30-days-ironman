"""Schema checks for the behavioural eval cases.

An eval case is only worth what its checks are anchored to. A case that names a
skill file which has since been renamed, or a check with no anchor into the
rules, fails quietly at eval time — the judge marks it `fail`, someone assumes
the skill regressed, and the real cause is a stale test. These run in the normal
suite so that breakage surfaces as a red test rather than as a confusing eval.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CASE_DIR = REPO_ROOT / "tests" / "nathan-code-review"
CASE_FILES = sorted(CASE_DIR.glob("*.yaml"))

REQUIRED_TOP_LEVEL = {
    "name",
    "description",
    "skill_files",
    "conversations",
    "behavioral_checks",
    "anti_checks",
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_there_are_cases_to_run():
    """A silently empty case directory would make `跑 eval` report success."""
    assert CASE_FILES, f"{CASE_DIR} 底下沒有任何 eval case"


@pytest.mark.parametrize("path", CASE_FILES, ids=lambda p: p.stem)
class TestCaseShape:
    def test_has_every_required_field(self, path):
        case = _load(path)
        assert REQUIRED_TOP_LEVEL <= set(case), (
            f"缺少欄位：{sorted(REQUIRED_TOP_LEVEL - set(case))}"
        )

    def test_name_matches_the_filename(self, path):
        """The judge reports by name; a mismatch makes the summary unmappable."""
        assert "-" in path.stem, (
            f"{path.name} 檔名要是 <序號>-<name>.yaml，缺少 '-' 就對不上 case 名稱"
        )
        assert _load(path)["name"] == path.stem.split("-", 1)[1]

    def test_every_skill_file_exists(self, path):
        for rel in _load(path)["skill_files"]:
            assert (REPO_ROOT / rel).is_file(), f"{path.name} 指向不存在的檔案：{rel}"

    def test_conversations_are_well_formed(self, path):
        turns = _load(path)["conversations"]
        assert turns, "golden conversation 不能是空的"
        assert turns[0]["role"] == "user", "對話要從 user 開始"
        for turn in turns:
            assert turn["role"] in {"user", "assistant"}
            assert turn.get("content", "").strip(), "每一輪都要有內容"

    @pytest.mark.parametrize("kind", ["behavioral_checks", "anti_checks"])
    def test_checks_are_populated_and_identified(self, path, kind):
        checks = _load(path)[kind]
        assert checks, f"{kind} 不能是空的"
        ids = [c["id"] for c in checks]
        assert len(ids) == len(set(ids)), f"{kind} 的 id 重複：{ids}"
        for check in checks:
            assert check["check"].strip(), f"{check['id']} 沒有敘述"

    def test_behavioral_checks_are_anchored_to_a_rule(self, path):
        """An unanchored check measures the judge's taste, not the skill."""
        for check in _load(path)["behavioral_checks"]:
            assert check.get("anchor", "").strip(), (
                f"{check['id']} 沒有 anchor，無法判斷它在驗證 skill 的哪一條規則"
            )

    def test_anchors_only_cite_files_the_judge_was_given(self, path):
        """An anchor pointing outside skill_files is an unanswerable check.

        The judge may only read the files the case lists, so a check anchored
        to a document it was never handed can only be marked fail — and the
        report then reads as a skill regression when the real fault is the
        case. Both cases in the first eval run had exactly this defect.
        """
        case = _load(path)
        given = {Path(rel).name for rel in case["skill_files"]}
        for check in case["behavioral_checks"]:
            cited = re.findall(r"[\w./-]+\.(?:md|py)", check["anchor"])
            for name in {Path(c).name for c in cited}:
                assert name in given, (
                    f"{check['id']} 的 anchor 引用 {name}，但它不在 skill_files 裡——"
                    f"judge 讀不到，這條註定 fail"
                )

    def test_anti_checks_state_the_failure_they_catch(self, path):
        for check in _load(path)["anti_checks"]:
            assert check.get("failure_mode", "").strip(), (
                f"{check['id']} 沒有 failure_mode，讀者無從得知它在防什麼退化"
            )


def test_case_names_are_unique():
    names = [_load(p)["name"] for p in CASE_FILES]
    assert len(names) == len(set(names)), f"case 名稱重複：{names}"


def test_the_judge_prompt_is_present():
    """`跑 eval` dispatches subagents with this file as their prompt."""
    assert (CASE_DIR / "judge.md").is_file()


def test_scenario_identifiers_are_not_lifted_from_the_skill_docs():
    """A case whose answer is printed in the teaching material proves nothing.

    If a scenario reuses an identifier the skill itself uses as an example, a
    judge can match it from memory of the docs rather than from the rules, and
    the case stops discriminating between a skill that works and one that does
    not.
    """
    docs = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (REPO_ROOT / "skills" / "nathan-code-review").rglob("*")
        if p.is_file() and p.suffix in {".md", ".py"}
    )
    for path in CASE_FILES:
        case = _load(path)
        project = case.get("scenario", {}).get("project")
        if project:
            assert project not in docs, (
                f"{path.name} 的 scenario.project '{project}' 也出現在 skill 文件裡"
            )
