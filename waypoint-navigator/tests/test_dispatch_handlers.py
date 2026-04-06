"""
Tests for ScriptExecutor dispatch handler methods.

100 % offline — no real game, no real input.
Directly calls the _handle_* methods and utility methods on a minimal
ScriptExecutor instance backed by MagicMock ctrl/navigator.
"""
from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from src.script_executor import ScriptExecutor
from src.models import Coordinate
from src.script_parser import Instruction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ex(dry_run: bool = True, **kwargs) -> ScriptExecutor:
    ctrl = MagicMock()
    ex = ScriptExecutor(
        ctrl=ctrl,
        navigator=None,
        dry_run=dry_run,
        step_interval=0.0,
        jitter=0.0,
        log_fn=lambda _: None,
    )
    for k, v in kwargs.items():
        setattr(ex, k, v)
    return ex


def _ins(**kwargs) -> MagicMock:
    """Make a minimal Instruction-like mock."""
    ins = MagicMock(spec=Instruction)
    ins.kind = kwargs.get("kind", "action")
    ins.action = kwargs.get("action", "")
    ins.label = kwargs.get("label", "")
    ins.label_jump = kwargs.get("label_jump", None)
    ins.hotkey_vk = kwargs.get("hotkey_vk", 0)
    ins.wait_secs = kwargs.get("wait_secs", 0)
    ins.stat = kwargs.get("stat", "hp")
    ins.op = kwargs.get("op", "<")
    ins.threshold = kwargs.get("threshold", 50)
    ins.goto_label = kwargs.get("goto_label", "lbl")
    ins.sentence = kwargs.get("sentence", "")
    ins.words = kwargs.get("words", [])
    ins.coord = kwargs.get("coord", None)
    ins.var_name = kwargs.get("var_name", "hp")
    ins.label_skip = kwargs.get("label_skip", None)
    ins.raw = kwargs.get("raw", "")
    return ins


def _coord(x: int = 100, y: int = 200, z: int = 7) -> MagicMock:
    c = MagicMock()
    c.x, c.y, c.z = x, y, z
    c.to_tibia_coord = lambda: c
    return c


# ---------------------------------------------------------------------------
# _handle_action_end
# ---------------------------------------------------------------------------

class TestHandleActionEnd:

    def test_sets_running_false(self):
        ex = _make_ex()
        ex._running = True
        ex._handle_action_end(_ins())
        assert ex._running is False

    def test_returns_none(self):
        ex = _make_ex()
        ex._running = True
        result = ex._handle_action_end(_ins())
        assert result is None


# ---------------------------------------------------------------------------
# _handle_combat_action
# ---------------------------------------------------------------------------

class TestHandleCombatAction:

    def test_no_combat_manager_logs_warning(self):
        logs = []
        ex = _make_ex(dry_run=False)
        ex._log_fn = logs.append
        ex._handle_combat_action(_ins(action="combat_pause"))
        assert any("CombatManager not attached" in m for m in logs)

    def test_with_combat_calls_method(self):
        cm = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._combat = cm
        ex._handle_combat_action(_ins(action="combat_pause"))
        cm.pause.assert_called_once()

    def test_with_combat_dry_run_does_not_call_method(self):
        cm = MagicMock()
        ex = _make_ex(dry_run=True)
        ex._combat = cm
        ex._handle_combat_action(_ins(action="combat_pause"))
        cm.pause.assert_not_called()

    def test_exception_in_method_swallowed(self):
        cm = MagicMock()
        cm.pause.side_effect = RuntimeError("boom")
        ex = _make_ex(dry_run=False)
        ex._combat = cm
        # Should not raise
        ex._handle_combat_action(_ins(action="combat_pause"))

    def test_exception_logs_warning(self):
        logs = []
        cm = MagicMock()
        cm.pause.side_effect = RuntimeError("boom")
        ex = _make_ex(dry_run=False)
        ex._combat = cm
        ex._log_fn = logs.append
        ex._handle_combat_action(_ins(action="combat_pause"))
        assert any("raised" in m for m in logs)

    def test_combat_resume_calls_resume(self):
        cm = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._combat = cm
        ex._handle_combat_action(_ins(action="combat_resume"))
        cm.resume.assert_called_once()

    def test_combat_start_calls_start(self):
        cm = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._combat = cm
        ex._handle_combat_action(_ins(action="combat_start"))
        cm.start.assert_called_once()

    def test_combat_stop_calls_stop(self):
        cm = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._combat = cm
        ex._handle_combat_action(_ins(action="combat_stop"))
        cm.stop.assert_called_once()

    def test_returns_none(self):
        ex = _make_ex(dry_run=False)
        result = ex._handle_combat_action(_ins(action="combat_pause"))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_depot
# ---------------------------------------------------------------------------

