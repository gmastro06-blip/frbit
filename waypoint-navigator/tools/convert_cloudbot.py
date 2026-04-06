"""convert_cloudbot.py
---------------------
Convert CloudBot ``waypoints.in`` scripts to waypoint-navigator JSON routes.

CloudBot script repository: https://github.com/CloudBotScripts/scripts

Supported CloudBot waypoint keywords:
    node   (x, y, z)  → action "walk"   (regular move)
    stand  (x, y, z)  → action "stand"  (walk + brief wait)
    door   (x, y, z)  → action "door"   (walk + open door)
    rope   (x, y, z)  → action "rope"   (walk + use rope)
    ladder (x, y, z)  → action "ladder" (walk + use ladder)

Labels (``label <name>``) partition the script into named sections.
Actions (``action <name>``), loads (``load <file>``) and comments are
preserved in metadata but do not produce waypoints.

Output JSON format (one per label section or merged)::

    {
        "name":          "<script_name> / <label>",
        "source":        "cloudbot",
        "source_script": "<script_name>",
        "label":         "<label>",
        "waypoints": [
            {"name": "walk_0000", "x": 33645, "y": 32012, "z": 12, "action": "walk"},
            {"name": "door_0001", "x": 33647, "y": 31947, "z":  7, "action": "door"},
            ...
        ]
    }

Usage examples::

    # Single file → single JSON (all waypoints merged)
    python tools/convert_cloudbot.py glooth_bandit_east/waypoints.in -o routes/glooth_bandit_east.json

    # Single file → one JSON per label section
    python tools/convert_cloudbot.py glooth_bandit_east/waypoints.in -o routes/glooth_east/ --split-labels

    # Extract only the 'hunt' section
    python tools/convert_cloudbot.py glooth_bandit_east/waypoints.in -o routes/glooth_east_hunt.json --label hunt

    # Batch: entire cloned CloudBot repo → routes/cloudbot/ (one JSON per label per script)
    python tools/convert_cloudbot.py cloudbot_scripts/ -o routes/cloudbot/ --batch
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Waypoint(NamedTuple):
    x: int
    y: int
    z: int
    action: str   # "walk" | "stand" | "door" | "rope" | "ladder"
    label: str    # CloudBot section label this point belongs to


# ---------------------------------------------------------------------------
# Parsing constants
# ---------------------------------------------------------------------------

# CloudBot keyword → action name for our format
_KEYWORD_MAP: Dict[str, str] = {
    "node":   "walk",
    "stand":  "stand",
    "door":   "door",
    "rope":   "rope",
    "ladder": "ladder",
}

# Regex that matches a (x, y, z) coordinate tuple anywhere on a line.
# Numbers may be negative.
_COORD_RE = re.compile(
    r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)"
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_in_file(path: Path) -> List[Waypoint]:
    """Parse a CloudBot ``waypoints.in`` file into a list of :class:`Waypoint`.

    The parser normalises multi-line coordinate tuples (CloudBot sometimes
    wraps ``(x,\\ny)`` across two source lines) by collapsing continuation
    lines before pattern-matching.

    Parameters
    ----------
    path:
        Absolute or relative path to the ``waypoints.in`` file.

    Returns
    -------
    list[Waypoint]
        Ordered list of waypoints preserving source order.  Each waypoint
        carries the name of the ``label`` section it belongs to.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    waypoints: List[Waypoint] = []
    current_label = "start"

    # ── Step 1: normalise multi-line tuples ──────────────────────────────
    # Accumulate source characters into logical lines; a "logical line" is
    # flushed when open-paren count equals close-paren count.
    logical_lines: List[str] = []
    buf = ""
    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        # Skip blank lines and comments (# prefix)
        if not stripped or stripped.startswith("#"):
            if buf:
                logical_lines.append(buf)
                buf = ""
            continue
        buf = (buf + " " + stripped).strip() if buf else stripped
        # Flush when parentheses balance (or no parens at all)
        if buf.count("(") == buf.count(")"):
            logical_lines.append(buf)
            buf = ""
    if buf:
        logical_lines.append(buf)

    # ── Step 2: classify each logical line ───────────────────────────────
    for line in logical_lines:
        first_token = line.split()[0].lower() if line.split() else ""

        if first_token == "label":
            parts = line.split(None, 1)
            current_label = parts[1].strip() if len(parts) > 1 else "unnamed"
            continue

        if first_token in _KEYWORD_MAP:
            action = _KEYWORD_MAP[first_token]
            m = _COORD_RE.search(line)
            if m:
                x = int(m.group(1))
                y = int(m.group(2))
                z = int(m.group(3))
                waypoints.append(Waypoint(x, y, z, action, current_label))
            # else: malformed coordinate — silently skip
            continue

        # action <name>, load <file>, etc. → no waypoints produced

    return waypoints


# ---------------------------------------------------------------------------
# Grouping / output helpers
# ---------------------------------------------------------------------------

def group_by_label(waypoints: List[Waypoint]) -> Dict[str, List[Waypoint]]:
    """Return an ordered dict mapping label name → list of :class:`Waypoint`."""
    groups: Dict[str, List[Waypoint]] = {}
    for wp in waypoints:
        groups.setdefault(wp.label, []).append(wp)
    return groups


