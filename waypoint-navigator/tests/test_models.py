"""
Tests para src/models.py
Corre sin OBS, Tibia ni archivos de mapa.
"""
from __future__ import annotations

import math
import pytest

from src.models import Coordinate, Waypoint, Route, BOUNDS, GROUND_FLOOR, FloorTransition


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinate:
    def test_default_z(self):
        c = Coordinate(32000, 31500)
        assert c.z == GROUND_FLOOR

    def test_validate_ok(self):
        Coordinate(32000, 31500, 7).validate()  # sin excepción

    def test_validate_x_out_of_range(self):
        with pytest.raises(ValueError, match="x="):
            Coordinate(x=99999, y=31500, z=7).validate()

    def test_validate_y_out_of_range(self):
        with pytest.raises(ValueError, match="y="):
            Coordinate(x=32000, y=99999, z=7).validate()

    def test_validate_z_out_of_range(self):
        with pytest.raises(ValueError, match="z="):
            Coordinate(x=32000, y=31500, z=16).validate()

    def test_distance_to_same_point(self):
        c = Coordinate(32000, 31500, 7)
        assert c.distance_to(c) == 0

    def test_distance_to_cardinal(self):
        a = Coordinate(32000, 31500, 7)
        b = Coordinate(32005, 31500, 7)
        assert a.distance_to(b) == 5  # Chebyshev

    def test_distance_to_diagonal(self):
        a = Coordinate(32000, 31500, 7)
        b = Coordinate(32003, 31004, 7)  # dx=3, dy=496 → Chebyshev = 496
        assert a.distance_to(b) == max(3, abs(31500 - 31004))

    def test_distance_different_floor_raises(self):
        a = Coordinate(32000, 31500, 7)
        b = Coordinate(32000, 31500, 8)
        with pytest.raises(ValueError):
            a.distance_to(b)

    def test_euclidean_to(self):
        a = Coordinate(0 + BOUNDS["xMin"], 0 + BOUNDS["yMin"], 7)
        b = Coordinate(3 + BOUNDS["xMin"], 4 + BOUNDS["yMin"], 7)
        assert abs(a.euclidean_to(b) - 5.0) < 1e-9

    def test_to_pixel(self):
        c = Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 20, 7)
        px, py = c.to_pixel()
        assert px == 10
        assert py == 20

    def test_from_pixel_roundtrip(self):
        c = Coordinate(32100, 31200, 7)
        px, py = c.to_pixel()
        c2 = Coordinate.from_pixel(px, py, 7)
        assert c == c2

    def test_from_dict(self):
        d = {"x": 32100, "y": 31200, "z": 7}
        c = Coordinate.from_dict(d)
        assert c.x == 32100 and c.y == 31200 and c.z == 7

    def test_from_dict_default_z(self):
        d = {"x": 32100, "y": 31200}
        c = Coordinate.from_dict(d)
        assert c.z == GROUND_FLOOR

    def test_to_dict(self):
        c = Coordinate(32100, 31200, 7)
        d = c.to_dict()
        assert d == {"x": 32100, "y": 31200, "z": 7}

    def test_frozen(self):
        c = Coordinate(32100, 31200, 7)
        with pytest.raises((AttributeError, TypeError)):
            c.x = 99999  # type: ignore[misc]

    def test_ordering(self):
        a = Coordinate(32000, 31000, 7)
        b = Coordinate(32001, 31000, 7)
        assert a < b


# ─────────────────────────────────────────────────────────────────────────────
# Route
# ─────────────────────────────────────────────────────────────────────────────

class TestRoute:
    def _make_route(self, n: int = 10) -> Route:
        start = Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7)
        steps = [Coordinate(BOUNDS["xMin"] + i, BOUNDS["yMin"], 7) for i in range(n)]
        end   = steps[-1]
        return Route(start=start, end=end, steps=steps, found=True, total_distance=float(n - 1))

    def test_summary_contains_steps(self):
        r = self._make_route(10)
        s = r.summary()
        assert "10" in s

    def test_not_found_route(self):
        start = Coordinate(32000, 31000, 7)
        end   = Coordinate(32999, 31999, 7)
        r = Route(start=start, end=end, steps=[], found=False)
        assert r.found is False
        summary = r.summary()
        assert summary  # no vacío


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate.manhattan_to()
# ─────────────────────────────────────────────────────────────────────────────

