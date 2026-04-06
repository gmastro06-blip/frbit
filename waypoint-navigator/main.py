"""
WaypointNavigator CLI
---------------------
Usage:
    python main.py navigate --sx 32369 --sy 32241 --ex 32343 --ey 32211 --floor 7
    python main.py navigate-name "thais depot" "thais temple"
    python main.py search-waypoints thais
    python main.py floor-stats 7
    python main.py show-floor 7
    python main.py calibrate
    python main.py track --dest-name "temple"
"""

from __future__ import annotations

import argparse
import datetime as _dt_main
import logging
import signal
import sys
import time as _t_main
from pathlib import Path
from typing import Any, Optional

# Ensure stdout handles UTF-8 (e.g. ✓ ✗ characters on Windows cp1252 terminals)
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except AttributeError:
    pass

# Ensure src/ is importable even when running main.py directly
sys.path.insert(0, str(Path(__file__).parent))

from src.models import Coordinate
from src.navigator import WaypointNavigator
from src.visualizer import MapVisualizer
from src.character_detector import CharacterDetector, DetectorConfig


# ---------------------------------------------------------------------------
# Graceful shutdown helper (double Ctrl+C to force exit)
# ---------------------------------------------------------------------------

class _GracefulShutdown:
    """Context manager that installs SIGINT/SIGTERM handlers tied to a session.

    First signal  → calls ``session.stop()`` for a clean shutdown.
    Second signal → calls ``os._exit(1)`` to force-kill (in case stop() hangs).
    On exit resets the original handlers.
    """

    def __init__(self, session: Any) -> None:
        self._session = session
        self._orig_sigint: Any = None
        self._orig_sigterm: Any = None
        self._triggered = False

    def __enter__(self) -> "_GracefulShutdown":
        self._orig_sigint = signal.getsignal(signal.SIGINT)
        self._orig_sigterm = (
            signal.getsignal(signal.SIGTERM) if hasattr(signal, "SIGTERM") else None
        )
        signal.signal(signal.SIGINT, self._handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._handler)
        return self

    def __exit__(self, *_: Any) -> None:
        signal.signal(signal.SIGINT, self._orig_sigint or signal.SIG_DFL)
        if hasattr(signal, "SIGTERM") and self._orig_sigterm is not None:
            signal.signal(signal.SIGTERM, self._orig_sigterm)

    def _handler(self, signum: int, frame: Any) -> None:
        if self._triggered:
            print("\n[shutdown] Second signal — force exit.")
            import os
            os._exit(1)
        self._triggered = True
        name = "SIGINT" if signum == signal.SIGINT.value else "SIGTERM"
        print(f"\n[shutdown] {name} received — stopping session… "
              "(press Ctrl+C again to force)")
        try:
            self._session.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------

def cmd_navigate(args: argparse.Namespace) -> None:
    nav = WaypointNavigator()
    start = Coordinate(args.sx, args.sy, args.floor)
    end = Coordinate(args.ex, args.ey, args.floor)
    print(f"\nFinding path from {start} to {end} …\n")
    route = nav.navigate(start, end)
    print(route.summary())

    if route.found and args.save:
        viz = MapVisualizer(nav.loader)
        save_path = Path(args.save)
        viz.show_route(route, save_path=save_path)

    elif route.found and not args.no_viz:
        viz = MapVisualizer(nav.loader)
        viz.show_route(route)


def cmd_navigate_name(args: argparse.Namespace) -> None:
    nav = WaypointNavigator()
    floor = args.floor if args.floor >= 0 else None
    try:
        route = nav.navigate_by_name(args.start, args.end, floor=floor)
    except ValueError as exc:
        print(f"Error: {exc}")
        return

    print(route.summary())

    if route.found and args.save:
        viz = MapVisualizer(nav.loader)
        viz.show_route(route, save_path=Path(args.save), title=f"{args.start} → {args.end}")
    elif route.found and not args.no_viz:
        viz = MapVisualizer(nav.loader)
        viz.show_route(route, title=f"{args.start} → {args.end}")


def cmd_search(args: argparse.Namespace) -> None:
    nav = WaypointNavigator()
    floor = args.floor if args.floor >= 0 else None
    results = nav.find_waypoints(args.query, floor=floor)
    if not results:
        print(f"No waypoints found for '{args.query}'.")
        return
    print(f"\nFound {len(results)} waypoint(s) matching '{args.query}':\n")
    for wp in results[:50]:            # cap at 50 for readability
        print(f"  {wp}")


def cmd_floor_stats(args: argparse.Namespace) -> None:
    nav = WaypointNavigator()
    stats = nav.walkable_region_stats(args.floor)
    print(f"\nFloor {stats['floor']:02d} statistics:")
    print(f"  Total tiles    : {stats['total_tiles']:,}")
    print(f"  Walkable       : {stats['walkable_tiles']:,}  ({stats['pct_walkable']}%)")
    print(f"  Non-walkable   : {stats['non_walkable']:,}")


def cmd_show_floor(args: argparse.Namespace) -> None:
    nav = WaypointNavigator()
    waypoints = nav.find_waypoints("", floor=args.floor)   # all on this floor
    viz = MapVisualizer(nav.loader)
    save_path = Path(args.save) if args.save else None
    viz.show_floor(
        args.floor,
        waypoints=waypoints,
        title=f"Tibia – Floor {args.floor:02d}",
        save_path=save_path,
    )


def _wait_until(
    target_hhmm: str,
    log_fn: Any,
    _now_fn: Any = None,
    _sleep_fn: Any = None,
) -> None:
    """Block until *target_hhmm* ("HH:MM" local time). Wraps to next day if past.

    Parameters
    ----------
    target_hhmm:
        Target time as "HH:MM" string.
    log_fn:
        Callable that receives status messages.
    _now_fn:
        Injectable callable returning current datetime (for testing).
    _sleep_fn:
        Injectable callable replacing time.sleep (for testing).
    """
    now_fn    = _now_fn    or _dt_main.datetime.now
    sleep_fn  = _sleep_fn  or _t_main.sleep

    h, m = map(int, target_hhmm.split(":"))
    now = now_fn()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += _dt_main.timedelta(days=1)
        log_fn(
            f"[run] \u26a0  {target_hhmm} ya pas\u00f3 hoy \u2014 esperando hasta "
            f"MA\u00d1ANA ({target:%Y-%m-%d %H:%M})"
        )
    total_secs = (target - now).total_seconds()
    log_fn(f"[run] Esperando hasta {target_hhmm} ({total_secs / 60:.1f} min)…")
    while True:
        remaining = (target - now_fn()).total_seconds()
        if remaining <= 0:
            break
        sleep_fn(min(60.0, remaining))
        rem2 = (target - now_fn()).total_seconds()
        if rem2 > 60:
            log_fn(f"[run] Faltan {rem2 / 60:.1f} min para {target_hhmm}…")
    log_fn(f"[run] Hora alcanzada: {target_hhmm} — iniciando sesión.")


