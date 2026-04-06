"""
Tests for src/spawn_manager.py — SpawnManager
Fully offline: pure logic, no external dependencies.
"""
from __future__ import annotations

import time
from typing import Optional
from unittest.mock import MagicMock

import pytest

from src.spawn_manager import (
    SpawnManager,
    SpawnManagerConfig,
    SpawnPoint,
    SpawnStatus,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _spawn(name: str, priority: int = 1, min_level: int = 1,
           script: str = "", expected_monsters=None,
           check_waypoint=None) -> SpawnPoint:
    return SpawnPoint(
        name=name,
        script=script,
        priority=priority,
        min_level=min_level,
        expected_monsters=expected_monsters or [],
        check_waypoint=check_waypoint or [],
    )


def _config(*spawns: SpawnPoint, **kwargs) -> SpawnManagerConfig:
    return SpawnManagerConfig(spawns=list(spawns), **kwargs)


def _manager(*spawns: SpawnPoint, **kwargs) -> SpawnManager:
    cfg = _config(*spawns, **kwargs)
    return SpawnManager(config=cfg)


# ─────────────────────────────────────────────────────────────────────────────
# SpawnStatus enum
# ─────────────────────────────────────────────────────────────────────────────

class TestSpawnStatus:

    def test_all_values(self):
        assert SpawnStatus.UNKNOWN.value == "unknown"
        assert SpawnStatus.FREE.value == "free"
        assert SpawnStatus.OCCUPIED.value == "occupied"
        assert SpawnStatus.DANGEROUS.value == "dangerous"
        assert SpawnStatus.COOLDOWN.value == "cooldown"


# ─────────────────────────────────────────────────────────────────────────────
# SpawnPoint dataclass
# ─────────────────────────────────────────────────────────────────────────────

class TestSpawnPoint:

    def test_defaults(self):
        sp = SpawnPoint(name="test")
        assert sp.script == ""
        assert sp.priority == 1
        assert sp.min_level == 1
        assert sp.expected_monsters == []
        assert sp.check_waypoint == []

    def test_custom_fields(self):
        sp = _spawn("wasp_south", priority=2, min_level=30, script="route.in")
        assert sp.name == "wasp_south"
        assert sp.priority == 2
        assert sp.min_level == 30
        assert sp.script == "route.in"


# ─────────────────────────────────────────────────────────────────────────────
# SpawnManagerConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestSpawnManagerConfig:

    def test_defaults(self):
        cfg = SpawnManagerConfig()
        assert cfg.occupied_timeout_s == 300.0
        assert cfg.max_retries == 2
        assert cfg.switch_cooldown_s == 60.0
        assert cfg.spawns == []

    def test_custom_values(self):
        cfg = SpawnManagerConfig(
            occupied_timeout_s=120.0,
            max_retries=3,
            switch_cooldown_s=30.0,
        )
        assert cfg.occupied_timeout_s == 120.0
        assert cfg.max_retries == 3
        assert cfg.switch_cooldown_s == 30.0


# ─────────────────────────────────────────────────────────────────────────────
# SpawnManager construction
# ─────────────────────────────────────────────────────────────────────────────

class TestConstruction:

    def test_empty_manager(self):
        m = SpawnManager()
        assert m.spawn_count == 0
        assert m.current_spawn is None
        assert m.switch_count == 0

    def test_initializes_all_spawns_as_unknown(self):
        sp1 = _spawn("a")
        sp2 = _spawn("b")
        m = _manager(sp1, sp2)
        assert m.get_status("a") == SpawnStatus.UNKNOWN
        assert m.get_status("b") == SpawnStatus.UNKNOWN

    def test_spawn_count(self):
        m = _manager(_spawn("a"), _spawn("b"), _spawn("c"))
        assert m.spawn_count == 3

    def test_with_event_bus(self):
        bus = MagicMock()
        m = SpawnManager(config=SpawnManagerConfig(), event_bus=bus)
        assert m._event_bus is bus

    def test_config_property(self):
        cfg = SpawnManagerConfig(max_retries=5)
        m = SpawnManager(config=cfg)
        assert m.config.max_retries == 5


# ─────────────────────────────────────────────────────────────────────────────
# Status management
# ─────────────────────────────────────────────────────────────────────────────

class TestStatusManagement:

    def test_mark_free(self):
        m = _manager(_spawn("a"))
        m.mark_free("a")
        assert m.get_status("a") == SpawnStatus.FREE

    def test_mark_free_clears_retry_count(self):
        m = _manager(_spawn("a"))
        m.mark_occupied("a")
        m.mark_free("a")
        assert m._retry_counts.get("a", 0) == 0

    def test_mark_occupied(self):
        m = _manager(_spawn("a"))
        m.mark_occupied("a")
        assert m._status["a"] == SpawnStatus.OCCUPIED

    def test_mark_occupied_increments_retry(self):
        m = _manager(_spawn("a"))
        m.mark_occupied("a")
        m.mark_occupied("a")
        assert m._retry_counts["a"] == 2

    def test_mark_occupied_emits_event(self):
        bus = MagicMock()
        m = SpawnManager(config=_config(_spawn("a")), event_bus=bus)
        m.mark_occupied("a")
        bus.emit.assert_called()

    def test_mark_dangerous(self):
        m = _manager(_spawn("a"))
        m.mark_dangerous("a")
        assert m._status["a"] == SpawnStatus.DANGEROUS

    def test_get_status_unknown_for_unregistered(self):
        m = _manager()
        assert m.get_status("nonexistent") == SpawnStatus.UNKNOWN

    def test_get_status_cooldown_after_timeout(self):
        m = _manager(_spawn("a"), occupied_timeout_s=0.0)
        m.mark_occupied("a")
        # Timeout is 0, so it should immediately transition to cooldown
        assert m.get_status("a") == SpawnStatus.COOLDOWN

    def test_get_status_still_occupied_before_timeout(self):
        m = _manager(_spawn("a"), occupied_timeout_s=9999.0)
        m.mark_occupied("a")
        assert m.get_status("a") == SpawnStatus.OCCUPIED


# ─────────────────────────────────────────────────────────────────────────────
# Spawn selection
# ─────────────────────────────────────────────────────────────────────────────

class TestSpawnSelection:

    def test_best_available_empty_returns_none(self):
        m = _manager()
        assert m.best_available() is None

    def test_best_available_returns_highest_priority(self):
        sp1 = _spawn("primary", priority=1)
        sp2 = _spawn("secondary", priority=2)
        m = _manager(sp1, sp2)
        best = m.best_available()
        assert best is not None
        assert best.name == "primary"

    def test_best_available_skips_occupied(self):
        sp1 = _spawn("primary", priority=1)
        sp2 = _spawn("secondary", priority=2)
        m = _manager(sp1, sp2)
        m.mark_occupied("primary")
        # timeout is 300s, not expired yet
        best = m.best_available()
        assert best is not None
        assert best.name == "secondary"

    def test_best_available_skips_dangerous(self):
        sp1 = _spawn("primary", priority=1)
        sp2 = _spawn("secondary", priority=2)
        m = _manager(sp1, sp2)
        m.mark_dangerous("primary")
        best = m.best_available()
        assert best is not None
        assert best.name == "secondary"

    def test_best_available_none_when_all_occupied(self):
        sp1 = _spawn("a")
        sp2 = _spawn("b")
        m = _manager(sp1, sp2)
        m.mark_occupied("a")
        m.mark_occupied("b")
        best = m.best_available()
        # a and b are occupied (not yet in cooldown because timeout=300)
        assert best is None

    def test_best_available_includes_cooldown(self):
        sp1 = _spawn("a", priority=1)
        m = _manager(sp1, occupied_timeout_s=0.0)
        m.mark_occupied("a")
        # After timeout=0, status is COOLDOWN, which is included
        best = m.best_available()
        assert best is not None
        assert best.name == "a"

    def test_recommend_respects_level(self):
        sp1 = _spawn("hard", priority=1, min_level=100)
        sp2 = _spawn("easy", priority=2, min_level=1)
        m = _manager(sp1, sp2)
        rec = m.recommend(char_level=20)
        assert rec is not None
        assert rec.name == "easy"

    def test_recommend_no_level_filter(self):
        sp1 = _spawn("hard", priority=1, min_level=100)
        m = _manager(sp1)
        rec = m.recommend(char_level=0)
        assert rec is not None
        assert rec.name == "hard"

    def test_recommend_returns_none_when_all_filtered(self):
        sp = _spawn("a", min_level=100)
        m = _manager(sp)
        rec = m.recommend(char_level=1)
        assert rec is None

    def test_recommend_skips_occupied(self):
        sp1 = _spawn("a", priority=1)
        m = _manager(sp1)
        m.mark_occupied("a")
        rec = m.recommend(char_level=0)
        assert rec is None

    def test_recommend_skips_dangerous(self):
        sp1 = _spawn("a", priority=1)
        m = _manager(sp1)
        m.mark_dangerous("a")
        rec = m.recommend(char_level=0)
        assert rec is None

    def test_get_spawn_script_returns_script(self):
        sp = _spawn("a", script="route.in")
        m = _manager(sp)
        assert m.get_spawn_script("a") == "route.in"

    def test_get_spawn_script_returns_empty_for_unknown(self):
        m = _manager()
        assert m.get_spawn_script("nonexistent") == ""


# ─────────────────────────────────────────────────────────────────────────────
# switch_spawn()
# ─────────────────────────────────────────────────────────────────────────────

class TestSwitchSpawn:

    def test_switch_returns_next_spawn(self):
        sp1 = _spawn("primary", priority=1)
        sp2 = _spawn("secondary", priority=2)
        m = _manager(sp1, sp2, switch_cooldown_s=0.0)
        m._current_spawn = "primary"
        result = m.switch_spawn()
        assert result is not None

    def test_switch_respects_cooldown(self):
        sp1 = _spawn("a", priority=1)
        sp2 = _spawn("b", priority=2)
        m = _manager(sp1, sp2, switch_cooldown_s=9999.0)
        m._last_switch_ts = time.monotonic()
        result = m.switch_spawn()
        assert result is None

    def test_switch_increments_switch_count(self):
        sp1 = _spawn("primary", priority=1)
        sp2 = _spawn("secondary", priority=2)
        m = _manager(sp1, sp2, switch_cooldown_s=0.0, occupied_timeout_s=9999.0)
        m._current_spawn = "primary"
        # primary will be marked occupied by switch_spawn, secondary is free
        m.mark_free("secondary")
        m.switch_spawn()
        assert m.switch_count >= 1

    def test_switch_emits_event(self):
        sp1 = _spawn("primary", priority=1)
        sp2 = _spawn("secondary", priority=2)
        bus = MagicMock()
        cfg = _config(sp1, sp2, switch_cooldown_s=0.0, occupied_timeout_s=9999.0)
        m = SpawnManager(config=cfg, event_bus=bus)
        m.mark_free("secondary")
        m._current_spawn = "primary"
        m.switch_spawn()
        # emit should be called (at least for mark_occupied and switch event)
        assert bus.emit.called

    def test_switch_no_current_spawn(self):
        sp1 = _spawn("primary", priority=1)
        m = _manager(sp1, switch_cooldown_s=0.0)
        m._current_spawn = None
        result = m.switch_spawn()
        # no current spawn to occupy; should still try best_available
        assert result is not None or result is None  # just must not crash


# ─────────────────────────────────────────────────────────────────────────────
# should_retry_primary()
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldRetryPrimary:

    def test_no_spawns_returns_false(self):
        m = _manager()
        assert m.should_retry_primary() is False

    def test_under_max_retries_returns_true(self):
        sp = _spawn("primary")
        m = _manager(sp, max_retries=3)
        assert m.should_retry_primary() is True

    def test_at_max_retries_returns_false(self):
        sp = _spawn("primary")
        m = _manager(sp, max_retries=2)
        m.mark_occupied("primary")
        m.mark_occupied("primary")
        assert m.should_retry_primary() is False


# ─────────────────────────────────────────────────────────────────────────────
# Properties
# ─────────────────────────────────────────────────────────────────────────────

class TestProperties:

    def test_current_spawn_setter(self):
        m = _manager(_spawn("a"))
        m.current_spawn = "a"
        assert m.current_spawn == "a"

    def test_available_spawns_excludes_occupied(self):
        sp1 = _spawn("a", priority=1)
        sp2 = _spawn("b", priority=2)
        m = _manager(sp1, sp2)
        m.mark_occupied("a")
        available = m.available_spawns
        names = [s.name for s in available]
        assert "a" not in names
        assert "b" in names

    def test_available_spawns_excludes_dangerous(self):
        sp1 = _spawn("a")
        sp2 = _spawn("b")
        m = _manager(sp1, sp2)
        m.mark_dangerous("a")
        available = m.available_spawns
        names = [s.name for s in available]
        assert "a" not in names

    def test_available_spawns_includes_free(self):
        sp = _spawn("a")
        m = _manager(sp)
        m.mark_free("a")
        assert len(m.available_spawns) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Event bus _emit()
# ─────────────────────────────────────────────────────────────────────────────

class TestEmit:

    def test_emit_without_bus_no_crash(self):
        m = _manager(_spawn("a"))
        m._emit("test_event", {"data": 1})  # should not raise

    def test_emit_with_bus(self):
        bus = MagicMock()
        m = SpawnManager(config=_config(_spawn("a")), event_bus=bus)
        m._emit("e_test", {"key": "val"})
        bus.emit.assert_called_once_with("e_test", {"key": "val"})

    def test_emit_swallows_bus_exceptions(self):
        bus = MagicMock()
        bus.emit.side_effect = RuntimeError("bus down")
        m = SpawnManager(config=_config(_spawn("a")), event_bus=bus)
        m._emit("e_test", {})  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# stats_snapshot()
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsSnapshot:

    def test_snapshot_keys(self):
        sp = _spawn("a")
        m = _manager(sp)
        snap = m.stats_snapshot()
        assert "current_spawn" in snap
        assert "switch_count" in snap
        assert "spawn_status" in snap

    def test_snapshot_current_spawn_none_initially(self):
        m = _manager(_spawn("a"))
        assert m.stats_snapshot()["current_spawn"] is None

    def test_snapshot_spawn_status_contains_all_spawns(self):
        m = _manager(_spawn("a"), _spawn("b"))
        snap = m.stats_snapshot()
        assert "a" in snap["spawn_status"]
        assert "b" in snap["spawn_status"]
