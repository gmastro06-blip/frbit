"""
run_phase1.py — Fase 1: Verificación de combat + detección OCR (sin ruta, sin loot)
======================================================================================
Qué hace:
  - Conecta al cliente Tibia (ventana "Tibia")
  - Captura frames via dxcam (GPU DXGI, más rápido y sin foco)
  - Arranca SOLO el CombatManager con OCR
  - Imprime en tiempo real: detecciones de monstruos, kills, HP/MP, spells lanzados
  - Sin movimiento, sin loot, sin depot

Cómo usar:
  1. Abre Tibia y entra al personaje
  2. Párate cerca de los monstruos objetivo (ratas/wasps en Thais)
  3. python run_phase1.py
  4. Observa el log. Ctrl+C para detener.

Verifica que:
  [C] → monstruos detectados por OCR (Cave Rat, Wasp, etc.)
  [C] ☠ Kill confirmado → la detección de kill funciona
  [H] → healer disparándose cuando HP baja
  Spells (F7/F8/F9/F10) → se lanzan correctamente
"""

import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Optional

# Asegurarse de que el directorio raíz está en el path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.session import BotSession, SessionConfig

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("output/phase1.log", encoding="utf-8"),
    ],
)

print("""
+------------------------------------------------------+
|         FASE 1 -- Combat + OCR detection only        |
|  Sin ruta - Sin loot - Solo deteccion y ataque       |
|  Ctrl+C para detener                                 |
+------------------------------------------------------+
""")

# ── Configuración Fase 1 ─────────────────────────────────────────────────────
cfg = SessionConfig(
    # --- Ventana / input ---
    target_window   = "Tibia",          # fragmento del título de la ventana
    input_method    = "postmessage",    # background — sin necesidad de foco
    start_delay     = 5.0,              # 5s para que hagas clic en Tibia antes de empezar

    # --- Frame source ---
    # mss captura el monitor completo (mismas coords absolutas que dxcam pantalla-completa).
    # No usa DXGI/OutputDuplication → no cuelga si el proceso anterior fue forzado a cerrar.
    # hpmp_config.json calibrado con ROIs absolutas de pantalla (hp_roi y=52, etc.)
    frame_source    = "mss",    # GDI, sin DXGI — no necesita foco de ventana
    monitor_idx     = 2,        # monitor 2 = donde corre Tibia (left=1920)

    # --- Módulos activos ---
    auto_combat     = True,             # ← ÚNICO módulo activo en fase 1
    auto_loot       = False,            # desactivado en fase 1
    auto_refill     = False,
    depot_after_run = False,

    # --- Healer (activo para verificar) ---
    # Ajusta estos VK a tus hotkeys reales de Tibia:
    heal_hp_pct          = 70,          # % HP para heal normal
    heal_emergency_pct   = 30,          # % HP para heal de emergencia
    mana_threshold_pct   = 20,          # % MP para mana potion
    heal_hotkey_vk       = 0x70,        # F1 — heal normal
    emergency_hotkey_vk  = 0x72,        # F3 — heal emergencia
    mana_hotkey_vk       = 0x71,        # F2 — mana potion

    # --- Combat ---
    combat_config_file  = "combat_config.json",

    # --- Posición ---
    position_source      = "none",      # sin minimap en fase 1
    use_position_resolver = False,

    # --- Anti-ruido ---
    break_scheduler      = False,       # sin breaks en prueba
    stuck_detector       = False,       # sin stuck detector (no hay ruta)
    death_handler        = True,        # detectar muerte sí
    anti_kick            = True,        # evitar kick AFK

    # --- Sin ruta ---
    route_file           = "",
    loop_route           = False,

    # --- Telemetría ---
    soak_monitor         = True,
    session_stats        = True,
)

# ── Crear directorio de output ────────────────────────────────────────────────
Path("output").mkdir(exist_ok=True)

# ── Sesión ────────────────────────────────────────────────────────────────────
session = BotSession(cfg)

def _on_exit(sig: Optional[int], frame: Optional[FrameType]) -> None:
    print("\n\n[FASE 1] Deteniendo… (puede tardar 2s)")
    session.stop()
    sys.exit(0)

signal.signal(signal.SIGINT, _on_exit)
signal.signal(signal.SIGTERM, _on_exit)

print("[FASE 1] Iniciando en 5 segundos — haz clic en la ventana de Tibia ahora")
print("[FASE 1] Logs en: output/phase1.log\n")

try:
    session.start()

    # Sin ruta → el main loop termina inmediatamente.
    # Mantenemos el script vivo para que el thread de combat siga corriendo.
    print("[FASE 1] ✓ Combat thread activo — observa el log arriba")
    print("[FASE 1] Verifica que aparezcan líneas como:")
    print("          [C] Cave Rat detectado → ataque")
    print("          [C] ☠ Kill confirmado")
    print("          [H] HP=65% → heal disparado")
    print()

    while True:
        time.sleep(1.0)

except KeyboardInterrupt:
    _on_exit(None, None)
except Exception as exc:
    logging.exception("[FASE 1] Error fatal: %s", exc)
    session.stop()
    sys.exit(1)
