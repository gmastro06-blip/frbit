"""
_clean_output.py
================
Limpia los archivos temporales / de depuración del directorio output/.

Borra:
  - debug_*.png          Imágenes de depuración del scan_roi y calibrador
  - debug_fullscreen.png
  - debug_contours.png
  - *.txt (excepto download_log.txt)
  - run_waypoints/stats.json y summary.txt (con --stats)

Uso:
    python examples/_clean_output.py              # sólo debug / temp
    python examples/_clean_output.py --stats      # también stats de ruta
    python examples/_clean_output.py --all        # todo lo anterior + logs
    python examples/_clean_output.py --dry-run    # mostrar qué se borraría
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


OUTPUT_DIR = Path(__file__).parent.parent / "output"


def _collect_targets(include_stats: bool = False, include_logs: bool = False) -> list[Path]:
    targets: list[Path] = []

    if not OUTPUT_DIR.exists():
        return targets

    # Debug images
    targets.extend(OUTPUT_DIR.glob("debug_*.png"))

    # Temp run-waypoint stats
    if include_stats:
        rw = OUTPUT_DIR / "run_waypoints"
        if rw.exists():
            targets.extend(rw.glob("*.json"))
            targets.extend(rw.glob("*.txt"))

    # Log files (optional)
    if include_logs:
        targets.extend(p for p in OUTPUT_DIR.glob("*.txt")
                       if p.name != "download_log.txt")

    return targets


def main() -> None:
    ap = argparse.ArgumentParser(description="Limpia el directorio output/")
    ap.add_argument("--stats",   action="store_true",
                    help="También borra run_waypoints/*.json y *.txt")
    ap.add_argument("--all",     action="store_true",
                    help="Equivale a --stats + --logs")
    ap.add_argument("--logs",    action="store_true",
                    help="También borra *.txt (excepto download_log.txt)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Mostrar qué se borraría sin borrar nada")
    args = ap.parse_args()

    include_stats = args.stats or args.all
    include_logs  = args.logs  or args.all

    targets = _collect_targets(include_stats=include_stats, include_logs=include_logs)

    if not targets:
        print("  output/ ya está limpio.")
        return

    label = "[DRY-RUN] " if args.dry_run else ""
    print(f"  {label}Archivos a eliminar ({len(targets)}):")
    for p in sorted(targets):
        rel = p.relative_to(OUTPUT_DIR.parent)
        print(f"    {rel}")

    if args.dry_run:
        print(f"\n  [DRY-RUN] Nada eliminado.")
        return

    deleted = 0
    for p in targets:
        try:
            p.unlink()
            deleted += 1
        except OSError as exc:
            print(f"  [!] No se pudo borrar {p.name}: {exc}")

    print(f"\n  Eliminados {deleted}/{len(targets)} archivos de output/")


if __name__ == "__main__":
    main()
