"""
Tests for session lifecycle modules:
  - src/session_stop.py      — perform_session_shutdown
  - src/session_threads.py   — start_session_threads, ThreadStartupState
  - src/session_script.py    — script helpers, run_session_script
  - src/session_startup.py   — apply_smart_defaults, run_preflight_checks,
                               start_session, startup_subsystems helpers
  - src/session_runtime.py   — run_session_loop, _run_script_route_loop,
                               _run_waypoint_route_loop

100% offline — no OBS, no Tibia, no hardware.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from src import session_stop, session_threads, session_script, session_startup, session_runtime
from src.models import Coordinate
from src.session_threads import ThreadStartupState, start_session_threads
from src.session_stop import (
    _stop_component,
    _close_optional,
    _join_optional_thread,
    perform_session_shutdown,
)


# ─────────────────────────────────────────────────────────────────────────────
# Common helpers
# ─────────────────────────────────────────────────────────────────────────────

def _noop(*a, **kw):
    pass


def _coord(x=32369, y=32241, z=7):
    return Coordinate(x, y, z)


def _mock_thread():
    t = MagicMock(spec=threading.Thread)
    t.is_alive.return_value = False
    return t


# ─────────────────────────────────────────────────────────────────────────────
# session_stop tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStopComponent:
    def test_noop_on_none(self):
        logs = []
        _stop_component(name="x", component=None, log_fn=logs.append)
        assert logs == []

    def test_calls_stop_on_component(self):
        comp = MagicMock()
        _stop_component(name="healer", component=comp, log_fn=_noop)
        comp.stop.assert_called_once()

    def test_logs_exception_on_error(self):
        comp = MagicMock()
        comp.stop.side_effect = RuntimeError("stop failed")
        logs = []
        _stop_component(name="healer", component=comp, log_fn=logs.append)
        assert any("healer" in m for m in logs)
        assert any("stop failed" in m for m in logs)


class TestCloseOptional:
    def test_noop_on_none(self):
        logs = []
        _close_optional(close_fn=None, label="x", log_fn=logs.append)
        assert logs == []

    def test_calls_close_fn(self):
        called = []
        _close_optional(close_fn=lambda: called.append(1), label="X", log_fn=_noop)
        assert called == [1]

    def test_logs_exception(self):
        logs = []
        def bad_close():
            raise RuntimeError("close failed")
        _close_optional(close_fn=bad_close, label="pico", log_fn=logs.append)
        assert any("pico" in m for m in logs)


class TestJoinOptionalThread:
    def test_noop_on_none(self):
        current = threading.current_thread()
        _join_optional_thread(thread=None, current_thread=current, timeout=1)

    def test_noop_when_same_thread(self):
        t = threading.current_thread()
        _join_optional_thread(thread=t, current_thread=t, timeout=1)

    def test_joins_different_thread(self):
        current = threading.current_thread()
        other = _mock_thread()
        _join_optional_thread(thread=other, current_thread=current, timeout=0.1)
        other.join.assert_called_once_with(timeout=0.1)


class TestPerformSessionShutdown:
    def _make_stats(self):
        return {"routes_completed": 3, "heal_fired": 10, "mana_fired": 5}

    def test_calls_abort_on_executor(self):
        executor = MagicMock()
        logs = []
        perform_session_shutdown(
            executor=executor,
            stoppable_components=[],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        executor.abort.assert_called_once()

    def test_none_executor_ok(self):
        logs = []
        perform_session_shutdown(
            executor=None,
            stoppable_components=[],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        assert any("Session stopped" in m for m in logs)

    def test_stops_all_stoppable_components(self):
        healer = MagicMock()
        looter = MagicMock()
        logs = []
        perform_session_shutdown(
            executor=None,
            stoppable_components=[("healer", healer), ("looter", looter)],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        healer.stop.assert_called_once()
        looter.stop.assert_called_once()

    def test_stops_none_components_gracefully(self):
        logs = []
        perform_session_shutdown(
            executor=None,
            stoppable_components=[("healer", None), ("looter", None)],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        assert any("Session stopped" in m for m in logs)

    def test_calls_ctrl_close_if_available(self):
        ctrl = MagicMock()
        ctrl.close = MagicMock()
        logs = []
        perform_session_shutdown(
            executor=None,
            stoppable_components=[],
            ctrl=ctrl,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        ctrl.close.assert_called_once()

    def test_pico_close_called(self):
        pico = MagicMock()
        pico.close = MagicMock()
        logs = []
        perform_session_shutdown(
            executor=None,
            stoppable_components=[],
            ctrl=None,
            pico=pico,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        pico.close.assert_called_once()

    def test_calls_save_fns(self):
        save_stats = MagicMock()
        save_chk = MagicMock()
        perform_session_shutdown(
            executor=None,
            stoppable_components=[],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=_noop,
            save_stats_fn=save_stats,
            save_checkpoint_fn=save_chk,
        )
        save_stats.assert_called_once()
        save_chk.assert_called_once()

    def test_session_stats_summary_logged(self):
        sess_stats = MagicMock()
        sess_stats.summary_text.return_value = "kills=5"
        logs = []
        perform_session_shutdown(
            executor=None,
            stoppable_components=[],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=sess_stats,
            path_viz=None,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        assert any("kills=5" in m for m in logs)

    def test_path_viz_save_called(self):
        path_viz = MagicMock()
        logs = []
        perform_session_shutdown(
            executor=None,
            stoppable_components=[],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=path_viz,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        path_viz.save_cumulative.assert_called_once()

    def test_path_viz_save_exception_is_logged(self):
        path_viz = MagicMock()
        path_viz.save_cumulative.side_effect = RuntimeError("save failed")
        logs = []
        perform_session_shutdown(
            executor=None,
            stoppable_components=[],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=path_viz,
            log_fn=logs.append,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
        )
        assert any("PathVisualizer save error" in m for m in logs)

    def test_joins_threads(self):
        current = threading.current_thread()
        main_t = _mock_thread()
        watchdog_t = _mock_thread()
        ww_t = _mock_thread()
        perform_session_shutdown(
            executor=None,
            stoppable_components=[],
            ctrl=None,
            pico=None,
            main_thread=main_t,
            watchdog_thread=watchdog_t,
            window_watchdog_thread=ww_t,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=_noop,
            save_stats_fn=_noop,
            save_checkpoint_fn=_noop,
            current_thread_fn=lambda: current,
        )
        main_t.join.assert_called_once_with(timeout=10)
        watchdog_t.join.assert_called_once_with(timeout=3)
        ww_t.join.assert_called_once_with(timeout=2)

    def test_idempotent_double_shutdown(self):
        """Calling shutdown twice should not crash — both save_fns called twice."""
        save_stats = MagicMock()
        save_chk = MagicMock()
        kwargs = dict(
            executor=None,
            stoppable_components=[],
            ctrl=None,
            pico=None,
            main_thread=None,
            watchdog_thread=None,
            window_watchdog_thread=None,
            stats=self._make_stats(),
            session_stats=None,
            path_viz=None,
            log_fn=_noop,
            save_stats_fn=save_stats,
            save_checkpoint_fn=save_chk,
        )
        perform_session_shutdown(**kwargs)
        perform_session_shutdown(**kwargs)
        assert save_stats.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# session_threads tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_thread_config(
    *,
    frame_window: str = "",
    dry_run: bool = False,
    watchdog_timeout: float = 0.0,
    start_delay: float = 0.0,
):
    return SimpleNamespace(
        frame_window=frame_window,
        dry_run=dry_run,
        watchdog_timeout=watchdog_timeout,
        start_delay=start_delay,
    )


class TestThreadStartupState:
    def test_default_state(self):
        state = ThreadStartupState()
        assert state.ww_hwnds == {}
        assert state.ww_thread is None
        assert state.watchdog_thread is None
        assert state.main_thread is None
        assert state.last_move_time == pytest.approx(0.0)


class TestStartSessionThreads:
    def _thread_factory(self):
        """Returns a factory that creates mock threads that track start() calls."""
        created = []
        def factory(target=None, daemon=False):
            t = MagicMock(spec=threading.Thread)
            t.start = MagicMock()
            created.append(t)
            return t
        return factory, created

    def test_starts_main_thread(self):
        factory, created = self._thread_factory()
        state = start_session_threads(
            config=_make_thread_config(),
            raw_ctrl=None,
            arduino=None,
            log_fn=_noop,
            window_watchdog_target=_noop,
            watchdog_target=_noop,
            run_loop_target=_noop,
            wait_for_start_position_lock=lambda: True,
            thread_factory=factory,
            monotonic_fn=lambda: 0.0,
            sleep_fn=_noop,
            random_uniform_fn=lambda a, b: 1.0,
            find_window_fn=lambda n: None,
        )
        assert state.main_thread is not None
        state.main_thread.start.assert_called_once()

    def test_raises_when_start_position_lock_fails(self):
        factory, _ = self._thread_factory()
        with pytest.raises(RuntimeError, match="Start position lock failed"):
            start_session_threads(
                config=_make_thread_config(),
                raw_ctrl=None,
                arduino=None,
                log_fn=_noop,
                window_watchdog_target=_noop,
                watchdog_target=_noop,
                run_loop_target=_noop,
                wait_for_start_position_lock=lambda: False,
                thread_factory=factory,
                monotonic_fn=lambda: 0.0,
                sleep_fn=_noop,
                random_uniform_fn=lambda a, b: 1.0,
                find_window_fn=lambda n: None,
            )

    def test_watchdog_thread_started_when_timeout_positive(self):
        factory, created = self._thread_factory()
        state = start_session_threads(
            config=_make_thread_config(watchdog_timeout=30.0),
            raw_ctrl=None,
            arduino=None,
            log_fn=_noop,
            window_watchdog_target=_noop,
            watchdog_target=_noop,
            run_loop_target=_noop,
            wait_for_start_position_lock=lambda: True,
            thread_factory=factory,
            monotonic_fn=lambda: 42.0,
            sleep_fn=_noop,
            random_uniform_fn=lambda a, b: 1.0,
            find_window_fn=lambda n: None,
        )
        assert state.watchdog_thread is not None
        assert state.last_move_time == pytest.approx(42.0)

    def test_watchdog_not_started_when_timeout_zero(self):
        factory, _ = self._thread_factory()
        state = start_session_threads(
            config=_make_thread_config(watchdog_timeout=0.0),
            raw_ctrl=None,
            arduino=None,
            log_fn=_noop,
            window_watchdog_target=_noop,
            watchdog_target=_noop,
            run_loop_target=_noop,
            wait_for_start_position_lock=lambda: True,
            thread_factory=factory,
            monotonic_fn=lambda: 0.0,
            sleep_fn=_noop,
            random_uniform_fn=lambda a, b: 1.0,
            find_window_fn=lambda n: None,
        )
        assert state.watchdog_thread is None

    def test_window_watchdog_started_when_hwnd_available(self):
        raw_ctrl = MagicMock()
        raw_ctrl.is_connected.return_value = True
        raw_ctrl.hwnd = 0xDEAD  # valid non-zero hwnd
        factory, _ = self._thread_factory()
        state = start_session_threads(
            config=_make_thread_config(dry_run=False),
            raw_ctrl=raw_ctrl,
            arduino=None,
            log_fn=_noop,
            window_watchdog_target=_noop,
            watchdog_target=_noop,
            run_loop_target=_noop,
            wait_for_start_position_lock=lambda: True,
            thread_factory=factory,
            monotonic_fn=lambda: 0.0,
            sleep_fn=_noop,
            random_uniform_fn=lambda a, b: 1.0,
            find_window_fn=lambda n: None,
        )
        assert state.ww_thread is not None
        assert "Tibia" in state.ww_hwnds

    def test_window_watchdog_not_started_in_dry_run(self):
        raw_ctrl = MagicMock()
        raw_ctrl.is_connected.return_value = True
        raw_ctrl.hwnd = 0xDEAD
        factory, _ = self._thread_factory()
        state = start_session_threads(
            config=_make_thread_config(dry_run=True),
            raw_ctrl=raw_ctrl,
            arduino=None,
            log_fn=_noop,
            window_watchdog_target=_noop,
            watchdog_target=_noop,
            run_loop_target=_noop,
            wait_for_start_position_lock=lambda: True,
            thread_factory=factory,
            monotonic_fn=lambda: 0.0,
            sleep_fn=_noop,
            random_uniform_fn=lambda a, b: 1.0,
            find_window_fn=lambda n: None,
        )
        assert state.ww_thread is None

    def test_arduino_failover_configured(self):
        raw_ctrl = MagicMock()
        raw_ctrl.is_connected.return_value = False
        raw_ctrl.hwnd = 0
        arduino = MagicMock()
        factory, _ = self._thread_factory()
        logs = []
        start_session_threads(
            config=_make_thread_config(),
            raw_ctrl=raw_ctrl,
            arduino=arduino,
            log_fn=logs.append,
            window_watchdog_target=_noop,
            watchdog_target=_noop,
            run_loop_target=_noop,
            wait_for_start_position_lock=lambda: True,
            thread_factory=factory,
            monotonic_fn=lambda: 0.0,
            sleep_fn=_noop,
            random_uniform_fn=lambda a, b: 1.0,
            find_window_fn=lambda n: None,
        )
        raw_ctrl.set_arduino_failover.assert_called_once_with(arduino)
        assert raw_ctrl._using_arduino_failover is True
        assert any("Arduino" in m for m in logs)

    def test_start_delay_calls_sleep(self):
        slept = []
        factory, _ = self._thread_factory()
        start_session_threads(
            config=_make_thread_config(start_delay=2.0),
            raw_ctrl=None,
            arduino=None,
            log_fn=_noop,
            window_watchdog_target=_noop,
            watchdog_target=_noop,
            run_loop_target=_noop,
            wait_for_start_position_lock=lambda: True,
            thread_factory=factory,
            monotonic_fn=lambda: 0.0,
            sleep_fn=lambda t: slept.append(t),
            random_uniform_fn=lambda a, b: 1.0,
            find_window_fn=lambda n: None,
        )
        assert len(slept) == 1
        assert slept[0] == pytest.approx(2.0)

    def test_frame_window_hwnd_added(self):
        window_info = SimpleNamespace(hwnd=0xABCD)
        factory, _ = self._thread_factory()
        state = start_session_threads(
            config=_make_thread_config(frame_window="OBS Projector"),
            raw_ctrl=None,
            arduino=None,
            log_fn=_noop,
            window_watchdog_target=_noop,
            watchdog_target=_noop,
            run_loop_target=_noop,
            wait_for_start_position_lock=lambda: True,
            thread_factory=factory,
            monotonic_fn=lambda: 0.0,
            sleep_fn=_noop,
            random_uniform_fn=lambda a, b: 1.0,
            find_window_fn=lambda n: window_info,
        )
        assert "OBS Projector" in state.ww_hwnds
        assert state.ww_hwnds["OBS Projector"] == 0xABCD


# ─────────────────────────────────────────────────────────────────────────────
# session_script tests
# ─────────────────────────────────────────────────────────────────────────────

def _instruction(kind="node", coord=None, label=None):
    sc = None
    if coord is not None:
        sc = SimpleNamespace(x=coord.x, y=coord.y, z=coord.z)
        # Make it behave like ScriptCoord.to_tibia_coord()
        sc = coord  # Coordinate already works
    return SimpleNamespace(kind=kind, coord=sc, label=label)


class TestParseJsonScript:
    def test_raises_on_missing_script_array(self, tmp_path: Path):
        p = tmp_path / "r.json"
        p.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="no 'script' array"):
            session_script._parse_json_script(
                route_path=p,
                config=SimpleNamespace(
                    dry_run=False,
                    input_method="postmessage",
                    jitter_pct=0.0,
                    step_interval=0.5,
                    position_source="minimap",
                ),
                ctrl=None,
                healer=None,
                get_position=lambda: None,
                set_position=lambda v: None,
                log_fn=_noop,
                json_script_parser=lambda r: r,
            )

    def test_parses_valid_json_script(self, tmp_path: Path):
        p = tmp_path / "r.json"
        data = {"script": [{"kind": "action", "action": "end"}]}
        p.write_text(json.dumps(data), encoding="utf-8")
        prep = session_script._parse_json_script(
            route_path=p,
            config=SimpleNamespace(
                dry_run=False,
                input_method="postmessage",
                jitter_pct=0.0,
                step_interval=0.5,
                position_source="minimap",
            ),
            ctrl=None,
            healer=None,
            get_position=lambda: None,
            set_position=lambda v: None,
            log_fn=_noop,
            json_script_parser=lambda r: r,
        )
        assert prep.instructions == [{"kind": "action", "action": "end"}]
        assert prep.dry_run is False

    def test_session_overrides_applied(self, tmp_path: Path):
        p = tmp_path / "r.json"
        data = {
            "script": [{"kind": "end"}],
            "session": {
                "input_method": "mouse",
                "jitter_pct": 0.15,
                "step_interval": 0.8,
                "dry_run": True,
            },
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        ctrl = SimpleNamespace(input_method="postmessage", jitter_pct=0.0)
        prep = session_script._parse_json_script(
            route_path=p,
            config=SimpleNamespace(
                dry_run=False,
                input_method="postmessage",
                jitter_pct=0.0,
                step_interval=0.5,
                position_source="minimap",
            ),
            ctrl=ctrl,
            healer=None,
            get_position=lambda: None,
            set_position=lambda v: None,
            log_fn=_noop,
            json_script_parser=lambda r: r,
        )
        assert prep.dry_run is True
        assert prep.step_interval == pytest.approx(0.8)
        assert ctrl.input_method == "postmessage"  # "mouse" is normalized to "postmessage"
        assert ctrl.jitter_pct == pytest.approx(0.15)

    def test_meta_start_coord_sets_position(self, tmp_path: Path):
        p = tmp_path / "r.json"
        data = {
            "script": [{"kind": "end"}],
            "_meta": {"start_coord": {"x": 32369, "y": 32241, "z": 7}},
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        positions = []
        session_script._parse_json_script(
            route_path=p,
            config=SimpleNamespace(
                dry_run=False,
                input_method="postmessage",
                jitter_pct=0.0,
                step_interval=0.5,
                position_source="none",
            ),
            ctrl=None,
            healer=None,
            get_position=lambda: None,
            set_position=positions.append,
            log_fn=_noop,
            json_script_parser=lambda r: r,
        )
        assert len(positions) == 1
        assert positions[0] == Coordinate(32369, 32241, 7)

    def test_meta_start_coord_as_list(self, tmp_path: Path):
        p = tmp_path / "r.json"
        data = {
            "script": [{"kind": "end"}],
            "_meta": {"start_coord": [32369, 32241, 7]},
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        positions = []
        session_script._parse_json_script(
            route_path=p,
            config=SimpleNamespace(
                dry_run=False,
                input_method="postmessage",
                jitter_pct=0.0,
                step_interval=0.5,
                position_source="none",
            ),
            ctrl=None,
            healer=None,
            get_position=lambda: None,
            set_position=positions.append,
            log_fn=_noop,
            json_script_parser=lambda r: r,
        )
        assert len(positions) == 1
        assert positions[0] == Coordinate(32369, 32241, 7)

    def test_healer_config_overrides_applied(self, tmp_path: Path):
        p = tmp_path / "r.json"
        data = {
            "script": [{"kind": "end"}],
            "session": {
                "heal_hp_pct": 80,
                "mana_threshold_pct": 50,
                "heal_hotkey_vk": 0x70,
            },
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        import dataclasses

        @dataclasses.dataclass
        class _HealCfg:
            hp_threshold_pct: int = 50
            mp_threshold_pct: int = 30
            heal_hotkey_vk: int = 0

        healer = SimpleNamespace(_cfg=_HealCfg())
        session_script._parse_json_script(
            route_path=p,
            config=SimpleNamespace(
                dry_run=False,
                input_method="postmessage",
                jitter_pct=0.0,
                step_interval=0.5,
                position_source="none",
            ),
            ctrl=None,
            healer=healer,
            get_position=lambda: None,
            set_position=lambda v: None,
            log_fn=_noop,
            json_script_parser=lambda r: r,
        )
        assert healer._cfg.hp_threshold_pct == 80
        assert healer._cfg.mp_threshold_pct == 50


class TestParseScriptSource:
    def test_non_json_parses_with_parse_file_fn(self, tmp_path: Path):
        p = tmp_path / "route.in"
        p.write_text("node 100 200 7\n", encoding="utf-8")
        parsed = []
        prep = session_script._parse_script_source(
            path=p,
            config=SimpleNamespace(
                dry_run=False,
                input_method="postmessage",
                jitter_pct=0.0,
                step_interval=0.5,
                position_source="none",
            ),
            ctrl=None,
            healer=None,
            get_position=lambda: None,
            set_position=lambda v: None,
            log_fn=_noop,
            resolve_route_fn=lambda path: Path(path),
            parse_file_fn=lambda fp: ["node_instruction"],
            json_script_parser=lambda r: r,
        )
        assert prep.instructions == ["node_instruction"]

    def test_json_delegates_to_parse_json_script(self, tmp_path: Path):
        p = tmp_path / "route.json"
        data = {"script": [{"kind": "end"}]}
        p.write_text(json.dumps(data), encoding="utf-8")
        prep = session_script._parse_script_source(
            path=p,
            config=SimpleNamespace(
                dry_run=False,
                input_method="postmessage",
                jitter_pct=0.0,
                step_interval=0.5,
                position_source="none",
            ),
            ctrl=None,
            healer=None,
            get_position=lambda: None,
            set_position=lambda v: None,
            log_fn=_noop,
            resolve_route_fn=lambda path: Path(path),
            parse_file_fn=lambda fp: [],
            json_script_parser=lambda r: r,
        )
        assert prep.instructions == [{"kind": "end"}]


class TestResolveResumeIndex:
    def test_returns_zero_when_no_checkpoint(self):
        idx = session_script._resolve_resume_index(
            route_identity="route.json",
            instruction_count=10,
            load_checkpoint_fn=lambda: None,
            log_fn=_noop,
        )
        assert idx == 0

    def test_returns_zero_when_checkpoint_doesnt_match_route(self):
        chk = MagicMock()
        chk.matches_route.return_value = False
        idx = session_script._resolve_resume_index(
            route_identity="route.json",
            instruction_count=10,
            load_checkpoint_fn=lambda: chk,
            log_fn=_noop,
        )
        assert idx == 0

    def test_returns_zero_when_route_mode_not_script(self):
        chk = MagicMock()
        chk.matches_route.return_value = True
        chk.extra = {"route_mode": "waypoint"}
        idx = session_script._resolve_resume_index(
            route_identity="route.json",
            instruction_count=10,
            load_checkpoint_fn=lambda: chk,
            log_fn=_noop,
        )
        assert idx == 0

    def test_returns_saved_index(self):
        chk = MagicMock()
        chk.matches_route.return_value = True
        chk.extra = {"route_mode": "script", "script_resume_instruction_index": 5}
        logs = []
        idx = session_script._resolve_resume_index(
            route_identity="route.json",
            instruction_count=10,
            load_checkpoint_fn=lambda: chk,
            log_fn=logs.append,
        )
        assert idx == 5
        assert any("Resuming" in m for m in logs)

    def test_clamps_index_to_valid_range(self):
        chk = MagicMock()
        chk.matches_route.return_value = True
        chk.extra = {"route_mode": "script", "script_resume_instruction_index": 999}
        idx = session_script._resolve_resume_index(
            route_identity="route.json",
            instruction_count=3,
            load_checkpoint_fn=lambda: chk,
            log_fn=_noop,
        )
        assert idx <= 2


class TestApplyScriptRegions:
    def test_adds_blocked_regions(self):
        executor = MagicMock()
        executor._blocked_pixels = []
        session_script._apply_script_regions(
            executor=executor,
            blocked_regions=[{"x_min": 1, "x_max": 10, "y_min": 1, "y_max": 10, "z": 7}],
            walkable_overrides=[],
            log_fn=_noop,
        )
        executor.add_blocked_region.assert_called_once_with(
            x_min=1, x_max=10, y_min=1, y_max=10, z=7
        )

    def test_adds_walkable_overrides(self):
        executor = MagicMock()
        executor._blocked_pixels = []
        executor.force_walkable_region.return_value = 5
        session_script._apply_script_regions(
            executor=executor,
            blocked_regions=[],
            walkable_overrides=[{"x_min": 5, "x_max": 15, "y_min": 5, "y_max": 15, "z": 7}],
            log_fn=_noop,
        )
        executor.force_walkable_region.assert_called_once()

    def test_skips_invalid_blocked_region(self):
        executor = MagicMock()
        executor._blocked_pixels = []
        logs = []
        session_script._apply_script_regions(
            executor=executor,
            blocked_regions=[{"x_min": "bad", "x_max": 10, "y_min": 1, "y_max": 10}],
            walkable_overrides=[],
            log_fn=logs.append,
        )
        assert any("Invalid blocked_region" in m for m in logs)

    def test_skips_non_dict_regions(self):
        executor = MagicMock()
        executor._blocked_pixels = []
        session_script._apply_script_regions(
            executor=executor,
            blocked_regions=["not a dict"],
            walkable_overrides=[],
            log_fn=_noop,
        )
        executor.add_blocked_region.assert_not_called()


class TestScriptMovementPoints:
    def test_returns_movement_coords(self):
        c1 = Coordinate(100, 200, 7)
        c2 = Coordinate(101, 201, 7)
        instr1 = SimpleNamespace(kind="node", coord=SimpleNamespace(
            x=c1.x, y=c1.y, z=c1.z,
            to_tibia_coord=lambda: c1
        ))
        instr2 = SimpleNamespace(kind="label", coord=None)
        instr3 = SimpleNamespace(kind="stand", coord=SimpleNamespace(
            x=c2.x, y=c2.y, z=c2.z,
            to_tibia_coord=lambda: c2
        ))
        points = session_script.script_movement_points([instr1, instr2, instr3])
        assert len(points) == 2
        assert points[0] == (0, c1)
        assert points[1] == (2, c2)

    def test_skips_label_instructions(self):
        label = SimpleNamespace(kind="label", coord=None)
        points = session_script.script_movement_points([label])
        assert points == []

    def test_skips_non_movement_kinds(self):
        instr = SimpleNamespace(kind="wait", coord=Coordinate(100, 200, 7))
        points = session_script.script_movement_points([instr])
        assert points == []


class TestApplyLearnedWalkability:
    def test_noop_when_no_loader(self):
        session_script._apply_learned_walkability(
            loader=None,
            instructions=[],
            collect_route_critical_tiles_fn=lambda i: set(),
            log_fn=_noop,
        )

    def test_calls_apply_on_loader(self):
        loader = MagicMock()
        loader.apply_learned_walkability_for_tiles.return_value = 3
        logs = []
        session_script._apply_learned_walkability(
            loader=loader,
            instructions=[],
            collect_route_critical_tiles_fn=lambda i: {(100, 200, 7)},
            log_fn=logs.append,
        )
        loader.apply_learned_walkability_for_tiles.assert_called_once_with({(100, 200, 7)})
        assert any("3 tiles" in m for m in logs)

    def test_handles_loader_exception(self):
        loader = MagicMock()
        loader.apply_learned_walkability_for_tiles.side_effect = RuntimeError("db error")
        logs = []
        session_script._apply_learned_walkability(
            loader=loader,
            instructions=[],
            collect_route_critical_tiles_fn=lambda i: {(100, 200, 7)},
            log_fn=logs.append,
        )
        assert any("Learned walkability load error" in m for m in logs)


class TestWireLootCounter:
    def test_returns_none_when_no_looter(self):
        result = session_script._wire_loot_counter(looter=None, get_executor=lambda: None)
        assert result is None

    def test_wires_callback(self):
        looter = SimpleNamespace(on_item_looted=None)
        original_cb = object()
        looter.on_item_looted = original_cb

        executor = MagicMock()
        get_exec = lambda: executor

        prev = session_script._wire_loot_counter(looter=looter, get_executor=get_exec)
        assert prev is original_cb
        assert looter.on_item_looted is not original_cb

    def test_wired_callback_calls_executor(self):
        looter = SimpleNamespace(on_item_looted=None)
        executor = MagicMock()
        session_script._wire_loot_counter(looter=looter, get_executor=lambda: executor)
        # Invoke the newly-wired callback
        looter.on_item_looted("sword", 1)
        executor.increment_item_count.assert_called_once_with("sword", 1)


class TestFinalizeScriptRun:
    def test_clears_checkpoint_on_normal_stop(self):
        executor = MagicMock()
        executor.stop_reason = "completed"
        executor.resume_instruction_index = 10
        executor.last_confirmed_node_index = 9
        executor._blocked_pixels = []
        executor._preblocked_count = 0

        save = MagicMock()
        clear = MagicMock()
        set_exec = MagicMock()

        session_script._finalize_script_run(
            executor=executor,
            route_identity="route.json",
            loader=None,
            looter=None,
            previous_loot_callback=None,
            get_path_viz=lambda: None,
            set_executor=set_exec,
            save_checkpoint_fn=save,
            clear_checkpoint_fn=clear,
            log_fn=_noop,
        )
        clear.assert_called_once()
        save.assert_not_called()
        set_exec.assert_called_once_with(None)

    def test_saves_checkpoint_on_movement_failed(self):
        executor = MagicMock()
        executor.stop_reason = "movement_failed"
        executor.resume_instruction_index = 5
        executor.last_confirmed_node_index = 4
        executor._blocked_pixels = []
        executor._preblocked_count = 0

        save = MagicMock()
        clear = MagicMock()

        session_script._finalize_script_run(
            executor=executor,
            route_identity="route.json",
            loader=None,
            looter=None,
            previous_loot_callback=None,
            get_path_viz=lambda: None,
            set_executor=MagicMock(),
            save_checkpoint_fn=save,
            clear_checkpoint_fn=clear,
            log_fn=_noop,
        )
        save.assert_called_once()
        clear.assert_not_called()

    def test_restores_loot_callback(self):
        executor = MagicMock()
        executor.stop_reason = "completed"
        executor._blocked_pixels = []
        executor._preblocked_count = 0
        executor.resume_instruction_index = 0
        executor.last_confirmed_node_index = 0

        looter = SimpleNamespace(on_item_looted="new_cb")
        orig_cb = "orig_cb"

        session_script._finalize_script_run(
            executor=executor,
            route_identity="route.json",
            loader=None,
            looter=looter,
            previous_loot_callback=orig_cb,
            get_path_viz=lambda: None,
            set_executor=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            clear_checkpoint_fn=MagicMock(),
            log_fn=_noop,
        )
        assert looter.on_item_looted == orig_cb


# ─────────────────────────────────────────────────────────────────────────────
# session_startup tests
# ─────────────────────────────────────────────────────────────────────────────

class TestApplySmartDefaults:
    def test_enables_pvp_detector_when_skulls_exist(self, tmp_path: Path):
        skulls = tmp_path / "skulls"
        skulls.mkdir()
        (skulls / "skull.png").write_bytes(b"")

        session_obj = SimpleNamespace(
            _cfg=SimpleNamespace(pvp_detector=False, monitor_conditions=False, adaptive_roi=False)
        )
        enabled = session_startup.apply_smart_defaults(
            session=session_obj, templates_base=tmp_path
        )
        assert "pvp_detector" in enabled
        assert session_obj._cfg.pvp_detector is True

    def test_enables_conditions_when_conditions_exist(self, tmp_path: Path):
        cond = tmp_path / "conditions"
        cond.mkdir()
        (cond / "cond.png").write_bytes(b"")
        session_obj = SimpleNamespace(
            _cfg=SimpleNamespace(pvp_detector=False, monitor_conditions=False, adaptive_roi=False)
        )
        enabled = session_startup.apply_smart_defaults(
            session=session_obj, templates_base=tmp_path
        )
        assert "monitor_conditions" in enabled

    def test_enables_adaptive_roi_when_anchors_exist(self, tmp_path: Path):
        anchors = tmp_path / "anchors"
        anchors.mkdir()
        (anchors / "anchor.png").write_bytes(b"")
        session_obj = SimpleNamespace(
            _cfg=SimpleNamespace(pvp_detector=False, monitor_conditions=False, adaptive_roi=False)
        )
        enabled = session_startup.apply_smart_defaults(
            session=session_obj, templates_base=tmp_path
        )
        assert "adaptive_roi" in enabled

    def test_skips_already_enabled(self, tmp_path: Path):
        skulls = tmp_path / "skulls"
        skulls.mkdir()
        (skulls / "skull.png").write_bytes(b"")
        session_obj = SimpleNamespace(
            _cfg=SimpleNamespace(pvp_detector=True, monitor_conditions=False, adaptive_roi=False)
        )
        enabled = session_startup.apply_smart_defaults(
            session=session_obj, templates_base=tmp_path
        )
        assert "pvp_detector" not in enabled

    def test_returns_empty_when_no_templates(self, tmp_path: Path):
        session_obj = SimpleNamespace(
            _cfg=SimpleNamespace(pvp_detector=False, monitor_conditions=False, adaptive_roi=False)
        )
        enabled = session_startup.apply_smart_defaults(
            session=session_obj, templates_base=tmp_path
        )
        assert enabled == []


class TestRunPreflightChecks:
    def test_skips_in_dry_run(self):
        session_obj = SimpleNamespace(
            _cfg=SimpleNamespace(dry_run=True),
            _log=lambda m: None,
        )
        preflight_fn = MagicMock()
        session_startup.run_preflight_checks(
            session=session_obj, run_preflight_fn=preflight_fn
        )
        preflight_fn.assert_not_called()

    def test_passes_when_ok(self):
        result = MagicMock()
        result.ok = True
        result.results = [object(), object()]

        session_obj = SimpleNamespace(
            _cfg=SimpleNamespace(dry_run=False, input_method="postmessage"),
            _log=lambda m: None,
        )
        logs = []
        session_obj._log = logs.append
        session_startup.run_preflight_checks(
            session=session_obj,
            run_preflight_fn=lambda cfg, skip_driver, log_fn: result,
        )
        assert any("checks passed" in m for m in logs)

    def test_raises_when_failed(self):
        failure = MagicMock()
        failure.__str__ = lambda self: "driver missing"

        result = MagicMock()
        result.ok = False
        result.summary.return_value = "FAILED"
        result.failures = [failure]

        session_obj = SimpleNamespace(
            _cfg=SimpleNamespace(dry_run=False, input_method="postmessage"),
            _set_running=MagicMock(),
        )
        logs = []
        session_obj._log = logs.append

        with pytest.raises(RuntimeError, match="Preflight FAILED"):
            session_startup.run_preflight_checks(
                session=session_obj,
                run_preflight_fn=lambda cfg, skip_driver, log_fn: result,
            )
        session_obj._set_running.assert_called_once_with(False)


# ─────────────────────────────────────────────────────────────────────────────
# session_runtime tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_runtime_config(
    *,
    route_file: str = "",
    loop_route: bool = False,
    dry_run: bool = False,
    depot_after_run: bool = False,
    resume_waypoint_index: int = 0,
):
    return SimpleNamespace(
        route_file=route_file,
        loop_route=loop_route,
        dry_run=dry_run,
        depot_after_run=depot_after_run,
        resume_waypoint_index=resume_waypoint_index,
    )


class TestRunSessionLoop:
    def test_idle_loop_when_no_route_file(self):
        running = [True, True, False]
        idx = [0]
        def is_running():
            v = running[idx[0]]
            idx[0] = min(idx[0] + 1, len(running) - 1)
            return v

        logs = []
        session_runtime.run_session_loop(
            config=_make_runtime_config(route_file=""),
            inc_routes_fn=lambda: None,
            event_bus=MagicMock(),
            break_scheduler=None,
            depot=None,
            depot_orchestrator=None,
            ctrl=None,
            frame_getter=None,
            get_position=lambda: None,
            is_running=is_running,
            set_running=MagicMock(),
            get_consecutive_errors=lambda: 0,
            set_consecutive_errors=MagicMock(),
            log_fn=logs.append,
            resolve_route_fn=lambda p: Path(p),
            run_script_fn=MagicMock(),
            load_waypoints_fn=MagicMock(),
            navigate_to_fn=MagicMock(),
            exec_route_fn=MagicMock(),
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=MagicMock(),
            format_traceback_fn=lambda: "",
            sleep_fn=lambda t: None,
            random_uniform_fn=lambda a, b: 0.5,
        )
        assert any("No route_file" in m for m in logs)

    def test_stops_when_route_file_not_found(self, tmp_path: Path):
        set_running = MagicMock()
        logs = []

        def resolve(p):
            raise FileNotFoundError(f"not found: {p}")

        session_runtime.run_session_loop(
            config=_make_runtime_config(route_file="missing.json"),
            inc_routes_fn=lambda: None,
            event_bus=MagicMock(),
            break_scheduler=None,
            depot=None,
            depot_orchestrator=None,
            ctrl=None,
            frame_getter=None,
            get_position=lambda: None,
            is_running=lambda: True,
            set_running=set_running,
            get_consecutive_errors=lambda: 0,
            set_consecutive_errors=MagicMock(),
            log_fn=logs.append,
            resolve_route_fn=resolve,
            run_script_fn=MagicMock(),
            load_waypoints_fn=MagicMock(),
            navigate_to_fn=MagicMock(),
            exec_route_fn=MagicMock(),
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=MagicMock(),
            format_traceback_fn=lambda: "",
            sleep_fn=lambda t: None,
            random_uniform_fn=lambda a, b: 0.5,
        )
        set_running.assert_called_with(False)

    def test_delegates_in_script_to_script_loop(self, tmp_path: Path):
        p = tmp_path / "route.in"
        p.write_text("node 100 200 7\n", encoding="utf-8")

        run_script = MagicMock()
        set_running = MagicMock()

        # is_running: True once to run the loop, then False
        calls = [True, False]
        ci = [0]
        def is_running():
            v = calls[ci[0]]
            ci[0] = min(ci[0] + 1, 1)
            return v

        session_runtime.run_session_loop(
            config=_make_runtime_config(route_file=str(p)),
            inc_routes_fn=lambda: None,
            event_bus=MagicMock(),
            break_scheduler=None,
            depot=None,
            depot_orchestrator=None,
            ctrl=None,
            frame_getter=None,
            get_position=lambda: None,
            is_running=is_running,
            set_running=set_running,
            get_consecutive_errors=lambda: 0,
            set_consecutive_errors=MagicMock(),
            log_fn=_noop,
            resolve_route_fn=lambda path: Path(path),
            run_script_fn=run_script,
            load_waypoints_fn=MagicMock(),
            navigate_to_fn=MagicMock(),
            exec_route_fn=MagicMock(),
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=MagicMock(),
            format_traceback_fn=lambda: "",
            sleep_fn=lambda t: None,
            random_uniform_fn=lambda a, b: 0.5,
        )
        run_script.assert_called_once_with(p)

    def test_json_script_detected_and_dispatched(self, tmp_path: Path):
        p = tmp_path / "route.json"
        data = {"script": [{"kind": "end"}], "session": {}}
        p.write_text(json.dumps(data), encoding="utf-8")

        run_script = MagicMock()
        calls = [True, False]
        ci = [0]
        def is_running():
            v = calls[ci[0]]
            ci[0] = min(ci[0] + 1, 1)
            return v

        session_runtime.run_session_loop(
            config=_make_runtime_config(route_file=str(p)),
            inc_routes_fn=lambda: None,
            event_bus=MagicMock(),
            break_scheduler=None,
            depot=None,
            depot_orchestrator=None,
            ctrl=None,
            frame_getter=None,
            get_position=lambda: None,
            is_running=is_running,
            set_running=MagicMock(),
            get_consecutive_errors=lambda: 0,
            set_consecutive_errors=MagicMock(),
            log_fn=_noop,
            resolve_route_fn=lambda path: Path(path),
            run_script_fn=run_script,
            load_waypoints_fn=MagicMock(),
            navigate_to_fn=MagicMock(),
            exec_route_fn=MagicMock(),
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=MagicMock(),
            format_traceback_fn=lambda: "",
            sleep_fn=lambda t: None,
            random_uniform_fn=lambda a, b: 0.5,
        )
        run_script.assert_called_once_with(p)


class TestRunScriptRouteLoop:
    def test_exits_after_single_run_when_no_loop(self):
        run_script = MagicMock()
        set_running = MagicMock()
        calls = [True, True]
        ci = [0]
        def is_running():
            v = calls[ci[0]] if ci[0] < len(calls) else False
            ci[0] += 1
            return v

        counter: list[int] = []
        session_runtime._run_script_route_loop(
            route_path=Path("route.in"),
            config=SimpleNamespace(loop_route=False, dry_run=False),
            json_peek=None,
            is_running=is_running,
            set_running=set_running,
            inc_routes_fn=lambda: counter.append(1),
            break_scheduler=None,
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            log_fn=_noop,
            run_script_fn=run_script,
        )
        run_script.assert_called_once()
        assert len(counter) == 1
        set_running.assert_called_with(False)

    def test_loops_when_loop_route_set(self):
        run_count = [0]
        set_running_calls = []

        def run_script(p):
            run_count[0] += 1

        run_limit = 3
        def is_running():
            return run_count[0] < run_limit

        counter: list[int] = []
        session_runtime._run_script_route_loop(
            route_path=Path("route.in"),
            config=SimpleNamespace(loop_route=True, dry_run=False),
            json_peek=None,
            is_running=is_running,
            set_running=set_running_calls.append,
            inc_routes_fn=lambda: counter.append(1),
            break_scheduler=None,
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            log_fn=_noop,
            run_script_fn=run_script,
        )
        assert run_count[0] == run_limit
        assert len(counter) == run_limit

    def test_stops_on_run_script_exception(self):
        set_running = MagicMock()

        def failing_script(p):
            raise RuntimeError("script crashed")

        logs = []
        session_runtime._run_script_route_loop(
            route_path=Path("route.in"),
            config=SimpleNamespace(loop_route=True, dry_run=False),
            json_peek=None,
            is_running=lambda: True,
            set_running=set_running,
            inc_routes_fn=lambda: None,
            break_scheduler=None,
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            log_fn=logs.append,
            run_script_fn=failing_script,
        )
        set_running.assert_called_with(False)
        assert any("run_script raised" in m for m in logs)


class TestRunWaypointRouteLoop:
    def _make_waypoints(self, n: int = 3):
        return [
            SimpleNamespace(coord=Coordinate(100 + i, 200, 7))
            for i in range(n)
        ]

    def _make_route(self, found: bool = True):
        from src.models import Route
        steps = [Coordinate(100 + i, 200, 7) for i in range(3)]
        return Route(start=steps[0], end=steps[-1], steps=steps, found=found)

    def test_exits_when_fewer_than_2_waypoints(self):
        set_running = MagicMock()
        logs = []
        session_runtime._run_waypoint_route_loop(
            waypoints=[SimpleNamespace(coord=Coordinate(100, 200, 7))],
            config=_make_runtime_config(),
            is_running=lambda: True,
            set_running=set_running,
            inc_routes_fn=lambda: None,
            event_bus=MagicMock(),
            break_scheduler=None,
            depot=None,
            depot_orchestrator=None,
            ctrl=None,
            frame_getter=None,
            get_position=lambda: None,
            log_fn=logs.append,
            get_consecutive_errors=lambda: 0,
            set_consecutive_errors=MagicMock(),
            navigate_to_fn=MagicMock(),
            exec_route_fn=MagicMock(),
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=MagicMock(),
            format_traceback_fn=lambda: "",
        )
        set_running.assert_called_with(False)
        assert any("fewer than 2" in m for m in logs)

    def test_runs_route_for_each_waypoint_pair(self):
        route = self._make_route(found=True)
        waypoints = self._make_waypoints(3)
        exec_route = MagicMock()
        save_chk = MagicMock()
        set_running = MagicMock()

        # is_running must return True enough times:
        # 1x for while-check + 2x for inner is_running() check per pair
        calls = [True] * 10
        ci = [0]
        def is_running():
            return True  # always True; loop_route=False breaks naturally

        session_runtime._run_waypoint_route_loop(
            waypoints=waypoints,
            config=_make_runtime_config(loop_route=False),
            is_running=is_running,
            set_running=set_running,
            inc_routes_fn=lambda: None,
            event_bus=MagicMock(),
            break_scheduler=None,
            depot=None,
            depot_orchestrator=None,
            ctrl=None,
            frame_getter=None,
            get_position=lambda: None,
            log_fn=_noop,
            get_consecutive_errors=lambda: 0,
            set_consecutive_errors=MagicMock(),
            navigate_to_fn=lambda a, b: [route],
            exec_route_fn=exec_route,
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=save_chk,
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=MagicMock(),
            format_traceback_fn=lambda: "",
        )
        # 2 pairs of waypoints (0→1 and 1→2)
        assert exec_route.call_count == 2

    def test_depot_orchestrator_triggered_when_should_resupply(self):
        route = self._make_route(found=True)
        waypoints = self._make_waypoints(2)
        depot_orch = MagicMock()
        depot_orch.should_resupply.return_value = True
        depot_orch.stats_snapshot.return_value = {}
        event_bus = MagicMock()

        session_runtime._run_waypoint_route_loop(
            waypoints=waypoints,
            config=_make_runtime_config(loop_route=False),
            is_running=lambda: True,  # loop_route=False handles exit naturally
            set_running=MagicMock(),
            inc_routes_fn=lambda: None,
            event_bus=event_bus,
            break_scheduler=None,
            depot=None,
            depot_orchestrator=depot_orch,
            ctrl=None,
            frame_getter=None,
            get_position=lambda: _coord(),
            log_fn=_noop,
            get_consecutive_errors=lambda: 0,
            set_consecutive_errors=MagicMock(),
            navigate_to_fn=lambda a, b: [route],
            exec_route_fn=MagicMock(),
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=MagicMock(),
            format_traceback_fn=lambda: "",
        )
        depot_orch.run_resupply.assert_called_once()
        event_bus.emit.assert_any_call("resupply_done", {})

    def test_stops_after_10_consecutive_errors(self):
        def bad_navigate(a, b):
            raise RuntimeError("nav failed")

        set_running = MagicMock()
        consec = [0]

        def get_consec():
            return consec[0]

        def set_consec(v):
            consec[0] = v

        session_runtime._run_waypoint_route_loop(
            waypoints=self._make_waypoints(2),
            config=_make_runtime_config(loop_route=True),
            is_running=lambda: True,
            set_running=set_running,
            inc_routes_fn=lambda: None,
            event_bus=MagicMock(),
            break_scheduler=None,
            depot=None,
            depot_orchestrator=None,
            ctrl=MagicMock(_consecutive_failures=0),
            frame_getter=None,
            get_position=lambda: None,
            log_fn=_noop,
            get_consecutive_errors=get_consec,
            set_consecutive_errors=set_consec,
            navigate_to_fn=bad_navigate,
            exec_route_fn=MagicMock(),
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=lambda t: None,
            format_traceback_fn=lambda: "",
        )
        set_running.assert_called_with(False)
        assert consec[0] >= 10

    def test_post_depot_run_after_route_completion(self):
        route = self._make_route(found=True)
        waypoints = self._make_waypoints(2)
        depot = MagicMock()
        depot.run_depot_cycle.return_value = True
        depot.cycle_count = 1
        event_bus = MagicMock()

        session_runtime._run_waypoint_route_loop(
            waypoints=waypoints,
            config=_make_runtime_config(loop_route=False, depot_after_run=True),
            is_running=lambda: True,  # loop_route=False handles exit naturally
            set_running=MagicMock(),
            inc_routes_fn=lambda: None,
            event_bus=event_bus,
            break_scheduler=None,
            depot=depot,
            depot_orchestrator=None,
            ctrl=None,
            frame_getter=None,
            get_position=lambda: _coord(),
            log_fn=_noop,
            get_consecutive_errors=lambda: 0,
            set_consecutive_errors=MagicMock(),
            navigate_to_fn=lambda a, b: [route],
            exec_route_fn=MagicMock(),
            exec_multifloor_fn=MagicMock(),
            save_checkpoint_fn=MagicMock(),
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            jittered_sleep_fn=MagicMock(),
            format_traceback_fn=lambda: "",
        )
        depot.run_depot_cycle.assert_called_once()
        event_bus.emit.assert_any_call("depot_done", {"success": True, "cycles": 1})


# ─────────────────────────────────────────────────────────────────────────────
# Additional session_runtime tests — spawn_manager & break_scheduler paths
# ─────────────────────────────────────────────────────────────────────────────

class TestRunScriptRouteLoopSpawnManager:
    def test_registers_initial_spawn_when_script_matches(self):
        """SpawnManager's current_spawn is None → should be set when script matches."""
        spawn = SimpleNamespace(script="route.in", name="thais_wasps")
        spawn_mgr = SimpleNamespace(
            current_spawn=None,
            config=SimpleNamespace(spawns=[spawn]),
            get_spawn_script=MagicMock(return_value=None),
        )
        logs = []

        def run_script(p):
            pass

        session_runtime._run_script_route_loop(
            route_path=Path("route.in"),
            config=SimpleNamespace(loop_route=False, dry_run=False),
            json_peek=None,
            is_running=lambda: True,
            set_running=lambda v: None,
            inc_routes_fn=lambda: None,
            break_scheduler=None,
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            log_fn=logs.append,
            run_script_fn=run_script,
            spawn_manager=spawn_mgr,
        )
        assert spawn_mgr.current_spawn == "thais_wasps"
        assert any("SpawnManager" in m for m in logs)

    def test_spawn_switch_changes_route_path(self):
        """When spawn_manager switches spawn, route_path should update."""
        new_script = "/scripts/other_spawn.in"
        spawn_mgr = MagicMock()
        spawn_mgr.current_spawn = "other_spawn"
        spawn_mgr.get_spawn_script.return_value = new_script
        spawn_mgr.config.spawns = []

        run_count = [0]

        def run_script(p):
            run_count[0] += 1

        # loop 2 times: first with original, second with new script
        is_running_vals = [True, True, False]
        iv = [0]
        def is_running():
            v = is_running_vals[iv[0]] if iv[0] < len(is_running_vals) else False
            iv[0] += 1
            return v

        logs = []
        session_runtime._run_script_route_loop(
            route_path=Path("route.in"),
            config=SimpleNamespace(loop_route=True, dry_run=False),
            json_peek=None,
            is_running=is_running,
            set_running=lambda v: None,
            inc_routes_fn=lambda: None,
            break_scheduler=None,
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            log_fn=logs.append,
            run_script_fn=run_script,
            spawn_manager=spawn_mgr,
        )
        assert any("Spawn cambiado" in m or "cargando script" in m for m in logs)

    def test_break_scheduler_executed_in_loop(self):
        break_sch = MagicMock()
        break_sch.should_break.return_value = True

        run_count = [0]
        def run_script(p):
            run_count[0] += 1

        # Run 2 iterations then stop
        is_running_vals = [True, True, False]
        iv = [0]
        def is_running():
            v = is_running_vals[iv[0]] if iv[0] < len(is_running_vals) else False
            iv[0] += 1
            return v

        session_runtime._run_script_route_loop(
            route_path=Path("route.in"),
            config=SimpleNamespace(loop_route=True, dry_run=False),
            json_peek=None,
            is_running=is_running,
            set_running=lambda v: None,
            inc_routes_fn=lambda: None,
            break_scheduler=break_sch,
            pause_subsystems=_noop,
            resume_subsystems=_noop,
            log_fn=_noop,
            run_script_fn=run_script,
        )
        break_sch.execute_break.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# Additional session_script tests — _wire_executor_context & _preload_script_context
