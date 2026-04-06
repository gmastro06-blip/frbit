"""
Frame Quality Checker
---------------------
Validates captured frames before processing by vision modules.

Detects:
- Black / near-black frames (capture failure)
- Blurry frames (window being moved / alt-tabbed)
- Wrong resolution
- Corrupt / empty frames

Usage:
    from src.frame_quality import FrameQualityChecker, FrameQuality

    checker = FrameQualityChecker()
    quality = checker.check(frame)
    if quality != FrameQuality.OK:
        log(f"Bad frame: {quality.name}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Tuple, Any

import cv2
import numpy as np


class FrameQuality(Enum):
    """Result of a frame quality check."""
    OK = auto()
    BLACK = auto()
    BLURRY = auto()
    WRONG_SIZE = auto()
    CORRUPT = auto()


@dataclass
class FrameQualityConfig:
    """Thresholds for frame quality detection.

    Attributes
    ----------
    black_mean_threshold : float
        Frames with global mean brightness below this are "black".
    blur_laplacian_threshold : float
        Frames with Laplacian variance below this are "blurry".
    expected_width : int
        Expected frame width (0 = skip size check).
    expected_height : int
        Expected frame height (0 = skip size check).
    """
    black_mean_threshold: float = 5.0
    blur_laplacian_threshold: float = 50.0
    expected_width: int = 0
    expected_height: int = 0


class FrameQualityChecker:
    """Stateless frame quality validator."""

    def __init__(self, config: Optional[FrameQualityConfig] = None) -> None:
        self._cfg = config or FrameQualityConfig()

    @property
    def config(self) -> FrameQualityConfig:
        return self._cfg

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def is_black(self, frame: np.ndarray) -> bool:
        """True when the frame is (near-) completely black."""
        if frame is None or frame.size == 0:
            return True
        return float(np.mean(frame)) < self._cfg.black_mean_threshold

    def is_blurry(self, frame: np.ndarray) -> bool:
        """True when the frame appears blurry (low edge content)."""
        if frame is None or frame.size == 0:
            return True
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return variance < self._cfg.blur_laplacian_threshold

    def is_valid_resolution(
        self,
        frame: np.ndarray,
        expected_w: int = 0,
        expected_h: int = 0,
    ) -> bool:
        """True when frame matches the expected resolution."""
        ew = expected_w or self._cfg.expected_width
        eh = expected_h or self._cfg.expected_height
        if ew == 0 and eh == 0:
            return True  # no expectation set
        h, w = frame.shape[:2]
        if ew > 0 and w != ew:
            return False
        if eh > 0 and h != eh:
            return False
        return True

    def is_corrupt(self, frame: np.ndarray) -> bool:
        """True when the frame is None, empty, or has unexpected shape."""
        if frame is None:
            return True
        if frame.size == 0:
            return True
        if frame.ndim < 2:
            return True
        return False

    # ------------------------------------------------------------------
    # Combined check
    # ------------------------------------------------------------------

    def check(self, frame: Optional[np.ndarray]) -> FrameQuality:
        """Run all quality checks and return the first failure, or OK.

        Check order: corrupt → wrong size → black → blurry → OK.
        """
        if frame is None or self.is_corrupt(frame):
            return FrameQuality.CORRUPT
        if not self.is_valid_resolution(frame):
            return FrameQuality.WRONG_SIZE
        if self.is_black(frame):
            return FrameQuality.BLACK
        if self.is_blurry(frame):
            return FrameQuality.BLURRY
        return FrameQuality.OK

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnostics(self, frame: Optional[np.ndarray]) -> dict[str, Any]:
        """Return a dict with all quality metrics for the given frame."""
        if frame is None:
            return {
                "quality": FrameQuality.CORRUPT.name,
                "is_corrupt": True,
            }
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        return {
            "quality": self.check(frame).name,
            "width": w,
            "height": h,
            "mean_brightness": round(float(np.mean(frame)), 2),
            "laplacian_variance": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
            "is_corrupt": self.is_corrupt(frame),
            "is_black": self.is_black(frame),
            "is_blurry": self.is_blurry(frame),
            "is_valid_resolution": self.is_valid_resolution(frame),
        }
