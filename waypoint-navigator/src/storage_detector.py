"""
storage_detector.py
-------------------
Vision module: given a frame, return a StorageState describing every open
storage surface.

Detection pipeline per container window
  1. find_all_containers(frame)  →  list of bounding boxes (Canny + contours,
                                    reuses ui_detection logic but returns ALL
                                    matches, not just the largest)
  2. For each box: crop title bar (top TITLE_H pixels)
  3. read_title(crop)            →  lowercase ASCII string
  4. classify_title(text)        →  StorageSurface
  5. Assemble ContainerWindow list → StorageState

OCR strategy
  Title bars in Tibia are dark backgrounds with 1–3 word white/light labels.
  We use a two-tier approach to avoid paying EasyOCR startup cost on every
  frame:
    a) Fast-path: template matching against pre-rendered title-text sprites
       (optional; populated by calling StorageDetector.register_title_template)
    b) Slow-path: EasyOCR `readtext` on the title bar crop
       (initialised lazily, same pattern as hpmp_detector.py)

All pixel operations are resolution-independent: every threshold or size is
expressed relative to `ref_width` / `ref_height` and scaled to the actual
frame at runtime.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .storage_state import ContainerWindow, StorageSurface, StorageState

# ---------------------------------------------------------------------------
# Title-text → surface mapping (patterns checked in order; first match wins).
# All strings are compared against a lowercased, stripped OCR result.
# ---------------------------------------------------------------------------
_TITLE_PATTERNS: List[Tuple[re.Pattern[str], StorageSurface]] = [
    (re.compile(r"store\s+inbox"),          StorageSurface.STORE_INBOX),
    (re.compile(r"\binbox\b"),              StorageSurface.INBOX),
    (re.compile(r"\bstash\b"),              StorageSurface.STASH),
    (re.compile(r"manage\s+containers?"),   StorageSurface.MANAGE_CONTAINERS),
    (re.compile(r"depot\s+(chest|locker|box)"), StorageSurface.DEPOT_CHEST),
    (re.compile(r"\bdepot\b"),              StorageSurface.DEPOT_CHEST),
]

# Height (px at ref 1080p) of the title bar we crop for OCR
_TITLE_H_REF = 18

# Minimum container dimensions at ref 1080p
_MIN_W_REF = 100
_MIN_H_REF = 80

# ---------------------------------------------------------------------------


@dataclass
class StorageDetectorConfig:
    """Tunable parameters for StorageDetector."""

    ref_width: int = 1920
    ref_height: int = 1080
    # OCR confidence below which we ignore a reading
    ocr_min_confidence: float = 0.40
    # Maximum number of containers to return (prevents runaway on busy screens)
    max_containers: int = 8
    # Search zone for containers: fraction of frame width from the right edge
    search_right_fraction: float = 0.60
    # If True, also search the full frame (slower; catches unusual layouts)
    full_frame_fallback: bool = False
    # Stale threshold: re-detect if state is older than this many seconds
    state_ttl_s: float = 0.3


class StorageDetector:
    """
    Frame → StorageState.

    Usage::

        detector = StorageDetector()
        detector.set_frame_getter(lambda: session.get_frame())
        state = detector.detect()
        if state.has(StorageSurface.STASH):
            stash_window = state.find(StorageSurface.STASH)
    """

    def __init__(
        self,
        config: Optional[StorageDetectorConfig] = None,
        *,
        ocr_reader: Any = None,          # injected EasyOCR reader (optional)
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cfg = config or StorageDetectorConfig()
        self._log = log_fn or (lambda msg: None)
        # EasyOCR lazy init
        self._ocr_reader: Any = ocr_reader
        self._ocr_lock = threading.Lock()
        # Optional pre-rendered title templates {label: (surface, ndarray)}
        self._title_templates: Dict[str, Tuple[StorageSurface, np.ndarray]] = {}
        # Frame getter registered externally
        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        # Cache
        self._last_state: Optional[StorageState] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def set_frame_getter(self, getter: Callable[[], Optional[np.ndarray]]) -> None:
        self._frame_getter = getter

    def register_title_template(
        self, label: str, surface: StorageSurface, template: np.ndarray
    ) -> None:
        """Register a pre-rendered title sprite for fast-path matching."""
        self._title_templates[label.lower()] = (surface, template)

    def detect(self, frame: Optional[np.ndarray] = None) -> StorageState:
        """
        Detect all open storage windows in *frame* (or fetch via frame_getter).

        Returns a cached result if it is still within ``config.state_ttl_s``.
        """
        if frame is None and self._frame_getter is not None:
            frame = self._frame_getter()

        if frame is None:
            return StorageState(surface=StorageSurface.UNKNOWN)

        # Return cached state if fresh enough
        if (
            self._last_state is not None
            and not self._last_state.is_stale(self._cfg.state_ttl_s)
        ):
            return self._last_state

        windows = self._find_all_containers(frame)
        primary = self._primary_surface(windows)
        state = StorageState(
            surface=primary,
            open_windows=windows,
            detected_at=time.monotonic(),
            frame_size=(frame.shape[1], frame.shape[0]),
        )
        self._last_state = state
        return state

    def invalidate(self) -> None:
        """Force re-detection on next call to detect()."""
        self._last_state = None

    # ── Container discovery ───────────────────────────────────────────────────

    def _find_all_containers(self, frame: np.ndarray) -> List[ContainerWindow]:
        """Return ContainerWindow for every container panel visible in frame."""
        fh, fw = frame.shape[:2]
        scale_x = fw / self._cfg.ref_width
        scale_y = fh / self._cfg.ref_height

        min_w = max(40, int(_MIN_W_REF * scale_x))
        min_h = max(40, int(_MIN_H_REF * scale_y))

        # Search zone: right portion of the frame (where Tibia's sidebars live)
        search_x = int(fw * (1.0 - self._cfg.search_right_fraction))
        search_roi = (search_x, 0, fw - search_x, fh)

        boxes = self._canny_boxes(frame, search_roi, min_w, min_h)
        if not boxes and self._cfg.full_frame_fallback:
            boxes = self._canny_boxes(frame, (0, 0, fw, fh), min_w, min_h)

        windows: List[ContainerWindow] = []
        for box in boxes[: self._cfg.max_containers]:
            title, surface, conf = self._identify_window(frame, box, scale_y)
            windows.append(
                ContainerWindow(
                    roi=box,
                    title=title,
                    surface=surface,
                    confidence=conf,
                )
            )
        return windows

    def _canny_boxes(
        self,
        frame: np.ndarray,
        search_roi: Tuple[int, int, int, int],
        min_w: int,
        min_h: int,
    ) -> List[Tuple[int, int, int, int]]:
        """Canny-edge contour scan, returns deduplicated bounding boxes."""
        rx, ry, rw, rh = search_roi
        fh, fw = frame.shape[:2]
        rx = max(0, min(rx, fw))
        ry = max(0, min(ry, fh))
        crop = frame[ry: min(ry + rh, fh), rx: min(rx + rw, fw)]
        if crop.size == 0:
            return []

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        edges = cv2.Canny(gray, 30, 120)
        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        boxes: List[Tuple[int, int, int, int]] = []
        for cnt in contours:
            bx, by, bw, bh = cv2.boundingRect(cnt)
            if bw < min_w or bh < min_h:
                continue
            aspect = bw / max(bh, 1)
            if not (0.2 < aspect < 4.0):
                continue
            abs_box = (rx + bx, ry + by, bw, bh)
            if not self._overlaps_existing(abs_box, boxes, iou_threshold=0.4):
                boxes.append(abs_box)

        # Sort: larger area first (primary container is usually the biggest)
        boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
        return boxes

    @staticmethod
    def _overlaps_existing(
        box: Tuple[int, int, int, int],
        existing: List[Tuple[int, int, int, int]],
        iou_threshold: float,
    ) -> bool:
        bx, by, bw, bh = box
        for ex, ey, ew, eh in existing:
            ix = max(bx, ex)
            iy = max(by, ey)
            ix2 = min(bx + bw, ex + ew)
            iy2 = min(by + bh, ey + eh)
            if ix2 <= ix or iy2 <= iy:
                continue
            inter = (ix2 - ix) * (iy2 - iy)
            union = bw * bh + ew * eh - inter
            if inter / max(union, 1) > iou_threshold:
                return True
        return False

    # ── Window identification ─────────────────────────────────────────────────

    def _identify_window(
        self,
        frame: np.ndarray,
        box: Tuple[int, int, int, int],
        scale_y: float,
    ) -> Tuple[str, StorageSurface, float]:
        """Return (title_text, surface, confidence) for one container box."""
        title_h = max(12, int(_TITLE_H_REF * scale_y))
        bx, by, bw, bh = box
        fh, fw = frame.shape[:2]

        # Crop just the title bar
        title_crop = frame[
            max(0, by): min(by + title_h, fh),
            max(0, bx): min(bx + bw, fw),
        ]
        if title_crop.size == 0:
            return "", StorageSurface.UNKNOWN, 0.0

        # Fast path: template matching
        surface, conf = self._match_template(title_crop)
        if surface != StorageSurface.UNKNOWN:
            return surface.value, surface, conf

        # Slow path: OCR
        text = self._ocr_title(title_crop)
        surface = classify_title(text)
        return text, surface, (0.9 if surface != StorageSurface.UNKNOWN else 0.0)

    def _match_template(
        self, title_crop: np.ndarray
    ) -> Tuple[StorageSurface, float]:
        """Try template matching against registered title sprites."""
        if not self._title_templates:
            return StorageSurface.UNKNOWN, 0.0

        gray = cv2.cvtColor(title_crop, cv2.COLOR_BGR2GRAY) if title_crop.ndim == 3 else title_crop
        best_conf = 0.0
        best_surface = StorageSurface.UNKNOWN

        for _label, (surface, tmpl) in self._title_templates.items():
            if tmpl.ndim == 3:
                tmpl_g = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY)
            else:
                tmpl_g = tmpl
            if tmpl_g.shape[0] > gray.shape[0] or tmpl_g.shape[1] > gray.shape[1]:
                # Scale template down to fit title crop
                scale = min(gray.shape[0] / tmpl_g.shape[0], gray.shape[1] / tmpl_g.shape[1])
                tmpl_g = cv2.resize(tmpl_g, (0, 0), fx=scale, fy=scale)
            if tmpl_g.shape[0] > gray.shape[0] or tmpl_g.shape[1] > gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, tmpl_g, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > best_conf:
                best_conf = max_val
                best_surface = surface

        threshold = 0.65
        if best_conf >= threshold:
            return best_surface, best_conf
        return StorageSurface.UNKNOWN, 0.0

    def _ocr_title(self, title_crop: np.ndarray) -> str:
        """Run EasyOCR on a title bar crop; returns lowercased stripped text."""
        reader = self._get_ocr_reader()
        if reader is None:
            return ""
        try:
            # Upscale for better OCR accuracy on small title bars
            h, w = title_crop.shape[:2]
            if h < 24:
                scale = 24 / h
                title_crop = cv2.resize(title_crop, (0, 0), fx=scale, fy=scale)

            results = reader.readtext(title_crop, detail=1)
            parts = []
            for _, text, conf in results:
                if conf >= self._cfg.ocr_min_confidence:
                    parts.append(text.strip())
            return " ".join(parts).lower().strip()
        except Exception as exc:
            self._log(f"[SD] OCR title failed: {exc!r}")
            return ""

    def _get_ocr_reader(self) -> Any:
        """Lazy-init EasyOCR reader (English, CPU-only)."""
        if self._ocr_reader is not None:
            return self._ocr_reader
        with self._ocr_lock:
            if self._ocr_reader is not None:
                return self._ocr_reader
            try:
                import easyocr  # type: ignore[import]
                self._ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
                self._log("[SD] EasyOCR reader initialised for title detection.")
            except Exception as exc:
                self._log(f"[SD] EasyOCR unavailable — title OCR disabled: {exc!r}")
                self._ocr_reader = None
        return self._ocr_reader

    # ── Surface priority ──────────────────────────────────────────────────────

    @staticmethod
    def _primary_surface(windows: List[ContainerWindow]) -> StorageSurface:
        """
        Select the most contextually relevant surface from the window list.

        Priority: MANAGE_CONTAINERS > STASH > STORE_INBOX > INBOX > DEPOT_CHEST
        > INVENTORY > UNKNOWN
        """
        priority = [
            StorageSurface.MANAGE_CONTAINERS,
            StorageSurface.STASH,
            StorageSurface.STORE_INBOX,
            StorageSurface.INBOX,
            StorageSurface.DEPOT_CHEST,
            StorageSurface.INVENTORY,
        ]
        surfaces = {w.surface for w in windows}
        for s in priority:
            if s in surfaces:
                return s
        return StorageSurface.UNKNOWN


# ---------------------------------------------------------------------------
# Module-level helper: classify a raw OCR'd title string
# ---------------------------------------------------------------------------

def classify_title(text: str) -> StorageSurface:
    """Map a lowercased title-bar string to a StorageSurface."""
    t = text.lower().strip()
    for pattern, surface in _TITLE_PATTERNS:
        if pattern.search(t):
            return surface
    return StorageSurface.UNKNOWN
