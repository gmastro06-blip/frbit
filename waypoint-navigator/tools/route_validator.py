"""Route Validator — validates route JSON files for structural AND walkability correctness.

Checks performed
----------------
1. JSON syntax + schema basics (original checks)
2. random_stand choices have x/y/z
3. Consecutive waypoint distance — warns if > MAX_SEGMENT_TILES apart
4. Waypoint inside blocked_region — would make A* goal-snap fail
5. walkable_override vs blocked_region conflict on same tile
6. Walkability against floor map PNG — each waypoint tile must be walkable
   (after applying walkable_overrides and blocked_regions from the route)
7. A* reachability — for every consecutive pair of coordinate waypoints,
   runs the actual pathfinder and reports unreachable segments

Usage:
    python tools/route_validator.py routes/thais_rat_hunt.json
    python tools/route_validator.py routes/          # all .json in dir
    python tools/route_validator.py --all            # entire routes/ dir
    python tools/route_validator.py routes/thais_rat_hunt.json --no-astar
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ANSI colours
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

# Warn when two consecutive waypoints are more than this many tiles apart
MAX_SEGMENT_TILES = 20

# Warn (not error) when A* path is longer than this ratio × direct distance
STRETCH_WARN_RATIO = 3.0

# ── helpers ──────────────────────────────────────────────────────────────────

def _mdist(ax: int, ay: int, bx: int, by: int) -> int:
    return abs(ax - bx) + abs(ay - by)


def _tile_in_region(x: int, y: int, z: int, region: dict[str, Any]) -> bool:
    return (
        region.get("x_min", x) <= x <= region.get("x_max", x)
        and region.get("y_min", y) <= y <= region.get("y_max", y)
        and region.get("z", z) == z
    )


def _tile_in_override(x: int, y: int, z: int, ov: dict[str, Any]) -> bool:
    return (
        ov.get("x_min", x) <= x <= ov.get("x_max", x)
        and ov.get("y_min", y) <= y <= ov.get("y_max", y)
        and ov.get("z", z) == z
    )


_VALIDATOR_IF_RE = re.compile(r"^(hp|mp)\s*([<>]=?)\s*(\d+)$", re.IGNORECASE)


def _resolve_coord(s: dict) -> tuple[int, int, int] | None:
    """Extract (x, y, z) from an instruction, supporting both "x/y/z" and "at" forms."""
    if "x" in s and "y" in s:
        return s["x"], s["y"], s.get("z", 7)
    at = s.get("at")
    if isinstance(at, (list, tuple)) and len(at) >= 3:
        return int(at[0]), int(at[1]), int(at[2])
    return None


def _extract_coord_steps(script: list[dict]) -> list[tuple[int, int, int, int]]:
    """Return list of (instruction_index, x, y, z) for all coordinate-bearing steps."""
    result = []
    for i, s in enumerate(script):
        kind = s.get("kind", "")
        if kind in ("stand", "node"):
            c = _resolve_coord(s)
            if c:
                result.append((i, c[0], c[1], c[2]))
        elif kind == "random_stand":
            for ch in s.get("choices", []):
                if "x" in ch and "y" in ch:
                    result.append((i, ch["x"], ch["y"], ch.get("z", 7)))
                    break
    return result


# ── walkability grid builder ──────────────────────────────────────────────────

def _build_grid(loader: Any, floor: int,
                walkable_overrides: list[dict],
                blocked_regions: list[dict]) -> Any:
    """Return numpy walkability grid with route overrides applied.

    Order mirrors session_script.py _apply_script_regions():
      1. add_blocked_region  (blocked first)
      2. force_walkable_region (overrides applied LAST — they WIN over blocks)
    So walkable_overrides take precedence over blocked_regions for the same tile.
    """
    import numpy as np
    from src.models import BOUNDS

    grid = loader.get_walkability(floor).copy()   # bool H×W
    x_off = BOUNDS["xMin"]
    y_off = BOUNDS["yMin"]
    h, w = grid.shape

    # Step 1 — Apply blocked_regions first (flip to False)
    for br in blocked_regions:
        if br.get("z", floor) != floor:
            continue
        for tx in range(br["x_min"], br["x_max"] + 1):
            for ty in range(br["y_min"], br["y_max"] + 1):
                px, py = tx - x_off, ty - y_off
                if 0 <= py < h and 0 <= px < w:
                    grid[py, px] = False

    # Step 2 — Apply walkable_overrides last (flip to True, overrides wins)
    for ov in walkable_overrides:
        if ov.get("z", floor) != floor:
            continue
        for tx in range(ov["x_min"], ov["x_max"] + 1):
            for ty in range(ov["y_min"], ov["y_max"] + 1):
                px, py = tx - x_off, ty - y_off
                if 0 <= py < h and 0 <= px < w:
                    grid[py, px] = True

    return grid


# ── main validation ───────────────────────────────────────────────────────────

def validate_route(
    path: Path,
    run_astar: bool = True,
) -> list[str]:
    """
    Returns a list of hard-failure error strings (empty = route is valid).
    Soft warnings are collected internally but not returned; call
    ``validate_route_full`` to get (errors, warnings).
    """
    errors, _ = validate_route_full(path, run_astar=run_astar)
    return errors


def validate_route_full(
    path: Path,
    run_astar: bool = True,
) -> tuple[list[str], list[str]]:
    """
    Returns (errors, warnings).
    errors   = hard failures (route will not work)
    warnings = soft issues (route may work but is suspicious)
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. Load JSON ──────────────────────────────────────────────────────────
    if not path.exists():
        return [f"File not found: {path}"], []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"], []

    if not isinstance(data, dict):
        return ["Root must be a JSON object"], []

    script = data.get("script")
    wps_raw = data.get("waypoints")
    entries = data.get("entries")

    if script is None and wps_raw is None and entries is None:
        # Files with only config keys (hunt setup files, not route files) are not routes
        _config_only_keys = {"general", "tools", "items", "spells", "healing", "hunt_config",
                             "containers", "persistent_actions", "label_actions",
                             "target_monsters", "target_spells", "loot", "script_options"}
        if _config_only_keys & set(data.keys()):
            return [], ["Not a route file (setup/config only) — skipping route checks"]
        return ["Missing 'waypoints', 'script', or 'entries' key"], []

    # Explicit empty waypoints list is always an error
    if wps_raw is not None and isinstance(wps_raw, list) and len(wps_raw) == 0:
        return ["'waypoints' list is empty — route has 0 waypoints"], []

    # Normalise to a flat list of coord dicts for basic checks
    if script is not None:
        if not isinstance(script, list):
            return ["'script' must be a list"], []
        coord_wps = [s for s in script if isinstance(s, dict) and "x" in s and "y" in s]
    elif wps_raw is not None:
        coord_wps = wps_raw if isinstance(wps_raw, list) else []
    else:
        coord_wps = [s for s in (entries or []) if isinstance(s, dict) and "x" in s and "y" in s]

    # ── 2. Schema checks on each instruction ─────────────────────────────────
    all_instructions = script or wps_raw or entries or []
    for i, s in enumerate(all_instructions):
        if not isinstance(s, dict):
            errors.append(f"instruction[{i}]: not a dict")
            continue
        kind = s.get("kind", "")

        # Raw waypoints format (no "kind") — validate coords directly
        if not kind and "x" in s and "y" in s:
            x, y, z = s.get("x", 0), s.get("y", 0), s.get("z", 0)
            if not (30000 <= x <= 35000):
                errors.append(f"waypoint[{i}]: x={x} outside typical Tibia range")
            if not (30000 <= y <= 35000):
                errors.append(f"waypoint[{i}]: y={y} outside typical Tibia range")
            if not (0 <= z <= 15):
                errors.append(f"waypoint[{i}]: z={z} outside range [0-15]")

        if kind in ("stand", "node"):
            c = _resolve_coord(s)
            if c is None:
                errors.append(f"instruction[{i}] {kind}: missing coordinates (need 'x'/'y'/'z' or 'at')")
            else:
                x, y, z = c
                if not (30000 <= x <= 35000):
                    errors.append(f"instruction[{i}]: x={x} outside typical Tibia range")
                if not (30000 <= y <= 35000):
                    errors.append(f"instruction[{i}]: y={y} outside typical Tibia range")
                if not (0 <= z <= 15):
                    errors.append(f"instruction[{i}]: z={z} outside range [0-15]")

        elif kind == "random_stand":
            choices = s.get("choices", [])
            if not choices:
                errors.append(f"instruction[{i}] random_stand: no choices")
            for ci, c in enumerate(choices):
                for k in ("x", "y", "z"):
                    if k not in c:
                        errors.append(
                            f"instruction[{i}] random_stand choice[{ci}]: missing '{k}'"
                        )

        elif kind == "goto":
            if "label" not in s:
                errors.append(f"instruction[{i}] goto: missing 'label'")

        elif kind == "if_stat":
            # Accept both verbose form and "if": "hp<40" shorthand
            has_verbose = all(k in s for k in ("stat", "op", "threshold"))
            has_short   = "if" in s and _VALIDATOR_IF_RE.match(str(s.get("if", "")))
            if not has_verbose and not has_short:
                errors.append(
                    f"instruction[{i}] if_stat: need 'stat'/'op'/'threshold' "
                    f"or compact 'if' (e.g. \"hp<40\")"
                )
            if "goto_label" not in s:
                errors.append(f"instruction[{i}] if_stat: missing 'goto_label'")

    # Validate labels referenced by goto/if_stat exist
    if script:
        defined_labels = {s["label"] for s in script if s.get("kind") == "label" and "label" in s}
        for i, s in enumerate(script):
            if s.get("kind") == "goto":
                lbl = s.get("label", "")
                if lbl and lbl not in defined_labels:
                    errors.append(f"instruction[{i}] goto: label '{lbl}' never defined")
            if s.get("kind") == "if_stat":
                lbl = s.get("goto_label", "")
                if lbl and lbl not in defined_labels:
                    errors.append(f"instruction[{i}] if_stat: goto_label '{lbl}' never defined")

    if errors:
        return errors, warnings   # skip expensive checks if schema is broken

    if not coord_wps:
        warnings.append("Route has no coordinate waypoints (labels/gotos only)")
        return errors, warnings

    # ── 2b. Consecutive duplicate detection for raw waypoints (no "kind") ────
    # _extract_coord_steps only handles "node"/"stand" kinds; for the legacy
    # "waypoints" list format we detect duplicates directly on coord_wps.
    if wps_raw is not None and script is None:
        prev_wp = None
        for i, wp in enumerate(coord_wps):
            if not isinstance(wp, dict):
                continue
            cur = (wp.get("x"), wp.get("y"), wp.get("z"))
            if prev_wp is not None and cur == prev_wp[1]:
                errors.append(
                    f"waypoint[{i}]: duplicate of waypoint[{prev_wp[0]}] at "
                    f"({cur[0]},{cur[1]},z={cur[2]})"
                )
            prev_wp = (i, cur)

    if errors:
        return errors, warnings

    # ── 3. Consecutive distance check ────────────────────────────────────────
    coord_steps = _extract_coord_steps(script or all_instructions)
    prev_step = None
    for idx, x, y, z in coord_steps:
        if prev_step:
            pi, px, py, pz = prev_step
            if pz == z:
                d = _mdist(px, py, x, y)
                if d > MAX_SEGMENT_TILES:
                    warnings.append(
                        f"instruction[{idx}]: ({x},{y}) is {d} tiles from previous waypoint "
                        f"instruction[{pi}] ({px},{py}) — A* segment may be slow"
                    )
        prev_step = (idx, x, y, z)

    # ── 4. Waypoint inside blocked_region ────────────────────────────────────
    blocked_regions: list[dict] = data.get("blocked_regions", [])
    walkable_overrides: list[dict] = data.get("walkable_overrides", [])

    for idx, x, y, z in coord_steps:
        for bi, br in enumerate(blocked_regions):
            if _tile_in_region(x, y, z, br):
                errors.append(
                    f"instruction[{idx}]: waypoint ({x},{y},z={z}) is inside "
                    f"blocked_region[{bi}] ({br.get('x_min')}-{br.get('x_max')}, "
                    f"{br.get('y_min')}-{br.get('y_max')}) — A* goal-snap will fail"
                )

    # ── 5. walkable_override vs blocked_region tile conflict ─────────────────
    # walkable_overrides WIN (applied last) — these tiles are OPEN despite being in a blocked_region.
    # Only warn if a waypoint destination relies on a conflict tile (usually intentional).
    conflict_tiles: set[tuple[int, int, int]] = set()
    for bi, br in enumerate(blocked_regions):
        bz = br.get("z", 7)
        for tx in range(br["x_min"], br["x_max"] + 1):
            for ty in range(br["y_min"], br["y_max"] + 1):
                for oi, ov in enumerate(walkable_overrides):
                    if _tile_in_override(tx, ty, bz, ov):
                        conflict_tiles.add((tx, ty, bz))
    # Only report conflicts where a waypoint actually lands on a conflict tile
    for idx, x, y, z in coord_steps:
        if (x, y, z) in conflict_tiles:
            warnings.append(
                f"instruction[{idx}]: waypoint ({x},{y},z={z}) is in BOTH a "
                f"blocked_region AND a walkable_override — override wins (tile is open), "
                f"but double-check this is intentional"
            )

    # ── 6 + 7. Walkability + A* checks (need map loader) ────────────────────
    try:
        from src.map_loader import TibiaMapLoader
        from src.pathfinder import AStarPathfinder
        from src.models import Coordinate, BOUNDS
        import numpy as np
    except ImportError as e:
        warnings.append(f"Skipping map/A* checks (import failed: {e})")
        return errors, warnings

    try:
        loader = TibiaMapLoader()
    except Exception as e:
        warnings.append(f"Skipping map/A* checks (loader failed: {e})")
        return errors, warnings

    # Group coord_steps by floor
    floors_used: set[int] = {z for _, _, _, z in coord_steps}

    grids: dict[int, Any] = {}
    pathfinders: dict[int, AStarPathfinder] = {}
    for floor in floors_used:
        try:
            g = _build_grid(loader, floor, walkable_overrides, blocked_regions)
            grids[floor] = g
            pathfinders[floor] = AStarPathfinder(g, max_nodes=500_000)
        except Exception as e:
            warnings.append(f"Skipping floor {floor} map checks: {e}")

    x_off = BOUNDS["xMin"]
    y_off = BOUNDS["yMin"]

    # 6. Walkability per waypoint
    for idx, x, y, z in coord_steps:
        if z not in grids:
            continue
        grid = grids[z]
        h, w = grid.shape
        px, py = x - x_off, y - y_off
        if 0 <= py < h and 0 <= px < w:
            if not grid[py, px]:
                errors.append(
                    f"instruction[{idx}]: waypoint ({x},{y},z={z}) is NOT walkable "
                    f"on floor map (after overrides+blocks) — bot will be stuck here"
                )
        else:
            warnings.append(
                f"instruction[{idx}]: ({x},{y},z={z}) is outside map bounds"
            )

    if not run_astar:
        return errors, warnings

    # 7. A* reachability between consecutive same-floor waypoints
    prev_step = None
    for idx, x, y, z in coord_steps:
        if prev_step is None:
            prev_step = (idx, x, y, z)
            continue
        pi, px, py, pz = prev_step
        prev_step = (idx, x, y, z)

        if pz != z:
            continue   # floor change — handled by transition logic, skip
        if z not in pathfinders:
            continue

        pf = pathfinders[z]
        try:
            start = Coordinate(x=px, y=py, z=z)
            goal  = Coordinate(x=x,  y=y,  z=z)
            route = pf.find_path(start, goal)
        except Exception as e:
            warnings.append(
                f"instruction[{idx}]: A* from ({px},{py})→({x},{y}) raised {e}"
            )
            continue

        if not route.found:
            errors.append(
                f"instruction[{idx}]: A* found NO PATH from ({px},{py}) → ({x},{y}) z={z} "
                f"— segment is unreachable with current blocked_regions"
            )
        else:
            direct = _mdist(px, py, x, y)
            actual = len(route.steps) - 1 if route.steps else 0
            max_allowed = max(direct + 12, math.ceil(direct * STRETCH_WARN_RATIO))
            if actual > max_allowed:
                warnings.append(
                    f"instruction[{idx}]: path ({px},{py})→({x},{y}) needs {actual} steps "
                    f"but direct dist={direct} (ratio={actual/max(direct,1):.1f}x) — "
                    f"run_phase3 will abort this segment (stretch limit={max_allowed})"
                )

    return errors, warnings


