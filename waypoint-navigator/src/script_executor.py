"""
ScriptExecutor
--------------
Executes a list of :class:`~src.script_parser.Instruction` objects
(produced by :class:`~src.script_parser.ScriptParser`) step by step.

Supported instruction kinds
---------------------------
node / stand / ladder / shovel / rope  → A* walk to coordinate
label                                  → noop (jump target)
goto <label>                           → unconditional jump
action end                             → stop execution
action wait                            → sleep 1 s
action combat_pause                    → pause CombatManager loop
action combat_resume                   → resume CombatManager loop
action combat_start                    → start CombatManager loop
action combat_stop                     → stop CombatManager loop
action walk_keys                       → switch input_method to scancode (keyboard)
action walk_mouse                      → switch input_method to postmessage (mouse)
action chat_on                         → press Enter to open Tibia chat console
action chat_off                        → press Escape to close Tibia chat console
action deposit / depot                 → run a DepotManager cycle
action sell                            → delegate to npc_handler or log stub
action buy_potions                     → delegate to npc_handler or log stub
action buy_ammo                        → delegate to npc_handler or log stub
action check_supplies                  → delegate to npc_handler or log stub
action check_ammo                      → delegate to npc_handler or log stub
action check                           → log current HP/MP from healer
action check_time                      → stop if hours_leave reached
wait <N>                               → sleep N seconds
use_hotkey <vk>                        → press VK key
use_item <name> vk=N                   → press VK key (when vk given)
if hp/mp <op> N goto <lbl>            → conditional jump
say <text>                             → type text in Tibia chat
talk_npc [words]                       → send each word to NPC chat
depot                                  → run a DepotManager cycle
cond_jump (legacy frbot)               → conditional branch on variable

Usage
-----
::

    from src.script_parser import ScriptParser
    from src.script_executor import ScriptExecutor

    instructions = ScriptParser.parse_file(Path("routes/hunt.in"))
    executor = ScriptExecutor(ctrl, navigator, log_fn=print)
    executor.set_position(Coordinate(32377, 32222, 7))
    executor.execute(instructions)
"""

from __future__ import annotations

import datetime
import logging
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("wn.se")  # script executor file-logger

from .models import BOUNDS
from .script_executor_walk import (
    execute_segment_steps,
    is_segment_path_excessive,
    retry_transient_block,
    should_retry_transient_block,
    walk_to,
)
from .script_executor_trade import (
    buy_ammo_chat,
    buy_potions_chat,
    check_ammo,
    check_supplies,
    click_dialog_option,
    parse_trade_items,
    sell_chat,
    trade_gui_or_chat,
    verify_npc_dialog,
)
from .script_executor_runtime import (
    dispatch_instruction,
    execute_script,
    handle_action_end,
    handle_chat_toggle,
    handle_check,
    handle_check_time,
    handle_combat_action,
    handle_cond_jump,
    handle_depot,
    handle_goto,
    handle_if_stat,
    handle_label,
    handle_movement,
    handle_npc_action,
    handle_open_door,
    handle_random_stand,
    handle_say,
    handle_talk_npc,
    handle_use_hotkey,
    handle_wait,
    handle_walk_mode,
    rewind_to_last_confirmed_node,
)
from .script_executor_interaction import (
    click_dialog_option as interaction_click_dialog_option,
    open_door as interaction_open_door,
    say_to_npc as interaction_say_to_npc,
    switch_to_npc_channel as interaction_switch_to_npc_channel,
    verify_npc_dialog as interaction_verify_npc_dialog,
)
from .script_executor_state import (
    add_wp_waypoint,
    arm_post_block_position_watch,
    block_diagnostic_dir,
    click_character_tile,
    current_wp_position,
    estimate_game_viewport_bounds,
    find_nearest_walkable,
    get_pathfinder,
    is_leave_time,
    note_post_block_position_result,
    read_stat,
    record_wp_action,
    request_replan,
    save_block_diagnostic,
    sleep_interruptible,
    sync_position,
)
from .script_parser import Instruction
try:
    from .navigation.waypoint_logger import WaypointLogger, Position as WPPosition
except ImportError:
    WaypointLogger = None  # type: ignore
    WPPosition = None  # type: ignore

try:
    from .action_verifier import (
        verify_position_changed,
        verify_dialog_open,
        find_dialog_option,
        ActionVerificationError,
    )
except ImportError:  # pragma: no cover
    verify_position_changed = None  # type: ignore
    verify_dialog_open = None       # type: ignore
    find_dialog_option = None       # type: ignore
    ActionVerificationError = Exception  # type: ignore


