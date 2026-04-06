"""
diag_hpmp.py — Calibrar y diagnosticar posición de barras HP/MP
================================================================
Captura un frame de OBS, dibuja los ROIs de HP y MP encima,
y guarda la imagen para verificar visualmente que apuntan
a las barras correctas.

Uso:
    python examples/diag_hpmp.py --source obs-ws --obs-scene-source "Tibia_Fuente"
    python examples/diag_hpmp.py --source obs-ws --save-roi "1620,440,90,10,1620,452,90,10"

Salida:
    output/diag_hpmp.png   ← imagen con ROIs dibujados
    Consola: lecturas HP/MP actuales

Si los rectángulos NO están sobre las barras, edita hpmp_config.json
o usa --save-roi para guardar la nueva configuración.
"""
import sys
import os
import argparse
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import cv2

from src.character_detector import DetectorConfig, OBSWebSocketSource
from src.hpmp_detector import HpMpDetector, HpMpConfig

_REF_W = 1920
_REF_H = 1080


def get_frame(args) -> np.ndarray | None:
    """Captura un frame de OBS WebSocket."""
    cfg = DetectorConfig(
        obs_ws_host=args.obs_host,
        obs_ws_port=args.obs_port,
        obs_ws_password=args.obs_password,
    )
    if args.obs_scene_source:
        cfg.obs_source = args.obs_scene_source
    src = OBSWebSocketSource(cfg, capture_width=0)   # resolución real, sin downscale
    try:
        src.connect()
        print("  OBS conectado. Esperando frame…")
        for _ in range(10):
            frame = src.get_frame()
            if frame is not None and frame.size > 0:
                return frame
            time.sleep(0.3)
        return None
    finally:
        src.disconnect()


def _draw_roi(img: np.ndarray, roi, color, label: str) -> None:
    fh, fw = img.shape[:2]
    sx, sy = fw / _REF_W, fh / _REF_H
    x, y, w, h = roi
    x0, y0 = int(x * sx), int(y * sy)
    x1, y1 = int((x + w) * sx), int((y + h) * sy)
    # Ampliar para visibilidad (bordes ±3px)
    cv2.rectangle(img, (x0 - 3, y0 - 3), (x1 + 3, y1 + 3), color, 2)
    cv2.putText(img, label, (x0, max(14, y0 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)


def main() -> None:
    ap = argparse.ArgumentParser(description="Diagnóstico de posición HP/MP ROI")
    ap.add_argument("--source",           default="obs-ws", choices=["obs-ws"])
    ap.add_argument("--obs-host",         default="localhost")
    ap.add_argument("--obs-port",         type=int, default=4455)
    ap.add_argument("--obs-password",     default="")
    ap.add_argument("--obs-scene-source", default="")
    ap.add_argument("--output",           default="output/diag_hpmp.png",
                    help="Ruta donde guardar la imagen de diagnóstico")
    ap.add_argument("--save-roi",         default="",
                    help="Guardar nueva config: 'hp_x,hp_y,hp_w,hp_h,mp_x,mp_y,mp_w,mp_h'")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    # Guardar nueva config si se pide
    if args.save_roi:
        vals = [int(v.strip()) for v in args.save_roi.split(",")]
        if len(vals) != 8:
            print("ERROR: --save-roi necesita 8 valores: hp_x,hp_y,hp_w,hp_h,mp_x,mp_y,mp_w,mp_h")
            sys.exit(1)
        cfg = HpMpConfig(
            hp_roi=vals[0:4],
            mp_roi=vals[4:8],
        )
        cfg.save()
        print(f"  Guardado en hpmp_config.json:  HP={vals[:4]}  MP={vals[4:]}")
        return

    # Capturar frame
    print("Capturando frame de OBS…")
    frame = get_frame(args)
    if frame is None:
        print("ERROR: no se pudo obtener frame de OBS")
        sys.exit(1)

    fh, fw = frame.shape[:2]
    print(f"  Frame capturado: {fw}x{fh}")

    # Leer HP/MP
    det = HpMpDetector()
    hp, mp = det.read_bars(frame)
    print(f"  Lectura HP: {hp}%   MP: {mp}%")

    # Dibujar ROIs en copia del frame
    vis = frame.copy()
    _draw_roi(vis, det._cfg.hp_roi, (0, 64, 255), f"HP={hp}%")
    _draw_roi(vis, det._cfg.mp_roi, (255, 64,  0), f"MP={mp}%")

    # Añadir info de resolución
    cv2.putText(vis, f"{fw}x{fh}", (10, fh - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    # Info en consola sobre las posiciones escaladas
    sx, sy = fw / _REF_W, fh / _REF_H
    hp_roi = det._cfg.hp_roi
    mp_roi = det._cfg.mp_roi
    print()
    print("  ROIs en resolución 1920×1080 (config):")
    print(f"    HP: x={hp_roi[0]} y={hp_roi[1]} w={hp_roi[2]} h={hp_roi[3]}")
    print(f"    MP: x={mp_roi[0]} y={mp_roi[1]} w={mp_roi[2]} h={mp_roi[3]}")
    print()
    print(f"  ROIs escalados al frame {fw}x{fh}:")
    print(f"    HP: x={int(hp_roi[0]*sx)} y={int(hp_roi[1]*sy)}"
          f" x2={int((hp_roi[0]+hp_roi[2])*sx)} y2={int((hp_roi[1]+hp_roi[3])*sy)}"
          f"  ({int(hp_roi[2]*sx)}x{int(hp_roi[3]*sy)}px)")
    print(f"    MP: x={int(mp_roi[0]*sx)} y={int(mp_roi[1]*sy)}"
          f" x2={int((mp_roi[0]+mp_roi[2])*sx)} y2={int((mp_roi[1]+mp_roi[3])*sy)}"
          f"  ({int(mp_roi[2]*sx)}x{int(mp_roi[3]*sy)}px)")

    # Guardar imagen
    cv2.imwrite(args.output, vis)
    print()
    print(f"  Imagen guardada: {args.output}")
    print()
    print("  INSTRUCCIONES:")
    print("  1. Abre output/diag_hpmp.png")
    print("  2. Verifica que los rectángulos rojo (HP) y azul (MP) están sobre las barras")
    print("  3. Si no coinciden, mide la posición con un visor de imagen y ejecuta:")
    print("     python examples/diag_hpmp.py --save-roi hp_x,hp_y,hp_w,hp_h,mp_x,mp_y,mp_w,mp_h")
    print()
    print("  EJEMPLO para ajustar:")
    print("     python examples/diag_hpmp.py --save-roi 1620,440,90,10,1620,452,90,10")


if __name__ == "__main__":
    main()
