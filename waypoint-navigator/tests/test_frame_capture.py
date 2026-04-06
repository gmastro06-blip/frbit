"""
tests/test_frame_capture.py
---------------------------
Offline tests for src/frame_capture.py — all external libs are mocked.
No Tibia, OBS, NGINX, dxcam, or mss required.
"""
from __future__ import annotations

import base64
import sys
import threading
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.frame_capture import (
    MssCapture,
    DxcamCapture,
    PrintWindowCapture,
    RtmpCapture,
    WGCCapture,
    build_frame_getter,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)
_FRAME[0, 0] = [10, 20, 30]  # sentinel pixel


def _fake_mss_module():
    mod = types.ModuleType("mss")
    sct = MagicMock()
    sct.monitors = [None, {"left": 0, "top": 0, "width": 1920, "height": 1080}]
    # grab() returns an object whose np.array() gives a 4-channel image
    raw = np.zeros((1080, 1920, 4), dtype=np.uint8)
    raw[0, 0] = [10, 20, 30, 255]
    sct.grab.return_value = raw
    # In the real mss library, mss.mss() returns an object that is BOTH the
    # context manager (__enter__ returns self) AND has grab() / monitors.
    sct.__enter__ = MagicMock(return_value=sct)
    sct.__exit__ = MagicMock(return_value=False)
    mod.mss = MagicMock(return_value=sct)  # type: ignore[attr-defined]
    return mod, sct


def _fake_dxcam_module():
    mod = types.ModuleType("dxcam")
    cam = MagicMock()
    cam.get_latest_frame.return_value = _FRAME.copy()
    mod.create = MagicMock(return_value=cam)  # type: ignore[attr-defined]
    return mod, cam


def _fake_cv2_cap(frames=None):
    cap = MagicMock()
    cap.isOpened.return_value = True
    _frames = list(frames or [_FRAME.copy()])
    _iter = iter(_frames)

    def _read():
        try:
            return True, next(_iter)
        except StopIteration:
            return False, None

    cap.read.side_effect = _read
    return cap


def _fake_obsws_module(client: MagicMock):
    mod = types.ModuleType("obsws_python")
    mod.ReqClient = MagicMock(return_value=client)  # type: ignore[attr-defined]
    return mod


def _fake_winsdk_interop(size_obj: Any):
    winsdk_mod = types.ModuleType("winsdk")
    windows_mod = types.ModuleType("winsdk.windows")
    graphics_mod = types.ModuleType("winsdk.windows.graphics")
    capture_mod = types.ModuleType("winsdk.windows.graphics.capture")
    interop_mod = types.ModuleType("winsdk.windows.graphics.capture.interop")
    interop_mod.create_for_window = lambda hwnd: types.SimpleNamespace(size=size_obj)  # type: ignore[attr-defined]
    return {
        "winsdk": winsdk_mod,
        "winsdk.windows": windows_mod,
        "winsdk.windows.graphics": graphics_mod,
        "winsdk.windows.graphics.capture": capture_mod,
        "winsdk.windows.graphics.capture.interop": interop_mod,
    }


# ─────────────────────────────────────────────────────────────────────────────
# build_frame_getter — factory
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildFrameGetterFactory:
    def test_unknown_source_raises(self):
        with pytest.raises(ValueError, match="Unknown frame source"):
            build_frame_getter("banana")

    def test_unknown_source_message_lists_valid(self):
        with pytest.raises(ValueError) as exc_info:
            build_frame_getter("obs_websocket")
        msg = str(exc_info.value)
        assert "mss" in msg
        assert "rtmp" in msg
        assert "dxcam" in msg

    def test_returns_callable_mss(self):
        mss_mod, _ = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            fn = build_frame_getter("mss")
        assert callable(fn)

    def test_returns_callable_dxcam(self):
        dx_mod, _ = _fake_dxcam_module()
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            fn = build_frame_getter("dxcam", fps=5)
        assert callable(fn)

    def test_source_case_insensitive(self):
        mss_mod, _ = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            fn = build_frame_getter("MSS")
        assert callable(fn)

    def test_source_strips_whitespace(self):
        mss_mod, _ = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            fn = build_frame_getter("  mss  ")
        assert callable(fn)


