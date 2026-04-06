#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_roi_detection.py
-----------------------
Debug tool - muestra las capturas ROI en ventanas para verificar alineación.

Usage:
    python debug_roi_detection.py
"""

import sys
import io
import cv2
import json
import numpy as np
from pathlib import Path
from typing import Any

# Fix Windows console encoding for emoji support
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def _to_uint8_color(*values: int) -> np.ndarray:
    return np.array(values, dtype=np.uint8)


def _cv_in_range(image: Any, lower: Any, upper: Any) -> np.ndarray:
    return cv2.inRange(image, lower, upper)


def main() -> int:
    print("🔍 ROI Detection Debug Tool")
    print("=" * 40)
    print("📋 Mostrará ventanas con las capturas de cada ROI")
    print("🔍 Verifica si las coordenadas están alineadas correctamente")
    print("❌ Presiona 'q' en cualquier ventana para salir")
    print()

    # Setup frame capture
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter
        frame_getter = build_frame_getter("mss")
        print("✅ Frame capture inicializado")
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1

    # Load configs
    try:
        with open('hpmp_config.json', 'r', encoding='utf-8') as hpmp_file:
            hpmp_config = json.load(hpmp_file)
        with open('minimap_config.json', 'r', encoding='utf-8') as minimap_file:
            minimap_config = json.load(minimap_file)
        with open('combat_config.json', 'r', encoding='utf-8') as combat_file:
            combat_config = json.load(combat_file)
        with open('condition_config.json', 'r', encoding='utf-8') as condition_file:
            condition_config = json.load(condition_file)

        configs = {
            'hpmp': hpmp_config,
            'minimap': minimap_config,
            'combat': combat_config,
            'condition': condition_config,
        }
        print("✅ Configs loaded")
    except Exception as e:
        print(f"❌ Config error: {e}")
        return 1

    print("\n🖼️  Generando ventanas debug...")

    while True:
        # Capture frame
        frame = frame_getter()
        if frame is None:
            print("❌ No frame")
            break

        h, w = frame.shape[:2]
        print(f"\n📸 Frame: {w}x{h}")

        # 1. Full frame (small)
        frame_small = cv2.resize(frame, (960, 540))  # 50% scale
        cv2.putText(frame_small, "Full Frame (50% scale)", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.imshow("1. Full Frame", frame_small)

        # 2. HP Bar
        try:
            x, y, w_roi, h_roi = configs['hpmp']['hp_roi']
            hp_roi = frame[y:y+h_roi, x:x+w_roi]

            if hp_roi.size > 0:
                # Scale up for visibility
                scale_factor = max(1, 300 // max(w_roi, h_roi))
                hp_display = cv2.resize(hp_roi, (w_roi * scale_factor, h_roi * scale_factor), interpolation=cv2.INTER_NEAREST)

                # Add info
                cv2.putText(hp_display, f"HP ROI: [{x},{y},{w_roi},{h_roi}]", (5, 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

                # Analyze colors
                red_mask = _cv_in_range(hp_roi, _to_uint8_color(0, 0, 100), _to_uint8_color(80, 80, 255))
                red_pixels = int(np.count_nonzero(red_mask))
                total_pixels = w_roi * h_roi
                red_pct = (red_pixels / total_pixels) * 100 if total_pixels > 0 else 0

                cv2.putText(hp_display, f"Red pixels: {red_pct:.1f}%", (5, 35),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

                cv2.imshow("2. HP Bar ROI", hp_display)
                print(f"   HP ROI: {w_roi}x{h_roi} at ({x},{y}) - Red pixels: {red_pct:.1f}%")
            else:
                print("   ❌ HP ROI empty")
        except Exception as e:
            print(f"   ❌ HP ROI error: {e}")

        # 3. MP Bar
        try:
            x, y, w_roi, h_roi = configs['hpmp']['mp_roi']
            mp_roi = frame[y:y+h_roi, x:x+w_roi]

            if mp_roi.size > 0:
                scale_factor = max(1, 300 // max(w_roi, h_roi))
                mp_display = cv2.resize(mp_roi, (w_roi * scale_factor, h_roi * scale_factor), interpolation=cv2.INTER_NEAREST)

                cv2.putText(mp_display, f"MP ROI: [{x},{y},{w_roi},{h_roi}]", (5, 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

                # Analyze blue colors
                blue_mask = _cv_in_range(mp_roi, _to_uint8_color(100, 0, 0), _to_uint8_color(255, 80, 80))
                blue_pixels = int(np.count_nonzero(blue_mask))
                total_pixels = w_roi * h_roi
                blue_pct = (blue_pixels / total_pixels) * 100 if total_pixels > 0 else 0

                cv2.putText(mp_display, f"Blue pixels: {blue_pct:.1f}%", (5, 35),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)

                cv2.imshow("3. MP Bar ROI", mp_display)
                print(f"   MP ROI: {w_roi}x{h_roi} at ({x},{y}) - Blue pixels: {blue_pct:.1f}%")
            else:
                print("   ❌ MP ROI empty")
        except Exception as e:
            print(f"   ❌ MP ROI error: {e}")

        # 4. Minimap
        try:
            x, y, w_roi, h_roi = configs['minimap']['roi']
            minimap_roi = frame[y:y+h_roi, x:x+w_roi]

            if minimap_roi.size > 0:
                scale_factor = max(1, 400 // max(w_roi, h_roi))
                minimap_display = cv2.resize(minimap_roi, (w_roi * scale_factor, h_roi * scale_factor), interpolation=cv2.INTER_NEAREST)

                cv2.putText(minimap_display, f"Minimap: [{x},{y},{w_roi},{h_roi}]", (5, 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

                # Analyze bright pixels (character cross)
                gray = cv2.cvtColor(minimap_roi, cv2.COLOR_BGR2GRAY)
                bright_mask = _cv_in_range(gray, np.uint8(200), np.uint8(255))
                bright_pixels = int(np.count_nonzero(bright_mask))

                cv2.putText(minimap_display, f"Bright pixels: {bright_pixels}", (5, 35),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)

                cv2.imshow("4. Minimap ROI", minimap_display)
                print(f"   Minimap: {w_roi}x{h_roi} at ({x},{y}) - Bright pixels: {bright_pixels}")
            else:
                print("   ❌ Minimap ROI empty")
        except Exception as e:
            print(f"   ❌ Minimap ROI error: {e}")

        # 5. Battle List
        try:
            x, y, w_roi, h_roi = configs['combat']['battle_list_roi']
            battle_roi = frame[y:y+h_roi, x:x+w_roi]

            if battle_roi.size > 0:
                # Scale down if too big
                if w_roi > 400 or h_roi > 400:
                    scale = min(400/w_roi, 400/h_roi)
                    battle_display = cv2.resize(battle_roi, (int(w_roi*scale), int(h_roi*scale)))
                else:
                    battle_display = battle_roi.copy()

                cv2.putText(battle_display, f"Battle: [{x},{y},{w_roi},{h_roi}]", (5, 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

                # Analyze content
                gray = cv2.cvtColor(battle_roi, cv2.COLOR_BGR2GRAY)
                content_mask = _cv_in_range(gray, np.uint8(30), np.uint8(255))
                content_pixels = int(np.count_nonzero(content_mask))

                cv2.putText(battle_display, f"Content pixels: {content_pixels}", (5, 35),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

                cv2.imshow("5. Battle List ROI", battle_display)
                print(f"   Battle: {w_roi}x{h_roi} at ({x},{y}) - Content pixels: {content_pixels}")
            else:
                print("   ❌ Battle ROI empty")
        except Exception as e:
            print(f"   ❌ Battle ROI error: {e}")

        # 6. Status Icons
        try:
            x, y, w_roi, h_roi = configs['condition']['condition_icons_roi']
            status_roi = frame[y:y+h_roi, x:x+w_roi]

            if status_roi.size > 0:
                scale_factor = max(1, 300 // max(w_roi, h_roi))
                status_display = cv2.resize(status_roi, (w_roi * scale_factor, h_roi * scale_factor), interpolation=cv2.INTER_NEAREST)

                cv2.putText(status_display, f"Status: [{x},{y},{w_roi},{h_roi}]", (5, 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

                # Analyze colored pixels
                hsv = cv2.cvtColor(status_roi, cv2.COLOR_BGR2HSV)
                colored_mask = _cv_in_range(hsv[:, :, 1], np.uint8(80), np.uint8(255))
                colored_pixels = int(np.count_nonzero(colored_mask))

                cv2.putText(status_display, f"Colored pixels: {colored_pixels}", (5, 35),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1)

                cv2.imshow("6. Status Icons ROI", status_display)
                print(f"   Status: {w_roi}x{h_roi} at ({x},{y}) - Colored pixels: {colored_pixels}")
            else:
                print("   ❌ Status ROI empty")
        except Exception as e:
            print(f"   ❌ Status ROI error: {e}")

        # Wait for key press
        key = cv2.waitKey(1000) & 0xFF  # Update every 1 second
        if key == ord('q'):
            print("\n🛑 Saliendo...")
            break

    cv2.destroyAllWindows()
    print("✅ Debug completado")
    print()
    print("🔍 Análisis:")
    print("• Si las ventanas ROI muestran regiones incorrectas → coordenadas mal")
    print("• Si las regiones son correctas pero detection falla → ajustar thresholds")
    print("• Verificar que Tibia client esté visible en OBS projector")
    return 0

if __name__ == "__main__":
    exit(main())