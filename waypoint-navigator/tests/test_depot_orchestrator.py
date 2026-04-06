"""
Tests for src/depot_orchestrator.py — DepotOrchestrator, ResupplyConfig.
100% offline: no OBS, no Tibia, no hardware.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from src.depot_orchestrator import DepotOrchestrator, ResupplyConfig
from src.models import Coordinate


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _logs() -> list[str]:
    return []


def _make_orchestrator(
    *,
    enabled: bool = True,
    navigate_to_depot: bool = True,
    buy_supplies: bool = True,
    bank_withdraw: bool = False,
    check_interval_s: float = 0.0,
    max_resupply: int = 0,
    depot_coord: list | None = None,
    return_coord: list | None = None,
    abort_on_failure: bool = False,
    depot_manager: object | None = None,
    trade_manager: object | None = None,
    inventory_manager: object | None = None,
    navigator: object | None = None,
    ctrl: object | None = None,
) -> tuple[DepotOrchestrator, list[str]]:
    logs: list[str] = []
    cfg = ResupplyConfig(
        enabled=enabled,
        check_interval_s=check_interval_s,
        depot_coord=depot_coord or [32369, 32241, 7],
        return_coord=return_coord or [],
        navigate_to_depot=navigate_to_depot,
        buy_supplies_after_depot=buy_supplies,
        bank_withdraw_before_buy=bank_withdraw,
        max_resupply_per_session=max_resupply,
        abort_hunt_on_failure=abort_on_failure,
    )
    orch = DepotOrchestrator(
        config=cfg,
        depot_manager=depot_manager,
        trade_manager=trade_manager,
        inventory_manager=inventory_manager,
        navigator=navigator,
        ctrl=ctrl,
        log_fn=logs.append,
    )
    return orch, logs


def _mock_inv(needs: bool = True) -> MagicMock:
    inv = MagicMock()
    inv.needs_depot.return_value = needs
    inv.last_inventory = None
    return inv


def _mock_nav(route_found: bool = True) -> MagicMock:
    nav = MagicMock()
    route = MagicMock()
    route.found = route_found
    nav.navigate.return_value = route
    return nav


def _mock_depot(ok: bool = True) -> MagicMock:
    depot = MagicMock()
    depot.run_depot_cycle.return_value = ok
    return depot


def _mock_trade(ok: bool = True) -> MagicMock:
    trade = MagicMock()
    trade.run_cycle.return_value = ok
    return trade


def _coord() -> Coordinate:
    return Coordinate(32369, 32241, 7)


# ─────────────────────────────────────────────────────────────────────────────
# ResupplyConfig tests
# ─────────────────────────────────────────────────────────────────────────────

class TestResupplyConfig:
    def test_defaults(self):
        cfg = ResupplyConfig()
        assert cfg.enabled is True
        assert cfg.check_interval_s == pytest.approx(30.0)
        assert cfg.navigate_to_depot is True
        assert cfg.buy_supplies_after_depot is True
        assert cfg.bank_withdraw_before_buy is False
        assert cfg.max_resupply_per_session == 0
        assert cfg.abort_hunt_on_failure is False

    def test_custom_values(self):
        cfg = ResupplyConfig(
            enabled=False,
            check_interval_s=10.0,
            depot_coord=[1, 2, 3],
            max_resupply_per_session=5,
        )
        assert cfg.enabled is False
        assert cfg.check_interval_s == pytest.approx(10.0)
        assert cfg.depot_coord == [1, 2, 3]
        assert cfg.max_resupply_per_session == 5


# ─────────────────────────────────────────────────────────────────────────────
# should_resupply tests
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldResupply:
    def test_returns_false_when_disabled(self):
        orch, _ = _make_orchestrator(enabled=False, inventory_manager=_mock_inv(True))
        assert orch.should_resupply() is False

    def test_returns_false_without_inventory_manager(self):
        orch, logs = _make_orchestrator(inventory_manager=None)
        # Force cooldown to 0 so interval check passes
        orch._last_check_ts = 0.0
        result = orch.should_resupply()
        assert result is False
        assert any("InventoryManager" in m for m in logs)

    def test_returns_false_during_cooldown(self):
        orch, _ = _make_orchestrator(
            check_interval_s=60.0,
            inventory_manager=_mock_inv(True),
        )
        orch._last_check_ts = time.monotonic()  # just checked
        assert orch.should_resupply() is False

    def test_returns_true_when_needs_depot(self):
        orch, _ = _make_orchestrator(
            check_interval_s=0.0,
            inventory_manager=_mock_inv(True),
        )
        orch._last_check_ts = 0.0
        assert orch.should_resupply() is True

    def test_returns_false_when_no_need(self):
        orch, _ = _make_orchestrator(
            check_interval_s=0.0,
            inventory_manager=_mock_inv(False),
        )
        orch._last_check_ts = 0.0
        assert orch.should_resupply() is False

    def test_respects_max_resupply_per_session(self):
        inv = _mock_inv(True)
        orch, _ = _make_orchestrator(
            check_interval_s=0.0,
            max_resupply=2,
            inventory_manager=inv,
        )
        orch._resupply_count = 2
        orch._last_check_ts = 0.0
        assert orch.should_resupply() is False

    def test_calls_inventory_checks_when_frame_provided(self):
        inv = _mock_inv(True)
        orch, _ = _make_orchestrator(check_interval_s=0.0, inventory_manager=inv)
        orch._last_check_ts = 0.0
        frame = object()
        orch.should_resupply(frame)
        inv.check_inventory.assert_called_once_with(frame)
        inv.check_supplies.assert_called_once_with(frame)

    def test_updates_last_check_ts_after_check(self):
        inv = _mock_inv(False)
        orch, _ = _make_orchestrator(check_interval_s=0.0, inventory_manager=inv)
        orch._last_check_ts = 0.0
        before = time.monotonic()
        orch.should_resupply()
        assert orch._last_check_ts >= before

    def test_trigger_reason_supplies_low(self):
        inv = _mock_inv(True)
        inv.last_inventory = None
        orch, _ = _make_orchestrator(check_interval_s=0.0, inventory_manager=inv)
        orch._last_check_ts = 0.0
        orch.should_resupply()
        assert orch.last_trigger_reason == "supplies_low"

    def test_trigger_reason_inventory_full(self):
        from src.inventory_manager import InventoryStatus, InventoryReading
        inv = _mock_inv(True)
        reading = MagicMock()
        reading.status = InventoryStatus.FULL
        inv.last_inventory = reading
        orch, _ = _make_orchestrator(check_interval_s=0.0, inventory_manager=inv)
        orch._last_check_ts = 0.0
        orch.should_resupply()
        assert orch.last_trigger_reason == "inventory_full"

    def test_handles_inventory_check_exception(self):
        inv = _mock_inv(False)
        inv.check_inventory.side_effect = RuntimeError("boom")
        orch, logs = _make_orchestrator(check_interval_s=0.0, inventory_manager=inv)
        orch._last_check_ts = 0.0
        # Should not raise even if check_inventory raises
        result = orch.should_resupply(frame=object())
        assert result is False
        assert any("Inventory check error" in m for m in logs)


# ─────────────────────────────────────────────────────────────────────────────
# run_resupply tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRunResupply:
    def test_full_cycle_success(self):
        nav = _mock_nav(route_found=True)
        depot = _mock_depot(ok=True)
        trade = _mock_trade(ok=True)
        walk_fn = MagicMock(return_value=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=True,
            depot_manager=depot,
            trade_manager=trade,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)

        result = orch.run_resupply(
            player_pos=_coord(),
            return_pos=_coord(),
        )

        assert result is True
        assert orch.resupply_count == 1
        depot.run_depot_cycle.assert_called_once()
        trade.run_cycle.assert_called_once()
        walk_fn.assert_called()

    def test_navigation_failure_continues_by_default(self):
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=False)  # walk fails
        depot = _mock_depot(ok=True)
        trade = _mock_trade(ok=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=True,
            abort_on_failure=False,
            depot_manager=depot,
            trade_manager=trade,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)

        result = orch.run_resupply(player_pos=_coord(), return_pos=_coord())
        # Still completes depot + trade even after nav failure
        depot.run_depot_cycle.assert_called()
        trade.run_cycle.assert_called()

    def test_navigation_failure_aborts_when_configured(self):
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=False)  # walk fails
        depot = _mock_depot(ok=True)
        trade = _mock_trade(ok=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=True,
            abort_on_failure=True,
            depot_manager=depot,
            trade_manager=trade,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)

        result = orch.run_resupply(player_pos=_coord(), return_pos=_coord())
        assert result is False
        depot.run_depot_cycle.assert_not_called()

    def test_depot_cycle_failure_marks_partial(self):
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=True)
        depot = _mock_depot(ok=False)  # depot returns False
        trade = _mock_trade(ok=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=True,
            depot_manager=depot,
            trade_manager=trade,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)
        result = orch.run_resupply(player_pos=_coord(), return_pos=_coord())

        assert result is False
        assert orch.resupply_count == 0  # failed resupply not counted

    def test_depot_cycle_exception_marks_partial(self):
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=True)
        depot = MagicMock()
        depot.run_depot_cycle.side_effect = RuntimeError("depot crashed")

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=False,
            depot_manager=depot,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)
        result = orch.run_resupply(player_pos=_coord(), return_pos=_coord())
        assert result is False
        assert any("Depot cycle error" in m for m in logs)

    def test_trade_cycle_exception_does_not_affect_success(self):
        """Trade failure should be logged but not affect the success flag since
        depot completed successfully."""
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=True)
        depot = _mock_depot(ok=True)
        trade = MagicMock()
        trade.run_cycle.side_effect = RuntimeError("trade boom")

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=True,
            depot_manager=depot,
            trade_manager=trade,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)
        result = orch.run_resupply(player_pos=_coord(), return_pos=_coord())
        # depot succeeded, trade raised — cycle counted anyway
        assert orch.resupply_count == 1
        assert any("Trade cycle error" in m for m in logs)

    def test_skips_navigation_when_not_configured(self):
        depot = _mock_depot(ok=True)
        trade = _mock_trade(ok=True)
        nav = _mock_nav()

        orch, logs = _make_orchestrator(
            navigate_to_depot=False,
            buy_supplies=True,
            depot_manager=depot,
            trade_manager=trade,
            navigator=nav,
        )
        result = orch.run_resupply(player_pos=_coord())
        nav.navigate.assert_not_called()
        depot.run_depot_cycle.assert_called_once()
        assert result is True

    def test_skips_trade_when_not_configured(self):
        depot = _mock_depot(ok=True)
        trade = _mock_trade(ok=True)
        nav = _mock_nav()
        walk_fn = MagicMock(return_value=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=False,
            depot_manager=depot,
            trade_manager=trade,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)
        orch.run_resupply(player_pos=_coord(), return_pos=_coord())
        trade.run_cycle.assert_not_called()

    def test_emits_resupply_complete_event(self):
        depot = _mock_depot(ok=True)
        bus = MagicMock()
        orch, _ = _make_orchestrator(
            navigate_to_depot=False,
            buy_supplies=False,
            depot_manager=depot,
        )
        orch.set_event_bus(bus)
        orch.run_resupply()
        bus.emit.assert_called_with("resupply_complete", pytest.approx({
            "count": 1,
            "success": True,
            "elapsed_s": pytest.approx(0.0, abs=5.0),
        }, abs=1))

    def test_return_coord_from_config_used_when_no_return_pos(self):
        """When return_pos is None, fall back to config.return_coord for step 5."""
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=True)
        depot = _mock_depot(ok=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=False,
            return_coord=[32343, 32211, 7],
            depot_manager=depot,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)
        orch.run_resupply(player_pos=_coord(), return_pos=None)
        # Step 5 "Navigating back" log should appear (even if walk fails due to no player pos)
        assert any("Navigating back" in m for m in logs)

    def test_no_route_found_does_not_walk(self):
        nav = _mock_nav(route_found=False)
        walk_fn = MagicMock(return_value=True)
        depot = _mock_depot(ok=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=False,
            abort_on_failure=False,
            depot_manager=depot,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)
        orch.run_resupply(player_pos=_coord(), return_pos=None)
        walk_fn.assert_not_called()

    def test_no_player_pos_skips_navigation(self):
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=True)
        depot = _mock_depot(ok=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=True,
            buy_supplies=False,
            depot_manager=depot,
            navigator=nav,
        )
        orch.set_walk_fn(walk_fn)
        # No player_pos — navigation should be skipped
        orch.run_resupply(player_pos=None, return_pos=None)
        walk_fn.assert_not_called()
        assert any("No player position" in m for m in logs)


# ─────────────────────────────────────────────────────────────────────────────
# _navigate_to tests
# ─────────────────────────────────────────────────────────────────────────────

class TestNavigateTo:
    def test_returns_false_without_navigator(self):
        orch, _ = _make_orchestrator(navigate_to_depot=True)
        result = orch._navigate_to([32369, 32241, 7], player_pos=_coord())
        assert result is False

    def test_returns_false_with_short_coord(self):
        orch, _ = _make_orchestrator(navigator=_mock_nav())
        result = orch._navigate_to([32369, 32241], player_pos=_coord())
        assert result is False

    def test_returns_false_without_walk_fn(self):
        nav = _mock_nav(route_found=True)
        orch, logs = _make_orchestrator(navigator=nav)
        result = orch._navigate_to([32369, 32241, 7], player_pos=_coord())
        assert result is False
        assert any("No walk_fn" in m for m in logs)

    def test_returns_true_on_success(self):
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=True)
        orch, _ = _make_orchestrator(navigator=nav)
        orch.set_walk_fn(walk_fn)
        result = orch._navigate_to([32369, 32241, 7], player_pos=_coord())
        assert result is True

    def test_returns_false_when_walk_fn_fails(self):
        nav = _mock_nav(route_found=True)
        walk_fn = MagicMock(return_value=False)
        orch, _ = _make_orchestrator(navigator=nav)
        orch.set_walk_fn(walk_fn)
        result = orch._navigate_to([32369, 32241, 7], player_pos=_coord())
        assert result is False

    def test_handles_navigate_exception(self):
        nav = MagicMock()
        nav.navigate.side_effect = RuntimeError("nav boom")
        walk_fn = MagicMock(return_value=True)
        orch, logs = _make_orchestrator(navigator=nav)
        orch.set_walk_fn(walk_fn)
        result = orch._navigate_to([32369, 32241, 7], player_pos=_coord())
        assert result is False
        assert any("Navigation error" in m for m in logs)


# ─────────────────────────────────────────────────────────────────────────────
# _bank_withdraw tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBankWithdraw:
    def _mock_ctrl_connected(self) -> MagicMock:
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        ctrl.type_text = MagicMock()
        ctrl.press_key = MagicMock()
        return ctrl

    def test_skips_when_no_ctrl(self):
        orch, logs = _make_orchestrator(ctrl=None, bank_withdraw=True)
        result = orch._bank_withdraw()
        assert result is False
        assert any("No controller" in m for m in logs)

    def test_skips_when_ctrl_disconnected(self):
        ctrl = MagicMock()
        ctrl.is_connected.return_value = False
        orch, logs = _make_orchestrator(ctrl=ctrl, bank_withdraw=True)
        result = orch._bank_withdraw()
        assert result is False

    @patch("time.sleep")
    @patch("random.uniform", return_value=0.5)
    def test_sends_dialogue_sequence(self, mock_rnd, mock_sleep):
        ctrl = self._mock_ctrl_connected()
        orch, _ = _make_orchestrator(ctrl=ctrl, bank_withdraw=True)
        result = orch._bank_withdraw()
        assert result is True
        # Should call type_text three times: hi, withdraw all, yes
        assert ctrl.type_text.call_count == 3

    @patch("time.sleep")
    @patch("random.uniform", return_value=0.5)
    def test_uses_specific_amount_when_configured(self, mock_rnd, mock_sleep):
        ctrl = self._mock_ctrl_connected()
        orch, _ = _make_orchestrator(ctrl=ctrl, bank_withdraw=True)
        orch._cfg.bank_withdraw_amount = 50000
        orch._bank_withdraw()
        calls = [c.args[0] for c in ctrl.type_text.call_args_list]
        assert any("withdraw 50000" in msg for msg in calls)

    @patch("time.sleep")
    @patch("random.uniform", return_value=0.5)
    def test_uses_withdraw_all_when_amount_zero(self, mock_rnd, mock_sleep):
        ctrl = self._mock_ctrl_connected()
        orch, _ = _make_orchestrator(ctrl=ctrl, bank_withdraw=True)
        orch._cfg.bank_withdraw_amount = 0
        orch._bank_withdraw()
        calls = [c.args[0] for c in ctrl.type_text.call_args_list]
        assert any("withdraw all" in msg for msg in calls)


# ─────────────────────────────────────────────────────────────────────────────
# _send_npc_dialogue tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSendNpcDialogue:
    @patch("time.sleep")
    @patch("random.uniform", return_value=0.5)
    def test_sends_all_messages(self, mock_rnd, mock_sleep):
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        orch, _ = _make_orchestrator(ctrl=ctrl)
        result = orch._send_npc_dialogue(["hi", "buy", "yes"], delay=0.1)
        assert result is True
        assert ctrl.type_text.call_count == 3

    @patch("time.sleep")
    @patch("random.uniform", return_value=0.5)
    def test_aborts_on_disconnect_mid_dialogue(self, mock_rnd, mock_sleep):
        ctrl = MagicMock()
        ctrl.is_connected.side_effect = [True, True, False]  # disconnects on 3rd msg
        orch, logs = _make_orchestrator(ctrl=ctrl)
        result = orch._send_npc_dialogue(["hi", "buy", "yes"], delay=0.1)
        assert result is False

    def test_returns_false_without_type_text(self):
        ctrl = MagicMock(spec=[])  # no type_text attribute
        orch, _ = _make_orchestrator(ctrl=ctrl)
        result = orch._send_npc_dialogue(["hi"], delay=0.1)
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# stats_snapshot / properties
# ─────────────────────────────────────────────────────────────────────────────

class TestStatsAndProperties:
    def test_stats_snapshot_initial(self):
        orch, _ = _make_orchestrator()
        snap = orch.stats_snapshot()
        assert snap["resupply_count"] == 0
        assert snap["enabled"] is True
        assert snap["last_trigger_reason"] == ""

    def test_resupply_count_property(self):
        orch, _ = _make_orchestrator()
        orch._resupply_count = 5
        assert orch.resupply_count == 5

    def test_last_trigger_reason_property(self):
        orch, _ = _make_orchestrator()
        orch._last_trigger_reason = "inventory_full"
        assert orch.last_trigger_reason == "inventory_full"

    def test_set_walk_fn(self):
        orch, _ = _make_orchestrator()
        fn = lambda r: True
        orch.set_walk_fn(fn)
        assert orch._walk_fn is fn

    def test_set_event_bus(self):
        orch, _ = _make_orchestrator()
        bus = MagicMock()
        orch.set_event_bus(bus)
        assert orch._event_bus is bus

    def test_set_frame_getter(self):
        orch, _ = _make_orchestrator()
        getter = lambda: None
        orch.set_frame_getter(getter)
        assert orch._frame_getter is getter

    def test_emit_with_no_event_bus(self):
        orch, _ = _make_orchestrator()
        # Should not raise
        orch._emit("test_event", {"data": 1})

    def test_emit_with_event_bus(self):
        orch, _ = _make_orchestrator()
        bus = MagicMock()
        orch.set_event_bus(bus)
        orch._emit("test_event", {"data": 1})
        bus.emit.assert_called_once_with("test_event", {"data": 1})

    def test_emit_handles_bus_exception(self):
        orch, _ = _make_orchestrator()
        bus = MagicMock()
        bus.emit.side_effect = RuntimeError("bus error")
        orch.set_event_bus(bus)
        # Should not raise
        orch._emit("test_event", {})


# ─────────────────────────────────────────────────────────────────────────────
# Bank withdraw via run_resupply integration
# ─────────────────────────────────────────────────────────────────────────────

class TestBankWithdrawIntegration:
    @patch("time.sleep")
    @patch("random.uniform", return_value=0.5)
    def test_bank_withdraw_called_in_full_cycle(self, mock_rnd, mock_sleep):
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        depot = _mock_depot(ok=True)

        orch, logs = _make_orchestrator(
            navigate_to_depot=False,
            buy_supplies=False,
            bank_withdraw=True,
            depot_manager=depot,
            ctrl=ctrl,
        )
        orch.run_resupply()
        # type_text should be called (hi, withdraw all, yes)
        assert ctrl.type_text.called

    @patch("time.sleep")
    @patch("random.uniform", return_value=0.5)
    def test_bank_withdraw_skipped_when_not_configured(self, mock_rnd, mock_sleep):
        ctrl = MagicMock()
        ctrl.is_connected.return_value = True
        depot = _mock_depot(ok=True)

        orch, _ = _make_orchestrator(
            navigate_to_depot=False,
            buy_supplies=False,
            bank_withdraw=False,
            depot_manager=depot,
            ctrl=ctrl,
        )
        orch.run_resupply()
        ctrl.type_text.assert_not_called()
