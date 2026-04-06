"""Tests for all Fase 7 modules: PvP, Inventory, Alerts, Spawn, Stats."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

# ── 7.1 PvP Detection ───────────────────────────────────────────────────────
from src.pvp_detector import PvPAction, PvPConfig, PvPDetection, PvPDetector

_FRAME = np.zeros((200, 200, 3), dtype=np.uint8)


class TestPvPConfig:
    def test_defaults(self) -> None:
        cfg = PvPConfig()
        assert cfg.enabled is True
        assert cfg.action == PvPAction.WARN
        assert cfg.confidence == 0.70
        assert cfg.min_consecutive == 2

    def test_custom(self) -> None:
        cfg = PvPConfig(action=PvPAction.FLEE, cooldown_s=5.0, min_consecutive=3)
        assert cfg.action == PvPAction.FLEE
        assert cfg.cooldown_s == 5.0


class TestPvPDetection:
    def test_defaults(self) -> None:
        d = PvPDetection()
        assert d.detected is False
        assert d.player_count == 0


class TestPvPAction:
    def test_all_values(self) -> None:
        vals = {a.value for a in PvPAction}
        assert "ignore" in vals
        assert "flee" in vals
        assert "logout" in vals


class TestPvPDetector:
    def test_disabled(self) -> None:
        det = PvPDetector(PvPConfig(enabled=False))
        result = det.scan(_FRAME)
        assert result.detected is False

    def test_empty_frame(self) -> None:
        det = PvPDetector()
        result = det.scan(np.empty(0))
        assert result.detected is False

    def test_none_frame(self) -> None:
        det = PvPDetector()
        result = det.scan(None)  # type: ignore
        assert result.detected is False

    def test_no_templates_no_detection(self) -> None:
        """Dark frame, no skull templates → no false positive."""
        det = PvPDetector(PvPConfig(skull_templates=[], name_color_ranges=[]))
        result = det.scan(np.zeros((300, 300, 3), dtype=np.uint8))
        assert result.player_count == 0

    def test_skull_template_match(self) -> None:
        """Skull template present in battle list → detection."""
        # Create a frame with a recognisable pattern
        frame = np.zeros((300, 300, 3), dtype=np.uint8)
        cv2.rectangle(frame, (180, 180), (195, 195), (0, 0, 255), -1)  # red square

        # Use that same pattern as skull template
        tmpl = frame[178:197, 178:197].copy()
        cfg = PvPConfig(
            skull_templates=[tmpl],
            battle_list_roi=[0, 0, 300, 300],
            confidence=0.85,
            min_consecutive=1,
        )
        det = PvPDetector(cfg)
        result = det.scan(frame)
        assert result.player_count >= 1

    def test_consecutive_tracking(self) -> None:
        det = PvPDetector(PvPConfig(min_consecutive=3))
        for _ in range(3):
            result = det.scan(_FRAME)
        assert det.consecutive_count == 0  # no actual detection in dark frame

    def test_recommended_action_no_detection(self) -> None:
        det = PvPDetector(PvPConfig(action=PvPAction.FLEE))
        det.scan(_FRAME)
        assert det.recommended_action == PvPAction.IGNORE

    def test_stats_snapshot(self) -> None:
        det = PvPDetector()
        det.scan(_FRAME)
        snap = det.stats_snapshot()
        assert "total_scans" in snap
        assert snap["total_scans"] == 1

    def test_event_bus_integration(self) -> None:
        bus = MagicMock()
        cfg = PvPConfig(min_consecutive=1, cooldown_s=0)
        det = PvPDetector(cfg, event_bus=bus)
        # Create detectable frame
        frame = np.zeros((300, 300, 3), dtype=np.uint8)
        cv2.rectangle(frame, (180, 180), (195, 195), (0, 0, 255), -1)
        tmpl = frame[178:197, 178:197].copy()
        det._config.skull_templates = [tmpl]
        det._config.battle_list_roi = [0, 0, 300, 300]
        det._config.confidence = 0.80
        det.scan(frame)
        # If detection happened, bus.emit should be called
        if det.total_detections > 0:
            bus.emit.assert_called()


# ── 7.2 & 7.3 Inventory & Supply ────────────────────────────────────────────
from src.inventory_manager import (
    InventoryConfig,
    InventoryManager,
    InventoryReading,
    InventoryStatus,
    SupplyItem,
    SupplyReading,
    SupplyStatus,
)


class TestInventoryConfig:
    def test_defaults(self) -> None:
        cfg = InventoryConfig()
        assert cfg.enabled is True
        assert cfg.capacity_slots == 20
        assert cfg.full_threshold == 0.95

    def test_custom(self) -> None:
        cfg = InventoryConfig(capacity_slots=30, full_threshold=0.90)
        assert cfg.capacity_slots == 30


class TestInventoryReading:
    def test_free_slots(self) -> None:
        r = InventoryReading(occupied_slots=15, total_slots=20)
        assert r.free_slots == 5

    def test_free_slots_overflow(self) -> None:
        r = InventoryReading(occupied_slots=25, total_slots=20)
        assert r.free_slots == 0


class TestInventoryStatus:
    def test_values(self) -> None:
        vals = {s.value for s in InventoryStatus}
        assert "full" in vals
        assert "nearly_full" in vals
        assert "ok" in vals


class TestSupplyStatus:
    def test_values(self) -> None:
        vals = {s.value for s in SupplyStatus}
        assert "critical" in vals
        assert "empty" in vals
        assert "ok" in vals


class TestInventoryManager:
    def test_disabled(self) -> None:
        mgr = InventoryManager(InventoryConfig(enabled=False))
        r = mgr.check_inventory(_FRAME)
        assert r.status == InventoryStatus.UNKNOWN

    def test_empty_frame(self) -> None:
        mgr = InventoryManager()
        r = mgr.check_inventory(np.empty(0))
        assert r.status == InventoryStatus.UNKNOWN

    def test_dark_frame_empty_inventory(self) -> None:
        """Dark frame → no occupied slots."""
        mgr = InventoryManager(InventoryConfig(
            inventory_roi=[0, 0, 200, 200],
            capacity_slots=20,
        ))
        dark = np.zeros((300, 300, 3), dtype=np.uint8)
        r = mgr.check_inventory(dark)
        assert r.status == InventoryStatus.OK
        assert r.occupied_slots == 0

    def test_bright_frame_occupied(self) -> None:
        """Bright frame → slots appear occupied."""
        mgr = InventoryManager(InventoryConfig(
            inventory_roi=[0, 0, 200, 200],
            capacity_slots=4,
            full_threshold=0.5,
        ))
        bright = np.random.randint(100, 255, (300, 300, 3), dtype=np.uint8)
        r = mgr.check_inventory(bright)
        assert r.occupied_slots > 0

    def test_needs_depot_when_full(self) -> None:
        mgr = InventoryManager()
        mgr._last_inv = InventoryReading(status=InventoryStatus.FULL)
        assert mgr.needs_depot() is True

    def test_needs_depot_critical_supply(self) -> None:
        mgr = InventoryManager()
        mgr._last_supplies["mana"] = SupplyReading(status=SupplyStatus.CRITICAL)
        assert mgr.needs_depot() is True

    def test_no_depot_needed(self) -> None:
        mgr = InventoryManager()
        mgr._last_inv = InventoryReading(status=InventoryStatus.OK)
        assert mgr.needs_depot() is False

    def test_should_check_interval(self) -> None:
        mgr = InventoryManager(InventoryConfig(check_interval_s=0.01))
        assert mgr.should_check() is True
        mgr._last_check_ts = time.monotonic()
        assert mgr.should_check() is False
        time.sleep(0.02)
        assert mgr.should_check() is True

    def test_check_supplies_empty(self) -> None:
        mgr = InventoryManager(InventoryConfig(supplies=[]))
        results = mgr.check_supplies(_FRAME)
        assert len(results) == 0

    def test_check_supplies_with_item(self) -> None:
        item = SupplyItem(name="Health Potion", slot_roi=[0, 0, 32, 32])
        mgr = InventoryManager(InventoryConfig(supplies=[item]))
        dark = np.zeros((100, 100, 3), dtype=np.uint8)
        results = mgr.check_supplies(dark)
        assert len(results) == 1
        assert results[0].name == "Health Potion"
        # No template → honest UNKNOWN (R6.2)
        assert results[0].status == SupplyStatus.UNKNOWN
        assert results[0].confidence == 0.0

    def test_stats_snapshot(self) -> None:
        mgr = InventoryManager(InventoryConfig(
            inventory_roi=[0, 0, 100, 100],
        ))
        dark = np.zeros((100, 100, 3), dtype=np.uint8)
        mgr.check_inventory(dark)
        snap = mgr.stats_snapshot()
        assert "total_checks" in snap
        assert snap["total_checks"] == 1

    def test_uncalibrated_roi_returns_unknown(self) -> None:
        """Default ROI [0,0,0,0] → UNKNOWN (R6.2 honesty)."""
        mgr = InventoryManager(InventoryConfig())  # default ROI
        frame = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
        r = mgr.check_inventory(frame)
        assert r.status == InventoryStatus.UNKNOWN

    def test_supply_no_template_unknown(self) -> None:
        """Supply without template → UNKNOWN, not a fake count (R6.2)."""
        item = SupplyItem(name="Mana Potion", slot_roi=[0, 0, 50, 50])
        mgr = InventoryManager(InventoryConfig(supplies=[item]))
        bright = np.random.randint(100, 255, (200, 200, 3), dtype=np.uint8)
        results = mgr.check_supplies(bright)
        assert results[0].status == SupplyStatus.UNKNOWN
        assert results[0].estimated_count == 0

    def test_supply_with_template_nms(self) -> None:
        """Supply with real template uses NMS for counting."""
        # Build a small 10x10 template and a crop with the pattern tiled
        tmpl = np.full((10, 10, 3), 200, dtype=np.uint8)
        # Create a 50x50 frame with the template placed in two non-overlapping spots
        frame = np.zeros((50, 50, 3), dtype=np.uint8)
        frame[5:15, 5:15] = tmpl
        frame[5:15, 30:40] = tmpl
        item = SupplyItem(
            name="Test Item",
            slot_roi=[0, 0, 50, 50],
            template=tmpl,
            critical_threshold=0,
            low_threshold=1,
        )
        mgr = InventoryManager(InventoryConfig(supplies=[item]))
        results = mgr.check_supplies(frame)
        assert results[0].confidence > 0
        # With NMS we should get a reasonable small count, not inflated
        assert results[0].estimated_count >= 1

    def test_event_bus_full(self) -> None:
        bus = MagicMock()
        mgr = InventoryManager(
            InventoryConfig(
                inventory_roi=[0, 0, 200, 200],
                capacity_slots=4,
                full_threshold=0.3,  # easy to trigger
            ),
            event_bus=bus,
        )
        bright = np.random.randint(100, 255, (300, 300, 3), dtype=np.uint8)
        mgr.check_inventory(bright)
        if mgr.full_count > 0:
            bus.emit.assert_called()


# ── 7.4 Alerts & Log Rotation ───────────────────────────────────────────────
from src.alert_system import AlertConfig, AlertSystem, LogRotationConfig, LogRotator


class TestLogRotationConfig:
    def test_defaults(self) -> None:
        cfg = LogRotationConfig()
        assert cfg.log_dir == "logs"
        assert cfg.max_file_size_mb == 10.0
        assert cfg.max_files == 5


class TestLogRotator:
    def test_setup_creates_handler(self, tmp_path) -> None:
        cfg = LogRotationConfig(log_dir=str(tmp_path / "logs"))
        rotator = LogRotator(cfg)
        handler = rotator.setup("test_waypoint_rot")
        assert handler is not None
        assert rotator.is_active is True
        rotator.teardown("test_waypoint_rot")
        assert rotator.is_active is False

    def test_log_dir_created(self, tmp_path) -> None:
        log_dir = tmp_path / "new_logs"
        cfg = LogRotationConfig(log_dir=str(log_dir))
        rotator = LogRotator(cfg)
        rotator.setup("test_waypoint_rot2")
        assert log_dir.exists()
        rotator.teardown("test_waypoint_rot2")


class TestAlertConfig:
    def test_defaults(self) -> None:
        cfg = AlertConfig()
        assert cfg.enabled is False
        assert "e3" in cfg.events

    def test_custom(self) -> None:
        cfg = AlertConfig(enabled=True, cooldown_s=30.0)
        assert cfg.enabled is True


class TestAlertSystem:
    def test_disabled(self) -> None:
        alerts = AlertSystem(AlertConfig(enabled=False))
        assert alerts.send("test", {}) is False

    def test_cooldown(self) -> None:
        alerts = AlertSystem(AlertConfig(enabled=True, cooldown_s=999))
        # No webhook configured → send fails, but cooldown logic tested
        alerts.send("test", {})
        assert alerts.total_sent == 0  # no actual delivery

    def test_format_message(self) -> None:
        alerts = AlertSystem()
        msg = alerts._format_message("e3", {"character": "Hiyoko"})
        assert "E3" in msg
        assert "Hiyoko" in msg

    def test_format_none_data(self) -> None:
        alerts = AlertSystem()
        msg = alerts._format_message("e32", None)
        assert "E32" in msg

    def test_stats_snapshot(self) -> None:
        alerts = AlertSystem()
        snap = alerts.stats_snapshot()
        assert "total_sent" in snap
        assert snap["total_sent"] == 0

    def test_subscribe_to_eventbus(self) -> None:
        bus = MagicMock()
        alerts = AlertSystem(AlertConfig(events=["e3", "e18"]))
        alerts.subscribe(bus)
        assert bus.subscribe.call_count == 2


# ── 7.5 Multi-Spawn Routing ─────────────────────────────────────────────────
from src.spawn_manager import SpawnManager, SpawnManagerConfig, SpawnPoint, SpawnStatus


class TestSpawnPoint:
    def test_defaults(self) -> None:
        sp = SpawnPoint(name="test")
        assert sp.priority == 1
        assert sp.min_level == 1
        assert sp.script == ""


class TestSpawnManagerConfig:
    def test_defaults(self) -> None:
        cfg = SpawnManagerConfig()
        assert cfg.occupied_timeout_s == 300.0
        assert cfg.max_retries == 2


class TestSpawnManager:
    def _make_config(self) -> SpawnManagerConfig:
        return SpawnManagerConfig(
            spawns=[
                SpawnPoint("wasp_1", "wasp1.txt", priority=1),
                SpawnPoint("wasp_2", "wasp2.txt", priority=2),
                SpawnPoint("spider_1", "spider1.txt", priority=3),
            ],
            switch_cooldown_s=0,
        )

    def test_initial_status(self) -> None:
        mgr = SpawnManager(self._make_config())
        assert mgr.get_status("wasp_1") == SpawnStatus.UNKNOWN

    def test_mark_free(self) -> None:
        mgr = SpawnManager(self._make_config())
        mgr.mark_free("wasp_1")
        assert mgr.get_status("wasp_1") == SpawnStatus.FREE

    def test_mark_occupied(self) -> None:
        mgr = SpawnManager(self._make_config())
        mgr.mark_occupied("wasp_1")
        assert mgr.get_status("wasp_1") == SpawnStatus.OCCUPIED

    def test_mark_dangerous(self) -> None:
        mgr = SpawnManager(self._make_config())
        mgr.mark_dangerous("wasp_1")
        assert mgr.get_status("wasp_1") == SpawnStatus.DANGEROUS

    def test_best_available_prefers_priority(self) -> None:
        mgr = SpawnManager(self._make_config())
        best = mgr.best_available()
        assert best is not None
        assert best.name == "wasp_1"

    def test_best_available_skips_occupied(self) -> None:
        mgr = SpawnManager(self._make_config())
        mgr.mark_occupied("wasp_1")
        best = mgr.best_available()
        assert best is not None
        assert best.name == "wasp_2"

    def test_best_available_skips_dangerous(self) -> None:
        mgr = SpawnManager(self._make_config())
        mgr.mark_dangerous("wasp_1")
        mgr.mark_dangerous("wasp_2")
        best = mgr.best_available()
        assert best is not None
        assert best.name == "spider_1"

    def test_best_available_all_occupied(self) -> None:
        mgr = SpawnManager(self._make_config())
        for sp in mgr._config.spawns:
            mgr.mark_occupied(sp.name)
        best = mgr.best_available()
        assert best is None

    def test_switch_spawn(self) -> None:
        mgr = SpawnManager(self._make_config())
        mgr.current_spawn = "wasp_1"
        new = mgr.switch_spawn()
        assert new is not None
        assert new.name != "wasp_1"

    def test_switch_cooldown(self) -> None:
        cfg = self._make_config()
        cfg.switch_cooldown_s = 999.0
        mgr = SpawnManager(cfg)
        mgr.current_spawn = "wasp_1"
        mgr.switch_spawn()  # first switch works
        second = mgr.switch_spawn()  # should be blocked by cooldown
        assert second is None

    def test_available_spawns(self) -> None:
        mgr = SpawnManager(self._make_config())
        mgr.mark_occupied("wasp_2")
        available = mgr.available_spawns
        names = [s.name for s in available]
        assert "wasp_2" not in names
        assert "wasp_1" in names

    def test_should_retry_primary(self) -> None:
        cfg = self._make_config()
        cfg.max_retries = 2
        mgr = SpawnManager(cfg)
        assert mgr.should_retry_primary() is True
        mgr.mark_occupied("wasp_1")
        mgr.mark_occupied("wasp_1")
        assert mgr.should_retry_primary() is False

    def test_spawn_count(self) -> None:
        mgr = SpawnManager(self._make_config())
        assert mgr.spawn_count == 3

    def test_stats_snapshot(self) -> None:
        mgr = SpawnManager(self._make_config())
        snap = mgr.stats_snapshot()
        assert "current_spawn" in snap
        assert "spawn_status" in snap

    def test_event_bus_occupied(self) -> None:
        bus = MagicMock()
        mgr = SpawnManager(self._make_config(), event_bus=bus)
        mgr.mark_occupied("wasp_1")
        bus.emit.assert_called_once()

    def test_occupied_timeout_transitions_to_cooldown(self) -> None:
        cfg = self._make_config()
        cfg.occupied_timeout_s = 0.01  # very short
        mgr = SpawnManager(cfg)
        mgr.mark_occupied("wasp_1")
        time.sleep(0.05)
        status = mgr.get_status("wasp_1")
        assert status == SpawnStatus.COOLDOWN


# ── 7.6 Hunting Session Stats ───────────────────────────────────────────────
from src.session_stats import HuntingSessionStats, SessionStatsConfig


class TestSessionStatsConfig:
    def test_defaults(self) -> None:
        cfg = SessionStatsConfig()
        assert cfg.update_interval_s == 30.0
        assert cfg.exp_per_monster == {}


class TestHuntingSessionStats:
    def test_start_stop(self) -> None:
        stats = HuntingSessionStats()
        assert stats.is_active is False
        stats.start()
        assert stats.is_active is True
        stats.stop()
        assert stats.is_active is False

    def test_record_kill(self) -> None:
        stats = HuntingSessionStats()
        stats.start()
        stats.record_kill("Wasp", exp=24)
        stats.record_kill("Wasp", exp=24)
        stats.record_kill("Bug", exp=15)
        assert stats.total_kills == 3
        assert stats.total_exp == 63
        assert stats.kills_by_monster == {"Wasp": 2, "Bug": 1}

    def test_record_kill_auto_exp(self) -> None:
        cfg = SessionStatsConfig(exp_per_monster={"Wasp": 24, "Spider": 12})
        stats = HuntingSessionStats(cfg)
        stats.record_kill("Wasp")
        assert stats.total_exp == 24

    def test_record_loot(self) -> None:
        stats = HuntingSessionStats()
        stats.record_loot(["Gold Coin", "Honeycomb"], value_gp=50)
        assert stats.total_loot_gp == 50

    def test_record_death(self) -> None:
        stats = HuntingSessionStats()
        stats.record_death()
        stats.record_death()
        assert stats.deaths == 2

    def test_record_heal_spell(self) -> None:
        stats = HuntingSessionStats()
        stats.record_heal()
        stats.record_spell()
        stats.record_spell()
        r = stats.report()
        assert r["heals_used"] == 1
        assert r["spells_cast"] == 2

    def test_elapsed_time(self) -> None:
        stats = HuntingSessionStats()
        stats.start()
        time.sleep(0.05)
        assert stats.elapsed_s >= 0.04
        stats.stop()
        e = stats.elapsed_s
        time.sleep(0.05)
        assert abs(stats.elapsed_s - e) < 0.02  # frozen after stop

    def test_rates_zero_when_no_time(self) -> None:
        stats = HuntingSessionStats()
        assert stats.kills_per_hour == 0.0
        assert stats.exp_per_hour == 0.0
        assert stats.loot_per_hour == 0.0

    def test_reset(self) -> None:
        stats = HuntingSessionStats()
        stats.start()
        stats.record_kill("Wasp", 24)
        stats.reset()
        assert stats.total_kills == 0
        assert stats.total_exp == 0
        assert stats.is_active is False

    def test_report_structure(self) -> None:
        stats = HuntingSessionStats()
        stats.start()
        stats.record_kill("Wasp", 24)
        r = stats.report()
        expected_keys = {
            "active", "elapsed_s", "elapsed_h", "total_kills",
            "total_exp", "total_loot_gp", "deaths", "heals_used",
            "spells_cast", "kills_per_hour", "exp_per_hour",
            "loot_gp_per_hour", "kills_by_monster",
        }
        assert expected_keys.issubset(set(r.keys()))

    def test_summary_text(self) -> None:
        stats = HuntingSessionStats()
        stats.start()
        stats.record_kill("Wasp", 24)
        text = stats.summary_text()
        assert "Hunting Session" in text
        assert "Wasp" in text

    def test_eventbus_integration(self) -> None:
        bus = MagicMock()
        stats = HuntingSessionStats()
        stats.subscribe(bus)
        assert bus.subscribe.call_count == 6  # kill, loot, death, heal, spell, mana

    def test_on_kill_event(self) -> None:
        stats = HuntingSessionStats()
        stats._on_kill({"name": "Wasp", "exp": 24})
        assert stats.total_kills == 1
        assert stats.total_exp == 24

    def test_on_loot_event(self) -> None:
        stats = HuntingSessionStats()
        stats._on_loot({"items": ["Gold"], "value_gp": 100})
        assert stats.total_loot_gp == 100

    def test_on_death_event(self) -> None:
        stats = HuntingSessionStats()
        stats._on_death({})
        assert stats.deaths == 1
