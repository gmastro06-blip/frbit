#!/usr/bin/env python3
"""
improved_player_status.py
-------------------------
Versión mejorada con algoritmos de detección más precisos.

Usage:
    python improved_player_status.py
"""

import sys
import time
import json
import cv2
import numpy as np
from pathlib import Path

def analyze_hp_mp_bars(frame: np.ndarray, configs: dict) -> tuple:
    """Análisis mejorado de barras HP/MP."""
    try:
        # HP Bar analysis
        x, y, w, h = configs['hpmp']['hp_roi']
        hp_region = frame[y:y+h, x:x+w]

        # MP Bar analysis
        x2, y2, w2, h2 = configs['hpmp']['mp_roi']
        mp_region = frame[y2:y2+h2, x2:x2+w2]

        hp_result = "0%"
        mp_result = "0%"

        if hp_region.size > 0:
            # Convert to HSV for better color detection
            hp_hsv = cv2.cvtColor(hp_region, cv2.COLOR_BGR2HSV)

            # Red color range in HSV (more accurate than BGR)
            red_lower1 = np.array([0, 100, 100])    # Lower red range
            red_upper1 = np.array([10, 255, 255])
            red_lower2 = np.array([160, 100, 100])  # Upper red range
            red_upper2 = np.array([180, 255, 255])

            red_mask1 = cv2.inRange(hp_hsv, red_lower1, red_upper1)
            red_mask2 = cv2.inRange(hp_hsv, red_lower2, red_upper2)
            red_mask = cv2.bitwise_or(red_mask1, red_mask2)

            red_pixels = np.count_nonzero(red_mask)
            total_pixels = w * h

            if total_pixels > 0 and red_pixels > 5:  # Minimum threshold
                hp_pct = min(100, (red_pixels / total_pixels) * 100)
                hp_result = f"{int(hp_pct)}%"

        if mp_region.size > 0:
            # Blue color detection in HSV
            mp_hsv = cv2.cvtColor(mp_region, cv2.COLOR_BGR2HSV)

            # Blue color range
            blue_lower = np.array([100, 100, 100])  # Blue range
            blue_upper = np.array([130, 255, 255])

            blue_mask = cv2.inRange(mp_hsv, blue_lower, blue_upper)
            blue_pixels = np.count_nonzero(blue_mask)
            total_pixels = w2 * h2

            if total_pixels > 0 and blue_pixels > 5:  # Minimum threshold
                mp_pct = min(100, (blue_pixels / total_pixels) * 100)
                mp_result = f"{int(mp_pct)}%"

        return hp_result, mp_result

    except Exception as e:
        return f"Error: {e}", f"Error: {e}"

def analyze_minimap_position(frame: np.ndarray, configs: dict) -> str:
    """Análisis mejorado de posición en minimap."""
    try:
        x, y, w, h = configs['minimap']['roi']
        minimap_region = frame[y:y+h, x:x+w]

        if minimap_region.size == 0:
            return "ROI empty"

        # Convert to grayscale
        gray = cv2.cvtColor(minimap_region, cv2.COLOR_BGR2GRAY)

        # Look for very bright pixels (character cross)
        _, binary = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)

        # Count connected components (cross pattern)
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

        # Look for cross-like pattern (small bright regions)
        bright_regions = 0
        for i in range(1, num_labels):  # Skip background
            area = stats[i, cv2.CC_STAT_AREA]
            if 2 <= area <= 20:  # Typical size for character cross pixels
                bright_regions += 1

        if bright_regions >= 2:  # Cross typically has 2+ components
            return "Character detected"
        elif np.count_nonzero(binary) > 10:
            return "Partial detection"
        else:
            return "No character"

    except Exception as e:
        return f"Error: {e}"

def analyze_status_conditions(frame: np.ndarray, configs: dict) -> str:
    """Análisis mejorado de condiciones de estado (hambre, etc.)."""
    try:
        x, y, w, h = configs['condition']['condition_icons_roi']
        status_region = frame[y:y+h, x:x+w]

        if status_region.size == 0:
            return "ROI empty"

        # Convert to HSV for better color analysis
        hsv = cv2.cvtColor(status_region, cv2.COLOR_BGR2HSV)

        # Look for saturated colors (status icons are typically colorful)
        # Exclude grayscale pixels
        saturation = hsv[:, :, 1]
        high_sat_mask = saturation > 100  # High saturation threshold

        # Also check for specific condition colors
        # Hunger icon is typically orange/yellow
        hunger_lower = np.array([15, 100, 100])  # Orange/yellow range
        hunger_upper = np.array([35, 255, 255])
        hunger_mask = cv2.inRange(hsv, hunger_lower, hunger_upper)

        # Poison is typically green
        poison_lower = np.array([40, 100, 100])
        poison_upper = np.array([80, 255, 255])
        poison_mask = cv2.inRange(hsv, poison_lower, poison_upper)

        # Count pixels
        high_sat_pixels = np.count_nonzero(high_sat_mask)
        hunger_pixels = np.count_nonzero(hunger_mask)
        poison_pixels = np.count_nonzero(poison_mask)

        # Decision logic
        if hunger_pixels > 15:
            return "Hungry: True"
        elif poison_pixels > 15:
            return "Poisoned: True"
        elif high_sat_pixels > 20:
            return "Unknown status: True"
        else:
            return "Status: False"

    except Exception as e:
        return f"Error: {e}"

