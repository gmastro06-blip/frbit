#!/usr/bin/env python
"""
Pre-flight Check — Fase C readiness
------------------------------------
Validates everything needed to run the bot with a real Tibia client:
- Route file validity (waypoints, coordinates on floor 7)
- Config files present and valid
- Templates available
- Session/CLI dry-run parsing
- Input controller dry-run
- Dependencies installed

Usage:
    python tools/preflight_check.py
    python tools/preflight_check.py --route routes/thais_route_simple.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_icon(ok: bool) -> str:
    return "[OK]" if ok else "[FAIL]"


def check_route(route_path: Path) -> List[str]:
    """Validate route file. Returns list of issues."""
    issues: List[str] = []
    if not route_path.exists():
        issues.append(f"Route file not found: {route_path}")
        return issues

    try:
        data = json.loads(route_path.read_text(encoding="utf-8"))
    except Exception as e:
        issues.append(f"Route JSON parse error: {e}")
        return issues

    waypoints = data.get("waypoints", [])
    # Also support unified JSON routes with a script or recorded entries array.
    if not waypoints:
        waypoints = [s for s in data.get("script", []) if "x" in s and "y" in s]
    if not waypoints:
        waypoints = [s for s in data.get("entries", []) if "x" in s and "y" in s]
    if not waypoints:
        issues.append("Route has 0 waypoints")
        return issues

    for i, wp in enumerate(waypoints):
        if not all(k in wp for k in ("x", "y", "z")):
            issues.append(f"Waypoint {i}: missing x/y/z keys")
        elif not (30000 <= wp["x"] <= 35000 and 30000 <= wp["y"] <= 35000):
            issues.append(f"Waypoint {i}: coords out of Tibia range ({wp['x']}, {wp['y']})")

    return issues


def check_configs() -> List[str]:
    """Check all config files exist and are valid JSON."""
    issues: List[str] = []
    configs = [
        "hpmp_config.json",
        "combat_config.json",
        "minimap_config.json",
        "detector_config.json",
    ]
    for name in configs:
        path = ROOT / name
        if not path.exists():
            issues.append(f"Config missing: {name}")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            issues.append(f"Config invalid JSON: {name} ({e})")

    return issues


def check_templates() -> Dict[str, int]:
    """Count templates in each category."""
    cache = ROOT / "cache" / "templates"
    counts: Dict[str, int] = {}
    for subdir in ["monsters", "corpses", "loot_items", "trade_items"]:
        d = cache / subdir
        if d.is_dir():
            pngs = list(d.glob("*.png"))
            counts[subdir] = len(pngs)
        else:
            counts[subdir] = 0
    return counts


def check_dependencies() -> List[str]:
    """Check key runtime dependencies are importable."""
    issues: List[str] = []
    deps = [
        ("cv2", "opencv-python"),
        ("numpy", "numpy"),
        ("mss", "mss"),
    ]
    for mod, pkg in deps:
        try:
            importlib.import_module(mod)
        except ImportError:
            issues.append(f"Missing dependency: {pkg} (import {mod})")

    # Optional but recommended
    optional = [
        ("dxcam", "dxcam"),
    ]
    for mod, pkg in optional:
        try:
            importlib.import_module(mod)
        except ImportError:
            pass  # Optional, don't report as issue

    return issues


def check_session_config(route: str) -> List[str]:
    """Try to construct a SessionConfig and check it's valid."""
    issues: List[str] = []
    try:
        from src.session import SessionConfig
        cfg = SessionConfig(
            route_file=route,
            frame_source="mss",
            input_method="postmessage",
            dry_run=True,
        )
        # Validate route resolution
        if route:
            from src.session import _resolve_route
            _resolve_route(route)
    except FileNotFoundError as e:
        issues.append(f"Route resolution failed: {e}")
    except Exception as e:
        issues.append(f"SessionConfig creation failed: {e}")
    return issues


def check_input_controller() -> List[str]:
    """Check InputController can be instantiated (dry/no-window mode)."""
    issues: List[str] = []
    try:
        from src.input_controller import InputController
        ctrl = InputController("__nonexistent_window__", input_method="postmessage")
        # Should not crash, just won't find the window
    except Exception as e:
        issues.append(f"InputController init failed: {e}")
    return issues


def check_critical_templates() -> List[str]:
    """Check death_screen.png and login_screen.png exist."""
    issues: List[str] = []
    cache = ROOT / "cache" / "templates"
    for name in ("death_screen.png", "login_screen.png"):
        if not (cache / name).is_file():
            issues.append(f"Critical template missing: cache/templates/{name}")
    return issues


