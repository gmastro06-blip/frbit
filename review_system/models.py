"""Pydantic data models for the review system."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Priority(int, Enum):
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


class Category(str, Enum):
    ARCHITECTURE = "architecture"
    CODE_QUALITY = "code_quality"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    SECURITY = "security"
    PERFORMANCE = "performance"


class ArchitecturePattern(str, Enum):
    MVC = "mvc"
    LAYERED = "layered"
    EVENT_DRIVEN = "event_driven"
    MICROKERNEL = "microkernel"
    PIPELINE = "pipeline"


class ReportFormat(str, Enum):
    HTML = "html"
    JSON = "json"
    MARKDOWN = "markdown"


# ── Config ────────────────────────────────────────────────────────────────────

class ReviewConfig(BaseModel):
    project_path: Path
    include_patterns: list[str] = Field(default_factory=lambda: ["**/*.py"])
    exclude_patterns: list[str] = Field(default_factory=list)
    analysis_depth: str = "standard"  # quick | standard | deep
    enable_security_scan: bool = True
    enable_performance_analysis: bool = False

    model_config = {"arbitrary_types_allowed": True}


# ── Issue ─────────────────────────────────────────────────────────────────────

class Issue(BaseModel):
    file: str
    line: int = 0
    column: int = 0
    severity: Severity
    category: str
    message: str
    suggestion: str = ""


# ── Code metrics ──────────────────────────────────────────────────────────────

class FileMetrics(BaseModel):
    path: str
    total_lines: int = 0
    code_lines: int = 0
    comment_lines: int = 0
    blank_lines: int = 0
    cyclomatic_complexity: dict[str, int] = Field(default_factory=dict)
    cognitive_complexity: dict[str, int] = Field(default_factory=dict)


class CodeMetrics(BaseModel):
    total_lines: int = 0
    code_lines: int = 0
    comment_lines: int = 0
    blank_lines: int = 0
    cyclomatic_complexity: dict[str, int] = Field(default_factory=dict)
    cognitive_complexity: dict[str, int] = Field(default_factory=dict)
    maintainability_index: float = 0.0
    per_file_metrics: list[FileMetrics] = Field(default_factory=list)


class DuplicateBlock(BaseModel):
    lines: int
    instances: list[tuple[str, int]] = Field(default_factory=list)  # (file, start_line)


class DependencyGraph(BaseModel):
    nodes: list[str] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    circular_dependencies: list[list[str]] = Field(default_factory=list)


# ── Architecture ──────────────────────────────────────────────────────────────

class ComponentInfo(BaseModel):
    name: str
    path: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    cohesion_score: float = 0.0
    coupling_score: float = 0.0


class ArchitectureReport(BaseModel):
    detected_patterns: list[ArchitecturePattern] = Field(default_factory=list)
    components: list[ComponentInfo] = Field(default_factory=list)
    layer_violations: list[str] = Field(default_factory=list)
    god_objects: list[str] = Field(default_factory=list)
    orphan_modules: list[str] = Field(default_factory=list)


# ── Quality ───────────────────────────────────────────────────────────────────

class CoverageResult(BaseModel):
    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    function_coverage: float = 0.0
    uncovered_files: list[str] = Field(default_factory=list)
    critical_uncovered: list[str] = Field(default_factory=list)


class DocumentationScore(BaseModel):
    docstring_coverage: float = 0.0
    readme_quality: float = 0.0
    inline_comments_ratio: float = 0.0
    missing_docs: list[str] = Field(default_factory=list)


class QualityMetrics(BaseModel):
    overall_score: float = 0.0
    test_coverage: CoverageResult = Field(default_factory=CoverageResult)
    documentation: DocumentationScore = Field(default_factory=DocumentationScore)
    code_smells: list[str] = Field(default_factory=list)
    technical_debt_hours: float = 0.0


# ── Error report ──────────────────────────────────────────────────────────────

class ErrorReport(BaseModel):
    syntax_errors: list[Issue] = Field(default_factory=list)
    type_errors: list[Issue] = Field(default_factory=list)
    security_issues: list[Issue] = Field(default_factory=list)
    style_issues: list[Issue] = Field(default_factory=list)
    best_practice_violations: list[Issue] = Field(default_factory=list)

    def all_issues(self) -> list[Issue]:
        return (
            self.syntax_errors
            + self.type_errors
            + self.security_issues
            + self.style_issues
            + self.best_practice_violations
        )


# ── Recommendation ────────────────────────────────────────────────────────────

class Recommendation(BaseModel):
    id: str
    title: str
    description: str
    category: Category
    priority: Priority
    affected_files: list[str] = Field(default_factory=list)
    estimated_effort_hours: float = 1.0
    impact_score: float = 5.0
    implementation_steps: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)

    @field_validator("impact_score")
    @classmethod
    def _clamp_impact(cls, v: float) -> float:
        return max(0.0, min(10.0, v))


# ── Progress ──────────────────────────────────────────────────────────────────

class ProgressInfo(BaseModel):
    percent: float = 0.0
    phase: str = ""
    files_processed: int = 0
    files_total: int = 0


# ── ReviewResult ──────────────────────────────────────────────────────────────

class ReviewResult(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    project_name: str = ""
    project_path: str = ""
    config: ReviewConfig | None = None

    code_metrics: CodeMetrics = Field(default_factory=CodeMetrics)
    dependency_graph: DependencyGraph = Field(default_factory=DependencyGraph)
    architecture_report: ArchitectureReport = Field(default_factory=ArchitectureReport)
    quality_metrics: QualityMetrics = Field(default_factory=QualityMetrics)
    error_report: ErrorReport = Field(default_factory=ErrorReport)

    overall_health_score: float = 0.0
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    critical_issues: list[Issue] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)

    analysis_duration_seconds: float = 0.0
    files_analyzed: int = 0
    total_issues_found: int = 0

    @field_validator("overall_health_score")
    @classmethod
    def _clamp_health(cls, v: float) -> float:
        return max(0.0, min(100.0, v))

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, data: str) -> "ReviewResult":
        return cls.model_validate_json(data)
