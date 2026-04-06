"""
src/depot_orchestrator.py — Depot cycle orchestration.

Ties InventoryManager (supply/capacity checks) with DepotManager (depot cycle)
and TradeManager (NPC buy/sell) into a single resupply workflow:

  1. Periodically polls InventoryManager.needs_depot()
  2. When triggered: navigate → depot → deposit → (bank) → (buy supplies) → return
  3. Resumes the hunt route from the last waypoint.

Usage in BotSession::

    orch = DepotOrchestrator(depot_mgr, trade_mgr, inv_mgr, navigator, ctrl)
    # In the route loop, after each waypoint segment:
    if orch.should_resupply(frame):
        orch.run_resupply(player_pos, return_pos)
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("wn.do")


# ── State machine types ───────────────────────────────────────────────────────

class ResupplyStep(Enum):
    IDLE = auto()
    NAVIGATING = auto()
    DEPOSITING = auto()
    BANKING = auto()
    BUYING = auto()
    RETURNING = auto()
    DONE = auto()
    FAILED = auto()


@dataclass
class StepResult:
    success: bool
    recoverable: bool          # True = can retry / continue; False = abort cycle
    step: ResupplyStep
    details: str = ""
    exception: Optional[Exception] = None


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class ResupplyConfig:
    """
    Configuration for the automated resupply cycle.

    Parameters
    ----------
    enabled : bool
        Master switch.
    check_interval_s : float
        Minimum seconds between supply checks during hunting.
    depot_coord : List[int]
        [x, y, z] world coordinate of the depot.
    return_coord : List[int]
        [x, y, z] world coordinate to return to after resupply.
        If empty, returns to the position where the resupply was triggered.
    navigate_to_depot : bool
        If True, use the navigator to walk to the depot.
        If False, assume the bot is already at the depot (post-route mode).
    buy_supplies_after_depot : bool
        If True, run TradeManager.run_cycle() after depositing.
    bank_withdraw_before_buy : bool
        If True, withdraw gold from bank NPC before buying supplies.
    bank_withdraw_amount : int
        Amount to withdraw (0 = withdraw all / "withdraw all").
    max_resupply_per_session : int
        Maximum number of resupply cycles per session (0 = unlimited).
    abort_hunt_on_failure : bool
        If True, stop the hunting session if resupply fails.
    """

    enabled: bool = True
    check_interval_s: float = 30.0
    depot_coord: List[int] = field(default_factory=list)
    return_coord: List[int] = field(default_factory=list)
    navigate_to_depot: bool = True
    buy_supplies_after_depot: bool = True
    bank_withdraw_before_buy: bool = False
    bank_withdraw_amount: int = 0
    max_resupply_per_session: int = 0
    abort_hunt_on_failure: bool = False


# ── Orchestrator ──────────────────────────────────────────────────────────────

class DepotOrchestrator:
    """
    Orchestrates the full resupply cycle via an explicit state machine:

        IDLE → NAVIGATING → DEPOSITING → BANKING → BUYING → RETURNING → DONE
                                                                       ↘ FAILED

    All sub-managers (depot, trade, inventory, navigator) are optional;
    the orchestrator gracefully skips unavailable steps.
    """

    def __init__(
        self,
        config: Optional[ResupplyConfig] = None,
        depot_manager: Any = None,       # DepotManager
        trade_manager: Any = None,        # TradeManager
        inventory_manager: Any = None,    # InventoryManager
        navigator: Any = None,            # Navigator
        ctrl: Any = None,                 # InputController
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cfg = config or ResupplyConfig()
        self._depot = depot_manager
        self._trade = trade_manager
        self._inv = inventory_manager
        self._nav = navigator
        self._ctrl = ctrl
        self._log_fn = log_fn or (lambda msg: _log.info(msg))

        # Callback: (route) -> bool — walks a Route object tile-by-tile
        self._walk_fn: Optional[Callable[[Any], bool]] = None
        self._event_bus: Optional[Any] = None
        self._frame_getter: Optional[Callable[[], Any]] = None

        # State machine
        self._current_step: ResupplyStep = ResupplyStep.IDLE

        # Stats
        self._last_check_ts: float = 0.0
        self._resupply_count: int = 0
        self._last_resupply_ts: float = 0.0
        self._last_trigger_reason: str = ""

    # ── Configuration ────────────────────────────────────────────────────

    def set_walk_fn(self, fn: Callable[[Any], bool]) -> None:
        """Register ``(route) -> bool`` to execute a route tile-by-tile."""
        self._walk_fn = fn

    def set_event_bus(self, bus: Any) -> None:
        """Register EventBus for ``resupply_complete`` events."""
        self._event_bus = bus

    def set_frame_getter(self, fn: Callable[[], Any]) -> None:
        """Register a frame getter for NPC dialog verification."""
        self._frame_getter = fn

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def current_step(self) -> ResupplyStep:
        """Current state machine step (for observability)."""
        return self._current_step

    def should_resupply(self, frame: Any = None) -> bool:
        """
        Check if a resupply cycle should be triggered.

        Checks:
        1. Cooldown (check_interval_s)
        2. Max resupply count
        3. InventoryManager.needs_depot()

        Parameters
        ----------
        frame : ndarray, optional
            Current game frame for supply detection.

        Returns
        -------
        bool
        """
        if not self._cfg.enabled:
            return False

        # Respect max resupply limit
        if (
            self._cfg.max_resupply_per_session > 0
            and self._resupply_count >= self._cfg.max_resupply_per_session
        ):
            return False

        # Cooldown between checks
        now = time.monotonic()
        if now - self._last_check_ts < self._cfg.check_interval_s:
            return False

        # Delegate to InventoryManager
        if self._inv is None:
            self._log("  [R] ⚠ should_resupply: InventoryManager no configurado — resupply nunca se disparará. Registra set_inventory_manager().")
            return False

        # Run a fresh check if a frame is available.
        # Only advance _last_check_ts after a successful check so a crash here
        # doesn't silence resupply for a full check_interval_s period.
        if frame is not None:
            try:
                self._inv.check_inventory(frame)
                self._inv.check_supplies(frame)
            except Exception as exc:
                self._log(f"  [R] Inventory check error: {exc!r}")
        self._last_check_ts = now

        if self._inv.needs_depot():
            last_inv = self._inv.last_inventory
            if last_inv is not None:
                from src.inventory_manager import InventoryStatus
                if last_inv.status == InventoryStatus.FULL:
                    self._last_trigger_reason = "inventory_full"
                else:
                    self._last_trigger_reason = "supplies_low"
            else:
                self._last_trigger_reason = "supplies_low"
            self._log(
                f"  [R] Trigger: {self._last_trigger_reason}"
            )
            return True

        return False

    def run_resupply(
        self,
        player_pos: Any = None,
        return_pos: Any = None,
    ) -> bool:
        """
        Execute the full resupply flow via the state machine.

        Steps:
        1. Navigate to depot (if navigate_to_depot=True and navigator available)
        2. Run DepotManager.run_depot_cycle()
        3. Bank withdraw (if configured)
        4. Buy supplies via TradeManager (if configured)
        5. Navigate back to return_pos

        Parameters
        ----------
        player_pos : Coordinate, optional
            Current player position.
        return_pos : Coordinate, optional
            Position to return to after resupply.
            Falls back to ResupplyConfig.return_coord, then player_pos.

        Returns
        -------
        bool
            True if the resupply completed successfully.
        """
        self._log("  [R] ═══ Starting resupply cycle ═══")
        start_time = time.monotonic()

        self._current_step = ResupplyStep.IDLE
        result = self._run_resupply_cycle(player_pos=player_pos, return_pos=return_pos)
        success = result.success

        end_time = time.monotonic()
        elapsed = end_time - start_time

        # Only count successful resupplies toward max_resupply_per_session.
        # A failed cycle must not consume the session budget.
        if success:
            self._resupply_count += 1
        self._last_resupply_ts = end_time
        self._log(
            f"  [R] ═══ Resupply #{self._resupply_count} done "
            f"({elapsed:.1f}s) {'✓' if success else '⚠ partial'} ═══"
        )
        self._emit("resupply_complete", {
            "count": self._resupply_count,
            "success": success,
            "elapsed_s": round(elapsed, 2),
        })
        return success

    # ── State machine ────────────────────────────────────────────────────

    def _run_resupply_cycle(
        self,
        player_pos: Any = None,
        return_pos: Any = None,
    ) -> StepResult:
        """
        Execute all resupply steps in sequence, advancing _current_step.

        Transition table:
            IDLE → NAVIGATING → DEPOSITING → BANKING → BUYING → RETURNING → DONE
                                                                           ↘ FAILED (only if abort_hunt_on_failure=True and nav fails)

        Navigation failure with abort_hunt_on_failure=True → FAILED immediately.
        Navigation failure with abort_hunt_on_failure=False → continue (recoverable).
        Deposit failure → result=False but cycle continues (banking/buying/returning still run).
        Bank/Trade failure → non-fatal, logged, cycle continues.

        This means the cycle reports overall success only if deposit succeeded.
        Banking, buying, and return failures never affect the overall success flag.
        """
        overall_success = True

        # Step 1: Navigate to depot
        self._current_step = ResupplyStep.NAVIGATING
        nav_result = self._step_navigate(player_pos)
        if not nav_result.success and not nav_result.recoverable:
            self._current_step = ResupplyStep.FAILED
            return nav_result

        # Step 2: Deposit loot — failure marks result False but cycle continues
        self._current_step = ResupplyStep.DEPOSITING
        deposit_result = self._step_deposit(player_pos)
        if not deposit_result.success:
            overall_success = False

        # Step 3: Bank withdraw (non-fatal — failure logged, cycle continues)
        self._current_step = ResupplyStep.BANKING
        self._step_bank()

        # Step 4: Buy supplies (non-fatal — failure logged, cycle continues)
        self._current_step = ResupplyStep.BUYING
        self._step_buy()

        # Step 5: Navigate back
        self._current_step = ResupplyStep.RETURNING
        self._step_return(player_pos=player_pos, return_pos=return_pos)

        if overall_success:
            self._current_step = ResupplyStep.DONE
            return StepResult(
                success=True,
                recoverable=True,
                step=ResupplyStep.DONE,
                details="Resupply cycle completed",
            )
        else:
            self._current_step = ResupplyStep.FAILED
            return StepResult(
                success=False,
                recoverable=True,
                step=ResupplyStep.FAILED,
                details="Resupply cycle completed with deposit failure",
            )

    def _step_navigate(self, player_pos: Any) -> StepResult:
        """
        Step 1: Navigate to the depot.

        Returns recoverable=False only when abort_hunt_on_failure=True and
        navigation failed. Otherwise navigation failure is treated as
        recoverable (cycle continues — maybe we're already at depot).
        """
        if not (self._cfg.navigate_to_depot and self._nav is not None and self._cfg.depot_coord):
            self._log("  [R] Step 1: Skip navigation (not configured)")
            return StepResult(
                success=True, recoverable=True, step=ResupplyStep.NAVIGATING,
                details="navigation skipped",
            )

        self._log("  [R] Step 1: Navigating to depot…")
        nav_ok = self._navigate_to(self._cfg.depot_coord, player_pos)
        if nav_ok:
            return StepResult(
                success=True, recoverable=True, step=ResupplyStep.NAVIGATING,
            )

        self._log("  [R] ⚠ Navigation to depot failed")
        if self._cfg.abort_hunt_on_failure:
            return StepResult(
                success=False,
                recoverable=False,
                step=ResupplyStep.NAVIGATING,
                details="navigation failed; abort_hunt_on_failure=True",
            )
        # Continue anyway — maybe we're already close
        return StepResult(
            success=True, recoverable=True, step=ResupplyStep.NAVIGATING,
            details="navigation failed but continuing (abort_hunt_on_failure=False)",
        )

    def _step_deposit(self, player_pos: Any) -> StepResult:
        """
        Step 2: Run the depot cycle (deposit loot).

        Failure sets overall_success=False but does NOT abort the cycle —
        subsequent steps (bank, buy, return) still execute.
        """
        if self._depot is None:
            self._log("  [R] Step 2: Skip depot (no DepotManager)")
            return StepResult(
                success=True, recoverable=True, step=ResupplyStep.DEPOSITING,
                details="depot skipped (no manager)",
            )

        self._log("  [R] Step 2: Running depot cycle…")
        try:
            depot_ok = self._depot.run_depot_cycle(player_pos=player_pos)
            if depot_ok:
                return StepResult(
                    success=True, recoverable=True, step=ResupplyStep.DEPOSITING,
                )
            self._log("  [R] ⚠ Depot cycle returned False")
            return StepResult(
                success=False,
                recoverable=True,
                step=ResupplyStep.DEPOSITING,
                details="depot cycle returned False",
            )
        except Exception as exc:
            self._log(f"  [R] ⚠ Depot cycle error: {exc!r}")
            return StepResult(
                success=False,
                recoverable=True,
                step=ResupplyStep.DEPOSITING,
                details=f"depot cycle raised: {exc!r}",
                exception=exc,
            )

    def _step_bank(self) -> StepResult:
        """
        Step 3: Bank withdraw (non-fatal).

        Failure is logged but does not abort the cycle.
        """
        if not (self._cfg.bank_withdraw_before_buy and self._depot is not None):
            return StepResult(
                success=True, recoverable=True, step=ResupplyStep.BANKING,
                details="bank withdraw skipped",
            )

        self._log("  [R] Step 3: Bank withdraw…")
        try:
            ok = self._bank_withdraw()
            return StepResult(
                success=ok, recoverable=True, step=ResupplyStep.BANKING,
                details="bank withdraw complete" if ok else "bank withdraw returned False",
            )
        except Exception as exc:
            self._log(f"  [R] ⚠ Bank withdraw error: {exc!r}")
            return StepResult(
                success=False, recoverable=True, step=ResupplyStep.BANKING,
                details=f"bank withdraw raised: {exc!r}",
                exception=exc,
            )

    def _step_buy(self) -> StepResult:
        """
        Step 4: Buy supplies via TradeManager (non-fatal).

        Failure is logged but does not abort the cycle.
        """
        if not (self._cfg.buy_supplies_after_depot and self._trade is not None):
            return StepResult(
                success=True, recoverable=True, step=ResupplyStep.BUYING,
                details="trade skipped",
            )

        self._log("  [R] Step 4: Buying supplies…")
        try:
            trade_ok = self._trade.run_cycle()
            if not trade_ok:
                self._log("  [R] ⚠ Trade cycle returned False")
            return StepResult(
                success=trade_ok, recoverable=True, step=ResupplyStep.BUYING,
                details="trade complete" if trade_ok else "trade returned False",
            )
        except Exception as exc:
            self._log(f"  [R] ⚠ Trade cycle error: {exc!r}")
            return StepResult(
                success=False, recoverable=True, step=ResupplyStep.BUYING,
                details=f"trade raised: {exc!r}",
                exception=exc,
            )

    def _step_return(self, player_pos: Any, return_pos: Any) -> StepResult:
        """
        Step 5: Navigate back to return position (non-fatal).

        Failure is logged but does not affect the cycle result.
        """
        effective_return = return_pos
        if effective_return is None and self._cfg.return_coord:
            from src.models import Coordinate
            rc = self._cfg.return_coord
            if len(rc) >= 3:
                effective_return = Coordinate(x=rc[0], y=rc[1], z=rc[2])
        if effective_return is None:
            effective_return = player_pos  # return to where we started

        if not (
            self._cfg.navigate_to_depot
            and self._nav is not None
            and effective_return is not None
        ):
            return StepResult(
                success=True, recoverable=True, step=ResupplyStep.RETURNING,
                details="return navigation skipped",
            )

        self._log("  [R] Step 5: Navigating back…")
        nav_target: List[int] = [
            effective_return.x, effective_return.y, effective_return.z,
        ]
        nav_ok = self._navigate_to(
            nav_target,
            player_pos=None,  # current pos unknown after depot
        )
        return StepResult(
            success=nav_ok, recoverable=True, step=ResupplyStep.RETURNING,
            details="return navigation complete" if nav_ok else "return navigation failed (non-fatal)",
        )

    # ── Navigation helper ────────────────────────────────────────────────

    def _navigate_to(
        self,
        target_coord: List[int],
        player_pos: Any = None,
    ) -> bool:
        """
        Use the navigator to find a path to target_coord and walk it.
        Returns True if navigation completed.
        """
        if self._nav is None:
            return False

        try:
            from src.models import Coordinate

            if len(target_coord) < 3:
                self._log("  [R] ⚠ Invalid target coord")
                return False

            target = Coordinate(
                x=target_coord[0], y=target_coord[1], z=target_coord[2],
            )

            start = player_pos
            if start is None:
                self._log("  [R] ⚠ No player position — cannot navigate")
                return False

            route = self._nav.navigate(start, target)
            if route is None or not getattr(route, "found", False):
                self._log(
                    f"  [R] ⚠ No route found from {start} to {target}"
                )
                return False

            self._log(f"  [R] Route found → {target}")

            # H5-fix: actually walk the route via walk_fn callback
            if self._walk_fn is not None:
                walk_ok = self._walk_fn(route)
                if not walk_ok:
                    self._log("  [R] ⚠ Walk execution failed")
                    return False
            else:
                self._log("  [R] ✖ No walk_fn registrado — la ruta no se ejecutó. Registra set_walk_fn() antes de llamar run_resupply().")
                return False

            return True

        except Exception as exc:
            self._log(f"  [R] Navigation error: {exc!r}")
            return False

    # ── Bank withdraw ────────────────────────────────────────────────────

    def _bank_withdraw(self) -> bool:
        """
        Withdraw gold from the bank NPC.

        Sends: "hi" → "withdraw {amount}" or "withdraw all" → "yes".
        Reuses the DepotManager's ctrl for sending text.
        Retries the full dialogue sequence once on failure.
        """
        if self._ctrl is None and self._depot is not None:
            self._ctrl = getattr(self._depot, "_ctrl", None)

        if self._ctrl is None or not self._ctrl.is_connected():
            self._log("  [R] ⚠ No controller — bank withdraw skipped")
            return False

        amount = self._cfg.bank_withdraw_amount
        withdraw_cmd = f"withdraw {amount}" if amount > 0 else "withdraw all"

        delay = 1.2  # NPC dialogue delay
        if self._depot is not None:
            delay = getattr(
                getattr(self._depot, "_cfg", None), "bank_dialogue_delay", 1.2,
            )

        for attempt in range(2):
            if attempt > 0:
                self._log("  [R] Bank withdraw retry…")
                time.sleep(delay)

            ok = self._send_npc_dialogue(["hi", withdraw_cmd, "yes"], delay)
            if ok:
                self._log("  [R] Bank withdraw complete ✓")
                return True

        self._log("  [R] ⚠ Bank withdraw failed after retries")
        return False

    def _send_npc_dialogue(self, messages: List[str], delay: float) -> bool:
        """Send a sequence of NPC dialogue lines with delay between each.

        After the first message ("hi"), waits for the NPC dialog to appear
        before sending the remaining commands.  If no frame_getter is wired,
        falls back to a timed delay (original behaviour).
        """
        ctrl = self._ctrl
        if ctrl is None or not hasattr(ctrl, "type_text"):
            self._log("  [R] ⚠ Controller has no type_text — skipping")
            return False

        time.sleep(random.uniform(0.2, 0.45))
        for idx, msg in enumerate(messages):
            if not ctrl.is_connected():
                self._log(f"  [R] ⚠ Connection lost during '{msg}'")
                return False
            ctrl.type_text(msg)
            ctrl.press_key(0x0D)  # VK_RETURN
            self._log(f"  [R] NPC → '{msg}'")
            time.sleep(delay * random.uniform(0.8, 1.25))

            # After "hi", verify the NPC dialog appeared before proceeding.
            # Without this check, subsequent messages are typed into public chat.
            if idx == 0 and self._frame_getter is not None:
                try:
                    from src.action_verifier import verify_dialog_open
                    dialog_ok = verify_dialog_open(
                        self._frame_getter, timeout=2.0, poll_interval=0.3
                    )
                except Exception:
                    _log.debug("verify_dialog_open unavailable — assuming OK", exc_info=True)
                    dialog_ok = True  # verifier not available — assume OK
                if not dialog_ok:
                    self._log("  [R] ⚠ NPC dialog no detectado tras 'hi' — abortando secuencia")
                    return False
        return True

    # ── Stats ────────────────────────────────────────────────────────────

    @property
    def resupply_count(self) -> int:
        return self._resupply_count

    @property
    def last_trigger_reason(self) -> str:
        return self._last_trigger_reason

    def stats_snapshot(self) -> Dict[str, Any]:
        return {
            "resupply_count": self._resupply_count,
            "last_resupply_ts": self._last_resupply_ts,
            "last_trigger_reason": self._last_trigger_reason,
            "enabled": self._cfg.enabled,
        }

    def _emit(self, event: str, data: Any = None) -> None:
        if self._event_bus is not None:
            try:
                self._event_bus.emit(event, data)
            except Exception:
                _log.debug("_emit(%s) failed (ignored)", event, exc_info=True)

    def _log(self, msg: str) -> None:
        self._log_fn(msg)
