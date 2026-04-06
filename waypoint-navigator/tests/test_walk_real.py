"""
Tests for ScriptExecutor._walk_to / _execute_segment_steps / _sync_position
with dry_run=False (real navigation path).

All tests are 100% offline — no Tibia, no OBS, no real input.
time.sleep is patched to keep tests fast.
"""
from __future__ import annotations

from typing import Any, List, Optional, cast
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from src.models import Coordinate
from src.script_executor import ScriptExecutor


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _coord(x: int = 100, y: int = 200, z: int = 7) -> Coordinate:
    return Coordinate(x, y, z)


def _seg(steps: List[Coordinate], found: bool = True) -> MagicMock:
    """Fake navigation segment (duck-typed Route)."""
    s = MagicMock()
    s.found = found
    s.steps = steps
    return s


def _make_executor(
    *,
    dry_run: bool = False,
    start_pos: Optional[Coordinate] = None,
    position_getter=None,
    nav: Any = "default",
    step_interval: float = 0.0,
) -> ScriptExecutor:
    ctrl = MagicMock()
    if nav == "default":
        nav = MagicMock()
    ex = ScriptExecutor(
        ctrl=ctrl,
        navigator=nav,
        dry_run=dry_run,
        step_interval=step_interval,
        jitter=0.0,
        log_fn=lambda _: None,
        position_getter=position_getter,
    )
    if start_pos is not None:
        ex._current_pos = start_pos
    return ex


# Patch time.sleep everywhere in script_executor to avoid real waits
_PATCH_SLEEP = patch("src.script_executor.time")


# ─────────────────────────────────────────────────────────────────────────────
# _sync_position
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncPosition:

    def test_no_position_getter_does_nothing(self):
        ex = _make_executor(start_pos=_coord(100, 100))
        ex._sync_position()
        assert ex._current_pos == _coord(100, 100)

    def test_accepts_valid_position(self):
        new_pos = _coord(101, 100)
        ex = _make_executor(
            start_pos=_coord(100, 100),
            position_getter=lambda: new_pos,
        )
        ex._sync_position()
        assert ex._current_pos == new_pos

    def test_rejects_large_jump(self):
        old = _coord(100, 100)
        new_pos = _coord(100 + 50, 100)  # 50 tiles jump > _MAX_SYNC_JUMP=10
        ex = _make_executor(start_pos=old, position_getter=lambda: new_pos)
        ex._sync_position()
        assert ex._current_pos == old  # kept old position

    def test_accepts_small_drift(self):
        old = _coord(100, 100)
        new_pos = _coord(103, 102)  # 3+2=5 tiles, within _MAX_SYNC_JUMP=10
        ex = _make_executor(start_pos=old, position_getter=lambda: new_pos)
        ex._sync_position()
        assert ex._current_pos == new_pos

    def test_position_getter_returns_none(self):
        old = _coord(100, 100)
        ex = _make_executor(start_pos=old, position_getter=lambda: None)
        ex._sync_position()
        assert ex._current_pos == old

    def test_bootstraps_when_current_pos_none(self):
        new_pos = _coord(100, 100)
        ex = _make_executor(position_getter=lambda: new_pos)
        ex._current_pos = None
        ex._sync_position()
        assert ex._current_pos == new_pos


# ─────────────────────────────────────────────────────────────────────────────
# _walk_to — high-level
# ─────────────────────────────────────────────────────────────────────────────

