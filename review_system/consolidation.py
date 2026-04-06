"""Consolidation — Health Score, strengths/weaknesses, recommendations (Task 10)."""
from __future__ import annotations

from collections.abc import Iterable

from review_system.models import (
    ArchitectureReport,
    Category,
    CodeMetrics,
    DependencyGraph,
    ErrorReport,
    Issue,
    Priority,
    QualityMetrics,
    Recommendation,
    Severity,
)


# ── Health Score ──────────────────────────────────────────────────────────────

def calcular_health_score(
    code_metrics: CodeMetrics,
    quality_metrics: QualityMetrics,
    error_report: ErrorReport,
) -> float:
    """Deterministic health score in [0, 100]."""
    critical_count = sum(
        1 for i in error_report.all_issues() if i.severity == Severity.CRITICAL
    )
    if critical_count > 0:
        cap = 49.0  # forced below 50
    else:
        cap = 100.0

    avg_cyclo = _avg(code_metrics.cyclomatic_complexity.values())
    # Complexity sub-score: 0-25
    if avg_cyclo <= 5:
        complex_score = 25.0
    elif avg_cyclo <= 10:
        complex_score = 15.0
    else:
        complex_score = 5.0

    # Coverage sub-score: 0-25
    cov = quality_metrics.test_coverage.line_coverage
    cov_score = min(25.0, cov / 4)  # 100% → 25

    # Errors sub-score: 0-25
    total_issues = len(error_report.all_issues())
    err_score = max(0.0, 25.0 - total_issues * 0.5)

    # Docs sub-score: 0-25
    doc_score = min(25.0, quality_metrics.documentation.docstring_coverage / 4)

    raw = complex_score + cov_score + err_score + doc_score
    return round(max(0.0, min(cap, raw)), 1)


