"""Tests for critical production paths in session.py — Phase A (A2-A4).

These tests cover the ZERO-coverage survival paths that keep the bot alive
during a 24h AFK session:

A2: Error recovery loop — consecutive error counting, 10-error bailout, checkpoint save on crash
A3: _exec_route closed-loop — position verification every 3 steps, retry on stuck
A4: _exec_transition + _exec_multifloor — rope/shovel/use hotkeys, z-level retry
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch, PropertyMock, call

import pytest

from src.session import BotSession, SessionConfig
from src.models import Coordinate, FloorTransition, Route, Waypoint


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_config(**kw) -> SessionConfig:
    defaults: dict = dict(
        route_file="",
        heal_hp_pct=70,
        start_delay=0.0,
        loop_route=False,
        step_interval=0.0001,  # must be >0 (validated)
        step_delay_min=0,
        step_delay_max=0,
        watchdog_timeout=999,
        transition_delay=0.0,
    )
    defaults.update(kw)
    return SessionConfig(**defaults)


def _coord(x: int, y: int, z: int = 7) -> Coordinate:
    return Coordinate(x=x, y=y, z=z)


def _route(steps: List[Coordinate], found: bool = True) -> Route:
    """Create a Route-like MagicMock or real Route with the given steps."""
    r = MagicMock(spec=Route)
    r.found = found
    r.steps = steps
    r.start = steps[0] if steps else Coordinate(0, 0, 7)
    r.end = steps[-1] if steps else Coordinate(0, 0, 7)
    return r


def _make_route_file(tmp_path: Path, waypoints: list | None = None) -> Path:
    """Create a valid waypoint route JSON file with ≥2 waypoints."""
    data = {"waypoints": waypoints or [
        {"name": "A", "x": 32369, "y": 32241, "z": 7},
        {"name": "B", "x": 32370, "y": 32241, "z": 7},
        {"name": "C", "x": 32371, "y": 32241, "z": 7},
    ]}
    path = tmp_path / "route.json"
    path.write_text(json.dumps(data))
    return path


def _make_session(tmp_path: Path | None = None, **kw) -> BotSession:
    """Create a silent BotSession. If tmp_path given + no route_file, creates one."""
    if tmp_path and "route_file" not in kw:
        kw["route_file"] = str(_make_route_file(tmp_path))
    s = BotSession(config=_make_config(**kw))
    s._log_cb = lambda msg: None  # silence logs
    return s


# ══════════════════════════════════════════════════════════════════════════════
# A2: Error recovery loop — the 10-error bailout that prevents infinite crash
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorRecoveryLoop:
    """Cover session.py lines 1438-1452: consecutive error counting → bailout."""

    def test_10_consecutive_errors_stops_session(self, tmp_path: Path):
        """10 consecutive exceptions in the route loop → _running = False."""
        s = _make_session(tmp_path, loop_route=True)
        s._running = True

        # Navigator always raises → propagates through navigate_to()
        mock_nav = MagicMock()
        mock_nav.navigate.side_effect = RuntimeError("boom")
        s._navigator = mock_nav

        with patch('src.session.jittered_sleep'):
            s._run_loop()

        assert s._running is False
        assert s._consecutive_errors >= 10

    def test_checkpoint_saved_on_crash(self, tmp_path: Path):
        """Each exception triggers _save_checkpoint() for crash recovery."""
        s = _make_session(tmp_path, loop_route=False)
        s._running = True

        mock_nav = MagicMock()
        mock_nav.navigate.side_effect = RuntimeError("boom")
        s._navigator = mock_nav
        s._save_checkpoint = MagicMock()

        with patch('src.session.jittered_sleep'):
            s._run_loop()

        # save_checkpoint called at least once on error
        assert s._save_checkpoint.call_count >= 1

    def test_success_resets_consecutive_errors(self, tmp_path: Path):
        """A successful route completion resets _consecutive_errors to 0."""
        s = _make_session(tmp_path, loop_route=False)
        s._running = True

        route_mock = _route([_coord(100, 100), _coord(101, 100)])
        mock_nav = MagicMock()
        mock_nav.navigate.return_value = route_mock
        s._navigator = mock_nav
        s._ctrl = None  # _exec_route checks is_connected → returns immediately

        s._run_loop()

        assert s._consecutive_errors == 0
        assert s._stats["routes_completed"] == 1

    def test_error_loop_jittered_sleep_between_retries(self, tmp_path: Path):
        """After an error, jittered_sleep(2.0) is called before retry."""
        s = _make_session(tmp_path, loop_route=False)
        s._running = True

        mock_nav = MagicMock()
        mock_nav.navigate.side_effect = RuntimeError("fail")
        s._navigator = mock_nav

        with patch('src.session.jittered_sleep') as mock_sleep:
            s._run_loop()

        mock_sleep.assert_called_with(2.0)

    def test_fewer_than_2_waypoints_exits_gracefully(self, tmp_path: Path):
        """Route with <2 waypoints logs and stops without crash."""
        path = _make_route_file(tmp_path, waypoints=[
            {"name": "Solo", "x": 100, "y": 100, "z": 7},
        ])
        s = _make_session(route_file=str(path))
        s._running = True

        s._run_loop()

        assert s._running is False

    def test_missing_route_file_exits_gracefully(self, tmp_path: Path):
        """FileNotFoundError when route file doesn't exist → clean exit."""
        s = _make_session(route_file=str(tmp_path / "nonexistent.json"))
        s._running = True

        s._run_loop()

        assert s._running is False


