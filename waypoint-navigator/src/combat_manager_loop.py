from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from .combat_manager import CombatManager


Detection = Tuple[int, int, float, str]
MonsterSlot = Tuple[str, int]
FrameSleep = Callable[[float], Any]


def run_loop(
    manager: "CombatManager",
    *,
    jittered_sleep_fn: FrameSleep,
    verify_target_selected_fn: Any,
    time_module: Any,
    counter_cls: Any,
) -> None:
    # Contract: race-condition prevention for _current_target / _tracked_target.
    # At the START of each tick we take a single snapshot under the lock.
    # All decisions within the tick use this local snapshot.
    # Mutations to shared state happen only at well-defined points (select,
    # kill confirmation, flee) via the lock, and only at the END of the
    # relevant decision.  Never read _current_target / _tracked_target bare
    # (without the lock) inside a tick after the initial snapshot.
    manager._log("  [C] Loop activo — esperando monstruos…")
    frame_fail_streak = 0
    max_frame_fails = 30

    while manager._running:
        try:
            if manager._paused:
                jittered_sleep_fn(0.1)
                continue

            if manager._frame_getter is None:
                jittered_sleep_fn(0.5)
                continue

            frame, frame_fail_streak = _get_frame(
                manager,
                frame_fail_streak=frame_fail_streak,
                max_frame_fails=max_frame_fails,
                jittered_sleep_fn=jittered_sleep_fn,
            )
            if frame is None:
                if not manager._running:
                    break
                continue

            # ── Tick snapshot — single consistent read of shared target state ──
            # Contract: _current_target and _tracked_target are volatile (written
            # by notify_kill, flee, and select branches).  Taking a snapshot here
            # ensures that all routing decisions in this tick use a consistent
            # view of the target state.  Subsequent writes go through the lock.
            with manager._lock:
                tick_current_target = manager._current_target   # noqa: F841
                tick_tracked_target = manager._tracked_target   # noqa: F841
                tick_in_combat = manager._in_combat             # noqa: F841
            # tick_in_combat / tick_current_target / tick_tracked_target are
            # the authoritative values for this tick's routing decisions.
            # Use them when checking state before any mutation in this tick.

            hp_pct = manager._read_hp_pct(frame)
            detections = manager._detector.detect_auto(frame)
            mob_count = len(detections)
            now = time_module.monotonic()

            if mob_count > 0 and manager._check_anti_lure(mob_count):
                if manager._cfg.lure_action == "flee":
                    manager._log("  [C] ⚠ ANTI-LURE: flee activado")
                    if manager._cfg.flee_vk:
                        manager._ctrl.press_key(manager._cfg.flee_vk)
                    with manager._lock:
                        manager._in_combat = False
                        manager._current_target = None
                        manager._tracked_target = None
                    jittered_sleep_fn(1.5)
                    continue

            if _should_flee(manager, hp_pct=hp_pct, mob_count=mob_count):
                manager._log(
                    f"  [C] ⚠ HP {hp_pct}% — HUYENDO (mobs={mob_count}, threshold={manager._cfg.hp_flee_pct}%)"
                )
                if manager._cfg.flee_vk:
                    manager._ctrl.press_key(manager._cfg.flee_vk)
                manager._emit("e26", {"hp_pct": hp_pct, "mob_count": mob_count})
                with manager._lock:
                    manager._in_combat = False
                    manager._current_target = None
                    manager._tracked_target = None
                jittered_sleep_fn(1.5)
                continue

            current_names = [detection[3] for detection in detections]
            if detections:
                _refresh_current_target_tracking(manager, detections=detections, now=now)
            if not detections:
                if _handle_empty_detections(
                    manager,
                    jittered_sleep_fn=jittered_sleep_fn,
                    counter_cls=counter_cls,
                ):
                    continue
            else:
                manager._empty_frames_streak = 0

            with manager._lock:
                in_combat = manager._in_combat
            if not in_combat:
                manager._confirm_streak += 1
                if manager._confirm_streak < manager._ENGAGE_CONFIRM_FRAMES:
                    jittered_sleep_fn(manager._cfg.check_interval)
                    continue

            if in_combat and manager._tracked_detection_counts:
                _process_absent_monsters(
                    manager,
                    current_detections=detections,
                    counter_cls=counter_cls,
                )
            elif in_combat:
                seed_names = manager._prev_detection_names or current_names
                manager._tracked_detection_counts = dict(counter_cls(seed_names))
                _process_absent_monsters(
                    manager,
                    current_detections=detections,
                    counter_cls=counter_cls,
                )

            manager._prev_detection_names = current_names
            detections = manager._sort_by_priority(detections)

            _select_target(
                manager,
                detections=detections,
                now=now,
                jittered_sleep_fn=jittered_sleep_fn,
                verify_target_selected_fn=verify_target_selected_fn,
                mob_count=mob_count,
            )
            _send_attack_hotkey(manager, now=now)
            manager._cast_spells(frame, mob_count=mob_count)
            jittered_sleep_fn(manager._cfg.check_interval)
        except Exception as exc:
            manager._log(f"  [C] ⚠ Error en loop: {exc}")
            jittered_sleep_fn(max(manager._cfg.check_interval, 1.0))

    with manager._lock:
        kills, attacks = manager._kills, manager._attacks_sent
    manager._log(f"  [C] Loop terminado — kills={kills} attacks={attacks}")


