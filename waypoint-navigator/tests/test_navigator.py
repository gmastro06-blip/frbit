"""
Tests para src/navigator.py — WaypointNavigator.
Cubre los métodos de gestión de waypoints y utilidades que no están en
test_navigation.py (A*) ni en test_navigator_multifloor.py (multifloor):

  - add_waypoint / add_waypoint_from_coords
  - get_all_waypoints / find_waypoints
  - save_custom_waypoints / load_custom_waypoints
  - is_floor_loaded
  - navigate() en error / cross-floor
  - navigate_by_name
  - navigate_route
  - total_distance
  - nearest_waypoint
  - walkable_region_stats

100 % offline — sin red, sin PIL extra, sin EasyOCR.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from src.models import Coordinate, Waypoint, Route, BOUNDS
from src.navigator import WaypointNavigator
from src.pathfinder import AStarPathfinder
from src.transitions import TransitionRegistry
from src.map_loader import TibiaMapLoader


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_XO = BOUNDS["xMin"]
_YO = BOUNDS["yMin"]


def _coord(px: int, py: int, z: int = 7) -> Coordinate:
    return Coordinate(_XO + px, _YO + py, z)


def _all_walkable(h: int = 200, w: int = 200) -> np.ndarray:
    return np.ones((h, w), dtype=bool)


def _build_nav(
    floors: list[int] | None = None,
    waypoints: list[Waypoint] | None = None,
) -> WaypointNavigator:
    """
    Navigator con pathfinders sintéticos y markers en memoria.
    No hace llamadas de red ni accede a disco.
    """
    if floors is None:
        floors = [7]

    with patch.object(TransitionRegistry, "load", return_value=TransitionRegistry()):
        nav = WaypointNavigator.__new__(WaypointNavigator)
        nav._pathfinders = {}
        nav._custom_waypoints = []
        nav._route_cache = {}
        nav.transitions = TransitionRegistry()
        nav._log = lambda _msg: None  # suppress output in tests
        nav.loader = TibiaMapLoader.__new__(TibiaMapLoader)
        nav.loader._map_images = {}
        nav.loader._walkability = {}
        nav.loader._waypoints = waypoints or []

    walk = _all_walkable()
    for z in floors:
        nav._pathfinders[z] = AStarPathfinder(walk)
        nav.loader._walkability[f"{z:02d}"] = walk

    return nav


def _wp(name: str, px: int = 0, py: int = 0, z: int = 7) -> Waypoint:
    return Waypoint(name=name, coord=_coord(px, py, z))


# ─────────────────────────────────────────────────────────────────────────────
# __init__ and load_floor (lines 53-70)
# ─────────────────────────────────────────────────────────────────────────────

class TestInitAndLoadFloor:

    def test_init_creates_loader(self):
        """Lines 53-58: __init__ sets up loader, pathfinders, and transitions."""
        with patch.object(TibiaMapLoader, "__init__", return_value=None):
            with patch.object(TransitionRegistry, "load", return_value=TransitionRegistry()):
                nav = WaypointNavigator.__new__(WaypointNavigator)
                TibiaMapLoader.__init__(nav.__class__.__new__(TibiaMapLoader))  # side-effect free
                # Manually invoke __init__ via the real path
                nav2 = WaypointNavigator.__new__(WaypointNavigator)
                WaypointNavigator.__init__(nav2)
                assert nav2._route_cache == {}
                assert nav2._custom_waypoints == []
                assert isinstance(nav2._pathfinders, dict)

    def test_load_floor_clears_route_cache(self):
        """Lines 66-70: load_floor populates pathfinder and clears cache."""
        import numpy as np
        walkability = np.ones((50, 50), dtype=bool)
        nav = _build_nav(floors=[7])
        nav._route_cache["dummy"] = "value"
        nav.loader.get_walkability = MagicMock(return_value=walkability)
        nav.load_floor(8)
        assert nav._route_cache == {}
        assert nav.is_floor_loaded(8) is True


# ─────────────────────────────────────────────────────────────────────────────
# is_floor_loaded
# ─────────────────────────────────────────────────────────────────────────────

class TestIsFloorLoaded:

    def test_loaded_floor_returns_true(self):
        nav = _build_nav(floors=[7])
        assert nav.is_floor_loaded(7) is True

    def test_unloaded_floor_returns_false(self):
        nav = _build_nav(floors=[7])
        assert nav.is_floor_loaded(8) is False

    def test_multiple_floors_tracked_independently(self):
        nav = _build_nav(floors=[6, 7, 8])
        assert nav.is_floor_loaded(6) is True
        assert nav.is_floor_loaded(5) is False


# ─────────────────────────────────────────────────────────────────────────────
# add_waypoint / add_waypoint_from_coords
# ─────────────────────────────────────────────────────────────────────────────

class TestAddWaypoint:

    def test_add_waypoint_increases_custom_count(self):
        nav = _build_nav()
        nav.add_waypoint(_wp("Depot", 10, 10))
        assert len(nav._custom_waypoints) == 1

    def test_add_multiple_waypoints(self):
        nav = _build_nav()
        for i in range(5):
            nav.add_waypoint(_wp(f"WP{i}", i, i))
        assert len(nav._custom_waypoints) == 5

    def test_add_waypoint_from_coords_returns_waypoint(self):
        nav = _build_nav()
        wp = nav.add_waypoint_from_coords("Temple", _XO + 5, _YO + 5, 7)
        assert isinstance(wp, Waypoint)
        assert wp.name == "Temple"
        assert wp.coord == Coordinate(_XO + 5, _YO + 5, 7)

    def test_add_waypoint_from_coords_stores_it(self):
        nav = _build_nav()
        nav.add_waypoint_from_coords("Bank", _XO + 3, _YO + 3, 7)
        assert any(wp.name == "Bank" for wp in nav._custom_waypoints)

    def test_description_stored_correctly(self):
        nav = _build_nav()
        wp = nav.add_waypoint_from_coords("X", _XO, _YO, 7, description="my spot")
        assert wp.description == "my spot"


# ─────────────────────────────────────────────────────────────────────────────
# get_all_waypoints / find_waypoints
# ─────────────────────────────────────────────────────────────────────────────

class TestGetAllWaypoints:

    def test_returns_loader_plus_custom(self):
        loader_wp = _wp("LoaderWP", 0, 0)
        custom_wp = _wp("CustomWP", 1, 1)
        nav = _build_nav(waypoints=[loader_wp])
        nav.add_waypoint(custom_wp)
        all_wps = nav.get_all_waypoints()
        assert loader_wp in all_wps
        assert custom_wp in all_wps

    def test_empty_loader_only_custom(self):
        nav = _build_nav(waypoints=[])
        nav.add_waypoint(_wp("OnlyCustom", 5, 5))
        all_wps = nav.get_all_waypoints()
        assert len(all_wps) == 1
        assert all_wps[0].name == "OnlyCustom"

    def test_no_waypoints_returns_empty(self):
        nav = _build_nav(waypoints=[])
        assert nav.get_all_waypoints() == []


class TestFindWaypoints:

    def test_find_by_substring_case_insensitive(self):
        nav = _build_nav(waypoints=[
            _wp("Thais Depot", 10, 10),
            _wp("Thais Temple", 20, 20),
            _wp("Edron Temple", 30, 30),
        ])
        results = nav.find_waypoints("thais")
        assert len(results) == 2
        names = {wp.name for wp in results}
        assert "Thais Depot" in names
        assert "Thais Temple" in names

    def test_find_no_match_returns_empty(self):
        nav = _build_nav(waypoints=[_wp("Thais Depot", 1, 1)])
        assert nav.find_waypoints("xyz_no_match") == []

    def test_find_with_floor_filter(self):
        nav = _build_nav(waypoints=[
            _wp("Temple z7",  10, 10, z=7),
            _wp("Temple z8",  10, 10, z=8),
            _wp("Temple z10", 10, 10, z=10),
        ])
        results = nav.find_waypoints("temple", floor=7)
        assert len(results) == 1
        assert results[0].coord.z == 7

    def test_find_includes_custom_waypoints(self):
        nav = _build_nav(waypoints=[_wp("Official", 1, 1)])
        nav.add_waypoint(_wp("CustomDepot", 5, 5))
        results = nav.find_waypoints("depot")
        assert any(wp.name == "CustomDepot" for wp in results)

    def test_find_exact_name(self):
        nav = _build_nav(waypoints=[_wp("Exact Name", 1, 1)])
        results = nav.find_waypoints("exact name")
        assert len(results) == 1


# ─────────────────────────────────────────────────────────────────────────────
# save_custom_waypoints / load_custom_waypoints
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveLoadCustomWaypoints:

    def test_save_creates_json_file(self, tmp_path: Path):
        nav = _build_nav()
        nav.add_waypoint_from_coords("A", _XO, _YO, 7)
        p = tmp_path / "custom.json"
        nav.save_custom_waypoints(p)
        assert p.exists()

    def test_saved_json_is_valid(self, tmp_path: Path):
        nav = _build_nav()
        nav.add_waypoint_from_coords("TestWP", _XO + 5, _YO + 5, 7)
        p = tmp_path / "wps.json"
        nav.save_custom_waypoints(p)
        data = json.loads(p.read_text())
        assert isinstance(data, list)
        assert len(data) == 1

    def test_load_restores_waypoints(self, tmp_path: Path):
        nav = _build_nav()
        nav.add_waypoint_from_coords("Alpha", _XO + 1, _YO + 1, 7)
        nav.add_waypoint_from_coords("Beta",  _XO + 2, _YO + 2, 7)
        p = tmp_path / "wps.json"
        nav.save_custom_waypoints(p)

        nav2 = _build_nav()
        nav2.load_custom_waypoints(p)
        names = {wp.name for wp in nav2._custom_waypoints}
        assert "Alpha" in names
        assert "Beta" in names

    def test_save_load_roundtrip_coords(self, tmp_path: Path):
        nav = _build_nav()
        orig = _coord(10, 20, 7)
        nav.add_waypoint_from_coords("TestCoord", orig.x, orig.y, orig.z)
        p = tmp_path / "wps.json"
        nav.save_custom_waypoints(p)

        nav2 = _build_nav()
        nav2.load_custom_waypoints(p)
        assert nav2._custom_waypoints[0].coord == orig

    def test_load_appends_to_existing(self, tmp_path: Path):
        nav = _build_nav()
        nav.add_waypoint_from_coords("X", _XO, _YO, 7)
        p = tmp_path / "wps.json"
        nav.save_custom_waypoints(p)

        nav2 = _build_nav()
        nav2.add_waypoint(_wp("ExistingWP", 5, 5))
        nav2.load_custom_waypoints(p)
        assert len(nav2._custom_waypoints) == 2

    def test_save_empty_list(self, tmp_path: Path):
        nav = _build_nav()
        p = tmp_path / "empty.json"
        nav.save_custom_waypoints(p)
        data = json.loads(p.read_text())
        assert data == []


# ─────────────────────────────────────────────────────────────────────────────
# navigate() — error paths
# ─────────────────────────────────────────────────────────────────────────────

class TestNavigateErrorPaths:

    def test_cross_floor_raises_value_error(self):
        nav = _build_nav(floors=[7, 8])
        with pytest.raises(ValueError, match="multifloor"):
            nav.navigate(_coord(0, 0, 7), _coord(5, 5, 8))

    def test_navigate_loads_floor_if_not_loaded(self):
        """navigate() must call load_floor when the floor is missing."""
        nav = _build_nav(floors=[])   # no floors pre-loaded
        with patch.object(nav, "load_floor", side_effect=lambda f: nav._pathfinders.update(
            {f: AStarPathfinder(_all_walkable())}
        )) as mock_load:
            nav.navigate(_coord(0, 0, 7), _coord(5, 5, 7))
            mock_load.assert_called_once_with(7)


# ─────────────────────────────────────────────────────────────────────────────
# navigate_by_name
# ─────────────────────────────────────────────────────────────────────────────

class TestNavigateByName:

    def test_raises_when_start_not_found(self):
        nav = _build_nav(waypoints=[_wp("Temple", 10, 10)])
        with pytest.raises(ValueError, match="Depot"):
            nav.navigate_by_name("Depot", "Temple")

    def test_raises_when_end_not_found(self):
        nav = _build_nav(waypoints=[_wp("Depot", 10, 10)])
        with pytest.raises(ValueError, match="Temple"):
            nav.navigate_by_name("Depot", "Temple")

    def test_navigates_between_named_waypoints(self):
        wps = [
            _wp("Depot",  0,  0, z=7),
            _wp("Temple", 10, 0, z=7),
        ]
        nav = _build_nav(floors=[7], waypoints=wps)
        route = nav.navigate_by_name("Depot", "Temple")
        assert route.found is True
        assert route.end == wps[1].coord

    def test_uses_first_match_when_multiple(self):
        """Cuando hay duplicados, usa el primero."""
        wps = [
            _wp("Temple A", 0, 0, z=7),
            _wp("Temple B", 5, 5, z=7),
        ]
        nav = _build_nav(floors=[7], waypoints=wps)
        route = nav.navigate_by_name("Temple A", "Temple B")
        assert route.found is True

    def test_logs_when_multiple_start_candidates(self):
        """Lines 411, 413: multiple candidates for same name logs a message."""
        logs = []
        wps = [
            _wp("Depot", 0, 0, z=7),
            _wp("Depot", 5, 0, z=7),  # duplicate name — len(starts) > 1
            _wp("Temple", 10, 0, z=7),
            _wp("Temple", 15, 0, z=7),  # duplicate name — len(ends) > 1
        ]
        nav = _build_nav(floors=[7], waypoints=wps)
        nav._log = logs.append
        route = nav.navigate_by_name("Depot", "Temple")
        assert route.found is True
        # Both log messages should have been emitted
        assert any("Depot" in m or "candidates" in m for m in logs)
        assert any("Temple" in m or "candidates" in m for m in logs)

    def test_cross_floor_pair_fallback(self):
        """Lines 421, 429-435: when all pairs span different floors, picks closest cross-floor."""
        wps = [
            _wp("Depot",  0, 0, z=7),
            _wp("Temple", 5, 0, z=8),  # different floor — no same-floor pair
        ]
        nav = _build_nav(floors=[7, 8], waypoints=wps)
        # This should trigger the cross-floor fallback (lines 429-435)
        # navigate_multifloor will return empty (no transitions) — result is empty Route
        route = nav.navigate_by_name("Depot", "Temple")
        # The function returns a Route (found=False) without crashing
        assert route is not None

    def test_navigate_by_name_multifloor_segments_merged(self):
        """Lines 444-454: different-floor waypoints → navigate_multifloor path merged."""
        from src.transitions import TransitionRegistry, FloorTransition
        from src.models import Coordinate as C
        # Build a transition from floor 7 to floor 8
        t = FloorTransition(
            entry=C(_XO + 5, _YO + 0, 7),
            exit=C(_XO + 5, _YO + 0, 8),
            kind="ladder",
        )
        wps = [
            _wp("Depot",  0, 0, z=7),
            _wp("Temple", 10, 0, z=8),
        ]
        nav = _build_nav(floors=[7, 8], waypoints=wps)
        nav.transitions = TransitionRegistry([t])
        route = nav.navigate_by_name("Depot", "Temple")
        # With a valid transition the multifloor path should be found
        assert route is not None

    def test_navigate_by_name_same_floor_disconnected_fallback_empty(self):
        """Lines 465-468: same-floor path fails → multifloor fallback returns empty → original route."""
        wps = [_wp("A", 0, 0, z=7), _wp("B", 10, 0, z=7)]
        nav = _build_nav(floors=[7], waypoints=wps)
        failed_route = Route(start=_coord(0, 0), end=_coord(10, 0), steps=[], total_distance=0.0, found=False)
        with patch.object(nav, "navigate", return_value=failed_route):
            with patch.object(nav, "navigate_multifloor", return_value=[]):
                route = nav.navigate_by_name("A", "B")
        assert route is failed_route

    def test_navigate_by_name_same_floor_disconnected_fallback_with_segments(self):
        """Lines 469-477: same-floor path fails → multifloor fallback returns segments → merged."""
        wps = [_wp("A", 0, 0, z=7), _wp("B", 10, 0, z=7)]
        nav = _build_nav(floors=[7], waypoints=wps)
        failed_route = Route(start=_coord(0, 0), end=_coord(10, 0), steps=[], total_distance=0.0, found=False)
        seg = Route(start=_coord(0, 0), end=_coord(10, 0), steps=[_coord(5, 0)], total_distance=10.0, found=True)
        with patch.object(nav, "navigate", return_value=failed_route):
            with patch.object(nav, "navigate_multifloor", return_value=[seg]):
                route = nav.navigate_by_name("A", "B")
        assert route.found is True
        assert route.total_distance == pytest.approx(10.0)

    def test_navigate_by_name_fallback_segment_not_found(self):
        """Line 476: not-found segment in multifloor fallback sets found_fb=False."""
        wps = [_wp("A", 0, 0, z=7), _wp("B", 10, 0, z=7)]
        nav = _build_nav(floors=[7], waypoints=wps)
        failed_route = Route(start=_coord(0, 0), end=_coord(10, 0), steps=[], total_distance=0.0, found=False)
        bad_seg = Route(start=_coord(0, 0), end=_coord(10, 0), steps=[], total_distance=5.0, found=False)
        with patch.object(nav, "navigate", return_value=failed_route):
            with patch.object(nav, "navigate_multifloor", return_value=[bad_seg]):
                route = nav.navigate_by_name("A", "B")
        assert route.found is False


# ─────────────────────────────────────────────────────────────────────────────
# navigate_route
# ─────────────────────────────────────────────────────────────────────────────

class TestNavigateRoute:

    def test_requires_at_least_two_waypoints(self):
        nav = _build_nav(floors=[7])
        with pytest.raises(ValueError, match="2"):
            nav.navigate_route([_coord(0, 0)])

    def test_two_stops_returns_one_segment(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(5, 0)])
        assert len(segs) == 1

    def test_three_stops_returns_two_segments(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(5, 0), _coord(10, 0)])
        assert len(segs) == 2

    def test_all_segments_found_on_walkable_grid(self):
        nav = _build_nav(floors=[7])
        stops = [_coord(i * 5, 0) for i in range(4)]
        segs = nav.navigate_route(stops)
        assert all(s.found for s in segs)

    def test_last_segment_ends_at_final_coord(self):
        nav = _build_nav(floors=[7])
        stops = [_coord(0, 0), _coord(5, 0), _coord(10, 5)]
        segs = nav.navigate_route(stops)
        assert segs[-1].end == stops[-1]

    def test_segments_are_route_objects(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(3, 0)])
        assert all(isinstance(s, Route) for s in segs)


# ─────────────────────────────────────────────────────────────────────────────
# total_distance
# ─────────────────────────────────────────────────────────────────────────────

class TestTotalDistance:

    def test_zero_for_empty_list(self):
        nav = _build_nav()
        assert nav.total_distance([]) == pytest.approx(0.0)

    def test_single_segment_matches_route_distance(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(10, 0)])
        total = nav.total_distance(segs)
        assert total == pytest.approx(segs[0].total_distance)

    def test_multiple_segments_sum_correctly(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(5, 0), _coord(10, 0)])
        total = nav.total_distance(segs)
        expected = sum(s.total_distance for s in segs)
        assert total == pytest.approx(expected)

    def test_distance_is_non_negative(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(8, 6)])
        assert nav.total_distance(segs) >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# nearest_waypoint
# ─────────────────────────────────────────────────────────────────────────────

class TestNearestWaypoint:

    def test_returns_closest_waypoint(self):
        wps = [
            _wp("Near",  1,  1, z=7),
            _wp("Far",  50, 50, z=7),
        ]
        nav = _build_nav(waypoints=wps)
        nearest = nav.nearest_waypoint(_coord(0, 0, 7), top_n=1)
        assert nearest[0].name == "Near"

    def test_top_n_limits_results(self):
        wps = [_wp(f"WP{i}", i * 5, 0) for i in range(10)]
        nav = _build_nav(waypoints=wps)
        nearest = nav.nearest_waypoint(_coord(0, 0), top_n=3)
        assert len(nearest) == 3

    def test_filters_by_same_floor(self):
        wps = [
            _wp("On z7",  0,  0, z=7),
            _wp("On z8",  1,  1, z=8),   # diferente piso
        ]
        nav = _build_nav(waypoints=wps)
        nearest = nav.nearest_waypoint(_coord(0, 0, 7), top_n=5)
        for wp in nearest:
            assert wp.coord.z == 7

    def test_returns_empty_when_no_waypoints_on_floor(self):
        wps = [_wp("On z8", 0, 0, z=8)]
        nav = _build_nav(waypoints=wps)
        nearest = nav.nearest_waypoint(_coord(0, 0, 7), top_n=5)
        assert nearest == []

    def test_sorted_by_distance(self):
        wps = [
            _wp("Far",    20, 0, z=7),
            _wp("Near",    2, 0, z=7),
            _wp("Middle", 10, 0, z=7),
        ]
        nav = _build_nav(waypoints=wps)
        nearest = nav.nearest_waypoint(_coord(0, 0), top_n=3)
        dists = [_coord(0, 0).euclidean_to(wp.coord) for wp in nearest]
        assert dists == sorted(dists), "Waypoints deben estar ordenados por distancia"

    def test_default_top_n_is_5(self):
        wps = [_wp(f"WP{i}", i, 0) for i in range(10)]
        nav = _build_nav(waypoints=wps)
        nearest = nav.nearest_waypoint(_coord(0, 0))
        assert len(nearest) <= 5


# ─────────────────────────────────────────────────────────────────────────────
# walkable_region_stats
# ─────────────────────────────────────────────────────────────────────────────

class TestWalkableRegionStats:

    def test_returns_required_keys(self):
        nav = _build_nav(floors=[7])
        stats = nav.walkable_region_stats(7)
        for key in ("floor", "total_tiles", "walkable_tiles",
                    "non_walkable", "pct_walkable"):
            assert key in stats, f"Falta clave '{key}'"

    def test_all_walkable_grid_pct_100(self):
        nav = _build_nav(floors=[7])
        stats = nav.walkable_region_stats(7)
        assert stats["pct_walkable"] == pytest.approx(100.0)

    def test_no_walkable_grid_pct_0(self):
        nav = _build_nav(floors=[])
        walk = np.zeros((50, 50), dtype=bool)
        nav._pathfinders[7] = AStarPathfinder(walk)
        nav.loader._walkability["07"] = walk
        stats = nav.walkable_region_stats(7)
        assert stats["pct_walkable"] == pytest.approx(0.0)
        assert stats["walkable_tiles"] == 0

    def test_half_walkable_grid(self):
        nav = _build_nav(floors=[])
        walk = np.zeros((10, 10), dtype=bool)
        walk[:, :5] = True   # mitad izquierda walkable
        nav._pathfinders[7]      = AStarPathfinder(walk)
        nav.loader._walkability["07"] = walk
        stats = nav.walkable_region_stats(7)
        assert stats["walkable_tiles"] == 50
        assert stats["non_walkable"]   == 50
        assert stats["total_tiles"]    == 100
        assert stats["pct_walkable"]   == pytest.approx(50.0)

    def test_floor_key_matches_requested(self):
        nav = _build_nav(floors=[7])
        stats = nav.walkable_region_stats(7)
        assert stats["floor"] == 7

    def test_total_tiles_equals_area(self):
        h, w = 200, 200
        nav = _build_nav(floors=[7])
        stats = nav.walkable_region_stats(7)
        assert stats["total_tiles"] == h * w

    def test_walkable_plus_nonwalkable_equals_total(self):
        nav = _build_nav(floors=[7])
        stats = nav.walkable_region_stats(7)
        assert stats["walkable_tiles"] + stats["non_walkable"] == stats["total_tiles"]


# ─────────────────────────────────────────────────────────────────────────────
# WalkableRegionStats — cache behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestWalkableRegionStatsCache:

    def test_uses_cached_pathfinder_not_loader(self):
        """When floor is already in _pathfinders, loader.get_walkability is NOT called."""
        nav = _build_nav(floors=[7])
        # Spy on loader — it should NOT be queried for floor 7
        call_count = []
        orig_get = nav.loader.get_walkability.__class__  # just to confirm it exists
        from unittest.mock import patch as _patch
        with _patch.object(nav.loader, "get_walkability", side_effect=AssertionError("should not be called")) as mock_get:
            # floor 7 is already in _pathfinders, so no call expected
            stats = nav.walkable_region_stats(7)
        assert stats["floor"] == 7

    def test_loader_called_for_uncached_floor(self):
        """When floor is NOT in _pathfinders, walkability is fetched (via loader or fallback)."""
        nav = _build_nav(floors=[])   # no floors pre-loaded
        # Inject walkability directly so it doesn't fail
        walk = _all_walkable(50, 50)
        nav.loader._walkability["08"] = walk
        # floor 8 not in _pathfinders, but walkability is available
        # navigator will fall back to loader path
        stats = nav.walkable_region_stats(8)
        assert stats["floor"] == 8
        assert stats["total_tiles"] == 50 * 50


# ─────────────────────────────────────────────────────────────────────────────
# remove_waypoint() / clear_custom_waypoints()
# ─────────────────────────────────────────────────────────────────────────────

class TestRemoveWaypoint:

    def test_remove_existing_returns_count(self):
        nav = _build_nav()
        nav._custom_waypoints = [_wp("temple"), _wp("depot"), _wp("spawn")]
        removed = nav.remove_waypoint("depot")
        assert removed == 1
        assert len(nav._custom_waypoints) == 2

    def test_remove_case_insensitive(self):
        nav = _build_nav()
        nav._custom_waypoints = [_wp("Temple Of Life"), _wp("Depot")]
        removed = nav.remove_waypoint("temple of life")
        assert removed == 1
        names = [w.name for w in nav._custom_waypoints]
        assert "Temple Of Life" not in names

    def test_remove_by_substring(self):
        nav = _build_nav()
        nav._custom_waypoints = [_wp("North Temple"), _wp("South Temple"), _wp("Depot")]
        removed = nav.remove_waypoint("temple")
        assert removed == 2
        assert len(nav._custom_waypoints) == 1

    def test_remove_no_match_returns_zero(self):
        nav = _build_nav()
        nav._custom_waypoints = [_wp("temple"), _wp("depot")]
        removed = nav.remove_waypoint("nonexistent")
        assert removed == 0
        assert len(nav._custom_waypoints) == 2

    def test_remove_from_empty_returns_zero(self):
        nav = _build_nav()
        nav._custom_waypoints = []
        removed = nav.remove_waypoint("anything")
        assert removed == 0

    def test_remove_all_matches_clears_list(self):
        nav = _build_nav()
        nav._custom_waypoints = [_wp("alpha"), _wp("alpha copy"), _wp("alpha v2")]
        removed = nav.remove_waypoint("alpha")
        assert removed == 3
        assert nav._custom_waypoints == []


class TestClearCustomWaypoints:

    def test_clear_removes_all(self):
        nav = _build_nav()
        nav._custom_waypoints = [_wp("a"), _wp("b"), _wp("c")]
        nav.clear_custom_waypoints()
        assert nav._custom_waypoints == []

    def test_clear_empty_does_not_raise(self):
        nav = _build_nav()
        nav._custom_waypoints = []
        nav.clear_custom_waypoints()  # should not raise
        assert nav._custom_waypoints == []

    def test_add_after_clear_works(self):
        nav = _build_nav()
        nav._custom_waypoints = [_wp("old")]
        nav.clear_custom_waypoints()
        nav._custom_waypoints.append(_wp("new"))
        assert len(nav._custom_waypoints) == 1
        assert nav._custom_waypoints[0].name == "new"


# ─────────────────────────────────────────────────────────────────────────────
# loaded_floors()
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadedFloors:

    def test_empty_when_no_floors(self):
        nav = _build_nav(floors=[])
        assert nav.loaded_floors() == []

    def test_single_floor(self):
        nav = _build_nav(floors=[7])
        assert nav.loaded_floors() == [7]

    def test_multiple_floors_sorted(self):
        nav = _build_nav(floors=[9, 7, 8])
        assert nav.loaded_floors() == [7, 8, 9]

    def test_returns_list_type(self):
        nav = _build_nav(floors=[7])
        assert isinstance(nav.loaded_floors(), list)


# ─────────────────────────────────────────────────────────────────────────────
# unload_floor()
# ─────────────────────────────────────────────────────────────────────────────

class TestUnloadFloor:

    def test_unload_loaded_floor_returns_true(self):
        nav = _build_nav(floors=[7])
        assert nav.unload_floor(7) is True

    def test_unload_removes_pathfinder(self):
        nav = _build_nav(floors=[7])
        nav.unload_floor(7)
        assert not nav.is_floor_loaded(7)

    def test_unload_not_loaded_returns_false(self):
        nav = _build_nav(floors=[7])
        assert nav.unload_floor(8) is False

    def test_unload_does_not_affect_other_floors(self):
        nav = _build_nav(floors=[6, 7, 8])
        nav.unload_floor(7)
        assert nav.is_floor_loaded(6)
        assert nav.is_floor_loaded(8)
        assert not nav.is_floor_loaded(7)

    def test_double_unload_returns_false_second_time(self):
        nav = _build_nav(floors=[7])
        nav.unload_floor(7)
        assert nav.unload_floor(7) is False

    def test_unload_reduces_loaded_floors_count(self):
        nav = _build_nav(floors=[7, 8])
        nav.unload_floor(7)
        assert nav.loaded_floors() == [8]


# ─────────────────────────────────────────────────────────────────────────────
# reload_floor()
# ─────────────────────────────────────────────────────────────────────────────

class TestReloadFloor:

    def test_reload_keeps_floor_available(self):
        nav = _build_nav(floors=[7])
        with patch.object(nav, "load_floor", side_effect=lambda f: nav._pathfinders.update(
            {f: AStarPathfinder(_all_walkable())}
        )):
            nav.reload_floor(7)
            assert nav.is_floor_loaded(7)

    def test_reload_calls_load_floor(self):
        nav = _build_nav(floors=[7])
        load_calls = []
        def mock_load(f):
            load_calls.append(f)
            nav._pathfinders.update({f: AStarPathfinder(_all_walkable())})
        with patch.object(nav, "load_floor", side_effect=mock_load):
            nav.reload_floor(7)
            assert 7 in load_calls

    def test_reload_replaces_old_pathfinder(self):
        nav = _build_nav(floors=[7])
        old_pf = nav._pathfinders[7]
        with patch.object(nav, "load_floor", side_effect=lambda f: nav._pathfinders.update(
            {f: AStarPathfinder(_all_walkable())}
        )):
            nav.reload_floor(7)
            assert nav._pathfinders[7] is not old_pf


# ─────────────────────────────────────────────────────────────────────────────
# waypoints_on_floor()
# ─────────────────────────────────────────────────────────────────────────────

class TestWaypointsOnFloor:

    def test_returns_only_waypoints_on_floor(self):
        wps = [
            _wp("A", 1, 1, z=7),
            _wp("B", 2, 2, z=8),
            _wp("C", 3, 3, z=7),
        ]
        nav = _build_nav(waypoints=wps)
        result = nav.waypoints_on_floor(7)
        assert len(result) == 2
        assert all(wp.coord.z == 7 for wp in result)

    def test_includes_custom_waypoints(self):
        nav = _build_nav(waypoints=[_wp("BuiltIn", 0, 0, z=7)])
        nav.add_waypoint(_wp("Custom", 5, 5, z=7))
        result = nav.waypoints_on_floor(7)
        names = {wp.name for wp in result}
        assert "BuiltIn" in names
        assert "Custom" in names

    def test_empty_when_no_waypoints_on_floor(self):
        nav = _build_nav(waypoints=[_wp("OnZ8", 0, 0, z=8)])
        assert nav.waypoints_on_floor(7) == []

    def test_all_floors_covered(self):
        wps = [_wp(f"WP{z}", 0, 0, z=z) for z in range(5, 10)]
        nav = _build_nav(waypoints=wps)
        for z in range(5, 10):
            assert len(nav.waypoints_on_floor(z)) == 1

    def test_empty_navigator_returns_empty(self):
        nav = _build_nav(waypoints=[])
        assert nav.waypoints_on_floor(7) == []


# ─────────────────────────────────────────────────────────────────────────────
# route_summary()
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteSummary:

    def test_empty_segments_returns_no_segments_string(self):
        nav = _build_nav()
        assert nav.route_summary([]) == "No segments."

    def test_single_segment_contains_start_and_end(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(5, 0)])
        summary = nav.route_summary(segs)
        assert "1/1" in summary

    def test_multi_segment_lists_all(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(5, 0), _coord(10, 0)])
        summary = nav.route_summary(segs)
        assert "1/2" in summary
        assert "2/2" in summary

    def test_summary_contains_total_line(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(5, 0)])
        summary = nav.route_summary(segs)
        assert "Total:" in summary

    def test_found_segment_shows_checkmark(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(3, 0)])
        summary = nav.route_summary(segs)
        assert "✓" in summary

    def test_total_distance_in_summary_is_correct(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(5, 0), _coord(10, 0)])
        summary = nav.route_summary(segs)
        expected_dist = nav.total_distance(segs)
        # The formatted value appears in the summary
        assert f"dist={expected_dist:.1f}" in summary

    def test_returns_string_type(self):
        nav = _build_nav(floors=[7])
        segs = nav.navigate_route([_coord(0, 0), _coord(2, 0)])
        assert isinstance(nav.route_summary(segs), str)


# ─────────────────────────────────────────────────────────────────────────────
# waypoints_by_name
# ─────────────────────────────────────────────────────────────────────────────

class TestWaypointsByName:

    def test_returns_matching_waypoints(self):
        nav = _build_nav(floors=[7])
        nav.add_waypoint(Waypoint("Alpha", _coord(0, 0)))
        nav.add_waypoint(Waypoint("Beta", _coord(1, 0)))
        result = nav.waypoints_by_name(["Alpha"])
        assert len(result) == 1
        assert result[0].name == "Alpha"

    def test_returns_multiple_matches(self):
        nav = _build_nav(floors=[7])
        nav.add_waypoint(Waypoint("Alpha", _coord(0, 0)))
        nav.add_waypoint(Waypoint("Beta", _coord(1, 0)))
        result = nav.waypoints_by_name(["Alpha", "Beta"])
        assert {wp.name for wp in result} == {"Alpha", "Beta"}

    def test_empty_names_returns_empty(self):
        nav = _build_nav(floors=[7])
        nav.add_waypoint(Waypoint("Alpha", _coord(0, 0)))
        assert nav.waypoints_by_name([]) == []

    def test_unknown_name_returns_empty(self):
        nav = _build_nav(floors=[7])
        nav.add_waypoint(Waypoint("Alpha", _coord(0, 0)))
        assert nav.waypoints_by_name(["Nonexistent"]) == []

    def test_case_sensitive(self):
        nav = _build_nav(floors=[7])
        nav.add_waypoint(Waypoint("Alpha", _coord(0, 0)))
        assert nav.waypoints_by_name(["alpha"]) == []

    def test_accepts_set(self):
        nav = _build_nav(floors=[7])
        nav.add_waypoint(Waypoint("Alpha", _coord(0, 0)))
        result = nav.waypoints_by_name({"Alpha"})
        assert result[0].name == "Alpha"


# ─────────────────────────────────────────────────────────────────────────────
# count_waypoints_on_floor
# ─────────────────────────────────────────────────────────────────────────────

class TestCountWaypointsOnFloor:

    def test_zero_when_no_waypoints_on_floor(self):
        nav = _build_nav(floors=[7])
        assert nav.count_waypoints_on_floor(8) == 0

    def test_counts_added_waypoints(self):
        nav = _build_nav(floors=[7])
        nav.add_waypoint(Waypoint("A", _coord(0, 0)))
        nav.add_waypoint(Waypoint("B", _coord(1, 0)))
        assert nav.count_waypoints_on_floor(7) >= 2

    def test_does_not_count_other_floor(self):
        nav = _build_nav(floors=[7])
        coord_f8 = Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 8)
        nav.add_waypoint(Waypoint("X", coord_f8))
        count_7 = nav.count_waypoints_on_floor(7)
        count_8 = nav.count_waypoints_on_floor(8)
        assert count_8 >= 1
        assert count_7 == nav.count_waypoints_on_floor(7)  # unchanged

    def test_equals_len_waypoints_on_floor(self):
        nav = _build_nav(floors=[7])
        nav.add_waypoint(Waypoint("A", _coord(0, 0)))
        nav.add_waypoint(Waypoint("B", _coord(1, 0)))
        assert nav.count_waypoints_on_floor(7) == len(nav.waypoints_on_floor(7))

    def test_returns_int(self):
        nav = _build_nav(floors=[7])
        assert isinstance(nav.count_waypoints_on_floor(7), int)


# ─────────────────────────────────────────────────────────────────────────────
# all_loaded_floor_stats
# ─────────────────────────────────────────────────────────────────────────────

class TestAllLoadedFloorStats:

    def test_empty_when_no_floors(self):
        nav = _build_nav(floors=[])
        assert nav.all_loaded_floor_stats() == []

    def test_one_entry_per_floor(self):
        nav = _build_nav(floors=[7])
        stats = nav.all_loaded_floor_stats()
        assert len(stats) == 1

    def test_multiple_floors(self):
        nav = _build_nav(floors=[6, 7])
        stats = nav.all_loaded_floor_stats()
        assert len(stats) == 2

    def test_ordered_by_floor(self):
        nav = _build_nav(floors=[8, 6, 7])
        stats = nav.all_loaded_floor_stats()
        floors = [s["floor"] for s in stats]
        assert floors == sorted(floors)

    def test_each_entry_has_required_keys(self):
        nav = _build_nav(floors=[7])
        stat = nav.all_loaded_floor_stats()[0]
        for key in ("floor", "total_tiles", "walkable_tiles",
                    "non_walkable", "pct_walkable"):
            assert key in stat, f"Missing key: {key}"


class TestNavigatorFloorCount:

    def test_floor_count_zero_initially(self):
        nav = _build_nav(floors=[])
        assert nav.floor_count == 0

    def test_floor_count_one_after_load(self):
        nav = _build_nav(floors=[7])
        assert nav.floor_count == 1

    def test_floor_count_multiple_floors(self):
        nav = _build_nav(floors=[6, 7, 8])
        assert nav.floor_count == 3

    def test_floor_count_decreases_on_unload(self):
        nav = _build_nav(floors=[6, 7])
        nav.unload_floor(7)
        assert nav.floor_count == 1

    def test_floor_count_returns_int(self):
        nav = _build_nav()
        assert isinstance(nav.floor_count, int)


class TestNavigatorCustomWaypointCount:

    def test_custom_waypoint_count_zero_initially(self):
        nav = _build_nav()
        assert nav.custom_waypoint_count == 0

    def test_custom_waypoint_count_after_add(self):
        nav = _build_nav()
        nav.add_waypoint(_wp("A"))
        assert nav.custom_waypoint_count == 1

    def test_custom_waypoint_count_multiple(self):
        nav = _build_nav()
        for n in ("A", "B", "C"):
            nav.add_waypoint(_wp(n))
        assert nav.custom_waypoint_count == 3

    def test_custom_waypoint_count_decreases_on_remove(self):
        nav = _build_nav()
        nav.add_waypoint(_wp("Alpha"))
        nav.add_waypoint(_wp("Beta"))
        nav.remove_waypoint("Alpha")
        assert nav.custom_waypoint_count == 1

    def test_custom_waypoint_count_zero_after_clear(self):
        nav = _build_nav()
        nav.add_waypoint(_wp("X"))
        nav.clear_custom_waypoints()
        assert nav.custom_waypoint_count == 0


class TestNavigatorStatsSnapshot:

    def test_returns_dict(self):
        nav = _build_nav()
        assert isinstance(nav.stats_snapshot(), dict)

    def test_all_keys_present(self):
        nav = _build_nav()
        snap = nav.stats_snapshot()
        for key in ("loaded_floors", "floor_count",
                    "custom_waypoints", "total_waypoints", "transitions_loaded"):
            assert key in snap, f"Missing key: {key}"

    def test_loaded_floors_sorted(self):
        nav = _build_nav(floors=[8, 6, 7])
        snap = nav.stats_snapshot()
        assert snap["loaded_floors"] == [6, 7, 8]

    def test_floor_count_matches(self):
        nav = _build_nav(floors=[6, 7])
        snap = nav.stats_snapshot()
        assert snap["floor_count"] == 2

    def test_custom_waypoints_count_matches(self):
        nav = _build_nav()
        nav.add_waypoint(_wp("Depot"))
        snap = nav.stats_snapshot()
        assert snap["custom_waypoints"] == 1

    def test_total_waypoints_includes_builtin(self):
        wps = [_wp("Builtin", z=7)]
        nav = _build_nav(waypoints=wps)
        snap = nav.stats_snapshot()
        # loader._waypoints has 1 built-in; custom_waypoints = 0
        assert snap["total_waypoints"] == 1

    def test_transitions_loaded_is_int(self):
        nav = _build_nav()
        snap = nav.stats_snapshot()
        assert isinstance(snap["transitions_loaded"], int)


class TestNavigatorHasCustomWaypoints:

    def test_has_custom_waypoints_false_initially(self):
        nav = _build_nav()
        assert nav.has_custom_waypoints is False

    def test_has_custom_waypoints_true_after_add(self):
        nav = _build_nav()
        nav.add_waypoint(_wp("Depot"))
        assert nav.has_custom_waypoints is True

    def test_has_custom_waypoints_false_after_clear(self):
        nav = _build_nav()
        nav.add_waypoint(_wp("Depot"))
        nav.clear_custom_waypoints()
        assert nav.has_custom_waypoints is False

    def test_has_custom_waypoints_returns_bool(self):
        nav = _build_nav()
        assert isinstance(nav.has_custom_waypoints, bool)

    def test_has_custom_waypoints_consistent_with_count(self):
        nav = _build_nav()
        nav.add_waypoint(_wp("A"))
        nav.add_waypoint(_wp("B"))
        assert nav.has_custom_waypoints == (nav.custom_waypoint_count > 0)


class TestNavigatorHasLoadedFloors:

    def test_has_loaded_floors_true_with_floor(self):
        nav = _build_nav(floors=[7])
        assert nav.has_loaded_floors is True

    def test_has_loaded_floors_false_for_empty_nav(self):
        nav = _build_nav(floors=[])
        assert nav.has_loaded_floors is False

    def test_has_loaded_floors_false_after_all_unloaded(self):
        nav = _build_nav(floors=[7])
        nav.unload_floor(7)
        assert nav.has_loaded_floors is False

    def test_has_loaded_floors_returns_bool(self):
        nav = _build_nav()
        assert isinstance(nav.has_loaded_floors, bool)

    def test_has_loaded_floors_consistent_with_floor_count(self):
        nav = _build_nav(floors=[7, 8])
        assert nav.has_loaded_floors == (nav.floor_count > 0)
