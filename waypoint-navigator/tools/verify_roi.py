#!/usr/bin/env python
"""
ROI Verification Tool
---------------------
Loads all JSON config files with ROI values, validates them against a
reference resolution (default 1920×1080), and optionally overlays the
ROIs onto a screenshot for visual inspection.

Usage:
    python tools/verify_roi.py                         # validate only
    python tools/verify_roi.py --screenshot cap.png    # validate + overlay image
    python tools/verify_roi.py --capture               # capture live + overlay
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Colour palette for ROI overlays ──────────────────────────────────────
_COLORS: Dict[str, Tuple[int, int, int]] = {
    "hp_roi":          (0, 255, 0),      # green
    "mp_roi":          (255, 100, 0),     # orange
    "text_roi":        (200, 200, 0),     # yellow
    "roi":             (0, 200, 255),     # cyan  (minimap)
    "battle_list_roi": (255, 0, 0),       # red
    "viewport_roi":    (255, 255, 255),   # white
    "chat_roi":        (200, 0, 200),     # magenta
}

# ── Config loaders ───────────────────────────────────────────────────────

def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_all_rois(ref_w: int = 1920, ref_h: int = 1080) -> List[Dict[str, Any]]:
    """Return a list of dicts: {name, roi, source, valid, issues}."""
    results: List[Dict[str, Any]] = []

    # hpmp_config.json
    hpmp = _load_json(ROOT / "hpmp_config.json")
    for key in ("hp_roi", "mp_roi", "text_roi"):
        if key in hpmp:
            results.append(_check_roi(key, hpmp[key], "hpmp_config.json", ref_w, ref_h))

    # minimap_config.json
    mm = _load_json(ROOT / "minimap_config.json")
    if "roi" in mm:
        results.append(_check_roi("roi (minimap)", mm["roi"], "minimap_config.json", ref_w, ref_h))

    # combat_config.json
    cc = _load_json(ROOT / "combat_config.json")
    if "battle_list_roi" in cc:
        results.append(
            _check_roi("battle_list_roi", cc["battle_list_roi"], "combat_config.json", ref_w, ref_h)
        )

    # detector_config.json (if exists)
    dc = _load_json(ROOT / "detector_config.json")
    for key in dc:
        if key.endswith("_roi") and isinstance(dc[key], list):
            results.append(_check_roi(key, dc[key], "detector_config.json", ref_w, ref_h))

    return results


def _check_roi(
    name: str,
    roi: Any,
    source: str,
    ref_w: int,
    ref_h: int,
) -> Dict[str, Any]:
    """Validate a single [x, y, w, h] ROI."""
    issues: List[str] = []

    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        return {"name": name, "roi": roi, "source": source, "valid": False,
                "issues": ["Not a 4-element list"]}

    x, y, w, h = roi
    if w <= 0 or h <= 0:
        issues.append(f"Width ({w}) or height ({h}) <= 0")
    if x < 0 or y < 0:
        issues.append(f"Negative origin ({x}, {y})")
    if x + w > ref_w:
        issues.append(f"Extends past right edge: x+w={x + w} > {ref_w}")
    if y + h > ref_h:
        issues.append(f"Extends past bottom edge: y+h={y + h} > {ref_h}")
    if w * h < 16:
        issues.append(f"Area too small: {w * h}px²")
    if w * h > ref_w * ref_h * 0.5:
        issues.append(f"Area suspiciously large: {w * h}px² (>{ref_w * ref_h * 0.5})")

    return {
        "name": name,
        "roi": [x, y, w, h],
        "source": source,
        "valid": len(issues) == 0,
        "issues": issues,
    }


def print_report(results: List[Dict[str, Any]]) -> int:
    """Print a human-readable report, return number of issues."""
    total_issues = 0
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║          ROI Configuration Verification              ║")
    print("╚══════════════════════════════════════════════════════╝\n")

    for r in results:
        status = "✓" if r["valid"] else "✗"
        roi_str = str(r["roi"]) if isinstance(r["roi"], list) else "INVALID"
        print(f"  {status}  {r['name']:<25} {roi_str:<30} ({r['source']})")
        for iss in r["issues"]:
            print(f"       ⚠  {iss}")
            total_issues += 1

    print(f"\n  Total: {len(results)} ROIs checked, {total_issues} issues found.\n")
    return total_issues


def draw_overlay(img: np.ndarray, results: List[Dict[str, Any]]) -> np.ndarray:
    """Draw all valid ROIs onto the image with labels."""
    import cv2

    out = img.copy()
    for r in results:
        if not r["valid"]:
            continue
        x, y, w, h = r["roi"]
        # Determine colour
        base_name = r["name"].split(" ")[0]  # strip " (minimap)" etc.
        color = _COLORS.get(base_name, (200, 200, 200))
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        label = r["name"]
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(out, (x, y - th - 6), (x + tw + 4, y), color, -1)
        cv2.putText(out, label, (x + 2, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 0, 0), 1, cv2.LINE_AA)
    return out


def capture_live() -> np.ndarray | None:
    """Capture a screenshot via MSS (primary monitor)."""
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            shot = sct.grab(monitor)
            return np.array(shot)[:, :, :3]  # drop alpha, BGR
    except Exception as exc:
        print(f"  ✗ MSS capture failed: {exc}")
        return None


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Verify ROI configurations")
    parser.add_argument("--screenshot", default="", help="Path to screenshot PNG for overlay")
    parser.add_argument("--capture", action="store_true", help="Capture live screenshot via MSS")
    parser.add_argument("--ref-width", type=int, default=1920, help="Reference width (default 1920)")
    parser.add_argument("--ref-height", type=int, default=1080, help="Reference height (default 1080)")
    parser.add_argument("--output", default="output/roi_overlay.png", help="Where to save overlay")
    args = parser.parse_args()

    results = load_all_rois(args.ref_width, args.ref_height)
    issues = print_report(results)

    # Optional overlay
    img = None
    if args.screenshot:
        import cv2
        img = cv2.imread(args.screenshot)
        if img is None:
            print(f"  ✗ Could not load {args.screenshot}")
    elif args.capture:
        img = capture_live()

    if img is not None:
        overlay = draw_overlay(img, results)
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        import cv2
        cv2.imwrite(str(out_path), overlay)
        print(f"  → Overlay saved to {out_path}")

    sys.exit(1 if issues > 0 else 0)


if __name__ == "__main__":
    main()
