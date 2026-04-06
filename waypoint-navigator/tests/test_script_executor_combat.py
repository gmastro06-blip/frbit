"""
Tests for ScriptExecutor ↔ CombatManager integration.

Covers:
  - combat_manager parameter in __init__
  - set_combat_manager() hot-swap
  - action combat_pause  → combat.pause()
  - action combat_resume → combat.resume()
  - action combat_start  → combat.start()
  - action combat_stop   → combat.stop()
  - no combat_manager attached → warning logged, no raise
  - dry_run=True → log but don't call method
  - exception inside method → swallowed, warning logged
  - multiple combat actions in one script
  - BotSession.run_script() passes combat_manager to executor
  - ScriptParser parses action combat_* correctly
"""
from __future__ import annotations

from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from src.script_executor import ScriptExecutor
from src.script_parser import Instruction, ScriptParser


# ─────────────────────────────────────────────────────────────────────────────
# Factories
# ─────────────────────────────────────────────────────────────────────────────

def _mock_ctrl() -> MagicMock:
    return MagicMock()


def _mock_nav() -> MagicMock:
    return MagicMock()


def _mock_combat() -> MagicMock:
    cm = MagicMock()
    cm.pause  = MagicMock()
    cm.resume = MagicMock()
    cm.start  = MagicMock()
    cm.stop   = MagicMock()
    return cm


def _make_executor(combat_manager=None, dry_run=False) -> ScriptExecutor:
    logs: List[str] = []
    ex = ScriptExecutor(
        ctrl=_mock_ctrl(),
        navigator=_mock_nav(),
        combat_manager=combat_manager,
        dry_run=dry_run,
        log_fn=logs.append,
    )
    ex._logs = logs  # type: ignore[attr-defined]
    return ex


def _action_ins(action: str) -> Instruction:
    return Instruction(kind="action", action=action, raw=f"action {action}")


# ─────────────────────────────────────────────────────────────────────────────
# __init__ — combat_manager parameter
# ─────────────────────────────────────────────────────────────────────────────

class TestScriptExecutorCombatInit:

    def test_default_combat_manager_is_none(self):
        ex = ScriptExecutor(ctrl=_mock_ctrl(), navigator=_mock_nav())
        assert ex._combat is None

    def test_combat_manager_assigned(self):
        cm = _mock_combat()
        ex = ScriptExecutor(ctrl=_mock_ctrl(), navigator=_mock_nav(), combat_manager=cm)
        assert ex._combat is cm

    def test_set_combat_manager_replaces(self):
        ex = _make_executor()
        cm = _mock_combat()
        ex.set_combat_manager(cm)
        assert ex._combat is cm

    def test_set_combat_manager_hot_swap(self):
        cm1 = _mock_combat()
        cm2 = _mock_combat()
        ex = _make_executor(combat_manager=cm1)
        ex.set_combat_manager(cm2)
        assert ex._combat is cm2
        assert ex._combat is not cm1

    def test_set_combat_manager_to_none(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm)
        ex.set_combat_manager(None)
        assert ex._combat is None


# ─────────────────────────────────────────────────────────────────────────────
# action combat_pause
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatPause:

    def test_pause_called(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm)
        ex.execute([_action_ins("combat_pause")])
        cm.pause.assert_called_once()

    def test_pause_not_called_when_dry_run(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm, dry_run=True)
        ex.execute([_action_ins("combat_pause")])
        cm.pause.assert_not_called()

    def test_pause_dry_run_logs_action(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm, dry_run=True)
        ex.execute([_action_ins("combat_pause")])
        assert any("combat_pause" in m for m in ex._logs)  # type: ignore[attr-defined]

    def test_pause_no_combat_logs_warning(self):
        ex = _make_executor(combat_manager=None)
        ex.execute([_action_ins("combat_pause")])   # must not raise
        assert any("CombatManager not attached" in m for m in ex._logs)  # type: ignore[attr-defined]

    def test_pause_exception_swallowed(self):
        cm = _mock_combat()
        cm.pause.side_effect = RuntimeError("boom")
        ex = _make_executor(combat_manager=cm)
        ex.execute([_action_ins("combat_pause")])   # must not propagate
        assert any("raised" in m for m in ex._logs)  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# action combat_resume
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatResume:

    def test_resume_called(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm)
        ex.execute([_action_ins("combat_resume")])
        cm.resume.assert_called_once()

    def test_resume_not_called_when_dry_run(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm, dry_run=True)
        ex.execute([_action_ins("combat_resume")])
        cm.resume.assert_not_called()

    def test_resume_no_combat_warns(self):
        ex = _make_executor(combat_manager=None)
        ex.execute([_action_ins("combat_resume")])
        assert any("CombatManager not attached" in m for m in ex._logs)  # type: ignore[attr-defined]

    def test_resume_exception_swallowed(self):
        cm = _mock_combat()
        cm.resume.side_effect = ValueError("oops")
        ex = _make_executor(combat_manager=cm)
        ex.execute([_action_ins("combat_resume")])
        assert any("raised" in m for m in ex._logs)  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# action combat_start
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatStart:

    def test_start_called(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm)
        ex.execute([_action_ins("combat_start")])
        cm.start.assert_called_once()

    def test_start_not_called_when_dry_run(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm, dry_run=True)
        ex.execute([_action_ins("combat_start")])
        cm.start.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# action combat_stop
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatStop:

    def test_stop_called(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm)
        ex.execute([_action_ins("combat_stop")])
        cm.stop.assert_called_once()

    def test_stop_not_called_when_dry_run(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm, dry_run=True)
        ex.execute([_action_ins("combat_stop")])
        cm.stop.assert_not_called()

    def test_stop_exception_swallowed(self):
        cm = _mock_combat()
        cm.stop.side_effect = Exception("error")
        ex = _make_executor(combat_manager=cm)
        ex.execute([_action_ins("combat_stop")])
        assert any("raised" in m for m in ex._logs)  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# Multiple combat actions in sequence
