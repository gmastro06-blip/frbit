"""
Tests for src/ui_detection.py and R3 visual detection integration.
Fully offline: uses synthetic frames only.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.ui_detection import (
    detect_context_menu,
    detect_container_window,
    find_menu_entry_offset,
    scale_offset_x,
    scale_offset_y,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _blank(h: int = 1080, w: int = 1920) -> np.ndarray:
    """Black BGR frame."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def _frame_with_white_rect(
    h: int = 1080,
    w: int = 1920,
    x: int = 400,
    y: int = 300,
    rw: int = 120,
    rh: int = 200,
) -> np.ndarray:
    """Black frame with a white rectangle burned in."""
    f = _blank(h, w)
    f[y : y + rh, x : x + rw] = 255
    return f


def _menu_like_frame(
    h: int = 1080,
    w: int = 1920,
    mx: int = 400,
    my: int = 300,
    mw: int = 100,
    mh: int = 80,
    rows: int = 4,
) -> np.ndarray:
    """Frame with a synthetic menu: alternating dark/light rows."""
    f = _blank(h, w)
    row_h = mh // rows
    for i in range(rows):
        color = 200 if i % 2 == 0 else 80
        ry = my + i * row_h
        f[ry : ry + row_h, mx : mx + mw] = color
    return f


# ─────────────────────────────────────────────────────────────────────────────
# detect_context_menu
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectContextMenu:
    """detect_context_menu: frame-diff based menu finder."""

    def test_returns_none_when_both_frames_none(self):
        assert detect_context_menu(None, None, 100, 100) is None  # type: ignore[arg-type]

    def test_returns_none_when_before_none(self):
        f = _blank()
        assert detect_context_menu(None, f, 100, 100) is None  # type: ignore[arg-type]

    def test_returns_none_when_identical_frames(self):
        f = _blank()
        assert detect_context_menu(f, f.copy(), 500, 500) is None

    def test_detects_large_white_rect_as_menu(self):
        before = _blank()
        after = _blank()
        # Simulate a menu appearing near (500, 400)
        after[400:500, 460:560] = 255  # 100×100 white block
        result = detect_context_menu(before, after, 500, 400)
        assert result is not None
        x, y, w, h = result
        assert w > 50
        assert h > 50

    def test_ignores_tiny_change(self):
        before = _blank()
        after = _blank()
        after[400:405, 500:505] = 255  # 5×5 — too small
        assert detect_context_menu(before, after, 500, 400) is None

    def test_works_on_smaller_resolution(self):
        before = _blank(720, 1280)
        after = _blank(720, 1280)
        after[200:320, 600:720] = 255
        result = detect_context_menu(before, after, 650, 200)
        assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# find_menu_entry_offset
# ─────────────────────────────────────────────────────────────────────────────

class TestFindMenuEntryOffset:
    """find_menu_entry_offset: Sobel-based row separator finder."""

    def test_returns_none_for_empty_roi(self):
        f = _blank()
        assert find_menu_entry_offset(f, (0, 0, 0, 0), 0) is None

    def test_fallback_for_uniform_crop(self):
        """Uniform crop → no separators → fallback entry height."""
        f = _blank()
        f[300:380, 400:500] = 128  # grey block
        result = find_menu_entry_offset(f, (400, 300, 100, 80), entry_index=0)
        assert result is not None
        cx, cy = result
        assert 400 <= cx <= 500  # within menu x range
        assert 300 <= cy <= 380  # within menu y range

    def test_finds_first_entry_in_striped_menu(self):
        """Menu with alternating dark/light rows → entry centres."""
        f = _menu_like_frame(mx=400, my=300, mw=100, mh=80, rows=4)
        result = find_menu_entry_offset(f, (400, 300, 100, 80), entry_index=0)
        assert result is not None
        cx, cy = result
        assert 400 <= cx <= 500
        # Should be near the top of the menu
        assert 300 <= cy <= 340

    def test_returns_none_when_entry_index_too_large(self):
        f = _blank()
        f[300:320, 400:500] = 128  # tiny crop → only 1 fallback entry
        # entry_index=99 is absurdly large
        result = find_menu_entry_offset(f, (400, 300, 100, 20), entry_index=99)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# detect_container_window
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectContainerWindow:
    """detect_container_window: Canny edge based container finder."""

    def test_returns_none_for_none_frame(self):
        assert detect_container_window(None) is None  # type: ignore[arg-type]

    def test_returns_none_for_empty_frame(self):
        assert detect_container_window(np.empty((0, 0, 3), dtype=np.uint8)) is None

    def test_returns_none_for_blank_frame(self):
        """A pure-black frame has no edges → no container."""
        assert detect_container_window(_blank()) is None

    def test_detects_large_rect_in_right_third(self):
        """A bright rectangle in the right third of the screen is a container."""
        f = _blank()
        # Place a white rect at x=1400 (right third)
        f[200:500, 1400:1700] = 255
        result = detect_container_window(f)
        assert result is not None
        x, y, w, h = result
        assert w >= 150
        assert h >= 100

    def test_ignores_small_rect(self):
        """Rectangle smaller than min_width / min_height → ignored."""
        f = _blank()
        f[200:230, 1500:1530] = 255  # 30×30
        assert detect_container_window(f) is None

    def test_custom_search_roi(self):
        """Passing search_roi limits detection to that region."""
        f = _blank()
        f[100:350, 100:400] = 255  # left side — outside default search area
        # With default → not found (it's in the left third)
        assert detect_container_window(f) is None
        # With custom ROI → found
        result = detect_container_window(f, search_roi=(50, 50, 500, 500))
        assert result is not None

    def test_works_on_720p_frame(self):
        f = _blank(720, 1280)
        f[100:300, 900:1150] = 255
        result = detect_container_window(f)
        assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# scale_offset_x / scale_offset_y