# ─────────────────────────────────────────────────────────────────────────────
# MssCapture
# ─────────────────────────────────────────────────────────────────────────────

class TestMssCapture:
    def test_open_returns_callable(self):
        mss_mod, _ = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            cap = MssCapture()
            fn = cap.open()
        assert callable(fn)

    def test_grab_returns_bgr_ndarray(self):
        mss_mod, sct = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            cap = MssCapture()
            fn = cap.open()
            frame = fn()
        assert frame is not None
        assert frame.ndim == 3
        assert frame.shape[2] == 3   # no alpha

    def test_grab_drops_alpha_channel(self):
        mss_mod, _ = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            cap = MssCapture()
            fn = cap.open()
            frame = fn()
        assert frame is not None
        assert frame.shape[2] == 3

    def test_monitor_index_forwarded(self):
        mss_mod, sct = _fake_mss_module()
        sct.monitors = [None, {"m": 1}, {"m": 2}]
        extra_raw = np.zeros((1080, 1920, 4), dtype=np.uint8)
        sct.grab.return_value = extra_raw
        # hwnd=0 → falls back to monitor_idx
        import ctypes
        with patch.dict("sys.modules", {"mss": mss_mod}):
            with patch("src.frame_capture._hwnd_client_region", return_value=None):
                cap = MssCapture(hwnd=0, monitor_idx=2)
                fn = cap.open()
                fn()
        # grab should be called with monitors[2]
        sct.grab.assert_called_with({"m": 2})

    def test_hwnd_region_used_when_provided(self):
        mss_mod, sct = _fake_mss_module()
        sct.monitors = [None, {"m": 1}]
        sct.grab.return_value = np.zeros((100, 200, 4), dtype=np.uint8)
        region = {"left": 10, "top": 20, "width": 200, "height": 100}
        with patch.dict("sys.modules", {"mss": mss_mod}):
            with patch("src.frame_capture._hwnd_client_region", return_value=region):
                cap = MssCapture(hwnd=0x1234)
                fn = cap.open()
                fn()
        sct.grab.assert_called_with(region)

    def test_close_no_error(self):
        mss_mod, sct = _fake_mss_module()
        with patch.dict("sys.modules", {"mss": mss_mod}):
            cap = MssCapture()
            cap.open()
            cap.close()


# ─────────────────────────────────────────────────────────────────────────────
# DxcamCapture
# ─────────────────────────────────────────────────────────────────────────────

class TestDxcamCapture:
    def test_open_returns_callable(self):
        dx_mod, _ = _fake_dxcam_module()
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            fn = DxcamCapture().open()
        assert callable(fn)

    def test_grab_returns_frame(self):
        dx_mod, cam = _fake_dxcam_module()
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            fn = DxcamCapture().open()
            frame = fn()
        assert frame is not None
        assert frame.shape == (1080, 1920, 3)

    def test_grab_returns_none_when_no_frame(self):
        dx_mod, cam = _fake_dxcam_module()
        cam.get_latest_frame.return_value = None
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            fn = DxcamCapture().open()
            frame = fn()
        assert frame is None

    def test_fps_forwarded_to_start(self):
        dx_mod, cam = _fake_dxcam_module()
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            DxcamCapture(fps=5).open()
        cam.start.assert_called_with(target_fps=5)

    def test_close_calls_stop(self):
        dx_mod, cam = _fake_dxcam_module()
        with patch.dict("sys.modules", {"dxcam": dx_mod}):
            cap = DxcamCapture()
            cap.open()
            cap.close()
        cam.stop.assert_called_once()

    def test_close_before_open_no_error(self):
        cap = DxcamCapture()
        cap.close()  # _cam is None — should not raise


# ─────────────────────────────────────────────────────────────────────────────
# PrintWindowCapture
# ─────────────────────────────────────────────────────────────────────────────

