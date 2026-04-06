"""
debug_walker.py — Verificación visual paso a paso del walker.

Muestra en tiempo real:
  - Frame capturado de Tibia (monitor 2)
  - Posición actual leída del minimapa
  - Waypoints de la ruta dibujados sobre el minimapa
  - Qué waypoint es el más cercano (paso actual estimado)
  - Distancia al próximo waypoint
  - Estado: en ruta / cerca del depot / en la calle / en el temple

Uso:
  python -X utf8 debug_walker.py            # foto instantánea → output/debug_walker.png
  python -X utf8 debug_walker.py --loop     # actualiza cada 2s, Ctrl+C para detener
  python -X utf8 debug_walker.py --loop 1   # actualiza cada 1s
"""

import sys
import json
import math
import time
from pathlib import Path

import cv2
import mss
import numpy as np

ROOT = Path(__file__).resolve().parent.parent  # waypoint-navigator/
OUT  = ROOT / "output"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

# ── Config ────────────────────────────────────────────────────────────────────
MONITOR_IDX = 2
ROUTE_FILE  = ROOT / "routes" / "thais_rat_hunt.json"

MM_CFG   = json.loads((ROOT / "minimap_config.json").read_text())
MM_ROI   = MM_CFG.get("roi", [1740, 27, 119, 110])   # x, y, w, h
TILES_W  = MM_CFG.get("tiles_wide", 120)
MM_X, MM_Y, MM_W, MM_H = MM_ROI

# Zoom del minimapa anotado (×4 para que se vea bien)
ZOOM = 4

# Zonas conocidas con nombre
ZONES = [
    ("TEMPLE",  32369, 32242, 7,  8),   # (nombre, cx, cy, z, radio_tiles)
    ("DEPOT",   32352, 32227, 7, 10),
    ("CALLE W", 32345, 32215, 7,  8),
    ("CALLE E", 32380, 32215, 7,  8),
    ("CALLE C", 32369, 32215, 7,  8),
]

# ── Colores BGR ───────────────────────────────────────────────────────────────
C_WAYPOINT  = (0, 200, 255)   # amarillo — waypoints normales
C_RAND_WP   = (0, 140, 255)   # naranja  — random_stand
C_PLAYER    = (0, 255, 0)     # verde    — posición actual
C_NEAREST   = (0, 0, 255)     # rojo     — waypoint más cercano
C_ZONE      = (200, 200, 200) # gris     — círculos de zonas
C_TEXT      = (255, 255, 255)
C_BG        = (30, 30, 30)

# ── Leer ruta ─────────────────────────────────────────────────────────────────
def load_waypoints(route_file: Path) -> list[tuple[int, int, int, str]]:
    """Devuelve lista de (x, y, z, kind) de la ruta."""
    data = json.loads(route_file.read_text())
    waypoints = []
    for step in data.get("script", []):
        kind = step.get("kind", "")
        if kind in ("stand", "node"):
            if "x" in step and "y" in step:
                waypoints.append((step["x"], step["y"], step.get("z", 7), "stand"))
        elif kind == "random_stand":
            for c in step.get("choices", []):
                waypoints.append((c["x"], c["y"], c.get("z", 7), "random_stand"))
    return waypoints

# ── Leer posición del minimapa ────────────────────────────────────────────────
def read_position(frame: np.ndarray) -> tuple[int, int, int] | None:
    """Lee posición actual usando MinimapRadar. Retorna (x, y, z) o None."""
    try:
        from src.minimap_radar import MinimapRadar, MinimapConfig
        from src.map_loader import TibiaMapLoader
        import dataclasses
        valid_fields = {f.name for f in dataclasses.fields(MinimapConfig)}
        cfg = MinimapConfig(**{k: v for k, v in MM_CFG.items() if k in valid_fields})
        loader = TibiaMapLoader()
        radar = MinimapRadar(loader, config=cfg)
        result = radar.read(frame, floor=cfg.floor)
        if result is not None:
            return result.x, result.y, result.z
    except Exception as e:
        print(f"[DBG] radar error: {e}")
    return None

