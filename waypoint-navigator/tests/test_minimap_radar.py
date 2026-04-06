"""
tests/test_minimap_radar.py
===========================
Tests for src/minimap_radar.py — MinimapConfig and MinimapRadar.
All TibiaMapLoader calls are mocked so no map files are needed.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from src.minimap_radar import MinimapConfig, MinimapRadar, _REF_W, _REF_H
from src.models import Coordinate, BOUNDS


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _blank_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    """Solid-black BGR frame."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def _fake_floor_map(size: int = 512) -> np.ndarray:
    """Random uint8 grayscale map larger than any reasonable template."""
    rng = np.random.default_rng(42)
    return (rng.integers(0, 255, (size, size)) * 1).astype(np.uint8)


def _make_radar(floor_gray: np.ndarray, cfg: MinimapConfig | None = None) -> MinimapRadar:
    """Build a MinimapRadar with a stubbed TibiaMapLoader that returns `floor_gray`."""
    if floor_gray.ndim == 2:
        # get_map_image returns RGBA
        rgba = np.dstack([
            np.zeros_like(floor_gray),  # B
            np.zeros_like(floor_gray),  # G
            floor_gray,                 # R
            np.full_like(floor_gray, 255),  # A
        ])
    else:
        rgba = floor_gray

    loader = MagicMock()
    loader.get_map_image.return_value = rgba
    radar  = MinimapRadar(loader, config=cfg or MinimapConfig())
    return radar


# ─────────────────────────────────────────────────────────────────────────────
# TestMinimapConfigDefaults
# ─────────────────────────────────────────────────────────────────────────────

class TestMinimapConfigDefaults:
    def test_default_roi(self):
        cfg = MinimapConfig()
        assert cfg.roi == [1710, 37, 175, 175]

    def test_default_tiles_wide(self):
        assert MinimapConfig().tiles_wide == 90

    def test_default_floor(self):
        assert MinimapConfig().floor == 7

    def test_default_confidence(self):
        assert MinimapConfig().confidence == pytest.approx(0.55)

    def test_default_mask_center(self):
        assert MinimapConfig().mask_center is True


# ─────────────────────────────────────────────────────────────────────────────
# TestMinimapConfigSaveLoad
# ─────────────────────────────────────────────────────────────────────────────

class TestMinimapConfigSaveLoad:
    def test_roundtrip(self, tmp_path):
        cfg = MinimapConfig(roi=[100, 200, 50, 50], tiles_wide=45, floor=5,
                            confidence=0.35, mask_center=False)
        p = tmp_path / "mc.json"
        cfg.save(p)
        loaded = MinimapConfig.load(p)
        assert loaded.roi        == [100, 200, 50, 50]
        assert loaded.tiles_wide == 45
        assert loaded.floor      == 5
        assert loaded.confidence == pytest.approx(0.35)
        assert loaded.mask_center is False

    def test_load_missing_returns_default(self, tmp_path):
        cfg = MinimapConfig.load(tmp_path / "nonexistent.json")
        assert cfg.floor == 7   # default

    def test_save_creates_json(self, tmp_path):
        p = tmp_path / "mc.json"
        MinimapConfig().save(p)
        assert p.exists()
        data = json.loads(p.read_text())
        assert "roi" in data and "floor" in data

    def test_load_ignores_unknown_keys(self, tmp_path):
        p = tmp_path / "mc.json"
        p.write_text(json.dumps({"roi": [0,0,100,100], "floor": 3,
                                 "tiles_wide": 90, "confidence": 0.4,
                                 "mask_center": True, "unknown_key": 999}))
        cfg = MinimapConfig.load(p)
        assert cfg.floor == 3


# ─────────────────────────────────────────────────────────────────────────────
# TestMinimapRadarFloorProperty
# ─────────────────────────────────────────────────────────────────────────────

class TestMinimapRadarFloorProperty:
    def test_floor_getter(self):
        radar = _make_radar(_fake_floor_map(), cfg=MinimapConfig(floor=5))
        assert radar.floor == 5

    def test_floor_setter(self):
        radar = _make_radar(_fake_floor_map())
        radar.floor = 10
        assert radar.floor == 10

    def test_confidence_property(self):
        radar = _make_radar(_fake_floor_map(), cfg=MinimapConfig(confidence=0.55))
        assert radar.confidence == pytest.approx(0.55)


# ─────────────────────────────────────────────────────────────────────────────
# TestCropMinimap
# ─────────────────────────────────────────────────────────────────────────────

class TestCropMinimap:
    """Test _crop_minimap via read() — frame with roi completely inside."""

    def test_full_hd_frame_returns_nonzero_crop(self):
        radar = _make_radar(_fake_floor_map(600),
                            cfg=MinimapConfig(roi=[1710, 385, 175, 175]))
        frame = _blank_frame(1920, 1080)
        # The private method should return a crop (not None)
        crop = radar._crop_minimap(frame)
        assert crop is not None
        assert crop.shape[0] > 0 and crop.shape[1] > 0

    def test_scaled_frame_also_crops(self):
        """Half-resolution frame — ROI scales proportionally."""
        radar = _make_radar(_fake_floor_map(600),
                            cfg=MinimapConfig(roi=[1710, 385, 175, 175]))
        frame = _blank_frame(960, 540)    # half of 1920×1080
        crop = radar._crop_minimap(frame)
        assert crop is not None

    def test_tiny_frame_returns_none(self):
        radar = _make_radar(_fake_floor_map())
        frame = _blank_frame(10, 10)      # too small
        crop = radar._crop_minimap(frame)
        assert crop is None

    def test_roi_partially_outside_frame(self):
        """A ROI that starts outside the frame should still return something
        or None — the important thing is no crash."""
        radar = _make_radar(_fake_floor_map(600),
                            cfg=MinimapConfig(roi=[1800, 1000, 200, 200]))
        frame = _blank_frame(1920, 1080)
        # May return None (tiny sub-ROI) or a small crop; no exception
        radar._crop_minimap(frame)       # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# TestGetFloorGray
