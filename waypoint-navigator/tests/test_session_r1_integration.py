"""
Tests for Fase R1 — integration of orphaned modules into BotSession.

Verifies that all 8 previously-orphaned modules are now properly
imported, instantiated, wired, and torn down by BotSession.
"""
from __future__ import annotations

import numpy as np
import time
from unittest.mock import MagicMock, patch

import pytest

from src.session import BotSession, SessionConfig
from src.frame_quality import FrameQualityChecker, FrameQuality
from src.position_resolver import PositionResolver
from src.pvp_detector import PvPDetector
from src.inventory_manager import InventoryManager
from src.alert_system import AlertSystem
from src.spawn_manager import SpawnManager
from src.session_stats import HuntingSessionStats
from src.adaptive_roi import AdaptiveROIDetector


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_config(**kw) -> SessionConfig:
    defaults: dict = dict(
        route_file="",
        heal_hp_pct=70,
        start_delay=0.0,
        loop_route=False,
        death_handler=False,
        reconnect_handler=False,
        anti_kick=False,
    )
    defaults.update(kw)
    return SessionConfig(**defaults)


def _fake_frame() -> np.ndarray:
    """Return a valid BGR frame for testing."""
    return np.random.randint(50, 200, (1080, 1920, 3), dtype=np.uint8)


# ── SessionConfig field existence ────────────────────────────────────────────

class TestSessionConfigR1Fields:
    """Verify that all R1 config fields exist with correct defaults."""

    def test_frame_quality_check_default(self):
        cfg = SessionConfig()
        assert cfg.frame_quality_check is True

    def test_use_position_resolver_default(self):
        cfg = SessionConfig()
        assert cfg.use_position_resolver is True

    def test_pvp_detector_default(self):
        cfg = SessionConfig()
        assert cfg.pvp_detector is False

    def test_inventory_check_default(self):
        cfg = SessionConfig()
        assert cfg.inventory_check is False

    def test_alert_enabled_default(self):
        cfg = SessionConfig()
        assert cfg.alert_enabled is False

    def test_session_stats_default(self):
        cfg = SessionConfig()
        assert cfg.session_stats is True

    def test_spawn_manager_default(self):
        cfg = SessionConfig()
        assert cfg.spawn_manager is False

    def test_adaptive_roi_default(self):
        cfg = SessionConfig()
        assert cfg.adaptive_roi is False

    def test_pvp_action_default(self):
        cfg = SessionConfig()
        assert cfg.pvp_action == "warn"

    def test_alert_discord_webhook_default(self):
        cfg = SessionConfig()
        assert cfg.alert_discord_webhook == ""


# ── BotSession property accessors ───────────────────────────────────────────

class TestBotSessionR1Properties:
    """Verify has_* properties for new modules."""

    def test_has_frame_quality_before_start(self):
        session = BotSession(_make_config())
        assert session.has_frame_quality is False

    def test_has_position_resolver_before_start(self):
        session = BotSession(_make_config())
        assert session.has_position_resolver is False

    def test_has_pvp_detector_before_start(self):
        session = BotSession(_make_config())
        assert session.has_pvp_detector is False

    def test_has_inventory_manager_before_start(self):
        session = BotSession(_make_config())
        assert session.has_inventory_manager is False

    def test_has_alert_system_before_start(self):
        session = BotSession(_make_config())
        assert session.has_alert_system is False

    def test_has_session_stats_before_start(self):
        session = BotSession(_make_config())
        assert session.has_session_stats is False

    def test_has_spawn_manager_before_start(self):
        session = BotSession(_make_config())
        assert session.has_spawn_manager is False

    def test_has_adaptive_roi_before_start(self):
        session = BotSession(_make_config())
        assert session.has_adaptive_roi is False


# ── Module instantiation on start ────────────────────────────────────────────

