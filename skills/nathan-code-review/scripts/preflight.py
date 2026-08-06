# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Preflight environment inventory for the nathan-code-review skill.

The review pipeline is built out of optional external steps (SAST, dependency
scanning, linting, code navigation). Before running anything, it needs to know
which of those tools and credentials actually exist on this machine, so that it
can (a) skip the steps it cannot run and (b) disclose those gaps honestly in the
final report instead of silently pretending the checks passed.

This script performs that inventory only. It never fails because something is
missing: a missing tool is data, not an error.

What is checked:
  - tools on PATH (with a cheap version probe where the tool supports one)
  - the GitLab token environment variable (name only, never the value)
  - the opengrep ruleset directory

CLI:
    uv run preflight.py            # machine-readable JSON on stdout
    uv run preflight.py --human    # compact zh-TW table on stdout

Exit codes:
    0  the check ran (regardless of how much was missing)
    2  usage error (unknown flag / unexpected argument)
    1  unexpected internal failure
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

# A version probe is a local process that prints one line and exits. 5 seconds is
# generous for that while still guaranteeing the whole preflight cannot hang the
# pipeline: worst case is len(TOOLS) * 5s if every tool were to wedge.
VERSION_TIMEOUT_SECONDS = 5.0

# opengrep is the one exception: its first invocation on a cold page cache was
# measured at ~27s on this machine (warm runs are ~2s), so the default 5s would
# report a false "version unknown" for a perfectly usable binary. 30s bounds that
# one-off cost while still capping a genuine hang.
SLOW_VERSION_TIMEOUT_SECONDS = 30.0

# Matches the first "1.2" / "1.2.3" / "1.2.3-beta.1" style token in the tool's
# own version banner, which differs per tool ("git version 2.43.0", "uv 0.4.0",
# "Version: 0.72.0", "oxlint v0.9.0"). Parsing one number pattern is more robust
# than maintaining a bespoke parser per tool.
VERSION_PATTERN = re.compile(r"\d+\.\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.]+)?")

# Environment variables holding the GitLab token, in priority order. Only the
# NAME is ever reported; the value must never leave this process.
GITLAB_TOKEN_VARS: tuple[str, ...] = ("GITLAB_TOKEN", "NCR_GITLAB_TOKEN")

# Environment variable that overrides the opengrep ruleset location.
OPENGREP_RULES_VAR = "NCR_OPENGREP_RULES"

# Default opengrep ruleset checkout, relative to the user's home directory.
OPENGREP_RULES_DEFAULT_RELATIVE = "semgrep-rules"


class ToolSpec:
    """Declarative description of one external tool we probe for."""

    def __init__(
        self,
        name: str,
        category: str,
        version_flag: str | None,
        missing_reason: str,
        timeout: float = VERSION_TIMEOUT_SECONDS,
    ) -> None:
        self.name = name
        self.category = category
        # None means "this tool has no cheap version flag"; we then report
        # availability without a version rather than paying for a slow call.
        self.version_flag = version_flag
        # Human-facing (zh-TW) consequence of the tool being absent.
        self.missing_reason = missing_reason
        # Per-tool override for slow-starting binaries.
        self.timeout = timeout


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "git",
        "required",
        "--version",
        "未安裝，無法取得 diff 與 commit 資訊，review 流程無法進行",
    ),
    ToolSpec(
        "uv",
        "required",
        "--version",
        "未安裝，無法執行本 skill 的 PEP 723 scripts",
    ),
    ToolSpec(
        "trivy",
        "scanner",
        "--version",
        "未安裝，略過相依套件漏洞與 secret 掃描",
    ),
    ToolSpec(
        "opengrep",
        "scanner",
        "--version",
        "未安裝，略過 SAST 掃描",
        timeout=SLOW_VERSION_TIMEOUT_SECONDS,
    ),
    ToolSpec(
        "ruff",
        "scanner",
        "--version",
        "未安裝，略過 Python lint 檢查",
    ),
    ToolSpec(
        "ty",
        "scanner",
        "--version",
        "未安裝，略過 Python 型別檢查",
    ),
    ToolSpec(
        "oxlint",
        "scanner",
        "--version",
        "未安裝，略過 JavaScript/TypeScript lint 檢查",
    ),
    ToolSpec(
        "codegraph",
        "navigation",
        "--version",
        "未安裝，略過程式碼結構索引，改用純文字搜尋導覽",
    ),
)