def _get_frame(
    manager: "CombatManager",
    *,
    frame_fail_streak: int,
    max_frame_fails: int,
    jittered_sleep_fn: FrameSleep,
) -> tuple[np.ndarray | None, int]:
    frame_getter = manager._frame_getter
    if frame_getter is None:
        return None, frame_fail_streak
    try:
        frame = frame_getter()
    except Exception as exc:
        manager._log(f"  [C] ⚠ frame_getter error: {exc}")
        frame_fail_streak += 1
        if frame_fail_streak >= max_frame_fails:
            manager._log(
                f"  [C] ✖ frame_getter falló {max_frame_fails} veces consecutivas — deteniendo combate"
            )
            manager._running = False
            return None, frame_fail_streak
        jittered_sleep_fn(manager._cfg.check_interval)
        return None, frame_fail_streak

    if frame is None:
        frame_fail_streak += 1
        if frame_fail_streak >= max_frame_fails:
            manager._log(
                f"  [C] ✖ frame_getter devolvió None {max_frame_fails} veces — deteniendo combate"
            )
            manager._running = False
            return None, frame_fail_streak
        jittered_sleep_fn(manager._cfg.check_interval)
        return None, frame_fail_streak

    return frame, 0


def _should_flee(manager: "CombatManager", *, hp_pct: int | None, mob_count: int) -> bool:
    if manager._cfg.hp_flee_pct > 0 and hp_pct is not None and hp_pct < manager._cfg.hp_flee_pct:
        return True
    return (
        manager._cfg.flee_mob_count > 0
        and mob_count >= manager._cfg.flee_mob_count
        and manager._cfg.hp_flee_pct > 0
        and hp_pct is not None
        and hp_pct < manager._cfg.hp_flee_pct + 20
    )


def _handle_empty_detections(
    manager: "CombatManager",
    *,
    jittered_sleep_fn: FrameSleep,
    counter_cls: Any,
) -> bool:
    manager._empty_frames_streak += 1
    manager._confirm_streak = 0
    with manager._lock:
        in_combat = manager._in_combat
    if not in_combat:
        manager._prev_detection_names = []
        manager._tracked_detection_counts = {}
        manager._absence_counter.clear()
        jittered_sleep_fn(manager._cfg.check_interval)
        return True
    if in_combat and manager._empty_frames_streak >= manager._absence_frames_required:
        tracked_counts = dict(manager._tracked_detection_counts)
        if not tracked_counts and manager._prev_detection_names:
            tracked_counts = dict(counter_cls(manager._prev_detection_names))
        killed_names = _expand_tracked_names(tracked_counts)
        if not killed_names:
            killed_names = [""]
        tracked_target = None
        with manager._lock:
            if not manager._in_combat:
                killed_names = []
            else:
                tracked_target = manager._tracked_target
                manager._in_combat = False
                manager._current_target = None
                manager._tracked_target = None
                if tracked_target is not None:
                    manager._last_target_result = manager._build_target_result(
                        tracked_target=tracked_target,
                        name=tracked_target.name,
                        reason="battle_list_empty",
                    )
        if not killed_names:
            manager._prev_detection_names = []
            manager._tracked_detection_counts = {}
            manager._absence_counter.clear()
            jittered_sleep_fn(manager._cfg.check_interval)
            return True
        if tracked_target is not None:
            manager._log(
                f"  [C] ✓ Target terminado: {tracked_target.name or 'unknown'} (battle_list_empty)"
            )
        for dead_name in killed_names:
            _confirm_kill(
                manager,
                dead_name,
                reason="battle_list_empty",
                current_detections=[],
            )
        manager._prev_detection_names = []
        manager._tracked_detection_counts = {}
        manager._absence_counter.clear()
    jittered_sleep_fn(manager._cfg.check_interval)
    return True


