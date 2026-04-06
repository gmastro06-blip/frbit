from __future__ import annotations

import dataclasses
import random
import time
from pathlib import Path
from typing import Any, Callable


def apply_smart_defaults(*, session: Any, templates_base: Path) -> list[str]:
    enabled: list[str] = []
    if not session._cfg.pvp_detector:
        skulls = templates_base / "skulls"
        if skulls.is_dir() and any(skulls.glob("*.png")):
            session._cfg.pvp_detector = True
            enabled.append("pvp_detector")
    if not session._cfg.monitor_conditions:
        conditions = templates_base / "conditions"
        if conditions.is_dir() and any(conditions.glob("*.png")):
            session._cfg.monitor_conditions = True
            enabled.append("monitor_conditions")
    if not session._cfg.adaptive_roi:
        anchors = templates_base / "anchors"
        if anchors.is_dir() and any(anchors.glob("*.png")):
            session._cfg.adaptive_roi = True
            enabled.append("adaptive_roi")
    return enabled


def run_preflight_checks(*, session: Any, run_preflight_fn: Any) -> None:
    if session._cfg.dry_run:
        session._log("[PREFLIGHT] Skipped (dry_run=True)")
        return

    skip_driver = (session._cfg.input_method != "interception") or getattr(session._cfg, "arduino_enabled", False)
    result = run_preflight_fn(session._cfg, skip_driver=skip_driver, log_fn=session._log)
    if not result.ok:
        session._log("[PREFLIGHT] " + result.summary())
        session._set_running(False)
        raise RuntimeError(
            "Preflight FAILED — cannot start.\n"
            + "\n".join(str(failure) for failure in result.failures)
        )
    session._log(f"[PREFLIGHT] {len(result.results)} checks passed")


def start_session(*, session: Any, set_jitter_fn: Any, reset_fatigue_fn: Any, run_preflight_fn: Any, templates_base: Path) -> None:
    if session._get_running():
        session._log("Session already running — ignored.")
        return

    session._log("Starting session …")
    session._replace_stats(start_time=time.time())
    session._set_running(True)
    session._startup_phase = "init"

    set_jitter_fn(session._cfg.jitter_pct)
    reset_fatigue_fn()

    enabled = apply_smart_defaults(session=session, templates_base=templates_base)
    if enabled:
        session._log(f"Smart defaults: auto-enabled {', '.join(enabled)} (templates found).")

    run_preflight_checks(session=session, run_preflight_fn=run_preflight_fn)

    try:
        session._startup_subsystems()
    except Exception as exc:
        session._log(f"[!] FATAL: startup failed during phase '{session._startup_phase}': {exc}")
        session._set_running(False)
        session.stop(force_cleanup=True)
        raise


def startup_subsystems(*, session: Any) -> None:
    session._init_input()
    session._init_navigation()
    session._init_healer()
    cached = session._init_capture()
    session._init_optional_subsystems(cached)
    session._init_safety_handlers(cached)
    session._init_integrated_modules()
    session._init_monitoring(cached)
    session._start_threads()


