"""
GM Detector — detect Game Master presence and trigger safety actions.

Scans the battle list and chat area for GM indicators (name colours,
specific text patterns) and reacts by pausing the bot, mimicking
human behaviour, or logging out.

GM names in Tibia appear with a distinctive light-blue/cyan colour
in the battle list.  Chat messages from GMs use a similar colour.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np

_log = logging.getLogger("wn.gd")


# ── Configuration ────────────────────────────────────────────────────────────

class GMAction(Enum):
    """Action when a GM is detected."""
    ALERT = "alert"          # emit event + alert only
    PAUSE = "pause"          # pause all subsystems
    LOGOUT = "logout"        # send Ctrl+L to logout
    HUMAN_MIMIC = "mimic"    # simulate human-like actions


@dataclass
class GMDetectorConfig:
    """
    Parameters
    ----------
    enabled : bool
        Master switch.
    action : GMAction
        What to do on GM detection.
    battle_list_roi : List[int]
        [x, y, w, h] of the battle list area.
    chat_roi : List[int]
        [x, y, w, h] of the chat window area.
    scan_interval : float
        Seconds between scans.
    min_consecutive : int
        Require N consecutive detections before confirming.
    gm_name_patterns : List[str]
        Regex patterns for GM names (applied to OCR results if available).
    gm_hsv_lower : List[int]
        Lower HSV bound for GM name colour (light blue/cyan in Tibia).
    gm_hsv_upper : List[int]
        Upper HSV bound for GM name colour.
    mimic_duration : float
        How long (seconds) to mimic human actions before resuming.
    cooldown_s : float
        Min seconds between consecutive gm_detected events.
    pause_timeout_s : float
        Max seconds to stay paused on a GM false positive before auto-resume.
    reference_resolution : List[int]
        Base resolution used to define ROIs; ROIs are scaled to the live frame.
    """
    enabled: bool = True
    action: GMAction = GMAction.PAUSE
    battle_list_roi: List[int] = field(
        default_factory=lambda: [1568, 175, 162, 345]
    )
    chat_roi: List[int] = field(
        default_factory=lambda: [7, 570, 600, 200]
    )
    scan_interval: float = 1.0
    min_consecutive: int = 2
    gm_name_patterns: List[str] = field(
        default_factory=lambda: [r"\bGM\s", r"\bCM\s", r"Gamemaster"]
    )  # NOTE: not used — OCR-based detection not implemented; colour scan only
    # GM names in Tibia are light-blue / cyan (H~85-105, S~80-255, V~180-255)
    gm_hsv_lower: List[int] = field(default_factory=lambda: [85, 80, 180])
    gm_hsv_upper: List[int] = field(default_factory=lambda: [105, 255, 255])
    mimic_duration: float = 60.0
    cooldown_s: float = 30.0
    pause_timeout_s: float = 180.0
    reference_resolution: List[int] = field(default_factory=lambda: [1920, 1080])


# ── Detection result ─────────────────────────────────────────────────────────

@dataclass
class GMDetection:
    """Result of a single GM scan."""
    detected: bool = False
    source: str = ""       # "battle_list" | "chat" | ""
    confidence: float = 0.0
    pixel_count: int = 0
    timestamp: float = 0.0


# ── Detector ─────────────────────────────────────────────────────────────────

class GMDetector:
    """
    Background thread that scans the game screen for GM presence.

    Integrates with EventBus, InputController (for human mimicry),
    and the session pause/resume mechanism.

    Usage::

        detector = GMDetector(config, event_bus=bus)
        detector.set_frame_getter(cached_getter)
        detector.set_input_controller(ctrl)
        detector.set_pause_fn(session._pause_subsystems)
        detector.set_resume_fn(session._resume_subsystems)
        detector.start()
    """

    _MIN_PIXEL_THRESHOLD = 30  # min cyan pixels to consider a GM present

    def __init__(
        self,
        config: Optional[GMDetectorConfig] = None,
        event_bus: Any = None,
    ) -> None:
        self._cfg = config or GMDetectorConfig()
        self._event_bus = event_bus

        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._ctrl: Any = None
        self._pause_fn: Optional[Callable[[], None]] = None
        self._resume_fn: Optional[Callable[[], None]] = None
        self._log_cb: Callable[[str], None] = lambda m: None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._consecutive = 0
        self._last_event_ts = 0.0
        self._last_enabled = self._cfg.enabled
        self._total_scans = 0
        self._total_detections = 0
        self._last_result: Optional[GMDetection] = None

    # ── Wiring ───────────────────────────────────────────────────────────

    def set_frame_getter(self, fn: Callable[[], Optional[np.ndarray]]) -> None:
        self._frame_getter = fn

    def set_input_controller(self, ctrl: Any) -> None:
        self._ctrl = ctrl

    def set_pause_fn(self, fn: Callable[[], None]) -> None:
        self._pause_fn = fn

    def set_resume_fn(self, fn: Callable[[], None]) -> None:
        self._resume_fn = fn

    def set_log_callback(self, fn: Callable[[str], None]) -> None:
        self._log_cb = fn

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._scan_loop,
            daemon=True,
            name=f"t-{os.urandom(3).hex()}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Scan loop ────────────────────────────────────────────────────────

    def _scan_loop(self) -> None:
        while self._running:
            if self._cfg.enabled != self._last_enabled:
                self._consecutive = 0
                self._last_enabled = self._cfg.enabled
            if not self._cfg.enabled:
                time.sleep(self._cfg.scan_interval)
                continue
            try:
                result = self._scan_once()
                if result.detected:
                    self._consecutive += 1
                else:
                    self._consecutive = 0

                now = time.monotonic()
                if (
                    self._consecutive >= self._cfg.min_consecutive
                    and (now - self._last_event_ts) >= self._cfg.cooldown_s
                ):
                    self._on_gm_confirmed(result)
                    self._last_event_ts = now
            except Exception:
                _log.debug("GM scan error", exc_info=True)

            time.sleep(self._cfg.scan_interval * random.uniform(0.8, 1.25))

    def _scan_once(self) -> GMDetection:
        """Run a single scan on the current frame."""
        self._total_scans += 1
        result = GMDetection(timestamp=time.monotonic())

        if self._frame_getter is None:
            return result

        frame = self._frame_getter()
        if frame is None or frame.size == 0:
            return result

        # Check battle list for GM name colour (cyan)
        bl_pixels = self._check_gm_colour(frame, self._cfg.battle_list_roi)
        if bl_pixels >= self._MIN_PIXEL_THRESHOLD:
            result.detected = True
            result.source = "battle_list"
            result.pixel_count = bl_pixels
            result.confidence = min(1.0, bl_pixels / 200.0)
            self._last_result = result
            return result

        # Check chat area
        chat_pixels = self._check_gm_colour(frame, self._cfg.chat_roi)
        if chat_pixels >= self._MIN_PIXEL_THRESHOLD:
            result.detected = True
            result.source = "chat"
            result.pixel_count = chat_pixels
            result.confidence = min(1.0, chat_pixels / 200.0)

        self._last_result = result
        return result

    def _check_gm_colour(self, frame: np.ndarray, roi: List[int]) -> int:
        """Count cyan/light-blue GM-coloured pixels in the given ROI."""
        if len(roi) < 4:
            return 0
        x, y, w, h = self._scale_roi(frame, roi)
        fh, fw = frame.shape[:2]
        x = min(x, fw - 1)
        y = min(y, fh - 1)
        w = min(w, fw - x)
        h = min(h, fh - y)
        if w <= 0 or h <= 0:
            return 0

        crop = frame[y : y + h, x : x + w]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV) if crop.ndim == 3 else None
        if hsv is None:
            return 0

        lower = np.array(self._cfg.gm_hsv_lower, dtype=np.uint8)
        upper = np.array(self._cfg.gm_hsv_upper, dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        return int(np.count_nonzero(mask))

    def _scale_roi(self, frame: np.ndarray, roi: List[int]) -> tuple[int, int, int, int]:
        if len(roi) < 4:
            return (0, 0, 0, 0)
        ref_w = max(1, int(self._cfg.reference_resolution[0]))
        ref_h = max(1, int(self._cfg.reference_resolution[1]))
        frame_h, frame_w = frame.shape[:2]
        scale_x = frame_w / ref_w
        scale_y = frame_h / ref_h
        return (
            int(round(roi[0] * scale_x)),
            int(round(roi[1] * scale_y)),
            max(1, int(round(roi[2] * scale_x))),
            max(1, int(round(roi[3] * scale_y))),
        )

    # ── Confirmed GM ─────────────────────────────────────────────────────

    def _on_gm_confirmed(self, result: GMDetection) -> None:
        """React to confirmed GM detection."""
        self._total_detections += 1
        self._log_cb(
            f"[GM DETECTED] source={result.source} "
            f"pixels={result.pixel_count} action={self._cfg.action.value}"
        )
        _log.warning(
            "GM detected! source=%s pixels=%d action=%s",
            result.source, result.pixel_count, self._cfg.action.value,
        )

        # Emit event
        self._emit("e15", {
            "source": result.source,
            "confidence": result.confidence,
            "action": self._cfg.action.value,
        })

        action = self._cfg.action
        if action == GMAction.PAUSE:
            self._do_pause()
        elif action == GMAction.LOGOUT:
            self._do_logout()
        elif action == GMAction.HUMAN_MIMIC:
            self._do_human_mimic()
        # GMAction.ALERT → event already emitted, nothing else

    def _do_pause(self) -> None:
        """Pause all subsystems until GM is no longer detected."""
        if self._pause_fn:
            self._pause_fn()
            self._log_cb("[G] Paused — waiting for GM to leave.")

        try:
            # Wait until GM is no longer detected
            clear_count = 0
            deadline = time.monotonic() + max(0.0, self._cfg.pause_timeout_s)
            while self._running and clear_count < 5:
                if self._cfg.pause_timeout_s > 0 and time.monotonic() >= deadline:
                    self._log_cb("[G] Pause timeout reached — auto-resuming.")
                    break
                time.sleep(self._cfg.scan_interval * random.uniform(1.5, 2.5))
                result = self._scan_once()
                if not result.detected:
                    clear_count += 1
                else:
                    clear_count = 0
        finally:
            self._consecutive = 0
            if self._resume_fn:
                self._resume_fn()
                self._log_cb("[G] GM no longer detected — resuming.")

    def _do_logout(self) -> None:
        """Send Ctrl+Q / Ctrl+L to log out."""
        if self._pause_fn:
            self._pause_fn()
        try:
            if self._ctrl is not None and hasattr(self._ctrl, "key_combo"):
                # Ctrl+L = logout in Tibia
                self._ctrl.key_combo(0x11, 0x4C)  # VK_CONTROL + 'L'
                self._log_cb("[G] Logout initiated.")
        finally:
            # Resume so reconnect_handler can take over if it reconnects
            if self._resume_fn:
                self._resume_fn()

    def _do_human_mimic(self) -> None:
        """Simulate human-like actions for a period to appear legit."""
        if self._pause_fn:
            self._pause_fn()

        try:
            self._log_cb("[G] Mimicking human behaviour...")
            end_time = time.monotonic() + self._cfg.mimic_duration

            while self._running and time.monotonic() < end_time:
                action = random.choice(["wait", "camera", "inventory", "walk"])

                if action == "wait":
                    time.sleep(random.uniform(2.0, 8.0))
                elif action == "camera" and self._ctrl:
                    # Rotate camera with arrow keys
                    vk = random.choice([0x25, 0x27])  # LEFT or RIGHT arrow
                    self._ctrl.press_key(vk, delay=random.uniform(0.1, 0.4))
                    time.sleep(random.uniform(1.0, 3.0))
                elif action == "inventory" and self._ctrl:
                    # Open/close inventory container (F12 common)
                    self._ctrl.press_key(0x7B, delay=random.uniform(0.05, 0.15))
                    time.sleep(random.uniform(1.0, 4.0))
                elif action == "walk" and self._ctrl:
                    # Random small walk
                    vk = random.choice([0x25, 0x26, 0x27, 0x28])  # arrow key
                    self._ctrl.press_key(vk, delay=random.uniform(0.03, 0.08))
                    time.sleep(random.uniform(0.5, 2.0))
        finally:
            if self._resume_fn:
                self._resume_fn()
                self._log_cb("[G] Mimic phase done — resuming.")

    # ── Event bus ────────────────────────────────────────────────────────

    def _emit(self, event: str, data: Any) -> None:
        if self._event_bus is not None and hasattr(self._event_bus, "emit"):
            try:
                self._event_bus.emit(event, data)
            except Exception:
                pass

    # ── Properties / stats ───────────────────────────────────────────────

    @property
    def config(self) -> GMDetectorConfig:
        return self._cfg

    @property
    def consecutive_count(self) -> int:
        return self._consecutive

    @property
    def total_detections(self) -> int:
        return self._total_detections

    @property
    def total_scans(self) -> int:
        return self._total_scans

    @property
    def last_result(self) -> Optional[GMDetection]:
        return self._last_result

    def stats_snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self._cfg.enabled,
            "action": self._cfg.action.value,
            "total_scans": self._total_scans,
            "total_detections": self._total_detections,
            "consecutive": self._consecutive,
        }
