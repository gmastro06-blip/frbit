#!/usr/bin/env python
"""
Template Capture & Extraction Tool
===================================
Captures screenshots from a live Tibia window and assists in cropping
sub-regions into template PNGs suitable for the vision pipeline.

Usage
-----
  # Interactive capture + crop mode:
  python tools/capture_templates.py --window "Tibia"

  # Capture one full screenshot and save it:
  python tools/capture_templates.py --window "Tibia" --screenshot output/capture.png

  # Extract a region from an existing screenshot:
  python tools/capture_templates.py --from-file output/capture.png --crop

  # Batch extract templates using a JSON recipe:
  python tools/capture_templates.py --from-file output/capture.png --recipe recipe.json

Recipe JSON format
------------------
  [
    {"name": "wasp_corpse", "category": "corpses",    "roi": [x, y, w, h]},
    {"name": "honeycomb",   "category": "loot_items", "roi": [x, y, w, h]},
    ...
  ]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "cache" / "templates"

# Categories that the bot uses
CATEGORIES = [
    "monsters",
    "corpses",
    "loot_items",
    "conditions",
    "anchors",
    "skulls",
    "trade_items",
]

# ---------------------------------------------------------------------------
# Capture helpers
# ---------------------------------------------------------------------------


def capture_window(title_fragment: str = "Tibia") -> Optional[np.ndarray]:
    """Capture the Tibia window using MSS (works even when not focused).

    Falls back to full-screen capture if the window is not found.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        # Find the window
        hwnd = user32.FindWindowW(None, None)
        found_hwnd = 0
        cb_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def _enum_cb(h: int, _: int) -> bool:
            nonlocal found_hwnd
            length = user32.GetWindowTextLengthW(h)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(h, buf, length + 1)
                if title_fragment.lower() in buf.value.lower():
                    found_hwnd = h
                    return False  # stop enumeration
            return True

        user32.EnumWindows(cb_type(_enum_cb), 0)

        if not found_hwnd:
            print(f"[!] Window '{title_fragment}' not found. Capturing full screen.")
            return _capture_full_screen()

        # Get window rect
        rect = wt.RECT()
        user32.GetClientRect(found_hwnd, ctypes.byref(rect))
        pt = wt.POINT(0, 0)
        ctypes.windll.user32.ClientToScreen(found_hwnd, ctypes.byref(pt))

        import mss  # type: ignore[import]

        monitor = {
            "left": pt.x,
            "top": pt.y,
            "width": rect.right,
            "height": rect.bottom,
        }
        with mss.mss() as sct:
            img = sct.grab(monitor)
            frame = np.array(img)[:, :, :3]  # drop alpha
            return frame

    except Exception as exc:
        print(f"[!] Window capture failed ({exc}). Trying full screen.")
        return _capture_full_screen()


def _capture_full_screen() -> Optional[np.ndarray]:
    """Capture the entire primary monitor."""
    try:
        import mss  # type: ignore[import]

        with mss.mss() as sct:
            img = sct.grab(sct.monitors[1])
            frame = np.array(img)[:, :, :3]
            return frame
    except ImportError:
        print("[!] mss not installed. Run: pip install mss")
        return None


def capture_from_file(path: str) -> Optional[np.ndarray]:
    """Load a screenshot from disk."""
    img = cv2.imread(path)
    if img is None:
        print(f"[!] Could not load image: {path}")
    return img


# ---------------------------------------------------------------------------
# Interactive crop tool
# ---------------------------------------------------------------------------


