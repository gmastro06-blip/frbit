"""Tests for src.frame_cache.FrameCache — T7."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.frame_cache import FrameCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frame(val: int = 42) -> np.ndarray:
    """Return a small 2×2 BGR frame filled with *val*."""
    return np.full((2, 2, 3), val, dtype=np.uint8)


# ---------------------------------------------------------------------------
# Basic TTL behaviour
# ---------------------------------------------------------------------------

class TestFrameCacheTTL:
    """Verify that the TTL-based cache returns cached vs fresh frames."""

    def test_first_call_captures(self) -> None:
        getter = MagicMock(return_value=_make_frame(1))
        cache = FrameCache(getter, ttl_ms=50)
        result = cache.get_frame()
        assert result is not None
        assert np.array_equal(result, _make_frame(1))
        getter.assert_called_once()

    def test_second_call_within_ttl_returns_cached(self) -> None:
        getter = MagicMock(return_value=_make_frame(1))
        cache = FrameCache(getter, ttl_ms=500)  # generous TTL
        f1 = cache.get_frame()
        f2 = cache.get_frame()
        assert f1 is f2  # same object — no new capture
        getter.assert_called_once()

    def test_call_after_ttl_recaptures(self) -> None:
        getter = MagicMock(side_effect=[_make_frame(1), _make_frame(2)])
        cache = FrameCache(getter, ttl_ms=10)
        f1 = cache.get_frame()
        time.sleep(0.020)  # exceed 10ms TTL
        f2 = cache.get_frame()
        assert f1 is not None
        assert f2 is not None
        assert not np.array_equal(f1, f2)
        assert getter.call_count == 2

    def test_invalidate_forces_recapture(self) -> None:
        getter = MagicMock(side_effect=[_make_frame(1), _make_frame(2)])
        cache = FrameCache(getter, ttl_ms=5000)
        f1 = cache.get_frame()
        cache.invalidate()
        f2 = cache.get_frame()
        assert f1 is not None
        assert f2 is not None
        assert not np.array_equal(f1, f2)
        assert getter.call_count == 2

    def test_none_frame_is_cached(self) -> None:
        """None from getter should be cached — don't hammer a failing source."""
        getter = MagicMock(return_value=None)
        cache = FrameCache(getter, ttl_ms=500)
        r1 = cache.get_frame()
        r2 = cache.get_frame()
        assert r1 is None
        assert r2 is None
        getter.assert_called_once()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestFrameCacheProperties:

    def test_ttl_ms_property(self) -> None:
        cache = FrameCache(MagicMock(), ttl_ms=123.0)
        assert cache.ttl_ms == pytest.approx(123.0, abs=0.01)

    def test_default_ttl(self) -> None:
        cache = FrameCache(MagicMock())
        assert cache.ttl_ms == pytest.approx(50.0, abs=0.01)

    def test_raw_getter_property(self) -> None:
        fn = MagicMock()
        cache = FrameCache(fn, ttl_ms=10)
        assert cache.raw_getter is fn

    def test_ttl_ms_setter_updates_runtime_value(self) -> None:
        cache = FrameCache(MagicMock(), ttl_ms=10)
        cache.ttl_ms = 125.0
        assert cache.ttl_ms == pytest.approx(125.0, abs=0.01)

    def test_frame_changed_true_when_diff_check_disabled(self) -> None:
        frame = np.ones((8, 8, 3), dtype=np.uint8)
        cache = FrameCache(lambda: frame, ttl_ms=0, diff_threshold=0.0)
        cache.get_frame()
        assert cache.frame_changed is True


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestFrameCacheThreadSafety:

    def test_concurrent_access_single_capture(self) -> None:
        """Multiple threads hitting get_frame() within TTL → only one capture."""
        call_count = 0
        lock = threading.Lock()

        def slow_getter() -> np.ndarray:
            nonlocal call_count
            with lock:
                call_count += 1
            time.sleep(0.005)  # simulate slow capture
            return _make_frame(99)

        cache = FrameCache(slow_getter, ttl_ms=500)
        results: list[np.ndarray | None] = [None] * 10
        threads = []
        for i in range(10):
            t = threading.Thread(target=lambda idx=i: results.__setitem__(idx, cache.get_frame()))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Only 1 capture should have happened (all threads within TTL)
        assert call_count == 1
        for r in results:
            assert r is not None
            assert np.array_equal(r, _make_frame(99))

    def test_invalidate_under_contention(self) -> None:
        """invalidate() during concurrent reads doesn't crash."""
        getter = MagicMock(return_value=_make_frame(7))
        cache = FrameCache(getter, ttl_ms=500)
        cache.get_frame()  # prime

        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(50):
                    cache.get_frame()
            except Exception as e:
                errors.append(e)

        def invalidator() -> None:
            try:
                for _ in range(50):
                    cache.invalidate()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads.append(threading.Thread(target=invalidator))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert errors == []


# ---------------------------------------------------------------------------
# Integration-style: session wiring pattern
# ---------------------------------------------------------------------------

class TestFrameCacheWiringPattern:
    """Simulate how session.py wires the cache to multiple subsystems."""

    def test_shared_cache_across_consumers(self) -> None:
        """Two consumers calling get_frame each → only 1 real capture per cycle."""
        getter = MagicMock(return_value=_make_frame(55))
        cache = FrameCache(getter, ttl_ms=500)

        # Simulate healer and combat each calling get_frame
        f_healer = cache.get_frame()
        f_combat = cache.get_frame()
        assert f_healer is f_combat
        getter.assert_called_once()

    def test_sequential_cycles_recapture(self) -> None:
        """After TTL expires, a new cycle gets a fresh frame."""
        frames = [_make_frame(i) for i in range(5)]
        getter = MagicMock(side_effect=frames)
        cache = FrameCache(getter, ttl_ms=10)

        f1 = cache.get_frame()  # cycle 1
        time.sleep(0.015)
        f2 = cache.get_frame()  # cycle 2

        assert f1 is not None
        assert f2 is not None
        assert not np.array_equal(f1, f2)
        assert getter.call_count == 2
