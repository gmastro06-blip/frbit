"""
Navigator
-----------------
High-level navigation API that ties together:
  - TibiaMapLoader  (map data + walkability)
  - AStarPathfinder (shortest path)
  - Waypoint model  (named locations)

Usage example
-------------
    from src import WaypointNavigator, Coordinate

    nav = WaypointNavigator()
    nav.load_floor(7)                       # ground floor

    start = Coordinate(32369, 32241, 7)     # Thais depot
    end   = Coordinate(32343, 32211, 7)

    route = nav.navigate(start, end)
    print(route.summary())

    # Or use named waypoints
    route2 = nav.navigate_by_name("Thais depot", "Thais temple")
    print(route2.summary())
"""

from __future__ import annotations

import heapq
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

_ROUTE_CACHE_MAXSIZE = 256  # unique routes kept across a typical session

from .map_loader import TibiaMapLoader
from .models import Coordinate, FloorTransition, Route, Waypoint
from .pathfinder import AStarPathfinder
from .transitions import TransitionRegistry


def _merge_segments(
    start: Coordinate,
    end: Coordinate,
    segments: List[Route],
) -> Route:
    steps: List[Coordinate] = []
    total_distance = 0.0
    found = bool(segments)
    for seg in segments:
        steps.extend(seg.steps)
        total_distance += seg.total_distance
        if not seg.found:
            found = False
    return Route(start=start, end=end, steps=steps,
                 total_distance=total_distance, found=found)


