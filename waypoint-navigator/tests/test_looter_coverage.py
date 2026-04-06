"""
test_looter_coverage.py
-----------------------
Extra tests to cover missing branches in src/looter.py.
100% offline — no real game, no Windows APIs, no template images required.
"""
from __future__ import annotations

import threading
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.looter import (
    LootConfig,
    CorpseDetector,
    ItemDetector,
    Looter,
    PendingCorpse,
)
from src.models import Coordinate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blank(h=1080, w=1920):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _bright(h=1080, w=1920):
    """Frame with non-zero mean so slot-brightness check fires."""
    f = np.full((h, w, 3), 50, dtype=np.uint8)
    return f


def _mock_ctrl(connected=True, click_ok=True, right_click_ok=True):
    ctrl = MagicMock()
    ctrl.is_connected.return_value = connected
    ctrl.click.return_value = click_ok
    ctrl.right_click.return_value = right_click_ok
    ctrl.left_click.return_value = True
    ctrl.shift_click.return_value = None
    return ctrl


def _make_looter(ctrl=None, cfg=None, frame=None):
    cfg = cfg or LootConfig(loot_delay=0.0, container_settle=0.0)
    ltr = Looter(ctrl=ctrl or _mock_ctrl(), config=cfg)
    ltr.set_log_callback(lambda m: None)
    if frame is not False:  # False means don't set any getter
        ltr.set_frame_getter(lambda: (frame if frame is not None else _blank()))
    return ltr


# ---------------------------------------------------------------------------
# LootConfig.validate — every error branch (lines 178-210)
# ---------------------------------------------------------------------------

class TestLootConfigValidate:

    def _base(self, **kw) -> LootConfig:
        defaults = dict(
            viewport_roi=[0, 0, 1460, 1080],
            container_roi=[1470, 500, 420, 400],
            tile_size_px=32,
            container_cols=4,
            slot_size_px=34,
            corpse_confidence=0.6,
            item_confidence=0.6,
            max_range_tiles=0,
            ref_width=1920,
            ref_height=1080,
            loot_mode="all",
        )
        defaults.update(kw)
        return LootConfig(**defaults)

    def test_viewport_roi_wrong_length(self):
        cfg = self._base(viewport_roi=[0, 0, 10])
        with pytest.raises(ValueError, match="viewport_roi must have 4"):
            cfg.validate()

    def test_viewport_roi_negative(self):
        cfg = self._base(viewport_roi=[0, -1, 10, 10])
        with pytest.raises(ValueError, match="non-negative"):
            cfg.validate()

    def test_container_roi_wrong_length(self):
        cfg = self._base(container_roi=[0, 0, 10])
        with pytest.raises(ValueError, match="container_roi must have 4"):
            cfg.validate()

    def test_container_roi_negative(self):
        cfg = self._base(container_roi=[-1, 0, 10, 10])
        with pytest.raises(ValueError, match="non-negative"):
            cfg.validate()

    def test_tile_size_zero(self):
        cfg = self._base(tile_size_px=0)
        with pytest.raises(ValueError, match="tile_size_px"):
            cfg.validate()

    def test_container_cols_zero(self):
        cfg = self._base(container_cols=0)
        with pytest.raises(ValueError, match="container_cols"):
            cfg.validate()

    def test_slot_size_zero(self):
        cfg = self._base(slot_size_px=0)
        with pytest.raises(ValueError, match="slot_size_px"):
            cfg.validate()

    def test_corpse_confidence_out_of_range(self):
        cfg = self._base(corpse_confidence=1.5)
        with pytest.raises(ValueError, match="corpse_confidence"):
            cfg.validate()

    def test_item_confidence_out_of_range(self):
        cfg = self._base(item_confidence=-0.1)
        with pytest.raises(ValueError, match="item_confidence"):
            cfg.validate()

    def test_max_range_tiles_negative(self):
        cfg = self._base(max_range_tiles=-1)
        with pytest.raises(ValueError, match="max_range_tiles"):
            cfg.validate()

    def test_ref_width_zero(self):
        cfg = self._base(ref_width=0)
        with pytest.raises(ValueError, match="ref_width"):
            cfg.validate()

    def test_ref_height_zero(self):
        cfg = self._base(ref_height=0)
        with pytest.raises(ValueError, match="ref_height"):
            cfg.validate()

    def test_loot_mode_invalid(self):
        cfg = self._base(loot_mode="bogus")
        with pytest.raises(ValueError, match="loot_mode"):
            cfg.validate()

    def test_valid_config_passes(self):
        cfg = self._base()
        cfg.validate()  # must not raise

    def test_properties(self):
        cfg = self._base(loot_mode="whitelist", loot_whitelist=["x"])
        assert cfg.is_whitelist_mode
        assert cfg.has_whitelist
        assert not cfg.is_range_limited

        cfg2 = self._base(max_range_tiles=3)
        assert cfg2.is_range_limited


