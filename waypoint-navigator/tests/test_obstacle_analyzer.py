"""Tests for ObstacleAnalyzer — runtime obstacle detection from minimap colours."""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from src.models import Coordinate, BOUNDS
from src.obstacle_analyzer import (
    ObstacleAnalyzer,
    AnalysisResult,
    TileInfo,
    _PAL_COLORS,
    _PAL_WALKABLE,
    _COLOR_TOLERANCE,
)

# ── helpers ──────────────────────────────────────────────────────────────────

_X_MIN = BOUNDS["xMin"]
_Y_MIN = BOUNDS["yMin"]


def _fake_loader(walk_arr: np.ndarray | None = None) -> MagicMock:
    """Return a mock TibiaMapLoader with a configurable walkability array."""
    loader = MagicMock()
    if walk_arr is None:
        walk_arr = np.ones((100, 100), dtype=bool)
    loader.get_walkability.return_value = walk_arr
    return loader


def _make_frame(w: int = 1920, h: int = 1080, color: tuple[int, int, int] = (0, 0, 0)) -> np.ndarray:
    """Create a BGR frame of the given size filled with *color*."""
    frame = np.full((h, w, 3), color, dtype=np.uint8)
    return frame


def _paint_minimap(frame: np.ndarray, color: tuple[int, int, int],
                   roi: list[int] | None = None) -> None:
    """Paint the minimap region of *frame* with a solid colour."""
    if roi is None:
        roi = [1710, 37, 175, 175]
    x, y, w, h = roi
    frame[y:y + h, x:x + w] = color


# ── TileInfo ─────────────────────────────────────────────────────────────────

class TestTileInfo:
    def test_fields(self) -> None:
        ti = TileInfo(x=100, y=200, z=7, live_walkable=True,
                      static_walkable=False, discrepancy=True)
        assert ti.x == 100
        assert ti.discrepancy is True

    def test_default_color(self) -> None:
        ti = TileInfo(x=0, y=0, z=0, live_walkable=False,
                      static_walkable=False, discrepancy=False)
        assert ti.color_bgr == (0, 0, 0)


# ── AnalysisResult ───────────────────────────────────────────────────────────

class TestAnalysisResult:
    def test_empty(self) -> None:
        r = AnalysisResult(center=None)
        assert r.tiles == []
        assert r.discrepancies == []
        assert r.blocked_tiles == []
        assert r.open_tiles == []
        assert r.tile_count == 0

    def test_with_center(self) -> None:
        c = Coordinate(_X_MIN + 50, _Y_MIN + 50, 7)
        r = AnalysisResult(center=c)
        assert r.center is c


# ── Constructor ──────────────────────────────────────────────────────────────

class TestObstacleAnalyzerInit:
    def test_defaults(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader)
        assert oa._tiles_wide == 90
        assert oa._roi == [1710, 37, 175, 175]

    def test_custom_params(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader, tiles_wide=60, roi=[100, 100, 200, 200])
        assert oa._tiles_wide == 60
        assert oa._roi == [100, 100, 200, 200]


# ── _crop_minimap ────────────────────────────────────────────────────────────

class TestCropMinimap:
    def test_returns_crop(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader, roi=[100, 50, 80, 80])
        frame = _make_frame(1920, 1080)
        _paint_minimap(frame, (153, 153, 153), roi=[100, 50, 80, 80])
        crop = oa._crop_minimap(frame)
        assert crop is not None
        assert crop.shape[0] > 0 and crop.shape[1] > 0

    def test_small_frame_returns_none(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader)
        # Tiny frame where ROI would be microscopic
        frame = _make_frame(10, 10)
        crop = oa._crop_minimap(frame)
        assert crop is None

    def test_scaled_resolution(self) -> None:
        """A larger frame (e.g. 3840x2160) should scale the ROI."""
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader, roi=[1710, 37, 175, 175])
        frame = _make_frame(3840, 2160)
        crop = oa._crop_minimap(frame)
        assert crop is not None
        # Should be roughly 2× the base ROI size
        assert crop.shape[1] >= 300


# ── _classify_walkable ───────────────────────────────────────────────────────

