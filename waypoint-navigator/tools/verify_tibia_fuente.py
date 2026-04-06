#!/usr/bin/env python3
"""
verify_tibia_fuente.py
----------------------
🔥 VERIFICACIÓN CRÍTICA: Proyector en ventana (Fuente) - Tibia_Fuente
Verifica que el bot esté capturando del projector correcto.
"""

import sys
import time
import json
import cv2
import numpy as np
from pathlib import Path
import subprocess
from typing import Optional

WindowMatch = tuple[int, str]

def check_obs_process() -> bool:
    """Verify OBS Studio is running."""
    try:
        # Check if OBS process exists
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq obs64.exe'],
                              capture_output=True, text=True, shell=True)

        obs_running = 'obs64.exe' in result.stdout

        if obs_running:
            print("  ✅ OBS Studio is running")
            return True
        else:
            print("  ❌ OBS Studio is NOT running")
            return False

    except Exception as e:
        print(f"  ⚠️  Cannot verify OBS process: {e}")
        return False

def check_tibia_fuente_window() -> Optional[bool]:
    """Check if Tibia_Fuente projector window exists."""
    try:
        import win32gui
        import win32con

        tibia_fuente_windows: list[WindowMatch] = []

        def enum_window_callback(hwnd: int, results: list[WindowMatch]) -> None:
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                if "Tibia_Fuente" in window_title and "Proyector en ventana" in window_title:
                    results.append((hwnd, window_title))

        win32gui.EnumWindows(enum_window_callback, tibia_fuente_windows)

        if tibia_fuente_windows:
            for hwnd, title in tibia_fuente_windows:
                print(f"  ✅ Found: {title}")

                # Get window position and size
                rect = win32gui.GetWindowRect(hwnd)
                left, top, right, bottom = rect
                width = right - left
                height = bottom - top

                print(f"      Position: ({left}, {top})")
                print(f"      Size: {width}x{height}")

                # Check if it's on Monitor 2 (typically starts at x=1920)
                if left >= 1900:  # Assuming Monitor 2 starts around x=1920
                    print(f"      ✅ Located on Monitor 2")
                else:
                    print(f"      ⚠️  Located on Monitor 1 (should be Monitor 2)")

            return True
        else:
            print("  ❌ 'Proyector en ventana (Fuente) - Tibia_Fuente' window NOT found")
            print("      Check OBS → Right-click Tibia_Fuente → Proyector en ventana (Fuente)")
            return False

    except ImportError:
        print("  ⚠️  Cannot check windows (win32gui not available)")
        return None
    except Exception as e:
        print(f"  ❌ Error checking Tibia_Fuente window: {e}")
        return False

def verify_capture_content() -> Optional[bool]:
    """Verify what the bot is actually capturing."""
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter

        frame_getter = build_frame_getter("mss")
        frame = frame_getter()

        if frame is None:
            print("  ❌ No frame captured")
            return False

        # Save current capture
        cv2.imwrite("captures/verify_tibia_fuente_capture.png", frame)

        # Quick analysis for Tibia-like content
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Look for colorful content (Tibia has colorful minimap, etc.)
        saturation = hsv[:, :, 1]
        high_sat_pixels = np.sum(saturation > 100)
        total_pixels = frame.shape[0] * frame.shape[1]
        color_ratio = high_sat_pixels / total_pixels

        # Look for game-like characteristics
        mean_brightness = np.mean(frame)

        print(f"  📊 Frame analysis:")
        print(f"      Size: {frame.shape[1]}x{frame.shape[0]}")
        print(f"      Mean brightness: {mean_brightness:.1f}")
        print(f"      Colorful content: {color_ratio*100:.1f}%")
        print(f"      Saved: captures/verify_tibia_fuente_capture.png")

        # Heuristics for content type
        if color_ratio < 0.02 and mean_brightness < 50:
            print("  🔍 Appears to be: Dark/empty content")
            return False
        elif color_ratio < 0.05 and mean_brightness > 150:
            print("  🔍 Appears to be: Web browser/text content (LinkedIn?)")
            return False
        elif color_ratio > 0.05 and 50 < mean_brightness < 150:
            print("  🔍 Appears to be: Game content (likely Tibia)")
            return True
        else:
            print("  🔍 Appears to be: Unknown content")
            return None

    except Exception as e:
        print(f"  ❌ Capture verification failed: {e}")
        return False

def main() -> int:
    print("🔥 TIBIA_FUENTE CONFIGURATION VERIFICATION")
    print("=" * 55)

    print("⚠️  CRITICAL: Bot MUST capture from 'Proyector en ventana (Fuente) - Tibia_Fuente'")
    print("")

    verification_steps = [
        ("1. OBS Studio Running", check_obs_process),
        ("2. Tibia_Fuente Projector Window", check_tibia_fuente_window),
        ("3. Capture Content Analysis", verify_capture_content),
    ]

    results = []

    for step_name, check_func in verification_steps:
        print(f"🔍 {step_name}:")
        try:
            result = check_func()
            results.append(result)
        except Exception as e:
            print(f"  ❌ Error: {e}")
            results.append(False)
        print("")

    # Overall assessment
    print("📋 VERIFICATION RESULTS:")

    obs_ok, window_ok, content_ok = results

    if obs_ok and window_ok and content_ok:
        print("  ✅ CONFIGURATION CORRECT")
        print("  ✅ Bot should capture Tibia_Fuente properly")
        print("  🚀 Ready for: python improved_player_status.py")

    elif not obs_ok:
        print("  ❌ CONFIGURATION ERROR: OBS not running")
        print("  🔧 Fix: Start OBS Studio")

    elif not window_ok:
        print("  ❌ CONFIGURATION ERROR: Tibia_Fuente projector missing")
        print("  🔧 Fix steps:")
        print("     1. Open OBS Studio")
        print("     2. Verify 'Tibia_Fuente' source exists")
        print("     3. Right-click 'Tibia_Fuente' → 'Proyector en ventana (Fuente)'")
        print("     4. Move projector window to Monitor 2")
        print("     5. Resize to 1920x1080")

    elif not content_ok:
        print("  ❌ CONFIGURATION ERROR: Wrong content captured")
        print("  🔧 Fix steps:")
        print("     1. Verify Tibia_Fuente projector shows Tibia game")
        print("     2. Verify projector is on correct monitor")
        print("     3. Verify Tibia character is logged in")
        print("     4. Check captures/verify_tibia_fuente_capture.png")

    else:
        print("  ⚠️  PARTIAL CONFIGURATION")
        print("  🔧 Check individual step results above")

    print(f"\n🎯 REMEMBER:")
    print(f"   🔥 Source name MUST be exactly 'Tibia_Fuente'")
    print(f"   🔥 Use source projector, NOT scene projector")
    print(f"   🔥 Projector MUST be on Monitor 2")
    print(f"   🔥 Bot captures from Monitor 2 via MSS")
    return 0

if __name__ == "__main__":
    exit(main())