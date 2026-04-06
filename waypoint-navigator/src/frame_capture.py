"""
frame_capture.py
----------------
Pluggable frame-source backends that replace OBS WebSocket.

Each backend exposes:
    getter = BackendClass(...).open()  # returns Callable[[], np.ndarray | None]

Factory::

    from src.frame_capture import build_frame_getter

    # --- drop-in replacements for OBS ---
    get = build_frame_getter("mss")            # primary monitor via DXGI (mss)
    get = build_frame_getter("dxcam")          # GPU DXGI, best DirectX capture
    get = build_frame_getter("printwindow",    # Win32 PrintWindow — true background
                             hwnd=0x12345)
    get = build_frame_getter("rtmp",           # NGINX RTMP stream
                             url="rtmp://localhost/live/tibia",
                             ffmpeg_window="Tibia")  # optional: auto-launch FFmpeg

Backends
--------
mss           : pip install mss  — DXGI, window must not be minimized.
dxcam         : pip install dxcam — GPU DXGI, faster, same minimise constraint.
printwindow   : Win32 API only — works behind other windows, may be blank on DX12+.
rtmp          : cv2.VideoCapture on RTMP URL. Optionally launches FFmpeg gdigrab to
                push the Tibia window stream (no OBS required).

NGINX RTMP quick-start (no OBS)
---------------------------------
1. Run NGINX with the RTMP module (Docker example):
       docker run -d -p 1935:1935 tiangolo/nginx-rtmp

2. The 'rtmp' backend auto-launches FFmpeg to push the game window:
       get = build_frame_getter("rtmp",
                                url="rtmp://localhost/live/tibia",
                                ffmpeg_window="Tibia",
                                fps=10)

   Equivalent manual FFmpeg command:
       ffmpeg -f gdigrab -framerate 10 -i title=Tibia \
              -c:v libx264 -preset ultrafast -tune zerolatency \
              -f flv rtmp://localhost/live/tibia
"""

from __future__ import annotations

import importlib
import logging
import subprocess
import threading
import random
import time
from typing import Any, Callable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("wn.fc")

# ---------------------------------------------------------------------------
# MSS backend
# ---------------------------------------------------------------------------

def _hwnd_client_region(hwnd: int) -> Optional[dict[str, int]]:
    """Return mss monitor dict for the client area of *hwnd*, or None on error."""
    try:
        import ctypes
        import ctypes.wintypes as _wt
        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        user32 = ctypes.windll.user32
        pt = _wt.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        cr = _RECT()
        user32.GetClientRect(hwnd, ctypes.byref(cr))
        w = cr.right - cr.left
        h = cr.bottom - cr.top
        if w <= 0 or h <= 0:
            return None
        return {"left": pt.x, "top": pt.y, "width": w, "height": h}
    except Exception:
        logger.debug("_hwnd_client_region failed for hwnd=%s", hwnd, exc_info=True)
        return None


class MssCapture:
    """DXGI screen capture via the *mss* library (pip install mss).

    Parameters
    ----------
    hwnd : int, optional
        When given, captures only the client area of that window.
        More efficient than full-monitor capture and works even when the
        window is partially off-screen.
    monitor_idx : int
        Fallback full-monitor index when *hwnd* is not given (1 = primary in
        ``mss.monitors``; index 0 is the virtual all-monitors entry).
    """

    def __init__(self, hwnd: int = 0, monitor_idx: int = 1) -> None:
        self._hwnd        = hwnd
        self._monitor_idx = monitor_idx
        self._sct: Any    = None

    def open(self) -> Callable[[], Optional[np.ndarray]]:
        import mss
        import threading

        # Resolve capture region using a temporary mss instance (main thread).
        # Do NOT store this instance — mss uses thread-local GDI device contexts,
        # so an instance created in one thread cannot be used from another.
        with mss.mss() as _tmp_sct:
            if self._hwnd:
                region = _hwnd_client_region(self._hwnd)
            else:
                region = None

            if region is None:
                n_monitors = len(_tmp_sct.monitors)
                monitor_idx = self._monitor_idx
                if monitor_idx >= n_monitors:
                    if n_monitors > 1:
                        logger.warning(
                            "[MssCapture] monitor_idx=%d out of range; falling back to primary monitor",
                            monitor_idx,
                        )
                        monitor_idx = 1
                    else:
                        raise ValueError(
                            f"MssCapture: monitor_idx={monitor_idx} out of range "
                            f"(available: 0..{n_monitors - 1})"
                        )
                region = dict(_tmp_sct.monitors[monitor_idx])  # copy

        _region = region  # capture into closure
        _hwnd   = self._hwnd
        # Thread-local storage: each calling thread gets its own mss instance
        # to avoid AttributeError('_thread._local has no attribute srcdc').
        _tls = threading.local()
        _first_logged = False

        def _get_sct() -> Any:
            if not hasattr(_tls, "sct"):
                _tls.sct = mss.mss()
            return _tls.sct

        def _grab() -> Optional[np.ndarray]:
            nonlocal _first_logged
            try:
                if _hwnd:
                    r = _hwnd_client_region(_hwnd)
                    mon = r if r else _region
                else:
                    mon = _region
                sct = _get_sct()
                img = sct.grab(mon)
                arr = np.array(img)[:, :, :3]
                if not _first_logged:
                    _first_logged = True
                    logger.info(
                        "[MssCapture] First frame: %dx%d",
                        arr.shape[1], arr.shape[0],
                    )
                return arr
            except Exception:
                # Reset thread-local sct so next call gets a fresh instance
                _tls.sct = None  # type: ignore[assignment]
                if hasattr(_tls, "sct"):
                    del _tls.sct
                logger.debug("MssCapture grab failed", exc_info=True)
                return None

        return _grab

    def close(self) -> None:
        # Thread-local sct instances are closed automatically when their
        # threads exit; nothing to clean up from the main thread here.
        pass


