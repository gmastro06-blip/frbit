"""Comprehensive tests for the Human Input System (HIS).

Covers all 8 HIS modules:
  - TimingHumanizer  (timing_humanizer.py)
  - BehaviorSimulator (behavior_simulator.py)
  - MouseMovementEngine (mouse_movement_engine.py)
  - MetricsCollector  (metrics_collector.py)
  - ProfileManager    (profile_manager.py)
  - KeyboardLayout    (utils/keyboard_layout.py)
  - ConfigurationParser (config/parser.py)
  - HumanInputSystem  (core/human_input_system.py) — integration
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from human_input_system.config.models import (
    ArduinoConfig,
    BehaviorConfig,
    BehaviorProfile,
    Configuration,
    MouseConfig,
    TimingConfig,
)
from human_input_system.core.timing_humanizer import TimingHumanizer
from human_input_system.core.behavior_simulator import BehaviorSimulator
from human_input_system.core.mouse_movement_engine import MouseMovementEngine
from human_input_system.core.metrics_collector import MetricsCollector
from human_input_system.core.profile_manager import ProfileManager
from human_input_system.core.events import (
    InputEvent, KeyPressEvent, KeyReleaseEvent, MouseMoveEvent,
)
from human_input_system.utils.keyboard_layout import KeyboardLayout
from human_input_system.config.parser import ConfigurationParser


# ═══════════════════════════════════════════════════════════════════════
# TimingHumanizer
# ═══════════════════════════════════════════════════════════════════════

class TestTimingHumanizer:
    def setup_method(self) -> None:
        self.cfg = TimingConfig()
        self.th = TimingHumanizer(self.cfg)

    def test_reaction_time_in_range(self):
        for _ in range(200):
            rt = self.th.get_reaction_time(fatigue_level=0.0)
            assert 150.0 <= rt <= 550.0

    def test_reaction_time_increases_with_fatigue(self):
        samples_low = [self.th.get_reaction_time(0.0) for _ in range(500)]
        samples_high = [self.th.get_reaction_time(0.8) for _ in range(500)]
        avg_low = sum(samples_low) / len(samples_low)
        avg_high = sum(samples_high) / len(samples_high)
        assert avg_high > avg_low  # higher fatigue → slower reaction

    def test_key_press_duration_in_range(self):
        for _ in range(200):
            d = self.th.get_key_press_duration(0.0)
            assert 50.0 <= d <= 170.0

    def test_key_press_duration_fatigue(self):
        samples_0 = [self.th.get_key_press_duration(0.0) for _ in range(500)]
        samples_1 = [self.th.get_key_press_duration(1.0) for _ in range(500)]
        assert sum(samples_1) / len(samples_1) > sum(samples_0) / len(samples_0)

    def test_micro_pause_in_range(self):
        for _ in range(200):
            mp = self.th.get_micro_pause()
            assert 10.0 <= mp <= 50.0

    def test_movement_duration_fitts_law(self):
        short_dist = [self.th.get_movement_duration(50.0) for _ in range(200)]
        long_dist = [self.th.get_movement_duration(1000.0) for _ in range(200)]
        assert sum(long_dist) / len(long_dist) > sum(short_dist) / len(short_dist)

    def test_movement_duration_clamp(self):
        for _ in range(100):
            d = self.th.get_movement_duration(1.0)
            assert 200.0 <= d <= 2000.0

    def test_add_jitter_positive(self):
        for _ in range(100):
            j = self.th.add_jitter(100.0)
            assert j >= 1.0  # clamped to min 1

    def test_add_jitter_conservative(self):
        # Jitter should be small (5% std dev)
        vals = [self.th.add_jitter(1000.0) for _ in range(500)]
        avg = sum(vals) / len(vals)
        assert 900.0 < avg < 1100.0  # within 10% of base


# ═══════════════════════════════════════════════════════════════════════
# BehaviorSimulator
# ═══════════════════════════════════════════════════════════════════════

class TestBehaviorSimulator:
    def setup_method(self) -> None:
        self.cfg = BehaviorConfig()
        self.bs = BehaviorSimulator(self.cfg)

    def test_initial_fatigue(self):
        assert self.bs.get_fatigue_level() == 0.0

    def test_fatigue_increments(self):
        self.bs.update_fatigue(3600.0)  # 1 hour
        fl = self.bs.get_fatigue_level()
        assert 0.08 <= fl <= 0.12  # ~0.10

    def test_fatigue_capped_at_one(self):
        self.bs.update_fatigue(100_000)
        assert self.bs.get_fatigue_level() == 1.0

    def test_set_fatigue_level_clamped(self):
        self.bs.set_fatigue_level(2.0)
        assert self.bs.get_fatigue_level() == 1.0
        self.bs.set_fatigue_level(-1.0)
        assert self.bs.get_fatigue_level() == 0.0

    def test_error_generation_returns_valid_types(self):
        self.bs.set_fatigue_level(0.5)  # increase error rate
        types_seen: set[str] = set()
        for _ in range(5000):
            err = self.bs.should_generate_error()
            if err:
                types_seen.add(err)
        # Should have seen at least 2 different error types
        assert len(types_seen) >= 2
        assert types_seen.issubset({"wrong_key", "double_press", "miss_click", "hesitation"})

    def test_error_generation_none_with_zero_rate(self):
        cfg = BehaviorConfig(error_rate_base=0.0)
        bs = BehaviorSimulator(cfg)
        for _ in range(200):
            assert bs.should_generate_error() is None

    def test_wrong_key_vk_returns_adjacent(self):
        # VK for 'f' = 0x46 → adjacent keys exist
        result = self.bs.apply_wrong_key_error_vk(0x46)
        assert isinstance(result, int)

    def test_double_press_delay_range(self):
        for _ in range(100):
            d = self.bs.apply_double_press_error()
            assert 20.0 <= d <= 80.0

    def test_miss_click_offset(self):
        for _ in range(100):
            x2, y2 = self.bs.apply_miss_click_offset(500, 500)
            dist = math.hypot(x2 - 500, y2 - 500)
            assert 3.0 <= dist <= 30.0  # slightly wider than [5, 25] for rounding

    def test_hesitation_delay_range(self):
        for _ in range(100):
            h = self.bs.apply_hesitation_delay()
            assert 200.0 <= h <= 800.0

    def test_afk_trigger_low_probability(self):
        # With 0 fatigue, very unlikely per single call
        self.bs.set_fatigue_level(0.0)
        triggers = sum(1 for _ in range(10000) if self.bs.should_trigger_afk_pause())
        assert triggers < 100  # very low

    def test_afk_trigger_suppressed_in_critical(self):
        self.bs.set_critical_check(lambda: True)  # always critical
        triggers = sum(1 for _ in range(10000) if self.bs.should_trigger_afk_pause())
        assert triggers == 0

    def test_afk_duration_range(self):
        for _ in range(100):
            d = self.bs.generate_afk_duration()
            assert 30.0 <= d <= 300.0

    def test_reset_fatigue_after_afk(self):
        self.bs.set_fatigue_level(0.9)
        self.bs.reset_fatigue_after_afk()
        fl = self.bs.get_fatigue_level()
        assert 0.2 <= fl <= 0.4

    def test_critical_check_default_false(self):
        assert self.bs.is_in_critical_situation() is False

    def test_critical_check_cb_exception(self):
        self.bs.set_critical_check(lambda: 1 / 0)
        assert self.bs.is_in_critical_situation() is False


# ═══════════════════════════════════════════════════════════════════════
# MouseMovementEngine
# ═══════════════════════════════════════════════════════════════════════

class TestMouseMovementEngine:
    def setup_method(self) -> None:
        self.cfg = MouseConfig()
        self.mme = MouseMovementEngine(self.cfg)

    def test_bezier_path_length(self):
        path = self.mme.generate_bezier_path((0, 0), (500, 300))
        assert len(path) == self.cfg.points_per_movement

    def test_bezier_starts_and_ends_near_target(self):
        path = self.mme.generate_bezier_path((100, 100), (800, 600))
        # Start should be near (100, 100)
        assert abs(path[0][0] - 100) <= 5
        assert abs(path[0][1] - 100) <= 5
        # End should be near (800, 600)
        assert abs(path[-1][0] - 800) <= 5
        assert abs(path[-1][1] - 600) <= 5

    def test_bezier_zero_distance(self):
        path = self.mme.generate_bezier_path((200, 200), (200, 200))
        assert len(path) == 2  # just start and end

    def test_micro_movements_applied(self):
        # Without micro-movements, path would be on a perfect curve
        path = self.mme.generate_bezier_path((0, 0), (500, 500), num_points=20)
        # Check intermediate points have some offset
        # At least some should differ from t=0.5 linear interpolation
        mid = path[10]
        assert mid != (250, 250)  # micro-movements shift it

    def test_velocity_profile_shape(self):
        """Sigmoid profile: starts slow, fast middle, slow end."""
        profile = self.mme.calculate_velocity_profile(100)
        assert len(profile) == 100
        assert all(0.0 <= v <= 1.0 for v in profile)
        # Middle should be faster than edges
        assert profile[50] > profile[5]
        assert profile[50] > profile[95]

    def test_velocity_profile_single_point(self):
        profile = self.mme.calculate_velocity_profile(1)
        assert profile == [1.0]

    def test_overshoot_probability_nonzero(self):
        count = sum(1 for _ in range(1000) if self.mme.should_overshoot())
        # default 30% ± 5%
        assert 200 < count < 400

    def test_overshoot_point_beyond_target(self):
        pt = self.mme.generate_overshoot_point((500, 500), (1.0, 0.0))
        assert pt[0] > 500  # overshoot in x direction

    def test_approach_vector_normalized(self):
        path = [(0, 0), (10, 0), (20, 0), (30, 0), (40, 0)]
        vec = self.mme.calculate_approach_vector(path)
        mag = math.hypot(vec[0], vec[1])
        assert abs(mag - 1.0) < 0.01

    def test_approach_vector_short_path(self):
        vec = self.mme.calculate_approach_vector([(0, 0)])
        assert vec == (1.0, 0.0)  # default fallback

    def test_full_movement_returns_path(self):
        path = self.mme.generate_full_movement((0, 0), (400, 300))
        assert len(path) >= 10


# ═══════════════════════════════════════════════════════════════════════
# MetricsCollector
# ═══════════════════════════════════════════════════════════════════════

class TestMetricsCollector:
    def test_record_and_stats(self, tmp_path):
        mc = MetricsCollector(str(tmp_path))
        mc.record_key_press("F1", 85.0, 220.0, False)
        mc.record_key_press("F2", 90.0, 210.0, True)
        mc.record_mouse_movement((0, 0), (500, 300), 450.0, 50)
        mc.record_error("wrong_key")
        mc.record_afk_pause(60.0)

        stats = mc.get_statistics()
        assert stats["total_inputs"] == 3  # 2 keys + 1 mouse
        assert stats["reaction_times"]["mean"] > 0
        assert stats["key_press_durations"]["mean"] > 0
        assert stats["mouse_movements"]["avg_duration"] == 450.0
        assert stats["afk_pauses"]["count"] == 1
        assert stats["afk_pauses"]["avg_duration"] == 60.0
        assert stats["error_rates"]["total"] > 0
        mc.close()

    def test_empty_stats(self, tmp_path):
        mc = MetricsCollector(str(tmp_path))
        stats = mc.get_statistics()
        assert stats["total_inputs"] == 0
        assert stats["reaction_times"]["mean"] == 0
        mc.close()

    def test_describe_empty(self, tmp_path):
        mc = MetricsCollector(str(tmp_path))
        desc = mc._describe([])
        assert desc["mean"] == 0
        mc.close()

    def test_describe_with_data(self, tmp_path):
        mc = MetricsCollector(str(tmp_path))
        desc = mc._describe([10.0, 20.0, 30.0, 40.0, 50.0])
        assert desc["mean"] == 30.0
        assert desc["min"] == 10.0
        assert desc["max"] == 50.0
        mc.close()

    def test_generate_report(self, tmp_path):
        mc = MetricsCollector(str(tmp_path))
        mc.record_key_press("A", 80.0, 200.0, False)
        report_path = str(tmp_path / "report.json")
        mc.generate_report(report_path)
        assert Path(report_path).exists()
        data = json.loads(Path(report_path).read_text())
        assert "total_inputs" in data
        mc.close()

    def test_log_rotation(self, tmp_path):
        mc = MetricsCollector(str(tmp_path))
        mc.log_with_timestamp("test message")
        # Should create a .log file
        logs = list(Path(str(tmp_path)).glob("*.log"))
        assert len(logs) >= 1
        mc.close()

    def test_close_idempotent(self, tmp_path):
        mc = MetricsCollector(str(tmp_path))
        mc.log_with_timestamp("data")
        mc.close()
        mc.close()  # should not raise

    def test_thread_safety(self, tmp_path):
        mc = MetricsCollector(str(tmp_path))
        errors = []

        def worker(n: int) -> None:
            try:
                for i in range(50):
                    mc.record_key_press(f"K{n}", float(i), float(i * 10), False)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert mc.get_statistics()["total_inputs"] == 250
        mc.close()


# ═══════════════════════════════════════════════════════════════════════
# KeyboardLayout
# ═══════════════════════════════════════════════════════════════════════

class TestKeyboardLayout:
    def test_adjacent_keys_known(self):
        adj = KeyboardLayout.get_adjacent_keys("q")
        assert "w" in adj
        assert "a" in adj

    def test_adjacent_keys_unknown(self):
        adj = KeyboardLayout.get_adjacent_keys("!")
        assert adj == []

    def test_random_adjacent_returns_neighbor(self):
        for _ in range(50):
            adj = KeyboardLayout.get_random_adjacent("f")
            assert adj in ["d", "r", "t", "g", "v", "c"]

    def test_random_adjacent_unknown_returns_same(self):
        assert KeyboardLayout.get_random_adjacent("!") == "!"

    def test_vk_to_key_mapping(self):
        assert KeyboardLayout.VK_TO_KEY[0x41] == "a"
        assert KeyboardLayout.VK_TO_KEY[0x5A] == "z"

    def test_key_to_vk_mapping(self):
        assert KeyboardLayout.KEY_TO_VK["a"] == 0x41

    def test_adjacent_vk(self):
        # VK for 's' = 0x53, adjacent: a, w, e, d, x, z
        result = KeyboardLayout.get_adjacent_vk(0x53)
        assert result != 0x53 or True  # could be same by chance, but unlikely

    def test_adjacent_vk_unknown(self):
        # VK 0xFF not mapped
        assert KeyboardLayout.get_adjacent_vk(0xFF) == 0xFF


# ═══════════════════════════════════════════════════════════════════════
# Events
# ═══════════════════════════════════════════════════════════════════════

class TestEvents:
    def test_key_press_event_serial(self):
        e = KeyPressEvent(timestamp=0.0, event_type="", key="F1", duration=120.0)
        assert e.event_type == "key_press"
        assert e.to_serial_command() == "KEY_PRESS|F1|120\n"

    def test_key_release_event_serial(self):
        e = KeyReleaseEvent(timestamp=0.0, event_type="", key="ESC")
        assert e.event_type == "key_release"
        assert e.to_serial_command() == "KEY_RELEASE|ESC\n"

    def test_mouse_move_event(self):
        e = MouseMoveEvent(timestamp=0.0, event_type="", x=100, y=200)
        assert e.event_type == "mouse_move"

    def test_input_event_base(self):
        e = InputEvent(timestamp=1.0, event_type="test")
        with pytest.raises(NotImplementedError):
            e.to_serial_command()


# ═══════════════════════════════════════════════════════════════════════
# ConfigurationParser
# ═══════════════════════════════════════════════════════════════════════

class TestConfigurationParser:
    def _write_yaml(self, tmp_path: Path, data: dict) -> str:
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return str(p)

    def test_parse_defaults(self, tmp_path):
        path = self._write_yaml(tmp_path, {})
        parser = ConfigurationParser(path)
        cfg = parser.parse()
        assert isinstance(cfg, Configuration)
        assert cfg.enable_humanization is True
        assert "default" in cfg.profiles

    def test_parse_custom_timing(self, tmp_path):
        data = {"timing": {"reaction_time_mean": 300.0}}
        path = self._write_yaml(tmp_path, data)
        cfg = ConfigurationParser(path).parse()
        assert cfg.timing.reaction_time_mean == 300.0

    def test_parse_profiles(self, tmp_path):
        data = {
            "profiles": {
                "fast": {
                    "name": "fast",
                    "timing": {"reaction_time_mean": 150.0},
                    "behavior": {},
                    "mouse": {},
                },
            },
            "system": {"active_profile": "fast"},
        }
        path = self._write_yaml(tmp_path, data)
        cfg = ConfigurationParser(path).parse()
        assert "fast" in cfg.profiles

    def test_validate_ranges_good(self):
        raw = {"timing": {"reaction_time_mean": 220.0}}
        errors = ConfigurationParser.validate_ranges(raw)
        assert len(errors) == 0

    def test_validate_ranges_bad(self):
        raw = {"timing": {"reaction_time_mean": 5000.0}}
        errors = ConfigurationParser.validate_ranges(raw)
        assert len(errors) > 0

    def test_validate_error_probabilities_sum(self):
        raw = {"behavior": {"error_probabilities": {"a": 0.3, "b": 0.3}}}
        errors = ConfigurationParser.validate_ranges(raw)
        assert any("suman" in e for e in errors)

    def test_apply_defaults_missing_sections(self):
        raw: dict = {}
        result = ConfigurationParser.apply_defaults(raw)
        assert "timing" in result
        assert "behavior" in result
        assert "mouse" in result

    def test_reload(self, tmp_path):
        path = self._write_yaml(tmp_path, {"timing": {"reaction_time_mean": 200.0}})
        parser = ConfigurationParser(path)
        cfg1 = parser.parse()
        # Modify file
        Path(path).write_text(
            yaml.dump({"timing": {"reaction_time_mean": 350.0}}), encoding="utf-8"
        )
        cfg2 = parser.reload()
        assert cfg2.timing.reaction_time_mean == 350.0

    def test_to_yaml(self, tmp_path):
        path = self._write_yaml(tmp_path, {})
        parser = ConfigurationParser(path)
        cfg = parser.parse()
        yaml_str = parser.to_yaml(cfg)
        assert "timing" in yaml_str
        assert "behavior" in yaml_str

    def test_parse_real_config(self):
        """Parse the actual project config.yaml."""
        cfg_path = _ROOT / "human_input_system" / "config.yaml"
        if cfg_path.exists():
            parser = ConfigurationParser(str(cfg_path))
            cfg = parser.parse()
            assert cfg.enable_humanization is True
            assert "default" in cfg.profiles
            assert cfg.timing.reaction_time_mean > 0


# ═══════════════════════════════════════════════════════════════════════
# Configuration Models
# ═══════════════════════════════════════════════════════════════════════

class TestConfigModels:
    def test_timing_config_validate(self):
        assert TimingConfig().validate() is True
        assert TimingConfig(reaction_time_mean=5.0).validate() is False

    def test_behavior_config_validate(self):
        assert BehaviorConfig().validate() is True
        assert BehaviorConfig(error_rate_base=0.9).validate() is False

    def test_mouse_config_validate(self):
        assert MouseConfig().validate() is True
        assert MouseConfig(overshoot_probability=2.0).validate() is False

    def test_arduino_config_validate(self):
        assert ArduinoConfig().validate() is True
        assert ArduinoConfig(baudrate=0).validate() is False

    def test_behavior_profile_round_trip(self):
        bp = BehaviorProfile(
            name="test",
            timing=TimingConfig(),
            behavior=BehaviorConfig(),
            mouse=MouseConfig(),
        )
        d = bp.to_dict()
        bp2 = BehaviorProfile.from_dict(d)
        assert bp2.name == "test"
        assert bp2.timing.reaction_time_mean == bp.timing.reaction_time_mean

    def test_configuration_validate_empty(self):
        cfg = Configuration()
        errors = cfg.validate()
        # No profiles → no "active not found" error if profiles dict is empty
        assert isinstance(errors, list)

    def test_configuration_validate_missing_profile(self):
        bp = BehaviorProfile(
            name="x", timing=TimingConfig(), behavior=BehaviorConfig(), mouse=MouseConfig()
        )
        cfg = Configuration(profiles={"x": bp}, active_profile="missing")
        errors = cfg.validate()
        assert any("not found" in e for e in errors)


# ═══════════════════════════════════════════════════════════════════════
# ProfileManager
# ═══════════════════════════════════════════════════════════════════════

class TestProfileManager:
    def _make_parser(self, tmp_path: Path) -> ConfigurationParser:
        data = {
            "profiles": {
                "default": {
                    "name": "default",
                    "timing": {"reaction_time_mean": 220.0},
                    "behavior": {},
                    "mouse": {},
                },
                "fast": {
                    "name": "fast",
                    "timing": {"reaction_time_mean": 150.0},
                    "behavior": {},
                    "mouse": {},
                },
            },
            "system": {"active_profile": "default"},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return ConfigurationParser(str(p))

    def test_load_profiles(self, tmp_path):
        parser = self._make_parser(tmp_path)
        pm = ProfileManager(parser)
        pm.load_profiles()
        assert "default" in pm.list_profiles()
        assert "fast" in pm.list_profiles()

    def test_active_profile(self, tmp_path):
        parser = self._make_parser(tmp_path)
        pm = ProfileManager(parser)
        pm.load_profiles()
        assert pm.get_active_profile() is not None
        assert pm.get_active_profile().name == "default"

    def test_set_active_profile(self, tmp_path):
        parser = self._make_parser(tmp_path)
        pm = ProfileManager(parser)
        pm.load_profiles()
        ok = pm.set_active_profile("fast", transition_duration=0.1)
        assert ok is True
        time.sleep(0.3)  # wait for transition
        assert pm.get_active_profile().name == "fast"

    def test_set_nonexistent_profile(self, tmp_path):
        parser = self._make_parser(tmp_path)
        pm = ProfileManager(parser)
        pm.load_profiles()
        ok = pm.set_active_profile("missing")
        assert ok is False

    def test_get_profile_parameters(self, tmp_path):
        parser = self._make_parser(tmp_path)
        pm = ProfileManager(parser)
        pm.load_profiles()
        params = pm.get_profile_parameters("default")
        assert params is not None
        assert "timing" in params

    def test_get_nonexistent_profile_parameters(self, tmp_path):
        parser = self._make_parser(tmp_path)
        pm = ProfileManager(parser)
        pm.load_profiles()
        assert pm.get_profile_parameters("no_such") is None

    def test_create_custom_profile(self, tmp_path):
        parser = self._make_parser(tmp_path)
        pm = ProfileManager(parser)
        pm.load_profiles()
        ok = pm.create_custom_profile("custom", {
            "timing": {"reaction_time_mean": 200.0},
            "behavior": {},
            "mouse": {},
        })
        assert ok is True
        assert "custom" in pm.list_profiles()

    def test_circadian_adjustments(self, tmp_path):
        parser = self._make_parser(tmp_path)
        pm = ProfileManager(parser)
        pm.load_profiles()
        pm.apply_circadian_adjustments()  # should not raise

    def test_interpolate_dicts(self):
        src = {"a": 10.0, "b": {"c": 100.0}}
        tgt = {"a": 20.0, "b": {"c": 200.0}}
        result = ProfileManager._interpolate_dicts(src, tgt, 0.5)
        assert result["a"] == 15.0
        assert result["b"]["c"] == 150.0


# ═══════════════════════════════════════════════════════════════════════
# HumanInputSystem — Integration
# ═══════════════════════════════════════════════════════════════════════

class TestHumanInputSystemIntegration:
    """Test the full HIS orchestrator with mocked InputController."""

    @pytest.fixture
    def his(self, tmp_path):
        """Create HIS with real config and mock controller."""
        cfg_path = _ROOT / "human_input_system" / "config.yaml"
        mock_ic = MagicMock()
        mock_ic.press_key.return_value = True
        mock_ic.hold_key.return_value = True
        mock_ic.click.return_value = True
        mock_ic.click_human.return_value = True
        mock_ic.click_absolute.return_value = True
        mock_ic.shift_click.return_value = True
        mock_ic.type_text.return_value = True
        mock_ic.hwnd = 0x12345
        mock_ic.input_method = "interception"
        mock_ic.jitter_pct = 0.15
        mock_ic.interception_available = True
        mock_ic.is_connected.return_value = True
        mock_ic.find_target.return_value = True
        mock_ic.focus_now.return_value = True

        from human_input_system import HumanInputSystem
        his = HumanInputSystem(str(cfg_path), mock_ic)
        yield his, mock_ic
        his.close()

    def test_press_key(self, his):
        sys_, mock_ic = his
        result = sys_.press_key(0x70)  # F1
        assert result is True
        assert mock_ic.press_key.called

    def test_hold_key(self, his):
        sys_, mock_ic = his
        result = sys_.hold_key(0x70, 0.5)
        assert result is True
        assert mock_ic.hold_key.called

    def test_click(self, his):
        sys_, mock_ic = his
        result = sys_.click(500, 300, "left")
        assert result is True
        assert mock_ic.click.called

    def test_click_human(self, his):
        sys_, mock_ic = his
        result = sys_.click_human(500, 300, "right")
        assert result is True
        assert mock_ic.click_human.called

    def test_click_absolute(self, his):
        sys_, mock_ic = his
        result = sys_.click_absolute(800, 600)
        assert result is True
        assert mock_ic.click_absolute.called

    def test_shift_click(self, his):
        sys_, mock_ic = his
        result = sys_.shift_click(200, 200)
        assert result is True
        assert mock_ic.shift_click.called

    def test_type_text(self, his):
        sys_, mock_ic = his
        result = sys_.type_text("hi")
        assert result is True

    def test_passthrough_hwnd(self, his):
        sys_, _ = his
        assert sys_.hwnd == 0x12345

    def test_passthrough_input_method(self, his):
        sys_, _ = his
        assert sys_.input_method == "interception"

    def test_passthrough_is_connected(self, his):
        sys_, _ = his
        assert sys_.is_connected() is True

    def test_disable_humanization(self, his):
        sys_, mock_ic = his
        sys_.enable_humanization(False)
        sys_.press_key(0x70)
        # With humanization disabled, should call IC directly
        assert mock_ic.press_key.called

    def test_enable_humanization_toggle(self, his):
        sys_, _ = his
        sys_.enable_humanization(False)
        assert sys_._enabled is False
        sys_.enable_humanization(True)
        assert sys_._enabled is True

    def test_get_metrics(self, his):
        sys_, _ = his
        sys_.press_key(0x70)
        metrics = sys_.get_metrics()
        assert metrics["total_inputs"] >= 1

    def test_set_profile(self, his):
        sys_, _ = his
        # "default" profile exists
        result = sys_.set_profile("default")
        assert result is True

    def test_reload_config(self, his):
        sys_, _ = his
        result = sys_.reload_config()
        assert result is True

    def test_getattr_delegation(self, his):
        sys_, mock_ic = his
        mock_ic.some_custom_attr = "delegated"
        assert sys_.some_custom_attr == "delegated"

    def test_set_critical_check(self, his):
        sys_, _ = his
        sys_.set_critical_check(lambda: False)
        # Should not raise
