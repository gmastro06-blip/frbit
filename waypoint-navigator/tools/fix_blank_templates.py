#!/usr/bin/env python
"""
Fix blank/near-blank templates by re-downloading and cropping to bounding box.

These templates have tiny sprites on 32x32 black backgrounds, making them
nearly useless for template matching. This script:
1. Re-downloads the original GIF sprites from Tibia Wiki
2. Crops to the non-transparent bounding box
3. Resizes to a minimum 16x16 with aspect ratio preserved
4. Composites onto black and saves as BGR PNG
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Optional, Tuple
from urllib.request import urlopen, Request
from urllib.parse import quote
import json

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "templates"
API_URL = "https://tibia.fandom.com/api.php"

# Templates that need fixing: (subdir, filename_stem, wiki_name)
FIXES = [
    ("monsters", "assassin", "Assassin"),
    ("monsters", "azure_frog", "Azure_Frog"),
    ("monsters", "black_sheep", "Black_Sheep"),
    ("monsters", "coral_frog", "Coral_Frog"),
    ("monsters", "crimson_frog", "Crimson_Frog"),
    ("monsters", "necromancer", "Necromancer"),
    ("monsters", "orchid_frog", "Orchid_Frog"),
    ("monsters", "rat", "Rat"),
    ("monsters", "salamander", "Salamander"),
    ("monsters", "soulsnatcher", "Soulsnatcher"),
    ("monsters", "vampire", "Vampire"),
    ("loot_items", "worm", "Worm"),
]


def get_wiki_image_url(wiki_name: str) -> Optional[str]:
    """Get direct image URL from Tibia Wiki API."""
    params = (
        f"?action=query&titles=File:{quote(wiki_name)}.gif"
        f"&prop=imageinfo&iiprop=url&format=json"
    )
    url = API_URL + params
    req = Request(url, headers={"User-Agent": "WaypointNavigator/1.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            ii = page.get("imageinfo", [])
            if ii:
                return ii[0].get("url")
    except Exception as e:
        print(f"  [WARN] API error for {wiki_name}: {e}")
    return None


def download_and_crop(
    wiki_name: str, min_size: int = 16
) -> Optional[np.ndarray]:
    """Download sprite, crop to bounding box, ensure min size."""
    url = get_wiki_image_url(wiki_name)
    if not url:
        print(f"  [FAIL] No URL for {wiki_name}")
        return None

    req = Request(url, headers={"User-Agent": "WaypointNavigator/1.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"  [FAIL] Download error for {wiki_name}: {e}")
        return None

    # Load with PIL to handle GIF animation + transparency
    pil_img = Image.open(io.BytesIO(raw))
    
    # For GIFs, get first frame
    if hasattr(pil_img, "n_frames") and pil_img.n_frames > 1:
        pil_img.seek(0)
    
    # Convert to RGBA
    rgba = pil_img.convert("RGBA")
    arr = np.array(rgba)  # H, W, 4 (RGBA)
    
    alpha = arr[:, :, 3]
    
    # Find bounding box of non-transparent pixels
    rows = np.any(alpha > 10, axis=1)
    cols = np.any(alpha > 10, axis=0)
    
    if not rows.any() or not cols.any():
        print(f"  [FAIL] {wiki_name}: Fully transparent")
        return None
    
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    
    # Crop to bounding box with 1px padding
    rmin = max(0, rmin - 1)
    cmin = max(0, cmin - 1)
    rmax = min(arr.shape[0] - 1, rmax + 1)
    cmax = min(arr.shape[1] - 1, cmax + 1)
    
    cropped = arr[rmin:rmax + 1, cmin:cmax + 1]
    
    # Get RGB and alpha
    rgb = cropped[:, :, :3]
    crop_alpha = cropped[:, :, 3:4] / 255.0
    
    # Composite onto black background
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    result = (bgr.astype(np.float32) * crop_alpha).astype(np.uint8)
    
    h, w = result.shape[:2]
    
    # Scale up if too small (maintain aspect ratio)
    if max(h, w) < min_size:
        scale = min_size / max(h, w)
        new_w = max(min_size, int(w * scale))
        new_h = max(min_size, int(h * scale))
        result = cv2.resize(result, (new_w, new_h), interpolation=cv2.INTER_NEAREST).astype(np.uint8)
    
    # Cap at 32x32 if larger
    h, w = result.shape[:2]
    if max(h, w) > 32:
        scale = 32 / max(h, w)
        new_w = int(w * scale)
        new_h = int(h * scale)
        result = cv2.resize(result, (new_w, new_h), interpolation=cv2.INTER_AREA).astype(np.uint8)
    
    return result


def main() -> None:
    print("=" * 60)
    print("  Fix Blank Templates — Crop to Bounding Box")
    print("=" * 60)
    
    fixed = 0
    failed = 0
    
    for subdir, stem, wiki_name in FIXES:
        out_path = CACHE / subdir / f"{stem}.png"
        
        # Check current state
        if out_path.exists():
            old = cv2.imread(str(out_path))
            old_mean = old.mean() if old is not None else 0
        else:
            old_mean = 0
        
        print(f"\n  {subdir}/{stem}.png (old mean={old_mean:.1f})")
        
        result = download_and_crop(wiki_name)
        if result is None:
            failed += 1
            continue
        
        h, w = result.shape[:2]
        new_mean = result.mean()
        
        # Save
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), result)
        
        print(f"    -> {w}x{h} mean={new_mean:.1f} (was {old_mean:.1f}) [OK]")
        fixed += 1
    
    print(f"\n{'=' * 60}")
    print(f"  Fixed: {fixed}  Failed: {failed}")
    print(f"{'=' * 60}")
    
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
