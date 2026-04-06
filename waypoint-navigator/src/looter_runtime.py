from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, List, Tuple

import numpy as np

from .client_actions import quick_loot_target, select_context_menu_entry

if TYPE_CHECKING:
    from .looter import Looter, PendingCorpse


def open_corpse(
    looter: "Looter",
    cx: int,
    cy: int,
    *,
    detect_context_menu_fn: Callable[..., Any],
    find_menu_entry_offset_fn: Callable[..., Any],
    jittered_sleep_fn: Callable[[float], Any],
) -> bool:
    result = select_context_menu_entry(
        ctrl=looter._ctrl,
        click_x=cx,
        click_y=cy,
        entry_index=0,
        fallback_offset_y=looter._cfg.context_menu_offset_y,
        frame_getter=looter._frame_getter,
        detect_context_menu_fn=detect_context_menu_fn,
        find_menu_entry_offset_fn=find_menu_entry_offset_fn,
        sleep_fn=jittered_sleep_fn,
        scale_x_offset_fn=looter._scale_x_offset,
        scale_y_offset_fn=looter._scale_y_offset,
    )
    if result.success:
        looter._log(f"  [L] Contenedor abierto en ({cx},{cy}) [{result.method}]")
    return result.success


def quick_loot_corpse(
    looter: "Looter",
    cx: int,
    cy: int,
    *,
    detect_context_menu_fn: Callable[..., Any],
    find_menu_entry_offset_fn: Callable[..., Any],
    jittered_sleep_fn: Callable[[float], Any],
) -> bool:
    result = quick_loot_target(
        ctrl=looter._ctrl,
        click_x=cx,
        click_y=cy,
        use_hotkey=False,
        quick_loot_menu_offset_y=looter._cfg.quick_loot_menu_offset_y,
        frame_getter=looter._frame_getter,
        detect_context_menu_fn=detect_context_menu_fn,
        find_menu_entry_offset_fn=find_menu_entry_offset_fn,
        sleep_fn=jittered_sleep_fn,
        scale_x_offset_fn=looter._scale_x_offset,
        scale_y_offset_fn=looter._scale_y_offset,
    )
    if result.success:
        looter._log(f"  [L] Quick Loot enviado en ({cx},{cy}) [{result.method}]")
    return result.success


def pick_items(
    looter: "Looter",
    frame: np.ndarray,
    *,
    jittered_sleep_fn: Callable[[float], Any],
) -> Tuple[int, List[str]]:
    picked = 0
    picked_names: List[str] = []
    if looter._cfg.loot_mode == "whitelist":
        items = looter._item_det.detect_whitelist(frame)
        for ix, iy, conf, name in items:
            looter._ctrl.shift_click(ix, iy)
            looter._log(f"  [L] ✓ {name} ({ix},{iy}) conf={conf:.2f}")
            picked += 1
            picked_names.append(name)
            jittered_sleep_fn(0.12)
        return picked, picked_names

    slots = looter._item_det.all_slot_positions(frame)
    for ix, iy in slots:
        looter._ctrl.shift_click(ix, iy)
        jittered_sleep_fn(0.10)
        picked += 1
    return picked, picked_names


def verify_loot(
    looter: "Looter",
    frame_before: np.ndarray,
    frame_after: np.ndarray,
    expected_picked: int,
) -> int:
    del frame_before, expected_picked
    if looter._cfg.loot_mode == "whitelist":
        remaining = looter._item_det.detect_whitelist(frame_after)
        return len(remaining)
    slots_after = looter._item_det.all_slot_positions(frame_after)
    return len(slots_after)