class TestManhattanTo:

    def test_horizontal(self):
        a = Coordinate(32000, 31000, 7)
        b = Coordinate(32005, 31000, 7)
        assert a.manhattan_to(b) == 5

    def test_vertical(self):
        a = Coordinate(32000, 31000, 7)
        b = Coordinate(32000, 31008, 7)
        assert a.manhattan_to(b) == 8

    def test_diagonal(self):
        a = Coordinate(32000, 31000, 7)
        b = Coordinate(32003, 31004, 7)
        assert a.manhattan_to(b) == 7   # abs(3)+abs(4)

    def test_same_coord(self):
        a = Coordinate(32000, 31000, 7)
        assert a.manhattan_to(a) == 0

    def test_negative_deltas_abs(self):
        a = Coordinate(32010, 31010, 7)
        b = Coordinate(32000, 31000, 7)
        assert a.manhattan_to(b) == 20  # abs(-10)+abs(-10)

    def test_different_floors_still_computes_xy(self):
        """manhattan_to ignores z, only measures XY distance."""
        a = Coordinate(32000, 31000, 7)
        b = Coordinate(32003, 31004, 8)
        assert a.manhattan_to(b) == 7

    def test_symmetric(self):
        a = Coordinate(32000, 31000, 7)
        b = Coordinate(32003, 31004, 7)
        assert a.manhattan_to(b) == b.manhattan_to(a)


# ─────────────────────────────────────────────────────────────────────────────
# Route.step_count
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteStepCount:

    def _route(self, n: int, found: bool = True) -> Route:
        start = Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7)
        steps = [Coordinate(BOUNDS["xMin"] + i, BOUNDS["yMin"], 7) for i in range(n)]
        end   = steps[-1] if steps else start
        return Route(start=start, end=end, steps=steps, found=found, total_distance=float(max(n - 1, 0)))

    def test_step_count_matches_len_steps(self):
        r = self._route(10)
        assert r.step_count == 10

    def test_step_count_single_step(self):
        r = self._route(1)
        assert r.step_count == 1

    def test_not_found_empty_steps(self):
        start = Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7)
        r = Route(start=start, end=start, steps=[], found=False)
        assert r.step_count == 0

    def test_step_count_consistent_with_steps_list(self):
        r = self._route(7)
        assert r.step_count == len(r.steps)


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate.clamp()
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateClamp:

    def test_in_bounds_coord_unchanged(self):
        c = Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 10, 7)
        assert c.clamp() == c

    def test_x_below_min_clamped(self):
        c = Coordinate(BOUNDS["xMin"] - 100, BOUNDS["yMin"], 7)
        clamped = c.clamp()
        assert clamped.x == BOUNDS["xMin"]
        assert clamped.y == BOUNDS["yMin"]

    def test_x_above_max_clamped(self):
        c = Coordinate(BOUNDS["xMax"] + 500, BOUNDS["yMin"] + 1, 7)
        clamped = c.clamp()
        assert clamped.x == BOUNDS["xMax"]

    def test_y_below_min_clamped(self):
        c = Coordinate(BOUNDS["xMin"] + 1, BOUNDS["yMin"] - 200, 7)
        clamped = c.clamp()
        assert clamped.y == BOUNDS["yMin"]

    def test_y_above_max_clamped(self):
        c = Coordinate(BOUNDS["xMin"] + 1, BOUNDS["yMax"] + 999, 7)
        clamped = c.clamp()
        assert clamped.y == BOUNDS["yMax"]

    def test_z_below_zero_clamped(self):
        c = Coordinate(BOUNDS["xMin"] + 1, BOUNDS["yMin"] + 1, -5)
        clamped = c.clamp()
        assert clamped.z == BOUNDS["zMin"]

    def test_z_above_15_clamped(self):
        c = Coordinate(BOUNDS["xMin"] + 1, BOUNDS["yMin"] + 1, 20)
        clamped = c.clamp()
        assert clamped.z == BOUNDS["zMax"]

    def test_all_three_axes_clamped_simultaneously(self):
        c = Coordinate(BOUNDS["xMin"] - 1, BOUNDS["yMax"] + 1, 99)
        clamped = c.clamp()
        assert clamped.x == BOUNDS["xMin"]
        assert clamped.y == BOUNDS["yMax"]
        assert clamped.z == BOUNDS["zMax"]

    def test_clamp_is_idempotent(self):
        c = Coordinate(BOUNDS["xMin"] - 1, BOUNDS["yMin"] - 1, -1)
        assert c.clamp() == c.clamp().clamp()


