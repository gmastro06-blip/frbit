"""B3: DashboardServer coverage push — 22% → ≥45%.

Covers: init, set_stats_fn, push_log, push_event, start/stop HTTP,
_build_message, is_running property, http_url/ws_url, buffer limits.
"""
from __future__ import annotations

import json
import time
import threading
import types
from unittest.mock import MagicMock, patch

import pytest

from src.dashboard_server import DashboardServer, _DashboardHTTPHandler


# ══════════════════════════════════════════════════════════════════════════════
# Init & Config
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardServerInit:
    def test_default_ports(self):
        srv = DashboardServer()
        assert 49152 <= srv._http_port <= 65000
        assert 49152 <= srv._ws_port <= 65000

    def test_custom_ports(self):
        srv = DashboardServer(port=9090, ws_port=9191, push_interval=5.0)
        assert srv._http_port == 9090
        assert srv._ws_port == 9191
        assert srv._push_interval == 5.0

    def test_not_running_initially(self):
        srv = DashboardServer()
        assert srv.is_running is False

    def test_http_url(self):
        srv = DashboardServer(port=8080)
        assert srv.http_url == "http://localhost:8080"

    def test_ws_url(self):
        srv = DashboardServer(ws_port=8765)
        assert srv.ws_url == "ws://localhost:8765"

    def test_client_count_zero(self):
        srv = DashboardServer()
        assert srv.client_count == 0

    def test_initial_buffers_empty(self):
        srv = DashboardServer()
        assert srv._log_buffer == []
        assert srv._event_buffer == []


# ══════════════════════════════════════════════════════════════════════════════
# Stats function
# ══════════════════════════════════════════════════════════════════════════════

class TestSetStatsFn:
    def test_set_and_use(self):
        srv = DashboardServer()
        srv.set_stats_fn(lambda: {"hp": 100})
        assert srv._stats_fn is not None
        assert srv._stats_fn() == {"hp": 100}

    def test_none_by_default(self):
        srv = DashboardServer()
        assert srv._stats_fn is None


# ══════════════════════════════════════════════════════════════════════════════
# Push log / event buffers
# ══════════════════════════════════════════════════════════════════════════════

