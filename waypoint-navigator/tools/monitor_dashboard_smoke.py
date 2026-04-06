from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.session import BotSession, SessionConfig


class SmokeFailure(RuntimeError):
    """Raised when a smoke check fails validation."""


def _classify_live_window(title: str) -> Optional[str]:
    normalized = title.casefold()
    if "tibia" not in normalized:
        return None
    if "proyector" in normalized or "projector" in normalized:
        return "projector"
    return "tibia"


def discover_live_windows() -> dict[str, list[dict[str, Any]]]:
    if sys.platform != "win32":
        raise SmokeFailure("live mode requires Windows")

    try:
        from src.input_controller import list_windows
    except Exception as exc:
        raise SmokeFailure(f"live mode could not enumerate windows: {exc}") from exc

    windows: dict[str, list[dict[str, Any]]] = {"tibia": [], "projector": []}
    for window in list_windows():
        kind = _classify_live_window(window.title)
        if kind is None:
            continue
        windows[kind].append({"hwnd": int(window.hwnd), "title": window.title})
    return windows


def _select_live_window(
    windows: dict[str, list[dict[str, Any]]],
    *,
    frame_window: str,
) -> dict[str, Any]:
    candidates = windows["projector"] + windows["tibia"]
    if frame_window:
        fragment = frame_window.casefold()
        for window in candidates:
            if fragment in str(window["title"]).casefold():
                return window
        raise SmokeFailure(f"live frame window not found: {frame_window!r}")

    if windows["projector"]:
        return windows["projector"][0]
    if windows["tibia"]:
        return windows["tibia"][0]
    raise SmokeFailure("live mode did not find a Tibia or OBS projector window")


def _live_source_candidates(frame_source: str) -> list[str]:
    source = frame_source.strip().lower()
    if source:
        return [source]
    return ["wgc", "mss"]


def _live_source_kwargs(
    source: str,
    *,
    capture_window: dict[str, Any],
    monitor_idx: Optional[int],
) -> dict[str, Any]:
    hwnd = int(capture_window["hwnd"])
    kwargs: dict[str, Any] = {}

    if source == "mss":
        if monitor_idx is not None:
            kwargs["monitor_idx"] = monitor_idx
        else:
            kwargs["hwnd"] = hwnd
    elif source == "dxcam":
        kwargs["hwnd"] = hwnd
        if monitor_idx is not None:
            kwargs["output_idx"] = max(monitor_idx - 1, 0)
    elif source in {"wgc", "printwindow"}:
        kwargs["hwnd"] = hwnd

    return kwargs


def _close_frame_getter(frame_getter: Any) -> None:
    close = getattr(frame_getter, "close", None)
    if callable(close):
        close()


def _read_live_frame(frame_getter: Any, *, capture_attempts: int) -> Any:
    for attempt in range(capture_attempts):
        frame = frame_getter()
        if frame is not None:
            return frame
        if attempt + 1 < capture_attempts:
            time.sleep(0.2)
    raise SmokeFailure("live capture returned no frames")


def capture_live_frame(
    *,
    frame_source: str,
    frame_window: str,
    monitor_idx: Optional[int],
    capture_attempts: int,
) -> dict[str, Any]:
    from src.frame_capture import build_frame_getter

    windows = discover_live_windows()
    capture_window = _select_live_window(windows, frame_window=frame_window)
    source_errors: list[str] = []

    for source in _live_source_candidates(frame_source):
        frame_getter = None
        kwargs = _live_source_kwargs(
            source,
            capture_window=capture_window,
            monitor_idx=monitor_idx,
        )
        try:
            frame_getter = build_frame_getter(source, **kwargs)
            frame = _read_live_frame(frame_getter, capture_attempts=capture_attempts)
            return {
                "frame": frame,
                "source": source,
                "capture_window": capture_window,
                "projector_windows": [item["title"] for item in windows["projector"]],
                "tibia_windows": [item["title"] for item in windows["tibia"]],
            }
        except Exception as exc:
            source_errors.append(f"{source}: {exc}")
        finally:
            if frame_getter is not None:
                _close_frame_getter(frame_getter)

    joined_errors = "; ".join(source_errors) if source_errors else "no frame source attempted"
    raise SmokeFailure(f"live capture failed: {joined_errors}")


def detect_live_position(session: BotSession, frame: Any) -> Optional[dict[str, int]]:
    from src.minimap_radar import MinimapRadar

    loader = session.monitor_loader()
    if loader is None:
        raise SmokeFailure("live mode could not initialize the minimap loader")

    radar = MinimapRadar(loader=loader)
    hint = session.current_position(allow_route_seed=True)
    coord = radar.read(frame, hint=hint)
    if coord is None:
        return None

    session._set_position(coord)
    return {"x": coord.x, "y": coord.y, "z": coord.z}


