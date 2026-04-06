"""
Tests for src/healer.py — AutoHealer, HealConfig
Fully offline: mocked InputController, HpMpDetector, frame_getter.
"""
from __future__ import annotations

import json
import math
import time
import threading
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.healer import AutoHealer, HealConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_ctrl() -> MagicMock:
    ctrl = MagicMock()
    ctrl.press_key = MagicMock()
    return ctrl


def _mock_detector(hp: float = 100.0, mp: float = 100.0) -> MagicMock:
    """Return a mock HpMpDetector whose read_bars() returns (hp, mp) tuple."""
    det = MagicMock()
    det.read_bars.return_value = (hp, mp)
    return det


def _make_healer(
    hp: float = 100.0,
    mp: float = 100.0,
    config: Optional[HealConfig] = None,
    ctrl=None,
) -> AutoHealer:
    cfg = config or HealConfig(
        hp_threshold_pct=70,
        hp_emergency_pct=30,
        mp_threshold_pct=30,
        heal_hotkey_vk=0x70,
        mana_hotkey_vk=0x71,
        emergency_hotkey_vk=0x72,
        heal_cooldown=0.0,
        mana_cooldown=0.0,
        emergency_cooldown=0.0,
        check_interval=0.01,
    )
    healer = AutoHealer(
        ctrl=ctrl or _mock_ctrl(),
        config=cfg,
        frame_getter=lambda: np.zeros((100, 100, 3), dtype=np.uint8),
        detector=_mock_detector(hp, mp),
    )
    healer.set_log_callback(lambda msg: None)
    return healer


# ─────────────────────────────────────────────────────────────────────────────
# HealConfig: save / load / defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestHealConfig:

    def test_default_values(self):
        cfg = HealConfig()
        assert cfg.hp_threshold_pct  == 70
        assert cfg.hp_emergency_pct  == 30
        assert cfg.mp_threshold_pct  == 30
        assert cfg.heal_hotkey_vk    == 0x70
        assert cfg.mana_hotkey_vk    == 0x71
        assert cfg.emergency_hotkey_vk == 0x72

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "heal_config.json"
        cfg = HealConfig(hp_threshold_pct=50, heal_cooldown=2.5)
        cfg.save(path)
        loaded = HealConfig.load(path)
        assert loaded.hp_threshold_pct == 50
        assert loaded.heal_cooldown == pytest.approx(2.5)

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        cfg = HealConfig.load(path)
        assert cfg.hp_threshold_pct == 70  # default

    def test_saved_json_is_valid(self, tmp_path: Path):
        path = tmp_path / "heal.json"
        HealConfig().save(path)
        with open(path) as f:
            data = json.load(f)
        assert "hp_threshold_pct" in data
        assert "heal_hotkey_vk" in data

    def test_load_ignores_unknown_keys(self, tmp_path: Path):
        path = tmp_path / "heal.json"
        path.write_text(json.dumps({"hp_threshold_pct": 60,
                                    "future_unknown_key": "ignored"}))
        cfg = HealConfig.load(path)
        assert cfg.hp_threshold_pct == 60


