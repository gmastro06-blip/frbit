from __future__ import annotations

from typing import TYPE_CHECKING, Any, Collection

if TYPE_CHECKING:
    from .input_controller import InputController


def press_postmessage(
    controller: "InputController",
    vk: int,
    delay: float,
    *,
    user32_module: Any,
    time_module: Any,
    wm_keydown: int,
    wm_keyup: int,
) -> None:
    lparam_down = controller._make_lparam(vk, 0)
    lparam_up = controller._make_lparam(vk, 1)
    user32_module.PostMessageW(controller._hwnd, wm_keydown, vk, lparam_down)
    time_module.sleep(delay)
    user32_module.PostMessageW(controller._hwnd, wm_keyup, vk, lparam_up)


def ensure_foreground(
    controller: "InputController",
    *,
    user32_module: Any,
    time_module: Any,
) -> bool:
    if (
        not controller._using_arduino_failover
        and controller.input_method == "interception"
        and not controller._interception_failed
    ):
        return True
    if user32_module.GetForegroundWindow() == controller._hwnd:
        return True
    deadline = time_module.monotonic() + 3.0
    while time_module.monotonic() < deadline:
        if user32_module.GetForegroundWindow() == controller._hwnd:
            return True
        time_module.sleep(0.05)
    user32_module.SetForegroundWindow(controller._hwnd)
    return True


def press_interception(
    controller: "InputController",
    vk: int,
    delay: float,
    *,
    ctypes_module: Any,
    time_module: Any,
    extended_vk: Collection[int],
) -> None:
    from interception.constants import KeyFlag  # type: ignore[import-untyped]
    from interception.strokes import KeyStroke  # type: ignore[import-untyped]

    ctx = controller._get_interception()
    scan = ctypes_module.windll.user32.MapVirtualKeyW(vk, 0)
    extended = vk in extended_vk

    flags_down: int = KeyFlag.KEY_DOWN
    flags_up: int = KeyFlag.KEY_UP
    if extended:
        flags_down |= KeyFlag.KEY_E0
        flags_up |= KeyFlag.KEY_E0

    ctx.send(ctx.keyboard, KeyStroke(scan, flags_down))
    time_module.sleep(delay)
    ctx.send(ctx.keyboard, KeyStroke(scan, flags_up))


def ensure_pico_foreground(controller: "InputController") -> None:
    del controller
    return


def click_interception(
    controller: "InputController",
    x: int,
    y: int,
    button: str,
    *,
    ctypes_module: Any,
    user32_module: Any,
    time_module: Any,
    random_module: Any,
    wt_module: Any,
) -> None:
    from interception.constants import MouseButtonFlag, MouseFlag  # type: ignore[import-untyped]
    from interception.strokes import MouseStroke  # type: ignore[import-untyped]

    ctx = controller._get_interception()
    pt = wt_module.POINT(x, y)
    user32_module.ClientToScreen(controller._hwnd, ctypes_module.byref(pt))

    scr_w = user32_module.GetSystemMetrics(0) or 1920
    scr_h = user32_module.GetSystemMetrics(1) or 1080
    abs_x = int(pt.x * 65535 / scr_w)
    abs_y = int(pt.y * 65535 / scr_h)

    if button == "left":
        btn_down = MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN
        btn_up = MouseButtonFlag.MOUSE_LEFT_BUTTON_UP
    else:
        btn_down = MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN
        btn_up = MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP

    move = MouseStroke(MouseFlag.MOUSE_MOVE_ABSOLUTE, 0, 0, abs_x, abs_y)
    ctx.send(ctx.mouse, move)
    time_module.sleep(random_module.uniform(0.006, 0.025))

    down = MouseStroke(0, btn_down, 0, 0, 0)
    ctx.send(ctx.mouse, down)
    time_module.sleep(controller._jitter(0.05))

    up = MouseStroke(0, btn_up, 0, 0, 0)
    ctx.send(ctx.mouse, up)


def press_scancode(
    controller: "InputController",
    vk: int,
    delay: float,
    *,
    ctypes_module: Any,
    time_module: Any,
    extended_vk: Collection[int],
) -> None:
    scan = ctypes_module.windll.user32.MapVirtualKeyW(vk, 0)
    extended = vk in extended_vk
    controller._keybd_event(vk, scan, True, extended)
    time_module.sleep(delay)
    controller._keybd_event(vk, scan, False, extended)


def press_hybrid(
    controller: "InputController",
    vk: int,
    delay: float,
    *,
    ctypes_module: Any,
    time_module: Any,
    extended_vk: Collection[int],
) -> None:
    scan = ctypes_module.windll.user32.MapVirtualKeyW(vk, 0)
    extended = vk in extended_vk
    controller._keybd_event(vk, scan, True, extended)
    time_module.sleep(delay)
    controller._keybd_event(vk, scan, False, extended)


def type_interception(
    controller: "InputController",
    text: str,
    *,
    ctypes_module: Any,
    time_module: Any,
) -> None:
    from interception.constants import KeyFlag  # type: ignore[import-untyped]
    from interception.strokes import KeyStroke  # type: ignore[import-untyped]

    ctx = controller._get_interception()
    vk_shift = 0x10
    scan_shift = ctypes_module.windll.user32.MapVirtualKeyW(vk_shift, 0)

    for ch in text:
        vk_scan = ctypes_module.windll.user32.VkKeyScanW(ord(ch))
        if vk_scan == -1 or vk_scan == 0xFFFF:
            continue
        vk = vk_scan & 0xFF
        need_shift = bool((vk_scan >> 8) & 1)
        scan = ctypes_module.windll.user32.MapVirtualKeyW(vk, 0)

        if need_shift:
            ctx.send(ctx.keyboard, KeyStroke(scan_shift, KeyFlag.KEY_DOWN))
            time_module.sleep(controller._jitter(0.005))

        ctx.send(ctx.keyboard, KeyStroke(scan, KeyFlag.KEY_DOWN))
        time_module.sleep(controller._jitter(0.010))
        ctx.send(ctx.keyboard, KeyStroke(scan, KeyFlag.KEY_UP))

        if need_shift:
            time_module.sleep(controller._jitter(0.005))
            ctx.send(ctx.keyboard, KeyStroke(scan_shift, KeyFlag.KEY_UP))

        time_module.sleep(controller._jitter(0.02))


def type_hardware(
    controller: "InputController",
    text: str,
    *,
    ctypes_module: Any,
    time_module: Any,
) -> None:
    vk_shift = 0x10
    scan_shift = ctypes_module.windll.user32.MapVirtualKeyW(vk_shift, 0)

    for ch in text:
        vk_scan = ctypes_module.windll.user32.VkKeyScanW(ord(ch))
        if vk_scan == -1 or vk_scan == 0xFFFF:
            continue
        vk = vk_scan & 0xFF
        need_shift = bool((vk_scan >> 8) & 1)
        scan = ctypes_module.windll.user32.MapVirtualKeyW(vk, 0)

        if need_shift:
            controller._keybd_event(vk_shift, scan_shift, True, False)
            time_module.sleep(controller._jitter(0.005))

        controller._keybd_event(vk, scan, True, False)
        time_module.sleep(controller._jitter(0.010))
        controller._keybd_event(vk, scan, False, False)

        if need_shift:
            time_module.sleep(controller._jitter(0.005))
            controller._keybd_event(vk_shift, scan_shift, False, False)

        time_module.sleep(controller._jitter(0.02))