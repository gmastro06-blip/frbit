#!/usr/bin/env python3
"""
single_detection_test.py
-------------------------
Una sola captura y análisis para ver exactly what's happening.
"""

import sys
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
    print("🧪 SINGLE DETECTION TEST")
    print("=" * 40)

    # Setup
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter
        frame_getter = build_frame_getter("mss")
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
    except Exception as e:
        print(f"❌ Config error: {e}")
        return 1

    # Single capture
    frame = frame_getter()
    if frame is None:
        print("❌ No frame captured")
        return 1

    print(f"✅ Frame: {frame.shape[1]}x{frame.shape[0]}")

    # ---- HP BAR ANALYSIS ----
    print(f"\n❤️ HP BAR ANALYSIS:")
    x, y, w, h = configs['hpmp']['hp_roi']
    hp_region = frame[y:y+h, x:x+w]

    print(f"   ROI: [{x}, {y}, {w}, {h}] = {w}x{h} region")

    if hp_region.size > 0:
        # Save HP region for inspection
        cv2.imwrite("captures/debug_hp_region.png", hp_region)

        # Color analysis
        mean_color = np.mean(hp_region, axis=(0, 1))  # BGR
        print(f"   Mean color (BGR): [{mean_color[0]:.1f}, {mean_color[1]:.1f}, {mean_color[2]:.1f}]")

        # Check for different color ranges
        red_bgr = _cv_in_range(hp_region, _to_uint8_color(0, 0, 100), _to_uint8_color(100, 100, 255))
        red_count = np.count_nonzero(red_bgr)

        green_bgr = _cv_in_range(hp_region, _to_uint8_color(0, 100, 0), _to_uint8_color(100, 255, 100))
        green_count = np.count_nonzero(green_bgr)

        # HSV analysis
        hp_hsv = cv2.cvtColor(hp_region, cv2.COLOR_BGR2HSV)
        red_hsv = _cv_in_range(hp_hsv, _to_uint8_color(0, 50, 50), _to_uint8_color(10, 255, 255))
        red_hsv_count = np.count_nonzero(red_hsv)

        print(f"   Red pixels (BGR): {red_count}/{w*h} ({(red_count/(w*h)*100):.1f}%)")
        print(f"   Green pixels (BGR): {green_count}/{w*h} ({(green_count/(w*h)*100):.1f}%)")
        print(f"   Red pixels (HSV): {red_hsv_count}/{w*h} ({(red_hsv_count/(w*h)*100):.1f}%)")

        # Check if it's mostly dark/background
        dark_pixels = _cv_in_range(cv2.cvtColor(hp_region, cv2.COLOR_BGR2GRAY), np.uint8(0), np.uint8(50))
        dark_count = np.count_nonzero(dark_pixels)
        print(f"   Dark pixels: {dark_count}/{w*h} ({(dark_count/(w*h)*100):.1f}%)")

    # ---- MP BAR ANALYSIS ----
    print(f"\n💙 MP BAR ANALYSIS:")
    x, y, w, h = configs['hpmp']['mp_roi']
    mp_region = frame[y:y+h, x:x+w]

    print(f"   ROI: [{x}, {y}, {w}, {h}] = {w}x{h} region")

    if mp_region.size > 0:
        cv2.imwrite("captures/debug_mp_region.png", mp_region)

        mean_color = np.mean(mp_region, axis=(0, 1))
        print(f"   Mean color (BGR): [{mean_color[0]:.1f}, {mean_color[1]:.1f}, {mean_color[2]:.1f}]")

        # Blue analysis
        blue_bgr = _cv_in_range(mp_region, _to_uint8_color(100, 0, 0), _to_uint8_color(255, 100, 100))
        blue_count = np.count_nonzero(blue_bgr)

        mp_hsv = cv2.cvtColor(mp_region, cv2.COLOR_BGR2HSV)
        blue_hsv = _cv_in_range(mp_hsv, _to_uint8_color(100, 50, 50), _to_uint8_color(130, 255, 255))
        blue_hsv_count = np.count_nonzero(blue_hsv)

        print(f"   Blue pixels (BGR): {blue_count}/{w*h} ({(blue_count/(w*h)*100):.1f}%)")
        print(f"   Blue pixels (HSV): {blue_hsv_count}/{w*h} ({(blue_hsv_count/(w*h)*100):.1f}%)")

    # ---- MINIMAP ANALYSIS ----
    print(f"\n🗺️ MINIMAP ANALYSIS:")
    x, y, w, h = configs['minimap']['roi']
    minimap_region = frame[y:y+h, x:x+w]

    print(f"   ROI: [{x}, {y}, {w}, {h}] = {w}x{h} region")

    if minimap_region.size > 0:
        cv2.imwrite("captures/debug_minimap_region.png", minimap_region)

        gray = cv2.cvtColor(minimap_region, cv2.COLOR_BGR2GRAY)

        # Different brightness thresholds
        for threshold in [200, 220, 240, 250]:
            bright = _cv_in_range(gray, np.uint8(threshold), np.uint8(255))
            bright_count = np.count_nonzero(bright)
            print(f"   Pixels >{threshold}: {bright_count} ({(bright_count/(w*h)*100):.1f}%)")

    # ---- BATTLE LIST ANALYSIS ----
    print(f"\n⚔️ BATTLE LIST ANALYSIS:")
    x, y, w, h = configs['combat']['battle_list_roi']
    battle_region = frame[y:y+h, x:x+w]

    print(f"   ROI: [{x}, {y}, {w}, {h}] = {w}x{h} region")

    if battle_region.size > 0:
        cv2.imwrite("captures/debug_battle_region.png", battle_region)

        gray = cv2.cvtColor(battle_region, cv2.COLOR_BGR2GRAY)

        # Text-like content analysis
        non_black = _cv_in_range(gray, np.uint8(30), np.uint8(255))
        content_count = np.count_nonzero(non_black)
        print(f"   Non-black pixels: {content_count}/{w*h} ({(content_count/(w*h)*100):.1f}%)")

        # Look for text patterns
        _, binary = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY)
        text_pixels = np.count_nonzero(binary)
        print(f"   Text-like pixels: {text_pixels}/{w*h} ({(text_pixels/(w*h)*100):.1f}%)")

    print(f"\n📁 Debug images saved to captures/:")
    print(f"   • debug_hp_region.png")
    print(f"   • debug_mp_region.png")
    print(f"   • debug_minimap_region.png")
    print(f"   • debug_battle_region.png")
    print(f"\n💡 Check these images to see exactly what the bot is capturing!")
    return 0

if __name__ == "__main__":
    exit(main())