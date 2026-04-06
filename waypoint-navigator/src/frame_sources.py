"""
frame_sources.py
----------------
Fuentes de captura de frames para el detector de posición.

Clases incluidas:
  - OBSWebSocketSource  : OBS WebSocket v5 (requiere OBS 28+)
  - VirtualCameraSource : OBS Virtual Camera via cv2
  - WGCSource           : Windows Graphics Capture API (D3D11, sin OBS)
  - MSSScreenSource     : Captura directa de pantalla via mss/DXGI
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

import cv2
import numpy as np

from .detector_config import DetectorConfig

_log = logging.getLogger("wn.fs")


# ---------------------------------------------------------------------------
class OBSWebSocketSource:
    """
    Obtiene fotogramas desde OBS a través de WebSocket v5.
    Requiere OBS 28+ con el plugin WebSocket activado.
    """

    def __init__(self, config: DetectorConfig, capture_width: int = 0) -> None:
        self._cfg = config
        self._client: Any = None
        self._capture_width = capture_width  # 0 = tamaño real del canvas

    def connect(self) -> None:
        try:
            import obsws_python as obs
            self._client = obs.ReqClient(
                host=self._cfg.obs_ws_host,
                port=self._cfg.obs_ws_port,
                password=self._cfg.obs_ws_password,
                timeout=5,
            )
            _log.info("[OBS-WS] Conectado a %s:%s", self._cfg.obs_ws_host, self._cfg.obs_ws_port)
        except Exception as exc:
            raise ConnectionError(f"No se pudo conectar a OBS WebSocket: {exc}") from exc

    def get_frame(self) -> Optional[np.ndarray]:
        """Devuelve el frame de la fuente configurada como array BGR."""
        if self._client is None:
            self.connect()
        try:
            import base64

            # Si no hay fuente configurada, usar la escena activa como fuente
            source_name = self._cfg.obs_source
            if not source_name:
                source_name = self._client.get_current_program_scene().current_program_scene_name

            w, h = self._get_source_size()

            resp = self._client.get_source_screenshot(
                name=source_name,
                img_format="png",
                width=w,
                height=h,
                quality=-1,
            )
            b64: str = resp.image_data.split(",", 1)[-1]
            data = base64.b64decode(b64)
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return img
        except Exception as exc:
            _log.debug("[OBS-WS] Error obteniendo frame: %s", exc)
            return None

    def _get_source_size(self) -> tuple[int, int]:
        """
        Devuelve (width, height) para la petición de screenshot.
        Si capture_width > 0, escala proporcional a ese ancho máximo.
        Fallback: 1920×1080.
        """
        try:
            vs = self._client.get_video_settings()
            full_w, full_h = int(vs.base_width), int(vs.base_height)
        except Exception:
            full_w, full_h = 1920, 1080
        if self._capture_width > 0 and full_w > self._capture_width:
            scale  = self._capture_width / full_w
            return self._capture_width, max(8, int(full_h * scale))
        return full_w, full_h

    def disconnect(self) -> None:
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None


# ---------------------------------------------------------------------------
class VirtualCameraSource:
    """
    Lee frames desde la Virtual Camera de OBS (aparece como webcam en cv2).
    Más simple que WebSocket: no requiere contraseña ni configuración extra.
    """

    def __init__(self, cam_index: int = 0) -> None:
        self._index = cam_index
        self._cap: Optional[cv2.VideoCapture] = None

    def connect(self) -> None:
        # Use DSHOW backend on Windows for better resolution negotiation
        self._cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            # Fallback: no backend hint
            self._cap = cv2.VideoCapture(self._index)
        if not self._cap.isOpened():
            raise ConnectionError(
                f"No se pudo abrir la cámara índice {self._index}. "
                "Asegúrate de que OBS Virtual Camera esté activa."
            )
        # Request 1920×1080 — OBS Virtual Camera supports it
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        _log.info("[VirtualCam] Cámara %s abierta (%sx%s)", self._index,
                  int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                  int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def get_frame(self) -> Optional[np.ndarray]:
        if self._cap is None:
            self.connect()
        ret, frame = self._cap.read()  # type: ignore[union-attr]
        return frame if ret else None

    def disconnect(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    def __del__(self) -> None:
        try:
            self.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------------------
class WGCSource:
    """
    Captura frames directamente de la ventana Tibia usando Windows Graphics
    Capture API (WGC) — sin OBS, sin memoria, sin OCR de coordenadas.

    Ventajas
    --------
    * No requiere OBS instalado ni activo.
    * Captura incluso cuando Tibia está detrás de otras ventanas.
    * Captura directa D3D11 → sin latencia de Virtual Camera.
    * Compatible con BattlEye (solo usa WinRT público de Windows).

    Requisitos
    ----------
    * Windows 10 build 1903 (19H1) o superior.
    * ``pip install winsdk``
    * El proceso de Python debe poder acceder a la ventana Tibia (usuario normal).

    Uso
    ---
    ::

        src = WGCSource()
        src.connect()
        frame = src.get_frame()   # BGR numpy array
        src.disconnect()

    Internally delegates to :class:`~src.frame_capture.WGCCapture` to avoid
    duplicating D3D11/COM vtable code.
    """

    # Número de frames de calentamiento antes de aceptar el primero real
    _WARMUP = 4
    # Intentos máximos por llamada a get_frame()
    _MAX_TRIES = 30
    # Espera entre intentos en segundos
    _INTERVAL = 0.033

    def __init__(self, window_title: str = "Tibia") -> None:
        self._title    = window_title
        self._hwnd: int = 0
        self._width:  int = 0
        self._height: int = 0
        self._connected = False
        self._capture: Any = None   # WGCCapture instance
        self._grab: Any = None      # closure returned by WGCCapture.open()

    # ------------------------------------------------------------------ API
    def connect(self) -> None:
        """Inicializa el pipeline WGC completo."""
        import ctypes

        # ── 1. Encontrar hwnd de Tibia ──────────────────────────────────────
        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)  # type: ignore[untyped-decorator]
        def _cb(h: int, _: int) -> bool:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, buf, 256)
            if self._title.lower() in buf.value.lower() and user32.IsWindowVisible(h):
                found.append(h)
            return True

        user32.EnumWindows(_cb, 0)
        if not found:
            raise ConnectionError(
                f"WGCSource: ventana '{self._title}' no encontrada. "
                "¿Está Tibia abierto?"
            )
        self._hwnd = found[0]

        # ── 2. Delegate to WGCCapture ───────────────────────────────────────
        from .frame_capture import WGCCapture

        cap = WGCCapture(self._hwnd)
        self._grab = cap.open()
        self._capture = cap

        # Read window size from the capture item for logging
        from winsdk.windows.graphics.capture.interop import create_for_window
        item = create_for_window(self._hwnd)
        sz = item.size
        self._width = sz.width
        self._height = sz.height

        self._connected = True

        # Calentamiento: espera hasta recibir un frame con brillo > 0.
        for _ in range(60):   # 60 × 50 ms = 3 s máximo
            time.sleep(random.uniform(0.03, 0.07))
            bgr = self._grab()
            if bgr is not None and float(bgr.mean()) > 1.0:
                break   # frame real → WGC listo

        _log.info("[W] Capturando '%s' hwnd=%#x %sx%s", self._title, self._hwnd,
                  self._width, self._height)

    def get_frame(self) -> Optional[np.ndarray]:
        """
        Devuelve el siguiente frame disponible como BGR numpy array.
        Bloquea hasta ``_MAX_TRIES × _INTERVAL`` segundos como máximo.
        """
        if not self._connected:
            self.connect()

        for _ in range(self._MAX_TRIES):
            frame = self._grab()
            if frame is not None:
                return frame
            time.sleep(self._INTERVAL * random.uniform(0.8, 1.2))

        return None  # timeout

    def disconnect(self) -> None:
        """Libera todos los recursos WGC."""
        if self._capture is not None:
            self._capture.close()
            self._capture = None
        self._grab = None
        self._connected = False


# ---------------------------------------------------------------------------
class MSSScreenSource:
    """
    Captura directa de pantalla con mss (sin OBS, como fallback).

    Parameters
    ----------
    monitor : int
        Índice del monitor a capturar.
        0 = todos los monitores combinados, 1 = monitor principal,
        2 = segundo monitor, etc.
    """

    def __init__(self, monitor: int = 1) -> None:
        self._monitor = monitor
        self._sct: Any = None
        self._region: Any = None

    def connect(self) -> None:
        import mss as _mss
        self._sct = _mss.mss()
        available = len(self._sct.monitors) - 1  # índice 0 = "todos"
        if self._monitor > available:
            self._sct.close()
            self._sct = None
            raise ConnectionError(
                f"Monitor {self._monitor} no disponible. "
                f"Monitores detectados: 1..{available}  (0 = todos)"
            )
        self._region = self._sct.monitors[self._monitor]
        _log.info("[ScreenSource] Monitor %s seleccionado.", self._monitor)

    def get_frame(self) -> Optional[np.ndarray]:
        if self._sct is None:
            self.connect()
        shot = self._sct.grab(self._region)
        # shot.rgb es bytes RGB sin canal alpha — reshape directo, sin cvtColor
        img = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)
        return img[:, :, ::-1].copy()  # RGB → BGR

    def disconnect(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None
            self._region = None
