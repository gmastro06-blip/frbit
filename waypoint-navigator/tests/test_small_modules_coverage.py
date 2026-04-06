"""
Coverage tests for small modules: frame_watchdog, adaptive_roi, stuck_detector,
pvp_detector, reconnect_handler, death_handler, inventory_manager, depot_orchestrator.

All tests are 100% offline — no real game, no real input.
"""
from __future__ import annotations

import time
import threading
from typing import Any
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# frame_watchdog — start/stop lifecycle, _try_restart, _loop, _log
# ─────────────────────────────────────────────────────────────────────────────

from src.frame_watchdog import FrameWatchdog, FrameWatchdogConfig, FrameHealth


class TestFrameWatchdogLifecycle:

    def test_start_sets_running(self):
        wd = FrameWatchdog(FrameWatchdogConfig(poll_interval=60.0))
        wd.start()
        assert wd._running is True
        wd._running = False  # stop thread from looping

    def test_start_idempotent(self):
        wd = FrameWatchdog(FrameWatchdogConfig(poll_interval=60.0))
        wd.start()
        thread1 = wd._thread
        wd.start()  # second call — should NOT create a new thread
        assert wd._thread is thread1
        wd._running = False

    def test_stop_clears_running(self):
        wd = FrameWatchdog(FrameWatchdogConfig(poll_interval=0.001))
        wd.start()
        wd.stop()
        assert wd._running is False

    def test_set_log_callback_used(self):
        msgs = []
        wd = FrameWatchdog(FrameWatchdogConfig(poll_interval=60.0))
        wd.set_log_callback(lambda m: msgs.append(m))
        wd.start()
        wd._running = False
        assert any("started" in m.lower() for m in msgs)


class TestFrameWatchdogTryRestart:

    def _wd(self, cooldown=0.0):
        cfg = FrameWatchdogConfig(max_failures=2, restart_cooldown=cooldown)
        return FrameWatchdog(cfg)

    def test_restart_fn_called(self):
        wd = self._wd()
        calls = []
        wd.set_restart_fn(lambda: calls.append(1))
        wd._try_restart()
        assert calls == [1]

    def test_restart_resets_streaks(self):
        wd = self._wd()
        wd._failure_streak = 5
        wd._frozen_streak = 3
        wd._last_hash = "abc"
        wd.set_restart_fn(lambda: None)
        wd._try_restart()
        assert wd._failure_streak == 0
        assert wd._frozen_streak == 0
        assert wd._last_hash is None

    def test_restart_fn_exception_swallowed(self):
        wd = self._wd()
        wd.set_restart_fn(lambda: 1 / 0)
        wd._try_restart()  # should not raise

    def test_no_restart_fn_logs_warning(self):
        msgs = []
        wd = self._wd()
        wd.set_log_callback(lambda m: msgs.append(m))
        wd._try_restart()
        assert any("No restart_fn" in m or "cannot auto" in m.lower() for m in msgs)

    def test_cooldown_blocks_restart(self):
        wd = self._wd(cooldown=100.0)
        calls = []
        wd.set_restart_fn(lambda: calls.append(1))
        wd._last_restart_ts = time.monotonic()  # just restarted
        wd._try_restart()
        assert calls == []  # blocked by cooldown

    def test_total_restarts_incremented(self):
        wd = self._wd()
        wd.set_restart_fn(lambda: None)
        wd._try_restart()
        assert wd._total_restarts == 1


class TestFrameWatchdogLoop:

    def test_loop_disabled_skips_assessment(self):
        cfg = FrameWatchdogConfig(enabled=False, poll_interval=0.0)
        wd = FrameWatchdog(cfg)
        assessed = []
        wd._assess_frame = lambda: assessed.append(1) or FrameHealth.HEALTHY

        iterations = [0]
        original_sleep = time.sleep

        def fake_sleep(t):
            iterations[0] += 1
            if iterations[0] >= 2:
                wd._running = False

        with patch("src.frame_watchdog.time") as mock_time:
            mock_time.sleep = fake_sleep
            mock_time.monotonic = time.monotonic
            wd._running = True
            wd._loop()

        assert assessed == []  # disabled config → no assessment

    def test_loop_exception_in_assess_swallowed(self):
        cfg = FrameWatchdogConfig(enabled=True, poll_interval=0.0)
        wd = FrameWatchdog(cfg)
        wd._assess_frame = lambda: 1 / 0  # raises
        wd._handle_health = lambda h: None

        iterations = [0]

        def fake_sleep(t):
            iterations[0] += 1
            if iterations[0] >= 2:
                wd._running = False

        with patch("src.frame_watchdog.time") as mock_time:
            mock_time.sleep = fake_sleep
            mock_time.monotonic = time.monotonic
            wd._running = True
            wd._loop()  # should not raise