class TestWalkTo:

    def test_skip_if_navigator_none(self):
        ex = _make_executor(nav=None, start_pos=_coord(100, 100))
        ex._walk_to(_coord(103, 100), "node")
        # Should not crash, ctrl never called
        ex._ctrl.move_to_tile.assert_not_called()

    def test_dry_run_updates_current_pos(self):
        dest = _coord(103, 100)
        ex = _make_executor(dry_run=True, start_pos=_coord(100, 100))
        ex._walk_to(dest, "node")
        assert ex._current_pos == dest

    def test_bootstraps_pos_when_none(self):
        """When _current_pos is None, bootstrap to dest (no real pos getter)."""
        dest = _coord(103, 100)
        nav = MagicMock()
        seg = _seg(steps=[dest])  # already there
        nav.navigate.return_value = seg
        ex = _make_executor(nav=nav, step_interval=0.0)
        ex._current_pos = None
        with _PATCH_SLEEP:
            ex._walk_to(dest, "node")
        # Should not crash

    def test_already_at_destination(self):
        """If _current_pos == dest, navigate returns 1-step route, no moves made."""
        dest = _coord(100, 100)
        nav = MagicMock()
        seg = _seg(steps=[dest])  # single-step route = already there
        nav.navigate.return_value = seg
        ex = _make_executor(nav=nav, start_pos=dest, step_interval=0.0)
        with _PATCH_SLEEP:
            ex._walk_to(dest, "node")
        ex._ctrl.move_to_tile.assert_not_called()

    def test_simple_3_tile_walk_east(self):
        """Walk east 3 tiles — move_to_tile(1, 0) called 3 times."""
        src = _coord(100, 100)
        dst = _coord(103, 100)
        steps = [_coord(100 + i, 100) for i in range(4)]  # 4 coords = 3 hops
        nav = MagicMock()
        nav.navigate.return_value = _seg(steps=steps)
        ex = _make_executor(nav=nav, start_pos=src, step_interval=0.0)
        ex._running = True
        with _PATCH_SLEEP:
            ex._walk_to(dst, "node")
        assert ex._ctrl.move_to_tile.call_count == 3
        ex._ctrl.move_to_tile.assert_called_with(1, 0)

    def test_walk_path_not_found_aborts(self):
        """navigate() returns found=False → abort without moving."""
        src = _coord(100, 100)
        dst = _coord(150, 150)  # far away, unreachable
        nav = MagicMock()
        seg = _seg(steps=[], found=False)
        nav.navigate.return_value = seg
        # No loader so snap-to-walkable returns None
        nav.loader = None
        ex = _make_executor(nav=nav, start_pos=src, step_interval=0.0)
        with _PATCH_SLEEP:
            ex._walk_to(dst, "node")
        ex._ctrl.move_to_tile.assert_not_called()

    def test_walk_path_not_found_does_not_snap_to_nonadjacent_start(self):
        src = _coord(100, 100)
        dst = _coord(103, 100)
        nav = MagicMock()
        nav.navigate.return_value = _seg(steps=[], found=False)
        nav.loader = None
        ex = _make_executor(nav=nav, start_pos=src, step_interval=0.0)
        setattr(ex, "_find_nearest_walkable", MagicMock(return_value=_coord(102, 100)))

        with _PATCH_SLEEP:
            ex._walk_to(dst, "node")

        assert nav.navigate.call_count == 1
        assert ex._current_pos == src
        ex._ctrl.move_to_tile.assert_not_called()

    def test_excessive_segment_plan_aborts_before_moving(self):
        src = _coord(100, 100)
        dst = _coord(106, 100)
        detour = [_coord(100 + i, 100) for i in range(26)]
        nav = MagicMock()
        nav.navigate.return_value = _seg(steps=detour)
        ex = _make_executor(nav=nav, start_pos=src, step_interval=0.0)
        ex._running = True

        with _PATCH_SLEEP:
            ex._walk_to(dst, "node")

        ex._ctrl.move_to_tile.assert_not_called()

    def test_dynamic_blocked_tile_is_reused_on_retry(self):
        src = _coord(31750, 30990)
        dst = _coord(31751, 30990)
        ex = _make_executor(start_pos=src, step_interval=0.0)

        class _PF:
            def __init__(self) -> None:
                self.walkability = np.ones((64, 64), dtype=bool)

        pf = _PF()
        blocked_px = dst.x - 31744
        blocked_py = dst.y - 30976

        call_count = [0]

        def navigate_side_effect(start, end):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: blocked pixel must be applied to walkability
                assert bool(pf.walkability[blocked_py, blocked_px]) is False
            return _seg(steps=[], found=False)

        ex._nav.navigate = MagicMock(side_effect=navigate_side_effect)
        setattr(ex, "_get_pathfinder", MagicMock(return_value=pf))
        ex._blocked_pixels.append((blocked_px, blocked_py, dst.z))
        ex._preblocked_count = 0

        with _PATCH_SLEEP:
            ex._walk_to(dst, "node")

        # Decay logic calls navigate twice: first with blocks applied, then without
        assert ex._nav.navigate.call_count >= 1
        ex._nav.navigate.assert_called_with(src, dst)

    def test_walk_stops_when_aborted(self):
        """If _running is cleared mid-walk, steps stop."""
        src = _coord(100, 100)
        dst = _coord(110, 100)
        steps = [_coord(100 + i, 100) for i in range(11)]  # 10 hops
        nav = MagicMock()
        nav.navigate.return_value = _seg(steps=steps)

        call_count = [0]

        def fake_move(dx, dy):
            call_count[0] += 1
            if call_count[0] >= 2:
                ex._running = False  # abort after 2nd move

        ex = _make_executor(nav=nav, start_pos=src, step_interval=0.0)
        ex._ctrl.move_to_tile.side_effect = fake_move
        # Need _running=True before starting walk
        ex._running = True
        with _PATCH_SLEEP:
            ex._walk_to(dst, "node")
        assert ex._ctrl.move_to_tile.call_count <= 3  # stopped early

    def test_walk_north(self):
        """Walk north (y decreases)."""
        src = _coord(100, 100)
        dst = _coord(100, 97)
        steps = [_coord(100, 100 - i) for i in range(4)]
        nav = MagicMock()
        nav.navigate.return_value = _seg(steps=steps)
        ex = _make_executor(nav=nav, start_pos=src, step_interval=0.0)
        ex._running = True
        with _PATCH_SLEEP:
            ex._walk_to(dst, "stand")
        ex._ctrl.move_to_tile.assert_called_with(0, -1)

    def test_multifloor_navigate_called_when_z_differs(self):
        """When src.z != dst.z, navigate_multifloor is used."""
        src = _coord(100, 100, z=7)
        dst = _coord(100, 100, z=6)
        seg = _seg(steps=[src, dst])
        nav = MagicMock()
        nav.navigate_multifloor.return_value = [seg]
        ex = _make_executor(nav=nav, start_pos=src, step_interval=0.0)
        with _PATCH_SLEEP:
            ex._walk_to(dst, "node")
        nav.navigate_multifloor.assert_called_once()
        nav.navigate.assert_not_called()

    def test_multifloor_not_found_aborts(self):
        """navigate_multifloor returns None → walk aborted."""
        src = _coord(100, 100, z=7)
        dst = _coord(200, 200, z=6)
        nav = MagicMock()
        nav.navigate_multifloor.return_value = None
        ex = _make_executor(nav=nav, start_pos=src, step_interval=0.0)
        with _PATCH_SLEEP:
            ex._walk_to(dst, "node")
        ex._ctrl.move_to_tile.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# _execute_segment_steps
