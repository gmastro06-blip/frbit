from __future__ import annotations

import logging
import math
from typing import Any


def walk_to(*, executor: Any, dest: Any, kind: str, logger: logging.Logger) -> None:
    executor._last_walk_ok = True

    if executor._dry_run:
        executor._log(f"[X] [dry] walk {kind} → {dest}")
        executor._current_pos = dest
        executor._add_wp_waypoint(dest, kind)
        return

    if executor._nav is None:
        executor._log(f"[X] ⚠  no navigator — skipping walk to {dest}")
        executor._last_walk_ok = False
        return

    if executor._stuck is not None:
        try:
            executor._stuck.set_walking(True)
        except Exception as exc:
            executor._log(f"[X] ⚠ stuck.set_walking(True) error: {exc}")

    executor._sync_position()
    if executor._current_pos is None:
        executor._log(
            f"[X] ⚠  position unknown — bootstrapping to {dest} "
            "(place character at this tile before the script starts)"
        )
        executor._current_pos = dest

    blocked_pixels = executor._blocked_pixels

    executor._sync_position()
    executor._replan_requested = False

    if (
        executor._obstacle_analyzer is not None
        and executor._frame_getter is not None
        and executor._current_pos is not None
    ):
        try:
            frame = executor._frame_getter()
            if frame is not None:
                from .models import BOUNDS

                blocked_coords = executor._obstacle_analyzer.get_blocked_coords(
                    frame,
                    executor._current_pos,
                    executor._current_pos.z,
                )
                for blocked_coord in blocked_coords:
                    if executor._obstacle_analyzer.confirm_blocked(blocked_coord):
                        blocked_x = blocked_coord.x - BOUNDS["xMin"]
                        blocked_y = blocked_coord.y - BOUNDS["yMin"]
                        entry = (blocked_x, blocked_y, blocked_coord.z)
                        executor._remember_blocked_pixel(entry)
        except Exception as exc:
            executor._log(f"[X] obstacle scan error: {exc}")

    segments: list[Any] = []
    current_pos = executor._current_pos
    if current_pos is not None and not (
        current_pos.x == dest.x
        and current_pos.y == dest.y
        and current_pos.z == dest.z
    ):
        patched: list[tuple[int, int, int]] = []
        try:
            for blocked_x, blocked_y, blocked_z in blocked_pixels:
                pathfinder = executor._get_pathfinder(blocked_z)
                if pathfinder is None:
                    continue
                if getattr(pathfinder.walkability, "ndim", 0) != 2:
                    continue
                height, width = pathfinder.walkability.shape
                if (
                    0 <= blocked_x < width
                    and 0 <= blocked_y < height
                    and pathfinder.walkability[blocked_y, blocked_x]
                ):
                    pathfinder.walkability[blocked_y, blocked_x] = False
                    patched.append((blocked_x, blocked_y, blocked_z))

            if current_pos.z != dest.z:
                segments = executor._nav.navigate_multifloor(current_pos, dest) or []
            else:
                logger.info(
                    "[walk] A* from (%d,%d) to (%d,%d)",
                    current_pos.x,
                    current_pos.y,
                    dest.x,
                    dest.y,
                )
                segment = executor._nav.navigate(current_pos, dest)
                if not getattr(segment, "found", False):
                    if len(executor._blocked_pixels) > executor._preblocked_count:
                        executor._trim_dynamic_blocked_pixels()
                        blocked_pixels = executor._blocked_pixels
                        segment = executor._nav.navigate(current_pos, dest)
                if not getattr(segment, "found", False):
                    try:
                        snapped = executor._find_nearest_walkable(
                            current_pos.x,
                            current_pos.y,
                            current_pos.z,
                            radius=5,
                        )
                    except Exception:
                        snapped = None
                    if (
                        snapped is not None
                        and snapped != current_pos
                        and current_pos.manhattan_to(snapped) == 1
                    ):
                        executor._log(
                            f"[X] ⚠  snapping start from {current_pos} → {snapped} "
                            "(nearest walkable)"
                        )
                        executor._current_pos = snapped
                        segment = executor._nav.navigate(snapped, dest)
                segments = [segment]
        finally:
            for restored_x, restored_y, restored_z in patched:
                pathfinder = executor._get_pathfinder(restored_z)
                if pathfinder is not None:
                    pathfinder.walkability[restored_y, restored_x] = True

    if not segments and executor._current_pos != dest:
        executor._log(
            f"[X] ⚠  multifloor path not found "
            f"({executor._current_pos} → {dest}) — walk aborted"
        )
        executor._last_walk_ok = False

    for segment in segments:
        if not getattr(segment, "found", False):
            segment_start = getattr(segment, "start", executor._current_pos)
            segment_end = getattr(segment, "end", dest)
            executor._log(
                f"[X] ⚠  path not found ({segment_start} → {segment_end}) — walk aborted"
            )
            executor._last_walk_ok = False
            break
        if not executor._running:
            break
        steps = getattr(segment, "steps", [])
        logger.info(
            "[walk] segment: %d steps, found=%s",
            len(steps),
            getattr(segment, "found", "?"),
        )

        if executor._path_viz is not None:
            start_xy = (
                (executor._current_pos.x, executor._current_pos.y)
                if executor._current_pos
                else None
            )
            direct_distance = 0
            if executor._current_pos is not None:
                try:
                    direct_distance = executor._current_pos.manhattan_to(dest)
                except Exception:
                    direct_distance = abs(executor._current_pos.x - dest.x) + abs(
                        executor._current_pos.y - dest.y
                    )
            executor._path_viz.begin_segment(
                executor._walk_segment_counter,
                dest=(dest.x, dest.y, dest.z),
                start=start_xy,
            )
            executor._path_viz.set_planned_path(steps)
            executor._path_viz.set_segment_metrics(
                planned_steps=max(0, len(steps) - 1),
                direct_distance=direct_distance,
            )

        if executor._is_segment_path_excessive(steps, dest):
            executor._log(f"[X] ⚠ excessive segment plan to {dest} — aborting before walk")
            if executor._path_viz is not None:
                executor._path_viz.end_segment()
                executor._walk_segment_counter += 1
            executor._last_walk_ok = False
            break

        completed, blocked_tile = executor._execute_segment_steps(steps)

        if executor._path_viz is not None:
            if blocked_tile is not None:
                executor._path_viz.mark_blocked_tile((blocked_tile.x, blocked_tile.y))
            executor._path_viz.end_segment()
            executor._walk_segment_counter += 1
        if not completed:
            if blocked_tile is not None:
                blocked_x, blocked_y = blocked_tile.to_pixel()
                blocked_z = blocked_tile.z
                entry = (blocked_x, blocked_y, blocked_z)
                executor._remember_blocked_pixel(entry)
            executor._log(
                f"[X] ⚠  walk interrupted at {executor._current_pos} "
                f"(dest={dest}) — resume from previous node"
            )
            executor._last_walk_ok = False
            break

    if executor._stuck is not None:
        try:
            executor._stuck.set_walking(False)
        except Exception as exc:
            executor._log(f"[X] ⚠ stuck.set_walking(False) error: {exc}")

    executor._sync_position()
    if executor._current_pos is None:
        executor._current_pos = dest
    elif (
        executor._current_pos.x == dest.x
        and executor._current_pos.y == dest.y
        and executor._current_pos.z == dest.z
    ):
        executor._current_pos = dest


