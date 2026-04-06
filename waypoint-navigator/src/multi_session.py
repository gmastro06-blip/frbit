"""Multi-window session manager — run N Sessions in parallel.

Each session targets a different Tibia client window and uses its own
config.  The manager handles lifecycle (start/stop), aggregated stats,
and a single callback for consolidated logging.

Usage::

    from src.multi_session import MultiSessionManager

    mgr = MultiSessionManager()
    mgr.add("Knight-1", SessionConfig(target_window="Tibia - Knight1", ...))
    mgr.add("Druid-2",  SessionConfig(target_window="Tibia - Druid2",  ...))
    mgr.start_all()
    # ...
    mgr.stop_all()
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .session import BotSession, SessionConfig
from .map_loader import TibiaMapLoader


@dataclass
class _ManagedSession:
    """Internal wrapper around a BotSession with metadata."""
    name: str
    config: SessionConfig
    session: BotSession
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None


class MultiSessionManager:
    """Orchestrates multiple BotSession instances targeting different windows.

    Parameters
    ----------
    loader : TibiaMapLoader | None
        Shared map loader (avoids loading map data N times).
    log_callback : callable | None
        Consolidated log callback — receives ``"[name] message"`` format.
    """

    def __init__(
        self,
        loader: Optional[TibiaMapLoader] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._loader = loader
        self._log_cb = log_callback or print
        self._sessions: Dict[str, _ManagedSession] = {}
        self._lock = threading.Lock()

    # ── Adding / removing sessions ────────────────────────────────────────

    def add(
        self,
        name: str,
        config: SessionConfig,
        loader: Optional[TibiaMapLoader] = None,
    ) -> None:
        """Register a new session under *name*.

        Raises ``ValueError`` if a session with *name* already exists.
        """
        def _prefixed_log(msg: str) -> None:
            self._log_cb(f"[{name}] {msg}")

        session = BotSession(
            config=config,
            loader=loader or self._loader,
            log_callback=_prefixed_log,
        )
        with self._lock:
            if name in self._sessions:
                raise ValueError(f"Session '{name}' already exists")
            self._sessions[name] = _ManagedSession(
                name=name, config=config, session=session
            )

    def remove(self, name: str) -> None:
        """Stop (if running) and remove session *name*."""
        with self._lock:
            ms = self._sessions.pop(name, None)
        if ms is None:
            return
        if ms.session.is_running:
            ms.session.stop()
            ms.stopped_at = time.time()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start_all(self) -> None:
        """Start every registered session that is not already running."""
        with self._lock:
            items = list(self._sessions.values())
        for ms in items:
            if not ms.session.is_running:
                self._log_cb(f"[MultiSession] Starting '{ms.name}' …")
                ms.session.start()
                ms.started_at = time.time()

    def stop_all(self) -> None:
        """Stop every running session."""
        with self._lock:
            items = list(self._sessions.values())
        for ms in items:
            if ms.session.is_running:
                self._log_cb(f"[MultiSession] Stopping '{ms.name}' …")
                ms.session.stop()
                ms.stopped_at = time.time()

    def start(self, name: str) -> None:
        """Start a single session by *name*."""
        with self._lock:
            ms = self._sessions.get(name)
        if ms is None:
            raise KeyError(f"No session named '{name}'")
        if not ms.session.is_running:
            ms.session.start()
            ms.started_at = time.time()

    def stop(self, name: str) -> None:
        """Stop a single session by *name*."""
        with self._lock:
            ms = self._sessions.get(name)
        if ms is None:
            raise KeyError(f"No session named '{name}'")
        if ms.session.is_running:
            ms.session.stop()
            ms.stopped_at = time.time()

    # ── Status ────────────────────────────────────────────────────────────

    @property
    def session_names(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    @property
    def running_count(self) -> int:
        with self._lock:
            return sum(1 for ms in self._sessions.values() if ms.session.is_running)

    def is_running(self, name: str) -> bool:
        with self._lock:
            ms = self._sessions.get(name)
        return ms.session.is_running if ms else False

    def stats_snapshot(self) -> Dict[str, Any]:
        """Aggregated stats from all sessions."""
        with self._lock:
            items = list(self._sessions.items())
        result: Dict[str, Any] = {
            "total_sessions": len(items),
            "running": sum(1 for _, ms in items if ms.session.is_running),
            "sessions": {},
        }
        for name, ms in items:
            result["sessions"][name] = {
                "running": ms.session.is_running,
                "started_at": ms.started_at,
                "stopped_at": ms.stopped_at,
                "stats": ms.session.stats_snapshot(),
            }
        return result

    def get_session(self, name: str) -> "Optional[BotSession]":
        """Get the underlying BotSession by *name* (for advanced control)."""
        with self._lock:
            ms = self._sessions.get(name)
        return ms.session if ms else None
