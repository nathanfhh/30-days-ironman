# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run the nathan-code-review external scanners and return a compact digest.

The scanners this skill relies on (trivy, opengrep, ruff/ty/oxlint) emit output
that routinely runs to megabytes on a real project. A reviewing agent must never
load that into its context, so this runner does three things:

  1. invokes the tool with the flags fixed by ``references/scanners.md``
  2. archives the tool's full raw output next to the report, under ``--out``
  3. prints a small JSON digest on stdout — counts, plus a capped list of the
     highest-severity entries — from which the agent decides what is worth
     opening the archive for

Two rules from ``references/scanners.md`` are enforced here rather than left to
prose:

  - A missing tool is data, not a crash: the digest says ``status: skipped``
    with a reason, and the process still exits 0.
  - A crash that produced no findings is not a clean scan: a non-zero exit where
    non-zero is unexpected becomes ``status: error``, never an empty ``ok``.

Human-facing strings (reasons, summaries) are zh-TW because they are copied
into a published Traditional Chinese report. Tool names, rule ids, severities
and file paths stay in English.

CLI:
    uv run scan_runner.py trivy    --root <repo> --out <archive-prefix>
    uv run scan_runner.py opengrep --root <repo> --out <archive-prefix> --diff <diff-file>
    uv run scan_runner.py lint     --root <repo> --out <archive-prefix> --diff <diff-file>

``--out`` is a path prefix; the suffix (``.trivy.json`` / ``.opengrep.json`` /
``.lint.json``) is appended by this script, matching ``workspace-paths.md``.

Exit codes:
    0  the runner did its job (including when the scanner was skipped or failed
       — that outcome is reported inside the digest)
    2  usage error (unknown tool, missing/invalid --root, unreadable --diff)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Tunables. Each carries the reasoning that fixes its value, because a bare
# number here is a number nobody dares change later.
# --------------------------------------------------------------------------

# Scanners are I/O bound and trivy's first run may download a vulnerability
# database of several hundred MB. 15 minutes is long enough that a slow network
# does not manufacture a false "error", and short enough that one wedged process
# cannot stall a whole review phase.
SUBPROCESS_TIMEOUT_SECONDS = 900

# The digest exists to fit in an agent's context. 60 entries at roughly 200
# characters each is a few KB; the same scan's raw JSON is megabytes. Everything
# past the cap stays in the archive artifact and `truncated` says so, so nothing
# is lost — only deferred.
MAX_ENTRIES = 60

# Scanner messages run to several paragraphs. The digest only needs enough text
# to decide whether the lead is worth opening the artifact for.
MAX_MESSAGE_CHARS = 300

# Excluded from every scan. `.codegraph/` is the symbol index built in Phase 0:
# scanning our own index wastes time and produces findings about a directory
# that does not exist in the repository.
EXCLUDED_DIRS = (".git", ".codegraph")

# Ordering used to keep the worst findings when the entry cap bites.
SEVERITY_RANK = {
    "CRITICAL": 0,
    "HIGH": 1,
    "ERROR": 1,
    "MEDIUM": 2,
    "WARNING": 2,
    "LOW": 3,
    "INFO": 4,
    "UNKNOWN": 5,
}

# Extension -> semgrep-rules subdirectory, per the table in scanners.md.
EXTENSION_RULE_DIRS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".vue": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".html": "html",
    ".json": "json",
    ".sh": "bash",
    ".bash": "bash",
}

# `generic/` holds cross-language rules (hardcoded credentials, insecure
# transport) that apply whatever the file type, so it is never deselected.
ALWAYS_RULE_DIR = "generic"

# Walking a whole repository to guess its languages is only a fallback for a
# missing --diff; this bound keeps that fallback from turning into a full
# filesystem crawl on a monorepo.
MAX_FALLBACK_FILES = 20000


class UsageError(Exception):
    """Raised for caller mistakes — the only thing that makes this exit non-zero."""


# --------------------------------------------------------------------------
# Process handling
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RunResult:
    """Outcome of one subprocess invocation.

    ``failure`` is empty when the process actually ran to completion, whatever
    its exit code. It is non-empty (zh-TW) only when the process could not be
    started, was not found, or timed out.
    """

    exit_code: int | None
    stdout: str
    stderr: str
    failure: str = ""
    missing: bool = False


def _scanner_env() -> dict[str, str]:
    """The environment a scanner runs in — with this process's virtualenv stripped.

    ``VIRTUAL_ENV`` leaks in for a mundane reason with an ugly consequence. This
    skill is normally installed as a symlink into a repository, so running
    ``uv run scripts/scan_runner.py`` from the skill directory makes uv resolve
    *that* repository as the project and export its ``.venv``. ``ty`` honours
    ``VIRTUAL_ENV`` over the reviewed project's own ``.venv``, so it type-checks
    the code under review against a completely unrelated set of dependencies.

    What makes it worse than noise is that the label does not notice:
    ``ty_environment`` decides the mode from ``pyvenv.cfg`` existing in the
    reviewed repo, so it still reports ``resolved`` — and because the mode says
    resolved, the runner does **not** move ``unresolved-import`` into
    ``suppressed``. Every third-party import of the reviewed project is then
    forwarded as a real diagnostic (56 of them on the run that surfaced this),
    and a genuinely wrong import path is indistinguishable from the noise.

    Stripping it here rather than at the ty call site: every scanner should see
    the reviewed project, never the reviewer's own environment.
    """
    env = os.environ.copy()
    for var in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "CONDA_PREFIX"):
        env.pop(var, None)
    return env


