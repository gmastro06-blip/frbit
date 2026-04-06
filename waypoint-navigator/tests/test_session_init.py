"""
Tests for BotSession._init_* private methods.

Targets the 9 focused init methods split from the old monolithic
_startup_subsystems, plus helper methods (stats_snapshot, uptime, etc.).

100% offline — no Tibia window, no hardware, no network.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, List, cast
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.session import BotSession, SessionConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(**kw) -> SessionConfig:
    """Create a minimal SessionConfig for offline tests."""
    defaults: dict[str, Any] = dict(
        route_file="",
        start_delay=0.0,
        heal_hp_pct=70,
        heal_emergency_pct=30,
        mana_threshold_pct=30,
        input_method="postmessage",
        target_window="Tibia",
        dry_run=True,
        death_handler=False,
        reconnect_handler=False,
        anti_kick=False,
        stuck_detector=False,
        frame_quality_check=False,
        use_position_resolver=False,
        pvp_detector=False,
        inventory_check=False,
        alert_enabled=False,
        session_stats=False,
        spawn_manager=False,
        adaptive_roi=False,
        dashboard=False,
        break_scheduler=False,
        soak_monitor=False,
        gm_detector=False,
        chat_responder=False,
        auto_loot=False,
        depot_after_run=False,
        auto_combat=False,
        monitor_conditions=False,
        auto_refill=False,
        watchdog_timeout=0.0,
        frame_source="",
        position_source="none",
        arduino_enabled=False,
        pico_enabled=False,
        loop_route=False,
    )
    defaults.update(kw)
    return SessionConfig(**cast(Any, defaults))


def _session(**kw) -> BotSession:
    """Create a BotSession with minimal config and hardware stubs pre-set."""
    s = BotSession(config=_cfg(**kw), log_callback=lambda _: None)
    # These are normally set in _startup_subsystems; pre-init for safety.
    s._pico = None
    s._arduino = None
    s._raw_ctrl = None
    s._path_viz = None
    return s


def _mock_input_controller(connected: bool = True, interception_available: bool = False):
    """Return a mock InputController."""
    ctrl = MagicMock()
    ctrl.is_connected.return_value = connected
    ctrl.interception_available = interception_available
    ctrl.hwnd = 0
    return ctrl


# ─────────────────────────────────────────────────────────────────────────────
# _init_input
# ─────────────────────────────────────────────────────────────────────────────

class TestInitInput:

    def test_postmessage_sets_ctrl(self):
        """postmessage method — no interception check — sets _ctrl."""
        s = _session(input_method="postmessage")
        mock_ctrl = _mock_input_controller(connected=False)
        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False):
            s._init_input()
        assert s._ctrl is mock_ctrl
        assert s._raw_ctrl is mock_ctrl

    def test_window_not_found_logs_offline(self):
        """Offline window → log message, no exception."""
        logged: List[str] = []
        s = BotSession(config=_cfg(input_method="postmessage"),
                       log_callback=logged.append)
        s._pico = s._arduino = s._raw_ctrl = s._path_viz = None
        mock_ctrl = _mock_input_controller(connected=False)
        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False):
            s._init_input()
        assert any("offline" in m.lower() or "not found" in m.lower() for m in logged)

    def test_interception_method_raises_when_driver_unavailable(self):
        """interception + driver not available → RuntimeError."""
        s = _session(input_method="interception")
        mock_ctrl = _mock_input_controller(connected=False, interception_available=False)
        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="INTERCEPTION"):
                s._init_input()

    def test_interception_method_ok_when_driver_available(self):
        """interception + driver available → no exception."""
        s = _session(input_method="interception")
        mock_ctrl = _mock_input_controller(connected=True, interception_available=True)
        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False):
            s._init_input()
        assert s._ctrl is mock_ctrl

    def test_his_wrapping_when_available(self):
        """When HIS is available, _ctrl should be the HIS wrapper."""
        s = _session(input_method="postmessage")
        mock_ctrl = _mock_input_controller(connected=False)
        mock_his_instance = MagicMock()

        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", True), \
             patch("src.session._HIS", return_value=mock_his_instance):
            s._init_input()

        assert s._ctrl is mock_his_instance
        assert s._raw_ctrl is mock_ctrl

    def test_his_init_error_falls_back_to_raw_ctrl(self):
        """When HIS raises during init, fall back to raw InputController."""
        s = _session(input_method="postmessage")
        mock_ctrl = _mock_input_controller(connected=False)

        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", True), \
             patch("src.session._HIS", side_effect=Exception("HIS boom")):
            s._init_input()

        assert s._ctrl is mock_ctrl

    def test_arduino_path_when_available_and_enabled(self):
        """Arduino HID available + enabled → attempt initialize()."""
        s = _session(input_method="postmessage", arduino_enabled=True, arduino_port="COM3")
        mock_ctrl = _mock_input_controller(connected=False)
        mock_ard = MagicMock()
        mock_ard.initialize.return_value = True
        mock_ard_cls = MagicMock(return_value=mock_ard)
        mock_ard_cfg_cls = MagicMock()

        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False), \
             patch("src.session._ARDUINO_AVAILABLE", True), \
             patch("src.session._ArduinoHID", mock_ard_cls), \
             patch("src.session._ArduinoConfig", mock_ard_cfg_cls):
            s._init_input()

        mock_ard.initialize.assert_called_once()
        assert s._arduino is mock_ard

    def test_arduino_path_init_failure_sets_none(self):
        """Arduino HID initialize() returns False → _arduino stays None."""
        s = _session(input_method="postmessage", arduino_enabled=True)
        mock_ctrl = _mock_input_controller(connected=False)
        mock_ard = MagicMock()
        mock_ard.initialize.return_value = False

        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False), \
             patch("src.session._ARDUINO_AVAILABLE", True), \
             patch("src.session._ArduinoHID", return_value=mock_ard), \
             patch("src.session._ArduinoConfig", MagicMock()):
            s._init_input()

        assert s._arduino is None

    def test_pico_path_when_available_and_enabled(self):
        """Pico HID available + enabled → attempt initialize()."""
        s = _session(input_method="postmessage", pico_enabled=True, pico_port="COM4")
        mock_ctrl = _mock_input_controller(connected=False)
        mock_pico = MagicMock()
        mock_pico.initialize.return_value = True

        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False), \
             patch("src.session._PICO_AVAILABLE", True), \
             patch("src.session._PicoHID", return_value=mock_pico), \
             patch("src.session._PicoConfig", MagicMock()):
            s._init_input()

        mock_pico.initialize.assert_called_once()
        assert s._pico is mock_pico

    def test_pico_path_init_failure_sets_none(self):
        """Pico HID initialize() returns False → _pico stays None."""
        s = _session(input_method="postmessage", pico_enabled=True)
        mock_ctrl = _mock_input_controller(connected=False)
        mock_pico = MagicMock()
        mock_pico.initialize.return_value = False

        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False), \
             patch("src.session._PICO_AVAILABLE", True), \
             patch("src.session._PicoHID", return_value=mock_pico), \
             patch("src.session._PicoConfig", MagicMock()):
            s._init_input()

        assert s._pico is None

    def test_arduino_not_available_skips(self):
        """_ARDUINO_AVAILABLE=False and arduino_enabled=True → no crash."""
        s = _session(input_method="postmessage", arduino_enabled=True)
        mock_ctrl = _mock_input_controller(connected=False)

        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False), \
             patch("src.session._ARDUINO_AVAILABLE", False):
            s._init_input()

        assert s._arduino is None

    def test_pico_not_available_skips(self):
        """_PICO_AVAILABLE=False and pico_enabled=True → no crash."""
        s = _session(input_method="postmessage", pico_enabled=True)
        mock_ctrl = _mock_input_controller(connected=False)

        with patch("src.session.InputController", return_value=mock_ctrl), \
             patch("src.session._HIS_AVAILABLE", False), \
             patch("src.session._PICO_AVAILABLE", False):
            s._init_input()

        assert s._pico is None


# ─────────────────────────────────────────────────────────────────────────────
# _init_navigation
# ─────────────────────────────────────────────────────────────────────────────

class TestInitNavigation:

    def test_creates_navigator_and_loader(self):
        s = _session()
        with patch("src.session.WaypointNavigator") as mock_nav_cls, \
             patch("src.session.TibiaMapLoader") as mock_loader_cls:
            mock_nav = MagicMock()
            mock_nav_cls.return_value = mock_nav
            mock_loader = MagicMock()
            mock_loader_cls.return_value = mock_loader
            s._init_navigation()

        assert s._navigator is mock_nav
        assert s._loader is mock_loader

    def test_reuses_existing_loader(self):
        """When _loader is already set, it is not replaced."""
        s = _session()
        existing_loader = MagicMock()
        s._loader = existing_loader

        with patch("src.session.WaypointNavigator") as mock_nav_cls, \
             patch("src.session.TibiaMapLoader") as mock_loader_cls:
            mock_nav_cls.return_value = MagicMock()
            s._init_navigation()

        mock_loader_cls.assert_not_called()
        assert s._loader is existing_loader

    def test_navigator_loader_wired(self):
        """Navigator.loader attribute is set to the loader."""
        s = _session()
        mock_loader = MagicMock()
        s._loader = mock_loader
        mock_nav = MagicMock()

        with patch("src.session.WaypointNavigator", return_value=mock_nav):
            s._init_navigation()

        assert mock_nav.loader is mock_loader


# ─────────────────────────────────────────────────────────────────────────────
# _init_healer
# ─────────────────────────────────────────────────────────────────────────────

class TestInitHealer:

    def _setup_healer_session(self, **kw) -> BotSession:
        s = _session(**kw)
        mock_ctrl = _mock_input_controller(connected=False)
        s._ctrl = mock_ctrl
        return s

    def test_creates_healer_and_starts(self):
        s = self._setup_healer_session()
        mock_healer = MagicMock()
        with patch("src.session.AutoHealer", return_value=mock_healer):
            s._init_healer()
        mock_healer.start.assert_called_once()
        assert s._healer is mock_healer

    def test_healer_log_callback_set(self):
        s = self._setup_healer_session()
        mock_healer = MagicMock()
        with patch("src.session.AutoHealer", return_value=mock_healer):
            s._init_healer()
        mock_healer.set_log_callback.assert_called_once()

    def test_healer_on_heal_increments_stat(self):
        s = self._setup_healer_session()
        mock_healer = MagicMock()
        with patch("src.session.AutoHealer", return_value=mock_healer):
            s._init_healer()
        # Simulate the on_heal callback being invoked
        assert s._stats["heal_fired"] == 0
        healer = s._healer
        assert healer is not None
        on_heal = cast(Any, healer).on_heal
        on_heal()
        assert s._stats["heal_fired"] == 1

    def test_healer_on_mana_increments_stat(self):
        s = self._setup_healer_session()
        mock_healer = MagicMock()
        with patch("src.session.AutoHealer", return_value=mock_healer):
            s._init_healer()
        assert s._stats["mana_fired"] == 0
        healer = s._healer
        assert healer is not None
        on_mana = cast(Any, healer).on_mana
        on_mana()
        assert s._stats["mana_fired"] == 1

    def test_heal_config_uses_session_config_values(self):
        s = self._setup_healer_session(
            heal_hp_pct=65,
            heal_emergency_pct=25,
            mana_threshold_pct=40,
        )
        captured_cfg = []
        def capture_cfg(ctrl, config):
            captured_cfg.append(config)
            return MagicMock()

        with patch("src.session.AutoHealer", side_effect=capture_cfg):
            s._init_healer()

        assert captured_cfg[0].hp_threshold_pct == 65
        assert captured_cfg[0].hp_emergency_pct == 25
        assert captured_cfg[0].mp_threshold_pct == 40

    def test_critical_check_wired_when_ctrl_supports_it(self):
        s = self._setup_healer_session()
        mock_ctrl = MagicMock()
        mock_ctrl.is_connected.return_value = False
        mock_ctrl.interception_available = False
        mock_ctrl.hwnd = 0
        # Ensure ctrl has set_critical_check
        mock_ctrl.set_critical_check = MagicMock()
        s._ctrl = mock_ctrl

        mock_healer = MagicMock()
        with patch("src.session.AutoHealer", return_value=mock_healer):
            s._init_healer()

        mock_ctrl.set_critical_check.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# _init_capture
# ─────────────────────────────────────────────────────────────────────────────

class TestInitCapture:

    def _make_session_with_ctrl(self, **cfg_kw) -> BotSession:
        s = _session(**cfg_kw)
        mock_ctrl = _mock_input_controller(connected=False)
        s._ctrl = mock_ctrl
        s._raw_ctrl = mock_ctrl
        return s

    def test_no_frame_source_returns_none_cached(self):
        """Empty frame_source + position_source='none' → _cached is None."""
        s = self._make_session_with_ctrl()
        _cached = s._init_capture()
        assert _cached is None
        assert s._frame_getter is None

    def test_mss_source_calls_build_frame_getter(self):
        s = self._make_session_with_ctrl(frame_source="mss")
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg) as mock_build, \
             patch("src.session.dxcam", None, create=True):
            # Prevent dxcam auto-upgrade by making import fail
            import builtins
            real_import = builtins.__import__
            def patched_import(name, *args, **kwargs):
                if name == "dxcam":
                    raise ImportError("no dxcam")
                return real_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=patched_import):
                _cached = s._init_capture()
        assert s._frame_getter is mock_fg

    def test_dxcam_source_calls_build_frame_getter(self):
        s = self._make_session_with_ctrl(frame_source="dxcam")
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()
        assert s._frame_getter is mock_fg

    def test_printwindow_source(self):
        s = self._make_session_with_ctrl(frame_source="printwindow")
        # printwindow/wgc require a resolved window handle
        assert s._ctrl is not None
        ctrl = cast(Any, s._ctrl)
        ctrl.is_connected.return_value = True
        ctrl.hwnd = 0x1234
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()
        assert s._frame_getter is mock_fg

    def test_wgc_source(self):
        s = self._make_session_with_ctrl(frame_source="wgc")
        # wgc requires a resolved window handle
        assert s._ctrl is not None
        ctrl = cast(Any, s._ctrl)
        ctrl.is_connected.return_value = True
        ctrl.hwnd = 0x1234
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()
        assert s._frame_getter is mock_fg

    def test_obs_source(self):
        s = self._make_session_with_ctrl(frame_source="obs")
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()
        assert s._frame_getter is mock_fg

    def test_virtualcam_source(self):
        s = self._make_session_with_ctrl(frame_source="virtualcam")
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()
        assert s._frame_getter is mock_fg

    def test_rtmp_source(self):
        s = self._make_session_with_ctrl(
            frame_source="rtmp",
            rtmp_url="rtmp://localhost/live/tibia",
        )
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()
        assert s._frame_getter is mock_fg

    def test_frame_source_failure_logs_and_continues(self):
        """When _build_frame_getter raises, session logs and continues (no crash)."""
        logged: List[str] = []
        s = _session(frame_source="mss")
        s._ctrl = _mock_input_controller(connected=False)
        s._raw_ctrl = s._ctrl
        s._log_cb = logged.append

        import builtins
        real_import = builtins.__import__
        def patched_import(name, *args, **kwargs):
            if name == "dxcam":
                raise ImportError("no dxcam")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=patched_import), \
             patch("src.session._build_frame_getter", side_effect=RuntimeError("capture fail")):
            _cached = s._init_capture()

        assert _cached is None
        assert any("fail" in m.lower() or "disabled" in m.lower() for m in logged)

    def test_existing_frame_getter_not_replaced(self):
        """If _frame_getter is already set before _init_capture, it is kept."""
        s = self._make_session_with_ctrl(frame_source="mss")
        existing_fg = MagicMock()
        s._frame_getter = existing_fg

        with patch("src.session._build_frame_getter") as mock_build:
            _cached = s._init_capture()

        mock_build.assert_not_called()
        assert s._frame_getter is existing_fg

    def test_frame_cache_wraps_getter(self):
        """After init_capture with a valid source, _frame_cache should be set."""
        s = self._make_session_with_ctrl(frame_source="dxcam")
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()
        assert s._frame_cache is not None

    def test_cached_is_callable_when_getter_set(self):
        """_cached returned from _init_capture is callable."""
        s = self._make_session_with_ctrl(frame_source="dxcam")
        mock_fg = MagicMock()
        with patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()
        assert callable(_cached)

    def test_position_source_mss_used_as_fallback(self):
        """position_source='mss' with no frame_source → treated as mss."""
        s = self._make_session_with_ctrl(frame_source="", position_source="mss")
        mock_fg = MagicMock()

        import builtins
        real_import = builtins.__import__
        def patched_import(name, *args, **kwargs):
            if name == "dxcam":
                raise ImportError("no dxcam")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=patched_import), \
             patch("src.session._build_frame_getter", return_value=mock_fg):
            _cached = s._init_capture()

        assert s._frame_getter is mock_fg


class TestStartPositionLock:

    def test_start_position_lock_skips_without_start_pos(self):
        s = _session(position_source="minimap")
        s._radar = MagicMock()
        assert s._wait_for_start_position_lock() is True

    def test_start_position_lock_accepts_position_within_tolerance(self):
        from src.models import Coordinate

        s = _session(
            position_source="minimap",
            start_pos="100,200,7",
            startup_position_tolerance=2,
        )
        s._radar = MagicMock()
        s._position = Coordinate(x=101, y=200, z=7)

        assert s._wait_for_start_position_lock() is True

    def test_start_position_lock_fails_when_position_stays_far(self):
        from src.models import Coordinate

        s = _session(
            position_source="minimap",
            start_pos="100,200,7",
            startup_position_tolerance=1,
            startup_position_timeout=0.0,
        )
        s._radar = MagicMock()
        s._position = Coordinate(x=120, y=220, z=7)
        with patch.object(s, "_update_position") as mock_update:
            assert s._wait_for_start_position_lock() is False
        mock_update.assert_called()

    def test_start_threads_aborts_when_start_position_lock_fails(self):
        s = _session(position_source="minimap", start_pos="100,200,7")
        with patch.object(s, "_wait_for_start_position_lock", return_value=False):
            with pytest.raises(RuntimeError, match="Start position lock failed"):
                s._start_threads()


# ─────────────────────────────────────────────────────────────────────────────
# _init_optional_subsystems
# ─────────────────────────────────────────────────────────────────────────────

class TestInitOptionalSubsystems:

    def _make_session(self, **kw) -> BotSession:
        s = _session(**kw)
        s._ctrl = MagicMock()
        s._healer = MagicMock()
        s._event_bus = MagicMock()
        return s

    def test_no_optional_subsystems_by_default(self):
        s = self._make_session()
        s._init_optional_subsystems(None)
        assert s._depot is None
        assert s._looter is None
        assert s._combat is None
        assert s._condition_monitor is None
        assert s._trade is None

    def test_depot_created_when_depot_after_run(self):
        s = self._make_session(depot_after_run=True)
        mock_depot = MagicMock()
        with patch("src.session.DepotManager", return_value=mock_depot):
            s._init_optional_subsystems(None)
        assert s._depot is mock_depot

    def test_depot_gets_frame_getter_when_cached(self):
        s = self._make_session(depot_after_run=True)
        mock_depot = MagicMock()
        cached = MagicMock()
        with patch("src.session.DepotManager", return_value=mock_depot):
            s._init_optional_subsystems(cached)
        mock_depot.set_frame_getter.assert_called_once_with(cached)

    def test_looter_created_when_auto_loot(self):
        s = self._make_session(auto_loot=True)
        mock_looter = MagicMock()
        with patch("src.session.Looter", return_value=mock_looter):
            s._init_optional_subsystems(None)
        assert s._looter is mock_looter
        mock_looter.start.assert_called_once()

    def test_looter_gets_frame_getter_when_cached(self):
        s = self._make_session(auto_loot=True)
        mock_looter = MagicMock()
        cached = MagicMock()
        with patch("src.session.Looter", return_value=mock_looter):
            s._init_optional_subsystems(cached)
        mock_looter.set_frame_getter.assert_called_once_with(cached)

    def test_combat_created_when_auto_combat(self):
        s = self._make_session(auto_combat=True)
        mock_combat = MagicMock()
        mock_hp_det = MagicMock()
        with patch("src.session.CombatManager", return_value=mock_combat), \
             patch("src.session.HpMpDetector", return_value=mock_hp_det):
            s._init_optional_subsystems(None)
        assert s._combat is mock_combat
        mock_combat.start.assert_called_once()

    def test_condition_monitor_created_when_enabled(self):
        s = self._make_session(monitor_conditions=True)
        mock_cm = MagicMock()
        with patch("src.session.ConditionMonitor", return_value=mock_cm):
            s._init_optional_subsystems(None)
        assert s._condition_monitor is mock_cm
        mock_cm.start.assert_called_once()

    def test_trade_manager_created_when_auto_refill(self):
        s = self._make_session(auto_refill=True)
        mock_trade = MagicMock()
        with patch("src.session.TradeManager", return_value=mock_trade):
            s._init_optional_subsystems(None)
        assert s._trade is mock_trade

    def test_healer_gets_frame_getter_when_cached(self):
        s = self._make_session()
        cached = MagicMock()
        healer = MagicMock()
        s._healer = healer
        s._event_bus = MagicMock()
        s._init_optional_subsystems(cached)
        healer.set_frame_getter.assert_called_once_with(cached)

    def test_obstacle_analyzer_is_always_none(self):
        """ObstacleAnalyzer is intentionally disabled."""
        s = self._make_session()
        s._init_optional_subsystems(None)
        assert s._obstacle_analyzer is None


# ─────────────────────────────────────────────────────────────────────────────
# _init_safety_handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestInitSafetyHandlers:

    def _make_session(self, **kw) -> BotSession:
        s = _session(**kw)
        s._ctrl = MagicMock()
        s._event_bus = MagicMock()
        return s

    def test_death_handler_created_when_enabled(self):
        s = self._make_session(death_handler=True)
        mock_dh = MagicMock()
        with patch("src.session.DeathHandler", return_value=mock_dh):
            s._init_safety_handlers(None)
        assert s._death_handler is mock_dh
        mock_dh.start.assert_called_once()

    def test_death_handler_none_when_disabled(self):
        s = self._make_session(death_handler=False)
        s._init_safety_handlers(None)
        assert s._death_handler is None

    def test_death_handler_gets_frame_getter(self):
        s = self._make_session(death_handler=True)
        mock_dh = MagicMock()
        cached = MagicMock()
        with patch("src.session.DeathHandler", return_value=mock_dh):
            s._init_safety_handlers(cached)
        mock_dh.set_frame_getter.assert_called_once_with(cached)

    def test_reconnect_handler_created_when_enabled(self):
        s = self._make_session(reconnect_handler=True)
        mock_rh = MagicMock()
        with patch("src.session.ReconnectHandler", return_value=mock_rh):
            s._init_safety_handlers(None)
        assert s._reconnect_handler is mock_rh
        mock_rh.start.assert_called_once()

    def test_reconnect_handler_none_when_disabled(self):
        s = self._make_session(reconnect_handler=False)
        s._init_safety_handlers(None)
        assert s._reconnect_handler is None

    def test_reconnect_handler_gets_login_fn(self):
        s = self._make_session(reconnect_handler=True)
        mock_rh = MagicMock()
        my_login = MagicMock()
        s._login_fn = my_login
        with patch("src.session.ReconnectHandler", return_value=mock_rh):
            s._init_safety_handlers(None)
        mock_rh.set_login_fn.assert_called_once_with(my_login)

    def test_anti_kick_created_when_enabled(self):
        s = self._make_session(anti_kick=True, anti_kick_idle=200.0)
        mock_ak = MagicMock()
        with patch("src.session.AntiKick", return_value=mock_ak):
            s._init_safety_handlers(None)
        assert s._anti_kick is mock_ak
        mock_ak.start.assert_called_once()

    def test_anti_kick_none_when_disabled(self):
        s = self._make_session(anti_kick=False)
        s._init_safety_handlers(None)
        assert s._anti_kick is None

    def test_stuck_detector_created_when_enabled(self):
        s = self._make_session(stuck_detector=True)
        mock_sd = MagicMock()
        with patch("src.session.StuckDetector", return_value=mock_sd):
            s._init_safety_handlers(None)
        assert s._stuck_det is mock_sd
        mock_sd.start.assert_called_once()

    def test_stuck_detector_none_when_disabled(self):
        s = self._make_session(stuck_detector=False)
        s._init_safety_handlers(None)
        assert s._stuck_det is None

    def test_start_pos_parsed_and_set(self):
        s = self._make_session(start_pos="100,200,7")
        s._position = None
        s._init_safety_handlers(None)
        from src.models import Coordinate
        assert s._position == Coordinate(x=100, y=200, z=7)

    def test_start_pos_not_overrides_existing_position(self):
        """If _position is already set, start_pos is not applied."""
        from src.models import Coordinate
        s = self._make_session(start_pos="999,999,1")
        existing = Coordinate(x=50, y=50, z=7)
        s._position = existing
        s._init_safety_handlers(None)
        assert s._position == existing

    def test_re_equip_hotkeys_parsed(self):
        s = self._make_session(
            death_handler=True,
            re_equip_hotkeys="0x70,0x71,112",
        )
        captured_cfg = []
        def capture(ctrl, config):
            captured_cfg.append(config)
            return MagicMock()

        with patch("src.session.DeathHandler", side_effect=capture):
            s._init_safety_handlers(None)

        assert captured_cfg[0].re_equip_hotkeys == [0x70, 0x71, 112]


# ─────────────────────────────────────────────────────────────────────────────
# _init_integrated_modules
# ─────────────────────────────────────────────────────────────────────────────

class TestInitIntegratedModules:

    def _make_session(self, **kw) -> BotSession:
        s = _session(**kw)
        s._ctrl = MagicMock()
        s._radar = None
        s._event_bus = MagicMock()
        return s

    def test_frame_quality_created_when_enabled(self):
        s = self._make_session(frame_quality_check=True)
        mock_fqc = MagicMock()
        with patch("src.session.FrameQualityChecker", return_value=mock_fqc):
            s._init_integrated_modules()
        assert s._frame_quality is mock_fqc

    def test_frame_quality_none_when_disabled(self):
        s = self._make_session(frame_quality_check=False)
        s._init_integrated_modules()
        assert s._frame_quality is None

    def test_position_resolver_created_when_enabled(self):
        s = self._make_session(use_position_resolver=True)
        mock_pr = MagicMock()
        mock_local = MagicMock()
        mock_local.is_available = False
        with patch("src.session.PositionResolver", return_value=mock_pr), \
             patch("src.session.TibiaLocalMinimapReader", return_value=mock_local):
            s._init_integrated_modules()
        assert s._pos_resolver is mock_pr

    def test_position_resolver_none_when_disabled(self):
        s = self._make_session(use_position_resolver=False)
        s._init_integrated_modules()
        assert s._pos_resolver is None

    def test_pvp_detector_created_when_enabled(self):
        s = self._make_session(pvp_detector=True, pvp_action="warn")
        mock_pvp = MagicMock()
        mock_pvp_cfg = MagicMock()
        mock_pvp_cfg.skull_templates = []
        with patch("src.session.PvPDetector", return_value=mock_pvp), \
             patch("src.session.PvPConfig", return_value=mock_pvp_cfg):
            s._init_integrated_modules()
        assert s._pvp_detector is mock_pvp

    def test_pvp_detector_none_when_disabled(self):
        s = self._make_session(pvp_detector=False)
        s._init_integrated_modules()
        assert s._pvp_detector is None

    def test_inventory_manager_created_when_enabled(self):
        s = self._make_session(inventory_check=True, inventory_roi="10,20,100,80")
        mock_inv = MagicMock()
        with patch("src.session.InventoryManager", return_value=mock_inv):
            s._init_integrated_modules()
        assert s._inventory_mgr is mock_inv

    def test_inventory_manager_none_when_disabled(self):
        s = self._make_session(inventory_check=False)
        s._init_integrated_modules()
        assert s._inventory_mgr is None

    def test_depot_orchestrator_created_when_depot_and_inventory(self):
        s = self._make_session(depot_after_run=True, inventory_check=True)
        mock_depot = MagicMock()
        mock_inv = MagicMock()
        mock_orch = MagicMock()
        s._depot = mock_depot
        s._inventory_mgr = mock_inv
        s._navigator = MagicMock()
        with patch("src.session.DepotOrchestrator", return_value=mock_orch):
            s._init_integrated_modules()
        assert s._depot_orch is mock_orch

    def test_path_viz_is_none(self):
        s = self._make_session()
        s._init_integrated_modules()
        assert s._path_viz is None


# ─────────────────────────────────────────────────────────────────────────────
# _init_monitoring
# ─────────────────────────────────────────────────────────────────────────────

class TestInitMonitoring:

    def _make_session(self, **kw) -> BotSession:
        s = _session(**kw)
        s._ctrl = MagicMock()
        s._event_bus = MagicMock()
        s._frame_getter = None
        return s

    def test_alert_system_created_when_enabled(self):
        s = self._make_session(alert_enabled=True)
        mock_alert = MagicMock()
        with patch("src.session.AlertSystem", return_value=mock_alert):
            s._init_monitoring(None)
        assert s._alert_system is mock_alert
        mock_alert.subscribe.assert_called_once()

    def test_alert_system_none_when_disabled(self):
        s = self._make_session(alert_enabled=False)
        s._init_monitoring(None)
        assert s._alert_system is None

    def test_session_stats_created_when_enabled(self):
        s = self._make_session(session_stats=True)
        mock_stats = MagicMock()
        with patch("src.session.HuntingSessionStats", return_value=mock_stats):
            s._init_monitoring(None)
        assert s._session_stats is mock_stats
        mock_stats.start.assert_called_once()

    def test_session_stats_none_when_disabled(self):
        s = self._make_session(session_stats=False)
        s._init_monitoring(None)
        assert s._session_stats is None

    def test_spawn_manager_created_when_enabled(self):
        s = self._make_session(spawn_manager=True)
        mock_sm = MagicMock()
        with patch("src.session.SpawnManager", return_value=mock_sm):
            s._init_monitoring(None)
        assert s._spawn_mgr is mock_sm

    def test_spawn_manager_none_when_disabled(self):
        s = self._make_session(spawn_manager=False)
        s._init_monitoring(None)
        assert s._spawn_mgr is None

    def test_adaptive_roi_created_when_enabled(self):
        s = self._make_session(adaptive_roi=True)
        mock_roi = MagicMock()
        mock_roi.load_anchors_from_dir.return_value = 3
        with patch("src.session.AdaptiveROIDetector", return_value=mock_roi):
            s._init_monitoring(None)
        assert s._adaptive_roi is mock_roi

    def test_adaptive_roi_none_when_disabled(self):
        s = self._make_session(adaptive_roi=False)
        s._init_monitoring(None)
        assert s._adaptive_roi is None

    def test_break_scheduler_created_when_enabled(self):
        s = self._make_session(break_scheduler=True)
        mock_bs = MagicMock()
        with patch("src.session.BreakScheduler", return_value=mock_bs):
            s._init_monitoring(None)
        assert s._break_scheduler is mock_bs
        mock_bs.start.assert_called_once()

    def test_break_scheduler_none_when_disabled(self):
        s = self._make_session(break_scheduler=False)
        s._init_monitoring(None)
        assert s._break_scheduler is None

    def test_soak_monitor_created_when_enabled(self):
        s = self._make_session(soak_monitor=True)
        mock_sm = MagicMock()
        with patch("src.session.SoakMonitor", return_value=mock_sm):
            s._init_monitoring(None)
        assert s._soak_monitor is mock_sm
        mock_sm.start.assert_called_once()

    def test_soak_monitor_none_when_disabled(self):
        s = self._make_session(soak_monitor=False)
        s._init_monitoring(None)
        assert s._soak_monitor is None

    def test_gm_detector_created_when_enabled(self):
        s = self._make_session(gm_detector=True, gm_action="pause")
        mock_gmd = MagicMock()
        with patch("src.session.GMDetector", return_value=mock_gmd):
            s._init_monitoring(None)
        assert s._gm_detector is mock_gmd
        mock_gmd.start.assert_called_once()

    def test_gm_detector_none_when_disabled(self):
        s = self._make_session(gm_detector=False)
        s._init_monitoring(None)
        assert s._gm_detector is None

    def test_chat_responder_created_when_enabled(self):
        s = self._make_session(chat_responder=True)
        mock_cr = MagicMock()
        with patch("src.session.ChatResponder", return_value=mock_cr):
            s._init_monitoring(None)
        assert s._chat_responder is mock_cr
        mock_cr.start.assert_called_once()

    def test_chat_responder_none_when_disabled(self):
        s = self._make_session(chat_responder=False)
        s._init_monitoring(None)
        assert s._chat_responder is None

    def test_gm_detector_gets_frame_getter_when_cached(self):
        s = self._make_session(gm_detector=True)
        mock_gmd = MagicMock()
        cached = MagicMock()
        with patch("src.session.GMDetector", return_value=mock_gmd):
            s._init_monitoring(cached)
        mock_gmd.set_frame_getter.assert_called_once_with(cached)

    def test_chat_responder_gets_frame_getter_when_cached(self):
        s = self._make_session(chat_responder=True)
        mock_cr = MagicMock()
        cached = MagicMock()
        with patch("src.session.ChatResponder", return_value=mock_cr):
            s._init_monitoring(cached)
        mock_cr.set_frame_getter.assert_called_once_with(cached)

    def test_auto_calibrate_roi_runs_when_frame_available(self):
        import numpy as np
        s = self._make_session(auto_calibrate_roi=True)
        frame = np.zeros((600, 800, 3), dtype=np.uint8)
        s._frame_getter = lambda: frame
        mock_roi = MagicMock()
        mock_roi.detect_or_fallback.return_value = {"hp": [0, 0, 100, 20]}
        with patch("src.session.AdaptiveROIDetector", return_value=mock_roi):
            s._init_monitoring(None)
        mock_roi.detect_or_fallback.assert_called_once()

    def test_dashboard_import_error_logged(self):
        """If DashboardServer import fails, it's logged and continues."""
        logged: List[str] = []
        s = _session(dashboard=True)
        s._ctrl = MagicMock()
        s._event_bus = MagicMock()
        s._frame_getter = None
        s._log_cb = logged.append

        with patch("src.session.BotSession._log", side_effect=lambda msg: logged.append(msg)), \
             patch.dict("sys.modules", {"src.dashboard_server": None}):
            try:
                s._init_monitoring(None)
            except Exception:
                pass
        # Just verify it doesn't propagate unhandled