def probe_version(
    executable: str, version_flag: str, timeout: float
) -> tuple[str | None, str | None]:
    """Run `executable version_flag` and extract a version string.

    Returns (version, failure_reason). Exactly one of the two is not None.
    Every foreseeable failure (missing binary, permission, hang, crash, odd
    output) is converted into a zh-TW reason string; nothing propagates.
    """
    try:
        completed = subprocess.run(  # fixed argv, shell=False
            [executable, version_flag],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"版本查詢逾時（超過 {timeout:g} 秒）"
    except OSError as exc:
        return None, f"版本查詢失敗：{exc.strerror or exc}"
    except subprocess.SubprocessError as exc:
        return None, f"版本查詢失敗：{exc}"

    # Some tools print their banner to stderr, so consider both streams.
    output = f"{completed.stdout}\n{completed.stderr}"
    match = VERSION_PATTERN.search(output)
    if match is not None:
        return match.group(0), None

    if completed.returncode != 0:
        return None, f"版本查詢以 exit code {completed.returncode} 結束，無法解析版本"
    return None, "無法從輸出解析版本字串"


def check_tool(spec: ToolSpec) -> dict[str, Any]:
    """Look one tool up on PATH and, if present, probe its version."""
    try:
        found = shutil.which(spec.name)
    except OSError as exc:
        # A broken PATH entry can raise; treat it as "not usable".
        return {
            "available": False,
            "path": None,
            "version": None,
            "category": spec.category,
            "reason": f"PATH 查詢失敗：{exc.strerror or exc}",
        }

    if found is None:
        return {
            "available": False,
            "path": None,
            "version": None,
            "category": spec.category,
            "reason": spec.missing_reason,
        }

    path = Path(found).as_posix()
    result: dict[str, Any] = {
        "available": True,
        "path": path,
        "version": None,
        "category": spec.category,
    }

    if spec.version_flag is None:
        result["reason"] = "已安裝，此工具未提供版本旗標"
        return result

    version, failure_reason = probe_version(found, spec.version_flag, spec.timeout)
    result["version"] = version
    if version is None:
        result["reason"] = f"已安裝但{failure_reason}"
    return result


def check_gitlab_token() -> dict[str, Any]:
    """Report WHICH env var holds the GitLab token, never its value."""
    for var_name in GITLAB_TOKEN_VARS:
        raw = os.environ.get(var_name)
        # An empty / whitespace-only value is treated as unset: it would fail at
        # the API call anyway, and reporting it as available would be misleading.
        if raw is not None and raw.strip() != "":
            return {"available": True, "source": var_name}

    # 沒有 token 但設了 API base override：走 proxy，PRIVATE-TOKEN 由 proxy 端
    # 注入，等同「憑證可用」——只是憑證不在這個環境裡（這正是 proxy 的目的）。
    api_base = os.environ.get("NCR_GITLAB_API_BASE", "").strip()
    if api_base:
        return {
            "available": True,
            "source": f"NCR_GITLAB_API_BASE（{api_base}，token 由 proxy 注入）",
        }

    joined = " 或 ".join(GITLAB_TOKEN_VARS)
    return {
        "available": False,
        "source": None,
        "reason": f"未設定 {joined}，略過 GitLab MR 讀寫相關步驟",
    }


def check_opengrep_rules() -> dict[str, Any]:
    """Locate the opengrep ruleset directory and report whether it exists."""
    override = os.environ.get(OPENGREP_RULES_VAR)
    if override is not None and override.strip() != "":
        rules_path = Path(override.strip()).expanduser()
        source = OPENGREP_RULES_VAR
    else:
        rules_path = Path.home() / OPENGREP_RULES_DEFAULT_RELATIVE
        source = "default"

    posix_path = rules_path.as_posix()
    try:
        exists = rules_path.is_dir()
    except OSError as exc:
        return {
            "available": False,
            "path": posix_path,
            "source": source,
            "reason": f"規則目錄無法存取：{exc.strerror or exc}",
        }

    if exists:
        return {"available": True, "path": posix_path, "source": source}

    return {
        "available": False,
        "path": posix_path,
        "source": source,
        "reason": "規則目錄不存在，opengrep 無規則可用，略過 SAST 掃描",
    }


def build_summary(
    tools: dict[str, dict[str, Any]],
    credentials: dict[str, dict[str, Any]],
    rulesets: dict[str, dict[str, Any]],
    missing: list[str],
) -> str:
    """Compose the zh-TW summary sentence that ends up in the report."""
    total = len(tools)
    available_count = total - len(missing)

    parts = [f"工具可用 {available_count}/{total}。"]
    if missing:
        parts.append(f"缺少：{'、'.join(missing)}，對應步驟將略過並於報告揭露。")
    else:
        parts.append("所有預期工具皆可用。")

    token = credentials["gitlab_token"]
    if token["available"]:
        parts.append(f"GitLab token 來自 {token['source']}。")
    else:
        parts.append("未偵測到 GitLab token，僅能進行本地 review。")

    rules = rulesets["opengrep"]
    if rules["available"]:
        parts.append(f"opengrep ruleset 位於 {rules['path']}。")
    else:
        parts.append(f"opengrep ruleset（{rules['path']}）不存在。")

    return "".join(parts)


def run_preflight() -> dict[str, Any]:
    """Collect the whole inventory into the report-shaped dictionary."""
    tools = {spec.name: check_tool(spec) for spec in TOOLS}
    credentials = {"gitlab_token": check_gitlab_token()}
    rulesets = {"opengrep": check_opengrep_rules()}
    missing = [name for name, info in tools.items() if not info["available"]]

    return {
        "tools": tools,
        "credentials": credentials,
        "rulesets": rulesets,
        "missing": missing,
        "summary": build_summary(tools, credentials, rulesets, missing),
    }


def display_width(text: str) -> int:
    """Terminal column count for `text`, counting CJK glyphs as two columns."""
    # 'W' (wide) and 'F' (fullwidth) are the East Asian classes that occupy two
    # columns in a monospaced terminal; everything else is treated as one.
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text
    )