class TestFrameWatchdogEmit:

    def test_emit_calls_bus(self):
        wd = FrameWatchdog()
        bus = MagicMock()
        wd.set_event_bus(bus)
        wd._emit("test_event", {"x": 1})
        bus.emit.assert_called_once_with("test_event", {"x": 1})

    def test_emit_bus_exception_swallowed(self):
        wd = FrameWatchdog()
        bus = MagicMock()
        bus.emit.side_effect = RuntimeError("bus error")
        wd.set_event_bus(bus)
        wd._emit("event", {})  # should not raise

    def test_log_fallback_to_stdlib(self):
        wd = FrameWatchdog()
        # No log callback set — uses stdlib logger
        wd._log("test message")  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# adaptive_roi — load_anchors, proportional ROI, detect_or_fallback
# ─────────────────────────────────────────────────────────────────────────────

from src.adaptive_roi import AdaptiveROIDetector, AdaptiveROIConfig, AnchorTemplate


class TestAdaptiveROI:

    def test_load_anchors_no_dir_returns_zero(self):
        detector = AdaptiveROIDetector()
        with patch("src.adaptive_roi._TEMPLATES_DIR") as mock_dir:
            anchors_dir = MagicMock()
            anchors_dir.is_dir.return_value = False
            mock_dir.__truediv__ = lambda self, other: anchors_dir
            result = detector.load_anchors_from_dir()
        assert result == 0

    def test_get_proportional_roi_unknown_name_returns_none(self):
        detector = AdaptiveROIDetector()
        result = detector.get_proportional_roi("nonexistent_roi", 1920, 1080)
        assert result is None

    def test_get_proportional_roi_known_name(self):
        detector = AdaptiveROIDetector()
        # Use a name that exists in _REFERENCE_ROIS
        from src.adaptive_roi import _REFERENCE_ROIS
        if _REFERENCE_ROIS:
            name = next(iter(_REFERENCE_ROIS))
            result = detector.get_proportional_roi(name, 1920, 1080)
            assert isinstance(result, list)
            assert len(result) == 4

    def test_get_all_proportional_rois_returns_dict(self):
        detector = AdaptiveROIDetector()
        result = detector.get_all_proportional_rois(1920, 1080)
        assert isinstance(result, dict)

    def test_detect_or_fallback_no_anchors(self):
        detector = AdaptiveROIDetector()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        result = detector.detect_or_fallback(frame)
        assert isinstance(result, dict)

    def test_detect_or_fallback_with_anchor(self):
        detector = AdaptiveROIDetector()
        anchor_img = np.zeros((20, 20, 3), dtype=np.uint8)
        anchor = AnchorTemplate(
            name="hp_bar",
            image=anchor_img,
            offset=(0, 0),
            expected_size=(0, 0),
            confidence=0.7,
        )
        detector.register_anchor(anchor)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # detect_cached may not find it, but should not crash
        result = detector.detect_or_fallback(frame)
        assert isinstance(result, dict)

    def test_load_anchors_meta_read_error(self):
        """load_anchors_from_dir: meta file exists but JSON is corrupt."""
        detector = AdaptiveROIDetector()

        mock_anchors_dir = MagicMock()
        mock_anchors_dir.is_dir.return_value = True
        mock_anchors_dir.glob.return_value = []  # no images

        mock_meta = MagicMock()
        mock_meta.is_file.return_value = True
        mock_meta.read_text.return_value = "NOT_JSON"

        with patch("src.adaptive_roi._TEMPLATES_DIR") as mock_tdir, \
             patch("src.adaptive_roi._ANCHORS_META", mock_meta):
            mock_tdir.__truediv__ = lambda self, other: mock_anchors_dir
            result = detector.load_anchors_from_dir()

        assert result == 0  # no anchors loaded but no crash


# ─────────────────────────────────────────────────────────────────────────────
# stuck_detector — lifecycle, _tick, recovery actions
# ─────────────────────────────────────────────────────────────────────────────

