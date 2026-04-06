"""
WalkabilityOverlay
------------------
Real-time OpenCV HUD that visualises walkability, position, blocked tiles,
and route progress — similar to a diagnostic panel used during live walk
tests.

The overlay renders into a small window (resizable) showing:
  - Minimap crop with walkability colour-coding
  - Player position + waypoint target
  - Blocked / discrepancy tiles highlighted
  - Step directions (e.g. "dsssd")
  - Status text (blocked, walking, idle, etc.)
  - Coordinate info bar at the bottom

Designed to run in a background thread alongside the bot.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .models import Coordinate, BOUNDS

logger = logging.getLogger("wn")

# ── Layout constants ─────────────────────────────────────────────────────────
_OVERLAY_W = 400
_OVERLAY_H = 400
_HEADER_H = 120      # space for text above the map area
_FOOTER_H = 24       # bottom info bar
_MAP_H = _OVERLAY_H - _HEADER_H - _FOOTER_H

_BG_COLOR = (0, 0, 0)
_WALKABLE_COLOR = (180, 180, 180)     # grey
_NON_WALKABLE_COLOR = (40, 40, 40)    # dark
_BLOCKED_COLOR = (0, 0, 200)          # red (BGR)
_DISCREPANCY_COLOR = (0, 180, 255)    # orange
_PLAYER_COLOR = (0, 255, 0)           # green
_WAYPOINT_COLOR = (255, 255, 0)       # cyan (BGR)
_ROUTE_COLOR = (200, 200, 0)          # teal
_STEP_COLOR = (255, 200, 100)         # light blue

_DIR_MAP = {(0, -1): "n", (0, 1): "s", (-1, 0): "w", (1, 0): "e",
            (1, -1): "ne", (-1, -1): "nw", (1, 1): "se", (-1, 1): "sw",
            (0, 0): "."}


@dataclass
class OverlayState:
    """Mutable state fed to the overlay from the walk engine."""
    position: Optional[Coordinate] = None
    waypoint: Optional[Coordinate] = None
    initial_pos: Optional[Coordinate] = None
    step_target: Optional[Coordinate] = None
    status: str = "idle"
    directions: str = ""
    pnf_count: int = 0
    replan_count: int = 0
    blocked_tiles: List[Tuple[int, int, int]] = field(default_factory=list)
    route_tiles: List[Tuple[int, int]] = field(default_factory=list)
    floor: int = 7


class WalkabilityOverlay:
    """OpenCV-based diagnostic HUD for walkability and navigation state.

    Parameters
    ----------
    loader : TibiaMapLoader
        Source of static walkability data.
    view_radius : int
        Number of tiles to show around the player (half-width).
    window_name : str
        OpenCV window title.
    """

    def __init__(
        self,
        loader: Any,
        view_radius: int = 30,
        window_name: str = "diag",
    ) -> None:
        self._loader = loader
        self._radius = view_radius
        self._win = window_name
        self._state = OverlayState()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._fps_target = 10
        self._last_render: Optional[np.ndarray] = None

    # ──────────────────────────────────────────────────────────────────────
    # Public state update API (thread-safe)
    # ──────────────────────────────────────────────────────────────────────

    def update(self, **kwargs: Any) -> None:
        """Update any OverlayState fields.  Safe to call from any thread."""
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._state, k):
                    setattr(self._state, k, v)

    def add_direction(self, dx: int, dy: int) -> None:
        """Append a step direction letter to the directions string."""
        d = _DIR_MAP.get((dx, dy), "?")
        with self._lock:
            self._state.directions += d
            # Keep only last 40 chars
            if len(self._state.directions) > 40:
                self._state.directions = self._state.directions[-40:]

    _MAX_BLOCKED_TILES = 10_000

    def add_blocked(self, x: int, y: int, z: int) -> None:
        with self._lock:
            entry = (x, y, z)
            if entry not in self._state.blocked_tiles:
                self._state.blocked_tiles.append(entry)
                if len(self._state.blocked_tiles) > self._MAX_BLOCKED_TILES:
                    # Drop oldest half to amortise the cost of trimming
                    self._state.blocked_tiles = self._state.blocked_tiles[self._MAX_BLOCKED_TILES // 2:]

    def set_route(self, steps: List[Coordinate]) -> None:
        """Set the current A* route to display."""
        with self._lock:
            self._state.route_tiles = [(s.x, s.y) for s in steps]

    # ──────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the overlay rendering loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"t-{os.urandom(3).hex()}")
        self._thread.start()

    def stop(self) -> None:
        """Stop the overlay and destroy the OpenCV window."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        try:
            cv2.destroyWindow(self._win)
        except Exception:
            pass

    @property
    def running(self) -> bool:
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # Render (single frame)
    # ──────────────────────────────────────────────────────────────────────

    def render(self) -> np.ndarray:
        """Render the current state into a BGR image and return it."""
        with self._lock:
            pos = self._state.position
            wp = self._state.waypoint
            init = self._state.initial_pos
            step = self._state.step_target
            status = self._state.status
            dirs = self._state.directions
            pnf = self._state.pnf_count
            replan = self._state.replan_count
            blocked = list(self._state.blocked_tiles)
            route = list(self._state.route_tiles)
            floor = self._state.floor

        canvas = np.zeros((_OVERLAY_H, _OVERLAY_W, 3), dtype=np.uint8)

        # ── Header text ──────────────────────────────────────────────────
        y_text = 18
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.5
        thick = 1
        white = (255, 255, 255)
        yellow = (0, 255, 255)

        # Directions
        if dirs:
            cv2.putText(canvas, dirs[-30:], (8, y_text), font, scale, yellow, thick)
            y_text += 22

        # Status
        status_color = (0, 0, 255) if "block" in status.lower() else (0, 255, 0)
        cv2.putText(canvas, status, (8, y_text), font, scale, status_color, thick)
        y_text += 22

        # Coordinates (abbreviated: last 2 digits of x:y)
        coord_parts = []
        if pos:
            coord_parts.append(f"{pos.x % 100:02d}:{pos.y % 100:02d}")
        if wp:
            coord_parts.append(f"{wp.x % 100:02d}:{wp.y % 100:02d}")
        if step:
            coord_parts.append(f"{step.x % 100:02d}:{step.y % 100:02d}")
        if coord_parts:
            cv2.putText(canvas, "  ".join(coord_parts), (8, y_text), font, scale, white, thick)
            y_text += 22

        # pnf / replan
        info_line = f"pnf={pnf}"
        if replan > 0:
            info_line += f"  replan={replan}"
        if blocked:
            info_line += f"  blk={len(blocked)}"
        cv2.putText(canvas, info_line, (8, y_text), font, scale, white, thick)

        # ── Map area ─────────────────────────────────────────────────────
        map_w = _OVERLAY_W
        map_h = _MAP_H
        map_origin_y = _HEADER_H

        if pos is not None:
            self._render_map(canvas, map_origin_y, map_w, map_h,
                             pos, floor, blocked, route, wp, step)
        else:
            cv2.putText(canvas, "no position", (map_w // 2 - 50, map_origin_y + map_h // 2),
                        font, 0.6, (100, 100, 100), 1)

        # ── Footer bar ───────────────────────────────────────────────────
        footer_y = _OVERLAY_H - _FOOTER_H
        cv2.rectangle(canvas, (0, footer_y), (_OVERLAY_W, _OVERLAY_H), (30, 30, 30), -1)

        parts: list[str] = []
        if pos:
            parts.append(f"pos:{pos.x % 100}.{pos.y % 100}")
        if wp:
            parts.append(f"way:{wp.x % 100}.{wp.y % 100}")
        if init:
            parts.append(f"init:{init.x % 100}.{init.y % 100}")
        if blocked:
            bx, by, _ = blocked[-1]
            parts.append(f"block:{bx % 100}.{by % 100}")
        if step:
            parts.append(f"step:{step.x % 100}.{step.y % 100}")

        footer_text = "".join(parts) if parts else "waiting..."
        cv2.putText(canvas, footer_text, (4, _OVERLAY_H - 6),
                    font, 0.35, (200, 200, 200), 1)

        self._last_render = canvas
        return canvas

    def _render_map(
        self,
        canvas: np.ndarray,
        y0: int,
        w: int,
        h: int,
        pos: Coordinate,
        floor: int,
        blocked: list[Tuple[int, int, int]],
        route: list[Tuple[int, int]],
        wp: Optional[Coordinate],
        step: Optional[Coordinate],
    ) -> None:
        """Draw the walkability map region centred on *pos*."""
        r = self._radius
        view_size = 2 * r + 1  # tiles across

        # Pixel scale: how many canvas pixels per tile
        tile_px = min(w // view_size, h // view_size)
        if tile_px < 1:
            tile_px = 1

        try:
            walk_arr = self._loader.get_walkability(floor)
        except Exception:
            logger.debug("Failed to load walkability for floor %d", floor, exc_info=True)
            return

        arr_h, arr_w = walk_arr.shape
        x_min = BOUNDS["xMin"]
        y_min = BOUNDS["yMin"]

        blocked_set = {(bx, by) for bx, by, bz in blocked if bz == floor}
        route_set = set(route)

        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                tile_x = pos.x + dx
                tile_y = pos.y + dy

                px = tile_x - x_min
                py = tile_y - y_min

                # Determine colour
                if (tile_x, tile_y) in blocked_set:
                    color = _BLOCKED_COLOR
                elif (tile_x, tile_y) in route_set:
                    color = _ROUTE_COLOR
                elif 0 <= px < arr_w and 0 <= py < arr_h and walk_arr[py, px]:
                    color = _WALKABLE_COLOR
                else:
                    color = _NON_WALKABLE_COLOR

                # Canvas pixel coordinates
                cx = (dx + r) * tile_px
                cy = y0 + (dy + r) * tile_px
                cv2.rectangle(canvas, (cx, cy), (cx + tile_px - 1, cy + tile_px - 1),
                              color, -1)

        # Draw player dot (centre)
        center_cx = r * tile_px + tile_px // 2
        center_cy = y0 + r * tile_px + tile_px // 2
        cv2.circle(canvas, (center_cx, center_cy), max(2, tile_px // 2), _PLAYER_COLOR, -1)

        # Draw waypoint marker
        if wp is not None:
            wdx = wp.x - pos.x
            wdy = wp.y - pos.y
            if abs(wdx) <= r and abs(wdy) <= r:
                wcx = (wdx + r) * tile_px + tile_px // 2
                wcy = y0 + (wdy + r) * tile_px + tile_px // 2
                cv2.drawMarker(canvas, (wcx, wcy), _WAYPOINT_COLOR,
                               cv2.MARKER_CROSS, tile_px, 1)

        # Draw step target
        if step is not None:
            sdx = step.x - pos.x
            sdy = step.y - pos.y
            if abs(sdx) <= r and abs(sdy) <= r:
                scx = (sdx + r) * tile_px + tile_px // 2
                scy = y0 + (sdy + r) * tile_px + tile_px // 2
                cv2.drawMarker(canvas, (scx, scy), _STEP_COLOR,
                               cv2.MARKER_DIAMOND, tile_px, 1)

    # ──────────────────────────────────────────────────────────────────────
    # Background loop
    # ──────────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Render loop running in a daemon thread."""
        cv2.namedWindow(self._win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self._win, _OVERLAY_W, _OVERLAY_H)
        interval = 1.0 / self._fps_target

        while self._running:
            t0 = time.monotonic()
            frame = self.render()
            cv2.imshow(self._win, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC to close
                self._running = False
                break
            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        try:
            cv2.destroyWindow(self._win)
        except Exception:
            pass
