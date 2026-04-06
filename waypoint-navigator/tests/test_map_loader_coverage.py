"""
test_map_loader_coverage.py
---------------------------
Extra tests for src/map_loader.py missing branches.
100% offline — no HTTP requests, all file I/O patched or done in tmp_path.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.map_loader import TibiaMapLoader
from src.models import BOUNDS, Coordinate, Waypoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_XO = BOUNDS["xMin"]
_YO = BOUNDS["yMin"]


def _loader(tmp_path: Path, log=None) -> TibiaMapLoader:
    return TibiaMapLoader(cache_dir=tmp_path, log_fn=log)


def _rgba_png_array(h=4, w=4) -> np.ndarray:
    """Simple all-gray RGBA array (walkable)."""
    arr = np.full((h, w, 4), 128, dtype=np.uint8)
    arr[:, :, 3] = 255
    return arr


def _markers_bytes(entries=None) -> bytes:
    data = entries or [
        {"name": "Thais Temple", "x": 32369, "y": 32241, "z": 7, "type": "temple"},
        {"name": "Edron Temple", "x": 33191, "y": 31818, "z": 7, "type": "temple"},
    ]
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# get_map_image — covers lines 67-70 (cache miss → _load_png)
# ---------------------------------------------------------------------------

class TestGetMapImage:

    def test_caches_on_second_call(self, tmp_path):
        ldr = _loader(tmp_path)
        fake = _rgba_png_array()
        with patch.object(ldr, "_load_png", return_value=fake) as m:
            r1 = ldr.get_map_image(7)
            r2 = ldr.get_map_image(7)
        m.assert_called_once()  # loaded only once
        assert r1 is r2

    def test_different_floors_loaded_separately(self, tmp_path):
        ldr = _loader(tmp_path)
        fake = _rgba_png_array()
        with patch.object(ldr, "_load_png", return_value=fake):
            ldr.get_map_image(0)
            ldr.get_map_image(1)
        assert "00" in ldr._map_images
        assert "01" in ldr._map_images


# ---------------------------------------------------------------------------
# _load_png — download path (lines 254-260) — network call patched
# ---------------------------------------------------------------------------

class TestLoadPng:

    def test_download_when_not_cached(self, tmp_path):
        ldr = _loader(tmp_path)
        fake_arr = _rgba_png_array()

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.content = b"PNGDATA"

        from PIL import Image
        import io

        # Build a real tiny PNG in memory
        img = Image.fromarray(np.zeros((2, 2, 4), dtype=np.uint8), mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        fake_resp.content = png_bytes

        with patch("src.map_loader.requests.get", return_value=fake_resp) as mock_get:
            result = ldr._load_png("floor-07-map.png")

        mock_get.assert_called_once()
        assert isinstance(result, np.ndarray)
        # File should now be cached on disk
        assert (tmp_path / "floor-07-map.png").exists()

    def test_uses_cache_when_exists(self, tmp_path):
        from PIL import Image
        import io

        img = Image.fromarray(np.zeros((2, 2, 4), dtype=np.uint8), mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        (tmp_path / "floor-07-map.png").write_bytes(buf.getvalue())

        ldr = _loader(tmp_path)
        with patch("src.map_loader.requests.get") as mock_get:
            result = ldr._load_png("floor-07-map.png")

        mock_get.assert_not_called()
        assert isinstance(result, np.ndarray)


# ---------------------------------------------------------------------------
# _load_markers — download + parse paths (lines 293-310)
# ---------------------------------------------------------------------------

class TestLoadMarkers:

    def test_download_markers_when_not_cached(self, tmp_path):
        ldr = _loader(tmp_path)
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.content = _markers_bytes()

        with patch("src.map_loader.requests.get", return_value=fake_resp):
            wps = ldr.get_waypoints()

        assert len(wps) == 2
        assert (tmp_path / "markers.json").exists()

    def test_uses_cached_markers(self, tmp_path):
        (tmp_path / "markers.json").write_bytes(_markers_bytes())
        ldr = _loader(tmp_path)

        with patch("src.map_loader.requests.get") as mock_get:
            wps = ldr.get_waypoints()

        mock_get.assert_not_called()
        assert len(wps) == 2

    def test_malformed_entries_skipped(self, tmp_path):
        data = [
            {"name": "Good", "x": 32369, "y": 32241, "z": 7, "type": "temple"},
            {"bad_key": True},  # malformed
        ]
        (tmp_path / "markers.json").write_bytes(json.dumps(data).encode())
        ldr = _loader(tmp_path)
        wps = ldr.get_waypoints()
        assert len(wps) == 1  # malformed entry skipped

    def test_get_waypoints_caches(self, tmp_path):
        (tmp_path / "markers.json").write_bytes(_markers_bytes())
        ldr = _loader(tmp_path)
        wps1 = ldr.get_waypoints()
        wps2 = ldr.get_waypoints()
        assert wps1 is wps2  # same list object (cached)


# ---------------------------------------------------------------------------
# save_learned_blocks / load_learned_blocks — all branches (lines 342-434)
# ---------------------------------------------------------------------------

class TestLearnedWalkability:

    def test_save_creates_file(self, tmp_path):
        ldr = _loader(tmp_path)
        count = ldr.save_learned_blocks([(100, 200, 7)])
        assert (tmp_path / "learned_walkability.json").exists()
        assert count >= 1

    def test_save_opened_list(self, tmp_path):
        ldr = _loader(tmp_path)
        count = ldr.save_learned_blocks([], opened=[(101, 201, 7)])
        assert count >= 1

    def test_save_merges_with_existing(self, tmp_path):
        ldr = _loader(tmp_path)
        ldr.save_learned_blocks([(100, 200, 7)])
        count2 = ldr.save_learned_blocks([(101, 201, 7)])
        assert count2 >= 2

    def test_save_no_duplicate(self, tmp_path):
        ldr = _loader(tmp_path)
        ldr.save_learned_blocks([(100, 200, 7)])
        ldr.save_learned_blocks([(100, 200, 7)])
        # same entry not duplicated
        path = tmp_path / "learned_walkability.json"
        data = json.loads(path.read_text())
        assert len(data["blocked"]) == 1
        assert data["blocked"][0]["hits"] == 2

    def test_load_empty_when_no_file(self, tmp_path):
        ldr = _loader(tmp_path)
        blocked, opened = ldr.load_learned_blocks()
        assert blocked == []
        assert opened == []

    def test_load_invalid_json(self, tmp_path):
        ldr = _loader(tmp_path)
        (tmp_path / "learned_walkability.json").write_text("NOTJSON")
        blocked, opened = ldr.load_learned_blocks()
        assert blocked == []

    def test_load_valid_entries(self, tmp_path):
        ldr = _loader(tmp_path)
        ldr.save_learned_blocks([(100, 200, 7)], opened=[(105, 205, 7)])
        ldr.save_learned_blocks([(100, 200, 7)])
        blocked, opened = ldr.load_learned_blocks()
        assert (100, 200, 7) in blocked
        assert (105, 205, 7) in opened

    def test_single_block_observation_not_applied(self, tmp_path):
        ldr = _loader(tmp_path)
        ldr.save_learned_blocks([(100, 200, 7)])
        blocked, opened = ldr.load_learned_blocks()
        assert (100, 200, 7) not in blocked
        assert opened == []

    def test_opened_tile_clears_stale_block(self, tmp_path):
        ldr = _loader(tmp_path)
        ldr.save_learned_blocks([(100, 200, 7)])
        ldr.save_learned_blocks([], opened=[(100, 200, 7)])
        data = json.loads((tmp_path / "learned_walkability.json").read_text())
        assert data["blocked"] == []
        assert len(data["opened"]) == 1

    def test_critical_tile_requires_extra_confirmation(self, tmp_path):
        ldr = _loader(tmp_path)
        tile = (100, 200, 7)
        ldr.save_learned_blocks([tile])
        ldr.save_learned_blocks([tile])
        blocked, opened = ldr.load_learned_blocks(critical_tiles=[tile])
        assert tile not in blocked
        assert opened == []
        ldr.save_learned_blocks([tile])
        blocked, opened = ldr.load_learned_blocks(critical_tiles=[tile])
        assert tile in blocked

    def test_load_prunes_expired_blocked(self, tmp_path):
        ldr = _loader(tmp_path)
        # Write an entry with a very old timestamp
        old_ts = "2020-01-01T00:00:00"
        data = {"blocked": [{"xyz": [100, 200, 7], "ts": old_ts}], "opened": []}
        (tmp_path / "learned_walkability.json").write_text(json.dumps(data))
        # TTL of 4 hours — old entry should be pruned
        blocked, opened = ldr.load_learned_blocks(blocked_ttl_hours=4.0)
        assert (100, 200, 7) not in blocked

    def test_load_opened_ttl_zero_never_expires(self, tmp_path):
        ldr = _loader(tmp_path)
        old_ts = "2020-01-01T00:00:00"
        data = {"blocked": [], "opened": [{"xyz": [101, 201, 7], "ts": old_ts}]}
        (tmp_path / "learned_walkability.json").write_text(json.dumps(data))
        blocked, opened = ldr.load_learned_blocks(opened_ttl_hours=0.0)
        assert (101, 201, 7) in opened

    def test_save_with_legacy_data(self, tmp_path):
        """Migrates legacy bare-list entries on save."""
        ldr = _loader(tmp_path)
        # Write legacy format (list-of-lists)
        data = {"blocked": [[100, 200, 7]], "opened": []}
        (tmp_path / "learned_walkability.json").write_text(json.dumps(data))
        count = ldr.save_learned_blocks([(110, 210, 7)])
        assert count >= 2  # legacy + new

    def test_save_corrupt_existing_ignored(self, tmp_path):
        """If existing file is corrupt, save still works."""
        ldr = _loader(tmp_path)
        (tmp_path / "learned_walkability.json").write_text("CORRUPT")
        count = ldr.save_learned_blocks([(100, 200, 7)])
        assert count >= 1


# ---------------------------------------------------------------------------
# _migrate_entries (lines 464-474)
# ---------------------------------------------------------------------------

class TestMigrateEntries:

    def test_dict_entries_passed_through(self):
        e = {"xyz": [1, 2, 3], "ts": "2024-01-01T00:00:00"}
        result = TibiaMapLoader._migrate_entries([e])
        assert result[0] is e

    def test_list_entries_converted(self):
        result = TibiaMapLoader._migrate_entries([[10, 20, 7]])
        assert result[0]["xyz"] == [10, 20, 7]
        assert "ts" in result[0]

    def test_tuple_entries_converted(self):
        result = TibiaMapLoader._migrate_entries([(10, 20, 7)])
        assert result[0]["xyz"] == [10, 20, 7]

    def test_invalid_entries_skipped(self):
        result = TibiaMapLoader._migrate_entries(["junk", None, 42])
        assert result == []


# ---------------------------------------------------------------------------
# _entry_key (lines 477-480)
# ---------------------------------------------------------------------------

class TestEntryKey:

    def test_dict_key(self):
        assert TibiaMapLoader._entry_key({"xyz": [1, 2, 3]}) == (1, 2, 3)

    def test_list_key(self):
        assert TibiaMapLoader._entry_key([4, 5, 6]) == (4, 5, 6)


# ---------------------------------------------------------------------------
# _entry_alive (lines 483-494)
# ---------------------------------------------------------------------------

class TestEntryAlive:

    def _now(self):
        return datetime.datetime.utcnow()

    def test_ttl_zero_always_alive(self):
        e = {"xyz": [1, 2, 3], "ts": "2000-01-01T00:00:00"}
        assert TibiaMapLoader._entry_alive(e, 0.0, self._now()) is True

    def test_fresh_entry_alive(self):
        ts = datetime.datetime.utcnow().isoformat()
        e = {"xyz": [1, 2, 3], "ts": ts}
        assert TibiaMapLoader._entry_alive(e, 4.0, self._now()) is True

    def test_old_entry_dead(self):
        e = {"xyz": [1, 2, 3], "ts": "2020-01-01T00:00:00"}
        assert TibiaMapLoader._entry_alive(e, 4.0, self._now()) is False

    def test_corrupt_timestamp_dead(self):
        e = {"xyz": [1, 2, 3], "ts": "not-a-date"}
        assert TibiaMapLoader._entry_alive(e, 4.0, self._now()) is False

    def test_missing_ts_key_dead(self):
        e = {"xyz": [1, 2, 3]}
        assert TibiaMapLoader._entry_alive(e, 4.0, self._now()) is False


# ---------------------------------------------------------------------------
# _filter_by_ttl (lines 496-508)
# ---------------------------------------------------------------------------

class TestFilterByTtl:

    def test_keeps_fresh_filters_old(self):
        now = datetime.datetime.utcnow()
        fresh_ts = now.isoformat()
        old_ts = "2020-01-01T00:00:00"
        entries = [
            {"xyz": [1, 2, 7], "ts": fresh_ts},
            {"xyz": [2, 3, 7], "ts": old_ts},
        ]
        result = TibiaMapLoader._filter_by_ttl(entries, 4.0, now)
        assert (1, 2, 7) in result
        assert (2, 3, 7) not in result

    def test_ttl_zero_keeps_all(self):
        old_ts = "2020-01-01T00:00:00"
        entries = [{"xyz": [1, 2, 7], "ts": old_ts}]
        result = TibiaMapLoader._filter_by_ttl(entries, 0.0, datetime.datetime.utcnow())
        assert (1, 2, 7) in result

    def test_short_xyz_skipped(self):
        now = datetime.datetime.utcnow()
        entries = [{"xyz": [1, 2], "ts": now.isoformat()}]
        result = TibiaMapLoader._filter_by_ttl(entries, 0.0, now)
        assert result == []


# ---------------------------------------------------------------------------
# apply_learned_walkability (lines 436-459)
# ---------------------------------------------------------------------------

class TestApplyLearnedWalkability:

    def _make_walkable_grid(self, h=10, w=10) -> np.ndarray:
        return np.ones((h, w), dtype=bool)

    def test_blocks_and_opens_tiles(self, tmp_path):
        ldr = _loader(tmp_path)
        px = _XO + 2
        py = _YO + 2
        # Pre-populate walkability for floor 7
        grid = self._make_walkable_grid()
        ldr._walkability["07"] = grid

        # Save a blocked and an opened entry
        ldr.save_learned_blocks([(px, py, 7)], opened=[(_XO + 3, _YO + 3, 7)])
        ldr.save_learned_blocks([(px, py, 7)])
        # Mark opened tile as non-walkable first so apply can flip it
        grid[3, 3] = False

        count = ldr.apply_learned_walkability()
        assert count >= 1

    def test_no_entries_zero_count(self, tmp_path):
        ldr = _loader(tmp_path)
        count = ldr.apply_learned_walkability()
        assert count == 0

    def test_apply_learned_walkability_skips_critical_tile_until_extra_hit(self, tmp_path):
        ldr = _loader(tmp_path)
        tile = (_XO + 2, _YO + 2, 7)
        grid = self._make_walkable_grid()
        ldr._walkability["07"] = grid

        ldr.save_learned_blocks([tile])
        ldr.save_learned_blocks([tile])

        count = ldr.apply_learned_walkability_for_tiles([tile])
        assert count == 0
        assert bool(grid[2, 2]) is True

        ldr.save_learned_blocks([tile])

        count = ldr.apply_learned_walkability_for_tiles([tile])
        assert count == 1
        assert bool(grid[2, 2]) is False


# ---------------------------------------------------------------------------
# Additional public API — count_waypoints, stats_snapshot, properties,
# waypoint_names, list_cached_floors  (lines 169-240)
# ---------------------------------------------------------------------------

class TestPublicAPI:

    def test_loaded_count_property(self, tmp_path):
        ldr = _loader(tmp_path)
        assert ldr.loaded_count == 0
        ldr._walkability["07"] = np.ones((2, 2), dtype=bool)
        assert ldr.loaded_count == 1

    def test_count_waypoints_all(self, tmp_path):
        (tmp_path / "markers.json").write_bytes(_markers_bytes())
        ldr = _loader(tmp_path)
        assert ldr.count_waypoints() == 2

    def test_count_waypoints_by_floor(self, tmp_path):
        data = [
            {"name": "A", "x": 32369, "y": 32241, "z": 7, "type": "temple"},
            {"name": "B", "x": 32369, "y": 32241, "z": 8, "type": "temple"},
        ]
        (tmp_path / "markers.json").write_bytes(json.dumps(data).encode())
        ldr = _loader(tmp_path)
        assert ldr.count_waypoints(floor=7) == 1
        assert ldr.count_waypoints(floor=8) == 1
        assert ldr.count_waypoints(floor=0) == 0

    def test_stats_snapshot(self, tmp_path):
        ldr = _loader(tmp_path)
        snap = ldr.stats_snapshot()
        assert "loaded_count" in snap
        assert "waypoints_loaded" in snap
        assert snap["waypoints_loaded"] is False

    def test_has_waypoints_false_then_true(self, tmp_path):
        (tmp_path / "markers.json").write_bytes(_markers_bytes())
        ldr = _loader(tmp_path)
        assert not ldr.has_waypoints
        ldr.get_waypoints()
        assert ldr.has_waypoints

    def test_has_cached_floors_false(self, tmp_path):
        ldr = _loader(tmp_path)
        assert not ldr.has_cached_floors

    def test_has_map_images_false(self, tmp_path):
        ldr = _loader(tmp_path)
        assert not ldr.has_map_images

    def test_waypoints_loaded_property(self, tmp_path):
        (tmp_path / "markers.json").write_bytes(_markers_bytes())
        ldr = _loader(tmp_path)
        assert not ldr.waypoints_loaded
        ldr.get_waypoints()
        assert ldr.waypoints_loaded

    def test_map_images_count(self, tmp_path):
        ldr = _loader(tmp_path)
        assert ldr.map_images_count == 0
        ldr._map_images["07"] = np.zeros((2, 2, 4), dtype=np.uint8)
        assert ldr.map_images_count == 1

    def test_waypoint_names_all(self, tmp_path):
        (tmp_path / "markers.json").write_bytes(_markers_bytes())
        ldr = _loader(tmp_path)
        names = ldr.waypoint_names()
        assert names == sorted(names)
        assert len(names) == 2

    def test_waypoint_names_by_floor(self, tmp_path):
        data = [
            {"name": "A", "x": 32369, "y": 32241, "z": 7, "type": "temple"},
            {"name": "B", "x": 32369, "y": 32241, "z": 8, "type": "temple"},
        ]
        (tmp_path / "markers.json").write_bytes(json.dumps(data).encode())
        ldr = _loader(tmp_path)
        assert ldr.waypoint_names(floor=7) == ["A"]
        assert ldr.waypoint_names(floor=8) == ["B"]

    def test_list_cached_floors_skips_invalid(self, tmp_path):
        ldr = _loader(tmp_path)
        ldr._walkability["07"] = np.ones((2, 2), dtype=bool)
        ldr._walkability["bad"] = np.ones((2, 2), dtype=bool)
        floors = ldr.list_cached_floors()
        assert 7 in floors
        assert all(isinstance(f, int) for f in floors)

    def test_floor_loaded(self, tmp_path):
        ldr = _loader(tmp_path)
        assert not ldr.floor_loaded(7)
        ldr._walkability["07"] = np.ones((2, 2), dtype=bool)
        assert ldr.floor_loaded(7)

    def test_clear_cache(self, tmp_path):
        (tmp_path / "markers.json").write_bytes(_markers_bytes())
        ldr = _loader(tmp_path)
        ldr.get_waypoints()
        ldr._walkability["07"] = np.ones((2, 2), dtype=bool)
        ldr._map_images["07"] = np.zeros((2, 2, 4), dtype=np.uint8)
        ldr.clear_cache()
        assert ldr.loaded_count == 0
        assert ldr.map_images_count == 0
        assert not ldr.waypoints_loaded

    def test_preload_floor(self, tmp_path):
        ldr = _loader(tmp_path)
        fake = _rgba_png_array()
        with patch.object(ldr, "_load_png", return_value=fake):
            ldr.preload_floor(7)
        assert "07" in ldr._walkability

    def test_log_fn_used(self, tmp_path):
        messages: list[str] = []
        ldr = _loader(tmp_path, log=messages.append)
        fake = _rgba_png_array()
        with patch.object(ldr, "_load_png", return_value=fake):
            ldr.preload_floor(7)
        assert any("preloaded" in m.lower() or "floor" in m.lower() for m in messages)

    def test_find_waypoints_floor_filter(self, tmp_path):
        data = [
            {"name": "Thais Temple", "x": 32369, "y": 32241, "z": 7, "type": "temple"},
            {"name": "Thais Depot", "x": 32361, "y": 32232, "z": 7, "type": "depot"},
            {"name": "Edron Temple", "x": 33191, "y": 31818, "z": 6, "type": "temple"},
        ]
        (tmp_path / "markers.json").write_bytes(json.dumps(data).encode())
        ldr = _loader(tmp_path)
        results = ldr.find_waypoints("temple", floor=7)
        assert all(wp.coord.z == 7 for wp in results)
        assert len(results) == 1

    def test_is_walkable_out_of_bounds(self, tmp_path):
        ldr = _loader(tmp_path)
        grid = np.ones((4, 4), dtype=bool)
        ldr._walkability["07"] = grid
        # px, py way outside the grid
        coord = Coordinate(_XO + 9999, _YO + 9999, 7)
        assert ldr.is_walkable(coord) is False

    def test_get_walkability_region_clamped(self, tmp_path):
        ldr = _loader(tmp_path)
        grid = np.ones((10, 10), dtype=bool)
        ldr._walkability["07"] = grid
        # Request region partially outside — should not raise, returns clamped slice
        region = ldr.get_walkability_region(7, _XO, _YO, 5, 5)
        assert region.shape[0] <= 5
        assert region.shape[1] <= 5


# ---------------------------------------------------------------------------
# Corrupt download validation
# ---------------------------------------------------------------------------

class TestCorruptDownloadValidation:

    def test_corrupt_png_not_cached(self, tmp_path):
        """_load_png must NOT cache a corrupt download."""
        ldr = _loader(tmp_path)
        fake_resp = MagicMock()
        fake_resp.content = b"not-a-png-at-all"
        fake_resp.raise_for_status = MagicMock()

        with patch("src.map_loader.requests.get", return_value=fake_resp):
            with pytest.raises(ValueError, match="corrupt"):
                ldr._load_png("floor-07-path.png")

        assert not (tmp_path / "floor-07-path.png").exists()

    def test_corrupt_json_not_cached(self, tmp_path):
        """_load_markers must NOT cache invalid JSON."""
        ldr = _loader(tmp_path)
        fake_resp = MagicMock()
        fake_resp.content = b"<html>Rate limit</html>"
        fake_resp.raise_for_status = MagicMock()

        with patch("src.map_loader.requests.get", return_value=fake_resp):
            with pytest.raises(ValueError, match="corrupt"):
                ldr._load_markers()

        assert not (tmp_path / "markers.json").exists()

    def test_cached_corrupt_png_deleted_on_load(self, tmp_path):
        """If a cached PNG is corrupt, it should be deleted and re-raised."""
        cache = tmp_path / "bad.png"
        cache.write_bytes(b"CORRUPT")
        ldr = _loader(tmp_path)
        with pytest.raises(Exception):
            ldr._load_png("bad.png")
        assert not cache.exists()
