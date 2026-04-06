#!/usr/bin/env python3
"""
Test what monitor the bot is actually capturing from
"""

import sys
import cv2
import numpy as np
from pathlib import Path

def test_bot_frame_capture() -> int | None:
    """Test what the bot frame capture is actually seeing"""
    print("Testing bot frame capture configuration...")

    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter

        print("Testing with default configuration (monitor_idx=1):")
        frame_getter_1 = build_frame_getter("mss")  # default monitor_idx=1
        frame_1 = frame_getter_1()

        if frame_1 is not None:
            cv2.imwrite("captures/bot_default_capture.png", frame_1)
            print(f"   Frame size: {frame_1.shape[1]}x{frame_1.shape[0]}")

            # Analyze content
            hsv = cv2.cvtColor(frame_1, cv2.COLOR_BGR2HSV)
            saturation = hsv[:, :, 1]
            colorful_ratio = np.sum(saturation > 100) / (frame_1.shape[0] * frame_1.shape[1])
            mean_brightness = np.mean(frame_1)

            print(f"   Brightness: {mean_brightness:.1f}")
            print(f"   Colorful content: {colorful_ratio*100:.1f}%")
            print(f"   Saved: captures/bot_default_capture.png")

            if colorful_ratio < 0.02 and mean_brightness > 150:
                print("   --> Appears to be browser/text content (LinkedIn/Gmail)")
            elif colorful_ratio > 0.05:
                print("   --> Appears to be game content (likely Tibia)")
            else:
                print("   --> Unknown content type")

        print("\nTesting with Monitor 2 configuration (monitor_idx=2):")
        frame_getter_2 = build_frame_getter("mss", monitor_idx=2)
        frame_2 = frame_getter_2()

        if frame_2 is not None:
            cv2.imwrite("captures/bot_monitor2_capture.png", frame_2)
            print(f"   Frame size: {frame_2.shape[1]}x{frame_2.shape[0]}")

            # Analyze content
            hsv = cv2.cvtColor(frame_2, cv2.COLOR_BGR2HSV)
            saturation = hsv[:, :, 1]
            colorful_ratio = np.sum(saturation > 100) / (frame_2.shape[0] * frame_2.shape[1])
            mean_brightness = np.mean(frame_2)

            print(f"   Brightness: {mean_brightness:.1f}")
            print(f"   Colorful content: {colorful_ratio*100:.1f}%")
            print(f"   Saved: captures/bot_monitor2_capture.png")

            if colorful_ratio < 0.02 and mean_brightness > 150:
                print("   --> Appears to be browser/text content")
            elif colorful_ratio > 0.05:
                print("   --> Appears to be game content (likely Tibia)")
                print("   --> BOT SHOULD USE THIS CONFIGURATION!")
            else:
                print("   --> Unknown content type")

        print("\n" + "="*60)
        print("CONCLUSION:")
        if frame_1 is not None and frame_2 is not None:
            # Compare colorfulness
            hsv1 = cv2.cvtColor(frame_1, cv2.COLOR_BGR2HSV)
            hsv2 = cv2.cvtColor(frame_2, cv2.COLOR_BGR2HSV)
            colorful_1 = np.sum(hsv1[:,:,1] > 100) / (frame_1.shape[0] * frame_1.shape[1])
            colorful_2 = np.sum(hsv2[:,:,1] > 100) / (frame_2.shape[0] * frame_2.shape[1])

            if colorful_2 > colorful_1 * 2:  # Much more colorful
                print("Monitor 2 has significantly more colorful content -> TIBIA GAME")
                print("Bot should be configured to use monitor_idx=2")
                return 2
            elif colorful_1 > colorful_2 * 2:
                print("Monitor 1 has significantly more colorful content -> TIBIA GAME")
                print("Bot should be configured to use monitor_idx=1")
                return 1
            else:
                print("Content analysis inconclusive")
                return None

    except Exception as e:
        print(f"ERROR: {e}")
        return None

    return None

if __name__ == "__main__":
    result = test_bot_frame_capture()
    if result:
        print(f"\nRECOMMENDATION: Configure bot to use monitor_idx={result}")
    else:
        print("\nRECOMMENDATION: Manual verification needed")