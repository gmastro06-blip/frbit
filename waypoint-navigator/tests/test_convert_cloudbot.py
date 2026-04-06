"""
Tests for tools/convert_cloudbot.py
"""
from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

# Make sure 'tools' package is importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.convert_cloudbot import (
    Waypoint,
    convert_file,
    group_by_label,
    parse_in_file,
    to_json_dict,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


SIMPLE_IN = """\
# example script
label start
node (100, 200, 7)
stand (110, 210, 7)

label hunt
door (120, 220, 7)
rope (130, 230, 8)
ladder (140, 240, 9)
"""

MULTILINE_IN = """\
label start
node (1000,
1001, 5)
"""

COMMENTS_ONLY = """\
# just a comment
# another
"""


# ─────────────────────────────────────────────────────────────────────────────
# parse_in_file
# ─────────────────────────────────────────────────────────────────────────────

class TestParseInFile:

    def test_parses_basic_waypoints(self, tmp_path):
        p = _write(tmp_path, "wp.in", SIMPLE_IN)
        wps = parse_in_file(p)
        assert len(wps) == 5

    def test_correct_actions(self, tmp_path):
        p = _write(tmp_path, "wp.in", SIMPLE_IN)
        wps = parse_in_file(p)
        actions = [w.action for w in wps]
        assert actions == ["walk", "stand", "door", "rope", "ladder"]

    def test_correct_labels(self, tmp_path):
        p = _write(tmp_path, "wp.in", SIMPLE_IN)
        wps = parse_in_file(p)
        assert wps[0].label == "start"
        assert wps[1].label == "start"
        assert wps[2].label == "hunt"
        assert wps[4].label == "hunt"

    def test_correct_coordinates(self, tmp_path):
        p = _write(tmp_path, "wp.in", SIMPLE_IN)
        wps = parse_in_file(p)
        first = wps[0]
        assert (first.x, first.y, first.z) == (100, 200, 7)

    def test_multiline_coord_parsed(self, tmp_path):
        p = _write(tmp_path, "wp.in", MULTILINE_IN)
        wps = parse_in_file(p)
        assert len(wps) == 1
        assert (wps[0].x, wps[0].y, wps[0].z) == (1000, 1001, 5)

    def test_comments_ignored(self, tmp_path):
        p = _write(tmp_path, "wp.in", COMMENTS_ONLY)
        wps = parse_in_file(p)
        assert wps == []

    def test_default_label_is_start(self, tmp_path):
        """No explicit label → implicit 'start' label."""
        p = _write(tmp_path, "wp.in", "node (1,2,3)\n")
        wps = parse_in_file(p)
        assert wps[0].label == "start"

    def test_action_lines_produce_no_waypoints(self, tmp_path):
        content = "label start\naction summon\nnode (1,2,3)\n"
        p = _write(tmp_path, "wp.in", content)
        wps = parse_in_file(p)
        assert len(wps) == 1  # only the node


# ─────────────────────────────────────────────────────────────────────────────
# group_by_label
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupByLabel:

    def test_groups_correctly(self, tmp_path):
        p = _write(tmp_path, "wp.in", SIMPLE_IN)
        wps = parse_in_file(p)
        groups = group_by_label(wps)
        assert set(groups.keys()) == {"start", "hunt"}
        assert len(groups["start"]) == 2
        assert len(groups["hunt"])  == 3

    def test_single_label(self, tmp_path):
        p = _write(tmp_path, "wp.in", "node (1,2,3)\nnode (4,5,6)\n")
        wps = parse_in_file(p)
        groups = group_by_label(wps)
        assert list(groups.keys()) == ["start"]
        assert len(groups["start"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# to_json_dict
# ─────────────────────────────────────────────────────────────────────────────

class TestToJsonDict:

    def _wps(self):
        return [
            Waypoint(100, 200, 7, "walk",   "start"),
            Waypoint(110, 210, 7, "stand",  "start"),
        ]

    def test_structure(self):
        d = to_json_dict(self._wps(), "myscript", "start")
        assert d["name"]          == "myscript / start"
        assert d["source"]        == "cloudbot"
        assert d["source_script"] == "myscript"
        assert d["label"]         == "start"
        assert isinstance(d["waypoints"], list)
        assert len(d["waypoints"]) == 2

    def test_waypoint_fields(self):
        d = to_json_dict(self._wps(), "script", "all")
        wp0 = d["waypoints"][0]
        assert wp0["x"]      == 100
        assert wp0["y"]      == 200
        assert wp0["z"]      == 7
        assert wp0["action"] == "walk"
        assert wp0["name"]   == "walk_0000"

    def test_waypoint_name_sequence(self):
        d = to_json_dict(self._wps(), "s", "l")
        names = [w["name"] for w in d["waypoints"]]
        assert names == ["walk_0000", "stand_0001"]

    def test_empty_waypoints(self):
        d = to_json_dict([], "s", "empty")
        assert d["waypoints"] == []


# ─────────────────────────────────────────────────────────────────────────────
# convert_file — single output
# ─────────────────────────────────────────────────────────────────────────────

class TestConvertFile:

    def test_creates_json_file(self, tmp_path):
        src = _write(tmp_path, "wp.in", SIMPLE_IN)
        out = tmp_path / "output.json"
        paths = convert_file(src, out)
        assert len(paths) == 1
        assert out.exists()

    def test_json_is_valid(self, tmp_path):
        src = _write(tmp_path, "wp.in", SIMPLE_IN)
        out = tmp_path / "output.json"
        convert_file(src, out)
        data = json.loads(out.read_text())
        assert "waypoints" in data
        assert len(data["waypoints"]) == 5

    def test_split_labels_creates_multiple_files(self, tmp_path):
        src = _write(tmp_path, "wp.in", SIMPLE_IN)
        out_dir = tmp_path / "out"
        paths = convert_file(src, out_dir, split_labels=True)
        assert len(paths) == 2
        # naming: <parent_dir_name>__<label>.json
        # in_path.parent.name is the pytest tmp_path directory name
        expected_labels = {p.stem.split("__")[-1] for p in paths}
        assert expected_labels == {"start", "hunt"}

    def test_only_label_filters(self, tmp_path):
        src = _write(tmp_path, "wp.in", SIMPLE_IN)
        out = tmp_path / "hunt.json"
        paths = convert_file(src, out, only_label="hunt")
        assert len(paths) == 1
        data = json.loads(out.read_text())
        assert len(data["waypoints"]) == 3
        assert all(w["action"] in {"door", "rope", "ladder"} for w in data["waypoints"])

    def test_empty_script_produces_no_file(self, tmp_path):
        """Scripts with no waypoints → no JSON written, empty paths list returned."""
        src = _write(tmp_path, "empty.in", "# nothing here\n")
        out = tmp_path / "empty.json"
        paths = convert_file(src, out)
        assert paths == []
        assert not out.exists()
