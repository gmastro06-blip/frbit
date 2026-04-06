#!/usr/bin/env python3
"""
tools/validate_templates.py — Validate all template images in cache/templates/.

Checks:
  1. Each image loads with cv2.imread (not corrupted).
  2. Dimensions are within sane bounds (>= 8px, <= 200px per side).
  3. Not blank (mean pixel value > 5).
  4. (Optional) If --screenshot is given, run matchTemplate against it and
     report confidence for each template.

Usage:
  python tools/validate_templates.py
  python tools/validate_templates.py --screenshot path/to/screenshot.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _ROOT / "cache" / "templates"

# Bounds for sane template dimensions
MIN_SIDE = 8
MAX_SIDE = 200

# Groups of template subdirectories to scan
_SUBDIRS = [
    "monsters",
    "corpses",
    "loot_items",
    "conditions",
    "anchors",
    "skulls",
    "trade_items",
]

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _is_image_file(p: Path) -> bool:
    return p.suffix.lower() in _IMAGE_EXTS


def _scan_templates() -> List[Tuple[str, Path]]:
    """Return (category, path) for every image in template directories."""
    found: List[Tuple[str, Path]] = []
    for subdir in _SUBDIRS:
        d = _TEMPLATES_DIR / subdir
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file() and _is_image_file(f):
                found.append((subdir, f))
    # Also scan root templates/ for uncategorized images
    for f in sorted(_TEMPLATES_DIR.glob("*")):
        if f.is_file() and _is_image_file(f):
            found.append(("root", f))
    return found


def validate_template(
    path: Path,
    *,
    screenshot: np.ndarray | None = None,
) -> Tuple[bool, str, float | None]:
    """Validate a single template image.

    Returns (ok, message, confidence_vs_screenshot_or_None).
    """
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return False, "FAILED to load (corrupted or unreadable)", None

    h, w = img.shape[:2]
    if w < MIN_SIDE or h < MIN_SIDE:
        return False, f"TOO SMALL ({w}x{h} — min {MIN_SIDE}px)", None
    if w > MAX_SIDE or h > MAX_SIDE:
        return False, f"TOO LARGE ({w}x{h} — max {MAX_SIDE}px)", None

    mean_val = float(np.mean(img))
    if mean_val < 5.0:
        return False, f"BLANK (mean pixel {mean_val:.1f})", None

    conf: float | None = None
    if screenshot is not None:
        sh, sw = screenshot.shape[:2]
        if h <= sh and w <= sw:
            result = cv2.matchTemplate(screenshot, img, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            conf = float(max_val)

    status = "OK"
    ok = True
    if conf is not None and conf < 0.50:
        status = f"LOW CONF ({conf:.2f})"
        # Still "ok" structurally, but might not match
    return ok, f"{status} ({w}x{h}, mean={mean_val:.0f})", conf


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate template images")
    parser.add_argument(
        "--screenshot", "-s",
        type=str,
        default=None,
        help="Path to a real screenshot to test template matching confidence",
    )
    args = parser.parse_args()

    screenshot: np.ndarray | None = None
    if args.screenshot:
        screenshot = cv2.imread(args.screenshot, cv2.IMREAD_COLOR)
        if screenshot is None:
            print(f"ERROR: Cannot load screenshot: {args.screenshot}")
            return 1
        print(f"Screenshot loaded: {screenshot.shape[1]}x{screenshot.shape[0]}")

    templates = _scan_templates()
    if not templates:
        print(f"No template images found in {_TEMPLATES_DIR}")
        return 1

    # Group by category
    categories: dict[str, List[Tuple[Path, bool, str, float | None]]] = {}
    for cat, path in templates:
        ok, msg, conf = validate_template(path, screenshot=screenshot)
        categories.setdefault(cat, []).append((path, ok, msg, conf))

    # Report
    total = 0
    passed = 0
    failed = 0
    low_conf = 0

    for cat in _SUBDIRS + ["root"]:
        items = categories.get(cat, [])
        if not items:
            print(f"\n=== {cat}/ === (empty)")
            continue

        print(f"\n=== {cat}/ === ({len(items)} templates)")
        for path, ok, msg, conf in items:
            total += 1
            icon = "OK" if ok else "FAIL"
            conf_str = f" conf={conf:.2f}" if conf is not None else ""
            if ok:
                passed += 1
            else:
                failed += 1
            if conf is not None and conf < 0.50:
                low_conf += 1
            print(f"  {icon} {path.name:40s} {msg}{conf_str}")

    print(f"\n{'='*60}")
    print(f"Total: {total} | Passed: {passed} | Failed: {failed} | Low confidence: {low_conf}")

    if screenshot is None:
        print("\nTip: run with --screenshot <path> to test template matching confidence.")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
