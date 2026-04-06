"""ReviewOrchestrator — coordinates the full analysis pipeline (Task 11)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from review_system.architecture_analyzer import analizar_arquitectura
from review_system.code_analyzer import (
    analizar_metricas,
    construir_grafo_dependencias,
    detectar_codigo_duplicado,
)
from review_system.consolidation import (
    calcular_health_score,
    filtrar_issues_criticos,
    generar_recomendaciones,
    identificar_fortalezas_debilidades,
)
from review_system.error_detector import detectar_errores
from review_system.exceptions import ProjectNotFoundException
from review_system.file_discovery import descubrir_archivos_python, validate_path
from review_system.models import (
    ProgressInfo,
    ReviewConfig,
    ReviewResult,
)
from review_system.quality_analyzer import evaluar_calidad


class ReviewOrchestrator:
    """Orchestrates the complete analysis flow."""

    def __init__(self) -> None:
        self._progress = ProgressInfo()
        self._cancelled = threading.Event()
        self._cache_dir: Path | None = None

    # ── Progress ──────────────────────────────────────────────────────────

    def obtener_progreso(self) -> ProgressInfo:
        return self._progress.model_copy()

    def cancelar_revision(self) -> None:
        self._cancelled.set()

    def _update(self, phase: str, pct: float, processed: int = 0, total: int = 0) -> None:
        self._progress = ProgressInfo(
            percent=round(pct, 1),
            phase=phase,
            files_processed=processed,
            files_total=total,
        )

    # ── Cache helpers ─────────────────────────────────────────────────────

    def _cache_key(self, files: list[Path]) -> str:
        h = hashlib.sha256()
        for f in files:
            h.update(str(f).encode())
            h.update(str(f.stat().st_mtime_ns).encode())
        return h.hexdigest()

    # ── Main entry point ──────────────────────────────────────────────────

    def iniciar_revision(self, config: ReviewConfig) -> ReviewResult:
        """Execute the full review pipeline."""
        self._cancelled.clear()
        start = time.monotonic()

        # 1. Validate
        self._update("validation", 0)
        project = validate_path(config.project_path)

        # 2. Discover files
        self._update("file_discovery", 5)
        files = descubrir_archivos_python(
            project,
            config.include_patterns,
            config.exclude_patterns,
        )
        n = len(files)
        self._update("file_discovery", 10, 0, n)

        if self._cancelled.is_set():
            return self._empty_result(config, time.monotonic() - start)

        # 3. Code analysis
        self._update("code_analysis", 15, 0, n)
        code_metrics = analizar_metricas(files)
        self._update("code_analysis", 30, n, n)

        if self._cancelled.is_set():
            return self._empty_result(config, time.monotonic() - start)

        # 4. Dependency graph
        self._update("dependency_graph", 35, 0, n)
        dep_graph = construir_grafo_dependencias(files)
        self._update("dependency_graph", 45, n, n)

        if self._cancelled.is_set():
            return self._empty_result(config, time.monotonic() - start)

        # 5. Architecture
        self._update("architecture_analysis", 50, 0, n)
        arch_report = analizar_arquitectura(files, dep_graph)
        self._update("architecture_analysis", 60, n, n)

        if self._cancelled.is_set():
            return self._empty_result(config, time.monotonic() - start)

        # 6. Quality
        self._update("quality_analysis", 65, 0, n)
        quality = evaluar_calidad(files, project)
        self._update("quality_analysis", 75, n, n)

        if self._cancelled.is_set():
            return self._empty_result(config, time.monotonic() - start)

        # 7. Error detection
        self._update("error_detection", 80, 0, n)
        errors = detectar_errores(
            files,
            cwd=str(project),
            enable_security=config.enable_security_scan,
        )
        self._update("error_detection", 90, n, n)

        if self._cancelled.is_set():
            return self._empty_result(config, time.monotonic() - start)

        # 8. Consolidation
        self._update("consolidation", 92)
        strengths, weaknesses = identificar_fortalezas_debilidades(
            code_metrics, arch_report, quality, errors, dep_graph
        )
        recs = generar_recomendaciones(weaknesses, errors)
        health = calcular_health_score(code_metrics, quality, errors)
        critical = filtrar_issues_criticos(errors)

        elapsed = time.monotonic() - start
        self._update("done", 100, n, n)

        return ReviewResult(
            timestamp=datetime.now(),
            project_name=project.name,
            project_path=str(project),
            config=config,
            code_metrics=code_metrics,
            dependency_graph=dep_graph,
            architecture_report=arch_report,
            quality_metrics=quality,
            error_report=errors,
            overall_health_score=health,
            strengths=strengths,
            weaknesses=weaknesses,
            critical_issues=critical,
            recommendations=recs,
            analysis_duration_seconds=round(elapsed, 2),
            files_analyzed=n,
            total_issues_found=len(errors.all_issues()),
        )

    def _empty_result(self, config: ReviewConfig, elapsed: float) -> ReviewResult:
        return ReviewResult(
            project_name=config.project_path.name,
            project_path=str(config.project_path),
            config=config,
            analysis_duration_seconds=round(elapsed, 2),
        )
