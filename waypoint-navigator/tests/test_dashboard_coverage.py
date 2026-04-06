"""
test_dashboard_coverage.py — coverage boost for dashboard_server.py
100 % offline: no real HTTP, no websockets.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.dashboard_server import DashboardServer, _DashboardHTTPHandler


# ── _DashboardHTTPHandler ─────────────────────────────────────────────────────

def _make_handler(path="/", auth_token="", stats_fn=None):
    """Create a handler instance without actually connecting."""
    mock_request = MagicMock()
    mock_request.makefile.return_value = BytesIO(b"GET / HTTP/1.1\r\n\r\n")
    mock_server = MagicMock()
    mock_server.server_address = ("127.0.0.1", 9999)

    # Patch __init__ to avoid actually doing HTTP init
    with patch("src.dashboard_server._DashboardHTTPHandler.__init__", return_value=None):
        handler = _DashboardHTTPHandler.__new__(_DashboardHTTPHandler)

    cast(Any, handler).path = path
    cast(Any, handler).headers = {}
    cast(Any, handler).wfile = BytesIO()
    cast(Any, handler)._auth_token = auth_token
    cast(Any, handler)._stats_fn = stats_fn
    cast(Any, handler)._start_time = time.monotonic()
    cast(Any, handler).server = mock_server
    cast(Any, handler).request = mock_request
    return handler


def _send_response(handler, code):
    handler._response_code = code


class TestDashboardHTTPHandler:
    def test_check_auth_no_token(self):
        h = _make_handler()
        h._auth_token = ""
        assert h._check_auth() is True

    def test_check_auth_valid_bearer(self):
        h = _make_handler(auth_token="secret")
        h._auth_token = "secret"
        h.headers = {"Authorization": "Bearer secret"}
        assert h._check_auth() is True

    def test_check_auth_invalid_bearer(self):
        h = _make_handler(auth_token="secret")
        h._auth_token = "secret"
        h.headers = {"Authorization": "Bearer wrong"}
        with patch.object(h, "send_error") as mock_err:
            result = h._check_auth()
        assert result is False
        mock_err.assert_called_with(401, "Unauthorized")

    def test_check_auth_no_bearer_prefix(self):
        h = _make_handler(auth_token="secret")
        h._auth_token = "secret"
        h.headers = {"Authorization": "Basic abc"}
        with patch.object(h, "send_error"):
            result = h._check_auth()
        assert result is False

    def test_do_get_root(self):
        h = _make_handler(path="/")
        with patch.object(h, "_check_auth", return_value=True), \
             patch.object(h, "_serve_html") as mock_html:
            h.do_GET()
        mock_html.assert_called_once()

    def test_do_get_index(self):
        h = _make_handler(path="/index.html")
        with patch.object(h, "_check_auth", return_value=True), \
             patch.object(h, "_serve_html") as mock_html:
            h.do_GET()
        mock_html.assert_called_once()

    def test_do_get_health(self):
        h = _make_handler(path="/health")
        with patch.object(h, "_check_auth", return_value=True), \
             patch.object(h, "_serve_health") as mock_health:
            h.do_GET()
        mock_health.assert_called_once()

    def test_do_get_404(self):
        h = _make_handler(path="/unknown")
        with patch.object(h, "_check_auth", return_value=True), \
             patch.object(h, "send_error") as mock_err:
            h.do_GET()
        mock_err.assert_called_with(404)

    def test_do_get_auth_fail(self):
        h = _make_handler(path="/")
        with patch.object(h, "_check_auth", return_value=False):
            # should return early without calling _serve_html
            with patch.object(h, "_serve_html") as mock_html:
                h.do_GET()
        mock_html.assert_not_called()

    def test_serve_health_no_psutil(self):
        h = _make_handler()
        h._stats_fn = None
        buf = BytesIO()
        h.wfile = buf
        with patch.object(h, "send_response"), \
             patch.object(h, "send_header"), \
             patch.object(h, "end_headers"), \
             patch.dict("sys.modules", {"psutil": None}):
            h._serve_health()
        buf.seek(0)
        content = buf.read()
        data = json.loads(content)
        assert data["status"] == "ok"

    def test_serve_health_with_psutil(self):
        h = _make_handler()
        h._stats_fn = lambda: {"hp": 100}
        buf = BytesIO()
        h.wfile = buf
        mock_proc = MagicMock()
        mock_proc.memory_info.return_value = MagicMock(rss=50 * 1024 * 1024)
        mock_proc.num_threads.return_value = 4
        mock_psutil = MagicMock()
        mock_psutil.Process.return_value = mock_proc
        with patch.object(h, "send_response"), \
             patch.object(h, "send_header"), \
             patch.object(h, "end_headers"), \
             patch.dict("sys.modules", {"psutil": mock_psutil}):
            h._serve_health()
        buf.seek(0)
        data = json.loads(buf.read())
        assert "subsystems" in data

    def test_serve_health_stats_fn_raises(self):
        h = _make_handler()
        h._stats_fn = lambda: (_ for _ in ()).throw(RuntimeError("fail"))
        buf = BytesIO()
        h.wfile = buf
        with patch.object(h, "send_response"), \
             patch.object(h, "send_header"), \
             patch.object(h, "end_headers"), \
             patch.dict("sys.modules", {"psutil": None}):
            h._serve_health()
        buf.seek(0)
        data = json.loads(buf.read())
        assert data.get("subsystems") == "error"

    def test_serve_html_file_not_found(self):
        h = _make_handler()
        buf = BytesIO()
        h.wfile = buf
        with patch("src.dashboard_server._HTML_FILE") as mock_file, \
             patch.object(h, "send_response"), \
             patch.object(h, "send_header"), \
             patch.object(h, "end_headers"):
            mock_file.exists.return_value = False
            h._serve_html()
        buf.seek(0)
        content = buf.read()
        assert b"not found" in content

    def test_serve_html_injects_ws_url(self):
        h = _make_handler()
        h._ws_url = "ws://localhost:8765"
        h._auth_token = "tok"
        buf = BytesIO()
        h.wfile = buf
        with patch("src.dashboard_server._HTML_FILE") as mock_file, \
             patch.object(h, "send_response"), \
             patch.object(h, "send_header"), \
             patch.object(h, "end_headers"):
            mock_file.exists.return_value = True
            mock_file.read_text.return_value = "const WS_PLACEHOLDER = '__WS_URL__';"
            h._serve_html()
        buf.seek(0)
        content = buf.read().decode("utf-8")
        assert "ws://localhost:8765?token=tok" in content

    def test_log_message_suppressed(self):
        h = _make_handler()
        # Should not raise
        h.log_message("%s %s", "GET", "/")


# ── DashboardServer ───────────────────────────────────────────────────────────

class TestDashboardServer:
    def _make_server(self, **kw):
        with patch.dict("os.environ", {}, clear=True):
            return DashboardServer(**kw)

    def test_init_defaults(self):
        srv = self._make_server(port=0, ws_port=0)
        assert srv._running is False

    def test_init_with_token(self):
        srv = self._make_server(port=0, ws_port=0, auth_token="tok")
        assert srv._auth_token == "tok"

    def test_init_token_from_env(self):
        with patch.dict("os.environ", {"DASHBOARD_TOKEN": "envtok"}):
            srv = DashboardServer(port=0, ws_port=0)
        assert srv._auth_token == "envtok"

    def test_push_log(self):
        srv = self._make_server(port=0)
        srv.push_log("hello")
        assert "hello" in srv._log_buffer

    def test_push_log_overflow(self):
        srv = self._make_server(port=0)
        for i in range(300):
            srv.push_log(f"line {i}")
        assert len(srv._log_buffer) <= srv._log_buffer_max

    def test_push_event(self):
        srv = self._make_server(port=0)
        srv.push_event("test_event", {"x": 1})
        assert len(srv._event_buffer) == 1

    def test_push_event_overflow(self):
        srv = self._make_server(port=0)
        for i in range(200):
            srv.push_event("e", i)
        assert len(srv._event_buffer) <= srv._event_buffer_max

    def test_set_stats_fn(self):
        srv = self._make_server(port=0)
        fn = lambda: {"a": 1}
        srv.set_stats_fn(fn)
        assert srv._stats_fn is fn

    def test_urls(self):
        srv = self._make_server(port=8080, ws_port=8765)
        assert "8080" in srv.http_url
        assert "8765" in srv.ws_url

    def test_client_count(self):
        srv = self._make_server(port=0)
        assert srv.client_count == 0

    def test_build_message(self):
        srv = self._make_server(port=0)
        srv._stats_fn = lambda: {"hp": 100}
        msg = srv._build_message("init")
        assert msg["type"] == "init"
        assert "stats" in msg
        assert "logs" in msg

    def test_build_message_stats_raises(self):
        srv = self._make_server(port=0)
        srv._stats_fn = lambda: (_ for _ in ()).throw(ValueError("boom"))
        msg = srv._build_message("update")
        assert "error" in msg["stats"]

    def test_start_stop(self):
        srv = self._make_server(port=0, ws_port=0)
        mock_httpserver = MagicMock()
        with patch("src.dashboard_server.HTTPServer", return_value=mock_httpserver), \
             patch("threading.Thread") as mock_thread:
            t1 = MagicMock()
            t2 = MagicMock()
            mock_thread.side_effect = [t1, t2]
            srv.start()
        assert srv._running is True
        srv.stop()

    def test_start_idempotent(self):
        srv = self._make_server(port=0, ws_port=0)
        srv._running = True
        mock_httpserver = MagicMock()
        with patch("src.dashboard_server.HTTPServer", return_value=mock_httpserver):
            srv.start()  # should be a no-op
        # still running, no extra threads
        assert srv._running is True

    def test_run_ws_no_websockets(self):
        """_run_ws should fall back gracefully when websockets is not installed."""
        srv = self._make_server(port=0, ws_port=0)
        srv._running = False  # immediately stop the fallback loop
        with patch.dict("sys.modules", {"websockets": None, "websockets.server": None}):
            srv._run_ws()

    def test_run_http(self):
        srv = self._make_server(port=0, ws_port=0)
        mock_http = MagicMock()
        mock_http.serve_forever.side_effect = Exception("stop")
        srv._http_server = mock_http
        srv._run_http()  # should not raise

    def test_stop_without_threads(self):
        srv = self._make_server(port=0, ws_port=0)
        srv._running = True
        # No threads assigned — stop should not crash
        srv.stop()
