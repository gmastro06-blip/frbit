from __future__ import annotations

import threading
from typing import Any, Callable, Iterable


def _stop_component(
    *,
    name: str,
    component: Any,
    log_fn: Callable[[str], None],
) -> None:
    if component is None:
        return
    try:
        component.stop()
    except Exception as exc:
        log_fn(f"  ⚠ Error stopping {name}: {exc}")


def _close_optional(
    *,
    close_fn: Callable[[], object] | None,
    label: str,
    log_fn: Callable[[str], None],
) -> None:
    if close_fn is None:
        return
    try:
        close_fn()
    except Exception as exc:
        log_fn(f"  ⚠ Error closing {label}: {exc}")


def _join_optional_thread(
    *,
    thread: Any,
    current_thread: threading.Thread,
    timeout: float,
) -> None:
    if thread is None or thread is current_thread:
        return
    thread.join(timeout=timeout)


def perform_session_shutdown(
    *,
    executor: Any,
    stoppable_components: Iterable[tuple[str, Any]],
    ctrl: Any,
    pico: Any,
    main_thread: Any,
    watchdog_thread: Any,
    window_watchdog_thread: Any,
    stats: dict[str, Any],
    session_stats: Any,
    path_viz: Any,
    log_fn: Callable[[str], None],
    save_stats_fn: Callable[[], None],
    save_checkpoint_fn: Callable[[], None],
    current_thread_fn: Callable[[], threading.Thread] = threading.current_thread,
) -> None:
    if executor is not None:
        executor.abort()

    for name, component in stoppable_components:
        _stop_component(name=name, component=component, log_fn=log_fn)

    ctrl_close = getattr(ctrl, "close", None)
    if callable(ctrl_close):
        _close_optional(close_fn=ctrl_close, label="HIS", log_fn=log_fn)

    if pico is not None:
        _close_optional(close_fn=pico.close, label="Pico 2", log_fn=log_fn)

    current_thread = current_thread_fn()
    _join_optional_thread(thread=main_thread, current_thread=current_thread, timeout=10)
    _join_optional_thread(thread=watchdog_thread, current_thread=current_thread, timeout=3)
    _join_optional_thread(thread=window_watchdog_thread, current_thread=current_thread, timeout=2)

    log_fn(
        f"Session stopped. Stats: routes={stats['routes_completed']}  "
        f"heals={stats['heal_fired']}  mana={stats['mana_fired']}"
    )
    if session_stats is not None:
        log_fn(f"Hunting stats: {session_stats.summary_text()}")

    if path_viz is not None:
        try:
            path_viz.save_cumulative()
            log_fn("[S] PathVisualizer cumulative image saved.")
        except Exception as exc:
            log_fn(f"  ⚠ PathVisualizer save error: {exc}")

    save_stats_fn()
    save_checkpoint_fn()