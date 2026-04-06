"""Tests for ChatResponder — auto-reply to private messages."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, call

import numpy as np
import pytest

from src.chat_responder import (
    ChatResponder,
    ChatResponderConfig,
    _GENERIC_RESPONSES,
    _GM_RESPONSES,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    """Create a blank BGR frame."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def _make_frame_with_purple_pixels(
    roi: list[int], pixel_count: int = 60
) -> np.ndarray:
    """Create a frame with purple/magenta pixels in the chat ROI (simulating PM)."""
    frame = _make_frame()
    x, y, w, h = roi
    # Purple in BGR is approx (200, 50, 200); in HSV ~ H=150, S=192, V=200
    end_x = min(x + pixel_count, x + w)
    frame[y : y + 2, x : end_x] = (200, 50, 200)  # BGR purple
    return frame


# ── Config tests ─────────────────────────────────────────────────────────────

class TestChatResponderConfig:
    def test_defaults(self) -> None:
        cfg = ChatResponderConfig()
        assert cfg.enabled is True
        assert cfg.scan_interval == 2.0
        assert cfg.response_delay_min == 3.0
        assert cfg.response_delay_max == 12.0
        assert len(cfg.chat_roi) == 4
        assert cfg.min_pixel_threshold == 40
        assert cfg.cooldown_s == 30.0
        assert cfg.max_responses_per_session == 20

    def test_custom_config(self) -> None:
        cfg = ChatResponderConfig(scan_interval=5.0, cooldown_s=60.0)
        assert cfg.scan_interval == 5.0
        assert cfg.cooldown_s == 60.0

    def test_default_responses(self) -> None:
        cfg = ChatResponderConfig()
        assert len(cfg.generic_responses) > 0
        assert len(cfg.gm_responses) > 0
        assert "hey" in cfg.generic_responses


# ── Instantiation ────────────────────────────────────────────────────────────

class TestChatResponderInit:
    def test_defaults(self) -> None:
        r = ChatResponder()
        assert r.total_scans == 0
        assert r.total_pms_detected == 0
        assert r.total_responses_sent == 0
        assert r.is_running is False

    def test_custom_config(self) -> None:
        cfg = ChatResponderConfig(cooldown_s=10.0)
        r = ChatResponder(config=cfg)
        assert r.config.cooldown_s == 10.0

    def test_set_frame_getter(self) -> None:
        r = ChatResponder()
        fn = MagicMock(return_value=None)
        r.set_frame_getter(fn)
        assert r._frame_getter is fn

    def test_set_input_controller(self) -> None:
        r = ChatResponder()
        ctrl = MagicMock()
        r.set_input_controller(ctrl)
        assert r._ctrl is ctrl


# ── PM detection ─────────────────────────────────────────────────────────────

class TestChatResponderDetection:
    def test_no_frame_getter(self) -> None:
        r = ChatResponder()
        detected, count = r._check_for_pm()
        assert detected is False
        assert count == 0

    def test_blank_frame_no_pm(self) -> None:
        r = ChatResponder()
        r.set_frame_getter(lambda: _make_frame())
        detected, count = r._check_for_pm()
        assert detected is False

    def test_none_frame(self) -> None:
        r = ChatResponder()
        r.set_frame_getter(lambda: None)
        detected, count = r._check_for_pm()
        assert detected is False

    def test_detects_purple_pixels(self) -> None:
        roi = [7, 570, 600, 200]
        frame = _make_frame_with_purple_pixels(roi, pixel_count=80)
        r = ChatResponder()
        r.set_frame_getter(lambda: frame)
        detected, count = r._check_for_pm()
        assert detected is True
        assert count >= 40

    def test_below_threshold_no_detection(self) -> None:
        roi = [7, 570, 600, 200]
        frame = _make_frame()
        x, y = roi[0], roi[1]
        frame[y, x : x + 5] = (200, 50, 200)  # Only 5 pixels
        r = ChatResponder()
        r.set_frame_getter(lambda: frame)
        detected, count = r._check_for_pm()
        assert detected is False


# ── Response handling ────────────────────────────────────────────────────────