def cmd_run(args: argparse.Namespace) -> None:
    """Start a full BotSession (route walk + healer + optional looter/depot)."""
    import time as _time
    from src.session import BotSession, SessionConfig

    def _vk(s: str) -> int:
        """Accept decimal or hex strings: '0x70', '112', etc."""
        return int(s, 0)

    # Resolve --class to combat config file
    _combat_cfg = getattr(args, "combat_config", "")
    if not _combat_cfg and getattr(args, "char_class", "") != "":
        _class_map = {
            "knight":   "combat_config.json",
            "druid":    "combat_config_druid.json",
            "paladin":  "combat_config_paladin.json",
            "sorcerer": "combat_config_sorcerer.json",
        }
        _combat_cfg = _class_map.get(args.char_class, "")
        if _combat_cfg:
            print(f"[run] Using combat config for {args.char_class}: {_combat_cfg}")

    cfg = SessionConfig(
        route_file          = args.route,
        heal_hp_pct         = args.heal,
        heal_emergency_pct  = args.emergency_pct,
        mana_threshold_pct  = args.mana_pct,
        heal_hotkey_vk      = _vk(args.heal_vk),
        emergency_hotkey_vk = _vk(args.emergency_vk),
        mana_hotkey_vk      = _vk(args.mana_vk),
        auto_loot           = args.loot,
        depot_after_run     = args.depot,
        input_method        = args.input_method,
        target_window       = args.window,
        start_delay         = args.start_delay,
        loop_route          = args.loop,
        jitter_pct          = args.jitter,
        watchdog_timeout    = args.watchdog_timeout,
        step_delay_min      = args.step_delay_min,
        step_delay_max      = args.step_delay_max,
        position_source     = args.position_source,
        frame_source        = getattr(args, "frame_source", ""),
        frame_window        = getattr(args, "frame_window", ""),
        monitor_idx         = getattr(args, "monitor_idx", 2),
        auto_combat         = args.combat,
        monitor_conditions  = args.conditions,
        dry_run             = args.dry_run,
        combat_config_file    = _combat_cfg,
        condition_config_file = getattr(args, "condition_config", ""),
        start_pos             = getattr(args, "start_pos", ""),
        pico_enabled          = getattr(args, "pico", False),
        pico_port             = getattr(args, "pico_port", "auto"),
        gm_detector           = getattr(args, "gm_detector", False),
        gm_action             = getattr(args, "gm_action", "pause"),
        pvp_detector          = getattr(args, "pvp_detector", False),
        break_scheduler       = not getattr(args, "no_break", False),
        anti_kick_idle        = getattr(args, "anti_kick_idle", 300.0),
        re_equip_hotkeys      = getattr(args, "re_equip", ""),
        max_deaths            = getattr(args, "max_deaths", 0),
        dashboard             = getattr(args, "dashboard", False),
        dashboard_port        = getattr(args, "dashboard_port", 8080),
        shovel_hotkey_vk      = _vk(getattr(args, "shovel_vk", "0")),
        rope_hotkey_vk        = _vk(getattr(args, "rope_vk", "0")),
    )

    logs: list[str] = []

    def _log(msg: str) -> None:
        print(msg)
        logs.append(msg)

    start_at = getattr(args, "start_at", "")
    if start_at:
        _wait_until(start_at, print)

    session = BotSession(cfg, log_callback=_log)

    # ── Resume from checkpoint ─────────────────────────────────────────
    if getattr(args, "resume", False):
        from src.session_persistence import SessionCheckpoint
        ckpt = SessionCheckpoint.load()
        if ckpt and not ckpt.is_stale(max_age_seconds=86400):
            if ckpt.matches_route(args.route) and ckpt.waypoint_index > 0:
                cfg.resume_waypoint_index = ckpt.waypoint_index
                _log(f"[run] Resuming from checkpoint: waypoint #{ckpt.waypoint_index} "
                     f"(saved {ckpt.timestamp_iso})")
            else:
                _log("[run] --resume: checkpoint route mismatch or index=0 — starting fresh.")
        else:
            _log("[run] --resume: no valid checkpoint found — starting fresh.")

    # ── FriendHealer (Exura Sio ami) ───────────────────────────────────
    _friend_sio_vk      = int(getattr(args, "friend_sio_vk",      "0"), 0)
    _friend_gran_sio_vk = int(getattr(args, "friend_gran_sio_vk", "0"), 0)
    _fhealer: Any = None  # ctrl wired and start() called after session.start()
    if _friend_sio_vk or _friend_gran_sio_vk:
        from src.healer import FriendHealer, FriendHealConfig
        _fhcfg = FriendHealConfig(
            sio_hotkey_vk           = _friend_sio_vk,
            gran_sio_hotkey_vk      = _friend_gran_sio_vk,
            sio_threshold_pct       = getattr(args, "friend_sio_pct",      70),
            gran_sio_threshold_pct  = getattr(args, "friend_gran_sio_pct", 40),
        )
        _fhealer = FriendHealer(ctrl=None, config=_fhcfg)  # ctrl set below
        _log(f"[run] FriendHealer creado — Sio VK=0x{_friend_sio_vk:x}  "
             f"Gran Sio VK=0x{_friend_gran_sio_vk:x}")
    print(f"\n[run] BotSession iniciado — ruta: {args.route or '(ninguna)'}")
    fs = getattr(args, "frame_source", "") or "(auto)"
    print(f"      heal={args.heal}%  loop={args.loop}  método={args.input_method}  frames={fs}")
    if args.dry_run:
        print("      [DRY-RUN] modo simulación — ningún input real enviado")
    if args.start_delay:
        print(f"      Espera {args.start_delay}s antes del primer movimiento…")

    if getattr(args, "monitor", False):
        # GUI runs on main thread; session loop runs in background thread
        with _GracefulShutdown(session):
            session.start()
            if _fhealer is not None:
                _fhealer._ctrl = session._ctrl
                _fhealer.start()
            try:
                session.open_monitor()
            finally:
                session.stop()
                snap = session.stats_snapshot()
                print(f"\n[run] Sesión terminada — ciclos: {snap.get('cycle_count', 0)} "
                      f"| waypoints: {snap.get('waypoints_visited', 0)}")
    else:
        with _GracefulShutdown(session):
            try:
                session.start()
                if _fhealer is not None:
                    _fhealer._ctrl = session._ctrl
                    _fhealer.start()
                while session.is_running:
                    _time.sleep(0.5)
            except KeyboardInterrupt:
                print("\n[run] Ctrl+C — deteniendo sesión…")
            finally:
                session.stop()
                snap = session.stats_snapshot()
                print(f"\n[run] Sesión terminada — ciclos: {snap.get('cycle_count', 0)} "
                  f"| waypoints: {snap.get('waypoints_visited', 0)}")