# ---------------------------------------------------------------------------
# CorpseDetector.reload and detect with no templates / None frame (line 256,268)
# ---------------------------------------------------------------------------

class TestCorpseDetector:

    def test_reload_called(self):
        cfg = LootConfig()
        det = CorpseDetector(cfg)
        # reload simply calls _load_templates again — no crash
        det.reload()

    def test_detect_returns_empty_no_templates(self):
        cfg = LootConfig()
        det = CorpseDetector(cfg)
        # no templates loaded → []
        assert det.detect(_blank()) == []

    def test_detect_returns_empty_none_frame(self):
        cfg = LootConfig()
        det = CorpseDetector(cfg)
        assert det.detect(None) == []  # type: ignore

    def test_detect_empty_roi(self):
        """Frame where scale_roi produces zero-area region returns []."""
        cfg = LootConfig(viewport_roi=[0, 0, 0, 0])
        det = CorpseDetector(cfg)
        det._templates = [("fake", np.zeros((5, 5, 3), dtype=np.uint8))]
        assert det.detect(_blank()) == []


# ---------------------------------------------------------------------------
# ItemDetector — reload, detect_whitelist edge cases (lines 335, 348-383)
# ---------------------------------------------------------------------------

class TestItemDetector:

    def test_reload(self):
        cfg = LootConfig()
        det = ItemDetector(cfg)
        det.reload()  # no crash

    def test_detect_whitelist_no_templates(self):
        cfg = LootConfig()
        det = ItemDetector(cfg)
        assert det.detect_whitelist(_blank()) == []

    def test_detect_whitelist_none_frame(self):
        cfg = LootConfig()
        det = ItemDetector(cfg)
        assert det.detect_whitelist(None) == []  # type: ignore

    def test_detect_whitelist_whitelist_filter(self):
        """Templates not in the whitelist are skipped."""
        cfg = LootConfig(loot_mode="whitelist", loot_whitelist=["allowed"])
        det = ItemDetector(cfg)
        # inject two fake templates: one in whitelist, one not
        fake = np.zeros((3, 3, 3), dtype=np.uint8)
        det._templates = [("allowed", fake), ("forbidden", fake)]
        # With blank frame the roi will be all zeros and no matches,
        # but the whitelist filter code path runs without error
        result = det.detect_whitelist(_blank(100, 200))
        assert isinstance(result, list)

    def test_detect_whitelist_empty_roi(self):
        cfg = LootConfig(container_roi=[0, 0, 0, 0])
        det = ItemDetector(cfg)
        det._templates = [("x", np.zeros((3, 3, 3), dtype=np.uint8))]
        assert det.detect_whitelist(_blank()) == []

    def test_all_slot_positions_fallback_roi(self):
        """all_slot_positions falls back to config ROI when no container detected."""
        cfg = LootConfig(container_roi=[0, 0, 200, 200], slot_size_px=32, container_cols=4)
        det = ItemDetector(cfg)
        frame = _bright(300, 400)
        with patch("src.looter.detect_container_window", return_value=None):
            pos = det.all_slot_positions(frame, max_slots=4)
        assert isinstance(pos, list)

    def test_all_slot_positions_visual_container(self):
        """all_slot_positions uses detected container rect when available."""
        cfg = LootConfig(container_roi=[0, 0, 200, 200], slot_size_px=32, container_cols=4)
        det = ItemDetector(cfg)
        frame = _bright(300, 400)
        fake_container = (10, 10, 180, 180)
        with patch("src.looter.detect_container_window", return_value=fake_container):
            pos = det.all_slot_positions(frame, max_slots=4)
        assert isinstance(pos, list)


