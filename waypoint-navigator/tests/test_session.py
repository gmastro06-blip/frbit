"""
Tests para src/session.py — BotSession, SessionConfig.
100 % offline: InputController, TibiaMapLoader, WaypointNavigator y
AutoHealer se reemplazan con mocks para no necesitar Tibia ni red.
"""
from __future__ import annotations

import json
import types
import threading
import time
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch, call

import pytest

from src.session import BotSession, SessionConfig
from src.models import Coordinate, Route, Waypoint


# ─────────────────────────────────────────────────────────────────────────────
# Helpers genéricos
# ─────────────────────────────────────────────────────────────────────────────

def _logs() -> list:
    msgs: list = []
    return msgs


def _make_config(**kw) -> SessionConfig:
    defaults: dict = dict(
        route_file="",
        heal_hp_pct=70,
        start_delay=0.0,   # sin espera en tests
        loop_route=False,
    )
    defaults.update(kw)
    return SessionConfig(**defaults)


def _mock_route(found: bool = True, steps: int = 3) -> MagicMock:
    route = MagicMock(spec=Route)
    route.found = found
    route.steps = [Coordinate(100 + i, 100, 7) for i in range(steps)]
    return route


def _mock_navigator(route: MagicMock | None = None) -> MagicMock:
    nav = MagicMock()
    nav.navigate.return_value = route or _mock_route()
    nav.navigate_multifloor.return_value = [route or _mock_route()]
    return nav


def _mock_ctrl(connected: bool = True) -> MagicMock:
    ctrl = MagicMock()
    ctrl.is_connected.return_value = connected
    return ctrl


def _mock_healer() -> MagicMock:
    healer = MagicMock()
    healer.force_heal.return_value = True
    healer.force_mana.return_value = True
    healer._hp_pct = 100.0
    healer._mp_pct = 100.0
    return healer


def _make_route_file(tmp_path: Path, waypoints: list[dict] | None = None) -> Path:
    """Crea un archivo JSON de ruta válido con ≥ 2 waypoints por defecto."""
    data = {"waypoints": waypoints or [
        {"name": "A", "x": 32369, "y": 32241, "z": 7},
        {"name": "B", "x": 32343, "y": 32211, "z": 7},
        {"name": "C", "x": 32300, "y": 32200, "z": 7},
    ]}
    path = tmp_path / "route.json"
    path.write_text(json.dumps(data))
    return path


# ─────────────────────────────────────────────────────────────────────────────
# SessionConfig: valores por defecto, save/load, casos borde
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfig:

    def test_default_values(self):
        cfg = SessionConfig()
        assert cfg.heal_hp_pct       == 70
        assert cfg.heal_emergency_pct == 30
        assert cfg.mana_threshold_pct == 30
        assert cfg.heal_hotkey_vk    == 0x70
        assert cfg.mana_hotkey_vk    == 0x71
        assert cfg.emergency_hotkey_vk == 0x72
        assert cfg.auto_loot         is False
        assert cfg.depot_after_run   is False
        assert cfg.input_method      == "interception"
        assert cfg.loop_route        is False

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        cfg = SessionConfig(heal_hp_pct=55, start_delay=1.5, loop_route=True)
        path = tmp_path / "session.json"
        cfg.save(path)
        loaded = SessionConfig.load(path)
        assert loaded.heal_hp_pct  == 55
        assert loaded.start_delay  == pytest.approx(1.5)
        assert loaded.loop_route   is True

    def test_load_missing_file_returns_defaults(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        cfg = SessionConfig.load(path)
        assert cfg.heal_hp_pct == 70

    def test_load_ignores_unknown_keys(self, tmp_path: Path):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({
            "heal_hp_pct": 60,
            "future_key_that_doesnt_exist": "ignored",
        }))
        cfg = SessionConfig.load(path)
        assert cfg.heal_hp_pct == 60

    def test_saved_json_contains_expected_fields(self, tmp_path: Path):
        path = tmp_path / "s.json"
        SessionConfig().save(path)
        data = json.loads(path.read_text())
        assert "heal_hp_pct" in data
        assert "heal_hotkey_vk" in data
        assert "route_file" in data


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: construcción y propiedades
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionConstruction:

    def test_not_running_at_creation(self):
        session = BotSession(config=_make_config())
        assert session.is_running is False

    def test_stats_have_expected_keys(self):
        session = BotSession(config=_make_config())
        s = session.stats
        assert "routes_completed" in s
        assert "heal_fired" in s
        assert "mana_fired" in s
        assert "start_time" in s

    def test_stats_initial_counts_are_zero(self):
        session = BotSession(config=_make_config())
        s = session.stats
        assert s["routes_completed"] == 0
        assert s["heal_fired"]       == 0
        assert s["mana_fired"]       == 0

    def test_config_property_returns_config(self):
        cfg = _make_config(heal_hp_pct=42)
        session = BotSession(config=cfg)
        assert session.config.heal_hp_pct == 42

    def test_log_callback_called(self):
        msgs: list = []
        session = BotSession(config=_make_config(), log_callback=msgs.append)
        session._log("hello")
        assert any("hello" in m for m in msgs)

    def test_default_log_callback_is_print(self, capsys):
        session = BotSession(config=_make_config())
        session._log("test_print")
        assert "test_print" in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: métodos sin healer activo
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionNoHealer:

    def test_force_heal_returns_false_when_no_healer(self):
        session = BotSession(config=_make_config())
        assert session.force_heal() is False

    def test_force_mana_returns_false_when_no_healer(self):
        session = BotSession(config=_make_config())
        assert session.force_mana() is False

    def test_set_hp_mp_no_crash_when_no_healer(self):
        session = BotSession(config=_make_config())
        # No debe lanzar excepción
        session.set_hp_mp(50, 30)

    def test_stop_when_not_running_is_noop(self):
        session = BotSession(config=_make_config())
        session.stop()   # no debe lanzar
        assert session.is_running is False

    def test_set_position_from_executor_reseeds_radar_anchor(self):
        session = BotSession(config=_make_config())
        radar = MagicMock()
        radar._last_coord = None
        session._radar = radar
        coord = Coordinate(32354, 32214, 7)

        session._set_position_from_executor(coord)

        assert session._position == coord
        assert session._radar._last_coord == coord

    def test_set_position_from_executor_sets_deadreckon_flag(self):
        """Dead-reckoned position should mark the flag for reacquisition."""
        session = BotSession(config=_make_config())
        session._position_from_deadreckon = False  # initial state
        coord = Coordinate(32354, 32214, 7)

        session._set_position_from_executor(coord)

        assert session._position_from_deadreckon is True


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: _inc_stat
# ─────────────────────────────────────────────────────────────────────────────

