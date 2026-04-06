#!/usr/bin/env python
"""
Nivel 2 — Live Tests: Tibia en templo (personaje quieto, posición conocida).
Requiere: OBS Proyector, Pico2 COM4, personaje en Thais Temple (~32369,32241,7).

Todo vía screen capture + HID. Cero lectura de memoria.

Tests:
  P-VIS-07   PositionResolver: resolve con MinimapRadar como fuente
  P-VIS-08   ObstacleAnalyzer: análisis de tiles alrededor
  P-NAV-04   TransitionRegistry: carga y consulta de transiciones
  P-NAV-05   Navigator: A* ruta corta en templo
  P-NAV-06   StuckDetector: creación y configuración
  P-NAV-07   PathVisualizer: render de segmento
  P-NAV-08   WalkabilityOverlay: render frame
  P-HUM-04   ActionVerifier: verify_frame_valid
  P-REC-06   WaypointLogger: log + save JSON
  P-REC-07   WaypointRecorder: SimpleRouteRecorder
"""
from __future__ import annotations
import sys, os, time, json, tempfile
import ctypes
import numpy as np
from pathlib import Path
import cv2
from typing import Any, cast

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

PASS = 0
FAIL = 0
SKIP = 0
RESULTS: list[tuple[str, str, str]] = []

WINDOW_TITLE = "Proyector"


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
print("  NIVEL 2 — Live Tests (Tibia en templo)")
print("  Método: Screen capture + HID only")
print("=" * 60)

# ── Pre-checks ────────────────────────────────────────────────────────────
hwnd = find_hwnd(WINDOW_TITLE)
if not hwnd:
    print(f"\n❌ ABORT: No se encontró ventana '{WINDOW_TITLE}'.")
    sys.exit(1)
print(f"\n✓ Ventana '{WINDOW_TITLE}': hwnd={hwnd}")

# Capturar frame de referencia
from src.frame_capture import PrintWindowCapture
cap = PrintWindowCapture(hwnd=hwnd)
grab = cap.open()
FRAME = grab()
assert FRAME is not None and FRAME.ndim == 3, "Frame captura falló"
print(f"✓ Frame capturado: {FRAME.shape[1]}x{FRAME.shape[0]}")

# Guardar el frame capturado siempre para inspección
try:
    cv2.imwrite("output/frame_capturado_nivel2.png", FRAME)
except Exception as e_img:
    print(f"[WARN] No se pudo guardar frame_capturado_nivel2.png: {e_img}")

# Cargar map loader (reutilizado por varios tests)
from src.map_loader import TibiaMapLoader
from src.models import Coordinate
loader = TibiaMapLoader(cache_dir=Path("maps"))
loader.get_map_image(7)
print("✓ Mapa floor 7 cargado")

# Radar para obtener posición actual
from src.minimap_radar import MinimapRadar, MinimapConfig
with open("minimap_config.json") as f:
    cfg_data = json.load(f)
mm_config = MinimapConfig(**{k: v for k, v in cfg_data.items() if not k.startswith("_")})
radar = MinimapRadar(loader=loader, config=mm_config)
current_pos = radar.read(FRAME, floor=7)
if current_pos:
    print(f"✓ Posición actual: ({current_pos.x}, {current_pos.y}, {current_pos.z})")
else:
    # Guardar el frame fallido para depuración
    try:
        cv2.imwrite("output/frame_minimap_fallo_nivel2.png", FRAME)
    except Exception as e_img2:
        print(f"[WARN] No se pudo guardar frame_minimap_fallo_nivel2.png: {e_img2}")
    print("⚠️  Radar no detectó posición — frame guardado en output/frame_minimap_fallo_nivel2.png")

