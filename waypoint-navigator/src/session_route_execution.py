from __future__ import annotations

from typing import Any, Callable


def execute_route(
    *,
    route: Any,
    ctrl: Any,
    config: Any,
    is_running: Callable[[], bool],
    log_fn: Callable[[str], None],
    loot_in_progress: Any,
    set_last_move_time: Callable[[float], None],
    anti_kick: Any,
    radar: Any,
    frame_getter: Callable[[], Any] | None,
    get_position: Callable[[], Any],
    update_position_fn: Callable[..., Any],
    check_frame_extras_fn: Callable[[Any], None],
    monotonic_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
    random_uniform_fn: Callable[[float, float], float],
    jittered_sleep_fn: Callable[..., Any],
    macro_pause_fn: Callable[..., Any],
    verify_position_changed_fn: Callable[..., Any],
) -> None:
    if ctrl is None or not ctrl.is_connected():
        return

    step_jitter = config.step_delay_min < config.step_delay_max
    for index in range(1, len(route.steps)):
        if not is_running():
            return
        if loot_in_progress.is_set():
            log_fn("  [W] [PAUSE] Loot en curso -- walker en pausa")
            loot_deadline = monotonic_fn() + 12.0
            while is_running() and loot_in_progress.is_set() and monotonic_fn() < loot_deadline:
                sleep_fn(0.1)
            if loot_in_progress.is_set():
                loot_in_progress.clear()
                log_fn("  [W] [WARN] Timeout esperando loot -- reanudando walker")

        previous_step = route.steps[index - 1]
        current_step = route.steps[index]
        delta_x = current_step.x - previous_step.x
        delta_y = current_step.y - previous_step.y
        ctrl.move_to_tile(delta_x, delta_y)
        set_last_move_time(monotonic_fn())

        if config.step_interval > 0:
            jittered_sleep_fn(config.step_interval)
        macro_pause_fn(log_fn=log_fn)
        if anti_kick is not None:
            anti_kick.notify_activity()

        if index % 3 == 0 and radar is not None and frame_getter is not None:
            moved = verify_position_changed_fn(
                radar,
                get_position(),
                frame_getter,
                timeout=1.5,
                poll_interval=0.25,
            )
            if not moved and is_running():
                log_fn(f"  [V] ⚠ No movement detected at step {index} — retrying")
                ctrl.move_to_tile(delta_x, delta_y)
                sleep_fn(random_uniform_fn(0.2, 0.45))

        if index % 5 == 0:
            update_position_fn()
            if frame_getter is not None:
                frame = frame_getter()
                if frame is not None:
                    check_frame_extras_fn(frame)

        if step_jitter:
            sleep_fn(random_uniform_fn(config.step_delay_min, config.step_delay_max))


def navigate_back_to(
    *,
    target: Any,
    ctrl: Any,
    log_fn: Callable[[str], None],
    update_position_fn: Callable[..., Any],
    get_position: Callable[[], Any],
    navigate_to_fn: Callable[..., Any],
    exec_route_fn: Callable[[Any], None],
    is_running: Callable[[], bool],
) -> bool:
    if ctrl is None or not ctrl.is_connected():
        log_fn("[NAV-BACK] No input controller — cannot navigate")
        return False
    update_position_fn()
    start = get_position()
    if start is None:
        log_fn("[NAV-BACK] Unknown current position — cannot navigate")
        return False
    try:
        routes = navigate_to_fn(start, target, multifloor=(start.z != target.z))
    except Exception as exc:
        log_fn(f"[NAV-BACK] Route calculation failed: {exc}")
        return False

    for route in routes:
        if not getattr(route, "found", False):
            log_fn(f"[NAV-BACK] No route found → {target}")
            return False
        exec_route_fn(route)
        if not is_running():
            return False

    log_fn(f"[NAV-BACK] Arrived at {target}")
    return True


