"""
Tests for new buff features in src/healer.py:
  - HealConfig utamo/haste fields
  - AutoHealer utamo vita casting
  - AutoHealer Auto Hur casting + condition suppression
  - FriendHealConfig / FriendHealer
"""
from __future__ import annotations

import time
from typing import Optional, Set
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.healer import AutoHealer, FriendHealConfig, FriendHealer, HealConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_ctrl() -> MagicMock:
    ctrl = MagicMock()
    ctrl.press_key = MagicMock()
    return ctrl


def _mock_detector(hp: float = 100.0, mp: float = 100.0) -> MagicMock:
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
# HealConfig — new buff fields
# ─────────────────────────────────────────────────────────────────────────────

class TestHealConfigBuffFields:

    def test_utamo_defaults(self):
        cfg = HealConfig()
        assert cfg.utamo_hotkey_vk  == 0
        assert cfg.utamo_min_mp_pct == 95
        assert cfg.utamo_cooldown   == pytest.approx(22.0)

    def test_haste_defaults(self):
        cfg = HealConfig()
        assert cfg.haste_hotkey_vk == 0
        assert cfg.haste_cooldown  == pytest.approx(16.0)

    def test_utamo_field_roundtrip(self, tmp_path):
        path = tmp_path / "heal_config.json"
        cfg = HealConfig(utamo_hotkey_vk=0x73, utamo_min_mp_pct=90, utamo_cooldown=20.0)
        cfg.save(path)
        loaded = HealConfig.load(path)
        assert loaded.utamo_hotkey_vk  == 0x73
        assert loaded.utamo_min_mp_pct == 90
        assert loaded.utamo_cooldown   == pytest.approx(20.0)

    def test_haste_field_roundtrip(self, tmp_path):
        path = tmp_path / "heal_config.json"
        cfg = HealConfig(haste_hotkey_vk=0x74, haste_cooldown=14.0)
        cfg.save(path)
        loaded = HealConfig.load(path)
        assert loaded.haste_hotkey_vk == 0x74
        assert loaded.haste_cooldown  == pytest.approx(14.0)