class WaypointNavigator:
    """
    Main navigator class.

    Parameters
    ----------
    cache_dir : Path, optional
        Directory where map PNGs / JSON are cached.
    """

    def __init__(self, cache_dir: Optional[Path] = None, log_fn: Optional[Callable[[str], None]] = None) -> None:
        self.loader = TibiaMapLoader(cache_dir=cache_dir)
        self._pathfinders: Dict[int, AStarPathfinder] = {}
        self._custom_waypoints: List[Waypoint] = []
        self._route_cache: Dict[Tuple[Coordinate, Coordinate], Route] = {}
        self.transitions = TransitionRegistry.load()
        self._log: Callable[[str], None] = log_fn or print

    # -----------------------------------------------------------------------
    # Floor management
    # -----------------------------------------------------------------------

    def load_floor(self, floor: int) -> None:
        """Download (if needed) and build the pathfinder for *floor*."""
        self._log(f"Loading floor {floor:02d} …")
        walkability = self.loader.get_walkability(floor)
        self._pathfinders[floor] = AStarPathfinder(walkability)
        self._route_cache.clear()
        self._log(f"Floor {floor:02d} ready. Map size: {walkability.shape[1]}×{walkability.shape[0]} tiles.")

    def is_floor_loaded(self, floor: int) -> bool:
        return floor in self._pathfinders

    # -----------------------------------------------------------------------
    # Waypoint management
    # -----------------------------------------------------------------------

    def add_waypoint(self, waypoint: Waypoint) -> None:
        """Add a custom waypoint to the navigator."""
        self._custom_waypoints.append(waypoint)

    def add_waypoint_from_coords(
        self,
        name: str,
        x: int,
        y: int,
        z: int,
        description: str = "",
    ) -> Waypoint:
        wp = Waypoint(name=name, coord=Coordinate(x, y, z), description=description)
        self.add_waypoint(wp)
        return wp

    def get_all_waypoints(self) -> List[Waypoint]:
        """Return built-in markers + custom waypoints."""
        return self.loader.get_waypoints() + self._custom_waypoints

    def find_waypoints(self, query: str, floor: Optional[int] = None) -> List[Waypoint]:
        """Search all waypoints by name (case-insensitive substring)."""
        q = query.lower()
        results = [wp for wp in self.get_all_waypoints() if q in wp.name.lower()]
        if floor is not None:
            results = [wp for wp in results if wp.coord.z == floor]
        return results

    def save_custom_waypoints(self, path: Path) -> None:
        """Persist custom waypoints to a JSON file (atomic write)."""
        data = [wp.to_dict() for wp in self._custom_waypoints]
        tmp = Path(str(path) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
        self._log(f"Saved {len(data)} custom waypoints → {path}")

    def load_custom_waypoints(self, path: Path) -> None:
        """Load custom waypoints from a JSON file (appends to existing)."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for item in data:
            self._custom_waypoints.append(Waypoint.from_dict(item))
        self._log(f"Loaded {len(data)} custom waypoints from {path}")

    def remove_waypoint(self, name: str) -> int:
        """
        Remove all custom waypoints whose name contains *name* (case-insensitive).

        Returns the number of waypoints removed.
        """
        q = name.lower()
        before = len(self._custom_waypoints)
        self._custom_waypoints = [
            wp for wp in self._custom_waypoints if q not in wp.name.lower()
        ]
        removed = before - len(self._custom_waypoints)
        if removed:
            self._log(f"Removed {removed} custom waypoint(s) matching '{name}'")
        return removed

    def clear_custom_waypoints(self) -> None:
        """Remove all custom waypoints."""
        count = len(self._custom_waypoints)
        self._custom_waypoints.clear()
        self._route_cache.clear()
        self._log(f"Cleared {count} custom waypoint(s)")

    # -----------------------------------------------------------------------
    # Navigation
    # -----------------------------------------------------------------------

    def navigate(self, start: Coordinate, end: Coordinate) -> Route:
        """
        Find the shortest walkable path from *start* to *end*.
        Automatically loads the required floor if not already loaded.
        For cross-floor navigation use navigate_multifloor().
        """
        if start.z != end.z:
            raise ValueError(
                "Cross-floor navigation requires navigate_multifloor(). "
                "Both coordinates must be on the same floor for navigate()."
            )
        floor = start.z
        if not self.is_floor_loaded(floor):
            self.load_floor(floor)

        pathfinder = self._pathfinders[floor]
        cache_key = (start, end)
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]
        route = pathfinder.find_path(start, end)
        if route.found:
            if len(self._route_cache) >= _ROUTE_CACHE_MAXSIZE:
                # Evict oldest entry (insertion-order dict, Python 3.7+)
                self._route_cache.pop(next(iter(self._route_cache)))
            self._route_cache[cache_key] = route
        return route

    def _walk_floor_path(
        self,
        start: Coordinate,
        floor_path: List[Tuple[Coordinate, Optional[FloorTransition]]],
    ) -> List[Tuple[Route, Optional[FloorTransition]]]:
        result: List[Tuple[Route, Optional[FloorTransition]]] = []
        current_pos = start
        for dest_coord, transition in floor_path:
            seg = self.navigate(current_pos, dest_coord)
            result.append((seg, transition))
            if not seg.found:
                break
            current_pos = transition.exit if transition is not None else dest_coord
        return result

    def navigate_multifloor(
        self,
        start: Coordinate,
        end: Coordinate,
        max_transitions: int = 10,
    ) -> List[Route]:
        """
        Find a path between *start* and *end* across multiple floors.

        Returns a list of Route segments. Each segment is a single-floor A*
        path. Between consecutive segments there is a FloorTransition.
        The transition tile is the LAST step of segment[i] and the EXIT
        coordinate is the FIRST step of segment[i+1].

        Algorithm
        ---------
        1. If same floor → single navigate() call (falls back to normal A*).
        2. Find all transitions reachable from start.z towards end.z using
           Dijkstra over the floor-transition graph.
        3. For each hop: A*(current_pos → transition.entry) + hop to exit.
        4. Final hop: A*(last_exit → end).

        Returns
        -------
        list[Route]
            Ordered list of single-floor Route segments.
            Access the full action plan with navigate_multifloor_plan().
        """
        if start.z == end.z:
            direct = self.navigate(start, end)
            if direct.found:
                return [direct]
            # Same floor but disconnected components (e.g. Venore canal).
            # Fall through to multifloor Dijkstra to route via another floor.

        # ── Step 1: find floor path via Dijkstra on transition graph ──────
        floor_path = self._floor_dijkstra(start, end, max_transitions, force_multifloor=(start.z == end.z))
        if floor_path is None:
            # No transition chain found → return empty failed route
            return [Route(start=start, end=end, found=False)]

        # floor_path: list of (Coordinate, FloorTransition|None)
        # Each entry is (pos_on_floor, transition_to_use_to_advance)
        # Last entry has transition=None (destination reached)

        return [seg for seg, _ in self._walk_floor_path(start, floor_path)]

    def navigate_multifloor_plan(
        self,
        start: Coordinate,
        end: Coordinate,
    ) -> List[dict[str, Any]]:
        """
        Returns a human-readable step plan for a multi-floor route.

        Each dict has:
            'type'     : 'walk' | 'transition'
            'segment'  : Route (for walk steps)
            'transition': FloorTransition (for transition steps)
            'summary'  : str description
        """
        if start.z == end.z:
            route = self.navigate(start, end)
            if route.found:
                return [{"type": "walk", "segment": route, "summary": route.summary()}]
            # Same floor, disconnected components — fall through to multifloor Dijkstra

        floor_path = self._floor_dijkstra(start, end, max_transitions=10, force_multifloor=(start.z == end.z))
        if floor_path is None:
            return [{"type": "error", "summary": f"No floor path from z={start.z} to z={end.z}"}]

        plan: List[dict[str, Any]] = []
        for seg, transition in self._walk_floor_path(start, floor_path):
            plan.append({"type": "walk", "segment": seg, "summary": seg.summary()})
            if transition is not None:
                plan.append({
                    "type": "transition",
                    "transition": transition,
                    "summary": str(transition),
                })
        return plan

    # -----------------------------------------------------------------------
    # Internal: floor-graph Dijkstra
    # -----------------------------------------------------------------------

    def _floor_dijkstra(
        self,
        start: Coordinate,
        end: Coordinate,
        max_transitions: int = 10,
        force_multifloor: bool = False,
    ) -> Optional[List[Tuple[Coordinate, Optional[FloorTransition]]]]:
        """
        Dijkstra over the transition graph to find the cheapest sequence of
        floor hops from start.z to end.z.

        Parameters
        ----------
        force_multifloor : bool
            When True, skip the same-floor early-return and always explore
            floor transitions.  Used by ``navigate_multifloor`` when A* fails
            on the same floor (disconnected components — e.g. Venore canal).

        Returns a list of (target_coord, transition_or_None) tuples.
        The last tuple always has transition=None and target_coord=end.
        Returns None if no path exists.

        Cost model:
            - Euclidean distance from current pos to transition.entry (tiles)
            - +1 for each floor change (to prefer fewer hops)

        Uses a parent-pointer dict for path reconstruction, eliminating
        the O(depth) list copy on every heapq.heappush call.
        """
        # Same-floor shortcut: caller already verified A* connectivity.
        if start.z == end.z and not force_multifloor:
            return [(end, None)]

        INF = float("inf")
        start_node: Tuple[int, int, int] = (start.x, start.y, start.z)
        # heap: (cost, (x, y, z), depth)
        heap: list[tuple[float, tuple[int, int, int], int]] = [(0.0, start_node, 0)]
        # visited: nodes already popped (stale-entry guard)
        visited: Dict[Tuple[int, int, int], float] = {}
        # dist: best known cost to each node (updated at PUSH time)
        # This prevents overwriting parent pointers with suboptimal paths
        # before a node is popped (classic Dijkstra g-score dict).
        dist: Dict[Tuple[int, int, int], float] = {start_node: 0.0}
        # parent[node] = (prev_node, path_step) | None for start_node
        # path_step = (dest_coord, transition) meaning:
        #   "walk to dest_coord on dest_coord.z, then cross transition"
        parent: Dict[Tuple[int, int, int], Any] = {start_node: None}

        while heap:
            cost, node, depth = heapq.heappop(heap)
            x, y, z = node

            if visited.get(node, INF) <= cost:
                continue
            visited[node] = cost

            if z == end.z and depth > 0:
                # Reached destination floor after at least one hop.
                # Verify that `end` is actually reachable from this position
                # on the same floor (guards against arriving on the wrong
                # disconnected island on the same floor, e.g. Venore canal).
                # (depth>0 guard prevents early-exit when start.z == end.z)
                candidate = Coordinate(x, y, z)
                reachability = self.navigate(candidate, end)
                if reachability.found:
                    steps: List[Tuple[Coordinate, Optional[FloorTransition]]] = []
                    cur = node
                    while parent[cur] is not None:
                        prev_node, path_step = parent[cur]
                        steps.append(path_step)
                        cur = prev_node
                    steps.reverse()
                    steps.append((end, None))
                    return steps
                # Not reachable from here — continue looking for a better path

            if depth >= max_transitions:
                continue

            # Explore transitions from this floor
            current_coord = Coordinate(x, y, z)
            for t in self.transitions.from_floor(z):
                # Check whether the transition entry is actually reachable
                # from the current position on floor z.  This is necessary
                # because Euclidean distance is an unreliable proxy when
                # islands are disconnected (e.g. Venore canal separating
                # depot island from temple island on the same floor).
                entry_coord = Coordinate(t.entry.x, t.entry.y, z)
                reach_to_entry = self.navigate(current_coord, entry_coord)
                if not reach_to_entry.found:
                    continue  # transition not accessible from this position

                d = reach_to_entry.total_distance
                new_cost = cost + d + 1.0  # +1 per floor hop
                exit_node: Tuple[int, int, int] = (t.exit.x, t.exit.y, t.exit.z)
                # Use dist (updated at push time) so we don't overwrite a
                # cheaper parent that was set before this node was visited.
                if dist.get(exit_node, INF) > new_cost:
                    dist[exit_node] = new_cost
                    parent[exit_node] = (
                        node,
                        (Coordinate(t.entry.x, t.entry.y, z), t),
                    )
                    heapq.heappush(heap, (new_cost, exit_node, depth + 1))

        return None  # no path found

    def navigate_by_name(
        self,
        start_name: str,
        end_name: str,
        floor: Optional[int] = None,
    ) -> Route:
        """
        Navigate between two waypoints by name.

        When *floor* is given, results are restricted to that floor.
        When multiple waypoints match and *floor* is not given, the pair
        (start, end) that minimises ``start.coord.euclidean_to(end.coord)``
        across the same floor is preferred over a cross-floor first-match.
        A warning is logged whenever more than one candidate exists.
        """
        starts = self.find_waypoints(start_name, floor=floor)
        ends = self.find_waypoints(end_name, floor=floor)

        if not starts:
            raise ValueError(f"No waypoint found matching '{start_name}'.")
        if not ends:
            raise ValueError(f"No waypoint found matching '{end_name}'.")

        # Disambiguate: prefer same-floor pairs, then closest pair overall
        if len(starts) > 1:
            self._log(f"navigate_by_name: {len(starts)} candidates for '{start_name}' — picking best match")
        if len(ends) > 1:
            self._log(f"navigate_by_name: {len(ends)} candidates for '{end_name}' — picking best match")

        # Try to find a same-floor pair first
        start_wp = ends_wp = None
        best_dist = float("inf")
        for s in starts:
            for e in ends:
                if s.coord.z != e.coord.z:
                    continue
                d = s.coord.euclidean_to(e.coord)
                if d < best_dist:
                    best_dist = d
                    start_wp, ends_wp = s, e

        # Fall back to closest cross-floor pair if no same-floor pair found
        if start_wp is None:
            self._log(f"navigate_by_name: no same-floor pair for '{start_name}'→'{end_name}' — using multifloor")
            for s in starts:
                for e in ends:
                    d = abs(s.coord.z - e.coord.z) * 1000 + s.coord.euclidean_to(e.coord)
                    if d < best_dist:
                        best_dist = d
                        start_wp, ends_wp = s, e

        if start_wp is None or ends_wp is None:  # pragma: no cover
            raise ValueError(
                f"navigate_by_name: no waypoints found for "
                f"'{start_name}' or '{end_name}'"
            )
        self._log(f"Navigating: {start_wp} → {ends_wp}")
        if start_wp.coord.z != ends_wp.coord.z:
            segments = self.navigate_multifloor(start_wp.coord, ends_wp.coord)
            return _merge_segments(start_wp.coord, ends_wp.coord, segments)
        route = self.navigate(start_wp.coord, ends_wp.coord)
        if route.found:
            return route
        # Same floor but disconnected — try multifloor as fallback
        self._log("navigate_by_name: same-floor path failed, trying multifloor fallback")
        segments = self.navigate_multifloor(start_wp.coord, ends_wp.coord)
        if not segments:
            return route  # return the original failed route
        return _merge_segments(start_wp.coord, ends_wp.coord, segments)

    def navigate_route(
        self,
        waypoints: List[Coordinate],
    ) -> List[Route]:
        """
        Navigate through an ordered list of coordinates (multi-stop route).
        Returns the list of segment routes.
        """
        if len(waypoints) < 2:
            raise ValueError("Need at least 2 waypoints for a route.")

        segments: List[Route] = []
        for i in range(len(waypoints) - 1):
            seg = self.navigate(waypoints[i], waypoints[i + 1])
            segments.append(seg)
            status = "✓" if seg.found else "✗"
            self._log(f"  Segment {i + 1}: {waypoints[i]} → {waypoints[i + 1]}   {status} ({len(seg.steps)} steps)")
        return segments

    def total_distance(self, segments: List[Route]) -> float:
        """Sum of all segment distances."""
        return sum(s.total_distance for s in segments)

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    def nearest_waypoint(self, coord: Coordinate, top_n: int = 5) -> List[Waypoint]:
        """Return the *top_n* closest map markers to *coord* (same floor)."""
        candidates = [
            wp for wp in self.get_all_waypoints()
            if wp.coord.z == coord.z
        ]
        candidates.sort(key=lambda wp: coord.euclidean_to(wp.coord))
        return candidates[:top_n]

    def walkable_region_stats(self, floor: int) -> dict[str, Any]:
        """Return basic stats about the walkable area on a floor.

        Uses the already-loaded pathfinder walkability matrix when available
        (avoids a redundant disk/network fetch).
        """
        if floor in self._pathfinders:
            w = self._pathfinders[floor].walkability
        else:
            w = self.loader.get_walkability(floor)
        total = w.size
        walkable = int(w.sum())
        return {
            "floor": floor,
            "total_tiles": total,
            "walkable_tiles": walkable,
            "non_walkable": total - walkable,
            "pct_walkable": round(100.0 * walkable / total, 2),
        }

    def loaded_floors(self) -> List[int]:
        """Return a sorted list of floor numbers that have an active pathfinder."""
        return sorted(self._pathfinders.keys())

    @property
    def floor_count(self) -> int:
        """Number of floors that currently have a loaded pathfinder."""
        return len(self._pathfinders)

    @property
    def custom_waypoint_count(self) -> int:
        """Number of custom waypoints added via :meth:`add_waypoint`."""
        return len(self._custom_waypoints)

    def stats_snapshot(self) -> Dict[str, Any]:
        """Return a point-in-time summary of navigator state.

        Keys
        ----
        loaded_floors       Sorted list of floors with an active pathfinder.
        floor_count         Number of loaded floors.
        custom_waypoints    Number of custom waypoints.
        total_waypoints     Custom + built-in waypoints.
        transitions_loaded  Number of registered floor transitions.
        """
        return {
            "loaded_floors":      self.loaded_floors(),
            "floor_count":        self.floor_count,
            "custom_waypoints":   self.custom_waypoint_count,
            "total_waypoints":    len(self.get_all_waypoints()),
            "transitions_loaded": len(self.transitions),
        }

    @property
    def has_custom_waypoints(self) -> bool:
        """True when at least one custom waypoint has been added."""
        return self.custom_waypoint_count > 0

    @property
    def has_loaded_floors(self) -> bool:
        """True when at least one floor has an active pathfinder loaded."""
        return self.floor_count > 0

    @property
    def total_waypoint_count(self) -> int:
        """Total number of waypoints available (built-in + custom)."""
        return len(self.get_all_waypoints())

    @property
    def has_transitions(self) -> bool:
        """True when at least one floor transition is registered."""
        return len(self.transitions) > 0

    def unload_floor(self, floor: int) -> bool:
        """
        Release the pathfinder for *floor*, freeing its walkability array.

        Returns
        -------
        bool
            True if the floor was loaded (and is now unloaded), False if it
            was not loaded to begin with.
        """
        if floor in self._pathfinders:
            del self._pathfinders[floor]
            self._log(f"Floor {floor:02d} unloaded.")
            return True
        return False

    def reload_floor(self, floor: int) -> None:
        """Force a fresh load of *floor*, discarding any cached pathfinder."""
        self.unload_floor(floor)
        self.load_floor(floor)

    def waypoints_on_floor(self, floor: int) -> List[Waypoint]:
        """Return all waypoints (built-in + custom) located on *floor*."""
        return [wp for wp in self.get_all_waypoints() if wp.coord.z == floor]

    def waypoints_by_name(self, names: Iterable[str]) -> List[Waypoint]:
        """
        Return waypoints whose name exactly matches any entry in *names*.

        *names* may be any iterable of strings.  Lookups are case-sensitive.
        Order mirrors the order of ``get_all_waypoints()``.

        Example
        -------
        >>> nav.waypoints_by_name(["Thais Temple", "Thais Depot"])
        """
        name_set = set(names)
        return [wp for wp in self.get_all_waypoints() if wp.name in name_set]

    def count_waypoints_on_floor(self, floor: int) -> int:
        """
        Return the number of waypoints (built-in + custom) on *floor*.

        Equivalent to ``len(waypoints_on_floor(floor))`` but avoids building
        a temporary list.
        """
        return sum(1 for wp in self.get_all_waypoints() if wp.coord.z == floor)

    def all_loaded_floor_stats(self) -> List[dict[str, Any]]:
        """
        Return :meth:`walkable_region_stats` for every currently loaded floor.

        Results are ordered by floor number (ascending).
        """
        return [self.walkable_region_stats(f) for f in self.loaded_floors()]

    def route_summary(self, segments: List[Route]) -> str:
        """
        Build a multi-line summary string for a list of route segments.

        Format
        ------
        Segment 1/N: (x,y,z) → (x,y,z)  ✓  42 steps  dist=58.3
        Segment 2/N: ...
        ─────────────────────────────────────────
        Total: N segments | M steps | dist=D.D
        """
        if not segments:
            return "No segments."
        lines: List[str] = []
        total_steps = 0
        total_dist = 0.0
        n = len(segments)
        for i, seg in enumerate(segments, start=1):
            status = "✓" if seg.found else "✗"
            steps = len(seg.steps)
            total_steps += steps
            total_dist += seg.total_distance
            lines.append(
                f"Segment {i}/{n}: {seg.start} → {seg.end}  "
                f"{status}  {steps} steps  dist={seg.total_distance:.1f}"
            )
        lines.append("─" * 45)
        lines.append(
            f"Total: {n} segment(s) | {total_steps} steps | dist={total_dist:.1f}"
        )
        return "\n".join(lines)
