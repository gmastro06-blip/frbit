"""
Example 3  Searching and listing waypoints, floor statistics, and
             named-location navigation.

Run:
    python examples/waypoint_search.py
"""

import sys
from pathlib import Path

# Add parent directory to path so src module can be imported
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models import Coordinate as Coordinate
from src.navigator import WaypointNavigator as WaypointNavigator
from src.visualizer import MapVisualizer as MapVisualizer


def main() -> None:
    print("=" * 60)
    print("  WaypointNavigator Waypoint Search & Stats Example")
    print("=" * 60)

    nav = WaypointNavigator()

    # -----------------------------------------------------------------------
    # 1. Search for waypoints by keyword
    # -----------------------------------------------------------------------
    keywords = ["depot", "temple", "boat", "lighthouse", "cave"]
    for kw in keywords:
        results = nav.find_waypoints(kw)
        print(f"\n[{kw!r}]  {len(results)} result(s)")
        for wp in results[:5]:
            print(f"    {wp}")
        if len(results) > 5:
            print(f"    … and {len(results) - 5} more")

    # -----------------------------------------------------------------------
    # 2. Floor statistics (downloads path PNG on first call)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Floor walkability statistics")
    print("=" * 60)
    for floor_id in [6, 7, 8]:
        stats = nav.walkable_region_stats(floor_id)
        print(
            f"  Floor {stats['floor']:02d} │ "
            f"walkable: {stats['walkable_tiles']:>8,} / {stats['total_tiles']:,} "
            f"({stats['pct_walkable']:5.1f}%)"
        )

    # -----------------------------------------------------------------------
    # 3. Named navigation: depot → temple
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Named navigation: 'thais depot' → 'thais temple'")
    print("=" * 60)
    try:
        route = nav.navigate_by_name("thais depot", "thais temple", floor=7)
        print(route.summary())

        # Show the nearest waypoints along the route
        nearby = nav.nearest_waypoint(route.end, top_n=8)
        print("\nWaypoints near destination:")
        for wp in nearby:
            print(f"  {wp}")

        if route.found:
            out_dir = Path(__file__).parent.parent / "output"
            out_dir.mkdir(exist_ok=True)
            viz = MapVisualizer(nav.loader)
            viz.show_route(
                route,
                waypoints=nearby,
                title="Thais: Depot → Temple",
                save_path=out_dir / "depot_to_temple.png",
            )

    except ValueError as exc:
        print(f"Navigation failed: {exc}")
        # Fallback to hardcoded coordinates
        START = Coordinate(32369, 32241, 7)
        END   = Coordinate(32344, 32219, 7)
        print(f"Using fallback coordinates: {START} → {END}")
        route = nav.navigate(START, END)
        print(route.summary())


if __name__ == "__main__":
    main()
