"""Additional tests for src/combat_manager.py to improve branch coverage.

Targets the uncovered lines including:
- CombatConfig.validate() error paths (lines 156, 162, 164, 166, 181, 185)
- BattleDetector.detect_ocr() OCR logic branches (329, 334-375)
- CombatManager._cast_spells() AoE filtering, press_key failure, global CD (795-833)
- CombatManager._sort_by_priority() substring fallback (863-864)
- CombatManager._check_anti_lure() flee path (903)
- CombatManager._loop() all major branches (897-1114)
- CombatManager._emit() event bus (539)
- CombatManager.stop(), start() re-entrant guard
"""

from __future__ import annotations

from collections import Counter

import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

from src.combat_manager import (
    BattleDetector,
    CombatConfig,
    CombatManager,
    TrackedCombatTarget,
    _OCR_UI_BLACKLIST,
)
from src.combat_manager_loop import _handle_empty_detections, _process_absent_monsters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_ctrl() -> MagicMock:
    ctrl = MagicMock()
    ctrl.press_key.return_value = True
    ctrl.click.return_value = True
    ctrl.is_connected.return_value = True
    return ctrl


def _mock_hp_det(hp: int = 80, mp: int = 80) -> MagicMock:
    det = MagicMock()
    det.read_bars.return_value = (hp, mp)
    return det


def _make_cfg(**kw) -> CombatConfig:
    defaults = dict(
        battle_list_roi=[0, 0, 200, 200],
        templates_dir="__nonexistent__",
        check_interval=0.01,
        attack_vk=0,
    )
    defaults.update(kw)
    return CombatConfig(**defaults)


def _make_cm(ctrl=None, cfg=None, hp=80, mp=80, **kw) -> CombatManager:
    cm = CombatManager(
        ctrl=ctrl or _mock_ctrl(),
        hp_detector=_mock_hp_det(hp, mp),
        config=cfg or _make_cfg(),
        **kw,
    )
    cm.set_log_callback(lambda msg: None)
    return cm


def _blank_frame(h: int = 200, w: int = 200) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# CombatConfig.validate() — uncovered error branches
# ---------------------------------------------------------------------------

class TestCombatConfigValidateErrors:

    def test_negative_roi_value_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            CombatConfig(battle_list_roi=[-1, 0, 200, 200]).validate()

    def test_check_interval_negative_raises(self):
        with pytest.raises(ValueError, match="check_interval"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], check_interval=-1.0).validate()

    def test_skip_top_negative_raises(self):
        with pytest.raises(ValueError, match="skip_top"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], skip_top=-1).validate()

    def test_slot_height_zero_raises(self):
        with pytest.raises(ValueError, match="slot_height"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], slot_height=0).validate()

    def test_ref_width_zero_raises(self):
        with pytest.raises(ValueError, match="ref_width"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], ref_width=0).validate()

    def test_ref_height_zero_raises(self):
        with pytest.raises(ValueError, match="ref_height"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], ref_height=0).validate()

    def test_aoe_mob_threshold_zero_raises(self):
        with pytest.raises(ValueError, match="aoe_mob_threshold"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], aoe_mob_threshold=0).validate()

    def test_flee_mob_count_negative_raises(self):
        with pytest.raises(ValueError, match="flee_mob_count"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], flee_mob_count=-1).validate()

    def test_max_expected_mobs_negative_raises(self):
        with pytest.raises(ValueError, match="max_expected_mobs"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], max_expected_mobs=-1).validate()

    def test_invalid_lure_action_raises(self):
        with pytest.raises(ValueError, match="lure_action"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], lure_action="attack").validate()

    def test_attack_vk_out_of_range_raises(self):
        with pytest.raises(ValueError, match="attack_vk"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], attack_vk=0x1FF).validate()

    def test_flee_vk_out_of_range_raises(self):
        with pytest.raises(ValueError, match="flee_vk"):
            CombatConfig(battle_list_roi=[0, 0, 200, 200], flee_vk=0x200).validate()

    def test_spell_vk_out_of_range_raises(self):
        with pytest.raises(ValueError, match="spells"):
            CombatConfig(
                battle_list_roi=[0, 0, 200, 200],
                spells=[{"vk": 0x200}]
            ).validate()

    def test_valid_config_no_raise(self):
        cfg = CombatConfig(battle_list_roi=[0, 0, 200, 200])
        cfg.validate()  # should not raise


# ---------------------------------------------------------------------------
# BattleDetector.detect_ocr — OCR logic branches
# ---------------------------------------------------------------------------

