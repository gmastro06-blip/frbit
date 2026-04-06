from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .models import Coordinate


@dataclass
class ScriptPreparation:
    route_path: Path
    route_identity: str
    instructions: list[Any]
    dry_run: bool
    step_interval: float
    hours_leave: list[float] = field(default_factory=list)
    blocked_regions: list[dict[str, Any]] = field(default_factory=list)
    walkable_overrides: list[dict[str, Any]] = field(default_factory=list)
    wasp_setup: dict[str, Any] | None = None


def _parse_json_script(
    *,
    route_path: Path,
    config: Any,
    ctrl: Any,
    healer: Any,
    get_position: Callable[[], Any],
    set_position: Callable[[Any], None],
    log_fn: Callable[[str], None],
    json_script_parser: Callable[[Any], list[Any]],
) -> ScriptPreparation:
    with open(route_path, encoding="utf-8") as file_obj:
        data = json.load(file_obj)

    raw_script = data.get("script", []) if isinstance(data, dict) else []
    if not raw_script:
        raise ValueError(f"JSON file has no 'script' array: {route_path}")

    instructions = json_script_parser(raw_script)
    json_session = data.get("session", {}) if isinstance(data, dict) else {}
    dry_run = config.dry_run or bool(json_session.get("dry_run", False))

    input_method = str(json_session.get("input_method", config.input_method))
    if input_method == "mouse":
        input_method = "postmessage"
    if ctrl is not None and input_method != ctrl.input_method:
        ctrl.input_method = input_method
        log_fn(f"[S] input_method → {input_method} (from JSON)")

    jitter_pct = float(json_session.get("jitter_pct", config.jitter_pct))
    if ctrl is not None:
        ctrl.jitter_pct = jitter_pct

    step_interval = float(json_session.get("step_interval", config.step_interval))

    if "rope_hotkey_vk" in json_session:
        config.rope_hotkey_vk = int(json_session["rope_hotkey_vk"])
    if "shovel_hotkey_vk" in json_session:
        config.shovel_hotkey_vk = int(json_session["shovel_hotkey_vk"])

    if healer is not None and hasattr(healer, "_cfg"):
        updates: dict[str, Any] = {}
        if "heal_hp_pct" in json_session:
            updates["hp_threshold_pct"] = int(json_session["heal_hp_pct"])
        if "heal_emergency_pct" in json_session:
            updates["hp_emergency_pct"] = int(json_session["heal_emergency_pct"])
        if "mana_threshold_pct" in json_session:
            updates["mp_threshold_pct"] = int(json_session["mana_threshold_pct"])
        if "heal_hotkey_vk" in json_session:
            updates["heal_hotkey_vk"] = int(json_session["heal_hotkey_vk"])
        if "mana_hotkey_vk" in json_session:
            updates["mana_hotkey_vk"] = int(json_session["mana_hotkey_vk"])
        if "emergency_hotkey_vk" in json_session:
            updates["emergency_hotkey_vk"] = int(json_session["emergency_hotkey_vk"])
        if updates:
            healer._cfg = dataclasses.replace(healer._cfg, **updates)

    script_options = data.get("script_options", {}) if isinstance(data, dict) else {}
    hours_leave = [float(hour) for hour in script_options.get("hours_leave", []) if hour is not None]
    blocked_regions = data.get("blocked_regions", []) if isinstance(data, dict) else []
    walkable_overrides = data.get("walkable_overrides", []) if isinstance(data, dict) else []
    wasp_setup = data.get("wasp_setup") if isinstance(data, dict) else None
    if not isinstance(wasp_setup, dict):
        setup_alias = data.get("setup") if isinstance(data, dict) else None
        wasp_setup = setup_alias if isinstance(setup_alias, dict) else None

    tracking_disabled = getattr(config, "position_source", "none") == "none"
    current_position = None if tracking_disabled else get_position()

    if current_position is None:
        meta = data.get("_meta", {}) if isinstance(data, dict) else {}
        start_coord = meta.get("start_coord")
        # Accept either {"x": ..., "y": ..., "z": ...} dict or [x, y, z] array
        if isinstance(start_coord, (list, tuple)) and len(start_coord) >= 3:
            start_coord = {"x": start_coord[0], "y": start_coord[1], "z": start_coord[2]}
        if isinstance(start_coord, dict):
            try:
                position = Coordinate(
                    x=int(start_coord["x"]),
                    y=int(start_coord["y"]),
                    z=int(start_coord["z"]),
                )
                set_position(position)
                log_fn(
                    f"[S] start_coord from JSON _meta: {position} "
                    "— place your character there before the script moves!"
                )
            except Exception as exc:
                log_fn(f"[!] _meta.start_coord parse error: {exc}")

    return ScriptPreparation(
        route_path=route_path,
        route_identity=str(route_path),
        instructions=instructions,
        dry_run=dry_run,
        step_interval=step_interval,
        hours_leave=hours_leave,
        blocked_regions=blocked_regions,
        walkable_overrides=walkable_overrides,
        wasp_setup=wasp_setup,
    )


