#!/usr/bin/env python3
"""
Quick projector detection - simplified version
"""

import sys
import cv2
import numpy as np
from pathlib import Path

def detect_projector_location():
    """Detecta en que monitor esta el proyector Tibia_Fuente."""
    try:
        import win32gui

        tibia_projector_info = None

        def enum_window_callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                if "Tibia_Fuente" in window_title and "Proyector en ventana" in window_title:
                    rect = win32gui.GetWindowRect(hwnd)
                    left, top, right, bottom = rect
                    width = right - left
                    height = bottom - top
                    results.append({
                        'hwnd': hwnd,
                        'title': window_title,
                        'left': left,
                        'top': top,
                        'width': width,
                        'height': height,
                        'right': right,
                        'bottom': bottom
                    })

        windows = []
        win32gui.EnumWindows(enum_window_callback, windows)

        if windows:
            tibia_projector_info = windows[0]
            print(f"PROYECTOR ENCONTRADO:")
            print(f"   Title: {tibia_projector_info['title']}")
            print(f"   Position: ({tibia_projector_info['left']}, {tibia_projector_info['top']})")
            print(f"   Size: {tibia_projector_info['width']}x{tibia_projector_info['height']}")

            return tibia_projector_info
        else:
            print("Proyector Tibia_Fuente NO encontrado")
            return None

    except ImportError:
        print("win32gui no disponible")
        return None
    except Exception as e:
        print(f"Error detectando proyector: {e}")
        return None

def get_monitor_info():
    """Obtiene informacion de todos los monitores."""
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        import mss

        with mss.mss() as sct:
            monitors = sct.monitors

            print(f"MONITORES DETECTADOS:")
            for i, monitor in enumerate(monitors):
                if i == 0:
                    continue  # Skip "all screens"

                print(f"   Monitor {i}: {monitor['width']}x{monitor['height']} at ({monitor['left']}, {monitor['top']})")

            return monitors

    except Exception as e:
        print(f"Error detectando monitores: {e}")
        return None

def determine_projector_monitor(projector_info, monitors):
    """Determina en que monitor esta el proyector."""
    if not projector_info or not monitors:
        return None

    proj_left = projector_info['left']
    proj_top = projector_info['top']

    print(f"\nANALISIS DE UBICACION:")
    print(f"   Proyector en: ({proj_left}, {proj_top})")

    for i, monitor in enumerate(monitors):
        if i == 0:  # Skip "all screens"
            continue

        mon_left = monitor['left']
        mon_top = monitor['top']
        mon_right = monitor['left'] + monitor['width']
        mon_bottom = monitor['top'] + monitor['height']

        # Check if projector is within this monitor's bounds
        if (mon_left <= proj_left < mon_right and
            mon_top <= proj_top < mon_bottom):

            print(f"   PROYECTOR ESTA EN MONITOR {i}")
            print(f"      Monitor bounds: ({mon_left}, {mon_top}) to ({mon_right}, {mon_bottom})")
            print(f"      Proyector position: ({proj_left}, {proj_top})")

            return i

    print(f"   Proyector no esta claramente en ningun monitor")
    return None

def test_capture_from_monitor(monitor_num, monitors):
    """Captura desde un monitor especifico para verificar."""
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        import mss

        if monitor_num >= len(monitors):
            print(f"Monitor {monitor_num} no existe")
            return False

        with mss.mss() as sct:
            monitor = monitors[monitor_num]
            screenshot = sct.grab(monitor)
            frame = np.array(screenshot)

            # Convert BGRA to BGR if needed
            if frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # Save test capture
            filename = f"captures/monitor_{monitor_num}_test.png"
            cv2.imwrite(filename, frame)

            # Quick analysis
            mean_brightness = np.mean(frame)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            saturation = hsv[:, :, 1]
            colorful_ratio = np.sum(saturation > 100) / (frame.shape[0] * frame.shape[1])

            print(f"CAPTURA MONITOR {monitor_num}:")
            print(f"   File: {filename}")
            print(f"   Size: {frame.shape[1]}x{frame.shape[0]}")
            print(f"   Brightness: {mean_brightness:.1f}")
            print(f"   Colorful content: {colorful_ratio*100:.1f}%")

            return True

    except Exception as e:
        print(f"Error capturando de Monitor {monitor_num}: {e}")
        return False

def main():
    print("DETECCION AUTOMATICA DE MONITOR DEL PROYECTOR")
    print("=" * 55)

    # 1. Detect projector location
    projector_info = detect_projector_location()

    # 2. Get monitor information
    monitors = get_monitor_info()

    # 3. Determine which monitor has the projector
    if projector_info and monitors:
        target_monitor = determine_projector_monitor(projector_info, monitors)

        if target_monitor:
            print(f"\nRESULTADO:")
            print(f"   Proyector Tibia_Fuente esta en: MONITOR {target_monitor}")

            # 4. Test capture from that monitor
            print(f"\nTESTING CAPTURE FROM MONITOR {target_monitor}:")
            if test_capture_from_monitor(target_monitor, monitors):
                print(f"\nSOLUCION:")
                print(f"   Bot debe capturar desde MONITOR {target_monitor}")
                print(f"   Modify frame_capture.py to use monitors[{target_monitor}]")
                print(f"\nVERIFICATION:")
                print(f"   Check: captures/monitor_{target_monitor}_test.png")
                print(f"   Should show: Tibia game content")

            return target_monitor
        else:
            print(f"\nNo se pudo determinar el monitor del proyector")

    # Fallback: test all monitors
    print(f"\nFALLBACK: Testing all monitors...")
    if monitors:
        for i in range(1, len(monitors)):
            test_capture_from_monitor(i, monitors)

        print(f"\nCheck captures/monitor_*_test.png files")
        print(f"   Find which one shows Tibia content")

    return None

if __name__ == "__main__":
    result = main()
    if result:
        print(f"\nUSE MONITOR {result} FOR BOT CAPTURE")
    else:
        print(f"\nManual verification needed")