"""ErrorDetector — static analysis, type checking, security scanning (Task 8)."""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

from review_system.exceptions import SecurityException
from review_system.models import ErrorReport, Issue, Severity

_TOOL_WHITELIST = {"pylint", "flake8", "mypy", "bandit"}
_TOOL_TIMEOUT = 300  # 5 minutes


def _run_tool(tool: str, args: list[str], cwd: str) -> str:
    """Run an external tool safely."""
    if tool not in _TOOL_WHITELIST:
        raise SecurityException(f"Tool not whitelisted: {tool}")
    exe = shutil.which(tool) or shutil.which(tool, path=str(Path(sys.executable).parent))
    if exe is None:
        return ""
    try:
        result = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=_TOOL_TIMEOUT,
            cwd=cwd,
        )
        return result.stdout + "\n" + result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


# ── Static analysis with flake8 ──────────────────────────────────────────────

_SEV_MAP: dict[str, Severity] = {
    "E": Severity.HIGH,
    "W": Severity.MEDIUM,
    "F": Severity.CRITICAL,
    "C": Severity.LOW,
    "N": Severity.INFO,
}


def ejecutar_flake8(files: list[Path], cwd: str) -> list[Issue]:
    """Run flake8 and parse issues."""
    if not files:
        return []
    output = _run_tool("flake8", [str(f) for f in files], cwd)
    if not output.strip():
        return []
    issues: list[Issue] = []
    for line in output.splitlines():
        m = re.match(r"(.+?):(\d+):(\d+): ([A-Z]\d+) (.+)", line)
        if m:
            code_letter = m.group(4)[0]
            issues.append(
                Issue(
                    file=m.group(1),
                    line=int(m.group(2)),
                    column=int(m.group(3)),
                    severity=_SEV_MAP.get(code_letter, Severity.INFO),
                    category="flake8",
                    message=f"{m.group(4)}: {m.group(5)}",
                    suggestion="",
                )
            )
    return issues


# ── Type checking with mypy ───────────────────────────────────────────────────

def verificar_tipos(files: list[Path], cwd: str) -> list[Issue]:
    if not files:
        return []
    output = _run_tool("mypy", ["--no-color-output", *[str(f) for f in files]], cwd)
    if not output.strip():
        return []
    issues: list[Issue] = []
    for line in output.splitlines():
        m = re.match(r"(.+?):(\d+): error: (.+)", line)
        if m:
            issues.append(
                Issue(
                    file=m.group(1),
                    line=int(m.group(2)),
                    column=0,
                    severity=Severity.HIGH,
                    category="mypy",
                    message=m.group(3),
                )
            )
    return issues


# ── Security scanning with bandit ─────────────────────────────────────────────

def escanear_seguridad(files: list[Path], cwd: str) -> list[Issue]:
    if not files:
        return []
    output = _run_tool("bandit", ["-r", "-f", "custom", "--msg-template",
                                   "{relpath}:{line}: {test_id}[{severity}]: {msg}",
                                   *[str(f) for f in files]], cwd)
    if not output.strip():
        return []
    issues: list[Issue] = []
    for line in output.splitlines():
        m = re.match(r"(.+?):(\d+): (\w+)\[(\w+)]: (.+)", line)
        if m:
            sev_str = m.group(4).upper()
            sev = Severity.HIGH if sev_str == "HIGH" else (
                Severity.CRITICAL if sev_str == "CRITICAL" else Severity.MEDIUM
            )
            issues.append(
                Issue(
                    file=m.group(1),
                    line=int(m.group(2)),
                    column=0,
                    severity=sev,
                    category="bandit",
                    message=f"{m.group(3)}: {m.group(5)}",
                )
            )
    return issues


# ── Syntax checking ──────────────────────────────────────────────────────────

def verificar_sintaxis(files: list[Path]) -> list[Issue]:
    issues: list[Issue] = []
    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=str(f))
        except SyntaxError as e:
            issues.append(
                Issue(
                    file=str(f),
                    line=e.lineno or 0,
                    column=e.offset or 0,
                    severity=Severity.CRITICAL,
                    category="syntax",
                    message=str(e.msg),
                    suggestion="Fix the syntax error to allow further analysis",
                )
            )
    return issues


# ── Full detection ────────────────────────────────────────────────────────────

def detectar_errores(
    files: list[Path],
    cwd: str,
    enable_security: bool = True,
) -> ErrorReport:
    syntax = verificar_sintaxis(files)
    # Only analyse files without syntax errors
    valid = [f for f in files if not any(i.file == str(f) for i in syntax)]
    style = ejecutar_flake8(valid, cwd)
    type_errors = verificar_tipos(valid, cwd)
    security: list[Issue] = []
    if enable_security:
        security = escanear_seguridad(valid, cwd)
    return ErrorReport(
        syntax_errors=syntax,
        type_errors=type_errors,
        security_issues=security,
        style_issues=style,
    )