def _avg(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


# ── Strengths & Weaknesses ────────────────────────────────────────────────────

def identificar_fortalezas_debilidades(
    code_metrics: CodeMetrics,
    architecture_report: ArchitectureReport,
    quality_metrics: QualityMetrics,
    error_report: ErrorReport,
    dependency_graph: DependencyGraph,
) -> tuple[list[str], list[str]]:
    fortalezas: list[str] = []
    debilidades: list[str] = []

    # Complexity
    avg_c = _avg(code_metrics.cyclomatic_complexity.values())
    if avg_c < 5:
        fortalezas.append(
            f"Complejidad ciclomática baja (promedio: {avg_c:.1f}). "
            "El código es fácil de entender y mantener."
        )
    elif avg_c > 10:
        debilidades.append(
            f"Complejidad ciclomática alta (promedio: {avg_c:.1f}). "
            "Considerar refactorizar funciones complejas."
        )

    # Test coverage
    cov = quality_metrics.test_coverage.line_coverage
    if cov > 80:
        fortalezas.append(
            f"Excelente cobertura de tests ({cov:.1f}%)."
        )
    elif cov < 50:
        debilidades.append(
            f"Cobertura de tests insuficiente ({cov:.1f}%)."
        )

    # Architecture patterns
    if architecture_report.detected_patterns:
        pat_str = ", ".join(p.value for p in architecture_report.detected_patterns)
        fortalezas.append(f"Patrones arquitectónicos reconocibles: {pat_str}.")

    # God objects
    if architecture_report.god_objects:
        debilidades.append(
            f"God Objects detectados: {', '.join(architecture_report.god_objects[:3])}."
        )

    # Circular deps
    if not dependency_graph.circular_dependencies:
        fortalezas.append("Sin dependencias circulares.")
    else:
        debilidades.append(
            f"{len(dependency_graph.circular_dependencies)} dependencias circulares detectadas."
        )

    # Critical issues
    crits = sum(1 for i in error_report.all_issues() if i.severity == Severity.CRITICAL)
    if crits == 0:
        fortalezas.append("Sin errores críticos detectados.")
    else:
        debilidades.append(f"{crits} errores críticos requieren atención inmediata.")

    # Docs
    ds = quality_metrics.documentation.docstring_coverage
    if ds > 70:
        fortalezas.append(f"Buena documentación ({ds:.1f}% funciones con docstring).")
    elif ds < 30:
        debilidades.append(f"Documentación insuficiente ({ds:.1f}%).")

    # Ensure at least one entry
    if not fortalezas and not debilidades:
        fortalezas.append("Proyecto analizado sin hallazgos significativos.")

    return fortalezas, debilidades


# ── Recommendations ───────────────────────────────────────────────────────────

_REC_ID = 0


def _next_id() -> str:
    global _REC_ID
    _REC_ID += 1
    return f"REC-{_REC_ID:03d}"


def generar_recomendaciones(
    weaknesses: list[str],
    error_report: ErrorReport,
) -> list[Recommendation]:
    recs: list[Recommendation] = []

    for w in weaknesses:
        cat = _categorize(w)
        pri = _prioritize(w, error_report)
        recs.append(
            Recommendation(
                id=_next_id(),
                title=w[:80],
                description=w,
                category=cat,
                priority=pri,
                estimated_effort_hours=_estimate_effort(w),
                impact_score=_estimate_impact(pri),
                implementation_steps=_suggest_steps(w),
            )
        )

    # Extra recs for critical issues
    crits = [i for i in error_report.all_issues() if i.severity == Severity.CRITICAL]
    for issue in crits[:5]:
        recs.append(
            Recommendation(
                id=_next_id(),
                title=f"Fix critical: {issue.message[:60]}",
                description=f"{issue.file}:{issue.line} — {issue.message}",
                category=Category.CODE_QUALITY,
                priority=Priority.CRITICAL,
                affected_files=[issue.file],
                estimated_effort_hours=0.5,
                impact_score=9.0,
                implementation_steps=[
                    f"Open {issue.file} at line {issue.line}",
                    f"Fix: {issue.suggestion or issue.message}",
                    "Run tests to verify",
                ],
            )
        )

    # Sort by priority (CRITICAL=1 first)
    recs.sort(key=lambda r: r.priority.value)
    return recs


def _categorize(w: str) -> Category:
    low = w.lower()
    if "cobertura" in low or "test" in low:
        return Category.TESTING
    if "document" in low or "docstring" in low:
        return Category.DOCUMENTATION
    if "god object" in low or "circular" in low or "patr" in low:
        return Category.ARCHITECTURE
    if "seguridad" in low or "security" in low or "critical" in low:
        return Category.SECURITY
    return Category.CODE_QUALITY


def _prioritize(w: str, er: ErrorReport) -> Priority:
    low = w.lower()
    if "crítico" in low or "critical" in low:
        return Priority.CRITICAL
    if "god object" in low or "circular" in low:
        return Priority.HIGH
    if "cobertura" in low and "insuficiente" in low:
        return Priority.HIGH
    return Priority.MEDIUM


def _estimate_effort(w: str) -> float:
    if "god object" in w.lower():
        return 8.0
    if "circular" in w.lower():
        return 4.0
    if "cobertura" in w.lower():
        return 6.0
    if "document" in w.lower():
        return 3.0
    return 2.0


def _estimate_impact(pri: Priority) -> float:
    return {Priority.CRITICAL: 9.5, Priority.HIGH: 7.5, Priority.MEDIUM: 5.0, Priority.LOW: 3.0}[pri]


def _suggest_steps(w: str) -> list[str]:
    low = w.lower()
    if "cobertura" in low:
        return [
            "Identificar módulos sin tests",
            "Escribir tests unitarios para funciones públicas",
            "Ejecutar coverage para verificar mejora",
        ]
    if "god object" in low:
        return [
            "Identificar responsabilidades separadas",
            "Extraer clases auxiliares",
            "Actualizar imports",
        ]
    if "circular" in low:
        return [
            "Graficar el ciclo de dependencias",
            "Introducir interfaz/protocolo para invertir la dependencia",
            "Mover código compartido a módulo base",
        ]
    if "document" in low:
        return [
            "Agregar docstrings a funciones públicas",
            "Documentar parámetros y valores de retorno",
            "Actualizar README",
        ]
    return ["Analizar la debilidad", "Planificar corrección", "Implementar y verificar"]


def filtrar_issues_criticos(error_report: ErrorReport) -> list[Issue]:
    return [i for i in error_report.all_issues() if i.severity == Severity.CRITICAL]
