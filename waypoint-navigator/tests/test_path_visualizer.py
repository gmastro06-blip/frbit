"""
Tests for src/path_visualizer.py — PathVisualizer
Fully offline: map loader mocked, no display required.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from src.path_visualizer import PathVisualizer, StepRecord


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_X_MIN = 31744
_Y_MIN = 30976


def _loader(rgba: bool = True) -> MagicMock:
    ldr = MagicMock()
    arr = np.full((500, 500, 4 if rgba else 3), 128, dtype=np.uint8)
    ldr.get_map_image.return_value = arr
    return ldr


def _viz(tmp_path: Path, **kwargs) -> PathVisualizer:
    return PathVisualizer(_loader(), output_dir=tmp_path, **kwargs)


def _dest(dx: int = 0) -> tuple:
    return (_X_MIN + 50 + dx, _Y_MIN + 50, 7)


def _record(idx: int = 0, radar: bool = True) -> StepRecord:
    return StepRecord(
        planned_x=_X_MIN + 10 + idx,
        planned_y=_Y_MIN + 10 + idx,
        actual_x=_X_MIN + 10 + idx,
        actual_y=_Y_MIN + 10 + idx,
        radar_confirmed=radar,
        step_idx=idx,
    )


class _FakeCoord:
    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y


# ─────────────────────────────────────────────────────────────────────────────
# StepRecord
# ─────────────────────────────────────────────────────────────────────────────

class TestStepRecord:

    def test_fields_set(self):
        sr = _record(5, radar=False)
        assert sr.planned_x == _X_MIN + 15
        assert sr.radar_confirmed is False
        assert sr.step_idx == 5

    def test_timestamp_set(self):
        import time
        before = time.monotonic()
        sr = _record()
        after = time.monotonic()
        assert before <= sr.timestamp <= after


# ─────────────────────────────────────────────────────────────────────────────
# Construction
# ─────────────────────────────────────────────────────────────────────────────

class TestConstruction:

    def test_output_dir_created(self, tmp_path):
        out = tmp_path / "traces"
        PathVisualizer(_loader(), output_dir=out)
        assert out.exists()

    def test_mask_dir_created(self, tmp_path):
        out = tmp_path / "path_trace"
        pv = PathVisualizer(_loader(), output_dir=out)
        assert pv._mask_out.exists()
        assert pv._mask_out.name == "masks"

    def test_default_floor_is_7(self, tmp_path):
        pv = _viz(tmp_path)
        assert pv._floor == 7

    def test_custom_floor(self, tmp_path):
        pv = PathVisualizer(_loader(), output_dir=tmp_path, floor=8)
        assert pv._floor == 8

    def test_initial_state(self, tmp_path):
        pv = _viz(tmp_path)
        assert pv._steps == []
        assert pv._planned_path == []
        assert pv._cumulative_img is None


# ─────────────────────────────────────────────────────────────────────────────
# begin_segment / record_step / set_planned_path
# ─────────────────────────────────────────────────────────────────────────────

class TestBeginAndRecord:

    def test_begin_clears_steps(self, tmp_path):
        pv = _viz(tmp_path)
        pv._steps = [_record()]
        pv.begin_segment(1, _dest())
        assert pv._steps == []

    def test_begin_sets_segment_id(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(42, _dest())
        assert pv._segment_id == 42

    def test_begin_sets_dest(self, tmp_path):
        pv = _viz(tmp_path)
        dest = _dest()
        pv.begin_segment(0, dest)
        assert pv._dest == dest

    def test_begin_sets_start(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest(), start=(_X_MIN + 5, _Y_MIN + 5))
        assert pv._start == (_X_MIN + 5, _Y_MIN + 5)

    def test_record_step_appends(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 1, _Y_MIN + 1), (_X_MIN + 1, _Y_MIN + 1), True, 0)
        assert len(pv._steps) == 1

    def test_set_planned_path(self, tmp_path):
        pv = _viz(tmp_path)
        coords = [_FakeCoord(_X_MIN + i, _Y_MIN + i) for i in range(5)]
        pv.set_planned_path(coords)
        assert pv._planned_path == [(_X_MIN + i, _Y_MIN + i) for i in range(5)]

    def test_record_step_tracks_first_divergence(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 1, _Y_MIN + 1), (_X_MIN + 2, _Y_MIN + 1), True, 0)
        assert pv._first_divergence == (_X_MIN + 2, _Y_MIN + 1)

    def test_mark_blocked_tile(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.mark_blocked_tile((_X_MIN + 3, _Y_MIN + 4))
        assert pv._blocked_tile == (_X_MIN + 3, _Y_MIN + 4)


# ─────────────────────────────────────────────────────────────────────────────
# end_segment
# ─────────────────────────────────────────────────────────────────────────────

class TestEndSegment:

    def test_no_data_returns_none(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        assert pv.end_segment() is None

    def test_with_steps_returns_path(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 6, _Y_MIN + 6), True, 0)
        result = pv.end_segment()
        assert result is not None
        assert result.exists()

    def test_creates_png_file(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), False, 0)
        pv.end_segment()
        pngs = list(tmp_path.glob("*.png"))
        assert len(pngs) == 1

    def test_creates_mask_png_file(self, tmp_path):
        out = tmp_path / "path_trace"
        pv = PathVisualizer(_loader(), output_dir=out)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), False, 0)
        pv.end_segment()
        mask_pngs = list((tmp_path / "masks").glob("*.png"))
        assert len(mask_pngs) == 1

    def test_filename_contains_segment_id(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(7, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        result = pv.end_segment()
        assert result is not None
        assert "0007" in result.name

    def test_render_error_returns_none(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        # Make loader raise to exercise the except branch
        pv._loader.get_map_image.side_effect = RuntimeError("no map")
        # Should still return a path (fallback to black bg)
        result = pv.end_segment()
        assert result is not None

    def test_with_planned_path_only(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        coords = [_FakeCoord(_X_MIN + i, _Y_MIN + i) for i in range(3)]
        pv.set_planned_path(coords)
        result = pv.end_segment()
        assert result is not None

    def test_segment_markers_render_with_planned_path(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.set_planned_path([_FakeCoord(_X_MIN + i, _Y_MIN + i) for i in range(3)])
        pv.record_step((_X_MIN + 1, _Y_MIN + 1), (_X_MIN + 2, _Y_MIN + 1), True, 1)
        pv.mark_blocked_tile((_X_MIN + 3, _Y_MIN + 3))
        result = pv.end_segment()
        assert result is not None

    def test_segment_output_uses_larger_annotated_canvas(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        result = pv.end_segment()
        assert result is not None
        import cv2
        rendered = cv2.imread(str(result))
        assert rendered is not None
        assert rendered.shape[0] > pv._HEADER_HEIGHT

    def test_imwrite_failure_returns_none(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        with patch("cv2.imwrite", side_effect=OSError("disk full")):
            result = pv.end_segment()
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# save_cumulative
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveCumulative:

    def test_no_data_returns_none(self, tmp_path):
        pv = _viz(tmp_path)
        assert pv.save_cumulative() is None

    def test_after_end_segment_returns_path(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        pv.set_segment_metrics(planned_steps=7, direct_distance=3)
        pv.end_segment()
        result = pv.save_cumulative()
        assert result is not None
        assert result.name == "cumulative.png"

    def test_save_cumulative_creates_mask(self, tmp_path):
        out = tmp_path / "path_trace"
        pv = PathVisualizer(_loader(), output_dir=out)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        pv.end_segment()
        pv.save_cumulative()
        assert (tmp_path / "masks" / "cumulative.png").exists()

    def test_save_cumulative_imwrite_failure_returns_none(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        pv.end_segment()
        with patch("cv2.imwrite", side_effect=OSError("disk full")):
            result = pv.save_cumulative()
        assert result is None

    def test_cumulative_expands_across_segments(self, tmp_path):
        pv = _viz(tmp_path)
        # First segment
        pv.begin_segment(0, _dest(0))
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        pv.end_segment()
        # Second segment at a different location
        pv.begin_segment(1, _dest(100))
        pv.record_step((_X_MIN + 100, _Y_MIN + 100), (_X_MIN + 100, _Y_MIN + 100), False, 0)
        pv.end_segment()
        result = pv.save_cumulative()
        assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# _get_bounds
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBounds:

    def test_empty_returns_default(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, None)
        bounds = pv._get_bounds()
        assert bounds == (0, 0, 1, 1)

    def test_bounds_include_dest(self, tmp_path):
        pv = _viz(tmp_path)
        dest = (_X_MIN + 50, _Y_MIN + 60, 7)
        pv.begin_segment(0, dest)
        xmin, ymin, xmax, ymax = pv._get_bounds()
        assert xmin <= _X_MIN + 50 <= xmax
        assert ymin <= _Y_MIN + 60 <= ymax

    def test_bounds_include_start(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, None, start=(_X_MIN + 5, _Y_MIN + 5))
        xmin, ymin, xmax, ymax = pv._get_bounds()
        assert xmin <= _X_MIN + 5 <= xmax


# ─────────────────────────────────────────────────────────────────────────────
# _tile_to_px
# ─────────────────────────────────────────────────────────────────────────────

class TestTileToPx:

    def test_origin_tile(self, tmp_path):
        pv = _viz(tmp_path)
        cx, cy = pv._tile_to_px(10, 20, 10, 20)
        s = pv._SCALE
        assert cx == s // 2
        assert cy == s // 2

    def test_offset_tile(self, tmp_path):
        pv = _viz(tmp_path)
        s = pv._SCALE
        cx, cy = pv._tile_to_px(11, 21, 10, 20)
        assert cx == s + s // 2
        assert cy == s + s // 2


# ─────────────────────────────────────────────────────────────────────────────
# Non-RGBA loader (BGR background path)
# ─────────────────────────────────────────────────────────────────────────────

class TestNonRgbaLoader:

    def test_bgr_map_image_works(self, tmp_path):
        ldr = _loader(rgba=False)  # 3-channel
        pv = PathVisualizer(ldr, output_dir=tmp_path)
        pv.begin_segment(0, _dest())
        pv.record_step((_X_MIN + 5, _Y_MIN + 5), (_X_MIN + 5, _Y_MIN + 5), True, 0)
        result = pv.end_segment()
        assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# Multi-step rendering (planned path lines, radar/non-radar color branching)
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiStepRendering:

    def test_radar_and_non_radar_steps(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        for i in range(5):
            pv.record_step(
                (_X_MIN + i, _Y_MIN + i),
                (_X_MIN + i, _Y_MIN + i),
                radar_ok=(i % 2 == 0),
                idx=i,
            )
        result = pv.end_segment()
        assert result is not None

    def test_planned_path_lines_drawn(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        coords = [_FakeCoord(_X_MIN + i * 2, _Y_MIN + i * 2) for i in range(4)]
        pv.set_planned_path(coords)
        pv.record_step((_X_MIN + 2, _Y_MIN + 2), (_X_MIN + 2, _Y_MIN + 2), True, 0)
        result = pv.end_segment()
        assert result is not None

    def test_with_start_and_dest(self, tmp_path):
        pv = _viz(tmp_path)
        pv.begin_segment(
            0,
            dest=(_X_MIN + 30, _Y_MIN + 30, 7),
            start=(_X_MIN + 5, _Y_MIN + 5),
        )
        pv.record_step((_X_MIN + 10, _Y_MIN + 10), (_X_MIN + 10, _Y_MIN + 10), True, 0)
        result = pv.end_segment()
        assert result is not None


# ---------------------------------------------------------------------------
# Canvas dimension cap
# ---------------------------------------------------------------------------

class TestCanvasCap:

    def test_render_caps_huge_canvas(self, tmp_path):
        """Extremely wide bounds should be downscaled instead of OOM."""
        pv = _viz(tmp_path)
        pv.begin_segment(0, _dest())
        # Fake a 600-tile spread — at SCALE=8 that's 4800 px (over _MAX_DIM).
        far_x = _X_MIN + 600
        far_y = _Y_MIN + 600
        pv.record_step((_X_MIN, _Y_MIN), (_X_MIN, _Y_MIN), True, 0)
        pv.record_step((far_x, far_y), (far_x, far_y), True, 1)
        result = pv.end_segment()
        assert result is not None
        img = cv2.imread(str(result))
        assert img is not None
        # With _MAX_DIM=4000 the rendered body must be capped.
        assert img.shape[0] <= PathVisualizer._MAX_DIM + PathVisualizer._HEADER_HEIGHT + 100
        assert img.shape[1] <= PathVisualizer._MAX_DIM + 100
