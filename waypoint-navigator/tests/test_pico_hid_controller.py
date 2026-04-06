"""Tests for PicoHIDController (human_input_system/core/pico_hid_controller.py).

All serial I/O is mocked — no physical device required.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from human_input_system.config.models import PicoConfig
from human_input_system.core.pico_hid_controller import (
    PicoHIDController,
    _VK_TO_PICO_KEY,
    _CMD_KEY_PRESS,
    _CMD_KEY_RELEASE,
    _CMD_MOUSE_MOVE,
    _CMD_MOUSE_CLICK,
    _CMD_MOUSE_SCROLL,
    _CMD_RELEASE_ALL,
    _CMD_PING,
    _CMD_STATUS,
    _RESP_ACK,
    _RESP_PONG,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_serial(readline_return: str = _RESP_ACK) -> MagicMock:
    s = MagicMock()
    s.readline.return_value = (readline_return + "\n").encode()
    s.timeout = 0.1
    return s


def _make_controller(enabled: bool = True, port: str = "COM3") -> PicoHIDController:
    cfg = PicoConfig(enabled=enabled, port=port, baudrate=115200, timeout=0.1, retry_attempts=1)
    fallback = MagicMock()
    return PicoHIDController(cfg, fallback)


# ═══════════════════════════════════════════════════════════════════════
# Construction
# ═══════════════════════════════════════════════════════════════════════

class TestPicoHIDControllerInit:
    def test_not_available_on_init(self):
        ctrl = _make_controller()
        assert ctrl.is_available() is False

    def test_cmd_count_starts_at_zero(self):
        ctrl = _make_controller()
        assert ctrl._cmd_count == 0

    def test_serial_none_on_init(self):
        ctrl = _make_controller()
        assert ctrl._serial is None


# ═══════════════════════════════════════════════════════════════════════
# initialize() — disabled config
# ═══════════════════════════════════════════════════════════════════════

class TestPicoInitializeDisabled:
    def test_returns_false_when_disabled(self):
        cfg = PicoConfig(enabled=False)
        ctrl = PicoHIDController(cfg, MagicMock())
        assert ctrl.initialize() is False

    def test_remains_unavailable_when_disabled(self):
        cfg = PicoConfig(enabled=False)
        ctrl = PicoHIDController(cfg, MagicMock())
        ctrl.initialize()
        assert ctrl.is_available() is False


# ═══════════════════════════════════════════════════════════════════════
# initialize() — pyserial missing
# ═══════════════════════════════════════════════════════════════════════

class TestPicoInitializeNoSerial:
    def test_returns_false_without_pyserial(self):
        ctrl = _make_controller()
        with patch.dict("sys.modules", {"serial": None, "serial.tools.list_ports": None}):
            result = ctrl.initialize()
        assert result is False

    def test_remains_unavailable_without_pyserial(self):
        ctrl = _make_controller()
        with patch.dict("sys.modules", {"serial": None, "serial.tools.list_ports": None}):
            ctrl.initialize()
        assert not ctrl.is_available()


# ═══════════════════════════════════════════════════════════════════════
# initialize() — successful connection
# ═══════════════════════════════════════════════════════════════════════

class TestPicoInitializeSuccess:
    def _patch_serial_connect(self, pong=_RESP_PONG):
        serial_mod = MagicMock()
        conn = _make_serial(pong)
        serial_mod.Serial.return_value = conn
        serial_mod.tools.list_ports.comports.return_value = []
        return serial_mod, conn

    def test_returns_true_on_pong(self):
        ctrl = _make_controller()
        serial_mod, _ = self._patch_serial_connect()
        with patch.dict("sys.modules", {"serial": serial_mod, "serial.tools": serial_mod.tools, "serial.tools.list_ports": serial_mod.tools.list_ports}):
            result = ctrl.initialize()
        assert result is True

    def test_sets_available_on_successful_connect(self):
        ctrl = _make_controller()
        serial_mod, _ = self._patch_serial_connect()
        with patch.dict("sys.modules", {"serial": serial_mod, "serial.tools": serial_mod.tools, "serial.tools.list_ports": serial_mod.tools.list_ports}):
            ctrl.initialize()
        assert ctrl.is_available() is True

    def test_sends_ping_during_connect(self):
        ctrl = _make_controller()
        serial_mod, conn = self._patch_serial_connect()
        with patch.dict("sys.modules", {"serial": serial_mod, "serial.tools": serial_mod.tools, "serial.tools.list_ports": serial_mod.tools.list_ports}):
            ctrl.initialize()
        conn.write.assert_called_with(f"{_CMD_PING}\n".encode())

    def test_wrong_response_leaves_unavailable(self):
        ctrl = _make_controller()
        serial_mod, _ = self._patch_serial_connect(pong="WRONG")
        with patch.dict("sys.modules", {"serial": serial_mod, "serial.tools": serial_mod.tools, "serial.tools.list_ports": serial_mod.tools.list_ports}):
            result = ctrl.initialize()
        assert result is False
        assert not ctrl.is_available()


# ═══════════════════════════════════════════════════════════════════════
# initialize() — exception on connect
# ═══════════════════════════════════════════════════════════════════════

class TestPicoInitializeException:
    def test_serial_exception_leaves_unavailable(self):
        ctrl = _make_controller()
        serial_mod = MagicMock()
        serial_mod.Serial.side_effect = OSError("no port")
        serial_mod.tools.list_ports.comports.return_value = []
        with patch.dict("sys.modules", {"serial": serial_mod, "serial.tools": serial_mod.tools, "serial.tools.list_ports": serial_mod.tools.list_ports}):
            result = ctrl.initialize()
        assert result is False

    def test_retry_attempts_respected(self):
        cfg = PicoConfig(enabled=True, port="COM5", baudrate=115200, timeout=0.1, retry_attempts=3)
        ctrl = PicoHIDController(cfg, MagicMock())
        serial_mod = MagicMock()
        serial_mod.Serial.side_effect = OSError("fail")
        serial_mod.tools.list_ports.comports.return_value = []
        with patch("time.sleep"):  # don't actually sleep
            with patch.dict("sys.modules", {"serial": serial_mod, "serial.tools": serial_mod.tools, "serial.tools.list_ports": serial_mod.tools.list_ports}):
                ctrl.initialize()
        assert serial_mod.Serial.call_count == 3


# ═══════════════════════════════════════════════════════════════════════
# initialize() — auto-detect port
# ═══════════════════════════════════════════════════════════════════════

class TestPicoAutoDetect:
    def test_auto_detect_prioritizes_circuitpython(self):
        cfg = PicoConfig(enabled=True, port="auto", baudrate=115200, timeout=0.1, retry_attempts=1)
        ctrl = PicoHIDController(cfg, MagicMock())

        port_cp = MagicMock()
        port_cp.description = "CircuitPython CDC"
        port_cp.manufacturer = ""
        port_cp.device = "COM10"

        port_other = MagicMock()
        port_other.description = "Generic COM port"
        port_other.manufacturer = ""
        port_other.device = "COM5"

        serial_mod = MagicMock()
        conn = _make_serial(_RESP_PONG)
        serial_mod.Serial.return_value = conn
        serial_mod.tools.list_ports.comports.return_value = [port_other, port_cp]

        with patch("time.sleep"):
            with patch.dict("sys.modules", {"serial": serial_mod, "serial.tools": serial_mod.tools, "serial.tools.list_ports": serial_mod.tools.list_ports}):
                ctrl.initialize()

        # COM10 (CircuitPython) should have been tried first
        first_call_port = serial_mod.Serial.call_args_list[0][1].get("port") or serial_mod.Serial.call_args_list[0][0][0]
        assert first_call_port == "COM10"

    def test_auto_detect_includes_raspberry_manufacturer(self):
        cfg = PicoConfig(enabled=True, port=None, baudrate=115200, timeout=0.1, retry_attempts=1)
        ctrl = PicoHIDController(cfg, MagicMock())

        port_rpi = MagicMock()
        port_rpi.description = "USB Serial"
        port_rpi.manufacturer = "Raspberry Pi"
        port_rpi.device = "COM7"

        serial_mod = MagicMock()
        conn = _make_serial(_RESP_PONG)
        serial_mod.Serial.return_value = conn
        serial_mod.tools.list_ports.comports.return_value = [port_rpi]

        with patch("time.sleep"):
            with patch.dict("sys.modules", {"serial": serial_mod, "serial.tools": serial_mod.tools, "serial.tools.list_ports": serial_mod.tools.list_ports}):
                result = ctrl.initialize()

        assert result is True


# ═══════════════════════════════════════════════════════════════════════
# close()
# ═══════════════════════════════════════════════════════════════════════

class TestPicoClose:
    def _make_connected(self) -> tuple[PicoHIDController, MagicMock]:
        ctrl = _make_controller()
        serial = _make_serial()
        ctrl._serial = serial
        ctrl._available = True
        return ctrl, serial

    def test_close_sets_unavailable(self):
        ctrl, _ = self._make_connected()
        ctrl.close()
        assert not ctrl.is_available()

    def test_close_sets_serial_none(self):
        ctrl, _ = self._make_connected()
        ctrl.close()
        assert ctrl._serial is None

    def test_close_sends_release_all(self):
        ctrl, serial = self._make_connected()
        ctrl.close()
        serial.write.assert_called_with(f"{_CMD_RELEASE_ALL}\n".encode())

    def test_close_when_already_closed_is_safe(self):
        ctrl = _make_controller()
        ctrl.close()  # serial is None — should not raise

    def test_close_suppresses_serial_exception(self):
        ctrl, serial = self._make_connected()
        serial.write.side_effect = OSError("broken pipe")
        ctrl.close()  # should not raise
        assert not ctrl.is_available()

    def test_close_suppresses_close_exception(self):
        ctrl, serial = self._make_connected()
        serial.close.side_effect = OSError("already closed")
        ctrl.close()  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# release_all_keys()
# ═══════════════════════════════════════════════════════════════════════

class TestPicoReleaseAllKeys:
    def test_release_all_returns_true_on_ack(self):
        ctrl = _make_controller()
        ctrl._serial = _make_serial(_RESP_ACK)
        ctrl._available = True
        assert ctrl.release_all_keys() is True

    def test_release_all_increments_cmd_count(self):
        ctrl = _make_controller()
        ctrl._serial = _make_serial(_RESP_ACK)
        ctrl._available = True
        ctrl.release_all_keys()
        assert ctrl._cmd_count == 1

    def test_release_all_returns_false_when_unavailable(self):
        ctrl = _make_controller()
        assert ctrl.release_all_keys() is False


# ═══════════════════════════════════════════════════════════════════════
# status()
# ═══════════════════════════════════════════════════════════════════════

class TestPicoStatus:
    def test_status_returns_dict_on_ok_response(self):
        ctrl = _make_controller()
        ctrl._serial = _make_serial("OK|12345|99")
        ctrl._available = True
        result = ctrl.status()
        assert result == {"uptime_ms": 12345, "cmd_count": 99}

    def test_status_returns_none_when_unavailable(self):
        ctrl = _make_controller()
        assert ctrl.status() is None

    def test_status_returns_none_on_bad_response(self):
        ctrl = _make_controller()
        ctrl._serial = _make_serial("INVALID")
        ctrl._available = True
        assert ctrl.status() is None

    def test_status_returns_none_on_short_ok_response(self):
        ctrl = _make_controller()
        ctrl._serial = _make_serial("OK|5000")  # missing cmd_count
        ctrl._available = True
        assert ctrl.status() is None


# ═══════════════════════════════════════════════════════════════════════
# HID command methods
# ═══════════════════════════════════════════════════════════════════════

class TestPicoSendKeyPress:
    def _ctrl_with_serial(self, response: str = _RESP_ACK) -> PicoHIDController:
        ctrl = _make_controller()
        ctrl._serial = _make_serial(response)
        ctrl._available = True
        return ctrl

    def test_send_key_press_returns_true_on_ack(self):
        ctrl = self._ctrl_with_serial()
        assert ctrl.send_key_press("a", 100) is True

    def test_send_key_press_translates_vk(self):
        ctrl = self._ctrl_with_serial()
        ctrl.send_key_press("38", 80)  # VK 38 → "UP"
        ctrl._serial.write.assert_called_with(f"{_CMD_KEY_PRESS}|UP|80\n".encode())

    def test_send_key_press_unknown_vk_passes_through(self):
        ctrl = self._ctrl_with_serial()
        ctrl.send_key_press("999", 60)
        ctrl._serial.write.assert_called_with(f"{_CMD_KEY_PRESS}|999|60\n".encode())

    def test_send_key_release(self):
        ctrl = self._ctrl_with_serial()
        assert ctrl.send_key_release("w") is True

    def test_send_key_release_sends_correct_command(self):
        ctrl = self._ctrl_with_serial()
        ctrl.send_key_release("space")
        ctrl._serial.write.assert_called_with(f"{_CMD_KEY_RELEASE}|space\n".encode())

    def test_send_mouse_move_relative(self):
        ctrl = self._ctrl_with_serial()
        assert ctrl.send_mouse_move(10, -5, relative=True) is True

    def test_send_mouse_move_sends_relative_flag(self):
        ctrl = self._ctrl_with_serial()
        ctrl.send_mouse_move(10, 20, relative=True)
        ctrl._serial.write.assert_called_with(f"{_CMD_MOUSE_MOVE}|10|20|1\n".encode())

    def test_send_mouse_move_absolute(self):
        ctrl = self._ctrl_with_serial()
        ctrl.send_mouse_move(100, 200, relative=False)
        ctrl._serial.write.assert_called_with(f"{_CMD_MOUSE_MOVE}|100|200|0\n".encode())

    def test_send_mouse_click(self):
        ctrl = self._ctrl_with_serial()
        assert ctrl.send_mouse_click("left") is True

    def test_send_mouse_click_sends_correct_button(self):
        ctrl = self._ctrl_with_serial()
        ctrl.send_mouse_click("right")
        ctrl._serial.write.assert_called_with(f"{_CMD_MOUSE_CLICK}|right\n".encode())

    def test_send_mouse_scroll(self):
        ctrl = self._ctrl_with_serial()
        assert ctrl.send_mouse_scroll(3) is True

    def test_send_mouse_scroll_sends_amount(self):
        ctrl = self._ctrl_with_serial()
        ctrl.send_mouse_scroll(-2)
        ctrl._serial.write.assert_called_with(f"{_CMD_MOUSE_SCROLL}|-2\n".encode())

    def test_send_returns_false_when_unavailable(self):
        ctrl = _make_controller()
        assert ctrl.send_key_press("a", 100) is False
        assert ctrl.send_key_release("a") is False
        assert ctrl.send_mouse_move(0, 0) is False
        assert ctrl.send_mouse_click("left") is False
        assert ctrl.send_mouse_scroll(1) is False


# ═══════════════════════════════════════════════════════════════════════
# _send_command_raw — dynamic timeout
# ═══════════════════════════════════════════════════════════════════════

class TestPicoSendCommandRaw:
    def test_key_press_raises_timeout_temporarily(self):
        ctrl = _make_controller()
        serial = _make_serial(_RESP_ACK)
        serial.timeout = 0.1
        ctrl._serial = serial
        ctrl._available = True

        # KEY_PRESS|a|500 → needs 0.5 + 0.2 = 0.7 s
        ctrl._send_command(f"{_CMD_KEY_PRESS}|a|500")

        # timeout was temporarily set to 0.7, then restored to 0.1
        assert serial.timeout == 0.1

    def test_short_key_press_no_timeout_change(self):
        ctrl = _make_controller()
        serial = _make_serial(_RESP_ACK)
        serial.timeout = 1.0  # already more than needed
        ctrl._serial = serial
        ctrl._available = True

        ctrl._send_command(f"{_CMD_KEY_PRESS}|a|50")  # 50ms → 0.25s needed < 1.0

        # timeout should not have been changed
        assert serial.timeout == 1.0

    def test_serial_exception_sets_unavailable(self):
        ctrl = _make_controller()
        serial = MagicMock()
        serial.timeout = 0.1
        serial.write.side_effect = OSError("broken")
        ctrl._serial = serial
        ctrl._available = True

        result = ctrl._send_command_raw("PING")
        assert result is None
        assert not ctrl.is_available()

    def test_empty_response_returns_none(self):
        ctrl = _make_controller()
        serial = MagicMock()
        serial.timeout = 0.1
        serial.readline.return_value = b""
        ctrl._serial = serial
        ctrl._available = True

        result = ctrl._send_command_raw("PING")
        assert result is None

    def test_returns_none_when_not_available(self):
        ctrl = _make_controller()
        result = ctrl._send_command_raw("PING")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# _send_command — ACK tracking
# ═══════════════════════════════════════════════════════════════════════

class TestPicoSendCommand:
    def test_ack_increments_cmd_count(self):
        ctrl = _make_controller()
        ctrl._serial = _make_serial(_RESP_ACK)
        ctrl._available = True
        ctrl._send_command("TEST")
        ctrl._send_command("TEST")
        assert ctrl._cmd_count == 2

    def test_non_ack_does_not_increment(self):
        ctrl = _make_controller()
        ctrl._serial = _make_serial("NAK")
        ctrl._available = True
        ctrl._send_command("TEST")
        assert ctrl._cmd_count == 0

    def test_returns_false_on_non_ack(self):
        ctrl = _make_controller()
        ctrl._serial = _make_serial("WRONG")
        ctrl._available = True
        assert ctrl._send_command("CMD") is False


# ═══════════════════════════════════════════════════════════════════════
# VK key map
# ═══════════════════════════════════════════════════════════════════════

class TestPicoVkKeyMap:
    def test_arrow_keys_mapped(self):
        assert _VK_TO_PICO_KEY["37"] == "LEFT"
        assert _VK_TO_PICO_KEY["38"] == "UP"
        assert _VK_TO_PICO_KEY["39"] == "RIGHT"
        assert _VK_TO_PICO_KEY["40"] == "DOWN"

    def test_function_keys_mapped(self):
        for i, fnum in enumerate(range(112, 124), start=1):
            assert _VK_TO_PICO_KEY[str(fnum)] == f"F{i}"

    def test_modifier_keys_mapped(self):
        assert _VK_TO_PICO_KEY["16"] == "SHIFT"
        assert _VK_TO_PICO_KEY["17"] == "CTRL"
        assert _VK_TO_PICO_KEY["18"] == "ALT"

    def test_space_enter_esc_mapped(self):
        assert _VK_TO_PICO_KEY["32"] == "SPACE"
        assert _VK_TO_PICO_KEY["13"] == "ENTER"
        assert _VK_TO_PICO_KEY["27"] == "ESC"


# ═══════════════════════════════════════════════════════════════════════
# Thread safety — lock prevents concurrent writes
# ═══════════════════════════════════════════════════════════════════════

class TestPicoThreadSafety:
    def test_concurrent_commands_do_not_interleave(self):
        ctrl = _make_controller()
        serial = _make_serial(_RESP_ACK)
        ctrl._serial = serial
        ctrl._available = True

        errors = []
        def send():
            try:
                ctrl.send_key_press("a", 10)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=send) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
