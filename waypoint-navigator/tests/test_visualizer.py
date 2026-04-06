"""
tests/test_visualizer.py
=========================
Tests for src/visualizer.py — MapVisualizer.

All matplotlib and TibiaMapLoader calls are mocked so the tests run
without display, map files, or heavy dependencies.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call
from typing import List

import numpy as np
import pytest

from src.visualizer import (
    MapVisualizer,
    ROUTE_COLOR,
    START_COLOR,
    END_COLOR,
    WAYPOINT_COLOR,
    TILE_ZOOM,
    CROP_PADDING,
)
from src.models import Coordinate, Route, Waypoint, BOUNDS


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fake_loader(map_size: int = 400) -> MagicMock:
    """Return a MagicMock TibiaMapLoader that returns random RGBA images."""
    loader = MagicMock()
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (map_size, map_size, 4), dtype=np.uint8)
    loader.get_map_image.return_value = img
    walk = rng.integers(0, 2, (map_size, map_size), dtype=np.uint8)
    loader.get_walkability.return_value = walk.astype(bool)
    return loader


def _simple_route(floor: int = 7, n: int = 5) -> Route:
    steps = [Coordinate(BOUNDS["xMin"] + 10 + i, BOUNDS["yMin"] + 10, floor)
             for i in range(n)]
    return Route(start=steps[0], end=steps[-1], steps=steps, found=True)


def _not_found_route() -> Route:
    s = Coordinate(BOUNDS["xMin"] + 5, BOUNDS["yMin"] + 5, 7)
    return Route(start=s, end=s, steps=[], found=False)


# ─────────────────────────────────────────────────────────────────────────────
# TestConstants
# ─────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_route_color_is_tuple(self):
        assert isinstance(ROUTE_COLOR, tuple)
        assert len(ROUTE_COLOR) == 3

    def test_colors_in_range(self):
        for c in (ROUTE_COLOR, START_COLOR, END_COLOR, WAYPOINT_COLOR):
            for v in c:
                assert 0.0 <= v <= 1.0

    def test_tile_zoom_positive(self):
        assert TILE_ZOOM > 0

    def test_crop_padding_positive(self):
        assert CROP_PADDING > 0


# ─────────────────────────────────────────────────────────────────────────────
# TestConstruction
# ─────────────────────────────────────────────────────────────────────────────

class TestConstruction:
    def test_loader_stored(self):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        assert vis.loader is loader


# ─────────────────────────────────────────────────────────────────────────────
# TestShowFloor
# ─────────────────────────────────────────────────────────────────────────────

class TestShowFloor:
    def _run_show_floor(self, **kwargs):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        mock_plt = MagicMock()
        mock_fig = MagicMock()
        mock_ax  = MagicMock()
        mock_plt.subplots.return_value = (mock_fig, mock_ax)

        with patch("src.visualizer.MapVisualizer.show_floor",
                   side_effect=lambda *a, **kw: None):
            vis.show_floor(floor=7, **kwargs)

    def test_show_floor_no_crash(self, tmp_path):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        mock_plt = MagicMock()
        mock_fig = MagicMock()
        mock_ax  = MagicMock()
        mock_plt.subplots.return_value = (mock_fig, mock_ax)

        save_path = tmp_path / "out.png"
        with patch("matplotlib.pyplot.subplots", return_value=(mock_fig, mock_ax)), \
             patch("matplotlib.pyplot.tight_layout"), \
             patch("matplotlib.pyplot.savefig"), \
             patch("matplotlib.pyplot.show"), \
             patch("matplotlib.pyplot.close"):
            # Should call loader.get_map_image with requested floor
            vis.show_floor(floor=7, save_path=save_path)
        loader.get_map_image.assert_called_with(7)

    def test_show_floor_passes_routes_to_draw(self, tmp_path):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        route = _simple_route()
        mock_fig = MagicMock()
        mock_ax  = MagicMock()

        with patch("matplotlib.pyplot.subplots", return_value=(mock_fig, mock_ax)), \
             patch("matplotlib.pyplot.tight_layout"), \
             patch("matplotlib.pyplot.savefig"), \
             patch("matplotlib.pyplot.show"), \
             patch("matplotlib.pyplot.close"), \
             patch.object(vis, "_draw_route") as dr:
            vis.show_floor(floor=7, routes=[route],
                           save_path=tmp_path / "x.png")
        dr.assert_called_once()

    def test_show_floor_passes_waypoints(self, tmp_path):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        wp = Waypoint(name="A", coord=Coordinate(BOUNDS["xMin"] + 5, BOUNDS["yMin"] + 5, 7))
        mock_fig = MagicMock()
        mock_ax  = MagicMock()

        with patch("matplotlib.pyplot.subplots", return_value=(mock_fig, mock_ax)), \
             patch("matplotlib.pyplot.tight_layout"), \
             patch("matplotlib.pyplot.savefig"), \
             patch("matplotlib.pyplot.show"), \
             patch("matplotlib.pyplot.close"), \
             patch.object(vis, "_draw_waypoint") as dw:
            vis.show_floor(floor=7, waypoints=[wp],
                           save_path=tmp_path / "x.png")
        dw.assert_called_once_with(mock_ax, wp)


# ─────────────────────────────────────────────────────────────────────────────
# TestDrawRoute
# ─────────────────────────────────────────────────────────────────────────────

class TestDrawRoute:
    def test_not_found_route_does_nothing(self):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        ax  = MagicMock()
        vis._draw_route(ax, _not_found_route())
        ax.plot.assert_not_called()

    def test_empty_steps_does_nothing(self):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        ax  = MagicMock()
        route = Route(start=Coordinate(31800, 31000, 7),
                      end=Coordinate(31810, 31000, 7),
                      steps=[], found=True)
        vis._draw_route(ax, route)
        ax.plot.assert_not_called()

    def test_valid_route_draws_line(self):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        ax  = MagicMock()
        vis._draw_route(ax, _simple_route())
        ax.plot.assert_called_once()

    def test_valid_route_draws_start_and_end_markers(self):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        ax  = MagicMock()
        vis._draw_route(ax, _simple_route())
        assert ax.scatter.call_count == 2   # start + end

    def test_custom_color_passed_through(self):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        ax  = MagicMock()
        custom_color = (0.0, 1.0, 0.0)
        vis._draw_route(ax, _simple_route(), color=custom_color)
        call_kwargs = ax.plot.call_args
        assert custom_color in call_kwargs.args or call_kwargs.kwargs.get("color") == custom_color


# ─────────────────────────────────────────────────────────────────────────────
# TestDrawWaypoint
# ─────────────────────────────────────────────────────────────────────────────

class TestDrawWaypoint:
    def test_draws_scatter_and_annotate(self):
        ax = MagicMock()
        wp = Waypoint(name="TestPoint",
                      coord=Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 10, 7))
        MapVisualizer._draw_waypoint(ax, wp)
        ax.scatter.assert_called_once()
        ax.annotate.assert_called_once()

    def test_annotate_uses_waypoint_name(self):
        ax = MagicMock()
        wp = Waypoint(name="MyLabel",
                      coord=Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 10, 7))
        MapVisualizer._draw_waypoint(ax, wp)
        annotate_args = ax.annotate.call_args
        assert "MyLabel" in annotate_args.args or "MyLabel" in str(annotate_args)


# ─────────────────────────────────────────────────────────────────────────────
# TestShowRoute
# ─────────────────────────────────────────────────────────────────────────────

class TestShowRoute:
    def test_not_found_route_prints_and_returns(self, capsys):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        vis.show_route(_not_found_route())
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or len(captured.out) == 0

    def test_found_route_calls_loader(self, tmp_path):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        route = _simple_route()
        mock_fig = MagicMock()
        mock_ax  = MagicMock()

        with patch("matplotlib.pyplot.subplots", return_value=(mock_fig, mock_ax)), \
             patch("matplotlib.pyplot.tight_layout"), \
             patch("matplotlib.pyplot.savefig"), \
             patch("matplotlib.pyplot.show"), \
             patch("matplotlib.pyplot.close"), \
             patch.object(vis, "_draw_route"):
            vis.show_route(route, save_path=tmp_path / "r.png")
        loader.get_map_image.assert_called()

    def test_walkability_mode_calls_get_walkability(self, tmp_path):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        route = _simple_route()
        mock_fig = MagicMock()
        mock_ax  = MagicMock()

        with patch("matplotlib.pyplot.subplots", return_value=(mock_fig, mock_ax)), \
             patch("matplotlib.pyplot.tight_layout"), \
             patch("matplotlib.pyplot.savefig"), \
             patch("matplotlib.pyplot.show"), \
             patch("matplotlib.pyplot.close"), \
             patch.object(vis, "_draw_route"):
            vis.show_route(route, show_walkability=True,
                           save_path=tmp_path / "r.png")
        loader.get_walkability.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# TestShowMultiRoute
# ─────────────────────────────────────────────────────────────────────────────

class TestShowMultiRoute:
    def test_empty_steps_prints_warning(self, capsys):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        seg = Route(start=Coordinate(31800, 31000, 7),
                    end=Coordinate(31810, 31000, 7),
                    steps=[], found=True)
        vis.show_multi_route([seg])
        out = capsys.readouterr().out
        assert "no steps" in out.lower() or len(out) == 0

    def test_valid_segments_draw_each_segment(self, tmp_path):
        loader = _fake_loader()
        vis = MapVisualizer(loader)
        seg1 = _simple_route(floor=7)
        seg2 = _simple_route(floor=7)
        mock_fig = MagicMock()
        mock_ax  = MagicMock()

        with patch("matplotlib.pyplot.subplots", return_value=(mock_fig, mock_ax)), \
             patch("matplotlib.pyplot.tight_layout"), \
             patch("matplotlib.pyplot.savefig"), \
             patch("matplotlib.pyplot.show"), \
             patch("matplotlib.pyplot.close"), \
             patch.object(vis, "_draw_route") as dr:
            vis.show_multi_route([seg1, seg2], save_path=tmp_path / "mr.png")
        assert dr.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# TestRouteBoundingBox
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteBoundingBox:

    def test_raises_on_empty_route(self):
        vis = MapVisualizer(_fake_loader())
        route = Route(start=Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7),
                      end=Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7),
                      steps=[], found=False)
        with pytest.raises(ValueError, match="no steps"):
            vis.route_bounding_box(route)

    def test_returns_four_ints(self):
        vis = MapVisualizer(_fake_loader())
        route = _simple_route()
        bbox = vis.route_bounding_box(route)
        assert len(bbox) == 4
        assert all(isinstance(v, int) for v in bbox)

    def test_px0_less_than_px1(self):
        vis = MapVisualizer(_fake_loader())
        bbox = vis.route_bounding_box(_simple_route())
        assert bbox[0] < bbox[2]

    def test_py0_less_than_py1(self):
        vis = MapVisualizer(_fake_loader())
        bbox = vis.route_bounding_box(_simple_route())
        assert bbox[1] < bbox[3]

    def test_no_padding_tighter_box(self):
        vis = MapVisualizer(_fake_loader())
        route = _simple_route()
        padded   = vis.route_bounding_box(route, padding=50)
        unpadded = vis.route_bounding_box(route, padding=0)
        assert padded[0] <= unpadded[0]
        assert padded[1] <= unpadded[1]
        assert padded[2] >= unpadded[2]
        assert padded[3] >= unpadded[3]

    def test_clamps_to_non_negative(self):
        vis = MapVisualizer(_fake_loader())
        # Route starting at origin pixel (0,0) with huge padding
        steps = [Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7),
                 Coordinate(BOUNDS["xMin"] + 1, BOUNDS["yMin"] + 1, 7)]
        route = Route(start=steps[0], end=steps[-1], steps=steps, found=True)
        bbox = vis.route_bounding_box(route, padding=9999)
        assert bbox[0] >= 0
        assert bbox[1] >= 0


# ─────────────────────────────────────────────────────────────────────────────
# TestWaypointsInView
# ─────────────────────────────────────────────────────────────────────────────

class TestWaypointsInView:

    def _wp(self, name: str, px: int, py: int, z: int = 7) -> Waypoint:
        return Waypoint(
            name=name,
            coord=Coordinate(BOUNDS["xMin"] + px, BOUNDS["yMin"] + py, z),
        )

    def test_empty_list_returns_empty(self):
        vis = MapVisualizer(_fake_loader())
        assert vis.waypoints_in_view([], 7, 0, 0, 100, 100) == []

    def test_waypoint_inside_view_included(self):
        vis = MapVisualizer(_fake_loader())
        wp = self._wp("inside", 50, 50)
        result = vis.waypoints_in_view([wp], 7, 0, 0, 100, 100)
        assert wp in result

    def test_waypoint_outside_view_excluded(self):
        vis = MapVisualizer(_fake_loader())
        wp = self._wp("outside", 200, 200)
        result = vis.waypoints_in_view([wp], 7, 0, 0, 100, 100)
        assert wp not in result

    def test_waypoint_wrong_floor_excluded(self):
        vis = MapVisualizer(_fake_loader())
        wp = self._wp("z8", 50, 50, z=8)
        result = vis.waypoints_in_view([wp], 7, 0, 0, 100, 100)
        assert result == []

    def test_boundary_pixel_included(self):
        vis = MapVisualizer(_fake_loader())
        wp = self._wp("on_edge", 100, 100)   # pixel (100,100) on boundary
        result = vis.waypoints_in_view([wp], 7, 0, 0, 100, 100)
        assert wp in result

    def test_multiple_floors_mixed(self):
        vis = MapVisualizer(_fake_loader())
        wps = [
            self._wp("A", 10, 10, z=7),
            self._wp("B", 20, 20, z=8),
            self._wp("C", 30, 30, z=7),
        ]
        result = vis.waypoints_in_view(wps, 7, 0, 0, 500, 500)
        names = {w.name for w in result}
        assert names == {"A", "C"}


# ─────────────────────────────────────────────────────────────────────────────
# TestSegmentColors
# ─────────────────────────────────────────────────────────────────────────────

class TestSegmentColors:

    def test_zero_returns_empty(self):
        assert MapVisualizer.segment_colors(0) == []

    def test_negative_returns_empty(self):
        assert MapVisualizer.segment_colors(-1) == []

    def test_length_matches_n(self):
        for n in (1, 3, 5, 10):
            assert len(MapVisualizer.segment_colors(n)) == n

    def test_each_color_is_rgb_triple(self):
        for color in MapVisualizer.segment_colors(6):
            assert len(color) == 3
            assert all(0.0 <= v <= 1.0 for v in color)

    def test_colors_are_distinct_for_n_gt_1(self):
        colors = MapVisualizer.segment_colors(4)
        assert len(set(colors)) == 4, "Expected all distinct colors"

    def test_single_color_valid(self):
        colors = MapVisualizer.segment_colors(1)
        assert len(colors) == 1
        r, g, b = colors[0]
        assert 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# has_map_image / stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestVisualizerExtras:

    def test_has_map_image_true(self):
        loader = _fake_loader()
        loader.floor_loaded.return_value = True
        viz = MapVisualizer(loader)
        assert viz.has_map_image(7) is True
        loader.floor_loaded.assert_called_once_with(7)

    def test_has_map_image_false(self):
        loader = _fake_loader()
        loader.floor_loaded.return_value = False
        viz = MapVisualizer(loader)
        assert viz.has_map_image(5) is False

    def test_has_map_image_returns_bool(self):
        loader = _fake_loader()
        loader.floor_loaded.return_value = True
        viz = MapVisualizer(loader)
        assert isinstance(viz.has_map_image(7), bool)

    def test_stats_snapshot_returns_dict(self):
        loader = _fake_loader()
        loader.stats_snapshot.return_value = {"loaded_count": 0, "waypoints_loaded": False}
        viz = MapVisualizer(loader)
        assert isinstance(viz.stats_snapshot(), dict)

    def test_stats_snapshot_all_keys(self):
        loader = _fake_loader()
        loader.stats_snapshot.return_value = {"loaded_count": 1, "waypoints_loaded": False}
        viz = MapVisualizer(loader)
        snap = viz.stats_snapshot()
        assert "loaded_count" in snap
        assert "waypoints_loaded" in snap

    def test_stats_snapshot_values(self):
        loader = _fake_loader()
        loader.stats_snapshot.return_value = {"loaded_count": 3, "waypoints_loaded": True}
        viz = MapVisualizer(loader)
        snap = viz.stats_snapshot()
        assert snap["loaded_count"]     == 3
        assert snap["waypoints_loaded"] is True

    def test_stats_snapshot_delegates_to_loader(self):
        loader = _fake_loader()
        loader.stats_snapshot.return_value = {"loaded_count": 0, "waypoints_loaded": False}
        viz = MapVisualizer(loader)
        viz.stats_snapshot()
        loader.stats_snapshot.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# loaded_count / waypoints_loaded
# ─────────────────────────────────────────────────────────────────────────────

class TestVisualizerLoadedCount:

    def test_delegates_to_loader(self):
        loader = _fake_loader()
        loader.loaded_count = 3
        viz = MapVisualizer(loader)
        assert viz.loaded_count == 3

    def test_zero_when_nothing_cached(self):
        loader = _fake_loader()
        loader.loaded_count = 0
        viz = MapVisualizer(loader)
        assert viz.loaded_count == 0

    def test_returns_int(self):
        loader = _fake_loader()
        loader.loaded_count = 2
        viz = MapVisualizer(loader)
        assert isinstance(viz.loaded_count, int)

    def test_reflects_loader_changes(self):
        loader = _fake_loader()
        loader.loaded_count = 1
        viz = MapVisualizer(loader)
        loader.loaded_count = 5
        assert viz.loaded_count == 5


class TestVisualizerWaypointsLoaded:

    def test_false_when_no_waypoints(self):
        loader = _fake_loader()
        loader.has_waypoints = False
        viz = MapVisualizer(loader)
        assert viz.waypoints_loaded is False

    def test_true_when_waypoints_present(self):
        loader = _fake_loader()
        loader.has_waypoints = True
        viz = MapVisualizer(loader)
        assert viz.waypoints_loaded is True

    def test_returns_bool(self):
        loader = _fake_loader()
        loader.has_waypoints = False
        viz = MapVisualizer(loader)
        assert isinstance(viz.waypoints_loaded, bool)

    def test_reflects_loader_state(self):
        loader = _fake_loader()
        loader.has_waypoints = False
        viz = MapVisualizer(loader)
        loader.has_waypoints = True
        assert viz.waypoints_loaded is True


# ─────────────────────────────────────────────────────────────────────────────
# MapVisualizer.map_images_count
# ─────────────────────────────────────────────────────────────────────────────

class TestMapVisualizerMapImagesCount:

    def test_zero_when_loader_has_none(self):
        loader = _fake_loader()
        loader.map_images_count = 0
        viz = MapVisualizer(loader)
        assert viz.map_images_count == 0

    def test_reflects_loader_value(self):
        loader = _fake_loader()
        loader.map_images_count = 3
        viz = MapVisualizer(loader)
        assert viz.map_images_count == 3

    def test_updates_when_loader_changes(self):
        loader = _fake_loader()
        loader.map_images_count = 1
        viz = MapVisualizer(loader)
        loader.map_images_count = 5
        assert viz.map_images_count == 5

    def test_returns_int(self):
        loader = _fake_loader()
        loader.map_images_count = 2
        viz = MapVisualizer(loader)
        assert isinstance(viz.map_images_count, int)


# ─────────────────────────────────────────────────────────────────────────────
# Missing coverage: save_path print branches and waypoint filter branches
# ─────────────────────────────────────────────────────────────────────────────

def _coord(x_off: int = 10, y_off: int = 10, z: int = 7) -> Coordinate:
    return Coordinate(BOUNDS["xMin"] + x_off, BOUNDS["yMin"] + y_off, z)


def _simple_route(z: int = 7) -> Route:
    steps = [_coord(i, i, z) for i in range(5)]
    return Route(start=steps[0], end=steps[-1], steps=steps, found=True)


def _waypoint(x_off: int = 10, y_off: int = 10, z: int = 7) -> Waypoint:
    return Waypoint(coord=_coord(x_off, y_off, z), name="WP")


class TestShowFloorSavePath:
    """Line 83: plt.show() branch (no save_path) and print branch (save_path)."""

    @patch("matplotlib.pyplot")
    def test_no_save_path_calls_show(self, mock_plt):
        loader = _fake_loader()
        viz = MapVisualizer(loader)
        fig_mock = MagicMock()
        ax_mock = MagicMock()
        mock_plt.subplots.return_value = (fig_mock, ax_mock)
        viz.show_floor(7)
        mock_plt.show.assert_called_once()

    @patch("matplotlib.pyplot")
    def test_save_path_triggers_savefig_and_print(self, mock_plt, tmp_path):
        loader = _fake_loader()
        viz = MapVisualizer(loader)
        fig_mock = MagicMock()
        ax_mock = MagicMock()
        mock_plt.subplots.return_value = (fig_mock, ax_mock)

        save = tmp_path / "out.png"
        import builtins
        with patch.object(builtins, "print") as mock_print:
            viz.show_floor(7, save_path=save)

        mock_plt.savefig.assert_called_once()
        mock_print.assert_called_once()
        assert "Saved" in mock_print.call_args[0][0]


class TestShowRouteWaypointFilter:
    """Lines 135-139: waypoints in view filtered and drawn; line 152: save print."""

    @patch("matplotlib.pyplot")
    def test_waypoints_in_view_drawn(self, mock_plt):
        loader = _fake_loader()
        loader.get_walkability.return_value = np.ones((400, 400), dtype=bool)
        viz = MapVisualizer(loader)
        fig_mock = MagicMock()
        ax_mock = MagicMock()
        mock_plt.subplots.return_value = (fig_mock, ax_mock)

        route = _simple_route()
        wp_in = _waypoint(10, 10, 7)   # within bounding box
        wp_out = _waypoint(380, 380, 7)  # outside bounding box

        viz.show_route(route, waypoints=[wp_in, wp_out])
        # ax_mock.scatter should have been called (for route + waypoint markers)
        assert ax_mock.scatter.called

    @patch("matplotlib.pyplot")
    def test_show_route_save_path_print(self, mock_plt, tmp_path):
        loader = _fake_loader()
        viz = MapVisualizer(loader)
        fig_mock = MagicMock()
        ax_mock = MagicMock()
        mock_plt.subplots.return_value = (fig_mock, ax_mock)

        route = _simple_route()
        save = tmp_path / "r.png"
        import builtins
        with patch.object(builtins, "print") as mock_print:
            viz.show_route(route, save_path=save)

        mock_plt.savefig.assert_called_once()
        mock_print.assert_called_once()


class TestShowMultiRoute:
    """Lines 172 (warning), 199-203 (waypoint filter), 215 (save print)."""

    @patch("matplotlib.pyplot")
    def test_multifloor_warning_printed(self, mock_plt):
        loader = _fake_loader()
        viz = MapVisualizer(loader)
        fig_mock = MagicMock()
        ax_mock = MagicMock()
        mock_plt.subplots.return_value = (fig_mock, ax_mock)

        # Two segments on different floors → warning
        seg_z7 = _simple_route(z=7)
        seg_z8 = _simple_route(z=8)
        import builtins
        with patch.object(builtins, "print") as mock_print:
            viz.show_multi_route([seg_z7, seg_z8])

        calls = [str(c) for c in mock_print.call_args_list]
        assert any("Warning" in c or "multi-floor" in c for c in calls)

    @patch("matplotlib.pyplot")
    def test_waypoints_filtered_in_multi_route(self, mock_plt):
        loader = _fake_loader()
        viz = MapVisualizer(loader)
        fig_mock = MagicMock()
        ax_mock = MagicMock()
        mock_plt.subplots.return_value = (fig_mock, ax_mock)

        route = _simple_route(z=7)
        wp = _waypoint(10, 10, 7)
        viz.show_multi_route([route], waypoints=[wp])
        assert ax_mock.scatter.called

    @patch("matplotlib.pyplot")
    def test_no_steps_prints_message(self, mock_plt):
        """Lines 178-179: show_multi_route with segments having no steps."""
        loader = _fake_loader()
        viz = MapVisualizer(loader)
        # Route with found=False → steps is empty → all_steps empty → print
        empty_route = Route(
            start=_coord(), end=_coord(1, 1), steps=[], found=False
        )
        import builtins
        with patch.object(builtins, "print") as mock_print:
            viz.show_multi_route([empty_route])
        mock_print.assert_called_once()
        assert "No steps" in str(mock_print.call_args)

    @patch("matplotlib.pyplot")
    def test_show_multi_save_path_print(self, mock_plt, tmp_path):
        loader = _fake_loader()
        viz = MapVisualizer(loader)
        fig_mock = MagicMock()
        ax_mock = MagicMock()
        mock_plt.subplots.return_value = (fig_mock, ax_mock)

        route = _simple_route()
        save = tmp_path / "multi.png"
        import builtins
        with patch.object(builtins, "print") as mock_print:
            viz.show_multi_route([route], save_path=save)

        mock_plt.savefig.assert_called_once()
        mock_print.assert_called_once()
