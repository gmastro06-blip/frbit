#!/usr/bin/env python3
"""
Simple test of window-specific capture
"""

import sys
import cv2
import numpy as np
from pathlib import Path

WindowInfo = list[int]

def test_simple_window_capture() -> bool:
    """Simple test using direct window capture"""
    print("SIMPLE WINDOW CAPTURE TEST")
    print("=" * 40)

    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter

        print("1. Testing window by title capture...")

        # Use the exact window title we detected
        window_title = "Tibia - Aelzerand Neeymas"
        print(f"   Window: '{window_title}'")

        # Find window HWND
        try:
            import win32gui

            hwnd = None
            def find_window_callback(h: int, results: WindowInfo) -> None:
                if win32gui.IsWindowVisible(h):
                    title = win32gui.GetWindowText(h)
                    if title == window_title:
                        results.append(h)

            windows: WindowInfo = []
            win32gui.EnumWindows(find_window_callback, windows)

            if windows:
                hwnd = windows[0]
                print(f"   Found HWND: {hwnd:#x}")
            else:
                print(f"   ERROR: Window '{window_title}' not found!")
                return False

        except ImportError:
            print("   win32gui not available, cannot find window")
            return False

        print("\n2. Capturing from window...")

        # Build frame getter with specific hwnd
        frame_getter = build_frame_getter("mss", hwnd=hwnd)
        frame = frame_getter()

        if frame is None:
            print("   ERROR: No frame captured!")
            return False

        cv2.imwrite("captures/simple_window_test.png", frame)
        print(f"   Frame: {frame.shape[1]}x{frame.shape[0]}")
        print("   Saved: captures/simple_window_test.png")

        # Quick analysis
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        colorful_ratio = np.sum(saturation > 100) / (frame.shape[0] * frame.shape[1])
        mean_brightness = np.mean(frame)

        print(f"   Brightness: {mean_brightness:.1f}")
        print(f"   Colorful: {colorful_ratio*100:.1f}%")

        # Compare with monitor capture
        print("\n3. Comparing with Monitor 2 capture...")

        monitor_getter = build_frame_getter("mss", monitor_idx=2)
        monitor_frame = monitor_getter()

        if monitor_frame is not None:
            cv2.imwrite("captures/monitor2_comparison.png", monitor_frame)

            m_hsv = cv2.cvtColor(monitor_frame, cv2.COLOR_BGR2HSV)
            m_sat = m_hsv[:, :, 1]
            m_colorful = np.sum(m_sat > 100) / (monitor_frame.shape[0] * monitor_frame.shape[1])
            m_bright = np.mean(monitor_frame)

            print(f"   Monitor brightness: {m_bright:.1f}")
            print(f"   Monitor colorful: {m_colorful*100:.1f}%")

        print("\n" + "=" * 40)
        print("ANALYSIS:")

        # Check if window capture is working
        if colorful_ratio > 0.05 and 50 < mean_brightness < 150:
            print("✓ Window capture: WORKING")
            print("  Game content detected in window capture")

            # Compare efficiency
            if frame.shape != monitor_frame.shape:
                window_pixels = frame.shape[0] * frame.shape[1]
                monitor_pixels = monitor_frame.shape[0] * monitor_frame.shape[1]
                efficiency = (1 - window_pixels / monitor_pixels) * 100
                print(f"  Efficiency gain: {efficiency:.1f}% fewer pixels to process")

            return True
        else:
            print("✗ Window capture: ISSUE")
            print("  May not be capturing game content correctly")
            return False

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_simple_window_capture()

    if success:
        print("\nREADY: Window-specific capture is working!")
        print("Bot will be more efficient with direct window capture")
    else:
        print("\nNEEDS WORK: Check window title or OBS setup")