"""
src/dashboard_server.py — Web dashboard for remote remote monitoring.

Serves a lightweight HTML5 dashboard with live stats via WebSocket.

Architecture:
  - asyncio WebSocket server (stdlib + websockets library)
  - JSON messages: session stats, events, log lines
  - Static HTML served via a simple HTTP handler

Usage::

    from src.dashboard_server import DashboardServer

    server = DashboardServer(port=8765)
    server.set_stats_fn(lambda: session.monitor_snapshot())
    server.start()          # runs in a background thread
    # ... bot runs ...
    server.stop()

Or standalone::

    python -m src.dashboard_server --port 8765
"""

from __future__ import annotations

import asyncio
import hmac
import importlib
import json
import logging
import os
import random
import socket
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Set, cast

_log = logging.getLogger("wn.ds")

# Path to the embedded HTML file
_HTML_DIR = Path(__file__).parent.parent / "src"
_HTML_FILE = Path(__file__).parent / "dashboard.html"

# Default refresh interval (seconds) for pushing stats
_DEFAULT_PUSH_INTERVAL = 2.0


class _DashboardHTTPHandler(SimpleHTTPRequestHandler):
    """Serves the dashboard HTML file on GET /."""

    # Class-level references set by DashboardServer before starting
    _stats_fn: Optional[Callable[[], Dict[str, Any]]] = None
    _start_time: float = 0.0
    _ws_url: str = ""
    _auth_token: str = ""  # empty = no auth required

    def _check_auth(self) -> bool:
        """Validate Bearer token.  Returns True if authorised."""
        if not self._auth_token:
            return True  # no token configured → allow all
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            provided = auth[7:]
            if hmac.compare_digest(provided, self._auth_token):
                return True
        self.send_error(401, "Unauthorized")
        return False

    def do_GET(self) -> None:
        if not self._check_auth():
            return
        if self.path in ("/", "/index.html"):
            self._serve_html()
        elif self.path == "/health":
            self._serve_health()
        else:
            self.send_error(404)

    def _serve_health(self) -> None:
        health: Dict[str, Any] = {"status": "ok"}
        try:
            psutil = importlib.import_module("psutil")
            proc = psutil.Process()
            health["uptime_s"] = round(time.monotonic() - self._start_time, 1)
            health["memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
            health["threads"] = proc.num_threads()
        except Exception:
            _log.debug("health endpoint failed to collect process stats", exc_info=True)
            health["uptime_s"] = round(time.monotonic() - self._start_time, 1)
        if self._stats_fn is not None:
            try:
                health["subsystems"] = deepcopy(self._stats_fn())
            except Exception:
                _log.debug("health endpoint stats snapshot failed", exc_info=True)
                health["subsystems"] = "error"
        body = json.dumps(health).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self) -> None:
        if _HTML_FILE.exists():
            ws_url = self._ws_url
            if ws_url and self._auth_token:
                sep = "&" if "?" in ws_url else "?"
                ws_url = f"{ws_url}{sep}token={self._auth_token}"
            content = _HTML_FILE.read_text(encoding="utf-8").replace("__WS_URL__", ws_url).encode("utf-8")
        else:
            content = b"<html><body><h1>Dashboard HTML not found</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Suppress default stderr logging
        _log.debug("HTTP: " + format, *args)