class TestDetectOcrBranches:

    def _make_det(self, tmp_path, **kw) -> BattleDetector:
        cfg = CombatConfig(templates_dir=str(tmp_path), **kw)
        return BattleDetector(cfg)

    def test_ocr_read_exception_returns_empty(self, tmp_path):
        """EasyOCR reader raises — returns []."""
        det = self._make_det(tmp_path)
        mock_reader = MagicMock()
        mock_reader.readtext.side_effect = RuntimeError("OCR fail")
        det._ocr_reader = mock_reader

        result = det.detect_ocr(_blank_frame())
        assert result == []

    def _make_det_fullframe(self, tmp_path, **kw) -> BattleDetector:
        """Make a BattleDetector where ROI = full 200x200 frame."""
        cfg = CombatConfig(templates_dir=str(tmp_path),
                           battle_list_roi=[0, 0, 200, 200],
                           ref_width=200, ref_height=200,
                           **kw)
        return BattleDetector(cfg)

    def test_ocr_skips_low_confidence(self, tmp_path):
        """Text below ocr_confidence threshold is skipped."""
        det = self._make_det_fullframe(tmp_path, ocr_confidence=0.5)
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = [
            ([[10, 10], [60, 10], [60, 25], [10, 25]], "Wasp", 0.2),
        ]
        det._ocr_reader = mock_reader

        result = det.detect_ocr(np.zeros((200, 200, 3), dtype=np.uint8))
        assert result == []

    def test_ocr_skips_empty_text(self, tmp_path):
        """Empty text string is skipped."""
        det = self._make_det_fullframe(tmp_path, ocr_confidence=0.3)
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = [
            ([[10, 10], [60, 10], [60, 25], [10, 25]], "", 0.9),
        ]
        det._ocr_reader = mock_reader
        result = det.detect_ocr(np.zeros((200, 200, 3), dtype=np.uint8))
        assert result == []

    def test_ocr_skips_short_name(self, tmp_path):
        """Name < 3 chars after normalisation is skipped."""
        det = self._make_det_fullframe(tmp_path, ocr_confidence=0.3)
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = [
            ([[10, 10], [30, 10], [30, 25], [10, 25]], "AB", 0.9),
        ]
        det._ocr_reader = mock_reader
        result = det.detect_ocr(np.zeros((200, 200, 3), dtype=np.uint8))
        assert result == []

    def test_ocr_skips_ui_blacklist(self, tmp_path):
        """Words in _OCR_UI_BLACKLIST are filtered out."""
        det = self._make_det_fullframe(tmp_path, ocr_confidence=0.3)
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = [
            ([[10, 10], [80, 10], [80, 25], [10, 25]], "battle", 0.9),
        ]
        det._ocr_reader = mock_reader
        result = det.detect_ocr(np.zeros((200, 200, 3), dtype=np.uint8))
        assert result == []

    def test_ocr_skips_out_of_roi_bbox(self, tmp_path):
        """Bounding box centroid outside ROI image is rejected."""
        det = self._make_det_fullframe(tmp_path, ocr_confidence=0.3)
        mock_reader = MagicMock()
        # bbox with centre at (-5, -5) — outside ROI
        mock_reader.readtext.return_value = [
            ([[-10, -10], [-5, -10], [-5, -5], [-10, -5]], "Wasp", 0.9),
        ]
        det._ocr_reader = mock_reader
        result = det.detect_ocr(np.zeros((200, 200, 3), dtype=np.uint8))
        assert result == []

    def test_ocr_skips_top_slot(self, tmp_path):
        """skip_top=10 filters first slot detections."""
        det = self._make_det_fullframe(tmp_path, ocr_confidence=0.3,
                                       skip_top=10, slot_height=22)
        mock_reader = MagicMock()
        # Centre at local (65, 5) → slot_idx = (ry+5 - ry)/22 = 5/22 = 0 < skip_top=10
        mock_reader.readtext.return_value = [
            ([[40, 0], [90, 0], [90, 10], [40, 10]], "Wasp", 0.9),
        ]
        det._ocr_reader = mock_reader
        result = det.detect_ocr(np.zeros((200, 200, 3), dtype=np.uint8))
        assert result == []

    def test_ocr_valid_detection_returned(self, tmp_path):
        """Valid text produces a detection tuple."""
        det = self._make_det_fullframe(tmp_path, ocr_confidence=0.3,
                                       skip_top=0, slot_height=22)
        mock_reader = MagicMock()
        # centre at local (65, 50) → within 200x200 ROI, slot_idx=2 >= skip_top=0
        mock_reader.readtext.return_value = [
            ([[40, 45], [90, 45], [90, 55], [40, 55]], "Wasp", 0.9),
        ]
        det._ocr_reader = mock_reader
        result = det.detect_ocr(np.zeros((200, 200, 3), dtype=np.uint8))
        assert len(result) == 1
        assert result[0][3] == "Wasp"

    def test_ocr_empty_roi(self, tmp_path):
        """ROI that is zero-sized returns []."""
        det = self._make_det(tmp_path, battle_list_roi=[5000, 5000, 1, 1])
        det._ocr_reader = MagicMock()
        result = det.detect_ocr(np.zeros((10, 10, 3), dtype=np.uint8))
        assert result == []