# ─────────────────────────────────────────────────────────────────────────────
# Waypoint.__lt__  (sortable)
# ─────────────────────────────────────────────────────────────────────────────

_BASE = Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7)


class TestWaypointSortable:

    def test_lt_alphabetical_order(self):
        a = Waypoint(name="Alpha", coord=_BASE)
        b = Waypoint(name="Beta", coord=_BASE)
        assert a < b

    def test_lt_reversed(self):
        a = Waypoint(name="Zebra", coord=_BASE)
        b = Waypoint(name="Aardvark", coord=_BASE)
        assert not (a < b)

    def test_sorted_list(self):
        names = ["Depot", "Temple", "Bank", "Arena"]
        wps = [Waypoint(name=n, coord=_BASE) for n in names]
        sorted_wps = sorted(wps)
        assert [w.name for w in sorted_wps] == sorted(names)

    def test_equal_names_not_lt(self):
        a = Waypoint(name="Temple", coord=_BASE)
        b = Waypoint(name="Temple", coord=_BASE)
        assert not (a < b)
        assert not (b < a)

    def test_min_returns_alphabetically_first(self):
        wps = [Waypoint(name=n, coord=_BASE) for n in ["Temple", "Arena", "Depot"]]
        assert min(wps).name == "Arena"


# ─────────────────────────────────────────────────────────────────────────────
# Route.reversed()
# ─────────────────────────────────────────────────────────────────────────────

_C = lambda px, py: Coordinate(BOUNDS["xMin"] + px, BOUNDS["yMin"] + py, 7)


class TestRouteReversed:

    def _route(self, n: int) -> Route:
        steps = [_C(i, 0) for i in range(n)]
        return Route(
            start=steps[0],
            end=steps[-1],
            steps=steps,
            total_distance=float(n - 1),
            found=True,
        )

    def test_start_and_end_swapped(self):
        r = self._route(5)
        rev = r.reversed()
        assert rev.start == r.end
        assert rev.end == r.start

    def test_steps_are_reversed(self):
        r = self._route(5)
        rev = r.reversed()
        assert rev.steps == list(reversed(r.steps))

    def test_total_distance_preserved(self):
        r = self._route(5)
        rev = r.reversed()
        assert rev.total_distance == pytest.approx(r.total_distance)

    def test_found_preserved(self):
        r = self._route(3)
        assert r.reversed().found is True

    def test_not_found_preserved(self):
        c = _C(0, 0)
        r = Route(start=c, end=c, steps=[], total_distance=0.0, found=False)
        assert r.reversed().found is False

    def test_double_reverse_equals_original(self):
        r = self._route(5)
        assert r.reversed().reversed().steps == r.steps

    def test_reversed_is_new_object(self):
        r = self._route(5)
        assert r.reversed() is not r

    def test_empty_steps_reversed(self):
        c = _C(0, 0)
        r = Route(start=c, end=c, steps=[], total_distance=0.0, found=False)
        rev = r.reversed()
        assert rev.steps == []


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate.offset
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateOffset:

    def test_offset_x(self):
        c = Coordinate(100, 100, 7)
        assert c.offset(1, 0) == Coordinate(101, 100, 7)

    def test_offset_y(self):
        c = Coordinate(100, 100, 7)
        assert c.offset(0, -1) == Coordinate(100, 99, 7)

    def test_offset_z(self):
        c = Coordinate(100, 100, 7)
        assert c.offset(0, 0, 1) == Coordinate(100, 100, 8)

    def test_offset_all_axes(self):
        c = Coordinate(100, 200, 7)
        assert c.offset(3, -2, -1) == Coordinate(103, 198, 6)

    def test_offset_zero_is_equal(self):
        c = Coordinate(100, 100, 7)
        assert c.offset(0, 0, 0) == c

    def test_offset_returns_new_object(self):
        c = Coordinate(100, 100, 7)
        assert c.offset(1, 0) is not c


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate.is_same_floor
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateIsSameFloor:

    def test_same_floor(self):
        a = Coordinate(100, 100, 7)
        b = Coordinate(200, 200, 7)
        assert a.is_same_floor(b) is True

    def test_different_floor(self):
        a = Coordinate(100, 100, 7)
        b = Coordinate(100, 100, 8)
        assert a.is_same_floor(b) is False

    def test_symmetric(self):
        a = Coordinate(100, 100, 6)
        b = Coordinate(200, 200, 6)
        assert b.is_same_floor(a) is True

    def test_floor_zero(self):
        a = Coordinate(100, 100, 0)
        b = Coordinate(50, 50, 0)
        assert a.is_same_floor(b) is True


