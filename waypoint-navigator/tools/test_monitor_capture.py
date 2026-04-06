#!/usr/bin/env python3
"""
test_monitor_capture.py
-----------------------
Test capture from each available monitor to find Tibia.
"""

import sys
from pathlib import Path
import cv2

def main() -> int:
    print("🖥️  MULTI-MONITOR CAPTURE TEST")
    print("=" * 45)

    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        import mss
    except Exception as e:
        print(f"❌ MSS import failed: {e}")
        return 1

    with mss.mss() as sct:
        monitors = sct.monitors

        print(f"🔍 Found {len(monitors)-1} monitors:")
        for i, monitor in enumerate(monitors):
            if i == 0:
                print(f"  Monitor {i}: All screens combined")
            else:
                print(f"  Monitor {i}: {monitor['width']}x{monitor['height']} at ({monitor['left']}, {monitor['top']})")

        print(f"\n📸 Capturing from each monitor...")

        for i in range(1, len(monitors)):  # Skip monitor 0 (all screens)
            try:
                monitor = monitors[i]
                screenshot = sct.grab(monitor)

                # Convert to numpy array
                import numpy as np
                frame = np.array(screenshot)

                # Convert BGRA to BGR
                if frame.shape[2] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                h, w = frame.shape[:2]

                # Save capture
                filename = f"captures/monitor_{i}_capture.png"
                cv2.imwrite(filename, frame)

                # Quick analysis
                mean_brightness = np.mean(frame)

                print(f"  ✅ Monitor {i}: {w}x{h} → {filename}")
                print(f"      Mean brightness: {mean_brightness:.1f}")

                # Look for Tibia-like characteristics
                # Tibia typically has colorful minimap, HP/MP bars, etc.
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                saturation = hsv[:, :, 1]
                high_saturation_pixels = np.sum(saturation > 100)
                total_pixels = w * h
                color_ratio = high_saturation_pixels / total_pixels

                print(f"      Colorful content: {color_ratio*100:.1f}%")

                if color_ratio > 0.1:  # More than 10% colorful
                    print(f"      💡 Likely candidate for game content")

                # Check if it looks like a web browser (too much text)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                edges = cv2.Canny(gray, 50, 150)
                edge_ratio = np.sum(edges > 0) / total_pixels

                if edge_ratio > 0.15:  # Lots of edges = text/UI
                    print(f"      📄 High text/UI content (web browser?)")
                elif color_ratio > 0.05:
                    print(f"      🎮 Possible game content")

            except Exception as e:
                print(f"  ❌ Monitor {i}: Error - {e}")

    print(f"\n📋 ANÁLISIS:")
    print(f"1. Check captures/monitor_X_capture.png files")
    print(f"2. Find which monitor shows Tibia")
    print(f"3. Use that monitor number for bot configuration")

    print(f"\n🔧 TO FIX BOT CAPTURE:")
    print(f"   Option A: Move OBS projector to Monitor 1")
    print(f"   Option B: Configure bot to use correct monitor")
    print(f"            (modify src/frame_capture.py)")

    print(f"\n🎯 Look for monitor with:")
    print(f"   • Tibia game window visible")
    print(f"   • Colorful minimap")
    print(f"   • Character HP/MP bars")
    print(f"   • NOT web browser content")
    return 0

if __name__ == "__main__":
    exit(main())