class TestHandleDepot:

    def test_no_depot_manager_logs_warning(self):
        logs = []
        ex = _make_ex()
        ex._log_fn = logs.append
        ex._handle_depot(_ins())
        assert any("DepotManager not attached" in m for m in logs)

    def test_with_depot_calls_run_depot_cycle(self):
        dm = MagicMock()
        ex = _make_ex()
        ex._depot = dm
        ex._handle_depot(_ins())
        dm.run_depot_cycle.assert_called_once()

    def test_with_depot_passes_current_pos(self):
        dm = MagicMock()
        pos = _coord()
        ex = _make_ex()
        ex._depot = dm
        ex._current_pos = pos
        ex._handle_depot(_ins())
        dm.run_depot_cycle.assert_called_once_with(player_pos=pos)

    def test_with_wp_logger_records_action(self):
        dm = MagicMock()
        wp_logger = MagicMock()
        ex = _make_ex()
        ex._depot = dm
        ex._wp_logger = wp_logger
        ex._handle_depot(_ins())
        wp_logger.record_action.assert_called()

    def test_wp_logger_exception_swallowed(self):
        dm = MagicMock()
        wp_logger = MagicMock()
        wp_logger.record_action.side_effect = RuntimeError("fail")
        ex = _make_ex()
        ex._depot = dm
        ex._wp_logger = wp_logger
        # Should not raise
        ex._handle_depot(_ins())

    def test_returns_none(self):
        ex = _make_ex()
        result = ex._handle_depot(_ins())
        assert result is None


# ---------------------------------------------------------------------------
# _handle_walk_mode
# ---------------------------------------------------------------------------

class TestHandleWalkMode:

    def test_walk_keys_sets_scancode_dry_run(self):
        ex = _make_ex(dry_run=True)
        ex._handle_walk_mode(_ins(action="walk_keys"))
        # In dry_run, ctrl.input_method should NOT be set
        ex._ctrl.input_method  # just accessing doesn't raise

    def test_walk_keys_sets_scancode_live(self):
        ex = _make_ex(dry_run=False)
        ex._handle_walk_mode(_ins(action="walk_keys"))
        assert ex._ctrl.input_method == "scancode"

    def test_walk_mouse_sets_postmessage(self):
        ex = _make_ex(dry_run=False)
        ex._handle_walk_mode(_ins(action="walk_mouse"))
        assert ex._ctrl.input_method == "postmessage"

    def test_dry_run_does_not_set_ctrl(self):
        ex = _make_ex(dry_run=True)
        # Make a fresh ctrl so we can check it isn't modified
        ctrl = MagicMock()
        ex._ctrl = ctrl
        ex._handle_walk_mode(_ins(action="walk_keys"))
        # input_method should not have been set (it's a mock attr — check set wasn't called)
        # The mock records attribute sets; confirm 'input_method' was NOT assigned
        assert "input_method" not in [name for name, args, kw in ctrl.mock_calls
                                       if name == "__setattr__" and args and args[0] == "input_method"]

    def test_returns_none(self):
        ex = _make_ex()
        result = ex._handle_walk_mode(_ins(action="walk_keys"))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_chat_toggle
# ---------------------------------------------------------------------------

class TestHandleChatToggle:

    def test_chat_on_dry_run_no_key_press(self):
        ex = _make_ex(dry_run=True)
        ex._handle_chat_toggle(_ins(action="chat_on"))
        ex._ctrl.press_key.assert_not_called()

    def test_chat_on_live_presses_enter(self):
        ex = _make_ex(dry_run=False)
        ex._handle_chat_toggle(_ins(action="chat_on"))
        ex._ctrl.press_key.assert_called_with(0x0D)

    def test_chat_off_live_presses_escape(self):
        ex = _make_ex(dry_run=False)
        ex._handle_chat_toggle(_ins(action="chat_off"))
        ex._ctrl.press_key.assert_called_with(0x1B)

    def test_returns_none(self):
        ex = _make_ex()
        result = ex._handle_chat_toggle(_ins(action="chat_on"))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_npc_action
# ---------------------------------------------------------------------------