# ─────────────────────────────────────────────────────────────────────────────

class TestCombatActionsSequence:

    def test_pause_then_resume_both_called(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm)
        ex.execute([
            _action_ins("combat_pause"),
            _action_ins("combat_resume"),
        ])
        cm.pause.assert_called_once()
        cm.resume.assert_called_once()

    def test_start_pause_stop_in_order(self):
        call_order: List[str] = []
        cm = _mock_combat()
        cm.start.side_effect  = lambda: call_order.append("start")
        cm.pause.side_effect  = lambda: call_order.append("pause")
        cm.stop.side_effect   = lambda: call_order.append("stop")
        ex = _make_executor(combat_manager=cm)
        ex.execute([
            _action_ins("combat_start"),
            _action_ins("combat_pause"),
            _action_ins("combat_stop"),
        ])
        assert call_order == ["start", "pause", "stop"]

    def test_unknown_combat_action_not_dispatched(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm)
        # "action combat_fly" is not a real action — falls through to unhandled
        ex.execute([_action_ins("combat_fly")])
        cm.pause.assert_not_called()
        cm.start.assert_not_called()

    def test_mixed_script_calls_only_combat(self):
        cm = _mock_combat()
        ex = _make_executor(combat_manager=cm)
        ex.execute([
            Instruction(kind="wait", wait_secs=0.0, raw="wait 0"),
            _action_ins("combat_pause"),
            Instruction(kind="label", label="here", raw="label here"),
        ])
        cm.pause.assert_called_once()
        cm.resume.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# ScriptParser recognises action combat_* tokens
# ─────────────────────────────────────────────────────────────────────────────

class TestScriptParserCombatActions:

    def test_parse_combat_pause(self):
        ins = ScriptParser.parse_text("action combat_pause")
        assert len(ins) == 1
        assert ins[0].kind == "action"
        assert ins[0].action == "combat_pause"

    def test_parse_combat_resume(self):
        ins = ScriptParser.parse_text("action combat_resume")
        assert ins[0].action == "combat_resume"

    def test_parse_combat_start(self):
        ins = ScriptParser.parse_text("action combat_start")
        assert ins[0].action == "combat_start"

    def test_parse_combat_stop(self):
        ins = ScriptParser.parse_text("action combat_stop")
        assert ins[0].action == "combat_stop"

    def test_parse_mixed_script_with_combat(self):
        script = (
            "label hunt_start\n"
            "node (100,200,7)\n"
            "action combat_pause\n"
            "wait 0.5\n"
            "action combat_resume\n"
            "goto hunt_start\n"
        )
        ins = ScriptParser.parse_text(script)
        kinds = [i.kind for i in ins]
        assert "label"  in kinds
        assert "node"   in kinds
        assert "action" in kinds
        assert "wait"   in kinds
        assert "goto"   in kinds
        actions = [i.action for i in ins if i.kind == "action"]
        assert "combat_pause"  in actions
        assert "combat_resume" in actions


# ─────────────────────────────────────────────────────────────────────────────
# BotSession.run_script passes combat_manager to ScriptExecutor
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionRunScriptCombatWiring:

    def test_run_script_passes_combat_manager(self, tmp_path: Path):
        """
        When BotSession.run_script() is called, the executor receives
        combat_manager=session._combat.
        """
        from src.session import BotSession, SessionConfig

        # Script that just ends immediately
        script = tmp_path / "test.in"
        script.write_text("action end\n")

        session = BotSession(SessionConfig(dry_run=True))

        captured_kwargs: dict = {}

        original_init = ScriptExecutor.__init__

        def spy_init(self_inner, **kwargs):  # type: ignore[override]
            captured_kwargs.update(kwargs)
            original_init(self_inner, **kwargs)

        with patch.object(ScriptExecutor, "__init__", spy_init):
            with patch("src.script_parser.ScriptParser.parse_file",
                       return_value=[_action_ins("end")]):
                try:
                    session.run_script(script)
                except Exception:
                    pass

        assert "combat_manager" in captured_kwargs

    def test_run_script_combat_manager_is_session_combat(self, tmp_path: Path):
        """
        The combat_manager kwarg equals session._combat (may be None before start()).
        """
        from src.session import BotSession, SessionConfig

        script = tmp_path / "test.in"
        script.write_text("action end\n")

        session = BotSession(SessionConfig(dry_run=True))
        captured_kwargs: dict = {}

        original_init = ScriptExecutor.__init__

        def spy_init(self_inner, **kwargs):  # type: ignore[override]
            captured_kwargs.update(kwargs)
            original_init(self_inner, **kwargs)

        with patch.object(ScriptExecutor, "__init__", spy_init):
            with patch("src.script_parser.ScriptParser.parse_file",
                       return_value=[_action_ins("end")]):
                try:
                    session.run_script(script)
                except Exception:
                    pass

        assert captured_kwargs.get("combat_manager") is session._combat
