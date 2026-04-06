"""
Tests para src/hpmp_detector.py
Usa frames BGR sintéticos — sin OBS.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.hpmp_detector import HpMpDetector, HpMpConfig, NumericReading
from tests.conftest import HP_ROI, MP_ROI


# ─────────────────────────────────────────────────────────────────────────────
# Configuración con ROIs fijos (los mismos que conftest usa para pintar)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def detector() -> HpMpDetector:
    cfg = HpMpConfig(
        hp_roi=HP_ROI,
        mp_roi=MP_ROI,
        smoothing=1,      # sin suavizado para resultados deterministas
    )
    return HpMpDetector(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHpMpDetector:

    def test_hp100_mp100(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        hp, mp = detector.read_bars(hp100_mp100_frame)
        assert hp == 100, f"Esperaba HP=100, obtuvo {hp}"
        assert mp == 100, f"Esperaba MP=100, obtuvo {mp}"

    def test_hp0_mp0_blank(self, detector: HpMpDetector, hp0_mp0_frame: np.ndarray) -> None:
        hp, mp = detector.read_bars(hp0_mp0_frame)
        assert hp == 0, f"Esperaba HP=0, obtuvo {hp}"
        assert mp == 0, f"Esperaba MP=0, obtuvo {mp}"

    def test_hp75_approx(self, detector: HpMpDetector, hp75_mp50_frame: np.ndarray) -> None:
        hp, _ = detector.read_bars(hp75_mp50_frame)
        assert hp is not None
        # Tolerancia ±3 por redondeo entero en fill_frac + int truncation
        assert abs(hp - 75) <= 3, f"HP esperado ~75, obtuvo {hp}"

    def test_mp50_approx(self, detector: HpMpDetector, hp75_mp50_frame: np.ndarray) -> None:
        _, mp = detector.read_bars(hp75_mp50_frame)
        assert mp is not None
        assert abs(mp - 50) <= 3, f"MP esperado ~50, obtuvo {mp}"

    def test_read_hp_only(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        hp = detector.read_hp(hp100_mp100_frame)
        assert hp == 100

    def test_read_mp_only(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        mp = detector.read_mp(hp100_mp100_frame)
        assert mp == 100

    def test_various_fill_levels(self, detector: HpMpDetector) -> None:
        """Prueba varios niveles de relleno para confirmar la escala."""
        import numpy as np
        from tests.conftest import _blank_frame, _fill_roi_bgr

        for pct in (10, 25, 50, 75, 90, 100):
            frame = _blank_frame()
            _fill_roi_bgr(frame, HP_ROI, bgr=(0, 200, 0), fill_frac=pct / 100)
            hp = detector.read_hp(frame)
            assert hp is not None
            assert abs(hp - pct) <= 3, f"fill={pct}%  → leído={hp}%"

    def test_debug_overlay_returns_same_shape(
        self, detector: HpMpDetector, hp75_mp50_frame
    ) -> None:
        out = detector.debug_overlay(hp75_mp50_frame)
        assert out.shape == hp75_mp50_frame.shape

    def test_smoothing_averages(self, hp100_mp100_frame: np.ndarray,
                                hp0_mp0_frame) -> None:
        """Con smoothing=2, el promedio de 100%+0% debería ser ~50%."""
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=2,
                         outlier_threshold=0)  # disable outlier rejection for test
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)  # primera lectura → 100
        hp, _ = det.read_bars(hp0_mp0_frame)   # segunda    → media (100+0)=50
        assert hp is not None
        assert abs(hp - 50) <= 5

    def test_invalid_roi_returns_none(self, blank_frame: np.ndarray) -> None:
        """ROI fuera de los límites del frame → debe devolver None sin crash."""
        bad_cfg = HpMpConfig(hp_roi=[9900, 9900, 10, 10], mp_roi=[9900, 9920, 10, 10])
        det = HpMpDetector(bad_cfg)
        hp, mp = det.read_bars(blank_frame)
        # Puede devolver 0 o None — no debe levantar excepción
        assert hp in (0, None)
        assert mp in (0, None)

    def test_red_in_mp_roi_not_counted_as_mp(self, detector: HpMpDetector) -> None:
        """Un pixel rojo en el ROI de MP no debe ser contado como MP."""
        from tests.conftest import _blank_frame, _fill_roi_bgr
        frame = _blank_frame()
        _fill_roi_bgr(frame, MP_ROI, bgr=(0, 0, 220), fill_frac=1.0)  # rojo en MP ROI
        mp = detector.read_mp(frame)
        assert mp == 0, f"Rojo en MP ROI no debería detectarse como MP. Obtuvo {mp}"

    def test_blue_in_hp_roi_not_counted_as_hp(self, detector: HpMpDetector) -> None:
        """Un pixel azul en el ROI de HP no debe ser contado como HP."""
        from tests.conftest import _blank_frame, _fill_roi_bgr
        frame = _blank_frame()
        _fill_roi_bgr(frame, HP_ROI, bgr=(220, 0, 0), fill_frac=1.0)  # azul en HP ROI
        hp = detector.read_hp(frame)
        assert hp == 0, f"Azul en HP ROI no debería detectarse como HP. Obtuvo {hp}"


# ─────────────────────────────────────────────────────────────────────────────
# reset_history()
# ─────────────────────────────────────────────────────────────────────────────

class TestHpMpResetHistory:

    def test_initial_histories_empty(self, detector: HpMpDetector) -> None:
        assert len(detector._hp_history) == 0
        assert len(detector._mp_history) == 0

    def test_reset_clears_histories(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        from src.hpmp_detector import HpMpConfig
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.read_bars(hp100_mp100_frame)
        assert len(det._hp_history) > 0
        det.reset_history()
        assert len(det._hp_history) == 0
        assert len(det._mp_history) == 0

    def test_reset_clears_last_readings(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        assert detector.last_hp is not None
        detector.reset_history()
        assert detector.last_hp is None
        assert detector.last_mp is None

    def test_reset_on_fresh_detector_does_not_raise(self, detector: HpMpDetector) -> None:
        detector.reset_history()  # should not raise
        assert len(detector._hp_history) == 0

    def test_readings_continue_after_reset(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        detector.reset_history()
        hp, mp = detector.read_bars(hp100_mp100_frame)
        assert hp == 100
        assert mp == 100


# ─────────────────────────────────────────────────────────────────────────────
# last_hp / last_mp properties
# ─────────────────────────────────────────────────────────────────────────────

class TestHpMpLastReadings:

    def test_none_before_first_read(self, detector: HpMpDetector) -> None:
        assert detector.last_hp is None
        assert detector.last_mp is None

    def test_hp_set_after_read(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        assert detector.last_hp == 100

    def test_mp_set_after_read(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        assert detector.last_mp == 100

    def test_last_values_survive_second_read(self, detector: HpMpDetector,
                                              hp100_mp100_frame, hp75_mp50_frame) -> None:
        detector.read_bars(hp100_mp100_frame)
        detector.read_bars(hp75_mp50_frame)
        # After second read, last_hp should reflect the second frame (~75)
        assert detector.last_hp is not None
        assert abs(detector.last_hp - 75) <= 3

    def test_last_hp_is_int_or_none(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        assert isinstance(detector.last_hp, int)

    def test_last_mp_is_int_or_none(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        assert isinstance(detector.last_mp, int)


# ─────────────────────────────────────────────────────────────────────────────
# update_config()
# ─────────────────────────────────────────────────────────────────────────────

class TestHpMpUpdateConfig:

    def test_config_is_replaced(self, detector: HpMpDetector) -> None:
        new_cfg = HpMpConfig(hp_roi=[0, 0, 10, 5], mp_roi=[0, 10, 10, 5], smoothing=1)
        detector.update_config(new_cfg)
        assert detector._cfg is new_cfg

    def test_history_cleared_on_update(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        from src.hpmp_detector import HpMpConfig
        cfg_s = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg_s)
        det.read_bars(hp100_mp100_frame)
        assert len(det._hp_history) > 0
        det.update_config(HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1))
        assert len(det._hp_history) == 0

    def test_last_readings_cleared_on_update(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        assert detector.last_hp == 100
        detector.update_config(HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI))
        assert detector.last_hp is None

    def test_new_smoothing_takes_effect(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        new_cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=5)
        detector.update_config(new_cfg)
        assert detector._cfg.smoothing == 5


# ─────────────────────────────────────────────────────────────────────────────
# HpMpDetector.set_smoothing
# ─────────────────────────────────────────────────────────────────────────────

class TestSetSmoothing:

    def test_updates_config_smoothing(self, detector: HpMpDetector) -> None:
        detector.set_smoothing(5)
        assert detector._cfg.smoothing == 5

    def test_set_to_one_disables(self, detector: HpMpDetector) -> None:
        detector.set_smoothing(1)
        assert detector._cfg.smoothing == 1

    def test_below_one_raises(self, detector: HpMpDetector) -> None:
        with pytest.raises(ValueError):
            detector.set_smoothing(0)

    def test_trims_existing_history(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        # Build a 3-element history first
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=5)
        det = HpMpDetector(cfg)
        for _ in range(3):
            det.read_bars(hp100_mp100_frame)
        det.set_smoothing(2)
        assert len(det._hp_history) <= 2
        assert len(det._mp_history) <= 2

    def test_negative_raises(self, detector: HpMpDetector) -> None:
        with pytest.raises(ValueError):
            detector.set_smoothing(-3)


# ─────────────────────────────────────────────────────────────────────────────
# HpMpDetector.is_critical
# ─────────────────────────────────────────────────────────────────────────────

class TestIsCritical:

    def test_not_critical_when_never_read(self, detector: HpMpDetector) -> None:
        assert detector.is_critical(50) is False

    def test_critical_hp_below_threshold(self, detector: HpMpDetector, hp0_mp0_frame: np.ndarray) -> None:
        detector.read_bars(hp0_mp0_frame)
        assert detector.is_critical(50) is True

    def test_not_critical_hp_above_threshold(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        assert detector.is_critical(50) is False

    def test_critical_mp_check(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        # HP is 100% (not critical at threshold=50), but mp_threshold=101 forces MP critical
        assert detector.is_critical(hp_threshold=50, mp_threshold=101) is True

    def test_mp_check_disabled_when_zero(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        # mp_threshold=0 means MP check is off
        assert detector.is_critical(hp_threshold=50, mp_threshold=0) is False

    def test_returns_bool(self, detector: HpMpDetector) -> None:
        assert isinstance(detector.is_critical(50), bool)


# ─────────────────────────────────────────────────────────────────────────────
# HpMpDetector.stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsSnapshot:

    def test_returns_dict(self, detector: HpMpDetector) -> None:
        assert isinstance(detector.stats_snapshot(), dict)

    def test_initial_last_hp_none(self, detector: HpMpDetector) -> None:
        assert detector.stats_snapshot()["last_hp"] is None

    def test_last_hp_reflected_after_read(self, detector: HpMpDetector, hp100_mp100_frame: np.ndarray) -> None:
        detector.read_bars(hp100_mp100_frame)
        snap = detector.stats_snapshot()
        assert snap["last_hp"] == 100

    def test_all_keys_present(self, detector: HpMpDetector) -> None:
        snap = detector.stats_snapshot()
        for key in ("last_hp", "last_mp", "hp_history_len", "mp_history_len", "smoothing"):
            assert key in snap, f"Missing key: {key}"

    def test_smoothing_value_matches_config(self, detector: HpMpDetector) -> None:
        assert detector.stats_snapshot()["smoothing"] == detector._cfg.smoothing

    def test_history_len_grows_with_reads(
        self, hp100_mp100_frame) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=5)
        det = HpMpDetector(cfg)
        for i in range(3):
            det.read_bars(hp100_mp100_frame)
        snap = det.stats_snapshot()
        assert snap["hp_history_len"] == 3
        assert snap["mp_history_len"] == 3


class TestHpMpHistoryExtras:

    def test_has_history_false_initially(self, detector: HpMpDetector) -> None:
        assert detector.has_history is False

    def test_has_history_true_after_read(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.has_history is True

    def test_has_history_false_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.has_history is False

    def test_average_hp_none_initially(self, detector: HpMpDetector) -> None:
        assert detector.average_hp is None

    def test_average_mp_none_initially(self, detector: HpMpDetector) -> None:
        assert detector.average_mp is None

    def test_average_hp_after_reads(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.average_hp is not None

    def test_average_mp_after_reads(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.average_mp is not None

    def test_average_hp_returns_float(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert isinstance(det.average_hp, float)

    def test_average_mp_returns_float(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert isinstance(det.average_mp, float)

    def test_average_hp_none_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.average_hp is None

    def test_average_mp_none_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.average_mp is None


class TestHpMpRatios:

    def test_hp_ratio_none_initially(self, detector: HpMpDetector) -> None:
        assert detector.hp_ratio is None

    def test_mp_ratio_none_initially(self, detector: HpMpDetector) -> None:
        assert detector.mp_ratio is None

    def test_hp_ratio_after_read(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.hp_ratio is not None

    def test_mp_ratio_after_read(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.mp_ratio is not None

    def test_hp_ratio_is_normalised(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.hp_ratio is not None
        assert 0.0 <= det.hp_ratio <= 1.0

    def test_mp_ratio_is_normalised(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.mp_ratio is not None
        assert 0.0 <= det.mp_ratio <= 1.0

    def test_hp_ratio_returns_float(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert isinstance(det.hp_ratio, float)

    def test_mp_ratio_returns_float(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert isinstance(det.mp_ratio, float)

    def test_hp_ratio_none_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.hp_ratio is None

    def test_mp_ratio_none_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.mp_ratio is None


# ─────────────────────────────────────────────────────────────────────────────
# has_hp / has_mp
# ─────────────────────────────────────────────────────────────────────────────

class TestHasHpHasMp:

    def test_has_hp_false_initially(self, detector) -> None:
        assert detector.has_hp is False

    def test_has_mp_false_initially(self, detector) -> None:
        assert detector.has_mp is False

    def test_has_hp_true_after_read(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.has_hp is True

    def test_has_mp_true_after_read(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.has_mp is True

    def test_has_hp_false_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.has_hp is False

    def test_has_mp_false_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.has_mp is False

    def test_has_hp_returns_bool(self, detector) -> None:
        assert isinstance(detector.has_hp, bool)

    def test_has_mp_returns_bool(self, detector) -> None:
        assert isinstance(detector.has_mp, bool)

    def test_has_hp_consistent_with_last_hp(self, detector) -> None:
        detector._last_hp = 80
        assert detector.has_hp == (detector.last_hp is not None)


# ─────────────────────────────────────────────────────────────────────────────
# hp_history_size / mp_history_size
# ─────────────────────────────────────────────────────────────────────────────

class TestHistorySize:

    def test_hp_history_size_zero_initially(self, detector) -> None:
        assert detector.hp_history_size == 0

    def test_mp_history_size_zero_initially(self, detector) -> None:
        assert detector.mp_history_size == 0

    def test_hp_history_size_after_read(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        assert det.hp_history_size >= 1

    def test_hp_history_size_zero_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.hp_history_size == 0

    def test_mp_history_size_zero_after_reset(self, hp100_mp100_frame: np.ndarray) -> None:
        cfg = HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=3)
        det = HpMpDetector(cfg)
        det.read_bars(hp100_mp100_frame)
        det.reset_history()
        assert det.mp_history_size == 0

    def test_history_size_returns_int(self, detector) -> None:
        assert isinstance(detector.hp_history_size, int)
        assert isinstance(detector.mp_history_size, int)

    def test_consistent_with_internal_list(self, detector) -> None:
        detector._hp_history = [50, 60, 70]
        assert detector.hp_history_size == 3


# ─────────────────────────────────────────────────────────────────────────────
# NumericReading dataclass
# ─────────────────────────────────────────────────────────────────────────────

import time as _time


class TestNumericReading:

    def test_default_all_none(self) -> None:
        nr = NumericReading()
        assert nr.hp is None
        assert nr.mp is None
        assert nr.hp_max is None
        assert nr.mp_max is None

    def test_hp_pct_none_when_hp_none(self) -> None:
        nr = NumericReading(hp=None, hp_max=850)
        assert nr.hp_pct is None

    def test_hp_pct_none_when_hp_max_none(self) -> None:
        nr = NumericReading(hp=512, hp_max=None)
        assert nr.hp_pct is None

    def test_hp_pct_correct(self) -> None:
        nr = NumericReading(hp=500, hp_max=1000)
        assert nr.hp_pct == 50.0

    def test_hp_pct_rounding(self) -> None:
        nr = NumericReading(hp=512, hp_max=850)
        assert nr.hp_pct == round(512 * 100 / 850, 1)

    def test_mp_pct_correct(self) -> None:
        nr = NumericReading(mp=300, mp_max=600)
        assert nr.mp_pct == 50.0

    def test_mp_pct_none_when_mp_max_zero(self) -> None:
        nr = NumericReading(mp=0, mp_max=0)
        assert nr.mp_pct is None  # division by zero guard

    def test_age_increases_over_time(self) -> None:
        nr = NumericReading(timestamp=_time.monotonic() - 2.0)
        assert nr.age() >= 2.0

    def test_is_stale_false_fresh(self) -> None:
        nr = NumericReading(timestamp=_time.monotonic())
        assert nr.is_stale(5.0) is False

    def test_is_stale_true_old(self) -> None:
        nr = NumericReading(timestamp=_time.monotonic() - 10.0)
        assert nr.is_stale(5.0) is True

    def test_is_stale_default_5s(self) -> None:
        nr = NumericReading(timestamp=_time.monotonic() - 6.0)
        assert nr.is_stale() is True


# ─────────────────────────────────────────────────────────────────────────────
# HpMpConfig — nuevos campos OCR
# ─────────────────────────────────────────────────────────────────────────────

class TestHpMpConfigOcrFields:

    def test_hp_text_roi_default_exists(self) -> None:
        cfg = HpMpConfig()
        assert len(cfg.hp_text_roi) == 4

    def test_mp_text_roi_default_exists(self) -> None:
        cfg = HpMpConfig()
        assert len(cfg.mp_text_roi) == 4

    def test_ocr_confidence_default(self) -> None:
        cfg = HpMpConfig()
        assert 0.0 <= cfg.ocr_confidence <= 1.0

    def test_numeric_update_interval_default(self) -> None:
        cfg = HpMpConfig()
        assert cfg.numeric_update_interval == 0.0

    def test_custom_ocr_confidence_stored(self) -> None:
        cfg = HpMpConfig(ocr_confidence=0.7)
        assert cfg.ocr_confidence == 0.7

    def test_custom_numeric_update_interval_stored(self) -> None:
        cfg = HpMpConfig(numeric_update_interval=2.0)
        assert cfg.numeric_update_interval == 2.0


# ─────────────────────────────────────────────────────────────────────────────
# read_numeric_hpmp() — mock del OCR backend
# ─────────────────────────────────────────────────────────────────────────────

from tests.conftest import HP_ROI, MP_ROI, _blank_frame
from unittest.mock import patch, MagicMock


def _det_no_file() -> HpMpDetector:
    """Detector con config minimal (sin leer hpmp_config.json)."""
    return HpMpDetector(HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1))


class TestReadNumericHpmp:

    def test_returns_numeric_reading(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", return_value=(512, 850)):
            result = det.read_numeric_hpmp(frame)
        assert isinstance(result, NumericReading)

    def test_hp_mp_values_populated(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", side_effect=[(512, 850), (300, 600)]):
            result = det.read_numeric_hpmp(frame)
        assert result.hp == 512
        assert result.hp_max == 850
        assert result.mp == 300
        assert result.mp_max == 600

    def test_hp_pct_computed_correctly(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", side_effect=[(500, 1000), (1, 1)]):
            result = det.read_numeric_hpmp(frame)
        assert result.hp_pct == 50.0

    def test_ocr_failure_returns_none_fields(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", return_value=(None, None)):
            result = det.read_numeric_hpmp(frame)
        assert result.hp is None
        assert result.mp is None
        assert result.hp_max is None

    def test_timestamp_set(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        before = _time.monotonic()
        with patch.object(det, "_ocr_bar_text", return_value=(100, 1000)):
            result = det.read_numeric_hpmp(frame)
        assert result.timestamp >= before

    def test_single_value_without_max(self) -> None:
        """OCR que devuelve solo current sin max."""
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", side_effect=[(750, None), (200, None)]):
            result = det.read_numeric_hpmp(frame)
        assert result.hp == 750
        assert result.hp_max is None
        assert result.hp_pct is None   # no se puede calcular sin max


# ─────────────────────────────────────────────────────────────────────────────
# hp_exact / mp_exact / hp_max / mp_max properties
# ─────────────────────────────────────────────────────────────────────────────

class TestExactProperties:

    def _inject(self, det: HpMpDetector, **kw: object) -> None:
        """Inyecta directamente un NumericReading en el caché."""
        with det._numeric_lock:
            det._numeric = NumericReading(
                timestamp=_time.monotonic(), **kw  # type: ignore[arg-type]
            )

    def test_hp_exact_none_when_no_numeric(self) -> None:
        det = _det_no_file()
        assert det.hp_exact is None

    def test_mp_exact_none_when_no_numeric(self) -> None:
        det = _det_no_file()
        assert det.mp_exact is None

    def test_hp_max_none_when_no_numeric(self) -> None:
        det = _det_no_file()
        assert det.hp_max is None

    def test_mp_max_none_when_no_numeric(self) -> None:
        det = _det_no_file()
        assert det.mp_max is None

    def test_hp_exact_from_cache(self) -> None:
        det = _det_no_file()
        self._inject(det, hp=512, hp_max=850, mp=300, mp_max=600)
        assert det.hp_exact == 512

    def test_mp_exact_from_cache(self) -> None:
        det = _det_no_file()
        self._inject(det, hp=512, hp_max=850, mp=300, mp_max=600)
        assert det.mp_exact == 300

    def test_hp_max_from_cache(self) -> None:
        det = _det_no_file()
        self._inject(det, hp=512, hp_max=850, mp=300, mp_max=600)
        assert det.hp_max == 850

    def test_mp_max_from_cache(self) -> None:
        det = _det_no_file()
        self._inject(det, hp=512, hp_max=850, mp=300, mp_max=600)
        assert det.mp_max == 600

    def test_hp_pct_exact_uses_ocr_when_fresh(self) -> None:
        det = _det_no_file()
        self._inject(det, hp=500, hp_max=1000, mp=1, mp_max=1)
        assert det.hp_pct_exact == 50.0

    def test_hp_pct_exact_fallback_to_bar(self, hp100_mp100_frame: np.ndarray) -> None:
        """Sin datos OCR, hp_pct_exact debe usar la última lectura de barra."""
        det = HpMpDetector(HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1))
        det.read_bars(hp100_mp100_frame)
        assert det._numeric is None     # no hay OCR
        assert det.hp_pct_exact == 100.0

    def test_mp_pct_exact_fallback_to_bar(self, hp100_mp100_frame: np.ndarray) -> None:
        det = HpMpDetector(HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1))
        det.read_bars(hp100_mp100_frame)
        assert det.mp_pct_exact == 100.0

    def test_hp_pct_exact_none_when_no_data(self) -> None:
        det = _det_no_file()
        assert det.hp_pct_exact is None

    def test_hp_pct_exact_falls_back_when_stale(self, hp100_mp100_frame: np.ndarray) -> None:
        """Datos OCR obsoletos (> 5 s) → fallback a barra."""
        det = HpMpDetector(HpMpConfig(hp_roi=HP_ROI, mp_roi=MP_ROI, smoothing=1))
        det.read_bars(hp100_mp100_frame)
        # Inyectar dato OCR obsoleto
        with det._numeric_lock:
            det._numeric = NumericReading(
                hp=300, hp_max=1000, mp=1, mp_max=1,
                timestamp=_time.monotonic() - 10.0,  # 10 s ago
            )
        # Debe caer en el valor de barra (100) no en el OCR obsoleto (30.0)
        assert det.hp_pct_exact == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# start_numeric_reader / stop_numeric_reader
# ─────────────────────────────────────────────────────────────────────────────

class TestNumericReaderThread:

    def test_not_running_initially(self) -> None:
        det = _det_no_file()
        assert det.numeric_reader_running is False

    def test_running_after_start(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", return_value=(100, 1000)):
            det.start_numeric_reader(lambda: frame, interval=0.05)
            _time.sleep(0.15)  # dar tiempo a que arranque
            assert det.numeric_reader_running is True
            det.stop_numeric_reader()

    def test_not_running_after_stop(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", return_value=(100, 1000)):
            det.start_numeric_reader(lambda: frame, interval=0.05)
            det.stop_numeric_reader()
        assert det.numeric_reader_running is False

    def test_numeric_cache_populated_by_thread(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", return_value=(512, 850)):
            det.start_numeric_reader(lambda: frame, interval=0.05)
            _time.sleep(0.2)
            det.stop_numeric_reader()
        assert det.numeric is not None
        assert det.hp_exact == 512
        assert det.hp_max == 850

    def test_double_start_does_not_create_second_thread(self) -> None:
        det = _det_no_file()
        frame = _blank_frame()
        with patch.object(det, "_ocr_bar_text", return_value=(1, 1)):
            det.start_numeric_reader(lambda: frame, interval=0.05)
            t1 = det._numeric_thread
            det.start_numeric_reader(lambda: frame, interval=0.05)  # segundo llamado
            t2 = det._numeric_thread
            det.stop_numeric_reader()
        assert t1 is t2, "No debe crear un segundo hilo si uno ya está activo"

    def test_frame_getter_returning_none_does_not_crash(self) -> None:
        det = _det_no_file()
        det.start_numeric_reader(lambda: None, interval=0.05)
        _time.sleep(0.15)
        det.stop_numeric_reader()
        # numeric may still be None (frame was None), that’s fine
        assert det.numeric_reader_running is False


# ─────────────────────────────────────────────────────────────────────────────
# stats_snapshot() — nuevas claves
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsSnapshotOcr:

    def test_ocr_keys_present(self) -> None:
        det = _det_no_file()
        snap = det.stats_snapshot()
        for key in ("hp_exact", "mp_exact", "hp_max", "mp_max",
                    "numeric_age_s", "numeric_reader_running"):
            assert key in snap, f"Falta clave: {key}"

    def test_ocr_keys_none_when_no_numeric(self) -> None:
        det = _det_no_file()
        snap = det.stats_snapshot()
        assert snap["hp_exact"] is None
        assert snap["mp_exact"] is None
        assert snap["hp_max"] is None
        assert snap["mp_max"] is None
        assert snap["numeric_age_s"] is None

    def test_ocr_keys_populated_from_cache(self) -> None:
        det = _det_no_file()
        with det._numeric_lock:
            det._numeric = NumericReading(
                hp=512, hp_max=850, mp=300, mp_max=600,
                timestamp=_time.monotonic(),
            )
        snap = det.stats_snapshot()
        assert snap["hp_exact"] == 512
        assert snap["mp_exact"] == 300
        assert snap["hp_max"] == 850
        assert snap["mp_max"] == 600
        assert snap["numeric_age_s"] is not None
        assert snap["numeric_age_s"] >= 0.0

    def test_numeric_reader_running_false_initially(self) -> None:
        det = _det_no_file()
        assert det.stats_snapshot()["numeric_reader_running"] is False


# ─────────────────────────────────────────────────────────────────────────────
# reset_history() limpia el caché OCR
# ─────────────────────────────────────────────────────────────────────────────

class TestResetHistoryClearsNumeric:

    def test_reset_clears_numeric_cache(self) -> None:
        det = _det_no_file()
        with det._numeric_lock:
            det._numeric = NumericReading(hp=512, timestamp=_time.monotonic())
        det.reset_history()
        assert det.numeric is None

    def test_reset_on_fresh_detector_no_crash(self) -> None:
        det = _det_no_file()
        det.reset_history()  # should not raise
        assert det.numeric is None

    def test_hp_exact_none_after_reset(self) -> None:
        det = _det_no_file()
        with det._numeric_lock:
            det._numeric = NumericReading(hp=100, timestamp=_time.monotonic())
        det.reset_history()
        assert det.hp_exact is None


# ─────────────────────────────────────────────────────────────────────────────
# HpMpDetector.has_both
# ─────────────────────────────────────────────────────────────────────────────

class TestHpMpHasBoth:

    def test_false_before_any_reading(self, detector) -> None:
        assert detector.has_both is False

    def test_false_when_only_hp_read(self, detector) -> None:
        detector._last_hp = 80
        assert detector.has_both is False

    def test_false_when_only_mp_read(self, detector) -> None:
        detector._last_mp = 60
        assert detector.has_both is False

    def test_true_when_both_read(self, detector) -> None:
        detector._last_hp = 80
        detector._last_mp = 60
        assert detector.has_both is True

    def test_returns_bool(self, detector) -> None:
        assert isinstance(detector.has_both, bool)


# ─────────────────────────────────────────────────────────────────────────────
# HpMpDetector.is_reading_stable
# ─────────────────────────────────────────────────────────────────────────────

class TestHpMpIsReadingStable:

    def test_false_when_no_history(self, detector) -> None:
        assert detector.is_reading_stable is False

    def test_false_when_partial_history(self, detector) -> None:
        smoothing = detector._cfg.smoothing
        detector._hp_history = list(range(smoothing - 1))
        assert detector.is_reading_stable is False

    def test_true_when_history_equals_smoothing(self, detector) -> None:
        smoothing = detector._cfg.smoothing
        detector._hp_history = list(range(smoothing))
        assert detector.is_reading_stable is True

    def test_true_when_history_exceeds_smoothing(self, detector) -> None:
        smoothing = detector._cfg.smoothing
        detector._hp_history = list(range(smoothing + 3))
        assert detector.is_reading_stable is True

    def test_returns_bool(self, detector) -> None:
        assert isinstance(detector.is_reading_stable, bool)


# ─────────────────────────────────────────────────────────────────────────────
# auto_calibrate / _detect_bar
# ─────────────────────────────────────────────────────────────────────────────

def _make_bar_frame(
    hp_row: int = 300,
    mp_row: int = 320,
    bar_height: int = 8,
    frame_w: int = 1920,
    frame_h: int = 1080,
) -> np.ndarray:
    """Synthetic 1920×1080 BGR frame with a green HP bar and a blue MP bar
    painted at custom positions (not the default ROIs)."""
    frame = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)
    # HP bar: saturated green pixels spanning 80 % of frame width
    hp_x0 = frame_w // 10
    hp_x1 = frame_w * 9 // 10
    frame[hp_row: hp_row + bar_height, hp_x0:hp_x1] = (0, 200, 0)   # BGR green
    # MP bar: blue pixels spanning 85 % of frame width
    mp_x0 = frame_w // 12
    mp_x1 = frame_w * 11 // 12
    frame[mp_row: mp_row + bar_height, mp_x0:mp_x1] = (220, 0, 0)   # BGR blue
    return frame


class TestAutoCalibrate:

    def test_returns_true_when_both_bars_found(self) -> None:
        det = HpMpDetector(HpMpConfig(smoothing=1))
        frame = _make_bar_frame()
        assert det.auto_calibrate(frame) is True

    def test_hp_roi_updated_to_bar_position(self) -> None:
        det = HpMpDetector(HpMpConfig(smoothing=1))
        frame = _make_bar_frame(hp_row=300, mp_row=360)
        det.auto_calibrate(frame)
        # Reference-space y coordinate of HP bar should be near 300
        # (frame is 1080px tall, ref is 1080 → sy=1.0, so y ≈ 300)
        _, y, _, h = det._cfg.hp_roi
        assert abs(y - 300) <= 5, f"HP ROI y={y} should be near 300"
        assert h >= 1

    def test_mp_roi_updated_to_bar_position(self) -> None:
        det = HpMpDetector(HpMpConfig(smoothing=1))
        frame = _make_bar_frame(hp_row=300, mp_row=360)
        det.auto_calibrate(frame)
        _, y, _, h = det._cfg.mp_roi
        assert abs(y - 360) <= 5, f"MP ROI y={y} should be near 360"
        assert h >= 1

    def test_returns_false_on_black_frame(self) -> None:
        det = HpMpDetector(HpMpConfig(smoothing=1))
        black = np.zeros((1080, 1920, 3), dtype=np.uint8)
        assert det.auto_calibrate(black) is False

    def test_returns_false_on_none_frame(self) -> None:
        det = HpMpDetector(HpMpConfig(smoothing=1))
        assert det.auto_calibrate(None) is False  # type: ignore[arg-type]

    def test_history_cleared_after_calibrate(self) -> None:
        det = HpMpDetector(HpMpConfig(smoothing=3))
        det._hp_history = type(det._hp_history)([80, 90, 100])
        det._last_hp = 100
        frame = _make_bar_frame()
        det.auto_calibrate(frame)
        assert len(det._hp_history) == 0
        assert det._last_hp is None

    def test_detect_bar_returns_none_when_mask_empty(self) -> None:
        mask = np.zeros((100, 200), dtype=bool)
        result = HpMpDetector._detect_bar(mask, min_width=20)
        assert result is None

    def test_detect_bar_finds_horizontal_band(self) -> None:
        mask = np.zeros((200, 400), dtype=bool)
        mask[50:55, 10:350] = True  # 340-pixel-wide band at rows 50–54
        result = HpMpDetector._detect_bar(mask, min_width=50)
        assert result is not None
        x, y, w, h = result
        assert abs(y - 50) <= 2
        assert w >= 300

    def test_calibrated_roi_reads_bar_correctly(self) -> None:
        """After auto_calibrate the detector must correctly read 100% from the bar."""
        det = HpMpDetector(HpMpConfig(smoothing=1))
        frame = _make_bar_frame(hp_row=300, mp_row=360)
        assert det.auto_calibrate(frame)
        hp, mp = det.read_bars(frame)
        assert hp is not None and hp >= 70, f"HP should be high after calibration, got {hp}"
        assert mp is not None and mp >= 70, f"MP should be high after calibration, got {mp}"
