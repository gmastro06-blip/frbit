"""Tests for HP/MP detection against real Tibia screenshots.

These tests validate the hpmp_detector against actual captured frames
from the user's Tibia client (charactername Hiyoko San, 1920×1009).
They are skipped automatically if the screenshots are not present.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.hpmp_detector import HpMpDetector, HpMpConfig


_IMAGE_DIR = Path(__file__).resolve().parent.parent.parent / "image"

# Screenshots and their expected approximate HP/MP values
# Extracted from pixel analysis of the actual screenshots
_SCREENSHOTS = [
    # (filename, expected_hp_range, expected_mp_range)
    ("2026-03-01_123922585_Hiyoko San_Hotkey.png", (95, 100), (95, 100)),
    ("2026-03-01_123937108_Hiyoko San_Hotkey.png", (95, 100), (95, 100)),
    ("2026-03-01_124041684_Hiyoko San_Hotkey.png", (65, 80), (30, 45)),
    ("2026-03-01_124129417_Hiyoko San_Hotkey.png", (33, 45), (44, 56)),
    ("2026-03-01_124142220_Hiyoko San_Hotkey.png", (20, 32), (47, 58)),
]

_has_screenshots = _IMAGE_DIR.exists() and any(
    (_IMAGE_DIR / s[0]).exists() for s in _SCREENSHOTS
)


@pytest.fixture()
def detector() -> HpMpDetector:
    """Create a detector with ROIs matching the Hiyoko San screenshots (1920×1009)."""
    cfg = HpMpConfig.load()
    # Override with the ROIs calibrated for these specific screenshots
    cfg.hp_roi = [10, 7, 770, 12]
    cfg.mp_roi = [787, 7, 755, 12]
    return HpMpDetector(cfg)


@pytest.mark.skipif(not _has_screenshots, reason="Real screenshots not available")
class TestRealScreenshotCalibration:
    """Validate HP/MP detection against real Tibia screenshots."""

    @pytest.mark.parametrize(
        "filename, hp_range, mp_range",
        [s for s in _SCREENSHOTS if (_IMAGE_DIR / s[0]).exists()],
        ids=[s[0][:20] for s in _SCREENSHOTS if (_IMAGE_DIR / s[0]).exists()],
    )
    def test_hp_detection(
        self,
        detector: HpMpDetector,
        filename: str,
        hp_range: tuple[int, int],
        mp_range: tuple[int, int],
    ):
        frame = cv2.imread(str(_IMAGE_DIR / filename))
        assert frame is not None, f"Failed to load {filename}"

        hp = detector._read_bar(frame, detector._cfg.hp_roi, "hp")
        assert hp is not None
        assert hp_range[0] <= hp <= hp_range[1], (
            f"HP={hp}% not in expected range {hp_range} for {filename}"
        )

    @pytest.mark.parametrize(
        "filename, hp_range, mp_range",
        [s for s in _SCREENSHOTS if (_IMAGE_DIR / s[0]).exists()],
        ids=[s[0][:20] for s in _SCREENSHOTS if (_IMAGE_DIR / s[0]).exists()],
    )
    def test_mp_detection(
        self,
        detector: HpMpDetector,
        filename: str,
        hp_range: tuple[int, int],
        mp_range: tuple[int, int],
    ):
        frame = cv2.imread(str(_IMAGE_DIR / filename))
        assert frame is not None

        mp = detector._read_bar(frame, detector._cfg.mp_roi, "mp")
        assert mp is not None
        assert mp_range[0] <= mp <= mp_range[1], (
            f"MP={mp}% not in expected range {mp_range} for {filename}"
        )

    def test_hp_gradient_ordering(self, detector: HpMpDetector):
        """HP values should decrease across screenshots 1→5."""
        values = []
        for filename, _, _ in _SCREENSHOTS:
            path = _IMAGE_DIR / filename
            if not path.exists():
                pytest.skip(f"Missing {filename}")
            frame = cv2.imread(str(path))
            assert frame is not None, f"Failed to read {path}"
            hp = detector._read_bar(frame, detector._cfg.hp_roi, "hp")
            assert hp is not None, f"_read_bar returned None for {path}"
            values.append(hp)

        # Screenshots are ordered: full HP → damaged → critical
        # SS1 ≈ SS2 (both full), SS3 < SS1, SS4 < SS3, SS5 < SS4
        assert values[0] >= values[2], f"SS1 HP={values[0]} should >= SS3 HP={values[2]}"
        assert values[2] >= values[3], f"SS3 HP={values[2]} should >= SS4 HP={values[3]}"
        assert values[3] >= values[4], f"SS4 HP={values[3]} should >= SS5 HP={values[4]}"

    def test_full_hp_is_100(self, detector: HpMpDetector):
        """Full HP should read as 100% (or very close)."""
        path = _IMAGE_DIR / _SCREENSHOTS[0][0]
        if not path.exists():
            pytest.skip("Missing first screenshot")
        frame = cv2.imread(str(path))
        assert frame is not None
        hp = detector._read_bar(frame, detector._cfg.hp_roi, "hp")
        assert hp is not None
        assert hp >= 95, f"Full HP should be >=95%, got {hp}%"

    def test_critical_hp_detected(self, detector: HpMpDetector):
        """Critical HP (red bar) should still be detected (>0)."""
        path = _IMAGE_DIR / _SCREENSHOTS[-1][0]
        if not path.exists():
            pytest.skip("Missing last screenshot")
        frame = cv2.imread(str(path))
        assert frame is not None
        hp = detector._read_bar(frame, detector._cfg.hp_roi, "hp")
        assert hp is not None
        assert hp > 0, "Red HP bar should not read as 0%"
        assert hp < 35, f"Critical HP should be <35%, got {hp}%"


@pytest.mark.skipif(not _has_screenshots, reason="Real screenshots not available")
class TestHpBarColorThresholds:
    """Unit tests validating that the color detection thresholds
    correctly classify each HP bar color state."""

    @pytest.mark.parametrize(
        "bgr, expected_detected, description",
        [
            ((0, 190, 0), True, "Green HP (100%)"),
            ((4, 158, 109), True, "Yellow HP (70%)"),
            ((9, 152, 200), True, "Orange HP (40%)"),
            ((47, 47, 190), True, "Red HP (25%)"),
            ((42, 42, 42), False, "Empty bar (dark gray)"),
            ((72, 72, 72), False, "Panel background (medium gray)"),
            ((220, 0, 0), False, "Pure blue (should NOT be HP)"),
            ((126, 57, 0), False, "MP bar blue (should NOT be HP)"),
        ],
    )
    def test_hp_color_classification(
        self,
        bgr: tuple[int, int, int],
        expected_detected: bool,
        description: str,
    ):
        """Build a synthetic bar with one color and check detection.

        The detector scales ROI relative to 1920×1080, so we must build a
        full-reference-size frame with the color painted in the HP ROI area.
        """
        # Use the real HP ROI from config
        cfg = HpMpConfig.load()
        detector = HpMpDetector(cfg)
        x, y, w, h = cfg.hp_roi  # [12, 5, 769, 12]

        # Build a 1920×1080 frame filled with dark gray (empty bar bg)
        frame = np.full((1080, 1920, 3), (42, 42, 42), dtype=np.uint8)
        # Paint the HP ROI area with the test color
        frame[y : y + h, x : x + w] = bgr

        hp = detector._read_bar(frame, cfg.hp_roi, "hp")

        if expected_detected:
            assert hp is not None and hp > 50, (
                f"{description}: BGR{bgr} should be detected as HP, got {hp}%"
            )
        else:
            assert hp is not None and hp <= 5, (
                f"{description}: BGR{bgr} should NOT be HP, got {hp}%"
            )
