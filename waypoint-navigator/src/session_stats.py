"""
Hunting session statistics — track exp/h, loot/h, deaths, time active.

Fase 7.6 — Session stats for hunting performance monitoring.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_log = logging.getLogger("wn.ss")


# ── Configuration ────────────────────────────────────────────────────────────
@dataclass
class SessionStatsConfig:
    """
    Parameters
    ----------
    exp_per_monster : Dict[str, int]
        Expected experience per monster type.
    update_interval_s : float
        How often to recalculate rates.
    """

    exp_per_monster: Dict[str, int] = field(default_factory=dict)
    update_interval_s: float = 30.0


# ── Kill record ──────────────────────────────────────────────────────────────
@dataclass
class KillRecord:
    """Record of a single kill."""

    name: str
    timestamp: float
    exp_gained: int = 0


@dataclass
class LootRecord:
    """Record of a loot pickup."""

    items: List[str] = field(default_factory=list)
    timestamp: float = 0.0
    value_gp: int = 0


# ── Session stats tracker ───────────────────────────────────────────────────
class HuntingSessionStats:
    """
    Track and compute hunting session statistics.

    Integrates with EventBus for automatic kill/loot/death tracking.

    Usage::

        stats = HuntingSessionStats(config)
        stats.subscribe(event_bus)
        stats.start()
        # ... hunting happens ...
        report = stats.report()
    """

    def __init__(
        self,
        config: Optional[SessionStatsConfig] = None,
        event_bus: Any = None,
    ) -> None:
        self._config = config or SessionStatsConfig()
        self._event_bus = event_bus
        self._start_ts: float = 0.0
        self._end_ts: float = 0.0
        self._active: bool = False

        # Counters
        self._kills: List[KillRecord] = []
        self._loots: List[LootRecord] = []
        self._deaths: int = 0
        self._total_exp: int = 0
        self._total_loot_gp: int = 0
        self._heals_used: int = 0
        self._mana_used: int = 0
        self._spells_cast: int = 0

        # Per-monster breakdown
        self._kills_by_monster: Dict[str, int] = {}

    # ── Session lifecycle ────────────────────────────────────────────────
    def start(self) -> None:
        """Start or resume the session timer."""
        if not self._active:
            self._start_ts = time.monotonic()
            self._active = True
            _log.info("Hunting session started")

    def stop(self) -> None:
        """Pause the session timer."""
        if self._active:
            self._end_ts = time.monotonic()
            self._active = False
            _log.info("Hunting session stopped")

    def reset(self) -> None:
        """Clear all statistics."""
        self._kills.clear()
        self._loots.clear()
        self._deaths = 0
        self._total_exp = 0
        self._total_loot_gp = 0
        self._heals_used = 0
        self._mana_used = 0
        self._spells_cast = 0
        self._kills_by_monster.clear()
        self._start_ts = 0.0
        self._end_ts = 0.0
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    # ── Event recording ──────────────────────────────────────────────────
    def record_kill(self, name: str = "", exp: int = 0) -> None:
        """Record a monster kill."""
        if exp == 0 and name:
            exp = self._config.exp_per_monster.get(name, 0)

        record = KillRecord(name=name, timestamp=time.monotonic(), exp_gained=exp)
        self._kills.append(record)
        self._total_exp += exp

        if name:
            self._kills_by_monster[name] = self._kills_by_monster.get(name, 0) + 1

    def record_loot(self, items: Optional[List[str]] = None, value_gp: int = 0) -> None:
        """Record a loot pickup."""
        record = LootRecord(
            items=items or [],
            timestamp=time.monotonic(),
            value_gp=value_gp,
        )
        self._loots.append(record)
        self._total_loot_gp += value_gp

    def record_death(self) -> None:
        """Record a character death."""
        self._deaths += 1

    def record_heal(self) -> None:
        """Record a heal usage."""
        self._heals_used += 1

    def record_spell(self) -> None:
        """Record a spell cast."""
        self._spells_cast += 1

    def record_mana(self) -> None:
        """Record a mana potion usage."""
        self._mana_used += 1

    # ── EventBus integration ─────────────────────────────────────────────
    def subscribe(self, event_bus: Any) -> None:
        """Subscribe to relevant events on the bus."""
        if event_bus is None:
            return
        event_bus.subscribe("e1", self._on_kill)
        event_bus.subscribe("e2", self._on_loot)
        event_bus.subscribe("e3", self._on_death)
        event_bus.subscribe("e4", self._on_heal)
        event_bus.subscribe("e5", self._on_spell)
        event_bus.subscribe("e6", self._on_mana)

    def _on_kill(self, data: Any) -> None:
        name = data.get("name", "") if isinstance(data, dict) else str(data)
        exp = data.get("exp", 0) if isinstance(data, dict) else 0
        self.record_kill(name, exp)

    def _on_loot(self, data: Any) -> None:
        if isinstance(data, dict):
            self.record_loot(data.get("items", []), data.get("value_gp", 0))
        else:
            self.record_loot()

    def _on_death(self, data: Any) -> None:
        self.record_death()

    def _on_heal(self, data: Any) -> None:
        self.record_heal()

    def _on_spell(self, data: Any) -> None:
        self.record_spell()

    def _on_mana(self, data: Any) -> None:
        self.record_mana()

    # ── Computed stats ───────────────────────────────────────────────────
    @property
    def elapsed_s(self) -> float:
        """Total elapsed session time in seconds."""
        if self._start_ts == 0:
            return 0.0
        end = time.monotonic() if self._active else self._end_ts
        return max(0.0, end - self._start_ts)

    @property
    def elapsed_h(self) -> float:
        """Total elapsed time in hours."""
        return self.elapsed_s / 3600.0

    @property
    def total_kills(self) -> int:
        return len(self._kills)

    @property
    def total_exp(self) -> int:
        return self._total_exp

    @property
    def total_loot_gp(self) -> int:
        return self._total_loot_gp

    @property
    def deaths(self) -> int:
        return self._deaths

    @property
    def kills_per_hour(self) -> float:
        h = self.elapsed_h
        return self.total_kills / h if h > 0 else 0.0

    @property
    def exp_per_hour(self) -> float:
        h = self.elapsed_h
        return self._total_exp / h if h > 0 else 0.0

    @property
    def loot_per_hour(self) -> float:
        h = self.elapsed_h
        return self._total_loot_gp / h if h > 0 else 0.0

    @property
    def kills_by_monster(self) -> Dict[str, int]:
        return dict(self._kills_by_monster)

    # ── Report ───────────────────────────────────────────────────────────
    def report(self) -> Dict[str, Any]:
        """Generate a complete session report."""
        return {
            "active": self._active,
            "elapsed_s": round(self.elapsed_s, 1),
            "elapsed_h": round(self.elapsed_h, 3),
            "total_kills": self.total_kills,
            "total_exp": self._total_exp,
            "total_loot_gp": self._total_loot_gp,
            "deaths": self._deaths,
            "heals_used": self._heals_used,
            "spells_cast": self._spells_cast,
            "kills_per_hour": round(self.kills_per_hour, 1),
            "exp_per_hour": round(self.exp_per_hour, 0),
            "loot_gp_per_hour": round(self.loot_per_hour, 0),
            "kills_by_monster": dict(self._kills_by_monster),
        }

    def summary_text(self) -> str:
        """Human-readable session summary."""
        r = self.report()
        lines = [
            f"=== Hunting Session ===",
            f"Duration: {r['elapsed_h']:.2f}h ({r['elapsed_s']:.0f}s)",
            f"Kills: {r['total_kills']} ({r['kills_per_hour']:.0f}/h)",
            f"Exp: {r['total_exp']:,} ({r['exp_per_hour']:,.0f}/h)",
            f"Loot: {r['total_loot_gp']:,} gp ({r['loot_gp_per_hour']:,.0f} gp/h)",
            f"Deaths: {r['deaths']}",
            f"Heals: {r['heals_used']}, Spells: {r['spells_cast']}",
        ]
        if r["kills_by_monster"]:
            lines.append("--- Kills by monster ---")
            for name, count in sorted(r["kills_by_monster"].items(), key=lambda x: -x[1]):
                lines.append(f"  {name}: {count}")
        return "\n".join(lines)