class TestClassifyWalkable:
    def test_grey_is_walkable(self) -> None:
        tile_img = np.full((5, 5, 3), (153, 153, 153), dtype=np.uint8)
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert result.shape == (5, 5)
        assert result.all()  # all walkable

    def test_black_is_non_walkable(self) -> None:
        tile_img = np.zeros((5, 5, 3), dtype=np.uint8)
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert not result.any()  # all non-walkable

    def test_wall_is_non_walkable(self) -> None:
        # Blue/red in BGR = (0, 51, 255) → building/wall
        tile_img = np.full((3, 3, 3), (0, 51, 255), dtype=np.uint8)
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert not result.any()

    def test_brown_is_walkable(self) -> None:
        # Brown dirt in BGR = (51, 102, 153) → RGB (153,102,51), walkable
        tile_img = np.full((3, 3, 3), (51, 102, 153), dtype=np.uint8)
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert result.all()

    def test_mixed_colours(self) -> None:
        """A 2×1 image: one grey tile (walkable) and one black tile (not)."""
        tile_img = np.zeros((1, 2, 3), dtype=np.uint8)
        tile_img[0, 0] = (153, 153, 153)  # walkable
        tile_img[0, 1] = (0, 0, 0)        # non-walkable
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert result[0, 0] is np.True_
        assert result[0, 1] is np.False_

    def test_unknown_colour_non_walkable(self) -> None:
        """Colour far from any palette entry → non-walkable."""
        tile_img = np.full((2, 2, 3), (128, 0, 128), dtype=np.uint8)
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert not result.any()


# ── _resize_to_tiles ─────────────────────────────────────────────────────────

class TestResizeToTiles:
    def test_output_shape(self) -> None:
        crop = np.zeros((175, 175, 3), dtype=np.uint8)
        tile_img = ObstacleAnalyzer._resize_to_tiles(crop, 20, 20)
        assert tile_img.shape == (20, 20, 3)

    def test_single_colour_preserved(self) -> None:
        crop = np.full((100, 100, 3), (153, 153, 153), dtype=np.uint8)
        tile_img = ObstacleAnalyzer._resize_to_tiles(crop, 10, 10)
        # After INTER_AREA resize, the colour should be preserved
        assert np.allclose(tile_img, 153, atol=5)


# ── analyze() ────────────────────────────────────────────────────────────────

class TestAnalyze:
    def test_no_center_returns_minimal_result(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader, roi=[100, 50, 80, 80])
        frame = _make_frame()
        result = oa.analyze(frame, center=None)
        assert result.center is None
        assert result.tiles == []

    def test_all_walkable_no_discrepancies(self) -> None:
        """All-grey minimap + all-True walkability → 0 discrepancies."""
        walk = np.ones((100, 100), dtype=bool)
        loader = _fake_loader(walk)
        roi = [100, 50, 80, 80]
        oa = ObstacleAnalyzer(loader, tiles_wide=10, roi=roi)

        frame = _make_frame()
        _paint_minimap(frame, (153, 153, 153), roi=roi)

        center = Coordinate(_X_MIN + 50, _Y_MIN + 50, 7)
        result = oa.analyze(frame, center=center, floor=7)
        assert result.tile_count > 0
        assert result.discrepancy_count == 0

    def test_detects_blocked_tile(self) -> None:
        """Static says walkable, but minimap shows wall → blocked_tiles."""
        walk = np.ones((200, 200), dtype=bool)  # all walkable in static
        loader = _fake_loader(walk)
        roi = [100, 50, 80, 80]
        # Use a small tiles_wide so each tile maps to a big chunk of the ROI
        oa = ObstacleAnalyzer(loader, tiles_wide=5, roi=roi)

        # Fill minimap with wall colour (non-walkable)
        frame = _make_frame()
        _paint_minimap(frame, (0, 51, 255), roi=roi)

        center = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        result = oa.analyze(frame, center=center, floor=7)

        # All tiles should be discrepancies (static=True, live=False)
        assert result.discrepancy_count > 0
        assert len(result.blocked_tiles) > 0
        assert all(not t.live_walkable for t in result.blocked_tiles)
        assert all(t.static_walkable for t in result.blocked_tiles)

    def test_detects_open_tile(self) -> None:
        """Static says non-walkable, but minimap shows grey → open_tiles."""
        walk = np.zeros((200, 200), dtype=bool)  # all non-walkable in static
        loader = _fake_loader(walk)
        roi = [100, 50, 80, 80]
        oa = ObstacleAnalyzer(loader, tiles_wide=5, roi=roi)

        frame = _make_frame()
        _paint_minimap(frame, (153, 153, 153), roi=roi)  # walkable colour

        center = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        result = oa.analyze(frame, center=center, floor=7)

        assert len(result.open_tiles) > 0
        assert all(t.live_walkable for t in result.open_tiles)
        assert all(not t.static_walkable for t in result.open_tiles)

    def test_loader_exception_returns_empty(self) -> None:
        """If loader.get_walkability raises, return empty result."""
        loader = MagicMock()
        loader.get_walkability.side_effect = FileNotFoundError("no data")
        oa = ObstacleAnalyzer(loader, roi=[100, 50, 80, 80])

        frame = _make_frame()
        _paint_minimap(frame, (153, 153, 153), roi=[100, 50, 80, 80])
        center = Coordinate(_X_MIN + 50, _Y_MIN + 50, 7)
        result = oa.analyze(frame, center=center, floor=7)
        assert result.tiles == []