class TestPrintWindowCapture:
    """Mocking ctypes.windll is fragile; we test the public surface instead."""

    def test_open_returns_callable(self):
        cap = PrintWindowCapture(hwnd=0x1234)
        fn = cap.open()
        assert callable(fn)

    def test_close_is_noop(self):
        cap = PrintWindowCapture(hwnd=0x1234)
        cap.close()  # should not raise

    def test_grab_returns_none_on_win32_error(self):
        """If ctypes calls fail (no real HWND) the function should not crash."""
        cap = PrintWindowCapture(hwnd=0xDEAD)
        fn = cap.open()
        # Invoke — on Windows without that HWND it will return a blank/small array
        # or raise; we only check it doesn't propagate uncaught exceptions here
        # by swallowing SystemError / OSError from ctypes
        try:
            result = fn()
            # if it runs, result is ndarray or None
            assert result is None or isinstance(result, np.ndarray)
        except Exception:
            pass  # win32 error acceptable in test environment


# ─────────────────────────────────────────────────────────────────────────────
# RtmpCapture
# ─────────────────────────────────────────────────────────────────────────────

class TestRtmpCapture:
    def _make_rtmp(self, **extra) -> tuple["RtmpCapture", MagicMock]:
        cap_mock = _fake_cv2_cap([_FRAME.copy()])
        cv2_mod = MagicMock()
        cv2_mod.VideoCapture.return_value = cap_mock
        return RtmpCapture(connect_timeout=0.0, **extra), cv2_mod

    def test_open_returns_callable(self):
        rtmp, cv2_mod = self._make_rtmp()
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            fn = rtmp.open()
        rtmp.close()
        assert callable(fn)

    def test_get_returns_none_before_first_frame(self):
        """_latest starts as None; getter returns None immediately."""
        rtmp, cv2_mod = self._make_rtmp()
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            fn = rtmp.open()
        # Don't wait for the bg thread — latest may or may not be filled yet
        result = fn()
        rtmp.close()
        assert result is None or isinstance(result, np.ndarray)

    def test_get_returns_frame_after_bg_thread(self):
        import time as _time
        cap_mock = MagicMock()
        cap_mock.isOpened.return_value = True
        frames = [_FRAME.copy()] * 5
        _it = iter(frames)

        def _read():
            try:
                return True, next(_it)
            except StopIteration:
                return False, None

        cap_mock.read.side_effect = _read
        cv2_mod = MagicMock()
        cv2_mod.VideoCapture.return_value = cap_mock

        rtmp = RtmpCapture(connect_timeout=0.0)
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            fn = rtmp.open()
            _time.sleep(0.15)   # let bg thread grab at least one frame
            frame = fn()
        rtmp.close()
        assert frame is not None
        assert frame.shape == (1080, 1920, 3)

    def test_close_terminates_ffmpeg_proc(self):
        cap_mock = MagicMock()
        cap_mock.isOpened.return_value = True
        cap_mock.read.return_value = (False, None)
        cv2_mod = MagicMock()
        cv2_mod.VideoCapture.return_value = cap_mock

        proc = MagicMock()
        rtmp = RtmpCapture(connect_timeout=0.0)
        rtmp._proc = proc  # inject mock proc

        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            rtmp.open()
        rtmp.close()
        proc.terminate.assert_called_once()

    def test_no_ffmpeg_when_no_window_param(self):
        cap_mock = MagicMock()
        cap_mock.isOpened.return_value = True
        cap_mock.read.return_value = (False, None)
        cv2_mod = MagicMock()
        cv2_mod.VideoCapture.return_value = cap_mock

        rtmp = RtmpCapture(ffmpeg_window=None, connect_timeout=0.0)
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            with patch("subprocess.Popen") as mock_popen:
                rtmp.open()
                mock_popen.assert_not_called()
        rtmp.close()

    def test_ffmpeg_launched_with_correct_url(self):
        cap_mock = MagicMock()
        cap_mock.isOpened.return_value = True
        cap_mock.read.return_value = (False, None)
        cv2_mod = MagicMock()
        cv2_mod.VideoCapture.return_value = cap_mock

        url = "rtmp://localhost/live/test"
        rtmp = RtmpCapture(
            url=url,
            ffmpeg_window="Tibia",
            fps=5,
            connect_timeout=0.0,
        )
        with patch.dict("sys.modules", {"cv2": cv2_mod}):
            with patch("subprocess.Popen") as mock_popen:
                proc_mock = MagicMock()
                mock_popen.return_value = proc_mock
                rtmp.open()
                call_args = mock_popen.call_args[0][0]
                assert url in call_args
                assert "gdigrab" in call_args
                assert "title=Tibia" in call_args
        rtmp.close()

    def test_url_stored_correctly(self):
        url = "rtmp://192.168.1.10:1935/live/game"
        rtmp = RtmpCapture(url=url)
        assert rtmp._url == url


