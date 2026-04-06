"""
Tests for WaypointNavigator multi-floor navigation:
  - navigate_multifloor()
  - navigate_multifloor_plan()
  - _floor_dijkstra()

Fully offline: pathfinders injected from synthetic walkability arrays,
transitions built in-memory — no map downloads.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import numpy as np
import pytest

from src.models import Coordinate, FloorTransition, Route, BOUNDS
from src.navigator import WaypointNavigator
from src.pathfinder import AStarPathfinder
from src.transitions import TransitionRegistry


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_XO = BOUNDS["xMin"]
_YO = BOUNDS["yMin"]


def _coord(px: int, py: int, z: int = 7) -> Coordinate:
    return Coordinate(_XO + px, _YO + py, z)


def _all_walkable(h: int = 200, w: int = 200) -> np.ndarray:
    return np.ones((h, w), dtype=bool)


def _build_navigator(floors: List[int], map_size: int = 200) -> WaypointNavigator:
    """
    Build a WaypointNavigator with synthetic pathfinders injected for
    the specified floors — no network calls.
    """
    # Patch TransitionRegistry.load() to avoid reading cache/transitions.json
    with patch.object(TransitionRegistry, "load", return_value=TransitionRegistry()):
        nav = WaypointNavigator.__new__(WaypointNavigator)
        nav._pathfinders = {}
        nav._custom_waypoints = []
        nav._route_cache = {}
        nav.transitions = TransitionRegistry()
        # Stub loader so load_floor() never makes network calls
        from src.map_loader import TibiaMapLoader
        nav.loader = TibiaMapLoader.__new__(TibiaMapLoader)
        nav.loader._map_images = {}
        nav.loader._walkability = {}
        nav.loader._waypoints = None

    walk = _all_walkable(map_size, map_size)
    for z in floors:
        nav._pathfinders[z] = AStarPathfinder(walk)
    return nav


def _add_transition(nav: WaypointNavigator, t: FloorTransition) -> None:
    nav.transitions.add(t)


# ─────────────────────────────────────────────────────────────────────────────
# Same-floor: navigate_multifloor delegates to navigate()
# ─────────────────────────────────────────────────────────────────────────────

class TestSameFloor:

    def test_same_floor_returns_single_segment(self):
        nav = _build_navigator([7])
        start = _coord(10, 10, 7)
        end   = _coord(20, 10, 7)
        segments = nav.navigate_multifloor(start, end)
        assert len(segments) == 1
        assert segments[0].found is True

    def test_same_floor_path_reaches_destination(self):
        nav = _build_navigator([7])
        start = _coord(5, 5, 7)
        end   = _coord(5, 15, 7)
        segments = nav.navigate_multifloor(start, end)
        assert segments[0].steps[-1] == end


# ─────────────────────────────────────────────────────────────────────────────
# Cross-floor with no transitions → failed route
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTransitions:

    def test_cross_floor_no_transitions_returns_failed_route(self):
        nav = _build_navigator([7, 8])
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        # No transitions registered → Dijkstra returns None
        segments = nav.navigate_multifloor(start, end)
        assert len(segments) == 1
        assert segments[0].found is False

    def test_failed_route_has_correct_start_and_end(self):
        nav = _build_navigator([7, 8])
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        seg = nav.navigate_multifloor(start, end)[0]
        assert seg.start == start
        assert seg.end   == end


# ─────────────────────────────────────────────────────────────────────────────
# Cross-floor with one transition (z=7 → z=8)
# ─────────────────────────────────────────────────────────────────────────────

class TestOneTransition:

    def _nav_with_transition(self) -> tuple:
        nav = _build_navigator([7, 8])
        entry = _coord(50, 50, 7)
        exit_ = _coord(50, 50, 8)
        t = FloorTransition(entry=entry, exit=exit_, kind="walk")
        _add_transition(nav, t)
        return nav, t

    def test_returns_two_segments(self):
        nav, t = self._nav_with_transition()
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        segments = nav.navigate_multifloor(start, end)
        # Segment 1: start → transition.entry (floor 7)
        # Segment 2: transition.exit → end (floor 8)
        assert len(segments) == 2

    def test_first_segment_ends_at_transition_entry(self):
        nav, t = self._nav_with_transition()
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        segments = nav.navigate_multifloor(start, end)
        assert segments[0].steps[-1] == t.entry

    def test_second_segment_ends_at_destination(self):
        nav, t = self._nav_with_transition()
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        segments = nav.navigate_multifloor(start, end)
        assert segments[-1].steps[-1] == end

    def test_all_segments_found(self):
        nav, t = self._nav_with_transition()
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        segments = nav.navigate_multifloor(start, end)
        assert all(s.found for s in segments)


# ─────────────────────────────────────────────────────────────────────────────
# Two hops: z=7 → z=8 → z=9
# ─────────────────────────────────────────────────────────────────────────────

class TestTwoHops:

    def _nav_two_hops(self):
        nav = _build_navigator([7, 8, 9])
        t1 = FloorTransition(
            entry=_coord(50, 50, 7), exit=_coord(50, 50, 8), kind="walk"
        )
        t2 = FloorTransition(
            entry=_coord(80, 80, 8), exit=_coord(80, 80, 9), kind="walk"
        )
        _add_transition(nav, t1)
        _add_transition(nav, t2)
        return nav, t1, t2

    def test_three_segments_for_two_floor_hops(self):
        nav, t1, t2 = self._nav_two_hops()
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 9)
        segments = nav.navigate_multifloor(start, end)
        assert len(segments) == 3

    def test_second_segment_connects_transitions(self):
        nav, t1, t2 = self._nav_two_hops()
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 9)
        segments = nav.navigate_multifloor(start, end)
        # Middle segment goes from t1.exit to t2.entry (both on floor 8)
        mid = segments[1]
        assert mid.start.z == 8
        assert mid.found


# ─────────────────────────────────────────────────────────────────────────────
# navigate_multifloor_plan
# ─────────────────────────────────────────────────────────────────────────────

class TestMultifloorPlan:

    def test_same_floor_plan_has_single_walk_step(self):
        nav = _build_navigator([7])
        plan = nav.navigate_multifloor_plan(_coord(5, 5), _coord(15, 5))
        assert len(plan) == 1
        assert plan[0]["type"] == "walk"

    def test_plan_has_walk_and_transition_entries(self):
        nav = _build_navigator([7, 8])
        t = FloorTransition(
            entry=_coord(50, 50, 7), exit=_coord(50, 50, 8), kind="walk"
        )
        _add_transition(nav, t)
        plan = nav.navigate_multifloor_plan(_coord(10, 10, 7), _coord(10, 10, 8))
        types = [p["type"] for p in plan]
        assert "walk"       in types
        assert "transition" in types

    def test_no_transitions_plan_returns_error_entry(self):
        nav = _build_navigator([7, 8])
        plan = nav.navigate_multifloor_plan(_coord(5, 5, 7), _coord(5, 5, 8))
        assert plan[0]["type"] == "error"
        assert "summary" in plan[0]

    def test_plan_walk_entries_have_segment_key(self):
        nav = _build_navigator([7, 8])
        t = FloorTransition(
            entry=_coord(50, 50, 7), exit=_coord(50, 50, 8), kind="walk"
        )
        _add_transition(nav, t)
        plan = nav.navigate_multifloor_plan(_coord(10, 10, 7), _coord(10, 10, 8))
        for entry in plan:
            if entry["type"] == "walk":
                assert "segment" in entry
                assert isinstance(entry["segment"], Route)

    def test_plan_transition_entries_have_transition_key(self):
        nav = _build_navigator([7, 8])
        t = FloorTransition(
            entry=_coord(50, 50, 7), exit=_coord(50, 50, 8), kind="rope"
        )
        _add_transition(nav, t)
        plan = nav.navigate_multifloor_plan(_coord(10, 10, 7), _coord(10, 10, 8))
        for entry in plan:
            if entry["type"] == "transition":
                assert "transition" in entry
                assert isinstance(entry["transition"], FloorTransition)


class TestWalkFloorPath:

    def test_stops_after_first_failed_intermediate_segment(self):
        nav = _build_navigator([7, 8, 9])
        t1 = FloorTransition(entry=_coord(50, 50, 7), exit=_coord(50, 50, 8), kind="walk")
        t2 = FloorTransition(entry=_coord(80, 80, 8), exit=_coord(80, 80, 9), kind="walk")
        floor_path = [(t1.entry, t1), (t2.entry, t2), (_coord(10, 10, 9), None)]

        success = Route(start=_coord(10, 10, 7), end=t1.entry, steps=[_coord(10, 10, 7), t1.entry], found=True)
        failure = Route(start=t1.exit, end=t2.entry, steps=[], found=False)

        with patch.object(nav, "navigate", side_effect=[success, failure, AssertionError("should stop")]):
            result = nav._walk_floor_path(_coord(10, 10, 7), floor_path)

        assert len(result) == 2
        assert result[0][0].found is True
        assert result[1][0].found is False


# ─────────────────────────────────────────────────────────────────────────────
# _floor_dijkstra internals
# ─────────────────────────────────────────────────────────────────────────────

class TestFloorDijkstra:

    def test_same_floor_returns_single_step_to_end(self):
        nav = _build_navigator([7])
        start = _coord(10, 10, 7)
        end   = _coord(20, 20, 7)
        path = nav._floor_dijkstra(start, end, max_transitions=10)
        assert path is not None
        assert len(path) == 1
        dest, trans = path[0]
        assert dest == end
        assert trans is None

    def test_returns_none_when_no_transitions(self):
        nav = _build_navigator([7, 8])
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        path = nav._floor_dijkstra(start, end, max_transitions=10)
        assert path is None

    def test_finds_path_with_one_transition(self):
        nav = _build_navigator([7, 8])
        t = FloorTransition(
            entry=_coord(50, 50, 7), exit=_coord(50, 50, 8), kind="walk"
        )
        _add_transition(nav, t)
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        path = nav._floor_dijkstra(start, end, max_transitions=10)
        assert path is not None
        # Path: [(_coord(50,50,7), t), (_coord(10,10,8), None)]
        assert len(path) == 2
        assert path[-1][1] is None        # last step has no transition
        assert path[-1][0] == end         # last step is the destination

    def test_prefers_closer_transition(self):
        """Dijkstra should choose the nearest transition entry."""
        nav = _build_navigator([7, 8])
        # Far transition
        t_far  = FloorTransition(
            entry=_coord(150, 10, 7), exit=_coord(150, 10, 8), kind="walk"
        )
        # Close transition
        t_near = FloorTransition(
            entry=_coord(15, 10, 7), exit=_coord(15, 10, 8), kind="walk"
        )
        _add_transition(nav, t_far)
        _add_transition(nav, t_near)
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        path = nav._floor_dijkstra(start, end, max_transitions=10)
        assert path is not None
        chosen_entry = path[0][0]
        # Should choose the near transition
        assert chosen_entry == t_near.entry

    def test_max_transitions_limits_hops(self):
        """With max_transitions=0, cross-floor path should be impossible."""
        nav = _build_navigator([7, 8])
        t = FloorTransition(
            entry=_coord(50, 50, 7), exit=_coord(50, 50, 8), kind="walk"
        )
        _add_transition(nav, t)
        start = _coord(10, 10, 7)
        end   = _coord(10, 10, 8)
        path = nav._floor_dijkstra(start, end, max_transitions=0)
        assert path is None


# ─────────────────────────────────────────────────────────────────────────────
# total_waypoint_count
# ─────────────────────────────────────────────────────────────────────────────

class TestTotalWaypointCount:

    def test_returns_zero_with_no_waypoints(self):
        nav = _build_navigator([7])
        nav.loader._waypoints = []
        assert nav.total_waypoint_count == 0

    def test_returns_int(self):
        nav = _build_navigator([7])
        nav.loader._waypoints = []
        assert isinstance(nav.total_waypoint_count, int)

    def test_counts_custom_waypoints(self):
        from src.models import Waypoint
        nav = _build_navigator([7])
        nav.loader._waypoints = []
        wp = Waypoint(coord=_coord(100, 100, 7), name="Alpha")
        nav._custom_waypoints.append(wp)
        assert nav.total_waypoint_count == 1

    def test_counts_builtin_and_custom(self):
        from src.models import Waypoint
        nav = _build_navigator([7])
        nav.loader._waypoints = [Waypoint(coord=_coord(50, 50, 7), name="X")]
        nav._custom_waypoints.append(Waypoint(coord=_coord(60, 60, 7), name="Y"))
        assert nav.total_waypoint_count == 2

    def test_equals_len_get_all_waypoints(self):
        from src.models import Waypoint
        nav = _build_navigator([7])
        nav.loader._waypoints = [Waypoint(coord=_coord(50, 50, 7), name="Z")]
        assert nav.total_waypoint_count == len(nav.get_all_waypoints())


# ─────────────────────────────────────────────────────────────────────────────
# has_transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestHasTransitions:

    def test_false_when_no_transitions(self):
        nav = _build_navigator([7])
        assert nav.has_transitions is False

    def test_true_after_adding_transition(self):
        nav = _build_navigator([7, 8])
        t = FloorTransition(entry=_coord(50, 50, 7), exit=_coord(50, 50, 8), kind="walk")
        _add_transition(nav, t)
        assert nav.has_transitions is True

    def test_returns_bool(self):
        nav = _build_navigator([7])
        assert isinstance(nav.has_transitions, bool)

    def test_consistent_with_len_transitions(self):
        nav = _build_navigator([7, 8])
        t = FloorTransition(entry=_coord(30, 30, 7), exit=_coord(30, 30, 8), kind="walk")
        _add_transition(nav, t)
        assert nav.has_transitions == (len(nav.transitions) > 0)

    def test_remains_true_with_multiple_transitions(self):
        nav = _build_navigator([7, 8])
        for i in range(3):
            _add_transition(nav, FloorTransition(
                entry=_coord(10 + i, 10, 7), exit=_coord(10 + i, 10, 8), kind="walk"
            ))
        assert nav.has_transitions is True


# ─────────────────────────────────────────────────────────────────────────────
# Dijkstra edge cases: visited guard (line 332) and unreachable entry (line 369)
# ─────────────────────────────────────────────────────────────────────────────

class TestDijkstraEdgeCases:

    def test_visited_guard_two_transitions_same_exit(self):
        """Line 332: two transitions share exit node — cheaper visits first, stale expensive is skipped."""
        nav = _build_navigator([7, 8, 9], map_size=200)
        # t1: far entry (high cost) — added FIRST so it gets pushed first
        t1 = FloorTransition(entry=_coord(150, 0, 7), exit=_coord(50, 50, 8), kind="walk")
        # t2: close entry (low cost) — overwrites dist, ALSO pushed → stale t1-push in heap
        t2 = FloorTransition(entry=_coord(2, 0, 7), exit=_coord(50, 50, 8), kind="walk")
        _add_transition(nav, t1)
        _add_transition(nav, t2)
        # end is on floor 9 (no transitions → path is None, but stale entry is processed)
        start = _coord(0, 0, 7)
        end = _coord(10, 10, 9)
        # _floor_dijkstra exhausts heap → returns None, but line 332 fires on stale pop
        path = nav._floor_dijkstra(start, end, max_transitions=5)
        # With no floor-9 transitions the path can't be found
        assert path is None

    def test_transition_entry_not_reachable_skipped(self):
        """Line 369: transition entry placed on an isolated non-walkable island is skipped."""
        import numpy as np
        # Create a map where row 100+ is all blocked (not walkable)
        walk = np.ones((200, 200), dtype=bool)
        walk[100:, :] = False  # bottom half not walkable

        nav = _build_navigator([7, 8])
        # Override floor 7 pathfinder with the partly-blocked map
        nav._pathfinders[7] = AStarPathfinder(walk)

        # Place transition entry in the blocked area (not reachable from start)
        t = FloorTransition(entry=_coord(150, 150, 7), exit=_coord(50, 50, 8), kind="walk")
        _add_transition(nav, t)

        start = _coord(5, 5, 7)
        end = _coord(5, 5, 8)
        # The only transition has an unreachable entry → path not found
        result = nav._floor_dijkstra(start, end, max_transitions=5)
        assert result is None
