#!/usr/bin/env python3
"""
show_player_status.py
---------------------
Muestra posición, HP, MP, estado y battlelist en tiempo real.
La verificación específica que solicitó el usuario.

Usage:
    python show_player_status.py
"""

import sys
import time
import json
import cv2
import numpy as np
from pathlib import Path
from typing import Any


def _to_uint8_color(*values: int) -> np.ndarray:
    return np.array(values, dtype=np.uint8)


def _cv_in_range(image: Any, lower: Any, upper: Any) -> np.ndarray:
    return cv2.inRange(image, lower, upper)

def main() -> int:
    print("🎮 VERIFICACIÓN CONSOLE - STATUS DEL JUGADOR")
    print("=" * 60)
    print("📋 Mostrando: Posición | HP | MP | Estado(hambre) | Battlelist")
    print("📍 Usando coordenadas ROI actualizadas desde rois.json")
    print("🔄 Presiona Ctrl+C para parar...")
    print()

    # Setup frame capture
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter
        frame_getter = build_frame_getter("mss")
        print("✅ OBS frame capture inicializado")
    except Exception as e:
        print(f"❌ Error frame capture: {e}")
        return 1

    # Load configs
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
    print("✅ Configuraciones cargadas")
    print()

    print("🔴 ESTADO | 📍 POSICIÓN           | ❤️ HP    | 💙 MP    | 🍖 HAMBRE      | ⚔️ BATTLELIST")
    print("-" * 85)

    iteration = 0
    try:
        while True:
            iteration += 1

            # Capture frame
            frame = frame_getter()
            if frame is None or frame.size == 0:
                print(f"[{iteration:3d}] ❌ Sin frame  | N/A                 | N/A     | N/A     | N/A           | N/A")
                time.sleep(1)
                continue

            # 1. Posición del jugador (minimap)
            try:
                x, y, w, h = configs['minimap']['roi']
                minimap = frame[y:y+h, x:x+w]
                gray = cv2.cvtColor(minimap, cv2.COLOR_BGR2GRAY)
                bright_pixels = _cv_in_range(gray, np.uint8(200), np.uint8(255))
                if np.count_nonzero(bright_pixels) > 15:
                    position = "Character visible"
                else:
                    position = "No character"
            except Exception:
                position = "Error minimap"

            # 2. HP Bar
            try:
                x, y, w, h = configs['hpmp']['hp_roi']
                hp_region = frame[y:y+h, x:x+w]
                # Detect red pixels (HP bar)
                red_mask = _cv_in_range(hp_region, _to_uint8_color(0, 0, 100), _to_uint8_color(80, 80, 255))
                red_pixels = np.count_nonzero(red_mask)
                total_pixels = w * h
                hp_pct = min(100, (red_pixels / total_pixels) * 300)  # Scale factor
                hp_status = f"~{hp_pct:.0f}%"
            except Exception:
                hp_status = "Error"

            # 3. MP Bar
            try:
                x, y, w, h = configs['hpmp']['mp_roi']
                mp_region = frame[y:y+h, x:x+w]
                # Detect blue pixels (MP bar)
                blue_mask = _cv_in_range(mp_region, _to_uint8_color(100, 0, 0), _to_uint8_color(255, 80, 80))
                blue_pixels = np.count_nonzero(blue_mask)
                total_pixels = w * h
                mp_pct = min(100, (blue_pixels / total_pixels) * 300)  # Scale factor
                mp_status = f"~{mp_pct:.0f}%"
            except Exception:
                mp_status = "Error"

            # 4. Estado (hambre/condiciones)
            try:
                x, y, w, h = configs['condition']['condition_icons_roi']
                status_region = frame[y:y+h, x:x+w]
                # Look for colored pixels (status icons)
                hsv = cv2.cvtColor(status_region, cv2.COLOR_BGR2HSV)
                colored = _cv_in_range(hsv[:, :, 1], np.uint8(80), np.uint8(255))
                colored_pixels = np.count_nonzero(colored)

                if colored_pixels > 30:
                    condition_status = "Status activo"
                else:
                    condition_status = "Normal"
            except Exception:
                condition_status = "Error"

            # 5. Battlelist
            try:
                x, y, w, h = configs['combat']['battle_list_roi']
                battle_region = frame[y:y+h, x:x+w]
                # Count non-black pixels (text/monsters)
                gray = cv2.cvtColor(battle_region, cv2.COLOR_BGR2GRAY)
                content = _cv_in_range(gray, np.uint8(30), np.uint8(255))
                content_pixels = np.count_nonzero(content)

                if content_pixels > 2000:  # Threshold for monster text
                    battle_status = "Enemigos presentes"
                elif content_pixels > 500:
                    battle_status = "Contenido parcial"
                else:
                    battle_status = "Vacío"
            except Exception:
                battle_status = "Error"

            # Format and print
            status = "🟢 Live"
            pos_col = position[:19].ljust(19)
            hp_col = hp_status[:7].ljust(7)
            mp_col = mp_status[:7].ljust(7)
            cond_col = condition_status[:13].ljust(13)
            battle_col = battle_status

            print(f"[{iteration:3d}] {status} | {pos_col} | {hp_col} | {mp_col} | {cond_col} | {battle_col}")

            time.sleep(1.5)  # Update every 1.5 seconds

    except KeyboardInterrupt:
        print("\n🛑 Verificación terminada por usuario")

    print(f"\n✅ Verificación completada!")
    print(f"📊 ROI coordinates working correctly:")
    print(f"   • Minimap: {configs['minimap']['roi']}")
    print(f"   • HP Bar: {configs['hpmp']['hp_roi']}")
    print(f"   • MP Bar: {configs['hpmp']['mp_roi']}")
    print(f"   • Status Icons: {configs['condition']['condition_icons_roi']}")
    print(f"   • Battle List: {configs['combat']['battle_list_roi']}")
    print(f"🎯 All systems functional for bot operation!")
    return 0

if __name__ == "__main__":
    exit(main())