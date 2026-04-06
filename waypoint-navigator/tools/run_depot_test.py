"""
run_depot_test.py — Prueba de depot: temple → depot stow_all → temple
======================================================================
Qué hace:
  - Camina desde el temple al depot de Thais
  - Hace right-click en el depot chest → "Stow All Items"
  - Vuelve al temple

ANTES DE CORRER:
  1. Párate en el temple de Thais (32369, 32242, 7)
  2. Asegúrate de tener ítems en el backpack para depositar
  3. Ajusta depot_chest_coord en depot_config.json si el chest
     no está en (32348, 32222, 7) — ver CALIBRACIÓN abajo

CALIBRACIÓN del depot_chest_coord:
  - Párate en (32347, 32226, 7) mirando el depot chest
  - Usa debug_capture.py para ver la pantalla
  - Anota la coordenada Tibia del tile del chest (puedes verla
    en el cliente con Shift+click en el chest)
  - Actualiza depot_config.json con esa coordenada

Si "Stow All Items" no aparece en el menú:
  - Cambia stow_all_menu_entry_index en depot_config.json (prueba 0, 1, 2)
"""

import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.session import BotSession, SessionConfig
from src.depot_manager import DepotConfig, DepotManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("output/depot_test.log", encoding="utf-8"),
    ],
)

print("""
╔══════════════════════════════════════════════════════╗
║     DEPOT TEST — Temple → Depot stow_all → Temple   ║
║  Párate en temple (32369,32242,7) · Ctrl+C para stop ║
╚══════════════════════════════════════════════════════╝
""")

Path("output").mkdir(exist_ok=True)

cfg = SessionConfig(
    # --- Ventana / input ---
    target_window   = "Tibia",
    input_method    = "interception",
    start_delay     = 5.0,

    # --- Frame source ---
    frame_source    = "mss",
    monitor_idx     = 2,

    # --- Ruta ---
    route_file      = "routes/thais_depot_test.json",
    loop_route      = False,

    # --- Módulos activos ---
    auto_combat     = False,
    auto_loot       = False,
    auto_refill     = False,
    depot_after_run = True,       # habilita DepotManager

    # --- Healer deshabilitado para test limpio ---
    heal_hp_pct          = 70,
    heal_emergency_pct   = 30,
    mana_threshold_pct   = 20,
    heal_hotkey_vk       = 0,
    emergency_hotkey_vk  = 0,
    mana_hotkey_vk       = 0,

    # --- Walk timing ---
    step_interval   = 0.45,
    step_delay_min  = 0.0,
    step_delay_max  = 0.05,

    # --- Posición ---
    position_source       = "minimap",
    use_position_resolver = True,

    # --- Anti-ban mínimo ---
    break_scheduler = False,
    stuck_detector  = True,
    death_handler   = True,
    anti_kick       = False,
    session_stats   = False,
    soak_monitor    = False,
)

session = BotSession(cfg)

# Cargar depot_config.json si existe
depot_cfg_path = ROOT / "depot_config.json"
if depot_cfg_path.exists():
    try:
        dc = DepotConfig.load(depot_cfg_path)
        if session._depot is not None:
            session._depot._cfg = dc
        print(f"[DEPOT] Config cargada: chest={dc.depot_chest_coord}, mode={dc.deposit_mode}")
    except Exception as e:
        print(f"[DEPOT] ⚠ No se pudo cargar depot_config.json: {e}")

def _on_exit(sig: Optional[int], frame: Optional[FrameType]) -> None:
    print("\n[DEPOT TEST] Deteniendo…")
    session.stop()
    sys.exit(0)

signal.signal(signal.SIGINT, _on_exit)
signal.signal(signal.SIGTERM, _on_exit)

print("[DEPOT TEST] Párate en Thais temple (32369, 32242, 7)")
print("[DEPOT TEST] Iniciando en 5 segundos — haz clic en Tibia ahora")
print("[DEPOT TEST] Logs en: output/depot_test.log\n")

try:
    session.start()

    while session.is_running:
        time.sleep(1.0)

    print("[DEPOT TEST] Ruta completada")

except KeyboardInterrupt:
    _on_exit(None, None)
except Exception as exc:
    logging.exception("[DEPOT TEST] Error fatal: %s", exc)
    session.stop()
    sys.exit(1)