class TestIncStat:

    def test_increments_existing_key(self):
        session = BotSession(config=_make_config())
        session._inc_stat("heal_fired")
        assert session.stats["heal_fired"] == 1

    def test_increments_multiple_times(self):
        session = BotSession(config=_make_config())
        for _ in range(5):
            session._inc_stat("mana_fired")
        assert session.stats["mana_fired"] == 5

    def test_creates_new_key_if_missing(self):
        session = BotSession(config=_make_config())
        session._inc_stat("custom_counter")
        assert session.stats["custom_counter"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: load_waypoints
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadWaypoints:

    def test_loads_waypoints_from_dict_format(self, tmp_path: Path):
        path = _make_route_file(tmp_path)
        session = BotSession(config=_make_config())
        wps = session.load_waypoints(path)
        assert len(wps) == 3
        assert wps[0].name == "A"
        assert wps[0].coord == Coordinate(32369, 32241, 7)

    def test_loads_waypoints_from_list_format(self, tmp_path: Path):
        """Formato alternativo: JSON array en raíz."""
        data = [
            {"name": "P1", "x": 100, "y": 200, "z": 7},
            {"name": "P2", "x": 110, "y": 210, "z": 7},
        ]
        path = tmp_path / "list_route.json"
        path.write_text(json.dumps(data))
        session = BotSession(config=_make_config())
        wps = session.load_waypoints(path)
        assert len(wps) == 2
        assert wps[1].name == "P2"

    def test_missing_file_raises_file_not_found(self, tmp_path: Path):
        session = BotSession(config=_make_config())
        with pytest.raises(FileNotFoundError):
            session.load_waypoints(tmp_path / "nonexistent.json")

    def test_empty_waypoints_list(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"waypoints": []}))
        session = BotSession(config=_make_config())
        wps = session.load_waypoints(path)
        assert wps == []

    def test_waypoints_default_z_is_7(self, tmp_path: Path):
        path = tmp_path / "no_z.json"
        path.write_text(json.dumps({"waypoints": [
            {"name": "W", "x": 111, "y": 222},   # sin z
        ]}))
        session = BotSession(config=_make_config())
        wps = session.load_waypoints(path)
        assert wps[0].coord.z == 7


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: navigate_to (sin llamar start — navigator creado internamente)
# ─────────────────────────────────────────────────────────────────────────────

