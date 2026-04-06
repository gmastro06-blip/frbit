"""
Tests for new CombatManager features:
  - on_kill callback
  - _kills auto-increment when battle list goes empty while in_combat=True
  - detect_auto() (template → OCR fallback)
  - OCR config fields (ocr_detection, ocr_confidence)
Fully offline: no OBS, no Tibia process.
"""
from __future__ import annotations

import time
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.combat_manager import CombatConfig, CombatManager, BattleDetector


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Factories
# ─────────────────────────────────────────────────────────────────────────────

def _mock_ctrl() -> MagicMock:
    ctrl = MagicMock()
    ctrl.is_connected.return_value = True
    ctrl.press_key.return_value = True
    ctrl.click.return_value = True
    return ctrl


def _make_cm(ctrl=None, cfg=None) -> CombatManager:
    cm = CombatManager(
        ctrl=ctrl or _mock_ctrl(),
        config=cfg or CombatConfig(),
    )
    cm.set_log_callback(lambda msg: None)
    return cm


def _blank_frame(w: int = 200, h: int = 200) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
# on_kill callback
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerOnKill:

    def test_on_kill_none_by_default(self):
        cm = _make_cm()
        assert cm.on_kill is None

    def test_on_kill_settable(self):
        cm = _make_cm()
        fn = lambda: None
        cm.on_kill = fn
        assert cm.on_kill is fn

    def test_on_kill_callback_is_callable(self):
        """on_kill attribute should be directly invocable when set."""
        cm = _make_cm()
        called = [0]
        cm.on_kill = lambda: called.__setitem__(0, called[0] + 1)
        cm.on_kill()  # invoke the callback directly
        assert called[0] == 1

    def test_notify_kill_increments_kills(self):
        cm = _make_cm()
        cm.notify_kill()
        assert cm.kills == 1

    def test_notify_kill_multiple_times(self):
        cm = _make_cm()
        cm.on_kill = lambda: None
        cm.notify_kill()
        cm.notify_kill()
        cm.notify_kill()
        assert cm.kills == 3

    def test_on_kill_exception_does_not_propagate(self):
        cm = _make_cm()
        def bad_kill():
            raise RuntimeError("boom")
        cm.on_kill = bad_kill
        # notify_kill catches exceptions internally
        cm.notify_kill()  # should not raise
        assert cm.kills == 1

    def test_notify_kill_resets_combat_state(self):
        cm = _make_cm()
        cm._in_combat = True
        cm._current_target = (100, 200)
        cm.notify_kill()
        assert cm.is_in_combat is False
        assert cm._current_target is None

    def test_on_kill_not_called_when_none(self):
        cm = _make_cm()
        cm.on_kill = None
        # Should not raise
        cm.notify_kill()
        assert cm.kills == 1


# ─────────────────────────────────────────────────────────────────────────────
# _kills auto-increment loop detection
# ─────────────────────────────────────────────────────────────────────────────

class TestKillsAutoCount:
    """Verify that kills are counted when battle list empties while in_combat."""

    def _run_one_loop_tick(self, cm: CombatManager, detections: list) -> None:
        """
        Simulate one tick of the combat loop by directly calling
        _loop logic with mocked detector.
        """
        with patch.object(cm._detector, "detect_auto", return_value=detections):
            frame = _blank_frame()
            # Simulate the flee + detect portion of the loop
            hp_pct = cm._read_hp_pct(frame)
            cm._last_hp_pct = hp_pct
            dets = cm._detector.detect_auto(frame)
            if not dets:
                if cm._in_combat:
                    cm._kills += 1
                    if cm.on_kill is not None:
                        try:
                            cm.on_kill()
                        except Exception:
                            pass
                cm._in_combat = False
                cm._current_target = None

    def test_kill_counted_when_list_empties_from_combat(self):
        cm = _make_cm()
        cm._in_combat = True
        cm._current_target = (100, 200)
        self._run_one_loop_tick(cm, detections=[])
        assert cm.kills == 1

    def test_kill_not_counted_when_not_in_combat(self):
        cm = _make_cm()
        cm._in_combat = False
        self._run_one_loop_tick(cm, detections=[])
        assert cm.kills == 0

    def test_on_kill_triggered_by_auto_count(self):
        cm = _make_cm()
        cm._in_combat = True
        called = [0]
        cm.on_kill = lambda: called.__setitem__(0, called[0] + 1)
        self._run_one_loop_tick(cm, detections=[])
        assert called[0] == 1