# ---------------------------------------------------------------------------
# DXCam backend
# ---------------------------------------------------------------------------

class DxcamCapture:
    """GPU DXGI capture via *dxcam* (pip install dxcam). Fastest DirectX option.

    Parameters
    ----------
    hwnd : int, optional
        When given, captures only the client area of that window.
    output_idx : int
        GPU output / monitor index (0 = primary).
    fps : int
        Target capture frame-rate.
    """

    def __init__(self, hwnd: int = 0, output_idx: int = 0, fps: int = 10) -> None:
        self._hwnd       = hwnd
        self._output_idx = output_idx
        self._fps        = fps
        self._cam: Any   = None

    def open(self) -> Callable[[], Optional[np.ndarray]]:
        # dxcam deshabilitado por defecto — dirty DXGI state tras force-kill.
        # If dxcam fails to initialise (e.g. after a hard process kill left
        # the DXGI device in a dirty state) we log the specific error and
        # return a no-op getter rather than raising, so the caller can fall
        # back to a different backend.
        try:
            dxcam = importlib.import_module("dxcam")
        except ImportError as exc:
            logger.warning("[DxcamCapture] dxcam not installed — %s", exc)
            return lambda: None
        try:
            self._cam = dxcam.create(output_idx=self._output_idx, output_color="BGR")
        except Exception as exc:
            logger.warning(
                "[DxcamCapture] failed to create camera for output_idx=%d — %s",
                self._output_idx, exc,
            )
            return lambda: None

        region = None
        if self._hwnd:
            r = _hwnd_client_region(self._hwnd)
            if r:
                region = (r["left"], r["top"],
                          r["left"] + r["width"], r["top"] + r["height"])

        if region:
            self._cam.start(region=region, target_fps=self._fps)
        else:
            self._cam.start(target_fps=self._fps)

        _first_logged = False

        def _grab() -> Optional[np.ndarray]:
            nonlocal _first_logged
            frame: Optional[np.ndarray] = self._cam.get_latest_frame()
            if frame is not None and not _first_logged:
                _first_logged = True
                logger.info(
                    "[DxcamCapture] First frame: %dx%d",
                    frame.shape[1], frame.shape[0],
                )
            return frame  # BGR ndarray or None

        return _grab

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
            except Exception:
                logger.debug("DxcamCapture failed to stop camera", exc_info=True)
        self._cam = None


# ---------------------------------------------------------------------------
# PrintWindow backend (Win32 — true background capture)
# ---------------------------------------------------------------------------

class PrintWindowCapture:
    """Win32 PrintWindow(PW_RENDERFULLCONTENT) — captures behind other windows.

    Note: May return a blank frame on games using DX12 / hardware-only layers.
    Flag 0x2 requests the DWM compositor surface; works for most DX11 games.
    """

    def __init__(self, hwnd: int, flag: int = 0x2) -> None:
        self._hwnd = hwnd
        self._flag = flag

    def open(self) -> Callable[[], Optional[np.ndarray]]:
        import ctypes
        import ctypes.wintypes as wt

        gdi32  = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        hwnd   = self._hwnd
        flag   = self._flag

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize",          ctypes.c_uint32),
                ("biWidth",         ctypes.c_int32),
                ("biHeight",        ctypes.c_int32),
                ("biPlanes",        ctypes.c_uint16),
                ("biBitCount",      ctypes.c_uint16),
                ("biCompression",   ctypes.c_uint32),
                ("biSizeImage",     ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed",       ctypes.c_uint32),
                ("biClrImportant",  ctypes.c_uint32),
            ]

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        def _grab() -> Optional[np.ndarray]:
            cr = RECT()
            user32.GetClientRect(hwnd, ctypes.byref(cr))
            w = cr.right - cr.left
            h = cr.bottom - cr.top
            if w <= 0 or h <= 0:
                return None

            hdc_win = user32.GetDC(hwnd)
            if not hdc_win:
                return None
            hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
            if not hdc_mem:
                user32.ReleaseDC(hwnd, hdc_win)
                return None
            hbm = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
            if not hbm:
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(hwnd, hdc_win)
                return None
            gdi32.SelectObject(hdc_mem, hbm)
            user32.PrintWindow(hwnd, hdc_mem, flag)

            bih = BITMAPINFOHEADER()
            bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bih.biWidth = w; bih.biHeight = -h
            bih.biPlanes = 1; bih.biBitCount = 32
            buf = (ctypes.c_uint8 * (w * h * 4))()
            gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bih), 0)

            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_win)

            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
            return arr[:, :, :3]  # drop alpha → BGR

        return _grab

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# NGINX RTMP backend (no OBS — FFmpeg pushes, cv2 reads)
# ---------------------------------------------------------------------------