def pad(text: str, width: int) -> str:
    """Left-align `text` to `width` display columns (never truncates)."""
    return text + " " * max(0, width - display_width(text))


def render_human(report: dict[str, Any]) -> str:
    """Render the inventory as a compact zh-TW table."""
    header = ("項目", "狀態", "版本 / 來源", "說明")
    rows: list[tuple[str, str, str, str]] = []

    for name, info in report["tools"].items():
        status = "可用" if info["available"] else "缺少"
        detail = info.get("version") or info.get("path") or "-"
        rows.append((name, status, detail, info.get("reason", "")))

    token = report["credentials"]["gitlab_token"]
    rows.append(
        (
            "gitlab_token",
            "可用" if token["available"] else "缺少",
            token["source"] or "-",
            token.get("reason", ""),
        )
    )

    rules = report["rulesets"]["opengrep"]
    rows.append(
        (
            "opengrep rules",
            "可用" if rules["available"] else "缺少",
            f"{rules['path']}（{rules['source']}）",
            rules.get("reason", ""),
        )
    )

    columns = range(len(header))
    widths = [
        max(display_width(header[i]), *(display_width(row[i]) for row in rows))
        for i in columns
    ]
    # Two spaces between columns keeps the table readable without box drawing.
    gap = "  "

    # rstrip: the last column is padded like the others, but trailing blanks add
    # nothing and make the output noisy when piped into a report.
    lines = [gap.join(pad(header[i], widths[i]) for i in columns).rstrip()]
    lines.append(gap.join("-" * widths[i] for i in columns))
    lines.extend(
        gap.join(pad(row[i], widths[i]) for i in columns).rstrip() for row in rows
    )
    lines.append("")
    lines.append(report["summary"])
    return "\n".join(lines)


USAGE = "用法：preflight.py [--human]"


def main(argv: list[str]) -> int:
    """Parse arguments, run the inventory, print the requested format."""
    human = False
    for arg in argv:
        if arg == "--human":
            human = True
        elif arg in ("-h", "--help"):
            print(USAGE)
            print("  --human   以 zh-TW 表格輸出，預設輸出 JSON")
            return 0
        else:
            print(f"錯誤：無法識別的參數 {arg!r}。{USAGE}", file=sys.stderr)
            return 2

    report = run_preflight()
    if human:
        print(render_human(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - no traceback may ever escape
        print(f"錯誤：preflight 檢查本身失敗：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
