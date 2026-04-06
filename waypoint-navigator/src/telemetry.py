"""
src/telemetry.py — Session telemetry for Navigator.

Records per-session stats and saves an atomic JSON snapshot to disk.

JSON schema
-----------
{
  "session_id":    "2026-02-25T14:30:00",   # ISO-8601 start time
  "start_ts":      1740495000.0,             # Unix epoch
  "end_ts":        1740496800.0,             # Unix epoch (set on finish/save)
  "duration_s":    1800.0,                   # wall-clock seconds
  "route_name":    "thais_depot_to_temple",
  "steps_walked":  1250,
  "steps_failed":  12,
  "stuck_count":   5,
  "recalib_count": 2,
  "items_looted":  48,
  "depot_cycles":  3,
  "kills":         24,
  "deaths":        0,
  "errors": [
    {"ts": 1740495120.0, "msg": "OCR timeout at (32100, 31800)"}
  ]
}

Usage
-----
    from src.telemetry import TelemetrySession
    session = TelemetrySession(route_name="thais_temple_to_depot")
    session.record_step(success=True)
    session.record_stuck()
    session.save(Path("output/session_2026-02-25.json"))
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Any

logger = logging.getLogger("wn.tl")


class TelemetrySession:
    """Tracks per-run statistics and serialises them to JSON.

    All counter methods are **thread-safe by design**: the GIL protects
    individual integer increments; no separate lock is needed for a
    single-process bot.
    """

    def __init__(self, route_name: str = "") -> None:
        self._start_ts: float = time.time()
        self._end_ts: Optional[float] = None
        self.route_name: str = route_name

        # ── Counters ────────────────────────────────────────────────────────
        self.steps_walked: int = 0
        self.steps_failed: int = 0
        self.stuck_count: int = 0
        self.recalib_count: int = 0
        self.items_looted: int = 0
        self.depot_cycles: int = 0
        self.kills: int = 0
        self.deaths: int = 0

        # ── Error log ───────────────────────────────────────────────────────
        self._errors: List[dict[str, Any]] = []

    # ── Recording API ──────────────────────────────────────────────────────

    def record_step(self, success: bool = True) -> None:
        """Record one navigation step attempt."""
        if success:
            self.steps_walked += 1
        else:
            self.steps_failed += 1

    def record_stuck(self) -> None:
        """Record a stuck detection event."""
        self.stuck_count += 1

    def record_recalib(self) -> None:
        """Record a mid-walk route recalibration (A* re-run)."""
        self.recalib_count += 1

    def record_loot(self, count: int = 1) -> None:
        """Record *count* items looted."""
        if count > 0:
            self.items_looted += count

    def record_depot_cycle(self) -> None:
        """Record one completed depot cycle."""
        self.depot_cycles += 1

    def record_kill(self, count: int = 1) -> None:
        """Record *count* combat kills."""
        if count > 0:
            self.kills += count

    def record_death(self) -> None:
        """Record a player death."""
        self.deaths += 1

    def record_error(self, msg: object) -> None:
        """Append a timestamped error message to the error log."""
        self._errors.append({"ts": time.time(), "msg": str(msg)})

    # ── Derived properties ─────────────────────────────────────────────────

    @property
    def start_ts(self) -> float:
        return self._start_ts

    @property
    def end_ts(self) -> Optional[float]:
        return self._end_ts

    @property
    def duration_s(self) -> float:
        """Elapsed wall-clock seconds (auto-updates until finish() is called)."""
        end = self._end_ts if self._end_ts is not None else time.time()
        return max(0.0, end - self._start_ts)

    @property
    def session_id(self) -> str:
        """ISO-8601 string derived from the start timestamp."""
        return datetime.fromtimestamp(self._start_ts).strftime("%Y-%m-%dT%H:%M:%S")

    @property
    def errors(self) -> List[dict[str, Any]]:
        """Read-only view of the error log."""
        return list(self._errors)

    @property
    def total_steps(self) -> int:
        return self.steps_walked + self.steps_failed

    @property
    def success_rate(self) -> float:
        """Step success rate 0.0–1.0; returns 1.0 if no steps recorded."""
        total = self.total_steps
        if total == 0:
            return 1.0
        return self.steps_walked / total

    # ── Snapshot ───────────────────────────────────────────────────────────

    def finish(self) -> dict[str, Any]:
        """Seal the session (set end_ts) and return the full snapshot dict.

        Calling finish() multiple times is idempotent — the first call
        sets and locks end_ts.
        """
        if self._end_ts is None:
            self._end_ts = time.time()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        """Return current stats as a plain dict (does NOT seal end_ts)."""
        end = self._end_ts if self._end_ts is not None else time.time()
        return {
            "session_id":    self.session_id,
            "start_ts":      self._start_ts,
            "end_ts":        end,
            "duration_s":    round(max(0.0, end - self._start_ts), 3),
            "route_name":    self.route_name,
            "steps_walked":  self.steps_walked,
            "steps_failed":  self.steps_failed,
            "stuck_count":   self.stuck_count,
            "recalib_count": self.recalib_count,
            "items_looted":  self.items_looted,
            "depot_cycles":  self.depot_cycles,
            "kills":         self.kills,
            "deaths":        self.deaths,
            "errors":        list(self._errors),
        }

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        """Atomically write the current snapshot to *path* as JSON.

        Uses a temp-file + os.replace() to prevent partial writes.
        Calls finish() if the session has not been sealed yet.
        """
        if self._end_ts is None:
            self.finish()

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            # Best-effort cleanup of the temp file
            tmp.unlink(missing_ok=True)
            logger.error(
                "[Telemetry] Failed to save session to %s", path,
                exc_info=True,
            )
            raise

    @classmethod
    def load(cls, path: Path) -> "TelemetrySession":
        """Reconstruct a TelemetrySession from a saved JSON file.

        Useful for post-run analytics or merging stats.
        Returns a *sealed* session (end_ts is already set).
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        session = cls(route_name=data.get("route_name", ""))
        session._start_ts      = float(data.get("start_ts", session._start_ts))
        session._end_ts        = float(data.get("end_ts", time.time()))
        session.steps_walked   = int(data.get("steps_walked",  0))
        session.steps_failed   = int(data.get("steps_failed",  0))
        session.stuck_count    = int(data.get("stuck_count",   0))
        session.recalib_count  = int(data.get("recalib_count", 0))
        session.items_looted   = int(data.get("items_looted",  0))
        session.depot_cycles   = int(data.get("depot_cycles",  0))
        session.kills          = int(data.get("kills",         0))
        session.deaths         = int(data.get("deaths",        0))
        session._errors        = list(data.get("errors",       []))
        return session

    # ── Repr ───────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"TelemetrySession(route={self.route_name!r}, "
            f"steps={self.steps_walked}, stuck={self.stuck_count}, "
            f"duration={self.duration_s:.1f}s)"
        )