def run_live_check(
    session: BotSession,
    *,
    frame_source: str,
    frame_window: str,
    monitor_idx: Optional[int],
    capture_attempts: int,
    require_position: bool,
) -> dict[str, Any]:
    capture = capture_live_frame(
        frame_source=frame_source,
        frame_window=frame_window,
        monitor_idx=monitor_idx,
        capture_attempts=capture_attempts,
    )
    frame = capture.pop("frame")
    if getattr(frame, "ndim", 0) < 2:
        raise SmokeFailure("live capture returned an invalid frame")

    frame_height, frame_width = frame.shape[:2]
    position = None
    position_error: Optional[str] = None
    try:
        position = detect_live_position(session, frame)
    except Exception as exc:
        position_error = str(exc)

    if require_position and position is None:
        raise SmokeFailure(position_error or "live mode could not resolve a minimap position")

    result = {
        **capture,
        "frame": {
            "width": int(frame_width),
            "height": int(frame_height),
            "mean_brightness": round(float(frame.mean()), 2),
        },
        "position": position,
        "position_detected": position is not None,
    }
    if position_error is not None:
        result["position_error"] = position_error
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke test the shared monitor/dashboard UI contract.",
    )
    parser.add_argument(
        "--route",
        default="routes/thais_wasp_24h_session.json",
        help="Route file used to seed route name and start position.",
    )
    parser.add_argument(
        "--uptime-seconds",
        type=float,
        default=5.0,
        help="Synthetic uptime used for the dry-run smoke session.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run guarded live capture checks against a real Tibia/OBS window.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required alongside --live so real-environment checks are never accidental.",
    )
    parser.add_argument(
        "--frame-source",
        default="",
        help="Explicit live frame source override (for example: wgc, mss, printwindow, dxcam).",
    )
    parser.add_argument(
        "--frame-window",
        default="",
        help="Optional window title fragment used to select the live capture window.",
    )
    parser.add_argument(
        "--monitor-idx",
        type=int,
        default=None,
        help="Optional 1-based monitor index used by monitor-based live capture backends.",
    )
    parser.add_argument(
        "--capture-attempts",
        type=int,
        default=10,
        help="Maximum live frame reads before the smoke fails.",
    )
    parser.add_argument(
        "--require-position",
        action="store_true",
        help="Fail the live smoke if the minimap position cannot be resolved from the captured frame.",
    )
    parser.add_argument(
        "--skip-monitor",
        action="store_true",
        help="Skip the Tk monitor build/poll check.",
    )
    parser.add_argument(
        "--skip-dashboard",
        action="store_true",
        help="Skip the dashboard HTTP/WebSocket check.",
    )
    parser.add_argument(
        "--skip-websocket",
        action="store_true",
        help="Skip the dashboard WebSocket payload check.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation for the final summary output.",
    )
    return parser


def build_smoke_session(route_file: str, uptime_seconds: float) -> BotSession:
    session = BotSession(SessionConfig(dry_run=True, route_file=route_file))
    session._running = True
    session._stats["start_time"] = time.time() - uptime_seconds
    return session


def run_snapshot_check(session: BotSession) -> dict[str, Any]:
    snapshot = session.monitor_snapshot()
    route_name = Path(session.config.route_file).name
    if snapshot.get("route") != route_name:
        raise SmokeFailure(
            f"snapshot route mismatch: expected {route_name!r}, got {snapshot.get('route')!r}",
        )

    position = snapshot.get("position")
    if not isinstance(position, dict) or not {"x", "y", "z"}.issubset(position):
        raise SmokeFailure(f"snapshot position missing xyz keys: {position!r}")

    uptime_seconds = snapshot.get("uptime_seconds")
    if not isinstance(uptime_seconds, (int, float)):
        raise SmokeFailure(f"snapshot uptime_seconds is not numeric: {uptime_seconds!r}")

    return {
        "route": snapshot.get("route"),
        "position": position,
        "current_wpt": snapshot.get("current_wpt"),
        "uptime_seconds": uptime_seconds,
    }


def run_monitor_check(session: BotSession) -> dict[str, Any]:
    import tkinter as tk

    from src.monitor_gui import MonitorGui

    root = tk.Tk()
    root.withdraw()
    gui = MonitorGui(session=session, root=root)
    try:
        gui.build()
        gui._poll()
        state = gui.state
    finally:
        gui.close()

    route_name = Path(session.config.route_file).name
    if not gui.is_built:
        raise SmokeFailure("monitor GUI did not build successfully")
    if state.get("route") != route_name:
        raise SmokeFailure(
            f"monitor route mismatch: expected {route_name!r}, got {state.get('route')!r}",
        )
    if not isinstance(state.get("uptime"), str) or not state["uptime"]:
        raise SmokeFailure(f"monitor uptime is empty: {state.get('uptime')!r}")

    return {
        "built": gui.is_built,
        "route": state.get("route"),
        "current_wpt": state.get("current_wpt"),
        "uptime": state.get("uptime"),
    }