class RtmpCapture:
    """Read frames from an RTMP stream via cv2.VideoCapture.

    Optionally auto-launches an FFmpeg subprocess that captures the game
    window with ``gdigrab`` and pushes it to the RTMP URL, replacing OBS.

    Parameters
    ----------
    url : str
        RTMP URL to consume, e.g. ``rtmp://localhost/live/tibia``.
    ffmpeg_window : str, optional
        Window title fragment for FFmpeg gdigrab (e.g. ``"Tibia"``).
        If given, FFmpeg is launched automatically on ``open()``.
    fps : int
        Capture / target frame-rate for the gdigrab push.
    ffmpeg_extra : list[str], optional
        Additional FFmpeg args inserted before ``-f flv <url>``.
    connect_timeout : float
        Seconds to wait for the RTMP stream to become available.
    """

    def __init__(
        self,
        url: str = "rtmp://localhost/live/tibia",
        ffmpeg_window: Optional[str] = None,
        fps: int = 10,
        ffmpeg_extra: Optional[List[str]] = None,
        connect_timeout: float = 10.0,
    ) -> None:
        self._url = url
        self._ffmpeg_window = ffmpeg_window
        self._fps = fps
        self._ffmpeg_extra: List[str] = ffmpeg_extra or []
        self._connect_timeout = connect_timeout

        self._proc:  Optional[subprocess.Popen[bytes]] = None
        self._cap:   Any = None
        self._latest: Optional[np.ndarray] = None
        self._lock   = threading.Lock()
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── public ──────────────────────────────────────────────────────────────

    def open(self) -> Callable[[], Optional[np.ndarray]]:
        """Start FFmpeg (if configured) and begin the background grab loop."""
        if self._ffmpeg_window:
            self._launch_ffmpeg()

        self._connect_cap()
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

        def _get() -> Optional[np.ndarray]:
            with self._lock:
                return self._latest

        return _get

    def close(self) -> None:
        """Stop background thread, release VideoCapture, terminate FFmpeg."""
        self._stop_flag.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                logger.debug("RtmpCapture failed to release VideoCapture", exc_info=True)
        self._cap = None
        if self._proc is not None:
            # Graceful shutdown: SIGTERM first, then SIGKILL if it doesn't exit.
            # Always call wait() so the process is fully reaped (no zombies).
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    logger.warning("RtmpCapture FFmpeg process did not exit after kill — possible zombie")
        self._proc = None
        with self._lock:
            self._latest = None

    # ── internal ────────────────────────────────────────────────────────────

    def _launch_ffmpeg(self) -> None:
        cmd = [
            "ffmpeg", "-y",
            "-f", "gdigrab",
            "-framerate", str(self._fps),
            "-i", f"title={self._ffmpeg_window}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-an",                        # no audio
        ] + self._ffmpeg_extra + [
            "-f", "flv", self._url,
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _connect_cap(self) -> None:
        import cv2
        deadline = time.monotonic() + self._connect_timeout
        while time.monotonic() < deadline:
            cap = cv2.VideoCapture(self._url)
            if cap.isOpened():
                self._cap = cap
                return
            cap.release()
            time.sleep(random.uniform(0.35, 0.65))
        # Proceed anyway; grab loop will keep retrying
        self._cap = cv2.VideoCapture(self._url)

    def _grab_loop(self) -> None:
        import cv2
        while not self._stop_flag.is_set():
            if self._cap is None or not self._cap.isOpened():
                time.sleep(random.uniform(0.35, 0.65))
                continue
            ok, frame = self._cap.read()
            if ok and frame is not None:
                with self._lock:
                    self._latest = frame
            else:
                # stream interrupted — try to reconnect
                try:
                    self._cap.release()
                except Exception:
                    logger.debug("RtmpCapture failed to release interrupted stream", exc_info=True)
                time.sleep(random.uniform(0.7, 1.4))
                self._cap = cv2.VideoCapture(self._url)


# ---------------------------------------------------------------------------
# Virtual Camera backend (OBS Virtual Camera / any DirectShow device)
# ---------------------------------------------------------------------------


class VirtualCameraCapture:
    """Read frames from a DirectShow virtual camera device (e.g. OBS Virtual Camera).

    OBS Studio 28+ ships a built-in Virtual Camera.  Once OBS has a Game Capture
    source pointed at Tibia and Virtual Camera is started (Tools → Start Virtual
    Camera), this backend reads the frames via ``cv2.VideoCapture``.

    Parameters
    ----------
    device_index : int
        DirectShow device index (default 0 — auto-detects the first available
        virtual camera).  If OBS Virtual Camera is the only video device, 0 is
        correct.  Pass ``-1`` to scan indices 0-9 and pick the one with the
        highest resolution.
    width : int, optional
        Requested capture width.  0 = use device default.
    height : int, optional
        Requested capture height.  0 = use device default.
    fps : int, optional
        Requested frame rate.  0 = use device default.
    """

    def __init__(
        self,
        device_index: int = 0,
        width: int = 0,
        height: int = 0,
        fps: int = 0,
    ) -> None:
        self._device_index = device_index
        self._width  = width
        self._height = height
        self._fps    = fps

        self._cap:    Any = None
        self._latest: Optional[np.ndarray] = None
        self._lock    = threading.Lock()
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── public ──────────────────────────────────────────────────────────────

    @classmethod
    def find_obs_index(cls) -> int:
        """Scan DirectShow indices 0-9 and return the index with the largest
        frame (proxy for OBS Virtual Camera vs a tiny webcam).  Returns 0 if
        nothing is found."""
        import cv2
        best_idx  = 0
        best_area = 0
        for idx in range(10):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                continue
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            area = w * h
            cap.release()
            if area > best_area:
                best_area = area
                best_idx  = idx
        return best_idx

    def open(self) -> Callable[[], Optional[np.ndarray]]:
        """Open the DirectShow device and start the background grab loop."""
        import cv2

        idx = self._device_index
        if idx == -1:
            idx = self.find_obs_index()

        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            raise RuntimeError(
                f"VirtualCameraCapture: cannot open device index {idx}. "
                "Make sure OBS Virtual Camera is started (Tools → Start Virtual Camera)."
            )
        if self._width  > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._width)
        if self._height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        if self._fps    > 0:
            cap.set(cv2.CAP_PROP_FPS, self._fps)

        self._cap = cap
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

        def _get() -> Optional[np.ndarray]:
            with self._lock:
                return self._latest

        return _get

    def close(self) -> None:
        """Stop the background thread and release the device."""
        self._stop_flag.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                logger.debug("VirtualCameraCapture failed to release device", exc_info=True)
        self._cap = None
        with self._lock:
            self._latest = None

    # ── internal ────────────────────────────────────────────────────────────

    def _grab_loop(self) -> None:
        while not self._stop_flag.is_set():
            if self._cap is None or not self._cap.isOpened():
                time.sleep(random.uniform(0.07, 0.15))
                continue
            ok, frame = self._cap.read()
            if ok and frame is not None:
                with self._lock:
                    self._latest = frame
            else:
                time.sleep(random.uniform(0.008, 0.02))


