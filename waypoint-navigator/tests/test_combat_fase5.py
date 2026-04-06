"""Tests for Fase 5 combat hardening features.

Covers:
- 5.1 Monster priority config & sorting
- 5.2 Multi-target AoE spell selection
- 5.3 Enhanced flee logic (mob count)
- 5.5 Kill confirmation (per-monster tracking)
- 5.6 Anti-lure detection
- EventBus integration
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.combat_manager import CombatConfig, CombatManager, BattleDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctrl() -> MagicMock:
    ctrl = MagicMock()
    ctrl.click.return_value = True
    ctrl.press_key.return_value = True
    ctrl.is_connected.return_value = True
    return ctrl


def _make_cm(
    ctrl: MagicMock | None = None,
    config: CombatConfig | None = None,
    event_bus: MagicMock | None = None,
    **kw,
) -> CombatManager:
    ctrl = ctrl or _make_ctrl()
    config = config or CombatConfig(
        battle_list_roi=[0, 0, 100, 100],
        templates_dir="__nonexistent__",
    )
    return CombatManager(ctrl, config=config, event_bus=event_bus, **kw)


# ===========================================================================
# 5.1 — Monster priority config
# ===========================================================================


class TestMonsterPriorityConfig:
    """Config fields for monster priority."""

    def test_default_empty_list(self):
        cfg = CombatConfig(battle_list_roi=[0, 0, 100, 100])
        assert cfg.monster_priority == []

    def test_load_with_priority(self, tmp_path):
        p = tmp_path / "cc.json"
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            monster_priority=["Wasp", "Bug", "Spider"],
        )
        cfg.save(p)
        loaded = CombatConfig.load(p)
        assert loaded.monster_priority == ["Wasp", "Bug", "Spider"]

    def test_validate_accepts_priority(self):
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            monster_priority=["Wasp"],
        )
        cfg.validate()  # Should not raise


class TestSortByPriority:
    """CombatManager._sort_by_priority behaviour."""

    def test_no_priority_returns_unchanged(self):
        cm = _make_cm()
        dets = [(10, 50, 0.9, "Bug"), (20, 30, 0.8, "Wasp")]
        assert cm._sort_by_priority(dets) == dets

    def test_priority_reorders(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            monster_priority=["Wasp", "Bug"],
        ))
        # Bug is listed first by Y (higher on screen) but Wasp has higher priority
        dets = [(10, 30, 0.9, "Bug"), (20, 50, 0.8, "Wasp")]
        result = cm._sort_by_priority(dets)
        assert result[0][3] == "Wasp"
        assert result[1][3] == "Bug"

    def test_unknown_monster_gets_lowest(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            monster_priority=["Wasp"],
        ))
        dets = [(10, 30, 0.9, "Cyclops"), (20, 50, 0.8, "Wasp")]
        result = cm._sort_by_priority(dets)
        assert result[0][3] == "Wasp"
        assert result[1][3] == "Cyclops"

    def test_secondary_sort_by_y(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            monster_priority=["Wasp"],
        ))
        dets = [
            (10, 60, 0.9, "Wasp"),
            (20, 30, 0.8, "Wasp"),
        ]
        result = cm._sort_by_priority(dets)
        # Same priority → sorted by Y (lower Y first)
        assert result[0][1] == 30
        assert result[1][1] == 60

    def test_case_insensitive_matching(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            monster_priority=["wasp"],
        ))
        dets = [(10, 30, 0.9, "Bug"), (20, 50, 0.8, "WASP")]
        result = cm._sort_by_priority(dets)
        assert result[0][3] == "WASP"

    def test_substring_matching(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            monster_priority=["Poison Spider"],
        ))
        dets = [(10, 30, 0.9, "Bug"), (20, 50, 0.8, "Poison Spider")]
        result = cm._sort_by_priority(dets)
        assert result[0][3] == "Poison Spider"


# ===========================================================================
# 5.2 — Multi-target AoE spell selection
# ===========================================================================


class TestAoeMobThresholdConfig:
    """Config for AoE mob threshold."""

    def test_default_is_2(self):
        cfg = CombatConfig(battle_list_roi=[0, 0, 100, 100])
        assert cfg.aoe_mob_threshold == 2

    def test_validate_rejects_zero(self):
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            aoe_mob_threshold=0,
        )
        with pytest.raises(ValueError, match="aoe_mob_threshold"):
            cfg.validate()

    def test_save_load_preserves(self, tmp_path):
        p = tmp_path / "cc.json"
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            aoe_mob_threshold=3,
        )
        cfg.save(p)
        loaded = CombatConfig.load(p)
        assert loaded.aoe_mob_threshold == 3


class TestCastSpellsAoeAware:
    """_cast_spells selects spell type based on mob count."""

    def test_single_mob_skips_aoe(self):
        ctrl = _make_ctrl()
        cm = _make_cm(ctrl=ctrl, config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            aoe_mob_threshold=2,
            spells=[
                {"vk": 0x71, "min_mp": 0, "cooldown": 0, "label": "exori", "type": "aoe"},
                {"vk": 0x72, "min_mp": 0, "cooldown": 0, "label": "exori ico", "type": "single_target"},
            ],
        ))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        cm._cast_spells(frame, mob_count=1)

        # AoE (0x71) should NOT be cast, single (0x72) should be cast
        vks = [call.args[0] for call in ctrl.press_key.call_args_list]
        assert 0x72 in vks
        assert 0x71 not in vks

    def test_multiple_mobs_use_aoe(self):
        ctrl = _make_ctrl()
        cm = _make_cm(ctrl=ctrl, config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            aoe_mob_threshold=2,
            spells=[
                {"vk": 0x71, "min_mp": 0, "cooldown": 0, "label": "exori", "type": "aoe"},
                {"vk": 0x72, "min_mp": 0, "cooldown": 0, "label": "exori ico", "type": "single_target"},
            ],
        ))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        cm._cast_spells(frame, mob_count=3)

        # AoE (0x71) should be cast, single (0x72) should NOT
        vks = [call.args[0] for call in ctrl.press_key.call_args_list]
        assert 0x71 in vks
        assert 0x72 not in vks

    def test_taunt_used_with_multiple_mobs(self):
        ctrl = _make_ctrl()
        cm = _make_cm(ctrl=ctrl, config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            aoe_mob_threshold=2,
            spells=[
                {"vk": 0x73, "min_mp": 0, "cooldown": 0, "label": "exeta res", "type": "taunt"},
            ],
        ))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        cm._cast_spells(frame, mob_count=3)

        vks = [call.args[0] for call in ctrl.press_key.call_args_list]
        assert 0x73 in vks

    def test_untyped_spells_always_cast(self):
        """Spells without type field are always considered."""
        ctrl = _make_ctrl()
        cm = _make_cm(ctrl=ctrl, config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            spells=[
                {"vk": 0x71, "min_mp": 0, "cooldown": 0, "label": "exura"},
            ],
        ))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        cm._cast_spells(frame, mob_count=1)
        assert ctrl.press_key.called

    def test_mp_gating_still_works(self):
        """Spell not cast if MP too low, regardless of mob count."""
        ctrl = _make_ctrl()
        hp_det = MagicMock()
        hp_det.read_bars.return_value = (100, 10)  # HP=100, MP=10
        cm = _make_cm(ctrl=ctrl, config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            spells=[
                {"vk": 0x71, "min_mp": 50, "cooldown": 0, "label": "exori", "type": "aoe"},
            ],
        ))
        cm._hp = hp_det
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        cm._cast_spells(frame, mob_count=5)
        assert not ctrl.press_key.called


# ===========================================================================
# 5.3 — Enhanced flee logic (mob count)
# ===========================================================================


class TestFleeMobCountConfig:
    """Config for mob-count-based flee."""

    def test_default_is_zero(self):
        cfg = CombatConfig(battle_list_roi=[0, 0, 100, 100])
        assert cfg.flee_mob_count == 0

    def test_validate_rejects_negative(self):
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            flee_mob_count=-1,
        )
        with pytest.raises(ValueError, match="flee_mob_count"):
            cfg.validate()

    def test_save_load_preserves(self, tmp_path):
        p = tmp_path / "cc.json"
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            flee_mob_count=4,
        )
        cfg.save(p)
        loaded = CombatConfig.load(p)
        assert loaded.flee_mob_count == 4


# ===========================================================================
# 5.5 — Per-monster kill confirmation
# ===========================================================================


class TestPerMonsterKillTracking:
    """Kill counting tracks individual monsters disappearing."""

    def test_prev_detection_names_tracked(self):
        cm = _make_cm()
        assert cm._prev_detection_names == []
        cm._prev_detection_names = ["Wasp", "Bug"]
        assert cm.prev_detection_count == 2

    def test_notify_kill_accepts_name(self):
        cm = _make_cm()
        cm.notify_kill("Wasp")
        assert cm.kills == 1

    def test_notify_kill_no_name_backward_compat(self):
        cm = _make_cm()
        cm.notify_kill()
        assert cm.kills == 1

    def test_on_kill_fires_with_notify(self):
        cm = _make_cm()
        calls = []
        cm.on_kill = lambda: calls.append(1)
        cm.notify_kill("Wasp")
        assert len(calls) == 1


# ===========================================================================
# 5.6 — Anti-lure detection
# ===========================================================================


class TestAntiLureConfig:
    """Config for anti-lure detection."""

    def test_default_disabled(self):
        cfg = CombatConfig(battle_list_roi=[0, 0, 100, 100])
        assert cfg.max_expected_mobs == 0
        assert cfg.lure_action == "warn"

    def test_validate_rejects_negative_max(self):
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            max_expected_mobs=-1,
        )
        with pytest.raises(ValueError, match="max_expected_mobs"):
            cfg.validate()

    def test_validate_rejects_bad_action(self):
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            lure_action="explode",
        )
        with pytest.raises(ValueError, match="lure_action"):
            cfg.validate()

    def test_save_load_preserves(self, tmp_path):
        p = tmp_path / "cc.json"
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            max_expected_mobs=6,
            lure_action="flee",
        )
        cfg.save(p)
        loaded = CombatConfig.load(p)
        assert loaded.max_expected_mobs == 6
        assert loaded.lure_action == "flee"


class TestAntiLureDetection:
    """CombatManager._check_anti_lure behaviour."""

    def test_disabled_when_zero(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            max_expected_mobs=0,
        ))
        assert cm._check_anti_lure(10) is False

    def test_no_warning_below_threshold(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            max_expected_mobs=6,
        ))
        assert cm._check_anti_lure(5) is False
        assert cm.lure_warnings == 0

    def test_warning_above_threshold(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            max_expected_mobs=4,
        ))
        assert cm._check_anti_lure(5) is True
        assert cm.lure_warnings == 1

    def test_cumulative_warnings(self):
        cm = _make_cm(config=CombatConfig(
            battle_list_roi=[0, 0, 100, 100],
            templates_dir="__nonexistent__",
            max_expected_mobs=3,
        ))
        cm._check_anti_lure(5)
        cm._check_anti_lure(4)
        assert cm.lure_warnings == 2

    def test_eventbus_emit_on_lure(self):
        bus = MagicMock()
        cm = _make_cm(
            config=CombatConfig(
                battle_list_roi=[0, 0, 100, 100],
                templates_dir="__nonexistent__",
                max_expected_mobs=3,
                lure_action="warn",
            ),
            event_bus=bus,
        )
        cm._check_anti_lure(5)
        bus.emit.assert_called_once()
        args = bus.emit.call_args
        assert args[0][0] == "e25"
        assert args[0][1]["mob_count"] == 5


# ===========================================================================
# EventBus integration
# ===========================================================================


class TestEventBusIntegration:
    """CombatManager emits events through EventBus."""

    def test_emit_helper_with_bus(self):
        bus = MagicMock()
        cm = _make_cm(event_bus=bus)
        cm._emit("test_event", {"x": 1})
        bus.emit.assert_called_once_with("test_event", {"x": 1})

    def test_emit_helper_without_bus(self):
        cm = _make_cm()
        cm._emit("test_event")  # Should not raise

    def test_emit_helper_bus_exception(self):
        bus = MagicMock()
        bus.emit.side_effect = RuntimeError("boom")
        cm = _make_cm(event_bus=bus)
        cm._emit("test_event")  # Should not raise

    def test_notify_kill_emits_event(self):
        bus = MagicMock()
        cm = _make_cm(event_bus=bus)
        cm.notify_kill("Wasp")
        bus.emit.assert_called_once_with("e1", {"name": "Wasp"})

    def test_stats_snapshot_includes_new_fields(self):
        cm = _make_cm()
        snap = cm.stats_snapshot()
        assert "lure_warnings" in snap
        assert "mobs_visible" in snap
        assert snap["lure_warnings"] == 0
        assert snap["mobs_visible"] == 0


# ===========================================================================
# Loot verification (5.4) — placeholder hooks
# ===========================================================================


class TestLootVerificationHooks:
    """Verify that kill events can drive loot verification workflow."""

    def test_on_kill_callback_for_loot(self):
        """on_kill can be used to trigger looter.notify_kill()."""
        cm = _make_cm()
        loot_notifications = []
        cm.on_kill = lambda: loot_notifications.append("loot")
        cm.notify_kill("Wasp")
        assert loot_notifications == ["loot"]

    def test_multiple_kills_trigger_multiple_loots(self):
        cm = _make_cm()
        loot_calls = []
        cm.on_kill = lambda: loot_calls.append(1)
        cm.notify_kill("Wasp")
        cm.notify_kill("Bug")
        assert len(loot_calls) == 2


# ===========================================================================
# Config validation edge cases
# ===========================================================================


class TestConfigValidationFase5:
    """Validation of all Fase 5 config fields."""

    @pytest.mark.parametrize("field, bad_val, match", [
        ("aoe_mob_threshold", 0, "aoe_mob_threshold"),
        ("flee_mob_count", -1, "flee_mob_count"),
        ("max_expected_mobs", -1, "max_expected_mobs"),
        ("lure_action", "boom", "lure_action"),
    ])
    def test_invalid_values_raise(self, field, bad_val, match):
        kwargs = {"battle_list_roi": [0, 0, 100, 100], field: bad_val}
        cfg = CombatConfig(**kwargs)
        with pytest.raises(ValueError, match=match):
            cfg.validate()

    @pytest.mark.parametrize("field, good_val", [
        ("monster_priority", ["A", "B"]),
        ("aoe_mob_threshold", 1),
        ("aoe_mob_threshold", 5),
        ("flee_mob_count", 0),
        ("flee_mob_count", 10),
        ("max_expected_mobs", 0),
        ("max_expected_mobs", 8),
        ("lure_action", "warn"),
        ("lure_action", "flee"),
        ("lure_action", "ignore"),
    ])
    def test_valid_values_pass(self, field, good_val):
        kwargs = {"battle_list_roi": [0, 0, 100, 100], field: good_val}
        cfg = CombatConfig(**kwargs)
        cfg.validate()
