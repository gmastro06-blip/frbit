from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .script_parser import ScriptCoord, ScriptParser


@dataclass(frozen=True)
class CoordinateValidationSummary:
    count: int
    min_x: int
    max_x: int
    min_y: int
    max_y: int
    min_z: int
    max_z: int


class RouteJsonSimulator:
    """Load a unified route JSON and validate its coordinate data.

    This helper class is useful to verify that a JSON route file has valid
    Tibia coordinates in its ``script``, ``waypoints`` or ``entries`` arrays,
    and to inspect the generated coordinate sequence.
    """

    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data

    @classmethod
    def from_file(cls, path: Path) -> "RouteJsonSimulator":
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Route JSON root must be an object")
        return cls(data)

    def validate_coordinates(self) -> List[str]:
        errors: List[str] = []

        if not isinstance(self.data, dict):
            errors.append("Route JSON must be an object")
            return errors

        errors.extend(self._validate_meta_start_coord())
        errors.extend(self._validate_script_coordinates())
        errors.extend(self._validate_waypoint_list("waypoints"))
        errors.extend(self._validate_waypoint_list("entries"))
        return errors

    def get_coordinate_sequence(self) -> List[ScriptCoord]:
        coords: List[ScriptCoord] = []
        start = self._parse_start_coord()
        if start is not None:
            coords.append(start)

        if script := self.data.get("script"):
            if isinstance(script, list):
                coords.extend(self._coords_from_script(script))

        elif waypoints := self.data.get("waypoints"):
            coords.extend(self._coords_from_waypoints(waypoints))

        elif entries := self.data.get("entries"):
            coords.extend(self._coords_from_waypoints(entries))

        return coords

    def get_coordinate_summary(self) -> Optional[CoordinateValidationSummary]:
        coords = self.get_coordinate_sequence()
        if not coords:
            return None
        xs = [coord.x for coord in coords]
        ys = [coord.y for coord in coords]
        zs = [coord.z for coord in coords]
        return CoordinateValidationSummary(
            count=len(coords),
            min_x=min(xs),
            max_x=max(xs),
            min_y=min(ys),
            max_y=max(ys),
            min_z=min(zs),
            max_z=max(zs),
        )

    def _validate_meta_start_coord(self) -> List[str]:
        errors: List[str] = []
        meta = self.data.get("_meta")
        if not isinstance(meta, dict):
            return errors
        start = meta.get("start_coord")
        if start is None:
            return errors
        if not isinstance(start, dict):
            errors.append("_meta.start_coord must be an object")
            return errors
        errors.extend(self._validate_coord_object(start, "_meta.start_coord"))
        return errors

    def _validate_script_coordinates(self) -> List[str]:
        errors: List[str] = []
        if "script" not in self.data:
            return errors
        script = self.data["script"]
        if not isinstance(script, list):
            errors.append("script must be a list")
            return errors

        instructions = ScriptParser.from_json_script(script)
        errors.extend(ScriptParser.validate_labels(instructions))

        prev_coord: Optional[Tuple[int, int, int]] = None
        for idx, ins in enumerate(instructions):
            if ins.is_movement and ins.coord is None:
                errors.append(f"script[{idx}]: movement instruction '{ins.kind}' missing coordinates")
                continue
            if ins.coord is not None:
                errors.extend(self._validate_coord_element(ins.coord, f"script[{idx}].coord"))
                current = (ins.coord.x, ins.coord.y, ins.coord.z)
                if current == prev_coord:
                    errors.append(f"script[{idx}]: duplicate consecutive coordinate {current}")
                prev_coord = current
        return errors

    def _validate_waypoint_list(self, key: str) -> List[str]:
        errors: List[str] = []
        if key not in self.data:
            return errors
        items = self.data[key]
        if not isinstance(items, list):
            errors.append(f"{key} must be a list")
            return errors

        prev_coord: Optional[Tuple[int, int, int]] = None
        for idx, item in enumerate(items):
            if isinstance(item, dict) and "x" in item and "y" in item and "z" in item:
                errors.extend(self._validate_coord_object(item, f"{key}[{idx}]") )
                if self._is_valid_coord_dict(item):
                    current = (int(item["x"]), int(item["y"]), int(item["z"]))
                    if current == prev_coord:
                        errors.append(f"{key}[{idx}]: duplicate consecutive coordinate {current}")
                    prev_coord = current
            elif isinstance(item, (list, tuple)) and len(item) >= 3:
                coord_dict = {"x": item[0], "y": item[1], "z": item[2]}
                errors.extend(self._validate_coord_object(coord_dict, f"{key}[{idx}]") )
                if self._is_valid_coord_dict(coord_dict):
                    current = (int(coord_dict["x"]), int(coord_dict["y"]), int(coord_dict["z"]))
                    if current == prev_coord:
                        errors.append(f"{key}[{idx}]: duplicate consecutive coordinate {current}")
                    prev_coord = current
        return errors

    def _coords_from_script(self, script: Sequence[Any]) -> List[ScriptCoord]:
        coordinates: List[ScriptCoord] = []
        instructions = ScriptParser.from_json_script(list(script))
        for ins in instructions:
            if ins.coord is not None:
                coordinates.append(ins.coord)
        return coordinates

    def _coords_from_waypoints(self, items: Sequence[Any]) -> List[ScriptCoord]:
        coordinates: List[ScriptCoord] = []
        for item in items:
            if isinstance(item, dict) and "x" in item and "y" in item and "z" in item:
                try:
                    coordinates.append(ScriptCoord(int(item["x"]), int(item["y"]), int(item["z"])))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, (list, tuple)) and len(item) >= 3:
                try:
                    coordinates.append(ScriptCoord(int(item[0]), int(item[1]), int(item[2])))
                except (TypeError, ValueError):
                    continue
        return coordinates

    def _parse_start_coord(self) -> Optional[ScriptCoord]:
        meta = self.data.get("_meta")
        if not isinstance(meta, dict):
            return None
        start = meta.get("start_coord")
        if not isinstance(start, dict):
            return None
        if self._is_valid_coord_dict(start):
            return ScriptCoord(int(start["x"]), int(start["y"]), int(start["z"]))
        return None

    def _validate_coord_object(self, obj: Dict[str, Any], prefix: str) -> List[str]:
        errors: List[str] = []
        for field_name in ("x", "y", "z"):
            if field_name not in obj:
                errors.append(f"{prefix}: missing '{field_name}'")
                continue
            if not isinstance(obj[field_name], int):
                errors.append(
                    f"{prefix}.{field_name}: expected integer, got {type(obj[field_name]).__name__}"
                )

        if self._is_valid_coord_dict(obj):
            x = int(obj["x"])
            y = int(obj["y"])
            z = int(obj["z"])
            if not (30000 <= x <= 35000):
                errors.append(f"{prefix}: x={x} outside typical Tibia range [30000-35000]")
            if not (30000 <= y <= 35000):
                errors.append(f"{prefix}: y={y} outside typical Tibia range [30000-35000]")
            if not (0 <= z <= 15):
                errors.append(f"{prefix}: z={z} outside range [0-15]")
        return errors

    def _validate_coord_element(self, coord: ScriptCoord, prefix: str) -> List[str]:
        return self._validate_coord_object({"x": coord.x, "y": coord.y, "z": coord.z}, prefix)

    @staticmethod
    def _is_valid_coord_dict(obj: Dict[str, Any]) -> bool:
        return (
            isinstance(obj.get("x"), int)
            and isinstance(obj.get("y"), int)
            and isinstance(obj.get("z"), int)
        )