# ---------------------------------------------------------------------------
# CombatManager._cast_spells — AoE/single-target filtering, press_key fail
# ---------------------------------------------------------------------------

class TestCastSpellsAoEFiltering:

    def test_single_target_spell_skipped_when_aoe_preferred(self):
        """type='single_target' skipped when mob_count >= aoe_mob_threshold."""
        spells = [{"vk": 0x71, "min_mp": 0, "cooldown": 0.0, "label": "x",
                   "type": "single_target"}]
        cfg = _make_cfg(spells=spells, aoe_mob_threshold=2)
        cm = _make_cm(cfg=cfg, mp=100)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(_blank_frame(), mob_count=3)  # mob_count=3 >= aoe_threshold=2
        ctrl.press_key.assert_not_called()

    def test_aoe_spell_skipped_when_single_preferred(self):
        """type='aoe' skipped when mob_count < aoe_mob_threshold."""
        spells = [{"vk": 0x71, "min_mp": 0, "cooldown": 0.0, "label": "x",
                   "type": "aoe"}]
        cfg = _make_cfg(spells=spells, aoe_mob_threshold=3)
        cm = _make_cm(cfg=cfg, mp=100)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(_blank_frame(), mob_count=1)  # mob_count=1 < aoe_threshold=3
        ctrl.press_key.assert_not_called()

    def test_taunt_spell_skipped_when_single_preferred(self):
        """type='taunt' is treated like AoE and skipped with few mobs."""
        spells = [{"vk": 0x72, "min_mp": 0, "cooldown": 0.0, "label": "t",
                   "type": "taunt"}]
        cfg = _make_cfg(spells=spells, aoe_mob_threshold=3)
        cm = _make_cm(cfg=cfg, mp=100)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(_blank_frame(), mob_count=1)
        ctrl.press_key.assert_not_called()

    def test_aoe_spell_fires_when_aoe_preferred(self):
        """type='aoe' fires when mob_count >= aoe_mob_threshold."""
        spells = [{"vk": 0x71, "min_mp": 0, "cooldown": 0.0, "label": "exori mas",
                   "type": "aoe"}]
        cfg = _make_cfg(spells=spells, aoe_mob_threshold=2)
        cm = _make_cm(cfg=cfg, mp=100)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(_blank_frame(), mob_count=2)
        ctrl.press_key.assert_called_with(0x71)

    def test_press_key_fail_breaks_spell_loop(self):
        """When press_key returns False, loop breaks (no further spells cast)."""
        spells = [
            {"vk": 0x71, "min_mp": 0, "cooldown": 0.0, "label": "a"},
            {"vk": 0x72, "min_mp": 0, "cooldown": 0.0, "label": "b"},
        ]
        cfg = _make_cfg(spells=spells)
        cm = _make_cm(cfg=cfg, mp=100)
        ctrl = _mock_ctrl()
        ctrl.press_key.return_value = False  # always fails
        cm._ctrl = ctrl
        cm._cast_spells(_blank_frame(), mob_count=1)
        # Called once (failed), should not retry second spell
        ctrl.press_key.assert_called_once_with(0x71)

    def test_global_cooldown_blocks_spell(self):
        """If _last_any_spell is recent, spells are blocked by global CD."""
        spells = [{"vk": 0x71, "min_mp": 0, "cooldown": 0.0, "label": "x"}]
        cfg = _make_cfg(spells=spells)
        cm = _make_cm(cfg=cfg, mp=100)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._last_any_spell = time.monotonic() + 9999.0  # far future
        cm._cast_spells(_blank_frame(), mob_count=1)
        ctrl.press_key.assert_not_called()

    def test_spell_vk_zero_is_skipped(self):
        """vk=0 spell entry is silently skipped."""
        spells = [{"vk": 0, "min_mp": 0, "cooldown": 0.0, "label": "none"}]
        cfg = _make_cfg(spells=spells)
        cm = _make_cm(cfg=cfg, mp=100)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(_blank_frame(), mob_count=1)
        ctrl.press_key.assert_not_called()

    def test_cast_spells_without_hp_detector_fallback(self):
        """When _hp is None and _cached_mp_pct is None, spell with min_mp=0 fires."""
        spells = [{"vk": 0x71, "min_mp": 0, "cooldown": 0.0, "label": "x"}]
        cfg = _make_cfg(spells=spells)
        cm = _make_cm(cfg=cfg, mp=80)
        cm._hp = None
        cm._cached_mp_pct = None
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(_blank_frame(), mob_count=1)
        ctrl.press_key.assert_called_with(0x71)

    def test_cast_spells_hp_fallback_read_exception(self):
        """When _cached_mp_pct is None and _hp.read_bars raises, spell still fires if min_mp=0."""
        spells = [{"vk": 0x71, "min_mp": 0, "cooldown": 0.0, "label": "x"}]
        cfg = _make_cfg(spells=spells)
        cm = _make_cm(cfg=cfg, mp=80)
        bad_hp = MagicMock()
        bad_hp.read_bars.side_effect = RuntimeError("sensor error")
        cm._hp = bad_hp
        cm._cached_mp_pct = None
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(_blank_frame(), mob_count=1)
        ctrl.press_key.assert_called_with(0x71)


