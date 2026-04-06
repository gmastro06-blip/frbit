"""
looter_demo.py
==============
Standalone demo del módulo Looter.

Usa un frame sintético (o un frame capturado de OBS) para probar el pipeline
de detección de cadáveres → apertura de contenedor → recogida de ítems.

Uso básico (dry-run con frame sintético):
    python examples/looter_demo.py --dry-run

Uso con OBS Virtual Camera (cámara real):
    python examples/looter_demo.py --source virtual-cam --window "Tibia"

Uso con WebSocket de OBS:
    python examples/looter_demo.py --source obs-ws --obs-host localhost --obs-port 4455

Argumentos
----------
--source        virtual-cam | obs-ws | synthetic   (default: synthetic)
--window        Título de la ventana de Tibia        (default: "Tibia")
--whitelist     Ítems a recoger (nombres de template), separados por coma
--loot-mode     whitelist | all                      (default: all)
--tile-size     Píxeles por tile                     (default: 32)
--obs-host      Host del servidor WebSocket de OBS   (default: localhost)
--obs-port      Puerto WebSocket de OBS              (default: 4455)
--obs-pass      Contraseña WebSocket de OBS          (default: "")
--kill-coord    Coordenada del cadáver "X,Y,Z"       (default: 32100,31900,7)
--player-coord  Posición del jugador  "X,Y,Z"        (default: 32100,31900,7)
--frames        Número de frames a procesar          (default: 30)
--dry-run       Solo muestra la configuración sin abrir la pantalla.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np  # used in type annotations and helper functions

# Añade el directorio raíz al path para importar src/
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.input_controller import InputController
from src.looter import Looter, LootConfig
from src.models import Coordinate


# ---------------------------------------------------------------------------
# Helpers de captura
# ---------------------------------------------------------------------------

def _build_synthetic_frame(width: int = 1280, height: int = 720) -> "np.ndarray":
    """Crea un frame BGR de color uniforme (simula un viewport vacío)."""
    import numpy as np
    return np.zeros((height, width, 3), dtype=np.uint8)


def _capture_virtualcam(width: int = 1280, height: int = 720) -> "np.ndarray | None":
    """Captura un frame de la OBS Virtual Camera (índice 0 por defecto)."""
    import cv2
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


def _capture_obsws(host: str, port: int, password: str) -> "np.ndarray | None":
    """Captura un screenshot vía OBS WebSocket (requiere obsws-python)."""
    try:
        import obsws_python as obs  # type: ignore[import-untyped]
        import base64, cv2, numpy as np
        cl = obs.ReqClient(host=host, port=port, password=password, timeout=3)
        resp = cl.get_source_screenshot(
            name="Game Capture",
            img_format="png",
            width=1280, height=720,
            quality=-1
        )
        raw = base64.b64decode(resp.image_data.split(",", 1)[1])
        arr = np.frombuffer(raw, np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as exc:  # noqa: BLE001
        print(f"[OBS-WS] Error capturando frame: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Looter demo – detecta cadáveres y recoge ítems.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--source", choices=["virtual-cam", "obs-ws", "synthetic"],
                   default="synthetic",
                   help="Fuente de frames (default: synthetic)")
    p.add_argument("--window", default="Tibia",
                   help="Título de la ventana de Tibia (default: Tibia)")
    p.add_argument("--whitelist", default="",
                   help="Ítems a recoger separados por coma (default: vacío = modo 'all')")
    p.add_argument("--loot-mode", choices=["whitelist", "all"], default="all",
                   help="Modo de loot (default: all)")
    p.add_argument("--tile-size", type=int, default=32,
                   help="Píxeles por tile en el viewport (default: 32)")
    p.add_argument("--obs-host", default="localhost",
                   help="Host del servidor WebSocket de OBS")
    p.add_argument("--obs-port", type=int, default=4455,
                   help="Puerto del servidor WebSocket de OBS")
    p.add_argument("--obs-pass", default="",
                   help="Contraseña del servidor WebSocket de OBS")
    p.add_argument("--kill-coord", default="32100,31900,7",
                   help="Coordenada del cadáver 'X,Y,Z' (default: 32100,31900,7)")
    p.add_argument("--player-coord", default="32100,31900,7",
                   help="Posición del jugador 'X,Y,Z' (default: 32100,31900,7)")
    p.add_argument("--frames", type=int, default=30,
                   help="Frames a procesar en el loop (default: 30)")
    p.add_argument("--dry-run", action="store_true",
                   help="Solo muestra la configuración sin ejecutar el looter.")
    return p.parse_args()


def _parse_coord(raw: str) -> Coordinate:
    parts = [int(v.strip()) for v in raw.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Coordenada inválida: {raw!r} — usa 'X,Y,Z'")
    return Coordinate(x=parts[0], y=parts[1], z=parts[2])


def main() -> None:
    args = parse_args()

    # --- Coordenadas ---
    try:
        kill_coord   = _parse_coord(args.kill_coord)
        player_coord = _parse_coord(args.player_coord)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    # --- Construir LootConfig ---
    whitelist = [s.strip() for s in args.whitelist.split(",") if s.strip()]
    cfg = LootConfig(
        tile_size_px=args.tile_size,
        loot_mode=args.loot_mode,
        loot_whitelist=whitelist,
    )

    print("=" * 60)
    print("  Looter Demo")
    print("=" * 60)
    print(f"  Fuente        : {args.source}")
    print(f"  Ventana       : {args.window}")
    print(f"  Modo loot     : {args.loot_mode}")
    print(f"  Whitelist     : {whitelist if whitelist else '(todo)'}")
    print(f"  Tile size     : {args.tile_size} px/tile")
    print(f"  Kill coord    : {kill_coord}")
    print(f"  Player coord  : {player_coord}")
    print(f"  Frames        : {args.frames}")
    print(f"  Dry-run       : {args.dry_run}")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY-RUN] Configuración cargada correctamente. Saliendo.")
        _show_dry_run_summary(cfg, kill_coord, player_coord)
        return

    # --- Crear Looter ---
    def player_getter() -> Coordinate:
        """En producción reemplaza esto con la lectura real del minimap."""
        return player_coord

    _ctrl = InputController(target_title=args.window)
    _current_frame: list = [_build_synthetic_frame()]

    def _get_frame():
        return _current_frame[0]

    looter = Looter(ctrl=_ctrl, config=cfg)
    looter.set_frame_getter(_get_frame)
    looter.set_player_getter(player_getter)
    looter.start()

    # Notificar la muerte para que el looter sepa que hay un cadáver
    looter.notify_kill(coord=kill_coord)

    print(f"\n[INFO] Looter iniciado. Procesando {args.frames} frames...\n")

    frames_processed = 0
    try:
        for i in range(args.frames):
            # --- Capturar frame ---
            if args.source == "virtual-cam":
                frame = _capture_virtualcam()
            elif args.source == "obs-ws":
                frame = _capture_obsws(args.obs_host, args.obs_port, args.obs_pass)
            else:
                frame = _build_synthetic_frame()

            if frame is None:
                print(f"  Frame {i+1:3d}: [WARN] No se pudo capturar frame — usando sintético")
                frame = _build_synthetic_frame()

            # --- Actualizar frame compartido ---
            _current_frame[0] = frame
            frames_processed += 1

            stats = looter.stats
            print(
                f"  Frame {i+1:3d}/{args.frames}"
                f"  |  cadáveres detectados: {stats.get('corpses_detected', 0):3d}"
                f"  |  ítems looteados: {stats.get('items_looted', 0):3d}"
                f"  |  ciclos completados: {stats.get('loot_cycles', 0):3d}",
                end="\r",
            )
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n[INFO] Interrumpido por el usuario.")
    finally:
        looter.stop()

    print(f"\n\n[DONE] {frames_processed} frames procesados.")
    _print_final_stats(looter.stats)


def _show_dry_run_summary(cfg: LootConfig, kill: Coordinate, player: Coordinate) -> None:
    """Muestra información relevante del looter sin ejecutarlo."""
    import math
    dx = kill.x - player.x
    dy = kill.y - player.y
    dist = math.sqrt(dx * dx + dy * dy)
    print(f"\n  Distancia jugador→cadáver : {dist:.2f} tiles")
    print(f"  Tile size                 : {cfg.tile_size_px} px/tile")
    px = cfg.tile_size_px * dx
    py = cfg.tile_size_px * dy
    print(f"  Offset en pantalla (px)   : dx={px}, dy={py}")
    print(f"  Max range tiles           : {cfg.max_range_tiles} (0 = sin límite)")
    print(f"  Loot mode                 : {cfg.loot_mode}")
    whitelist_str = ", ".join(cfg.loot_whitelist) if cfg.loot_whitelist else "(ninguno — recoger todo)"
    print(f"  Whitelist                 : {whitelist_str}")
    templates_dir = Path(__file__).parent.parent / "cache" / "templates"
    corpse_tpl  = list((templates_dir / "corpses").glob("*.png"))
    item_tpl    = list((templates_dir / "loot_items").glob("*.png"))
    print(f"\n  Templates cadáveres       : {len(corpse_tpl)} archivos")
    print(f"  Templates ítems           : {len(item_tpl)} archivos")
    if not corpse_tpl:
        print("    [HINT] Usa examples/capture_templates.py para generar templates de cadáveres.")
    if not item_tpl and cfg.loot_mode == "whitelist":
        print("    [HINT] Necesitas templates de ítems para el modo 'whitelist'.")


def _print_final_stats(stats: dict) -> None:
    print("\n  === Estadísticas finales ===")
    labels = {
        "corpses_detected": "Cadáveres detectados",
        "loot_cycles":      "Ciclos de loot completados",
        "items_looted":     "Ítems looteados",
        "loot_errors":      "Errores de loot",
    }
    for key, label in labels.items():
        print(f"  {label:<30s}: {stats.get(key, 0)}")


if __name__ == "__main__":
    main()
