"""
Tests for src/session_persistence.py — SessionCheckpoint
All tests use tmp_path — no writes to output/.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.session_persistence import SessionCheckpoint


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ckpt(**kwargs) -> SessionCheckpoint:
    return SessionCheckpoint(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestDefaults:

    def test_default_route_file(self):
        assert _ckpt().route_file == ""

    def test_default_waypoint_index(self):
        assert _ckpt().waypoint_index == 0

    def test_default_position(self):
        c = _ckpt()
        assert c.position_x == 0
        assert c.position_y == 0
        assert c.position_z == 7

    def test_default_stats_zero(self):
        c = _ckpt()
        assert c.routes_completed == 0
        assert c.heal_fired == 0
        assert c.mana_fired == 0
        assert c.loot_events == 0
        assert c.uptime_seconds == 0.0

    def test_default_extra_empty(self):
        assert _ckpt().extra == {}


# ─────────────────────────────────────────────────────────────────────────────
# save / load round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveLoad:

    def test_save_creates_file(self, tmp_path):
        c = _ckpt(route_file="route.csv", waypoint_index=5)
        path = tmp_path / "ckpt.json"
        c.save(path)
        assert path.exists()

    def test_save_produces_valid_json(self, tmp_path):
        c = _ckpt(route_file="r.csv")
        path = tmp_path / "ckpt.json"
        c.save(path)
        with open(path) as f:
            data = json.load(f)
        assert data["route_file"] == "r.csv"

    def test_save_sets_timestamp(self, tmp_path):
        c = _ckpt()
        assert c.timestamp == 0.0
        path = tmp_path / "ckpt.json"
        before = time.time()
        c.save(path)
        after = time.time()
        assert before <= c.timestamp <= after

    def test_save_sets_timestamp_iso(self, tmp_path):
        c = _ckpt()
        c.save(tmp_path / "ckpt.json")
        assert "T" in c.timestamp_iso

    def test_save_creates_parent_dirs(self, tmp_path):
        c = _ckpt()
        path = tmp_path / "nested" / "deep" / "ckpt.json"
        c.save(path)
        assert path.exists()

    def test_save_tmp_file_removed_on_success(self, tmp_path):
        c = _ckpt()
        path = tmp_path / "ckpt.json"
        c.save(path)
        assert not (tmp_path / "ckpt.json.tmp").exists()

    def test_load_missing_file_returns_none(self, tmp_path):
        result = SessionCheckpoint.load(tmp_path / "no_file.json")
        assert result is None

    def test_load_restores_all_fields(self, tmp_path):
        c = _ckpt(
            route_file="test.csv",
            waypoint_index=42,
            position_x=32000,
            position_y=31000,
            position_z=8,
            routes_completed=3,
            heal_fired=10,
            mana_fired=5,
            loot_events=20,
            uptime_seconds=1234.5,
        )
        path = tmp_path / "ckpt.json"
        c.save(path)

        loaded = SessionCheckpoint.load(path)
        assert loaded is not None
        assert loaded.route_file == "test.csv"
        assert loaded.waypoint_index == 42
        assert loaded.position_x == 32000
        assert loaded.position_y == 31000
        assert loaded.position_z == 8
        assert loaded.routes_completed == 3
        assert loaded.heal_fired == 10
        assert loaded.mana_fired == 5
        assert loaded.loot_events == 20
        assert loaded.uptime_seconds == pytest.approx(1234.5)

    def test_load_extra_fields_go_to_extra(self, tmp_path):
        path = tmp_path / "ckpt.json"
        data = {"route_file": "r.csv", "unknown_field": "value123"}
        with open(path, "w") as f:
            json.dump(data, f)
        loaded = SessionCheckpoint.load(path)
        assert loaded is not None
        assert loaded.extra.get("unknown_field") == "value123"

    def test_load_corrupt_json_returns_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid}", encoding="utf-8")
        assert SessionCheckpoint.load(path) is None

    def test_save_write_error_does_not_raise(self, tmp_path, monkeypatch):
        """If json.dump fails, save() catches and logs — does not propagate."""
        import json as _json
        c = _ckpt()
        path = tmp_path / "ckpt.json"

        def _bad_dump(*a, **kw):
            raise OSError("no space")

        monkeypatch.setattr(_json, "dump", _bad_dump)
        c.save(path)  # must not raise
        assert not path.exists()  # file not created on failure


# ─────────────────────────────────────────────────────────────────────────────
# clear()
# ─────────────────────────────────────────────────────────────────────────────

class TestClear:

    def test_clear_deletes_file(self, tmp_path):
        path = tmp_path / "ckpt.json"
        path.write_text("{}", encoding="utf-8")
        SessionCheckpoint.clear(path)
        assert not path.exists()

    def test_clear_nonexistent_does_not_raise(self, tmp_path):
        SessionCheckpoint.clear(tmp_path / "no_file.json")


# ─────────────────────────────────────────────────────────────────────────────
# is_stale()
# ─────────────────────────────────────────────────────────────────────────────

class TestIsStale:

    def test_zero_timestamp_is_stale(self):
        assert _ckpt().is_stale() is True

    def test_fresh_checkpoint_not_stale(self, tmp_path):
        c = _ckpt()
        c.save(tmp_path / "ckpt.json")
        assert c.is_stale(max_age_seconds=3600) is False

    def test_old_checkpoint_is_stale(self):
        c = _ckpt()
        c.timestamp = time.time() - 7200  # 2 hours ago
        assert c.is_stale(max_age_seconds=3600) is True


# ─────────────────────────────────────────────────────────────────────────────
# matches_route()
# ─────────────────────────────────────────────────────────────────────────────

class TestMatchesRoute:

    def test_matches_same_route(self):
        assert _ckpt(route_file="r.csv").matches_route("r.csv") is True

    def test_does_not_match_different_route(self):
        assert _ckpt(route_file="r.csv").matches_route("other.csv") is False

    def test_empty_route_matches_empty(self):
        assert _ckpt().matches_route("") is True


# ─────────────────────────────────────────────────────────────────────────────
# load() extra-field merging edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadExtraFieldMerging:

    def test_existing_extra_merged(self, tmp_path):
        path = tmp_path / "ckpt.json"
        data = {
            "route_file": "r.csv",
            "extra": {"existing": 1},
            "unknown_field": "hello",
        }
        with open(path, "w") as f:
            json.dump(data, f)
        loaded = SessionCheckpoint.load(path)
        assert loaded is not None
        assert loaded.extra.get("existing") == 1
        assert loaded.extra.get("unknown_field") == "hello"
