"""Tests for skills/nathan-code-review/scripts/scan_runner.py.

The linters are replaced with fake executables placed ahead of everything on
PATH. That keeps the suite offline and independent of whether ruff/ty/oxlint
happen to be installed, and — more usefully — lets a test produce the outputs
that actually caused trouble: oxlint's non-JSON "nothing to lint" line, and a
ty run in which every third-party import is unresolvable.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest


@pytest.fixture
def fake_tool(tmp_path, monkeypatch):
    """Install a fake executable that prints fixed output and exits with a code."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def install(
        name: str, *, stdout: str = "", stderr: str = "", exit_code: int = 0
    ) -> Path:
        script = bin_dir / name
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"sys.stdout.write({stdout!r})\n"
            f"sys.stderr.write({stderr!r})\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return script

    return install


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "x.py").write_text("import requests\n", encoding="utf-8")
    return root


def _venv(root: Path) -> None:
    """Make a directory look enough like a virtualenv for the mode check."""
    (root / ".venv").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Rule-directory selection
# --------------------------------------------------------------------------


class TestExtensionsOf:
    """Path.suffix alone silently under-selects rulesets on compound names."""

    @pytest.mark.parametrize(
        ("name", "expected_member"),
        [
            ("config.yaml.sample", ".yaml"),
            ("values.yml.j2", ".yml"),
            ("settings.json.tmpl", ".json"),
            ("app.py.in", ".py"),
            ("plain.py", ".py"),
        ],
    )
    def test_finds_a_meaningful_suffix_behind_a_trailing_one(
        self, scan_runner, name, expected_member
    ):
        assert expected_member in scan_runner.extensions_of(name)

    def test_recognises_dockerfile_variants(self, scan_runner):
        assert "Dockerfile" in scan_runner.extensions_of("deployment/Dockerfile.prod")
        assert "Dockerfile" in scan_runner.extensions_of("Dockerfile")

    def test_a_sample_config_now_selects_the_yaml_ruleset(self, scan_runner):
        """The concrete regression: config.yaml.sample used to select nothing."""
        extensions = scan_runner.diff_extensions({"config.yaml.sample": [(1, 2)]})
        assert "yaml" in scan_runner.select_rule_dirs(extensions)

    def test_generic_is_always_selected(self, scan_runner):
        assert "generic" in scan_runner.select_rule_dirs(set())


# --------------------------------------------------------------------------
# ty: inference mode
# --------------------------------------------------------------------------


class TestTyEnvironment:
    def test_a_repo_without_a_virtualenv_is_bare(self, scan_runner, repo):
        mode, note = scan_runner.ty_environment(repo)
        assert mode == "bare"
        assert "bare" in note

    def test_an_existing_virtualenv_is_used(self, scan_runner, repo):
        _venv(repo)
        mode, note = scan_runner.ty_environment(repo)
        assert mode == "resolved"
        assert ".venv" in note

    def test_a_directory_named_venv_without_the_marker_does_not_count(
        self, scan_runner, repo
    ):
        (repo / ".venv").mkdir()  # no pyvenv.cfg
        assert scan_runner.ty_environment(repo)[0] == "bare"

    def test_it_never_creates_anything(self, scan_runner, repo):
        before = sorted(p.name for p in repo.iterdir())
        scan_runner.ty_environment(repo)
        assert sorted(p.name for p in repo.iterdir()) == before


TY_OUTPUT = (
    "app/x.py:1:8: error[unresolved-import] Cannot resolve imported module `requests`\n"
    "app/x.py:9:5: error[unresolved-import] Cannot resolve imported module `pydantic`\n"
    "app/x.py:20:7: error[invalid-argument-type] Expected `date`, found `date | None`\n"
)


