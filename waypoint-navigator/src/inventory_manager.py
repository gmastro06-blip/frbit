"""
Inventory & supply management — detect inventory full and supply levels.

Fase 7.2 — Inventory full handling (go to depot before continuing).
Fase 7.3 — Supply check before each hunting cycle.

Honesty rules (R6.2):
- Template matching with NMS when templates exist.
- Without templates → return UNKNOWN instead of inventing a number.
- Uncalibrated ROI → return UNKNOWN immediately.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

_log = logging.getLogger("wn.im")


# ── Helpers ──────────────────────────────────────────────────────────────────
def _nms_count(
    xs: np.ndarray, ys: np.ndarray, tw: int, th: int,
) -> int:
    """Count distinct template-match detections using greedy NMS.

    Parameters
    ----------
    xs, ys : arrays of x/y match coordinates (from ``np.where``).
    tw, th : template width/height — used as suppression radius.
    """
    if len(xs) == 0:
        return 0

    # Sort by y then x
    order = np.lexsort((xs, ys))
    xs = xs[order]
    ys = ys[order]

    keep: list[bool] = [True] * len(xs)
    for i in range(len(xs)):
        if not keep[i]:
            continue
        for j in range(i + 1, len(xs)):
            if not keep[j]:
                continue
            if abs(int(xs[i]) - int(xs[j])) < tw and abs(int(ys[i]) - int(ys[j])) < th:
                keep[j] = False
    return sum(keep)


# ── Inventory status ─────────────────────────────────────────────────────────
class InventoryStatus(Enum):
    """Current inventory state."""

    OK = "ok"
    NEARLY_FULL = "nearly_full"
    FULL = "full"
    UNKNOWN = "unknown"


class SupplyStatus(Enum):
    """Current supply level."""

    OK = "ok"
    LOW = "low"
    CRITICAL = "critical"
    EMPTY = "empty"
    UNKNOWN = "unknown"


# ── Configuration ────────────────────────────────────────────────────────────
@dataclass
class SupplyItem:
    """
    A supply to monitor.

    Parameters
    ----------
    name : str
        Human-readable name (e.g. "Health Potion", "Mana Potion").
    slot_roi : List[int]
        [x, y, w, h] of the inventory slot to monitor.
    template : Optional[np.ndarray]
        Template image of the supply item.
    min_count : int
        Minimum required before hunting.
    low_threshold : int
        Below this → SupplyStatus.LOW.
    critical_threshold : int
        Below this → SupplyStatus.CRITICAL.
    """

    name: str
    slot_roi: List[int] = field(default_factory=lambda: [0, 0, 32, 32])
    template: Optional[np.ndarray] = field(default=None, repr=False)
    min_count: int = 100
    low_threshold: int = 50
    critical_threshold: int = 10


@dataclass
class InventoryConfig:
    """
    Parameters
    ----------
    enabled : bool
        Master switch.
    inventory_roi : List[int]
        [x, y, w, h] of the full inventory panel.
    capacity_slots : int
        Total number of inventory slots.
    full_threshold : float
        Fraction of occupied slots to consider "full" (0.0–1.0).
    nearly_full_threshold : float
        Fraction to trigger "nearly_full" warning.
    supplies : List[SupplyItem]
        Supplies to monitor.
    check_interval_s : float
        Minimum seconds between inventory scans.
    depot_action : str
        What to do when inventory is full: "depot", "drop", "warn".
    """

    enabled: bool = True
    inventory_roi: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    capacity_slots: int = 20
    full_threshold: float = 0.95
    nearly_full_threshold: float = 0.80
    supplies: List[SupplyItem] = field(default_factory=list)
    check_interval_s: float = 10.0
    depot_action: str = "warn"

    def validate(self) -> None:
        """Raise ``ValueError`` on invalid config values."""
        if len(self.inventory_roi) != 4:
            raise ValueError(
                f"inventory_roi must have 4 elements, got {len(self.inventory_roi)}"
            )
        if any(v < 0 for v in self.inventory_roi):
            raise ValueError(f"inventory_roi contains negative values: {self.inventory_roi}")
        if self.capacity_slots <= 0:
            raise ValueError(f"capacity_slots must be > 0, got {self.capacity_slots}")
        if not 0 < self.full_threshold <= 1:
            raise ValueError(f"full_threshold must be in (0, 1], got {self.full_threshold}")
        if not 0 < self.nearly_full_threshold <= 1:
            raise ValueError(
                f"nearly_full_threshold must be in (0, 1], got {self.nearly_full_threshold}"
            )
        if self.nearly_full_threshold >= self.full_threshold:
            raise ValueError(
                f"nearly_full_threshold ({self.nearly_full_threshold}) "
                f"must be < full_threshold ({self.full_threshold})"
            )
        if self.check_interval_s <= 0:
            raise ValueError(f"check_interval_s must be > 0, got {self.check_interval_s}")
        if self.depot_action not in ("depot", "drop", "warn"):
            raise ValueError(
                f"depot_action must be 'depot', 'drop', or 'warn', got '{self.depot_action}'"
            )


# ── Detection results ────────────────────────────────────────────────────────
@dataclass
class InventoryReading:
    """Result of an inventory scan."""

    status: InventoryStatus = InventoryStatus.UNKNOWN
    occupied_slots: int = 0
    total_slots: int = 20
    fill_ratio: float = 0.0
    timestamp: float = 0.0

    @property
    def free_slots(self) -> int:
        return max(0, self.total_slots - self.occupied_slots)


@dataclass
class SupplyReading:
    """Result of a supply check for one item."""

    name: str = ""
    status: SupplyStatus = SupplyStatus.UNKNOWN
    estimated_count: int = 0
    confidence: float = 0.0


# ── Manager ──────────────────────────────────────────────────────────────────
class InventoryManager:
    """
    Monitor inventory capacity and supply levels.

    Usage::

        mgr = InventoryManager(config)
        inv = mgr.check_inventory(frame)
        if inv.status == InventoryStatus.FULL:
            # go to depot
        supplies = mgr.check_supplies(frame)
    """

    def __init__(
        self,
        config: Optional[InventoryConfig] = None,
        event_bus: Any = None,
    ) -> None:
        self._config = config or InventoryConfig()
        self._event_bus = event_bus
        self._last_inv: Optional[InventoryReading] = None
        self._last_supplies: Dict[str, SupplyReading] = {}
        self._last_check_ts: float = 0.0
        self._total_checks: int = 0
        self._full_count: int = 0

    # ── Inventory check ──────────────────────────────────────────────────
    def check_inventory(self, frame: np.ndarray) -> InventoryReading:
        """
        Scan inventory to determine fill level.

        Returns *UNKNOWN* when the inventory ROI is uncalibrated
        (``[0,0,0,0]``) — honest about not being able to detect.
        When a ROI is configured, uses a brightness/variance heuristic
        (clearly labelled as such).
        """
        self._total_checks += 1
        reading = InventoryReading(
            total_slots=self._config.capacity_slots,
            timestamp=time.monotonic(),
        )

        if not self._config.enabled or frame is None or frame.size == 0:
            self._last_inv = reading
            return reading

        roi = self._config.inventory_roi
        if len(roi) < 4 or roi[2] <= 0 or roi[3] <= 0:
            # Uncalibrated — we genuinely cannot detect inventory state.
            _log.warning(
                "Inventory ROI not calibrated (%s) — returning UNKNOWN", roi,
            )
            reading.status = InventoryStatus.UNKNOWN
            self._last_inv = reading
            self._last_check_ts = time.monotonic()
            return reading

        x, y, w, h = roi
        fh, fw = frame.shape[:2]
        x = min(x, fw - 1)
        y = min(y, fh - 1)
        w = min(w, fw - x)
        h = min(h, fh - y)
        if w <= 0 or h <= 0:
            reading.status = InventoryStatus.UNKNOWN
            self._last_inv = reading
            self._last_check_ts = time.monotonic()
            return reading
        crop = frame[y : y + h, x : x + w]

        occupied = self._estimate_occupied_slots(crop)
        reading.occupied_slots = occupied
        reading.fill_ratio = occupied / max(1, self._config.capacity_slots)

        if reading.fill_ratio >= self._config.full_threshold:
            reading.status = InventoryStatus.FULL
            self._full_count += 1
            self._emit("e19", {
                "fill_ratio": reading.fill_ratio,
                "free_slots": reading.free_slots,
                "action": self._config.depot_action,
            })
        elif reading.fill_ratio >= self._config.nearly_full_threshold:
            reading.status = InventoryStatus.NEARLY_FULL
        else:
            reading.status = InventoryStatus.OK

        self._last_inv = reading
        self._last_check_ts = time.monotonic()
        return reading

    def _estimate_occupied_slots(self, crop: np.ndarray) -> int:
        """Estimate occupied slots via brightness/variance heuristic.

        .. warning::
            This is a **rough heuristic**, not real slot detection.
            It divides the ROI into a grid and checks mean/std per cell.
            Accuracy depends on the ROI being tightly cropped to the
            actual inventory panel.  Results should be treated as
            approximate.
        """
        if crop.size == 0:
            return 0

        h, w = crop.shape[:2]
        total = self._config.capacity_slots
        if total <= 0:
            return 0

        _log.debug(
            "Using brightness/variance heuristic for %d slots (%dx%d crop)",
            total, w, h,
        )

        # Divide ROI into a grid of slots
        cols = min(total, max(1, int(np.sqrt(total * (w / max(1, h))))))
        rows = max(1, (total + cols - 1) // cols)
        cell_w = max(1, w // cols)
        cell_h = max(1, h // rows)

        occupied = 0
        for r in range(rows):
            for c in range(cols):
                if r * cols + c >= total:
                    break
                cy = r * cell_h
                cx = c * cell_w
                cell = crop[cy : cy + cell_h, cx : cx + cell_w]
                if cell.size == 0:
                    continue
                mean_val = float(np.mean(cell))
                std_val = float(np.std(cell))
                if mean_val > 30 and std_val > 15:
                    occupied += 1

        return occupied

    # ── Supply check ─────────────────────────────────────────────────────
    def check_supplies(self, frame: np.ndarray) -> List[SupplyReading]:
        """Check all configured supply items."""
        readings: List[SupplyReading] = []

        if not self._config.enabled or frame is None or frame.size == 0:
            return readings

        for item in self._config.supplies:
            reading = self._check_single_supply(frame, item)
            readings.append(reading)
            self._last_supplies[item.name] = reading

            if reading.status in (SupplyStatus.CRITICAL, SupplyStatus.EMPTY):
                self._emit("e20", {
                    "name": item.name,
                    "status": reading.status.value,
                    "estimated_count": reading.estimated_count,
                })

        return readings

    def _check_single_supply(
        self, frame: np.ndarray, item: SupplyItem,
    ) -> SupplyReading:
        """Check a single supply item.

        Uses template matching + NMS when a template is provided.
        Without a template the method honestly returns *UNKNOWN*
        instead of inventing a number.
        """
        reading = SupplyReading(name=item.name)

        roi = item.slot_roi
        if len(roi) >= 4 and roi[2] > 0 and roi[3] > 0:
            x, y, w, h = roi
            fh, fw = frame.shape[:2]
            x = min(x, fw - 1)
            y = min(y, fh - 1)
            w = min(w, fw - x)
            h = min(h, fh - y)
            crop = frame[y : y + h, x : x + w]
        else:
            return reading

        if crop.size == 0:
            return reading

        # ── With template → real detection (NMS) ────────────────────────
        if item.template is not None and item.template.size > 0:
            gray_c = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
            tmpl = item.template
            gray_t = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if tmpl.ndim == 3 else tmpl
            t_h, t_w = gray_t.shape[:2]
            g_h, g_w = gray_c.shape[:2]

            if t_w <= g_w and t_h <= g_h:
                result = cv2.matchTemplate(gray_c, gray_t, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                reading.confidence = float(max_val)

                # NMS to count distinct detections
                ys_arr, xs_arr = np.where(result >= 0.80)
                count = _nms_count(xs_arr, ys_arr, t_w, t_h)
                reading.estimated_count = count
            else:
                reading.confidence = 0.0
                reading.estimated_count = 0

            # Determine status from real count
            if reading.estimated_count <= 0:
                reading.status = SupplyStatus.EMPTY
            elif reading.estimated_count <= item.critical_threshold:
                reading.status = SupplyStatus.CRITICAL
            elif reading.estimated_count <= item.low_threshold:
                reading.status = SupplyStatus.LOW
            else:
                reading.status = SupplyStatus.OK

            return reading

        # ── No template → cannot count, be honest ───────────────────────
        _log.warning(
            "No template for supply '%s' — cannot estimate count", item.name,
        )
        reading.status = SupplyStatus.UNKNOWN
        reading.estimated_count = 0
        reading.confidence = 0.0
        return reading

    # ── Convenience ──────────────────────────────────────────────────────
    def needs_depot(self) -> bool:
        """True if inventory is full or any supply is critical."""
        if self._last_inv and self._last_inv.status == InventoryStatus.FULL:
            return True
        return any(
            r.status in (SupplyStatus.CRITICAL, SupplyStatus.EMPTY)
            for r in self._last_supplies.values()
        )

    def should_check(self) -> bool:
        """True if enough time has elapsed since last check."""
        return (time.monotonic() - self._last_check_ts) >= self._config.check_interval_s

    # ── Event bus ────────────────────────────────────────────────────────
    def _emit(self, event: str, data: Any) -> None:
        if self._event_bus is not None and hasattr(self._event_bus, "emit"):
            try:
                self._event_bus.emit(event, data)
            except Exception:
                pass

    # ── Properties ───────────────────────────────────────────────────────
    @property
    def config(self) -> InventoryConfig:
        return self._config

    @property
    def last_inventory(self) -> Optional[InventoryReading]:
        return self._last_inv

    @property
    def last_supplies(self) -> Dict[str, SupplyReading]:
        return dict(self._last_supplies)

    @property
    def total_checks(self) -> int:
        return self._total_checks

    @property
    def full_count(self) -> int:
        return self._full_count

    def stats_snapshot(self) -> Dict[str, Any]:
        return {
            "total_checks": self._total_checks,
            "full_count": self._full_count,
            "last_status": self._last_inv.status.value if self._last_inv else "unknown",
            "supplies": {
                k: {"status": v.status.value, "count": v.estimated_count}
                for k, v in self._last_supplies.items()
            },
        }
