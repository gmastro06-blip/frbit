"""Tests for src.humanizer module."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src import humanizer
from src.humanizer import (
    JITTER_PCT,
    _fatigue_factor,
    humanize,
    jittered_sleep,
    micro_pause,
    reset_fatigue,
    set_jitter,
)


# ═══════════════════════════════════════════════════════════════════════════════
# set_jitter / JITTER_PCT
# ═══════════════════════════════════════════════════════════════════════════════


class TestSetJitter:
    def setup_method(self):
        set_jitter(0.15)  # restore default before each test

    def test_default_jitter(self):
        assert humanizer.JITTER_PCT == 0.15

    def test_set_jitter_updates(self):
        set_jitter(0.3)
        assert humanizer.JITTER_PCT == 0.3

    def test_set_jitter_clamps_high(self):
        set_jitter(5.0)
        assert humanizer.JITTER_PCT == 1.0

    def test_set_jitter_clamps_low(self):
        set_jitter(-0.5)
        assert humanizer.JITTER_PCT == 0.0

    def teardown_method(self):
        set_jitter(0.15)


# ═══════════════════════════════════════════════════════════════════════════════
# reset_fatigue / _fatigue_factor
# ═══════════════════════════════════════════════════════════════════════════════


class TestFatigue:
    def setup_method(self):
        # Reset fatigue state
        with humanizer._jitter_lock:
            humanizer._fatigue_start = None

    def test_no_fatigue_when_not_started(self):
        assert _fatigue_factor() == 1.0

    def test_fatigue_factor_increases_over_time(self):
        reset_fatigue()
        # Immediately after reset, factor ≈ 1.0
        f0 = _fatigue_factor()
        assert 0.99 < f0 <= 1.01

        # Simulate 2 hours elapsed
        with humanizer._jitter_lock:
            humanizer._fatigue_start = time.monotonic() - 2 * 3600
        f2 = _fatigue_factor()
        assert f2 > 1.0

    def test_fatigue_caps_at_max(self):
        # Simulate 10 hours (well past ramp)
        with humanizer._jitter_lock:
            humanizer._fatigue_start = time.monotonic() - 10 * 3600
        f = _fatigue_factor()
        assert abs(f - (1.0 + humanizer._FATIGUE_MAX_EXTRA)) < 0.01

    def test_reset_fatigue_resets_timer(self):
        with humanizer._jitter_lock:
            humanizer._fatigue_start = time.monotonic() - 10 * 3600
        assert _fatigue_factor() > 1.1
        reset_fatigue()
        assert _fatigue_factor() < 1.01

    def teardown_method(self):
        with humanizer._jitter_lock:
            humanizer._fatigue_start = None


# ═══════════════════════════════════════════════════════════════════════════════
# humanize
# ═══════════════════════════════════════════════════════════════════════════════


class TestHumanize:
    def setup_method(self):
        set_jitter(0.15)
        with humanizer._jitter_lock:
            humanizer._fatigue_start = None

    def test_minimum_value(self):
        val = humanize(0.0)
        assert val >= 0.001

    def test_result_near_base(self):
        results = [humanize(1.0) for _ in range(100)]
        avg = sum(results) / len(results)
        # With 15% jitter, average should be near 1.0
        assert 0.85 < avg < 1.15

    def test_zero_jitter_returns_base(self):
        val = humanize(0.5, pct=0.0)
        assert abs(val - 0.5) < 0.01  # only fatigue can differ

    def test_custom_pct_overrides_global(self):
        set_jitter(0.0)
        # With global at 0, custom pct=0.5 should still jitter
        results = [humanize(1.0, pct=0.5) for _ in range(50)]
        # They shouldn't all be identical
        assert max(results) - min(results) > 0.01

    def test_fatigue_increases_result(self):
        val_no_fatigue = humanize(1.0, pct=0.0)
        with humanizer._jitter_lock:
            humanizer._fatigue_start = time.monotonic() - 10 * 3600
        val_fatigued = humanize(1.0, pct=0.0)
        assert val_fatigued > val_no_fatigue

    def teardown_method(self):
        set_jitter(0.15)
        with humanizer._jitter_lock:
            humanizer._fatigue_start = None


# ═══════════════════════════════════════════════════════════════════════════════
# micro_pause
# ═══════════════════════════════════════════════════════════════════════════════


class TestMicroPause:
    @patch("src.humanizer.random.random", return_value=0.001)  # < 0.02 threshold
    @patch("src.humanizer.random.uniform", return_value=0.8)
    @patch("src.humanizer.time.sleep")
    def test_pause_triggers(self, mock_sleep, mock_uniform, mock_random):
        result = micro_pause()
        assert result == 0.8
        mock_sleep.assert_called_once_with(0.8)

    @patch("src.humanizer.random.random", return_value=0.5)  # > 0.02 threshold
    def test_no_pause(self, mock_random):
        result = micro_pause()
        assert result == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# jittered_sleep
# ═══════════════════════════════════════════════════════════════════════════════


class TestJitteredSleep:
    @patch("src.humanizer.time.sleep")
    @patch("src.humanizer.micro_pause", return_value=0.0)
    def test_calls_sleep(self, mock_micro, mock_sleep):
        set_jitter(0.0)
        with humanizer._jitter_lock:
            humanizer._fatigue_start = None
        result = jittered_sleep(0.5, pct=0.0)
        mock_sleep.assert_called_once()
        # Should sleep approximately 0.5s
        slept = mock_sleep.call_args[0][0]
        assert 0.49 < slept < 0.51

    def teardown_method(self):
        set_jitter(0.15)
        with humanizer._jitter_lock:
            humanizer._fatigue_start = None
