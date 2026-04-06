"""
tools/run_route.py - Ejecuta y supervisa una ruta grabada.

Carga el .in generado por record_route.py, construye el stack mínimo
(InputController → Navigator → ScriptExecutor) y ejecuta el script
mostrando progreso en tiempo real.

Uso:
    python tools/run_route.py routes/mi_ruta.in
    python tools/run_route.py routes/mi_ruta.in --dry-run
    python tools/run_route.py routes/mi_ruta.in --loops 3 --floor 7

Teclas mientras corre (ESC = parar):
    ESC / Ctrl+C = detener la ejecución y salir limpiamente
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import threading
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

G  = "\033[92m"
C  = "\033[96m"
Y  = "\033[93m"
RE = "\033[91m"
B  = "\033[1m"
X  = "\033[0m"

_user32 = ctypes.windll.user32
_VK_ESC = 0x1B
_esc_prev = False


def _esc_pressed() -> bool:
    global _esc_prev
    state = bool(_user32.GetAsyncKeyState(_VK_ESC) & 0x8000)
    fired = state and not _esc_prev
    _esc_prev = state
    return fired


# ---------------------------------------------------------------------------
# Helpers de entorno
# ---------------------------------------------------------------------------

def _find_hwnd(fragment: str) -> int:
    results: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def cb(hwnd: int, _: int) -> bool:
        if _user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, buf, 256)
            if fragment.lower() in buf.value.lower():
                results.append(hwnd)
        return True
    _user32.EnumWindows(WNDENUMPROC(cb), 0)
    return results[0] if results else 0


def _build_frame_getter(window: str = "Proyector"):
    hwnd = _find_hwnd(window)
    if not hwnd:
        raise RuntimeError(f"Ventana '{window}' no encontrada")
    from src.frame_capture import PrintWindowCapture
    fg = PrintWindowCapture(hwnd=hwnd).open()
    print(f"  Frame: PrintWindow hwnd={hwnd:#x}")
    return fg


def _build_radar(fg, floor: int):
    from src.minimap_radar import MinimapConfig, MinimapRadar
    from src.map_loader import TibiaMapLoader

    cfg    = MinimapConfig.load()
    loader = TibiaMapLoader(cache_dir=Path("maps"))
    loader.get_map_image(floor)
    radar  = MinimapRadar(loader=loader, config=cfg)

    # Prueba de lectura inicial
    frame = fg()
    coord = radar.read(frame) if frame is not None else None
    if coord:
        print(f"  Radar: {G}OK{X} → ({coord.x},{coord.y},{coord.z})")
    else:
        print(f"  Radar: {Y}sin posición inicial — continuando de todos modos{X}")
    return radar, coord


def _build_navigator(floor: int):
    from src.navigator import WaypointNavigator
    nav = WaypointNavigator(cache_dir=Path("maps"))
    nav.load_floor(floor)
    return nav


def _build_ctrl(tibia_title: str = "Tibia", dry_run: bool = False,
                input_method: str = "interception"):
    from src.input_controller import InputController
    ctrl = InputController(target_title=tibia_title, input_method=input_method)
    if dry_run:
        print(f"  InputController: {Y}dry-run{X} (sin envío real de input)")
    else:
        if not ctrl.find_target():
            raise RuntimeError(
                f"No se pudo conectar a la ventana '{tibia_title}'.\n"
                f"  Asegúrate de que Tibia esté abierto."
            )
        print(f"  InputController: hwnd={ctrl.hwnd:#x}  method={input_method}")
    return ctrl


# ---------------------------------------------------------------------------
# Supervisión en tiempo real
# ---------------------------------------------------------------------------

class _PositionOracle:
    """
    Hilo dedicado que lee el radar a 4 Hz y expone la posición actual
    via get().  Thread-safe.  Único lugar donde se llama radar.read().
    """

    def __init__(self, fg, radar, initial_hint=None):
        self._fg    = fg
        self._radar = radar
        self._lock  = threading.Lock()
        self._pos   = initial_hint
        self._hint  = initial_hint
        self._ko    = 0
        self._stop  = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def get(self):
        with self._lock:
            return self._pos

    @property
    def ko_count(self) -> int:
        with self._lock:
            return self._ko

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._fg()
                if frame is not None:
                    with self._lock:
                        h = self._hint
                    coord = self._radar.read(frame, hint=h)
                    with self._lock:
                        if coord:
                            self._pos   = coord
                            self._hint  = coord
                            self._ko    = 0
                        else:
                            self._ko += 1
            except Exception:
                pass
            self._stop.wait(0.25)   # 4 lecturas por segundo


class _Supervisor:
    """
    Corre en un hilo secundario: muestra progreso en tiempo real
    leyendo posición del oracle (sin hacer radar.read() propio).
    """

    def __init__(self, executor, oracle):
        self._ex     = executor
        self._oracle = oracle
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _loop(self) -> None:
        last_line = ""

        while not self._stop.is_set():
            coord  = self._oracle.get()
            ko     = self._oracle.ko_count
            if coord:
                pos_str = f"({coord.x},{coord.y},{coord.z})"
            elif ko >= 3:
                pos_str = f"{Y}sin pos x{ko}{X}"
            else:
                pos_str = f"{RE}sin pos{X}"

            instr = getattr(self._ex, "_current_instr", None)
            instr_str = str(instr) if instr else "---"
            if len(instr_str) > 40:
                instr_str = instr_str[:40] + "…"

            idx   = getattr(self._ex, "_current_idx", 0)
            total = len(getattr(self._ex, "_instructions", []) or [])
            pct   = f"{idx}/{total}" if total else "?"

            line = (
                f"\r  {C}pos{X} {pos_str}  "
                f"{C}paso{X} {pct}  "
                f"{C}>{X} {instr_str}          "
            )
            if line != last_line:
                sys.stdout.write(line)
                sys.stdout.flush()
                last_line = line

            self._stop.wait(0.5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    script: Path,
    floor: int,
    loops: int,
    dry_run: bool,
    window: str,
    tibia: str,
    input_method: str = "interception",
) -> None:
    from src.script_parser import ScriptParser
    from src.script_executor import ScriptExecutor
    from src.models import Coordinate

    print(f"\n{B}{'='*58}{X}")
    print(f"  {B}Run Route{X}  —  {C}{script.name}{X}")
    print(f"  loops={loops}  dry_run={dry_run}  floor={floor}  input={input_method}")
    print(f"{'='*58}")

    # ── Infraestructura ──────────────────────────────────────────────────
    try:
        fg            = _build_frame_getter(window)
        radar, hint   = _build_radar(fg, floor)
        nav           = _build_navigator(floor)
        ctrl          = _build_ctrl(tibia, dry_run, input_method)
    except Exception as e:
        print(f"\n  {RE}ERROR: {e}{X}\n")
        return

    # ── Oracle de posición (radar a 4 Hz, thread-safe) ───────────────────
    oracle = _PositionOracle(fg, radar, initial_hint=hint)
    oracle.start()

    # ── Logs estructurados ───────────────────────────────────────────────
    log_lines: list[str] = []

    def _log(msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        log_lines.append(line)
        # Mostrar solo errores/advertencias en el log permanente
        if any(k in msg for k in ("ERROR", "WARN", "⚠", "✖", "skip", "abort")):
            sys.stdout.write(f"\n  {Y}{line}{X}\n")
            sys.stdout.flush()

    # ── Parsear script ───────────────────────────────────────────────────
    parser = ScriptParser()
    instructions = parser.parse_file(Path(script))
    print(f"  Script: {len(instructions)} instrucciones")
    print(f"\n  {Y}ESC = detener{X}  —  corriendo...\n")

    # ── Executor ─────────────────────────────────────────────────────────
    executor = ScriptExecutor(
        ctrl=ctrl,
        navigator=nav,
        frame_getter=fg,
        minimap_radar=radar,
        position_getter=oracle.get,
        dry_run=dry_run,
        log_fn=_log,
        step_interval=0.55,
        dispatch_retries=2,
    )
    # El oracle actualiza a 4 Hz; ampliar el jump-guard para que no rechace
    # lecturas válidas cuando el dead-reckoning diverge de la posición real.
    executor._MAX_STEP_JUMP = 12
    executor._MAX_SYNC_JUMP = 30

    # Supervisor en hilo separado (solo display, no hace radar.read())
    supervisor = _Supervisor(executor, oracle)
    supervisor.start()

    # ── Bucle de ejecución ───────────────────────────────────────────────
    completed = 0
    start_ts  = time.monotonic()

    def _esc_watcher():
        while not stop_event.is_set():
            if _esc_pressed():
                print(f"\n\n  {Y}ESC — deteniendo...{X}")
                executor.abort()
                stop_event.set()
                break
            time.sleep(0.05)

    stop_event = threading.Event()
    esc_thread = threading.Thread(target=_esc_watcher, daemon=True)
    esc_thread.start()

    try:
        for loop in range(loops):
            if stop_event.is_set():
                break
            if loops > 1:
                sys.stdout.write(f"\n  {G}Loop {loop+1}/{loops}{X}\n")
                sys.stdout.flush()

            executor.execute(instructions)
            completed += 1

            if loops > 1 and loop < loops - 1 and not stop_event.is_set():
                time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}Ctrl+C — abortando...{X}")
        executor.abort()
    finally:
        stop_event.set()
        supervisor.stop()
        oracle.stop()

    # ── Resumen ──────────────────────────────────────────────────────────
    elapsed = time.monotonic() - start_ts
    print(f"\n\n{B}{'='*58}{X}")
    print(f"  {G}Completado:{X} {completed}/{loops} loops  en {elapsed:.1f}s")

    warnings = [l for l in log_lines if any(k in l for k in ("⚠", "skip", "abort", "WARN", "ERROR"))]
    if warnings:
        print(f"  {Y}Avisos ({len(warnings)}):{X}")
        for w in warnings[-10:]:
            print(f"    {w}")

    # Guardar log
    log_path = script.with_suffix(".run.log")
    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    print(f"  Log: {log_path}")
    print(f"{B}{'='*58}{X}\n")


# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Ejecuta y supervisa una ruta grabada")
    p.add_argument("script",
                   help="Archivo .in a ejecutar (ej: routes/mi_ruta.in)")
    p.add_argument("--floor",    type=int,   default=7,
                   help="Floor actual (default: 7)")
    p.add_argument("--loops",    type=int,   default=1,
                   help="Número de veces que repite la ruta (default: 1)")
    p.add_argument("--dry-run",  action="store_true",
                   help="Sin envío real de input (solo simula)")
    p.add_argument("--window",   default="Proyector",
                   help="Ventana OBS projector (default: Proyector)")
    p.add_argument("--tibia",    default="Tibia",
                   help="Ventana Tibia para input (default: Tibia)")
    p.add_argument("--input-method", default="interception",
                   choices=["postmessage", "scancode", "hybrid", "interception"],
                   help="Método de input (default: postmessage)")
    args = p.parse_args()

    run(
        script       = Path(args.script),
        floor        = args.floor,
        loops        = args.loops,
        dry_run      = args.dry_run,
        window       = args.window,
        tibia        = args.tibia,
        input_method = args.input_method,
    )


if __name__ == "__main__":
    main()
