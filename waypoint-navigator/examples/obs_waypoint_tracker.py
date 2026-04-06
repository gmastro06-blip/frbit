"""
OBS Waypoint Tracker — captura en tiempo real + comparación de waypoints
=========================================================================
Lee la posición del personaje Tibia desde OBS (Virtual Camera, WebSocket)
o directamente de un monitor y la compara en tiempo real contra los
waypoints del mapa.

Características:
  • Selección de monitor  (--monitor 1|2|…, solo con --source screen)
  • Fuentes OBS: Virtual Camera, WebSocket v5, captura directa con mss
  • Waypoints cercanos con distancia y dirección
  • Alerta configurable de proximidad  (--alert-dist N tiles)
  • Seguimiento de ruta multi-waypoint  (--route "wp1,wp2,wp3")
  • Ventana OpenCV con overlay de waypoints sobre el frame capturado
  • Dashboard de texto actualizado en consola cada ciclo

Ejemplos de uso
---------------
  Captura el monitor 2 de la pantalla (sin OBS):
    python examples/obs_waypoint_tracker.py --source screen --monitor 2

  OBS Virtual Camera (monitor/fuente única):
    python examples/obs_waypoint_tracker.py --source virtual-cam

  OBS WebSocket con fuente concreta:
    python examples/obs_waypoint_tracker.py --source obs-ws \\
        --obs-source "Tibia" --obs-password secret

  Seguir ruta nombrada:
    python examples/obs_waypoint_tracker.py --route "thais depot,thais temple,thais bank"

  Sólo mostrar los 10 waypoints más cercanos:
    python examples/obs_waypoint_tracker.py --top-n 10

Antes de ejecutar por primera vez:
  1. Calibra el ROI:   python src/calibrator.py
  2. (si usas obs-ws)  Activa OBS → Tools → WebSocket Server Settings
  3. (si usas virtual-cam) Activa OBS → Start Virtual Camera
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np

from src.character_detector import (
    CharacterDetector,
    DetectorConfig,
    MSSScreenSource,
    VirtualCameraSource,
    OBSWebSocketSource,
)
from src.models import Coordinate
from src.navigator import WaypointNavigator

# ---------------------------------------------------------------------------
DIVIDER = "═" * 60
THIN    = "─" * 60

# Colores BGR para el overlay de OpenCV
_C_GREEN  = (0, 255, 80)
_C_CYAN   = (255, 220, 0)
_C_RED    = (0, 60, 255)
_C_YELLOW = (0, 215, 255)
_C_WHITE  = (240, 240, 240)
_C_BG     = (20, 20, 20)


# ---------------------------------------------------------------------------
# Helpers de dirección
# ---------------------------------------------------------------------------

_DIR8 = [
    ("N",  0, -1), ("NE",  1, -1), ("E",  1,  0), ("SE",  1,  1),
    ("S",  0,  1), ("SO", -1,  1), ("O", -1,  0), ("NO", -1, -1),
]


def _compass(origin: Coordinate, target: Coordinate) -> str:
    """Devuelve la dirección cardinal aproximada de *target* respecto a *origin*."""
    dx = target.x - origin.x
    dy = target.y - origin.y  # Y crece hacia el sur en Tibia
    if dx == 0 and dy == 0:
        return "·"
    angle = math.degrees(math.atan2(dy, dx))  # -180..180  (E=0, S=90, W=180, N=-90)
    # Convertir al sistema de 8 sectores
    sector = int((angle + 202.5) % 360 // 45)
    dirs = ["E", "SE", "S", "SO", "O", "NO", "N", "NE"]
    return dirs[sector]


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------

class OBSWaypointTracker:
    """
    Tracker en tiempo real que combina:
      - CharacterDetector (fuente OBS / pantalla)
      - WaypointNavigator (búsqueda de waypoints y rutas)
      - Overlay visual en ventana OpenCV
    """

    def __init__(
        self,
        source: str,
        monitor: int,
        top_n: int,
        alert_dist: float,
        route_names: List[str],
        obs_source: str,
        obs_password: str,
        obs_cam_index: int,
        interval: float,
        no_window: bool,
        debug: bool,
    ) -> None:
        self.top_n      = top_n
        self.alert_dist = alert_dist
        self.no_window  = no_window
        self._debug     = debug

        # ── Navigator
        self.nav = WaypointNavigator()

        # ── Ruta multi-waypoint (nombres)
        self._route_wps: List[Coordinate] = []
        self._route_names: List[str]      = []
        self._route_idx: int              = 0
        if route_names:
            self._resolve_route(route_names)

        # ── Config detector
        cfg = DetectorConfig.load()
        if obs_password:
            cfg.obs_ws_password = obs_password
        if obs_source:
            cfg.obs_source = obs_source
        if obs_cam_index >= 0:
            cfg.obs_cam_index = obs_cam_index
        if interval > 0:
            cfg.sample_interval = interval

        # ── Parche: si la fuente es 'screen' redirigimos monitor
        self._monitor   = monitor
        self._source_id = source

        self._detector = CharacterDetector(
            source=source,
            config=cfg,
            debug=debug,
        )

        # Parchear la fuente MSSScreenSource con el monitor elegido
        if source == "screen":
            self._detector._source = MSSScreenSource(monitor=monitor)

        # ── Estado compartido (acceso desde callback + hilo ventana)
        self._lock          = threading.Lock()
        self._last_coord    : Optional[Coordinate]            = None
        self._last_frame    : Optional[np.ndarray]            = None
        self._last_near     : List                            = []
        self._alerts        : List[str]                       = []
        self._route_status  : str                             = ""
        self._cycle         : int                             = 0

        # ── Registrar callback
        self._detector.on_position(self._on_position)

    # -----------------------------------------------------------------------
    # Resolución de ruta
    # -----------------------------------------------------------------------

    def _resolve_route(self, names: List[str]) -> None:
        print(f"\nResolviendo ruta: {' → '.join(names)}")
        for name in names:
            wps = self.nav.find_waypoints(name)
            if not wps:
                print(f"  ✗ Waypoint no encontrado: {name!r}")
                continue
            self._route_wps.append(wps[0].coord)
            self._route_names.append(wps[0].name)
            print(f"  ✓ {wps[0]}")
        if len(self._route_wps) < 2:
            print("  AVISO: Se necesitan ≥2 waypoints para seguimiento de ruta.")
            self._route_wps = []

    # -----------------------------------------------------------------------
    # Callback de posición detectada
    # -----------------------------------------------------------------------

    def _on_position(self, coord: Coordinate) -> None:
        # Cargar piso si es necesario
        if not self.nav.is_floor_loaded(coord.z):
            print(f"\n  Cargando piso {coord.z:02d} …")
            self.nav.load_floor(coord.z)

        near = self.nav.nearest_waypoint(coord, top_n=self.top_n)

        # Alertas de proximidad
        alerts: List[str] = []
        for wp in near:
            dist = coord.euclidean_to(wp.coord)
            if dist <= self.alert_dist:
                alerts.append(f"🔔 CERCA: {wp.name} ({dist:.0f} tiles)")

        # Progreso en ruta
        route_status = ""
        if self._route_wps:
            route_status = self._update_route(coord)

        # Capturar frame actual para overlay
        frame = self._detector._source.get_frame()

        with self._lock:
            self._last_coord   = coord
            self._last_near    = near
            self._alerts       = alerts
            self._route_status = route_status
            self._cycle       += 1
            if frame is not None:
                self._last_frame = frame.copy()

        self._print_dashboard(coord, near, alerts, route_status)

    # -----------------------------------------------------------------------
    # Progreso de ruta
    # -----------------------------------------------------------------------

    def _update_route(self, coord: Coordinate) -> str:
        if self._route_idx >= len(self._route_wps):
            return "🏁 Ruta completada"

        target = self._route_wps[self._route_idx]

        # Avanzar si llegamos al waypoint actual (radio ≤ 5 tiles)
        if (coord.z == target.z
                and coord.euclidean_to(target) <= 5.0):
            self._route_idx += 1
            if self._route_idx >= len(self._route_wps):
                return "🏁 Ruta completada"
            target = self._route_wps[self._route_idx]

        # Calcular info hacia el próximo objetivo
        total  = len(self._route_wps)
        name   = self._route_names[self._route_idx]
        dist   = coord.euclidean_to(target) if coord.z == target.z else float("inf")
        dire   = _compass(coord, target) if coord.z == target.z else "?"
        return (
            f"Ruta [{self._route_idx + 1}/{total}]  "
            f"{name}  "
            f"— {dist:.0f}t {dire}"
        )

    # -----------------------------------------------------------------------
    # Dashboard de consola
    # -----------------------------------------------------------------------

    def _print_dashboard(
        self,
        coord: Coordinate,
        near: List,
        alerts: List[str],
        route_status: str,
    ) -> None:
        lines = [
            f"\n{DIVIDER}",
            f"  Ciclo #{self._cycle:04d}   Posición: {coord}",
            THIN,
        ]

        if route_status:
            lines.append(f"  {route_status}")
            lines.append(THIN)

        lines.append(f"  {'WP más cercanos':30s}  {'Dist':>8}  {'Dir':>4}")
        lines.append(f"  {'─'*30}  {'─'*8}  {'─'*4}")
        for wp in near:
            dist = coord.euclidean_to(wp.coord)
            dire = _compass(coord, wp.coord) if coord.z == wp.coord.z else "↕"
            marker = " ●" if dist <= self.alert_dist else "  "
            lines.append(
                f"{marker} {wp.name[:30]:30s}  {dist:>8.1f}  {dire:>4}"
            )

        if alerts:
            lines.append(THIN)
            for a in alerts:
                lines.append(f"  {a}")

        print("\n".join(lines))

    # -----------------------------------------------------------------------
    # Overlay OpenCV
    # -----------------------------------------------------------------------

    def _draw_overlay(self, frame: np.ndarray, coord: Coordinate, near: List, alerts: List[str]) -> np.ndarray:
        """Dibuja un panel HUD sobre el frame capturado."""
        h, w = frame.shape[:2]
        overlay = frame.copy()

        # Panel oscuro semitransparente (esquina superior derecha)
        panel_w  = 360
        panel_h  = 30 + len(near) * 22 + (len(alerts) * 22 if alerts else 0) + 40
        px1, py1 = w - panel_w - 10, 10
        px2, py2 = w - 10, py1 + panel_h
        cv2.rectangle(overlay, (px1, py1), (px2, py2), _C_BG, -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

        font  = cv2.FONT_HERSHEY_SIMPLEX
        fs    = 0.45
        lh    = 22  # line height px
        tx    = px1 + 8
        ty    = py1 + 20

        # Título
        cv2.putText(frame, f"Pos: {coord}", (tx, ty), font, fs, _C_CYAN, 1, cv2.LINE_AA)
        ty += lh + 4

        # Waypoints cercanos
        for wp in near:
            dist = coord.euclidean_to(wp.coord)
            dire = _compass(coord, wp.coord) if coord.z == wp.coord.z else "-"
            color  = _C_RED if dist <= self.alert_dist else _C_GREEN
            label  = f"{wp.name[:22]:22s} {dist:6.0f}t {dire}"
            cv2.putText(frame, label, (tx, ty), font, fs, color, 1, cv2.LINE_AA)
            ty += lh

        # Alertas
        for a in alerts:
            cv2.putText(frame, a, (tx, ty), font, fs, _C_YELLOW, 1, cv2.LINE_AA)
            ty += lh

        # Minicompass (visual rápido del waypoint más cercano)
        if near:
            cx, cy = px1 + panel_w // 2, py2 + 30
            r = 18
            cv2.circle(frame, (cx, cy), r, _C_WHITE, 1)
            target = near[0].coord
            if coord.z == target.z:
                dx = target.x - coord.x
                dy = target.y - coord.y
                mag = math.hypot(dx, dy) or 1
                ax = int(cx + dx / mag * r * 0.8)
                ay = int(cy + dy / mag * r * 0.8)
                cv2.arrowedLine(frame, (cx, cy), (ax, ay), _C_RED, 2, tipLength=0.3)

        return frame

    # -----------------------------------------------------------------------
    # Ventana de preview
    # -----------------------------------------------------------------------

    def _window_loop(self) -> None:
        cv2.namedWindow("OBS Waypoint Tracker", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("OBS Waypoint Tracker", 960, 540)

        while self._running:
            with self._lock:
                frame = self._last_frame.copy() if self._last_frame is not None else None
                coord = self._last_coord
                near  = list(self._last_near)
                alerts = list(self._alerts)

            if frame is not None and coord is not None:
                frame = self._draw_overlay(frame, coord, near, alerts)
                cv2.imshow("OBS Waypoint Tracker", frame)
            else:
                # Placeholder mientras no hay frame
                placeholder = np.zeros((200, 500, 3), dtype=np.uint8)
                cv2.putText(
                    placeholder, "Esperando frame OBS...",
                    (30, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, _C_WHITE, 1
                )
                cv2.imshow("OBS Waypoint Tracker", placeholder)

            key = cv2.waitKey(100) & 0xFF
            if key in (ord("q"), ord("Q"), 27):  # Q o ESC cierra
                self.stop()
                break

        cv2.destroyAllWindows()

    # -----------------------------------------------------------------------
    # Start / Stop
    # -----------------------------------------------------------------------

    def start(self) -> None:
        print(DIVIDER)
        print("  OBS Waypoint Tracker")
        print(DIVIDER)
        print(f"  Fuente     : {self._source_id}"
              + (f" (monitor {self._monitor})" if self._source_id == "screen" else ""))
        print(f"  Top-N WPs  : {self.top_n}")
        print(f"  Alerta     : ≤ {self.alert_dist} tiles")
        if self._route_wps:
            print(f"  Ruta       : {' → '.join(self._route_names)}")
        print(DIVIDER)
        print("  Iniciando … (Ctrl+C o 'Q' en la ventana para detener)\n")

        self._running = True
        self._detector.start()

        if not self.no_window:
            # Ejecutar la ventana en el hilo principal (OpenCV requiere el hilo principal en Windows)
            self._window_loop()
        else:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

    def stop(self) -> None:
        self._running = False
        self._detector.stop()
        last = self._detector.last_position
        if last:
            print(f"\n  Última posición: {last}")
        print("  Tracker detenido.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="obs_waypoint_tracker",
        description=(
            "Captura en tiempo real desde OBS o monitor "
            "y compara la posición con los waypoints del mapa."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python examples/obs_waypoint_tracker.py --source screen --monitor 2
  python examples/obs_waypoint_tracker.py --source virtual-cam --top-n 10
  python examples/obs_waypoint_tracker.py --source obs-ws --obs-source "Tibia" --obs-password secret
  python examples/obs_waypoint_tracker.py --route "thais depot,thais temple" --alert-dist 30
""",
    )

    # Fuente de captura
    grp = p.add_argument_group("Fuente de captura")
    grp.add_argument(
        "--source", default="screen",
        choices=["screen", "virtual-cam", "obs-ws"],
        help="Fuente de captura (default: screen)",
    )
    grp.add_argument(
        "--monitor", type=int, default=1,
        help="Número de monitor a capturar cuando --source=screen (1=principal, 2=secundario…)",
    )
    grp.add_argument("--obs-source",   default="", help="Nombre de la fuente en OBS (solo obs-ws)")
    grp.add_argument("--obs-password", default="", help="Contraseña del WebSocket de OBS")
    grp.add_argument("--obs-cam-index", type=int, default=-1,
                     help="Índice de la Virtual Camera de OBS en cv2 (default: usa config)")

    # Waypoints
    grp2 = p.add_argument_group("Comparación de waypoints")
    grp2.add_argument(
        "--top-n", type=int, default=7,
        help="Número de waypoints cercanos a mostrar (default: 7)",
    )
    grp2.add_argument(
        "--alert-dist", type=float, default=20.0,
        help="Distancia en tiles para mostrar alerta de proximidad (default: 20)",
    )
    grp2.add_argument(
        "--route", default="",
        help=(
            'Lista de nombres de waypoint separados por coma para seguimiento '
            'de ruta. Ej: "thais depot,thais temple,thais bank"'
        ),
    )

    # Misc
    grp3 = p.add_argument_group("Otras opciones")
    grp3.add_argument(
        "--interval", type=float, default=0.0,
        help="Intervalo de muestreo en segundos (0 = usa detector_config.json)",
    )
    grp3.add_argument(
        "--no-window", action="store_true",
        help="Desactiva la ventana OpenCV (solo salida por consola)",
    )
    grp3.add_argument(
        "--debug", action="store_true",
        help="Guarda debug_roi.png con la imagen del ROI procesado",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    route_names: List[str] = (
        [n.strip() for n in args.route.split(",") if n.strip()]
        if args.route else []
    )

    tracker = OBSWaypointTracker(
        source        = args.source,
        monitor       = args.monitor,
        top_n         = args.top_n,
        alert_dist    = args.alert_dist,
        route_names   = route_names,
        obs_source    = args.obs_source,
        obs_password  = args.obs_password,
        obs_cam_index = args.obs_cam_index,
        interval      = args.interval,
        no_window     = args.no_window,
        debug         = args.debug,
    )

    try:
        tracker.start()
    except KeyboardInterrupt:
        tracker.stop()


if __name__ == "__main__":
    main()
