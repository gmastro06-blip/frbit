"""
deprecated_ocr.py
-----------------
Clases deprecadas de OCR de coordenadas.

.. deprecated::
    El cliente Tibia (OTC vía Proyector) no muestra coordenadas como texto.
    Usar ``MinimapRadar`` (palette matching) + ``PositionResolver`` en su lugar.
    Estas clases se mantienen solo por compatibilidad; serán eliminadas.
"""

from __future__ import annotations

import random
import re
import threading
import time
from typing import Any, Callable, List, Optional

import cv2
import numpy as np

from .detector_config import DetectorConfig
from .frame_sources import (
    MSSScreenSource,
    OBSWebSocketSource,
    VirtualCameraSource,
    WGCSource,
)
from .image_processing import ImageProcessor
from .models import Coordinate

# Regex que acepta las variantes de formato de coordenadas Tibia
_COORD_RE = re.compile(
    r"(?:X[:\s]*)?(\d{4,6})"   # X  (4-6 dígitos)
    r"[,\s/|Y:\s]+"
    r"(?:Y[:\s]*)?(\d{4,6})"   # Y
    r"[,\s/|Z:\s]+"
    r"(?:Z[:\s]*)?(\d{1,2})"   # Z  (0-15)
)


# ---------------------------------------------------------------------------
class CoordinateOCR:
    """
    .. deprecated::
        El cliente Tibia (OTC vía Proyector) no muestra coordenadas como texto.
        Usar MinimapRadar (template matching) + TibiaLocalMinimapReader en su
        lugar.  Esta clase se mantiene solo por compatibilidad; será eliminada.

    Extrae coordenadas Tibia (X, Y, Z) de un numpy array de imagen usando
    EasyOCR en modo CPU con allowlist restringida a dígitos y separadores.
    """

    def __init__(self, confidence: float = 0.4) -> None:
        self._confidence = confidence
        self._reader: Any = None

    def _init_reader(self) -> None:
        if self._reader is None:
            print("  [OCR] Iniciando EasyOCR (primera vez descarga ~50 MB de modelos) …")
            import easyocr
            self._reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            print("  [OCR] Listo.")

    def read(self, img: np.ndarray) -> Optional[Coordinate]:
        """
        Dado un array BGR/gray del ROI, devuelve la Coordinate o None.
        """
        self._init_reader()

        results: list[Any] = self._reader.readtext(
            img,
            allowlist="0123456789, XYZxyz:/()",
            detail=1,
        )

        # Concatenar texto de todos los bloques con confianza suficiente
        text = " ".join(
            r[1] for r in results
            if float(r[2]) >= self._confidence
        )
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> Optional[Coordinate]:
        """Aplica regex para extraer X, Y, Z del texto crudo."""
        text = text.strip()
        if not text:
            return None
        m = _COORD_RE.search(text)
        if not m:
            return None
        x, y, z = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Validación básica de rango Tibia
        if not (31744 <= x <= 34048 and 30976 <= y <= 32768 and 0 <= z <= 15):
            return None
        return Coordinate(x, y, z)


