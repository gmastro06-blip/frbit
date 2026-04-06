"""
Tests para src/combat_manager.py — BattleDetector.
Sin OBS ni Tibia. Usa templates sintéticos en memoria.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.combat_manager import BattleDetector, CombatConfig, TrackedCombatTarget


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(templates_dir: str | Path, confidence: float = 0.50) -> CombatConfig:
    return CombatConfig(
        battle_list_roi=[0, 0, 1920, 1080],  # ROI = frame completo (simplifica tests)
        templates_dir=str(templates_dir),
        confidence=confidence,
    )


def _blank_frame(w: int = 320, h: int = 240) -> np.ndarray:
    """Frame BGR negro pequeño para tests rápidos."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def _grey_patch(value: int, size: int = 20) -> np.ndarray:
    """Parche gris de *size*×*size* con valor uniforme."""
    return np.full((size, size), value, dtype=np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBattleDetectorNoTemplates:
    """Sin templates cargados — detect() siempre devuelve []."""

    def test_detect_no_templates_empty(self, tmp_path: Path):
        cfg = _make_config(tmp_path)        # directorio vacío
        det = BattleDetector(cfg)
        assert det._templates == []
        result = det.detect(_blank_frame())
        assert result == []

    def test_detect_none_frame(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        det = BattleDetector(cfg)
        result = det.detect(None)           # type: ignore[arg-type]
        assert result == []

    def test_templates_count_is_zero(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        det = BattleDetector(cfg)
        assert len(det._templates) == 0


class TestBattleDetectorWithTemplates:
    """Templates sintéticos guardados en tmp_path."""

    def _setup_detector(self, tmp_path: Path,
                        patch_value: int = 200,
                        patch_size: int = 15,
                        confidence: float = 0.85) -> tuple[BattleDetector, np.ndarray, str]:
        """
        Crea un detector con un template gris, e inserta ese template
        en un frame BGR para que sea detectable.
        Devuelve (detector, frame, nombre_template).
        """
        monsters_dir = tmp_path / "monsters"
        monsters_dir.mkdir(parents=True)

        # Guardar template como PNG
        tmpl_name = "test_monster"
        patch = _grey_patch(patch_value, patch_size)
        cv2.imwrite(str(monsters_dir / f"{tmpl_name}.png"), patch)

        cfg = _make_config(tmp_path, confidence=confidence)
        det = BattleDetector(cfg)

        # Crear frame con el template incrustado en una posición conocida
        frame = _blank_frame(320, 240)
        bx, by = 50, 60   # posición del template en el frame
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_frame[by : by + patch_size, bx : bx + patch_size] = patch_value
        # Convertir back a BGR
        frame = cv2.cvtColor(gray_frame, cv2.COLOR_GRAY2BGR)

        return det, frame, tmpl_name

    def test_template_detected(self, tmp_path: Path):
        det, frame, name = self._setup_detector(tmp_path)
        if not det._templates:
            pytest.skip("No se cargaron templates")
        results = det.detect(frame)
        assert len(results) > 0, "Debería detectar al menos un monstruo"

    def test_detection_name_matches(self, tmp_path: Path):
        det, frame, name = self._setup_detector(tmp_path)
        if not det._templates:
            pytest.skip("No se cargaron templates")
        results = det.detect(frame)
        if results:
            detected_names = [r[3] for r in results]
            assert name in detected_names

    def test_detection_confidence_in_range(self, tmp_path: Path):
        det, frame, _ = self._setup_detector(tmp_path)
        if not det._templates:
            pytest.skip("No se cargaron templates")
        for cx, cy, conf, _ in det.detect(frame):
            assert 0.0 <= conf <= 1.0

    def test_reload_adds_new_template(self, tmp_path: Path):
        monsters_dir = tmp_path / "monsters"
        monsters_dir.mkdir(parents=True)
        cfg = _make_config(tmp_path)
        det = BattleDetector(cfg)
        initial = len(det._templates)

        # Añadir un nuevo template y recargar
        new_patch = _grey_patch(120, 10)
        cv2.imwrite(str(monsters_dir / "new_monster.png"), new_patch)
        det.reload()
        assert len(det._templates) >= initial + 1

    def test_template_too_large_skipped(self, tmp_path: Path):
        """Template más grande que el ROI no debe causar crash."""
        monsters_dir = tmp_path / "monsters"
        monsters_dir.mkdir(parents=True)

        # Template 400×400 — mayor que el frame 320×240
        huge = np.full((400, 400), 200, dtype=np.uint8)
        cv2.imwrite(str(monsters_dir / "huge.png"), huge)

        cfg = _make_config(tmp_path)
        det = BattleDetector(cfg)
        result = det.detect(_blank_frame(320, 240))
        # No debe crashear, simplemente ignorar
        assert isinstance(result, list)

    def test_skip_top_removes_top_entries(self, tmp_path: Path):
        """skip_top=1 debe eliminar el primer resultado ordenado por y."""
        det, frame, _ = self._setup_detector(tmp_path)
        if not det._templates:
            pytest.skip("No se cargaron templates")

        det._cfg.skip_top = 0
        results_full = det.detect(frame)

        det._cfg.skip_top = 1
        results_skip = det.detect(frame)

        if len(results_full) > 0:
            assert len(results_skip) == max(0, len(results_full) - 1)

    def test_debug_save_creates_file(self, tmp_path: Path):
        det, frame, _ = self._setup_detector(tmp_path)
        out_path = str(tmp_path / "debug_out.png")
        det.debug_save(frame, out_path)
        assert Path(out_path).exists()


# ─────────────────────────────────────────────────────────────────────────────
# CombatConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatConfig:

    def test_default_values(self):
        cfg = CombatConfig()
        assert cfg.confidence > 0
        assert cfg.ref_width == 1920
        assert cfg.ref_height == 1080

    def test_save_and_load(self, tmp_path: Path):
        cfg = CombatConfig(confidence=0.77, hp_flee_pct=25)
        path = tmp_path / "combat_config.json"
        cfg.save(path)
        loaded = CombatConfig.load(path)
        assert abs(loaded.confidence - 0.77) < 1e-6
        assert loaded.hp_flee_pct == 25

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        cfg = CombatConfig.load(path)
        assert cfg.ref_width == 1920


# ─────────────────────────────────────────────────────────────────────────────
# CombatManager
# ─────────────────────────────────────────────────────────────────────────────

from unittest.mock import MagicMock
from src.combat_manager import CombatManager


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


def _make_manager(
    hp: int = 80,
    mp: int = 80,
    spells=None,
    hp_flee_pct: int = 0,
) -> CombatManager:
    cfg = CombatConfig(
        attack_vk=0,
        hp_flee_pct=hp_flee_pct,
        spells=spells or [],
        check_interval=0.01,
    )
    cm = CombatManager(
        ctrl=_mock_ctrl(),
        hp_detector=_mock_hp_det(hp, mp),
        config=cfg,
    )
    return cm


class TestCombatManagerConstruction:

    def test_initial_not_running(self):
        cm = _make_manager()
        assert cm.is_in_combat is False

    def test_initial_kills_zero(self):
        cm = _make_manager()
        assert cm.kills == 0

    def test_initial_attacks_sent_zero(self):
        cm = _make_manager()
        assert cm.attacks_sent == 0

    def test_initial_last_hp_pct_none(self):
        cm = _make_manager()
        assert cm.last_hp_pct is None

    def test_initial_not_paused(self):
        cm = _make_manager()
        assert cm.is_paused is False

    def test_get_status_keys(self):
        cm = _make_manager()
        status = cm.get_status()
        for key in ("in_combat", "kills", "attacks", "hp_pct", "current_target"):
            assert key in status


class TestCombatManagerNotifyKill:

    def test_notify_kill_increments_kills(self):
        cm = _make_manager()
        cm.notify_kill()
        assert cm.kills == 1

    def test_notify_kill_twice(self):
        cm = _make_manager()
        cm.notify_kill()
        cm.notify_kill()
        assert cm.kills == 2

    def test_notify_kill_clears_combat(self):
        cm = _make_manager()
        cm._in_combat = True
        cm._current_target = (100, 200)
        cm.notify_kill()
        assert cm.is_in_combat is False
        assert cm._current_target is None


class TestCombatManagerResetKills:

    def test_reset_kills_zeros_counter(self):
        cm = _make_manager()
        cm.notify_kill()
        cm.notify_kill()
        cm.reset_kills()
        assert cm.kills == 0

    def test_reset_kills_zeros_attacks(self):
        cm = _make_manager()
        cm._attacks_sent = 42
        cm.reset_kills()
        assert cm.attacks_sent == 0


class TestCombatManagerPauseResume:

    def test_pause_sets_paused(self):
        cm = _make_manager()
        cm.pause()
        assert cm.is_paused is True

    def test_resume_clears_paused(self):
        cm = _make_manager()
        cm.pause()
        cm.resume()
        assert cm.is_paused is False


class TestCombatManagerUpdateConfig:

    def test_update_config_replaces_cfg(self):
        cm = _make_manager()
        new_cfg = CombatConfig(confidence=0.99, hp_flee_pct=50)
        cm.update_config(new_cfg)
        assert cm._cfg.confidence == pytest.approx(0.99)
        assert cm._cfg.hp_flee_pct == 50

    def test_update_config_rebuilds_detector(self):
        cm = _make_manager()
        old_det = cm._detector
        cm.update_config(CombatConfig())
        assert cm._detector is not old_det


class TestCombatManagerReadHpPct:

    def test_read_hp_pct_returns_hp_from_detector(self, tmp_path: Path):
        cm = _make_manager(hp=65)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = cm._read_hp_pct(frame)
        assert result == 65

    def test_read_hp_pct_returns_none_without_detector(self):
        cm = _make_manager()
        cm._hp = None
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = cm._read_hp_pct(frame)
        assert result is None

    def test_read_hp_pct_returns_none_on_exception(self):
        cm = _make_manager()
        bad_det = MagicMock()
        bad_det.read_bars.side_effect = RuntimeError("sensor error")
        cm._hp = bad_det
        result = cm._read_hp_pct(np.zeros((10, 10, 3), dtype=np.uint8))
        assert result is None


class TestCombatManagerCastSpells:

    def test_no_spells_does_not_press_key(self):
        cm = _make_manager(spells=[])
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(np.zeros((100, 100, 3), dtype=np.uint8))
        ctrl.press_key.assert_not_called()

    def test_spell_fires_when_mp_sufficient(self):
        spells = [{"vk": 0x71, "min_mp": 50, "cooldown": 0.0, "label": "exori"}]
        cm = _make_manager(hp=80, mp=80, spells=spells)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(np.zeros((100, 100, 3), dtype=np.uint8))
        ctrl.press_key.assert_called_with(0x71)

    def test_spell_blocked_when_mp_insufficient(self):
        spells = [{"vk": 0x71, "min_mp": 90, "cooldown": 0.0, "label": "exori"}]
        cm = _make_manager(hp=80, mp=40, spells=spells)  # mp=40 < min_mp=90
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(np.zeros((100, 100, 3), dtype=np.uint8))
        ctrl.press_key.assert_not_called()

    def test_spell_blocked_during_cooldown(self):
        import time
        spells = [{"vk": 0x71, "min_mp": 0, "cooldown": 9999.0, "label": "x"}]
        cm = _make_manager(mp=100, spells=spells)
        cm._spell_cds[0x71] = time.time()  # just cast
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(np.zeros((100, 100, 3), dtype=np.uint8))
        ctrl.press_key.assert_not_called()

    def test_reset_spell_cooldowns_allows_immediate_cast(self):
        import time
        spells = [{"vk": 0x71, "min_mp": 0, "cooldown": 0.5, "label": "x"}]
        cm = _make_manager(mp=100, spells=spells)
        cm._spell_cds[0x71] = time.monotonic()   # blocked (M1: monotonic clock)
        ctrl = _mock_ctrl()
        cm._ctrl = ctrl
        cm._cast_spells(np.zeros((100, 100, 3), dtype=np.uint8))
        ctrl.press_key.assert_not_called()
        cm.reset_spell_cooldowns()
        cm._cast_spells(np.zeros((100, 100, 3), dtype=np.uint8))
        ctrl.press_key.assert_called_with(0x71)

    def test_reset_spell_cooldowns_clears_dict(self):
        cm = _make_manager()
        cm._spell_cds = {0x71: 1234.0, 0x72: 5678.0}
        cm.reset_spell_cooldowns()
        assert cm._spell_cds == {}


class TestCombatManagerGetStatus:

    def test_status_reflects_notify_kill(self):
        cm = _make_manager()
        cm.notify_kill()
        status = cm.get_status()
        assert status["kills"] == 1
        assert status["in_combat"] is False
        assert status["current_target"] is None

    def test_status_reflects_attacks_sent(self):
        cm = _make_manager()
        cm._attacks_sent = 7
        status = cm.get_status()
        assert status["attacks"] == 7


class TestCombatManagerTargetTracking:

    def test_current_target_name_reflects_tracked_target(self):
        cm = _make_manager()
        cm._tracked_target = TrackedCombatTarget(
            name="Troll",
            position=(100, 200),
            acquired_at=time.monotonic(),
            last_seen_at=time.monotonic(),
        )
        assert cm.current_target_name == "Troll"

    def test_notify_kill_records_last_target_result(self):
        cm = _make_manager()
        acquired_at = time.monotonic() - 1.0
        cm._current_target = (100, 200)
        cm._tracked_target = TrackedCombatTarget(
            name="Troll",
            position=(100, 200),
            acquired_at=acquired_at,
            last_seen_at=acquired_at,
        )

        cm.notify_kill("Troll")

        result = cm.last_target_result
        assert result is not None
        assert result["name"] == "Troll"
        assert result["reason"] == "external_notify"
        assert result["position"] == (100, 200)


# ─────────────────────────────────────────────────────────────────────────────
# CombatManager.is_running
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerIsRunning:

    def test_false_before_start(self):
        cm = _make_manager()
        assert cm.is_running is False

    def test_true_after_start(self):
        cm = _make_manager()
        cm.start()
        assert cm.is_running is True
        cm.stop()

    def test_false_after_stop(self):
        cm = _make_manager()
        cm.start()
        cm.stop()
        assert cm.is_running is False

    def test_returns_bool(self):
        cm = _make_manager()
        assert isinstance(cm.is_running, bool)


# ─────────────────────────────────────────────────────────────────────────────
# CombatManager.set_log_callback / _log
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerSetLogCallback:

    def test_no_callback_defaults_to_print(self, capsys):
        cm = _make_manager()
        cm._log("hello")
        captured = capsys.readouterr()
        assert "hello" in captured.out

    def test_callback_receives_message(self):
        cm = _make_manager()
        msgs: list[str] = []
        cm.set_log_callback(msgs.append)
        cm._log("test message")
        assert msgs == ["test message"]

    def test_callback_suppresses_print(self, capsys):
        cm = _make_manager()
        cm.set_log_callback(lambda m: None)
        cm._log("silent")
        captured = capsys.readouterr()
        assert "silent" not in captured.out

    def test_multiple_messages_routed_to_callback(self):
        cm = _make_manager()
        msgs: list[str] = []
        cm.set_log_callback(msgs.append)
        for i in range(5):
            cm._log(f"msg{i}")
        assert len(msgs) == 5

    def test_callback_can_be_replaced(self):
        cm = _make_manager()
        sink_a: list[str] = []
        sink_b: list[str] = []
        cm.set_log_callback(sink_a.append)
        cm._log("a")
        cm.set_log_callback(sink_b.append)
        cm._log("b")
        assert sink_a == ["a"]
        assert sink_b == ["b"]


# ─────────────────────────────────────────────────────────────────────────────
# CombatManager.stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerStatsSnapshot:

    def test_returns_dict(self):
        cm = _make_manager()
        snap = cm.stats_snapshot()
        assert isinstance(snap, dict)

    def test_initial_kills_zero(self):
        cm = _make_manager()
        assert cm.stats_snapshot()["kills"] == 0

    def test_kills_reflected(self):
        cm = _make_manager()
        cm._kills = 4
        assert cm.stats_snapshot()["kills"] == 4

    def test_attacks_reflected(self):
        cm = _make_manager()
        cm._attacks_sent = 7
        assert cm.stats_snapshot()["attacks"] == 7

    def test_paused_flag_reflected(self):
        cm = _make_manager()
        cm.pause()
        snap = cm.stats_snapshot()
        assert snap["paused"] is True

    def test_all_expected_keys_present(self):
        cm = _make_manager()
        snap = cm.stats_snapshot()
        for key in ("kills", "attacks", "hp_pct", "in_combat", "paused",
                    "spells", "active_cooldowns"):
            assert key in snap, f"Missing key: {key}"


# ─────────────────────────────────────────────────────────────────────────────
# CombatManager.spells_count / add_spell / remove_spell
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerSpellManagement:

    def test_spells_count_empty(self):
        cm = _make_manager()
        assert cm.spells_count == 0

    def test_add_spell_increments_count(self):
        cm = _make_manager()
        cm.add_spell({"vk": 0x71, "label": "exura"})
        assert cm.spells_count == 1

    def test_add_multiple_spells(self):
        cm = _make_manager()
        cm.add_spell({"vk": 0x71})
        cm.add_spell({"vk": 0x72})
        assert cm.spells_count == 2

    def test_add_spell_without_vk_raises(self):
        import pytest
        cm = _make_manager()
        with pytest.raises(ValueError):
            cm.add_spell({"label": "no_vk"})

    def test_remove_spell_returns_true_when_found(self):
        cm = _make_manager()
        cm.add_spell({"vk": 0x71})
        assert cm.remove_spell(0x71) is True

    def test_remove_spell_decrements_count(self):
        cm = _make_manager()
        cm.add_spell({"vk": 0x71})
        cm.remove_spell(0x71)
        assert cm.spells_count == 0

    def test_remove_spell_returns_false_when_missing(self):
        cm = _make_manager()
        assert cm.remove_spell(0xFF) is False

    def test_remove_only_matching_vk(self):
        cm = _make_manager()
        cm.add_spell({"vk": 0x71})
        cm.add_spell({"vk": 0x72})
        cm.remove_spell(0x71)
        assert cm.spells_count == 1
        assert cm._cfg.spells[0]["vk"] == 0x72


# ─────────────────────────────────────────────────────────────────────────────
# spell_vks / has_frame_getter
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerExtras:

    def test_spell_vks_empty(self):
        cm = _make_manager()
        assert cm.spell_vks() == []

    def test_spell_vks_contains_added_vk(self):
        cm = _make_manager()
        cm.add_spell({"vk": 0x71})
        assert 0x71 in cm.spell_vks()

    def test_spell_vks_multiple(self):
        cm = _make_manager()
        cm.add_spell({"vk": 0x71})
        cm.add_spell({"vk": 0x72})
        assert cm.spell_vks() == [0x71, 0x72]

    def test_spell_vks_after_remove(self):
        cm = _make_manager()
        cm.add_spell({"vk": 0x71})
        cm.add_spell({"vk": 0x72})
        cm.remove_spell(0x71)
        assert cm.spell_vks() == [0x72]

    def test_spell_vks_returns_list(self):
        cm = _make_manager()
        assert isinstance(cm.spell_vks(), list)

    def test_has_frame_getter_false_initially(self):
        cm = _make_manager()
        assert cm.has_frame_getter is False

    def test_has_frame_getter_true_after_set(self):
        cm = _make_manager()
        cm.set_frame_getter(lambda: None)
        assert cm.has_frame_getter is True

    def test_has_frame_getter_returns_bool(self):
        cm = _make_manager()
        assert isinstance(cm.has_frame_getter, bool)


class TestCombatManagerHasLogCallback:

    def test_has_log_callback_false_initially(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert cm.has_log_callback is False

    def test_has_log_callback_true_after_set(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm.set_log_callback(lambda m: None)
        assert cm.has_log_callback is True

    def test_has_log_callback_returns_bool(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert isinstance(cm.has_log_callback, bool)

    def test_has_log_callback_consistent_with_log_behavior(self):
        msgs: list[str] = []
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm.set_log_callback(msgs.append)
        assert cm.has_log_callback is True
        cm._log("test")
        assert msgs == ["test"]


class TestCombatManagerActiveCooldowns:

    def test_active_cooldown_count_zero_initially(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert cm.active_cooldown_count == 0

    def test_active_cooldown_count_after_manual_add(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._spell_cds[0x71] = 999999.0  # simulate active cooldown
        assert cm.active_cooldown_count == 1

    def test_active_cooldown_count_returns_int(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert isinstance(cm.active_cooldown_count, int)

    def test_active_cooldown_count_zero_after_reset(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._spell_cds[0x71] = 999999.0
        cm.reset_spell_cooldowns()
        assert cm.active_cooldown_count == 0

    def test_has_active_cooldowns_false_initially(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert cm.has_active_cooldowns is False

    def test_has_active_cooldowns_true_after_manual_add(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._spell_cds[0x71] = 999999.0
        assert cm.has_active_cooldowns is True

    def test_has_active_cooldowns_false_after_reset(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._spell_cds[0x71] = 999999.0
        cm.reset_spell_cooldowns()
        assert cm.has_active_cooldowns is False

    def test_has_active_cooldowns_returns_bool(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert isinstance(cm.has_active_cooldowns, bool)

    def test_has_active_cooldowns_consistent_with_count(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._spell_cds[0x71] = 999999.0
        assert cm.has_active_cooldowns == (cm.active_cooldown_count > 0)


# ─────────────────────────────────────────────────────────────────────────────
# has_kills / has_attacked
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerHasKills:

    def test_false_initially(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert cm.has_kills is False

    def test_true_after_kill_increment(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._kills = 1
        assert cm.has_kills is True

    def test_false_after_reset(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._kills = 3
        cm.reset_kills()
        assert cm.has_kills is False

    def test_consistent_with_kills_count(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._kills = 2
        assert cm.has_kills == (cm.kills > 0)

    def test_returns_bool(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert isinstance(cm.has_kills, bool)


class TestCombatManagerHasAttacked:

    def test_false_initially(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert cm.has_attacked is False

    def test_true_after_attacks_sent(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._attacks_sent = 5
        assert cm.has_attacked is True

    def test_false_after_reset(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._attacks_sent = 3
        cm.reset_kills()
        assert cm.has_attacked is False

    def test_consistent_with_attacks_sent(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._attacks_sent = 7
        assert cm.has_attacked == (cm.attacks_sent > 0)

    def test_returns_bool(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert isinstance(cm.has_attacked, bool)


# ─────────────────────────────────────────────────────────────────────────────
# BattleDetector.template_count / has_templates
# ─────────────────────────────────────────────────────────────────────────────

class TestBattleDetectorTemplateCount:

    def test_zero_when_dir_empty(self, tmp_path: Path):
        det = BattleDetector(_make_config(tmp_path))
        assert det.template_count == 0

    def test_nonzero_after_template_added(self, tmp_path: Path):
        monsters = tmp_path / "monsters"
        monsters.mkdir(parents=True)
        import cv2 as _cv2
        _cv2.imwrite(str(monsters / "rat.png"), _grey_patch(180))
        det = BattleDetector(_make_config(tmp_path))
        assert det.template_count >= 1

    def test_returns_int(self, tmp_path: Path):
        det = BattleDetector(_make_config(tmp_path))
        assert isinstance(det.template_count, int)

    def test_consistent_with_internal_list(self, tmp_path: Path):
        det = BattleDetector(_make_config(tmp_path))
        assert det.template_count == len(det._templates)

    def test_increases_after_reload(self, tmp_path: Path):
        monsters = tmp_path / "monsters"
        monsters.mkdir(parents=True)
        det = BattleDetector(_make_config(tmp_path))
        assert det.template_count == 0
        import cv2 as _cv2
        _cv2.imwrite(str(monsters / "goblin.png"), _grey_patch(150))
        det.reload()
        assert det.template_count >= 1


class TestBattleDetectorHasTemplates:

    def test_false_when_empty(self, tmp_path: Path):
        det = BattleDetector(_make_config(tmp_path))
        assert det.has_templates is False

    def test_true_when_template_loaded(self, tmp_path: Path):
        monsters = tmp_path / "monsters"
        monsters.mkdir(parents=True)
        import cv2 as _cv2
        _cv2.imwrite(str(monsters / "orc.png"), _grey_patch(200))
        det = BattleDetector(_make_config(tmp_path))
        assert det.has_templates is (det.template_count > 0)

    def test_returns_bool(self, tmp_path: Path):
        det = BattleDetector(_make_config(tmp_path))
        assert isinstance(det.has_templates, bool)

    def test_consistent_with_template_count(self, tmp_path: Path):
        det = BattleDetector(_make_config(tmp_path))
        assert det.has_templates == (det.template_count > 0)

    def test_false_after_templates_cleared(self, tmp_path: Path):
        monsters = tmp_path / "monsters"
        monsters.mkdir(parents=True)
        import cv2 as _cv2
        _cv2.imwrite(str(monsters / "bear.png"), _grey_patch(160))
        det = BattleDetector(_make_config(tmp_path))
        det._templates.clear()
        assert det.has_templates is False


# ─────────────────────────────────────────────────────────────────────────────
# CombatManager.has_spells
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerHasSpells:

    def test_false_when_no_spells_configured(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert cm.has_spells is False

    def test_true_after_adding_a_spell(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm.add_spell({"vk": 0x71, "label": "exura"})
        assert cm.has_spells is True

    def test_false_after_removing_all_spells(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm.add_spell({"vk": 0x71, "label": "exura"})
        cm._cfg.spells.clear()
        assert cm.has_spells is False

    def test_consistent_with_spells_count(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm.add_spell({"vk": 0x72, "label": "exura gran"})
        assert cm.has_spells == (cm.spells_count > 0)

    def test_returns_bool(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert isinstance(cm.has_spells, bool)


# ─────────────────────────────────────────────────────────────────────────────
# CombatManager.has_last_hp
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatManagerHasLastHp:

    def test_false_before_any_hp_reading(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert cm.has_last_hp is False

    def test_true_after_recording_nonzero_hp(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._last_hp_pct = 75
        assert cm.has_last_hp is True

    def test_true_when_hp_is_zero(self):
        # 0 is a valid reading (character is dead / fully drained)
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._last_hp_pct = 0
        assert cm.has_last_hp is True

    def test_consistent_with_last_hp_pct_not_none(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        cm._last_hp_pct = 50
        assert cm.has_last_hp == (cm.last_hp_pct is not None)

    def test_returns_bool(self):
        cm = CombatManager(ctrl=_mock_ctrl(), config=CombatConfig())
        assert isinstance(cm.has_last_hp, bool)