def cmd_run_script(args: argparse.Namespace) -> None:
    """Parse and execute a .in bot script via BotSession."""
    import time as _time
    from src.session import BotSession, SessionConfig

    def _vk(s: str) -> int:
        return int(s, 0)

    # Resolve --class to combat config file
    _combat_cfg_rs = getattr(args, "combat_config", "")
    if not _combat_cfg_rs and getattr(args, "char_class", False):
        _class_map_rs = {
            "knight":   "combat_config.json",
            "druid":    "combat_config_druid.json",
            "paladin":  "combat_config_paladin.json",
            "sorcerer": "combat_config_sorcerer.json",
        }
        _combat_cfg_rs = _class_map_rs.get(args.char_class, "")
        if _combat_cfg_rs:
            print(f"[run-script] Using combat config for {args.char_class}: {_combat_cfg_rs}")

    # Resolve position_source: "auto" → "minimap" when a frame source is available
    _pos_src = getattr(args, "position_source", "auto")
    if _pos_src == "auto":
        _pos_src = "minimap" if getattr(args, "frame_source", "") else "none"

    # Resolve start_pos: try --start-pos first, then _meta.start_coord from JSON route
    _start_pos = getattr(args, "start_pos", "")
    if not _start_pos:
        import json as _json
        try:
            with open(args.script, encoding="utf-8") as _sf:
                _route = _json.load(_sf)
            _sc = _route.get("_meta", {}).get("start_coord", {})
            if "x" in _sc and "y" in _sc and "z" in _sc:
                _start_pos = f"{_sc['x']},{_sc['y']},{_sc['z']}"
                print(f"[run-script] Auto start_pos from _meta.start_coord: {_start_pos}")
        except Exception:
            pass

    cfg = SessionConfig(
        route_file   = "",
        input_method = args.input_method,
        target_window= args.window,
        start_delay  = args.start_delay,
        auto_loot    = args.loot,
        depot_after_run = args.depot,
        auto_combat  = args.combat,
        monitor_conditions = args.conditions,
        dry_run      = args.dry_run,
        frame_source = getattr(args, "frame_source", ""),
        frame_window = getattr(args, "frame_window", ""),
        monitor_idx  = getattr(args, "monitor_idx", 2),
        combat_config_file    = _combat_cfg_rs,
        condition_config_file = getattr(args, "condition_config", ""),
        pico_enabled = getattr(args, "pico", False),
        pico_port    = getattr(args, "pico_port", "auto"),
        position_source = _pos_src,
        start_pos       = _start_pos,
        shovel_hotkey_vk = _vk(getattr(args, "shovel_vk", "0")),
        rope_hotkey_vk   = _vk(getattr(args, "rope_vk", "0")),
        heal_hp_pct         = getattr(args, "heal", 70),
        heal_emergency_pct  = getattr(args, "emergency_pct", 30),
        mana_threshold_pct  = getattr(args, "mana_pct", 30),
        heal_hotkey_vk      = _vk(getattr(args, "heal_vk", "0x70")),
        emergency_hotkey_vk = _vk(getattr(args, "emergency_vk", "0x72")),
        mana_hotkey_vk      = _vk(getattr(args, "mana_vk", "0x71")),
        max_deaths          = getattr(args, "max_deaths", 1),
    )

    logs: list[str] = []

    def _log(msg: str) -> None:
        print(msg)
        logs.append(msg)

    import threading as _threading

    session = BotSession(cfg, log_callback=_log)
    if args.dry_run:
        print("[run-script] modo DRY-RUN — ningún input real")
    print(f"[run-script] Script: {args.script}")

    if getattr(args, "monitor", False):
        # run_script is synchronous → execute in a worker thread,
        # open the monitor on the main (Tkinter-only) thread.
        script_exc: list[Exception] = []

        def _run_script_thread() -> None:
            session.start()
            try:
                session.run_script(args.script)
            except Exception as exc:  # noqa: BLE001
                script_exc.append(exc)
            finally:
                session.stop()

        t = _threading.Thread(target=_run_script_thread, daemon=True)
        t.start()
        with _GracefulShutdown(session):
            try:
                session.open_monitor()
            finally:
                session.stop()
                t.join(timeout=5)
                if script_exc:
                    print(f"ERROR en script: {script_exc[0]}")
                snap = session.stats_snapshot()
                print(f"\n[run-script] Terminado — heals={snap.get('heal_fired',0)} "
                      f"mana={snap.get('mana_fired',0)}")
    else:
        with _GracefulShutdown(session):
            session.start()
            try:
                session.run_script(args.script)
            except FileNotFoundError as exc:
                print(f"ERROR: {exc}")
            except KeyboardInterrupt:
                print("\n[run-script] Ctrl+C")
            finally:
                session.stop()
                snap = session.stats_snapshot()
                print(f"\n[run-script] Terminado — heals={snap.get('heal_fired',0)} "
                      f"mana={snap.get('mana_fired',0)}")