def analyze_battle_list(frame: np.ndarray, configs: dict) -> str:
    """Análisis mejorado de battle list."""
    try:
        x, y, w, h = configs['combat']['battle_list_roi']
        battle_region = frame[y:y+h, x:x+w]

        if battle_region.size == 0:
            return "ROI empty"

        # Convert to grayscale
        gray = cv2.cvtColor(battle_region, cv2.COLOR_BGR2GRAY)

        # Improved text detection
        # Look for text-like patterns (not just non-black pixels)
        _, binary = cv2.threshold(gray, 50, 255, cv2.THRESH_BINARY)

        # Count connected components that could be text
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

        text_regions = 0
        total_text_area = 0

        for i in range(1, num_labels):  # Skip background
            area = stats[i, cv2.CC_STAT_AREA]
            width = stats[i, cv2.CC_STAT_WIDTH]
            height = stats[i, cv2.CC_STAT_HEIGHT]

            # Text-like characteristics
            if 10 <= area <= 500 and 3 <= width <= 100 and 5 <= height <= 30:
                aspect_ratio = width / height
                if 0.3 <= aspect_ratio <= 10:  # Reasonable aspect ratio for text
                    text_regions += 1
                    total_text_area += area

        # Decision logic
        if text_regions >= 3 and total_text_area > 200:
            return "Enemies present"
        elif text_regions >= 1 and total_text_area > 80:
            return "Possible enemy"
        else:
            return "Empty"

    except Exception as e:
        return f"Error: {e}"

def main() -> int:
    print("🎮 IMPROVED PLAYER STATUS VERIFICATION")
    print("=" * 70)
    print("📋 Usando algoritmos mejorados de detección")
    print("🔄 Presiona Ctrl+C para parar...")
    print()

    # Setup
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter
        frame_getter = build_frame_getter("mss")
        print("✅ Frame capture initialized")
    except Exception as e:
        print(f"❌ Frame capture error: {e}")
        return 1

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

    print("💡 TIP: Ejecuta 'python debug_roi_detection.py' para ver las capturas ROI")
    print()

    # Header
    print("🔴 STATUS | 📍 POSITION         | ❤️ HP   | 💙 MP   | 🍖 CONDITIONS     | ⚔️ BATTLELIST")
    print("-" * 85)

    iteration = 0
    try:
        while True:
            iteration += 1

            # Capture frame
            frame = frame_getter()
            if frame is None or frame.size == 0:
                status = "❌ No frame"
                position = hp = mp = conditions = battlelist = "N/A"
            else:
                status = "🟢 Live"

                # Improved analysis
                position = analyze_minimap_position(frame, configs)
                hp, mp = analyze_hp_mp_bars(frame, configs)
                conditions = analyze_status_conditions(frame, configs)
                battlelist = analyze_battle_list(frame, configs)

            # Format columns
            status_col = status[:11].ljust(11)
            pos_col = position[:19].ljust(19)
            hp_col = hp[:6].ljust(6)
            mp_col = mp[:6].ljust(6)
            cond_col = conditions[:17].ljust(17)
            battle_col = battlelist

            print(f"[{iteration:3d}] {status_col} | {pos_col} | {hp_col} | {mp_col} | {cond_col} | {battle_col}")

            time.sleep(1.0)  # Update every second

    except KeyboardInterrupt:
        print("\n🛑 Monitoring stopped by user")

    print(f"\n📊 Debug recommendations:")
    print(f"   • Run: python debug_roi_detection.py  (shows ROI captures)")
    print(f"   • Ensure Tibia character is logged in and visible")
    print(f"   • Verify OBS projector is showing Tibia on Monitor 2")
    print(f"   • Check ROI coordinates align with actual game elements")
    return 0

if __name__ == "__main__":
    exit(main())