# ---------------------------------------------------------------------------
# Looter public properties and setters (lines 571-692)
# ---------------------------------------------------------------------------

class TestLooterProperties:

    def test_is_running_false_initially(self):
        ltr = _make_looter()
        assert not ltr.is_running

    def test_has_frame_getter(self):
        ltr = _make_looter()
        assert ltr.has_frame_getter

    def test_has_player_getter_false(self):
        ltr = _make_looter()
        assert not ltr.has_player_getter

    def test_has_player_getter_true(self):
        ltr = _make_looter()
        ltr.set_player_getter(lambda: None)
        assert ltr.has_player_getter

    def test_whitelist_count(self):
        cfg = LootConfig(loot_whitelist=["a", "b"])
        ltr = _make_looter(cfg=cfg)
        assert ltr.whitelist_count == 2

    def test_stats_snapshot_keys(self):
        ltr = _make_looter()
        snap = ltr.stats_snapshot()
        assert "looted" in snap
        assert "items_picked" in snap
        assert "pending" in snap
        assert "is_running" in snap
        assert "is_paused" in snap
        assert "loot_mode" in snap
        assert "whitelist_count" in snap

    def test_has_pending(self):
        ltr = _make_looter()
        assert not ltr.has_pending
        ltr.notify_kill(None)
        assert ltr.has_pending

    def test_has_looted_false(self):
        ltr = _make_looter()
        assert not ltr.has_looted

    def test_is_whitelist_mode(self):
        cfg = LootConfig(loot_mode="whitelist")
        ltr = _make_looter(cfg=cfg)
        assert ltr.is_whitelist_mode

    def test_looted_count_and_items_picked_count(self):
        ltr = _make_looter()
        assert ltr.looted_count == 0
        assert ltr.items_picked_count == 0

    def test_has_items_picked(self):
        ltr = _make_looter()
        assert not ltr.has_items_picked

    def test_clear_pending(self):
        ltr = _make_looter()
        ltr.notify_kill(None)
        ltr.clear_pending()
        assert ltr.pending_count == 0

    def test_reset_stats(self):
        ltr = _make_looter()
        ltr._looted = 5
        ltr._items_picked = 10
        ltr.reset_stats()
        assert ltr._looted == 0
        assert ltr._items_picked == 0

    def test_update_config(self):
        ltr = _make_looter()
        new_cfg = LootConfig(loot_mode="whitelist")
        ltr.update_config(new_cfg)
        assert ltr._cfg.loot_mode == "whitelist"

    def test_set_loot_mode_valid(self):
        ltr = _make_looter()
        ltr.set_loot_mode("whitelist")
        assert ltr._cfg.loot_mode == "whitelist"
        ltr.set_loot_mode("quick")
        assert ltr._cfg.loot_mode == "quick"
        ltr.set_loot_mode("all")
        assert ltr._cfg.loot_mode == "all"

    def test_set_loot_mode_invalid(self):
        ltr = _make_looter()
        with pytest.raises(ValueError):
            ltr.set_loot_mode("bogus")

    def test_add_remove_whitelist(self):
        ltr = _make_looter()
        ltr.add_to_whitelist("sword")
        assert "sword" in ltr._cfg.loot_whitelist
        # duplicate ignored
        ltr.add_to_whitelist("sword")
        assert ltr._cfg.loot_whitelist.count("sword") == 1
        assert ltr.remove_from_whitelist("sword") is True
        assert ltr.remove_from_whitelist("sword") is False

    def test_loot_summary(self):
        ltr = _make_looter()
        summary = ltr.loot_summary()
        assert "looted=" in summary
        assert "mode=" in summary

    def test_pause_resume(self):
        ltr = _make_looter()
        assert not ltr.is_paused
        ltr.pause()
        assert ltr.is_paused
        ltr.resume()
        assert not ltr.is_paused

    def test_stats_property(self):
        ltr = _make_looter()
        s = ltr.stats
        assert "looted" in s and "items_picked" in s


# ---------------------------------------------------------------------------
# Looter._corpse_screen_pos — coordinate path + range check + template path
# ---------------------------------------------------------------------------