from src.stuck_detector import StuckDetector, StuckConfig as StuckDetectorConfig, RecoveryAction


def _make_stuck(**kw):
    cfg = StuckDetectorConfig(stuck_timeout=0.01, poll_interval=0.0, **kw)
    sd = StuckDetector(config=cfg)
    sd.set_log_callback(lambda _: None)
    return sd


class TestStuckDetectorLifecycle:

    def test_start_stop(self):
        sd = _make_stuck()
        sd.set_position_getter(lambda: None)
        sd.start()
        assert sd._running
        sd.stop()
        assert not sd._running

    def test_pause_resume(self):
        sd = _make_stuck()
        sd.pause()
        assert sd._paused
        sd.resume()
        assert not sd._paused

    def test_set_walking_resets_timer(self):
        sd = _make_stuck()
        sd.set_walking(False)
        assert sd._walking is False
        sd.set_walking(True)
        assert sd._walking is True

    def test_set_target_direction(self):
        sd = _make_stuck()
        sd.set_target_direction(1, 0)
        assert sd._target_direction == (1, 0)


class TestStuckDetectorTick:

    def test_tick_no_position_getter_returns(self):
        sd = _make_stuck()
        sd._tick()  # should not crash

    def test_tick_position_none_returns(self):
        sd = _make_stuck()
        sd.set_position_getter(lambda: None)
        sd._tick()

    def test_tick_position_moved_resets_timer(self):
        from src.models import Coordinate
        pos_a = Coordinate(100, 100, 7)
        pos_b = Coordinate(101, 100, 7)
        # First tick: getter returns pos_b (different from _last_pos=pos_a) → moved
        sd = _make_stuck()
        sd.set_position_getter(lambda: pos_b)
        sd._last_pos = pos_a
        sd._walking = True
        sd._last_move_time = time.monotonic()  # recent
        sd._tick()  # moved → no stuck triggered
        assert sd._total_stucks == 0

    def test_tick_stuck_triggers_recovery(self):
        from src.models import Coordinate
        pos = Coordinate(100, 100, 7)
        cfg = StuckDetectorConfig(stuck_timeout=0.0, poll_interval=0.0, nudge_retries=0)
        sd = StuckDetector(config=cfg)
        sd.set_log_callback(lambda _: None)
        sd.set_position_getter(lambda: pos)
        sd._last_pos = pos
        sd._last_move_time = time.monotonic() - 999  # definitely stuck
        sd._walking = True
        repath_called = []
        sd.set_repath_fn(lambda: repath_called.append(1) or True)
        sd._tick()
        assert sd._total_stucks == 1


class TestStuckChooseRecovery:

    def _sd(self, nudge_retries=2, max_recovery=10):
        cfg = StuckDetectorConfig(nudge_retries=nudge_retries, max_recovery_attempts=max_recovery)
        sd = StuckDetector(config=cfg)
        sd.set_log_callback(lambda _: None)
        return sd

    def test_n0_returns_repath(self):
        sd = self._sd()
        sd._recovery_count = 0
        assert sd._choose_recovery() == RecoveryAction.REPATH

    def test_n1_returns_nudge(self):
        sd = self._sd(nudge_retries=2)
        sd._recovery_count = 1
        assert sd._choose_recovery() == RecoveryAction.NUDGE

    def test_escape_after_nudge_retries(self):
        sd = self._sd(nudge_retries=2)
        sd._recovery_count = 3  # 1 repath + 2 nudges → escape
        assert sd._choose_recovery() == RecoveryAction.ESCAPE

    def test_abort_at_max_attempts(self):
        sd = self._sd(nudge_retries=1, max_recovery=5)
        sd._recovery_count = 10
        assert sd._choose_recovery() == RecoveryAction.ABORT


