"""Tests for DepotOrchestrator, SessionPersistence edges, and MouseBezier math.

A7: DepotOrchestrator — run_resupply 5-step flow, _navigate_to, _bank_withdraw
A8: SessionPersistence — is_stale, matches_route, load unknown fields, roundtrip
A9: MouseBezier — pure math validation (Bézier, ease_in_out, bezier_path)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.depot_orchestrator import DepotOrchestrator, ResupplyConfig
from src.session_persistence import SessionCheckpoint
from src.mouse_bezier import (
    _cubic_bezier,
    _ease_in_out,
    _random_control_point,
    bezier_path,
)
from src.models import Coordinate


# ══════════════════════════════════════════════════════════════════════════════
# A7: DepotOrchestrator
# ══════════════════════════════════════════════════════════════════════════════


class TestShouldResupply:
    """should_resupply: cooldown, max limit, inventory delegation."""

    def test_disabled_returns_false(self):
        cfg = ResupplyConfig(enabled=False)
        orch = DepotOrchestrator(config=cfg)
        assert orch.should_resupply() is False

    def test_max_resupply_reached(self):
        cfg = ResupplyConfig(enabled=True, max_resupply_per_session=2)
        orch = DepotOrchestrator(config=cfg)
        orch._resupply_count = 2
        assert orch.should_resupply() is False

    def test_cooldown_prevents_rapid_checks(self):
        cfg = ResupplyConfig(enabled=True, check_interval_s=60)
        orch = DepotOrchestrator(config=cfg)
        orch._last_check_ts = time.monotonic()  # just checked
        assert orch.should_resupply() is False

    def test_inventory_needs_depot_triggers(self):
        cfg = ResupplyConfig(enabled=True, check_interval_s=0)
        mock_inv = MagicMock()
        mock_inv.needs_depot.return_value = True
        mock_inv.last_inventory = None
        orch = DepotOrchestrator(config=cfg, inventory_manager=mock_inv)
        orch._last_check_ts = 0  # bypass cooldown

        assert orch.should_resupply() is True

    def test_inventory_no_need_returns_false(self):
        cfg = ResupplyConfig(enabled=True, check_interval_s=0)
        mock_inv = MagicMock()
        mock_inv.needs_depot.return_value = False
        orch = DepotOrchestrator(config=cfg, inventory_manager=mock_inv)
        orch._last_check_ts = 0

        assert orch.should_resupply() is False

    def test_no_inventory_manager_returns_false(self):
        cfg = ResupplyConfig(enabled=True, check_interval_s=0)
        orch = DepotOrchestrator(config=cfg, inventory_manager=None)
        orch._last_check_ts = 0
        assert orch.should_resupply() is False

    def test_frame_passed_to_inventory(self):
        cfg = ResupplyConfig(enabled=True, check_interval_s=0)
        mock_inv = MagicMock()
        mock_inv.needs_depot.return_value = False
        orch = DepotOrchestrator(config=cfg, inventory_manager=mock_inv)
        orch._last_check_ts = 0

        fake_frame = MagicMock()
        orch.should_resupply(frame=fake_frame)

        mock_inv.check_inventory.assert_called_once_with(fake_frame)
        mock_inv.check_supplies.assert_called_once_with(fake_frame)


class TestRunResupply:
    """run_resupply: 5-step flow with mocked sub-managers."""

    def _make_orch(self, **kw) -> DepotOrchestrator:
        cfg_defaults = dict(
            enabled=True,
            navigate_to_depot=True,
            buy_supplies_after_depot=True,
            bank_withdraw_before_buy=True,
            bank_withdraw_amount=10000,
            depot_coord=[32000, 32000, 7],
            return_coord=[32100, 32100, 7],
        )
        cfg_defaults.update(kw)
        cfg = ResupplyConfig(**cfg_defaults)

        mock_depot = MagicMock()
        mock_depot.run_depot_cycle.return_value = True

        mock_trade = MagicMock()
        mock_trade.run_cycle.return_value = True

        mock_nav = MagicMock()
        route = MagicMock(found=True)
        mock_nav.navigate.return_value = route

        mock_ctrl = MagicMock()
        mock_ctrl.is_connected.return_value = True

        orch = DepotOrchestrator(
            config=cfg,
            depot_manager=mock_depot,
            trade_manager=mock_trade,
            navigator=mock_nav,
            ctrl=mock_ctrl,
            log_fn=lambda msg: None,
        )
        return orch

    def test_full_5_step_flow(self):
        orch = self._make_orch()
        player = Coordinate(32050, 32050, 7)

        with patch('time.sleep'):
            result = orch.run_resupply(player_pos=player, return_pos=None)

        assert result is True
        assert orch._resupply_count == 1
        # All steps executed
        orch._depot.run_depot_cycle.assert_called_once()
        orch._trade.run_cycle.assert_called_once()
        # Step 1 navigates to depot; Step 5 passes player_pos=None so
        # _navigate_to returns early → navigate() called once total.
        assert orch._nav.navigate.call_count == 1

    def test_navigate_skip_when_disabled(self):
        orch = self._make_orch(navigate_to_depot=False)
        player = Coordinate(32050, 32050, 7)

        with patch('time.sleep'):
            result = orch.run_resupply(player_pos=player)

        assert result is True
        # Navigator not called for step 1 (but still called for step 5 return)

    def test_depot_failure_continues(self):
        orch = self._make_orch()
        orch._depot.run_depot_cycle.return_value = False
        player = Coordinate(32050, 32050, 7)

        with patch('time.sleep'):
            result = orch.run_resupply(player_pos=player)

        # Returns False because depot failed
        assert result is False
        # But trade still attempted
        orch._trade.run_cycle.assert_called_once()

    def test_depot_exception_continues(self):
        orch = self._make_orch()
        orch._depot.run_depot_cycle.side_effect = RuntimeError("boom")
        player = Coordinate(32050, 32050, 7)

        with patch('time.sleep'):
            result = orch.run_resupply(player_pos=player)

        assert result is False
        orch._trade.run_cycle.assert_called_once()

    def test_no_managers_completes_gracefully(self):
        cfg = ResupplyConfig(enabled=True)
        orch = DepotOrchestrator(config=cfg, log_fn=lambda msg: None)

        with patch('time.sleep'):
            result = orch.run_resupply(player_pos=None)

        assert result is True
        assert orch._resupply_count == 1

    def test_resupply_count_increments(self):
        orch = self._make_orch()
        player = Coordinate(32050, 32050, 7)

        with patch('time.sleep'):
            orch.run_resupply(player_pos=player)
            orch.run_resupply(player_pos=player)

        assert orch._resupply_count == 2

    def test_abort_on_nav_failure(self):
        orch = self._make_orch(abort_hunt_on_failure=True)
        orch._nav.navigate.return_value = MagicMock(found=False)
        player = Coordinate(32050, 32050, 7)

        with patch('time.sleep'):
            result = orch.run_resupply(player_pos=player)

        assert result is False


class TestNavigateTo:
    """_navigate_to: route finding delegation."""

    def test_no_navigator_returns_false(self):
        orch = DepotOrchestrator(log_fn=lambda m: None)
        assert orch._navigate_to([100, 200, 7]) is False

    def test_no_player_pos_returns_false(self):
        orch = DepotOrchestrator(navigator=MagicMock(), log_fn=lambda m: None)
        assert orch._navigate_to([100, 200, 7], player_pos=None) is False

    def test_invalid_coord_returns_false(self):
        orch = DepotOrchestrator(navigator=MagicMock(), log_fn=lambda m: None)
        assert orch._navigate_to([100], player_pos=Coordinate(0, 0, 7)) is False

    def test_route_found_returns_true(self):
        mock_nav = MagicMock()
        mock_nav.navigate.return_value = MagicMock(found=True)
        orch = DepotOrchestrator(navigator=mock_nav, log_fn=lambda m: None)
        # walk_fn must be set; without it _navigate_to returns False (D2 fix)
        orch.set_walk_fn(lambda route: True)
        player = Coordinate(0, 0, 7)

        result = orch._navigate_to([100, 200, 7], player_pos=player)
        assert result is True

    def test_route_not_found_returns_false(self):
        mock_nav = MagicMock()
        mock_nav.navigate.return_value = MagicMock(found=False)
        orch = DepotOrchestrator(navigator=mock_nav, log_fn=lambda m: None)
        player = Coordinate(0, 0, 7)

        result = orch._navigate_to([100, 200, 7], player_pos=player)
        assert result is False


class TestBankWithdraw:
    """_bank_withdraw: NPC dialogue sequence."""

    def test_sends_hi_withdraw_yes(self):
        mock_ctrl = MagicMock()
        mock_ctrl.is_connected.return_value = True
        cfg = ResupplyConfig(bank_withdraw_amount=5000)
        orch = DepotOrchestrator(config=cfg, ctrl=mock_ctrl, log_fn=lambda m: None)

        with patch('time.sleep'):
            result = orch._bank_withdraw()

        assert result is True
        # 3 type_text calls: "hi", "withdraw 5000", "yes"
        assert mock_ctrl.type_text.call_count == 3
        mock_ctrl.type_text.assert_any_call("hi")
        mock_ctrl.type_text.assert_any_call("withdraw 5000")
        mock_ctrl.type_text.assert_any_call("yes")
        # 3 Enter presses after each message
        assert mock_ctrl.press_key.call_count == 3

    def test_withdraw_all_when_amount_zero(self):
        mock_ctrl = MagicMock()
        mock_ctrl.is_connected.return_value = True
        cfg = ResupplyConfig(bank_withdraw_amount=0)
        orch = DepotOrchestrator(config=cfg, ctrl=mock_ctrl, log_fn=lambda m: None)

        with patch('time.sleep'):
            orch._bank_withdraw()

        mock_ctrl.type_text.assert_any_call("withdraw all")

    def test_no_ctrl_returns_false(self):
        orch = DepotOrchestrator(ctrl=None, log_fn=lambda m: None)
        result = orch._bank_withdraw()
        assert result is False

    def test_disconnected_ctrl_returns_false(self):
        mock_ctrl = MagicMock()
        mock_ctrl.is_connected.return_value = False
        orch = DepotOrchestrator(ctrl=mock_ctrl, log_fn=lambda m: None)
        result = orch._bank_withdraw()
        assert result is False


class TestStatsSnapshot:
    def test_snapshot_contains_expected_keys(self):
        orch = DepotOrchestrator(log_fn=lambda m: None)
        snap = orch.stats_snapshot()
        assert "resupply_count" in snap
        assert "last_resupply_ts" in snap
        assert "last_trigger_reason" in snap
        assert "enabled" in snap


# ══════════════════════════════════════════════════════════════════════════════
# A8: SessionPersistence — edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestSessionCheckpointSaveLoad:
    """Full save/load roundtrip with all fields."""

    def test_save_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "ckpt.json"
        ckpt = SessionCheckpoint(
            route_file="routes/thais.json",
            waypoint_index=5,
            position_x=32000,
            position_y=32100,
            position_z=7,
            routes_completed=3,
            heal_fired=10,
            mana_fired=5,
            loot_events=42,
            uptime_seconds=1234.5,
        )
        ckpt.save(path)

        loaded = SessionCheckpoint.load(path)
        assert loaded is not None
        assert loaded.route_file == "routes/thais.json"
        assert loaded.waypoint_index == 5
        assert loaded.position_x == 32000
        assert loaded.position_z == 7
        assert loaded.routes_completed == 3
        assert loaded.heal_fired == 10
        assert loaded.mana_fired == 5
        assert loaded.loot_events == 42
        assert loaded.uptime_seconds == pytest.approx(1234.5)
        assert loaded.timestamp > 0

    def test_load_nonexistent_returns_none(self, tmp_path: Path):
        result = SessionCheckpoint.load(tmp_path / "missing.json")
        assert result is None

    def test_load_corrupt_json_returns_none(self, tmp_path: Path):
        path = tmp_path / "corrupt.json"
        path.write_text("{{not valid json")
        result = SessionCheckpoint.load(path)
        assert result is None

    def test_clear_deletes_file(self, tmp_path: Path):
        path = tmp_path / "ckpt.json"
        ckpt = SessionCheckpoint()
        ckpt.save(path)
        assert path.exists()

        SessionCheckpoint.clear(path)
        assert not path.exists()

    def test_clear_nonexistent_no_crash(self, tmp_path: Path):
        SessionCheckpoint.clear(tmp_path / "nope.json")  # no crash


class TestSessionCheckpointIsStale:
    """is_stale: age-based staleness check."""

    def test_fresh_checkpoint_not_stale(self):
        ckpt = SessionCheckpoint(timestamp=time.time())
        assert ckpt.is_stale(max_age_seconds=3600) is False

    def test_old_checkpoint_is_stale(self):
        ckpt = SessionCheckpoint(timestamp=time.time() - 7200)
        assert ckpt.is_stale(max_age_seconds=3600) is True

    def test_zero_timestamp_is_stale(self):
        ckpt = SessionCheckpoint(timestamp=0)
        assert ckpt.is_stale() is True

    def test_negative_timestamp_is_stale(self):
        ckpt = SessionCheckpoint(timestamp=-1)
        assert ckpt.is_stale() is True


class TestSessionCheckpointMatchesRoute:
    """matches_route: route file comparison."""

    def test_exact_match(self):
        ckpt = SessionCheckpoint(route_file="routes/thais.json")
        assert ckpt.matches_route("routes/thais.json") is True

    def test_mismatch(self):
        ckpt = SessionCheckpoint(route_file="routes/thais.json")
        assert ckpt.matches_route("routes/venore.json") is False

    def test_empty_strings(self):
        ckpt = SessionCheckpoint(route_file="")
        assert ckpt.matches_route("") is True


class TestSessionCheckpointLoadUnknownFields:
    """load() with extra unknown fields → stored in extra dict."""

    def test_unknown_fields_go_to_extra(self, tmp_path: Path):
        path = tmp_path / "ckpt.json"
        data = {
            "route_file": "r.json",
            "waypoint_index": 3,
            "position_x": 100,
            "position_y": 200,
            "position_z": 7,
            "routes_completed": 0,
            "heal_fired": 0,
            "mana_fired": 0,
            "loot_events": 0,
            "uptime_seconds": 0,
            "timestamp": 0,
            "timestamp_iso": "",
            "extra": {},
            "future_field": "hello",
            "another_field": 42,
        }
        path.write_text(json.dumps(data))

        loaded = SessionCheckpoint.load(path)
        assert loaded is not None
        assert loaded.extra["future_field"] == "hello"
        assert loaded.extra["another_field"] == 42


# ══════════════════════════════════════════════════════════════════════════════
# A9: MouseBezier — pure math
# ══════════════════════════════════════════════════════════════════════════════


class TestCubicBezier:
    """_cubic_bezier: parametric curve evaluation."""

    def test_t0_returns_p0(self):
        p0 = (0.0, 0.0)
        p3 = (100.0, 100.0)
        result = _cubic_bezier(0.0, p0, (30.0, 60.0), (70.0, 40.0), p3)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.0)

    def test_t1_returns_p3(self):
        p0 = (0.0, 0.0)
        p3 = (100.0, 200.0)
        result = _cubic_bezier(1.0, p0, (30.0, 60.0), (70.0, 140.0), p3)
        assert result[0] == pytest.approx(100.0)
        assert result[1] == pytest.approx(200.0)

    def test_t05_between_endpoints(self):
        """At t=0.5, the point should be somewhere between p0 and p3."""
        p0 = (0.0, 0.0)
        p3 = (100.0, 100.0)
        result = _cubic_bezier(0.5, p0, (30.0, 70.0), (70.0, 30.0), p3)
        assert 0 < result[0] < 100
        assert 0 < result[1] < 100

    def test_straight_line_midpoint(self):
        """When control points are on the line, t=0.5 → midpoint."""
        p0 = (0.0, 0.0)
        p3 = (100.0, 0.0)
        # Control points exactly on the line
        p1 = (33.33, 0.0)
        p2 = (66.66, 0.0)
        result = _cubic_bezier(0.5, p0, p1, p2, p3)
        assert result[0] == pytest.approx(50.0, abs=1.0)
        assert result[1] == pytest.approx(0.0, abs=1.0)


class TestEaseInOut:
    """_ease_in_out: smooth-step function."""

    def test_zero(self):
        assert _ease_in_out(0.0) == pytest.approx(0.0)

    def test_one(self):
        assert _ease_in_out(1.0) == pytest.approx(1.0)

    def test_half(self):
        assert _ease_in_out(0.5) == pytest.approx(0.5)

    def test_monotonic(self):
        """The function should be monotonically increasing on [0, 1]."""
        prev = 0.0
        for i in range(101):
            t = i / 100.0
            val = _ease_in_out(t)
            assert val >= prev - 1e-10
            prev = val

    def test_slow_at_start(self):
        """Near t=0, the function should be below the linear value."""
        assert _ease_in_out(0.1) < 0.1

    def test_slow_at_end(self):
        """Near t=1, the function should be above the linear value."""
        assert _ease_in_out(0.9) > 0.9


class TestRandomControlPoint:
    """_random_control_point: control point generation."""

    def test_near_midpoint(self):
        """Generated point should be near the midpoint of start→end."""
        start = (0.0, 0.0)
        end = (100.0, 0.0)
        # Run many times, check average is near midpoint
        xs = []
        for _ in range(100):
            x, y = _random_control_point(start, end, spread=0.2)
            xs.append(x)
        avg_x = sum(xs) / len(xs)
        # Should be roughly in the middle (within 30 of center)
        assert 20 < avg_x < 80

    def test_zero_distance_no_crash(self):
        """Same start and end → no divide by zero."""
        result = _random_control_point((50.0, 50.0), (50.0, 50.0), spread=0.5)
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestBezierPath:
    """bezier_path: full path generation."""

    def test_starts_and_ends_correctly(self):
        start = (100, 200)
        end = (500, 400)
        path = bezier_path(start, end, steps=20)
        assert path[0] == start
        assert path[-1] == end

    def test_correct_number_of_points(self):
        path = bezier_path((0, 0), (100, 100), steps=30)
        assert len(path) == 31  # steps + 1

    def test_auto_steps_scales_with_distance(self):
        short_path = bezier_path((0, 0), (50, 0))
        long_path = bezier_path((0, 0), (500, 0))
        assert len(long_path) > len(short_path)

    def test_short_distance_minimum_10_steps(self):
        path = bezier_path((0, 0), (1, 0))
        assert len(path) >= 11  # at least 10 steps + 1

    def test_long_distance_capped_at_120_steps(self):
        path = bezier_path((0, 0), (10000, 10000))
        assert len(path) <= 121  # max 120 steps + 1

    def test_all_points_are_integer_tuples(self):
        path = bezier_path((100, 200), (500, 400), steps=15)
        for x, y in path:
            assert isinstance(x, int)
            assert isinstance(y, int)

    def test_points_stay_near_path(self):
        """All points should be within a reasonable distance of the line."""
        start = (0, 0)
        end = (100, 0)
        path = bezier_path(start, end, steps=20, spread=0.3)
        for x, y in path:
            # y should be within ~50 of the line (30% spread of 100px)
            assert abs(y) < 60

    def test_same_start_and_end_no_crash(self):
        """Zero-length path should not crash."""
        path = bezier_path((50, 50), (50, 50), steps=10)
        assert path[0] == (50, 50)
        assert path[-1] == (50, 50)