class TestTyBareMode:
    """unresolved-import in bare mode is an environment artefact, not a defect."""

    def _run(self, scan_runner, repo, fake_tool):
        # Exit 1 is inside TY_OK_EXIT_CODES; the concise fallback is forced by
        # the non-JSON stdout (json.loads raises), not by the exit code.
        fake_tool("ty", stdout=TY_OUTPUT, exit_code=1)
        return scan_runner._run_ty(repo)

    def test_unresolved_imports_are_set_aside_not_reported(
        self, scan_runner, repo, fake_tool
    ):
        status, entries, _ = self._run(scan_runner, repo, fake_tool)
        assert status["mode"] == "bare"
        assert [e["rule"] for e in entries] == ["invalid-argument-type"]
        assert len(status["suppressed"]) == 2

    def test_setting_them_aside_is_disclosed(self, scan_runner, repo, fake_tool):
        status, _, _ = self._run(scan_runner, repo, fake_tool)
        joined = " ".join(status["notes"])
        assert "bare" in joined
        assert "unresolved-import 2 件" in joined

    def test_they_are_kept_for_a_human_to_look_at(self, scan_runner, repo, fake_tool):
        """Dropping them entirely would hide a genuinely wrong import path."""
        status, _, _ = self._run(scan_runner, repo, fake_tool)
        assert {e["rule"] for e in status["suppressed"]} == {"unresolved-import"}

    def test_a_resolved_run_keeps_unresolved_imports_as_real_findings(
        self, scan_runner, repo, fake_tool
    ):
        _venv(repo)
        status, entries, _ = self._run(scan_runner, repo, fake_tool)
        assert status["mode"] == "resolved"
        assert len(entries) == 3
        assert status["suppressed"] == []

    def test_the_mode_is_always_stated_even_when_nothing_was_suppressed(
        self, scan_runner, repo, fake_tool
    ):
        fake_tool("ty", stdout="", exit_code=0)
        status, entries, _ = scan_runner._run_ty(repo)
        assert status["mode"] == "bare"
        assert entries == []
        assert any("bare" in note for note in status["notes"])


# --------------------------------------------------------------------------
# oxlint: nothing to lint is not a failure
# --------------------------------------------------------------------------


class TestOxlintNothingToLint:
    MARKER = "No files found to lint"

    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    def test_no_javascript_is_skipped_not_an_error(
        self, scan_runner, repo, fake_tool, stream
    ):
        """It prints a bare line that is not JSON; parsing it used to say error."""
        fake_tool("oxlint", exit_code=1, **{stream: self.MARKER + "\n"})
        status, entries, _ = scan_runner._run_oxlint(repo)
        assert status["status"] == "skipped"
        assert entries == []

    def test_the_reason_is_stated(self, scan_runner, repo, fake_tool):
        fake_tool("oxlint", stderr=self.MARKER + "\n", exit_code=1)
        status, _, _ = scan_runner._run_oxlint(repo)
        assert status["skipped_reason"].strip()

    def test_real_diagnostics_are_still_parsed(self, scan_runner, repo, fake_tool):
        payload = {
            "diagnostics": [
                {
                    "filename": str(repo / "app" / "a.js"),
                    "code": "no-unused-vars",
                    "severity": "warning",
                    "message": "unused",
                    "labels": [{"span": {"line": 3}}],
                }
            ]
        }
        fake_tool("oxlint", stdout=json.dumps(payload), exit_code=1)
        status, entries, _ = scan_runner._run_oxlint(repo)
        assert status["status"] == "ok"
        assert [e["rule"] for e in entries] == ["no-unused-vars"]

    def test_genuinely_broken_output_is_still_an_error(
        self, scan_runner, repo, fake_tool
    ):
        """The fix must not turn every parse failure into a quiet skip."""
        fake_tool("oxlint", stdout="not json at all", exit_code=1)
        status, _, _ = scan_runner._run_oxlint(repo)
        assert status["status"] == "error"


# --------------------------------------------------------------------------
# Missing tools never become a clean result
# --------------------------------------------------------------------------


class TestMissingToolsAreDisclosed:
    @pytest.mark.parametrize(
        ("tool", "runner"),
        [("ruff", "_run_ruff"), ("ty", "_run_ty"), ("oxlint", "_run_oxlint")],
    )
    def test_an_absent_tool_is_skipped_with_a_reason(
        self, scan_runner, repo, tmp_path, monkeypatch, tool, runner
    ):
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        status, entries, _ = getattr(scan_runner, runner)(repo)
        assert status["status"] == "skipped"
        assert tool in status["skipped_reason"]
        assert entries == []

    def test_an_unexpected_exit_code_is_an_error_not_a_clean_scan(
        self, scan_runner, repo, fake_tool
    ):
        fake_tool("ruff", stdout="", stderr="internal panic", exit_code=2)
        status, entries, _ = scan_runner._run_ruff(repo)
        assert status["status"] == "error"
        assert entries == []


