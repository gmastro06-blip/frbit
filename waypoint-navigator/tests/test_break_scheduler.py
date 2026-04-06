"""Tests for src.break_scheduler module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from src.break_scheduler import BreakScheduler, BreakSchedulerConfig


@pytest.fixture
def fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.break_scheduler.threading.Event.wait", lambda self, timeout=None: False)


# ═══════════════════════════════════════════════════════════════════════════════
# BreakSchedulerConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakSchedulerConfig:
    def test_defaults(self):
        cfg = BreakSchedulerConfig()
        assert cfg.play_min_minutes == 45.0
        assert cfg.play_max_minutes == 120.0
        assert cfg.break_min_minutes == 3.0
        assert cfg.break_max_minutes == 15.0
        assert cfg.long_break_after_hours == 4.0
        assert cfg.max_daily_hours == 16.0
        assert cfg.enabled is True

    def test_custom(self):
        cfg = BreakSchedulerConfig(play_min_minutes=10, max_daily_hours=8)
        assert cfg.play_min_minutes == 10.0
        assert cfg.max_daily_hours == 8.0


# ═══════════════════════════════════════════════════════════════════════════════
# BreakScheduler — init & start
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakSchedulerInit:
    def test_not_started(self):
        bs = BreakScheduler()
        assert bs.on_break is False
        assert bs.breaks_taken == 0
        assert bs.should_break() is False  # not started → never true

    def test_start_sets_session(self):
        bs = BreakScheduler()
        bs.start()
        assert bs._session_start is not None
        assert bs._next_break_at > 0

    def test_start_schedules_within_range(self):
        cfg = BreakSchedulerConfig(play_min_minutes=10, play_max_minutes=20)
        bs = BreakScheduler(config=cfg)
        bs.start()
        delay_s = bs._next_break_at - bs._session_start  # type: ignore[operator]
        assert 10 * 60 <= delay_s <= 20 * 60


# ═══════════════════════════════════════════════════════════════════════════════
# should_break
# ═══════════════════════════════════════════════════════════════════════════════


class TestShouldBreak:
    def test_returns_false_before_start(self):
        bs = BreakScheduler()
        assert bs.should_break() is False

    def test_returns_false_when_disabled(self):
        cfg = BreakSchedulerConfig(enabled=False)
        bs = BreakScheduler(config=cfg)
        bs.start()
        bs._next_break_at = 0  # would trigger if enabled
        assert bs.should_break() is False

    def test_returns_true_when_due(self):
        bs = BreakScheduler()
        bs.start()
        # Force next_break_at to the past
        bs._next_break_at = time.monotonic() - 1
        assert bs.should_break() is True

    def test_returns_false_on_break(self):
        bs = BreakScheduler()
        bs.start()
        bs._next_break_at = time.monotonic() - 1
        bs._on_break = True
        assert bs.should_break() is False

    def test_daily_cap_triggers_break(self):
        cfg = BreakSchedulerConfig(max_daily_hours=0.0001)  # ~0.36 s
        bs = BreakScheduler(config=cfg)
        bs.start()
        # Pretend session started long ago
        bs._session_start = time.monotonic() - 3600
        assert bs.should_break() is True


# ═══════════════════════════════════════════════════════════════════════════════
# execute_break
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecuteBreak:
    def test_normal_break_duration(self, fast_wait: None):
        cfg = BreakSchedulerConfig(
            play_min_minutes=0.01,
            play_max_minutes=0.02,
            break_min_minutes=0.01,
            break_max_minutes=0.02,
        )
        bs = BreakScheduler(config=cfg)
        bs.start()
        bs._next_break_at = time.monotonic() - 1  # force due

        actual = bs.execute_break()
        assert actual >= 0
        assert bs.breaks_taken == 1
        assert bs.on_break is False

    def test_pause_resume_called(self, fast_wait: None):
        cfg = BreakSchedulerConfig(break_min_minutes=0.01, break_max_minutes=0.02)
        bs = BreakScheduler(config=cfg)
        bs.start()

        pause_fn = MagicMock()
        resume_fn = MagicMock()
        bs.execute_break(pause_fn=pause_fn, resume_fn=resume_fn)

        pause_fn.assert_called_once()
        resume_fn.assert_called_once()

    def test_pause_fn_error_does_not_abort(self, fast_wait: None):
        cfg = BreakSchedulerConfig(break_min_minutes=0.01, break_max_minutes=0.02)
        bs = BreakScheduler(config=cfg)
        bs.start()

        pause_fn = MagicMock(side_effect=RuntimeError("oops"))
        resume_fn = MagicMock()
        bs.execute_break(pause_fn=pause_fn, resume_fn=resume_fn)

        # Break still completed despite pause error
        assert bs.breaks_taken == 1
        resume_fn.assert_called_once()

    def test_resume_fn_error_does_not_crash(self, fast_wait: None):
        cfg = BreakSchedulerConfig(break_min_minutes=0.01, break_max_minutes=0.02)
        bs = BreakScheduler(config=cfg)
        bs.start()

        resume_fn = MagicMock(side_effect=RuntimeError("oops"))
        bs.execute_break(resume_fn=resume_fn)
        assert bs.breaks_taken == 1

    def test_long_break_after_threshold(self, fast_wait: None):
        cfg = BreakSchedulerConfig(
            long_break_after_hours=0.0001,  # ~0.36 s
            long_break_min_minutes=0.5,
            long_break_max_minutes=1.0,
            break_min_minutes=0.01,
            break_max_minutes=0.02,
        )
        bs = BreakScheduler(config=cfg)
        bs.start()
        # Simulate 1 hour of play
        bs._session_start = time.monotonic() - 3600

        bs.execute_break()
        # Break log should say LONG BREAK
        assert bs._break_log[-1]["kind"] == "LONG BREAK"

    def test_daily_cap_resets_session(self, fast_wait: None):
        """After daily cap sleep, session counters reset so it doesn't loop."""
        cfg = BreakSchedulerConfig(max_daily_hours=0.0001)
        bs = BreakScheduler(config=cfg)
        bs.start()

        # Force session to look like it exceeded daily cap
        bs._session_start = time.monotonic() - 3600 * 20  # 20h ago

        bs.execute_break()

        # Session start should have been reset to a recent value.
        assert bs._session_start is not None
        assert bs._session_start > time.monotonic() - 1.0
        assert bs._total_break_time == 0.0
        # Now elapsed hours should be near 0, not still >= max_daily_hours
        assert bs._session_elapsed_hours() < 1.0

    def test_break_log_capped(self, fast_wait: None):
        cfg = BreakSchedulerConfig(break_min_minutes=0.001, break_max_minutes=0.002)
        bs = BreakScheduler(config=cfg)
        bs._max_break_log = 5
        bs.start()
        for _ in range(10):
            bs.execute_break()
        assert len(bs._break_log) <= 5