# ─────────────────────────────────────────────────────────────────────────────
# Waypoint.on_floor
# ─────────────────────────────────────────────────────────────────────────────

class TestWaypointOnFloor:

    def test_on_correct_floor(self):
        wp = Waypoint("A", Coordinate(100, 100, 7))
        assert wp.on_floor(7) is True

    def test_not_on_wrong_floor(self):
        wp = Waypoint("A", Coordinate(100, 100, 7))
        assert wp.on_floor(8) is False

    def test_floor_zero(self):
        wp = Waypoint("A", Coordinate(100, 100, 0))
        assert wp.on_floor(0) is True

# ─────────────────────────────────────────────────────────────────────────────
# Route.contains
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteContains:

    def _make_route(self):
        steps = [Coordinate(100 + i, 100, 7) for i in range(5)]
        return Route(start=steps[0], end=steps[-1], steps=steps,
                     total_distance=4.0, found=True)

    def test_contains_step(self):
        r = self._make_route()
        assert r.contains(Coordinate(102, 100, 7)) is True

    def test_not_contains_absent(self):
        r = self._make_route()
        assert r.contains(Coordinate(999, 999, 7)) is False

    def test_contains_first_step(self):
        r = self._make_route()
        assert r.contains(r.steps[0]) is True

    def test_contains_last_step(self):
        r = self._make_route()
        assert r.contains(r.steps[-1]) is True

    def test_empty_route_not_contains(self):
        c = Coordinate(100, 100, 7)
        r = Route(start=c, end=c, steps=[], total_distance=0.0, found=False)
        assert r.contains(c) is False

# ─────────────────────────────────────────────────────────────────────────────
# Route.slice
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteSlice:

    def _make_route(self, n: int = 10):
        steps = [Coordinate(100 + i, 100, 7) for i in range(n)]
        return Route(start=steps[0], end=steps[-1], steps=steps,
                     total_distance=float(n - 1), found=True)

    def test_slice_length(self):
        r = self._make_route(10)
        s = r.slice(2, 7)
        assert len(s.steps) == 5

    def test_slice_start_coord(self):
        r = self._make_route(10)
        s = r.slice(2, 7)
        assert s.start == r.steps[2]

    def test_slice_end_coord(self):
        r = self._make_route(10)
        s = r.slice(2, 7)
        assert s.end == r.steps[6]

    def test_slice_found_true(self):
        r = self._make_route(10)
        assert r.slice(0, 5).found is True

    def test_empty_slice_found_false(self):
        r = self._make_route(10)
        assert r.slice(5, 5).found is False

    def test_empty_slice_has_no_steps(self):
        r = self._make_route(10)
        assert r.slice(5, 5).steps == []

    def test_full_slice_equals_original(self):
        r = self._make_route(10)
        s = r.slice(0, 10)
        assert s.steps == r.steps

    def test_distance_proportional(self):
        r = self._make_route(10)  # total_distance = 9.0
        s = r.slice(0, 5)         # 5 steps out of 10
        assert s.total_distance == pytest.approx(9.0 * 5 / 10)


