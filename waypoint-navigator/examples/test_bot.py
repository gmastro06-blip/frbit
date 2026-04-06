"""
test_bot.py — Diagnóstico completo de todos los módulos del bot
================================================================
Verifica en vivo (o sobre una imagen de prueba) que cada módulo funciona
antes de lanzar el bot completo con auto_walker.py.

Módulos probados:
  1. Conexión OBS (WebSocket o VirtualCam)
  2. HP/MP detector  — lee las barras del cliente
  3. Battle List (CombatManager) — detecta monstruos con template matching
  4. ConditionMonitor  — detecta iconos de condición (veneno, parálisis…)
  5. Input Controller  — verifica que la ventana de Tibia está accesible

Modos:
  --watch          : loop continuo (actualiza pantalla cada segundo)
  --once           : captura una sola vez y guarda debug PNGs
  --image PATH     : usar imagen local en vez de OBS

Salida:
  output/test_bot_debug.png  — frame con overlays de todos los ROI
  output/test_bot_battle.png — recorte de la battle list con detecciones
  output/test_bot_hpmp.png   — recorte de los ROI de HP/MP

Uso:
    cd C:\\Users\\gmast\\Documents\\frbit\\waypoint-navigator
    python examples/test_bot.py --source obs-ws
    python examples/test_bot.py --source obs-ws --watch
    python examples/test_bot.py --image output/screen_capture.png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

import cv2
import numpy as np

# ── Módulos del proyecto ─────────────────────────────────────────────────────
from src.hpmp_detector      import HpMpDetector, HpMpConfig
from src.input_controller   import InputController, find_window

try:
    from src.combat_manager   import CombatManager, CombatConfig, BattleDetector
    _HAS_COMBAT = True
except ImportError:
    _HAS_COMBAT = False
    print("  [WARN] combat_manager no disponible")

try:
    from src.condition_monitor import ConditionMonitor, ConditionConfig, ConditionDetector
    _HAS_COND = True
except ImportError:
    _HAS_COND = False
    print("  [WARN] condition_monitor no disponible")

# ── OBS source helper ───────────────────────────────────────────────────────
def _make_obs_source(source: str, cam_idx: int, host: str, port: int,
                     password: str, scene_src: str):
    """Devuelve una fuente conectada (obs_ws o virtual_cam)."""
    if source == "obs-ws":
        from src.character_detector import OBSWebSocketSource, DetectorConfig
        cfg = DetectorConfig.load()
        cfg.obs_ws_host     = host
        cfg.obs_ws_port     = port
        cfg.obs_ws_password = password
        if scene_src:
            cfg.obs_source = scene_src
        src: OBSWebSocketSource | VirtualCameraSource = OBSWebSocketSource(cfg, capture_width=0)
        src.connect()
        return src
    else:
        from src.character_detector import VirtualCameraSource
        src_vc: VirtualCameraSource = VirtualCameraSource(cam_idx)
        src_vc.connect()
        return src_vc


def _get_frame(source, image_path: str | None) -> np.ndarray | None:
    """Obtiene un frame de OBS o de un archivo local."""
    if image_path:
        img = cv2.imread(image_path)
        if img is None:
            print(f"  [ERR] No se pudo leer la imagen: {image_path}")
        return img
    try:
        return source.get_frame()
    except Exception as exc:
        print(f"  [ERR] No se pudo obtener frame: {exc}")
        return None


# ── Dibujo de overlays ───────────────────────────────────────────────────────
def _draw_roi(frame: np.ndarray, roi: list, color: tuple, label: str) -> None:
    x, y, w, h = roi
    # Escalar si el frame no es 1920×1080
    fh, fw = frame.shape[:2]
    rx, ry = fw / 1920, fh / 1080
    x2 = int(x * rx); y2 = int(y * ry)
    w2 = int(w * rx); h2 = int(h * ry)
    cv2.rectangle(frame, (x2, y2), (x2 + w2, y2 + h2), color, 2)
    cv2.putText(frame, label, (x2 + 2, y2 - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)


# ── Test Individual ─────────────────────────────────────────────────────────
def run_tests(frame: np.ndarray, args) -> dict:
    """Ejecuta todos los tests sobre un frame y devuelve un resumen."""
    results: dict = {
        "hp": None,
        "mp": None,
        "monsters": [],
        "conditions": [],
    }
    out_dir = project_root / "output"
    out_dir.mkdir(exist_ok=True)
    dbg = frame.copy()

    # ── 1) HP/MP ──────────────────────────────────────────────────────────────
    hp_cfg = HpMpConfig.load()
    hp_det = HpMpDetector(hp_cfg)
    try:
        hp, mp = hp_det.read_bars(frame)
        results["hp"] = hp
        results["mp"] = mp
        _draw_roi(dbg, hp_cfg.hp_roi, (0, 80, 255), f"HP {hp}%")
        _draw_roi(dbg, hp_cfg.mp_roi, (255, 100, 0), f"MP {mp}%")
        print(f"  [HP/MP]   HP={hp}%  MP={mp}%")
    except Exception as e:
        results["hp"] = results["mp"] = None
        print(f"  [HP/MP]   ERROR: {e}")

    # ── 2) Battle List / monstruos ────────────────────────────────────────────
    if _HAS_COMBAT:
        cc = CombatConfig.load()
        bd = BattleDetector(cc)
        try:
            detections = bd.detect(frame)
            results["monsters"] = [(name, conf) for _, _, conf, name in detections]
            _draw_roi(dbg, cc.battle_list_roi, (0, 220, 100), f"BATTLE({len(detections)})")
            for cx, cy, conf, name in detections:
                fh, fw = frame.shape[:2]
                rx, ry = fw / cc.ref_width, fh / cc.ref_height
                cxs = int(cx * rx); cys = int(cy * ry)
                cv2.circle(dbg, (cxs, cys), 12, (0, 200, 255), 2)
                cv2.putText(dbg, f"{name}:{conf:.2f}", (cxs + 14, cys),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 255), 1)
            if detections:
                print(f"  [COMBAT]  {len(detections)} monstruo(s) detectado(s):")
                for _, _, conf, name in detections:
                    print(f"            - {name}  conf={conf:.3f}")
            else:
                print(f"  [COMBAT]  Sin monstruos en battle list "
                      f"({len(bd._templates)} templates cargados)")
            # Guardar recorte de battle list
            fh, fw = frame.shape[:2]
            rx2, ry2 = fw / cc.ref_width, fh / cc.ref_height
            bx, by, bw, bh_r = cc.battle_list_roi
            bx2 = int(bx * rx2); by2 = int(by * ry2)
            bw2 = int(bw * rx2); bh2 = int(bh_r * ry2)
            roi_crop = frame[by2:by2+bh2, bx2:bx2+bw2]
            if roi_crop.size > 0:
                cv2.imwrite(str(out_dir / "test_bot_battle.png"), roi_crop)
        except Exception as e:
            results["monsters"] = []
            print(f"  [COMBAT]  ERROR: {e}")
    else:
        pass

    # ── 3) Condiciones ────────────────────────────────────────────────────────
    if _HAS_COND:
        cond_cfg = ConditionConfig.load()
        cond_det = ConditionDetector(cond_cfg)
        try:
            active = cond_det.detect(frame)
            results["conditions"] = list(active)
            _draw_roi(dbg, cond_cfg.condition_icons_roi,
                      (180, 60, 255), f"COND:{','.join(active) if active else 'none'}")
            if active:
                print(f"  [COND]    Condiciones activas: {', '.join(active)}")
            else:
                print(f"  [COND]    Sin condiciones detectadas")
        except Exception as e:
            results["conditions"] = []
            print(f"  [COND]    ERROR: {e}")
    else:
        pass

    # ── 4) Guardar debug ──────────────────────────────────────────────────────
    cv2.imwrite(str(out_dir / "test_bot_debug.png"), dbg)
    # Recorte HP/MP
    fh2, fw2 = frame.shape[:2]
    rx3, ry3 = fw2 / 1920, fh2 / 1080
    hx, hy, hw, hh = hp_cfg.hp_roi
    crop_h = frame[max(0, int(hy*ry3)-5) : int((hy+hh)*ry3)+30,
                   max(0, int(hx*rx3)-5) : int((hx+hw)*rx3)+5]
    if crop_h.size > 0:
        cv2.imwrite(str(out_dir / "test_bot_hpmp.png"), crop_h)

    return results


# ── Resumen en consola ───────────────────────────────────────────────────────
def print_summary(results: dict, elapsed: float) -> None:
    print()
    print("=" * 48)
    print("  RESUMEN DEL DIAGNÓSTICO")
    print("=" * 48)
    hp = results.get("hp")
    mp = results.get("mp")
    monsters = results.get("monsters", [])
    conds = results.get("conditions", [])

    def _ok(val): return "[OK]" if val is not None else "[??]"

    print(f"  HP/MP       : {_ok(hp)} HP={hp}%  MP={mp}%")
    print(f"  Monstruos   : {'[OK]' if monsters else '[  ]'} {len(monsters)} detectados")
    for name, conf in monsters:
        print(f"    - {name:<28} conf={conf:.3f}")
    print(f"  Condiciones : {len(conds)} activas  {conds if conds else '(ninguna)'}")
    print(f"  Tiempo      : {elapsed*1000:.0f}ms")
    print()
    print("  Debug guardado en: output/test_bot_debug.png")
    print("  Battle list  en: output/test_bot_battle.png")
    print("  HP/MP crop   en: output/test_bot_hpmp.png")
    print("=" * 48)


# ── Loop de watch ────────────────────────────────────────────────────────────
def watch_loop(source, args) -> None:
    """Modo --watch: actualiza en consola cada segundo, guarda debug PNGs."""
    print("  [WATCH] Presiona Ctrl+C para salir\n")
    interval = args.interval
    iteration = 0
    try:
        while True:
            iteration += 1
            t0 = time.time()
            frame = _get_frame(source, getattr(args, "image", None))
            if frame is None:
                time.sleep(1.0)
                continue

            print(f"\n--- Iter {iteration}  {time.strftime('%H:%M:%S')} ---")
            results = run_tests(frame, args)
            elapsed = time.time() - t0
            print_summary(results, elapsed)
            remaining = max(0.0, interval - elapsed)
            time.sleep(remaining)
    except KeyboardInterrupt:
        print("\n  [WATCH] Detenido.")


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Diagnóstico de módulos del bot (HP/MP, combat, conditions)"
    )
    ap.add_argument("--source",   default="obs-ws",
                    choices=["obs-ws", "virtual-cam"])
    ap.add_argument("--obs-host", default="localhost")
    ap.add_argument("--obs-port", type=int, default=4455)
    ap.add_argument("--obs-password", default="")
    ap.add_argument("--obs-scene-source", default="")
    ap.add_argument("--cam",      type=int, default=0,
                    help="Índice de la Virtual Camera (solo virtual-cam)")
    ap.add_argument("--image",    default=None,
                    help="Ruta a imagen local (en vez de OBS)")
    ap.add_argument("--watch",    action="store_true",
                    help="Loop continuo (actualiza cada --interval segundos)")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="Segundos entre actualizaciones en modo --watch (default 2)")
    ap.add_argument("--test-input", action="store_true",
                    help="Verificar que la ventana de Tibia es accesible")
    ap.add_argument("--tibia-title", default="Tibia",
                    help="Título ventana Tibia para --test-input")
    args = ap.parse_args()

    print("=" * 48)
    print("  TEST BOT — diagnóstico de módulos")
    print("=" * 48)

    # ── Test de ventana Tibia ─────────────────────────────────────────────
    if args.test_input:
        windows = []
        try:
            from src.input_controller import list_windows
            windows = list_windows()
        except Exception:
            pass
        tibia_found = any(args.tibia_title.lower() in w.title.lower()
                          for w in windows)
        if tibia_found:
            w_match = next(w for w in windows
                           if args.tibia_title.lower() in w.title.lower())
            print(f"  [INPUT]   [OK] Ventana '{w_match.title}' "
                  f"HWND={w_match.hwnd:#010x}")
        else:
            print(f"  [INPUT]   [??] Ventana '{args.tibia_title}' no encontrada")
            if windows:
                print(f"  [INPUT]   Ventanas disponibles:")
                for w in windows[:8]:
                    print(f"    {w.hwnd:#010x}  {w.title!r}")
        print()

    # ── Conectar fuente ───────────────────────────────────────────────────
    source = None
    if not args.image:
        print(f"  Conectando fuente: {args.source}…")
        try:
            source = _make_obs_source(
                args.source, args.cam,
                args.obs_host, args.obs_port,
                args.obs_password, args.obs_scene_source,
            )
            print(f"  [OBS]     [OK] Conectado")
        except Exception as e:
            print(f"  [OBS]     [ERR] {e}")
            if not args.image:
                print("  Usa --image <ruta> para probar con un screenshot guardado.")
                sys.exit(1)
    else:
        print(f"  Usando imagen local: {args.image}")
    print()

    # ── Modo ──────────────────────────────────────────────────────────────
    if args.watch:
        watch_loop(source, args)
    else:
        t0 = time.time()
        frame = _get_frame(source, args.image)
        if frame is None:
            print("  [ERR] Sin frame — verifica la conexión OBS.")
            sys.exit(1)
        print(f"  Frame: {frame.shape[1]}x{frame.shape[0]}  dtype={frame.dtype}")
        print()
        results = run_tests(frame, args)
        elapsed = time.time() - t0
        print_summary(results, elapsed)


if __name__ == "__main__":
    main()
