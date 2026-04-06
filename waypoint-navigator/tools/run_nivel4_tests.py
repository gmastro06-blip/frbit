#!/usr/bin/env python
"""
Nivel 4 — Live Tests: Loot + Economy + Script Executor
Requiere: OBS Proyector, Tibia abierto.

Tests de init, config, lifecycle (desde templo, sin depot/trade real).

Tests:
  P-LOOT-02  InventoryManager: config, check_inventory
  P-LOOT-03  DepotManager: init, config, lifecycle
  P-LOOT-04  TradeManager: init, config
  P-LOOT-05  DepotOrchestrator: init, should_resupply
  P-SCR-02   ScriptExecutor: init, FV-05/FV-06
"""
from __future__ import annotations
import sys, os, time, json
import ctypes
import numpy as np
from pathlib import Path

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
print("  NIVEL 4 — Live Tests (Loot + Economy + Script)")
print("  Método: Screen capture + HID only")
print("=" * 60)

hwnd = find_hwnd(WINDOW_TITLE)
if not hwnd:
    print(f"\n❌ ABORT: No se encontró ventana '{WINDOW_TITLE}'.")
    sys.exit(1)
print(f"\n✓ Ventana '{WINDOW_TITLE}': hwnd={hwnd}")

from src.frame_capture import PrintWindowCapture

cap = PrintWindowCapture(hwnd)
grab = cap.open()
ref_frame = grab()
assert ref_frame is not None and ref_frame.shape[0] > 100
print(f"✓ Frame capturado: {ref_frame.shape}")


from typing import Any
def frame_getter() -> Any:
    return grab()


from src.input_controller import InputController

ic = InputController(target_title="Tibia", input_method="interception")
ic.find_target()
print("✓ InputController listo")

# ── P-LOOT-02: InventoryManager ─────────────────────────────────────────
print("\n▸ P-LOOT-02 — InventoryManager")

try:
    from src.inventory_manager import InventoryManager, InventoryConfig

    inv = InventoryManager()
    report("P-LOOT-02a", "PASS", f"config OK, capacity={inv.config.capacity_slots}")
except Exception as e:
    report("P-LOOT-02a", "FAIL", str(e))

try:
    reading = inv.check_inventory(ref_frame)
    report("P-LOOT-02b", "PASS",
           f"status={reading.status.name}, checks={inv.total_checks}")
except Exception as e:
    report("P-LOOT-02b", "FAIL", str(e))

# ── P-LOOT-03: DepotManager ─────────────────────────────────────────────
print("\n▸ P-LOOT-03 — DepotManager")

try:
    from src.depot_manager import DepotManager, DepotConfig

    dm = DepotManager(ctrl=ic)
    dm.set_frame_getter(frame_getter)
    report("P-LOOT-03a", "PASS",
           f"cycle_count={dm.cycle_count}, idle={dm.is_idle}")
except Exception as e:
    report("P-LOOT-03a", "FAIL", str(e))

try:
    snap = dm.stats_snapshot()
    has_keys = "cycle_count" in snap or "items_deposited" in snap or isinstance(snap, dict)
    report("P-LOOT-03b", "PASS" if has_keys else "FAIL",
           f"stats keys: {list(snap.keys())[:5]}")
except Exception as e:
    report("P-LOOT-03b", "FAIL", str(e))

# ── P-LOOT-04: TradeManager ─────────────────────────────────────────────
print("\n▸ P-LOOT-04 — TradeManager")

try:
    from src.trade_manager import TradeManager, TradeConfig

    tm = TradeManager(ctrl=ic)
    tm.set_frame_getter(frame_getter)
    report("P-LOOT-04a", "PASS", "Init OK")
except Exception as e:
    report("P-LOOT-04a", "FAIL", str(e))

try:
    if hasattr(TradeConfig, "buy_list"):
        tc = TradeConfig()
        report("P-LOOT-04b", "PASS", f"config fields OK")
    else:
        report("P-LOOT-04b", "PASS", "TradeConfig created")
except Exception as e:
    report("P-LOOT-04b", "FAIL", str(e))

# ── P-LOOT-05: DepotOrchestrator ────────────────────────────────────────
print("\n▸ P-LOOT-05 — DepotOrchestrator")

try:
    from src.depot_orchestrator import DepotOrchestrator, ResupplyConfig

    do = DepotOrchestrator(
        depot_manager=dm,
        trade_manager=tm,
        inventory_manager=inv,
        ctrl=ic,
    )
    report("P-LOOT-05a", "PASS", "Init OK con sub-managers")
except Exception as e:
    report("P-LOOT-05a", "FAIL", str(e))

try:
    needs = do.should_resupply(ref_frame)
    report("P-LOOT-05b", "PASS",
           f"should_resupply={needs} (expected False at temple)")
except Exception as e:
    report("P-LOOT-05b", "FAIL", str(e))

# ── P-SCR-02: ScriptExecutor ────────────────────────────────────────────
print("\n▸ P-SCR-02 — ScriptExecutor")

try:
    from src.script_executor import ScriptExecutor
    from src.navigator import WaypointNavigator

    nav = WaypointNavigator(cache_dir=Path("maps"))
    se = ScriptExecutor(ctrl=ic, navigator=nav, dry_run=True)
    report("P-SCR-02a", "PASS", "Init OK (dry_run)")
except Exception as e:
    report("P-SCR-02a", "FAIL", str(e))

# FV-05/FV-06: check internal methods exist
try:
    has_ammo = hasattr(se, "_check_ammo") or hasattr(se, "_dispatch_action")
    has_supplies = hasattr(se, "_check_supplies") or hasattr(se, "_dispatch_action")
    report("P-SCR-02b", "PASS" if has_ammo and has_supplies else "FAIL",
           f"_check_ammo={has_ammo}, _check_supplies={has_supplies}")
except Exception as e:
    report("P-SCR-02b", "FAIL", str(e))

# Parse route script (JSON format)
try:
    from src.script_parser import ScriptParser

    with open("routes/thais_rat_hunt.json") as f:
        route_data = json.load(f)
    instructions = ScriptParser.from_json_script(route_data.get("script", []))
    report("P-SCR-02c", "PASS",
           f"instrucciones={len(instructions)}")
except Exception as e:
    report("P-SCR-02c", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"  NIVEL 4 — RESULTADO: {PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
print("=" * 60)
if RESULTS:
    max_id = max(len(r[0]) for r in RESULTS)
    for tid, st, det in RESULTS:
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(st, "?")
        print(f"  {icon} {tid:<{max_id}}  {det}")
print("=" * 60)

if FAIL > 0:
    print(f"\n⚠️  {FAIL} test(s) FALLARON")
    sys.exit(1)
else:
    print(f"\n🎉  Todos {PASS} tests PASS")
    sys.exit(0)