def is_segment_path_excessive(*, executor: Any, steps: list[Any], dest: Any) -> bool:
    planned_steps = max(0, len(steps) - 1)
    if planned_steps <= 1 or executor._current_pos is None:
        return False

    try:
        direct_distance = executor._current_pos.manhattan_to(dest)
    except Exception:
        direct_distance = abs(executor._current_pos.x - dest.x) + abs(executor._current_pos.y - dest.y)

    if direct_distance <= 0:
        return False

    max_allowed = max(
        direct_distance + executor._MAX_SEGMENT_STRETCH_BUFFER,
        math.ceil(direct_distance * executor._MAX_SEGMENT_STRETCH_RATIO),
    )
    if planned_steps <= max_allowed:
        return False

    stretch_ratio = planned_steps / max(1, direct_distance)
    executor._log(
        "[walk] plan inflation: "
        f"planned={planned_steps} direct={direct_distance} "
        f"stretch={stretch_ratio:.1f}x max_allowed={max_allowed}"
    )
    return True


def execute_segment_steps(*, executor: Any, steps: list[Any], logger: logging.Logger) -> tuple[bool, Any]:
    blocked_retries = executor._BLOCKED_RETRIES
    total_steps = len(steps) - 1
    blind_streak = 0

    for index in range(1, len(steps)):
        if not executor._running:
            return True, None

        if executor._replan_requested:
            executor._replan_requested = False
            executor._log(f"[walk] step {index}/{total_steps}: resume requested by StuckDetector")
            return False, None

        if index % executor._RESYNC_EVERY == 0:
            executor._sync_position()

        previous = steps[index - 1]
        current = steps[index]
        delta_x = current.x - previous.x
        delta_y = current.y - previous.y

        if abs(delta_x) + abs(delta_y) != 1:
            executor._log(
                f"[walk] step {index}/{total_steps}: invalid planned step {previous}→{current} "
                f"d=({delta_x},{delta_y}) — aborting segment"
            )
            return False, current

        actual = executor._current_pos
        if actual is not None:
            if actual.x == current.x and actual.y == current.y:
                executor._log(f"[walk] step {index}/{total_steps}: skip (already at {current})")
                continue

            drift = abs(actual.x - previous.x) + abs(actual.y - previous.y)
            if drift > executor._PATH_ALIGNMENT_THRESHOLD:
                executor._log(
                    f"[walk] step {index}/{total_steps}: drift {drift} "
                    f"(actual={actual}, expected near {previous}) — aborting segment"
                )
                logger.warning(
                    "[walk] step %d/%d: ABORT drift=%d actual=(%d,%d) expected=(%d,%d)",
                    index,
                    total_steps,
                    drift,
                    actual.x,
                    actual.y,
                    previous.x,
                    previous.y,
                )
                return False, None

        direction_label = {
            (0, -1): "N/UP",
            (0, 1): "S/DOWN",
            (-1, 0): "W/LEFT",
            (1, 0): "E/RIGHT",
        }.get((delta_x, delta_y), f"?({delta_x},{delta_y})")
        logger.info(
            "[walk] step %d/%d: d=(%d,%d) %s  actual=%s",
            index,
            total_steps,
            delta_x,
            delta_y,
            direction_label,
            f"({actual.x},{actual.y})" if actual else "None",
        )
        executor._log(
            f"[walk] step {index}/{total_steps}: {previous}→{current} d=({delta_x},{delta_y}) "
            f"actual={actual}"
        )

        pos_before = executor._current_pos
        moved = False
        radar_confirmed = False
        for attempt in range(1 + blocked_retries):
            executor._ctrl.move_to_tile(delta_x, delta_y)
            executor._sleep(executor._interval)

            if executor._position_getter is not None:
                actual = executor._position_getter()
                if actual is not None and pos_before is not None:
                    jump_x = abs(actual.x - pos_before.x)
                    jump_y = abs(actual.y - pos_before.y)
                    if jump_x > executor._MAX_STEP_JUMP or jump_y > executor._MAX_STEP_JUMP:
                        logger.warning(
                            "[walk] step %d/%d: REJECTED radar (%d,%d)->(%d,%d) d=(%d,%d)",
                            index,
                            total_steps,
                            pos_before.x,
                            pos_before.y,
                            actual.x,
                            actual.y,
                            jump_x,
                            jump_y,
                        )
                        actual = None

                if actual is not None:
                    executor._current_pos = actual
                    radar_confirmed = True
                    actually_moved = (
                        pos_before is None
                        or actual.x != pos_before.x
                        or actual.y != pos_before.y
                    )
                    delta_actual_x = abs(actual.x - current.x)
                    delta_actual_y = abs(actual.y - current.y)
                    on_target = delta_actual_x <= 1 and delta_actual_y <= 1
                    if executor._note_post_block_position_result(True):
                        return False, None

                    if on_target and actually_moved:
                        moved = True
                        break

                    if actually_moved and not on_target:
                        total_drift = delta_actual_x + delta_actual_y
                        if total_drift > executor._STEP_DRIFT_ACCEPT_THRESHOLD:
                            executor._log(
                                f"[walk] step {index}/{total_steps}: drift {total_drift} "
                                f"(expected {current}, actual {actual}) — replanning"
                            )
                            return False, None
                        moved = True
                        break

                    if attempt < blocked_retries:
                        executor._log(
                            f"[walk] step {index}/{total_steps}: blocked try {attempt + 1}/{blocked_retries} "
                            f"(actual={actual}, target={current})"
                        )
                    pos_before = actual
                else:
                    got_reading = False
                    for _ in range(executor._RADAR_RETRIES):
                        executor._sleep(executor._RADAR_RETRY_DELAY)
                        actual = executor._position_getter()
                        if actual is None:
                            continue
                        if pos_before is not None:
                            jump_x = abs(actual.x - pos_before.x)
                            jump_y = abs(actual.y - pos_before.y)
                            if jump_x > executor._MAX_STEP_JUMP or jump_y > executor._MAX_STEP_JUMP:
                                logger.warning(
                                    "[walk] step %d/%d retry: REJECTED radar d=(%d,%d)",
                                    index,
                                    total_steps,
                                    jump_x,
                                    jump_y,
                                )
                                actual = None
                                continue
                        executor._current_pos = actual
                        radar_confirmed = True
                        got_reading = True
                        actually_moved = (
                            pos_before is None
                            or actual.x != pos_before.x
                            or actual.y != pos_before.y
                        )
                        delta_actual_x = abs(actual.x - current.x)
                        delta_actual_y = abs(actual.y - current.y)
                        on_target = delta_actual_x <= 1 and delta_actual_y <= 1
                        if executor._note_post_block_position_result(True):
                            return False, None
                        if on_target and actually_moved:
                            moved = True
                        elif actually_moved and not on_target:
                            total_drift = delta_actual_x + delta_actual_y
                            if total_drift > executor._STEP_DRIFT_ACCEPT_THRESHOLD:
                                executor._log(
                                    f"[walk] step {index}/{total_steps}: drift {total_drift} "
                                    f"(expected {current}, actual {actual}) — aborting segment"
                                )
                                return False, None
                            moved = True
                        break
                    if got_reading:
                        if moved:
                            break
                        if attempt < blocked_retries:
                            executor._log(
                                f"[walk] step {index}/{total_steps}: blocked try {attempt + 1}/{blocked_retries} "
                                f"(actual={actual}, target={current})"
                            )
                        pos_before = actual
                        continue
                    if executor._note_post_block_position_result(False):
                        return False, None
                    moved = True
                    break
            else:
                moved = True
                break

        if not moved and executor._should_retry_transient_block(
            current_pos=executor._current_pos,
            target_pos=current,
        ):
            executor._log(
                f"[walk] step {index}/{total_steps}: transient block suspected at {current} "
                "— waiting for tile to clear"
            )
            moved = executor._retry_transient_block(
                dx=delta_x,
                dy=delta_y,
                curr=current,
                step_index=index,
                total_steps=total_steps,
            )

        if not moved:
            executor._arm_post_block_position_watch()
            executor._save_block_diagnostic(
                blocked_tile=current,
                actual_pos=executor._current_pos,
                dest=steps[-1] if steps else current,
                step_index=index,
                total_steps=total_steps,
            )
            executor._log(
                f"[walk] step {index}/{total_steps}: BLOCKED {1 + blocked_retries} "
                f"attempts (target={current}) — aborting segment"
            )
            return False, current

        if radar_confirmed and executor._map_loader is not None:
            try:
                if not executor._map_loader.is_walkable(current):
                    from .models import BOUNDS

                    opened_x = current.x - BOUNDS["xMin"]
                    opened_y = current.y - BOUNDS["yMin"]
                    entry = (opened_x, opened_y, current.z)
                    if executor._remember_opened_pixel(entry):
                        executor._log(
                            f"[walk] step {index}/{total_steps}: walked through static wall "
                            f"({current.x},{current.y}) — marking open"
                        )
            except Exception:
                logger.debug("Static wall learning update failed", exc_info=True)

        if (
            index % executor._LOOKAHEAD_EVERY == 0
            and executor._obstacle_analyzer is not None
            and executor._frame_getter is not None
            and executor._current_pos is not None
        ):
            try:
                frame = executor._frame_getter()
                if frame is not None:
                    from .models import BOUNDS

                    ahead_set = set()
                    limit = min(index + 1 + executor._LOOKAHEAD_TILES * 2, len(steps))
                    for ahead_index in range(index + 1, limit):
                        step = steps[ahead_index]
                        ahead_set.add((step.x, step.y, step.z))
                    if ahead_set:
                        blocked_coords = executor._obstacle_analyzer.get_blocked_coords(
                            frame,
                            executor._current_pos,
                            executor._current_pos.z,
                        )
                        for blocked_coord in blocked_coords:
                            if (blocked_coord.x, blocked_coord.y, blocked_coord.z) in ahead_set:
                                blocked_x = blocked_coord.x - BOUNDS["xMin"]
                                blocked_y = blocked_coord.y - BOUNDS["yMin"]
                                entry = (blocked_x, blocked_y, blocked_coord.z)
                                executor._remember_blocked_pixel(entry)
                                executor._log(
                                    f"[walk] step {index}/{total_steps}: 🔭 obstacle detected ahead "
                                    f"at ({blocked_coord.x},{blocked_coord.y}) — proactive reroute"
                                )
                                return False, None
            except Exception as exc:
                executor._log(f"[walk] lookahead error: {exc}")

        if radar_confirmed:
            blind_streak = 0
        else:
            blind_streak += 1
            if blind_streak == 1:
                executor._log(
                    f"[walk] step {index}/{total_steps}: ⚠ radar returned None — using dead-reckoning"
                )
            if blind_streak >= executor._MAX_BLIND_STEPS:
                executor._log(
                    f"[walk] step {index}/{total_steps}: ✖ radar blind for {blind_streak} "
                    "consecutive steps — aborting walk (position unreliable)"
                )
                return False, None

        executor._add_wp_waypoint(executor._current_pos or current, "walk")

        if executor._path_viz is not None:
            actual_pos = executor._current_pos or current
            executor._path_viz.record_step(
                planned=(current.x, current.y),
                actual=(actual_pos.x, actual_pos.y),
                radar_ok=radar_confirmed,
                idx=index,
            )

        if not radar_confirmed:
            # Only dead-reckon when no position_getter exists at all.
            # When a getter exists but returned None, keep the last
            # confirmed position instead of blindly assuming arrival —
            # this prevents cumulative drift when the character is blocked.
            if executor._position_getter is None:
                executor._current_pos = current
                if executor._position_setter is not None:
                    executor._position_setter(current)

    return True, None


