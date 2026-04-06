from __future__ import annotations

from typing import Any


def open_door(*, executor: Any, door_coord: Any) -> None:
    max_attempts = 5
    executor._log(f"[X] open_door at ({door_coord.x},{door_coord.y},{door_coord.z})")
    if executor._dry_run:
        return

    executor._sync_position()
    pos = executor._current_pos
    if pos is None:
        executor._log("[X] ⚠  open_door: position unknown — skipping")
        return

    for attempt in range(1, max_attempts + 1):
        dx = max(-1, min(1, door_coord.x - pos.x))
        dy = max(-1, min(1, door_coord.y - pos.y))
        if dx == 0 and dy == 0:
            executor._log("[X] open_door: already at door tile ✓")
            return

        executor._ctrl.move_to_tile(dx, dy)
        executor._sleep(0.35)

        executor._sync_position()
        new_pos = executor._current_pos
        if new_pos is not None and (new_pos.x != pos.x or new_pos.y != pos.y):
            executor._log(
                f"[X] open_door: moved {pos}→{new_pos} "
                f"(attempt {attempt}) ✓"
            )
            return
        executor._log(
            f"[X] open_door: attempt {attempt}/{max_attempts} "
            f"— pos still {pos}, retrying"
        )
        executor._sleep(0.25)

    executor._log("[X] ⚠  open_door: could not pass through after retries")


def say_to_npc(*, executor: Any, text: str) -> None:
    executor._ctrl.press_key(0x0D)
    executor._sleep(0.15)
    executor._ctrl.type_text(text)
    executor._ctrl.press_key(0x0D)
    executor._sleep(1.0)


def switch_to_npc_channel(*, executor: Any) -> None:
    if executor._frame_getter is None:
        return
    try:
        import cv2 as _cv2
        import numpy as _np

        frame = executor._frame_getter()
        if frame is None:
            return
        height, width = frame.shape[:2]
        y1, y2 = int(height * 0.875), int(height * 0.89)
        x_max = int(width * 0.81)
        strip = frame[y1:y2, :x_max, :]
        b_ch = strip[:, :, 0].astype(_np.int16)
        g_ch = strip[:, :, 1].astype(_np.int16)
        r_ch = strip[:, :, 2].astype(_np.int16)
        mx = _np.maximum(r_ch, _np.maximum(g_ch, b_ch))
        mn = _np.minimum(r_ch, _np.minimum(g_ch, b_ch))
        sat = mx - mn
        mask = (sat > 50) & (mx > 100)
        if int(mask.sum()) < 5:
            executor._log("[X] ⚠  NPC tab not detected (no coloured label)")
            return
        ys, xs = _np.where(mask)
        cx = int(xs.mean())
        cy = y1 + int(ys.mean())
        executor._log(f"[X] NPC channel tab at ({cx},{cy}) — clicking")
        executor._ctrl.click(cx, cy)
        executor._sleep(0.4)
    except Exception as exc:
        executor._log(f"[X] ⚠  NPC tab switch error: {exc}")


def verify_npc_dialog(*, executor: Any, verify_dialog_open_fn: Any) -> None:
    if verify_dialog_open_fn is None:
        executor._log("[X] NPC dialog verify skipped — verifier unavailable")
        return
    if executor._frame_getter is None:
        executor._log("[X] NPC dialog verify skipped — frame_getter unavailable")
        return
    try:
        frame = executor._frame_getter()
        if frame is None:
            executor._log("[X] NPC dialog verify skipped — frame unavailable")
            return
        if verify_dialog_open_fn(frame):
            executor._log("[X] NPC dialog detected ✓")
            return
        executor._log("[X] ⚠ NPC dialog not detected after greeting")
    except Exception as exc:
        executor._log(f"[X] ⚠ NPC dialog verify error: {exc}")


def click_dialog_option(*, executor: Any, word: str, find_dialog_option_fn: Any) -> bool:
    if find_dialog_option_fn is None:
        return False
    if executor._frame_getter is None:
        return False
    try:
        frame = executor._frame_getter()
        if frame is None:
            return False
        point = find_dialog_option_fn(frame, word)
        if point is None:
            executor._log(f"[X] NPC option not found: {word}")
            return False
        if not executor._dry_run:
            executor._ctrl.click(point[0], point[1])
        executor._log(f"[X] NPC option clicked: {word} @ {point}")
        return True
    except Exception as exc:
        executor._log(f"[X] ⚠ NPC option click error ({word}): {exc}")
        return False