# ══════════════════════════════════════════════════════════════════════════════
# A2b: Checkpoint after each waypoint + route_done event
# ══════════════════════════════════════════════════════════════════════════════

class TestRouteCompletionEvents:
    """Verify checkpoint save and route_done event after successful segments."""

    def test_checkpoint_saved_after_each_segment(self, tmp_path: Path):
        path = _make_route_file(tmp_path)
        s = _make_session(route_file=str(path), loop_route=False)
        s._running = True

        route_mock = _route([_coord(100, 100), _coord(101, 100)])
        mock_nav = MagicMock()
        mock_nav.navigate.return_value = route_mock
        s._navigator = mock_nav
        s._ctrl = None  # skip exec
        s._save_checkpoint = MagicMock()

        s._run_loop()

        # 3 waypoints = 2 segments → at least 2 checkpoint saves
        assert s._save_checkpoint.call_count >= 2

    def test_route_done_event_emitted(self, tmp_path: Path):
        path = _make_route_file(tmp_path)
        s = _make_session(route_file=str(path), loop_route=False)
        s._running = True

        route_mock = _route([_coord(100, 100), _coord(101, 100)])
        mock_nav = MagicMock()
        mock_nav.navigate.return_value = route_mock
        s._navigator = mock_nav
        s._ctrl = None

        events: list = []
        s._event_bus.subscribe("route_done", lambda data: events.append(data))

        s._run_loop()

        assert len(events) == 1
        assert events[0]["routes_completed"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# A3: _exec_route — closed-loop position verification + retry on stuck
# ══════════════════════════════════════════════════════════════════════════════

class TestExecRouteClosedLoop:
    """Cover session.py _exec_route: step-by-step walking with position verify."""

    def test_move_to_tile_called_for_each_step(self):
        """Each step pair (prev→curr) calls ctrl.move_to_tile(dx, dy)."""
        s = _make_session(step_interval=0.0001)
        s._running = True
        ctrl = MagicMock(is_connected=MagicMock(return_value=True))
        s._ctrl = ctrl

        steps = [_coord(100, 100), _coord(101, 100), _coord(102, 100)]
        route = _route(steps)

        with patch('time.sleep'):
            s._exec_route(route)

        # 2 steps: (100→101) dx=1,dy=0, (101→102) dx=1,dy=0
        assert ctrl.move_to_tile.call_count == 2
        ctrl.move_to_tile.assert_any_call(1, 0)

    def test_stop_aborts_mid_route(self):
        """Setting _running=False aborts walking immediately."""
        s = _make_session(step_interval=0.0001)
        s._running = True
        ctrl = MagicMock(is_connected=MagicMock(return_value=True))
        s._ctrl = ctrl

        # 10-step route but stop after 1st step
        steps = [_coord(100 + i, 100) for i in range(10)]
        route = _route(steps)

        def stop_after_first(*args, **kwargs):
            s._running = False

        ctrl.move_to_tile.side_effect = stop_after_first

        with patch('time.sleep'):
            s._exec_route(route)

        assert ctrl.move_to_tile.call_count == 1

    def test_verify_position_at_step_3(self):
        """At step i%3==0 (i=3), verify_position_changed is called."""
        s = _make_session(step_interval=0.0001)
        s._running = True
        ctrl = MagicMock(is_connected=MagicMock(return_value=True))
        s._ctrl = ctrl

        mock_radar = MagicMock()
        s._radar = mock_radar
        s._frame_getter = MagicMock(return_value=MagicMock())
        s._position = _coord(100, 100)

        # 5 coords → steps at i=1,2,3,4; i=3 triggers verify
        steps = [_coord(100 + i, 100) for i in range(5)]
        route = _route(steps)

        with patch('src.session.verify_position_changed', return_value=True) as mock_vpc, \
             patch('time.sleep'):
            s._exec_route(route)

        # Step i=3 triggers verify_position_changed
        assert mock_vpc.call_count >= 1

    def test_retry_move_when_position_unchanged(self):
        """When verify_position_changed returns False → retry the step."""
        s = _make_session(step_interval=0.0001)
        s._running = True
        ctrl = MagicMock(is_connected=MagicMock(return_value=True))
        s._ctrl = ctrl

        s._radar = MagicMock()
        s._frame_getter = MagicMock(return_value=MagicMock())
        s._position = _coord(100, 100)

        # 5 coords → steps at i=1,2,3,4. i=3 triggers verify → False → retry
        steps = [_coord(100 + i, 100) for i in range(5)]
        route = _route(steps)

        with patch('src.session.verify_position_changed', return_value=False), \
             patch('time.sleep'):
            s._exec_route(route)

        # 4 normal moves + 1 retry at step 3
        assert ctrl.move_to_tile.call_count == 5

    def test_update_position_at_step_5(self):
        """At step i%5==0 (i=5), _update_position is called."""
        s = _make_session(step_interval=0.0001)
        s._running = True
        ctrl = MagicMock(is_connected=MagicMock(return_value=True))
        s._ctrl = ctrl
        s._update_position = MagicMock()
        s._check_frame_extras = MagicMock()
        s._frame_getter = MagicMock(return_value=MagicMock())

        # 7 coords → steps at i=1..6. i=5 triggers _update_position
        steps = [_coord(100 + i, 100) for i in range(7)]
        route = _route(steps)

        with patch('src.session.verify_position_changed', return_value=True), \
             patch('time.sleep'):
            s._exec_route(route)

        # Step 5 triggers _update_position
        s._update_position.assert_called()

    def test_anti_kick_notified_each_step(self):
        """AntiKick.notify_activity() is called on every step."""
        s = _make_session(step_interval=0.0001)
        s._running = True
        ctrl = MagicMock(is_connected=MagicMock(return_value=True))
        s._ctrl = ctrl
        ak = MagicMock()
        s._anti_kick = ak

        steps = [_coord(100 + i, 100) for i in range(4)]  # 3 steps
        route = _route(steps)

        with patch('time.sleep'):
            s._exec_route(route)

        assert ak.notify_activity.call_count == 3

    def test_no_ctrl_returns_immediately(self):
        """No controller → _exec_route exits without crash."""
        s = _make_session()
        s._running = True
        s._ctrl = None

        route = _route([_coord(100, 100), _coord(101, 100)])
        s._exec_route(route)  # should not raise

    def test_disconnected_ctrl_returns_immediately(self):
        """Disconnected controller → _exec_route returns without walking."""
        s = _make_session()
        s._running = True
        s._ctrl = MagicMock(is_connected=MagicMock(return_value=False))

        route = _route([_coord(100, 100), _coord(101, 100)])
        s._exec_route(route)
        s._ctrl.move_to_tile.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# A4: _exec_transition — rope/shovel/use hotkeys + z-level verify + retry
# ══════════════════════════════════════════════════════════════════════════════

class TestExecTransition:
    """Cover session.py _exec_transition: floor transition execution."""

    def _make_transition(self, kind: str = "rope",
                          entry_z: int = 8, exit_z: int = 7) -> FloorTransition:
        return FloorTransition(
            entry=_coord(100, 100, entry_z),
            exit=_coord(100, 100, exit_z),
            kind=kind,
        )

    def _setup_session(self, rope_vk: int = 0x70, shovel_vk: int = 0x71,
                       position_z: int = 8) -> BotSession:
        s = _make_session(
            rope_hotkey_vk=rope_vk,
            shovel_hotkey_vk=shovel_vk,
            transition_delay=0.0,
        )
        s._running = True
        s._ctrl = MagicMock(is_connected=MagicMock(return_value=True))
        s._position = _coord(100, 100, position_z)
        return s

    def test_rope_presses_rope_hotkey(self):
        s = self._setup_session(rope_vk=0x70)
        t = self._make_transition(kind="rope")

        with patch('src.session.jittered_sleep'):
            s._exec_transition(t)

        s._ctrl.press_key.assert_any_call(0x70)

    def test_shovel_presses_shovel_hotkey(self):
        s = self._setup_session(shovel_vk=0x71)
        t = self._make_transition(kind="shovel")

        with patch('src.session.jittered_sleep'):
            s._exec_transition(t)

        s._ctrl.press_key.assert_any_call(0x71)

    def test_use_falls_back_to_rope_hotkey(self):
        s = self._setup_session(rope_vk=0x70)
        t = self._make_transition(kind="use")

        with patch('src.session.jittered_sleep'):
            s._exec_transition(t)

        s._ctrl.press_key.assert_any_call(0x70)

    def test_walk_and_ladder_press_no_key(self):
        """Walk/ladder transitions don't press any key."""
        for kind in ("walk", "ladder"):
            s = self._setup_session()
            t = self._make_transition(kind=kind)

            with patch('src.session.jittered_sleep'):
                s._exec_transition(t)

            s._ctrl.press_key.assert_not_called()

    def test_z_level_verify_retries_on_unchanged(self):
        """When z-level doesn't change after transition → retry once."""
        s = self._setup_session(position_z=8, rope_vk=0x70)
        s._radar = MagicMock()
        s._frame_getter = MagicMock()

        # _update_position keeps z=8 (didn't change)
        def keep_same_z():
            s._position = _coord(100, 100, 8)

        s._update_position = keep_same_z

        t = self._make_transition(kind="rope", entry_z=8, exit_z=7)

        with patch('src.session.jittered_sleep'):
            s._exec_transition(t)

        # press_key called TWICE: initial + retry
        assert s._ctrl.press_key.call_count == 2

    def test_z_level_verify_no_retry_when_changed(self):
        """When z-level changes correctly → NO retry."""
        s = self._setup_session(position_z=8, rope_vk=0x70)
        s._radar = MagicMock()
        s._frame_getter = MagicMock()

        # _update_position changes z to 7 (success!)
        def change_z():
            s._position = _coord(100, 100, 7)

        s._update_position = change_z

        t = self._make_transition(kind="rope", entry_z=8, exit_z=7)

        with patch('src.session.jittered_sleep'):
            s._exec_transition(t)

        # press_key called only ONCE (no retry needed)
        assert s._ctrl.press_key.call_count == 1

    def test_no_ctrl_returns_immediately(self):
        """No controller → _exec_transition exits without crash."""
        s = _make_session()
        s._ctrl = None
        t = self._make_transition()

        s._exec_transition(t)  # should not raise

    def test_transition_delay_applied(self):
        """transition_delay > 0 → jittered_sleep called."""
        s = self._setup_session()
        s._cfg.transition_delay = 1.5

        t = self._make_transition(kind="walk")

        with patch('src.session.jittered_sleep') as mock_sleep:
            s._exec_transition(t)

        mock_sleep.assert_called_with(1.5)

    def test_disabled_vk_does_not_press_key(self):
        """rope_hotkey_vk=0 → press_key is NOT called even for rope transition."""
        s = self._setup_session(rope_vk=0x00)
        t = self._make_transition(kind="rope")

        with patch('src.session.jittered_sleep'):
            s._exec_transition(t)

        s._ctrl.press_key.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# A4b: _exec_multifloor — stitch route segments with transitions
# ══════════════════════════════════════════════════════════════════════════════

class TestExecMultifloor:
    """Cover session.py _exec_multifloor: multi-floor journey execution."""

    def _make_nav_with_transitions(self, transitions: list[FloorTransition]) -> MagicMock:
        mock_nav = MagicMock()
        mock_registry = MagicMock()
        mock_registry.transitions = transitions
        mock_nav.transitions = mock_registry
        return mock_nav

    def test_walks_each_segment_and_transitions(self):
        """Two segments + one transition between them."""
        s = _make_session(transition_delay=0.0)
        s._running = True
        s._ctrl = MagicMock(is_connected=MagicMock(return_value=True))

        seg1 = _route([_coord(100, 100, 8), _coord(100, 101, 8)])
        seg2 = _route([_coord(100, 101, 7), _coord(100, 102, 7)])

        transition = FloorTransition(
            entry=_coord(100, 101, 8),
            exit=_coord(100, 101, 7),
            kind="rope",
        )

        s._navigator = self._make_nav_with_transitions([transition])
        s._exec_route = MagicMock()
        s._exec_transition = MagicMock()

        s._exec_multifloor(
            [seg1, seg2],
            start=_coord(100, 100, 8),
            end=_coord(100, 102, 7),
        )

        assert s._exec_route.call_count == 2
        s._exec_transition.assert_called_once_with(transition)

    def test_skips_unfound_segment(self):
        """Segment with found=False → skipped."""
        s = _make_session()
        s._running = True
        s._ctrl = MagicMock()

        seg1 = _route([_coord(100, 100)], found=False)
        seg2 = _route([_coord(100, 101), _coord(100, 102)])

        s._navigator = self._make_nav_with_transitions([])
        s._exec_route = MagicMock()
        s._exec_transition = MagicMock()

        s._exec_multifloor(
            [seg1, seg2],
            start=_coord(100, 100),
            end=_coord(100, 102),
        )

        # Only seg2 should be executed
        assert s._exec_route.call_count == 1

    def test_stop_aborts_multifloor(self):
        """Setting _running=False aborts multifloor mid-journey."""
        s = _make_session()
        s._running = True
        s._ctrl = MagicMock()

        s._navigator = self._make_nav_with_transitions([])

        def stop_after_first(route):
            s._running = False

        s._exec_route = stop_after_first
        s._exec_transition = MagicMock()

        seg1 = _route([_coord(100, 100), _coord(100, 101)])
        seg2 = _route([_coord(100, 101), _coord(100, 102)])

        s._exec_multifloor([seg1, seg2], _coord(100, 100), _coord(100, 102))

        # Should NOT have called _exec_transition (stopped after seg1)
        s._exec_transition.assert_not_called()

    def test_missing_transition_uses_delay(self):
        """No registered transition → sleeps transition_delay."""
        s = _make_session(transition_delay=0.5)
        s._running = True
        s._ctrl = MagicMock()

        s._navigator = self._make_nav_with_transitions([])  # empty!
        s._exec_route = MagicMock()

        seg1 = _route([_coord(100, 100), _coord(100, 101)])
        seg2 = _route([_coord(100, 101), _coord(100, 102)])

        with patch('time.sleep') as mock_sleep:
            s._exec_multifloor([seg1, seg2], _coord(100, 100), _coord(100, 102))

        # With jitter the exact value varies; just verify sleep was called
        assert mock_sleep.called

    def test_no_navigator_returns_immediately(self):
        """No navigator → _exec_multifloor returns without crash."""
        s = _make_session()
        s._navigator = None

        seg1 = _route([_coord(100, 100)])
        s._exec_multifloor([seg1], _coord(100, 100), _coord(100, 101))
        # No crash = pass


# ══════════════════════════════════════════════════════════════════════════════
# A2c: Pause/Resume subsystems — death/reconnect recovery
# ══════════════════════════════════════════════════════════════════════════════

class TestPauseResumeSubsystems:
    """Cover session.py _pause_subsystems / _resume_subsystems."""

    def test_pause_calls_all_subsystems(self):
        s = _make_session()
        s._healer = MagicMock()
        s._combat = MagicMock()
        s._anti_kick = MagicMock()

        s._pause_subsystems()

        s._healer.pause.assert_called_once()
        s._combat.pause.assert_called_once()
        s._anti_kick.pause.assert_called_once()

    def test_resume_calls_all_subsystems(self):
        s = _make_session()
        s._healer = MagicMock()
        s._combat = MagicMock()
        s._anti_kick = MagicMock()

        s._resume_subsystems()

        s._healer.resume.assert_called_once()
        s._combat.resume.assert_called_once()
        s._anti_kick.resume.assert_called_once()

    def test_pause_healer_failure_doesnt_crash(self):
        """Exception in healer.pause() should not propagate."""
        s = _make_session()
        s._healer = MagicMock()
        s._healer.pause.side_effect = RuntimeError("boom")
        s._combat = MagicMock()
        s._anti_kick = MagicMock()

        s._pause_subsystems()  # should not raise
        s._combat.pause.assert_called_once()  # combat still paused

    def test_resume_combat_failure_doesnt_crash(self):
        s = _make_session()
        s._healer = MagicMock()
        s._combat = MagicMock()
        s._combat.resume.side_effect = RuntimeError("boom")
        s._anti_kick = MagicMock()

        s._resume_subsystems()  # should not raise
        s._anti_kick.resume.assert_called_once()

    def test_pause_with_none_subsystems(self):
        """All subsystems None → no crash."""
        s = _make_session()
        s._healer = None
        s._combat = None
        s._anti_kick = None

        s._pause_subsystems()  # should not raise
        s._resume_subsystems()  # should not raise


# ══════════════════════════════════════════════════════════════════════════════
# Mid-route resupply trigger
# ══════════════════════════════════════════════════════════════════════════════

class TestMidRouteResupply:
    """Cover session.py depot_orch.should_resupply mid-route check."""

    def test_resupply_triggered_mid_route(self, tmp_path: Path):
        path = _make_route_file(tmp_path)
        s = _make_session(route_file=str(path), loop_route=False)
        s._running = True

        route_mock = _route([_coord(100, 100), _coord(101, 100)])
        mock_nav = MagicMock()
        mock_nav.navigate.return_value = route_mock
        s._navigator = mock_nav
        s._ctrl = None  # skip exec_route

        # Setup depot orchestrator that says "yes, resupply"
        mock_orch = MagicMock()
        mock_orch.should_resupply.return_value = True
        mock_orch.run_resupply.return_value = True
        mock_orch.stats_snapshot.return_value = {"count": 1}
        s._depot_orch = mock_orch

        s._run_loop()

        mock_orch.run_resupply.assert_called()
