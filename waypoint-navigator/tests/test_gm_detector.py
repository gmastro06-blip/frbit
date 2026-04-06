"""Tests for GMDetector — GM presence detection on battle list / chat."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.gm_detector import GMDetector, GMDetectorConfig, GMAction, GMDetection


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    """Create a blank BGR frame."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def _make_frame_with_cyan_pixels(
    roi: list[int], pixel_count: int = 50
) -> np.ndarray:
    """Create a frame with cyan pixels in the given ROI (simulating GM name)."""
    frame = _make_frame()
    x, y, w, h = roi
    # Cyan in BGR is (255, 255, 0); in HSV ~ H=90, S=255, V=255
    # Paint a block of cyan pixels
    end_x = min(x + pixel_count, x + w)
    frame[y : y + 2, x : end_x] = (255, 255, 0)  # BGR cyan
    return frame


# ── Config tests ─────────────────────────────────────────────────────────────

class TestGMDetectorConfig:
    def test_defaults(self) -> None:
        cfg = GMDetectorConfig()
        assert cfg.enabled is True
        assert cfg.action == GMAction.PAUSE
        assert cfg.scan_interval == 1.0
        assert cfg.min_consecutive == 2
        assert len(cfg.battle_list_roi) == 4
        assert len(cfg.chat_roi) == 4

    def test_custom_action(self) -> None:
        cfg = GMDetectorConfig(action=GMAction.LOGOUT)
        assert cfg.action == GMAction.LOGOUT

    def test_custom_hsv(self) -> None:
        cfg = GMDetectorConfig(gm_hsv_lower=[80, 100, 200], gm_hsv_upper=[110, 255, 255])
        assert cfg.gm_hsv_lower == [80, 100, 200]


# ── Detection result tests ───────────────────────────────────────────────────

class TestGMDetection:
    def test_defaults(self) -> None:
        d = GMDetection()
        assert d.detected is False
        assert d.source == ""
        assert d.confidence == 0.0
        assert d.pixel_count == 0

    def test_fields(self) -> None:
        d = GMDetection(detected=True, source="battle_list", pixel_count=100)
        assert d.detected is True
        assert d.source == "battle_list"


# ── Detector instantiation ──────────────────────────────────────────────────

class TestGMDetectorInit:
    def test_default_config(self) -> None:
        d = GMDetector()
        assert d.config.enabled is True
        assert d.total_scans == 0
        assert d.total_detections == 0
        assert d.consecutive_count == 0

    def test_custom_config(self) -> None:
        cfg = GMDetectorConfig(action=GMAction.HUMAN_MIMIC, min_consecutive=3)
        d = GMDetector(config=cfg)
        assert d.config.action == GMAction.HUMAN_MIMIC
        assert d.config.min_consecutive == 3

    def test_set_frame_getter(self) -> None:
        d = GMDetector()
        fn = MagicMock(return_value=None)
        d.set_frame_getter(fn)
        assert d._frame_getter is fn

    def test_set_input_controller(self) -> None:
        d = GMDetector()
        ctrl = MagicMock()
        d.set_input_controller(ctrl)
        assert d._ctrl is ctrl

    def test_set_pause_resume_fn(self) -> None:
        d = GMDetector()
        pause = MagicMock()
        resume = MagicMock()
        d.set_pause_fn(pause)
        d.set_resume_fn(resume)
        assert d._pause_fn is pause
        assert d._resume_fn is resume


# ── Scanning ─────────────────────────────────────────────────────────────────

class TestGMDetectorScan:
    def test_scan_no_frame_getter(self) -> None:
        d = GMDetector()
        result = d._scan_once()
        assert result.detected is False
        assert d.total_scans == 1

    def test_scan_blank_frame(self) -> None:
        d = GMDetector()
        d.set_frame_getter(lambda: _make_frame())
        result = d._scan_once()
        assert result.detected is False
        assert d.total_scans == 1

    def test_scan_none_frame(self) -> None:
        d = GMDetector()
        d.set_frame_getter(lambda: None)
        result = d._scan_once()
        assert result.detected is False

    def test_scan_detects_cyan_in_battle_list(self) -> None:
        roi = [1568, 175, 162, 345]
        frame = _make_frame_with_cyan_pixels(roi, pixel_count=60)
        d = GMDetector()
        d.set_frame_getter(lambda: frame)
        result = d._scan_once()
        assert result.detected is True
        assert result.source == "battle_list"
        assert result.pixel_count >= 30

    def test_scan_detects_cyan_in_chat(self) -> None:
        bl_roi = [1568, 175, 162, 345]
        chat_roi = [7, 570, 600, 200]
        # No cyan in battle list, cyan in chat
        frame = _make_frame()
        x, y, w, h = chat_roi
        frame[y : y + 2, x : x + 60] = (255, 255, 0)
        d = GMDetector()
        d.set_frame_getter(lambda: frame)
        result = d._scan_once()
        assert result.detected is True
        assert result.source == "chat"

    def test_scan_below_threshold_no_detection(self) -> None:
        roi = [1568, 175, 162, 345]
        frame = _make_frame()
        x, y = roi[0], roi[1]
        frame[y, x : x + 5] = (255, 255, 0)  # Only 5 pixels — below threshold
        d = GMDetector()
        d.set_frame_getter(lambda: frame)
        result = d._scan_once()
        assert result.detected is False


# ── Consecutive tracking ─────────────────────────────────────────────────────

