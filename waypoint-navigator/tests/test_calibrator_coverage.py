"""
tests/test_calibrator_coverage.py
==================================
Additional coverage tests for src/calibrator.py.

All tests are 100% offline:
  - OpenCV windows are mocked (no GUI).
  - Frame capture (WGCSource, VirtualCameraSource, etc.) is mocked.
  - Config save/load is intercepted via tmp_path or MagicMock.
  - numpy arrays are used directly for frame-based tests.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _make_frame(h: int = 600, w: int = 800) -> np.ndarray:
    """Return a BGR uint8 array filled with random-ish pixel values."""
    return np.random.randint(30, 200, (h, w, 3), dtype=np.uint8)


def _patch_cv2():
    """Return a cv2 mock that supports namedWindow / setMouseCallback /
    imshow / waitKey / destroyWindow / destroyAllWindows / rectangle /
    putText / resize / EVENT_* constants."""
    cv2 = MagicMock(name="cv2_mock")
    cv2.EVENT_LBUTTONDOWN = 1
    cv2.EVENT_MOUSEMOVE   = 0
    cv2.EVENT_LBUTTONUP   = 4
    cv2.WINDOW_NORMAL     = 0
    cv2.FONT_HERSHEY_SIMPLEX = 0
    cv2.LINE_AA           = 16
    cv2.CAP_DSHOW         = 700
    cv2.CAP_PROP_FRAME_WIDTH  = 3
    cv2.CAP_PROP_FRAME_HEIGHT = 4
    cv2.CAP_PROP_FPS          = 5
    # waitKey returns ord('q') by default (skip the ROI dialog immediately)
    cv2.waitKey.return_value = ord("q")
    # resize returns a copy of a small array
    cv2.resize.side_effect = lambda img, size, **kw: np.zeros((*reversed(size), 3), dtype=np.uint8)
    return cv2


# ---------------------------------------------------------------------------
# _mouse_cb
# ---------------------------------------------------------------------------

class TestMouseCallback:
    """Tests for the internal _mouse_cb global function."""

    def test_lbuttondown_sets_start_and_end(self):
        import src.calibrator as cal
        import cv2 as _cv2
        cal._rect_start = None
        cal._rect_end   = None
        cal._drawing    = False
        cal._mouse_cb(_cv2.EVENT_LBUTTONDOWN, 10, 20, 0, None)
        assert cal._rect_start == (10, 20)
        assert cal._rect_end   == (10, 20)
        assert cal._drawing is True

    def test_mousemove_while_drawing_updates_end(self):
        import src.calibrator as cal
        import cv2 as _cv2
        cal._rect_start = (5, 5)
        cal._drawing    = True
        cal._mouse_cb(_cv2.EVENT_MOUSEMOVE, 50, 60, 0, None)
        assert cal._rect_end == (50, 60)

    def test_mousemove_not_drawing_does_nothing(self):
        import src.calibrator as cal
        import cv2 as _cv2
        cal._rect_end = (1, 2)
        cal._drawing  = False
        cal._mouse_cb(_cv2.EVENT_MOUSEMOVE, 99, 99, 0, None)
        assert cal._rect_end == (1, 2)

    def test_lbuttonup_finalises_and_clears_drawing(self):
        import src.calibrator as cal
        import cv2 as _cv2
        cal._drawing = True
        cal._mouse_cb(_cv2.EVENT_LBUTTONUP, 100, 200, 0, None)
        assert cal._rect_end == (100, 200)
        assert cal._drawing is False


# ---------------------------------------------------------------------------
# _draw_existing_roi
# ---------------------------------------------------------------------------

class TestDrawExistingRoi:
    """_draw_existing_roi() should only call cv2.rectangle when roi is valid."""

    def test_none_roi_is_noop(self):
        cv2_mock = _patch_cv2()
        with patch("src.calibrator.cv2", cv2_mock):
            from src.calibrator import _draw_existing_roi
            canvas = _make_frame()
            _draw_existing_roi(canvas, None, 1.0, (0, 255, 0), "label")
        cv2_mock.rectangle.assert_not_called()

    def test_short_roi_is_noop(self):
        cv2_mock = _patch_cv2()
        with patch("src.calibrator.cv2", cv2_mock):
            from src.calibrator import _draw_existing_roi
            canvas = _make_frame()
            _draw_existing_roi(canvas, [10, 20, 30], 1.0, (0, 255, 0), "label")
        cv2_mock.rectangle.assert_not_called()

    def test_valid_roi_calls_rectangle(self):
        cv2_mock = _patch_cv2()
        with patch("src.calibrator.cv2", cv2_mock):
            from src.calibrator import _draw_existing_roi
            canvas = _make_frame()
            _draw_existing_roi(canvas, [10, 20, 30, 40], 1.0, (0, 255, 0), "hp")
        cv2_mock.rectangle.assert_called_once()

    def test_scale_applied_to_coords(self):
        """When scale=0.5 the drawn rect should use halved coordinates."""
        cv2_mock = _patch_cv2()
        with patch("src.calibrator.cv2", cv2_mock):
            from src.calibrator import _draw_existing_roi
            canvas = _make_frame()
            _draw_existing_roi(canvas, [100, 200, 50, 60], 0.5, (0, 180, 180), "actual")
        args = cv2_mock.rectangle.call_args
        # first positional: canvas, then pt1, pt2
        pt1, pt2 = args[0][1], args[0][2]
        assert pt1 == (50, 100)   # 100*0.5, 200*0.5
        assert pt2 == (75, 130)   # (100+50)*0.5, (200+60)*0.5


# ---------------------------------------------------------------------------
# calibrate_roi  (the generic dialog)
# ---------------------------------------------------------------------------

class TestCalibrateRoi:
    """Tests for calibrate_roi() with a fully mocked cv2."""

    def _run_with_key(self, key_sequence, rect_start=None, rect_end=None,
                      frame_shape=(600, 800, 3), existing_roi=None):
        """
        Run calibrate_roi() pumping *key_sequence* from waitKey one by one.
        Returns (saved: bool, on_save_calls: list).
        """
        cv2_mock = _patch_cv2()
        _keys = iter(key_sequence)
        cv2_mock.waitKey.side_effect = lambda ms: next(_keys, ord("q"))

        saved_args: list = []

        def on_save(x, y, w, h):
            saved_args.append((x, y, w, h))

        frame = np.zeros(frame_shape, dtype=np.uint8)

        import src.calibrator as cal
        # Inject desired rect state via module globals
        original_start = cal._rect_start
        original_end   = cal._rect_end
        original_draw  = cal._drawing

        with patch("src.calibrator.cv2", cv2_mock):
            # Pre-set the selection so 's' can trigger save
            if rect_start is not None:
                cal._rect_start = rect_start
                cal._rect_end   = rect_end
                cal._drawing    = False
            result = cal.calibrate_roi(
                frame=frame,
                title="Test ROI",
                instructions="Draw a box",
                on_save=on_save,
                existing_roi=existing_roi,
                min_w=5,
                min_h=3,
            )

        # Restore
        cal._rect_start = original_start
        cal._rect_end   = original_end
        cal._drawing    = original_draw

        return result, saved_args

    def test_q_returns_false_without_saving(self):
        saved, calls = self._run_with_key([ord("q")])
        assert saved is False
        assert calls == []

    def test_r_resets_rect(self):
        """Pressing R clears the rect; then Q exits."""
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        keys = iter([ord("r"), ord("q")])
        cv2_mock.waitKey.side_effect = lambda ms: next(keys, ord("q"))

        frame = np.zeros((600, 800, 3), dtype=np.uint8)
        cal._rect_start = (10, 10)
        cal._rect_end   = (50, 50)
        cal._drawing    = False

        with patch("src.calibrator.cv2", cv2_mock):
            result = cal.calibrate_roi(
                frame=frame, title="T", instructions="I",
                on_save=lambda *a: None,
            )
        assert result is False
        # After R the globals should be None
        assert cal._rect_start is None
        assert cal._rect_end   is None

    def test_s_without_rect_does_not_save(self):
        """Pressing S without a drawn rect should not call on_save."""
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        keys = iter([ord("s"), ord("q")])
        cv2_mock.waitKey.side_effect = lambda ms: next(keys, ord("q"))
        frame = np.zeros((600, 800, 3), dtype=np.uint8)
        cal._rect_start = None
        cal._rect_end   = None

        saved_calls = []
        with patch("src.calibrator.cv2", cv2_mock):
            result = cal.calibrate_roi(
                frame=frame, title="T", instructions="I",
                on_save=lambda x, y, w, h: saved_calls.append(True),
            )
        assert result is False
        assert saved_calls == []

    def test_s_with_too_small_rect_does_not_save(self):
        """A rect smaller than min_w/min_h should reject and loop."""
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        keys = iter([ord("s"), ord("q")])
        cv2_mock.waitKey.side_effect = lambda ms: next(keys, ord("q"))
        frame = np.zeros((600, 800, 3), dtype=np.uint8)
        cal._rect_start = (10, 10)
        cal._rect_end   = (12, 11)  # 2×1 — too small for min_w=5, min_h=3

        saved_calls = []
        with patch("src.calibrator.cv2", cv2_mock):
            result = cal.calibrate_roi(
                frame=frame, title="T", instructions="I",
                on_save=lambda x, y, w, h: saved_calls.append(True),
                min_w=5, min_h=3,
            )
        assert result is False
        assert saved_calls == []

    def test_s_with_valid_rect_saves_and_returns_true(self):
        """A large-enough rect pressed with S should call on_save and return True."""
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        # calibrate_roi resets _rect_start/_rect_end at entry, so we must set
        # them from inside the waitKey side_effect (simulates mouse draw + S).
        def _waitkey(ms: int) -> int:
            cal._rect_start = (10, 20)
            cal._rect_end   = (110, 70)  # 100×50
            return ord("s")
        cv2_mock.waitKey.side_effect = _waitkey
        frame = np.zeros((600, 800, 3), dtype=np.uint8)

        saved_calls = []
        with patch("src.calibrator.cv2", cv2_mock):
            result = cal.calibrate_roi(
                frame=frame, title="T", instructions="I",
                on_save=lambda x, y, w, h: saved_calls.append((x, y, w, h)),
                min_w=5, min_h=3,
            )
        assert result is True
        assert len(saved_calls) == 1
        x, y, w, h = saved_calls[0]
        assert w == 100 and h == 50

    def test_rect_coordinates_normalised(self):
        """Even if start > end, coordinates are swapped so x0<x1, y0<y1."""
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        # calibrate_roi resets _rect_start/_rect_end at entry, so assign them
        # inside the waitKey side_effect — same pattern used by
        # test_s_with_valid_rect_saves_and_returns_true.
        def _waitkey(ms: int) -> int:
            cal._rect_start = (200, 150)   # drag from bottom-right …
            cal._rect_end   = (50, 30)     # … to top-left
            return ord("s")
        cv2_mock.waitKey.side_effect = _waitkey
        frame = np.zeros((600, 800, 3), dtype=np.uint8)

        saved_calls: list = []
        with patch("src.calibrator.cv2", cv2_mock):
            cal.calibrate_roi(
                frame=frame, title="T", instructions="I",
                on_save=lambda x, y, w, h: saved_calls.append((x, y, w, h)),
                min_w=5, min_h=3,
            )
        assert len(saved_calls) == 1, (
            f"on_save debería haberse llamado exactamente una vez, "
            f"pero saved_calls={saved_calls!r}"
        )
        x, y, w, h = saved_calls[0]
        assert x == 50 and y == 30
        assert w == 150 and h == 120

    def test_existing_roi_drawn_as_reference(self):
        """calibrate_roi with an existing_roi should call _draw_existing_roi."""
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        cv2_mock.waitKey.return_value = ord("q")
        frame = np.zeros((600, 800, 3), dtype=np.uint8)
        cal._rect_start = None
        cal._rect_end   = None

        with patch("src.calibrator.cv2", cv2_mock), \
             patch("src.calibrator._draw_existing_roi") as mock_draw:
            cal.calibrate_roi(
                frame=frame, title="T", instructions="I",
                on_save=lambda *a: None,
                existing_roi=[10, 20, 100, 50],
            )
        mock_draw.assert_called()

    def test_large_frame_is_scaled_down(self):
        """A frame larger than 1280×720 should trigger cv2.resize."""
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        cv2_mock.waitKey.return_value = ord("q")
        # 2560×1440 > max 1280×720
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        cal._rect_start = None
        cal._rect_end   = None

        with patch("src.calibrator.cv2", cv2_mock):
            cal.calibrate_roi(
                frame=frame, title="T", instructions="I",
                on_save=lambda *a: None,
            )
        cv2_mock.resize.assert_called()

    def test_small_frame_not_resized(self):
        """A frame already ≤ 1280×720 should NOT be resized."""
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        cv2_mock.waitKey.return_value = ord("q")
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        cal._rect_start = None
        cal._rect_end   = None

        with patch("src.calibrator.cv2", cv2_mock):
            cal.calibrate_roi(
                frame=frame, title="T", instructions="I",
                on_save=lambda *a: None,
            )
        cv2_mock.resize.assert_not_called()


# ---------------------------------------------------------------------------
# _capture_frame
# ---------------------------------------------------------------------------

class TestCaptureFrame:
    """Tests for _capture_frame() with all hardware mocked."""

    _UNSET = object()

    def _make_source_mock(self, frame=_UNSET):
        src_mock = MagicMock()
        src_mock.get_frame.return_value = _make_frame() if frame is TestCaptureFrame._UNSET else frame
        return src_mock

    def test_mss_source_returns_frame(self):
        frame = _make_frame()
        with patch("src.calibrator.WGCSource") as MockWGC, \
             patch("src.calibrator.DetectorConfig") as MockCfg:
            src_inst = self._make_source_mock(frame)
            MockWGC.return_value = src_inst
            MockCfg.load.return_value = MagicMock(obs_source="")
            from src.calibrator import _capture_frame
            result = _capture_frame(source="mss", window_title="Tibia")
        assert result is not None
        np.testing.assert_array_equal(result, frame)

    def test_wgc_source_returns_frame(self):
        frame = _make_frame()
        with patch("src.calibrator.WGCSource") as MockWGC, \
             patch("src.calibrator.DetectorConfig") as MockCfg:
            src_inst = self._make_source_mock(frame)
            MockWGC.return_value = src_inst
            MockCfg.load.return_value = MagicMock(obs_source="")
            from src.calibrator import _capture_frame
            result = _capture_frame(source="wgc", window_title="TestWindow")
        assert result is not None

    def test_virtual_cam_source_warms_up(self):
        frame = _make_frame()
        with patch("src.calibrator.VirtualCameraSource") as MockVC, \
             patch("src.calibrator.DetectorConfig") as MockCfg:
            src_inst = self._make_source_mock(frame)
            MockVC.return_value = src_inst
            MockCfg.load.return_value = MagicMock(obs_source="", obs_cam_index=0)
            from src.calibrator import _capture_frame
            result = _capture_frame(source="virtual-cam")
        # 5 warmup calls + 1 real call = 6
        assert src_inst.get_frame.call_count >= 6

    def test_screen_source_uses_mss_screen(self):
        frame = _make_frame()
        with patch("src.calibrator.MSSScreenSource") as MockMSS, \
             patch("src.calibrator.DetectorConfig") as MockCfg:
            src_inst = self._make_source_mock(frame)
            MockMSS.return_value = src_inst
            MockCfg.load.return_value = MagicMock(obs_source="")
            from src.calibrator import _capture_frame
            result = _capture_frame(source="screen")
        assert result is not None

    def test_obs_ws_source(self):
        frame = _make_frame()
        with patch("src.calibrator.OBSWebSocketSource") as MockOBS, \
             patch("src.calibrator.DetectorConfig") as MockCfg:
            src_inst = self._make_source_mock(frame)
            MockOBS.return_value = src_inst
            MockCfg.load.return_value = MagicMock(obs_source="GameCapture")
            from src.calibrator import _capture_frame
            result = _capture_frame(source="obs-ws", obs_source_name="GameCapture")
        assert result is not None

    def test_disconnect_exception_is_swallowed(self):
        """Even if src.disconnect() raises, _capture_frame should not propagate it."""
        frame = _make_frame()
        with patch("src.calibrator.WGCSource") as MockWGC, \
             patch("src.calibrator.DetectorConfig") as MockCfg:
            src_inst = self._make_source_mock(frame)
            src_inst.disconnect.side_effect = RuntimeError("boom")
            MockWGC.return_value = src_inst
            MockCfg.load.return_value = MagicMock(obs_source="")
            from src.calibrator import _capture_frame
            result = _capture_frame(source="mss")
        assert result is not None

    def test_none_frame_propagated(self):
        with patch("src.calibrator.WGCSource") as MockWGC, \
             patch("src.calibrator.DetectorConfig") as MockCfg:
            src_inst = self._make_source_mock(None)
            MockWGC.return_value = src_inst
            MockCfg.load.return_value = MagicMock(obs_source="")
            from src.calibrator import _capture_frame
            result = _capture_frame(source="mss")
        assert result is None


# ---------------------------------------------------------------------------
# calibrate_coord / calibrate_hp / calibrate_mp / calibrate_minimap
# ---------------------------------------------------------------------------

class TestModeSpecificCalibrators:
    """
    Each mode-specific calibrator should:
      1. Load its config.
      2. Call calibrate_roi with appropriate parameters.
      3. The on_save closure should persist the roi.
    """

    def _run_mode_fn(self, fn, frame, mock_config_cls, config_attr, roi_val):
        """
        Helper: mock cv2 to press 'S' immediately, inject the rect so it
        triggers the save callback, and verify the config attribute is updated.
        """
        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        def _waitkey(ms: int) -> int:
            cal._rect_start = (10, 20)
            cal._rect_end   = (110, 70)  # 100×50, passes any min_w/min_h
            return ord("s")
        cv2_mock.waitKey.side_effect = _waitkey

        cfg_inst = MagicMock()
        # pre-set the roi attribute so existing_roi is accessible
        setattr(cfg_inst, config_attr, [0, 0, 10, 10])
        mock_config_cls.load.return_value = cfg_inst

        with patch("src.calibrator.cv2", cv2_mock):
            result = fn(frame)

        return result, cfg_inst

    def test_calibrate_coord_calls_save(self):
        frame = _make_frame()
        import src.calibrator as cal
        with patch("src.calibrator.DetectorConfig") as MockCfg, \
             patch("src.calibrator.ImageProcessor") as MockProc:
            cv2_mock_inner = _patch_cv2()
            def _waitkey(ms: int) -> int:
                cal._rect_start = (10, 20)
                cal._rect_end   = (110, 70)
                return ord("s")
            cv2_mock_inner.waitKey.side_effect = _waitkey

            cfg_inst = MagicMock()
            cfg_inst.roi = [0, 0, 10, 10]
            MockCfg.load.return_value = cfg_inst

            proc_inst = MagicMock()
            proc_inst.preprocess.return_value = np.zeros((10, 10, 3), dtype=np.uint8)
            MockProc.return_value = proc_inst

            with patch("src.calibrator.cv2", cv2_mock_inner):
                result = cal.calibrate_coord(frame)

        assert result is True
        cfg_inst.save.assert_called()

    def test_calibrate_hp_calls_save(self):
        frame = _make_frame()
        import src.calibrator as cal
        with patch("src.calibrator.HpMpConfig") as MockCfg:
            cv2_mock = _patch_cv2()
            def _waitkey(ms: int) -> int:
                cal._rect_start = (10, 20)
                cal._rect_end   = (110, 70)
                return ord("s")
            cv2_mock.waitKey.side_effect = _waitkey

            cfg_inst = MagicMock()
            cfg_inst.hp_roi = [0, 0, 10, 10]
            MockCfg.load.return_value = cfg_inst

            with patch("src.calibrator.cv2", cv2_mock):
                result = cal.calibrate_hp(frame)

        assert result is True
        cfg_inst.save.assert_called()
        assert cfg_inst.hp_roi == [10, 20, 100, 50]

    def test_calibrate_mp_calls_save(self):
        frame = _make_frame()
        import src.calibrator as cal
        with patch("src.calibrator.HpMpConfig") as MockCfg:
            cv2_mock = _patch_cv2()
            def _waitkey(ms: int) -> int:
                cal._rect_start = (10, 20)
                cal._rect_end   = (110, 70)
                return ord("s")
            cv2_mock.waitKey.side_effect = _waitkey

            cfg_inst = MagicMock()
            cfg_inst.mp_roi = [0, 0, 10, 10]
            MockCfg.load.return_value = cfg_inst

            with patch("src.calibrator.cv2", cv2_mock):
                result = cal.calibrate_mp(frame)

        assert result is True
        cfg_inst.save.assert_called()
        assert cfg_inst.mp_roi == [10, 20, 100, 50]

    def test_calibrate_minimap_calls_save(self):
        frame = _make_frame()
        import src.calibrator as cal
        with patch("src.calibrator.MinimapConfig") as MockCfg:
            cv2_mock = _patch_cv2()
            def _waitkey(ms: int) -> int:
                cal._rect_start = (10, 20)
                cal._rect_end   = (110, 70)
                return ord("s")
            cv2_mock.waitKey.side_effect = _waitkey

            cfg_inst = MagicMock()
            cfg_inst.roi = [0, 0, 10, 10]
            MockCfg.load.return_value = cfg_inst

            with patch("src.calibrator.cv2", cv2_mock):
                result = cal.calibrate_minimap(frame)

        assert result is True
        cfg_inst.save.assert_called()

    def test_calibrate_hp_q_returns_false(self):
        frame = _make_frame()
        with patch("src.calibrator.HpMpConfig") as MockCfg:
            import src.calibrator as cal
            cv2_mock = _patch_cv2()
            cv2_mock.waitKey.return_value = ord("q")
            cal._rect_start = None
            cal._rect_end   = None

            cfg_inst = MagicMock()
            cfg_inst.hp_roi = [0, 0, 10, 10]
            MockCfg.load.return_value = cfg_inst

            with patch("src.calibrator.cv2", cv2_mock):
                result = cal.calibrate_hp(frame)

        assert result is False

    def test_calibrate_mp_q_returns_false(self):
        frame = _make_frame()
        with patch("src.calibrator.HpMpConfig") as MockCfg:
            import src.calibrator as cal
            cv2_mock = _patch_cv2()
            cv2_mock.waitKey.return_value = ord("q")
            cal._rect_start = None
            cal._rect_end   = None

            cfg_inst = MagicMock()
            cfg_inst.mp_roi = [0, 0, 10, 10]
            MockCfg.load.return_value = cfg_inst

            with patch("src.calibrator.cv2", cv2_mock):
                result = cal.calibrate_mp(frame)

        assert result is False

    def test_calibrate_minimap_q_returns_false(self):
        frame = _make_frame()
        with patch("src.calibrator.MinimapConfig") as MockCfg:
            import src.calibrator as cal
            cv2_mock = _patch_cv2()
            cv2_mock.waitKey.return_value = ord("q")
            cal._rect_start = None
            cal._rect_end   = None

            cfg_inst = MagicMock()
            cfg_inst.roi = [0, 0, 10, 10]
            MockCfg.load.return_value = cfg_inst

            with patch("src.calibrator.cv2", cv2_mock):
                result = cal.calibrate_minimap(frame)

        assert result is False


# ---------------------------------------------------------------------------
# calibrate_battle_list
# ---------------------------------------------------------------------------

class TestCalibrateBattleList:

    def test_returns_false_when_combat_manager_missing(self):
        """If combat_manager is unavailable, calibrate_battle_list returns False."""
        import src.calibrator as cal
        frame = _make_frame()
        with patch.dict("sys.modules", {"src.combat_manager": None}):
            result = cal.calibrate_battle_list(frame)
        assert result is False

    def test_saves_battle_list_roi(self):
        frame = _make_frame()
        # Build a minimal fake combat_manager module
        fake_combat = types.ModuleType("src.combat_manager")
        cfg_inst = MagicMock()
        cfg_inst.battle_list_roi = [0, 0, 50, 50]
        CombatConfig = MagicMock()
        CombatConfig.load.return_value = cfg_inst
        fake_combat.CombatConfig = CombatConfig
        fake_combat.COMBAT_CONFIG_FILE = Path("/tmp/combat.json")

        import src.calibrator as cal
        cv2_mock = _patch_cv2()
        def _waitkey(ms: int) -> int:
            cal._rect_start = (10, 20)
            cal._rect_end   = (110, 120)  # 100×100 — passes min_w=50, min_h=50
            return ord("s")
        cv2_mock.waitKey.side_effect = _waitkey

        with patch.dict("sys.modules", {"src.combat_manager": fake_combat}), \
             patch("src.calibrator.cv2", cv2_mock):
            result = cal.calibrate_battle_list(frame)

        assert result is True
        assert cfg_inst.battle_list_roi == [10, 20, 100, 100]
        cfg_inst.save.assert_called()


# ---------------------------------------------------------------------------
# calibrate() — main entry point
# ---------------------------------------------------------------------------

class TestCalibrate:
    """Tests for the high-level calibrate() function."""

    def test_frame_none_prints_error_and_returns(self, capsys):
        with patch("src.calibrator._capture_frame", return_value=None), \
             patch("src.calibrator.cv2", _patch_cv2()):
            from src.calibrator import calibrate
            calibrate(source="mss", mode="coord")
        out = capsys.readouterr().out
        assert "ERROR" in out or "No se pudo" in out

    def test_single_mode_runs_once(self):
        frame = _make_frame()
        cv2_mock = _patch_cv2()
        cv2_mock.waitKey.return_value = ord("q")

        mock_fn = MagicMock(return_value=False)
        with patch("src.calibrator._capture_frame", return_value=frame), \
             patch("src.calibrator.cv2", cv2_mock), \
             patch.dict("src.calibrator._MODE_FNS", {"coord": mock_fn}):
            from src.calibrator import calibrate
            calibrate(source="mss", mode="coord")

        mock_fn.assert_called_once_with(frame)

    def test_all_mode_runs_all_modes(self):
        frame = _make_frame()
        cv2_mock = _patch_cv2()
        cv2_mock.waitKey.return_value = ord("q")

        mock_fns = {m: MagicMock(return_value=True)
                    for m in ("coord", "hp", "mp", "minimap", "battle-list")}

        with patch("src.calibrator._capture_frame", return_value=frame), \
             patch("src.calibrator.cv2", cv2_mock), \
             patch.dict("src.calibrator._MODE_FNS", mock_fns):
            from src.calibrator import calibrate
            calibrate(source="mss", mode="all")

        for fn in mock_fns.values():
            fn.assert_called_once_with(frame)

    def test_unknown_mode_skipped(self, capsys):
        frame = _make_frame()
        cv2_mock = _patch_cv2()
        with patch("src.calibrator._capture_frame", return_value=frame), \
             patch("src.calibrator.cv2", cv2_mock):
            from src.calibrator import calibrate
            calibrate(source="mss", mode="nonexistent_mode_xyz")
        out = capsys.readouterr().out
        assert "desconocido" in out or "nonexistent" in out.lower() or "Modo" in out

    def test_completed_count_printed(self, capsys):
        frame = _make_frame()
        cv2_mock = _patch_cv2()
        mock_fn = MagicMock(return_value=True)
        with patch("src.calibrator._capture_frame", return_value=frame), \
             patch("src.calibrator.cv2", cv2_mock), \
             patch.dict("src.calibrator._MODE_FNS", {"hp": mock_fn}):
            from src.calibrator import calibrate
            calibrate(source="mss", mode="hp")
        out = capsys.readouterr().out
        assert "1/1" in out


# ---------------------------------------------------------------------------
# validate_roi_bounds
# ---------------------------------------------------------------------------

class TestValidateRoiBounds:

    def test_valid_roi_no_warnings(self):
        from src.calibrator import validate_roi_bounds
        warns = validate_roi_bounds([10, 10, 100, 50])
        assert warns == []

    def test_invalid_roi_format(self):
        from src.calibrator import validate_roi_bounds
        warns = validate_roi_bounds([0, 0, 0, 50])  # w=0 is invalid
        assert len(warns) > 0

    def test_exceeds_frame_width(self):
        from src.calibrator import validate_roi_bounds
        warns = validate_roi_bounds([1900, 0, 100, 50], frame_w=1920, frame_h=1080)
        assert any("ancho" in w.lower() or "width" in w.lower() or "excede" in w for w in warns)

    def test_exceeds_frame_height(self):
        from src.calibrator import validate_roi_bounds
        warns = validate_roi_bounds([0, 1060, 100, 50], frame_w=1920, frame_h=1080)
        assert any("alto" in w.lower() or "height" in w.lower() or "excede" in w for w in warns)

    def test_too_small_roi(self):
        from src.calibrator import validate_roi_bounds
        warns = validate_roi_bounds([0, 0, 2, 2])
        assert any("pequeño" in w or "small" in w.lower() for w in warns)

    def test_oversized_roi_fraction_warning(self):
        from src.calibrator import validate_roi_bounds
        # 1000×600 on a 1920×1080 frame = 28.9% — under 50%, no warning
        warns = validate_roi_bounds([0, 0, 1000, 600], frame_w=1920, frame_h=1080)
        # No fraction warning expected
        fraction_warns = [w for w in warns if "%" in w]
        assert fraction_warns == []

    def test_full_frame_roi_triggers_fraction_warning(self):
        from src.calibrator import validate_roi_bounds
        # 1920×1080 = 100% of the frame
        warns = validate_roi_bounds([0, 0, 1920, 1080], frame_w=1920, frame_h=1080)
        assert any("%" in w for w in warns)

    def test_custom_frame_dimensions(self):
        from src.calibrator import validate_roi_bounds
        warns = validate_roi_bounds([0, 0, 100, 50], frame_w=80, frame_h=40)
        assert any("excede" in w for w in warns)


# ---------------------------------------------------------------------------
# calibrate_headless
# ---------------------------------------------------------------------------

class TestCalibrateHeadless:
    """Tests for calibrate_headless() — no GUI, direct roi injection."""

    def test_coord_mode_saves(self):
        from src.calibrator import calibrate_headless
        with patch("src.calibrator.DetectorConfig") as MockDC:
            cfg_inst = MagicMock()
            MockDC.load.return_value = cfg_inst
            result = calibrate_headless({"coord": [10, 10, 100, 40]})
        assert result is True
        assert cfg_inst.roi == [10, 10, 100, 40]
        cfg_inst.save.assert_called()

    def test_hp_mode_saves(self):
        from src.calibrator import calibrate_headless
        with patch("src.calibrator.HpMpConfig") as MockHp:
            cfg_inst = MagicMock()
            MockHp.load.return_value = cfg_inst
            result = calibrate_headless({"hp": [14, 356, 134, 6]})
        assert result is True
        assert cfg_inst.hp_roi == [14, 356, 134, 6]

    def test_mp_mode_saves(self):
        from src.calibrator import calibrate_headless
        with patch("src.calibrator.HpMpConfig") as MockHp:
            cfg_inst = MagicMock()
            MockHp.load.return_value = cfg_inst
            result = calibrate_headless({"mp": [14, 370, 134, 6]})
        assert result is True
        assert cfg_inst.mp_roi == [14, 370, 134, 6]

    def test_minimap_mode_saves(self):
        from src.calibrator import calibrate_headless
        with patch("src.calibrator.MinimapConfig") as MockMm:
            cfg_inst = MagicMock()
            MockMm.load.return_value = cfg_inst
            result = calibrate_headless({"minimap": [1628, 22, 106, 109]})
        assert result is True
        assert cfg_inst.roi == [1628, 22, 106, 109]

    def test_battle_list_mode_saves(self):
        from src.calibrator import calibrate_headless
        fake_combat = types.ModuleType("src.combat_manager")
        cfg_inst = MagicMock()
        CombatConfig = MagicMock()
        CombatConfig.load.return_value = cfg_inst
        fake_combat.CombatConfig = CombatConfig
        with patch.dict("sys.modules", {"src.combat_manager": fake_combat}):
            result = calibrate_headless({"battle-list": [1569, 444, 162, 229]})
        assert result is True

    def test_battle_list_combat_manager_missing_returns_false(self):
        from src.calibrator import calibrate_headless
        with patch.dict("sys.modules", {"src.combat_manager": None}):
            result = calibrate_headless({"battle-list": [1569, 444, 162, 229]})
        assert result is False

    def test_unknown_mode_returns_false(self, capsys):
        from src.calibrator import calibrate_headless
        result = calibrate_headless({"foobar": [10, 10, 100, 50]})
        assert result is False

    def test_invalid_roi_skips_and_returns_false(self, capsys):
        from src.calibrator import calibrate_headless
        # w=0 → fails validate_roi_bounds
        result = calibrate_headless({"coord": [0, 0, 0, 50]})
        assert result is False

    def test_multiple_modes_all_valid(self):
        from src.calibrator import calibrate_headless
        with patch("src.calibrator.DetectorConfig") as MockDC, \
             patch("src.calibrator.HpMpConfig") as MockHp:
            dc_inst = MagicMock()
            hp_inst = MagicMock()
            MockDC.load.return_value = dc_inst
            MockHp.load.return_value = hp_inst
            result = calibrate_headless({
                "coord": [10, 10, 100, 40],
                "hp":    [14, 356, 134, 6],
            })
        assert result is True

    def test_multiple_modes_one_invalid_returns_false(self):
        from src.calibrator import calibrate_headless
        with patch("src.calibrator.DetectorConfig") as MockDC:
            dc_inst = MagicMock()
            MockDC.load.return_value = dc_inst
            result = calibrate_headless({
                "coord":  [10, 10, 100, 40],
                "foobar": [10, 10, 100, 40],  # unknown → False
            })
        assert result is False


# ---------------------------------------------------------------------------
# apply_preset
# ---------------------------------------------------------------------------

class TestApplyPreset:

    def test_known_preset_calls_headless(self):
        from src.calibrator import apply_preset
        with patch("src.calibrator.calibrate_headless", return_value=True) as mock_hl:
            result = apply_preset("1920x1080")
        assert result is True
        mock_hl.assert_called_once()

    def test_unknown_preset_raises_value_error(self):
        from src.calibrator import apply_preset
        with pytest.raises(ValueError, match="no soportada"):
            apply_preset("640x480")

    def test_preset_rois_are_correct(self):
        """The 1920x1080 preset should include all five expected modes."""
        from src.calibrator import _STANDARD_PRESETS
        preset = _STANDARD_PRESETS["1920x1080"]
        for mode in ("coord", "hp", "mp", "minimap", "battle-list"):
            assert mode in preset
            roi = preset[mode]
            assert len(roi) == 4
            assert all(v >= 0 for v in roi)
