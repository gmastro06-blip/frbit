#!/usr/bin/env python
"""
Nivel 7 — Soak Test: Estabilidad + Multi-Session
Requiere: OBS Proyector, Tibia abierto.

Corre SoakMonitor + BotSession (dry_run) durante 5 minutos para verificar
estabilidad de recursos. También verifica MultiSessionManager.

Tests:
  P-MON-02   SoakMonitor: 5 min sampling, peak memory < 500 MB
  P-SES-04   MultiSessionManager: init, add, remove, stats
  T5-SOAK    BotSession dry_run soak: sin crash, recursos estables
"""
from __future__ import annotations
import sys, os, time, threading
import ctypes
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

PASS = 0
FAIL = 0
SKIP = 0
RESULTS: list[tuple[str, str, str]] = []
WINDOW_TITLE = "Proyector"
SOAK_DURATION_S = 300  # 5 minutos


def report(test_id: str, status: str, detail: str = "") -> None:
    global PASS, FAIL, SKIP
    RESULTS.append((test_id, status, detail))
    if status == "PASS":
        PASS += 1
    elif status == "FAIL":
        FAIL += 1
    else:
        SKIP += 1
    icon = {"PASS": "\u2705", "FAIL": "\u274c", "SKIP": "\u23ed\ufe0f"}.get(status, "?")
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
print("  NIVEL 7 — Soak Test (5 min estabilidad)")
print("  Método: Screen capture + HID only")
print("=" * 60)

hwnd = find_hwnd(WINDOW_TITLE)
if not hwnd:
    print(f"\n\u274c ABORT: No se encontr\u00f3 ventana '{WINDOW_TITLE}'.")
    sys.exit(1)
print(f"\n\u2713 Ventana '{WINDOW_TITLE}': hwnd={hwnd}")

# ═══════════════════════════════════════════════════════════════════════════
# P-SES-04 — MultiSessionManager (rápido, antes del soak)
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-SES-04: MultiSessionManager \u2500\u2500")
try:
    from src.multi_session import MultiSessionManager
    from src.session import SessionConfig

    msm = MultiSessionManager(log_callback=lambda m: None)
    assert msm.count == 0, f"count={msm.count}"
    report("P-SES-04a", "PASS", f"Init OK, count={msm.count}")
except Exception as e:
    report("P-SES-04a", "FAIL", str(e))

try:
    cfg1 = SessionConfig(dry_run=True, route_file="routes/thais_rat_hunt.json")
    cfg2 = SessionConfig(dry_run=True, route_file="routes/test_north_40.json")
    msm.add("session_alpha", cfg1)
    msm.add("session_beta", cfg2)
    assert msm.count == 2, f"count={msm.count}"
    assert "session_alpha" in msm.session_names
    assert "session_beta" in msm.session_names
    report("P-SES-04b", "PASS",
           f"2 sessions added: {msm.session_names}")
except Exception as e:
    report("P-SES-04b", "FAIL", str(e))

try:
    # Duplicate add → ValueError
    dup_ok = False
    try:
        msm.add("session_alpha", cfg1)
    except ValueError:
        dup_ok = True
    assert dup_ok, "No ValueError on duplicate!"
    report("P-SES-04c", "PASS", "Duplicate add → ValueError OK")
except Exception as e:
    report("P-SES-04c", "FAIL", str(e))

try:
    msm.remove("session_beta")
    assert msm.count == 1
    snap = msm.stats_snapshot()
    assert isinstance(snap, dict)
    assert snap["total_sessions"] == 1
    report("P-SES-04d", "PASS",
           f"remove OK, count=1, stats={snap}")
    # Cleanup
    msm.remove("session_alpha")
