#!/usr/bin/env python3
"""Download corpse templates from Tibia Wiki Corpses page.

Source: https://tibia.fandom.com/wiki/Corpses

In Tibia, some creatures share corpse sprites:
  - Wasp / Bug → Dead Cockroach
  - Poison Spider → Dead Spider
  - Rotworm → Dead Centipede

This script downloads the real sprites via MediaWiki API and creates
aliases for the shared corpses.
"""

from __future__ import annotations

import shutil
import sys
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image

API = "https://tibia.fandom.com/api.php"
DEST = Path(__file__).resolve().parent.parent / "cache" / "templates" / "corpses"

# ── Real corpse sprites that exist in the wiki ──────────────────────
REAL_CORPSES: dict[str, str] = {
    # output_name → wiki filename (File:…)
    "rat_corpse":        "Dead_Rat.gif",
    "spider_corpse":     "Dead_Spider.gif",
    "snake_corpse":      "Dead_Snake.gif",
    "troll_corpse":      "Dead_Troll.gif",
    "wolf_corpse":       "Dead_Wolf.gif",
    "cockroach_corpse":  "Dead_Cockroach.gif",
    "centipede_corpse":  "Dead_Centipede.gif",
    "bat_corpse":        "Dead_Bat.gif",
    "chicken_corpse":    "Dead_Chicken.gif",
    "cat_corpse":        "Dead_Cat.gif",
    "rabbit_corpse":     "Dead_Rabbit.gif",
}

# ── Aliases: creatures that share a corpse with another ─────────────
ALIASES: dict[str, str] = {
    # alias_output_name → copies from this REAL_CORPSES key
    "wasp_corpse":           "cockroach_corpse",
    "bug_corpse":            "cockroach_corpse",
    "poison_spider_corpse":  "spider_corpse",
    "rotworm_corpse":        "centipede_corpse",
}


def get_image_url(wiki_filename: str) -> str | None:
    """Resolve wiki filename → direct image URL via MediaWiki API."""
    resp = requests.get(API, params={
        "action": "query",
        "titles": f"File:{wiki_filename}",
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }, timeout=15)
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        ii = page.get("imageinfo", [])
        if ii:
            return ii[0]["url"]
    return None


def download_and_save(url: str, dest_path: Path) -> bool:
    """Download image from URL, convert to 32×32 BGR PNG."""
    resp = requests.get(url, timeout=15)
    if resp.status_code != 200:
        return False

    img: Image.Image = Image.open(BytesIO(resp.content))

    # Handle RGBA / palette with transparency
    if img.mode in ("RGBA", "PA"):
        bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg.convert("RGB")
    elif img.mode == "P":
        if "transparency" in img.info:
            img = img.convert("RGBA")
            bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg.convert("RGB")
        else:
            img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    arr = np.array(img)  # RGB
    arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    arr = cv2.resize(arr, (32, 32), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dest_path), arr)
    return True


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)

    ok, fail = 0, 0

    # 1) Download real corpse sprites
    for name, wiki_file in REAL_CORPSES.items():
        dest_path = DEST / f"{name}.png"
        url = get_image_url(wiki_file)
        if url is None:
            print(f"  [FAIL] {name}: wiki file '{wiki_file}' not found")
            fail += 1
            continue
        if download_and_save(url, dest_path):
            print(f"  [OK]   {name} <- {wiki_file}")
            ok += 1
        else:
            print(f"  [FAIL] {name}: download failed")
            fail += 1

    # 2) Create alias copies
    for alias, source in ALIASES.items():
        src_path = DEST / f"{source}.png"
        dst_path = DEST / f"{alias}.png"
        if not src_path.exists():
            print(f"  [FAIL] {alias}: source '{source}' missing")
            fail += 1
            continue
        shutil.copy2(src_path, dst_path)
        print(f"  [OK]   {alias} <- copy of {source}")
        ok += 1

    # 3) Validate all
    print(f"\n-- Validation --")
    all_pngs = sorted(DEST.glob("*.png"))
    valid = invalid = 0
    for p in all_pngs:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is not None and img.shape == (32, 32, 3):
            valid += 1
        else:
            shape = img.shape if img is not None else "None"
            print(f"  [BAD]  INVALID: {p.name} -> shape={shape}")
            invalid += 1

    print(f"\nTotal: {ok} downloaded/copied, {fail} failed")
    print(f"Valid: {valid}/{len(all_pngs)}, Invalid: {invalid}")
    if fail > 0 or invalid > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
