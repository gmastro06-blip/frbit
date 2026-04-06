from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models import Coordinate, Route
from src.script_parser import ScriptCoord
from src.session import SessionConfig
from src import session_script, session_watchdog


@dataclass
class _DummyHealConfig:
    hp_threshold_pct: int = 50
    hp_emergency_pct: int = 20
    mp_threshold_pct: int = 30
    heal_hotkey_vk: int = 0x70
    mana_hotkey_vk: int = 0x71
    emergency_hotkey_vk: int = 0x72


def _instruction(kind: str = "node", coord: Coordinate | None = None, label: str | None = None):
    script_coord = None
    if coord is not None:
        script_coord = ScriptCoord(coord.x, coord.y, coord.z)
    return SimpleNamespace(kind=kind, coord=script_coord, label=label)


class TestSessionScriptHelpers:

    def test_parse_json_script_raises_without_script_array(self, tmp_path: Path):
        path = tmp_path / "route.json"
        path.write_text("{}", encoding="utf-8")

        with patch("builtins.print"):
            try:
                session_script._parse_json_script(
                    route_path=path,
                    config=SessionConfig(),
                    ctrl=None,
                    healer=None,
                    get_position=lambda: None,
                    set_position=lambda value: None,
                    log_fn=lambda msg: None,
                    json_script_parser=lambda raw: raw,
                )
                raise AssertionError("expected ValueError")
            except ValueError as exc:
                assert "no 'script' array" in str(exc)

    def test_parse_json_script_applies_session_overrides_and_meta_position(self, tmp_path: Path):
        path = tmp_path / "route.json"
        path.write_text(
            """
            {
              "script": [{"kind": "action", "action": "end"}],
                            "wasp_setup": {"hunt_config": {"ammo_name": "bolt", "take_ammo": 300}},
              "session": {
                "input_method": "mouse",
                "jitter_pct": 0.25,
                "step_interval": 0.8,
                                "rope_hotkey_vk": 123,
                                "shovel_hotkey_vk": 122,
                "heal_hp_pct": 65,
                "heal_emergency_pct": 25,
                "mana_threshold_pct": 40,
                "heal_hotkey_vk": 112,
                "mana_hotkey_vk": 113,
                "emergency_hotkey_vk": 114
              },
              "script_options": {"hours_leave": [9.5]},
              "blocked_regions": [{"x_min": 1, "x_max": 2, "y_min": 3, "y_max": 4}],
              "walkable_overrides": [{"x_min": 5, "x_max": 6, "y_min": 7, "y_max": 8}],
              "_meta": {"start_coord": {"x": 100, "y": 200, "z": 7}}
            }
            """,
            encoding="utf-8",
        )
        ctrl = SimpleNamespace(input_method="interception", jitter_pct=0.0)
        healer = SimpleNamespace(_cfg=_DummyHealConfig())
        config = SessionConfig()
        logs: list[str] = []
        set_calls: list[Coordinate] = []

        result = session_script._parse_json_script(
            route_path=path,
            config=config,
            ctrl=ctrl,
            healer=healer,
            get_position=lambda: None,
            set_position=set_calls.append,
            log_fn=logs.append,
            json_script_parser=lambda raw: [SimpleNamespace(kind="action", coord=None)],
        )

        assert result.dry_run is False
        assert result.step_interval == 0.8
        assert result.hours_leave == [9.5]
        assert len(result.blocked_regions) == 1
        assert len(result.walkable_overrides) == 1
        assert result.wasp_setup == {"hunt_config": {"ammo_name": "bolt", "take_ammo": 300}}
        assert config.rope_hotkey_vk == 123
        assert config.shovel_hotkey_vk == 122
        assert ctrl.input_method == "postmessage"
        assert ctrl.jitter_pct == 0.25
        assert healer._cfg.hp_threshold_pct == 65
        assert healer._cfg.hp_emergency_pct == 25
        assert healer._cfg.mp_threshold_pct == 40
        assert set_calls == [Coordinate(100, 200, 7)]
        assert any("start_coord" in msg for msg in logs)

    def test_parse_json_script_ignores_bootstrap_position_when_tracking_disabled(self, tmp_path: Path):
        path = tmp_path / "route.json"
        path.write_text(
            """
            {
              "script": [{"kind": "action", "action": "end"}],
              "_meta": {"start_coord": {"x": 32352, "y": 32227, "z": 7}}
            }
            """,
            encoding="utf-8",
        )
        set_calls: list[Coordinate] = []

        session_script._parse_json_script(
            route_path=path,
            config=SessionConfig(position_source="none"),
            ctrl=None,
            healer=None,
            get_position=lambda: Coordinate(32000, 32000, 7),
            set_position=set_calls.append,
            log_fn=lambda msg: None,
            json_script_parser=lambda raw: [SimpleNamespace(kind="action", coord=None)],
        )

        assert set_calls == [Coordinate(32352, 32227, 7)]

    def test_resolve_resume_index_ignores_invalid_checkpoint_metadata(self):
        checkpoint = SimpleNamespace(matches_route=lambda route: True, extra={"route_mode": "script", "script_resume_instruction_index": "bad"})
        result = session_script._resolve_resume_index(
            route_identity="route",
            instruction_count=5,
            load_checkpoint_fn=lambda: checkpoint,
            log_fn=lambda msg: None,
        )
        assert result == 0

    def test_preload_script_context_logs_focus_failure_and_floor_load_error(self):
        logs: list[str] = []
        navigator = MagicMock()
        navigator.is_floor_loaded.return_value = False
        navigator.load_floor.side_effect = RuntimeError("boom")
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        ctrl.input_method = "postmessage"
        ctrl.focus_now.return_value = False
        ctrl.hwnd = 123

        session_script._preload_script_context(
            instructions=[_instruction(coord=Coordinate(1, 2, 7))],
            navigator=navigator,
            ctrl=ctrl,
            dry_run=False,
            log_fn=logs.append,
        )

        assert any("No se pudo enfocar" in msg for msg in logs)
        assert any("Error cargando floor 7" in msg for msg in logs)

    def test_apply_script_regions_logs_invalid_entries_and_totals(self):
        executor = MagicMock()
        executor._blocked_pixels = [(1, 1, 7), (2, 2, 7)]
        executor.force_walkable_region.return_value = 3
        logs: list[str] = []

        session_script._apply_script_regions(
            executor=executor,
            blocked_regions=[{"x_min": 1, "x_max": 2, "y_min": 3, "y_max": 4}, {"bad": 1}],
            walkable_overrides=[{"x_min": 1, "x_max": 1, "y_min": 1, "y_max": 1}, {"oops": 1}],
            log_fn=logs.append,
        )

        executor.add_blocked_region.assert_called_once()
        executor.force_walkable_region.assert_called_once()
        assert any("Invalid blocked_region entry" in msg for msg in logs)
        assert any("Invalid walkable_override entry" in msg for msg in logs)
        assert any("Pre-blocked" in msg for msg in logs)
        assert any("Force-walkable" in msg for msg in logs)

    def test_apply_learned_walkability_logs_patched_tiles_and_errors(self):
        logs: list[str] = []
        loader = MagicMock()
        loader.apply_learned_walkability_for_tiles.return_value = 4

        session_script._apply_learned_walkability(
            loader=loader,
            instructions=[_instruction(coord=Coordinate(1, 1, 7))],
            collect_route_critical_tiles_fn=lambda instructions: {(1, 1, 7)},
            log_fn=logs.append,
        )
        assert any("Learned walkability: 4 tiles" in msg for msg in logs)

        logs.clear()
        session_script._apply_learned_walkability(
            loader=loader,
            instructions=[],
            collect_route_critical_tiles_fn=lambda instructions: (_ for _ in ()).throw(RuntimeError("bad")),
            log_fn=logs.append,
        )
        assert any("Learned walkability load error" in msg for msg in logs)

    def test_wire_loot_counter_increments_executor_items(self):
        looter = SimpleNamespace(on_item_looted=lambda item, amount: None)
        executor = MagicMock()
        previous = session_script._wire_loot_counter(looter=looter, get_executor=lambda: executor)

        looter.on_item_looted("gold", 2)

        assert callable(previous)
        executor.increment_item_count.assert_called_once_with("gold", 2)

    def test_finalize_script_run_saves_checkpoint_and_learned_blocks(self):
        executor = SimpleNamespace(
            resume_instruction_index=3,
            last_confirmed_node_index=2,
            stop_reason="movement_failed",
            _blocked_pixels=[(1, 2, 7), (3, 4, 7)],
            _preblocked_count=1,
            _opened_pixels=[(5, 6, 7)],
        )
        loader = MagicMock()
        looter = SimpleNamespace(on_item_looted="old")
        path_viz = MagicMock()
        save_checkpoint = MagicMock()
        clear_checkpoint = MagicMock()
        set_executor = MagicMock()

        session_script._finalize_script_run(
            executor=executor,
            route_identity="route.json",
            loader=loader,
            looter=looter,
            previous_loot_callback="previous",
            get_path_viz=lambda: path_viz,
            set_executor=set_executor,
            save_checkpoint_fn=save_checkpoint,
            clear_checkpoint_fn=clear_checkpoint,
            log_fn=lambda msg: None,
        )

        path_viz.save_cumulative.assert_called_once()
        save_checkpoint.assert_called_once()
        clear_checkpoint.assert_not_called()
        loader.save_learned_blocks.assert_called_once()
        set_executor.assert_called_once_with(None)
        assert looter.on_item_looted == "previous"

    def test_align_and_collect_script_route_helpers_cover_success_and_exceptions(self):
        current = Coordinate(10, 10, 7)
        instructions = [
            _instruction(coord=Coordinate(9, 10, 7)),
            _instruction(coord=Coordinate(11, 10, 7)),
            _instruction(coord=Coordinate(12, 10, 7)),
        ]
        navigator = MagicMock()

        def _navigate(start: Coordinate, end: Coordinate):
            if end == Coordinate(11, 10, 7):
                return Route(
                    start=Coordinate(9, 10, 7),
                    end=Coordinate(11, 10, 7),
                    steps=[Coordinate(9, 10, 7), Coordinate(10, 10, 7), Coordinate(11, 10, 7)],
                    found=True,
                    total_distance=2.0,
                )
            raise RuntimeError("boom")

        navigator.navigate.side_effect = _navigate
        debug_calls: list[tuple[Coordinate, Coordinate]] = []

        aligned = session_script.align_script_start_index(
            instructions=instructions,
            start_index=0,
            position=current,
            navigator=navigator,
            log_fn=lambda msg: None,
            debug_fn=lambda start, end: debug_calls.append((start, end)),
        )
        critical = session_script.collect_route_critical_tiles(
            instructions=instructions,
            navigator=navigator,
            debug_fn=lambda start, end: debug_calls.append((start, end)),
        )

        assert aligned == 1
        assert (10, 10, 7) in critical
        assert debug_calls

    def test_run_session_script_skips_bootstrap_position_and_auto_align_when_tracking_disabled(self):
        config = SessionConfig(position_source="none")
        prepared = session_script.ScriptPreparation(
            route_path=Path("route.json"),
            route_identity="route.json",
            instructions=[SimpleNamespace(kind="action", coord=None)],
            dry_run=False,
            step_interval=0.45,
        )
        executor = MagicMock()
        executor.resume_instruction_index = 0
        executor.last_confirmed_node_index = 0
        executor.stop_reason = "completed"
        executor._blocked_pixels = []
        executor._preblocked_count = 0

        with patch("src.session_script._parse_script_source", return_value=prepared), \
             patch("src.session_script._resolve_resume_index", return_value=0), \
             patch("src.session_script._wire_executor_context") as wire_ctx, \
             patch("src.session_script._preload_script_context"), \
             patch("src.session_script._apply_script_regions"), \
             patch("src.session_script._apply_learned_walkability"), \
             patch("src.session_script._wire_loot_counter", return_value=None), \
             patch("src.session_script._finalize_script_run"):
            session_script.run_session_script(
                path="route.json",
                config=config,
                ctrl=None,
                navigator=MagicMock(),
                healer=None,
                frame_getter=None,
                depot=None,
                combat=None,
                radar=None,
                stuck_detector=None,
                obstacle_analyzer=None,
                loader=None,
                looter=None,
                get_position=lambda: Coordinate(32000, 32000, 7),
                set_position=lambda coord: None,
                get_real_position_fn=MagicMock(),
                set_position_from_executor_fn=lambda coord: None,
                npc_handler_factory=lambda: None,
                log_fn=lambda msg: None,
                get_executor=lambda: None,
                set_executor=lambda value: None,
                get_path_viz=lambda: None,
                set_path_viz=lambda value: None,
                load_checkpoint_fn=lambda: None,
                save_checkpoint_fn=lambda **kwargs: None,
                clear_checkpoint_fn=lambda: None,
                align_script_start_index_fn=MagicMock(side_effect=AssertionError("should not auto-align")),
                collect_route_critical_tiles_fn=lambda instructions: set(),
                resolve_route_fn=lambda path: Path(path),
                parse_file_fn=lambda path: [],
                json_script_parser=lambda raw: raw,
                script_executor_cls=MagicMock(return_value=executor),
            )

        wire_ctx.assert_called_once()
        assert wire_ctx.call_args.kwargs["position"] is None
        executor.execute.assert_called_once_with(prepared.instructions, start_index=0)

    def test_run_session_script_attaches_wasp_setup_to_executor(self):
        config = SessionConfig(position_source="none")
        prepared = session_script.ScriptPreparation(
            route_path=Path("route.json"),
            route_identity="route.json",
            instructions=[SimpleNamespace(kind="action", coord=None)],
            dry_run=False,
            step_interval=0.45,
            wasp_setup={"hunt_config": {"ammo_name": "bolt", "take_ammo": 300}},
        )
        executor = MagicMock()
        executor.resume_instruction_index = 0
        executor.last_confirmed_node_index = 0
        executor.stop_reason = "completed"
        executor._blocked_pixels = []
        executor._preblocked_count = 0

        with patch("src.session_script._parse_script_source", return_value=prepared), \
             patch("src.session_script._resolve_resume_index", return_value=0), \
             patch("src.session_script._wire_executor_context"), \
             patch("src.session_script._preload_script_context"), \
             patch("src.session_script._apply_script_regions"), \
             patch("src.session_script._apply_learned_walkability"), \
             patch("src.session_script._wire_loot_counter", return_value=None), \
             patch("src.session_script._finalize_script_run"):
            session_script.run_session_script(
                path="route.json",
                config=config,
                ctrl=None,
                navigator=MagicMock(),
                healer=None,
                frame_getter=None,
                depot=None,
                combat=None,
                radar=None,
                stuck_detector=None,
                obstacle_analyzer=None,
                loader=None,
                looter=None,
                get_position=lambda: None,
                set_position=lambda coord: None,
                get_real_position_fn=MagicMock(),
                set_position_from_executor_fn=lambda coord: None,
                npc_handler_factory=lambda: None,
                log_fn=lambda msg: None,
                get_executor=lambda: None,
                set_executor=lambda value: None,
                get_path_viz=lambda: None,
                set_path_viz=lambda value: None,
                load_checkpoint_fn=lambda: None,
                save_checkpoint_fn=lambda **kwargs: None,
                clear_checkpoint_fn=lambda: None,
                align_script_start_index_fn=lambda **kwargs: 0,
                collect_route_critical_tiles_fn=lambda instructions: set(),
                resolve_route_fn=lambda path: Path(path),
                parse_file_fn=lambda path: [],
                json_script_parser=lambda raw: raw,
                script_executor_cls=MagicMock(return_value=executor),
            )

        assert executor._wasp_setup == prepared.wasp_setup


