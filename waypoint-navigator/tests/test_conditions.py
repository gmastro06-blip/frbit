"""
Tests para src/condition_monitor.py — ConditionDetector (modo color).
Usa frames BGR sintéticos — sin OBS.
"""
from __future__ import annotations

import numpy as np
import pytest
import cv2

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from src.condition_monitor import (
    ConditionDetector, ConditionConfig, ConditionReaction, ConditionMonitor,
    _HSV_RANGES, _MIN_PIXELS,
)
from tests.conftest import COND_ROI, REF_W, REF_H, _blank_frame, _hsv2bgr


# ─────────────────────────────────────────────────────────────────────────────
# Fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def color_detector() -> ConditionDetector:
    cfg = ConditionConfig(
        condition_icons_roi=COND_ROI,
        detection_mode="color",
    )
    return ConditionDetector(cfg)


def _make_condition_frame(condition: str) -> np.ndarray:
    """Genera un frame con el color HSV de la condición en el ROI."""
    frame = _blank_frame()
    hmin, hmax, smin, smax, vmin, vmax = _HSV_RANGES[condition]
    # Usar el punto medio del rango HSV para garantizar detección
    h = (hmin + hmax) // 2
    s = (smin + smax) // 2
    v = (vmin + vmax) // 2
    bgr = _hsv2bgr(h, s, v)
    x, y, w, h_roi = COND_ROI
    # Pintar suficientes píxeles para superar _MIN_PIXELS
    n = max(_MIN_PIXELS.get(condition, 5) + 5, 15)
    frame[y : y + h_roi, x : x + n] = bgr
    return frame


