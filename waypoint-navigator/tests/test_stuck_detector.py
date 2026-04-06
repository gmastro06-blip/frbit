"""Tests for src.stuck_detector."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.stuck_detector import StuckDetector, StuckConfig, RecoveryAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeCoord:
    """Minimal Coordinate stand-in for tests."""

    def __init__(self, x: int, y: int, z: int = 7):
        self.x = x
        self.y = y
        self.z = z

    def __repr__(self) -> str:
        return f"FakeCoord({self.x},{self.y},{self.z})"


# ---------------------------------------------------------------------------
# StuckConfig tests
# ---------------------------------------------------------------------------

class TestStuckConfig:
    def test_defaults(self):
        cfg = StuckConfig()
        assert cfg.stuck_timeout == 8.0
        assert cfg.poll_interval == 0.5
        assert cfg.nudge_retries == 3
        assert cfg.recovery_cooldown == 2.0
        assert cfg.max_recovery_attempts == 10
        assert cfg.enabled is True

    def test_custom(self):
        cfg = StuckConfig(stuck_timeout=5.0, nudge_retries=2, enabled=False)
        assert cfg.stuck_timeout == 5.0
        assert cfg.nudge_retries == 2
        assert cfg.enabled is False


# ---------------------------------------------------------------------------
# StuckDetector unit tests (no threads)
# ---------------------------------------------------------------------------

class TestStuckDetectorNoThread:
    """Tests that exercise _tick() directly, without starting the thread."""

    def _make(self, **cfg_kwargs) -> StuckDetector:
        defaults: dict[str, object] = {"stuck_timeout": 1.0, "poll_interval": 0.1}
        defaults.update(cfg_kwargs)
        cfg = StuckConfig(**defaults)  # type: ignore[arg-type]
        sd = StuckDetector(config=cfg)
        sd._walking = True  # simulate active walking
        return sd

    def test_no_position_getter_skips(self):
        sd = self._make()
        sd._tick()  # should not raise

    def test_position_change_resets_timer(self):
        pos_seq = iter([FakeCoord(100, 200), FakeCoord(101, 200)])
        sd = self._make()
        sd.set_position_getter(lambda: next(pos_seq))
        sd._last_move_time = time.monotonic()

        sd._tick()  # reads (100,200) — first pos, sets _last_pos
        assert sd._last_pos is not None

        sd._tick()  # reads (101,200) — moved → reset timer
        assert sd._recovery_count == 0
        assert sd._total_stucks == 0

    def test_stuck_declared_after_timeout(self):
        pos = FakeCoord(100, 200)
        sd = self._make(stuck_timeout=0.0)  # immediate timeout
        sd.set_position_getter(lambda: pos)
        bus = MagicMock()
        sd.set_event_bus(bus)

        # First tick sets last_pos
        sd._last_move_time = time.monotonic() - 5  # already past timeout
        sd._last_pos = pos  # already recorded

        sd._tick()
        assert sd._total_stucks == 1
        bus.emit.assert_called()
        first_call = bus.emit.call_args_list[0]
        assert first_call[0][0] == "e29"

    def test_nudge_recovery_first(self):
        pos = FakeCoord(100, 200)
        sd = self._make(stuck_timeout=0.0)
        sd.set_position_getter(lambda: pos)
        nudge = MagicMock()
        sd.set_nudge_fn(nudge)
        bus = MagicMock()
        sd.set_event_bus(bus)

        sd._last_pos = pos
        sd._last_move_time = time.monotonic() - 5
        sd._recovery_count = 1  # n=1 → NUDGE (n=0 is now REPATH)

        sd._tick()
        nudge.assert_called_once()
        assert sd._recovery_count == 2

    def test_nudge_recovery(self):
        pos = FakeCoord(100, 200)
        sd = self._make(stuck_timeout=0.0, nudge_retries=3)
        sd.set_position_getter(lambda: pos)
        nudge = MagicMock()
        sd.set_nudge_fn(nudge)

        sd._last_pos = pos
        sd._last_move_time = time.monotonic() - 5
        sd._recovery_count = 1  # n=1 → NUDGE (n=0 is now REPATH)

        with patch("src.stuck_detector.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic()
            mock_time.sleep = time.sleep
            sd._tick()

        nudge.assert_called_once()

    def test_escape_recovery(self):
        pos = FakeCoord(100, 200)
        sd = self._make(stuck_timeout=0.0, nudge_retries=2)
        sd.set_position_getter(lambda: pos)
        escape = MagicMock()
        sd.set_escape_fn(escape)

        sd._last_pos = pos
        sd._last_move_time = time.monotonic() - 5
        sd._recovery_count = 3  # n=3 → ESCAPE (n=0=REPATH, n=1..2=NUDGE, n=3..4=ESCAPE)

        with patch("src.stuck_detector.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic()
            mock_time.sleep = time.sleep
            sd._tick()

        escape.assert_called_once()

    def test_abort_after_max_attempts(self):
        pos = FakeCoord(100, 200)
        sd = self._make(stuck_timeout=0.0, max_recovery_attempts=2, nudge_retries=0)
        sd.set_position_getter(lambda: pos)
        bus = MagicMock()
        sd.set_event_bus(bus)

        sd._last_pos = pos
        sd._last_move_time = time.monotonic() - 5
        sd._recovery_count = 10  # well past max

        sd._tick()
        assert sd._walking is False  # abort sets walking=False
        # Should have emitted stuck_abort
        events = [c[0][0] for c in bus.emit.call_args_list]
        assert "e31" in events


# ---------------------------------------------------------------------------
# RecoveryAction escalation
# ---------------------------------------------------------------------------

class TestChooseRecovery:
    def _make(self, **kwargs) -> StuckDetector:
        cfg = StuckConfig(**kwargs)
        return StuckDetector(config=cfg)

    def test_first_is_repath(self):
        sd = self._make()
        sd._recovery_count = 0
        assert sd._choose_recovery() == RecoveryAction.REPATH

    def test_nudge_after_repath(self):
        sd = self._make()
        sd._recovery_count = 1
        assert sd._choose_recovery() == RecoveryAction.NUDGE

    def test_nudge_initial_attempts(self):
        sd = self._make(nudge_retries=3)
        sd._recovery_count = 1
        assert sd._choose_recovery() == RecoveryAction.NUDGE
        sd._recovery_count = 2
        assert sd._choose_recovery() == RecoveryAction.NUDGE
        sd._recovery_count = 3
        assert sd._choose_recovery() == RecoveryAction.NUDGE

    def test_escape_after_nudge(self):
        sd = self._make(nudge_retries=3)
        sd._recovery_count = 4
        assert sd._choose_recovery() == RecoveryAction.ESCAPE
        sd._recovery_count = 5
        assert sd._choose_recovery() == RecoveryAction.ESCAPE

    def test_abort_at_max(self):
        sd = self._make(nudge_retries=3, max_recovery_attempts=5)
        sd._recovery_count = 10  # well past max
        assert sd._choose_recovery() == RecoveryAction.ABORT

    def test_max_recovery_attempts_triggers_abort_before_chain_end(self):
        """max_recovery_attempts=3 should ABORT at count 3, even though
        the chain would still be in NUDGE territory (nudge_retries=5)."""
        sd = self._make(nudge_retries=5, max_recovery_attempts=3)
        sd._recovery_count = 3
        assert sd._choose_recovery() == RecoveryAction.ABORT


# ---------------------------------------------------------------------------
# set_walking behavior
# ---------------------------------------------------------------------------

class TestSetWalking:
    def test_set_walking_resets_recovery_count(self):
        sd = StuckDetector()
        sd._recovery_count = 5
        sd.set_walking(True)
        assert sd._recovery_count == 0
        assert sd._walking is True

    def test_set_walking_false(self):
        sd = StuckDetector()
        sd.set_walking(False)
        assert sd._walking is False


# ---------------------------------------------------------------------------
# Thread start/stop
# ---------------------------------------------------------------------------

class TestStuckDetectorThread:
    def test_start_stop(self):
        sd = StuckDetector(config=StuckConfig(poll_interval=0.05))
        sd.start()
        assert sd.is_running is True
        time.sleep(0.1)
        sd.stop()
        assert sd.is_running is False

    def test_pause_resume(self):
        sd = StuckDetector()
        sd.start()
        sd.pause()
        assert sd.is_paused is True
        sd.resume()
        assert sd.is_paused is False
        sd.stop()


# ---------------------------------------------------------------------------
# Stats snapshot
# ---------------------------------------------------------------------------

class TestStatsSnapshot:
    def test_snapshot_fields(self):
        sd = StuckDetector()
        snap = sd.stats_snapshot()
        assert "running" in snap
        assert "walking" in snap
        assert "total_stucks" in snap
        assert "recovery_count" in snap

    def test_reset_counters(self):
        sd = StuckDetector()
        sd._total_stucks = 5
        sd._recovery_count = 3
        sd.reset_counters()
        assert sd.total_stucks == 0
        assert sd.recovery_count == 0


# ---------------------------------------------------------------------------
# T5 — Abort cooldown: auto re-enable after abort
# ---------------------------------------------------------------------------

class TestAbortCooldown:
    def test_abort_cooldown_config_default(self):
        cfg = StuckConfig()
        assert cfg.abort_cooldown == 60.0

    def test_abort_cooldown_config_custom(self):
        cfg = StuckConfig(abort_cooldown=30.0)
        assert cfg.abort_cooldown == 30.0

    def test_abort_sets_abort_time(self):
        sd = StuckDetector()
        sd._walking = True
        sd._do_abort()
        assert sd._walking is False
        assert sd._abort_time > 0

    def test_re_enable_after_cooldown(self):
        """Walking is re-enabled after abort_cooldown elapses."""
        cfg = StuckConfig(abort_cooldown=0.0, poll_interval=0.05)
        sd = StuckDetector(config=cfg)
        sd._walking = True
        sd._running = True

        # Simulate abort
        sd._do_abort()
        assert sd._walking is False
        assert sd._abort_time > 0

        # Simulate the loop re-enable check
        sd._check_abort_cooldown()
        assert sd._walking is True
        assert sd._abort_time == 0.0
        assert sd._recovery_count == 0

    def test_no_re_enable_before_cooldown(self):
        """Walking stays disabled before cooldown expires."""
        cfg = StuckConfig(abort_cooldown=9999.0, poll_interval=0.05)
        sd = StuckDetector(config=cfg)
        sd._walking = True
        sd._running = True
        sd._do_abort()

        # Cooldown hasn't elapsed — walking should stay off
        sd._check_abort_cooldown()
        assert sd._walking is False

    def test_manual_set_walking_clears_abort(self):
        """Calling set_walking(True) manually after abort works normally."""
        sd = StuckDetector()
        sd._walking = True
        sd._do_abort()
        assert sd._walking is False

        sd.set_walking(True)
        assert sd._walking is True
        assert sd._recovery_count == 0