# ── Tile → pixel en el minimapa anotado ───────────────────────────────────────
def tile_to_pixel(tx: int, ty: int, center_x: int, center_y: int, zoom: int = ZOOM) -> tuple[int, int]:
    """Convierte coordenada de tile a pixel en la imagen del minimapa (zoom ×4)."""
    px_per_tile = (MM_W / TILES_W) * zoom
    cx = (MM_W * zoom) // 2
    cy = (MM_H * zoom) // 2
    px = int(cx + (tx - center_x) * px_per_tile)
    py = int(cy + (ty - center_y) * px_per_tile)
    return px, py

# ── Distancia tiles ───────────────────────────────────────────────────────────
def dist(ax: int, ay: int, bx: int, by: int) -> float:
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)

# ── Zona actual ───────────────────────────────────────────────────────────────
def current_zone(px: int, py: int, pz: int) -> str:
    for name, cx, cy, z, r in ZONES:
        if pz == z and dist(px, py, cx, cy) <= r:
            return name
    return "EN RUTA"

# ── Snapshot principal ────────────────────────────────────────────────────────
def snapshot() -> Path | None:
    # 1. Captura
    with mss.mss() as sct:
        monitors = sct.monitors
        if MONITOR_IDX >= len(monitors):
            print(f"[ERR] Monitor {MONITOR_IDX} no disponible")
            return None
        img = sct.grab(monitors[MONITOR_IDX])
        frame = np.array(img)[:, :, :3].copy()

    print(f"[DBG] Frame: {frame.shape[1]}×{frame.shape[0]}")

    # 2. Leer posición
    pos = read_position(frame)
    if pos:
        px, py, pz = pos
        print(f"[POS] Posición actual: ({px}, {py}, z={pz})")
        zone = current_zone(px, py, pz)
        print(f"[POS] Zona: {zone}")
    else:
        px, py, pz = None, None, 7
        print("[POS] No se pudo leer posición del minimapa")
        zone = "DESCONOCIDA"

    # 3. Cargar waypoints
    waypoints = load_waypoints(ROUTE_FILE)

    # 4. Waypoint más cercano
    nearest_idx, nearest_dist = None, float("inf")
    if pos and px is not None and py is not None:
        for i, (wx, wy, wz, wk) in enumerate(waypoints):
            if wz == pz:
                d = dist(px, py, wx, wy)
                if d < nearest_dist:
                    nearest_dist, nearest_idx = d, i

    if nearest_idx is not None:
        nwx, nwy, nwz, nwk = waypoints[nearest_idx]
        print(f"[WP]  Waypoint más cercano: #{nearest_idx} ({nwx},{nwy}) — {nearest_dist:.1f} tiles")
        # Próximo waypoint
        next_idx = (nearest_idx + 1) % len(waypoints)
        nxt = waypoints[next_idx]
        print(f"[WP]  Próximo waypoint:     #{next_idx} ({nxt[0]},{nxt[1]})")

    # 5. Construir minimapa anotado
    crop = frame[MM_Y:MM_Y+MM_H, MM_X:MM_X+MM_W].copy()
    mm_big = cv2.resize(crop, (MM_W * ZOOM, MM_H * ZOOM), interpolation=cv2.INTER_NEAREST)

    center_x = px if px else 32369
    center_y = py if py else 32215

    # Dibujar zonas
    for name, zx, zy, zz, zr in ZONES:
        if zz == pz:
            pp = tile_to_pixel(zx, zy, center_x, center_y)
            r_px = int(zr * (MM_W / TILES_W) * ZOOM)
            cv2.circle(mm_big, pp, r_px, C_ZONE, 1)
            cv2.putText(mm_big, name, (pp[0] - 20, pp[1] - r_px - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, C_ZONE, 1)

    # Dibujar waypoints
    seen = set()
    for i, (wx, wy, wz, wk) in enumerate(waypoints):
        if wz != pz or (wx, wy) in seen:
            continue
        seen.add((wx, wy))
        pp = tile_to_pixel(wx, wy, center_x, center_y)
        color = C_RAND_WP if wk == "random_stand" else C_WAYPOINT
        if i == nearest_idx:
            color = C_NEAREST
            cv2.circle(mm_big, pp, 6, color, -1)
        else:
            cv2.circle(mm_big, pp, 3, color, -1)
        cv2.putText(mm_big, str(i), (pp[0] + 4, pp[1] - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, color, 1)

    # Dibujar posición actual del jugador
    if pos and px is not None and py is not None:
        pp_player = tile_to_pixel(px, py, center_x, center_y)
        cv2.circle(mm_big, pp_player, 5, C_PLAYER, -1)
        cv2.circle(mm_big, pp_player, 7, C_PLAYER, 1)

    # 6. Panel de info (a la derecha del minimapa)
    INFO_W = 280
    panel = np.full((MM_H * ZOOM, INFO_W, 3), C_BG, dtype=np.uint8)

    def put(text: str, row: int, color: tuple = C_TEXT, scale: float = 0.42) -> None:
        cv2.putText(panel, text, (6, 14 + row * 18),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1)

    put("=== WALKER STATUS ===", 0, (100, 220, 255), 0.45)
    if pos:
        put(f"POS:  ({px}, {py}, z={pz})", 2, C_PLAYER)
        put(f"ZONA: {zone}", 3,
            (0, 255, 0) if zone != "EN RUTA" and zone != "DESCONOCIDA" else C_TEXT)
    else:
        put("POS:  no detectada", 2, (0, 0, 220))

    if nearest_idx is not None:
        nwx, nwy = waypoints[nearest_idx][:2]
        put(f"WP cercano: #{nearest_idx} ({nwx},{nwy})", 5, C_NEAREST)
        put(f"Distancia:  {nearest_dist:.1f} tiles", 6)
        nxt = waypoints[(nearest_idx + 1) % len(waypoints)]
        put(f"Proximo WP: #{(nearest_idx+1)%len(waypoints)} ({nxt[0]},{nxt[1]})", 7, C_WAYPOINT)

    put(f"Waypoints ruta: {len(set((w[0],w[1]) for w in waypoints))}", 9)
    put("Verde=jugador  Rojo=mas cercano", 11, (180, 180, 180), 0.36)
    put("Amarillo=stand Naranja=random", 12, (180, 180, 180), 0.36)
    put(f"Hora: {time.strftime('%H:%M:%S')}", 14, (150, 150, 150), 0.38)

    # 7. Ensamblar imagen final
    vis = np.hstack([mm_big, panel])

    # 8. Guardar
    out_path = OUT / "debug_walker.png"
    cv2.imwrite(str(out_path), vis)
    print(f"[OK]  Guardado: {out_path}")
    return out_path

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    loop_mode = "--loop" in sys.argv
    interval = 2.0
    for arg in sys.argv[1:]:
        try:
            interval = float(arg)
        except ValueError:
            pass

    if loop_mode:
        print(f"Modo loop — actualizando cada {interval}s. Ctrl+C para detener.")
        print(f"Abre output/debug_walker.png en un visor con auto-recarga.")
        try:
            while True:
                snapshot()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nDetenido.")
    else:
        snapshot()
        print()
        print("Leyenda:")
        print("  Verde      = posicion actual del personaje")
        print("  Rojo       = waypoint mas cercano en la ruta")
        print("  Amarillo   = waypoints normales (stand)")
        print("  Naranja    = waypoints variables (random_stand)")
        print("  Gris circulo = zona nombrada (TEMPLE, DEPOT, CALLE)")
