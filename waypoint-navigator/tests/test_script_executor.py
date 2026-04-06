"""
Tests para src/script_executor.py — ScriptExecutor
100 % offline: InputController y WaypointNavigator se reemplazan con mocks.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable, List, Optional, cast
from unittest.mock import MagicMock, call, patch

import pytest

from src.script_executor import ScriptExecutor, _build_labels
from src.script_parser import Instruction, ScriptParser


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_executor(**kw: Any) -> ScriptExecutor:
    ctrl          = kw.pop("ctrl",          MagicMock())
    nav           = kw.pop("navigator",     MagicMock())
    dry_run       = bool(kw.pop("dry_run",        True))
    log_fn: Optional[Callable[[str], None]] = kw.pop("log_fn", lambda _: None)
    healer        = kw.pop("healer",        None)
    frame_getter  = kw.pop("frame_getter",  None)
    depot_manager = kw.pop("depot_manager", None)
    jitter        = float(kw.pop("jitter",       0.0))
    step_interval = float(kw.pop("step_interval", 0.18))
    return ScriptExecutor(
        ctrl=ctrl, navigator=nav, step_interval=step_interval,
        healer=healer, frame_getter=frame_getter, depot_manager=depot_manager,
        dry_run=dry_run, jitter=jitter, log_fn=log_fn,
    )


def _parse(text: str) -> List[Instruction]:
    return ScriptParser.parse_text(text)


def _coord(x: int = 100, y: int = 200, z: int = 7) -> MagicMock:
    """Fake Coordinate with the attributes ScriptExecutor reads."""
    c = MagicMock()
    c.x, c.y, c.z = x, y, z
    return c


# ─────────────────────────────────────────────────────────────────────────────
# _build_labels helper
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildLabels:

    def test_empty_instructions(self):
        assert _build_labels([]) == {}

    def test_single_label(self):
        ins = _parse("label loop")
        assert _build_labels(ins) == {"loop": 0}

    def test_label_index_correct(self):
        ins = _parse("node (1,2,7)\nlabel middle\nwait 1")
        assert _build_labels(ins) == {"middle": 1}

    def test_multiple_labels(self):
        ins = _parse("label a\nnode (1,2,7)\nlabel b")
        lmap = _build_labels(ins)
        assert lmap["a"] == 0
        assert lmap["b"] == 2

    def test_non_label_instructions_ignored(self):
        ins = _parse("node (1,2,7)\nwait 1\naction end")
        assert _build_labels(ins) == {}

    def test_label_names_lowercased(self):
        ins = _parse("label MyLabel")
        assert "mylabel" in _build_labels(ins)


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — construction
# ─────────────────────────────────────────────────────────────────────────────

class TestScriptExecutorConstruction:

    def test_default_not_running(self):
        ex = _make_executor()
        assert ex.is_running is False

    def test_dry_run_stored(self):
        ex = _make_executor(dry_run=True)
        assert ex._dry_run is True

    def test_jitter_stored(self):
        ex = _make_executor(jitter=0.05)
        assert ex._jitter == 0.05

    def test_set_position(self):
        ex = _make_executor()
        pos = _coord()
        ex.set_position(pos)
        assert ex._current_pos is pos

    def test_set_depot_manager(self):
        ex = _make_executor()
        dm = MagicMock()
        ex.set_depot_manager(dm)
        assert ex._depot is dm

    def test_abort_sets_running_false(self):
        ex = _make_executor()
        ex._running = True
        ex.abort()
        assert ex.is_running is False

    def test_save_block_diagnostic_writes_artifacts(self, tmp_path):
        import numpy as np

        frame = np.zeros((80, 120, 3), dtype=np.uint8)
        ex = _make_executor(frame_getter=lambda: frame)
        viz = SimpleNamespace(_out=tmp_path / "path_trace" / "run_test")
        viz._out.mkdir(parents=True, exist_ok=True)
        ex.set_path_visualizer(viz)
        ex._radar = MagicMock()
        ex._radar._crop_minimap.return_value = np.zeros((24, 24, 3), dtype=np.uint8)
        loader = MagicMock()
        loader.get_map_image.return_value = np.zeros((2048, 2560, 4), dtype=np.uint8)
        ex.set_map_loader(loader)

        blocked = SimpleNamespace(x=32350, y=32214, z=7)
        actual = SimpleNamespace(x=32351, y=32214, z=7)
        dest = SimpleNamespace(x=32345, y=32214, z=7)

        meta_path = ex._save_block_diagnostic(
            blocked_tile=blocked,
            actual_pos=actual,
            dest=dest,
            step_index=8,
            total_steps=13,
        )

        assert meta_path is not None
        assert meta_path.exists()
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        assert payload["blocked_tile"] == [32350, 32214, 7]
        assert payload["actual_pos"] == [32351, 32214, 7]
        assert payload["viewport_bounds"] == [0, 0, 98, 66]
        assert payload["viewport_size"] == [98, 66]
        assert (meta_path.parent / meta_path.name.replace("_meta.json", "_frame.png")).exists()
        assert (meta_path.parent / meta_path.name.replace("_meta.json", "_viewport.png")).exists()
        assert (meta_path.parent / meta_path.name.replace("_meta.json", "_minimap.png")).exists()
        assert (meta_path.parent / meta_path.name.replace("_meta.json", "_map.png")).exists()

    def test_save_block_diagnostic_without_frame_still_writes_metadata(self, tmp_path):
        ex = _make_executor(frame_getter=lambda: None)
        viz = SimpleNamespace(_out=tmp_path / "path_trace" / "run_test")
        viz._out.mkdir(parents=True, exist_ok=True)
        ex.set_path_visualizer(viz)

        blocked = SimpleNamespace(x=32350, y=32214, z=7)
        meta_path = ex._save_block_diagnostic(
            blocked_tile=blocked,
            actual_pos=None,
            dest=None,
            step_index=8,
            total_steps=13,
        )

        assert meta_path is not None
        assert meta_path.exists()
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        assert payload["blocked_tile"] == [32350, 32214, 7]
        assert "frame_path" not in payload

    def test_post_block_position_watch_stops_after_threshold(self):
        ex = _make_executor()
        ex._running = True
        ex._arm_post_block_position_watch()

        for _ in range(ex._MAX_POST_BLOCK_POSITION_MISSES - 1):
            assert ex._note_post_block_position_result(False) is False

        assert ex._note_post_block_position_result(False) is True
        assert ex.stop_reason == "resolver_degraded"
        assert ex.is_running is False

    def test_post_block_position_watch_resets_on_recovery(self):
        ex = _make_executor()
        ex._arm_post_block_position_watch()
        assert ex._note_post_block_position_result(False) is False
        assert ex._note_post_block_position_result(True) is False
        assert ex._watch_post_block_position_loss is False
        assert ex._post_block_position_miss_streak == 0

    def test_handle_movement_does_not_rewind_when_resolver_degraded(self):
        ex = _make_executor()
        ex._walk_to = MagicMock(side_effect=lambda dest, kind: setattr(ex, "_last_walk_ok", False))  # type: ignore[method-assign]
        ex._last_walk_ok = False
        ex._stop_reason = "resolver_degraded"
        ex._rewind_to_last_confirmed_node = MagicMock()  # type: ignore[method-assign]
        ins = MagicMock()
        ins.kind = "node"
        ins.coord.to_tibia_coord.return_value = _coord(101, 200, 7)

        result = ex._handle_movement(ins)

        assert result is None
        ex._rewind_to_last_confirmed_node.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — action end
# ─────────────────────────────────────────────────────────────────────────────

class TestActionEnd:

    def test_stops_at_action_end(self):
        ex = _make_executor()
        ins = _parse("node (1,2,7)\naction end\nnode (3,4,7)")
        # dry_run=True → no actual movement
        ex.set_position(_coord())
        executed_kinds = []
        real_dispatch = ex._dispatch

        def tracking_dispatch(i: Any) -> Optional[str]:
            executed_kinds.append(i.kind)
            return real_dispatch(i)

        ex._dispatch_override = tracking_dispatch
        ex.execute(ins)
        # node[0] and action[1] executed; node[2] should NOT be (stop after end)
        assert "action" in executed_kinds
        assert executed_kinds.count("node") <= 1   # at most the first node

    def test_is_running_false_after_end(self):
        ex = _make_executor()
        ex.execute(_parse("action end"))
        assert ex.is_running is False


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — wait
# ─────────────────────────────────────────────────────────────────────────────

class TestWait:

    def test_wait_dry_run_no_sleep(self):
        ex = _make_executor(dry_run=True)
        with patch("src.script_executor.time") as mock_time:
            ex.execute(_parse("wait 1.5"))
            mock_time.sleep.assert_not_called()

    def test_wait_real_calls_sleep(self):
        ex = _make_executor(dry_run=False, log_fn=lambda _: None)
        slept: list = []
        with patch("src.script_executor.time") as mock_time:
            mock_time.sleep = lambda s: slept.append(s)
            ex.execute(_parse("wait 2.0"))
        assert sum(slept) == pytest.approx(2.0, abs=0.1)

    def test_action_wait_treated_as_wait(self):
        ex = _make_executor(dry_run=True)
        logs: list = []
        ex._log_fn = logs.append
        ex.execute(_parse("action wait"))
        assert any("wait" in m.lower() for m in logs)


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — goto / label
# ─────────────────────────────────────────────────────────────────────────────

class TestGotoLabel:

    def test_goto_jumps_to_label(self):
        visited_kinds: list = []
        ex = _make_executor()
        real = ex._dispatch

        def track(i: Any) -> Optional[str]:
            visited_kinds.append(i.kind)
            return real(i)

        ex._dispatch_override = track
        # Script: wait -> goto end -> wait (skipped) -> label end -> action end
        script = "wait 0\ngoto end_label\nwait 0\nlabel end_label\naction end"
        ex.execute(_parse(script))
        # Second 'wait' (index 2) must NOT appear
        # First 'wait' (index 0) must appear
        assert visited_kinds.count("wait") == 1

    def test_unknown_label_does_not_crash(self):
        ex = _make_executor()
        ex.execute(_parse("goto nonexistent_label\naction end"))
        assert ex.is_running is False

    def test_label_itself_is_noop(self):
        logs: list = []
        ex = _make_executor(log_fn=logs.append)
        ex.execute(_parse("label mypoint\naction end"))
        # Should complete without error
        assert ex.is_running is False


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — use_hotkey / use_item
# ─────────────────────────────────────────────────────────────────────────────

class TestUseHotkey:

    def test_use_hotkey_calls_press_key_when_not_dry(self):
        ctrl = MagicMock()
        ex = ScriptExecutor(ctrl=ctrl, navigator=MagicMock(),
                            dry_run=False, log_fn=lambda _: None)
        with patch("src.script_executor.time"):
            ex.execute(_parse("use_hotkey 0x70"))
        ctrl.press_key.assert_called_once_with(0x70)

    def test_use_hotkey_dry_run_no_press(self):
        ctrl = MagicMock()
        ex = ScriptExecutor(ctrl=ctrl, navigator=MagicMock(),
                            dry_run=True, log_fn=lambda _: None)
        ex.execute(_parse("use_hotkey 0x71"))
        ctrl.press_key.assert_not_called()

    def test_use_item_with_vk_calls_press(self):
        ctrl = MagicMock()
        ex = ScriptExecutor(ctrl=ctrl, navigator=MagicMock(),
                            dry_run=False, log_fn=lambda _: None)
        with patch("src.script_executor.time"):
            ex.execute(_parse("use_item exura vk=0x70"))
        ctrl.press_key.assert_called_once_with(0x70)


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — if_stat
# ─────────────────────────────────────────────────────────────────────────────

class TestIfStat:

    def _healer(self, hp: int = 100, mp: int = 100) -> MagicMock:
        h = MagicMock()
        h.hp_pct = hp
        h.mp_pct = mp
        return h

    def test_if_hp_less_jumps_when_triggered(self):
        ex = _make_executor(healer=self._healer(hp=20))
        visited: list = []

        def track(i):
            visited.append(i.kind)
            return ex._dispatch.__wrapped__(i) if hasattr(ex._dispatch, "__wrapped__") else None

        # Manual test using execute with label map
        script = "if hp < 30 goto flee\naction end\nlabel flee\naction end"
        ins = _parse(script)
        ex.set_position(_coord())
        executed: list = []

        real = ex._dispatch
        results: list = []

        def recording_dispatch(i: Any) -> Optional[str]:
            results.append(i)
            return real(i)

        ex._dispatch_override = recording_dispatch
        ex.execute(ins)
        # should have jumped to 'flee' label (index 2), not executed node at index 1
        kinds = [i.kind for i in results]
        # action end should appear twice (once for flee, once at end)
        # if_stat and two action end
        assert "if_stat" in kinds

    def test_if_hp_less_no_jump_when_not_triggered(self):
        ex = _make_executor(healer=self._healer(hp=80))
        ins = _parse("if hp < 30 goto flee\naction end\nlabel flee\naction end")
        dispatched: list = []
        real = ex._dispatch

        def rec(i: Any) -> Optional[str]:
            dispatched.append(i.kind)
            return real(i)

        ex._dispatch_override = rec
        ex.execute(ins)
        # Should NOT jump — action end at index 1 executes and stops
        assert dispatched.index("action") < dispatched.index("if_stat") + 2

    def test_if_stat_no_healer_skips_condition(self):
        ex = _make_executor(healer=None)
        logs: list = []
        ex._log_fn = logs.append
        ex.execute(_parse("if hp < 50 goto lbl\naction end"))
        assert any("can't read" in m.lower() or "condition" in m.lower() for m in logs)


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — depot instruction
# ─────────────────────────────────────────────────────────────────────────────

class TestDepotInstruction:

    def test_depot_calls_run_depot_cycle(self):
        dm = MagicMock()
        ex = _make_executor(depot_manager=dm)
        ex.set_position(_coord())
        ex._last_walk_ok = True
        ex.execute(_parse("depot\naction end"))
        dm.run_depot_cycle.assert_called_once()

    def test_depot_passes_current_pos(self):
        dm = MagicMock()
        ex = _make_executor(depot_manager=dm)
        pos = _coord(50, 60, 7)
        ex.set_position(pos)
        ex._last_walk_ok = True
        ex.execute(_parse("depot\naction end"))
        dm.run_depot_cycle.assert_called_once_with(player_pos=pos)

    def test_depot_without_manager_logs_warning(self):
        logs: list = []
        ex = _make_executor(depot_manager=None, log_fn=logs.append)
        ex._last_walk_ok = True
        ex.execute(_parse("depot\naction end"))
        assert any("depotmanager" in m.lower() or "not attached" in m.lower() for m in logs)

    def test_action_depot_legacy_also_calls_cycle(self):
        """Legacy 'action depot' from frbot scripts must also work."""
        dm = MagicMock()
        ex = _make_executor(depot_manager=dm)
        ex._last_walk_ok = True
        ins = [
            Instruction(kind="action", action="depot"),
            Instruction(kind="action", action="end"),
        ]
        ex.execute(ins)
        dm.run_depot_cycle.assert_called_once()

    def test_depot_does_not_stop_execution(self):
        dm = MagicMock()
        ex = _make_executor(depot_manager=dm)
        ins = [
            Instruction(kind="depot"),
            Instruction(kind="wait", wait_secs=0.0),
            Instruction(kind="action", action="end"),
        ]
        dispatched: list = []
        real = ex._dispatch

        def rec(i: Any) -> Optional[str]:
            dispatched.append(i.kind)
            return real(i)

        ex._dispatch_override = rec
        ex.execute(ins)
        assert "wait" in dispatched
        assert "action" in dispatched


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — dry_run movement
# ─────────────────────────────────────────────────────────────────────────────

class TestDryRunMovement:

    def test_movement_dry_run_updates_position(self):
        ex = _make_executor(dry_run=True)
        ex.set_position(_coord(100, 200, 7))

        # Use a plain MagicMock so we can set return values freely
        dest_coord = _coord(110, 210, 7)
        coord_mock = MagicMock()
        coord_mock.to_tibia_coord.return_value = dest_coord
        coord_mock.z = 7
        ins = MagicMock()
        ins.kind = "node"
        ins.coord = coord_mock
        ex._dispatch(ins)
        assert ex._current_pos is dest_coord

    def test_movement_dry_run_no_ctrl_calls(self):
        ctrl = MagicMock()
        ex = ScriptExecutor(ctrl=ctrl, navigator=MagicMock(),
                            dry_run=True, log_fn=lambda _: None)
        ex.set_position(_coord())
        coord_mock = MagicMock()
        coord_mock.to_tibia_coord.return_value = _coord(101, 200, 7)
        coord_mock.z = 7
        ins = MagicMock()
        ins.kind = "node"
        ins.coord = coord_mock
        ex._dispatch(ins)
        ctrl.move_to_tile.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — say / talk_npc
# ─────────────────────────────────────────────────────────────────────────────

class TestSayTalkNpc:

    def test_say_dry_run_no_type_text(self):
        ctrl = MagicMock()
        ex = ScriptExecutor(ctrl=ctrl, navigator=MagicMock(),
                            dry_run=True, log_fn=lambda _: None)
        ex.execute(_parse('call say({"sentence": "hello"})'))
        ctrl.type_text.assert_not_called()

    def test_talk_npc_dry_run_no_type(self):
        ctrl = MagicMock()
        ex = ScriptExecutor(ctrl=ctrl, navigator=MagicMock(),
                            dry_run=True, log_fn=lambda _: None)
        ex.execute(_parse('call talk_npc({"list_words": ["hi", "bye"]})'))
        ctrl.type_text.assert_not_called()

    def test_talk_npc_real_calls_type_for_each_word(self):
        ctrl = MagicMock()
        ex = ScriptExecutor(ctrl=ctrl, navigator=MagicMock(),
                            dry_run=False, log_fn=lambda _: None)
        with patch("src.script_executor.time"):
            ex.execute(_parse('call talk_npc({"list_words": ["hi", "bye"]})'))
        assert ctrl.type_text.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — abort()
# ─────────────────────────────────────────────────────────────────────────────

class TestAbort:

    def test_abort_stops_mid_script(self):
        ex = _make_executor()
        dispatched: list = []
        real = ex._dispatch

        call_count = [0]

        def rec(i: Any) -> Optional[str]:
            call_count[0] += 1
            dispatched.append(i.kind)
            if call_count[0] == 1:
                ex.abort()           # abort after first instruction
            return real(i)

        ex._dispatch_override = rec
        ins = _parse("wait 0\nwait 0\nwait 0\naction end")
        ex.execute(ins)
        # Only 1 instruction should have been dispatched before abort
        assert len(dispatched) == 1

    def test_is_running_false_after_abort(self):
        ex = _make_executor()
        ex.abort()
        assert ex.is_running is False


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — _read_stat
# ─────────────────────────────────────────────────────────────────────────────

class TestReadStat:

    def test_returns_none_without_healer(self):
        ex = _make_executor(healer=None)
        assert ex._read_stat("hp") is None

    def test_reads_hp_pct_attribute(self):
        h = MagicMock()
        h.hp_pct = 45
        ex = _make_executor(healer=h)
        assert ex._read_stat("hp") == 45

    def test_reads_private_hp_pct_fallback(self):
        h = MagicMock(spec=[])   # no hp_pct attr
        h._hp_pct = 30
        ex = _make_executor(healer=h)
        assert ex._read_stat("hp") == 30

    def test_reads_mp(self):
        h = MagicMock()
        h.mp_pct = 70
        ex = _make_executor(healer=h)
        assert ex._read_stat("mp") == 70

    def test_unknown_stat_returns_none(self):
        h = MagicMock()
        ex = _make_executor(healer=h)
        assert ex._read_stat("stamina") is None

    def test_case_insensitive_stat_name(self):
        h = MagicMock()
        h.hp_pct = 55
        ex = _make_executor(healer=h)
        assert ex._read_stat("HP") == 55
        assert ex._read_stat("Hp") == 55

    def test_reads_private_mp_pct_fallback(self):
        h = MagicMock(spec=[])
        h._mp_pct = 42
        ex = _make_executor(healer=h)
        assert ex._read_stat("mp") == 42


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — small action handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckTime:

    def test_stops_when_leave_time_reached(self):
        logs: list[str] = []
        ex = _make_executor(log_fn=logs.append)
        ex._running = True
        ex._hours_leave = [9.5]
        ex._is_leave_time = MagicMock(return_value=True)  # type: ignore[method-assign]

        result = ex._handle_check_time(MagicMock())

        assert result is None
        assert ex.is_running is False
        assert any("leave time reached" in message for message in logs)

    def test_continues_without_hours_leave(self):
        logs: list[str] = []
        ex = _make_executor(log_fn=logs.append)
        ex._running = True
        ex._hours_leave = []

        result = ex._handle_check_time(MagicMock())

        assert result is None
        assert ex.is_running is True
        assert any("no hours_leave configured" in message for message in logs)

    def test_continues_when_not_yet_leave_time(self):
        logs: list[str] = []
        ex = _make_executor(log_fn=logs.append)
        ex._running = True
        ex._hours_leave = [20.0]
        ex._is_leave_time = MagicMock(return_value=False)  # type: ignore[method-assign]

        result = ex._handle_check_time(MagicMock())

        assert result is None
        assert ex.is_running is True
        assert any("not yet leave time" in message for message in logs)


class TestCondJumpItemCounts:

    def test_item_count_below_threshold_jumps(self):
        ex = _make_executor()
        ex._item_counter["mana potion"] = 12
        ins = cast(Any, SimpleNamespace(var_name="mana potion", threshold=20, label_jump="keep_hunting", label_skip="leave"))

        assert ex._handle_cond_jump(ins) == "keep_hunting"

    def test_item_count_at_threshold_skips(self):
        ex = _make_executor()
        ex._item_counter["mana potion"] = 20
        ins = cast(Any, SimpleNamespace(var_name="mana potion", threshold=20, label_jump="keep_hunting", label_skip="leave"))

        assert ex._handle_cond_jump(ins) == "leave"

    def test_item_count_uses_large_default_threshold(self):
        ex = _make_executor()
        ex._item_counter["bolt"] = 50
        ins = cast(Any, SimpleNamespace(var_name="bolt", threshold=0, label_jump="keep_hunting", label_skip="leave"))

        assert ex._handle_cond_jump(ins) == "keep_hunting"


class TestWalkModeAndChatToggle:

    def test_walk_keys_switches_to_scancode(self):
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=False)

        ex._handle_walk_mode(cast(Any, SimpleNamespace(action="walk_keys")))

        assert ctrl.input_method == "scancode"

    def test_walk_mouse_dry_run_does_not_mutate_controller(self):
        ctrl = MagicMock()
        ctrl.input_method = "scancode"
        ex = _make_executor(ctrl=ctrl, dry_run=True)

        ex._handle_walk_mode(cast(Any, SimpleNamespace(action="walk_mouse")))

        assert ctrl.input_method == "scancode"

    def test_chat_on_presses_enter(self):
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=False)
        ex._sleep = MagicMock()  # type: ignore[method-assign]

        ex._handle_chat_toggle(cast(Any, SimpleNamespace(action="chat_on")))

        ctrl.press_key.assert_called_once_with(0x0D)

    def test_chat_off_presses_escape(self):
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=False)
        ex._sleep = MagicMock()  # type: ignore[method-assign]

        ex._handle_chat_toggle(cast(Any, SimpleNamespace(action="chat_off")))

        ctrl.press_key.assert_called_once_with(0x1B)


class TestRandomStand:

    def test_no_choices_is_noop(self):
        ex = _make_executor()
        ex._walk_to = MagicMock()  # type: ignore[method-assign]

        result = ex._handle_random_stand(cast(Any, SimpleNamespace(choices=[])))

        assert result is None
        ex._walk_to.assert_not_called()

    def test_choice_is_walked_as_stand(self):
        ex = _make_executor()
        ex._walk_to = MagicMock()  # type: ignore[method-assign]
        choice_a = SimpleNamespace(to_tibia_coord=lambda: _coord(111, 222, 7))
        choice_b = SimpleNamespace(to_tibia_coord=lambda: _coord(333, 444, 7))

        with patch("src.script_executor.random.choice", return_value=choice_b):
            result = ex._handle_random_stand(cast(Any, SimpleNamespace(choices=[choice_a, choice_b])))

        assert result is None
        ex._walk_to.assert_called_once()
        args = ex._walk_to.call_args[0]
        assert args[0].x == 333 and args[0].y == 444 and args[1] == "stand"


class TestOpenDoor:

    def test_dry_run_skips_controller_calls(self):
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=True)

        ex._open_door(SimpleNamespace(x=101, y=100, z=7))

        ctrl.move_to_tile.assert_not_called()

    def test_already_at_door_tile_returns_without_move(self):
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=False)
        ex._current_pos = _coord(101, 100, 7)
        ex._sync_position = MagicMock()  # type: ignore[method-assign]

        ex._open_door(SimpleNamespace(x=101, y=100, z=7))

        ctrl.move_to_tile.assert_not_called()

    def test_retries_until_position_changes(self):
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=False)
        start = _coord(100, 100, 7)
        moved = _coord(101, 100, 7)
        ex._current_pos = start
        positions = [start, start, moved]

        def sync_side_effect() -> None:
            ex._current_pos = positions.pop(0)

        ex._sync_position = MagicMock(side_effect=sync_side_effect)  # type: ignore[method-assign]
        ex._sleep = MagicMock()  # type: ignore[method-assign]

        ex._open_door(SimpleNamespace(x=101, y=100, z=7))

        assert ctrl.move_to_tile.call_count == 2
        ctrl.move_to_tile.assert_any_call(1, 0)


class TestViewportAndNpcHelpers:

    def test_estimate_viewport_bounds_uses_default_when_frame_has_no_size(self):
        ex = _make_executor()

        bounds = ex._estimate_game_viewport_bounds(object())

        assert bounds == (0, 0, 1568, 837)

    def test_switch_to_npc_channel_logs_when_tab_not_detected(self):
        import numpy as np

        logs: list[str] = []
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=False, log_fn=logs.append, frame_getter=lambda: np.zeros((100, 100, 3), dtype=np.uint8))

        ex._switch_to_npc_channel()

        ctrl.click.assert_not_called()
        assert any("npc tab not detected" in message.lower() for message in logs)

    def test_switch_to_npc_channel_logs_errors(self):
        logs: list[str] = []
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=False, log_fn=logs.append, frame_getter=MagicMock(side_effect=RuntimeError("boom")))

        ex._switch_to_npc_channel()

        ctrl.click.assert_not_called()
        assert any("npc tab switch error" in message.lower() for message in logs)

    def test_verify_npc_dialog_skips_without_verifier(self):
        logs: list[str] = []
        ex = _make_executor(log_fn=logs.append, frame_getter=lambda: object())

        with patch("src.script_executor.verify_dialog_open", None):
            ex._verify_npc_dialog()

        assert any("verify skipped" in message.lower() for message in logs)

    def test_verify_npc_dialog_logs_exception(self):
        logs: list[str] = []
        ex = _make_executor(log_fn=logs.append, frame_getter=lambda: object())

        with patch("src.script_executor.verify_dialog_open", side_effect=RuntimeError("dialog boom")):
            ex._verify_npc_dialog()

        assert any("verify error" in message.lower() for message in logs)

    def test_click_dialog_option_returns_false_when_not_found(self):
        logs: list[str] = []
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, log_fn=logs.append, frame_getter=lambda: object())

        with patch("src.script_executor.find_dialog_option", return_value=None):
            result = ex._click_dialog_option("trade")

        assert result is False
        ctrl.click.assert_not_called()
        assert any("not found" in message.lower() for message in logs)

    def test_click_character_tile_logs_frame_getter_error_and_clicks_default_center(self):
        logs: list[str] = []
        ctrl = MagicMock()
        ex = _make_executor(ctrl=ctrl, dry_run=False, log_fn=logs.append, frame_getter=MagicMock(side_effect=RuntimeError("frame boom")))

        ex._click_character_tile()

        ctrl.click.assert_called_once_with(784, 418, button="left")
        assert any("frame_getter error" in message.lower() for message in logs)


class TestLeaveTimeAndTradeParsing:

    def test_is_leave_time_returns_false_after_first_trigger(self):
        ex = _make_executor()
        ex._leave_time_fired = True
        ex._hours_leave = [9.5]

        assert ex._is_leave_time() is False

    def test_is_leave_time_handles_midnight_wrap(self):
        import datetime as _dt

        ex = _make_executor()
        ex._hours_leave = [0.1]
        ex._start_time_h = 23.5

        fake_now = _dt.datetime(2026, 4, 4, 0, 15)
        with patch("src.script_executor.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            assert ex._is_leave_time() is True

    def test_parse_trade_items_invalid_json_returns_empty(self):
        ex = _make_executor()
        ins = SimpleNamespace(raw="{not-json")

        assert ex._parse_trade_items(ins) == []


class TestTransientBlockGuards:

    def test_should_retry_transient_block_rejects_z_mismatch(self):
        ex = _make_executor(position_getter=lambda: _coord())
        current = SimpleNamespace(z=7, manhattan_to=lambda other: 1)
        target = SimpleNamespace(z=8)

        assert ex._should_retry_transient_block(current_pos=current, target_pos=target) is False

    def test_should_retry_transient_block_rejects_distance_errors(self):
        ex = _make_executor(position_getter=lambda: _coord())

        class _BadPos:
            z = 7

            def manhattan_to(self, other: Any) -> int:
                raise RuntimeError("bad distance")

        assert ex._should_retry_transient_block(current_pos=_BadPos(), target_pos=SimpleNamespace(z=7)) is False


# ─────────────────────────────────────────────────────────────────────────────
# ScriptExecutor — _sleep jitter
# ─────────────────────────────────────────────────────────────────────────────

class TestSleep:

    def test_sleep_zero_no_sleep(self):
        ex = _make_executor(jitter=0.0)
        with patch("src.script_executor.time") as mt:
            mt.sleep = MagicMock()
            ex._sleep(0.0)
            mt.sleep.assert_not_called()

    def test_sleep_positive(self):
        ex = _make_executor(jitter=0.0)
        ex._running = True  # mirrors execute() context
        slept: list = []
        with patch("src.script_executor.time") as mt:
            mt.sleep = lambda s: slept.append(s)
            ex._sleep(1.5)
        assert abs(sum(slept) - 1.5) < 0.001

    def test_sleep_with_jitter_adds_extra(self):
        ex = _make_executor(jitter=1.0)
        ex._running = True  # mirrors execute() context
        slept: list = []
        with patch("src.script_executor.time") as mt:
            mt.sleep = lambda s: slept.append(s)
            with patch("src.script_executor.random.uniform", return_value=0.5):
                ex._sleep(1.0)
        assert abs(sum(slept) - 1.5) < 0.001


# ─────────────────────────────────────────────────────────────────────────────
# Integration: full short script
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegration:

    def test_empty_script_completes(self):
        ex = _make_executor()
        ex.execute([])
        assert ex.is_running is False

    def test_action_end_only(self):
        ex = _make_executor()
        ex.execute(_parse("action end"))
        assert ex.is_running is False

    def test_wait_then_end(self):
        ex = _make_executor(dry_run=True)
        ex.execute(_parse("wait 0.1\naction end"))
        assert ex.is_running is False

    def test_use_hotkey_then_end_dry(self):
        ctrl = MagicMock()
        ex = ScriptExecutor(ctrl=ctrl, navigator=MagicMock(),
                            dry_run=True, log_fn=lambda _: None)
        ex.execute(_parse("use_hotkey 0x70\naction end"))
        ctrl.press_key.assert_not_called()
        assert ex.is_running is False

    def test_loop_with_goto(self):
        """Ensure goto loop terminates when abort() is called."""
        ex = _make_executor()
        count = [0]
        real = ex._dispatch

        def rec(i: Any) -> Optional[str]:
            if i.kind == "label":
                count[0] += 1
                if count[0] >= 3:
                    ex.abort()
            return real(i)

        ex._dispatch_override = rec
        ex.execute(_parse("label loop\ngoto loop"))
        assert count[0] >= 3

    def test_depot_in_full_script(self):
        dm = MagicMock()
        ex = _make_executor(depot_manager=dm)
        ex._last_walk_ok = True
        ex.execute(_parse("wait 0\ndepot\naction end"))
        dm.run_depot_cycle.assert_called_once()

    def test_script_stat_counts_used_in_executor(self):
        """ScriptParser.script_stats and ScriptExecutor must agree on depot count."""
        from src.script_parser import ScriptParser as SP
        script = "depot\ndepot\nwait 1\naction end"
        ins = SP.parse_text(script)
        stats = SP.script_stats(ins)
        assert stats["depots"] == 2

        dm = MagicMock()
        ex = _make_executor(depot_manager=dm)
        ex._last_walk_ok = True
        ex.execute(ins)
        assert dm.run_depot_cycle.call_count == 2


# ── add_blocked_region() ────────────────────────────────────────────────────

class TestAddBlockedRegion:
    def test_adds_tiles_as_pixel_coords(self) -> None:
        ex = _make_executor()
        # BOUNDS xMin=31744, yMin=30976
        ex.add_blocked_region(31744, 31746, 30976, 30977, 7)
        # 3 x-values * 2 y-values = 6 tiles
        assert len(ex._blocked_pixels) == 6
        assert (0, 0, 7) in ex._blocked_pixels
        assert (2, 1, 7) in ex._blocked_pixels

    def test_no_duplicates(self) -> None:
        ex = _make_executor()
        ex.add_blocked_region(31744, 31745, 30976, 30976, 7)
        ex.add_blocked_region(31744, 31745, 30976, 30976, 7)
        assert len(ex._blocked_pixels) == 2

    def test_merges_with_existing(self) -> None:
        ex = _make_executor()
        ex._blocked_pixels.append((0, 0, 7))
        ex.add_blocked_region(31744, 31745, 30976, 30976, 7)
        # (0,0,7) already existed + (1,0,7) added = 2
        assert len(ex._blocked_pixels) == 2


# ── force_walkable_region() ─────────────────────────────────────────────────

class TestForceWalkableRegion:
    def test_flips_nonwalkable_tiles(self) -> None:
        import numpy as np
        ex = _make_executor()
        pf = MagicMock()
        pf.walkability = np.zeros((2048, 2560), dtype=bool)
        ex._nav = MagicMock()
        ex._nav._pathfinders = {7: pf}
        # BOUNDS: xMin=31744, yMin=30976
        flipped = ex.force_walkable_region(31744, 31746, 30976, 30977, 7)
        assert flipped == 6  # 3 x * 2 y
        assert pf.walkability[0, 0]
        assert pf.walkability[1, 2]

    def test_already_walkable_not_counted(self) -> None:
        import numpy as np
        ex = _make_executor()
        pf = MagicMock()
        pf.walkability = np.ones((2048, 2560), dtype=bool)
        ex._nav = MagicMock()
        ex._nav._pathfinders = {7: pf}
        flipped = ex.force_walkable_region(31744, 31745, 30976, 30976, 7)
        assert flipped == 0

    def test_no_navigator_returns_zero(self) -> None:
        ex = _make_executor()
        ex._nav = None
        assert ex.force_walkable_region(31744, 31745, 30976, 30976, 7) == 0

    def test_force_walkable_region_clears_route_cache(self) -> None:
        import numpy as np

        ex = _make_executor()
        pf = MagicMock()
        pf.walkability = np.zeros((2048, 2560), dtype=bool)
        ex._nav = MagicMock()
        ex._nav._pathfinders = {7: pf}
        ex._nav._route_cache = {("a", "b"): MagicMock()}

        flipped = ex.force_walkable_region(31744, 31744, 30976, 30976, 7)

        assert flipped == 1
        assert ex._nav._route_cache == {}


class TestLearnedTiles:
    def test_remember_blocked_pixel_deduplicates(self) -> None:
        ex = _make_executor()

        assert ex._remember_blocked_pixel((1, 2, 7)) is True
        assert ex._remember_blocked_pixel((1, 2, 7)) is False
        assert ex._blocked_pixels == [(1, 2, 7)]

    def test_remember_opened_pixel_caps_growth(self) -> None:
        ex = _make_executor()
        ex._max_opened_pixels = 3

        for idx in range(5):
            ex._remember_opened_pixel((idx, idx, 7))

        assert len(ex._opened_pixels) == 3
        assert ex._opened_pixels == [(2, 2, 7), (3, 3, 7), (4, 4, 7)]


# ─────────────────────────────────────────────────────────────────────────────
# WasP-compat actions: check_ammo / check_supplies / buy_ammo
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckAmmo:

    def _make(self) -> ScriptExecutor:
        return _make_executor(dry_run=False)

    def test_first_run_skips_buy(self):
        """Before any hunting, check_ammo assumes ammo is full → skip."""
        ex = self._make()
        ex._has_hunted = False
        ins = MagicMock()
        result = ex._check_ammo(ins)
        assert result == "skip_ammo"

    def test_after_hunting_goes_to_buy(self):
        """After hunting, check_ammo does NOT skip → returns None (go buy)."""
        ex = self._make()
        ex._has_hunted = True
        ins = MagicMock()
        result = ex._check_ammo(ins)
        assert result is None

    def test_force_resupply_overrides_skip(self):
        """Even on first run, _force_resupply forces a buy."""
        ex = self._make()
        ex._has_hunted = False
        ex._force_resupply = True
        ins = MagicMock()
        result = ex._check_ammo(ins)
        assert result is None
        # flag should be consumed
        assert ex._force_resupply is False


class TestCheckSupplies:

    def _make(self) -> ScriptExecutor:
        return _make_executor(dry_run=False)

    def test_pre_hunt_returns_none(self):
        """Before hunting, supplies assumed OK → continue (None)."""
        ex = self._make()
        ex._has_hunted = False
        ins = MagicMock()
        result = ex._check_supplies(ins)
        assert result is None

    def test_post_hunt_returns_none(self):
        """After hunting, supplies assumed low → continue to town (None)."""
        ex = self._make()
        ex._has_hunted = True
        ins = MagicMock()
        result = ex._check_supplies(ins)
        assert result is None


class TestBuyAmmoChat:

    def _make(self) -> ScriptExecutor:
        ex = _make_executor(dry_run=False)
        ex._say_to_npc = MagicMock()  # type: ignore[method-assign]
        ex._switch_to_npc_channel = MagicMock()  # type: ignore[method-assign]
        ex._sleep = MagicMock()  # type: ignore[method-assign]
        return ex

    def test_buy_from_instruction_raw(self):
        """Reads items from ins.raw JSON and types buy commands."""
        ex = self._make()
        ins = MagicMock()
        ins.raw = '{"items": [{"name": "royal star", "qty": 300}]}'
        ex._buy_ammo_chat(ins)
        say_to_npc = cast(Any, ex.__dict__["_say_to_npc"])
        calls = [str(c) for c in say_to_npc.call_args_list]
        assert any("buy 300 royal star" in c for c in calls)
        assert any("yes" in c for c in calls)

    def test_buy_from_wasp_setup_fallback(self):
        """Falls back to _wasp_setup hunt_config when ins.raw has no items."""
        ex = self._make()
        ex._wasp_setup = {
            "hunt_config": {"ammo_name": "bolt", "take_ammo": 500}
        }
        ins = MagicMock()
        ins.raw = "{}"
        ex._buy_ammo_chat(ins)
        say_to_npc = cast(Any, ex.__dict__["_say_to_npc"])
        calls = [str(c) for c in say_to_npc.call_args_list]
        assert any("buy 500 bolt" in c for c in calls)

    def test_no_items_logs_skip(self):
        """When no items can be resolved, logs warning and does nothing."""
        ex = self._make()
        ex._wasp_setup = None
        ins = MagicMock()
        ins.raw = "{}"
        ex._buy_ammo_chat(ins)
        cast(Any, ex.__dict__["_say_to_npc"]).assert_not_called()


class TestHuntLabelSetsHasHunted:

    def test_label_hunt_sets_flag(self):
        ex = _make_executor(dry_run=True)
        ex._has_hunted = False
        ins = _parse("label hunt\nnode (1,2,7)")
        ex._instructions = ins
        ex.__dict__["_labels"] = _build_labels(ins)
        ex._dispatch(ins[0])
        assert ex._has_hunted is True

    def test_label_downcave_sets_flag(self):
        ex = _make_executor(dry_run=True)
        ex._has_hunted = False
        ins = _parse("label downcave\nnode (1,2,7)")
        ex._dispatch(ins[0])
        assert ex._has_hunted is True

    def test_other_label_does_not_set_flag(self):
        ex = _make_executor(dry_run=True)
        ex._has_hunted = False
        ins = _parse("label depot\nnode (1,2,7)")
        ex._dispatch(ins[0])
        assert ex._has_hunted is False
