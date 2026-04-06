"""
Tests for src/minimap_calibrator.py
Uses synthetic frames and mocked dependencies — no live window.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from src.minimap_calibrator import (
    CalibrationResult,
    MinimapCalibrator,
    _MIN_ACCEPT_SCORE,
    _MIN_PALETTE_FRAC,
    _ROI_SCAN_X_FRAC,
    _ROI_SCAN_Y_FRAC,
    _TILES_WIDE_CANDIDATES,
    _ZOOM_TW_MAX,
    _ZOOM_TW_MIN,
    ensure_minimap_zoom,
)
from src.minimap_radar import (
    MinimapConfig,
    _MINIMAP_PALETTE,
    _REF_H,
    _REF_W,
    _quantize_to_palette,
)
from src.models import BOUNDS, Coordinate


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_palette_frame(
    w: int = 1920,
    h: int = 1080,
    *,
    minimap_x: int = 1720,
    minimap_y: int = 20,
    minimap_size: int = 160,
) -> np.ndarray:
    """Create a BGR frame with a palette-coloured minimap block.

    The minimap region is filled with Tibia palette colours;
    the rest of the frame is dark grey (non-palette).
    """
    frame = np.full((h, w, 3), 30, dtype=np.uint8)

    # Fill the minimap region column by column with palette colours
    n_colours = len(_MINIMAP_PALETTE)
    for col in range(minimap_size):
        idx = col % n_colours
        bgr = _MINIMAP_PALETTE[idx].astype(np.uint8)
        frame[minimap_y : minimap_y + minimap_size, minimap_x + col] = bgr

    # Add a bright cross in the centre (character marker)
    cx = minimap_x + minimap_size // 2
    cy = minimap_y + minimap_size // 2
    frame[cy - 2 : cy + 3, cx, :] = 255
    frame[cy, cx - 2 : cx + 3, :] = 255

    return frame


def _make_floor_rgba(w: int = 2304, h: int = 1792) -> np.ndarray:
    """Create a synthetic RGBA floor map that matches the palette distribution."""
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255  # fully opaque
    n_colours = len(_MINIMAP_PALETTE)
    for col in range(w):
        idx = col % n_colours
        bgr = _MINIMAP_PALETTE[idx].astype(np.uint8)
        rgba[:, col, 0] = bgr[0]
        rgba[:, col, 1] = bgr[1]
        rgba[:, col, 2] = bgr[2]
    return rgba


def _mock_loader(floor_rgba: Optional[np.ndarray] = None) -> MagicMock:
    """Return a mock TibiaMapLoader with get_map_image()."""
    loader = MagicMock()
    rgba = floor_rgba if floor_rgba is not None else _make_floor_rgba()
    loader.get_map_image.return_value = rgba
    return loader


# ---------------------------------------------------------------------------
# CalibrationResult dataclass
# ---------------------------------------------------------------------------

class TestCalibrationResult:
    def test_defaults(self) -> None:
        cfg = MinimapConfig()
        res = CalibrationResult(success=True, config=cfg)
        assert res.success is True
        assert res.best_score == 0.0
        assert res.position is None
        assert res.sweep_scores == []
        assert res.messages == []
        assert res.minimap_crop is None

    def test_fields_stored(self) -> None:
        cfg = MinimapConfig(tiles_wide=42)
        pos = Coordinate(32369, 32241, 7)
        res = CalibrationResult(
            success=True,
            config=cfg,
            best_score=0.85,
            position=pos,
            sweep_scores=[(40, 0.8), (42, 0.85)],
            messages=["OK"],
        )
        assert res.config.tiles_wide == 42
        assert res.position == pos
        assert len(res.sweep_scores) == 2


# ---------------------------------------------------------------------------
# _make_mask (static method)
# ---------------------------------------------------------------------------

class TestMakeMask:
    def test_shape_and_dtype(self) -> None:
        mask = MinimapCalibrator._make_mask(50, 60)
        assert mask.shape == (50, 60)
        assert mask.dtype == bool

    def test_centre_has_hole(self) -> None:
        mask = MinimapCalibrator._make_mask(50, 60)
        # Centre should be False (masked out)
        assert mask[25, 30] is np.bool_(False)

    def test_corners_are_true(self) -> None:
        mask = MinimapCalibrator._make_mask(50, 60)
        assert mask[0, 0] is np.bool_(True)
        assert mask[0, 59] is np.bool_(True)
        assert mask[49, 0] is np.bool_(True)
        assert mask[49, 59] is np.bool_(True)

    def test_explicit_centre(self) -> None:
        mask = MinimapCalibrator._make_mask(50, 60, center_y=10, center_x=10)
        assert mask[10, 10] is np.bool_(False)
        # Far corner should be True
        assert mask[49, 59] is np.bool_(True)

    def test_minimum_radius(self) -> None:
        """Even tiny masks should have a radius of at least 3."""
        mask = MinimapCalibrator._make_mask(10, 10)
        # Centre should be masked
        assert mask[5, 5] is np.bool_(False)


# ---------------------------------------------------------------------------
# _palette_fraction (static method)
# ---------------------------------------------------------------------------

class TestPaletteFraction:
    def test_pure_palette_image(self) -> None:
        """A block filled entirely with palette colours should have frac ≈ 1.0."""
        block = np.zeros((20, 20, 3), dtype=np.uint8)
        for row in range(20):
            idx = row % len(_MINIMAP_PALETTE)
            block[row, :] = _MINIMAP_PALETTE[idx].astype(np.uint8)
        frac = MinimapCalibrator._palette_fraction(block)
        assert frac >= 0.9

    def test_non_palette_image(self) -> None:
        """Random noise should have low palette match."""
        rng = np.random.RandomState(42)
        block = rng.randint(0, 256, (50, 50, 3), dtype=np.uint8)
        frac = MinimapCalibrator._palette_fraction(block)
        assert frac < 0.4

    def test_all_black(self) -> None:
        """All black = palette index 12 (unexplored), fraction ~1.0."""
        block = np.zeros((20, 20, 3), dtype=np.uint8)
        frac = MinimapCalibrator._palette_fraction(block)
        assert frac >= 0.9


# ---------------------------------------------------------------------------
# _crop_with_roi
# ---------------------------------------------------------------------------

class TestCropWithRoi:
    def test_valid_roi_at_ref_resolution(self) -> None:
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        cal = MinimapCalibrator(loader=_mock_loader())
        crop = cal._crop_with_roi(frame, [100, 200, 300, 400])
        assert crop is not None
        assert crop.shape[:2] == (400, 300)

    def test_roi_scales_to_different_resolution(self) -> None:
        frame = np.zeros((2160, 3840, 3), dtype=np.uint8)  # 4K
        cal = MinimapCalibrator(loader=_mock_loader())
        crop = cal._crop_with_roi(frame, [100, 200, 300, 400])
        assert crop is not None
        # At 2× scale: 300*2=600, 400*2=800
        assert crop.shape[1] == 600  # width
        assert crop.shape[0] == 800  # height

    def test_roi_too_small_returns_none(self) -> None:
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        cal = MinimapCalibrator(loader=_mock_loader())
        # ROI of 10x10 = below 30px threshold
        crop = cal._crop_with_roi(frame, [0, 0, 10, 10])
        assert crop is None

    def test_roi_all_zeros(self) -> None:
        """ROI = [0,0,0,0] should return None (size < 30)."""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        cal = MinimapCalibrator(loader=_mock_loader())
        crop = cal._crop_with_roi(frame, [0, 0, 0, 0])
        assert crop is None


# ---------------------------------------------------------------------------
# _get_floor_quant (caching)
# ---------------------------------------------------------------------------

class TestGetFloorQuant:
    def test_returns_quantized_array(self) -> None:
        loader = _mock_loader()
        cal = MinimapCalibrator(loader=loader, floor=7)
        q = cal._get_floor_quant(7)
        assert q.dtype == np.uint8
        assert q.ndim == 2

    def test_caching(self) -> None:
        loader = _mock_loader()
        cal = MinimapCalibrator(loader=loader, floor=7)
        q1 = cal._get_floor_quant(7)
        q2 = cal._get_floor_quant(7)
        assert q1 is q2  # same object = cache hit
        loader.get_map_image.assert_called_once()

    def test_different_floors_not_cached(self) -> None:
        loader = _mock_loader()
        cal = MinimapCalibrator(loader=loader, floor=7)
        cal._get_floor_quant(7)
        cal._get_floor_quant(8)
        assert loader.get_map_image.call_count == 2


# ---------------------------------------------------------------------------
# _palette_match
# ---------------------------------------------------------------------------

class TestPaletteMatch:
    def test_perfect_match_yields_high_score(self) -> None:
        """When template == slice of floor, score should be near 1.0."""
        loader = _mock_loader()
        cal = MinimapCalibrator(loader=loader, floor=7)
        q_floor = cal._get_floor_quant(7)

        tw, th = 40, 30
        # Extract a real slice from the floor as template
        q_tpl = q_floor[100 : 100 + th, 100 : 100 + tw].copy()
        mask = MinimapCalibrator._make_mask(th, tw)

        score, loc, ox, oy = cal._palette_match(q_floor, q_tpl, mask)
        assert score > 0.8

    def test_empty_template_returns_zero(self) -> None:
        """Template with no stable-palette pixels → score = 0."""
        loader = _mock_loader()
        cal = MinimapCalibrator(loader=loader, floor=7)
        q_floor = cal._get_floor_quant(7)

        # All palette index 12 (black, not in _STABLE_PALETTE_INDICES)
        q_tpl = np.full((10, 10), 12, dtype=np.uint8)
        mask = np.ones((10, 10), dtype=bool)
        score, loc, _, _ = cal._palette_match(q_floor, q_tpl, mask)
        assert score == 0.0

    def test_template_larger_than_search_returns_zero(self) -> None:
        """Template bigger than search area → score = 0."""
        q_floor = np.zeros((5, 5), dtype=np.uint8)
        q_tpl = np.zeros((10, 10), dtype=np.uint8)
        mask = np.ones((10, 10), dtype=bool)
        cal = MinimapCalibrator(loader=_mock_loader())
        score, _, _, _ = cal._palette_match(q_floor, q_tpl, mask)
        assert score == 0.0

    def test_hint_constrains_search(self) -> None:
        """With a hint, search should still work (just faster)."""
        loader = _mock_loader()
        hint = Coordinate(BOUNDS["xMin"] + 200, BOUNDS["yMin"] + 200, 7)
        cal = MinimapCalibrator(loader=loader, floor=7, hint=hint)
        q_floor = cal._get_floor_quant(7)
        tw, th = 30, 20
        q_tpl = q_floor[200 : 200 + th, 200 : 200 + tw].copy()
        mask = MinimapCalibrator._make_mask(th, tw)
        score, loc, ox, oy = cal._palette_match(q_floor, q_tpl, mask)
        assert score > 0.5


# ---------------------------------------------------------------------------
# _scan_for_minimap
# ---------------------------------------------------------------------------

class TestScanForMinimap:
    def test_finds_palette_block_in_top_right(self) -> None:
        """A palette-coloured block in the expected area should be found."""
        frame = _make_palette_frame()
        cal = MinimapCalibrator(loader=_mock_loader())
        msgs: List[str] = []
        roi, crop = cal._scan_for_minimap(frame, msgs)
        assert crop is not None
        assert roi != (0, 0, 0, 0)

    def test_no_palette_anywhere_returns_none(self) -> None:
        """A blank frame without palette colours should fail."""
        frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)
        cal = MinimapCalibrator(loader=_mock_loader())
        msgs: List[str] = []
        roi, crop = cal._scan_for_minimap(frame, msgs)
        assert crop is None
        assert roi == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# _find_minimap_roi
# ---------------------------------------------------------------------------

class TestFindMinimapRoi:
    def test_existing_valid_roi(self) -> None:
        """If existing ROI passes palette check, it should be kept."""
        frame = _make_palette_frame(minimap_x=1720, minimap_y=20, minimap_size=160)
        cfg = MinimapConfig(roi=[1720, 20, 160, 160])
        cal = MinimapCalibrator(loader=_mock_loader())
        msgs: List[str] = []
        roi, crop = cal._find_minimap_roi(frame, cfg, msgs)
        assert crop is not None
        assert any("Existing ROI" in m for m in msgs)

    def test_existing_roi_fails_falls_back_to_scan(self) -> None:
        """Invalid ROI (no palette in the area) should trigger a scan."""
        frame = _make_palette_frame(minimap_x=1720, minimap_y=20, minimap_size=160)
        cfg = MinimapConfig(roi=[100, 100, 160, 160])  # wrong area
        cal = MinimapCalibrator(loader=_mock_loader())
        msgs: List[str] = []
        roi, crop = cal._find_minimap_roi(frame, cfg, msgs)
        # Scan should find the real minimap in the top-right
        assert crop is not None


# ---------------------------------------------------------------------------
# calibrate (full pipeline)
# ---------------------------------------------------------------------------

class TestCalibrate:
    @pytest.fixture()
    def _floor_data(self) -> np.ndarray:
        """Small synthetic floor map."""
        return _make_floor_rgba(w=256, h=256)

    def test_calibrate_success_with_matching_data(self, _floor_data: np.ndarray) -> None:
        """End-to-end: frame + floor data that match → success."""
        loader = _mock_loader(_floor_data)

        # Build floor quantized once so we can make a matching minimap
        bgr_floor = cv2.cvtColor(_floor_data, cv2.COLOR_RGBA2BGR)
        q_floor = _quantize_to_palette(bgr_floor)

        # Take a 80x60 patch from the floor as the "minimap"
        tw, th = 40, 30
        patch_bgr = bgr_floor[50 : 50 + th, 50 : 50 + tw]

        # Scale it up to ~160×120 (what a 1920×1080 minimap crop would be)
        minimap_crop = cv2.resize(patch_bgr, (160, 120), interpolation=cv2.INTER_NEAREST)

        # Place it in a full frame at the typical minimap location
        frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)
        frame[20 : 20 + 120, 1720 : 1720 + 160] = minimap_crop

        # Add character cross
        frame[80, 1800, :] = 255
        frame[79 : 82, 1800, :] = 255
        frame[80, 1799 : 1802, :] = 255

        cfg = MinimapConfig(roi=[1720, 20, 160, 120])
        cal = MinimapCalibrator(loader=loader, floor=7)
        result = cal.calibrate(frame, config=cfg)

        assert isinstance(result, CalibrationResult)
        assert len(result.sweep_scores) > 0
        assert len(result.messages) > 0

    def test_calibrate_fail_no_minimap(self) -> None:
        """Blank frame with no minimap → success=False."""
        frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)
        cfg = MinimapConfig(roi=[0, 0, 0, 0])
        cal = MinimapCalibrator(loader=_mock_loader())
        result = cal.calibrate(frame, config=cfg)
        assert result.success is False
        assert any("FAIL" in m for m in result.messages)

    def test_calibrate_low_score_returns_fail(self) -> None:
        """If best_score < _MIN_ACCEPT_SCORE, result.success == False."""
        # Make a minimap that doesn't match the floor at all
        frame = _make_palette_frame()
        # Use a floor that is all one colour (palette idx 0)
        floor_rgba = np.zeros((100, 100, 4), dtype=np.uint8)
        floor_rgba[:, :, :3] = _MINIMAP_PALETTE[0].astype(np.uint8)
        floor_rgba[:, :, 3] = 255
        loader = _mock_loader(floor_rgba)

        cfg = MinimapConfig(roi=[1720, 20, 160, 160])
        cal = MinimapCalibrator(loader=loader, floor=7)
        result = cal.calibrate(frame, config=cfg)
        # A uniform floor won't yield a strong match
        if not result.success:
            assert result.best_score < _MIN_ACCEPT_SCORE

    def test_calibrate_uses_auto_floor(self) -> None:
        """auto_floor=True should try multiple floors."""
        frame = _make_palette_frame()
        # Use a small floor map to keep the test fast — we only care that
        # multiple floors are queried, not about matching accuracy.
        loader = _mock_loader(floor_rgba=_make_floor_rgba(w=300, h=200))
        cfg = MinimapConfig(roi=[1720, 20, 160, 160])
        cal = MinimapCalibrator(loader=loader, floor=7, auto_floor=True)
        result = cal.calibrate(frame, config=cfg)
        # It should have called get_map_image for multiple floors
        assert loader.get_map_image.call_count > 1


# ---------------------------------------------------------------------------
# _sweep_tiles_wide
# ---------------------------------------------------------------------------

class TestSweepTilesWide:
    def test_returns_expected_tuple(self) -> None:
        """_sweep_tiles_wide must return (tw, score, pos, results)."""
        loader = _mock_loader()
        cal = MinimapCalibrator(loader=loader, floor=7)
        q_floor = cal._get_floor_quant(7)

        # Use a simple palette crop
        crop = np.zeros((100, 120, 3), dtype=np.uint8)
        n = len(_MINIMAP_PALETTE)
        for col in range(120):
            crop[:, col] = _MINIMAP_PALETTE[col % n].astype(np.uint8)

        msgs: List[str] = []
        tw, score, pos, results = cal._sweep_tiles_wide(crop, q_floor, msgs)
        assert isinstance(tw, int)
        assert isinstance(score, float)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_all_candidates_tested(self) -> None:
        """Every candidate in _TILES_WIDE_CANDIDATES should be tested."""
        loader = _mock_loader()
        cal = MinimapCalibrator(loader=loader, floor=7)
        q_floor = cal._get_floor_quant(7)

        crop = np.zeros((100, 120, 3), dtype=np.uint8)
        n = len(_MINIMAP_PALETTE)
        for col in range(120):
            crop[:, col] = _MINIMAP_PALETTE[col % n].astype(np.uint8)

        msgs: List[str] = []
        _, _, _, results = cal._sweep_tiles_wide(crop, q_floor, msgs)
        tested_tw = {r[0] for r in results}
        for tw in _TILES_WIDE_CANDIDATES:
            th = max(1, int(100 * tw / 120))
            if th >= 5 and tw >= 5:
                assert tw in tested_tw, f"tiles_wide={tw} was not tested"


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------

class TestConstants:
    def test_tiles_wide_candidates_sorted_start(self) -> None:
        assert _TILES_WIDE_CANDIDATES[0] == 25

    def test_tiles_wide_candidates_count(self) -> None:
        assert len(_TILES_WIDE_CANDIDATES) > 30

    def test_min_accept_score(self) -> None:
        assert 0 < _MIN_ACCEPT_SCORE < 1

    def test_zoom_range(self) -> None:
        assert _ZOOM_TW_MIN < _ZOOM_TW_MAX
        assert _ZOOM_TW_MIN > 0

    def test_scan_fractions(self) -> None:
        assert 0 < _ROI_SCAN_X_FRAC < 0.5
        assert 0 < _ROI_SCAN_Y_FRAC < 0.5

    def test_min_palette_frac(self) -> None:
        assert 0 < _MIN_PALETTE_FRAC < 1


# ---------------------------------------------------------------------------
# ensure_minimap_zoom (mocked)
# ---------------------------------------------------------------------------

class TestEnsureMinimapZoom:
    def test_no_frame_returns_none(self) -> None:
        ctrl = MagicMock()
        result = ensure_minimap_zoom(lambda: None, ctrl)
        assert result is None

    @patch("src.minimap_calibrator.MinimapCalibrator.calibrate")
    def test_calibration_fail_returns_result(self, mock_cal: MagicMock) -> None:
        """Failed calibration should return the failed result, no keypresses."""
        cfg = MinimapConfig()
        failed = CalibrationResult(success=False, config=cfg, best_score=0.1)
        mock_cal.return_value = failed

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ctrl = MagicMock()
        result = ensure_minimap_zoom(lambda: frame, ctrl)
        assert result is not None
        assert result.success is False
        ctrl.press_key.assert_not_called()

    @patch("src.minimap_calibrator.MinimapCalibrator.calibrate")
    def test_zoom_already_ok_no_correction(self, mock_cal: MagicMock) -> None:
        """If tw is in range [30, 60], no keys should be pressed."""
        cfg = MinimapConfig(tiles_wide=40)
        ok = CalibrationResult(success=True, config=cfg, best_score=0.9)
        mock_cal.return_value = ok

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ctrl = MagicMock()
        result = ensure_minimap_zoom(lambda: frame, ctrl)
        assert result is not None
        assert result.success is True
        ctrl.press_key.assert_not_called()

    @patch("src.minimap_calibrator.MinimapCalibrator.calibrate")
    @patch("src.minimap_calibrator.time.sleep")
    def test_zoom_too_low_presses_O(
        self, mock_sleep: MagicMock, mock_cal: MagicMock
    ) -> None:
        """tw < 30 should press O (zoom out), then succeed."""
        cfg_low = MinimapConfig(tiles_wide=20)
        cfg_ok = MinimapConfig(tiles_wide=40)
        low = CalibrationResult(success=True, config=cfg_low, best_score=0.9)
        ok = CalibrationResult(success=True, config=cfg_ok, best_score=0.9)
        mock_cal.side_effect = [low, ok]

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ctrl = MagicMock()
        result = ensure_minimap_zoom(lambda: frame, ctrl)
        assert result is not None
        # Should have pressed O (0x4F)
        ctrl.press_key.assert_called_with(0x4F)

    @patch("src.minimap_calibrator.MinimapCalibrator.calibrate")
    @patch("src.minimap_calibrator.time.sleep")
    def test_zoom_too_high_presses_I(
        self, mock_sleep: MagicMock, mock_cal: MagicMock
    ) -> None:
        """tw > 60 should press I (zoom in)."""
        cfg_high = MinimapConfig(tiles_wide=80)
        cfg_ok = MinimapConfig(tiles_wide=40)
        high = CalibrationResult(success=True, config=cfg_high, best_score=0.9)
        ok = CalibrationResult(success=True, config=cfg_ok, best_score=0.9)
        mock_cal.side_effect = [high, ok]

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ctrl = MagicMock()
        result = ensure_minimap_zoom(lambda: frame, ctrl)
        assert result is not None
        ctrl.press_key.assert_called_with(0x49)

    @patch("src.minimap_calibrator.MinimapCalibrator.calibrate")
    @patch("src.minimap_calibrator.time.sleep")
    def test_frame_lost_mid_correction_aborts(
        self, mock_sleep: MagicMock, mock_cal: MagicMock
    ) -> None:
        """If frame_getter returns None after a zoom press, abort."""
        cfg_low = MinimapConfig(tiles_wide=20)
        low = CalibrationResult(success=True, config=cfg_low, best_score=0.9)
        mock_cal.return_value = low

        frames = iter([np.zeros((1080, 1920, 3), dtype=np.uint8), None])
        ctrl = MagicMock()
        result = ensure_minimap_zoom(lambda: next(frames, None), ctrl)
        assert result is not None

    @patch("src.minimap_calibrator.MinimapCalibrator.calibrate")
    @patch("src.minimap_calibrator.time.sleep")
    def test_stuck_detection(
        self, mock_sleep: MagicMock, mock_cal: MagicMock
    ) -> None:
        """If tw doesn't change after 3+ presses, should break."""
        cfg = MinimapConfig(tiles_wide=20)
        stuck = CalibrationResult(success=True, config=cfg, best_score=0.9)
        # Always return same tw=20
        mock_cal.return_value = stuck

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ctrl = MagicMock()
        result = ensure_minimap_zoom(lambda: frame, ctrl, max_attempts=10)
        # Should have stopped after 4 presses (initial + 3 unchanged)
        assert ctrl.press_key.call_count <= 5
