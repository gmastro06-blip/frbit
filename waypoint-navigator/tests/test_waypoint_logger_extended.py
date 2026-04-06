"""
tests/test_waypoint_logger_extended.py
=======================================
Covers the previously-untested branches in WaypointLogger:
  - to_dict()           — lines 98-135
  - save_json()         — lines 139-141
  - load_json()         — lines 148-182
  - export_tibia_map_io() — line 186
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Tuple

import pytest


def _imports() -> Tuple[Any, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    from navigation.waypoint_logger import WaypointLogger, Position
    return WaypointLogger, Position


# ---------------------------------------------------------------------------
# to_dict()
# ---------------------------------------------------------------------------

class TestToDict:

    def test_schema_version(self):
        WaypointLogger, Position = _imports()
        d = WaypointLogger("r").to_dict()
        assert d["schema_version"] == 1

    def test_map_name_in_dict(self):
        WaypointLogger, Position = _imports()
        d = WaypointLogger("thais").to_dict()
        assert d["map_name"] == "thais"

    def test_origin_set(self):
        WaypointLogger, Position = _imports()
        origin = Position(100, 200, 7)
        d = WaypointLogger("r", origin=origin).to_dict()
        assert d["origin"] == {"x": 100, "y": 200, "z": 7}

    def test_origin_none(self):
        WaypointLogger, Position = _imports()
        d = WaypointLogger("r").to_dict()
        assert d["origin"] is None

    def test_waypoints_structure(self):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("r")
        logger.add_waypoint(10, 20, 7, action="walk", label="A")
        d = logger.to_dict()
        assert len(d["waypoints"]) == 1
        wp = d["waypoints"][0]
        assert wp["id"] == 1
        assert wp["label"] == "A"
        assert wp["action"] == "walk"
        assert wp["position"] == {"x": 10, "y": 20, "z": 7}
        assert isinstance(wp["timestamp"], float)

    def test_waypoint_params_included(self):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("r")
        logger.add_waypoint(1, 2, 3, params={"key": "val"})
        d = logger.to_dict()
        assert d["waypoints"][0]["params"] == {"key": "val"}

    def test_action_with_position(self):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("r")
        pos = Position(5, 6, 7)
        logger.record_action("move", "moved", position=pos)
        d = logger.to_dict()
        assert len(d["actions"]) == 1
        a = d["actions"][0]
        assert a["type"] == "move"
        assert a["description"] == "moved"
        assert a["position"] == {"x": 5, "y": 6, "z": 7}

    def test_action_without_position(self):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("r")
        logger.record_action("event", "something happened")
        d = logger.to_dict()
        assert d["actions"][0]["position"] is None

    def test_action_meta_included(self):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("r")
        logger.record_action("loot", "autopick", meta={"item": "gold coin"})
        d = logger.to_dict()
        assert d["actions"][0]["meta"] == {"item": "gold coin"}

    def test_generated_at_is_float(self):
        WaypointLogger, Position = _imports()
        d = WaypointLogger("r").to_dict()
        assert isinstance(d["generated_at"], float)

    def test_empty_waypoints_and_actions(self):
        WaypointLogger, Position = _imports()
        d = WaypointLogger("r").to_dict()
        assert d["waypoints"] == []
        assert d["actions"] == []


# ---------------------------------------------------------------------------
# save_json()
# ---------------------------------------------------------------------------

class TestSaveJson:

    def test_file_is_created(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("save_test")
        logger.add_waypoint(1, 2, 3)
        p = tmp_path / "out.json"
        logger.save_json(str(p))
        assert p.exists()

    def test_file_contains_valid_json(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("save_test")
        logger.add_waypoint(10, 20, 7, label="start")
        p = tmp_path / "out.json"
        logger.save_json(str(p))
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["map_name"] == "save_test"
        assert len(data["waypoints"]) == 1

    def test_custom_indent(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("r")
        logger.add_waypoint(1, 2, 3)
        p = tmp_path / "out.json"
        logger.save_json(str(p), indent=4)
        raw = p.read_text(encoding="utf-8")
        # 4-space indent produces lines starting with "    "
        assert "    " in raw


# ---------------------------------------------------------------------------
# load_json()
# ---------------------------------------------------------------------------

class TestLoadJson:

    def _write_and_load(self, tmp_path: Path, data: dict) -> Any:
        WaypointLogger, Position = _imports()
        p = tmp_path / "route.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        logger = WaypointLogger("empty")
        logger.load_json(str(p))
        return logger

    def test_restores_waypoints(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        data = {
            "waypoints": [
                {"id": 3, "position": {"x": 1, "y": 2, "z": 7},
                 "action": "walk", "label": "A", "params": {}, "timestamp": 1.0}
            ],
            "actions": [],
        }
        logger = self._write_and_load(tmp_path, data)
        assert len(logger.waypoints) == 1
        assert logger.waypoints[0].id == 3
        assert logger.waypoints[0].position.x == 1
        assert logger.waypoints[0].position.y == 2
        assert logger.waypoints[0].position.z == 7
        assert logger.waypoints[0].label == "A"

    def test_next_id_advances_past_loaded_max(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        data = {
            "waypoints": [
                {"id": 5, "position": {"x": 1, "y": 2, "z": 7},
                 "action": "walk", "label": None, "params": {}, "timestamp": 0.0}
            ],
            "actions": [],
        }
        logger = self._write_and_load(tmp_path, data)
        new_wp = logger.add_waypoint(3, 4, 7)
        assert new_wp.id == 6   # picked up from loaded max id

    def test_restores_action_with_position(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        data = {
            "waypoints": [],
            "actions": [
                {"timestamp": 10.0, "type": "move", "description": "walked",
                 "position": {"x": 5, "y": 6, "z": 7}, "meta": {}}
            ],
        }
        logger = self._write_and_load(tmp_path, data)
        assert len(logger.actions) == 1
        a = logger.actions[0]
        assert a.type == "move"
        assert a.position is not None
        assert a.position.x == 5
        assert a.position.y == 6
        assert a.position.z == 7

    def test_restores_action_without_position(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        data = {
            "waypoints": [],
            "actions": [
                {"timestamp": 1.0, "type": "event", "description": "something",
                 "position": None, "meta": {}}
            ],
        }
        logger = self._write_and_load(tmp_path, data)
        assert logger.actions[0].position is None

    def test_clears_previous_contents(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("r")
        logger.add_waypoint(99, 99, 7)
        logger.record_action("old", "old action")
        data: dict[str, list[object]] = {"waypoints": [], "actions": []}
        p = tmp_path / "r.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        logger.load_json(str(p))
        assert logger.waypoints == []
        assert logger.actions == []

    def test_missing_keys_use_defaults(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        # Minimal payload — missing optional fields
        data = {
            "waypoints": [{"position": {"x": 1, "y": 2, "z": 3}}],
            "actions": [],
        }
        logger = self._write_and_load(tmp_path, data)
        assert logger.waypoints[0].action == "walk"  # default

    def test_action_meta_restored(self, tmp_path: Path):
        WaypointLogger, Position = _imports()
        data = {
            "waypoints": [],
            "actions": [
                {"timestamp": 1.0, "type": "loot", "description": "autopick",
                 "position": None, "meta": {"npc": "banker"}}
            ],
        }
        logger = self._write_and_load(tmp_path, data)
        assert logger.actions[0].meta == {"npc": "banker"}


# ---------------------------------------------------------------------------
# export_tibia_map_io()
# ---------------------------------------------------------------------------

class TestExportTibiaMapIO:

    def test_returns_valid_json_string(self):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("tibia_route")
        logger.add_waypoint(100, 200, 7, label="start")
        raw = logger.export_tibia_map_io()
        data = json.loads(raw)   # must not raise
        assert data["map_name"] == "tibia_route"

    def test_structure_matches_to_dict(self):
        WaypointLogger, Position = _imports()
        logger = WaypointLogger("r", origin=Position(1, 2, 7))
        logger.add_waypoint(1, 2, 7)
        logger.record_action("ev", "desc")
        raw = logger.export_tibia_map_io()
        data = json.loads(raw)
        assert "waypoints" in data
        assert "actions" in data
        assert data["origin"] == {"x": 1, "y": 2, "z": 7}

    def test_is_string(self):
        WaypointLogger, Position = _imports()
        result = WaypointLogger("r").export_tibia_map_io()
        assert isinstance(result, str)

    def test_pretty_printed(self):
        WaypointLogger, Position = _imports()
        result = WaypointLogger("r").export_tibia_map_io()
        # Default indent=2 means newlines + spaces are present
        assert "\n" in result
