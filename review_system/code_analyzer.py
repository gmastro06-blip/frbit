"""CodeAnalyzer — AST metrics, complexity, line counts, duplicates (Tasks 3-4)."""
from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from pathlib import Path

from review_system.models import (
    CodeMetrics,
    DependencyGraph,
    DuplicateBlock,
    FileMetrics,
)


# ── Complexity ────────────────────────────────────────────────────────────────

def calcular_complejidad_ciclomatica(node: ast.AST) -> int:
    """Cyclomatic complexity for a function/method AST node."""
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.While, ast.For)):
            complexity += 1
        elif isinstance(child, ast.ExceptHandler):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            complexity += len(child.ifs)
    return complexity


def _cognitive_add(node: ast.AST, depth: int) -> int:
    """Simple cognitive complexity approximation."""
    score = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While)):
            score += 1 + depth
            score += _cognitive_add(child, depth + 1)
        elif isinstance(child, ast.ExceptHandler):
            score += 1 + depth
            score += _cognitive_add(child, depth + 1)
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        else:
            score += _cognitive_add(child, depth)
    return score


def calcular_complejidad_cognitiva(node: ast.AST) -> int:
    return _cognitive_add(node, 0)


# ── Line counting ─────────────────────────────────────────────────────────────

def _count_lines(source: str) -> tuple[int, int, int, int]:
    """Return (total, code, comment, blank)."""
    total = code = comment = blank = 0
    in_docstring = False
    ds_char = '"""'
    for raw in source.splitlines():
        total += 1
        stripped = raw.strip()
        if not stripped:
            blank += 1
            continue
        # Docstring detection (simple heuristic)
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                ds_char = stripped[:3]
                if stripped.count(ds_char) >= 2:
                    comment += 1
                    continue
                in_docstring = True
                comment += 1
                continue
        else:
            comment += 1
            if ds_char in stripped:
                in_docstring = False
            continue
        if stripped.startswith("#"):
            comment += 1
        else:
            code += 1
    return total, code, comment, blank


# ── Per-file analysis ─────────────────────────────────────────────────────────

def analizar_archivo(path: Path) -> FileMetrics:
    """Analyse a single Python file and return its metrics."""
    source = path.read_text(encoding="utf-8", errors="replace")
    total, code, comment, blank = _count_lines(source)
    cyclo: dict[str, int] = {}
    cogn: dict[str, int] = {}
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return FileMetrics(
            path=str(path),
            total_lines=total,
            code_lines=code,
            comment_lines=comment,
            blank_lines=blank,
        )
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fname = f"{path.name}::{node.name}"
            cyclo[fname] = calcular_complejidad_ciclomatica(node)
            cogn[fname] = calcular_complejidad_cognitiva(node)
    return FileMetrics(
        path=str(path),
        total_lines=total,
        code_lines=code,
        comment_lines=comment,
        blank_lines=blank,
        cyclomatic_complexity=cyclo,
        cognitive_complexity=cogn,
    )


def analizar_metricas(files: list[Path]) -> CodeMetrics:
    """Aggregate metrics for all *files*."""
    per_file: list[FileMetrics] = []
    all_cyclo: dict[str, int] = {}
    all_cogn: dict[str, int] = {}
    total = code = comment = blank = 0
    for f in files:
        fm = analizar_archivo(f)
        per_file.append(fm)
        total += fm.total_lines
        code += fm.code_lines
        comment += fm.comment_lines
        blank += fm.blank_lines
        all_cyclo.update(fm.cyclomatic_complexity)
        all_cogn.update(fm.cognitive_complexity)

    # Maintainability index (simplified Halstead-free formula)
    avg_cyclo = (
        sum(all_cyclo.values()) / len(all_cyclo) if all_cyclo else 1.0
    )
    loc = max(code, 1)
    mi = max(0.0, 171 - 5.2 * _ln(loc) - 0.23 * avg_cyclo - 16.2 * _ln(loc))
    mi = min(100.0, mi * 100 / 171)

    return CodeMetrics(
        total_lines=total,
        code_lines=code,
        comment_lines=comment,
        blank_lines=blank,
        cyclomatic_complexity=all_cyclo,
        cognitive_complexity=all_cogn,
        maintainability_index=round(mi, 2),
        per_file_metrics=per_file,
    )


def _ln(x: float) -> float:
    import math
    return math.log(max(x, 1))


# ── Dependency graph ──────────────────────────────────────────────────────────

def _extract_imports(source: str) -> list[str]:
    """Extract imported module names from source code."""
    modules: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return modules
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module.split(".")[0])
    return modules


def construir_grafo_dependencias(files: list[Path]) -> DependencyGraph:
    """Build a dependency graph from a list of Python files."""
    module_names: dict[str, str] = {}  # stem -> full path
    for f in files:
        module_names[f.stem] = str(f)

    nodes = list(module_names.keys())
    edges: list[tuple[str, str]] = []
    for f in files:
        source = f.read_text(encoding="utf-8", errors="replace")
        imports = _extract_imports(source)
        for imp in imports:
            if imp in module_names and imp != f.stem:
                edges.append((f.stem, imp))
    # De-duplicate
    edges = list(set(edges))

    cycles = _detectar_ciclos(nodes, edges)
    return DependencyGraph(nodes=nodes, edges=edges, circular_dependencies=cycles)


def _detectar_ciclos(
    nodes: list[str], edges: list[tuple[str, str]]
) -> list[list[str]]:
    """Detect circular dependencies via DFS."""
    adj: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        adj[src].append(dst)

    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        for neighbour in adj.get(node, []):
            if neighbour not in visited:
                dfs(neighbour, path[:])
            elif neighbour in rec_stack:
                idx = path.index(neighbour)
                cycle = path[idx:] + [neighbour]
                cycles.append(cycle)
        rec_stack.discard(node)

    for n in nodes:
        if n not in visited:
            dfs(n, [])

    return cycles


# ── Duplicate detection ───────────────────────────────────────────────────────

_MIN_DUP_LINES = 6


def detectar_codigo_duplicado(files: list[Path]) -> list[DuplicateBlock]:
    """Detect duplicated code blocks (≥ 6 contiguous lines) across files."""
    hashes: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i in range(len(lines) - _MIN_DUP_LINES + 1):
            block = "\n".join(
                l.strip() for l in lines[i : i + _MIN_DUP_LINES] if l.strip()
            )
            if len(block) < 20:
                continue
            h = hashlib.md5(block.encode()).hexdigest()
            hashes[h].append((str(f), i + 1))

    duplicates: list[DuplicateBlock] = []
    for h, locations in hashes.items():
        if len(locations) >= 2:
            # Keep only distinct files
            seen: set[str] = set()
            unique: list[tuple[str, int]] = []
            for loc in locations:
                if loc[0] not in seen:
                    seen.add(loc[0])
                    unique.append(loc)
            if len(unique) >= 2:
                duplicates.append(
                    DuplicateBlock(lines=_MIN_DUP_LINES, instances=unique)
                )
    return duplicates
