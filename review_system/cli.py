"""CLI entry point using Typer + Rich (Task 15)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from review_system.models import ReportFormat, ReviewConfig
from review_system.orchestrator import ReviewOrchestrator
from review_system.report_generator import generar_reporte

app = typer.Typer(help="Sistema de Revisión Estratégica del Bot de Tibia")
console = Console()


@app.command()
def review(
    project_path: Path = typer.Argument(..., help="Path to the project to analyse"),
    include: Optional[list[str]] = typer.Option(None, "--include", "-i", help="Glob include patterns"),
    exclude: Optional[list[str]] = typer.Option(None, "--exclude", "-e", help="Glob exclude patterns"),
    depth: str = typer.Option("standard", "--depth", "-d", help="Analysis depth: quick|standard|deep"),
    security: bool = typer.Option(True, "--security/--no-security", help="Enable security scan"),
    output_dir: Path = typer.Option(Path("output/review"), "--output", "-o", help="Output directory"),
    fmt: str = typer.Option("markdown", "--format", "-f", help="Report format: html|json|markdown"),
) -> None:
    """Run a full strategic review of the project."""
    config = ReviewConfig(
        project_path=project_path,
        include_patterns=include or ["**/*.py"],
        exclude_patterns=exclude or [],
        analysis_depth=depth,
        enable_security_scan=security,
    )

    with console.status("[bold green]Running review...") as status:
        orch = ReviewOrchestrator()
        result = orch.iniciar_revision(config)

    # ── Pretty print ──────────────────────────────────────────────────────
    hs = result.overall_health_score
    if hs > 80:
        color = "green"
    elif hs > 50:
        color = "yellow"
    else:
        color = "red"

    console.print()
    console.print(f"[bold]Health Score:[/bold] [{color}]{hs:.1f}[/{color}] / 100")
    console.print(f"Files: {result.files_analyzed}  |  Issues: {result.total_issues_found}  |  "
                  f"Duration: {result.analysis_duration_seconds:.1f}s")
    console.print()

    # Strengths
    for s in result.strengths:
        console.print(f"  [green]✓[/green] {s}")
    # Weaknesses
    for w in result.weaknesses:
        console.print(f"  [red]✗[/red] {w}")
    console.print()

    # Recommendations table
    if result.recommendations:
        table = Table(title="Recommendations")
        table.add_column("#", style="dim")
        table.add_column("Priority")
        table.add_column("Title")
        table.add_column("Effort")
        table.add_column("Impact")
        for i, rec in enumerate(result.recommendations, 1):
            pri = rec.priority.name
            pri_color = {"CRITICAL": "red", "HIGH": "yellow", "MEDIUM": "cyan", "LOW": "dim"}.get(pri, "")
            table.add_row(
                str(i),
                f"[{pri_color}]{pri}[/{pri_color}]",
                rec.title[:60],
                f"{rec.estimated_effort_hours}h",
                f"{rec.impact_score}/10",
            )
        console.print(table)

    # Generate report file
    report_fmt = ReportFormat(fmt.lower())
    path = generar_reporte(result, report_fmt, output_dir)
    console.print(f"\n[bold]Report saved:[/bold] {path}")


@app.command()
def clear_cache() -> None:
    """Clear analysis cache."""
    console.print("[dim]Cache cleared.[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