class TestLintEnvelope:
    """信封是不開 `sub` 的讀者唯一會看的東西，必須取最差、且說出理由。"""

    def _all_tools(self, fake_tool, *, ty_exit: int = 0, ty_stdout: str = ""):
        fake_tool("ruff", stdout="[]", exit_code=0)
        fake_tool("ty", stdout=ty_stdout, exit_code=ty_exit)
        fake_tool("oxlint", stdout=json.dumps({"diagnostics": []}), exit_code=0)

    def test_a_crashed_subtool_is_not_hidden_behind_a_working_one(
        self, scan_runner, repo, tmp_path, fake_tool
    ):
        """The reproduced bug: ty exits 3, ruff is fine, envelope said `ok`."""
        self._all_tools(fake_tool, ty_exit=3, ty_stdout="")
        digest = scan_runner.run_lint(repo, tmp_path / "out" / "mr1", None)

        assert digest["status"] == "error"
        assert "ty" in digest["skipped_reason"]
        assert digest["sub"]["ruff"]["status"] == "ok"

    def test_a_missing_subtool_leaves_the_envelope_skipped_with_a_reason(
        self, scan_runner, repo, tmp_path, fake_tool
    ):
        # ruff and ty present, oxlint absent from PATH entirely.
        fake_tool("ruff", stdout="[]", exit_code=0)
        fake_tool("ty", stdout="", exit_code=0)
        digest = scan_runner.run_lint(repo, tmp_path / "out" / "mr1", None)

        assert digest["status"] == "skipped"
        assert "oxlint" in digest["skipped_reason"]

    def test_error_outranks_skipped(self, scan_runner, repo, tmp_path, fake_tool):
        """Both present at once: the envelope reports the worse of the two."""
        fake_tool("ruff", stdout="", stderr="internal panic", exit_code=2)
        fake_tool("ty", stdout="", exit_code=0)
        digest = scan_runner.run_lint(repo, tmp_path / "out" / "mr1", None)

        assert digest["status"] == "error"
        assert "ruff" in digest["skipped_reason"]
        assert "oxlint" in digest["skipped_reason"]

    def test_every_tool_working_is_still_ok(
        self, scan_runner, repo, tmp_path, fake_tool
    ):
        """Worst-first must not turn every ordinary run into a failure."""
        self._all_tools(fake_tool)
        digest = scan_runner.run_lint(repo, tmp_path / "out" / "mr1", None)

        assert digest["status"] == "ok"
        assert digest["skipped_reason"] == ""

    def test_a_subtool_that_failed_without_explaining_itself_is_still_named(
        self, scan_runner, repo, tmp_path, fake_tool, monkeypatch
    ):
        """A tool that failed silently is the one a reader must not have to infer."""
        self._all_tools(fake_tool)
        monkeypatch.setattr(
            scan_runner,
            "_run_ty",
            lambda root: (
                {"status": "error", "exit_code": 3, "skipped_reason": "", "notes": []},
                [],
                None,
            ),
        )
        digest = scan_runner.run_lint(repo, tmp_path / "out" / "mr1", None)

        assert digest["status"] == "error"
        assert "ty" in digest["skipped_reason"]
        assert "未附理由" in digest["skipped_reason"]


class TestLintCounts:
    """沒有 --diff 時，「未歸屬」不得被寫成「在本次變更內」。"""

    @pytest.fixture
    def diff(self, tmp_path):
        path = tmp_path / "change.diff"
        path.write_text(
            "--- a/app/x.py\n+++ b/app/x.py\n@@ -1,1 +1,2 @@\n import requests\n+x = 1\n",
            encoding="utf-8",
        )
        return path

    def _ruff_only(self, fake_tool, repo):
        payload = [
            {
                "filename": str(repo / "app" / "x.py"),
                "code": "F401",
                "message": "unused import",
                "location": {"row": 2},
            }
        ]
        fake_tool("ruff", stdout=json.dumps(payload), exit_code=1)

    def test_without_a_diff_the_counts_say_unattributed(
        self, scan_runner, repo, tmp_path, fake_tool
    ):
        self._ruff_only(fake_tool, repo)
        digest = scan_runner.run_lint(repo, tmp_path / "out" / "mr1", None)

        assert "in_diff" not in digest["counts"]
        assert digest["counts"]["unattributed"] == 1
        assert digest["sub"]["ruff"]["counts"] == {"unattributed": 1}

    def test_with_a_diff_the_two_original_keys_stay(
        self, scan_runner, repo, tmp_path, fake_tool, diff
    ):
        self._ruff_only(fake_tool, repo)
        digest = scan_runner.run_lint(repo, tmp_path / "out" / "mr1", diff)

        assert set(digest["counts"]) == {"in_diff", "outside_diff"}
        assert "unattributed" not in digest["counts"]

    def test_the_unattributed_state_is_disclosed_in_the_notes(
        self, scan_runner, repo, tmp_path, fake_tool
    ):
        self._ruff_only(fake_tool, repo)
        digest = scan_runner.run_lint(repo, tmp_path / "out" / "mr1", None)
        assert any("未歸屬" in note for note in digest["notes"])


