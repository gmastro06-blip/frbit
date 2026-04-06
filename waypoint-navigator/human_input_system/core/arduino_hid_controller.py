"""ArduinoHIDController — comunicación serial con Arduino HID + fallback."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from ..config.models import ArduinoConfig

_log = logging.getLogger(__name__)

# Protocolo serial
_CMD_KEY_PRESS = "KEY_PRESS"
_CMD_KEY_RELEASE = "KEY_RELEASE"
_CMD_COMBO = "COMBO"
_CMD_MOUSE_MOVE = "MOUSE_MOVE"
_CMD_MOUSE_CLICK = "MOUSE_CLICK"
_CMD_PING = "PING"
_CMD_STATUS = "STATUS"
_RESP_ACK = "ACK"
_RESP_PONG = "PONG"


class ArduinoHIDController:
    """Controla un Arduino como dispositivo HID real.

    Si el Arduino no está disponible, todo se delega silenciosamente
    al *fallback_controller* (``InputController``).
    """

    def __init__(self, config: ArduinoConfig, fallback_controller: Any) -> None:
        self._cfg = config
        self._fallback = fallback_controller
        self._serial: Any = None  # serial.Serial — importado sólo si disponible
        self._available = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Detecta y conecta con el Arduino. Retorna True si está disponible."""
        if not self._cfg.enabled:
            _log.info("[Arduino] Deshabilitado en configuración — usando fallback")
            return False

        try:
            import serial
            import serial.tools.list_ports
        except ImportError:
            _log.warning("[Arduino] pyserial no instalado — usando fallback")
            return False

        port = self._cfg.port
        ports_to_try = []

        if port and port != "auto":
            ports_to_try.append(port)
        else:
            for p in serial.tools.list_ports.comports():
                ports_to_try.append(p.device)

        for port_name in ports_to_try:
            try:
                conn = serial.Serial(
                    port=port_name,
                    baudrate=self._cfg.baudrate,
                    timeout=self._cfg.timeout,
                )
                time.sleep(0.1)  # Arduino reset delay
                conn.write(f"{_CMD_PING}\n".encode())
                resp = conn.readline().decode().strip()
                if resp == _RESP_PONG:
                    self._serial = conn
                    self._available = True
                    _log.info(f"[Arduino] Conectado en {port_name}")
                    return True
                conn.close()
            except Exception as exc:
                _log.debug(f"[Arduino] No disponible en {port_name}: {exc}")

        _log.info("[Arduino] No se encontró dispositivo — usando fallback")
        return False

    def close(self) -> None:
        """Cierra la conexión serial."""
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                self._available = False
                _log.info("[Arduino] Conexión cerrada")

    def is_available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def send_key_press(self, key: str, duration: float) -> bool:
        """``KEY_PRESS|{key}|{duration_ms}``."""
        return self._send_command(f"{_CMD_KEY_PRESS}|{key}|{int(duration)}")

    def send_key_release(self, key: str) -> bool:
        """``KEY_RELEASE|{key}``."""
        return self._send_command(f"{_CMD_KEY_RELEASE}|{key}")

    def send_combo(self, modifier: str, key: str, duration_ms: int = 80) -> bool:
        """``COMBO|{modifier}|{key}|{duration_ms}`` — holds modifier while pressing key."""
        return self._send_command(f"{_CMD_COMBO}|{modifier}|{key}|{duration_ms}")

    def send_status(self) -> Optional[tuple[int, int]]:
        """Send STATUS command. Returns (uptime_ms, cmd_count) or None on failure."""
        if not self._available or self._serial is None:
            return None
        with self._lock:
            try:
                self._serial.write(f"{_CMD_STATUS}\n".encode())
                resp = self._serial.readline().decode().strip()
                if resp.startswith("OK|"):
                    parts = resp.split("|")
                    if len(parts) >= 3:
                        return (int(parts[1]), int(parts[2]))
            except Exception as exc:
                _log.warning(f"[Arduino] STATUS error: {exc}")
                self._available = False
        return None

    def send_mouse_move(self, x: int, y: int, relative: bool = False) -> bool:
        """``MOUSE_MOVE|{x}|{y}|{0|1}``."""
        return self._send_command(
            f"{_CMD_MOUSE_MOVE}|{x}|{y}|{int(relative)}"
        )

    def send_mouse_click(self, button: str) -> bool:
        """``MOUSE_CLICK|{button}``."""
        return self._send_command(f"{_CMD_MOUSE_CLICK}|{button}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send_command(self, cmd: str) -> bool:
        """Envía comando y espera ACK. Fallback si falla."""
        if not self._available or self._serial is None:
            return False

        with self._lock:
            try:
                self._serial.write(f"{cmd}\n".encode())
                resp = self._serial.readline().decode().strip()
                if resp == _RESP_ACK:
                    return True
                _log.warning(f"[Arduino] Respuesta inesperada: {resp!r}")
            except Exception as exc:
                _log.warning(f"[Arduino] Error de comunicación: {exc}")
                self._available = False

        return False