# ─────────────────────────────────────────────────────────────────────────────

class TestWireExecutorContext:
    def test_sets_position(self):
        executor = MagicMock()
        pos = _coord()
        session_script._wire_executor_context(
            executor=executor,
            position=pos,
            obstacle_analyzer=None,
            loader=None,
            set_path_viz=_noop,
            log_fn=_noop,
        )
        executor.set_position.assert_called_once_with(pos)

    def test_sets_obstacle_analyzer(self):
        executor = MagicMock()
        analyzer = MagicMock()
        session_script._wire_executor_context(
            executor=executor,
            position=None,
            obstacle_analyzer=analyzer,
            loader=None,
            set_path_viz=_noop,
            log_fn=_noop,
        )
        executor.set_obstacle_analyzer.assert_called_once_with(analyzer)

    def test_sets_map_loader(self):
        executor = MagicMock()
        loader = MagicMock()
        session_script._wire_executor_context(
            executor=executor,
            position=None,
            obstacle_analyzer=None,
            loader=loader,
            set_path_viz=_noop,
            log_fn=_noop,
        )
        executor.set_map_loader.assert_called_once_with(loader)

    def test_path_visualizer_init_failure_logged(self):
        """When PathVisualizer import fails, log the error and continue."""
        executor = MagicMock()
        loader = MagicMock()
        logs = []

        # Mock PathVisualizer constructor to raise
        with patch("src.session_script.Path"):
            with patch("src.path_visualizer.PathVisualizer", side_effect=RuntimeError("pv fail")):
                session_script._wire_executor_context(
                    executor=executor,
                    position=None,
                    obstacle_analyzer=None,
                    loader=loader,
                    set_path_viz=lambda pv: None,
                    log_fn=logs.append,
                )
        # Should not raise; error is logged
        executor.set_map_loader.assert_called_once_with(loader)


