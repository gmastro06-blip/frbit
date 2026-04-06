from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class MonitoringSubsystems:
    alert_system: Any = None
    session_stats: Any = None
    spawn_manager: Any = None
    adaptive_roi: Any = None
    dashboard: Any = None
    break_scheduler: Any = None
    soak_monitor: Any = None
    gm_detector: Any = None
    chat_responder: Any = None
    log_callback: Callable[[str], None] | None = None


def initialize_monitoring(
    *,
    config: Any,
    ctrl: Any,
    event_bus: Any,
    cached_getter: Any,
    frame_getter: Any,
    log_fn: Callable[[str], None],
    log_callback: Callable[[str], None],
    stats_snapshot: Callable[[], Any],
    pause_subsystems: Callable[[], None],
    resume_subsystems: Callable[[], None],
    stop_session: Callable[[], None],
    alert_system_cls: Any,
    alert_config_cls: Any,
    session_stats_cls: Any,
    spawn_manager_cls: Any,
    adaptive_roi_detector_cls: Any,
    break_scheduler_cls: Any,
    break_scheduler_config_cls: Any,
    soak_monitor_cls: Any,
    soak_monitor_config_cls: Any,
    gm_detector_cls: Any,
    gm_detector_config_cls: Any,
    gm_action_cls: Any,
    chat_responder_cls: Any,
    chat_responder_config_cls: Any,
) -> MonitoringSubsystems:
    result = MonitoringSubsystems(log_callback=log_callback)

    if config.alert_enabled:
        alert_config = alert_config_cls(
            enabled=True,
            discord_webhook=config.alert_discord_webhook,
            telegram_bot_token=config.alert_telegram_token,
            telegram_chat_id=config.alert_telegram_chat,
        )
        result.alert_system = alert_system_cls(config=alert_config)
        result.alert_system.subscribe(event_bus)
        log_fn("AlertSystem enabled (subscribed to EventBus).")

    if config.session_stats:
        result.session_stats = session_stats_cls(event_bus=event_bus)
        result.session_stats.subscribe(event_bus)
        result.session_stats.start()
        log_fn("HuntingSessionStats tracking started.")

    def _on_permanent_stop(data: Any) -> None:
        log_fn(
            "[!] PERMANENT STOP detectado (StuckDetector alcanzó max_aborts) "
            "- deteniendo sesión automáticamente."
        )
        _ = data
        stop_session()

    event_bus.subscribe("e32", _on_permanent_stop)

    if config.spawn_manager:
        result.spawn_manager = spawn_manager_cls(event_bus=event_bus)

        def _on_pvp_detected(data: Any) -> None:
            if result.spawn_manager is None:
                return
            current = result.spawn_manager.current_spawn
            switched = result.spawn_manager.switch_spawn()
            if switched:
                log_fn(
                    f"[S] PvP detectado — spawn '{current}' marcado ocupado, "
                    f"cambiando a '{switched.name}'"
                )
            else:
                log_fn(
                    f"[S] PvP detectado en '{current}' — no hay spawns alternativos disponibles"
                )

        event_bus.subscribe("e18", _on_pvp_detected)
        log_fn("SpawnManager enabled (auto-switch on PvP via e18).")

    if config.adaptive_roi:
        result.adaptive_roi = adaptive_roi_detector_cls()
        anchor_count = result.adaptive_roi.load_anchors_from_dir()
        if anchor_count:
            log_fn(f"AdaptiveROIDetector enabled ({anchor_count} anchors).")
        else:
            log_fn("AdaptiveROIDetector enabled (proportional fallback).")

    if config.dashboard:
        try:
            from .dashboard_server import DashboardServer

            result.dashboard = DashboardServer(
                port=config.dashboard_port,
                ws_port=config.dashboard_ws_port,
                auth_token=config.dashboard_auth_token,
            )
            result.dashboard.set_stats_fn(stats_snapshot)
            original_log_fn: Callable[[str], None] = result.log_callback or log_fn

            def _dashboard_log(message: str) -> None:
                original_log_fn(message)
                if result.dashboard:
                    result.dashboard.push_log(message)

            result.log_callback = _dashboard_log
            for event_name in ("e1", "e4", "e6", "e2", "e3", "waypoint", "stuck", "pvp", "e31", "e32"):
                def _make_event_handler(name: str) -> Callable[[Any], None]:
                    def _handler(data: Any) -> None:
                        if result.dashboard:
                            result.dashboard.push_event(name, data)

                    return _handler

                event_bus.subscribe(event_name, _make_event_handler(event_name))
            result.dashboard.start()
            log_fn(
                f"Dashboard started: {result.dashboard.http_url}  |  "
                f"WS: {result.dashboard.ws_url}"
            )
        except ImportError:
            log_fn("[!] Dashboard requires 'websockets' - pip install websockets")
        except Exception as error:
            log_fn(f"[!] Dashboard init failed: {error}")

    if config.break_scheduler:
        break_scheduler_config = break_scheduler_config_cls(
            play_min_minutes=config.break_play_min,
            play_max_minutes=config.break_play_max,
            break_min_minutes=config.break_min,
            break_max_minutes=config.break_max,
            long_break_after_hours=config.break_long_after_h,
            long_break_min_minutes=config.break_long_min,
            long_break_max_minutes=config.break_long_max,
        )
        result.break_scheduler = break_scheduler_cls(
            config=break_scheduler_config,
            log_fn=log_fn,
        )
        result.break_scheduler.start()
        log_fn("BreakScheduler enabled (anti-ban session pauses).")

    if config.auto_calibrate_roi and frame_getter is not None:
        try:
            calibration_frame = frame_getter()
            if calibration_frame is not None:
                frame_height, frame_width = calibration_frame.shape[:2]
                if result.adaptive_roi is None:
                    result.adaptive_roi = adaptive_roi_detector_cls()
                rois = result.adaptive_roi.detect_or_fallback(calibration_frame)
                log_fn(
                    f"Auto-calibrated {len(rois)} ROIs "
                    f"for {frame_width}x{frame_height} frame: {', '.join(rois.keys())}."
                )
        except Exception as error:
            log_fn(f"[!] Auto-calibration failed: {error}")

    if config.soak_monitor:
        soak_monitor_config = soak_monitor_config_cls(
            sample_interval=config.soak_sample_interval,
            memory_warn_mb=config.soak_memory_warn_mb,
        )
        result.soak_monitor = soak_monitor_cls(config=soak_monitor_config, log_fn=log_fn)
        result.soak_monitor.start()

    if config.gm_detector:
        gm_action_map = {
            "alert": gm_action_cls.ALERT,
            "pause": gm_action_cls.PAUSE,
            "logout": gm_action_cls.LOGOUT,
            "mimic": gm_action_cls.HUMAN_MIMIC,
        }
        gm_detector_config = gm_detector_config_cls(
            action=gm_action_map.get(config.gm_action.lower(), gm_action_cls.PAUSE),
            scan_interval=config.gm_scan_interval,
        )
        result.gm_detector = gm_detector_cls(config=gm_detector_config, event_bus=event_bus)
        result.gm_detector.set_log_callback(log_fn)
        if cached_getter:
            result.gm_detector.set_frame_getter(cached_getter)
        if ctrl:
            result.gm_detector.set_input_controller(ctrl)
        result.gm_detector.set_pause_fn(pause_subsystems)
        result.gm_detector.set_resume_fn(resume_subsystems)
        result.gm_detector.start()
        log_fn(f"GMDetector enabled (action={config.gm_action}).")

    if config.chat_responder:
        chat_responder_config = chat_responder_config_cls(
            response_delay_min=config.chat_response_delay_min,
            response_delay_max=config.chat_response_delay_max,
        )
        result.chat_responder = chat_responder_cls(
            config=chat_responder_config,
            event_bus=event_bus,
        )
        result.chat_responder.set_log_callback(log_fn)
        if cached_getter:
            result.chat_responder.set_frame_getter(cached_getter)
        if ctrl:
            result.chat_responder.set_input_controller(ctrl)
        result.chat_responder.start()
        log_fn("ChatResponder enabled (auto-reply to PMs).")

    return result