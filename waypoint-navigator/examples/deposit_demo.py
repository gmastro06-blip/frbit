"""
deposit_demo.py
===============
Demo standalone del DepotManager.

Simula un ciclo de depot completo sin necesidad de Tibia abierto.
En modo dry-run muestra la configuración y el cálculo de píxeles.
En modo real conecta el InputController a la ventana de Tibia y ejecuta
el ciclo (abrir chest, depositar ítems, cerrar).

Uso rápido (dry-run, sin Tibia):
    python examples/deposit_demo.py --dry-run

Uso real (requiere Tibia abierto):
    python examples/deposit_demo.py --window "Tibia" --chest 32258,32248,7

Argumentos
----------
--window        Título de la ventana Tibia             (default: "Tibia")
--chest         Coordenada del chest "X,Y,Z"           (default: 32258,32248,7)
--bank          Coordenada del NPC banco "X,Y,Z"       (default: desactivado)
--player        Posición actual del jugador "X,Y,Z"    (default: igual al chest)
--tile-size     Píxeles por tile                       (default: 32)
--items         Ítems a depositar separados por coma   (default: todos)
--cycles        Número de ciclos a ejecutar            (default: 1)
--cycle-delay   Segundos entre ciclos                  (default: 2.0)
--close-vk      VK para cerrar contenedores (hex/dec)  (default: 0 = desactivado)
--source        virtual-cam | obs-ws | synthetic       (default: synthetic)
--obs-host      Host WebSocket de OBS                  (default: localhost)
--obs-port      Puerto WebSocket de OBS                (default: 4455)
--obs-pass      Contraseña WebSocket de OBS            (default: "")
--dry-run       Mostrar config sin ejecutar
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.depot_manager import DepotConfig, DepotManager
from src.models import Coordinate


# ---------------------------------------------------------------------------
# Frame capture helpers
# ---------------------------------------------------------------------------

def _synthetic_frame(width: int = 1280, height: int = 720):
    import numpy as np
    return np.zeros((height, width, 3), dtype=np.uint8)


def _capture_virtual_cam():
    import cv2
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


def _capture_obs_ws(host: str, port: int, password: str):
    try:
        import base64, cv2, numpy as np
        import obsws_python as obs  # type: ignore[import-untyped]
        cl = obs.ReqClient(host=host, port=port, password=password, timeout=3)
        resp = cl.get_source_screenshot(
            name="Game Capture", img_format="png",
            width=1280, height=720, quality=-1,
        )
        raw = base64.b64decode(resp.image_data.split(",", 1)[1])
        arr = np.frombuffer(raw, np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as exc:
        print(f"[OBS-WS] Error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="DepotManager demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--window",      default="Tibia")
    p.add_argument("--chest",       default="32258,32248,7",
                   help="Coord del chest X,Y,Z")
    p.add_argument("--bank",        default="",
                   help="Coord NPC banco X,Y,Z (vacío = desactivado)")
    p.add_argument("--player",      default="",
                   help="Posición jugador X,Y,Z (default = igual al chest)")
    p.add_argument("--tile-size",   type=int, default=32)
    p.add_argument("--items",       default="",
                   help="Ítems a depositar separados por coma (vacío = todos)")
    p.add_argument("--cycles",      type=int, default=1)
    p.add_argument("--cycle-delay", type=float, default=2.0)
    p.add_argument("--close-vk",    default="0",
                   help="VK para cerrar contenedores (hex 0x1B o dec 27)")
    p.add_argument("--source",      choices=["virtual-cam", "obs-ws", "synthetic"],
                   default="synthetic")
    p.add_argument("--obs-host",    default="localhost")
    p.add_argument("--obs-port",    type=int, default=4455)
    p.add_argument("--obs-pass",    default="")
    p.add_argument("--dry-run",     action="store_true")
    return p.parse_args()


def _parse_coord(raw: str) -> Optional[Coordinate]:
    if not raw.strip():
        return None
    parts = [int(v.strip()) for v in raw.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Coordenada inválida: {raw!r} — usa X,Y,Z")
    return Coordinate(x=parts[0], y=parts[1], z=parts[2])


def _parse_vk(raw: str) -> int:
    raw = raw.strip()
    if raw.startswith("0x") or raw.startswith("0X"):
        return int(raw, 16)
    return int(raw)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    try:
        chest_coord  = _parse_coord(args.chest)
        bank_coord   = _parse_coord(args.bank)
        player_coord = _parse_coord(args.player) if args.player else chest_coord
        close_vk     = _parse_vk(args.close_vk)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    if chest_coord is None:
        print("[ERROR] Se requiere una coordenada de chest válida (--chest X,Y,Z).")
        sys.exit(1)
    if player_coord is None:
        player_coord = chest_coord

    items = [s.strip() for s in args.items.split(",") if s.strip()]

    # Build DepotConfig
    cfg = DepotConfig(
        depot_chest_coord    = [chest_coord.x, chest_coord.y, chest_coord.z],
        bank_npc_coord       = [bank_coord.x, bank_coord.y, bank_coord.z] if bank_coord else [],
        tile_size_px         = args.tile_size,
        close_containers_vk  = close_vk,
    )

    # ── Print summary ───────────────────────────────────────────────────────
    print("=" * 60)
    print("  Depot Demo")
    print("=" * 60)
    print(f"  Ventana        : {args.window}")
    print(f"  Chest coord    : {chest_coord}")
    print(f"  Player coord   : {player_coord}")
    print(f"  Bank NPC       : {bank_coord or '(desactivado)'}")
    print(f"  Tile size      : {args.tile_size} px/tile")
    print(f"  Ítems          : {items if items else '(todos)'}")
    print(f"  Ciclos         : {args.cycles}")
    print(f"  Delay ciclosm  : {args.cycle_delay}s")
    print(f"  Close VK       : {hex(close_vk) if close_vk else '(desactivado)'}")
    print(f"  Fuente frame   : {args.source}")
    print(f"  Dry-run        : {args.dry_run}")
    print("=" * 60)

    if args.dry_run:
        _show_dry_run(cfg, chest_coord, player_coord)
        return

    # ── Set up InputController ──────────────────────────────────────────────
    from src.input_controller import InputController

    ctrl = InputController(target_title=args.window, input_method="postmessage")
    ctrl.find_target()
    if not ctrl.is_connected():
        print(f"\n[ERROR] No se pudo conectar a la ventana '{args.window}'.")
        print("  Asegúrate de que Tibia esté abierto y el título coincida.")
        sys.exit(1)

    print(f"\n  [OK] Conectado a '{args.window}'")

    # ── Frame getter ────────────────────────────────────────────────────────
    def get_frame():
        if args.source == "virtual-cam":
            f = _capture_virtual_cam()
            return f if f is not None else _synthetic_frame()
        if args.source == "obs-ws":
            f = _capture_obs_ws(args.obs_host, args.obs_port, args.obs_pass)
            return f if f is not None else _synthetic_frame()
        return _synthetic_frame()

    # ── Log callback ────────────────────────────────────────────────────────
    def log(msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] {msg}")

    # ── Create DepotManager ─────────────────────────────────────────────────
    manager = DepotManager(ctrl=ctrl, config=cfg, frame_getter=get_frame)
    manager.set_log_callback(log)

    # ── Run cycles ──────────────────────────────────────────────────────────
    print(f"\n  Iniciando {args.cycles} ciclo(s) de depot…\n")
    success_count = 0
    try:
        for i in range(args.cycles):
            print(f"\n  ── Ciclo {i+1}/{args.cycles} ──────────────────────────────")
            ok = manager.run_depot_cycle(
                player_pos=player_coord,
                backpack_items=items if items else None,
            )
            if ok:
                success_count += 1
            snap = manager.stats_snapshot()
            print(f"  Ciclos OK      : {snap['cycle_count']}")
            print(f"  Ítems deposit. : {snap['items_deposited']}")
            print(f"  Media/ciclo    : {snap['items_per_cycle']:.1f}")
            if i < args.cycles - 1:
                print(f"\n  Esperando {args.cycle_delay}s antes del siguiente ciclo…")
                time.sleep(args.cycle_delay)
    except KeyboardInterrupt:
        print("\n\n  Interrumpido por el usuario.")

    # ── Final stats ─────────────────────────────────────────────────────────
    snap = manager.stats_snapshot()
    print("\n  ═══ Estadísticas finales ═══════════════")
    print(f"  Ciclos completados : {snap['cycle_count']}/{args.cycles}")
    print(f"  Ciclos fallidos    : {args.cycles - success_count}")
    print(f"  Ítems depositados  : {snap['items_deposited']}")
    print(f"  Media por ciclo    : {snap['items_per_cycle']:.1f}")
    print("  ════════════════════════════════════════\n")


def _show_dry_run(cfg: DepotConfig, chest: Coordinate, player: Coordinate) -> None:
    """Print pixel-offset calculations without connecting to Tibia."""
    print("\n  [DRY-RUN] Cálculo de offsets en pantalla:")
    dx = chest.x - player.x
    dy = chest.y - player.y
    cx, cy = cfg.viewport_center
    px = cx + dx * cfg.tile_size_px
    py = cy + dy * cfg.tile_size_px
    print(f"  Chest coord       : ({chest.x}, {chest.y}, {chest.z})")
    print(f"  Player coord      : ({player.x}, {player.y}, {player.z})")
    print(f"  Viewport center   : {cfg.viewport_center}")
    print(f"  Tile size         : {cfg.tile_size_px} px/tile")
    print(f"  Delta tiles       : dx={dx}, dy={dy}")
    print(f"  Chest en pantalla : px=({px}, {py})")
    print(f"  Container ROI     : {cfg.container_roi}")
    print(f"  Open wait         : {cfg.open_wait}s")
    print(f"  Deposit mode      : {cfg.deposit_mode}")
    cap = cfg.max_items_per_cycle
    print(f"  Max items/ciclo   : {cap if cap else '(sin límite)'}")
    print(f"\n  [DRY-RUN] Nada ejecutado — todo OK.")


if __name__ == "__main__":
    main()
