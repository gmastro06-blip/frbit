"""
export_route_json.py
====================
Herramienta de línea de comandos para crear y exportar ficheros de ruta JSON
compatibles con el WaypointNavigator.

Modos de uso
------------
1. Crear ruta desde coordenadas inline:

    python examples/export_route_json.py \\
        --coords "32369,32241,7 32370,32240,7 32371,32238,7" \\
        --out routes/mi_ruta.json

2. Convertir/re-exportar una ruta existente con metadatos extra:

    python examples/export_route_json.py \\
        --input routes/thais_depot_to_temple.json \\
        --name "Thais depot→temple v2" \\
        --out routes/thais_depot_to_temple_v2.json

3. Leer coords desde stdin (una por línea  "X,Y,Z"):

    echo "32369,32241,7" | python examples/export_route_json.py \\
        --stdin --out routes/custom.json

4. Dry-run – muestra la ruta sin escribir:

    python examples/export_route_json.py \\
        --input routes/thais_depot_to_temple.json --dry-run

Formato del JSON de salida
--------------------------
{
  "name": "...",
  "description": "...",
  "author": "...",
  "created": "2026-02-24T12:00:00",
  "waypoints": [
    {"x": 32369, "y": 32241, "z": 7, "name": "", "action": "walk"},
    ...
  ]
}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _parse_waypoints_from_string(raw: str) -> List[dict]:
    """
    Parse coords from a space OR newline separated string.
    Each token must be  "X,Y,Z"  or  "X,Y,Z:name"  or  "X,Y,Z:name:action".
    """
    waypoints: List[dict] = []
    for token in raw.replace("\n", " ").split():
        token = token.strip()
        if not token:
            continue
        parts = token.split(":")
        coord_part = parts[0]
        name_part   = parts[1] if len(parts) > 1 else ""
        action_part = parts[2] if len(parts) > 2 else "walk"
        nums = [int(v.strip()) for v in coord_part.split(",")]
        if len(nums) != 3:
            raise ValueError(f"Coordenada inválida: {token!r} — usa X,Y,Z")
        waypoints.append({
            "x": nums[0], "y": nums[1], "z": nums[2],
            "name": name_part,
            "action": action_part,
        })
    return waypoints


def _load_existing_route(path: Path) -> dict:
    with open(path) as f:
        data = json.load(f)
    # Normalise both formats:  list of coords  OR  dict with "waypoints" key
    if isinstance(data, list):
        return {"name": path.stem, "description": "", "author": "", "waypoints": data}
    return data


def _normalise_waypoints(wps: list) -> List[dict]:
    """Ensure every waypoint has name and action fields."""
    result = []
    for wp in wps:
        if isinstance(wp, (list, tuple)):
            x, y, z = int(wp[0]), int(wp[1]), int(wp[2])
            result.append({"x": x, "y": y, "z": z, "name": "", "action": "walk"})
        else:
            result.append({
                "x":      int(wp.get("x", 0)),
                "y":      int(wp.get("y", 0)),
                "z":      int(wp.get("z", 0)),
                "name":   str(wp.get("name", "")),
                "action": str(wp.get("action", "walk")),
            })
    return result


def _build_route(
    waypoints: List[dict],
    name: str = "",
    description: str = "",
    author: str = "",
    loop: bool = False,
) -> dict:
    return {
        "name":        name,
        "description": description,
        "author":      author,
        "loop":        loop,
        "created":     datetime.now().isoformat(timespec="seconds"),
        "waypoints":   waypoints,
    }


def _print_metadata(route: dict) -> None:
    """Print summary for a metadata-only route (no embedded waypoints)."""
    print(f"\n  {'─'*50}")
    print(f"  Nombre      : {route.get('name', '(sin nombre)')}")
    print(f"  Descripción : {route.get('description', '')}")
    start = route.get("start", {})
    end   = route.get("end", {})
    if start:
        print(f"  Start       : ({start.get('x')}, {start.get('y')}, z={start.get('z')})")
    if end:
        print(f"  End         : ({end.get('x')}, {end.get('y')}, z={end.get('z')})")
    print(f"  Steps       : {route.get('steps_count', '?')}")
    print(f"  (Sin array de waypoints — solo metadatos)")
    print(f"  {'─'*50}\n")


def _print_summary(route: dict) -> None:
    wps = route.get("waypoints", [])
    print(f"\n  {'─'*50}")
    print(f"  Nombre      : {route.get('name', '(sin nombre)')}")
    print(f"  Descripción : {route.get('description', '')}")
    print(f"  Autor       : {route.get('author', '')}")
    print(f"  Loop        : {route.get('loop', False)}")
    print(f"  Waypoints   : {len(wps)}")
    for i, wp in enumerate(wps[:8]):
        act = wp.get("action", "walk")
        nm  = f"  [{wp.get('name')}]" if wp.get("name") else ""
        print(f"    [{i:3d}] ({wp['x']}, {wp['y']}, z={wp['z']})  {act}{nm}")
    if len(wps) > 8:
        print(f"    … y {len(wps) - 8} más")
    print(f"  {'─'*50}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Crear / exportar rutas JSON para WaypointNavigator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--coords", metavar="COORDS",
                     help="Coords inline 'X,Y,Z X,Y,Z ...' (sep. por espacio / coma)")
    src.add_argument("--input",  metavar="FILE",
                     help="Ruta JSON existente para re-exportar / añadir metadatos")
    src.add_argument("--stdin",  action="store_true",
                     help="Leer coords desde stdin, una por línea 'X,Y,Z'")

    ap.add_argument("--out",         metavar="FILE", default="",
                    help="Fichero de salida .json (default: imprime en stdout)")
    ap.add_argument("--name",        default="",
                    help="Nombre de la ruta")
    ap.add_argument("--description", default="",
                    help="Descripción libre")
    ap.add_argument("--author",      default="",
                    help="Autor / nombre del bot")
    ap.add_argument("--loop",        action="store_true",
                    help="Marcar la ruta como cíclica (loop=true)")
    ap.add_argument("--indent",      type=int, default=2,
                    help="Indentación JSON (default: 2, 0=compacto)")
    ap.add_argument("--dry-run",     action="store_true",
                    help="Mostrar la ruta sin escribir ningún fichero")

    args = ap.parse_args()

    # ── Gather waypoints ────────────────────────────────────────────────────
    base_route: dict = {}
    indent_val: Optional[int] = args.indent if args.indent > 0 else None

    if args.stdin:
        raw = sys.stdin.read()
        waypoints = _normalise_waypoints(_parse_waypoints_from_string(raw))
    elif args.coords:
        waypoints = _normalise_waypoints(_parse_waypoints_from_string(args.coords))
    elif args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"[ERROR] Fichero no encontrado: {args.input}")
            sys.exit(1)
        base_route = _load_existing_route(input_path)
        waypoints  = _normalise_waypoints(base_route.get("waypoints", []))
    else:
        ap.print_help()
        sys.exit(0)

    if not waypoints:
        if args.input and not (args.coords or args.stdin):
            # Route metadata file without embedded waypoints — re-export as-is
            _print_metadata(base_route)
            json_str = json.dumps(base_route, indent=indent_val, ensure_ascii=False)
            if args.dry_run:
                print("  [DRY-RUN] Nada escrito.\n")
                return
            if args.out:
                out_path = Path(args.out)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json_str, encoding="utf-8")
                print(f"  Metadata re-exportada → {out_path}\n")
            else:
                print(json_str)
            return
        print("[ERROR] No se obtuvieron waypoints.")
        sys.exit(1)

    # ── Build route ─────────────────────────────────────────────────────────
    route = _build_route(
        waypoints   = waypoints,
        name        = args.name or base_route.get("name", ""),
        description = args.description or base_route.get("description", ""),
        author      = args.author or base_route.get("author", ""),
        loop        = args.loop or base_route.get("loop", False),
    )

    _print_summary(route)

    # ── Output ───────────────────────────────────────────────────────────────
    json_str = json.dumps(route, indent=indent_val, ensure_ascii=False)

    if args.dry_run:
        print("  [DRY-RUN] Nada escrito.\n")
        return

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_str, encoding="utf-8")
        print(f"  Ruta exportada → {out_path}\n")
    else:
        print(json_str)


if __name__ == "__main__":
    main()