class TestCoordinateIsAdjacentTo:

    def test_cardinal_right_is_adjacent(self):
        c = Coordinate(32370, 32240, 7)
        assert c.is_adjacent_to(Coordinate(32371, 32240, 7)) is True

    def test_cardinal_left_is_adjacent(self):
        c = Coordinate(32370, 32240, 7)
        assert c.is_adjacent_to(Coordinate(32369, 32240, 7)) is True

    def test_cardinal_up_is_adjacent(self):
        c = Coordinate(32370, 32240, 7)
        assert c.is_adjacent_to(Coordinate(32370, 32239, 7)) is True

    def test_cardinal_down_is_adjacent(self):
        c = Coordinate(32370, 32240, 7)
        assert c.is_adjacent_to(Coordinate(32370, 32241, 7)) is True

    def test_diagonal_is_adjacent(self):
        c = Coordinate(32370, 32240, 7)
        assert c.is_adjacent_to(Coordinate(32371, 32241, 7)) is True

    def test_two_tiles_away_not_adjacent(self):
        c = Coordinate(32370, 32240, 7)
        assert c.is_adjacent_to(Coordinate(32372, 32240, 7)) is False

    def test_same_tile_not_adjacent(self):
        c = Coordinate(32370, 32240, 7)
        assert c.is_adjacent_to(c) is False

    def test_different_floor_not_adjacent(self):
        c = Coordinate(32370, 32240, 7)
        assert c.is_adjacent_to(Coordinate(32371, 32240, 8)) is False

class TestRouteFirstLast:

    def _make_route(self, n: int = 5):
        steps = [Coordinate(100 + i, 100, 7) for i in range(n)]
        return Route(start=steps[0], end=steps[-1], steps=steps, found=True)

    def test_first_returns_first_step(self):
        r = self._make_route()
        assert r.first == r.steps[0]

    def test_last_returns_last_step(self):
        r = self._make_route()
        assert r.last == r.steps[-1]

    def test_first_none_on_empty(self):
        r = Route(start=Coordinate(100, 100, 7), end=Coordinate(105, 100, 7), found=False)
        assert r.first is None

    def test_last_none_on_empty(self):
        r = Route(start=Coordinate(100, 100, 7), end=Coordinate(105, 100, 7), found=False)
        assert r.last is None

    def test_single_step_first_equals_last(self):
        c = Coordinate(100, 100, 7)
        r = Route(start=c, end=c, steps=[c], found=True)
        assert r.first == r.last == c

    def test_first_is_step_zero(self):
        r = self._make_route(4)
        assert r.first == Coordinate(100, 100, 7)

    def test_last_is_step_n_minus_one(self):
        r = self._make_route(4)
        assert r.last == Coordinate(103, 100, 7)


class TestCoordinateOnFloor:

    def test_on_floor_true_same_floor(self):
        c = Coordinate(32370, 32240, 7)
        assert c.on_floor(7) is True

    def test_on_floor_false_different_floor(self):
        c = Coordinate(32370, 32240, 7)
        assert c.on_floor(8) is False

    def test_on_floor_zero_floor(self):
        c = Coordinate(32370, 32240, 0)
        assert c.on_floor(0) is True

    def test_on_floor_negative_z(self):
        c = Coordinate(32370, 32240, 7)
        assert c.on_floor(-1) is False


class TestRouteIsValid:

    def _make_route(self, n: int = 5):
        steps = [Coordinate(100 + i, 100, 7) for i in range(n)]
        return Route(start=steps[0], end=steps[-1], steps=steps, found=True)

    def test_valid_when_found_and_has_steps(self):
        r = self._make_route()
        assert r.is_valid is True

    def test_invalid_when_not_found(self):
        r = Route(start=Coordinate(100, 100, 7), end=Coordinate(105, 100, 7), found=False)
        assert r.is_valid is False

    def test_invalid_when_found_but_no_steps(self):
        r = Route(start=Coordinate(100, 100, 7), end=Coordinate(105, 100, 7),
                  steps=[], found=True)
        assert r.is_valid is False

    def test_valid_single_step(self):
        c = Coordinate(100, 100, 7)
        r = Route(start=c, end=c, steps=[c], found=True)
        assert r.is_valid is True

    def test_invalid_after_slice_empty(self):
        r = self._make_route(10)
        s = r.slice(5, 5)  # empty slice
        assert s.is_valid is False


