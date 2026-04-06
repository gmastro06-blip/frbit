"""
CLI ROI Capture Tool - Command line interface for OBS projector ROI calibration

This provides a command-line interface for capturing ROIs from the OBS projector
(Monitor 2) using the same frame system as the Waypoint Navigator bot.

Usage examples:
    python cli_roi_capture.py --calibrate minimap
    python cli_roi_capture.py --list-presets
    python cli_roi_capture.py --test-config minimap_config.json

Key features:
- Captures directly from OBS projector on Monitor 2
- Uses bot's frame capture system for pixel-perfect alignment
- Interactive OpenCV selection with real-time preview
- Preset templates for all bot modules
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional
import cv2
import numpy as np

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False
    print("Warning: mss not available. Screenshot capture disabled.")

try:
    from src.frame_capture import build_frame_getter
    from src.frame_cache import FrameCache
    FRAME_CAPTURE_AVAILABLE = True
except ImportError:
    FRAME_CAPTURE_AVAILABLE = False
    print("Warning: Bot frame capture system not available.")


# ROI presets matching the GUI tool
ROIConfig = dict[str, int]
ROIPreset = dict[str, Any]


ROI_PRESETS: dict[str, ROIPreset] = {
    "minimap": {
        "config_file": "minimap_config.json",
        "key": "roi",
        "description": "Minimap detection area",
        "default": {"x": 1665, "y": 55, "width": 240, "height": 176}
    },
    "hp_bar": {
        "config_file": "hpmp_config.json",
        "key": "hp_roi",
        "description": "Health points bar region",
        "default": {"x": 157, "y": 56, "width": 120, "height": 12}
    },
    "mp_bar": {
        "config_file": "hpmp_config.json",
        "key": "mp_roi",
        "description": "Mana points bar region",
        "default": {"x": 157, "y": 75, "width": 120, "height": 12}
    },
    "battle_list": {
        "config_file": "combat_config.json",
        "key": "battle_list_roi",
        "description": "Combat battle list area",
        "default": {"x": 1720, "y": 245, "width": 185, "height": 400}
    },
    "chat": {
        "config_file": "chat_config.json",
        "key": "chat_roi",
        "description": "Chat messages area",
        "default": {"x": 8, "y": 304, "width": 640, "height": 356}
    },
    "status_icons": {
        "config_file": "condition_config.json",
        "key": "status_roi",
        "description": "Status condition icons",
        "default": {"x": 1665, "y": 32, "width": 240, "height": 20}
    },
    "depot": {
        "config_file": "depot_config.json",
        "key": "depot_roi",
        "description": "Depot container area",
        "default": {"x": 1403, "y": 152, "width": 502, "height": 364}
    },
    "gm_scan": {
        "config_file": "gm_detector_config.json",
        "key": "scan_roi",
        "description": "GM detection scan area",
        "default": {"x": 0, "y": 0, "width": 1920, "height": 1080}
    }
}


class CLIROICapture:
    """Command line ROI capture tool."""

    def __init__(self, project_path: Path | None = None) -> None:
        self.project_path = project_path or Path("c:/Users/gmast/Documents/frbit/waypoint-navigator")

    def capture_screenshot(self, output_file: Optional[str] = None) -> Optional[np.ndarray]:
        """Capture screenshot from Monitor 2 (OBS projector)."""
        if not MSS_AVAILABLE:
            print("Error: mss not available for screenshot capture")
            return None

        try:
            with mss.mss() as sct:
                # Capture Monitor 2 specifically (where OBS projector runs)
                monitor2 = {
                    "top": 0,
                    "left": 1920,  # Monitor 2 offset in dual setup
                    "width": 1920,
                    "height": 1080
                }
                screenshot = sct.grab(monitor2)
                img_array = np.array(screenshot)
                img_bgr = cv2.cvtColor(img_array, cv2.COLOR_BGRA2BGR)

                if output_file:
                    cv2.imwrite(output_file, img_bgr)
                    print(f"📷 Screenshot saved to {output_file} (Monitor 2 - OBS projector)")

                return img_bgr

        except Exception as e:
            print(f"Error capturing screenshot: {e}")
            return None

    def capture_obs_frame(self) -> Optional[np.ndarray]:
        """Capture frame from OBS projector using bot's frame system."""
        if not FRAME_CAPTURE_AVAILABLE:
            print("Error: Bot frame capture system not available")
            return None

        try:
            # Use same configuration as the bot - Monitor 2
            frame_getter = build_frame_getter("mss", monitor_idx=2)
            frame_cache = FrameCache(frame_getter, ttl_ms=50)

            frame = frame_cache.get_frame()
            if frame is not None:
                print("📹 Captured frame from OBS projector (Monitor 2)")
                return frame
            else:
                print("Error: No frame captured from OBS projector")
                return None

        except Exception as e:
            print(f"Error capturing OBS frame: {e}")
            return None

    def interactive_roi_select(self, image: np.ndarray, roi_type: str) -> Optional[ROIConfig]:
        """Interactive ROI selection using OpenCV with zoom functionality."""
        print(f"\\n🎯 ROI Selection for '{roi_type}' (with zoom support)")
        print("Controls:")
        print("  • Click and drag to select region")
        print("  • '+'/'-' or Mouse Wheel: Zoom in/out")
        print("  • Arrow keys: Pan when zoomed")
        print("  • 'r': Reset selection and zoom")
        print("  • 'f': Fit to window (reset zoom)")
        print("  • Enter/Space: Confirm selection")
        print("  • Esc/q: Cancel")

        # Create window
        window_name = f"ROI Selection - {roi_type}"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)

        # Initial setup
        original_image = image.copy()
        h, w = original_image.shape[:2]

        # Zoom and pan state
        zoom_level = 1.0
        zoom_levels = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
        zoom_index = 3  # Start at 100%
        pan_x, pan_y = 0.0, 0.0

        # Initial window size
        max_display_size = 1000
        if w > max_display_size or h > max_display_size:
            scale = max_display_size / max(w, h)
            display_w, display_h = int(w * scale), int(h * scale)
        else:
            display_w, display_h = w, h
            scale = 1.0

        cv2.resizeWindow(window_name, display_w, display_h)

        # Selection state
        roi_coords = None
        start_point = None
        current_point = None
        selecting = False

        def update_display() -> tuple[np.ndarray, int, int]:
            nonlocal zoom_level, pan_x, pan_y, display_w, display_h

            # Calculate display region with zoom and pan
            zoom_level = zoom_levels[zoom_index]

            # Calculate visible region in original image coordinates
            view_w = display_w / zoom_level
            view_h = display_h / zoom_level

            # Constrain pan to image bounds
            max_pan_x = max(0, w - view_w)
            max_pan_y = max(0, h - view_h)
            pan_x = max(0, min(pan_x, max_pan_x))
            pan_y = max(0, min(pan_y, max_pan_y))

            # Extract visible region
            x1, y1 = max(0, int(pan_x)), max(0, int(pan_y))
            x2, y2 = min(w, int(pan_x + view_w)), min(h, int(pan_y + view_h))

            if x2 > x1 and y2 > y1:
                view_img = original_image[y1:y2, x1:x2]

                # Resize for display
                display_img = cv2.resize(view_img, (display_w, display_h),
                                       interpolation=cv2.INTER_NEAREST if zoom_level > 2.0 else cv2.INTER_LINEAR)
            else:
                display_img = cv2.resize(original_image, (display_w, display_h))

            return display_img, x1, y1

        def mouse_callback(event: int, x: int, y: int, flags: int, param: Any) -> None:
            nonlocal roi_coords, start_point, current_point, selecting
            nonlocal zoom_index, pan_x, pan_y

            # Convert display coords to image coords
            display_img, view_x1, view_y1 = update_display()
            zoom_level = zoom_levels[zoom_index]

            # Scale from display to view region
            view_x = (x * (display_img.shape[1] / display_w)) + view_x1
            view_y = (y * (display_img.shape[0] / display_h)) + view_y1

            if event == cv2.EVENT_LBUTTONDOWN:
                start_point = (view_x, view_y)
                current_point = (view_x, view_y)
                selecting = True

            elif event == cv2.EVENT_MOUSEMOVE and selecting:
                current_point = (view_x, view_y)

            elif event == cv2.EVENT_LBUTTONUP and selecting:
                if start_point and current_point:
                    # Calculate ROI in original image coordinates
                    x1 = min(start_point[0], view_x)
                    y1 = min(start_point[1], view_y)
                    x2 = max(start_point[0], view_x)
                    y2 = max(start_point[1], view_y)

                    roi_coords = {
                        "x": int(max(0, x1)),
                        "y": int(max(0, y1)),
                        "width": int(min(w - x1, x2 - x1)),
                        "height": int(min(h - y1, y2 - y1))
                    }

                    print(f"Selection: {roi_coords}")

                selecting = False

            # Mouse wheel zoom
            elif event == cv2.EVENT_MOUSEWHEEL:
                if flags > 0:  # Zoom in
                    if zoom_index < len(zoom_levels) - 1:
                        # Zoom towards cursor position
                        old_zoom = zoom_levels[zoom_index]
                        zoom_index += 1
                        new_zoom = zoom_levels[zoom_index]

                        # Adjust pan to zoom towards cursor
                        zoom_ratio = new_zoom / old_zoom
                        cursor_x_in_image = pan_x + (x / display_w) * (display_w / old_zoom)
                        cursor_y_in_image = pan_y + (y / display_h) * (display_h / old_zoom)

                        pan_x = cursor_x_in_image - (x / display_w) * (display_w / new_zoom)
                        pan_y = cursor_y_in_image - (y / display_h) * (display_h / new_zoom)

                else:  # Zoom out
                    if zoom_index > 0:
                        zoom_index -= 1

        cv2.setMouseCallback(window_name, mouse_callback)

        while True:
            # Update display
            display_img, view_x1, view_y1 = update_display()
            zoom_level = zoom_levels[zoom_index]

            # Draw current selection
            if selecting and start_point and current_point:
                # Convert image coords back to display coords for drawing
                sx = int((start_point[0] - view_x1) * display_w / (display_img.shape[1]))
                sy = int((start_point[1] - view_y1) * display_h / (display_img.shape[0]))
                cx = int((current_point[0] - view_x1) * display_w / (display_img.shape[1]))
                cy = int((current_point[1] - view_y1) * display_h / (display_img.shape[0]))

                cv2.rectangle(display_img, (sx, sy), (cx, cy), (0, 0, 255), 2)

                # Size text
                w_sel = abs(current_point[0] - start_point[0])
                h_sel = abs(current_point[1] - start_point[1])
                size_text = f"{int(w_sel)}x{int(h_sel)}"
                cv2.putText(display_img, size_text, (min(sx, cx), min(sy, cy) - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            # Draw confirmed selection
            if roi_coords:
                x1_d = int((roi_coords["x"] - view_x1) * display_w / (display_img.shape[1]))
                y1_d = int((roi_coords["y"] - view_y1) * display_h / (display_img.shape[0]))
                x2_d = int((roi_coords["x"] + roi_coords["width"] - view_x1) * display_w / (display_img.shape[1]))
                y2_d = int((roi_coords["y"] + roi_coords["height"] - view_y1) * display_h / (display_img.shape[0]))

                cv2.rectangle(display_img, (x1_d, y1_d), (x2_d, y2_d), (0, 255, 0), 3)

                # Add text
                text = f"{roi_coords['width']}x{roi_coords['height']}"
                cv2.putText(display_img, text, (x1_d, y1_d - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            # Add zoom info
            zoom_text = f"Zoom: {int(zoom_level * 100)}% | {roi_type}"
            cv2.putText(display_img, zoom_text, (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

            # Add crosshairs at center when zoomed
            if zoom_level > 1.5:
                center_x, center_y = display_w // 2, display_h // 2
                cv2.line(display_img, (center_x - 20, center_y), (center_x + 20, center_y), (128, 128, 128), 1)
                cv2.line(display_img, (center_x, center_y - 20), (center_x, center_y + 20), (128, 128, 128), 1)

            cv2.imshow(window_name, display_img)

            key = cv2.waitKey(1) & 0xFF

            # Keyboard controls
            if key == ord('r'):
                # Reset selection and zoom
                roi_coords = None
                start_point = None
                current_point = None
                selecting = False
                zoom_index = 3  # 100%
                pan_x, pan_y = 0, 0
                print("Reset selection and zoom")

            elif key == ord('f'):
                # Fit to window
                zoom_index = 3  # 100%
                pan_x, pan_y = 0, 0
                print("Fit to window")

            elif key in [ord('+'), ord('=')]:
                # Zoom in
                if zoom_index < len(zoom_levels) - 1:
                    zoom_index += 1
                    print(f"Zoom: {int(zoom_levels[zoom_index] * 100)}%")

            elif key in [ord('-'), ord('_')]:
                # Zoom out
                if zoom_index > 0:
                    zoom_index -= 1
                    print(f"Zoom: {int(zoom_levels[zoom_index] * 100)}%")

            elif key == 82:  # Up arrow
                pan_y -= 20 / zoom_level

            elif key == 84:  # Down arrow
                pan_y += 20 / zoom_level

            elif key == 81:  # Left arrow
                pan_x -= 20 / zoom_level

            elif key == 83:  # Right arrow
                pan_x += 20 / zoom_level

            elif key in [ord(' '), 13]:  # Space or Enter
                if roi_coords:
                    break
                else:
                    print("No ROI selected yet")

            elif key in [27, ord('q')]:  # Esc or q
                print("Selection cancelled")
                roi_coords = None
                break

        cv2.destroyAllWindows()

        if roi_coords:
            print(f"✅ ROI confirmed with zoom: {roi_coords}")
            return roi_coords
        else:
            print("❌ No ROI selected")
            return None

    def apply_preset_roi(self, roi_type: str) -> Optional[ROIConfig]:
        """Apply preset ROI coordinates."""
        if roi_type not in ROI_PRESETS:
            print(f"Error: Unknown ROI type '{roi_type}'")
            return None

        preset = ROI_PRESETS[roi_type]
        roi_coords = preset["default"].copy()
        print(f"📋 Applied preset ROI for '{roi_type}': {roi_coords}")
        return roi_coords

    def save_roi_config(self, roi_type: str, roi_coords: ROIConfig, output_file: Optional[str] = None) -> None:
        """Save ROI coordinates to config file."""
        if roi_type not in ROI_PRESETS:
            # Custom ROI type
            config = {"roi": roi_coords}
            config_file_path: str | Path = output_file or f"custom_{roi_type}_config.json"
        else:
            # Preset ROI type
            preset = ROI_PRESETS[roi_type]
            config_file_name = output_file or str(preset["config_file"])
            config_key = str(preset["key"])
            config_path = self.project_path / config_file_name

            # Load existing config if available
            if config_path.exists():
                try:
                    with open(config_path, 'r') as f:
                        config = json.load(f)
                except Exception:
                    config = {}
            else:
                config = {}

            # Update with new ROI
            config[config_key] = roi_coords
            config_file_path = config_path

        # Save config
        try:
            if isinstance(config_file_path, str):
                config_path = Path(config_file_path)
            else:
                config_path = config_file_path

            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)

            print(f"💾 Saved ROI config to {config_path}")

        except Exception as e:
            print(f"Error saving config: {e}")

    def test_config(self, config_file: str, roi_key: Optional[str] = None) -> None:
        """Test existing ROI configuration."""
        config_path = Path(config_file)
        if not config_path.exists():
            config_path = self.project_path / config_file

        if not config_path.exists():
            print(f"Error: Config file not found: {config_file}")
            return

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Find ROI in config
            roi_coords = None
            if roi_key:
                roi_coords = config.get(roi_key)
            else:
                # Try common ROI keys
                for key in ["roi", "hp_roi", "mp_roi", "battle_list_roi", "chat_roi", "status_roi", "depot_roi", "scan_roi"]:
                    if key in config:
                        roi_coords = config[key]
                        roi_key = key
                        break

            if not roi_coords:
                print(f"Error: No ROI found in config {config_file}")
                print(f"Available keys: {list(config.keys())}")
                return

            print(f"🧪 Testing ROI '{roi_key}' from {config_file}")
            print(f"ROI: {roi_coords}")

            # Try to capture current frame for testing from OBS projector
            frame = self.capture_obs_frame()
            if frame is None:
                frame = self.capture_screenshot()

            if frame is not None:
                # Extract ROI region
                x, y = roi_coords["x"], roi_coords["y"]
                w, h = roi_coords["width"], roi_coords["height"]

                if 0 <= x < frame.shape[1] and 0 <= y < frame.shape[0] and x + w <= frame.shape[1] and y + h <= frame.shape[0]:
                    roi_region = frame[y:y+h, x:x+w]

                    # Save test image
                    test_file = f"test_roi_{roi_key}_{int(time.time())}.png"
                    cv2.imwrite(test_file, roi_region)
                    print(f"📸 ROI region saved to {test_file}")

                    print(f"✅ ROI test successful - region size: {roi_region.shape}")
                else:
                    print(f"❌ ROI coordinates out of bounds for image size {frame.shape}")
            else:
                print("⚠️ Could not capture frame for testing")

        except Exception as e:
            print(f"Error testing config: {e}")

    def list_presets(self) -> None:
        """List available ROI presets."""
        print("\\n📋 Available ROI Presets:")
        print("=" * 50)

        for roi_type, preset in ROI_PRESETS.items():
            default = preset["default"]
            print(f"• {roi_type}")
            print(f"  Description: {preset['description']}")
            print(f"  Config: {preset['config_file']}")
            print(f"  Default: ({default['x']}, {default['y']}) {default['width']}x{default['height']}")
            print()

    def interactive_calibration(self, roi_type: str) -> None:
        """Full interactive calibration workflow."""
        print(f"\\n🔧 Interactive ROI Calibration for '{roi_type}'")
        print("=" * 50)

        # Step 1: Capture image from OBS projector
        print("\\n1. Capturing image from OBS projector...")
        image = self.capture_obs_frame()  # Try OBS frame first
        if image is None:
            print("Falling back to Monitor 2 screenshot...")
            image = self.capture_screenshot()

        if image is None:
            print("❌ Failed to capture image from OBS projector")
            print("💡 Ensure OBS projector is running on Monitor 2")
            return

        print(f"✅ Image captured from OBS: {image.shape[1]}x{image.shape[0]}")

        # Step 2: Choose selection method
        print("\\n2. Choose ROI selection method:")
        print("  [i] Interactive selection (mouse)")
        print("  [p] Use preset coordinates")
        print("  [m] Manual coordinate entry")

        choice = input("Selection method (i/p/m): ").lower().strip()

        roi_coords = None

        if choice == 'i':
            try:
                roi_coords = self.interactive_roi_select(image, roi_type)
            except Exception as e:
                print(f"Error in interactive selection: {e}")
                print("Falling back to preset coordinates")
                roi_coords = self.apply_preset_roi(roi_type)

        elif choice == 'p':
            roi_coords = self.apply_preset_roi(roi_type)

        elif choice == 'm':
            print("\\nEnter ROI coordinates:")
            try:
                x = int(input("X: "))
                y = int(input("Y: "))
                width = int(input("Width: "))
                height = int(input("Height: "))
                roi_coords = {"x": x, "y": y, "width": width, "height": height}
                print(f"Manual ROI: {roi_coords}")
            except ValueError:
                print("Invalid coordinates entered")
                return

        else:
            print("Invalid choice")
            return

        if not roi_coords:
            print("❌ No ROI coordinates obtained")
            return

        # Step 3: Preview ROI
        print("\\n3. Preview ROI...")
        try:
            x, y, w, h = roi_coords["x"], roi_coords["y"], roi_coords["width"], roi_coords["height"]
            roi_region = image[y:y+h, x:x+w]

            preview_file = f"preview_{roi_type}_{int(time.time())}.png"
            cv2.imwrite(preview_file, roi_region)
            print(f"📸 ROI preview saved to {preview_file}")

        except Exception as e:
            print(f"Error creating preview: {e}")

        # Step 4: Confirm and save
        print("\\n4. Save configuration?")
        confirm = input("Save this ROI configuration? (y/N): ").lower().strip()

        if confirm == 'y':
            self.save_roi_config(roi_type, roi_coords)
            print(f"✅ ROI calibration completed for '{roi_type}'")
        else:
            print("❌ Configuration not saved")


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CLI ROI Capture Tool for Waypoint Navigator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --calibrate minimap
  %(prog)s --screenshot capture.png --type hp_bar --interactive
  %(prog)s --list-presets
  %(prog)s --test-config minimap_config.json
"""
    )

    parser.add_argument("--calibrate", type=str, metavar="ROI_TYPE",
                       help="Interactive calibration for ROI type")

    parser.add_argument("--screenshot", type=str, metavar="FILE",
                       help="Capture screenshot to file")

    parser.add_argument("--type", type=str, metavar="ROI_TYPE",
                       help="ROI type for operations")

    parser.add_argument("--interactive", action="store_true",
                       help="Use interactive ROI selection")

    parser.add_argument("--preset", action="store_true",
                       help="Use preset coordinates for ROI type")

    parser.add_argument("--coords", type=str, metavar="X,Y,W,H",
                       help="Manual coordinates: x,y,width,height")

    parser.add_argument("--output", type=str, metavar="FILE",
                       help="Output config file")

    parser.add_argument("--list-presets", action="store_true",
                       help="List available ROI presets")

    parser.add_argument("--test-config", type=str, metavar="FILE",
                       help="Test existing config file")

    parser.add_argument("--project-path", type=str, metavar="PATH",
                       help="Project root path (default: current directory)")

    args = parser.parse_args()

    # Initialize tool
    project_path = Path(args.project_path) if args.project_path else None
    tool = CLIROICapture(project_path)

    # Handle list presets
    if args.list_presets:
        tool.list_presets()
        return

    # Handle test config
    if args.test_config:
        tool.test_config(args.test_config)
        return

    # Handle calibration
    if args.calibrate:
        tool.interactive_calibration(args.calibrate)
        return

    # Handle screenshot capture
    if args.screenshot:
        image = tool.capture_screenshot(args.screenshot)
        if image is not None and args.type:
            # Continue with ROI selection
            roi_coords = None

            if args.interactive:
                try:
                    roi_coords = tool.interactive_roi_select(image, args.type)
                except Exception as e:
                    print(f"Interactive selection failed: {e}")

            elif args.preset:
                roi_coords = tool.apply_preset_roi(args.type)

            elif args.coords:
                try:
                    x, y, w, h = map(int, args.coords.split(','))
                    roi_coords = {"x": x, "y": y, "width": w, "height": h}
                except ValueError:
                    print("Error: Invalid coordinates format. Use: x,y,width,height")
                    return

            if roi_coords:
                tool.save_roi_config(args.type, roi_coords, args.output)

        return

    # No specific action specified
    print("No action specified. Use --help for usage information.")
    print("Quick start: python cli_roi_capture.py --calibrate minimap")


if __name__ == "__main__":
    import time
    main()