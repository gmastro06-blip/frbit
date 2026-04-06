#!/usr/bin/env python
"""
Capture Missing Templates — Interactive tool to fill empty template dirs.

Captures a Tibia screenshot and guides the user through cropping the
specific regions needed for PvP skulls, condition icons, and UI anchors.

Usage
-----
    python tools/capture_missing_templates.py --source mss --monitor-idx 2
    python tools/capture_missing_templates.py --from-file screenshot.png
    python tools/capture_missing_templates.py --source mss --category skulls

Categories
----------
    skulls      : 5 skull indicators (white, orange, red, black, green)
    conditions  : 6 status icons (poison, paralyze, burning, drunk, bleeding, freezing)
    anchors     : 6 UI corner anchors (hp_bar, mp_bar, minimap, battle_list, inventory, chat)
    all         : All of the above (default)

Each category shows a reference region hint so you know roughly where
to look on the Tibia client.

Controls (in OpenCV window)
---------------------------
    Left-click + drag  → draw selection rectangle
    S                  → save current selection
    R                  → reset selection
    N                  → skip this template
    Q                  → quit current category
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "cache" / "templates"

# Template definitions: (filename, hint_text, approx_size)
SKULL_TEMPLATES = [
    ("white_skull",  "White skull — appears next to player name in battle list", (12, 12)),
    ("orange_skull", "Orange skull — player recently attacked someone",          (12, 12)),
    ("red_skull",    "Red skull — player has many unjustified kills",            (12, 12)),
    ("black_skull",  "Black skull — worst PK status",                            (12, 12)),
    ("green_skull",  "Green skull — party member indicator",                     (12, 12)),
]

CONDITION_TEMPLATES = [
    ("poison",    "Poison icon — green skull/drop in condition bar",   (12, 12)),
    ("paralyze",  "Paralyze icon — blue icon in condition bar",       (12, 12)),
    ("burning",   "Burning icon — orange/red flame in condition bar",  (12, 12)),
    ("drunk",     "Drunk icon — yellow/green bottle in condition bar", (12, 12)),
    ("bleeding",  "Bleeding icon — red drops in condition bar",        (12, 12)),
    ("freezing",  "Freezing icon — blue/cyan crystal in condition bar",(12, 12)),
]

ANCHOR_TEMPLATES = [
    ("hp_bar_corner",       "Top-left corner of the HP bar (green bar)",       (20, 12)),
    ("mp_bar_corner",       "Top-left corner of the MP bar (blue bar)",        (20, 12)),
    ("minimap_corner",      "Top-left corner of the minimap widget",           (20, 20)),
    ("battle_list_header",  "Header/title bar of the battle list panel",       (80, 16)),
    ("inventory_header",    "Header/title bar of the inventory panel",         (80, 16)),
    ("chat_header",         "Header/title bar of the chat panel",              (80, 16)),
]

# Metadata for anchors (offset from anchor to ROI origin, expected ROI size)
ANCHOR_META = {
    "hp_bar_corner":      {"offset": [0, 0],  "expected_size": [769, 12]},
    "mp_bar_corner":      {"offset": [0, 0],  "expected_size": [768, 12]},
    "minimap_corner":     {"offset": [0, 0],  "expected_size": [113, 115]},
    "battle_list_header": {"offset": [0, 16], "expected_size": [170, 200]},
    "inventory_header":   {"offset": [0, 16], "expected_size": [170, 300]},
    "chat_header":        {"offset": [0, 16], "expected_size": [400, 200]},
}


class InteractiveCropper:
    """OpenCV-based interactive rectangle cropper."""

    def __init__(self, frame: np.ndarray, title: str = "Crop") -> None:
        self._frame = frame.copy()
        self._display = frame.copy()
        self._title = title
        self._drawing = False
        self._ix = 0
        self._iy = 0
        self._fx = 0
        self._fy = 0
        self._has_rect = False

    def _mouse_cb(self, event: int, x: int, y: int, _f: int, _p: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self._drawing = True
            self._ix, self._iy = x, y
            self._fx, self._fy = x, y
            self._has_rect = False
        elif event == cv2.EVENT_MOUSEMOVE and self._drawing:
            self._fx, self._fy = x, y
            self._display = self._frame.copy()
            cv2.rectangle(self._display, (self._ix, self._iy), (x, y), (0, 255, 0), 2)
        elif event == cv2.EVENT_LBUTTONUP:
            self._drawing = False
            self._fx, self._fy = x, y
            self._has_rect = True

    def run(self) -> Optional[np.ndarray]:
        """Show window, let user crop. Returns cropped BGR array or None."""
        cv2.namedWindow(self._title, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self._title, self._mouse_cb)

        print(f"\n  [{self._title}]")
        print("  Drag to select | S=save | R=reset | N=skip | Q=quit category")

        while True:
            cv2.imshow(self._title, self._display)
            key = cv2.waitKey(30) & 0xFF

            if key == ord("s") and self._has_rect:
                x1 = min(self._ix, self._fx)
                y1 = min(self._iy, self._fy)
                x2 = max(self._ix, self._fx)
                y2 = max(self._iy, self._fy)
                if x2 - x1 > 2 and y2 - y1 > 2:
                    cv2.destroyWindow(self._title)
                    return self._frame[y1:y2, x1:x2].copy()
            elif key == ord("r"):
                self._display = self._frame.copy()
                self._has_rect = False
            elif key in (ord("n"), ord("q"), 27):
                cv2.destroyWindow(self._title)
                if key == ord("q"):
                    return None  # signal quit category
                return None  # skip

        return None  # unreachable but makes mypy happy


def capture_frame(source: str, monitor_idx: int, from_file: str) -> Optional[np.ndarray]:
    """Get a frame from the specified source."""
    if from_file:
        img = cv2.imread(from_file)
        if img is None:
            print(f"ERROR: Cannot read image: {from_file}")
            return None
        return img

    sys.path.insert(0, str(ROOT / "src"))
    from frame_capture import build_frame_getter  # type: ignore[import-untyped]

    getter = build_frame_getter(source, monitor_idx=monitor_idx)
    frame = getter()
    if hasattr(getter, "close"):
        getter.close()  # type: ignore[attr-defined]
    return frame


def capture_category(
    frame: np.ndarray,
    category: str,
    templates: list[tuple[str, str, tuple[int, int]]],
) -> int:
    """Interactively capture templates for a category. Returns count saved."""
    out_dir = TEMPLATES_DIR / category
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for filename, hint, _approx in templates:
        out_path = out_dir / f"{filename}.png"
        if out_path.exists():
            print(f"  [SKIP] {out_path.name} already exists")
            saved += 1
            continue

        print(f"\n  >> {hint}")
        print(f"     Target: {out_path}")
        cropper = InteractiveCropper(frame, f"{category}/{filename}")
        crop = cropper.run()

        if crop is None:
            print(f"  [SKIP] {filename}")
            continue

        cv2.imwrite(str(out_path), crop)
        print(f"  [SAVED] {out_path.name} ({crop.shape[1]}x{crop.shape[0]} px)")
        saved += 1

    return saved


def save_anchors_meta() -> None:
    """Write anchors_meta.json with offset + expected_size per anchor."""
    meta_path = TEMPLATES_DIR / "anchors" / "anchors_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(ANCHOR_META, indent=2), encoding="utf-8")
    print(f"  [META] Saved {meta_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture missing template images")
    parser.add_argument("--source", default="mss", help="Frame source (mss/dxcam/printwindow)")
    parser.add_argument("--monitor-idx", type=int, default=1, help="MSS monitor index")
    parser.add_argument("--from-file", default="", help="Use existing screenshot instead of live capture")
    parser.add_argument(
        "--category",
        choices=["skulls", "conditions", "anchors", "all"],
        default="all",
        help="Which template category to capture",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  TEMPLATE CAPTURE TOOL — Fill missing template directories")
    print("=" * 60)

    frame = capture_frame(args.source, args.monitor_idx, args.from_file)
    if frame is None:
        print("ERROR: Failed to capture frame. Exiting.")
        sys.exit(1)

    print(f"\n  Frame captured: {frame.shape[1]}x{frame.shape[0]}")

    categories: list[tuple[str, list[tuple[str, str, tuple[int, int]]]]] = []
    if args.category in ("skulls", "all"):
        categories.append(("skulls", SKULL_TEMPLATES))
    if args.category in ("conditions", "all"):
        categories.append(("conditions", CONDITION_TEMPLATES))
    if args.category in ("anchors", "all"):
        categories.append(("anchors", ANCHOR_TEMPLATES))

    total = 0
    for cat_name, cat_templates in categories:
        print(f"\n{'─' * 40}")
        print(f"  CATEGORY: {cat_name.upper()}")
        print(f"{'─' * 40}")
        n = capture_category(frame, cat_name, cat_templates)
        total += n
        print(f"  → {n}/{len(cat_templates)} templates saved for {cat_name}")

    # Always save anchors meta after anchor capture
    if args.category in ("anchors", "all"):
        save_anchors_meta()

    print(f"\n{'=' * 60}")
    print(f"  TOTAL: {total} templates saved")
    print(f"{'=' * 60}")
    remaining = check_missing()
    if remaining:
        print(f"\n  Still missing: {', '.join(remaining)}")
    else:
        print("\n  ALL templates captured! Modules fully operational.")


def check_missing() -> list[str]:
    """Return list of missing template files."""
    missing = []
    for name, _, _ in SKULL_TEMPLATES:
        p = TEMPLATES_DIR / "skulls" / f"{name}.png"
        if not p.exists():
            missing.append(f"skulls/{name}.png")
    for name, _, _ in CONDITION_TEMPLATES:
        p = TEMPLATES_DIR / "conditions" / f"{name}.png"
        if not p.exists():
            missing.append(f"conditions/{name}.png")
    for name, _, _ in ANCHOR_TEMPLATES:
        p = TEMPLATES_DIR / "anchors" / f"{name}.png"
        if not p.exists():
            missing.append(f"anchors/{name}.png")
    return missing


if __name__ == "__main__":
    main()
