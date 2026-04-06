
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple


import cv2
import numpy as np

from .action_verifier import with_retry
from .ui_detection import detect_container_window


@dataclass(frozen=True)
class ClientActionResult:
    success: bool
    method: str


def _run_action(
    action_fn: Callable[[], ClientActionResult],
    *,
    verify_fn: Optional[Callable[[], bool]] = None,
    max_attempts: int = 1,
    delay_between: float = 0.25,
) -> ClientActionResult:
    last_result = ClientActionResult(False, "failed")

    def _attempt() -> ClientActionResult:
        nonlocal last_result
        last_result = action_fn()
        return last_result

    wrapped = with_retry(
        max_attempts=max(1, max_attempts),
        verify=lambda result: result.success and (verify_fn() if verify_fn is not None else True),
        delay_between=delay_between,
    )(_attempt)

    try:
        return wrapped()
    except Exception:
        return last_result


def select_context_menu_entry(
    *,
    ctrl: Any,
    click_x: int,
    click_y: int,
    entry_index: int,
    fallback_offset_y: int,
    frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None,
    frame_ref: Optional[np.ndarray] = None,
    detect_context_menu_fn: Optional[Callable[..., Any]],
    find_menu_entry_offset_fn: Optional[Callable[..., Any]],
    sleep_fn: Callable[[float], Any],
    scale_x_offset_fn: Optional[Callable[[int, Optional[np.ndarray]], int]] = None,
    scale_y_offset_fn: Optional[Callable[[int, Optional[np.ndarray]], int]] = None,
    fallback_offset_x: int = 5,
    verify_fn: Optional[Callable[[], bool]] = None,
    max_attempts: int = 1,
) -> ClientActionResult:
    def _action() -> ClientActionResult:
        frame_before = frame_getter() if frame_getter is not None else frame_ref
        if not ctrl.click(click_x, click_y, button="right"):
            return ClientActionResult(False, "right_click")

        sleep_fn(0.25)
        frame_after = frame_getter() if frame_getter is not None else None
        if frame_before is not None and frame_after is not None and detect_context_menu_fn is not None:
            menu_box = detect_context_menu_fn(frame_before, frame_after, click_x, click_y)
            if menu_box is not None and find_menu_entry_offset_fn is not None:
                entry = find_menu_entry_offset_fn(frame_after, menu_box, entry_index=entry_index)
                if entry is not None:
                    ex, ey = entry
                    if ctrl.click(ex, ey, button="left"):
                        return ClientActionResult(True, "visual")
                    return ClientActionResult(False, "visual_click")

        ref_frame = frame_after if frame_after is not None else frame_before
        offset_x = fallback_offset_x
        offset_y = fallback_offset_y
        if scale_x_offset_fn is not None:
            offset_x = scale_x_offset_fn(fallback_offset_x, ref_frame)
        if scale_y_offset_fn is not None:
            offset_y = scale_y_offset_fn(fallback_offset_y, ref_frame)

        if ctrl.click(click_x + offset_x, click_y + offset_y, button="left"):
            return ClientActionResult(True, "offset")
        return ClientActionResult(False, "offset_click")

    return _run_action(
        _action,
        verify_fn=verify_fn,
        max_attempts=max_attempts,
    )


def quick_loot_target(
    *,
    ctrl: Any,
    click_x: int,
    click_y: int,
    use_hotkey: bool,
    quick_loot_menu_offset_y: int,
    frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None,
    detect_context_menu_fn: Optional[Callable[..., Any]] = None,
    find_menu_entry_offset_fn: Optional[Callable[..., Any]] = None,
    sleep_fn: Callable[[float], Any],
    scale_x_offset_fn: Optional[Callable[[int, Optional[np.ndarray]], int]] = None,
    scale_y_offset_fn: Optional[Callable[[int, Optional[np.ndarray]], int]] = None,
    verify_fn: Optional[Callable[[], bool]] = None,
    max_attempts: int = 1,
) -> ClientActionResult:
    if use_hotkey:
        def _action() -> ClientActionResult:
            if not ctrl.move_mouse(click_x, click_y):
                return ClientActionResult(False, "move_mouse")
            sleep_fn(0.08)
            if ctrl.key_combo(0x12, 0x51):
                return ClientActionResult(True, "hotkey")
            return ClientActionResult(False, "key_combo")

        return _run_action(
            _action,
            verify_fn=verify_fn,
            max_attempts=max_attempts,
        )

    return select_context_menu_entry(
        ctrl=ctrl,
        click_x=click_x,
        click_y=click_y,
        entry_index=1,
        fallback_offset_y=quick_loot_menu_offset_y,
        frame_getter=frame_getter,
        detect_context_menu_fn=detect_context_menu_fn,
        find_menu_entry_offset_fn=find_menu_entry_offset_fn,
        sleep_fn=sleep_fn,
        scale_x_offset_fn=scale_x_offset_fn,
        scale_y_offset_fn=scale_y_offset_fn,
        verify_fn=verify_fn,
        max_attempts=max_attempts,
    )