class TestPushLog:
    def test_push_one_log(self):
        srv = DashboardServer()
        srv.push_log("hello")
        assert srv._log_buffer == ["hello"]

    def test_buffer_limit(self):
        srv = DashboardServer()
        for i in range(250):
            srv.push_log(f"line-{i}")
        assert len(srv._log_buffer) == 200  # max = _log_buffer_max

    def test_thread_safety(self):
        srv = DashboardServer()
        errors = []

        def pusher():
            try:
                for i in range(100):
                    srv.push_log(f"t-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=pusher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(srv._log_buffer) <= 200


class TestPushEvent:
    def test_push_one_event(self):
        srv = DashboardServer()
        srv.push_event("e4", {"amount": 50})
        assert len(srv._event_buffer) == 1
        assert srv._event_buffer[0]["event"] == "e4"
        assert srv._event_buffer[0]["data"]["amount"] == 50
        assert "ts" in srv._event_buffer[0]

    def test_buffer_limit(self):
        srv = DashboardServer()
        for i in range(120):
            srv.push_event(f"evt-{i}")
        assert len(srv._event_buffer) == 100  # max = _event_buffer_max


# ══════════════════════════════════════════════════════════════════════════════
# _build_message
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildMessage:
    def test_init_message_structure(self):
        srv = DashboardServer()
        srv.set_stats_fn(lambda: {"hp": 100, "mp": 80})
        srv.push_log("log-1")
        srv.push_event("combat_start")
        msg = srv._build_message("init")
        assert msg["type"] == "init"
        assert "ts" in msg
        assert msg["stats"] == {"hp": 100, "mp": 80}
        assert "log-1" in msg["logs"]
        assert len(msg["events"]) == 1
        assert msg["clients"] == 0

    def test_update_message(self):
        srv = DashboardServer()
        msg = srv._build_message("update")
        assert msg["type"] == "update"
        assert msg["stats"] == {}  # no stats_fn set

    def test_stats_fn_exception_captured(self):
        srv = DashboardServer()
        srv.set_stats_fn(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        msg = srv._build_message("update")
        assert "error" in msg["stats"]

    def test_normalizes_uptime_seconds_alias(self):
        srv = DashboardServer()
        srv.set_stats_fn(lambda: {"uptime_secs": 45})

        msg = srv._build_message("update")

        assert msg["stats"]["uptime_seconds"] == 45

    def test_normalizes_position_objects(self):
        srv = DashboardServer()
        srv.set_stats_fn(lambda: {"position": types.SimpleNamespace(x=10, y=20, z=7)})

        msg = srv._build_message("update")

        assert msg["stats"]["position"] == {"x": 10, "y": 20, "z": 7}


# ══════════════════════════════════════════════════════════════════════════════
# Start / Stop HTTP
# ══════════════════════════════════════════════════════════════════════════════

class TestStartStop:
    def test_start_sets_running(self):
        # Use high port numbers to avoid conflicts
        srv = DashboardServer(port=18080, ws_port=18765)
        try:
            srv.start()
            assert srv.is_running is True
            assert srv._http_thread is not None
            assert srv._ws_thread is not None
        finally:
            srv.stop()

    def test_stop_clears_running(self):
        srv = DashboardServer(port=18081, ws_port=18766)
        srv.start()
        srv.stop()
        assert srv.is_running is False

    def test_start_idempotent(self):
        srv = DashboardServer(port=18082, ws_port=18767)
        try:
            srv.start()
            srv.start()  # should not crash or start duplicate servers
            assert srv.is_running is True
        finally:
            srv.stop()

    def test_stop_without_start(self):
        srv = DashboardServer()
        srv.stop()  # should not crash

    def test_start_raises_cleanly_when_port_in_use(self):
        srv = DashboardServer(port=18082, ws_port=18767)

        with patch.object(srv, "_assert_port_available", side_effect=OSError("in use")):
            with pytest.raises(OSError):
                srv.start()

        assert srv.is_running is False
        assert srv._http_thread is None
        assert srv._ws_thread is None

    def test_stop_closes_servers_and_joins_threads(self):
        srv = DashboardServer()
        srv._running = True
        http_server = MagicMock()
        http_thread = MagicMock()
        ws_thread = MagicMock()
        ws_loop = MagicMock()
        ws_loop.is_running.return_value = True
        srv._http_server = http_server
        srv._http_thread = http_thread
        srv._ws_thread = ws_thread
        srv._ws_loop = ws_loop

        srv.stop()

        http_server.shutdown.assert_called_once()
        http_server.server_close.assert_called_once()
        ws_loop.call_soon_threadsafe.assert_called_once_with(ws_loop.stop)
        http_thread.join.assert_called_once()
        ws_thread.join.assert_called_once()
        assert srv._http_thread is None
        assert srv._ws_thread is None
        assert srv._ws_loop is None


# ══════════════════════════════════════════════════════════════════════════════
# HTTP Handler
# ══════════════════════════════════════════════════════════════════════════════

class TestHTTPHandler:
    def test_health_endpoint(self):
        """Verify /health returns 200 with status ok."""
        import io
        from http.server import HTTPServer

        srv = DashboardServer(port=18083, ws_port=18768)
        try:
            srv.start()
            time.sleep(0.3)  # give HTTP server time to bind

            import urllib.request
            resp = urllib.request.urlopen(f"http://127.0.0.1:18083/health", timeout=2)
            data = json.loads(resp.read().decode())
            assert data["status"] == "ok"
        finally:
            srv.stop()

    def test_root_returns_html(self):
        srv = DashboardServer(port=18084, ws_port=18769)
        try:
            srv.start()
            time.sleep(0.3)

            import urllib.request
            resp = urllib.request.urlopen("http://127.0.0.1:18084/", timeout=2)
            content_type = resp.headers.get("Content-Type", "")
            assert "text/html" in content_type
        finally:
            srv.stop()
