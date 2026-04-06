"""
Tests for main.py helper: _wait_until and --start-at CLI argument.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as _main


class TestWaitUntil:
    """
    _wait_until accepts optional _now_fn and _sleep_fn for testing —
    no mocking of datetime internals needed.
    """

    def _make_clock(self, offset_secs: float = 65.0):
        """Return (target_str, now_fn) where now_fn returns base on first call
        and 'past target' on all subsequent calls.

        offset_secs must be > 60 so that target_str is a *different* HH:MM
        than the base time; otherwise _wait_until wraps to the next day and
        the injected now_fn (which only knows about the original target) can
        never satisfy the exit condition.
        """
        import datetime as _dt

        base = _dt.datetime(2025, 1, 15, 10, 30, 0)
        target = base + _dt.timedelta(seconds=offset_secs)
        target_str = target.strftime("%H:%M")

        call_n = 0

        def now_fn():
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                return base
            return target + _dt.timedelta(seconds=1)

        return target_str, now_fn

    def test_logs_esperando_on_entry(self):
        target_str, now_fn = self._make_clock()
        logs: list[str] = []
        _main._wait_until(target_str, logs.append,
                          _now_fn=now_fn, _sleep_fn=lambda s: None)
        assert any("Esperando" in m for m in logs)

    def test_logs_hora_alcanzada_on_exit(self):
        target_str, now_fn = self._make_clock()
        logs: list[str] = []
        _main._wait_until(target_str, logs.append,
                          _now_fn=now_fn, _sleep_fn=lambda s: None)
        assert any("Hora" in m for m in logs)

    def test_logs_at_least_two_messages(self):
        target_str, now_fn = self._make_clock()
        logs: list[str] = []
        _main._wait_until(target_str, logs.append,
                          _now_fn=now_fn, _sleep_fn=lambda s: None)
        assert len(logs) >= 2

    def test_sleep_is_called(self):
        """Verifies _sleep_fn is invoked when remaining > 0.
        offset must be >60 s so target_str is a different HH:MM than base.
        now_fn must return base on calls 1 AND 2 (initial + first loop check),
        then return past-target on call 3+ so the loop calls sleep before exiting."""
        import datetime as _dt

        base = _dt.datetime(2025, 1, 15, 10, 30, 0)
        target = base + _dt.timedelta(seconds=90)
        target_str = target.strftime("%H:%M")

        calls: list[float] = []
        call_n = 0

        def now_fn():
            nonlocal call_n
            call_n += 1
            if call_n <= 2:          # initial setup + first loop remaining check
                return base
            return target + _dt.timedelta(seconds=1)  # past target → loop exits

        _main._wait_until(target_str, lambda m: None,
                          _now_fn=now_fn, _sleep_fn=calls.append)
        assert len(calls) >= 1

    def test_invalid_format_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            _main._wait_until("invalid", lambda m: None)


class TestStartAtArgument:
    """Verify --start-at is wired into the CLI argument parser."""

    def test_start_at_in_parser(self):
        import argparse
        # We only need to check that p_run accepts --start-at
        # Run main.py's build_parser via the module
        parser = _main.build_parser()
        ns = parser.parse_args([
            "run",
            "--route", "dummy.json",
            "--start-at", "03:30",
            "--dry-run",
        ])
        assert ns.start_at == "03:30"

    def test_start_at_default_empty(self):
        parser = _main.build_parser()
        ns = parser.parse_args(["run", "--dry-run"])
        assert ns.start_at == ""