async def read_websocket_payload(ws_url: str) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url) as websocket:
        raw_message = await websocket.recv()
    payload = json.loads(raw_message)
    return {
        "type": payload.get("type"),
        "route": payload.get("stats", {}).get("route"),
        "position": payload.get("stats", {}).get("position"),
        "uptime_seconds": payload.get("stats", {}).get("uptime_seconds"),
        "clients": payload.get("clients"),
    }


def run_websocket_check(ws_url: str) -> dict[str, Any]:
    return asyncio.run(read_websocket_payload(ws_url))


def run_dashboard_check(
    session: BotSession,
    *,
    require_websocket: bool,
) -> dict[str, Any]:
    from src.dashboard_server import DashboardServer

    server = DashboardServer()
    server.set_stats_fn(session.monitor_snapshot)
    try:
        server.start()
        with urllib.request.urlopen(f"{server.http_url}/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(server.http_url, timeout=5) as response:
            html = response.read().decode("utf-8")

        if health.get("status") != "ok":
            raise SmokeFailure(f"dashboard health not ok: {health!r}")

        subsystems = health.get("subsystems", {})
        expected_route = Path(session.config.route_file).name
        if subsystems.get("route") != expected_route:
            raise SmokeFailure(
                f"dashboard route mismatch: expected {expected_route!r}, got {subsystems.get('route')!r}",
            )
        if server.ws_url not in html:
            raise SmokeFailure("dashboard HTML does not contain the injected WebSocket URL")

        result: dict[str, Any] = {
            "http_url": server.http_url,
            "ws_url": server.ws_url,
            "health_status": health.get("status"),
            "route": subsystems.get("route"),
            "position": subsystems.get("position"),
            "ws_injected": True,
        }
        if require_websocket:
            result["websocket"] = run_websocket_check(server.ws_url)
        return result
    finally:
        server.stop()


def run_smoke(
    *,
    route_file: str,
    uptime_seconds: float,
    live: bool,
    confirm_live: bool,
    frame_source: str,
    frame_window: str,
    monitor_idx: Optional[int],
    capture_attempts: int,
    require_position: bool,
    skip_monitor: bool,
    skip_dashboard: bool,
    skip_websocket: bool,
) -> dict[str, Any]:
    if capture_attempts < 1:
        raise SmokeFailure("capture_attempts must be at least 1")
    if monitor_idx is not None and monitor_idx < 1:
        raise SmokeFailure("monitor_idx must be at least 1")
    if live and not confirm_live:
        raise SmokeFailure("live mode requires --confirm-live")

    session = build_smoke_session(route_file=route_file, uptime_seconds=uptime_seconds)
    summary: dict[str, Any] = {
        "route": route_file,
    }

    live_summary: Optional[dict[str, Any]] = None
    if live:
        live_summary = run_live_check(
            session,
            frame_source=frame_source,
            frame_window=frame_window,
            monitor_idx=monitor_idx,
            capture_attempts=capture_attempts,
            require_position=require_position,
        )
        summary["live"] = live_summary

    snapshot = run_snapshot_check(session)
    if live_summary is not None:
        snapshot["position_source"] = "live" if live_summary["position_detected"] else "route_seed"
        if live_summary["position"] is not None and snapshot["position"] != live_summary["position"]:
            raise SmokeFailure("snapshot position does not match the live detected position")
    summary["snapshot"] = snapshot

    if not skip_monitor:
        summary["monitor"] = run_monitor_check(session)

    if not skip_dashboard:
        summary["dashboard"] = run_dashboard_check(
            session,
            require_websocket=not skip_websocket,
        )

    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_smoke(
            route_file=args.route,
            uptime_seconds=args.uptime_seconds,
            live=args.live,
            confirm_live=args.confirm_live,
            frame_source=args.frame_source,
            frame_window=args.frame_window,
            monitor_idx=args.monitor_idx,
            capture_attempts=args.capture_attempts,
            require_position=args.require_position,
            skip_monitor=args.skip_monitor,
            skip_dashboard=args.skip_dashboard,
            skip_websocket=args.skip_websocket,
        )
    except SmokeFailure as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=True, indent=args.indent))
        return 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True, indent=args.indent))
        return 2

    print(json.dumps({"status": "ok", **summary}, ensure_ascii=True, indent=args.indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())