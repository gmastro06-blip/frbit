from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .frame_cache import FrameCache
from .frame_watchdog import FrameWatchdog


@dataclass
class CapturePipeline:
    frame_getter: Optional[Callable[..., Any]]
    frame_cache: Optional[FrameCache]
    cached_getter: Optional[Callable[..., Any]]
    frame_watchdog: Optional[FrameWatchdog]


def initialize_capture_pipeline(
    *,
    config: Any,
    ctrl: Any,
    event_bus: Any,
    log_fn: Callable[[str], None],
    build_frame_getter: Callable[..., Any],
    existing_frame_getter: Optional[Callable[..., Any]],
    existing_frame_cache: Optional[FrameCache],
) -> CapturePipeline:
    frame_getter = existing_frame_getter
    frame_cache = existing_frame_cache

    if frame_getter is None:
        frame_getter = _build_frame_source(
            config=config,
            ctrl=ctrl,
            log_fn=log_fn,
            build_frame_getter=build_frame_getter,
        )

    if frame_getter and frame_cache is None:
        frame_cache = FrameCache(frame_getter, ttl_ms=50.0)

    cached_getter = frame_cache.get_frame if frame_cache else None
    frame_watchdog: Optional[FrameWatchdog] = None
    if frame_cache is not None and not config.dry_run:
        frame_watchdog = FrameWatchdog()
        frame_watchdog.set_frame_getter(frame_cache.get_frame)
        frame_watchdog.set_restart_fn(frame_cache.invalidate)
        frame_watchdog.set_event_bus(event_bus)
        frame_watchdog.set_log_callback(log_fn)
        frame_watchdog.set_window_title(config.target_window)
        frame_watchdog.start()
        log_fn("FrameWatchdog started.")

    return CapturePipeline(
        frame_getter=frame_getter,
        frame_cache=frame_cache,
        cached_getter=cached_getter,
        frame_watchdog=frame_watchdog,
    )


def _build_frame_source(
    *,
    config: Any,
    ctrl: Any,
    log_fn: Callable[[str], None],
    build_frame_getter: Callable[..., Any],
) -> Optional[Callable[..., Any]]:
    source, source_explicit = _resolve_frame_source(config)
    tried_dxcam = False
    if source == "mss" and not source_explicit and importlib.util.find_spec("dxcam") is not None:
        source = "dxcam"
        tried_dxcam = True
        log_fn("DXCam available — upgrading from mss to dxcam.")

    if not source:
        return None

    hwnd = _resolve_capture_hwnd(config, ctrl, log_fn)
    try:
        kwargs = _build_source_kwargs(source, config, ctrl, hwnd, source_explicit)
        frame_getter = build_frame_getter(source, **kwargs)
        log_fn(f"Frame source '{source}' ready.")
        return frame_getter
    except Exception as first_error:
        if tried_dxcam and source == "dxcam":
            log_fn(f"[!] DXCam failed ({first_error}) — falling back to mss.")
            return _build_mss_fallback(config, log_fn, build_frame_getter, hwnd)
        log_fn(f"[!] Frame source '{source}' failed ({first_error}) — frames disabled.")
        return None


def _resolve_frame_source(config: Any) -> tuple[str, bool]:
    source = (config.frame_source or "").strip().lower()
    source_explicit = bool(source)
    if source:
        return source, source_explicit

    source = config.position_source.strip().lower()
    if source in ("mss", "minimap"):
        return "mss", False
    return "", False


def _resolve_capture_hwnd(config: Any, ctrl: Any, log_fn: Callable[[str], None]) -> int:
    hwnd = 0
    frame_window = config.frame_window.strip()
    if frame_window:
        from src.input_controller import find_window as find_window

        frame_window_info = find_window(frame_window)
        if frame_window_info:
            hwnd = frame_window_info.hwnd
            log_fn(f"Frame window '{frame_window}' → hwnd={hwnd:#x}")
        else:
            log_fn(f"[!] Frame window '{frame_window}' not found — using Tibia hwnd.")

    if hwnd:
        return hwnd

    if ctrl is not None and ctrl.is_connected():
        return ctrl.hwnd or 0
    return 0


def _build_source_kwargs(
    source: str,
    config: Any,
    ctrl: Any,
    hwnd: int,
    source_explicit: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    frame_window = config.frame_window.strip()

    if source == "mss":
        if config.monitor_idx != 1:
            kwargs["monitor_idx"] = config.monitor_idx
        elif hwnd and not source_explicit:
            kwargs["hwnd"] = hwnd
    elif source == "dxcam":
        kwargs["fps"] = config.rtmp_fps
    elif source in ("printwindow", "wgc"):
        if not hwnd:
            raise ValueError(
                f"{source} capture requires a resolved window handle "
                f"(target='{config.target_window}', frame_window='{frame_window or config.target_window}')"
            )
        kwargs["hwnd"] = hwnd
    elif source in ("obs", "virtualcam"):
        kwargs["device_index"] = config.obs_device_index
    elif source == "rtmp":
        kwargs["url"] = config.rtmp_url
        if config.rtmp_ffmpeg_window:
            kwargs["ffmpeg_window"] = config.rtmp_ffmpeg_window
        kwargs["fps"] = config.rtmp_fps

    return kwargs


def _build_mss_fallback(
    config: Any,
    log_fn: Callable[[str], None],
    build_frame_getter: Callable[..., Any],
    hwnd: int,
) -> Optional[Callable[..., Any]]:
    fallback_kwargs: dict[str, Any] = {}
    if config.monitor_idx != 1:
        fallback_kwargs["monitor_idx"] = config.monitor_idx
    elif hwnd:
        fallback_kwargs["hwnd"] = hwnd

    try:
        frame_getter = build_frame_getter("mss", **fallback_kwargs)
        log_fn("Frame source 'mss' ready (fallback).")
        return frame_getter
    except Exception as fallback_error:
        log_fn(f"[!] Frame source 'mss' also failed ({fallback_error}) — frames disabled.")
        return None