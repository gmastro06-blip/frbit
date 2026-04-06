"""Tests for WalkabilityOverlay — diagnostic HUD for walkability visualisation."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.models import Coordinate, BOUNDS
from src.walkability_overlay import (
    WalkabilityOverlay,
    OverlayState,
    _OVERLAY_W,
    _OVERLAY_H,
    _DIR_MAP,
)

# ── helpers ──────────────────────────────────────────────────────────────────

_X_MIN = BOUNDS["xMin"]
_Y_MIN = BOUNDS["yMin"]


def _fake_loader(walk_arr: np.ndarray | None = None) -> MagicMock:
    loader = MagicMock()
    if walk_arr is None:
        walk_arr = np.ones((100, 100), dtype=bool)
    loader.get_walkability.return_value = walk_arr
    return loader


# ── OverlayState ─────────────────────────────────────────────────────────────

class TestOverlayState:
    def test_defaults(self) -> None:
        s = OverlayState()
        assert s.position is None
        assert s.waypoint is None
        assert s.status == "idle"
        assert s.directions == ""
        assert s.pnf_count == 0
        assert s.blocked_tiles == []
        assert s.route_tiles == []
        assert s.floor == 7

    def test_custom_fields(self) -> None:
        pos = Coordinate(_X_MIN + 50, _Y_MIN + 50, 7)
        s = OverlayState(position=pos, status="walking", pnf_count=2)
        assert s.position is pos
        assert s.status == "walking"
        assert s.pnf_count == 2


# ── Constructor ──────────────────────────────────────────────────────────────

class TestWalkabilityOverlayInit:
    def test_defaults(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        assert ov._radius == 30
        assert ov._win == "diag"
        assert ov.running is False

    def test_custom_params(self) -> None:
        ov = WalkabilityOverlay(_fake_loader(), view_radius=15, window_name="Test")
        assert ov._radius == 15
        assert ov._win == "Test"


# ── update() ─────────────────────────────────────────────────────────────────

class TestUpdate:
    def test_updates_position(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        pos = Coordinate(_X_MIN + 10, _Y_MIN + 10, 7)
        ov.update(position=pos)
        assert ov._state.position is pos

    def test_updates_status(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.update(status="blocked")
        assert ov._state.status == "blocked"

    def test_updates_multiple(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.update(status="walking", pnf_count=3, floor=6)
        assert ov._state.status == "walking"
        assert ov._state.pnf_count == 3
        assert ov._state.floor == 6

    def test_ignores_unknown_fields(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.update(nonexistent_field=42)
        assert not hasattr(ov._state, "nonexistent_field")


# ── add_direction() ──────────────────────────────────────────────────────────

class TestAddDirection:
    def test_south(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.add_direction(0, 1)
        assert ov._state.directions == "s"

    def test_north(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.add_direction(0, -1)
        assert ov._state.directions == "n"

    def test_east(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.add_direction(1, 0)
        assert ov._state.directions == "e"

    def test_sequence(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.add_direction(0, 1)
        ov.add_direction(0, 1)
        ov.add_direction(1, 0)
        assert ov._state.directions == "sse"

    def test_truncates_to_40(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov._state.directions = "x" * 45
        ov.add_direction(0, 1)
        assert len(ov._state.directions) == 40
        assert ov._state.directions.endswith("s")

    def test_unknown_dir(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.add_direction(5, 5)
        assert ov._state.directions == "?"


# ── add_blocked() ────────────────────────────────────────────────────────────

class TestAddBlocked:
    def test_adds_entry(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.add_blocked(100, 200, 7)
        assert (100, 200, 7) in ov._state.blocked_tiles

    def test_no_duplicates(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        ov.add_blocked(100, 200, 7)
        ov.add_blocked(100, 200, 7)
        assert ov._state.blocked_tiles.count((100, 200, 7)) == 1


# ── set_route() ──────────────────────────────────────────────────────────────

class TestSetRoute:
    def test_stores_xy_tuples(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        steps = [
            Coordinate(_X_MIN + 1, _Y_MIN + 1, 7),
            Coordinate(_X_MIN + 2, _Y_MIN + 1, 7),
        ]
        ov.set_route(steps)
        assert ov._state.route_tiles == [
            (_X_MIN + 1, _Y_MIN + 1),
            (_X_MIN + 2, _Y_MIN + 1),
        ]


# ── render() ─────────────────────────────────────────────────────────────────

class TestRender:
    def test_returns_bgr_image(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        img = ov.render()
        assert isinstance(img, np.ndarray)
        assert img.shape == (_OVERLAY_H, _OVERLAY_W, 3)
        assert img.dtype == np.uint8

    def test_no_position_shows_placeholder(self) -> None:
        ov = WalkabilityOverlay(_fake_loader())
        img = ov.render()
        # Should be mostly black with "no position" text (not all zeros)
        # Just verify it rendered without error and stored as last_render
        assert ov._last_render is not None

    def test_with_position(self) -> None:
        walk = np.ones((200, 200), dtype=bool)
        ov = WalkabilityOverlay(_fake_loader(walk), view_radius=5)
        pos = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        ov.update(position=pos, floor=7)
        img = ov.render()
        assert img.shape == (_OVERLAY_H, _OVERLAY_W, 3)
        # The map area should have some non-zero pixels (walkable tiles drawn)
        assert img.sum() > 0

    def test_with_waypoint(self) -> None:
        walk = np.ones((200, 200), dtype=bool)
        ov = WalkabilityOverlay(_fake_loader(walk), view_radius=5)
        pos = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        wp = Coordinate(_X_MIN + 102, _Y_MIN + 98, 7)
        ov.update(position=pos, waypoint=wp, floor=7)
        img = ov.render()
        assert img.sum() > 0

    def test_with_blocked_tiles(self) -> None:
        walk = np.ones((200, 200), dtype=bool)
        ov = WalkabilityOverlay(_fake_loader(walk), view_radius=5)
        pos = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        ov.update(position=pos, floor=7)
        ov.add_blocked(_X_MIN + 101, _Y_MIN + 100, 7)
        img = ov.render()
        assert img.sum() > 0

    def test_with_directions_and_status(self) -> None:
        walk = np.ones((200, 200), dtype=bool)
        ov = WalkabilityOverlay(_fake_loader(walk), view_radius=5)
        pos = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        ov.update(position=pos, status="blocked", floor=7)
        ov.add_direction(0, 1)
        ov.add_direction(0, 1)
        img = ov.render()
        assert img.sum() > 0

    def test_with_route(self) -> None:
        walk = np.ones((200, 200), dtype=bool)
        ov = WalkabilityOverlay(_fake_loader(walk), view_radius=5)
        pos = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        ov.update(position=pos, floor=7)
        route = [Coordinate(_X_MIN + 101, _Y_MIN + 100, 7),
                 Coordinate(_X_MIN + 102, _Y_MIN + 100, 7)]
        ov.set_route(route)
        img = ov.render()
        assert img.sum() > 0

    def test_loader_exception_graceful(self) -> None:
        loader = MagicMock()
        loader.get_walkability.side_effect = FileNotFoundError
        ov = WalkabilityOverlay(loader, view_radius=5)
        pos = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        ov.update(position=pos, floor=7)
        # Should not raise
        img = ov.render()
        assert isinstance(img, np.ndarray)

    def test_footer_shows_coords(self) -> None:
        walk = np.ones((200, 200), dtype=bool)
        ov = WalkabilityOverlay(_fake_loader(walk), view_radius=5)
        pos = Coordinate(_X_MIN + 45, _Y_MIN + 35, 7)
        wp = Coordinate(_X_MIN + 46, _Y_MIN + 36, 7)
        ov.update(position=pos, waypoint=wp, floor=7)
        # Render and check that the footer bar region is not all black
        img = ov.render()
        footer_region = img[_OVERLAY_H - 24:, :]
        assert footer_region.sum() > 0


# ── start()/stop() lifecycle ─────────────────────────────────────────────────

class TestLifecycle:
    @patch("src.walkability_overlay.cv2")
    def test_start_stop(self, mock_cv2: MagicMock) -> None:
        mock_cv2.waitKey.return_value = 0xFF  # no key pressed
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        ov.start()
        assert ov.running is True
        ov.stop()
        assert ov.running is False

    def test_double_start_ignored(self) -> None:
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        with patch("src.walkability_overlay.cv2"):
            ov.start()
            thread1 = ov._thread
            ov.start()
            assert ov._thread is thread1
            ov.stop()

    def test_stop_without_start(self) -> None:
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        ov.stop()  # Should not raise


# ── _DIR_MAP ─────────────────────────────────────────────────────────────────

class TestDirMap:
    def test_cardinal_directions(self) -> None:
        assert _DIR_MAP[(0, -1)] == "n"
        assert _DIR_MAP[(0, 1)] == "s"
        assert _DIR_MAP[(-1, 0)] == "w"
        assert _DIR_MAP[(1, 0)] == "e"

    def test_diagonal_directions(self) -> None:
        assert _DIR_MAP[(1, -1)] == "ne"
        assert _DIR_MAP[(-1, -1)] == "nw"
        assert _DIR_MAP[(1, 1)] == "se"
        assert _DIR_MAP[(-1, 1)] == "sw"

    def test_stationary(self) -> None:
        assert _DIR_MAP[(0, 0)] == "."


# ── Missing coverage: render() branches ──────────────────────────────────────

class TestRenderMissingBranches:
    """Cover the remaining uncovered branches in render() and _render_map()."""

    def _coord(self, dx: int = 0, dy: int = 0) -> Coordinate:
        return Coordinate(_X_MIN + 10 + dx, _Y_MIN + 10 + dy, 7)

    def test_render_with_step_target_in_header(self) -> None:
        """Line 211: step_target is not None → coord part added."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        pos = self._coord()
        step = self._coord(1, 0)
        ov.update(position=pos, step_target=step, floor=7)
        frame = ov.render()
        assert frame is not None

    def test_render_with_replan_nonzero(self) -> None:
        """Line 219: replan_count > 0 → info_line += replan."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        pos = self._coord()
        ov.update(position=pos, replan_count=3, floor=7)
        frame = ov.render()
        assert frame is not None

    def test_render_with_initial_pos_in_footer(self) -> None:
        """Line 246: initial_pos is not None → footer text."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        pos = self._coord()
        ov.update(position=pos, initial_pos=self._coord(2, 0), floor=7)
        frame = ov.render()
        assert frame is not None

    def test_render_with_step_in_footer(self) -> None:
        """Line 251: step_target in footer."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        pos = self._coord()
        ov.update(position=pos, step_target=self._coord(1, 0), floor=7)
        frame = ov.render()
        assert frame is not None

    def test_render_map_tile_px_minimum_one(self) -> None:
        """Line 280: view_radius so large that tile_px < 1 → clamped to 1."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=500)
        pos = self._coord()
        ov.update(position=pos, floor=7)
        frame = ov.render()
        assert frame is not None

    def test_render_map_non_walkable_color(self) -> None:
        """Line 311: tile outside walk array → _NON_WALKABLE_COLOR."""
        walk_arr = np.zeros((100, 100), dtype=bool)
        ov = WalkabilityOverlay(_fake_loader(walk_arr), view_radius=5)
        pos = self._coord()
        ov.update(position=pos, floor=7)
        frame = ov.render()
        assert frame is not None

    def test_render_map_with_step_target_in_radius(self) -> None:
        """Lines 336-341: step is within radius → drawMarker called."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        pos = self._coord()
        step = self._coord(2, 0)
        ov.update(position=pos, step_target=step, floor=7)
        frame = ov.render()
        assert frame is not None

    def test_add_blocked_over_cap_triggers_rotation(self) -> None:
        """Line 131: blocked_tiles > 10000 → rotation by half-drop."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)
        limit = WalkabilityOverlay._MAX_BLOCKED_TILES
        ov._state.blocked_tiles = [(i, i, 7) for i in range(limit)]
        ov.add_blocked(99999, 99999, 7)
        assert len(ov._state.blocked_tiles) == limit // 2 + 1


