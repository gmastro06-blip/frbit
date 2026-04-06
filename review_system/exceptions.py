"""Custom exceptions for the review system."""
from __future__ import annotations

from pathlib import Path


class ProjectNotFoundException(Exception):
    """Raised when the project path does not exist."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"Project not found: {path}")


class NoFilesFoundException(Exception):
    """Raised when no Python files are discovered."""

    def __init__(self, path: Path, include: list[str], exclude: list[str]) -> None:
        self.path = path
        self.include = include
        self.exclude = exclude
        super().__init__(
            f"No Python files found in {path} "
            f"(include={include}, exclude={exclude})"
        )


class SecurityException(Exception):
    """Raised on path traversal or disallowed tool usage."""


class ResourceLimitException(Exception):
    """Raised when resource limits are exceeded."""
