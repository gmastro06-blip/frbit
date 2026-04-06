"""
calibrate_viewport.py — Calibra viewport_center y tile_size_px para depot_manager
==================================================================================
Cómo usar:
  1. Abre Tibia en el monitor 2 con el personaje en pie (no en combate)
  2. Corre: python -X utf8 calibrate_viewport.py
  3. Sigue las instrucciones en pantalla — solo mueves el ratón a dos posiciones
  4. El script actualiza depot_config.json automáticamente
"""

import json
import sys
import time
from pathlib import Path

try:
    import win32api
except ImportError:
    print("[ERROR] Necesitas pywin32: pip install pywin32")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
DEPOT_CFG = ROOT / "depot_config.json"

# El monitor 2 empieza en x=1920 (monitor 1 = 0..1919)
MONITOR2_LEFT = 1920
MONITOR2_TOP  = 0

COUNTDOWN = 4  # segundos para cada captura


def countdown(msg: str, secs: int = COUNTDOWN) -> tuple[int, int]:
    """Muestra countdown, luego captura posición del ratón.
    Devuelve (x, y) relativo al monitor 2."""
    print(f"\n{msg}")
    for i in range(secs, 0, -1):
        x, y = win32api.GetCursorPos()
        rel_x = x - MONITOR2_LEFT
        rel_y = y - MONITOR2_TOP
        print(f"  {i}s — ratón en pantalla=({x},{y})  relativo monitor2=({rel_x},{rel_y})", end="\r")
        time.sleep(1.0)
    x, y = win32api.GetCursorPos()
    rel_x = x - MONITOR2_LEFT
    rel_y = y - MONITOR2_TOP
    print(f"  ✓ Capturado: pantalla=({x},{y})  relativo monitor2=({rel_x},{rel_y})          ")
    return rel_x, rel_y


print("""
╔═══════════════════════════════════════════════════════╗
║         CALIBRACIÓN DE VIEWPORT — Depot Manager       ║
╚═══════════════════════════════════════════════════════╝

Necesitamos 2 puntos en el juego:

  PUNTO 1 — El centro de tu personaje en pantalla
  PUNTO 2 — El centro del tile exactamente 3 tiles AL NORTE de tu personaje
             (en Tibia: y decrece al ir norte, así que 3 tiles arriba visualmente)

Tip para Punto 2: cuenta 3 tiles hacia arriba desde tu personaje.
En Tibia con zoom estándar cada tile mide ~32-64 px.

Cuando el script diga "MUEVE EL RATON", tienes 4 segundos.
""")

input("Pulsa ENTER cuando Tibia esté visible en monitor 2 y tu personaje esté parado...")

# ── PUNTO 1: centro del personaje ────────────────────────────────────────────
cx, cy = countdown(
    "PUNTO 1 — Mueve el ratón al CENTRO DE TU PERSONAJE y espera...",
)

# ── PUNTO 2: tile 3 al norte ──────────────────────────────────────────────────
nx, ny = countdown(
    "PUNTO 2 — Mueve el ratón al centro del tile 3 tiles AL NORTE (arriba) de tu personaje y espera...",
)

# ── Calcular tile_size ────────────────────────────────────────────────────────
# Los 3 tiles al norte deberían estar ~3*tile_size pixels más arriba (menor y)
dy = cy - ny   # diferencia vertical (positivo = ny está más arriba)
if dy <= 0:
    print(f"\n[AVISO] El punto 2 (y={ny}) no está más arriba que el punto 1 (y={cy}).")
    print("  Asegúrate de poner el ratón en un tile AL NORTE (ARRIBA) del personaje.")
    print("  Usando tile_size_px=32 por defecto.")
    tile_size = 32
else:
    tile_size = round(dy / 3)
    print(f"\n  Diferencia vertical: {dy}px / 3 tiles = {tile_size}px por tile")

print(f"\n  viewport_center = ({cx}, {cy})")
print(f"  tile_size_px    = {tile_size}")

# ── Actualizar depot_config.json ──────────────────────────────────────────────
if DEPOT_CFG.exists():
    with open(DEPOT_CFG, encoding="utf-8") as f:
        cfg = json.load(f)
else:
    cfg = {}

cfg["viewport_center"] = [cx, cy]
cfg["tile_size_px"]     = tile_size

with open(DEPOT_CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)

print(f"\n✓ depot_config.json actualizado con viewport_center=[{cx},{cy}] y tile_size_px={tile_size}")
print("\nSiguiente paso: corre run_depot_test.py y comprueba en los logs que el clic al chest es correcto.")
print("  El log dirá: stow_all: clic derecho en chest (32348,32222) → px=(X,Y)")
print("  Ese (X,Y) debería ser el tile del chest visible en pantalla.")