# ─────────────────────────────────────────────────────────────────────────────
# AutoHealer — utamo vita
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoHealerUtamo:

    def _cfg_with_utamo(self, mp_pct: int = 100) -> tuple:
        """Return (ctrl, healer) with utamo enabled, full MP."""
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            heal_hotkey_vk=0,
            mana_hotkey_vk=0,
            emergency_hotkey_vk=0,
            utamo_hotkey_vk=0x73,       # F4
            utamo_min_mp_pct=95,
            utamo_cooldown=0.0,         # no cooldown for tests
            haste_hotkey_vk=0,
            check_interval=0.01,
        )
        healer = AutoHealer(
            ctrl=ctrl,
            config=cfg,
            frame_getter=lambda: np.zeros((100, 100, 3), dtype=np.uint8),
            detector=_mock_detector(100.0, float(mp_pct)),
        )
        healer.set_log_callback(lambda msg: None)
        return ctrl, healer

    def test_casts_when_mp_sufficient(self):
        ctrl, healer = self._cfg_with_utamo(mp_pct=100)
        healer._tick()
        ctrl.press_key.assert_called_with(0x73)

    def test_no_cast_when_mp_too_low(self):
        ctrl, healer = self._cfg_with_utamo(mp_pct=80)   # below 95%
        healer._tick()
        ctrl.press_key.assert_not_called()

    def test_no_cast_when_disabled(self):
        """haste_hotkey_vk=0 → disabled."""
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            heal_hotkey_vk=0,
            mana_hotkey_vk=0,
            emergency_hotkey_vk=0,
            utamo_hotkey_vk=0,
            haste_hotkey_vk=0,
            check_interval=0.01,
        )
        healer = AutoHealer(
            ctrl=ctrl,
            config=cfg,
            frame_getter=lambda: np.zeros((100, 100, 3), dtype=np.uint8),
            detector=_mock_detector(100.0, 100.0),
        )
        healer.set_log_callback(lambda msg: None)
        healer._tick()
        ctrl.press_key.assert_not_called()

    def test_utamo_cast_counter(self):
        ctrl, healer = self._cfg_with_utamo(mp_pct=100)
        assert healer.utamo_casts == 0
        healer._tick()
        assert healer.utamo_casts == 1
        healer._tick()
        assert healer.utamo_casts == 2

    def test_utamo_cooldown_prevents_recast(self):
        ctrl, healer = self._cfg_with_utamo(mp_pct=100)
        # Set a real cooldown
        healer._cfg.utamo_cooldown = 30.0
        healer._tick()
        assert ctrl.press_key.call_count == 1
        healer._tick()
        # Cooldown not elapsed → no second cast
        assert ctrl.press_key.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# AutoHealer — Auto Hur
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoHealerHaste:

    def _cfg_with_haste(self) -> tuple:
        ctrl = _mock_ctrl()
        cfg = HealConfig(
            heal_hotkey_vk=0,
            mana_hotkey_vk=0,
            emergency_hotkey_vk=0,
            utamo_hotkey_vk=0,
            haste_hotkey_vk=0x74,   # F5
            haste_cooldown=0.0,
            check_interval=0.01,
        )
        healer = AutoHealer(
            ctrl=ctrl,
            config=cfg,
            frame_getter=lambda: np.zeros((100, 100, 3), dtype=np.uint8),
            detector=_mock_detector(100.0, 100.0),
        )
        healer.set_log_callback(lambda msg: None)
        return ctrl, healer

    def test_casts_when_no_haste_and_no_battle(self):
        ctrl, healer = self._cfg_with_haste()
        healer.set_conditions_getter(lambda: set())
        healer._tick()
        ctrl.press_key.assert_called_with(0x74)

    def test_suppressed_when_haste_active(self):
        ctrl, healer = self._cfg_with_haste()
        healer.set_conditions_getter(lambda: {"haste"})
        healer._tick()
        ctrl.press_key.assert_not_called()

    def test_suppressed_during_combat(self):
        ctrl, healer = self._cfg_with_haste()
        healer.set_conditions_getter(lambda: {"battle"})
        healer._tick()
        ctrl.press_key.assert_not_called()

    def test_haste_cast_counter(self):
        ctrl, healer = self._cfg_with_haste()
        healer.set_conditions_getter(lambda: set())
        assert healer.haste_casts == 0
        healer._tick()
        assert healer.haste_casts == 1

    def test_conditions_getter_exception_treated_as_empty(self):
        """Errors from conditions_getter should not crash — returns empty set."""
        ctrl, healer = self._cfg_with_haste()

        def _bad_getter() -> Set[str]:
            raise RuntimeError("detector failure")

        healer.set_conditions_getter(_bad_getter)
        # Should not raise; haste is cast (empty set = no haste/battle)
        healer._tick()
        ctrl.press_key.assert_called_with(0x74)

    def test_cooldown_prevents_recast(self):
        ctrl, healer = self._cfg_with_haste()
        healer._cfg.haste_cooldown = 30.0
        healer.set_conditions_getter(lambda: set())
        healer._tick()
        assert ctrl.press_key.call_count == 1
        healer._tick()
        assert ctrl.press_key.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# FriendHealConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestFriendHealConfig:

    def test_defaults(self):
        cfg = FriendHealConfig()
        assert cfg.sio_threshold_pct      == 70
        assert cfg.gran_sio_threshold_pct == 40
        assert cfg.sio_hotkey_vk          == 0
        assert cfg.gran_sio_hotkey_vk     == 0
        assert cfg.sio_cooldown           == pytest.approx(1.5)
        assert cfg.gran_sio_cooldown      == pytest.approx(1.5)

    def test_custom_config(self):
        cfg = FriendHealConfig(
            sio_threshold_pct=80,
            gran_sio_threshold_pct=30,
            sio_hotkey_vk=0x73,
            gran_sio_hotkey_vk=0x74,
        )
        assert cfg.sio_threshold_pct == 80
        assert cfg.gran_sio_hotkey_vk == 0x74


