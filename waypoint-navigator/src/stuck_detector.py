"""
StuckDetector
-------------
Monitors the player's position and declares "stuck" when no movement
is detected for a configurable duration during active walking.

Recovery strategy chain (escalating severity):
    1. **Nudge**   — send a small movement to free the character.
    2. **Escape**  — press escape hotkey / use rope / use item.
    3. **Abort**   — give up and emit ``stuck_abort`` event.

Integration::

    from src.stuck_detector import StuckDetector, StuckConfig

    sd = StuckDetector(config=StuckConfig())
    sd.set_position_getter(lambda: radar.last_coord)
    sd.set_event_bus(bus)
    sd.start()
    # ... walking ...
    sd.stop()
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.humanizer import jittered_sleep

logger = logging.getLogger("wn.sd")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class StuckConfig:
    """Tuneable parameters for stuck detection and recovery.

    stuck_timeout : float
        Seconds of zero movement before declaring "stuck" (default 8 s).
    poll_interval : float
        How often to sample position (default 0.5 s).
    nudge_retries : int
        How many random nudge attempts before escalating (default 3).
    recovery_cooldown : float
        Minimum gap between consecutive recovery attempts (default 2 s).
    max_recovery_attempts : int
        Total recovery attempts before aborting (default 10).
    enabled : bool
        Master kill-switch (default True).
    """

    stuck_timeout: float = 8.0
    poll_interval: float = 0.5
    nudge_retries: int = 3
    recovery_cooldown: float = 2.0
    max_recovery_attempts: int = 10
    enabled: bool = True
    abort_cooldown: float = 60.0  # T5: seconds before re-enabling after abort
    max_aborts: int = 3           # C: max total aborts before permanent stop


# ---------------------------------------------------------------------------
# Recovery actions
# ---------------------------------------------------------------------------

class RecoveryAction(Enum):
    """Possible recovery actions (escalating severity)."""
    REPATH = auto()
    NUDGE = auto()
    ESCAPE = auto()
    ABORT = auto()


# ---------------------------------------------------------------------------
# StuckDetector
# ---------------------------------------------------------------------------

class StuckDetector:
    """Monitor position and trigger recovery when stuck.

    The detector runs in a background daemon thread and relies on callbacks
    for position reading and movement sending.  All callbacks are optional —
    when not set the corresponding recovery step is skipped.

    Parameters
    ----------
    config : StuckConfig, optional
        Detection tunables.
    """

    def __init__(self, config: Optional[StuckConfig] = None) -> None:
        self._cfg = config or StuckConfig()

        # Callbacks (wire before start)
        self._position_getter: Optional[Callable[[], Any]] = None
        self._repath_fn: Optional[Callable[[], bool]] = None
        self._nudge_fn: Optional[Callable[[int, int], None]] = None
        self._escape_fn: Optional[Callable[[], None]] = None
        self._event_bus: Optional[Any] = None  # EventBus
        self._log_cb: Optional[Callable[[str], None]] = None

        # Lock protects mutable state shared between background thread and callers
        self._lock = threading.Lock()

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._paused = False
        self._walking = False  # only check when actively walking

        self._last_pos: Optional[Any] = None
        self._last_move_time: float = 0.0
        self._recovery_count: int = 0
        self._total_stucks: int = 0
        self._recovery_actions: Dict[str, int] = {
            "repath": 0, "nudge": 0, "escape": 0, "abort": 0,
        }
        self._target_direction: Optional[Tuple[int, int]] = None  # (dx, dy) to dest
        self._abort_time: float = 0.0  # T5: monotonic time of last abort
        self._global_abort_count: int = 0  # C: total aborts this session

    # ── Configuration ─────────────────────────────────────────────────────────

    def set_position_getter(self, fn: Callable[[], Any]) -> None:
        """Register ``() -> Coordinate | None`` for position polling."""
        self._position_getter = fn

    def set_repath_fn(self, fn: Callable[[], bool]) -> None:
        """Legacy compatibility hook.

        Dynamic replanning is disabled; older callers may still wire this
        callback without affecting the active recovery chain.
        """
        self._repath_fn = fn

    def set_nudge_fn(self, fn: Callable[[int, int], None]) -> None:
        """Register ``(dx, dy) -> None`` — send a 1-tile movement."""
        self._nudge_fn = fn

    def set_escape_fn(self, fn: Callable[[], None]) -> None:
        """Register ``() -> None`` — press escape/rope/item."""
        self._escape_fn = fn

    def set_event_bus(self, bus: Any) -> None:
        """Register EventBus for ``stuck_detected`` / ``stuck_recovered`` / ``stuck_abort`` events."""
        self._event_bus = bus

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self._log_cb = cb

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background monitoring thread."""
        if self._running:
            return
        # Ensure any previous thread has terminated before starting a new one
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._stop_event.clear()
        self._running = True
        self._last_move_time = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("[J] Detector started")

    def stop(self) -> None:
        """Stop background monitoring."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._log(
            f"[J] Detector stopped — total stucks={self._total_stucks}"
        )

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._last_move_time = time.monotonic()  # reset timer

    def set_target_direction(self, dx: int, dy: int) -> None:
        """Hint: direction toward the destination (used to bias nudge direction)."""
        self._target_direction = (dx, dy)

    def set_walking(self, walking: bool) -> None:
        """Notify the detector whether the bot is currently walking.

        Only checks for stuck while ``walking=True`` — prevents false
        positives during NPC conversations, depot cycles, waits, etc.
        """
        with self._lock:
            self._walking = walking
            if walking:
                self._last_move_time = time.monotonic()
                self._recovery_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def total_stucks(self) -> int:
        """Number of times stuck was declared this session."""
        return self._total_stucks

    @property
    def recovery_count(self) -> int:
        """Recovery attempts for the current stuck episode."""
        return self._recovery_count

    def reset_counters(self) -> None:
        with self._lock:
            self._total_stucks = 0
            self._recovery_count = 0
            self._global_abort_count = 0
            for k in self._recovery_actions:
                self._recovery_actions[k] = 0

    def stats_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "walking": self._walking,
                "total_stucks": self._total_stucks,
                "recovery_count": self._recovery_count,
                "global_abort_count": self._global_abort_count,
                "recovery_actions": dict(self._recovery_actions),
            }

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            if self._paused or not self._cfg.enabled:
                if not self._sleep(self._cfg.poll_interval, pct=0.25):
                    break
                continue

            # T5: Re-enable walking after abort cooldown
            if not self._walking and self._abort_time > 0:
                self._check_abort_cooldown()

            if not self._walking:
                if not self._sleep(self._cfg.poll_interval, pct=0.25):
                    break
                continue

            try:
                self._tick()
            except Exception as exc:
                logger.warning("[J] tick error: %s", exc)

            if not self._sleep(self._cfg.poll_interval, pct=0.25):
                break

    @staticmethod
    def _pos_moved(pos_a: Any, pos_b: Any, *, check_z: bool = False) -> bool:
        """Return True if pos_a and pos_b both exist and differ in x, y (and optionally z)."""
        if pos_a is None or pos_b is None:
            return False
        if getattr(pos_a, "x", 0) != getattr(pos_b, "x", 0):
            return True
        if getattr(pos_a, "y", 0) != getattr(pos_b, "y", 0):
            return True
        if check_z and getattr(pos_a, "z", 0) != getattr(pos_b, "z", 0):
            return True
        return False

    def _tick(self) -> None:
        """One monitoring cycle: check pos, decide stuck, trigger recovery."""
        pos = self._read_pos()
        if pos is None:
            return  # can't read position — skip

        now = time.monotonic()

        # Check if position changed
        with self._lock:
            if self._last_pos is not None:
                moved = self._pos_moved(pos, self._last_pos, check_z=True)
            else:
                moved = True  # first reading — assume moved

            if moved:
                self._last_pos = pos
                self._last_move_time = now
                self._recovery_count = 0
                return

            # Not moved — check timeout atomically while lock is still held
            # to avoid a TOCTOU race where resume() resets _last_move_time
            # between here and the stuck declaration below.
            idle = now - self._last_move_time
            if idle < self._cfg.stuck_timeout:
                return

        # ── STUCK declared ────────────────────────────────────────────────
        with self._lock:
            self._total_stucks += 1
            total = self._total_stucks
        self._log(
            f"[J] ⚠ No movement for {idle:.1f}s at {pos} "
            f"(stuck #{total})"
        )
        self._emit("e29", {
            "position": pos,
            "idle_secs": idle,
            "total": total,
        })

        # ── Recovery chain ────────────────────────────────────────────────
        with self._lock:
            action = self._choose_recovery()
            self._recovery_count += 1

        if action == RecoveryAction.REPATH:
            self._do_repath()
        elif action == RecoveryAction.NUDGE:
            self._do_nudge()
        elif action == RecoveryAction.ESCAPE:
            self._do_escape()
        elif action == RecoveryAction.ABORT:
            self._do_abort()

        # Reset timer so we wait another full timeout before re-triggering
        with self._lock:
            self._last_move_time = time.monotonic()

    def _choose_recovery(self) -> RecoveryAction:
        """Select escalating recovery action based on attempt count.

        Chain: REPATH (×1) → NUDGE (×nudge_retries) → ESCAPE (×2) → ABORT.
        ``max_recovery_attempts`` hard-caps the total before ABORT.
        """
        n = self._recovery_count
        if n >= self._cfg.max_recovery_attempts:
            return RecoveryAction.ABORT
        if n == 0:
            return RecoveryAction.REPATH
        elif n <= self._cfg.nudge_retries:
            return RecoveryAction.NUDGE
        elif n <= self._cfg.nudge_retries + 2:
            return RecoveryAction.ESCAPE
        else:
            return RecoveryAction.ABORT

    # ── Recovery actions ──────────────────────────────────────────────────────

    def _do_repath(self) -> None:
        self._log("[J] Recovery: repath")
        self._recovery_actions["repath"] += 1
        if self._repath_fn is not None:
            try:
                ok = self._repath_fn()
                if ok:
                    self._emit("e30", {"action": "repath"})
                    return
            except Exception as exc:
                self._log(f"[J] repath error: {exc}")
        # repath unavailable/failed — escalate next time

    def _do_nudge(self) -> None:
        self._log("[J] Recovery: nudge")
        self._recovery_actions["nudge"] += 1
        if self._nudge_fn is not None:
            # Bias nudge perpendicular to travel direction when available
            if self._target_direction is not None:
                tdx, tdy = self._target_direction
                # Try perpendicular directions first: (-tdy, tdx) or (tdy, -tdx)
                perp_choices = [(-tdy, tdx), (tdy, -tdx)]
                dx, dy = random.choice(perp_choices)
                # Clamp to -1..1
                dx = max(-1, min(1, dx)) if dx != 0 else random.choice([-1, 1])
                dy = max(-1, min(1, dy)) if dy != 0 else random.choice([-1, 1])
            else:
                dx = random.choice([-1, 0, 1])
                dy = random.choice([-1, 0, 1])
                if dx == 0 and dy == 0:
                    dx = 1
            try:
                pos_before = self._read_pos()
                self._nudge_fn(dx, dy)
                # Verify movement actually happened (poll for up to 1s)
                moved = False
                for _ in range(4):
                    if not self._sleep(0.25):
                        return
                    pos_after = self._read_pos()
                    if self._pos_moved(pos_after, pos_before):
                        moved = True
                        break
                if moved:
                    self._emit("e30", {"action": "nudge", "dx": dx, "dy": dy})
                else:
                    self._log(f"[J] Nudge ({dx},{dy}) did not change position")
            except Exception as exc:
                self._log(f"[J] nudge error: {exc}")

    def _do_escape(self) -> None:
        self._log("[J] Recovery: escape")
        self._recovery_actions["escape"] += 1
        if self._escape_fn is not None:
            try:
                # M4-fix: verify position changed after escape
                pos_before = self._read_pos()
                self._escape_fn()
                if not self._sleep(1.0):
                    return
                pos_after = self._read_pos()
                moved = self._pos_moved(pos_after, pos_before)
                if moved:
                    self._emit("e30", {"action": "escape"})
                else:
                    self._log("[J] Escape did not change position")
            except Exception as exc:
                self._log(f"[J] escape error: {exc}")

    def _do_abort(self) -> None:
        with self._lock:
            self._recovery_actions["abort"] += 1
            self._global_abort_count += 1
            self._walking = False
            self._abort_time = time.monotonic()  # T5: track for re-enable cooldown
        self._log(
            f"[J] Recovery: ABORT — giving up "
            f"(global abort #{self._global_abort_count}/{self._cfg.max_aborts})"
        )
        self._emit("e31", {
            "total_stucks": self._total_stucks,
            "recovery_attempts": self._recovery_count,
            "global_abort_count": self._global_abort_count,
        })

    def _check_abort_cooldown(self) -> None:
        """T5/C: re-enable walking after abort_cooldown, unless max_aborts exceeded."""
        with self._lock:
            elapsed = time.monotonic() - self._abort_time
            if elapsed < self._cfg.abort_cooldown:
                return
            if self._global_abort_count >= self._cfg.max_aborts:
                # C: too many total aborts — permanent stop, emit final event
                self._abort_time = 0.0
            else:
                self._walking = True
                self._recovery_count = 0
                self._abort_time = 0.0
                self._last_move_time = time.monotonic()
        if self._global_abort_count >= self._cfg.max_aborts:
            self._log(
                f"[J] PERMANENT STOP — reached max_aborts={self._cfg.max_aborts}. "
                "Restart the bot to resume walking."
            )
            self._emit("e32", {
                "global_abort_count": self._global_abort_count,
                "total_stucks": self._total_stucks,
            })
        else:
            self._log(
                f"[J] Re-enabled after {elapsed:.0f}s abort cooldown "
                f"(global aborts: {self._global_abort_count}/{self._cfg.max_aborts})"
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sleep(self, secs: float, *, pct: float = 0.0) -> bool:
        delay = secs * random.uniform(1.0 - pct, 1.0 + pct) if pct else secs
        return not self._stop_event.wait(max(0.0, delay))

    def _read_pos(self) -> Optional[Any]:
        if self._position_getter is None:
            return None
        try:
            return self._position_getter()
        except Exception:
            return None

    def _emit(self, event: str, data: Any = None) -> None:
        if self._event_bus is not None:
            try:
                self._event_bus.emit(event, data)
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            self._log_cb(msg)
        else:
            logger.info(msg)
