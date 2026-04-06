"""
run_phase2.py — Fase 2: Combat + Loot (sin ruta)
=================================================
Qué hace:
  - Todo lo de Fase 1 +
  - Looter activo: detecta cadáveres, pausa walker (aunque no hay walker), loota
  - Quick loot via Alt+Q (más rápido que menú contextual)
  - Imprime: cadáver detectado, walker pausado, items recogidos

Cómo usar:
  1. Completa Fase 1 exitosamente
  2. Párate en el spawn de ratas en Thais (justo fuera del templo)
  3. python run_phase2.py
  4. Deja que mate 3-5 monstruos y observa el loot

Verifica que:
  [L] Cadáver detectado en pantalla (cx,cy)
  [W] ⏸ Loot en curso — walker en pausa    ← pausa confirmada
  [L] Alt+Q enviado sobre (cx,cy)            ← hotkey enviado
  [L] ✓ Quick Loot → cadáver looteado       ← loot exitoso
  (reanuda automáticamente)
"""

import logging
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.session import BotSession, SessionConfig
from src.looter import LootConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("output/phase2.log", encoding="utf-8"),
    ],
)

print("""
╔══════════════════════════════════════════════════════╗
║      FASE 2 — Combat + Loot Alt+Q (sin ruta)         ║
║  Mata monstruos · Loota via Alt+Q · Sin caminar      ║
║  Ctrl+C para detener                                 ║
╚══════════════════════════════════════════════════════╝
""")

Path("output").mkdir(exist_ok=True)

# ── Generar loot_config.json con Alt+Q si no existe ──────────────────────────
loot_cfg_path = ROOT / "loot_config.json"
if not loot_cfg_path.exists():
    lc = LootConfig(
        loot_mode             = "quick",
        use_hotkey_quick_loot = True,   # Alt+Q
        loot_delay            = 1.2,    # segundos antes de intentar lootear
        max_range_tiles       = 3,      # máxima distancia al cadáver
    )
    lc.save(loot_cfg_path)
    print(f"[FASE 2] loot_config.json generado en {loot_cfg_path}")
else:
    print(f"[FASE 2] Usando loot_config.json existente ({loot_cfg_path})")

cfg = SessionConfig(
    # --- Ventana / input ---
    target_window   = "Tibia",
    input_method    = "postmessage",
    start_delay     = 5.0,

    # --- Frame source ---
    frame_source    = "mss",    # GDI, sin DXGI — no necesita foco de ventana
    monitor_idx     = 2,        # monitor 2 = donde corre Tibia (left=1920)

    # --- Módulos activos ---
    auto_combat     = True,
    auto_loot       = True,             # ← LOOT ACTIVADO
    auto_refill     = False,
    depot_after_run = False,

    # --- Healer ---
    heal_hp_pct          = 70,
    heal_emergency_pct   = 30,
    mana_threshold_pct   = 20,
    heal_hotkey_vk       = 0x70,        # F1
    emergency_hotkey_vk  = 0x72,        # F3
    mana_hotkey_vk       = 0x71,        # F2

    # --- Combat ---
    combat_config_file  = "combat_config.json",

    # --- Posición (necesaria para calcular posición del cadáver en pantalla) ---
    position_source       = "none",
    use_position_resolver = False,

    # --- Sin ruta ---
    route_file    = "",
    loop_route    = False,

    # --- Anti-ruido ---
    break_scheduler = False,
    stuck_detector  = False,
    death_handler   = True,
    anti_kick       = True,
    session_stats   = True,
    soak_monitor    = True,
)

session = BotSession(cfg)

def _on_exit(sig: int | None, frame: object) -> None:
    print("\n\n[FASE 2] Deteniendo…")
    session.stop()
    sys.exit(0)

signal.signal(signal.SIGINT, _on_exit)
signal.signal(signal.SIGTERM, _on_exit)

print("[FASE 2] Iniciando en 5 segundos — haz clic en Tibia ahora")
print("[FASE 2] Logs en: output/phase2.log\n")

try:
    session.start()

    print("[FASE 2] ✓ Combat + Loot activos")
    print("[FASE 2] Observa el flujo:")
    print("  1. [C] Monstruo detectado + atacado")
    print("  2. [C] ☠ Kill confirmado → notify_kill()")
    print("  3. [L] Cadáver registrado en cola")
    print("  4. [W] ⏸ Loot en curso — walker en pausa")
    print("  5. [L] Alt+Q enviado → cadáver looteado")
    print()

    while True:
        time.sleep(1.0)

except KeyboardInterrupt:
    _on_exit(None, None)
except Exception as exc:
    logging.exception("[FASE 2] Error fatal: %s", exc)
    session.stop()
    sys.exit(1)
