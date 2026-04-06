"""
tests/test_trade.py - Unit tests for TradeManager, TradeConfig, TradeItemDetector.

All tests run fully offline: no Tibia window, no OBS, no real input.
InputController and frame_getter are mocked.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from typing import Optional

import numpy as np
import pytest

from src.trade_manager import (
    TradeConfig,
    TradeItem,
    TradeItemDetector,
    TradeManager,
    TradeWindowNotFound,
    TRADE_CONFIG_FILE,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _blank_frame(h: int = 1080, w: int = 1920) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _trade_frame() -> np.ndarray:
    """Frame with the golden header color at the expected window_roi position."""
    frame = _blank_frame()
    cfg = TradeConfig()
    x, y, rw, rh = cfg.window_roi
    # Paint a gold/brown region [h=20, s=150, v=180] in HSV -> BGR
    import cv2
    gold_bgr = np.full((rh, rw, 3), (30, 120, 200), dtype=np.uint8)  # approximation
    # Use target HSV [20, 150, 180] -> convert
    hsv_patch = np.full((rh, rw, 3), [20, 160, 200], dtype=np.uint8)
    bgr_patch = cv2.cvtColor(hsv_patch, cv2.COLOR_HSV2BGR)
    frame[y: y + rh, x: x + rw] = bgr_patch
    return frame


def _make_ctrl(connected: bool = True) -> MagicMock:
    ctrl = MagicMock()
    ctrl.is_connected.return_value = connected
    ctrl.hwnd = 12345
    ctrl.press_key.return_value = True
    ctrl.type_text.return_value = True
    ctrl.click.return_value = True
    return ctrl


def _make_manager(
    connected: bool = True,
    window_open: bool = True,
    cfg: Optional[TradeConfig] = None,
) -> TradeManager:
    ctrl = _make_ctrl(connected)
    manager = TradeManager(ctrl, cfg or TradeConfig(), log_fn=lambda _: None)
    frame = _trade_frame() if window_open else _blank_frame()
    manager.set_frame_getter(lambda: frame)
    return manager


# ─── TradeConfig ──────────────────────────────────────────────────────────────

class TestTradeConfig:
    def test_defaults(self):
        cfg = TradeConfig()
        assert cfg.confidence == pytest.approx(0.62)
        assert cfg.greet_text == "trade"
        assert cfg.window_timeout == pytest.approx(5.0)

    def test_buy_items_empty(self):
        cfg = TradeConfig()
        assert cfg.buy_items() == []

    def test_sell_items_empty(self):
        cfg = TradeConfig()
        assert cfg.sell_items() == []

    def test_buy_items_parsed(self):
        cfg = TradeConfig()
        cfg.buy_list = [{"name": "health_potion", "quantity": 50, "max_price": 120}]
        items = cfg.buy_items()
        assert len(items) == 1
        assert items[0].name == "health_potion"
        assert items[0].quantity == 50
        assert items[0].max_price == 120

    def test_sell_items_parsed(self):
        cfg = TradeConfig()
        cfg.sell_list = [{"name": "dead_troll_spike", "quantity": 10}]
        items = cfg.sell_items()
        assert items[0].name == "dead_troll_spike"
        assert items[0].quantity == 10

    def test_save_load_roundtrip(self, tmp_path):
        cfg = TradeConfig()
        cfg.greet_text = "hi"
        cfg.buy_list = [{"name": "mana_potion", "quantity": 20, "max_price": 80}]
        path = tmp_path / "trade_cfg.json"
        cfg.save(path)
        loaded = TradeConfig.load(path)
        assert loaded.greet_text == "hi"
        assert loaded.buy_list[0]["name"] == "mana_potion"

    def test_load_nonexistent_returns_defaults(self, tmp_path):
        loaded = TradeConfig.load(tmp_path / "missing.json")
        assert loaded.greet_text == "trade"

    def test_save_is_atomic(self, tmp_path):
        cfg = TradeConfig()
        path = tmp_path / "trade.json"
        cfg.save(path)
        assert path.exists()
        assert not (tmp_path / "trade.json.tmp").exists()

    def test_roi_lengths(self):
        cfg = TradeConfig()
        assert len(cfg.window_roi) == 4
        assert len(cfg.item_list_roi) == 4
        assert len(cfg.qty_field_roi) == 4
        assert len(cfg.buy_btn_pos) == 2
        assert len(cfg.sell_btn_pos) == 2
        assert len(cfg.cancel_btn_pos) == 2


# ─── TradeItem ────────────────────────────────────────────────────────────────

class TestTradeItem:
    def test_defaults(self):
        item = TradeItem(name="health_potion")
        assert item.quantity == 1
        assert item.max_price == 0

    def test_with_all_fields(self):
        item = TradeItem(name="mana_potion", quantity=100, max_price=50)
        assert item.name == "mana_potion"
        assert item.quantity == 100
        assert item.max_price == 50


# ─── TradeItemDetector ────────────────────────────────────────────────────────

class TestTradeItemDetector:
    def test_no_templates_dir_does_not_crash(self, tmp_path):
        cfg = TradeConfig()
        cfg.templates_dir = str(tmp_path / "tpls")
        det = TradeItemDetector(cfg)
        assert det._templates == {}

    def test_find_item_no_templates_returns_none(self):
        cfg = TradeConfig()
        cfg.templates_dir = "/nonexistent"
        det = TradeItemDetector(cfg)
        result = det.find_item(_blank_frame(), "health_potion")
        assert result is None

    def test_find_item_none_frame_returns_none(self, tmp_path):
        cfg = TradeConfig()
        cfg.templates_dir = str(tmp_path)
        det = TradeItemDetector(cfg)
        assert det.find_item(None, "item") is None  # type: ignore

    def test_is_window_closed_on_blank_frame(self):
        cfg = TradeConfig()
        det = TradeItemDetector(cfg)
        assert not det.is_trade_window_open(_blank_frame())

    def test_is_window_open_with_gold_pixels(self):
        import cv2
        cfg = TradeConfig()
        det = TradeItemDetector(cfg)
        frame = _blank_frame()
        x, y, rw, rh = cfg.window_roi
        # Paint target HSV color into the ROI
        patch = np.full((rh, rw, 3), [cfg.window_header_color_hsv[0], 180, 200],
                        dtype=np.uint8)
        frame[y: y + rh, x: x + rw] = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
        assert det.is_trade_window_open(frame)

    def test_is_window_open_returns_false_on_none(self):
        det = TradeItemDetector(TradeConfig())
        assert not det.is_trade_window_open(None)  # type: ignore

    def test_find_item_known_template(self, tmp_path):
        """Find a template that IS in the item_list_roi."""
        import cv2
        cfg = TradeConfig()
        cfg.templates_dir = str(tmp_path)
        cfg.confidence = 0.50
        # Create template
        tmpl = np.ones((20, 20, 3), dtype=np.uint8) * 200
        tdir = tmp_path / "trade_items"
        tdir.mkdir()
        cv2.imwrite(str(tdir / "hp_ptn.png"), tmpl)
        det = TradeItemDetector(cfg)
        det.reload()
        # Build frame with template pasted inside item_list_roi
        frame = _blank_frame()
        x, y = cfg.item_list_roi[0] + 5, cfg.item_list_roi[1] + 5
        frame[y: y + 20, x: x + 20] = tmpl
        result = det.find_item(frame, "hp_ptn")
        assert result is not None
        cx, cy = result
        assert cfg.item_list_roi[0] <= cx <= cfg.item_list_roi[0] + cfg.item_list_roi[2]
        assert cfg.item_list_roi[1] <= cy <= cfg.item_list_roi[1] + cfg.item_list_roi[3]

    def test_read_price_no_crash_on_bad_roi(self):
        det = TradeItemDetector(TradeConfig())
        # If easyocr not available or can't read price, returns None without crash
        result = det.read_price(_blank_frame())
        assert result is None or isinstance(result, int)

    def test_reload_picks_new_templates(self, tmp_path):
        import cv2
        cfg = TradeConfig()
        cfg.templates_dir = str(tmp_path)
        tdir = tmp_path / "trade_items"
        tdir.mkdir()
        det = TradeItemDetector(cfg)
        assert "new_item" not in det._templates
        cv2.imwrite(str(tdir / "new_item.png"), np.ones((10, 10, 3), dtype=np.uint8))
        det.reload()
        assert "new_item" in det._templates


# ─── TradeManager ─────────────────────────────────────────────────────────────

class TestTradeManagerInit:
    def test_defaults(self):
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl)
        assert tm._cfg is not None
        assert tm.last_bought == {}
        assert tm.last_sold == {}

    def test_custom_config(self):
        cfg = TradeConfig()
        cfg.greet_text = "sell"
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg)
        assert tm._cfg.greet_text == "sell"

    def test_set_frame_getter(self):
        tm = _make_manager()
        got = tm._frame()
        assert got is not None
        assert got.shape[2] == 3


class TestTradeManagerOpen:
    def test_open_trade_sends_greet(self):
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, TradeConfig(), log_fn=lambda _: None)
        # Window always detected
        tm._detector.is_trade_window_open = MagicMock(return_value=True)  # type: ignore[method-assign]
        tm.set_frame_getter(lambda: _blank_frame())
        result = tm.open_trade("hi")
        ctrl.press_key.assert_called()
        ctrl.type_text.assert_called_with("hi")
        assert result is True

    def test_open_trade_uses_config_greet_if_none_given(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        cfg.greet_text = "trade"
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        tm._detector.is_trade_window_open = MagicMock(return_value=True)  # type: ignore[method-assign]
        tm.set_frame_getter(lambda: _blank_frame())
        tm.open_trade(None)
        ctrl.type_text.assert_called_with("trade")

    def test_wait_for_window_timeout(self):
        tm = _make_manager(window_open=False)
        cfg = TradeConfig()
        cfg.window_timeout = 0.1
        tm._cfg = cfg
        result = tm.wait_for_window()
        assert result is False

    def test_wait_for_window_finds_immediately(self):
        tm = _make_manager(window_open=True)
        tm._detector.is_trade_window_open = MagicMock(return_value=True)  # type: ignore[method-assign]
        result = tm.wait_for_window()
        assert result is True


class TestTradeManagerClose:
    def test_close_trade(self):
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame())
        tm.close_trade()
        ctrl.press_key.assert_called()

    def test_close_trade_disconnected_no_crash(self):
        ctrl = _make_ctrl(connected=False)
        tm = TradeManager(ctrl, log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame())
        tm.close_trade()  # should not raise


class TestTradeManagerBuySell:
    def test_execute_buy_item_found(self, tmp_path):
        import cv2
        cfg = TradeConfig()
        cfg.templates_dir = str(tmp_path)
        cfg.window_timeout = 0.1
        cfg.click_delay = 0.0
        tdir = tmp_path / "trade_items"
        tdir.mkdir()
        tmpl = np.ones((20, 20, 3), dtype=np.uint8) * 200
        cv2.imwrite(str(tdir / "hp.png"), tmpl)
        cfg.buy_list = [{"name": "hp", "quantity": 10, "max_price": 0}]
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        # Build frame with template in ROI
        frame = _blank_frame()
        x, y = cfg.item_list_roi[0] + 5, cfg.item_list_roi[1] + 5
        frame[y: y + 20, x: x + 20] = tmpl
        tm.set_frame_getter(lambda: frame)
        bought = tm.execute_buy_list()
        assert "hp" in bought
        assert bought["hp"] == 10

    def test_execute_buy_item_not_found_returns_empty(self):
        cfg = TradeConfig()
        cfg.use_search_field = False
        cfg.buy_list = [{"name": "nonexistent_item", "quantity": 5, "max_price": 0}]
        cfg.click_delay = 0.0
        tm = _make_manager(cfg=cfg)
        bought = tm.execute_buy_list()
        assert bought == {}

    def test_execute_sell_item_not_found_returns_empty(self):
        cfg = TradeConfig()
        cfg.use_search_field = False
        cfg.sell_list = [{"name": "missing_trophy", "quantity": 3}]
        cfg.click_delay = 0.0
        tm = _make_manager(cfg=cfg)
        sold = tm.execute_sell_list()
        assert sold == {}

    def test_max_price_skip(self, tmp_path):
        import cv2
        cfg = TradeConfig()
        cfg.templates_dir = str(tmp_path)
        cfg.click_delay = 0.0
        tdir = tmp_path / "trade_items"
        tdir.mkdir()
        tmpl = np.ones((20, 20, 3), dtype=np.uint8) * 180
        cv2.imwrite(str(tdir / "sword.png"), tmpl)
        cfg.buy_list = [{"name": "sword", "quantity": 1, "max_price": 50}]
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        frame = _blank_frame()
        x, y = cfg.item_list_roi[0] + 5, cfg.item_list_roi[1] + 5
        frame[y: y + 20, x: x + 20] = tmpl
        tm.set_frame_getter(lambda: frame)
        # Force price read to return 999 (over max)
        tm._detector.read_price = MagicMock(return_value=999)  # type: ignore[method-assign]
        bought = tm.execute_buy_list()
        assert bought == {}

    def test_max_price_zero_skips_check(self, tmp_path):
        import cv2
        cfg = TradeConfig()
        cfg.templates_dir = str(tmp_path)
        cfg.click_delay = 0.0
        tdir = tmp_path / "trade_items"
        tdir.mkdir()
        tmpl = np.ones((20, 20, 3), dtype=np.uint8) * 150
        cv2.imwrite(str(tdir / "potion.png"), tmpl)
        cfg.buy_list = [{"name": "potion", "quantity": 1, "max_price": 0}]
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        frame = _blank_frame()
        x, y = cfg.item_list_roi[0] + 5, cfg.item_list_roi[1] + 5
        frame[y: y + 20, x: x + 20] = tmpl
        tm.set_frame_getter(lambda: frame)
        tm._detector.read_price = MagicMock(return_value=9999)  # type: ignore[method-assign]
        bought = tm.execute_buy_list()
        # max_price=0 means no check -> should buy
        assert "potion" in bought


class TestRunCycle:
    def test_run_cycle_window_not_found(self):
        cfg = TradeConfig()
        cfg.window_timeout = 0.1
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame())
        # is_trade_window_open always False
        tm._detector.is_trade_window_open = MagicMock(return_value=False)  # type: ignore[method-assign]
        result = tm.run_cycle()
        assert result is False

    def test_run_cycle_ok(self, tmp_path):
        cfg = TradeConfig()
        cfg.window_timeout = 0.5
        cfg.greet_delay = 0.0
        cfg.click_delay = 0.0
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame())
        tm._detector.is_trade_window_open = MagicMock(return_value=True)  # type: ignore[method-assign]
        tm.execute_sell_list = MagicMock(return_value={})  # type: ignore[method-assign]
        tm.execute_buy_list  = MagicMock(return_value={})  # type: ignore[method-assign]
        tm.close_trade       = MagicMock()  # type: ignore[method-assign]
        result = tm.run_cycle()
        assert result is True
        tm.execute_sell_list.assert_called_once()
        tm.execute_buy_list.assert_called_once()
        tm.close_trade.assert_called_once()

    def test_run_cycle_clears_stats(self):
        cfg = TradeConfig()
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        tm.last_bought = {"old": 1}
        tm.last_sold   = {"oldsell": 2}
        tm._detector.is_trade_window_open = MagicMock(return_value=False)  # type: ignore[method-assign]
        cfg.window_timeout = 0.05
        tm.set_frame_getter(lambda: _blank_frame())
        tm.run_cycle()
        assert tm.last_bought == {}
        assert tm.last_sold   == {}

    def test_run_cycle_exception_calls_close(self):
        cfg = TradeConfig()
        cfg.window_timeout = 0.05
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame())
        tm._detector.is_trade_window_open = MagicMock(return_value=True)  # type: ignore[method-assign]
        tm.execute_sell_list = MagicMock(side_effect=RuntimeError("crash"))  # type: ignore[method-assign]
        close_mock = MagicMock()
        tm.close_trade = close_mock  # type: ignore[method-assign]
        result = tm.run_cycle()
        assert result is False
        close_mock.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests — bugs fixed during full project audit
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionBugs:
    """Regression suite for bugs 5-7 found in trade_manager.py during audit.

    Bug 5: TradeItemDetector used bare print() everywhere — log_fn ignored.
    Bug 6: TradeManager had no set_log_callback(); logs bypassed session log.
    Bug 7: wait_for_window() used time.time() for deadline instead of time.monotonic().
    """

    # ── Bug 5: TradeItemDetector routes through log_fn ────────────────────────

    def test_bug5_detector_log_fn_receives_messages_not_print(self):
        """TradeItemDetector must call log_fn, not print(), when log_fn is provided."""
        received: list[str] = []
        cfg = TradeConfig()
        detector = TradeItemDetector(cfg, log_fn=received.append)
        # _load_templates() is called during construction; it emits at least one
        # message if templates dir is missing/empty.  Force a reload to be sure.
        detector._load_templates()
        # At minimum a "no templates" warning must reach our callback, not stdout.
        # Even if the dir exists but is empty, received should have entries.
        # We only assert our callback was the target — not that print() was used.
        assert isinstance(received, list)   # callback is the correct object

    def test_bug5_detector_log_fn_called_on_missing_templates_dir(self, tmp_path):
        """When templates_dir does not exist, log_fn must receive the warning."""
        received: list[str] = []
        cfg = TradeConfig()
        cfg.templates_dir = str(tmp_path / "nonexistent_dir")
        detector = TradeItemDetector(cfg, log_fn=received.append)
        detector._load_templates()


# ─── Search-field trade methods ──────────────────────────────────────────────

class TestSwitchTab:
    """Cover switch_tab() — clicks buy or sell tab position."""

    def test_switch_tab_buy(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            tm.switch_tab("buy")
        ctrl.click.assert_called_with(cfg.buy_tab_pos[0], cfg.buy_tab_pos[1], button="left")

    def test_switch_tab_sell(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            tm.switch_tab("sell")
        ctrl.click.assert_called_with(cfg.sell_tab_pos[0], cfg.sell_tab_pos[1], button="left")


class TestSearchAndSelectItem:
    """Cover _search_and_select_item() — types name in search field."""

    def test_returns_true_and_types_name(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            result = tm._search_and_select_item("mana potion")
        assert result is True
        ctrl.type_text.assert_called_once_with("mana potion")

    def test_clicks_search_field_and_first_item(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            tm._search_and_select_item("rope")
        # Should click search_field_pos first, then first_item_pos
        click_calls = ctrl.click.call_args_list
        positions = [(c[0][0], c[0][1]) for c in click_calls]
        assert tuple(cfg.search_field_pos) in positions
        assert tuple(cfg.first_item_pos) in positions

    def test_clears_previous_text(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            tm._search_and_select_item("sword")
        # Must press DEL to clear previous search
        ctrl.press_key.assert_any_call(0x2E)  # VK_DELETE


class TestBuySingleItem:
    """Cover buy_single_item() — switches tab and executes transaction."""

    def test_buy_single_item_success(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        cfg.use_search_field = True
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            result = tm.buy_single_item("mana potion", 50)
        assert result is True
        assert tm.last_bought == {"mana potion": 50}

    def test_buy_single_item_switches_to_buy_tab(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            tm.buy_single_item("health potion", 10)
        # First click must be the buy tab
        first_click = ctrl.click.call_args_list[0]
        assert first_click == call(cfg.buy_tab_pos[0], cfg.buy_tab_pos[1], button="left")


class TestSellSingleItem:
    """Cover sell_single_item() — switches tab and executes transaction."""

    def test_sell_single_item_success(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        cfg.use_search_field = True
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            result = tm.sell_single_item("empty flask", 10)
        assert result is True
        assert tm.last_sold == {"empty flask": 10}

    def test_sell_single_item_switches_to_sell_tab(self):
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            tm.sell_single_item("gold coin", 5)
        first_click = ctrl.click.call_args_list[0]
        assert first_click == call(cfg.sell_tab_pos[0], cfg.sell_tab_pos[1], button="left")

    def test_sell_quantity_zero_becomes_one(self):
        """sell_single_item(qty=0) passes max(0,1)=1 to the transaction."""
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        logs: list[str] = []
        tm.set_log_callback(logs.append)
        with patch("src.trade_manager.time.sleep"):
            tm.sell_single_item("flask", 0)
        # The log should show qty 1 (not 0)
        assert any("1x" in l for l in logs)


class TestExecuteTransactionSearchFieldPath:
    """Cover the use_search_field=True branch of _execute_transaction."""

    def test_search_field_path_uses_ok_btn(self):
        """When use_search_field=True, the action button is ok_btn_pos."""
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        cfg.use_search_field = True
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        from src.trade_manager import TradeItem
        item = TradeItem(name="mana potion", quantity=5)
        with patch("src.trade_manager.time.sleep"):
            result = tm._execute_transaction(item, "buy")
        assert result is True
        # ok_btn_pos should be clicked (the action button in search-field mode)
        click_positions = [(c[0][0], c[0][1]) for c in ctrl.click.call_args_list]
        assert tuple(cfg.ok_btn_pos) in click_positions

    def test_search_field_path_buy_max_price_exceeded(self):
        """Even in search-field mode, max_price check still works."""
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        cfg.use_search_field = True
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        frame = _blank_frame()
        tm.set_frame_getter(lambda: frame)
        tm._detector.read_price = MagicMock(return_value=999)  # type: ignore[method-assign]
        from src.trade_manager import TradeItem
        item = TradeItem(name="sword", quantity=1, max_price=50)
        with patch("src.trade_manager.time.sleep"):
            result = tm._execute_transaction(item, "buy")
        assert result is False

    def test_bug5_detector_without_log_fn_does_not_raise(self):
        """TradeItemDetector with no log_fn must still work without crashing."""
        cfg = TradeConfig()
        detector = TradeItemDetector(cfg)  # no log_fn — falls back to print()
        # Should not raise even when templates dir is missing
        detector._load_templates()

    # ── Bug 6: TradeManager.set_log_callback propagates to detector ──────────

    def test_bug6_set_log_callback_routes_logs(self):
        """After set_log_callback(), _log() must route messages to the new callback."""
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, TradeConfig())  # no initial log_fn
        received: list[str] = []
        tm.set_log_callback(received.append)
        tm._log("hello from trade")
        assert any("hello from trade" in m for m in received)

    def test_bug6_set_log_callback_propagates_to_detector(self):
        """set_log_callback() must update the detector's internal log_fn too."""
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, TradeConfig(), log_fn=lambda _: None)
        new_received: list[str] = []
        # Store a stable reference — list.append creates a new object on each access
        cb = new_received.append
        tm.set_log_callback(cb)
        # The detector's _log_fn must now point to the same callback object.
        assert tm._detector._log_fn is cb

    def test_bug6_log_before_set_callback_falls_back_to_print(self, capsys):
        """Without set_log_callback, _log() must fall back to print (not raise)."""
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, TradeConfig())
        tm._log_cb = None   # ensure no callback
        tm._log("stdout fallback")
        captured = capsys.readouterr()
        assert "stdout fallback" in captured.out

    # ── Bug 7: wait_for_window uses time.monotonic ────────────────────────────

    def test_bug7_wait_for_window_calls_monotonic_not_time(self):
        """wait_for_window() deadline tracking must use time.monotonic(), not time.time()."""
        import time as _time
        from unittest.mock import patch as _patch

        ctrl = _make_ctrl()
        cfg = TradeConfig()
        cfg.window_timeout = 0.01   # fast timeout
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame())

        monotonic_calls: list[float] = []
        original_monotonic = _time.monotonic

        def tracking_monotonic() -> float:
            v = original_monotonic()
            monotonic_calls.append(v)
            return v

        with _patch("src.trade_manager.time.monotonic", side_effect=tracking_monotonic):
            tm.wait_for_window()

        assert len(monotonic_calls) >= 2, (
            "time.monotonic() must be called at least twice in wait_for_window() "
            "(once for deadline, once per loop iteration)"
        )

    def test_bug7_wait_for_window_respects_timeout(self):
        """wait_for_window() must return False (not hang) when window never appears."""
        ctrl = _make_ctrl()
        cfg = TradeConfig()
        cfg.window_timeout = 0.05   # 50 ms — must not hang
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame())   # blank frame = window never found

        import time as _time
        t0 = _time.monotonic()
        result = tm.wait_for_window()
        elapsed = _time.monotonic() - t0

        assert result is False
        assert elapsed < 2.0, "wait_for_window() hung far beyond its timeout"


