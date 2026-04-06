#!/usr/bin/env python3
"""
Simple test to verify Monitor 2 shows Tibia content
"""

import sys
import cv2
import numpy as np
from pathlib import Path
from typing import Any


def _to_uint8_color(*values: int) -> np.ndarray:
    return np.array(values, dtype=np.uint8)


def _cv_in_range(image: Any, lower: Any, upper: Any) -> np.ndarray:
    return cv2.inRange(image, lower, upper)

def simple_detection_test() -> bool:
    """Simple test of frame capture from Monitor 2"""
    print("SIMPLE DETECTION TEST - MONITOR 2")
    print("=" * 45)

    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))

        # Test with the new default configuration
        from frame_capture import build_frame_getter

        # Bot should now use Monitor 2 by default
        print("Testing bot frame capture (should be Monitor 2)...")
        frame_getter = build_frame_getter("mss")  # Uses default monitor_idx from session
        frame = frame_getter()

        if frame is None:
            print("ERROR: No frame captured")
            return False

        cv2.imwrite("captures/bot_detection_test.png", frame)
        print(f"Frame captured: {frame.shape[1]}x{frame.shape[0]}")

        # Analyze content for game characteristics
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        colorful_ratio = np.sum(saturation > 100) / (frame.shape[0] * frame.shape[1])
        mean_brightness = np.mean(frame)

        print(f"Brightness: {mean_brightness:.1f}")
        print(f"Colorful content: {colorful_ratio*100:.1f}%")
        print(f"Saved: captures/bot_detection_test.png")

        # Look for specific Tibia game elements
        if colorful_ratio > 0.05 and 50 < mean_brightness < 150:
            print("CONTENT: Appears to be game content (likely Tibia)")

            # Check for green HP bar pixels (around top area)
            top_region = frame[0:50, :]
            green_mask = _cv_in_range(hsv[0:50, :], _to_uint8_color(40, 50, 50), _to_uint8_color(80, 255, 255))
            green_pixels = int(np.sum(green_mask > 0))

            # Check for blue MP bar pixels
            blue_mask = _cv_in_range(hsv[0:50, :], _to_uint8_color(100, 50, 50), _to_uint8_color(130, 255, 255))
            blue_pixels = int(np.sum(blue_mask > 0))

            print(f"Green pixels (HP bar): {green_pixels}")
            print(f"Blue pixels (MP bar): {blue_pixels}")

            if green_pixels > 100 or blue_pixels > 100:
                print("SUCCESS: Detected HP/MP bar colors - TIBIA GAME CONFIRMED!")
                return True
            else:
                print("WARNING: No clear HP/MP bars detected")
                return False

        elif colorful_ratio < 0.02 and mean_brightness > 150:
            print("CONTENT: Appears to be browser/text content")
            print("ERROR: Bot is still capturing wrong monitor")
            return False
        else:
            print("CONTENT: Unknown content type")
            return False

    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    success = simple_detection_test()

    print("\n" + "=" * 45)
    if success:
        print("RESULT: SUCCESS - Bot should detect Tibia correctly now")
        print("Try running: python improved_player_status.py")
    else:
        print("RESULT: FAILED - Check captures/bot_detection_test.png")
        print("May need additional configuration")