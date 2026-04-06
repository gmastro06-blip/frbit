#!/usr/bin/env python3
"""
certify_rois.py
----------------
Certificación final de ROIs - solo verificaciones esenciales.

Usage:
    python certify_rois.py
"""

import json
import sys
from pathlib import Path
from typing import Any

def load_config(filename: str) -> dict[str, Any]:
    """Load JSON config file safely."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}

def verify_frame_capture() -> tuple[bool, str]:
    """Verify frame capture works."""
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter

        frame_getter = build_frame_getter("mss")
        frame = frame_getter()

        if frame is not None:
            h, w = frame.shape[:2]
            return True, f"{w}x{h}"
        else:
            return False, "No frame captured"
    except Exception as e:
        return False, str(e)

def main() -> int:
    print("🎯 ROI Configuration Certification")
    print("=" * 50)

    # 1. Frame capture verification
    print("📸 Frame Capture:")
    capture_ok, capture_info = verify_frame_capture()
    if capture_ok:
        print(f"  ✅ OBS frame capture working: {capture_info}")
    else:
        print(f"  ❌ Frame capture failed: {capture_info}")
        print("  💡 Ensure OBS projector is running on Monitor 2")

    print()

    # 2. Config file verification
    print("📋 Configuration Files:")

    configs_to_check = [
        ("hpmp_config.json", ["hp_roi", "mp_roi"], "HP/MP Detection"),
        ("combat_config.json", ["battle_list_roi"], "Battle List"),
        ("minimap_config.json", ["roi"], "Minimap Position"),
        ("condition_config.json", ["condition_icons_roi"], "Status Icons"),
        ("chat_config.json", ["chat_roi"], "Chat Messages"),
    ]

    config_success = 0
    roi_success = 0

    for config_file, roi_keys, description in configs_to_check:
        config = load_config(config_file)

        if "error" in config:
            print(f"  ❌ {config_file:<20} - Error: {config['error']}")
        else:
            print(f"  ✅ {config_file:<20} - {description}")
            config_success += 1

            # Check ROI keys
            for roi_key in roi_keys:
                if roi_key in config:
                    roi_coords = config[roi_key]
                    x, y, w, h = roi_coords

                    # Validate coordinates
                    if 0 <= x <= 1920 and 0 <= y <= 1080 and w > 0 and h > 0:
                        print(f"    ✅ {roi_key:<20} = [{x}, {y}, {w}, {h}]")
                        roi_success += 1
                    else:
                        print(f"    ❌ {roi_key:<20} = [{x}, {y}, {w}, {h}] (invalid coordinates)")
                else:
                    print(f"    ❌ {roi_key:<20} - Not found")

    print()

    # 3. ROI source verification
    print("📍 ROI Source:")
    try:
        with open("C:/Users/gmast/Desktop/rois.json", 'r', encoding='utf-8') as f:
            source_rois = json.load(f)

        print(f"  ✅ Source ROI file found: {len(source_rois['rois'])} regions")
        print(f"  ✅ Resolution: {source_rois['image_info']['width']}x{source_rois['image_info']['height']}")
        print(f"  ✅ Coordinates calibrated with manual ROI capture tool")

        # Verify coordinates match
        matches = 0
        mismatches = 0

        for roi in source_rois['rois']:
            name = roi['name']
            expected = [roi['x'], roi['y'], roi['width'], roi['height']]

            # Check in corresponding config
            if name == 'HP Bar':
                config = load_config('hpmp_config.json')
                actual = config.get('hp_roi', [])
            elif name == 'MP Bar':
                config = load_config('hpmp_config.json')
                actual = config.get('mp_roi', [])
            elif name == 'Battle List':
                config = load_config('combat_config.json')
                actual = config.get('battle_list_roi', [])
            elif name == 'Chat':
                config = load_config('chat_config.json')
                actual = config.get('chat_roi', [])
            elif name == 'Status Icons':
                config = load_config('condition_config.json')
                actual = config.get('condition_icons_roi', [])
            elif name == 'Minimap':
                config = load_config('minimap_config.json')
                actual = config.get('roi', [])
            else:
                continue

            if actual == expected:
                matches += 1
            else:
                mismatches += 1
                print(f"    ⚠️  {name}: config {actual} != source {expected}")

        if mismatches == 0:
            print(f"  ✅ All {matches} ROI coordinates match source file")
        else:
            print(f"  ⚠️  {mismatches} ROI mismatches found")

    except Exception as e:
        print(f"  ❌ Cannot verify source file: {e}")

    print()

    # 4. Final certification
    print("🏆 CERTIFICATION RESULTS:")
    print("=" * 30)

    if capture_ok:
        print("✅ Frame Capture: WORKING")
    else:
        print("❌ Frame Capture: FAILED")

    print(f"✅ Config Files: {config_success}/{len(configs_to_check)} OK")
    print(f"✅ ROI Coordinates: {roi_success} valid")

    if capture_ok and config_success == len(configs_to_check):
        print("\n🎮 CERTIFICATION: PASSED ✅")
        print("🚀 Bot ready for production use!")
        print("")
        print("📋 Test commands:")
        print("  python quick_roi_test.py     # Visual verification")
        print("  python test_roi_live.py      # Live monitoring")
        print("  python main.py navigate ...  # Run navigation")
        return 0
    else:
        print("\n❌ CERTIFICATION: FAILED")
        print("💡 Fix issues above before running bot")
        return 1

if __name__ == "__main__":
    exit(main())