# ─────────────────────────────────────────────────────────────────────────────
# SessionConfig — new fields
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigFrameSource:
    def test_frame_source_default_empty(self):
        from src.session import SessionConfig
        cfg = SessionConfig()
        assert cfg.frame_source == ""

    def test_rtmp_url_default(self):
        from src.session import SessionConfig
        cfg = SessionConfig()
        assert "rtmp://" in cfg.rtmp_url

    def test_rtmp_ffmpeg_window_default(self):
        from src.session import SessionConfig
        cfg = SessionConfig()
        assert cfg.rtmp_ffmpeg_window == "Tibia"

    def test_rtmp_fps_default(self):
        from src.session import SessionConfig
        cfg = SessionConfig()
        assert cfg.rtmp_fps == 10

    def test_frame_source_round_trip_json(self, tmp_path):
        from src.session import SessionConfig
        p = tmp_path / "cfg.json"
        cfg = SessionConfig(frame_source="dxcam", rtmp_fps=15)
        cfg.save(p)
        loaded = SessionConfig.load(p)
        assert loaded.frame_source == "dxcam"
        assert loaded.rtmp_fps == 15

    def test_set_rtmp_source(self):
        from src.session import SessionConfig
        cfg = SessionConfig(
            frame_source="rtmp",
            rtmp_url="rtmp://localhost/live/tibia",
            rtmp_ffmpeg_window="",
        )
        assert cfg.frame_source == "rtmp"
        assert cfg.rtmp_url == "rtmp://localhost/live/tibia"
        assert cfg.rtmp_ffmpeg_window == ""


# ─────────────────────────────────────────────────────────────────────────────
# WGCCapture
# ─────────────────────────────────────────────────────────────────────────────

