"""
tests/test_character_detector.py
=================================
Tests for src/character_detector.py.

Focused on pure-Python parts:
  - DetectorConfig  (save / load)
  - _COORD_RE       (regex patterns)
  - CoordinateOCR._parse  (static, no EasyOCR)
  - ImageProcessor.preprocess  (OpenCV pipeline)
  - CharacterDetector (construction, callbacks, detect_once with mocked source)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.character_detector import (
    DetectorConfig,
    ImageProcessor,
    CoordinateOCR,
    CharacterDetector,
    _COORD_RE,
)
from src.models import Coordinate


# ─────────────────────────────────────────────────────────────────────────────
# TestDetectorConfigDefaults
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectorConfigDefaults:
    def test_roi_default(self):
        cfg = DetectorConfig()
        assert cfg.roi == [0, 0, 200, 40]

    def test_obs_source_empty(self):
        assert DetectorConfig().obs_source == ""

    def test_ws_defaults(self):
        cfg = DetectorConfig()
        assert cfg.obs_ws_host     == "localhost"
        assert cfg.obs_ws_port     == 4455
        assert cfg.obs_ws_password == ""

    def test_sample_interval(self):
        assert DetectorConfig().sample_interval == pytest.approx(0.5)

    def test_ocr_confidence(self):
        assert DetectorConfig().ocr_confidence == pytest.approx(0.4)


# ─────────────────────────────────────────────────────────────────────────────
# TestDetectorConfigSaveLoad
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectorConfigSaveLoad:
    def test_roundtrip(self, tmp_path):
        cfg = DetectorConfig(
            roi=[10, 20, 300, 50],
            obs_source="Tibia",
            obs_ws_host="192.168.1.1",
            obs_ws_port=4456,
            obs_ws_password="secret",
            ocr_confidence=0.7,
            sample_interval=0.25,
        )
        p = tmp_path / "dc.json"
        cfg.save(p)
        loaded = DetectorConfig.load(p)
        assert loaded.roi            == [10, 20, 300, 50]
        assert loaded.obs_source     == "Tibia"
        assert loaded.obs_ws_port    == 4456
        assert loaded.ocr_confidence == pytest.approx(0.7)

    def test_load_missing_returns_default(self, tmp_path):
        cfg = DetectorConfig.load(tmp_path / "nope.json")
        assert cfg.roi == [0, 0, 200, 40]

    def test_save_creates_file(self, tmp_path):
        p = tmp_path / "dc.json"
        DetectorConfig().save(p)
        assert p.exists()


# ─────────────────────────────────────────────────────────────────────────────
# TestCoordRegex
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordRegex:
    """Verify _COORD_RE matches all documented Tibia coordinate formats."""

    def test_csv_format(self):
        m = _COORD_RE.search("32369, 32241, 7")
        assert m is not None
        assert m.group(1) == "32369"
        assert m.group(2) == "32241"
        assert m.group(3) == "7"

    def test_labelled_format(self):
        m = _COORD_RE.search("X: 32369 Y: 32241 Z: 7")
        assert m is not None
        assert m.group(1) == "32369"
        assert m.group(3) == "7"

    def test_paren_format(self):
        m = _COORD_RE.search("(32369,32241,7)")
        assert m is not None
        assert m.group(2) == "32241"

    def test_pipe_separator(self):
        m = _COORD_RE.search("32369|32241|7")
        assert m is not None

    def test_mixed_spaces(self):
        m = _COORD_RE.search("32369 32241 7")
        assert m is not None

    def test_no_match_on_short_numbers(self):
        """3-digit numbers should not match (need at least 4)."""
        m = _COORD_RE.search("123 456 7")
        assert m is None

    def test_no_match_on_empty(self):
        assert _COORD_RE.search("") is None

    def test_extracts_from_noisy_text(self):
        """OCR-typical garbage around the numbers."""
        m = _COORD_RE.search("Pos: 32100, 31500, 7 mana: 590")
        assert m is not None
        assert m.group(1) == "32100"


# ─────────────────────────────────────────────────────────────────────────────
# TestCoordinateOCR_Parse
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateOCR_Parse:
    """Test the static _parse method without invoking EasyOCR."""

    def test_valid_text(self):
        c = CoordinateOCR._parse("32369, 32241, 7")
        assert c == Coordinate(32369, 32241, 7)

    def test_valid_labeled(self):
        c = CoordinateOCR._parse("X: 32369 Y: 32241 Z: 7")
        assert c is not None
        assert c.x == 32369
        assert c.z == 7

    def test_empty_returns_none(self):
        assert CoordinateOCR._parse("") is None

    def test_out_of_range_x_returns_none(self):
        # x below xMin (31744)
        assert CoordinateOCR._parse("10000, 32241, 7") is None

    def test_out_of_range_y_returns_none(self):
        # y above yMax (32768)
        assert CoordinateOCR._parse("32369, 99999, 7") is None

    def test_out_of_range_z_returns_none(self):
        # z > 15
        assert CoordinateOCR._parse("32369, 32241, 20") is None

    def test_valid_boundaries(self):
        # Exact boundary values
        c = CoordinateOCR._parse(f"{31744}, {30976}, 0")
        assert c == Coordinate(31744, 30976, 0)

    def test_garbage_text_returns_none(self):
        assert CoordinateOCR._parse("no numbers here at all") is None


# ─────────────────────────────────────────────────────────────────────────────
# TestImageProcessor
# ─────────────────────────────────────────────────────────────────────────────

class TestImageProcessor:
    def test_bgr_input_returns_2d(self):
        proc = ImageProcessor(scale=1)
        img = np.random.randint(0, 255, (40, 200, 3), dtype=np.uint8)
        result = proc.preprocess(img)
        assert result.ndim == 2

    def test_grayscale_input(self):
        proc = ImageProcessor(scale=1)
        img = np.random.randint(0, 255, (40, 200), dtype=np.uint8)
        result = proc.preprocess(img)
        assert result.ndim == 2

    def test_rgba_input(self):
        proc = ImageProcessor(scale=1)
        img = np.random.randint(0, 255, (40, 200, 4), dtype=np.uint8)
        result = proc.preprocess(img)
        assert result.ndim == 2

    def test_scale_factor_increases_size(self):
        proc = ImageProcessor(scale=4)
        img = np.random.randint(0, 255, (10, 50, 3), dtype=np.uint8)
        result = proc.preprocess(img)
        assert result.shape == pytest.approx((10 * 4, 50 * 4), abs=5)

    def test_output_dtype_uint8(self):
        proc = ImageProcessor(scale=2)
        img = np.random.randint(0, 255, (20, 100, 3), dtype=np.uint8)
        result = proc.preprocess(img)
        assert result.dtype == np.uint8

    def test_output_values_binary(self):
        """Adaptive threshold should produce mostly 0 or 255."""
        proc = ImageProcessor(scale=2)
        img = np.random.randint(0, 255, (20, 100, 3), dtype=np.uint8)
        result = proc.preprocess(img)
        unique_vals = set(np.unique(result).tolist())
        assert unique_vals.issubset({0, 255})


# ─────────────────────────────────────────────────────────────────────────────
# TestCharacterDetectorConstruction
# ─────────────────────────────────────────────────────────────────────────────

class TestCharacterDetectorConstruction:
    def test_virtual_cam_source(self):
        d = CharacterDetector(source="virtual-cam")
        assert d._source_name == "virtual-cam"

    def test_screen_source(self):
        d = CharacterDetector(source="screen")
        assert d._source_name == "screen"

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="source debe ser"):
            CharacterDetector(source="invalid-source")

    def test_last_position_none_initially(self):
        d = CharacterDetector(source="virtual-cam")
        assert d.last_position is None

    def test_on_position_callback_registered(self):
        d = CharacterDetector(source="virtual-cam")
        cb = MagicMock()
        d.on_position(cb)
        assert cb in d._callbacks


# ─────────────────────────────────────────────────────────────────────────────
# TestDetectOnce
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectOnce:
    def test_detect_once_returns_none_when_source_returns_none(self):
        d = CharacterDetector(source="virtual-cam")
        d._source = MagicMock()
        d._source.get_frame.return_value = None
        result = d.detect_once()
        assert result is None

    def test_detect_once_calls_process_frame(self):
        d = CharacterDetector(source="virtual-cam")
        d._source = MagicMock()
        frame = np.zeros((40, 200, 3), dtype=np.uint8)
        d._source.get_frame.return_value = frame
        # Patch _process_frame to avoid EasyOCR
        with patch.object(d, '_process_frame', return_value=Coordinate(32369, 32241, 7)) as mock_process:
            result = d.detect_once()
            assert result == Coordinate(32369, 32241, 7)
            mock_process.assert_called_once_with(frame)


# ─────────────────────────────────────────────────────────────────────────────
# TestProcessFrame
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessFrame:
    def test_empty_roi_returns_none(self):
        """ROI with w or h = 0 should return None without crashing."""
        d = CharacterDetector(source="virtual-cam",
                              config=DetectorConfig(roi=[0, 0, 0, 40]))
        frame = np.zeros((80, 200, 3), dtype=np.uint8)
        result = d._process_frame(frame)
        assert result is None

    def test_valid_roi_calls_ocr(self):
        """_process_frame should invoke OCR on the cropped frame."""
        d = CharacterDetector(source="virtual-cam",
                              config=DetectorConfig(roi=[0, 0, 200, 40]))
        frame = np.zeros((80, 200, 3), dtype=np.uint8)
        coord = Coordinate(32369, 32241, 7)
        d._ocr = MagicMock()
        d._ocr.read.return_value = coord
        result = d._process_frame(frame)
        assert result == coord
        d._ocr.read.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# TestCallbackFired
# ─────────────────────────────────────────────────────────────────────────────

class TestCallbackFired:
    def test_callback_fired_on_position_change(self):
        """The callback is invoked when the detected position changes."""
        d = CharacterDetector(source="virtual-cam")
        d._source = MagicMock()
        frame = np.zeros((40, 200, 3), dtype=np.uint8)
        d._source.get_frame.return_value = frame
        coord = Coordinate(32369, 32241, 7)

        fired = []
        d.on_position(lambda c: fired.append(c))

        # Simulate one _loop iteration inline (detect_once + compare + fire)
        with patch.object(d, '_process_frame', return_value=coord):
            result = d.detect_once()
            if result and result != d._last:
                d._last = result
                for cb in d._callbacks:
                    cb(result)

        assert fired == [coord]

    def test_callback_not_fired_when_position_unchanged(self):
        d = CharacterDetector(source="virtual-cam")
        coord = Coordinate(32369, 32241, 7)
        d._last = coord
        d._source = MagicMock()
        d._source.get_frame.return_value = np.zeros((40, 200, 3), dtype=np.uint8)

        fired = []
        d.on_position(lambda c: fired.append(c))

        # coord == d._last → no callback
        with patch.object(d, '_process_frame', return_value=coord):
            result = d.detect_once()
            if result and result != d._last:
                for cb in d._callbacks:
                    cb(result)

        assert fired == []


# ─────────────────────────────────────────────────────────────────────────────
# TestIsRunning
# ─────────────────────────────────────────────────────────────────────────────

class TestIsRunning:

    def test_not_running_initially(self):
        d = CharacterDetector(source="virtual-cam")
        assert d.is_running is False

    def test_running_after_manual_set(self):
        d = CharacterDetector(source="virtual-cam")
        d._running = True
        assert d.is_running is True

    def test_not_running_after_stop_flag(self):
        d = CharacterDetector(source="virtual-cam")
        d._running = True
        d._running = False
        assert d.is_running is False


# ─────────────────────────────────────────────────────────────────────────────
# TestRemoveCallback
# ─────────────────────────────────────────────────────────────────────────────

class TestRemoveCallback:

    def test_remove_registered_callback(self):
        d = CharacterDetector(source="virtual-cam")
        cb = MagicMock()
        d.on_position(cb)
        assert d.remove_callback(cb) is True
        assert cb not in d._callbacks

    def test_remove_unknown_callback_returns_false(self):
        d = CharacterDetector(source="virtual-cam")
        cb = MagicMock()
        assert d.remove_callback(cb) is False

    def test_remove_only_target_leaves_others(self):
        d = CharacterDetector(source="virtual-cam")
        cb1, cb2 = MagicMock(), MagicMock()
        d.on_position(cb1)
        d.on_position(cb2)
        d.remove_callback(cb1)
        assert cb1 not in d._callbacks
        assert cb2 in d._callbacks

    def test_double_remove_returns_false_second_time(self):
        d = CharacterDetector(source="virtual-cam")
        cb = MagicMock()
        d.on_position(cb)
        d.remove_callback(cb)
        assert d.remove_callback(cb) is False


# ─────────────────────────────────────────────────────────────────────────────
# TestClearCallbacks
# ─────────────────────────────────────────────────────────────────────────────

class TestClearCallbacks:

    def test_clear_removes_all(self):
        d = CharacterDetector(source="virtual-cam")
        for _ in range(5):
            d.on_position(MagicMock())
        d.clear_callbacks()
        assert d._callbacks == []

    def test_clear_empty_does_not_raise(self):
        d = CharacterDetector(source="virtual-cam")
        d.clear_callbacks()  # should not raise
        assert d._callbacks == []

    def test_add_after_clear_works(self):
        d = CharacterDetector(source="virtual-cam")
        d.on_position(MagicMock())
        d.clear_callbacks()
        cb = MagicMock()
        d.on_position(cb)
        assert len(d._callbacks) == 1
        assert d._callbacks[0] is cb


# ─────────────────────────────────────────────────────────────────────────────
# TestUpdateConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestCharacterDetectorUpdateConfig:

    def test_config_replaced(self):
        d = CharacterDetector(source="virtual-cam")
        new_cfg = DetectorConfig(ocr_confidence=0.9)
        d.update_config(new_cfg)
        assert d._cfg is new_cfg

    def test_ocr_confidence_updated(self):
        d = CharacterDetector(source="virtual-cam")
        new_cfg = DetectorConfig(ocr_confidence=0.8)
        d.update_config(new_cfg)
        assert d._ocr._confidence == pytest.approx(0.8)

    def test_existing_callbacks_unchanged(self):
        d = CharacterDetector(source="virtual-cam")
        cb = MagicMock()
        d.on_position(cb)
        d.update_config(DetectorConfig())
        assert cb in d._callbacks

    def test_last_position_not_cleared(self):
        d = CharacterDetector(source="virtual-cam")
        coord = Coordinate(32369, 32241, 7)
        d._last = coord
        d.update_config(DetectorConfig())
        assert d._last == coord


# ─────────────────────────────────────────────────────────────────────────────
# callback_count / source_name / stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestCharacterDetectorExtras:

    def test_callback_count_zero_initially(self):
        d = CharacterDetector(source="virtual-cam")
        assert d.callback_count == 0

    def test_callback_count_increases(self):
        d = CharacterDetector(source="virtual-cam")
        d.on_position(lambda c: None)
        d.on_position(lambda c: None)
        assert d.callback_count == 2

    def test_callback_count_decreases_after_remove(self):
        d = CharacterDetector(source="virtual-cam")
        cb = MagicMock()
        d.on_position(cb)
        d.remove_callback(cb)
        assert d.callback_count == 0

    def test_callback_count_zero_after_clear(self):
        d = CharacterDetector(source="virtual-cam")
        d.on_position(lambda c: None)
        d.on_position(lambda c: None)
        d.clear_callbacks()
        assert d.callback_count == 0

    def test_source_name_virtual_cam(self):
        d = CharacterDetector(source="virtual-cam")
        assert d.source_name == "virtual-cam"

    def test_source_name_screen(self):
        d = CharacterDetector(source="screen")
        assert d.source_name == "screen"

    def test_stats_snapshot_returns_dict(self):
        d = CharacterDetector(source="virtual-cam")
        assert isinstance(d.stats_snapshot(), dict)

    def test_stats_snapshot_all_keys(self):
        d = CharacterDetector(source="virtual-cam")
        snap = d.stats_snapshot()
        for key in ("is_running", "last_position_set",
                    "callback_count", "source_name"):
            assert key in snap, f"Missing key: {key}"

    def test_stats_snapshot_initial_values(self):
        d = CharacterDetector(source="virtual-cam")
        snap = d.stats_snapshot()
        assert snap["is_running"]        is False
        assert snap["last_position_set"] is False
        assert snap["callback_count"]    == 0
        assert snap["source_name"]       == "virtual-cam"

    def test_stats_snapshot_reflects_last_position(self):
        d = CharacterDetector(source="virtual-cam")
        d._last = Coordinate(32369, 32241, 7)
        snap = d.stats_snapshot()
        assert snap["last_position_set"] is True

    def test_stats_snapshot_callback_count_matches(self):
        d = CharacterDetector(source="virtual-cam")
        d.on_position(lambda c: None)
        snap = d.stats_snapshot()
        assert snap["callback_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# has_callbacks
# ─────────────────────────────────────────────────────────────────────────────

class TestHasCallbacks:

    def test_false_initially(self):
        d = CharacterDetector(source="virtual-cam")
        assert d.has_callbacks is False

    def test_true_after_register(self):
        d = CharacterDetector(source="virtual-cam")
        d.on_position(lambda c: None)
        assert d.has_callbacks is True

    def test_false_after_clear(self):
        d = CharacterDetector(source="virtual-cam")
        d.on_position(lambda c: None)
        d.clear_callbacks()
        assert d.has_callbacks is False

    def test_false_after_remove_last(self):
        d = CharacterDetector(source="virtual-cam")
        cb = MagicMock()
        d.on_position(cb)
        d.remove_callback(cb)
        assert d.has_callbacks is False

    def test_true_with_multiple_callbacks(self):
        d = CharacterDetector(source="virtual-cam")
        d.on_position(lambda c: None)
        d.on_position(lambda c: None)
        assert d.has_callbacks is True


# ─────────────────────────────────────────────────────────────────────────────
# has_last_position
# ─────────────────────────────────────────────────────────────────────────────

class TestHasLastPosition:

    def test_false_initially(self):
        d = CharacterDetector(source="virtual-cam")
        assert d.has_last_position is False

    def test_true_after_setting_last(self):
        d = CharacterDetector(source="virtual-cam")
        d._last = Coordinate(32369, 32241, 7)
        assert d.has_last_position is True

    def test_false_when_last_none(self):
        d = CharacterDetector(source="virtual-cam")
        d._last = None
        assert d.has_last_position is False

    def test_consistent_with_stats_snapshot(self):
        d = CharacterDetector(source="virtual-cam")
        d._last = Coordinate(100, 200, 5)
        assert d.has_last_position == d.stats_snapshot()["last_position_set"]

    def test_false_cleared_by_none(self):
        d = CharacterDetector(source="virtual-cam")
        d._last = Coordinate(1, 2, 3)
        d._last = None
        assert d.has_last_position is False