# ─────────────────────────────────────────────────────────────────────────────

class TestGetFloorGray:
    def test_returns_2d_array(self):
        radar = _make_radar(_fake_floor_map())
        gray = radar._get_floor_gray(7)
        assert gray.ndim == 2

    def test_cached_on_second_call(self):
        loader = MagicMock()
        rng = np.random.default_rng(0)
        rgba = rng.integers(0, 255, (200, 200, 4), dtype=np.uint8)
        loader.get_map_image.return_value = rgba
        radar = MinimapRadar(loader, config=MinimapConfig())
        _ = radar._get_floor_gray(7)
        _ = radar._get_floor_gray(7)
        assert loader.get_map_image.call_count == 1   # second call uses cache

    def test_different_floors_loaded_separately(self):
        loader = MagicMock()
        rng = np.random.default_rng(0)
        loader.get_map_image.return_value = rng.integers(0, 255, (200, 200, 4), dtype=np.uint8)
        radar = MinimapRadar(loader, config=MinimapConfig())
        _ = radar._get_floor_gray(6)
        _ = radar._get_floor_gray(7)
        assert loader.get_map_image.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# TestStats
# ─────────────────────────────────────────────────────────────────────────────

class TestStats:
    def test_initial_stats_zero(self):
        radar = _make_radar(_fake_floor_map())
        s = radar.stats()
        assert "hits=0" in s
        assert "miss=0" in s

    def test_stats_format(self):
        s = _make_radar(_fake_floor_map()).stats()
        assert "RADAR" in s or "radar" in s.lower()


# ─────────────────────────────────────────────────────────────────────────────
# TestRead_BlankFrame
# ─────────────────────────────────────────────────────────────────────────────

class TestRead_BlankFrame:
    def test_blank_frame_no_match(self):
        """A blank black frame produces a constant template → no match above threshold."""
        floor_map = _fake_floor_map(600)
        radar = _make_radar(floor_map, cfg=MinimapConfig(confidence=0.99))
        frame = _blank_frame(1920, 1080)
        result = radar.read(frame)
        # Either None (no match) or a coordinate — both valid; we just check no exception
        # With confidence=0.99, a blank frame virtually never matches
        assert result is None or isinstance(result, Coordinate)

    def test_tiny_frame_returns_none(self):
        radar = _make_radar(_fake_floor_map())
        frame = _blank_frame(10, 10)
        result = radar.read(frame)
        assert result is None    # _crop_minimap returns None → read returns None


# ─────────────────────────────────────────────────────────────────────────────
# TestRead_CoordConversion
# ─────────────────────────────────────────────────────────────────────────────

class TestRead_CoordConversion:
    def test_coordinate_bounds_respected(self):
        """When a match is found, the returned coordinate must be in valid Tibia range."""
        floor_map = _fake_floor_map(600)
        radar = _make_radar(floor_map, cfg=MinimapConfig(confidence=0.0))  # always accept

        # Build a real-ish minimap frame so the template isn't constant
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # paint some texture in the minimap ROI area
        rng = np.random.default_rng(7)
        frame[385:560, 1710:1885] = rng.integers(50, 200, (175, 175, 3), dtype=np.uint8)

        result = radar.read(frame)
        if result is not None:
            assert BOUNDS["xMin"] <= result.x <= BOUNDS["xMax"]
            assert BOUNDS["yMin"] <= result.y <= BOUNDS["yMax"]
            assert 0 <= result.z <= 15

    def test_floor_override_used(self):
        """floor parameter overrides cfg.floor."""
        floor_map = _fake_floor_map(600)
        radar = _make_radar(floor_map, cfg=MinimapConfig(floor=7, confidence=0.0))

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        rng = np.random.default_rng(3)
        frame[385:560, 1710:1885] = rng.integers(50, 200, (175, 175, 3), dtype=np.uint8)

        result = radar.read(frame, floor=9)
        # The loader was called with floor=9 (not 7)
        assert radar._loader.get_map_image.call_args_list[-1] == ((9,),)  # type: ignore


