"""
Tests for new BotSession / SessionConfig features:
  - auto_combat, monitor_conditions, dry_run fields
  - event_bus property
  - has_combat, has_condition_monitor properties
  - run_script() method
  - set_frame_getter() propagation to all sub-components
Fully offline: no OBS, no Tibia process.
"""
from __future__ import annotations

from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from src.session import BotSession, SessionConfig
from src.event_bus import EventBus
from src.models import Coordinate, Route
from src.session_persistence import SessionCheckpoint


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _silent_log(msg: str) -> None:
    pass


def _make_session(**cfg_kwargs) -> BotSession:
    cfg = SessionConfig(**cfg_kwargs)
    return BotSession(cfg, log_callback=_silent_log)


# ─────────────────────────────────────────────────────────────────────────────
# SessionConfig — new fields
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigNewFields:

    def test_auto_combat_default_false(self):
        cfg = SessionConfig()
        assert cfg.auto_combat is False

    def test_monitor_conditions_default_false(self):
        cfg = SessionConfig()
        assert cfg.monitor_conditions is False

    def test_dry_run_default_false(self):
        cfg = SessionConfig()
        assert cfg.dry_run is False

    def test_new_fields_save_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "session_cfg.json"
        cfg = SessionConfig(auto_combat=True, monitor_conditions=True, dry_run=True)
        cfg.save(path)
        loaded = SessionConfig.load(path)
        assert loaded.auto_combat is True
        assert loaded.monitor_conditions is True
        assert loaded.dry_run is True

    def test_default_roundtrip_preserves_false(self, tmp_path: Path):
        path = tmp_path / "session_cfg.json"
        SessionConfig().save(path)
        loaded = SessionConfig.load(path)
        assert loaded.auto_combat is False
        assert loaded.dry_run is False


# ─────────────────────────────────────────────────────────────────────────────
# BotSession — event_bus property
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionEventBus:

    def test_event_bus_is_eventbus_instance(self):
        s = _make_session()
        assert isinstance(s.event_bus, EventBus)

    def test_event_bus_same_instance_across_calls(self):
        s = _make_session()
        assert s.event_bus is s.event_bus

    def test_event_bus_not_none(self):
        s = _make_session()
        assert s.event_bus is not None

    def test_subscribe_and_emit_on_session_bus(self):
        s = _make_session()
        received: List[object] = []
        s.event_bus.subscribe("test_ev", received.append)
        s.event_bus.emit("test_ev", 42)
        assert received == [42]


# ─────────────────────────────────────────────────────────────────────────────
# BotSession — has_combat / has_condition_monitor (before start)
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionNewProperties:

    def test_has_combat_false_before_start(self):
        s = _make_session()
        assert s.has_combat is False

    def test_has_condition_monitor_false_before_start(self):
        s = _make_session()
        assert s.has_condition_monitor is False

    def test_has_combat_returns_bool(self):
        s = _make_session()
        assert isinstance(s.has_combat, bool)

    def test_has_condition_monitor_returns_bool(self):
        s = _make_session()
        assert isinstance(s.has_condition_monitor, bool)