# ── CLI ───────────────────────────────────────────────────────────────────────

def validate_path(target: Path, run_astar: bool = True) -> tuple[int, int]:
    files = sorted(target.glob("*.json")) if target.is_dir() else [target]
    valid = error_count = 0

    for f in files:
        errors, warnings = validate_route_full(f, run_astar=run_astar)

        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            sc = d.get("script") or d.get("waypoints") or d.get("entries") or []
            n_instr = len(sc)
        except Exception:
            n_instr = 0

        if errors:
            error_count += 1
            print(f"  [{RED}FAIL{RESET}] {f.name}  ({n_instr} instructions)")
            for e in errors:
                print(f"         {RED}ERR{RESET}  {e}")
            for w in warnings:
                print(f"         {YELLOW}WARN{RESET} {w}")
        else:
            valid += 1
            tag = f"{GREEN}OK{RESET}  "
            warn_str = f"  {YELLOW}({len(warnings)} warnings){RESET}" if warnings else ""
            print(f"  [{tag}] {f.name}  ({n_instr} instructions){warn_str}")
            for w in warnings:
                print(f"         {YELLOW}WARN{RESET} {w}")

    return valid, error_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Route JSON validator")
    parser.add_argument("target", nargs="?", default="",
                        help="Path to .json file or directory of routes")
    parser.add_argument("--all", action="store_true",
                        help="Validate all routes in routes/ directory")
    parser.add_argument("--no-astar", action="store_true",
                        help="Skip A* reachability checks (faster)")
    args = parser.parse_args()

    if args.all or not args.target:
        target = ROOT / "routes"
    else:
        target = Path(args.target)
        if not target.is_absolute():
            target = ROOT / target

    run_astar = not args.no_astar
    mode = "schema+walkability" if not run_astar else "full (schema+walkability+A*)"
    print(f"\nValidating: {target}  [{CYAN}{mode}{RESET}]\n")
    ok, fail = validate_path(target, run_astar=run_astar)
    print(f"\n  {GREEN}{ok} valid{RESET}, {RED}{fail} with errors{RESET}\n")
    sys.exit(1 if fail > 0 else 0)


if __name__ == "__main__":
    main()
