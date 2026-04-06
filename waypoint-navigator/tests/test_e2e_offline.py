"""Offline E2E integration tests for QA-05 .. QA-13 subsystems.

Each test class mirrors a QA scenario but runs WITHOUT a live Tibia client.
Validates: module imports, class construction, config round-trip, public-API
method existence, and basic mock-driven behaviour.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ═══════════════════════════════════════════════════════════════════════
# QA-05  Combat & Battle List
# ═══════════════════════════════════════════════════════════════════════

class TestQA05Combat:
    def test_import_combat_manager(self):
        from src.combat_manager import CombatConfig

    def test_combat_config_defaults(self):
        from src.combat_manager import CombatConfig
        cfg = CombatConfig()
        assert hasattr(cfg, "attack_vk")
        assert hasattr(cfg, "spells")

    def test_combat_config_validate(self):
        from src.combat_manager import CombatConfig
        cfg = CombatConfig()
        cfg.validate()  # should not raise

    def test_combat_config_round_trip(self, tmp_path):
        from src.combat_manager import CombatConfig
        cfg = CombatConfig()
        path = tmp_path / "combat.json"
        cfg.save(path)
        cfg2 = CombatConfig.load(path)
        assert cfg2.attack_vk == cfg.attack_vk

    def test_combat_config_class_selector(self):
        """Combat supports different vocations via class-specific config."""
        from src.combat_manager import CombatConfig
        for name in ("druid", "paladin", "sorcerer"):
            path = _ROOT / f"combat_config_{name}.json"
            if path.exists():
                cfg = CombatConfig.load(path)
                cfg.validate()  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# QA-06  Auto Healer & Buffs
# ═══════════════════════════════════════════════════════════════════════

class TestQA06Healer:
    def test_import_healer(self):
        from src.healer import AutoHealer, HealConfig

    def test_heal_config_defaults(self):
        from src.healer import HealConfig
        cfg = HealConfig()
        assert cfg.hp_threshold_pct > 0
        assert cfg.mp_threshold_pct > 0

    def test_heal_config_validate(self):
        from src.healer import HealConfig
        cfg = HealConfig()
        cfg.validate()  # should not raise

    def test_heal_config_round_trip(self, tmp_path):
        from src.healer import HealConfig
        cfg = HealConfig()
        path = tmp_path / "heal.json"
        cfg.save(path)
        cfg2 = HealConfig.load(path)
        assert cfg2.hp_threshold_pct == cfg.hp_threshold_pct

    def test_healer_construct_with_mock(self):
        from src.healer import AutoHealer, HealConfig
        ctrl = MagicMock()
        cfg = HealConfig()
        healer = AutoHealer(ctrl, config=cfg)
        assert healer is not None

    def test_healer_start_stop(self):
        from src.healer import AutoHealer, HealConfig
        ctrl = MagicMock()
        healer = AutoHealer(ctrl, config=HealConfig())
        healer.set_frame_getter(lambda: np.zeros((1080, 1920, 3), dtype=np.uint8))
        healer.start()
        time.sleep(0.3)
        healer.stop()

    def test_healer_emergency_property(self):
        from src.healer import HealConfig
        cfg = HealConfig()
        assert hasattr(cfg, "hp_emergency_pct")


# ═══════════════════════════════════════════════════════════════════════
# QA-07  Looter
# ═══════════════════════════════════════════════════════════════════════

class TestQA07Looter:
    def test_import_looter(self):
        from src.looter import LootConfig

    def test_loot_config_defaults(self):
        from src.looter import LootConfig
        cfg = LootConfig()
        assert hasattr(cfg, "loot_whitelist")

    def test_loot_config_round_trip(self, tmp_path):
        from src.looter import LootConfig
        cfg = LootConfig()
        path = tmp_path / "loot.json"
        cfg.save(path)
        cfg2 = LootConfig.load(path)
        assert cfg2.tile_size_px == cfg.tile_size_px


# ═══════════════════════════════════════════════════════════════════════
# QA-08  Depot Cycle
# ═══════════════════════════════════════════════════════════════════════

class TestQA08Depot:
    def test_import_depot_modules(self):
        from src.depot_manager import DepotManager, DepotConfig
        from src.depot_orchestrator import DepotOrchestrator

    def test_depot_config_defaults(self):
        from src.depot_manager import DepotConfig
        cfg = DepotConfig()
        assert hasattr(cfg, "depot_chest_coord")

    def test_depot_config_round_trip(self, tmp_path):
        from src.depot_manager import DepotConfig
        cfg = DepotConfig()
        path = tmp_path / "depot.json"
        cfg.save(path)
        cfg2 = DepotConfig.load(path)
        assert cfg2.open_wait == cfg.open_wait

    def test_depot_manager_construct(self):
        from src.depot_manager import DepotManager, DepotConfig
        ctrl = MagicMock()
        dm = DepotManager(ctrl, config=DepotConfig())
        dm.set_frame_getter(lambda: np.zeros((1080, 1920, 3), dtype=np.uint8))
        assert dm is not None


# ═══════════════════════════════════════════════════════════════════════
# QA-09  Trade NPC
# ═══════════════════════════════════════════════════════════════════════

class TestQA09Trade:
    def test_import_trade(self):
        from src.trade_manager import TradeConfig, TradeItem

    def test_trade_config_defaults(self):
        from src.trade_manager import TradeConfig
        cfg = TradeConfig()
        assert hasattr(cfg, "buy_btn_pos")
        assert hasattr(cfg, "sell_btn_pos")

    def test_trade_item_dataclass(self):
        from src.trade_manager import TradeItem
        item = TradeItem(name="Health Potion", quantity=50, max_price=50)
        assert item.name == "Health Potion"
        assert item.quantity == 50

    def test_trade_config_round_trip(self, tmp_path):
        from src.trade_manager import TradeConfig
        cfg = TradeConfig()
        path = tmp_path / "trade.json"
        cfg.save(path)
        cfg2 = TradeConfig.load(path)
        assert cfg2.buy_btn_pos == cfg.buy_btn_pos


# ═══════════════════════════════════════════════════════════════════════
# QA-10  Death & Reconnect
# ═══════════════════════════════════════════════════════════════════════

class TestQA10DeathReconnect:
    def test_import_death_handler(self):
        from src.death_handler import DeathHandler, DeathConfig

    def test_import_reconnect_handler(self):
        from src.reconnect_handler import ReconnectHandler, ReconnectConfig

    def test_death_config_defaults(self):
        from src.death_handler import DeathConfig
        cfg = DeathConfig()
        assert cfg.check_interval > 0

    def test_death_handler_construct(self):
        from src.death_handler import DeathHandler, DeathConfig
        ctrl = MagicMock()
        dh = DeathHandler(ctrl, config=DeathConfig())
        dh.set_frame_getter(lambda: np.zeros((1080, 1920, 3), dtype=np.uint8))
        assert dh is not None

    def test_reconnect_config_defaults(self):
        from src.reconnect_handler import ReconnectConfig
        cfg = ReconnectConfig()
        assert cfg.max_retries > 0

    def test_reconnect_handler_construct(self):
        from src.reconnect_handler import ReconnectHandler, ReconnectConfig
        ctrl = MagicMock()
        rh = ReconnectHandler(ctrl, config=ReconnectConfig())
        rh.set_frame_getter(lambda: np.zeros((1080, 1920, 3), dtype=np.uint8))
        assert rh is not None

    def test_death_handler_event_bus(self):
        from src.death_handler import DeathHandler, DeathConfig
        from src.event_bus import EventBus
        ctrl = MagicMock()
        bus = EventBus()
        dh = DeathHandler(ctrl, config=DeathConfig())
        dh.set_event_bus(bus)
        # Should not raise


# ═══════════════════════════════════════════════════════════════════════
# QA-11  Anti-Kick
# ═══════════════════════════════════════════════════════════════════════

class TestQA11AntiKick:
    def test_import_anti_kick(self):
        from src.anti_kick import AntiKick, AntiKickConfig

    def test_config_defaults(self):
        from src.anti_kick import AntiKickConfig
        cfg = AntiKickConfig()
        assert cfg.idle_threshold > 0
        assert cfg.enabled is True

    def test_start_stop(self):
        from src.anti_kick import AntiKick, AntiKickConfig
        ctrl = MagicMock()
        ak = AntiKick(ctrl, config=AntiKickConfig())
        ak.start()
        time.sleep(0.3)
        assert ak.is_running
        ak.stop()
        assert not ak.is_running

    def test_notify_activity_resets(self):
        from src.anti_kick import AntiKick, AntiKickConfig
        ctrl = MagicMock()
        ak = AntiKick(ctrl, config=AntiKickConfig())
        ak.start()
        ak.notify_activity()
        ak.stop()

    def test_actions_sent_initially_zero(self):
        from src.anti_kick import AntiKick, AntiKickConfig
        ctrl = MagicMock()
        ak = AntiKick(ctrl, config=AntiKickConfig())
        assert ak.actions_sent == 0


# ═══════════════════════════════════════════════════════════════════════
# QA-12  PvP Detection
# ═══════════════════════════════════════════════════════════════════════

class TestQA12PvP:
    def test_import_pvp(self):
        from src.pvp_detector import PvPDetector, PvPConfig, PvPAction

    def test_pvp_actions_enum(self):
        from src.pvp_detector import PvPAction
        assert PvPAction.IGNORE.name == "IGNORE"
        assert PvPAction.LOGOUT.name == "LOGOUT"

    def test_pvp_config_defaults(self):
        from src.pvp_detector import PvPConfig
        cfg = PvPConfig()
        assert hasattr(cfg, "enabled")
        cfg.validate()  # should not raise

    def test_pvp_detector_construct(self):
        from src.pvp_detector import PvPDetector, PvPConfig
        det = PvPDetector(config=PvPConfig(), auto_load=False)
        assert det is not None

    def test_pvp_scan_empty_frame(self):
        from src.pvp_detector import PvPDetector, PvPConfig
        det = PvPDetector(config=PvPConfig(), auto_load=False)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        result = det.scan(frame)
        assert result.player_count == 0


# ═══════════════════════════════════════════════════════════════════════
# QA-13  Condition Monitor
# ═══════════════════════════════════════════════════════════════════════

class TestQA13ConditionMonitor:
    def test_import_condition_monitor(self):
        from src.condition_monitor import ConditionMonitor, ConditionConfig

    def test_condition_config_defaults(self):
        from src.condition_monitor import ConditionConfig
        cfg = ConditionConfig()
        assert hasattr(cfg, "check_interval")

    def test_condition_config_round_trip(self, tmp_path):
        from src.condition_monitor import ConditionConfig
        cfg = ConditionConfig()
        path = tmp_path / "cond.json"
        cfg.save(path)
        cfg2 = ConditionConfig.load(path)
        assert cfg2.check_interval == cfg.check_interval


# ═══════════════════════════════════════════════════════════════════════
# Cross-cutting: EventBus integration
# ═══════════════════════════════════════════════════════════════════════

class TestEventBusIntegration:
    def test_event_bus_import(self):
        from src.event_bus import EventBus

    def test_event_bus_pub_sub(self):
        from src.event_bus import EventBus
        bus = EventBus()
        received: list[dict] = []
        bus.subscribe("e3", lambda data: received.append(data))
        bus.emit("e3", {"count": 1})
        assert len(received) == 1
        assert received[0]["count"] == 1
