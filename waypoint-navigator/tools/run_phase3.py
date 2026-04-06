"""
run_phase3.py — Fase 3: Hunt completo (ruta + combat + loot + heal)
====================================================================
Qué hace:
  - Todo lo de Fase 2 +
  - Camina la ruta routes/thais_rat_hunt.json en loop
  - Walker pausa al detectar cadáver, loota y continúa
  - Healer activo durante toda la ruta
  - Stats cada 5 minutos en el log

Cómo usar:
  1. Completa Fase 2 exitosamente
  2. Párate en el temple de Thais (coordenada 32369, 32241, 7)
  3. python run_phase3.py
  4. Deja correr 30 minutos observando el log

Verifica que:
  Walker camina de waypoint a waypoint
  [W] ⏸ Loot en curso → pausa → reanuda sin desviarse
  No hay "corpse not found" repetidos (cadáver encontrado bien)
  El personaje no queda stuck entre dos waypoints
  HP nunca llega a 0 (healer funcionando)

Ajustes si algo falla:
  "corpse not found" mucho → aumentar loot_delay en loot_config.json (1.5 → 2.0)
  Walker no pausa → verificar que on_loot_start está en logs
  Monstruos no detectados → revisar battle_list_roi en combat_config.json
"""

import io
import logging
import signal
import sys
import time
from pathlib import Path

# Forzar UTF-8 en stdout/stderr para evitar UnicodeEncodeError en Windows (cp1252)
# reconfigure() es más robusto que reemplazar el wrapper (Python 3.7+)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
elif hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
elif hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.session import BotSession, SessionConfig
from src.looter import LootConfig

Path("output").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("output/phase3.log", encoding="utf-8"),
    ],
)

print("=" * 56)
print("  FASE 3 -- Hunt completo: ruta + combat + loot")
print("  Thais Rat Hunt | Loop indefinido | Ctrl+C para stop")
print("=" * 56)

# ── Generar loot_config.json con Alt+Q si no existe ──────────────────────────
loot_cfg_path = ROOT / "loot_config.json"
if not loot_cfg_path.exists():
    lc = LootConfig(
        loot_mode             = "quick",
        use_hotkey_quick_loot = True,
        loot_delay            = 1.5,    # un poco más generoso en fase 3 (personaje en movimiento)
        max_range_tiles       = 3,
    )
    lc.save(loot_cfg_path)
    print(f"[FASE 3] loot_config.json generado")

cfg = SessionConfig(
    # --- Ventana / input ---
    target_window   = "Tibia",
    input_method    = "interception",   # requerido para path Arduino HID
    arduino_enabled = True,             # Arduino Leonardo en COM4 como HID primario
    arduino_port    = "auto",            # auto-detect COM port (COM4 confirmed working)
    start_delay     = 5.0,

    # --- Frame source ---
    frame_source    = "mss",    # GDI, sin DXGI — no necesita foco de ventana
    monitor_idx     = 2,        # monitor 2 = donde corre Tibia (left=1920)

    # --- RUTA ---
    route_file      = "routes/thais_rat_hunt.json",
    loop_route      = True,             # loop infinito
    start_pos       = "32369,32241,7",  # posición inicial del temple (override calibrador)
    startup_position_tolerance = 15,   # acepta cualquier tile del corredor del temple

    # --- Módulos activos ---
    auto_combat     = True,
    auto_loot       = True,
    auto_refill     = True,
    depot_after_run = True,             # crea DepotManager + TradeManager para el script

    # --- Healer ---
    heal_hp_pct          = 70,
    heal_emergency_pct   = 30,
    mana_threshold_pct   = 20,
    heal_hotkey_vk       = 0x70,        # F1
    emergency_hotkey_vk  = 0x72,        # F3
    mana_hotkey_vk       = 0x71,        # F2

    # --- Combat + Trade ---
    combat_config_file  = "combat_config.json",
    trade_config_file   = "trade_config.json",

    # --- Walk timing (ajustar según latencia del personaje) ---
    step_interval   = 0.45,             # segundos entre pasos (knight: 0.45s/tile aprox)
    step_delay_min  = 0.02,             # jitter mínimo — nunca pasos perfectamente iguales
    step_delay_max  = 0.15,             # jitter máximo — ~33% variación natural

    # --- Posición ---
    position_source       = "minimap",   # leer posición real del minimapa cada paso
    use_position_resolver = True,

    # --- Anti-ban / seguridad ---
    break_scheduler = True,             # breaks automáticos (45-120min play, 3-15min break)
    stuck_detector  = True,             # detectar stuck + recuperar
    death_handler   = True,             # detectar muerte
    anti_kick       = True,             # evitar kick AFK

    # --- Stats y telemetría ---
    session_stats        = True,
    soak_monitor         = True,
    soak_memory_warn_mb  = 1000.0,   # EasyOCR solo usa ~500MB
)

session = BotSession(cfg)

def _on_exit(sig: int | None, frame: object | None) -> None:
    print("\n\n[FASE 3] Deteniendo… (guardando stats)")
    session.stop()
    print("[FASE 3] Stats guardados en output/")
    sys.exit(0)

signal.signal(signal.SIGINT, _on_exit)
signal.signal(signal.SIGTERM, _on_exit)

print("[FASE 3] Párate en Thais temple (32369, 32241, 7)")
print("[FASE 3] Iniciando en 5 segundos — haz clic en Tibia ahora")
print("[FASE 3] Logs en: output/phase3.log\n")

try:
    session.start()

    # session.start() lanza el loop en un hilo daemon y retorna inmediatamente.
    # Bloqueamos el hilo principal aquí para que el proceso no termine.
    while session.is_running:
        time.sleep(1.0)

    print("[FASE 3] Sesión finalizada")

except KeyboardInterrupt:
    _on_exit(None, None)
except Exception as exc:
    logging.exception("[FASE 3] Error fatal: %s", exc)
    session.stop()
    sys.exit(1)
