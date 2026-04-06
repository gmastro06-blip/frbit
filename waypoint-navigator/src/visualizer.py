"""
MapVisualizer
-------------
Renders Tibia map floors with optional overlays:
  - A* routes (coloured polyline)
  - Waypoints / markers (dots + labels)
  - Start / end markers

Uses matplotlib for rendering to screen or file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np

from .map_loader import TibiaMapLoader
from .models import Coordinate, Route, Waypoint, BOUNDS

# Default colours (RGB 0-1 floats)
ROUTE_COLOR = (1.0, 0.2, 0.2)       # red
ROUTE_WIDTH = 1.5
START_COLOR = (0.1, 0.9, 0.1)       # green
END_COLOR = (0.9, 0.1, 0.1)         # red
WAYPOINT_COLOR = (1.0, 0.85, 0.0)   # yellow

TILE_ZOOM = 4          # how many screen pixels per map tile in cropped views
CROP_PADDING = 50      # tiles of padding around a route bounding box


class MapVisualizer:
    """
    Visualize Tibia maps with routes and waypoints.

    Parameters
    ----------
    loader : TibiaMapLoader
        Provides map images and walkability data.
    """

    def __init__(self, loader: TibiaMapLoader) -> None:
        self.loader = loader

    # -----------------------------------------------------------------------
    # Full-floor view
    # -----------------------------------------------------------------------

    def show_floor(
        self,
        floor: int,
        waypoints: Optional[List[Waypoint]] = None,
        routes: Optional[List[Route]] = None,
        title: str = "",
        save_path: Optional[Path] = None,
    ) -> None:
        """Display (or save) the entire floor PNG with optional overlays."""
        import matplotlib.pyplot as plt

        img = self.loader.get_map_image(floor)
        fig, ax = plt.subplots(figsize=(14, 11))
        ax.imshow(img, origin="upper", interpolation="nearest")

        if routes:
            for route in routes:
                self._draw_route(ax, route)

        if waypoints:
            for wp in waypoints:
                if wp.coord.z == floor:
                    self._draw_waypoint(ax, wp)

        ax.set_title(title or f"Tibia – Floor {floor:02d}")
        ax.set_xlabel("X  (pixel = 1 tile)")
        ax.set_ylabel("Y  (pixel = 1 tile)")
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            print(f"Saved → {save_path}")
        else:
            plt.show()
        plt.close(fig)

    # -----------------------------------------------------------------------
    # Cropped route view
    # -----------------------------------------------------------------------

    def show_route(
        self,
        route: Route,
        waypoints: Optional[List[Waypoint]] = None,
        title: str = "",
        padding: int = CROP_PADDING,
        save_path: Optional[Path] = None,
        show_walkability: bool = False,
    ) -> None:
        """Display a zoomed-in view around a route."""
        import matplotlib.pyplot as plt

        if not route.found or not route.steps:
            print("Route not found – nothing to display.")
            return

        floor = route.start.z
        img = self.loader.get_map_image(floor) if not show_walkability else None
        walkability = self.loader.get_walkability(floor) if show_walkability else None

        # Bounding box in pixel space
        xs = [c.to_pixel()[0] for c in route.steps]
        ys = [c.to_pixel()[1] for c in route.steps]
        px0 = max(min(xs) - padding, 0)
        py0 = max(min(ys) - padding, 0)
        px1 = min(max(xs) + padding, BOUNDS["xMax"] - BOUNDS["xMin"])
        py1 = min(max(ys) + padding, BOUNDS["yMax"] - BOUNDS["yMin"])

        fig, ax = plt.subplots(figsize=(10, 8))

        if show_walkability and walkability is not None:
            cropped = walkability[py0:py1, px0:px1].astype(np.uint8) * 255
            ax.imshow(
                np.stack([cropped, np.zeros_like(cropped), np.zeros_like(cropped)], axis=-1),
                origin="upper", extent=(px0, px1, py1, py0),
            )
        elif img is not None:
            cropped = img[py0:py1, px0:px1]
            ax.imshow(cropped, origin="upper", extent=(px0, px1, py1, py0))

        # Draw route
        self._draw_route(ax, route)

        # Draw waypoints in view
        if waypoints:
            for wp in waypoints:
                if wp.coord.z == floor:
                    px, py = wp.coord.to_pixel()
                    if px0 <= px <= px1 and py0 <= py <= py1:
                        self._draw_waypoint(ax, wp)

        ax.set_xlim(px0, px1)
        ax.set_ylim(py1, py0)
        ax.set_title(title or f"Route: {route.start} → {route.end}")
        ax.set_xlabel(f"Game X  (origin={BOUNDS['xMin']})")
        ax.set_ylabel(f"Game Y  (origin={BOUNDS['yMin']})")
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            print(f"Saved → {save_path}")
        else:
            plt.show()
        plt.close(fig)

    # -----------------------------------------------------------------------
    # Multi-segment route view
    # -----------------------------------------------------------------------

    def show_multi_route(
        self,
        segments: List[Route],
        waypoints: Optional[List[Waypoint]] = None,
        title: str = "Multi-stop Route",
        save_path: Optional[Path] = None,
    ) -> None:
        """Display all route segments together on a single map."""
        import matplotlib.pyplot as plt
        import matplotlib

        floors = {s.start.z for s in segments}
        if len(floors) > 1:
            print("Warning: multi-floor routes – showing only the first floor.")
        floor = list(floors)[0]

        img = self.loader.get_map_image(floor)
        all_steps = [c for seg in segments for c in seg.steps]
        if not all_steps:
            print("No steps to display.")
            return

        xs = [c.to_pixel()[0] for c in all_steps]
        ys = [c.to_pixel()[1] for c in all_steps]
        px0 = max(min(xs) - CROP_PADDING, 0)
        py0 = max(min(ys) - CROP_PADDING, 0)
        px1 = min(max(xs) + CROP_PADDING, BOUNDS["xMax"] - BOUNDS["xMin"])
        py1 = min(max(ys) + CROP_PADDING, BOUNDS["yMax"] - BOUNDS["yMin"])

        fig, ax = plt.subplots(figsize=(12, 10))
        cropped = img[py0:py1, px0:px1]
        ax.imshow(cropped, origin="upper", extent=(px0, px1, py1, py0))

        cmap = matplotlib.colormaps.get_cmap("rainbow").resampled(max(len(segments), 1))
        for i, seg in enumerate(segments):
            if seg.found and seg.steps:
                color = cmap(i)[:3]
                self._draw_route(ax, seg, color=color, label=f"Seg {i + 1}")

        if waypoints:
            for wp in waypoints:
                if wp.coord.z == floor:
                    px, py = wp.coord.to_pixel()
                    if px0 <= px <= px1 and py0 <= py <= py1:
                        self._draw_waypoint(ax, wp)

        ax.set_xlim(px0, px1)
        ax.set_ylim(py1, py0)
        ax.set_title(title)
        ax.legend(loc="upper right", fontsize=7)
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            print(f"Saved → {save_path}")
        else:
            plt.show()
        plt.close(fig)

    # -----------------------------------------------------------------------
    # Utility helpers
    # -----------------------------------------------------------------------

    def route_bounding_box(
        self,
        route: Route,
        padding: int = CROP_PADDING,
    ) -> Tuple[int, int, int, int]:
        """Return ``(px0, py0, px1, py1)`` pixel bounds for *route* + padding.

        Clamps to the valid map pixel range.  Raises ``ValueError`` if the
        route has no steps.
        """
        if not route.steps:
            raise ValueError("route has no steps")
        xs = [c.to_pixel()[0] for c in route.steps]
        ys = [c.to_pixel()[1] for c in route.steps]
        x_max = BOUNDS["xMax"] - BOUNDS["xMin"]
        y_max = BOUNDS["yMax"] - BOUNDS["yMin"]
        px0 = max(min(xs) - padding, 0)
        py0 = max(min(ys) - padding, 0)
        px1 = min(max(xs) + padding, x_max)
        py1 = min(max(ys) + padding, y_max)
        return px0, py0, px1, py1

    def waypoints_in_view(
        self,
        waypoints: List[Waypoint],
        floor: int,
        px0: int,
        py0: int,
        px1: int,
        py1: int,
    ) -> List[Waypoint]:
        """Return the subset of *waypoints* whose pixel position falls inside
        the rectangle ``(px0, py0, px1, py1)`` on the given *floor*.
        """
        result = []
        for wp in waypoints:
            if wp.coord.z != floor:
                continue
            px, py = wp.coord.to_pixel()
            if px0 <= px <= px1 and py0 <= py <= py1:
                result.append(wp)
        return result

    @staticmethod
    def segment_colors(n: int) -> List[Tuple[float, float, float]]:
        """Return *n* visually distinct RGB tuples (values 0-1) for coloring
        multiple route segments in ``show_multi_route``.

        When ``n == 0`` an empty list is returned.
        """
        if n <= 0:
            return []
        # Evenly space hue around the colour wheel, then convert HSV→RGB
        colors: List[Tuple[float, float, float]] = []
        for i in range(n):
            # Simple HSV→RGB with S=1, V=1
            h = i / n
            h6 = h * 6.0
            hi = int(h6) % 6
            f  = h6 - int(h6)
            q  = 1.0 - f
            rgb_map = [
                (1.0, f,   0.0),
                (q,   1.0, 0.0),
                (0.0, 1.0, f  ),
                (0.0, q,   1.0),
                (f,   0.0, 1.0),
                (1.0, 0.0, q  ),
            ]
            colors.append(rgb_map[hi])
        return colors

    def has_map_image(self, floor: int) -> bool:
        """Return True when the loader already has a map image for *floor*.

        Delegates to ``loader.floor_loaded()`` so no image is loaded just
        to answer the query.
        """
        return self.loader.floor_loaded(floor)

    def stats_snapshot(self) -> dict[str, Any]:
        """Point-in-time diagnostic dict.

        Keys
        ----
        loaded_count : int   — floors cached in the loader
        waypoints_loaded : bool — whether waypoints have been parsed
        """
        snap = self.loader.stats_snapshot()
        return {
            "loaded_count":     snap.get("loaded_count", 0),
            "waypoints_loaded": snap.get("waypoints_loaded", False),
        }

    @property
    def loaded_count(self) -> int:
        """Number of floors currently cached in the underlying loader."""
        return self.loader.loaded_count

    @property
    def waypoints_loaded(self) -> bool:
        """True when the loader has at least one waypoint available."""
        return self.loader.has_waypoints

    @property
    def map_images_count(self) -> int:
        """Number of floor map images currently cached in the underlying loader."""
        return self.loader.map_images_count

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _draw_route(
        self,
        ax: Any,
        route: Route,
        color: Tuple[float, float, float] = ROUTE_COLOR,
        label: str = "",
        linewidth: float = ROUTE_WIDTH,
    ) -> None:
        if not route.found or not route.steps:
            return
        xs = [c.to_pixel()[0] for c in route.steps]
        ys = [c.to_pixel()[1] for c in route.steps]
        ax.plot(xs, ys, "-", color=color, linewidth=linewidth, label=label or "Route", zorder=3)
        # Start marker
        ax.scatter(xs[0], ys[0], c=[START_COLOR], s=80, zorder=5, marker="o")
        # End marker
        ax.scatter(xs[-1], ys[-1], c=[END_COLOR], s=80, zorder=5, marker="X")

    @staticmethod
    def _draw_waypoint(ax: Any, wp: Waypoint) -> None:
        px, py = wp.coord.to_pixel()
        ax.scatter(px, py, c=[WAYPOINT_COLOR], s=30, zorder=4, marker="^", edgecolors="black", linewidths=0.5)
        ax.annotate(
            wp.name,
            (px, py),
            textcoords="offset points",
            xytext=(4, 4),
            fontsize=5,
            color="white",
            zorder=6,
        )
