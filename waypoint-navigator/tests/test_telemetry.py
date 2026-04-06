"""
tests/test_telemetry.py — Tests for src/telemetry.py (TelemetrySession).
100 % offline: no OBS, no Tibia process, no file I/O unless using tmp_path.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List

import pytest

from src.telemetry import TelemetrySession


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _session(route: str = "test_route") -> TelemetrySession:
    return TelemetrySession(route_name=route)


# ─────────────────────────────────────────────────────────────────────────────
# Construction / defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetryDefaults:

    def test_default_route_name_empty(self):
        s = TelemetrySession()
        assert s.route_name == ""

    def test_custom_route_name(self):
        s = TelemetrySession(route_name="thais_depot_to_temple")
        assert s.route_name == "thais_depot_to_temple"

    def test_all_counters_zero(self):
        s = _session()
        assert s.steps_walked  == 0
        assert s.steps_failed  == 0
        assert s.stuck_count   == 0
        assert s.recalib_count == 0
        assert s.items_looted  == 0
        assert s.depot_cycles  == 0
        assert s.kills         == 0
        assert s.deaths        == 0

    def test_errors_empty(self):
        s = _session()
        assert s.errors == []

    def test_end_ts_none_initially(self):
        s = _session()
        assert s.end_ts is None

    def test_start_ts_is_float(self):
        s = _session()
        assert isinstance(s.start_ts, float)

    def test_start_ts_recent(self):
        before = time.time()
        s = _session()
        after  = time.time()
        assert before <= s.start_ts <= after

    def test_session_id_is_string(self):
        s = _session()
        assert isinstance(s.session_id, str)

    def test_session_id_format(self):
        s = _session()
        import re
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"
        assert re.match(pattern, s.session_id), f"unexpected: {s.session_id}"


# ─────────────────────────────────────────────────────────────────────────────
# Counter methods
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetryCounters:

    def test_record_step_success(self):
        s = _session()
        s.record_step(success=True)
        assert s.steps_walked == 1
        assert s.steps_failed == 0

    def test_record_step_fail(self):
        s = _session()
        s.record_step(success=False)
        assert s.steps_walked == 0
        assert s.steps_failed == 1

    def test_record_step_default_is_success(self):
        s = _session()
        s.record_step()
        assert s.steps_walked == 1

    def test_record_step_accumulates(self):
        s = _session()
        for _ in range(10):
            s.record_step(success=True)
        for _ in range(3):
            s.record_step(success=False)
        assert s.steps_walked == 10
        assert s.steps_failed == 3

    def test_record_stuck(self):
        s = _session()
        s.record_stuck()
        s.record_stuck()
        assert s.stuck_count == 2

    def test_record_recalib(self):
        s = _session()
        s.record_recalib()
        assert s.recalib_count == 1

    def test_record_loot_single(self):
        s = _session()
        s.record_loot(5)
        assert s.items_looted == 5

    def test_record_loot_accumulates(self):
        s = _session()
        s.record_loot(3)
        s.record_loot(7)
        assert s.items_looted == 10

    def test_record_loot_zero_ignored(self):
        s = _session()
        s.record_loot(0)
        assert s.items_looted == 0

    def test_record_loot_negative_ignored(self):
        s = _session()
        s.record_loot(-1)
        assert s.items_looted == 0

    def test_record_depot_cycle(self):
        s = _session()
        s.record_depot_cycle()
        s.record_depot_cycle()
        assert s.depot_cycles == 2

    def test_record_kill_single(self):
        s = _session()
        s.record_kill()
        assert s.kills == 1

    def test_record_kill_count(self):
        s = _session()
        s.record_kill(3)
        assert s.kills == 3

    def test_record_kill_zero_ignored(self):
        s = _session()
        s.record_kill(0)
        assert s.kills == 0

    def test_record_kill_negative_ignored(self):
        s = _session()
        s.record_kill(-1)
        assert s.kills == 0

    def test_record_death(self):
        s = _session()
        s.record_death()
        assert s.deaths == 1

    def test_record_death_accumulates(self):
        s = _session()
        s.record_death()
        s.record_death()
        assert s.deaths == 2


# ─────────────────────────────────────────────────────────────────────────────
# Error log
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetryErrors:

    def test_record_error_appends_dict(self):
        s = _session()
        s.record_error("OCR timeout")
        assert len(s.errors) == 1
        e = s.errors[0]
        assert "ts" in e
        assert e["msg"] == "OCR timeout"

    def test_record_error_ts_is_float(self):
        s = _session()
        s.record_error("test")
        assert isinstance(s.errors[0]["ts"], float)

    def test_record_error_multiple(self):
        s = _session()
        s.record_error("err1")
        s.record_error("err2")
        assert len(s.errors) == 2
        assert s.errors[0]["msg"] == "err1"
        assert s.errors[1]["msg"] == "err2"

    def test_errors_returns_copy(self):
        s = _session()
        s.record_error("x")
        copy = s.errors
        copy.append({"ts": 0.0, "msg": "injected"})
        assert len(s.errors) == 1  # original unchanged

    def test_record_error_non_string_coerced(self):
        s = _session()
        s.record_error(42)
        assert s.errors[0]["msg"] == "42"


# ─────────────────────────────────────────────────────────────────────────────
# Derived properties
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetryDerived:

    def test_total_steps_zero(self):
        assert _session().total_steps == 0

    def test_total_steps_sum(self):
        s = _session()
        s.record_step(True)
        s.record_step(False)
        assert s.total_steps == 2

    def test_success_rate_no_steps(self):
        assert _session().success_rate == pytest.approx(1.0)

    def test_success_rate_all_success(self):
        s = _session()
        for _ in range(10):
            s.record_step(True)
        assert s.success_rate == pytest.approx(1.0)

    def test_success_rate_mixed(self):
        s = _session()
        for _ in range(7):
            s.record_step(True)
        for _ in range(3):
            s.record_step(False)
        assert s.success_rate == pytest.approx(0.7)

    def test_duration_increases_over_time(self):
        s = _session()
        d1 = s.duration_s
        time.sleep(0.05)
        d2 = s.duration_s
        assert d2 > d1

    def test_duration_fixed_after_finish(self):
        s = _session()
        s.finish()
        d1 = s.duration_s
        time.sleep(0.05)
        d2 = s.duration_s
        assert d1 == pytest.approx(d2, abs=1e-9)

    def test_duration_non_negative(self):
        s = _session()
        assert s.duration_s >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# finish() / snapshot()
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetryFinishSnapshot:

    def test_finish_seals_end_ts(self):
        s = _session()
        assert s.end_ts is None
        s.finish()
        assert s.end_ts is not None

    def test_finish_idempotent(self):
        s = _session()
        snap1 = s.finish()
        time.sleep(0.02)
        snap2 = s.finish()
        assert snap1["end_ts"] == snap2["end_ts"]

    def test_snapshot_contains_all_keys(self):
        s = _session()
        snap = s.snapshot()
        required = {
            "session_id", "start_ts", "end_ts", "duration_s",
            "route_name",
            "steps_walked", "steps_failed",
            "stuck_count", "recalib_count",
            "items_looted", "depot_cycles",
            "kills", "deaths",
            "errors",
        }
        assert required <= set(snap.keys()), f"missing: {required - set(snap.keys())}"

    def test_snapshot_reflects_counters(self):
        s = TelemetrySession(route_name="depot_run")
        for _ in range(5):
            s.record_step(True)
        s.record_step(False)
        s.record_stuck()
        s.record_recalib()
        s.record_loot(12)
        s.record_depot_cycle()
        s.record_kill(3)
        s.record_death()
        s.record_error("crash")
        snap = s.snapshot()
        assert snap["route_name"]    == "depot_run"
        assert snap["steps_walked"]  == 5
        assert snap["steps_failed"]  == 1
        assert snap["stuck_count"]   == 1
        assert snap["recalib_count"] == 1
        assert snap["items_looted"]  == 12
        assert snap["depot_cycles"]  == 1
        assert snap["kills"]         == 3
        assert snap["deaths"]        == 1
        assert len(snap["errors"])   == 1

    def test_snapshot_does_not_seal(self):
        s = _session()
        s.snapshot()
        assert s.end_ts is None

    def test_snapshot_duration_is_float(self):
        s = _session()
        assert isinstance(s.snapshot()["duration_s"], float)

    def test_snapshot_errors_is_list(self):
        s = _session()
        assert isinstance(s.snapshot()["errors"], list)


# ─────────────────────────────────────────────────────────────────────────────
# save() / load() — persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetryPersistence:

    def test_save_creates_file(self, tmp_path: Path):
        s = _session()
        path = tmp_path / "session.json"
        s.save(path)
        assert path.exists()

    def test_save_valid_json(self, tmp_path: Path):
        s = _session()
        path = tmp_path / "session.json"
        s.save(path)
        with open(path) as f:
            data = json.load(f)
        assert "session_id" in data

    def test_save_seals_end_ts(self, tmp_path: Path):
        s = _session()
        assert s.end_ts is None
        s.save(tmp_path / "session.json")
        assert s.end_ts is not None

    def test_save_no_tmp_file_left(self, tmp_path: Path):
        s = _session()
        path = tmp_path / "session.json"
        s.save(path)
        tmp = path.with_suffix(".tmp")
        assert not tmp.exists()

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        s = _session()
        path = tmp_path / "nested" / "deep" / "session.json"
        s.save(path)
        assert path.exists()

    def test_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "session.json"
        orig = TelemetrySession(route_name="thais_temple_to_depot")
        orig.record_step(True)
        orig.record_step(False)
        orig.record_stuck()
        orig.record_loot(10)
        orig.record_kill(2)
        orig.record_error("test err")
        orig.finish()
        orig.save(path)

        loaded = TelemetrySession.load(path)
        assert loaded.route_name    == orig.route_name
        assert loaded.steps_walked  == orig.steps_walked
        assert loaded.steps_failed  == orig.steps_failed
        assert loaded.stuck_count   == orig.stuck_count
        assert loaded.items_looted  == orig.items_looted
        assert loaded.kills         == orig.kills
        assert len(loaded.errors)   == 1
        assert loaded.errors[0]["msg"] == "test err"

    def test_load_start_ts_preserved(self, tmp_path: Path):
        path = tmp_path / "s.json"
        s = _session()
        ts = s.start_ts
        s.save(path)
        loaded = TelemetrySession.load(path)
        assert loaded.start_ts == pytest.approx(ts)

    def test_load_end_ts_is_sealed(self, tmp_path: Path):
        path = tmp_path / "s.json"
        s = _session()
        s.save(path)
        loaded = TelemetrySession.load(path)
        assert loaded.end_ts is not None

    def test_load_missing_fields_use_defaults(self, tmp_path: Path):
        path = tmp_path / "minimal.json"
        path.write_text(json.dumps({"route_name": "x", "start_ts": 1.0, "end_ts": 2.0}))
        s = TelemetrySession.load(path)
        assert s.kills         == 0
        assert s.deaths        == 0
        assert s.depot_cycles  == 0


# ─────────────────────────────────────────────────────────────────────────────
# __repr__
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetryRepr:

    def test_repr_contains_route_name(self):
        s = TelemetrySession(route_name="my_route")
        assert "my_route" in repr(s)

    def test_repr_contains_steps(self):
        s = _session()
        s.record_step()
        s.record_step()
        assert "steps=2" in repr(s)

    def test_repr_is_string(self):
        assert isinstance(repr(_session()), str)


# ─────────────────────────────────────────────────────────────────────────────
# save() error path (lines 202-209)
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveErrorPath:

    def test_save_raises_on_write_failure(self, tmp_path, monkeypatch):
        """When json.dump fails the exception must propagate and tmp file is cleaned up."""
        import json as _json
        s = _session()
        path = tmp_path / "s.json"

        def _bad_dump(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(_json, "dump", _bad_dump)
        with pytest.raises(OSError, match="disk full"):
            s.save(path)

        # tmp file must not be left behind
        assert not path.with_suffix(".tmp").exists()