def _parse_script_source(
    *,
    path: str | Path,
    config: Any,
    ctrl: Any,
    healer: Any,
    get_position: Callable[[], Any],
    set_position: Callable[[Any], None],
    log_fn: Callable[[str], None],
    resolve_route_fn: Callable[[str | Path], Path],
    parse_file_fn: Callable[[Path], list[Any]],
    json_script_parser: Callable[[Any], list[Any]],
) -> ScriptPreparation:
    route_path = resolve_route_fn(path)
    if route_path.suffix.lower() != ".json":
        return ScriptPreparation(
            route_path=route_path,
            route_identity=str(route_path),
            instructions=parse_file_fn(route_path),
            dry_run=config.dry_run,
            step_interval=config.step_interval,
        )
    return _parse_json_script(
        route_path=route_path,
        config=config,
        ctrl=ctrl,
        healer=healer,
        get_position=get_position,
        set_position=set_position,
        log_fn=log_fn,
        json_script_parser=json_script_parser,
    )


def _resolve_resume_index(
    *,
    route_identity: str,
    instruction_count: int,
    load_checkpoint_fn: Callable[[], Any],
    log_fn: Callable[[str], None],
) -> int:
    checkpoint = load_checkpoint_fn()
    if not checkpoint or not checkpoint.matches_route(route_identity):
        return 0
    extra = checkpoint.extra if isinstance(checkpoint.extra, dict) else {}
    if extra.get("route_mode") != "script":
        return 0
    try:
        resume_index = int(extra.get("script_resume_instruction_index", 0))
    except (TypeError, ValueError):
        resume_index = 0
    resume_index = max(0, min(resume_index, max(0, instruction_count - 1)))
    if resume_index > 0:
        log_fn(f"[S] Resuming script checkpoint from instruction [{resume_index}]")
    return resume_index


def _wire_executor_context(
    *,
    executor: Any,
    position: Any,
    obstacle_analyzer: Any,
    loader: Any,
    set_path_viz: Callable[[Any], None],
    log_fn: Callable[[str], None],
) -> None:
    if position is not None:
        executor.set_position(position)
    if obstacle_analyzer is not None:
        executor.set_obstacle_analyzer(obstacle_analyzer)
    if loader is not None:
        executor.set_map_loader(loader)
        try:
            from .path_visualizer import PathVisualizer

            run_id = time.strftime("run_%Y%m%d_%H%M%S")
            trace_dir = Path("output/path_trace") / run_id
            mask_dir = Path("output/masks") / run_id
            path_viz = PathVisualizer(
                loader,
                output_dir=trace_dir,
                mask_output_dir=mask_dir,
                floor=7,
            )
            executor.set_path_visualizer(path_viz)
            set_path_viz(path_viz)
            log_fn(
                "[S] PathVisualizer enabled — "
                f"traces in {trace_dir}/ and masks in {mask_dir}/"
            )
        except Exception as exc:
            log_fn(f"[S] PathVisualizer init failed: {exc}")


def _preload_script_context(
    *,
    instructions: list[Any],
    navigator: Any,
    ctrl: Any,
    dry_run: bool,
    log_fn: Callable[[str], None],
) -> None:
    needs_focus = (
        not dry_run
        and ctrl is not None
        and ctrl.is_connected()
        and ctrl.input_method not in ("interception",)
    )
    if needs_focus:
        log_fn("[→] Enfocando ventana de Tibia …")
        if ctrl.focus_now():
            log_fn(f"[✓] Ventana de Tibia en primer plano (hwnd={ctrl.hwnd})")
        else:
            log_fn("[!] No se pudo enfocar Tibia — hazlo manualmente antes de que arranque el walker.")
    elif not dry_run:
        log_fn("[S] Skip focus (interception/pico — no necesita foco de ventana)")

    if navigator is None:
        return
    floors_needed: set[int] = set()
    for instruction in instructions:
        if instruction.coord is not None:
            floors_needed.add(instruction.coord.z)
    for floor in sorted(floors_needed):
        if navigator.is_floor_loaded(floor):
            continue
        log_fn(f"[S] Pre-cargando floor {floor} …")
        try:
            navigator.load_floor(floor)
        except Exception as exc:
            log_fn(f"[!] Error cargando floor {floor}: {exc}")