class CropTool:
    """OpenCV-based interactive ROI selector for template extraction."""

    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame.copy()
        self.display = frame.copy()
        self.rois: List[Tuple[int, int, int, int]] = []  # (x, y, w, h)
        self.drawing = False
        self.start_pt: Tuple[int, int] = (0, 0)
        self.current_pt: Tuple[int, int] = (0, 0)
        self.zoom_factor = 1.0

    def _mouse_cb(self, event: int, x: int, y: int, flags: int, _: Any) -> None:
        # Scale back to original frame coordinates
        ox = int(x / self.zoom_factor)
        oy = int(y / self.zoom_factor)

        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.start_pt = (ox, oy)
            self.current_pt = (ox, oy)

        elif event == cv2.EVENT_MOUSEMOVE:
            self.current_pt = (ox, oy)
            if self.drawing:
                self._redraw()

        elif event == cv2.EVENT_LBUTTONUP:
            self.drawing = False
            x0, y0 = self.start_pt
            x1, y1 = ox, oy
            rx, ry = min(x0, x1), min(y0, y1)
            rw, rh = abs(x1 - x0), abs(y1 - y0)
            if rw > 2 and rh > 2:
                self.rois.append((rx, ry, rw, rh))
                print(f"  ROI #{len(self.rois)}: [{rx}, {ry}, {rw}, {rh}]  ({rw}x{rh} px)")
            self._redraw()

    def _redraw(self) -> None:
        self.display = self.frame.copy()
        # Draw completed ROIs in green
        for i, (rx, ry, rw, rh) in enumerate(self.rois):
            cv2.rectangle(self.display, (rx, ry), (rx + rw, ry + rh), (0, 255, 0), 2)
            cv2.putText(
                self.display,
                f"#{i + 1}",
                (rx + 2, ry + 14),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )
        # Draw current selection in blue
        if self.drawing:
            x0, y0 = self.start_pt
            x1, y1 = self.current_pt
            cv2.rectangle(self.display, (x0, y0), (x1, y1), (255, 100, 0), 2)

        # Apply zoom
        if self.zoom_factor != 1.0:
            h, w = self.display.shape[:2]
            nw, nh = int(w * self.zoom_factor), int(h * self.zoom_factor)
            show = cv2.resize(self.display, (nw, nh), interpolation=cv2.INTER_NEAREST)
        else:
            show = self.display
        cv2.imshow("Template Capture — draw ROIs, [s]ave, [u]ndo, [q]uit", show)

    def run(self) -> List[Tuple[int, int, int, int]]:
        """Launch the interactive crop window. Returns list of (x, y, w, h)."""
        cv2.namedWindow(
            "Template Capture — draw ROIs, [s]ave, [u]ndo, [q]uit",
            cv2.WINDOW_NORMAL,
        )
        cv2.setMouseCallback(
            "Template Capture — draw ROIs, [s]ave, [u]ndo, [q]uit",
            self._mouse_cb,
        )
        self._redraw()

        print("\n=== Template Crop Tool ===")
        print("  Draw rectangles to select template regions.")
        print("  [s] Save all ROIs  [u] Undo last  [+/-] Zoom  [q] Quit\n")

        while True:
            key = cv2.waitKey(20) & 0xFF
            if key == ord("q") or key == 27:   # q or ESC
                break
            elif key == ord("u") and self.rois:
                removed = self.rois.pop()
                print(f"  Undo ROI #{len(self.rois) + 1}: {list(removed)}")
                self._redraw()
            elif key == ord("s"):
                break
            elif key == ord("+") or key == ord("="):
                self.zoom_factor = min(4.0, self.zoom_factor + 0.5)
                self._redraw()
            elif key == ord("-"):
                self.zoom_factor = max(0.25, self.zoom_factor - 0.5)
                self._redraw()

        cv2.destroyAllWindows()
        return self.rois


# ---------------------------------------------------------------------------
# Save extracted templates
# ---------------------------------------------------------------------------


def save_template(
    frame: np.ndarray,
    roi: Tuple[int, int, int, int],
    name: str,
    category: str,
) -> Path:
    """Crop the ROI from frame and save as a PNG template."""
    x, y, w, h = roi
    crop = frame[y : y + h, x : x + w]
    out_dir = TEMPLATES_DIR / category
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.png"
    cv2.imwrite(str(out_path), crop)
    print(f"  Saved: {out_path.relative_to(ROOT)}  ({w}x{h} px)")
    return out_path


