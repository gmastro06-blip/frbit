"""Capture a live minimap frame and verify palette classification."""
import cv2
import numpy as np
import ctypes
import ctypes.wintypes as wt
from collections import Counter
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Capture via PrintWindow ──────────────────────────────────────────────────

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

def find_hwnd(title):
    result = []
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, buf, 256)
        if title.lower() in buf.value.lower() and user32.IsWindowVisible(h):
            result.append(h)
        return True
    user32.EnumWindows(cb, 0)
    return result[0] if result else None

hwnd = find_hwnd("Proyector")
if not hwnd:
    print("ERROR: ventana Proyector no encontrada")
    sys.exit(1)

r = wt.RECT()
user32.GetClientRect(hwnd, ctypes.byref(r))
w, h = r.right, r.bottom
print(f"Frame: {w}x{h}")

hdcWin = user32.GetDC(hwnd)
hdcMem = gdi32.CreateCompatibleDC(hdcWin)
hbm = gdi32.CreateCompatibleBitmap(hdcWin, w, h)
gdi32.SelectObject(hdcMem, hbm)
user32.PrintWindow(hwnd, hdcMem, 2)

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biComp", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPPM", ctypes.c_int32),
        ("biYPPM", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImp", ctypes.c_uint32),
    ]

bi = BITMAPINFOHEADER()
bi.biSize = ctypes.sizeof(bi)
bi.biWidth = w
bi.biHeight = -h
bi.biPlanes = 1
bi.biBitCount = 32
buf = (ctypes.c_char * (w * h * 4))()
gdi32.GetDIBits(hdcMem, hbm, 0, h, buf, ctypes.byref(bi), 0)
img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()

gdi32.DeleteObject(hbm)
gdi32.DeleteDC(hdcMem)
user32.ReleaseDC(hwnd, hdcWin)

# ── Crop minimap ─────────────────────────────────────────────────────────────

roi = [1753, 30, 107, 109]
REF_W, REF_H = 1920, 1080
sx, sy = w / REF_W, h / REF_H
x0 = max(0, int(roi[0] * sx))
y0 = max(0, int(roi[1] * sy))
x1 = min(w, int((roi[0] + roi[2]) * sx))
y1 = min(h, int((roi[1] + roi[3]) * sy))
crop = img[y0:y1, x0:x1]
print(f"Minimap crop: {crop.shape} (roi {x0},{y0} -> {x1},{y1})")

# ── Downsample to 1px/tile ──────────────────────────────────────────────────

tiles_w = 107
tiles_h = max(1, int(crop.shape[0] * tiles_w / crop.shape[1]))
tile_img = cv2.resize(crop, (tiles_w, tiles_h), interpolation=cv2.INTER_AREA)
print(f"Tile grid: {tiles_w}x{tiles_h}")

flat = tile_img.reshape(-1, 3)

# ── Top colors ───────────────────────────────────────────────────────────────

