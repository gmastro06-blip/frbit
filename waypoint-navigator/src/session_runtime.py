from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger("wn")


def _run_script_route_loop(
    *,
    route_path: Path,
    config: Any,
    json_peek: Any,
    is_running: Callable[[], bool],
    set_running: Callable[[bool], None],
    inc_routes_fn: Callable[[], None],
    break_scheduler: Any,
    pause_subsystems: Callable[[], None],
    resume_subsystems: Callable[[], None],
    log_fn: Callable[[str], None],
    run_script_fn: Callable[[Path], None],
    spawn_manager: Any = None,
) -> None:
    loop_route = config.loop_route
    if route_path.suffix.lower() == ".json":
        json_session = json_peek.get("session", {}) if isinstance(json_peek, dict) else {}
        loop_route = config.loop_route or (
            bool(json_session.get("loop_route", False)) and not config.dry_run
        )
        log_fn("[S] Unified script JSON detected — delegating to run_script.")
    else:
        log_fn("[S] Cavebot .in script detected — delegating to run_script.")

    # Register the initial script as the current spawn so SpawnManager
    # knows what to mark as occupied when PvP is detected.
    if spawn_manager is not None and spawn_manager.current_spawn is None:
        for sp in spawn_manager.config.spawns:
            if Path(sp.script) == route_path or sp.script == str(route_path):
                spawn_manager.current_spawn = sp.name
                log_fn(f"[S] SpawnManager: spawn inicial registrado como '{sp.name}'")
                break

    while is_running():
        try:
            run_script_fn(route_path)
        except Exception as exc:
            _log.error("run_script raised in script route loop", exc_info=exc)
            log_fn(f"[!] run_script raised: {exc!r} — stopping loop")
            set_running(False)
            return
        inc_routes_fn()

        # Check if SpawnManager switched to a different spawn while the script ran
        if spawn_manager is not None:
            current_spawn = spawn_manager.current_spawn
            if current_spawn:
                new_script = spawn_manager.get_spawn_script(current_spawn)
                if new_script and Path(new_script) != route_path:
                    log_fn(
                        f"[S] Spawn cambiado a '{current_spawn}' — "
                        f"cargando script: {new_script}"
                    )
                    route_path = Path(new_script)

        if not loop_route:
            break
        if break_scheduler is not None and break_scheduler.should_break():
            break_scheduler.execute_break(
                pause_fn=pause_subsystems,
                resume_fn=resume_subsystems,
            )

    set_running(False)


def _run_waypoint_route_loop(
    *,
    waypoints: list[Any],
    config: Any,
    is_running: Callable[[], bool],
    set_running: Callable[[bool], None],
    inc_routes_fn: Callable[[], None],
    event_bus: Any,
    break_scheduler: Any,
    depot: Any,
    depot_orchestrator: Any,
    ctrl: Any,
    frame_getter: Callable[[], Any] | None,
    get_position: Callable[[], Any],
    log_fn: Callable[[str], None],
    get_consecutive_errors: Callable[[], int],
    set_consecutive_errors: Callable[[int], None],
    navigate_to_fn: Callable[[Any, Any], Any],
    exec_route_fn: Callable[[Any], None],
    exec_multifloor_fn: Callable[[Any, Any, Any], None],
    save_checkpoint_fn: Callable[..., None],
    pause_subsystems: Callable[[], None],
    resume_subsystems: Callable[[], None],
    jittered_sleep_fn: Callable[..., Any],
    format_traceback_fn: Callable[[], str],
) -> None:
    if len(waypoints) < 2:
        log_fn("Route has fewer than 2 waypoints — nothing to do.")
        set_running(False)
        return

    log_fn(f"Loaded {len(waypoints)} waypoints from {config.route_file}")

    start_index = max(0, min(config.resume_waypoint_index, len(waypoints) - 2))
    if start_index > 0:
        log_fn(f"Resuming from waypoint index {start_index} (of {len(waypoints)})")
    first_loop = True
    routes_this_session = 0

    while is_running():
        try:
            loop_start = start_index if first_loop else 0
            first_loop = False
            for index in range(loop_start, len(waypoints) - 1):
                if not is_running():
                    break
                waypoint_from = waypoints[index]
                waypoint_to = waypoints[index + 1]
                routes = navigate_to_fn(waypoint_from.coord, waypoint_to.coord)
                if any(route.found for route in routes):
                    if len(routes) > 1:
                        exec_multifloor_fn(routes, waypoint_from.coord, waypoint_to.coord)
                    else:
                        exec_route_fn(routes[0])
                else:
                    log_fn(
                        f"[!] A* no encontró ruta "
                        f"({waypoint_from.coord} → {waypoint_to.coord}) — waypoint omitido"
                    )
                save_checkpoint_fn(waypoint_index=index + 1)

                if depot_orchestrator is not None:
                    frame = frame_getter() if frame_getter is not None else None
                    if depot_orchestrator.should_resupply(frame):
                        log_fn("Mid-route resupply triggered.")
                        depot_orchestrator.run_resupply(
                            player_pos=get_position(),
                            return_pos=waypoint_to.coord if hasattr(waypoint_to, "coord") else None,
                        )
                        event_bus.emit("resupply_done", depot_orchestrator.stats_snapshot())

            inc_routes_fn()
            routes_this_session += 1
            set_consecutive_errors(0)
            event_bus.emit("route_done", {"routes_completed": routes_this_session})

            if config.depot_after_run and depot is not None:
                log_fn("Running post-route depot cycle …")
                ok = depot.run_depot_cycle(player_pos=get_position())
                event_bus.emit("depot_done", {"success": ok, "cycles": depot.cycle_count})

            if break_scheduler is not None and break_scheduler.should_break():
                break_scheduler.execute_break(
                    pause_fn=pause_subsystems,
                    resume_fn=resume_subsystems,
                )
        except Exception as exc:
            consecutive_errors = get_consecutive_errors() + 1
            set_consecutive_errors(consecutive_errors)
            _log.error(
                "Error in waypoint route loop (consecutive #%d)", consecutive_errors,
                exc_info=exc,
            )
            log_fn(
                f"[!] Error in route loop: {exc!r} "
                f"(consecutive #{consecutive_errors})\n{format_traceback_fn()}"
            )
            save_checkpoint_fn()

            ctrl_failures = getattr(ctrl, "_consecutive_failures", 0)
            if (
                ctrl is not None
                and ctrl_failures >= 3
                and not getattr(ctrl, "_emergency_stopped", False)
            ):
                log_fn("[!] Input controller reporting repeated failures — emergency stop")
                if hasattr(ctrl, "emergency_stop"):
                    ctrl.emergency_stop()
                set_running(False)
                break

            if consecutive_errors >= 10:
                log_fn("[!] 10 consecutive errors — stopping to avoid infinite loop")
                set_running(False)
                break

            jittered_sleep_fn(2.0)

        if not config.loop_route:
            break

    set_running(False)