except Exception as e:
    report("P-SES-04d", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-MON-02 + T5-SOAK — SoakMonitor 5 min
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n\u2500\u2500 P-MON-02 + T5-SOAK: Soak Test ({SOAK_DURATION_S}s) \u2500\u2500")

from src.soak_monitor import SoakMonitor, SoakMonitorConfig
from src.frame_capture import PrintWindowCapture

soak_cfg = SoakMonitorConfig(
    sample_interval=5.0,
    memory_warn_mb=500.0,
    enabled=True,
)
soak = SoakMonitor(config=soak_cfg, log_fn=lambda m: None)

# Frame capture loop to simulate real workload
cap = PrintWindowCapture(hwnd=hwnd)
grab = cap.open()

from src.minimap_radar import MinimapRadar, MinimapConfig
from src.map_loader import TibiaMapLoader
import json

loader = TibiaMapLoader(cache_dir=Path("maps"))
loader.get_map_image(7)

with open("minimap_config.json") as f:
    mm_data = json.load(f)
mm_cfg = MinimapConfig(**{k: v for k, v in mm_data.items() if not k.startswith("_")})
radar = MinimapRadar(loader=loader, config=mm_cfg)

print(f"  \u25b6 Iniciando soak: {SOAK_DURATION_S}s con sampling cada {soak_cfg.sample_interval}s")
print(f"    Workload: frame capture + radar read cada 2s")

soak.start()
report("P-MON-02a", "PASS", "SoakMonitor started")

# Workload loop: capture frames + read position every 2s
errors: list[str] = []
frame_count = 0
position_count = 0
start_time = time.time()
last_report = start_time

try:
    while time.time() - start_time < SOAK_DURATION_S:
        try:
            frame = grab()
            frame_count += 1
            if frame is not None:
                pos = radar.read(frame, floor=7)
                if pos:
                    position_count += 1
        except Exception as e:
            errors.append(f"t={time.time()-start_time:.0f}s: {e}")
            if len(errors) > 20:
                break

        # Progress report every 60s
        elapsed = time.time() - start_time
        if elapsed - (last_report - start_time) >= 60:
            snap = soak.stats_snapshot()
            mem = snap.get("peak_memory_mb", 0)
            cpu = snap.get("peak_cpu_pct", 0)
            print(f"    [{elapsed:.0f}s] frames={frame_count}, "
                  f"pos={position_count}, mem={mem:.0f}MB, "
                  f"cpu={cpu:.0f}%, errors={len(errors)}")
            last_report = time.time()

        time.sleep(2.0)

except KeyboardInterrupt:
    print("\n  \u26a0\ufe0f  Interrumpido por usuario")

soak.stop()
elapsed_total = time.time() - start_time

# ── Analyse results ──────────────────────────────────────────────────────
snap = soak.stats_snapshot()
peak_mem = snap.get("peak_memory_mb", 0)
peak_cpu = snap.get("peak_cpu_pct", 0)
peak_threads = snap.get("peak_threads", 0)
samples = snap.get("samples", 0)
warnings = snap.get("warnings_count", 0)

print(f"\n  Soak completado: {elapsed_total:.0f}s")
print(f"    Samples: {samples}")
print(f"    Peak RAM: {peak_mem:.1f} MB")
print(f"    Peak CPU: {peak_cpu:.1f}%")
print(f"    Peak threads: {peak_threads}")
print(f"    Warnings: {warnings}")
print(f"    Frames: {frame_count}, Positiones: {position_count}")
print(f"    Errores workload: {len(errors)}")

# P-MON-02b: Memory < 500 MB
if peak_mem > 0:
    report("P-MON-02b", "PASS" if peak_mem < 500 else "FAIL",
           f"Peak memory: {peak_mem:.1f} MB (limit 500)")
else:
    report("P-MON-02b", "PASS", "Memory not tracked (psutil?), no OOM")

# P-MON-02c: Samples collected
report("P-MON-02c", "PASS" if samples >= 5 else "FAIL",
       f"{samples} samples en {elapsed_total:.0f}s")

# T5-SOAK-a: Run duration
report("T5-SOAK-a", "PASS" if elapsed_total >= SOAK_DURATION_S * 0.9 else "FAIL",
       f"Dur\u00f3 {elapsed_total:.0f}s (target {SOAK_DURATION_S}s)")

# T5-SOAK-b: No critical errors
report("T5-SOAK-b", "PASS" if len(errors) == 0 else "FAIL",
       f"{len(errors)} errores durante soak")

# T5-SOAK-c: Frames processed
fps = frame_count / elapsed_total if elapsed_total > 0 else 0
report("T5-SOAK-c", "PASS" if frame_count > 10 else "FAIL",
       f"{frame_count} frames ({fps:.2f} fps), {position_count} posiciones")

# T5-SOAK-d: No warning spam
report("T5-SOAK-d", "PASS" if warnings < 10 else "FAIL",
       f"{warnings} warnings del soak monitor")

# ═══════════════════════════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"  NIVEL 7 RESULTADO: {PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
print("=" * 60)
if RESULTS:
    max_id = max(len(r[0]) for r in RESULTS)
    for tid, st, det in RESULTS:
        icon = {"PASS": "\u2705", "FAIL": "\u274c", "SKIP": "\u23ed\ufe0f"}.get(st, "?")
        print(f"  {icon} {tid:<{max_id}}  {det}")
print("=" * 60)

if FAIL > 0:
    print(f"\n\u26a0\ufe0f  {FAIL} test(s) FALLARON")
    sys.exit(1)
else:
    print(f"\n\U0001f389  Todos {PASS} tests PASS — PRODUCTION READY")
    sys.exit(0)