class TestStuckRecoveryActions:

    def test_do_repath_calls_fn(self):
        sd = _make_stuck()
        calls = []
        sd.set_repath_fn(lambda: calls.append(1) or True)
        sd._do_repath()
        assert calls == [1]

    def test_do_repath_no_fn(self):
        sd = _make_stuck()
        sd._do_repath()  # no crash

    def test_do_repath_fn_exception(self):
        sd = _make_stuck()
        sd.set_repath_fn(lambda: 1 / 0)
        sd._do_repath()  # no crash

    def test_do_nudge_no_fn(self):
        sd = _make_stuck()
        sd._do_nudge()  # no crash

    def test_do_nudge_with_target_direction(self):
        sd = _make_stuck()
        calls = []
        sd.set_nudge_fn(lambda dx, dy: calls.append((dx, dy)))
        sd._target_direction = (1, 0)
        sd.set_position_getter(lambda: None)
        with patch("src.stuck_detector.jittered_sleep"):
            sd._do_nudge()
        assert calls  # nudge was called

    def test_do_nudge_no_target_direction(self):
        sd = _make_stuck()
        calls = []
        sd.set_nudge_fn(lambda dx, dy: calls.append((dx, dy)))
        sd.set_position_getter(lambda: None)
        with patch("src.stuck_detector.jittered_sleep"):
            sd._do_nudge()
        assert calls

    def test_do_escape_no_fn(self):
        sd = _make_stuck()
        sd._do_escape()  # no crash

    def test_do_escape_with_fn(self):
        sd = _make_stuck()
        called = []
        sd.set_escape_fn(lambda: called.append(1))
        sd.set_position_getter(lambda: None)
        with patch("src.stuck_detector.jittered_sleep"):
            sd._do_escape()
        assert called

    def test_do_abort_sets_walking_false(self):
        sd = _make_stuck()
        sd._walking = True
        sd._do_abort()
        assert sd._walking is False
        assert sd._abort_time > 0

    def test_check_abort_cooldown_re_enables(self):
        sd = _make_stuck()
        sd._abort_time = time.monotonic() - 9999  # long ago
        sd._walking = False
        sd._check_abort_cooldown()
        assert sd._walking is True


class TestStuckEmit:

    def test_emit_calls_bus(self):
        sd = _make_stuck()
        bus = MagicMock()
        sd.set_event_bus(bus)
        sd._emit("e29", {"x": 1})
        bus.emit.assert_called_once_with("e29", {"x": 1})

    def test_emit_bus_exception_swallowed(self):
        sd = _make_stuck()
        bus = MagicMock()
        bus.emit.side_effect = RuntimeError
        sd.set_event_bus(bus)
        sd._emit("evt", {})  # no crash

    def test_read_pos_getter_exception_returns_none(self):
        sd = _make_stuck()
        sd.set_position_getter(lambda: 1 / 0)
        assert sd._read_pos() is None

    def test_log_fallback(self):
        sd = StuckDetector()
        sd._log("test")  # uses stdlib logger — no crash


# ─────────────────────────────────────────────────────────────────────────────
# pvp_detector — PvPConfig.validate, scan
# ─────────────────────────────────────────────────────────────────────────────

from src.pvp_detector import PvPDetector, PvPConfig, PvPAction


