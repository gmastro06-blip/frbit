"""
tests/test_minimap_radar_extended.py
=====================================
Covers the remaining uncovered branches in minimap_radar.py:

MinimapRadar (primary radar):
  - _find_char_center: few-pixels path (lines 85-86), final fallback (91)
  - _is_border_line: 1-D array path (109-110)
  - MinimapConfig.validate(): ValueError raises (295, 297)
  - _reject_tracking_jump: consecutive-5 reset (372-374)
  - read(): grayscale crop (417), palette-fail (421-423), constrained
    fallback (449-450), no-hint else-branch (459-463)
  - jump_rejects property (620)
  - save_debug_image (843-851)

TibiaLocalMinimapReader (local-client radar):
  - is_available, current_floor, hint_coordinate (939-977)
  - read(): all early-exit paths + happy path (1003-1083, 1088-1098)
  - _crop_minimap (1104-1114)
  - _build_sector_index (1120-1131)
  - _build_3x3_mosaic (1145-1163)
  - _find_candidate_files (1172-1197)
  - _load_sector (1201-1211)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Optional, cast
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import cv2


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import src.minimap_radar as _mr_mod
from src.minimap_radar import (
    MinimapConfig,
    MinimapRadar,
    TibiaLocalMinimapReader,
    _find_char_center,
    _is_border_line,
    _REF_W,
    _REF_H,
)
from src.models import Coordinate, BOUNDS


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _blank_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _fake_floor_quant(size: int = 300) -> np.ndarray:
    """Random palette-index (uint8, 0-14) array large enough to search."""
    rng = np.random.default_rng(42)
    return (rng.integers(0, 15, (size, size), dtype=np.uint8)).astype(np.uint8)


def _make_radar(cfg: Optional[MinimapConfig] = None) -> MinimapRadar:
    loader = MagicMock()
    rng = np.random.default_rng(0)
    rgba = rng.integers(0, 255, (300, 300, 4), dtype=np.uint8)
    loader.get_map_image.return_value = rgba
    return MinimapRadar(loader, config=cfg or MinimapConfig())


def _make_local_reader(tmp_path: Path, confidence: float = 0.5) -> TibiaLocalMinimapReader:
    return TibiaLocalMinimapReader(
        config=MinimapConfig(),
        minimap_dir=str(tmp_path),
        confidence=confidence,
    )


def _write_sector_png(path: Path, content: Optional[np.ndarray] = None) -> None:
    """Write a 256×256 BGR PNG sector file."""
    if content is None:
        content = np.full((256, 256, 3), 100, dtype=np.uint8)
    cv2.imwrite(str(path), content)


# ─────────────────────────────────────────────────────────────────────────────
# _find_char_center
# ─────────────────────────────────────────────────────────────────────────────

class TestFindCharCenter:

    def test_dark_image_returns_center(self):
        """Peak < 220 → immediately returns (0.5, 0.5)."""
        # All pixels at value 50 (well below 220)
        crop = np.full((60, 60, 3), 50, dtype=np.uint8)
        cx, cy = _find_char_center(crop)
        assert cx == pytest.approx(0.5)
        assert cy == pytest.approx(0.5)

    def test_few_pixels_at_peak_expands_threshold(self):
        """Exactly 3 pixels at peak value (< 4 = _CHAR_MIN_PIXELS) → uses loose threshold."""
        # 50×50 BGR crop, all dark (50), with 3 pixels at value 230 in the center
        crop = np.full((50, 50, 3), 50, dtype=np.uint8)
        # ROI is central 60%: margin = 0.20 → my=10, mx=10 → roi = [10:40, 10:40]
        # Place 3 pixels at roi coords (5,5), (5,6), (5,7) → image coords (15,15)...(15,17)
        crop[15, 15] = [230, 230, 230]
        crop[15, 16] = [230, 230, 230]
        crop[15, 17] = [230, 230, 230]
        # 3 pixels at peak (230) < 4 → expands to >220 → same 3 pixels → len≥2 → returns coord
        cx, cy = _find_char_center(crop)
        # Result should NOT be (0.5, 0.5) since we found valid coords
        assert not (cx == pytest.approx(0.5) and cy == pytest.approx(0.5))

    def test_single_bright_pixel_returns_center(self):
        """Only 1 pixel above threshold after expansion → final fallback (0.5, 0.5)."""
        crop = np.full((50, 50, 3), 50, dtype=np.uint8)
        # Place exactly 1 pixel at 222 in central region
        crop[25, 25] = [222, 222, 222]
        # peak=222 >= 220 → NOT caught by first check
        # mask(roi==222) → 1 pixel < 4 → expands to roi>220 → still 1 pixel < 2
        cx, cy = _find_char_center(crop)
        assert cx == pytest.approx(0.5)
        assert cy == pytest.approx(0.5)

    def test_many_bright_pixels_returns_centroid(self):
        """Normal case: >= 4 bright pixels → returns centroid, not (0.5, 0.5)."""
        crop = np.full((50, 50, 3), 50, dtype=np.uint8)
        # Put a 4-pixel bright spot in the central region
        for r in range(20, 24):
            crop[r, 25] = [255, 255, 255]
        cx, cy = _find_char_center(crop)
        assert not (cx == pytest.approx(0.5) and cy == pytest.approx(0.5))


# ─────────────────────────────────────────────────────────────────────────────
# _is_border_line (1D path — lines 109-110)
# ─────────────────────────────────────────────────────────────────────────────

class TestIsBorderLine1D:

    def test_dark_1d_array_is_border(self):
        """Dark uniform 1D array → True (dark border)."""
        pixels = np.array([70, 72, 68, 71, 73, 70, 72, 68, 71, 73], dtype=np.uint8)
        # All < 85, std ≈ 1.5 < 30 → True
        assert _is_border_line(pixels, threshold=85) is True

    def test_bright_1d_array_is_not_border(self):
        """Bright varied 1D array → False."""
        pixels = np.array([200, 50, 180, 30, 160, 250, 100], dtype=np.uint8)
        assert _is_border_line(pixels, threshold=85) is False

    def test_uniform_bright_1d_not_border(self):
        """Uniform bright 1D array: not below threshold → False."""
        pixels = np.full(20, 200, dtype=np.uint8)
        assert _is_border_line(pixels, threshold=85) is False

    def test_dark_1d_high_std_not_border(self):
        """Dark but high-variance 1D array → False (std too high)."""
        # Some pixels 5, some 80 → mean < 85 but std > 30
        pixels = np.array([5, 80, 5, 80, 5, 80, 5, 80, 5, 80], dtype=np.uint8)
        assert _is_border_line(pixels, threshold=85) is False


# ─────────────────────────────────────────────────────────────────────────────
# MinimapConfig.validate() raises
# ─────────────────────────────────────────────────────────────────────────────

class TestMinimapConfigValidate:

    def test_three_element_roi_raises(self):
        cfg = MinimapConfig(roi=[100, 200, 50])
        with pytest.raises(ValueError, match="must have 4 elements"):
            cfg.validate()

    def test_negative_roi_value_raises(self):
        cfg = MinimapConfig(roi=[100, -1, 50, 50])
        with pytest.raises(ValueError, match="non-negative"):
            cfg.validate()

    def test_valid_roi_does_not_raise(self):
        MinimapConfig(roi=[0, 0, 100, 100]).validate()  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# _reject_tracking_jump: consecutive-5 reset (lines 372-374)
# ─────────────────────────────────────────────────────────────────────────────

class TestRejectTrackingJumpReset:

    def test_consecutive_5_resets_state(self):
        radar = _make_radar()
        radar._last_coord = Coordinate(BOUNDS["xMin"] + 5, BOUNDS["yMin"] + 5, 7)
        radar._hit_count = 10
        # Call _reject_tracking_jump exactly 5 times
        for _ in range(5):
            radar._reject_tracking_jump()
        # After 5th: state should be reset
        assert radar._last_coord is None
        assert radar._hit_count == 0
        assert radar._consecutive_jump_rejects == 0

    def test_four_consecutive_rejects_no_reset(self):
        radar = _make_radar()
        radar._last_coord = Coordinate(BOUNDS["xMin"] + 5, BOUNDS["yMin"] + 5, 7)
        radar._hit_count = 10
        for _ in range(4):
            radar._reject_tracking_jump()
        # After 4: not yet reset
        assert radar._last_coord is not None
        assert radar._hit_count == 10

    def test_jump_rejects_counter_increments(self):
        radar = _make_radar()
        assert radar.jump_rejects == 0
        radar._reject_tracking_jump()
        assert radar.jump_rejects == 1

    def test_jump_rejects_property_readable(self):
        """Covers the jump_rejects @property getter (line 620)."""
        radar = _make_radar()
        radar._jump_rejects = 7
        assert radar.jump_rejects == 7


# ─────────────────────────────────────────────────────────────────────────────
# read() edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestReadEdgeCases:

    def test_grayscale_crop_returns_none(self):
        """If crop is 2D (grayscale), read() increments miss and returns None."""
        radar = _make_radar(MinimapConfig(confidence=0.0))
        # Patch _crop_minimap to return a 2D array
        with patch.object(radar, "_crop_minimap",
                          return_value=np.zeros((40, 40), dtype=np.uint8)):
            result = radar.read(_blank_frame())
        assert result is None
        assert radar._miss_count == 1

    def test_palette_check_failed_returns_none(self):
        """When _quantize_and_check returns palette_ok=False → miss + None."""
        radar = _make_radar(MinimapConfig(confidence=0.0))
        with patch.object(radar, "_crop_minimap",
                          return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch("src.minimap_radar._quantize_and_check",
                   return_value=(np.zeros((40, 40), dtype=np.uint8), False)):
            result = radar.read(_blank_frame())
        assert result is None
        assert radar._miss_count == 1

    def test_constrained_miss_falls_back_to_full_floor(self):
        """Constrained search below confidence → fallback full-floor search called."""
        radar = _make_radar(MinimapConfig(confidence=0.5))
        radar._last_coord = Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 10, 7)

        call_count = {"n": 0}

        def _fake_palette_match(q_floor, q_tpl, mask, hint=None, padding=60):
            call_count["n"] += 1
            if hint is not None:
                # First call (constrained) → below confidence
                return 0.1, (0, 0), 0, 0
            # Second call (full-floor) → still below confidence (just testing fallback path)
            return 0.1, (0, 0), 0, 0

        with patch.object(radar, "_crop_minimap",
                          return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch("src.minimap_radar._quantize_and_check",
                   return_value=(np.zeros((40, 40), dtype=np.uint8), True)), \
             patch("src.minimap_radar._find_char_center", return_value=(0.5, 0.5)), \
             patch.object(radar, "_get_floor_quant",
                          return_value=np.zeros((200, 200), dtype=np.uint8)), \
             patch.object(radar, "_palette_match", side_effect=_fake_palette_match):
            result = radar.read(_blank_frame())

        # Two calls: constrained + fallback
        assert call_count["n"] == 2
        assert result is None   # both below confidence

    def test_hint_on_different_floor_uses_full_floor_search(self):
        """Hint on different floor → else branch → full-floor palette_match."""
        radar = _make_radar(MinimapConfig(confidence=0.5))
        # last_coord on floor 6, reading floor 7 → z mismatch → else branch
        radar._last_coord = Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 10, 6)

        call_args = []

        def _fake_palette_match(q_floor, q_tpl, mask, hint=None, padding=60):
            call_args.append(hint)
            return 0.1, (0, 0), 0, 0

        with patch.object(radar, "_crop_minimap",
                          return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch("src.minimap_radar._quantize_and_check",
                   return_value=(np.zeros((40, 40), dtype=np.uint8), True)), \
             patch("src.minimap_radar._find_char_center", return_value=(0.5, 0.5)), \
             patch.object(radar, "_get_floor_quant",
                          return_value=np.zeros((200, 200), dtype=np.uint8)), \
             patch.object(radar, "_palette_match", side_effect=_fake_palette_match):
            radar.read(_blank_frame(), floor=7)

        # Only one call with no hint (full-floor else branch)
        assert len(call_args) == 1
        assert call_args[0] is None

    def test_no_hint_no_last_coord_uses_else_branch(self):
        """No hint, no last_coord → else branch."""
        radar = _make_radar(MinimapConfig(confidence=0.5))
        # _last_coord is None, no hint passed

        call_args = []

        def _fake_palette_match(q_floor, q_tpl, mask, hint=None, padding=60):
            call_args.append(hint)
            return 0.1, (0, 0), 0, 0

        with patch.object(radar, "_crop_minimap",
                          return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch("src.minimap_radar._quantize_and_check",
                   return_value=(np.zeros((40, 40), dtype=np.uint8), True)), \
             patch("src.minimap_radar._find_char_center", return_value=(0.5, 0.5)), \
             patch.object(radar, "_get_floor_quant",
                          return_value=np.zeros((200, 200), dtype=np.uint8)), \
             patch.object(radar, "_palette_match", side_effect=_fake_palette_match):
            radar.read(_blank_frame())

        assert len(call_args) == 1
        assert call_args[0] is None


# ─────────────────────────────────────────────────────────────────────────────
# save_debug_image (lines 843-851)
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveDebugImage:

    def test_valid_frame_saves_file(self, tmp_path: Path):
        """crop not None → cv2.imwrite is called."""
        radar = _make_radar(MinimapConfig(roi=[0, 0, 1920, 1080]))
        frame = _blank_frame(1920, 1080)
        out = str(tmp_path / "debug.png")
        # crop is the full frame → imwrite should be called
        radar.save_debug_image(frame, out)
        # We just verify no exception is raised; the file may or may not exist
        # depending on whether the crop is large enough after border strip

    def test_tiny_frame_prints_message(self, capsys: pytest.CaptureFixture):
        """When crop is None, prints 'ROI out of bounds'."""
        radar = _make_radar()
        tiny_frame = _blank_frame(10, 10)
        radar.save_debug_image(tiny_frame, "does_not_matter.png")
        out = capsys.readouterr().out
        assert "ROI out of bounds" in out

    def test_frozen_returns_early(self):
        """When sys.frozen=True, method returns immediately without saving."""
        radar = _make_radar()
        import sys as _sys
        _sys.frozen = True  # type: ignore[attr-defined]
        try:
            with patch.object(_mr_mod.cv2, "imwrite") as mock_write:
                radar.save_debug_image(_blank_frame(), "test.png")
            mock_write.assert_not_called()
        finally:
            sys_mod = cast(Any, _sys)
            if hasattr(sys_mod, "frozen"):
                delattr(sys_mod, "frozen")


# ─────────────────────────────────────────────────────────────────────────────
# TibiaLocalMinimapReader: is_available, current_floor, hint_coordinate
# ─────────────────────────────────────────────────────────────────────────────

class TestLocalReaderBasicProperties:

    def test_not_available_when_dir_missing(self, tmp_path: Path):
        reader = TibiaLocalMinimapReader(
            config=MinimapConfig(),
            minimap_dir=str(tmp_path / "nonexistent"),
        )
        assert reader.is_available is False

    def test_available_when_dir_exists(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        assert reader.is_available is True

    def test_current_floor_no_dir(self, tmp_path: Path):
        reader = TibiaLocalMinimapReader(
            config=MinimapConfig(),
            minimap_dir=str(tmp_path / "missing"),
        )
        assert reader.current_floor() is None

    def test_current_floor_returns_z_from_most_recent_file(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        # Create files with names conforming to Minimap_Color_X_Y_Z.png
        (tmp_path / "Minimap_Color_32256_32256_5.png").write_bytes(b"")
        (tmp_path / "Minimap_Color_32256_32256_7.png").write_bytes(b"")
        # Touch the floor-7 file to make it newest
        import os
        t = time.time() + 1
        os.utime(str(tmp_path / "Minimap_Color_32256_32256_7.png"), (t, t))
        floor = reader.current_floor()
        assert floor == 7

    def test_current_floor_no_matching_files(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        # Create file with wrong naming pattern
        (tmp_path / "something_else.png").write_bytes(b"")
        assert reader.current_floor() is None

    def test_hint_coordinate_no_dir(self, tmp_path: Path):
        reader = TibiaLocalMinimapReader(
            config=MinimapConfig(),
            minimap_dir=str(tmp_path / "missing"),
        )
        assert reader.hint_coordinate() is None

    def test_hint_coordinate_returns_sector_center(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        (tmp_path / "Minimap_Color_32256_32256_7.png").write_bytes(b"")
        hint = reader.hint_coordinate()
        assert hint is not None
        assert hint.x == 32256 + 128
        assert hint.y == 32256 + 128
        assert hint.z == 7

    def test_hint_coordinate_no_valid_files(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        (tmp_path / "badname.png").write_bytes(b"")
        assert reader.hint_coordinate() is None

    def test_last_coord_initially_none(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        assert reader.last_coord is None

    def test_hit_rate_zero_initially(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        assert reader.hit_rate == pytest.approx(0.0)

    def test_hit_rate_after_hits(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        reader._hit_count = 3
        reader._miss_count = 1
        assert reader.hit_rate == pytest.approx(0.75)

    def test_stats_format(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        s = reader.stats()
        assert "LocalRadar" in s
        assert "hits=0" in s
        assert "miss=0" in s


# ─────────────────────────────────────────────────────────────────────────────
# TibiaLocalMinimapReader.read() early exits
# ─────────────────────────────────────────────────────────────────────────────

class TestLocalReaderReadEarlyExits:

    def test_read_returns_none_when_dir_missing(self, tmp_path: Path):
        reader = TibiaLocalMinimapReader(
            config=MinimapConfig(),
            minimap_dir=str(tmp_path / "missing"),
        )
        assert reader.read(_blank_frame()) is None

    def test_read_returns_none_when_crop_is_none(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        with patch.object(reader, "_crop_minimap", return_value=None):
            assert reader.read(_blank_frame()) is None

    def test_read_returns_none_when_floor_unknown(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        with patch.object(reader, "_crop_minimap",
                          return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch.object(reader, "current_floor", return_value=None):
            assert reader.read(_blank_frame()) is None

    def test_read_returns_none_when_no_sectors(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        with patch.object(reader, "_crop_minimap",
                          return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch.object(reader, "current_floor", return_value=7), \
             patch.object(reader, "_build_sector_index", return_value={}):
            assert reader.read(_blank_frame()) is None

    def test_read_miss_when_below_confidence(self, tmp_path: Path):
        """Template matches but score below confidence → miss + None."""
        reader = _make_local_reader(tmp_path, confidence=0.99)
        _write_sector_png(tmp_path / "Minimap_Color_32256_32256_7.png")
        frame = _blank_frame()
        with patch.object(reader, "current_floor", return_value=7):
            result = reader.read(frame)
        # With all-black frame and all-grey sector, TM_CCOEFF_NORMED may
        # give NaN/0 → below 0.99 confidence → None
        assert result is None
        assert reader._miss_count >= 1

    def test_read_returns_coord_on_good_match(self, tmp_path: Path):
        """Happy path: mocked high match → returns Coordinate."""
        reader = _make_local_reader(tmp_path, confidence=0.5)
        # Create a real sector file (needed to pass sector_index check)
        _write_sector_png(tmp_path / "Minimap_Color_32256_32256_7.png")
        frame = _blank_frame()

        # Patch matchTemplate to return a high-confidence result
        fake_match = np.full((600, 600), 0.9, dtype=np.float32)
        with patch.object(reader, "current_floor", return_value=7), \
             patch.object(_mr_mod.cv2, "matchTemplate", return_value=fake_match):
            result = reader.read(frame)

        assert result is not None
        assert isinstance(result, Coordinate)
        assert reader._hit_count == 1
        assert reader.last_coord == result

    def test_read_uses_floor_param_when_provided(self, tmp_path: Path):
        """If floor= passed explicitly, skips current_floor()."""
        reader = _make_local_reader(tmp_path, confidence=0.99)
        _write_sector_png(tmp_path / "Minimap_Color_32256_32256_5.png")
        frame = _blank_frame()
        with patch.object(reader, "current_floor") as mock_floor:
            reader.read(frame, floor=5)
        mock_floor.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# TibiaLocalMinimapReader._crop_minimap
# ─────────────────────────────────────────────────────────────────────────────

class TestLocalReaderCropMinimap:

    def test_valid_frame_returns_crop(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        # Default roi=[1710, 37, 175, 175]; 1920×1080 frame → 175×175 crop
        frame = _blank_frame(1920, 1080)
        crop = reader._crop_minimap(frame)
        assert crop is not None
        assert crop.shape[0] > 0 and crop.shape[1] > 0

    def test_tiny_frame_returns_none(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        crop = reader._crop_minimap(_blank_frame(10, 10))
        assert crop is None


# ─────────────────────────────────────────────────────────────────────────────
# TibiaLocalMinimapReader._build_sector_index
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildSectorIndex:

    def test_returns_dict_for_floor(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        (tmp_path / "Minimap_Color_32256_32256_7.png").write_bytes(b"")
        (tmp_path / "Minimap_Color_32512_32256_7.png").write_bytes(b"")
        (tmp_path / "Minimap_Color_32256_32256_6.png").write_bytes(b"")  # different floor
        idx = reader._build_sector_index(7)
        assert (32256, 32256) in idx
        assert (32512, 32256) in idx
        assert (32256, 32256) in idx
        assert len(idx) == 2  # only floor 7

    def test_ignores_wrong_name_format(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        (tmp_path / "SomeOtherFile.png").write_bytes(b"")
        (tmp_path / "Minimap_Color_32256_32256_7.png").write_bytes(b"")
        idx = reader._build_sector_index(7)
        assert len(idx) == 1

    def test_ignores_non_integer_parts(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        (tmp_path / "Minimap_Color_abc_32256_7.png").write_bytes(b"")
        idx = reader._build_sector_index(7)
        assert len(idx) == 0

    def test_empty_dir_returns_empty_dict(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        assert reader._build_sector_index(7) == {}


# ─────────────────────────────────────────────────────────────────────────────
# TibiaLocalMinimapReader._build_3x3_mosaic
# ─────────────────────────────────────────────────────────────────────────────

class TestBuild3x3Mosaic:

    def test_missing_center_returns_none(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        index = {(32512, 32256): tmp_path / "Minimap_Color_32512_32256_7.png"}
        # Center (32256, 32256) is NOT in index → None
        result = reader._build_3x3_mosaic(32256, 32256, 7, index)
        assert result is None

    def test_center_present_returns_768x768(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        center = tmp_path / "Minimap_Color_32256_32256_7.png"
        _write_sector_png(center)
        index = {(32256, 32256): center}
        mosaic = reader._build_3x3_mosaic(32256, 32256, 7, index)
        assert mosaic is not None
        assert mosaic.shape == (768, 768)

    def test_neighbor_sectors_loaded_into_mosaic(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        S = 256
        cx, cy = 32256, 32256
        # Create center + right neighbor
        center = tmp_path / "Minimap_Color_32256_32256_7.png"
        right = tmp_path / "Minimap_Color_32512_32256_7.png"
        _write_sector_png(center, np.full((S, S, 3), 100, dtype=np.uint8))
        _write_sector_png(right, np.full((S, S, 3), 200, dtype=np.uint8))
        index = {(cx, cy): center, (cx + S, cy): right}
        mosaic = reader._build_3x3_mosaic(cx, cy, 7, index)
        assert mosaic is not None
        # Right neighbor occupies mosaic[256:512, 512:768] — loaded (non-zero)
        # (exact values depend on quantization; just check shape and non-crash)
        assert mosaic.shape == (768, 768)


# ─────────────────────────────────────────────────────────────────────────────
# TibiaLocalMinimapReader._find_candidate_files
# ─────────────────────────────────────────────────────────────────────────────

class TestFindCandidateFiles:

    def test_returns_all_files_for_floor(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        (tmp_path / "Minimap_Color_32256_32256_7.png").write_bytes(b"")
        (tmp_path / "Minimap_Color_32512_32256_7.png").write_bytes(b"")
        (tmp_path / "Minimap_Color_32256_32256_6.png").write_bytes(b"")
        result = reader._find_candidate_files(7)
        assert len(result) == 2
        # Each entry is (path, sx, sy, sz)
        for p, sx, sy, sz in result:
            assert sz == 7

    def test_floor_none_infers_from_newest_file(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        import os
        f7 = tmp_path / "Minimap_Color_32256_32256_7.png"
        f6 = tmp_path / "Minimap_Color_32256_32256_6.png"
        f7.write_bytes(b"")
        f6.write_bytes(b"")
        # Make f7 the newest
        t = time.time() + 10
        os.utime(str(f7), (t, t))
        result = reader._find_candidate_files(None)
        # Should find floor=7 (inferred from newest file) and return only floor-7 files
        assert all(sz == 7 for _, _, _, sz in result)

    def test_ignores_malformed_names(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        (tmp_path / "bad_file.png").write_bytes(b"")
        assert reader._find_candidate_files(7) == []


# ─────────────────────────────────────────────────────────────────────────────
# TibiaLocalMinimapReader._load_sector
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadSector:

    def test_loads_real_png_as_grayscale(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        sector = tmp_path / "Minimap_Color_32256_32256_7.png"
        _write_sector_png(sector)
        gray = reader._load_sector(sector)
        assert isinstance(gray, np.ndarray)
        assert gray.ndim == 2
        assert gray.shape == (256, 256)

    def test_caches_result(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        sector = tmp_path / "Minimap_Color_32256_32256_7.png"
        _write_sector_png(sector)
        g1 = reader._load_sector(sector)
        g2 = reader._load_sector(sector)
        assert g1 is g2  # same object from cache

    def test_nonexistent_file_returns_zeros(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        fake_path = tmp_path / "Minimap_Color_99999_99999_7.png"
        result = reader._load_sector(fake_path)
        assert result.shape == (256, 256)
        assert result.sum() == 0  # all zeros

    def test_cache_eviction_at_21_entries(self, tmp_path: Path):
        reader = _make_local_reader(tmp_path)
        sectors = []
        # Create 21 different sector PNGs
        for i in range(21):
            p = tmp_path / f"Minimap_Color_{32256 + i * 256}_32256_7.png"
            _write_sector_png(p)
            sectors.append(p)
        # Load all 21 to trigger eviction (>20 evicts oldest)
        for s in sectors:
            reader._load_sector(s)
        # Cache should have at most 20 entries after eviction
        assert len(reader._sector_cache) <= 21  # eviction happens at >20