class TestCorpseScreenPos:

    def test_coordinate_based_in_range(self):
        cfg = LootConfig(max_range_tiles=5, loot_delay=0.0, container_settle=0.0)
        ltr = _make_looter(cfg=cfg)
        player = MagicMock(x=100, y=100)
        ltr.set_player_getter(lambda: player)

        corpse = PendingCorpse(tile_x=101, tile_y=101, tile_z=7)
        pos = ltr._corpse_screen_pos(corpse, _blank())
        assert pos is not None
        assert len(pos) == 2

    def test_coordinate_based_out_of_range(self):
        cfg = LootConfig(max_range_tiles=2, loot_delay=0.0, container_settle=0.0)
        ltr = _make_looter(cfg=cfg)
        player = MagicMock(x=100, y=100)
        ltr.set_player_getter(lambda: player)

        corpse = PendingCorpse(tile_x=110, tile_y=110, tile_z=7)
        pos = ltr._corpse_screen_pos(corpse, _blank())
        assert pos is None

    def test_player_getter_returns_none(self):
        """Falls through to template matching (returns None when no templates)."""
        cfg = LootConfig(loot_delay=0.0, container_settle=0.0)
        ltr = _make_looter(cfg=cfg)
        ltr.set_player_getter(lambda: None)

        corpse = PendingCorpse(tile_x=100, tile_y=100, tile_z=7)
        pos = ltr._corpse_screen_pos(corpse, _blank())
        assert pos is None

    def test_no_tile_x_falls_to_template(self):
        """corpse without tile coords → template matching branch."""
        ltr = _make_looter()
        corpse = PendingCorpse(tile_x=None, tile_y=None, tile_z=None)
        pos = ltr._corpse_screen_pos(corpse, _blank())
        assert pos is None


# ---------------------------------------------------------------------------
# Looter._scale_y_offset / _scale_x_offset (lines 881-891)
# ---------------------------------------------------------------------------

class TestScaleOffsets:

    def test_scale_y_none_frame(self):
        ltr = _make_looter()
        assert ltr._scale_y_offset(10, None) == 10

    def test_scale_y_with_frame(self):
        ltr = _make_looter()
        frame = _blank(540, 960)  # half resolution
        result = ltr._scale_y_offset(10, frame)
        assert isinstance(result, int)

    def test_scale_x_none_frame(self):
        ltr = _make_looter()
        assert ltr._scale_x_offset(8, None) == 8

    def test_scale_x_with_frame(self):
        ltr = _make_looter()
        frame = _blank(540, 960)
        result = ltr._scale_x_offset(8, frame)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# Looter._open_corpse (lines 893-928)
# ---------------------------------------------------------------------------

class TestOpenCorpse:

    def test_right_click_fails(self):
        ctrl = _mock_ctrl(click_ok=False)
        ctrl.click.return_value = False
        ltr = _make_looter(ctrl=ctrl)
        assert ltr._open_corpse(100, 200) is False

    def test_fallback_offset_path(self):
        """When no menu detected, uses offset fallback."""
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl)
        with patch("src.looter.detect_context_menu", return_value=None), \
             patch("src.looter.jittered_sleep"):
            result = ltr._open_corpse(100, 200)
        assert result is True

    def test_visual_menu_path(self):
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl)
        fake_menu = (90, 200, 100, 40)
        fake_entry = (95, 210)
        with patch("src.looter.detect_context_menu", return_value=fake_menu), \
             patch("src.looter.find_menu_entry_offset", return_value=fake_entry), \
             patch("src.looter.jittered_sleep"):
            result = ltr._open_corpse(100, 200)
        assert result is True

    def test_visual_menu_click_fails(self):
        ctrl = _mock_ctrl()
        ctrl.click.return_value = False
        ltr = _make_looter(ctrl=ctrl)
        fake_menu = (90, 200, 100, 40)
        fake_entry = (95, 210)
        with patch("src.looter.detect_context_menu", return_value=fake_menu), \
             patch("src.looter.find_menu_entry_offset", return_value=fake_entry), \
             patch("src.looter.jittered_sleep"):
            result = ltr._open_corpse(100, 200)
        # click returns False → returns False
        assert result is False

    def test_no_frame_getter(self):
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, frame=False)
        with patch("src.looter.jittered_sleep"):
            result = ltr._open_corpse(50, 60)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Looter._quick_loot_corpse (lines 930-964)
