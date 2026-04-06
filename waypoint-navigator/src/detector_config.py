"""
detector_config.py
------------------
Configuración persistente del detector de coordenadas.
Separado de character_detector.py para reducir acoplamiento.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List

from .config_paths import DETECTOR_CONFIG

CONFIG_FILE = DETECTOR_CONFIG


@dataclass
class DetectorConfig:
    """Configuración persistente del detector."""

    # ROI en píxeles del fotograma completo: [x, y, w, h]
    roi: List[int] = field(default_factory=lambda: [0, 0, 200, 40])
    # Nombre de la fuente en OBS (vacío = usa el frame completo del Virtual Cam)
    obs_source: str = ""
    # Índice de la Virtual Camera de OBS en cv2.VideoCapture
    obs_cam_index: int = 0
    # Puerto y contraseña del WebSocket de OBS
    obs_ws_host: str = "localhost"
    obs_ws_port: int = 4455
    obs_ws_password: str = ""
    # Umbral de confianza mínimo para aceptar un resultado OCR
    ocr_confidence: float = 0.4
    # Intervalo de muestreo en segundos
    sample_interval: float = 0.5

    def __post_init__(self) -> None:
        import os as _os
        if not self.obs_ws_password:
            self.obs_ws_password = _os.environ.get("OBS_WS_PASSWORD", "")
        self._validate_roi(self.roi, "DetectorConfig.roi")

    @staticmethod
    def _validate_roi(roi: list[int], label: str) -> None:
        if len(roi) != 4:
            raise ValueError(f"{label} must have 4 elements, got {len(roi)}")
        if any(v < 0 for v in roi):
            raise ValueError(f"{label} values must be non-negative: {roi}")

    def save(self, path: Path = CONFIG_FILE) -> None:
        with open(path, "w") as f:
            json.dump(self.__dict__, f, indent=2)

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "DetectorConfig":
        if not path.exists():
            return cls()
        with open(path) as f:
            data = json.load(f)
        # Filter out unknown keys to avoid TypeError on extra fields
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