# ---------------------------------------------------------------------------
# WGC (Windows Graphics Capture) backend
# ---------------------------------------------------------------------------

# Shared GUID struct type — defined once so all WGC helpers use the same class
# (ctypes CFUNCTYPE / argtypes enforce pointer-type identity).
import ctypes as _ct_wgc


class _WGC_GUID(_ct_wgc.Structure):
    _fields_ = [
        ("D1", _ct_wgc.c_ulong), ("D2", _ct_wgc.c_ushort),
        ("D3", _ct_wgc.c_ushort), ("D4", _ct_wgc.c_ubyte * 8),
    ]


def _wgc_make_guid(s: str) -> Any:
    """Return a _WGC_GUID struct for the given GUID string (always the same class)."""
    p = s.strip("{}").split("-")
    g = _WGC_GUID()
    g.D1 = int(p[0], 16)
    g.D2 = int(p[1], 16)
    g.D3 = int(p[2], 16)
    g.D4 = (_ct_wgc.c_ubyte * 8)(*bytes.fromhex(p[3] + p[4]))
    return g


def _wgc_create_d3d11_device() -> Tuple[Any, Any]:
    """Create a hardware D3D11 device via DXGI factory + first adapter.

    Returns (dev_ptr, ctx_ptr) as ctypes.c_void_p.  Raises on failure.
    """
    import ctypes

    POINTER = ctypes.POINTER
    c_void_p = ctypes.c_void_p
    CFUNCTYPE = ctypes.CFUNCTYPE
    c_long = ctypes.c_long

    dxgi  = ctypes.windll.dxgi
    d3d11 = ctypes.windll.d3d11

    # CreateDXGIFactory1 → IDXGIFactory1
    fac = c_void_p()
    fn = dxgi.CreateDXGIFactory1
    fn.restype  = c_long
    fn.argtypes = [POINTER(_WGC_GUID), POINTER(c_void_p)]
    guid_fac = _wgc_make_guid("{770AAE78-F26F-4DBA-A829-253C83D1B387}")
    hr = fn(ctypes.byref(guid_fac), ctypes.byref(fac))
    if hr != 0 or not fac.value:
        raise RuntimeError(f"CreateDXGIFactory1 failed: {hr:#010x}")

    # EnumAdapters (vtable[7] on IDXGIFactory)
    vt_f = ctypes.cast(fac, POINTER(c_void_p)).contents.value
    assert vt_f is not None  # guaranteed: fac.value checked above
    EnumAdapters = CFUNCTYPE(c_long, c_void_p, ctypes.c_uint, POINTER(c_void_p))(
        ctypes.cast(vt_f, POINTER(c_void_p * 15)).contents[7])  # type: ignore[arg-type]
    adapter = c_void_p()
    hr2 = EnumAdapters(fac.value, 0, ctypes.byref(adapter))
    if hr2 != 0 or not adapter.value:
        raise RuntimeError(f"EnumAdapters failed: {hr2:#010x}")

    # D3D11CreateDevice (pAdapter, UNKNOWN, NULL, BGRA_SUPPORT, NULL, 0, SDK_VER)
    dev = c_void_p()
    ctx = c_void_p()
    hr3 = d3d11.D3D11CreateDevice(
        adapter, 0, None, 0x20, None, 0, 7,
        ctypes.byref(dev), None, ctypes.byref(ctx),
    )
    if hr3 != 0 or not dev.value:
        raise RuntimeError(f"D3D11CreateDevice failed: {hr3:#010x}")
    return dev, ctx