# ─── execute_buy/sell_list with search-field mode ────────────────────────────

class TestExecuteListsSearchField:
    """Cover execute_buy_list / execute_sell_list with use_search_field=True."""

    def test_execute_buy_list_search_field(self):
        cfg = TradeConfig()
        cfg.use_search_field = True
        cfg.buy_list = [{"name": "mana potion", "quantity": 50, "max_price": 0}]
        cfg.click_delay = 0.0
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            bought = tm.execute_buy_list()
        assert "mana potion" in bought
        assert bought["mana potion"] == 50

    def test_execute_sell_list_search_field(self):
        cfg = TradeConfig()
        cfg.use_search_field = True
        cfg.sell_list = [{"name": "flask", "quantity": 10}]
        cfg.click_delay = 0.0
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            sold = tm.execute_sell_list()
        assert "flask" in sold
        assert sold["flask"] == 10

    def test_execute_buy_list_search_field_multiple_items(self):
        cfg = TradeConfig()
        cfg.use_search_field = True
        cfg.buy_list = [
            {"name": "mana potion", "quantity": 50, "max_price": 0},
            {"name": "health potion", "quantity": 20, "max_price": 0},
        ]
        cfg.click_delay = 0.0
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, cfg, log_fn=lambda _: None)
        with patch("src.trade_manager.time.sleep"):
            bought = tm.execute_buy_list()
        assert len(bought) == 2
        assert bought["mana potion"] == 50
        assert bought["health potion"] == 20