class TestPreloadScriptContext:
    def _make_instruction(self, z: int):
        c = SimpleNamespace(z=z)
        return SimpleNamespace(kind="node", coord=c)

    def test_preloads_floors_not_already_loaded(self):
        navigator = MagicMock()
        navigator.is_floor_loaded.return_value = False
        instr = self._make_instruction(z=7)
        logs = []
        session_script._preload_script_context(
            instructions=[instr],
            navigator=navigator,
            ctrl=None,
            dry_run=True,
            log_fn=logs.append,
        )
        navigator.load_floor.assert_called_once_with(7)
        assert any("Pre-cargando floor 7" in m for m in logs)

    def test_skips_already_loaded_floors(self):
        navigator = MagicMock()
        navigator.is_floor_loaded.return_value = True
        instr = self._make_instruction(z=7)
        session_script._preload_script_context(
            instructions=[instr],
            navigator=navigator,
            ctrl=None,
            dry_run=True,
            log_fn=_noop,
        )
        navigator.load_floor.assert_not_called()

    def test_logs_floor_load_exception(self):
        navigator = MagicMock()
        navigator.is_floor_loaded.return_value = False
        navigator.load_floor.side_effect = RuntimeError("floor load failed")
        instr = self._make_instruction(z=7)
        logs = []
        session_script._preload_script_context(
            instructions=[instr],
            navigator=navigator,
            ctrl=None,
            dry_run=True,
            log_fn=logs.append,
        )
        assert any("Error cargando floor 7" in m for m in logs)

    def test_noop_when_no_navigator(self):
        session_script._preload_script_context(
            instructions=[self._make_instruction(z=7)],
            navigator=None,
            ctrl=None,
            dry_run=True,
            log_fn=_noop,
        )

    def test_focuses_window_when_not_interception(self):
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        ctrl.input_method = "postmessage"
        ctrl.focus_now.return_value = True
        ctrl.hwnd = 0xABCD
        logs = []
        session_script._preload_script_context(
            instructions=[],
            navigator=None,
            ctrl=ctrl,
            dry_run=False,
            log_fn=logs.append,
        )
        ctrl.focus_now.assert_called_once()
        assert any("Enfocando" in m for m in logs)

    def test_skips_focus_when_interception(self):
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        ctrl.input_method = "interception"
        logs = []
        session_script._preload_script_context(
            instructions=[],
            navigator=None,
            ctrl=ctrl,
            dry_run=False,
            log_fn=logs.append,
        )
        ctrl.focus_now.assert_not_called()
        assert any("Skip focus" in m for m in logs)