def init_input(
    *,
    session: Any,
    input_controller_cls: Any,
    his_available: bool,
    his_cls: Any,
    his_config_path: Path,
    arduino_available: bool,
    arduino_config_cls: Any,
    arduino_hid_cls: Any,
    pico_available: bool,
    pico_config_cls: Any,
    pico_hid_cls: Any,
) -> None:
    session._startup_phase = "input_controller"
    raw_ctrl = input_controller_cls(
        target_title=session._cfg.target_window,
        input_method=session._cfg.input_method,
        jitter_pct=session._cfg.jitter_pct,
    )
    raw_ctrl.find_target()
    if not raw_ctrl.is_connected():
        session._log(f"[!] Window '{session._cfg.target_window}' not found. Continuing in offline mode.")
    if session._cfg.input_method == "interception" and not raw_ctrl.interception_available:
        raise RuntimeError(
            "[E] INTERCEPTION DRIVER NOT AVAILABLE.\n"
            "Cannot start with PostMessage fallback.\n"
            "Install the Interception driver and reboot."
        )

    if arduino_available and getattr(session._cfg, "arduino_enabled", False):
        ard_cfg = arduino_config_cls(enabled=True, port=getattr(session._cfg, "arduino_port", "auto"))
        arduino = arduino_hid_cls(config=ard_cfg, fallback_controller=raw_ctrl)
        if arduino.initialize():
            session._arduino = arduino
            session._log("[Arduino] HID hardware conectado — input via USB HID real")
        else:
            session._log("[Arduino] No disponible — continuando sin hardware HID")

    if pico_available and getattr(session._cfg, "pico_enabled", False):
        pico_cfg = pico_config_cls(enabled=True, port=getattr(session._cfg, "pico_port", "auto"))
        pico = pico_hid_cls(config=pico_cfg, fallback_controller=raw_ctrl)
        if pico.initialize():
            session._pico = pico
            raw_ctrl.set_arduino_failover(pico)
            raw_ctrl._using_arduino_failover = True
            session._log("[Pico2] HID hardware conectado — input via USB HID real (PRIMARY)")
        else:
            session._log("[Pico2] No disponible — continuando sin hardware HID")

    if his_available:
        try:
            session._ctrl = his_cls(str(his_config_path), raw_ctrl)
            session._log("[HIS] Human Input System activo — fatiga, errores humanos, circadiano habilitados")
        except Exception as exc:
            session._ctrl = raw_ctrl
            session._log(f"[HIS] Error inicializando HIS ({exc}) — usando InputController directo")
    else:
        session._ctrl = raw_ctrl
        session._log("[HIS] human_input_system no disponible — usando InputController directo")
    session._raw_ctrl = raw_ctrl


def init_navigation(*, session: Any, map_loader_cls: Any, navigator_cls: Any) -> None:
    session._startup_phase = "navigator"
    if session._loader is None:
        session._loader = map_loader_cls(log_fn=session._log)
    session._navigator = navigator_cls()
    session._navigator.loader = session._loader


def init_healer(*, session: Any, heal_config_cls: Any, auto_healer_cls: Any) -> None:
    session._startup_phase = "healer"
    heal_cfg = dataclasses.replace(
        heal_config_cls.load(),
        hp_threshold_pct=session._cfg.heal_hp_pct,
        hp_emergency_pct=session._cfg.heal_emergency_pct,
        mp_threshold_pct=session._cfg.mana_threshold_pct,
        heal_hotkey_vk=session._cfg.heal_hotkey_vk,
        mana_hotkey_vk=session._cfg.mana_hotkey_vk,
        emergency_hotkey_vk=session._cfg.emergency_hotkey_vk,
    )
    session._healer = auto_healer_cls(session._ctrl, heal_cfg)
    session._healer.set_log_callback(session._log)

    def _on_heal() -> None:
        session._inc_stat("heal_fired")
        payload = {"hp_pct": getattr(session._healer, "_hp_pct", None)}
        session._event_bus.emit(session.__class__.HEAL if hasattr(session.__class__, 'HEAL') else "e4", payload)
        session._event_bus.emit("heal", payload)

    def _on_mana() -> None:
        session._inc_stat("mana_fired")
        payload = {"mp_pct": getattr(session._healer, "_mp_pct", None)}
        session._event_bus.emit("e6", payload)
        session._event_bus.emit("mana", payload)

    session._healer.on_heal = _on_heal
    session._healer.on_mana = _on_mana
    session._healer.start()

    critical_check_setter = getattr(session._ctrl, "set_critical_check", None)
    if callable(critical_check_setter):
        critical_check_setter(
            lambda: (session._healer is not None and session._healer.is_running)
            or (session._combat is not None and session._combat.is_in_combat)
        )


def init_capture(*, session: Any, initialize_capture_pipeline_fn: Any, build_frame_getter_fn: Any) -> Any:
    session._startup_phase = "frame_source"
    session._radar = None
    capture = initialize_capture_pipeline_fn(
        config=session._cfg,
        ctrl=session._ctrl,
        event_bus=session._event_bus,
        log_fn=session._log,
        build_frame_getter=build_frame_getter_fn,
        existing_frame_getter=session._frame_getter,
        existing_frame_cache=session._frame_cache,
    )
    session._frame_getter = capture.frame_getter
    session._frame_cache = capture.frame_cache
    session._frame_watchdog = capture.frame_watchdog
    return capture.cached_getter


