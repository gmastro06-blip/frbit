#!/usr/bin/env python3
"""
Test window-specific capture configuration
"""

import sys
from pathlib import Path
from typing import Any


def _to_uint8_color(*values: int) -> Any:
    import numpy as np
    return np.array(values, dtype=np.uint8)


def _cv_in_range(image: Any, lower: Any, upper: Any) -> Any:
    import cv2
    return cv2.inRange(image, lower, upper)

def test_window_capture() -> bool:
    """Test new window-specific frame capture"""
    print("TESTING WINDOW-SPECIFIC CAPTURE")
    print("=" * 50)

    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))

        # Test with session configuration
        from session import SessionConfig
        from frame_capture import build_frame_getter
        import input_controller

        print("1. Testing window detection...")

        # Get the configured window title
        config = SessionConfig()
        frame_window = config.frame_window
        print(f"   Configured window: '{frame_window}'")

        # Find the window
        window_info = input_controller.find_window(frame_window)
        if window_info:
            print(f"   Window found: HWND {window_info.hwnd:#x}")
            print(f"   Window rect: ({window_info.left}, {window_info.top}, {window_info.width}, {window_info.height})")
            hwnd = window_info.hwnd
        else:
            print(f"   ERROR: Window '{frame_window}' not found!")
            return False

        print("\n2. Testing frame capture with window HWND...")

        # Build frame getter with hwnd
        frame_getter = build_frame_getter("mss", hwnd=hwnd)
        frame = frame_getter()

        if frame is None:
            print("   ERROR: No frame captured!")
            return False

        print(f"   Frame captured: {frame.shape[1]}x{frame.shape[0]}")

        # Save and analyze frame
        import cv2
        import numpy as np

        cv2.imwrite("captures/window_capture_test.png", frame)
        print(f"   Saved: captures/window_capture_test.png")

        # Analyze content
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        colorful_ratio = np.sum(saturation > 100) / (frame.shape[0] * frame.shape[1])
        mean_brightness = np.mean(frame)

        print(f"   Brightness: {mean_brightness:.1f}")
        print(f"   Colorful content: {colorful_ratio*100:.1f}%")

        # Look for game UI elements
        print("\n3. Detecting game elements...")

        # HP bar (green pixels in top area)
        top_region = frame[0:50, :]
        hsv_top = cv2.cvtColor(top_region, cv2.COLOR_BGR2HSV)
        green_mask = _cv_in_range(hsv_top, _to_uint8_color(40, 100, 100), _to_uint8_color(80, 255, 255))
        green_pixels = np.sum(green_mask > 0)

        # MP bar (blue pixels)
        blue_mask = _cv_in_range(hsv_top, _to_uint8_color(100, 100, 100), _to_uint8_color(130, 255, 255))
        blue_pixels = np.sum(blue_mask > 0)

        print(f"   HP bar (green) pixels: {green_pixels}")
        print(f"   MP bar (blue) pixels: {blue_pixels}")

        # Minimap area (top-right corner relative to window)
        window_width = frame.shape[1]
        minimap_x = max(0, window_width - 150)
        minimap_roi = frame[20:140, minimap_x:window_width]

        if minimap_roi.size > 0:
            hsv_minimap = cv2.cvtColor(minimap_roi, cv2.COLOR_BGR2HSV)
            minimap_saturation = hsv_minimap[:, :, 1]
            minimap_colorful = np.sum(minimap_saturation > 100) / (minimap_roi.shape[0] * minimap_roi.shape[1])
            print(f"   Minimap colorfulness: {minimap_colorful*100:.1f}%")
        else:
            minimap_colorful = 0

        print("\n" + "=" * 50)
        print("RESULTS:")

        success = (
            colorful_ratio > 0.05 and              # Game-like content
            50 < mean_brightness < 150 and         # Appropriate brightness
            green_pixels > 500                     # HP bar visible
        )

        if success:
            print("✓ SUCCESS: Window capture is working correctly!")
            print("  - Game content detected")
            print("  - UI elements visible")
            print("  - Ready for bot operations")

            return True
        else:
            print("✗ ISSUE: Window capture may need adjustment")
            print(f"  - Colorful content: {colorful_ratio*100:.1f}% (need >5%)")
            print(f"  - Brightness: {mean_brightness:.1f} (need 50-150)")
            print(f"  - HP bar pixels: {green_pixels} (need >500)")
            print("  - Check captures/window_capture_test.png")

            return False

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_window_capture()

    print("\n" + "=" * 50)
    if success:
        print("WINDOW CAPTURE: READY")
        print("Bot will now capture directly from Tibia window")
        print("This is more efficient and accurate than monitor capture")
    else:
        print("WINDOW CAPTURE: NEEDS WORK")
        print("May need to adjust window title or check OBS setup")