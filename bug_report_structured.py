"""Reporte estructurado de bugs usando review_system.models."""

from collections import Counter

from review_system.models import Issue, Severity, Category

# ── Issues Críticos ──────────────────────────────────────────────────────────

CRITICAL_ISSUES: list[Issue] = [
]

# ── Issues High ──────────────────────────────────────────────────────────────

HIGH_ISSUES: list[Issue] = [
]

# ── Issues Medium ────────────────────────────────────────────────────────────

MEDIUM_ISSUES: list[Issue] = [
]

# ── Consolidación de Reporte ─────────────────────────────────────────────────

ALL_ISSUES = CRITICAL_ISSUES + HIGH_ISSUES + MEDIUM_ISSUES


def _estimate_health_score(critical_count: int, high_count: int, medium_count: int) -> float:
    score = 100.0 - critical_count * 25.0 - high_count * 5.0 - medium_count * 1.5
    if critical_count > 0:
        score = min(score, 49.0)
    return round(max(0.0, score), 1)

def generar_reporte_consolidado() -> list[Issue]:
    """Genera reporte consolidado usando el sistema estructurado."""
    print("=" * 80)
    print("REPORTE ESTRUCTURADO DE BUGS - WAYPOINT NAVIGATOR")
    print("=" * 80)

    # Contar por severidad
    critical_count = len([i for i in ALL_ISSUES if i.severity == Severity.CRITICAL])
    high_count = len([i for i in ALL_ISSUES if i.severity == Severity.HIGH])
    medium_count = len([i for i in ALL_ISSUES if i.severity == Severity.MEDIUM])

    print(f"\n📊 RESUMEN:")
    print(f"  • CRÍTICOS: {critical_count}")
    print(f"  • HIGH: {high_count}")
    print(f"  • MEDIUM: {medium_count}")
    print(f"  • TOTAL: {len(ALL_ISSUES)} issues")

    health_score = _estimate_health_score(critical_count, high_count, medium_count)

    print(f"  • HEALTH SCORE: {health_score}/100")

    # Issues por categoría
    print(f"\n🏗️ POR CATEGORÍA:")
    for category in Category:
        count = len([i for i in ALL_ISSUES if i.category == category.value])
        if count > 0:
            print(f"  • {category.value.replace('_', ' ').title()}: {count}")

    # Top 5 críticos/high
    print(f"\n🔥 PRIORIDAD MÁXIMA:")
    priority_issues = CRITICAL_ISSUES + HIGH_ISSUES[:3]
    for i, issue in enumerate(priority_issues, 1):
        severity_icon = "🚨" if issue.severity == Severity.CRITICAL else "⚠️"
        print(f"  {i}. {severity_icon} {issue.file}:{issue.line}")
        print(f"     {issue.message}")
        if issue.suggestion:
            print(f"     → {issue.suggestion}")
        print()

    # Impacto por archivo
    print(f"\n📁 ARCHIVOS MÁS AFECTADOS:")
    file_counts = Counter(issue.file for issue in ALL_ISSUES)

    for file, count in file_counts.most_common(8):
        file_short = file.split('/')[-1]  # Solo nombre de archivo
        print(f"  • {file_short}: {count} issues")

    print(f"\n🎯 RECOMENDACIONES INMEDIATAS:")
    print(f"  All tracked issues resolved. Codebase health at maximum.")
    print(f"  Recommended next steps: expand integration test coverage,")
    print(f"  run live field tests (niveles 1-7), and monitor production telemetry.")

    return ALL_ISSUES

if __name__ == "__main__":
    issues = generar_reporte_consolidado()