from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ThreadStartupState:
    ww_hwnds: dict[str, int] = field(default_factory=dict)
    ww_thread: Any = None
    watchdog_thread: Any = None
    main_thread: Any = None
    last_move_time: float = 0.0


def start_session_threads(
    *,
    config: Any,
    raw_ctrl: Any,
    arduino: Any,
    log_fn: Callable[[str], None],
    window_watchdog_target: Callable[[], None],
    watchdog_target: Callable[[], None],
    run_loop_target: Callable[[], None],
    wait_for_start_position_lock: Callable[[], bool],
    thread_factory: Callable[..., Any],
    monotonic_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
    random_uniform_fn: Callable[[float, float], float],
    find_window_fn: Callable[[str], Any],
) -> ThreadStartupState:
    state = ThreadStartupState()

    if arduino is not None and raw_ctrl is not None:
        raw_ctrl.set_arduino_failover(arduino)
        raw_ctrl._using_arduino_failover = True
        log_fn("[Arduino] HID configurado como input PRIMARY — Interception bypassed.")

    if raw_ctrl and raw_ctrl.is_connected():
        hwnd = raw_ctrl.hwnd
        if isinstance(hwnd, int) and hwnd:
            state.ww_hwnds["Tibia"] = hwnd

    frame_window = config.frame_window.strip()
    if frame_window:
        window_info = find_window_fn(frame_window)
        if window_info and isinstance(window_info.hwnd, int) and window_info.hwnd:
            state.ww_hwnds[frame_window] = window_info.hwnd

    if state.ww_hwnds and not config.dry_run:
        state.ww_thread = thread_factory(target=window_watchdog_target, daemon=True)
        state.ww_thread.start()
        labels = ", ".join(f"{key}={value:#x}" for key, value in state.ww_hwnds.items())
        log_fn(f"[WW] Window watchdog active: {labels}")

    if config.watchdog_timeout > 0:
        state.last_move_time = monotonic_fn()
        state.watchdog_thread = thread_factory(target=watchdog_target, daemon=True)
        state.watchdog_thread.start()
        log_fn(f"Watchdog enabled — timeout {config.watchdog_timeout:.0f}s.")

    if config.start_delay > 0:
        log_fn(f"Waiting {config.start_delay:.1f}s before first move …")
        sleep_fn(config.start_delay * random_uniform_fn(0.85, 1.2))

    if not wait_for_start_position_lock():
        raise RuntimeError(
            "Start position lock failed — current minimap position is too far from start_pos."
        )

    state.main_thread = thread_factory(target=run_loop_target, daemon=True)
    state.main_thread.start()
    log_fn("Session started.")
    return state