def check_hpmp_roi() -> List[str]:
    """Validate HP/MP ROI dimensions in hpmp_config.json are sane."""
    issues: List[str] = []
    path = ROOT / "hpmp_config.json"
    if not path.exists():
        return issues  # already reported by check_configs
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("hp_roi", "mp_roi"):
            roi = data.get(key)
            if roi is None:
                issues.append(f"hpmp_config.json: '{key}' missing")
                continue
            if len(roi) != 4:
                issues.append(f"hpmp_config.json: '{key}' should have 4 values [x,y,w,h], got {len(roi)}")
                continue
            x, y, w, h = roi
            if w <= 0 or h <= 0:
                issues.append(f"hpmp_config.json: '{key}' has invalid dimensions w={w}, h={h}")
            if x < 0 or y < 0:
                issues.append(f"hpmp_config.json: '{key}' has negative offset x={x}, y={y}")
    except Exception as e:
        issues.append(f"hpmp_config.json parse error: {e}")
    return issues


def check_pico2() -> tuple[bool, str]:
    """Check if Pico2 HID is reachable. Returns (ok, message)."""
    try:
        import serial.tools.list_ports  # type: ignore
        ports = list(serial.tools.list_ports.comports())
        pico_ports = [p for p in ports if "pico" in (p.description or "").lower()
                      or "2e8a" in (p.hwid or "").lower()]
        if pico_ports:
            return True, f"Pico2 found on {pico_ports[0].device} ({pico_ports[0].description})"
        com_list = ", ".join(p.device for p in ports) if ports else "none"
        return False, f"Pico2 not detected (available ports: {com_list})"
    except ImportError:
        return False, "pyserial not installed (pip install pyserial)"
    except Exception as e:
        return False, f"Port scan error: {e}"


def _live_capture_kwargs(source: str, window_title: str) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    kwargs: dict[str, Any] = {}
    source_key = source.lower().strip()
    if source_key in {"printwindow", "wgc", "mss", "dxcam"}:
        from src.input_controller import find_window

        window = find_window(window_title)
        if window is None:
            issues.append(f"Window not found for live capture: '{window_title}'")
            return kwargs, issues
        if source_key in {"printwindow", "wgc", "mss"}:
            kwargs["hwnd"] = window.hwnd
    return kwargs, issues


def check_live_frame_capture(source: str = "mss", window_title: str = "Tibia") -> List[str]:
    """Actually capture a frame and verify it is non-black and correctly sized.

    Requires Tibia to be running and visible on screen.
    Returns an empty list on success; skips silently if capture raises ImportError.
    """
    issues: List[str] = []
    try:
        import numpy as np
        from src.frame_capture import build_frame_getter
        kwargs, setup_issues = _live_capture_kwargs(source, window_title)
        if setup_issues:
            return setup_issues
        getter = build_frame_getter(source, **kwargs)
        frame = getter()
    except ImportError:
        return issues  # optional backend not installed — not a blocker
    except Exception as exc:
        issues.append(f"Frame capture ({source}) could not start: {exc}")
        return issues

    if frame is None:
        issues.append(
            f"Frame capture ({source}) returned None — "
            "is Tibia open and not minimized?"
        )
        return issues

    h, w = frame.shape[:2]
    mean_val = float(frame.mean())

    if mean_val < 8.0:
        issues.append(
            f"Frame is nearly black (mean={mean_val:.1f}) — "
            "window may be minimized or behind another window"
        )
    if w < 800 or h < 600:
        issues.append(
            f"Frame too small: {w}×{h} — "
            "expected at least 800×600 (check capture region)"
        )

    return issues