def run_process(argv: list[str], cwd: Path) -> RunResult:
    """Run a scanner. Never uses a shell, always bounded by a timeout."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=_scanner_env(),
            capture_output=True,
            text=True,
            errors="replace",  # scanner output can carry undecodable bytes
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return RunResult(
            None, "", "", f"{argv[0]} 未安裝（不在 PATH 上）", missing=True
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            None,
            "",
            "",
            f"{argv[0]} 執行逾時（超過 {SUBPROCESS_TIMEOUT_SECONDS} 秒），本次掃描結果不可信",
        )
    except OSError as exc:
        return RunResult(None, "", "", f"{argv[0]} 無法啟動：{exc}")
    return RunResult(proc.returncode, proc.stdout, proc.stderr, "")


def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


# --------------------------------------------------------------------------
# Digest envelope
# --------------------------------------------------------------------------


def build_digest(
    tool: str,
    *,
    status: str = "ok",
    exit_code: int | None = None,
    artifact: str = "",
    counts: dict[str, int] | None = None,
    skipped_reason: str = "",
    entries: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every tool returns the same envelope, so the caller parses it once."""
    digest: dict[str, Any] = {
        "tool": tool,
        "status": status,
        "exit_code": exit_code,
        "artifact": artifact,
        "counts": dict(counts or {}),
        "skipped_reason": skipped_reason,
        "entries": list(entries or []),
        "truncated": truncated,
        "notes": list(notes or []),
    }
    if extra:
        digest.update(extra)
    return digest