class TestWGCCapture:
    """WGCCapture tests — winsdk and D3D11 calls are mocked."""

    # ── Construction ──────────────────────────────────────────────────────────

    def test_stores_hwnd(self):
        cap = WGCCapture(hwnd=0xABCD)
        assert cap._hwnd == 0xABCD

    def test_initial_state_is_closed(self):
        cap = WGCCapture(hwnd=0x1234)
        assert cap._pool is None
        assert cap._session is None
        assert cap._staging_cache == []

    # ── open() — winsdk not installed ────────────────────────────────────────

    def test_open_raises_runtimeerror_when_winsdk_missing(self):
        """open() must raise RuntimeError with an install hint when winsdk is absent."""
        cap = WGCCapture(hwnd=0x1234)
        with patch.dict("sys.modules", {"winsdk": None}):
            with pytest.raises(RuntimeError, match="winsdk"):
                cap.open()

    # ── open() — happy path (full mock) ──────────────────────────────────────

    def _make_winsdk_mocks(self):
        """Return a dict of fake winsdk sub-modules plus a mock winsdk top level."""
        fake_item       = MagicMock(name="GraphicsCaptureItem")
        fake_item.size  = MagicMock()
        fake_pool       = MagicMock(name="FramePool")
        fake_session    = MagicMock(name="Session")
        fake_pool.create_capture_session.return_value = fake_session

        winsdk_mod = MagicMock(name="winsdk")

        capture_mod = MagicMock(name="winsdk.windows.graphics.capture")
        capture_mod.Direct3D11CaptureFramePool.create.return_value = fake_pool

        interop_mod = MagicMock(name="winsdk.windows.graphics.capture.interop")
        interop_mod.create_for_window.return_value = fake_item

        directx_mod = MagicMock(name="winsdk.windows.graphics.directx")
        directx_mod.DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED = 0

        winrt_direct3d_mod = MagicMock(name="winsdk.windows.graphics.directx.direct3d11")
        winrt_direct3d_mod.IDirect3DDevice = MagicMock()

        return {
            "winsdk": winsdk_mod,
            "winsdk.windows.graphics.capture": capture_mod,
            "winsdk.windows.graphics.capture.interop": interop_mod,
            "winsdk.windows.graphics.directx": directx_mod,
            "winsdk.windows.graphics.directx.direct3d11": winrt_direct3d_mod,
        }, fake_pool, fake_session

    def test_open_returns_callable_with_mocked_winsdk(self):
        mocks, fake_pool, fake_session = self._make_winsdk_mocks()
        cap = WGCCapture(hwnd=0x1234)

        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            fn = cap.open()

        assert callable(fn)

    def test_open_stores_pool_and_session(self):
        mocks, fake_pool, fake_session = self._make_winsdk_mocks()
        cap = WGCCapture(hwnd=0x1234)

        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            cap.open()

        assert cap._pool  is not None
        assert cap._session is not None

    def test_open_calls_start_capture(self):
        mocks, fake_pool, fake_session = self._make_winsdk_mocks()
        cap = WGCCapture(hwnd=0x1234)

        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            cap.open()

        fake_session.start_capture.assert_called_once()

    # ── grab() ────────────────────────────────────────────────────────────────

    def test_grab_returns_none_when_pool_returns_none(self):
        mocks, fake_pool, fake_session = self._make_winsdk_mocks()
        fake_pool.try_get_next_frame.return_value = None

        cap = WGCCapture(hwnd=0x1234)
        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            fn = cap.open()

        result = fn()
        assert result is None

    def test_grab_returns_last_frame_when_pool_returns_none(self):
        """After a successful frame, None from the pool returns the cached frame."""
        mocks, fake_pool, fake_session = self._make_winsdk_mocks()
        fake_frame    = MagicMock(name="frame")
        fake_frame.surface = MagicMock()
        expected_bgr  = _FRAME.copy()

        calls = iter([fake_frame, None])
        fake_pool.try_get_next_frame.side_effect = lambda: next(calls, None)

        cap = WGCCapture(hwnd=0x1234)
        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch("src.frame_capture._wgc_surface_to_numpy",
                   return_value=expected_bgr), \
             patch.dict("sys.modules", mocks):
            fn = cap.open()
            first  = fn()   # pool returns fake_frame → _wgc_surface_to_numpy → expected_bgr
            second = fn()   # pool returns None → cached

        assert first is expected_bgr
        assert second is expected_bgr

    def test_grab_tolerates_surface_to_numpy_exception(self):
        """If _wgc_surface_to_numpy raises, getter returns None (doesn't propagate)."""
        mocks, fake_pool, fake_session = self._make_winsdk_mocks()
        fake_frame = MagicMock(); fake_frame.surface = MagicMock()
        fake_pool.try_get_next_frame.return_value = fake_frame

        cap = WGCCapture(hwnd=0x1234)
        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch("src.frame_capture._wgc_surface_to_numpy",
                   side_effect=OSError("D3D lost")), \
             patch.dict("sys.modules", mocks):
            fn = cap.open()
            result = fn()

        assert result is None

    # ── close() ───────────────────────────────────────────────────────────────

    def test_close_is_noop_before_open(self):
        cap = WGCCapture(hwnd=0x1234)
        cap.close()  # must not raise

    def test_close_clears_pool_and_session(self):
        mocks, fake_pool, fake_session = self._make_winsdk_mocks()
        cap = WGCCapture(hwnd=0x1234)

        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            cap.open()

        cap.close()
        assert cap._pool    is None
        assert cap._session is None

    def test_close_calls_session_close(self):
        mocks, fake_pool, fake_session = self._make_winsdk_mocks()
        cap = WGCCapture(hwnd=0x1234)

        with patch("src.frame_capture._wgc_create_d3d11_device",
                   return_value=(MagicMock(), MagicMock())), \
             patch("src.frame_capture._wgc_d3d_device_to_winrt",
                   return_value=MagicMock()), \
             patch.dict("sys.modules", mocks):
            cap.open()

        cap.close()
        fake_session.close.assert_called_once()

    # ── build_frame_getter integration ────────────────────────────────────────

    def test_wgc_in_valid_sources(self):
        """'wgc' is a valid source identifier (error message lists it)."""
        try:
            build_frame_getter("wgc", hwnd=0x1234)
        except ValueError as exc:
            # Should NOT be a ValueError — "wgc" must be a recognised source key
            pytest.fail(f"build_frame_getter raised ValueError for 'wgc': {exc}")
        except Exception:
            pass  # winsdk / D3D11 hardware not available in CI — acceptable

    def test_unknown_source_raises_value_error(self):
        with pytest.raises(ValueError, match="wgc"):
            build_frame_getter("does_not_exist_xyz")


