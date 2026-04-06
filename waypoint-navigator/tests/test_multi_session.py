"""Tests for MultiSessionManager — B1: Coverage push from 0% to ≥50%.

Covers:
- add / remove sessions
- start_all / stop_all lifecycle
- start(name) / stop(name) individual
- session_names, count, running_count properties
- stats_snapshot aggregation
- get_session retrieval
- error paths: duplicate add, unknown name
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.multi_session import MultiSessionManager, _ManagedSession


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_config(name: str = "TestClient") -> MagicMock:
    """Create a mock SessionConfig."""
    cfg = MagicMock()
    cfg.target_window = f"Tibia - {name}"
    # Required fields for SessionConfig validation
    cfg.step_interval = 0.1
    cfg.start_delay = 0
    return cfg


def _patch_bot_session():
    """Patch BotSession so it doesn't try to init real subsystems."""
    return patch("src.multi_session.BotSession")


# ══════════════════════════════════════════════════════════════════════════════
# Init & Configuration
# ══════════════════════════════════════════════════════════════════════════════

class TestMultiSessionInit:
    def test_default_init(self):
        mgr = MultiSessionManager()
        assert mgr.count == 0
        assert mgr.session_names == []
        assert mgr.running_count == 0

    def test_custom_log_callback(self):
        logs = []
        mgr = MultiSessionManager(log_callback=logs.append)
        assert mgr._log_cb is not None

    def test_shared_loader(self):
        mock_loader = MagicMock()
        mgr = MultiSessionManager(loader=mock_loader)
        assert mgr._loader is mock_loader


# ══════════════════════════════════════════════════════════════════════════════
# Add / Remove
# ══════════════════════════════════════════════════════════════════════════════

class TestAddRemove:
    def test_add_session(self):
        with _patch_bot_session():
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            assert mgr.count == 1
            assert "Knight-1" in mgr.session_names

    def test_add_duplicate_raises(self):
        with _patch_bot_session():
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            with pytest.raises(ValueError, match="already exists"):
                mgr.add("Knight-1", _mock_config("Knight1"))

    def test_add_multiple_sessions(self):
        with _patch_bot_session():
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.add("Druid-2", _mock_config("Druid2"))
            assert mgr.count == 2

    def test_remove_existing(self):
        with _patch_bot_session() as MockBS:
            MockBS.return_value.is_running = False
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.remove("Knight-1")
            assert mgr.count == 0

    def test_remove_nonexistent_is_noop(self):
        mgr = MultiSessionManager()
        mgr.remove("nonexistent")  # should not crash
        assert mgr.count == 0

    def test_remove_stops_running_session(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = True
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.remove("Knight-1")
            mock_session.stop.assert_called_once()

    def test_add_with_custom_loader(self):
        with _patch_bot_session() as MockBS:
            mgr = MultiSessionManager()
            custom_loader = MagicMock()
            mgr.add("Knight-1", _mock_config("Knight1"), loader=custom_loader)
            # BotSession should be created with the custom loader
            call_kwargs = MockBS.call_args[1]
            assert call_kwargs["loader"] is custom_loader

    def test_add_uses_shared_loader_when_no_custom(self):
        with _patch_bot_session() as MockBS:
            shared_loader = MagicMock()
            mgr = MultiSessionManager(loader=shared_loader)
            mgr.add("Knight-1", _mock_config("Knight1"))
            call_kwargs = MockBS.call_args[1]
            assert call_kwargs["loader"] is shared_loader


# ══════════════════════════════════════════════════════════════════════════════
# Lifecycle: start_all / stop_all
# ══════════════════════════════════════════════════════════════════════════════

class TestLifecycle:
    def test_start_all(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = False
            logs = []
            mgr = MultiSessionManager(log_callback=logs.append)
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.add("Druid-2", _mock_config("Druid2"))
            mgr.start_all()
            assert mock_session.start.call_count == 2

    def test_start_all_skips_already_running(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = True
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.start_all()
            mock_session.start.assert_not_called()

    def test_stop_all(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = True
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.add("Druid-2", _mock_config("Druid2"))
            mgr.stop_all()
            assert mock_session.stop.call_count == 2

    def test_stop_all_skips_not_running(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = False
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.stop_all()
            mock_session.stop.assert_not_called()

    def test_start_single(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = False
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.start("Knight-1")
            mock_session.start.assert_called_once()

    def test_start_unknown_raises(self):
        mgr = MultiSessionManager()
        with pytest.raises(KeyError, match="No session named"):
            mgr.start("nonexistent")

    def test_stop_single(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = True
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.stop("Knight-1")
            mock_session.stop.assert_called_once()

    def test_stop_unknown_raises(self):
        mgr = MultiSessionManager()
        with pytest.raises(KeyError, match="No session named"):
            mgr.stop("nonexistent")

    def test_stop_not_running_is_noop(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = False
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            mgr.stop("Knight-1")
            mock_session.stop.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# Properties & Status
# ══════════════════════════════════════════════════════════════════════════════

class TestStatus:
    def test_running_count(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = True
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            assert mgr.running_count == 1

    def test_is_running_existing(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = True
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            assert mgr.is_running("Knight-1") is True

    def test_is_running_nonexistent(self):
        mgr = MultiSessionManager()
        assert mgr.is_running("nope") is False

    def test_get_session_existing(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            assert mgr.get_session("Knight-1") is mock_session

    def test_get_session_nonexistent(self):
        mgr = MultiSessionManager()
        assert mgr.get_session("nope") is None


# ══════════════════════════════════════════════════════════════════════════════
# Stats Snapshot
# ══════════════════════════════════════════════════════════════════════════════

class TestStatsSnapshot:
    def test_empty_manager(self):
        mgr = MultiSessionManager()
        snap = mgr.stats_snapshot()
        assert snap["total_sessions"] == 0
        assert snap["running"] == 0
        assert snap["sessions"] == {}

    def test_with_sessions(self):
        with _patch_bot_session() as MockBS:
            mock_session = MockBS.return_value
            mock_session.is_running = False
            mock_session.stats_snapshot.return_value = {"uptime": 0}
            mgr = MultiSessionManager()
            mgr.add("Knight-1", _mock_config("Knight1"))
            snap = mgr.stats_snapshot()
            assert snap["total_sessions"] == 1
            assert "Knight-1" in snap["sessions"]
            assert snap["sessions"]["Knight-1"]["stats"] == {"uptime": 0}

    def test_log_callback_prefixes_name(self):
        """The log callback passed to BotSession should prefix with [name]."""
        with _patch_bot_session() as MockBS:
            logs = []
            mgr = MultiSessionManager(log_callback=logs.append)
            mgr.add("Knight-1", _mock_config("Knight1"))
            # Get the log_callback that was passed to BotSession
            call_kwargs = MockBS.call_args[1]
            log_fn = call_kwargs["log_callback"]
            log_fn("test message")
            assert "[Knight-1] test message" in logs
