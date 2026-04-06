"""Tests for ObstacleAnalyzer and WalkabilityOverlay."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
from src.walkability_overlay import (
    WalkabilityOverlay,
    OverlayState,
    _DIR_MAP,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_loader(floor: int = 7, arr_shape: tuple[int, int] = (1792, 2304)) -> MagicMock:
    """Create a mock TibiaMapLoader with a walkability array."""
    loader = MagicMock()
    walkability = np.ones(arr_shape, dtype=bool)
    loader.get_walkability.return_value = walkability
    return loader


def _make_frame(h: int = 1080, w: int = 1920) -> np.ndarray:
    """Create a dummy BGR frame."""
    return np.full((h, w, 3), 153, dtype=np.uint8)  # grey = walkable


def _walkable_grey_frame(h: int = 1080, w: int = 1920) -> np.ndarray:
    """Frame where the minimap ROI is all grey (153,153,153) = walkable floor."""
    return np.full((h, w, 3), 153, dtype=np.uint8)


def _non_walkable_frame(h: int = 1080, w: int = 1920) -> np.ndarray:
    """Frame where the minimap ROI is all red (0,51,255 BGR) = building/wall."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :, 0] = 0
    frame[:, :, 1] = 51
    frame[:, :, 2] = 255
    return frame


# ── ObstacleAnalyzer ─────────────────────────────────────────────────────────

class TestObstacleAnalyzerInit:
    def test_default_roi(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader)
        assert oa._roi == [1710, 37, 175, 175]
        assert oa._tiles_wide == 90

    def test_custom_roi(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader, tiles_wide=50, roi=[100, 100, 200, 200])
        assert oa._roi == [100, 100, 200, 200]
        assert oa._tiles_wide == 50


class TestObstacleAnalyzerCrop:
    def test_crop_returns_correct_region(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader, roi=[0, 0, 100, 100])
        frame = _make_frame()
        crop = oa._crop_minimap(frame)
        assert crop is not None
        assert crop.shape[0] > 0
        assert crop.shape[1] > 0

    def test_crop_returns_none_for_tiny_roi(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader, roi=[0, 0, 5, 5])
        frame = _make_frame()
        crop = oa._crop_minimap(frame)
        assert crop is None

    def test_crop_scales_with_resolution(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader, roi=[0, 0, 100, 100])
        frame_hd = _make_frame(1080, 1920)
        frame_4k = _make_frame(2160, 3840)
        crop_hd = oa._crop_minimap(frame_hd)
        crop_4k = oa._crop_minimap(frame_4k)
        assert crop_hd is not None
        assert crop_4k is not None
        # 4K crop should be ~2x the size of HD
        assert crop_4k.shape[0] > crop_hd.shape[0]


class TestClassifyWalkable:
    def test_grey_is_walkable(self) -> None:
        """Grey (153,153,153) minimap pixels → walkable."""
        tile_img = np.full((5, 5, 3), 153, dtype=np.uint8)
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert result.shape == (5, 5)
        assert result.all()

    def test_red_is_non_walkable(self) -> None:
        """Red/building (BGR 0,51,255) → non-walkable."""
        tile_img = np.zeros((5, 5, 3), dtype=np.uint8)
        tile_img[:, :, 0] = 0
        tile_img[:, :, 1] = 51
        tile_img[:, :, 2] = 255
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert not result.any()

    def test_green_is_walkable(self) -> None:
        """Grass (BGR 0,204,0) → walkable (97.6 % in path data)."""
        tile_img = np.zeros((5, 5, 3), dtype=np.uint8)
        tile_img[:, :, 1] = 204
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert result.all()

    def test_brown_is_walkable(self) -> None:
        """Brown/dirt (BGR 51,102,153) → walkable."""
        tile_img = np.zeros((5, 5, 3), dtype=np.uint8)
        tile_img[:, :, 0] = 51
        tile_img[:, :, 1] = 102
        tile_img[:, :, 2] = 153
        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert result.all()

    def test_mixed_tiles(self) -> None:
        """Mix of walkable and non-walkable."""
        tile_img = np.zeros((2, 2, 3), dtype=np.uint8)
        # Top-left: grey walkable
        tile_img[0, 0] = [153, 153, 153]
        # Top-right: red wall
        tile_img[0, 1] = [0, 51, 255]
        # Bottom-left: brown dirt walkable (BGR)
        tile_img[1, 0] = [51, 102, 153]
        # Bottom-right: black non-walkable
        tile_img[1, 1] = [0, 0, 0]

        result = ObstacleAnalyzer._classify_walkable(tile_img)
        assert result[0, 0]       # grey walkable
        assert not result[0, 1]   # red wall
        assert result[1, 0]       # brown walkable
        assert not result[1, 1]   # black


