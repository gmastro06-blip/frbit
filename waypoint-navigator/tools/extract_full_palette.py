"""Extract the COMPLETE Tibia minimap palette from all 16 floor PNGs.

Cross-reference each color with the path PNG walkability data to determine
which colors are walkable and which are not.

This is the definitive source of truth — every pixel that appears in the
official tibiamaps.io floor PNGs is analysed.
"""
import cv2
import numpy as np
import os
import json
from collections import defaultdict

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")

# ── Collect every unique BGR color across ALL 16 floors ──────────────────────

# For each unique RGB color, track: total pixels, walkable pixels, non-walkable pixels
color_stats: dict[tuple[int, int, int], dict] = defaultdict(
    lambda: {"total": 0, "walkable": 0, "non_walkable": 0, "floors": set()}
)

for floor in range(16):
    map_path = os.path.join(CACHE, f"floor-{floor:02d}-map.png")
    path_path = os.path.join(CACHE, f"floor-{floor:02d}-path.png")

    if not os.path.exists(map_path):
        print(f"  Floor {floor:02d}: SKIP (no map)")
        continue

    # Load map image (RGBA → BGR)
    map_img = cv2.imread(map_path, cv2.IMREAD_UNCHANGED)
    if map_img is None:
        print(f"  Floor {floor:02d}: SKIP (read error)")
        continue

    # Handle RGBA or RGB
    if map_img.shape[2] == 4:
        alpha = map_img[:, :, 3]
        bgr = map_img[:, :, :3]
    else:
        alpha = np.full(map_img.shape[:2], 255, dtype=np.uint8)
        bgr = map_img

    # Load path image for walkability
    has_path = os.path.exists(path_path)
    if has_path:
        path_img = cv2.imread(path_path, cv2.IMREAD_UNCHANGED)
        if path_img is not None and path_img.shape[2] >= 3:
            pr = path_img[:, :, 2] if path_img.shape[2] >= 3 else path_img[:, :, 0]
            pg = path_img[:, :, 1]
            pb = path_img[:, :, 0]
            # Walkability from path PNG:
            # Yellow (255,255,0) = non-walkable wall
            # Black (<10,<10,<10) = non-walkable/unexplored
            # White (>245,>245,>245) = unexplored
            # Gray (R==G==B, 1-254) = walkable
            is_yellow = (pr == 255) & (pg == 255) & (pb == 0)
            is_black = (pr < 10) & (pg < 10) & (pb < 10)
            is_white = (pr > 245) & (pg > 245) & (pb > 245)
            walkable = ~is_yellow & ~is_black & ~is_white
        else:
            walkable = None
    else:
        walkable = None

    # Only process non-transparent, non-black pixels (black = unexplored background)
    h, w = bgr.shape[:2]
    mask = (alpha > 0) & ~((bgr[:,:,0] == 0) & (bgr[:,:,1] == 0) & (bgr[:,:,2] == 0))

    # Flatten
    pixels = bgr[mask]  # shape (N, 3)
    walk_vals = walkable[mask] if walkable is not None else None

    # Count unique colors on this floor
    floor_colors = set()
    for i in range(len(pixels)):
        b, g, r = int(pixels[i, 0]), int(pixels[i, 1]), int(pixels[i, 2])
        key = (b, g, r)
        floor_colors.add(key)
        color_stats[key]["total"] += 1
        if walk_vals is not None:
            if walk_vals[i]:
                color_stats[key]["walkable"] += 1
            else:
                color_stats[key]["non_walkable"] += 1
        color_stats[key]["floors"].add(floor)

    print(f"  Floor {floor:02d}: {len(pixels):>8d} non-black pixels, {len(floor_colors):>4d} unique colors")

# ── Convert floor sets to lists for JSON ─────────────────────────────────────

print(f"\n{'='*70}")
print(f"TOTAL UNIQUE BGR COLORS ACROSS ALL FLOORS: {len(color_stats)}")
print(f"{'='*70}\n")