# ─────────────────────────────────────────────────────────────────────────────
# Tests de detección individual
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionDetectorColor:

    def test_no_conditions_blank(self, color_detector: ConditionDetector,
                                  no_condition_frame: np.ndarray):
        active = color_detector.detect(no_condition_frame)
        assert len(active) == 0, f"No debería detectar nada en frame negro: {active}"

    @pytest.mark.parametrize("condition", ["poison", "paralyze", "burning",
                                            "drunk", "bleeding", "freezing"])
    def test_single_condition_detected(self, color_detector: ConditionDetector,
                                        condition: str):
        frame = _make_condition_frame(condition)
        active = color_detector.detect(frame)
        assert condition in active, (
            f"Condición '{condition}' no detectada. Detectadas: {active}"
        )

    def test_poison_frame_fixture(self, color_detector: ConditionDetector,
                                   poison_frame: np.ndarray):
        active = color_detector.detect(poison_frame)
        assert "poison" in active

    def test_paralyze_frame_fixture(self, color_detector: ConditionDetector,
                                     paralyze_frame: np.ndarray):
        active = color_detector.detect(paralyze_frame)
        assert "paralyze" in active

    def test_multiple_conditions(self, color_detector: ConditionDetector):
        """Dos condiciones simultáneas → ambas detectadas."""
        frame = _blank_frame()

        # Poison
        hp, sp, vp = [(_HSV_RANGES["poison"][0] + _HSV_RANGES["poison"][1]) // 2,
                       (_HSV_RANGES["poison"][2] + _HSV_RANGES["poison"][3]) // 2,
                       (_HSV_RANGES["poison"][4] + _HSV_RANGES["poison"][5]) // 2]
        bgr_p = _hsv2bgr(hp, sp, vp)
        x, y, w, h = COND_ROI
        frame[y : y + h, x : x + 20] = bgr_p

        # Freezing — en otra parte del mismo ROI
        hf = (_HSV_RANGES["freezing"][0] + _HSV_RANGES["freezing"][1]) // 2
        sf = (_HSV_RANGES["freezing"][2] + _HSV_RANGES["freezing"][3]) // 2
        vf = (_HSV_RANGES["freezing"][4] + _HSV_RANGES["freezing"][5]) // 2
        bgr_f = _hsv2bgr(hf, sf, vf)
        frame[y : y + h, x + 30 : x + 50] = bgr_f

        active = color_detector.detect(frame)
        assert "poison" in active
        assert "freezing" in active

    def test_empty_roi(self):
        """ROI de 0 píxeles → devuelve set vacío sin crash."""
        cfg = ConditionConfig(
            condition_icons_roi=[0, 0, 0, 0],
            detection_mode="color",
        )
        det = ConditionDetector(cfg)
        active = det.detect(_blank_frame())
        assert isinstance(active, set)
        assert len(active) == 0

    def test_detect_returns_set(self, color_detector: ConditionDetector,
                                 blank_frame: np.ndarray):
        result = color_detector.detect(blank_frame)
        assert isinstance(result, set)

    def test_color_outside_roi_not_detected(self, color_detector: ConditionDetector):
        """Píxeles de veneno fuera del ROI no deben activar la detección."""
        frame = _blank_frame()
        # Pintar en la esquina superior izquierda (fuera del ROI de condiciones)
        bgr = _hsv2bgr(60, 200, 180)
        frame[0:30, 0:50] = bgr
        active = color_detector.detect(frame)
        assert "poison" not in active, (
            f"Detectó veneno fuera del ROI: {active}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ConditionReaction
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionReaction:

    def test_label_defaults_to_condition_name(self):
        r = ConditionReaction(condition="poison")
        assert r.label == "poison"

    def test_custom_label_preserved(self):
        r = ConditionReaction(condition="poison", label="antídoto F2")
        assert r.label == "antídoto F2"

    def test_default_vk_is_zero(self):
        r = ConditionReaction(condition="paralyze")
        assert r.vk == 0

    def test_default_cooldown(self):
        r = ConditionReaction(condition="burning")
        assert r.cooldown == pytest.approx(2.5)

    def test_vk_stored(self):
        r = ConditionReaction(condition="freezing", vk=0x71)
        assert r.vk == 0x71


# ─────────────────────────────────────────────────────────────────────────────
# ConditionConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionConfig:

    def test_default_detection_mode(self):
        cfg = ConditionConfig()
        assert cfg.detection_mode == "color"

    def test_default_check_interval(self):
        cfg = ConditionConfig()
        assert cfg.check_interval == pytest.approx(0.5)

    def test_default_ref_dimensions(self):
        cfg = ConditionConfig()
        assert cfg.ref_width  == 1920
        assert cfg.ref_height == 1080

    def test_default_reactions_empty(self):
        cfg = ConditionConfig()
        assert cfg.reactions == []

    def test_save_creates_json(self, tmp_path: Path):
        p = tmp_path / "cond.json"
        ConditionConfig().save(p)
        assert p.exists()
        data = json.loads(p.read_text())
        assert "detection_mode" in data

    def test_save_load_roundtrip(self, tmp_path: Path):
        p = tmp_path / "cond.json"
        cfg = ConditionConfig(detection_mode="template", check_interval=0.2)
        cfg.save(p)
        loaded = ConditionConfig.load(p)
        assert loaded.detection_mode == "template"
        assert loaded.check_interval == pytest.approx(0.2)

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        cfg = ConditionConfig.load(tmp_path / "nonexistent.json")
        assert cfg.detection_mode == "color"

    def test_load_ignores_unknown_keys(self, tmp_path: Path):
        p = tmp_path / "cond.json"
        p.write_text(json.dumps({"detection_mode": "color", "future_field": 999}))
        cfg = ConditionConfig.load(p)
        assert cfg.detection_mode == "color"


# ─────────────────────────────────────────────────────────────────────────────
# ConditionDetector — _scale_roi & debug_save
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionDetectorScaleRoi:

    def test_full_resolution_no_scaling(self):
        cfg = ConditionConfig(condition_icons_roi=[100, 50, 200, 30])
        det = ConditionDetector(cfg)
        frame = np.zeros((REF_H, REF_W, 3), dtype=np.uint8)
        rx, ry, rw, rh = det._scale_roi(frame)
        assert rx == 100
        assert ry == 50
        assert rw == 200
        assert rh == 30

    def test_half_resolution_scales_down(self):
        cfg = ConditionConfig(
            condition_icons_roi=[200, 100, 400, 60],
            ref_width=1920, ref_height=1080,
        )
        det = ConditionDetector(cfg)
        # frame at 960×540 (half scale)
        frame = np.zeros((540, 960, 3), dtype=np.uint8)
        rx, ry, rw, rh = det._scale_roi(frame)
        assert rx == 100
        assert ry == 50
        assert rw == 200
        assert rh == 30

    def test_zero_roi_returns_zeros(self):
        cfg = ConditionConfig(condition_icons_roi=[0, 0, 0, 0])
        det = ConditionDetector(cfg)
        frame = np.zeros((REF_H, REF_W, 3), dtype=np.uint8)
        rx, ry, rw, rh = det._scale_roi(frame)
        assert rw == 0
        assert rh == 0


class TestConditionDetectorDebugSave:

    def test_creates_output_file(self, tmp_path: Path):
        cfg = ConditionConfig(condition_icons_roi=COND_ROI, detection_mode="color")
        det = ConditionDetector(cfg)
        out = str(tmp_path / "debug.png")
        det.debug_save(_blank_frame(), path=out)
        assert Path(out).exists()

    def test_debug_save_with_active_condition(self, tmp_path: Path):
        cfg = ConditionConfig(condition_icons_roi=COND_ROI, detection_mode="color")
        det = ConditionDetector(cfg)
        frame = _blank_frame()
        # Inject poison color into ROI so detect() finds it
        h, s, v = [(_HSV_RANGES["poison"][i*2] + _HSV_RANGES["poison"][i*2+1]) // 2
                   for i in range(3)]
        bgr = _hsv2bgr(h, s, v)
        x, y, w, hr = COND_ROI
        n = max(_MIN_PIXELS.get("poison", 5) + 5, 15)
        frame[y: y + hr, x: x + n] = bgr
        out = str(tmp_path / "debug_active.png")
        det.debug_save(frame, path=out)
        assert Path(out).exists()


class TestConditionDetectorTemplateMode:

    def test_template_mode_no_templates_falls_back_to_color(self):
        """Con detection_mode='template' pero sin templates en disco, cae a color."""
        cfg = ConditionConfig(
            condition_icons_roi=COND_ROI,
            detection_mode="template",
        )
        det = ConditionDetector(cfg)
        # sin templates → _templates vacío → _detect_template devuelve _detect_color
        frame = _blank_frame()
        result = det.detect(frame)
        assert isinstance(result, set)

    def test_template_mode_empty_frame_returns_empty_set(self):
        cfg = ConditionConfig(
            condition_icons_roi=[0, 0, 0, 0],
            detection_mode="template",
        )
        det = ConditionDetector(cfg)
        result = det.detect(_blank_frame())
        assert result == set()


# ─────────────────────────────────────────────────────────────────────────────
# ConditionMonitor
# ─────────────────────────────────────────────────────────────────────────────

def _make_poison_frame() -> np.ndarray:
    """Frame 1920×1080 con color veneno dentro del ROI por defecto."""
    frame = _blank_frame()
    h, s, v = [(_HSV_RANGES["poison"][i*2] + _HSV_RANGES["poison"][i*2+1]) // 2
               for i in range(3)]
    bgr = _hsv2bgr(h, s, v)
    x, y, w, rh = COND_ROI
    n = max(_MIN_PIXELS.get("poison", 5) + 10, 20)
    frame[y: y + rh, x: x + n] = bgr
    return frame


class TestConditionMonitorConstruction:

    def test_construction_with_defaults(self):
        ctrl = MagicMock()
        cfg  = ConditionConfig(condition_icons_roi=COND_ROI)
        mon  = ConditionMonitor(ctrl, config=cfg)
        assert not mon._running
        assert mon._frame_getter is None

    def test_reactions_loaded_from_config(self):
        ctrl = MagicMock()
        cfg  = ConditionConfig(
            condition_icons_roi=COND_ROI,
            reactions=[{"condition": "poison", "vk": 0x71, "cooldown": 3.0}],
        )
        mon = ConditionMonitor(ctrl, config=cfg)
        assert "poison" in mon._reactions
        assert mon._reactions["poison"].vk == 0x71

    def test_active_conditions_empty_on_init(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert mon.active_conditions == set()


class TestConditionMonitorDynamicConfig:

    def test_set_frame_getter_stored(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        fn  = lambda: None
        mon.set_frame_getter(fn)
        assert mon._frame_getter is fn

    def test_add_reaction_stored(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("paralyze", vk=0x72, cooldown=2.0)
        assert "paralyze" in mon._reactions
        assert mon._reactions["paralyze"].vk == 0x72

    def test_add_reaction_overwrites_existing(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x70, cooldown=1.0)
        mon.add_reaction("poison", vk=0x71, cooldown=5.0)
        assert mon._reactions["poison"].vk == 0x71

    def test_add_reaction_default_label_is_condition(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("burning", vk=0x73)
        assert mon._reactions["burning"].label == "burning"


class TestConditionMonitorStartStop:

    def test_start_sets_running(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig(check_interval=0.01))
        mon.start()
        assert mon._running is True
        mon.stop()

    def test_stop_clears_running(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig(check_interval=0.01))
        mon.start()
        mon.stop()
        assert mon._running is False

    def test_double_start_does_not_spawn_extra_thread(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig(check_interval=0.01))
        mon.start()
        t1 = mon._thread
        mon.start()   # no-op
        assert mon._thread is t1
        mon.stop()

    def test_pause_and_resume(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig(check_interval=0.01))
        mon.pause()
        assert mon._paused is True
        mon.resume()
        assert mon._paused is False


class TestConditionMonitorHasCondition:

    def test_has_condition_false_when_empty(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert mon.has_condition("poison") is False

    def test_active_conditions_property_returns_copy(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon._active_conditions = {"poison", "freezing"}
        result = mon.active_conditions
        assert "poison" in result
        assert "freezing" in result
        # Modifying the copy does not affect internal state
        result.add("paralyze")
        assert "paralyze" not in mon._active_conditions

    def test_has_condition_true_when_active(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon._active_conditions = {"burning"}
        assert mon.has_condition("burning") is True
        assert mon.has_condition("poison") is False


class TestConditionMonitorReactionThread:

    def test_reaction_fires_on_detected_condition(self):
        ctrl = MagicMock()
        ctrl.press_key.return_value = True
        cfg  = ConditionConfig(
            condition_icons_roi=COND_ROI,
            detection_mode="color",
            check_interval=0.01,
        )
        mon = ConditionMonitor(ctrl, config=cfg)
        poison_frame = _make_poison_frame()
        mon.set_frame_getter(lambda: poison_frame)
        mon.add_reaction("poison", vk=0x71, cooldown=0.0)
        mon.start()
        time.sleep(0.15)
        mon.stop()
        # press_key should have been called with the poison VK at least once
        calls = [c.args[0] for c in ctrl.press_key.call_args_list]
        assert 0x71 in calls

    def test_no_reaction_when_no_frame_getter(self):
        ctrl = MagicMock()
        cfg  = ConditionConfig(check_interval=0.01)
        mon  = ConditionMonitor(ctrl, config=cfg)
        mon.add_reaction("poison", vk=0x71, cooldown=0.0)
        # No frame_getter set → loop just sleeps
        mon.start()
        time.sleep(0.05)
        mon.stop()
        ctrl.press_key.assert_not_called()

    def test_no_reaction_when_vk_is_zero(self):
        ctrl = MagicMock()
        cfg  = ConditionConfig(
            condition_icons_roi=COND_ROI,
            detection_mode="color",
            check_interval=0.01,
        )
        mon = ConditionMonitor(ctrl, config=cfg)
        poison_frame = _make_poison_frame()
        mon.set_frame_getter(lambda: poison_frame)
        mon.add_reaction("poison", vk=0, cooldown=0.0)  # disabled
        mon.start()
        time.sleep(0.1)
        mon.stop()
        ctrl.press_key.assert_not_called()

    def test_reaction_cooldown_limits_calls(self):
        ctrl = MagicMock()
        ctrl.press_key.return_value = True
        cfg  = ConditionConfig(
            condition_icons_roi=COND_ROI,
            detection_mode="color",
            check_interval=0.005,
        )
        mon = ConditionMonitor(ctrl, config=cfg)
        poison_frame = _make_poison_frame()
        mon.set_frame_getter(lambda: poison_frame)
        mon.add_reaction("poison", vk=0x71, cooldown=999.0)  # very long cooldown
        mon.start()
        time.sleep(0.1)
        mon.stop()
        # With a 999s cooldown, only 1 press should happen
        calls = [c.args[0] for c in ctrl.press_key.call_args_list if c.args and c.args[0] == 0x71]
        assert len(calls) <= 1


# ─────────────────────────────────────────────────────────────────────────────
# ConditionMonitor.remove_reaction / list_reactions /
# reset_reaction_counts / reaction_counts / update_config
# ─────────────────────────────────────────────────────────────────────────────

def _make_monitor() -> ConditionMonitor:
    ctrl = MagicMock()
    cfg  = ConditionConfig(condition_icons_roi=COND_ROI, detection_mode="color")
    return ConditionMonitor(ctrl, config=cfg)


class TestConditionMonitorRemoveReaction:

    def test_remove_existing_returns_true(self):
        mon = _make_monitor()
        mon.add_reaction("poison", vk=0x71)
        assert mon.remove_reaction("poison") is True

    def test_remove_nonexistent_returns_false(self):
        mon = _make_monitor()
        assert mon.remove_reaction("unknown_condition") is False

    def test_removed_reaction_no_longer_in_list(self):
        mon = _make_monitor()
        mon.add_reaction("poison", vk=0x71)
        mon.remove_reaction("poison")
        assert "poison" not in mon.list_reactions()

    def test_remove_does_not_affect_other_reactions(self):
        mon = _make_monitor()
        mon.add_reaction("poison",   vk=0x71)
        mon.add_reaction("paralyze", vk=0x72)
        mon.remove_reaction("poison")
        assert "paralyze" in mon.list_reactions()

    def test_double_remove_second_returns_false(self):
        mon = _make_monitor()
        mon.add_reaction("poison", vk=0x71)
        mon.remove_reaction("poison")
        assert mon.remove_reaction("poison") is False


class TestConditionMonitorListReactions:

    def test_empty_when_no_reactions(self):
        mon = _make_monitor()
        assert mon.list_reactions() == []

    def test_returns_added_reactions(self):
        mon = _make_monitor()
        mon.add_reaction("poison",   vk=0x71)
        mon.add_reaction("paralyze", vk=0x72)
        assert set(mon.list_reactions()) == {"poison", "paralyze"}

    def test_returns_sorted_list(self):
        mon = _make_monitor()
        mon.add_reaction("poison",   vk=0x71)
        mon.add_reaction("burning",  vk=0x72)
        mon.add_reaction("paralyze", vk=0x73)
        lst = mon.list_reactions()
        assert lst == sorted(lst)

    def test_overwrite_does_not_duplicate(self):
        mon = _make_monitor()
        mon.add_reaction("poison", vk=0x71)
        mon.add_reaction("poison", vk=0x72)  # overwrite
        assert mon.list_reactions().count("poison") == 1


class TestConditionMonitorReactionCounts:

    def test_initial_counts_empty(self):
        mon = _make_monitor()
        assert mon.reaction_counts == {}

    def test_reset_clears_counts(self):
        mon = _make_monitor()
        mon._reaction_counts["poison"] = 5
        mon.reset_reaction_counts()
        assert mon.reaction_counts == {}

    def test_reset_on_empty_is_noop(self):
        mon = _make_monitor()
        mon.reset_reaction_counts()  # should not raise
        assert mon.reaction_counts == {}

    def test_reaction_counts_is_copy(self):
        mon = _make_monitor()
        mon._reaction_counts["poison"] = 3
        snapshot = mon.reaction_counts
        snapshot["poison"] = 99
        assert mon._reaction_counts["poison"] == 3  # internal unaffected


class TestConditionMonitorUpdateConfig:

    def test_update_config_replaces_cfg(self):
        mon = _make_monitor()
        new_cfg = ConditionConfig(check_interval=9.9)
        mon.update_config(new_cfg)
        assert mon._cfg.check_interval == pytest.approx(9.9)

    def test_update_config_rebuilds_detector(self):
        mon = _make_monitor()
        old_det = mon._det
        mon.update_config(ConditionConfig())
        assert mon._det is not old_det

    def test_update_config_preserves_existing_reactions(self):
        mon = _make_monitor()
        mon.add_reaction("poison", vk=0x71)
        mon.update_config(ConditionConfig())  # no reactions in new config
        assert "poison" in mon.list_reactions()

    def test_update_config_merges_new_reactions(self):
        mon = _make_monitor()
        mon.add_reaction("poison",  vk=0x71)
        new_cfg = ConditionConfig(reactions=[
            {"condition": "paralyze", "vk": 0x72, "cooldown": 3.0, "label": "utani"}
        ])
        mon.update_config(new_cfg)
        assert "poison"   in mon.list_reactions()
        assert "paralyze" in mon.list_reactions()

    def test_update_config_overwrites_conflicting_reaction(self):
        mon = _make_monitor()
        mon.add_reaction("poison", vk=0x71, cooldown=1.0)
        new_cfg = ConditionConfig(reactions=[
            {"condition": "poison", "vk": 0x99, "cooldown": 5.0, "label": "new"}
        ])
        mon.update_config(new_cfg)
        assert mon._reactions["poison"].vk == 0x99


# ─────────────────────────────────────────────────────────────────────────────
# ConditionMonitor.is_running / is_paused
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionMonitorIsRunningIsPaused:

    def test_is_running_false_before_start(self):
        mon = _make_monitor()
        assert mon.is_running is False

    def test_is_running_true_after_start(self):
        mon = _make_monitor()
        mon.start()
        assert mon.is_running is True
        mon.stop()

    def test_is_running_false_after_stop(self):
        mon = _make_monitor()
        mon.start()
        mon.stop()
        assert mon.is_running is False

    def test_is_paused_false_initially(self):
        mon = _make_monitor()
        assert mon.is_paused is False

    def test_is_paused_true_after_pause(self):
        mon = _make_monitor()
        mon.pause()
        assert mon.is_paused is True

    def test_is_paused_false_after_resume(self):
        mon = _make_monitor()
        mon.pause()
        mon.resume()
        assert mon.is_paused is False


# ─────────────────────────────────────────────────────────────────────────────
# ConditionMonitor.active_count / total_reactions_fired / stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionMonitorStatsAndCount:

    def test_active_count_zero_initially(self):
        mon = _make_monitor()
        assert mon.active_count == 0

    def test_active_count_reflects_active_conditions(self):
        mon = _make_monitor()
        mon._active_conditions = {"poison", "paralyze"}
        assert mon.active_count == 2

    def test_total_reactions_fired_zero_initially(self):
        mon = _make_monitor()
        assert mon.total_reactions_fired == 0

    def test_total_reactions_fired_sums_counts(self):
        mon = _make_monitor()
        mon._reaction_counts = {"poison": 3, "paralyze": 2}
        assert mon.total_reactions_fired == 5

    def test_stats_snapshot_returns_dict(self):
        mon = _make_monitor()
        assert isinstance(mon.stats_snapshot(), dict)

    def test_stats_snapshot_all_keys(self):
        mon = _make_monitor()
        snap = mon.stats_snapshot()
        for key in ("active_conditions", "reaction_counts", "total_reactions",
                    "is_running", "is_paused", "reactions_registered"):
            assert key in snap, f"Missing key: {key}"

    def test_stats_snapshot_is_running_false(self):
        mon = _make_monitor()
        assert mon.stats_snapshot()["is_running"] is False

    def test_stats_snapshot_total_reactions_consistent(self):
        mon = _make_monitor()
        mon._reaction_counts = {"poison": 2, "fire": 1}
        snap = mon.stats_snapshot()
        assert snap["total_reactions"] == 3

    def test_stats_snapshot_active_conditions_sorted(self):
        mon = _make_monitor()
        mon._active_conditions = {"poison", "bleed", "curse"}
        snap = mon.stats_snapshot()
        assert snap["active_conditions"] == sorted(["poison", "bleed", "curse"])


class TestConditionMonitorHasFrameGetter:

    def test_has_frame_getter_false_initially(self):
        mon = _make_monitor()  # _make_monitor does NOT call set_frame_getter
        assert mon.has_frame_getter is False

    def test_has_frame_getter_true_after_set(self):
        mon = _make_monitor()
        mon.set_frame_getter(lambda: None)
        assert mon.has_frame_getter is True

    def test_has_frame_getter_returns_bool(self):
        mon = _make_monitor()
        assert isinstance(mon.has_frame_getter, bool)


class TestConditionNames:

    def test_condition_names_returns_list(self):
        names = ConditionMonitor.condition_names()
        assert isinstance(names, list)

    def test_condition_names_sorted(self):
        names = ConditionMonitor.condition_names()
        assert names == sorted(names)

    def test_condition_names_nonempty(self):
        assert len(ConditionMonitor.condition_names()) > 0

    def test_condition_names_contains_poison(self):
        assert "poison" in ConditionMonitor.condition_names()

    def test_condition_names_contains_paralyze(self):
        assert "paralyze" in ConditionMonitor.condition_names()

    def test_condition_names_matches_hsv_keys(self):
        assert set(ConditionMonitor.condition_names()) == set(_HSV_RANGES.keys())


class TestConditionMonitorReactionCount:

    def test_reaction_count_zero_initially(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert mon.reaction_count == 0

    def test_reaction_count_after_add(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        assert mon.reaction_count == 1

    def test_reaction_count_multiple_adds(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        mon.add_reaction("fire", vk=0x72)
        assert mon.reaction_count == 2

    def test_reaction_count_returns_int(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert isinstance(mon.reaction_count, int)

    def test_reaction_count_decreases_after_remove(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        mon.remove_reaction("poison")
        assert mon.reaction_count == 0


class TestConditionMonitorHasReactions:

    def test_has_reactions_false_initially(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert mon.has_reactions is False

    def test_has_reactions_true_after_add(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        assert mon.has_reactions is True

    def test_has_reactions_false_after_remove(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        mon.remove_reaction("poison")
        assert mon.has_reactions is False

    def test_has_reactions_returns_bool(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert isinstance(mon.has_reactions, bool)

    def test_has_reactions_consistent_with_reaction_count(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        assert mon.has_reactions == (mon.reaction_count > 0)


# ─────────────────────────────────────────────────────────────────────────────
# has_fired
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionMonitorHasFired:

    def test_false_initially(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert mon.has_fired is False

    def test_true_after_reaction_count_set(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        mon._reaction_counts["poison"] = 3
        assert mon.has_fired is True

    def test_false_after_reset(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        mon._reaction_counts["poison"] = 2
        mon.reset_reaction_counts()
        assert mon.has_fired is False

    def test_consistent_with_total_reactions_fired(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon.add_reaction("poison", vk=0x71)
        mon._reaction_counts["poison"] = 1
        assert mon.has_fired == (mon.total_reactions_fired > 0)

    def test_returns_bool(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert isinstance(mon.has_fired, bool)


# ─────────────────────────────────────────────────────────────────────────────
# is_active
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionMonitorIsActive:

    def test_false_initially(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert mon.is_active is False

    def test_true_when_active_conditions_set(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon._active_conditions = {"poison"}
        assert mon.is_active is True

    def test_false_after_clearing_active_conditions(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon._active_conditions = {"poison"}
        mon._active_conditions.clear()
        assert mon.is_active is False

    def test_consistent_with_active_count(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon._active_conditions = {"bleeding", "poison"}
        assert mon.is_active == (mon.active_count > 0)

    def test_returns_bool(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        assert isinstance(mon.is_active, bool)


# ─────────────────────────────────────────────────────────────────────────────
# Regression: ConditionMonitor log callback mechanism
# Bug: start(), stop() and _loop() used bare print() — no log callback support.
#      Added set_log_callback() / _log() following same pattern as AutoHealer.
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionBug3CondMonitorLogCallback:

    def test_set_log_callback_stored(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        cb = lambda msg: None
        mon.set_log_callback(cb)
        assert mon._log_cb is cb

    def test_log_uses_callback_when_set(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        captured: list[str] = []
        mon.set_log_callback(captured.append)
        mon._log("test message")
        assert captured == ["test message"]

    def test_log_without_callback_prints(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig())
        mon._log("fallback output")
        assert "fallback output" in capsys.readouterr().out

    def test_start_routes_through_log_callback(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig(check_interval=0.01))
        captured: list[str] = []
        mon.set_log_callback(captured.append)
        mon.start()
        mon.stop()
        assert any("[N]" in m for m in captured), (
            f"Expected [N] log from start(); got: {captured}"
        )

    def test_stop_routes_through_log_callback(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig(check_interval=0.01))
        captured: list[str] = []
        mon.set_log_callback(captured.append)
        mon.start()
        mon.stop()
        # Last captured message should contain "Detenido"
        combined = " ".join(captured)
        assert "Detenido" in combined, (
            f"Expected 'Detenido' in log; got: {captured}"
        )

    def test_start_does_not_print_when_callback_set(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig(check_interval=0.01))
        mon.set_log_callback(lambda m: None)
        mon.start()
        mon.stop()
        out = capsys.readouterr().out
        assert "Monitor" not in out

    def test_multiple_log_messages_all_go_through_callback(self):
        mon = ConditionMonitor(MagicMock(), config=ConditionConfig(check_interval=0.01))
        captured: list[str] = []
        mon.set_log_callback(captured.append)
        mon.start()
        time.sleep(0.05)
        mon.stop()
        # At minimum: "Loop activo" + start message + stop message
        assert len(captured) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# Regression: ConditionMonitor uses time.monotonic() for cooldowns
# Bug: _loop used time.time() — vulnerable to clock changes and inconsistent
#      with all other modules.  Fixed to time.monotonic().
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionBug4CondMonitorMonotonic:

    def test_cooldown_enforced_after_log_callback_set(self):
        """Cooldown must still work correctly with monotonic clock."""
        ctrl = MagicMock()
        ctrl.press_key.return_value = True
        cfg = ConditionConfig(
            condition_icons_roi=COND_ROI,
            detection_mode="color",
            check_interval=0.005,
        )
        mon = ConditionMonitor(ctrl, config=cfg)
        mon.set_log_callback(lambda m: None)
        poison_frame = _make_poison_frame()
        mon.set_frame_getter(lambda: poison_frame)
        mon.add_reaction("poison", vk=0x71, cooldown=999.0)
        mon.start()
        time.sleep(0.1)
        mon.stop()
        calls = [c.args[0] for c in ctrl.press_key.call_args_list if c.args and c.args[0] == 0x71]
        assert len(calls) <= 1, (
            f"Cooldown not enforced — expected ≤1 press, got {len(calls)}"
        )

    def test_reaction_fires_immediately_on_first_detection(self):
        """First detection should always fire (last_used defaults to 0.0 < monotonic)."""
        ctrl = MagicMock()
        ctrl.press_key.return_value = True
        cfg = ConditionConfig(
            condition_icons_roi=COND_ROI,
            detection_mode="color",
            check_interval=0.01,
        )
        mon = ConditionMonitor(ctrl, config=cfg)
        mon.set_log_callback(lambda m: None)
        poison_frame = _make_poison_frame()
        mon.set_frame_getter(lambda: poison_frame)
        mon.add_reaction("poison", vk=0x71, cooldown=0.0)
        mon.start()
        time.sleep(0.1)
        mon.stop()
        calls = [c.args[0] for c in ctrl.press_key.call_args_list if c.args and c.args[0] == 0x71]
        assert len(calls) >= 1, "Expected at least one press on first detection"

    def test_failed_key_press_does_not_consume_cooldown(self):
        ctrl = MagicMock()
        ctrl.press_key.side_effect = [False, False, True]
        cfg = ConditionConfig(
            condition_icons_roi=COND_ROI,
            detection_mode="color",
            check_interval=0.005,
        )
        mon = ConditionMonitor(ctrl, config=cfg)
        poison_frame = _make_poison_frame()
        mon.set_frame_getter(lambda: poison_frame)
        mon.add_reaction("poison", vk=0x71, cooldown=999.0)
        mon.start()
        time.sleep(0.1)
        mon.stop()

        calls = [c.args[0] for c in ctrl.press_key.call_args_list if c.args and c.args[0] == 0x71]
        assert len(calls) >= 3, "Failed presses must be retried without waiting for cooldown"
        assert mon.reaction_counts.get("poison") == 1
