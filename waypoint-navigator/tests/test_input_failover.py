"""Tests for InputController Arduino HID failover mechanism."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from src.input_controller import InputController


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_ctrl(**kwargs) -> InputController:
    """Create an InputController that won't try to find a window."""
    ctrl = InputController(target_title="Test", input_method="interception", **kwargs)
    ctrl._hwnd = 12345  # Fake connected window
    ctrl.is_connected = lambda: True  # type: ignore[assignment]
    return ctrl


def _make_arduino_mock(available: bool = True) -> MagicMock:
    """Create a mock ArduinoHIDController."""
    arduino = MagicMock()
    arduino.is_available.return_value = available
    arduino.send_key_press.return_value = True
    arduino.send_combo.return_value = True
    arduino.send_key_release.return_value = True
    arduino.send_mouse_move.return_value = True
    arduino.send_mouse_click.return_value = True
    return arduino


# ── set_arduino_failover ─────────────────────────────────────────────────────

class TestSetArduinoFailover:
    def test_register_arduino(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        ctrl.set_arduino_failover(arduino)
        assert ctrl._arduino_hid is arduino
        assert ctrl.using_arduino_failover is False  # Not active yet

    def test_no_arduino_by_default(self) -> None:
        ctrl = _make_ctrl()
        assert ctrl._arduino_hid is None
        assert ctrl._using_arduino_failover is False


# ── _interception_warn_fallback ──────────────────────────────────────────────

class TestInterceptionWarnFallback:
    def test_crashes_without_arduino(self) -> None:
        ctrl = _make_ctrl()
        with pytest.raises(RuntimeError, match="Driver failed"):
            ctrl._interception_warn_fallback("test_action")
        assert ctrl._interception_failed is True

    def test_failover_to_arduino_when_available(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock(available=True)
        ctrl.set_arduino_failover(arduino)

        # Should NOT raise
        ctrl._interception_warn_fallback("test_action")

        assert ctrl._interception_failed is True
        assert ctrl._using_arduino_failover is True

    def test_crashes_when_arduino_not_available(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock(available=False)
        ctrl.set_arduino_failover(arduino)

        with pytest.raises(RuntimeError, match="Driver failed"):
            ctrl._interception_warn_fallback("test_action")
        assert ctrl._interception_failed is True
        assert ctrl._using_arduino_failover is False


# ── press_key with Arduino failover ─────────────────────────────────────────

class TestPressKeyArduinoFailover:
    def test_press_key_uses_arduino_when_failover_active(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True
        ctrl._interception_failed = True

        result = ctrl.press_key(0x41, delay=0.05)  # 'A' key

        assert result is True
        arduino.send_key_press.assert_called_once()
        args = arduino.send_key_press.call_args[0]
        assert args[0] == "A"

    def test_press_key_returns_false_when_arduino_fails(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        arduino.send_key_press.return_value = False
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True
        ctrl._interception_failed = True

        assert ctrl.press_key(0x41, delay=0.05) is False

    def test_press_key_not_arduino_when_interception_works(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        ctrl.set_arduino_failover(arduino)
        # _using_arduino_failover is False

        # Mock interception to work
        mock_ctx = MagicMock()
        ctrl._interception_ctx = mock_ctx

        with patch.object(ctrl, "_press_interception") as mock_press:
            result = ctrl.press_key(0x41, delay=0.01)

        assert result is True
        arduino.send_key_press.assert_not_called()

    def test_emergency_stop_blocks_arduino(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True
        ctrl.emergency_stop()

        result = ctrl.press_key(0x41)
        assert result is False
        arduino.send_key_press.assert_not_called()


# ── hold_key with Arduino failover ──────────────────────────────────────────

class TestHoldKeyArduinoFailover:
    def test_hold_key_uses_arduino(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True

        result = ctrl.hold_key(0x41, duration=0.3)

        assert result is True
        arduino.send_key_press.assert_called_once()
        args = arduino.send_key_press.call_args[0]
        assert args[0] == "A"
        assert args[1] == 300  # duration in ms

    def test_hold_key_returns_false_when_arduino_fails(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        arduino.send_key_press.return_value = False
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True

        assert ctrl.hold_key(0x41, duration=0.3) is False


class TestTypeTextArduinoFailover:
    def test_type_text_uses_hid_names(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True

        assert ctrl.type_text("aq") is True
        sent = [call.args[0] for call in arduino.send_key_press.call_args_list]
        assert sent == ["A", "Q"]

    def test_type_text_returns_false_when_arduino_fails(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        arduino.send_key_press.side_effect = [True, False]
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True

        assert ctrl.type_text("ab") is False


# ── Properties ───────────────────────────────────────────────────────────────

class TestFailoverProperties:
    def test_using_arduino_failover_property(self) -> None:
        ctrl = _make_ctrl()
        assert ctrl.using_arduino_failover is False

        ctrl._using_arduino_failover = True
        assert ctrl.using_arduino_failover is True

    def test_interception_failed_latches(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        ctrl.set_arduino_failover(arduino)

        ctrl._interception_warn_fallback("test")

        assert ctrl._interception_failed is True
        # Even with Arduino failover, the interception_failed flag stays True
        assert ctrl._using_arduino_failover is True


# ── _press_two_keys with Arduino ─────────────────────────────────────────────

class TestPressTwoKeysArduinoFailover:
    def test_two_keys_uses_arduino(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True

        result = ctrl._press_two_keys(0x25, 0x26, delay=0.1)  # LEFT + UP

        assert result is True
        arduino.send_combo.assert_called_once_with("LEFT", "UP", 100)

    def test_two_keys_returns_false_when_combo_fails(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        arduino.send_combo.return_value = False
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True

        assert ctrl._press_two_keys(0x25, 0x26, delay=0.1) is False


class TestClickArduinoFailover:
    def test_click_returns_false_when_mouse_click_fails(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        arduino.send_mouse_click.return_value = False
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True

        with patch.object(ctrl, "_set_cursor_from_client", return_value=(100, 200)), \
             patch("src.input_controller.time.sleep"):
            assert ctrl.click(10, 20) is False

    def test_shift_click_returns_false_when_mouse_click_fails(self) -> None:
        ctrl = _make_ctrl()
        arduino = _make_arduino_mock()
        arduino.send_mouse_click.return_value = False
        ctrl.set_arduino_failover(arduino)
        ctrl._using_arduino_failover = True

        with patch.object(ctrl, "_set_cursor_from_client", return_value=(100, 200)), \
             patch("src.input_controller.time.sleep"):
            assert ctrl.shift_click(10, 20) is False
