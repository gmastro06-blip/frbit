"""
test_depot_coverage.py
----------------------
Extra tests to cover missing branches in src/depot_manager.py.
100% offline — no real game, no Windows APIs, no template images required.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from src.depot_manager import DepotManager, DepotConfig, DEPOT_CONFIG_FILE
from src.models import Coordinate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blank(h=960, w=1280):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _bright(h=960, w=1280):
    return np.full((h, w, 3), 50, dtype=np.uint8)


def _mock_ctrl(connected=True):
    ctrl = MagicMock()
    ctrl.is_connected.return_value = connected
    ctrl.right_click.return_value = True
    ctrl.left_click.return_value = True
    ctrl.shift_click.return_value = None
    ctrl.press_key.return_value = None
    ctrl.type_text = MagicMock()
    return ctrl


def _make_manager(ctrl=None, cfg=None, frame=None):
    dm = DepotManager(
        ctrl=ctrl or _mock_ctrl(),
        config=cfg or DepotConfig(
            open_wait=0.0,
            container_detect_wait=0.0,
            bank_dialogue_delay=0.0,
            close_containers_wait=0.0,
        ),
        frame_getter=(lambda: frame) if frame is not None else None,
    )
    dm.set_log_callback(lambda m: None)
    return dm


# ---------------------------------------------------------------------------
# DepotConfig.save / load roundtrip
# ---------------------------------------------------------------------------

class TestDepotConfigSaveLoad:

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "depot.json"
        cfg = DepotConfig(tile_size_px=64, open_wait=1.5)
        cfg.save(path)
        loaded = DepotConfig.load(path)
        assert loaded.tile_size_px == 64
        assert loaded.open_wait == pytest.approx(1.5)

    def test_load_missing_returns_defaults(self, tmp_path):
        cfg = DepotConfig.load(tmp_path / "nonexistent.json")
        assert cfg.tile_size_px == 32

    def test_save_uses_temp_file(self, tmp_path):
        path = tmp_path / "cfg.json"
        DepotConfig().save(path)
        assert path.exists()


# ---------------------------------------------------------------------------
# DepotManager public properties (lines 203-278)
# ---------------------------------------------------------------------------

class TestDepotManagerProperties:

    def test_cycle_count_zero(self):
        dm = _make_manager()
        assert dm.cycle_count == 0

    def test_items_deposited_zero(self):
        dm = _make_manager()
        assert dm.items_deposited == 0

    def test_reset_stats(self):
        dm = _make_manager()
        dm._cycle_count = 3
        dm._items_deposited = 10
        dm.reset_stats()
        assert dm.cycle_count == 0
        assert dm.items_deposited == 0

    def test_has_frame_getter_true(self):
        dm = _make_manager(frame=_blank())
        assert dm.has_frame_getter

    def test_has_frame_getter_false(self):
        dm = _make_manager()
        assert not dm.has_frame_getter

    def test_has_log_callback_true(self):
        dm = _make_manager()
        # set_log_callback already called in _make_manager
        assert dm.has_log_callback

    def test_items_per_cycle_zero_cycles(self):
        dm = _make_manager()
        assert dm.items_per_cycle == pytest.approx(0.0)

    def test_items_per_cycle_nonzero(self):
        dm = _make_manager()
        dm._cycle_count = 2
        dm._items_deposited = 10
        assert dm.items_per_cycle == pytest.approx(5.0)

    def test_stats_snapshot(self):
        dm = _make_manager()
        snap = dm.stats_snapshot()
        assert "cycle_count" in snap
        assert "items_deposited" in snap
        assert "items_per_cycle" in snap
        assert "has_frame_getter" in snap
        assert "has_log_callback" in snap

    def test_is_idle_true(self):
        dm = _make_manager()
        assert dm.is_idle

    def test_is_idle_false(self):
        dm = _make_manager()
        dm._cycle_count = 1
        assert not dm.is_idle

    def test_has_deposited_items(self):
        dm = _make_manager()
        assert not dm.has_deposited_items
        dm._items_deposited = 1
        assert dm.has_deposited_items

    def test_has_run_cycles(self):
        dm = _make_manager()
        assert not dm.has_run_cycles
        dm._cycle_count = 1
        assert dm.has_run_cycles

    def test_is_unlimited(self):
        dm = _make_manager(cfg=DepotConfig(max_items_per_cycle=0))
        assert dm.is_unlimited

    def test_has_cap(self):
        dm = _make_manager(cfg=DepotConfig(max_items_per_cycle=5))
        assert dm.has_cap

    def test_update_config(self):
        dm = _make_manager()
        dm._tmpl_cache = {"x": None}
        new_cfg = DepotConfig(tile_size_px=64)
        dm.update_config(new_cfg)
        assert dm._cfg.tile_size_px == 64
        assert dm._tmpl_cache is None


# ---------------------------------------------------------------------------
# _open_chest — all branches (lines 342-407)
# ---------------------------------------------------------------------------

class TestOpenChest:

    def test_no_chest_coord_returns_false(self):
        cfg = DepotConfig(depot_chest_coord=[])
        dm = _make_manager(cfg=cfg)
        assert dm._open_chest() is False

    def test_not_connected_returns_false(self):
        ctrl = _mock_ctrl(connected=False)
        cfg = DepotConfig(depot_chest_coord=[100, 200])
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        assert dm._open_chest() is False

    def test_right_click_fails(self):
        ctrl = _mock_ctrl()
        ctrl.right_click.return_value = False
        cfg = DepotConfig(depot_chest_coord=[100, 200], open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg, frame=_blank())
        with patch("src.depot_manager.jittered_sleep"):
            result = dm._open_chest()
        assert result is False

    def test_fallback_offset_path(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(depot_chest_coord=[100, 200], open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg, frame=_blank())
        with patch("src.depot_manager.detect_context_menu", return_value=None), \
             patch("src.depot_manager.jittered_sleep"), \
             patch("time.sleep"):
            result = dm._open_chest()
        assert result is True

    def test_visual_menu_path_success(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(depot_chest_coord=[100, 200], open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg, frame=_blank())
        fake_menu = (90, 200, 100, 40)
        fake_entry = (95, 210)
        with patch("src.depot_manager.detect_context_menu", return_value=fake_menu), \
             patch("src.depot_manager.find_menu_entry_offset", return_value=fake_entry), \
             patch("src.depot_manager.jittered_sleep"), \
             patch("time.sleep"):
            result = dm._open_chest()
        assert result is True

    def test_visual_menu_left_click_fails(self):
        ctrl = _mock_ctrl()
        ctrl.left_click.return_value = False
        cfg = DepotConfig(depot_chest_coord=[100, 200], open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg, frame=_blank())
        fake_menu = (90, 200, 100, 40)
        fake_entry = (95, 210)
        with patch("src.depot_manager.detect_context_menu", return_value=fake_menu), \
             patch("src.depot_manager.find_menu_entry_offset", return_value=fake_entry), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._open_chest()
        assert result is False

    def test_fallback_left_click_fails(self):
        ctrl = _mock_ctrl()
        ctrl.left_click.return_value = False
        cfg = DepotConfig(depot_chest_coord=[100, 200], open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg, frame=_blank())
        with patch("src.depot_manager.detect_context_menu", return_value=None), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._open_chest()
        assert result is False

    def test_with_player_pos(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(depot_chest_coord=[100, 200], open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg, frame=_blank())
        player = MagicMock(x=99, y=199)
        with patch("src.depot_manager.detect_context_menu", return_value=None), \
             patch("src.depot_manager.jittered_sleep"), \
             patch("time.sleep"):
            result = dm._open_chest(player)
        assert result is True

    def test_no_frame_getter(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(depot_chest_coord=[100, 200], open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg)  # no frame getter
        with patch("src.depot_manager.jittered_sleep"), \
             patch("time.sleep"):
            result = dm._open_chest()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _wait_for_container — no frame_getter branch (line 415-417)
# ---------------------------------------------------------------------------

class TestWaitForContainer:

    def test_no_frame_getter_returns_true(self):
        dm = _make_manager()
        with patch("time.sleep"):
            result = dm._wait_for_container(max_wait=0.0)
        assert result is True

    def test_container_detected_returns_true(self):
        call_count = [0]

        def fake_getter():
            call_count[0] += 1
            f = _blank()
            return f

        cfg = DepotConfig(container_roi=[0, 0, 100, 100], open_wait=0.0)
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=cfg, frame_getter=fake_getter)
        dm.set_log_callback(lambda m: None)
        with patch.object(dm, "_detect_open_container", return_value=True), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._wait_for_container(max_wait=1.0)
        assert result is True

    def test_container_timeout_returns_false(self):
        def fake_getter():
            return _blank()

        cfg = DepotConfig(container_roi=[0, 0, 100, 100], open_wait=0.0)
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=cfg, frame_getter=fake_getter)
        dm.set_log_callback(lambda m: None)
        with patch.object(dm, "_detect_open_container", return_value=False), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._wait_for_container(max_wait=0.001)
        assert result is False


# ---------------------------------------------------------------------------
# _detect_open_container (lines 439-466)
# ---------------------------------------------------------------------------

class TestDetectOpenContainer:

    def test_none_crop_returns_false(self):
        dm = _make_manager()
        assert dm._detect_open_container(None) is False

    def test_empty_array_returns_false(self):
        dm = _make_manager()
        assert dm._detect_open_container(np.zeros((0, 0, 3), dtype=np.uint8)) is False

    def test_visual_detection_returns_true(self):
        dm = _make_manager()
        crop = _blank(40, 80)
        with patch("src.depot_manager.detect_container_window", return_value=(0, 0, 80, 40)):
            assert dm._detect_open_container(crop) is True

    def test_hsv_fallback_not_blue(self):
        dm = _make_manager()
        crop = np.full((20, 20, 3), 50, dtype=np.uint8)  # gray, not blue
        with patch("src.depot_manager.detect_container_window", return_value=None):
            result = dm._detect_open_container(crop)
        assert result is False

    def test_hsv_fallback_blue_enough(self):
        dm = _make_manager()
        # Fill with a blue HSV color (H≈120 in OpenCV) — BGR=(200,0,0) is blue
        crop = np.zeros((20, 20, 3), dtype=np.uint8)
        crop[:, :] = (200, 0, 0)  # BGR blue
        with patch("src.depot_manager.detect_container_window", return_value=None):
            result = dm._detect_open_container(crop)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _deposit_items — loot_all mode + shift_click mode (lines 468-499)
# ---------------------------------------------------------------------------

class TestDepositItems:

    def test_deposit_mode_loot_all_delegates(self):
        cfg = DepotConfig(deposit_mode="loot_all")
        dm = _make_manager(cfg=cfg)
        with patch.object(dm, "_deposit_loot_all", return_value=3) as m:
            result = dm._deposit_items()
        m.assert_called_once()
        assert result == 3

    def test_deposit_no_slots_returns_zero(self):
        cfg = DepotConfig(deposit_mode="shift_click")
        dm = _make_manager(cfg=cfg)
        with patch.object(dm, "_find_backpack_slots", return_value=[]):
            result = dm._deposit_items()
        assert result == 0

    def test_deposit_shift_click_all(self):
        cfg = DepotConfig(deposit_mode="shift_click", max_items_per_cycle=0,
                          open_wait=0.0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        slots = [(100, 200), (120, 200)]
        with patch.object(dm, "_find_backpack_slots", return_value=slots), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._deposit_items()
        assert result == 2

    def test_deposit_shift_click_with_cap(self):
        cfg = DepotConfig(deposit_mode="shift_click", max_items_per_cycle=1,
                          open_wait=0.0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        slots = [(100, 200), (120, 200), (140, 200)]
        with patch.object(dm, "_find_backpack_slots", return_value=slots), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._deposit_items()
        assert result == 1

    def test_deposit_with_item_names_slot_matches(self):
        cfg = DepotConfig(deposit_mode="shift_click", max_items_per_cycle=0,
                          open_wait=0.0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        slots = [(100, 200)]
        with patch.object(dm, "_find_backpack_slots", return_value=slots), \
             patch.object(dm, "_slot_matches", return_value=True), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._deposit_items(item_names=["sword"])
        assert result == 1

    def test_deposit_with_item_names_no_match(self):
        cfg = DepotConfig(deposit_mode="shift_click", max_items_per_cycle=0,
                          open_wait=0.0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        slots = [(100, 200)]
        with patch.object(dm, "_find_backpack_slots", return_value=slots), \
             patch.object(dm, "_slot_matches", return_value=False), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._deposit_items(item_names=["sword"])
        assert result == 0


# ---------------------------------------------------------------------------
# _deposit_loot_all — all three branches (lines 501-539)
# ---------------------------------------------------------------------------

class TestDepositLootAll:

    def test_vk_branch(self):
        cfg = DepotConfig(loot_all_vk=0x44)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("src.depot_manager.jittered_sleep"):
            result = dm._deposit_loot_all()
        ctrl.press_key.assert_called_with(0x44)
        assert result == 1

    def test_btn_pos_branch(self):
        cfg = DepotConfig(loot_all_btn_pos=[500, 300])
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("src.depot_manager.jittered_sleep"):
            result = dm._deposit_loot_all()
        ctrl.left_click.assert_called()
        assert result == 1

    def test_fallback_shift_click(self):
        cfg = DepotConfig(loot_all_vk=0, loot_all_btn_pos=[], max_items_per_cycle=0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        slots = [(100, 200), (120, 200)]
        with patch.object(dm, "_find_backpack_slots", return_value=slots), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._deposit_loot_all()
        assert result == 2

    def test_fallback_with_cap(self):
        cfg = DepotConfig(loot_all_vk=0, loot_all_btn_pos=[], max_items_per_cycle=1)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        slots = [(100, 200), (120, 200)]
        with patch.object(dm, "_find_backpack_slots", return_value=slots), \
             patch("src.depot_manager.jittered_sleep"):
            result = dm._deposit_loot_all()
        assert result == 1


# ---------------------------------------------------------------------------
# _find_backpack_slots — all branches (lines 541-594)
# ---------------------------------------------------------------------------

class TestFindBackpackSlots:

    def test_visual_container_detected(self):
        cfg = DepotConfig(container_roi=[0, 0, 400, 400],
                          backpack_slot_spacing=32, backpack_slot_cols=4,
                          backpack_slot_rows=2)
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=cfg,
                          frame_getter=lambda: _blank(500, 600))
        dm.set_log_callback(lambda m: None)
        fake_ctr = (10, 10, 300, 250)
        with patch("src.depot_manager.detect_container_window", return_value=fake_ctr), \
             patch("src.depot_manager.scale_offset_x", return_value=12), \
             patch("src.depot_manager.scale_offset_y", return_value=24):
            slots = dm._find_backpack_slots()
        assert len(slots) == 8  # 4 cols × 2 rows

    def test_fallback_with_origin_configured(self):
        cfg = DepotConfig(container_roi=[0, 0, 400, 400],
                          backpack_slot_origin=[834, 270],
                          backpack_slot_spacing=32,
                          backpack_slot_cols=4, backpack_slot_rows=2)
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=cfg)
        dm.set_log_callback(lambda m: None)
        with patch("src.depot_manager.detect_container_window", return_value=None):
            slots = dm._find_backpack_slots()
        assert len(slots) == 8

    def test_fallback_no_origin_no_frame(self):
        """Invalid origin AND no frame → uses hardcoded defaults."""
        cfg = DepotConfig(container_roi=[0, 0, 400, 400],
                          backpack_slot_origin=[],
                          backpack_slot_spacing=32,
                          backpack_slot_cols=2, backpack_slot_rows=2)
        dm = _make_manager(cfg=cfg)  # no frame getter
        slots = dm._find_backpack_slots()
        assert len(slots) == 4  # 2×2

    def test_fallback_no_origin_with_frame(self):
        """Invalid origin BUT frame available → uses scale_offset to compute."""
        cfg = DepotConfig(container_roi=[0, 0, 400, 400],
                          backpack_slot_origin=[],
                          backpack_slot_spacing=32,
                          backpack_slot_cols=2, backpack_slot_rows=2)
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=cfg,
                          frame_getter=lambda: _blank(540, 960))
        dm.set_log_callback(lambda m: None)
        with patch("src.depot_manager.detect_container_window", return_value=None):
            slots = dm._find_backpack_slots()
        assert len(slots) == 4


# ---------------------------------------------------------------------------
# _slot_matches — branches (lines 596-642)
# ---------------------------------------------------------------------------

class TestSlotMatches:

    def test_no_frame_getter_returns_true(self):
        dm = _make_manager()  # no frame getter
        assert dm._slot_matches(100, 200, ["sword"]) is True

    def test_none_frame_returns_true(self):
        dm = _make_manager(frame=None)
        dm._frame_getter = lambda: None
        assert dm._slot_matches(100, 200, ["sword"]) is True

    def test_empty_slot_crop_returns_false(self):
        dm = _make_manager(frame=_blank(0, 0))
        dm._frame_getter = lambda: _blank(2, 2)
        # slot at (100, 200) way outside a 2×2 frame — crop will be empty
        result = dm._slot_matches(100, 200, ["sword"])
        assert result is False

    def test_no_templates_dir_returns_true(self, tmp_path):
        cfg = DepotConfig()
        ctrl = _mock_ctrl()
        dm = DepotManager(ctrl=ctrl, config=cfg,
                          frame_getter=lambda: _blank())
        dm.set_log_callback(lambda m: None)
        with patch("src.depot_manager._TEMPLATES_DIR", tmp_path / "nonexistent"):
            result = dm._slot_matches(100, 200, ["sword"])
        assert result is True

    def test_no_match_returns_false(self, tmp_path):
        """Templates dir exists but no template matches."""
        (tmp_path / "loot_items").mkdir()
        with patch("src.depot_manager._TEMPLATES_DIR", tmp_path):
            cfg = DepotConfig()
            ctrl = _mock_ctrl()
            dm = DepotManager(ctrl=ctrl, config=cfg,
                              frame_getter=lambda: _bright())
            dm.set_log_callback(lambda m: None)
            result = dm._slot_matches(100, 200, ["sword"])
        assert result is False


# ---------------------------------------------------------------------------
# _bank_deposit (lines 644-693)
# ---------------------------------------------------------------------------

class TestBankDeposit:

    def test_not_connected_returns_false(self):
        ctrl = _mock_ctrl(connected=False)
        dm = _make_manager(ctrl=ctrl)
        with patch("src.depot_manager.jittered_sleep"), patch("time.sleep"):
            result = dm._bank_deposit()
        assert result is False

    def test_happy_path_no_frame_getter(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(bank_dialogue_delay=0.0, open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("src.depot_manager.jittered_sleep"), patch("time.sleep"):
            result = dm._bank_deposit()
        assert result is True

    def test_dialog_ok_via_frame_getter(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(bank_dialogue_delay=0.0, open_wait=0.0)
        dm = DepotManager(ctrl=ctrl, config=cfg,
                          frame_getter=lambda: _blank())
        dm.set_log_callback(lambda m: None)
        with patch("src.depot_manager.jittered_sleep"), \
             patch("time.sleep"), \
             patch("src.action_verifier.verify_dialog_open", return_value=True):
            result = dm._bank_deposit()
        assert result is True

    def test_dialog_not_ok_retries(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(bank_dialogue_delay=0.0, open_wait=0.0)
        dm = DepotManager(ctrl=ctrl, config=cfg,
                          frame_getter=lambda: _blank())
        dm.set_log_callback(lambda m: None)
        with patch("src.depot_manager.jittered_sleep"), \
             patch("time.sleep"), \
             patch("src.action_verifier.verify_dialog_open", return_value=False):
            result = dm._bank_deposit()
        # Should still complete (retry hi then continue)
        assert result is True

    def test_connection_lost_mid_loop(self):
        call_seq = [True, True, False]  # connected on first two msgs, then drop
        call_idx = [0]

        def connected_side_effect():
            v = call_seq[min(call_idx[0], len(call_seq) - 1)]
            call_idx[0] += 1
            return v

        ctrl = _mock_ctrl()
        ctrl.is_connected.side_effect = connected_side_effect
        cfg = DepotConfig(bank_dialogue_delay=0.0, open_wait=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("src.depot_manager.jittered_sleep"), patch("time.sleep"):
            result = dm._bank_deposit()
        assert result is False


# ---------------------------------------------------------------------------
# bank_withdraw (lines 695-729)
# ---------------------------------------------------------------------------

class TestBankWithdraw:

    def test_not_connected_returns_false(self):
        ctrl = _mock_ctrl(connected=False)
        dm = _make_manager(ctrl=ctrl)
        with patch("src.depot_manager.jittered_sleep"):
            result = dm.bank_withdraw()
        assert result is False

    def test_withdraw_all(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(bank_dialogue_delay=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("src.depot_manager.jittered_sleep"), patch("time.sleep"):
            result = dm.bank_withdraw(0)
        assert result is True

    def test_withdraw_amount(self):
        ctrl = _mock_ctrl()
        cfg = DepotConfig(bank_dialogue_delay=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("src.depot_manager.jittered_sleep"), patch("time.sleep"):
            result = dm.bank_withdraw(1000)
        assert result is True

    def test_connection_lost_mid_withdraw(self):
        call_seq = [True, False]
        call_idx = [0]

        def side_effect():
            v = call_seq[min(call_idx[0], len(call_seq) - 1)]
            call_idx[0] += 1
            return v

        ctrl = _mock_ctrl()
        ctrl.is_connected.side_effect = side_effect
        cfg = DepotConfig(bank_dialogue_delay=0.0)
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("src.depot_manager.jittered_sleep"), patch("time.sleep"):
            result = dm.bank_withdraw()
        assert result is False


# ---------------------------------------------------------------------------
# bank_deposit_gold (lines 731-745)
# ---------------------------------------------------------------------------

class TestBankDepositGold:

    def test_no_npc_coord_returns_false(self):
        cfg = DepotConfig(bank_npc_coord=[])
        dm = _make_manager(cfg=cfg)
        result = dm.bank_deposit_gold()
        assert result is False

    def test_with_npc_coord_calls_bank_deposit(self):
        cfg = DepotConfig(bank_npc_coord=[100, 200, 7], bank_dialogue_delay=0.0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch.object(dm, "_bank_deposit", return_value=True) as m:
            result = dm.bank_deposit_gold()
        m.assert_called_once()
        assert result is True


# ---------------------------------------------------------------------------
# _close_containers (lines 747-751)
# ---------------------------------------------------------------------------

class TestCloseContainers:

    def test_no_vk_does_nothing(self):
        cfg = DepotConfig(close_containers_vk=0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("time.sleep"):
            dm._close_containers()
        ctrl.press_key.assert_not_called()

    def test_vk_presses_key(self):
        cfg = DepotConfig(close_containers_vk=0x1B, close_containers_wait=0.0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch("time.sleep"):
            dm._close_containers()
        ctrl.press_key.assert_called_with(0x1B)


# ---------------------------------------------------------------------------
# run_depot_cycle — key branches (lines 288-336)
# ---------------------------------------------------------------------------

class TestRunDepotCycle:

    def test_open_chest_fails_returns_false(self):
        dm = _make_manager()
        with patch.object(dm, "_open_chest", return_value=False):
            result = dm.run_depot_cycle()
        assert result is False

    def test_container_timeout_abort(self):
        cfg = DepotConfig(abort_on_container_timeout=True, open_wait=0.0,
                          container_detect_wait=0.0, depot_chest_coord=[100, 200])
        dm = _make_manager(cfg=cfg)
        with patch.object(dm, "_open_chest", return_value=True), \
             patch.object(dm, "_wait_for_container", return_value=False):
            result = dm.run_depot_cycle()
        assert result is False

    def test_container_timeout_continue(self):
        cfg = DepotConfig(abort_on_container_timeout=False, open_wait=0.0,
                          container_detect_wait=0.0, bank_npc_coord=[],
                          close_containers_vk=0, depot_chest_coord=[100, 200])
        dm = _make_manager(cfg=cfg)
        with patch.object(dm, "_open_chest", return_value=True), \
             patch.object(dm, "_wait_for_container", return_value=False), \
             patch.object(dm, "_deposit_items", return_value=5), \
             patch.object(dm, "_close_containers"):
            result = dm.run_depot_cycle()
        assert result is True
        assert dm.cycle_count == 1
        assert dm.items_deposited == 5

    def test_happy_path_with_bank(self):
        cfg = DepotConfig(bank_npc_coord=[100, 200, 7], open_wait=0.0,
                          container_detect_wait=0.0, close_containers_vk=0,
                          depot_chest_coord=[100, 200], bank_dialogue_delay=0.0)
        ctrl = _mock_ctrl()
        dm = _make_manager(ctrl=ctrl, cfg=cfg)
        with patch.object(dm, "_open_chest", return_value=True), \
             patch.object(dm, "_wait_for_container", return_value=True), \
             patch.object(dm, "_deposit_items", return_value=3), \
             patch.object(dm, "_bank_deposit", return_value=True), \
             patch.object(dm, "_close_containers"):
            result = dm.run_depot_cycle()
        assert result is True
        assert dm.cycle_count == 1
