"""
Pre-flight Checker
------------------
Validates **all** production prerequisites before the bot starts.

Each check returns a :class:`CheckResult` with severity
(``PASS`` / ``WARN`` / ``FAIL``).  :func:`run_preflight` aggregates every
check and returns a :class:`PreflightReport`.

Integration::

    from src.preflight import run_preflight

    report = run_preflight(cfg)
    if not report.ok:
        for r in report.failures:
            print(r)
        sys.exit(1)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, List, Optional

logger = logging.getLogger("wn.pf")


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

class Severity(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.name}: {self.message}"


@dataclass
class PreflightReport:
    results: List[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.severity != Severity.FAIL for r in self.results)

    @property
    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if r.severity == Severity.FAIL]

    @property
    def warnings(self) -> List[CheckResult]:
        return [r for r in self.results if r.severity == Severity.WARN]

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.severity == Severity.PASS)
        warns = len(self.warnings)
        fails = len(self.failures)
        status = "READY" if self.ok else "BLOCKED"
        lines = [f"Preflight: {status}  ({passed}/{total} pass, {warns} warn, {fails} fail)"]
        for r in self.results:
            lines.append(f"  {r}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def check_interception_driver() -> CheckResult:
    """Verify the Interception kernel driver is installed and loadable."""
    try:
        from interception import Interception  # type: ignore[import-untyped]
        ctx = Interception()
        return CheckResult("interception_driver", Severity.PASS,
                           "Driver loaded OK")
    except Exception as exc:
        return CheckResult("interception_driver", Severity.FAIL,
                           f"Driver not available: {exc}.  "
                           "Instala interception-python y el driver "
                           "(install-interception.exe /install), luego reinicia.")


def check_interception_package() -> CheckResult:
    """Verify the interception-python package is importable."""
    try:
        import interception  # type: ignore[import-untyped] # noqa: F401
        return CheckResult("interception_package", Severity.PASS,
                           "Package importable")
    except ImportError:
        return CheckResult("interception_package", Severity.FAIL,
                           "pip install interception-python  (requerido)")


def check_mouse_move_fn(cfg: Any = None) -> CheckResult:
    """Warn when click_human() would use SetCursorPos (hookable by BattlEye).

    Safe paths:
     - input_method='interception'  → Interception MouseStroke (undetectable).
     - arduino_enabled / pico_enabled → USB HID hardware click (undetectable).
    Unsafe: any other input_method without hardware HID → SetCursorPos hookable.
    """
    if cfg is None:
        return CheckResult("mouse_move_fn", Severity.PASS,
                           "No config — check skipped.")
    method = getattr(cfg, "input_method", "interception")
    arduino = getattr(cfg, "arduino_enabled", False)
    pico = getattr(cfg, "pico_enabled", False)
    if method == "interception" or arduino or pico:
        return CheckResult("mouse_move_fn", Severity.PASS,
                           "Hardware-level mouse movement active (Interception / HID).")
    return CheckResult(
        "mouse_move_fn", Severity.WARN,
        f"input_method='{method}' without hardware HID — click_human() will use "
        "SetCursorPos which is hookable by BattlEye.  "
        "Set input_method='interception' or enable arduino_enabled/pico_enabled.",
    )


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _validate_roi(roi: Any, label: str) -> Optional[str]:
    """Return error string if *roi* is invalid, else None."""
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        return f"{label}: expected [x,y,w,h], got {roi!r}"
    try:
        x, y, w, h = (int(v) for v in roi)
    except (TypeError, ValueError):
        return f"{label}: non-integer values in {roi!r}"
    if x < 0 or y < 0:
        return f"{label}: negative x/y in {roi}"
    if w < 1 or h < 1:
        return f"{label}: width/height must be >= 1 in {roi}"
    return None


def check_hpmp_config() -> CheckResult:
    """Validate hpmp_config.json exists and has valid ROIs."""
    p = _PROJECT_ROOT / "hpmp_config.json"
    if not p.exists():
        return CheckResult("hpmp_config", Severity.FAIL,
                           f"Archivo no encontrado: {p}.  "
                           "Ejecuta  python -m src.calibrator --mode hpmp")
    try:
        data = _load_json(p)
    except Exception as exc:
        return CheckResult("hpmp_config", Severity.FAIL, f"JSON inválido: {exc}")
    errors: list[str] = []
    for key in ("hp_roi", "mp_roi"):
        roi = data.get(key)
        if roi is None:
            errors.append(f"falta '{key}'")
        else:
            err = _validate_roi(roi, key)
            if err:
                errors.append(err)
    if errors:
        return CheckResult("hpmp_config", Severity.FAIL, "; ".join(errors))
    return CheckResult("hpmp_config", Severity.PASS, "hp_roi + mp_roi válidos")


def check_minimap_config() -> CheckResult:
    """Validate minimap_config.json exists and has a valid ROI."""
    p = _PROJECT_ROOT / "minimap_config.json"
    if not p.exists():
        return CheckResult("minimap_config", Severity.WARN,
                           f"Archivo no encontrado: {p}.  "
                           "Solo necesario si usas position_source=minimap.")
    try:
        data = _load_json(p)
    except Exception as exc:
        return CheckResult("minimap_config", Severity.FAIL, f"JSON inválido: {exc}")
    roi = data.get("roi")
    if roi is None:
        return CheckResult("minimap_config", Severity.FAIL, "falta 'roi'")
    err = _validate_roi(roi, "roi")
    if err:
        return CheckResult("minimap_config", Severity.FAIL, err)
    return CheckResult("minimap_config", Severity.PASS, "ROI válido")


def check_combat_config(path: str = "") -> CheckResult:
    """Validate combat config JSON structure."""
    if not path:
        path = str(_PROJECT_ROOT / "combat_config.json")
    p = Path(path)
    if not p.exists():
        return CheckResult("combat_config", Severity.WARN,
                           f"Archivo no encontrado: {p}.  "
                           "Solo necesario si auto_combat=True.")
    try:
        data = _load_json(p)
    except Exception as exc:
        return CheckResult("combat_config", Severity.FAIL, f"JSON inválido: {exc}")
    errors: list[str] = []
    roi = data.get("battle_list_roi")
    if roi is None:
        errors.append("falta 'battle_list_roi'")
    else:
        err = _validate_roi(roi, "battle_list_roi")
        if err:
            errors.append(err)
    spells = data.get("spells")
    if not isinstance(spells, list):
        errors.append("falta 'spells' (array)")
    elif spells:
        for i, s in enumerate(spells):
            if not isinstance(s, dict):
                errors.append(f"spells[{i}]: no es un objeto")
            elif "vk" not in s:
                errors.append(f"spells[{i}]: falta 'vk'")
    if errors:
        return CheckResult("combat_config", Severity.FAIL, "; ".join(errors))
    n_spells = len(data.get("spells", []))
    return CheckResult("combat_config", Severity.PASS,
                       f"battle_list_roi + {n_spells} spells OK")


def check_route_file(route_file: str) -> CheckResult:
    """Validate the route file exists and contains >= 2 waypoints."""
    if not route_file:
        return CheckResult("route_file", Severity.WARN,
                           "No se especificó route_file en SessionConfig.")
    p = Path(route_file)
    if not p.exists():
        # Try under routes/ directory
        p = _PROJECT_ROOT / "routes" / route_file
    if not p.exists():
        return CheckResult("route_file", Severity.FAIL,
                           f"Ruta no encontrada: {route_file}")
    try:
        data = _load_json(p)
    except Exception as exc:
        return CheckResult("route_file", Severity.FAIL, f"JSON inválido: {exc}")
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "script" in data:
        # Unified JSON: count movement nodes (entries with x + y) in "script"
        items = [s for s in data["script"]
                 if isinstance(s, dict) and "x" in s and "y" in s]
    else:
        items = data.get("waypoints", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return CheckResult("route_file", Severity.FAIL, "formato inválido: no es una lista de waypoints")
    if len(items) < 2:
        return CheckResult("route_file", Severity.FAIL,
                           f"Solo {len(items)} waypoint(s) — se necesitan >= 2 para una ruta")
    # Validate each waypoint has x, y
    for i, wp in enumerate(items):
        if not isinstance(wp, dict):
            return CheckResult("route_file", Severity.FAIL,
                               f"waypoint[{i}]: no es un objeto")
        if "x" not in wp or "y" not in wp:
            return CheckResult("route_file", Severity.FAIL,
                               f"waypoint[{i}]: falta 'x' o 'y'")
    return CheckResult("route_file", Severity.PASS,
                       f"{len(items)} waypoints en {p.name}")


def check_templates_dir() -> CheckResult:
    """Verify that cache/templates directory exists and has template images."""
    tpl = _PROJECT_ROOT / "cache" / "templates"
    if not tpl.is_dir():
        return CheckResult("templates_dir", Severity.WARN,
                           f"Directorio no encontrado: {tpl}.  "
                           "Necesario para combat y loot.")
    pngs = list(tpl.rglob("*.png"))
    if not pngs:
        return CheckResult("templates_dir", Severity.WARN,
                           "cache/templates existe pero no contiene imágenes PNG.")
    return CheckResult("templates_dir", Severity.PASS,
                       f"{len(pngs)} templates encontrados")


def check_his_config() -> CheckResult:
    """Verify the Human Input System config.yaml exists."""
    p = _PROJECT_ROOT / "human_input_system" / "config.yaml"
    if not p.exists():
        return CheckResult("his_config", Severity.WARN,
                           f"Archivo no encontrado: {p}.  "
                           "HIS usará defaults internos.")
    try:
        import yaml  # type: ignore[import-untyped]  # noqa: F401
        with open(p, encoding="utf-8") as f:
            yaml.safe_load(f)
        return CheckResult("his_config", Severity.PASS, "config.yaml válido")
    except ImportError:
        return CheckResult("his_config", Severity.WARN, "PyYAML no instalado — HIS usará defaults")
    except Exception as exc:
        return CheckResult("his_config", Severity.FAIL, f"YAML inválido: {exc}")


def check_maps_dir() -> CheckResult:
    """Verify that cached floor maps exist (needed for pathfinding)."""
    maps = _PROJECT_ROOT / "maps"
    if not maps.is_dir():
        return CheckResult("maps_cache", Severity.WARN,
                           "Directorio maps/ no existe.  Se descargará automáticamente "
                           "del CDN al primer uso, pero requiere internet.")
    floors = list(maps.glob("*.png"))
    if not floors:
        return CheckResult("maps_cache", Severity.WARN,
                           "maps/ existe pero no tiene PNGs descargados.")
    return CheckResult("maps_cache", Severity.PASS,
                       f"{len(floors)} floor maps cacheados")


def check_dependencies() -> CheckResult:
    """Verify critical Python dependencies are importable."""
    missing: list[str] = []
    for mod in ("numpy", "PIL", "cv2", "mss"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return CheckResult("dependencies", Severity.FAIL,
                           f"Módulos faltantes: {', '.join(missing)}.  "
                           "pip install -e '.[dev]'")
    return CheckResult("dependencies", Severity.PASS, "numpy, PIL, cv2, mss OK")


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_preflight(
    cfg: Any = None,
    *,
    skip_driver: bool = False,
    log_fn: Optional[Callable[[str], None]] = None,
) -> PreflightReport:
    """Run all pre-flight checks and return a :class:`PreflightReport`.

    Parameters
    ----------
    cfg : SessionConfig, optional
        When provided, enables route_file and combat_config validation.
    skip_driver : bool
        Skip the Interception driver check (useful in CI/test environments).
    log_fn : callable, optional
        Log callback — each check result is logged.
    """
    report = PreflightReport()

    checks: list[Callable[[], CheckResult]] = [
        check_dependencies,
        check_interception_package,
    ]
    if not skip_driver:
        checks.append(check_interception_driver)

    checks.extend([
        check_hpmp_config,
        check_minimap_config,
        check_templates_dir,
        check_his_config,
        check_maps_dir,
    ])

    # Config-independent checks that require cfg
    if cfg is not None:
        checks.append(lambda: check_mouse_move_fn(cfg))

    for check in checks:
        result = check()
        report.results.append(result)
        if log_fn:
            log_fn(str(result))

    # Config-dependent checks
    if cfg is not None:
        route = getattr(cfg, "route_file", "")
        r = check_route_file(route)
        report.results.append(r)
        if log_fn:
            log_fn(str(r))

        combat_cfg = getattr(cfg, "combat_config_file", "")
        if getattr(cfg, "auto_combat", False) or combat_cfg:
            r = check_combat_config(combat_cfg)
            report.results.append(r)
            if log_fn:
                log_fn(str(r))

    return report
