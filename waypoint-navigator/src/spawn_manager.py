"""
Multi-spawn routing — handle occupied hunting spots with alternatives.

Fase 7.5 — Multi-spawn routing (occupied spot → alternative spot).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

_log = logging.getLogger("wn.sm")


# ── Configuration ────────────────────────────────────────────────────────────
class SpawnStatus(Enum):
    """Status of a hunting spot."""

    UNKNOWN = "unknown"
    FREE = "free"
    OCCUPIED = "occupied"
    DANGEROUS = "dangerous"
    COOLDOWN = "cooldown"


@dataclass
class SpawnPoint:
    """
    A hunting spot definition.

    Parameters
    ----------
    name : str
        Human-readable ID (e.g. "wasp_south_1").
    script : str
        Script/route file to execute at this spawn.
    priority : int
        Lower = preferred. 1 = primary, 2+ = alternatives.
    min_level : int
        Minimum character level recommended.
    expected_monsters : List[str]
        Monster names expected at this location.
    check_waypoint : List[int]
        [x, y, z] coordinate to check if spot is occupied.
    """

    name: str
    script: str = ""
    priority: int = 1
    min_level: int = 1
    expected_monsters: List[str] = field(default_factory=list)
    check_waypoint: List[int] = field(default_factory=list)


@dataclass
class SpawnManagerConfig:
    """
    Parameters
    ----------
    spawns : List[SpawnPoint]
        Available hunting spots in priority order.
    occupied_timeout_s : float
        Seconds to wait before re-checking an occupied spot.
    max_retries : int
        Times to reattempt primary spot before switching.
    switch_cooldown_s : float
        Minimum seconds between spawn switches.
    """

    spawns: List[SpawnPoint] = field(default_factory=list)
    occupied_timeout_s: float = 300.0
    max_retries: int = 2
    switch_cooldown_s: float = 60.0


# ── Manager ──────────────────────────────────────────────────────────────────
class SpawnManager:
    """
    Manage multiple hunting spawn points.

    Tracks which spots are occupied and recommends the best available.

    Usage::

        mgr = SpawnManager(config)
        mgr.mark_occupied("wasp_south_1")
        next_spawn = mgr.best_available()
    """

    def __init__(
        self,
        config: Optional[SpawnManagerConfig] = None,
        event_bus: Any = None,
    ) -> None:
        self._config = config or SpawnManagerConfig()
        self._event_bus = event_bus
        self._status: Dict[str, SpawnStatus] = {}
        self._occupied_ts: Dict[str, float] = {}
        self._current_spawn: Optional[str] = None
        self._switch_count: int = 0
        self._last_switch_ts: float = 0.0
        self._retry_counts: Dict[str, int] = {}

        # Initialize all spawns as UNKNOWN
        for sp in self._config.spawns:
            self._status[sp.name] = SpawnStatus.UNKNOWN

    # ── Status management ────────────────────────────────────────────────
    def mark_free(self, name: str) -> None:
        """Mark a spawn as free/available."""
        self._status[name] = SpawnStatus.FREE
        self._retry_counts.pop(name, None)

    def mark_occupied(self, name: str) -> None:
        """Mark a spawn as occupied by another player."""
        self._status[name] = SpawnStatus.OCCUPIED
        self._occupied_ts[name] = time.monotonic()
        self._retry_counts[name] = self._retry_counts.get(name, 0) + 1
        self._emit("e27", {"name": name})
        _log.info("Spawn '%s' marked as occupied", name)

    def mark_dangerous(self, name: str) -> None:
        """Mark a spawn as too dangerous (PK, too many monsters)."""
        self._status[name] = SpawnStatus.DANGEROUS

    def get_status(self, name: str) -> SpawnStatus:
        """Get the current status of a spawn."""
        # Check cooldown expiration
        if self._status.get(name) == SpawnStatus.OCCUPIED:
            ts = self._occupied_ts.get(name, 0.0)
            if (time.monotonic() - ts) >= self._config.occupied_timeout_s:
                self._status[name] = SpawnStatus.COOLDOWN
        return self._status.get(name, SpawnStatus.UNKNOWN)

    # ── Spawn selection ──────────────────────────────────────────────────
    def best_available(self) -> Optional[SpawnPoint]:
        """
        Return the best available spawn point.

        Picks the highest priority (lowest number) spawn that isn't
        occupied or dangerous.
        """
        candidates = []
        for sp in self._config.spawns:
            status = self.get_status(sp.name)
            if status in (SpawnStatus.FREE, SpawnStatus.UNKNOWN, SpawnStatus.COOLDOWN):
                candidates.append(sp)

        if not candidates:
            return None

        # Sort by priority (lower = better)
        candidates.sort(key=lambda s: s.priority)
        return candidates[0]

    def recommend(self, char_level: int = 0) -> Optional[SpawnPoint]:
        """Return the best spawn considering level requirements.

        Parameters
        ----------
        char_level : int
            Current character level. If 0, level filtering is skipped.
        """
        candidates = []
        for sp in self._config.spawns:
            status = self.get_status(sp.name)
            if status in (SpawnStatus.OCCUPIED, SpawnStatus.DANGEROUS):
                continue
            if char_level > 0 and sp.min_level > char_level:
                continue
            candidates.append(sp)

        if not candidates:
            return None
        candidates.sort(key=lambda s: s.priority)
        return candidates[0]

    def get_spawn_script(self, name: str) -> str:
        """Return the script/route path for a spawn, or empty string."""
        for sp in self._config.spawns:
            if sp.name == name:
                return sp.script
        return ""

    def switch_spawn(self) -> Optional[SpawnPoint]:
        """
        Switch to the next available spawn.

        Respects switch_cooldown_s. Returns the new spawn or None.
        """
        now = time.monotonic()
        if (now - self._last_switch_ts) < self._config.switch_cooldown_s:
            _log.debug("Switch cooldown active, waiting")
            return None

        # Mark current as occupied
        if self._current_spawn:
            self.mark_occupied(self._current_spawn)

        next_sp = self.best_available()
        if next_sp is not None:
            # H5-fix: save old spawn name BEFORE overwrite so event has correct 'from'
            old_spawn = self._current_spawn
            self._current_spawn = next_sp.name
            self._switch_count += 1
            self._last_switch_ts = now
            self._emit("e28", {
                "from": old_spawn,
                "to": next_sp.name,
                "priority": next_sp.priority,
            })
            _log.info("Switching to spawn '%s' (priority %d)", next_sp.name, next_sp.priority)

        return next_sp

    def should_retry_primary(self) -> bool:
        """True if primary spawn should be retried (under max_retries)."""
        if not self._config.spawns:
            return False
        primary = self._config.spawns[0].name
        retries = self._retry_counts.get(primary, 0)
        return retries < self._config.max_retries

    # ── Properties ───────────────────────────────────────────────────────
    @property
    def config(self) -> SpawnManagerConfig:
        return self._config

    @property
    def current_spawn(self) -> Optional[str]:
        return self._current_spawn

    @current_spawn.setter
    def current_spawn(self, name: str) -> None:
        self._current_spawn = name

    @property
    def switch_count(self) -> int:
        return self._switch_count

    @property
    def spawn_count(self) -> int:
        return len(self._config.spawns)

    @property
    def available_spawns(self) -> List[SpawnPoint]:
        """All spawns that aren't occupied or dangerous."""
        result = []
        for sp in self._config.spawns:
            status = self.get_status(sp.name)
            if status not in (SpawnStatus.OCCUPIED, SpawnStatus.DANGEROUS):
                result.append(sp)
        return result

    # ── Event bus ────────────────────────────────────────────────────────
    def _emit(self, event: str, data: Any) -> None:
        if self._event_bus is not None and hasattr(self._event_bus, "emit"):
            try:
                self._event_bus.emit(event, data)
            except Exception:
                pass

    def stats_snapshot(self) -> Dict[str, Any]:
        return {
            "current_spawn": self._current_spawn,
            "switch_count": self._switch_count,
            "spawn_status": {
                sp.name: self.get_status(sp.name).value
                for sp in self._config.spawns
            },
        }
