"""
Tests for src/frame_watchdog.py

All tests are offline — no real frame capture, no real OS calls.
win32gui is mocked where window-visibility logic is tested.
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.frame_watchdog import FrameHealth, FrameWatchdog, FrameWatchdogConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _healthy_frame() -> np.ndarray:
    """Return a simple non-black, non-frozen frame."""
    frame = np.random.randint(30, 200, (100, 200, 3), dtype=np.uint8)
    return frame


def _black_frame() -> np.ndarray:
    return np.zeros((100, 200, 3), dtype=np.uint8)


def _watchdog(
    *,
    max_failures: int = 4,
    black_threshold: float = 8.0,
    frozen_streak: int = 3,
    restart_cooldown: float = 0.0,
) -> FrameWatchdog:
    cfg = FrameWatchdogConfig(
        max_failures=max_failures,
        black_threshold=black_threshold,
        frozen_streak=frozen_streak,
        restart_cooldown=restart_cooldown,
    )
    return FrameWatchdog(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# FrameHealth classification
# ─────────────────────────────────────────────────────────────────────────────

class TestAssessFrame:

    def test_none_getter_returns_healthy(self):
        wd = _watchdog()
        assert wd._assess_frame() == FrameHealth.HEALTHY

    def test_none_frame_returns_none_health(self):
        wd = _watchdog()
        wd.set_frame_getter(lambda: None)
        assert wd._assess_frame() == FrameHealth.NONE

    def test_black_frame_returns_black(self):
        wd = _watchdog()
        wd.set_frame_getter(_black_frame)
        assert wd._assess_frame() == FrameHealth.BLACK

    def test_healthy_frame_returns_healthy(self):
        frame = _healthy_frame()
        wd = _watchdog()
        wd.set_frame_getter(lambda: frame)
        assert wd._assess_frame() == FrameHealth.HEALTHY

    def test_frozen_frame_detected_after_streak(self):
        frame = np.full((100, 200, 3), 50, dtype=np.uint8)  # static
        wd = _watchdog(frozen_streak=3)
        wd.set_frame_getter(lambda: frame)
        # First call seeds _last_hash (not counted); then need frozen_streak more.
        for _ in range(4):
            result = wd._assess_frame()
        assert result == FrameHealth.FROZEN

    def test_changing_frames_not_frozen(self):
        counter = [0]
        def getter():
            counter[0] += 1
            # Ensure mean > black_threshold (8.0): use values 30–229
            val = (counter[0] % 200) + 30
            return np.full((100, 200, 3), val, dtype=np.uint8)
        wd = _watchdog(frozen_streak=3)
        wd.set_frame_getter(getter)
        for _ in range(5):
            result = wd._assess_frame()
        assert result == FrameHealth.HEALTHY


# ─────────────────────────────────────────────────────────────────────────────
# Restart behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestRestart:

    def test_restart_fn_called_after_max_failures(self):
        wd = _watchdog(max_failures=3)
        wd.set_frame_getter(lambda: None)
        restarted = []
        wd.set_restart_fn(lambda: restarted.append(1))
        for _ in range(3):
            wd._handle_health(FrameHealth.NONE)
        assert restarted, "restart_fn should have been called"

    def test_restart_fn_not_called_before_max_failures(self):
        wd = _watchdog(max_failures=4)
        wd.set_frame_getter(lambda: None)
        restarted = []
        wd.set_restart_fn(lambda: restarted.append(1))
        for _ in range(3):
            wd._handle_health(FrameHealth.NONE)
        assert not restarted

    def test_no_restart_fn_does_not_raise(self):
        wd = _watchdog(max_failures=2)
        for _ in range(3):
            wd._handle_health(FrameHealth.NONE)   # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# Window visibility — minimized window skips restart
# ─────────────────────────────────────────────────────────────────────────────

class TestWindowVisibility:

    def _wd_with_title(self, title: str = "Tibia") -> FrameWatchdog:
        wd = _watchdog(max_failures=2, restart_cooldown=0.0)
        wd.set_window_title(title)
        return wd

    def test_no_title_is_window_visible_returns_true(self):
        wd = _watchdog()
        assert wd._is_window_visible() is True

    def test_win32gui_unavailable_returns_true(self):
        wd = self._wd_with_title("Tibia")
        with patch.dict("sys.modules", {"win32gui": None}):
            assert wd._is_window_visible() is True

    def test_minimized_window_skips_restart(self):
        """When the game window is minimized and a BLACK streak fires, restart
        must NOT be called and frame_black_minimized must be emitted."""
        wd = self._wd_with_title("Tibia")
        restarted = []
        emitted: List[str] = []

        wd.set_restart_fn(lambda: restarted.append(1))

        bus = MagicMock()
        bus.emit.side_effect = lambda event, data: emitted.append(event)
        wd.set_event_bus(bus)

        mock_win32gui = MagicMock()
        mock_win32gui.IsWindow.return_value = True
        mock_win32gui.IsIconic.return_value = True   # window IS minimized
        mock_win32gui.FindWindow.return_value = 12345

        with patch.dict("sys.modules", {"win32gui": mock_win32gui}):
            for _ in range(3):
                wd._handle_health(FrameHealth.BLACK)

        assert not restarted, "restart_fn must NOT be called when window is minimized"
        assert "frame_black_minimized" in emitted

    def test_visible_window_triggers_normal_restart(self):
        """When the game window is visible and BLACK streak fires, restart IS called."""
        wd = self._wd_with_title("Tibia")
        restarted = []
        wd.set_restart_fn(lambda: restarted.append(1))

        mock_win32gui = MagicMock()
        mock_win32gui.IsWindow.return_value = True
        mock_win32gui.IsIconic.return_value = False   # window is NOT minimized
        mock_win32gui.FindWindow.return_value = 12345

        with patch.dict("sys.modules", {"win32gui": mock_win32gui}):
            for _ in range(3):
                wd._handle_health(FrameHealth.BLACK)

        assert restarted, "restart_fn must be called when window is visible"

    def test_window_not_found_treats_as_visible(self):
        """FindWindow returning 0 means window not found — treat as visible."""
        wd = self._wd_with_title("Tibia")
        restarted = []
        wd.set_restart_fn(lambda: restarted.append(1))

        mock_win32gui = MagicMock()
        mock_win32gui.IsWindow.return_value = False
        mock_win32gui.FindWindow.return_value = 0   # not found

        with patch.dict("sys.modules", {"win32gui": mock_win32gui}):
            for _ in range(3):
                wd._handle_health(FrameHealth.BLACK)

        assert restarted, "restart_fn must be called when window handle not found"

    def test_set_window_title_invalidates_hwnd_cache(self):
        wd = _watchdog()
        wd._hwnd_cache = 99999
        wd.set_window_title("NewTitle")
        assert wd._hwnd_cache == 0


# ─────────────────────────────────────────────────────────────────────────────
# Recovery events
# ─────────────────────────────────────────────────────────────────────────────

class TestRecovery:

    def test_recovered_event_emitted_on_first_healthy_after_failure(self):
        wd = _watchdog(max_failures=2)
        emitted: List[str] = []
        bus = MagicMock()
        bus.emit.side_effect = lambda ev, d: emitted.append(ev)
        wd.set_event_bus(bus)

        # Cause failure streak
        for _ in range(2):
            wd._handle_health(FrameHealth.NONE)
        # Recover
        wd._handle_health(FrameHealth.HEALTHY)
        assert "frame_capture_recovered" in emitted

    def test_healthy_after_healthy_does_not_emit_recovered(self):
        wd = _watchdog(max_failures=2)
        emitted: List[str] = []
        bus = MagicMock()
        bus.emit.side_effect = lambda ev, d: emitted.append(ev)
        wd.set_event_bus(bus)

        wd._handle_health(FrameHealth.HEALTHY)
        wd._handle_health(FrameHealth.HEALTHY)
        assert "frame_capture_recovered" not in emitted


# ─────────────────────────────────────────────────────────────────────────────
# is_healthy / seconds_unhealthy properties
# ─────────────────────────────────────────────────────────────────────────────

class TestProperties:

    def test_is_healthy_true_by_default(self):
        assert _watchdog().is_healthy is True

    def test_is_healthy_false_after_bad_frame(self):
        wd = _watchdog()
        wd._handle_health(FrameHealth.NONE)
        assert wd.is_healthy is False

    def test_seconds_unhealthy_zero_when_healthy(self):
        assert _watchdog().seconds_unhealthy == 0.0

    def test_seconds_unhealthy_positive_after_bad_frame(self):
        wd = _watchdog()
        wd._handle_health(FrameHealth.NONE)
        time.sleep(0.1)  # 100ms — Windows timer resolution is ~15ms, 10ms was unreliable
        assert wd.seconds_unhealthy > 0.0

    def test_stats_snapshot_keys(self):
        snap = _watchdog().stats_snapshot()
        for key in ("healthy", "failure_streak", "total_restarts", "last_health", "seconds_unhealthy"):
            assert key in snap
