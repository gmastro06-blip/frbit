"""Tests for ArduinoHIDController — serial communication with mock."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from human_input_system.config.models import ArduinoConfig
from human_input_system.core.arduino_hid_controller import ArduinoHIDController


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctrl(enabled: bool = True, port: str = "COM3") -> tuple:
    """Create controller + mock fallback."""
    cfg = ArduinoConfig(enabled=enabled, port=port)
    fallback = MagicMock()
    ctrl = ArduinoHIDController(config=cfg, fallback_controller=fallback)
    return ctrl, fallback


def _mock_serial(pong: bool = True):
    """Create a mock serial.Serial that answers PONG or not."""
    mock_conn = MagicMock()
    if pong:
        mock_conn.readline.return_value = b"PONG\n"
    else:
        mock_conn.readline.return_value = b"NOPE\n"
    return mock_conn


# ---------------------------------------------------------------------------
# Tests — Constructor & Config
# ---------------------------------------------------------------------------

class TestArduinoInit:
    def test_disabled_config_returns_false(self):
        ctrl, _ = _make_ctrl(enabled=False)
        result = ctrl.initialize()
        assert result is False
        assert ctrl.is_available() is False

    def test_no_pyserial_returns_false(self):
        ctrl, _ = _make_ctrl()
        with patch.dict("sys.modules", {"serial": None, "serial.tools": None,
                                          "serial.tools.list_ports": None}):
            result = ctrl.initialize()
        assert result is False

    def test_constructor_stores_config(self):
        ctrl, fb = _make_ctrl(port="COM7")
        assert ctrl._cfg.port == "COM7"
        assert ctrl._fallback is fb
        assert ctrl._available is False

    def test_is_available_initially_false(self):
        ctrl, _ = _make_ctrl()
        assert ctrl.is_available() is False


# ---------------------------------------------------------------------------
# Tests — Connection
# ---------------------------------------------------------------------------

class TestArduinoConnect:
    @patch("human_input_system.core.arduino_hid_controller.time")
    def test_connect_success(self, mock_time):
        ctrl, _ = _make_ctrl(port="COM3")
        mock_conn = _mock_serial(pong=True)

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.return_value = mock_conn
        mock_serial_mod.tools.list_ports.comports.return_value = []

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            result = ctrl.initialize()

        assert result is True
        assert ctrl.is_available() is True
        mock_conn.write.assert_called_once_with(b"PING\n")

    @patch("human_input_system.core.arduino_hid_controller.time")
    def test_connect_no_pong(self, mock_time):
        ctrl, _ = _make_ctrl(port="COM3")
        mock_conn = _mock_serial(pong=False)

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.return_value = mock_conn
        mock_serial_mod.tools.list_ports.comports.return_value = []

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            result = ctrl.initialize()

        assert result is False
        mock_conn.close.assert_called_once()

    @patch("human_input_system.core.arduino_hid_controller.time")
    def test_auto_port_scans_all(self, mock_time):
        ctrl, _ = _make_ctrl(port="auto")

        # Simulate 2 ports, second one responds PONG
        port1 = MagicMock()
        port1.device = "COM3"
        port2 = MagicMock()
        port2.device = "COM4"

        mock_conn_fail = MagicMock()
        mock_conn_fail.readline.return_value = b"NOPE\n"
        mock_conn_ok = _mock_serial(pong=True)

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = [mock_conn_fail, mock_conn_ok]
        mock_serial_mod.tools.list_ports.comports.return_value = [port1, port2]

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            result = ctrl.initialize()

        assert result is True


# ---------------------------------------------------------------------------
# Tests — Commands
# ---------------------------------------------------------------------------

class TestArduinoCommands:
    def _connected_ctrl(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b"ACK\n"
        ctrl._serial = mock_conn
        ctrl._available = True
        return ctrl, mock_conn

    def test_send_key_press(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_key_press("F1", 120.0)
        assert result is True
        mock_conn.write.assert_called_once_with(b"KEY_PRESS|F1|120\n")

    def test_send_key_release(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_key_release("F5")
        assert result is True
        mock_conn.write.assert_called_once_with(b"KEY_RELEASE|F5\n")

    def test_send_mouse_move_relative(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_mouse_move(100, 200, relative=True)
        assert result is True
        mock_conn.write.assert_called_once_with(b"MOUSE_MOVE|100|200|1\n")

    def test_send_mouse_move_absolute(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_mouse_move(500, 300, relative=False)
        assert result is True
        mock_conn.write.assert_called_once_with(b"MOUSE_MOVE|500|300|0\n")

    def test_send_mouse_click(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_mouse_click("LEFT")
        assert result is True
        mock_conn.write.assert_called_once_with(b"MOUSE_CLICK|LEFT\n")

    def test_send_mouse_click_right(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_mouse_click("RIGHT")
        assert result is True
        mock_conn.write.assert_called_once_with(b"MOUSE_CLICK|RIGHT\n")

    def test_send_fails_when_not_available(self):
        ctrl, _ = _make_ctrl()
        assert ctrl.send_key_press("A", 80.0) is False
        assert ctrl.send_key_release("A") is False
        assert ctrl.send_mouse_move(10, 10) is False
        assert ctrl.send_mouse_click("LEFT") is False

    def test_unexpected_response_returns_false(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b"ERR\n"
        ctrl._serial = mock_conn
        ctrl._available = True
        result = ctrl.send_key_press("Q", 50.0)
        assert result is False

    def test_serial_exception_marks_unavailable(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.write.side_effect = OSError("disconnected")
        ctrl._serial = mock_conn
        ctrl._available = True
        result = ctrl.send_key_press("W", 80.0)
        assert result is False
        assert ctrl.is_available() is False


# ---------------------------------------------------------------------------
# Tests — Close
# ---------------------------------------------------------------------------

class TestArduinoClose:
    def test_close_serial(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        ctrl._serial = mock_conn
        ctrl._available = True
        ctrl.close()
        mock_conn.close.assert_called_once()
        assert ctrl._serial is None
        assert ctrl.is_available() is False

    def test_close_no_connection(self):
        ctrl, _ = _make_ctrl()
        ctrl.close()  # should not raise

    def test_close_exception_suppressed(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.close.side_effect = OSError("already closed")
        ctrl._serial = mock_conn
        ctrl._available = True
        ctrl.close()  # should not raise
        assert ctrl._serial is None


# ---------------------------------------------------------------------------
# Tests — Extended Coverage (Concurrency, Protocol, Integration)
# ---------------------------------------------------------------------------

class TestArduinoConcurrency:
    """Thread-safety of _send_command under concurrent access."""

    def test_concurrent_key_presses(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b"ACK\n"
        ctrl._serial = mock_conn
        ctrl._available = True

        errors: list[Exception] = []

        def worker(key: str, n: int) -> None:
            try:
                for _ in range(50):
                    ctrl.send_key_press(key, 80.0)
            except Exception as exc:
                errors.append(exc)

        import threading
        threads = [threading.Thread(target=worker, args=(f"F{i}", i)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0

    def test_concurrent_mixed_commands(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b"ACK\n"
        ctrl._serial = mock_conn
        ctrl._available = True

        errors: list[Exception] = []

        def key_worker() -> None:
            try:
                for _ in range(30):
                    ctrl.send_key_press("A", 80.0)
                    ctrl.send_key_release("A")
            except Exception as exc:
                errors.append(exc)

        def mouse_worker() -> None:
            try:
                for _ in range(30):
                    ctrl.send_mouse_move(100, 200)
                    ctrl.send_mouse_click("LEFT")
            except Exception as exc:
                errors.append(exc)

        import threading
        t1 = threading.Thread(target=key_worker)
        t2 = threading.Thread(target=mouse_worker)
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert len(errors) == 0

    def test_close_during_send(self):
        """Close during active sending should not crash."""
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b"ACK\n"
        ctrl._serial = mock_conn
        ctrl._available = True

        import threading
        def send_loop() -> None:
            for _ in range(100):
                ctrl.send_key_press("X", 50.0)

        t = threading.Thread(target=send_loop)
        t.start()
        ctrl.close()
        t.join()  # should not raise


class TestArduinoProtocolEdgeCases:
    """Protocol boundary conditions."""

    def _connected_ctrl(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b"ACK\n"
        ctrl._serial = mock_conn
        ctrl._available = True
        return ctrl, mock_conn

    def test_key_press_zero_duration(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_key_press("A", 0.0)
        assert result is True
        mock_conn.write.assert_called_once_with(b"KEY_PRESS|A|0\n")

    def test_key_press_large_duration(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_key_press("F12", 5000.0)
        assert result is True
        mock_conn.write.assert_called_once_with(b"KEY_PRESS|F12|5000\n")

    def test_mouse_move_negative_coords(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_mouse_move(-50, -100, relative=True)
        assert result is True
        mock_conn.write.assert_called_once_with(b"MOUSE_MOVE|-50|-100|1\n")

    def test_mouse_move_zero_coords(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_mouse_move(0, 0)
        assert result is True
        mock_conn.write.assert_called_once_with(b"MOUSE_MOVE|0|0|0\n")

    def test_mouse_move_max_coords(self):
        ctrl, mock_conn = self._connected_ctrl()
        result = ctrl.send_mouse_move(1920, 1080)
        assert result is True
        mock_conn.write.assert_called_once_with(b"MOUSE_MOVE|1920|1080|0\n")

    def test_empty_response_returns_false(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b"\n"
        ctrl._serial = mock_conn
        ctrl._available = True
        result = ctrl.send_key_press("A", 80.0)
        assert result is False

    def test_timeout_empty_bytes(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b""
        ctrl._serial = mock_conn
        ctrl._available = True
        result = ctrl.send_mouse_click("LEFT")
        assert result is False

    def test_partial_ack_returns_false(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.readline.return_value = b"AC\n"  # partial
        ctrl._serial = mock_conn
        ctrl._available = True
        result = ctrl.send_key_release("B")
        assert result is False

    def test_multiple_failures_keep_unavailable(self):
        ctrl, _ = _make_ctrl()
        mock_conn = MagicMock()
        mock_conn.write.side_effect = OSError("lost")
        ctrl._serial = mock_conn
        ctrl._available = True
        ctrl.send_key_press("A", 50.0)
        assert ctrl.is_available() is False
        # Second call should also fail gracefully
        assert ctrl.send_key_press("B", 50.0) is False


class TestArduinoAutoPort:
    """Extended auto-port scanning scenarios."""

    @patch("human_input_system.core.arduino_hid_controller.time")
    def test_auto_port_no_devices(self, mock_time):
        ctrl, _ = _make_ctrl(port="auto")
        mock_serial_mod = MagicMock()
        mock_serial_mod.tools.list_ports.comports.return_value = []

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            result = ctrl.initialize()
        assert result is False

    @patch("human_input_system.core.arduino_hid_controller.time")
    def test_auto_port_all_fail(self, mock_time):
        ctrl, _ = _make_ctrl(port="auto")
        port1 = MagicMock(); port1.device = "COM3"
        port2 = MagicMock(); port2.device = "COM4"

        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = OSError("no device")
        mock_serial_mod.tools.list_ports.comports.return_value = [port1, port2]

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            result = ctrl.initialize()
        assert result is False

    @patch("human_input_system.core.arduino_hid_controller.time")
    def test_explicit_port_connect_failure(self, mock_time):
        ctrl, _ = _make_ctrl(port="COM99")
        mock_serial_mod = MagicMock()
        mock_serial_mod.Serial.side_effect = OSError("port not found")
        mock_serial_mod.tools.list_ports.comports.return_value = []

        with patch.dict("sys.modules", {
            "serial": mock_serial_mod,
            "serial.tools": mock_serial_mod.tools,
            "serial.tools.list_ports": mock_serial_mod.tools.list_ports,
        }):
            result = ctrl.initialize()
        assert result is False


class TestArduinoFallbackIntegration:
    """Verify fallback controller is stored correctly."""

    def test_fallback_stored(self):
        ctrl, fb = _make_ctrl()
        assert ctrl._fallback is fb
        assert ctrl._fallback is not None

    def test_unavailable_returns_false_not_fallback(self):
        """When Arduino unavailable, commands return False (HIS handles fallback)."""
        ctrl, fb = _make_ctrl()
        assert ctrl.send_key_press("A", 80.0) is False
        fb.press_key.assert_not_called()  # fallback NOT called by controller itself


class TestArduinoSendCombo:
    """Tests for send_combo (lines 115-117)."""

    def _connected_ctrl(self):
        ctrl, _ = _make_ctrl()
        conn = MagicMock()
        conn.readline.return_value = b"ACK\n"
        ctrl._serial = conn
        ctrl._available = True
        return ctrl, conn

    def test_send_combo_returns_true_on_ack(self):
        ctrl, _ = self._connected_ctrl()
        assert ctrl.send_combo("CTRL", "C") is True

    def test_send_combo_default_duration(self):
        ctrl, conn = self._connected_ctrl()
        ctrl.send_combo("ALT", "F4")
        conn.write.assert_called_once_with(b"COMBO|ALT|F4|80\n")

    def test_send_combo_custom_duration(self):
        ctrl, conn = self._connected_ctrl()
        ctrl.send_combo("SHIFT", "A", duration_ms=120)
        conn.write.assert_called_once_with(b"COMBO|SHIFT|A|120\n")

    def test_send_combo_returns_false_when_unavailable(self):
        ctrl, _ = _make_ctrl()
        assert ctrl.send_combo("CTRL", "Z") is False

    def test_send_combo_nack_returns_false(self):
        ctrl, _ = _make_ctrl()
        conn = MagicMock()
        conn.readline.return_value = b"NAK\n"
        ctrl._serial = conn
        ctrl._available = True
        assert ctrl.send_combo("CTRL", "X") is False


class TestArduinoSendStatus:
    """Tests for send_status (lines 119-134)."""

    def _connected_ctrl(self, response: str) -> tuple:
        ctrl, _ = _make_ctrl()
        conn = MagicMock()
        conn.readline.return_value = (response + "\n").encode()
        ctrl._serial = conn
        ctrl._available = True
        return ctrl, conn

    def test_send_status_returns_tuple_on_ok(self):
        ctrl, _ = self._connected_ctrl("OK|5000|42")
        result = ctrl.send_status()
        assert result == (5000, 42)

    def test_send_status_sends_status_command(self):
        ctrl, conn = self._connected_ctrl("OK|1000|1")
        ctrl.send_status()
        conn.write.assert_called_once_with(b"STATUS\n")

    def test_send_status_returns_none_when_unavailable(self):
        ctrl, _ = _make_ctrl()
        assert ctrl.send_status() is None

    def test_send_status_returns_none_on_non_ok_response(self):
        ctrl, _ = self._connected_ctrl("ERR")
        assert ctrl.send_status() is None

    def test_send_status_returns_none_on_short_ok(self):
        ctrl, _ = self._connected_ctrl("OK|5000")  # missing cmd_count field
        assert ctrl.send_status() is None

    def test_send_status_exception_sets_unavailable(self):
        ctrl, _ = _make_ctrl()
        conn = MagicMock()
        conn.write.side_effect = OSError("broken")
        ctrl._serial = conn
        ctrl._available = True
        result = ctrl.send_status()
        assert result is None
        assert not ctrl.is_available()
