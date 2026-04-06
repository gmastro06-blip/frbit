"""
examples/session_demo.py
------------------------
Demo completo del BotSession: carga una ruta JSON, arranca el healer,
camina los waypoints una vez e imprime stats al terminar.

Uso:
    python examples/session_demo.py
    python examples/session_demo.py --route routes/thais_depot_to_temple.json
    python examples/session_demo.py --route routes/thais_depot_to_temple.json --loop
    python examples/session_demo.py --dry-run   # sin necesidad de Tibia abierto

El flag --dry-run omite la conexión a la ventana Tibia; ideal para
probar la lógica de carga de rutas y el healer con umbral muy bajo.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.session import BotSession, SessionConfig


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="BotSession demo")
    ap.add_argument("--route",     default="routes/thais_depot_to_temple.json",
                    help="Fichero JSON de ruta (default: thais_depot_to_temple.json)")
    ap.add_argument("--window",    default="Tibia",
                    help="Título (fragmento) de la ventana Tibia (default: Tibia)")
    ap.add_argument("--heal-hp",   type=int, default=70,
                    help="HP%% para curar (default: 70)")
    ap.add_argument("--mana-mp",   type=int, default=30,
                    help="MP%% para mana (default: 30)")
    ap.add_argument("--loop",      action="store_true",
                    help="Repetir la ruta indefinidamente hasta Ctrl-C")
    ap.add_argument("--start-delay", type=float, default=3.0,
                    help="Segundos de espera antes del primer movimiento (default: 3)")
    ap.add_argument("--rope-vk",   type=lambda x: int(x, 0), default=0,
                    help="VK para soga (hex 0x71 o dec 113). 0=desactivado (default: 0)")
    ap.add_argument("--shovel-vk", type=lambda x: int(x, 0), default=0,
                    help="VK para pala (hex ok). 0=desactivado (default: 0)")
    ap.add_argument("--dry-run",   action="store_true",
                    help="Sin conexión a Tibia — solo muestra la ruta y los stats")
    args = ap.parse_args()

    # ── Build config ───────────────────────────────────────────────────────
    cfg = SessionConfig(
        route_file         = args.route,
        heal_hp_pct        = args.heal_hp,
        mana_threshold_pct = args.mana_mp,
        loop_route         = args.loop,
        start_delay        = 0.0 if args.dry_run else args.start_delay,
        target_window      = args.window,
        input_method       = "postmessage",
        rope_hotkey_vk     = args.rope_vk,
        shovel_hotkey_vk   = args.shovel_vk,
    )

    # ── Preview route ──────────────────────────────────────────────────────
    route_path = Path(args.route)
    if route_path.exists():
        with open(route_path) as f:
            raw = json.load(f)
        wps = raw.get("waypoints", raw if isinstance(raw, list) else [])
        print(f"\n  Ruta cargada: {route_path.name}")
        print(f"  Waypoints   : {len(wps)}")
        for i, wp in enumerate(wps[:5]):
            x, y, z = wp.get("x"), wp.get("y"), wp.get("z")
            name = wp.get("name", "")
            print(f"    [{i}] ({x}, {y}, z={z})  {name}")
        if len(wps) > 5:
            print(f"    … y {len(wps)-5} más")
    else:
        print(f"\n  [!] Fichero de ruta no encontrado: {args.route}")
        print("      Ejecuta con --dry-run para probar sin ruta.")

    print(f"\n  Config  heal<{cfg.heal_hp_pct}%  mana<{cfg.mana_threshold_pct}%  "
          f"loop={cfg.loop_route}  delay={cfg.start_delay}s")

    if args.dry_run:
        print("  [DRY-RUN] Sin conexión a Tibia.\n")

    # ── Log callback ───────────────────────────────────────────────────────
    def log(msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] {msg}")

    # ── Start session ──────────────────────────────────────────────────────
    session = BotSession(cfg, log_callback=log)

    # Graceful Ctrl-C
    def _signal_handler(sig, frame):
        print("\n\n  Ctrl-C recibido — deteniendo …")
        session.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)

    if not args.dry_run:
        session.start()
    else:
        # In dry-run mode, just show what would happen
        print("  Waypoints que se navegarían:")
        try:
            wps_list = session.load_waypoints(args.route)
            for i, wp in enumerate(wps_list):
                print(f"    [{i}] {wp.coord}  (name={wp.name!r})")
        except FileNotFoundError:
            print("    (fichero no encontrado)")
        print("\n  [DRY-RUN] Sesión no iniciada.")
        return

    # ── Monitor loop ───────────────────────────────────────────────────────
    print("\n  Sesión activa. Ctrl-C para detener.\n")
    try:
        while session.is_running:
            st = session.stats
            hp_pct: float = 0.0
            mp_pct: float = 0.0
            if session._healer:
                hp_pct, mp_pct = session._healer.read_stats()
            print(
                f"\r  HP={hp_pct:.0f}%  MP={mp_pct:.0f}%  "
                f"routes={st['routes_completed']}  "
                f"heals={st['heal_fired']}  mana={st['mana_fired']}   ",
                end="", flush=True,
            )
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n\n  Deteniendo …")
    finally:
        session.stop()

    # ── Final stats ────────────────────────────────────────────────────────
    st = session.stats
    elapsed = time.time() - (st["start_time"] or time.time())
    print(f"\n  ─── Stats finales ───────────────────")
    print(f"  Rutas completadas : {st['routes_completed']}")
    print(f"  Heals disparados  : {st['heal_fired']}")
    print(f"  Mana disparados   : {st['mana_fired']}")
    print(f"  Tiempo activo     : {elapsed:.0f}s")
    print(f"  ─────────────────────────────────────\n")


if __name__ == "__main__":
    main()
