"""
conftest.py — pytest fixtures compartidos para toda la suite.

Genera frames BGR sintéticos sin necesidad de OBS ni Tibia.
Todos los frames son 19201080 (resolución de referencia de los módulos).
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

# Garantizar que el paquete raíz es importable desde tests/
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Constantes de referencia
# ─────────────────────────────────────────────────────────────────────────────
REF_W, REF_H = 1920, 1080

# ROIs por defecto de HpMpConfig (x, y, w, h)
HP_ROI = [1713, 445, 94, 9]
MP_ROI = [1713, 457, 94, 9]

# ROI por defecto de ConditionConfig
COND_ROI = [1709, 462, 200, 30]


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades internas
# ─────────────────────────────────────────────────────────────────────────────

def _blank_frame() -> np.ndarray:
    """Frame 1920×1080 completamente negro (dtype uint8, BGR)."""
    return np.zeros((REF_H, REF_W, 3), dtype=np.uint8)


def _fill_roi_bgr(frame: np.ndarray, roi: list[int], bgr: tuple[int, int, int],
                  fill_frac: float = 1.0) -> None:
    """
    Rellena *fill_frac* (0.0-1.0) del ancho del ROI con el color BGR dado.
    Rellena de izquierda a derecha, igual que las barras de HP/MP.
    """
    x, y, w, h = roi
    filled_w = max(1, int(round(w * fill_frac)))
    frame[y : y + h, x : x + filled_w] = bgr


def _hsv2bgr(h: int, s: int, v: int) -> tuple[int, int, int]:
    """Convierte un pixel HSV (OpenCV, rango 0-179/255/255) a BGR."""
    pixel = np.array([[[h, s, v]]], dtype=np.uint8)
    bgr = cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)
    b, g, r = int(bgr[0, 0, 0]), int(bgr[0, 0, 1]), int(bgr[0, 0, 2])
    return (b, g, r)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures de frames
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def blank_frame() -> np.ndarray:
    """Frame 1920×1080 completamente negro."""
    return _blank_frame()


@pytest.fixture()
def hp75_mp50_frame() -> np.ndarray:
    """
    Frame con HP al 75% (barra verde) y MP al 50% (barra azul).

    Colores elegidos para pasar los filtros de HpMpDetector:
      HP verde → R=0, G=200, B=0   (BGR: 0, 200, 0)
      MP azul  → R=0, G=0,   B=220 (BGR: 220, 0, 0)
    """
    frame = _blank_frame()
    _fill_roi_bgr(frame, HP_ROI, bgr=(0, 200, 0), fill_frac=0.75)
    _fill_roi_bgr(frame, MP_ROI, bgr=(220, 0, 0), fill_frac=0.50)
    return frame


@pytest.fixture()
def hp100_mp100_frame() -> np.ndarray:
    """Frame con HP/MP al 100%."""
    frame = _blank_frame()
    _fill_roi_bgr(frame, HP_ROI, bgr=(0, 200, 0), fill_frac=1.0)
    _fill_roi_bgr(frame, MP_ROI, bgr=(220, 0, 0), fill_frac=1.0)
    return frame


@pytest.fixture()
def hp0_mp0_frame() -> np.ndarray:
    """Frame sin ningún pixel de color en las barras → HP=0, MP=0."""
    return _blank_frame()   # negro puro, sin colores de barra


@pytest.fixture()
def poison_frame() -> np.ndarray:
    """Frame con el icono de veneno (verde) en el área de condiciones."""
    frame = _blank_frame()
    # Veneno: H=60 (0-179), S=200, V=180  → verde brillante
    bgr = _hsv2bgr(60, 200, 180)
    x, y, w, h = COND_ROI
    # Pinta varios píxeles (≥ _MIN_PIXELS) con el color de veneno
    num = 20
    frame[y : y + h, x : x + num] = bgr
    return frame


@pytest.fixture()
def paralyze_frame() -> np.ndarray:
    """Frame con el icono de parálisis (azul-morado) en condiciones."""
    frame = _blank_frame()
    # Parálisis: H=120, S=180, V=180 → violeta-azul
    bgr = _hsv2bgr(120, 180, 180)
    x, y, w, h = COND_ROI
    frame[y : y + h, x : x + 20] = bgr
    return frame


@pytest.fixture()
def no_condition_frame() -> np.ndarray:
    """Frame sin ninguna condición activa (solo negro)."""
    return _blank_frame()


# ─────────────────────────────────────────────────────────────────────────────
# Auto-mock preflight for ALL tests — driver not available in CI / test env
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent BotSession.start() from aborting due to preflight failures
    in test environments where the Interception driver and calibration
    configs are not present."""
    from unittest.mock import MagicMock
    from src.preflight import PreflightReport, CheckResult, Severity

    _fake_report = PreflightReport(results=[
        CheckResult("test_bypass", Severity.PASS, "mocked for tests"),
    ])
    monkeypatch.setattr(
        "src.session.run_preflight",
        MagicMock(return_value=_fake_report),
    )