def init_optional_subsystems(*, session: Any, cached_getter: Any, initialize_optional_subsystems_fn: Any, session_events: Any, depot_manager_cls: Any, looter_cls: Any, combat_manager_cls: Any, combat_config_cls: Any, hpmp_detector_cls: Any, condition_monitor_cls: Any, condition_config_cls: Any, trade_manager_cls: Any, trade_config_cls: Any) -> None:
    optional = initialize_optional_subsystems_fn(
        config=session._cfg,
        ctrl=session._ctrl,
        healer=session._healer,
        event_bus=session._event_bus,
        cached_getter=cached_getter,
        log_fn=session._log,
        position_getter=lambda: session._position,
        loot_in_progress=session._loot_in_progress,
        session_events=session_events,
        depot_manager_cls=depot_manager_cls,
        looter_cls=looter_cls,
        combat_manager_cls=combat_manager_cls,
        combat_config_cls=combat_config_cls,
        hpmp_detector_cls=hpmp_detector_cls,
        condition_monitor_cls=condition_monitor_cls,
        condition_config_cls=condition_config_cls,
        trade_manager_cls=trade_manager_cls,
        trade_config_cls=trade_config_cls,
    )
    session._obstacle_analyzer = optional.obstacle_analyzer
    session._depot = optional.depot
    session._looter = optional.looter
    session._combat = optional.combat
    session._condition_monitor = optional.condition_monitor
    session._trade = optional.trade


def init_safety_handlers(*, session: Any, cached_getter: Any, initialize_safety_handlers_fn: Any, death_handler_cls: Any, death_config_cls: Any, reconnect_handler_cls: Any, reconnect_config_cls: Any, anti_kick_cls: Any, anti_kick_config_cls: Any, stuck_detector_cls: Any) -> None:
    safety = initialize_safety_handlers_fn(
        config=session._cfg,
        ctrl=session._ctrl,
        event_bus=session._event_bus,
        cached_getter=cached_getter,
        log_fn=session._log,
        current_position=session._position,
        parse_start_pos=session._parse_start_pos,
        pause_subsystems=session._pause_subsystems,
        resume_subsystems=session._resume_subsystems,
        navigate_back_to=session._navigate_back_to,
        stop_session=session.stop,
        login_fn=session._login_fn,
        death_handler_cls=death_handler_cls,
        death_config_cls=death_config_cls,
        reconnect_handler_cls=reconnect_handler_cls,
        reconnect_config_cls=reconnect_config_cls,
        anti_kick_cls=anti_kick_cls,
        anti_kick_config_cls=anti_kick_config_cls,
        stuck_detector_cls=stuck_detector_cls,
    )
    session._position = safety.position
    session._death_handler = safety.death_handler
    session._reconnect_handler = safety.reconnect_handler
    session._anti_kick = safety.anti_kick
    session._stuck_det = safety.stuck_detector


def init_integrated_modules(*, session: Any, initialize_integrated_modules_fn: Any, frame_quality_checker_cls: Any, position_resolver_cls: Any, position_resolver_config_cls: Any, source_kind_cls: Any, tibia_local_minimap_reader_cls: Any, pvp_detector_cls: Any, pvp_config_cls: Any, pvp_action_cls: Any, inventory_manager_cls: Any, inventory_config_cls: Any, resupply_config_cls: Any, depot_orchestrator_cls: Any) -> None:
    integrated = initialize_integrated_modules_fn(
        config=session._cfg,
        radar=session._radar,
        event_bus=session._event_bus,
        depot_manager=session._depot,
        trade_manager=session._trade,
        navigator=session._navigator,
        ctrl=session._ctrl,
        walk_route=session._walk_route,
        log_fn=session._log,
        current_position=session._position,
        frame_quality_checker_cls=frame_quality_checker_cls,
        position_resolver_cls=position_resolver_cls,
        position_resolver_config_cls=position_resolver_config_cls,
        source_kind_cls=source_kind_cls,
        tibia_local_minimap_reader_cls=tibia_local_minimap_reader_cls,
        pvp_detector_cls=pvp_detector_cls,
        pvp_config_cls=pvp_config_cls,
        pvp_action_cls=pvp_action_cls,
        inventory_manager_cls=inventory_manager_cls,
        inventory_config_cls=inventory_config_cls,
        resupply_config_cls=resupply_config_cls,
        depot_orchestrator_cls=depot_orchestrator_cls,
    )
    session._path_viz = integrated.path_viz
    session._frame_quality = integrated.frame_quality
    session._pos_resolver = integrated.position_resolver
    session._local_reader = integrated.local_reader
    session._pvp_detector = integrated.pvp_detector
    session._inventory_mgr = integrated.inventory_manager
    session._depot_orch = integrated.depot_orchestrator
    session._position = integrated.position


