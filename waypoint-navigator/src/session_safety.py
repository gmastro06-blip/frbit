from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class SafetySubsystems:
    position: Any
    death_handler: Any = None
    reconnect_handler: Any = None
    anti_kick: Any = None
    stuck_detector: Any = None


def initialize_safety_handlers(
    *,
    config: Any,
    ctrl: Any,
    event_bus: Any,
    cached_getter: Any,
    log_fn: Callable[[str], None],
    current_position: Any,
    parse_start_pos: Callable[[], Any],
    pause_subsystems: Callable[[], None],
    resume_subsystems: Callable[[], None],
    navigate_back_to: Callable[..., Any],
    stop_session: Callable[[], None],
    login_fn: Any,
    death_handler_cls: Any,
    death_config_cls: Any,
    reconnect_handler_cls: Any,
    reconnect_config_cls: Any,
    anti_kick_cls: Any,
    anti_kick_config_cls: Any,
    stuck_detector_cls: Any,
) -> SafetySubsystems:
    position = current_position
    if position is None:
        parsed_position = parse_start_pos()
        if parsed_position is not None:
            position = parsed_position
            log_fn(f"[S] Start position (CLI): {position}")

    result = SafetySubsystems(position=position)

    if config.death_handler:
        re_equip_hotkeys = _parse_re_equip_hotkeys(config.re_equip_hotkeys)
        death_config = death_config_cls(
            max_deaths=config.max_deaths,
            re_equip_hotkeys=re_equip_hotkeys,
            confirm_frames=5,
        )
        result.death_handler = death_handler_cls(ctrl=ctrl, config=death_config)
        result.death_handler.set_log_callback(log_fn)
        result.death_handler.set_event_bus(event_bus)
        result.death_handler.set_pause_fn(pause_subsystems)
        result.death_handler.set_resume_fn(resume_subsystems)
        result.death_handler.set_position_getter(lambda: result.position)
        result.death_handler.set_navigate_fn(navigate_back_to)
        result.death_handler.set_stop_session_fn(stop_session)
        if cached_getter:
            result.death_handler.set_frame_getter(cached_getter)
        result.death_handler.start()
        log_fn("DeathHandler started.")

    if config.reconnect_handler:
        reconnect_config = reconnect_config_cls(
            max_retries=config.reconnect_max_retries,
            server_save_hours=[float(hour.strip()) for hour in config.server_save_hours.split(",") if hour.strip()],
        )
        result.reconnect_handler = reconnect_handler_cls(ctrl=ctrl, config=reconnect_config)
        result.reconnect_handler.set_log_callback(log_fn)
        result.reconnect_handler.set_event_bus(event_bus)
        result.reconnect_handler.set_pause_fn(pause_subsystems)
        result.reconnect_handler.set_resume_fn(resume_subsystems)
        if cached_getter:
            result.reconnect_handler.set_frame_getter(cached_getter)
        if login_fn is not None:
            result.reconnect_handler.set_login_fn(login_fn)
        result.reconnect_handler.start()
        log_fn("ReconnectHandler started.")

    if config.anti_kick:
        anti_kick_config = anti_kick_config_cls(idle_threshold=config.anti_kick_idle)
        result.anti_kick = anti_kick_cls(ctrl=ctrl, config=anti_kick_config)
        result.anti_kick.set_log_callback(log_fn)
        result.anti_kick.start()
        log_fn(f"AntiKick started (idle threshold {config.anti_kick_idle:.0f}s).")

    if config.stuck_detector:
        result.stuck_detector = stuck_detector_cls()
        result.stuck_detector.set_log_callback(log_fn)
        result.stuck_detector.set_event_bus(event_bus)
        result.stuck_detector.set_position_getter(lambda: result.position)
        if ctrl is not None:
            narrowed_ctrl = ctrl

            def _nudge(dx: int, dy: int) -> None:
                if dx != 0:
                    narrowed_ctrl.move("right" if dx > 0 else "left", abs(dx))
                else:
                    narrowed_ctrl.move("down" if dy > 0 else "up", abs(dy))

            result.stuck_detector.set_nudge_fn(_nudge)
            result.stuck_detector.set_escape_fn(
                lambda: (narrowed_ctrl.press_key(0x1B), None)[-1]
            )
        result.stuck_detector.start()
        log_fn("StuckDetector started.")

    return result


def _parse_re_equip_hotkeys(value: str) -> list[int]:
    re_equip_hotkeys: list[int] = []
    if not value.strip():
        return re_equip_hotkeys
    for token in value.split(","):
        token = token.strip()
        if token:
            re_equip_hotkeys.append(int(token, 0))
    return re_equip_hotkeys