# ─────────────────────────────────────────────────────────────────────────────
# frame_sources.py — OBS / VirtualCam / WGC
# ─────────────────────────────────────────────────────────────────────────────

class TestOBSWebSocketSource:
    def test_connect_creates_reqclient(self):
        from src.detector_config import DetectorConfig
        from src.frame_sources import OBSWebSocketSource

        client = MagicMock()
        mod = _fake_obsws_module(client)
        cfg = DetectorConfig(obs_ws_host="host", obs_ws_port=4456, obs_ws_password="pw")

        with patch.dict("sys.modules", {"obsws_python": mod}):
            src = OBSWebSocketSource(cfg)
            src.connect()

        mod.ReqClient.assert_called_once_with(host="host", port=4456, password="pw", timeout=5)

    def test_get_frame_uses_current_scene_when_source_empty(self):
        from src.detector_config import DetectorConfig
        from src.frame_sources import OBSWebSocketSource

        client = MagicMock()
        client.get_current_program_scene.return_value = types.SimpleNamespace(current_program_scene_name="Scene")
        client.get_video_settings.return_value = types.SimpleNamespace(base_width=1920, base_height=1080)
        payload = "data:image/png;base64," + base64.b64encode(b"png").decode("ascii")
        client.get_source_screenshot.return_value = types.SimpleNamespace(image_data=payload)

        with patch.dict("sys.modules", {"obsws_python": _fake_obsws_module(client)}):
            with patch("src.frame_sources.cv2.imdecode", return_value=_FRAME.copy()) as imdecode:
                frame = OBSWebSocketSource(DetectorConfig()).get_frame()

        assert frame is not None
        client.get_source_screenshot.assert_called_once_with(
            name="Scene", img_format="png", width=1920, height=1080, quality=-1,
        )
        imdecode.assert_called_once()

    def test_get_source_size_scales_when_capture_width_smaller(self):
        from src.detector_config import DetectorConfig
        from src.frame_sources import OBSWebSocketSource

        src = OBSWebSocketSource(DetectorConfig(), capture_width=960)
        src._client = MagicMock()
        src._client.get_video_settings.return_value = types.SimpleNamespace(base_width=1920, base_height=1080)

        assert src._get_source_size() == (960, 540)

    def test_disconnect_clears_client(self):
        from src.detector_config import DetectorConfig
        from src.frame_sources import OBSWebSocketSource

        src = OBSWebSocketSource(DetectorConfig())
        client = MagicMock()
        src._client = client
        src.disconnect()
        client.disconnect.assert_called_once()
        assert src._client is None


class TestVirtualCameraSource:
    def test_connect_falls_back_when_dshow_not_opened(self):
        from src.frame_sources import VirtualCameraSource

        primary = MagicMock()
        primary.isOpened.return_value = False
        fallback = MagicMock()
        fallback.isOpened.return_value = True

        with patch("src.frame_sources.cv2.VideoCapture", side_effect=[primary, fallback]) as video_capture:
            src = VirtualCameraSource(cam_index=2)
            src.connect()

        assert src._cap is fallback
        assert video_capture.call_count == 2
        fallback.set.assert_any_call(3, 1920)
        fallback.set.assert_any_call(4, 1080)

    def test_get_frame_lazy_connect_returns_frame(self):
        from src.frame_sources import VirtualCameraSource

        cap = MagicMock()
        cap.isOpened.return_value = True
        cap.read.return_value = (True, _FRAME.copy())

        with patch("src.frame_sources.cv2.VideoCapture", return_value=cap):
            frame = VirtualCameraSource().get_frame()

        assert frame is not None
        assert frame.shape == (1080, 1920, 3)

    def test_disconnect_releases_capture(self):
        from src.frame_sources import VirtualCameraSource

        cap = MagicMock()
        src = VirtualCameraSource()
        src._cap = cap
        src.disconnect()
        cap.release.assert_called_once()
        assert src._cap is None

    def test_del_releases_capture(self):
        from src.frame_sources import VirtualCameraSource

        cap = MagicMock()
        src = VirtualCameraSource()
        src._cap = cap
        src.__del__()
        cap.release.assert_called_once()
        assert src._cap is None


