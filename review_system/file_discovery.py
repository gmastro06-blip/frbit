"""File discovery and path validation (Task 2)."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from review_system.exceptions import (
    NoFilesFoundException,
    ProjectNotFoundException,
    ResourceLimitException,
    SecurityException,
)

MAX_FILES = 10_000
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_TOTAL_SIZE = 1024 * 1024 * 1024  # 1 GB


def validate_path(path: Path, workspace: Path | None = None) -> Path:
    """Resolve *path* and reject path-traversal attempts."""
    resolved = path.resolve()
    if ".." in path.parts:
        raise SecurityException(f"Path traversal detected in {path}")
    if workspace is not None:
        ws = workspace.resolve()
        if not str(resolved).startswith(str(ws)):
            raise SecurityException(
                f"Path {resolved} is outside workspace {ws}"
            )
    if not resolved.exists():
        raise ProjectNotFoundException(resolved)
    return resolved


def descubrir_archivos_python(
    root_path: Path,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[Path]:
    """Discover Python files under *root_path* respecting include/exclude globs."""
    if include_patterns is None:
        include_patterns = ["**/*.py"]
    if exclude_patterns is None:
        exclude_patterns = []

    resolved = root_path.resolve()
    if not resolved.is_dir():
        raise ProjectNotFoundException(resolved)

    # Gather candidate files from include patterns
    candidates: set[Path] = set()
    for pat in include_patterns:
        candidates.update(resolved.glob(pat))

    # Apply exclude patterns (matched against relative path string)
    filtered: list[Path] = []
    for f in candidates:
        if not f.is_file():
            continue
        rel = str(f.relative_to(resolved))
        if any(fnmatch.fnmatch(rel, ep) for ep in exclude_patterns):
            continue
        filtered.append(f)

    if not filtered:
        raise NoFilesFoundException(resolved, include_patterns, exclude_patterns)

    # Resource limits
    if len(filtered) > MAX_FILES:
        raise ResourceLimitException(
            f"Too many files: {len(filtered)} > {MAX_FILES}"
        )

    total_size = 0
    for f in filtered:
        sz = f.stat().st_size
        if sz > MAX_FILE_SIZE:
            raise ResourceLimitException(
                f"File too large: {f} ({sz} bytes > {MAX_FILE_SIZE})"
            )
        total_size += sz
    if total_size > MAX_TOTAL_SIZE:
        raise ResourceLimitException(
            f"Total size too large: {total_size} bytes > {MAX_TOTAL_SIZE}"
        )

    # Sorted alphabetically by resolved path
    filtered.sort()
    return filtered
