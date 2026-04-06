#!/usr/bin/env python
"""
Nivel 1 — Live Tests: Tibia abierto, idle (sin movimiento).
Requiere: OBS Proyector abierto con Tibia visible, Pico2 en COM4.

Todo vía captura de pantalla + HID. Cero lectura de memoria.

Tests:
  P-INP-01  InputController: find_window + press_key (sin efecto en juego)
  P-INP-02  Mouse Bézier: curvatura y path
  P-VIS-01  Frame Capture: PrintWindow no-negro
  P-VIS-02  Frame Sources: WGCSource get_frame
  P-VIS-05  Minimap Radar: posición detectada
  P-VIS-06  Minimap Calibrator: auto-calibración
  P-HP-01   HpMpDetector: lectura de barras
  P-HUM-02  Adaptive ROI: detección de anchors
  P-HUM-03  UI Detection: funciones disponibles
"""
from __future__ import annotations
import sys, os, time, json
import ctypes
import ctypes.wintypes as wt
import numpy as np
from numpy.typing import NDArray
from typing import Callable, cast

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

PASS = 0
FAIL = 0
SKIP = 0
RESULTS: list[tuple[str, str, str]] = []  # (test_id, status, detail)

WINDOW_TITLE = "Proyector"  # OBS Projector con Tibia
FrameArray = NDArray[np.uint8]
CAPTURED_FRAME: FrameArray | None = None


def report(test_id: str, status: str, detail: str = "") -> None:
    global PASS, FAIL, SKIP
    RESULTS.append((test_id, status, detail))
    if status == "PASS":
        PASS += 1
    elif status == "FAIL":
        FAIL += 1
    else:
        SKIP += 1
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "?")
    print(f"  {icon} {test_id}: {status}  {detail}")