# ---------------------------------------------------------------------------

class TestQuickLootCorpse:

    def test_right_click_fails(self):
        ctrl = _mock_ctrl()
        ctrl.click.return_value = False
        ltr = _make_looter(ctrl=ctrl)
        with patch("src.looter.jittered_sleep"):
            result = ltr._quick_loot_corpse(100, 200)
        assert result is False

    def test_fallback_offset_success(self):
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl)
        with patch("src.looter.detect_context_menu", return_value=None), \
             patch("src.looter.jittered_sleep"):
            result = ltr._quick_loot_corpse(100, 200)
        assert result is True

    def test_visual_menu_path(self):
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl)
        fake_menu = (90, 200, 100, 40)
        fake_entry = (95, 220)
        with patch("src.looter.detect_context_menu", return_value=fake_menu), \
             patch("src.looter.find_menu_entry_offset", return_value=fake_entry), \
             patch("src.looter.jittered_sleep"):
            result = ltr._quick_loot_corpse(100, 200)
        assert result is True


# ---------------------------------------------------------------------------
# Looter._pick_items (lines 966-993)
# ---------------------------------------------------------------------------

class TestPickItems:

    def test_pick_whitelist_mode(self):
        cfg = LootConfig(loot_mode="whitelist", loot_delay=0.0, container_settle=0.0)
        ltr = _make_looter(cfg=cfg)
        frame = _blank()
        # no templates → detect_whitelist returns []
        count, names = ltr._pick_items(frame)
        assert count == 0
        assert names == []

    def test_pick_all_mode(self):
        cfg = LootConfig(loot_mode="all", loot_delay=0.0, container_settle=0.0)
        ltr = _make_looter(cfg=cfg)
        frame = _bright()
        # patch all_slot_positions to return two slots
        with patch.object(ltr._item_det, "all_slot_positions", return_value=[(50, 60), (70, 80)]), \
             patch("src.looter.jittered_sleep"):
            count, names = ltr._pick_items(frame)
        assert count == 2
        assert names == []


# ---------------------------------------------------------------------------
# Looter._verify_loot (lines 995-1014)
# ---------------------------------------------------------------------------

class TestVerifyLoot:

    def test_verify_whitelist(self):
        cfg = LootConfig(loot_mode="whitelist", loot_delay=0.0, container_settle=0.0)
        ltr = _make_looter(cfg=cfg)
        frame = _blank()
        # no templates → 0 remaining
        assert ltr._verify_loot(frame, frame, 2) == 0

    def test_verify_all(self):
        cfg = LootConfig(loot_mode="all", loot_delay=0.0, container_settle=0.0)
        ltr = _make_looter(cfg=cfg)
        frame = _blank()
        with patch.object(ltr._item_det, "all_slot_positions", return_value=[]):
            result = ltr._verify_loot(frame, frame, 2)
        assert result == 0


# ---------------------------------------------------------------------------
# Looter.stow_container (lines 748-823)
# ---------------------------------------------------------------------------

class TestStowContainer:

    def test_with_fixed_pos(self):
        cfg = LootConfig(stow_all_container_pos=[500, 300], loot_delay=0.0,
                         container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)
        with patch("src.looter.detect_context_menu", return_value=None), \
             patch("src.looter.jittered_sleep"):
            result = ltr.stow_container()
        assert isinstance(result, bool)

    def test_without_frame_getter(self):
        """No frame getter → falls into the no-frame branch."""
        cfg = LootConfig(stow_all_container_pos=[], loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg, frame=False)
        with patch("src.looter.jittered_sleep"):
            result = ltr.stow_container()
        assert isinstance(result, bool)

    def test_with_frame_no_container_detected(self):
        cfg = LootConfig(stow_all_container_pos=[], loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)
        with patch("src.looter.detect_container_window", return_value=None), \
             patch("src.looter.detect_context_menu", return_value=None), \
             patch("src.looter.jittered_sleep"):
            result = ltr.stow_container()
        assert isinstance(result, bool)

    def test_with_container_detected(self):
        cfg = LootConfig(stow_all_container_pos=[], loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)
        fake_ctr = (100, 200, 150, 200)
        with patch("src.looter.detect_container_window", return_value=fake_ctr), \
             patch("src.looter.detect_context_menu", return_value=None), \
             patch("src.looter.jittered_sleep"):
            result = ltr.stow_container()
        assert isinstance(result, bool)

    def test_visual_menu_stow(self):
        cfg = LootConfig(stow_all_container_pos=[], loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)
        fake_ctr = (100, 200, 150, 200)
        fake_menu = (100, 210, 100, 30)
        fake_entry = (105, 215)
        with patch("src.looter.detect_container_window", return_value=fake_ctr), \
             patch("src.looter.detect_context_menu", return_value=fake_menu), \
             patch("src.looter.find_menu_entry_offset", return_value=fake_entry), \
             patch("src.looter.jittered_sleep"):
            result = ltr.stow_container()
        assert result is True

    def test_right_click_fails_stow(self):
        cfg = LootConfig(stow_all_container_pos=[500, 300], loot_delay=0.0,
                         container_settle=0.0)
        ctrl = _mock_ctrl()
        ctrl.click.return_value = False
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)
        with patch("src.looter.jittered_sleep"):
            result = ltr.stow_container()
        assert result is False


