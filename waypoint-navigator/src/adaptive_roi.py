"""
Adaptive ROI — anchor-based UI element auto-detection.

Given a set of anchor templates (small images of known UI corners / labels),
detect their screen positions via template matching and derive accurate
ROI rectangles for HP bars, minimap, battle list, etc.

This allows working across different resolutions and UI layouts
without manual calibration.

Fase 6.5 — Adaptive ROI for vision pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

_log = logging.getLogger("wn.ar")

from src.config_paths import TEMPLATES_DIR as _TEMPLATES_DIR

_ANCHORS_META = _TEMPLATES_DIR / "anchors" / "anchors_meta.json"

# Reference ROIs at 1920×1080 — used for proportional scaling fallback
_REFERENCE_ROIS: Dict[str, List[int]] = {
    "hp_bar":           [12, 28, 769, 12],
    "mp_bar":           [788, 28, 768, 12],
    "minimap":          [1750, 35, 113, 115],
    "battle_list":      [1565, 448, 170, 200],
    "condition_icons":  [1709, 462, 200, 30],
}


# ── Configuration ────────────────────────────────────────────────────────────
@dataclass
class AnchorTemplate:
    """
    A small template image used to locate a UI element on screen.

    Parameters
    ----------
    name : str
        Human-readable identifier (e.g. "hp_bar_left", "minimap_top_left").
    image : np.ndarray
        Template image (BGR).  Can be loaded via ``load_anchor()``.
    offset : Tuple[int, int]
        (dx, dy) offset from the match location to the ROI origin.
    expected_size : Tuple[int, int]
        (width, height) of the ROI derived from this anchor.
    confidence : float
        Minimum match confidence (0.0–1.0).
    """

    name: str
    image: np.ndarray
    offset: Tuple[int, int] = (0, 0)
    expected_size: Tuple[int, int] = (0, 0)
    confidence: float = 0.70


@dataclass
class AdaptiveROIConfig:
    """
    Parameters
    ----------
    reference_width : int
        Reference resolution width the default ROIs are based on.
    reference_height : int
        Reference resolution height.
    scale_tolerance : float
        Allowed deviation from expected scale ratio (0.0–1.0).
    cache_hits : int
        After this many consecutive identical detections, stop re-scanning.
    """

    reference_width: int = 1920
    reference_height: int = 1080
    scale_tolerance: float = 0.15
    cache_hits: int = 10


# ── ROI result ───────────────────────────────────────────────────────────────
@dataclass
class DetectedROI:
    """Result of an anchor-based ROI detection."""

    name: str
    roi: List[int]  # [x, y, w, h]
    confidence: float
    anchor_pos: Tuple[int, int]  # (x, y) of matched anchor

    @property
    def x(self) -> int:
        return self.roi[0]

    @property
    def y(self) -> int:
        return self.roi[1]

    @property
    def w(self) -> int:
        return self.roi[2]

    @property
    def h(self) -> int:
        return self.roi[3]

    def crop(self, frame: np.ndarray) -> np.ndarray:
        """Crop ROI from frame."""
        return frame[self.y : self.y + self.h, self.x : self.x + self.w]


# ── Detector ─────────────────────────────────────────────────────────────────
class AdaptiveROIDetector:
    """
    Detect UI element positions via anchor template matching.

    Usage::

        detector = AdaptiveROIDetector()
        detector.register_anchor(AnchorTemplate("hp_left", tmpl_img, (0, 0), (200, 12)))
        results = detector.detect(frame)
        hp_roi = detector.get_roi("hp_left")
    """

    def __init__(self, config: Optional[AdaptiveROIConfig] = None) -> None:
        self._config = config or AdaptiveROIConfig()
        self._anchors: List[AnchorTemplate] = []
        self._cached: Dict[str, DetectedROI] = {}
        self._cache_count: int = 0
        self._proportional_cache: Dict[str, List[int]] = {}

    # ── Auto-loading ─────────────────────────────────────────────────
    def load_anchors_from_dir(self) -> int:
        """Load anchor templates from cache/templates/anchors/.

        If ``anchors_meta.json`` exists alongside the PNGs, uses it for
        offset and expected_size metadata.  Otherwise registers each image
        with zero offset and its own size.

        Returns the number of anchors successfully loaded.
        """
        anchors_dir = _TEMPLATES_DIR / "anchors"
        if not anchors_dir.is_dir():
            _log.debug("Anchor templates dir not found: %s", anchors_dir)
            return 0

        meta: Dict[str, Dict[str, Any]] = {}
        if _ANCHORS_META.is_file():
            try:
                meta = json.loads(_ANCHORS_META.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                _log.warning("Failed to read anchors_meta.json: %s", exc)

        count = 0
        for ext in ("*.png", "*.jpg", "*.bmp"):
            for path in sorted(anchors_dir.glob(ext)):
                img = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                name = path.stem
                m = meta.get(name, {})
                offset_raw = m.get("offset", (0, 0))
                expected_size_raw = m.get("expected_size", (0, 0))
                offset = (
                    int(offset_raw[0]) if isinstance(offset_raw, (list, tuple)) and len(offset_raw) >= 2 else 0,
                    int(offset_raw[1]) if isinstance(offset_raw, (list, tuple)) and len(offset_raw) >= 2 else 0,
                )
                expected_size = (
                    int(expected_size_raw[0]) if isinstance(expected_size_raw, (list, tuple)) and len(expected_size_raw) >= 2 else 0,
                    int(expected_size_raw[1]) if isinstance(expected_size_raw, (list, tuple)) and len(expected_size_raw) >= 2 else 0,
                )
                anchor = AnchorTemplate(
                    name=name,
                    image=img,
                    offset=offset,
                    expected_size=expected_size,
                    confidence=float(m.get("confidence", 0.70)),
                )
                self._anchors.append(anchor)
                count += 1
                _log.info("Loaded anchor: %s (%dx%d)", name, img.shape[1], img.shape[0])

        if count:
            _log.info("AdaptiveROI: %d anchors loaded from %s", count, anchors_dir)
        else:
            _log.info(
                "AdaptiveROI: no anchor PNGs in %s — proportional fallback active",
                anchors_dir,
            )
        return count

    # ── Proportional fallback ─────────────────────────────────────────
    def get_proportional_roi(
        self,
        name: str,
        frame_width: int,
        frame_height: int,
    ) -> Optional[List[int]]:
        """Scale a reference 1920×1080 ROI proportionally to the current frame size.

        Returns [x, y, w, h] or None if the ROI name is unknown.
        """
        ref = _REFERENCE_ROIS.get(name)
        if ref is None:
            return None
        sx = frame_width / self._config.reference_width
        sy = frame_height / self._config.reference_height
        return [
            int(ref[0] * sx),
            int(ref[1] * sy),
            int(ref[2] * sx),
            int(ref[3] * sy),
        ]

    def get_all_proportional_rois(
        self,
        frame_width: int,
        frame_height: int,
    ) -> Dict[str, List[int]]:
        """Return proportionally scaled versions of all reference ROIs."""
        sx = frame_width / self._config.reference_width
        sy = frame_height / self._config.reference_height
        return {
            name: [
                int(roi[0] * sx),
                int(roi[1] * sy),
                int(roi[2] * sx),
                int(roi[3] * sy),
            ]
            for name, roi in _REFERENCE_ROIS.items()
        }

    def detect_or_fallback(
        self,
        frame: np.ndarray,
    ) -> Dict[str, List[int]]:
        """Try anchor-based detection; fall back to proportional scaling.

        Returns a dict mapping ROI name → [x, y, w, h].
        Anchor results take precedence over proportional fallback.
        """
        fh, fw = frame.shape[:2]

        # Start with proportional fallback for all known ROIs
        result = self.get_all_proportional_rois(fw, fh)

        # Override with anchor-based detections (higher accuracy)
        if self._anchors:
            detected = self.detect_cached(frame)
            for name, droi in detected.items():
                result[name] = droi.roi

        return result

    # ── Anchor management ────────────────────────────────────────────────
    def register_anchor(self, anchor: AnchorTemplate) -> None:
        """Register an anchor template for detection."""
        self._anchors.append(anchor)

    def register_anchors(self, anchors: List[AnchorTemplate]) -> None:
        """Register multiple anchor templates."""
        self._anchors.extend(anchors)

    @property
    def anchor_names(self) -> List[str]:
        return [a.name for a in self._anchors]

    @property
    def anchor_count(self) -> int:
        return len(self._anchors)

    # ── Detection ────────────────────────────────────────────────────────
    def detect(self, frame: np.ndarray) -> Dict[str, DetectedROI]:
        """
        Run anchor matching on the given frame.

        Returns dict mapping anchor name → DetectedROI for successful matches.
        Failed matches are excluded.
        """
        if frame is None or frame.size == 0:
            return {}

        results: Dict[str, DetectedROI] = {}
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame

        for anchor in self._anchors:
            roi = self._match_anchor(gray, anchor, frame.shape)
            if roi is not None:
                results[anchor.name] = roi

        # Cache management
        if results:
            if results == self._cached:
                self._cache_count += 1
            else:
                self._cached = dict(results)
                self._cache_count = 1

        return results

    def detect_cached(self, frame: np.ndarray) -> Dict[str, DetectedROI]:
        """
        Like ``detect()`` but skips re-scanning when cache is stable.
        """
        if self._cache_count >= self._config.cache_hits and self._cached:
            return dict(self._cached)
        return self.detect(frame)

    def get_roi(self, name: str) -> Optional[DetectedROI]:
        """Return the last detected ROI for the given anchor name."""
        return self._cached.get(name)

    def get_roi_list(self, name: str) -> Optional[List[int]]:
        """Return [x, y, w, h] for the given anchor name, or None."""
        roi = self._cached.get(name)
        return roi.roi if roi is not None else None

    # ── Scale helpers ────────────────────────────────────────────────────
    def compute_scale(self, frame: np.ndarray) -> Tuple[float, float]:
        """Compute (sx, sy) scale factors from frame vs reference resolution."""
        h, w = frame.shape[:2]
        sx = w / self._config.reference_width
        sy = h / self._config.reference_height
        return sx, sy

    def scale_roi(
        self,
        roi: List[int],
        sx: float,
        sy: float,
    ) -> List[int]:
        """Scale a reference ROI [x, y, w, h] by factors (sx, sy)."""
        x, y, w, h = roi
        return [
            int(x * sx),
            int(y * sy),
            int(w * sx),
            int(h * sy),
        ]

    # ── Internal ─────────────────────────────────────────────────────────
    def _match_anchor(
        self,
        gray: np.ndarray,
        anchor: AnchorTemplate,
        frame_shape: tuple[int, ...],
    ) -> Optional[DetectedROI]:
        """Match a single anchor template against the frame."""
        tmpl = anchor.image
        if tmpl.ndim == 3:
            tmpl = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY)

        th, tw = tmpl.shape[:2]
        gh, gw = gray.shape[:2]

        if tw > gw or th > gh:
            return None

        result = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < anchor.confidence:
            return None

        ax, ay = max_loc
        dx, dy = anchor.offset
        ew, eh = anchor.expected_size

        roi_x = ax + dx
        roi_y = ay + dy
        roi_w = ew if ew > 0 else tw
        roi_h = eh if eh > 0 else th

        # Clamp to frame bounds
        roi_x = max(0, roi_x)
        roi_y = max(0, roi_y)
        roi_w = min(roi_w, gw - roi_x)
        roi_h = min(roi_h, gh - roi_y)

        if roi_w <= 0 or roi_h <= 0:
            return None

        return DetectedROI(
            name=anchor.name,
            roi=[roi_x, roi_y, roi_w, roi_h],
            confidence=float(max_val),
            anchor_pos=(ax, ay),
        )

    # ── Stats ────────────────────────────────────────────────────────────
    @property
    def config(self) -> AdaptiveROIConfig:
        return self._config

    @property
    def cached_rois(self) -> Dict[str, DetectedROI]:
        return dict(self._cached)

    @property
    def cache_hit_count(self) -> int:
        return self._cache_count

    def clear_cache(self) -> None:
        """Reset cached detections."""
        self._cached.clear()
        self._cache_count = 0

    def stats_snapshot(self) -> Dict[str, Any]:
        """Return summary for telemetry / logging."""
        return {
            "anchor_count": self.anchor_count,
            "cached_rois": {k: v.roi for k, v in self._cached.items()},
            "cache_hits": self._cache_count,
        }


# ── Utility ──────────────────────────────────────────────────────────────────
def load_anchor(
    path: str | Path,
    name: str,
    offset: Tuple[int, int] = (0, 0),
    expected_size: Tuple[int, int] = (0, 0),
    confidence: float = 0.70,
) -> AnchorTemplate:
    """Load an anchor template from an image file."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Cannot load anchor image: {path}")
    return AnchorTemplate(
        name=name,
        image=img,
        offset=offset,
        expected_size=expected_size,
        confidence=confidence,
    )