# ── Sort by total pixel count ────────────────────────────────────────────────

sorted_colors = sorted(color_stats.items(), key=lambda x: x[1]["total"], reverse=True)

# ── Classify each color ─────────────────────────────────────────────────────

print(f"{'BGR':>20s}  {'RGB':>20s}  {'Total':>9s}  {'Walk':>8s}  {'NoWalk':>8s}  {'%Walk':>6s}  {'Floors':>10s}  {'Classification'}")
print("-" * 120)

walkable_colors = []
non_walkable_colors = []
ambiguous_colors = []

for (b, g, r), stats in sorted_colors:
    total = stats["total"]
    w = stats["walkable"]
    nw = stats["non_walkable"]
    floors = sorted(stats["floors"])

    # Determine walkability
    if w + nw == 0:
        pct = "N/A"
        classification = "unknown"
    else:
        pct_val = w / (w + nw) * 100
        pct = f"{pct_val:5.1f}%"
        if pct_val >= 80:
            classification = "WALKABLE"
        elif pct_val <= 20:
            classification = "NON-WALKABLE"
        else:
            classification = f"AMBIGUOUS ({pct_val:.0f}%)"

    floor_str = ",".join(str(f) for f in floors[:8])
    if len(floors) > 8:
        floor_str += "..."

    if total >= 100:  # Only show significant colors
        print(f"  ({b:3d},{g:3d},{r:3d})  ({r:3d},{g:3d},{b:3d})  {total:>9d}  {w:>8d}  {nw:>8d}  {pct:>6s}  {floor_str:>10s}  {classification}")

    if classification == "WALKABLE":
        walkable_colors.append({"bgr": [b, g, r], "rgb": [r, g, b], "total": total, "pct_walk": round(w/(w+nw)*100, 1) if w+nw > 0 else 0, "floors": floors})
    elif classification == "NON-WALKABLE":
        non_walkable_colors.append({"bgr": [b, g, r], "rgb": [r, g, b], "total": total, "pct_walk": round(w/(w+nw)*100, 1) if w+nw > 0 else 0, "floors": floors})
    else:
        ambiguous_colors.append({"bgr": [b, g, r], "rgb": [r, g, b], "total": total, "pct_walk": round(w/(w+nw)*100, 1) if w+nw > 0 else 0, "floors": floors})

# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*70}")
print(f"SUMMARY")
print(f"{'='*70}")
print(f"\n  WALKABLE colors (>80% walkable in path data):")
for c in sorted(walkable_colors, key=lambda x: x["total"], reverse=True):
    if c["total"] >= 100:
        print(f"    BGR ({c['bgr'][0]:3d},{c['bgr'][1]:3d},{c['bgr'][2]:3d})  RGB ({c['rgb'][0]:3d},{c['rgb'][1]:3d},{c['rgb'][2]:3d})  {c['total']:>9d} px  {c['pct_walk']:5.1f}% walk  floors: {c['floors']}")

print(f"\n  NON-WALKABLE colors (<20% walkable in path data):")
for c in sorted(non_walkable_colors, key=lambda x: x["total"], reverse=True):
    if c["total"] >= 100:
        print(f"    BGR ({c['bgr'][0]:3d},{c['bgr'][1]:3d},{c['bgr'][2]:3d})  RGB ({c['rgb'][0]:3d},{c['rgb'][1]:3d},{c['rgb'][2]:3d})  {c['total']:>9d} px  {c['pct_walk']:5.1f}% walk  floors: {c['floors']}")

print(f"\n  AMBIGUOUS colors (20-80% walkable):")
for c in sorted(ambiguous_colors, key=lambda x: x["total"], reverse=True):
    if c["total"] >= 50:
        print(f"    BGR ({c['bgr'][0]:3d},{c['bgr'][1]:3d},{c['bgr'][2]:3d})  RGB ({c['rgb'][0]:3d},{c['rgb'][1]:3d},{c['rgb'][2]:3d})  {c['total']:>9d} px  {c['pct_walk']:5.1f}% walk  floors: {c['floors']}")

