"""
storage_state.py
----------------
Data model for Tibia's modern storage surfaces.

The modern Tibia client exposes distinct storage surfaces that the bot must
track explicitly:

  inventory       — equipment + backpack sidebar (always accessible)
  depot_chest     — classic depot container window (opened at NPC / chest tile)
  stash           — Stash tab (attached to depot chest in depot area)
  inbox           — Inbox tab (parcels, quest rewards)
  store_inbox     — Store Inbox (Store purchases; premium only)
  manage_containers — modal dialog for container management

This module is purely data — no vision, no I/O, no threading.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
class StorageSurface(Enum):
    """Identifiers for every distinct storage surface in the Tibia UI."""

    UNKNOWN = "unknown"
    INVENTORY = "inventory"
    DEPOT_CHEST = "depot_chest"
    STASH = "stash"
    INBOX = "inbox"
    STORE_INBOX = "store_inbox"
    MANAGE_CONTAINERS = "manage_containers"

    # Convenience helpers ─────────────────────────────────────────────────────
    @property
    def is_depot_family(self) -> bool:
        """True for surfaces that live inside the depot NPC area."""
        return self in (
            StorageSurface.DEPOT_CHEST,
            StorageSurface.STASH,
            StorageSurface.INBOX,
            StorageSurface.STORE_INBOX,
            StorageSurface.MANAGE_CONTAINERS,
        )

    @property
    def supports_stow(self) -> bool:
        """True for surfaces where 'Stow All Items' is meaningful."""
        return self in (StorageSurface.DEPOT_CHEST, StorageSurface.STASH)

    @property
    def is_read_only(self) -> bool:
        """Some surfaces (Inbox, Store Inbox) cannot be deposited into."""
        return self in (StorageSurface.INBOX, StorageSurface.STORE_INBOX)


# ---------------------------------------------------------------------------
@dataclass
class ContainerWindow:
    """One open container / panel detected in the current frame."""

    # Bounding box in frame coordinates [x, y, w, h]
    roi: Tuple[int, int, int, int]
    # OCR'd title bar text (lowercased, stripped)
    title: str
    # Classified surface based on title
    surface: StorageSurface
    # Pixel position of the first slot (for interact targeting)
    first_slot: Optional[Tuple[int, int]] = None
    # Detection confidence — 1.0 for exact title match, <1.0 for fuzzy
    confidence: float = 1.0

    # ── Convenience ──────────────────────────────────────────────────────────
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

    @property
    def center(self) -> Tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    @property
    def title_bar_roi(self) -> Tuple[int, int, int, int]:
        """Approximate ROI of the title bar (top ~18 px of the container)."""
        return self.x, self.y, self.w, 18


# ---------------------------------------------------------------------------
@dataclass
class StorageState:
    """Snapshot of all detected storage windows at a given moment."""

    # Primary surface — the most contextually relevant open surface,
    # or UNKNOWN if nothing useful is detected.
    surface: StorageSurface
    # All container windows found in the frame (may be empty)
    open_windows: List[ContainerWindow] = field(default_factory=list)
    # Monotonic time of detection
    detected_at: float = field(default_factory=time.monotonic)
    # Frame dimensions at detection time (w, h)
    frame_size: Tuple[int, int] = (0, 0)

    # ── Lookups ──────────────────────────────────────────────────────────────
    def find(self, surface: StorageSurface) -> Optional[ContainerWindow]:
        """Return the first window matching *surface*, or None."""
        for w in self.open_windows:
            if w.surface == surface:
                return w
        return None

    def has(self, surface: StorageSurface) -> bool:
        return self.find(surface) is not None

    @property
    def is_depot_open(self) -> bool:
        """Any depot-family window is currently visible."""
        return any(w.surface.is_depot_family for w in self.open_windows)

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.detected_at

    def is_stale(self, max_age_s: float = 0.5) -> bool:
        return self.age_s > max_age_s


# ---------------------------------------------------------------------------
@dataclass
class StorageTransition:
    """Records a single surface transition for debugging and telemetry."""

    from_surface: StorageSurface
    to_surface: StorageSurface
    success: bool
    elapsed_s: float
    note: str = ""