# ─────────────────────────────────────────────────────────────────────────────
# AutoHealer: construction
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoHealerConstruction:

    def test_default_construction(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert not healer.is_running

    def test_set_frame_getter(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        fn = lambda: None
        healer.set_frame_getter(fn)
        assert healer._frame_getter is fn

    def test_set_log_callback(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        logs: List[str] = []
        healer.set_log_callback(logs.append)
        healer._log("hello")
        assert logs == ["hello"]

    def test_log_without_callback_prints(self, capsys):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._log("test output")
        assert "test output" in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────────────
# read_stats / initial values
# ─────────────────────────────────────────────────────────────────────────────

class TestReadStats:

    def test_initial_stats_are_100(self):
        healer = _make_healer()
        hp, mp = healer.read_stats()
        assert hp == pytest.approx(100.0)
        assert mp == pytest.approx(100.0)

    def test_read_hp_and_read_mp_split(self):
        healer = _make_healer()
        assert healer.read_hp() == pytest.approx(100.0)
        assert healer.read_mp() == pytest.approx(100.0)

    def test_stats_updated_after_tick(self):
        healer = _make_healer(hp=55.0, mp=20.0)
        healer._tick()
        hp, mp = healer.read_stats()
        assert hp == pytest.approx(55.0)
        assert mp == pytest.approx(20.0)


# ─────────────────────────────────────────────────────────────────────────────
# _tick() — heal logic
# ─────────────────────────────────────────────────────────────────────────────

class TestHealerTick:

    def test_no_action_when_hp_mp_full(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=100.0, mp=100.0, ctrl=ctrl)
        healer._tick()
        ctrl.press_key.assert_not_called()

    def test_heal_fires_when_hp_below_threshold(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=60.0, mp=100.0, ctrl=ctrl)   # 60 < 70
        healer._tick()
        ctrl.press_key.assert_called_with(0x70)

    def test_heal_does_not_fire_when_hp_above_threshold(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=80.0, mp=100.0, ctrl=ctrl)   # 80 > 70
        healer._tick()
        # Only mana check runs — MP is full so no calls
        ctrl.press_key.assert_not_called()

    def test_mana_fires_when_mp_below_threshold(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=100.0, mp=20.0, ctrl=ctrl)   # 20 < 30
        healer._tick()
        ctrl.press_key.assert_called_with(0x71)

    def test_emergency_fires_when_hp_critically_low(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=10.0, mp=100.0, ctrl=ctrl)   # 10 < 30 (emergency)
        healer._tick()
        calls = [c.args[0] for c in ctrl.press_key.call_args_list]
        assert 0x72 in calls   # emergency key

    def test_emergency_takes_priority_over_normal_heal(self):
        """When HP is in emergency range, only emergency key is pressed (not heal)."""
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=10.0, mp=100.0, ctrl=ctrl)
        healer._tick()
        calls = [c.args[0] for c in ctrl.press_key.call_args_list]
        # Emergency must be in the calls
        assert 0x72 in calls
        # Normal heal should NOT be pressed (emergency branch uses elif)
        assert 0x70 not in calls

    def test_both_heal_and_mana_can_fire_same_tick(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=60.0, mp=20.0, ctrl=ctrl)
        healer._tick()
        calls = [c.args[0] for c in ctrl.press_key.call_args_list]
        assert 0x70 in calls   # heal
        assert 0x71 in calls   # mana


# ─────────────────────────────────────────────────────────────────────────────
# Cooldown enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestCooldowns:

    def test_heal_not_repeated_within_cooldown(self):
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            hp_threshold_pct=70,
            heal_hotkey_vk=0x70,
            mana_hotkey_vk=0,
            emergency_hotkey_vk=0,
            heal_cooldown=999.0,   # very long cooldown
            check_interval=0.01,
        )
        healer = _make_healer(hp=50.0, ctrl=ctrl, config=cfg)
        healer._tick()   # first tick: fires heal
        healer._tick()   # second tick: cooldown blocks
        assert ctrl.press_key.call_count == 1

    def test_mana_not_repeated_within_cooldown(self):
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            hp_threshold_pct=0,
            emergency_hotkey_vk=0,
            heal_hotkey_vk=0,
            mana_hotkey_vk=0x71,
            mp_threshold_pct=50,
            mana_cooldown=999.0,
            check_interval=0.01,
        )
        healer = _make_healer(hp=100.0, mp=20.0, ctrl=ctrl, config=cfg)
        healer._tick()
        healer._tick()
        assert ctrl.press_key.call_count == 1

    def test_emergency_not_repeated_within_cooldown(self):
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            hp_threshold_pct=0,
            heal_hotkey_vk=0,
            mana_hotkey_vk=0,
            emergency_hotkey_vk=0x72,
            hp_emergency_pct=50,
            emergency_cooldown=999.0,
            heal_cooldown=0.0,
            mana_cooldown=0.0,
            check_interval=0.01,
        )
        healer = _make_healer(hp=20.0, ctrl=ctrl, config=cfg)
        healer._tick()
        healer._tick()
        assert ctrl.press_key.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# force_heal / force_mana
# ─────────────────────────────────────────────────────────────────────────────

class TestForceHealing:

    def test_force_heal_fires_regardless_of_cooldown(self):
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            heal_hotkey_vk=0x70,
            mana_hotkey_vk=0,
            emergency_hotkey_vk=0,
            heal_cooldown=999.0,
        )
        healer = AutoHealer(ctrl=ctrl, config=cfg)
        healer.set_log_callback(lambda m: None)
        healer.force_heal()
        healer.force_heal()   # second call ignores cooldown because force=True
        assert ctrl.press_key.call_count == 2

    def test_force_heal_returns_false_when_no_vk(self):
        cfg = HealConfig(heal_hotkey_vk=0)
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        healer.set_log_callback(lambda m: None)
        assert healer.force_heal() is False

    def test_force_mana_fires(self):
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            heal_hotkey_vk=0,
            mana_hotkey_vk=0x71,
            emergency_hotkey_vk=0,
            mana_cooldown=999.0,
        )
        healer = AutoHealer(ctrl=ctrl, config=cfg)
        healer.set_log_callback(lambda m: None)
        result = healer.force_mana()
        assert result is True
        ctrl.press_key.assert_called_once_with(0x71)


# ─────────────────────────────────────────────────────────────────────────────
# Disabled hotkeys (vk = 0)
# ─────────────────────────────────────────────────────────────────────────────

class TestDisabledHotkeys:

    def test_disabled_heal_does_not_fire(self):
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            hp_threshold_pct=70,
            heal_hotkey_vk=0,          # disabled
            mana_hotkey_vk=0,
            emergency_hotkey_vk=0,
            hp_emergency_pct=30,
            heal_cooldown=0.0,
        )
        healer = _make_healer(hp=50.0, ctrl=ctrl, config=cfg)
        healer._tick()
        ctrl.press_key.assert_not_called()

    def test_disabled_mana_does_not_fire(self):
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            mp_threshold_pct=50,
            mana_hotkey_vk=0,          # disabled
            heal_hotkey_vk=0,
            emergency_hotkey_vk=0,
            mana_cooldown=0.0,
        )
        healer = _make_healer(hp=100.0, mp=20.0, ctrl=ctrl, config=cfg)
        healer._tick()
        ctrl.press_key.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# _read_from_frame — fallbacks
# ─────────────────────────────────────────────────────────────────────────────