# ---------------------------------------------------------------------------
class CharacterDetector:
    """
    .. deprecated::
        Usa PositionResolver con MinimapRadar + TibiaLocalMinimapReader.
        El OCR de coordenadas no funciona porque el cliente no muestra el texto.
        Esta clase se mantiene solo por compatibilidad; será eliminada.

    Detecta continuamente la posición del personaje Tibia.

    Uso::

        detector = CharacterDetector(source="obs-ws")  # o "virtual-cam" / "screen"
        detector.on_position(lambda coord: print(coord))
        detector.start()
        ...
        detector.stop()

    También se puede usar en modo síncrono una sola vez::

        coord = detector.detect_once()
    """

    def __init__(
        self,
        source: str = "virtual-cam",
        config: Optional[DetectorConfig] = None,
        debug: bool = False,
        window_title: str = "",
    ) -> None:
        self._cfg = config or DetectorConfig.load()
        self._debug = debug
        self._processor = ImageProcessor()
        self._ocr = CoordinateOCR(self._cfg.ocr_confidence)
        self._callbacks: List[Callable[[Coordinate], None]] = []
        self._last: Optional[Coordinate] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Seleccionar fuente
        if source == "obs-ws":
            self._source: Any = OBSWebSocketSource(self._cfg)
        elif source == "virtual-cam":
            self._source = VirtualCameraSource(self._cfg.obs_cam_index)
        elif source == "wgc":
            self._source = WGCSource(window_title or "Tibia")
        elif source == "mss":
            self._source = WGCSource(window_title or "Tibia")
        elif source == "screen":
            self._source = MSSScreenSource()
        else:
            raise ValueError(
                f"source debe ser 'obs-ws', 'virtual-cam', 'wgc', 'mss' o 'screen'. Got: {source!r}"
            )

        self._source_name = source

    # -----------------------------------------------------------------------
    # Callbacks
    # -----------------------------------------------------------------------
    def on_position(self, callback: Callable[[Coordinate], None]) -> None:
        """Registra un callback que se llama cada vez que la posición cambia."""
        self._callbacks.append(callback)

    # -----------------------------------------------------------------------
    # Detección puntual
    # -----------------------------------------------------------------------
    def detect_once(self) -> Optional[Coordinate]:
        """Captura un frame y retorna la coordenada detectada o None."""
        frame = self._source.get_frame()
        if frame is None:
            return None
        return self._process_frame(frame)

    # -----------------------------------------------------------------------
    # Tracking continuo
    # -----------------------------------------------------------------------
    def start(self) -> None:
        """Inicia el hilo de tracking en background."""
        self._source.connect()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"  [T] Tracking iniciado (fuente: {self._source_name}, "
              f"intervalo: {self._cfg.sample_interval}s)")

    def stop(self) -> None:
        """Detiene el tracking."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        self._source.disconnect()
        print("  [T] Tracking detenido.")

    @property
    def last_position(self) -> Optional[Coordinate]:
        return self._last

    @property
    def is_running(self) -> bool:
        """True while the background tracking thread is active."""
        return self._running

    def remove_callback(
        self, callback: Callable[[Coordinate], None]
    ) -> bool:
        """Remove a previously registered callback.

        Returns True if found and removed, False otherwise.
        """
        try:
            self._callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def clear_callbacks(self) -> None:
        """Remove all registered position callbacks."""
        self._callbacks.clear()

    def update_config(self, config: DetectorConfig) -> None:
        """Hot-swap the detector configuration without restarting the thread.

        Updates OCR confidence from the new config.
        """
        self._cfg = config
        self._ocr._confidence = config.ocr_confidence

    @property
    def callback_count(self) -> int:
        """Number of position callbacks currently registered."""
        return len(self._callbacks)

    @property
    def source_name(self) -> str:
        """The source type used by this detector instance (e.g. 'virtual-cam')."""
        return self._source_name

    def stats_snapshot(self) -> dict[str, Any]:
        """Keys: is_running, last_position_set, callback_count, source_name."""
        return {
            "is_running":       self._running,
            "last_position_set": self._last is not None,
            "callback_count":   self.callback_count,
            "source_name":      self._source_name,
        }

    @property
    def has_callbacks(self) -> bool:
        """True when at least one position callback is registered."""
        return self.callback_count > 0

    @property
    def has_last_position(self) -> bool:
        """True when at least one position has been detected."""
        return self._last is not None

    # -----------------------------------------------------------------------
    # Interno
    # -----------------------------------------------------------------------
    def _loop(self) -> None:
        while self._running:
            try:
                coord = self.detect_once()
                if coord and coord != self._last:
                    self._last = coord
                    for cb in self._callbacks:
                        cb(coord)
            except Exception as exc:
                print(f"  [T] Error en loop: {exc}")
            time.sleep(self._cfg.sample_interval * random.uniform(0.8, 1.25))

    def _process_frame(self, frame: np.ndarray) -> Optional[Coordinate]:
        """Recorta ROI, preprocesa y aplica OCR."""
        x, y, w, h = self._cfg.roi
        roi = frame[y: y + h, x: x + w]

        if roi.size == 0:
            print("  [T] ROI vacío — calibra la región con calibrator.py")
            return None

        processed = self._processor.preprocess(roi)

        if self._debug:
            self._processor.debug_save(processed)

        return self._ocr.read(processed)
