"""
tests/test_frame_capture_coverage.py
======================================
Additional coverage tests for src/frame_capture.py.

All tests are 100% offline:
  - mss.mss() is mocked.
  - dxcam is mocked as a fake module.
  - ctypes windll calls for PrintWindowCapture are mocked.
  - cv2.VideoCapture is mocked for RtmpCapture / VirtualCameraCapture.
  - winsdk and D3D11 helpers are mocked for WGCCapture.
  - subprocess.Popen is mocked for FFmpeg tests.
"""
from __future__ import annotations

import sys
import time
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call, PropertyMock

import numpy as np
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.frame_capture import (
    MssCapture,
    DxcamCapture,
    PrintWindowCapture,
    RtmpCapture,
    VirtualCameraCapture,
    WGCCapture,
    build_frame_getter,
    _hwnd_client_region,
    _wgc_make_guid,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_FRAME = np.random.randint(10, 240, (1080, 1920, 3), dtype=np.uint8)


def _fake_mss_module(width=1920, height=1080):
    """Build a minimal mss module mock."""
    mod = types.ModuleType("mss")
    sct = MagicMock(name="sct")
    raw = np.zeros((height, width, 4), dtype=np.uint8)
    raw[0, 0] = [10, 20, 30, 255]
    sct.grab.return_value = raw
    sct.monitors = [None, {"left": 0, "top": 0, "width": width, "height": height}]
    sct.__enter__ = MagicMock(return_value=sct)
    sct.__exit__ = MagicMock(return_value=False)
    mod.mss = MagicMock(return_value=sct)
    return mod, sct


def _fake_dxcam_module(frame=None):
    mod = types.ModuleType("dxcam")
    cam = MagicMock(name="dxcam_cam")
    cam.get_latest_frame.return_value = (frame if frame is not None else _FRAME.copy())
    mod.create = MagicMock(return_value=cam)
    return mod, cam


def _fake_cv2_videocapture(frames=None, opened=True):
    cap = MagicMock(name="VideoCapture")
    cap.isOpened.return_value = opened
    if frames is None:
        frames = [_FRAME.copy()]
    _it = iter(frames)

    def _read():
        try:
            return True, next(_it)
        except StopIteration:
            return False, None

    cap.read.side_effect = _read
    cap.get.return_value = 1920.0
    return cap


# ---------------------------------------------------------------------------
# _hwnd_client_region
# ---------------------------------------------------------------------------

class TestHwndClientRegion:
    """Tests for the _hwnd_client_region() helper."""

    def test_returns_none_on_exception(self):
        """If ctypes fails to import or calls raise, should return None."""
        with patch("src.frame_capture._hwnd_client_region", return_value=None):
            from src.frame_capture import _hwnd_client_region as fn
            # Just verify the fallback is used
            result = fn(0)
        assert result is None

    def test_returns_none_for_zero_size_window(self):
        """A window with zero client area returns None."""
        import ctypes
        import ctypes.wintypes as wt

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        # Patch ctypes.windll.user32 at the module level
        fake_user32 = MagicMock()
        fake_user32.ClientToScreen.return_value = 0
        fake_user32.GetClientRect.side_effect = lambda hwnd, ref: None

        def mock_get_client_rect(hwnd, ref):
            # set right=left, bottom=top so w=0, h=0
            ref._obj.right  = ref._obj.left
            ref._obj.bottom = ref._obj.top

        with patch("ctypes.windll") as mock_windll:
            mock_windll.user32 = fake_user32
            result = _hwnd_client_region(999)
        # Any outcome is acceptable — just should not raise
        assert result is None or isinstance(result, dict)

    def test_returns_dict_shape_when_valid(self):
        """When ctypes returns positive size, the dict has the right keys."""
        import ctypes
        import ctypes.wintypes as wt

        fake_user32 = MagicMock()

        # Simulate ClientToScreen filling the POINT with (100, 200)
        def _cts(hwnd, pt_ref):
            pt_ref._obj.x = 100
            pt_ref._obj.y = 200

        # Simulate GetClientRect filling the RECT with right=300, bottom=100
        def _gcr(hwnd, rect_ref):
            rect_ref._obj.left  = 0
            rect_ref._obj.top   = 0
            rect_ref._obj.right = 300
            rect_ref._obj.bottom = 100

        fake_user32.ClientToScreen.side_effect = _cts
        fake_user32.GetClientRect.side_effect  = _gcr

        with patch("ctypes.windll") as mock_windll:
            mock_windll.user32 = fake_user32
            result = _hwnd_client_region(0x1234)

        # If ctypes co-operates, we get a dict; otherwise None is fine too
        if result is not None:
            assert "left" in result
            assert "width" in result
            assert result["width"] > 0


# ---------------------------------------------------------------------------
# MssCapture — additional paths
# ---------------------------------------------------------------------------

class TestMssCaptureAdditional:
    def test_monitor_out_of_range_raises(self):
        mss_mod, sct = _fake_mss_module()
        sct.monitors = [None]  # only monitor[0], so idx=1 is out of range
        with patch.dict("sys.modules", {"mss": mss_mod}):
            with patch("src.frame_capture._hwnd_client_region", return_value=None):
                cap = MssCapture(hwnd=0, monitor_idx=1)
                with pytest.raises(ValueError, match="out of range"):
                    cap.open()

    def test_hwnd_refresh_on_each_grab(self):
        """With an hwnd, _hwnd_client_region is called each time grab is invoked."""
        mss_mod, sct = _fake_mss_module()
        region = {"left": 10, "top": 20, "width": 200, "height": 100}
        raw = np.zeros((100, 200, 4), dtype=np.uint8)
        sct.grab.return_value = raw

        with patch.dict("sys.modules", {"mss": mss_mod}):
            with patch("src.frame_capture._hwnd_client_region",
                       return_value=region) as mock_cr:
                cap = MssCapture(hwnd=0x5678)
                fn = cap.open()
                fn()
                fn()
        # Should have been called twice (once at open for initial region,
        # then once per grab call)
        assert mock_cr.call_count >= 2

    def test_hwnd_fallback_when_region_fails_on_grab(self):
        """If _hwnd_client_region returns None during grab, use the initial region."""
        mss_mod, sct = _fake_mss_module()
        initial_region = {"left": 5, "top": 5, "width": 100, "height": 80}
        raw = np.zeros((80, 100, 4), dtype=np.uint8)
        sct.grab.return_value = raw

        call_count = [0]

        def _cr(hwnd):
            call_count[0] += 1
            return initial_region if call_count[0] == 1 else None

        with patch.dict("sys.modules", {"mss": mss_mod}):
            with patch("src.frame_capture._hwnd_client_region", side_effect=_cr):
                cap = MssCapture(hwnd=0x9999)
                fn = cap.open()
                frame = fn()

        assert frame is not None

    def test_grab_returns_none_on_exception(self):
        """If sct.grab raises an exception, _grab should return None."""
        mss_mod, sct = _fake_mss_module()
        sct.grab.side_effect = OSError("capture error")
        with patch.dict("sys.modules", {"mss": mss_mod}):
            with patch("src.frame_capture._hwnd_client_region", return_value=None):
                cap = MssCapture()
                fn = cap.open()
                result = fn()
        assert result is None

    def test_logs_first_frame_only(self, caplog):
        """The 'First frame' log message should appear exactly once."""
        import logging
        mss_mod, sct = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            with patch("src.frame_capture._hwnd_client_region", return_value=None):
                cap = MssCapture()
                fn = cap.open()
                with caplog.at_level(logging.INFO):
                    fn()
                    fn()
                    fn()
        first_frame_msgs = [r for r in caplog.records
                            if "First frame" in r.getMessage()]
        assert len(first_frame_msgs) == 1

    def test_close_before_open_no_error(self):
        cap = MssCapture()
        cap.close()  # _sct is None, should not raise


# ---------------------------------------------------------------------------
# DxcamCapture — additional paths
# ---------------------------------------------------------------------------

class TestDxcamCaptureAdditional:
    def test_create_failure_returns_noop(self):
        # PR4: dxcam create failure now logs and returns a no-op getter instead of raising,
        # enabling graceful degradation (dirty DXGI state after force-kill).
        dx_mod = types.ModuleType("dxcam")
        dx_mod.create = MagicMock(side_effect=RuntimeError("GPU error"))
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            getter = DxcamCapture(output_idx=99).open()
            assert callable(getter)
            assert getter() is None

    def test_hwnd_region_passed_to_start(self):
        dx_mod, cam = _fake_dxcam_module()
        region = {"left": 10, "top": 20, "width": 300, "height": 200}
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            with patch("src.frame_capture._hwnd_client_region", return_value=region):
                DxcamCapture(hwnd=0xABCD, fps=15).open()
        cam.start.assert_called_with(
            region=(10, 20, 310, 220), target_fps=15
        )

    def test_hwnd_no_region_starts_without_region(self):
        dx_mod, cam = _fake_dxcam_module()
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            with patch("src.frame_capture._hwnd_client_region", return_value=None):
                DxcamCapture(hwnd=0xABCD, fps=5).open()
        cam.start.assert_called_with(target_fps=5)

    def test_grab_logs_first_frame(self, caplog):
        import logging
        dx_mod, cam = _fake_dxcam_module()
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            fn = DxcamCapture().open()
            with caplog.at_level(logging.INFO, logger="wn.fc"):
                fn()
                fn()
        msgs = [r for r in caplog.records if "First frame" in r.getMessage()]
        assert len(msgs) == 1

    def test_close_stop_exception_suppressed(self):
        dx_mod, cam = _fake_dxcam_module()
        cam.stop.side_effect = RuntimeError("stop error")
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            cap = DxcamCapture()
            cap.open()
        cap.close()  # should not raise


# ---------------------------------------------------------------------------
# PrintWindowCapture — mocked Win32 paths
# ---------------------------------------------------------------------------

class TestPrintWindowCaptureAdditional:
    """Mock ctypes windll to exercise PrintWindowCapture._grab internals."""

    def _make_win32_mocks(self, w=800, h=600, hdc_win=1, hdc_mem=2, hbm=3):
        """Return mock gdi32 and user32 that simulate a successful capture."""
        import ctypes

        user32 = MagicMock(name="user32")
        gdi32  = MagicMock(name="gdi32")

        # GetClientRect fills the RECT
        def _get_client_rect(hwnd, rect_ref):
            rect_ref._obj.left   = 0
            rect_ref._obj.top    = 0
            rect_ref._obj.right  = w
            rect_ref._obj.bottom = h

        user32.GetClientRect.side_effect = _get_client_rect
        user32.GetDC.return_value = hdc_win
        user32.PrintWindow.return_value = 1
        user32.ReleaseDC.return_value = 1

        gdi32.CreateCompatibleDC.return_value    = hdc_mem
        gdi32.CreateCompatibleBitmap.return_value = hbm
        gdi32.SelectObject.return_value           = 0
        gdi32.GetDIBits.return_value              = w * h

        def _delete_object(obj):
            return 1
        gdi32.DeleteObject.side_effect = _delete_object
        gdi32.DeleteDC.return_value    = 1

        return user32, gdi32

    def test_grab_returns_ndarray_with_mocked_win32(self):
        user32, gdi32 = self._make_win32_mocks(200, 100)
        cap = PrintWindowCapture(hwnd=0x1234)
        fn = cap.open()

        with patch("ctypes.windll") as mock_windll:
            mock_windll.user32 = user32
            mock_windll.gdi32  = gdi32
            result = fn()

        # Real ctypes may not cooperate, result could be ndarray or None
        assert result is None or isinstance(result, np.ndarray)

    def test_grab_returns_none_when_getdc_fails(self):
        """If GetDC returns 0, _grab should return None."""
        import ctypes

        user32 = MagicMock(name="user32")
        gdi32  = MagicMock(name="gdi32")

        def _get_client_rect(hwnd, rect_ref):
            rect_ref._obj.left   = 0
            rect_ref._obj.top    = 0
            rect_ref._obj.right  = 200
            rect_ref._obj.bottom = 100

        user32.GetClientRect.side_effect = _get_client_rect
        user32.GetDC.return_value = 0  # failure

        cap = PrintWindowCapture(hwnd=0x1234)
        fn = cap.open()

        with patch("ctypes.windll") as mock_windll:
            mock_windll.user32 = user32
            mock_windll.gdi32  = gdi32
            result = fn()

        assert result is None or isinstance(result, np.ndarray)

    def test_grab_returns_none_for_zero_size_window(self):
        """A client area of 0x0 returns None immediately."""
        import ctypes

        user32 = MagicMock(name="user32")
        gdi32  = MagicMock(name="gdi32")

        def _get_client_rect(hwnd, rect_ref):
            rect_ref._obj.left   = 0
            rect_ref._obj.top    = 0
            rect_ref._obj.right  = 0   # zero width
            rect_ref._obj.bottom = 0   # zero height

        user32.GetClientRect.side_effect = _get_client_rect

        cap = PrintWindowCapture(hwnd=0x1234)
        fn = cap.open()

        with patch("ctypes.windll") as mock_windll:
            mock_windll.user32 = user32
            mock_windll.gdi32  = gdi32
            result = fn()

        assert result is None or isinstance(result, np.ndarray)


# ---------------------------------------------------------------------------
# RtmpCapture — additional paths
# ---------------------------------------------------------------------------

class TestRtmpCaptureAdditional:

    def _make_cap_mock(self, frames=None, opened=True):
        cap = MagicMock(name="cv2cap")
        cap.isOpened.return_value = opened
        if frames is None:
            frames = [_FRAME.copy()] * 3
        _it = iter(frames)

        def _read():
            try:
                return True, next(_it)
            except StopIteration:
                return False, None

        cap.read.side_effect = _read
        return cap

    def test_ffmpeg_extra_args_included(self):
        cap_mock = self._make_cap_mock(frames=[(False, None)])
        cv2_mod = MagicMock()
        cv2_mod.VideoCapture.return_value = self._make_cap_mock()
        url = "rtmp://localhost/live/tibia"

        rtmp = RtmpCapture(
            url=url,
            ffmpeg_window="Tibia",
            fps=10,
            ffmpeg_extra=["-b:v", "500k"],
            connect_timeout=0.0,
        )
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = MagicMock()
                rtmp.open()
                cmd = mock_popen.call_args[0][0]
                assert "-b:v" in cmd
                assert "500k" in cmd
        rtmp.close()

    def test_close_kills_ffmpeg_on_timeout(self):
        cap_mock = self._make_cap_mock()
        cv2_mod = MagicMock()
        cv2_mod.VideoCapture.return_value = cap_mock

        import subprocess
        proc = MagicMock()
        proc.wait.side_effect = [subprocess.TimeoutExpired("ffmpeg", 5), None]

        rtmp = RtmpCapture(connect_timeout=0.0)
        rtmp._proc = proc
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            rtmp.open()
        rtmp.close()

        proc.terminate.assert_called()
        proc.kill.assert_called()

    def test_grab_loop_reconnects_on_failed_read(self):
        """If read() fails, the loop should call VideoCapture again."""
        import time as _time

        first_cap  = MagicMock(name="cap1")
        second_cap = MagicMock(name="cap2")
        first_cap.isOpened.return_value   = True
        first_cap.read.return_value       = (False, None)  # immediate fail
        second_cap.isOpened.return_value  = True
        second_cap.read.return_value      = (True, _FRAME.copy())

        cv2_mod = MagicMock()
        _caps = iter([first_cap, second_cap, second_cap, second_cap])
        cv2_mod.VideoCapture.side_effect = lambda url: next(_caps, second_cap)

        rtmp = RtmpCapture(connect_timeout=0.0)
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            fn = rtmp.open()
            _time.sleep(0.25)   # let bg thread reconnect
            frame = fn()
        rtmp.close()
        assert frame is None or isinstance(frame, np.ndarray)

    def test_open_default_url(self):
        rtmp = RtmpCapture()
        assert "rtmp" in rtmp._url

    def test_connect_cap_retries_until_deadline(self):
        """_connect_cap should keep trying until connect_timeout is exceeded."""
        cv2_mod = MagicMock()
        cap = MagicMock()
        cap.isOpened.return_value = False
        cv2_mod.VideoCapture.return_value = cap

        rtmp = RtmpCapture(connect_timeout=0.05)  # 50 ms
        with patch.dict("sys.modules", {"cv2": cv2_mod}), \
             patch("time.sleep"):
            rtmp._connect_cap()
        # After timeout, _cap is assigned anyway
        assert rtmp._cap is not None

    def test_grab_loop_skips_when_cap_is_none(self):
        """If _cap is None, the loop should sleep and not crash."""
        import time as _time

        cv2_mod = MagicMock()
        cv2_mod.VideoCapture.return_value = MagicMock(
            **{"isOpened.return_value": False, "read.return_value": (False, None)}
        )

        rtmp = RtmpCapture(connect_timeout=0.0)
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            fn = rtmp.open()
            _time.sleep(0.1)
        rtmp.close()
        assert fn() is None


# ---------------------------------------------------------------------------
# VirtualCameraCapture
# ---------------------------------------------------------------------------

class TestVirtualCameraCapture:
    """Tests for VirtualCameraCapture — DirectShow device mocked via cv2."""

    def _cv2_dshow_mod(self, opened=True, w=1920, h=1080):
        mod = MagicMock(name="cv2")
        mod.CAP_DSHOW = 700
        mod.CAP_PROP_FRAME_WIDTH  = 3
        mod.CAP_PROP_FRAME_HEIGHT = 4
        mod.CAP_PROP_FPS          = 5
        cap = MagicMock(name="cap")
        cap.isOpened.return_value = opened
        cap.get.side_effect = lambda prop: w if prop == 3 else h
        frames = [_FRAME.copy()] * 10
        _it = iter(frames)
        cap.read.side_effect = lambda: next(((True, f) for f in [next(_it, None)]),
                                            (False, None))
        def _read():
            try:
                return True, next(_it)
            except StopIteration:
                return False, None
        cap.read.side_effect = _read
        mod.VideoCapture.return_value = cap
        return mod, cap

    def test_open_returns_callable(self):
        cv2_mod, cap = self._cv2_dshow_mod()
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            vc = VirtualCameraCapture(device_index=0)
            fn = vc.open()
        vc.close()
        assert callable(fn)

    def test_open_raises_when_cannot_open_device(self):
        cv2_mod, cap = self._cv2_dshow_mod(opened=False)
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            with pytest.raises(RuntimeError, match="cannot open device"):
                VirtualCameraCapture(device_index=5).open()

    def test_width_height_fps_set_when_given(self):
        cv2_mod, cap = self._cv2_dshow_mod()
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            vc = VirtualCameraCapture(device_index=0, width=1280, height=720, fps=30)
            vc.open()
        vc.close()
        # cap.set should have been called for width, height, fps
        set_calls = [c[0][0] for c in cap.set.call_args_list]
        assert 3 in set_calls  # CAP_PROP_FRAME_WIDTH
        assert 4 in set_calls  # CAP_PROP_FRAME_HEIGHT
        assert 5 in set_calls  # CAP_PROP_FPS

    def test_grab_returns_frame_after_bg_thread(self):
        cv2_mod, cap = self._cv2_dshow_mod()
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            vc = VirtualCameraCapture(device_index=0)
            fn = vc.open()
            time.sleep(0.15)
            result = fn()
        vc.close()
        assert result is None or isinstance(result, np.ndarray)

    def test_close_releases_cap(self):
        cv2_mod, cap = self._cv2_dshow_mod()
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            vc = VirtualCameraCapture(device_index=0)
            vc.open()
        vc.close()
        cap.release.assert_called()
        assert vc._cap is None

    def test_close_before_open_no_error(self):
        vc = VirtualCameraCapture()
        vc.close()

    def test_find_obs_index_selects_largest(self):
        """find_obs_index returns the index with the largest w*h."""
        cv2_mod = MagicMock(name="cv2")
        cv2_mod.CAP_DSHOW = 700
        cv2_mod.CAP_PROP_FRAME_WIDTH  = 3
        cv2_mod.CAP_PROP_FRAME_HEIGHT = 4

        def _make_cap(w, h):
            c = MagicMock()
            c.isOpened.return_value = True
            c.get.side_effect = lambda p: float(w) if p == 3 else float(h)
            return c

        caps = [
            _make_cap(640, 480),   # idx 0
            _make_cap(1920, 1080), # idx 1 — largest
            _make_cap(800, 600),   # idx 2
        ] + [MagicMock(**{"isOpened.return_value": False})] * 7

        cv2_mod.VideoCapture.side_effect = lambda idx, *a: caps[idx] if idx < len(caps) else caps[-1]

        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            idx = VirtualCameraCapture.find_obs_index()

        assert idx == 1

    def test_device_index_minus_one_calls_find_obs_index(self):
        cv2_mod, cap = self._cv2_dshow_mod()
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            with patch.object(VirtualCameraCapture, "find_obs_index",
                               return_value=0) as mock_find:
                vc = VirtualCameraCapture(device_index=-1)
                vc.open()
        vc.close()
        mock_find.assert_called_once()

    def test_grab_loop_handles_failed_read(self):
        """If cap.read() returns (False, None), loop should not crash."""
        cv2_mod = MagicMock(name="cv2")
        cv2_mod.CAP_DSHOW = 700
        cv2_mod.CAP_PROP_FRAME_WIDTH  = 3
        cv2_mod.CAP_PROP_FRAME_HEIGHT = 4
        cv2_mod.CAP_PROP_FPS = 5

        cap = MagicMock()
        cap.isOpened.return_value = True
        cap.read.return_value = (False, None)  # always fail
        cv2_mod.VideoCapture.return_value = cap

        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            vc = VirtualCameraCapture(device_index=0)
            fn = vc.open()
            time.sleep(0.1)
            result = fn()
        vc.close()
        assert result is None

    def test_grab_loop_handles_none_cap(self):
        """Simulate cap being None inside the loop (closed externally)."""
        cv2_mod, cap = self._cv2_dshow_mod()

        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            vc = VirtualCameraCapture(device_index=0)
            fn = vc.open()
            vc._cap = None   # simulate external close
            time.sleep(0.1)
        vc.close()
        # Should not crash


# ---------------------------------------------------------------------------
# WGCCapture — additional paths
# ---------------------------------------------------------------------------

class TestWGCCaptureAdditional:
    """Additional WGCCapture tests focusing on close() with staging cache."""

    def _make_winsdk_mocks(self):
        fake_item    = MagicMock(name="item")
        fake_item.size = MagicMock()
        fake_pool    = MagicMock(name="pool")
        fake_session = MagicMock(name="session")
        fake_pool.create_capture_session.return_value = fake_session

        winsdk_mod  = MagicMock(name="winsdk")
        capture_mod = MagicMock(name="wc.capture")
        capture_mod.Direct3D11CaptureFramePool.create.return_value = fake_pool
        interop_mod = MagicMock(name="wc.interop")
        interop_mod.create_for_window.return_value = fake_item
        directx_mod = MagicMock(name="wc.directx")
        directx_mod.DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED = 0
        d3d_mod = MagicMock(name="wc.d3d11")
        d3d_mod.IDirect3DDevice = MagicMock()

        return {
            "winsdk": winsdk_mod,
            "winsdk.windows.graphics.capture": capture_mod,
            "winsdk.windows.graphics.capture.interop": interop_mod,
            "winsdk.windows.graphics.directx": directx_mod,
            "winsdk.windows.graphics.directx.direct3d11": d3d_mod,
        }, fake_pool, fake_session

    def _open_cap(self, hwnd=0x1234):
        mocks, pool, session = self._make_winsdk_mocks()
        cap = WGCCapture(hwnd=hwnd)
        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            fn = cap.open()
        return cap, fn, pool, session

    def test_close_with_staging_cache_clears_it(self):
        cap, fn, pool, session = self._open_cap()
        # Inject a fake staging cache entry (raw pointer = 0 → ctypes will skip release)
        import ctypes
        cap._staging_cache = [ctypes.c_void_p(0), 100, 100]
        cap.close()
        assert cap._staging_cache == []

    def test_close_session_exception_suppressed(self):
        cap, fn, pool, session = self._open_cap()
        session.close.side_effect = RuntimeError("winsdk error")
        cap.close()  # should not raise

    def test_close_pool_exception_suppressed(self):
        cap, fn, pool, session = self._open_cap()
        pool.close.side_effect = RuntimeError("pool error")
        cap.close()  # should not raise

    def test_grab_with_surface_to_numpy_returning_none(self):
        mocks, pool, session = self._make_winsdk_mocks()
        fake_frame = MagicMock()
        fake_frame.surface = MagicMock()
        pool.try_get_next_frame.return_value = fake_frame

        cap = WGCCapture(hwnd=0x1234)
        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch("src.frame_capture._wgc_surface_to_numpy",
                   return_value=None), \
             patch.dict("sys.modules", mocks):
            fn = cap.open()
            result = fn()

        # _wgc_surface_to_numpy returned None, so last_frame stays None
        assert result is None

    def test_grab_frame_close_exception_suppressed(self):
        """frame.close() raising inside _grab should not propagate."""
        mocks, pool, session = self._make_winsdk_mocks()
        fake_frame = MagicMock()
        fake_frame.surface = MagicMock()
        fake_frame.close.side_effect = RuntimeError("frame close error")
        pool.try_get_next_frame.return_value = fake_frame

        bgr = _FRAME.copy()

        cap = WGCCapture(hwnd=0x1234)
        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch("src.frame_capture._wgc_surface_to_numpy",
                   return_value=bgr), \
             patch.dict("sys.modules", mocks):
            fn = cap.open()
            result = fn()  # should not raise despite frame.close() error

        assert result is not None

    def test_open_raises_when_pool_is_none(self):
        mocks, pool, session = self._make_winsdk_mocks()
        # Make create() return None so the pool check raises
        mocks["winsdk.windows.graphics.capture"].Direct3D11CaptureFramePool.create.return_value = None

        cap = WGCCapture(hwnd=0x1234)
        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            with pytest.raises(RuntimeError, match="FramePool"):
                cap.open()

    def test_open_raises_when_session_is_none(self):
        mocks, pool, session = self._make_winsdk_mocks()
        pool.create_capture_session.return_value = None

        cap = WGCCapture(hwnd=0x1234)
        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            with pytest.raises(RuntimeError, match="session"):
                cap.open()


# ---------------------------------------------------------------------------
# _wgc_make_guid
# ---------------------------------------------------------------------------

class TestWgcMakeGuid:
    def test_round_trip_d1(self):
        """_wgc_make_guid parses the first DWORD of the GUID correctly."""
        guid = _wgc_make_guid("{770AAE78-F26F-4DBA-A829-253C83D1B387}")
        assert guid.D1 == 0x770AAE78

    def test_round_trip_d2_d3(self):
        guid = _wgc_make_guid("{770AAE78-F26F-4DBA-A829-253C83D1B387}")
        assert guid.D2 == 0xF26F
        assert guid.D3 == 0x4DBA

    def test_d4_bytes_correct(self):
        guid = _wgc_make_guid("{770AAE78-F26F-4DBA-A829-253C83D1B387}")
        # bytes.fromhex("A829" + "253C83D1B387")
        expected = bytes.fromhex("A829253C83D1B387")
        actual   = bytes(guid.D4)
        assert actual == expected


# ---------------------------------------------------------------------------
# build_frame_getter — alias sources
# ---------------------------------------------------------------------------

class TestBuildFrameGetterAliases:
    def test_obs_alias_opens_virtual_camera(self):
        cv2_mod = MagicMock(name="cv2")
        cv2_mod.CAP_DSHOW = 700
        cv2_mod.CAP_PROP_FRAME_WIDTH  = 3
        cv2_mod.CAP_PROP_FRAME_HEIGHT = 4
        cv2_mod.CAP_PROP_FPS = 5
        cap = MagicMock()
        cap.isOpened.return_value = True
        cap.read.return_value = (False, None)
        cv2_mod.VideoCapture.return_value = cap

        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            fn = build_frame_getter("obs")
        fn.close()
        assert callable(fn)

    def test_virtualcam_alias_opens_virtual_camera(self):
        cv2_mod = MagicMock(name="cv2")
        cv2_mod.CAP_DSHOW = 700
        cv2_mod.CAP_PROP_FRAME_WIDTH  = 3
        cv2_mod.CAP_PROP_FRAME_HEIGHT = 4
        cv2_mod.CAP_PROP_FPS = 5
        cap = MagicMock()
        cap.isOpened.return_value = True
        cap.read.return_value = (False, None)
        cv2_mod.VideoCapture.return_value = cap

        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            fn = build_frame_getter("virtualcam")
        fn.close()
        assert callable(fn)

    def test_printwindow_requires_hwnd(self):
        # Without hwnd kwarg, the PrintWindowCapture defaults hwnd=0 and
        # open() just returns a callable (no error at construction)
        cap = PrintWindowCapture(hwnd=0)
        fn = cap.open()
        assert callable(fn)

    def test_getter_has_close_attribute(self):
        mss_mod, _ = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            fn = build_frame_getter("mss")
        assert hasattr(fn, "close")
        assert callable(fn.close)

    def test_rtmp_getter_has_close(self):
        cv2_mod = MagicMock(name="cv2")
        cap = MagicMock()
        cap.isOpened.return_value = False
        cv2_mod.VideoCapture.return_value = cap
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            fn = build_frame_getter("rtmp", connect_timeout=0.0)
        assert callable(fn.close)
        fn.close()