def _wgc_raw_ptr(winsdk_obj: Any) -> int:
    """Extract the raw IInspectable* COM pointer from a winsdk Object at offset +24."""
    import ctypes
    return ctypes.cast(id(winsdk_obj) + 24,
                       ctypes.POINTER(ctypes.c_void_p)).contents.value or 0


def _wgc_wrap_iunknown_qi(raw: int) -> Tuple[Any, Any, Any]:
    """Return (qi_fn, ar_fn, rl_fn) ctypes callables from a raw IUnknown pointer."""
    import ctypes
    CFUNCTYPE = ctypes.CFUNCTYPE
    c_long    = ctypes.c_long
    c_ulong   = ctypes.c_ulong
    c_void_p  = ctypes.c_void_p
    POINTER   = ctypes.POINTER

    vt   = ctypes.cast(raw, POINTER(c_void_p)).contents.value
    assert vt is not None  # caller guarantees raw != 0
    vt3  = ctypes.cast(vt, POINTER(c_void_p * 3)).contents
    QI   = CFUNCTYPE(c_long,  c_void_p, POINTER(_WGC_GUID), POINTER(c_void_p))(vt3[0])  # type: ignore[arg-type]
    AR   = CFUNCTYPE(c_ulong, c_void_p)(vt3[1])  # type: ignore[arg-type]
    RL   = CFUNCTYPE(c_ulong, c_void_p)(vt3[2])  # type: ignore[arg-type]
    return QI, AR, RL


def _wgc_qi(raw: int, iid_str: str) -> int:
    """QueryInterface a raw COM pointer for the given IID.  Returns raw ptr or 0."""
    import ctypes
    QI, _AR, _RL = _wgc_wrap_iunknown_qi(raw)
    result = ctypes.c_void_p()
    guid   = _wgc_make_guid(iid_str)
    hr = QI(raw, ctypes.byref(guid), ctypes.byref(result))
    return (result.value or 0) if hr == 0 else 0


def _wgc_d3d_device_to_winrt(dev: Any, ctx: Any) -> Any:
    """Convert a raw ID3D11Device ctypes pointer to a winsdk IDirect3DDevice.

    Uses IDXGIDevice1 QI + CreateDirect3D11DeviceFromDXGIDevice + pointer
    injection into a temporary winsdk carrier object.
    """
    import ctypes

    d3d11 = ctypes.windll.d3d11

    # QI ID3D11Device → IDXGIDevice1  (IDXGIDevice v1 fails on modern Windows)
    dxgi_dev1 = _wgc_qi(dev.value, "{77DB970F-6276-48BA-BA28-070143B4392C}")
    if not dxgi_dev1:
        raise RuntimeError("QI IDXGIDevice1 failed")

    # CreateDirect3D11DeviceFromDXGIDevice
    fn = d3d11.CreateDirect3D11DeviceFromDXGIDevice
    fn.restype  = ctypes.HRESULT
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    winrt_ptr = ctypes.c_void_p()
    fn(ctypes.c_void_p(dxgi_dev1), ctypes.byref(winrt_ptr))
    if not winrt_ptr.value:
        raise RuntimeError("CreateDirect3D11DeviceFromDXGIDevice returned NULL")

    # Wrap as winsdk IDirect3DDevice via pointer injection into a carrier Object
    from winsdk.windows.graphics.capture.interop import create_for_window  # noqa  # type: ignore[reportMissingImports]
    import winsdk.windows.graphics.directx.direct3d11 as _d3dm  # noqa  # type: ignore[reportMissingImports]

    # We need any winsdk Object as carrier — create a temporary GraphicsCaptureItem
    # using the desktop window handle (always available).
    import ctypes.wintypes as _wt
    user32   = ctypes.windll.user32
    desk_hwnd = user32.GetDesktopWindow()
    carrier  = create_for_window(desk_hwnd)
    c_addr   = id(carrier)
    old_ptr  = ctypes.cast(c_addr + 24, ctypes.POINTER(ctypes.c_void_p)).contents.value

    _QI, _AR, _RL = _wgc_wrap_iunknown_qi(winrt_ptr.value)
    _AR(winrt_ptr.value)  # carrier will own a reference
    ctypes.cast(c_addr + 24, ctypes.POINTER(ctypes.c_void_p)).contents.value = winrt_ptr.value

    try:
        wd3d = _d3dm.IDirect3DDevice._from(carrier)
    finally:
        ctypes.cast(c_addr + 24, ctypes.POINTER(ctypes.c_void_p)).contents.value = old_ptr
        _RL(winrt_ptr.value)

    return wd3d


