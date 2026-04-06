"""Analyze the grey border around the minimap to find exact map bounds."""
import cv2
import numpy as np
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.frame_capture import build_frame_getter
from src.input_controller import find_window

info = find_window("Proyector")
getter = build_frame_getter("wgc", hwnd=info.hwnd)
time.sleep(0.8)
frame = None
for _ in range(15):
    frame = getter()
    if frame is not None:
        break
    time.sleep(0.2)
getter.close()

fh, fw = frame.shape[:2]
sx, sy = fw / 1920, fh / 1080
print(f"Frame: {fw}x{fh}")

# Take a wider area to capture the full grey border
x0 = int(1680 * sx)
y0 = int(10 * sy)
x1 = int(1915 * sx)
y1 = int(240 * sy)
big = frame[y0:y1, x0:x1]
bh, bw = big.shape[:2]
print(f"Big crop: {bw}x{bh}  (from frame [{x0}:{x1}, {y0}:{y1}])")

cv2.imwrite("output/radar_test/border_big.png", big)

gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
b_ch, g_ch, r_ch = cv2.split(big)

# Grey border: all channels similar, brightness 55-100
diffs = np.stack([
    np.abs(b_ch.astype(int) - g_ch.astype(int)),
    np.abs(g_ch.astype(int) - r_ch.astype(int)),
    np.abs(b_ch.astype(int) - r_ch.astype(int)),
])
sat = np.max(diffs, axis=0)

is_border_grey = (sat < 20) & (gray >= 50) & (gray <= 100)

cv2.imwrite("output/radar_test/border_grey_mask.png",
            is_border_grey.astype(np.uint8) * 255)

# Find border rows/cols
row_frac = np.array([is_border_grey[r].mean() for r in range(bh)])
col_frac = np.array([is_border_grey[:, c].mean() for c in range(bw)])

# Border rows: >30% grey pixels
border_rows = np.where(row_frac > 0.25)[0]
border_cols = np.where(col_frac > 0.25)[0]

if len(border_rows) > 0:
    print(f"\nBorder rows: {border_rows[0]}..{border_rows[-1]}")
if len(border_cols) > 0:
    print(f"Border cols: {border_cols[0]}..{border_cols[-1]}")

# Find inner edges (where map content starts)
# Top: scan down, find first border row, then first non-border row after it
def find_inner_edge_forward(fracs, threshold_in=0.25, threshold_out=0.10):
    in_border = False
    for i, f in enumerate(fracs):
        if f > threshold_in:
            in_border = True
        elif in_border and f < threshold_out:
            return i
    return 0

def find_inner_edge_backward(fracs, threshold_in=0.25, threshold_out=0.10):
    in_border = False
    for i in range(len(fracs) - 1, -1, -1):
        if fracs[i] > threshold_in:
            in_border = True
        elif in_border and fracs[i] < threshold_out:
            return i
    return len(fracs) - 1

map_top = find_inner_edge_forward(row_frac)
map_bot = find_inner_edge_backward(row_frac)
map_left = find_inner_edge_forward(col_frac)
map_right = find_inner_edge_backward(col_frac)

print(f"\nMap area in big crop:")
print(f"  Top:    row {map_top}")
print(f"  Bottom: row {map_bot}")
print(f"  Left:   col {map_left}")
print(f"  Right:  col {map_right}")
print(f"  Size:   {map_right - map_left + 1} x {map_bot - map_top + 1}")

# Convert to frame coordinates
map_fx0 = x0 + map_left
map_fy0 = y0 + map_top
map_fx1 = x0 + map_right
map_fy1 = y0 + map_bot
print(f"\nMap area in frame pixels:")
print(f"  ({map_fx0}, {map_fy0}) to ({map_fx1}, {map_fy1})")
print(f"  Size: {map_fx1 - map_fx0 + 1} x {map_fy1 - map_fy0 + 1}")

# Convert to 1920x1080 reference coords
ref_x = int(map_fx0 / sx)
ref_y = int(map_fy0 / sy)
ref_w = int((map_fx1 - map_fx0 + 1) / sx)
ref_h = int((map_fy1 - map_fy0 + 1) / sy)
print(f"\nOptimal ROI (1920x1080 ref):")
print(f"  [{ref_x}, {ref_y}, {ref_w}, {ref_h}]")

# Find character cross in the map area
map_crop = big[map_top:map_bot + 1, map_left:map_right + 1]
cv2.imwrite("output/radar_test/border_map_only.png", map_crop)

# Detect white cross
mgray = cv2.cvtColor(map_crop, cv2.COLOR_BGR2GRAY)
mh, mw = map_crop.shape[:2]
bright = mgray > 220
ys, xs = np.where(bright)
if len(ys) >= 2:
    char_y = int(np.median(ys))
    char_x = int(np.median(xs))
    print(f"\nCharacter cross in map crop: ({char_x}, {char_y})")
    print(f"Map crop center: ({mw // 2}, {mh // 2})")
    print(f"Offset from center: dx={char_x - mw // 2}, dy={char_y - mh // 2}")
    print(f"Character fraction: ({char_x / mw:.3f}, {char_y / mh:.3f})")

# Draw annotated version
ann = big.copy()
# Draw border detection (blue rectangle)
cv2.rectangle(ann, (map_left, map_top), (map_right, map_bot), (255, 0, 0), 2)
# Draw character position (green circle)
if len(ys) >= 2:
    cv2.circle(ann, (map_left + char_x, map_top + char_y), 8, (0, 255, 0), 2)
    cv2.putText(ann, "player", (map_left + char_x + 10, map_top + char_y - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
# Draw center (red circle)
cv2.circle(ann, (map_left + mw // 2, map_top + mh // 2), 8, (0, 0, 255), 2)
cv2.putText(ann, "center", (map_left + mw // 2 + 10, map_top + mh // 2 - 5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

# Scale up 3x for visibility
ann3x = cv2.resize(ann, (bw * 3, bh * 3), interpolation=cv2.INTER_NEAREST)
cv2.imwrite("output/radar_test/border_annotated_3x.png", ann3x)

# Also save the map-only crop scaled up
map4x = cv2.resize(map_crop, (mw * 4, mh * 4), interpolation=cv2.INTER_NEAREST)
cv2.imwrite("output/radar_test/border_map_4x.png", map4x)

print("\nAll images saved to output/radar_test/")