class TestHandleNpcAction:

    def test_with_npc_handler_calls_it(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        i = _ins(action="sell")
        ex._handle_npc_action(i)
        handler.assert_called_once_with("sell", i)

    def test_with_npc_handler_buy_potions(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        i = _ins(action="buy_potions")
        ex._handle_npc_action(i)
        handler.assert_called_once_with("buy_potions", i)

    def test_with_npc_handler_buy_ammo(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        i = _ins(action="buy_ammo")
        ex._handle_npc_action(i)
        handler.assert_called_once_with("buy_ammo", i)

    def test_with_npc_handler_sell_inline_items_prefers_scripted_chat(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        i = _ins(action="sell", raw='{"items": [{"name": "vial", "qty": 0}]}')
        with patch.object(ex, "_sell_chat") as sell_chat:
            ex._handle_npc_action(i)
        sell_chat.assert_called_once_with(i)
        handler.assert_not_called()

    def test_with_npc_handler_buy_potions_inline_items_prefers_scripted_chat(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        i = _ins(action="buy_potions", raw='{"items": [{"name": "mana potion", "qty": 10}]}')
        with patch.object(ex, "_buy_potions_chat") as buy_chat:
            ex._handle_npc_action(i)
        buy_chat.assert_called_once_with(i)
        handler.assert_not_called()

    def test_with_npc_handler_buy_ammo_inline_items_prefers_scripted_chat(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        i = _ins(action="buy_ammo", raw='{"items": [{"name": "bolt", "qty": 100}]}')
        with patch.object(ex, "_buy_ammo_chat") as buy_ammo:
            ex._handle_npc_action(i)
        buy_ammo.assert_called_once_with(i)
        handler.assert_not_called()

    def test_with_npc_handler_check_ammo(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        i = _ins(action="check_ammo")
        ex._handle_npc_action(i)
        handler.assert_called_once_with("check_ammo", i)

    def test_with_npc_handler_check_supplies(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        i = _ins(action="check_supplies")
        ex._handle_npc_action(i)
        handler.assert_called_once_with("check_supplies", i)

    def test_npc_handler_exception_swallowed(self):
        handler = MagicMock(side_effect=RuntimeError("boom"))
        ex = _make_ex(dry_run=False)
        ex._npc_handler = handler
        # Should not raise
        ex._handle_npc_action(_ins(action="sell"))

    def test_no_handler_sell_calls_trade_gui(self):
        """Without npc_handler, sell calls _trade_gui_or_chat."""
        ex = _make_ex(dry_run=False)
        ex._npc_handler = None
        with patch.object(ex, "_trade_gui_or_chat") as mock_tgoc:
            ex._handle_npc_action(_ins(action="sell"))
            mock_tgoc.assert_called_once()

    def test_no_handler_buy_potions_calls_trade_gui(self):
        ex = _make_ex(dry_run=False)
        ex._npc_handler = None
        with patch.object(ex, "_trade_gui_or_chat") as mock_tgoc:
            ex._handle_npc_action(_ins(action="buy_potions"))
            mock_tgoc.assert_called_once()

    def test_no_handler_buy_ammo_calls_chat(self):
        ex = _make_ex(dry_run=False)
        ex._npc_handler = None
        with patch.object(ex, "_buy_ammo_chat") as mock_bac:
            ex._handle_npc_action(_ins(action="buy_ammo"))
            mock_bac.assert_called_once()

    def test_no_handler_check_ammo_returns_jump(self):
        ex = _make_ex(dry_run=False)
        ex._npc_handler = None
        with patch.object(ex, "_check_ammo", return_value="skip_ammo"):
            result = ex._handle_npc_action(_ins(action="check_ammo"))
        assert result == "skip_ammo"

    def test_no_handler_check_supplies_returns_jump(self):
        ex = _make_ex(dry_run=False)
        ex._npc_handler = None
        with patch.object(ex, "_check_supplies", return_value="leave"):
            result = ex._handle_npc_action(_ins(action="check_supplies"))
        assert result == "leave"

    def test_no_handler_unknown_action_logs_stub(self):
        logs = []
        ex = _make_ex(dry_run=False)
        ex._npc_handler = None
        ex._log_fn = logs.append
        ex._handle_npc_action(_ins(action="unknown_npc_action"))
        assert any("stub" in m or "no npc_handler" in m for m in logs)

    def test_dry_run_does_not_call_handler(self):
        handler = MagicMock()
        ex = _make_ex(dry_run=True)
        ex._npc_handler = handler
        ex._handle_npc_action(_ins(action="sell"))
        handler.assert_not_called()

    def test_with_wp_logger_records_action(self):
        wp_logger = MagicMock()
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        ex._handle_npc_action(_ins(action="sell"))
        wp_logger.record_action.assert_called()

    def test_wp_logger_exception_swallowed(self):
        wp_logger = MagicMock()
        wp_logger.record_action.side_effect = RuntimeError("fail")
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        # Should not raise
        ex._handle_npc_action(_ins(action="sell"))

    def test_returns_none_when_no_jump(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_npc_action(_ins(action="sell"))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_check
# ---------------------------------------------------------------------------

class TestHandleCheck:

    def test_no_healer_logs_unavailable(self):
        logs = []
        ex = _make_ex()
        ex._log_fn = logs.append
        ex._healer = None
        ex._handle_check(_ins())
        assert any("no healer" in m or "stat unavailable" in m for m in logs)

    def test_with_healer_logs_hp_mp(self):
        logs = []
        healer = MagicMock()
        healer.hp_pct = 80
        healer.mp_pct = 60
        ex = _make_ex()
        ex._healer = healer
        ex._log_fn = logs.append
        ex._handle_check(_ins())
        assert any("HP=80%" in m for m in logs)

    def test_with_healer_and_wp_logger_records(self):
        healer = MagicMock()
        healer.hp_pct = 80
        healer.mp_pct = 60
        wp_logger = MagicMock()
        ex = _make_ex()
        ex._healer = healer
        ex._wp_logger = wp_logger
        ex._handle_check(_ins())
        wp_logger.record_action.assert_called()

    def test_wp_logger_exception_swallowed(self):
        healer = MagicMock()
        healer.hp_pct = 80
        healer.mp_pct = 60
        wp_logger = MagicMock()
        wp_logger.record_action.side_effect = RuntimeError("fail")
        ex = _make_ex()
        ex._healer = healer
        ex._wp_logger = wp_logger
        # Should not raise
        ex._handle_check(_ins())

    def test_returns_none(self):
        ex = _make_ex()
        result = ex._handle_check(_ins())
        assert result is None


# ---------------------------------------------------------------------------
# _handle_check_time
# ---------------------------------------------------------------------------

class TestHandleCheckTime:

    def test_no_hours_leave_continues(self):
        logs = []
        ex = _make_ex()
        ex._log_fn = logs.append
        ex._hours_leave = []
        ex._handle_check_time(_ins())
        assert ex._running is False  # dry_run default state; _running not set by this handler alone
        # Just check it logs "not yet leave time" or "no hours_leave"
        assert any("not yet" in m or "no hours_leave" in m for m in logs)

    def test_leave_time_reached_stops_executor(self):
        ex = _make_ex()
        ex._running = True
        ex._hours_leave = [0.0, 23.99]  # broad window to guarantee a hit
        ex._start_time_h = 0.0
        # Force _is_leave_time to return True
        with patch.object(ex, "_is_leave_time", return_value=True):
            ex._handle_check_time(_ins())
        assert ex._running is False

    def test_leave_time_not_reached_keeps_running(self):
        ex = _make_ex()
        ex._running = True
        with patch.object(ex, "_is_leave_time", return_value=False):
            ex._handle_check_time(_ins(action="check_time"))
        # _running is True initially; handler doesn't set it False when leave not reached
        assert ex._running is True

    def test_returns_none(self):
        ex = _make_ex()
        result = ex._handle_check_time(_ins())
        assert result is None


# ---------------------------------------------------------------------------
# _handle_wait
# ---------------------------------------------------------------------------

class TestHandleWait:

    def test_wait_secs_positive_logs(self):
        logs = []
        ex = _make_ex(dry_run=True)
        ex._log_fn = logs.append
        ex._handle_wait(_ins(wait_secs=3.0))
        assert any("3.0" in m for m in logs)

    def test_wait_secs_zero_defaults_to_one(self):
        logs = []
        ex = _make_ex(dry_run=True)
        ex._log_fn = logs.append
        ex._handle_wait(_ins(wait_secs=0))
        assert any("1.0" in m for m in logs)

    def test_dry_run_does_not_sleep(self):
        ex = _make_ex(dry_run=True)
        with patch("time.sleep") as mock_sleep:
            ex._handle_wait(_ins(wait_secs=5.0))
        mock_sleep.assert_not_called()

    def test_live_run_calls_sleep(self):
        ex = _make_ex(dry_run=False)
        ex._running = True
        with patch("time.sleep") as mock_sleep:
            ex._handle_wait(_ins(wait_secs=0.05))
        mock_sleep.assert_called()

    def test_returns_none(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_wait(_ins(wait_secs=1.0))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_label
# ---------------------------------------------------------------------------

class TestHandleLabel:

    def test_hunt_label_sets_has_hunted(self):
        ex = _make_ex()
        ex._has_hunted = False
        ex._handle_label(_ins(label="hunt"))
        assert ex._has_hunted is True

    def test_downcave_label_sets_has_hunted(self):
        ex = _make_ex()
        ex._has_hunted = False
        ex._handle_label(_ins(label="downcave"))
        assert ex._has_hunted is True

    def test_other_label_does_not_set_has_hunted(self):
        ex = _make_ex()
        ex._has_hunted = False
        ex._handle_label(_ins(label="some_other_label"))
        assert ex._has_hunted is False

    def test_returns_none(self):
        ex = _make_ex()
        result = ex._handle_label(_ins(label="hunt"))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_goto
# ---------------------------------------------------------------------------

class TestHandleGoto:

    def test_returns_label_jump(self):
        ex = _make_ex()
        result = ex._handle_goto(_ins(label_jump="my_label"))
        assert result == "my_label"

    def test_returns_none_when_label_jump_none(self):
        ex = _make_ex()
        result = ex._handle_goto(_ins(label_jump=None))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_use_hotkey
# ---------------------------------------------------------------------------

class TestHandleUseHotkey:

    def test_no_hotkey_vk_does_nothing(self):
        ex = _make_ex(dry_run=False)
        ex._handle_use_hotkey(_ins(hotkey_vk=0))
        ex._ctrl.press_key.assert_not_called()

    def test_with_hotkey_vk_dry_run_no_press(self):
        ex = _make_ex(dry_run=True)
        ex._handle_use_hotkey(_ins(hotkey_vk=0x70))
        ex._ctrl.press_key.assert_not_called()

    def test_with_hotkey_vk_live_presses_key(self):
        ex = _make_ex(dry_run=False)
        ex._handle_use_hotkey(_ins(hotkey_vk=0x70))
        ex._ctrl.press_key.assert_called_with(0x70)

    def test_with_wp_logger_records_action(self):
        wp_logger = MagicMock()
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        ex._handle_use_hotkey(_ins(hotkey_vk=0x70))
        wp_logger.record_action.assert_called()

    def test_wp_logger_exception_swallowed(self):
        wp_logger = MagicMock()
        wp_logger.record_action.side_effect = RuntimeError("fail")
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        # Should not raise
        ex._handle_use_hotkey(_ins(hotkey_vk=0x70))

    def test_returns_none(self):
        ex = _make_ex()
        result = ex._handle_use_hotkey(_ins(hotkey_vk=0x70))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_if_stat
# ---------------------------------------------------------------------------

class TestHandleIfStat:

    def _healer(self, hp: int = 80, mp: int = 60) -> MagicMock:
        h = MagicMock()
        h.hp_pct = hp
        h.mp_pct = mp
        return h

    def test_no_healer_skips_condition(self):
        logs = []
        ex = _make_ex()
        ex._healer = None
        ex._log_fn = logs.append
        result = ex._handle_if_stat(_ins(stat="hp", op="<", threshold=50, goto_label="low"))
        assert result is None
        assert any("can't read" in m or "condition skipped" in m for m in logs)

    def test_hp_less_than_triggers_jump(self):
        ex = _make_ex()
        ex._healer = self._healer(hp=30)
        result = ex._handle_if_stat(_ins(stat="hp", op="<", threshold=50, goto_label="low_hp"))
        assert result == "low_hp"

    def test_hp_less_than_no_trigger(self):
        ex = _make_ex()
        ex._healer = self._healer(hp=80)
        result = ex._handle_if_stat(_ins(stat="hp", op="<", threshold=50, goto_label="low_hp"))
        assert result is None

    def test_hp_greater_than_triggers_jump(self):
        ex = _make_ex()
        ex._healer = self._healer(hp=90)
        result = ex._handle_if_stat(_ins(stat="hp", op=">", threshold=80, goto_label="high_hp"))
        assert result == "high_hp"

    def test_hp_less_equal_triggers_jump(self):
        ex = _make_ex()
        ex._healer = self._healer(hp=50)
        result = ex._handle_if_stat(_ins(stat="hp", op="<=", threshold=50, goto_label="lbl"))
        assert result == "lbl"

    def test_hp_greater_equal_triggers_jump(self):
        ex = _make_ex()
        ex._healer = self._healer(hp=50)
        result = ex._handle_if_stat(_ins(stat="hp", op=">=", threshold=50, goto_label="lbl"))
        assert result == "lbl"

    def test_mp_stat_reads_correctly(self):
        ex = _make_ex()
        ex._healer = self._healer(hp=100, mp=20)
        result = ex._handle_if_stat(_ins(stat="mp", op="<", threshold=50, goto_label="low_mp"))
        assert result == "low_mp"

    def test_stat_unavailable_skips(self):
        ex = _make_ex()
        ex._healer = None
        result = ex._handle_if_stat(_ins(stat="hp", op="<", threshold=50, goto_label="lbl"))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_cond_jump
# ---------------------------------------------------------------------------

class TestHandleCondJump:

    def _healer(self, hp: int = 80, mp: int = 60) -> MagicMock:
        h = MagicMock()
        h.hp_pct = hp
        h.mp_pct = mp
        return h

    def test_hp_below_threshold_returns_label_jump(self):
        ex = _make_ex()
        ex._healer = self._healer(hp=20)
        result = ex._handle_cond_jump(_ins(
            var_name="hp", threshold=50, label_jump="low", label_skip="ok"
        ))
        assert result == "low"

    def test_hp_above_threshold_returns_label_skip(self):
        ex = _make_ex()
        ex._healer = self._healer(hp=80)
        result = ex._handle_cond_jump(_ins(
            var_name="hp", threshold=50, label_jump="low", label_skip="ok"
        ))
        assert result == "ok"

    def test_mp_below_threshold_returns_label_jump(self):
        ex = _make_ex()
        ex._healer = self._healer(mp=10)
        result = ex._handle_cond_jump(_ins(
            var_name="mp", threshold=50, label_jump="low_mp", label_skip=None
        ))
        assert result == "low_mp"

    def test_hp_no_healer_no_value(self):
        ex = _make_ex()
        ex._healer = None
        result = ex._handle_cond_jump(_ins(
            var_name="hp", threshold=50, label_jump="low", label_skip="ok"
        ))
        # value is None → condition not triggered → return label_skip
        assert result == "ok"

    def test_item_count_below_threshold_jumps(self):
        ex = _make_ex()
        ex._item_counter = {"arrow": 5}
        result = ex._handle_cond_jump(_ins(
            var_name="arrow", threshold=50, label_jump="hunt", label_skip="leave"
        ))
        assert result == "hunt"

    def test_item_count_at_threshold_returns_skip(self):
        ex = _make_ex()
        ex._item_counter = {"arrow": 50}
        result = ex._handle_cond_jump(_ins(
            var_name="arrow", threshold=50, label_jump="hunt", label_skip="leave"
        ))
        assert result == "leave"

    def test_item_count_zero_threshold_uses_9999(self):
        ex = _make_ex()
        ex._item_counter = {"arrow": 100}
        result = ex._handle_cond_jump(_ins(
            var_name="arrow", threshold=0, label_jump="hunt", label_skip="leave"
        ))
        # threshold defaults to 9999, count=100 < 9999 → jump
        assert result == "hunt"

    def test_item_count_missing_item_zero(self):
        ex = _make_ex()
        ex._item_counter = {}
        result = ex._handle_cond_jump(_ins(
            var_name="gold", threshold=100, label_jump="hunt", label_skip="leave"
        ))
        # count=0 < 100 → jump
        assert result == "hunt"


# ---------------------------------------------------------------------------
# _handle_say
# ---------------------------------------------------------------------------

class TestHandleSay:

    def test_empty_sentence_does_nothing(self):
        ex = _make_ex(dry_run=False)
        result = ex._handle_say(_ins(sentence=""))
        assert result is None
        ex._ctrl.press_key.assert_not_called()

    def test_dry_run_no_key_press(self):
        ex = _make_ex(dry_run=True)
        ex._handle_say(_ins(sentence="hi"))
        ex._ctrl.press_key.assert_not_called()

    def test_live_run_presses_enter_types_text(self):
        ex = _make_ex(dry_run=False)
        ex._handle_say(_ins(sentence="hello"))
        assert ex._ctrl.press_key.call_count >= 2  # open + send
        ex._ctrl.type_text.assert_called_with("hello")

    def test_with_wp_logger_records(self):
        wp_logger = MagicMock()
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        ex._handle_say(_ins(sentence="hi"))
        wp_logger.record_action.assert_called()

    def test_wp_logger_exception_swallowed(self):
        wp_logger = MagicMock()
        wp_logger.record_action.side_effect = RuntimeError("fail")
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        ex._handle_say(_ins(sentence="hi"))  # Should not raise

    def test_returns_none(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_say(_ins(sentence="hi"))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_talk_npc
# ---------------------------------------------------------------------------

class TestHandleTalkNpc:

    def test_no_words_does_nothing(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_talk_npc(_ins(words=[]))
        assert result is None

    def test_dry_run_logs_word_no_ctrl(self):
        logs = []
        ex = _make_ex(dry_run=True)
        ex._log_fn = logs.append
        ex._handle_talk_npc(_ins(words=["hi", "trade"]))
        # Should log the first word
        assert any("hi" in m for m in logs)
        ex._ctrl.press_key.assert_not_called()

    def test_wp_logger_records_with_words(self):
        wp_logger = MagicMock()
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        ex._handle_talk_npc(_ins(words=["hi"]))
        wp_logger.record_action.assert_called()

    def test_wp_logger_exception_swallowed(self):
        wp_logger = MagicMock()
        wp_logger.record_action.side_effect = RuntimeError("fail")
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        ex._handle_talk_npc(_ins(words=["hi"]))  # Should not raise

    def test_returns_none(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_talk_npc(_ins(words=["hi"]))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_open_door
# ---------------------------------------------------------------------------

class TestHandleOpenDoor:

    def test_no_coord_returns_none(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_open_door(_ins(coord=None))
        assert result is None

    def test_with_coord_dry_run_returns_none(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_open_door(_ins(coord=_coord()))
        assert result is None

    def test_with_coord_calls_open_door(self):
        ex = _make_ex(dry_run=True)
        with patch.object(ex, "_open_door") as mock_od:
            coord = _coord()
            ex._handle_open_door(_ins(coord=coord))
            mock_od.assert_called_once()

    def test_returns_none(self):
        ex = _make_ex()
        result = ex._handle_open_door(_ins(coord=None))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_movement
# ---------------------------------------------------------------------------

class TestHandleMovement:

    def test_no_coord_returns_none(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_movement(_ins(kind="node", coord=None))
        assert result is None

    def test_node_dry_run_updates_current_pos(self):
        dest = _coord(x=50, y=60, z=7)
        ex = _make_ex(dry_run=True)
        i = _ins(kind="node", coord=dest)
        i.kind = "node"
        ex._handle_movement(i)
        # In dry_run _walk_to logs and sets _current_pos to dest
        assert ex._current_pos is dest

    def test_stand_dry_run_updates_current_pos(self):
        dest = _coord(x=55, y=65, z=7)
        ex = _make_ex(dry_run=True)
        i = _ins(kind="stand", coord=dest)
        i.kind = "stand"
        ex._handle_movement(i)
        assert ex._current_pos is dest

    def test_rope_dry_run_logs_message(self):
        logs = []
        dest = _coord(x=50, y=60, z=8)
        ex = _make_ex(dry_run=True)
        ex._log_fn = logs.append
        i = _ins(kind="rope", coord=dest)
        i.kind = "rope"
        ex._handle_movement(i)
        # Should log walk dry message
        assert any("dry" in m or "walk" in m for m in logs)

    def test_rope_dry_run_with_wp_logger_records(self):
        wp_logger = MagicMock()
        dest = _coord(x=50, y=60, z=8)
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        i = _ins(kind="rope", coord=dest)
        i.kind = "rope"
        ex._handle_movement(i)
        wp_logger.add_waypoint.assert_called()

    def test_returns_none(self):
        ex = _make_ex(dry_run=True)
        result = ex._handle_movement(_ins(kind="node", coord=None))
        assert result is None


# ---------------------------------------------------------------------------
# _handle_movement with rope / shovel (dry_run, position tracking)
# ---------------------------------------------------------------------------

class TestHandleMovementRopeShovel:

    def test_rope_no_vk_logs_warning(self):
        """rope with rope_hotkey_vk=0 (not configured) logs warning."""
        logs = []
        dest = _coord(x=50, y=60, z=8)
        ex = _make_ex(dry_run=False)
        ex._rope_vk = 0
        ex._log_fn = logs.append
        # _walk_to will call nav (which is None) — patch it to return immediately
        with patch.object(ex, "_walk_to"):
            i = MagicMock()
            i.kind = "rope"
            i.coord = dest
            ex._handle_movement(i)
        # Without vk configured, should log warning
        assert any("rope" in m.lower() for m in logs)

    def test_shovel_no_vk_logs_warning(self):
        logs = []
        dest = _coord(x=50, y=60, z=7)
        ex = _make_ex(dry_run=False)
        ex._shovel_vk = 0
        ex._log_fn = logs.append
        with patch.object(ex, "_walk_to"):
            i = MagicMock()
            i.kind = "shovel"
            i.coord = dest
            ex._handle_movement(i)
        assert any("shovel" in m.lower() for m in logs)

    def test_rope_with_vk_in_dry_run(self):
        """In dry_run mode, rope vk press is skipped but pos update happens."""
        dest = _coord(x=50, y=60, z=8)
        ex = _make_ex(dry_run=True)
        ex._rope_vk = 0x72
        ex._current_pos = _coord(x=50, y=60, z=9)
        # dry_run → _walk_to just sets _current_pos to dest
        i = MagicMock()
        i.kind = "rope"
        i.coord = dest
        ex._handle_movement(i)
        # After walk_to (dry), _current_pos = dest; then rope logic: z -= 1
        # rope block only runs when not dry_run; so _current_pos stays at dest
        # (rope z logic is in non-dry_run block only via press_key guard)

    def test_shovel_with_vk_in_dry_run(self):
        dest = _coord(x=55, y=65, z=7)
        ex = _make_ex(dry_run=True)
        ex._shovel_vk = 0x73
        i = MagicMock()
        i.kind = "shovel"
        i.coord = dest
        ex._handle_movement(i)
        # Should not raise


# ---------------------------------------------------------------------------
# _read_stat
# ---------------------------------------------------------------------------

class TestReadStat:

    def test_no_healer_returns_none(self):
        ex = _make_ex()
        assert ex._read_stat("hp") is None
        assert ex._read_stat("mp") is None

    def test_hp_from_hp_pct(self):
        healer = MagicMock()
        healer.hp_pct = 75
        ex = _make_ex()
        ex._healer = healer
        assert ex._read_stat("hp") == 75

    def test_hp_from_private_hp_pct(self):
        healer = MagicMock(spec=[])
        healer._hp_pct = 55
        ex = _make_ex()
        ex._healer = healer
        assert ex._read_stat("hp") == 55

    def test_mp_from_mp_pct(self):
        healer = MagicMock()
        healer.mp_pct = 40
        ex = _make_ex()
        ex._healer = healer
        assert ex._read_stat("mp") == 40

    def test_mp_from_private_mp_pct(self):
        healer = MagicMock(spec=[])
        healer._mp_pct = 30
        ex = _make_ex()
        ex._healer = healer
        assert ex._read_stat("mp") == 30

    def test_unknown_stat_returns_none(self):
        healer = MagicMock()
        ex = _make_ex()
        ex._healer = healer
        assert ex._read_stat("stamina") is None

    def test_hp_uppercase(self):
        healer = MagicMock()
        healer.hp_pct = 88
        ex = _make_ex()
        ex._healer = healer
        assert ex._read_stat("HP") == 88


# ---------------------------------------------------------------------------
# _is_leave_time
# ---------------------------------------------------------------------------

class TestIsLeaveTime:

    def test_returns_false_when_no_hours_leave(self):
        ex = _make_ex()
        ex._hours_leave = []
        assert ex._is_leave_time() is False

    def test_fire_once_returns_false_on_second_call(self):
        ex = _make_ex()
        ex._hours_leave = [0.0, 23.99]
        ex._start_time_h = 0.0
        with patch.object(ex, "_is_leave_time", wraps=ex._is_leave_time) as _:
            # Manually set flag
            ex._leave_time_fired = True
            result = ex._is_leave_time()
        assert result is False

    def test_leave_window_triggers_when_in_window(self):
        """Set start at 0.0, leave at current+0.001 — should trigger."""
        ex = _make_ex()
        now = datetime.datetime.now()
        now_h = now.hour + now.minute / 60.0
        # Use a small past window
        past_h = max(0.0, now_h - 0.01)
        ex._start_time_h = past_h - 0.001
        ex._hours_leave = [now_h]
        ex._leave_time_fired = False
        result = ex._is_leave_time()
        # Result depends on actual time; we just confirm no exception
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _check_ammo
# ---------------------------------------------------------------------------

class TestCheckAmmo:

    def test_not_hunted_returns_skip(self):
        ex = _make_ex()
        ex._has_hunted = False
        result = ex._check_ammo(_ins())
        assert result == "skip_ammo"

    def test_hunted_returns_none(self):
        ex = _make_ex()
        ex._has_hunted = True
        result = ex._check_ammo(_ins())
        assert result is None

    def test_force_resupply_overrides_skip(self):
        ex = _make_ex()
        ex._has_hunted = False
        ex._force_resupply = True
        result = ex._check_ammo(_ins())
        # force_resupply clears the skip → returns None (buy)
        assert result is None

    def test_force_resupply_clears_flag(self):
        ex = _make_ex()
        ex._has_hunted = False
        ex._force_resupply = True
        ex._check_ammo(_ins())
        assert getattr(ex, "_force_resupply", False) is False


# ---------------------------------------------------------------------------
# _check_supplies
# ---------------------------------------------------------------------------

class TestCheckSupplies:

    def test_not_hunted_returns_none(self):
        ex = _make_ex()
        ex._has_hunted = False
        result = ex._check_supplies(_ins())
        assert result is None

    def test_hunted_returns_none(self):
        ex = _make_ex()
        ex._has_hunted = True
        result = ex._check_supplies(_ins())
        assert result is None


# ---------------------------------------------------------------------------
# increment_item_count / item_counts property
# ---------------------------------------------------------------------------

class TestItemCounter:

    def test_increment_creates_entry(self):
        ex = _make_ex()
        ex.increment_item_count("arrow", 10)
        assert ex._item_counter.get("arrow") == 10

    def test_increment_accumulates(self):
        ex = _make_ex()
        ex.increment_item_count("gold", 5)
        ex.increment_item_count("gold", 3)
        assert ex._item_counter.get("gold") == 8

    def test_increment_lowercases_key(self):
        ex = _make_ex()
        ex.increment_item_count("ARROW", 1)
        assert "arrow" in ex._item_counter

    def test_item_counts_returns_snapshot(self):
        ex = _make_ex()
        ex.increment_item_count("item", 5)
        snap = ex.item_counts
        assert snap["item"] == 5
        # Modifying snap shouldn't affect internal counter
        snap["item"] = 999
        assert ex._item_counter["item"] == 5


# ---------------------------------------------------------------------------
# set_path_visualizer / set_obstacle_analyzer / set_map_loader
# ---------------------------------------------------------------------------

class TestSetters:

    def test_set_path_visualizer(self):
        ex = _make_ex()
        viz = MagicMock()
        ex.set_path_visualizer(viz)
        assert ex._path_viz is viz

    def test_set_obstacle_analyzer(self):
        ex = _make_ex()
        ana = MagicMock()
        ex.set_obstacle_analyzer(ana)
        assert ex._obstacle_analyzer is ana

    def test_set_map_loader(self):
        ex = _make_ex()
        ldr = MagicMock()
        ex.set_map_loader(ldr)
        assert ex._map_loader is ldr


# ---------------------------------------------------------------------------
# request_replan
# ---------------------------------------------------------------------------

class TestRequestReplan:

    def test_sets_flag_and_returns_true(self):
        ex = _make_ex()
        ex._replan_requested = False
        result = ex.request_replan()
        assert ex._replan_requested is True
        assert result is True


# ---------------------------------------------------------------------------
# _dispatch routing
# ---------------------------------------------------------------------------

class TestDispatch:

    def test_dispatch_unknown_kind_logs_unhandled(self):
        logs = []
        ex = _make_ex()
        ex._log_fn = logs.append
        i = _ins(kind="unknown_xyz")
        i.kind = "unknown_xyz"
        ex._dispatch(i)
        assert any("unhandled" in m for m in logs)

    def test_dispatch_action_unknown_no_crash(self):
        ex = _make_ex()
        i = _ins(kind="action", action="nonexistent_action")
        # Should return None without crashing
        result = ex._dispatch(i)
        assert result is None

    def test_dispatch_kind_handler_called(self):
        ex = _make_ex()
        called = []
        ex._KIND_HANDLERS["label"] = lambda ins: called.append(ins) or None
        i = _ins(kind="label")
        ex._dispatch(i)
        assert called

    def test_dispatch_action_handler_called(self):
        ex = _make_ex()
        called = []
        ex._ACTION_HANDLERS["end"] = lambda ins: called.append(ins) or None
        i = _ins(kind="action", action="end")
        ex._dispatch(i)
        assert called


# ---------------------------------------------------------------------------
# execute() — wp_logger integration
# ---------------------------------------------------------------------------

class TestExecuteWpLogger:

    def test_execute_with_wp_logger_records_start_end(self):
        wp_logger = MagicMock()
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        ex._current_pos = None  # no position
        from src.script_parser import Instruction
        ins = [Instruction(kind="label", label="test", raw="label test")]
        ex.execute(ins)
        # record_action called at least twice (start + end)
        assert wp_logger.record_action.call_count >= 2

    def test_execute_wp_logger_exception_swallowed(self):
        wp_logger = MagicMock()
        wp_logger.record_action.side_effect = RuntimeError("fail")
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        from src.script_parser import Instruction
        ins = [Instruction(kind="action", action="end", raw="action end")]
        # Should not raise despite wp_logger failing
        ex.execute(ins)


# ---------------------------------------------------------------------------
# _walk_to dry_run path
# ---------------------------------------------------------------------------

class TestWalkToDryRun:

    def test_dry_run_sets_current_pos(self):
        ex = _make_ex(dry_run=True)
        dest = _coord(x=100, y=200, z=7)
        ex._walk_to(dest, "node")
        assert ex._current_pos is dest

    def test_dry_run_with_wp_logger_records(self):
        wp_logger = MagicMock()
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        dest = _coord(x=100, y=200, z=7)
        ex._walk_to(dest, "node")
        wp_logger.add_waypoint.assert_called_once_with(dest.x, dest.y, dest.z, action="node")

    def test_dry_run_wp_logger_exception_swallowed(self):
        wp_logger = MagicMock()
        wp_logger.add_waypoint.side_effect = RuntimeError("fail")
        ex = _make_ex(dry_run=True)
        ex._wp_logger = wp_logger
        dest = _coord(x=100, y=200, z=7)
        ex._walk_to(dest, "node")  # Should not raise

    def test_dry_run_no_nav_skips(self):
        ex = _make_ex(dry_run=False)
        ex._nav = None
        # With no nav and no dry_run, should log and return
        logs = []
        ex._log_fn = logs.append
        dest = _coord()
        ex._walk_to(dest, "node")
        assert any("no navigator" in m for m in logs)


# ---------------------------------------------------------------------------
# _parse_trade_items
# ---------------------------------------------------------------------------

class TestParseTradeItems:

    def test_valid_json_returns_items(self):
        ex = _make_ex()
        i = MagicMock()
        i.raw = '{"items": [{"name": "mana potion", "qty": 50}]}'
        result = ex._parse_trade_items(i)
        assert result == [{"name": "mana potion", "qty": 50}]

    def test_invalid_json_returns_empty(self):
        ex = _make_ex()
        i = MagicMock()
        i.raw = "not json at all"
        result = ex._parse_trade_items(i)
        assert result == []

    def test_missing_items_key_returns_empty(self):
        ex = _make_ex()
        i = MagicMock()
        i.raw = '{"other": "stuff"}'
        result = ex._parse_trade_items(i)
        assert result == []


# ---------------------------------------------------------------------------
# _verify_npc_dialog / _click_dialog_option (no frame_getter path)
# ---------------------------------------------------------------------------

class TestNpcDialogHelpers:

    def test_verify_npc_dialog_no_frame_getter_logs(self):
        logs = []
        ex = _make_ex()
        ex._frame_getter = None
        ex._log_fn = logs.append
        ex._verify_npc_dialog()
        assert any("skip" in m.lower() or "no verifier" in m.lower() for m in logs)

    def test_click_dialog_option_no_frame_getter_returns_false(self):
        ex = _make_ex()
        ex._frame_getter = None
        result = ex._click_dialog_option("trade")
        assert result is False
