"""
Live Tracker — Character Detector + WaypointNavigator
======================================================
Detecta la posición del personaje en tiempo real y calcula
automáticamente la ruta más corta al destino configurado.

Uso:
    python examples/live_tracker.py --dest-x 32344 --dest-y 32219 --dest-z 7
    python examples/live_tracker.py --source obs-ws --dest-name "temple"

Antes de ejecutar:
    1.  Activa OBS y habilita 'Virtual Camera' (si usas virtual-cam)
        – o configura OBS WebSocket (Tools → WebSocket Server Settings)
    2.  Calibra el ROI:   python src/calibrator.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.character_detector import CharacterDetector, DetectorConfig
from src.models import Coordinate
from src.navigator import WaypointNavigator


# ---------------------------------------------------------------------------
DIVIDER = "─" * 52


def format_route_info(
    nav: WaypointNavigator,
    current: Coordinate,
    dest: Coordinate,
) -> str:
    route = nav.navigate(current, dest)
    if route.found:
        return (
            f"  Pos actual : {current}\n"
            f"  Destino    : {dest}\n"
            f"  Pasos      : {len(route.steps)}\n"
            f"  Distancia  : {route.total_distance:.1f} tiles\n"
            f"  Próximo    : {route.steps[1] if len(route.steps) > 1 else '(ya llegaste)'}"
        )
    return (
        f"  Pos actual : {current}\n"
        f"  Destino    : {dest}\n"
        f"  Ruta       : ✗ No encontrada (¿mismo piso?)"
    )


# ---------------------------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    cfg = DetectorConfig.load()

    # Configurar contraseña OBS si se pasó por argumento
    if args.obs_password:
        cfg.obs_ws_password = args.obs_password
    if args.obs_source:
        cfg.obs_source = args.obs_source
    if args.interval:
        cfg.sample_interval = args.interval

    # Destino: por nombre o coordenadas
    nav = WaypointNavigator()
    dest: Optional[Coordinate] = None

    if args.dest_name:
        wps = nav.find_waypoints(args.dest_name)
        if not wps:
            print(f"ERROR: No se encontró waypoint '{args.dest_name}'")
            sys.exit(1)
        dest = wps[0].coord
        print(f"Destino seleccionado: {wps[0]}")
    elif args.dest_x and args.dest_y:
        dest = Coordinate(args.dest_x, args.dest_y, args.dest_z)
        print(f"Destino: {dest}")
    else:
        print("INFO: Sin destino configurado — solo mostrará posición detectada.")

    # Precargar piso si hay destino
    if dest and not nav.is_floor_loaded(dest.z):
        nav.load_floor(dest.z)

    print(DIVIDER)
    print(f"Fuente de captura : {args.source}"
          + (f" (monitor {args.monitor})" if args.source == "screen" else ""))
    print(f"Intervalo muestreo: {cfg.sample_interval}s")
    print(f"ROI configurado   : {cfg.roi}")
    print(DIVIDER)
    print("Iniciando tracker … (Ctrl+C para detener)\n")

    # Callback de posición
    def on_position(coord: Coordinate) -> None:
        # Autocargar piso si cambia
        if not nav.is_floor_loaded(coord.z):
            print(f"  Cargando piso {coord.z:02d} …")
            nav.load_floor(coord.z)

        print(f"\n{DIVIDER}")
        if dest is not None and dest.z == coord.z:
            print(format_route_info(nav, coord, dest))
        else:
            near = nav.nearest_waypoint(coord, top_n=3)
            near_txt = ", ".join(f"{w.name} ({coord.euclidean_to(w.coord):.0f}t)" for w in near)
            print(f"  Posición : {coord}")
            print(f"  Cercanos : {near_txt}")

    from src.character_detector import MSSScreenSource as _MSS
    detector = CharacterDetector(
        source=args.source,
        config=cfg,
        debug=args.debug,
    )
    # Aplicar selección de monitor si la fuente es 'screen'
    if args.source == "screen":
        detector._source = _MSS(monitor=args.monitor)
    detector.on_position(on_position)

    try:
        detector.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        detector.stop()
        last = detector.last_position
        if last:
            print(f"\nÚltima posición detectada: {last}")


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="live_tracker",
        description="Tracking de posición Tibia en tiempo real via OBS",
    )
    p.add_argument("--source", default="virtual-cam",
                   choices=["virtual-cam", "obs-ws", "screen"],
                   help="Fuente de captura (default: virtual-cam)")
    p.add_argument("--monitor", type=int, default=1,
                   help="Monitor a capturar cuando --source=screen (1=principal, 2=secundario…)")
    p.add_argument("--obs-source", default="",
                   help="Nombre de la fuente en OBS (solo --source obs-ws)")
    p.add_argument("--obs-password", default="",
                   help="Contraseña del WebSocket de OBS")
    p.add_argument("--dest-x", type=int, default=0, help="Destino X")
    p.add_argument("--dest-y", type=int, default=0, help="Destino Y")
    p.add_argument("--dest-z", type=int, default=7,  help="Destino Z / piso (default 7)")
    p.add_argument("--dest-name", default="",
                   help="Nombre del waypoint destino (busca en markers.json)")
    p.add_argument("--interval", type=float, default=0.0,
                   help="Intervalo de muestreo en segundos (default: usa config)")
    p.add_argument("--debug", action="store_true",
                   help="Guarda debug_roi.png con la imagen procesada")
    return p


if __name__ == "__main__":
    run(build_parser().parse_args())
