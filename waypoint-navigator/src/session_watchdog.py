from __future__ import annotations

import ctypes
from typing import Any, Callable


def check_subsystem_health(
    *,
    healer: Any,
    combat: Any,
    looter: Any,
    death_handler: Any,
    reconnect_handler: Any,
    anti_kick: Any,
    stuck_detector: Any,
    loot_in_progress: Any,
    arduino: Any,
    arduino_last_uptime_ms: int | None,
    event_bus: Any,
    log_fn: Callable[[str], None],
    stop_session: Callable[[], None],
) -> int | None:
    subsystems: list[tuple[str, Any]] = [
        ("healer", healer),
        ("combat", combat),
        ("looter", looter),
        ("death_handler", death_handler),
        ("reconnect", reconnect_handler),
        ("anti_kick", anti_kick),
        ("stuck_det", stuck_detector),
    ]
    for name, subsystem in subsystems:
        if subsystem is None:
            continue
        thread = getattr(subsystem, "_thread", None)
        running = getattr(subsystem, "_running", False)
        if not running or thread is None or thread.is_alive():
            continue
        log_fn(f"[watchdog] ⚠ {name} thread died — restarting")
        try:
            subsystem._running = False
            if name == "looter" and loot_in_progress.is_set():
                loot_in_progress.clear()
                log_fn("[watchdog] cleared stale _loot_in_progress (looter died mid-loot)")
            subsystem.start()
            log_fn(f"[watchdog] ✓ {name} restarted")
        except Exception as exc:
            log_fn(f"[watchdog] ✗ {name} restart failed: {exc}")

    if arduino is None:
        return arduino_last_uptime_ms
    if not arduino.is_available():
        log_fn("[watchdog] ⚠ Arduino HID disconnected — stopping session")
        event_bus.emit("arduino_disconnected", {})
        stop_session()
        return arduino_last_uptime_ms

    status = arduino.send_status()
    if status is None:
        return arduino_last_uptime_ms

    uptime_ms, _ = status
    previous_uptime = arduino_last_uptime_ms or 0
    if previous_uptime > 0 and uptime_ms < previous_uptime:
        log_fn(
            f"[watchdog] ⚠ Arduino rebooted! uptime {uptime_ms}ms < prev {previous_uptime}ms "
            "(possible overheating) — stopping session"
        )
        event_bus.emit("arduino_rebooted", {"uptime_ms": uptime_ms})
        stop_session()
        return arduino_last_uptime_ms

    return uptime_ms


def run_watchdog_loop(
    *,
    timeout: float,
    is_running: Callable[[], bool],
    sleep_fn: Callable[[float], None],
    check_subsystem_health_fn: Callable[[], None],
    get_position: Callable[[], Any],
    get_pos_none_since: Callable[[], float],
    set_pos_none_since: Callable[[float], None],
    get_last_move_time: Callable[[], float],
    set_last_move_time: Callable[[float], None],
    event_bus: Any,
    log_fn: Callable[[str], None],
    monotonic_fn: Callable[[], float],
) -> None:
    while is_running():
        sleep_fn(min(timeout / 2.0, 5.0))
        if not is_running():
            break

        check_subsystem_health_fn()

        now_w = monotonic_fn()
        if get_position() is None:
            pos_none_since = get_pos_none_since()
            if pos_none_since == 0.0:
                set_pos_none_since(now_w)
            elif (now_w - pos_none_since) > 30.0:
                log_fn(
                    f"[watchdog] ⚠ Position unreadable for "
                    f"{now_w - pos_none_since:.0f}s — "
                    "radar/OCR pipeline may be broken"
                )
                event_bus.emit("position_lost", {"seconds": round(now_w - pos_none_since, 1)})
                set_pos_none_since(now_w)
        else:
            set_pos_none_since(0.0)

        idle = monotonic_fn() - get_last_move_time()
        if idle >= timeout:
            log_fn(
                f"[watchdog] No movement for {idle:.0f}s "
                f"(threshold {timeout:.0f}s) — possible stuck state."
            )
            event_bus.emit(
                "watchdog",
                {"idle_seconds": round(idle, 1), "threshold": timeout},
            )
            set_last_move_time(monotonic_fn())


def run_window_watchdog_loop(
    *,
    is_running: Callable[[], bool],
    window_handles: dict[str, int],
    log_fn: Callable[[str], None],
    stop_session: Callable[[], None],
    sleep_fn: Callable[[float], None],
    debug_fn: Callable[[], None],
) -> None:
    user32 = ctypes.windll.user32
    restore_flag = 9

    while is_running():
        for label, hwnd in window_handles.items():
            try:
                if not user32.IsWindow(hwnd):
                    log_fn(
                        f"[WW] ⚠ {label} window (hwnd={hwnd:#x}) "
                        f"NO LONGER EXISTS — stopping session!"
                    )
                    stop_session()
                    return
                if user32.IsIconic(hwnd):
                    foreground_hwnd = user32.GetForegroundWindow()
                    foreground_title = ""
                    if foreground_hwnd:
                        title_buffer = ctypes.create_unicode_buffer(256)
                        user32.GetWindowTextW(foreground_hwnd, title_buffer, 256)
                        foreground_title = title_buffer.value[:60]
                    log_fn(
                        f"[WW] {label} MINIMIZED -- restoring "
                        f"(fg={foreground_hwnd:#x} '{foreground_title}')"
                    )
                    user32.ShowWindow(hwnd, restore_flag)
            except Exception:
                debug_fn()
        sleep_fn(0.3)