"""
run_monitor.py — Abre la ventana MonitorGui con una BotSession en dry_run.

Uso rápido (sin Tibia, sin mapa):
    python examples/run_monitor.py

Con ruta real:
    python examples/run_monitor.py --route routes/thais_temple_to_depot_bank.json

Con sesión real (Tibia abierto):
    python examples/run_monitor.py --route routes/thais_depot_to_temple.json --window Tibia --no-dry-run
"""

import sys
import argparse
import threading
from pathlib import Path

# project root
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from src.session import BotSession, SessionConfig
from src.monitor_gui import MonitorGui, MonitorConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Abre el Monitor GUI con una BotSession."
    )
    parser.add_argument("--route",        default="",
                        help="Ruta JSON (routes/*.json). Vacío = sesión idle.")
    parser.add_argument("--window",       default="Tibia",
                        help="Título de la ventana de Tibia (default: Tibia).")
    parser.add_argument("--loop",         action="store_true",
                        help="Repetir la ruta indefinidamente.")
    parser.add_argument("--no-dry-run",   action="store_true",
                        help="Enviar inputs reales (requiere Tibia abierto).")
    parser.add_argument("--refresh-ms",   type=int, default=1000,
                        help="Intervalo de refresco del monitor en ms (default 1000).")
    parser.add_argument("--geometry",     default="460x880",
                        help="Tamaño de ventana Tkinter, e.g. 460x880.")
    args = parser.parse_args()

    dry_run = not args.no_dry_run

    cfg = SessionConfig(
        route_file  = args.route,
        target_window = args.window,
        loop_route  = args.loop,
        dry_run     = dry_run,
        start_delay = 0.0,
    )

    logs: list[str] = []

    def _log(msg: str) -> None:
        print(msg)
        logs.append(msg)

    session = BotSession(cfg, log_callback=_log)

    print("=" * 50)
    print("  WaypointNavigator — Monitor GUI")
    print("=" * 50)
    print(f"  ruta      : {args.route or '(ninguna — sesión idle)'}")
    print(f"  dry_run   : {dry_run}")
    print(f"  loop      : {args.loop}")
    print(f"  refresh   : {args.refresh_ms} ms")
    print()
    print("  Abriendo ventana Tkinter…  (ciérrala para detener)")
    print()

    # sesión corre en hilo de fondo
    session.start()

    gui_cfg = MonitorConfig(
        refresh_ms=args.refresh_ms,
        geometry=args.geometry,
    )

    try:
        # open_monitor bloquea hasta que el usuario cierra la ventana
        session.open_monitor(config=gui_cfg)
    finally:
        session.stop()
        snap = session.stats_snapshot()
        print()
        print("Sesión detenida.")
        print(f"  routes_completed : {snap.get('routes_completed', 0)}")
        print(f"  heal_fired       : {snap.get('heal_fired', 0)}")
        print(f"  mana_fired       : {snap.get('mana_fired', 0)}")


if __name__ == "__main__":
    main()
