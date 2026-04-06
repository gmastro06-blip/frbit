"""
examples/healer_demo.py
-----------------------
Demo standalone del AutoHealer + HpMpDetector sobre un frame de OBS.

Uso:
    python examples/healer_demo.py --source virtual-cam
    python examples/healer_demo.py --source obs-ws --heal-vk 0x70 --mana-vk 0x71
    python examples/healer_demo.py --dry-run        # sin ventana Tibia, solo imprime stats

Flujo:
  1. Abre la fuente de vídeo (OBS Virtual Camera o WebSocket)
  2. Lee HP/MP del frame usando HpMpDetector
  3. Si HP/MP bajan del umbral → dispara la hotkey configurable
  4. Imprime stats cada segundo hasta Ctrl-C
"""

from __future__ import annotations

import argparse
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.healer import AutoHealer, HealConfig
from src.hpmp_detector import HpMpDetector, HpMpConfig
from src.input_controller import InputController


def main() -> None:
    ap = argparse.ArgumentParser(description="AutoHealer demo")
    ap.add_argument("--source", default="virtual-cam",
                    choices=["virtual-cam", "obs-ws", "screen"],
                    help="Fuente de vídeo (default: virtual-cam)")
    ap.add_argument("--heal-vk",   default="0x70",
                    help="VK hex del heal normal, ej. 0x70 = F1 (default: 0x70)")
    ap.add_argument("--mana-vk",   default="0x71",
                    help="VK hex del mana, ej. 0x71 = F2 (default: 0x71)")
    ap.add_argument("--emerg-vk",  default="0x72",
                    help="VK hex de emergencia (default: 0x72)")
    ap.add_argument("--heal-hp",   type=int, default=70,
                    help="HP%% umbral para curar (default: 70)")
    ap.add_argument("--emerg-hp",  type=int, default=30,
                    help="HP%% de emergencia (default: 30)")
    ap.add_argument("--mana-mp",   type=int, default=30,
                    help="MP%% umbral para mana (default: 30)")
    ap.add_argument("--window",    default="Tibia",
                    help="Fragmento del título de la ventana Tibia (default: Tibia)")
    ap.add_argument("--dry-run",   action="store_true",
                    help="No conecta a Tibia, solo imprime lecturas de HP/MP")
    args = ap.parse_args()

    # ── Config ─────────────────────────────────────────────────────────────
    heal_vk  = int(args.heal_vk,  16)
    mana_vk  = int(args.mana_vk,  16)
    emerg_vk = int(args.emerg_vk, 16)

    cfg = HealConfig(
        hp_threshold_pct  = args.heal_hp,
        hp_emergency_pct  = args.emerg_hp,
        mp_threshold_pct  = args.mana_mp,
        heal_hotkey_vk    = heal_vk,
        mana_hotkey_vk    = mana_vk,
        emergency_hotkey_vk = emerg_vk,
    )

    # ── Input controller ───────────────────────────────────────────────────
    ctrl: InputController | None = None
    if not args.dry_run:
        ctrl = InputController(args.window, input_method="postmessage")
        ctrl.find_target()
        if not ctrl.is_connected():
            print(f"[!] Ventana '{args.window}' no encontrada — ejecutando en modo dry-run")
            ctrl = None

    # ── HpMp detector ──────────────────────────────────────────────────────
    hp_cfg  = HpMpConfig.load()
    detector = HpMpDetector(hp_cfg)

    # ── Frame source ───────────────────────────────────────────────────────
    frame_getter = None
    cap = None

    if not args.dry_run:
        import cv2
        if args.source == "virtual-cam":
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                def frame_getter():
                    ret, frame = cap.read()
                    return frame if ret else None
                print(f"  [healer_demo] Virtual Camera abierta.")
            else:
                print("  [!] No se pudo abrir la cámara — dry-run")
        elif args.source == "obs-ws":
            try:
                from src.character_detector import OBSWebSocketSource, DetectorConfig
                obs_src = OBSWebSocketSource(DetectorConfig.load())
                obs_src.connect()
                frame_getter = obs_src.get_frame
                print("  [healer_demo] OBS WebSocket conectado.")
            except Exception as e:
                print(f"  [!] OBS WebSocket error: {e} — dry-run")

    # ── Healer ─────────────────────────────────────────────────────────────
    healer = AutoHealer(ctrl, config=cfg, detector=detector)

    heal_count = [0]
    mana_count = [0]

    def on_heal():
        heal_count[0] += 1
        print(f"  >>> HEAL disparado (total={heal_count[0]})")

    def on_mana():
        mana_count[0] += 1
        print(f"  >>> MANA disparado (total={mana_count[0]})")

    healer.on_heal = on_heal
    healer.on_mana = on_mana

    if frame_getter is not None:
        healer.set_frame_getter(frame_getter)

    healer.start()
    print("\n  [healer_demo] Ejecutando. Ctrl-C para detener.\n")
    print(f"  Config: heal<{cfg.hp_threshold_pct}%  emergency<{cfg.hp_emergency_pct}%  mana<{cfg.mp_threshold_pct}%")
    print(f"  Hotkeys: heal=F{heal_vk-0x6F}  mana=F{mana_vk-0x6F}  emerg=F{emerg_vk-0x6F}\n")

    try:
        while True:
            hp_pct, mp_pct = healer.read_stats()
            status = "DRY" if (ctrl is None) else "LIVE"
            print(f"\r  [{status}] HP={hp_pct:.0f}%  MP={mp_pct:.0f}%  "
                  f"heals={heal_count[0]}  mana={mana_count[0]}   ",
                  end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\n  Deteniendo …")
    finally:
        healer.stop()
        if cap is not None:
            cap.release()
        print(f"  Fin. heals={heal_count[0]}  mana={mana_count[0]}")


if __name__ == "__main__":
    main()