class TestRouteContainsFloor:
    def _make_multi_floor_route(self) -> Route:
        steps = [
            Coordinate(100, 100, 7),
            Coordinate(101, 100, 7),
            Coordinate(102, 100, 8),
        ]
        return Route(start=steps[0], end=steps[-1], steps=steps, found=True)

    def test_true_for_present_floor(self):
        r = self._make_multi_floor_route()
        assert r.contains_floor(7) is True

    def test_true_for_second_floor(self):
        r = self._make_multi_floor_route()
        assert r.contains_floor(8) is True

    def test_false_for_absent_floor(self):
        r = self._make_multi_floor_route()
        assert r.contains_floor(9) is False

    def test_false_for_empty_steps(self):
        r = Route(start=Coordinate(100, 100, 7), end=Coordinate(105, 100, 7))
        assert r.contains_floor(7) is False

class TestRouteFloorSpan:
    def _make_route(self, z_values: list) -> Route:
        steps = [Coordinate(100 + i, 100, z) for i, z in enumerate(z_values)]
        if steps:
            return Route(start=steps[0], end=steps[-1], steps=steps, found=True)
        return Route(start=Coordinate(100, 100, 7), end=Coordinate(100, 100, 7))

    def test_span_single_floor(self):
        r = self._make_route([7, 7, 7])
        assert r.floor_span() == 1

    def test_span_two_floors(self):
        r = self._make_route([7, 7, 8])
        assert r.floor_span() == 2

    def test_span_empty_route(self):
        r = self._make_route([])
        assert r.floor_span() == 0

    def test_span_three_floors(self):
        r = self._make_route([7, 8, 9])
        assert r.floor_span() == 3

    def test_span_counts_unique_floors(self):
        # Many steps on same two floors should still give span==2
        r = self._make_route([7, 7, 8, 8, 7, 8])
        assert r.floor_span() == 2


class TestFloorTransitionIsAscending:
    def _trans(self, entry_z: int, exit_z: int) -> "FloorTransition":
        from src.models import FloorTransition
        entry = Coordinate(32370, 32240, entry_z)
        exit_ = Coordinate(32370, 32240, exit_z)
        return FloorTransition(entry=entry, exit=exit_)

    def test_ascending_when_exit_z_lower(self):
        # Moving from z=8 → z=7 is going *up*
        assert self._trans(8, 7).is_ascending is True

    def test_descending_not_ascending(self):
        assert self._trans(7, 8).is_ascending is False

    def test_same_floor_not_ascending(self):
        assert self._trans(7, 7).is_ascending is False

    def test_multi_floor_ascend(self):
        # z=10 → z=7 (3 floors up)
        assert self._trans(10, 7).is_ascending is True


class TestFloorTransitionFloorDelta:
    def _trans(self, entry_z: int, exit_z: int) -> "FloorTransition":
        from src.models import FloorTransition
        entry = Coordinate(32370, 32240, entry_z)
        exit_ = Coordinate(32370, 32240, exit_z)
        return FloorTransition(entry=entry, exit=exit_)

    def test_delta_descend_is_positive(self):
        # z=7 → z=8: going down, delta = +1
        assert self._trans(7, 8).floor_delta == 1

    def test_delta_ascend_is_negative(self):
        # z=8 → z=7: going up, delta = -1
        assert self._trans(8, 7).floor_delta == -1

    def test_delta_same_floor_is_zero(self):
        assert self._trans(7, 7).floor_delta == 0

    def test_delta_multi_floor(self):
        assert self._trans(7, 10).floor_delta == 3

    def test_delta_is_symmetric(self):
        # ascending and descending versions sum to zero
        assert self._trans(7, 8).floor_delta + self._trans(8, 7).floor_delta == 0


# ─────────────────────────────────────────────────────────────────────────────
# Route.is_empty / Route.distance_per_step
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteIsEmpty:

    def test_true_with_no_steps(self):
        r = Route(start=Coordinate(1, 1, 7), end=Coordinate(2, 2, 7))
        assert r.is_empty is True

    def test_false_with_one_step(self):
        c = Coordinate(1, 1, 7)
        r = Route(start=c, end=c, steps=[c], found=True)
        assert r.is_empty is False

    def test_false_with_multiple_steps(self):
        cs = [Coordinate(i, i, 7) for i in range(3)]
        r = Route(start=cs[0], end=cs[-1], steps=cs, found=True)
        assert r.is_empty is False

    def test_true_even_when_found_false(self):
        r = Route(start=Coordinate(1, 1, 7), end=Coordinate(2, 2, 7), found=False)
        assert r.is_empty is True

