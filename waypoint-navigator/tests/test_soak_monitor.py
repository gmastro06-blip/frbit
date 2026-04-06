"""
Tests for src/soak_monitor.py — SoakMonitor
Offline: no psutil required, background thread is stopped before assertions.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from src.soak_monitor import SoakMonitor, SoakMonitorConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _monitor(tmp_path: Path, **cfg_kwargs) -> SoakMonitor:
    log_file = str(tmp_path / "soak.jsonl")
    cfg_kwargs.setdefault("sample_interval", 0.05)
    cfg_kwargs.setdefault("log_file", log_file)
    cfg = SoakMonitorConfig(**cfg_kwargs)
    return SoakMonitor(config=cfg)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

class TestConfig:

    def test_defaults(self):
        cfg = SoakMonitorConfig()
        assert cfg.sample_interval == 30.0
        assert cfg.max_log_size_mb == 50.0
        assert cfg.memory_warn_mb == 500.0
        assert cfg.enabled is True
        assert cfg.log_file == ""

    def test_custom_log_file_used(self, tmp_path):
        log = str(tmp_path / "custom.jsonl")
        m = SoakMonitor(SoakMonitorConfig(log_file=log, sample_interval=1.0))
        assert m._log_path == Path(log)

    def test_default_log_path_has_soak_in_name(self):
        m = SoakMonitor()
        assert "soak" in m._log_path.name


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestLifecycle:

    def test_not_running_initially(self, tmp_path):
        m = _monitor(tmp_path)
        assert not m.is_running

    def test_start_sets_running(self, tmp_path):
        m = _monitor(tmp_path)
        m.start()
        try:
            assert m.is_running
        finally:
            m.stop()

    def test_stop_clears_running(self, tmp_path):
        m = _monitor(tmp_path)
        m.start()
        m.stop()
        assert not m.is_running

    def test_double_start_idempotent(self, tmp_path):
        m = _monitor(tmp_path)
        m.start()
        thread1 = m._thread
        m.start()  # second call should do nothing
        assert m._thread is thread1
        m.stop()

    def test_stop_without_start_does_not_raise(self, tmp_path):
        m = _monitor(tmp_path)
        m.stop()  # should not raise

    def test_log_callback_called(self, tmp_path):
        logs: List[str] = []
        m = _monitor(tmp_path)
        m._log_fn = logs.append
        m.start()
        time.sleep(0.05)
        m.stop()
        assert any(logs)

    def test_log_fn_none_uses_logger(self, tmp_path):
        m = _monitor(tmp_path)
        assert m._log_fn is None
        m._log("test message")  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsSnapshot:

    def test_snapshot_keys(self, tmp_path):
        m = _monitor(tmp_path)
        snap = m.stats_snapshot()
        for key in ("running", "samples", "peak_memory_mb", "peak_threads",
                    "peak_cpu_pct", "warnings_count", "latest"):
            assert key in snap

    def test_snapshot_running_false_before_start(self, tmp_path):
        m = _monitor(tmp_path)
        assert m.stats_snapshot()["running"] is False

    def test_samples_increment_after_start(self, tmp_path):
        m = _monitor(tmp_path, sample_interval=0.02)
        m.start()
        time.sleep(0.15)
        m.stop()
        assert m.stats_snapshot()["samples"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# _collect_sample (no psutil path)
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectSample:

    def test_collect_without_psutil(self, tmp_path):
        m = _monitor(tmp_path)
        m._start_time = time.monotonic()
        sample = m._collect_sample(None, False)
        assert "ts" in sample
        assert "elapsed_h" in sample
        assert "threads" in sample

    def test_collect_with_psutil_mock(self, tmp_path):
        m = _monitor(tmp_path)
        m._start_time = time.monotonic()

        proc = MagicMock()
        mem = MagicMock()
        mem.rss = 200 * 1024 * 1024
        mem.vms = 400 * 1024 * 1024
        proc.memory_info.return_value = mem
        proc.cpu_percent.return_value = 15.0
        proc.num_threads.return_value = 8

        mock_ps = MagicMock()
        mock_ps.cpu_percent.return_value = 20.0
        vm = MagicMock()
        vm.percent = 55.0
        mock_ps.virtual_memory.return_value = vm

        with patch.dict("sys.modules", {"psutil": mock_ps}):
            sample = m._collect_sample(proc, True)

        assert sample["rss_mb"] == pytest.approx(200.0, abs=1)
        assert sample["cpu_pct"] == 15.0
        assert sample["threads"] == 8

    def test_collect_psutil_memory_exception_falls_back(self, tmp_path):
        m = _monitor(tmp_path)
        m._start_time = time.monotonic()

        proc = MagicMock()
        proc.memory_info.side_effect = RuntimeError("no access")
        proc.cpu_percent.side_effect = RuntimeError("no access")
        proc.num_threads.side_effect = RuntimeError("no access")

        mock_ps = MagicMock()
        mock_ps.cpu_percent.side_effect = RuntimeError("no access")
        mock_ps.virtual_memory.side_effect = RuntimeError("no access")

        with patch.dict("sys.modules", {"psutil": mock_ps}):
            sample = m._collect_sample(proc, True)

        assert sample.get("rss_mb", 0.0) == 0.0
        assert sample.get("cpu_pct", 0.0) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# _record — peak tracking, warnings, log write
# ─────────────────────────────────────────────────────────────────────────────

class TestRecord:

    def test_record_updates_peak_memory(self, tmp_path):
        m = _monitor(tmp_path)
        m._record({"rss_mb": 300.0, "threads": 5, "cpu_pct": 10.0})
        assert m._peak_memory_mb == pytest.approx(300.0)

    def test_record_updates_peak_threads(self, tmp_path):
        m = _monitor(tmp_path)
        m._record({"rss_mb": 0.0, "threads": 12, "cpu_pct": 0.0})
        assert m._peak_threads == 12

    def test_record_updates_peak_cpu(self, tmp_path):
        m = _monitor(tmp_path)
        m._record({"rss_mb": 0.0, "threads": 1, "cpu_pct": 75.0})
        assert m._peak_cpu_pct == pytest.approx(75.0)

    def test_record_increments_samples(self, tmp_path):
        m = _monitor(tmp_path)
        m._record({"rss_mb": 0.0, "threads": 1})
        assert m._samples == 1

    def test_memory_warn_adds_warning(self, tmp_path):
        m = _monitor(tmp_path, memory_warn_mb=100.0)
        logs: List[str] = []
        m._log_fn = logs.append
        m._record({"rss_mb": 200.0, "threads": 1, "cpu_pct": 0.0})
        assert len(m._warnings) == 1
        assert any("200" in w or "Memory" in w for w in m._warnings)

    def test_memory_warn_cap_at_200(self, tmp_path):
        m = _monitor(tmp_path, memory_warn_mb=0.0)
        m._log_fn = lambda s: None
        for _ in range(250):
            m._record({"rss_mb": 1.0, "threads": 1, "cpu_pct": 0.0})
        assert len(m._warnings) <= 200

    def test_record_writes_jsonl_line(self, tmp_path):
        m = _monitor(tmp_path)
        m._log_path.parent.mkdir(parents=True, exist_ok=True)
        m._record({"rss_mb": 10.0, "threads": 2})
        assert m._log_path.exists()
        with open(m._log_path) as f:
            line = f.readline()
        assert json.loads(line)["rss_mb"] == 10.0


# ─────────────────────────────────────────────────────────────────────────────
# _write_log_line — rotation
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteLogLine:

    def test_write_creates_file(self, tmp_path):
        m = _monitor(tmp_path)
        m._log_path.parent.mkdir(parents=True, exist_ok=True)
        m._write_log_line({"x": 1})
        assert m._log_path.exists()

    def test_rotation_when_size_exceeded(self, tmp_path):
        m = _monitor(tmp_path, max_log_size_mb=0.0)
        m._log_fn = lambda s: None
        m._log_path.parent.mkdir(parents=True, exist_ok=True)
        # Write something to ensure file exists with non-zero size
        m._log_path.write_text("x" * 1024, encoding="utf-8")
        m._write_log_line({"y": 2})
        rotated = m._log_path.with_suffix(".jsonl.old")
        assert rotated.exists()

    def test_rotation_removes_old_backup(self, tmp_path):
        m = _monitor(tmp_path, max_log_size_mb=0.0)
        m._log_fn = lambda s: None
        m._log_path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-create both log and old backup
        m._log_path.write_text("x" * 1024, encoding="utf-8")
        m._log_path.with_suffix(".jsonl.old").write_text("old data", encoding="utf-8")
        m._write_log_line({"z": 3})  # should not raise despite old backup existing

    def test_write_error_does_not_raise(self, tmp_path):
        m = _monitor(tmp_path)
        # Point log to a directory (not a file) to cause write failure
        m._log_path = tmp_path  # tmp_path is a directory → open() fails
        m._write_log_line({"err": 1})  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# Background loop — psutil import error path
# ─────────────────────────────────────────────────────────────────────────────

class TestLoopNoPsutil:

    def test_loop_works_without_psutil(self, tmp_path):
        """The loop must collect and record samples even when psutil is absent."""
        m = _monitor(tmp_path, sample_interval=0.02)
        with patch.dict("sys.modules", {"psutil": None}):
            m.start()
            time.sleep(0.12)
            m.stop()
        assert m.stats_snapshot()["samples"] > 0

    def test_loop_exception_in_collect_does_not_stop_thread(self, tmp_path):
        """If _collect_sample raises, the loop continues (lines 157-158)."""
        m = _monitor(tmp_path, sample_interval=0.02)
        call_count = [0]
        original_collect = m._collect_sample

        def boom(proc, has_psutil):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated collect error")
            return original_collect(proc, has_psutil)

        m._collect_sample = boom
        m.start()
        time.sleep(0.15)
        m.stop()
        # At least one sample recorded after the exception
        assert m.stats_snapshot()["samples"] >= 1