class TestAlignScriptStartIndex:
    def _make_instr(self, kind: str, coord: Coordinate | None):
        if coord is None:
            return SimpleNamespace(kind=kind, coord=None)
        sc = SimpleNamespace(
            x=coord.x, y=coord.y, z=coord.z,
            to_tibia_coord=lambda: coord
        )
        return SimpleNamespace(kind=kind, coord=sc)

    def test_returns_start_index_when_nonzero(self):
        idx = session_script.align_script_start_index(
            instructions=[],
            start_index=5,
            position=_coord(),
            navigator=MagicMock(),
            log_fn=_noop,
            debug_fn=lambda a, b: None,
        )
        assert idx == 5

    def test_returns_zero_when_no_position(self):
        idx = session_script.align_script_start_index(
            instructions=[],
            start_index=0,
            position=None,
            navigator=MagicMock(),
            log_fn=_noop,
            debug_fn=lambda a, b: None,
        )
        assert idx == 0

    def test_returns_zero_when_no_navigator(self):
        idx = session_script.align_script_start_index(
            instructions=[],
            start_index=0,
            position=_coord(),
            navigator=None,
            log_fn=_noop,
            debug_fn=lambda a, b: None,
        )
        assert idx == 0

    def test_returns_zero_when_fewer_than_2_movement_points(self):
        instr = self._make_instr("node", _coord())
        idx = session_script.align_script_start_index(
            instructions=[instr],
            start_index=0,
            position=_coord(),
            navigator=MagicMock(),
            log_fn=_noop,
            debug_fn=lambda a, b: None,
        )
        assert idx == 0

    def test_finds_aligned_start_when_position_on_route(self):
        c1 = Coordinate(100, 200, 7)
        c2 = Coordinate(101, 200, 7)
        current_pos = Coordinate(100, 200, 7)  # same as c1

        instr1 = self._make_instr("node", c1)
        instr2 = self._make_instr("node", c2)

        route = MagicMock()
        route.found = True
        route.steps = [c1]  # current pos is on this segment

        nav = MagicMock()
        nav.navigate.return_value = route
        logs = []

        idx = session_script.align_script_start_index(
            instructions=[instr1, instr2],
            start_index=0,
            position=current_pos,
            navigator=nav,
            log_fn=logs.append,
            debug_fn=lambda a, b: None,
        )
        assert idx == 1
        assert any("Auto-align" in m for m in logs)

    def test_handles_navigator_exception(self):
        c1 = Coordinate(100, 200, 7)
        c2 = Coordinate(101, 200, 7)
        instr1 = self._make_instr("node", c1)
        instr2 = self._make_instr("node", c2)

        nav = MagicMock()
        nav.navigate.side_effect = RuntimeError("nav failed")
        debug_calls = []

        idx = session_script.align_script_start_index(
            instructions=[instr1, instr2],
            start_index=0,
            position=_coord(),
            navigator=nav,
            log_fn=_noop,
            debug_fn=lambda a, b: debug_calls.append((a, b)),
        )
        assert idx == 0
        assert len(debug_calls) == 1