class TestTrivyVacuityGate:
    """「無標的」不得偽裝成「乾淨」：樹裡沒有依賴 manifest 時，digest 必須講出來。"""

    def test_finds_manifests_and_skips_excluded_dirs(self, scan_runner, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        nested = tmp_path / "sub"
        nested.mkdir()
        (nested / "package.json").write_text("{}")
        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "go.mod").write_text("module x\n")

        found = scan_runner.find_dependency_manifests(tmp_path)
        names = sorted(p.name for p in found)
        assert names == ["package.json", "pyproject.toml"]

    def test_empty_tree_has_no_manifests(self, scan_runner, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text("x = 1\n")
        assert scan_runner.find_dependency_manifests(tmp_path) == []

    def test_manifestless_tree_gets_the_vacuity_note(
        self, scan_runner, tmp_path, monkeypatch
    ):
        """整條 run_trivy 的接線：空報告 + 無 manifest → notes 揭露無標的。"""
        from types import SimpleNamespace

        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text("x = 1\n")
        out_prefix = tmp_path / "digest" / "mr1"
        artifact = out_prefix.with_name(out_prefix.name + ".trivy.json")
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"SchemaVersion": 2}')  # trivy 空報告：連 Results 鍵都沒有

        monkeypatch.setattr(scan_runner, "tool_available", lambda name: True)
        monkeypatch.setattr(
            scan_runner,
            "run_process",
            lambda argv, cwd=None: SimpleNamespace(
                failure="", missing=False, exit_code=0, stdout="", stderr=""
            ),
        )
        digest = scan_runner.run_trivy(tmp_path, out_prefix)
        assert digest["status"] == "ok"
        assert any("沒有標的" in n for n in digest["notes"])
        assert any("零發現不代表依賴乾淨" in n for n in digest["notes"])

    def test_tree_with_manifest_gets_no_vacuity_note(
        self, scan_runner, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace

        (tmp_path / "pyproject.toml").write_text("[project]\n")
        out_prefix = tmp_path / "digest" / "mr1"
        artifact = out_prefix.with_name(out_prefix.name + ".trivy.json")
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"SchemaVersion": 2, "Results": []}')

        monkeypatch.setattr(scan_runner, "tool_available", lambda name: True)
        monkeypatch.setattr(
            scan_runner,
            "run_process",
            lambda argv, cwd=None: SimpleNamespace(
                failure="", missing=False, exit_code=0, stdout="", stderr=""
            ),
        )
        digest = scan_runner.run_trivy(tmp_path, out_prefix)
        assert not any("沒有標的" in n for n in digest["notes"])

    def test_lockfile_suffix_rule_catches_any_lock(self, scan_runner, tmp_path):
        # 比照完整版 A2 gate：lock 類走後綴通則，未來的新 lockfile 也接得住
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / "pnpm-lock.yaml").write_text("")
        names = sorted(p.name for p in scan_runner.find_dependency_manifests(tmp_path))
        assert names == ["pnpm-lock.yaml", "uv.lock"]

    def test_vendored_manifests_do_not_count(self, scan_runner, tmp_path):
        # node_modules 裡永遠有 package.json，算進去等於 gate 永遠不觸發
        nm = tmp_path / "node_modules" / "leftpad"
        nm.mkdir(parents=True)
        (nm / "package.json").write_text("{}")
        assert scan_runner.find_dependency_manifests(tmp_path) == []
