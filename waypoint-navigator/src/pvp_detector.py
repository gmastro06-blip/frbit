"""
PvP detection — detect other players on screen and trigger safety actions.

Monitors the game screen for player name-plates or skull indicators
that distinguish other players from NPCs/monsters.

Fase 7.1 — PvP detection (another player on screen → pause/flee).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np

_log = logging.getLogger("wn.pd")

from src.config_paths import TEMPLATES_DIR as _TEMPLATES_DIR


# ── Configuration ────────────────────────────────────────────────────────────
class PvPAction(Enum):
    """Action to take when a player is detected."""

    IGNORE = "ignore"
    WARN = "warn"
    PAUSE = "pause"
    FLEE = "flee"
    LOGOUT = "logout"


@dataclass
class PvPConfig:
    """
    Parameters
    ----------
    enabled : bool
        Master switch for PvP detection.
    action : PvPAction
        What to do when another player is detected.
    battle_list_roi : List[int]
        [x, y, w, h] of the battle list area to scan.
    skull_templates : List[np.ndarray]
        Template images of skull indicators (white, yellow, red, black).
    name_color_ranges : List[tuple]
        HSV color ranges for player name-plates (white text on dark bg).
    confidence : float
        Minimum template match confidence.
    cooldown_s : float
        Minimum seconds between consecutive detections.
    min_consecutive : int
        Require N consecutive detections before acting (noise filter).
    safe_names : List[str]
        Player names to ignore (party members, friends).
    """

    enabled: bool = True
    action: PvPAction = PvPAction.WARN
    battle_list_roi: List[int] = field(default_factory=lambda: [1568, 175, 162, 345])
    skull_templates: List[np.ndarray] = field(default_factory=list)
    name_color_ranges: List[tuple[int, int, float]] = field(default_factory=list)
    confidence: float = 0.70
    cooldown_s: float = 2.0
    min_consecutive: int = 2
    safe_names: List[str] = field(default_factory=list)

    def validate(self) -> None:
        """Raise ``ValueError`` on invalid config values."""
        if len(self.battle_list_roi) != 4:
            raise ValueError(
                f"battle_list_roi must have 4 elements, got {len(self.battle_list_roi)}"
            )
        if any(v < 0 for v in self.battle_list_roi):
            raise ValueError(
                f"battle_list_roi contains negative values: {self.battle_list_roi}"
            )
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if self.cooldown_s < 0:
            raise ValueError(f"cooldown_s must be >= 0, got {self.cooldown_s}")
        if self.min_consecutive < 1:
            raise ValueError(f"min_consecutive must be >= 1, got {self.min_consecutive}")


# ── Detection result ─────────────────────────────────────────────────────────
@dataclass
class PvPDetection:
    """Result of a PvP scan."""

    detected: bool = False
    player_count: int = 0
    confidence: float = 0.0
    skull_positions: List[tuple[int, int, float]] = field(default_factory=list)
    timestamp: float = 0.0


# ── Detector ─────────────────────────────────────────────────────────────────
class PvPDetector:
    """
    Scan battle list area for player indicators (skulls, name colours).

    Usage::

        detector = PvPDetector(config)
        result = detector.scan(frame)
        if result.detected:
            detector.recommended_action  # PvPAction
    """

    def __init__(
        self,
        config: Optional[PvPConfig] = None,
        event_bus: Any = None,
        *,
        auto_load: bool = True,
    ) -> None:
        self._config = config or PvPConfig()
        self._event_bus = event_bus
        self._consecutive: int = 0
        self._last_detection_ts: float = 0.0
        self._total_detections: int = 0
        self._total_scans: int = 0
        self._last_result: Optional[PvPDetection] = None
        if auto_load and not self._config.skull_templates:
            self._auto_load_skull_templates()

    # ── Template loading ─────────────────────────────────────────────────
    def _auto_load_skull_templates(self) -> None:
        """Load skull PNGs from cache/templates/skulls/ if available."""
        skulls_dir = _TEMPLATES_DIR / "skulls"
        if not skulls_dir.is_dir():
            _log.debug("Skull templates dir not found: %s", skulls_dir)
            return
        loaded: list[np.ndarray] = []
        for ext in ("*.png", "*.jpg", "*.bmp"):
            for path in sorted(skulls_dir.glob(ext)):
                img = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if img is not None:
                    loaded.append(img)
                    _log.info("Loaded skull template: %s", path.name)
        if loaded:
            self._config.skull_templates = loaded
            _log.info("PvP: %d skull templates loaded", len(loaded))
        else:
            _log.warning(
                "PvP: no skull templates in %s — using color fallback",
                skulls_dir,
            )

    # ── Scanning ─────────────────────────────────────────────────────────
    def scan(self, frame: np.ndarray) -> PvPDetection:
        """
        Scan the battle list for player indicators.

        Returns PvPDetection with detection status.
        """
        self._total_scans += 1
        result = PvPDetection(timestamp=time.monotonic())

        if not self._config.enabled or frame is None or frame.size == 0:
            self._consecutive = 0
            self._last_result = result
            return result

        # Crop battle list ROI
        roi = self._config.battle_list_roi
        if len(roi) >= 4:
            x, y, w, h = roi[0], roi[1], roi[2], roi[3]
            fh, fw = frame.shape[:2]
            x = min(x, fw - 1)
            y = min(y, fh - 1)
            w = min(w, fw - x)
            h = min(h, fh - y)
            if w > 0 and h > 0:
                crop = frame[y : y + h, x : x + w]
            else:
                crop = frame
        else:
            crop = frame

        # Check skulls via template matching
        skull_hits = self._check_skull_templates(crop)
        result.skull_positions = skull_hits
        result.player_count = len(skull_hits)

        # Check name colour patterns (fallback if no skull templates)
        if not skull_hits and not self._config.skull_templates:
            color_count = self._check_name_colors(crop)
            result.player_count = color_count

        result.detected = result.player_count > 0

        if result.detected:
            result.confidence = max(
                (c for _, _, c in skull_hits),
                default=0.5,
            )

        # Consecutive tracking
        if result.detected:
            self._consecutive += 1
        else:
            self._consecutive = 0

        # Cooldown + min-consecutive filter
        now = time.monotonic()
        confirmed = (
            result.detected
            and self._consecutive >= self._config.min_consecutive
            and (now - self._last_detection_ts) >= self._config.cooldown_s
        )

        if confirmed:
            self._total_detections += 1
            self._last_detection_ts = now
            self._emit("e18", {
                "player_count": result.player_count,
                "action": self._config.action.value,
                "confidence": result.confidence,
            })
            _log.warning(
                "PvP detected! %d player(s), action=%s",
                result.player_count,
                self._config.action.value,
            )

        self._last_result = result
        return result

    # ── Template matching ────────────────────────────────────────────────
    def _check_skull_templates(self, crop: np.ndarray) -> List[tuple[int, int, float]]:
        """Match skull templates. Returns [(x, y, confidence), ...]."""
        hits: List[tuple[int, int, float]] = []
        if not self._config.skull_templates:
            return hits

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop

        for tmpl in self._config.skull_templates:
            t = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if tmpl.ndim == 3 else tmpl
            th, tw = t.shape[:2]
            gh, gw = gray.shape[:2]
            if tw > gw or th > gh:
                continue

            result = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= self._config.confidence)
            for pt_y, pt_x in zip(*locations):
                hits.append((int(pt_x), int(pt_y), float(result[pt_y, pt_x])))

        return hits

    def _check_name_colors(self, crop: np.ndarray) -> int:
        """
        Count player-like name plates by color analysis.

        Player names in Tibia appear as white/coloured text on dark background.
        This is a heuristic fallback when no skull templates are available.
        """
        if crop.size == 0:
            return 0

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV) if crop.ndim == 3 else None
        if hsv is None:
            return 0

        # Look for bright white text (player names): S < 30, V > 200
        mask = cv2.inRange(hsv, (0, 0, 200), (180, 30, 255))  # type: ignore[call-overload]
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Filter by size (name-plate like: wider than tall, minimum area)
        name_plates = 0
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            if area >= 100 and w > h * 2:
                name_plates += 1

        return name_plates

    # ── Event bus ────────────────────────────────────────────────────────
    def _emit(self, event: str, data: Any) -> None:
        if self._event_bus is not None and hasattr(self._event_bus, "emit"):
            try:
                self._event_bus.emit(event, data)
            except Exception:
                pass

    # ── Properties ───────────────────────────────────────────────────────
    @property
    def config(self) -> PvPConfig:
        return self._config

    @property
    def recommended_action(self) -> PvPAction:
        """Action to take based on current detection state."""
        if self._consecutive >= self._config.min_consecutive:
            return self._config.action
        return PvPAction.IGNORE

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
    def last_result(self) -> Optional[PvPDetection]:
        return self._last_result

    def stats_snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "action": self._config.action.value,
            "total_scans": self._total_scans,
            "total_detections": self._total_detections,
            "consecutive": self._consecutive,
        }
