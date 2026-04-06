"""
ReconnectHandler
----------------
Detects when the Tibia client has been disconnected (login screen or
"Connection Lost" dialog) and attempts to log back in.

Detection methods:
  1. **Template match** — "Connection Lost" or Tibia login screen template.
  2. **Heuristic** — detects the solid-dark login background + centered content.

Recovery steps:
  1. Wait ``reconnect_delay`` seconds (server might be in save or restarting).
  2. If login screen detected, type account/password and press Enter.
  3. Wait for character select, click character, press Enter.
  4. Emit ``reconnected`` event.
  5. If reconnect fails after ``max_retries``, emit ``reconnect_failed``.

Integration::

    from src.reconnect_handler import ReconnectHandler, ReconnectConfig

    rh = ReconnectHandler(ctrl=input_ctrl)
    rh.set_frame_getter(frame_getter)
    rh.set_event_bus(bus)
    rh.start()
"""

from __future__ import annotations

import datetime
import logging
import threading
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import cv2
import numpy as np

logger = logging.getLogger("wn.rh")

_TEMPLATE_DIR = __import__("pathlib").Path(__file__).parent.parent / "cache" / "templates"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ReconnectConfig:
    """Tuneable parameters for reconnection.

    check_interval : float
        Seconds between login-screen checks (default 5.0 s).
    reconnect_delay : float
        Seconds to wait before attempting reconnect after disconnect is
        detected (default 10.0 s — lets server save finish).
    max_retries : int
        Maximum reconnect attempts before giving up (default 5).
    retry_delay : float
        Seconds between retry attempts (default 30.0 s).
    template_path : str
        Path to login screen template. Empty = use heuristic.
    confidence : float
        Template-match confidence threshold (default 0.7).
    heuristic_dark_ratio : float
        Fraction of dark pixels needed for heuristic login detection.
    server_save_hours : list[float]
        Hours (hour + min/60) when server saves happen. During ±3min of
        these times the handler waits longer before reconnecting.
    server_save_extra_delay : float
        Extra seconds to wait if within server-save window (default 120).
    """

    check_interval: float = 5.0
    reconnect_delay: float = 10.0
    max_retries: int = 5
    retry_delay: float = 30.0
    template_path: str = ""
    confidence: float = 0.7
    heuristic_dark_ratio: float = 0.60
    server_save_hours: list[float] = None  # type: ignore[assignment]
    server_save_extra_delay: float = 120.0
    max_backoff: float = 300.0  # T4: cap for exponential backoff (5 min)
    login_timeout_s: float = 60.0  # B5: max seconds for a single login_fn call
    max_total_retries: int = 50  # absolute cap across initial + backoff retries

    def __post_init__(self) -> None:
        if self.server_save_hours is None:
            self.server_save_hours = [10.0]  # 10:00 CET typical Tibia save


# ---------------------------------------------------------------------------
# ReconnectHandler
# ---------------------------------------------------------------------------