quantized = [(int(b) // 5 * 5, int(g) // 5 * 5, int(r) // 5 * 5) for b, g, r in flat]
counts = Counter(quantized)
print(f"\n=== TOP 15 COLORES BGR (cuantizados x5) ===")
for bgr, cnt in counts.most_common(15):
    pct = cnt / len(flat) * 100
    rgb = (bgr[2], bgr[1], bgr[0])
    print(f"  BGR {str(bgr):>18s}  RGB {str(rgb):>18s}  {cnt:>5d} tiles ({pct:5.1f}%)")

# ── Classify with NEW palette ────────────────────────────────────────────────

PALETTE_NEW = [
    ((153, 153, 153), True,  "grey_floor"),
    ((51, 102, 153),  True,  "dirt/sand"),
    ((102, 255, 153), True,  "light_grass"),
    ((204, 204, 204), True,  "lighter_grey"),
    ((0, 153, 102),   True,  "swamp"),
    ((0, 51, 255),    False, "wall/building"),
    ((0, 204, 0),     False, "tree"),
    ((0, 102, 0),     False, "mountain"),
    ((153, 102, 51),  False, "water"),
    ((0, 255, 255),   False, "yellow"),
    ((0, 0, 0),       False, "unexplored"),
    ((255, 255, 255), False, "white"),
]

pal_arr = np.array([c for c, _, _ in PALETTE_NEW], dtype=np.int16)
pal_walk = np.array([w for _, w, _ in PALETTE_NEW])
pal_name = [n for _, _, n in PALETTE_NEW]

diffs = np.abs(flat.astype(np.int16)[:, np.newaxis, :] - pal_arr[np.newaxis, :, :])
cheby = diffs.max(axis=2)
best_idx = cheby.argmin(axis=1)
best_dist = cheby[np.arange(len(flat)), best_idx]

walkable_mask = np.where(best_dist <= 30, pal_walk[best_idx], False)
n_walk = int(walkable_mask.sum())
n_total = len(flat)

print(f"\n=== CLASIFICACION NUEVA (CORREGIDA) ===")
print(f"  Walkable:     {n_walk:>5d} ({n_walk / n_total * 100:.1f}%)")
print(f"  Non-walkable: {n_total - n_walk:>5d} ({(n_total - n_walk) / n_total * 100:.1f}%)")
for i, name in enumerate(pal_name):
    matched = int(((best_idx == i) & (best_dist <= 30)).sum())
    if matched > 0:
        flag = "WALK" if pal_walk[i] else "BLOCK"
        print(f"    {name:>15s}: {matched:>5d} tiles  [{flag}]")
no_match = int((best_dist > 30).sum())
if no_match:
    print(f"    {'(no match)':>15s}: {no_match:>5d} tiles  [BLOCK]")

# ── Classify with OLD (buggy) palette ────────────────────────────────────────

PALETTE_OLD = [
    ((153, 153, 153), True,  "grey_floor"),
    ((153, 102, 51),  True,  "dirt/sand(BUG)"),
    ((102, 255, 153), True,  "light_grass"),
    ((204, 204, 204), True,  "lighter_grey"),
    ((0, 51, 255),    False, "wall/building"),
    ((0, 204, 0),     False, "tree"),
    ((0, 102, 0),     False, "mountain"),
    ((51, 102, 153),  False, "water(BUG)"),
    ((0, 255, 255),   False, "yellow"),
    ((0, 0, 0),       False, "unexplored"),
    ((255, 255, 255), False, "white"),
]

pal_old = np.array([c for c, _, _ in PALETTE_OLD], dtype=np.int16)
pw_old = np.array([w for _, w, _ in PALETTE_OLD])
pn_old = [n for _, _, n in PALETTE_OLD]

d_old = np.abs(flat.astype(np.int16)[:, np.newaxis, :] - pal_old[np.newaxis, :, :])
c_old = d_old.max(axis=2)
bi_old = c_old.argmin(axis=1)
bd_old = c_old[np.arange(len(flat)), bi_old]
walk_old = np.where(bd_old <= 30, pw_old[bi_old], False)
nw_old = int(walk_old.sum())

print(f"\n=== CLASIFICACION VIEJA (BUGGY) ===")
print(f"  Walkable:     {nw_old:>5d} ({nw_old / n_total * 100:.1f}%)")
print(f"  Non-walkable: {n_total - nw_old:>5d} ({(n_total - nw_old) / n_total * 100:.1f}%)")
for i, name in enumerate(pn_old):
    matched = int(((bi_old == i) & (bd_old <= 30)).sum())
    if matched > 0:
        flag = "WALK" if pw_old[i] else "BLOCK"
        print(f"    {name:>15s}: {matched:>5d} tiles  [{flag}]")

# ── Diff ─────────────────────────────────────────────────────────────────────

changed = int((walkable_mask != walk_old).sum())
print(f"\n=== DIFERENCIA ===")
print(f"  Tiles que cambian de clasificacion: {changed}")
print(f"  Paleta VIEJA bloqueaba: {n_total - nw_old} tiles")
print(f"  Paleta NUEVA bloquea:   {n_total - n_walk} tiles")
print(f"  Falsos positivos eliminados: {(n_total - nw_old) - (n_total - n_walk)}")

# ── Compare with static walkability ──────────────────────────────────────────

from src.minimap_radar import MinimapRadar
from src.map_loader import TibiaMapLoader

loader = TibiaMapLoader()
static_walk = loader.get_walkability(7)
from src.models import BOUNDS
xmin, ymin = BOUNDS["xMin"], BOUNDS["yMin"]

# Read position from radar
radar = MinimapRadar(loader)
pos = radar.read(img)
if pos:
    print(f"\n=== COMPARACION CON MAPA ESTATICO ===")
    print(f"  Posicion radar: ({pos[0]}, {pos[1]})")
    cx, cy = pos[0], pos[1]
    half_w = tiles_w // 2
    half_h = tiles_h // 2
    walk_grid = walkable_mask.reshape(tiles_h, tiles_w)
    agree = 0
    disagree_new_walk = 0
    disagree_new_block = 0
    total_compared = 0
    for ty in range(tiles_h):
        for tx in range(tiles_w):
            tile_x = cx - half_w + tx
            tile_y = cy - half_h + ty
            px = tile_x - xmin
            py = tile_y - ymin
            if 0 <= px < static_walk.shape[1] and 0 <= py < static_walk.shape[0]:
                sw = bool(static_walk[py, px])
                lw = bool(walk_grid[ty, tx])
                total_compared += 1
                if sw == lw:
                    agree += 1
                elif lw and not sw:
                    disagree_new_walk += 1
                else:
                    disagree_new_block += 1
    print(f"  Tiles comparados:    {total_compared}")
    print(f"  Coinciden:           {agree} ({agree/total_compared*100:.1f}%)")
    print(f"  Live=walk,Map=block: {disagree_new_walk} (posible puerta abierta)")
    print(f"  Live=block,Map=walk: {disagree_new_block} (obstaculos reales)")
    
    # Save comparison images
    os.makedirs("output", exist_ok=True)
    viz = np.zeros((tiles_h, tiles_w, 3), dtype=np.uint8)
    viz[walk_grid] = (0, 200, 0)      # green = walkable
    viz[~walk_grid] = (0, 0, 200)     # red = blocked
    viz_big = cv2.resize(viz, (tiles_w * 4, tiles_h * 4), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite("output/minimap_walkability_new.png", viz_big)
    cv2.imwrite("output/minimap_crop_raw.png", crop)
    cv2.imwrite("output/minimap_tiles.png", cv2.resize(tile_img, (tiles_w*4, tiles_h*4), interpolation=cv2.INTER_NEAREST))
    print(f"\n  Imagenes guardadas en output/")
else:
    print("\n  WARN: no se pudo leer posicion del radar")
    os.makedirs("output", exist_ok=True)
    walk_grid = walkable_mask.reshape(tiles_h, tiles_w)
    viz = np.zeros((tiles_h, tiles_w, 3), dtype=np.uint8)
    viz[walk_grid] = (0, 200, 0)
    viz[~walk_grid] = (0, 0, 200)
    viz_big = cv2.resize(viz, (tiles_w * 4, tiles_h * 4), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite("output/minimap_walkability_new.png", viz_big)
    cv2.imwrite("output/minimap_crop_raw.png", crop)
    print(f"\n  Imagenes guardadas en output/ (sin comparar con mapa, sin posicion)")

print("\nDONE")
