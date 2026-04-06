"""PicoHIDController — USB serial communication with Raspberry Pi Pico 2 HID."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from ..config.models import PicoConfig

_log = logging.getLogger("wn.ph")

# Protocol commands — same as Arduino but Pico supports extras
_CMD_KEY_PRESS = "KEY_PRESS"
_CMD_KEY_RELEASE = "KEY_RELEASE"
_CMD_MOUSE_MOVE = "MOUSE_MOVE"
_CMD_MOUSE_CLICK = "MOUSE_CLICK"
_CMD_MOUSE_SCROLL = "MOUSE_SCROLL"
_CMD_RELEASE_ALL = "RELEASE_ALL"
_CMD_PING = "PING"
_CMD_STATUS = "STATUS"
_RESP_ACK = "ACK"
_RESP_PONG = "PONG"

# ── VK code (decimal string) → Pico2 key name ────────────────────────────────
# Callers send str(vk) but the Pico firmware expects named keys like "UP".
_VK_TO_PICO_KEY: dict[str, str] = {
    "37": "LEFT",   "38": "UP",    "39": "RIGHT",  "40": "DOWN",
    "65": "a",      "68": "d",     "83": "s",      "87": "w",
    "32": "SPACE",  "13": "ENTER", "27": "ESC",    "9":  "TAB",
    "16": "SHIFT",  "17": "CTRL",  "18": "ALT",
    "112": "F1",  "113": "F2",  "114": "F3",  "115": "F4",
    "116": "F5",  "117": "F6",  "118": "F7",  "119": "F8",
    "120": "F9",  "121": "F10", "122": "F11", "123": "F12",
}


class PicoHIDController:
    """Controls a Raspberry Pi Pico 2 as a real USB HID device.

    If the Pico is not available, all calls return False and the caller
    should fall back to the fallback_controller (InputController).

    The Pico 2 runs CircuitPython firmware (pico2/code.py) that receives
    serial commands and emulates real USB keyboard/mouse events
    indistinguishable from physical hardware.
    """

    def __init__(self, config: PicoConfig, fallback_controller: Any) -> None:
        self._cfg = config
        self._fallback = fallback_controller
        self._serial: Any = None
        self._available = False
        self._lock = threading.Lock()
        self._cmd_count = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Detect and connect to the Pico 2. Returns True if available."""
        if not self._cfg.enabled:
            _log.info("Pico HID disabled in config")
            return False

        try:
            import serial
            import serial.tools.list_ports
        except ImportError:
            _log.warning("pyserial not installed — Pico HID unavailable")
            return False

        port = self._cfg.port
        candidates: list[str] = []

        if port and port != "auto":
            candidates.append(port)
        else:
            # Auto-detect: look for Pico 2 (CircuitPython CDC)
            for p in serial.tools.list_ports.comports():
                desc = (p.description or "").lower()
                mfr = (p.manufacturer or "").lower()
                # CircuitPython on Pico 2 shows as "CircuitPython CDC"
                # or manufacturer "Raspberry Pi"
                if any(tag in desc for tag in ("circuitpython", "pico", "rp2350")):
                    candidates.insert(0, p.device)  # prioritize
                elif "raspberry" in mfr:
                    candidates.insert(0, p.device)
                else:
                    candidates.append(p.device)

        for port_name in candidates:
            if self._try_connect(port_name):
                return True

        _log.info("No Pico 2 device found")
        return False

    def _try_connect(self, port_name: str) -> bool:
        """Attempt connection on a single port."""
        try:
            import serial
        except ImportError:
            return False

        retries = max(1, self._cfg.retry_attempts)
        for attempt in range(retries):
            try:
                conn = serial.Serial(
                    port=port_name,
                    baudrate=self._cfg.baudrate,
                    timeout=self._cfg.timeout,
                )
                time.sleep(0.05)  # CircuitPython boot settle time
                # Flush any stale data
                conn.reset_input_buffer()
                conn.write(f"{_CMD_PING}\n".encode())
                resp = conn.readline().decode("utf-8", "ignore").strip()
                if resp == _RESP_PONG:
                    self._serial = conn
                    self._available = True
                    _log.info("Pico 2 connected on %s", port_name)
                    return True
                conn.close()
            except Exception as exc:
                _log.debug(
                    "Pico not available on %s (attempt %d): %s",
                    port_name, attempt + 1, exc,
                )
                time.sleep(0.1)

        return False

    def release_all_keys(self) -> bool:
        """Send RELEASE_ALL to release every held key and mouse button."""
        return self._send_command(_CMD_RELEASE_ALL)

    def close(self) -> None:
        """Release all keys, then close the serial connection."""
        with self._lock:
            if self._serial is not None:
                try:
                    # Best-effort release before closing
                    self._serial.reset_input_buffer()
                    self._serial.write(f"{_CMD_RELEASE_ALL}\n".encode())
                    self._serial.flush()
                except Exception:
                    pass
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                self._available = False
                _log.info("Pico 2 connection closed (keys released)")

    def is_available(self) -> bool:
        return self._available

    def status(self) -> Optional[dict]:
        """Query device status. Returns dict with uptime_ms and cmd_count, or None."""
        resp = self._send_command_raw(_CMD_STATUS)
        if resp and resp.startswith("OK|"):
            parts = resp.split("|")
            if len(parts) >= 3:
                return {
                    "uptime_ms": int(parts[1]),
                    "cmd_count": int(parts[2]),
                }
        return None

    # ------------------------------------------------------------------
    # HID Commands
    # ------------------------------------------------------------------

    def send_key_press(self, key: str, duration: float) -> bool:
        """KEY_PRESS|{key}|{duration_ms}"""
        key = _VK_TO_PICO_KEY.get(key, key)
        return self._send_command(f"{_CMD_KEY_PRESS}|{key}|{int(duration)}")

    def send_key_release(self, key: str) -> bool:
        """KEY_RELEASE|{key}"""
        return self._send_command(f"{_CMD_KEY_RELEASE}|{key}")

    def send_mouse_move(self, x: int, y: int, relative: bool = False) -> bool:
        """MOUSE_MOVE|{x}|{y}|{0|1}"""
        return self._send_command(
            f"{_CMD_MOUSE_MOVE}|{x}|{y}|{int(relative)}"
        )

    def send_mouse_click(self, button: str) -> bool:
        """MOUSE_CLICK|{button}"""
        return self._send_command(f"{_CMD_MOUSE_CLICK}|{button}")

    def send_mouse_scroll(self, amount: int) -> bool:
        """MOUSE_SCROLL|{amount}"""
        return self._send_command(f"{_CMD_MOUSE_SCROLL}|{amount}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send_command(self, cmd: str) -> bool:
        """Send command and wait for ACK. Returns True on success."""
        resp = self._send_command_raw(cmd)
        if resp == _RESP_ACK:
            self._cmd_count += 1
            return True
        return False

    def _send_command_raw(self, cmd: str) -> Optional[str]:
        """Send command and return raw response string.

        For blocking commands like KEY_PRESS that hold the key for
        *duration_ms* before replying, temporarily raises the serial
        read timeout so ``readline()`` doesn't time out while the Pico
        is holding the key down.
        """
        if not self._available or self._serial is None:
            return None

        with self._lock:
            try:
                # Estimate response delay: KEY_PRESS|key|<dur_ms> blocks dur_ms
                extra_s = 0.0
                if cmd.startswith(_CMD_KEY_PRESS):
                    parts = cmd.split("|")
                    if len(parts) >= 3:
                        extra_s = int(parts[2]) / 1000.0

                old_timeout = getattr(self._serial, "timeout", None)
                if extra_s > 0 and isinstance(old_timeout, (int, float)):
                    needed = extra_s + 0.2
                    if needed > old_timeout:
                        self._serial.timeout = needed
                    else:
                        old_timeout = None  # no restore needed
                else:
                    old_timeout = None  # no restore needed

                self._serial.reset_input_buffer()
                self._serial.write(f"{cmd}\n".encode())
                resp = self._serial.readline().decode("utf-8", "ignore").strip()

                if old_timeout is not None:
                    self._serial.timeout = old_timeout

                return resp if resp else None
            except Exception as exc:
                _log.warning("Pico communication error: %s", exc)
                self._available = False
                return None