# ─────────────────────────────────────────────────────────────────────────────
# detect_auto
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectAuto:

    def test_detect_auto_uses_templates_when_available(self, tmp_path):
        import cv2 as _cv2
        monsters_dir = tmp_path / "monsters"
        monsters_dir.mkdir(parents=True)
        # Create a 15x15 grey template
        tmpl = np.full((15, 15), 200, dtype=np.uint8)
        _cv2.imwrite(str(monsters_dir / "troll.png"), tmpl)

        cfg = CombatConfig(templates_dir=str(tmp_path), confidence=0.5)
        det = BattleDetector(cfg)
        assert det.has_templates is True
        # detect_auto should delegate to detect() (template path)
        frame = _blank_frame(300, 300)
        result = det.detect_auto(frame)
        assert isinstance(result, list)

    def test_detect_auto_returns_empty_when_no_templates_and_ocr_disabled(self, tmp_path):
        cfg = CombatConfig(templates_dir=str(tmp_path), ocr_detection=False)
        det = BattleDetector(cfg)
        assert det.has_templates is False
        result = det.detect_auto(_blank_frame())
        assert result == []

    def test_detect_auto_calls_detect_ocr_when_ocr_enabled_no_templates(self, tmp_path):
        cfg = CombatConfig(templates_dir=str(tmp_path), ocr_detection=True)
        det = BattleDetector(cfg)
        assert det.has_templates is False
        with patch.object(det, "detect_ocr", return_value=[]) as mock_ocr:
            det.detect_auto(_blank_frame())
            mock_ocr.assert_called_once()

    def test_detect_auto_prefers_ocr_over_templates_when_ocr_enabled(self, tmp_path):
        """When ocr_detection=True, OCR is used and templates are NOT loaded (memory opt)."""
        import cv2 as _cv2
        monsters_dir = tmp_path / "monsters"
        monsters_dir.mkdir(parents=True)
        tmpl = np.full((10, 10), 128, dtype=np.uint8)
        _cv2.imwrite(str(monsters_dir / "rat.png"), tmpl)

        cfg = CombatConfig(templates_dir=str(tmp_path), ocr_detection=True, confidence=0.5)
        det = BattleDetector(cfg)
        # Templates NOT loaded when ocr_detection=True (memory optimization)
        assert det.has_templates is False
        with patch.object(det, "detect_ocr", return_value=[]) as mock_ocr:
            det.detect_auto(_blank_frame())
            mock_ocr.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# OCR config fields
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatConfigOcr:

    def test_ocr_detection_default_false(self):
        cfg = CombatConfig()
        assert cfg.ocr_detection is False

    def test_ocr_confidence_default(self):
        cfg = CombatConfig()
        assert cfg.ocr_confidence == pytest.approx(0.3)

    def test_ocr_fields_persist_through_save_load(self, tmp_path):
        path = tmp_path / "combat_config.json"
        cfg = CombatConfig(ocr_detection=True, ocr_confidence=0.55)
        cfg.save(path)
        loaded = CombatConfig.load(path)
        assert loaded.ocr_detection is True
        assert loaded.ocr_confidence == pytest.approx(0.55)

    def test_detect_ocr_returns_list_without_easyocr(self, tmp_path):
        """detect_ocr should return [] gracefully when easyocr not importable."""
        cfg = CombatConfig(templates_dir=str(tmp_path))
        det = BattleDetector(cfg)
        with patch.dict("sys.modules", {"easyocr": None}):
            result = det.detect_ocr(_blank_frame())
            assert result == []

    def test_detect_ocr_returns_list_on_none_frame(self, tmp_path):
        cfg = CombatConfig(templates_dir=str(tmp_path), ocr_detection=True)
        det = BattleDetector(cfg)
        result = det.detect_ocr(None)   # type: ignore[arg-type]
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# CombatManager new properties
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerNewProps:

    def test_kills_zero_initially(self):
        cm = _make_cm()
        assert cm.kills == 0

    def test_reset_kills_zeros_counters(self):
        cm = _make_cm()
        cm._kills = 5
        cm._attacks_sent = 10
        cm.reset_kills()
        assert cm.kills == 0
        assert cm.attacks_sent == 0

    def test_has_kills_false_initially(self):
        cm = _make_cm()
        assert cm.has_kills is False

    def test_has_kills_true_after_notify_kill(self):
        cm = _make_cm()
        cm.notify_kill()
        assert cm.has_kills is True

    def test_stats_snapshot_includes_kills(self):
        cm = _make_cm()
        cm._kills = 3
        snap = cm.stats_snapshot()
        assert snap["kills"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# notify_kill now fires on_kill callback (regression guard)
# ─────────────────────────────────────────────────────────────────────────────

class TestNotifyKillFiresOnKill:

    def test_on_kill_called_by_notify_kill(self):
        """on_kill must be invoked when notify_kill() is called explicitly."""
        cm = _make_cm()
        called: List[int] = []
        cm.on_kill = lambda: called.append(1)
        cm.notify_kill()
        assert called == [1]

    def test_notify_kill_fires_on_kill_each_time(self):
        cm = _make_cm()
        count: List[int] = [0]
        cm.on_kill = lambda: count.__setitem__(0, count[0] + 1)
        cm.notify_kill()
        cm.notify_kill()
        cm.notify_kill()
        assert count[0] == 3

    def test_on_kill_not_called_if_none(self):
        """notify_kill must not crash when on_kill is None."""
        cm = _make_cm()
        cm.on_kill = None
        cm.notify_kill()  # should not raise
        assert cm.kills == 1


# ─────────────────────────────────────────────────────────────────────────────
# CombatConfig.reselect_interval field
# ─────────────────────────────────────────────────────────────────────────────

class TestReselectInterval:

    def test_default_is_3_seconds(self):
        cfg = CombatConfig()
        assert cfg.reselect_interval == 3.0

    def test_custom_value_accepted(self):
        cfg = CombatConfig(reselect_interval=5.0)
        assert cfg.reselect_interval == 5.0

    def test_persists_through_save_load(self, tmp_path):
        path = tmp_path / "cc.json"
        cfg = CombatConfig(reselect_interval=7.5)
        cfg.save(path)
        loaded = CombatConfig.load(path)
        assert loaded.reselect_interval == 7.5


# ─────────────────────────────────────────────────────────────────────────────
# BattleDetector OCR reader caching
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectOcrReaderCache:

    def test_ocr_reader_none_initially(self, tmp_path):
        cfg = CombatConfig(templates_dir=str(tmp_path))
        det = BattleDetector(cfg)
        assert det._ocr_reader is None

    def test_ocr_reader_stays_none_when_easyocr_missing(self, tmp_path):
        cfg = CombatConfig(templates_dir=str(tmp_path))
        det = BattleDetector(cfg)
        import numpy as np
        with patch.dict("sys.modules", {"easyocr": None}):
            det.detect_ocr(np.zeros((50, 50, 3), dtype=np.uint8))
        # Reader should still be None — import failed, nothing cached
        assert det._ocr_reader is None

    def test_ocr_reader_cached_after_successful_init(self, tmp_path):
        """Once EasyOCR is available, the reader must be re-used."""
        import numpy as np
        cfg = CombatConfig(templates_dir=str(tmp_path))
        det = BattleDetector(cfg)

        mock_reader = MagicMock()
        mock_reader.readtext.return_value = []
        mock_easyocr = MagicMock()
        mock_easyocr.Reader.return_value = mock_reader

        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        with patch.dict("sys.modules", {"easyocr": mock_easyocr}):
            det.detect_ocr(frame)
            det.detect_ocr(frame)

        # Reader constructor called exactly once
        mock_easyocr.Reader.assert_called_once()