def find_hwnd(title_fragment: str) -> int:
    """Busca hwnd por título de ventana — solo Win32 API pública."""
    user32 = ctypes.windll.user32
    results: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def callback(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if title_fragment.lower() in buf.value.lower():
                results.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return results[0] if results else 0


# ═══════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("  NIVEL 1 — Live Tests (Tibia abierto, idle)")
print("  Método: Screen capture + HID only (sin memoria)")
print("=" * 60)

# ── Pre-check: ¿existe la ventana? ────────────────────────────────────────
hwnd = find_hwnd(WINDOW_TITLE)
if not hwnd:
    print(f"\n❌ ABORT: No se encontró ventana '{WINDOW_TITLE}'.")
    print("   Abre OBS Projector con Tibia visible e intenta otra vez.")
    sys.exit(1)
print(f"\n✓ Ventana '{WINDOW_TITLE}' encontrada: hwnd={hwnd}")

# ═══════════════════════════════════════════════════════════════════════════
# P-INP-01 — InputController
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-INP-01: InputController ──")
try:
    from src.input_controller import InputController, find_window, WindowInfo
    from human_input_system.core.pico_hid_controller import PicoHIDController
    from human_input_system.config.models import PicoConfig

    # 1a. find_window
    winfo = find_window(WINDOW_TITLE)
    assert winfo is not None, f"find_window('{WINDOW_TITLE}') retornó None"
    assert winfo.hwnd != 0, "hwnd es 0"
    report("P-INP-01a", "PASS", f"find_window OK, hwnd={winfo.hwnd}, title={winfo.title!r}")

    # 1b. InputController con Interception (driver-level, no postmessage)
    ic = InputController(target_title="Tibia", input_method="interception")
    target_window = ic.find_target()
    assert target_window is not None, "InputController no encontró ventana Tibia"
    assert ic.is_connected(), "InputController no conectó tras find_target()"
    info = ic.stats_snapshot()
    assert info.get("input_method") == "interception", f"Método incorrecto: {info.get('input_method')}"
    report("P-INP-01b", "PASS", f"InputController: interception, hwnd={info.get('hwnd')}")

    # 1b2. Pico HID obligatorio (COM4)
    pico_cfg = PicoConfig(enabled=True, port="COM4")
    pico = PicoHIDController(config=pico_cfg, fallback_controller=ic)
    pico_ok = pico.initialize()
    assert pico_ok, "Pico HID no disponible — conectar Pico2 en COM4"
    ic.set_arduino_failover(pico)
    pico_status = pico.status() or {}
    report("P-INP-01b2", "PASS",
           f"Pico HID COM4 activo — uptime={pico_status.get('uptime_ms', '?')}ms")

    # 1c. Concurrencia (sin enviar a Tibia — solo lock test)
    import threading
    errors: list[str] = []

    def lock_test(tid: int) -> None:
        for _ in range(50):
            try:
                ic.stats_snapshot()
            except RuntimeError as e:
                errors.append(str(e))

    threads = [threading.Thread(target=lock_test, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(errors) == 0, f"{len(errors)} errores de concurrencia"
    report("P-INP-01c", "PASS", "200 llamadas concurrentes sin error")

except Exception as e:
    report("P-INP-01", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-INP-02 — Mouse Bézier
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-INP-02: Mouse Bézier ──")
try:
    from src.mouse_bezier import bezier_path

    # 2a. Path tiene curvatura
    path = bezier_path((100, 100), (500, 400), steps=30)
    assert len(path) >= 20, f"Path muy corto: {len(path)}"
    assert path[0] == (100, 100), f"Inicio incorrecto: {path[0]}"
    assert path[-1] == (500, 400), f"Fin incorrecto: {path[-1]}"

    # Verificar curvatura: punto medio NO debe estar en línea recta
    mid = path[len(path) // 2]
    # Punto de línea recta: (300, 250)
    straight_mid = (300, 250)
    dist = abs(mid[0] - straight_mid[0]) + abs(mid[1] - straight_mid[1])
    assert dist > 5, f"Path sin curvatura, dist al medio recto = {dist}"
    report("P-INP-02a", "PASS", f"Path: {len(path)} pts, curvatura={dist}px off-line")

    # 2b. Múltiples paths son diferentes (randomización)
    paths = [bezier_path((100, 100), (500, 400), steps=30) for _ in range(5)]
    unique = len(set(tuple(p[15]) for p in paths))
    assert unique >= 2, "Paths idénticos — sin randomización"
    report("P-INP-02b", "PASS", f"{unique}/5 puntos medios únicos")

except Exception as e:
    report("P-INP-02", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-VIS-01 — Frame Capture (PrintWindow)
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-VIS-01: Frame Capture (PrintWindow) ──")
try:
    from src.frame_capture import PrintWindowCapture

    cap = PrintWindowCapture(hwnd=hwnd)
    grab = cast(Callable[[], FrameArray], cap.open())

    t0 = time.perf_counter()
    frame = grab()
    latency_ms = (time.perf_counter() - t0) * 1000

    assert frame is not None, "grab() retornó None"
    assert frame.ndim == 3, f"Frame no es 3D: {frame.ndim}"
    frame_height, frame_width, frame_channels = frame.shape
    assert frame_channels == 3 or frame_channels == 4, f"Canales inesperados: {frame_channels}"
    assert frame_width > 800, f"Ancho sospechoso: {frame_width}"
    assert frame_height > 600, f"Alto sospechoso: {frame_height}"

    # Check no-negro
    nonzero_pct = np.count_nonzero(frame) / frame.size * 100
    assert nonzero_pct > 5, f"Frame negro: solo {nonzero_pct:.1f}% no-cero"

    report("P-VIS-01a", "PASS",
           f"Frame {frame_width}x{frame_height}x{frame_channels}, {nonzero_pct:.1f}% no-cero, {latency_ms:.1f}ms")

    # Latencia
    times = []
    for _ in range(10):
        t0 = time.perf_counter()
        f = grab()
        times.append((time.perf_counter() - t0) * 1000)
    avg_ms = sum(times) / len(times)
    report("P-VIS-01b", "PASS" if avg_ms < 100 else "FAIL",
           f"Latencia promedio: {avg_ms:.1f}ms (10 frames)")

    # Guardar frame de referencia para tests siguientes
    CAPTURED_FRAME = frame
    cap.close() if hasattr(cap, 'close') else None

except Exception as e:
    CAPTURED_FRAME = None
    report("P-VIS-01", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-VIS-02 — Frame Sources (WGCSource)
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-VIS-02: Frame Sources (WGCSource) ──")
try:
    from src.frame_sources import WGCSource

    src = WGCSource(window_title=WINDOW_TITLE)
    src.connect()
    raw_wgc_frame = src.get_frame()
    assert raw_wgc_frame is not None, "WGCSource.get_frame() retornó None"
    wgc_frame = cast(FrameArray, raw_wgc_frame)
    frame_height, frame_width = wgc_frame.shape[:2]
    assert frame_width > 800 and frame_height > 600, (
        f"Dimensiones inesperadas: {frame_width}x{frame_height}"
    )
    nonzero = np.count_nonzero(wgc_frame) / wgc_frame.size * 100
    assert nonzero > 5, f"Frame negro: {nonzero:.1f}%"
    report("P-VIS-02", "PASS", f"WGC frame {frame_width}x{frame_height}, {nonzero:.1f}% no-cero")
    src.disconnect()
    if CAPTURED_FRAME is None:
        CAPTURED_FRAME = wgc_frame
except ImportError as e:
    report("P-VIS-02", "SKIP", f"winsdk no disponible: {e}")
except Exception as e:
    report("P-VIS-02", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-VIS-05 — Minimap Radar (posición por pixel matching)
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-VIS-05: Minimap Radar ──")
if CAPTURED_FRAME is not None:
    try:
        from pathlib import Path
        from src.map_loader import TibiaMapLoader
        from src.minimap_radar import MinimapRadar, MinimapConfig
        import cv2

        loader = TibiaMapLoader(cache_dir=Path("maps"))
        loader.get_map_image(7)  # pre-load floor 7

        captured_frame = cast(FrameArray, CAPTURED_FRAME)
        cfg_data = json.loads(Path("minimap_config.json").read_text(encoding="utf-8"))
        config = MinimapConfig(**{k: v for k, v in cfg_data.items() if not k.startswith("_")})

        radar = MinimapRadar(loader=loader, config=config)

        t0 = time.perf_counter()
        pos = radar.read(captured_frame, floor=7)
        radar_ms = (time.perf_counter() - t0) * 1000

        # Guardar el frame capturado siempre para inspección
        try:
            cv2.imwrite("output/frame_capturado_nivel1.png", captured_frame)
        except Exception as e_img:
            print(f"[WARN] No se pudo guardar frame_capturado_nivel1.png: {e_img}")

        if pos is not None:
            report("P-VIS-05a", "PASS",
                   f"Posición: ({pos.x}, {pos.y}, {pos.z}), conf={radar.confidence:.2f}, {radar_ms:.0f}ms")
        else:
            # Guardar el frame fallido para depuración
            try:
                cv2.imwrite("output/frame_minimap_fallo_nivel1.png", captured_frame)
            except Exception as e_img2:
                print(f"[WARN] No se pudo guardar frame_minimap_fallo_nivel1.png: {e_img2}")
            report("P-VIS-05a", "FAIL", f"read() retornó None ({radar_ms:.0f}ms) — frame guardado en output/frame_minimap_fallo_nivel1.png")

        # Estabilidad: 5 lecturas consecutivas
        readings = []
        for _ in range(5):
            p = radar.read(captured_frame, floor=7)
            if p:
                readings.append((p.x, p.y))
        if len(readings) >= 3:
            xs = [r[0] for r in readings]
            ys = [r[1] for r in readings]
            spread = max(max(xs) - min(xs), max(ys) - min(ys))
            report("P-VIS-05b", "PASS" if spread <= 3 else "FAIL",
                   f"Estabilidad: {len(readings)}/5 OK, spread={spread} tiles")
        else:
            report("P-VIS-05b", "FAIL", f"Solo {len(readings)}/5 lecturas exitosas — frame guardado en output/frame_minimap_fallo_nivel1.png")

    except Exception as e:
        report("P-VIS-05", "FAIL", str(e))
else:
    report("P-VIS-05", "SKIP", "No hay frame capturado")

# ═══════════════════════════════════════════════════════════════════════════
# P-VIS-06 — Minimap Calibrator
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-VIS-06: Minimap Calibrator ──")
if CAPTURED_FRAME is not None:
    try:
        from src.minimap_calibrator import MinimapCalibrator

        captured_frame = cast(FrameArray, CAPTURED_FRAME)
        calibrator = MinimapCalibrator(floor=7)
        result = calibrator.calibrate(captured_frame)

        if result.success:
            cfg = result.config
            report("P-VIS-06a", "PASS",
                   f"Calibración OK: tiles_wide={cfg.tiles_wide}, roi={cfg.roi}, "
                   f"score={result.best_score:.3f}")
            if result.position:
                report("P-VIS-06b", "PASS",
                       f"Posición calibrada: ({result.position.x}, {result.position.y}, {result.position.z})")
            else:
                report("P-VIS-06b", "FAIL", "Calibración OK pero sin posición")
        else:
            msgs = "; ".join(result.messages[:3])
            report("P-VIS-06", "FAIL", f"Calibración falló: {msgs}")

    except Exception as e:
        report("P-VIS-06", "FAIL", str(e))
else:
    report("P-VIS-06", "SKIP", "No hay frame capturado")

# ═══════════════════════════════════════════════════════════════════════════
# P-HP-01 — HpMpDetector (lectura de barras de vida/mana por pixel)
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-HP-01: HpMpDetector ──")
if CAPTURED_FRAME is not None:
    try:
        from src.hpmp_detector import HpMpDetector

        captured_frame = cast(FrameArray, CAPTURED_FRAME)
        det = HpMpDetector()  # carga hpmp_config.json por defecto
        hp, mp = det.read_bars(captured_frame)

        if hp is not None:
            assert 0 <= hp <= 100, f"HP fuera de rango: {hp}"
            report("P-HP-01a", "PASS", f"HP={hp}%")
        else:
            report("P-HP-01a", "FAIL", "HP es None")

        if mp is not None:
            assert 0 <= mp <= 100, f"MP fuera de rango: {mp}"
            report("P-HP-01b", "PASS", f"MP={mp}%")
        else:
            report("P-HP-01b", "FAIL", "MP es None")

        # Estabilidad: 5 lecturas del mismo frame dan ±2%
        hps: list[int] = []
        for _ in range(5):
            hp_result = det.read_bars(captured_frame)
            hp_candidate = hp_result[0]
            if hp_candidate is not None:
                hps.append(hp_candidate)
        if len(hps) >= 3:
            spread = max(hps) - min(hps)
            report("P-HP-01c", "PASS" if spread <= 2 else "FAIL",
                   f"Estabilidad HP: spread={spread}% en {len(hps)} lecturas")
        else:
            report("P-HP-01c", "FAIL", f"Solo {len(hps)}/5 lecturas de HP")

    except Exception as e:
        report("P-HP-01", "FAIL", str(e))
else:
    report("P-HP-01", "SKIP", "No hay frame capturado")

# ═══════════════════════════════════════════════════════════════════════════
# P-HUM-02 — Adaptive ROI (detección de UI por template matching)
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-HUM-02: Adaptive ROI ──")
if CAPTURED_FRAME is not None:
    try:
        from src.adaptive_roi import AdaptiveROIDetector

        captured_frame = cast(FrameArray, CAPTURED_FRAME)
        roi_det = AdaptiveROIDetector()
        n_anchors = roi_det.load_anchors_from_dir()

        if n_anchors > 0:
            rois = roi_det.detect(captured_frame)
            report("P-HUM-02a", "PASS",
                   f"{n_anchors} anchors cargados, {len(rois)} ROIs detectados")
            for name, droi in list(rois.items())[:5]:
                report(f"P-HUM-02.{name}", "PASS",
                       f"roi={droi.roi}, conf={droi.confidence:.2f}")
        else:
            # Sin anchors en disco — test proporcional
            frame_height, frame_width = captured_frame.shape[:2]
            prop_rois = roi_det.get_all_proportional_rois(frame_width, frame_height)
            report("P-HUM-02a", "PASS" if len(prop_rois) > 0 else "SKIP",
                   f"Sin anchors en cache/, {len(prop_rois)} ROIs proporcionales")

    except Exception as e:
        report("P-HUM-02", "FAIL", str(e))
else:
    report("P-HUM-02", "SKIP", "No hay frame capturado")

# ═══════════════════════════════════════════════════════════════════════════
# P-HUM-03 — UI Detection (funciones puras sobre frames)
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-HUM-03: UI Detection ──")
try:
    from src.ui_detection import (
        detect_context_menu,
        detect_container_window,
        scale_offset_y,
        scale_offset_x,
    )

    # 3a. Funciones importables
    report("P-HUM-03a", "PASS", "Todas las funciones importadas OK")

    # 3b. scale_offset funciona
    if CAPTURED_FRAME is not None:
        captured_frame = cast(FrameArray, CAPTURED_FRAME)
        h_frame, w_frame = captured_frame.shape[:2]
        scaled = scale_offset_y(100, captured_frame)
        expected = int(100 * h_frame / 1080)
        assert abs(scaled - expected) <= 2, f"scale_offset_y: {scaled} vs esperado ~{expected}"
        report("P-HUM-03b", "PASS", f"scale_offset_y(100) = {scaled} (ref_height=1080)")

        # 3c. detect_container_window con frame real (puede dar None si no hay container)
        container = detect_container_window(captured_frame)
        if container:
            report("P-HUM-03c", "PASS", f"Container detectado: {container}")
        else:
            report("P-HUM-03c", "PASS", "No container visible (esperado en idle)")

        # 3d. detect_context_menu entre dos frames iguales = no menu
        menu_result = detect_context_menu(captured_frame, captured_frame, 500, 400)
        assert menu_result is None, f"Detectó menu fantasma: {menu_result}"
        report("P-HUM-03d", "PASS", "Sin menu fantasma entre frames iguales")
    else:
        report("P-HUM-03b", "SKIP", "No hay frame")

except Exception as e:
    report("P-HUM-03", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"  NIVEL 1 RESULTADO: {PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
print("=" * 60)
for tid, st, detail in RESULTS:
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(st, "?")
    print(f"  {icon} {tid}: {detail}")

if FAIL > 0:
    print(f"\n⚠️  {FAIL} tests fallaron — revisar salida arriba")
    sys.exit(1)
else:
    print(f"\n✅ Nivel 1 completo: {PASS} tests pasaron")
    sys.exit(0)