# ─────────────────────────────────────────────────────────────────────────────
# FriendHealer — _tick logic
# ─────────────────────────────────────────────────────────────────────────────

class TestFriendHealer:

    def _make(self, hp: float, sio_vk: int = 0x73, gran_vk: int = 0x74) -> tuple:
        ctrl = _mock_ctrl()
        cfg = FriendHealConfig(
            sio_threshold_pct=70,
            gran_sio_threshold_pct=40,
            sio_hotkey_vk=sio_vk,
            gran_sio_hotkey_vk=gran_vk,
            sio_cooldown=0.0,
            gran_sio_cooldown=0.0,
        )
        healer = FriendHealer(ctrl, cfg)
        healer.set_log_callback(lambda msg: None)
        healer.set_friend_hp_getter(lambda: hp)
        return ctrl, healer

    def test_no_cast_when_hp_full(self):
        ctrl, h = self._make(hp=100.0)
        h._tick()
        ctrl.press_key.assert_not_called()

    def test_sio_cast_when_hp_below_threshold(self):
        ctrl, h = self._make(hp=60.0)  # below 70
        h._tick()
        ctrl.press_key.assert_called_once_with(0x73)

    def test_gran_sio_priority_at_critical_hp(self):
        ctrl, h = self._make(hp=30.0)  # below both 70 and 40
        h._tick()
        # Gran Sio must be cast (priority), NOT regular Sio
        ctrl.press_key.assert_called_once_with(0x74)

    def test_gran_sio_cast_counter(self):
        ctrl, h = self._make(hp=20.0)
        assert h.gran_sio_casts == 0
        h._tick()
        assert h.gran_sio_casts == 1
        assert h.sio_casts == 0

    def test_sio_cast_counter(self):
        ctrl, h = self._make(hp=60.0)
        assert h.sio_casts == 0
        h._tick()
        assert h.sio_casts == 1

    def test_sio_cooldown_prevents_recast(self):
        ctrl, h = self._make(hp=60.0)
        h._cfg.sio_cooldown = 30.0
        h._tick()
        assert ctrl.press_key.call_count == 1
        h._tick()
        assert ctrl.press_key.call_count == 1

    def test_no_cast_without_getter(self):
        ctrl = _mock_ctrl()
        cfg = FriendHealConfig(sio_hotkey_vk=0x73)
        h = FriendHealer(ctrl, cfg)
        h.set_log_callback(lambda msg: None)
        # No getter registered
        h._tick()
        ctrl.press_key.assert_not_called()

    def test_reset_counts(self):
        ctrl, h = self._make(hp=20.0)
        h._tick()
        h._tick()
        assert h.gran_sio_casts == 2
        h.reset_counts()
        assert h.gran_sio_casts == 0
        assert h.sio_casts == 0

    def test_disabled_hotkeys_no_cast(self):
        ctrl, h = self._make(hp=20.0, sio_vk=0, gran_vk=0)
        h._tick()
        ctrl.press_key.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# FriendHealer — thread lifecycle (smoke test)
# ─────────────────────────────────────────────────────────────────────────────

class TestFriendHealerThread:

    def test_start_stop(self):
        ctrl = _mock_ctrl()
        cfg = FriendHealConfig(check_interval=0.01)
        h = FriendHealer(ctrl, cfg)
        h.set_log_callback(lambda msg: None)
        h.set_friend_hp_getter(lambda: 100.0)  # HP fine — no cast
        h.start()
        assert h._running is True
        time.sleep(0.05)
        h.stop()
        assert h._running is False

    def test_double_start_is_safe(self):
        ctrl = _mock_ctrl()
        h = FriendHealer(ctrl, FriendHealConfig(check_interval=0.05))
        h.set_log_callback(lambda msg: None)
        h.start()
        h.start()   # second call should be a no-op
        h.stop()
