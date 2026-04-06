"""
Demo del OBS Waypoint Tracker — sin necesidad de OBS ni Tibia abierto.
Simula a un personaje caminando de Thais Depot al Thais Temple,
ejecuta el comparador de waypoints en tiempo real y guarda los frames.

Uso:
    python examples/obs_demo.py
"""
from __future__ import annotations

import json, math, sys, time, warnings
warnings.filterwarnings("ignore")
from pathlib import Path

# Forzar UTF-8 en Windows para emojis y caracteres especiales
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np

from src.models import Coordinate
from src.navigator import WaypointNavigator

# ── Ruta simulada: Thais Depot → Thais temple (floor 7) ─────────────────────
# Interpolamos ~40 pasos entre los dos puntos
START = Coordinate(32369, 32241, 7)   # Thais depot
END   = Coordinate(32344, 32219, 7)   # Thais temple

STEPS = 50        # frames simulados
ALERT = 25.0      # tiles para alerta
TOP_N = 7

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

DIVIDER = "=" * 60
THIN    = "-" * 60

_DIR_MAP = ["E","SE","S","SO","O","NO","N","NE"]
def _compass(o: Coordinate, t: Coordinate) -> str:
    dx, dy = t.x - o.x, t.y - o.y
    if dx == 0 and dy == 0: return "·"
    a = math.degrees(math.atan2(dy, dx))
    return _DIR_MAP[int((a + 202.5) % 360 // 45)]


def _build_hud(frame: np.ndarray, coord: Coordinate, near: list,
               route_pct: float, step: int, total: int) -> np.ndarray:
    h, w = frame.shape[:2]
    panel_w, panel_h = 400, 40 + len(near) * 24 + 60
    px1, py1 = w - panel_w - 12, 12
    cv2.rectangle(frame, (px1-2, py1-2), (px1+panel_w, py1+panel_h), (0,0,0), -1)
    cv2.rectangle(frame, (px1-2, py1-2), (px1+panel_w, py1+panel_h), (60,60,60), 1)

    fn = cv2.FONT_HERSHEY_SIMPLEX
    tx, ty, lh = px1 + 8, py1 + 18, 23

    cv2.putText(frame, f"Pos: {coord}",
                (tx, ty), fn, 0.48, (255, 220, 0), 1, cv2.LINE_AA)
    ty += lh
    cv2.putText(frame, f"Paso {step}/{total}  Ruta: {route_pct:.0f}%",
                (tx, ty), fn, 0.44, (200, 200, 200), 1, cv2.LINE_AA)
    ty += lh + 4

    for wp in near:
        dist = coord.euclidean_to(wp.coord)
        dire = _compass(coord, wp.coord) if coord.z == wp.coord.z else "-"
        color = (0, 60, 255) if dist <= ALERT else (0, 220, 80)
        label = f"{wp.name[:24]:24s} {dist:5.0f}t {dire}"
        cv2.putText(frame, label, (tx, ty), fn, 0.43, color, 1, cv2.LINE_AA)
        ty += lh

    # Barra de progreso
    bar_y = py1 + panel_h - 22
    bar_w = panel_w - 16
    cv2.rectangle(frame, (tx, bar_y), (tx + bar_w, bar_y+10), (40,40,40), -1)
    fill = int(bar_w * route_pct / 100)
    cv2.rectangle(frame, (tx, bar_y), (tx + fill, bar_y+10), (0,180,255), -1)
    cv2.putText(frame, "Ruta", (tx, bar_y - 4), fn, 0.38, (150,150,150), 1)

    # Mini brújula al waypoint más cercano
    if near:
        cx, cy = px1 + 30, py1 + panel_h + 35
        cv2.circle(frame, (cx, cy), 24, (80,80,80), 1)
        t = near[0].coord
        if coord.z == t.z:
            dx, dy2 = t.x - coord.x, t.y - coord.y
            mag = math.hypot(dx, dy2) or 1
            ax, ay = int(cx + dx/mag*18), int(cy + dy2/mag*18)
            cv2.arrowedLine(frame, (cx,cy), (ax,ay), (0,60,255), 2, tipLength=0.35)
        cv2.putText(frame, near[0].name[:18], (cx+28, cy+5), fn, 0.38, (0,60,255), 1)

    return frame


def main() -> None:
    print(DIVIDER)
    print("  OBS Waypoint Tracker — DEMO")
    print(DIVIDER)

    nav = WaypointNavigator()
    nav.load_floor(7)

    # Ruta real A* desde depot hasta temple
    route = nav.navigate(START, END)
    path  = route.steps if route.found else [
        Coordinate(
            int(START.x + (END.x - START.x) * i / (STEPS-1)),
            int(START.y + (END.y - START.y) * i / (STEPS-1)),
            7,
        )
        for i in range(STEPS)
    ]

    total = len(path)
    print(f"  Ruta A*: {total} pasos | {route.total_distance:.1f} tiles")
    print(f"Guardando frames en {OUTPUT_DIR}/")
    print(DIVIDER + "\n")

    frames_out = []

    for idx, coord in enumerate(path):
        near   = nav.nearest_waypoint(coord, top_n=TOP_N)
        pct    = idx / max(total - 1, 1) * 100
        alerts = [wp for wp in near if coord.euclidean_to(wp.coord) <= ALERT]

        # ── Dashboard consola ───────────────────────────────────────────
        print(f"\n{THIN}")
        print(f"  Paso {idx+1:03d}/{total}   Pos: {coord}   Ruta: {pct:.0f}%")
        print(f"  {'Waypoint':26s}  {'Dist':>7}  Dir")
        for wp in near:
            d = coord.euclidean_to(wp.coord)
            c = _compass(coord, wp.coord) if coord.z == wp.coord.z else "↕"
            marker = " ●" if d <= ALERT else "  "
            print(f"  {marker} {wp.name[:26]:26s}  {d:7.1f}  {c}")
        if alerts:
            print(f"\n  🔔 ALERTA: {', '.join(w.name for w in alerts)}")

        # ── Frame visual ────────────────────────────────────────────────
        # Canvas 800×200 negro con cuadrícula tenue
        canvas = np.zeros((200, 800, 3), dtype=np.uint8)
        # Cuadrícula
        for gx in range(0, 800, 40):
            cv2.line(canvas, (gx,0), (gx,200), (18,18,18), 1)
        for gy in range(0, 200, 40):
            cv2.line(canvas, (0,gy), (800,gy), (18,18,18), 1)

        def tile_to_px(c: Coordinate) -> tuple[int,int]:
            ox = int((c.x - START.x + 40) * 2)
            oy = int((c.y - START.y + 40) * 2.5)
            return max(0, min(799, ox)), max(0, min(199, oy))

        # Traza ruta recorrida
        for pi in range(1, idx+1):
            cv2.line(canvas, tile_to_px(path[pi-1]), tile_to_px(path[pi]),
                     (0, 120, 255), 1)

        # Waypoints cercanos
        for wp in near:
            px, py = tile_to_px(wp.coord)
            col = (0, 60, 255) if coord.euclidean_to(wp.coord) <= ALERT else (0, 180, 80)
            cv2.circle(canvas, (px, py), 5, col, -1)
            cv2.putText(canvas, wp.name[:12], (px+6, py+4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.28, col, 1)

        # Personaje
        cx_p, cy_p = tile_to_px(coord)
        cv2.circle(canvas, (cx_p, cy_p), 7, (255, 255, 0), -1)
        cv2.circle(canvas, (cx_p, cy_p), 7, (0,0,0), 1)

        # Destino
        dx_p, dy_p = tile_to_px(END)
        cv2.drawMarker(canvas, (dx_p, dy_p), (0,0,255),
                       cv2.MARKER_STAR, 14, 2)

        canvas = _build_hud(canvas, coord, near, pct, idx+1, total)
        frames_out.append(canvas.copy())

        # Guardar cada 10 frames + primero y último
        if idx % 10 == 0 or idx == total - 1:
            cv2.imwrite(str(OUTPUT_DIR / f"demo_frame_{idx:04d}.png"), canvas)

    # ── Generar GIF / video ─────────────────────────────────────────────
    try:
        import imageio
        gif_path = OUTPUT_DIR / "demo_route.gif"
        with imageio.get_writer(str(gif_path), mode="I", fps=8, loop=0) as writer:
            for f in frames_out:
                writer.append_data(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))  # type: ignore[attr-defined]
        print(f"\n{DIVIDER}")
        print(f"  GIF animado guardado: {gif_path}")
    except ImportError:
        pass

    # Video MP4
    try:
        vp = OUTPUT_DIR / "demo_route.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
        vw = cv2.VideoWriter(str(vp), fourcc, 8, (800, 200))
        for f in frames_out:
            vw.write(f)
        vw.release()
        print(f"  Video MP4 guardado  : {vp}")
    except Exception as e:
        print(f"  (video: {e})")

    print(DIVIDER)
    print(f"  Demo completado. {total} pasos procesados.")
    print(f"  Frames en: {OUTPUT_DIR}")
    print(DIVIDER)


if __name__ == "__main__":
    main()