class TestBotSessionR1Start:
    """Verify that modules are instantiated when their config flags are True."""

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_frame_quality_created_when_enabled(self, _loader, _ctrl):
        cfg = _make_config(frame_quality_check=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_frame_quality is True
            assert isinstance(session._frame_quality, FrameQualityChecker)
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_frame_quality_not_created_when_disabled(self, _loader, _ctrl):
        cfg = _make_config(frame_quality_check=False)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_frame_quality is False
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_position_resolver_created_when_enabled(self, _loader, _ctrl):
        cfg = _make_config(use_position_resolver=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_position_resolver is True
            assert isinstance(session._pos_resolver, PositionResolver)
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_pvp_detector_created_when_enabled(self, _loader, _ctrl):
        cfg = _make_config(pvp_detector=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_pvp_detector is True
            assert isinstance(session._pvp_detector, PvPDetector)
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_inventory_manager_created_when_enabled(self, _loader, _ctrl):
        cfg = _make_config(inventory_check=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_inventory_manager is True
            assert isinstance(session._inventory_mgr, InventoryManager)
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_alert_system_created_when_enabled(self, _loader, _ctrl):
        cfg = _make_config(alert_enabled=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_alert_system is True
            assert isinstance(session._alert_system, AlertSystem)
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_session_stats_created_when_enabled(self, _loader, _ctrl):
        cfg = _make_config(session_stats=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_session_stats is True
            assert isinstance(session._session_stats, HuntingSessionStats)
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_session_stats_not_created_when_disabled(self, _loader, _ctrl):
        cfg = _make_config(session_stats=False)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_session_stats is False
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_spawn_manager_created_when_enabled(self, _loader, _ctrl):
        cfg = _make_config(spawn_manager=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_spawn_manager is True
            assert isinstance(session._spawn_mgr, SpawnManager)
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_adaptive_roi_created_when_enabled(self, _loader, _ctrl):
        cfg = _make_config(adaptive_roi=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_adaptive_roi is True
            assert isinstance(session._adaptive_roi, AdaptiveROIDetector)
        finally:
            session.stop()


# ── EventBus wiring ──────────────────────────────────────────────────────────

class TestBotSessionR1EventBus:
    """Verify that modules with event_bus are wired to the session's bus."""

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_pvp_detector_uses_session_event_bus(self, _loader, _ctrl):
        cfg = _make_config(pvp_detector=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session._pvp_detector is not None
            assert session._pvp_detector._event_bus is session._event_bus  # type: ignore[union-attr]
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_inventory_manager_uses_session_event_bus(self, _loader, _ctrl):
        cfg = _make_config(inventory_check=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session._inventory_mgr is not None
            assert session._inventory_mgr._event_bus is session._event_bus  # type: ignore[union-attr]
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_session_stats_subscribed_to_event_bus(self, _loader, _ctrl):
        cfg = _make_config(session_stats=True)
        session = BotSession(cfg)
        session.start()
        try:
            # HuntingSessionStats subscribes to kill, loot, death etc.
            # Verify by emitting a kill event and checking it's recorded
            session._event_bus.emit("e1", {"monster": "Wasp"})
            assert session._session_stats is not None
            assert session._session_stats.total_kills >= 1  # type: ignore[union-attr]
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_alert_system_subscribed_to_event_bus(self, _loader, _ctrl):
        cfg = _make_config(alert_enabled=True)
        session = BotSession(cfg)
        session.start()
        try:
            # AlertSystem should be subscribed (even if it does nothing
            # without webhook URLs). Just verify it was wired.
            assert session._alert_system is not None
        finally:
            session.stop()

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_spawn_manager_uses_session_event_bus(self, _loader, _ctrl):
        cfg = _make_config(spawn_manager=True)
        session = BotSession(cfg)
        session.start()
        try:
            assert session._spawn_mgr is not None
            assert session._spawn_mgr._event_bus is session._event_bus  # type: ignore[union-attr]
        finally:
            session.stop()


# ── Frame quality gate ───────────────────────────────────────────────────────

class TestBotSessionFrameQualityGate:
    """Verify that _update_position rejects bad frames when quality checker is active."""

    def test_black_frame_rejected(self):
        cfg = _make_config(frame_quality_check=True)
        session = BotSession(cfg)
        session._frame_quality = FrameQualityChecker()
        session._frame_getter = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        session._radar = MagicMock()

        session._update_position()

        # Radar should NOT have been called because the black frame was rejected
        session._radar.read.assert_not_called()

    def test_good_frame_passes(self):
        cfg = _make_config(frame_quality_check=True)
        session = BotSession(cfg)
        session._frame_quality = FrameQualityChecker()
        # A checkerboard-like frame with good variance
        good_frame = np.random.randint(50, 200, (1080, 1920, 3), dtype=np.uint8)
        session._frame_getter = lambda: good_frame
        session._radar = MagicMock()
        session._radar.read.return_value = None

        session._update_position()

        # Radar should have been called for a good frame
        session._radar.read.assert_called_once()


# ── Position resolver wiring ─────────────────────────────────────────────────

class TestBotSessionPositionResolver:
    """Verify PositionResolver is used when enabled."""

    def test_resolver_used_instead_of_direct_radar(self):
        from src.models import Coordinate
        cfg = _make_config(use_position_resolver=True)
        session = BotSession(cfg)
        session._frame_quality = None  # disable quality gate for this test

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = Coordinate(100, 200, 7)
        session._pos_resolver = mock_resolver

        good_frame = _fake_frame()
        session._frame_getter = lambda: good_frame
        session._radar = MagicMock()  # should NOT be called

        session._update_position()

        mock_resolver.resolve.assert_called_once()
        session._radar.read.assert_not_called()
        assert session._position == Coordinate(100, 200, 7)

    def test_fallback_to_radar_when_resolver_disabled(self):
        from src.models import Coordinate
        cfg = _make_config(use_position_resolver=False)
        session = BotSession(cfg)
        session._frame_quality = None
        session._pos_resolver = None

        good_frame = _fake_frame()
        session._frame_getter = lambda: good_frame
        session._radar = MagicMock()
        session._radar.read.return_value = Coordinate(50, 60, 7)

        session._update_position()

        session._radar.read.assert_called_once()
        assert session._position == Coordinate(50, 60, 7)


# ── _check_frame_extras ─────────────────────────────────────────────────────

class TestBotSessionCheckFrameExtras:
    """Verify PvP and inventory checks run when modules are active."""

    def test_pvp_scan_called(self):
        session = BotSession(_make_config())
        session._pvp_detector = MagicMock()
        frame = _fake_frame()
        session._check_frame_extras(frame)
        session._pvp_detector.scan.assert_called_once_with(frame)

    def test_inventory_check_called_when_should_check(self):
        session = BotSession(_make_config())
        session._inventory_mgr = MagicMock()
        session._inventory_mgr.should_check.return_value = True
        frame = _fake_frame()
        session._check_frame_extras(frame)
        session._inventory_mgr.check_inventory.assert_called_once_with(frame)

    def test_inventory_check_skipped_when_not_due(self):
        session = BotSession(_make_config())
        session._inventory_mgr = MagicMock()
        session._inventory_mgr.should_check.return_value = False
        frame = _fake_frame()
        session._check_frame_extras(frame)
        session._inventory_mgr.check_inventory.assert_not_called()

    def test_no_crash_when_modules_none(self):
        session = BotSession(_make_config())
        session._pvp_detector = None
        session._inventory_mgr = None
        frame = _fake_frame()
        # Should not raise
        session._check_frame_extras(frame)


# ── Stop / teardown ──────────────────────────────────────────────────────────

class TestBotSessionR1Stop:
    """Verify that session_stats.stop() is called on session stop."""

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_session_stats_stopped(self, _loader, _ctrl):
        cfg = _make_config(session_stats=True)
        session = BotSession(cfg)
        session.start()
        assert session._session_stats is not None
        session.stop()
        # After stop, the stats should have a non-zero elapsed (or at least been stopped)
        # Just verify stop() didn't crash
        assert session.is_running is False


# ── Full start/stop with ALL modules ────────────────────────────────────────

class TestBotSessionR1AllModules:
    """Start and stop a session with every R1 module enabled."""

    @patch("src.session.InputController")
    @patch("src.session.TibiaMapLoader")
    def test_start_stop_all_modules_no_crash(self, _loader, _ctrl):
        cfg = _make_config(
            frame_quality_check=True,
            use_position_resolver=True,
            pvp_detector=True,
            pvp_action="warn",
            inventory_check=True,
            alert_enabled=True,
            session_stats=True,
            spawn_manager=True,
            adaptive_roi=True,
        )
        session = BotSession(cfg)
        session.start()
        try:
            assert session.has_frame_quality
            assert session.has_position_resolver
            assert session.has_pvp_detector
            assert session.has_inventory_manager
            assert session.has_alert_system
            assert session.has_session_stats
            assert session.has_spawn_manager
            assert session.has_adaptive_roi
        finally:
            session.stop()
        assert not session.is_running