class TestAnalyze:
    def test_analysis_without_center(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader, roi=[0, 0, 200, 200])
        frame = _walkable_grey_frame()
        result = oa.analyze(frame, center=None)
        assert isinstance(result, AnalysisResult)
        assert result.center is None
        assert result.tiles == []

    def test_analysis_with_center_all_walkable(self) -> None:
        """All grey frame + all-walkable static → no discrepancies."""
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader, roi=[0, 0, 200, 200], tiles_wide=10)
        frame = _walkable_grey_frame()
        center = Coordinate(32369, 32241, 7)
        result = oa.analyze(frame, center=center, floor=7)
        assert result.center == center
        assert result.tile_count > 0
        assert len(result.tiles) > 0
        # All should be walkable, no discrepancies
        assert result.discrepancy_count == 0
        assert len(result.blocked_tiles) == 0

    def test_analysis_detects_blocked(self) -> None:
        """Non-walkable frame + walkable static → blocked tiles detected."""
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader, roi=[0, 0, 200, 200], tiles_wide=10)
        frame = _non_walkable_frame()
        center = Coordinate(32369, 32241, 7)
        result = oa.analyze(frame, center=center, floor=7)
        assert len(result.blocked_tiles) > 0
        assert result.discrepancy_count > 0

    def test_analysis_detects_open(self) -> None:
        """Walkable frame + non-walkable static → open tiles detected."""
        # Static walkability: all False
        loader = MagicMock()
        walkability = np.zeros((1792, 2304), dtype=bool)
        loader.get_walkability.return_value = walkability

        oa = ObstacleAnalyzer(loader, roi=[0, 0, 200, 200], tiles_wide=10)
        frame = _walkable_grey_frame()
        center = Coordinate(32369, 32241, 7)
        result = oa.analyze(frame, center=center, floor=7)
        assert len(result.open_tiles) > 0


class TestGetBlockedCoords:
    def test_returns_coordinates(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader, roi=[0, 0, 200, 200], tiles_wide=10)
        frame = _non_walkable_frame()
        center = Coordinate(32369, 32241, 7)
        blocked = oa.get_blocked_coords(frame, center, floor=7)
        assert isinstance(blocked, list)
        assert len(blocked) > 0
        assert all(isinstance(c, Coordinate) for c in blocked)


class TestConfirmBlocked:
    def test_threshold_tracking(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader)
        coord = Coordinate(32369, 32241, 7)
        assert not oa.confirm_blocked(coord, threshold=3)
        assert not oa.confirm_blocked(coord, threshold=3)
        assert oa.confirm_blocked(coord, threshold=3)

    def test_clear_single(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader)
        coord = Coordinate(32369, 32241, 7)
        oa.confirm_blocked(coord)
        oa.clear_confirmed(coord)
        assert (32369, 32241, 7) not in oa.confirmed_blocked_tiles

    def test_clear_all(self) -> None:
        loader = _make_loader()
        oa = ObstacleAnalyzer(loader)
        oa.confirm_blocked(Coordinate(1, 2, 7))
        oa.confirm_blocked(Coordinate(3, 4, 7))
        oa.clear_confirmed()
        assert len(oa.confirmed_blocked_tiles) == 0


class TestResizeToTiles:
    def test_resize_output_shape(self) -> None:
        crop = np.zeros((200, 200, 3), dtype=np.uint8)
        result = ObstacleAnalyzer._resize_to_tiles(crop, 10, 10)
        assert result.shape == (10, 10, 3)


class TestTileInfo:
    def test_dataclass_fields(self) -> None:
        ti = TileInfo(x=100, y=200, z=7,
                      live_walkable=True, static_walkable=False,
                      discrepancy=True, color_bgr=(153, 153, 153))
        assert ti.x == 100
        assert ti.discrepancy


class TestAnalysisResult:
    def test_default_empty(self) -> None:
        ar = AnalysisResult(center=None)
        assert ar.tiles == []
        assert ar.discrepancies == []
        assert ar.blocked_tiles == []
        assert ar.open_tiles == []
        assert ar.tile_count == 0


# ── WalkabilityOverlay ───────────────────────────────────────────────────────

class TestOverlayState:
    def test_defaults(self) -> None:
        s = OverlayState()
        assert s.position is None
        assert s.waypoint is None
        assert s.status == "idle"
        assert s.directions == ""
        assert s.pnf_count == 0
        assert s.blocked_tiles == []
        assert s.route_tiles == []
        assert s.floor == 7