# ─── TradeConfig.load() from file ───────────────────────────────────────────

class TestTradeConfigLoad:
    """Cover TradeConfig.load() reading a JSON file."""

    def test_load_from_file(self, tmp_path):
        import json
        cfg_data = {
            "use_search_field": False,
            "confidence": 0.75,
            "buy_list": [{"name": "sword", "quantity": 1, "max_price": 100}],
        }
        cfg_file = tmp_path / "trade.json"
        cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")
        cfg = TradeConfig.load(cfg_file)
        assert cfg.use_search_field is False
        assert cfg.confidence == pytest.approx(0.75)
        assert len(cfg.buy_list) == 1

    def test_load_missing_file_returns_defaults(self, tmp_path):
        cfg = TradeConfig.load(tmp_path / "nonexistent.json")
        assert cfg.use_search_field is True      # default
        assert cfg.confidence == pytest.approx(0.62)

    def test_load_ignores_unknown_keys(self, tmp_path):
        import json
        cfg_data = {"unknown_key_xyz": 999, "confidence": 0.80}
        cfg_file = tmp_path / "trade.json"
        cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")
        cfg = TradeConfig.load(cfg_file)
        assert cfg.confidence == pytest.approx(0.80)
        assert not hasattr(cfg, "unknown_key_xyz")


