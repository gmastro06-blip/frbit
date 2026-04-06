"""
Calibrador de ROI — multi-modo para todos los detectores
---------------------------------------------------------
Modos disponibles:
  coord        Coordenadas OCR del minimapa  → detector_config.json
  hp           Barra de HP (roja)            → hpmp_config.json
  mp           Barra de MP (azul)            → hpmp_config.json
  minimap      Widget del minimapa           → minimap_config.json
  battle-list  Panel de Battle List          → combat_config.json
  all          Recorre todos los modos en secuencia

Fuentes de captura (--source):
  mss          Captura directa de la ventana Tibia (recomendado, sin OBS)
  virtual-cam  OBS Virtual Camera (requiere OBS con Virtual Camera activa)
  obs-ws       OBS WebSocket (requiere obs-websocket plugin)
  screen       Monitor completo con mss

Instrucciones de uso:
  1. Abre Tibia (o activa OBS Virtual Camera si usas --source virtual-cam)
  2. Ejecuta:  python src/calibrator.py --source mss --mode all
  3. Se abre la ventana con el frame de Tibia
  4. Para cada ROI: dibuja el rectángulo y pulsa S para guardar
  5. R = reintentar el mismo ROI   Q = saltar al siguiente / salir
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np

from src.character_detector import (
    DetectorConfig,
    MSSScreenSource,
    OBSWebSocketSource,
    VirtualCameraSource,
    WGCSource,
    CONFIG_FILE,
    ImageProcessor,
)
from src.hpmp_detector import HpMpConfig, HPMP_CONFIG_FILE
from src.minimap_radar import MinimapConfig, MINIMAP_CONFIG_FILE

# ---------------------------------------------------------------------------
_rect_start: Optional[tuple[int, int]] = None
_rect_end:   Optional[tuple[int, int]] = None
_drawing = False


def _mouse_cb(event: int, x: int, y: int, flags: int, param: object) -> None:
    global _rect_start, _rect_end, _drawing
    if event == cv2.EVENT_LBUTTONDOWN:
        _rect_start = (x, y)
        _rect_end   = (x, y)
        _drawing    = True
    elif event == cv2.EVENT_MOUSEMOVE and _drawing:
        _rect_end = (x, y)
    elif event == cv2.EVENT_LBUTTONUP:
        _rect_end = (x, y)
        _drawing  = False


def _capture_frame(
    source: str = "virtual-cam",
    obs_source_name: str = "",
    window_title: str = "",
) -> Optional[np.ndarray]:
    """Capture one frame from the given source. Returns BGR array or None."""
    cfg = DetectorConfig.load()
    cfg.obs_source = obs_source_name
    print(f"  Capturando frame desde '{source}' …")
    if source == "obs-ws":
        src: Any = OBSWebSocketSource(cfg)
        src.connect()
    elif source == "virtual-cam":
        src = VirtualCameraSource(cfg.obs_cam_index)
        src.connect()
        # Warm-up: discard first few frames (camera buffers stale data on open)
        for _ in range(5):
            src.get_frame()
    elif source == "mss":
        title = window_title or "Tibia"
        src = WGCSource(title)
        src.connect()
    elif source == "wgc":
        title = window_title or "Tibia"
        src = WGCSource(title)
        src.connect()
    else:
        src = MSSScreenSource()

    frame: Optional[np.ndarray] = src.get_frame()
    try:
        src.disconnect()
    except Exception:
        pass
    return frame


def _draw_existing_roi(
    canvas: np.ndarray,
    roi: Optional[List[int]],
    scale: float,
    color: Tuple[int, int, int],
    label: str,
) -> None:
    """Draw the currently saved ROI on the canvas as a reference rectangle."""
    if roi is None or len(roi) < 4:
        return
    x, y, w, h = roi
    x0 = int(x * scale)
    y0 = int(y * scale)
    x1 = int((x + w) * scale)
    y1 = int((y + h) * scale)
    cv2.rectangle(canvas, (x0, y0), (x1, y1), color, 1)
    cv2.putText(canvas, f"[{label}]", (x0 + 2, max(y0 - 4, 12)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1, cv2.LINE_AA)


def calibrate_roi(
    frame: np.ndarray,
    title: str,
    instructions: str,
    on_save: Callable[[int, int, int, int], None],
    existing_roi: Optional[List[int]] = None,
    min_w: int = 5,
    min_h: int = 3,
) -> bool:
    """
    Generic ROI calibration dialog.

    Displays *frame* in a window, lets the user draw a rectangle,
    calls *on_save(x, y, w, h)* with original-scale coordinates.

    Returns True if saved, False if skipped (Q).
    """
    global _rect_start, _rect_end, _drawing
    _rect_start = None
    _rect_end   = None
    _drawing    = False

    max_w, max_h = 1280, 720
    h_orig, w_orig = frame.shape[:2]
    scale = min(max_w / w_orig, max_h / h_orig, 1.0)
    disp = cv2.resize(frame, (int(w_orig * scale), int(h_orig * scale))) if scale < 1.0 else frame.copy()

    print(f"\n{'─'*60}")
    print(f"  ROI: {title}")
    print(f"  {instructions}")
    print(f"  S = guardar   R = reset   Q = saltar / salir")
    print(f"{'─'*60}")

    win = f"Calibrador — {title}"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, _mouse_cb)

    saved = False
    while True:
        canvas = disp.copy()

        # Show existing ROI as reference (dim cyan)
        _draw_existing_roi(canvas, existing_roi, scale, (0, 180, 180), "actual")

        # Show new rectangle being drawn
        if _rect_start and _rect_end:
            cv2.rectangle(canvas, _rect_start, _rect_end, (0, 255, 0), 2)

        hint = f"{title} | S=guardar  R=reset  Q=saltar"
        txt_y = canvas.shape[0] - 10
        cv2.putText(canvas, hint, (10, txt_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 220, 255), 1, cv2.LINE_AA)
        cv2.imshow(win, canvas)
        key = cv2.waitKey(30) & 0xFF

        if key == ord("q"):
            print(f"  ↩ Saltando '{title}' sin guardar.")
            break

        elif key == ord("r"):
            _rect_start = None
            _rect_end   = None

        elif key == ord("s"):
            if not _rect_start or not _rect_end:
                print("  Dibuja un rectángulo primero.")
                continue
            x0 = min(_rect_start[0], _rect_end[0])
            y0 = min(_rect_start[1], _rect_end[1])
            x1 = max(_rect_start[0], _rect_end[0])
            y1 = max(_rect_start[1], _rect_end[1])
            if (x1 - x0) < min_w or (y1 - y0) < min_h:
                print(f"  Rectángulo demasiado pequeño (min {min_w}×{min_h}px), inténtalo de nuevo.")
                continue

            rx = int(x0 / scale)
            ry = int(y0 / scale)
            rw = int((x1 - x0) / scale)
            rh = int((y1 - y0) / scale)
            on_save(rx, ry, rw, rh)
            print(f"  ✓ Guardado: x={rx} y={ry} w={rw} h={rh}")
            saved = True
            break

    cv2.destroyWindow(win)
    return saved


# ---------------------------------------------------------------------------
# Mode-specific calibration functions
# ---------------------------------------------------------------------------

def calibrate_coord(frame: np.ndarray) -> bool:
    """Calibrate the coordinate OCR ROI (CharacterDetector)."""
    cfg = DetectorConfig.load()

    def save(x: int, y: int, w: int, h: int) -> None:
        cfg.roi = [x, y, w, h]
        cfg.save()
        print(f"  → Guardado en {CONFIG_FILE}")
        # Preview
        processor = ImageProcessor()
        roi_crop  = frame[y: y + h, x: x + w]
        processed = processor.preprocess(roi_crop)
        processor.debug_save(processed, "debug_roi_coord.png")
        cv2.imshow("ROI coord (lo que ve el OCR)", processed)
        cv2.waitKey(1500)
        cv2.destroyWindow("ROI coord (lo que ve el OCR)")

    return calibrate_roi(
        frame=frame,
        title="Coordenadas OCR (texto del minimapa)",
        instructions="Arrastra sobre el texto de coordenadas, p.ej. '32369, 32241, 7'",
        on_save=save,
        existing_roi=cfg.roi,
        min_w=40,
        min_h=8,
    )


def calibrate_hp(frame: np.ndarray) -> bool:
    """Calibrate the HP bar ROI."""
    cfg = HpMpConfig.load()

    def save(x: int, y: int, w: int, h: int) -> None:
        cfg.hp_roi = [x, y, w, h]
        cfg.save()
        print(f"  → Guardado en {HPMP_CONFIG_FILE}")

    return calibrate_roi(
        frame=frame,
        title="Barra HP (roja)",
        instructions="Arrastra sobre la barra ROJA de HP en el panel de stats",
        on_save=save,
        existing_roi=cfg.hp_roi,
        min_w=20,
        min_h=3,
    )


def calibrate_mp(frame: np.ndarray) -> bool:
    """Calibrate the MP bar ROI."""
    cfg = HpMpConfig.load()

    def save(x: int, y: int, w: int, h: int) -> None:
        cfg.mp_roi = [x, y, w, h]
        cfg.save()
        print(f"  → Guardado en {HPMP_CONFIG_FILE}")

    return calibrate_roi(
        frame=frame,
        title="Barra MP (azul)",
        instructions="Arrastra sobre la barra AZUL de MP en el panel de stats",
        on_save=save,
        existing_roi=cfg.mp_roi,
        min_w=20,
        min_h=3,
    )


def calibrate_minimap(frame: np.ndarray) -> bool:
    """Calibrate the minimap widget ROI."""
    cfg = MinimapConfig.load()

    def save(x: int, y: int, w: int, h: int) -> None:
        cfg.roi = [x, y, w, h]
        cfg.save()
        print(f"  → Guardado en {MINIMAP_CONFIG_FILE}")

    return calibrate_roi(
        frame=frame,
        title="Widget del Minimapa",
        instructions="Arrastra sobre el círculo del minimapa (panel derecho de Tibia)",
        on_save=save,
        existing_roi=cfg.roi,
        min_w=40,
        min_h=40,
    )


def calibrate_battle_list(frame: np.ndarray) -> bool:
    """Calibrate the Battle List panel ROI."""
    try:
        from src.combat_manager import CombatConfig, COMBAT_CONFIG_FILE as _CF
        cfg = CombatConfig.load()
        _config_file = _CF
    except Exception:
        print("  ⚠ combat_manager no disponible — saltando battle-list")
        return False

    def save(x: int, y: int, w: int, h: int) -> None:
        cfg.battle_list_roi = [x, y, w, h]
        cfg.save()
        print(f"  → Guardado en {_config_file}")

    return calibrate_roi(
        frame=frame,
        title="Panel Battle List",
        instructions="Arrastra sobre el panel de Battle List (lista de monstruos atacados)",
        on_save=save,
        existing_roi=cfg.battle_list_roi,
        min_w=50,
        min_h=50,
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

_ALL_MODES = ["coord", "hp", "mp", "minimap", "battle-list"]
_MODE_FNS  = {
    "coord":        calibrate_coord,
    "hp":           calibrate_hp,
    "mp":           calibrate_mp,
    "minimap":      calibrate_minimap,
    "battle-list":  calibrate_battle_list,
}


def list_modes() -> List[str]:
    """Return a copy of the available calibration mode names."""
    return list(_ALL_MODES)


def validate_roi(roi: object) -> bool:
    """Return True if *roi* is a valid ``[x, y, w, h]`` list.

    Rules checked:
      * Must be a list (or tuple) of exactly 4 items.
      * All items must be non-negative integers.
      * Width and height (indices 2 and 3) must be at least 1.
    """
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        return False
    try:
        x, y, w, h = (int(v) for v in roi)
    except (TypeError, ValueError):
        return False
    return x >= 0 and y >= 0 and w >= 1 and h >= 1


def mode_exists(mode: str) -> bool:
    """Return True if *mode* is a recognised calibration mode name."""
    return mode in _MODE_FNS


def roi_area(roi: object) -> int:
    """Return the pixel area (w * h) of a valid *roi*, or 0 if invalid."""
    if not validate_roi(roi):
        return 0
    assert isinstance(roi, (list, tuple))
    _, _, w, h = (int(v) for v in roi)
    return w * h


def roi_aspect_ratio(roi: object) -> float:
    """Return the aspect ratio (w / h) of a valid *roi*, or 0.0 if invalid.

    A ratio > 1.0 means wider than tall; < 1.0 means taller than wide.
    """
    if not validate_roi(roi):
        return 0.0
    assert isinstance(roi, (list, tuple))
    _, _, w, h = (int(v) for v in roi)
    return w / h


def roi_center(roi: object) -> Tuple[int, int]:
    """Return the ``(cx, cy)`` pixel centre of a valid *roi*, or ``(0, 0)`` if invalid."""
    if not validate_roi(roi):
        return (0, 0)
    assert isinstance(roi, (list, tuple))
    x, y, w, h = (int(v) for v in roi)
    return (x + w // 2, y + h // 2)


def roi_overlaps(roi_a: object, roi_b: object) -> bool:
    """Return ``True`` when *roi_a* and *roi_b* have a non-empty intersection.

    Two ROIs that only share an edge (zero-area intersection) are considered
    *not* overlapping.  Returns ``False`` when either ROI is invalid.
    """
    if not validate_roi(roi_a) or not validate_roi(roi_b):
        return False
    assert isinstance(roi_a, (list, tuple)) and isinstance(roi_b, (list, tuple))
    ax, ay, aw, ah = (int(v) for v in roi_a)
    bx, by, bw, bh = (int(v) for v in roi_b)
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def calibrate(
    source: str = "virtual-cam",
    obs_source_name: str = "",
    mode: str = "coord",
    window_title: str = "",
) -> None:
    """Main calibration entry point."""
    frame = _capture_frame(source, obs_source_name, window_title=window_title)
    if frame is None:
        print("ERROR: No se pudo capturar ningún frame.")
        print("Verifica que OBS Virtual Camera esté activa o que --source sea correcto.")
        return

    h, w = frame.shape[:2]
    print(f"  Frame: {w}×{h}px")

    modes = _ALL_MODES if mode == "all" else [mode]
    completed = 0
    for m in modes:
        fn = _MODE_FNS.get(m)
        if fn is None:
            print(f"  ⚠ Modo '{m}' desconocido — saltando")
            continue
        result = fn(frame)
        if result:
            completed += 1

    cv2.destroyAllWindows()
    print(f"\n  Calibración completada: {completed}/{len(modes)} ROIs guardados.")


# ---------------------------------------------------------------------------
# Bounds validation
# ---------------------------------------------------------------------------

_STANDARD_PRESETS: dict[str, dict[str, list[int]]] = {
    "1920x1080": {
        "coord":       [75, 22, 180, 15],
        "hp":          [14, 356, 134, 6],
        "mp":          [14, 370, 134, 6],
        "minimap":     [1628, 22, 106, 109],
        "battle-list": [1569, 444, 162, 229],
    },
}


def validate_roi_bounds(
    roi: list[int],
    frame_w: int = 1920,
    frame_h: int = 1080,
) -> list[str]:
    """Return a list of warning strings if *roi* is suspicious.

    Checks:
      - ROI must be inside [0..frame_w, 0..frame_h].
      - Width and height must be at least 3 px.
      - Area must be < 50 % of frame (prevents accidental full-screen).
    """
    warnings: list[str] = []
    if not validate_roi(roi):
        warnings.append("ROI inválido (debe ser [x, y, w, h] con valores >= 0)")
        return warnings
    x, y, w, h = roi
    if x + w > frame_w:
        warnings.append(f"ROI excede el ancho del frame ({x}+{w} > {frame_w})")
    if y + h > frame_h:
        warnings.append(f"ROI excede el alto del frame ({y}+{h} > {frame_h})")
    if w < 3 or h < 3:
        warnings.append(f"ROI demasiado pequeño ({w}×{h} px)")
    area_pct = (w * h) / (frame_w * frame_h) * 100
    if area_pct > 50:
        warnings.append(f"ROI ocupa {area_pct:.0f}% del frame — posible error")
    return warnings


def calibrate_headless(
    roi_overrides: dict[str, list[int]],
    frame_w: int = 1920,
    frame_h: int = 1080,
) -> bool:
    """Apply ROI values directly to config files without GUI.

    *roi_overrides* maps mode names ("hp", "mp", "minimap", etc.) to
    ``[x, y, w, h]`` lists.  Returns True if all saved successfully.
    """
    ok = True
    for mode_name, roi in roi_overrides.items():
        warns = validate_roi_bounds(roi, frame_w, frame_h)
        if warns:
            for warn_msg in warns:
                print(f"  ⚠ {mode_name}: {warn_msg}")
            ok = False
            continue

        x, y, w, h = roi
        if mode_name == "coord":
            cfg = DetectorConfig.load()
            cfg.roi = [x, y, w, h]
            cfg.save()
        elif mode_name == "hp":
            cfg_hp = HpMpConfig.load()
            cfg_hp.hp_roi = [x, y, w, h]
            cfg_hp.save()
        elif mode_name == "mp":
            cfg_mp = HpMpConfig.load()
            cfg_mp.mp_roi = [x, y, w, h]
            cfg_mp.save()
        elif mode_name == "minimap":
            cfg_mm = MinimapConfig.load()
            cfg_mm.roi = [x, y, w, h]
            cfg_mm.save()
        elif mode_name == "battle-list":
            try:
                from src.combat_manager import CombatConfig as _CC
                cfg_bl = _CC.load()
                cfg_bl.battle_list_roi = [x, y, w, h]
                cfg_bl.save()
            except Exception:
                print(f"  ⚠ battle-list: combat_manager no disponible")
                ok = False
                continue
        else:
            print(f"  ⚠ Modo desconocido: {mode_name}")
            ok = False
            continue

        print(f"  ✓ {mode_name}: roi=[{x}, {y}, {w}, {h}] guardado")
    return ok


def apply_preset(resolution: str = "1920x1080") -> bool:
    """Apply standard ROI values for the given *resolution*.

    Returns True if all saved.  Raises ``ValueError`` for unknown resolutions.
    """
    preset = _STANDARD_PRESETS.get(resolution)
    if preset is None:
        raise ValueError(
            f"Resolución '{resolution}' no soportada. "
            f"Disponibles: {list(_STANDARD_PRESETS.keys())}"
        )
    print(f"  Aplicando preset {resolution} …")
    return calibrate_headless(preset)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Calibrador multi-ROI para Navigator")
    ap.add_argument(
        "--source", default="mss",
        choices=["mss", "virtual-cam", "obs-ws", "screen", "wgc"],
        help="Fuente de captura (default: mss → ventana Tibia directa)",
    )
    ap.add_argument(
        "--obs-source", default="",
        help="Nombre de la fuente OBS (solo para --source obs-ws)",
    )
    ap.add_argument(
        "--window", default="",
        help="Título (o fragmento) de la ventana a capturar (default: 'Tibia')",
    )
    ap.add_argument(
        "--mode", default="all",
        choices=["coord", "hp", "mp", "minimap", "battle-list", "all"],
        help=(
            "ROI a calibrar: "
            "coord=coordenadas OCR  hp=barra HP  mp=barra MP  "
            "minimap=minimapa  battle-list=panel de combate  "
            "all=todos en secuencia (default)"
        ),
    )
    ap.add_argument(
        "--roi", default="",
        help=(
            "Modo headless: establece ROI directamente sin GUI. "
            "Formato: 'x,y,w,h'. Requiere --mode distinto de 'all'. "
            "Ej: --mode hp --roi 14,356,134,6"
        ),
    )
    ap.add_argument(
        "--preset", default="",
        choices=["", "1920x1080"],
        help="Aplica ROIs estándar para la resolución indicada (sin GUI).",
    )
    args = ap.parse_args()

    if args.preset:
        apply_preset(args.preset)
    elif args.roi:
        if args.mode == "all":
            print("ERROR: --roi requiere --mode específico (no 'all').")
            sys.exit(1)
        parts = args.roi.split(",")
        if len(parts) != 4:
            print("ERROR: --roi debe tener formato x,y,w,h")
            sys.exit(1)
        try:
            roi_vals = [int(p.strip()) for p in parts]
        except ValueError:
            print("ERROR: --roi valores deben ser enteros")
            sys.exit(1)
        calibrate_headless({args.mode: roi_vals})
    else:
        calibrate(source=args.source, obs_source_name=args.obs_source, mode=args.mode,
                 window_title=args.window)
