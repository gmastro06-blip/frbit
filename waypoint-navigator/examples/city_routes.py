#!/usr/bin/env python3
"""
City Routes — Launcher de rutas para ciudades free de Tibia
============================================================

Rutas disponibles para pruebas en ciudades free:

  ID  Ciudad          Ruta                          Distancia   Coords inicio
  ─── ─────────────── ───────────────────────────── ─────────── ───────────────────
  1   Thais           Temple → Depot                57 pasos    (32369,32241,z=7)
  2   Venore          Depot → Temple                MULTI-FLOOR (32927,32076,z=7)
  3   Ab'Dendriel     Temple → Depot                272 pasos   (32607,31680,z=7)
  4   Carlin          Depot → Temple                36 pasos    (32336,31784,z=7)

Uso:
    python examples/city_routes.py --route 1 --dry-run
    python examples/city_routes.py --route 2
    python examples/city_routes.py --list

Parámetros adicionales se pasan directamente a auto_walker:
    python examples/city_routes.py --route 1 --verify-pos --move-threshold 0.3
"""

import sys
import json
import subprocess
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ─────────────────────────────────────────────────────────────────────────────
# Tabla de rutas pre-definidas para ciudades free
# ─────────────────────────────────────────────────────────────────────────────
ROUTES = [
    {
        "id":      1,
        "city":    "Thais",
        "name":    "Temple → Depot",
        "dest":    "Thais Depot",
        "x":       32369,
        "y":       32241,
        "floor":   7,
        "steps":   "57",
        "note":    "Ruta corta, ideal para primera prueba. Calles rectas.",
    },
    {
        "id":      2,
        "city":    "Venore",
        "name":    "Depot → Temple",
        "dest":    "Venore Temple",
        "x":       32927,
        "y":       32076,
        "floor":   7,
        "steps":   "MULTI-FLOOR",
        "note":    "Depot(z=7) y Temple(z=7) separados por agua. Ruta pasa por z=6. Pathfinder actual no soporta multi-floor.",
    },
    {
        "id":      3,
        "city":    "Ab'Dendriel",
        "name":    "Temple → Depot",
        "dest":    "Ab'Dendriel Depot",
        "x":       32607,
        "y":       31680,
        "floor":   7,
        "steps":   "272",
        "note":    "Ciudad élfica. Calles estrechas — buen test del A*.",
    },
    {
        "id":      4,
        "city":    "Carlin",
        "name":    "Depot → Temple",
        "dest":    "Carlin Temple",
        "x":       32336,
        "y":       31784,
        "floor":   7,
        "steps":   "36",
        "note":    "Ruta corta. Ciudad del norte, alternativa a Thais.",
    },
]

PYTHON = sys.executable
WALKER = str(project_root / "examples" / "auto_walker.py")
CUSTOM_WP = str(project_root / "cache" / "custom_waypoints.json")


def list_routes() -> None:
    print("\n📍 Rutas disponibles para ciudades free:\n")
    print(f"  {'ID':>3}  {'Ciudad':<16} {'Ruta':<28} {'Pasos':>7}  Notas")
    print(f"  {'─'*3}  {'─'*16} {'─'*28} {'─'*7}  {'─'*40}")
    for r in ROUTES:
        print(
            f"  {r['id']:>3}  {r['city']:<16} {r['name']:<28} {r['steps']:>7}  {r['note']}"
        )
    print()
    print("Uso:  python examples/city_routes.py --route <ID> [--dry-run]")
    print()


def run_route(route: dict, extra_args: list, dry_run: bool, start_delay: int) -> int:
    """Build and execute the auto_walker command for a given route entry."""
    cmd = [
        PYTHON, WALKER,
        "--dest",    route["dest"],
        "--x",       str(route["x"]),
        "--y",       str(route["y"]),
        "--floor",   str(route["floor"]),
        # cache/custom_waypoints.json se carga automáticamente por auto_walker
        "--source",  "virtual-cam",
        "--obs-cam", "0",
        "--verify-pos",
        "--move-threshold", "0.3",
        "--start-delay", str(start_delay),
    ]
    if dry_run:
        cmd.append("--dry-run")
    cmd.extend(extra_args)

    print(f"\n{'='*60}")
    print(f"  Ciudad  : {route['city']}")
    print(f"  Ruta    : {route['name']}")
    print(f"  Destino : {route['dest']}")
    print(f"  Inicio  : ({route['x']},{route['y']},z={route['floor']})")
    print(f"  Modo    : {'DRY-RUN' if dry_run else 'REAL ⚠'}")
    print(f"{'='*60}\n")
    print("Comando:")
    print("  " + " ".join(cmd))
    print()

    result = subprocess.run(cmd)
    return result.returncode


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Launcher de rutas para ciudades free de Tibia",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--route",        type=int, default=None,
                    help="ID de la ruta a ejecutar (ver --list)")
    ap.add_argument("--list",         action="store_true",
                    help="Mostrar todas las rutas disponibles")
    ap.add_argument("--dry-run",      action="store_true",
                    help="Simular sin enviar inputs reales")
    ap.add_argument("--start-delay",  type=int, default=5,
                    help="Segundos de cuenta atrás (default 5)")

    args, extra = ap.parse_known_args()

    if args.list or args.route is None:
        list_routes()
        if args.route is None and not args.list:
            ap.print_help()
        return

    matches = [r for r in ROUTES if r["id"] == args.route]
    if not matches:
        print(f"[!] Ruta ID={args.route} no encontrada. Usa --list para ver opciones.")
        sys.exit(1)

    route = matches[0]
    rc = run_route(route, extra, dry_run=args.dry_run, start_delay=args.start_delay)
    sys.exit(rc)


if __name__ == "__main__":
    main()