# ─────────────────────────────────────────────────────────────────────────────

class TestScaleOffset:
    """Resolution-scaling helpers."""

    def test_scale_y_identity_at_1080(self):
        f = _blank(1080, 1920)
        assert scale_offset_y(20, f) == 20

    def test_scale_y_half_resolution(self):
        f = _blank(540, 960)
        assert scale_offset_y(20, f) == 10

    def test_scale_y_double_resolution(self):
        f = _blank(2160, 3840)
        assert scale_offset_y(20, f) == 40

    def test_scale_x_identity_at_1920(self):
        f = _blank(1080, 1920)
        assert scale_offset_x(20, f) == 20

    def test_scale_x_half(self):
        f = _blank(540, 960)
        assert scale_offset_x(20, f) == 10

    def test_scale_returns_at_least_1(self):
        """Even with tiny offset and small frame, result >= 1."""
        f = _blank(100, 100)
        assert scale_offset_y(1, f) >= 1
        assert scale_offset_x(1, f) >= 1

    def test_scale_y_none_frame_passthrough(self):
        assert scale_offset_y(42, np.empty((0,), dtype=np.uint8)) == 42

    def test_scale_x_none_frame_passthrough(self):
        assert scale_offset_x(42, np.empty((0,), dtype=np.uint8)) == 42


# ─────────────────────────────────────────────────────────────────────────────
# Integration: re-exports from looter
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterReExports:
    """Verify looter re-exports the detection functions."""

    def test_detect_context_menu_importable_from_looter(self):
        from src.looter import detect_context_menu as dcm
        assert dcm is detect_context_menu

    def test_detect_container_window_importable_from_looter(self):
        from src.looter import detect_container_window as dcw
        assert dcw is detect_container_window

    def test_find_menu_entry_offset_importable_from_looter(self):
        from src.looter import find_menu_entry_offset as fmo
        assert fmo is find_menu_entry_offset


# ─────────────────────────────────────────────────────────────────────────────
# Integration: depot_manager uses ui_detection
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotManagerVisualDetection:
    """Verify depot_manager imports and uses ui_detection correctly."""

    def test_depot_manager_imports_detection(self):
        import src.depot_manager as dm
        # Should have the visual detection modules available
        assert hasattr(dm, "detect_context_menu")
        assert hasattr(dm, "detect_container_window")

    def test_detect_open_container_uses_visual(self):
        """_detect_open_container should return True for a frame with edges."""
        from unittest.mock import MagicMock
        from src.depot_manager import DepotManager, DepotConfig
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        mgr = DepotManager(ctrl=ctrl, config=DepotConfig())
        mgr.set_log_callback(lambda s: None)

        # Frame with a bright rectangle should trigger edge-based detection
        crop = np.zeros((200, 200, 3), dtype=np.uint8)
        crop[20:180, 20:180] = 200  # large bright rect → edges
        assert mgr._detect_open_container(crop) is True

    def test_detect_open_container_empty_crop(self):
        from unittest.mock import MagicMock
        from src.depot_manager import DepotManager, DepotConfig
        ctrl = MagicMock()
        mgr = DepotManager(ctrl=ctrl, config=DepotConfig())
        mgr.set_log_callback(lambda s: None)
        assert mgr._detect_open_container(None) is False
        assert mgr._detect_open_container(np.empty((0, 0, 3), dtype=np.uint8)) is False

    def test_tile_to_screen_auto_center(self):
        """When viewport_center is empty, should derive from frame."""
        from unittest.mock import MagicMock
        from src.depot_manager import DepotManager, DepotConfig
        ctrl = MagicMock()
        cfg = DepotConfig(viewport_center=[])
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        mgr = DepotManager(ctrl=ctrl, config=cfg, frame_getter=lambda: frame)
        mgr.set_log_callback(lambda s: None)
        px, py = mgr._tile_to_screen(100, 100, 100, 100)
        # Should be frame center: 960, 540
        assert px == 960
        assert py == 540

    def test_tile_to_screen_config_center(self):
        """When viewport_center is set, uses config values."""
        from unittest.mock import MagicMock
        from src.depot_manager import DepotManager, DepotConfig
        ctrl = MagicMock()
        cfg = DepotConfig(viewport_center=[640, 480])
        mgr = DepotManager(ctrl=ctrl, config=cfg)
        mgr.set_log_callback(lambda s: None)
        px, py = mgr._tile_to_screen(10, 10, 10, 10)
        assert (px, py) == (640, 480)


# ─────────────────────────────────────────────────────────────────────────────
# Empty-crop guard paths (lines 50, 186)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyCropGuards:

    def test_detect_context_menu_empty_crop_returns_none(self):
        """Click point at edge of a tiny frame → crop becomes empty → None."""
        small = np.zeros((5, 5, 3), dtype=np.uint8)
        # click at (4,4) with search_margin=120 → crop will still exist but
        # let's use click at far right with a 1-px wide frame to force empty crop
        tiny = np.zeros((10, 1, 3), dtype=np.uint8)
        result = detect_context_menu(tiny, tiny, click_x=0, click_y=0,
                                     search_margin=0)
        # margin=0 → x1=x2=0, crop width=0 → None
        assert result is None

    def test_detect_container_window_empty_crop_returns_none(self):
        """search_roi that falls completely outside frame → empty crop → None."""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        # search_roi entirely outside the frame
        result = detect_container_window(frame, search_roi=(200, 200, 50, 50))
        assert result is None
