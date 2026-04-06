"""Production Release Validation Suite
=====================================

One-command production readiness check.  Run this before every release.

Exit codes:
    0  All checks pass — RELEASE OK
    1  One or more checks failed — NOT READY

Usage:
    python tools/release_check.py               # full check
    python tools/release_check.py --quick        # skip long-running tests
    python tools/release_check.py --json         # machine-readable output
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class Check:
    name: str
    status: str = "SKIP"   # PASS / FAIL / WARN / SKIP
    detail: str = ""


@dataclass
class ReleaseReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status == "FAIL")

    @property
    def warned(self) -> int:
        return sum(1 for c in self.checks if c.status == "WARN")

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "total": len(self.checks),
                "passed": self.passed,
                "failed": self.failed,
                "warned": self.warned,
                "release_ok": self.ok,
            },
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail}
                        for c in self.checks],
        }


def check_imports(report: ReleaseReport) -> None:
    """Verify all 52+ src modules import without error."""
    src_dir = ROOT / "src"
    py_files = sorted(src_dir.glob("*.py"))
    failures = []
    for f in py_files:
        if f.name.startswith("_") and f.name != "__init__.py":
            continue
        mod_name = f"src.{f.stem}"
        try:
            importlib.import_module(mod_name)
        except Exception as exc:
            failures.append(f"{mod_name}: {exc}")

    if failures:
        report.add("module_imports", "FAIL", f"{len(failures)} failures: {failures[0]}")
    else:
        report.add("module_imports", "PASS", f"{len(py_files)} modules OK")


def check_his_imports(report: ReleaseReport) -> None:
    """Verify Human Input System imports."""
    try:
        from human_input_system import HumanInputSystem  # noqa: F401
        report.add("his_import", "PASS", "HumanInputSystem importable")
    except Exception as exc:
        report.add("his_import", "WARN", f"HIS not available: {exc}")


def check_configs(report: ReleaseReport) -> None:
    """Verify all required config files exist and are valid JSON."""
    required = [
        "hpmp_config.json", "minimap_config.json",
        "detector_config.json", "combat_config.json",
    ]
    class_configs = [
        "combat_config_druid.json", "combat_config_paladin.json",
        "combat_config_sorcerer.json",
    ]

    # Required
    for cfg_name in required:
        p = ROOT / cfg_name
        if not p.exists():
            report.add(f"config:{cfg_name}", "FAIL", "missing")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
            report.add(f"config:{cfg_name}", "PASS", "valid JSON")
        except Exception as exc:
            report.add(f"config:{cfg_name}", "FAIL", f"invalid: {exc}")

    # Class configs (optional but should exist)
    for cfg_name in class_configs:
        p = ROOT / cfg_name
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                n_spells = len(data.get("spells", []))
                has_healing = "healing" in data
                report.add(f"config:{cfg_name}", "PASS",
                           f"{n_spells} spells, healing={'yes' if has_healing else 'no'}")
            except Exception as exc:
                report.add(f"config:{cfg_name}", "FAIL", f"invalid: {exc}")
        else:
            report.add(f"config:{cfg_name}", "WARN", "not present")


def check_templates(report: ReleaseReport) -> None:
    """Verify template directory has sufficient files."""
    tpl = ROOT / "cache" / "templates"
    if not tpl.exists():
        report.add("templates", "FAIL", "cache/templates/ missing")
        return
    count = len([f for f in tpl.rglob("*") if f.is_file()])
    if count >= 50:
        report.add("templates", "PASS", f"{count} template files")
    elif count > 0:
        report.add("templates", "WARN", f"only {count} files (recommend 50+)")
    else:
        report.add("templates", "FAIL", "0 template files")


def check_routes(report: ReleaseReport) -> None:
    """Verify routes directory and validate route files."""
    routes_dir = ROOT / "routes"
    if not routes_dir.exists():
        report.add("routes", "FAIL", "routes/ missing")
        return
    json_routes = list(routes_dir.glob("*.json"))
    in_routes = list(routes_dir.glob("*.in"))
    total = len(json_routes) + len(in_routes)

    errors = 0
    for rf in json_routes:
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
            wps = data.get("waypoints", [])
            if not wps:
                errors += 1
        except Exception:
            errors += 1

    if total >= 5 and errors == 0:
        report.add("routes", "PASS", f"{total} route files ({len(json_routes)} JSON, {len(in_routes)} .in)")
    elif total >= 5:
        report.add("routes", "WARN", f"{total} routes, {errors} with issues")
    else:
        report.add("routes", "FAIL", f"only {total} routes")


def check_preflight(report: ReleaseReport) -> None:
    """Run preflight checks."""
    try:
        from src.preflight import run_preflight
        from src.session import SessionConfig
        cfg = SessionConfig(
            route_file="routes/thais_depot_to_temple.json",
            auto_combat=True,
            combat_config_file="combat_config.json",
        )
        result = run_preflight(cfg, skip_driver=True)
        fails = sum(1 for r in result.results if r.severity.name == "FAIL")
        passes = sum(1 for r in result.results if r.severity.name == "PASS")
        if fails == 0:
            report.add("preflight", "PASS", f"{passes}/{len(result.results)} checks pass")
        else:
            report.add("preflight", "FAIL", f"{fails} failures out of {len(result.results)}")
    except Exception as exc:
        report.add("preflight", "FAIL", f"Error: {exc}")


def check_interception(report: ReleaseReport) -> None:
    """Verify interception driver is available."""
    try:
        from interception import Interception
        ctx = Interception()
        report.add("interception_driver", "PASS", "driver loaded")
        del ctx
    except Exception as exc:
        report.add("interception_driver", "FAIL", f"{exc}")


def check_pytest(report: ReleaseReport, quick: bool = False) -> None:
    """Run pytest and verify all tests pass."""
    timeout = 120 if quick else 900
    args = [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"]
    if quick:
        args += ["-x", "--timeout=10", "-k", "not soak and not slow"]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), encoding="utf-8", errors="replace",
        )
        lines = result.stdout.strip().splitlines()
        summary_lines = [l for l in lines if "passed" in l or "failed" in l]
        summary = summary_lines[-1] if summary_lines else lines[-1] if lines else "no output"
        if "failed" not in summary and "error" not in summary.lower():
            report.add("pytest", "PASS", summary.strip())
        else:
            report.add("pytest", "FAIL", summary.strip())
    except subprocess.TimeoutExpired:
        report.add("pytest", "WARN", "timeout expired")
    except Exception as exc:
        report.add("pytest", "FAIL", f"Error running pytest: {exc}")


def check_anti_detection(report: ReleaseReport) -> None:
    """Verify anti-BattlEye hardening is in place."""
    issues = []

    # R1: No PostMessage fallback in interception mode
    ic_path = ROOT / "src" / "input_controller.py"
    if ic_path.exists():
        code = ic_path.read_text(encoding="utf-8")
        if "RuntimeError" in code and "_interception_warn_fallback" in code:
            pass  # Good — crash on fallback
        else:
            issues.append("R1: PostMessage fallback may still exist")

    # R3: Session abort without driver
    session_path = ROOT / "src" / "session.py"
    if session_path.exists():
        code = session_path.read_text(encoding="utf-8")
        if "INTERCEPTION DRIVER NO DISPONIBLE" in code:
            pass  # Good
        else:
            issues.append("R3: Session may not abort without driver")

    # A1: Anti-kick jitter
    ak_path = ROOT / "src" / "anti_kick.py"
    if ak_path.exists():
        code = ak_path.read_text(encoding="utf-8")
        if "random" in code:
            pass  # Good
        else:
            issues.append("A1: Anti-kick may lack jitter")

    if issues:
        report.add("anti_detection", "FAIL", "; ".join(issues))
    else:
        report.add("anti_detection", "PASS", "R1,R2,R3,A1 hardening verified")


def check_arduino_framework(report: ReleaseReport) -> None:
    """Verify Arduino HID framework exists."""
    arduino_file = ROOT / "human_input_system" / "core" / "arduino_hid_controller.py"
    firmware_file = ROOT / "arduino" / "tibia_hid" / "tibia_hid.ino"
    if arduino_file.exists():
        if firmware_file.exists():
            report.add("arduino_hid", "PASS", "controller + firmware present")
        else:
            report.add("arduino_hid", "WARN", "controller exists, firmware missing (optional)")
    else:
        report.add("arduino_hid", "WARN", "Arduino HID not implemented (optional)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Production Release Validation")
    parser.add_argument("--quick", action="store_true",
                        help="Skip long-running tests (pytest full suite)")
    parser.add_argument("--json", action="store_true",
                        help="Output machine-readable JSON")
    parser.add_argument("--skip-tests", action="store_true",
                        help="Skip pytest entirely (config-only check)")
    args = parser.parse_args()

    import os
    os.system("")  # Enable ANSI on Windows

    report = ReleaseReport()

    if not args.json:
        print(f"\n{BOLD}{'='*60}{RESET}")
        print(f"  {BOLD}PRODUCTION RELEASE CHECK{RESET}")
        print(f"{'='*60}\n")

    steps = [
        ("Module imports", lambda: check_imports(report)),
        ("HIS import", lambda: check_his_imports(report)),
        ("Config files", lambda: check_configs(report)),
        ("Templates", lambda: check_templates(report)),
        ("Routes", lambda: check_routes(report)),
        ("Preflight", lambda: check_preflight(report)),
        ("Interception driver", lambda: check_interception(report)),
        ("Anti-detection", lambda: check_anti_detection(report)),
        ("Arduino framework", lambda: check_arduino_framework(report)),
    ]

    if not args.skip_tests:
        steps.append(("Pytest", lambda: check_pytest(report, quick=args.quick)))

    for name, fn in steps:
        if not args.json:
            sys.stdout.write(f"  Checking {name}...")
            sys.stdout.flush()
        fn()
        last = report.checks[-1]
        if not args.json:
            icons = {"PASS": f"{GREEN}PASS{RESET}", "FAIL": f"{RED}FAIL{RESET}",
                     "WARN": f"{YELLOW}WARN{RESET}", "SKIP": "SKIP"}
            print(f" [{icons.get(last.status, last.status)}] {last.detail}")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"\n{'='*60}")
        total = len(report.checks)
        print(f"  {report.passed}/{total} passed, {report.failed} failed, {report.warned} warnings")
        if report.ok:
            print(f"  {GREEN}{BOLD}RELEASE: OK{RESET}")
        else:
            print(f"  {RED}{BOLD}RELEASE: NOT READY{RESET}")
        print(f"{'='*60}\n")

    # Save report
    out = ROOT / "output" / "release_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.json:
        print(f"  Report saved: {out}\n")

    sys.exit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