def check_live_hpmp_roi(source: str = "mss", window_title: str = "Tibia") -> List[str]:
    """Capture a live frame and verify HP/MP ROIs contain the expected bar colours.

    HP bar   → green pixels  (G channel dominant)
    MP bar   → blue pixels   (B channel dominant)

    An empty ROI or wrong colours means the calibration is off for this machine.
    Requires Tibia to be running with bars visible.  Skips silently on import errors.
    """
    issues: List[str] = []
    config_path = ROOT / "hpmp_config.json"
    if not config_path.exists():
        return issues  # already reported by check_configs

    try:
        import json
        import numpy as np
        from src.frame_capture import build_frame_getter

        data = json.loads(config_path.read_text(encoding="utf-8"))
        kwargs, setup_issues = _live_capture_kwargs(source, window_title)
        if setup_issues:
            return setup_issues
        getter = build_frame_getter(source, **kwargs)
        frame = getter()
    except ImportError:
        return issues
    except Exception as exc:
        issues.append(f"Live HP/MP ROI check: capture failed ({exc})")
        return issues

    if frame is None:
        issues.append("Live HP/MP ROI check: frame is None — Tibia not running?")
        return issues

    h_img, w_img = frame.shape[:2]

    def _check_roi(key: str, dominant: str) -> None:
        roi = data.get(key)
        if roi is None or len(roi) != 4:
            return  # structure check done by check_hpmp_roi
        x, y, rw, rh = roi
        if y + rh > h_img or x + rw > w_img or rw <= 0 or rh <= 0:
            issues.append(
                f"{key} [{x},{y},{rw},{rh}] falls outside frame "
                f"({w_img}×{h_img}) — ROI miscalibrated"
            )
            return
        crop = frame[y: y + rh, x: x + rw].astype(int)
        b, g, r = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        if dominant == "green":
            colored = int(((g > 80) & ((g - r) > 25) & ((g - b) > 25)).sum())
            color_name = "green"
        else:  # blue
            colored = int(((b > 80) & ((b - r) > 25) & ((b - g) > 15)).sum())
            color_name = "blue"
        if colored < 5:
            issues.append(
                f"{key}: only {colored} {color_name} pixel(s) found in ROI — "
                "HP/MP bar not visible or ROI position is wrong for this resolution"
            )

    _check_roi("hp_roi", "green")
    _check_roi("mp_roi", "blue")
    return issues


