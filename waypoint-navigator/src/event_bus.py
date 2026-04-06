"""
EventBus
--------
Lightweight synchronous pub/sub event bus that decouples modules inside
the session.

Usage::

    from src.event_bus import EventBus

    bus = EventBus()

    # Subscribe
    bus.subscribe("e1", lambda data: print(f"kill! {data}"))
    bus.subscribe("e4", lambda data: None)

    # Emit (calls all registered handlers synchronously)
    bus.emit("e1", {"name": "troll", "coord": coord})

Supported events (by convention)
---------------------------------
``"kill"``          — combat: monster confirmed dead
                      ``data = {"name": str, "coord": Coordinate | None}``
``"heal"``          — healer: heal hotkey fired
                      ``data = {"hp_pct": int}``
``"mana"``          — healer: mana hotkey fired
                      ``data = {"mp_pct": int}``
``"condition"``     — condition monitor: new condition detected
                      ``data = {"condition": str}``
``"condition_clear"``— condition monitor: condition gone
                      ``data = {"condition": str}``
``"depot_done"``    — depot manager: cycle completed
                      ``data = {"items": int, "cycle": int}``
``"loot_done"``     — looter: corpse looted
                      ``data = {"items": int, "corpse": str}``
``"route_done"``    — session: one full route loop finished
                      ``data = {"cycle": int}``
``"watchdog"``      — session: no movement for too long
                      ``data = {"idle_secs": float}``
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List

logger = logging.getLogger("wn.eb")


# ---------------------------------------------------------------------------
Handler = Callable[[Any], None]


class EventBus:
    """
    Thread-safe synchronous event bus.

    All handlers registered for an event are called in the order they were
    subscribed, on the same thread that called :meth:`emit`.

    Parameters
    ----------
    on_error : callable, optional
        ``fn(event, handler, exc)`` called when a handler raises.
        Default: silently ignores exceptions.
    """

    def __init__(
        self,
        on_error: Callable[[str, Handler, Exception], None] | None = None,
    ) -> None:
        self._handlers: Dict[str, List[Handler]] = {}
        self._lock = threading.Lock()
        self._on_error = on_error
        self._handler_errors: int = 0

    # ── Subscription ─────────────────────────────────────────────────────────

    def subscribe(self, event: str, handler: Handler) -> None:
        """
        Register *handler* for *event*.

        The same handler can be registered multiple times for the same event;
        it will be called that many times per emit.

        Parameters
        ----------
        event : str
            Event name (e.g. ``"kill"``, ``"heal"``).
        handler : callable
            ``fn(data: Any) -> None`` — receives the data passed to emit.
        """
        with self._lock:
            self._handlers.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> bool:
        """
        Remove the first occurrence of *handler* from *event*.

        Returns ``True`` if the handler was found and removed.
        """
        with self._lock:
            lst = self._handlers.get(event, [])
            try:
                lst.remove(handler)
                return True
            except ValueError:
                return False

    def unsubscribe_all(self, event: str | None = None) -> None:
        """
        Remove all handlers.

        If *event* is given, only that event's handlers are cleared.
        If *event* is ``None``, all handlers for all events are cleared.
        """
        with self._lock:
            if event is None:
                self._handlers.clear()
            else:
                self._handlers.pop(event, None)

    # ── Emission ─────────────────────────────────────────────────────────────

    def emit(self, event: str, data: Any = None) -> int:
        """
        Call all registered handlers for *event* synchronously.

        Parameters
        ----------
        event : str
            Event name.
        data : Any, optional
            Payload passed to each handler.

        Returns
        -------
        int
            Number of handlers that were called.
        """
        with self._lock:
            handlers = list(self._handlers.get(event, []))

        called = 0
        for h in handlers:
            try:
                h(data)
                called += 1
            except Exception as exc:
                with self._lock:
                    self._handler_errors += 1
                if self._on_error is not None:
                    self._on_error(event, h, exc)
                else:
                    logger.warning(
                        "[EventBus] handler %r for event %r raised: %s",
                        h, event, exc,
                    )
        return called

    # ── Introspection ────────────────────────────────────────────────────────

    def subscriber_count(self, event: str) -> int:
        """Return the number of handlers registered for *event*."""
        with self._lock:
            return len(self._handlers.get(event, []))

    def registered_events(self) -> List[str]:
        """Return a sorted list of event names that have at least one handler."""
        with self._lock:
            return sorted(k for k, v in self._handlers.items() if v)

    @property
    def total_handlers(self) -> int:
        """Total number of handler registrations across all events."""
        with self._lock:
            return sum(len(v) for v in self._handlers.values())

    @property
    def has_any_handlers(self) -> bool:
        """True when at least one handler is registered."""
        return self.total_handlers > 0

    @property
    def handler_errors(self) -> int:
        """Total number of handler exceptions caught during emit()."""
        return self._handler_errors

    def stats_snapshot(self) -> Dict[str, Any]:
        """Return a lightweight dict of event bus statistics."""
        return {
            "registered_events": self.registered_events(),
            "total_handlers": self.total_handlers,
            "handler_errors": self._handler_errors,
        }

    def __repr__(self) -> str:  # pragma: no cover
        events = self.registered_events()
        return f"EventBus(events={events}, total_handlers={self.total_handlers})"