class DashboardServer:
    """
    WebSocket + HTTP server for live remote monitoring.

    Parameters
    ----------
    port : int
        HTTP port for the dashboard (default 8080).
    ws_port : int
        WebSocket port for live updates (default 8765).
    push_interval : float
        Seconds between stat pushes (default 2.0).
    auth_token : str
        Bearer token required for all HTTP/WS connections.
        Empty string disables authentication.
    """

    def __init__(
        self,
        port: int = 0,
        ws_port: int = 0,
        push_interval: float = _DEFAULT_PUSH_INTERVAL,
        auth_token: str = "",
    ) -> None:
        # Resolve token from environment if not explicitly provided
        if not auth_token:
            auth_token = os.environ.get("DASHBOARD_TOKEN", "")
        if not auth_token:
            _log.warning(
                "DashboardServer: no auth token configured — dashboard is "
                "accessible without authentication. Set the DASHBOARD_TOKEN "
                "environment variable or pass auth_token= to enable auth."
            )
        # Use ephemeral ports by default to avoid fingerprinting
        self._http_port = port or random.randint(49152, 65000)
        self._ws_port = ws_port or random.randint(49152, 65000)
        self._push_interval = push_interval
        self._auth_token = auth_token

        self._stats_fn: Optional[Callable[[], Dict[str, Any]]] = None
        self._log_buffer: List[str] = []
        self._log_buffer_max = 200
        self._event_buffer: List[Dict[str, Any]] = []
        self._event_buffer_max = 100

        self._running = False
        self._http_server: Optional[HTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_clients: Set[Any] = set()
        self._lock = threading.Lock()

    # ── Configuration ────────────────────────────────────────────────────

    def set_stats_fn(self, fn: Callable[[], Dict[str, Any]]) -> None:
        """Set a callable that returns the current session stats dict."""
        self._stats_fn = fn
        # Keep the HTTP handler class variable in sync so live updates take effect
        # even when set_stats_fn() is called after start()
        cast(Any, _DashboardHTTPHandler)._stats_fn = fn

    def push_log(self, line: str) -> None:
        """Add a log line to the buffer (thread-safe)."""
        with self._lock:
            self._log_buffer.append(line)
            if len(self._log_buffer) > self._log_buffer_max:
                self._log_buffer = self._log_buffer[-self._log_buffer_max:]

    def push_event(self, event_name: str, data: Any = None) -> None:
        """Add an event to the buffer (thread-safe)."""
        with self._lock:
            self._event_buffer.append({
                "event": event_name,
                "data": data,
                "ts": time.time(),
            })
            if len(self._event_buffer) > self._event_buffer_max:
                self._event_buffer = self._event_buffer[-self._event_buffer_max:]

    # ── Start / Stop ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start both HTTP and WebSocket servers in background threads."""
        if self._running:
            return
        self._assert_port_available(self._http_port, "HTTP")
        self._assert_port_available(self._ws_port, "WebSocket")

        # HTTP server
        cast(Any, _DashboardHTTPHandler)._stats_fn = self._stats_fn
        _DashboardHTTPHandler._start_time = time.monotonic()
        _DashboardHTTPHandler._ws_url = self.ws_url
        _DashboardHTTPHandler._auth_token = self._auth_token
        self._http_server = HTTPServer(
            ("127.0.0.1", self._http_port), _DashboardHTTPHandler,  # M8-fix: bind localhost only
        )
        self._http_thread = threading.Thread(
            target=self._run_http, daemon=True, name=f"t-{os.urandom(3).hex()}",
        )
        self._running = True
        self._http_thread.start()

        # WebSocket server
        self._ws_thread = threading.Thread(
            target=self._run_ws, daemon=True, name=f"t-{os.urandom(3).hex()}",
        )
        self._ws_thread.start()

        _log.info(
            "Dashboard started: http://localhost:%d  ws://localhost:%d",
            self._http_port, self._ws_port,
        )

    def stop(self) -> None:
        """Stop both servers."""
        self._running = False
        http_server = self._http_server
        self._http_server = None
        if http_server is not None:
            try:
                http_server.shutdown()
            finally:
                http_server.server_close()

        ws_loop = self._ws_loop
        if ws_loop is not None and ws_loop.is_running():
            ws_loop.call_soon_threadsafe(ws_loop.stop)

        current_thread = threading.current_thread()
        for thread in (self._http_thread, self._ws_thread):
            if thread is not None and thread is not current_thread:
                thread.join(timeout=3.0)

        self._http_thread = None
        self._ws_thread = None
        self._ws_loop = None
        with self._lock:
            self._ws_clients.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def http_url(self) -> str:
        return f"http://localhost:{self._http_port}"

    @property
    def ws_url(self) -> str:
        return f"ws://localhost:{self._ws_port}"

    @property
    def client_count(self) -> int:
        return len(self._ws_clients)

    # ── HTTP thread ──────────────────────────────────────────────────────

    def _run_http(self) -> None:
        try:
            self._http_server.serve_forever()  # type: ignore[union-attr]
        except Exception as exc:
            _log.error("Dashboard HTTP error: %s", exc)

    # ── WebSocket thread ─────────────────────────────────────────────────

    def _run_ws(self) -> None:
        """Run the WebSocket server in a new asyncio event loop."""
        try:
            websockets = importlib.import_module("websockets")
        except ImportError:
            _log.warning(
                "websockets not installed — WebSocket push disabled. "
                "Install with: pip install websockets"
            )
            # Fall back to a polling-only mode (HTTP /health endpoint)
            while self._running:
                time.sleep(random.uniform(0.7, 1.4))
            return

        self._ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._ws_loop)

        auth_token = self._auth_token  # captured for closures

        async def _handler(ws: Any) -> None:
            # H5-fix: validate token from query param ?token=<tok>
            if auth_token:
                qs = parse_qs(urlparse(ws.request.path).query)
                provided = (qs.get("token") or [""])[0]
                if not hmac.compare_digest(provided, auth_token):
                    await ws.close(4001, "Unauthorized")
                    _log.warning("WS: rejected client (bad token)")
                    return

            self._ws_clients.add(ws)
            _log.info("WS client connected (%d total)", len(self._ws_clients))
            try:
                # Send initial snapshot
                await ws.send(json.dumps(self._build_message("init")))
                # Keep connection alive; handle incoming pings/commands
                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        if data.get("type") == "ping":
                            await ws.send(json.dumps({"type": "pong"}))
                    except json.JSONDecodeError:
                        _log.debug("WS: invalid JSON from client")
                    except Exception:
                        _log.debug("WS: error handling client message", exc_info=True)
            except Exception:
                _log.debug("WS: client connection error", exc_info=True)
            finally:
                self._ws_clients.discard(ws)
                _log.info("WS client disconnected (%d remaining)", len(self._ws_clients))

        async def _push_loop() -> None:
            """Periodically push stats to all connected clients."""
            while self._running:
                await asyncio.sleep(self._push_interval)
                if self._ws_clients:
                    msg = json.dumps(self._build_message("update"))
                    disconnected = set()
                    for ws in list(self._ws_clients):
                        try:
                            await ws.send(msg)
                        except Exception:
                            _log.debug("WS: failed to send to client, disconnecting", exc_info=True)
                            disconnected.add(ws)
                    self._ws_clients -= disconnected

        async def _main() -> None:
            async with websockets.serve(_handler, "127.0.0.1", self._ws_port):  # M8-fix
                await _push_loop()

        try:
            self._ws_loop.run_until_complete(_main())
        except RuntimeError as exc:
            if "Event loop stopped before Future completed" not in str(exc):
                _log.error("Dashboard WS error: %s", exc)
        except Exception as exc:
            _log.error("Dashboard WS error: %s", exc)
        finally:
            pending = [task for task in asyncio.all_tasks(self._ws_loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                self._ws_loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._ws_loop.run_until_complete(self._ws_loop.shutdown_asyncgens())
            self._ws_loop.close()
            self._ws_loop = None
            with self._lock:
                self._ws_clients.clear()

    def _assert_port_available(self, port: int, label: str) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise OSError(f"{label} port {port} is already in use") from exc
        finally:
            probe.close()

    # ── Message building ─────────────────────────────────────────────────

    @staticmethod
    def _normalize_position(position: Any) -> Any:
        if position is None or isinstance(position, dict):
            return position
        if all(hasattr(position, attr) for attr in ("x", "y", "z")):
            return {
                "x": getattr(position, "x", None),
                "y": getattr(position, "y", None),
                "z": getattr(position, "z", None),
            }
        return position

    def _normalize_stats(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        normalized = deepcopy(stats)
        if "uptime_seconds" not in normalized and "uptime_secs" in normalized:
            normalized["uptime_seconds"] = normalized["uptime_secs"]
        route_name = normalized.get("route_name")
        if route_name is not None and "route" not in normalized:
            normalized["route"] = route_name
        if "position" in normalized:
            normalized["position"] = self._normalize_position(normalized.get("position"))
        return normalized

    def _build_message(self, msg_type: str) -> Dict[str, Any]:
        """Build a JSON-serializable message for WebSocket clients."""
        stats: Dict[str, Any] = {}
        if self._stats_fn is not None:
            try:
                with self._lock:
                    stats = self._normalize_stats(deepcopy(self._stats_fn()))
            except Exception as exc:
                stats = {"error": str(exc)}

        with self._lock:
            logs = list(self._log_buffer[-50:])
            events = list(self._event_buffer[-20:])

        return {
            "type": msg_type,
            "ts": time.time(),
            "stats": stats,
            "logs": logs,
            "events": events,
            "clients": len(self._ws_clients),
        }


# ── CLI entry point ──────────────────────────────────────────────────────────

def _demo_stats() -> Dict[str, Any]:
    """Demo stats for standalone testing."""
    return {
        "routes_completed": 42,
        "heal_fired": 128,
        "mana_fired": 67,
        "loot_events": 15,
        "uptime_seconds": time.time() % 3600,
        "route": "demo_route.json",
        "current_wpt": "[node  32369,32241,7]",
        "position": {"x": 32369, "y": 32241, "z": 7},
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Dashboard server (standalone)")
    p.add_argument("--port", type=int, default=8080, help="HTTP port")
    p.add_argument("--ws-port", type=int, default=8765, help="WebSocket port")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG)
    srv = DashboardServer(port=args.port, ws_port=args.ws_port)
    srv.set_stats_fn(_demo_stats)
    srv.start()
    _log.info("Dashboard: %s  |  WS: %s", srv.http_url, srv.ws_url)
    try:
        while True:
            time.sleep(random.uniform(0.8, 1.3))
    except KeyboardInterrupt:
        srv.stop()
        _log.info("Stopped.")
