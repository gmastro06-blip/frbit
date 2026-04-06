"""
Position resolver with configurable fallback chain.

Chains multiple position sources (MinimapRadar, LocalMinimap, OCR,
MemoryReader) and returns the first successful reading.  Tracks
per-source hit/miss statistics for telemetry.

Fase 6.2 — Fallback chain for position detection.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

import numpy as np

from .models import Coordinate

_log = logging.getLogger("wn.pr")

_HINT_MAX_AXIS_JUMP = 10
_HINT_MAX_MANHATTAN_JUMP = 4


# ── Position source protocol ────────────────────────────────────────────────
@runtime_checkable
class PositionSource(Protocol):
    """Any object that can return a coordinate from a frame."""

    def read(self, frame: np.ndarray, **kwargs: Any) -> Optional[Coordinate]: ...


# ── Source wrapper ───────────────────────────────────────────────────────────
class SourceKind(Enum):
    """Well-known source identifiers."""

    MINIMAP_RADAR = "minimap_radar"
    LOCAL_MINIMAP = "local_minimap"
    COORDINATE_OCR = "coordinate_ocr"
    MEMORY_READER = "memory_reader"
    CUSTOM = "custom"


@dataclass
class SourceStats:
    """Per-source performance counters."""

    hits: int = 0
    misses: int = 0
    total_ms: float = 0.0
    last_result: Optional[Coordinate] = field(default=None, repr=False)

    @property
    def attempts(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.attempts if self.attempts else 0.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.attempts if self.attempts else 0.0


@dataclass
class _SourceEntry:
    """Internal wrapper for a registered source."""

    name: str
    kind: SourceKind
    source: PositionSource
    enabled: bool = True
    stats: SourceStats = field(default_factory=SourceStats)
    # If True, ``frame`` is NOT passed (source reads from memory / files)
    frameless: bool = False


# ── Configuration ────────────────────────────────────────────────────────────
@dataclass
class PositionResolverConfig:
    """
    Parameters
    ----------
    max_stale_ms : float
        Maximum age (ms) of the last-known position before it expires.
        0 = never expires (always return last-known as ultimate fallback).
    log_misses : bool
        Log a warning on every full-chain miss.
    """

    max_stale_ms: float = 5000.0
    log_misses: bool = True


# ── Resolver ─────────────────────────────────────────────────────────────────
class PositionResolver:
    """
    Iterates through registered position sources in priority order.

    Usage::

        resolver = PositionResolver()
        resolver.add_source("radar", SourceKind.MINIMAP_RADAR, my_radar)
        resolver.add_source("local", SourceKind.LOCAL_MINIMAP, local_reader)

        coord = resolver.resolve(frame)
    """

    def __init__(self, config: Optional[PositionResolverConfig] = None) -> None:
        self._config = config or PositionResolverConfig()
        self._sources: List[_SourceEntry] = []
        self._lock = threading.Lock()
        self._last_coord: Optional[Coordinate] = None
        self._last_coord_ts: float = 0.0  # time.monotonic()
        self._resolve_count: int = 0

    # ── Source management ────────────────────────────────────────────────
    def add_source(
        self,
        name: str,
        kind: SourceKind,
        source: PositionSource,
        *,
        frameless: bool = False,
    ) -> None:
        """Append a source to the end of the chain (lowest priority)."""
        entry = _SourceEntry(name=name, kind=kind, source=source, frameless=frameless)
        with self._lock:
            self._sources.append(entry)
        _log.debug("Registered position source: %s (%s)", name, kind.value)

    def insert_source(
        self,
        index: int,
        name: str,
        kind: SourceKind,
        source: PositionSource,
        *,
        frameless: bool = False,
    ) -> None:
        """Insert a source at a specific position in the chain."""
        entry = _SourceEntry(name=name, kind=kind, source=source, frameless=frameless)
        with self._lock:
            self._sources.insert(index, entry)

    def remove_source(self, name: str) -> bool:
        """Remove a source by name.  Returns True if found."""
        with self._lock:
            for i, e in enumerate(self._sources):
                if e.name == name:
                    self._sources.pop(i)
                    return True
        return False

    def enable_source(self, name: str, enabled: bool = True) -> None:
        """Enable or disable a source without removing it."""
        with self._lock:
            for e in self._sources:
                if e.name == name:
                    e.enabled = enabled
                    return
        raise KeyError(f"Source not found: {name}")

    @property
    def source_names(self) -> List[str]:
        with self._lock:
            return [e.name for e in self._sources]

    @property
    def source_count(self) -> int:
        with self._lock:
            return len(self._sources)

    def _is_consistent_with_hint(
        self,
        coord: Optional[Coordinate],
        hint: Optional[Coordinate],
    ) -> bool:
        if coord is None or hint is None or coord.z != hint.z:
            return True
        dx = abs(coord.x - hint.x)
        dy = abs(coord.y - hint.y)
        return (
            dx <= _HINT_MAX_AXIS_JUMP
            and dy <= _HINT_MAX_AXIS_JUMP
            and (dx + dy) <= _HINT_MAX_MANHATTAN_JUMP
        )

    # ── Resolution ───────────────────────────────────────────────────────
    def resolve(
        self,
        frame: Optional[np.ndarray] = None,
        *,
        hint: Optional[Coordinate] = None,
        floor: Optional[int] = None,
    ) -> Optional[Coordinate]:
        """
        Try each source in order; return the first successful coordinate.

        Falls back to last-known position if all sources fail (subject to
        ``max_stale_ms``).
        """
        with self._lock:
            self._resolve_count += 1
            sources = list(self._sources)

        for entry in sources:
            if not entry.enabled:
                continue

            t0 = time.monotonic()
            coord: Optional[Coordinate] = None

            try:
                if entry.frameless:
                    coord = entry.source.read(np.empty(0))
                elif frame is not None:
                    kwargs: Dict[str, Any] = {}
                    if hint is not None:
                        kwargs["hint"] = hint
                    if floor is not None:
                        kwargs["floor"] = floor
                    coord = entry.source.read(frame, **kwargs)
            except Exception:
                _log.debug("Source %s raised, skipping", entry.name, exc_info=True)

            elapsed = (time.monotonic() - t0) * 1000
            with self._lock:
                entry.stats.total_ms += elapsed

            if coord is not None:
                if not self._is_consistent_with_hint(coord, hint):
                    with self._lock:
                        entry.stats.misses += 1
                    continue
                with self._lock:
                    entry.stats.hits += 1
                    entry.stats.last_result = coord
                    self._last_coord = coord
                    self._last_coord_ts = time.monotonic()
                return coord
            else:
                with self._lock:
                    entry.stats.misses += 1

        # All sources failed — try last-known
        with self._lock:
            last = self._last_coord
            last_ts = self._last_coord_ts
        if last is not None:
            if not self._is_consistent_with_hint(last, hint):
                last = None
            elif self._config.max_stale_ms <= 0:
                return last
            else:
                age_ms = (time.monotonic() - last_ts) * 1000
                if age_ms <= self._config.max_stale_ms:
                    return last

        if self._config.log_misses:
            _log.warning("All position sources failed (resolve #%d)", self._resolve_count)
        return None

    # ── Stats ────────────────────────────────────────────────────────────
    @property
    def last_coordinate(self) -> Optional[Coordinate]:
        with self._lock:
            return self._last_coord

    @property
    def resolve_count(self) -> int:
        with self._lock:
            return self._resolve_count

    def stats_snapshot(self) -> Dict[str, Any]:
        """Return per-source statistics for telemetry / GUI."""
        with self._lock:
            resolve_count = self._resolve_count
            last_coord = self._last_coord
            sources = list(self._sources)
        result: Dict[str, Any] = {
            "resolve_count": resolve_count,
            "last_coord": last_coord,
            "sources": {},
        }
        for e in sources:
            result["sources"][e.name] = {
                "kind": e.kind.value,
                "enabled": e.enabled,
                "hits": e.stats.hits,
                "misses": e.stats.misses,
                "hit_rate": round(e.stats.hit_rate, 3),
                "avg_ms": round(e.stats.avg_ms, 2),
                "last_result": e.stats.last_result,
            }
        return result

    def reset_stats(self) -> None:
        """Clear all counters."""
        with self._lock:
            for e in self._sources:
                e.stats = SourceStats()
            self._resolve_count = 0
