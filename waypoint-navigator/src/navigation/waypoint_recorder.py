from __future__ import annotations
"""Recorder utilities for waypoint capture.

This module provides two helpers:

- `WaypointRecorder`: a convenience wrapper around `WaypointLogger` that
    manages attachment to a `ScriptExecutor`, `out_path`, and optional
    autosave. Use this in integration scenarios where you run scripts and
    want a rich export (waypoints + actions + timestamps) and CloudBot/project
    JSON exports.

- `SimpleRouteRecorder`: a minimal, fast recorder that stores only simple
    waypoint tuples (x,y,z) and optional labels. Use this for quick manual
    captures, unit tests, or situations where you don't need action metadata.

When to use which:
- Use `WaypointRecorder` when running the real executor or when you need
    the full `WaypointLogger` features (timestamps, action records, exports).
- Use `SimpleRouteRecorder` for quick captures, small utilities, or
    when you want a tiny JSON with only waypoint coordinates.

Examples:
    - Integration: `rec = WaypointRecorder(map_name='route', out_path='routes/r.json'); rec.attach(executor)`
    - Quick capture: `r = SimpleRouteRecorder(); r.add(32347,32226,7,'start'); r.save('routes/simple.json')`
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .waypoint_logger import WaypointLogger


class WaypointRecorder:
    """Convenience wrapper around `WaypointLogger`.

    - Manages output path and autosave.
    - Can be attached to a `ScriptExecutor` via `attach(executor)` which sets
      the executor's `waypoint_logger` (used by the modified ScriptExecutor).
    """

    def __init__(self, map_name: str = "route", out_path: Optional[str] = None, autosave: bool = True):
        self.logger = WaypointLogger(map_name=map_name)
        self.autosave = autosave
        self.out_path = Path(out_path) if out_path else None

    def attach(self, executor: Any) -> None:
        """Attach this recorder to a ScriptExecutor instance.

        The ScriptExecutor already supports an optional `waypoint_logger`
        parameter; this method sets that attribute so the executor will
        record waypoints/actions into this recorder's `WaypointLogger`.
        """
        setattr(executor, "_wp_logger", self.logger)

    def save(self, path: Optional[str] = None) -> None:
        """Save the recorded data to disk.

        If *path* is provided, use it; otherwise use the recorder's
        configured `out_path`, or default to `routes/recorded_waypoints.json`.
        """
        p = Path(path) if path else self.out_path
        if p is None:
            from src.config_paths import ROUTES_DIR
            p = ROUTES_DIR / "recorded_waypoints.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        self.logger.save_json(str(p))

    def load(self, path: str) -> None:
        """Load an existing JSON into the recorder's logger."""
        self.logger.load_json(path)

    def __enter__(self) -> "WaypointRecorder":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.autosave:
            try:
                self.save()
            except Exception as save_exc:
                import logging
                logging.getLogger("wn.wr").error("autosave failed: %s", save_exc)


class SimpleRouteRecorder:
    """A very small and simple recorder for quick route captures.

    API:
    - add(point) where point is (x,y,z) or dict
    - save(path) -> writes simple JSON {"waypoints": [...]}.
    """

    def __init__(self) -> None:
        self.waypoints: List[Dict[str, Any]] = []

    def add(self, x: Any = None, y: Any = None, z: Any = None, label: Optional[str] = None) -> None:
        if isinstance(x, dict):
            pos = x
            x = pos.get("x")
            y = pos.get("y")
            z = pos.get("z")
            label = pos.get("label", label)

        if x is None or y is None or z is None:
            raise ValueError("Waypoint requires x, y and z coordinates")
        self.waypoints.append({"x": int(x), "y": int(y), "z": int(z), "label": label})

    def to_dict(self) -> Dict[str, List[Dict[str, Any]]]:
        return {"waypoints": self.waypoints}

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

