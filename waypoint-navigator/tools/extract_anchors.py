"""Extract anchor templates from a game screenshot.

Coordinates determined from pixel analysis of 1920x1009 frames
(OBS Window Projection of Tibia Classic, standard layout).

Reference ROIs (calibrated at 1920x1080):
  hp_bar:      [12, 28, 769, 12]    (hpmp_config.json)
  mp_bar:      [788, 28, 768, 12]   (hpmp_config.json)
  minimap:     [1753, 30, 107, 109] (minimap_config.json)
  battle_list: [1569, 444, 162, 229](combat_config.json)

Usage:
    python tools/extract_anchors.py [screenshot_path]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent          # waypoint-navigator/
PROJ = ROOT.parent                                     # frbit/
TEMPLATES_DIR = ROOT / "cache" / "templates"
ANCHORS_DIR = TEMPLATES_DIR / "anchors"

# Reference resolution the ROI configs were calibrated at
REF_W, REF_H = 1920, 1080

# Reference ROIs from config files
_REF_ROIS = {
    "hp_bar":      [12, 28, 769, 12],
    "mp_bar":      [788, 28, 768, 12],
    "minimap":     [1753, 30, 107, 109],
    "battle_list": [1569, 444, 162, 229],
}


def _scale(roi: list[int], sx: float, sy: float) -> tuple[int, int, int, int]:
    x, y, w, h = roi
    return int(x * sx), int(y * sy), int(w * sx), int(h * sy)


def _crop(img: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    H, W = img.shape[:2]
    return img[max(0, y):min(H, y + h), max(0, x):min(W, x + w)].copy()


def _find_dark_line_groups(
    gray: np.ndarray, panel_x: int = 1560, panel_w: int = 160, threshold: int = 20,
) -> list[tuple[int, int]]:
    """Find groups of dark horizontal lines in the right panel (section separators)."""
    H = gray.shape[0]
    strip = gray[:, panel_x:panel_x + panel_w]
    avg = strip.mean(axis=1)

    dark: list[int] = [y for y in range(H) if avg[y] < threshold]
    if not dark:
        return []

    groups: list[tuple[int, int]] = []
    start = prev = dark[0]
    for dl in dark[1:]:
        if dl - prev > 3:
            groups.append((start, prev))
            start = dl
        prev = dl
    groups.append((start, prev))
    return groups


def extract(src_path: str | Path) -> None:
    """Extract anchor templates from screenshot."""
    src = Path(src_path)
    if not src.exists():
        print(f"ERROR: {src} not found"); sys.exit(1)

    img = cv2.imread(str(src))
    if img is None:
        print(f"ERROR: cannot read {src}"); sys.exit(1)

    H, W = img.shape[:2]
    sx, sy = W / REF_W, H / REF_H
    print(f"Source: {src.name} ({W}x{H}), scale=({sx:.4f}, {sy:.4f})")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Scale reference ROIs to actual frame size
    rois = {name: _scale(roi, sx, sy) for name, roi in _REF_ROIS.items()}
    for name, (x, y, w, h) in rois.items():
        print(f"  {name}: x={x} y={y} w={w} h={h}")

    # Find section headers in right panel
    dark_groups = _find_dark_line_groups(gray)
    print(f"\nDark-line groups (section borders): {len(dark_groups)}")
    for i, (ys, ye) in enumerate(dark_groups):
        print(f"  [{i}] y={ys}-{ye}")

    # Identify battle list and inventory headers
    mm_x, mm_y, mm_w, mm_h = rois["minimap"]
    mm_bottom = mm_y + mm_h

    # First section header after minimap → battle list header
    bl_header_y = None
    for ys, _ye in dark_groups:
        if ys > mm_bottom - 20:
            bl_header_y = ys
            break

    # Second major section header (>150px below first) → inventory
    inv_header_y = None
    if bl_header_y is not None:
        for ys, _ye in dark_groups:
            if ys > bl_header_y + 150:
                inv_header_y = ys
                break

    # Chat area: scan bottom third for brightness transition
    bottom_strip = gray[H * 2 // 3:, :W // 3].mean(axis=1)
    chat_y = H * 2 // 3
    for i in range(len(bottom_strip) - 5):
        # Look for text area start (brightness spike from messages)
        if bottom_strip[i] > 85:
            chat_y = H * 2 // 3 + i - 10
            break

    print(f"\nDetected positions:")
    print(f"  Battle list header: y={bl_header_y}")
    print(f"  Inventory header:   y={inv_header_y}")
    print(f"  Chat area:          y={chat_y}")

    # === Extract anchors ===
    ANCHORS_DIR.mkdir(parents=True, exist_ok=True)
    meta: dict[str, dict] = {}

    # 1. hp_bar_corner — left border of HP bar area
    hp_x, hp_y, hp_w, hp_h = rois["hp_bar"]
    ax, ay = max(0, hp_x - 12), max(0, hp_y - 12)
    patch = _crop(img, ax, ay, 30, 28)
    cv2.imwrite(str(ANCHORS_DIR / "hp_bar_corner.png"), patch)
    meta["hp_bar_corner"] = {
        "offset": [hp_x - ax, hp_y - ay],
        "expected_size": [hp_w, hp_h],
        "confidence": 0.70,
    }
    print(f"\n  hp_bar_corner: {patch.shape[1]}x{patch.shape[0]} from ({ax},{ay})")

    # 2. mp_bar_corner — left border of MP bar (HP/MP junction)
    mp_x, mp_y, mp_w, mp_h = rois["mp_bar"]
    ax, ay = max(0, mp_x - 12), max(0, mp_y - 12)
    patch = _crop(img, ax, ay, 30, 28)
    cv2.imwrite(str(ANCHORS_DIR / "mp_bar_corner.png"), patch)
    meta["mp_bar_corner"] = {
        "offset": [mp_x - ax, mp_y - ay],
        "expected_size": [mp_w, mp_h],
        "confidence": 0.70,
    }
    print(f"  mp_bar_corner: {patch.shape[1]}x{patch.shape[0]} from ({ax},{ay})")

    # 3. minimap_corner — top-left corner of minimap frame
    ax, ay = max(0, mm_x - 15), max(0, mm_y - 8)
    patch = _crop(img, ax, ay, 35, 35)
    cv2.imwrite(str(ANCHORS_DIR / "minimap_corner.png"), patch)
    meta["minimap_corner"] = {
        "offset": [mm_x - ax, mm_y - ay],
        "expected_size": [mm_w, mm_h],
        "confidence": 0.70,
    }
    print(f"  minimap_corner: {patch.shape[1]}x{patch.shape[0]} from ({ax},{ay})")

    # 4. battle_list_header — section header strip
    if bl_header_y is not None:
        ax, ay = 1560, max(0, bl_header_y - 2)
        patch = _crop(img, ax, ay, 120, 12)
        # Content starts ~25px below header
        content_y = bl_header_y + 25
        bl_x = rois["battle_list"][0]
        cv2.imwrite(str(ANCHORS_DIR / "battle_list_header.png"), patch)
        meta["battle_list_header"] = {
            "offset": [bl_x - ax, content_y - ay],
            "expected_size": [rois["battle_list"][2], 200],
            "confidence": 0.65,
        }
        print(f"  battle_list_header: {patch.shape[1]}x{patch.shape[0]} from ({ax},{ay})")
    else:
        print("  WARNING: battle_list_header not found")

    # 5. inventory_header — second section header
    if inv_header_y is not None:
        ax, ay = 1560, max(0, inv_header_y - 2)
        patch = _crop(img, ax, ay, 120, 12)
        cv2.imwrite(str(ANCHORS_DIR / "inventory_header.png"), patch)
        meta["inventory_header"] = {
            "offset": [9, 27],
            "expected_size": [162, 100],
            "confidence": 0.65,
        }
        print(f"  inventory_header: {patch.shape[1]}x{patch.shape[0]} from ({ax},{ay})")
    else:
        print("  WARNING: inventory_header not found")

    # 6. chat_header — chat console top area
    ax, ay = 5, max(0, chat_y - 3)
    patch = _crop(img, ax, ay, 120, 16)
    cv2.imwrite(str(ANCHORS_DIR / "chat_header.png"), patch)
    meta["chat_header"] = {
        "offset": [0, 16],
        "expected_size": [400, 200],
        "confidence": 0.60,
    }
    print(f"  chat_header: {patch.shape[1]}x{patch.shape[0]} from ({ax},{ay})")

    # === Save meta JSON ===
    meta_path = ANCHORS_DIR / "anchors_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\n  anchors_meta.json saved ({len(meta)} entries)")

    # === Validate ===
    ok = 0
    for name in meta:
        p = ANCHORS_DIR / f"{name}.png"
        a = cv2.imread(str(p))
        if a is not None and a.size > 0:
            print(f"  OK {name}: {a.shape[1]}x{a.shape[0]}")
            ok += 1
        else:
            print(f"  FAIL {name}")

    print(f"\nDone! {ok}/{len(meta)} anchors extracted to {ANCHORS_DIR}")


def main() -> None:
    if len(sys.argv) > 1:
        src = sys.argv[1]
    else:
        # Use the clean Hotkey screenshot (no colored overlays)
        candidates = [
            PROJ / "image" / "2026-03-01_124142220_Hiyoko San_Hotkey.png",
            PROJ / "image" / "annotated_rois.png",
        ]
        src = None
        for c in candidates:
            if c.exists():
                src = str(c)
                break
        if src is None:
            print("ERROR: No screenshot found. Pass path as argument.")
            sys.exit(1)

    extract(src)


if __name__ == "__main__":
    main()
