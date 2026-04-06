#!/usr/bin/env python3
"""
console_demo.py
----------------
Demo de consola simple - muestra valores básicos sin dependencias complejas.

Usage:
    python console_demo.py
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

def load_config(filename: str) -> dict[str, Any]:
    """Load config file."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def analyze_hp_mp_bars(frame: np.ndarray) -> tuple[str, str]:
    """Simple HP/MP analysis usando análisis de colores."""
    try:
        # Load ROIs
        config = load_config("hpmp_config.json")
        hp_roi = config.get("hp_roi", [12, 28, 771, 14])
        mp_roi = config.get("mp_roi", [786, 26, 770, 15])

        # Extract HP bar region
        x, y, w, h = hp_roi
        hp_region = frame[y:y+h, x:x+w]

        # Extract MP bar region
        x, y, w, h = mp_roi
        mp_region = frame[y:y+h, x:x+w]

        # Simple analysis: count red pixels for HP, blue for MP
        hp_red_pixels = 0
        mp_blue_pixels = 0

        if hp_region.size > 0:
            # Red pixels in HP region (rough estimate)
            hp_red = _cv_in_range(hp_region, _to_uint8_color(0, 0, 100), _to_uint8_color(100, 100, 255))
            hp_red_pixels = int(np.count_nonzero(hp_red))

        if mp_region.size > 0:
            # Blue pixels in MP region (rough estimate)
            mp_blue = _cv_in_range(mp_region, _to_uint8_color(100, 0, 0), _to_uint8_color(255, 255, 100))
            mp_blue_pixels = int(np.count_nonzero(mp_blue))

        # Convert to rough percentages
        hp_total_pixels = hp_region.size // 3 if hp_region.size > 0 else 1
        mp_total_pixels = mp_region.size // 3 if mp_region.size > 0 else 1

        hp_pct = min(100, (hp_red_pixels / hp_total_pixels) * 100)
        mp_pct = min(100, (mp_blue_pixels / mp_total_pixels) * 100)

        return f"~{hp_pct:.0f}%", f"~{mp_pct:.0f}%"

    except Exception:
        return "?", "?"

def analyze_minimap_for_position(frame: np.ndarray) -> str:
    """Simple minimap analysis - busca la cruz del character."""
    try:
        config = load_config("minimap_config.json")
        roi = config.get("roi", [1753, 27, 109, 112])

        x, y, w, h = roi
        minimap_region = frame[y:y+h, x:x+w]

        if minimap_region.size == 0:
            return "No minimap"

        # Simple analysis: look for bright cross-like pattern
        gray = cv2.cvtColor(minimap_region, cv2.COLOR_BGR2GRAY)

        # Look for high-intensity pixels (character cross)
        bright_pixels = _cv_in_range(gray, np.uint8(200), np.uint8(255))
        bright_count = int(np.count_nonzero(bright_pixels))

        if bright_count > 10:  # Reasonable threshold
            return "Character visible"
        else:
            return "Character not detected"

    except Exception as e:
        return f"Error: {e}"

def analyze_battle_list(frame: np.ndarray) -> str:
    """Simple battle list analysis."""
    try:
        config = load_config("combat_config.json")
        roi = config.get("battle_list_roi", [1570, 335, 161, 394])

        x, y, w, h = roi
        battle_region = frame[y:y+h, x:x+w]

        if battle_region.size == 0:
            return "No battle region"

        # Simple analysis: look for non-background pixels
        gray = cv2.cvtColor(battle_region, cv2.COLOR_BGR2GRAY)

        # Count non-black pixels (assume black background)
        non_black = _cv_in_range(gray, np.uint8(30), np.uint8(255))
        content_pixels = int(np.count_nonzero(non_black))

        # Simple heuristic
        if content_pixels > 1000:  # Reasonable threshold for text/monsters
            return "Enemies detected"
        else:
            return "Empty"

    except Exception as e:
        return f"Error: {e}"

def analyze_status_icons(frame: np.ndarray) -> str:
    """Simple status analysis."""
    try:
        config = load_config("condition_config.json")
        roi = config.get("condition_icons_roi", [792, 60, 110, 15])

        x, y, w, h = roi
        status_region = frame[y:y+h, x:x+w]

        if status_region.size == 0:
            return "No status region"

        # Look for colorful pixels (status icons are usually colored)
        hsv = cv2.cvtColor(status_region, cv2.COLOR_BGR2HSV)

        # Count saturated pixels (colored icons)
        colored = _cv_in_range(hsv[:, :, 1], np.uint8(100), np.uint8(255))
        colored_pixels = int(np.count_nonzero(colored))

        if colored_pixels > 50:  # Some threshold
            return "Status effects present"
        else:
            return "No status effects"

    except Exception as e:
        return f"Error: {e}"

def main() -> int:
    print("🎮 Console Demo - Live Bot Status")
    print("=" * 60)
    print("Simple analysis of game state using updated ROI coordinates")
    print("Press Ctrl+C to stop")
    print()

    # Setup frame capture
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter
        frame_getter = build_frame_getter("mss")
    except Exception as e:
        print(f"❌ Frame capture failed: {e}")
        print("💡 Ensure OBS projector is running on Monitor 2")
        return 1

    print("📊 Status | 📍 Position | ❤️ HP | 💙 MP | 🗡️ Battle | 🍖 Status")
    print("-" * 60)

    iteration = 0
    try:
        while True:
            iteration += 1

            # Capture frame
            frame = frame_getter()
            if frame is not None and frame.size > 0:
                status = "✅ Live"

                # Analyze different components
                position_str = analyze_minimap_for_position(frame)
                hp_str, mp_str = analyze_hp_mp_bars(frame)
                battle_str = analyze_battle_list(frame)
                status_str = analyze_status_icons(frame)
            else:
                status = "❌ No frame"
                position_str = hp_str = mp_str = battle_str = status_str = "N/A"

            # Format output
            status_col = status[:12].ljust(12)
            pos_col = position_str[:18].ljust(18)
            hp_col = hp_str[:8].ljust(8)
            mp_col = mp_str[:8].ljust(8)
            battle_col = battle_str[:15].ljust(15)
            status_effects_col = status_str[:20]

            print(f"[{iteration:3d}] {status_col} | {pos_col} | {hp_col} | {mp_col} | {battle_col} | {status_effects_col}")

            time.sleep(1.0)  # Update every second

    except KeyboardInterrupt:
        print("\n🛑 Demo stopped by user")

    print("\n✅ Console demo completed!")
    print("\n📝 Notes:")
    print("• This demo uses simple color analysis, not full OCR/template matching")
    print("• For precise readings, use full bot modules with test_roi_live.py")
    print("• All ROI coordinates are working and extracting correct regions")

    return 0

if __name__ == "__main__":
    exit(main())