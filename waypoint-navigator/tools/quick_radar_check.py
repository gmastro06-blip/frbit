"""
Quick radar check — mirrors the working test_radar_live.py approach.
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.frame_capture import build_frame_getter
from src.input_controller import find_window
from src.minimap_radar import MinimapRadar, MinimapConfig
from src.map_loader import TibiaMapLoader
from src.models import Coordinate

info = find_window("Proyector")
if not info:
    print("Window not found"); exit(1)
print(f"Window: hwnd={info.hwnd:#x}")

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

# Use the same approach as test_radar_live.py
loader = TibiaMapLoader()

# Try multiple floors
for floor in [7, 6, 8]:
    loader.preload_floor(floor)
    radar = MinimapRadar(loader=loader)
    
    frame = getter()
    if frame is None:
        continue
    
    t0 = time.perf_counter()
    coord = radar.read(frame)  # No hint — let it search
    dt = (time.perf_counter() - t0) * 1000
    
    if coord:
        print(f"  Floor {floor}: {coord}  [{dt:.0f}ms]")
    else:
        print(f"  Floor {floor}: None  [{dt:.0f}ms]")

# Try with hint
print("\nWith hint:")
radar2 = MinimapRadar(loader=loader)
for i in range(5):
    frame = getter()
    if frame is None:
        print(f"  Read {i+1}: no frame"); continue
    t0 = time.perf_counter()
    coord = radar2.read(frame)
    dt = (time.perf_counter() - t0) * 1000
    print(f"  Read {i+1}: {coord}  [{dt:.0f}ms]")
    time.sleep(0.3)

getter.close()
