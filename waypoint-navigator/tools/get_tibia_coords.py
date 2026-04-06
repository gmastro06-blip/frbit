#!/usr/bin/env python3
"""
Get exact Tibia window coordinates
"""

import win32gui

WindowInfo = dict[str, int | str]

def get_tibia_window_coordinates() -> list[WindowInfo]:
    """Find Tibia window and get its exact coordinates"""

    tibia_windows: list[WindowInfo] = []

    def enum_window_callback(hwnd: int, results: list[WindowInfo]) -> None:
        if win32gui.IsWindowVisible(hwnd):
            window_title = win32gui.GetWindowText(hwnd)
            if "Tibia" in window_title and "Proyector" not in window_title:
                rect = win32gui.GetWindowRect(hwnd)
                left, top, right, bottom = rect
                width = right - left
                height = bottom - top

                results.append({
                    'title': window_title,
                    'hwnd': hex(hwnd),
                    'x': left,
                    'y': top,
                    'width': width,
                    'height': height,
                    'right': right,
                    'bottom': bottom
                })

    win32gui.EnumWindows(enum_window_callback, tibia_windows)

    print("TIBIA WINDOW COORDINATES:")
    print("=" * 40)

    if tibia_windows:
        for window in tibia_windows:
            print(f"Window: {window['title']}")
            print(f"  HWND: {window['hwnd']}")
            print(f"  Position: ({window['x']}, {window['y']})")
            print(f"  Size: {window['width']} x {window['height']}")
            print(f"  Region: ({window['x']}, {window['y']}, {window['width']}, {window['height']})")
            print()
    else:
        print("No Tibia windows found")

    return tibia_windows

if __name__ == "__main__":
    windows = get_tibia_window_coordinates()

    if windows:
        main_window = windows[0]
        print("FOR BOT CONFIGURATION:")
        print(f"frame_window = '{main_window['title']}'")
        print(f"# OR use region: x={main_window['x']}, y={main_window['y']}, w={main_window['width']}, h={main_window['height']}")