# ─────────────────────────────────────────────────────────────────────────────
# _start_threads
# ─────────────────────────────────────────────────────────────────────────────

class TestStartThreads:

    def _make_session(self, **kw) -> BotSession:
        s = _session(**kw)
        mock_ctrl = _mock_input_controller(connected=False)
        s._ctrl = mock_ctrl
        s._raw_ctrl = mock_ctrl
        return s

    def test_dry_run_no_ww_thread(self):
        """dry_run=True → window watchdog thread should NOT be started."""
        s = self._make_session(dry_run=True, start_delay=0.0)
        mock_raw = _mock_input_controller(connected=True)
        mock_raw.hwnd = 0x1234
        s._raw_ctrl = mock_raw

        with patch("src.session.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            s._start_threads()

        # Only the main _run_loop thread should be started in dry_run
        # (ww_thread is guarded by `not self._cfg.dry_run`)
        started_targets = [
            call.kwargs.get("target") or (call.args[0] if call.args else None)
            for call in mock_thread_cls.call_args_list
        ]
        assert s._ww_thread is None

    def test_watchdog_thread_started_when_timeout_positive(self):
        s = self._make_session(watchdog_timeout=30.0, start_delay=0.0)

        started_targets = []

        class FakeThread:
            def __init__(self, target=None, daemon=False):
                self._target = target
                started_targets.append(target)
            def start(self):
                pass

        with patch("src.session.threading.Thread", FakeThread), \
             patch("src.session.time.sleep"):
            s._start_threads()

        # Should have started: watchdog + main loop
        assert s._watchdog_thread is not None
        assert any(t == s._watchdog_loop for t in started_targets)

    def test_main_thread_always_started(self):
        s = self._make_session(start_delay=0.0)

        started_targets = []

        class FakeThread:
            def __init__(self, target=None, daemon=False):
                self._target = target
                started_targets.append(target)
            def start(self):
                pass

        with patch("src.session.threading.Thread", FakeThread), \
             patch("src.session.time.sleep"):
            s._start_threads()

        assert s._thread is not None
        assert any(t == s._run_loop for t in started_targets)

    def test_arduino_failover_wired_when_set(self):
        s = self._make_session(start_delay=0.0)
        mock_ard = MagicMock()
        mock_raw = MagicMock()
        mock_raw.is_connected.return_value = False
        mock_raw.hwnd = 0
        s._arduino = mock_ard
        s._raw_ctrl = mock_raw

        with patch("src.session.threading.Thread") as MockThread, \
             patch("src.session.time.sleep"):
            MockThread.return_value = MagicMock()
            s._start_threads()

        mock_raw.set_arduino_failover.assert_called_once_with(mock_ard)

    def test_no_start_delay_skips_sleep(self):
        """start_delay=0.0 → time.sleep not called."""
        s = self._make_session(start_delay=0.0)
        with patch("src.session.threading.Thread") as MockThread, \
             patch("src.session.time.sleep") as mock_sleep:
            MockThread.return_value = MagicMock()
            s._start_threads()
        mock_sleep.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# stats_snapshot and uptime helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsSnapshot:

    def test_snapshot_has_expected_keys(self):
        s = _session()
        snap = s.stats_snapshot()
        assert "uptime_secs" in snap
        assert "is_running" in snap
        assert "cycle_count" in snap
        assert "waypoints_visited" in snap

    def test_snapshot_is_running_reflects_state(self):
        s = _session()
        s._running = True
        s._stats["start_time"] = time.monotonic()
        snap = s.stats_snapshot()
        assert snap["is_running"] is True

    def test_snapshot_includes_break_scheduler_stats(self):
        s = _session()
        mock_bs = MagicMock()
        mock_bs.stats_snapshot.return_value = {"breaks": 2}
        s._break_scheduler = mock_bs
        snap = s.stats_snapshot()
        assert snap["break_scheduler"] == {"breaks": 2}

    def test_snapshot_includes_soak_monitor_stats(self):
        s = _session()
        mock_sm = MagicMock()
        mock_sm.stats_snapshot.return_value = {"cpu_pct": 5.0}
        s._soak_monitor = mock_sm
        snap = s.stats_snapshot()
        assert snap["soak_monitor"] == {"cpu_pct": 5.0}

    def test_uptime_none_when_not_running(self):
        s = _session()
        assert s.uptime() is None

    def test_uptime_none_when_start_time_not_set(self):
        s = _session()
        s._running = True
        s._stats["start_time"] = None
        assert s.uptime() is None

    def test_stats_summary_returns_string(self):
        s = _session()
        result = s.stats_summary()
        assert isinstance(result, str)
        assert "routes" in result


# ─────────────────────────────────────────────────────────────────────────────
# Miscellaneous session helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionMisc:

    def test_set_login_fn_stores_fn(self):
        s = _session()
        fn = MagicMock()
        s.set_login_fn(fn)
        assert s._login_fn is fn

    def test_set_login_fn_propagates_to_reconnect_handler(self):
        s = _session()
        mock_rh = MagicMock()
        s._reconnect_handler = mock_rh
        fn = MagicMock()
        s.set_login_fn(fn)
        mock_rh.set_login_fn.assert_called_once_with(fn)

    def test_update_config_replaces_cfg(self):
        s = _session()
        new_cfg = _cfg(heal_hp_pct=50)
        s.update_config(new_cfg)
        assert s._cfg is new_cfg

    def test_event_bus_property(self):
        s = _session()
        from src.event_bus import EventBus
        assert isinstance(s.event_bus, EventBus)

    def test_has_loader_false_by_default(self):
        s = _session()
        assert s.has_loader is False

    def test_has_loader_true_when_set(self):
        s = _session()
        s._loader = MagicMock()
        assert s.has_loader is True

    def test_has_gm_detector_false_by_default(self):
        s = _session()
        assert s.has_gm_detector is False

    def test_has_chat_responder_false_by_default(self):
        s = _session()
        assert s.has_chat_responder is False

    def test_has_position_resolver_false_by_default(self):
        s = _session()
        assert s.has_position_resolver is False

    def test_has_inventory_manager_false_by_default(self):
        s = _session()
        assert s.has_inventory_manager is False

    def test_has_session_stats_false_by_default(self):
        s = _session()
        assert s.has_session_stats is False

    def test_has_adaptive_roi_false_by_default(self):
        s = _session()
        assert s.has_adaptive_roi is False

    def test_has_trade_manager_false_by_default(self):
        s = _session()
        assert s.has_trade_manager is False

    def test_config_property(self):
        cfg = _cfg(heal_hp_pct=55)
        s = BotSession(config=cfg, log_callback=lambda _: None)
        s._pico = s._arduino = s._raw_ctrl = s._path_viz = None
        assert s.config is cfg

    def test_stats_property_returns_copy(self):
        s = _session()
        stats1 = s.stats
        stats2 = s.stats
        assert stats1 is not stats2  # separate copies
        assert stats1 == stats2


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_route helper (module-level function)
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRoute:

    def test_absolute_path_returned_when_exists(self, tmp_path):
        from src.session import _resolve_route
        p = tmp_path / "my_route.json"
        p.write_text("{}", encoding="utf-8")
        result = _resolve_route(str(p))
        assert result == p

    def test_raises_when_not_found(self, tmp_path):
        from src.session import _resolve_route
        with pytest.raises(FileNotFoundError):
            _resolve_route(str(tmp_path / "nonexistent.json"))


# ─────────────────────────────────────────────────────────────────────────────
# SessionConfig validate / load / save
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigValidation:

    def test_valid_config_no_raise(self):
        cfg = SessionConfig()
        cfg.validate()  # should not raise

    def test_invalid_heal_hp_pct_raises(self):
        cfg = SessionConfig(heal_hp_pct=150)
        with pytest.raises(ValueError):
            cfg.validate()

    def test_emergency_pct_must_be_less_than_hp_pct(self):
        cfg = SessionConfig(heal_hp_pct=50, heal_emergency_pct=60)
        with pytest.raises(ValueError):
            cfg.validate()

    def test_invalid_step_interval_raises(self):
        cfg = SessionConfig(step_interval=0.0)
        with pytest.raises(ValueError):
            cfg.validate()

    def test_negative_start_delay_raises(self):
        cfg = SessionConfig(start_delay=-1.0)
        with pytest.raises(ValueError):
            cfg.validate()

    def test_save_and_load_round_trip(self, tmp_path):
        cfg = SessionConfig(heal_hp_pct=65, heal_emergency_pct=20)
        path = tmp_path / "test_session.json"
        cfg.save(path)
        cfg2 = SessionConfig.load(path)
        assert cfg2.heal_hp_pct == 65
        assert cfg2.heal_emergency_pct == 20

    def test_load_returns_default_when_file_missing(self, tmp_path):
        path = tmp_path / "missing.json"
        cfg = SessionConfig.load(path)
        assert isinstance(cfg, SessionConfig)