class TestNavigateTo:

    def test_navigate_to_same_floor(self):
        mock_nav = _mock_navigator()
        session = BotSession(config=_make_config())
        with patch("src.session.WaypointNavigator", return_value=mock_nav), \
             patch("src.session.TibiaMapLoader"):
            routes = session.navigate_to(
                Coordinate(100, 100, 7),
                Coordinate(200, 200, 7),
            )
        assert len(routes) == 1
        mock_nav.navigate.assert_called_once()

    def test_navigate_to_different_floors_uses_multifloor(self):
        mock_nav = _mock_navigator()
        session = BotSession(config=_make_config())
        with patch("src.session.WaypointNavigator", return_value=mock_nav), \
             patch("src.session.TibiaMapLoader"):
            routes = session.navigate_to(
                Coordinate(100, 100, 7),
                Coordinate(100, 100, 8),
            )
        mock_nav.navigate_multifloor.assert_called_once()
        assert len(routes) >= 1

    def test_navigate_to_multifloor_explicit_flag(self):
        mock_nav = _mock_navigator()
        session = BotSession(config=_make_config())
        with patch("src.session.WaypointNavigator", return_value=mock_nav), \
             patch("src.session.TibiaMapLoader"):
            session.navigate_to(
                Coordinate(100, 100, 7),
                Coordinate(200, 200, 7),
                multifloor=True,
            )
        mock_nav.navigate_multifloor.assert_called_once()

    def test_navigate_to_reuses_existing_navigator(self):
        mock_nav = _mock_navigator()
        session = BotSession(config=_make_config())
        session._navigator = mock_nav

        session.navigate_to(Coordinate(1, 1, 7), Coordinate(2, 2, 7))
        session.navigate_to(Coordinate(1, 1, 7), Coordinate(3, 3, 7))

        # El navegador se reutiliza; navigate() llama 2 veces
        assert mock_nav.navigate.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: start / stop con mocks (sin red ni Tibia)
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionStartStop:

    def _patched_start(self, session: BotSession):
        """Context manager que parchea todo lo externo antes de start()."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            mock_ctrl   = _mock_ctrl()
            mock_healer = _mock_healer()
            mock_nav    = _mock_navigator()
            with patch("src.session.InputController", return_value=mock_ctrl), \
                 patch("src.session.TibiaMapLoader"), \
                 patch("src.session.WaypointNavigator", return_value=mock_nav), \
                 patch("src.session.AutoHealer", return_value=mock_healer):
                yield mock_ctrl, mock_healer, mock_nav

        return _ctx()

    def test_start_sets_running(self):
        cfg = _make_config(start_delay=0.0)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        with self._patched_start(session):
            session.start()
            assert session.is_running is True
            session.stop()

    def test_stop_clears_running(self):
        cfg = _make_config(start_delay=0.0)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        with self._patched_start(session):
            session.start()
            session.stop()
        assert session.is_running is False

    def test_double_start_is_noop(self):
        cfg = _make_config(start_delay=0.0)
        msgs: list = []
        session = BotSession(config=cfg, log_callback=msgs.append)
        with self._patched_start(session):
            session.start()
            session.start()   # segundo start — no-op
            assert len([m for m in msgs if "already running" in m.lower()]) == 1
            session.stop()

    def test_start_calls_healer_start(self):
        cfg = _make_config(start_delay=0.0)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        mock_ctrl   = _mock_ctrl()
        mock_healer = _mock_healer()
        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session.TibiaMapLoader"), \
             patch("src.session.WaypointNavigator", return_value=_mock_navigator()), \
             patch("src.session.AutoHealer", return_value=mock_healer):
            session.start()
            mock_healer.start.assert_called_once()
            session.stop()

    def test_stop_calls_healer_stop(self):
        cfg = _make_config(start_delay=0.0)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        mock_healer = _mock_healer()
        with patch("src.session.InputController", return_value=_mock_ctrl()), \
             patch("src.session.TibiaMapLoader"), \
             patch("src.session.WaypointNavigator", return_value=_mock_navigator()), \
             patch("src.session.AutoHealer", return_value=mock_healer):
            session.start()
            session.stop()
        mock_healer.stop.assert_called_once()

    def test_stats_start_time_set_after_start(self):
        cfg = _make_config(start_delay=0.0)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        with self._patched_start(session):
            session.start()
            assert session.stats["start_time"] is not None
            session.stop()


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: force_heal / force_mana con healer activo
# ─────────────────────────────────────────────────────────────────────────────

class TestForceHealing:

    def test_force_heal_delegates_to_healer(self):
        session = BotSession(config=_make_config())
        mock_healer = _mock_healer()
        session._healer = mock_healer
        result = session.force_heal()
        mock_healer.force_heal.assert_called_once()
        assert result is True

    def test_force_mana_delegates_to_healer(self):
        session = BotSession(config=_make_config())
        mock_healer = _mock_healer()
        session._healer = mock_healer
        result = session.force_mana()
        mock_healer.force_mana.assert_called_once()
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: set_hp_mp con healer activo
# ─────────────────────────────────────────────────────────────────────────────

class TestSetHpMp:

    def test_set_hp_mp_updates_healer_directly(self):
        session = BotSession(config=_make_config())
        mock_healer = MagicMock()
        session._healer = mock_healer
        session.set_hp_mp(45, 20)
        assert mock_healer._hp_pct == 45
        assert mock_healer._mp_pct == 20


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: _run_loop — casos edge sin conexión real
# ─────────────────────────────────────────────────────────────────────────────

class TestRunLoop:

    def test_no_route_file_loop_exits_on_stop(self):
        """Sin route_file el loop espera; stop() lo termina limpiamente."""
        cfg = _make_config(route_file="", start_delay=0.0)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        session._running = True
        t = threading.Thread(target=session._run_loop, daemon=True)
        t.start()
        time.sleep(0.05)
        session._running = False
        t.join(timeout=2.0)
        assert not t.is_alive(), "El loop no terminó en tiempo"

    def test_missing_route_file_sets_running_false(self, tmp_path: Path):
        cfg = _make_config(route_file=str(tmp_path / "nonexistent.json"),
                           start_delay=0.0)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        session._running = True
        session._run_loop()
        assert session._running is False

    def test_route_with_one_waypoint_sets_running_false(self, tmp_path: Path):
        """Ruta con < 2 waypoints termina el loop."""
        path = _make_route_file(tmp_path, waypoints=[
            {"name": "Solo", "x": 100, "y": 100, "z": 7},
        ])
        cfg = _make_config(route_file=str(path), start_delay=0.0)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        session._running = True
        session._run_loop()
        assert session._running is False

    def test_run_loop_increments_routes_completed(self, tmp_path: Path):
        """Un ciclo completo incrementa routes_completed."""
        path = _make_route_file(tmp_path)
        cfg = _make_config(route_file=str(path), start_delay=0.0,
                           loop_route=False)
        session = BotSession(config=cfg, log_callback=lambda m: None)
        mock_nav = _mock_navigator()
        session._navigator = mock_nav
        session._ctrl = None   # _exec_route verifica is_connected → skip

        session._running = True
        session._run_loop()
        assert session.stats["routes_completed"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# TestResetStats
# ─────────────────────────────────────────────────────────────────────────────

class TestResetStats:

    def test_reset_clears_routes_completed(self):
        session = BotSession(config=_make_config())
        session._inc_stat("routes_completed")
        session._inc_stat("routes_completed")
        session.reset_stats()
        assert session.stats["routes_completed"] == 0

    def test_reset_clears_all_counters(self):
        session = BotSession(config=_make_config())
        for key in ("routes_completed", "heal_fired", "mana_fired", "loot_events"):
            session._inc_stat(key)
        session.reset_stats()
        for key in ("routes_completed", "heal_fired", "mana_fired", "loot_events"):
            assert session.stats[key] == 0

    def test_reset_preserves_start_time(self):
        session = BotSession(config=_make_config())
        session._stats["start_time"] = 12345.0
        session.reset_stats()
        assert session.stats["start_time"] == 12345.0

    def test_reset_on_fresh_session_is_noop(self):
        session = BotSession(config=_make_config())
        session.reset_stats()  # already zeros — must not raise
        for key in ("routes_completed", "heal_fired", "mana_fired", "loot_events"):
            assert session.stats[key] == 0

    def test_inc_after_reset_starts_from_one(self):
        session = BotSession(config=_make_config())
        session._inc_stat("heal_fired")
        session._inc_stat("heal_fired")
        session.reset_stats()
        session._inc_stat("heal_fired")
        assert session.stats["heal_fired"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# TestUptime
# ─────────────────────────────────────────────────────────────────────────────

class TestUptime:

    def test_uptime_none_when_not_running(self):
        session = BotSession(config=_make_config())
        assert session.uptime() is None

    def test_uptime_none_when_start_time_not_set(self):
        session = BotSession(config=_make_config())
        session._running = True
        session._stats["start_time"] = None
        assert session.uptime() is None

    def test_uptime_positive_when_running(self):
        session = BotSession(config=_make_config())
        session._running = True
        session._stats["start_time"] = time.monotonic() - 5.0
        up = session.uptime()
        assert up is not None
        assert up >= 5.0

    def test_uptime_grows_over_time(self):
        session = BotSession(config=_make_config())
        session._running = True
        session._stats["start_time"] = time.monotonic() - 1.0
        up1 = session.uptime()
        time.sleep(0.05)
        up2 = session.uptime()
        assert up1 is not None and up2 is not None
        assert up2 > up1

    def test_uptime_not_running_after_stop_flag(self):
        session = BotSession(config=_make_config())
        session._running = True
        session._stats["start_time"] = time.monotonic()
        session._running = False
        assert session.uptime() is None


# ─────────────────────────────────────────────────────────────────────────────
# TestStatsSummary
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsSummary:

    def test_returns_string(self):
        session = BotSession(config=_make_config())
        assert isinstance(session.stats_summary(), str)

    def test_contains_routes(self):
        session = BotSession(config=_make_config())
        session._inc_stat("routes_completed")
        assert "routes=1" in session.stats_summary()

    def test_contains_heals(self):
        session = BotSession(config=_make_config())
        for _ in range(3):
            session._inc_stat("heal_fired")
        assert "heals=3" in session.stats_summary()

    def test_contains_mana(self):
        session = BotSession(config=_make_config())
        session._inc_stat("mana_fired")
        session._inc_stat("mana_fired")
        assert "mana=2" in session.stats_summary()

    def test_shows_stopped_when_not_running(self):
        session = BotSession(config=_make_config())
        assert "stopped" in session.stats_summary()

    def test_shows_uptime_when_running(self):
        session = BotSession(config=_make_config())
        session._running = True
        session._stats["start_time"] = time.monotonic() - 10.0
        summary = session.stats_summary()
        assert "s" in summary and "stopped" not in summary


# ─────────────────────────────────────────────────────────────────────────────
# TestUpdateConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionUpdateConfig:

    def test_config_replaced(self):
        session = BotSession(config=_make_config(heal_hp_pct=70))
        new_cfg = _make_config(heal_hp_pct=50)
        session.update_config(new_cfg)
        assert session.config.heal_hp_pct == 50

    def test_config_is_new_object(self):
        session = BotSession(config=_make_config())
        new_cfg = _make_config()
        session.update_config(new_cfg)
        assert session.config is new_cfg

    def test_stats_unchanged_after_update(self):
        session = BotSession(config=_make_config())
        session._inc_stat("heal_fired")
        session.update_config(_make_config())
        assert session.stats["heal_fired"] == 1

    def test_running_state_unchanged_after_update(self):
        session = BotSession(config=_make_config())
        session._running = True
        session.update_config(_make_config())
        assert session._running is True
        session._running = False  # cleanup


# ─────────────────────────────────────────────────────────────────────────────
# BotSession: set_login_fn / _parse_start_pos
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionLoginFn:

    def test_set_login_fn_stores_callback_without_handler(self):
        session = BotSession(config=_make_config())

        def login_fn() -> bool:
            return True

        session.set_login_fn(login_fn)

        assert session._login_fn is login_fn

    def test_set_login_fn_updates_reconnect_handler(self):
        session = BotSession(config=_make_config())
        reconnect_handler = MagicMock()
        session._reconnect_handler = reconnect_handler

        def login_fn() -> bool:
            return True

        session.set_login_fn(login_fn)

        reconnect_handler.set_login_fn.assert_called_once_with(login_fn)
        assert session._login_fn is login_fn


class TestParseStartPos:

    def test_parse_start_pos_none_when_empty(self):
        session = BotSession(config=_make_config(start_pos=""))

        assert session._parse_start_pos() is None

    def test_parse_start_pos_valid_coordinate(self):
        session = BotSession(config=_make_config(start_pos="100, 200, 7"))

        assert session._parse_start_pos() == Coordinate(100, 200, 7)

    def test_parse_start_pos_invalid_logs_and_returns_none(self):
        msgs: List[str] = []
        session = BotSession(
            config=_make_config(start_pos="100,200"),
            log_callback=msgs.append,
        )

        result = session._parse_start_pos()

        assert result is None
        assert any("invalid start_pos" in message.lower() for message in msgs)

    def test_parse_start_pos_non_numeric_logs_and_returns_none(self):
        msgs: List[str] = []
        session = BotSession(
            config=_make_config(start_pos="x,200,7"),
            log_callback=msgs.append,
        )

        result = session._parse_start_pos()

        assert result is None
        assert any("invalid start_pos" in message.lower() for message in msgs)


class TestSessionPositionAndCallbacks:

    def test_is_start_position_locked_false_when_actual_missing(self):
        session = BotSession(config=_make_config(startup_position_tolerance=1))
        expected = Coordinate(100, 200, 7)

        assert session._is_start_position_locked(None, expected) is False

    def test_is_start_position_locked_false_when_expected_missing(self):
        session = BotSession(config=_make_config(startup_position_tolerance=1))
        actual = Coordinate(100, 200, 7)

        assert session._is_start_position_locked(actual, None) is False

    def test_is_start_position_locked_false_when_floor_differs(self):
        session = BotSession(config=_make_config(startup_position_tolerance=1))
        actual = Coordinate(100, 200, 7)
        expected = Coordinate(100, 200, 6)

        assert session._is_start_position_locked(actual, expected) is False

    def test_get_real_position_returns_none_when_update_fails(self):
        session = BotSession(config=_make_config())
        session._update_position = MagicMock(return_value=False)  # type: ignore[method-assign]

        assert session._get_real_position() is None

    def test_get_real_position_returns_current_position_on_success(self):
        session = BotSession(config=_make_config())
        expected = Coordinate(123, 456, 7)
        session._position = expected
        session._update_position = MagicMock(return_value=True)  # type: ignore[method-assign]

        assert session._get_real_position() == expected

    def test_get_real_position_returns_fresh_position_after_update(self):
        session = BotSession(config=_make_config())
        updated = Coordinate(321, 654, 7)

        def _update() -> bool:
            session._position = updated
            return True

        session._update_position = _update  # type: ignore[method-assign]

        assert session._get_real_position() == updated

    def test_stuck_repath_returns_false_without_executor(self):
        session = BotSession(config=_make_config())
        session._executor = None

        assert session._stuck_repath() is False

    def test_stuck_repath_delegates_to_executor(self):
        session = BotSession(config=_make_config())
        executor = MagicMock()
        executor.request_replan.return_value = True
        session._executor = executor

        assert session._stuck_repath() is True
        executor.request_replan.assert_called_once()

    def test_uptime_none_when_not_running_even_with_start_time(self):
        session = BotSession(config=_make_config())
        session._running = False
        session._stats["start_time"] = time.time() - 15.0

        assert session.uptime() is None


class TestSessionNpcHandlerAndHealth:

    def test_make_npc_handler_returns_none_without_trade_manager(self):
        session = BotSession(config=_make_config())
        session._trade = None

        assert session._make_npc_handler() is None

    def test_make_npc_handler_trade_action_runs_cycle(self):
        session = BotSession(config=_make_config())
        trade = MagicMock()
        session._trade = trade

        handler = session._make_npc_handler()

        assert handler is not None
        handler("sell", MagicMock())
        trade.run_cycle.assert_called_once()

    def test_make_npc_handler_info_action_does_not_run_cycle(self):
        msgs: List[str] = []
        session = BotSession(config=_make_config(), log_callback=msgs.append)
        trade = MagicMock()
        session._trade = trade

        handler = session._make_npc_handler()

        assert handler is not None
        handler("check_ammo", MagicMock())
        trade.run_cycle.assert_not_called()
        assert any("informational" in message.lower() for message in msgs)

    def test_check_subsystem_health_restarts_dead_looter_and_clears_stale_flag(self):
        msgs: List[str] = []
        session = BotSession(config=_make_config(), log_callback=msgs.append)
        looter = MagicMock()
        looter._running = True
        looter._thread = MagicMock()
        looter._thread.is_alive.return_value = False
        session._looter = looter
        session._loot_in_progress.set()

        session._check_subsystem_health()

        assert session._loot_in_progress.is_set() is False
        looter.start.assert_called_once()
        assert any("restarting" in message.lower() for message in msgs)


# ─────────────────────────────────────────────────────────────────────────────
# has_healer
# ─────────────────────────────────────────────────────────────────────────────

class TestHasHealer:

    def test_false_before_start(self):
        session = BotSession(config=_make_config())
        assert session.has_healer is False

    def test_true_after_healer_assigned(self):
        session = BotSession(config=_make_config())
        session._healer = MagicMock()
        assert session.has_healer is True

    def test_false_after_healer_cleared(self):
        session = BotSession(config=_make_config())
        session._healer = MagicMock()
        session._healer = None
        assert session.has_healer is False

    def test_returns_bool(self):
        session = BotSession(config=_make_config())
        assert isinstance(session.has_healer, bool)

    def test_consistent_with_internal_field(self):
        session = BotSession(config=_make_config())
        assert session.has_healer == (session._healer is not None)


# ─────────────────────────────────────────────────────────────────────────────
# has_navigator
# ─────────────────────────────────────────────────────────────────────────────

class TestHasNavigator:

    def test_false_before_start(self):
        session = BotSession(config=_make_config())
        assert session.has_navigator is False

    def test_true_after_navigator_assigned(self):
        session = BotSession(config=_make_config())
        session._navigator = MagicMock()
        assert session.has_navigator is True

    def test_returns_bool(self):
        session = BotSession(config=_make_config())
        assert isinstance(session.has_navigator, bool)

    def test_false_when_explicitly_none(self):
        session = BotSession(config=_make_config())
        session._navigator = None
        assert session.has_navigator is False

    def test_consistent_with_internal_field(self):
        session = BotSession(config=_make_config())
        session._navigator = MagicMock()
        assert session.has_navigator == (session._navigator is not None)


# ─────────────────────────────────────────────────────────────────────────────
# has_loader
# ─────────────────────────────────────────────────────────────────────────────

class TestHasLoader:

    def test_false_when_no_loader_provided(self):
        session = BotSession(config=_make_config())
        assert session.has_loader is False

    def test_true_when_loader_injected(self):
        session = BotSession(config=_make_config(), loader=MagicMock())
        assert session.has_loader is True

    def test_false_after_loader_cleared(self):
        session = BotSession(config=_make_config(), loader=MagicMock())
        session._loader = None
        assert session.has_loader is False

    def test_returns_bool(self):
        session = BotSession(config=_make_config())
        assert isinstance(session.has_loader, bool)

    def test_consistent_with_internal_field(self):
        mock_loader = MagicMock()
        session = BotSession(config=_make_config(), loader=mock_loader)
        assert session.has_loader == (session._loader is not None)


# ─────────────────────────────────────────────────────────────────────────────
# routes_completed
# ─────────────────────────────────────────────────────────────────────────────

class TestRoutesCompleted:

    def test_zero_initially(self):
        session = BotSession(config=_make_config())
        assert session.routes_completed == 0

    def test_increments_with_inc_stat(self):
        session = BotSession(config=_make_config())
        session._inc_stat("routes_completed")
        assert session.routes_completed == 1

    def test_returns_int(self):
        session = BotSession(config=_make_config())
        assert isinstance(session.routes_completed, int)

    def test_reset_restores_zero(self):
        session = BotSession(config=_make_config())
        session._inc_stat("routes_completed")
        session._inc_stat("routes_completed")
        session.reset_stats()
        assert session.routes_completed == 0

    def test_consistent_with_stats_dict(self):
        session = BotSession(config=_make_config())
        session._inc_stat("routes_completed")
        assert session.routes_completed == session.stats["routes_completed"]


# ─────────────────────────────────────────────────────────────────────────────
# has_started
# ─────────────────────────────────────────────────────────────────────────────

class TestHasStarted:

    def test_false_until_started(self):
        session = BotSession(config=_make_config())
        assert session.has_started is False

    def test_true_after_start_time_set(self):
        session = BotSession(config=_make_config())
        import time as _time
        session._stats["start_time"] = _time.monotonic()
        assert session.has_started is True

    def test_returns_bool(self):
        session = BotSession(config=_make_config())
        assert isinstance(session.has_started, bool)

    def test_consistent_with_stats_start_time(self):
        session = BotSession(config=_make_config())
        assert session.has_started == (session._stats["start_time"] is not None)

    def test_persists_after_reset_stats(self):
        import time as _time
        session = BotSession(config=_make_config())
        session._stats["start_time"] = _time.monotonic()
        session.reset_stats()
        # reset_stats does NOT clear start_time
        assert session.has_started is True


# ─────────────────────────────────────────────────────────────────────────────
# SessionConfig — new fields
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigNewFields:

    def test_jitter_pct_default_zero(self):
        cfg = SessionConfig()
        assert cfg.jitter_pct == 0.15  # F7.1: default changed from 0.0 to 0.15

    def test_jitter_pct_custom(self):
        cfg = SessionConfig(jitter_pct=0.15)
        assert cfg.jitter_pct == 0.15

    def test_position_source_default_none(self):
        cfg = SessionConfig()
        assert cfg.position_source == "none"

    def test_position_source_custom(self):
        cfg = SessionConfig(position_source="minimap")
        assert cfg.position_source == "minimap"

    def test_watchdog_timeout_default_zero(self):
        cfg = SessionConfig()
        assert cfg.watchdog_timeout == 0.0

    def test_watchdog_timeout_custom(self):
        cfg = SessionConfig(watchdog_timeout=60.0)
        assert cfg.watchdog_timeout == 60.0

    def test_step_delay_min_default(self):
        cfg = SessionConfig()
        assert cfg.step_delay_min == 0.0

    def test_step_delay_max_default(self):
        cfg = SessionConfig()
        assert cfg.step_delay_max == 0.0

    def test_step_delay_custom_range(self):
        cfg = SessionConfig(step_delay_min=0.1, step_delay_max=0.3)
        assert cfg.step_delay_min == 0.1
        assert cfg.step_delay_max == 0.3

    def test_save_load_round_trip_new_fields(self, tmp_path):
        path = tmp_path / "sess.json"
        cfg = SessionConfig(
            jitter_pct=0.2,
            position_source="minimap",
            watchdog_timeout=45.0,
            step_delay_min=0.05,
            step_delay_max=0.15,
        )
        cfg.save(path)
        loaded = SessionConfig.load(path)
        assert loaded.jitter_pct == 0.2
        assert loaded.position_source == "minimap"
        assert loaded.watchdog_timeout == 45.0
        assert loaded.step_delay_min == 0.05
        assert loaded.step_delay_max == 0.15


# ─────────────────────────────────────────────────────────────────────────────
# BotSession.stats_snapshot()
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsSnapshot:

    def test_returns_dict(self):
        session = BotSession(config=_make_config())
        snap = session.stats_snapshot()
        assert isinstance(snap, dict)

    def test_contains_routes_completed(self):
        session = BotSession(config=_make_config())
        snap = session.stats_snapshot()
        assert "routes_completed" in snap

    def test_contains_cycle_count_alias(self):
        session = BotSession(config=_make_config())
        snap = session.stats_snapshot()
        assert "cycle_count" in snap

    def test_cycle_count_matches_routes_completed(self):
        session = BotSession(config=_make_config())
        session._stats["routes_completed"] = 3
        snap = session.stats_snapshot()
        assert snap["cycle_count"] == 3

    def test_contains_is_running(self):
        session = BotSession(config=_make_config())
        snap = session.stats_snapshot()
        assert "is_running" in snap
        assert snap["is_running"] is False

    def test_contains_uptime_secs(self):
        session = BotSession(config=_make_config())
        snap = session.stats_snapshot()
        assert "uptime_secs" in snap

    def test_uptime_secs_none_when_not_started(self):
        session = BotSession(config=_make_config())
        snap = session.stats_snapshot()
        assert snap["uptime_secs"] is None

    def test_is_independent_copy(self):
        session = BotSession(config=_make_config())
        snap = session.stats_snapshot()
        snap["routes_completed"] = 999
        assert session._stats["routes_completed"] == 0

    def test_waypoints_visited_key_present(self):
        session = BotSession(config=_make_config())
        snap = session.stats_snapshot()
        assert "waypoints_visited" in snap


class TestMonitorSnapshot:

    def test_monitor_snapshot_contains_ui_contract(self):
        session = BotSession(config=_make_config(route_file="routes/hunt.json"))
        session._stats["start_time"] = time.time() - 45
        session._running = True
        current_coord = Coordinate(100, 200, 7)
        session._executor = types.SimpleNamespace(
            _current_pos=current_coord,
            _current_instr=types.SimpleNamespace(kind="node", coord=current_coord),
        )

        snap = session.monitor_snapshot()

        assert snap["route"] == "hunt.json"
        assert snap["route_name"] == "hunt.json"
        assert snap["position"] == {"x": 100, "y": 200, "z": 7}
        assert snap["current_wpt"] == "[node  100,200,7]"
        assert snap["uptime_seconds"] is not None

    def test_current_position_can_seed_from_route(self, tmp_path: Path):
        route_path = tmp_path / "seed_route.json"
        route_path.write_text(json.dumps({
            "_meta": {"start_coord": {"x": 32369, "y": 32241, "z": 7}},
        }))
        session = BotSession(config=_make_config(route_file=str(route_path)))

        pos = session.current_position(allow_route_seed=True)

        assert pos == Coordinate(32369, 32241, 7)
        assert session._position == Coordinate(32369, 32241, 7)

    def test_current_position_prefers_executor_and_updates_cache(self):
        session = BotSession(config=_make_config())
        live_pos = Coordinate(32100, 32200, 7)
        session._executor = types.SimpleNamespace(_current_pos=live_pos)

        pos = session.current_position()

        assert pos == live_pos
        assert session._position == live_pos


class TestMonitorControlHelpers:

    def test_set_targeting_enabled_pauses_combat(self):
        session = BotSession(config=_make_config())
        session._combat = MagicMock()

        session.set_targeting_enabled(False)

        session._combat.pause.assert_called_once()

    def test_set_walking_enabled_updates_executor_flag(self):
        session = BotSession(config=_make_config())
        session._executor = types.SimpleNamespace(_walking_paused=False)

        session.set_walking_enabled(False)

        assert session._executor._walking_paused is True

    def test_set_looting_enabled_pauses_looter(self):
        session = BotSession(config=_make_config())
        session._looter = MagicMock()

        session.set_looting_enabled(False)

        session._looter.pause.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# BotSession watchdog attributes
# ─────────────────────────────────────────────────────────────────────────────

class TestBotSessionWatchdogAttributes:

    def test_watchdog_thread_initially_none(self):
        session = BotSession(config=_make_config())
        assert session._watchdog_thread is None

    def test_last_move_time_initially_zero(self):
        session = BotSession(config=_make_config())
        assert session._last_move_time == 0.0

    def test_position_initially_none(self):
        session = BotSession(config=_make_config())
        assert session._position is None

    def test_radar_initially_none(self):
        session = BotSession(config=_make_config())
        assert session._radar is None


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests — wiring bugs fixed in session.py / trade integration
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionWiring:
    """Regression suite for the 5 wiring bugs found in session.py during audit.

    Bug 1: AutoHealer.set_log_callback() was never called after healer creation.
    Bug 2: ConditionMonitor.set_log_callback() was never called after CM creation.
    Bug 3: set_frame_getter() did not propagate to self._healer.
    Bug 4: ScriptExecutor received npc_handler=None even when TradeManager was active.
    Bug 5: has_trade_manager returned False even when auto_refill=True.
    """

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _start_session(extra_cfg: dict | None = None, extra_patches: dict | None = None):
        """Context manager: patches all externals, calls start(), yields session + mocks."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            cfg_kw = dict(start_delay=0.0)
            if extra_cfg:
                cfg_kw.update(extra_cfg)
            cfg = _make_config(**cfg_kw)
            session = BotSession(config=cfg, log_callback=lambda _: None)

            mock_healer = _mock_healer()
            mock_cm     = MagicMock()
            mock_trade  = MagicMock()

            patches = {
                "src.session.InputController":    MagicMock(return_value=_mock_ctrl()),
                "src.session.TibiaMapLoader":     MagicMock(),
                "src.session.WaypointNavigator":  MagicMock(return_value=_mock_navigator()),
                "src.session.AutoHealer":         MagicMock(return_value=mock_healer),
                "src.session.ConditionMonitor":   MagicMock(return_value=mock_cm),
                "src.session.TradeManager":       MagicMock(return_value=mock_trade),
            }
            if extra_patches:
                patches.update(extra_patches)

            with patch("src.session.InputController",   patches["src.session.InputController"]), \
                 patch("src.session.TibiaMapLoader",    patches["src.session.TibiaMapLoader"]), \
                 patch("src.session.WaypointNavigator", patches["src.session.WaypointNavigator"]), \
                 patch("src.session.AutoHealer",        patches["src.session.AutoHealer"]), \
                 patch("src.session.ConditionMonitor",  patches["src.session.ConditionMonitor"]), \
                 patch("src.session.TradeManager",      patches["src.session.TradeManager"]):
                session.start()
                yield session, mock_healer, mock_cm, mock_trade
                session.stop()

        return _ctx()

    # ── Bug 1: healer log callback wired ─────────────────────────────────────

    def test_bug1_healer_set_log_callback_called_on_start(self):
        """start() must call healer.set_log_callback() so healer logs reach session log."""
        with self._start_session() as (session, mock_healer, mock_cm, _):
            mock_healer.set_log_callback.assert_called_once()
            # Argument must be a callable
            cb = mock_healer.set_log_callback.call_args[0][0]
            assert callable(cb), "set_log_callback must receive a callable"

    # ── Bug 2: ConditionMonitor log callback wired ────────────────────────────

    def test_bug2_condition_monitor_set_log_callback_called_on_start(self):
        """start() must call condition_monitor.set_log_callback() so CM logs reach session log.
        ConditionMonitor is only created when monitor_conditions=True."""
        with self._start_session(extra_cfg={"monitor_conditions": True}) as (session, _, mock_cm, __):
            mock_cm.set_log_callback.assert_called_once()
            cb = mock_cm.set_log_callback.call_args[0][0]
            assert callable(cb)

    # ── Bug 3: set_frame_getter propagates to healer ──────────────────────────

    def test_bug3_set_frame_getter_propagates_to_healer(self):
        """set_frame_getter() must propagate to the healer component."""
        with self._start_session() as (session, mock_healer, _, __):
            dummy_getter = lambda: None  # noqa: E731
            session.set_frame_getter(dummy_getter)
            # Since T7 FrameCache, propagated getter is the cached wrapper
            mock_healer.set_frame_getter.assert_called_once()
            cached_fn = mock_healer.set_frame_getter.call_args[0][0]
            assert callable(cached_fn)

    # ── Bug 4: npc_handler wired in ScriptExecutor ───────────────────────────

    def test_bug4_make_npc_handler_returns_none_without_trade(self):
        """_make_npc_handler() returns None when no TradeManager is attached."""
        session = BotSession(config=_make_config())
        assert session._make_npc_handler() is None

    def test_bug4_make_npc_handler_returns_callable_with_trade(self):
        """_make_npc_handler() returns a callable (not None) when _trade is set."""
        session = BotSession(config=_make_config())
        session._trade = MagicMock()  # inject mock trade
        handler = session._make_npc_handler()
        assert callable(handler), "npc_handler must be a callable when _trade is set"

    def test_bug4_npc_handler_calls_run_cycle_for_sell(self):
        """npc_handler must forward sell/buy_potions/buy_ammo to trade.run_cycle()."""
        session = BotSession(config=_make_config())
        mock_trade = MagicMock()
        session._trade = mock_trade
        handler = session._make_npc_handler()
        assert handler is not None
        handler("sell", None)
        mock_trade.run_cycle.assert_called_once()

    def test_bug4_npc_handler_calls_run_cycle_for_buy_potions(self):
        session = BotSession(config=_make_config())
        mock_trade = MagicMock()
        session._trade = mock_trade
        handler = session._make_npc_handler()
        assert handler is not None
        handler("buy_potions", None)
        mock_trade.run_cycle.assert_called_once()

    def test_bug4_npc_handler_does_not_call_run_cycle_for_check_supplies(self):
        """check_supplies is informational — must NOT trigger run_cycle()."""
        session = BotSession(config=_make_config())
        mock_trade = MagicMock()
        session._trade = mock_trade
        handler = session._make_npc_handler()
        assert handler is not None
        logs: list = []
        session._log_cb = logs.append
        handler("check_supplies", None)
        mock_trade.run_cycle.assert_not_called()

    # ── Bug 5: has_trade_manager / auto_refill integration ───────────────────

    def test_bug5_has_trade_manager_false_by_default(self):
        """has_trade_manager is False when auto_refill is not set."""
        session = BotSession(config=_make_config())
        assert session.has_trade_manager is False

    def test_bug5_has_trade_manager_true_after_start_with_auto_refill(self):
        """has_trade_manager is True after start() when auto_refill=True."""
        with self._start_session(extra_cfg={"auto_refill": True}) as (session, *_):
            assert session.has_trade_manager is True

    def test_bug5_trade_attribute_none_without_auto_refill(self):
        """_trade stays None when auto_refill=False (default)."""
        with self._start_session() as (session, *_):
            assert session._trade is None