class ReconnectHandler:
    """Background thread that detects disconnects and attempts reconnection.

    The handler does NOT store credentials itself.  Provide a
    ``login_fn`` callback that executes the full login flow.

    Parameters
    ----------
    ctrl : InputController
    config : ReconnectConfig, optional
    """

    def __init__(
        self,
        ctrl: Any,
        config: Optional[ReconnectConfig] = None,
    ) -> None:
        self._ctrl = ctrl
        self._cfg = config or ReconnectConfig()
        self._template: Optional[np.ndarray] = None

        # Callbacks
        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._event_bus: Optional[Any] = None
        self._log_cb: Optional[Callable[[str], None]] = None
        self._login_fn: Optional[Callable[[], bool]] = None
        self._pause_fn: Optional[Callable[[], None]] = None
        self._resume_fn: Optional[Callable[[], None]] = None

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._disconnects: int = 0
        self._reconnects: int = 0
        self._consecutive_failures: int = 0
        self._login_timeouts: int = 0

        self._load_template()

    # ── Configuration ─────────────────────────────────────────────────────────

    def set_frame_getter(self, fn: Callable[[], Optional[np.ndarray]]) -> None:
        self._frame_getter = fn

    def set_event_bus(self, bus: Any) -> None:
        self._event_bus = bus

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self._log_cb = cb

    def set_login_fn(self, fn: Callable[[], bool]) -> None:
        """Register the login function: ``() -> bool``.

        It should execute the full login flow (type credentials, select
        character, press Enter). Return True on success.
        """
        self._login_fn = fn

    def set_pause_fn(self, fn: Callable[[], None]) -> None:
        """Callback to pause other subsystems during reconnection."""
        self._pause_fn = fn

    def set_resume_fn(self, fn: Callable[[], None]) -> None:
        """Callback to resume other subsystems after reconnection."""
        self._resume_fn = fn

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("[Q] Handler started")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._log(
            f"[Q] Handler stopped — disconnects={self._disconnects} "
            f"reconnects={self._reconnects}"
        )

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def disconnects(self) -> int:
        return self._disconnects

    @property
    def reconnects(self) -> int:
        return self._reconnects

    def check_now(self, frame: np.ndarray) -> bool:
        """Synchronous check: return True if frame shows login/disconnect screen."""
        return self._is_login_screen(frame)

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "disconnects": self._disconnects,
            "reconnects": self._reconnects,
            "consecutive_failures": self._consecutive_failures,
            "login_timeouts": self._login_timeouts,
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                logger.warning("[Q] tick error: %s", exc)
            if not self._safe_sleep(self._cfg.check_interval * random.uniform(0.8, 1.25)):
                break

    def _tick(self) -> None:
        if self._frame_getter is None:
            return
        frame = self._frame_getter()
        if frame is None:
            return

        if not self._is_login_screen(frame):
            # Connected — reset counters
            if self._consecutive_failures > 0:
                self._consecutive_failures = 0
            self._login_screen_streak: int = 0  # H8-fix: reset streak
            return

        # H8-fix: require multiple consecutive frames before acting on disconnect
        self._login_screen_streak = getattr(self, '_login_screen_streak', 0) + 1
        if self._login_screen_streak < 3:
            return  # not confirmed yet

        # ── Disconnect confirmed (3+ consecutive frames) ──────────────────
        self._login_screen_streak = 0
        self._disconnects += 1
        self._log(f"[Q] ⚠ Disconnect detected! (#{self._disconnects})")
        self._emit("e21", {"count": self._disconnects})

        # Pause other subsystems during reconnection
        if self._pause_fn:
            try:
                self._pause_fn()
                self._log("[Q] Paused subsystems for reconnection")
            except Exception as exc:
                self._log(f"[Q] pause_fn failed: {exc}")

        # Wait before reconnecting (server save awareness)
        delay = self._cfg.reconnect_delay
        if self._is_server_save_window():
            self._log(
                f"[Q] Server save window — waiting extra "
                f"{self._cfg.server_save_extra_delay}s"
            )
            delay += self._cfg.server_save_extra_delay
        self._safe_sleep(delay)

        # Attempt reconnection — initial fast retries
        for attempt in range(1, self._cfg.max_retries + 1):
            if not self._running:
                break

            self._log(f"[Q] Reconnect attempt {attempt}/{self._cfg.max_retries}")

            success = self._attempt_login()
            if success:
                self._reconnects += 1
                self._consecutive_failures = 0
                self._log(f"[Q] ✓ Reconnected (total={self._reconnects})")
                self._emit("e22", {
                    "attempt": attempt,
                    "reconnects": self._reconnects,
                })
                # Resume subsystems after successful reconnect
                if self._resume_fn:
                    try:
                        self._resume_fn()
                        self._log("[Q] Resumed subsystems after reconnect")
                    except Exception as exc:
                        self._log(f"[Q] resume_fn failed: {exc}")
                return

            self._consecutive_failures += 1
            if attempt < self._cfg.max_retries:
                self._log(f"[Q] Attempt {attempt} failed — retrying in {self._cfg.retry_delay}s")
                self._safe_sleep(self._cfg.retry_delay)

        # Initial batch exhausted — emit event but keep retrying with backoff
        if self._running:
            self._log("[Q] ✗ Initial reconnect attempts failed — entering backoff retry")
            self._emit("e23", {
                "disconnects": self._disconnects,
                "attempts": self._cfg.max_retries,
            })

        # T4: Exponential backoff loop with absolute cap
        backoff = self._cfg.retry_delay * 2
        total_attempts = self._cfg.max_retries
        while self._running:
            if total_attempts >= self._cfg.max_total_retries:
                self._log(
                    f"[Q] ✗ Absolute retry cap reached ({self._cfg.max_total_retries}) "
                    f"— giving up. Manual intervention required."
                )
                self._emit("e24", {
                    "total_attempts": total_attempts,
                    "disconnects": self._disconnects,
                })
                self._running = False
                break

            backoff = min(backoff, self._cfg.max_backoff)
            self._log(f"[Q] Backoff retry in {backoff:.0f}s ...")
            self._safe_sleep(backoff)
            if not self._running:
                break

            total_attempts += 1
            self._log(f"[Q] Backoff attempt #{total_attempts}/{self._cfg.max_total_retries}")
            success = self._attempt_login()
            if success:
                self._reconnects += 1
                self._consecutive_failures = 0
                self._log(
                    f"[Q] ✓ Reconnected after backoff "
                    f"(attempt #{total_attempts}, total={self._reconnects})"
                )
                self._emit("e22", {
                    "attempt": total_attempts,
                    "reconnects": self._reconnects,
                })
                if self._resume_fn:
                    try:
                        self._resume_fn()
                        self._log("[Q] Resumed subsystems after reconnect")
                    except Exception as exc:
                        self._log(f"[Q] resume_fn failed: {exc}")
                return

            self._consecutive_failures += 1
            backoff = min(backoff * 2, self._cfg.max_backoff)

        # stopped externally — resume subsystems so session can clean up
        if self._resume_fn:
            try:
                self._resume_fn()
                self._log("[Q] Resumed subsystems after handler stopped")
            except Exception as exc:
                self._log(f"[Q] resume_fn failed: {exc}")

    # ── Detection ─────────────────────────────────────────────────────────────

    def _is_login_screen(self, frame: np.ndarray) -> bool:
        """Return True if the frame shows the login/connection-lost screen."""
        if self._template is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
            tmpl = self._template
            if tmpl.shape[0] <= gray.shape[0] and tmpl.shape[1] <= gray.shape[1]:
                res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
                return float(res.max()) >= self._cfg.confidence
        return self._heuristic_match(frame)

    def _heuristic_match(self, frame: np.ndarray) -> bool:
        """Detect login screen by checking for predominantly dark background.

        Tibia's login screen is mostly solid dark with a small centered content
        area.  We check if >60% of the frame is very dark (gray < 30).
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        dark_pixels = np.count_nonzero(gray < 30)
        total = max(gray.size, 1)
        return float(dark_pixels) / total > self._cfg.heuristic_dark_ratio

    # ── Login ─────────────────────────────────────────────────────────────────

    def _attempt_login(self) -> bool:
        """Execute the login flow using the registered login_fn callback.

        B5: runs login_fn in a worker thread with a configurable timeout
        so a hung Tibia client cannot block the reconnect loop forever.
        """
        if self._login_fn is None:
            self._log("[Q] No login_fn registered — cannot reconnect")
            return False

        t0 = time.monotonic()
        result: list[bool] = [False]
        exc_holder: list[Optional[Exception]] = [None]
        login_fn = self._login_fn  # local ref for thread

        def _run() -> None:
            try:
                result[0] = login_fn()
            except Exception as e:
                exc_holder[0] = e

        worker = threading.Thread(target=_run, daemon=True, name=f"t-{id(self):x}")
        worker.start()
        worker.join(timeout=self._cfg.login_timeout_s)
        elapsed = time.monotonic() - t0

        if worker.is_alive():
            self._login_timeouts += 1
            self._log(
                f"[Q] login_fn TIMED OUT after {elapsed:.1f}s "
                f"(limit={self._cfg.login_timeout_s:.0f}s, "
                f"timeouts={self._login_timeouts})"
            )
            return False

        if exc_holder[0] is not None:
            self._log(f"[Q] login_fn raised: {exc_holder[0]}")
            return False

        self._log(
            f"[Q] login_fn completed in {elapsed:.1f}s "
            f"→ {'OK' if result[0] else 'FAIL'}"
        )
        return result[0]

    # ── Server save ───────────────────────────────────────────────────────────

    def _is_server_save_window(self) -> bool:
        """Return True if current time is within ±3 min of a server save hour."""
        now = datetime.datetime.now()
        now_minutes = now.hour * 60 + now.minute
        for h in (self._cfg.server_save_hours or []):
            save_minutes = int(h) * 60 + int((h % 1) * 60)
            diff = abs(now_minutes - save_minutes)
            # Handle midnight wrap (e.g. save at 23:58, now at 00:01)
            if min(diff, 1440 - diff) <= 3:
                return True
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_template(self) -> None:
        path = self._cfg.template_path
        if not path:
            default = _TEMPLATE_DIR / "login_screen.png"
            if default.exists():
                path = str(default)
            else:
                return
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            self._template = img
            self._log(f"[Q] Template loaded: {path}")

    def _safe_sleep(self, secs: float) -> bool:
        """Sleep in small chunks so stop() can interrupt quickly."""
        end = time.monotonic() + secs
        while self._running and time.monotonic() < end:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            if self._stop_event.wait(min(1.0, remaining)):
                return False
        return self._running

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