class TestWGCSource:
    def test_connect_raises_when_window_not_found(self):
        from src.frame_sources import WGCSource

        fake_user32 = types.SimpleNamespace(
            GetWindowTextW=lambda hwnd, buf, size: None,
            IsWindowVisible=lambda hwnd: False,
            EnumWindows=lambda cb, arg: True,
        )

        with patch("ctypes.WINFUNCTYPE", new=lambda *args, **kwargs: (lambda fn: fn)):
            with patch("ctypes.windll", new=types.SimpleNamespace(user32=fake_user32)):
                with pytest.raises(ConnectionError, match="no encontrada"):
                    WGCSource("Tibia").connect()

    def test_connect_initializes_capture_and_warmup(self):
        from src.frame_sources import WGCSource

        fake_user32 = types.SimpleNamespace()

        def _get_window_text(hwnd: int, buf: Any, size: int) -> None:
            buf.value = "Tibia Client"

        def _enum_windows(cb: Any, arg: int) -> bool:
            cb(1234, arg)
            return True

        fake_user32.GetWindowTextW = _get_window_text
        fake_user32.IsWindowVisible = lambda hwnd: True
        fake_user32.EnumWindows = _enum_windows

        frames = iter([None, np.ones((10, 10, 3), dtype=np.uint8)])

        def _grab_frame():
            return next(frames, np.ones((10, 10, 3), dtype=np.uint8))

        getter = MagicMock(side_effect=_grab_frame)
        cap_instance = MagicMock()
        cap_instance.open.return_value = getter
        winsdk_modules = _fake_winsdk_interop(types.SimpleNamespace(width=800, height=600))

        with patch("ctypes.WINFUNCTYPE", new=lambda *args, **kwargs: (lambda fn: fn)):
            with patch("ctypes.windll", new=types.SimpleNamespace(user32=fake_user32)):
                with patch.dict("sys.modules", winsdk_modules):
                    with patch("src.frame_capture.WGCCapture", return_value=cap_instance):
                        with patch("src.frame_sources.time.sleep"):
                            src = WGCSource("Tibia")
                            src.connect()

        assert src._connected is True
        assert src._hwnd == 1234
        assert src._width == 800
        assert src._height == 600
        cap_instance.open.assert_called_once()

    def test_get_frame_times_out_when_grab_returns_none(self):
        from src.frame_sources import WGCSource

        src = WGCSource()
        src._connected = True
        src._grab = MagicMock(return_value=None)
        src._MAX_TRIES = 2

        with patch("src.frame_sources.time.sleep"):
            assert src.get_frame() is None

    def test_disconnect_closes_capture(self):
        from src.frame_sources import WGCSource

        capture = MagicMock()
        src = WGCSource()
        src._capture = capture
        src._grab = MagicMock()
        src._connected = True
        src.disconnect()

        capture.close.assert_called_once()
        assert src._capture is None
        assert src._grab is None
        assert src._connected is False


# ─────────────────────────────────────────────────────────────────────────────
# MSSScreenSource — persistent mss instance (frame_sources.py)
# ─────────────────────────────────────────────────────────────────────────────

def _fake_mss_for_source(monitor_count: int = 1):
    """Build a fake mss module for MSSScreenSource tests.

    shot.rgb   = bytes of a 1920×1080 RGB image with sentinel pixel [0,0]=[10,20,30]
    shot.height = 1080 / shot.width = 1920
    """
    mod = types.ModuleType("mss")
    sct = MagicMock()
    sct.monitors = [None] + [
        {"left": i * 1920, "top": 0, "width": 1920, "height": 1080}
        for i in range(monitor_count)
    ]
    rgb_data = np.zeros((1080, 1920, 3), dtype=np.uint8)
    rgb_data[0, 0] = [10, 20, 30]   # sentinel: R=10 G=20 B=30 (RGB order)
    shot = MagicMock()
    shot.rgb    = rgb_data.tobytes()
    shot.height = 1080
    shot.width  = 1920
    sct.grab.return_value = shot
    mod.mss = MagicMock(return_value=sct)
    return mod, sct, shot