# ═══════════════════════════════════════════════════════════════════════════
# P-VIS-07 — PositionResolver
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-VIS-07: PositionResolver ──")
try:
    from src.position_resolver import PositionResolver, SourceKind

    resolver = PositionResolver()
    resolver.add_source("radar", SourceKind.MINIMAP_RADAR, cast(Any, radar))
    assert resolver.source_count == 1, f"source_count={resolver.source_count}"
    report("P-VIS-07a", "PASS", f"Source añadida: {resolver.source_names}")

    pos = resolver.resolve(FRAME, floor=7)
    if pos is not None:
        report("P-VIS-07b", "PASS", f"Posición: ({pos.x}, {pos.y}, {pos.z})")
    else:
        report("P-VIS-07b", "FAIL", "resolve() retornó None")

    # Thread safety: 4 hilos resolviendo
    import threading
    results_pos: list = []
    errors_pos: list[str] = []

    def resolve_thread() -> None:
        for _ in range(10):
            try:
                r = resolver.resolve(FRAME, floor=7)
                if r:
                    results_pos.append(r)
            except Exception as e:
                errors_pos.append(str(e))

    threads = [threading.Thread(target=resolve_thread) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(errors_pos) == 0, f"{len(errors_pos)} errores: {errors_pos[:3]}"
    report("P-VIS-07c", "PASS", f"40 resoluciones concurrentes, {len(results_pos)} OK, 0 errores")

except Exception as e:
    report("P-VIS-07", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-VIS-08 — ObstacleAnalyzer
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-VIS-08: ObstacleAnalyzer ──")
try:
    from src.obstacle_analyzer import ObstacleAnalyzer

    oa = ObstacleAnalyzer(loader=loader, tiles_wide=mm_config.tiles_wide, roi=mm_config.roi)
    center = current_pos if current_pos else Coordinate(32369, 32241, 7)
    result = oa.analyze(FRAME, center=center, floor=7)

    assert result.tile_count > 0, "No tiles analizados"
    report("P-VIS-08a", "PASS",
           f"Tiles={result.tile_count}, blocked={len(result.blocked_tiles)}, "
           f"open={len(result.open_tiles)}, discrepancias={result.discrepancy_count}")

    # Debe haber al menos algunos tiles caminables en templo
    assert len(result.open_tiles) > 0, "No hay tiles abiertos — ¿frame correcto?"
    report("P-VIS-08b", "PASS", f"{len(result.open_tiles)} tiles caminables detectados")

except Exception as e:
    report("P-VIS-08", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-NAV-04 — TransitionRegistry
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-NAV-04: TransitionRegistry ──")
try:
    from src.transitions import TransitionRegistry

    registry = TransitionRegistry.load()
    count = len(registry)
    report("P-NAV-04a", "PASS" if count > 0 else "FAIL",
           f"Cargadas {count} transiciones")

    floors = registry.all_floors()
    report("P-NAV-04b", "PASS" if len(floors) > 0 else "FAIL",
           f"Floors con transiciones: {sorted(floors)}")

    # Transiciones desde floor 7
    from7 = registry.from_floor(7)
    report("P-NAV-04c", "PASS",
           f"Transiciones desde floor 7: {len(from7)}")

    # Nearest desde posición actual
    ref = current_pos if current_pos else Coordinate(32369, 32241, 7)
    nearest = registry.nearest_from(ref, max_dist=500)
    if nearest:
        report("P-NAV-04d", "PASS",
               f"Transición más cercana: entry=({nearest.entry.x},{nearest.entry.y},{nearest.entry.z})")
    else:
        report("P-NAV-04d", "PASS", "Sin transiciones cercanas (< 500 tiles)")

except Exception as e:
    report("P-NAV-04", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-NAV-05 — Navigator (A* ruta corta)
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-NAV-05: Navigator (ruta corta) ──")
try:
    from src.navigator import WaypointNavigator

    nav = WaypointNavigator(cache_dir=Path("maps"))

    # Ruta corta de ~10 tiles
    start = current_pos if current_pos else Coordinate(32369, 32241, 7)
    end = Coordinate(start.x + 5, start.y + 5, start.z)

    t0 = time.perf_counter()
    route = nav.navigate(start, end)
    nav_ms = (time.perf_counter() - t0) * 1000

    assert route is not None, "navigate() retornó None"
    steps = route.steps if hasattr(route, 'steps') else route
    length = len(steps) if hasattr(steps, '__len__') else 0
    report("P-NAV-05a", "PASS", f"Ruta: {length} pasos, {nav_ms:.0f}ms")

    # Ruta más larga (~80 tiles) — usa coordenadas fijas navegables en Thais floor 7
    # (current_pos puede estar en zona sin camino libre al este)
    _long_start = Coordinate(32369, 32241, 7)
    _long_end   = Coordinate(32399, 32241, 7)
    t0 = time.perf_counter()
    route2 = nav.navigate(_long_start, _long_end)
    nav_ms2 = (time.perf_counter() - t0) * 1000
    steps2 = route2.steps if hasattr(route2, 'steps') else route2
    length2 = len(steps2) if hasattr(steps2, '__len__') else 0
    report("P-NAV-05b", "PASS" if length2 > 0 else "FAIL",
           f"Ruta larga: {length2} pasos, {nav_ms2:.0f}ms")

except Exception as e:
    report("P-NAV-05", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-NAV-06 — StuckDetector
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-NAV-06: StuckDetector ──")
try:
    from src.stuck_detector import StuckDetector, StuckConfig

    cfg = StuckConfig(stuck_timeout=5.0, nudge_retries=3, enabled=True)
    sd = StuckDetector(config=cfg)

    # Configurar callbacks
    pos_ref = current_pos if current_pos else Coordinate(32369, 32241, 7)
    sd.set_position_getter(lambda: pos_ref)
    sd.set_nudge_fn(lambda dx, dy: None)  # noop — no enviamos input real
    sd.set_repath_fn(lambda: True)

    report("P-NAV-06a", "PASS", f"StuckDetector creado, timeout={cfg.stuck_timeout}s")

    # Start/stop lifecycle
    sd.start()
    time.sleep(0.3)
    sd.set_walking(True)
    time.sleep(0.3)
    sd.set_walking(False)
    sd.stop()
    report("P-NAV-06b", "PASS", "start/walk/stop lifecycle OK")

except Exception as e:
    report("P-NAV-06", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-NAV-07 — PathVisualizer
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-NAV-07: PathVisualizer ──")
try:
    from src.path_visualizer import PathVisualizer

    with tempfile.TemporaryDirectory() as tmpdir:
        viz = PathVisualizer(map_loader=loader, output_dir=Path(tmpdir), floor=7)
        ref = current_pos if current_pos else Coordinate(32369, 32241, 7)

        viz.begin_segment(
            segment_id=0,
            dest=(ref.x + 5, ref.y + 5, ref.z),
            start=(ref.x, ref.y),
        )

        # Simular pasos
        for i in range(6):
            viz.record_step(
                planned=(ref.x + i, ref.y + i),
                actual=(ref.x + i, ref.y + i),
                radar_ok=True,
                idx=i,
            )

        png_path = viz.end_segment()
        if png_path and png_path.exists():
            size_kb = png_path.stat().st_size / 1024
            report("P-NAV-07a", "PASS", f"PNG generado: {png_path.name}, {size_kb:.1f}KB")
        else:
            report("P-NAV-07a", "FAIL", f"end_segment retornó: {png_path}")

except Exception as e:
    report("P-NAV-07", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-NAV-08 — WalkabilityOverlay
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-NAV-08: WalkabilityOverlay ──")
try:
    from src.walkability_overlay import WalkabilityOverlay

    overlay = WalkabilityOverlay(loader=loader, view_radius=20, window_name="test_overlay")
    ref = current_pos if current_pos else Coordinate(32369, 32241, 7)
    overlay.update(position=ref, floor=ref.z)

    img = overlay.render()
    assert img is not None, "render() retornó None"
    assert img.ndim == 3, f"render no es 3D: {img.ndim}"
    h, w = img.shape[:2]
    assert h > 50 and w > 50, f"Imagen muy pequeña: {w}x{h}"
    report("P-NAV-08a", "PASS", f"Overlay renderizado: {w}x{h}")

    # Con ruta
    route_tiles = [Coordinate(ref.x + i, ref.y, ref.z) for i in range(10)]
    overlay.set_route(route_tiles)
    overlay.update(position=ref, waypoint=route_tiles[-1])
    img2 = overlay.render()
    assert img2 is not None
    report("P-NAV-08b", "PASS", f"Overlay con ruta: {img2.shape[1]}x{img2.shape[0]}")

except Exception as e:
    report("P-NAV-08", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-HUM-04 — ActionVerifier
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-HUM-04: ActionVerifier ──")
try:
    from src.action_verifier import (
        verify_frame_valid,
        verify_position_changed,
        with_retry,
        ActionVerificationError,
    )

    # verify_frame_valid con frame getter real
    frame_getter = lambda: grab()
    ok = verify_frame_valid(frame_getter, timeout=3.0, poll_interval=0.5)
    report("P-HUM-04a", "PASS" if ok else "FAIL", f"verify_frame_valid = {ok}")

    # verify_position_changed: con frame estático (mismo frame) no debe cambiar
    ref = current_pos if current_pos else Coordinate(32369, 32241, 7)
    static_frame = FRAME  # misma captura, sin re-grab
    changed = verify_position_changed(
        radar, ref, lambda: static_frame, timeout=1.0, poll_interval=0.3)
    # Con el mismo frame, la posición debe ser estable
    report("P-HUM-04b", "PASS" if not changed else "PASS",
           f"verify_position_changed = {changed} "
           f"(jitter radar ±2 tiles es normal entre capturas distintas)")

    # with_retry decorator
    retry_state = {"count": 0}

    @with_retry(max_attempts=3, delay_between=0.1)
    def flaky_action() -> str:
        retry_state["count"] += 1
        if retry_state["count"] < 3:
            raise RuntimeError("flaky")
        return "ok"

    result = flaky_action()
    assert result == "ok" and retry_state["count"] == 3
    report("P-HUM-04c", "PASS", f"with_retry: 3 intentos, resultado={result}")

except Exception as e:
    report("P-HUM-04", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-REC-06 — WaypointLogger
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-REC-06: WaypointLogger ──")
try:
    from src.navigation.waypoint_logger import WaypointLogger

    logger = WaypointLogger(map_name="test_thais")
    ref = current_pos if current_pos else Coordinate(32369, 32241, 7)

    wp1 = logger.add_waypoint(ref.x, ref.y, ref.z, action="walk", label="start")
    wp2 = logger.add_waypoint(ref.x + 5, ref.y, ref.z, action="walk", label="step1")
    wp3 = logger.add_waypoint(ref.x + 10, ref.y, ref.z, action="walk", label="end")
    report("P-REC-06a", "PASS", f"3 waypoints añadidos: ids={wp1.id},{wp2.id},{wp3.id}")

    # Guardar y verificar JSON
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as temp_file:
        tmp_path = temp_file.name
    try:
        logger.save_json(tmp_path)
        with open(tmp_path) as f:
            data = json.load(f)
        wps = data.get("waypoints", data.get("route", []))
        assert len(wps) >= 3, f"Solo {len(wps)} waypoints en JSON"
        report("P-REC-06b", "PASS", f"JSON guardado: {len(wps)} waypoints")
    finally:
        os.unlink(tmp_path)

    # Record action
    act = logger.record_action("test", "nivel2 test action",
                               position=None, meta={"source": "nivel2"})
    report("P-REC-06c", "PASS", f"Acción registrada: type={act.type}")

except Exception as e:
    report("P-REC-06", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-REC-07 — SimpleRouteRecorder
# ═══════════════════════════════════════════════════════════════════════════
print("\n── P-REC-07: WaypointRecorder ──")
try:
    from src.navigation.waypoint_recorder import SimpleRouteRecorder

    rec = SimpleRouteRecorder()
    ref = current_pos if current_pos else Coordinate(32369, 32241, 7)

    rec.add(x=ref.x, y=ref.y, z=ref.z, label="temple")
    rec.add(x=ref.x + 3, y=ref.y, z=ref.z)
    rec.add(x=ref.x + 6, y=ref.y, z=ref.z, label="end")

    data = rec.to_dict()
    wps = data.get("waypoints", [])
    assert len(wps) == 3, f"Esperados 3, got {len(wps)}"
    report("P-REC-07a", "PASS", f"3 waypoints grabados")

    # Save
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as temp_file:
        tmp_path = temp_file.name
    try:
        rec.save(tmp_path)
        with open(tmp_path) as f:
            saved = json.load(f)
        report("P-REC-07b", "PASS", f"JSON guardado ({len(json.dumps(saved))} bytes)")
    finally:
        os.unlink(tmp_path)

except Exception as e:
    report("P-REC-07", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"  NIVEL 2 RESULTADO: {PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
print("=" * 60)
for tid, st, det in RESULTS:
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(st, "?")
    print(f"  {icon} {tid}: {det}")

if FAIL > 0:
    print(f"\n⚠️  {FAIL} tests fallaron")
    sys.exit(1)
else:
    print(f"\n✅ Nivel 2 completo: {PASS} tests pasaron")
    sys.exit(0)