class TestCollectRouteCriticalTiles:
    def _make_instr(self, kind: str, coord: Coordinate):
        sc = SimpleNamespace(
            x=coord.x, y=coord.y, z=coord.z,
            to_tibia_coord=lambda: coord
        )
        return SimpleNamespace(kind=kind, coord=sc)

    def test_returns_waypoint_tiles_without_navigator(self):
        c1 = Coordinate(100, 200, 7)
        c2 = Coordinate(101, 200, 7)
        tiles = session_script.collect_route_critical_tiles(
            instructions=[self._make_instr("node", c1), self._make_instr("node", c2)],
            navigator=None,
            debug_fn=lambda a, b: None,
        )
        assert (100, 200, 7) in tiles
        assert (101, 200, 7) in tiles

    def test_includes_route_steps_with_navigator(self):
        c1 = Coordinate(100, 200, 7)
        c2 = Coordinate(102, 200, 7)
        intermediate = Coordinate(101, 200, 7)

        route = MagicMock()
        route.found = True
        route.steps = [c1, intermediate, c2]

        nav = MagicMock()
        nav.navigate.return_value = route

        tiles = session_script.collect_route_critical_tiles(
            instructions=[self._make_instr("node", c1), self._make_instr("node", c2)],
            navigator=nav,
            debug_fn=lambda a, b: None,
        )
        assert (101, 200, 7) in tiles

    def test_handles_cross_floor_segments(self):
        c1 = Coordinate(100, 200, 7)
        c2 = Coordinate(100, 200, 8)  # different floor
        nav = MagicMock()
        tiles = session_script.collect_route_critical_tiles(
            instructions=[self._make_instr("node", c1), self._make_instr("node", c2)],
            navigator=nav,
            debug_fn=lambda a, b: None,
        )
        # Cross-floor segments are skipped — nav not called
        nav.navigate.assert_not_called()

    def test_handles_navigator_exception(self):
        c1 = Coordinate(100, 200, 7)
        c2 = Coordinate(101, 200, 7)
        nav = MagicMock()
        nav.navigate.side_effect = RuntimeError("nav fail")
        debug_calls = []
        session_script.collect_route_critical_tiles(
            instructions=[self._make_instr("node", c1), self._make_instr("node", c2)],
            navigator=nav,
            debug_fn=lambda a, b: debug_calls.append((a, b)),
        )
        assert len(debug_calls) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Additional session_startup tests — start_session & startup_subsystems