class TestSessionWatchdogHelpers:

    def test_check_subsystem_health_restarts_same_instance_preserving_callbacks(self):
        callback = object()

        class _DeadSubsystem:
            def __init__(self) -> None:
                self._running = True
                self._thread = MagicMock()
                self._thread.is_alive.return_value = False
                self.on_condition = callback
                self.start_calls = 0

            def start(self) -> None:
                self.start_calls += 1
                assert self.on_condition is callback

        subsystem = _DeadSubsystem()

        session_watchdog.check_subsystem_health(
            healer=None,
            combat=None,
            looter=None,
            death_handler=None,
            reconnect_handler=None,
            anti_kick=subsystem,
            stuck_detector=None,
            loot_in_progress=MagicMock(),
            arduino=None,
            arduino_last_uptime_ms=None,
            event_bus=MagicMock(),
            log_fn=lambda _: None,
            stop_session=MagicMock(),
        )

        assert subsystem.start_calls == 1
        assert subsystem.on_condition is callback

    def test_check_subsystem_health_handles_arduino_disconnect_and_reboot(self):
        logs: list[str] = []
        event_bus = MagicMock()
        stop_session = MagicMock()
        arduino = MagicMock()

        arduino.is_available.return_value = False
        result = session_watchdog.check_subsystem_health(
            healer=None,
            combat=None,
            looter=None,
            death_handler=None,
            reconnect_handler=None,
            anti_kick=None,
            stuck_detector=None,
            loot_in_progress=MagicMock(),
            arduino=arduino,
            arduino_last_uptime_ms=123,
            event_bus=event_bus,
            log_fn=logs.append,
            stop_session=stop_session,
        )
        assert result == 123
        event_bus.emit.assert_called_with("arduino_disconnected", {})
        stop_session.assert_called_once()

        logs.clear()
        event_bus.reset_mock()
        stop_session.reset_mock()
        arduino.is_available.return_value = True
        arduino.send_status.return_value = (100, {})
        result = session_watchdog.check_subsystem_health(
            healer=None,
            combat=None,
            looter=None,
            death_handler=None,
            reconnect_handler=None,
            anti_kick=None,
            stuck_detector=None,
            loot_in_progress=MagicMock(),
            arduino=arduino,
            arduino_last_uptime_ms=200,
            event_bus=event_bus,
            log_fn=logs.append,
            stop_session=stop_session,
        )
        assert result == 200
        event_bus.emit.assert_called_with("arduino_rebooted", {"uptime_ms": 100})
        stop_session.assert_called_once()

    def test_check_subsystem_health_updates_healthy_arduino_uptime(self):
        arduino = MagicMock()
        arduino.is_available.return_value = True
        arduino.send_status.return_value = (250, {})

        result = session_watchdog.check_subsystem_health(
            healer=None,
            combat=None,
            looter=None,
            death_handler=None,
            reconnect_handler=None,
            anti_kick=None,
            stuck_detector=None,
            loot_in_progress=MagicMock(),
            arduino=arduino,
            arduino_last_uptime_ms=200,
            event_bus=MagicMock(),
            log_fn=lambda msg: None,
            stop_session=MagicMock(),
        )
        assert result == 250

    def test_run_watchdog_loop_emits_position_lost_and_watchdog(self):
        states = iter([True, True, False])
        pos_none_since = {"value": 50.0}
        last_move_time = {"value": 10.0}
        event_bus = MagicMock()
        logs: list[str] = []
        monotonic_values = iter([100.0, 100.0, 100.0])

        session_watchdog.run_watchdog_loop(
            timeout=30.0,
            is_running=lambda: next(states),
            sleep_fn=lambda secs: None,
            check_subsystem_health_fn=lambda: None,
            get_position=lambda: None,
            get_pos_none_since=lambda: pos_none_since["value"],
            set_pos_none_since=lambda value: pos_none_since.__setitem__("value", value),
            get_last_move_time=lambda: last_move_time["value"],
            set_last_move_time=lambda value: last_move_time.__setitem__("value", value),
            event_bus=event_bus,
            log_fn=logs.append,
            monotonic_fn=lambda: next(monotonic_values),
        )

        assert any("Position unreadable" in msg for msg in logs)
        assert any("No movement" in msg for msg in logs)
        assert event_bus.emit.call_args_list[0].args[0] == "position_lost"
        assert event_bus.emit.call_args_list[1].args[0] == "watchdog"
        assert last_move_time["value"] == 100.0

    def test_run_window_watchdog_loop_restores_minimized_and_handles_errors(self):
        user32 = MagicMock()
        user32.IsWindow.return_value = True
        user32.IsIconic.return_value = True
        user32.GetForegroundWindow.return_value = 0x99

        def _get_title(hwnd, buf, size):
            buf.value = "Tibia main"

        user32.GetWindowTextW.side_effect = _get_title
        running = iter([True, False])

        with patch("src.session_watchdog.ctypes.windll", SimpleNamespace(user32=user32)):
            session_watchdog.run_window_watchdog_loop(
                is_running=lambda: next(running),
                window_handles={"main": 0x1234},
                log_fn=lambda msg: None,
                stop_session=MagicMock(),
                sleep_fn=lambda secs: None,
                debug_fn=MagicMock(),
            )

        user32.ShowWindow.assert_called_once_with(0x1234, 9)

        user32 = MagicMock()
        user32.IsWindow.side_effect = RuntimeError("boom")
        debug_fn = MagicMock()
        running = iter([True, False])
        with patch("src.session_watchdog.ctypes.windll", SimpleNamespace(user32=user32)):
            session_watchdog.run_window_watchdog_loop(
            is_running=lambda: next(running),
                window_handles={"main": 0x1234},
                log_fn=lambda msg: None,
                stop_session=MagicMock(),
                sleep_fn=lambda secs: None,
                debug_fn=debug_fn,
            )
        debug_fn.assert_called_once()

    def test_run_window_watchdog_loop_stops_when_window_disappears(self):
        user32 = MagicMock()
        user32.IsWindow.return_value = False
        stop_session = MagicMock()

        with patch("src.session_watchdog.ctypes.windll", SimpleNamespace(user32=user32)):
            session_watchdog.run_window_watchdog_loop(
                is_running=lambda: True,
                window_handles={"main": 0x1234},
                log_fn=lambda msg: None,
                stop_session=stop_session,
                sleep_fn=lambda secs: None,
                debug_fn=MagicMock(),
            )

        stop_session.assert_called_once()


# ---------------------------------------------------------------------------
# session_subsystems: pause/resume calls pause on anti_kick, not stop
# ---------------------------------------------------------------------------

class TestSessionSubsystemsPauseResume:
    def test_pause_calls_pause_on_anti_kick(self):
        from src.session_subsystems import pause_session_subsystems
        ak = MagicMock()
        pause_session_subsystems(
            healer=None, combat=None, looter=None,
            anti_kick=ak, log_fn=lambda m: None,
        )
        ak.pause.assert_called_once()
        ak.stop.assert_not_called()

    def test_resume_calls_resume_on_anti_kick(self):
        from src.session_subsystems import resume_session_subsystems
        ak = MagicMock()
        resume_session_subsystems(
            healer=None, combat=None, looter=None,
            anti_kick=ak, log_fn=lambda m: None,
        )
        ak.resume.assert_called_once()
        ak.start.assert_not_called()