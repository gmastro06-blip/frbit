"""
Analyze the HP/MP bar pixel data from a captured frame to find exact bar boundaries.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.frame_capture import build_frame_getter
from src.input_controller import find_window

import cv2
import numpy as np


def main() -> None:
    info = find_window("Proyector")
    if not info:
        print("Window not found"); return

    getter = build_frame_getter("wgc", hwnd=info.hwnd)
    time.sleep(0.8)
    frame = None
    for _ in range(15):
        frame = getter()
        if frame is not None:
            break
        time.sleep(0.2)
    if frame is None:
        print("No frame"); return

    fh, fw = frame.shape[:2]
    print(f"Frame: {fw}×{fh}")

    # ── Scan the top area (y=20..70) for HP/MP-colored horizontal runs ──
    # We'll look in native pixel coords (no scaling) and scan for:
    #   HP: saturation>=30, max_channel>=80, max(R,G)>=B
    #   MP: B>=100, B-R>=40, B-G>=20

    # Scan a generous vertical band
    y_start, y_end = 20, 70
    region = frame[y_start:y_end, :, :]  # full width, y=20..70

    b = region[:, :, 0].astype(np.int32)
    g = region[:, :, 1].astype(np.int32)
    r = region[:, :, 2].astype(np.int32)

    max_rg = np.maximum(r, g)
    max_ch = np.maximum(max_rg, b)
    min_ch = np.minimum(np.minimum(r, g), b)
    sat = max_ch - min_ch

    hp_mask = (sat >= 30) & (max_ch >= 80) & (max_rg >= b)
    mp_mask = (b >= 100) & ((b - r) >= 40) & ((b - g) >= 20)

    print(f"\n=== HP-colored pixels (y={y_start}..{y_end}) ===")
    hp_rows = np.where(hp_mask.any(axis=1))[0]
    if len(hp_rows):
        print(f"  Rows with HP pixels: y={hp_rows[0]+y_start}..{hp_rows[-1]+y_start}")
        # For each row with HP pixels, find x range
        for row_idx in hp_rows:
            cols = np.where(hp_mask[row_idx])[0]
            print(f"    y={row_idx+y_start}: x={cols[0]}..{cols[-1]} ({len(cols)} px)")
    else:
        print("  No HP-colored pixels found in this region!")

    print(f"\n=== MP-colored pixels (y={y_start}..{y_end}) ===")
    mp_rows = np.where(mp_mask.any(axis=1))[0]
    if len(mp_rows):
        print(f"  Rows with MP pixels: y={mp_rows[0]+y_start}..{mp_rows[-1]+y_start}")
        for row_idx in mp_rows:
            cols = np.where(mp_mask[row_idx])[0]
            print(f"    y={row_idx+y_start}: x={cols[0]}..{cols[-1]} ({len(cols)} px)")
    else:
        print("  No MP-colored pixels found in this region!")

    # ── Also dump a few pixel values around the current ROIs for context ──
    # Current config ROIs (in 1920×1080 ref, scaled)
    sx = fw / 1920
    sy = fh / 1080
    
    # HP ROI from config: [12, 38, 769, 13]
    hp_x0 = int(12 * sx)
    hp_y0 = int(38 * sy)
    hp_x1 = int((12+769) * sx)
    hp_y1 = int((38+13) * sy)
    
    # MP ROI from config: [794, 38, 990, 13] 
    mp_x0 = int(794 * sx)
    mp_y0 = int(38 * sy)
    mp_x1 = int((794+990) * sx)
    mp_y1 = int((38+13) * sy)
    
    print(f"\n=== Current ROIs (scaled to {fw}×{fh}) ===")
    print(f"  HP: x={hp_x0}..{hp_x1}, y={hp_y0}..{hp_y1}")
    print(f"  MP: x={mp_x0}..{mp_x1}, y={mp_y0}..{mp_y1}")
    
    # Sample pixel colors at the edges of each ROI
    print(f"\n=== HP bar edge pixels ===")
    mid_y = (hp_y0 + hp_y1) // 2
    for x in range(max(0, hp_x0-3), min(fw, hp_x1+5)):
        px = frame[mid_y, x]
        if x == hp_x0 or x == hp_x1-1 or x == hp_x1:
            marker = " <<<"
        else:
            marker = ""
        if x >= hp_x0 - 3 and x < hp_x0 + 3:
            print(f"    x={x}: BGR=({px[0]:3d},{px[1]:3d},{px[2]:3d}){marker}")
        elif x >= hp_x1 - 3 and x <= hp_x1 + 3:
            print(f"    x={x}: BGR=({px[0]:3d},{px[1]:3d},{px[2]:3d}){marker}")

    print(f"\n=== MP bar edge pixels ===")
    mid_y = (mp_y0 + mp_y1) // 2
    for x in range(max(0, mp_x0-3), min(fw, mp_x1+5)):
        px = frame[mid_y, x]
        if x == mp_x0 or x == mp_x1-1 or x == mp_x1:
            marker = " <<<"
        else:
            marker = ""
        if x >= mp_x0 - 3 and x < mp_x0 + 3:
            print(f"    x={x}: BGR=({px[0]:3d},{px[1]:3d},{px[2]:3d}){marker}")
        elif x >= mp_x1 - 3 and x <= mp_x1 + 3:
            print(f"    x={x}: BGR=({px[0]:3d},{px[1]:3d},{px[2]:3d}){marker}")

    # ── Save a wide crop of the bar area for visual inspection ──
    out = Path("output") / "hpmp_test"
    out.mkdir(parents=True, exist_ok=True)
    
    # Save y=20..70, full width, scaled 4x
    bar_area = frame[y_start:y_end, :, :].copy()
    # Draw current ROI rectangles
    cv2.rectangle(bar_area, (hp_x0, hp_y0-y_start), (hp_x1, hp_y1-y_start), (0,0,255), 1)
    cv2.rectangle(bar_area, (mp_x0, mp_y0-y_start), (mp_x1, mp_y1-y_start), (255,0,0), 1)
    bar_big = cv2.resize(bar_area, (bar_area.shape[1]*2, bar_area.shape[0]*4),
                         interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out / "bar_area_annotated.png"), bar_big)
    print(f"\n  Saved bar_area_annotated.png ({bar_big.shape[1]}×{bar_big.shape[0]})")

    getter.close()


if __name__ == "__main__":
    main()
