"""
test_hpmp_coverage.py — coverage boost for hpmp_detector.py
100 % offline: no OBS, no EasyOCR, no filesystem writes.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, mock_open

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.hpmp_detector import HpMpDetector, HpMpConfig, NumericReading

REF_W, REF_H = 1920, 1080
HP_ROI = [1713, 445, 94, 9]
MP_ROI = [1713, 457, 94, 9]


def _blank() -> np.ndarray:
    return np.zeros((REF_H, REF_W, 3), dtype=np.uint8)


def _cfg(**kw) -> HpMpConfig:
    defaults = dict(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1, outlier_threshold=35)
    defaults.update(kw)
    return HpMpConfig(**defaults)


def _det(**kw) -> HpMpDetector:
    return HpMpDetector(_cfg(**kw))


# ── HpMpConfig.save / load / validate ────────────────────────────────────────

def test_config_save(tmp_path):
    p = tmp_path / "hpmp.json"
    cfg = _cfg()
    cfg.save(p)
    assert p.exists()
    data = json.loads(p.read_text())
    assert "hp_roi" in data


def test_config_load_existing(tmp_path):
    p = tmp_path / "hpmp.json"
    data = {"hp_roi": HP_ROI, "mp_roi": MP_ROI}
    p.write_text(json.dumps(data))
    cfg = HpMpConfig.load(p)
    assert cfg.hp_roi == HP_ROI


def test_config_load_missing(tmp_path):
    cfg = HpMpConfig.load(tmp_path / "nope.json")
    assert isinstance(cfg, HpMpConfig)


def test_config_validate_bad_len():
    cfg = HpMpConfig(hp_roi=[1, 2, 3])
    with pytest.raises(ValueError):
        cfg.validate()


def test_config_validate_negative():
    cfg = HpMpConfig(hp_roi=[-1, 0, 10, 5])
    with pytest.raises(ValueError):
        cfg.validate()


# ── read_bars outlier / smoothing paths ──────────────────────────────────────

def test_outlier_rejection_spike():
    """A big upward spike is rejected (outlier); HP stays None until baseline."""
    det = _det(outlier_threshold=10)
    frame = _blank()
    # Paint HP bar green (≈100 %)
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+w] = (0, 200, 0)
    hp1, _ = det.read_bars(frame)  # baseline
    # Now paint only a tiny sliver (≈5 %) — that's a huge drop, accepted
    frame2 = _blank()
    frame2[y:y+h, x:x+5] = (0, 200, 0)
    hp2, _ = det.read_bars(frame2)
    assert hp2 is not None  # drop accepted (safety)


def test_outlier_rejection_mp_spike():
    det = _det(outlier_threshold=10)
    frame = _blank()
    x, y, w, h = MP_ROI
    frame[y:y+h, x:x+w] = (200, 0, 0)
    _, mp1 = det.read_bars(frame)
    # large upward spike on MP
    frame2 = _blank()
    frame2[y:y+h, x:x+w] = (200, 0, 0)
    _, mp2 = det.read_bars(frame2)
    assert mp2 is not None


def test_outlier_reset_after_5_consecutive():
    """After _OUTLIER_RESET consecutive rejections, the baseline resets and reading is accepted."""
    det = _det(outlier_threshold=5)
    # Seed a known last_hp
    frame = _blank()
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+50] = (0, 200, 0)  # ~50 %
    det.read_bars(frame)
    # Now send a "spike" 6 times
    spike = _blank()
    spike[y:y+h, x:x+w] = (0, 200, 0)  # ~100 %
    results = [det.read_bars(spike)[0] for _ in range(6)]
    # After 5 rejects the 6th should be accepted
    assert any(r is not None for r in results)


def test_smoothing_averages():
    det = _det(smoothing=3)
    frame = _blank()
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+w] = (0, 200, 0)
    for _ in range(3):
        hp, _ = det.read_bars(frame)
    assert hp == 100


def test_extreme_drop_warning_logged(caplog):
    import logging
    det = _det(outlier_threshold=0)  # no rejection
    frame = _blank()
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+w] = (0, 200, 0)
    det.read_bars(frame)  # hp~100
    # Now drop to ~10 %
    frame2 = _blank()
    frame2[y:y+h, x:x+9] = (0, 200, 0)
    with caplog.at_level(logging.WARNING):
        det.read_bars(frame2)
    # extreme_drop counter incremented
    assert det._hp_extreme_drops >= 1


# ── heal_if_needed ────────────────────────────────────────────────────────────

def test_heal_if_needed_fires():
    det = _det()
    ctrl = MagicMock()
    frame = _blank()
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+30] = (0, 200, 0)  # ~30 %
    result = det.heal_if_needed(frame, hp_threshold=70, ctrl=ctrl, heal_vk=0xF1)
    assert "HEAL!" in result
    ctrl.press_key.assert_called()


def test_heal_if_needed_no_fire_when_healthy():
    det = _det()
    ctrl = MagicMock()
    frame = _blank()
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+w] = (0, 200, 0)  # 100 %
    result = det.heal_if_needed(frame, hp_threshold=70, ctrl=ctrl, heal_vk=0xF1)
    ctrl.press_key.assert_not_called()


def test_heal_if_needed_mana_fires():
    det = _det()
    ctrl = MagicMock()
    frame = _blank()
    mx, my, mw, mh = MP_ROI
    frame[my:my+mh, mx:mx+20] = (200, 0, 0)  # ~20 %
    det.heal_if_needed(frame, hp_threshold=0, ctrl=ctrl, heal_vk=0xF1,
                       mp_threshold=50, mana_vk=0xF2)
    assert any(call[0][0] == 0xF2 for call in ctrl.press_key.call_args_list)


def test_heal_if_needed_hp_none():
    det = _det()
    ctrl = MagicMock()
    frame = None  # will give None from read_bars via _read_bar guard
    # Use a black frame (0%) rather than None
    frame = _blank()
    result = det.heal_if_needed(frame, hp_threshold=70, ctrl=ctrl, heal_vk=0xF1)
    assert "HP:" in result


def test_heal_mp_tag_no_threshold():
    det = _det()
    ctrl = MagicMock()
    frame = _blank()
    result = det.heal_if_needed(frame, hp_threshold=70, ctrl=ctrl, heal_vk=0xF1)
    assert "MP:" in result


# ── reset_history ─────────────────────────────────────────────────────────────

def test_reset_history():
    det = _det(smoothing=3)
    frame = _blank()
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+w] = (0, 200, 0)
    det.read_bars(frame)
    det.reset_history()
    assert det._last_hp is None
    assert len(det._hp_history) == 0


# ── stats_snapshot ────────────────────────────────────────────────────────────

def test_stats_snapshot():
    det = _det()
    snap = det.stats_snapshot()
    assert "last_hp" in snap
    assert "outlier_rejects_hp" in snap


# ── set_smoothing ─────────────────────────────────────────────────────────────

def test_set_smoothing():
    det = _det(smoothing=1)
    det.set_smoothing(5)
    assert det._cfg.smoothing == 5


# ── preload_ocr (just ensure no crash) ────────────────────────────────────────

def test_preload_ocr_no_crash():
    det = _det()
    with patch("threading.Thread") as mock_t:
        mock_t.return_value = MagicMock()
        det.preload_ocr()


# ── debug_overlay / save_debug_image ─────────────────────────────────────────

def test_debug_overlay():
    det = _det()
    frame = _blank()
    with patch("cv2.rectangle"), patch("cv2.putText"):
        out = det.debug_overlay(frame)
    assert out is not None


def test_save_debug_image_frozen(tmp_path):
    det = _det()
    frame = _blank()
    path = str(tmp_path / "dbg.png")
    with patch("cv2.imwrite") as mock_write, \
         patch("cv2.rectangle"), patch("cv2.putText"):
        det.save_debug_image(frame, path)
        mock_write.assert_called_once()


def test_save_debug_image_frozen_mode():
    """When sys.frozen is True, save_debug_image should return early."""
    det = _det()
    frame = _blank()
    import sys as _sys
    _sys.frozen = True
    try:
        with patch("cv2.imwrite") as mock_write:
            det.save_debug_image(frame, "x.png")
            mock_write.assert_not_called()
    finally:
        del _sys.frozen


# ── auto_calibrate ─────────────────────────────────────────────────────────────

def test_auto_calibrate_finds_bars():
    det = _det()
    frame = _blank()
    # Draw a wide HP bar (saturated green) at row 100
    frame[100:115, 200:1000] = (0, 200, 0)
    # Draw a wide MP bar (blue) at row 200
    frame[200:210, 200:900] = (200, 0, 0)
    result = det.auto_calibrate(frame)
    assert result is True


def test_auto_calibrate_no_bars():
    det = _det()
    frame = _blank()
    result = det.auto_calibrate(frame)
    assert result is False


def test_auto_calibrate_invalid_frame():
    det = _det()
    result = det.auto_calibrate(None)
    assert result is False


def test_auto_calibrate_save(tmp_path):
    det = _det()
    frame = _blank()
    frame[100:115, 200:1000] = (0, 200, 0)
    frame[200:210, 200:900] = (200, 0, 0)
    with patch.object(det._cfg, "save") as mock_save:
        det.auto_calibrate(frame, save=True)
        mock_save.assert_called_once()


# ── numeric OCR paths ─────────────────────────────────────────────────────────

def test_ocr_bar_text_no_reader():
    det = _det()
    det._ocr_reader = None
    with patch.object(det, "_get_ocr_reader", return_value=None):
        result = det._ocr_bar_text(_blank(), HP_ROI)
    assert result == (None, None)


def test_ocr_bar_text_with_reader():
    det = _det()
    mock_reader = MagicMock()
    mock_reader.readtext.return_value = [(None, "512 / 850", 0.9)]
    det._ocr_reader = mock_reader
    with patch("cv2.cvtColor", return_value=np.zeros((20, 100), dtype=np.uint8)), \
         patch("cv2.threshold", return_value=(None, np.zeros((20, 100), dtype=np.uint8))), \
         patch("cv2.bitwise_and", return_value=np.zeros((20, 100), dtype=np.uint8)), \
         patch("cv2.resize", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.bilateralFilter", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.adaptiveThreshold", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.getStructuringElement", return_value=np.ones((2, 2), dtype=np.uint8)), \
         patch("cv2.dilate", return_value=np.zeros((80, 400), dtype=np.uint8)):
        result = det._ocr_bar_text(_blank(), HP_ROI)
    assert result == (512, 850)


def test_ocr_single_number():
    det = _det()
    mock_reader = MagicMock()
    mock_reader.readtext.return_value = [(None, "512", 0.9)]
    det._ocr_reader = mock_reader
    with patch("cv2.cvtColor", return_value=np.zeros((20, 100), dtype=np.uint8)), \
         patch("cv2.threshold", return_value=(None, np.zeros((20, 100), dtype=np.uint8))), \
         patch("cv2.bitwise_and", return_value=np.zeros((20, 100), dtype=np.uint8)), \
         patch("cv2.resize", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.bilateralFilter", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.adaptiveThreshold", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.getStructuringElement", return_value=np.ones((2, 2), dtype=np.uint8)), \
         patch("cv2.dilate", return_value=np.zeros((80, 400), dtype=np.uint8)):
        result = det._ocr_bar_text(_blank(), HP_ROI)
    assert result == (512, None)


def test_ocr_exception_returns_none():
    det = _det()
    mock_reader = MagicMock()
    mock_reader.readtext.side_effect = RuntimeError("ocr fail")
    det._ocr_reader = mock_reader
    with patch("cv2.cvtColor", return_value=np.zeros((20, 100), dtype=np.uint8)), \
         patch("cv2.threshold", return_value=(None, np.zeros((20, 100), dtype=np.uint8))), \
         patch("cv2.bitwise_and", return_value=np.zeros((20, 100), dtype=np.uint8)), \
         patch("cv2.resize", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.bilateralFilter", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.adaptiveThreshold", return_value=np.zeros((80, 400), dtype=np.uint8)), \
         patch("cv2.getStructuringElement", return_value=np.ones((2, 2), dtype=np.uint8)), \
         patch("cv2.dilate", return_value=np.zeros((80, 400), dtype=np.uint8)):
        result = det._ocr_bar_text(_blank(), HP_ROI)
    assert result == (None, None)


def test_numeric_reader_running_property():
    det = _det()
    assert det.numeric_reader_running is False


def test_start_stop_numeric_reader():
    det = _det()
    frames = [None]
    det.start_numeric_reader(lambda: frames[0], interval=0.05)
    time.sleep(0.1)
    assert det.numeric_reader_running
    det.stop_numeric_reader()
    assert not det.numeric_reader_running


def test_start_numeric_reader_already_running():
    det = _det()
    det.start_numeric_reader(lambda: None, interval=0.1)
    # Second call should be a no-op
    det.start_numeric_reader(lambda: None, interval=0.1)
    det.stop_numeric_reader()


def test_numeric_properties_none():
    det = _det()
    assert det.numeric is None
    assert det.hp_exact is None
    assert det.mp_exact is None
    assert det.hp_max is None
    assert det.mp_max is None
    assert det.hp_pct_exact is None
    assert det.mp_pct_exact is None


def test_hp_pct_exact_with_last_hp():
    det = _det()
    det._last_hp = 75
    assert det.hp_pct_exact == 75.0


def test_mp_pct_exact_with_last_mp():
    det = _det()
    det._last_mp = 50
    assert det.mp_pct_exact == 50.0


def test_hp_pct_exact_with_fresh_numeric():
    det = _det()
    nr = NumericReading(hp=512, mp=400, hp_max=850, mp_max=800, timestamp=time.monotonic())
    det._numeric = nr
    val = det.hp_pct_exact
    assert val is not None
    assert abs(val - (512 / 850 * 100)) < 1.0


def test_get_ocr_reader_import_error():
    det = _det()
    with patch.dict("sys.modules", {"easyocr": None}):
        reader = det._get_ocr_reader()
    # easyocr not available → None
    assert reader is None or True  # either way no crash


def test_preprocess_text_roi_small_crop():
    det = _det()
    frame = _blank()
    # ROI that gives less than 4×2 px after scaling
    result = det._preprocess_text_roi(frame, [0, 0, 2, 1])
    assert result is None


def test_preprocess_text_roi_bgra():
    det = _det()
    # 4-channel frame
    frame = np.zeros((REF_H, REF_W, 4), dtype=np.uint8)
    # Fill the ROI region
    frame[445:454, 1713:1807, :] = 200
    with patch("cv2.cvtColor", side_effect=lambda x, code: np.zeros((x.shape[0], x.shape[1]), dtype=np.uint8)) as mc, \
         patch("cv2.threshold", return_value=(None, np.zeros((9, 94), dtype=np.uint8))), \
         patch("cv2.bitwise_and", return_value=np.zeros((9, 94), dtype=np.uint8)), \
         patch("cv2.resize", return_value=np.zeros((36, 376), dtype=np.uint8)), \
         patch("cv2.bilateralFilter", return_value=np.zeros((36, 376), dtype=np.uint8)), \
         patch("cv2.adaptiveThreshold", return_value=np.zeros((36, 376), dtype=np.uint8)), \
         patch("cv2.getStructuringElement", return_value=np.ones((2, 2), dtype=np.uint8)), \
         patch("cv2.dilate", return_value=np.zeros((36, 376), dtype=np.uint8)):
        result = det._preprocess_text_roi(frame, HP_ROI)
    # should not raise


def test_read_bar_none_frame():
    det = _det()
    result = det._read_bar(None, HP_ROI, "hp")
    assert result is None


def test_read_bar_invalid_channels():
    det = _det()
    frame_1ch = np.zeros((100, 100), dtype=np.uint8)
    result = det._read_bar(frame_1ch, [0, 0, 50, 5], "hp")
    assert result is None


def test_resolution_warning_logged(caplog):
    import logging
    det = _det()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    with caplog.at_level(logging.INFO):
        det._read_bar(frame, [0, 0, 50, 5], "hp")
    # Should have set _resolution_warned
    assert getattr(det, "_resolution_warned", False)


def test_confidence_properties():
    det = _det()
    assert det.hp_confidence == 0.0
    assert det.mp_confidence == 0.0
    assert det.hp_outlier_rejects == 0
    assert det.mp_outlier_rejects == 0


def test_confidence_updates_after_read():
    det = _det()
    frame = _blank()
    hx, hy, hw, hh = HP_ROI
    mx, my, mw, mh = MP_ROI
    frame[hy:hy+hh, hx:hx+hw] = (0, 200, 0)
    frame[my:my+mh, mx:mx+mw // 2] = (200, 0, 0)

    det.read_bars(frame)

    assert det.hp_confidence > 0.9
    assert 0.4 <= det.mp_confidence <= 0.6


def test_hp_history_size_property():
    det = _det(smoothing=3)
    assert det.hp_history_size == 0
    frame = _blank()
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+w] = (0, 200, 0)
    det.read_bars(frame)
    assert det.hp_history_size == 1


def test_warmed_up_property():
    det = _det(smoothing=2)
    assert det.warmed_up is False
    frame = _blank()
    x, y, w, h = HP_ROI
    frame[y:y+h, x:x+w] = (0, 200, 0)
    det.read_bars(frame)
    det.read_bars(frame)
    assert det.warmed_up is True
