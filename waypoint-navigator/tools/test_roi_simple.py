#!/usr/bin/env python3
"""
test_roi_simple.py
------------------
Simple ROI test - captures one frame and shows all detection results.

Usage:
    python test_roi_simple.py
"""

import sys
import json
import numpy as np
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def load_config(filename: str) -> dict:
    """Load JSON config file."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading {filename}: {e}")
        return {}

def capture_frame_once() -> tuple[np.ndarray | None, Any]:
    """Test basic frame capture from OBS."""
    print("📸 Testing frame capture...")

    try:
        from frame_capture import build_frame_getter
        frame_getter = build_frame_getter("mss")
        frame = frame_getter()

        if frame is not None:
            h, w = frame.shape[:2]
            print(f"✅ Frame captured: {w}x{h} pixels")
            return frame, frame_getter
        else:
            print("❌ No frame captured")
            return None, None
    except Exception as e:
        print(f"❌ Frame capture failed: {e}")
        return None, None

def extract_roi(frame: np.ndarray | None, roi_name: str, roi_coords: list[int]) -> bool:
    """Test ROI extraction from frame."""
    if frame is None:
        return False

    try:
        x, y, w, h = roi_coords
        roi_frame = frame[y:y+h, x:x+w]

        if roi_frame.size > 0:
            roi_h, roi_w = roi_frame.shape[:2]
            print(f"  ✅ {roi_name:<15} ROI extracted: {roi_w}x{roi_h} at ({x},{y})")
            return True
        else:
            print(f"  ❌ {roi_name:<15} ROI empty")
            return False
    except Exception as e:
        print(f"  ❌ {roi_name:<15} ROI failed: {e}")
        return False

def run_minimap_detection(frame: np.ndarray, frame_getter: Any) -> None:
    """Test minimap position detection."""
    print("\n🗺️  Testing minimap detection...")

    try:
        from minimap_radar import MinimapRadar
        config = load_config("minimap_config.json")

        radar = MinimapRadar(config, frame_getter=frame_getter)
        result = radar.read()

        if result and result.coordinate:
            coord = result.coordinate
            confidence = f"{result.confidence:.3f}" if result.confidence else "N/A"
            print(f"  ✅ Position detected: ({coord.x}, {coord.y}, floor {coord.z}) confidence={confidence}")
        else:
            print(f"  🔍 No position detected (character may not be visible)")

    except Exception as e:
        print(f"  ❌ Minimap detection failed: {e}")

def run_hpmp_detection(frame: np.ndarray, frame_getter: Any) -> None:
    """Test HP/MP bar detection."""
    print("\n❤️💙 Testing HP/MP detection...")

    try:
        from hpmp_detector import HPMPDetector
        config = load_config("hpmp_config.json")

        detector = HPMPDetector(config, frame_getter=frame_getter)
        reading = detector.read_bars()

        if reading and reading.success:
            if reading.hp is not None and reading.hp_max is not None:
                hp_pct = (reading.hp / reading.hp_max * 100) if reading.hp_max > 0 else 0
                print(f"  ✅ HP: {reading.hp}/{reading.hp_max} ({hp_pct:.1f}%)")
            else:
                print(f"  🔍 HP: No data detected")

            if reading.mp is not None and reading.mp_max is not None:
                mp_pct = (reading.mp / reading.mp_max * 100) if reading.mp_max > 0 else 0
                print(f"  ✅ MP: {reading.mp}/{reading.mp_max} ({mp_pct:.1f}%)")
            else:
                print(f"  🔍 MP: No data detected")
        else:
            print(f"  🔍 No HP/MP readings available")

    except Exception as e:
        print(f"  ❌ HP/MP detection failed: {e}")

def main() -> int:
    print("🧪 Simple ROI Test")
    print("=" * 40)
    print("Testing ROI configurations with single frame capture")
    print("")

    # 1. Test frame capture
    frame, frame_getter = capture_frame_once()
    if frame is None:
        print("❌ Cannot capture frame. Check OBS setup.")
        return 1

    print(f"\n📐 Testing ROI coordinate extraction...")

    # 2. Test all ROI extractions
    configs = [
        ("HP Bar", "hpmp_config.json", "hp_roi"),
        ("MP Bar", "hpmp_config.json", "mp_roi"),
        ("Battle List", "combat_config.json", "battle_list_roi"),
        ("Chat", "chat_config.json", "chat_roi"),
        ("Status Icons", "condition_config.json", "condition_icons_roi"),
        ("Minimap", "minimap_config.json", "roi"),
    ]

    roi_success = 0
    for name, config_file, roi_key in configs:
        config = load_config(config_file)
        if roi_key in config:
            if extract_roi(frame, name, config[roi_key]):
                roi_success += 1
        else:
            print(f"  ❌ {name:<15} ROI key '{roi_key}' not found in {config_file}")

    print(f"\n📊 ROI Extraction: {roi_success}/{len(configs)} successful")

    # 3. Test live detection modules
    if frame_getter:
        run_minimap_detection(frame, frame_getter)
        run_hpmp_detection(frame, frame_getter)

    print(f"\n✅ ROI testing completed!")
    print(f"📝 To run continuous monitoring: python test_roi_live.py")

    return 0

if __name__ == "__main__":
    exit(main())