def init_monitoring(*, session: Any, cached_getter: Any, initialize_monitoring_fn: Any, alert_system_cls: Any, alert_config_cls: Any, session_stats_cls: Any, spawn_manager_cls: Any, adaptive_roi_detector_cls: Any, break_scheduler_cls: Any, break_scheduler_config_cls: Any, soak_monitor_cls: Any, soak_monitor_config_cls: Any, gm_detector_cls: Any, gm_detector_config_cls: Any, gm_action_cls: Any, chat_responder_cls: Any, chat_responder_config_cls: Any) -> None:
    monitoring = initialize_monitoring_fn(
        config=session._cfg,
        ctrl=session._ctrl,
        event_bus=session._event_bus,
        cached_getter=cached_getter,
        frame_getter=session._frame_getter,
        log_fn=session._log,
        log_callback=session._log_cb,
        stats_snapshot=session.monitor_snapshot,
        pause_subsystems=session._pause_subsystems,
        resume_subsystems=session._resume_subsystems,
        stop_session=session.stop,
        alert_system_cls=alert_system_cls,
        alert_config_cls=alert_config_cls,
        session_stats_cls=session_stats_cls,
        spawn_manager_cls=spawn_manager_cls,
        adaptive_roi_detector_cls=adaptive_roi_detector_cls,
        break_scheduler_cls=break_scheduler_cls,
        break_scheduler_config_cls=break_scheduler_config_cls,
        soak_monitor_cls=soak_monitor_cls,
        soak_monitor_config_cls=soak_monitor_config_cls,
        gm_detector_cls=gm_detector_cls,
        gm_detector_config_cls=gm_detector_config_cls,
        gm_action_cls=gm_action_cls,
        chat_responder_cls=chat_responder_cls,
        chat_responder_config_cls=chat_responder_config_cls,
    )
    session._alert_system = monitoring.alert_system
    session._session_stats = monitoring.session_stats
    session._spawn_mgr = monitoring.spawn_manager
    session._adaptive_roi = monitoring.adaptive_roi
    session._dashboard = monitoring.dashboard
    session._break_scheduler = monitoring.break_scheduler
    session._soak_monitor = monitoring.soak_monitor
    session._gm_detector = monitoring.gm_detector
    session._chat_responder = monitoring.chat_responder
    if monitoring.log_callback is not None:
        session._log_cb = monitoring.log_callback


def start_threads(*, session: Any, start_session_threads_fn: Any, find_window_fn: Any, thread_cls: Any, monotonic_fn: Any, sleep_fn: Any, random_uniform_fn: Any) -> None:
    startup = start_session_threads_fn(
        config=session._cfg,
        raw_ctrl=session._raw_ctrl,
        arduino=session._arduino,
        log_fn=session._log,
        window_watchdog_target=session._window_watchdog_loop,
        watchdog_target=session._watchdog_loop,
        run_loop_target=session._run_loop,
        wait_for_start_position_lock=session._wait_for_start_position_lock,
        thread_factory=thread_cls,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
        random_uniform_fn=random_uniform_fn,
        find_window_fn=find_window_fn,
    )
    session._ww_hwnds = startup.ww_hwnds
    session._ww_thread = startup.ww_thread
    session._watchdog_thread = startup.watchdog_thread
    session._thread = startup.main_thread
    session._last_move_time = startup.last_move_time