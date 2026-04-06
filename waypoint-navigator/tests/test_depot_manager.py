"""
Tests for src/depot_manager.py — DepotManager, DepotConfig
Fully offline: no OBS, no Tibia process, no real mouse clicks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.depot_manager import DepotManager, DepotConfig, DEPOT_CONFIG_FILE
from src.models import Coordinate


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Factories
# ─────────────────────────────────────────────────────────────────────────────

def _mock_ctrl() -> MagicMock:
    """Return a mock InputController with all methods DepotManager needs."""
    ctrl = MagicMock()
    ctrl.is_connected.return_value = True
    return ctrl


def _make_manager(
    ctrl=None,
    config: Optional[DepotConfig] = None,
    frame_getter=None,
) -> DepotManager:
    dm = DepotManager(
        ctrl=ctrl or _mock_ctrl(),
        config=config or DepotConfig(),
        frame_getter=frame_getter,
    )
    # Suppress print output during tests
    dm.set_log_callback(lambda msg: None)
    return dm


def _solid_frame(h: int = 960, w: int = 1280, color=(0, 0, 0)) -> np.ndarray:
    """Create a solid-color BGR frame."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def _container_frame() -> np.ndarray:
    """
    Simulate a frame with an open container window:
    inject blue pixels into the container_roi region.
    """
    frame = _solid_frame()
    # Default container_roi = [820, 220, 280, 320]
    # Paint >2% of that region blue (container title bar color)
    frame[220:240, 820:1100] = (180, 40, 40)   # HSV~120 → blue in BGR: (B,G,R)
    return frame


# ─────────────────────────────────────────────────────────────────────────────
# DepotConfig: save / load / defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotConfig:

    def test_default_values(self):
        cfg = DepotConfig()
        assert cfg.tile_size_px == 32
        assert cfg.deposit_mode == "shift_click"
        assert cfg.open_wait == pytest.approx(0.8)
        assert cfg.max_items_per_cycle == 0
        assert cfg.close_containers_vk == 0

    def test_default_chest_coord_is_list(self):
        cfg = DepotConfig()
        assert isinstance(cfg.depot_chest_coord, list)
        assert len(cfg.depot_chest_coord) == 3

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "depot_config.json"
        cfg = DepotConfig(tile_size_px=64, open_wait=1.5, deposit_mode="loot_all")
        cfg.save(path)
        loaded = DepotConfig.load(path)
        assert loaded.tile_size_px == 64
        assert loaded.open_wait == pytest.approx(1.5)
        assert loaded.deposit_mode == "loot_all"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        cfg = DepotConfig.load(path)
        assert cfg.tile_size_px == 32     # default

    def test_saved_json_is_valid(self, tmp_path: Path):
        path = tmp_path / "depot_config.json"
        DepotConfig().save(path)
        with open(path) as f:
            data = json.load(f)
        assert "tile_size_px" in data
        assert "deposit_mode" in data

    def test_load_ignores_unknown_keys(self, tmp_path: Path):
        path = tmp_path / "depot_config.json"
        data = {"tile_size_px": 32, "unknown_future_key": "ignored"}
        path.write_text(json.dumps(data))
        cfg = DepotConfig.load(path)
        assert cfg.tile_size_px == 32


# ─────────────────────────────────────────────────────────────────────────────
# DepotManager: construction
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotManagerConstruction:

    def test_default_construction(self):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig())
        assert dm is not None

    def test_set_frame_getter(self):
        dm = _make_manager()
        getter = lambda: _solid_frame()
        dm.set_frame_getter(getter)
        assert dm._frame_getter is getter

    def test_set_log_callback(self):
        dm = _make_manager()
        logs: List[str] = []
        dm.set_log_callback(logs.append)
        dm._log("test message")
        assert logs == ["test message"]

    def test_log_prints_without_callback(self, capsys):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig())
        dm._log("hello printer")
        captured = capsys.readouterr()
        assert "hello printer" in captured.out


# ─────────────────────────────────────────────────────────────────────────────
# _tile_to_screen
# ─────────────────────────────────────────────────────────────────────────────

