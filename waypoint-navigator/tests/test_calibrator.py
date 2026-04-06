"""
tests/test_calibrator.py
========================
Tests for the pure-Python helpers in src/calibrator.py.

All tests are 100 % offline — no OpenCV windows, no frame capture,
no OBS connection.  Only the utility functions that don't require a
live frame are exercised here.
"""
from __future__ import annotations

import pytest

from src.calibrator import (
    list_modes,
    validate_roi,
    mode_exists,
    roi_area,
    roi_aspect_ratio,
    roi_center,
    roi_overlaps,
    _ALL_MODES,
)


# ─────────────────────────────────────────────────────────────────────────────
# TestListModes
# ─────────────────────────────────────────────────────────────────────────────

class TestListModes:

    def test_returns_list(self):
        assert isinstance(list_modes(), list)

    def test_contains_expected_modes(self):
        modes = list_modes()
        for m in ("coord", "hp", "mp", "minimap", "battle-list"):
            assert m in modes

    def test_length_matches_all_modes(self):
        assert len(list_modes()) == len(_ALL_MODES)

    def test_returns_copy_not_reference(self):
        modes = list_modes()
        modes.append("fake")
        assert "fake" not in _ALL_MODES

    def test_order_preserved(self):
        assert list_modes() == list(_ALL_MODES)

    def test_idempotent(self):
        assert list_modes() == list_modes()


# ─────────────────────────────────────────────────────────────────────────────
# TestValidateRoi
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateRoi:

    # ── valid inputs ──────────────────────────────────────────────────────────

    def test_valid_list_passes(self):
        assert validate_roi([10, 20, 100, 40]) is True

    def test_valid_tuple_passes(self):
        assert validate_roi((0, 0, 50, 30)) is True

    def test_zeros_for_x_y_allowed(self):
        assert validate_roi([0, 0, 1, 1]) is True

    def test_large_values_pass(self):
        assert validate_roi([1920, 1080, 400, 200]) is True

    def test_string_integers_coerce(self):
        # validate_roi tries int() conversion, so string ints are valid
        assert validate_roi(["10", "20", "100", "40"]) is True

    # ── invalid inputs ────────────────────────────────────────────────────────

    def test_negative_x_fails(self):
        assert validate_roi([-1, 0, 100, 40]) is False

    def test_negative_y_fails(self):
        assert validate_roi([0, -1, 100, 40]) is False

    def test_zero_width_fails(self):
        assert validate_roi([0, 0, 0, 40]) is False

    def test_zero_height_fails(self):
        assert validate_roi([0, 0, 100, 0]) is False

    def test_too_few_elements_fails(self):
        assert validate_roi([10, 20, 100]) is False

    def test_too_many_elements_fails(self):
        assert validate_roi([10, 20, 100, 40, 5]) is False

    def test_empty_list_fails(self):
        assert validate_roi([]) is False

    def test_non_sequence_fails(self):
        assert validate_roi(42) is False  # type: ignore[arg-type]

    def test_none_fails(self):
        assert validate_roi(None) is False  # type: ignore[arg-type]

    def test_non_numeric_strings_fail(self):
        assert validate_roi(["a", "b", "c", "d"]) is False

    def test_float_values_pass_after_coercion(self):
        # float values truncate to int; width/height must remain >= 1
        assert validate_roi([10.5, 20.7, 100.0, 40.9]) is True

    def test_negative_width_fails(self):
        assert validate_roi([0, 0, -10, 40]) is False


# ─────────────────────────────────────────────────────────────────────────────
# mode_exists / roi_area
# ─────────────────────────────────────────────────────────────────────────────

class TestModeExists:

    def test_known_modes_return_true(self):
        for m in ("coord", "hp", "mp", "minimap", "battle-list"):
            assert mode_exists(m) is True, f"Expected True for mode {m!r}"

    def test_unknown_mode_returns_false(self):
        assert mode_exists("nonexistent") is False

    def test_empty_string_returns_false(self):
        assert mode_exists("") is False

    def test_all_keyword_is_not_a_mode(self):
        # 'all' is a CLI alias but not in _MODE_FNS
        assert mode_exists("all") is False

    def test_case_sensitive(self):
        assert mode_exists("HP") is False  # 'hp' exists, 'HP' does not

    def test_returns_bool(self):
        assert isinstance(mode_exists("coord"), bool)


