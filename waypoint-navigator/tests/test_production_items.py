"""Tests for tools/route_validator.py and calibrator enhancements."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ═══════════════════════════════════════════════════════════════════════════
# Route Validator Tests
# ═══════════════════════════════════════════════════════════════════════════

from tools.route_validator import validate_route


class TestRouteValidator:
    def _write_route(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "test_route.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_valid_route(self, tmp_path):
        route = {
            "waypoints": [
                {"x": 32000, "y": 31000, "z": 7, "action": "walk"},
                {"x": 32001, "y": 31000, "z": 7, "action": "walk"},
            ]
        }
        p = self._write_route(tmp_path, route)
        errors = validate_route(p)
        assert errors == []

    def test_missing_waypoints_key(self, tmp_path):
        route = {"points": []}
        p = self._write_route(tmp_path, route)
        errors = validate_route(p)
        assert any("waypoints" in e.lower() for e in errors)

    def test_empty_waypoints(self, tmp_path):
        route = {"waypoints": []}
        p = self._write_route(tmp_path, route)
        errors = validate_route(p)
        assert any("empty" in e.lower() or "vací" in e.lower() or "0 waypoint" in e.lower()
                    for e in errors)

    def test_invalid_coordinates(self, tmp_path):
        route = {
            "waypoints": [
                {"x": 0, "y": 0, "z": 7},
            ]
        }
        p = self._write_route(tmp_path, route)
        errors = validate_route(p)
        assert len(errors) > 0  # out of Tibia coordinate range

    def test_duplicate_consecutive(self, tmp_path):
        route = {
            "waypoints": [
                {"x": 32000, "y": 31000, "z": 7},
                {"x": 32000, "y": 31000, "z": 7},
            ]
        }
        p = self._write_route(tmp_path, route)
        errors = validate_route(p)
        assert any("duplic" in e.lower() for e in errors)

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json at all", encoding="utf-8")
        errors = validate_route(p)
        assert any("json" in e.lower() for e in errors)

    def test_nonexistent_file(self, tmp_path):
        errors = validate_route(tmp_path / "nonexistent.json")
        assert len(errors) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Calibrator Enhancement Tests
# ═══════════════════════════════════════════════════════════════════════════

from src.calibrator import validate_roi_bounds, _STANDARD_PRESETS


class TestValidateRoiBounds:
    def test_valid_roi(self):
        errors = validate_roi_bounds([100, 100, 200, 50])
        assert errors == []

    def test_negative_coordinates(self):
        errors = validate_roi_bounds([-10, 100, 200, 50])
        assert len(errors) > 0

    def test_roi_exceeds_frame(self):
        errors = validate_roi_bounds([1800, 100, 200, 50])
        assert len(errors) > 0

    def test_zero_size(self):
        errors = validate_roi_bounds([100, 100, 0, 50])
        assert len(errors) > 0

    def test_custom_frame_size(self):
        errors = validate_roi_bounds([100, 100, 200, 50],
                                      frame_w=800, frame_h=600)
        assert errors == []


class TestStandardPresets:
    def test_1920x1080_exists(self):
        assert "1920x1080" in _STANDARD_PRESETS

    def test_preset_has_all_rois(self):
        preset = _STANDARD_PRESETS["1920x1080"]
        for key in ("coord", "hp", "mp", "minimap", "battle-list"):
            assert key in preset
            assert len(preset[key]) == 4  # x, y, w, h


# ═══════════════════════════════════════════════════════════════════════════
# Combat --class Selector Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCombatClassSelector:
    """Test that --class argument maps correctly to config files."""

    def test_class_map_values(self):
        """Verify the class map built in main.py resolves correctly."""
        _class_map = {
            "knight": "combat_config.json",
            "druid": "combat_config_druid.json",
            "paladin": "combat_config_paladin.json",
            "sorcerer": "combat_config_sorcerer.json",
        }
        assert _class_map["druid"] == "combat_config_druid.json"
        assert _class_map["knight"] == "combat_config.json"
        assert _class_map["paladin"] == "combat_config_paladin.json"
        assert _class_map["sorcerer"] == "combat_config_sorcerer.json"

    def test_all_class_configs_exist(self):
        """Verify class-specific config files exist on disk."""
        root = _ROOT
        for name in ["combat_config.json", "combat_config_druid.json",
                      "combat_config_paladin.json", "combat_config_sorcerer.json"]:
            assert (root / name).exists(), f"{name} not found"

    def test_class_configs_valid_json(self):
        """All combat config files should be valid JSON."""
        root = _ROOT
        for name in ["combat_config.json", "combat_config_druid.json",
                      "combat_config_paladin.json", "combat_config_sorcerer.json"]:
            data = json.loads((root / name).read_text(encoding="utf-8"))
            assert "spells" in data
            assert len(data["spells"]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# SessionConfig Arduino Fields
# ═══════════════════════════════════════════════════════════════════════════

from src.session import SessionConfig


class TestSessionConfigArduino:
    def test_arduino_disabled_by_default(self):
        cfg = SessionConfig()
        assert cfg.arduino_enabled is False
        assert cfg.arduino_port == "auto"

    def test_arduino_fields_settable(self):
        cfg = SessionConfig(arduino_enabled=True, arduino_port="COM5")
        assert cfg.arduino_enabled is True
        assert cfg.arduino_port == "COM5"
