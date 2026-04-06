"""
Example 1 – Basic navigation between two coordinates on the ground floor.

Run:
    python examples/basic_navigation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import Coordinate
from src.navigator import WaypointNavigator
from src.visualizer import MapVisualizer

# ---------------------------------------------------------------------------
# Ground floor (z=7) coordinates near Thais city center
# Thais depot area → Thais temple
# ---------------------------------------------------------------------------
START = Coordinate(x=32369, y=32241, z=7)   # Near Thais depot
END   = Coordinate(x=32344, y=32220, z=7)   # Near Thais temple


def main() -> None:
    print("=" * 60)
    print("  WaypointNavigator – Basic Navigation Example")
    print("=" * 60)

    # 1. Create navigator (downloads floor 7 PNGs on first run)
    nav = WaypointNavigator()

    # 2. Find path
    print(f"\nSearching path from {START} to {END} …")
    route = nav.navigate(START, END)

    # 3. Print result
    print(f"\n{route.summary()}")
    if route.found:
        print(f"\nFirst 10 steps:")
        for step in route.steps[:10]:
            print(f"  → {step}")
        if len(route.steps) > 10:
            print(f"  … ({len(route.steps) - 10} more steps)")

    # 4. Find nearest waypoints to the destination
    print("\nNearest map markers to destination:")
    nearest = nav.nearest_waypoint(END, top_n=5)
    for wp in nearest:
        dist = END.euclidean_to(wp.coord)
        print(f"  {wp.name:<35} {dist:5.1f} tiles away")

    # 5. Visualize (will open a matplotlib window; comment out if headless)
    if route.found:
        print("\nRendering route visualization …")
        viz = MapVisualizer(nav.loader)
        viz.show_route(
            route,
            waypoints=nearest,
            title="Thais area – Ground Floor",
            save_path=Path("output_basic_navigation.png"),
        )
        print("Saved to output_basic_navigation.png")


if __name__ == "__main__":
    main()