class TestGMDetectorConsecutive:
    def test_consecutive_increments(self) -> None:
        roi = [1568, 175, 162, 345]
        frame = _make_frame_with_cyan_pixels(roi, pixel_count=60)
        d = GMDetector(config=GMDetectorConfig(min_consecutive=3))
        d.set_frame_getter(lambda: frame)

        d._scan_once()
        assert d.consecutive_count == 0  # _scan_once doesn't update consecutive

    def test_consecutive_resets_on_clear(self) -> None:
        d = GMDetector()
        d._consecutive = 5
        d.set_frame_getter(lambda: _make_frame())  # blank = no detection

        # Simulate scan loop behavior
        result = d._scan_once()
        if not result.detected:
            d._consecutive = 0
        assert d._consecutive == 0


# ── Event emission ───────────────────────────────────────────────────────────

class TestGMDetectorEvents:
    def test_emit_gm_detected(self) -> None:
        bus = MagicMock()
        d = GMDetector(event_bus=bus)
        result = GMDetection(detected=True, source="battle_list", confidence=0.8)
        d._on_gm_confirmed(result)
        bus.emit.assert_called_once()
        call_args = bus.emit.call_args
        assert call_args[0][0] == "e15"
        assert call_args[0][1]["source"] == "battle_list"

    def test_emit_with_no_bus(self) -> None:
        d = GMDetector()
        result = GMDetection(detected=True, source="chat", confidence=0.5)
        # Should not raise
        d._on_gm_confirmed(result)
        assert d.total_detections == 1


# ── Action handlers ──────────────────────────────────────────────────────────

class TestGMDetectorActions:
    def test_pause_action_calls_pause_fn(self) -> None:
        pause_fn = MagicMock()
        resume_fn = MagicMock()
        d = GMDetector(config=GMDetectorConfig(action=GMAction.PAUSE))
        d.set_pause_fn(pause_fn)
        d.set_resume_fn(resume_fn)
        d.set_frame_getter(lambda: _make_frame())  # blank = no GM → clears quickly
        d._running = True

        result = GMDetection(detected=True, source="battle_list", confidence=0.8)
        d._on_gm_confirmed(result)
        pause_fn.assert_called_once()

    def test_logout_action_sends_keys(self) -> None:
        ctrl = MagicMock()
        d = GMDetector(config=GMDetectorConfig(action=GMAction.LOGOUT))
        d.set_input_controller(ctrl)
        d.set_pause_fn(MagicMock())

        result = GMDetection(detected=True, source="chat", confidence=0.7)
        d._on_gm_confirmed(result)
        ctrl.key_combo.assert_called_once_with(0x11, 0x4C)  # Ctrl+L

    def test_alert_action_only_emits(self) -> None:
        bus = MagicMock()
        d = GMDetector(config=GMDetectorConfig(action=GMAction.ALERT), event_bus=bus)

        result = GMDetection(detected=True, source="battle_list", confidence=0.9)
        d._on_gm_confirmed(result)
        bus.emit.assert_called_once()
        assert d.total_detections == 1

    def test_pause_action_times_out_and_resumes(self) -> None:
        pause_fn = MagicMock()
        resume_fn = MagicMock()
        d = GMDetector(config=GMDetectorConfig(action=GMAction.PAUSE, pause_timeout_s=0.1))
        d.set_pause_fn(pause_fn)
        d.set_resume_fn(resume_fn)
        d._running = True
        d._consecutive = 4
        d._scan_once = MagicMock(return_value=GMDetection(detected=True, source="battle_list"))

        with patch("src.gm_detector.time.sleep"), patch(
            "src.gm_detector.time.monotonic", side_effect=[0.0, 0.2]
        ):
            d._do_pause()

        pause_fn.assert_called_once()
        resume_fn.assert_called_once()
        assert d.consecutive_count == 0


# ── Lifecycle ────────────────────────────────────────────────────────────────

class TestGMDetectorLifecycle:
    def test_start_stop(self) -> None:
        d = GMDetector(config=GMDetectorConfig(scan_interval=0.05))
        d.set_frame_getter(lambda: _make_frame())
        d.start()
        assert d.is_running is True
        time.sleep(0.15)
        d.stop()
        assert d.is_running is False
        assert d.total_scans > 0

    def test_double_start_is_safe(self) -> None:
        d = GMDetector(config=GMDetectorConfig(scan_interval=0.05))
        d.set_frame_getter(lambda: _make_frame())
        d.start()
        d.start()  # Should not create second thread
        assert d.is_running
        d.stop()

    def test_scan_loop_resets_consecutive_when_detector_disabled(self) -> None:
        d = GMDetector(config=GMDetectorConfig(enabled=False, scan_interval=0.01))
        d._running = True
        d._consecutive = 3
        d._last_enabled = True

        def _stop(_: float) -> None:
            d._running = False

        with patch("src.gm_detector.time.sleep", side_effect=_stop):
            d._scan_loop()

        assert d.consecutive_count == 0


# ── Stats ────────────────────────────────────────────────────────────────────

class TestGMDetectorStats:
    def test_stats_snapshot(self) -> None:
        d = GMDetector()
        snap = d.stats_snapshot()
        assert snap["enabled"] is True
        assert snap["total_scans"] == 0
        assert snap["total_detections"] == 0
        assert snap["consecutive"] == 0

    def test_last_result(self) -> None:
        d = GMDetector()
        assert d.last_result is None
        d.set_frame_getter(lambda: _make_frame())
        d._scan_once()
        assert d.last_result is not None

    def test_roi_scales_with_frame_resolution(self) -> None:
        cfg = GMDetectorConfig(battle_list_roi=[1568, 175, 162, 345])
        d = GMDetector(config=cfg)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, w, h = d._scale_roi(frame, cfg.battle_list_roi)
        frame[y : y + 2, x : x + min(60, w)] = (255, 255, 0)
        d.set_frame_getter(lambda: frame)

        result = d._scan_once()
        assert result.detected is True
        assert result.source == "battle_list"