# ---------------------------------------------------------------------------
# CombatManager._sort_by_priority — substring / exact match fallback
# ---------------------------------------------------------------------------

class TestSortByPriorityFallback:

    def test_exact_match_priority(self):
        """Exact lowercase name match assigns correct priority index."""
        cfg = _make_cfg(monster_priority=["wasp", "bug"])
        cm = _make_cm(cfg=cfg)
        dets = [(10, 50, 0.9, "Bug"), (20, 30, 0.8, "Wasp")]
        result = cm._sort_by_priority(dets)
        assert result[0][3] == "Wasp"

    def test_unknown_name_gets_fallback(self):
        cfg = _make_cfg(monster_priority=["wasp"])
        cm = _make_cm(cfg=cfg)
        dets = [(10, 50, 0.9, "Troll"), (20, 30, 0.8, "Wasp")]
        result = cm._sort_by_priority(dets)
        assert result[0][3] == "Wasp"
        assert result[1][3] == "Troll"

    def test_empty_priority_returns_unchanged(self):
        cfg = _make_cfg(monster_priority=[])
        cm = _make_cm(cfg=cfg)
        dets = [(10, 50, 0.9, "Bug"), (20, 30, 0.8, "Wasp")]
        result = cm._sort_by_priority(dets)
        assert result == dets


# ---------------------------------------------------------------------------
# CombatManager._check_anti_lure — various branches
# ---------------------------------------------------------------------------

class TestCheckAntiLure:

    def test_no_max_expected_always_false(self):
        cfg = _make_cfg(max_expected_mobs=0)
        cm = _make_cm(cfg=cfg)
        assert cm._check_anti_lure(100) is False

    def test_under_max_returns_false(self):
        cfg = _make_cfg(max_expected_mobs=5)
        cm = _make_cm(cfg=cfg)
        assert cm._check_anti_lure(3) is False

    def test_over_max_returns_true(self):
        cfg = _make_cfg(max_expected_mobs=2)
        cm = _make_cm(cfg=cfg)
        assert cm._check_anti_lure(5) is True

    def test_over_max_increments_lure_warnings(self):
        cfg = _make_cfg(max_expected_mobs=2)
        cm = _make_cm(cfg=cfg)
        cm._check_anti_lure(5)
        cm._check_anti_lure(5)
        assert cm.lure_warnings == 2

    def test_lure_action_warn_does_not_press_key(self):
        cfg = _make_cfg(max_expected_mobs=2, lure_action="warn", flee_vk=0x71)
        cm = _make_cm(cfg=cfg)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._check_anti_lure(5)
        ctrl.press_key.assert_not_called()


# ---------------------------------------------------------------------------
# CombatManager._emit — event bus integration
# ---------------------------------------------------------------------------

class TestCombatManagerEmit:

    def test_emit_calls_event_bus(self):
        bus = MagicMock()
        cm = _make_cm(event_bus=bus)
        cm._emit("e1", {"name": "Troll"})
        bus.emit.assert_called_once_with("e1", {"name": "Troll"})

    def test_emit_no_bus_does_nothing(self):
        cm = _make_cm()
        cm._emit("e1", {"name": "X"})  # should not raise

    def test_emit_bus_exception_logged_not_raised(self):
        bus = MagicMock()
        bus.emit.side_effect = RuntimeError("bus error")
        msgs = []
        cm = _make_cm(event_bus=bus)
        cm.set_log_callback(msgs.append)
        cm._emit("e1", {"name": "X"})  # should not raise
        assert any("bus" in m or "emit" in m for m in msgs)

    def test_notify_kill_emits_event(self):
        bus = MagicMock()
        cm = _make_cm(event_bus=bus)
        cm.notify_kill("Troll")
        bus.emit.assert_called_with("e1", {"name": "Troll"})


# ---------------------------------------------------------------------------
# CombatManager.start() re-entrant guard
# ---------------------------------------------------------------------------