class TestRouteDistancePerStep:

    def test_zero_for_empty_route(self):
        r = Route(start=Coordinate(1, 1, 7), end=Coordinate(1, 1, 7),
                  total_distance=10.0)
        assert r.distance_per_step == pytest.approx(0.0)

    def test_correct_for_single_step(self):
        c = Coordinate(1, 1, 7)
        r = Route(start=c, end=c, steps=[c], total_distance=5.0, found=True)
        assert r.distance_per_step == pytest.approx(5.0)

    def test_correct_for_multiple_steps(self):
        cs = [Coordinate(i, i, 7) for i in range(4)]
        r = Route(start=cs[0], end=cs[-1], steps=cs,
                  total_distance=12.0, found=True)
        assert r.distance_per_step == pytest.approx(3.0)

    def test_zero_distance_returns_zero(self):
        cs = [Coordinate(i, 0, 7) for i in range(3)]
        r = Route(start=cs[0], end=cs[-1], steps=cs,
                  total_distance=0.0, found=True)
        assert r.distance_per_step == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate.is_surface
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateIsSurface:

    def test_true_when_z_equals_ground_floor(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR)
        assert c.is_surface is True

    def test_false_when_z_above_ground_floor(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR + 1)
        assert c.is_surface is False

    def test_false_when_z_below_ground_floor(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR - 1)
        assert c.is_surface is False

    def test_default_z_is_surface(self):
        c = Coordinate(32370, 32240)  # z defaults to GROUND_FLOOR
        assert c.is_surface is True


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate.is_underground
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateIsUnderground:

    def test_false_at_ground_floor(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR)
        assert c.is_underground is False

    def test_true_one_below_ground(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR + 1)
        assert c.is_underground is True

    def test_true_deep_underground(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR + 7)
        assert c.is_underground is True

    def test_false_above_ground(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR - 1)
        assert c.is_underground is False

    def test_mutually_exclusive_with_is_surface_underground(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR + 1)
        assert c.is_underground is True
        assert c.is_surface is False

    def test_mutually_exclusive_both_false_for_above_ground(self):
        c = Coordinate(32370, 32240, GROUND_FLOOR - 2)
        assert c.is_underground is False
        assert c.is_surface is False


# ─────────────────────────────────────────────────────────────────────────────
# Waypoint.is_default_icon
# ─────────────────────────────────────────────────────────────────────────────

class TestWaypointIsDefaultIcon:

    def test_true_when_icon_is_checkmark(self):
        wp = Waypoint(coord=Coordinate(32370, 32240, 7), name="A", icon="checkmark")
        assert wp.is_default_icon is True

    def test_false_when_icon_is_star(self):
        wp = Waypoint(coord=Coordinate(32370, 32240, 7), name="A", icon="star")
        assert wp.is_default_icon is False

    def test_false_when_icon_is_empty_string(self):
        wp = Waypoint(coord=Coordinate(32370, 32240, 7), name="A", icon="")
        assert wp.is_default_icon is False

    def test_false_when_icon_is_crossmark(self):
        wp = Waypoint(coord=Coordinate(32370, 32240, 7), name="A", icon="crossmark")
        assert wp.is_default_icon is False

    def test_default_waypoint_has_default_icon(self):
        wp = Waypoint(coord=Coordinate(32370, 32240, 7), name="A")
        # Default icon in Waypoint dataclass is "checkmark"
        assert wp.is_default_icon is True


# ─────────────────────────────────────────────────────────────────────────────
# Route.is_single_floor
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteIsSingleFloor:

    _A = Coordinate(32369, 32241, 7)
    _B = Coordinate(32370, 32241, 7)
    _C = Coordinate(32369, 32241, 8)

    def _route(self, steps: list) -> Route:
        return Route(start=self._A, end=self._B, steps=steps, found=bool(steps))

    def test_true_when_steps_list_is_empty(self):
        assert self._route([]).is_single_floor is True

    def test_true_when_single_step(self):
        assert self._route([self._A]).is_single_floor is True

    def test_true_when_all_steps_on_same_floor(self):
        steps = [Coordinate(32369 + i, 32241, 7) for i in range(5)]
        assert self._route(steps).is_single_floor is True

    def test_false_when_steps_span_two_floors(self):
        assert self._route([self._A, self._C]).is_single_floor is False