def run_loop(
    looter: "Looter",
    *,
    jittered_sleep_fn: Callable[[float], Any],
    time_module: Any,
    random_module: Any,
) -> None:
    looter._log("  [L] Loop activo — esperando cadáveres…")
    while looter._running:
        try:
            if looter._paused:
                jittered_sleep_fn(0.1)
                continue
            jittered_sleep_fn(0.2)

            corpse = _next_pending_corpse(looter, time_module=time_module, random_module=random_module)
            if corpse is None:
                with looter._lock:
                    looter._pending = [pending for pending in looter._pending if not pending.done]
                continue

            if looter._frame_getter is None:
                jittered_sleep_fn(0.5)
                continue

            try:
                frame = looter._frame_getter()
            except Exception as exc:
                looter._log(f"  [L] ⚠ frame_getter error: {exc}")
                corpse.attempts += 1
                if corpse.attempts >= 5:
                    looter._log(
                        f"  [L] ⚠ frame_getter falló 5 veces — descartando cadáver ({corpse.tile_x},{corpse.tile_y})"
                    )
                    corpse.done = True
                jittered_sleep_fn(0.5)
                continue

            if frame is None:
                corpse.attempts += 1
                if corpse.attempts >= 5:
                    looter._log(
                        f"  [L] ⚠ frame None 5 veces — descartando cadáver ({corpse.tile_x},{corpse.tile_y})"
                    )
                    corpse.done = True
                jittered_sleep_fn(0.3)
                continue

            pos = looter._corpse_screen_pos(corpse, frame)
            if pos is None:
                corpse.attempts += 1
                if corpse.attempts >= 5:
                    looter._log(
                        f"  [L] ⚠ No se encontró cadáver ({corpse.tile_x},{corpse.tile_y}) — descartando"
                    )
                    corpse.done = True
                continue

            cx, cy = pos
            src = "coord" if corpse.tile_x is not None else "template"
            looter._log(
                f"  [L] Cadáver en pantalla ({cx},{cy}) [{src}] — tile ({corpse.tile_x},{corpse.tile_y})"
            )
            if looter.on_loot_start:
                looter.on_loot_start()
            try:
                _process_corpse(
                    looter,
                    corpse=corpse,
                    frame=frame,
                    cx=cx,
                    cy=cy,
                    jittered_sleep_fn=jittered_sleep_fn,
                )
            finally:
                if looter.on_loot_finish:
                    looter.on_loot_finish()
        except Exception as exc:
            looter._log(f"  [L] ⚠ Error en loop: {exc}")
            jittered_sleep_fn(1.0)

    looter._log(f"  [L] Loop terminado — looteados={looter._looted} ítems={looter._items_picked}")


def _next_pending_corpse(
    looter: "Looter",
    *,
    time_module: Any,
    random_module: Any,
) -> "PendingCorpse | None":
    with looter._lock:
        for pending in looter._pending:
            if pending.done:
                continue
            if time_module.monotonic() - pending.created_at < looter._cfg.loot_delay * random_module.uniform(0.8, 1.3):
                continue
            return pending
    return None


def _process_corpse(
    looter: "Looter",
    *,
    corpse: "PendingCorpse",
    frame: np.ndarray,
    cx: int,
    cy: int,
    jittered_sleep_fn: Callable[[float], Any],
) -> None:
    if looter._cfg.loot_mode == "quick":
        if looter._cfg.use_hotkey_quick_loot:
            ok = looter._quick_loot_hotkey(cx, cy)
        else:
            ok = looter._quick_loot_corpse(cx, cy)
        if not ok:
            looter._log("  [L] ⚠ Quick Loot falló")
            corpse.attempts += 1
            if corpse.attempts >= 5:
                looter._log(
                    f"  [L] ⚠ Quick Loot: max intentos alcanzados ({corpse.tile_x},{corpse.tile_y}) — descartando"
                )
                corpse.done = True
            return
        with looter._lock:
            looter._looted += 1
        corpse.done = True
        looter._log("  [L] ✓ Quick Loot → cadáver looteado")
        if looter.on_item_looted is not None:
            try:
                looter.on_item_looted("__quick_looted__", 1)
            except Exception as exc:
                looter._log(f"  [L] on_item_looted callback error: {exc}")
        return

    opened = looter._open_corpse(cx, cy)
    if not opened:
        looter._log("  [L] ⚠ No se pudo abrir el cadáver")
        corpse.attempts += 1
        return

    jittered_sleep_fn(looter._cfg.container_settle)
    try:
        frame2 = _safe_get_frame(looter)
        if frame2 is None:
            frame2 = frame
    except Exception as exc:
        looter._log(f"  [L] ⚠ frame_getter (frame2) error: {exc}")
        frame2 = frame

    picked, picked_names = looter._pick_items(frame2)
    if picked > 0:
        jittered_sleep_fn(0.15)
        try:
            frame3 = _safe_get_frame(looter)
        except Exception as exc:
            looter._log(f"  [L] ⚠ frame_getter (frame3) error: {exc}")
            frame3 = None
        if frame3 is not None:
            remaining = looter._verify_loot(frame2, frame3, picked)
            if remaining > 0:
                looter._log(f"  [L] ⚠ Verificación: {remaining} ítems aún visibles — re-picking")
                extra, extra_names = looter._pick_items(frame3)
                picked += extra
                picked_names.extend(extra_names)

    with looter._lock:
        looter._items_picked += picked
        looter._looted += 1
    corpse.done = True
    looter._log(f"  [L] ✓ Cadáver looteado — {picked} ítem(s) recogidos")
    if picked > 0 and looter.on_item_looted is not None:
        try:
            if picked_names:
                for picked_name in picked_names:
                    looter.on_item_looted(picked_name.lower(), 1)
            else:
                looter.on_item_looted("__looted__", picked)
        except Exception as exc:
            looter._log(f"  [L] on_item_looted callback error: {exc}")


def _safe_get_frame(looter: "Looter") -> np.ndarray | None:
    frame_getter = looter._frame_getter
    if frame_getter is None:
        return None
    return frame_getter()