class TestRoiArea:

    def test_basic_area(self):
        assert roi_area([0, 0, 10, 5]) == 50

    def test_single_pixel(self):
        assert roi_area([0, 0, 1, 1]) == 1

    def test_large_roi(self):
        assert roi_area([100, 200, 300, 400]) == 120_000

    def test_invalid_roi_zero_width(self):
        assert roi_area([0, 0, 0, 10]) == 0

    def test_invalid_roi_negative_x(self):
        assert roi_area([-1, 0, 10, 10]) == 0

    def test_invalid_roi_none(self):
        assert roi_area(None) == 0  # type: ignore[arg-type]

    def test_tuple_roi(self):
        assert roi_area((0, 0, 20, 30)) == 600

    def test_float_roi_truncates(self):
        # int(10.9) == 10, int(5.9) == 5 → area = 50
        assert roi_area([0, 0, 10.9, 5.9]) == pytest.approx(50, abs=5)


# ─────────────────────────────────────────────────────────────────────────────
class TestRoiAspectRatio:

    def test_landscape_roi(self):
        assert roi_aspect_ratio([0, 0, 100, 50]) == pytest.approx(2.0)

    def test_portrait_roi(self):
        assert roi_aspect_ratio([0, 0, 50, 100]) == pytest.approx(0.5)

    def test_square_roi(self):
        assert roi_aspect_ratio([0, 0, 80, 80]) == pytest.approx(1.0)

    def test_invalid_roi_returns_zero(self):
        assert roi_aspect_ratio(None) == 0.0  # type: ignore[arg-type]

    def test_invalid_zero_width_returns_zero(self):
        assert roi_aspect_ratio([0, 0, 0, 10]) == 0.0

    def test_returns_float(self):
        assert isinstance(roi_aspect_ratio([0, 0, 100, 50]), float)


class TestRoiCenter:

    def test_basic_center(self):
        assert roi_center([10, 20, 100, 60]) == (60, 50)

    def test_origin_roi(self):
        cx, cy = roi_center([0, 0, 200, 100])
        assert cx == 100 and cy == 50

    def test_single_pixel_roi(self):
        assert roi_center([5, 7, 1, 1]) == (5, 7)

    def test_invalid_roi_returns_origin(self):
        assert roi_center(None) == (0, 0)  # type: ignore[arg-type]

    def test_returns_tuple_of_ints(self):
        cx, cy = roi_center([10, 20, 100, 60])
        assert isinstance(cx, int) and isinstance(cy, int)


class TestRoiOverlaps:

    def test_fully_overlapping_same_roi(self):
        assert roi_overlaps([0, 0, 100, 100], [0, 0, 100, 100]) is True

    def test_partial_overlap(self):
        assert roi_overlaps([0, 0, 50, 50], [25, 25, 50, 50]) is True

    def test_no_overlap_separate(self):
        assert roi_overlaps([0, 0, 50, 50], [100, 100, 50, 50]) is False

    def test_edge_touching_is_not_overlap(self):
        # ROI A ends at x=50; ROI B starts at x=50 — share edge only
        assert roi_overlaps([0, 0, 50, 50], [50, 0, 50, 50]) is False

    def test_contained_roi_overlaps(self):
        assert roi_overlaps([0, 0, 200, 200], [50, 50, 10, 10]) is True

    def test_invalid_first_roi_returns_false(self):
        assert roi_overlaps(None, [0, 0, 10, 10]) is False  # type: ignore[arg-type]

    def test_invalid_second_roi_returns_false(self):
        assert roi_overlaps([0, 0, 10, 10], None) is False  # type: ignore[arg-type]

    def test_returns_bool(self):
        assert isinstance(roi_overlaps([0, 0, 50, 50], [0, 0, 50, 50]), bool)