# ─────────────────────────────────────────────────────────────────────────────

class TestExecuteSegmentSteps:

    def _exec(self, start_pos: Coordinate, step_interval: float = 0.0) -> ScriptExecutor:
        ex = _make_executor(start_pos=start_pos, step_interval=step_interval)
        ex._running = True
        return ex

    def test_empty_steps_returns_completed(self):
        ex = self._exec(_coord(100, 100))
        completed, blocked = ex._execute_segment_steps([])
        assert completed is True
        assert blocked is None

    def test_single_step_east(self):
        src = _coord(100, 100)
        dst = _coord(101, 100)
        ex = self._exec(src)
        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, dst])
        assert completed is True
        assert blocked is None
        ex._ctrl.move_to_tile.assert_called_once_with(1, 0)

    def test_stops_when_not_running(self):
        src = _coord(100, 100)
        dst = _coord(101, 100)
        ex = self._exec(src)
        ex._running = False  # already stopped
        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, dst])
        assert completed is True  # graceful stop, not a replan
        ex._ctrl.move_to_tile.assert_not_called()

    def test_resume_requested_returns_false(self):
        src = _coord(100, 100)
        dst = _coord(101, 100)
        ex = self._exec(src)
        ex._replan_requested = True
        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, dst])
        assert completed is False
        assert blocked is None
        ex._ctrl.move_to_tile.assert_not_called()

    def test_skips_step_when_already_at_curr(self):
        """If actual position equals the next tile, skip the move."""
        src = _coord(100, 100)
        mid = _coord(101, 100)
        dst = _coord(102, 100)
        ex = self._exec(src)
        # Character is already at mid — step src→mid should be skipped
        ex._current_pos = mid
        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, mid, dst])
        # Only mid→dst is executed (src→mid was skipped)
        assert completed is True
        assert ex._ctrl.move_to_tile.call_count == 1
        ex._ctrl.move_to_tile.assert_called_with(1, 0)

    def test_large_drift_aborts_segment(self):
        """If actual position is far from expected, the segment aborts."""
        src = _coord(100, 100)
        dst = _coord(101, 100)
        # Position getter returns position 20 tiles away (huge drift)
        far_away = _coord(120, 100)
        ex = self._exec(src)
        ex._position_getter = lambda: far_away
        # Sync position first to set current_pos to far_away
        ex._current_pos = far_away
        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, dst])
        # drift = |120-100| + |100-100| = 20 > _DRIFT_THRESHOLD=8
        assert completed is False
        assert blocked is None

    def test_medium_step_drift_aborts_segment(self):
        src = _coord(100, 100)
        dst = _coord(101, 100)
        ex = self._exec(src)
        ex._position_getter = lambda: _coord(103, 101)

        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, dst])

        assert completed is False
        assert blocked is None

    def test_invalid_planned_step_aborts_segment(self):
        src = _coord(100, 100)
        jump = _coord(102, 100)
        ex = self._exec(src)

        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, jump])

        assert completed is False
        assert blocked == jump
        ex._ctrl.move_to_tile.assert_not_called()

    def test_multiple_steps_completed(self):
        """Walk east 5 tiles, all steps complete normally."""
        steps = [_coord(100 + i, 100) for i in range(6)]  # 5 hops
        ex = self._exec(steps[0])
        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps(steps)
        assert completed is True
        assert blocked is None
        assert ex._ctrl.move_to_tile.call_count == 5

    def test_position_confirmed_by_radar(self):
        """When position_getter confirms each step, walk completes cleanly."""
        steps = [_coord(100 + i, 100) for i in range(4)]
        step_idx = [0]

        def pos_getter():
            # Return next position after each move_to_tile call
            i = min(step_idx[0], len(steps) - 1)
            step_idx[0] += 1
            return steps[i]

        ex = self._exec(steps[0])
        ex._position_getter = pos_getter
        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps(steps)
        assert completed is True

    def test_transient_block_retries_then_moves(self):
        src = _coord(100, 100)
        dst = _coord(101, 100)
        ex = self._exec(src)

        readings = [
            _coord(100, 100),
            _coord(100, 100),
            _coord(100, 100),
            _coord(101, 100),
        ]

        def pos_getter():
            if readings:
                return readings.pop(0)
            return _coord(101, 100)

        ex._position_getter = pos_getter
        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, dst])

        assert completed is True
        assert blocked is None
        assert ex._ctrl.move_to_tile.call_count == 4

    def test_persistent_transient_block_still_aborts(self):
        src = _coord(100, 100)
        dst = _coord(101, 100)
        ex = self._exec(src)
        ex._position_getter = lambda: _coord(100, 100)

        with _PATCH_SLEEP:
            completed, blocked = ex._execute_segment_steps([src, dst])

        assert completed is False
        assert blocked == dst