def _process_absent_monsters(
    manager: "CombatManager",
    *,
    current_detections: List[Detection],
    counter_cls: Any,
) -> None:
    current_names = [detection[3] for detection in current_detections]
    current_counter = counter_cls(current_names)
    tracked_counts = dict(manager._tracked_detection_counts)
    if not tracked_counts:
        manager._tracked_detection_counts = dict(current_counter)
        return

    confirmed_names: List[str] = []
    next_tracked_counts: Dict[str, int] = dict(tracked_counts)

    for name, tracked_count in tracked_counts.items():
        current_count = current_counter.get(name, 0)
        if current_count >= tracked_count:
            next_tracked_counts[name] = current_count
            _clear_absence_slots(manager, name=name)
            continue

        missing_count = tracked_count - current_count
        confirmed_for_name = 0
        for slot_index in range(missing_count):
            slot = (name, slot_index)
            streak = manager._absence_counter.get(slot, 0) + 1
            if streak >= manager._absence_frames_required:
                manager._absence_counter.pop(slot, None)
                confirmed_for_name += 1
            else:
                manager._absence_counter[slot] = streak

        _clear_absence_slots(manager, name=name, keep_slots=missing_count)

        remaining_missing = missing_count - confirmed_for_name
        remaining_visible = current_count + remaining_missing
        if remaining_visible > 0:
            next_tracked_counts[name] = remaining_visible
        else:
            next_tracked_counts.pop(name, None)

        confirmed_names.extend([name] * confirmed_for_name)

    for name, current_count in current_counter.items():
        tracked_count = tracked_counts.get(name, 0)
        if current_count > tracked_count:
            next_tracked_counts[name] = current_count
            _clear_absence_slots(manager, name=name)

    manager._tracked_detection_counts = {
        name: count for name, count in next_tracked_counts.items() if count > 0
    }

    for confirmed_name in confirmed_names:
        _confirm_kill(
            manager,
            confirmed_name,
            reason="absence_confirmed",
            current_detections=current_detections,
        )


def _confirm_kill(
    manager: "CombatManager",
    name: str,
    *,
    reason: str,
    current_detections: List[Detection],
) -> None:
    _mark_current_target_finished_if_needed(
        manager,
        killed_name=name,
        reason=reason,
        current_detections=current_detections,
    )
    with manager._lock:
        manager._kills += 1
        total_kills = manager._kills
    manager._log(f"  [C] ☠ Kill confirmado: {name or 'unknown'} (total={total_kills})")
    manager._emit("e1", {"name": name})
    if manager.on_kill is not None:
        try:
            manager.on_kill()
        except Exception as exc:
            manager._log(f"  [C] on_kill callback failed: {exc}")


def _clear_absence_slots(
    manager: "CombatManager",
    *,
    name: str,
    keep_slots: int = 0,
) -> None:
    stale_slots: List[MonsterSlot] = [
        slot
        for slot in list(manager._absence_counter)
        if slot[0] == name and slot[1] >= keep_slots
    ]
    for slot in stale_slots:
        manager._absence_counter.pop(slot, None)


def _expand_tracked_names(tracked_counts: Dict[str, int]) -> List[str]:
    names: List[str] = []
    for name, count in tracked_counts.items():
        names.extend([name] * max(0, count))
    return names


