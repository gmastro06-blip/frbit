"""
tools/record_route.py - Grabador de rutas en tiempo real para Tibia.

Graba automáticamente el movimiento del personaje y permite marcar
acciones especiales con teclas de función (F1-F8) que NO interfieren
con los controles de Tibia.

Uso:
    python tools/record_route.py
    python tools/record_route.py --name mi_ruta --output routes/mi_ruta
    python tools/record_route.py --source wgc --window "Tibia"

Teclas de acción (funcionan con Tibia en primer plano):
    F1 = rope    en la posición actual
    F2 = door    en la posición actual
    F3 = ladder  en la posición actual
    F4 = shovel  en la posición actual
    F5 = stand   en la posición actual (waypoint exacto, sin pathfinding)
    F6 = undo    (borra la última entrada)
    F9 = guardar ahora
    F10 / ESC = guardar y salir

El movimiento se graba solo — no necesitas hacer nada mientras caminas.
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ANSI
G = "\033[92m"
C = "\033[96m"
Y = "\033[93m"
RE = "\033[91m"
B = "\033[1m"
X = "\033[0m"

# ---------------------------------------------------------------------------
# Detección de teclas — GetAsyncKeyState, sin paquetes extra
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32

_VK = {
    "F1":  0x70,
    "F2":  0x71,
    "F3":  0x72,
    "F4":  0x73,
    "F5":  0x74,
    "F6":  0x75,
    "F9":  0x78,
    "F10": 0x79,
    "ESC": 0x1B,
}
_KEY_PREV: dict[str, bool] = {k: False for k in _VK}


def _pressed(name: str) -> bool:
    """True en el flanco de subida (key-down → key-up transition)."""
    state = bool(_user32.GetAsyncKeyState(_VK[name]) & 0x8000)
    prev  = _KEY_PREV[name]
    _KEY_PREV[name] = state
    return state and not prev


# ---------------------------------------------------------------------------
# Captura de frames y radar
# ---------------------------------------------------------------------------

def _find_hwnd(fragment: str) -> int:
    results: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def cb(hwnd: int, _: int) -> bool:
        if _user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, buf, 256)
            if fragment.lower() in buf.value.lower():
                results.append(hwnd)
        return True
    _user32.EnumWindows(WNDENUMPROC(cb), 0)
    return results[0] if results else 0


def _build_capture(source: str, window: str):
    from src.minimap_radar import MinimapConfig, MinimapRadar
    from src.map_loader import TibiaMapLoader

    if source == "printwindow":
        hwnd = _find_hwnd(window)
        if not hwnd:
            raise RuntimeError(f"Ventana '{window}' no encontrada")
        from src.frame_capture import PrintWindowCapture
        fg = PrintWindowCapture(hwnd=hwnd).open()
        print(f"  PrintWindow hwnd={hwnd:#x}")
    else:
        from src.frame_capture import build_frame_getter
        fg = build_frame_getter(source)
        print(f"  Frame source: {source}")

    cfg    = MinimapConfig.load()
    loader = TibiaMapLoader(cache_dir=Path("maps"))

    # Precarga los tiles del floor actual
    img = loader.get_map_image(cfg.floor)
    if img is None:
        raise RuntimeError(
            f"No se pudo cargar el mapa para floor {cfg.floor}. "
            f"Asegúrate de que el directorio 'maps/' contiene los archivos .png del mapa."
        )

    radar = MinimapRadar(loader=loader, config=cfg)
    print(f"  Radar floor={cfg.floor}  roi={cfg.roi}")
    return fg, radar, cfg.floor


def _hint_coord(pos: Optional[tuple[int, int, int]]):
    if pos is None:
        return None
    try:
        from src.models import Coordinate
        return Coordinate(x=pos[0], y=pos[1], z=pos[2])
    except Exception:
        return None


def _read_pos(fg, radar, hint=None) -> Optional[tuple[int, int, int]]:
    try:
        frame = fg()
        if frame is None:
            return None
        coord = radar.read(frame, hint=hint)
        if coord is not None:
            return (coord.x, coord.y, coord.z)
    except Exception as e:
        pass
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(name: str, output: Path, source: str, window: str, interval: float) -> None:
    from src.navigation.route_recorder import LiveRouteRecorder

    rec = LiveRouteRecorder(name=name)
    rec.start()

    # Inicializar captura
    fg = radar = None
    radar_floor = 7
    radar_ok = False
    try:
        fg, radar, radar_floor = _build_capture(source, window)
        radar_ok = True
    except Exception as e:
        print(f"\n  {RE}ERROR{X} captura: {e}")
        print(f"  {Y}El script funciona, pero sin lectura automática de posición.{X}\n")

    # Diagnóstico rápido del radar
    if radar_ok:
        print(f"  Probando lectura del radar...", end=" ", flush=True)
        test_pos = _read_pos(fg, radar)
        if test_pos:
            print(f"{G}OK{X} → {test_pos}")
        else:
            print(f"{Y}SIN POSICIÓN{X}")
            print(f"  {Y}Verifica que el minimap sea visible y esté calibrado{X}")
            print(f"  {Y}(usa python tools/calibrate.py si es necesario){X}")

    print(f"\n{B}{'='*58}{X}")
    print(f"  {B}Record Route{X}  —  {C}{name}{X}")
    print(f"  Output: {output.with_suffix('.in')}")
    print(f"{'='*58}")
    print(f"  {G}F1{X}=rope  {G}F2{X}=door  {G}F3{X}=ladder  {G}F4{X}=shovel")
    print(f"  {G}F5{X}=stand (tile exacto)  {G}F6{X}=undo")
    print(f"  {G}F9{X}=guardar  {G}F10/ESC{X}=salir")
    if radar_ok:
        print(f"  {Y}Movimiento grabado automáticamente cada {interval}s{X}")
    else:
        print(f"  {Y}Sin radar — las acciones se graban en la última posición conocida{X}")
    print(f"{'='*58}\n")

    last_status  = ""
    last_auto_pos: Optional[tuple[int, int, int]] = None
    last_tick    = time.monotonic()
    radar_fails  = 0          # contador de fallos consecutivos del radar

    def _status(msg: str = "") -> None:
        nonlocal last_status
        cur = rec.current_pos
        pos_str = f"({cur[0]},{cur[1]},{cur[2]})" if cur else f"{RE}sin pos{X}"
        radar_str = f" {RE}[radar KO x{radar_fails}]{X}" if radar_fails >= 3 else ""
        line = f"\r  {C}pos{X} {pos_str}  {C}#{X}{rec.count}  {msg}{radar_str}          "
        if line != last_status:
            sys.stdout.write(line)
            sys.stdout.flush()
            last_status = line

    def _log(label: str, x: int, y: int, z: int) -> None:
        sys.stdout.write(f"\n  {G}+{X} {label:8s} ({x},{y},{z})\n")
        sys.stdout.flush()

    def _get_live() -> Optional[tuple[int, int, int]]:
        """Lee posición fresca del radar para una acción manual."""
        if fg and radar:
            return _read_pos(fg, radar, hint=_hint_coord(rec.current_pos))
        return None

    def _action_pos() -> Optional[tuple[int, int, int]]:
        """Posición para una acción manual: live > último conocido > None."""
        live = _get_live()
        if live:
            return live
        cur = rec.current_pos
        if cur:
            return cur
        return None   # sin posición → no graba

    running = True
    while running:
        now = time.monotonic()

        # ── Auto-grab posición ────────────────────────────────────────────
        if fg and radar and (now - last_tick) >= interval:
            last_tick = now
            pos = _read_pos(fg, radar, hint=_hint_coord(rec.current_pos))
            if pos:
                radar_fails = 0
                if pos != last_auto_pos:
                    added = rec.record_position(*pos)
                    last_auto_pos = pos
                    if added:
                        _status(f"{Y}walk{X}")
            else:
                radar_fails += 1

        # ── Teclas de acción ─────────────────────────────────────────────
        action_done = False

        if _pressed("F1"):
            p = _action_pos()
            if p:
                rec.rope(*p);   _log("rope",   *p)
            else:
                sys.stdout.write(f"\n  {RE}F1 ignorado — sin posición conocida{X}\n")
                sys.stdout.flush()
            action_done = True

        elif _pressed("F2"):
            p = _action_pos()
            if p:
                rec.door(*p);   _log("door",   *p)
            else:
                sys.stdout.write(f"\n  {RE}F2 ignorado — sin posición conocida{X}\n")
                sys.stdout.flush()
            action_done = True

        elif _pressed("F3"):
            p = _action_pos()
            if p:
                rec.ladder(*p); _log("ladder", *p)
            else:
                sys.stdout.write(f"\n  {RE}F3 ignorado — sin posición conocida{X}\n")
                sys.stdout.flush()
            action_done = True

        elif _pressed("F4"):
            p = _action_pos()
            if p:
                rec.shovel(*p); _log("shovel", *p)
            else:
                sys.stdout.write(f"\n  {RE}F4 ignorado — sin posición conocida{X}\n")
                sys.stdout.flush()
            action_done = True

        elif _pressed("F5"):
            p = _action_pos()
            if p:
                rec.stand(*p);  _log("stand",  *p)
            else:
                sys.stdout.write(f"\n  {RE}F5 ignorado — sin posición conocida{X}\n")
                sys.stdout.flush()
            action_done = True

        elif _pressed("F6"):
            entry = rec.undo()
            if entry:
                sys.stdout.write(f"\n  {Y}undo{X}: {entry.to_script_line()}\n")
                sys.stdout.flush()
            action_done = True

        elif _pressed("F9"):
            _save(rec, output)
            action_done = True

        elif _pressed("F10") or _pressed("ESC"):
            running = False
            continue

        if not action_done:
            _status()

        time.sleep(0.05)

    # Guardar al salir
    print()
    _save(rec, output)
    print(f"\n  {G}Listo.{X} {rec.count} entradas grabadas.\n")


def _save(rec, output: Path) -> None:
    script_path = output.with_suffix(".in")
    json_path   = output.with_suffix(".json")
    rec.save_script(str(script_path))
    rec.save_json(str(json_path))
    print(f"\n  {G}Guardado{X}: {script_path}  ({rec.count} entradas)")
    print(f"  {G}Guardado{X}: {json_path}")


# ---------------------------------------------------------------------------

def diag(source: str, window: str) -> None:
    """Captura un frame, muestra dimensiones y guarda recortes para debug."""
    import cv2
    import numpy as np
    from src.minimap_radar import MinimapConfig

    print(f"\n{B}── Diagnóstico de captura ──{X}")

    # 1. Obtener frame
    try:
        if source == "printwindow":
            hwnd = _find_hwnd(window)
            if not hwnd:
                print(f"  {RE}ERROR{X}: ventana '{window}' no encontrada")
                return
            from src.frame_capture import PrintWindowCapture
            fg = PrintWindowCapture(hwnd=hwnd).open()
            print(f"  PrintWindow hwnd={hwnd:#x}")
        else:
            from src.frame_capture import build_frame_getter
            fg = build_frame_getter(source)
        frame = fg()
    except Exception as e:
        print(f"  {RE}ERROR capturando frame: {e}{X}")
        return

    if frame is None:
        print(f"  {RE}frame es None — la ventana no responde{X}")
        return

    h, w = frame.shape[:2]
    print(f"  Frame: {w}x{h} px")

    # Guardar frame completo
    Path("output").mkdir(exist_ok=True)
    cv2.imwrite("output/diag_frame.png", frame)
    print(f"  {G}Guardado{X}: output/diag_frame.png  (frame completo)")

    # 2. Recorte según ROI del config
    cfg = MinimapConfig.load()
    rx, ry, rw, rh = cfg.roi
    print(f"  MinimapConfig.roi = [{rx}, {ry}, {rw}, {rh}]  (config actual)")

    crop = frame[ry:ry+rh, rx:rx+rw]
    if crop.size == 0:
        print(f"  {RE}ROI fuera del frame — el ROI no encaja en {w}x{h}{X}")
        print(f"  {Y}Necesitas recalibrar con: python tools/calibrate.py{X}")
    else:
        cv2.imwrite("output/diag_minimap_roi.png", crop)
        print(f"  {G}Guardado{X}: output/diag_minimap_roi.png  (recorte ROI)")

    # 3. Escala esperada vs real
    ref_w, ref_h = 1920, 1080
    if w != ref_w or h != ref_h:
        scale_x = w / ref_w
        scale_y = h / ref_h
        adj_x = int(rx * scale_x)
        adj_y = int(ry * scale_y)
        adj_w = int(rw * scale_x)
        adj_h = int(rh * scale_y)
        print(f"\n  {Y}El frame es {w}x{h} pero el ROI está configurado para 1920x1080.{X}")
        print(f"  ROI ajustado a esta resolución: [{adj_x}, {adj_y}, {adj_w}, {adj_h}]")
        crop2 = frame[adj_y:adj_y+adj_h, adj_x:adj_x+adj_w]
        if crop2.size > 0:
            cv2.imwrite("output/diag_minimap_scaled.png", crop2)
            print(f"  {G}Guardado{X}: output/diag_minimap_scaled.png  (recorte escalado)")
        print(f"\n  {Y}Para corregir, actualiza MinimapConfig.roi a [{adj_x}, {adj_y}, {adj_w}, {adj_h}]")
        print(f"  o ejecuta: python tools/calibrate.py{X}")
    else:
        print(f"  Resolución 1920x1080 ✓")

    # 4. Intentar leer posición igualmente
    print(f"\n  Intentando leer posición...")
    pos = _read_pos(fg, None if True else None)  # evita crash si radar falla
    try:
        from src.map_loader import TibiaMapLoader
        loader = TibiaMapLoader(cache_dir=Path("maps"))
        img = loader.get_map_image(cfg.floor)
        if img is None:
            print(f"  {RE}Mapa floor={cfg.floor} no cargado — falta el archivo en maps/{X}")
        else:
            mh, mw = img.shape[:2]
            print(f"  Mapa floor={cfg.floor}: {mw}x{mh} px  ✓")
            from src.minimap_radar import MinimapRadar
            radar = MinimapRadar(loader=loader, config=cfg)
            coord = radar.read(frame)
            if coord:
                print(f"  {G}Posición leída: ({coord.x}, {coord.y}, {coord.z}){X}  ✓")
            else:
                print(f"  {RE}radar.read() devolvió None{X}")
                print(f"  {Y}→ El minimap no coincide con el mapa cargado.{X}")
                print(f"  {Y}→ Revisa output/diag_minimap_roi.png y compáralo con el mapa.{X}")
    except Exception as e:
        print(f"  {RE}Error al probar radar: {e}{X}")

    print(f"\n  Abre la carpeta {B}output/{X} para ver los recortes.\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Grabador de rutas Tibia")
    p.add_argument("--name",     default="ruta",         help="Nombre de la ruta")
    p.add_argument("--output",   default="routes/ruta",  help="Salida (sin extensión)")
    p.add_argument(
        "--source", default="printwindow",
        choices=["printwindow", "mss", "wgc", "dxcam"],
        help="Fuente de captura (default: printwindow)",
    )
    p.add_argument("--window",   default="Proyector",    help="Título ventana (default: Proyector — OBS projector)")
    p.add_argument("--interval", type=float, default=0.5,help="Intervalo auto-grab (s)")
    p.add_argument("--diag",     action="store_true",    help="Modo diagnóstico: vuelca frame y ROI a output/")
    args = p.parse_args()

    if args.diag:
        diag(source=args.source, window=args.window)
        return

    run(
        name=args.name,
        output=Path(args.output),
        source=args.source,
        window=args.window,
        interval=args.interval,
    )


if __name__ == "__main__":
    main()
