#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_roi_alignment.py
-----------------------
Verifica automáticamente si las coordenadas ROI están alineadas correctamente.

Usage:
    python verify_roi_alignment.py
"""

import sys
import io
import json
import cv2
import numpy as np
from pathlib import Path
from typing import Any

# Fix Windows console encoding for emoji support
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def _cv_in_range(image: Any, lower: Any, upper: Any) -> np.ndarray:
    return cv2.inRange(image, lower, upper)

def main() -> int:
    print("🔍 ROI ALIGNMENT VERIFICATION")
    print("=" * 50)

    # Setup frame capture
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter
        frame_getter = build_frame_getter("mss")
    except Exception as e:
        print(f"❌ Frame capture error: {e}")
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
    except Exception as e:
        print(f"❌ Config error: {e}")
        return 1

    # Capture frame
    frame = frame_getter()
    if frame is None:
        print("❌ No frame captured")
        return 1

    h, w = frame.shape[:2]
    print(f"✅ Frame captured: {w}x{h}")

    # Save frame with ROI overlays for visual inspection
    frame_annotated = frame.copy()

    print("\n📐 ROI COORDINATE VERIFICATION:")

    # Check each ROI
    rois_to_check = [
        ("HP Bar", configs['hpmp']['hp_roi'], (0, 0, 255)),      # Red
        ("MP Bar", configs['hpmp']['mp_roi'], (255, 0, 0)),      # Blue
        ("Minimap", configs['minimap']['roi'], (0, 255, 0)),     # Green
        ("Battle List", configs['combat']['battle_list_roi'], (255, 255, 0)),  # Cyan
        ("Status Icons", configs['condition']['condition_icons_roi'], (255, 0, 255)),  # Magenta
    ]

    valid_rois = 0
    total_rois = len(rois_to_check)

    for name, (x, y, w, h), color in rois_to_check:
        # Boundary check
        if x < 0 or y < 0 or x + w > frame.shape[1] or y + h > frame.shape[0]:
            print(f"  ❌ {name:<12} - Out of bounds: [{x}, {y}, {w}, {h}]")
            continue

        # Extract ROI
        roi = frame[y:y+h, x:x+w]

        if roi.size == 0:
            print(f"  ❌ {name:<12} - Empty ROI: [{x}, {y}, {w}, {h}]")
            continue

        # Draw rectangle on annotated frame
        cv2.rectangle(frame_annotated, (x, y), (x+w, y+h), color, 2)
        cv2.putText(frame_annotated, name, (x, y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        # Basic content analysis
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        non_black = _cv_in_range(gray, np.uint8(10), np.uint8(255))
        content_pct = (np.count_nonzero(non_black) / (w * h)) * 100

        # Size validation
        size_ok = True
        if name in ["HP Bar", "MP Bar"] and (w < 100 or h < 5):
            size_ok = False
        elif name == "Minimap" and (w < 50 or h < 50):
            size_ok = False
        elif name == "Battle List" and (w < 100 or h < 100):
            size_ok = False
        elif name == "Status Icons" and (w < 50 or h < 10):
            size_ok = False

        if size_ok and content_pct > 5:  # Has some content
            print(f"  ✅ {name:<12} - Valid: [{x}, {y}, {w}, {h}] ({content_pct:.1f}% content)")
            valid_rois += 1
        else:
            print(f"  ⚠️  {name:<12} - Suspicious: [{x}, {y}, {w}, {h}] ({content_pct:.1f}% content)")

    # Save annotated frame
    output_path = Path("captures")
    output_path.mkdir(exist_ok=True)
    cv2.imwrite(str(output_path / "roi_alignment_check.png"), frame_annotated)

    print(f"\n📊 ALIGNMENT RESULTS:")
    print(f"   Valid ROIs: {valid_rois}/{total_rois}")
    print(f"   Annotated frame saved: captures/roi_alignment_check.png")

    # Specific game element checks
    print(f"\n🎮 GAME ELEMENT VERIFICATION:")

    # Check if HP/MP bars are in reasonable positions (top of screen)
    hp_y = configs['hpmp']['hp_roi'][1]
    mp_y = configs['hpmp']['mp_roi'][1]

    if hp_y < 100 and mp_y < 100:
        print(f"  ✅ HP/MP bars in top region (Y: {hp_y}, {mp_y})")
    else:
        print(f"  ❌ HP/MP bars not in expected top region (Y: {hp_y}, {mp_y})")

    # Check if minimap is in top-right
    minimap_x = configs['minimap']['roi'][0]
    minimap_y = configs['minimap']['roi'][1]

    if minimap_x > w * 0.8 and minimap_y < 200:
        print(f"  ✅ Minimap in top-right region ({minimap_x}, {minimap_y})")
    else:
        print(f"  ❌ Minimap not in expected top-right region ({minimap_x}, {minimap_y})")

    # Check if battle list is on right side
    battle_x = configs['combat']['battle_list_roi'][0]

    if battle_x > w * 0.7:
        print(f"  ✅ Battle list on right side (X: {battle_x})")
    else:
        print(f"  ❌ Battle list not on expected right side (X: {battle_x})")

    # Final diagnosis
    print(f"\n🔍 DIAGNOSIS:")

    if valid_rois == total_rois:
        print("  ✅ All ROI coordinates appear correct")
        print("  💡 If detection still fails, try:")
        print("     - python debug_roi_detection.py (visual debug)")
        print("     - python improved_player_status.py (better algorithms)")
    else:
        print("  ⚠️  Some ROI coordinates may be misaligned")
        print("  💡 Recommendations:")
        print("     - Use: python manual_roi_capture.py (recalibrate)")
        print("     - Check: captures/roi_alignment_check.png (visual verification)")
        print("     - Ensure Tibia client matches 1920x1080 OBS resolution")

    # Quick color analysis
    print(f"\n🎨 QUICK COLOR ANALYSIS:")

    # Sample HP bar area for red
    hp_x, hp_y, hp_w, hp_h = configs['hpmp']['hp_roi']
    hp_sample = frame[hp_y:hp_y+hp_h, hp_x:hp_x+hp_w]
    if hp_sample.size > 0:
        hp_hsv = cv2.cvtColor(hp_sample, cv2.COLOR_BGR2HSV)
        red_pixels = _cv_in_range(hp_hsv, np.array([0, 100, 100]), np.array([10, 255, 255]))
        red_count = np.count_nonzero(red_pixels)
        print(f"   HP region red pixels: {red_count}/{hp_w*hp_h} ({(red_count/(hp_w*hp_h)*100):.1f}%)")

    # Sample MP bar area for blue
    mp_x, mp_y, mp_w, mp_h = configs['hpmp']['mp_roi']
    mp_sample = frame[mp_y:mp_y+mp_h, mp_x:mp_x+mp_w]
    if mp_sample.size > 0:
        mp_hsv = cv2.cvtColor(mp_sample, cv2.COLOR_BGR2HSV)
        blue_pixels = _cv_in_range(mp_hsv, np.array([100, 100, 100]), np.array([130, 255, 255]))
        blue_count = np.count_nonzero(blue_pixels)
        print(f"   MP region blue pixels: {blue_count}/{mp_w*mp_h} ({(blue_count/(mp_w*mp_h)*100):.1f}%)")

    if valid_rois >= 4:
        return 0  # Success
    else:
        return 1  # Issues detected

if __name__ == "__main__":
    exit(main())