# ═══════════════════════════════════════════════════════════════════════════════
# time_until_break & stats
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimeAndStats:
    def test_time_until_break_before_start(self):
        bs = BreakScheduler()
        assert bs.time_until_break() == 0.0

    def test_time_until_break_positive(self):
        bs = BreakScheduler()
        bs.start()
        t = bs.time_until_break()
        assert t > 0

    def test_time_until_break_zero_when_due(self):
        bs = BreakScheduler()
        bs.start()
        bs._next_break_at = time.monotonic() - 1
        assert bs.time_until_break() == 0.0

    def test_stats_snapshot(self):
        bs = BreakScheduler()
        bs.start()
        snap = bs.stats_snapshot()
        assert "enabled" in snap
        assert "breaks_taken" in snap
        assert "on_break" in snap
        assert "next_break_in_m" in snap
        assert "session_hours" in snap

    def test_stop_does_not_crash(self):
        bs = BreakScheduler()
        bs.start()
        bs.stop()  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# Private helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestPrivateHelpers:
    def test_schedule_next_within_bounds(self):
        cfg = BreakSchedulerConfig(play_min_minutes=5, play_max_minutes=15)
        bs = BreakScheduler(config=cfg)
        now = time.monotonic()
        bs._schedule_next(now)
        delay = bs._next_break_at - now
        assert 5 * 60 <= delay <= 15 * 60

    def test_roll_break_duration_short(self):
        cfg = BreakSchedulerConfig(break_min_minutes=2, break_max_minutes=5)
        bs = BreakScheduler(config=cfg)
        for _ in range(20):
            d = bs._roll_break_duration(is_long=False)
            assert 2 * 60 <= d <= 5 * 60

    def test_roll_break_duration_long(self):
        cfg = BreakSchedulerConfig(long_break_min_minutes=10, long_break_max_minutes=30)
        bs = BreakScheduler(config=cfg)
        for _ in range(20):
            d = bs._roll_break_duration(is_long=True)
            assert 10 * 60 <= d <= 30 * 60

    def test_is_long_break_due(self):
        cfg = BreakSchedulerConfig(long_break_after_hours=2.0)
        bs = BreakScheduler(config=cfg)
        bs._session_start = time.monotonic()
        assert bs._is_long_break_due() is False
        # Simulate 3 hours of play
        bs._session_start = time.monotonic() - 3 * 3600
        assert bs._is_long_break_due() is True

    def test_session_elapsed_hours_before_start(self):
        bs = BreakScheduler()
        assert bs._session_elapsed_hours() == 0.0

    def test_session_elapsed_hours_excludes_breaks(self):
        bs = BreakScheduler()
        bs._session_start = time.monotonic() - 7200  # 2h ago
        bs._total_break_time = 3600  # 1h of breaks
        h = bs._session_elapsed_hours()
        assert 0.9 < h < 1.1  # ~1h of play
