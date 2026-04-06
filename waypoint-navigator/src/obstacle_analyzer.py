"""
ObstacleAnalyzer
----------------
Real-time analysis of minimap tiles to detect **runtime obstacles** that
differ from the static walkability data (tibiamaps).

Tibia minimap palette — DEFINITIVE (extracted from all 16 floor PNGs of
https://github.com/tibiamaps/tibia-map-data cross-referenced with the
pathfinding data).  There are exactly 14 unique colours across all floors.

WALKABLE (>80 % walkable in path data):
  BGR (153,153,153) RGB (153,153,153) grey stone floor         93.4 %  1.4 M px
  BGR ( 51,102,153) RGB (153,102, 51) dirt / sand / brown      91.3 %  1.2 M px
  BGR (  0,204,  0) RGB (  0,204,  0) grass / vegetation       97.6 %  946 K px
  BGR (153,204,255) RGB (255,204,153) sand / beach / light tan  97.8 %  321 K px
  BGR (255,255,255) RGB (255,255,255) snow / ice                97.2 %  139 K px

NON-WALKABLE (<20 % walkable in path data):
  BGR (102,102,102) RGB (102,102,102) dark grey (cave wall)      2.3 %  3.8 M px
  BGR (153,102, 51) RGB ( 51,102,153) water / blue               1.2 %  3.6 M px
  BGR (  0, 51,153) RGB (153, 51,  0) dark red-brown (lava/rock) 2.1 %  1.6 M px
  BGR (  0, 51,255) RGB (255, 51,  0) building / wall (red)      4.2 %  860 K px
  BGR (  0,102,  0) RGB (  0,102,  0) mountain / dark green      6.7 %  244 K px
  BGR (  0,102,255) RGB (255,102,  0) orange obstacle            7.2 %  119 K px
  BGR (102,255,153) RGB (153,255,102) light green (farmland)     9.4 %   49 K px
  BGR (  0,  0,  0) RGB (  0,  0,  0) unexplored / black        N/A

AMBIGUOUS (context-dependent, walkable on some tiles, non-walkable on others):
  BGR (255,255,204) RGB (204,255,255) light cyan / ice / shoal  34.7 %  253 K px
  BGR (  0,255,255) RGB (255,255,  0) yellow / stairs / special 65.0 %   74 K px

The analyser crops the minimap from a game frame, maps each pixel to a
Tibia tile, classifies it as walkable/non-walkable by colour, and
compares with the static walkability array to find **discrepancies**
(e.g. a tile marked walkable in tibiamaps but showing a wall colour
in-game, or vice-versa).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .models import Coordinate, BOUNDS

logger = logging.getLogger("wn")

# ── Tibia minimap palette — DEFINITIVE ───────────────────────────────────────
# Extracted from 14.6 M pixels across all 16 floor PNGs of tibia-map-data,
# cross-referenced with pathfinding walkability.  Exactly 14 unique colours.
#
# Classification rule: >80 % walkable in path data → WALKABLE
#                      <20 % → NON-WALKABLE
#                      else → assigned based on dominant use case
_PALETTE: list[tuple[tuple[int, int, int], bool]] = [
    # ── Walkable (5 colours) ─────────────────────────────────────────
    ((153, 153, 153), True),    # grey stone floor           93.4 % walk
    ((51,  102, 153), True),    # dirt / sand / brown        91.3 % walk
    ((0,   204, 0),   True),    # grass / vegetation         97.6 % walk
    ((153, 204, 255), True),    # sand / beach / light tan   97.8 % walk
    ((255, 255, 255), True),    # snow / ice                 97.2 % walk
    # ── Non-walkable (9 colours) ─────────────────────────────────────
    ((102, 102, 102), False),   # dark grey — cave wall       2.3 % walk
    ((153, 102, 51),  False),   # water (blue in RGB)         1.2 % walk
    ((0,   51,  153), False),   # dark red-brown (lava/rock)  2.1 % walk
    ((0,   51,  255), False),   # building / wall (red)       4.2 % walk
    ((0,   102, 0),   False),   # mountain / dark green       6.7 % walk
    ((0,   102, 255), False),   # orange obstacle             7.2 % walk
    ((102, 255, 153), False),   # light green (farmland)      9.4 % walk
    ((0,   0,   0),   False),   # unexplored / black
    # ── Ambiguous — default to non-walkable for safety ───────────────
    ((255, 255, 204), False),   # light cyan / ice / shoal   34.7 % walk
    ((0,   255, 255), False),   # yellow / stairs / special  65.0 % walk
]

# Build lookup arrays for vectorised matching
_PAL_COLORS = np.array([c for c, _ in _PALETTE], dtype=np.int16)  # (N, 3)
_PAL_WALKABLE = np.array([w for _, w in _PALETTE], dtype=bool)      # (N,)
_COLOR_TOLERANCE = 30  # Chebyshev distance for palette matching


@dataclass
class TileInfo:
    """Analysis result for a single tile."""
    x: int
    y: int
    z: int
    # From live minimap colour
    live_walkable: bool
    # From static map data
    static_walkable: bool
    # True when live and static disagree
    discrepancy: bool
    # Dominant BGR colour of this tile in the minimap crop
    color_bgr: Tuple[int, int, int] = (0, 0, 0)


@dataclass
class AnalysisResult:
    """Result of a full minimap analysis frame."""
    center: Optional[Coordinate]
    tiles: List[TileInfo] = field(default_factory=list)
    discrepancies: List[TileInfo] = field(default_factory=list)
    # Blocked-in-game tiles (static says walkable, live says not)
    blocked_tiles: List[TileInfo] = field(default_factory=list)
    # Unexpectedly open tiles (static says not walkable, live says walkable)
    open_tiles: List[TileInfo] = field(default_factory=list)
    tile_count: int = 0
    discrepancy_count: int = 0


class ObstacleAnalyzer:
    """Analyses the minimap crop to detect runtime obstacles.

    Parameters
    ----------
    loader : TibiaMapLoader
        Provides static walkability data.
    tiles_wide : int
        Number of tiles visible across the minimap width (default 90 at
        normal camera zoom).
    roi : list[int]
        ``[x, y, w, h]`` of the minimap widget in a 1920×1080 reference
        frame (same format as ``MinimapConfig.roi``).
    """

    _REF_W = 1920
    _REF_H = 1080

    def __init__(
        self,
        loader: Any,
        tiles_wide: int = 90,
        roi: Optional[list[int]] = None,
    ) -> None:
        self._loader = loader
        self._tiles_wide = tiles_wide
        self._roi = roi or [1710, 37, 175, 175]
        # Runtime blocked tiles confirmed by repeated analysis
        self._confirmed_blocked: Dict[Tuple[int, int, int], int] = {}

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def analyze(
        self,
        frame: np.ndarray,
        center: Optional[Coordinate] = None,
        floor: int = 7,
    ) -> AnalysisResult:
        """Analyse the minimap region of *frame* and return tile classification.

        Parameters
        ----------
        frame : np.ndarray
            Full game frame (BGR, any resolution).
        center : Coordinate, optional
            Current player position. If given, tiles are labelled with
            absolute game coordinates.
        floor : int
            Current floor (used for walkability lookup).
        """
        crop = self._crop_minimap(frame)
        if crop is None:
            return AnalysisResult(center=center)

        mh, mw = crop.shape[:2]
        tiles_w = self._tiles_wide
        tiles_h = max(1, int(mh * tiles_w / mw))

        # Downsample to 1 px per tile
        tile_img = self._resize_to_tiles(crop, tiles_w, tiles_h)

        # Classify each tile pixel by palette
        live_walkable = self._classify_walkable(tile_img)

        # Get static walkability for the visible area
        result = AnalysisResult(center=center, tile_count=tiles_w * tiles_h)

        if center is None:
            return result

        # Map tile pixels to absolute coordinates
        half_w = tiles_w // 2
        half_h = tiles_h // 2

        try:
            static_arr = self._loader.get_walkability(floor)
        except Exception:
            return result

        arr_h, arr_w = static_arr.shape
        x_min = BOUNDS["xMin"]
        y_min = BOUNDS["yMin"]

        for ty in range(tiles_h):
            for tx in range(tiles_w):
                tile_x = center.x - half_w + tx
                tile_y = center.y - half_h + ty
                px = tile_x - x_min
                py = tile_y - y_min

                lw = bool(live_walkable[ty, tx])

                # Static walkability (bounds-safe)
                if 0 <= px < arr_w and 0 <= py < arr_h:
                    sw = bool(static_arr[py, px])
                else:
                    sw = False

                bgr = tuple(int(v) for v in tile_img[ty, tx])
                ti = TileInfo(
                    x=tile_x, y=tile_y, z=floor,
                    live_walkable=lw,
                    static_walkable=sw,
                    discrepancy=(lw != sw),
                    color_bgr=(bgr[0], bgr[1], bgr[2]),
                )
                result.tiles.append(ti)
                if ti.discrepancy:
                    result.discrepancies.append(ti)
                    if sw and not lw:
                        result.blocked_tiles.append(ti)
                    elif lw and not sw:
                        result.open_tiles.append(ti)

        result.discrepancy_count = len(result.discrepancies)
        return result

    def get_blocked_coords(
        self,
        frame: np.ndarray,
        center: Coordinate,
        floor: int = 7,
    ) -> list[Coordinate]:
        """Return a list of tiles that static data says walkable but the live
        minimap shows as non-walkable (runtime obstacles).

        This is the main method used by the walk engine to learn about
        in-game obstacles that are missing from the static map data.
        """
        result = self.analyze(frame, center, floor)
        blocked = [
            Coordinate(t.x, t.y, t.z)
            for t in result.blocked_tiles
        ]
        # Sanity cap: >100 blocked per frame is almost certainly a detection
        # error (palette mismatch, wrong ROI, etc.).  Return empty to avoid
        # poisoning the pathfinder.
        if len(blocked) > 100:
            logger.warning(
                "ObstacleAnalyzer: %d blocked tiles in one frame — "
                "suppressed (likely false positives)",
                len(blocked),
            )
            return []
        return blocked

    def confirm_blocked(
        self,
        coord: Coordinate,
        threshold: int = 2,
    ) -> bool:
        """Register a blocked observation and return True after *threshold*
        consecutive sightings of the same tile as blocked.  Prevents false
        positives from transient creatures / players.
        """
        key = (coord.x, coord.y, coord.z)
        self._confirmed_blocked[key] = self._confirmed_blocked.get(key, 0) + 1
        # Cap dict size to prevent unbounded growth
        if len(self._confirmed_blocked) > 2000:
            oldest = list(self._confirmed_blocked.keys())[:1000]
            for k in oldest:
                del self._confirmed_blocked[k]
        return self._confirmed_blocked[key] >= threshold

    def clear_confirmed(self, coord: Optional[Coordinate] = None) -> None:
        """Clear confirmed-blocked state.  If *coord* is ``None``, clear all."""
        if coord is None:
            self._confirmed_blocked.clear()
        else:
            self._confirmed_blocked.pop((coord.x, coord.y, coord.z), None)

    @property
    def confirmed_blocked_tiles(self) -> list[Tuple[int, int, int]]:
        """Return all confirmed blocked tile keys ``(x, y, z)``."""
        return list(self._confirmed_blocked.keys())

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    def _crop_minimap(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Crop minimap ROI from the full game frame."""
        fh, fw = frame.shape[:2]
        rx, ry, rw, rh = self._roi

        sx = fw / self._REF_W
        sy = fh / self._REF_H
        x0 = max(0, int(rx * sx))
        y0 = max(0, int(ry * sy))
        x1 = min(fw, int((rx + rw) * sx))
        y1 = min(fh, int((ry + rh) * sy))

        if x1 - x0 < 20 or y1 - y0 < 20:
            return None
        return frame[y0:y1, x0:x1].copy()

    @staticmethod
    def _resize_to_tiles(crop: np.ndarray, tw: int, th: int) -> np.ndarray:
        """Downsample a BGR crop to exactly (th, tw) pixels — one per tile."""
        import cv2
        return cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _classify_walkable(tile_img: np.ndarray) -> np.ndarray:
        """Classify each tile pixel as walkable (True) or non-walkable.

        Uses nearest-palette matching against ``_PAL_COLORS`` with
        Chebyshev tolerance.
        """
        h, w = tile_img.shape[:2]
        flat = tile_img.reshape(-1, 3).astype(np.int16)  # (N, 3)

        # Distance to each palette colour: (N, P)
        diffs = np.abs(flat[:, np.newaxis, :] - _PAL_COLORS[np.newaxis, :, :])
        # Chebyshev: max channel distance per palette entry
        cheby = diffs.max(axis=2)  # (N, P)

        # Best matching palette index per pixel
        best_idx = cheby.argmin(axis=1)  # (N,)
        best_dist = cheby[np.arange(len(flat)), best_idx]

        # Classify: if within tolerance use palette, else default non-walkable
        walkable = np.where(
            best_dist <= _COLOR_TOLERANCE,
            _PAL_WALKABLE[best_idx],
            False,
        )
        return walkable.reshape(h, w)
