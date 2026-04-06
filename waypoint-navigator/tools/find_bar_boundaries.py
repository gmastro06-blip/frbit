"""
Find the exact contiguous HP/MP bar boundaries in a captured frame.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.frame_capture import build_frame_getter
from src.input_controller import find_window

import cv2
import numpy as np


def longest_run(mask_row):
    """Find start, end, length of the longest contiguous True run in a 1D bool array."""
    if not mask_row.any():
        return None, None, 0
    changes = np.diff(mask_row.astype(np.int8))
    starts = np.where(changes == 1)[0] + 1
    ends = np.where(changes == -1)[0] + 1
    if mask_row[0]:
        starts = np.concatenate([[0], starts])
    if mask_row[-1]:
        ends = np.concatenate([ends, [len(mask_row)]])
    lengths = ends - starts
    idx = np.argmax(lengths)
    return int(starts[idx]), int(ends[idx]), int(lengths[idx])


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
    sx = fw / 1920
    sy = fh / 1080

    # Scan y=25..55
    y_start, y_end = 25, 55
    region = frame[y_start:y_end, :, :]

    b = region[:, :, 0].astype(np.int32)
    g = region[:, :, 1].astype(np.int32)
    r = region[:, :, 2].astype(np.int32)

    max_rg = np.maximum(r, g)
    max_ch = np.maximum(max_rg, b)
    min_ch = np.minimum(np.minimum(r, g), b)
    sat = max_ch - min_ch

    hp_mask = (sat >= 30) & (max_ch >= 80) & (max_rg >= b)
    mp_mask = (b >= 100) & ((b - r) >= 40) & ((b - g) >= 20)

    print("\n=== HP bar: longest contiguous colored run per row ===")
    hp_best_len = 0
    hp_best_row = 0
    hp_best_x0 = 0
    hp_best_x1 = 0
    for ri in range(hp_mask.shape[0]):
        y = ri + y_start
        x0, x1, ln = longest_run(hp_mask[ri])
        if ln > 100:  # only meaningful runs
            print(f"  y={y}: x={x0}..{x1-1} (len={ln})")
            if ln > hp_best_len:
                hp_best_len = ln
                hp_best_row = y
                hp_best_x0 = x0
                hp_best_x1 = x1

    print(f"\n  → Best HP run: y={hp_best_row}, x={hp_best_x0}..{hp_best_x1-1}, "
          f"len={hp_best_len}")

    print("\n=== MP bar: longest contiguous colored run per row ===")
    mp_best_len = 0
    mp_best_row = 0
    mp_best_x0 = 0
    mp_best_x1 = 0
    for ri in range(mp_mask.shape[0]):
        y = ri + y_start
        x0, x1, ln = longest_run(mp_mask[ri])
        if ln > 100:
            print(f"  y={y}: x={x0}..{x1-1} (len={ln})")
            if ln > mp_best_len:
                mp_best_len = ln
                mp_best_row = y
                mp_best_x0 = x0
                mp_best_x1 = x1

    print(f"\n  → Best MP run: y={mp_best_row}, x={mp_best_x0}..{mp_best_x1-1}, "
          f"len={mp_best_len}")

    # Find vertical extent of each bar (rows where the longest run is at least
    # 90% of the best run length, and overlapping x range)
    hp_rows = []
    for ri in range(hp_mask.shape[0]):
        y = ri + y_start
        # Check if this row has a run overlapping the best x range
        row_mask = hp_mask[ri, hp_best_x0:hp_best_x1]
        coverage = row_mask.sum() / hp_best_len
        if coverage > 0.9:
            hp_rows.append(y)

    mp_rows = []
    for ri in range(mp_mask.shape[0]):
        y = ri + y_start
        row_mask = mp_mask[ri, mp_best_x0:mp_best_x1]
        coverage = row_mask.sum() / mp_best_len
        if coverage > 0.9:
            mp_rows.append(y)

    if hp_rows:
        print(f"\n  HP bar vertical extent: y={hp_rows[0]}..{hp_rows[-1]} "
              f"({len(hp_rows)} rows)")
    if mp_rows:
        print(f"  MP bar vertical extent: y={mp_rows[0]}..{mp_rows[-1]} "
              f"({len(mp_rows)} rows)")

    # Convert to reference coordinates (1920×1080)
    if hp_rows:
        ref_hp_x = round(hp_best_x0 / sx)
        ref_hp_y = round(hp_rows[0] / sy)
        ref_hp_w = round(hp_best_len / sx)
        ref_hp_h = round((hp_rows[-1] - hp_rows[0] + 1) / sy)
        print(f"\n  Suggested hp_roi (1920×1080 ref): [{ref_hp_x}, {ref_hp_y}, "
              f"{ref_hp_w}, {ref_hp_h}]")

    if mp_rows:
        ref_mp_x = round(mp_best_x0 / sx)
        ref_mp_y = round(mp_rows[0] / sy)
        ref_mp_w = round(mp_best_len / sx)
        ref_mp_h = round((mp_rows[-1] - mp_rows[0] + 1) / sy)
        print(f"  Suggested mp_roi (1920×1080 ref): [{ref_mp_x}, {ref_mp_y}, "
              f"{ref_mp_w}, {ref_mp_h}]")

    # Save annotated image with both current and suggested ROIs
    out = Path("output") / "hpmp_test"
    out.mkdir(parents=True, exist_ok=True)

    vis = frame[20:55, :, :].copy()
    off = 20
    # Current ROIs (red)
    cv2.rectangle(vis, (int(12*sx), int(38*sy)-off), (int(781*sx), int(51*sy)-off), (0,0,255), 1)
    cv2.rectangle(vis, (int(794*sx), int(38*sy)-off), (int(1784*sx), int(51*sy)-off), (255,0,0), 1)
    # Suggested ROIs (green)
    if hp_rows:
        cv2.rectangle(vis, (hp_best_x0, hp_rows[0]-off), (hp_best_x1, hp_rows[-1]-off+1), (0,255,0), 1)
    if mp_rows:
        cv2.rectangle(vis, (mp_best_x0, mp_rows[0]-off), (mp_best_x1, mp_rows[-1]-off+1), (0,255,0), 1)
    vis_big = cv2.resize(vis, (vis.shape[1]*2, vis.shape[0]*6), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out / "bar_boundaries.png"), vis_big)
    print(f"\n  Saved bar_boundaries.png (red=current ROI, green=detected bar)")

    getter.close()


if __name__ == "__main__":
    main()
