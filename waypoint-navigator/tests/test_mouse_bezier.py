"""Tests for src.mouse_bezier module."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from src.mouse_bezier import (
    _cubic_bezier,
    _ease_in_out,
    _random_control_point,
    bezier_path,
    move_mouse_smooth,
    move_mouse_to,
)


# ═══════════════════════════════════════════════════════════════════════════════
# _cubic_bezier
# ═══════════════════════════════════════════════════════════════════════════════


class TestCubicBezier:
    def test_t0_returns_p0(self):
        p0, p1, p2, p3 = (0.0, 0.0), (10.0, 20.0), (30.0, 40.0), (100.0, 100.0)
        result = _cubic_bezier(0.0, p0, p1, p2, p3)
        assert result == pytest.approx(p0, abs=1e-9)

    def test_t1_returns_p3(self):
        p0, p1, p2, p3 = (0.0, 0.0), (10.0, 20.0), (30.0, 40.0), (100.0, 100.0)
        result = _cubic_bezier(1.0, p0, p1, p2, p3)
        assert result == pytest.approx(p3, abs=1e-9)

    def test_midpoint_is_between(self):
        p0, p3 = (0.0, 0.0), (100.0, 0.0)
        p1, p2 = (33.0, 50.0), (66.0, 50.0)
        x, y = _cubic_bezier(0.5, p0, p1, p2, p3)
        assert 0 < x < 100
        assert y > 0  # curve goes above the line

    def test_straight_line(self):
        """Collinear control points → straight line."""
        p0, p3 = (0.0, 0.0), (100.0, 100.0)
        p1 = (33.3, 33.3)
        p2 = (66.6, 66.6)
        x, y = _cubic_bezier(0.5, p0, p1, p2, p3)
        assert abs(x - y) < 1.0  # approximately on the diagonal


# ═══════════════════════════════════════════════════════════════════════════════
# _ease_in_out
# ═══════════════════════════════════════════════════════════════════════════════


class TestEaseInOut:
    def test_boundaries(self):
        assert _ease_in_out(0.0) == pytest.approx(0.0)
        assert _ease_in_out(1.0) == pytest.approx(1.0)

    def test_midpoint(self):
        assert _ease_in_out(0.5) == pytest.approx(0.5)

    def test_monotonically_increasing(self):
        prev = 0.0
        for i in range(1, 101):
            t = i / 100
            val = _ease_in_out(t)
            assert val >= prev
            prev = val


# ═══════════════════════════════════════════════════════════════════════════════
# _random_control_point
# ═══════════════════════════════════════════════════════════════════════════════


class TestRandomControlPoint:
    def test_returns_tuple(self):
        cp = _random_control_point((0.0, 0.0), (100.0, 100.0))
        assert isinstance(cp, tuple)
        assert len(cp) == 2

    def test_not_at_endpoints(self):
        """Control point should generally not coincide with start or end."""
        start = (0.0, 0.0)
        end = (200.0, 200.0)
        for _ in range(20):
            cp = _random_control_point(start, end)
            # It's random, so just check it's not exactly at start/end
            dist_start = math.hypot(cp[0] - start[0], cp[1] - start[1])
            dist_end = math.hypot(cp[0] - end[0], cp[1] - end[1])
            if dist_start > 1 or dist_end > 1:
                return  # at least one iteration was off the endpoints
        pytest.fail("Control point always at an endpoint")

    def test_zero_distance(self):
        """Same start and end should not crash."""
        cp = _random_control_point((50.0, 50.0), (50.0, 50.0))
        assert isinstance(cp, tuple)


# ═══════════════════════════════════════════════════════════════════════════════
# bezier_path
# ═══════════════════════════════════════════════════════════════════════════════


class TestBezierPath:
    def test_starts_and_ends_at_correct_points(self):
        path = bezier_path((10, 20), (300, 400))
        assert path[0] == (10, 20)
        assert path[-1] == (300, 400)

    def test_custom_steps(self):
        path = bezier_path((0, 0), (100, 100), steps=25)
        assert len(path) == 26  # steps + 1

    def test_auto_steps_scales_with_distance(self):
        short = bezier_path((0, 0), (10, 10))
        long = bezier_path((0, 0), (1000, 1000))
        assert len(long) > len(short)

    def test_all_points_are_int_tuples(self):
        path = bezier_path((0, 0), (200, 200), steps=20)
        for pt in path:
            assert isinstance(pt, tuple)
            assert isinstance(pt[0], int)
            assert isinstance(pt[1], int)

    def test_path_length_matches_steps(self):
        path = bezier_path((0, 0), (500, 500), steps=50)
        assert len(path) == 51

    def test_zero_distance(self):
        """Same start and end should still produce a valid path."""
        path = bezier_path((100, 100), (100, 100), steps=10)
        assert len(path) == 11
        assert path[0] == (100, 100)
        assert path[-1] == (100, 100)


# ═══════════════════════════════════════════════════════════════════════════════
# move_mouse_smooth
# ═══════════════════════════════════════════════════════════════════════════════


class TestMoveMouseSmooth:
    def test_calls_move_fn_with_custom_callable(self):
        calls: list[tuple[int, int]] = []

        def track(x: int, y: int) -> None:
            calls.append((x, y))

        move_mouse_smooth((0, 0), (100, 100), duration=0.01, move_fn=track)
        # Should have called move_fn multiple times
        assert len(calls) >= 2
        # First and last positions
        assert calls[0] == (0, 0)
        assert calls[-1] == (100, 100)

    def test_short_path(self):
        """Very short distance should still complete."""
        move_fn = MagicMock()
        move_mouse_smooth((50, 50), (52, 52), duration=0.01, move_fn=move_fn)
        assert move_fn.call_count >= 2

    def test_auto_duration(self):
        """No explicit duration → should infer automatically."""
        move_fn = MagicMock()
        # Should not crash
        move_mouse_smooth((0, 0), (500, 500), move_fn=move_fn)
        assert move_fn.call_count >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# move_mouse_to
# ═══════════════════════════════════════════════════════════════════════════════


class TestMoveMouseTo:
    @patch("src.mouse_bezier._get_cursor_pos", return_value=(10, 20))
    @patch("src.mouse_bezier.move_mouse_smooth")
    def test_gets_current_pos_and_delegates(self, mock_smooth, mock_pos):
        move_mouse_to((300, 400), duration=0.01, move_fn=MagicMock())
        mock_pos.assert_called_once()
        mock_smooth.assert_called_once()
        args = mock_smooth.call_args
        assert args[0][0] == (10, 20)  # start = current pos
        assert args[0][1] == (300, 400)  # end = target


# ═══════════════════════════════════════════════════════════════════════════════
# _set_cursor_pos / _get_cursor_pos (Win32 paths via ctypes mock)
# ═══════════════════════════════════════════════════════════════════════════════

import src.mouse_bezier as _mb


class TestSetCursorPos:
    def test_warning_emitted_first_call(self):
        """_set_cursor_pos logs warning on first call (lines 42-48)."""
        import src.mouse_bezier as mb
        # Reset the warned flag so the warning fires
        mb._set_cursor_warned = False
        with patch("src.mouse_bezier.user32") as mock_user32:
            from src.mouse_bezier import _set_cursor_pos
            _set_cursor_pos(10, 20)
            mock_user32.SetCursorPos.assert_called_once_with(10, 20)
        # Subsequent call should not warn again (flag is set)
        assert mb._set_cursor_warned is True

    def test_second_call_no_warning(self):
        """Second call skips the warning branch."""
        import src.mouse_bezier as mb
        mb._set_cursor_warned = True
        with patch("src.mouse_bezier.user32") as mock_user32:
            from src.mouse_bezier import _set_cursor_pos
            _set_cursor_pos(5, 6)
            mock_user32.SetCursorPos.assert_called_once_with(5, 6)


class TestGetCursorPos:
    def test_returns_tuple(self):
        """_get_cursor_pos returns (x, y) via ctypes (lines 54-59)."""
        with patch("src.mouse_bezier.user32") as mock_user32:
            # GetCursorPos is a no-op mock; ctypes struct defaults to 0,0
            mock_user32.GetCursorPos.return_value = None
            from src.mouse_bezier import _get_cursor_pos
            result = _get_cursor_pos()
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], int)
            assert isinstance(result[1], int)


class TestMoveSmoothNLessThan2:
    def test_n_less_than_2_calls_move_fn(self):
        """When bezier_path returns <2 points, move_fn(end) called directly (lines 194-195)."""
        move_fn = MagicMock()
        with patch("src.mouse_bezier.bezier_path", return_value=[(50, 50)]):
            move_mouse_smooth((50, 50), (50, 50), duration=0.01, move_fn=move_fn)
        move_fn.assert_called_with(50, 50)