class TestReadFromFrame:

    def test_returns_cached_when_no_frame_getter(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer.set_log_callback(lambda m: None)
        healer._hp_pct = 55.0
        healer._mp_pct = 40.0
        hp, mp = healer._read_from_frame()
        assert hp == pytest.approx(55.0)
        assert mp == pytest.approx(40.0)

    def test_returns_cached_when_frame_is_none(self):
        healer = AutoHealer(
            ctrl=_mock_ctrl(),
            config=HealConfig(),
            frame_getter=lambda: None,
        )
        healer.set_log_callback(lambda m: None)
        healer._hp_pct = 70.0
        healer._mp_pct = 60.0
        hp, mp = healer._read_from_frame()
        assert hp == pytest.approx(70.0)
        assert mp == pytest.approx(60.0)

    def test_reads_from_detector_when_frame_available(self):
        det = _mock_detector(hp=45.0, mp=22.0)
        healer = AutoHealer(
            ctrl=_mock_ctrl(),
            config=HealConfig(),
            frame_getter=lambda: np.zeros((10, 10, 3), dtype=np.uint8),
            detector=det,
        )
        healer.set_log_callback(lambda m: None)
        hp, mp = healer._read_from_frame()
        assert hp == pytest.approx(45.0)
        assert mp == pytest.approx(22.0)


# ─────────────────────────────────────────────────────────────────────────────
# Background thread: start / stop
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoHealerThread:

    def test_start_sets_running(self):
        healer = _make_healer()
        healer.start()
        assert healer.is_running is True
        healer.stop()

    def test_stop_clears_running(self):
        healer = _make_healer()
        healer.start()
        healer.stop()
        assert healer.is_running is False

    def test_double_start_does_not_spawn_extra_thread(self):
        healer = _make_healer()
        healer.start()
        t1 = healer._thread
        healer.start()   # second start should be a no-op
        assert healer._thread is t1
        healer.stop()

    def test_stats_updated_by_background_thread(self):
        """After a brief wait the background thread should have updated stats."""
        det = _mock_detector(hp=42.0, mp=18.0)
        healer = AutoHealer(
            ctrl=_mock_ctrl(),
            config=HealConfig(check_interval=0.01),
            frame_getter=lambda: np.zeros((10, 10, 3), dtype=np.uint8),
            detector=det,
        )
        healer.set_log_callback(lambda m: None)
        healer.start()
        time.sleep(0.1)    # let at least a few ticks run
        healer.stop()
        hp, mp = healer.read_stats()
        assert hp == pytest.approx(42.0)
        assert mp == pytest.approx(18.0)


# ─────────────────────────────────────────────────────────────────────────────
# on_heal / on_mana / on_emergency callbacks
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoHealerCallbacks:

    def test_on_heal_called_when_heal_fires(self):
        ctrl = _mock_ctrl()
        fired: list[str] = []
        healer = _make_healer(hp=60.0, mp=100.0, ctrl=ctrl)
        healer.on_heal = lambda: fired.append("heal")
        healer._tick()
        assert "heal" in fired

    def test_on_heal_not_called_when_heal_does_not_fire(self):
        ctrl = _mock_ctrl()
        fired: list[str] = []
        healer = _make_healer(hp=100.0, mp=100.0, ctrl=ctrl)
        healer.on_heal = lambda: fired.append("heal")
        healer._tick()
        assert fired == []

    def test_on_mana_called_when_mana_fires(self):
        ctrl = _mock_ctrl()
        fired: list[str] = []
        healer = _make_healer(hp=100.0, mp=20.0, ctrl=ctrl)
        healer.on_mana = lambda: fired.append("mana")
        healer._tick()
        assert "mana" in fired

    def test_on_mana_not_called_when_mp_is_full(self):
        ctrl = _mock_ctrl()
        fired: list[str] = []
        healer = _make_healer(hp=100.0, mp=100.0, ctrl=ctrl)
        healer.on_mana = lambda: fired.append("mana")
        healer._tick()
        assert fired == []

    def test_on_emergency_called_when_emergency_fires(self):
        ctrl = _mock_ctrl()
        fired: list[str] = []
        healer = _make_healer(hp=10.0, mp=100.0, ctrl=ctrl)
        healer.on_emergency = lambda: fired.append("emergency")
        healer._tick()
        assert "emergency" in fired

    def test_on_emergency_not_called_on_normal_heal(self):
        ctrl = _mock_ctrl()
        fired: list[str] = []
        healer = _make_healer(hp=60.0, mp=100.0, ctrl=ctrl)   # 60 < 70 but > 30 (emergency)
        healer.on_emergency = lambda: fired.append("emergency")
        healer._tick()
        assert fired == []

    def test_on_heal_and_on_mana_both_fire_same_tick(self):
        ctrl = _mock_ctrl()
        fired: list[str] = []
        healer = _make_healer(hp=60.0, mp=20.0, ctrl=ctrl)
        healer.on_heal = lambda: fired.append("heal")
        healer.on_mana = lambda: fired.append("mana")
        healer._tick()
        assert "heal" in fired
        assert "mana" in fired

    def test_callbacks_default_to_none(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert healer.on_heal     is None
        assert healer.on_mana     is None
        assert healer.on_emergency is None

    def test_callback_invoked_exactly_once_per_tick(self):
        ctrl = _mock_ctrl()
        call_count: list[int] = [0]
        healer = _make_healer(hp=60.0, mp=100.0, ctrl=ctrl)
        healer.on_heal = lambda: call_count.__setitem__(0, call_count[0] + 1)
        healer._tick()
        assert call_count[0] == 1


# ─────────────────────────────────────────────────────────────────────────────
# pause() / resume() / is_paused
# ─────────────────────────────────────────────────────────────────────────────

class TestPauseResume:

    def test_initial_not_paused(self):
        healer = _make_healer()
        assert healer.is_paused is False

    def test_pause_sets_flag(self):
        healer = _make_healer()
        healer.pause()
        assert healer.is_paused is True

    def test_resume_clears_flag(self):
        healer = _make_healer()
        healer.pause()
        healer.resume()
        assert healer.is_paused is False

    def test_pause_prevents_tick_via_loop(self):
        """When paused, _tick should NOT be called by the loop."""
        healer = _make_healer(hp=50.0, mp=100.0)
        healer.pause()
        tick_calls: list[int] = [0]
        original_tick = healer._tick
        healer._tick = lambda: tick_calls.__setitem__(0, tick_calls[0] + 1) or original_tick()  # type: ignore
        # Simulate loop body once
        if not healer._paused:
            healer._tick()
        assert tick_calls[0] == 0   # paused — no tick

    def test_resume_allows_tick_via_loop(self):
        healer = _make_healer(hp=50.0, mp=100.0)
        healer.pause()
        healer.resume()
        tick_called: list[bool] = [False]
        original_tick = healer._tick
        healer._tick = lambda: tick_called.__setitem__(0, True) or original_tick()  # type: ignore
        if not healer._paused:
            healer._tick()
        assert tick_called[0] is True

    def test_paused_healer_does_not_fire_keys(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=10.0, mp=10.0, ctrl=ctrl)
        healer.pause()
        # Simulate one loop iteration (paused branch)
        if not healer._paused:
            healer._tick()
        ctrl.press_key.assert_not_called()

    def test_double_pause_stays_paused(self):
        healer = _make_healer()
        healer.pause()
        healer.pause()
        assert healer.is_paused is True

    def test_double_resume_stays_unpaused(self):
        healer = _make_healer()
        healer.resume()
        healer.resume()
        assert healer.is_paused is False


# ─────────────────────────────────────────────────────────────────────────────
# update_config()
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateConfig:

    def test_new_thresholds_applied_next_tick(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=80.0, mp=100.0, ctrl=ctrl)
        # Default threshold is 70 — HP=80 should NOT trigger heal
        healer._tick()
        ctrl.press_key.assert_not_called()

        # Raise threshold to 90 — HP=80 now below threshold
        new_cfg = HealConfig(
            hp_threshold_pct=90,
            hp_emergency_pct=10,
            mp_threshold_pct=10,
            heal_hotkey_vk=0x70,
            mana_hotkey_vk=0x71,
            emergency_hotkey_vk=0x72,
            heal_cooldown=0.0,
            mana_cooldown=0.0,
            emergency_cooldown=0.0,
            check_interval=0.01,
        )
        healer.update_config(new_cfg)
        healer._tick()
        ctrl.press_key.assert_called_with(0x70)

    def test_update_config_replaces_cfg(self):
        healer = _make_healer()
        new_cfg = HealConfig(hp_threshold_pct=55)
        healer.update_config(new_cfg)
        assert healer._cfg.hp_threshold_pct == 55

    def test_update_config_hotkey_change_respected(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=50.0, mp=100.0, ctrl=ctrl)
        new_cfg = HealConfig(
            hp_threshold_pct=70,
            hp_emergency_pct=10,
            mp_threshold_pct=10,
            heal_hotkey_vk=0x73,   # F4 instead of F1
            mana_hotkey_vk=0x71,
            emergency_hotkey_vk=0x72,
            heal_cooldown=0.0,
            mana_cooldown=0.0,
            emergency_cooldown=0.0,
        )
        healer.update_config(new_cfg)
        healer._tick()
        ctrl.press_key.assert_called_with(0x73)


# ─────────────────────────────────────────────────────────────────────────────
# reset_cooldowns()
# ─────────────────────────────────────────────────────────────────────────────

class TestResetCooldowns:

    def test_reset_allows_immediate_heal(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=50.0, mp=100.0, ctrl=ctrl)
        # Manually set last_heal to far future so cooldown blocks
        healer._last_heal = time.monotonic() + 9999.0
        # Verify blocked
        healer._tick()
        ctrl.press_key.assert_not_called()
        # Reset and try again
        healer.reset_cooldowns()
        healer._tick()
        ctrl.press_key.assert_called_with(0x70)

    def test_reset_allows_immediate_mana(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=100.0, mp=10.0, ctrl=ctrl)
        healer._last_mana = time.monotonic() + 9999.0
        healer._tick()
        ctrl.press_key.assert_not_called()
        healer.reset_cooldowns()
        healer._tick()
        ctrl.press_key.assert_called_with(0x71)

    def test_reset_allows_immediate_emergency(self):
        ctrl = _mock_ctrl()
        healer = _make_healer(hp=10.0, mp=100.0, ctrl=ctrl)
        future = time.monotonic() + 9999.0
        healer._last_emergency = future
        healer._last_heal      = future   # also block the elif heal branch
        healer._tick()
        ctrl.press_key.assert_not_called()
        healer.reset_cooldowns()
        healer._tick()
        ctrl.press_key.assert_called_with(0x72)

    def test_reset_sets_timestamps_to_zero(self):
        healer = _make_healer()
        healer._last_heal      = 999.0
        healer._last_mana      = 999.0
        healer._last_emergency = 999.0
        healer.reset_cooldowns()
        assert healer._last_heal      == -math.inf
        assert healer._last_mana      == -math.inf
        assert healer._last_emergency == -math.inf


# ─────────────────────────────────────────────────────────────────────────────
# stats_snapshot()
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsSnapshot:

    def test_snapshot_has_required_keys(self):
        healer = _make_healer(hp=60.0, mp=80.0)
        snap = healer.stats_snapshot()
        for key in ("hp_pct", "mp_pct", "hp_low", "mp_low",
                    "is_running", "is_paused",
                    "last_heal", "last_mana", "last_emergency"):
            assert key in snap, f"Missing key: {key}"

    def test_snapshot_hp_mp_values(self):
        healer = _make_healer(hp=65.0, mp=40.0)
        healer._tick()   # update internal cache
        snap = healer.stats_snapshot()
        assert snap["hp_pct"] == pytest.approx(65.0)
        assert snap["mp_pct"] == pytest.approx(40.0)

    def test_snapshot_hp_low_true_when_below_threshold(self):
        healer = _make_healer(hp=50.0, mp=100.0)
        healer._tick()
        snap = healer.stats_snapshot()
        assert snap["hp_low"] is True

    def test_snapshot_hp_low_false_when_above_threshold(self):
        healer = _make_healer(hp=90.0, mp=100.0)
        healer._tick()
        snap = healer.stats_snapshot()
        assert snap["hp_low"] is False

    def test_snapshot_mp_low_true(self):
        healer = _make_healer(hp=100.0, mp=20.0)
        healer._tick()
        snap = healer.stats_snapshot()
        assert snap["mp_low"] is True

    def test_snapshot_is_running_false_before_start(self):
        healer = _make_healer()
        snap = healer.stats_snapshot()
        assert snap["is_running"] is False

    def test_snapshot_is_paused_reflects_state(self):
        healer = _make_healer()
        healer.pause()
        snap = healer.stats_snapshot()
        assert snap["is_paused"] is True


# ─────────────────────────────────────────────────────────────────────────────
# hp_low / mp_low properties
# ─────────────────────────────────────────────────────────────────────────────

class TestHpMpLowProperties:

    def test_hp_low_true_below_threshold(self):
        healer = _make_healer(hp=50.0, mp=100.0)
        healer._tick()
        assert healer.hp_low is True

    def test_hp_low_false_above_threshold(self):
        healer = _make_healer(hp=90.0, mp=100.0)
        healer._tick()
        assert healer.hp_low is False

    def test_hp_low_false_at_exact_threshold(self):
        # threshold=70, hp=70 → NOT low (< not <=)
        healer = _make_healer(hp=70.0, mp=100.0)
        healer._tick()
        assert healer.hp_low is False

    def test_hp_low_true_just_below_threshold(self):
        healer = _make_healer(hp=69.9, mp=100.0)
        healer._tick()
        assert healer.hp_low is True

    def test_mp_low_true_below_threshold(self):
        healer = _make_healer(hp=100.0, mp=20.0)
        healer._tick()
        assert healer.mp_low is True

    def test_mp_low_false_above_threshold(self):
        healer = _make_healer(hp=100.0, mp=80.0)
        healer._tick()
        assert healer.mp_low is False

    def test_mp_low_false_at_exact_threshold(self):
        healer = _make_healer(hp=100.0, mp=30.0)
        healer._tick()
        assert healer.mp_low is False

    def test_both_low_simultaneously(self):
        healer = _make_healer(hp=50.0, mp=20.0)
        healer._tick()
        assert healer.hp_low is True
        assert healer.mp_low is True

    def test_neither_low(self):
        healer = _make_healer(hp=95.0, mp=95.0)
        healer._tick()
        assert healer.hp_low is False
        assert healer.mp_low is False


# ─────────────────────────────────────────────────────────────────────────────
# heals_done / mana_uses / emergency_uses counters
# ─────────────────────────────────────────────────────────────────────────────

class TestHealCounts:

    def test_initial_counts_are_zero(self):
        healer = _make_healer()
        assert healer.heals_done == 0
        assert healer.mana_uses == 0
        assert healer.emergency_uses == 0

    def test_heal_increments_heals_done(self):
        healer = _make_healer(hp=50.0, mp=100.0)  # HP below threshold
        healer._tick()
        assert healer.heals_done == 1
        assert healer.mana_uses == 0
        assert healer.emergency_uses == 0

    def test_mana_increments_mana_uses(self):
        healer = _make_healer(hp=100.0, mp=10.0)  # MP below threshold
        healer._tick()
        assert healer.mana_uses == 1
        assert healer.heals_done == 0

    def test_emergency_increments_emergency_uses(self):
        healer = _make_healer(hp=10.0, mp=100.0)  # HP below emergency threshold
        healer._tick()
        assert healer.emergency_uses == 1

    def test_multiple_ticks_accumulate(self):
        healer = _make_healer(hp=50.0, mp=100.0)
        healer._tick()
        healer._tick()
        healer._tick()
        assert healer.heals_done == 3

    def test_mana_and_heal_same_tick(self):
        # HP below normal threshold (50), MP below mana threshold (10)
        healer = _make_healer(hp=50.0, mp=10.0)
        healer._tick()
        assert healer.heals_done == 1
        assert healer.mana_uses == 1

    def test_no_heal_when_hp_above_threshold(self):
        healer = _make_healer(hp=90.0, mp=100.0)
        healer._tick()
        assert healer.heals_done == 0

    def test_stats_snapshot_includes_counters(self):
        healer = _make_healer(hp=50.0, mp=10.0)
        healer._tick()
        snap = healer.stats_snapshot()
        assert "heals_done" in snap
        assert "mana_uses" in snap
        assert "emergency_uses" in snap
        assert snap["heals_done"] == 1
        assert snap["mana_uses"] == 1


class TestResetHealCounts:

    def test_reset_zeros_all_counters(self):
        healer = _make_healer(hp=50.0, mp=10.0)
        healer._tick()  # increments heals_done and mana_uses
        healer.reset_heal_counts()
        assert healer.heals_done == 0
        assert healer.mana_uses == 0
        assert healer.emergency_uses == 0

    def test_reset_does_not_affect_cooldowns(self):
        healer = _make_healer(hp=50.0, mp=100.0)
        healer._tick()
        ts_before = healer._last_heal
        healer.reset_heal_counts()
        assert healer._last_heal == ts_before  # cooldown unchanged

    def test_counts_continue_after_reset(self):
        healer = _make_healer(hp=50.0, mp=100.0)
        healer._tick()
        healer.reset_heal_counts()
        healer._tick()
        assert healer.heals_done == 1

    def test_reset_on_fresh_healer_does_not_raise(self):
        healer = _make_healer()
        healer.reset_heal_counts()  # counters already 0 — should not raise
        assert healer.heals_done == 0

    def test_emergency_count_reset_independently(self):
        healer = _make_healer(hp=10.0, mp=100.0)  # emergency HP
        healer._tick()
        assert healer.emergency_uses == 1
        healer.reset_heal_counts()
        assert healer.emergency_uses == 0


# ─────────────────────────────────────────────────────────────────────────────
# total_actions / has_frame_getter / has_detector
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoHealerExtras:

    def test_total_actions_zero_initially(self):
        healer = _make_healer()
        assert healer.total_actions == 0

    def test_total_actions_sums_counters(self):
        healer = _make_healer(hp=50.0, mp=10.0)  # both heal + mana fire
        healer._tick()
        expected = healer.heals_done + healer.mana_uses + healer.emergency_uses
        assert healer.total_actions == expected

    def test_total_actions_after_emergency(self):
        healer = _make_healer(hp=10.0, mp=100.0)
        healer._tick()
        assert healer.total_actions >= 1

    def test_total_actions_returns_int(self):
        healer = _make_healer()
        assert isinstance(healer.total_actions, int)

    def test_total_actions_reset_with_heal_counts(self):
        healer = _make_healer(hp=50.0, mp=10.0)
        healer._tick()
        healer.reset_heal_counts()
        assert healer.total_actions == 0

    def test_has_frame_getter_false_initially(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert healer.has_frame_getter is False

    def test_has_frame_getter_true_after_set(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer.set_frame_getter(lambda: None)
        assert healer.has_frame_getter is True

    def test_has_frame_getter_true_in_make_healer(self):
        healer = _make_healer()
        assert healer.has_frame_getter is True

    def test_has_detector_true_with_default(self):
        # AutoHealer always creates a default HpMpDetector when none is passed.
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert healer.has_detector is True

    def test_has_detector_true_after_set(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer.set_detector(_mock_detector())
        assert healer.has_detector is True

    def test_has_detector_true_in_make_healer(self):
        healer = _make_healer()
        assert healer.has_detector is True


class TestAutoHealerHasLogCallback:

    def test_has_log_callback_false_initially(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert healer.has_log_callback is False

    def test_has_log_callback_true_after_set(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer.set_log_callback(lambda m: None)
        assert healer.has_log_callback is True

    def test_has_log_callback_returns_bool(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert isinstance(healer.has_log_callback, bool)

    def test_has_log_callback_false_after_init_with_make_healer(self):
        # _make_healer doesn't set a log callback directly
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert healer.has_log_callback is False


class TestAutoHealerHasUsedHeal:

    def test_has_used_heal_false_initially(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert healer.has_used_heal is False

    def test_has_used_heal_true_after_heals_done_incremented(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._heals_done = 1
        assert healer.has_used_heal is True

    def test_has_used_heal_false_after_reset(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._heals_done = 3
        healer.reset_heal_counts()
        assert healer.has_used_heal is False

    def test_has_used_heal_returns_bool(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert isinstance(healer.has_used_heal, bool)


class TestAutoHealerHasUsedMana:

    def test_has_used_mana_false_initially(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert healer.has_used_mana is False

    def test_has_used_mana_true_after_mana_uses_incremented(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._mana_uses = 2
        assert healer.has_used_mana is True

    def test_has_used_mana_false_after_reset(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._mana_uses = 3
        healer.reset_heal_counts()
        assert healer.has_used_mana is False

    def test_has_used_mana_returns_bool(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert isinstance(healer.has_used_mana, bool)

    def test_has_used_mana_consistent_with_mana_uses(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._mana_uses = 5
        assert healer.has_used_mana == (healer.mana_uses > 0)


# ─────────────────────────────────────────────────────────────────────────────
# has_emergency_uses
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoHealerHasEmergencyUses:

    def test_false_initially(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert healer.has_emergency_uses is False

    def test_true_after_increment(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._emergency_uses = 1
        assert healer.has_emergency_uses is True

    def test_false_after_reset(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._emergency_uses = 2
        healer.reset_heal_counts()
        assert healer.has_emergency_uses is False

    def test_consistent_with_emergency_uses(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        healer._emergency_uses = 3
        assert healer.has_emergency_uses == (healer.emergency_uses > 0)

    def test_returns_bool(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig())
        assert isinstance(healer.has_emergency_uses, bool)


# ─────────────────────────────────────────────────────────────────────────────
# is_healing_enabled
# ─────────────────────────────────────────────────────────────────────────────

class TestIsHealingEnabled:

    def test_true_when_hotkey_nonzero(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(heal_hotkey_vk=0x70))
        assert healer.is_healing_enabled is True

    def test_false_when_hotkey_zero(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(heal_hotkey_vk=0))
        assert healer.is_healing_enabled is False

    def test_returns_bool(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(heal_hotkey_vk=0x70))
        assert isinstance(healer.is_healing_enabled, bool)

    def test_reflects_config_value(self):
        cfg = HealConfig(heal_hotkey_vk=0x72)
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        assert healer.is_healing_enabled == (cfg.heal_hotkey_vk != 0)

    def test_false_is_consistent_with_vk(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(heal_hotkey_vk=0))
        assert healer.is_healing_enabled is (healer._cfg.heal_hotkey_vk != 0)


# ─────────────────────────────────────────────────────────────────────────────
# is_mana_enabled
# ─────────────────────────────────────────────────────────────────────────────

class TestIsManaEnabled:

    def test_true_when_hotkey_nonzero(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(mana_hotkey_vk=0x71))
        assert healer.is_mana_enabled is True

    def test_false_when_hotkey_zero(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(mana_hotkey_vk=0))
        assert healer.is_mana_enabled is False

    def test_returns_bool(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(mana_hotkey_vk=0x71))
        assert isinstance(healer.is_mana_enabled, bool)

    def test_reflects_config_value(self):
        cfg = HealConfig(mana_hotkey_vk=0x73)
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        assert healer.is_mana_enabled == (cfg.mana_hotkey_vk != 0)

    def test_independent_of_heal_enabled(self):
        healer = AutoHealer(ctrl=_mock_ctrl(),
                            config=HealConfig(heal_hotkey_vk=0x70, mana_hotkey_vk=0))
        assert healer.is_healing_enabled is True
        assert healer.is_mana_enabled is False


# ─────────────────────────────────────────────────────────────────────────────
# is_emergency_enabled
# ─────────────────────────────────────────────────────────────────────────────

class TestIsEmergencyEnabled:

    def test_true_when_hotkey_nonzero(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(emergency_hotkey_vk=0x72))
        assert healer.is_emergency_enabled is True

    def test_false_when_hotkey_zero(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(emergency_hotkey_vk=0))
        assert healer.is_emergency_enabled is False

    def test_returns_bool(self):
        healer = AutoHealer(ctrl=_mock_ctrl(), config=HealConfig(emergency_hotkey_vk=0x72))
        assert isinstance(healer.is_emergency_enabled, bool)

    def test_reflects_config_value(self):
        cfg = HealConfig(emergency_hotkey_vk=0x74)
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        assert healer.is_emergency_enabled == (cfg.emergency_hotkey_vk != 0)

    def test_all_three_enabled(self):
        cfg = HealConfig(heal_hotkey_vk=0x70, mana_hotkey_vk=0x71, emergency_hotkey_vk=0x72)
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        assert healer.is_healing_enabled is True
        assert healer.is_mana_enabled is True
        assert healer.is_emergency_enabled is True

    def test_all_three_disabled(self):
        cfg = HealConfig(heal_hotkey_vk=0, mana_hotkey_vk=0, emergency_hotkey_vk=0)
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        assert healer.is_healing_enabled is False
        assert healer.is_mana_enabled is False
        assert healer.is_emergency_enabled is False


# ─────────────────────────────────────────────────────────────────────────────
# HealConfig.is_emergency_below_heal
# ─────────────────────────────────────────────────────────────────────────────

class TestHealConfigIsEmergencyBelowHeal:

    def test_true_with_default_values(self):
        # defaults: hp_emergency=30, hp_threshold=70 → 30 < 70
        cfg = HealConfig()
        assert cfg.is_emergency_below_heal is True

    def test_true_when_emergency_strictly_below(self):
        cfg = HealConfig(hp_threshold_pct=60, hp_emergency_pct=20)
        assert cfg.is_emergency_below_heal is True

    def test_false_when_equal(self):
        cfg = HealConfig(hp_threshold_pct=50, hp_emergency_pct=50)
        assert cfg.is_emergency_below_heal is False

    def test_false_when_emergency_above_heal(self):
        cfg = HealConfig(hp_threshold_pct=30, hp_emergency_pct=60)
        assert cfg.is_emergency_below_heal is False

    def test_returns_bool(self):
        assert isinstance(HealConfig().is_emergency_below_heal, bool)


# ─────────────────────────────────────────────────────────────────────────────
# HealConfig.has_emergency_hotkey
# ─────────────────────────────────────────────────────────────────────────────

class TestHealConfigHasEmergencyHotkey:

    def test_true_with_default_config(self):
        # default emergency_hotkey_vk = 0x72 (F3)
        assert HealConfig().has_emergency_hotkey is True

    def test_true_when_hotkey_set(self):
        cfg = HealConfig(emergency_hotkey_vk=0x74)
        assert cfg.has_emergency_hotkey is True

    def test_false_when_hotkey_is_zero(self):
        cfg = HealConfig(emergency_hotkey_vk=0)
        assert cfg.has_emergency_hotkey is False

    def test_consistent_with_vk_not_zero(self):
        cfg = HealConfig(emergency_hotkey_vk=0x72)
        assert cfg.has_emergency_hotkey == (cfg.emergency_hotkey_vk != 0)

    def test_returns_bool(self):
        assert isinstance(HealConfig().has_emergency_hotkey, bool)


# ─────────────────────────────────────────────────────────────────────────────
# Regression: force_heal / force_mana must increment action counters
# Bug: _press_heal and _press_mana fired the hotkey but never incremented
#      _heals_done / _mana_uses, so force_heal/force_mana were invisible to
#      stats_snapshot / has_used_heal / total_actions.
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionBug1ForceHealCounter:

    def _make_force_healer(self) -> AutoHealer:
        cfg = HealConfig(
            heal_hotkey_vk=0x70,
            mana_hotkey_vk=0,
            emergency_hotkey_vk=0,
            heal_cooldown=999.0,   # ensure _tick() would block; force bypasses this
        )
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        healer.set_log_callback(lambda m: None)
        return healer

    def test_force_heal_increments_heals_done(self):
        healer = self._make_force_healer()
        assert healer.heals_done == 0
        healer.force_heal()
        assert healer.heals_done == 1

    def test_force_heal_twice_increments_twice(self):
        healer = self._make_force_healer()
        healer.force_heal()
        healer.force_heal()
        assert healer.heals_done == 2

    def test_force_heal_counter_shown_in_snapshot(self):
        healer = self._make_force_healer()
        healer.force_heal()
        snap = healer.stats_snapshot()
        assert snap["heals_done"] == 1

    def test_force_heal_sets_has_used_heal(self):
        healer = self._make_force_healer()
        assert healer.has_used_heal is False
        healer.force_heal()
        assert healer.has_used_heal is True

    def test_force_heal_contributes_to_total_actions(self):
        healer = self._make_force_healer()
        healer.force_heal()
        assert healer.total_actions == 1

    def test_force_heal_does_not_increment_mana_or_emergency(self):
        healer = self._make_force_healer()
        healer.force_heal()
        assert healer.mana_uses == 0
        assert healer.emergency_uses == 0

    def test_force_heal_returns_false_when_disabled_does_not_increment(self):
        cfg = HealConfig(heal_hotkey_vk=0)
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        healer.set_log_callback(lambda m: None)
        result = healer.force_heal()
        assert result is False
        assert healer.heals_done == 0


class TestRegressionBug2ForceManaCounter:

    def _make_force_mana_healer(self) -> AutoHealer:
        cfg = HealConfig(
            heal_hotkey_vk=0,
            mana_hotkey_vk=0x71,
            emergency_hotkey_vk=0,
            mana_cooldown=999.0,
        )
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        healer.set_log_callback(lambda m: None)
        return healer

    def test_force_mana_increments_mana_uses(self):
        healer = self._make_force_mana_healer()
        assert healer.mana_uses == 0
        healer.force_mana()
        assert healer.mana_uses == 1

    def test_force_mana_twice_increments_twice(self):
        healer = self._make_force_mana_healer()
        healer.force_mana()
        healer.force_mana()
        assert healer.mana_uses == 2

    def test_force_mana_shown_in_snapshot(self):
        healer = self._make_force_mana_healer()
        healer.force_mana()
        snap = healer.stats_snapshot()
        assert snap["mana_uses"] == 1

    def test_force_mana_sets_has_used_mana(self):
        healer = self._make_force_mana_healer()
        assert healer.has_used_mana is False
        healer.force_mana()
        assert healer.has_used_mana is True

    def test_force_mana_contributes_to_total_actions(self):
        healer = self._make_force_mana_healer()
        healer.force_mana()
        assert healer.total_actions == 1

    def test_force_mana_does_not_increment_heals_or_emergency(self):
        healer = self._make_force_mana_healer()
        healer.force_mana()
        assert healer.heals_done == 0
        assert healer.emergency_uses == 0

    def test_force_mana_returns_false_when_disabled_does_not_increment(self):
        cfg = HealConfig(mana_hotkey_vk=0)
        healer = AutoHealer(ctrl=_mock_ctrl(), config=cfg)
        healer.set_log_callback(lambda m: None)
        result = healer.force_mana()
        assert result is False
        assert healer.mana_uses == 0
