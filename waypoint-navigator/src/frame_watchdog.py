"""
src/frame_watchdog.py — Runtime frame-capture health monitor.

Runs a background thread that polls the frame_getter every few seconds and
classifies each frame as HEALTHY / BLACK / FROZEN / NONE.

When ``max_failures`` consecutive bad frames are detected:
  1. Calls ``restart_fn()`` (e.g. ``frame_cache.invalidate()``) to force a
     fresh capture on the next cycle.
  2. Emits ``frame_capture_failure`` on the EventBus.
  3. Sets ``is_healthy = False`` so callers can gate on it.

When frames recover, emits ``frame_capture_recovered``.

Integration in BotSession::

    watchdog = FrameWatchdog()
    watchdog.set_frame_getter(frame_cache.get_frame)
    watchdog.set_restart_fn(frame_cache.invalidate)   # cheap: force re-capture
    watchdog.set_event_bus(event_bus)
    watchdog.set_log_callback(self._log)
    watchdog.start()
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Optional

import numpy as np

_log = logging.getLogger("wn.fw")


class FrameHealth(Enum):
    HEALTHY = auto()
    NONE = auto()    # getter returned None — backend died
    BLACK = auto()   # mean pixel < threshold — window minimized / backend hung
    FROZEN = auto()  # identical content for N consecutive polls — backend locked


@dataclass
class FrameWatchdogConfig:
    """Tuneable parameters for the frame-capture health monitor.

    poll_interval : float
        Seconds between health checks (default 3.0 s).
    black_threshold : float
        Mean pixel value below which a frame is classified BLACK (default 8.0).
        A fully-black 1920×1080 frame has mean ≈ 0; real game frames ≥ 30.
    frozen_streak : int
        Consecutive identical-hash polls required to classify FROZEN (default 3).
        At poll_interval=3 s that means 9 s of the same frame → backend locked.
    max_failures : int
        Consecutive bad polls before restart_fn is called and the event is emitted
        (default 4 → 12 s at 3 s interval).
    restart_cooldown : float
        Minimum seconds between restart attempts (default 20.0 s).
    enabled : bool
        Master kill-switch.
    """

    poll_interval: float = 3.0
    black_threshold: float = 8.0
    frozen_streak: int = 3
    max_failures: int = 4
    restart_cooldown: float = 20.0
    enabled: bool = True


class FrameWatchdog:
    """Background monitor for frame-capture pipeline health.

    All callbacks are optional — missing callbacks degrade gracefully:
    no restart_fn  → logs warning, cannot auto-recover
    no event_bus   → no events emitted
    no log_cb      → falls back to stdlib logger
    """

    def __init__(self, config: Optional[FrameWatchdogConfig] = None) -> None:
        self._cfg = config or FrameWatchdogConfig()

        # Wired by caller
        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._restart_fn: Optional[Callable[[], None]] = None
        self._event_bus: Optional[Any] = None
        self._log_cb: Optional[Callable[[str], None]] = None

        # Window visibility tracking (optional — used to skip restart when
        # Tibia is minimized rather than when the capture pipeline has died)
        self._window_title: str = ""
        self._hwnd_cache: int = 0

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Health state (lock-protected)
        self._failure_streak: int = 0
        self._last_hash: Optional[str] = None
        self._frozen_streak: int = 0
        self._last_restart_ts: float = 0.0
        self._total_restarts: int = 0
        self._last_health: FrameHealth = FrameHealth.HEALTHY
        self._healthy: bool = True
        self._bad_since: float = 0.0   # monotonic time when unhealthy started

    # ── Wiring ───────────────────────────────────────────────────────────────

    def set_frame_getter(self, fn: Callable[[], Optional[np.ndarray]]) -> None:
        """Register the frame-capture callable (e.g. FrameCache.get_frame)."""
        self._frame_getter = fn

    def set_restart_fn(self, fn: Callable[[], None]) -> None:
        """Register restart callback (e.g. FrameCache.invalidate)."""
        self._restart_fn = fn

    def set_event_bus(self, bus: Any) -> None:
        self._event_bus = bus

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self._log_cb = cb

    def set_window_title(self, title: str) -> None:
        """Register the game-window title so black frames caused by minimising
        can be distinguished from real capture-pipeline failures.

        Call before ``start()``.  If not set, window-visibility checks are
        skipped and every black-frame streak triggers a normal restart.
        """
        self._window_title = title
        self._hwnd_cache = 0  # invalidate any cached handle

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("[FW] Frame watchdog started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        self._log("[FW] Frame watchdog stopped")

    # ── Public state ─────────────────────────────────────────────────────────

    @property
    def is_healthy(self) -> bool:
        """True when the last assessment was HEALTHY."""
        with self._lock:
            return self._healthy

    @property
    def seconds_unhealthy(self) -> float:
        """Seconds since the first bad frame, or 0.0 if currently healthy."""
        with self._lock:
            if self._healthy or self._bad_since == 0.0:
                return 0.0
            return time.monotonic() - self._bad_since

    def stats_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "healthy": self._healthy,
                "failure_streak": self._failure_streak,
                "total_restarts": self._total_restarts,
                "last_health": self._last_health.name,
                "seconds_unhealthy": (
                    0.0 if self._healthy
                    else max(0.0, time.monotonic() - self._bad_since)
                ),
            }

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            if not self._cfg.enabled:
                time.sleep(self._cfg.poll_interval)
                continue
            try:
                health = self._assess_frame()
                self._handle_health(health)
            except Exception as exc:
                _log.debug("[FW] assessment error: %s", exc)
            time.sleep(self._cfg.poll_interval)

    def _assess_frame(self) -> FrameHealth:
        if self._frame_getter is None:
            return FrameHealth.HEALTHY  # not wired — don't penalise

        frame = self._frame_getter()

        if frame is None:
            return FrameHealth.NONE

        if float(frame.mean()) < self._cfg.black_threshold:
            return FrameHealth.BLACK

        # Hash a centre crop (cheap, avoids hashing 6 MB for 1080p)
        h, w = frame.shape[:2]
        crop = frame[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
        digest = hashlib.md5(crop.tobytes(), usedforsecurity=False).hexdigest()

        with self._lock:
            if digest == self._last_hash:
                self._frozen_streak += 1
                frozen = self._frozen_streak >= self._cfg.frozen_streak
            else:
                self._last_hash = digest
                self._frozen_streak = 0
                frozen = False

        return FrameHealth.FROZEN if frozen else FrameHealth.HEALTHY

    def _handle_health(self, health: FrameHealth) -> None:
        with self._lock:
            prev_healthy = self._healthy
            self._last_health = health

            if health == FrameHealth.HEALTHY:
                recovered = not prev_healthy and self._failure_streak > 0
                self._failure_streak = 0
                self._healthy = True
                self._bad_since = 0.0

            else:
                self._failure_streak += 1
                if prev_healthy:
                    self._bad_since = time.monotonic()
                self._healthy = False
                streak = self._failure_streak
                recovered = False

        if health == FrameHealth.HEALTHY:
            if recovered:
                self._log("[FW] ✓ Frame capture recovered")
                self._emit("frame_capture_recovered", {})
            return

        self._log(
            f"[FW] ⚠ Bad frame: {health.name} "
            f"(streak={streak}/{self._cfg.max_failures})"
        )

        if streak >= self._cfg.max_failures:
            # BLACK frames while the game window is minimised are harmless —
            # the capture backend can't grab a minimised window.  Skip the
            # costly restart and emit a distinct event so the UI can inform
            # the user without triggering a reconnect sequence.
            if health == FrameHealth.BLACK and not self._is_window_visible():
                self._log(
                    "[FW] Black frame — game window appears minimized, skipping restart"
                )
                self._emit("frame_black_minimized", {"streak": streak})
                return

            self._emit("frame_capture_failure", {
                "reason": health.name,
                "streak": streak,
            })
            self._try_restart()

    def _try_restart(self) -> None:
        now = time.monotonic()
        with self._lock:
            if (now - self._last_restart_ts) < self._cfg.restart_cooldown:
                return
            self._last_restart_ts = now
            self._total_restarts += 1
            n = self._total_restarts

        self._log(f"[FW] Attempting frame-capture restart #{n}")
        if self._restart_fn is not None:
            try:
                self._restart_fn()
                self._log("[FW] Restart fn called — waiting for healthy frames")
                with self._lock:
                    self._failure_streak = 0
                    self._frozen_streak = 0
                    self._last_hash = None
            except Exception as exc:
                self._log(f"[FW] Restart failed: {exc}")
        else:
            self._log("[FW] No restart_fn — cannot auto-recover capture pipeline")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_window_visible(self) -> bool:
        """Return False when the game window is minimized/hidden.

        Uses win32gui on Windows.  Falls back to True (assume visible) if
        win32gui is unavailable, no title is set, or the window cannot be
        found — so the caller proceeds with a normal restart attempt.
        """
        if not self._window_title:
            return True  # not configured → can't tell → assume visible
        try:
            import win32gui
            hwnd = self._hwnd_cache
            if not hwnd or not win32gui.IsWindow(hwnd):
                hwnd = win32gui.FindWindow(None, self._window_title)
                self._hwnd_cache = hwnd
            if hwnd == 0:
                return True  # window not found → assume visible, let restart proceed
            return not win32gui.IsIconic(hwnd)   # IsIconic == minimized
        except Exception:
            return True  # win32gui not available or any OS error → assume visible

    def _emit(self, event: str, data: Any) -> None:
        if self._event_bus is not None:
            try:
                self._event_bus.emit(event, data)
            except Exception:
                _log.debug("FrameWatchdog failed to emit event %s", event, exc_info=True)

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            self._log_cb(msg)
        else:
            _log.info(msg)