def walk_route(
    *,
    route: Any,
    ctrl: Any,
    exec_route_fn: Callable[[Any], None],
    is_running: Callable[[], bool],
    log_fn: Callable[[str], None],
) -> bool:
    if ctrl is None or not ctrl.is_connected():
        return False
    try:
        exec_route_fn(route)
        return is_running()
    except Exception as exc:
        log_fn(f"[WALK-ROUTE] Error: {exc}")
        return False


def click_character_tile(
    *,
    ctrl: Any,
    frame_getter: Callable[[], Any] | None,
    debug_fn: Callable[[], None],
) -> None:
    if ctrl is None:
        return
    frame_width, frame_height = 1920, 1009
    if frame_getter is not None:
        try:
            frame = frame_getter()
            if frame is not None and frame.size > 0:
                frame_height, frame_width = frame.shape[:2]
        except Exception:
            debug_fn()
    game_right = int(frame_width * 0.817)
    game_bottom = int(frame_height * 0.83)
    center_x = game_right // 2
    center_y = game_bottom // 2
    ctrl.click(center_x, center_y, button="left")


def execute_transition(
    *,
    transition: Any,
    ctrl: Any,
    config: Any,
    get_position: Callable[[], Any],
    radar: Any,
    frame_getter: Callable[[], Any] | None,
    update_position_fn: Callable[..., Any],
    click_character_tile_fn: Callable[[], None],
    log_fn: Callable[[str], None],
    jittered_sleep_fn: Callable[..., Any],
) -> None:
    if ctrl is None:
        return

    current_position = get_position()
    old_z = current_position.z if current_position is not None else None
    kind = transition.kind

    def _trigger_transition_action() -> None:
        if kind in ("walk", "ladder"):
            return
        if kind == "rope":
            hotkey = config.rope_hotkey_vk
        elif kind == "shovel":
            hotkey = config.shovel_hotkey_vk
        elif kind == "use":
            hotkey = config.rope_hotkey_vk
        else:
            hotkey = 0
        if hotkey:
            ctrl.press_key(hotkey)
            jittered_sleep_fn(0.35)
            click_character_tile_fn()

    _trigger_transition_action()

    delay = config.transition_delay
    if delay > 0:
        jittered_sleep_fn(delay)

    if old_z is None or radar is None or frame_getter is None:
        return

    update_position_fn()
    new_position = get_position()
    new_z = new_position.z if new_position is not None else None
    if new_z is None or new_z != old_z:
        return

    log_fn(f"  [TRANSITION] ⚠ z-level unchanged ({old_z}) after {kind} — retrying")
    _trigger_transition_action()
    if delay > 0:
        jittered_sleep_fn(delay * 2.0)


def execute_multifloor(
    *,
    segments: list[Any],
    navigator: Any,
    is_running: Callable[[], bool],
    exec_route_fn: Callable[[Any], None],
    exec_transition_fn: Callable[[Any], None],
    log_fn: Callable[[str], None],
    transition_delay: float,
    sleep_fn: Callable[[float], None],
    random_uniform_fn: Callable[[float, float], float],
) -> None:
    if navigator is None:
        return

    transitions_by_entry: dict[Any, Any] = {}
    if hasattr(navigator, "transitions"):
        registry = navigator.transitions
        for transition in getattr(registry, "transitions", []):
            transitions_by_entry[transition.entry] = transition

    for index, route in enumerate(segments):
        if not is_running():
            return
        if not route.found:
            log_fn(f"Segment {index} has no valid path — skipping.")
            continue

        exec_route_fn(route)
        if index >= len(segments) - 1 or not is_running():
            continue

        last_tile = route.steps[-1] if route.steps else route.end
        transition = transitions_by_entry.get(last_tile)
        if transition is None and last_tile != route.end:
            transition = transitions_by_entry.get(route.end)
        if transition is not None:
            log_fn(f"Floor transition: {transition.kind} {transition.entry} → {transition.exit}")
            exec_transition_fn(transition)
            continue

        log_fn(
            f"⚠ Transición de piso no registrada en tile {last_tile} "
            f"(segmento {index + 1}/{len(segments)}) — "
            f"añade la entrada en cache/transitions.json"
        )
        sleep_fn(transition_delay * random_uniform_fn(0.8, 1.25))