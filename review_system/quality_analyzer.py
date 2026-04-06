"""QualityAnalyzer — coverage, docs, code smells, technical debt (Task 7)."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from review_system.models import (
    DocumentationScore,
    QualityMetrics,
    CoverageResult,
)


# ── Test coverage (via pytest-cov / coverage.py) ─────────────────────────────

def evaluar_cobertura_tests(project_path: Path) -> CoverageResult:
    """Run coverage analysis for the project (best-effort)."""
    src_dir = project_path / "src"
    test_dir = project_path / "tests"
    if not test_dir.is_dir():
        return CoverageResult()

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "report",
                "--format=total",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_path),
            timeout=300,
        )
        line_cov = 0.0
        for tok in result.stdout.strip().split():
            try:
                line_cov = float(tok)
            except ValueError:
                continue
    except Exception:
        line_cov = 0.0

    # List src files without any test counterpart
    uncovered: list[str] = []
    if src_dir.is_dir():
        for f in sorted(src_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            test_name = f"test_{f.stem}.py"
            if not (test_dir / test_name).exists():
                uncovered.append(f.name)

    return CoverageResult(
        line_coverage=line_cov,
        branch_coverage=0.0,
        function_coverage=0.0,
        uncovered_files=uncovered,
        critical_uncovered=[u for u in uncovered if u in _CRITICAL_MODULES],
    )


_CRITICAL_MODULES = {
    "session.py",
    "navigator.py",
    "combat_manager.py",
    "hpmp_detector.py",
    "script_executor.py",
}


# ── Documentation ─────────────────────────────────────────────────────────────

def evaluar_documentacion(files: list[Path], project_path: Path) -> DocumentationScore:
    total_funcs = 0
    documented_funcs = 0
    total_classes = 0
    documented_classes = 0
    total_code_lines = 0
    comment_lines = 0
    missing: list[str] = []

    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except Exception:
            continue

        for line in source.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                total_code_lines += 1
            if stripped.startswith("#"):
                comment_lines += 1

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total_funcs += 1
                if ast.get_docstring(node):
                    documented_funcs += 1
                else:
                    try:
                        rel = f.relative_to(project_path)
                    except ValueError:
                        rel = f
                    missing.append(f"{rel}::{node.name}")
            elif isinstance(node, ast.ClassDef):
                total_classes += 1
                if ast.get_docstring(node):
                    documented_classes += 1
                else:
                    try:
                        rel = f.relative_to(project_path)
                    except ValueError:
                        rel = f
                    missing.append(f"{rel}::{node.name}")

    ds_cov = (documented_funcs / total_funcs * 100) if total_funcs else 0.0
    cls_cov = (documented_classes / total_classes * 100) if total_classes else 0.0
    combined = (ds_cov + cls_cov) / 2 if total_classes else ds_cov

    # README quality
    readme_score = 0.0
    for name in ("README.md", "readme.md", "README.rst"):
        readme = project_path / name
        if readme.exists():
            text = readme.read_text(encoding="utf-8", errors="replace")
            sections = text.count("#")
            length = len(text)
            readme_score = min(100.0, sections * 10 + length / 100)
            break

    inline_ratio = (comment_lines / max(total_code_lines, 1)) * 100

    return DocumentationScore(
        docstring_coverage=round(combined, 1),
        readme_quality=round(readme_score, 1),
        inline_comments_ratio=round(inline_ratio, 1),
        missing_docs=missing[:50],
    )


# ── Code smells ───────────────────────────────────────────────────────────────

def detectar_code_smells(files: list[Path]) -> list[str]:
    smells: list[str] = []
    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nlines = (node.end_lineno or node.lineno) - node.lineno
                if nlines > 50:
                    smells.append(
                        f"[long-function] {f.name}::{node.name} ({nlines} lines)"
                    )
                nparams = len(node.args.args)
                if nparams > 5:
                    smells.append(
                        f"[many-params] {f.name}::{node.name} ({nparams} params)"
                    )
            elif isinstance(node, ast.ClassDef):
                nlines = (node.end_lineno or node.lineno) - node.lineno
                if nlines > 500:
                    smells.append(
                        f"[large-class] {f.name}::{node.name} ({nlines} lines)"
                    )
    return smells


# ── Technical debt estimation ─────────────────────────────────────────────────

def estimar_deuda_tecnica(smells: list[str], missing_docs: int) -> float:
    """Rough estimate of tech-debt in hours."""
    hours = 0.0
    for s in smells:
        if "[long-function]" in s:
            hours += 1.0
        elif "[many-params]" in s:
            hours += 0.5
        elif "[large-class]" in s:
            hours += 2.0
    hours += missing_docs * 0.1
    return round(hours, 1)


# ── Full analysis ─────────────────────────────────────────────────────────────

def evaluar_calidad(files: list[Path], project_path: Path) -> QualityMetrics:
    coverage = evaluar_cobertura_tests(project_path)
    docs = evaluar_documentacion(files, project_path)
    smells = detectar_code_smells(files)
    debt = estimar_deuda_tecnica(smells, len(docs.missing_docs))

    # Overall score: blend of coverage, docs, and inverse smells
    cov_score = coverage.line_coverage  # 0-100
    doc_score = docs.docstring_coverage  # 0-100
    smell_penalty = min(50, len(smells) * 2)
    overall = max(0.0, (cov_score * 0.4 + doc_score * 0.4 + 50 * 0.2) - smell_penalty)

    return QualityMetrics(
        overall_score=round(overall, 1),
        test_coverage=coverage,
        documentation=docs,
        code_smells=smells,
        technical_debt_hours=debt,
    )
