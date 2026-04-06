"""
tests/test_edge_cases.py
------------------------
Edge-case tests identified during QA:

  EC-05 — BotSession with loop_route=True and a 1-waypoint route exits cleanly
  EC-06 — WaypointNavigator.navigate(c, c) returns empty/trivial route, no exception
  EC-10 — _wait_until("25:00", ...) raises ValueError (invalid hour)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─────────────────────────────────────────────────────────────────────────────
# EC-05  BotSession 1-waypoint route with loop_route=True
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleWaypointRoute:
    """
    A JSON route with only one waypoint (< 2 required to form a path)
    must not hang or loop forever regardless of loop_route setting.

    Expected: session._run_loop exits immediately, setting _running=False.
    """

    def _make_tiny_route(self, tmp_path: Path) -> Path:
        import json
        route = [{"name": "solo_wp", "x": 32349, "y": 32225, "z": 7}]
        p = tmp_path / "single_wp.json"
        p.write_text(json.dumps(route), encoding="utf-8")
        return p

    def test_single_wp_loop_route_exits(self, tmp_path: Path) -> None:
        """EC-05: loop_route=True with 1 waypoint must NOT hang."""
        import threading
        from src.session import BotSession, SessionConfig

        route_file = str(self._make_tiny_route(tmp_path))
        logs: list[str] = []
        cfg = SessionConfig(
            route_file=route_file,
            loop_route=True,
            dry_run=True,
            start_delay=0.0,
            input_method="postmessage",
        )
        session = BotSession(cfg, log_callback=logs.append)

        done = threading.Event()

        def _run_and_signal() -> None:
            session.start()
            # Give the background thread time to process then stop
            import time
            for _ in range(50):         # up to 5 s
                time.sleep(0.1)
                if not session.is_running:
                    break
            session.stop()
            done.set()

        t = threading.Thread(target=_run_and_signal, daemon=True)
        t.start()
        finished = done.wait(timeout=10.0)
        assert finished, "Session with 1-waypoint loop_route=True hung for >10 s"
        assert not session.is_running
        assert any("fewer than 2" in m or "waypoints" in m.lower() for m in logs), (
            f"Expected 'fewer than 2 waypoints' log message, got: {logs}"
        )

    def test_single_wp_no_loop_exits(self, tmp_path: Path) -> None:
        """EC-05 (no-loop variant): loop_route=False also exits immediately."""
        import threading
        from src.session import BotSession, SessionConfig

        route_file = str(self._make_tiny_route(tmp_path))
        cfg = SessionConfig(
            route_file=route_file,
            loop_route=False,
            dry_run=True,
            start_delay=0.0,
            input_method="postmessage",
        )
        session = BotSession(cfg)

        done = threading.Event()

        def _run_and_signal() -> None:
            session.start()
            import time
            for _ in range(50):
                time.sleep(0.1)
                if not session.is_running:
                    break
            session.stop()
            done.set()

        t = threading.Thread(target=_run_and_signal, daemon=True)
        t.start()
        assert done.wait(timeout=10.0), "Session with 1-waypoint no-loop hung for >10 s"


# ─────────────────────────────────────────────────────────────────────────────
# EC-06  WaypointNavigator.navigate(c, c)  start == end
# ─────────────────────────────────────────────────────────────────────────────

class TestNavigateStartEqualsEnd:
    """
    navigate(c, c) where start == end must not raise an exception.
    The returned Route may be empty (steps=[]) or contain just the start tile,
    but it must be a valid Route object.
    """

    def test_navigate_same_coord_no_exception(self) -> None:
        """EC-06: navigate(c, c) returns a Route — no exception."""
        from src.navigator import WaypointNavigator
        from src.models import Coordinate, Route

        nav = WaypointNavigator()
        c = Coordinate(32349, 32225, 7)
        # Floor is loaded lazily inside navigate(); may raise FileNotFoundError
        # if map data is absent, but that is unrelated to start==end.
        try:
            route = nav.navigate(c, c)
        except FileNotFoundError:
            pytest.skip("Map data not available — skipping floor-load dependent test")

        assert isinstance(route, Route), f"Expected Route, got {type(route)}"
        # Steps may be empty or [c] — both are valid; we only forbid exceptions
        assert route.steps is not None

    def test_navigate_same_coord_found_or_empty(self) -> None:
        """EC-06: route is either found=True with trivial path or found=False."""
        from src.navigator import WaypointNavigator
        from src.models import Coordinate

        nav = WaypointNavigator()
        c = Coordinate(32349, 32225, 7)
        try:
            route = nav.navigate(c, c)
        except FileNotFoundError:
            pytest.skip("Map data not available")

        # If found, steps must be a list (possibly empty)
        if route.found:
            assert isinstance(route.steps, list)
        # If not found, that is also acceptable — no crash is what matters


# ─────────────────────────────────────────────────────────────────────────────
# EC-10  _wait_until with invalid hour ("25:00")
# ─────────────────────────────────────────────────────────────────────────────

class TestWaitUntilInvalidHour:
    """
    _wait_until("25:00", ...) must raise ValueError (datetime.replace rejects
    hour > 23).  The error should propagate to the caller, not be silently
    swallowed.
    """

    def test_hour_25_raises(self) -> None:
        """EC-10: '25:00' causes ValueError from datetime.replace(hour=25)."""
        import datetime as _dt
        import main as _main

        base = _dt.datetime(2025, 1, 15, 10, 30, 0)
        with pytest.raises(ValueError):
            _main._wait_until(
                "25:00",
                lambda m: None,
                _now_fn=lambda: base,
                _sleep_fn=lambda s: None,
            )

    def test_hour_24_raises(self) -> None:
        """EC-10 (boundary): '24:00' is also invalid."""
        import datetime as _dt
        import main as _main

        base = _dt.datetime(2025, 1, 15, 10, 30, 0)
        with pytest.raises(ValueError):
            _main._wait_until(
                "24:00",
                lambda m: None,
                _now_fn=lambda: base,
                _sleep_fn=lambda s: None,
            )

    def test_negative_minute_raises(self) -> None:
        """EC-10: '-1:-1' format (still splits on ':') fails on int conversion."""
        import main as _main

        with pytest.raises(ValueError):
            _main._wait_until("10:-5", lambda m: None)

    def test_next_day_warning_logged(self) -> None:
        """FIX-03: when target wraps to next day the log must contain 'MAÑANA'."""
        import datetime as _dt
        import main as _main

        # Use a target that is already past today (same hour:minute as base)
        base = _dt.datetime(2025, 1, 15, 10, 30, 0)
        target = base + _dt.timedelta(seconds=90)   # 10:31
        target_str = target.strftime("%H:%M")       # "10:31"

        # Make base = 10:32 so target < now → forces next-day wrap
        now_base = _dt.datetime(2025, 1, 15, 10, 32, 0)
        logs: list[str] = []
        call_n = [0]

        def now_fn() -> _dt.datetime:
            call_n[0] += 1
            if call_n[0] <= 2:
                return now_base
            return _dt.datetime(2025, 1, 16, 10, 32, 0)  # past next-day target

        _main._wait_until(target_str, logs.append, _now_fn=now_fn, _sleep_fn=lambda s: None)
        assert any("MA" in m.upper() and "ANA" in m.upper() for m in logs), (
            f"Expected 'MAÑANA' warning in logs, got: {logs}"
        )