# ─────────────────────────────────────────────────────────────────────────────

class TestStartSession:
    def _make_session(self, dry_run: bool = True, running: bool = False):
        session = MagicMock()
        session._cfg = SimpleNamespace(
            dry_run=dry_run,
            input_method="postmessage",
            jitter_pct=0.1,
            pvp_detector=False,
            monitor_conditions=False,
            adaptive_roi=False,
        )
        session._get_running.return_value = running
        session._startup_phase = None
        session._startup_subsystems = MagicMock()
        return session

    def test_no_op_when_already_running(self):
        session = self._make_session(running=True)
        logs = []
        session._log = logs.append

        session_startup.start_session(
            session=session,
            set_jitter_fn=MagicMock(),
            reset_fatigue_fn=MagicMock(),
            run_preflight_fn=MagicMock(),
            templates_base=Path("."),
        )
        session._startup_subsystems.assert_not_called()
        assert any("already running" in m for m in logs)

    def test_calls_set_jitter_and_reset_fatigue(self):
        session = self._make_session(running=False)
        session._log = _noop
        set_jitter = MagicMock()
        reset_fatigue = MagicMock()

        # preflight mock returns success
        result = MagicMock()
        result.ok = True
        result.results = []

        session_startup.start_session(
            session=session,
            set_jitter_fn=set_jitter,
            reset_fatigue_fn=reset_fatigue,
            run_preflight_fn=lambda cfg, skip_driver, log_fn: result,
            templates_base=Path("."),
        )
        set_jitter.assert_called_once_with(0.1)
        reset_fatigue.assert_called_once()

    def test_calls_startup_subsystems(self):
        session = self._make_session(running=False)
        session._log = _noop
        result = MagicMock()
        result.ok = True
        result.results = []

        session_startup.start_session(
            session=session,
            set_jitter_fn=MagicMock(),
            reset_fatigue_fn=MagicMock(),
            run_preflight_fn=lambda cfg, skip_driver, log_fn: result,
            templates_base=Path("."),
        )
        session._startup_subsystems.assert_called_once()

    def test_handles_startup_subsystem_failure(self):
        session = self._make_session(running=False)
        logs = []
        session._log = logs.append
        session._startup_subsystems.side_effect = RuntimeError("subsystem init failed")
        result = MagicMock()
        result.ok = True
        result.results = []

        with pytest.raises(RuntimeError, match="subsystem init failed"):
            session_startup.start_session(
                session=session,
                set_jitter_fn=MagicMock(),
                reset_fatigue_fn=MagicMock(),
                run_preflight_fn=lambda cfg, skip_driver, log_fn: result,
                templates_base=Path("."),
            )
        session._set_running.assert_called_with(False)
        assert any("FATAL" in m for m in logs)