def cmd_generate_route(args: argparse.Namespace) -> None:
    """Generate a walkable-step route JSON from two coordinates or waypoint names."""
    import json as _json

    nav = WaypointNavigator()
    floor = args.floor if args.floor >= 0 else 7

    # --- resolve start ---
    if args.start_name:
        results = nav.find_waypoints(args.start_name, floor=floor if args.floor >= 0 else None)
        if not results:
            print(f"ERROR: no waypoint matching '{args.start_name}'.")
            return
        start = results[0].coord
        print(f"Start  : {results[0].name} @ {start}")
    else:
        start = Coordinate(args.sx, args.sy, floor)
        print(f"Start  : {start}")

    # --- resolve end ---
    if args.end_name:
        results = nav.find_waypoints(args.end_name, floor=start.z)
        if not results:
            print(f"ERROR: no waypoint matching '{args.end_name}'.")
            return
        end = results[0].coord
        print(f"End    : {results[0].name} @ {end}")
    else:
        end = Coordinate(args.ex, args.ey, floor)
        print(f"End    : {end}")

    # --- find path ---
    if start.z != end.z:
        segments = nav.navigate_multifloor(start, end)
        all_steps: list[Coordinate] = []
        for seg in segments:
            all_steps.extend(seg.steps)
        total_dist = sum(s.total_distance for s in segments)
        found = any(s.found for s in segments)
    else:
        route = nav.navigate(start, end)
        all_steps = route.steps
        total_dist = route.total_distance
        found = route.found

    if not found or not all_steps:
        print("ERROR: no path found between the two points.")
        return

    print(f"Found  : {len(all_steps)} steps  (~{total_dist:.0f} tiles)")

    # --- build JSON ---
    waypoints = [
        {"name": f"step_{i:04d}", "x": c.x, "y": c.y, "z": c.z}
        for i, c in enumerate(all_steps)
    ]
    payload = {
        "meta": {
            "start": {"x": start.x, "y": start.y, "z": start.z},
            "end":   {"x": end.x,   "y": end.y,   "z": end.z},
            "steps": len(all_steps),
            "distance": round(total_dist, 1),
        },
        "waypoints": waypoints,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved  : {out_path}")


def cmd_status(args: argparse.Namespace) -> None:
    """Print the stats snapshot written by the last (or current) session."""
    import json as _json
    from pathlib import Path as _Path

    stats_file = _Path(__file__).parent / "output" / "session_stats.json"
    if not stats_file.exists():
        print("No stats file found. Run a session first.")
        return

    with open(stats_file, encoding="utf-8") as _f:
        snap = _json.load(_f)

    print("\n=== BotSession stats snapshot ===")
    order = (
        "start_time_iso", "is_running", "uptime_secs",
        "routes_completed", "heal_fired", "mana_fired", "loot_events",
    )
    for key in order:
        if key in snap:
            print(f"  {key:<22} {snap[key]}")
    # Print any remaining keys not in the fixed order
    for key, val in snap.items():
        if key not in order:
            print(f"  {key:<22} {val}")
    print()


def cmd_diagnose(args: argparse.Namespace) -> None:
    """Run the full diagnostic pipeline: capture → detect → report."""
    from tools.diagnose_pipeline import run_diagnose

    run_diagnose(
        source=args.source,
        window_title=args.window,
        output_dir="output",
        save_json=True,
        save_image=True,
        show=args.show,
    )


def cmd_calibrate(args: argparse.Namespace) -> None:
    from src.calibrator import calibrate
    window = getattr(args, "window", "")
    mode = getattr(args, "mode", "all")
    calibrate(source=args.source, obs_source_name=args.obs_source,
             mode=mode, window_title=window)


def cmd_track(args: argparse.Namespace) -> None:
    import time
    from src.minimap_radar import MinimapRadar, MinimapConfig

    cfg = DetectorConfig.load()
    if args.obs_password:
        cfg.obs_ws_password = args.obs_password
    if args.obs_source:
        cfg.obs_source = args.obs_source
    interval = args.interval if args.interval else 0.5

    nav = WaypointNavigator()
    dest = None
    if args.dest_name:
        wps = nav.find_waypoints(args.dest_name)
        if not wps:
            print(f"ERROR: No se encontró waypoint '{args.dest_name}'")
            return
        dest = wps[0].coord
        print(f"Destino: {wps[0]}")
    elif args.dest_x:
        dest = Coordinate(args.dest_x, args.dest_y, args.dest_z)
        print(f"Destino: {dest}")

    # Load destination floor for pathfinding
    if dest and not nav.is_floor_loaded(dest.z):
        nav.load_floor(dest.z)

    # ── Minimap radar tracking (preferred for wgc/mss) ──────────────
    window = getattr(args, "window", "")
    source = args.source

    # Build frame source
    if source == "wgc":
        from src.character_detector import WGCSource
        frame_src: Any = WGCSource(window or "Tibia")
    elif source == "mss":
        from src.character_detector import WGCSource
        frame_src = WGCSource(window or "Tibia")
    elif source == "virtual-cam":
        from src.character_detector import VirtualCameraSource
        frame_src = VirtualCameraSource(cfg.obs_cam_index)
    elif source == "screen":
        from src.character_detector import MSSScreenSource
        frame_src = MSSScreenSource()
    else:
        from src.character_detector import MSSScreenSource
        frame_src = MSSScreenSource()

    frame_src.connect()

    # Warm-up for virtual-cam
    if source == "virtual-cam":
        for _ in range(5):
            frame_src.get_frame()

    minimap_cfg = MinimapConfig.load()
    if getattr(args, "floor", None) is not None:
        minimap_cfg.floor = args.floor
    radar = MinimapRadar(loader=nav.loader, config=minimap_cfg)
    last_coord: Optional[Coordinate] = None
    print(f"Tracking minimap ({source}) floor={minimap_cfg.floor} iniciado — Ctrl+C para parar …\n",
          flush=True)

    try:
        while True:
            frame = frame_src.get_frame()
            if frame is None:
                time.sleep(interval)
                continue
            coord = radar.read(frame)
            if coord is not None and coord != last_coord:
                last_coord = coord
                if not nav.is_floor_loaded(coord.z):
                    nav.load_floor(coord.z)
                if dest and dest.z == coord.z:
                    route = nav.navigate(coord, dest)
                    steps = len(route.steps) if route.found else -1
                    dist = f"{route.total_distance:.0f}t" if route.found else "N/A"
                    print(f"  {coord}  →  destino: {steps} pasos / {dist} tiles",
                          flush=True)
                else:
                    near = nav.nearest_waypoint(coord, top_n=1)
                    nstr = f" | cerca: {near[0].name}" if near else ""
                    print(f"  {coord}{nstr}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        frame_src.disconnect()
        print("  [Track] Detenido.")


# ---------------------------------------------------------------------------

def cmd_monitor(args: argparse.Namespace) -> None:
    """Open the Tkinter MonitorGui (default entry point when no subcommand given)."""
    from src.session import BotSession, SessionConfig
    from src.monitor_gui import MonitorConfig

    cfg = SessionConfig(
        route_file = getattr(args, "route", "") or "",
        loop_route = getattr(args, "loop", False),
        dry_run    = getattr(args, "dry_run", False),
        start_delay = 0.0,
    )

    def _log(msg: str) -> None:
        print(msg)

    session = BotSession(cfg, log_callback=_log)
    gui_cfg = MonitorConfig(refresh_ms=getattr(args, "refresh_ms", 1000))
    try:
        session.open_monitor(config=gui_cfg)
    finally:
        session.stop()


# ---------------------------------------------------------------------------
# F7.4 — Multi-window support
# ---------------------------------------------------------------------------

def cmd_multi_run(args: argparse.Namespace) -> None:
    """Run multiple BotSessions targeting different windows from a JSON config."""
    import time as _time
    from src.multi_session import MultiSessionManager
    from src.session import SessionConfig

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"[multi-run] Config file not found: {cfg_path}")
        return

    import json
    with open(cfg_path, encoding="utf-8") as f:
        multi_cfg = json.load(f)

    sessions_data = multi_cfg.get("sessions", [])
    if not sessions_data:
        print("[multi-run] No sessions defined in config.")
        return

    mgr = MultiSessionManager(log_callback=print)
    for entry in sessions_data:
        name = entry.pop("name", f"session_{len(mgr.session_names) + 1}")
        cfg = SessionConfig(**entry)
        mgr.add(name, cfg)
        print(f"[multi-run] Added session '{name}' → window='{cfg.target_window}'")

    print(f"\n[multi-run] Starting {mgr.count} sessions …")
    mgr.start_all()

    try:
        while mgr.running_count > 0:
            _time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[multi-run] Ctrl+C — stopping all sessions …")
    finally:
        mgr.stop_all()
        snap = mgr.stats_snapshot()
        print(f"\n[multi-run] Done. {snap['total_sessions']} sessions completed.")
        for sname, sdata in snap.get("sessions", {}).items():
            st = sdata.get("stats", {})
            print(f"  [{sname}] routes={st.get('routes_completed', 0)} "
                  f"heals={st.get('heal_fired', 0)}")


# ---------------------------------------------------------------------------
# F7.6 — Standalone bank + NPC supply buying
# ---------------------------------------------------------------------------

def cmd_resupply(args: argparse.Namespace) -> None:
    """Execute a standalone bank and/or NPC buy cycle."""
    from src.input_controller import InputController
    from src.depot_manager import DepotManager, DepotConfig
    from src.trade_manager import TradeManager, TradeConfig

    ctrl = InputController(
        input_method=getattr(args, "input_method", "postmessage"),
    )

    action = args.action
    depot = DepotManager(ctrl=ctrl)
    depot.set_log_callback(print)

    if action in ("full", "deposit"):
        print("[resupply] Step: bank deposit (gold)")
        ok = depot.bank_deposit_gold()
        print(f"[resupply] Bank deposit: {'OK' if ok else 'SKIP (no bank_npc_coord)'}")

    if action in ("full", "withdraw"):
        print(f"[resupply] Step: bank withdraw (amount={args.amount})")
        ok = depot.bank_withdraw(args.amount)
        print(f"[resupply] Bank withdraw: {'OK' if ok else 'FAILED'}")

    if action in ("full", "buy"):
        trade_cfg = TradeConfig()
        if args.trade_config:
            try:
                trade_cfg = TradeConfig.load(Path(args.trade_config))
                print(f"[resupply] TradeConfig loaded from {args.trade_config}")
            except Exception as _e:
                print(f"[resupply] TradeConfig load failed ({_e}) — using defaults")
        trade = TradeManager(ctrl=ctrl, config=trade_cfg, log_fn=print)
        print("[resupply] Step: NPC buy supplies")
        ok = trade.run_cycle()
        print(f"[resupply] NPC trade: {'OK' if ok else 'FAILED'}")
        if ok:
            print(f"  Bought: {trade.last_bought}")
            print(f"  Sold:   {trade.last_sold}")

    print("[resupply] Done.")


# ---------------------------------------------------------------------------
# F7.7 — Standalone web dashboard
# ---------------------------------------------------------------------------

def cmd_dashboard(args: argparse.Namespace) -> None:
    """Start the web dashboard server (standalone demo mode)."""
    import time as _time
    from src.dashboard_server import DashboardServer

    import os as _os
    port = getattr(args, "port", 8080)
    ws_port = getattr(args, "ws_port", 8765)
    token = _os.environ.get("DASHBOARD_TOKEN", "")

    server = DashboardServer(port=port, ws_port=ws_port, auth_token=token)

    # In standalone mode, use demo stats
    _start = _time.time()

    def _demo_stats() -> dict:
        return {
            "routes_completed": 0,
            "heal_fired": 0,
            "mana_fired": 0,
            "loot_events": 0,
            "uptime_seconds": _time.time() - _start,
            "position": {"x": 0, "y": 0, "z": 7},
        }

    server.set_stats_fn(_demo_stats)
    server.start()
    print(f"[dashboard] HTTP: http://localhost:{port}")
    print(f"[dashboard] WS:   ws://localhost:{ws_port}")
    print("[dashboard] Press Ctrl+C to stop.")

    try:
        while True:
            _time.sleep(1.0)
    except KeyboardInterrupt:
        server.stop()
        print("\n[dashboard] Stopped.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waypoint-navigator",
        description="Tibia WaypointNavigator CLI",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    # --- monitor (default) ---
    p_mon = sub.add_parser(
        "monitor",
        help="Open the MonitorGui (default when no subcommand is given).",
    )
    p_mon.add_argument("--route", default="",
                       help="JSON route file (optional).")
    p_mon.add_argument("--loop",      action="store_true", help="Loop route indefinitely.")
    p_mon.add_argument("--dry-run",   action="store_true", help="Simulate: no real input sent.")
    p_mon.add_argument("--refresh-ms", type=int, default=1000, help="GUI refresh interval ms.")

    # --- run ---
    p_run = sub.add_parser("run", help="Start a full BotSession (walk + healer + loot).")
    p_run.add_argument("--route", default="",
                       help="JSON route file. Can be an absolute path, relative to cwd, "
                            "or relative to the routes/ folder "
                            "(e.g. 'thais_depot_to_temple.json' or 'wasp_thais/wasp_thais_ek.json').")
    p_run.add_argument("--heal",         type=int,   default=70,    help="Heal HP%% (default 70).")
    p_run.add_argument("--emergency-pct",type=int,   default=30,    help="Emergency HP%% (default 30).")
    p_run.add_argument("--mana-pct",     type=int,   default=30,    help="Mana restore MP%% (default 30).")
    p_run.add_argument("--heal-vk",      default="0x70", help="Heal hotkey VK  (default 0x70 = F1).")
    p_run.add_argument("--emergency-vk", default="0x72", help="Emergency hotkey VK  (default 0x72 = F3).")
    p_run.add_argument("--mana-vk",      default="0x71", help="Mana hotkey VK  (default 0x71 = F2).")
    p_run.add_argument("--loot",    action="store_true", help="Enable auto-loot.")
    p_run.add_argument("--depot",   action="store_true", help="Run depot cycle after route ends.")
    p_run.add_argument("--loop",    action="store_true", help="Loop route indefinitely.")
    p_run.add_argument("--combat",  action="store_true", help="Enable auto-combat (CombatManager).")
    p_run.add_argument("--conditions", action="store_true", help="Enable condition monitor.")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Simulation: log actions but send no real input.")
    p_run.add_argument("--monitor", action="store_true",
                       help="Open the Tkinter monitor GUI while the session runs.")
    p_run.add_argument("--combat-config",    default="",
                       help="Path to CombatConfig JSON (overrides defaults).")
    p_run.add_argument("--class", dest="char_class", default="",
                       choices=["", "knight", "druid", "paladin", "sorcerer"],
                       help="Character class — auto-selects combat_config_<class>.json. "
                            "Overridden by --combat-config if both given.")
    p_run.add_argument("--condition-config", default="",
                       help="Path to ConditionConfig JSON (overrides defaults).")
    p_run.add_argument("--window",       default="Tibia",        help="Tibia window title fragment.")
    p_run.add_argument("--input-method", default="interception",
                       choices=["postmessage", "scancode", "interception", "hybrid"],
                       help="Input method  (default: interception).")
    p_run.add_argument("--start-delay",  type=float, default=3.0,
                       help="Seconds to wait before first move  (default 3).")
    p_run.add_argument("--start-at", default="", metavar="HH:MM",
                       help="Wait until HH:MM local time before starting the session "
                            "(e.g. '03:30'). Wraps to next day if time has passed.")
    p_run.add_argument("--jitter",        type=float, default=0.0,
                       help="Input jitter fraction of base delay (0 = off).")
    p_run.add_argument("--watchdog-timeout", type=float, default=0.0,
                       help="Seconds without movement before watchdog alert (0 = disabled).")
    p_run.add_argument("--step-delay-min", type=float, default=0.0,
                       help="Per-step random pause minimum seconds (0 = off).")
    p_run.add_argument("--step-delay-max", type=float, default=0.0,
                       help="Per-step random pause maximum seconds (0 = off).")
    p_run.add_argument("--frame-source", default="",
                       choices=["", "mss", "dxcam", "printwindow", "wgc", "obs"],
                       help="Frame capture backend (default: auto). Options: mss, dxcam, printwindow, wgc, obs.")
    p_run.add_argument("--frame-window", default="",
                       help="Window title for frame capture (overrides Tibia hwnd). "
                            "Useful for capturing OBS projector window while sending input to Tibia.")
    p_run.add_argument("--monitor-idx", type=int, default=1,
                       help="MSS monitor index (1=primary, 2=secondary). Use 2 for OBS on second screen.")
    p_run.add_argument("--position-source", default="none",
                       choices=["none", "mss", "minimap"],
                       help="Position tracking source (default: none).")
    p_run.add_argument("--start-pos", default="",
                       help="Initial character position x,y,z (e.g. 32349,32225,8). "
                            "Auto-loaded from _meta.start_coord in unified JSON routes.")
    p_run.add_argument("--resume", action="store_true",
                       help="Resume from the last saved checkpoint "
                            "(skips to the last-visited waypoint index).")
    # ── FriendHealer (Exura Sio ami) ─────────────────────────────────────────
    p_run.add_argument("--friend-sio-vk",      default="0", metavar="VK",
                       help="Hotkey VK para Exura Sio ami (0 = desactivado).")
    p_run.add_argument("--friend-gran-sio-vk", default="0", metavar="VK",
                       help="Hotkey VK para Exura Gran Sio ami (0 = desactivado).")
    p_run.add_argument("--friend-sio-pct",     type=int, default=70,
                       help="HP%% del amigo en que se lanza Sio (default 70).")
    p_run.add_argument("--friend-gran-sio-pct", type=int, default=40,
                       help="HP%% del amigo en que se lanza Gran Sio (default 40).")
    # ── Safety / Anti-ban ─────────────────────────────────────────────────────
    p_run.add_argument("--gm-detector", action="store_true",
                       help="Enable Game-Master detection (pause/logout on GM sighting).")
    p_run.add_argument("--gm-action", default="pause",
                       choices=["warn", "pause", "logout", "human_mimic"],
                       help="Action when GM detected (default: pause).")
    p_run.add_argument("--pvp-detector", action="store_true",
                       help="Enable PvP skull detection.")
    p_run.add_argument("--no-break", action="store_true",
                       help="Disable the break scheduler (run continuously).")
    p_run.add_argument("--anti-kick-idle", type=float, default=300.0,
                       help="Seconds idle before anti-kick activates (default: 300).")
    # ── Death / Reconnect ─────────────────────────────────────────────────────
    p_run.add_argument("--re-equip", default="", metavar="VKs",
                       help='Post-respawn re-equip hotkeys, comma-sep (e.g. "0x75,0x76").')
    p_run.add_argument("--max-deaths", type=int, default=0,
                       help="Auto-stop after N deaths (0 = unlimited).")
    # ── Dashboard ─────────────────────────────────────────────────────────────
    p_run.add_argument("--dashboard", action="store_true",
                       help="Start the web dashboard for remote monitoring.")
    p_run.add_argument("--dashboard-port", type=int, default=8080,
                       help="Dashboard HTTP port (default: 8080).")
    # ── Loot ──────────────────────────────────────────────────────────────────
    p_run.add_argument("--loot-mode", default="all",
                       choices=["all", "whitelist", "quick"],
                       help="Loot mode: all (shift-click everything), whitelist, quick (default: all).")
    # ── Pico 2 HID ───────────────────────────────────────────────────────────
    p_run.add_argument("--pico", action="store_true",
                       help="Enable Raspberry Pi Pico 2 as HID failover device.")
    p_run.add_argument("--pico-port", default="auto",
                       help="Pico 2 COM port (default: auto-detect).")
    # ── Shovel / Rope VKs ────────────────────────────────────────────────
    p_run.add_argument("--shovel-vk", default="0", metavar="VK",
                       help="Shovel hotbar VK (e.g. 0x73=F4). Required for cave shoveling.")
    p_run.add_argument("--rope-vk", default="0", metavar="VK",
                       help="Rope hotbar VK (e.g. 0x74=F5). Required for rope-up transitions.")

    # --- run-script ---
    p_rs = sub.add_parser(
        "run-script",
        help="Execute a .in script file via BotSession.",
    )
    p_rs.add_argument("script", help="Path to the .in script file.")
    p_rs.add_argument("--window",        default="Tibia",        help="Tibia window title fragment.")
    p_rs.add_argument("--input-method",  default="interception",
                      choices=["postmessage", "scancode", "interception", "hybrid"])
    p_rs.add_argument("--start-delay",   type=float, default=0.0,
                      help="Seconds to wait before executing script (default 0).")
    p_rs.add_argument("--loot",          action="store_true", help="Enable auto-loot.")
    p_rs.add_argument("--depot",         action="store_true", help="Enable depot cycle.")
    p_rs.add_argument("--combat",        action="store_true", help="Enable auto-combat.")
    p_rs.add_argument("--conditions",    action="store_true", help="Enable condition monitor.")
    p_rs.add_argument("--dry-run",       action="store_true",
                      help="Simulation: log actions but send no real input.")
    p_rs.add_argument("--monitor",        action="store_true",
                      help="Open the Tkinter monitor GUI while the script runs.")
    p_rs.add_argument("--frame-source", default="",
                      choices=["", "mss", "dxcam", "printwindow", "wgc", "obs"],
                      help="Frame capture backend (default: auto).")
    p_rs.add_argument("--frame-window", default="",
                      help="Window title for frame capture (overrides Tibia hwnd).")
    p_rs.add_argument("--monitor-idx", type=int, default=1,
                      help="MSS monitor index (1=primary, 2=secondary). Use 2 for OBS on second screen.")
    p_rs.add_argument("--combat-config",    default="",
                      help="Path to CombatConfig JSON (overrides defaults).")
    p_rs.add_argument("--class", dest="char_class", default="",
                      choices=["", "knight", "druid", "paladin", "sorcerer"],
                      help="Character class — auto-selects combat_config_<class>.json.")
    p_rs.add_argument("--condition-config", default="",
                      help="Path to ConditionConfig JSON (overrides defaults).")
    p_rs.add_argument("--pico", action="store_true",
                      help="Enable Raspberry Pi Pico 2 as HID failover device.")
    p_rs.add_argument("--pico-port", default="auto",
                      help="Pico 2 COM port (default: auto-detect).")
    p_rs.add_argument("--position-source", default="auto",
                      choices=["auto", "none", "mss", "minimap"],
                      help="Position tracking source (default: auto — minimap when frame-source is set).")
    p_rs.add_argument("--start-pos", default="",
                      help="Initial character position x,y,z (e.g. 32369,32241,7). "
                           "Auto-loaded from _meta.start_coord in JSON routes.")
    p_rs.add_argument("--shovel-vk", default="0", metavar="VK",
                      help="Shovel hotbar VK (e.g. 0x7A=F11). Required for cave shoveling.")
    p_rs.add_argument("--rope-vk", default="0", metavar="VK",
                      help="Rope hotbar VK (e.g. 0x7B=F12). Required for rope-up transitions.")
    p_rs.add_argument("--heal",         type=int,   default=70,    help="Heal HP%% (default 70).")
    p_rs.add_argument("--emergency-pct",type=int,   default=30,    help="Emergency HP%% (default 30).")
    p_rs.add_argument("--mana-pct",     type=int,   default=30,    help="Mana restore MP%% (default 30).")
    p_rs.add_argument("--heal-vk",      default="0x70", help="Heal hotkey VK  (default 0x70 = F1).")
    p_rs.add_argument("--emergency-vk", default="0x72", help="Emergency hotkey VK  (default 0x72 = F3).")
    p_rs.add_argument("--mana-vk",      default="0x71", help="Mana hotkey VK  (default 0x71 = F2).")
    p_rs.add_argument("--max-deaths",   type=int,   default=1,
                      help="Stop session after N deaths (default 1, 0=unlimited).")

    # --- generate-route ---
    p_gen = sub.add_parser(
        "generate-route",
        help="Generate a JSON route file from two points (by name or coordinates).",
    )
    p_gen.add_argument("--start-name", default="", help="Start waypoint name (substring).")
    p_gen.add_argument("--sx", type=int, default=0, help="Start X coordinate.")
    p_gen.add_argument("--sy", type=int, default=0, help="Start Y coordinate.")
    p_gen.add_argument("--end-name",   default="", help="End waypoint name (substring).")
    p_gen.add_argument("--ex", type=int, default=0, help="End X coordinate.")
    p_gen.add_argument("--ey", type=int, default=0, help="End Y coordinate.")
    p_gen.add_argument("--floor", type=int, default=-1, help="Floor (-1 = auto from waypoint).")
    p_gen.add_argument(
        "--output", default="routes/generated.json",
        help="Output JSON file path  (default: routes/generated.json).",
    )

    # --- navigate ---
    p_nav = sub.add_parser("navigate", help="Find path between two coordinates.")
    p_nav.add_argument("--sx", type=int, required=True, help="Start X")
    p_nav.add_argument("--sy", type=int, required=True, help="Start Y")
    p_nav.add_argument("--ex", type=int, required=True, help="End X")
    p_nav.add_argument("--ey", type=int, required=True, help="End Y")
    p_nav.add_argument("--floor", type=int, default=7, help="Floor (default: 7 = ground)")
    p_nav.add_argument("--save", default="", help="Save route image to file instead of showing.")
    p_nav.add_argument("--no-viz", action="store_true", help="Skip visualization.")

    # --- navigate-name ---
    p_name = sub.add_parser("navigate-name", help="Navigate by waypoint names.")
    p_name.add_argument("start", help="Start waypoint name (substring match).")
    p_name.add_argument("end", help="End waypoint name (substring match).")
    p_name.add_argument("--floor", type=int, default=-1, help="Filter by floor (-1 = any).")
    p_name.add_argument("--save", default="", help="Save image to file.")
    p_name.add_argument("--no-viz", action="store_true", help="Skip visualization.")

    # --- search-waypoints ---
    p_search = sub.add_parser("search-waypoints", help="List waypoints matching a query.")
    p_search.add_argument("query", help="Search term.")
    p_search.add_argument("--floor", type=int, default=-1, help="Filter by floor (-1 = all).")

    # --- floor-stats ---
    p_stats = sub.add_parser("floor-stats", help="Show walkability stats for a floor.")
    p_stats.add_argument("floor", type=int, help="Floor number (0-15).")

    # --- show-floor ---
    p_show = sub.add_parser("show-floor", help="Display the full floor map.")
    p_show.add_argument("floor", type=int, help="Floor number (0-15).")
    p_show.add_argument("--save", default="", help="Save image to this path.")

    # --- calibrate ---
    p_cal = sub.add_parser("calibrate", help="Calibrar ROI del CharacterDetector.")
    p_cal.add_argument("--source", default="mss",
                       choices=["virtual-cam", "obs-ws", "screen", "mss", "wgc"],
                       help="Fuente de captura (default: mss)")
    p_cal.add_argument("--obs-source", default="", help="Nombre de la fuente OBS")
    p_cal.add_argument("--window", default="",
                       help="T\u00edtulo (o fragmento) de la ventana a capturar")
    p_cal.add_argument("--mode", default="all",
                       choices=["coord", "hp", "mp", "minimap", "battle-list", "all"],
                       help="ROI a calibrar (default: all)")

    # --- diagnose ---
    p_diag = sub.add_parser(
        "diagnose",
        help="Run full diagnostic: capture 1 frame, test all detectors, save report.",
    )
    p_diag.add_argument("--source", default="screen",
                        choices=["virtual-cam", "obs-ws", "screen", "mss", "wgc"],
                        help="Capture source (default: screen)")
    p_diag.add_argument("--window", default="Proyector",
                        help="Window title fragment for WGC/MSS capture (default: Proyector)")
    p_diag.add_argument("--show", action="store_true",
                        help="Show the annotated overlay in a window after diagnosis")

    # --- track ---
    p_track = sub.add_parser("track", help="Live tracking de posición del personaje.")
    p_track.add_argument("--source", default="virtual-cam",
                         choices=["virtual-cam", "obs-ws", "screen", "mss", "wgc"])
    p_track.add_argument("--window", default="",
                         help="Título (o fragmento) de la ventana a capturar")
    p_track.add_argument("--obs-source", default="")
    p_track.add_argument("--obs-password", default="")
    p_track.add_argument("--dest-x", type=int, default=0)
    p_track.add_argument("--dest-y", type=int, default=0)
    p_track.add_argument("--dest-z", type=int, default=7)
    p_track.add_argument("--dest-name", default="")
    p_track.add_argument("--interval", type=float, default=0.0)
    p_track.add_argument("--floor", type=int, default=None,
                         help="Floor override (7=ground, 8=underground). Default: read from minimap_config.json")
    p_track.add_argument("--debug", action="store_true")

    # ── status ──────────────────────────────────────────────────────────────
    sub.add_parser(
        "status",
        help="Show stats from the last session (reads output/session_stats.json).",
    )
    # ── multi-run (F7.4) ───────────────────────────────────────────────────────
    p_multi = sub.add_parser(
        "multi-run",
        help="Run multiple BotSessions from a JSON config (one per window).",
    )
    p_multi.add_argument(
        "config",
        help="Path to multi-session JSON config file.",
    )

    # ── resupply (F7.6) ───────────────────────────────────────────────────────
    p_resupply = sub.add_parser(
        "resupply",
        help="Run a standalone bank + NPC buy supplies cycle.",
    )
    p_resupply.add_argument("--window", default="Tibia",
                            help="Tibia window title fragment.")
    p_resupply.add_argument("--action", default="full",
                            choices=["full", "deposit", "withdraw", "buy"],
                            help="'full' = bank deposit + withdraw + buy, or a single step.")
    p_resupply.add_argument("--amount", type=int, default=0,
                            help="Gold to withdraw (0 = all). Only for 'withdraw'/'full'.")
    p_resupply.add_argument("--trade-config", default="",
                            help="Path to trade_config.json for NPC buying.")
    p_resupply.add_argument("--input-method", default="interception",
                            choices=["postmessage", "scancode", "interception", "hybrid"])

    # ── dashboard (F7.7) ──────────────────────────────────────────────────────
    p_dash = sub.add_parser(
        "dashboard",
        help="Start the web dashboard server (standalone, for testing).",
    )
    p_dash.add_argument("--port", type=int, default=8080, help="HTTP port.")
    p_dash.add_argument("--ws-port", type=int, default=8765, help="WebSocket port.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # No subcommand → open the Monitor GUI
    if args.command is None:
        cmd_monitor(args)
        return

    dispatch = {
        "monitor":          cmd_monitor,
        "run":              cmd_run,
        "run-script":       cmd_run_script,
        "generate-route":   cmd_generate_route,
        "navigate":         cmd_navigate,
        "navigate-name":    cmd_navigate_name,
        "search-waypoints": cmd_search,
        "floor-stats":      cmd_floor_stats,
        "show-floor":       cmd_show_floor,
        "calibrate":        cmd_calibrate,
        "diagnose":         cmd_diagnose,
        "track":            cmd_track,
        "status":           cmd_status,
        "multi-run":        cmd_multi_run,
        "resupply":         cmd_resupply,
        "dashboard":        cmd_dashboard,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    # Configure logging with rotation
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    from src.alert_system import LogRotator
    LogRotator().setup()

    main()