def _refresh_current_target_tracking(
    manager: "CombatManager",
    *,
    detections: List[Detection],
    now: float,
) -> None:
    with manager._lock:
        tracked_target = manager._tracked_target
    if tracked_target is None:
        return

    match = _find_named_detection_near_y(
        manager,
        detections=detections,
        name=tracked_target.name,
        target_y=tracked_target.position[1],
    )
    if match is None:
        return

    match_x, match_y, _, _ = match
    with manager._lock:
        active_target = manager._tracked_target
        if active_target is None or active_target.name != tracked_target.name:
            return
        active_target.position = (match_x, match_y)
        active_target.last_seen_at = now
        manager._current_target = (match_x, match_y)


def _mark_current_target_finished_if_needed(
    manager: "CombatManager",
    *,
    killed_name: str,
    reason: str,
    current_detections: List[Detection],
) -> None:
    with manager._lock:
        tracked_target = manager._tracked_target
    if tracked_target is None or tracked_target.name != killed_name:
        return
    if _find_named_detection_near_y(
        manager,
        detections=current_detections,
        name=tracked_target.name,
        target_y=tracked_target.position[1],
    ) is not None:
        return

    target_result = manager._build_target_result(
        tracked_target=tracked_target,
        name=tracked_target.name,
        reason=reason,
    )
    with manager._lock:
        active_target = manager._tracked_target
        if active_target is None or active_target.name != tracked_target.name:
            return
        manager._in_combat = False
        manager._current_target = None
        manager._tracked_target = None
        manager._last_target_result = target_result
    manager._log(
        f"  [C] ✓ Target terminado: {target_result['name'] or 'unknown'} ({reason})"
    )


def _find_named_detection_near_y(
    manager: "CombatManager",
    *,
    detections: List[Detection],
    name: str,
    target_y: int,
) -> Optional[Detection]:
    same_name = [detection for detection in detections if detection[3] == name]
    if not same_name:
        return None

    best_match = min(same_name, key=lambda detection: abs(detection[1] - target_y))
    tolerance = max(manager._cfg.slot_height * 2, 6)
    if abs(best_match[1] - target_y) > tolerance:
        return None
    return best_match


def _select_target(
    manager: "CombatManager",
    *,
    detections: List[Detection],
    now: float,
    jittered_sleep_fn: FrameSleep,
    verify_target_selected_fn: Any,
    mob_count: int,
) -> None:
    from .combat_manager import TrackedCombatTarget

    best_x, best_y, confidence, name = detections[0]
    with manager._lock:
        current_target = manager._current_target
        last_target_time = manager._last_target_time
    need_click = (
        current_target is None
        or current_target != (best_x, best_y)
        or (now - last_target_time) > manager._cfg.reselect_interval
    )
    if not need_click:
        return

    if not manager._ctrl.click(best_x, best_y, button="left"):
        return

    with manager._lock:
        manager._current_target = (best_x, best_y)
        manager._tracked_target = TrackedCombatTarget(
            name=name,
            position=(best_x, best_y),
            acquired_at=now,
            last_seen_at=now,
        )
        manager._in_combat = True
        manager._last_target_time = now
    manager._log(
        f"  [C] 🎯 Target: {name} @ ({best_x},{best_y}) conf={confidence:.2f} [{mob_count} mobs]"
    )
    jittered_sleep_fn(manager._cfg.click_to_attack_delay)

    if manager._verify_attacks and verify_target_selected_fn is not None:
        if verify_target_selected_fn(manager, timeout=1.5):
            return
        with manager._lock:
            manager._target_verify_fails += 1
            verify_fails = manager._target_verify_fails
        manager._log(
            f"  [C] ⚠ target verify: click did not select target (fails={verify_fails})"
        )
        manager._ctrl.click(best_x, best_y, button="left")
        jittered_sleep_fn(manager._cfg.click_to_attack_delay)


def _send_attack_hotkey(manager: "CombatManager", *, now: float) -> None:
    if not manager._cfg.attack_vk or not manager._ctrl.is_connected():
        return
    with manager._lock:
        attack_elapsed = now - manager._last_attack_vk_time
    if attack_elapsed < manager._cfg.reselect_interval:
        return
    manager._ctrl.press_key(manager._cfg.attack_vk)
    with manager._lock:
        manager._attacks_sent += 1
        manager._last_attack_vk_time = now