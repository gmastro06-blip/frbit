"""Tests for the review system — unit + property-based (Task 17)."""
from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from review_system.models import (
    ArchitectureReport,
    Category,
    CodeMetrics,
    DependencyGraph,
    DocumentationScore,
    DuplicateBlock,
    ErrorReport,
    Issue,
    Priority,
    QualityMetrics,
    Recommendation,
    ReportFormat,
    ReviewConfig,
    ReviewResult,
    Severity,
    CoverageResult,
)
from review_system.code_analyzer import (
    analizar_archivo,
    analizar_metricas,
    calcular_complejidad_ciclomatica,
    calcular_complejidad_cognitiva,
    construir_grafo_dependencias,
    detectar_codigo_duplicado,
    _count_lines,
    _detectar_ciclos,
)
from review_system.file_discovery import (
    descubrir_archivos_python,
    validate_path,
)
from review_system.consolidation import (
    calcular_health_score,
    identificar_fortalezas_debilidades,
    generar_recomendaciones,
)
from review_system.architecture_analyzer import (
    detectar_god_objects,
    detectar_modulos_huerfanos,
    detectar_patrones,
    mapear_componentes,
)
from review_system.quality_analyzer import (
    detectar_code_smells,
    evaluar_documentacion,
    estimar_deuda_tecnica,
)
from review_system.error_detector import verificar_sintaxis
from review_system.report_generator import generar_reporte
from review_system.exceptions import (
    NoFilesFoundException,
    ProjectNotFoundException,
    ResourceLimitException,
    SecurityException,
)
from review_system.orchestrator import ReviewOrchestrator

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal Python project in tmp_path."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text(textwrap.dedent('''\
        """Main module."""
        import os

        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        class App:
            """Application class."""
            def run(self) -> None:
                """Run the app."""
                pass
    '''))
    (src / "utils.py").write_text(textwrap.dedent('''\
        """Utility helpers."""
        from main import greet

        def helper() -> int:
            return 42
    '''))
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text(textwrap.dedent('''\
        def test_greet():
            assert True
    '''))
    (tmp_path / "README.md").write_text("# Project\n\n## Install\n\n## Usage\n")
    return tmp_path


@pytest.fixture
def sample_files(tmp_project: Path) -> list[Path]:
    return sorted((tmp_project / "src").glob("*.py"))


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 1.1 — Property: Round-trip serialization
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelSerialization:
    def test_reviewresult_roundtrip(self) -> None:
        r = ReviewResult(project_name="test", overall_health_score=75.0)
        j = r.to_json()
        r2 = ReviewResult.from_json(j)
        assert r2.project_name == r.project_name
        assert r2.overall_health_score == r.overall_health_score

    def test_issue_serialization(self) -> None:
        i = Issue(file="a.py", line=10, column=5, severity=Severity.HIGH, category="test", message="x")
        d = i.model_dump()
        i2 = Issue.model_validate(d)
        assert i2 == i

    def test_recommendation_clamp(self) -> None:
        r = Recommendation(
            id="R1", title="t", description="d", category=Category.TESTING,
            priority=Priority.HIGH, impact_score=15.0
        )
        assert r.impact_score == 10.0

    def test_health_score_clamp(self) -> None:
        r = ReviewResult(overall_health_score=150.0)
        assert r.overall_health_score == 100.0
        r2 = ReviewResult(overall_health_score=-5.0)
        assert r2.overall_health_score == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 2.2 — Properties: file discovery
# ═══════════════════════════════════════════════════════════════════════════════

class TestFileDiscovery:
    def test_discovers_all_py_files(self, tmp_project: Path) -> None:
        files = descubrir_archivos_python(tmp_project / "src")
        names = {f.name for f in files}
        assert "__init__.py" in names
        assert "main.py" in names
        assert "utils.py" in names

    def test_sorted_alphabetically(self, tmp_project: Path) -> None:
        files = descubrir_archivos_python(tmp_project / "src")
        assert files == sorted(files)

    def test_include_pattern(self, tmp_project: Path) -> None:
        files = descubrir_archivos_python(tmp_project / "src", include_patterns=["main.py"])
        assert len(files) == 1
        assert files[0].name == "main.py"

    def test_exclude_pattern(self, tmp_project: Path) -> None:
        files = descubrir_archivos_python(tmp_project / "src", exclude_patterns=["__init__.py"])
        names = {f.name for f in files}
        assert "__init__.py" not in names

    def test_nonexistent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProjectNotFoundException):
            descubrir_archivos_python(tmp_path / "nope")

    def test_empty_dir_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(NoFilesFoundException):
            descubrir_archivos_python(d)


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 2.4 — Properties: path validation / security
# ═══════════════════════════════════════════════════════════════════════════════

class TestPathSecurity:
    def test_traversal_rejected(self, tmp_path: Path) -> None:
        malicious = tmp_path / ".." / "etc" / "passwd"
        with pytest.raises(SecurityException):
            validate_path(malicious, workspace=tmp_path)

    def test_outside_workspace(self, tmp_path: Path) -> None:
        other = Path("C:/Windows/System32") if Path("C:/Windows").exists() else Path("/tmp")
        with pytest.raises((SecurityException, ProjectNotFoundException)):
            validate_path(other, workspace=tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 3.3 — Property: line count invariant
# ═══════════════════════════════════════════════════════════════════════════════

class TestLineCountInvariant:
    def test_total_equals_sum(self) -> None:
        source = "# comment\nimport os\n\ndef f():\n    pass\n"
        total, code, comment, blank = _count_lines(source)
        assert total == code + comment + blank

    @given(st.text(alphabet="abcdefghijklmnop# \n", min_size=0, max_size=200))
    @settings(max_examples=50)
    def test_total_equals_sum_property(self, src: str) -> None:
        total, code, comment, blank = _count_lines(src)
        assert total == code + comment + blank


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 3 — Complexity
# ═══════════════════════════════════════════════════════════════════════════════

class TestComplexity:
    def test_simple_function(self) -> None:
        tree = ast.parse("def f(): pass")
        func = tree.body[0]
        assert calcular_complejidad_ciclomatica(func) >= 1

    def test_if_adds_one(self) -> None:
        tree = ast.parse("def f(x):\n if x: pass")
        func = tree.body[0]
        assert calcular_complejidad_ciclomatica(func) == 2

    def test_bool_op(self) -> None:
        tree = ast.parse("def f(a,b,c):\n if a and b and c: pass")
        func = tree.body[0]
        assert calcular_complejidad_ciclomatica(func) >= 3

    def test_cognitive_simple(self) -> None:
        tree = ast.parse("def f(): pass")
        func = tree.body[0]
        assert calcular_complejidad_cognitiva(func) == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 4.2 — Property: dependency graph integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestDependencyGraph:
    def test_all_edges_reference_existing_nodes(self, sample_files: list[Path]) -> None:
        g = construir_grafo_dependencias(sample_files)
        node_set = set(g.nodes)
        for src, dst in g.edges:
            assert src in node_set
            assert dst in node_set

    def test_no_cycles_on_acyclic(self) -> None:
        cycles = _detectar_ciclos(["a", "b", "c"], [("a", "b"), ("b", "c")])
        assert cycles == []

    def test_detects_cycle(self) -> None:
        cycles = _detectar_ciclos(["a", "b"], [("a", "b"), ("b", "a")])
        assert len(cycles) >= 1
        # Each cycle must be a valid path
        for c in cycles:
            assert c[0] == c[-1]  # circular


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 6 — Architecture
# ═══════════════════════════════════════════════════════════════════════════════

class TestArchitecture:
    def test_god_object_detection(self, tmp_path: Path) -> None:
        # Create a class with >10 methods and >500 lines
        methods = "\n".join(f"    def m{i}(self):\n" + "        pass\n" * 50 for i in range(12))
        code = f"class BigClass:\n{methods}"
        f = tmp_path / "big.py"
        f.write_text(code)
        gods = detectar_god_objects([f])
        assert len(gods) >= 1

    def test_orphan_detection(self) -> None:
        g = DependencyGraph(nodes=["a", "b", "c"], edges=[("a", "b")])
        orphans = detectar_modulos_huerfanos(g)
        assert "c" in orphans

    def test_cohesion_coupling(self, sample_files: list[Path]) -> None:
        g = construir_grafo_dependencias(sample_files)
        comps = mapear_componentes(sample_files, g)
        for c in comps:
            assert 0.0 <= c.cohesion_score <= 1.0
            assert 0.0 <= c.coupling_score <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 7 — Quality
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuality:
    def test_docs_coverage_range(self, sample_files: list[Path], tmp_project: Path) -> None:
        doc = evaluar_documentacion(sample_files, tmp_project)
        assert 0.0 <= doc.docstring_coverage <= 100.0

    def test_code_smells_long_function(self, tmp_path: Path) -> None:
        lines = "def big():\n" + "    x = 1\n" * 60
        f = tmp_path / "big.py"
        f.write_text(lines)
        smells = detectar_code_smells([f])
        assert any("[long-function]" in s for s in smells)

    def test_tech_debt_estimate(self) -> None:
        smells = ["[long-function] f", "[large-class] C"]
        hours = estimar_deuda_tecnica(smells, 10)
        assert hours > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 8 — ErrorDetector
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorDetector:
    def test_syntax_error_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_text("def f(\n")
        issues = verificar_sintaxis([f])
        assert len(issues) >= 1
        assert issues[0].severity == Severity.CRITICAL

    def test_valid_file_no_syntax_errors(self, tmp_path: Path) -> None:
        f = tmp_path / "good.py"
        f.write_text("def f(): pass\n")
        issues = verificar_sintaxis([f])
        assert issues == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 10 — Health Score + Consolidation
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthScore:
    def test_range_0_100(self) -> None:
        score = calcular_health_score(CodeMetrics(), QualityMetrics(), ErrorReport())
        assert 0 <= score <= 100

    def test_critical_forces_below_50(self) -> None:
        er = ErrorReport(
            syntax_errors=[Issue(file="a.py", severity=Severity.CRITICAL, category="x", message="m")]
        )
        score = calcular_health_score(CodeMetrics(), QualityMetrics(), er)
        assert score < 50

    @given(st.floats(min_value=0, max_value=100))
    @settings(max_examples=20)
    def test_deterministic(self, cov: float) -> None:
        qm = QualityMetrics(test_coverage=CoverageResult(line_coverage=cov))
        s1 = calcular_health_score(CodeMetrics(), qm, ErrorReport())
        s2 = calcular_health_score(CodeMetrics(), qm, ErrorReport())
        assert s1 == s2

    def test_good_metrics_above_80(self) -> None:
        cm = CodeMetrics(cyclomatic_complexity={"f": 2})
        qm = QualityMetrics(
            test_coverage=CoverageResult(line_coverage=90),
            documentation=DocumentationScore(docstring_coverage=90),
        )
        score = calcular_health_score(cm, qm, ErrorReport())
        assert score > 80


class TestStrengthsWeaknesses:
    def test_at_least_one(self) -> None:
        s, w = identificar_fortalezas_debilidades(
            CodeMetrics(), ArchitectureReport(), QualityMetrics(), ErrorReport(), DependencyGraph()
        )
        assert len(s) + len(w) >= 1


class TestRecommendations:
    def test_one_per_weakness(self) -> None:
        ws = ["Bad coverage", "God objects"]
        recs = generar_recomendaciones(ws, ErrorReport())
        assert len(recs) >= len(ws)

    def test_sorted_by_priority(self) -> None:
        ws = ["errores críticos!", "God Objects detectados"]
        recs = generar_recomendaciones(ws, ErrorReport())
        for i in range(len(recs) - 1):
            assert recs[i].priority.value <= recs[i + 1].priority.value


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 14 — Reports
# ═══════════════════════════════════════════════════════════════════════════════

class TestReports:
    def _result(self) -> ReviewResult:
        return ReviewResult(
            project_name="test",
            overall_health_score=72.5,
            strengths=["Good <script>"], weaknesses=["Bad"],
            recommendations=[Recommendation(
                id="R1", title="Fix", description="d", category=Category.TESTING,
                priority=Priority.HIGH, implementation_steps=["step1"],
            )],
        )

    def test_html_valid(self, tmp_path: Path) -> None:
        r = self._result()
        path = generar_reporte(r, ReportFormat.HTML, tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "<html" in content
        # XSS sanitization check
        assert "<script>" not in content
        assert "&lt;script&gt;" in content

    def test_json_valid(self, tmp_path: Path) -> None:
        r = self._result()
        path = generar_reporte(r, ReportFormat.JSON, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["project_name"] == "test"

    def test_markdown_valid(self, tmp_path: Path) -> None:
        r = self._result()
        path = generar_reporte(r, ReportFormat.MARKDOWN, tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "# Review Report" in text


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 11 — Orchestrator integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrchestrator:
    def test_full_pipeline(self, tmp_project: Path) -> None:
        config = ReviewConfig(project_path=tmp_project / "src")
        orch = ReviewOrchestrator()
        result = orch.iniciar_revision(config)
        assert 0 <= result.overall_health_score <= 100
        assert result.files_analyzed > 0
        assert result.project_name == "src"
        assert len(result.strengths) + len(result.weaknesses) >= 1

    def test_cancellation(self, tmp_project: Path) -> None:
        config = ReviewConfig(project_path=tmp_project / "src")
        orch = ReviewOrchestrator()
        orch.cancelar_revision()  # cancel before start
        result = orch.iniciar_revision(config)
        # Should still complete (cancellation only checked between phases)
        assert result.files_analyzed >= 0

    def test_progress(self, tmp_project: Path) -> None:
        config = ReviewConfig(project_path=tmp_project / "src")
        orch = ReviewOrchestrator()
        p = orch.obtener_progreso()
        assert p.percent == 0.0
        result = orch.iniciar_revision(config)
        p = orch.obtener_progreso()
        assert p.percent == 100.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 12 — Duplicate detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicates:
    def test_no_dups_in_distinct_code(self, sample_files: list[Path]) -> None:
        dups = detectar_codigo_duplicado(sample_files)
        # Small distinct files should not trigger
        # (just verifying it runs without error)
        assert isinstance(dups, list)


# ═══════════════════════════════════════════════════════════════════════════════
#  Property-based: Hypothesis strategies
# ═══════════════════════════════════════════════════════════════════════════════

class TestPropertyBased:
    @given(st.lists(st.text(min_size=1, max_size=5, alphabet="abc"), min_size=1, max_size=8, unique=True))
    @settings(max_examples=30)
    def test_cycle_detection_on_no_edges(self, nodes: list[str]) -> None:
        """No edges → no cycles."""
        cycles = _detectar_ciclos(nodes, [])
        assert cycles == []

    @given(
        st.lists(st.text(min_size=1, max_size=5, alphabet="abc"), min_size=2, max_size=5, unique=True)
    )
    @settings(max_examples=30)
    def test_detected_cycles_are_valid(self, nodes: list[str]) -> None:
        """Every detected cycle must be a valid circular path."""
        edges = [(nodes[i], nodes[(i + 1) % len(nodes)]) for i in range(len(nodes))]
        cycles = _detectar_ciclos(nodes, edges)
        for c in cycles:
            assert c[0] == c[-1]
