#!/usr/bin/env python3
"""
monitor_capture_debug.py
------------------------
Debug avanzado: verificar qué monitor/ventana está capturando el bot.
"""

import sys
import cv2
import numpy as np
from pathlib import Path

def main() -> int:
    print("🖥️  MONITOR CAPTURE DEBUG")
    print("=" * 40)

    # Add src to path
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from frame_capture import build_frame_getter
    except Exception as e:
        print(f"❌ Import error: {e}")
        return 1

    print("🔍 Checking available capture methods...")

    # Test different capture methods
    capture_methods = ["mss", "dxcam", "printwindow"]

    for method in capture_methods:
        try:
            print(f"\n📸 Testing: {method}")
            frame_getter = build_frame_getter(method)
            frame = frame_getter()

            if frame is not None:
                h, w = frame.shape[:2]
                print(f"  ✅ {method}: {w}x{h} captured")

                # Save sample for inspection
                cv2.imwrite(f"captures/debug_{method}_capture.png", frame)
                print(f"  💾 Saved: captures/debug_{method}_capture.png")

                # Quick analysis
                mean_color = np.mean(frame)
                unique_colors = len(np.unique(frame.reshape(-1, frame.shape[-1]), axis=0))
                print(f"  📊 Mean brightness: {mean_color:.1f}")
                print(f"  🎨 Unique colors: {unique_colors}")

            else:
                print(f"  ❌ {method}: No frame captured")

        except Exception as e:
            print(f"  ❌ {method}: Error - {e}")

    print(f"\n🔍 Current bot configuration:")

    # Check what the bot is actually using
    try:
        print("📋 Default frame capture (what bot uses):")
        default_getter = build_frame_getter("mss")  # Bot default
        default_frame = default_getter()

        if default_frame is not None:
            h, w = default_frame.shape[:2]
            print(f"  ✅ Bot capture: {w}x{h}")

            # Save the exact frame the bot sees
            cv2.imwrite("captures/debug_bot_view.png", default_frame)
            print(f"  💾 Saved: captures/debug_bot_view.png")

            # Extract and save each ROI that bot uses
            import json

            configs = {}
            config_files = ['hpmp_config.json', 'minimap_config.json', 'combat_config.json', 'condition_config.json']

            for config_file in config_files:
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        configs[config_file] = json.load(f)
                except Exception:
                    pass

            # Extract each ROI
            roi_extractions = [
                ("HP", configs.get('hpmp_config.json', {}).get('hp_roi')),
                ("MP", configs.get('hpmp_config.json', {}).get('mp_roi')),
                ("Minimap", configs.get('minimap_config.json', {}).get('roi')),
                ("Battle", configs.get('combat_config.json', {}).get('battle_list_roi')),
                ("Status", configs.get('condition_config.json', {}).get('condition_icons_roi')),
            ]

            print(f"\n📐 ROI extractions from bot view:")
            for name, roi_coords in roi_extractions:
                if roi_coords:
                    x, y, w, h = roi_coords
                    if x + w <= default_frame.shape[1] and y + h <= default_frame.shape[0]:
                        roi_region = default_frame[y:y+h, x:x+w]
                        roi_filename = f"captures/debug_bot_{name.lower()}_roi.png"
                        cv2.imwrite(roi_filename, roi_region)
                        print(f"  💾 {name}: {w}x{h} at ({x},{y}) → {roi_filename}")

                        # Color analysis
                        if roi_region.size > 0:
                            mean_color = np.mean(roi_region, axis=(0, 1))
                            print(f"      Mean BGR: [{mean_color[0]:.1f}, {mean_color[1]:.1f}, {mean_color[2]:.1f}]")
                    else:
                        print(f"  ❌ {name}: ROI out of bounds")

        else:
            print(f"  ❌ Bot capture: Failed")

    except Exception as e:
        print(f"❌ Bot capture analysis failed: {e}")

    print(f"\n💡 ANÁLISIS:")
    print(f"1. Check captures/debug_*.png files to see what each method captures")
    print(f"2. Compare debug_bot_view.png with actual Tibia game")
    print(f"3. If debug_bot_view.png shows wrong content:")
    return 0
    print(f"   • MSS is capturing wrong monitor/window")
    print(f"   • Try different capture method")
    print(f"   • Check monitor configuration")

    print(f"\n🔧 If MSS captures wrong content, try:")
    print(f"   • Move Tibia to primary monitor")
    print(f"   • Move OBS projector to primary monitor")
    print(f"   • Check Windows display settings")
    print(f"   • Try: python test_different_capture_methods.py")

    return 0

if __name__ == "__main__":
    exit(main())