# ─────────────────────────────────────────────────────────────────────────────
# T6 — Watchdog subsystem health check
# ─────────────────────────────────────────────────────────────────────────────

class TestSubsystemHealthCheck:
    """Verify _check_subsystem_health restarts dead threads."""

    def _make_session(self) -> BotSession:
        return BotSession(config=_make_config())

    def test_no_subsystems_no_crash(self):
        session = self._make_session()
        # All subsystems are None — should not raise
        session._check_subsystem_health()

    def test_alive_subsystem_not_restarted(self):
        session = self._make_session()
        sub = MagicMock()
        sub._running = True
        sub._thread = MagicMock()
        sub._thread.is_alive.return_value = True  # alive
        session._healer = sub

        session._check_subsystem_health()
        sub.start.assert_not_called()

    def test_dead_subsystem_restarted(self):
        session = self._make_session()
        sub = MagicMock()
        sub._running = True
        sub._thread = MagicMock()
        sub._thread.is_alive.return_value = False  # dead
        session._healer = sub

        session._check_subsystem_health()
        sub.start.assert_called_once()

    def test_stopped_subsystem_not_restarted(self):
        """If a subsystem was intentionally stopped (_running=False), don't restart."""
        session = self._make_session()
        sub = MagicMock()
        sub._running = False
        sub._thread = MagicMock()
        sub._thread.is_alive.return_value = False
        session._combat = sub

        session._check_subsystem_health()
        sub.start.assert_not_called()

    def test_multiple_dead_subsystems(self):
        session = self._make_session()
        dead_healer = MagicMock()
        dead_healer._running = True
        dead_healer._thread = MagicMock()
        dead_healer._thread.is_alive.return_value = False
        session._healer = dead_healer

        dead_combat = MagicMock()
        dead_combat._running = True
        dead_combat._thread = MagicMock()
        dead_combat._thread.is_alive.return_value = False
        session._combat = dead_combat

        session._check_subsystem_health()
        dead_healer.start.assert_called_once()
        dead_combat.start.assert_called_once()

    def test_restart_exception_logged_not_raised(self):
        session = self._make_session()
        logs: list = []
        session._log_cb = lambda msg: logs.append(msg)

        sub = MagicMock()
        sub._running = True
        sub._thread = MagicMock()
        sub._thread.is_alive.return_value = False
        sub.start.side_effect = RuntimeError("restart boom")
        session._anti_kick = sub

        # Should not raise
        session._check_subsystem_health()
        assert any("restart failed" in msg for msg in logs)
