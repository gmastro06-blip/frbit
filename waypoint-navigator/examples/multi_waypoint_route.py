"""
Example 2 – Multi-stop waypoint route across Rookgaard / Thais area.

This example:
  1. Loads named waypoints from the official map markers
  2. Plans a multi-stop route through several landmarks
  3. Adds a custom user-defined waypoint
  4. Visualizes all segments together

Run:
    python examples/multi_waypoint_route.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import Coordinate
from src.navigator import WaypointNavigator
from src.visualizer import MapVisualizer


# ---------------------------------------------------------------------------
# A hand-crafted tour of Thais area (all ground floor, z=7)
# Coordinates from https://tibiamaps.io/map
# ---------------------------------------------------------------------------
STOPS = [
    Coordinate(32369, 32241, 7),   # Thais depot
    Coordinate(32344, 32219, 7),   # Thais temple area
    Coordinate(32382, 32236, 7),   # Thais city gate (East)
    Coordinate(32351, 32245, 7),   # Thais marketplace
]

STOP_LABELS = [
    "Thais Depot",
    "Thais Temple",
    "East Gate",
    "Marketplace",
]


def main() -> None:
    print("=" * 60)
    print("  WaypointNavigator – Multi-Stop Route Example")
    print("=" * 60)

    nav = WaypointNavigator()

    # -- Add custom waypoints ------------------------------------------------
    for coord, label in zip(STOPS, STOP_LABELS):
        nav.add_waypoint_from_coords(
            name=label,
            x=coord.x,
            y=coord.y,
            z=coord.z,
            description=f"Custom stop: {label}",
        )

    # -- Plan multi-stop route -----------------------------------------------
    print(f"\nPlanning route with {len(STOPS)} stops …\n")
    segments = nav.navigate_route(STOPS)

    # -- Summary -------------------------------------------------------------
    total = nav.total_distance(segments)
    found_count = sum(1 for s in segments if s.found)
    print(f"\nCompleted {found_count}/{len(segments)} segments.")
    print(f"Total walking distance: {total:.1f} tiles")

    # -- Save custom waypoints to JSON ---------------------------------------
    out_dir = Path(__file__).parent.parent / "output"
    out_dir.mkdir(exist_ok=True)
    nav.save_custom_waypoints(out_dir / "custom_waypoints.json")

    # -- Visualize -----------------------------------------------------------
    print("\nRendering multi-stop route visualization …")
    viz = MapVisualizer(nav.loader)
    viz.show_multi_route(
        segments,
        waypoints=nav.find_waypoints("", floor=7),
        title="Thais Multi-Stop Route – Ground Floor (z=7)",
        save_path=out_dir / "multi_stop_route.png",
    )
    print(f"Saved → {out_dir / 'multi_stop_route.png'}")


if __name__ == "__main__":
    main()
