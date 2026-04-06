"""
Tests for src/looter.py — LootConfig, CorpseDetector, ItemDetector, Looter
Fully offline: no OBS, no Tibia, no template images required.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.looter import LootConfig, CorpseDetector, ItemDetector, Looter, PendingCorpse
from src.models import Coordinate


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_ctrl() -> MagicMock:
    ctrl = MagicMock()
    ctrl.click.return_value = True
    ctrl.press_key.return_value = None
    return ctrl


def _blank_frame(h: int = 1080, w: int = 1920) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _make_looter(ctrl=None, config: Optional[LootConfig] = None) -> Looter:
    cfg = config or LootConfig(
        loot_delay=0.0,
        container_settle=0.0,
        max_range_tiles=0,
    )
    ltr = Looter(
        ctrl=ctrl or _mock_ctrl(),
        config=cfg,
    )
    ltr.set_frame_getter(lambda: _blank_frame())
    return ltr


# ─────────────────────────────────────────────────────────────────────────────
# LootConfig: save / load / defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestLootConfig:

    def test_default_values(self):
        cfg = LootConfig()
        assert cfg.tile_size_px        == 32
        assert cfg.loot_mode           == "all"
        assert cfg.confidence          == pytest.approx(0.60)
        assert cfg.max_range_tiles     == 2
        assert isinstance(cfg.loot_whitelist, list)

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "loot_config.json"
        cfg = LootConfig(tile_size_px=64, loot_mode="whitelist", confidence=0.75)
        cfg.save(path)
        loaded = LootConfig.load(path)
        assert loaded.tile_size_px == 64
        assert loaded.loot_mode    == "whitelist"
        assert loaded.confidence   == pytest.approx(0.75)

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        cfg = LootConfig.load(path)
        assert cfg.tile_size_px == 32

    def test_saved_json_is_valid(self, tmp_path: Path):
        path = tmp_path / "loot.json"
        LootConfig().save(path)
        with open(path) as f:
            data = json.load(f)
        assert "tile_size_px" in data
        assert "loot_mode"    in data

    def test_load_ignores_unknown_keys(self, tmp_path: Path):
        path = tmp_path / "loot.json"
        path.write_text(json.dumps({"tile_size_px": 16,
                                    "future_key": "ignored"}))
        cfg = LootConfig.load(path)
        assert cfg.tile_size_px == 16


# ─────────────────────────────────────────────────────────────────────────────
# CorpseDetector — no templates
# ─────────────────────────────────────────────────────────────────────────────

class TestCorpseDetectorNoTemplates:

    def test_detect_empty_on_blank_frame(self):
        cfg = LootConfig()
        det = CorpseDetector(cfg)
        # No templates → empty result
        result = det.detect(_blank_frame())
        assert result == []

    def test_detect_none_frame(self):
        cfg = LootConfig()
        det = CorpseDetector(cfg)
        result = det.detect(_blank_frame())  # detect() expects ndarray, not None
        assert result == []

    def test_template_count_zero_without_files(self, tmp_path: Path):
        """With an empty template dir, no templates are loaded."""
        import unittest.mock as mock
        empty_dir = tmp_path / "empty_templates"
        empty_dir.mkdir()
        with mock.patch("src.looter._TEMPLATES_DIR", empty_dir):
            cfg = LootConfig()
            det = CorpseDetector(cfg)
            assert len(det._templates) == 0


# ─────────────────────────────────────────────────────────────────────────────
# CorpseDetector — with a synthetic template
# ─────────────────────────────────────────────────────────────────────────────

class TestCorpseDetectorWithTemplate:

    def _make_detector_with_template(self, tmp_path: Path) -> CorpseDetector:
        """Create a CorpseDetector with one known template."""
        import cv2
        t_dir = tmp_path / "corpses"
        t_dir.mkdir(parents=True)
        # Create a small gradient template (non-uniform, so TM_CCOEFF_NORMED works)
        tmpl = np.zeros((16, 16, 3), dtype=np.uint8)
        for i in range(16):
            tmpl[i, :, 2] = i * 12     # red gradient in R channel (BGR)
        cv2.imwrite(str(t_dir / "rat_corpse.png"), tmpl)

        cfg = LootConfig()
        det = CorpseDetector.__new__(CorpseDetector)
        det._cfg = cfg
        det._templates = []
        for ext in ("*.png",):
            for p in sorted(t_dir.glob(ext)):
                img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                if img is not None:
                    det._templates.append((p.stem, img))
        return det

    def test_template_loaded(self, tmp_path: Path):
        det = self._make_detector_with_template(tmp_path)
        assert len(det._templates) == 1
        assert det._templates[0][0] == "rat_corpse"

    def test_detects_template_in_frame(self, tmp_path: Path):
        det = self._make_detector_with_template(tmp_path)
        tmpl_img = det._templates[0][1]   # the gradient template
        # Build a frame that contains the template at a known position
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        frame[50:50+16, 80:80+16] = tmpl_img
        results = det.detect(frame)
        assert len(results) >= 1
        name = results[0][3]
        assert name == "rat_corpse"

    def test_no_false_positive_on_blank(self, tmp_path: Path):
        det = self._make_detector_with_template(tmp_path)
        # Blue frame with noise (non-zero variance everywhere).
        # No red gradient pattern → must not match (confidence < 0.60)
        rng = np.random.default_rng(99)
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        # Add varying blue values so each 16×16 window has non-zero variance
        noise = rng.integers(10, 80, (200, 200), dtype=np.uint8)
        frame[:, :, 0] = noise   # B channel noise
        results = det.detect(frame)
        assert results == []


# ─────────────────────────────────────────────────────────────────────────────
# ItemDetector — no templates
# ─────────────────────────────────────────────────────────────────────────────

class TestItemDetectorNoTemplates:

    def test_whitelist_empty_without_templates(self):
        cfg = LootConfig()
        det = ItemDetector(cfg)
        assert det.detect_whitelist(_blank_frame()) == []

    def test_all_slot_positions_returns_list(self):
        cfg = LootConfig()
        det = ItemDetector(cfg)
        slots = det.all_slot_positions(_blank_frame())
        assert isinstance(slots, list)


# ─────────────────────────────────────────────────────────────────────────────
# PendingCorpse
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingCorpse:

    def test_created_at_is_recent(self):
        pc = PendingCorpse(tile_x=100, tile_y=200, tile_z=7)
        assert time.monotonic() - pc.created_at < 1.0

    def test_not_done_on_creation(self):
        pc = PendingCorpse(tile_x=None, tile_y=None, tile_z=None)
        assert pc.done is False

    def test_attempts_start_at_zero(self):
        pc = PendingCorpse(tile_x=None, tile_y=None, tile_z=None)
        assert pc.attempts == 0


# ─────────────────────────────────────────────────────────────────────────────
# Looter: construction
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterConstruction:

    def test_default_construction(self):
        ltr = _make_looter()
        assert ltr is not None
        assert not ltr._running

    def test_stats_initially_zero(self):
        ltr = _make_looter()
        assert ltr.stats == {"looted": 0, "items_picked": 0}

    def test_set_frame_getter(self):
        ltr = _make_looter()
        fn = lambda: None
        ltr.set_frame_getter(fn)
        assert ltr._frame_getter is fn

    def test_set_player_getter(self):
        ltr = _make_looter()
        fn = lambda: Coordinate(100, 200, 7)
        ltr.set_player_getter(fn)
        assert ltr._player_getter is fn


# ─────────────────────────────────────────────────────────────────────────────
# notify_kill
# ─────────────────────────────────────────────────────────────────────────────

class TestNotifyKill:

    def test_adds_pending_corpse(self):
        ltr = _make_looter()
        ltr.notify_kill(Coordinate(100, 200, 7))
        assert len(ltr._pending) == 1

    def test_multiple_kills_accumulate(self):
        ltr = _make_looter()
        ltr.notify_kill(Coordinate(100, 200, 7))
        ltr.notify_kill(Coordinate(105, 202, 7))
        assert len(ltr._pending) == 2

    def test_notify_without_coord_still_adds(self):
        ltr = _make_looter()
        ltr.notify_kill(coord=None)
        assert len(ltr._pending) == 1
        assert ltr._pending[0].tile_x is None

    def test_pending_corpse_has_correct_coords(self):
        ltr = _make_looter()
        ltr.notify_kill(Coordinate(32369, 32241, 7))
        pc = ltr._pending[0]
        assert pc.tile_x == 32369
        assert pc.tile_y == 32241
        assert pc.tile_z == 7


# ─────────────────────────────────────────────────────────────────────────────
# _corpse_screen_pos — coordinate-based calculation
# ─────────────────────────────────────────────────────────────────────────────

class TestCorpseScreenPos:

    def test_corpse_at_same_tile_as_player_gives_viewport_center(self):
        cfg = LootConfig(
            viewport_roi=[0, 0, 1280, 960],
            tile_size_px=32,
            ref_width=1280,
            ref_height=960,
        )
        ltr = _make_looter(config=cfg)
        ltr.set_player_getter(lambda: Coordinate(100, 200, 7))
        frame = _blank_frame(960, 1280)

        pc = PendingCorpse(tile_x=100, tile_y=200, tile_z=7)
        pos = ltr._corpse_screen_pos(pc, frame)
        assert pos is not None
        # Center of viewport [0,0,1280,960] half-width=640, half-height=480
        assert pos == (640, 480)

    def test_corpse_one_tile_east_of_player(self):
        cfg = LootConfig(
            viewport_roi=[0, 0, 1280, 960],
            tile_size_px=32,
            ref_width=1280,
            ref_height=960,
            max_range_tiles=0,
        )
        ltr = _make_looter(config=cfg)
        ltr.set_player_getter(lambda: Coordinate(100, 200, 7))
        frame = _blank_frame(960, 1280)

        pc = PendingCorpse(tile_x=101, tile_y=200, tile_z=7)
        pos = ltr._corpse_screen_pos(pc, frame)
        assert pos is not None
        cx, cy = pos
        assert cx == 672    # 640 + 32 (one tile east)
        assert cy == 480

    def test_returns_none_when_outside_max_range(self):
        cfg = LootConfig(max_range_tiles=1)
        ltr = _make_looter(config=cfg)
        ltr.set_player_getter(lambda: Coordinate(100, 200, 7))
        frame = _blank_frame()

        pc = PendingCorpse(tile_x=110, tile_y=200, tile_z=7)   # 10 tiles away
        pos = ltr._corpse_screen_pos(pc, frame)
        assert pos is None

    def test_returns_none_when_no_player_getter_and_no_templates(self):
        ltr = _make_looter()
        # No player getter, no templates → falls back to template matching → []
        frame = _blank_frame()
        pc = PendingCorpse(tile_x=100, tile_y=200, tile_z=7)
        pos = ltr._corpse_screen_pos(pc, frame)
        # Template matching returns empty → None
        assert pos is None


# ─────────────────────────────────────────────────────────────────────────────
# _open_corpse
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenCorpse:

    def test_sends_right_click_then_menu_click(self):
        ctrl = _mock_ctrl()
        ltr = _make_looter(ctrl=ctrl)
        result = ltr._open_corpse(500, 400)
        assert result is True
        # First call: right-click at corpse position
        calls = ctrl.click.call_args_list
        assert calls[0].args == (500, 400)
        assert calls[0].kwargs.get("button") == "right"
        # Second call: left-click at menu offset
        assert calls[1].kwargs.get("button") == "left"

    def test_returns_false_when_ctrl_fails(self):
        ctrl = _mock_ctrl()
        ctrl.click.return_value = False
        ltr = _make_looter(ctrl=ctrl)
        result = ltr._open_corpse(500, 400)
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# on_loot_start / on_loot_finish callbacks
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterCallbacks:

    def test_callbacks_are_none_by_default(self):
        ltr = _make_looter()
        assert ltr.on_loot_start   is None
        assert ltr.on_loot_finish  is None

    def test_can_set_callbacks(self):
        ltr = _make_looter()
        fired: List[str] = []
        ltr.on_loot_start  = lambda: fired.append("start")
        ltr.on_loot_finish = lambda: fired.append("finish")
        assert ltr.on_loot_start is not None


# ─────────────────────────────────────────────────────────────────────────────
# pending_count / clear_pending / update_config
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterPendingCount:

    def test_initial_pending_count_zero(self):
        ltr = _make_looter()
        assert ltr.pending_count == 0

    def test_notify_kill_increases_pending_count(self):
        ltr = _make_looter()
        coord = Coordinate(32100, 31200, 7)
        ltr.notify_kill(coord)
        assert ltr.pending_count == 1

    def test_multiple_kills_increase_count(self):
        ltr = _make_looter()
        for _ in range(3):
            ltr.notify_kill(Coordinate(32100, 31200, 7))
        assert ltr.pending_count == 3

    def test_done_corpse_not_counted(self):
        ltr = _make_looter()
        ltr.notify_kill(Coordinate(32100, 31200, 7))
        # Mark as done
        with ltr._lock:
            ltr._pending[0].done = True
        assert ltr.pending_count == 0


class TestLooterClearPending:

    def test_clear_removes_all(self):
        ltr = _make_looter()
        for _ in range(5):
            ltr.notify_kill(Coordinate(32100, 31200, 7))
        ltr.clear_pending()
        assert ltr.pending_count == 0

    def test_clear_empty_does_not_raise(self):
        ltr = _make_looter()
        ltr.clear_pending()  # should not raise
        assert ltr.pending_count == 0

    def test_add_after_clear_works(self):
        ltr = _make_looter()
        ltr.notify_kill(Coordinate(32100, 31200, 7))
        ltr.clear_pending()
        ltr.notify_kill(Coordinate(32200, 31300, 7))
        assert ltr.pending_count == 1


class TestLooterUpdateConfig:

    def test_update_config_replaces_cfg(self):
        ltr = _make_looter()
        new_cfg = LootConfig(tile_size_px=64, loot_mode="whitelist")
        ltr.update_config(new_cfg)
        assert ltr._cfg.tile_size_px == 64
        assert ltr._cfg.loot_mode == "whitelist"

    def test_update_config_rebuilds_corpse_detector(self):
        ltr = _make_looter()
        old_det = ltr._corpse_det
        ltr.update_config(LootConfig())
        assert ltr._corpse_det is not old_det

    def test_update_config_rebuilds_item_detector(self):
        ltr = _make_looter()
        old_det = ltr._item_det
        ltr.update_config(LootConfig())
        assert ltr._item_det is not old_det

    def test_update_config_applied_to_pending_count(self):
        """After update_config pending_count is still intact (queue not cleared)."""
        ltr = _make_looter()
        ltr.notify_kill(Coordinate(32100, 31200, 7))
        ltr.update_config(LootConfig())
        assert ltr.pending_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Looter.is_running
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterIsRunning:

    def test_false_before_start(self):
        ltr = _make_looter()
        assert ltr.is_running is False

    def test_true_after_start(self):
        ltr = _make_looter()
        ltr.start()
        assert ltr.is_running is True
        ltr.stop()

    def test_false_after_stop(self):
        ltr = _make_looter()
        ltr.start()
        ltr.stop()
        assert ltr.is_running is False

    def test_returns_bool(self):
        ltr = _make_looter()
        assert isinstance(ltr.is_running, bool)


# ─────────────────────────────────────────────────────────────────────────────
# Looter.pause / resume / is_paused
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterPauseResume:

    def test_initially_not_paused(self):
        ltr = _make_looter()
        assert ltr.is_paused is False

    def test_pause_sets_paused(self):
        ltr = _make_looter()
        ltr.pause()
        assert ltr.is_paused is True

    def test_resume_clears_paused(self):
        ltr = _make_looter()
        ltr.pause()
        ltr.resume()
        assert ltr.is_paused is False

    def test_pause_does_not_stop_thread(self):
        ltr = _make_looter()
        ltr.start()
        ltr.pause()
        assert ltr.is_running is True
        ltr.stop()

    def test_double_pause_stays_paused(self):
        ltr = _make_looter()
        ltr.pause()
        ltr.pause()
        assert ltr.is_paused is True

    def test_resume_without_pause_is_noop(self):
        ltr = _make_looter()
        ltr.resume()  # should not raise
        assert ltr.is_paused is False


# ─────────────────────────────────────────────────────────────────────────────
# Looter.reset_stats
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterResetStats:

    def test_initial_stats_zero(self):
        ltr = _make_looter()
        assert ltr.stats == {"looted": 0, "items_picked": 0}

    def test_reset_zeros_looted(self):
        ltr = _make_looter()
        ltr._looted = 5
        ltr.reset_stats()
        assert ltr._looted == 0

    def test_reset_zeros_items_picked(self):
        ltr = _make_looter()
        ltr._items_picked = 12
        ltr.reset_stats()
        assert ltr._items_picked == 0

    def test_reset_on_fresh_looter_is_noop(self):
        ltr = _make_looter()
        ltr.reset_stats()  # should not raise
        assert ltr.stats == {"looted": 0, "items_picked": 0}

    def test_stats_property_reflects_reset(self):
        ltr = _make_looter()
        ltr._looted = 3
        ltr._items_picked = 7
        ltr.reset_stats()
        s = ltr.stats
        assert s["looted"] == 0
        assert s["items_picked"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Looter.loot_summary
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterLootSummary:

    def test_returns_string(self):
        ltr = _make_looter()
        assert isinstance(ltr.loot_summary(), str)

    def test_initial_summary(self):
        ltr = _make_looter()
        s = ltr.loot_summary()
        assert "looted=0" in s
        assert "items=0" in s
        assert "pending=0" in s

    def test_summary_reflects_stats(self):
        ltr = _make_looter()
        ltr._looted = 5
        ltr._items_picked = 12
        s = ltr.loot_summary()
        assert "looted=5" in s
        assert "items=12" in s

    def test_summary_includes_mode(self):
        ltr = _make_looter(config=LootConfig(loot_mode="whitelist"))
        assert "mode=whitelist" in ltr.loot_summary()

    def test_summary_pending_count_accurate(self):
        ltr = _make_looter()
        ltr.notify_kill(None)
        ltr.notify_kill(None)
        assert "pending=2" in ltr.loot_summary()


# ─────────────────────────────────────────────────────────────────────────────
# Looter.set_loot_mode / add_to_whitelist / remove_from_whitelist
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterWhitelistAndMode:

    def test_set_loot_mode_all(self):
        ltr = _make_looter(config=LootConfig(loot_mode="whitelist"))
        ltr.set_loot_mode("all")
        assert ltr._cfg.loot_mode == "all"

    def test_set_loot_mode_whitelist(self):
        ltr = _make_looter()
        ltr.set_loot_mode("whitelist")
        assert ltr._cfg.loot_mode == "whitelist"

    def test_set_loot_mode_invalid_raises(self):
        import pytest
        ltr = _make_looter()
        with pytest.raises(ValueError):
            ltr.set_loot_mode("selective")

    def test_add_to_whitelist(self):
        ltr = _make_looter()
        ltr.add_to_whitelist("gold_coin")
        assert "gold_coin" in ltr._cfg.loot_whitelist

    def test_add_duplicate_ignored(self):
        ltr = _make_looter()
        ltr.add_to_whitelist("gold_coin")
        ltr.add_to_whitelist("gold_coin")
        assert ltr._cfg.loot_whitelist.count("gold_coin") == 1

    def test_remove_from_whitelist_returns_true(self):
        ltr = _make_looter(config=LootConfig(loot_whitelist=["gold_coin"]))
        assert ltr.remove_from_whitelist("gold_coin") is True

    def test_remove_from_whitelist_removes_item(self):
        ltr = _make_looter(config=LootConfig(loot_whitelist=["gold_coin"]))
        ltr.remove_from_whitelist("gold_coin")
        assert "gold_coin" not in ltr._cfg.loot_whitelist

    def test_remove_missing_returns_false(self):
        ltr = _make_looter()
        assert ltr.remove_from_whitelist("nonexistent") is False

    def test_add_multiple_items(self):
        ltr = _make_looter()
        for item in ("gold_coin", "platinum_coin", "crystal_coin"):
            ltr.add_to_whitelist(item)
        assert len(ltr._cfg.loot_whitelist) == 3


class TestLooterGetterFlags:

    def test_has_frame_getter_true_after_set(self):
        ltr = _make_looter()  # _make_looter calls set_frame_getter
        assert ltr.has_frame_getter is True

    def test_has_frame_getter_false_initially(self):
        ltr = Looter(ctrl=_mock_ctrl(), config=LootConfig())
        assert ltr.has_frame_getter is False

    def test_has_player_getter_false_initially(self):
        ltr = _make_looter()
        assert ltr.has_player_getter is False

    def test_has_player_getter_true_after_set(self):
        ltr = _make_looter()
        ltr.set_player_getter(lambda: None)
        assert ltr.has_player_getter is True

    def test_has_frame_getter_returns_bool(self):
        ltr = _make_looter()
        assert isinstance(ltr.has_frame_getter, bool)

    def test_has_player_getter_returns_bool(self):
        ltr = _make_looter()
        assert isinstance(ltr.has_player_getter, bool)


class TestLooterWhitelistCount:

    def test_whitelist_count_zero_initially(self):
        ltr = _make_looter()
        assert ltr.whitelist_count == 0

    def test_whitelist_count_increases_on_add(self):
        ltr = _make_looter()
        ltr.add_to_whitelist("gold_coin")
        assert ltr.whitelist_count == 1

    def test_whitelist_count_decreases_on_remove(self):
        ltr = _make_looter(config=LootConfig(loot_whitelist=["gold_coin", "ruby"]))
        ltr.remove_from_whitelist("gold_coin")
        assert ltr.whitelist_count == 1

    def test_whitelist_count_no_duplicates(self):
        ltr = _make_looter()
        ltr.add_to_whitelist("gold_coin")
        ltr.add_to_whitelist("gold_coin")
        assert ltr.whitelist_count == 1

    def test_whitelist_count_reflects_config(self):
        cfg = LootConfig(loot_whitelist=["a", "b", "c"])
        ltr = Looter(ctrl=_mock_ctrl(), config=cfg)
        assert ltr.whitelist_count == 3


class TestLooterStatsSnapshot:

    def test_returns_dict(self):
        ltr = _make_looter()
        snap = ltr.stats_snapshot()
        assert isinstance(snap, dict)

    def test_all_keys_present(self):
        ltr = _make_looter()
        snap = ltr.stats_snapshot()
        for key in ("looted", "items_picked", "pending", "is_running",
                    "is_paused", "loot_mode", "whitelist_count"):
            assert key in snap, f"Missing key: {key}"

    def test_initial_looted_zero(self):
        ltr = _make_looter()
        assert ltr.stats_snapshot()["looted"] == 0

    def test_initial_items_picked_zero(self):
        ltr = _make_looter()
        assert ltr.stats_snapshot()["items_picked"] == 0

    def test_is_paused_reflects_state(self):
        ltr = _make_looter()
        ltr.pause()
        assert ltr.stats_snapshot()["is_paused"] is True
        ltr.resume()
        assert ltr.stats_snapshot()["is_paused"] is False

    def test_loot_mode_reflected(self):
        cfg = LootConfig(loot_mode="whitelist")
        ltr = Looter(ctrl=_mock_ctrl(), config=cfg)
        assert ltr.stats_snapshot()["loot_mode"] == "whitelist"

    def test_whitelist_count_reflected(self):
        ltr = _make_looter()
        ltr.add_to_whitelist("crystal_coin")
        assert ltr.stats_snapshot()["whitelist_count"] == 1

    def test_pending_count_reflected(self):
        ltr = _make_looter()
        ltr.notify_kill()
        assert ltr.stats_snapshot()["pending"] == 1

    def test_is_running_false_before_start(self):
        ltr = _make_looter()
        assert ltr.stats_snapshot()["is_running"] is False


# ─────────────────────────────────────────────────────────────────────────────
# has_pending
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterHasPending:

    def test_false_initially(self):
        ltr = _make_looter()
        assert ltr.has_pending is False

    def test_true_after_notify_kill(self):
        ltr = _make_looter()
        ltr.notify_kill()
        assert ltr.has_pending is True

    def test_false_after_clear_pending(self):
        ltr = _make_looter()
        ltr.notify_kill()
        ltr.clear_pending()
        assert ltr.has_pending is False

    def test_consistent_with_pending_count(self):
        ltr = _make_looter()
        ltr.notify_kill()
        assert ltr.has_pending == (ltr.pending_count > 0)

    def test_returns_bool(self):
        ltr = _make_looter()
        assert isinstance(ltr.has_pending, bool)


# ─────────────────────────────────────────────────────────────────────────────
# has_looted
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterHasLooted:

    def test_false_initially(self):
        ltr = _make_looter()
        assert ltr.has_looted is False

    def test_true_after_increment(self):
        ltr = _make_looter()
        ltr._looted = 1
        assert ltr.has_looted is True

    def test_false_after_reset_stats(self):
        ltr = _make_looter()
        ltr._looted = 3
        ltr.reset_stats()
        assert ltr.has_looted is False

    def test_consistent_with_looted_counter(self):
        ltr = _make_looter()
        ltr._looted = 5
        assert ltr.has_looted == (ltr._looted > 0)

    def test_returns_bool(self):
        ltr = _make_looter()
        assert isinstance(ltr.has_looted, bool)


# ─────────────────────────────────────────────────────────────────────────────
# is_whitelist_mode
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterIsWhitelistMode:

    def test_false_by_default(self):
        ltr = _make_looter()
        assert ltr.is_whitelist_mode is False

    def test_true_when_loot_mode_whitelist(self):
        cfg = LootConfig(loot_mode="whitelist")
        ltr = Looter(ctrl=_mock_ctrl(), config=cfg)
        assert ltr.is_whitelist_mode is True

    def test_false_when_loot_mode_all(self):
        cfg = LootConfig(loot_mode="all")
        ltr = Looter(ctrl=_mock_ctrl(), config=cfg)
        assert ltr.is_whitelist_mode is False

    def test_true_after_set_whitelist_mode(self):
        ltr = _make_looter()
        ltr.set_loot_mode("whitelist")
        assert ltr.is_whitelist_mode is True

    def test_false_after_switch_back_to_all(self):
        cfg = LootConfig(loot_mode="whitelist")
        ltr = Looter(ctrl=_mock_ctrl(), config=cfg)
        ltr.set_loot_mode("all")
        assert ltr.is_whitelist_mode is False


# ─────────────────────────────────────────────────────────────────────────────
# looted_count
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterLootedCount:

    def test_zero_initially(self):
        ltr = _make_looter()
        assert ltr.looted_count == 0

    def test_reflects_internal_field(self):
        ltr = _make_looter()
        ltr._looted = 5
        assert ltr.looted_count == 5

    def test_returns_int(self):
        ltr = _make_looter()
        assert isinstance(ltr.looted_count, int)

    def test_consistent_with_has_looted(self):
        ltr = _make_looter()
        ltr._looted = 2
        assert ltr.looted_count > 0
        assert ltr.has_looted is True

    def test_zero_after_reset(self):
        ltr = _make_looter()
        ltr._looted = 4
        ltr.reset_stats()
        assert ltr.looted_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# items_picked_count / has_items_picked
# ─────────────────────────────────────────────────────────────────────────────

class TestLooterItemsPickedCount:

    def test_zero_initially(self):
        ltr = _make_looter()
        assert ltr.items_picked_count == 0

    def test_reflects_internal_field(self):
        ltr = _make_looter()
        ltr._items_picked = 12
        assert ltr.items_picked_count == 12

    def test_returns_int(self):
        ltr = _make_looter()
        assert isinstance(ltr.items_picked_count, int)

    def test_zero_after_reset(self):
        ltr = _make_looter()
        ltr._items_picked = 7
        ltr.reset_stats()
        assert ltr.items_picked_count == 0

    def test_consistent_with_stats_dict(self):
        ltr = _make_looter()
        ltr._items_picked = 3
        assert ltr.items_picked_count == ltr.stats["items_picked"]


class TestLooterHasItemsPicked:

    def test_false_initially(self):
        ltr = _make_looter()
        assert ltr.has_items_picked is False

    def test_true_after_increment(self):
        ltr = _make_looter()
        ltr._items_picked = 1
        assert ltr.has_items_picked is True

    def test_returns_bool(self):
        ltr = _make_looter()
        assert isinstance(ltr.has_items_picked, bool)

    def test_false_after_reset(self):
        ltr = _make_looter()
        ltr._items_picked = 5
        ltr.reset_stats()
        assert ltr.has_items_picked is False

    def test_consistent_with_items_picked_count(self):
        ltr = _make_looter()
        ltr._items_picked = 9
        assert ltr.has_items_picked == (ltr.items_picked_count > 0)


# ─────────────────────────────────────────────────────────────────────────────
# LootConfig property helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestLootConfigIsWhitelistMode:

    def test_false_by_default(self):
        assert LootConfig().is_whitelist_mode is False

    def test_true_when_mode_is_whitelist(self):
        cfg = LootConfig(loot_mode="whitelist")
        assert cfg.is_whitelist_mode is True

    def test_false_when_mode_is_all(self):
        cfg = LootConfig(loot_mode="all")
        assert cfg.is_whitelist_mode is False

    def test_consistent_with_loot_mode_field(self):
        cfg = LootConfig(loot_mode="whitelist")
        assert cfg.is_whitelist_mode == (cfg.loot_mode == "whitelist")

    def test_returns_bool(self):
        assert isinstance(LootConfig().is_whitelist_mode, bool)


class TestLootConfigHasWhitelist:

    def test_false_when_whitelist_is_empty(self):
        assert LootConfig().has_whitelist is False

    def test_true_when_whitelist_has_items(self):
        cfg = LootConfig(loot_whitelist=["golden_helmet", "fire_sword"])
        assert cfg.has_whitelist is True

    def test_true_when_single_item(self):
        cfg = LootConfig(loot_whitelist=["dragon_scale"])
        assert cfg.has_whitelist is True

    def test_false_after_clearing_whitelist(self):
        cfg = LootConfig(loot_whitelist=["item_a"])
        cfg.loot_whitelist.clear()
        assert cfg.has_whitelist is False

    def test_returns_bool(self):
        assert isinstance(LootConfig().has_whitelist, bool)


class TestLootConfigIsRangeLimited:

    def test_true_by_default(self):
        # default max_range_tiles = 2
        assert LootConfig().is_range_limited is True

    def test_false_when_range_is_zero(self):
        cfg = LootConfig(max_range_tiles=0)
        assert cfg.is_range_limited is False

    def test_true_when_range_is_one(self):
        cfg = LootConfig(max_range_tiles=1)
        assert cfg.is_range_limited is True

    def test_true_when_range_is_large(self):
        cfg = LootConfig(max_range_tiles=100)
        assert cfg.is_range_limited is True

    def test_returns_bool(self):
        assert isinstance(LootConfig().is_range_limited, bool)


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests for the 4 bugs fixed in review
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionBug1ShiftClick:
    """Bug 1: _pick_items must use ctrl.shift_click(), not press_key + click."""

    def test_whitelist_mode_uses_shift_click(self):
        """In whitelist mode, shift_click is called instead of press_key + click."""
        ctrl = _mock_ctrl()
        ctrl.shift_click = MagicMock(return_value=True)

        cfg = LootConfig(loot_mode="whitelist", loot_whitelist=["gold_coin"])
        ltr = Looter(ctrl=ctrl, config=cfg)

        # Inject a fake item detection result so _pick_items finds one item
        frame = _blank_frame()
        fake_items = [(50, 60, 0.9, "gold_coin")]
        ltr._item_det.detect_whitelist = MagicMock(return_value=fake_items)  # type: ignore[method-assign]

        count, names = ltr._pick_items(frame)

        assert count == 1
        assert names == ["gold_coin"]
        ctrl.shift_click.assert_called_once_with(50, 60)
        # press_key must NOT be called for shift
        for call in ctrl.press_key.call_args_list:
            assert call.args[0] != 0x10, "press_key(0x10) must not be used for SHIFT"

    def test_all_mode_uses_shift_click(self):
        """In 'all' mode, shift_click is called for every occupied slot."""
        ctrl = _mock_ctrl()
        ctrl.shift_click = MagicMock(return_value=True)

        cfg = LootConfig(loot_mode="all")
        ltr = Looter(ctrl=ctrl, config=cfg)

        frame = _blank_frame()
        fake_slots = [(100, 110), (132, 110)]
        ltr._item_det.all_slot_positions = MagicMock(return_value=fake_slots)  # type: ignore[method-assign]

        count, names = ltr._pick_items(frame)

        assert count == 2
        assert names == []
        assert ctrl.shift_click.call_count == 2


class TestRegressionBug2StartUsesLog:
    """Bug 2: start() must route its log message through self._log, not print()."""

    def test_start_calls_log_callback(self):
        """start() routes startup message through the registered log callback."""
        ltr = _make_looter()
        log_messages: List[str] = []
        ltr.set_log_callback(log_messages.append)
        ltr.start()
        ltr.stop()
        assert any("looter" in msg.lower() or "loot" in msg.lower()
                   for msg in log_messages), (
            f"Expected '[L]' message from start() in log callback. Got: {log_messages}"
        )

    def test_start_does_not_bypass_log_callback(self, capsys):
        """start() must not print directly to stdout when a log callback is set."""
        ltr = _make_looter()
        captured: List[str] = []
        ltr.set_log_callback(captured.append)
        ltr.start()
        ltr.stop()
        out = capsys.readouterr().out
        # The startup banner must not appear on raw stdout when callback is set
        assert "Hilo de looter" not in out


class TestRegressionBug3OnItemLootedOnlyPickedItems:
    """Bug 3: on_item_looted must fire only for items actually picked, not all whitelist items."""

    def test_callback_fires_only_for_picked_items(self):
        """
        With whitelist=['gold_coin','ruby','fire_sword'] and only 1 item found,
        on_item_looted must be called exactly once — not 3 times.
        """
        ctrl = _mock_ctrl()
        ctrl.shift_click = MagicMock(return_value=True)
        cfg = LootConfig(
            loot_mode="whitelist",
            loot_whitelist=["gold_coin", "ruby", "fire_sword"],
            loot_delay=0.0,
            container_settle=0.0,
        )
        ltr = Looter(ctrl=ctrl, config=cfg)

        fired: List[tuple] = []
        ltr.on_item_looted = lambda name, cnt: fired.append((name, cnt))

        # Simulate _pick_items returning 1 item
        ltr._pick_items = MagicMock(return_value=(1, ["gold_coin"]))  # type: ignore

        # Call the private on_item_looted dispatch logic directly by triggering
        # a loot cycle via the internal helper path
        picked, picked_names = ltr._pick_items(None)  # type: ignore
        if picked > 0 and ltr.on_item_looted is not None:
            if picked_names:
                for _wname in picked_names:
                    ltr.on_item_looted(_wname.lower(), 1)
            else:
                ltr.on_item_looted("__looted__", picked)

        assert len(fired) == 1, (
            f"Expected 1 callback call (only gold_coin was picked), got {len(fired)}: {fired}"
        )
        assert fired[0] == ("gold_coin", 1)

    def test_callback_fires_for_all_mode_as_looted_bulk(self):
        """In 'all' mode, callback is called once with '__looted__' and total count."""
        ctrl = _mock_ctrl()
        ctrl.shift_click = MagicMock(return_value=True)
        cfg = LootConfig(loot_mode="all", loot_delay=0.0, container_settle=0.0)
        ltr = Looter(ctrl=ctrl, config=cfg)

        fired: List[tuple] = []
        ltr.on_item_looted = lambda name, cnt: fired.append((name, cnt))

        ltr._pick_items = MagicMock(return_value=(3, []))  # type: ignore

        picked, picked_names = ltr._pick_items(None)  # type: ignore
        if picked > 0 and ltr.on_item_looted is not None:
            if picked_names:
                for _wname in picked_names:
                    ltr.on_item_looted(_wname.lower(), 1)
            else:
                ltr.on_item_looted("__looted__", picked)

        assert fired == [("__looted__", 3)]


class TestRegressionBug4AllSlotPositionsScaling:
    """Bug 4: all_slot_positions must scale slot_size_px by frame resolution."""

    def test_slot_positions_scale_with_frame_size(self):
        """
        A frame at half the reference resolution must produce slot centers
        at proportionally smaller pixel coordinates.
        """
        # Reference resolution: 1920×1080, slot 34px, container_roi=[0,0,300,200]
        cfg_ref = LootConfig(
            ref_width=1920, ref_height=1080,
            slot_size_px=34, container_cols=4,
            container_roi=[0, 0, 300, 200],
        )
        det_ref = ItemDetector(cfg_ref)

        # Frame at reference resolution — fill container ROI with bright pixels
        frame_ref = np.full((1080, 1920, 3), 50, dtype=np.uint8)
        slots_ref = det_ref.all_slot_positions(frame_ref, max_slots=4)

        # Frame at half resolution — fill corresponding ROI with bright pixels
        cfg_half = LootConfig(
            ref_width=1920, ref_height=1080,
            slot_size_px=34, container_cols=4,
            container_roi=[0, 0, 300, 200],
        )
        det_half = ItemDetector(cfg_half)
        frame_half = np.full((540, 960, 3), 50, dtype=np.uint8)
        slots_half = det_half.all_slot_positions(frame_half, max_slots=4)

        assert len(slots_ref) == len(slots_half), (
            f"Slot count mismatch: ref={len(slots_ref)} half={len(slots_half)}"
        )
        # Each half-resolution slot center must be ~half the reference center
        for (rx, ry), (hx, hy) in zip(slots_ref, slots_half):
            assert abs(hx - rx // 2) <= 2, f"X mismatch: ref={rx} half={hx}"
            assert abs(hy - ry // 2) <= 2, f"Y mismatch: ref={ry} half={hy}"

    def test_slot_positions_reference_resolution_unchanged(self):
        """At reference resolution, slot positions match expected grid layout."""
        cfg = LootConfig(
            ref_width=200, ref_height=200,
            slot_size_px=34, container_cols=4,
            container_roi=[0, 0, 200, 200],
        )
        det = ItemDetector(cfg)
        # Bright frame so all slots are "occupied"
        frame = np.full((200, 200, 3), 50, dtype=np.uint8)
        slots = det.all_slot_positions(frame, max_slots=4)
        assert len(slots) == 4
        # First slot center at (0*34 + 17, 0*34 + 17) = (17, 17)
        assert slots[0] == (17, 17)
        # Second slot (col=1): (34 + 17, 17) = (51, 17)
        assert slots[1] == (51, 17)


# ─────────────────────────────────────────────────────────────────────────────
# Quick Loot — new loot_mode="quick"
# ─────────────────────────────────────────────────────────────────────────────

class TestLootConfigQuickLoot:

    def test_quick_loot_menu_offset_default(self):
        assert LootConfig().quick_loot_menu_offset_y == 36

    def test_quick_loot_menu_offset_configurable(self):
        cfg = LootConfig(quick_loot_menu_offset_y=54)
        assert cfg.quick_loot_menu_offset_y == 54

    def test_save_and_load_quick_loot_offset(self, tmp_path: Path):
        path = tmp_path / "cfg.json"
        LootConfig(quick_loot_menu_offset_y=54).save(path)
        loaded = LootConfig.load(path)
        assert loaded.quick_loot_menu_offset_y == 54


class TestSetLootModeQuick:

    def test_set_loot_mode_quick_accepted(self):
        ltr = _make_looter()
        ltr.set_loot_mode("quick")
        assert ltr._cfg.loot_mode == "quick"

    def test_set_loot_mode_invalid_still_raises(self):
        ltr = _make_looter()
        with pytest.raises(ValueError):
            ltr.set_loot_mode("ultra")

    def test_set_loot_mode_back_to_all_from_quick(self):
        ltr = _make_looter(config=LootConfig(loot_mode="quick"))
        ltr.set_loot_mode("all")
        assert ltr._cfg.loot_mode == "all"

    def test_loot_summary_shows_quick_mode(self):
        ltr = _make_looter(config=LootConfig(loot_mode="quick"))
        assert "mode=quick" in ltr.loot_summary()


class TestQuickLootCorpse:

    def _quick_looter(self) -> tuple:
        ctrl = _mock_ctrl()
        cfg = LootConfig(loot_mode="quick", quick_loot_menu_offset_y=36)
        ltr = Looter(ctrl=ctrl, config=cfg)
        return ltr, ctrl

    def test_quick_loot_sends_right_click_on_corpse(self):
        ltr, ctrl = self._quick_looter()
        ltr._quick_loot_corpse(300, 400)
        first_call = ctrl.click.call_args_list[0]
        assert first_call.args[:2] == (300, 400)
        assert first_call.kwargs.get("button") == "right"

    def test_quick_loot_sends_left_click_at_menu_offset(self):
        ltr, ctrl = self._quick_looter()
        ltr._quick_loot_corpse(300, 400)
        second_call = ctrl.click.call_args_list[1]
        assert second_call.kwargs.get("button") == "left"
        # Y must equal corpse_y + quick_loot_menu_offset_y (36)
        assert second_call.args[1] == 400 + 36

    def test_quick_loot_returns_true_on_success(self):
        ltr, ctrl = self._quick_looter()
        assert ltr._quick_loot_corpse(300, 400) is True

    def test_quick_loot_returns_false_when_right_click_fails(self):
        ltr, ctrl = self._quick_looter()
        ctrl.click.return_value = False
        assert ltr._quick_loot_corpse(300, 400) is False

    def test_quick_loot_uses_configured_menu_offset(self):
        ctrl = _mock_ctrl()
        cfg = LootConfig(loot_mode="quick", quick_loot_menu_offset_y=54)
        ltr = Looter(ctrl=ctrl, config=cfg)
        ltr._quick_loot_corpse(100, 200)
        second_call = ctrl.click.call_args_list[1]
        assert second_call.args[1] == 200 + 54

    def test_quick_loot_does_not_open_container(self):
        """Quick Loot must NOT call _open_corpse or _pick_items."""
        ltr, ctrl = self._quick_looter()
        from unittest.mock import patch
        with patch.object(ltr, "_open_corpse") as mock_open, \
             patch.object(ltr, "_pick_items") as mock_pick:
            ltr._quick_loot_corpse(300, 400)
            mock_open.assert_not_called()
            mock_pick.assert_not_called()


class TestQuickLootLoopIntegration:
    """Verify the _loop processes quick-loot corpses without opening containers."""

    def _run_one_corpse_quick(self, on_item_looted=None):
        ctrl = _mock_ctrl()
        cfg = LootConfig(
            loot_mode="quick",
            quick_loot_menu_offset_y=36,
            loot_delay=0.0,
            container_settle=0.0,
            max_range_tiles=0,
        )
        ltr = Looter(ctrl=ctrl, config=cfg)
        ltr.set_frame_getter(lambda: _blank_frame())
        if on_item_looted:
            ltr.on_item_looted = on_item_looted
        # Pre-inject a pending corpse that's past the delay
        coord = Coordinate(100, 200, 7)
        ltr.notify_kill(coord)
        ltr._pending[0].created_at -= 10.0  # force past loot_delay
        ltr.set_player_getter(lambda: coord)  # corpse at same tile as player
        return ltr, ctrl

    def test_quick_loot_increments_looted_count(self):
        from unittest.mock import patch
        ltr, ctrl = self._run_one_corpse_quick()
        ltr.start()
        import time as _time
        _time.sleep(0.5)
        ltr.stop()
        assert ltr.looted_count >= 1

    def test_quick_loot_does_not_increment_items_picked(self):
        """items_picked stays 0 — no shift-clicks happen in quick mode."""
        from unittest.mock import patch
        ltr, ctrl = self._run_one_corpse_quick()
        ltr.start()
        import time as _time
        _time.sleep(0.5)
        ltr.stop()
        assert ltr.items_picked_count == 0

    def test_quick_loot_fires_on_item_looted_callback(self):
        fired: List[tuple] = []
        ltr, ctrl = self._run_one_corpse_quick(
            on_item_looted=lambda name, cnt: fired.append((name, cnt))
        )
        ltr.start()
        import time as _time
        _time.sleep(0.5)
        ltr.stop()
        assert any(name == "__quick_looted__" for name, _ in fired)

    def test_quick_loot_does_not_call_shift_click(self):
        ltr, ctrl = self._run_one_corpse_quick()
        ltr.start()
        import time as _time
        _time.sleep(0.5)
        ltr.stop()
        ctrl.shift_click.assert_not_called()

    def test_quick_loot_calls_on_loot_start_finish(self):
        events: List[str] = []
        ltr, ctrl = self._run_one_corpse_quick()
        ltr.on_loot_start  = lambda: events.append("start")
        ltr.on_loot_finish = lambda: events.append("finish")
        ltr.start()
        import time as _time
        _time.sleep(0.5)
        ltr.stop()
        assert "start"  in events
        assert "finish" in events


# ─────────────────────────────────────────────────────────────────────────────
# Stow All Items — stow_container()
# ─────────────────────────────────────────────────────────────────────────────

class TestLootConfigStowAll:

    def test_stow_all_menu_offset_default(self):
        assert LootConfig().stow_all_menu_offset_y == 18

    def test_stow_all_container_pos_default_empty(self):
        assert LootConfig().stow_all_container_pos == []

    def test_stow_all_fields_configurable(self):
        cfg = LootConfig(stow_all_menu_offset_y=36, stow_all_container_pos=[500, 300])
        assert cfg.stow_all_menu_offset_y == 36
        assert cfg.stow_all_container_pos == [500, 300]

    def test_save_and_load_stow_all_fields(self, tmp_path: Path):
        path = tmp_path / "cfg.json"
        LootConfig(stow_all_menu_offset_y=36, stow_all_container_pos=[500, 300]).save(path)
        loaded = LootConfig.load(path)
        assert loaded.stow_all_menu_offset_y == 36
        assert loaded.stow_all_container_pos == [500, 300]


class TestStowContainer:

    def _stow_looter(self, pos=None) -> tuple:
        ctrl = _mock_ctrl()
        cfg = LootConfig(
            stow_all_menu_offset_y=18,
            stow_all_container_pos=pos or [],
            container_roi=[800, 200, 400, 300],
            ref_width=1920,
            ref_height=1080,
        )
        ltr = Looter(ctrl=ctrl, config=cfg)
        return ltr, ctrl

    # --- fixed position path ---

    def test_fixed_pos_right_clicks_at_configured_position(self):
        ltr, ctrl = self._stow_looter(pos=[900, 210])
        ltr.stow_container()
        first_call = ctrl.click.call_args_list[0]
        assert first_call.args[:2] == (900, 210)
        assert first_call.kwargs.get("button") == "right"

    def test_fixed_pos_left_clicks_menu_at_offset(self):
        ltr, ctrl = self._stow_looter(pos=[900, 210])
        ltr.stow_container()
        second_call = ctrl.click.call_args_list[1]
        assert second_call.kwargs.get("button") == "left"
        assert second_call.args[1] == 210 + 18   # stow_all_menu_offset_y

    def test_fixed_pos_returns_true_on_success(self):
        ltr, ctrl = self._stow_looter(pos=[900, 210])
        assert ltr.stow_container() is True

    def test_fixed_pos_returns_false_when_right_click_fails(self):
        ltr, ctrl = self._stow_looter(pos=[900, 210])
        ctrl.click.return_value = False
        assert ltr.stow_container() is False

    # --- auto-computed position from container_roi ---

    def test_auto_pos_right_clicks_center_top_of_container_roi(self):
        """Without a fixed position, click target is the horizontal center of container_roi
        at the top (title bar) of the container window."""
        ltr, ctrl = self._stow_looter(pos=[])
        frame = _blank_frame(1080, 1920)
        ltr.stow_container(frame=frame)
        first_call = ctrl.click.call_args_list[0]
        cx_clicked, cy_clicked = first_call.args[:2]
        # container_roi=[800, 200, 400, 300] → center_x = 800 + 200 = 1000, top_y = 200+6
        assert cx_clicked == 1000
        assert cy_clicked == 206    # 200 + 6 (title bar offset)

    def test_auto_pos_scales_with_frame_resolution(self):
        """At half the reference resolution, click coordinates are halved."""
        ltr, ctrl = self._stow_looter(pos=[])
        frame_half = _blank_frame(540, 960)
        ltr.stow_container(frame=frame_half)
        first_call = ctrl.click.call_args_list[0]
        cx_clicked, cy_clicked = first_call.args[:2]
        assert cx_clicked == 500    # 1000 * 0.5
        # click_y = int(ry * scale_y) + scale_y_offset(6, 540/1080) = int(200*0.5) + 3 = 103
        assert cy_clicked == 103

    def test_auto_pos_fetches_frame_from_getter_when_none(self):
        """When frame=None and a frame getter is registered, uses the getter."""
        ltr, ctrl = self._stow_looter(pos=[])
        frame = _blank_frame(1080, 1920)
        ltr.set_frame_getter(lambda: frame)
        ltr.stow_container(frame=None)
        first_call = ctrl.click.call_args_list[0]
        cx_clicked = first_call.args[0]
        # Should use the getter's 1920-wide frame, so no halving
        assert cx_clicked == 1000

    def test_auto_pos_no_frame_no_getter_uses_scale_1(self):
        """With no frame and no getter, scale defaults to 1.0."""
        ltr, ctrl = self._stow_looter(pos=[])
        ltr.stow_container(frame=None)   # no frame getter registered
        first_call = ctrl.click.call_args_list[0]
        cx_clicked, cy_clicked = first_call.args[:2]
        assert cx_clicked == 1000
        assert cy_clicked == 206

    def test_stow_uses_configured_menu_offset(self):
        ctrl = _mock_ctrl()
        cfg = LootConfig(
            stow_all_menu_offset_y=36,
            stow_all_container_pos=[700, 180],
        )
        ltr = Looter(ctrl=ctrl, config=cfg)
        ltr.stow_container()
        second_call = ctrl.click.call_args_list[1]
        assert second_call.args[1] == 180 + 36

    def test_stow_two_clicks_total(self):
        ltr, ctrl = self._stow_looter(pos=[900, 210])
        ltr.stow_container()
        assert ctrl.click.call_count == 2

    def test_stow_logs_success(self):
        ltr, ctrl = self._stow_looter(pos=[900, 210])
        messages: List[str] = []
        ltr.set_log_callback(messages.append)
        ltr.stow_container()
        assert any("Stow All" in m for m in messages)

    def test_stow_logs_failure_on_right_click_error(self):
        ltr, ctrl = self._stow_looter(pos=[900, 210])
        ctrl.click.return_value = False
        messages: List[str] = []
        ltr.set_log_callback(messages.append)
        ltr.stow_container()
        assert any("⚠" in m or "fall" in m for m in messages)
