"""
debug_capture.py — Captura un frame y guarda imagen con ROIs superpuestos.

Muestra exactamente lo que el bot "ve" y dónde busca HP/MP.
Ejecutar: python debug_capture.py
"""

import json
import sys
from pathlib import Path

import cv2
import mss
import numpy as np

ROOT = Path(__file__).resolve().parent
OUT  = ROOT / "output"
OUT.mkdir(exist_ok=True)

# ── 1. Captura con mss (monitor 2 = OBS projector con Tibia) ────────────────
with mss.mss() as sct:
    mon = sct.monitors[2]   # monitor 2 = secundario (left=1920) donde corre Tibia
    img = sct.grab(mon)
    frame = np.array(img)[:, :, :3].copy()   # BGRA → BGR

print(f"[DBG] Frame capturado: {frame.shape[1]}×{frame.shape[0]} px  "
      f"(monitor 2: {mon})")

# ── 2. Cargar ROIs de hpmp_config.json ───────────────────────────────────────
cfg_path = ROOT / "hpmp_config.json"
if cfg_path.exists():
    cfg = json.loads(cfg_path.read_text())
else:
    print("[DBG] hpmp_config.json no encontrado — usando ROIs de prueba")
    cfg = {}

rois = {
    "hp_bar":      cfg.get("hp_roi",      [12,  52, 769, 13]),
    "mp_bar":      cfg.get("mp_roi",      [788, 52, 768, 13]),
    "hp_text":     cfg.get("hp_text_roi", [484, 333, 1376, 20]),
    "mp_text":     cfg.get("mp_text_roi", [374, 345, 1486, 20]),
}

COLORS = {
    "hp_bar":  (0,   80,  255),   # rojo
    "mp_bar":  (255, 120,  0  ),  # azul
    "hp_text": (0,   200, 255),   # amarillo
    "mp_text": (200, 255,  0  ),  # cyan
}

annotated = frame.copy()
for name, (x, y, w, h) in rois.items():
    color = COLORS[name]
    # Rectángulo completo
    cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
    # Etiqueta arriba
    cv2.putText(annotated, name, (x, max(y - 4, 10)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    # Recorte de la región (para vista ampliada)
    roi_crop = frame[y:y+h, x:x+w]
    print(f"[DBG] ROI '{name}' ({x},{y} {w}×{h}) — "
          f"mean RGB={roi_crop.mean(axis=(0,1)).astype(int).tolist()}")

# ── 3. Guardar imagen completa anotada ───────────────────────────────────────
full_path = OUT / "debug_frame.png"
cv2.imwrite(str(full_path), annotated)
print(f"[OK]  Frame completo guardado: {full_path}")

# ── 4. Guardar recortes ampliados de HP y MP ─────────────────────────────────
for name in ("hp_bar", "mp_bar"):
    x, y, w, h = rois[name]
    # Ampliar ×8 para ver los píxeles
    crop = frame[y:y+h, x:x+w]
    zoomed = cv2.resize(crop, (w * 8, h * 8), interpolation=cv2.INTER_NEAREST)
    crop_path = OUT / f"debug_{name}.png"
    cv2.imwrite(str(crop_path), zoomed)
    print(f"[OK]  Recorte '{name}' (×8) guardado: {crop_path}")

# ── 5. Recorte del top-left para verificar posición de Tibia ────────────────
topleft = frame[0:100, 0:300]
topleft_path = OUT / "debug_topleft.png"
cv2.imwrite(str(topleft_path), topleft)
print(f"[OK]  Top-left (0,0)→(300,100) guardado: {topleft_path}")

# ── 6. Captura y anotacion del minimap ROI ───────────────────────────────────
mm_cfg_path = ROOT / "minimap_config.json"
if mm_cfg_path.exists():
    mm_cfg = json.loads(mm_cfg_path.read_text())
    mx, my, mw, mh = mm_cfg.get("roi", [1740, 27, 175, 127])
    tiles_wide = mm_cfg.get("tiles_wide", 180)

    # Dibujar minimap ROI en la imagen anotada
    cv2.rectangle(annotated, (mx, my), (mx + mw, my + mh), (0, 255, 0), 2)
    cv2.putText(annotated, f"minimap ({mw}x{mh})", (mx, max(my - 4, 10)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)

    # Recorte ampliado del minimap x4
    mm_crop = frame[my:my+mh, mx:mx+mw]
    mm_zoom = cv2.resize(mm_crop, (mw * 4, mh * 4), interpolation=cv2.INTER_NEAREST)

    # Dibujar grilla de tiles (1 tile = mw/tiles_wide pixels * 4 zoom)
    px_per_tile = (mw / tiles_wide) * 4
    if px_per_tile >= 2:
        for tx in range(int(mw * 4 / px_per_tile) + 1):
            xi = int(tx * px_per_tile)
            cv2.line(mm_zoom, (xi, 0), (xi, mh * 4), (50, 50, 50), 1)
        for ty in range(int(mh * 4 / px_per_tile) + 1):
            yi = int(ty * px_per_tile)
            cv2.line(mm_zoom, (0, yi), (mw * 4, yi), (50, 50, 50), 1)

    mm_path = OUT / "debug_minimap.png"
    cv2.imwrite(str(mm_path), mm_zoom)
    print(f"[OK]  Minimap ROI ({mx},{my} {mw}x{mh}) tiles_wide={tiles_wide} guardado: {mm_path}")
    mm_raw = frame[my:my+mh, mx:mx+mw]
    print(f"[DBG] Minimap mean RGB={mm_raw.mean(axis=(0,1)).astype(int).tolist()}")

    # Guardar imagen completa RE-anotada con minimap
    full_path2 = OUT / "debug_frame.png"
    cv2.imwrite(str(full_path2), annotated)

print()
print("Abre las imagenes en output/ para verificar:")
print("  debug_frame.png    — pantalla completa con ROIs marcados (verde=minimap)")
print("  debug_hp_bar.png   — recorte de la barra de HP (x8 zoom)")
print("  debug_mp_bar.png   — recorte de la barra de MP (x8 zoom)")
print("  debug_topleft.png  — esquina top-left de la captura")
print("  debug_minimap.png  — minimap ampliado x4 con grilla de tiles")
