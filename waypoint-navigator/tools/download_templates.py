"""
download_templates.py — Descarga sprites de criatura/item desde Tibia Wiki
y genera templates 32x32 BGR PNG para corpse/loot/trade matching.

Uso:
    python tools/download_templates.py

Descarga sprites públicos de tibia.fandom.com, los convierte a 32x32 BGR PNG
y los deposita en cache/templates/{corpses,loot_items,trade_items}/.
"""

import io
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import requests
from PIL import Image as PILImage

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "templates"

# MediaWiki API para Tibia Wiki
API_URL = "https://tibia.fandom.com/api.php"

# ---------------------------------------------------------------------------
# Sprites a descargar
# ---------------------------------------------------------------------------

# Corpse sprites — nombre en wiki : nombre de archivo destino
CORPSE_SPRITES: Dict[str, str] = {
    "Dead_Wasp.gif": "wasp_corpse",
    "Dead_Spider.gif": "spider_corpse",
    "Dead_Poison_Spider.gif": "poison_spider_corpse",
    "Dead_Bug.gif": "bug_corpse",
    "Dead_Rotworm.gif": "rotworm_corpse",
    "Dead_Rat.gif": "rat_corpse",
    "Dead_Rat.gif": "cave_rat_corpse",   # cave rats share the same corpse sprite as rats
    "Dead_Snake.gif": "snake_corpse",
    "Dead_Troll.gif": "troll_corpse",
}

# Loot item sprites
LOOT_ITEM_SPRITES: Dict[str, str] = {
    "Gold_Coin.gif": "gold_coin",
    "Honeycomb.gif": "honeycomb",
    "Spider_Fangs.gif": "spider_fangs",
    "Poison_Spider_Shell.gif": "poison_spider_shell",
    "Cherry.gif": "cherry",
    "Meat.gif": "meat",
    "Bag.gif": "bag",
    "Worm.gif": "worm",
    "Cheese.gif": "cheese",
    # Note: Cave Rats in Tibia drop worm, gold coins, and cheese.
    # "Rat Tail" does not exist as a cave rat drop.
}

# Trade items (same sprites, used in NPC trade window inventory matching)
TRADE_ITEM_SPRITES: Dict[str, str] = {
    "Gold_Coin.gif": "gold_coin",
    "Honeycomb.gif": "honeycomb",
    "Spider_Fangs.gif": "spider_fangs",
    "Cherry.gif": "cherry",
    "Meat.gif": "meat",
}


def get_image_url(filename: str) -> Optional[str]:
    """Query MediaWiki API to get the direct image URL."""
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }
    try:
        r = requests.get(API_URL, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            ii = page.get("imageinfo", [])
            if ii:
                return ii[0].get("url")
    except Exception as e:
        print(f"  [WARN] API error for {filename}: {e}")
    return None


def download_image(url: str) -> Optional[np.ndarray]:
    """Download image from URL and return as BGR numpy array."""
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (tibia-bot-template-tool)"
        })
        r.raise_for_status()
        # Use PIL to handle GIF/PNG/etc
        img = PILImage.open(io.BytesIO(r.content))
        # For animated GIFs, take first frame
        pil_img = img.convert("RGBA")
        # Convert to numpy
        arr = np.array(pil_img)
        # RGBA -> BGR (remove alpha, apply to white background)
        alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
        rgb = arr[:, :, :3].astype(np.float32)
        # Composite on black background (Tibia game uses dark backgrounds)
        bgr = (rgb * alpha).astype(np.uint8)
        bgr = bgr[:, :, ::-1]  # RGB -> BGR
        return bgr
    except Exception as e:
        print(f"  [WARN] Download error: {e}")
        return None


def resize_to_32x32(img: np.ndarray) -> np.ndarray:
    """Resize image to 32x32 using INTER_AREA for downscale, INTER_LINEAR for upscale."""
    h, w = img.shape[:2]
    if h == 32 and w == 32:
        return img
    method = cv2.INTER_AREA if (h > 32 or w > 32) else cv2.INTER_LINEAR
    return cv2.resize(img, (32, 32), interpolation=method)