class TestTileToScreen:

    def test_same_tile_returns_center(self):
        cfg = DepotConfig(viewport_center=[640, 480], tile_size_px=32)
        dm = _make_manager(config=cfg)
        px, py = dm._tile_to_screen(100, 200, 100, 200)
        assert px == 640
        assert py == 480

    def test_one_tile_east(self):
        cfg = DepotConfig(viewport_center=[640, 480], tile_size_px=32)
        dm = _make_manager(config=cfg)
        px, py = dm._tile_to_screen(101, 200, 100, 200)
        assert px == 672    # 640 + 32
        assert py == 480

    def test_one_tile_south(self):
        cfg = DepotConfig(viewport_center=[640, 480], tile_size_px=32)
        dm = _make_manager(config=cfg)
        px, py = dm._tile_to_screen(100, 201, 100, 200)
        assert px == 640
        assert py == 512    # 480 + 32

    def test_negative_offset(self):
        cfg = DepotConfig(viewport_center=[640, 480], tile_size_px=32)
        dm = _make_manager(config=cfg)
        px, py = dm._tile_to_screen(99, 200, 100, 200)
        assert px == 608    # 640 - 32


# ─────────────────────────────────────────────────────────────────────────────
# _detect_open_container
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectOpenContainer:

    def test_black_frame_not_detected(self):
        dm = _make_manager()
        crop = _solid_frame(100, 100)
        assert dm._detect_open_container(crop) is False

    def test_none_frame_not_detected(self):
        dm = _make_manager()
        assert dm._detect_open_container(None) is False

    def test_empty_frame_not_detected(self):
        dm = _make_manager()
        assert dm._detect_open_container(np.zeros((0, 0, 3), dtype=np.uint8)) is False

    def test_blue_region_detected(self):
        dm = _make_manager()
        # Blue in BGR: (255, 0, 0) — HSV ≈ (120, 255, 255) which is in the blue range
        crop = np.zeros((50, 100, 3), dtype=np.uint8)
        crop[:, :50] = (255, 0, 0)   # Fill left half blue (BGR)
        assert dm._detect_open_container(crop) is True


# ─────────────────────────────────────────────────────────────────────────────
# _wait_for_container
# ─────────────────────────────────────────────────────────────────────────────

class TestWaitForContainer:

    def test_no_frame_getter_returns_true_after_wait(self):
        cfg = DepotConfig(open_wait=0.01)
        dm = _make_manager(config=cfg)
        result = dm._wait_for_container(max_wait=0.05)
        assert result is True

    def test_blue_frame_detected_immediately(self):
        blue_crop = np.zeros((50, 100, 3), dtype=np.uint8)
        blue_crop[:, :50] = (255, 0, 0)

        # Build a frame with the container_roi filled blue
        frame = _solid_frame()
        cfg = DepotConfig(container_roi=[0, 0, 100, 50])
        frame[0:50, 0:100] = blue_crop

        dm = _make_manager(config=cfg, frame_getter=lambda: frame.copy())
        result = dm._wait_for_container(max_wait=1.0)
        assert result is True

    def test_returns_false_on_timeout(self):
        dm = _make_manager(frame_getter=lambda: _solid_frame())
        result = dm._wait_for_container(max_wait=0.1)
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# _find_backpack_slots
# ─────────────────────────────────────────────────────────────────────────────

class TestFindBackpackSlots:

    def test_returns_list_of_tuples(self):
        dm = _make_manager()
        slots = dm._find_backpack_slots()
        assert isinstance(slots, list)
        assert len(slots) > 0
        assert all(isinstance(s, tuple) and len(s) == 2 for s in slots)

    def test_default_layout_has_32_slots(self):
        dm = _make_manager()
        slots = dm._find_backpack_slots()
        assert len(slots) == 32   # 4 cols × 8 rows

    def test_slot_spacing_is_34px(self):
        dm = _make_manager()
        slots = dm._find_backpack_slots()
        # Consecutive slots in same row differ by 34px in X
        assert slots[1][0] - slots[0][0] == 34
        # First slot of second row has same X as first slot of first row
        assert slots[4][0] == slots[0][0]


