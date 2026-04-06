"""
Debug radar: save minimap crop and check what's happening.
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from src.frame_capture import build_frame_getter
from src.input_controller import find_window
from src.minimap_radar import MinimapRadar, MinimapConfig
from src.map_loader import TibiaMapLoader

info = find_window("Proyector")
if not info:
    print("Window not found"); exit(1)

getter = build_frame_getter("wgc", hwnd=info.hwnd)
time.sleep(0.8)
frame = None
for _ in range(15):
    frame = getter()
    if frame is not None: break
    time.sleep(0.2)
if frame is None:
    print("No frame"); exit(1)

fh, fw = frame.shape[:2]
print(f"Frame: {fw}x{fh}")

cfg = MinimapConfig.load()
print(f"Config ROI: {cfg.roi}")
print(f"Config confidence: {cfg.confidence}")

# Save the frame and minimap crop
out = Path("output/radar_debug")
out.mkdir(parents=True, exist_ok=True)
cv2.imwrite(str(out / "full_frame.png"), frame)

# Crop minimap manually using the same logic as MinimapRadar
rx, ry, rw, rh = cfg.roi
_REF_W, _REF_H = 1920, 1080
sx = fw / _REF_W
sy = fh / _REF_H
x0 = max(0, int(rx * sx))
y0 = max(0, int(ry * sy))
x1 = min(fw, int((rx + rw) * sx))
y1 = min(fh, int((ry + rh) * sy))
print(f"Scaled ROI: ({x0},{y0})-({x1},{y1})")

crop = frame[y0:y1, x0:x1]
print(f"Crop size: {crop.shape[1]}x{crop.shape[0]}")
cv2.imwrite(str(out / "minimap_crop.png"), crop)

# Check if the crop is mostly black/empty (game might not be showing map)
gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
mean_gray = gray.mean()
nonzero = np.count_nonzero(gray > 10)
total = gray.size
print(f"Crop mean gray: {mean_gray:.1f}")
print(f"Non-black pixels: {nonzero}/{total} ({nonzero*100/total:.1f}%)")

# Check for common minimap colors
b, g, r = cv2.split(crop)
green_px = np.count_nonzero((g > 100) & (r < 80) & (b < 80))
blue_px = np.count_nonzero((b > 100) & (r > 30) & (g < 80))
print(f"Green terrain pixels: {green_px}")
print(f"Blue/orange marker pixels: {blue_px}")

# Try radar at lower confidence
loader = TibiaMapLoader()
for floor in [7]:
    loader.preload_floor(floor)
    
    for conf in [0.45, 0.30, 0.20, 0.10]:
        radar = MinimapRadar(loader=loader)
        radar._cfg.confidence = conf
        
        coord = radar.read(frame)
        if coord:
            print(f"  Floor {floor} conf={conf}: {coord}")
            break
        else:
            print(f"  Floor {floor} conf={conf}: None")

# Save zoomed crop
crop_big = cv2.resize(crop, (crop.shape[1]*4, crop.shape[0]*4), interpolation=cv2.INTER_NEAREST)
cv2.imwrite(str(out / "minimap_crop_4x.png"), crop_big)

print(f"\nDebug images in {out}/")
getter.close()
