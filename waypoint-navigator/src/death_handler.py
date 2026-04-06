"""
DeathHandler
------------
Detects the "You are dead" screen via template matching or pixel heuristic,
and executes the recovery sequence:

1. Detect death screen.
2. Wait for the respawn button / press OK.
3. Optionally re-equip gear (press hotkeys for backpack, amulet, ring).
4. Emit ``player_died`` event via EventBus.
5. Notify the session controller so it can decide whether to resume or stop.

Integration::

    from src.death_handler import DeathHandler, DeathConfig

    dh = DeathHandler(ctrl=input_ctrl)
    dh.set_frame_getter(frame_getter)
    dh.set_event_bus(bus)
    dh.start()
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from src.humanizer import jittered_sleep

import cv2
import numpy as np

logger = logging.getLogger("wn.dh")

# Default template path
from src.config_paths import TEMPLATES_DIR as _TEMPLATE_DIR


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class DeathConfig:
    """Tuneable parameters for death detection.

    check_interval : float
        Seconds between death-screen checks (default 1.0 s).
    template_path : str
        Path to a template image for "You are dead" text. If empty,
        uses a pixel-based heuristic (mostly-red screen).
    confidence : float
        Minimum template-match confidence (default 0.7).
    respawn_delay : float
        Seconds to wait after detecting death before pressing OK (default 3.0).
    re_equip_hotkeys : list[int]
        VK codes to press after respawn (backpack, ring, amulet).
    max_deaths : int
        Auto-stop after this many deaths (0 = unlimited).
    press_ok_vk : int
        VK code for confirming the death dialog (0 = press Enter).
    heuristic_red_ratio : float
        Fraction of red pixels needed for the heuristic death detection (0.30).
    confirm_frames : int
        Number of consecutive frames that must match before declaring death
        (default 2). Prevents single-frame false positives from blood/fire.
    navigation_timeout_s : float
        Max seconds allowed for post-death return navigation.
    """

    check_interval: float = 1.0
    template_path: str = ""
    confidence: float = 0.7
    respawn_delay: float = 3.0
    re_equip_hotkeys: List[int] = field(default_factory=list)
    max_deaths: int = 0
    press_ok_vk: int = 0  # 0 = Enter (0x0D)
    heuristic_red_ratio: float = 0.30
    confirm_frames: int = 2
    navigation_timeout_s: float = 45.0


# ---------------------------------------------------------------------------
# DeathHandler
# ---------------------------------------------------------------------------

class DeathHandler:
    """Background thread that monitors for the death screen.

    Parameters
    ----------
    ctrl : InputController
        For sending hotkeys to dismiss dialogs and re-equip.
    config : DeathConfig, optional
    """

    def __init__(
        self,
        ctrl: Any,
        config: Optional[DeathConfig] = None,
    ) -> None:
        self._ctrl = ctrl
        self._cfg = config or DeathConfig()
        self._template: Optional[np.ndarray] = None

        # Callbacks
        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._event_bus: Optional[Any] = None
        self._log_cb: Optional[Callable[[str], None]] = None
        self._on_death: Optional[Callable[[], None]] = None
        self._pause_fn: Optional[Callable[[], None]] = None
        self._resume_fn: Optional[Callable[[], None]] = None
        self._position_getter: Optional[Callable[[], Any]] = None
        self._navigate_fn: Optional[Callable[[Any], bool]] = None
        self._stop_session_fn: Optional[Callable[[], None]] = None

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._deaths: int = 0
        self._consecutive_death_frames: int = 0
        self._death_position: Optional[Any] = None
        self._pending_death_position: Optional[Any] = None

        self._load_template()

    # ── Configuration ─────────────────────────────────────────────────────────

    def set_frame_getter(self, fn: Callable[[], Optional[np.ndarray]]) -> None:
        self._frame_getter = fn

    def set_event_bus(self, bus: Any) -> None:
        self._event_bus = bus

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self._log_cb = cb

    def set_on_death(self, cb: Callable[[], None]) -> None:
        """Optional callback fired immediately when death is confirmed."""
        self._on_death = cb

    def set_pause_fn(self, fn: Callable[[], None]) -> None:
        """Callback to pause other subsystems during death recovery."""
        self._pause_fn = fn

    def set_resume_fn(self, fn: Callable[[], None]) -> None:
        """Callback to resume other subsystems after death recovery."""
        self._resume_fn = fn

    def set_position_getter(self, fn: Callable[[], Any]) -> None:
        """Register ``() -> Coordinate | None`` to capture position before death."""
        self._position_getter = fn

    def set_navigate_fn(self, fn: Callable[[Any], bool]) -> None:
        """Register ``(target_pos) -> bool`` to navigate back to pre-death position."""
        self._navigate_fn = fn

    def set_stop_session_fn(self, fn: Callable[[], None]) -> None:
        """Register callback to stop the entire session (called on max deaths)."""
        self._stop_session_fn = fn

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
        self._log("[D] Handler started")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                self._log("[D] Warning: handler thread did not exit cleanly within 3s")
            self._thread = None
        self._log(f"[D] Handler stopped — deaths={self._deaths}")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def deaths(self) -> int:
        return self._deaths

    def reset_deaths(self) -> None:
        self._deaths = 0

    def check_now(self, frame: np.ndarray) -> bool:
        """Synchronous check: return True if frame shows the death screen."""
        return self._is_death_screen(frame)

    @property
    def death_position(self) -> Optional[Any]:
        """Position where the player last died, or None."""
        return self._death_position

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "deaths": self._deaths,
            "death_position": self._death_position,
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                logger.warning("[D] tick error: %s", exc)
            if not self._sleep(self._cfg.check_interval, pct=0.25):
                break

    def _tick(self) -> None:
        if self._frame_getter is None:
            return
        frame = self._frame_getter()
        if frame is None:
            return

        if not self._is_death_screen(frame):
            self._consecutive_death_frames = 0
            self._pending_death_position = None
            return

        if self._consecutive_death_frames == 0 and self._position_getter is not None:
            try:
                self._pending_death_position = self._position_getter()
            except Exception:
                self._pending_death_position = None

        # Require N consecutive positive frames before confirming death
        self._consecutive_death_frames += 1
        if self._consecutive_death_frames < self._cfg.confirm_frames:
            return

        # ── Death confirmed ───────────────────────────────────────────────
        self._consecutive_death_frames = 0
        self._deaths += 1

        # Capture position before death (for return-to-route)
        if self._position_getter is not None:
            try:
                live_position = self._position_getter()
                self._death_position = live_position if live_position is not None else self._pending_death_position
            except Exception:
                self._death_position = self._pending_death_position
        else:
            self._death_position = self._pending_death_position
        self._pending_death_position = None

        self._log(f"[D] ☠ Player died! (death #{self._deaths}) at {self._death_position}")

        self._emit("e9", {"death_count": self._deaths})
        self._emit("e3", {"death_count": self._deaths})
        if self._on_death:
            try:
                self._on_death()
            except Exception as exc:
                self._log(f"[D] on_death callback failed: {exc}")

        # Check max deaths
        if self._cfg.max_deaths > 0 and self._deaths >= self._cfg.max_deaths:
            self._log(f"[D] Max deaths ({self._cfg.max_deaths}) reached — STOPPING SESSION")
            self._running = False
            self._emit("e10", {"deaths": self._deaths})
            if self._stop_session_fn:
                try:
                    self._stop_session_fn()
                except Exception as exc:
                    self._log(f"[D] stop_session_fn failed: {exc}")
            return

        # Pause other subsystems during recovery
        if self._pause_fn:
            try:
                self._pause_fn()
                self._log("[D] Paused subsystems for recovery")
            except Exception as exc:
                self._log(f"[D] pause_fn failed: {exc}")

        # Wait before trying to dismiss
        if not self._sleep(self._cfg.respawn_delay, pct=0.25):
            return

        # Press OK / Enter to dismiss dialog, then verify it was dismissed
        ok_vk = self._cfg.press_ok_vk if self._cfg.press_ok_vk else 0x0D  # Enter
        self._ctrl.press_key(ok_vk)
        self._log("[D] Pressed OK to dismiss death dialog")
        if not self._sleep(1.0):
            return

        # Verify the death dialog was actually dismissed — require 2 consecutive
        # non-death frames before proceeding (robust against visual artifacts)
        dismissed = False
        for _retry in range(5):
            verify_frame = self._frame_getter() if self._frame_getter is not None else None
            if verify_frame is not None and self._is_death_screen(verify_frame):
                self._log(f"[D] Dialog still visible (attempt {_retry + 1}/5) — pressing OK again")
                self._ctrl.press_key(ok_vk)
                if not self._sleep(1.0):
                    return
            else:
                # Require a second clean frame to confirm dismissal
                if not self._sleep(0.5):
                    return
                verify_frame2 = self._frame_getter() if self._frame_getter is not None else None
                if verify_frame2 is None or not self._is_death_screen(verify_frame2):
                    dismissed = True
                    break
                # Second frame still shows death — keep trying
                self._log("[D] Death screen reappeared on verify — retrying")
                self._ctrl.press_key(ok_vk)
                if not self._sleep(1.0):
                    return

        if not dismissed:
            self._log("[D] ⚠ Could not dismiss death dialog after 5 attempts")
            self._emit("e11", {"death_count": self._deaths})

        # Re-equip
        for vk in self._cfg.re_equip_hotkeys:
            if vk:
                self._ctrl.press_key(vk)
                self._log(f"[D] Re-equip: pressed VK=0x{vk:02X}")
                if not self._sleep(0.5):
                    return

        self._emit("e12", {
            "death_count": self._deaths,
            "re_equipped": len(self._cfg.re_equip_hotkeys),
        })

        # Resume subsystems after recovery
        if self._resume_fn:
            try:
                self._resume_fn()
                self._log("[D] Resumed subsystems after recovery")
            except Exception as exc:
                self._log(f"[D] resume_fn failed: {exc}")

        # Navigate back to pre-death position
        if self._navigate_fn is not None:
            if self._death_position is not None:
                self._log(f"[D] Navigating back to {self._death_position}")
                self._emit("e13", {"target": self._death_position})
                try:
                    nav_ok = self._run_navigation_with_timeout(self._death_position)
                    if nav_ok:
                        self._log("[D] Return navigation succeeded")
                        self._emit("e14", {
                            "target": self._death_position,
                            "success": True,
                        })
                    else:
                        self._log("[D] Return navigation failed")
                        self._emit("e14", {
                            "target": self._death_position,
                            "success": False,
                        })
                except Exception as exc:
                    self._log(f"[D] Return navigation error: {exc}")
            else:
                self._log("[D] ⚠ Death position unknown — cannot navigate back")
                self._emit("e14", {"target": None, "success": False})

        self._emit("death_recovery_complete", {"death_count": self._deaths})

    # ── Detection ─────────────────────────────────────────────────────────────

    def _is_death_screen(self, frame: np.ndarray) -> bool:
        """Return True if the frame shows the death screen.

        Uses template matching if a template is loaded, otherwise falls
        back to a heuristic based on the large red-tinted overlay that
        Tibia displays on death.
        """
        if self._template is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
            tmpl = self._template
            if tmpl.shape[0] <= gray.shape[0] and tmpl.shape[1] <= gray.shape[1]:
                res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
                return float(res.max()) >= self._cfg.confidence
        return self._heuristic_match(frame)

    def _run_navigation_with_timeout(self, target: Any) -> bool:
        navigate_fn = self._navigate_fn
        if navigate_fn is None:
            return False
        timeout_s = max(0.0, self._cfg.navigation_timeout_s)
        if timeout_s == 0.0:
            return bool(navigate_fn(target))

        result: dict[str, bool] = {"done": False, "ok": False}

        def _worker() -> None:
            try:
                result["ok"] = bool(navigate_fn(target))
            finally:
                result["done"] = True

        thread = threading.Thread(target=_worker, daemon=True, name=f"death-nav-{id(self):x}")
        thread.start()
        thread.join(timeout=timeout_s)
        if not result["done"]:
            self._log(f"[D] Return navigation timed out after {timeout_s:.1f}s")
            return False
        return result["ok"]

    def _heuristic_match(self, frame: np.ndarray) -> bool:
        """Detect the Tibia death screen by checking for a predominantly red overlay."""
        if frame.ndim != 3:
            return False
        b, g, r = cv2.split(frame)
        # Death screen in Tibia: heavy red tint, R >> G and R >> B
        red_mask = (r > 100) & (r.astype(int) - g.astype(int) > 50) & (r.astype(int) - b.astype(int) > 50)
        red_ratio = float(np.count_nonzero(red_mask)) / max(frame.shape[0] * frame.shape[1], 1)
        return bool(red_ratio > self._cfg.heuristic_red_ratio)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sleep(self, secs: float, *, pct: float = 0.0) -> bool:
        delay = secs * random.uniform(1.0 - pct, 1.0 + pct) if pct else secs
        return not self._stop_event.wait(max(0.0, delay))

    def _load_template(self) -> None:
        path = self._cfg.template_path
        if not path:
            # Try default location
            default = _TEMPLATE_DIR / "death_screen.png"
            if default.exists():
                path = str(default)
            else:
                return
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            self._template = img
            self._log(f"[D] Template loaded: {path}")

    def _emit(self, event: str, data: Any = None) -> None:
        if self._event_bus is not None:
            try:
                self._event_bus.emit(event, data)
            except Exception as exc:
                self._log(f"[D] event bus emit({event}) failed: {exc}")

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            self._log_cb(msg)
        else:
            logger.info(msg)