def _wgc_surface_to_numpy(
    surface: Any,
    dev: Any,
    ctx: Any,
    staging_cache: list[Any],
) -> Optional[np.ndarray]:
    """Convert a winsdk IDirect3DSurface to a BGR numpy array using raw vtable calls.

    Parameters
    ----------
    surface       : winsdk IDirect3DSurface
    dev           : ctypes.c_void_p — raw ID3D11Device pointer
    ctx           : ctypes.c_void_p — raw ID3D11DeviceContext immediate context
    staging_cache : mutable list — ``[c_void_p_staging, width, height]``
                    Reused across calls; avoids re-allocating the staging texture.
    """
    import ctypes
    import ctypes.wintypes as wt

    POINTER  = ctypes.POINTER
    c_void_p = ctypes.c_void_p
    c_uint   = ctypes.c_uint
    c_long   = ctypes.c_long
    CFUNC    = ctypes.CFUNCTYPE

    # ── 1. Extract raw IInspectable* from the winsdk surface ─────────────────
    raw_surf = _wgc_raw_ptr(surface)
    if not raw_surf:
        return None

    # ── 2. QI to IDXGIDxgiInterfaceAccess  {A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1}
    dxgi_access = _wgc_qi(raw_surf, "{A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1}")
    if not dxgi_access:
        return None

    # ── 3. GetInterface(IID_ID3D11Texture2D) via vtable[3] ───────────────────
    # IDXGIDxgiInterfaceAccess: QI(0), AR(1), RL(2), GetInterface(3)
    vt_acc     = ctypes.cast(dxgi_access, POINTER(c_void_p)).contents.value
    assert vt_acc is not None  # guaranteed: dxgi_access checked above
    GI_t       = CFUNC(c_long, c_void_p, POINTER(_WGC_GUID), POINTER(c_void_p))
    gi_fn      = GI_t(ctypes.cast(vt_acc, POINTER(c_void_p * 5)).contents[3])  # type: ignore[arg-type]
    tex        = c_void_p()
    guid_tex2d = _wgc_make_guid("{6F15AAF2-D208-4E89-9AB4-489535D34F9C}")
    hr = gi_fn(dxgi_access, ctypes.byref(guid_tex2d), ctypes.byref(tex))
    if hr != 0 or not tex.value:
        return None

    # ── 4. GetDesc on ID3D11Texture2D via vtable[10] ─────────────────────────
    # Inheritance chain: IUnknown(0-2), ID3D11DeviceChild(3-6),
    #   ID3D11Resource(7-9), ID3D11Texture2D: GetDesc(10)
    class _DXGI_SAMPLE_DESC(ctypes.Structure):
        _fields_ = [("Count", wt.UINT), ("Quality", wt.UINT)]

    class _TEXTURE2D_DESC(ctypes.Structure):
        _fields_ = [
            ("Width",          wt.UINT), ("Height",         wt.UINT),
            ("MipLevels",      wt.UINT), ("ArraySize",      wt.UINT),
            ("Format",         wt.UINT), ("SampleDesc",     _DXGI_SAMPLE_DESC),
            ("Usage",          wt.UINT), ("BindFlags",      wt.UINT),
            ("CPUAccessFlags", wt.UINT), ("MiscFlags",      wt.UINT),
        ]

    vt_tex   = ctypes.cast(tex, POINTER(c_void_p)).contents.value
    assert vt_tex is not None  # guaranteed: tex.value checked above
    GetDesc  = CFUNC(None, c_void_p, POINTER(_TEXTURE2D_DESC))(
        ctypes.cast(vt_tex, POINTER(c_void_p * 12)).contents[10])  # type: ignore[arg-type]
    desc = _TEXTURE2D_DESC()
    GetDesc(tex.value, ctypes.byref(desc))
    w, h = desc.Width, desc.Height

    # ── 5. Create / reuse staging texture ────────────────────────────────────
    # staging_cache = [c_void_p_staging, width, height]
    if (staging_cache
            and staging_cache[0]
            and staging_cache[0].value
            and staging_cache[1:] == [w, h]):
        staging = staging_cache[0]
    else:
        # Release old staging texture if it exists
        if staging_cache and staging_cache[0] and staging_cache[0].value:
            _old = staging_cache[0]
            _vt  = ctypes.cast(_old, POINTER(c_void_p)).contents.value
            assert _vt is not None  # guaranteed: _old.value checked above
            CFUNC(ctypes.c_ulong, c_void_p)(ctypes.cast(_vt, POINTER(c_void_p * 3)).contents[2])(_old.value)  # type: ignore[arg-type]

        # Staging desc: CPU-readable, no bind flags, STAGING usage
        s_desc = _TEXTURE2D_DESC(
            Width=w, Height=h, MipLevels=1, ArraySize=1,
            Format=desc.Format,
            SampleDesc=_DXGI_SAMPLE_DESC(Count=1, Quality=0),
            Usage=3,        # D3D11_USAGE_STAGING
            BindFlags=0, CPUAccessFlags=0x20000, MiscFlags=0,  # D3D11_CPU_ACCESS_READ
        )
        # ID3D11Device::CreateTexture2D => vtable[5]
        # (IUnknown:0-2, then CreateBuffer(3), CreateTexture1D(4), CreateTexture2D(5))
        staging = c_void_p()
        vt_dev  = ctypes.cast(dev, POINTER(c_void_p)).contents.value
        assert vt_dev is not None  # guaranteed: dev.value checked at creation
        CT2D    = CFUNC(c_long, c_void_p, POINTER(_TEXTURE2D_DESC), c_void_p, POINTER(c_void_p))
        CT2D(ctypes.cast(vt_dev, POINTER(c_void_p * 10)).contents[5])(  # type: ignore[arg-type]
            dev.value, ctypes.byref(s_desc), None, ctypes.byref(staging))
        if not staging.value:
            return None
        staging_cache.clear()
        staging_cache.extend([staging, w, h])

    # ── 6. CopyResource: vtable[47] on ID3D11DeviceContext ───────────────────
    vt_ctx   = ctypes.cast(ctx, POINTER(c_void_p)).contents.value
    assert vt_ctx is not None  # guaranteed: ctx.value checked at creation
    CopyRes  = CFUNC(None, c_void_p, c_void_p, c_void_p)
    CopyRes(ctypes.cast(vt_ctx, POINTER(c_void_p * 50)).contents[47])(  # type: ignore[arg-type]
        ctx.value, staging.value, tex.value)

    # ── 7. Map: vtable[14] ────────────────────────────────────────────────────
    class _MAPPED(ctypes.Structure):
        _fields_ = [("pData", c_void_p), ("RowPitch", wt.UINT), ("DepthPitch", wt.UINT)]

    mapped  = _MAPPED()
    Map_t   = CFUNC(c_long, c_void_p, c_void_p, c_uint, c_uint, c_uint, POINTER(_MAPPED))
    hr_map  = Map_t(ctypes.cast(vt_ctx, POINTER(c_void_p * 20)).contents[14])(  # type: ignore[arg-type]
        ctx.value, staging.value, 0, 1, 0, ctypes.byref(mapped))
    if hr_map != 0 or not mapped.pData:
        return None

    # ── 8. Copy to numpy ──────────────────────────────────────────────────────
    rp        = mapped.RowPitch
    assert mapped.pData is not None  # guaranteed: hr_map == 0 and mapped.pData checked above
    raw_bytes = (ctypes.c_ubyte * (h * rp)).from_address(mapped.pData)
    arr       = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(h, rp // 4, 4)[:, :w, :]
    bgr       = np.ascontiguousarray(arr[:, :, :3])  # BGRA → BGR (B8G8R8A8 bytes: B,G,R,A — drop A, already BGR)

    # ── 9. Unmap: vtable[15] ─────────────────────────────────────────────────
    CFUNC(None, c_void_p, c_void_p, c_uint)(
        ctypes.cast(vt_ctx, POINTER(c_void_p * 20)).contents[15])(  # type: ignore[arg-type]
        ctx.value, staging.value, 0)

    return bgr



class WGCCapture:
    """Windows Graphics Capture—captures window content regardless of occlusion.

    Requires Windows 10 version 1903 (build 18334) or later, and
    ``pip install winsdk``.  dxcam is not required.

    Unlike mss/dxcam (which capture screen pixels), WGC reads the application's
    GPU surface directly.  VS Code, a browser, or any other window can be in
    front of Tibia and this backend will still capture Tibia correctly.

    Parameters
    ----------
    hwnd : int
        Window handle of the target application.
    """

    def __init__(self, hwnd: int) -> None:
        self._hwnd       = hwnd
        self._pool: Any  = None
        self._session: Any = None
        self._dev: Any   = None   # ctypes c_void_p — raw ID3D11Device
        self._ctx: Any   = None   # ctypes c_void_p — raw ID3D11DeviceContext
        self._staging_cache: list[Any] = []  # [ptr, w, h] reuse staging texture

    def open(self) -> Callable[[], Optional[np.ndarray]]:
        try:
            import winsdk  # noqa — availability check  # type: ignore[reportMissingImports]
        except ImportError as exc:
            raise RuntimeError(
                "winsdk is required for the 'wgc' backend.  "
                "Install it with: pip install winsdk"
            ) from exc

        dev, ctx = _wgc_create_d3d11_device()
        self._dev = dev
        self._ctx = ctx

        wd3d = _wgc_d3d_device_to_winrt(dev, ctx)

        from winsdk.windows.graphics.capture.interop import create_for_window
        from winsdk.windows.graphics.capture import Direct3D11CaptureFramePool
        from winsdk.windows.graphics.directx import DirectXPixelFormat

        item = create_for_window(self._hwnd)
        self._pool = Direct3D11CaptureFramePool.create(
            wd3d,
            DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED,
            2,
            item.size,
        )
        if self._pool is None:
            raise RuntimeError("Failed to create Direct3D11CaptureFramePool")
        self._session = self._pool.create_capture_session(item)
        if self._session is None:
            raise RuntimeError("Failed to create capture session")
        self._session.start_capture()

        pool            = self._pool
        dev_ref         = self._dev
        ctx_ref         = self._ctx
        staging_cache   = self._staging_cache
        last_frame: List[Optional[np.ndarray]] = [None]

        def _grab() -> Optional[np.ndarray]:
            if pool is None:
                return last_frame[0]
            frame = pool.try_get_next_frame()
            if frame is None:
                return last_frame[0]
            try:
                bgr = _wgc_surface_to_numpy(
                    frame.surface, dev_ref, ctx_ref, staging_cache)
                if bgr is not None:
                    last_frame[0] = bgr
                return last_frame[0]
            except Exception:
                logger.debug("WGCCapture failed to convert frame surface", exc_info=True)
                return last_frame[0]
            finally:
                # Release the frame back to the pool so WGC can deliver new ones.
                try:
                    frame.close()
                except Exception:
                    logger.debug("WGCCapture failed to close frame", exc_info=True)

        return _grab

    def close(self) -> None:
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            logger.debug("WGCCapture failed to close session", exc_info=True)
        try:
            if self._pool is not None:
                self._pool.close()
        except Exception:
            logger.debug("WGCCapture failed to close frame pool", exc_info=True)
        self._pool    = None
        self._session = None
        self._dev     = None
        self._ctx     = None
        # Release the cached staging texture if any
        if self._staging_cache and self._staging_cache[0]:
            import ctypes
            _st_ptr = self._staging_cache[0]
            try:
                _vt   = ctypes.cast(_st_ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value
                assert _vt is not None  # guaranteed: _st_ptr.value checked above
                _rl_t = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
                _rl_t(ctypes.cast(_vt, ctypes.POINTER(ctypes.c_void_p * 3)).contents[2])(_st_ptr.value)  # type: ignore[arg-type]
            except Exception:
                logger.debug("WGCCapture failed to release staging texture", exc_info=True)
        self._staging_cache.clear()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_SOURCES = {
    "mss":          MssCapture,
    "dxcam":        DxcamCapture,
    "printwindow":  PrintWindowCapture,
    "rtmp":         RtmpCapture,
    "obs":          VirtualCameraCapture,
    "virtualcam":   VirtualCameraCapture,
    "wgc":          WGCCapture,
}


def build_frame_getter(
    source: str,
    **kwargs: Any,
) -> Callable[[], Optional[np.ndarray]]:
    """Return a ``() -> BGR ndarray | None`` callable for the requested source.

    Parameters
    ----------
    source : str
        One of ``"mss"``, ``"dxcam"``, ``"printwindow"``, ``"rtmp"``, ``"wgc"``.
    **kwargs
        Forwarded to the backend constructor.

    Examples
    --------
    >>> get = build_frame_getter("mss")
    >>> get = build_frame_getter("wgc", hwnd=0x304DA)
    >>> get = build_frame_getter("rtmp", url="rtmp://localhost/live/tibia",
    ...                          ffmpeg_window="Tibia", fps=10)
    >>> get = build_frame_getter("printwindow", hwnd=0xABCD)
    """
    key = source.lower().strip()
    cls = _SOURCES.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown frame source '{source}'. "
            f"Valid options: {sorted(_SOURCES)}"
        )
    instance = cls(**kwargs)
    getter: Callable[[], Optional[np.ndarray]] = instance.open()
    # Attach close handle so callers can release GPU/COM/FFmpeg resources.
    getter.close = getattr(instance, "close", lambda: None)  # type: ignore[attr-defined]
    return getter
