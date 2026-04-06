"""
Chat Responder — detect incoming private messages and respond automatically.

Monitors the chat area for PM indicators (purple/violet text colour in
Tibia) and sends pre-scripted replies with human-like delays.  GM-specific
messages trigger special handling (polite responses + optional alert).

PM text in Tibia is displayed in a distinctive purple/magenta colour
that is easy to isolate via HSV filtering.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np

_log = logging.getLogger("wn.cr")


# ── Pre-scripted response pools ──────────────────────────────────────────────

_GENERIC_RESPONSES = [
    "hey",
    "hi",
    "hola",
    "sup",
    "?",
    "yes?",
    "busy atm",
    "hunting sorry",
    "not now sry",
    "one sec",
    "brb",
]

_GM_RESPONSES = [
    "hello!",
    "hey, hi!",
    "hola",
    "hi there",
    "hello, how are you?",
    "hey :)",
]

_FOLLOWUP_RESPONSES = [
    "sry was busy",
    "im here now",
    "what's up?",
    "yes?",
    "need something?",
]


# ── Configuration ────────────────────────────────────────────────────────────

@dataclass
class ChatResponderConfig:
    """
    Parameters
    ----------
    enabled : bool
        Master switch.
    chat_roi : List[int]
        [x, y, w, h] of the chat window area on screen.
    scan_interval : float
        Seconds between checking for new PMs.
    response_delay_min : float
        Minimum seconds to wait before responding (simulates reading).
    response_delay_max : float
        Maximum seconds to wait before responding.
    pm_hsv_lower : List[int]
        Lower HSV bound for PM text colour (purple/magenta in Tibia).
    pm_hsv_upper : List[int]
        Upper HSV bound for PM text colour.
    min_pixel_threshold : int
        Minimum purple pixels to consider a PM present.
    cooldown_s : float
        Min seconds between auto-responses (avoid spam loops).
    generic_responses : List[str]
        Possible replies to normal PMs.
    gm_responses : List[str]
        Possible replies to GM PMs.
    max_responses_per_session : int
        Limit responses per session to avoid looking robotic.
    """
    enabled: bool = True
    chat_roi: List[int] = field(
        default_factory=lambda: [7, 570, 600, 200]
    )
    scan_interval: float = 2.0
    response_delay_min: float = 3.0
    response_delay_max: float = 12.0
    # PM text in Tibia: purple/magenta (H~130-160, S~50-255, V~100-255)
    pm_hsv_lower: List[int] = field(default_factory=lambda: [130, 50, 100])
    pm_hsv_upper: List[int] = field(default_factory=lambda: [160, 255, 255])
    min_pixel_threshold: int = 40
    cooldown_s: float = 30.0
    generic_responses: List[str] = field(default_factory=lambda: list(_GENERIC_RESPONSES))
    gm_responses: List[str] = field(default_factory=lambda: list(_GM_RESPONSES))
    max_responses_per_session: int = 20


# ── Chat Responder ───────────────────────────────────────────────────────────

class ChatResponder:
    """
    Background thread that monitors the chat area for PMs and responds.

    Usage::

        responder = ChatResponder(config, event_bus=bus)
        responder.set_frame_getter(cached_getter)
        responder.set_input_controller(ctrl)
        responder.start()
    """

    def __init__(
        self,
        config: Optional[ChatResponderConfig] = None,
        event_bus: Any = None,
    ) -> None:
        self._cfg = config or ChatResponderConfig()
        self._event_bus = event_bus

        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._ctrl: Any = None
        self._log_cb: Callable[[str], None] = lambda m: None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_response_ts = 0.0
        self._total_scans = 0
        self._total_pms_detected = 0
        self._total_responses_sent = 0
        self._last_pm_pixel_count = 0
        # Track previous PM state to detect *new* PMs only
        self._prev_pm_detected = False

    # ── Wiring ───────────────────────────────────────────────────────────

    def set_frame_getter(self, fn: Callable[[], Optional[np.ndarray]]) -> None:
        self._frame_getter = fn

    def set_input_controller(self, ctrl: Any) -> None:
        self._ctrl = ctrl

    def set_log_callback(self, fn: Callable[[str], None]) -> None:
        self._log_cb = fn

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
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

    # ── Monitor loop ─────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while self._running:
            try:
                pm_detected, pixel_count = self._check_for_pm()
                self._last_pm_pixel_count = pixel_count

                # Only respond to *new* PM appearance (rising edge)
                if pm_detected and not self._prev_pm_detected:
                    self._total_pms_detected += 1
                    self._handle_pm(pixel_count)

                self._prev_pm_detected = pm_detected

            except Exception:
                _log.debug("Chat responder error", exc_info=True)

            time.sleep(self._cfg.scan_interval * random.uniform(0.8, 1.25))

    def _check_for_pm(self) -> tuple[bool, int]:
        """Check if there are new PM-coloured pixels in the chat area."""
        self._total_scans += 1

        if self._frame_getter is None:
            return False, 0

        frame = self._frame_getter()
        if frame is None or frame.size == 0:
            return False, 0

        roi = self._cfg.chat_roi
        if len(roi) < 4:
            return False, 0

        x, y, w, h = roi[0], roi[1], roi[2], roi[3]
        fh, fw = frame.shape[:2]
        x = min(x, fw - 1)
        y = min(y, fh - 1)
        w = min(w, fw - x)
        h = min(h, fh - y)
        if w <= 0 or h <= 0:
            return False, 0

        crop = frame[y : y + h, x : x + w]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV) if crop.ndim == 3 else None
        if hsv is None:
            return False, 0

        lower = np.array(self._cfg.pm_hsv_lower, dtype=np.uint8)
        upper = np.array(self._cfg.pm_hsv_upper, dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        count = int(np.count_nonzero(mask))

        return count >= self._cfg.min_pixel_threshold, count

    # ── PM handling ──────────────────────────────────────────────────────

    def _handle_pm(self, pixel_count: int) -> None:
        """Process a detected PM: wait, then respond."""
        now = time.monotonic()

        # Cooldown check
        if (now - self._last_response_ts) < self._cfg.cooldown_s:
            return

        # Session limit
        if self._total_responses_sent >= self._cfg.max_responses_per_session:
            return

        self._emit("e16", {"pixel_count": pixel_count})
        self._log_cb(f"[A] PM detected (pixels={pixel_count})")

        # Human-like reading delay
        delay = random.uniform(
            self._cfg.response_delay_min,
            self._cfg.response_delay_max,
        )
        time.sleep(delay)

        if not self._running:
            return

        # Pick response
        # High pixel count near GM colour range → use GM responses
        if pixel_count > 300:
            response = random.choice(self._cfg.gm_responses)
        else:
            response = random.choice(self._cfg.generic_responses)

        self._send_response(response)

    def _send_response(self, text: str) -> None:
        """Type a response in the chat."""
        if self._ctrl is None:
            return
        if not hasattr(self._ctrl, "key_combo"):
            return

        # Open reply (Ctrl+R in Tibia replies to last PM)
        self._ctrl.key_combo(0x11, 0x52)  # VK_CONTROL + 'R'
        time.sleep(random.uniform(0.3, 0.8))

        # Type the response character by character with human jitter
        if hasattr(self._ctrl, "type_text"):
            self._ctrl.type_text(text)
        else:
            for char in text:
                vk = ord(char.upper()) if char.isalpha() else ord(char)
                self._ctrl.press_key(vk, delay=random.uniform(0.03, 0.12))
                time.sleep(random.uniform(0.05, 0.20))

        # Press Enter to send
        time.sleep(random.uniform(0.2, 0.6))
        self._ctrl.press_key(0x0D)  # VK_RETURN

        self._total_responses_sent += 1
        self._last_response_ts = time.monotonic()
        self._log_cb(f"[A] Responded: {text!r}")
        self._emit("e17", {"text": text})

    # ── Event bus ────────────────────────────────────────────────────────

    def _emit(self, event: str, data: Any) -> None:
        if self._event_bus is not None and hasattr(self._event_bus, "emit"):
            try:
                self._event_bus.emit(event, data)
            except Exception:
                pass

    # ── Properties / stats ───────────────────────────────────────────────

    @property
    def config(self) -> ChatResponderConfig:
        return self._cfg

    @property
    def total_scans(self) -> int:
        return self._total_scans

    @property
    def total_pms_detected(self) -> int:
        return self._total_pms_detected

    @property
    def total_responses_sent(self) -> int:
        return self._total_responses_sent

    def stats_snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self._cfg.enabled,
            "total_scans": self._total_scans,
            "pms_detected": self._total_pms_detected,
            "responses_sent": self._total_responses_sent,
        }