def check_map_data() -> List[str]:
    """Check map data files exist for floor 7 (Thais surface)."""
    issues: List[str] = []
    data_dir = ROOT / "data"
    if not data_dir.exists():
        issues.append("data/ directory not found")
        return issues

    # Check for floor 7 map file
    floor7_files = list(data_dir.glob("*07*")) + list(data_dir.glob("*7*"))
    if not floor7_files:
        # Try to check via MapLoader
        try:
            from src.map_loader import TibiaMapLoader
            from src.models import Coordinate
            loader = TibiaMapLoader()
            if loader.is_walkable(Coordinate(32369, 32241, 7)):
                pass  # Good, Thais temple area is walkable
            else:
                issues.append("Thais temple (32369,32241,7) not walkable -- map data issue")
        except Exception as e:
            issues.append(f"MapLoader check failed: {e}")

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-flight check for Fase C")
    parser.add_argument("--route", default="routes/thais_route_simple.json",
                        help="Route file to validate")
    parser.add_argument("--live", action="store_true",
                        help="Also capture a real frame and validate ROI colours "
                             "(requires Tibia running and visible)")
    parser.add_argument("--frame-source", default="mss",
                        help="Frame capture backend for --live checks (default: mss)")
    parser.add_argument("--frame-window", default="Tibia",
                        help="Window title for --live checks (default: Tibia)")
    args = parser.parse_args()

    route_path = ROOT / args.route if not Path(args.route).is_absolute() else Path(args.route)

    print("=" * 65)
    print("  PRE-FLIGHT CHECK — Fase C Readiness")
    print("=" * 65)
    total_issues = 0

    # 1. Dependencies
    print("\n-- Dependencies --")
    dep_issues = check_dependencies()
    print(f"  {check_icon(not dep_issues)} Runtime dependencies")
    for iss in dep_issues:
        print(f"       ! {iss}")
    total_issues += len(dep_issues)

    # 2. Config files
    print("\n-- Configuration --")
    cfg_issues = check_configs()
    print(f"  {check_icon(not cfg_issues)} Config files (4 JSON configs)")
    for iss in cfg_issues:
        print(f"       ! {iss}")
    total_issues += len(cfg_issues)

    # 3. Route
    print("\n-- Route --")
    route_issues = check_route(route_path)
    if not route_issues:
        data = json.loads(route_path.read_text(encoding="utf-8"))
        wps = data.get("waypoints", [])
        if not wps:
            wps = [s for s in data.get("script", []) if "x" in s and "y" in s]
        if not wps:
            wps = [s for s in data.get("entries", []) if "x" in s and "y" in s]
        if "script" in data:
            fmt = "script"
        elif "entries" in data:
            fmt = "entries"
        else:
            fmt = "waypoints"
        print(f"  [OK]  {args.route} ({len(wps)} {fmt})")
        # Print waypoint summary
        if wps:
            first = wps[0]
            last = wps[-1]
            print(f"        Start: ({first['x']}, {first['y']}, {first['z']})")
            print(f"        End:   ({last['x']}, {last['y']}, {last['z']})")
    else:
        print(f"  [FAIL] Route: {args.route}")
        for iss in route_issues:
            print(f"       ! {iss}")
    total_issues += len(route_issues)

    # 4. Templates
    print("\n-- Templates --")
    counts = check_templates()
    total_templates = sum(counts.values())
    print(f"  {check_icon(total_templates > 0)} Templates: {total_templates} total")
    for cat, n in counts.items():
        tag = "[OK]" if n > 0 else "[WARN]"
        print(f"        {tag} {cat}: {n}")

    # 5. Map data
    print("\n-- Map Data --")
    map_issues = check_map_data()
    print(f"  {check_icon(not map_issues)} Map data for floor 7 (Thais)")
    for iss in map_issues:
        print(f"       ! {iss}")
    total_issues += len(map_issues)

    # 6. Session config
    print("\n-- Session Config --")
    sess_issues = check_session_config(args.route)
    print(f"  {check_icon(not sess_issues)} SessionConfig(route, frame_source=mss, dry_run=True)")
    for iss in sess_issues:
        print(f"       ! {iss}")
    total_issues += len(sess_issues)

    # 7. Input controller
    print("\n-- Input Controller --")
    input_issues = check_input_controller()
    print(f"  {check_icon(not input_issues)} InputController(postmessage)")
    for iss in input_issues:
        print(f"       ! {iss}")
    total_issues += len(input_issues)

    # 8. Critical templates (death / login)
    print("\n-- Critical Templates --")
    tpl_issues = check_critical_templates()
    print(f"  {check_icon(not tpl_issues)} death_screen.png & login_screen.png")
    for iss in tpl_issues:
        print(f"       ! {iss}")
    total_issues += len(tpl_issues)

    # 9. HP/MP ROI validation
    print("\n-- HP/MP ROI --")
    roi_issues = check_hpmp_roi()
    print(f"  {check_icon(not roi_issues)} hpmp_config.json ROI dimensions")
    for iss in roi_issues:
        print(f"       ! {iss}")
    total_issues += len(roi_issues)

    # 10. Live frame capture + ROI colour check (only when --live passed)
    if args.live:
        print("\n-- Live Frame Capture --")
        live_issues = check_live_frame_capture(args.frame_source, args.frame_window)
        print(f"  {check_icon(not live_issues)} Frame capture ({args.frame_source}, "
              f"window='{args.frame_window}')")
        for iss in live_issues:
            print(f"       ! {iss}")
        total_issues += len(live_issues)

        print("\n-- Live HP/MP ROI Colours --")
        roi_live_issues = check_live_hpmp_roi(args.frame_source, args.frame_window)
        if not live_issues:  # only meaningful if we got a valid frame
            print(f"  {check_icon(not roi_live_issues)} "
                  "HP bar green pixels & MP bar blue pixels visible")
            for iss in roi_live_issues:
                print(f"       ! {iss}")
            total_issues += len(roi_live_issues)
        else:
            print("  [SKIP] Cannot check ROI colours without a valid frame")
    else:
        print("\n  [INFO] Run with --live to validate actual frame capture and ROI colours")
        print("         (requires Tibia to be running and visible on screen)")

    # 12. Pico2 HID (optional)
    print("\n-- Pico 2 HID (optional) --")
    pico_ok, pico_msg = check_pico2()
    tag = "[OK]" if pico_ok else "[WARN]"
    print(f"  {tag}  {pico_msg}")

    # 13. CLI command reference
    print("\n-- CLI Commands Ready --")
    print("  Dry-run:")
    print(f"    python main.py run --route {args.route} --frame-source mss --dry-run")
    print("  Full AFK (combat+heal+loot+loop+pico):")
    print(f"    python main.py run --route {args.route} --frame-source printwindow \\")
    print(f"      --frame-window \"Proyector\" --combat --heal 70 --loot --loop \\")
    print(f"      --pico --gm-detector --dashboard --start-delay 5")
    print("  Same but disable breaks (continuous):")
    print(f"    python main.py run --route {args.route} --frame-source printwindow \\")
    print(f"      --frame-window \"Proyector\" --combat --heal 70 --loot --loop \\")
    print(f"      --pico --no-break --start-delay 5")

    # Summary
    print("\n" + "=" * 65)
    if total_issues == 0:
        print("  PREFLIGHT: ALL CHECKS PASSED -- ready for Fase C")
    else:
        print(f"  PREFLIGHT: {total_issues} issue(s) found")
    print("=" * 65)

    sys.exit(1 if total_issues > 0 else 0)


if __name__ == "__main__":
    main()