class TestStartupSubsystems:
    def test_calls_all_init_methods(self):
        session = MagicMock()
        # init_capture returns a "cached" value
        session._init_capture.return_value = object()
        session_startup.startup_subsystems(session=session)
        session._init_input.assert_called_once()
        session._init_navigation.assert_called_once()
        session._init_healer.assert_called_once()
        session._init_capture.assert_called_once()
        session._init_optional_subsystems.assert_called_once()
        session._init_safety_handlers.assert_called_once()
        session._init_integrated_modules.assert_called_once()
        session._init_monitoring.assert_called_once()
        session._start_threads.assert_called_once()


class TestInitNavigation:
    def test_creates_navigator_with_loader(self):
        session = SimpleNamespace(
            _startup_phase=None,
            _loader=None,
            _log=_noop,
        )
        map_loader_cls = MagicMock()
        loader_inst = MagicMock()
        map_loader_cls.return_value = loader_inst

        navigator_cls = MagicMock()
        nav_inst = MagicMock()
        navigator_cls.return_value = nav_inst

        session_startup.init_navigation(
            session=session,
            map_loader_cls=map_loader_cls,
            navigator_cls=navigator_cls,
        )
        assert session._navigator is nav_inst
        assert nav_inst.loader is loader_inst
        assert session._startup_phase == "navigator"

    def test_reuses_existing_loader(self):
        existing_loader = MagicMock()
        session = SimpleNamespace(
            _startup_phase=None,
            _loader=existing_loader,
            _log=_noop,
        )
        map_loader_cls = MagicMock()
        navigator_cls = MagicMock()
        nav_inst = MagicMock()
        navigator_cls.return_value = nav_inst

        session_startup.init_navigation(
            session=session,
            map_loader_cls=map_loader_cls,
            navigator_cls=navigator_cls,
        )
        map_loader_cls.assert_not_called()
        assert nav_inst.loader is existing_loader


# ─────────────────────────────────────────────────────────────────────────────
# session_startup — init_input tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_input_session(*, input_method="postmessage", arduino_enabled=False, pico_enabled=False):
    return SimpleNamespace(
        _startup_phase=None,
        _ctrl=None,
        _raw_ctrl=None,
        _arduino=None,
        _pico=None,
        _cfg=SimpleNamespace(
            target_window="Tibia",
            input_method=input_method,
            jitter_pct=0.0,
            arduino_enabled=arduino_enabled,
            arduino_port="auto",
            pico_enabled=pico_enabled,
            pico_port="auto",
        ),
        _log=_noop,
    )