def run_session_loop(
    *,
    config: Any,
    inc_routes_fn: Callable[[], None],
    event_bus: Any,
    break_scheduler: Any,
    depot: Any,
    depot_orchestrator: Any,
    ctrl: Any,
    frame_getter: Callable[[], Any] | None,
    get_position: Callable[[], Any],
    is_running: Callable[[], bool],
    set_running: Callable[[bool], None],
    get_consecutive_errors: Callable[[], int],
    set_consecutive_errors: Callable[[int], None],
    log_fn: Callable[[str], None],
    resolve_route_fn: Callable[[str | Path], Path],
    run_script_fn: Callable[[Path], None],
    load_waypoints_fn: Callable[[str | Path], list[Any]],
    navigate_to_fn: Callable[[Any, Any], Any],
    exec_route_fn: Callable[[Any], None],
    exec_multifloor_fn: Callable[[Any, Any, Any], None],
    save_checkpoint_fn: Callable[..., None],
    pause_subsystems: Callable[[], None],
    resume_subsystems: Callable[[], None],
    jittered_sleep_fn: Callable[..., Any],
    format_traceback_fn: Callable[[], str],
    sleep_fn: Callable[[float], None],
    random_uniform_fn: Callable[[float, float], float],
    spawn_manager: Any = None,
) -> None:
    if not config.route_file:
        log_fn("No route_file configured — loop idle.")
        while is_running():
            sleep_fn(random_uniform_fn(0.35, 0.65))
        return

    try:
        route_path = resolve_route_fn(config.route_file)
    except FileNotFoundError as exc:
        log_fn(f"[!] {exc}")
        set_running(False)
        return

    json_peek: Any = None
    is_script = route_path.suffix.lower() in (".in", ".txt")
    if route_path.suffix.lower() == ".json":
        with open(route_path, encoding="utf-8") as file_obj:
            json_peek = json.load(file_obj)
        if isinstance(json_peek, dict) and "script" in json_peek:
            is_script = True

    if is_script:
        _run_script_route_loop(
            route_path=route_path,
            config=config,
            json_peek=json_peek,
            is_running=is_running,
            set_running=set_running,
            inc_routes_fn=inc_routes_fn,
            break_scheduler=break_scheduler,
            pause_subsystems=pause_subsystems,
            resume_subsystems=resume_subsystems,
            log_fn=log_fn,
            run_script_fn=run_script_fn,
            spawn_manager=spawn_manager,
        )
        return

    try:
        waypoints = load_waypoints_fn(config.route_file)
    except FileNotFoundError as exc:
        log_fn(f"[!] {exc}")
        set_running(False)
        return

    _run_waypoint_route_loop(
        waypoints=waypoints,
        config=config,
        is_running=is_running,
        set_running=set_running,
        inc_routes_fn=inc_routes_fn,
        event_bus=event_bus,
        break_scheduler=break_scheduler,
        depot=depot,
        depot_orchestrator=depot_orchestrator,
        ctrl=ctrl,
        frame_getter=frame_getter,
        get_position=get_position,
        log_fn=log_fn,
        get_consecutive_errors=get_consecutive_errors,
        set_consecutive_errors=set_consecutive_errors,
        navigate_to_fn=navigate_to_fn,
        exec_route_fn=exec_route_fn,
        exec_multifloor_fn=exec_multifloor_fn,
        save_checkpoint_fn=save_checkpoint_fn,
        pause_subsystems=pause_subsystems,
        resume_subsystems=resume_subsystems,
        jittered_sleep_fn=jittered_sleep_fn,
        format_traceback_fn=format_traceback_fn,
    )