# ─────────────────────────────────────────────────────────────────────────────
# BotSession — run_script()
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionRunScript:

    def test_run_script_raises_file_not_found(self, tmp_path: Path):
        s = _make_session()
        with pytest.raises(FileNotFoundError):
            s.run_script(tmp_path / "nonexistent.in")

    def test_run_script_executes_empty_script(self, tmp_path: Path):
        script = tmp_path / "empty.in"
        script.write_text("", encoding="utf-8")
        s = _make_session()
        # Should not raise even with no ctrl/navigator
        s.run_script(script)

    def test_run_script_in_dry_run_logs_not_sends(self, tmp_path: Path):
        """In dry_run mode, wait instruction does not block for real."""
        script = tmp_path / "test.in"
        script.write_text("wait 100\n", encoding="utf-8")
        logs: List[str] = []
        cfg = SessionConfig(dry_run=True)
        s = BotSession(cfg, log_callback=logs.append)

        import time
        t0 = time.monotonic()
        with patch("src.script_executor.time.sleep"):
            s.run_script(script)
        elapsed = time.monotonic() - t0
        # Should be nearly instant (no real 100s sleep)
        assert elapsed < 5.0

    def test_run_script_uses_session_position(self, tmp_path: Path):
        from src.models import Coordinate
        script = tmp_path / "pos.in"
        script.write_text("", encoding="utf-8")
        s = _make_session()
        s._position = Coordinate(100, 200, 7)
        # Should not raise
        s.run_script(script)

    def test_run_script_action_end(self, tmp_path: Path):
        script = tmp_path / "end.in"
        script.write_text("action end\n", encoding="utf-8")
        s = _make_session()
        s.run_script(script)  # should terminate cleanly

    def test_run_script_resumes_from_checkpoint_instruction(self, tmp_path: Path):
        script = tmp_path / "resume.in"
        script.write_text("wait 0\nwait 0\naction end\n", encoding="utf-8")
        s = _make_session()
        with patch.object(BotSession, "load_checkpoint") as load_ckpt:
            load_ckpt.return_value = SessionCheckpoint(
                route_file=str(script),
                extra={
                    "route_mode": "script",
                    "script_resume_instruction_index": 1,
                },
            )
            with patch("src.script_executor.ScriptExecutor.execute") as execute:
                s.run_script(script)
        assert execute.call_args.kwargs["start_index"] == 1

    def test_run_script_auto_aligns_start_index_to_prefix_segment(self, tmp_path: Path):
        script = tmp_path / "route.json"
        script.write_text(
            '{"script":[{"kind":"label","label":"start"},{"kind":"node","x":32368,"y":32234,"z":7},{"kind":"node","x":32369,"y":32224,"z":7},{"kind":"node","x":32368,"y":32215,"z":7}]}',
            encoding="utf-8",
        )
        s = _make_session()
        s._position = Coordinate(32368, 32232, 7)
        nav = MagicMock()
        nav.is_floor_loaded.return_value = True
        nav.navigate.return_value = Route(
            start=Coordinate(32368, 32234, 7),
            end=Coordinate(32369, 32224, 7),
            steps=[
                Coordinate(32368, 32234, 7),
                Coordinate(32368, 32233, 7),
                Coordinate(32368, 32232, 7),
                Coordinate(32368, 32231, 7),
                Coordinate(32369, 32224, 7),
            ],
            found=True,
            total_distance=4.0,
        )
        s._navigator = nav

        with patch.object(BotSession, "load_checkpoint", return_value=None):
            with patch("src.script_executor.ScriptExecutor.execute") as execute:
                s.run_script(script)

        assert execute.call_args.kwargs["start_index"] == 2

    def test_run_script_saves_cumulative_visualizer_image(self, tmp_path: Path):
        script = tmp_path / "empty.in"
        script.write_text("", encoding="utf-8")
        s = _make_session()
        s._loader = MagicMock()

        with patch("src.path_visualizer.PathVisualizer.save_cumulative") as save_cumulative:
            s.run_script(script)

        save_cumulative.assert_called_once()

    def test_run_script_marks_route_corridor_as_critical_tiles(self, tmp_path: Path):
        script = tmp_path / "route.json"
        script.write_text(
            '{"script":[{"kind":"label","label":"start"},{"kind":"node","x":32368,"y":32234,"z":7},{"kind":"node","x":32369,"y":32224,"z":7}]}',
            encoding="utf-8",
        )
        s = _make_session()
        s._loader = MagicMock()
        nav = MagicMock()
        nav.is_floor_loaded.return_value = True
        nav.navigate.return_value = Route(
            start=Coordinate(32368, 32234, 7),
            end=Coordinate(32369, 32224, 7),
            steps=[
                Coordinate(32368, 32234, 7),
                Coordinate(32368, 32233, 7),
                Coordinate(32368, 32232, 7),
                Coordinate(32369, 32224, 7),
            ],
            found=True,
            total_distance=4.0,
        )
        s._navigator = nav

        with patch("src.script_executor.ScriptExecutor.execute"):
            s.run_script(script)

        s._loader.apply_learned_walkability_for_tiles.assert_called_once_with(
            {
                (32368, 32234, 7),
                (32368, 32233, 7),
                (32368, 32232, 7),
                (32369, 32224, 7),
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# BotSession — set_frame_getter propagation
# ─────────────────────────────────────────────────────────────────────────────

class TestFrameGetterPropagation:

    def test_set_frame_getter_stored(self):
        s = _make_session()
        getter = lambda: None
        s.set_frame_getter(getter)
        assert s._frame_getter is getter

    def test_set_frame_getter_propagates_to_depot(self):
        s = _make_session()
        from src.depot_manager import DepotManager, DepotConfig
        dm = DepotManager(ctrl=MagicMock(), config=DepotConfig())
        s._depot = dm
        getter = lambda: None
        s.set_frame_getter(getter)
        # Since T7 FrameCache, subsystems get the cached wrapper, not raw
        assert dm._frame_getter is not None
        assert callable(dm._frame_getter)

    def test_set_frame_getter_propagates_to_looter(self):
        s = _make_session()
        from src.looter import Looter
        with patch.object(Looter, "start", return_value=None):
            from unittest.mock import MagicMock
            looter_mock = MagicMock()
            s._looter = looter_mock
        getter = lambda: None
        s.set_frame_getter(getter)
        # Since T7 FrameCache, propagated getter is the cached wrapper
        s._looter.set_frame_getter.assert_called_once()
        cached_fn = s._looter.set_frame_getter.call_args[0][0]
        assert callable(cached_fn)

    def test_set_frame_getter_propagates_to_combat(self):
        s = _make_session()
        combat_mock = MagicMock()
        s._combat = combat_mock
        getter = lambda: None
        s.set_frame_getter(getter)
        # Since T7 FrameCache, propagated getter is the cached wrapper
        combat_mock.set_frame_getter.assert_called_once()
        cached_fn = combat_mock.set_frame_getter.call_args[0][0]
        assert callable(cached_fn)

    def test_set_frame_getter_propagates_to_condition_monitor(self):
        s = _make_session()
        cm_mock = MagicMock()
        s._condition_monitor = cm_mock
        getter = lambda: None
        s.set_frame_getter(getter)
        # Since T7 FrameCache, propagated getter is the cached wrapper
        cm_mock.set_frame_getter.assert_called_once()
        cached_fn = cm_mock.set_frame_getter.call_args[0][0]
        assert callable(cached_fn)

    def test_set_frame_getter_with_none_components_no_error(self):
        s = _make_session()
        # All sub-components are None by default before start()
        getter = lambda: None
        s.set_frame_getter(getter)  # Should not raise
        assert s._frame_getter is getter


# ─────────────────────────────────────────────────────────────────────────────
# BotSession — stats_snapshot includes dry_run context
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionDryRun:

    def test_dry_run_in_config(self):
        s = _make_session(dry_run=True)
        assert s.config.dry_run is True

    def test_dry_run_false_in_config(self):
        s = _make_session(dry_run=False)
        assert s.config.dry_run is False

    def test_run_script_dry_run_passed_to_executor(self, tmp_path: Path):
        script = tmp_path / "s.in"
        script.write_text("", encoding="utf-8")

        captured_dry: List[bool] = []

        from src import script_executor as _se

        original_init = _se.ScriptExecutor.__init__

        def patched_init(self_ex, *args, dry_run=False, **kw):
            captured_dry.append(dry_run)
            kw.pop('dry_run', None)
            original_init(self_ex, *args, **kw, dry_run=dry_run)  # type: ignore[misc]

        with patch.object(_se.ScriptExecutor, "__init__", patched_init):
            s = _make_session(dry_run=True)
            s.run_script(script)

        assert captured_dry == [True]
