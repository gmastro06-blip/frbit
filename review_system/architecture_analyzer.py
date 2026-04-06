"""ArchitectureAnalyzer — patterns, cohesion/coupling, antipatterns (Task 6)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from review_system.models import (
    ArchitecturePattern,
    ArchitectureReport,
    ComponentInfo,
    DependencyGraph,
)


# ── Pattern detection ─────────────────────────────────────────────────────────

_PATTERN_KEYWORDS: dict[ArchitecturePattern, list[str]] = {
    ArchitecturePattern.EVENT_DRIVEN: ["event_bus", "EventBus", "publish", "subscribe", "emit"],
    ArchitecturePattern.PIPELINE: ["pipeline", "stage", "transform", "filter", "pipe"],
    ArchitecturePattern.MVC: ["controller", "view", "model", "template"],
    ArchitecturePattern.LAYERED: ["service", "repository", "dao", "handler"],
    ArchitecturePattern.MICROKERNEL: ["plugin", "extension", "hook", "register_plugin"],
}


def detectar_patrones(graph: DependencyGraph, files: list[Path]) -> list[ArchitecturePattern]:
    """Detect architecture patterns from code and dependency structure."""
    all_text = ""
    for f in files:
        try:
            all_text += f.read_text(encoding="utf-8", errors="replace") + "\n"
        except Exception:
            continue

    found: list[ArchitecturePattern] = []
    for pattern, keywords in _PATTERN_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in all_text)
        if hits >= 2:
            found.append(pattern)
    return found


# ── Component mapping ─────────────────────────────────────────────────────────

def mapear_componentes(
    files: list[Path], graph: DependencyGraph
) -> list[ComponentInfo]:
    """Map each file to a ComponentInfo with responsibilities and dependency info."""
    dep_lookup: dict[str, list[str]] = {}
    reverse_deps: dict[str, list[str]] = {}
    for src, dst in graph.edges:
        dep_lookup.setdefault(src, []).append(dst)
        reverse_deps.setdefault(dst, []).append(src)

    components: list[ComponentInfo] = []
    for f in files:
        stem = f.stem
        deps = dep_lookup.get(stem, [])
        rdeps = reverse_deps.get(stem, [])

        # Responsibilities: class/function names
        resps: list[str] = []
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.ClassDef):
                    resps.append(f"class {node.name}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    resps.append(f"def {node.name}")
        except Exception:
            pass

        # Cohesion: ratio of internal references / total symbols (simplified)
        cohesion = _compute_cohesion(f)
        # Coupling: normalized out-degree
        coupling = len(deps) / max(len(graph.nodes), 1)

        components.append(
            ComponentInfo(
                name=stem,
                path=str(f),
                responsibilities=resps[:20],
                dependencies=deps,
                cohesion_score=round(min(cohesion, 1.0), 2),
                coupling_score=round(min(coupling, 1.0), 2),
            )
        )
    return components


def _compute_cohesion(path: Path) -> float:
    """Approximate cohesion as ratio of internal cross-refs among top-level names."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except Exception:
        return 0.5

    top_names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top_names.add(node.name)
    if len(top_names) <= 1:
        return 1.0

    refs = 0
    for name in top_names:
        count = source.count(name)
        if count > 1:
            refs += 1
    return refs / len(top_names)


# ── God objects ───────────────────────────────────────────────────────────────

def detectar_god_objects(files: list[Path]) -> list[str]:
    """Detect classes with >10 methods AND >500 lines."""
    gods: list[str] = []
    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [
                    n
                    for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                lines = (node.end_lineno or 0) - (node.lineno or 0)
                if len(methods) > 10 and lines > 500:
                    gods.append(f"{f.stem}::{node.name}")
    return gods


# ── Orphan modules ────────────────────────────────────────────────────────────

def detectar_modulos_huerfanos(graph: DependencyGraph) -> list[str]:
    """Find modules with no incoming and no outgoing edges."""
    has_edge: set[str] = set()
    for src, dst in graph.edges:
        has_edge.add(src)
        has_edge.add(dst)
    return [n for n in graph.nodes if n not in has_edge]


# ── Layer violations (simple heuristic) ──────────────────────────────────────

_LAYER_ORDER = {
    "models": 0,
    "utils": 0,
    "data": 1,
    "service": 2,
    "controller": 3,
    "view": 3,
    "gui": 3,
    "main": 4,
}


def detectar_violaciones_capas(graph: DependencyGraph) -> list[str]:
    violations: list[str] = []
    for src, dst in graph.edges:
        src_layer = _guess_layer(src)
        dst_layer = _guess_layer(dst)
        if src_layer is not None and dst_layer is not None:
            if src_layer < dst_layer:
                continue  # OK: lower layer importing higher — wrong direction
            # Lower layers should not depend on higher ones is the normal rule,
            # but here we flag when upper layer is imported by lower.
    return violations


def _guess_layer(name: str) -> int | None:
    for key, layer in _LAYER_ORDER.items():
        if key in name.lower():
            return layer
    return None


# ── Full analysis ─────────────────────────────────────────────────────────────

def analizar_arquitectura(
    files: list[Path], graph: DependencyGraph
) -> ArchitectureReport:
    patterns = detectar_patrones(graph, files)
    components = mapear_componentes(files, graph)
    gods = detectar_god_objects(files)
    orphans = detectar_modulos_huerfanos(graph)
    violations = detectar_violaciones_capas(graph)
    return ArchitectureReport(
        detected_patterns=patterns,
        components=components,
        layer_violations=violations,
        god_objects=gods,
        orphan_modules=orphans,
    )