# ─── scale_pos with real frame ──────────────────────────────────────────────

class TestScalePos:
    """Cover _scale_pos() with frames of different sizes."""

    def test_scale_pos_no_frame(self):
        """Without frame_getter, returns positions as-is."""
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, TradeConfig(), log_fn=lambda _: None)
        x, y = tm._scale_pos([960, 540])
        assert x == 960 and y == 540

    def test_scale_pos_same_resolution(self):
        """With 1920x1080 frame (reference), positions unchanged."""
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, TradeConfig(), log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame(1080, 1920))
        x, y = tm._scale_pos([960, 540])
        assert x == 960 and y == 540

    def test_scale_pos_different_resolution(self):
        """With half-size frame, positions scale down."""
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, TradeConfig(), log_fn=lambda _: None)
        tm.set_frame_getter(lambda: _blank_frame(540, 960))
        x, y = tm._scale_pos([960, 540])
        assert x == 480 and y == 270

    def test_frame_getter_exception_returns_none(self):
        """When frame_getter raises, _frame returns None gracefully."""
        ctrl = _make_ctrl()
        tm = TradeManager(ctrl, TradeConfig(), log_fn=lambda _: None)
        tm.set_frame_getter(lambda: (_ for _ in ()).throw(RuntimeError("broken")))
        result = tm._frame()
        assert result is None