class TestStartReentrant:

    def test_start_twice_only_one_thread(self):
        cm = _make_cm()
        cm.start()
        thread_1 = cm._thread
        cm.start()  # should be a no-op
        thread_2 = cm._thread
        assert thread_1 is thread_2
        cm.stop()

    def test_stop_logs_kills_and_attacks(self):
        msgs = []
        cm = _make_cm()
        cm.set_log_callback(msgs.append)
        cm.start()
        cm.stop()
        assert any("kills" in m or "Detenido" in m for m in msgs)


# ---------------------------------------------------------------------------
# CombatManager._loop — full loop simulation via thread
# ---------------------------------------------------------------------------

class TestCombatManagerLoop:
    """Drive the actual _loop() thread with controlled frame sequences."""

    def _make_detection(self, x=50, y=50, conf=0.9, name="Troll"):
        return (x, y, conf, name)

    def test_loop_attacks_when_detection_present(self):
        """Loop clicks the detected monster target."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(attack_vk=0, check_interval=0.01, reselect_interval=0.0)
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)

        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        det_list = [self._make_detection()]
        with patch.object(cm._detector, "detect_auto", return_value=det_list):
            cm.start()
            time.sleep(0.15)
            cm.stop()

        # confirm_streak must reach ENGAGE_CONFIRM_FRAMES before click
        assert ctrl.click.call_count >= 1

    def test_loop_flee_when_hp_low(self):
        """When HP < hp_flee_pct, flee_vk is pressed."""
        ctrl = _mock_ctrl()
        flee_vk = 0x73
        cfg = _make_cfg(hp_flee_pct=50, flee_vk=flee_vk, check_interval=0.01)
        cm = CombatManager(
            ctrl=ctrl,
            config=cfg,
            hp_detector=_mock_hp_det(hp=20, mp=80),  # hp=20 < flee_pct=50
        )
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        det_list = [self._make_detection()]
        with patch.object(cm._detector, "detect_auto", return_value=det_list):
            cm.start()
            time.sleep(0.15)
            cm.stop()

        ctrl.press_key.assert_any_call(flee_vk)

    def test_loop_paused_does_not_click(self):
        """Paused loop should not click targets."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01)
        cm = CombatManager(ctrl=ctrl, config=cfg)
        cm.set_log_callback(lambda m: None)
        cm.pause()

        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)
        det_list = [self._make_detection()]

        with patch.object(cm._detector, "detect_auto", return_value=det_list):
            cm.start()
            time.sleep(0.1)
            cm.stop()

        ctrl.click.assert_not_called()

    def test_loop_no_frame_getter_waits(self):
        """No frame_getter set — loop keeps sleeping without crashing."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01)
        cm = CombatManager(ctrl=ctrl, config=cfg)
        cm.set_log_callback(lambda m: None)
        # Do NOT set frame_getter
        cm.start()
        time.sleep(0.1)
        cm.stop()
        ctrl.click.assert_not_called()

    def test_loop_frame_getter_returns_none(self):
        """frame_getter returning None is handled gracefully."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01)
        cm = CombatManager(ctrl=ctrl, config=cfg)
        cm.set_log_callback(lambda m: None)
        cm.set_frame_getter(lambda: None)
        cm.start()
        time.sleep(0.1)
        cm.stop()
        ctrl.click.assert_not_called()

    def test_loop_frame_getter_raises(self):
        """frame_getter raising an exception is handled gracefully."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01)
        cm = CombatManager(ctrl=ctrl, config=cfg)
        cm.set_log_callback(lambda m: None)

        call_count = [0]
        def bad_fg():
            call_count[0] += 1
            raise RuntimeError("capture error")

        cm.set_frame_getter(bad_fg)
        cm.start()
        time.sleep(0.1)
        cm.stop()
        # Loop should have run multiple times without crashing
        assert call_count[0] >= 1

    def test_loop_kill_confirmed_after_empty_streak(self):
        """Kill is registered after N consecutive empty detection frames."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01, reselect_interval=0.0)
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        # Pre-set in_combat and prev_detection_names as if combat was ongoing
        cm._in_combat = True
        cm._prev_detection_names = ["Troll"]
        cm._empty_frames_streak = 0
        # absence_frames_required=3; lower it to 1 so confirmation happens in one tick
        cm._absence_frames_required = 1

        # Return empty detections to trigger kill confirmation
        with patch("src.combat_manager.jittered_sleep"):
            with patch.object(cm._detector, "detect_auto", return_value=[]):
                cm.start()
                time.sleep(0.3)
                cm.stop()

        assert cm.kills >= 1

    def test_loop_per_monster_kill_tracked(self):
        """Per-monster absence counter triggers kill after N absent frames.

        With _absence_frames_required=1, Orc goes absent in one tick and is
        confirmed killed immediately.
        """
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01, reselect_interval=0.0)
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        # Start in combat tracking "Orc"; it disappears while "Troll" remains.
        cm._in_combat = True
        cm._confirm_streak = 99
        cm._prev_detection_names = ["Orc"]
        # Lower threshold to 1 so absence is confirmed in one frame
        cm._absence_frames_required = 1

        with patch.object(cm._detector, "detect_auto",
                          return_value=[(50, 50, 0.9, "Troll")]):
            cm.start()
            time.sleep(0.2)
            cm.stop()

        assert cm.kills >= 1

    def test_loop_on_kill_callback_triggered_by_loop(self):
        """on_kill is invoked when the loop detects a kill."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01)
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        kills_triggered = []
        cm.on_kill = lambda: kills_triggered.append(1)
        cm._in_combat = True
        cm._prev_detection_names = ["Troll"]

        with patch.object(cm._detector, "detect_auto", return_value=[]):
            cm.start()
            time.sleep(0.2)
            cm.stop()

        assert len(kills_triggered) >= 1

    def test_loop_on_kill_exception_does_not_crash_loop(self):
        """on_kill raising should not crash the loop."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01)
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        cm.on_kill = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        cm._in_combat = True
        cm._prev_detection_names = ["Troll"]

        with patch.object(cm._detector, "detect_auto", return_value=[]):
            cm.start()
            time.sleep(0.2)
            cm.stop()

        # Loop should have survived
        assert cm.kills >= 1

    def test_loop_attack_vk_sent_when_configured(self):
        """attack_vk hotkey is pressed when ctrl.is_connected() is True."""
        ctrl = _mock_ctrl()
        ctrl.is_connected.return_value = True
        attack_vk = 0x74
        cfg = _make_cfg(
            attack_vk=attack_vk,
            check_interval=0.01,
            reselect_interval=0.0,
        )
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        det_list = [(50, 50, 0.9, "Troll")]
        with patch.object(cm._detector, "detect_auto", return_value=det_list):
            cm._confirm_streak = 99  # skip engagement confirmation
            cm.start()
            time.sleep(0.2)
            cm.stop()

        ctrl.press_key.assert_any_call(attack_vk)

    def test_loop_anti_lure_flee_triggers(self):
        """Anti-lure with lure_action='flee' resets combat and presses flee_vk."""
        ctrl = _mock_ctrl()
        flee_vk = 0x75
        cfg = _make_cfg(
            max_expected_mobs=1,
            lure_action="flee",
            flee_vk=flee_vk,
            check_interval=0.01,
        )
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        # 3 detections > max_expected_mobs=1 → anti-lure flee
        det_list = [
            (10, 10, 0.9, "Troll"),
            (20, 20, 0.9, "Orc"),
            (30, 30, 0.9, "Goblin"),
        ]
        with patch.object(cm._detector, "detect_auto", return_value=det_list):
            cm.start()
            time.sleep(0.15)
            cm.stop()

        ctrl.press_key.assert_any_call(flee_vk)

    def test_loop_mob_count_flee_triggers(self):
        """flee_mob_count threshold triggers flee with sufficient mobs and HP."""
        ctrl = _mock_ctrl()
        flee_vk = 0x76
        cfg = _make_cfg(
            hp_flee_pct=50,
            flee_mob_count=2,
            flee_vk=flee_vk,
            check_interval=0.01,
        )
        # HP=45, just below hp_flee_pct=50 AND hp_flee_pct+20=70 > 45
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det(hp=45))
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        det_list = [(10, 10, 0.9, "Orc"), (20, 20, 0.9, "Troll")]
        with patch.object(cm._detector, "detect_auto", return_value=det_list):
            cm.start()
            time.sleep(0.15)
            cm.stop()

        ctrl.press_key.assert_any_call(flee_vk)

    def test_loop_verify_attacks_on_fail_retries_click(self):
        """With verify_attacks=True, failed verification retries click."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01, reselect_interval=0.0)
        cm = CombatManager(ctrl=ctrl, config=cfg, verify_attacks=True)
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        # Make verify_target_selected return False always
        with patch("src.combat_manager.verify_target_selected", return_value=False):
            det_list = [(50, 50, 0.9, "Troll")]
            with patch.object(cm._detector, "detect_auto", return_value=det_list):
                cm._confirm_streak = 99
                cm.start()
                time.sleep(0.15)
                cm.stop()

        assert cm.target_verify_fails >= 1

    def test_loop_exception_in_body_continues(self):
        """Generic exception inside loop body is caught and loop continues."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01)
        cm = CombatManager(ctrl=ctrl, config=cfg)
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        call_count = [0]
        def detect_fn(f):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("unexpected error")
            return []

        # The outer except sleeps max(check_interval, 1.0) = 1.0 second.
        # Patch jittered_sleep so recovery is immediate.
        with patch("src.combat_manager.jittered_sleep"):
            with patch.object(cm._detector, "detect_auto", side_effect=detect_fn):
                cm.start()
                time.sleep(0.2)
                cm.stop()

        assert call_count[0] >= 2  # loop recovered and ran again

    def test_loop_target_reselect_on_same_target_timeout(self):
        """Target is reselected when reselect_interval expires even for same target."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01, reselect_interval=0.05, attack_vk=0)
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        # Set current_target to same position as detection
        cm._current_target = (50, 50)
        cm._last_target_time = time.monotonic() - 10.0  # expired
        cm._in_combat = True
        cm._confirm_streak = 99

        det_list = [(50, 50, 0.9, "Troll")]
        with patch.object(cm._detector, "detect_auto", return_value=det_list):
            cm.start()
            time.sleep(0.15)
            cm.stop()

        assert ctrl.click.call_count >= 1

    def test_loop_engage_confirmation_streak(self):
        """Engagement confirmation: no click until ENGAGE_CONFIRM_FRAMES consecutive detections."""
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01, reselect_interval=0.0)
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)
        # _confirm_streak starts at 0, ENGAGE_CONFIRM_FRAMES=2
        # first iteration increments to 1 → no click
        # second iteration increments to 2 (or passes) → click

        det_list = [(50, 50, 0.9, "Troll")]
        with patch.object(cm._detector, "detect_auto", return_value=det_list):
            cm.start()
            time.sleep(0.15)
            cm.stop()

        # After enough iterations, click should have happened
        assert ctrl.click.call_count >= 1


# ---------------------------------------------------------------------------
# CombatManager.lure_warnings / prev_detection_count properties
# ---------------------------------------------------------------------------

class TestCombatManagerNewProperties:

    def test_lure_warnings_zero_initially(self):
        cm = _make_cm()
        assert cm.lure_warnings == 0

    def test_lure_warnings_increments(self):
        cfg = _make_cfg(max_expected_mobs=1)
        cm = _make_cm(cfg=cfg)
        cm._check_anti_lure(5)
        assert cm.lure_warnings == 1

    def test_prev_detection_count_zero_initially(self):
        cm = _make_cm()
        assert cm.prev_detection_count == 0

    def test_prev_detection_count_reflects_list(self):
        cm = _make_cm()
        cm._prev_detection_names = ["Troll", "Orc"]
        assert cm.prev_detection_count == 2

    def test_target_verify_fails_zero_initially(self):
        cm = _make_cm()
        assert cm.target_verify_fails == 0


class TestCombatDuplicateNameTracking:

    def test_partial_duplicate_disappearance_confirms_single_kill(self):
        cm = _make_cm()
        cm._tracked_detection_counts = {"Rat": 2, "Orc": 1}
        cm._prev_detection_names = ["Rat", "Rat", "Orc"]

        emitted: list[tuple[str, dict[str, str]]] = []
        with patch.object(cm, "_emit", side_effect=lambda event, data=None: emitted.append((event, data or {}))):
            for _ in range(2):
                _process_absent_monsters(
                    cm,
                    current_detections=[(10, 20, 0.9, "Rat"), (10, 42, 0.9, "Orc")],
                    counter_cls=Counter,
                )
                assert cm.kills == 0

            _process_absent_monsters(
                cm,
                current_detections=[(10, 20, 0.9, "Rat"), (10, 42, 0.9, "Orc")],
                counter_cls=Counter,
            )

        assert cm.kills == 1
        assert cm._tracked_detection_counts["Rat"] == 1
        assert emitted == [("e1", {"name": "Rat"})]

    def test_empty_detections_confirms_all_duplicate_kills(self):
        cm = _make_cm()
        cm._in_combat = True
        cm._tracked_detection_counts = {"Rat": 2}
        cm._prev_detection_names = ["Rat", "Rat"]

        emitted: list[tuple[str, dict[str, str]]] = []
        with patch.object(cm, "_emit", side_effect=lambda event, data=None: emitted.append((event, data or {}))):
            for _ in range(2):
                _handle_empty_detections(cm, jittered_sleep_fn=lambda _: None, counter_cls=Counter)
                assert cm.kills == 0

            _handle_empty_detections(cm, jittered_sleep_fn=lambda _: None, counter_cls=Counter)

        assert cm.kills == 2
        assert cm._tracked_detection_counts == {}
        assert emitted == [("e1", {"name": "Rat"}), ("e1", {"name": "Rat"})]

    def test_duplicate_kill_does_not_clear_same_named_current_target(self):
        cm = _make_cm()
        now = time.monotonic()
        cm._in_combat = True
        cm._current_target = (10, 42)
        cm._tracked_target = TrackedCombatTarget(
            name="Rat",
            position=(10, 42),
            acquired_at=now,
            last_seen_at=now,
        )
        cm._tracked_detection_counts = {"Rat": 2}
        cm._prev_detection_names = ["Rat", "Rat"]

        for _ in range(2):
            _process_absent_monsters(
                cm,
                current_detections=[(10, 42, 0.9, "Rat")],
                counter_cls=Counter,
            )

        _process_absent_monsters(
            cm,
            current_detections=[(10, 42, 0.9, "Rat")],
            counter_cls=Counter,
        )

        assert cm.kills == 1
        assert cm.current_target_name == "Rat"
        assert cm.last_target_result is None

    def test_empty_detections_record_last_target_result_for_current_target(self):
        cm = _make_cm()
        now = time.monotonic() - 1.0
        cm._in_combat = True
        cm._current_target = (10, 42)
        cm._tracked_target = TrackedCombatTarget(
            name="Rat",
            position=(10, 42),
            acquired_at=now,
            last_seen_at=now,
        )
        cm._tracked_detection_counts = {"Rat": 1}
        cm._prev_detection_names = ["Rat"]
        cm._absence_frames_required = 1

        _handle_empty_detections(cm, jittered_sleep_fn=lambda _: None, counter_cls=Counter)

        result = cm.last_target_result
        assert result is not None
        assert result["name"] == "Rat"
        assert result["reason"] == "battle_list_empty"
        assert cm.current_target_name is None
        assert cm.kills == 1

    def test_loop_does_not_reclick_when_same_target_shifts_slightly(self):
        ctrl = _mock_ctrl()
        cfg = _make_cfg(check_interval=0.01, reselect_interval=10.0, attack_vk=0)
        cm = CombatManager(ctrl=ctrl, config=cfg, hp_detector=_mock_hp_det())
        cm.set_log_callback(lambda m: None)
        frame = _blank_frame()
        cm.set_frame_getter(lambda: frame)

        now = time.monotonic()
        cm._in_combat = True
        cm._current_target = (50, 50)
        cm._last_target_time = now
        cm._confirm_streak = 99
        cm._tracked_target = TrackedCombatTarget(
            name="Troll",
            position=(50, 50),
            acquired_at=now,
            last_seen_at=now,
        )

        with patch.object(cm._detector, "detect_auto", return_value=[(50, 68, 0.9, "Troll")]):
            cm.start()
            time.sleep(0.1)
            cm.stop()

        assert ctrl.click.call_count == 0


# ---------------------------------------------------------------------------
# BattleDetector.detect_auto — all paths
# ---------------------------------------------------------------------------

class TestDetectAutoFull:

    def test_ocr_mode_calls_detect_ocr(self, tmp_path):
        cfg = CombatConfig(templates_dir=str(tmp_path), ocr_detection=True)
        det = BattleDetector(cfg)
        with patch.object(det, "detect_ocr", return_value=[]) as mock_ocr:
            det.detect_auto(_blank_frame())
            mock_ocr.assert_called_once()

    def test_template_mode_with_templates_calls_detect(self, tmp_path):
        cfg = CombatConfig(templates_dir=str(tmp_path), ocr_detection=False)
        det = BattleDetector(cfg)
        det._templates = [("troll", np.zeros((10, 10), dtype=np.uint8))]
        with patch.object(det, "detect", return_value=[]) as mock_det:
            det.detect_auto(_blank_frame())
            mock_det.assert_called_once()

    def test_template_mode_no_templates_returns_empty(self, tmp_path):
        cfg = CombatConfig(templates_dir=str(tmp_path), ocr_detection=False)
        det = BattleDetector(cfg)
        assert det._templates == []
        result = det.detect_auto(_blank_frame())
        assert result == []


# ---------------------------------------------------------------------------
# CombatConfig.validate() — VK spells path (line 182-185)
# ---------------------------------------------------------------------------

class TestCombatConfigSpellVkValidation:

    def test_spell_vk_in_range_ok(self):
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 200, 200],
            spells=[{"vk": 0x71}]
        )
        cfg.validate()  # should not raise

    def test_spell_vk_zero_ok(self):
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 200, 200],
            spells=[{"vk": 0x00}]
        )
        cfg.validate()  # vk=0 is valid (0x00 <= 0xFF)

    def test_spell_missing_vk_defaults_to_zero(self):
        """spell without 'vk' key uses get("vk", 0) = 0, which is valid."""
        cfg = CombatConfig(
            battle_list_roi=[0, 0, 200, 200],
            spells=[{"label": "no vk key"}]
        )
        cfg.validate()  # should not raise (vk defaults to 0)