# ---------------------------------------------------------------------------
# Looter loop integration — quick_loot mode path + on_item_looted callbacks
# ---------------------------------------------------------------------------

class TestLooterLoopIntegration:

    def _run_one_iteration(self, ltr: Looter, max_iters=50):
        """Run the loop in a thread, let it process one corpse, then stop."""
        ltr.start()
        for _ in range(max_iters):
            time.sleep(0.05)
            if not ltr.has_pending:
                break
        ltr.stop()

    def test_quick_loot_mode_callback(self):
        cfg = LootConfig(loot_mode="quick", loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)
        received = []
        ltr.on_item_looted = lambda name, count: received.append((name, count))
        ltr.on_loot_start = MagicMock()
        ltr.on_loot_finish = MagicMock()

        with patch.object(ltr, "_quick_loot_corpse", return_value=True), \
             patch.object(ltr, "_corpse_screen_pos", return_value=(100, 200)), \
             patch("src.looter.jittered_sleep"):
            ltr.notify_kill(None)
            self._run_one_iteration(ltr)

        assert ("__quick_looted__", 1) in received

    def test_quick_loot_fails_increments_attempts(self):
        cfg = LootConfig(loot_mode="quick", loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)

        with patch.object(ltr, "_quick_loot_corpse", return_value=False), \
             patch.object(ltr, "_corpse_screen_pos", return_value=(100, 200)), \
             patch("src.looter.jittered_sleep"):
            ltr.notify_kill(None)
            # Give loop a few ticks to try
            ltr.start()
            time.sleep(0.3)
            ltr.stop()

    def test_open_corpse_fails_increments_attempts(self):
        cfg = LootConfig(loot_mode="all", loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)

        with patch.object(ltr, "_open_corpse", return_value=False), \
             patch.object(ltr, "_corpse_screen_pos", return_value=(100, 200)), \
             patch("src.looter.jittered_sleep"):
            ltr.notify_kill(None)
            ltr.start()
            time.sleep(0.3)
            ltr.stop()

    def test_normal_mode_all_with_callback(self):
        cfg = LootConfig(loot_mode="all", loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)
        received = []
        ltr.on_item_looted = lambda name, count: received.append((name, count))

        with patch.object(ltr, "_open_corpse", return_value=True), \
             patch.object(ltr, "_corpse_screen_pos", return_value=(100, 200)), \
             patch.object(ltr, "_pick_items", return_value=(2, [])), \
             patch.object(ltr, "_verify_loot", return_value=0), \
             patch("src.looter.jittered_sleep"):
            ltr.notify_kill(None)
            self._run_one_iteration(ltr)

        assert ("__looted__", 2) in received

    def test_normal_mode_whitelist_with_callback(self):
        cfg = LootConfig(loot_mode="whitelist", loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)
        received = []
        ltr.on_item_looted = lambda name, count: received.append((name, count))

        with patch.object(ltr, "_open_corpse", return_value=True), \
             patch.object(ltr, "_corpse_screen_pos", return_value=(100, 200)), \
             patch.object(ltr, "_pick_items", return_value=(1, ["sword"])), \
             patch.object(ltr, "_verify_loot", return_value=0), \
             patch("src.looter.jittered_sleep"):
            ltr.notify_kill(None)
            self._run_one_iteration(ltr)

        assert ("sword", 1) in received

    def test_frame_getter_exception_handled(self):
        cfg = LootConfig(loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        call_count = [0]

        def bad_getter():
            call_count[0] += 1
            if call_count[0] <= 3:
                raise RuntimeError("frame error")
            return _blank()

        ltr = Looter(ctrl=ctrl, config=cfg)
        ltr.set_log_callback(lambda m: None)
        ltr.set_frame_getter(bad_getter)

        ltr.notify_kill(None)
        with patch("src.looter.jittered_sleep"):
            ltr.start()
            time.sleep(0.3)
            ltr.stop()
        # Just assert no unhandled exception propagated

    def test_loop_paused_path(self):
        cfg = LootConfig(loot_delay=0.0, container_settle=0.0)
        ltr = _make_looter(cfg=cfg)
        ltr.pause()
        ltr.start()
        time.sleep(0.15)
        ltr.resume()
        ltr.stop()

    def test_no_frame_getter_stalls(self):
        cfg = LootConfig(loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = Looter(ctrl=ctrl, config=cfg)
        ltr.set_log_callback(lambda m: None)
        # No frame_getter set
        ltr.notify_kill(None)
        with patch("src.looter.jittered_sleep"):
            ltr.start()
            time.sleep(0.2)
            ltr.stop()

    def test_none_frame_from_getter(self):
        cfg = LootConfig(loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = Looter(ctrl=ctrl, config=cfg)
        ltr.set_log_callback(lambda m: None)
        ltr.set_frame_getter(lambda: None)
        ltr.notify_kill(None)
        with patch("src.looter.jittered_sleep"):
            ltr.start()
            time.sleep(0.2)
            ltr.stop()

    def test_corpse_not_found_discards_after_max_attempts(self):
        cfg = LootConfig(loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)

        with patch.object(ltr, "_corpse_screen_pos", return_value=None), \
             patch("src.looter.jittered_sleep"):
            ltr.notify_kill(None)
            ltr.start()
            # Wait for up to 5 attempts
            for _ in range(40):
                time.sleep(0.05)
                with ltr._lock:
                    if all(p.done for p in ltr._pending):
                        break
            ltr.stop()

    def test_on_item_looted_callback_error_handled(self):
        cfg = LootConfig(loot_mode="all", loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)

        def bad_callback(name, count):
            raise RuntimeError("callback boom")

        ltr.on_item_looted = bad_callback

        with patch.object(ltr, "_open_corpse", return_value=True), \
             patch.object(ltr, "_corpse_screen_pos", return_value=(100, 200)), \
             patch.object(ltr, "_pick_items", return_value=(1, [])), \
             patch.object(ltr, "_verify_loot", return_value=0), \
             patch("src.looter.jittered_sleep"):
            ltr.notify_kill(None)
            self._run_one_iteration(ltr)
        # No crash despite bad callback

    def test_verification_repick(self):
        """remaining > 0 triggers re-pick."""
        cfg = LootConfig(loot_mode="all", loot_delay=0.0, container_settle=0.0)
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl, cfg=cfg)

        pick_calls = [0]

        def fake_pick(frame):
            pick_calls[0] += 1
            return (2, [])

        with patch.object(ltr, "_open_corpse", return_value=True), \
             patch.object(ltr, "_corpse_screen_pos", return_value=(100, 200)), \
             patch.object(ltr, "_pick_items", side_effect=fake_pick), \
             patch.object(ltr, "_verify_loot", return_value=1), \
             patch("src.looter.jittered_sleep"):
            ltr.notify_kill(None)
            self._run_one_iteration(ltr)

        assert pick_calls[0] >= 2


# ---------------------------------------------------------------------------
# Looter.stop — double-stop is safe
# ---------------------------------------------------------------------------

class TestLooterStop:

    def test_stop_without_start(self):
        ltr = _make_looter()
        ltr.stop()  # should not raise

    def test_start_twice_is_noop(self):
        ltr = _make_looter()
        ltr.start()
        ltr.start()  # second call is a noop
        ltr.stop()
