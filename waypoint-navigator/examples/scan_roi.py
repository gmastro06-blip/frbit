"""
Escanea la pantalla para localizar automáticamente dónde muestra
Tibia las coordenadas X,Y,Z y actualiza detector_config.json.

Uso:
    python examples/scan_roi.py                  # captura real de pantalla
    python examples/scan_roi.py --dry-run        # frame sintético, sin mss ni guardado

Opciones:
    --dry-run       Usa un frame negro sintético; no escribe archivos ni lee mss.
                    Útil para probar la lógica del script sin tener Tibia abierto.
    --no-save       Ejecuta el OCR real pero no guarda detector_config.json.
    --width INT     Ancho del frame sintético en dry-run (default: 1920).
    --height INT    Alto del frame sintético en dry-run (default: 1080).
"""
import argparse
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np

from src.character_detector import ImageProcessor, CoordinateOCR, DetectorConfig

CFG_PATH = Path(__file__).parent.parent / "detector_config.json"
OUT_DIR  = Path(__file__).parent.parent / "output"

# ── CLI ─────────────────────────────────────────────────────────────────────
_ap = argparse.ArgumentParser(description="Auto-scan ROI de coordenadas Tibia")
_ap.add_argument("--dry-run",  action="store_true",
                 help="Frame sintético negro; no escribe archivos ni captura pantalla")
_ap.add_argument("--no-save",  action="store_true",
                 help="Ejecuta el OCR real pero no actualiza detector_config.json")
_ap.add_argument("--width",    type=int, default=1920,
                 help="Ancho del frame sintético (solo en --dry-run, default: 1920)")
_ap.add_argument("--height",   type=int, default=1080,
                 help="Alto del frame sintético (solo en --dry-run, default: 1080)")
_args = _ap.parse_args()

proc = ImageProcessor(scale=3)
ocr  = CoordinateOCR(confidence=0.25)

if _args.dry_run:
    print(f"[DRY-RUN] Frame sintético {_args.width}×{_args.height}px — sin mss, sin escritura.")
    full = np.zeros((_args.height, _args.width, 4), dtype=np.uint8)  # BGRA negro
    h_full, w_full = full.shape[:2]
    print(f"Resolución: {w_full}×{h_full}")
else:
    import mss
    print("Capturando pantalla...")
    with mss.mss() as sct:
        full = np.array(sct.grab(sct.monitors[1]))   # BGRA  1080×1920
    h_full, w_full = full.shape[:2]
    print(f"Resolución: {w_full}×{h_full}")
    # Guarda imagen completa para referencia
    cv2.imwrite(str(OUT_DIR / "debug_fullscreen.png"),
                cv2.cvtColor(full, cv2.COLOR_BGRA2BGR))

# Estrategia rápida: busca bloques de texto blanco/claro sobre fondo oscuro
# (formato típico Tibia) con detección de contornos antes de pasar por OCR.
STRIP_W, STRIP_H = 260, 38

# 1. Convertir a grises y umbral para texto claro
gray  = cv2.cvtColor(full, cv2.COLOR_BGRA2GRAY)
_thresh, bw = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

# 2. Dilatación horizontal para unir dígitos de una misma línea
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 4))
dilated = cv2.dilate(bw, kernel)

# 3. Encontrar contornos de bloques de texto
contours, _hier = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

# Filtrar: anchura 60-400px, altura 8-30px
candidates = []
for cnt in contours:
    x, y, w, h = cv2.boundingRect(cnt)
    if 60 <= w <= 400 and 8 <= h <= 30:
        # Expandir un poco el recorte
        x0 = max(0, x - 5)
        y0 = max(0, y - 5)
        x1 = min(full.shape[1], x + w + 5)
        y1 = min(full.shape[0], y + h + 8)
        candidates.append((x0, y0, x1 - x0, y1 - y0))

# Ordenar por posición (top→bottom, left→right de la mitad derecha primero)
candidates.sort(key=lambda c: (c[0] < full.shape[1] // 2, c[1]))

print(f"Contornos de texto candidatos: {len(candidates)}")

# Guardar debug con contornos marcados
if not _args.dry_run:
    dbg = cv2.cvtColor(full, cv2.COLOR_BGRA2BGR)
    for c in candidates[:80]:
        cv2.rectangle(dbg, (c[0], c[1]), (c[0]+c[2], c[1]+c[3]), (0,255,0), 1)
    cv2.imwrite(str(OUT_DIR / "debug_contours.png"), dbg)
    print("  Contornos guardados en debug_contours.png")

found = []
print(f"Aplicando OCR a {min(len(candidates),150)} candidatos...")
for idx, (x0, y0, w, h) in enumerate(candidates[:150]):
    strip = full[y0:y0 + h, x0:x0 + w]
    if strip.size == 0:
        continue
    p = proc.preprocess(strip)
    r = ocr.read(p)
    if r:
        print(f"  ✓ ENCONTRADO en ({x0},{y0})  → {r}")
        found.append((x0, y0, max(w, STRIP_W), max(h, STRIP_H), r))
        if not _args.dry_run:
            tag = f"found_{x0}_{y0}"
            cv2.imwrite(str(OUT_DIR / f"debug_{tag}_raw.png"),
                        cv2.cvtColor(strip, cv2.COLOR_BGRA2BGR))
            cv2.imwrite(str(OUT_DIR / f"debug_{tag}_proc.png"), p)
        break
    if idx % 20 == 0:
        print(f"  ... {idx}/{min(len(candidates),150)}", end="\r")

if not found:
    msg = "[DRY-RUN] " if _args.dry_run else ""
    print(f"\n  {msg}✗ Coordenadas no encontradas en pantalla.")
    if not _args.dry_run:
        print("  Asegúrate de que la ventana de Tibia está visible y el personaje")
        print("  está en el juego (no en menú de login).")
        print("  Ejecuta: python src/calibrator.py --source screen")
else:
    x0, y0, w, h, coord = found[0]
    new_roi = [x0, y0, w, h]
    print(f"\n  ROI detectado: {new_roi}")
    print(f"  Coordenada detectada: {coord}")
    if _args.dry_run or _args.no_save:
        tag = "[DRY-RUN] " if _args.dry_run else "[--no-save] "
        print(f"  {tag}Config NO guardada.")
    else:
        cfg = DetectorConfig.load(CFG_PATH)
        cfg.roi = new_roi
        cfg.save(CFG_PATH)
        print(f"  Config guardada en: {CFG_PATH}")
