# pyright: reportMethodAssignment=false, reportAssignmentType=false

"""Additional tests for HumanInputSystem uncovered branches.

Targets lines: 86, 93-110, 138, 157, 178, 182, 189-195, 219, 234,
               244-245, 256, 272-304, 322-324, 347, 351, 355, 359, 362,
               368, 390, 397-399, 404-410, 414-431
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from human_input_system.config.models import (
    Configuration,
    BehaviorConfig,
    TimingConfig,
    MouseConfig,
    ArduinoConfig,
)
from human_input_system import HumanInputSystem


# ═══════════════════════════════════════════════════════════════════════
# Fixture
# ═══════════════════════════════════════════════════════════════════════

def _make_his(tmp_path: Path, cfg_overrides: dict | None = None) -> tuple[HumanInputSystem, MagicMock]:
    """Build HIS from the real config.yaml with a mock InputController."""
    cfg_path = _ROOT / "human_input_system" / "config.yaml"
    mock_ic = MagicMock()
    mock_ic.press_key.return_value = True
    mock_ic.hold_key.return_value = True
    mock_ic.click.return_value = True
    mock_ic.click_human.return_value = True
    mock_ic.click_absolute.return_value = True
    mock_ic.shift_click.return_value = True
    mock_ic.type_text.return_value = True
    mock_ic.hwnd = 99
    mock_ic.input_method = "sendinput"
    mock_ic.jitter_pct = 0.1
    mock_ic.interception_available = False
    mock_ic.is_connected.return_value = True
    mock_ic.find_target.return_value = None
    mock_ic.focus_now.return_value = True
    return HumanInputSystem(str(cfg_path), mock_ic), mock_ic


# ═══════════════════════════════════════════════════════════════════════
# Humanization disabled — passthrough paths
# ═══════════════════════════════════════════════════════════════════════

class TestHISDisabledPassthrough:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.his, self.ic = _make_his(tmp_path)
        self.his.enable_humanization(False)

    def teardown_method(self):
        self.his.close()

    def test_press_key_disabled_delegates(self):
        self.his.press_key(0x57)  # W
        self.ic.press_key.assert_called()

    def test_hold_key_disabled_delegates(self):
        self.his.hold_key(0x20, 0.3)  # Space
        self.ic.hold_key.assert_called()

    def test_type_text_disabled_delegates(self):
        self.his.type_text("abc")
        self.ic.type_text.assert_called_with("abc")

    def test_click_disabled_delegates(self):
        self.his.click(100, 200)
        self.ic.click.assert_called()

    def test_click_human_disabled_delegates(self):
        self.his.click_human(100, 200, "right")
        self.ic.click_human.assert_called()

    def test_click_absolute_disabled_delegates(self):
        self.his.click_absolute(300, 400)
        self.ic.click_absolute.assert_called()

    def test_shift_click_disabled_delegates(self):
        self.his.shift_click(150, 150)
        self.ic.shift_click.assert_called()

    def test_move_mouse_disabled_returns_true(self):
        result = self.his.move_mouse(500, 500)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════
# Error generation paths (wrong_key, double_press, hesitation, miss_click)
# ═══════════════════════════════════════════════════════════════════════

class TestHISErrorPaths:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.his, self.ic = _make_his(tmp_path)
        self.his.enable_humanization(True)

    def teardown_method(self):
        self.his.close()

    def test_wrong_key_error_fires_extra_press(self):
        """wrong_key: presses wrong key before correct one."""
        behavior = cast(Any, self.his._behavior)
        setattr(behavior, "should_generate_error", lambda: "wrong_key")
        setattr(behavior, "apply_wrong_key_error_vk", lambda vk: vk + 1)
        with patch("time.sleep"):
            self.his.press_key(0x41)  # A
        # At least 2 press_key calls (wrong + correct)
        assert self.ic.press_key.call_count >= 2

    def test_double_press_error_extra_press(self):
        """double_press: presses key once extra before the real press."""
        behavior = cast(Any, self.his._behavior)
        setattr(behavior, "should_generate_error", lambda: "double_press")
        setattr(behavior, "apply_double_press_error", lambda: 30.0)
        with patch("time.sleep"):
            self.his.press_key(0x41)
        assert self.ic.press_key.call_count >= 2

    def test_hesitation_error_sleeps(self):
        """hesitation: adds a sleep before the real keypress."""
        behavior = cast(Any, self.his._behavior)
        setattr(behavior, "should_generate_error", lambda: "hesitation")
        setattr(behavior, "apply_hesitation_delay", lambda: 500.0)
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda t: sleep_calls.append(t)):
            self.his.press_key(0x41)
        assert any(s > 0 for s in sleep_calls)

    def test_wrong_key_same_vk_does_not_double_press(self):
        """If wrong_key maps to same vk, no extra press."""
        behavior = cast(Any, self.his._behavior)
        setattr(behavior, "should_generate_error", lambda: "wrong_key")
        setattr(behavior, "apply_wrong_key_error_vk", lambda vk: vk)  # same key
        with patch("time.sleep"):
            self.his.press_key(0x41)
        assert self.ic.press_key.call_count == 1

    def test_click_miss_click_error(self):
        """miss_click: click goes to offset coords."""
        behavior = cast(Any, self.his._behavior)
        setattr(behavior, "should_generate_error", lambda: "miss_click")
        setattr(behavior, "apply_miss_click_offset", lambda x, y: (x + 10, y + 10))
        with patch("time.sleep"):
            self.his.click(200, 200)
        # Should have been called with offset coords
        args = self.ic.click.call_args[0]
        assert args[0] == 210 and args[1] == 210

    def test_click_hesitation_error_sleeps(self):
        behavior = cast(Any, self.his._behavior)
        setattr(behavior, "should_generate_error", lambda: "hesitation")
        setattr(behavior, "apply_hesitation_delay", lambda: 200.0)
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda t: sleep_calls.append(t)):
            self.his.click(300, 300)
        assert any(s > 0 for s in sleep_calls)

    def test_click_human_miss_click(self):
        behavior = cast(Any, self.his._behavior)
        setattr(behavior, "should_generate_error", lambda: "miss_click")
        setattr(behavior, "apply_miss_click_offset", lambda x, y: (x + 5, y + 5))
        with patch("time.sleep"):
            self.his.click_human(100, 100)
        args = self.ic.click_human.call_args[0]
        assert args[0] == 105 and args[1] == 105


# ═══════════════════════════════════════════════════════════════════════
# move_mouse — Bézier path execution
# ═══════════════════════════════════════════════════════════════════════

class TestHISMoveMouse:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.his, self.ic = _make_his(tmp_path)
        self.his.enable_humanization(True)

    def teardown_method(self):
        self.his.close()

    def test_move_mouse_returns_true(self):
        with patch("time.sleep"):
            result = self.his.move_mouse(400, 300)
        assert result is True

    def test_move_mouse_with_from_pos(self):
        with patch("time.sleep"):
            result = self.his.move_mouse(400, 300, from_pos=(100, 100))
        assert result is True

    def test_move_mouse_zero_distance_skips_loop(self):
        """generate_full_movement returning [] skips the sleep loop."""
        setattr(self.his._mouse, "generate_full_movement", lambda *_a, **_kw: [])
        with patch("time.sleep") as mock_sleep:
            result = self.his.move_mouse(100, 100, from_pos=(100, 100))
        assert result is True
        mock_sleep.assert_not_called()

    def test_move_mouse_records_metrics(self):
        initial = self.his.get_metrics()["total_inputs"]
        with patch("time.sleep"):
            self.his.move_mouse(500, 400, from_pos=(0, 0))
        new = self.his.get_metrics()["total_inputs"]
        assert new == initial + 1

    def test_move_mouse_via_arduino_when_available(self):
        """When arduino is_available, send_mouse_move is called per path point."""
        setattr(self.his._arduino, "is_available", lambda: True)
        send_mouse_move = MagicMock(return_value=True)
        setattr(self.his._arduino, "send_mouse_move", send_mouse_move)
        # Give a short path
        setattr(self.his._mouse, "generate_full_movement", lambda s, e, **kw: [(10, 10), (20, 20)])
        setattr(self.his._mouse, "calculate_velocity_profile", lambda n: [0.5] * n)
        with patch("time.sleep"):
            self.his.move_mouse(30, 30, from_pos=(0, 0))
        assert send_mouse_move.call_count == 2


# ═══════════════════════════════════════════════════════════════════════
# AFK pause
# ═══════════════════════════════════════════════════════════════════════

class TestHISAfkPause:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.his, self.ic = _make_his(tmp_path)
        self.his.enable_humanization(True)

    def teardown_method(self):
        self.his.close()

    def test_afk_pause_spawns_thread(self):
        setattr(self.his._behavior, "should_trigger_afk_pause", lambda: True)
        setattr(self.his._behavior, "generate_afk_duration", lambda: 0.01)
        with patch("time.sleep"):
            self.his.press_key(0x41)
        # Give thread time to start
        time.sleep(0.05)
        # _in_afk resets after worker finishes
        assert self.his._afk_thread is not None

    def test_second_afk_not_started_while_in_afk(self):
        self.his._in_afk = True
        self.his._start_afk_pause()  # should return early
        assert self.his._afk_thread is None

    def test_afk_worker_resets_enabled(self):
        """After AFK, humanization re-enables itself."""
        setattr(self.his._behavior, "generate_afk_duration", lambda: 0.01)
        reset_fatigue = MagicMock()
        setattr(self.his._behavior, "reset_fatigue_after_afk", reset_fatigue)
        with patch("time.sleep"):
            self.his._afk_pause_worker()
        assert self.his._enabled is True
        assert self.his._in_afk is False
        reset_fatigue.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# _execute_key_press — Arduino path
# ═══════════════════════════════════════════════════════════════════════

class TestHISExecuteKeyPress:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.his, self.ic = _make_his(tmp_path)

    def teardown_method(self):
        self.his.close()

    def test_arduino_path_used_when_available(self):
        setattr(self.his._arduino, "is_available", lambda: True)
        send_key_press = MagicMock(return_value=True)
        setattr(self.his._arduino, "send_key_press", send_key_press)
        result = self.his._execute_key_press(0x41, 0.08)
        assert result is True
        send_key_press.assert_called_once()

    def test_fallback_to_ic_when_arduino_fails(self):
        setattr(self.his._arduino, "is_available", lambda: True)
        setattr(self.his._arduino, "send_key_press", MagicMock(return_value=False))
        result = self.his._execute_key_press(0x41, 0.08)
        # arduino failed → falls through to ic
        self.ic.press_key.assert_called()

    def test_ic_used_when_arduino_unavailable(self):
        setattr(self.his._arduino, "is_available", lambda: False)
        result = self.his._execute_key_press(0x41, 0.05)
        self.ic.press_key.assert_called_with(0x41, 0.05)


# ═══════════════════════════════════════════════════════════════════════
# reload_config failure
# ═══════════════════════════════════════════════════════════════════════

class TestHISReloadConfig:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.his, _ = _make_his(tmp_path)

    def teardown_method(self):
        self.his.close()

    def test_reload_config_failure_returns_false(self):
        setattr(self.his._parser, "reload", MagicMock(side_effect=ValueError("bad yaml")))
        result = self.his.reload_config()
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# Passthrough property setters
# ═══════════════════════════════════════════════════════════════════════

class TestHISPassthroughProps:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.his, self.ic = _make_his(tmp_path)

    def teardown_method(self):
        self.his.close()

    def test_input_method_setter(self):
        self.his.input_method = "pico"
        assert self.ic.input_method == "pico"

    def test_jitter_pct_getter(self):
        self.ic.jitter_pct = 0.25
        assert self.his.jitter_pct == 0.25

    def test_jitter_pct_setter(self):
        self.his.jitter_pct = 0.33
        assert self.ic.jitter_pct == 0.33

    def test_interception_available(self):
        self.ic.interception_available = True
        assert self.his.interception_available is True

    def test_find_target(self):
        self.ic.find_target.return_value = "target"
        assert self.his.find_target() == "target"

    def test_focus_now(self):
        self.ic.focus_now.return_value = True
        assert self.his.focus_now() is True

    def test_hwnd_passthrough(self):
        self.ic.hwnd = 0xBEEF
        assert self.his.hwnd == 0xBEEF


# ═══════════════════════════════════════════════════════════════════════
# close()
# ═══════════════════════════════════════════════════════════════════════

class TestHISClose:
    def test_close_closes_arduino_and_metrics(self, tmp_path):
        his, _ = _make_his(tmp_path)
        arduino_close = MagicMock()
        metrics_close = MagicMock()
        setattr(his._arduino, "close", arduino_close)
        setattr(his._metrics, "close", metrics_close)
        his.close()
        arduino_close.assert_called_once()
        metrics_close.assert_called_once()

    def test_close_idempotent(self, tmp_path):
        his, _ = _make_his(tmp_path)
        his.close()
        his.close()  # second close should not raise
