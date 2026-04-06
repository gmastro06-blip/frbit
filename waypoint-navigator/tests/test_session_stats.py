"""
Tests for src/session_stats.py — HuntingSessionStats
Fully offline: no external dependencies.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from src.session_stats import (
    HuntingSessionStats,
    SessionStatsConfig,
    KillRecord,
    LootRecord,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _stats(**kwargs) -> HuntingSessionStats:
    config = SessionStatsConfig(**kwargs)
    return HuntingSessionStats(config=config)


# ─────────────────────────────────────────────────────────────────────────────
# Config defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionStatsConfig:

    def test_default_update_interval(self):
        cfg = SessionStatsConfig()
        assert cfg.update_interval_s == 30.0

    def test_default_exp_per_monster_empty(self):
        cfg = SessionStatsConfig()
        assert cfg.exp_per_monster == {}

    def test_custom_values(self):
        cfg = SessionStatsConfig(
            exp_per_monster={"troll": 50, "cave spider": 60},
            update_interval_s=10.0,
        )
        assert cfg.exp_per_monster["troll"] == 50
        assert cfg.update_interval_s == 10.0


# ─────────────────────────────────────────────────────────────────────────────
# KillRecord / LootRecord dataclasses
# ─────────────────────────────────────────────────────────────────────────────

class TestKillRecord:

    def test_fields(self):
        kr = KillRecord(name="troll", timestamp=1000.0, exp_gained=50)
        assert kr.name == "troll"
        assert kr.timestamp == 1000.0
        assert kr.exp_gained == 50

    def test_default_exp_zero(self):
        kr = KillRecord(name="x", timestamp=0.0)
        assert kr.exp_gained == 0


class TestLootRecord:

    def test_fields(self):
        lr = LootRecord(items=["gold", "sword"], timestamp=2000.0, value_gp=100)
        assert lr.items == ["gold", "sword"]
        assert lr.value_gp == 100

    def test_defaults(self):
        lr = LootRecord()
        assert lr.items == []
        assert lr.timestamp == 0.0
        assert lr.value_gp == 0


# ─────────────────────────────────────────────────────────────────────────────
# Construction
# ─────────────────────────────────────────────────────────────────────────────

class TestConstruction:

    def test_initial_active_false(self):
        s = HuntingSessionStats()
        assert s.is_active is False

    def test_initial_counters_zero(self):
        s = HuntingSessionStats()
        assert s.total_kills == 0
        assert s.total_exp == 0
        assert s.total_loot_gp == 0
        assert s.deaths == 0

    def test_with_none_config_uses_defaults(self):
        s = HuntingSessionStats(config=None)
        assert s._config is not None


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle: start / stop / reset
# ─────────────────────────────────────────────────────────────────────────────

class TestLifecycle:

    def test_start_sets_active(self):
        s = HuntingSessionStats()
        s.start()
        assert s.is_active is True

    def test_start_idempotent(self):
        s = HuntingSessionStats()
        s.start()
        start_ts = s._start_ts
        s.start()  # second call should not change start_ts
        assert s._start_ts == start_ts

    def test_stop_clears_active(self):
        s = HuntingSessionStats()
        s.start()
        s.stop()
        assert s.is_active is False

    def test_stop_without_start_no_crash(self):
        s = HuntingSessionStats()
        s.stop()  # should not raise

    def test_reset_clears_all(self):
        s = HuntingSessionStats()
        s.start()
        s.record_kill("troll", 50)
        s.record_loot(["gold"], 100)
        s.record_death()
        s.record_heal()
        s.record_spell()
        s.record_mana()
        s.reset()
        assert s.total_kills == 0
        assert s.total_exp == 0
        assert s.total_loot_gp == 0
        assert s.deaths == 0
        assert s._heals_used == 0
        assert s._spells_cast == 0
        assert s._mana_used == 0
        assert s.is_active is False
        assert s._start_ts == 0.0
        assert s._end_ts == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Event recording
# ─────────────────────────────────────────────────────────────────────────────

class TestEventRecording:

    def test_record_kill_increments_total(self):
        s = HuntingSessionStats()
        s.record_kill("troll", 50)
        assert s.total_kills == 1
        assert s.total_exp == 50

    def test_record_kill_accumulates(self):
        s = HuntingSessionStats()
        s.record_kill("troll", 50)
        s.record_kill("troll", 50)
        s.record_kill("spider", 25)
        assert s.total_kills == 3
        assert s.total_exp == 125

    def test_record_kill_exp_lookup_from_config(self):
        cfg = SessionStatsConfig(exp_per_monster={"troll": 150})
        s = HuntingSessionStats(config=cfg)
        s.record_kill("troll", 0)  # exp=0 → lookup from config
        assert s.total_exp == 150

    def test_record_kill_empty_name_no_config_lookup(self):
        cfg = SessionStatsConfig(exp_per_monster={"": 999})
        s = HuntingSessionStats(config=cfg)
        s.record_kill("", 10)
        assert s.total_exp == 10

    def test_record_kill_tracks_by_monster(self):
        s = HuntingSessionStats()
        s.record_kill("troll", 0)
        s.record_kill("troll", 0)
        s.record_kill("spider", 0)
        km = s.kills_by_monster
        assert km["troll"] == 2
        assert km["spider"] == 1

    def test_record_kill_empty_name_not_tracked_by_monster(self):
        s = HuntingSessionStats()
        s.record_kill("", 10)
        assert "" not in s.kills_by_monster

    def test_record_loot_with_items(self):
        s = HuntingSessionStats()
        s.record_loot(["gold coin", "mace"], value_gp=75)
        assert s.total_loot_gp == 75
        assert len(s._loots) == 1

    def test_record_loot_accumulates(self):
        s = HuntingSessionStats()
        s.record_loot(value_gp=100)
        s.record_loot(value_gp=200)
        assert s.total_loot_gp == 300

    def test_record_loot_without_items(self):
        s = HuntingSessionStats()
        s.record_loot()
        assert len(s._loots) == 1

    def test_record_death(self):
        s = HuntingSessionStats()
        s.record_death()
        s.record_death()
        assert s.deaths == 2

    def test_record_heal(self):
        s = HuntingSessionStats()
        s.record_heal()
        s.record_heal()
        assert s._heals_used == 2

    def test_record_spell(self):
        s = HuntingSessionStats()
        s.record_spell()
        assert s._spells_cast == 1

    def test_record_mana(self):
        s = HuntingSessionStats()
        s.record_mana()
        assert s._mana_used == 1


# ─────────────────────────────────────────────────────────────────────────────
# Elapsed time properties
# ─────────────────────────────────────────────────────────────────────────────

class TestElapsedTime:

    def test_elapsed_s_zero_before_start(self):
        s = HuntingSessionStats()
        assert s.elapsed_s == 0.0

    def test_elapsed_h_zero_before_start(self):
        s = HuntingSessionStats()
        assert s.elapsed_h == 0.0

    def test_elapsed_s_increases_after_start(self):
        s = HuntingSessionStats()
        s.start()
        e1 = s.elapsed_s
        time.sleep(0.02)
        e2 = s.elapsed_s
        assert e2 > e1
        s.stop()

    def test_elapsed_s_frozen_after_stop(self):
        s = HuntingSessionStats()
        s.start()
        s.stop()
        e1 = s.elapsed_s
        time.sleep(0.02)
        e2 = s.elapsed_s
        assert e1 == pytest.approx(e2, abs=0.01)

    def test_elapsed_h_is_elapsed_s_divided_by_3600(self):
        s = HuntingSessionStats()
        s.start()
        assert s.elapsed_h == pytest.approx(s.elapsed_s / 3600.0, rel=1e-3)
        s.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Computed rate properties
# ─────────────────────────────────────────────────────────────────────────────

class TestRateProperties:

    def test_kills_per_hour_zero_when_no_time(self):
        s = HuntingSessionStats()
        s.record_kill("troll", 0)
        assert s.kills_per_hour == 0.0

    def test_exp_per_hour_zero_when_no_time(self):
        s = HuntingSessionStats()
        s.record_kill("x", 100)
        assert s.exp_per_hour == 0.0

    def test_loot_per_hour_zero_when_no_time(self):
        s = HuntingSessionStats()
        s.record_loot(value_gp=100)
        assert s.loot_per_hour == 0.0

    def test_kills_per_hour_positive_after_start(self):
        s = HuntingSessionStats()
        s.start()
        # Mock elapsed by manipulating start_ts
        s._start_ts = time.monotonic() - 3600  # 1 hour ago
        for _ in range(10):
            s.record_kill("troll", 0)
        assert s.kills_per_hour > 0
        s.stop()

    def test_exp_per_hour_positive_after_start(self):
        s = HuntingSessionStats()
        s.start()
        s._start_ts = time.monotonic() - 3600
        s.record_kill("x", 1000)
        assert s.exp_per_hour > 0
        s.stop()

    def test_loot_per_hour_positive_after_start(self):
        s = HuntingSessionStats()
        s.start()
        s._start_ts = time.monotonic() - 3600
        s.record_loot(value_gp=5000)
        assert s.loot_per_hour > 0
        s.stop()


# ─────────────────────────────────────────────────────────────────────────────
# kills_by_monster property
# ─────────────────────────────────────────────────────────────────────────────

class TestKillsByMonster:

    def test_empty_initially(self):
        s = HuntingSessionStats()
        assert s.kills_by_monster == {}

    def test_returns_copy(self):
        s = HuntingSessionStats()
        s.record_kill("troll", 0)
        km = s.kills_by_monster
        km["injected"] = 99
        assert "injected" not in s.kills_by_monster


# ─────────────────────────────────────────────────────────────────────────────
# EventBus integration
# ─────────────────────────────────────────────────────────────────────────────

class TestEventBusIntegration:

    def _make_bus(self):
        from src.event_bus import EventBus
        return EventBus()

    def test_subscribe_with_none_bus_no_crash(self):
        s = HuntingSessionStats()
        s.subscribe(None)  # should not raise

    def test_subscribe_registers_handlers(self):
        from src.event_bus import EventBus
        bus = EventBus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        assert bus.subscriber_count("e1") >= 1

    def test_kill_event_increments_kill(self):
        bus = self._make_bus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        bus.emit("e1", {"name": "troll", "exp": 50})
        assert s.total_kills == 1
        assert s.total_exp == 50

    def test_kill_event_non_dict_data(self):
        bus = self._make_bus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        bus.emit("e1", "troll")  # non-dict
        assert s.total_kills == 1

    def test_loot_event_increments_loot(self):
        bus = self._make_bus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        bus.emit("e2", {"items": ["gold"], "value_gp": 100})
        assert s.total_loot_gp == 100

    def test_loot_event_non_dict(self):
        bus = self._make_bus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        bus.emit("e2", "loot")  # non-dict
        assert len(s._loots) == 1

    def test_death_event_increments_death(self):
        bus = self._make_bus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        bus.emit("e3", None)
        assert s.deaths == 1

    def test_heal_event(self):
        bus = self._make_bus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        bus.emit("e4", None)
        assert s._heals_used == 1

    def test_spell_event(self):
        bus = self._make_bus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        bus.emit("e5", None)
        assert s._spells_cast == 1

    def test_mana_event(self):
        bus = self._make_bus()
        s = HuntingSessionStats()
        s.subscribe(bus)
        bus.emit("e6", None)
        assert s._mana_used == 1


# ─────────────────────────────────────────────────────────────────────────────
# report()
# ─────────────────────────────────────────────────────────────────────────────

class TestReport:

    def test_report_keys(self):
        s = HuntingSessionStats()
        r = s.report()
        expected = {
            "active", "elapsed_s", "elapsed_h", "total_kills", "total_exp",
            "total_loot_gp", "deaths", "heals_used", "spells_cast",
            "kills_per_hour", "exp_per_hour", "loot_gp_per_hour", "kills_by_monster",
        }
        assert expected <= set(r.keys())

    def test_report_active_false(self):
        s = HuntingSessionStats()
        assert s.report()["active"] is False

    def test_report_after_activity(self):
        s = HuntingSessionStats()
        s.start()
        s.record_kill("troll", 50)
        s.record_loot(value_gp=100)
        s.record_death()
        s.stop()
        r = s.report()
        assert r["total_kills"] == 1
        assert r["total_exp"] == 50
        assert r["total_loot_gp"] == 100
        assert r["deaths"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# summary_text()
# ─────────────────────────────────────────────────────────────────────────────

class TestSummaryText:

    def test_summary_text_contains_session_header(self):
        s = HuntingSessionStats()
        text = s.summary_text()
        assert "Hunting Session" in text

    def test_summary_text_contains_kills(self):
        s = HuntingSessionStats()
        s.record_kill("troll", 0)
        text = s.summary_text()
        assert "1" in text

    def test_summary_text_with_monster_breakdown(self):
        s = HuntingSessionStats()
        s.record_kill("troll", 50)
        s.record_kill("troll", 50)
        s.record_kill("spider", 25)
        text = s.summary_text()
        assert "troll" in text
        assert "spider" in text

    def test_summary_text_without_monster_breakdown_no_monster_section(self):
        s = HuntingSessionStats()
        text = s.summary_text()
        assert "Kills by monster" not in text