def apply_recipe(frame: np.ndarray, recipe_path: str) -> int:
    """Extract templates from a JSON recipe file."""
    with open(recipe_path, encoding="utf-8") as f:
        entries: List[Dict[str, Any]] = json.load(f)

    count = 0
    for entry in entries:
        name = entry["name"]
        cat = entry["category"]
        roi = tuple(entry["roi"])
        if len(roi) != 4:
            print(f"  [!] Invalid ROI for {name}: {roi}")
            continue
        save_template(frame, roi, name, cat)  # type: ignore[arg-type]
        count += 1

    print(f"\n  Extracted {count} templates from recipe.")
    return count


# ---------------------------------------------------------------------------
# Interactive save flow (after CropTool)
# ---------------------------------------------------------------------------


def interactive_save(frame: np.ndarray, rois: List[Tuple[int, int, int, int]]) -> int:
    """Prompt user for name/category for each ROI, then save."""
    if not rois:
        print("No ROIs selected.")
        return 0

    print(f"\n--- Saving {len(rois)} ROI(s) ---")
    print(f"Categories: {', '.join(CATEGORIES)}\n")

    saved = 0
    for i, roi in enumerate(rois):
        x, y, w, h = roi
        print(f"ROI #{i + 1}: [{x}, {y}, {w}, {h}]  ({w}x{h} px)")

        # Show the cropped region
        crop = frame[y : y + h, x : x + w]
        preview = cv2.resize(crop, (max(w * 3, 96), max(h * 3, 96)), interpolation=cv2.INTER_NEAREST)
        cv2.imshow(f"ROI #{i + 1} preview", preview)
        cv2.waitKey(100)

        cat = input(f"  Category [{'/'.join(CATEGORIES)}]: ").strip()
        if cat not in CATEGORIES:
            print(f"  [!] Unknown category '{cat}'. Skipping.")
            cv2.destroyWindow(f"ROI #{i + 1} preview")
            continue
        name = input("  Template name (no extension): ").strip()
        if not name:
            print("  [!] Empty name. Skipping.")
            cv2.destroyWindow(f"ROI #{i + 1} preview")
            continue

        save_template(frame, roi, name, cat)
        saved += 1
        cv2.destroyWindow(f"ROI #{i + 1} preview")

    print(f"\nSaved {saved} template(s).")
    return saved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture Tibia screenshots and extract template PNGs.",
    )
    parser.add_argument("--window", default="Tibia", help="Window title fragment.")
    parser.add_argument("--from-file", default="", help="Load screenshot from file instead of capturing.")
    parser.add_argument("--screenshot", default="", help="Save a full screenshot to this path and exit.")
    parser.add_argument("--crop", action="store_true", help="Open interactive crop mode.")
    parser.add_argument("--recipe", default="", help="JSON recipe file for batch extraction.")

    args = parser.parse_args()

    # ── Get the frame ─────────────────────────────────────────────────────
    if args.from_file:
        frame = capture_from_file(args.from_file)
    else:
        print(f"Capturing window '{args.window}' ...")
        frame = capture_window(args.window)

    if frame is None:
        print("ERROR: Could not get a frame. Exiting.")
        sys.exit(1)

    print(f"Frame size: {frame.shape[1]}x{frame.shape[0]} ({frame.dtype})")

    # ── Save screenshot mode ──────────────────────────────────────────────
    if args.screenshot:
        out = Path(args.screenshot)
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), frame)
        print(f"Screenshot saved: {out}")
        return

    # ── Recipe mode ───────────────────────────────────────────────────────
    if args.recipe:
        apply_recipe(frame, args.recipe)
        return

    # ── Interactive crop mode (default) ───────────────────────────────────
    tool = CropTool(frame)
    rois = tool.run()
    if rois:
        interactive_save(frame, rois)
    else:
        print("No ROIs selected. Use --screenshot to just save the frame.")


if __name__ == "__main__":
    main()