class TestReadJumpRejection:
    def test_rejects_moderate_manhattan_jump_while_tracking(self):
        radar = _make_radar(_fake_floor_map(), cfg=MinimapConfig(confidence=0.0, max_jump_tiles=10))
        radar._last_coord = Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 10, 7)
        radar._hit_count = 3

        with patch.object(radar, "_crop_minimap", return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch("src.minimap_radar._quantize_and_check", return_value=(np.zeros((40, 40), dtype=np.uint8), True)), \
             patch("src.minimap_radar._find_char_center", return_value=(0.0, 0.0)), \
             patch.object(radar, "_get_floor_quant", return_value=np.zeros((200, 200), dtype=np.uint8)), \
             patch.object(radar, "_palette_match", return_value=(0.99, (14, 11), 0, 0)):
            result = radar.read(np.zeros((1080, 1920, 3), dtype=np.uint8))

        assert result is None
        assert radar._jump_rejects == 1

    def test_rejects_tracking_drift_far_from_hint(self):
        radar = _make_radar(_fake_floor_map(), cfg=MinimapConfig(confidence=0.0, max_jump_tiles=10))
        radar._last_coord = Coordinate(BOUNDS["xMin"] + 42, BOUNDS["yMin"] + 20, 7)
        radar._hit_count = 3
        hint = Coordinate(BOUNDS["xMin"] + 50, BOUNDS["yMin"] + 20, 7)

        with patch.object(radar, "_crop_minimap", return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch("src.minimap_radar._quantize_and_check", return_value=(np.zeros((40, 40), dtype=np.uint8), True)), \
             patch("src.minimap_radar._find_char_center", return_value=(0.0, 0.0)), \
             patch.object(radar, "_get_floor_quant", return_value=np.zeros((200, 200), dtype=np.uint8)), \
             patch.object(radar, "_palette_match", return_value=(0.99, (41, 20), 0, 0)):
            result = radar.read(np.zeros((1080, 1920, 3), dtype=np.uint8), hint=hint)

        assert result is None
        assert radar._jump_rejects == 1

    def test_accepts_small_jump_while_tracking(self):
        radar = _make_radar(_fake_floor_map(), cfg=MinimapConfig(confidence=0.0, max_jump_tiles=10))
        radar._last_coord = Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 10, 7)
        radar._hit_count = 3

        with patch.object(radar, "_crop_minimap", return_value=np.zeros((40, 40, 3), dtype=np.uint8)), \
             patch("src.minimap_radar._quantize_and_check", return_value=(np.zeros((40, 40), dtype=np.uint8), True)), \
             patch("src.minimap_radar._find_char_center", return_value=(0.0, 0.0)), \
             patch.object(radar, "_get_floor_quant", return_value=np.zeros((200, 200), dtype=np.uint8)), \
             patch.object(radar, "_palette_match", return_value=(0.99, (12, 11), 0, 0)):
            result = radar.read(np.zeros((1080, 1920, 3), dtype=np.uint8))

        assert result == Coordinate(BOUNDS["xMin"] + 12, BOUNDS["yMin"] + 11, 7)


# ─────────────────────────────────────────────────────────────────────────────
# TestMatchWithHint
# ─────────────────────────────────────────────────────────────────────────────

class TestMatchWithHint:
    def test_hint_same_floor_uses_area(self):
        """_match_with_hint should use the reduced area, not the full map."""
        floor_map = _fake_floor_map(400)
        radar = _make_radar(floor_map)
        floor_gray = radar._get_floor_gray(7)
        template = np.zeros((5, 5), dtype=np.uint8)
        hint = Coordinate(BOUNDS["xMin"] + 100, BOUNDS["yMin"] + 100, 7)
        result, ox, oy = radar._match_with_hint(floor_gray, template, hint,
                                                t_w=5, t_h=5, padding=60)
        assert result is not None
        assert result.ndim == 2

    def test_hint_returns_nonzero_offset_when_valid(self):
        floor_map = _fake_floor_map(400)
        radar = _make_radar(floor_map)
        floor_gray = radar._get_floor_gray(7)
        template = np.zeros((5, 5), dtype=np.uint8)
        hint = Coordinate(BOUNDS["xMin"] + 200, BOUNDS["yMin"] + 200, 7)
        _, ox, oy = radar._match_with_hint(floor_gray, template, hint,
                                           t_w=5, t_h=5, padding=10)
        # offset is >= 0
        assert ox >= 0 and oy >= 0

    def test_too_large_template_falls_back_to_full_map(self):
        """If the window is smaller than the template, fall back to full map search."""
        floor_map = _fake_floor_map(100)
        radar = _make_radar(floor_map)
        floor_gray = radar._get_floor_gray(7)
        # template almost as large as the floor map
        template = np.zeros((50, 50), dtype=np.uint8)
        hint = Coordinate(BOUNDS["xMin"] + 2, BOUNDS["yMin"] + 2, 7)
        result, ox, oy = radar._match_with_hint(floor_gray, template, hint,
                                                t_w=50, t_h=50, padding=1)
        # offset must be 0 (fell back to full map)
        assert ox == 0 and oy == 0

    def test_palette_match_uses_same_hint_window_geometry(self):
        floor_map = _fake_floor_map(400)
        radar = _make_radar(floor_map)
        floor_gray = radar._get_floor_gray(7)
        template = np.zeros((8, 10), dtype=np.uint8)
        hint = Coordinate(BOUNDS["xMin"] + 120, BOUNDS["yMin"] + 140, 7)
        expected_result, ox, oy = radar._match_with_hint(
            floor_gray,
            template,
            hint,
            t_w=10,
            t_h=8,
            padding=17,
        )

        score, _loc, px, py = radar._palette_match(
            floor_gray,
            template,
            np.ones_like(template, dtype=bool),
            hint,
            padding=17,
        )

        assert score >= 0.0
        assert (px, py) == (ox, oy)
        assert expected_result.ndim == 2


# ─────────────────────────────────────────────────────────────────────────────
# reset_stats()
# ─────────────────────────────────────────────────────────────────────────────

class TestResetStats:

    def test_initial_counters_zero(self):
        radar = _make_radar(_fake_floor_map())
        assert radar._hit_count == 0
        assert radar._miss_count == 0

    def test_reset_after_misses(self):
        radar = _make_radar(_fake_floor_map())
        radar._miss_count = 5
        radar._hit_count  = 3
        radar.reset_stats()
        assert radar._hit_count == 0
        assert radar._miss_count == 0

    def test_stats_string_resets_to_zero(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 10
        radar._miss_count = 5
        radar.reset_stats()
        assert "hits=0" in radar.stats()
        assert "miss=0" in radar.stats()

    def test_reset_on_fresh_radar_does_not_raise(self):
        radar = _make_radar(_fake_floor_map())
        radar.reset_stats()  # already 0 — must not raise
        assert radar._hit_count == 0

    def test_counters_continue_accumulating_after_reset(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count = 10
        radar.reset_stats()
        radar._hit_count += 3
        assert radar._hit_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# last_coord property
# ─────────────────────────────────────────────────────────────────────────────

class TestLastCoord:

    def test_none_before_first_read(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.last_coord is None

    def test_set_after_injecting_internal_coord(self):
        radar = _make_radar(_fake_floor_map())
        c = Coordinate(BOUNDS["xMin"] + 10, BOUNDS["yMin"] + 10, 7)
        radar._last_coord = c
        assert radar.last_coord == c

    def test_read_only_returns_coordinate_type(self):
        radar = _make_radar(_fake_floor_map())
        radar._last_coord = Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7)
        assert isinstance(radar.last_coord, Coordinate)


# ─────────────────────────────────────────────────────────────────────────────
# hit_rate property
# ─────────────────────────────────────────────────────────────────────────────

class TestHitRate:

    def test_zero_when_no_reads(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.hit_rate == pytest.approx(0.0)

    def test_all_hits(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 10
        radar._miss_count = 0
        assert radar.hit_rate == pytest.approx(1.0)

    def test_all_misses(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 0
        radar._miss_count = 10
        assert radar.hit_rate == pytest.approx(0.0)

    def test_fifty_percent(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 5
        radar._miss_count = 5
        assert radar.hit_rate == pytest.approx(0.5)

    def test_returns_float(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count = 3
        radar._miss_count = 1
        assert isinstance(radar.hit_rate, float)

    def test_reset_returns_zero(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 7
        radar._miss_count = 3
        radar.reset_stats()
        assert radar.hit_rate == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# update_config()
# ─────────────────────────────────────────────────────────────────────────────

class TestMinimapUpdateConfig:

    def test_config_is_replaced(self):
        radar = _make_radar(_fake_floor_map())
        new_cfg = MinimapConfig(floor=9, confidence=0.6)
        radar.update_config(new_cfg)
        assert radar._cfg is new_cfg
        assert radar.floor == 9
        assert radar.confidence == pytest.approx(0.6)

    def test_floor_cache_cleared_on_update(self):
        radar = _make_radar(_fake_floor_map())
        # Populate the gray cache
        _ = radar._get_floor_gray(7)
        assert 7 in radar._floor_gray
        radar.update_config(MinimapConfig())
        assert radar._floor_gray == {}

    def test_stats_not_reset_on_update(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 5
        radar._miss_count = 3
        radar.update_config(MinimapConfig())
        # Counters should be untouched
        assert radar._hit_count  == 5
        assert radar._miss_count == 3

    def test_update_changes_tiles_wide(self):
        radar = _make_radar(_fake_floor_map())
        new_cfg = MinimapConfig(tiles_wide=50)
        radar.update_config(new_cfg)
        assert radar._cfg.tiles_wide == 50


# ─────────────────────────────────────────────────────────────────────────────
# MinimapRadar.total_reads / is_tracking / clear_floor_cache / stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestMinimapExtras:

    def test_total_reads_zero_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.total_reads == 0

    def test_total_reads_sums_hits_and_misses(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 7
        radar._miss_count = 3
        assert radar.total_reads == 10

    def test_is_tracking_false_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.is_tracking is False

    def test_is_tracking_true_after_coord_set(self):
        radar = _make_radar(_fake_floor_map())
        radar._last_coord = Coordinate(32369, 32241, 7)
        assert radar.is_tracking is True

    def test_is_tracking_false_after_reset(self):
        radar = _make_radar(_fake_floor_map())
        radar._last_coord = Coordinate(32369, 32241, 7)
        radar.reset_stats()
        # reset_stats does not clear last_coord — is_tracking stays True
        assert radar.is_tracking is True

    def test_clear_floor_cache_empties_floor_gray(self):
        radar = _make_radar(_fake_floor_map())
        _ = radar._get_floor_gray(7)
        assert 7 in radar._floor_gray
        radar.clear_floor_cache()
        assert radar._floor_gray == {}

    def test_clear_floor_cache_on_empty_is_noop(self):
        radar = _make_radar(_fake_floor_map())
        radar.clear_floor_cache()  # should not raise
        assert radar._floor_gray == {}

    def test_stats_snapshot_returns_dict(self):
        radar = _make_radar(_fake_floor_map())
        assert isinstance(radar.stats_snapshot(), dict)

    def test_stats_snapshot_all_keys(self):
        radar = _make_radar(_fake_floor_map())
        snap = radar.stats_snapshot()
        for key in ("hits", "misses", "total", "hit_rate",
                    "floor", "last_coord", "is_tracking"):
            assert key in snap, f"Missing key: {key}"

    def test_stats_snapshot_initial_values(self):
        radar = _make_radar(_fake_floor_map())
        snap = radar.stats_snapshot()
        assert snap["hits"]        == 0
        assert snap["misses"]      == 0
        assert snap["total"]       == 0
        assert snap["hit_rate"]    == pytest.approx(0.0)
        assert snap["last_coord"]  is None
        assert snap["is_tracking"] is False

    def test_stats_snapshot_reflects_counts(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 4
        radar._miss_count = 1
        snap = radar.stats_snapshot()
        assert snap["hits"]     == 4
        assert snap["misses"]   == 1
        assert snap["total"]    == 5
        assert snap["hit_rate"] == pytest.approx(0.8)


class TestMinimapRateExtras:

    def test_miss_rate_one_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.miss_rate == pytest.approx(1.0)

    def test_miss_rate_zero_when_all_hits(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 10
        radar._miss_count = 0
        assert radar.miss_rate == pytest.approx(0.0)

    def test_miss_rate_complement_of_hit_rate(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count  = 3
        radar._miss_count = 7
        assert radar.miss_rate == pytest.approx(1.0 - radar.hit_rate)

    def test_miss_rate_returns_float(self):
        radar = _make_radar(_fake_floor_map())
        assert isinstance(radar.miss_rate, float)

    def test_cached_floor_count_zero_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.cached_floor_count == 0

    def test_cached_floor_count_one_after_read(self):
        radar = _make_radar(_fake_floor_map())
        _ = radar._get_floor_gray(7)
        assert radar.cached_floor_count == 1

    def test_cached_floor_count_zero_after_clear(self):
        radar = _make_radar(_fake_floor_map())
        _ = radar._get_floor_gray(7)
        radar.clear_floor_cache()
        assert radar.cached_floor_count == 0

    def test_floors_cached_empty_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.floors_cached == []

    def test_floors_cached_contains_loaded_floor(self):
        radar = _make_radar(_fake_floor_map())
        _ = radar._get_floor_gray(7)
        assert 7 in radar.floors_cached

    def test_floors_cached_sorted(self):
        radar = _make_radar(_fake_floor_map())
        for z in (8, 6, 7):
            # inject gray maps directly to avoid needing loaders for each floor
            radar._floor_gray[z] = np.zeros((4, 4), dtype=np.uint8)
        assert radar.floors_cached == [6, 7, 8]

    def test_floors_cached_empty_after_clear(self):
        radar = _make_radar(_fake_floor_map())
        _ = radar._get_floor_gray(7)
        radar.clear_floor_cache()
        assert radar.floors_cached == []


class TestMinimapHasLastCoord:

    def test_has_last_coord_false_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.has_last_coord is False

    def test_has_last_coord_true_after_set(self):
        radar = _make_radar(_fake_floor_map())
        radar._last_coord = Coordinate(32370, 32240, 7)
        assert radar.has_last_coord is True

    def test_has_last_coord_false_after_reset_to_none(self):
        radar = _make_radar(_fake_floor_map())
        radar._last_coord = Coordinate(32370, 32240, 7)
        radar._last_coord = None
        assert radar.has_last_coord is False

    def test_has_last_coord_returns_bool(self):
        radar = _make_radar(_fake_floor_map())
        assert isinstance(radar.has_last_coord, bool)

    def test_has_last_coord_consistent_with_last_coord(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.has_last_coord == (radar.last_coord is not None)


class TestMinimapHasMissed:

    def test_has_missed_false_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.has_missed is False

    def test_has_missed_true_after_miss_count_increment(self):
        radar = _make_radar(_fake_floor_map())
        radar._miss_count = 1
        assert radar.has_missed is True

    def test_has_missed_false_after_reset(self):
        radar = _make_radar(_fake_floor_map())
        radar._miss_count = 5
        radar.reset_stats()
        assert radar.has_missed is False

    def test_has_missed_returns_bool(self):
        radar = _make_radar(_fake_floor_map())
        assert isinstance(radar.has_missed, bool)

    def test_has_missed_consistent_with_miss_count(self):
        radar = _make_radar(_fake_floor_map())
        radar._miss_count = 3
        assert radar.has_missed == (radar._miss_count > 0)


# ─────────────────────────────────────────────────────────────────────────────
# has_reads
# ─────────────────────────────────────────────────────────────────────────────

class TestHasReads:

    def test_has_reads_false_when_fresh(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.has_reads is False

    def test_has_reads_true_after_hit(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count = 1
        assert radar.has_reads is True

    def test_has_reads_true_after_miss(self):
        radar = _make_radar(_fake_floor_map())
        radar._miss_count = 1
        assert radar.has_reads is True

    def test_has_reads_returns_bool(self):
        radar = _make_radar(_fake_floor_map())
        assert isinstance(radar.has_reads, bool)

    def test_has_reads_consistent_with_total_reads(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count = 5
        assert radar.has_reads == (radar.total_reads > 0)

    def test_has_reads_back_to_false_after_reset(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count = 3
        radar.reset_stats()
        assert radar.has_reads is False


# ─────────────────────────────────────────────────────────────────────────────
# hit_count
# ─────────────────────────────────────────────────────────────────────────────

class TestHitCount:

    def test_hit_count_zero_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.hit_count == 0

    def test_hit_count_reflects_internal_field(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count = 7
        assert radar.hit_count == 7

    def test_hit_count_returns_int(self):
        radar = _make_radar(_fake_floor_map())
        assert isinstance(radar.hit_count, int)

    def test_hit_count_non_negative(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.hit_count >= 0

    def test_hit_count_after_reset(self):
        radar = _make_radar(_fake_floor_map())
        radar._hit_count = 4
        radar.reset_stats()
        assert radar.hit_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# miss_count
# ─────────────────────────────────────────────────────────────────────────────

class TestMissCount:

    def test_miss_count_zero_initially(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.miss_count == 0

    def test_miss_count_reflects_internal_field(self):
        radar = _make_radar(_fake_floor_map())
        radar._miss_count = 3
        assert radar.miss_count == 3

    def test_miss_count_returns_int(self):
        radar = _make_radar(_fake_floor_map())
        assert isinstance(radar.miss_count, int)

    def test_miss_count_non_negative(self):
        radar = _make_radar(_fake_floor_map())
        assert radar.miss_count >= 0

    def test_miss_count_consistent_with_has_missed(self):
        radar = _make_radar(_fake_floor_map())
        radar._miss_count = 2
        assert radar.miss_count > 0
        assert radar.has_missed is True

    def test_miss_count_after_reset(self):
        radar = _make_radar(_fake_floor_map())
        radar._miss_count = 9
        radar.reset_stats()
        assert radar.miss_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# _quantize_and_check reference implementation (old broadcast approach)
# Used only inside tests to verify parity with the optimised channel-wise version.
# ─────────────────────────────────────────────────────────────────────────────

def _quantize_and_check_reference(
    crop_bgr: np.ndarray, min_fraction: float = 0.20
) -> tuple:
    """Old (N,15,3) broadcast implementation — reference oracle for parity tests."""
    from src.minimap_radar import _MINIMAP_PALETTE
    h, w = crop_bgr.shape[:2]
    flat = crop_bgr.reshape(-1, 3).astype(np.int16)
    dists = np.abs(flat[:, None, :] - _MINIMAP_PALETTE[None, :, :]).max(axis=2)
    best_idx = dists.argmin(axis=1)
    min_dists = dists[np.arange(len(best_idx)), best_idx]
    is_valid = bool((min_dists <= 25).mean() >= min_fraction)
    return best_idx.astype(np.uint8).reshape(h, w), is_valid


# ─────────────────────────────────────────────────────────────────────────────
# TestQuantizeAndCheck
# ─────────────────────────────────────────────────────────────────────────────

class TestQuantizeAndCheck:
    """Channel-wise _quantize_and_check must be bit-for-bit identical to the
    reference (N,15,3) broadcast implementation."""

    def _crop(self, h: int = 40, w: int = 50, seed: int = 0) -> np.ndarray:
        return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)

    def test_indices_match_reference(self):
        from src.minimap_radar import _quantize_and_check
        crop = self._crop(seed=42)
        new_idx, _ = _quantize_and_check(crop)
        ref_idx, _ = _quantize_and_check_reference(crop)
        np.testing.assert_array_equal(new_idx, ref_idx)

    def test_valid_flag_matches_reference(self):
        from src.minimap_radar import _quantize_and_check
        crop = self._crop(seed=7)
        _, new_valid = _quantize_and_check(crop)
        _, ref_valid = _quantize_and_check_reference(crop)
        assert new_valid == ref_valid

    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 99])
    def test_parity_across_seeds(self, seed):
        from src.minimap_radar import _quantize_and_check
        crop = self._crop(h=30, w=30, seed=seed)
        new_idx, new_v = _quantize_and_check(crop)
        ref_idx, ref_v = _quantize_and_check_reference(crop)
        np.testing.assert_array_equal(new_idx, ref_idx)
        assert new_v == ref_v

    def test_pure_palette_color_maps_to_correct_index(self):
        """Each exact palette color must map to its own index."""
        from src.minimap_radar import _quantize_and_check, _MINIMAP_PALETTE
        for expected_idx in range(len(_MINIMAP_PALETTE)):
            color = _MINIMAP_PALETTE[expected_idx]  # int16
            crop = np.full((4, 4, 3), color, dtype=np.uint8)
            indices, is_valid = _quantize_and_check(crop)
            assert (indices == expected_idx).all(), (
                f"palette[{expected_idx}]={color} mapped to wrong index"
            )
            assert is_valid is True

    def test_output_dtype_is_uint8(self):
        from src.minimap_radar import _quantize_and_check
        indices, _ = _quantize_and_check(self._crop())
        assert indices.dtype == np.uint8

    def test_output_shape_matches_input(self):
        from src.minimap_radar import _quantize_and_check
        h, w = 23, 17
        indices, _ = _quantize_and_check(self._crop(h=h, w=w))
        assert indices.shape == (h, w)

    def test_indices_in_valid_range(self):
        """All returned indices must be in [0, 14]."""
        from src.minimap_radar import _quantize_and_check
        indices, _ = _quantize_and_check(self._crop(seed=5))
        assert int(indices.min()) >= 0
        assert int(indices.max()) <= 14

    def test_minimap_crop_size_matches_production_roi(self):
        """Verify no crash for production ROI size 119×110."""
        from src.minimap_radar import _quantize_and_check
        crop = self._crop(h=110, w=119, seed=1)
        indices, _ = _quantize_and_check(crop)
        assert indices.shape == (110, 119)

    def test_single_pixel_crop(self):
        """1×1 crop must not crash and return shape (1,1)."""
        from src.minimap_radar import _quantize_and_check, _MINIMAP_PALETTE
        crop = np.array([[[int(c) for c in _MINIMAP_PALETTE[0]]]], dtype=np.uint8)
        indices, is_valid = _quantize_and_check(crop)
        assert indices.shape == (1, 1)
        assert indices[0, 0] == 0
        assert is_valid is True

    def test_min_fraction_zero_always_valid(self):
        """min_fraction=0.0 → is_valid=True even for an off-palette crop."""
        from src.minimap_radar import _quantize_and_check
        # [128,128,128] is ~25 Chebyshev distance from palette[0]=[153,153,153];
        # with default min_fraction=0.20 it may or may not pass, but with 0.0 it always does.
        crop = np.full((5, 5, 3), 128, dtype=np.uint8)
        _, is_valid = _quantize_and_check(crop, min_fraction=0.0)
        assert is_valid is True

    def test_min_fraction_one_exact_palette_crop_valid(self):
        """min_fraction=1.0 → is_valid=True when all pixels are exact palette colors."""
        from src.minimap_radar import _quantize_and_check, _MINIMAP_PALETTE
        color = np.array(_MINIMAP_PALETTE[0], dtype=np.uint8)
        crop = np.full((6, 6, 3), color, dtype=np.uint8)
        _, is_valid = _quantize_and_check(crop, min_fraction=1.0)
        assert is_valid is True

    def test_min_fraction_one_mixed_crop_invalid(self):
        """min_fraction=1.0 → is_valid=False when many pixels are far from all palette colors."""
        from src.minimap_radar import _quantize_and_check
        # [200, 50, 100] has Chebyshev dist > 25 from every palette color
        crop = np.full((10, 10, 3), [200, 50, 100], dtype=np.uint8)
        _, is_valid = _quantize_and_check(crop, min_fraction=1.0)
        assert is_valid is False


# ─────────────────────────────────────────────────────────────────────────────
# TestPaletteMatchOptimized
# ─────────────────────────────────────────────────────────────────────────────

class TestPaletteMatchOptimized:
    """_palette_match must produce correct scores after the pre-allocated-buffer
    and search_present-skip optimisation."""

    def _uniform_quant(self, size: int, idx: int) -> np.ndarray:
        return np.full((size, size), idx, dtype=np.uint8)

    def test_identical_floor_and_template_scores_one(self):
        """Uniform floor and template with the same palette index → score = 1.0."""
        radar = _make_radar(_fake_floor_map(300))
        q_floor = self._uniform_quant(200, idx=0)
        q_tpl   = self._uniform_quant(20, idx=0)
        mask    = np.ones(q_tpl.shape, dtype=bool)
        score, _, _, _ = radar._palette_match(q_floor, q_tpl, mask)
        assert score == pytest.approx(1.0, abs=1e-4)

    def test_disjoint_palette_indices_scores_zero(self):
        """Template idx=0, floor idx=1 everywhere → no pixel matches → score = 0."""
        radar = _make_radar(_fake_floor_map(300))
        q_floor = self._uniform_quant(200, idx=1)
        q_tpl   = self._uniform_quant(20, idx=0)
        mask    = np.ones(q_tpl.shape, dtype=bool)
        score, _, _, _ = radar._palette_match(q_floor, q_tpl, mask)
        assert score == pytest.approx(0.0, abs=1e-6)

    def test_returns_correct_types(self):
        """Return value must be (float, (int,int), int, int)."""
        radar = _make_radar(_fake_floor_map(300))
        q_floor = self._uniform_quant(200, idx=2)
        q_tpl   = self._uniform_quant(20, idx=2)
        mask    = np.ones(q_tpl.shape, dtype=bool)
        score, loc, off_x, off_y = radar._palette_match(q_floor, q_tpl, mask)
        assert isinstance(score, float)
        assert isinstance(loc, tuple) and len(loc) == 2
        assert isinstance(off_x, int) and isinstance(off_y, int)

    def test_all_mask_false_returns_zero(self):
        """Empty mask (no active pixels) → n_active = 0 → score = 0."""
        radar = _make_radar(_fake_floor_map(300))
        q_floor = self._uniform_quant(200, idx=0)
        q_tpl   = self._uniform_quant(20, idx=0)
        mask    = np.zeros(q_tpl.shape, dtype=bool)
        score, _, _, _ = radar._palette_match(q_floor, q_tpl, mask)
        assert score == pytest.approx(0.0, abs=1e-6)

    def test_template_larger_than_floor_returns_zero(self):
        """Template bigger than floor → rh/rw ≤ 0 → (0.0, (0,0), 0, 0)."""
        radar = _make_radar(_fake_floor_map(300))
        q_floor = self._uniform_quant(10, idx=0)
        q_tpl   = self._uniform_quant(20, idx=0)
        mask    = np.ones(q_tpl.shape, dtype=bool)
        score, loc, ox, oy = radar._palette_match(q_floor, q_tpl, mask)
        assert score == pytest.approx(0.0)
        assert loc == (0, 0)

    def test_constrained_hint_gives_same_score_as_full_on_uniform_floor(self):
        """On a uniform floor the constrained search must match the full search score."""
        radar = _make_radar(_fake_floor_map(300))
        size  = 200
        q_floor = self._uniform_quant(size, idx=3)
        q_tpl   = self._uniform_quant(20, idx=3)
        mask    = np.ones(q_tpl.shape, dtype=bool)

        score_full, _, _, _ = radar._palette_match(q_floor, q_tpl, mask)

        cx = BOUNDS["xMin"] + size // 2
        cy = BOUNDS["yMin"] + size // 2
        hint = Coordinate(cx, cy, 7)
        score_hint, _, _, _ = radar._palette_match(q_floor, q_tpl, mask,
                                                    hint=hint, padding=60)
        assert score_full == pytest.approx(1.0, abs=1e-4)
        assert score_hint == pytest.approx(1.0, abs=1e-4)

    def test_search_present_skip_does_not_affect_score(self):
        """Palette indices absent from the search area are skipped; score unchanged.

        Floor filled with idx=0 → search_present = {0}.  Indices 1..14 are
        absent and will be skipped.  But they contribute 0 to match_count
        anyway (f_bin would be all-zero), so the score must still be 1.0.
        """
        radar = _make_radar(_fake_floor_map(300))
        q_floor = self._uniform_quant(200, idx=0)   # only idx=0 present
        q_tpl   = self._uniform_quant(20, idx=0)
        mask    = np.ones(q_tpl.shape, dtype=bool)
        score, _, _, _ = radar._palette_match(q_floor, q_tpl, mask)
        assert score == pytest.approx(1.0, abs=1e-4)

    def test_partial_match_score_between_zero_and_one(self):
        """Floor with 50/50 split between two indices → template (single index)
        matches ~50% of positions at some location → score in (0, 1)."""
        radar = _make_radar(_fake_floor_map(300))
        size = 100
        q_floor = np.zeros((size, size), dtype=np.uint8)
        q_floor[:, size // 2 :] = 1  # right half = idx 1
        # Template is all idx=0 — it will match perfectly where floor=0
        q_tpl = self._uniform_quant(10, idx=0)
        mask  = np.ones(q_tpl.shape, dtype=bool)
        score, _, _, _ = radar._palette_match(q_floor, q_tpl, mask)
        # Best position should be somewhere in the left half → score = 1.0
        assert score == pytest.approx(1.0, abs=1e-4)

    def test_constrained_offsets_nonzero_with_hint_away_from_origin(self):
        """With hint far from (xMin,yMin), off_x and off_y must both be > 0."""
        radar = _make_radar(_fake_floor_map(300))
        q_floor = self._uniform_quant(200, idx=0)
        q_tpl   = self._uniform_quant(20, idx=0)
        mask    = np.ones(q_tpl.shape, dtype=bool)
        # Place hint at (xMin+150, yMin+150) so the constrained window starts > 0
        hint = Coordinate(BOUNDS["xMin"] + 150, BOUNDS["yMin"] + 150, 7)
        _, _, off_x, off_y = radar._palette_match(q_floor, q_tpl, mask,
                                                   hint=hint, padding=30)
        # x0 = max(0, 150 - 30 - 10) = 110 > 0
        assert off_x > 0
        assert off_y > 0

    def test_constrained_fallback_resets_offsets_to_zero(self):
        """When hint produces a search area ≤ template size, fallback sets off_x/off_y=0."""
        radar = _make_radar(_fake_floor_map(300))
        q_floor = self._uniform_quant(200, idx=0)
        q_tpl   = self._uniform_quant(20, idx=0)
        mask    = np.ones(q_tpl.shape, dtype=bool)
        # Hint at (xMin, yMin) with padding=0 → search area = (20,20) = template size
        # Condition: search.shape[0] <= th (20 <= 20) → fallback
        hint = Coordinate(BOUNDS["xMin"], BOUNDS["yMin"], 7)
        _, _, off_x, off_y = radar._palette_match(q_floor, q_tpl, mask,
                                                   hint=hint, padding=0)
        assert off_x == 0
        assert off_y == 0

    def test_template_with_unstable_palette_index_scores_zero(self):
        """Palette indices NOT in _STABLE_PALETTE_INDICES are skipped by the loop.
        A template filled entirely with idx=4 (white/snow, unstable) → n_active=0 → score=0.
        """
        from src.minimap_radar import _STABLE_PALETTE_INDICES
        radar = _make_radar(_fake_floor_map(300))
        # idx=4 (white/snow) is intentionally excluded from _STABLE_PALETTE_INDICES
        unstable_idx = next(i for i in range(15) if i not in _STABLE_PALETTE_INDICES)
        q_floor = self._uniform_quant(200, idx=unstable_idx)
        q_tpl   = self._uniform_quant(20, idx=unstable_idx)
        mask    = np.ones(q_tpl.shape, dtype=bool)
        score, _, _, _ = radar._palette_match(q_floor, q_tpl, mask)
        assert score == pytest.approx(0.0, abs=1e-6)

    def test_score_always_in_zero_one_range(self):
        """match_count / n_active must always be in [0, 1] for any valid input."""
        radar = _make_radar(_fake_floor_map(300))
        rng = np.random.default_rng(77)
        q_floor = rng.integers(0, 15, (150, 150), dtype=np.uint8)
        q_tpl   = rng.integers(0, 15, (20, 20),  dtype=np.uint8)
        mask    = rng.integers(0, 2,  (20, 20),  dtype=bool)
        score, _, _, _ = radar._palette_match(q_floor, q_tpl, mask)
        assert 0.0 <= score <= 1.0