def to_json_dict(
    waypoints: List[Waypoint],
    script_name: str,
    label: str,
) -> dict:
    """Build the waypoint-navigator JSON dict for a list of waypoints.

    Parameters
    ----------
    waypoints:
        Waypoints to serialise.
    script_name:
        CloudBot script folder name (e.g. ``"glooth_bandit_east"``).
    label:
        Label section name (e.g. ``"hunt"`` or ``"all"``).
    """
    wps = [
        {
            "name":   f"{wp.action}_{i:04d}",
            "x":      wp.x,
            "y":      wp.y,
            "z":      wp.z,
            "action": wp.action,
        }
        for i, wp in enumerate(waypoints)
    ]
    return {
        "name":          f"{script_name} / {label}",
        "source":        "cloudbot",
        "source_script": script_name,
        "label":         label,
        "waypoints":     wps,
    }


# ---------------------------------------------------------------------------
# File-level conversion
# ---------------------------------------------------------------------------

def convert_file(
    in_path: Path,
    out_path: Path,
    script_name: str = "",
    split_labels: bool = False,
    only_label: str = "",
) -> List[Path]:
    """Convert a single ``waypoints.in`` to one or more JSON route files.

    Parameters
    ----------
    in_path:
        Source CloudBot ``waypoints.in``.
    out_path:
        Destination path.  When ``split_labels=True`` this is treated as a
        directory; otherwise it must be a ``.json`` file path.
    script_name:
        Human-readable script identifier.  Defaults to the parent folder name.
    split_labels:
        When *True*, produce one JSON per label section inside ``out_path/``.
    only_label:
        When set, only emit waypoints from this label section.

    Returns
    -------
    list[Path]
        Paths of JSON files that were successfully written.
    """
    sname = script_name or in_path.parent.name or in_path.stem
    waypoints = parse_in_file(in_path)

    if not waypoints:
        print(f"  [WARN] {in_path}: no waypoints found — skipping")
        return []

    written: List[Path] = []

    if split_labels:
        groups = group_by_label(waypoints)
        out_dir = out_path if out_path.suffix == "" else out_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        for lbl, wps in groups.items():
            if only_label and lbl != only_label:
                continue
            if not wps:
                continue
            payload = to_json_dict(wps, sname, lbl)
            dest = out_dir / f"{sname}__{lbl}.json"
            dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"  ✓  {dest}  ({len(wps)} waypoints)")
            written.append(dest)
    else:
        if only_label:
            groups = group_by_label(waypoints)
            wps = groups.get(only_label, [])
        else:
            wps = waypoints

        if not wps:
            print(f"  [WARN] label '{only_label}' not found in {in_path}")
            return []

        lbl = only_label or "all"
        payload = to_json_dict(wps, sname, lbl)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  ✓  {out_path}  ({len(wps)} waypoints)")
        written.append(out_path)

    return written


# ---------------------------------------------------------------------------
# Batch conversion (whole cloned CloudBot repo)
# ---------------------------------------------------------------------------

def batch_convert(
    scripts_dir: Path,
    out_dir: Path,
    split_labels: bool = True,
    only_label: str = "",
) -> int:
    """Recursively find every ``waypoints.in`` under *scripts_dir* and convert.

    Parameters
    ----------
    scripts_dir:
        Root of a cloned CloudBot scripts repository (or any directory tree
        that contains ``waypoints.in`` files in subdirectories).
    out_dir:
        Output directory.  Files are written to
        ``out_dir/<script_name>/<label>.json`` (or merged).
    split_labels:
        Produce one JSON per label section (default *True* for batch mode).
    only_label:
        If set, only extract this label across all scripts.

    Returns
    -------
    int
        Number of JSON files written.
    """
    total = 0
    for in_path in sorted(scripts_dir.rglob("waypoints.in")):
        script_name = in_path.parent.name
        print(f"\n[{script_name}]")
        written = convert_file(
            in_path=in_path,
            out_path=out_dir / script_name / "waypoints.json",
            script_name=script_name,
            split_labels=split_labels,
            only_label=only_label,
        )
        total += len(written)
    return total


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="convert_cloudbot",
        description="Convert CloudBot waypoints.in to waypoint-navigator JSON routes.",
    )
    p.add_argument(
        "input",
        help=(
            "Path to a single waypoints.in file, OR a directory containing "
            "CloudBot scripts (use with --batch)."
        ),
    )
    p.add_argument(
        "-o", "--output",
        required=True,
        help=(
            "Output JSON file path, or output directory when --batch or "
            "--split-labels is used."
        ),
    )
    p.add_argument(
        "--label",
        default="",
        help=(
            "Extract only this label section (e.g. 'hunt').  "
            "When omitted all waypoints are included."
        ),
    )
    p.add_argument(
        "--split-labels",
        action="store_true",
        help="Produce one JSON file per label section.",
    )
    p.add_argument(
        "--batch",
        action="store_true",
        help=(
            "Input is a directory; recursively convert every waypoints.in found."
        ),
    )
    return p


def main(argv: Optional[List[str]] = None) -> None:
    """CLI entry point — can also be called programmatically with *argv* list."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    src = Path(args.input)
    dst = Path(args.output)

    if args.batch or src.is_dir():
        if not src.is_dir():
            print(f"ERROR: '{src}' is not a directory.", file=sys.stderr)
            sys.exit(1)
        n = batch_convert(
            scripts_dir=src,
            out_dir=dst,
            split_labels=True,
            only_label=args.label,
        )
        print(f"\nDone — {n} JSON file(s) written to {dst}")
    else:
        if not src.exists():
            print(f"ERROR: '{src}' not found.", file=sys.stderr)
            sys.exit(1)
        written = convert_file(
            in_path=src,
            out_path=dst,
            split_labels=args.split_labels,
            only_label=args.label,
        )
        if not written:
            print("No output written.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
