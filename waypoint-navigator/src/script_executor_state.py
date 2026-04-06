from __future__ import annotations

import datetime
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .models import BOUNDS

_log = logging.getLogger("wn.se")


def estimate_game_viewport_bounds(*, executor: Any, frame: Any = None) -> tuple[int, int, int, int]:
    """Estimate the visible game viewport bounds within a captured frame."""
    frame_w, frame_h = 1920, 1009
    if getattr(frame, "size", 0):
        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])

    game_right = max(1, min(frame_w, int(frame_w * 0.817)))
    game_bottom = max(1, min(frame_h, int(frame_h * 0.83)))
    return 0, 0, game_right, game_bottom


def block_diagnostic_dir(*, executor: Any) -> Path:
    if executor._path_viz is not None and hasattr(executor._path_viz, "_out"):
        out_dir = Path(getattr(executor._path_viz, "_out"))
        return out_dir.parent.parent / "block_diagnostics" / out_dir.name
    return Path(__file__).resolve().parent.parent / "output" / "block_diagnostics" / "run_manual"


def save_block_diagnostic(
    *,
    executor: Any,
    blocked_tile: Any,
    actual_pos: Any,
    dest: Any,
    step_index: int,
    total_steps: int,
) -> Optional[Path]:
    if blocked_tile is None:
        return None

    try:
        import cv2

        diag_dir = executor._block_diagnostic_dir()
        diag_dir.mkdir(parents=True, exist_ok=True)
        stem = (
            f"seg_{executor._walk_segment_counter:04d}_"
            f"step_{step_index:02d}_"
            f"block_{blocked_tile.x}_{blocked_tile.y}_{blocked_tile.z}"
        )
        meta_path = diag_dir / f"{stem}_meta.json"

        metadata: Dict[str, Any] = {
            "segment_id": executor._walk_segment_counter,
            "step_index": step_index,
            "total_steps": total_steps,
            "blocked_tile": [blocked_tile.x, blocked_tile.y, blocked_tile.z],
            "actual_pos": None,
            "dest": None,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        if actual_pos is not None:
            metadata["actual_pos"] = [actual_pos.x, actual_pos.y, actual_pos.z]
        if dest is not None:
            metadata["dest"] = [dest.x, dest.y, dest.z]

        frame = None
        if executor._frame_getter is not None:
            try:
                frame = executor._frame_getter()
            except Exception as exc:
                metadata["frame_error"] = str(exc)

        frame_img = frame if getattr(frame, "size", 0) else None
        if frame_img is not None:
            frame_path = diag_dir / f"{stem}_frame.png"
            cv2.imwrite(str(frame_path), frame_img)
            metadata["frame_path"] = str(frame_path)
            metadata["frame_size"] = [int(frame_img.shape[1]), int(frame_img.shape[0])]

            vx0, vy0, vx1, vy1 = executor._estimate_game_viewport_bounds(frame_img)
            viewport = frame_img[vy0:vy1, vx0:vx1]
            viewport_path = diag_dir / f"{stem}_viewport.png"
            cv2.imwrite(str(viewport_path), viewport)
            metadata["viewport_path"] = str(viewport_path)
            metadata["viewport_bounds"] = [vx0, vy0, vx1, vy1]
            metadata["viewport_size"] = [max(0, vx1 - vx0), max(0, vy1 - vy0)]

            if executor._radar is not None and hasattr(executor._radar, "_crop_minimap"):
                try:
                    minimap = executor._radar._crop_minimap(frame_img)
                    if getattr(minimap, "size", 0):
                        minimap_path = diag_dir / f"{stem}_minimap.png"
                        cv2.imwrite(str(minimap_path), minimap)
                        metadata["minimap_path"] = str(minimap_path)
                        metadata["minimap_size"] = [int(minimap.shape[1]), int(minimap.shape[0])]
                except Exception as exc:
                    metadata["minimap_error"] = str(exc)

        if executor._map_loader is not None:
            try:
                rgba = executor._map_loader.get_map_image(blocked_tile.z)
                px = blocked_tile.x - BOUNDS["xMin"]
                py = blocked_tile.y - BOUNDS["yMin"]
                radius = 12
                x0 = max(0, px - radius)
                y0 = max(0, py - radius)
                x1 = min(rgba.shape[1], px + radius + 1)
                y1 = min(rgba.shape[0], py + radius + 1)
                crop = rgba[y0:y1, x0:x1]
                if getattr(crop, "size", 0):
                    map_img = cv2.cvtColor(crop, cv2.COLOR_RGBA2BGR) if crop.shape[2] == 4 else crop.copy()
                    map_img = cv2.resize(
                        map_img,
                        (map_img.shape[1] * 12, map_img.shape[0] * 12),
                        interpolation=cv2.INTER_NEAREST,
                    )
                    bx = (px - x0) * 12 + 6
                    by = (py - y0) * 12 + 6
                    cv2.drawMarker(map_img, (bx, by), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 18, 2)
                    if actual_pos is not None:
                        ax = (actual_pos.x - BOUNDS["xMin"] - x0) * 12 + 6
                        ay = (actual_pos.y - BOUNDS["yMin"] - y0) * 12 + 6
                        if 0 <= ax < map_img.shape[1] and 0 <= ay < map_img.shape[0]:
                            cv2.drawMarker(map_img, (ax, ay), (255, 255, 0), cv2.MARKER_CROSS, 18, 2)
                    map_path = diag_dir / f"{stem}_map.png"
                    cv2.imwrite(str(map_path), map_img)
                    metadata["map_path"] = str(map_path)
            except Exception as exc:
                metadata["map_error"] = str(exc)

        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        executor._log(f"[walk] block diagnostic saved: {meta_path}")
        return meta_path
    except Exception as exc:
        executor._log(f"[walk] block diagnostic save failed: {exc}")
        return None


def request_replan(*, executor: Any) -> bool:
    executor._replan_requested = True
    return True


def arm_post_block_position_watch(*, executor: Any) -> None:
    executor._watch_post_block_position_loss = True
    executor._post_block_position_miss_streak = 0


def note_post_block_position_result(*, executor: Any, has_fresh_position: bool) -> bool:
    if not executor._watch_post_block_position_loss:
        return False

    if has_fresh_position:
        executor._watch_post_block_position_loss = False
        executor._post_block_position_miss_streak = 0
        return False

    executor._post_block_position_miss_streak += 1
    if executor._post_block_position_miss_streak < executor._MAX_POST_BLOCK_POSITION_MISSES:
        return False

    executor._stop_reason = "resolver_degraded"
    executor._running = False
    executor._resume_instruction_index = None
    executor._log(
        "[X] ✖ sustained position loss after blockage — "
        f"{executor._post_block_position_miss_streak} consecutive fresh-position misses"
    )
    return True


def current_wp_position(*, executor: Any, waypoint_position_cls: Any) -> Optional[Any]:
    if executor._current_pos is None or waypoint_position_cls is None:
        return None
    try:
        return waypoint_position_cls(
            executor._current_pos.x,
            executor._current_pos.y,
            executor._current_pos.z,
        )
    except Exception:
        _log.debug("Failed to build waypoint logger position", exc_info=True)
        return None


def record_wp_action(
    *,
    executor: Any,
    action: str,
    description: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    if executor._wp_logger is None:
        return
    try:
        executor._wp_logger.record_action(
            action,
            description,
            position=executor._current_wp_position(),
            meta=meta,
        )
    except Exception:
        _log.debug(
            "Waypoint logger record_action failed for %s",
            action,
            exc_info=True,
        )


def add_wp_waypoint(*, executor: Any, pos: Any, action: str) -> None:
    if executor._wp_logger is None or pos is None:
        return
    try:
        executor._wp_logger.add_waypoint(pos.x, pos.y, pos.z, action=action)
    except Exception:
        _log.debug(
            "Waypoint logger add_waypoint failed for %s",
            action,
            exc_info=True,
        )


def click_character_tile(*, executor: Any) -> None:
    if executor._dry_run:
        return
    frame = None
    if executor._frame_getter is not None:
        try:
            frame = executor._frame_getter()
        except Exception as exc:
            executor._log(f"[X] ⚠ frame_getter error in click_character: {exc}")
    vx0, vy0, vx1, vy1 = executor._estimate_game_viewport_bounds(frame)
    cx = vx0 + ((vx1 - vx0) // 2)
    cy = vy0 + ((vy1 - vy0) // 2)
    executor._log(f"[X] click character tile ({cx},{cy})")
    executor._ctrl.click(cx, cy, button="left")


def sync_position(*, executor: Any, logger: logging.Logger) -> None:
    if executor._position_getter is None:
        return
    pos = executor._position_getter()
    if pos is None:
        return
    old = executor._current_pos
    if old is not None and pos != old:
        dx = abs(pos.x - old.x)
        dy = abs(pos.y - old.y)
        if dx > executor._MAX_SYNC_JUMP or dy > executor._MAX_SYNC_JUMP:
            logger.warning(
                "[sync] REJECTED radar jump: (%d,%d)->(%d,%d) d=(%d,%d) — keeping dead-reckoning",
                old.x, old.y, pos.x, pos.y, dx, dy,
            )
            return
        if dx > 2 or dy > 2:
            logger.info(
                "[sync] drift: (%d,%d)->(%d,%d) d=(%d,%d)",
                old.x, old.y, pos.x, pos.y, dx, dy,
            )
    executor._current_pos = pos


def get_pathfinder(*, executor: Any, floor: int) -> Any:
    if executor._nav is None:
        return None
    pathfinders = getattr(executor._nav, "_pathfinders", None)
    if pathfinders is None:
        return None
    return pathfinders.get(floor)


def find_nearest_walkable(
    *,
    executor: Any,
    x: int,
    y: int,
    z: int,
    radius: int = 3,
) -> Optional[Any]:
    loader = getattr(executor._nav, "loader", None) if executor._nav is not None else None
    if loader is None:
        return None
    try:
        arr = loader.get_walkability(z)
    except Exception as exc:
        logging.getLogger("wn").debug(
            "get_walkability failed (floor %s, ignorado): %s",
            z,
            exc,
        )
        return None
    from .models import Coordinate

    arr_h, arr_w = arr.shape
    x_min = BOUNDS["xMin"]
    y_min = BOUNDS["yMin"]
    for current_radius in range(radius + 1):
        for dy in range(-current_radius, current_radius + 1):
            for dx in range(-current_radius, current_radius + 1):
                if abs(dx) != current_radius and abs(dy) != current_radius:
                    continue
                nx, ny = x + dx, y + dy
                px, py = nx - x_min, ny - y_min
                if 0 <= px < arr_w and 0 <= py < arr_h and arr[py, px]:
                    return Coordinate(nx, ny, z)
    return None


def is_leave_time(*, executor: Any, now_fn: Callable[[], Any] | None = None) -> bool:
    if getattr(executor, "_leave_time_fired", False):
        return False
    now = now_fn() if now_fn is not None else datetime.datetime.now()
    now_h = now.hour + now.minute / 60.0
    start_h = getattr(executor, "_start_time_h", None)
    if start_h is None:
        executor._start_time_h = now_h
        start_h = now_h

    def _in_window(hour: float) -> bool:
        if start_h <= now_h:
            return start_h < hour <= now_h
        return hour > start_h or hour <= now_h

    triggered = any(_in_window(hour) for hour in executor._hours_leave)
    if triggered:
        executor._leave_time_fired = True
    return triggered


def read_stat(*, executor: Any, stat_name: str) -> Optional[int]:
    if executor._healer is None:
        return None
    stat = stat_name.lower()
    if stat == "hp":
        value = getattr(executor._healer, "hp_pct", None)
        if value is None:
            value = getattr(executor._healer, "_hp_pct", None)
        return int(value) if value is not None else None
    if stat == "mp":
        value = getattr(executor._healer, "mp_pct", None)
        if value is None:
            value = getattr(executor._healer, "_mp_pct", None)
        return int(value) if value is not None else None
    return None


def sleep_interruptible(
    *,
    executor: Any,
    secs: float,
    uniform_fn: Callable[[float, float], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> None:
    total = secs
    chosen_uniform = uniform_fn or random.uniform
    chosen_sleep = sleep_fn or time.sleep
    if executor._jitter > 0:
        total += chosen_uniform(0.0, executor._jitter)
    if total <= 0:
        return
    chunk = executor._SLEEP_CHUNK
    n_full, remainder = divmod(total, chunk)
    for _ in range(int(n_full)):
        chosen_sleep(chunk)
        if not executor._running:
            return
    if remainder > 1e-9:
        chosen_sleep(remainder)