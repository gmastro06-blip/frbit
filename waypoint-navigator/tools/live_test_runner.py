#!/usr/bin/env python
"""
Live Test Runner — T1..T5
=========================
Automated live test scenarios against a real Tibia client.
Each test is self-contained and can be run independently.

Prerequisites:
  - Tibia client open, character at Thais Temple (32369,32241,7)
  - OBS Projector window "Proyector" visible
  - Pico 2 connected on COM4
  - Hotkeys: F1=heal, F2=mana, F3=emergency, F7-F10=combat
  - combat_config.json, hpmp_config.json, minimap_config.json present

Usage:
  python tools/live_test_runner.py t1          # Navigation only
  python tools/live_test_runner.py t2          # Combat + heal + loot
  python tools/live_test_runner.py t3          # Death recovery
  python tools/live_test_runner.py t4          # Reconnect
  python tools/live_test_runner.py t5          # AFK soak (30 min)
  python tools/live_test_runner.py all         # Run T1-T5 sequentially
  python tools/live_test_runner.py preflight   # Just run checks
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
CHECKPOINT_PATH = OUTPUT_DIR / "session_checkpoint.json"
sys.path.insert(0, str(ROOT))

# ── Common CLI base ──────────────────────────────────────────────────────────
PYTHON = sys.executable
MAIN   = str(ROOT / "main.py")
DEFAULT_ROUTE = "routes/thais_rat_hunt.json"
ACTIVE_ROUTE = DEFAULT_ROUTE
ACTIVE_START_POS = "32369,32241,7"
INTERACTIVE_MODE = True
T1_START_MISMATCH_LIMIT = 6


def _banner(title: str) -> None:
    width = 65
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width + "\n")


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _status_from_code(code: int) -> str:
    if code == 0:
        return "PASS"
    if code == -1:
        return "TIMEOUT"
    if code == -2:
        return "INTERRUPTED"
    return "FAIL"


def _overall_exit_code(results: list[dict[str, Any]]) -> int:
    for item in results:
        code = int(item["exit_code"])
        if code != 0:
            return code
    return 0


def _resolve_route(route: str) -> Path:
    path = Path(route)
    return path if path.is_absolute() else ROOT / path


def _load_route_points(route: str) -> list[dict[str, Any]]:
    data = json.loads(_resolve_route(route).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if isinstance(data.get("waypoints"), list):
            return [item for item in data["waypoints"] if isinstance(item, dict)]
        if isinstance(data.get("script"), list):
            return [item for item in data["script"] if isinstance(item, dict) and "x" in item and "y" in item and "z" in item]
        if isinstance(data.get("entries"), list):
            return [item for item in data["entries"] if isinstance(item, dict) and "x" in item and "y" in item and "z" in item]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict) and "x" in item and "y" in item and "z" in item]
    return []


def _load_route_data(route: str) -> dict[str, Any]:
    data = json.loads(_resolve_route(route).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _derive_start_pos(route: str) -> str:
    points = _load_route_points(route)
    if not points:
        raise ValueError(f"Route '{route}' has no coordinate entries")
    first = points[0]
    return f"{int(first['x'])},{int(first['y'])},{int(first['z'])}"


def _parse_xyz(text: str) -> tuple[int, int, int]:
    parts = [int(part.strip()) for part in text.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Expected x,y,z but got: {text!r}")
    return parts[0], parts[1], parts[2]


def _is_start_pos_compatible(expected_start_pos: str, actual_start_pos: str) -> bool:
    expected_x, expected_y, expected_z = _parse_xyz(expected_start_pos)
    actual_x, actual_y, actual_z = _parse_xyz(actual_start_pos)
    if expected_z != actual_z:
        return False
    return (abs(expected_x - actual_x) + abs(expected_y - actual_y)) <= T1_START_MISMATCH_LIMIT


def _detect_live_start_pos(expected_start_pos: str) -> str | None:
    try:
        from src.frame_capture import build_frame_getter
        from src.input_controller import find_window
        from src.map_loader import TibiaMapLoader
        from src.minimap_calibrator import MinimapCalibrator
        from src.models import Coordinate

        expected_x, expected_y, expected_z = _parse_xyz(expected_start_pos)
        expected = Coordinate(x=expected_x, y=expected_y, z=expected_z)
        window = find_window("Proyector")
        if window is None:
            print("  Start-pos adjust: Proyector window not found — keeping route start.")
            return None

        getter = build_frame_getter("printwindow", hwnd=window.hwnd)
        try:
            frame = None
            for _ in range(8):
                frame = getter()
                if frame is not None:
                    break
                time.sleep(0.2)
            if frame is None:
                print("  Start-pos adjust: no frame available — keeping route start.")
                return None

            loader = TibiaMapLoader()
            calibrator = MinimapCalibrator(loader=loader, floor=expected.z, hint=expected)
            result = calibrator.calibrate(frame)
            actual = result.position if result.success else None
            if actual is None:
                print("  Start-pos adjust: calibration failed — keeping route start.")
                return None
            if actual.z != expected.z:
                print(
                    f"  Start-pos adjust: floor mismatch actual={actual} expected={expected} — keeping route start."
                )
                return None

            adjusted = f"{actual.x},{actual.y},{actual.z}"
            if adjusted != expected_start_pos:
                print(
                    f"  Start-pos adjust: using live calibrated position {adjusted} instead of {expected_start_pos}."
                )
            else:
                print(f"  Start-pos adjust: calibrated start matches route start ({adjusted}).")
            return adjusted
        finally:
            close = getattr(getter, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"  Start-pos adjust: skipped ({exc})")
        return None


def _should_loop_t1(route: str) -> bool:
    data = _load_route_data(route)
    raw_session = data.get("session")
    session = raw_session if isinstance(raw_session, dict) else {}
    if session.get("loop_route") is False:
        return False
    point_count = len(_load_route_points(route))
    return point_count > 12


def _build_base_args(start_pos: str | None = None) -> list[str]:
    return [
        PYTHON, MAIN, "run",
        "--route", ACTIVE_ROUTE,
        "--window", "Tibia",
        "--pico", "--pico-port", "COM4",
        "--position-source", "minimap",
        "--start-pos", start_pos or ACTIVE_START_POS,
        "--frame-source", "printwindow",
        "--frame-window", "Proyector",
    ]


def _clear_session_checkpoint() -> None:
    CHECKPOINT_PATH.unlink(missing_ok=True)


def _load_session_checkpoint() -> dict[str, Any] | None:
    if not CHECKPOINT_PATH.exists():
        return None
    try:
        data = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _t1_post_run_failure(route: str) -> tuple[int, str] | None:
    checkpoint = _load_session_checkpoint()
    if checkpoint is None:
        return None

    route_file = str(checkpoint.get("route_file", "")).replace("\\", "/")
    expected = route.replace("\\", "/")
    if not route_file.endswith(expected):
        return None

    extra = checkpoint.get("extra")
    if not isinstance(extra, dict):
        return None

    stop_reason = str(extra.get("script_stop_reason", ""))
    if stop_reason not in {"movement_failed", "resolver_degraded"}:
        return None

    resume_idx = int(extra.get("script_resume_instruction_index", 0))
    if stop_reason == "resolver_degraded":
        return (
            4,
            "Route entered sustained position-resolver loss after blockage "
            f"and left a resumable checkpoint at instruction [{resume_idx}]",
        )
    return (
        3,
        "Route did not complete: ScriptExecutor stopped with movement_failed "
        f"and left a resumable checkpoint at instruction [{resume_idx}]",
    )


def _write_report(
    *,
    requested_test: str,
    timeout_override: int,
    skip_preflight: bool,
    preflight_ok: bool | None,
    results: list[dict[str, Any]],
    report_prefix: str,
    route: str,
) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    json_path = OUTPUT_DIR / f"{report_prefix}_{stamp}.json"
    md_path = OUTPUT_DIR / f"{report_prefix}_{stamp}.md"

    payload = {
        "generated_at": _now_iso(),
        "requested_test": requested_test,
        "timeout_override_s": timeout_override,
        "skip_preflight": skip_preflight,
        "preflight_ok": preflight_ok,
        "python": PYTHON,
        "root": str(ROOT),
        "route": route,
        "results": results,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    passed = sum(1 for item in results if item["status"] == "PASS")
    lines = [
        "# Live Test Runner Report",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Requested test: {requested_test}",
        f"- Route: {route}",
        f"- Python: {PYTHON}",
        f"- Preflight: {'PASS' if preflight_ok else 'FAIL' if preflight_ok is False else 'SKIPPED'}",
        f"- Summary: {passed}/{len(results)} passed",
        "",
        "| Test | Status | Exit | Duration (s) | Started | Ended |",
        "|------|--------|------|--------------|---------|-------|",
    ]
    for item in results:
        lines.append(
            f"| {item['name']} | {item['status']} | {item['exit_code']} | "
            f"{item['duration_s']:.1f} | {item['started_at']} | {item['ended_at']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _stop_process(proc: subprocess.Popen[Any], *, grace_s: float = 15.0) -> int:
    if proc.poll() is not None:
        return int(proc.returncode or 0)

    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
        return int(proc.wait(timeout=grace_s))
    except Exception:
        try:
            proc.terminate()
            return int(proc.wait(timeout=grace_s))
        except Exception:
            proc.kill()
            return int(proc.wait(timeout=5.0))


def _run(
    args: list[str],
    timeout: int | None = None,
    *,
    pass_on_timeout: bool = False,
) -> int:
    """Run a subprocess, stream output, return exit code."""
    print(f"  CMD: {' '.join(args[-8:])}")
    print(f"  Full: {' '.join(args)}\n")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(args, cwd=str(ROOT), creationflags=creationflags)
        return int(proc.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        if pass_on_timeout:
            print(f"\n  [PASS] Observation window of {timeout}s completed — stopping session.")
            _stop_process(proc)
            return 0
        print(f"\n  [TIMEOUT] Test exceeded {timeout}s limit — stopping.")
        _stop_process(proc)
        return -1
    except KeyboardInterrupt:
        try:
            _stop_process(proc)
        except Exception:
            pass
        print("\n  [INTERRUPTED] User cancelled.")
        return -2


def _pause(msg: str = "Press Enter to continue...") -> None:
    if not INTERACTIVE_MODE or not sys.stdin.isatty():
        print(f"\n  >>> {msg} [auto-continue]")
        return
    try:
        input(f"\n  >>> {msg}")
    except (EOFError, KeyboardInterrupt):
        pass


def preflight() -> bool:
    """Run preflight checks. Returns True if all pass."""
    _banner("PREFLIGHT CHECK")
    code = _run([PYTHON, str(ROOT / "tools" / "preflight_check.py"),
                 "--route", ACTIVE_ROUTE,
                 "--live",
                 "--frame-source", "printwindow",
                 "--frame-window", "Proyector"])
    return code == 0


# ═══════════════════════════════════════════════════════════════════════════════
# T1 — Navigation Only (no combat, no heal)
# ═══════════════════════════════════════════════════════════════════════════════

def test_t1(timeout: int = 300) -> int:
    """T1: Walk the route with no combat. Expect the bot to follow waypoints."""
    _banner("T1 — NAVIGATION WALK (no combat)")
    loop_enabled = _should_loop_t1(ACTIVE_ROUTE)
    effective_start_pos = _detect_live_start_pos(ACTIVE_START_POS) or ACTIVE_START_POS
    if not _is_start_pos_compatible(ACTIVE_START_POS, effective_start_pos):
        print(
            "  [FAIL] Live calibrated start is too far from the route start "
            f"({effective_start_pos} vs {ACTIVE_START_POS})."
        )
        print("  Reposition the character to the route start area before running T1.")
        return 2
    print("  Objective: Walk the Thais rat hunt route for ~5 minutes.")
    if loop_enabled:
        print("  Expected: Character follows waypoints, returns to temple, loops.")
    else:
        print("  Expected: Character follows the route once without forcing loop.")
    print("  Pass criteria:")
    print("    - Character moves tile-by-tile along the route")
    print("    - Position feedback works (minimap radar)")
    print("    - StuckDetector triggers replan if blocked by NPC/creature")
    print("    - No crash or unhandled exception")
    print()
    _pause("Ensure character is at Thais Temple (32369,32241,7). Press Enter to start T1...")
    _clear_session_checkpoint()

    args = _build_base_args(effective_start_pos) + [
        "--start-delay", "5",
        "--heal", "70",
        "--heal-vk", "0x70",       # F1
        "--emergency-vk", "0x72",  # F3
        "--mana-vk", "0x71",       # F2
    ]
    if loop_enabled:
        args.append("--loop")
    else:
        print("  Loop policy: disabled for one-pass certification route.")
    code = _run(args, timeout=timeout, pass_on_timeout=True)
    if code != 0:
        return code
    failure = _t1_post_run_failure(ACTIVE_ROUTE)
    if failure is not None:
        failure_code, message = failure
        print(f"\n  [FAIL] {message}")
        return failure_code
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# T2 — Combat + Heal + Loot
# ═══════════════════════════════════════════════════════════════════════════════

def test_t2(timeout: int = 600) -> int:
    """T2: Walk + combat + heal + loot. Full hunting loop."""
    _banner("T2 — COMBAT + HEAL + LOOT")
    print("  Objective: Hunt rats around Thais for ~10 minutes.")
    print("  Expected: Walk → detect monster → attack → heal → loot corpse → continue.")
    print("  Pass criteria:")
    print("    - CombatManager targets rats on screen")
    print("    - AutoHealer fires F1/F3 when HP drops")
    print("    - Looter opens corpses after kill")
    print("    - if_stat flee triggers when HP < 40%")
    print("    - Character returns to temple at low MP")
    print()
    _pause("Ensure character is at Thais Temple. Press Enter to start T2...")

    args = _build_base_args() + [
        "--start-delay", "5",
        "--loop",
        "--combat",
        "--class", "knight",
        "--loot",
        "--heal", "70",
        "--emergency-pct", "30",
        "--mana-pct", "30",
        "--heal-vk", "0x70",
        "--emergency-vk", "0x72",
        "--mana-vk", "0x71",
    ]
    return _run(args, timeout=timeout)


# ═══════════════════════════════════════════════════════════════════════════════
# T3 — Death → Detector → Respawn → Re-equip → Return
# ═══════════════════════════════════════════════════════════════════════════════

def test_t3(timeout: int = 300) -> int:
    """T3: Simulate death recovery flow.

    MANUAL STEP REQUIRED:
    After the bot starts, you must kill the character (walk into a strong monster
    area or use debug commands). The bot should:
      1. Detect death_screen.png
      2. Click OK to respawn at temple
      3. Fire re-equip hotkeys
      4. Navigate back to the route start
      5. Resume hunting
    """
    _banner("T3 — DEATH RECOVERY")
    print("  Objective: Test death detection → respawn → re-equip → resume.")
    print("  MANUAL: After the bot starts walking, you must cause the character")
    print("          to die (e.g. walk into dangerous area manually).")
    print("  Expected flow:")
    print("    1. DeathHandler detects death_screen.png template")
    print("    2. Clicks 'OK' to respawn at temple")
    print("    3. Fires re-equip hotkeys (F6, F7)")
    print("    4. Navigates back to route start")
    print("    5. Resumes the script loop")
    print()
    print("  Pass criteria:")
    print("    - Death detected within 5s of death screen appearing")
    print("    - Respawn click works (character at temple)")
    print("    - Re-equip hotkeys fire (check equipment)")
    print("    - Route resumes automatically")
    print()
    _pause("Ready to test death recovery? Press Enter to start T3...")

    args = _build_base_args() + [
        "--start-delay", "5",
        "--loop",
        "--combat",
        "--class", "knight",
        "--heal", "70",
        "--heal-vk", "0x70",
        "--emergency-vk", "0x72",
        "--mana-vk", "0x71",
        "--re-equip", "0x75,0x76",  # F6, F7 as re-equip hotkeys
        "--max-deaths", "2",
    ]
    return _run(args, timeout=timeout)


# ═══════════════════════════════════════════════════════════════════════════════
# T4 — Disconnect → Reconnect → Resume
# ═══════════════════════════════════════════════════════════════════════════════

def test_t4(timeout: int = 300) -> int:
    """T4: Simulate disconnect recovery.

    MANUAL STEP REQUIRED:
    After the bot starts, you must cause a disconnect:
      - Pull ethernet cable, or
      - Use Windows firewall to block Tibia, or
      - Kill the Tibia network adapter briefly
    The bot should:
      1. Detect login_screen.png
      2. Wait for connection to restore
      3. Click "OK" / enter credentials
      4. Resume the script
    """
    _banner("T4 — DISCONNECT / RECONNECT")
    print("  Objective: Test reconnection after network drop.")
    print("  MANUAL: After the bot starts, disconnect the network briefly.")
    print("  Expected flow:")
    print("    1. ReconnectHandler detects login_screen.png")
    print("    2. Waits for network (exponential backoff)")
    print("    3. Clicks through login UI")
    print("    4. Waits for character to load")
    print("    5. Resumes the script from last position")
    print()
    print("  Pass criteria:")
    print("    - Disconnect detected within 10s")
    print("    - Reconnect succeeds within 60s of network restore")
    print("    - Script resumes (character starts walking again)")
    print("    - No duplicate path execution")
    print()
    _pause("Ready to test reconnect? Press Enter to start T4...")

    args = _build_base_args() + [
        "--start-delay", "5",
        "--loop",
        "--combat",
        "--class", "knight",
        "--heal", "70",
        "--heal-vk", "0x70",
        "--emergency-vk", "0x72",
        "--mana-vk", "0x71",
    ]
    return _run(args, timeout=timeout)


# ═══════════════════════════════════════════════════════════════════════════════
# T5 — Soak Test (30+ min AFK with breaks)
# ═══════════════════════════════════════════════════════════════════════════════

def test_t5(timeout: int = 2400) -> int:
    """T5: Full AFK soak test. Runs for 30+ minutes with all systems enabled."""
    _banner("T5 — SOAK TEST (30+ min AFK)")
    print("  Objective: Full autonomous operation for 30+ minutes.")
    print("  All systems enabled: combat, heal, loot, death handler,")
    print("  reconnect, anti-kick, break scheduler, GM detector, dashboard.")
    print()
    print("  Pass criteria:")
    print("    - No crash for 30 min")
    print("    - Anti-kick fires if idle > 5 min (check logs)")
    print("    - Break scheduler pauses/resumes (check logs)")
    print("    - Memory usage stays under 500 MB")
    print("    - Kills/loot tracked in session stats")
    print("    - Dashboard accessible at http://localhost:8080 (if --dashboard)")
    print()
    print(f"  Timeout: {timeout}s ({timeout/60:.0f} min)")
    print()
    _pause("Ready for 30+ min soak test? Press Enter to start T5...")

    args = _build_base_args() + [
        "--start-delay", "5",
        "--loop",
        "--combat",
        "--class", "knight",
        "--loot",
        "--heal", "70",
        "--emergency-pct", "30",
        "--mana-pct", "30",
        "--heal-vk", "0x70",
        "--emergency-vk", "0x72",
        "--mana-vk", "0x71",
        "--re-equip", "0x75,0x76",
        "--gm-detector",
        "--dashboard",
        "--anti-kick-idle", "300",
        # Break scheduler ON by default (we don't pass --no-break)
    ]
    return _run(args, timeout=timeout)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

TESTS = {
    "t1": ("T1 — Navigation", test_t1),
    "t2": ("T2 — Combat+Heal+Loot", test_t2),
    "t3": ("T3 — Death Recovery", test_t3),
    "t4": ("T4 — Reconnect", test_t4),
    "t5": ("T5 — Soak AFK 30min", test_t5),
}


def main() -> int:
    global ACTIVE_ROUTE, ACTIVE_START_POS, INTERACTIVE_MODE

    parser = argparse.ArgumentParser(
        description="Live Test Runner — T1 through T5",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Tests:
  t1         Navigation only (walk route, no combat)
  t2         Combat + heal + loot (full hunt loop)
  t3         Death → respawn → re-equip → resume
  t4         Disconnect → reconnect → resume
  t5         30+ min AFK soak test (all systems)
  all        Run T1-T5 sequentially
  preflight  Run preflight checks only
""",
    )
    parser.add_argument("test", choices=["t1", "t2", "t3", "t4", "t5", "all", "preflight"],
                        help="Which test to run")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Override test timeout in seconds (0 = use default)")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="Skip preflight checks")
    parser.add_argument("--report-prefix", default="live_qa",
                        help="Prefix for JSON/Markdown evidence files in output/")
    parser.add_argument("--route", default=DEFAULT_ROUTE,
                        help="Route JSON to use for the live tests")
    parser.add_argument("--start-pos", default="",
                        help="Override start position as x,y,z (default: derive from first route point)")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Skip Enter prompts before starting each live scenario")
    args = parser.parse_args()

    ACTIVE_ROUTE = args.route
    ACTIVE_START_POS = args.start_pos or _derive_start_pos(ACTIVE_ROUTE)
    INTERACTIVE_MODE = not args.non_interactive

    os.chdir(ROOT)

    _banner("LIVE TEST RUNNER — Tibia Bot Validation")
    print(f"  Route:  {ACTIVE_ROUTE}")
    print(f"  Start:  {ACTIVE_START_POS}")
    print(f"  Python: {PYTHON}")
    print(f"  CWD:    {ROOT}")
    print()

    # Always run preflight first (unless skipped)
    if args.test == "preflight":
        ok = preflight()
        return 0 if ok else 1

    preflight_ok: bool | None = None

    if not args.skip_preflight:
        preflight_ok = preflight()
        if not preflight_ok:
            print("\n  [!] Preflight failed. Fix issues above before testing.")
            print("      Use --skip-preflight to bypass.\n")
            return 1

    results: list[dict[str, Any]] = []

    if args.test == "all":
        for key, (name, fn) in TESTS.items():
            kwargs = {}
            if args.timeout > 0:
                kwargs["timeout"] = args.timeout
            started_at = _now_iso()
            t0 = time.perf_counter()
            code = fn(**kwargs)
            duration_s = time.perf_counter() - t0
            results.append({
                "key": key,
                "name": name,
                "exit_code": code,
                "status": _status_from_code(code),
                "started_at": started_at,
                "ended_at": _now_iso(),
                "duration_s": duration_s,
            })
            if code not in (0, -2):
                _pause(f"{name} exited with code {code}. Continue to next test?")
    else:
        name, fn = TESTS[args.test]
        kwargs = {}
        if args.timeout > 0:
            kwargs["timeout"] = args.timeout
        started_at = _now_iso()
        t0 = time.perf_counter()
        code = fn(**kwargs)
        duration_s = time.perf_counter() - t0
        results.append({
            "key": args.test,
            "name": name,
            "exit_code": code,
            "status": _status_from_code(code),
            "started_at": started_at,
            "ended_at": _now_iso(),
            "duration_s": duration_s,
        })

    # Summary
    _banner("TEST RESULTS SUMMARY")
    for item in results:
        tag = item["status"] if item["status"] != "FAIL" else f"FAIL (exit {item['exit_code']})"
        print(f"  [{tag:>12}] {item['name']}")

    total = len(results)
    passed = sum(1 for item in results if item["status"] == "PASS")
    print(f"\n  {passed}/{total} passed\n")

    json_path, md_path = _write_report(
        requested_test=args.test,
        timeout_override=args.timeout,
        skip_preflight=args.skip_preflight,
        preflight_ok=preflight_ok,
        results=results,
        report_prefix=args.report_prefix,
        route=ACTIVE_ROUTE,
    )
    print(f"  JSON report: {json_path}")
    print(f"  Markdown report: {md_path}\n")

    return _overall_exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