def use_hotkey_on_current_tile(
    *,
    ctrl: Any,
    hotkey_vk: int,
    click_character_tile_fn: Callable[[], Any],
    sleep_fn: Callable[[float], Any],
    verify_fn: Optional[Callable[[], bool]] = None,
    max_attempts: int = 1,
    hotkey_delay: float = 0.35,
    settle_delay: float = 1.5,
) -> ClientActionResult:
    def _action() -> ClientActionResult:
        if not ctrl.press_key(hotkey_vk):
            return ClientActionResult(False, "press_key")
        sleep_fn(hotkey_delay)
        click_character_tile_fn()
        sleep_fn(settle_delay)
        return ClientActionResult(True, "hotkey_on_tile")

    return _run_action(
        _action,
        verify_fn=verify_fn,
        max_attempts=max_attempts,
    )


def cancel_current_action(
    *,
    ctrl: Any,
    cancel_vk: int,
    sleep_fn: Callable[[float], Any],
    verify_fn: Optional[Callable[[], bool]] = None,
    max_attempts: int = 1,
    wait_s: float = 0.3,
) -> ClientActionResult:
    def _action() -> ClientActionResult:
        if not ctrl.press_key(cancel_vk):
            return ClientActionResult(False, "press_key")
        sleep_fn(wait_s)
        return ClientActionResult(True, "cancel")

    return _run_action(
        _action,
        verify_fn=verify_fn,
        max_attempts=max_attempts,
    )


def close_open_containers(
    *,
    ctrl: Any,
    close_vk: int,
    sleep_fn: Callable[[float], Any],
    wait_s: float,
    frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None,
    container_roi: Optional[Tuple[int, int, int, int]] = None,
    ref_width: int = 1920,
    ref_height: int = 1080,
    verify_timeout: float = 1.0,
    verify_poll_interval: float = 0.15,
    max_attempts: int = 1,
) -> ClientActionResult:
    verify_fn = None
    if frame_getter is not None and container_roi is not None and len(container_roi) == 4:
        verify_fn = lambda: wait_for_container_closed(
            frame_getter=frame_getter,
            container_roi=container_roi,
            sleep_fn=sleep_fn,
            timeout=verify_timeout,
            poll_interval=verify_poll_interval,
            ref_width=ref_width,
            ref_height=ref_height,
        )

    return cancel_current_action(
        ctrl=ctrl,
        cancel_vk=close_vk,
        sleep_fn=sleep_fn,
        verify_fn=verify_fn,
        max_attempts=max_attempts,
        wait_s=wait_s,
    )


def wait_for_container_closed(
    *,
    frame_getter: Callable[[], Optional[np.ndarray]],
    container_roi: Tuple[int, int, int, int],
    sleep_fn: Callable[[float], Any],
    timeout: float,
    poll_interval: float,
    ref_width: int = 1920,
    ref_height: int = 1080,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is not None and not _container_is_visible(
            frame,
            container_roi=container_roi,
            ref_width=ref_width,
            ref_height=ref_height,
        ):
            return True
        sleep_fn(poll_interval)
    return False


def _container_is_visible(
    frame: np.ndarray,
    *,
    container_roi: Tuple[int, int, int, int],
    ref_width: int,
    ref_height: int,
) -> bool:
    if frame is None or frame.size == 0:
        return False

    x, y, w, h = container_roi
    frame_h, frame_w = frame.shape[:2]
    if frame_w != ref_width or frame_h != ref_height:
        x = int(x * frame_w / ref_width)
        y = int(y * frame_h / ref_height)
        w = int(w * frame_w / ref_width)
        h = int(h * frame_h / ref_height)

    x1 = max(0, min(x, frame_w))
    y1 = max(0, min(y, frame_h))
    x2 = max(0, min(x + w, frame_w))
    y2 = max(0, min(y + h, frame_h))
    if x2 <= x1 or y2 <= y1:
        return False

    crop = frame[y1:y2, x1:x2]
    detected = detect_container_window(
        crop,
        search_roi=(0, 0, crop.shape[1], crop.shape[0]),
        min_width=60,
        min_height=40,
    )
    if detected is not None:
        return True

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([100, 50, 50]),
        np.array([150, 255, 255]),
    )
    blue_ratio = float(np.sum(mask > 0)) / max(crop.shape[0] * crop.shape[1], 1)
    return blue_ratio > 0.02