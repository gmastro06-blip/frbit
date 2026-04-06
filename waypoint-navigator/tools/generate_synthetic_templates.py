"""Generate synthetic skull & condition templates for PvP and condition detection.

These are *approximations*. For pixel-perfect matching with a real Tibia client,
replace them with actual screenshots captured via tools/capture_templates.py.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

TEMPLATES = Path(__file__).resolve().parent.parent / "cache" / "templates"

# ── Skull pixel art (11×11, BGR) ─────────────────────────────────────────────
# Classic Tibia battle-list skull shape: cranium + eyes + jaw
# 0 = background (dark), 1 = skull outline colour

_SKULL_MASK = np.array([
    [0,0,0,1,1,1,1,1,0,0,0],
    [0,0,1,1,1,1,1,1,1,0,0],
    [0,1,1,1,1,1,1,1,1,1,0],
    [0,1,1,1,1,1,1,1,1,1,0],
    [0,1,1,0,1,1,1,0,1,1,0],
    [0,1,1,0,1,1,1,0,1,1,0],
    [0,0,1,1,1,1,1,1,1,0,0],
    [0,0,1,1,0,1,0,1,1,0,0],
    [0,0,0,1,1,1,1,1,0,0,0],
    [0,0,0,0,1,0,1,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0],
], dtype=np.uint8)

# BGR colours matching Tibia skull colours
_SKULL_COLORS: dict[str, tuple[int, int, int]] = {
    "white_skull":  (255, 255, 255),
    "red_skull":    (0,   0,   255),
    "black_skull":  (30,  30,  30),
    "yellow_skull": (0,   255, 255),
    "orange_skull": (0,   165, 255),
    "green_skull":  (0,   255, 0),
}

_BG_COLOR = (40, 40, 40)  # dark grey background like Tibia battle list


def _make_skull(name: str, color_bgr: tuple[int, int, int]) -> np.ndarray:
    h, w = _SKULL_MASK.shape
    img = np.full((h, w, 3), _BG_COLOR, dtype=np.uint8)
    img[_SKULL_MASK == 1] = color_bgr
    return img


# ── Condition icon pixel art (12×12, BGR) ────────────────────────────────────
# Each icon is a solid tinted region with a simple shape so that
# cv2.matchTemplate + colour analysis both work.  The HSV values are chosen
# to fall inside the _HSV_RANGES defined in condition_monitor.py.

def _hsv_to_bgr(h: int, s: int, v: int) -> tuple[int, int, int]:
    """Convert a single HSV pixel to BGR tuple."""
    px = np.array([[[h, s, v]]], dtype=np.uint8)
    bgr = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)
    return int(bgr[0, 0, 0]), int(bgr[0, 0, 1]), int(bgr[0, 0, 2])


# Condition template specs: (name, hsv_center, shape_kind)
#  hsv_center chosen inside the ranges from condition_monitor._HSV_RANGES
_CONDITION_SPECS: list[tuple[str, tuple[int, int, int], str]] = [
    # poison: HSV (40-80, 80-255, 50-220) → pick (60, 180, 140)
    ("poison",   (60, 180, 140), "circle"),
    # paralyze: HSV (100-140, 80-255, 50-200) → pick (120, 160, 130)
    ("paralyze", (120, 160, 130), "circle"),
    # burning: HSV (8-25, 150-255, 150-255) → pick (16, 200, 200)
    ("burning",  (16, 200, 200), "diamond"),
    # drunk: HSV (26-45, 100-255, 120-255) → pick (35, 160, 180)
    ("drunk",    (35, 160, 180), "circle"),
    # bleeding: HSV (0-5, 180-255, 120-255) → pick (2, 220, 190)
    ("bleeding", (2,  220, 190), "diamond"),
    # freezing: HSV (85-105, 80-255, 160-255) → pick (95, 150, 200)
    ("freezing", (95, 150, 200), "circle"),
]


def _make_condition(name: str, hsv: tuple[int, int, int], shape: str) -> np.ndarray:
    size = 12
    bg = (30, 30, 30)
    color_bgr = _hsv_to_bgr(*hsv)
    img = np.full((size, size, 3), bg, dtype=np.uint8)

    if shape == "circle":
        cv2.circle(img, (size // 2, size // 2), size // 2 - 1, color_bgr, -1)
    elif shape == "diamond":
        pts = np.array([
            [size // 2, 1],
            [size - 2, size // 2],
            [size // 2, size - 2],
            [1, size // 2],
        ], dtype=np.int32)
        cv2.fillPoly(img, [pts], color_bgr)

    return img


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    skulls_dir = TEMPLATES / "skulls"
    conditions_dir = TEMPLATES / "conditions"
    skulls_dir.mkdir(parents=True, exist_ok=True)
    conditions_dir.mkdir(parents=True, exist_ok=True)

    print("=== Generating skull templates ===")
    for name, color in _SKULL_COLORS.items():
        img = _make_skull(name, color)
        path = skulls_dir / f"{name}.png"
        cv2.imwrite(str(path), img)
        print(f"  ✔ {path.name}  ({img.shape[1]}×{img.shape[0]})")

    print("\n=== Generating condition templates ===")
    for name, hsv, shape in _CONDITION_SPECS:
        img = _make_condition(name, hsv, shape)
        path = conditions_dir / f"{name}.png"
        cv2.imwrite(str(path), img)
        h_center, s_center, v_center = hsv
        print(f"  ✔ {path.name}  ({img.shape[1]}×{img.shape[0]})  HSV=({h_center},{s_center},{v_center})")

    print(f"\nDone — {len(_SKULL_COLORS)} skulls + {len(_CONDITION_SPECS)} conditions generated.")


if __name__ == "__main__":
    main()