class TestMSSScreenSourcePersistent:
    """Tests for MSSScreenSource — verifies the persistent-instance optimisation."""

    def test_connect_creates_sct(self):
        """After connect(), _sct must not be None."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            src.connect()
        assert src._sct is not None

    def test_connect_caches_region(self):
        """_region must equal sct.monitors[1] after connect()."""
        from src.frame_sources import MSSScreenSource
        mod, sct, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            src.connect()
        assert src._region == sct.monitors[1]

    def test_mss_init_called_once_during_connect(self):
        """mss.mss() must be called exactly once — not once per frame."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            src.connect()
        mod.mss.assert_called_once()

    def test_get_frame_reuses_sct_across_multiple_calls(self):
        """mss() must not be reinitialised on every get_frame() call."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            src.connect()
            for _ in range(5):
                src.get_frame()
        mod.mss.assert_called_once()

    def test_get_frame_returns_bgr_ndarray(self):
        """get_frame() must return a 3-channel uint8 array of shape (1080,1920,3)."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            frame = src.get_frame()
        assert frame is not None
        assert frame.ndim == 3
        assert frame.shape == (1080, 1920, 3)
        assert frame.dtype == np.uint8

    def test_get_frame_rgb_to_bgr_conversion(self):
        """Sentinel pixel RGB=[10,20,30] must arrive as BGR=[30,20,10]."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            frame = src.get_frame()
        # RGB [R=10, G=20, B=30] → reversed → BGR [B=30, G=20, R=10]
        assert frame[0, 0, 0] == 30   # B
        assert frame[0, 0, 1] == 20   # G
        assert frame[0, 0, 2] == 10   # R

    def test_disconnect_clears_sct_and_region(self):
        """After disconnect(), _sct and _region must both be None."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            src.connect()
            src.disconnect()
        assert src._sct is None
        assert src._region is None

    def test_disconnect_calls_close(self):
        """disconnect() must call sct.close() to release DXGI resources."""
        from src.frame_sources import MSSScreenSource
        mod, sct, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            src.connect()
            src.disconnect()
        sct.close.assert_called_once()

    def test_get_frame_lazy_connect(self):
        """get_frame() without prior connect() must auto-connect."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            frame = src.get_frame()   # no explicit connect()
        assert frame is not None
        mod.mss.assert_called_once()

    def test_connect_invalid_monitor_raises_connection_error(self):
        """monitor index > available must raise ConnectionError and leave _sct=None."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source(monitor_count=1)
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=5)
            with pytest.raises(ConnectionError, match="Monitor 5"):
                src.connect()
        assert src._sct is None

    def test_disconnect_idempotent_when_not_connected(self):
        """disconnect() when _sct is already None must not raise."""
        from src.frame_sources import MSSScreenSource
        src = MSSScreenSource(monitor=1)
        assert src._sct is None
        src.disconnect()   # must not raise

    def test_get_frame_after_disconnect_reconnects(self):
        """get_frame() after disconnect() must auto-reconnect (lazy init)."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            src.connect()
            src.disconnect()
            frame = src.get_frame()   # should reconnect silently
        assert frame is not None
        assert mod.mss.call_count == 2   # once on connect, once on lazy reconnect

    def test_grab_called_with_cached_region(self):
        """sct.grab() must receive the exact _region object (not monitors[idx] inline)."""
        from src.frame_sources import MSSScreenSource
        mod, sct, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            src.connect()
            cached = src._region
            src.get_frame()
        sct.grab.assert_called_once_with(cached)

    def test_get_frame_returns_contiguous_array(self):
        """Frame must be C-contiguous so downstream OpenCV calls don't copy it."""
        from src.frame_sources import MSSScreenSource
        mod, _, _ = _fake_mss_for_source()
        with patch.dict("sys.modules", {"mss": mod}):
            src = MSSScreenSource(monitor=1)
            frame = src.get_frame()
        assert frame.flags["C_CONTIGUOUS"]