def cap_entries(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Sort worst-first, then cap. Truncation drops the least severe leads."""
    ordered = sorted(
        entries,
        key=lambda e: (
            SEVERITY_RANK.get(str(e.get("severity", "")).upper(), 9),
            str(e.get("file", "")),
            e.get("line") or 0,
        ),
    )
    if len(ordered) <= MAX_ENTRIES:
        return ordered, False
    return ordered[:MAX_ENTRIES], True


def shorten(text: str) -> str:
    text = " ".join(str(text).split())
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return text[: MAX_MESSAGE_CHARS - 1] + "…"


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def to_repo_relative(raw: str, root: Path) -> str:
    """Normalise a scanner-reported path to a forward-slash, repo-relative one.

    Tools disagree: trivy prints the path it was given, ruff prints absolute
    paths, oxlint and ty print paths relative to their working directory (which
    this runner always sets to the repo root). All three collapse here.
    """
    if not raw:
        return ""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        # Outside the repo (or unresolvable): keep it, but say so honestly.
        return Path(raw).as_posix()


def is_excluded(rel_path: str) -> bool:
    parts = rel_path.split("/")
    return any(part in EXCLUDED_DIRS for part in parts)


# --------------------------------------------------------------------------
# Unified diff parsing — the basis of lint attribution
# --------------------------------------------------------------------------

_DIFF_TARGET_RE = re.compile(r"^\+\+\+\s+(?:b/)?(.+?)(?:\t.*)?$")
# "@@ -old,count +new,count @@" — only the "+" side (the post-change line
# numbers) matters, because that is what a scanner reports against the checkout.
_HUNK_RE = re.compile(r"^@@+\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@")


def parse_unified_diff(diff_path: Path) -> dict[str, list[tuple[int, int]]]:
    """file -> list of inclusive (start, end) changed line ranges, new-file side."""
    try:
        text = diff_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UsageError(f"無法讀取 diff 檔案 {diff_path}：{exc}") from exc

    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("+++ "):
            match = _DIFF_TARGET_RE.match(line)
            target = match.group(1).strip() if match else ""
            # A deleted file has "+++ /dev/null": nothing on the new side to
            # attribute diagnostics to.
            current = None if target in ("", "/dev/null") else target
            if current is not None:
                ranges.setdefault(current, [])
            continue
        if current is not None and line.startswith("@@"):
            match = _HUNK_RE.match(line)
            if match is None:
                continue
            start = int(match.group(1))
            # "@@ -1 +1 @@" without a count means exactly one line.
            count = int(match.group(2)) if match.group(2) is not None else 1
            if count > 0:
                ranges[current].append((start, start + count - 1))
    return ranges


def extensions_of(path: str) -> set[str]:
    """Every suffix of a filename, plus the Dockerfile marker.

    All of them, not just the last one. ``Path("config.yaml.sample").suffix``
    is ``.sample``, so a file that is plainly YAML would select no YAML rules —
    and under-selecting a ruleset fails *silently*: the scan finds nothing and
    reads as clean. Compound names like this are routine for configuration
    (``.yaml.sample``, ``.yml.j2``, ``.json.tmpl``, ``.py.in``).

    Over-selecting only costs scan time, so the trade is one-sided.
    """
    name = Path(path).name
    found = {suffix.lower() for suffix in Path(name).suffixes}
    if name.startswith("Dockerfile"):
        found.add("Dockerfile")
    return found


def diff_extensions(changed: dict[str, list[tuple[int, int]]]) -> set[str]:
    """Extensions (and Dockerfile markers) present on the new side of the diff."""
    found: set[str] = set()
    for path in changed:
        found |= extensions_of(path)
    return found


def repo_extensions(root: Path) -> set[str]:
    """Fallback when no diff is supplied: what languages does this repo hold?"""
    found: set[str] = set()
    seen = 0
    for path in root.rglob("*"):
        if seen >= MAX_FALLBACK_FILES:
            break
        rel = path.relative_to(root).as_posix()
        if is_excluded(rel):
            continue
        if not path.is_file():
            continue
        seen += 1
        found |= extensions_of(path.name)
    return found


def line_in_diff(
    changed: dict[str, list[tuple[int, int]]], rel_path: str, line: int | None
) -> bool:
    if line is None:
        return False
    for start, end in changed.get(rel_path, ()):
        if start <= line <= end:
            return True
    return False


# --------------------------------------------------------------------------
# trivy
# --------------------------------------------------------------------------

# trivy exits 0 whether or not it found something, unless --exit-code is passed
# (this runner deliberately does not pass it: findings are read from the JSON).
# Any non-zero exit therefore means trivy itself failed.
TRIVY_OK_EXIT_CODES = frozenset({0})

# Dependency manifests trivy's vuln scanner can actually read. A tree with none
# of these has no vuln-scan target at all, and "no target" must never be
# reported as "clean" — the trap is real: when the clone is blocked and the tree
# is rebuilt from API-fetched source files, root-level manifests are the first
# thing to go missing, and trivy then returns an empty report with exit code 0.
# 顯式清單對齊完整版 review.md Track A2 的 gate，另補其生態缺口
# （go.sum / pom.xml / build.gradle）；lock 類比照完整版走後綴通則，
# 不逐一枚舉——uv.lock、poetry.lock、yarn.lock、package-lock.json、
# pnpm-lock.yaml 乃至未來的新 lockfile 都落在三個後綴裡。
DEPENDENCY_MANIFESTS = (
    "pyproject.toml",
    "requirements*.txt",
    "package.json",
    "Pipfile",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Gemfile",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)
LOCKFILE_SUFFIXES = (".lock", "-lock.json", "-lock.yaml")

# vendored 依賴目錄裡的 manifest 不算數：node_modules 裡永遠有 package.json，
# 把它當標的等於這個 gate 永遠不會觸發。
VENDOR_DIRS = frozenset({"node_modules", ".venv", "venv", "vendor"})

# 走訪原始碼樹時要跳過的目錄：掃描排除的 + vendored 依賴。任何「這棵樹裡有幾個
# 檔案」的統計都用這一份，否則 node_modules 會把數字灌大好幾個量級。
SKIPPED_TREE_DIRS = frozenset(EXCLUDED_DIRS) | VENDOR_DIRS


def find_dependency_manifests(root: Path) -> list[Path]:
    """List dependency manifests in the tree, skipping excluded/vendored dirs."""
    import fnmatch

    found: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file() or any(part in SKIPPED_TREE_DIRS for part in p.parts):
            continue
        name = p.name
        if name.endswith(LOCKFILE_SUFFIXES) or any(
            fnmatch.fnmatch(name, pat) for pat in DEPENDENCY_MANIFESTS
        ):
            found.append(p)
    return sorted(found)


def run_trivy(root: Path, out_prefix: Path) -> dict[str, Any]:
    artifact = out_prefix.with_name(out_prefix.name + ".trivy.json")
    if not tool_available("trivy"):
        return build_digest(
            "trivy",
            status="skipped",
            artifact=str(artifact),
            skipped_reason="trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描",
        )

    argv = [
        "trivy",
        "fs",
        "--scanners",
        "vuln,misconfig,secret",
        "--severity",
        "CRITICAL,HIGH,MEDIUM",
        "--format",
        "json",
        "--output",
        str(artifact),
    ]
    for excluded in EXCLUDED_DIRS:
        argv += ["--skip-dirs", excluded]
    argv.append(str(root))

    artifact.parent.mkdir(parents=True, exist_ok=True)
    result = run_process(argv, cwd=root)

    if result.failure:
        return build_digest(
            "trivy",
            status="skipped" if result.missing else "error",
            exit_code=result.exit_code,
            artifact=str(artifact),
            skipped_reason=result.failure,
        )

    notes: list[str] = []
    if result.exit_code not in TRIVY_OK_EXIT_CODES:
        return build_digest(
            "trivy",
            status="error",
            exit_code=result.exit_code,
            artifact=str(artifact),
            skipped_reason=(
                f"trivy 以非預期的結束碼 {result.exit_code} 結束，掃描結果不完整，"
                f"不得視為「無發現」；stderr 末段：{shorten(result.stderr[-MAX_MESSAGE_CHARS:])}"
            ),
            notes=notes,
        )

    # Staleness of the vulnerability DB is worth disclosing but is not a failure.
    if "db" in result.stderr.lower() and "fail" in result.stderr.lower():
        notes.append("trivy 弱點資料庫更新可能失敗，結果可能使用較舊的快取資料庫。")

    # 「無標的」與「乾淨」是兩件事。樹裡一個依賴 manifest 都沒有時，vuln 掃描
    # 從頭到尾沒有東西可查，零發現不代表依賴乾淨——報告必須揭露，不得寫成
    # 「安全掃描零命中」。
    if not find_dependency_manifests(root):
        file_count = sum(
            1
            for p in root.rglob("*")
            if p.is_file() and not any(part in SKIPPED_TREE_DIRS for part in p.parts)
        )
        notes.append(
            "掃描樹中未發現任何依賴 manifest（pyproject.toml / lockfile / "
            "package.json 等）——vuln 掃描沒有標的，零發現不代表依賴乾淨，"
            f"報告必須揭露此限制。misconfig 與 secret 掃描僅涵蓋樹中現有的 "
            f"{file_count} 個檔案。"
        )

    try:
        raw = json.loads(artifact.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return build_digest(
            "trivy",
            status="error",
            exit_code=result.exit_code,
            artifact=str(artifact),
            skipped_reason=f"trivy 執行完成但輸出無法解析：{exc}",
            notes=notes,
        )

    severity_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    entries: list[dict[str, Any]] = []

    for result_block in raw.get("Results") or []:
        target = to_repo_relative(str(result_block.get("Target", "")), root)

        for vuln in result_block.get("Vulnerabilities") or []:
            severity = str(vuln.get("Severity", "UNKNOWN")).upper()
            severity_counts[severity] += 1
            class_counts["vuln"] += 1
            pkg = str(vuln.get("PkgName", "")) or str(vuln.get("PkgID", ""))
            version = str(vuln.get("InstalledVersion", ""))
            entries.append(
                {
                    "class": "vuln",
                    "severity": severity,
                    "id": f"{pkg}@{version} {vuln.get('VulnerabilityID', '')}".strip(),
                    "file": to_repo_relative(str(vuln.get("PkgPath") or ""), root)
                    or target,
                    "line": None,  # trivy reports package-level, not line-level
                    "title": shorten(
                        str(vuln.get("Title") or vuln.get("Description") or "")
                    ),
                }
            )

        for misconf in result_block.get("Misconfigurations") or []:
            severity = str(misconf.get("Severity", "UNKNOWN")).upper()
            severity_counts[severity] += 1
            class_counts["misconfig"] += 1
            cause = misconf.get("CauseMetadata") or {}
            entries.append(
                {
                    "class": "misconfig",
                    "severity": severity,
                    "id": str(misconf.get("ID") or misconf.get("AVDID") or ""),
                    "file": target,
                    "line": cause.get("StartLine"),
                    "title": shorten(str(misconf.get("Title") or "")),
                }
            )

        for secret in result_block.get("Secrets") or []:
            severity = str(secret.get("Severity", "UNKNOWN")).upper()
            severity_counts[severity] += 1
            class_counts["secret"] += 1
            entries.append(
                {
                    "class": "secret",
                    "severity": severity,
                    "id": str(secret.get("RuleID") or ""),
                    "file": target,
                    "line": secret.get("StartLine"),
                    "title": shorten(str(secret.get("Title") or "")),
                }
            )

    capped, truncated = cap_entries(entries)
    if truncated:
        notes.append(
            f"共 {len(entries)} 筆，digest 只列出風險最高的 {MAX_ENTRIES} 筆；"
            f"完整結果在 {artifact.name}。"
        )
    if not entries:
        notes.append("trivy 正常執行完畢，於指定嚴重度區間內無發現。")

    return build_digest(
        "trivy",
        status="ok",
        exit_code=result.exit_code,
        artifact=str(artifact),
        counts=dict(severity_counts),
        entries=capped,
        truncated=truncated,
        notes=notes,
        extra={"class_counts": dict(class_counts)},
    )


# --------------------------------------------------------------------------
# opengrep
# --------------------------------------------------------------------------

# opengrep (like semgrep) exits 0 on a clean run, and 1 when findings are
# present and it was asked to signal them. 2 and above are real failures
# (bad config, parse error, internal crash).
OPENGREP_OK_EXIT_CODES = frozenset({0, 1})


def resolve_rules_root() -> Path:
    env_value = os.environ.get("NCR_OPENGREP_RULES", "").strip()
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / "semgrep-rules"


def select_rule_dirs(extensions: set[str]) -> list[str]:
    selected: set[str] = {ALWAYS_RULE_DIR}
    for ext in extensions:
        if ext == "Dockerfile":
            selected.add("dockerfile")
            continue
        mapped = EXTENSION_RULE_DIRS.get(ext)
        if mapped:
            selected.add(mapped)
    return sorted(selected)


def run_opengrep(
    root: Path, out_prefix: Path, diff_path: Path | None
) -> dict[str, Any]:
    artifact = out_prefix.with_name(out_prefix.name + ".opengrep.json")
    notes: list[str] = []

    if not tool_available("opengrep"):
        return build_digest(
            "opengrep",
            status="skipped",
            artifact=str(artifact),
            skipped_reason="opengrep 未安裝（不在 PATH 上），本次未執行 SAST 掃描",
        )

    rules_root = resolve_rules_root()
    if not rules_root.is_dir():
        return build_digest(
            "opengrep",
            status="skipped",
            artifact=str(artifact),
            skipped_reason=(
                f"opengrep 規則目錄不存在：{rules_root.as_posix()}。"
                "請 clone semgrep-rules，或以環境變數 NCR_OPENGREP_RULES 指定位置"
            ),
        )

    if diff_path is not None:
        extensions = diff_extensions(parse_unified_diff(diff_path))
        notes.append("規則目錄依 diff 中出現的副檔名挑選。")
    else:
        extensions = repo_extensions(root)
        notes.append("未提供 --diff，規則目錄改依整個 repo 的副檔名挑選。")

    wanted = select_rule_dirs(extensions)
    existing = [name for name in wanted if (rules_root / name).is_dir()]
    missing = [name for name in wanted if name not in existing]
    if missing:
        notes.append(f"規則目錄不存在，已略過：{', '.join(missing)}")

    if not existing:
        return build_digest(
            "opengrep",
            status="skipped",
            artifact=str(artifact),
            skipped_reason=(
                f"在 {rules_root.as_posix()} 底下找不到任何可用的規則目錄"
                f"（預期至少有 {ALWAYS_RULE_DIR}/）"
            ),
            notes=notes,
            extra={"rule_dirs": []},
        )

    argv = ["opengrep", "scan"]
    for name in existing:
        argv += ["--config", str(rules_root / name)]
    argv += ["--json", "--output", str(artifact)]
    for excluded in EXCLUDED_DIRS:
        argv += ["--exclude", excluded]
    argv.append(str(root))

    artifact.parent.mkdir(parents=True, exist_ok=True)
    result = run_process(argv, cwd=root)

    if result.failure:
        return build_digest(
            "opengrep",
            status="skipped" if result.missing else "error",
            exit_code=result.exit_code,
            artifact=str(artifact),
            skipped_reason=result.failure,
            notes=notes,
            extra={"rule_dirs": existing},
        )

    if result.exit_code not in OPENGREP_OK_EXIT_CODES:
        return build_digest(
            "opengrep",
            status="error",
            exit_code=result.exit_code,
            artifact=str(artifact),
            skipped_reason=(
                f"opengrep 以非預期的結束碼 {result.exit_code} 結束，掃描結果不完整，"
                f"不得視為「無發現」；stderr 末段："
                f"{shorten(result.stderr[-MAX_MESSAGE_CHARS:])}"
            ),
            notes=notes,
            extra={"rule_dirs": existing},
        )

    try:
        raw = json.loads(artifact.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return build_digest(
            "opengrep",
            status="error",
            exit_code=result.exit_code,
            artifact=str(artifact),
            skipped_reason=f"opengrep 執行完成但輸出無法解析：{exc}",
            notes=notes,
            extra={"rule_dirs": existing},
        )

    # check_id is prefixed with the dotted form of the rules root
    # ("home.nathan.semgrep-rules.python.lang..."); strip it so the digest
    # carries the rule id a human would recognise.
    prefix = ".".join(
        part for part in rules_root.resolve().parts if part not in ("/", "")
    )

    severity_counts: Counter[str] = Counter()
    entries: list[dict[str, Any]] = []
    for item in raw.get("results") or []:
        extra_block = item.get("extra") or {}
        severity = str(extra_block.get("severity", "INFO")).upper()
        severity_counts[severity] += 1
        rule_id = str(item.get("check_id", ""))
        if prefix and rule_id.startswith(prefix + "."):
            rule_id = rule_id[len(prefix) + 1 :]
        entries.append(
            {
                "rule_id": rule_id,
                "severity": severity,
                "file": to_repo_relative(str(item.get("path", "")), root),
                "line": (item.get("start") or {}).get("line"),
                "message": shorten(str(extra_block.get("message", ""))),
            }
        )

    scan_errors = raw.get("errors") or []
    if scan_errors:
        notes.append(f"opengrep 回報 {len(scan_errors)} 筆檔案層級錯誤，詳見輸出檔。")

    capped, truncated = cap_entries(entries)
    if truncated:
        notes.append(
            f"共 {len(entries)} 筆，digest 只列出風險最高的 {MAX_ENTRIES} 筆；"
            f"完整結果在 {artifact.name}。"
        )
    if not entries:
        notes.append("opengrep 正常執行完畢，選用的規則目錄下無發現。")

    return build_digest(
        "opengrep",
        status="ok",
        exit_code=result.exit_code,
        artifact=str(artifact),
        counts=dict(severity_counts),
        entries=capped,
        truncated=truncated,
        notes=notes,
        extra={"rule_dirs": existing, "rules_root": rules_root.as_posix()},
    )


# --------------------------------------------------------------------------
# lint: ruff / ty / oxlint
# --------------------------------------------------------------------------

# ruff: 0 clean, 1 violations found, 2 usage/internal error.
RUFF_OK_EXIT_CODES = frozenset({0, 1})
# ty: 0 clean, 1 diagnostics found, 2 usage/internal error (this is also how the
# unsupported --output-format json is detected).
TY_OK_EXIT_CODES = frozenset({0, 1})
# oxlint: 0 clean or warnings only, 1 lint errors found (also used when there is
# nothing to lint), >1 real failure.
OXLINT_OK_EXIT_CODES = frozenset({0, 1})

# oxlint prints this instead of JSON when the tree holds no JS/TS at all.
OXLINT_NO_FILES_MARKER = "No files found to lint"

# ty --output-format concise: "path:line:col: severity[rule] message".
_TY_LINE_RE = re.compile(
    r"^(?P<file>[^:]+(?::[^:\d][^:]*)*):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<severity>error|warning|info)(?:\[(?P<rule>[^\]]+)\])?\s*(?P<message>.*)$"
)

# In bare mode every third-party import is unresolvable, so this rule fires
# once per import across the whole project and says nothing about the change.
TY_UNRESOLVED_IMPORT_RULE = "unresolved-import"


def ty_environment(root: Path) -> tuple[str, str]:
    """Report whether ty can resolve third-party types. Never creates anything.

    Installing the reviewed project's dependencies would buy full type
    inference at two prices this skill will not pay.

    It would execute code the merge request's author controls: a source
    distribution builds through a PEP 517 backend, which is arbitrary code, and
    the ``pyproject.toml`` naming that backend is part of the branch under
    review. A tool whose whole purpose is reading untrusted branches must not
    install from them.

    And it would require network access. The environments this runs in cannot
    be assumed to have any, so a design that needs a download to work correctly
    is a design that silently degrades in the place it matters.

    So the mode is a fact about the environment, reported honestly, rather than
    something the scan reaches out and changes. Setting an environment up is
    the operator's decision, taken outside this tool.
    """
    # ⚠ **這支只看得到「有沒有一個 venv」，看不到「ty 剛才用了誰」。** 兩者曾經不一致：
    #   VIRTUAL_ENV 從呼叫端洩進去時，ty 用的是別的專案的環境，而這裡照樣回報 resolved
    #   ——標籤與現實脫節，而且因為它說 resolved，unresolved-import 不會被收進 suppressed，
    #   於是受審專案每一個第三方 import 都被當成真的診斷送出去。洩漏本身已經在
    #   `_scanner_env()` 堵掉；這段註解留著，是因為下一個在這裡加判斷的人要知道：
    #   **這個函式回答的是環境的形狀，不是 ty 的實際行為。** 要斷言後者，得看 ty 的輸出。
    leaked = [v for v in ("VIRTUAL_ENV", "CONDA_PREFIX") if os.environ.get(v)]
    for candidate in (root / ".venv", root / "venv"):
        if (candidate / "pyvenv.cfg").is_file():
            note = f"使用受審 repo 既有的虛擬環境 {candidate.name}/，第三方型別已解析。"
            if leaked:
                note += (
                    f"（呼叫端環境帶著 {'／'.join(leaked)}，已在執行掃描器時剝除，"
                    f"否則 ty 會改用那一個而這個標籤會說謊。）"
                )
            return "resolved", note
    return "bare", (
        "受審 repo 沒有可用的虛擬環境，ty 以 bare 模式執行："
        "第三方型別未解析，推導範圍僅專案內部與標準庫。"
        "本工具不會為了掃描而安裝相依（安裝會執行受審分支控制的程式碼，且需要網路）。"
    )


def _sub_entry(
    tool: str, rule: str, severity: str, file: str, line: int | None, message: str
) -> dict[str, Any]:
    return {
        "tool": tool,
        "rule": rule,
        "severity": severity.upper(),
        "file": file,
        "line": line,
        "message": shorten(message),
    }


def _partition(
    entries: list[dict[str, Any]],
    changed: dict[str, list[tuple[int, int]]],
    attribute: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split diagnostics into ones this change caused and pre-existing ones.

    This split is the whole point of running the linters over the project rather
    than over the diff: ty needs the project to resolve types, but the project
    is not what is under review. Reporting pre-existing project debt as though
    this author introduced it destroys the report's credibility with its reader
    — after the second false accusation nobody reads the third finding. So
    outside_diff diagnostics are counted and summarised in one line, never
    listed as findings.
    """
    if not attribute:
        # No diff supplied: attribution is impossible, so nothing is claimed.
        # The caller must label these `unattributed` rather than `in_diff` --
        # see _lint_counts.
        return entries, []
    in_diff: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    for entry in entries:
        target = (
            in_diff if line_in_diff(changed, entry["file"], entry["line"]) else outside
        )
        target.append(entry)
    return in_diff, outside


def _lint_counts(in_diff: int, outside: int, attribute: bool) -> dict[str, int]:
    """Name the counts after what the run actually established.

    With no --diff, _partition returns every diagnostic in its first slot
    because attribution was impossible — not because the diagnostics fall
    inside the change. Publishing that under `in_diff` asserts as fact the one
    thing this run could not determine, and a reader who only sees the counts
    has no way to tell the two apart. `unattributed` says what happened.
    """
    if attribute:
        return {"in_diff": in_diff, "outside_diff": outside}
    return {"unattributed": in_diff}


def _run_ruff(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    """Returns (sub-status block, entries, raw output for the archive)."""
    if not tool_available("ruff"):
        return (
            {
                "status": "skipped",
                "exit_code": None,
                "skipped_reason": "ruff 未安裝（不在 PATH 上）",
                "notes": [],
            },
            [],
            None,
        )
    argv = ["ruff", "check", "--output-format", "json", "--no-cache"]
    for excluded in EXCLUDED_DIRS:
        argv += ["--exclude", excluded]
    argv += ["--force-exclude", str(root)]
    result = run_process(argv, cwd=root)

    if result.failure:
        return (
            {
                "status": "skipped" if result.missing else "error",
                "exit_code": result.exit_code,
                "skipped_reason": result.failure,
                "notes": [],
            },
            [],
            None,
        )
    if result.exit_code not in RUFF_OK_EXIT_CODES:
        return (
            {
                "status": "error",
                "exit_code": result.exit_code,
                "skipped_reason": (
                    f"ruff 以非預期的結束碼 {result.exit_code} 結束，"
                    f"結果不得視為「無問題」；stderr："
                    f"{shorten(result.stderr[-MAX_MESSAGE_CHARS:])}"
                ),
                "notes": [],
            },
            [],
            {"stdout": result.stdout, "stderr": result.stderr},
        )
    try:
        raw = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return (
            {
                "status": "error",
                "exit_code": result.exit_code,
                "skipped_reason": f"ruff 輸出不是合法 JSON：{exc}",
                "notes": [],
            },
            [],
            {"stdout": result.stdout, "stderr": result.stderr},
        )

    entries: list[dict[str, Any]] = []
    for item in raw:
        rel = to_repo_relative(str(item.get("filename", "")), root)
        if is_excluded(rel):
            continue
        entries.append(
            _sub_entry(
                "ruff",
                str(item.get("code") or item.get("name") or ""),
                str(item.get("severity") or "error"),
                rel,
                (item.get("location") or {}).get("row"),
                str(item.get("message", "")),
            )
        )
    return (
        {
            "status": "ok",
            "exit_code": result.exit_code,
            "skipped_reason": "",
            "notes": [],
        },
        entries,
        raw,
    )


def _run_ty(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    if not tool_available("ty"):
        return (
            {
                "status": "skipped",
                "exit_code": None,
                "skipped_reason": "ty 未安裝（不在 PATH 上）",
                "notes": [],
            },
            [],
            None,
        )

    mode, mode_note = ty_environment(root)
    notes: list[str] = [mode_note]
    base = ["ty", "check"]
    for excluded in EXCLUDED_DIRS:
        base += ["--exclude", excluded]

    # ty may not support `--output-format json` (current builds offer only
    # full / concise / gitlab / github / junit). Try it, and fall back to the
    # machine-parseable plain format, recording that the fallback was used.
    result = run_process(base + ["--output-format", "json", str(root)], cwd=root)
    parsed_json: Any = None
    used_fallback = False

    if result.failure:
        return (
            {
                "status": "skipped" if result.missing else "error",
                "exit_code": result.exit_code,
                "skipped_reason": result.failure,
                "notes": notes,
            },
            [],
            None,
        )

    if result.exit_code in TY_OK_EXIT_CODES:
        try:
            parsed_json = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            used_fallback = True
    else:
        used_fallback = True

    if used_fallback:
        notes.append(
            "ty 不支援 --output-format json，已改用 concise 純文字輸出並解析。"
        )
        result = run_process(base + ["--output-format", "concise", str(root)], cwd=root)
        if result.failure:
            return (
                {
                    "status": "error",
                    "exit_code": result.exit_code,
                    "skipped_reason": result.failure,
                    "notes": notes,
                },
                [],
                None,
            )
        if result.exit_code not in TY_OK_EXIT_CODES:
            return (
                {
                    "status": "error",
                    "exit_code": result.exit_code,
                    "skipped_reason": (
                        f"ty 以非預期的結束碼 {result.exit_code} 結束，"
                        f"結果不得視為「無問題」；stderr："
                        f"{shorten(result.stderr[-MAX_MESSAGE_CHARS:])}"
                    ),
                    "notes": notes,
                },
                [],
                {"stdout": result.stdout, "stderr": result.stderr},
            )

    entries: list[dict[str, Any]] = []
    if parsed_json is not None and not used_fallback:
        # Defensive: if a future ty gains JSON output, accept the obvious shape.
        items = (
            parsed_json
            if isinstance(parsed_json, list)
            else parsed_json.get("diagnostics", [])
        )
        for item in items:
            rel = to_repo_relative(
                str(item.get("file") or item.get("filename") or ""), root
            )
            if is_excluded(rel):
                continue
            entries.append(
                _sub_entry(
                    "ty",
                    str(item.get("rule") or item.get("code") or ""),
                    str(item.get("severity") or "error"),
                    rel,
                    (item.get("line") or (item.get("location") or {}).get("row")),
                    str(item.get("message", "")),
                )
            )
        raw_payload: Any = parsed_json
    else:
        for line in result.stdout.splitlines():
            match = _TY_LINE_RE.match(line.strip())
            if match is None:
                continue
            rel = to_repo_relative(match.group("file"), root)
            if is_excluded(rel):
                continue
            entries.append(
                _sub_entry(
                    "ty",
                    match.group("rule") or "",
                    match.group("severity"),
                    rel,
                    int(match.group("line")),
                    match.group("message"),
                )
            )
        raw_payload = {
            "format": "concise-text",
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    # In bare mode unresolved-import is an artefact of the environment, not a
    # property of the code. Left in, it buries the real diagnostics: on a
    # project of any size it fires once per third-party import, which was 173
    # of 297 diagnostics the last time this ran. Dropped silently, a genuinely
    # wrong import path would disappear with it — so they are set aside and
    # reported separately, for a human to glance at rather than to act on.
    suppressed: list[dict[str, Any]] = []
    if mode == "bare":
        kept: list[dict[str, Any]] = []
        for entry in entries:
            (suppressed if entry["rule"] == TY_UNRESOLVED_IMPORT_RULE else kept).append(
                entry
            )
        entries = kept
        if suppressed:
            notes.append(
                f"bare 模式下抑制 unresolved-import {len(suppressed)} 件，不列為發現；"
                "若其中有專案內部的匯入路徑，仍值得人工確認（見 suppressed）。"
            )

    return (
        {
            "status": "ok",
            "exit_code": result.exit_code,
            "skipped_reason": "",
            "notes": notes,
            "mode": mode,
            "suppressed": suppressed,
        },
        entries,
        raw_payload,
    )


def _run_oxlint(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    if not tool_available("oxlint"):
        return (
            {
                "status": "skipped",
                "exit_code": None,
                "skipped_reason": "oxlint 未安裝（不在 PATH 上）",
                "notes": [],
            },
            [],
            None,
        )
    argv = ["oxlint", "--format", "json"]
    for excluded in EXCLUDED_DIRS:
        argv += ["--ignore-pattern", f"{excluded}/**"]
    argv.append(str(root))
    result = run_process(argv, cwd=root)

    if result.failure:
        return (
            {
                "status": "skipped" if result.missing else "error",
                "exit_code": result.exit_code,
                "skipped_reason": result.failure,
                "notes": [],
            },
            [],
            None,
        )

    notes: list[str] = []
    # oxlint reuses exit code 1 for "found lint errors" and for "no files to
    # lint", and prints the latter as a bare line that is not JSON. Parsing it
    # as JSON fails, which used to surface as status=error — reporting a broken
    # scanner on every Python-only repository, which is most of them here.
    # Nothing to lint is not a failure and not a clean bill of health either:
    # it is `skipped`, with the reason stated.
    if (
        OXLINT_NO_FILES_MARKER in result.stderr
        or OXLINT_NO_FILES_MARKER in result.stdout
    ):
        return (
            {
                "status": "skipped",
                "exit_code": result.exit_code,
                "skipped_reason": "此 repo 沒有 oxlint 可檢查的 JS/TS 檔案。",
                "notes": notes,
            },
            [],
            {"stdout": result.stdout, "stderr": result.stderr},
        )

    if result.exit_code not in OXLINT_OK_EXIT_CODES:
        return (
            {
                "status": "error",
                "exit_code": result.exit_code,
                "skipped_reason": (
                    f"oxlint 以非預期的結束碼 {result.exit_code} 結束，"
                    f"結果不得視為「無問題」；stderr："
                    f"{shorten(result.stderr[-MAX_MESSAGE_CHARS:])}"
                ),
                "notes": notes,
            },
            [],
            {"stdout": result.stdout, "stderr": result.stderr},
        )

    try:
        raw = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return (
            {
                "status": "error",
                "exit_code": result.exit_code,
                "skipped_reason": f"oxlint 輸出不是合法 JSON：{exc}",
                "notes": notes,
            },
            [],
            {"stdout": result.stdout, "stderr": result.stderr},
        )

    entries: list[dict[str, Any]] = []
    for item in raw.get("diagnostics") or []:
        rel = to_repo_relative(str(item.get("filename", "")), root)
        if is_excluded(rel):
            continue
        labels = item.get("labels") or []
        line = (labels[0].get("span") or {}).get("line") if labels else None
        entries.append(
            _sub_entry(
                "oxlint",
                str(item.get("code", "")),
                str(item.get("severity") or "warning"),
                rel,
                line,
                str(item.get("message", "")),
            )
        )
    return (
        {
            "status": "ok",
            "exit_code": result.exit_code,
            "skipped_reason": "",
            "notes": notes,
        },
        entries,
        raw,
    )


def run_lint(root: Path, out_prefix: Path, diff_path: Path | None) -> dict[str, Any]:
    artifact = out_prefix.with_name(out_prefix.name + ".lint.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)

    if diff_path is not None:
        changed = parse_unified_diff(diff_path)
        attribute = True
    else:
        changed = {}
        attribute = False

    runners = (("ruff", _run_ruff), ("ty", _run_ty), ("oxlint", _run_oxlint))

    sub: dict[str, Any] = {}
    raw_bundle: dict[str, Any] = {}
    all_in_diff: list[dict[str, Any]] = []
    total_in = 0
    total_out = 0
    notes: list[str] = []

    if not attribute:
        notes.append(
            "未提供 --diff，無法區分本次變更與專案既有問題，以下全部列為未歸屬。"
        )

    for name, runner in runners:
        status_block, entries, raw = runner(root)
        raw_bundle[name] = {
            "status": status_block["status"],
            "exit_code": status_block["exit_code"],
            "skipped_reason": status_block["skipped_reason"],
            "output": raw,
        }
        in_diff, outside = _partition(entries, changed, attribute)
        capped, truncated = cap_entries(in_diff)
        total_in += len(in_diff)
        total_out += len(outside)
        all_in_diff.extend(in_diff)

        sub_notes = list(status_block["notes"])
        if outside:
            # Counted, never itemised: see _partition's docstring.
            sub_notes.append(f"專案既有問題 {len(outside)} 件，不列入本次。")
        if truncated:
            scope = "本次變更內" if attribute else "未歸屬"
            sub_notes.append(
                f"{scope} {len(in_diff)} 件，digest 只列出 {MAX_ENTRIES} 件；"
                f"完整結果在 {artifact.name}。"
            )
        sub[name] = {
            "status": status_block["status"],
            "exit_code": status_block["exit_code"],
            "skipped_reason": status_block["skipped_reason"],
            "counts": _lint_counts(len(in_diff), len(outside), attribute),
            "entries": capped,
            "truncated": truncated,
            "notes": sub_notes,
        }
        # ty reports which inference mode it ran in, and what it set aside as a
        # consequence. Both belong in the digest: a scan that could not resolve
        # third-party types has to say so rather than read as a clean run.
        if status_block.get("mode"):
            sub[name]["mode"] = status_block["mode"]
        suppressed = status_block.get("suppressed") or []
        if suppressed:
            suppressed_capped, _ = cap_entries(suppressed)
            sub[name]["counts"]["suppressed"] = len(suppressed)
            sub[name]["suppressed"] = suppressed_capped

    statuses = {block["status"] for block in sub.values()}
    # Worst outcome wins. The envelope is what a reader who never opens `sub`
    # acts on, and taking the best sub-result meant a crashed ty next to a
    # working ruff arrived labelled `ok` with an empty reason — the exact
    # "silence is not a clean bill of health" failure this runner exists to
    # prevent. Per-tool detail still lives in `sub`, so nothing is lost by
    # reporting the envelope pessimistically.
    if "error" in statuses:
        status = "error"
    elif "skipped" in statuses:
        status = "skipped"
    else:
        status = "ok"

    skipped_reason = ""
    if status != "ok":
        # Every non-ok sub-tool is named, not only the ones that filled in a
        # reason: a tool that failed without explaining itself is precisely the
        # one a reader must not be left to infer.
        parts = [
            f"{name}：{block['skipped_reason'] or '（未附理由）'}"
            for name, block in sub.items()
            if block["status"] != "ok"
        ]
        skipped_reason = "；".join(parts)

    if total_out:
        notes.append(f"專案既有問題合計 {total_out} 件，僅計數揭露，不列為本次發現。")
    if attribute and not total_in:
        notes.append("本次變更行內沒有 lint / 型別診斷。")

    combined, truncated = cap_entries(all_in_diff)

    try:
        artifact.write_text(
            json.dumps(raw_bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        notes.append(f"無法寫入原始輸出封存檔 {artifact.name}：{exc}")

    return build_digest(
        "lint",
        status=status,
        exit_code=None,  # three independent tools; see sub[*].exit_code
        artifact=str(artifact),
        counts=_lint_counts(total_in, total_out, attribute),
        skipped_reason=skipped_reason,
        entries=combined,
        truncated=truncated,
        notes=notes,
        extra={"sub": sub},
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_runner.py",
        description=(
            "Run one external scanner, archive its full output under --out, "
            "and print a compact JSON digest on stdout."
        ),
    )
    parser.add_argument(
        "tool",
        choices=("trivy", "opengrep", "lint"),
        help="which scanner to run",
    )
    parser.add_argument("--root", required=True, help="repository root to scan")
    parser.add_argument(
        "--out",
        required=True,
        help="archive path prefix; .trivy.json / .opengrep.json / .lint.json is appended",
    )
    parser.add_argument(
        "--diff",
        help="unified diff file; drives lint attribution and opengrep rule selection",
    )
    args = parser.parse_args(argv)

    try:
        root = Path(args.root).expanduser().resolve()
        if not root.is_dir():
            raise UsageError(f"--root 不是目錄：{root}")

        out_prefix = Path(args.out).expanduser().resolve()
        if out_prefix.is_dir():
            raise UsageError(f"--out 必須是路徑前綴而非目錄：{out_prefix}")

        diff_path: Path | None = None
        if args.diff:
            diff_path = Path(args.diff).expanduser().resolve()
            if not diff_path.is_file():
                raise UsageError(f"--diff 檔案不存在：{diff_path}")

        if args.tool == "trivy":
            digest = run_trivy(root, out_prefix)
        elif args.tool == "opengrep":
            digest = run_opengrep(root, out_prefix, diff_path)
        else:
            digest = run_lint(root, out_prefix, diff_path)
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(digest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