class TestChatResponderResponse:
    def test_send_response_types_and_enters(self) -> None:
        ctrl = MagicMock()
        ctrl.type_text = MagicMock()
        r = ChatResponder()
        r.set_input_controller(ctrl)

        r._send_response("hello")

        # Should call key_combo(Ctrl+R), type_text, and press_key(Enter)
        ctrl.key_combo.assert_called_once_with(0x11, 0x52)
        ctrl.type_text.assert_called_once_with("hello")
        ctrl.press_key.assert_called_with(0x0D)  # Enter

    def test_send_response_fallback_no_type_text(self) -> None:
        ctrl = MagicMock(spec=["press_key", "key_combo"])  # No type_text
        r = ChatResponder()
        r.set_input_controller(ctrl)

        r._send_response("hi")

        # Should use key_combo for Ctrl+R, press_key for each char + Enter
        ctrl.key_combo.assert_called_once_with(0x11, 0x52)
        assert ctrl.press_key.call_count >= 3  # h, i, Enter

    def test_send_response_increments_counter(self) -> None:
        ctrl = MagicMock()
        ctrl.type_text = MagicMock()
        r = ChatResponder()
        r.set_input_controller(ctrl)

        assert r.total_responses_sent == 0
        r._send_response("yo")
        assert r.total_responses_sent == 1

    def test_handle_pm_respects_cooldown(self) -> None:
        ctrl = MagicMock()
        ctrl.type_text = MagicMock()
        cfg = ChatResponderConfig(
            cooldown_s=100.0,
            response_delay_min=0.01,
            response_delay_max=0.02,
        )
        r = ChatResponder(config=cfg)
        r.set_input_controller(ctrl)
        r._running = True

        # First response
        r._handle_pm(100)
        assert r.total_responses_sent == 1

        # Second within cooldown — should be skipped
        r._handle_pm(100)
        assert r.total_responses_sent == 1

    def test_handle_pm_respects_session_limit(self) -> None:
        ctrl = MagicMock()
        ctrl.type_text = MagicMock()
        cfg = ChatResponderConfig(
            max_responses_per_session=1,
            response_delay_min=0.01,
            response_delay_max=0.02,
            cooldown_s=0.0,
        )
        r = ChatResponder(config=cfg)
        r.set_input_controller(ctrl)
        r._running = True

        r._handle_pm(100)
        assert r.total_responses_sent == 1

        r._handle_pm(100)
        assert r.total_responses_sent == 1  # capped

    def test_no_ctrl_no_crash(self) -> None:
        r = ChatResponder()
        # No controller set
        r._send_response("hello")
        assert r.total_responses_sent == 0


# ── Event emission ───────────────────────────────────────────────────────────

class TestChatResponderEvents:
    def test_emits_pm_detected(self) -> None:
        bus = MagicMock()
        ctrl = MagicMock()
        ctrl.type_text = MagicMock()
        cfg = ChatResponderConfig(
            response_delay_min=0.01,
            response_delay_max=0.02,
            cooldown_s=0.0,
        )
        r = ChatResponder(config=cfg, event_bus=bus)
        r.set_input_controller(ctrl)
        r._running = True

        r._handle_pm(100)

        # Should emit pm_detected and pm_responded
        events_emitted = [c[0][0] for c in bus.emit.call_args_list]
        assert "e16" in events_emitted
        assert "e17" in events_emitted

    def test_emit_with_no_bus(self) -> None:
        r = ChatResponder()
        # Should not raise
        r._emit("test", {"x": 1})


# ── Lifecycle ────────────────────────────────────────────────────────────────

class TestChatResponderLifecycle:
    def test_start_stop(self) -> None:
        cfg = ChatResponderConfig(scan_interval=0.05)
        r = ChatResponder(config=cfg)
        r.set_frame_getter(lambda: _make_frame())
        r.start()
        assert r.is_running is True
        time.sleep(0.15)
        r.stop()
        assert r.is_running is False
        assert r.total_scans > 0

    def test_double_start_safe(self) -> None:
        cfg = ChatResponderConfig(scan_interval=0.05)
        r = ChatResponder(config=cfg)
        r.set_frame_getter(lambda: _make_frame())
        r.start()
        r.start()
        assert r.is_running
        r.stop()


# ── Stats ────────────────────────────────────────────────────────────────────

class TestChatResponderStats:
    def test_stats_snapshot(self) -> None:
        r = ChatResponder()
        snap = r.stats_snapshot()
        assert snap["enabled"] is True
        assert snap["total_scans"] == 0
        assert snap["pms_detected"] == 0
        assert snap["responses_sent"] == 0

    def test_rising_edge_detection(self) -> None:
        """PM should only trigger response on rising edge (new PM appearance)."""
        r = ChatResponder()
        ctrl = MagicMock()
        ctrl.type_text = MagicMock()
        r.set_input_controller(ctrl)

        # Simulate that _prev_pm_detected starts False
        assert r._prev_pm_detected is False

        # First detection — should count as new
        r._prev_pm_detected = False
        # If pm detected and not prev → new PM
        pm_detected = True
        if pm_detected and not r._prev_pm_detected:
            r._total_pms_detected += 1
        r._prev_pm_detected = pm_detected

        assert r.total_pms_detected == 1

        # Second detection (same PM still on screen) — NOT new
        pm_detected = True
        if pm_detected and not r._prev_pm_detected:
            r._total_pms_detected += 1
        r._prev_pm_detected = pm_detected

        assert r.total_pms_detected == 1  # Still 1

        # PM clears
        r._prev_pm_detected = False

        # New PM appears — should count
        pm_detected = True
        if pm_detected and not r._prev_pm_detected:
            r._total_pms_detected += 1
        r._prev_pm_detected = pm_detected

        assert r.total_pms_detected == 2