def _apply_script_regions(
    *,
    executor: Any,
    blocked_regions: list[dict[str, Any]],
    walkable_overrides: list[dict[str, Any]],
    log_fn: Callable[[str], None],
) -> None:
    for blocked_region in blocked_regions:
        if not isinstance(blocked_region, dict):
            continue
        try:
            executor.add_blocked_region(
                x_min=int(blocked_region["x_min"]),
                x_max=int(blocked_region["x_max"]),
                y_min=int(blocked_region["y_min"]),
                y_max=int(blocked_region["y_max"]),
                z=int(blocked_region.get("z", 7)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            log_fn(f"[!] Invalid blocked_region entry: {exc}")
    if blocked_regions:
        log_fn(
            f"[S] Pre-blocked {len(executor._blocked_pixels)} tiles "
            f"from {len(blocked_regions)} region(s)"
        )

    walkable_total = 0
    for walkable_override in walkable_overrides:
        if not isinstance(walkable_override, dict):
            continue
        try:
            walkable_total += executor.force_walkable_region(
                x_min=int(walkable_override["x_min"]),
                x_max=int(walkable_override["x_max"]),
                y_min=int(walkable_override["y_min"]),
                y_max=int(walkable_override["y_max"]),
                z=int(walkable_override.get("z", 7)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            log_fn(f"[!] Invalid walkable_override entry: {exc}")
    if walkable_overrides:
        log_fn(
            f"[S] Force-walkable: {walkable_total} tiles flipped "
            f"from {len(walkable_overrides)} override(s)"
        )


def _apply_learned_walkability(
    *,
    loader: Any,
    instructions: list[Any],
    collect_route_critical_tiles_fn: Callable[[list[Any]], set[tuple[int, int, int]]],
    log_fn: Callable[[str], None],
) -> None:
    if loader is None:
        return
    try:
        critical_tiles = collect_route_critical_tiles_fn(instructions)
        learned_tiles = loader.apply_learned_walkability_for_tiles(critical_tiles)
        if learned_tiles:
            log_fn(f"[S] Learned walkability: {learned_tiles} tiles patched from cache")
    except Exception as exc:
        log_fn(f"[!] Learned walkability load error: {exc}")


def _wire_loot_counter(
    *,
    looter: Any,
    get_executor: Callable[[], Any],
) -> Any:
    if looter is None:
        return None
    previous_callback = looter.on_item_looted

    def _loot_item_cb(item_name: str, amount: int) -> None:
        executor = get_executor()
        if executor is not None:
            executor.increment_item_count(item_name, amount)

    looter.on_item_looted = _loot_item_cb
    return previous_callback


def _finalize_script_run(
    *,
    executor: Any,
    route_identity: str,
    loader: Any,
    looter: Any,
    previous_loot_callback: Any,
    get_path_viz: Callable[[], Any],
    set_executor: Callable[[Any], None],
    save_checkpoint_fn: Callable[..., None],
    clear_checkpoint_fn: Callable[[], None],
    log_fn: Callable[[str], None],
) -> None:
    path_viz = get_path_viz()
    if path_viz is not None:
        try:
            path_viz.save_cumulative()
            log_fn("[S] PathVisualizer cumulative image saved.")
        except Exception as exc:
            log_fn(f"[S] PathVisualizer cumulative save failed: {exc}")

    resume_extra = {
        "route_mode": "script",
        "script_resume_instruction_index": executor.resume_instruction_index,
        "script_last_node_instruction_index": executor.last_confirmed_node_index,
        "script_stop_reason": executor.stop_reason,
    }
    if executor.stop_reason in {"movement_failed", "resolver_degraded"}:
        save_checkpoint_fn(route_file=route_identity, extra=resume_extra)
    else:
        clear_checkpoint_fn()

    if loader is not None:
        try:
            from .models import BOUNDS

            dynamic_blocks = executor._blocked_pixels[executor._preblocked_count:]
            dynamic_opened = getattr(executor, "_opened_pixels", [])
            if dynamic_blocks or dynamic_opened:
                block_coords = [
                    (px + BOUNDS["xMin"], py + BOUNDS["yMin"], z)
                    for px, py, z in dynamic_blocks
                ]
                open_coords = [
                    (px + BOUNDS["xMin"], py + BOUNDS["yMin"], z)
                    for px, py, z in dynamic_opened
                ]
                loader.save_learned_blocks(block_coords, opened=open_coords)
        except Exception as exc:
            log_fn(f"[!] Failed to save learned blocks: {exc}")

    set_executor(None)
    if looter is not None:
        looter.on_item_looted = previous_loot_callback


def script_movement_points(instructions: list[Any]) -> list[tuple[int, Coordinate]]:
    movement_kinds = {"node", "stand", "ladder", "shovel", "rope", "random_stand"}
    movement_points: list[tuple[int, Coordinate]] = []
    for idx, instruction in enumerate(instructions):
        if instruction.kind == "label":
            continue
        if instruction.kind in movement_kinds and instruction.coord is not None:
            movement_points.append((idx, instruction.coord.to_tibia_coord()))
    return movement_points


def align_script_start_index(
    *,
    instructions: list[Any],
    start_index: int,
    position: Any,
    navigator: Any,
    log_fn: Callable[[str], None],
    debug_fn: Callable[[Coordinate, Coordinate], None],
) -> int:
    if start_index > 0 or position is None or navigator is None:
        return start_index

    movement_points: list[tuple[int, Coordinate]] = []
    for idx, instruction in enumerate(instructions):
        if instruction.kind == "label":
            continue
        if instruction.kind in {"node", "stand", "ladder", "shovel", "rope", "random_stand"} and instruction.coord is not None:
            movement_points.append((idx, instruction.coord.to_tibia_coord()))
            continue
        if movement_points:
            break

    if len(movement_points) < 2:
        return start_index

    current = position
    for pos in range(len(movement_points) - 1):
        _, segment_start = movement_points[pos]
        next_idx, segment_end = movement_points[pos + 1]
        if current.z != segment_start.z or current.z != segment_end.z:
            continue
        try:
            route = navigator.navigate(segment_start, segment_end)
        except Exception:
            debug_fn(segment_start, segment_end)
            continue
        if not getattr(route, "found", False):
            continue
        if any(step == current for step in getattr(route, "steps", [])):
            log_fn(
                f"[S] Auto-align script start: current={current} lies on prefix "
                f"{segment_start}->{segment_end}; starting from instruction [{next_idx}]"
            )
            return next_idx

    return start_index


def collect_route_critical_tiles(
    *,
    instructions: list[Any],
    navigator: Any,
    debug_fn: Callable[[Coordinate, Coordinate], None],
) -> set[tuple[int, int, int]]:
    movement_points = script_movement_points(instructions)
    critical_tiles = {(coord.x, coord.y, coord.z) for _, coord in movement_points}
    if navigator is None or len(movement_points) < 2:
        return critical_tiles

    for pos in range(len(movement_points) - 1):
        _, segment_start = movement_points[pos]
        _, segment_end = movement_points[pos + 1]
        if segment_start.z != segment_end.z:
            continue
        try:
            route = navigator.navigate(segment_start, segment_end)
        except Exception:
            debug_fn(segment_start, segment_end)
            continue
        if not getattr(route, "found", False):
            continue
        for step in getattr(route, "steps", []):
            critical_tiles.add((step.x, step.y, step.z))
    return critical_tiles


def run_session_script(
    *,
    path: str | Path,
    config: Any,
    ctrl: Any,
    navigator: Any,
    healer: Any,
    frame_getter: Callable[[], Any] | None,
    depot: Any,
    combat: Any,
    radar: Any,
    stuck_detector: Any,
    obstacle_analyzer: Any,
    loader: Any,
    looter: Any,
    get_position: Callable[[], Any],
    set_position: Callable[[Any], None],
    get_real_position_fn: Callable[[], Any],
    set_position_from_executor_fn: Callable[[Any], None],
    npc_handler_factory: Callable[[], Any],
    log_fn: Callable[[str], None],
    get_executor: Callable[[], Any],
    set_executor: Callable[[Any], None],
    get_path_viz: Callable[[], Any],
    set_path_viz: Callable[[Any], None],
    load_checkpoint_fn: Callable[[], Any],
    save_checkpoint_fn: Callable[..., None],
    clear_checkpoint_fn: Callable[[], None],
    align_script_start_index_fn: Callable[..., int],
    collect_route_critical_tiles_fn: Callable[[list[Any]], set[tuple[int, int, int]]],
    resolve_route_fn: Callable[[str | Path], Path],
    parse_file_fn: Callable[[Path], list[Any]],
    json_script_parser: Callable[[Any], list[Any]],
    script_executor_cls: Any,
    force_align: bool = False,
) -> None:
    preparation = _parse_script_source(
        path=path,
        config=config,
        ctrl=ctrl,
        healer=healer,
        get_position=get_position,
        set_position=set_position,
        log_fn=log_fn,
        resolve_route_fn=resolve_route_fn,
        parse_file_fn=parse_file_fn,
        json_script_parser=json_script_parser,
    )

    resume_instruction_index = _resolve_resume_index(
        route_identity=preparation.route_identity,
        instruction_count=len(preparation.instructions),
        load_checkpoint_fn=load_checkpoint_fn,
        log_fn=log_fn,
    )

    # When position_source is "none", pass no position_getter so the
    # executor relies on pure dead-reckoning.  Otherwise the always-on
    # PositionResolver can feed stale/wrong readings that override
    # dead-reckoning and trigger drift-ABORT loops.
    tracking_disabled = getattr(config, "position_source", "none") == "none" and radar is None

    _effective_pos_getter: Callable[[], Any] | None = get_real_position_fn
    if tracking_disabled:
        _effective_pos_getter = None

    executor = script_executor_cls(
        ctrl=ctrl,
        navigator=navigator,
        healer=healer,
        frame_getter=frame_getter,
        depot_manager=depot,
        combat_manager=combat,
        dry_run=preparation.dry_run,
        log_fn=log_fn,
        step_interval=preparation.step_interval,
        position_getter=_effective_pos_getter,
        position_setter=set_position_from_executor_fn,
        hours_leave=preparation.hours_leave,
        rope_hotkey_vk=config.rope_hotkey_vk,
        shovel_hotkey_vk=config.shovel_hotkey_vk,
        npc_handler=npc_handler_factory(),
        minimap_radar=radar,
        stuck_detector=stuck_detector,
    )
    if preparation.wasp_setup is not None:
        executor._wasp_setup = preparation.wasp_setup

    current_position = None if tracking_disabled else get_position()

    _wire_executor_context(
        executor=executor,
        position=current_position,
        obstacle_analyzer=obstacle_analyzer,
        loader=loader,
        set_path_viz=set_path_viz,
        log_fn=log_fn,
    )
    _preload_script_context(
        instructions=preparation.instructions,
        navigator=navigator,
        ctrl=ctrl,
        dry_run=preparation.dry_run,
        log_fn=log_fn,
    )
    _apply_script_regions(
        executor=executor,
        blocked_regions=preparation.blocked_regions,
        walkable_overrides=preparation.walkable_overrides,
        log_fn=log_fn,
    )
    _apply_learned_walkability(
        loader=loader,
        instructions=preparation.instructions,
        collect_route_critical_tiles_fn=collect_route_critical_tiles_fn,
        log_fn=log_fn,
    )

    # Call auto-alignment when position tracking is active, OR when the caller
    # explicitly opts in (force_align=True) because it has a known position
    # even though the live-tracking pipeline is disabled.
    effective_start_index = resume_instruction_index
    if not tracking_disabled or force_align:
        effective_start_index = align_script_start_index_fn(
            instructions=preparation.instructions,
            start_index=resume_instruction_index,
        )

    log_fn(
        f"[S] Running script: {preparation.route_path.name} "
        f"({len(preparation.instructions)} instructions)"
    )

    previous_loot_callback = _wire_loot_counter(looter=looter, get_executor=get_executor)
    set_executor(executor)
    try:
        executor.execute(preparation.instructions, start_index=effective_start_index)
    finally:
        _finalize_script_run(
            executor=executor,
            route_identity=preparation.route_identity,
            loader=loader,
            looter=looter,
            previous_loot_callback=previous_loot_callback,
            get_path_viz=get_path_viz,
            set_executor=set_executor,
            save_checkpoint_fn=save_checkpoint_fn,
            clear_checkpoint_fn=clear_checkpoint_fn,
            log_fn=log_fn,
        )