# ── Current palette check ────────────────────────────────────────────────────

CURRENT_PALETTE = [
    ((153, 153, 153), True,  "grey_floor"),
    ((51,  102, 153), True,  "dirt/sand"),
    ((102, 255, 153), True,  "light_grass"),
    ((204, 204, 204), True,  "lighter_grey"),
    ((0,   153, 102), True,  "swamp"),
    ((0,   51,  255), False, "wall/building"),
    ((0,   204, 0),   False, "tree"),
    ((0,   102, 0),   False, "mountain"),
    ((153, 102, 51),  False, "water"),
    ((0,   255, 255), False, "yellow"),
    ((0,   0,   0),   False, "unexplored"),
    ((255, 255, 255), False, "white"),
]

print(f"\n{'='*70}")
print(f"CURRENT PALETTE COVERAGE CHECK")
print(f"{'='*70}")

total_pixels_all = sum(s["total"] for s in color_stats.values())
covered = 0
uncovered_significant = []

for (b, g, r), stats in sorted_colors:
    total = stats["total"]
    # Check if covered by any palette entry within tolerance 30
    matched = False
    for (pb, pg, pr), walk, name in CURRENT_PALETTE:
        if max(abs(b - pb), abs(g - pg), abs(r - pr)) <= 30:
            matched = True
            break
    if matched:
        covered += total
    else:
        if total >= 50:
            uncovered_significant.append({
                "bgr": (b, g, r),
                "rgb": (r, g, b),
                "total": total,
                "walkable": stats["walkable"],
                "non_walkable": stats["non_walkable"],
                "floors": sorted(stats["floors"]),
            })

print(f"\n  Total pixels (non-black): {total_pixels_all:>12d}")
print(f"  Covered by palette:       {covered:>12d} ({covered/total_pixels_all*100:.1f}%)")
print(f"  UNCOVERED:                {total_pixels_all - covered:>12d} ({(total_pixels_all-covered)/total_pixels_all*100:.1f}%)")

if uncovered_significant:
    print(f"\n  MISSING COLORS (>50 pixels, not within tolerance 30 of any palette entry):")
    for c in sorted(uncovered_significant, key=lambda x: x["total"], reverse=True):
        w = c["walkable"]
        nw = c["non_walkable"]
        if w + nw > 0:
            pct = f"{w/(w+nw)*100:.1f}%"
        else:
            pct = "N/A"
        print(f"    BGR ({c['bgr'][0]:3d},{c['bgr'][1]:3d},{c['bgr'][2]:3d})  RGB ({c['rgb'][0]:3d},{c['rgb'][1]:3d},{c['rgb'][2]:3d})  {c['total']:>8d} px  {pct:>6s} walk  floors: {c['floors']}")

# ── Save complete analysis to JSON ───────────────────────────────────────────

output = {
    "total_unique_colors": len(color_stats),
    "total_pixels": total_pixels_all,
    "palette_coverage_pct": round(covered / total_pixels_all * 100, 2),
    "walkable_colors": sorted(walkable_colors, key=lambda x: x["total"], reverse=True),
    "non_walkable_colors": sorted(non_walkable_colors, key=lambda x: x["total"], reverse=True),
    "ambiguous_colors": sorted(ambiguous_colors, key=lambda x: x["total"], reverse=True),
    "uncovered_significant": [
        {"bgr": list(c["bgr"]), "rgb": list(c["rgb"]), "total": c["total"],
         "walkable": c["walkable"], "non_walkable": c["non_walkable"], "floors": c["floors"]}
        for c in uncovered_significant
    ],
}

os.makedirs("output", exist_ok=True)
with open("output/full_palette_analysis.json", "w") as f:
    json.dump(output, f, indent=2)

print(f"\n  Full analysis saved to output/full_palette_analysis.json")
print("\nDONE")
