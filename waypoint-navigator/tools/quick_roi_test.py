#!/usr/bin/env python3
"""
quick_roi_test.py
-----------------
Quick ROI visual verification - saves ROI crops as images for inspection.

Usage:
    python quick_roi_test.py

Output:
    - captures/roi_test_TIMESTAMP/
      ├── full_frame.png
      ├── hp_bar.png
      ├── mp_bar.png
      ├── battle_list.png
      ├── chat.png
      ├── status_icons.png
      └── minimap.png
"""

import sys
import json
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime

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

def main() -> int:
    print("⚡ Quick ROI Test - Visual Verification")
    print("=" * 50)

    # Create output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("captures") / f"roi_test_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Capture frame
        print("📸 Capturing frame from OBS...")
        from frame_capture import build_frame_getter
        frame_getter = build_frame_getter("mss")
        frame = frame_getter()

        if frame is None:
            print("❌ No frame captured. Check OBS setup.")
            return 1

        h, w = frame.shape[:2]
        print(f"✅ Frame captured: {w}x{h} pixels")

        # Save full frame
        cv2.imwrite(str(output_dir / "full_frame.png"), frame)
        print(f"💾 Saved full frame to: {output_dir / 'full_frame.png'}")

        # ROI definitions
        roi_configs = [
            ("hp_bar", "hpmp_config.json", "hp_roi", "HP Bar"),
            ("mp_bar", "hpmp_config.json", "mp_roi", "MP Bar"),
            ("battle_list", "combat_config.json", "battle_list_roi", "Battle List"),
            ("chat", "chat_config.json", "chat_roi", "Chat"),
            ("status_icons", "condition_config.json", "condition_icons_roi", "Status Icons"),
            ("minimap", "minimap_config.json", "roi", "Minimap"),
        ]

        print(f"\n📐 Extracting and saving ROI crops...")

        successful_rois = 0
        for filename, config_file, roi_key, display_name in roi_configs:
            try:
                # Load config
                config = load_config(config_file)
                if roi_key not in config:
                    print(f"  ❌ {display_name:<15} - '{roi_key}' not found in {config_file}")
                    continue

                # Extract ROI
                roi_coords = config[roi_key]
                x, y, w, h = roi_coords

                # Validate coordinates
                if x < 0 or y < 0 or x + w > frame.shape[1] or y + h > frame.shape[0]:
                    print(f"  ❌ {display_name:<15} - Invalid coordinates {roi_coords}")
                    continue

                # Extract region
                roi_frame = frame[y:y+h, x:x+w]

                if roi_frame.size == 0:
                    print(f"  ❌ {display_name:<15} - Empty ROI")
                    continue

                # Add coordinates overlay
                roi_with_overlay = roi_frame.copy()
                cv2.putText(roi_with_overlay, f"{display_name}", (5, 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                cv2.putText(roi_with_overlay, f"({x},{y}) {w}x{h}", (5, roi_frame.shape[0] - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

                # Save ROI crop
                output_path = output_dir / f"{filename}.png"
                cv2.imwrite(str(output_path), roi_with_overlay)

                roi_h, roi_w = roi_frame.shape[:2]
                print(f"  ✅ {display_name:<15} - {roi_w:3d}x{roi_h:3d} at ({x:4d},{y:3d}) → {filename}.png")

                successful_rois += 1

            except Exception as e:
                print(f"  ❌ {display_name:<15} - Error: {e}")

        print(f"\n📊 Results: {successful_rois}/{len(roi_configs)} ROIs extracted successfully")
        print(f"📁 All files saved to: {output_dir}")

        # Create composite view
        print(f"\n🖼️  Creating composite view...")
        try:
            # Load saved images for composite
            composite_width = 1200
            composite_height = 800
            composite = np.zeros((composite_height, composite_width, 3), dtype=np.uint8)

            # Add title
            cv2.putText(composite, f"ROI Test Results - {timestamp}", (10, 25),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # Simple grid layout (2x3)
            positions = [(50, 50), (350, 50), (650, 50), (50, 250), (350, 250), (650, 250)]
            max_size = (280, 180)

            for i, (filename, _, _, display_name) in enumerate(roi_configs):
                if i >= len(positions):
                    break

                img_path = output_dir / f"{filename}.png"
                if img_path.exists():
                    roi_img = cv2.imread(str(img_path))
                    if roi_img is not None:
                        # Resize to fit
                        h_roi, w_roi = roi_img.shape[:2]
                        scale = min(max_size[0]/w_roi, max_size[1]/h_roi, 1.0)
                        new_w, new_h = int(w_roi * scale), int(h_roi * scale)
                        roi_resized = cv2.resize(roi_img, (new_w, new_h))

                        # Place in composite
                        x_pos, y_pos = positions[i]
                        y_end = min(y_pos + new_h, composite_height)
                        x_end = min(x_pos + new_w, composite_width)
                        composite[y_pos:y_end, x_pos:x_end] = roi_resized[:y_end-y_pos, :x_end-x_pos]

                        # Add label
                        cv2.putText(composite, display_name, (x_pos, y_pos - 5),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

            # Save composite
            cv2.imwrite(str(output_dir / "composite_view.png"), composite)
            print(f"✅ Composite view saved to: {output_dir / 'composite_view.png'}")

        except Exception as e:
            print(f"❌ Composite creation failed: {e}")

        print(f"\n🎯 Quick ROI test completed!")
        print(f"📝 Next steps:")
        print(f"   1. Check images in: {output_dir}")
        print(f"   2. Verify ROIs capture correct screen regions")
        print(f"   3. Run live test: python test_roi_live.py")

        return 0

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return 1

if __name__ == "__main__":
    exit(main())