# ---------------------------------------------------------------------------
class ScriptExecutor:
    """
    Sequential executor for parsed ``.in`` script instructions.

    Parameters
    ----------
    ctrl : InputController
        Sends keystrokes and mouse events to the game window.
    navigator : WaypointNavigator
        A* path-finder used for movement instructions.
    step_interval : float
        Seconds to pause between each tile step (default 0.18 s).
    healer : object, optional
        Object exposing ``.hp_pct`` / ``._hp_pct`` and ``.mp_pct`` /
        ``._mp_pct`` attributes; used by ``if_stat`` instructions.
    frame_getter : callable, optional
        ``() -> np.ndarray | None``  — screen frame source
        (passed through to :attr:`depot_manager` when needed).
    depot_manager : DepotManager, optional
        Invoked when a ``depot`` instruction is encountered.
    combat_manager : CombatManager, optional
        Target of ``action combat_pause/resume/start/stop`` instructions.
    hours_leave : list of float, optional
        Hours of the day (e.g. ``[9.5]`` = 09:30) at which ``action
        check_time`` will stop the executor.  Each value is
        ``hour + minutes/60``.
    npc_handler : callable, optional
        ``(action_name: str, ins: Instruction) -> None`` — called for NPC
        interaction actions (``sell``, ``buy_potions``, ``buy_ammo``,
        ``check_supplies``, ``check_ammo``). When *None* those actions are
        logged as stubs.
    dry_run : bool
        When *True*, log all actions but send no real input.
    jitter : float
        Maximum extra random seconds added to each ``_sleep()`` call
        (0 = disabled).
    log_fn : callable, optional
        ``(msg: str) -> None``  — logging callback (defaults to
        :func:`print`).
    """

    #: Re-read real position every N tile steps (when position_getter is set).
    _RESYNC_EVERY: int = 10
    #: Max times a failed movement can rewind to the previous confirmed node
    #: before the executor stops and leaves a resumable checkpoint.
    _RESUME_RETRY_MAX: int = 3
    #: Tile drift threshold (Manhattan) before triggering a replan mid-walk.
    _DRIFT_THRESHOLD: int = 8
    _PATH_ALIGNMENT_THRESHOLD: int = 3
    _STEP_DRIFT_ACCEPT_THRESHOLD: int = 2
    #: Max consecutive steps with no radar confirmation before aborting walk.
    #: Dead-reckoning works well for short-to-medium segments; only abort on
    #: extremely long stretches where accumulated error becomes dangerous.
    _MAX_BLIND_STEPS: int = 100
    #: Retries per step before marking a tile as blocked and replanning.
    _BLOCKED_RETRIES: int = 2
    #: Extra courtesy retries for temporary blockers on an adjacent tile.
    _TRANSIENT_BLOCK_RETRIES: int = 4
    #: Wait between transient-block retries (seconds).
    _TRANSIENT_BLOCK_DELAY: float = 0.35
    #: After a real block, abort certification if fresh position reads keep
    #: failing for too long while the executor is forced to dead-reckon.
    #: Set to 50 so depot interior (~12-15 steps of dead-reckoning due to
    #: radar texture mismatch) doesn't trigger premature abort.
    _MAX_POST_BLOCK_POSITION_MISSES: int = 50
    #: Abort segments whose A* detour is wildly longer than the direct distance.
    _MAX_SEGMENT_STRETCH_RATIO: float = 3.0
    _MAX_SEGMENT_STRETCH_BUFFER: int = 12
    #: Extra radar retries when first read returns None after a step.
    _RADAR_RETRIES: int = 2
    #: Delay between radar retries (seconds).
    _RADAR_RETRY_DELAY: float = 0.15
    #: Steps ahead to scan for proactive reroute (obstacle lookahead).
    _LOOKAHEAD_TILES: int = 3
    #: How often (in steps) to run the proactive lookahead scan.
    _LOOKAHEAD_EVERY: int = 5

    def __init__(
        self,
        ctrl: Any,
        navigator: Any,
        step_interval: float = 0.45,
        healer: Optional[Any] = None,
        frame_getter: Optional[Callable[[], Any]] = None,
        depot_manager: Optional[Any] = None,
        combat_manager: Optional[Any] = None,
        dry_run: bool = False,
        jitter: float = 0.0,
        log_fn: Optional[Callable[[str], None]] = None,
        position_getter: Optional[Callable[[], Any]] = None,
        position_setter: Optional[Callable[[Any], None]] = None,
        hours_leave: Optional[List[float]] = None,
        npc_handler: Optional[Callable[[str, Any], None]] = None,
        rope_hotkey_vk: int = 0,
        shovel_hotkey_vk: int = 0,
        waypoint_logger: Optional[Any] = None,
        minimap_radar: Optional[Any] = None,
        walk_verify_timeout: float = 3.0,
        walk_verify_retries: int = 2,
        stuck_detector: Optional[Any] = None,
        dispatch_retries: int = 2,
        dispatch_backoff_base: float = 0.3,
    ) -> None:
        self._ctrl            = ctrl
        self._nav             = navigator
        self._interval        = step_interval
        self._healer          = healer
        self._frame_getter    = frame_getter
        self._depot           = depot_manager
        self._combat          = combat_manager
        self._dry_run         = dry_run
        self._jitter          = jitter
        self._log_fn          = log_fn or print
        self._position_getter = position_getter
        self._position_setter = position_setter
        self._hours_leave   = list(hours_leave) if hours_leave else []
        self._npc_handler   = npc_handler
        self._rope_vk       = rope_hotkey_vk
        self._shovel_vk     = shovel_hotkey_vk
        # Optional waypoint logger (records waypoints and player actions)
        self._wp_logger = waypoint_logger

        # Optional minimap radar for walk verification feedback-loop
        self._radar = minimap_radar
        self._walk_verify_timeout = walk_verify_timeout
        self._walk_verify_retries = walk_verify_retries

        # Optional stuck detector (background thread monitoring position)
        self._stuck = stuck_detector

        # Optional path visualizer for debug trace images
        self._path_viz: Optional[Any] = None
        self._walk_segment_counter: int = 0
        self._watch_post_block_position_loss: bool = False
        self._post_block_position_miss_streak: int = 0

        # Dispatch retry config: retry transient errors with exponential backoff
        self._dispatch_retries = max(0, dispatch_retries)
        self._dispatch_backoff_base = dispatch_backoff_base

        self._running      = False
        # Current position tracking (Coordinate or None)
        self._current_pos: Optional[Any] = None
        # Last instruction dispatched (exposed for the GUI monitor)
        self._current_instr: Optional[Any] = None
        # Full instruction list (set by execute, exposed for the GUI minimap overlay)
        self._instructions: List[Any] = []
        # Item counter for cond_jump (conditional_jump_item_count_below)
        self._item_counter: Dict[str, int] = {}
        # Testing hook: when set, replaces _dispatch inside execute()
        self._dispatch_override: Optional[Callable[[Instruction], Optional[str]]] = None
        # Persistent blocked-tile pixels across walk calls:  (px, py, floor)
        self._blocked_pixels: List[tuple[int, int, int]] = []
        self._blocked_pixel_set: set[tuple[int, int, int]] = set()
        # Number of tiles added by add_blocked_region (pre-loaded, not dynamic)
        self._preblocked_count: int = 0
        # Flag set by StuckDetector to interrupt the current walk.
        self._replan_requested: bool = False
        # Instruction index to resume from after a failed movement.
        self._resume_instruction_index: Optional[int] = None
        self._current_instruction_index: int = 0
        self._last_confirmed_node_index: int = 0
        self._resume_retry_counts: Dict[int, int] = {}
        self._stop_reason: str = ""
        # None = no walk attempted yet; True/False reflect the latest walk result.
        self._last_walk_ok: Optional[bool] = None
        # Optional obstacle analyzer for minimap-based runtime block detection
        self._obstacle_analyzer: Optional[Any] = None
        # Tiles where the character walked through a static-wall (bidirectional learning)
        self._opened_pixels: List[tuple[int, int, int]] = []
        self._opened_pixel_set: set[tuple[int, int, int]] = set()
        self._max_opened_pixels: int = 512
        # Reference to map_loader for walkability checks (bidirectional learning)
        self._map_loader: Optional[Any] = None
        # WasP supply-check heuristics: track whether the hunt zone was reached
        self._has_hunted: bool = False
        # Optional WasP setup JSON (for hunt_config, items, etc.)
        self._wasp_setup: Optional[Dict[str, Any]] = None

        # ── Dispatch registry dicts ────────────────────────────────────────
        self._KIND_HANDLERS: Dict[str, Any] = {
            "depot":      self._handle_depot,
            "wait":       self._handle_wait,
            "label":      self._handle_label,
            "goto":       self._handle_goto,
            "use_hotkey": self._handle_use_hotkey,
            "use_item":   self._handle_use_hotkey,
            "if_stat":    self._handle_if_stat,
            "cond_jump":  self._handle_cond_jump,
            "say":        self._handle_say,
            "talk_npc":   self._handle_talk_npc,
            "open_door":  self._handle_open_door,
            "node":         self._handle_movement,
            "stand":        self._handle_movement,
            "ladder":       self._handle_movement,
            "shovel":       self._handle_movement,
            "rope":         self._handle_movement,
            "random_stand": self._handle_random_stand,
        }
        self._ACTION_HANDLERS: Dict[str, Any] = {
            "end":            self._handle_action_end,
            "combat_pause":   self._handle_combat_action,
            "combat_resume":  self._handle_combat_action,
            "combat_start":   self._handle_combat_action,
            "combat_stop":    self._handle_combat_action,
            "depot":          self._handle_depot,
            "deposit":        self._handle_depot,
            "walk_keys":      self._handle_walk_mode,
            "walk_mouse":     self._handle_walk_mode,
            "chat_on":        self._handle_chat_toggle,
            "chat_off":       self._handle_chat_toggle,
            "sell":           self._handle_npc_action,
            "buy_potions":    self._handle_npc_action,
            "buy_ammo":       self._handle_npc_action,
            "check_supplies": self._handle_npc_action,
            "check_ammo":     self._handle_npc_action,
            "check":          self._handle_check,
            "check_time":     self._handle_check_time,
            "wait":           self._handle_wait,
        }

    def set_path_visualizer(self, viz: Any) -> None:
        """Attach a :class:`~src.path_visualizer.PathVisualizer`."""
        self._path_viz = viz

    def set_obstacle_analyzer(self, analyzer: Any) -> None:
        """Wire an :class:`~src.obstacle_analyzer.ObstacleAnalyzer` for
        runtime minimap-based obstacle detection during walks."""
        self._obstacle_analyzer = analyzer

    def set_map_loader(self, loader: Any) -> None:
        """Wire a :class:`~src.map_loader.TibiaMapLoader` for bidirectional
        walkability learning (detecting tiles walkable in-game but walled
        in static data)."""
        self._map_loader = loader

    def _estimate_game_viewport_bounds(self, frame: Any = None) -> tuple[int, int, int, int]:
        return estimate_game_viewport_bounds(executor=self, frame=frame)

    def _block_diagnostic_dir(self) -> Path:
        return block_diagnostic_dir(executor=self)

    def _save_block_diagnostic(
        self,
        *,
        blocked_tile: Any,
        actual_pos: Any,
        dest: Any,
        step_index: int,
        total_steps: int,
    ) -> Optional[Path]:
        return save_block_diagnostic(
            executor=self,
            blocked_tile=blocked_tile,
            actual_pos=actual_pos,
            dest=dest,
            step_index=step_index,
            total_steps=total_steps,
        )

    def request_replan(self) -> bool:
        """Interrupt the current walk and rewind to the previous node.

        Dynamic replanning during a walk is disabled. This compatibility hook
        is kept so StuckDetector can still ask the executor to stop the active
        segment and resume from the last confirmed node instead."""
        return request_replan(executor=self)

    def _arm_post_block_position_watch(self) -> None:
        arm_post_block_position_watch(executor=self)

    def _note_post_block_position_result(self, has_fresh_position: bool) -> bool:
        return note_post_block_position_result(executor=self, has_fresh_position=has_fresh_position)

    @property
    def resume_instruction_index(self) -> int:
        if self._resume_instruction_index is not None:
            return self._resume_instruction_index
        return self._last_confirmed_node_index

    @property
    def last_confirmed_node_index(self) -> int:
        return self._last_confirmed_node_index

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True while :meth:`execute` is running."""
        return self._running

    def set_position(self, pos: Any) -> None:
        """Inject the current player position (:class:`~src.models.Coordinate`)."""
        self._current_pos = pos

    def _current_wp_position(self) -> Optional[Any]:
        return current_wp_position(executor=self, waypoint_position_cls=WPPosition)

    def _record_wp_action(
        self,
        action: str,
        description: str,
        *,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        record_wp_action(executor=self, action=action, description=description, meta=meta)

    def _add_wp_waypoint(self, pos: Any, action: str) -> None:
        add_wp_waypoint(executor=self, pos=pos, action=action)

    def set_depot_manager(self, dm: Any) -> None:
        """Hot-swap the DepotManager (safe to call before ``execute``)."""
        self._depot = dm

    def set_combat_manager(self, cm: Any) -> None:
        """Hot-swap the CombatManager (safe to call before ``execute``)."""
        self._combat = cm

    def add_blocked_region(
        self,
        x_min: int,
        x_max: int,
        y_min: int,
        y_max: int,
        z: int,
    ) -> None:
        """Pre-mark a rectangular region as blocked (world coordinates).

        Tiles in this region will be treated as non-walkable by A*,
        useful for correcting known walkability-data errors.
        """
        from .models import BOUNDS

        x_off = BOUNDS["xMin"]
        y_off = BOUNDS["yMin"]
        for wy in range(y_min, y_max + 1):
            for wx in range(x_min, x_max + 1):
                entry = (wx - x_off, wy - y_off, z)
                self._remember_blocked_pixel(entry)
        self._preblocked_count = len(self._blocked_pixels)
        self._invalidate_route_cache()

    def force_walkable_region(
        self,
        x_min: int,
        x_max: int,
        y_min: int,
        y_max: int,
        z: int,
    ) -> int:
        """Permanently mark a rectangular region as walkable (world coordinates).

        Directly patches the pathfinder walkability array.  Returns the
        number of tiles that were flipped from non-walkable to walkable.
        """
        from .models import BOUNDS

        pf = self._get_pathfinder(z)
        if pf is None:
            return 0
        if getattr(pf.walkability, 'ndim', 0) != 2:
            return 0
        h, w = pf.walkability.shape
        x_off = BOUNDS["xMin"]
        y_off = BOUNDS["yMin"]
        count = 0
        for wy in range(y_min, y_max + 1):
            for wx in range(x_min, x_max + 1):
                px = wx - x_off
                py = wy - y_off
                if 0 <= px < w and 0 <= py < h and not pf.walkability[py, px]:
                    pf.walkability[py, px] = True
                    count += 1
        if count > 0:
            self._invalidate_route_cache()
        return count

    def _invalidate_route_cache(self) -> None:
        route_cache = getattr(self._nav, "_route_cache", None)
        if isinstance(route_cache, dict):
            route_cache.clear()

    def _remember_blocked_pixel(self, entry: tuple[int, int, int]) -> bool:
        if entry in self._blocked_pixel_set or entry in self._blocked_pixels:
            self._blocked_pixel_set.add(entry)
            return False
        self._blocked_pixel_set.add(entry)
        self._blocked_pixels.append(entry)
        self._invalidate_route_cache()
        return True

    def _trim_dynamic_blocked_pixels(self) -> None:
        if len(self._blocked_pixels) <= self._preblocked_count:
            return
        self._blocked_pixels = self._blocked_pixels[: self._preblocked_count]
        self._blocked_pixel_set = set(self._blocked_pixels)
        self._invalidate_route_cache()

    def _remember_opened_pixel(self, entry: tuple[int, int, int]) -> bool:
        if entry in self._opened_pixel_set or entry in self._opened_pixels:
            self._opened_pixel_set.add(entry)
            return False
        self._opened_pixel_set.add(entry)
        self._opened_pixels.append(entry)
        overflow = len(self._opened_pixels) - self._max_opened_pixels
        if overflow > 0:
            for _ in range(overflow):
                dropped = self._opened_pixels.pop(0)
                self._opened_pixel_set.discard(dropped)
        self._invalidate_route_cache()
        return True

    def increment_item_count(self, item_name: str, n: int = 1) -> None:
        """Increment the internal loot counter for *item_name* by *n*.

        Called by the session when the Looter successfully loots an item.
        Used by ``cond_jump`` with ``conditional_jump_item_count_below``.
        """
        key = item_name.lower()
        self._item_counter[key] = self._item_counter.get(key, 0) + n

    @property
    def item_counts(self) -> Dict[str, int]:
        """Read-only snapshot of the current item counter."""
        return dict(self._item_counter)

    def abort(self) -> None:
        """Signal the running :meth:`execute` to stop after the current instruction.

        Thread-safe; can be called from a different thread.
        """
        self._running = False

    def execute(self, instructions: List[Instruction], start_index: int = 0) -> None:
        """
        Execute *instructions* sequentially.

        Runs until one of the following occurs:

        * An ``action end`` instruction is encountered.
        * :meth:`abort` is called from another thread.
        * The instruction list is exhausted.

        Parameters
        ----------
        instructions : list[Instruction]
            Parsed instructions produced by :class:`~src.script_parser.ScriptParser`.
        """
        execute_script(
            executor=self,
            instructions=instructions,
            start_index=start_index,
            build_labels_fn=_build_labels,
        )

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(self, ins: Instruction) -> Optional[str]:
        """Execute one instruction. Returns jump label or None."""
        return dispatch_instruction(executor=self, ins=ins)

    # ── Extracted dispatch handlers ───────────────────────────────────────────

    def _handle_action_end(self, ins: Instruction) -> Optional[str]:
        return handle_action_end(executor=self, ins=ins)

    def _handle_combat_action(self, ins: Instruction) -> Optional[str]:
        return handle_combat_action(executor=self, ins=ins)

    def _handle_depot(self, ins: Instruction) -> Optional[str]:
        return handle_depot(executor=self, ins=ins)

    def _handle_walk_mode(self, ins: Instruction) -> Optional[str]:
        return handle_walk_mode(executor=self, ins=ins)

    def _handle_chat_toggle(self, ins: Instruction) -> Optional[str]:
        return handle_chat_toggle(executor=self, ins=ins)

    def _handle_npc_action(self, ins: Instruction) -> Optional[str]:
        return handle_npc_action(executor=self, ins=ins)

    def _handle_check(self, ins: Instruction) -> Optional[str]:
        return handle_check(executor=self, ins=ins)

    def _handle_check_time(self, ins: Instruction) -> Optional[str]:
        return handle_check_time(executor=self, ins=ins)

    def _handle_wait(self, ins: Instruction) -> Optional[str]:
        return handle_wait(executor=self, ins=ins)

    def _handle_label(self, ins: Instruction) -> Optional[str]:
        return handle_label(executor=self, ins=ins)

    def _handle_goto(self, ins: Instruction) -> Optional[str]:
        return handle_goto(executor=self, ins=ins)

    def _handle_use_hotkey(self, ins: Instruction) -> Optional[str]:
        return handle_use_hotkey(executor=self, ins=ins)

    def _handle_if_stat(self, ins: Instruction) -> Optional[str]:
        return handle_if_stat(executor=self, ins=ins)

    def _handle_cond_jump(self, ins: Instruction) -> Optional[str]:
        return handle_cond_jump(executor=self, ins=ins)

    def _handle_say(self, ins: Instruction) -> Optional[str]:
        return handle_say(executor=self, ins=ins)

    def _handle_talk_npc(self, ins: Instruction) -> Optional[str]:
        return handle_talk_npc(executor=self, ins=ins)

    def _handle_open_door(self, ins: Instruction) -> Optional[str]:
        return handle_open_door(executor=self, ins=ins)

    def _handle_movement(self, ins: Instruction) -> Optional[str]:
        return handle_movement(executor=self, ins=ins)

    def _rewind_to_last_confirmed_node(self, dest: Any) -> None:
        rewind_to_last_confirmed_node(executor=self, dest=dest)

    def _handle_random_stand(self, ins: "Instruction") -> Optional[str]:
        """Pick a random coordinate from ins.choices and walk to it.

        Each loop cycle gets a different endpoint, breaking the deterministic
        movement signature detectable by server-side analytics.
        """
        return handle_random_stand(executor=self, ins=ins)

    # ── Movement helpers ──────────────────────────────────────────────────────

    def _click_character_tile(self) -> None:
        """Click on the character's tile (center of game viewport).

        Used after pressing a shovel/rope crosshair hotkey to complete
        the 'use with crosshair' action on the tile the character stands on.
        The game viewport center is estimated from the frame dimensions and
        the known sidebar boundary (battle_list_roi x-position).
        """
        click_character_tile(executor=self)

    #: Maximum plausible position change between consecutive syncs.
    #: Rejects radar readings that jump more than this many tiles from the
    #: last known position — protects against false matches.
    _MAX_SYNC_JUMP: int = 10

    #: Tighter jump guard for post-step verification.
    #: After a single step the character moves 0-1 tiles; anything beyond
    #: this many tiles is almost certainly a false radar match.
    _MAX_STEP_JUMP: int = 3

    def _sync_position(self) -> None:
        """Update ``_current_pos`` from the real-world position getter.

        Called before each A* query and periodically mid-walk so that
        subsequent waypoints start from the character's *actual* tile
        rather than the *theoretical* tile tracked by dead-reckoning.

        Rejects radar readings that jump more than ``_MAX_SYNC_JUMP``
        tiles from the current position (likely a false template match).
        """
        sync_position(executor=self, logger=_log)

    def _get_pathfinder(self, floor: int) -> Any:
        """Return the :class:`AStarPathfinder` for *floor*, or ``None``."""
        return get_pathfinder(executor=self, floor=floor)

    def _find_nearest_walkable(self, x: int, y: int, z: int, radius: int = 3) -> Optional[Any]:
        """Return the nearest walkable :class:`~src.models.Coordinate` to *(x, y)* on floor *z*.

        Checks tiles at increasing Manhattan radius (0 → *radius*) using
        direct numpy array indexing (O(1) per tile lookup) instead of
        calling ``is_walkable()`` to avoid repeated Python dispatch overhead.
        Returns ``None`` if no walkable tile is found within *radius*.
        """
        return find_nearest_walkable(executor=self, x=x, y=y, z=z, radius=radius)

    def _walk_to(self, dest: Any, kind: str) -> None:
        """Navigate to *dest* using A* then step tile-by-tile.

        Before computing the A* path the method syncs ``_current_pos``
        from the real position getter (MinimapRadar when available).

        If the character gets blocked or drifts off-path mid-walk, the
        walk aborts and the executor rewinds to the last confirmed node.
        """
        walk_to(executor=self, dest=dest, kind=kind, logger=_log)

    def _is_segment_path_excessive(self, steps: List[Any], dest: Any) -> bool:
        return is_segment_path_excessive(executor=self, steps=steps, dest=dest)

    def _execute_segment_steps(
        self, steps: List[Any],
    ) -> tuple[bool, Any]:
        """Walk through A* steps tile-by-tile with per-step verification.

        Returns ``(True, None)`` if the segment completed normally, or
        ``(False, blocked_tile)`` when the character drifted or got blocked
        and the caller should abort the segment.  *blocked_tile* is the
        :class:`~src.models.Coordinate` the character could not reach (may
        be ``None`` for pure-drift aborts).
        """
        return execute_segment_steps(executor=self, steps=steps, logger=_log)

    def _should_retry_transient_block(
        self,
        *,
        current_pos: Any,
        target_pos: Any,
    ) -> bool:
        return should_retry_transient_block(
            executor=self,
            current_pos=current_pos,
            target_pos=target_pos,
        )

    def _retry_transient_block(
        self,
        *,
        dx: int,
        dy: int,
        curr: Any,
        step_index: int,
        total_steps: int,
    ) -> bool:
        return retry_transient_block(
            executor=self,
            dx=dx,
            dy=dy,
            curr=curr,
            step_index=step_index,
            total_steps=total_steps,
        )

    # ── Door helpers ──────────────────────────────────────────────────────

    def _open_door(self, door_coord: Any) -> None:
        """Walk toward the door tile to open it, then verify passage.

        In Tibia, walking into a closed door opens it.  This method:
        1. Syncs position.
        2. Calculates direction from current pos to door tile.
        3. Sends movement toward the door (up to 5 attempts).
        4. Verifies the character actually moved through.
        """
        interaction_open_door(executor=self, door_coord=door_coord)

    def _say_to_npc(self, text: str) -> None:
        """Type *text* in the active NPC channel (Enter → text → Enter)."""
        interaction_say_to_npc(executor=self, text=text)

    def _switch_to_npc_channel(self) -> None:
        """Click the NPC channel tab so subsequent typing goes there.

        The NPC channel tab appears when talking to an NPC and its label
        is rendered in a coloured font (reddish/orange) unlike the white
        "Default" tab.  We scan the tab-bar strip for high-saturation
        bright pixels and click on the centroid.
        """
        interaction_switch_to_npc_channel(executor=self)

    # ── WasP-compat supply / ammo actions ────────────────────────────────────

    def _check_ammo(self, ins: Any) -> Optional[str]:
        """Check ammo quantity and jump to the appropriate label.

        Reads ``hunt_config`` from the session's WasP setup JSON (attached
        via ``_wasp_setup``).  If no setup is attached, reads ``ammo_name``
        / ``ammo_leave`` from ``ins.raw``.

        Behaviour mirrors WasP ``check_ammo``:
        - If ammo count <= ``ammo_leave`` → continue (go buy ammo).
        - Otherwise → jump to ``skip_ammo`` label so the buy step is skipped.

        Since we cannot read inventory counts from the screen yet, this
        implementation uses a heuristic: if the character has been hunting
        long enough (based on the *stuck* detector's walk-step counter),
        assume ammo may be low and do NOT skip.  On a fresh start or when
        ``_force_resupply`` is set, always buy.
        """
        return check_ammo(executor=self, ins=ins)

    def _check_supplies(self, ins: Any) -> Optional[str]:
        """Check if supplies (potions, ammo) are sufficient to continue hunting.

        Mirrors WasP ``check_supplies``:
        - Reads ``hunt_config`` from the WasP setup (``mana_leave``,
          ``ammo_leave``, ``cap_leave``).
        - If supplies are below thresholds → jump to ``leave`` label.
        - Otherwise → continue to ``go_hunt`` / next instruction.

        Current implementation: heuristic based on trip count.
        After first hunt cycle, always return to town for resupply (safe).
        """
        return check_supplies(executor=self, ins=ins)

    def _buy_ammo_chat(self, ins: Any) -> None:
        """Buy ammunition via NPC chat commands.

        Works identically to ``_buy_potions_chat`` but reads ammo config
        from ``hunt_config`` (WasP format) or ``ins.raw``.
        """
        buy_ammo_chat(executor=self, ins=ins)

    def _buy_potions_chat(self, ins: Any) -> None:
        """Buy potions via typed NPC channel commands.

        Reads ``items`` from ``ins.raw`` (JSON dict).  Each item is
        ``{"name": "mana potion", "qty": 50}``.  For every item the
        method types ``buy <qty> <name>`` then confirms with ``yes``.
        """
        buy_potions_chat(executor=self, ins=ins)

    def _sell_chat(self, ins: Any) -> None:
        """Sell items via typed NPC channel commands.

        Reads ``items`` from ``ins.raw``.  Each item is
        ``{"name": "dead rat", "qty": 0}`` (0 = sell all).
        Types ``sell all <name>`` or ``sell <qty> <name>`` + ``yes``.
        """
        sell_chat(executor=self, ins=ins)

    def _trade_gui_or_chat(self, ins: Any) -> None:
        """Execute buy/sell via the GUI trade window; fall back to chat.

        Strategy:
        1. Try to create a :class:`TradeManager` with the current config.
        2. If the trade window is detected open, use the GUI approach
           (search item → set quantity → click Ok).
        3. If no trade window or no frame source, fall back to chat commands.
        """
        trade_gui_or_chat(executor=self, ins=ins)

    def _parse_trade_items(self, ins: Any) -> list[dict[str, Any]]:
        """Parse ``items`` list from an instruction's raw JSON."""
        return parse_trade_items(ins=ins)

    def _verify_npc_dialog(self) -> None:
        """Verify an NPC dialog window is open after sending the greeting word.

        Uses ``verify_dialog_open`` from :mod:`action_verifier` if available and
        ``frame_getter`` is set.  If the dialog isn't detected, logs a warning
        but does NOT abort — the remaining words will still be sent since
        the heuristic can produce false-negatives.
        """
        interaction_verify_npc_dialog(executor=self, verify_dialog_open_fn=verify_dialog_open)

    def _click_dialog_option(self, word: str) -> bool:
        """Find a blue clickable keyword in the NPC dialog and click on it.

        Returns True if a blue keyword cluster was found and clicked.
        Uses :func:`find_dialog_option` from :mod:`action_verifier`.
        """
        return interaction_click_dialog_option(
            executor=self,
            word=word,
            find_dialog_option_fn=find_dialog_option,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_leave_time(self) -> bool:
        """Return True if the current local time has passed any configured departure hour.

        Each value in :attr:`_hours_leave` is ``hour + minutes / 60``
        (e.g. ``9.5`` = 09:30).

        Fire-once: returns True only the first time the hour is reached,
        then sets a flag so subsequent calls don't re-trigger.
        """
        return is_leave_time(executor=self, now_fn=lambda: datetime.datetime.now())

    def _read_stat(self, stat_name: str) -> Optional[int]:
        """Return HP or MP percent from the attached healer, or ``None``."""
        return read_stat(executor=self, stat_name=stat_name)

    _SLEEP_CHUNK: float = 0.05  # granularity for abort() interruptibility

    def _sleep(self, secs: float) -> None:
        """Sleep *secs* seconds, interruptible by abort() at 50 ms granularity.

        Uses divmod to avoid float accumulation across chunks.  The abort flag
        is only sampled *between* chunks so an already-started sleep is never
        left in an inconsistent state.
        """
        sleep_interruptible(
            executor=self,
            secs=secs,
            uniform_fn=random.uniform,
            sleep_fn=time.sleep,
        )

    def _log(self, msg: str) -> None:
        self._log_fn(msg)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _build_labels(instructions: List[Instruction]) -> Dict[str, int]:
    """Return ``{label_name: index}`` for all ``label`` instructions."""
    return {
        ins.label.lower(): idx
        for idx, ins in enumerate(instructions)
        if ins.kind == "label"
    }
