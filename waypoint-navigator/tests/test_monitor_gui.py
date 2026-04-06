"""
Tests for src/monitor_gui.py — MonitorGui, MonitorConfig

Fully offline: no real Tk window is ever created.
All tests work against the internal ``_state`` dict and the public logic
methods; tkinter is patched away entirely.
"""
from __future__ import annotations

import sys
import types
from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from src.monitor_gui import MonitorConfig, MonitorGui


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Factories
# ─────────────────────────────────────────────────────────────────────────────

def _mock_session() -> MagicMock:
    """Return a session mock with stats_snapshot + event_bus pre-wired."""
    session = MagicMock()
    session.event_bus = MagicMock()
    session.stats_snapshot.return_value = {
        "is_running":        False,
        "routes_completed":  0,
        "heal_fired":        0,
        "mana_fired":        0,
        "loot_events":       0,
        "uptime_secs":       0,
    }
    return session


def _make_gui(session=None, config=None, root=None) -> MonitorGui:
    return MonitorGui(
        session=session or _mock_session(),
        config=config,
        root=root or MagicMock(),
    )


def _subscribed_handlers(gui: MonitorGui) -> Dict[str, Any]:
    """
    Call _subscribe_events() and return a dict of {event_name: handler}
    captured from bus.subscribe calls.
    """
    gui._session.event_bus.reset_mock()
    gui._subscribe_events()
    result = {}
    for c in gui._session.event_bus.subscribe.call_args_list:
        event, handler = c.args
        result[event] = handler
    return result


class _FakeStringVar:
    def __init__(self, master: Any = None, value: str = "") -> None:
        self.master = master
        self.value = value

    def set(self, value: str) -> None:
        self.value = value


def _fake_tk_module(root: Any) -> Any:
    mod = types.SimpleNamespace()
    mod.Tk = MagicMock(return_value=root)
    mod.StringVar = _FakeStringVar
    return mod


