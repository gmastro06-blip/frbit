"""Tests for PicoHIDController — Raspberry Pi Pico 2 USB HID integration."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch, PropertyMock, call
import pytest

from human_input_system.config.models import PicoConfig
from human_input_system.core.pico_hid_controller import PicoHIDController


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_config(**overrides) -> PicoConfig:
    defaults = dict(enabled=True, port="COM99", baudrate=115200, timeout=0.1,
                    retry_attempts=1)
    defaults.update(overrides)
    return PicoConfig(**defaults)


def _make_serial_mock(ping_response: str = "PONG") -> MagicMock:
    """Create a mock serial.Serial object."""
    ser = MagicMock()
    ser.readline.return_value = (ping_response + "\n").encode()
    ser.reset_input_buffer = MagicMock()
    return ser


def _make_controller(config: PicoConfig | None = None,
                     fallback: MagicMock | None = None) -> PicoHIDController:
    cfg = config or _make_config()
    fb = fallback or MagicMock()
    return PicoHIDController(config=cfg, fallback_controller=fb)


# ── PicoConfig ───────────────────────────────────────────────────────────────

class TestPicoConfig:
    def test_defaults(self) -> None:
        cfg = PicoConfig()
        assert cfg.enabled is False
        assert cfg.port is None
        assert cfg.baudrate == 115200
        assert cfg.timeout == 0.1
        assert cfg.retry_attempts == 3

    def test_validate_ok(self) -> None:
        cfg = PicoConfig(enabled=True, baudrate=115200, timeout=0.1)
        assert cfg.validate() is True

    def test_validate_bad_baudrate(self) -> None:
        cfg = PicoConfig(baudrate=0)
        assert cfg.validate() is False

    def test_validate_bad_timeout(self) -> None:
        cfg = PicoConfig(timeout=0)
        assert cfg.validate() is False


# ── Initialize ───────────────────────────────────────────────────────────────

class TestInitialize:
    def test_disabled_returns_false(self) -> None:
        ctrl = _make_controller(_make_config(enabled=False))
        assert ctrl.initialize() is False
        assert ctrl.is_available() is False

    def test_no_pyserial_returns_false(self) -> None:
        ctrl = _make_controller()
        with patch.dict("sys.modules", {"serial": None, "serial.tools": None,
                                        "serial.tools.list_ports": None}):
            result = ctrl.initialize()
        assert result is False

    @patch("human_input_system.core.pico_hid_controller.serial", create=True)
    def test_connect_success(self, mock_serial_mod) -> None:
        mock_serial = _make_serial_mock("PONG")
        mock_serial_mod.Serial.return_value = mock_serial

        ctrl = _make_controller()
        with patch("human_input_system.core.pico_hid_controller.time"):
            with patch.object(ctrl, "_try_connect", return_value=True):
                ctrl._available = True

        assert ctrl.is_available() is True

    def test_try_connect_ping_pong(self) -> None:
        mock_serial = _make_serial_mock("PONG")
        ctrl = _make_controller()

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.return_value = mock_serial

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            with patch("human_input_system.core.pico_hid_controller.time"):
                result = ctrl._try_connect("COM99")

        assert result is True
        assert ctrl.is_available() is True
        mock_serial.write.assert_called_once_with(b"PING\n")

    def test_try_connect_bad_response(self) -> None:
        mock_serial = _make_serial_mock("NOPE")
        ctrl = _make_controller()

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.return_value = mock_serial

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            with patch("human_input_system.core.pico_hid_controller.time"):
                result = ctrl._try_connect("COM99")

        assert result is False
        assert ctrl.is_available() is False
        mock_serial.close.assert_called_once()

    def test_try_connect_exception(self) -> None:
        ctrl = _make_controller()

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = OSError("port busy")

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            with patch("human_input_system.core.pico_hid_controller.time"):
                result = ctrl._try_connect("COM99")

        assert result is False

    def test_retry_attempts(self) -> None:
        cfg = _make_config(retry_attempts=3)
        ctrl = _make_controller(cfg)

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = OSError("port busy")

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            with patch("human_input_system.core.pico_hid_controller.time"):
                result = ctrl._try_connect("COM99")

        assert result is False
        assert mock_serial_mod.Serial.call_count == 3


# ── Close ────────────────────────────────────────────────────────────────────

class TestClose:
    def test_close_serial(self) -> None:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True

        ctrl.close()

        mock_serial.close.assert_called_once()
        assert ctrl._serial is None
        assert ctrl.is_available() is False

    def test_close_when_not_connected(self) -> None:
        ctrl = _make_controller()
        ctrl.close()  # Should not raise

    def test_close_ignores_serial_error(self) -> None:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.close.side_effect = OSError("already closed")
        ctrl._serial = mock_serial
        ctrl._available = True

        ctrl.close()  # Should not raise
        assert ctrl._serial is None


# ── Key Commands ─────────────────────────────────────────────────────────────

class TestKeyCommands:
    def _setup_controller(self) -> tuple[PicoHIDController, MagicMock]:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.readline.return_value = b"ACK\n"
        mock_serial.reset_input_buffer = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True
        return ctrl, mock_serial

    def test_send_key_press(self) -> None:
        ctrl, ser = self._setup_controller()
        result = ctrl.send_key_press("F1", 150)
        assert result is True
        ser.write.assert_called_once_with(b"KEY_PRESS|F1|150\n")

    def test_send_key_press_duration_int(self) -> None:
        ctrl, ser = self._setup_controller()
        ctrl.send_key_press("SPACE", 80.5)
        ser.write.assert_called_once_with(b"KEY_PRESS|SPACE|80\n")

    def test_send_key_release(self) -> None:
        ctrl, ser = self._setup_controller()
        result = ctrl.send_key_release("F1")
        assert result is True
        ser.write.assert_called_once_with(b"KEY_RELEASE|F1\n")

    def test_key_press_err_response(self) -> None:
        ctrl, ser = self._setup_controller()
        ser.readline.return_value = b"ERR\n"
        result = ctrl.send_key_press("INVALID", 100)
        assert result is False

    def test_key_press_not_available(self) -> None:
        ctrl = _make_controller()
        result = ctrl.send_key_press("F1", 100)
        assert result is False

    def test_key_press_increments_cmd_count(self) -> None:
        ctrl, _ = self._setup_controller()
        assert ctrl._cmd_count == 0
        ctrl.send_key_press("F1", 100)
        assert ctrl._cmd_count == 1
        ctrl.send_key_press("F2", 100)
        assert ctrl._cmd_count == 2


# ── Mouse Commands ───────────────────────────────────────────────────────────

class TestMouseCommands:
    def _setup_controller(self) -> tuple[PicoHIDController, MagicMock]:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.readline.return_value = b"ACK\n"
        mock_serial.reset_input_buffer = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True
        return ctrl, mock_serial

    def test_send_mouse_move_relative(self) -> None:
        ctrl, ser = self._setup_controller()
        result = ctrl.send_mouse_move(100, 50, relative=True)
        assert result is True
        ser.write.assert_called_once_with(b"MOUSE_MOVE|100|50|1\n")

    def test_send_mouse_move_absolute(self) -> None:
        ctrl, ser = self._setup_controller()
        result = ctrl.send_mouse_move(500, 300, relative=False)
        assert result is True
        ser.write.assert_called_once_with(b"MOUSE_MOVE|500|300|0\n")

    def test_send_mouse_click_left(self) -> None:
        ctrl, ser = self._setup_controller()
        result = ctrl.send_mouse_click("LEFT")
        assert result is True
        ser.write.assert_called_once_with(b"MOUSE_CLICK|LEFT\n")

    def test_send_mouse_click_right(self) -> None:
        ctrl, ser = self._setup_controller()
        result = ctrl.send_mouse_click("RIGHT")
        assert result is True
        ser.write.assert_called_once_with(b"MOUSE_CLICK|RIGHT\n")

    def test_send_mouse_scroll(self) -> None:
        ctrl, ser = self._setup_controller()
        result = ctrl.send_mouse_scroll(3)
        assert result is True
        ser.write.assert_called_once_with(b"MOUSE_SCROLL|3\n")

    def test_send_mouse_scroll_negative(self) -> None:
        ctrl, ser = self._setup_controller()
        result = ctrl.send_mouse_scroll(-2)
        assert result is True
        ser.write.assert_called_once_with(b"MOUSE_SCROLL|-2\n")


# ── Status Command ───────────────────────────────────────────────────────────

class TestStatusCommand:
    def test_status_returns_dict(self) -> None:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.readline.return_value = b"OK|123456|42\n"
        mock_serial.reset_input_buffer = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True

        result = ctrl.status()
        assert result == {"uptime_ms": 123456, "cmd_count": 42}

    def test_status_not_available(self) -> None:
        ctrl = _make_controller()
        assert ctrl.status() is None

    def test_status_bad_response(self) -> None:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.readline.return_value = b"ERR\n"
        mock_serial.reset_input_buffer = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True

        assert ctrl.status() is None


# ── Error Handling ───────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_serial_exception_marks_unavailable(self) -> None:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.write.side_effect = OSError("USB disconnected")
        mock_serial.reset_input_buffer = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True

        result = ctrl.send_key_press("F1", 100)
        assert result is False
        assert ctrl.is_available() is False

    def test_empty_response_returns_false(self) -> None:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.readline.return_value = b""
        mock_serial.reset_input_buffer = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True

        result = ctrl.send_key_press("F1", 100)
        assert result is False

    def test_timeout_empty_readline(self) -> None:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.readline.return_value = b"\n"
        mock_serial.reset_input_buffer = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True

        result = ctrl.send_key_press("F1", 100)
        assert result is False


# ── Thread Safety ────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_commands(self) -> None:
        ctrl = _make_controller()
        mock_serial = MagicMock()
        mock_serial.readline.return_value = b"ACK\n"
        mock_serial.reset_input_buffer = MagicMock()
        ctrl._serial = mock_serial
        ctrl._available = True

        results: list[bool] = []
        errors: list[Exception] = []

        def send_cmd(key: str) -> None:
            try:
                r = ctrl.send_key_press(key, 50)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=send_cmd, args=(f"F{i}",))
                   for i in range(1, 6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert all(results)
        assert mock_serial.write.call_count == 5


# ── Integration with InputController Failover ────────────────────────────────

class TestFailoverIntegration:
    def test_pico_as_failover_in_input_controller(self) -> None:
        from src.input_controller import InputController

        ctrl = InputController(target_title="Test", input_method="interception")
        ctrl._hwnd = 12345
        ctrl.is_connected = lambda: True  # type: ignore[assignment]

        pico = _make_controller()
        pico._available = True
        mock_serial = MagicMock()
        mock_serial.readline.return_value = b"ACK\n"
        mock_serial.reset_input_buffer = MagicMock()
        pico._serial = mock_serial

        ctrl.set_arduino_failover(pico)
        assert ctrl._arduino_hid is pico

        # Trigger failover
        ctrl._interception_warn_fallback("test")
        assert ctrl._using_arduino_failover is True

        # Now press_key should use pico
        result = ctrl.press_key(0x41, delay=0.05)
        assert result is True
        pico._serial.write.assert_called()

    def test_pico_is_available_interface(self) -> None:
        """PicoHIDController has the same interface as ArduinoHIDController."""
        pico = _make_controller()
        # Verify interface compatibility
        assert hasattr(pico, "is_available")
        assert hasattr(pico, "send_key_press")
        assert hasattr(pico, "send_key_release")
        assert hasattr(pico, "send_mouse_move")
        assert hasattr(pico, "send_mouse_click")
        assert hasattr(pico, "initialize")
        assert hasattr(pico, "close")


# ── Auto-detect ──────────────────────────────────────────────────────────────

class TestAutoDetect:
    def test_auto_detect_prioritizes_circuitpython(self) -> None:
        """When port is 'auto', CircuitPython-described ports go first."""
        ctrl = _make_controller(_make_config(port="auto"))

        mock_port_cp = MagicMock()
        mock_port_cp.device = "COM10"
        mock_port_cp.description = "CircuitPython CDC control"
        mock_port_cp.manufacturer = "Raspberry Pi"

        mock_port_other = MagicMock()
        mock_port_other.device = "COM3"
        mock_port_other.description = "USB Serial Device"
        mock_port_other.manufacturer = "FTDI"

        mock_serial_mod = MagicMock()
        mock_serial_mod.tools.list_ports.comports.return_value = [
            mock_port_other, mock_port_cp,
        ]

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            with patch.object(ctrl, "_try_connect", return_value=True) as mock_try:
                ctrl.initialize()

            # Should try COM10 (CircuitPython) first
            assert mock_try.call_args_list[0] == call("COM10")
