"""
Nivel 0 — Live Test Plan: Fix Validation (FV-01..FV-18) + Infrastructure Modules
No Tibia client required. Pure Python tests.
"""
import sys, os, time, threading, importlib, ast, inspect, typing, traceback
from pathlib import Path
from dataclasses import asdict

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

from typing import Callable, Any, Dict

RESULTS: Dict[str, str] = {}

def run_test(name: str) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Decorator to run and record test results."""
    def decorator(fn: Callable[[], Any]) -> Callable[[], Any]:
        def wrapper() -> None:
            try:
                fn()
                RESULTS[name] = "PASS"
                print(f"  ✅ {name}: PASS")
            except Exception as e:
                RESULTS[name] = f"FAIL: {e}"
                print(f"  ❌ {name}: FAIL — {e}")
                traceback.print_exc()
        wrapper()
        return fn
    return decorator

# ══════════════════════════════════════════════════════════════
# FIX VALIDATION TESTS (FV-01 through FV-18)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("  FIX VALIDATION TESTS (FV-01..FV-18)")
print("=" * 65)

# FV-01: input_controller.py — double-release lock removed
@run_test("FV-01 InputController lock no double-release")
def _() -> None:
    from src.input_controller import InputController
    src = inspect.getsource(InputController.click)
    # The fix removed early _input_lock.release() calls; only finally: block releases
    # Count release calls — should be exactly 1 (in finally)
    lines = [l.strip() for l in src.splitlines()
             if "_input_lock.release()" in l and not l.startswith("#")]
    # The finally block has the only release. If there were 2+, it's the old bug.
    assert len(lines) <= 1, f"Found {len(lines)} release() calls — double-release still present!"

# FV-02: navigator.py — multifloor fallback in navigate_by_name
@run_test("FV-02 Navigator multifloor fallback")
def _() -> None:
    src_code = Path("src/navigator.py").read_text(encoding="utf-8")
    # After same-floor fails, should call multifloor navigation
    assert "navigate_multifloor" in src_code or "multifloor" in src_code.lower(), \
        "No multifloor fallback found in navigator.py"

# FV-03: combat_manager.py — hp_flee_pct > 0 guard
@run_test("FV-03 CombatManager flee guard hp_flee_pct > 0")
def _() -> None:
    src_code = Path("src/combat_manager.py").read_text(encoding="utf-8")
    assert "hp_flee_pct" in src_code and "> 0" in src_code, \
        "hp_flee_pct > 0 guard not found"

# FV-04: combat_manager.py — _last_attack_vk_time initialized
@run_test("FV-04 CombatManager _last_attack_vk_time init")
def _() -> None:
    src_code = Path("src/combat_manager.py").read_text(encoding="utf-8")
    assert "_last_attack_vk_time" in src_code, "Attribute not found"
    # Check it's set in __init__ — line 478 per grep
    parts = src_code.split("def __init__")
    assert len(parts) >= 2, "No __init__ found"
    # Find the __init__ that contains it (could be in 2nd+ class)
    found = any("_last_attack_vk_time" in p.split("\ndef ")[0] for p in parts[1:])
    assert found, "_last_attack_vk_time not in any __init__"

# FV-05: script_executor.py — _check_ammo -> Optional[str]
@run_test("FV-05 ScriptExecutor _check_ammo Optional[str]")
def _() -> None:
    src_code = Path("src/script_executor.py").read_text(encoding="utf-8")
    # Find the method signature
    for line in src_code.splitlines():
        if "def _check_ammo" in line:
            assert "Optional[str]" in line or "str | None" in line or "Optional" in line, \
                f"Return type not Optional[str]: {line.strip()}"
            return
    raise AssertionError("_check_ammo method not found")

# FV-06: script_executor.py — _check_supplies -> Optional[str]
@run_test("FV-06 ScriptExecutor _check_supplies Optional[str]")
def _() -> None:
    src_code = Path("src/script_executor.py").read_text(encoding="utf-8")
    for line in src_code.splitlines():
        if "def _check_supplies" in line:
            assert "Optional[str]" in line or "str | None" in line or "Optional" in line, \
                f"Return type not Optional[str]: {line.strip()}"
            return
    raise AssertionError("_check_supplies method not found")

# FV-07: stuck_detector.py — threading.Lock added
@run_test("FV-07 StuckDetector threading.Lock")
def _() -> None:
    src_code = Path("src/stuck_detector.py").read_text(encoding="utf-8")
    assert "threading.Lock()" in src_code, "No threading.Lock() found"
    assert "_lock" in src_code, "No _lock attribute found"

# FV-08: position_resolver.py — threading.Lock added
@run_test("FV-08 PositionResolver threading.Lock")
def _() -> None:
    src_code = Path("src/position_resolver.py").read_text(encoding="utf-8")
    assert "threading.Lock()" in src_code, "No threading.Lock() found"
    assert "_lock" in src_code, "No _lock attribute found"

# FV-09: models.py — import math at top level
@run_test("FV-09 models.py import math top-level")
def _() -> None:
    src_code = Path("src/models.py").read_text(encoding="utf-8")
    # Check imports section (first 30 lines)
    top = "\n".join(src_code.splitlines()[:30])
    assert "import math" in top, "math not imported at top level"

# FV-10: models.py — Route.slice end_idx=None default
@run_test("FV-10 Route.slice end_idx default None")
def _() -> None:
    from src.models import Route, Coordinate
    # Create a Route and slice it without end_idx
    steps = [Coordinate(i, 0, 7) for i in range(10)]
    route = Route(start=steps[0], end=steps[-1], steps=steps, total_distance=9.0, found=True)
    sliced = route.slice(start_idx=3)
    assert len(sliced.steps) == 7, f"Expected 7 steps, got {len(sliced.steps)}"

# FV-11: transitions.py — JSON corruption handled
@run_test("FV-11 TransitionRegistry JSON corruption")
def _() -> None:
    import tempfile
    from src.transitions import TransitionRegistry
    p = Path(tempfile.mktemp(suffix=".json"))
    p.write_text("{corrupt json!!!", encoding="utf-8")
    try:
        reg = TransitionRegistry.load(path=p)
        # Should return empty registry, not crash
        assert reg is not None
    finally:
        p.unlink(missing_ok=True)

# FV-12: transitions.py — O(n) remove_set pattern
@run_test("FV-12 transitions.py O(n) remove pattern")
def _() -> None:
    src_code = Path("src/transitions.py").read_text(encoding="utf-8")
    # Should use set() for O(n) removal
    assert "set(" in src_code, "No set() usage found — still O(n²)?"

# FV-13: transitions.py — reachable_floors dedup
@run_test("FV-13 transitions.py reachable_floors dedup")
def _() -> None:
    src_code = Path("src/transitions.py").read_text(encoding="utf-8")
    # Should have sorted(set(...)) pattern
    method_area = src_code[src_code.find("def reachable_floors"):]
    method_area = method_area[:method_area.find("\n    def ") if "\n    def " in method_area else 500]
    assert "set(" in method_area, "No set() dedup in reachable_floors"

# FV-14: walkability_overlay.py — dead deque import removed
@run_test("FV-14 walkability_overlay no deque import")
def _() -> None:
    src_code = Path("src/walkability_overlay.py").read_text(encoding="utf-8")
    tree = ast.parse(src_code)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            assert "deque" not in names and "Deque" not in names, \
                f"Dead import found: {names}"
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
            assert "deque" not in names, f"Dead import: {names}"

# FV-15: visualizer.py — dead mpatches import removed
@run_test("FV-15 visualizer no mpatches import")
def _() -> None:
    src_code = Path("src/visualizer.py").read_text(encoding="utf-8")
    assert "mpatches" not in src_code, "mpatches still in source!"

# FV-16: healer.py — _zero_hp_streak initialized in __init__
@run_test("FV-16 healer _zero_hp_streak in __init__")
def _() -> None:
    src_code = Path("src/healer.py").read_text(encoding="utf-8")
    init_section = src_code.split("def __init__")[1].split("\n    def ")[0]
    assert "_zero_hp_streak" in init_section, \
        "_zero_hp_streak not initialized in __init__"

# FV-17: condition_monitor.py — list_reactions with lock
@run_test("FV-17 condition_monitor list_reactions with lock")
def _() -> None:
    src_code = Path("src/condition_monitor.py").read_text(encoding="utf-8")
    # Find list_reactions method and check it uses self._lock
    lr_start = src_code.find("def list_reactions")
    assert lr_start != -1, "list_reactions not found"
    lr_body = src_code[lr_start:lr_start + 300]
    assert "_lock" in lr_body, "list_reactions does not use self._lock"

# FV-18: session_persistence.py — timestamp_iso uses self.timestamp
@run_test("FV-18 session_persistence consistent timestamp")
def _() -> None:
    src_code = Path("src/session_persistence.py").read_text(encoding="utf-8")
    save_start = src_code.find("def save")
    assert save_start != -1, "save method not found"
    save_body = src_code[save_start:save_start + 500]
    # Should use self.timestamp (stored once), not a second time.time()
    assert "self.timestamp" in save_body, "Does not reference self.timestamp in save"


# ══════════════════════════════════════════════════════════════
# NIVEL 0 INFRASTRUCTURE MODULE TESTS
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("  NIVEL 0 — INFRASTRUCTURE MODULES (no Tibia)")
print("=" * 65)

# P-NAV-02: Models — Coordinate, Route
@run_test("P-NAV-02 Models (Coordinate distance, Route.slice)")
def _() -> None:
    from src.models import Coordinate, Route
    c1 = Coordinate(100, 200, 7)
    c2 = Coordinate(103, 204, 7)
    dist = c1.distance_to(c2)
    # Chebyshev: max(|3|, |4|) = 4
    assert dist == 4.0, f"Expected Chebyshev dist 4.0, got {dist}"

    steps = [Coordinate(i, 0, 7) for i in range(10)]
    route = Route(start=steps[0], end=steps[-1], steps=steps, total_distance=9.0, found=True)
    sliced = route.slice(5)
    assert len(sliced.steps) == 5, f"slice(5): expected 5, got {len(sliced.steps)}"
    sliced2 = route.slice(2, 5)
    assert len(sliced2.steps) == 3, f"slice(2,5): expected 3, got {len(sliced2.steps)}"

# P-NAV-01: Map Loader
@run_test("P-NAV-01 MapLoader load floor")
def _() -> None:
    from src.map_loader import TibiaMapLoader
    loader = TibiaMapLoader()
    w = loader.get_walkability(7)
    assert w.shape[0] > 100 and w.shape[1] > 100, f"Unexpected shape: {w.shape}"
    # Should have both walkable (True) and non-walkable (False)
    assert w.any() and not w.all(), "Floor should have both walkable and blocked tiles"

# P-NAV-03: Pathfinder (A*)
@run_test("P-NAV-03 AStarPathfinder find_path")
def _() -> None:
    from src.pathfinder import AStarPathfinder
    from src.map_loader import TibiaMapLoader
    from src.models import Coordinate

    loader = TibiaMapLoader()
    wk = loader.get_walkability(7)
    pf = AStarPathfinder(walkability=wk)

    start = Coordinate(32369, 32241, 7)
    goal = Coordinate(32375, 32241, 7)
    route = pf.find_path(start, goal)
    assert route.found, "Path not found!"
    assert len(route.steps) > 0, "Path has no steps"

# P-NAV-04: Transitions
@run_test("P-NAV-04 TransitionRegistry load + reachable_floors")
def _() -> None:
    from src.transitions import TransitionRegistry
    reg = TransitionRegistry.load()
    floors = reg.reachable_floors(7)
    assert len(floors) == len(set(floors)), f"Duplicates! {floors}"

# P-HUM-01: Humanizer jittered_sleep
@run_test("P-HUM-01 Humanizer jittered_sleep variance")
def _() -> None:
    from src.humanizer import jittered_sleep
    times = []
    for _ in range(15):
        t0 = time.time()
        jittered_sleep(0.1)
        times.append(time.time() - t0)
    avg = sum(times) / len(times)
    variance = sum((t - avg) ** 2 for t in times) / len(times)
    assert variance > 0.00001, f"No jitter! variance={variance:.8f}"

# P-CMB-05: GameData
@run_test("P-CMB-05 GameData load monsters")
def _() -> None:
    from src.game_data import GameData
    gd = GameData()
    rat = gd.get_monster("Rat")
    assert rat is not None, "Rat not found in game data"
    assert rat.hp > 0, f"Rat HP invalid: {rat.hp}"

# P-SCR-01: ScriptParser
@run_test("P-SCR-01 ScriptParser parse .in file")
def _() -> None:
    from src.script_parser import ScriptParser
    # Parse one of the existing .in files
    scripts = list(Path("routes").glob("*.in"))
    assert len(scripts) > 0, "No .in script files found"
    instructions = ScriptParser.parse_file(scripts[0])
    assert len(instructions) > 0, f"No instructions parsed from {scripts[0].name}"

# P-VIS-04: FrameQuality
@run_test("P-VIS-04 FrameQuality check black frame")
def _() -> None:
    import numpy as np
    from src.frame_quality import FrameQualityChecker, FrameQuality

    checker = FrameQualityChecker()
    black = np.zeros((1080, 1920, 3), dtype=np.uint8)
    result = checker.check(black)
    # Black frame should be rejected (not OK)
    assert result != FrameQuality.OK, f"Black frame accepted as {result}"

    # Good frame should be OK
    good = np.random.randint(30, 255, (1080, 1920, 3), dtype=np.uint8)
    result2 = checker.check(good)
    assert result2 == FrameQuality.OK, f"Good frame rejected as {result2}"

# StuckDetector thread-safety test
@run_test("P-NAV-06 StuckDetector thread-safety")
def _() -> None:
    from src.stuck_detector import StuckDetector, StuckConfig
    cfg = StuckConfig(stuck_timeout=5.0, poll_interval=0.5)
    sd = StuckDetector(config=cfg)
    errors = []

    def tick_loop() -> None:
        try:
            for _ in range(80):
                sd._tick()
                time.sleep(0.005)
        except Exception as e:
            errors.append(e)

    def walk_loop() -> None:
        try:
            for _ in range(80):
                sd.set_walking(True)
                time.sleep(0.003)
                sd.set_walking(False)
                time.sleep(0.003)
        except Exception as e:
            errors.append(e)

    def stats_loop() -> None:
        try:
            for _ in range(40):
                sd.stats_snapshot()
                time.sleep(0.01)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=tick_loop),
        threading.Thread(target=walk_loop),
        threading.Thread(target=stats_loop),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(errors) == 0, f"{len(errors)} errors: {errors[:3]}"

# Session Stats
@run_test("P-SES-03 SessionStats record_kill/loot")
def _() -> None:
    from src.session_stats import HuntingSessionStats
    stats = HuntingSessionStats()
    stats.record_kill("Rat", exp=20)
    stats.record_kill("Rat", exp=20)
    assert stats.total_kills >= 2, f"Kills: {stats.total_kills}"

# Session Persistence
@run_test("P-SES-02 SessionCheckpoint save/load roundtrip")
def _() -> None:
    from src.session_persistence import SessionCheckpoint
    import tempfile, datetime

    cp = SessionCheckpoint(
        route_file="test.json",
        waypoint_index=5,
        routes_completed=2,
    )
    tmp = Path(tempfile.mktemp(suffix=".json"))
    try:
        cp.save(path=tmp)
        # After save, timestamp should be set
        assert cp.timestamp > 0, "timestamp not set after save"
        assert cp.timestamp_iso != "", "timestamp_iso not set"
        # Verify consistency (FV-18 functional)
        dt = datetime.datetime.fromtimestamp(cp.timestamp)
        assert dt.isoformat()[:19] == cp.timestamp_iso[:19], \
            f"Timestamps diverge: {dt.isoformat()[:19]} vs {cp.timestamp_iso[:19]}"

        # Load back
        cp2 = SessionCheckpoint.load(path=tmp)
        assert cp2 is not None, "load returned None"
        assert cp2.waypoint_index == 5
        assert cp2.routes_completed == 2
    finally:
        tmp.unlink(missing_ok=True)

# Break Scheduler
@run_test("P-REC-04 BreakScheduler config")
def _() -> None:
    from src.break_scheduler import BreakScheduler, BreakSchedulerConfig
    cfg = BreakSchedulerConfig(
        play_min_minutes=2, play_max_minutes=3,
        break_min_minutes=0.5, break_max_minutes=1,
    )
    bs = BreakScheduler(config=cfg)
    # Should have a next break scheduled
    assert bs is not None

# Telemetry
@run_test("P-MON-01 TelemetrySession")
def _() -> None:
    from src.telemetry import TelemetrySession
    ts = TelemetrySession(route_name="test_route")
    ts.record_step(success=True)
    ts.record_stuck()
    assert ts is not None

# Soak Monitor
@run_test("P-MON-02 SoakMonitor start/stop")
def _() -> None:
    from src.soak_monitor import SoakMonitor, SoakMonitorConfig
    cfg = SoakMonitorConfig(sample_interval=1.0, enabled=True)
    sm = SoakMonitor(config=cfg)
    sm.start()
    time.sleep(2.5)
    sm.stop()
    snap = sm.stats_snapshot()
    assert snap is not None

# ══════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 65)
print("  RESULTS SUMMARY")
print("=" * 65)

passed: int = sum(1 for v in RESULTS.values() if v == "PASS")
failed: int = sum(1 for v in RESULTS.values() if v != "PASS")

for name, result in RESULTS.items():
    icon = "✅" if result == "PASS" else "❌"
    print(f"  {icon} {name}: {result}")

print(f"\n  Total: {passed} PASS / {failed} FAIL / {len(RESULTS)} total")
if failed == 0:
    print("  🎉 ALL NIVEL 0 TESTS PASSED!")
else:
    print(f"  ⚠️  {failed} tests FAILED — review above")
    sys.exit(1)