def _fake_pil_modules() -> Dict[str, Any]:
    pil_module = types.ModuleType("PIL")
    image_module = types.ModuleType("PIL.Image")
    image_tk_module = types.ModuleType("PIL.ImageTk")

    fake_image = MagicMock()
    fake_image.resize.return_value = fake_image
    image_module.fromarray = MagicMock(return_value=fake_image)  # type: ignore[attr-defined]
    image_module.Resampling = types.SimpleNamespace(LANCZOS="LANCZOS")  # type: ignore[attr-defined]
    image_tk_module.PhotoImage = MagicMock(return_value="photo")  # type: ignore[attr-defined]
    pil_module.Image = image_module  # type: ignore[attr-defined]
    pil_module.ImageTk = image_tk_module  # type: ignore[attr-defined]

    return {
        "PIL": pil_module,
        "PIL.Image": image_module,
        "PIL.ImageTk": image_tk_module,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MonitorConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestMonitorConfig:

    def test_default_title(self):
        cfg = MonitorConfig()
        assert "Monitor" in cfg.title

    def test_default_refresh_ms(self):
        cfg = MonitorConfig()
        assert cfg.refresh_ms == 1000

    def test_custom_values(self):
        cfg = MonitorConfig(title="Test", geometry="800x600", refresh_ms=500)
        assert cfg.title == "Test"
        assert cfg.geometry == "800x600"
        assert cfg.refresh_ms == 500

    def test_default_geometry_is_string(self):
        cfg = MonitorConfig()
        assert isinstance(cfg.geometry, str)
        assert "x" in cfg.geometry

    def test_dataclass_equality(self):
        assert MonitorConfig() == MonitorConfig()
        assert MonitorConfig(refresh_ms=500) != MonitorConfig(refresh_ms=1000)


# ─────────────────────────────────────────────────────────────────────────────
# Construction / properties
# ─────────────────────────────────────────────────────────────────────────────

class TestMonitorGuiConstruction:

    def test_is_built_false_initially(self):
        gui = _make_gui()
        assert gui.is_built is False

    def test_has_session_true(self):
        gui = _make_gui()
        assert gui.has_session is True

    def test_has_session_false_when_none(self):
        gui = MonitorGui(session=None, root=MagicMock())
        assert gui.has_session is False

    def test_state_returns_dict(self):
        gui = _make_gui()
        assert isinstance(gui.state, dict)

    def test_state_has_all_keys(self):
        gui = _make_gui()
        s = gui.state
        for key in ("uptime", "is_running", "routes", "heals", "mana",
                    "loot", "kills", "conditions", "last_watchdog", "last_depot"):
            assert key in s, f"Missing key: {key}"

    def test_state_initial_values(self):
        gui = _make_gui()
        s = gui.state
        assert s["routes"]  == 0
        assert s["heals"]   == 0
        assert s["mana"]    == 0
        assert s["loot"]    == 0
        assert s["kills"]   == 0
        assert s["conditions"] == set()
        assert s["last_watchdog"] == "—"
        assert s["last_depot"]    == "—"

    def test_state_returns_copy(self):
        gui = _make_gui()
        s1 = gui.state
        s2 = gui.state
        assert s1 is not s2

    def test_conditions_copy_is_independent(self):
        gui = _make_gui()
        s = gui.state
        s["conditions"].add("poisoned")
        assert "poisoned" not in gui.state["conditions"]

    def test_default_config_used_when_none(self):
        gui = MonitorGui(session=_mock_session(), root=MagicMock())
        assert isinstance(gui._cfg, MonitorConfig)

    def test_custom_config_stored(self):
        cfg = MonitorConfig(refresh_ms=250)
        gui = _make_gui(config=cfg)
        assert gui._cfg.refresh_ms == 250


class TestBuildLifecycle:

    def test_build_creates_root_stringvars_and_schedules_poll(self):
        root = MagicMock()
        gui = MonitorGui(session=_mock_session(), root=None)

        with patch.object(gui, "_build_layout") as build_layout:
            with patch.object(gui, "_subscribe_events") as subscribe_events:
                with patch.dict(sys.modules, {"tkinter": _fake_tk_module(root)}):
                    gui.build()

        assert gui._root is root
        assert gui.is_built is True
        assert "route" in gui._svars
        assert "targeting_lbl" in gui._svars
        root.title.assert_called_once_with(gui._cfg.title)
        root.geometry.assert_called_once_with(gui._cfg.geometry)
        root.after.assert_called_once_with(gui._cfg.refresh_ms, gui._poll)
        build_layout.assert_called_once()
        subscribe_events.assert_called_once()

    def test_build_is_noop_when_already_built(self):
        gui = _make_gui()
        gui._built = True
        gui.build()
        assert gui._root is not None
        gui._root.title.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Event handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestEventHandlerRouteDone:

    def test_updates_routes(self):
        gui = _make_gui()
        gui._on_route_done({"routes_completed": 5})
        assert gui.state["routes"] == 5

    def test_accumulates_routes(self):
        gui = _make_gui()
        gui._on_route_done({"routes_completed": 3})
        gui._on_route_done({"routes_completed": 4})
        assert gui.state["routes"] == 4   # last value wins (authoritative)

    def test_missing_key_keeps_current(self):
        gui = _make_gui()
        gui._state["routes"] = 7
        gui._on_route_done({})
        assert gui.state["routes"] == 7


class TestEventHandlerDepotDone:

    def test_ok_cycle_stored(self):
        gui = _make_gui()
        gui._on_depot_done({"success": True, "cycles": 2})
        assert gui.state["last_depot"] == "cycle 2 (ok)"

    def test_failed_cycle_stored(self):
        gui = _make_gui()
        gui._on_depot_done({"success": False, "cycles": 1})
        assert gui.state["last_depot"] == "cycle 1 (fail)"

    def test_missing_cycles_shows_question_mark(self):
        gui = _make_gui()
        gui._on_depot_done({"success": True})
        assert "?" in gui.state["last_depot"]


class TestEventHandlerCondition:

    def test_condition_added(self):
        gui = _make_gui()
        gui._on_condition({"condition": "poisoned"})
        assert "poisoned" in gui.state["conditions"]

    def test_multiple_conditions_accumulate(self):
        gui = _make_gui()
        gui._on_condition({"condition": "poisoned"})
        gui._on_condition({"condition": "burning"})
        assert {"poisoned", "burning"} == gui.state["conditions"]

    def test_empty_condition_ignored(self):
        gui = _make_gui()
        gui._on_condition({"condition": ""})
        assert gui.state["conditions"] == set()

    def test_dupliaction_does_not_add_twice(self):
        gui = _make_gui()
        gui._on_condition({"condition": "poisoned"})
        gui._on_condition({"condition": "poisoned"})
        assert len(gui.state["conditions"]) == 1


class TestEventHandlerConditionClear:

    def test_condition_removed(self):
        gui = _make_gui()
        gui._on_condition({"condition": "poisoned"})
        gui._on_condition_clear({"condition": "poisoned"})
        assert "poisoned" not in gui.state["conditions"]

    def test_clear_nonexistent_no_error(self):
        gui = _make_gui()
        gui._on_condition_clear({"condition": "nonexistent"})  # no KeyError
        assert gui.state["conditions"] == set()

    def test_only_specified_condition_removed(self):
        gui = _make_gui()
        gui._on_condition({"condition": "poisoned"})
        gui._on_condition({"condition": "burning"})
        gui._on_condition_clear({"condition": "poisoned"})
        assert "burning" in gui.state["conditions"]
        assert "poisoned" not in gui.state["conditions"]


class TestEventHandlerWatchdog:

    def test_idle_seconds_stored(self):
        gui = _make_gui()
        gui._on_watchdog({"idle_seconds": 30.5})
        assert "30.5s idle" == gui.state["last_watchdog"]

    def test_idle_secs_fallback_key(self):
        gui = _make_gui()
        gui._on_watchdog({"idle_secs": 20})
        assert "20s idle" in gui.state["last_watchdog"]


class TestEventHandlerHealManaKill:

    def test_heal_increments(self):
        gui = _make_gui()
        gui._on_heal({})
        gui._on_heal({})
        assert gui.state["heals"] == 2

    def test_mana_increments(self):
        gui = _make_gui()
        gui._on_mana({})
        assert gui.state["mana"] == 1

    def test_kill_increments(self):
        gui = _make_gui()
        gui._on_kill({})
        gui._on_kill({})
        gui._on_kill({})
        assert gui.state["kills"] == 3

    def test_heal_mana_kill_independent(self):
        gui = _make_gui()
        gui._on_heal({})
        gui._on_mana({})
        gui._on_kill({})
        assert gui.state["heals"] == 1
        assert gui.state["mana"]  == 1
        assert gui.state["kills"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Polling
# ─────────────────────────────────────────────────────────────────────────────

class TestMonitorGuiPoll:

    def test_poll_prefers_monitor_snapshot_contract(self):
        class SessionStub:
            def __init__(self) -> None:
                self.event_bus = MagicMock()
                self.config = types.SimpleNamespace(route_file="routes/ignored.json")

            def monitor_snapshot(self) -> dict[str, object]:
                return {
                    "is_running": True,
                    "routes_completed": 9,
                    "heal_fired": 4,
                    "mana_fired": 1,
                    "loot_events": 2,
                    "uptime_seconds": 125,
                    "route": "snapshot.json",
                    "current_wpt": "[node  10,20,7]",
                    "break_info": "next 5m | #1",
                    "soak_mem": "321MB (peak 456)",
                }

            def stats_snapshot(self) -> dict[str, object]:
                raise AssertionError("stats_snapshot should not be used when monitor_snapshot exists")

        gui = _make_gui(session=SessionStub())

        gui._poll()

        assert gui.state["route"] == "snapshot.json"
        assert gui.state["current_wpt"] == "[node  10,20,7]"
        assert gui.state["break_info"] == "next 5m | #1"
        assert gui.state["soak_mem"] == "321MB (peak 456)"
        assert gui.state["uptime"] == "2m"

    def test_poll_reads_stats_snapshot(self):
        session = _mock_session()
        session.stats_snapshot.return_value = {
            "is_running": True,
            "routes_completed": 3,
            "heal_fired": 5,
            "mana_fired": 2,
            "loot_events": 7,
            "uptime_secs": 45,
        }
        gui = _make_gui(session=session)
        gui._poll()
        s = gui.state
        assert s["routes"]     == 3
        assert s["heals"]      == 5
        assert s["mana"]       == 2
        assert s["loot"]       == 7
        assert s["is_running"] is True

    def test_uptime_seconds_format(self):
        session = _mock_session()
        session.stats_snapshot.return_value = {"uptime_secs": 45, **_base_snap()}
        gui = _make_gui(session=session)
        gui._poll()
        assert gui.state["uptime"] == "45s"

    def test_uptime_minutes_format(self):
        session = _mock_session()
        session.stats_snapshot.return_value = {"uptime_secs": 125, **_base_snap()}
        gui = _make_gui(session=session)
        gui._poll()
        assert gui.state["uptime"] == "2m"

    def test_uptime_hours_format(self):
        session = _mock_session()
        session.stats_snapshot.return_value = {"uptime_secs": 7200, **_base_snap()}
        gui = _make_gui(session=session)
        gui._poll()
        assert gui.state["uptime"] == "2.0h"

    def test_exception_in_stats_snapshot_swallowed(self):
        session = _mock_session()
        session.stats_snapshot.side_effect = RuntimeError("boom")
        gui = _make_gui(session=session)
        gui._poll()    # must not raise

    def test_poll_schedules_next_call_when_built(self):
        gui = _make_gui()
        gui._built = True       # simulate built state
        with patch.object(gui, '_flush_svars'):
            gui._poll()
            assert gui._root is not None
            gui._root.after.assert_called_with(gui._cfg.refresh_ms, gui._poll)

    def test_poll_does_not_schedule_when_not_built(self):
        gui = _make_gui()
        gui._poll()
        assert gui._root is not None
        gui._root.after.assert_not_called()

    def test_poll_updates_break_memory_and_current_wpt(self):
        session = _mock_session()
        session.config.route_file = "routes/hunt.json"
        session._position = object()
        live_pos = types.SimpleNamespace(x=10, y=20, z=7)
        current_coord = types.SimpleNamespace(x=100, y=200, z=7)
        session._executor = types.SimpleNamespace(
            _current_pos=live_pos,
            _current_instr=types.SimpleNamespace(kind="node", coord=current_coord),
        )
        session.stats_snapshot.return_value = {
            **_base_snap(),
            "is_running": True,
            "break_scheduler": {"on_break": False, "next_break_in_m": 12, "breaks_taken": 3},
            "soak_monitor": {"peak_memory_mb": 456.0, "latest": {"rss_mb": 321.0}},
        }
        gui = _make_gui(session=session)
        gui._poll()

        assert gui.state["route"] == "hunt.json"
        assert gui.state["break_info"] == "next 12m | #3"
        assert gui.state["soak_mem"] == "321MB (peak 456)"
        assert gui.state["current_wpt"] == "[node  100,200,7]"
        assert session._position is live_pos

    def test_poll_calls_flush_and_minimap_when_built(self):
        gui = _make_gui()
        gui._built = True
        with patch.object(gui, "_flush_svars") as flush_svars:
            with patch.object(gui, "_update_minimap") as update_minimap:
                gui._poll()
        flush_svars.assert_called_once()
        update_minimap.assert_called_once()


def _base_snap() -> dict:
    return {
        "is_running": False,
        "routes_completed": 0,
        "heal_fired": 0,
        "mana_fired": 0,
        "loot_events": 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# _flush_svars
# ─────────────────────────────────────────────────────────────────────────────

class TestFlushSvars:

    def _make_with_svars(self) -> MonitorGui:
        gui = _make_gui()
        for key in ("route",
                    "uptime", "is_running", "routes", "heals", "mana",
                    "loot", "kills", "conditions", "last_watchdog", "last_depot"):
            gui._svars[key] = MagicMock()
        return gui

    def test_is_running_true_shows_YES(self):
        gui = self._make_with_svars()
        gui._state["is_running"] = True
        gui._flush_svars()
        gui._svars["is_running"].set.assert_called_with("YES")

    def test_is_running_false_shows_no(self):
        gui = self._make_with_svars()
        gui._state["is_running"] = False
        gui._flush_svars()
        gui._svars["is_running"].set.assert_called_with("no")

    def test_empty_conditions_shows_dash(self):
        gui = self._make_with_svars()
        gui._state["conditions"] = set()
        gui._flush_svars()
        gui._svars["conditions"].set.assert_called_with("—")

    def test_conditions_sorted_comma_joined(self):
        gui = self._make_with_svars()
        gui._state["conditions"] = {"poisoned", "burning", "cursed"}
        gui._flush_svars()
        expected = ", ".join(sorted({"poisoned", "burning", "cursed"}))
        gui._svars["conditions"].set.assert_called_with(expected)

    def test_numeric_fields_converted_to_str(self):
        gui = self._make_with_svars()
        gui._state["routes"] = 42
        gui._flush_svars()
        gui._svars["routes"].set.assert_called_with("42")

    def test_no_svars_no_crash(self):
        gui = _make_gui()
        gui._flush_svars()   # _svars is empty → should not raise

    def test_exception_in_svar_set_swallowed(self):
        gui = self._make_with_svars()
        gui._svars["uptime"].set.side_effect = RuntimeError("Tk gone")
        gui._flush_svars()   # must not propagate

    def test_toggle_button_colors_follow_flags(self):
        gui = self._make_with_svars()
        gui._btn_targeting = MagicMock()
        gui._btn_walking = MagicMock()
        gui._btn_looting = MagicMock()
        gui._targeting_on = True
        gui._walking_on = False
        gui._looting_on = True
        gui._flush_svars()

        gui._btn_targeting.configure.assert_called_with(bg=gui._CLR_ON, fg="#1e1e2e")
        gui._btn_walking.configure.assert_called_with(bg=gui._CLR_OFF, fg="#1e1e2e")
        gui._btn_looting.configure.assert_called_with(bg=gui._CLR_ON, fg="#1e1e2e")


# ─────────────────────────────────────────────────────────────────────────────
# Button callbacks
# ─────────────────────────────────────────────────────────────────────────────

class TestButtonCallbacks:

    def test_start_click_calls_session_start(self):
        session = _mock_session()
        gui = _make_gui(session=session)
        gui._on_start_click()
        session.start.assert_called_once()

    def test_stop_click_calls_session_stop(self):
        session = _mock_session()
        gui = _make_gui(session=session)
        gui._on_stop_click()
        session.stop.assert_called_once()

    def test_start_exception_swallowed(self):
        session = _mock_session()
        session.start.side_effect = RuntimeError("not connected")
        gui = _make_gui(session=session)
        gui._on_start_click()   # must not propagate

    def test_stop_exception_swallowed(self):
        session = _mock_session()
        session.stop.side_effect = RuntimeError("not running")
        gui = _make_gui(session=session)
        gui._on_stop_click()    # must not propagate

    def test_toggle_targeting_pauses_combat(self):
        session = _mock_session()
        combat = MagicMock()
        session._combat_mgr = combat
        gui = _make_gui(session=session)
        gui._on_toggle_targeting()
        combat.pause.assert_called_once()

    def test_toggle_walking_updates_executor_flag(self):
        session = _mock_session()
        executor = types.SimpleNamespace(_walking_paused=False)
        session._executor = executor
        gui = _make_gui(session=session)
        gui._on_toggle_walking()
        assert executor._walking_paused is True

    def test_print_position_prefers_executor_position(self):
        session = _mock_session()
        session._position = types.SimpleNamespace(x=1, y=2, z=3)
        session._executor = types.SimpleNamespace(_current_pos=types.SimpleNamespace(x=10, y=20, z=7))
        gui = _make_gui(session=session)
        with patch("src.monitor_gui._log") as log_mock:
            gui._on_print_position()
        assert any("10, 20, 7" in line for line in gui._log_lines)
        log_mock.info.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Event subscriptions
# ─────────────────────────────────────────────────────────────────────────────

class TestEventSubscriptions:

    def test_all_events_subscribed(self):
        gui = _make_gui()
        handlers = _subscribed_handlers(gui)
        expected = {
            "route_done", "depot_done", "condition", "condition_clear",
            "watchdog", "heal", "mana", "kill",
        }
        assert expected == set(handlers.keys())

    def test_route_done_handler_correct(self):
        gui = _make_gui()
        handlers = _subscribed_handlers(gui)
        assert handlers["route_done"] == gui._on_route_done

    def test_condition_handler_correct(self):
        gui = _make_gui()
        handlers = _subscribed_handlers(gui)
        assert handlers["condition"] == gui._on_condition

    def test_condition_clear_handler_correct(self):
        gui = _make_gui()
        handlers = _subscribed_handlers(gui)
        assert handlers["condition_clear"] == gui._on_condition_clear

    def test_watchdog_handler_correct(self):
        gui = _make_gui()
        handlers = _subscribed_handlers(gui)
        assert handlers["watchdog"] == gui._on_watchdog

    def test_heal_handler_correct(self):
        gui = _make_gui()
        handlers = _subscribed_handlers(gui)
        assert handlers["heal"] == gui._on_heal

    def test_mana_handler_correct(self):
        gui = _make_gui()
        handlers = _subscribed_handlers(gui)
        assert handlers["mana"] == gui._on_mana

    def test_kill_handler_correct(self):
        gui = _make_gui()
        handlers = _subscribed_handlers(gui)
        assert handlers["kill"] == gui._on_kill


# ─────────────────────────────────────────────────────────────────────────────
# close()
# ─────────────────────────────────────────────────────────────────────────────

class TestClose:

    def test_close_destroys_root(self):
        root = MagicMock()
        gui = _make_gui(root=root)
        gui.close()
        root.destroy.assert_called_once()

    def test_close_before_build_no_crash(self):
        gui = MonitorGui(session=_mock_session(), root=None)
        gui.close()   # root is None → should not raise

    def test_close_exception_swallowed(self):
        root = MagicMock()
        root.destroy.side_effect = RuntimeError("already gone")
        gui = _make_gui(root=root)
        gui.close()   # must not propagate


class TestAppendLogAndMinimap:

    def test_append_log_updates_widget_and_caps_buffer(self):
        gui = _make_gui()
        gui._log_text = MagicMock()
        for idx in range(205):
            gui.append_log(f"line {idx}")
        assert len(gui._log_lines) == 200
        gui._log_text.insert.assert_called()
        gui._log_text.see.assert_called_with("end")

    def test_update_minimap_without_position_shows_placeholder(self):
        gui = _make_gui()
        gui._minimap_canvas = MagicMock()
        gui._session._position = None
        gui._session._executor = None
        gui._session._loader = None

        with patch.dict(sys.modules, _fake_pil_modules()):
            gui._update_minimap()

        gui._minimap_canvas.delete.assert_called_with("all")
        gui._minimap_canvas.create_text.assert_called()

    def test_update_minimap_renders_loaded_floor(self):
        gui = _make_gui()
        gui._minimap_canvas = MagicMock()
        gui._session._position = types.SimpleNamespace(x=0, y=0, z=7, to_pixel=lambda: (30, 30))
        gui._session._executor = None
        gui._session._loader = types.SimpleNamespace(
            floor_loaded=lambda z: True,
            get_map_image=lambda z: np.zeros((80, 80, 4), dtype=np.uint8),
        )

        with patch.dict(sys.modules, _fake_pil_modules()):
            gui._update_minimap()

        gui._minimap_canvas.create_image.assert_called_once()
        gui._minimap_canvas.create_text.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# BotSession.open_monitor integration
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionOpenMonitor:

    def test_open_monitor_creates_and_runs_gui(self):
        from src.session import BotSession, SessionConfig

        session = BotSession(SessionConfig(dry_run=True))
        run_calls: list = []

        with patch("src.monitor_gui.MonitorGui.run", lambda self: run_calls.append(self)):
            session.open_monitor()

        assert len(run_calls) == 1

    def test_open_monitor_passes_config(self):
        from src.session import BotSession, SessionConfig
        from src.monitor_gui import MonitorConfig

        session = BotSession(SessionConfig(dry_run=True))
        cfg = MonitorConfig(title="Custom", refresh_ms=250)
        captured: list = []

        def spy_run(self_inner):
            captured.append(self_inner._cfg)

        with patch("src.monitor_gui.MonitorGui.run", spy_run):
            session.open_monitor(config=cfg)

        assert captured[0].title == "Custom"
        assert captured[0].refresh_ms == 250

    def test_open_monitor_session_is_self(self):
        from src.session import BotSession, SessionConfig

        session = BotSession(SessionConfig(dry_run=True))
        captured: list = []

        def spy_run(self_inner):
            captured.append(self_inner._session)

        with patch("src.monitor_gui.MonitorGui.run", spy_run):
            session.open_monitor()

        assert captured[0] is session


# ─────────────────────────────────────────────────────────────────────────────
# MonitorGui in __init__.py exports
# ─────────────────────────────────────────────────────────────────────────────

class TestPackageExports:

    def test_monitor_gui_exported(self):
        import src
        assert hasattr(src, "MonitorGui")

    def test_monitor_config_exported(self):
        import src
        assert hasattr(src, "MonitorConfig")