class TestWalkabilityOverlayInit:
    def test_default_state(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        assert ov._radius == 30
        assert ov._win == "diag"
        assert not ov.running


class TestOverlayUpdate:
    def test_update_position(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        pos = Coordinate(32369, 32241, 7)
        ov.update(position=pos, status="walking")
        assert ov._state.position == pos
        assert ov._state.status == "walking"

    def test_add_direction(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        ov.add_direction(0, 1)   # south
        ov.add_direction(0, 1)
        ov.add_direction(1, 0)   # east
        assert ov._state.directions == "sse"

    def test_directions_truncated(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        for _ in range(50):
            ov.add_direction(0, 1)
        assert len(ov._state.directions) <= 40

    def test_add_blocked(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        ov.add_blocked(100, 200, 7)
        assert (100, 200, 7) in ov._state.blocked_tiles

    def test_add_blocked_no_duplicates(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        ov.add_blocked(100, 200, 7)
        ov.add_blocked(100, 200, 7)
        assert ov._state.blocked_tiles.count((100, 200, 7)) == 1

    def test_set_route(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        steps = [Coordinate(32369, 32241, 7), Coordinate(32370, 32241, 7)]
        ov.set_route(steps)
        assert len(ov._state.route_tiles) == 2
        assert ov._state.route_tiles[0] == (32369, 32241)


class TestOverlayRender:
    def test_render_returns_bgr_image(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        img = ov.render()
        assert isinstance(img, np.ndarray)
        assert img.ndim == 3
        assert img.shape[2] == 3

    def test_render_with_position(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader, view_radius=10)
        pos = Coordinate(32369, 32241, 7)
        ov.update(position=pos, floor=7)
        img = ov.render()
        assert img is not None
        # Should have some non-black pixels (walkable area)
        assert img.sum() > 0

    def test_render_with_all_state(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader, view_radius=10)
        pos = Coordinate(32369, 32241, 7)
        wp = Coordinate(32370, 32242, 7)
        ov.update(
            position=pos,
            waypoint=wp,
            initial_pos=pos,
            step_target=Coordinate(32369, 32240, 7),
            status="walking",
            directions="nns",
            pnf_count=1,
            replan_count=2,
            floor=7,
        )
        ov.add_blocked(32368, 32241, 7)
        ov.set_route([pos, wp])
        img = ov.render()
        assert img is not None

    def test_render_no_position(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        img = ov.render()
        assert img is not None

    def test_render_with_blocked_status(self) -> None:
        loader = _make_loader()
        ov = WalkabilityOverlay(loader, view_radius=5)
        ov.update(
            position=Coordinate(32369, 32241, 7),
            status="blocked",
            floor=7,
        )
        img = ov.render()
        assert img is not None


class TestOverlayLifecycle:
    def test_start_stop(self) -> None:
        """Start/stop without actually showing a window (mocked cv2)."""
        loader = _make_loader()
        ov = WalkabilityOverlay(loader)
        # We can't actually open a window in tests, but we test the
        # internal state management
        assert not ov.running
        ov._running = True
        assert ov.running
        ov._running = False
        assert not ov.running


class TestDirMap:
    def test_cardinal_directions(self) -> None:
        assert _DIR_MAP[(0, -1)] == "n"
        assert _DIR_MAP[(0, 1)] == "s"
        assert _DIR_MAP[(-1, 0)] == "w"
        assert _DIR_MAP[(1, 0)] == "e"

    def test_diagonal_directions(self) -> None:
        assert _DIR_MAP[(1, -1)] == "ne"
        assert _DIR_MAP[(-1, -1)] == "nw"
        assert _DIR_MAP[(1, 1)] == "se"
        assert _DIR_MAP[(-1, 1)] == "sw"

    def test_no_move(self) -> None:
        assert _DIR_MAP[(0, 0)] == "."


# ── Palette constants sanity checks ─────────────────────────────────────────

class TestPaletteConstants:
    def test_palette_shape(self) -> None:
        assert _PAL_COLORS.ndim == 2
        assert _PAL_COLORS.shape[1] == 3
        assert len(_PAL_WALKABLE) == len(_PAL_COLORS)

    def test_tolerance_positive(self) -> None:
        assert _COLOR_TOLERANCE > 0

    def test_walkable_entries_exist(self) -> None:
        assert _PAL_WALKABLE.any(), "at least one palette entry should be walkable"

    def test_non_walkable_entries_exist(self) -> None:
        assert (~_PAL_WALKABLE).any(), "at least one palette entry should be non-walkable"