# ── _loop ESC and cleanup (lines 360-361, 369-370) ───────────────────────────

class TestLoopEscAndCleanup:

    def test_loop_esc_exits(self) -> None:
        """Lines 360-361: waitKey returns 27 (ESC) → _running becomes False."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)

        with patch("src.walkability_overlay.cv2") as mock_cv2:
            mock_cv2.WINDOW_NORMAL = 0
            mock_cv2.MARKER_CROSS = 0
            mock_cv2.MARKER_DIAMOND = 0
            mock_cv2.FONT_HERSHEY_SIMPLEX = 0
            mock_cv2.waitKey.return_value = 27  # ESC on first call

            ov._running = True
            ov._loop()

        assert not ov._running

    def test_loop_destroywindow_exception_suppressed(self) -> None:
        """Lines 369-370: cv2.destroyWindow raises → exception swallowed."""
        ov = WalkabilityOverlay(_fake_loader(), view_radius=5)

        with patch("src.walkability_overlay.cv2") as mock_cv2:
            mock_cv2.WINDOW_NORMAL = 0
            mock_cv2.MARKER_CROSS = 0
            mock_cv2.MARKER_DIAMOND = 0
            mock_cv2.FONT_HERSHEY_SIMPLEX = 0
            mock_cv2.waitKey.return_value = 27  # ESC immediately
            mock_cv2.destroyWindow.side_effect = RuntimeError("no display")

            ov._running = True
            ov._loop()  # must not raise