class TestInitInput:
    def _make_ctrl(self, connected=True, interception_available=True):
        ctrl = MagicMock()
        ctrl.is_connected.return_value = connected
        ctrl.interception_available = interception_available
        return ctrl

    def test_basic_postmessage_setup(self):
        session = _make_input_session()
        raw_ctrl = self._make_ctrl()
        ctrl_cls = MagicMock(return_value=raw_ctrl)

        session_startup.init_input(
            session=session,
            input_controller_cls=ctrl_cls,
            his_available=False,
            his_cls=None,
            his_config_path=Path("."),
            arduino_available=False,
            arduino_config_cls=None,
            arduino_hid_cls=None,
            pico_available=False,
            pico_config_cls=None,
            pico_hid_cls=None,
        )
        assert session._ctrl is raw_ctrl
        assert session._raw_ctrl is raw_ctrl
        assert session._startup_phase == "input_controller"

    def test_raises_on_interception_not_available(self):
        session = _make_input_session(input_method="interception")
        raw_ctrl = self._make_ctrl(interception_available=False)
        ctrl_cls = MagicMock(return_value=raw_ctrl)

        with pytest.raises(RuntimeError, match="INTERCEPTION DRIVER NOT AVAILABLE"):
            session_startup.init_input(
                session=session,
                input_controller_cls=ctrl_cls,
                his_available=False,
                his_cls=None,
                his_config_path=Path("."),
                arduino_available=False,
                arduino_config_cls=None,
                arduino_hid_cls=None,
                pico_available=False,
                pico_config_cls=None,
                pico_hid_cls=None,
            )

    def test_logs_when_window_not_found(self):
        session = _make_input_session()
        logs = []
        session._log = logs.append
        raw_ctrl = self._make_ctrl(connected=False)
        ctrl_cls = MagicMock(return_value=raw_ctrl)

        session_startup.init_input(
            session=session,
            input_controller_cls=ctrl_cls,
            his_available=False,
            his_cls=None,
            his_config_path=Path("."),
            arduino_available=False,
            arduino_config_cls=None,
            arduino_hid_cls=None,
            pico_available=False,
            pico_config_cls=None,
            pico_hid_cls=None,
        )
        assert any("not found" in m for m in logs)

    def test_his_system_initialized_when_available(self):
        session = _make_input_session()
        logs = []
        session._log = logs.append
        raw_ctrl = self._make_ctrl()
        ctrl_cls = MagicMock(return_value=raw_ctrl)
        his_inst = MagicMock()
        his_cls = MagicMock(return_value=his_inst)

        session_startup.init_input(
            session=session,
            input_controller_cls=ctrl_cls,
            his_available=True,
            his_cls=his_cls,
            his_config_path=Path("his.json"),
            arduino_available=False,
            arduino_config_cls=None,
            arduino_hid_cls=None,
            pico_available=False,
            pico_config_cls=None,
            pico_hid_cls=None,
        )
        assert session._ctrl is his_inst
        assert any("HIS" in m for m in logs)

    def test_his_fallback_when_init_fails(self):
        session = _make_input_session()
        logs = []
        session._log = logs.append
        raw_ctrl = self._make_ctrl()
        ctrl_cls = MagicMock(return_value=raw_ctrl)
        his_cls = MagicMock(side_effect=RuntimeError("HIS init failed"))

        session_startup.init_input(
            session=session,
            input_controller_cls=ctrl_cls,
            his_available=True,
            his_cls=his_cls,
            his_config_path=Path("his.json"),
            arduino_available=False,
            arduino_config_cls=None,
            arduino_hid_cls=None,
            pico_available=False,
            pico_config_cls=None,
            pico_hid_cls=None,
        )
        # Falls back to raw_ctrl
        assert session._ctrl is raw_ctrl
        assert any("HIS] Error" in m for m in logs)

    def test_arduino_enabled_and_initialized(self):
        session = _make_input_session(arduino_enabled=True)
        logs = []
        session._log = logs.append
        raw_ctrl = self._make_ctrl()
        ctrl_cls = MagicMock(return_value=raw_ctrl)

        arduino_inst = MagicMock()
        arduino_inst.initialize.return_value = True
        arduino_hid_cls = MagicMock(return_value=arduino_inst)
        arduino_config_cls = MagicMock()

        session_startup.init_input(
            session=session,
            input_controller_cls=ctrl_cls,
            his_available=False,
            his_cls=None,
            his_config_path=Path("."),
            arduino_available=True,
            arduino_config_cls=arduino_config_cls,
            arduino_hid_cls=arduino_hid_cls,
            pico_available=False,
            pico_config_cls=None,
            pico_hid_cls=None,
        )
        assert session._arduino is arduino_inst
        assert any("Arduino" in m and "HID" in m for m in logs)

    def test_arduino_init_failed_logs_message(self):
        session = _make_input_session(arduino_enabled=True)
        logs = []
        session._log = logs.append
        raw_ctrl = self._make_ctrl()
        ctrl_cls = MagicMock(return_value=raw_ctrl)

        arduino_inst = MagicMock()
        arduino_inst.initialize.return_value = False
        arduino_hid_cls = MagicMock(return_value=arduino_inst)
        arduino_config_cls = MagicMock()

        session_startup.init_input(
            session=session,
            input_controller_cls=ctrl_cls,
            his_available=False,
            his_cls=None,
            his_config_path=Path("."),
            arduino_available=True,
            arduino_config_cls=arduino_config_cls,
            arduino_hid_cls=arduino_hid_cls,
            pico_available=False,
            pico_config_cls=None,
            pico_hid_cls=None,
        )
        assert any("No disponible" in m for m in logs)

    def test_pico_enabled_and_initialized(self):
        session = _make_input_session(pico_enabled=True)
        logs = []
        session._log = logs.append
        raw_ctrl = self._make_ctrl()
        ctrl_cls = MagicMock(return_value=raw_ctrl)

        pico_inst = MagicMock()
        pico_inst.initialize.return_value = True
        pico_hid_cls = MagicMock(return_value=pico_inst)
        pico_config_cls = MagicMock()

        session_startup.init_input(
            session=session,
            input_controller_cls=ctrl_cls,
            his_available=False,
            his_cls=None,
            his_config_path=Path("."),
            arduino_available=False,
            arduino_config_cls=None,
            arduino_hid_cls=None,
            pico_available=True,
            pico_config_cls=pico_config_cls,
            pico_hid_cls=pico_hid_cls,
        )
        assert session._pico is pico_inst
        assert any("Pico2" in m for m in logs)


# ─────────────────────────────────────────────────────────────────────────────
# session_startup — init_healer tests
# ─────────────────────────────────────────────────────────────────────────────

class _MockSessionForHealer:
    """Mutable class for healer tests (SimpleNamespace can't reassign __class__)."""
    def __init__(self):
        self._startup_phase = None
        self._ctrl = MagicMock()
        self._healer = None
        self._combat = None
        self._event_bus = MagicMock()
        self._cfg = SimpleNamespace(
            heal_hp_pct=80,
            heal_emergency_pct=25,
            mana_threshold_pct=40,
            heal_hotkey_vk=0x70,
            mana_hotkey_vk=0x71,
            emergency_hotkey_vk=0x72,
        )
        self._log = _noop
        self._inc_stat = MagicMock()


class TestInitHealer:
    def _make_session(self):
        return _MockSessionForHealer()

    def _make_heal_config_cls(self):
        import dataclasses

        @dataclasses.dataclass
        class _HC:
            hp_threshold_pct: int = 50
            hp_emergency_pct: int = 20
            mp_threshold_pct: int = 30
            heal_hotkey_vk: int = 0
            mana_hotkey_vk: int = 0
            emergency_hotkey_vk: int = 0

        hcc = MagicMock()
        hcc.load.return_value = _HC()
        return hcc

    def test_creates_healer_with_config(self):
        session = self._make_session()
        healer_inst = MagicMock()
        healer_inst.is_running = True
        auto_healer_cls = MagicMock(return_value=healer_inst)

        session_startup.init_healer(
            session=session,
            heal_config_cls=self._make_heal_config_cls(),
            auto_healer_cls=auto_healer_cls,
        )
        assert session._healer is healer_inst
        healer_inst.start.assert_called_once()
        assert session._startup_phase == "healer"

    def test_on_heal_callback_increments_stat(self):
        session = self._make_session()
        healer_inst = MagicMock()
        auto_healer_cls = MagicMock(return_value=healer_inst)

        session_startup.init_healer(
            session=session,
            heal_config_cls=self._make_heal_config_cls(),
            auto_healer_cls=auto_healer_cls,
        )
        # Invoke the on_heal callback
        healer_inst.on_heal()
        session._inc_stat.assert_called_with("heal_fired")
        # "heal" event should be emitted (payload contains hp_pct)
        emitted_events = [c.args[0] for c in session._event_bus.emit.call_args_list]
        assert "heal" in emitted_events

    def test_on_mana_callback_increments_stat(self):
        session = self._make_session()
        healer_inst = MagicMock()
        auto_healer_cls = MagicMock(return_value=healer_inst)

        session_startup.init_healer(
            session=session,
            heal_config_cls=self._make_heal_config_cls(),
            auto_healer_cls=auto_healer_cls,
        )
        healer_inst.on_mana()
        session._inc_stat.assert_called_with("mana_fired")
        emitted_events = [c.args[0] for c in session._event_bus.emit.call_args_list]
        assert "mana" in emitted_events


# ─────────────────────────────────────────────────────────────────────────────
# session_startup — init_capture tests
# ─────────────────────────────────────────────────────────────────────────────

class TestInitCapture:
    def _make_session(self):
        return SimpleNamespace(
            _startup_phase=None,
            _radar=None,
            _frame_getter=None,
            _frame_cache=None,
            _frame_watchdog=None,
            _ctrl=MagicMock(),
            _event_bus=MagicMock(),
            _cfg=SimpleNamespace(),
            _log=_noop,
        )

    def test_capture_pipeline_initialized(self):
        session = self._make_session()

        captured_getter = object()
        capture_result = SimpleNamespace(
            frame_getter=MagicMock(),
            frame_cache=MagicMock(),
            frame_watchdog=MagicMock(),
            cached_getter=captured_getter,
        )
        init_pipeline = MagicMock(return_value=capture_result)
        build_fg = MagicMock()

        result = session_startup.init_capture(
            session=session,
            initialize_capture_pipeline_fn=init_pipeline,
            build_frame_getter_fn=build_fg,
        )
        assert result is captured_getter
        assert session._frame_getter is capture_result.frame_getter
        assert session._frame_cache is capture_result.frame_cache
        assert session._frame_watchdog is capture_result.frame_watchdog
        assert session._radar is None
        assert session._startup_phase == "frame_source"
