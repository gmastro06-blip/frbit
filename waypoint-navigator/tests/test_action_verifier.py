"""Tests for src/action_verifier.py"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.action_verifier import (
    ActionVerificationError,
    verify_frame_valid,
    verify_hp_changed,
    verify_mp_changed,
    verify_position_changed,
    verify_target_selected,
    with_retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord(x: int, y: int, z: int):
    return SimpleNamespace(x=x, y=y, z=z)


def _make_frame(mean_val: int = 128) -> np.ndarray:
    """Return a small BGR frame with a given mean brightness."""
    return np.full((100, 100, 3), mean_val, dtype=np.uint8)


# ---------------------------------------------------------------------------
# verify_position_changed
# ---------------------------------------------------------------------------

class TestVerifyPositionChanged:
    def test_returns_true_when_position_differs(self):
        old = _make_coord(100, 200, 7)
        new = _make_coord(101, 200, 7)
        radar = MagicMock()
        radar.read.return_value = new
        fg = MagicMock(return_value=_make_frame())

        result = verify_position_changed(radar, old, fg, timeout=0.5, poll_interval=0.05)
        assert result is True

    def test_returns_false_when_position_same(self):
        old = _make_coord(100, 200, 7)
        radar = MagicMock()
        radar.read.return_value = old  # same coords
        fg = MagicMock(return_value=_make_frame())

        result = verify_position_changed(radar, old, fg, timeout=0.3, poll_interval=0.05)
        assert result is False

    def test_returns_true_when_old_pos_none_and_new_detected(self):
        radar = MagicMock()
        radar.read.return_value = _make_coord(50, 50, 7)
        fg = MagicMock(return_value=_make_frame())

        result = verify_position_changed(radar, None, fg, timeout=0.3, poll_interval=0.05)
        assert result is True

    def test_returns_false_when_frame_getter_returns_none(self):
        radar = MagicMock()
        fg = MagicMock(return_value=None)

        result = verify_position_changed(radar, _make_coord(1, 1, 7), fg, timeout=0.3, poll_interval=0.05)
        assert result is False


# ---------------------------------------------------------------------------
# verify_hp_changed / verify_mp_changed
# ---------------------------------------------------------------------------

class TestVerifyHpChanged:
    def test_hp_went_up(self):
        det = MagicMock()
        det.read_bars.return_value = (80, 50)
        fg = MagicMock(return_value=_make_frame())

        assert verify_hp_changed(det, 60, fg, direction="up", timeout=0.3, poll_interval=0.05) is True

    def test_hp_went_down(self):
        det = MagicMock()
        det.read_bars.return_value = (30, 50)
        fg = MagicMock(return_value=_make_frame())

        assert verify_hp_changed(det, 60, fg, direction="down", timeout=0.3, poll_interval=0.05) is True

    def test_hp_unchanged(self):
        det = MagicMock()
        det.read_bars.return_value = (60, 50)
        fg = MagicMock(return_value=_make_frame())

        assert verify_hp_changed(det, 60, fg, direction="up", timeout=0.3, poll_interval=0.05) is False

    def test_old_hp_none_returns_false(self):
        det = MagicMock()
        fg = MagicMock(return_value=_make_frame())
        assert verify_hp_changed(det, None, fg, timeout=0.1) is False


class TestVerifyMpChanged:
    def test_mp_went_up(self):
        det = MagicMock()
        det.read_bars.return_value = (100, 80)
        fg = MagicMock(return_value=_make_frame())

        assert verify_mp_changed(det, 50, fg, direction="up", timeout=0.3, poll_interval=0.05) is True


# ---------------------------------------------------------------------------
# verify_target_selected
# ---------------------------------------------------------------------------

class TestVerifyTargetSelected:
    def test_target_selected(self):
        cm = SimpleNamespace(is_in_combat=True)
        assert verify_target_selected(cm, timeout=0.3, poll_interval=0.05) is True

    def test_no_target(self):
        cm = SimpleNamespace(is_in_combat=False)
        assert verify_target_selected(cm, timeout=0.3, poll_interval=0.05) is False


# ---------------------------------------------------------------------------
# verify_frame_valid
# ---------------------------------------------------------------------------

class TestVerifyFrameValid:
    def test_valid_frame(self):
        fg = MagicMock(return_value=_make_frame(128))
        assert verify_frame_valid(fg, timeout=0.3, poll_interval=0.05) is True

    def test_black_frame(self):
        fg = MagicMock(return_value=_make_frame(0))
        assert verify_frame_valid(fg, timeout=0.3, poll_interval=0.05) is False

    def test_none_frame(self):
        fg = MagicMock(return_value=None)
        assert verify_frame_valid(fg, timeout=0.3, poll_interval=0.05) is False


# ---------------------------------------------------------------------------
# with_retry decorator
# ---------------------------------------------------------------------------

class TestWithRetry:
    def test_succeeds_first_try(self):
        @with_retry(max_attempts=3)
        def action():
            return "ok"

        assert action() == "ok"

    def test_retries_on_exception(self):
        call_count = 0

        @with_retry(max_attempts=3, delay_between=0.01)
        def action():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("fail")
            return "ok"

        assert action() == "ok"
        assert call_count == 3

    def test_raises_after_all_attempts(self):
        @with_retry(max_attempts=2, delay_between=0.01)
        def action():
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            action()

    def test_verification_failure(self):
        @with_retry(max_attempts=2, verify=lambda r: r > 10, delay_between=0.01)
        def action():
            return 5  # always returns 5, which fails verify

        with pytest.raises(ActionVerificationError):
            action()

    def test_verification_success_second_try(self):
        results = iter([5, 15])

        @with_retry(max_attempts=3, verify=lambda r: r > 10, delay_between=0.01)
        def action():
            return next(results)

        assert action() == 15

    def test_on_fail_callback(self):
        failures = []

        @with_retry(max_attempts=2, delay_between=0.01, on_fail=lambda a, e: failures.append(a))
        def action():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            action()

        assert failures == [1, 2]
