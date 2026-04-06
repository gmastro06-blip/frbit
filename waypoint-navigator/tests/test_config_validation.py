"""
Tests for FIX-06 (JSON schema validation in configs) and
FIX-08 (GUI configuradora Tkinter).

FIX-06: validate() method on HealConfig, CombatConfig, SessionConfig.
FIX-08: MonitorGui exposes a "⚙ Configurar" button and _open_config_window().
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# FIX-06 — HealConfig.validate()
# ---------------------------------------------------------------------------

class TestHealConfigValidation:

    def _cfg(self, **kwargs):
        from src.healer import HealConfig
        return HealConfig(**kwargs)

    def test_default_config_is_valid(self):
        from src.healer import HealConfig
        HealConfig().validate()   # must not raise

    def test_hp_threshold_too_high_raises(self):
        with pytest.raises(ValueError, match="hp_threshold_pct"):
            self._cfg(hp_threshold_pct=101).validate()

    def test_hp_threshold_negative_raises(self):
        with pytest.raises(ValueError, match="hp_threshold_pct"):
            self._cfg(hp_threshold_pct=-1).validate()

    def test_hp_emergency_out_of_range_raises(self):
        with pytest.raises(ValueError, match="hp_emergency_pct"):
            self._cfg(hp_emergency_pct=200).validate()

    def test_mp_threshold_out_of_range_raises(self):
        with pytest.raises(ValueError, match="mp_threshold_pct"):
            self._cfg(mp_threshold_pct=101).validate()

    def test_negative_heal_cooldown_raises(self):
        with pytest.raises(ValueError, match="heal_cooldown"):
            self._cfg(heal_cooldown=-0.1).validate()

    def test_negative_check_interval_raises(self):
        with pytest.raises(ValueError, match="check_interval"):
            self._cfg(check_interval=-1.0).validate()

    def test_vk_code_too_large_raises(self):
        with pytest.raises(ValueError, match="heal_hotkey_vk"):
            self._cfg(heal_hotkey_vk=0x10000).validate()

    def test_vk_code_zero_is_valid(self):
        # 0 = disabled
        self._cfg(heal_hotkey_vk=0).validate()

    def test_boundary_values_are_valid(self):
        self._cfg(hp_threshold_pct=0).validate()
        self._cfg(hp_threshold_pct=100).validate()
        self._cfg(heal_cooldown=0.0).validate()
        self._cfg(heal_hotkey_vk=0xFFFF).validate()

    def test_load_raises_on_bad_json_file(self, tmp_path):
        from src.healer import HealConfig
        p = tmp_path / "bad_heal.json"
        p.write_text(json.dumps({"hp_threshold_pct": 150}), encoding="utf-8")
        with pytest.raises(ValueError, match="hp_threshold_pct"):
            HealConfig.load(p)

    def test_load_returns_instance_on_valid_json(self, tmp_path):
        from src.healer import HealConfig
        p = tmp_path / "heal.json"
        HealConfig(hp_threshold_pct=60).save(p)
        loaded = HealConfig.load(p)
        assert loaded.hp_threshold_pct == 60

    def test_load_returns_default_when_file_missing(self, tmp_path):
        from src.healer import HealConfig
        p = tmp_path / "nonexistent.json"
        loaded = HealConfig.load(p)
        assert isinstance(loaded, HealConfig)


# ---------------------------------------------------------------------------
# FIX-06 — CombatConfig.validate()
# ---------------------------------------------------------------------------

class TestCombatConfigValidation:

    def _cfg(self, **kwargs):
        from src.combat_manager import CombatConfig
        return CombatConfig(**kwargs)

    def test_default_is_valid(self):
        from src.combat_manager import CombatConfig
        CombatConfig().validate()

    def test_confidence_too_high_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            self._cfg(confidence=1.5).validate()

    def test_confidence_negative_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            self._cfg(confidence=-0.1).validate()

    def test_ocr_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError, match="ocr_confidence"):
            self._cfg(ocr_confidence=2.0).validate()

    def test_hp_flee_pct_out_of_range_raises(self):
        with pytest.raises(ValueError, match="hp_flee_pct"):
            self._cfg(hp_flee_pct=101).validate()

    def test_battle_list_roi_wrong_length_raises(self):
        with pytest.raises(ValueError, match="battle_list_roi"):
            self._cfg(battle_list_roi=[1, 2, 3]).validate()

    def test_battle_list_roi_5_elements_raises(self):
        with pytest.raises(ValueError, match="battle_list_roi"):
            self._cfg(battle_list_roi=[1, 2, 3, 4, 5]).validate()

    def test_negative_check_interval_raises(self):
        with pytest.raises(ValueError, match="check_interval"):
            self._cfg(check_interval=-1.0).validate()

    def test_negative_skip_top_raises(self):
        with pytest.raises(ValueError, match="skip_top"):
            self._cfg(skip_top=-1).validate()

    def test_boundary_confidence_values_valid(self):
        self._cfg(confidence=0.0).validate()
        self._cfg(confidence=1.0).validate()

    def test_load_raises_on_bad_confidence(self, tmp_path):
        from src.combat_manager import CombatConfig
        p = tmp_path / "bad_combat.json"
        p.write_text(json.dumps({"confidence": 5.0}), encoding="utf-8")
        with pytest.raises(ValueError, match="confidence"):
            CombatConfig.load(p)

    def test_load_roundtrip(self, tmp_path):
        from src.combat_manager import CombatConfig
        p = tmp_path / "combat.json"
        CombatConfig(confidence=0.8).save(p)
        loaded = CombatConfig.load(p)
        assert abs(loaded.confidence - 0.8) < 1e-9


# ---------------------------------------------------------------------------
# FIX-06 — SessionConfig.validate()
# ---------------------------------------------------------------------------

class TestSessionConfigValidation:

    def _cfg(self, **kwargs):
        from src.session import SessionConfig
        return SessionConfig(**kwargs)

    def test_default_is_valid(self):
        from src.session import SessionConfig
        SessionConfig().validate()

    def test_heal_hp_pct_out_of_range_raises(self):
        with pytest.raises(ValueError, match="heal_hp_pct"):
            self._cfg(heal_hp_pct=101).validate()

    def test_heal_emergency_pct_negative_raises(self):
        with pytest.raises(ValueError, match="heal_emergency_pct"):
            self._cfg(heal_emergency_pct=-5).validate()

    def test_mana_threshold_out_of_range_raises(self):
        with pytest.raises(ValueError, match="mana_threshold_pct"):
            self._cfg(mana_threshold_pct=200).validate()

    def test_step_interval_zero_raises(self):
        with pytest.raises(ValueError, match="step_interval"):
            self._cfg(step_interval=0.0).validate()

    def test_step_interval_negative_raises(self):
        with pytest.raises(ValueError, match="step_interval"):
            self._cfg(step_interval=-1.0).validate()

    def test_start_delay_negative_raises(self):
        with pytest.raises(ValueError, match="start_delay"):
            self._cfg(start_delay=-0.5).validate()

    def test_watchdog_timeout_negative_raises(self):
        with pytest.raises(ValueError, match="watchdog_timeout"):
            self._cfg(watchdog_timeout=-1.0).validate()

    def test_jitter_pct_out_of_range_raises(self):
        with pytest.raises(ValueError, match="jitter_pct"):
            self._cfg(jitter_pct=1.1).validate()

    def test_boundary_values_valid(self):
        self._cfg(heal_hp_pct=0).validate()
        self._cfg(heal_hp_pct=100).validate()
        self._cfg(step_interval=0.01).validate()
        self._cfg(jitter_pct=0.0).validate()
        self._cfg(jitter_pct=1.0).validate()

    def test_load_raises_on_bad_step_interval(self, tmp_path):
        from src.session import SessionConfig
        p = tmp_path / "bad_session.json"
        p.write_text(json.dumps({"step_interval": 0.0}), encoding="utf-8")
        with pytest.raises(ValueError, match="step_interval"):
            SessionConfig.load(p)

    def test_load_roundtrip(self, tmp_path):
        from src.session import SessionConfig
        p = tmp_path / "session.json"
        SessionConfig(step_interval=0.6, loop_route=True).save(p)
        loaded = SessionConfig.load(p)
        assert abs(loaded.step_interval - 0.6) < 1e-9
        assert loaded.loop_route is True


# ---------------------------------------------------------------------------
# FIX-08 — MonitorGui "⚙ Configurar" button + _open_config_window()
# ---------------------------------------------------------------------------

def _mock_session_gui() -> MagicMock:
    s = MagicMock()
    s.event_bus = MagicMock()
    s.stats_snapshot.return_value = {
        "is_running": False, "routes_completed": 0,
        "heal_fired": 0, "mana_fired": 0,
        "loot_events": 0, "uptime_secs": 0,
    }
    from src.session import SessionConfig
    s.config = SessionConfig()
    return s


class TestMonitorGuiConfigButton:

    def test_btn_config_none_before_build(self):
        from src.monitor_gui import MonitorGui
        gui = MonitorGui(session=_mock_session_gui(), root=MagicMock())
        assert gui._btn_config is None

    def test_btn_config_set_after_build(self):
        from src.monitor_gui import MonitorGui
        gui = MonitorGui(session=_mock_session_gui(), root=MagicMock())
        gui.build()
        # After build the config button reference should be set
        assert gui._btn_config is not None

    def test_open_config_window_with_no_root_does_not_crash(self):
        """When _root is None _open_config_window returns silently."""
        from src.monitor_gui import MonitorGui
        gui = MonitorGui(session=_mock_session_gui(), root=MagicMock())
        gui._root = None
        # Should not raise
        gui._open_config_window()

    def test_open_config_window_creates_toplevel(self):
        """_open_config_window should call tk.Toplevel on the root mock."""
        from src.monitor_gui import MonitorGui

        mock_root = MagicMock()
        # Simulate tkinter being available but using mocks
        mock_tk = MagicMock()
        mock_ttk = MagicMock()

        gui = MonitorGui(session=_mock_session_gui(), root=mock_root)

        with patch.dict("sys.modules", {"tkinter": mock_tk, "tkinter.ttk": mock_ttk}):
            # Patch Toplevel inside the method's import
            import tkinter as real_tk
            with patch.object(real_tk, "Toplevel", return_value=MagicMock()) as mock_tl:
                gui._root = MagicMock()   # ensure _root is not None
                gui._open_config_window()
                mock_tl.assert_called_once()

    def test_default_geometry_updated_to_920(self):
        from src.monitor_gui import MonitorConfig
        # Geometry must accommodate the new Configurar button row
        h = int(MonitorConfig().geometry.split("x")[1])
        assert h >= 900, f"Expected height >= 900, got {h}"
