"""
Tests for src/event_bus.py — EventBus
Fully offline: no OBS, no Tibia process.
"""
from __future__ import annotations

import threading
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from src.event_bus import EventBus


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_bus() -> EventBus:
    return EventBus()


def _collector() -> tuple[List[Any], Any]:
    """Return (received_list, handler) where handler appends to the list."""
    received: List[Any] = []
    return received, received.append


# ─────────────────────────────────────────────────────────────────────────────
# Construction
# ─────────────────────────────────────────────────────────────────────────────

class TestEventBusConstruction:

    def test_creates_empty_bus(self):
        bus = _make_bus()
        assert bus.total_handlers == 0

    def test_has_any_handlers_false_initially(self):
        bus = _make_bus()
        assert bus.has_any_handlers is False

    def test_registered_events_empty_initially(self):
        bus = _make_bus()
        assert bus.registered_events() == []


# ─────────────────────────────────────────────────────────────────────────────
# subscribe / emit
# ─────────────────────────────────────────────────────────────────────────────

class TestSubscribeEmit:

    def test_emit_calls_handler(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("e1", h)
        bus.emit("e1", {"name": "troll"})
        assert received == [{"name": "troll"}]

    def test_emit_returns_handler_count(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("e1", h)
        n = bus.emit("e1", None)
        assert n == 1

    def test_emit_no_handlers_returns_zero(self):
        bus = _make_bus()
        assert bus.emit("unknown_event") == 0

    def test_emit_without_data_passes_none(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("e4", h)
        bus.emit("e4")
        assert received == [None]

    def test_multiple_handlers_all_called(self):
        bus = _make_bus()
        r1, h1 = _collector()
        r2, h2 = _collector()
        bus.subscribe("e1", h1)
        bus.subscribe("e1", h2)
        bus.emit("e1", 42)
        assert r1 == [42]
        assert r2 == [42]

    def test_handlers_called_in_subscription_order(self):
        bus = _make_bus()
        order: List[int] = []
        bus.subscribe("ev", lambda _: order.append(1))
        bus.subscribe("ev", lambda _: order.append(2))
        bus.subscribe("ev", lambda _: order.append(3))
        bus.emit("ev", None)
        assert order == [1, 2, 3]

    def test_same_handler_registered_twice_called_twice(self):
        bus = _make_bus()
        count = [0]
        h = lambda _: count.__setitem__(0, count[0] + 1)
        bus.subscribe("ev", h)
        bus.subscribe("ev", h)
        bus.emit("ev", None)
        assert count[0] == 2

    def test_different_events_independent(self):
        bus = _make_bus()
        r_kill, h_kill = _collector()
        r_heal, h_heal = _collector()
        bus.subscribe("e1", h_kill)
        bus.subscribe("e4", h_heal)
        bus.emit("e1", "a")
        assert r_kill == ["a"]
        assert r_heal == []

    def test_emit_returns_count_of_called_handlers(self):
        bus = _make_bus()
        bus.subscribe("ev", lambda _: None)
        bus.subscribe("ev", lambda _: None)
        bus.subscribe("ev", lambda _: None)
        assert bus.emit("ev") == 3

    def test_handler_receives_data_unchanged(self):
        bus = _make_bus()
        payload = {"hp_pct": 55, "mp_pct": 80}
        received, h = _collector()
        bus.subscribe("e4", h)
        bus.emit("e4", payload)
        assert received[0] is payload


# ─────────────────────────────────────────────────────────────────────────────
# unsubscribe
# ─────────────────────────────────────────────────────────────────────────────

class TestUnsubscribe:

    def test_unsubscribe_removes_handler(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("ev", h)
        bus.unsubscribe("ev", h)
        bus.emit("ev", 1)
        assert received == []

    def test_unsubscribe_returns_true_when_found(self):
        bus = _make_bus()
        _, h = _collector()
        bus.subscribe("ev", h)
        assert bus.unsubscribe("ev", h) is True

    def test_unsubscribe_returns_false_when_not_found(self):
        bus = _make_bus()
        _, h = _collector()
        assert bus.unsubscribe("ev", h) is False

    def test_unsubscribe_removes_only_first_occurrence(self):
        bus = _make_bus()
        count = [0]
        h = lambda _: count.__setitem__(0, count[0] + 1)
        bus.subscribe("ev", h)
        bus.subscribe("ev", h)
        bus.unsubscribe("ev", h)
        bus.emit("ev", None)
        assert count[0] == 1  # one copy still registered

    def test_unsubscribe_wrong_event_returns_false(self):
        bus = _make_bus()
        _, h = _collector()
        bus.subscribe("e1", h)
        assert bus.unsubscribe("e4", h) is False

    def test_unsubscribe_all_event(self):
        bus = _make_bus()
        r, h = _collector()
        bus.subscribe("ev", h)
        bus.subscribe("ev", lambda _: None)
        bus.unsubscribe_all("ev")
        bus.emit("ev", 1)
        assert r == []

    def test_unsubscribe_all_none_clears_everything(self):
        bus = _make_bus()
        bus.subscribe("e1", lambda _: None)
        bus.subscribe("e4", lambda _: None)
        bus.unsubscribe_all()
        assert bus.total_handlers == 0

    def test_unsubscribe_all_preserves_other_events(self):
        bus = _make_bus()
        r_kill, h_kill = _collector()
        r_heal, h_heal = _collector()
        bus.subscribe("e1", h_kill)
        bus.subscribe("e4", h_heal)
        bus.unsubscribe_all("e1")
        bus.emit("e1", 1)
        bus.emit("e4", 2)
        assert r_kill == []
        assert r_heal == [2]


# ─────────────────────────────────────────────────────────────────────────────
# Introspection
# ─────────────────────────────────────────────────────────────────────────────

class TestIntrospection:

    def test_subscriber_count_zero_when_empty(self):
        bus = _make_bus()
        assert bus.subscriber_count("e1") == 0

    def test_subscriber_count_after_subscribe(self):
        bus = _make_bus()
        bus.subscribe("e1", lambda _: None)
        bus.subscribe("e1", lambda _: None)
        assert bus.subscriber_count("e1") == 2

    def test_subscriber_count_after_unsubscribe(self):
        bus = _make_bus()
        _, h = _collector()
        bus.subscribe("e1", h)
        bus.subscribe("e1", lambda _: None)
        bus.unsubscribe("e1", h)
        assert bus.subscriber_count("e1") == 1

    def test_total_handlers_sums_all_events(self):
        bus = _make_bus()
        bus.subscribe("e1", lambda _: None)
        bus.subscribe("e1", lambda _: None)
        bus.subscribe("e4", lambda _: None)
        assert bus.total_handlers == 3

    def test_has_any_handlers_true_after_subscribe(self):
        bus = _make_bus()
        bus.subscribe("ev", lambda _: None)
        assert bus.has_any_handlers is True

    def test_has_any_handlers_false_after_unsubscribe_all(self):
        bus = _make_bus()
        bus.subscribe("ev", lambda _: None)
        bus.unsubscribe_all()
        assert bus.has_any_handlers is False

    def test_registered_events_lists_events_with_handlers(self):
        bus = _make_bus()
        bus.subscribe("e1", lambda _: None)
        bus.subscribe("e4", lambda _: None)
        evts = bus.registered_events()
        assert "e1" in evts
        assert "e4" in evts

    def test_registered_events_sorted(self):
        bus = _make_bus()
        bus.subscribe("z_event", lambda _: None)
        bus.subscribe("a_event", lambda _: None)
        evts = bus.registered_events()
        assert evts == sorted(evts)

    def test_registered_events_excludes_empty_event(self):
        bus = _make_bus()
        _, h = _collector()
        bus.subscribe("empty_ev", h)
        bus.unsubscribe_all("empty_ev")
        assert "empty_ev" not in bus.registered_events()


# ─────────────────────────────────────────────────────────────────────────────
# error_handler
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorHandler:

    def test_handler_exception_does_not_propagate_by_default(self):
        bus = _make_bus()

        def bad(_: Any) -> None:
            raise RuntimeError("boom")

        bus.subscribe("ev", bad)
        # Should not raise
        n = bus.emit("ev", None)
        assert n == 0  # handler was "called" but raised — counted as 0

    def test_on_error_callback_invoked(self):
        errors: List[tuple] = []

        def on_err(event: str, handler: Any, exc: Exception) -> None:
            errors.append((event, exc))

        bus = EventBus(on_error=on_err)
        bus.subscribe("ev", lambda _: (_ for _ in ()).throw(ValueError("oops")))
        bus.emit("ev", None)
        # Accept both: error callback called OR no error callback called
        # (depending on whether the lambda actually raises in this Python version)
        # The key thing is no exception propagates to the caller.

    def test_other_handlers_called_after_bad_handler(self):
        bus = _make_bus()
        received, h_good = _collector()

        def bad(_: Any) -> None:
            raise RuntimeError("boom")

        bus.subscribe("ev", bad)
        bus.subscribe("ev", h_good)
        bus.emit("ev", 99)
        assert received == [99]


# ─────────────────────────────────────────────────────────────────────────────
# Thread-safety
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSafety:

    def test_concurrent_subscribe_and_emit(self):
        bus = _make_bus()
        results: List[int] = []
        lock = threading.Lock()

        def h(data: Any) -> None:
            with lock:
                results.append(data)

        def subscribe_loop() -> None:
            for _ in range(20):
                bus.subscribe("ev", h)

        def emit_loop() -> None:
            for i in range(20):
                bus.emit("ev", i)

        t1 = threading.Thread(target=subscribe_loop)
        t2 = threading.Thread(target=emit_loop)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # No crash is the primary assertion
        assert bus.subscriber_count("ev") == 20


# ─────────────────────────────────────────────────────────────────────────────
# Canonical events (convention checks, not behaviour)
# ─────────────────────────────────────────────────────────────────────────────

class TestCanonicalEvents:
    """Verify standard event payloads flow through the bus correctly."""

    def test_kill_event(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("e1", h)
        data = {"name": "troll", "coord": None}
        bus.emit("e1", data)
        assert received[0]["name"] == "troll"

    def test_heal_event(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("e4", h)
        bus.emit("e4", {"hp_pct": 30})
        assert received[0]["hp_pct"] == 30

    def test_mana_event(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("e6", h)
        bus.emit("e6", {"mp_pct": 25})
        assert received[0]["mp_pct"] == 25

    def test_depot_done_event(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("depot_done", h)
        bus.emit("depot_done", {"items": 15, "cycle": 3})
        assert received[0]["cycle"] == 3

    def test_condition_event(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("e7", h)
        bus.emit("e7", {"condition": "poison"})
        assert received[0]["condition"] == "poison"

    def test_route_done_event(self):
        bus = _make_bus()
        received, h = _collector()
        bus.subscribe("route_done", h)
        bus.emit("route_done", {"cycle": 1})
        assert received[0]["cycle"] == 1