# ── get_blocked_coords() ────────────────────────────────────────────────────

class TestGetBlockedCoords:
    def test_returns_coordinates(self) -> None:
        walk = np.ones((200, 200), dtype=bool)
        loader = _fake_loader(walk)
        roi = [100, 50, 80, 80]
        oa = ObstacleAnalyzer(loader, tiles_wide=5, roi=roi)

        frame = _make_frame()
        _paint_minimap(frame, (0, 51, 255), roi=roi)  # wall colour

        center = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        blocked = oa.get_blocked_coords(frame, center, floor=7)
        assert len(blocked) > 0
        assert all(isinstance(c, Coordinate) for c in blocked)
        assert all(c.z == 7 for c in blocked)

    def test_no_blocked_on_matching_data(self) -> None:
        walk = np.ones((200, 200), dtype=bool)
        loader = _fake_loader(walk)
        roi = [100, 50, 80, 80]
        oa = ObstacleAnalyzer(loader, tiles_wide=5, roi=roi)

        frame = _make_frame()
        _paint_minimap(frame, (153, 153, 153), roi=roi)  # walkable

        center = Coordinate(_X_MIN + 100, _Y_MIN + 100, 7)
        blocked = oa.get_blocked_coords(frame, center, floor=7)
        assert len(blocked) == 0


# ── confirm_blocked() ───────────────────────────────────────────────────────

class TestConfirmBlocked:
    def test_threshold_default(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader)
        c = Coordinate(32000, 31500, 7)
        assert oa.confirm_blocked(c) is False  # 1st
        assert oa.confirm_blocked(c) is True   # 2nd (threshold=2)

    def test_custom_threshold(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader)
        c = Coordinate(32000, 31500, 7)
        assert oa.confirm_blocked(c, threshold=3) is False
        assert oa.confirm_blocked(c, threshold=3) is False
        assert oa.confirm_blocked(c, threshold=3) is True

    def test_different_tiles_tracked_separately(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader)
        c1 = Coordinate(32000, 31500, 7)
        c2 = Coordinate(32001, 31500, 7)
        oa.confirm_blocked(c1)
        assert oa.confirm_blocked(c2) is False  # c2 only 1st time


# ── clear_confirmed() ───────────────────────────────────────────────────────

class TestClearConfirmed:
    def test_clear_all(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader)
        c = Coordinate(32000, 31500, 7)
        oa.confirm_blocked(c)
        oa.confirm_blocked(c)
        oa.clear_confirmed()
        assert oa.confirmed_blocked_tiles == []

    def test_clear_specific(self) -> None:
        loader = _fake_loader()
        oa = ObstacleAnalyzer(loader)
        c1 = Coordinate(32000, 31500, 7)
        c2 = Coordinate(32001, 31500, 7)
        oa.confirm_blocked(c1)
        oa.confirm_blocked(c2)
        oa.clear_confirmed(c1)
        # c2 should still be present
        assert (c2.x, c2.y, c2.z) in oa.confirmed_blocked_tiles
        assert (c1.x, c1.y, c1.z) not in oa.confirmed_blocked_tiles


# ── confirmed_blocked_tiles property ────────────────────────────────────────

class TestConfirmedBlockedProperty:
    def test_empty_initially(self) -> None:
        oa = ObstacleAnalyzer(_fake_loader())
        assert oa.confirmed_blocked_tiles == []

    def test_lists_all(self) -> None:
        oa = ObstacleAnalyzer(_fake_loader())
        oa.confirm_blocked(Coordinate(32000, 31500, 7))
        oa.confirm_blocked(Coordinate(32001, 31501, 7))
        assert len(oa.confirmed_blocked_tiles) == 2


# ── Palette constants ───────────────────────────────────────────────────────

class TestPaletteConstants:
    def test_pal_colors_shape(self) -> None:
        assert _PAL_COLORS.ndim == 2
        assert _PAL_COLORS.shape[1] == 3

    def test_pal_walkable_length(self) -> None:
        assert len(_PAL_WALKABLE) == len(_PAL_COLORS)

    def test_tolerance_positive(self) -> None:
        assert _COLOR_TOLERANCE > 0
