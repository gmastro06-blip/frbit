"""
character_detector.py — Shim de compatibilidad hacia atrás
----------------------------------------------------------
El contenido original fue separado en módulos independientes:

  - ``detector_config.py``  → DetectorConfig, CONFIG_FILE
  - ``image_processing.py`` → ImageProcessor
  - ``frame_sources.py``    → OBSWebSocketSource, VirtualCameraSource,
                               WGCSource, MSSScreenSource
  - ``deprecated_ocr.py``   → CoordinateOCR, CharacterDetector, _COORD_RE

Este archivo re-exporta todos los símbolos públicos para que los imports
existentes (``from src.character_detector import X``) sigan funcionando.
"""

from .detector_config import DetectorConfig, CONFIG_FILE  # noqa: F401
from .image_processing import ImageProcessor  # noqa: F401
from .frame_sources import (  # noqa: F401
    OBSWebSocketSource,
    VirtualCameraSource,
    WGCSource,
    MSSScreenSource,
)
from .deprecated_ocr import CoordinateOCR, CharacterDetector, _COORD_RE  # noqa: F401
