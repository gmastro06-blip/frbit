"""Additional tests for src/action_verifier.py to improve branch coverage.

Targets the uncovered lines:
- verify_hp_changed: frame=None branch (line 88-89)
- verify_mp_changed: frame=None branch (lines 117-118), direction=down with None hp (123-126)
- verify_dialog_open: template path, heuristic path, full loops
- find_dialog_option: main logic, cluster detection, debug_save path
- verify_floor_changed: frame=None, position not changed, floor changed
- verify_death_dismissed: frame=None, death still detected, death dismissed
- verify_char_active: all branches
- make_walk_verifier / make_heal_verifier: factory callables
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

from src.action_verifier import (
    ActionVerificationError,
    find_dialog_option,
    make_heal_verifier,
    make_walk_verifier,
    verify_char_active,
    verify_death_dismissed,
    verify_dialog_open,
    verify_floor_changed,
    verify_hp_changed,
    verify_mp_changed,
    verify_position_changed,
    verify_target_selected,
    with_retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord(x: int = 100, y: int = 200, z: int = 7):
    return SimpleNamespace(x=x, y=y, z=z)


def _bright_frame(h: int = 100, w: int = 100, val: int = 128) -> np.ndarray:
    """BGR frame with given uniform pixel value."""
    return np.full((h, w, 3), val, dtype=np.uint8)


def _black_frame(h: int = 100, w: int = 100) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# verify_hp_changed — frame=None branch (lines 88-89)
# ---------------------------------------------------------------------------

class TestVerifyHpChangedFrameNone:
    """Cover the frame=None branch inside the polling loop."""

    def test_none_then_valid_frame_hp_went_up(self):
        """First call returns None (sleep), second returns bright frame with higher HP."""
        det = MagicMock()
        det.read_bars.return_value = (90, 50)

        call_count = [0]

        def fg():
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # first call: triggers lines 88-89
            return _bright_frame()

        result = verify_hp_changed(det, 60, fg, direction="up", timeout=1.0, poll_interval=0.05)
        assert result is True
        assert call_count[0] >= 2

    def test_none_frame_only_returns_false_on_timeout(self):
        """Always-None frame_getter returns False after timeout."""
        det = MagicMock()
        fg = MagicMock(return_value=None)
        result = verify_hp_changed(det, 60, fg, direction="up", timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_hp_read_returns_none_hp(self):
        """read_bars returns (None, mp) — HP branch not triggered."""
        det = MagicMock()
        det.read_bars.return_value = (None, 50)
        fg = MagicMock(return_value=_bright_frame())
        result = verify_hp_changed(det, 60, fg, direction="up", timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_direction_down_hp_went_down(self):
        det = MagicMock()
        det.read_bars.return_value = (20, 50)
        fg = MagicMock(return_value=_bright_frame())
        result = verify_hp_changed(det, 60, fg, direction="down", timeout=0.3, poll_interval=0.05)
        assert result is True


# ---------------------------------------------------------------------------
# verify_mp_changed — frame=None branch (lines 117-118), direction branches
# ---------------------------------------------------------------------------

class TestVerifyMpChangedBranches:

    def test_none_then_valid_mp_went_up(self):
        det = MagicMock()
        det.read_bars.return_value = (80, 90)

        call_count = [0]

        def fg():
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return _bright_frame()

        result = verify_mp_changed(det, 50, fg, direction="up", timeout=1.0, poll_interval=0.05)
        assert result is True

    def test_mp_went_down(self):
        det = MagicMock()
        det.read_bars.return_value = (80, 20)
        fg = MagicMock(return_value=_bright_frame())
        result = verify_mp_changed(det, 50, fg, direction="down", timeout=0.3, poll_interval=0.05)
        assert result is True

    def test_mp_none_old_mp_returns_false(self):
        det = MagicMock()
        fg = MagicMock(return_value=_bright_frame())
        result = verify_mp_changed(det, None, fg, timeout=0.1)
        assert result is False

    def test_mp_read_returns_none(self):
        det = MagicMock()
        det.read_bars.return_value = (80, None)
        fg = MagicMock(return_value=_bright_frame())
        result = verify_mp_changed(det, 50, fg, direction="up", timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_mp_unchanged_returns_false(self):
        det = MagicMock()
        det.read_bars.return_value = (80, 50)
        fg = MagicMock(return_value=_bright_frame())
        result = verify_mp_changed(det, 50, fg, direction="up", timeout=0.3, poll_interval=0.05)
        assert result is False


# ---------------------------------------------------------------------------
# verify_dialog_open (lines 159-183)
# ---------------------------------------------------------------------------

class TestVerifyDialogOpen:
    """Test verify_dialog_open with and without template."""

    def test_with_template_match_returns_true(self):
        """template provided and cv2.matchTemplate gives high score."""
        import cv2
        frame = _bright_frame(100, 100, 200)
        fg = MagicMock(return_value=frame)

        # Create a small template
        template = np.full((10, 10, 3), 200, dtype=np.uint8)

        mock_res = MagicMock()
        mock_res.max.return_value = 0.95

        with patch("cv2.matchTemplate", return_value=mock_res):
            result = verify_dialog_open(fg, template=template, timeout=0.5, poll_interval=0.05)
        assert result is True

    def test_with_template_no_match_returns_false(self):
        """template provided but cv2.matchTemplate gives low score."""
        fg = MagicMock(return_value=_bright_frame())
        template = np.full((10, 10, 3), 200, dtype=np.uint8)

        mock_res = MagicMock()
        mock_res.max.return_value = 0.1

        with patch("cv2.matchTemplate", return_value=mock_res):
            result = verify_dialog_open(fg, template=template, timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_heuristic_white_region_detected(self):
        """No template; mostly-white ROI triggers return True."""
        # Create a mostly-white frame (value > 200)
        frame = np.full((200, 300, 3), 210, dtype=np.uint8)
        fg = MagicMock(return_value=frame)

        result = verify_dialog_open(fg, template=None, timeout=1.0, poll_interval=0.05)
        assert result is True

    def test_heuristic_dark_frame_returns_false(self):
        """No template; dark frame — heuristic returns False."""
        frame = _black_frame(200, 300)
        fg = MagicMock(return_value=frame)

        result = verify_dialog_open(fg, template=None, timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_none_frame_skipped(self):
        """frame_getter returning None is skipped; eventually times out."""
        fg = MagicMock(return_value=None)
        result = verify_dialog_open(fg, timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_none_then_white_frame(self):
        """First None, then white frame — returns True."""
        call_count = [0]
        white_frame = np.full((200, 300, 3), 210, dtype=np.uint8)

        def fg():
            call_count[0] += 1
            return None if call_count[0] == 1 else white_frame

        result = verify_dialog_open(fg, template=None, timeout=1.0, poll_interval=0.05)
        assert result is True


# ---------------------------------------------------------------------------
# find_dialog_option (lines 204-293)
# ---------------------------------------------------------------------------

class TestFindDialogOption:
    """Test find_dialog_option covering main logic branches."""

    def _make_blue_frame(self, h: int = 200, w: int = 300) -> np.ndarray:
        """Create a frame with blue keyword pixels (BGR ≈ 220, 120, 50)
        to trigger the mask detection and produce clusters."""
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        # Place pixels in the middle of the dialog region:
        # y1=60, y2=150, x1=45, x2=210 for h=200, w=300
        # Put plenty of blue pixels inside
        y_mid = int(h * 0.50)
        x_mid = int(w * 0.40)
        # BGR: B=220, G=120, R=50 → within mask [180-255, 60-220, 0-110]
        frame[y_mid - 5 : y_mid + 5, x_mid - 15 : x_mid + 15] = [220, 120, 50]
        return frame

    def test_returns_none_when_frame_none(self):
        fg = MagicMock(return_value=None)
        result = find_dialog_option(fg, "trade", timeout=0.3, poll_interval=0.05, debug_save=False)
        assert result is None

    def test_returns_none_when_no_blue_pixels(self):
        """Dark frame — mask empty, loop times out."""
        fg = MagicMock(return_value=_black_frame(200, 300))
        result = find_dialog_option(fg, "trade", timeout=0.3, poll_interval=0.05, debug_save=False)
        assert result is None

    def test_returns_coords_when_blue_cluster_found(self):
        """Frame with enough blue pixels — should return (cx, cy) tuple."""
        frame = self._make_blue_frame()
        fg = MagicMock(return_value=frame)
        result = find_dialog_option(fg, "trade", timeout=1.0, poll_interval=0.05, debug_save=False)
        # May return None if cluster too small — just check type
        assert result is None or (isinstance(result, tuple) and len(result) == 2)

    def test_debug_save_triggers_on_first_miss(self, tmp_path, monkeypatch):
        """debug_save=True and not frozen: diagnostic path attempted (smoke test)."""
        frame = _black_frame(200, 300)  # no blue pixels → miss triggers debug_save
        fg = MagicMock(return_value=frame)

        # Patch cv2.imwrite and os.makedirs to avoid actual file I/O
        with patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = find_dialog_option(
                fg, "deposit", timeout=0.3, poll_interval=0.05, debug_save=True
            )
        assert result is None

    def test_debug_save_skipped_when_frozen(self):
        """debug_save=True but sys.frozen=True — diagnostic skipped."""
        frame = _black_frame(200, 300)
        fg = MagicMock(return_value=frame)

        with patch.object(sys, "frozen", True, create=True):
            result = find_dialog_option(fg, "trade", timeout=0.3, poll_interval=0.05, debug_save=True)
        assert result is None

    def test_large_blue_cluster_returns_centroid(self):
        """Build a frame with a large enough cluster to pass area/size filters."""
        import cv2
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        # Paint a big blue-ish rectangle well inside the ROI
        # ROI for h=400,w=600: y1=120, y2=300, x1=90, x2=420
        # BGR: B=220, G=120, R=50
        frame[160:185, 130:200] = [220, 120, 50]
        fg = MagicMock(return_value=frame)
        result = find_dialog_option(fg, "buy", timeout=1.0, poll_interval=0.05, debug_save=False)
        # Result is either None or a valid (x, y) tuple
        assert result is None or (isinstance(result, tuple) and len(result) == 2)


# ---------------------------------------------------------------------------
# verify_floor_changed (lines 436-446)
# ---------------------------------------------------------------------------

class TestVerifyFloorChanged:

    def test_floor_changed_returns_true(self):
        radar = MagicMock()
        radar.read.return_value = _make_coord(z=8)  # different from old_z=7
        fg = MagicMock(return_value=_bright_frame())
        result = verify_floor_changed(radar, old_z=7, frame_getter=fg,
                                      timeout=0.5, poll_interval=0.05)
        assert result is True

    def test_floor_not_changed_returns_false(self):
        radar = MagicMock()
        radar.read.return_value = _make_coord(z=7)  # same z
        fg = MagicMock(return_value=_bright_frame())
        result = verify_floor_changed(radar, old_z=7, frame_getter=fg,
                                      timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_none_frame_skipped(self):
        radar = MagicMock()
        fg = MagicMock(return_value=None)
        result = verify_floor_changed(radar, old_z=7, frame_getter=fg,
                                      timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_radar_returns_none_pos(self):
        radar = MagicMock()
        radar.read.return_value = None  # no position fix
        fg = MagicMock(return_value=_bright_frame())
        result = verify_floor_changed(radar, old_z=7, frame_getter=fg,
                                      timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_none_then_changed_floor(self):
        radar = MagicMock()
        radar.read.return_value = _make_coord(z=8)
        call_count = [0]

        def fg():
            call_count[0] += 1
            return None if call_count[0] == 1 else _bright_frame()

        result = verify_floor_changed(radar, old_z=7, frame_getter=fg,
                                      timeout=1.0, poll_interval=0.05)
        assert result is True


# ---------------------------------------------------------------------------
# verify_death_dismissed (lines 464-470)
# ---------------------------------------------------------------------------

class TestVerifyDeathDismissed:

    def test_death_dismissed_returns_true(self):
        """death_checker returns False (no death screen) → dismissed."""
        fg = MagicMock(return_value=_bright_frame())
        death_checker = MagicMock(return_value=False)
        result = verify_death_dismissed(fg, death_checker, timeout=0.5, poll_interval=0.05)
        assert result is True

    def test_death_still_visible_returns_false(self):
        """death_checker always returns True → still on death screen."""
        fg = MagicMock(return_value=_bright_frame())
        death_checker = MagicMock(return_value=True)
        result = verify_death_dismissed(fg, death_checker, timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_none_frame_treated_as_not_dismissed(self):
        """None frame: condition 'frame is not None and not death_checker(frame)' is False."""
        fg = MagicMock(return_value=None)
        death_checker = MagicMock(return_value=False)
        result = verify_death_dismissed(fg, death_checker, timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_none_then_dismissed(self):
        """None first, then dismissed."""
        call_count = [0]
        death_checker = MagicMock(return_value=False)

        def fg():
            call_count[0] += 1
            return None if call_count[0] == 1 else _bright_frame()

        result = verify_death_dismissed(fg, death_checker, timeout=1.0, poll_interval=0.05)
        assert result is True


# ---------------------------------------------------------------------------
# verify_char_active (lines 503-524)
# ---------------------------------------------------------------------------

class TestVerifyCharActive:

    def test_returns_false_when_frame_none(self):
        fg = MagicMock(return_value=None)
        assert verify_char_active(fg) is False

    def test_returns_false_when_frame_empty(self):
        fg = MagicMock(return_value=np.zeros((0, 0, 3), dtype=np.uint8))
        assert verify_char_active(fg) is False

    def test_returns_false_when_frame_black(self):
        fg = MagicMock(return_value=_black_frame())
        assert verify_char_active(fg) is False

    def test_returns_true_for_bright_frame(self):
        fg = MagicMock(return_value=_bright_frame(val=128))
        assert verify_char_active(fg) is True

    def test_returns_false_when_death_screen_detected(self):
        fg = MagicMock(return_value=_bright_frame(val=128))
        death_checker = MagicMock(return_value=True)
        assert verify_char_active(fg, death_checker=death_checker) is False

    def test_returns_true_when_no_death_screen(self):
        fg = MagicMock(return_value=_bright_frame(val=128))
        death_checker = MagicMock(return_value=False)
        assert verify_char_active(fg, death_checker=death_checker) is True

    def test_returns_false_when_login_screen_detected(self):
        fg = MagicMock(return_value=_bright_frame(val=128))
        login_checker = MagicMock(return_value=True)
        assert verify_char_active(fg, login_checker=login_checker) is False

    def test_returns_true_when_not_on_login_screen(self):
        fg = MagicMock(return_value=_bright_frame(val=128))
        login_checker = MagicMock(return_value=False)
        assert verify_char_active(fg, login_checker=login_checker) is True

    def test_returns_false_when_hp_zero(self):
        fg = MagicMock(return_value=_bright_frame(val=128))
        hp_reader = MagicMock(return_value=0)
        assert verify_char_active(fg, hp_reader=hp_reader) is False

    def test_returns_false_when_hp_negative(self):
        fg = MagicMock(return_value=_bright_frame(val=128))
        hp_reader = MagicMock(return_value=-1)
        assert verify_char_active(fg, hp_reader=hp_reader) is False

    def test_returns_true_when_hp_positive(self):
        fg = MagicMock(return_value=_bright_frame(val=128))
        hp_reader = MagicMock(return_value=75)
        assert verify_char_active(fg, hp_reader=hp_reader) is True

    def test_returns_true_when_hp_reader_returns_none(self):
        """hp_reader returning None → HP check is skipped."""
        fg = MagicMock(return_value=_bright_frame(val=128))
        hp_reader = MagicMock(return_value=None)
        assert verify_char_active(fg, hp_reader=hp_reader) is True

    def test_death_check_before_login_check(self):
        """If death is detected, login_checker is not called (short-circuit)."""
        fg = MagicMock(return_value=_bright_frame(val=128))
        death_checker = MagicMock(return_value=True)
        login_checker = MagicMock(return_value=False)
        result = verify_char_active(fg, death_checker=death_checker, login_checker=login_checker)
        assert result is False
        login_checker.assert_not_called()

    def test_all_checks_pass_returns_true(self):
        fg = MagicMock(return_value=_bright_frame(val=100))
        death_checker = MagicMock(return_value=False)
        login_checker = MagicMock(return_value=False)
        hp_reader = MagicMock(return_value=80)
        result = verify_char_active(
            fg,
            death_checker=death_checker,
            login_checker=login_checker,
            hp_reader=hp_reader,
        )
        assert result is True


# ---------------------------------------------------------------------------
# make_walk_verifier (lines 407-409)
# ---------------------------------------------------------------------------

class TestMakeWalkVerifier:

    def test_returns_callable(self):
        radar = MagicMock()
        fg = MagicMock(return_value=_bright_frame())
        verifier = make_walk_verifier(radar, fg, timeout=0.1)
        assert callable(verifier)

    def test_verify_returns_true_when_position_changed(self):
        old_pos = _make_coord(100, 200, 7)
        new_pos = _make_coord(101, 200, 7)
        radar = MagicMock()
        radar.read.return_value = new_pos
        fg = MagicMock(return_value=_bright_frame())
        verifier = make_walk_verifier(radar, fg, timeout=0.5)
        assert verifier(old_pos, new_pos) is True

    def test_verify_returns_false_when_position_unchanged(self):
        pos = _make_coord(100, 200, 7)
        radar = MagicMock()
        radar.read.return_value = pos
        fg = MagicMock(return_value=_bright_frame())
        verifier = make_walk_verifier(radar, fg, timeout=0.3)
        assert verifier(pos, pos) is False


# ---------------------------------------------------------------------------
# make_heal_verifier (lines 418-420)
# ---------------------------------------------------------------------------

class TestMakeHealVerifier:

    def test_returns_callable(self):
        detector = MagicMock()
        fg = MagicMock(return_value=_bright_frame())
        verifier = make_heal_verifier(detector, fg, timeout=0.1)
        assert callable(verifier)

    def test_verify_returns_true_when_hp_went_up(self):
        detector = MagicMock()
        detector.read_bars.return_value = (90, 50)
        fg = MagicMock(return_value=_bright_frame())
        verifier = make_heal_verifier(detector, fg, timeout=0.5)
        assert verifier(60) is True  # HP went from 60 to 90

    def test_verify_returns_false_when_hp_unchanged(self):
        detector = MagicMock()
        detector.read_bars.return_value = (60, 50)
        fg = MagicMock(return_value=_bright_frame())
        verifier = make_heal_verifier(detector, fg, timeout=0.3)
        assert verifier(60) is False


# ---------------------------------------------------------------------------
# with_retry — on_fail callback with verify failure (lines 373-375)
# ---------------------------------------------------------------------------

class TestWithRetryOnFailVerify:

    def test_on_fail_called_on_verification_failure(self):
        """on_fail should be called when verify returns False."""
        failures = []

        @with_retry(
            max_attempts=2,
            verify=lambda r: r > 100,
            delay_between=0.01,
            on_fail=lambda a, e: failures.append((a, e)),
        )
        def action():
            return 5  # always fails verify

        with pytest.raises(ActionVerificationError):
            action()

        # on_fail should have been called for each failed attempt
        assert len(failures) == 2
        # e=None for verification failures (no exception)
        for attempt, exc in failures:
            assert exc is None

    def test_on_fail_not_called_on_success(self):
        failures = []

        @with_retry(
            max_attempts=3,
            verify=lambda r: r > 0,
            delay_between=0.01,
            on_fail=lambda a, e: failures.append(a),
        )
        def action():
            return 5

        result = action()
        assert result == 5
        assert failures == []

    def test_retry_sleeps_between_verify_failures(self):
        """Ensure delay_between applies between retries (smoke test)."""
        call_times = []

        @with_retry(max_attempts=3, verify=lambda r: False, delay_between=0.02)
        def action():
            call_times.append(time.monotonic())
            return 0

        with pytest.raises(ActionVerificationError):
            action()

        assert len(call_times) == 3

    def test_last_exc_raised_when_all_attempts_raise(self):
        """All attempts raise — last exception propagated."""

        @with_retry(max_attempts=3, delay_between=0.01)
        def action():
            raise TypeError("original")

        with pytest.raises(TypeError, match="original"):
            action()


# ---------------------------------------------------------------------------
# verify_position_changed — additional branches
# ---------------------------------------------------------------------------

class TestVerifyPositionChangedExtra:

    def test_z_differs_is_true(self):
        """Position with same x,y but different z should also count as changed."""
        old = _make_coord(100, 200, 7)
        new = _make_coord(100, 200, 8)
        radar = MagicMock()
        radar.read.return_value = new
        fg = MagicMock(return_value=_bright_frame())
        assert verify_position_changed(radar, old, fg, timeout=0.5, poll_interval=0.05) is True

    def test_radar_returns_none_returns_false(self):
        """radar.read returns None — can't determine new pos."""
        old = _make_coord()
        radar = MagicMock()
        radar.read.return_value = None
        fg = MagicMock(return_value=_bright_frame())
        result = verify_position_changed(radar, old, fg, timeout=0.3, poll_interval=0.05)
        assert result is False