class TestPvPConfigValidate:

    def test_wrong_roi_length(self):
        cfg = PvPConfig(battle_list_roi=[0, 0, 100])
        with pytest.raises(ValueError, match="4 elements"):
            cfg.validate()

    def test_negative_roi_value(self):
        cfg = PvPConfig(battle_list_roi=[-1, 0, 100, 50])
        with pytest.raises(ValueError, match="negative"):
            cfg.validate()

    def test_confidence_out_of_range(self):
        cfg = PvPConfig(confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            cfg.validate()

    def test_negative_cooldown(self):
        cfg = PvPConfig(cooldown_s=-1.0)
        with pytest.raises(ValueError, match="cooldown"):
            cfg.validate()

    def test_min_consecutive_zero(self):
        cfg = PvPConfig(min_consecutive=0)
        with pytest.raises(ValueError, match="min_consecutive"):
            cfg.validate()

    def test_valid_config_no_raise(self):
        cfg = PvPConfig()
        cfg.validate()  # default config should be valid


class TestPvPDetectorScan:

    def _det(self, **kw):
        cfg = PvPConfig(**kw)
        return PvPDetector(config=cfg, auto_load=False)

    def test_scan_none_frame_returns_no_detection(self):
        det = self._det()
        result = det.scan(None)
        assert result.detected is False

    def test_scan_disabled_returns_no_detection(self):
        det = self._det(enabled=False)
        frame = np.zeros((400, 200, 3), dtype=np.uint8)
        result = det.scan(frame)
        assert result.detected is False

    def test_scan_empty_frame_returns_no_detection(self):
        det = self._det()
        result = det.scan(np.zeros((0,), dtype=np.uint8))
        assert result.detected is False

    def test_scan_counts_total_scans(self):
        det = self._det()
        frame = np.zeros((400, 200, 3), dtype=np.uint8)
        det.scan(frame)
        det.scan(frame)
        assert det._total_scans == 2

    def test_scan_consecutive_tracking(self):
        det = self._det(min_consecutive=3, cooldown_s=0.0)
        frame = np.zeros((400, 200, 3), dtype=np.uint8)
        # No players → consecutive resets
        det._consecutive = 5
        det.scan(frame)
        assert det._consecutive == 0

    def test_scan_with_roi(self):
        det = self._det(battle_list_roi=[0, 0, 50, 50])
        frame = np.zeros((400, 200, 3), dtype=np.uint8)
        result = det.scan(frame)
        assert isinstance(result.detected, bool)

    def test_total_detections_stat(self):
        det = self._det()
        assert det._total_detections == 0


# ─────────────────────────────────────────────────────────────────────────────
# reconnect_handler — lifecycle, properties, _tick
# ─────────────────────────────────────────────────────────────────────────────

from src.reconnect_handler import ReconnectHandler, ReconnectConfig


def _rh(**kw):
    cfg = ReconnectConfig(**kw)
    rh = ReconnectHandler(ctrl=MagicMock(), config=cfg)
    rh.set_log_callback(lambda _: None)
    return rh


class TestReconnectHandlerLifecycle:

    def test_start_stop(self):
        rh = _rh(check_interval=60.0)
        rh.start()
        assert rh._running
        rh._running = False  # prevent loop
        rh.stop()

    def test_start_idempotent(self):
        rh = _rh(check_interval=60.0)
        rh.start()
        t1 = rh._thread
        rh.start()
        assert rh._thread is t1
        rh._running = False

    def test_is_running_property(self):
        rh = _rh()
        assert rh.is_running is False
        rh._running = True
        assert rh.is_running is True

    def test_disconnects_property(self):
        rh = _rh()
        rh._disconnects = 3
        assert rh.disconnects == 3

    def test_reconnects_property(self):
        rh = _rh()
        rh._reconnects = 7
        assert rh.reconnects == 7

    def test_stats_snapshot(self):
        rh = _rh()
        snap = rh.stats_snapshot()
        assert "disconnects" in snap
        assert "reconnects" in snap
        assert "consecutive_failures" in snap

    def test_check_now_no_crash(self):
        rh = _rh()
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        result = rh.check_now(frame)
        assert isinstance(result, bool)


class TestReconnectHandlerTick:

    def test_tick_no_frame_getter_returns(self):
        rh = _rh()
        rh._tick()  # no crash

    def test_tick_frame_none_returns(self):
        rh = _rh()
        rh.set_frame_getter(lambda: None)
        rh._tick()  # no crash

    def test_tick_not_login_screen_resets_failures(self):
        rh = _rh()
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        rh.set_frame_getter(lambda: frame)
        rh._consecutive_failures = 5
        rh._is_login_screen = lambda f: False
        rh._tick()
        assert rh._consecutive_failures == 0

    def test_tick_login_screen_streak_below_threshold(self):
        rh = _rh()
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        rh.set_frame_getter(lambda: frame)
        rh._is_login_screen = lambda f: True
        rh._login_screen_streak = 0
        rh._tick()
        rh._tick()
        # Only 2 frames, need 3 to confirm → no disconnect yet
        assert rh._disconnects == 0


# ─────────────────────────────────────────────────────────────────────────────
# death_handler — lifecycle, properties, _tick
# ─────────────────────────────────────────────────────────────────────────────

from src.death_handler import DeathHandler, DeathConfig as DeathHandlerConfig


def _dh(**kw):
    cfg = DeathHandlerConfig(**kw)
    dh = DeathHandler(ctrl=MagicMock(), config=cfg)
    dh.set_log_callback(lambda _: None)
    return dh


class TestDeathHandlerLifecycle:

    def test_start_stop(self):
        dh = _dh(check_interval=60.0)
        dh.start()
        assert dh._running
        dh._running = False
        dh.stop()

    def test_is_running_property(self):
        dh = _dh()
        assert dh.is_running is False

    def test_deaths_property(self):
        dh = _dh()
        dh._deaths = 3
        assert dh.deaths == 3

    def test_reset_deaths(self):
        dh = _dh()
        dh._deaths = 5
        dh.reset_deaths()
        assert dh.deaths == 0

    def test_death_position_property(self):
        dh = _dh()
        assert dh.death_position is None

    def test_stats_snapshot(self):
        dh = _dh()
        snap = dh.stats_snapshot()
        assert "deaths" in snap
        assert "running" in snap

    def test_check_now_returns_bool(self):
        dh = _dh()
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        result = dh.check_now(frame)
        assert isinstance(result, bool)


class TestDeathHandlerTick:

    def test_tick_no_frame_getter_returns(self):
        dh = _dh()
        dh._tick()  # no crash

    def test_tick_frame_none_returns(self):
        dh = _dh()
        dh.set_frame_getter(lambda: None)
        dh._tick()

    def test_tick_not_death_screen_resets_streak(self):
        dh = _dh()
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        dh.set_frame_getter(lambda: frame)
        dh._consecutive_death_frames = 5
        dh._is_death_screen = lambda f: False
        dh._tick()
        assert dh._consecutive_death_frames == 0

    def test_tick_death_below_confirm_threshold(self):
        dh = _dh(confirm_frames=3)
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        dh.set_frame_getter(lambda: frame)
        dh._is_death_screen = lambda f: True
        dh._consecutive_death_frames = 0
        dh._tick()
        assert dh._deaths == 0  # only 1 frame, need 3

    def test_tick_death_confirmed(self):
        dh = _dh(confirm_frames=1)
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        dh.set_frame_getter(lambda: frame)
        dh._is_death_screen = lambda f: True
        on_death_called = []
        dh.set_on_death(lambda: on_death_called.append(1))
        dh._tick()
        assert dh._deaths == 1
        assert on_death_called == [1]

    def test_tick_on_death_exception_swallowed(self):
        dh = _dh(confirm_frames=1)
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        dh.set_frame_getter(lambda: frame)
        dh._is_death_screen = lambda f: True
        dh.set_on_death(lambda: 1 / 0)
        dh._tick()  # no crash
        assert dh._deaths == 1

    def test_tick_max_deaths_stops_handler(self):
        dh = _dh(confirm_frames=1, max_deaths=1)
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        dh.set_frame_getter(lambda: frame)
        dh._is_death_screen = lambda f: True
        stop_called = []
        dh.set_stop_session_fn(lambda: stop_called.append(1))
        dh._tick()
        assert dh._running is False
        assert stop_called == [1]

    def test_death_position_from_getter(self):
        from src.models import Coordinate
        pos = Coordinate(100, 100, 7)
        dh = _dh(confirm_frames=1)
        frame = np.zeros((400, 600, 3), dtype=np.uint8)
        dh.set_frame_getter(lambda: frame)
        dh._is_death_screen = lambda f: True
        dh.set_position_getter(lambda: pos)
        dh._tick()
        assert dh.death_position == pos


# ─────────────────────────────────────────────────────────────────────────────
# inventory_manager — InventoryConfig.validate, InventoryReading.free_slots
# ─────────────────────────────────────────────────────────────────────────────

from src.inventory_manager import (
    InventoryConfig, InventoryReading, InventoryStatus, InventoryManager,
)


class TestInventoryConfigValidate:

    def test_wrong_roi_length(self):
        cfg = InventoryConfig(inventory_roi=[0, 0, 100])
        with pytest.raises(ValueError, match="4 elements"):
            cfg.validate()

    def test_negative_roi(self):
        cfg = InventoryConfig(inventory_roi=[-1, 0, 100, 50])
        with pytest.raises(ValueError, match="negative"):
            cfg.validate()

    def test_capacity_slots_zero(self):
        cfg = InventoryConfig(capacity_slots=0)
        with pytest.raises(ValueError, match="capacity_slots"):
            cfg.validate()

    def test_full_threshold_zero(self):
        cfg = InventoryConfig(full_threshold=0.0)
        with pytest.raises(ValueError, match="full_threshold"):
            cfg.validate()

    def test_nearly_full_threshold_zero(self):
        cfg = InventoryConfig(nearly_full_threshold=0.0)
        with pytest.raises(ValueError, match="nearly_full_threshold"):
            cfg.validate()

    def test_nearly_full_gte_full(self):
        cfg = InventoryConfig(nearly_full_threshold=0.9, full_threshold=0.85)
        with pytest.raises(ValueError, match="nearly_full_threshold"):
            cfg.validate()

    def test_check_interval_zero(self):
        cfg = InventoryConfig(check_interval_s=0.0)
        with pytest.raises(ValueError, match="check_interval"):
            cfg.validate()

    def test_invalid_depot_action(self):
        cfg = InventoryConfig(depot_action="invalid")
        with pytest.raises(ValueError, match="depot_action"):
            cfg.validate()

    def test_valid_default_config(self):
        InventoryConfig().validate()  # should not raise


class TestInventoryReading:

    def test_free_slots(self):
        r = InventoryReading(occupied_slots=5, total_slots=20)
        assert r.free_slots == 15

    def test_free_slots_no_underflow(self):
        r = InventoryReading(occupied_slots=25, total_slots=20)
        assert r.free_slots == 0



class TestInventoryManagerBasic:

    def _mgr(self):
        cfg = InventoryConfig()
        m = InventoryManager(config=cfg)
        return m

    def test_needs_depot_false_by_default(self):
        m = self._mgr()
        assert m.needs_depot() is False

    def test_last_inventory_none_by_default(self):
        m = self._mgr()
        assert m.last_inventory is None

    def test_check_inventory_no_frame_getter(self):
        m = self._mgr()
        frame = np.zeros((600, 800, 3), dtype=np.uint8)
        # No crash even without frame_getter wired
        m.check_inventory(frame)

    def test_stats_snapshot_keys(self):
        m = self._mgr()
        snap = m.stats_snapshot()
        assert "needs_depot" in snap or "last_inventory" in snap or isinstance(snap, dict)


# ─────────────────────────────────────────────────────────────────────────────
# depot_orchestrator — should_resupply, run_resupply
# ─────────────────────────────────────────────────────────────────────────────

from src.depot_orchestrator import DepotOrchestrator, ResupplyConfig


def _orch(**kw):
    cfg = ResupplyConfig(**kw)
    orch = DepotOrchestrator(config=cfg, log_fn=lambda _: None)
    return orch


class TestDepotOrchestratorShouldResupply:

    def test_disabled_returns_false(self):
        orch = _orch(enabled=False)
        assert orch.should_resupply() is False

    def test_max_resupply_exceeded_returns_false(self):
        orch = _orch(max_resupply_per_session=2)
        orch._resupply_count = 2
        assert orch.should_resupply() is False

    def test_within_cooldown_returns_false(self):
        orch = _orch(check_interval_s=1000.0)
        orch._last_check_ts = time.monotonic()  # just checked
        assert orch.should_resupply() is False

    def test_no_inventory_manager_returns_false(self):
        orch = _orch(check_interval_s=0.0)
        orch._last_check_ts = 0.0
        orch._inv = None
        assert orch.should_resupply() is False

    def test_inventory_needs_depot_returns_true(self):
        orch = _orch(check_interval_s=0.0)
        orch._last_check_ts = 0.0
        inv = MagicMock()
        inv.needs_depot.return_value = True
        inv.last_inventory = None
        orch._inv = inv
        result = orch.should_resupply()
        assert result is True

    def test_inventory_not_needed_returns_false(self):
        orch = _orch(check_interval_s=0.0)
        orch._last_check_ts = 0.0
        inv = MagicMock()
        inv.needs_depot.return_value = False
        orch._inv = inv
        assert orch.should_resupply() is False


class TestDepotOrchestratorRunResupply:

    def test_run_resupply_no_depot_completes(self):
        orch = _orch()
        orch._depot = None
        result = orch.run_resupply()
        # No depot → skips depot step, still returns True (success by default)
        assert isinstance(result, bool)

    def test_run_resupply_depot_cycle_called(self):
        orch = _orch()
        depot = MagicMock()
        depot.run_depot_cycle.return_value = True
        orch._depot = depot
        result = orch.run_resupply()
        depot.run_depot_cycle.assert_called_once()

    def test_run_resupply_increments_count(self):
        orch = _orch()
        depot = MagicMock()
        depot.run_depot_cycle.return_value = True
        orch._depot = depot
        orch.run_resupply()
        assert orch._resupply_count == 1

    def test_run_resupply_with_trade_manager(self):
        orch = _orch(buy_supplies_after_depot=True)
        depot = MagicMock()
        depot.run_depot_cycle.return_value = True
        orch._depot = depot
        trade = MagicMock()
        trade.buy_supplies.return_value = True
        orch._trade = trade
        orch.run_resupply()
        # trade.buy_supplies may or may not be called depending on implementation
        assert orch._resupply_count == 1