def should_retry_transient_block(*, executor: Any, current_pos: Any, target_pos: Any) -> bool:
    if executor._position_getter is None or current_pos is None:
        return False
    if getattr(current_pos, "z", None) != getattr(target_pos, "z", None):
        return False
    try:
        return current_pos.manhattan_to(target_pos) == 1
    except Exception:
        return False


def retry_transient_block(
    *,
    executor: Any,
    dx: int,
    dy: int,
    curr: Any,
    step_index: int,
    total_steps: int,
) -> bool:
    if executor._position_getter is None:
        return False

    origin = executor._current_pos
    for extra_attempt in range(1, executor._TRANSIENT_BLOCK_RETRIES + 1):
        executor._sleep(max(executor._interval, executor._TRANSIENT_BLOCK_DELAY))
        executor._ctrl.move_to_tile(dx, dy)
        executor._sleep(executor._interval)

        actual = executor._position_getter()
        if actual is None:
            continue

        executor._current_pos = actual
        delta_x = abs(actual.x - curr.x)
        delta_y = abs(actual.y - curr.y)
        actually_moved = origin is None or actual.x != origin.x or actual.y != origin.y
        if delta_x <= 1 and delta_y <= 1 and actually_moved:
            executor._log(
                f"[walk] step {step_index}/{total_steps}: transient block cleared "
                f"on retry {extra_attempt}/{executor._TRANSIENT_BLOCK_RETRIES}"
            )
            return True

        executor._log(
            f"[walk] step {step_index}/{total_steps}: transient block retry "
            f"{extra_attempt}/{executor._TRANSIENT_BLOCK_RETRIES} "
            f"(actual={actual}, target={curr})"
        )

    return False