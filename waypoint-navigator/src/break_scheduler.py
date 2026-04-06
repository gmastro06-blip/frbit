"""
BreakScheduler — Human-like session breaks for anti-ban protection.

Real players don't running 24/7 without pause.  This module injects periodic
breaks into the session loop, pausing all activity for a randomised
duration to mimic natural play patterns.

Integration::

    from src.break_scheduler import BreakScheduler, BreakSchedulerConfig

    bs = BreakScheduler(config=BreakSchedulerConfig())
    bs.start()

    # In the main loop, check before every cycle:
    if bs.should_break():
        bs.execute_break(pause_fn=session.pause, resume_fn=session.resume)

    bs.stop()
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("wn.bs")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BreakSchedulerConfig:
    """Parameters for session break scheduling.

    play_min_minutes : float
        Minimum play time before a break (default 45 min).
    play_max_minutes : float
        Maximum play time before a break (default 120 min).
    break_min_minutes : float
        Minimum break duration (default 3 min).
    break_max_minutes : float
        Maximum break duration (default 15 min).
    long_break_after_hours : float
        After this many hours, take a long break (default 4 h).
    long_break_min_minutes : float
        Minimum long break duration (default 15 min).
    long_break_max_minutes : float
        Maximum long break duration (default 45 min).
    enabled : bool
        Master switch.
    """

    play_min_minutes: float = 45.0
    play_max_minutes: float = 120.0
    break_min_minutes: float = 3.0
    break_max_minutes: float = 15.0
    long_break_after_hours: float = 4.0
    long_break_min_minutes: float = 15.0
    long_break_max_minutes: float = 45.0
    max_daily_hours: float = 16.0     # stop after N hours in a 24h window (0 = unlimited)
    enabled: bool = True


# ---------------------------------------------------------------------------
# BreakScheduler
# ---------------------------------------------------------------------------

class BreakScheduler:
    """Schedule human-like session breaks.

    The scheduler tracks cumulative play time and signals when breaks
    should occur.  It does NOT own any threads — instead the owning
    session checks :meth:`should_break` and calls :meth:`execute_break`.
    """

    def __init__(
        self,
        config: Optional[BreakSchedulerConfig] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cfg = config or BreakSchedulerConfig()
        self._log_fn = log_fn

        # Timing state
        self._session_start: Optional[float] = None
        self._last_break_end: Optional[float] = None
        self._next_break_at: float = 0.0  # monotonic time for next break
        self._total_break_time: float = 0.0
        self._breaks_taken: int = 0
        self._on_break: bool = False
        self._lock = threading.Lock()
        self._stop_event: threading.Event = threading.Event()

        # History for telemetry (B5: capped to prevent unbounded growth)
        self._break_log: List[Dict[str, Any]] = []
        self._max_break_log: int = 500
        self._log_trimmed: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Mark session start and schedule the first break."""
        with self._lock:
            now = time.monotonic()
            self._session_start = now
            self._last_break_end = now
            self._schedule_next(now)
            self._log(
                f"[B] Scheduler started — first break in "
                f"~{(self._next_break_at - now) / 60:.0f} min"
            )

    def should_break(self) -> bool:
        """Return True when it's time for a break."""
        if not self._cfg.enabled or self._session_start is None:
            return False
        with self._lock:
            if self._on_break:
                return False
            # Daily playtime cap: force a break when exceeded
            if self._cfg.max_daily_hours > 0 and self._session_elapsed_hours() >= self._cfg.max_daily_hours:
                return True
            return time.monotonic() >= self._next_break_at

    def execute_break(
        self,
        pause_fn: Optional[Callable[[], None]] = None,
        resume_fn: Optional[Callable[[], None]] = None,
    ) -> float:
        """Execute a break, returning the actual break duration in seconds.

        Parameters
        ----------
        pause_fn : callable, optional
            Called before the break (e.g. stop healer/combat).
        resume_fn : callable, optional
            Called after the break (e.g. restart healer/combat).
        """
        with self._lock:
            self._on_break = True
            # Daily cap hit → take a very long "sleep" break
            daily_exceeded = (
                self._cfg.max_daily_hours > 0
                and self._session_elapsed_hours() >= self._cfg.max_daily_hours
            )
            if daily_exceeded:
                is_long = True
                # Simulate a realistic night-time break: 6-10 hours
                duration = random.uniform(6.0, 10.0) * 3600
                kind = "DAILY CAP SLEEP"
            else:
                is_long = self._is_long_break_due()
                duration = self._roll_break_duration(is_long)
                kind = "LONG BREAK" if is_long else "BREAK"
        self._log(f"[B] {kind} starting — {duration / 60:.1f} min")

        if pause_fn is not None:
            try:
                pause_fn()
            except Exception as exc:
                self._log(f"[B] pause_fn error: {exc}")

        # Sleep in small chunks so we can be interrupted via abort_break()
        self._stop_event.clear()
        start = time.monotonic()
        remaining = duration
        while remaining > 0 and not self._stop_event.is_set():
            chunk = min(remaining, random.uniform(3.5, 7.0))
            self._stop_event.wait(chunk)
            remaining -= chunk

        actual = time.monotonic() - start

        if resume_fn is not None:
            try:
                resume_fn()
            except Exception as exc:
                self._log(f"[B] resume_fn error: {exc}")

        with self._lock:
            self._on_break = False
            self._breaks_taken += 1
            self._total_break_time += actual
            now = time.monotonic()
            self._last_break_end = now
            self._break_log.append({
                "break_num": self._breaks_taken,
                "kind": kind,
                "duration_s": round(actual, 1),
                "session_elapsed_h": round(self._session_elapsed_hours(), 2),
            })
            if len(self._break_log) > self._max_break_log:
                self._break_log = self._break_log[-self._max_break_log:]
                if not self._log_trimmed:
                    self._log_trimmed = True
                    self._log(
                        f"[B] Break log trimmed to last "
                        f"{self._max_break_log} entries"
                    )
            # After a daily-cap sleep, reset session counters so
            # _session_elapsed_hours() starts fresh (prevents infinite re-trigger).
            if daily_exceeded:
                self._session_start = now
                self._total_break_time = 0.0
            self._schedule_next(now)

        self._log(
            f"[B] {kind} ended ({actual / 60:.1f} min) — "
            f"next in ~{(self._next_break_at - time.monotonic()) / 60:.0f} min"
        )
        return actual

    @property
    def on_break(self) -> bool:
        return self._on_break

    @property
    def breaks_taken(self) -> int:
        return self._breaks_taken

    def abort_break(self) -> None:
        """Interrumpe el sleep de un descanso en curso inmediatamente."""
        self._stop_event.set()

    def time_until_break(self) -> float:
        """Seconds until the next scheduled break (0 if due or disabled)."""
        if not self._cfg.enabled or self._session_start is None:
            return 0.0
        return max(0.0, self._next_break_at - time.monotonic())

    def stats_snapshot(self) -> Dict[str, Any]:
        """Return scheduler state for telemetry/dashboard."""
        with self._lock:
            return {
                "enabled": self._cfg.enabled,
                "breaks_taken": self._breaks_taken,
                "total_break_time_m": round(self._total_break_time / 60, 1),
                "on_break": self._on_break,
                "next_break_in_m": round(self.time_until_break() / 60, 1),
                "session_hours": round(self._session_elapsed_hours(), 2),
            }

    def stop(self) -> None:
        """Mark scheduler stopped (for clean stats)."""
        self._log(
            f"[B] Scheduler stopped — {self._breaks_taken} breaks, "
            f"{self._total_break_time / 60:.1f} min total break time"
        )

    # ── Private ───────────────────────────────────────────────────────────────

    def _schedule_next(self, now: float) -> None:
        """Compute the next break time using Gaussian-distributed play interval."""
        mean = (self._cfg.play_min_minutes + self._cfg.play_max_minutes) / 2
        sigma = (self._cfg.play_max_minutes - self._cfg.play_min_minutes) / 4
        minutes = random.gauss(mean, sigma)
        minutes = max(self._cfg.play_min_minutes, min(self._cfg.play_max_minutes, minutes))
        self._next_break_at = now + minutes * 60

    def _roll_break_duration(self, is_long: bool) -> float:
        """Return break duration in seconds with Gaussian distribution."""
        if is_long:
            lo, hi = self._cfg.long_break_min_minutes, self._cfg.long_break_max_minutes
        else:
            lo, hi = self._cfg.break_min_minutes, self._cfg.break_max_minutes
        mean = (lo + hi) / 2
        sigma = (hi - lo) / 4
        minutes = random.gauss(mean, sigma)
        minutes = max(lo, min(hi, minutes))
        return minutes * 60

    def _is_long_break_due(self) -> bool:
        """True if cumulative play time exceeds the long-break threshold."""
        return self._session_elapsed_hours() >= self._cfg.long_break_after_hours

    def _session_elapsed_hours(self) -> float:
        """Total session time in hours (excluding break time)."""
        if self._session_start is None:
            return 0.0
        total = time.monotonic() - self._session_start
        play = total - self._total_break_time
        return max(0.0, play / 3600)

    def _log(self, msg: str) -> None:
        if self._log_fn is not None:
            self._log_fn(msg)
        else:
            logger.info(msg)
