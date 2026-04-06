"""
Tests para rutas no cubiertas de BotSession:
  - _update_position
  - _check_frame_extras
  - navigate_to
  - stop() con subsistemas
  - _exec_route
  - _run_loop con JSON unificado
  - _save_checkpoint / load_checkpoint
  - has_* properties
  - uptime / stats helpers

100% offline — sin Tibia, sin OBS, sin red.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, List, Optional, cast
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.models import Coordinate, Route, Waypoint
from src.session import BotSession, SessionConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _silent(msg: str) -> None:
    pass


def _cfg(**kw) -> SessionConfig:
    defaults: dict[str, Any] = dict(route_file="", start_delay=0.0, heal_hp_pct=70, loop_route=False)
    defaults.update(kw)
    return SessionConfig(**cast(Any, defaults))


def _session(**cfg_kw) -> BotSession:
    s = BotSession(config=_cfg(**cfg_kw), log_callback=_silent)
    # _pico / _arduino / _raw_ctrl / _path_viz are only set in _startup_subsystems,
    # so we need to initialise them here for tests that call stop() directly.
    s._pico = None
    s._arduino = None
    s._raw_ctrl = None
    s._path_viz = None
    return s


def _coord(x: int = 100, y: int = 200, z: int = 7) -> Coordinate:
    return Coordinate(x, y, z)


def _route(found: bool = True, n: int = 3) -> Route:
    steps = [_coord(100 + i, 200) for i in range(n)]
    return Route(start=steps[0], end=steps[-1], steps=steps, found=found)


def _black_frame() -> np.ndarray:
    return np.zeros((600, 800, 3), dtype=np.uint8)


def _white_frame() -> np.ndarray:
    return np.ones((600, 800, 3), dtype=np.uint8) * 255


# ─────────────────────────────────────────────────────────────────────────────
# _update_position
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdatePosition:

    def test_returns_false_when_no_frame_source(self):
        s = _session()
        s._frame_cache = None
        s._frame_getter = None
        assert s._update_position() is False

    def test_returns_false_when_frame_is_none(self):
        s = _session()
        s._frame_getter = lambda: None
        assert s._update_position() is False

    def test_returns_false_when_no_radar_or_resolver(self):
        s = _session()
        s._frame_getter = lambda: _black_frame()
        s._radar = None
        s._pos_resolver = None
        assert s._update_position() is False

    def test_uses_radar_when_no_resolver(self):
        s = _session()
        expected = _coord(150, 150)
        radar = MagicMock()
        radar.read.return_value = expected
        s._frame_getter = lambda: _black_frame()
        s._radar = radar
        s._pos_resolver = None
        result = s._update_position()
        assert result is True
        assert s._position == expected

    def test_returns_false_when_radar_returns_none(self):
        s = _session()
        radar = MagicMock()
        radar.read.return_value = None
        s._frame_getter = lambda: _black_frame()
        s._radar = radar
        s._pos_resolver = None
        assert s._update_position() is False

    def test_uses_pos_resolver_over_radar(self):
        s = _session()
        expected = _coord(120, 130)
        resolver = MagicMock()
        resolver.resolve.return_value = expected
        radar = MagicMock()
        s._frame_getter = lambda: _black_frame()
        s._pos_resolver = resolver
        s._radar = radar
        s._update_position()
        resolver.resolve.assert_called_once()
        radar.read.assert_not_called()
        assert s._position == expected

    def test_resolver_none_returns_false(self):
        s = _session()
        resolver = MagicMock()
        resolver.resolve.return_value = None
        s._frame_getter = lambda: _black_frame()
        s._pos_resolver = resolver
        assert s._update_position() is False

    def test_rejects_large_jump(self):
        s = _session()
        s._position = _coord(100, 100)
        jumped = _coord(200, 200)  # 100 tile jump > MAX_POS_JUMP
        radar = MagicMock()
        radar.read.return_value = jumped
        s._frame_getter = lambda: _black_frame()
        s._radar = radar
        result = s._update_position()
        assert result is False
        assert s._position == _coord(100, 100)  # unchanged

    def test_accepts_small_jump(self):
        s = _session()
        s._position = _coord(100, 100)
        nearby = _coord(103, 101)  # small jump
        radar = MagicMock()
        radar.read.return_value = nearby
        s._frame_getter = lambda: _black_frame()
        s._radar = radar
        result = s._update_position()
        assert result is True
        assert s._position == nearby

    def test_rejects_moderate_manhattan_jump(self):
        s = _session()
        s._position = _coord(100, 100)
        jumped = _coord(102, 103)
        radar = MagicMock()
        radar.read.return_value = jumped
        s._frame_getter = lambda: _black_frame()
        s._radar = radar
        result = s._update_position()
        assert result is False
        assert s._position == _coord(100, 100)

    def test_frame_quality_rejects_bad_frame(self):
        s = _session()
        fq = MagicMock()
        bad_result = MagicMock()
        bad_result.name = "BLACK"
        fq.check.return_value = bad_result
        s._frame_quality = fq
        s._frame_getter = lambda: _black_frame()
        radar = MagicMock()
        s._radar = radar
        result = s._update_position()
        assert result is False
        radar.read.assert_not_called()

    def test_frame_quality_ok_passes_through(self):
        s = _session()
        fq = MagicMock()
        ok_result = MagicMock()
        ok_result.name = "OK"
        fq.check.return_value = ok_result
        expected = _coord(100, 100)
        radar = MagicMock()
        radar.read.return_value = expected
        s._frame_quality = fq
        s._frame_getter = lambda: _black_frame()
        s._radar = radar
        result = s._update_position()
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# _check_frame_extras
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckFrameExtras:

    def test_calls_pvp_scan_when_detector_set(self):
        s = _session()
        pvp = MagicMock()
        s._pvp_detector = pvp
        frame = _black_frame()
        s._check_frame_extras(frame)
        pvp.scan.assert_called_once_with(frame)

    def test_no_pvp_detector_no_crash(self):
        s = _session()
        s._pvp_detector = None
        s._check_frame_extras(_black_frame())  # should not raise

    def test_calls_inventory_check_when_should_check(self):
        s = _session()
        inv = MagicMock()
        inv.should_check.return_value = True
        s._inventory_mgr = inv
        frame = _black_frame()
        s._check_frame_extras(frame)
        inv.check_inventory.assert_called_once_with(frame)

    def test_skips_inventory_when_should_check_false(self):
        s = _session()
        inv = MagicMock()
        inv.should_check.return_value = False
        s._inventory_mgr = inv
        s._check_frame_extras(_black_frame())
        inv.check_inventory.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# navigate_to
# ─────────────────────────────────────────────────────────────────────────────

class TestNavigateTo:

    def test_single_floor_uses_navigate(self):
        s = _session()
        route = _route()
        nav = MagicMock()
        nav.navigate.return_value = route
        s._navigator = nav
        start = _coord(100, 100, z=7)
        end   = _coord(110, 100, z=7)
        result = s.navigate_to(start, end)
        nav.navigate.assert_called_once_with(start, end)
        assert result == [route]

    def test_multifloor_when_z_differs(self):
        s = _session()
        route = _route()
        nav = MagicMock()
        nav.navigate_multifloor.return_value = [route]
        s._navigator = nav
        start = _coord(100, 100, z=7)
        end   = _coord(100, 100, z=6)
        result = s.navigate_to(start, end)
        nav.navigate_multifloor.assert_called_once_with(start, end)
        assert result == [route]

    def test_multifloor_forced_when_flag_set(self):
        s = _session()
        route = _route()
        nav = MagicMock()
        nav.navigate_multifloor.return_value = [route]
        s._navigator = nav
        start = _coord(100, 100, z=7)
        end   = _coord(110, 100, z=7)  # same floor
        result = s.navigate_to(start, end, multifloor=True)
        nav.navigate_multifloor.assert_called_once()
        nav.navigate.assert_not_called()

    def test_creates_navigator_when_none(self):
        s = _session()
        s._navigator = None
        route = _route()
        mock_nav = MagicMock()
        mock_nav.navigate.return_value = route
        with patch("src.session.WaypointNavigator", return_value=mock_nav), \
             patch("src.session.TibiaMapLoader"):
            result = s.navigate_to(_coord(), _coord(110, 200))
        assert result == [route]


# ─────────────────────────────────────────────────────────────────────────────
# stop() with subsystems
# ─────────────────────────────────────────────────────────────────────────────

class TestStopWithSubsystems:

    def test_stop_calls_healer_stop(self):
        s = _session()
        s._running = True
        healer = MagicMock()
        s._healer = healer
        s.stop()
        healer.stop.assert_called_once()

    def test_stop_calls_combat_stop(self):
        s = _session()
        s._running = True
        combat = MagicMock()
        s._combat = combat
        s.stop()
        combat.stop.assert_called_once()

    def test_stop_calls_looter_stop(self):
        s = _session()
        s._running = True
        looter = MagicMock()
        s._looter = looter
        s.stop()
        looter.stop.assert_called_once()

    def test_stop_calls_all_component_stops(self):
        s = _session()
        s._running = True
        components = {
            "_healer": MagicMock(),
            "_looter": MagicMock(),
            "_combat": MagicMock(),
            "_condition_monitor": MagicMock(),
            "_death_handler": MagicMock(),
            "_anti_kick": MagicMock(),
            "_break_scheduler": MagicMock(),
        }
        for attr, mock in components.items():
            setattr(s, attr, mock)
        s.stop()
        for attr, mock in components.items():
            mock.stop.assert_called_once(), f"{attr}.stop() not called"

    def test_stop_tolerates_component_stop_raising(self):
        s = _session()
        s._running = True
        bad_component = MagicMock()
        bad_component.stop.side_effect = RuntimeError("boom")
        s._healer = bad_component
        # Should not propagate the exception
        s.stop()
        assert s._running is False

    def test_stop_when_not_running_is_noop(self):
        s = _session()
        s._running = False
        healer = MagicMock()
        s._healer = healer
        s.stop()
        healer.stop.assert_not_called()

    def test_force_cleanup_stops_orphaned_components_after_failed_startup(self):
        s = _session()
        s._running = False
        healer = MagicMock()
        s._healer = healer
        s.stop(force_cleanup=True)
        healer.stop.assert_called_once()

    def test_stop_aborts_executor(self):
        s = _session()
        s._running = True
        executor = MagicMock()
        s._executor = executor
        s.stop()
        executor.abort.assert_called_once()

    def test_stop_clears_running_flag(self):
        s = _session()
        s._running = True
        s.stop()
        assert s._running is False


# ─────────────────────────────────────────────────────────────────────────────
# _exec_route
# ─────────────────────────────────────────────────────────────────────────────

class TestExecRoute:

    def test_skips_when_ctrl_none(self):
        s = _session()
        s._ctrl = None
        s._running = True
        route = _route(n=4)
        s._exec_route(route)  # should not raise

    def test_skips_when_ctrl_not_connected(self):
        s = _session()
        ctrl = MagicMock()
        ctrl.is_connected.return_value = False
        s._ctrl = ctrl
        s._running = True
        route = _route(n=4)
        s._exec_route(route)
        ctrl.move_to_tile.assert_not_called()

    def test_calls_move_to_tile_for_each_step(self):
        s = _session()
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        s._ctrl = ctrl
        s._running = True
        route = _route(n=4)  # 4 steps = 3 moves
        with patch("src.session.jittered_sleep"), \
             patch("src.session.time") as mock_time:
            mock_time.monotonic.return_value = 1.0
            mock_time.sleep = lambda _: None
            s._exec_route(route)
        assert ctrl.move_to_tile.call_count == 3

    def test_stops_mid_route_when_running_cleared(self):
        s = _session()
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        s._ctrl = ctrl
        s._running = True
        route = _route(n=10)
        call_count = [0]

        def fake_move(dx, dy):
            call_count[0] += 1
            if call_count[0] >= 2:
                s._running = False

        ctrl.move_to_tile.side_effect = fake_move
        with patch("src.session.jittered_sleep"), \
             patch("src.session.time") as mock_time:
            mock_time.monotonic.return_value = 1.0
            mock_time.sleep = lambda _: None
            s._exec_route(route)
        assert ctrl.move_to_tile.call_count <= 3  # stopped early

    def test_notifies_anti_kick_each_step(self):
        s = _session()
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        s._ctrl = ctrl
        s._running = True
        anti_kick = MagicMock()
        s._anti_kick = anti_kick
        route = _route(n=4)
        with patch("src.session.jittered_sleep"), \
             patch("src.session.time") as mock_time:
            mock_time.monotonic.return_value = 1.0
            mock_time.sleep = lambda _: None
            s._exec_route(route)
        assert anti_kick.notify_activity.call_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# _run_loop — unified JSON script path
# ─────────────────────────────────────────────────────────────────────────────

class TestRunLoopUnifiedScript:

    def _make_script_json(self, tmp_path: Path) -> Path:
        data = {
            "script": "node (100,200,7)\naction end",
            "session": {"loop_route": False},
        }
        p = tmp_path / "route.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_unified_json_calls_run_script(self, tmp_path: Path):
        path = self._make_script_json(tmp_path)
        s = _session(route_file=str(path), loop_route=False)
        s._running = True
        called = [False]

        def fake_run_script(p):
            called[0] = True

        setattr(s, "run_script", fake_run_script)
        s._run_loop()
        assert called[0] is True

    def test_unified_json_increments_routes_completed(self, tmp_path: Path):
        path = self._make_script_json(tmp_path)
        s = _session(route_file=str(path), loop_route=False)
        s._running = True
        setattr(s, "run_script", lambda p: None)
        s._run_loop()
        assert s.stats["routes_completed"] == 1

    def test_unified_json_run_script_exception_stops_loop(self, tmp_path: Path):
        path = self._make_script_json(tmp_path)
        s = _session(route_file=str(path), loop_route=False)
        s._running = True

        def bad_run_script(p):
            raise RuntimeError("script failed")

        setattr(s, "run_script", bad_run_script)
        s._run_loop()
        assert s._running is False

    def test_unified_json_loops_when_loop_route_true(self, tmp_path: Path):
        path = self._make_script_json(tmp_path)
        s = _session(route_file=str(path), loop_route=True)
        s._running = True
        run_count = [0]

        def counting_run_script(p):
            run_count[0] += 1
            if run_count[0] >= 3:
                s._running = False  # stop after 3 iterations

        setattr(s, "run_script", counting_run_script)
        s._run_loop()
        assert run_count[0] == 3


# ─────────────────────────────────────────────────────────────────────────────
# load_waypoints
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadWaypointsExtra:

    def test_loads_list_format(self, tmp_path: Path):
        data = [
            {"name": "A", "x": 100, "y": 200, "z": 7},
            {"name": "B", "x": 110, "y": 200, "z": 7},
        ]
        p = tmp_path / "route.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        s = _session()
        waypoints = s.load_waypoints(str(p))
        assert len(waypoints) == 2
        assert waypoints[0].coord == _coord(100, 200, 7)
        assert waypoints[1].coord == _coord(110, 200, 7)

    def test_loads_dict_with_waypoints_key(self, tmp_path: Path):
        data = {"waypoints": [
            {"name": "A", "x": 50, "y": 50, "z": 7},
        ]}
        p = tmp_path / "route.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        s = _session()
        waypoints = s.load_waypoints(str(p))
        assert len(waypoints) == 1
        assert waypoints[0].coord == _coord(50, 50, 7)

    def test_missing_file_raises(self, tmp_path: Path):
        s = _session()
        with pytest.raises(FileNotFoundError):
            s.load_waypoints(str(tmp_path / "nonexistent.json"))


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckpoint:

    def test_load_checkpoint_returns_none_or_checkpoint(self):
        # load_checkpoint delegates to SessionCheckpoint.load()
        from src.session_persistence import SessionCheckpoint
        with patch.object(SessionCheckpoint, "load", return_value=None):
            result = BotSession.load_checkpoint()
        assert result is None

    def test_clear_checkpoint_delegates(self):
        from src.session_persistence import SessionCheckpoint
        cleared = [False]
        with patch.object(SessionCheckpoint, "clear", lambda: cleared.__setitem__(0, True)):
            BotSession.clear_checkpoint()
        assert cleared[0] is True


# ─────────────────────────────────────────────────────────────────────────────
# has_* properties
# ─────────────────────────────────────────────────────────────────────────────

class TestHasProperties:

    def test_has_healer_false_by_default(self):
        s = _session()
        assert s.has_healer is False

    def test_has_healer_true_when_set(self):
        s = _session()
        s._healer = MagicMock()
        assert s.has_healer is True

    def test_has_navigator_false_by_default(self):
        s = _session()
        assert s.has_navigator is False

    def test_has_navigator_true_when_set(self):
        s = _session()
        s._navigator = MagicMock()
        assert s.has_navigator is True

    def test_has_combat_false_by_default(self):
        s = _session()
        assert s.has_combat is False

    def test_has_combat_true_when_set(self):
        s = _session()
        s._combat = MagicMock()
        assert s.has_combat is True

    def test_has_condition_monitor_false_by_default(self):
        s = _session()
        assert s.has_condition_monitor is False

    def test_has_alert_system_false_by_default(self):
        s = _session()
        assert s.has_alert_system is False

    def test_has_frame_quality_false_by_default(self):
        s = _session()
        assert s.has_frame_quality is False

    def test_has_pvp_detector_false_by_default(self):
        s = _session()
        assert s.has_pvp_detector is False

    def test_has_spawn_manager_false_by_default(self):
        s = _session()
        assert s.has_spawn_manager is False


# ─────────────────────────────────────────────────────────────────────────────
# uptime / is_running / routes_completed
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionHelpers:

    def test_uptime_none_before_start(self):
        s = _session()
        # uptime() returns None when not running
        assert s.uptime() is None

    def test_uptime_positive_when_running(self):
        s = _session()
        s._running = True
        s._stats["start_time"] = time.monotonic() - 5.0
        result = s.uptime()
        assert result is not None
        assert result >= 5.0

    def test_is_running_false_initially(self):
        assert _session().is_running is False

    def test_routes_completed_zero_initially(self):
        assert _session().routes_completed == 0

    def test_routes_completed_increments(self):
        s = _session()
        s._inc_stat("routes_completed")
        s._inc_stat("routes_completed")
        assert s.routes_completed == 2

    def test_reset_stats_clears_routes(self):
        s = _session()
        s._inc_stat("routes_completed")
        s.reset_stats()
        assert s.routes_completed == 0

    def test_has_started_false_initially(self):
        assert _session().has_started is False

    def test_has_started_true_after_start_time_set(self):
        s = _session()
        s._stats["start_time"] = time.time()
        assert s.has_started is True
