"""ReportGenerator — HTML, JSON, Markdown reports (Task 14)."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from review_system.models import (
    ReportFormat,
    ReviewResult,
    Severity,
)


def generar_reporte(result: ReviewResult, fmt: ReportFormat, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if fmt == ReportFormat.HTML:
        return _gen_html(result, output_dir)
    elif fmt == ReportFormat.JSON:
        return _gen_json(result, output_dir)
    else:
        return _gen_markdown(result, output_dir)


# ── JSON ──────────────────────────────────────────────────────────────────────

def _gen_json(result: ReviewResult, out: Path) -> Path:
    path = out / "review_result.json"
    path.write_text(result.to_json(), encoding="utf-8")
    return path


# ── Markdown ──────────────────────────────────────────────────────────────────

def _gen_markdown(result: ReviewResult, out: Path) -> Path:
    lines: list[str] = []
    a = lines.append
    a(f"# Review Report — {result.project_name}")
    a(f"\n**Date:** {result.timestamp:%Y-%m-%d %H:%M}")
    a(f"**Health Score:** {result.overall_health_score:.1f} / 100")
    a(f"**Files analyzed:** {result.files_analyzed}")
    a(f"**Duration:** {result.analysis_duration_seconds:.1f}s")
    a(f"**Issues found:** {result.total_issues_found}")
    a("")

    # Metrics
    a("## Code Metrics")
    cm = result.code_metrics
    a(f"| Metric | Value |")
    a(f"|--------|-------|")
    a(f"| Total lines | {cm.total_lines:,} |")
    a(f"| Code lines | {cm.code_lines:,} |")
    a(f"| Comment lines | {cm.comment_lines:,} |")
    a(f"| Blank lines | {cm.blank_lines:,} |")
    a(f"| Maintainability index | {cm.maintainability_index:.1f} |")
    a("")

    # Strengths
    a("## Strengths")
    for s in result.strengths:
        a(f"- ✅ {s}")
    a("")

    # Weaknesses
    a("## Weaknesses")
    for w in result.weaknesses:
        a(f"- ❌ {w}")
    a("")

    # Architecture
    ar = result.architecture_report
    if ar.detected_patterns:
        a("## Architecture Patterns")
        for p in ar.detected_patterns:
            a(f"- {p.value}")
        a("")

    if ar.god_objects:
        a("## God Objects")
        for g in ar.god_objects:
            a(f"- {g}")
        a("")

    # Quality
    a("## Quality")
    a(f"- Test coverage: {result.quality_metrics.test_coverage.line_coverage:.1f}%")
    a(f"- Docstring coverage: {result.quality_metrics.documentation.docstring_coverage:.1f}%")
    a(f"- Technical debt: {result.quality_metrics.technical_debt_hours:.1f}h")
    a(f"- Code smells: {len(result.quality_metrics.code_smells)}")
    a("")

    # Recommendations
    a("## Recommendations")
    for i, rec in enumerate(result.recommendations, 1):
        a(f"### {i}. [{rec.priority.name}] {rec.title}")
        a(f"  {rec.description}")
        a(f"  **Effort:** {rec.estimated_effort_hours}h | **Impact:** {rec.impact_score}/10")
        if rec.implementation_steps:
            a("  **Steps:**")
            for step in rec.implementation_steps:
                a(f"  1. {step}")
        a("")

    path = out / "review_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── HTML ──────────────────────────────────────────────────────────────────────

def _health_color(score: float) -> str:
    if score > 80:
        return "#22c55e"
    elif score > 50:
        return "#eab308"
    return "#ef4444"


def _gen_html(result: ReviewResult, out: Path) -> Path:
    hs = result.overall_health_score
    color = _health_color(hs)
    issues = result.error_report.all_issues()
    critical_count = sum(1 for i in issues if i.severity == Severity.CRITICAL)

    sections: list[str] = []
    s = sections.append

    # Header
    s(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Review — {_esc(result.project_name)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1e293b}}
h1{{border-bottom:2px solid #e2e8f0;padding-bottom:.5rem}}
h2{{color:#334155;margin-top:2rem}}
.score{{font-size:3rem;font-weight:700;color:{color}}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #e2e8f0;padding:.5rem;text-align:left}}
th{{background:#f8fafc}}
.tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.85rem;color:#fff}}
.critical{{background:#ef4444}} .high{{background:#f97316}}
.medium{{background:#eab308}} .low{{background:#3b82f6}} .info{{background:#6b7280}}
ul{{list-style:none;padding-left:0}} ul li::before{{content:"•";margin-right:.5rem}}
.str::before{{content:"✅ ";}} .weak::before{{content:"❌ ";}}
</style></head><body>""")

    s(f"<h1>Review Report — {_esc(result.project_name)}</h1>")
    s(f'<p class="score">{hs:.1f} / 100</p>')
    s(f"<p>Files: {result.files_analyzed} | Issues: {result.total_issues_found} "
      f"| Duration: {result.analysis_duration_seconds:.1f}s | "
      f"Date: {result.timestamp:%Y-%m-%d %H:%M}</p>")

    # Metrics table
    cm = result.code_metrics
    s("<h2>Code Metrics</h2><table><tr><th>Metric</th><th>Value</th></tr>")
    for label, val in [
        ("Total lines", f"{cm.total_lines:,}"),
        ("Code lines", f"{cm.code_lines:,}"),
        ("Comments", f"{cm.comment_lines:,}"),
        ("Blank", f"{cm.blank_lines:,}"),
        ("Maintainability", f"{cm.maintainability_index:.1f}"),
    ]:
        s(f"<tr><td>{label}</td><td>{val}</td></tr>")
    s("</table>")

    # Strengths / Weaknesses
    s("<h2>Strengths</h2><ul>")
    for st in result.strengths:
        s(f'<li class="str">{_esc(st)}</li>')
    s("</ul><h2>Weaknesses</h2><ul>")
    for w in result.weaknesses:
        s(f'<li class="weak">{_esc(w)}</li>')
    s("</ul>")

    # Recommendations
    s("<h2>Recommendations</h2>")
    for rec in result.recommendations:
        sev = rec.priority.name.lower()
        s(f'<h3><span class="tag {sev}">{rec.priority.name}</span> {_esc(rec.title)}</h3>')
        s(f"<p>{_esc(rec.description)}</p>")
        s(f"<p><b>Effort:</b> {rec.estimated_effort_hours}h | <b>Impact:</b> {rec.impact_score}/10</p>")
        if rec.implementation_steps:
            s("<ol>")
            for step in rec.implementation_steps:
                s(f"<li>{_esc(step)}</li>")
            s("</ol>")

    # Issues table (top 30)
    s("<h2>Issues (top 30)</h2><table><tr><th>File</th><th>Line</th><th>Sev</th><th>Message</th></tr>")
    for issue in sorted(issues, key=lambda i: i.severity.value)[:30]:
        sev = issue.severity.value
        s(f'<tr><td>{_esc(issue.file)}</td><td>{issue.line}</td>'
          f'<td><span class="tag {sev}">{sev}</span></td>'
          f'<td>{_esc(issue.message)}</td></tr>')
    s("</table>")

    s("</body></html>")

    path = out / "review_report.html"
    path.write_text("\n".join(sections), encoding="utf-8")
    return path


def _esc(s: str) -> str:
    """Sanitize output to prevent XSS."""
    return html.escape(str(s))