def process_sprites(
    sprite_map: Dict[str, str],
    dest_dir: Path,
    category: str,
) -> int:
    """Download and save sprites for one category. Returns count of saved files."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for wiki_filename, local_name in sprite_map.items():
        dest_path = dest_dir / f"{local_name}.png"
        if dest_path.exists():
            print(f"  [SKIP] {category}/{local_name}.png already exists")
            saved += 1
            continue

        print(f"  [GET]  {wiki_filename} -> {category}/{local_name}.png ... ", end="")
        url = get_image_url(wiki_filename)
        if not url:
            print("FAILED (no URL)")
            continue

        img = download_image(url)
        if img is None:
            print("FAILED (download)")
            continue

        img32 = resize_to_32x32(img)
        cv2.imwrite(str(dest_path), img32)
        saved += 1
        h, w = img.shape[:2]
        print(f"OK ({w}x{h} -> 32x32)")

    return saved


def generate_synthetic_ui_templates() -> int:
    """
    Generate synthetic trade UI templates (buy/sell buttons, NPC trade header).
    These are client-specific and can't be downloaded from the wiki.
    We create placeholder templates with distinctive colors that can be
    replaced with real captures later.
    """
    dest_dir = CACHE / "trade_items"
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    # Note: These synthetic templates are PLACEHOLDERS.
    # For real production use, capture actual UI elements from the game.
    # The trade_items directory already has item icons from the download above.
    print("  [INFO] Trade UI templates (buy_button, sell_button, npc_trade_header)")
    print("  [INFO] These must be captured from the actual game client.")
    print("  [INFO] Use: python tools/capture_templates.py --interactive")

    return count


def main() -> None:
    print("=" * 60)
    print("Tibia Template Downloader")
    print("=" * 60)
    print(f"Source: tibia.fandom.com (public wiki sprites)")
    print(f"Target: {CACHE}")
    print()

    # 1. Corpse templates
    print("[1/3] Corpse templates -> cache/templates/corpses/")
    n1 = process_sprites(CORPSE_SPRITES, CACHE / "corpses", "corpses")
    print(f"  => {n1}/{len(CORPSE_SPRITES)} corpse templates ready\n")

    # 2. Loot item templates
    print("[2/3] Loot item templates -> cache/templates/loot_items/")
    n2 = process_sprites(LOOT_ITEM_SPRITES, CACHE / "loot_items", "loot_items")
    print(f"  => {n2}/{len(LOOT_ITEM_SPRITES)} loot item templates ready\n")

    # 3. Trade item templates (same item sprites + UI note)
    print("[3/3] Trade item templates -> cache/templates/trade_items/")
    n3 = process_sprites(TRADE_ITEM_SPRITES, CACHE / "trade_items", "trade_items")
    generate_synthetic_ui_templates()
    print(f"  => {n3}/{len(TRADE_ITEM_SPRITES)} trade item templates ready\n")

    print("=" * 60)
    total = n1 + n2 + n3
    print(f"Total: {total} templates downloaded/ready")
    if total > 0:
        print("NEXT: Validate with  python tools/download_templates.py --validate")
    print("=" * 60)

    if "--validate" in sys.argv:
        validate_all()


def validate_all() -> None:
    """Check all template files are valid 32x32 BGR PNGs."""
    print("\n[VALIDATE] Checking all downloaded templates...")
    dirs = ["corpses", "loot_items", "trade_items"]
    ok = 0
    bad = 0
    for d in dirs:
        tdir = CACHE / d
        if not tdir.exists():
            continue
        for p in sorted(tdir.glob("*.png")):
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None:
                print(f"  [BAD] {d}/{p.name}: cannot read")
                bad += 1
                continue
            h, w, c = img.shape
            if h != 32 or w != 32 or c != 3:
                print(f"  [BAD] {d}/{p.name}: shape={img.shape} (expected 32x32x3)")
                bad += 1
            else:
                ok += 1
    print(f"  Valid: {ok}, Invalid: {bad}")


if __name__ == "__main__":
    main()
