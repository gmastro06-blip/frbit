import json
import os
import sys
from pathlib import Path
from typing import Any, Tuple

import pytest


def _import_logger() -> Tuple[Any, Any]:
    # Ensure src is importable when tests run from repository root
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from navigation.waypoint_logger import WaypointLogger, Position

    return WaypointLogger, Position


def test_cloudbot_export_structure(tmp_path: Path) -> None:
    WaypointLogger, Position = _import_logger()

    logger = WaypointLogger(map_name="test_route", origin=Position(100, 200, 7))
    logger.add_waypoint(100, 200, 7, action="start", label="start")
    logger.add_waypoint(110, 210, 7, action="walk", label="target")
    logger.record_action("talk_npc", "hello", position=Position(110, 210, 7), meta={"npc":"bank"})

    data = logger.export_cloudbot_dict()

    assert data["format"] == "cloudbot-route"
    assert data["version"] == 1
    assert data["name"] == "test_route"
    assert data["waypoints"] and len(data["waypoints"]) == 2
    assert data["actions"] and len(data["actions"]) == 1


def test_save_cloudbot_file(tmp_path: Path) -> None:
    WaypointLogger, Position = _import_logger()

    logger = WaypointLogger(map_name="file_route", origin=Position(1, 2, 0))
    logger.add_waypoint(1, 2, 0)
    logger.record_action("move", "moved to start", position=Position(1, 2, 0))

    out_file = tmp_path / "route_cb.json"
    logger.save_cloudbot(str(out_file))

    assert out_file.exists()
    content = json.loads(out_file.read_text(encoding="utf-8"))
    assert content.get("format") == "cloudbot-route"
    assert "waypoints" in content and isinstance(content["waypoints"], list)