# ─────────────────────────────────────────────────────────────────────────────
# _open_chest
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenChest:

    def test_returns_false_when_not_connected(self):
        ctrl = _mock_ctrl()
        ctrl.is_connected.return_value = False
        dm = _make_manager(ctrl=ctrl)
        assert dm._open_chest(player_pos=None) is False

    def test_returns_false_when_no_chest_coord(self):
        cfg = DepotConfig(depot_chest_coord=[])
        dm = _make_manager(config=cfg)
        assert dm._open_chest(player_pos=None) is False

    def test_sends_right_click_and_open_click(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(
            depot_chest_coord=[32258, 32248, 7],
            viewport_center=[640, 480],
            tile_size_px=32,
            open_wait=0.01,
        )
        player = Coordinate(32258, 32248, 7)
        dm = _make_manager(ctrl=ctrl, config=cfg)
        result = dm._open_chest(player_pos=player)
        assert result is True
        ctrl.right_click.assert_called_once()
        ctrl.left_click.assert_called_once()

    def test_click_position_at_viewport_center_when_on_chest_tile(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(
            depot_chest_coord=[100, 200, 7],
            viewport_center=[640, 480],
            tile_size_px=32,
            open_wait=0.01,
        )
        player = Coordinate(100, 200, 7)
        dm = _make_manager(ctrl=ctrl, config=cfg)
        dm._open_chest(player_pos=player)
        right_args = ctrl.right_click.call_args[0]
        assert right_args == (640, 480)


# ─────────────────────────────────────────────────────────────────────────────
# _deposit_items
# ─────────────────────────────────────────────────────────────────────────────

class TestDepositItems:

    def test_deposits_all_slots_when_item_names_none(self):
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl)
        count = dm._deposit_items(item_names=None)
        assert count == 32                    # 4×8 default slots
        assert ctrl.shift_click.call_count == 32

    def test_respects_max_items_per_cycle(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(max_items_per_cycle=5)
        dm = _make_manager(ctrl=ctrl, config=cfg)
        count = dm._deposit_items(item_names=None)
        assert count == 5
        assert ctrl.shift_click.call_count == 5

    def test_returns_zero_when_slots_empty(self):
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl)
        # Patch _find_backpack_slots to return empty
        from unittest.mock import patch
        with patch.object(dm, '_find_backpack_slots', return_value=[]):
            count = dm._deposit_items()
            assert count == 0
            ctrl.shift_click.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# _close_containers
# ─────────────────────────────────────────────────────────────────────────────

class TestCloseContainers:

    def test_no_vk_configured_does_nothing(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(close_containers_vk=0)
        dm = _make_manager(ctrl=ctrl, config=cfg)
        dm._close_containers()
        ctrl.press_key.assert_not_called()

    def test_vk_configured_presses_key(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(close_containers_vk=0x1B)  # ESC
        dm = _make_manager(ctrl=ctrl, config=cfg)
        dm._close_containers()
        ctrl.press_key.assert_called_once_with(0x1B)


# ─────────────────────────────────────────────────────────────────────────────
# run_depot_cycle — integration (mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestRunDepotCycle:

    def _fast_config(self) -> DepotConfig:
        return DepotConfig(
            depot_chest_coord=[100, 200, 7],
            viewport_center=[640, 480],
            tile_size_px=32,
            open_wait=0.0,
            max_items_per_cycle=2,
            close_containers_vk=0x1B,
        )

    def test_returns_false_when_not_connected(self):
        ctrl = _mock_ctrl()
        ctrl.is_connected.return_value = False
        dm = _make_manager(ctrl=ctrl, config=self._fast_config())
        result = dm.run_depot_cycle()
        assert result is False

    def test_full_cycle_returns_true(self):
        from unittest.mock import patch
        ctrl = _mock_ctrl()
        player = Coordinate(100, 200, 7)
        dm = _make_manager(ctrl=ctrl, config=self._fast_config())
        # Inject a fast container detector
        with patch.object(dm, '_wait_for_container', return_value=True):
            result = dm.run_depot_cycle(player_pos=player)
            assert result is True

    def test_shift_clicks_sent_in_full_cycle(self):
        from unittest.mock import patch
        ctrl = _mock_ctrl()
        player = Coordinate(100, 200, 7)
        cfg = self._fast_config()
        dm = _make_manager(ctrl=ctrl, config=cfg)
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle(player_pos=player)
            # max_items_per_cycle=2 → exactly 2 shift_clicks
            assert ctrl.shift_click.call_count == 2

    def test_close_key_pressed_at_end(self):
        from unittest.mock import patch
        ctrl = _mock_ctrl()
        player = Coordinate(100, 200, 7)
        dm = _make_manager(ctrl=ctrl, config=self._fast_config())
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle(player_pos=player)
            ctrl.press_key.assert_called_with(0x1B)

    def test_bank_deposit_skipped_when_no_npc_coord(self):
        from unittest.mock import patch
        ctrl = _mock_ctrl()
        cfg = self._fast_config()
        cfg.bank_npc_coord = []   # disabled
        dm = _make_manager(ctrl=ctrl, config=cfg)
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle()
            # type_text should NOT have been called
            ctrl.type_text.assert_not_called()

    def test_bank_deposit_sends_dialogue_when_configured(self):
        from unittest.mock import patch
        ctrl = _mock_ctrl()
        cfg = self._fast_config()
        cfg.bank_npc_coord = [100, 198, 7]
        dm = _make_manager(ctrl=ctrl, config=cfg)
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle()
            texts = [c.args[0] for c in ctrl.type_text.call_args_list]
            assert "hi" in texts
            assert "deposit all" in texts
            assert "yes" in texts


# ─────────────────────────────────────────────────────────────────────────────
# DepotManager statistics
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotManagerStats:

    def _fast_config(self) -> DepotConfig:
        return DepotConfig(
            depot_chest_coord=[100, 200, 7],
            viewport_center=[640, 480],
            tile_size_px=32,
            open_wait=0.0,
            max_items_per_cycle=2,
            close_containers_vk=0,
        )

    def test_initial_cycle_count_zero(self):
        dm = _make_manager()
        assert dm.cycle_count == 0

    def test_initial_items_deposited_zero(self):
        dm = _make_manager()
        assert dm.items_deposited == 0

    def test_cycle_count_increments_after_successful_cycle(self):
        from unittest.mock import patch
        dm = _make_manager(config=self._fast_config())
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle()
            assert dm.cycle_count == 1

    def test_cycle_count_increments_multiple_times(self):
        from unittest.mock import patch
        dm = _make_manager(config=self._fast_config())
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle()
            dm.run_depot_cycle()
            assert dm.cycle_count == 2

    def test_cycle_count_not_incremented_on_failed_cycle(self):
        ctrl = _mock_ctrl()
        ctrl.is_connected.return_value = False
        dm = _make_manager(ctrl=ctrl)
        dm.run_depot_cycle()
        assert dm.cycle_count == 0

    def test_items_deposited_accumulates(self):
        from unittest.mock import patch
        dm = _make_manager(config=self._fast_config())
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle()
            dm.run_depot_cycle()
            # max_items_per_cycle=2 per cycle => 4 total
            assert dm.items_deposited == 4

    def test_reset_stats_zeros_counters(self):
        from unittest.mock import patch
        dm = _make_manager(config=self._fast_config())
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle()
        dm.reset_stats()
        assert dm.cycle_count == 0
        assert dm.items_deposited == 0

    def test_reset_stats_on_fresh_manager_noops(self):
        dm = _make_manager()
        dm.reset_stats()  # should not raise
        assert dm.cycle_count == 0
        assert dm.items_deposited == 0


# ─────────────────────────────────────────────────────────────────────────────
# DepotManager.update_config()
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotManagerUpdateConfig:

    def test_update_config_replaces_cfg(self):
        dm = _make_manager()
        new_cfg = DepotConfig(tile_size_px=64, open_wait=2.5)
        dm.update_config(new_cfg)
        assert dm._cfg.tile_size_px == 64
        assert dm._cfg.open_wait == pytest.approx(2.5)

    def test_update_config_new_tile_size_affects_tile_to_screen(self):
        dm = _make_manager(config=DepotConfig(viewport_center=[640, 480], tile_size_px=32))
        dm.update_config(DepotConfig(viewport_center=[640, 480], tile_size_px=64))
        px, py = dm._tile_to_screen(101, 200, 100, 200)
        assert px == 704   # 640 + 64 (one tile east at 64px/tile)

    def test_update_config_preserves_stats(self):
        from unittest.mock import patch
        dm = _make_manager(config=DepotConfig(
            depot_chest_coord=[100, 200, 7],
            viewport_center=[640, 480],
            tile_size_px=32,
            open_wait=0.0,
            max_items_per_cycle=1,
            close_containers_vk=0,
        ))
        with patch.object(dm, '_wait_for_container', return_value=True):
            dm.run_depot_cycle()
            dm.update_config(DepotConfig())
            # Stats intact after config change
            assert dm.cycle_count == 1

    def test_update_config_does_not_reset_stats(self):
        dm = _make_manager()
        dm._cycle_count = 5
        dm._items_deposited = 42
        dm.update_config(DepotConfig())
        assert dm.cycle_count == 5
        assert dm.items_deposited == 42


# ─────────────────────────────────────────────────────────────────────────────
# DepotManager.has_frame_getter / items_per_cycle / stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotManagerExtras:

    def test_has_frame_getter_false_initially(self):
        dm = _make_manager()
        assert dm.has_frame_getter is False

    def test_has_frame_getter_true_after_set(self):
        dm = _make_manager()
        dm.set_frame_getter(lambda: None)
        assert dm.has_frame_getter is True

    def test_has_frame_getter_returns_bool(self):
        dm = _make_manager()
        assert isinstance(dm.has_frame_getter, bool)

    def test_items_per_cycle_zero_initially(self):
        dm = _make_manager()
        assert dm.items_per_cycle == pytest.approx(0.0)

    def test_items_per_cycle_correct(self):
        dm = _make_manager()
        dm._cycle_count     = 4
        dm._items_deposited = 20
        assert dm.items_per_cycle == pytest.approx(5.0)

    def test_items_per_cycle_fractional(self):
        dm = _make_manager()
        dm._cycle_count     = 3
        dm._items_deposited = 10
        assert dm.items_per_cycle == pytest.approx(10 / 3)

    def test_stats_snapshot_returns_dict(self):
        dm = _make_manager()
        assert isinstance(dm.stats_snapshot(), dict)

    def test_stats_snapshot_all_keys(self):
        dm = _make_manager()
        snap = dm.stats_snapshot()
        for key in ("cycle_count", "items_deposited", "items_per_cycle",
                    "has_frame_getter", "has_log_callback"):
            assert key in snap, f"Missing key: {key}"

    def test_stats_snapshot_initial_values(self):
        # Use a bare manager (no log callback) to verify the False branch.
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        snap = dm.stats_snapshot()
        assert snap["cycle_count"]      == 0
        assert snap["items_deposited"]  == 0
        assert snap["items_per_cycle"]  == pytest.approx(0.0)
        assert snap["has_frame_getter"] is False
        assert snap["has_log_callback"] is False

    def test_stats_snapshot_reflects_counts(self):
        dm = _make_manager()
        dm._cycle_count     = 2
        dm._items_deposited = 8
        snap = dm.stats_snapshot()
        assert snap["cycle_count"]     == 2
        assert snap["items_deposited"] == 8
        assert snap["items_per_cycle"] == pytest.approx(4.0)

    def test_stats_snapshot_has_log_callback_true_when_set(self):
        dm = _make_manager()
        dm.set_log_callback(lambda m: None)
        assert dm.stats_snapshot()["has_log_callback"] is True


class TestDepotManagerHasLogCallback:

    def test_has_log_callback_false_bare_manager(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert dm.has_log_callback is False

    def test_has_log_callback_true_after_set(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm.set_log_callback(lambda m: None)
        assert dm.has_log_callback is True

    def test_has_log_callback_returns_bool(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert isinstance(dm.has_log_callback, bool)

    def test_has_log_callback_consistent_with_stats_snapshot(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm.set_log_callback(print)
        assert dm.has_log_callback == dm.stats_snapshot()["has_log_callback"]


class TestDepotManagerIsIdleHasDeposited:

    def test_is_idle_true_initially(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert dm.is_idle is True

    def test_is_idle_false_after_cycle_count_incremented(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cycle_count = 1
        assert dm.is_idle is False

    def test_is_idle_true_after_reset(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cycle_count = 3
        dm.reset_stats()
        assert dm.is_idle is True

    def test_is_idle_returns_bool(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert isinstance(dm.is_idle, bool)

    def test_has_deposited_items_false_initially(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert dm.has_deposited_items is False

    def test_has_deposited_items_true_after_increment(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._items_deposited = 5
        assert dm.has_deposited_items is True

    def test_has_deposited_items_false_after_reset(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._items_deposited = 10
        dm.reset_stats()
        assert dm.has_deposited_items is False

    def test_has_deposited_items_returns_bool(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert isinstance(dm.has_deposited_items, bool)

    def test_has_deposited_consistent_with_items_deposited(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._items_deposited = 2
        assert dm.has_deposited_items == (dm.items_deposited > 0)


# ─────────────────────────────────────────────────────────────────────────────
# has_run_cycles
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotManagerHasRunCycles:

    def test_false_initially(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert dm.has_run_cycles is False

    def test_true_after_cycle_count_set(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cycle_count = 1
        assert dm.has_run_cycles is True

    def test_false_after_reset(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cycle_count = 5
        dm.reset_stats()
        assert dm.has_run_cycles is False

    def test_is_inverse_of_is_idle(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cycle_count = 2
        assert dm.has_run_cycles != dm.is_idle

    def test_returns_bool(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert isinstance(dm.has_run_cycles, bool)


# ─────────────────────────────────────────────────────────────────────────────
# DepotManager.is_unlimited
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotIsUnlimited:

    def test_true_when_max_items_is_zero(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert dm.is_unlimited is True

    def test_false_when_cap_is_set(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cfg.max_items_per_cycle = 10
        assert dm.is_unlimited is False

    def test_false_when_cap_equals_one(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cfg.max_items_per_cycle = 1
        assert dm.is_unlimited is False

    def test_true_after_resetting_cap_to_zero(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cfg.max_items_per_cycle = 5
        dm._cfg.max_items_per_cycle = 0
        assert dm.is_unlimited is True

    def test_returns_bool(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert isinstance(dm.is_unlimited, bool)


# ─────────────────────────────────────────────────────────────────────────────
# DepotManager.has_cap
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotHasCap:

    def test_false_when_no_cap_configured(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert dm.has_cap is False

    def test_true_when_cap_is_set(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cfg.max_items_per_cycle = 20
        assert dm.has_cap is True

    def test_true_when_cap_equals_one(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cfg.max_items_per_cycle = 1
        assert dm.has_cap is True

    def test_inverse_of_is_unlimited(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        dm._cfg.max_items_per_cycle = 5
        assert dm.has_cap != dm.is_unlimited

    def test_returns_bool(self):
        dm = DepotManager(ctrl=_mock_ctrl(), config=DepotConfig())
        assert isinstance(dm.has_cap, bool)


# ─────────────────────────────────────────────────────────────────────────────
# TestLootAllMode — _deposit_loot_all + deposit_mode routing
# ─────────────────────────────────────────────────────────────────────────────

class TestLootAllMode:

    # --- DepotConfig new fields ---

    def test_loot_all_vk_default_zero(self):
        cfg = DepotConfig()
        assert cfg.loot_all_vk == 0

    def test_loot_all_btn_pos_default_empty(self):
        cfg = DepotConfig()
        assert cfg.loot_all_btn_pos == []

    def test_loot_all_vk_set_and_load(self, tmp_path):
        import json
        path = tmp_path / "cfg.json"
        cfg = DepotConfig(loot_all_vk=0x46, loot_all_btn_pos=[320, 240])
        cfg.save(path)
        loaded = DepotConfig.load(path)
        assert loaded.loot_all_vk == 0x46
        assert loaded.loot_all_btn_pos == [320, 240]

    # --- _deposit_loot_all: VK path ---

    def test_loot_all_vk_calls_press_key(self):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig(loot_all_vk=0x46))

        with _patch_sleep():
            result = dm._deposit_loot_all()

        ctrl.press_key.assert_called_once_with(0x46)
        assert result == 1

    def test_loot_all_vk_returns_one(self):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig(loot_all_vk=0x10))
        with _patch_sleep():
            result = dm._deposit_loot_all()
        assert result == 1

    def test_loot_all_vk_does_not_call_shift_click(self):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig(loot_all_vk=0x10))
        with _patch_sleep():
            dm._deposit_loot_all()
        ctrl.shift_click.assert_not_called()

    # --- _deposit_loot_all: button-position path ---

    def test_loot_all_btn_pos_calls_left_click(self):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig(loot_all_btn_pos=[400, 300]))
        with _patch_sleep():
            result = dm._deposit_loot_all()
        ctrl.left_click.assert_called_once_with(400, 300)
        assert result == 1

    def test_loot_all_btn_pos_returns_one(self):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig(loot_all_btn_pos=[10, 20]))
        with _patch_sleep():
            result = dm._deposit_loot_all()
        assert result == 1

    def test_loot_all_btn_pos_does_not_call_shift_click(self):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig(loot_all_btn_pos=[10, 20]))
        with _patch_sleep():
            dm._deposit_loot_all()
        ctrl.shift_click.assert_not_called()

    # --- _deposit_loot_all: fallback path ---

    def test_loot_all_fallback_when_unconfigured(self):
        """Neither vk nor btn_pos set — falls back to shift_click loop."""
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig())
        # Provide a dummy frame so _find_backpack_slots returns nothing
        # The fallback should still return without crash
        with _patch_sleep():
            result = dm._deposit_loot_all()
        # With no slots found, count == 0 but no exception
        assert isinstance(result, int)

    def test_loot_all_fallback_logs_warning(self):
        from unittest.mock import patch as _patch
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig())
        logs: list[str] = []
        dm.set_log_callback(logs.append)
        with _patch_sleep():
            dm._deposit_loot_all()
        assert any("⚠" in l or "fallback" in l for l in logs)

    # --- deposit_mode routing ---

    def test_deposit_mode_loot_all_routes_to_method(self):
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig(deposit_mode="loot_all", loot_all_vk=0x46))
        called_direct = []

        original = dm._deposit_loot_all

        def spy():
            called_direct.append(True)
            with _patch_sleep():
                return original()

        dm._deposit_loot_all = spy  # type: ignore[method-assign]
        dm._frame_getter = lambda: None
        dm._frame = None  # type: ignore

        with _patch_sleep():
            dm._deposit_items()

        assert called_direct, "_deposit_loot_all should have been called"

    def test_deposit_mode_loot_all_vk_full_path(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(deposit_mode="loot_all", loot_all_vk=0x46)
        dm = DepotManager(ctrl=ctrl, config=cfg)
        with _patch_sleep():
            result = dm._deposit_items()
        ctrl.press_key.assert_called_with(0x46)
        assert result == 1

    def test_deposit_mode_default_not_loot_all(self):
        """deposit_mode='shift_click' should NOT call press_key at all."""
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=DepotConfig(deposit_mode="shift_click"))
        dm._frame_getter = lambda: None
        with _patch_sleep():
            dm._deposit_items()
        ctrl.press_key.assert_not_called()


# ─── tiny helpers used in TestLootAllMode ────────────────────────────────────

def _patch_sleep():
    from unittest.mock import patch
    return patch("src.depot_manager.time.sleep")


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests for the 2 bugs fixed in this review
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionBug1BankDepositEnterKey:
    """Bug 1: _bank_deposit must press Enter (VK_RETURN=0x0D) after each type_text call.
    Without Enter the NPC never receives the typed text and bank deposit silently fails."""

    def _bank_cfg(self) -> DepotConfig:
        return DepotConfig(bank_npc_coord=[100, 198, 7], bank_dialogue_delay=0.0)

    def test_press_key_called_for_each_dialogue_line(self):
        """VK_RETURN (0x0D) must be pressed once per NPC dialogue word."""
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, config=self._bank_cfg())
        with _patch_sleep():
            dm._bank_deposit()
        # Collect only the VK_RETURN presses
        enter_calls = [c for c in ctrl.press_key.call_args_list
                       if c.args[0] == 0x0D]
        assert len(enter_calls) == 3, (
            f"Expected 3 VK_RETURN presses (hi/deposit all/yes), got {len(enter_calls)}"
        )

    def test_press_key_enter_comes_after_each_type_text(self):
        """For each type_text('X') call there must be a press_key(0x0D) call after it."""
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, config=self._bank_cfg())
        call_order: List[str] = []
        ctrl.type_text.side_effect = lambda msg: call_order.append(f"text:{msg}")
        ctrl.press_key.side_effect = lambda vk: call_order.append(f"key:{vk:#04x}")

        with _patch_sleep():
            dm._bank_deposit()

        # Interleaved: text:hi, key:0x0d, text:deposit all, key:0x0d, text:yes, key:0x0d
        pairs = [(call_order[i], call_order[i + 1])
                 for i in range(0, len(call_order) - 1, 2)]
        for text_call, key_call in pairs:
            assert text_call.startswith("text:"), f"Expected type_text first, got: {text_call}"
            assert key_call == "key:0x0d",         f"Expected Enter after text, got: {key_call}"

    def test_dialogue_content_unchanged(self):
        """type_text is still called with the correct NPC dialogue lines."""
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, config=self._bank_cfg())
        with _patch_sleep():
            dm._bank_deposit()
        texts = [c.args[0] for c in ctrl.type_text.call_args_list]
        assert texts == ["hi", "deposit all", "yes"]

    def test_bank_deposit_returns_true_on_success(self):
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, config=self._bank_cfg())
        with _patch_sleep():
            result = dm._bank_deposit()
        assert result is True

    def test_bank_deposit_returns_false_when_not_connected(self):
        ctrl = _mock_ctrl()
        ctrl.is_connected.return_value = False
        dm = _make_manager(ctrl=ctrl, config=self._bank_cfg())
        with _patch_sleep():
            result = dm._bank_deposit()
        assert result is False
        ctrl.type_text.assert_not_called()

    def test_bank_deposit_no_enter_when_no_type_text_method(self):
        """If ctrl has no type_text, no press_key(Enter) should be called either."""
        ctrl = MagicMock(spec=["is_connected", "press_key", "right_click",
                               "left_click", "shift_click"])
        ctrl.is_connected.return_value = True
        dm = _make_manager(ctrl=ctrl, config=self._bank_cfg())
        with _patch_sleep():
            dm._bank_deposit()
        # press_key should not be called for Enter since type_text wasn't called
        enter_calls = [c for c in ctrl.press_key.call_args_list
                       if c.args[0] == 0x0D]
        assert len(enter_calls) == 0, (
            "press_key(Enter) must not be called when ctrl has no type_text"
        )


class TestRegressionBug2CloseContainersConfigurableWait:
    """Bug 2: _close_containers must use cfg.close_containers_wait, not a hardcoded 0.3s."""

    def test_default_close_containers_wait_is_0_3(self):
        assert DepotConfig().close_containers_wait == pytest.approx(0.3)

    def test_close_containers_wait_persists_in_save_load(self, tmp_path: Path):
        path = tmp_path / "cfg.json"
        DepotConfig(close_containers_wait=1.5).save(path)
        loaded = DepotConfig.load(path)
        assert loaded.close_containers_wait == pytest.approx(1.5)

    def test_close_containers_uses_configured_wait(self):
        """The sleep duration must be near cfg.close_containers_wait (with jitter)."""
        from unittest.mock import patch, call
        ctrl = _mock_ctrl()
        cfg = DepotConfig(close_containers_vk=0x1B, close_containers_wait=0.75)
        dm = _make_manager(ctrl=ctrl, config=cfg)
        sleep_calls: List[float] = []
        with patch("src.depot_manager.time.sleep",
                   side_effect=lambda d: sleep_calls.append(d)):
            dm._close_containers()
        assert any(0.5 <= v <= 1.1 for v in sleep_calls), (
            f"Expected sleep near 0.75 from close_containers_wait=0.75, got: {sleep_calls}"
        )

    def test_close_containers_does_not_sleep_hardcoded_0_3_when_changed(self):
        """Changing close_containers_wait away from 0.3 must change the sleep duration."""
        from unittest.mock import patch
        ctrl = _mock_ctrl()
        cfg = DepotConfig(close_containers_vk=0x1B, close_containers_wait=0.5)
        dm = _make_manager(ctrl=ctrl, config=cfg)
        sleep_calls: List[float] = []
        with patch("src.depot_manager.time.sleep",
                   side_effect=lambda d: sleep_calls.append(d)):
            dm._close_containers()
        assert 0.3 not in sleep_calls, (
            "sleep(0.3) must not be hardcoded; got 0.3 despite close_containers_wait=0.5"
        )
        assert any(0.3 <= v <= 0.75 for v in sleep_calls)

    def test_close_containers_no_vk_no_sleep(self):
        """With no VK configured, neither press_key nor sleep should be called."""
        from unittest.mock import patch
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, config=DepotConfig(close_containers_vk=0))
        sleep_calls: List[float] = []
        with patch("src.depot_manager.time.sleep",
                   side_effect=lambda d: sleep_calls.append(d)):
            dm._close_containers()
        ctrl.press_key.assert_not_called()
        assert sleep_calls == []

    def test_update_config_changes_close_wait(self):
        """update_config hot-swapping changes the wait used by _close_containers."""
        from unittest.mock import patch
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl,
                           config=DepotConfig(close_containers_vk=0x1B,
                                              close_containers_wait=0.1))
        sleep_calls: List[float] = []
        with patch("src.depot_manager.time.sleep",
                   side_effect=lambda d: sleep_calls.append(d)):
            dm._close_containers()
        assert any(0.05 <= v <= 0.2 for v in sleep_calls)
        sleep_calls.clear()

        dm.update_config(DepotConfig(close_containers_vk=0x1B, close_containers_wait=0.9))
        with patch("src.depot_manager.time.sleep",
                   side_effect=lambda d: sleep_calls.append(d)):
            dm._close_containers()
        assert any(0.6 <= v <= 1.2 for v in sleep_calls)
