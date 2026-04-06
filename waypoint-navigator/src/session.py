"""
BotSession
----------
High-level orchestrator that wires together all Navigator modules
into a single, easy-to-use API.

Usage example::

    from src.session import BotSession, SessionConfig

    cfg = SessionConfig(
        route_file="routes/thais_depot_to_temple.json",
        heal_hp_pct=65,
        heal_hotkey_vk=0x70,
        mana_hotkey_vk=0x71,
        auto_loot=True,
        depot_after_run=True,
    )
    session = BotSession(cfg)
    session.start()
    ...
    session.stop()

All sub-components are optional; only pass what you actually need.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import random
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Optional

if TYPE_CHECKING:
    from .path_visualizer import PathVisualizer

from .models import Coordinate, FloorTransition, Route, Waypoint
from .navigator import WaypointNavigator
from .map_loader import TibiaMapLoader
from .healer import AutoHealer, HealConfig
from .humanizer import jittered_sleep, set_jitter, reset_fatigue, macro_pause
from .input_controller import InputController

# Human Input System — transparent humanisation wrapper around InputController
try:
    from human_input_system import HumanInputSystem as _HIS
    _HIS_AVAILABLE = True
except ImportError:
    _HIS_AVAILABLE = False

# Arduino HID — optional hardware-level input (undetectable by BattlEye)
try:
    from human_input_system.core.arduino_hid_controller import ArduinoHIDController as _ArduinoHID
    from human_input_system.config.models import ArduinoConfig as _ArduinoConfig
    _ARDUINO_AVAILABLE = True
except ImportError:
    _ARDUINO_AVAILABLE = False

# Pico 2 HID — alternative hardware-level input via Raspberry Pi Pico 2
try:
    from human_input_system.core.pico_hid_controller import PicoHIDController as _PicoHID
    from human_input_system.config.models import PicoConfig as _PicoConfig
    _PICO_AVAILABLE = True
except ImportError:
    _PICO_AVAILABLE = False
from .minimap_radar import MinimapRadar, MinimapConfig, TibiaLocalMinimapReader
from .depot_manager import DepotManager
from .looter import Looter
from .combat_manager import CombatManager, CombatConfig
from .hpmp_detector import HpMpDetector, HpMpConfig
from .condition_monitor import ConditionMonitor, ConditionConfig
from .trade_manager import TradeManager, TradeConfig
from .event_bus import EventBus
from .frame_capture import build_frame_getter as _build_frame_getter
from .frame_cache import FrameCache
from .frame_watchdog import FrameWatchdog
from .session_capture import initialize_capture_pipeline
from .session_integrated import initialize_integrated_modules
from .session_startup import (
    init_capture as startup_init_capture,
    init_healer as startup_init_healer,
    init_input as startup_init_input,
    init_integrated_modules as startup_init_integrated_modules,
    init_monitoring as startup_init_monitoring,
    init_navigation as startup_init_navigation,
    init_optional_subsystems as startup_init_optional_subsystems,
    init_safety_handlers as startup_init_safety_handlers,
    start_session as startup_start_session,
    start_threads as startup_start_threads,
    startup_subsystems as startup_startup_subsystems,
)
from .session_monitoring import initialize_monitoring
from .session_optional import initialize_optional_subsystems
from .session_position import (
    check_frame_extras,
    get_real_position,
    set_position_from_executor,
    update_session_position,
)
from .session_safety import initialize_safety_handlers
from .session_stop import perform_session_shutdown
from .session_threads import start_session_threads
from .session_watchdog import check_subsystem_health, run_watchdog_loop, run_window_watchdog_loop
from .session_runtime import run_session_loop
from .session_script import (
    align_script_start_index,
    collect_route_critical_tiles,
    run_session_script,
    script_movement_points,
)
from .session_route_execution import (
    click_character_tile,
    execute_multifloor,
    execute_route,
    execute_transition,
    navigate_back_to,
    walk_route,
)
from .session_subsystems import build_npc_handler, pause_session_subsystems, resume_session_subsystems
from .death_handler import DeathHandler, DeathConfig
from .reconnect_handler import ReconnectHandler, ReconnectConfig
from .anti_kick import AntiKick, AntiKickConfig
from .break_scheduler import BreakScheduler, BreakSchedulerConfig
from .soak_monitor import SoakMonitor, SoakMonitorConfig
from .stuck_detector import StuckDetector, StuckConfig
from .frame_quality import FrameQualityChecker, FrameQualityConfig
from .position_resolver import PositionResolver, PositionResolverConfig, SourceKind
from .pvp_detector import PvPDetector, PvPConfig, PvPAction
from .inventory_manager import InventoryManager, InventoryConfig
from .alert_system import AlertSystem, AlertConfig
from .spawn_manager import SpawnManager, SpawnManagerConfig
from .session_stats import HuntingSessionStats, SessionStatsConfig
from .adaptive_roi import AdaptiveROIDetector, AdaptiveROIConfig
from .session_persistence import SessionCheckpoint
from .depot_orchestrator import DepotOrchestrator, ResupplyConfig
from .action_verifier import verify_position_changed
from .preflight import run_preflight
from .gm_detector import GMDetector, GMDetectorConfig, GMAction
from .chat_responder import ChatResponder, ChatResponderConfig

_log = logging.getLogger("wn")

# ---------------------------------------------------------------------------
SESSION_CONFIG_FILE = Path(__file__).parent.parent / "session_config.json"
_STATS_FILE         = Path(__file__).parent.parent / "output" / "session_stats.json"


class SessionEvent:
    """Named constants for EventBus events emitted by BotSession."""
    KILL            = "e1"
    LOOT_DONE       = "e2"
    HEAL            = "e4"
    MANA            = "e6"
    CONDITION       = "e7"
    CONDITION_CLEAR = "e8"


from src.config_paths import ROUTES_DIR as _ROUTES_DIR


def _safe_log(msg: str) -> None:
    try:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except (UnicodeEncodeError, UnicodeDecodeError):
        sys.stdout.write(msg.encode("ascii", errors="replace").decode("ascii") + "\n")
        sys.stdout.flush()


def _resolve_route(path: str | Path) -> Path:
    """Resolve a route file path, defaulting to the ``routes/`` folder.

    Resolution order:
    1. Path as-is (absolute or already relative to cwd).
    2. ``routes/<path>`` relative to the project root (strips a leading
       ``routes/`` segment from *path* first to avoid duplication).
    3. Raise :exc:`FileNotFoundError` with a helpful message.
    """
    p = Path(path)
    if p.exists():
        return p
    # Strip a leading "routes" segment to avoid _ROUTES_DIR/routes/... doubling.
    parts = p.parts
    p_rel = Path(*parts[1:]) if parts and parts[0].lower() == "routes" else p
    candidate = _ROUTES_DIR / p_rel
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Route file not found: '{p}'.\n"
        f"Looked in:\n  1. {p.resolve()}\n  2. {candidate.resolve()}\n"
        f"Files available in routes/:\n"
        + "\n".join(f"  {r.relative_to(_ROUTES_DIR.parent)}" for r in sorted(_ROUTES_DIR.rglob('*.json')))
    )


# ---------------------------------------------------------------------------
@dataclass
class SessionConfig:
    """
    Persistent configuration for BotSession.

    Parameters
    ----------
    route_file : str
        Path to a JSON waypoint route  (``routes/*.json``).
    heal_hp_pct : int
        HP % at which the healer fires the normal heal hotkey.
    heal_emergency_pct : int
        HP % at which the emergency hotkey fires.
    mana_threshold_pct : int
        MP % at which the mana-restore hotkey fires.
    heal_hotkey_vk : int
        VK code for the normal heal spell (0 = disabled).
    emergency_hotkey_vk : int
        VK code for the emergency spell (0 = disabled).
    mana_hotkey_vk : int
        VK code for the mana spell (0 = disabled).
    auto_loot : bool
        Whether to start the Looter.
    depot_after_run : bool
        Whether to run a DepotManager cycle after the walk loop ends.
    input_method : str
        ``'postmessage'`` (background) or ``'scancode'`` (foreground).
    target_window : str
        Tibia window title fragment used by InputController.
    start_delay : float
        Seconds to wait before the first move (lets you click into the window).
    loop_route : bool
        If True, repeat the route indefinitely until stop() is called.
    """

    route_file:           str   = ""
    heal_hp_pct:          int   = 70
    heal_emergency_pct:   int   = 30
    mana_threshold_pct:   int   = 30
    heal_hotkey_vk:       int   = 0x70    # F1
    emergency_hotkey_vk:  int   = 0x72    # F3
    mana_hotkey_vk:       int   = 0x71    # F2
    rope_hotkey_vk:       int   = 0x00    # 0 = disabled (configure to rope hotbar slot VK)
    shovel_hotkey_vk:     int   = 0x00    # 0 = disabled (configure to shovel hotbar slot VK)
    transition_delay:     float = 1.0     # seconds to wait after executing a floor transition
    auto_loot:            bool  = False
    depot_after_run:      bool  = False
    input_method:         str   = "interception"
    target_window:        str   = "Tibia"
    start_delay:          float = 3.0
    loop_route:           bool  = False
    jitter_pct:           float = 0.15    # input jitter as fraction of base delay (0 = off, 0.15 = ±15%)
    position_source:      str   = "none" # "none" | "mss" | "minimap"
    watchdog_timeout:     float = 0.0     # seconds before watchdog alert (0 = disabled)
    step_delay_min:       float = 0.0     # per-step extra random delay range – min seconds
    step_delay_max:       float = 0.0     # per-step extra random delay range – max seconds
    auto_combat:          bool  = False   # create & start CombatManager
    monitor_conditions:   bool  = False   # create & start ConditionMonitor
    dry_run:              bool  = False   # log all actions but send no real input
    combat_config_file:   str   = ""      # path to CombatConfig JSON (empty = defaults)
    condition_config_file: str  = ""      # path to ConditionConfig JSON (empty = defaults)
    auto_refill:          bool  = False   # create & start TradeManager for NPC refill
    trade_config_file:    str   = ""      # path to TradeConfig JSON (empty = defaults)
    start_pos:            str   = ""      # "x,y,z" initial character position (overrides _meta.start_coord)
    startup_position_tolerance: int = 2    # max Manhattan tiles between real position and start_pos before first move
    startup_position_timeout: float = 8.0  # seconds to wait for minimap position to align with start_pos
    step_interval:        float = 0.45    # seconds between consecutive tile steps (must match Tibia walk speed)
    # ── Frame source (replaces OBS) ─────────────────────────────────────────
    # ""            → use legacy position_source behaviour
    # "mss"         → DXGI via mss (pip install mss)
    # "dxcam"       → GPU DXGI via dxcam (pip install dxcam) — fastest
    # "printwindow" → Win32 PrintWindow — true background, needs hwnd via set_frame_getter
    # "wgc"         → Windows Graphics Capture — background-safe, requires pip install winsdk
    # "rtmp"        → NGINX RTMP stream; set rtmp_url + optionally rtmp_ffmpeg_window
    # "obs"         → OBS Virtual Camera (DirectShow) — Game Capture + Virtual Camera in OBS
    # "virtualcam"  → alias for "obs"
    frame_source:         str   = ""      # see above
    frame_window:         str   = ""      # window title for frame capture (overrides Tibia hwnd for wgc/mss/etc)
    monitor_idx:          int   = 2       # MSS monitor index (1=primary, 2=secondary)
    rtmp_url:             str   = "rtmp://localhost/live/tibia"
    rtmp_ffmpeg_window:   str   = "Tibia" # window title for FFmpeg gdigrab auto-push
    rtmp_fps:             int   = 10      # fps for dxcam / rtmp ffmpeg push
    obs_device_index:     int   = -1      # DirectShow device index for obs/virtualcam (-1 = auto)
    # ── Fase 4: Death / Reconnect / Anti-kick ──────────────────────────────
    death_handler:        bool  = True    # monitor for death screen
    max_deaths:           int   = 0       # auto-stop after N deaths (0 = unlimited)
    re_equip_hotkeys:     str   = ""      # comma-separated VK codes (hex/dec) for post-respawn re-equip
    reconnect_handler:    bool  = True    # monitor for disconnect / login screen
    reconnect_max_retries: int  = 5       # max reconnect attempts
    server_save_hours:    str   = "10.0"  # comma-separated hours for server save windows
    anti_kick:            bool  = True    # prevent AFK kick
    anti_kick_idle:       float = 300.0   # seconds idle before anti-kick fires
    stuck_detector:       bool  = True    # detect and recover from stuck states
    # ── Fase R1: Integrated modules ────────────────────────────────────────
    frame_quality_check:  bool  = True    # validate each frame before processing
    use_position_resolver: bool = True    # use PositionResolver multi-source chain
    position_resolver_stale_ms: float = 5000.0  # max age before position considered stale
    pvp_detector:         bool  = False   # scan for PvP skulls (needs skull templates)
    pvp_action:           str   = "warn"  # ignore/warn/pause/flee/logout
    inventory_check:      bool  = False   # periodic inventory status monitoring
    inventory_roi:        str   = ""      # "x,y,w,h" ROI for inventory area
    alert_enabled:        bool  = False   # enable Discord/Telegram alerts
    alert_discord_webhook: str = ""
    alert_telegram_token:  str = ""
    alert_telegram_chat:   str = ""
    session_stats:         bool  = True
    spawn_manager:         bool  = False
    adaptive_roi:          bool  = False
    dashboard:             bool  = False
    dashboard_port:        int   = 8080
    dashboard_ws_port:     int   = 8765
    dashboard_auth_token:  str   = ""
    break_scheduler:       bool  = False
    break_play_min:        float = 45.0
    break_play_max:        float = 90.0
    break_min:             float = 3.0
    break_max:             float = 15.0
    break_long_after_h:    float = 4.0
    break_long_min:        float = 10.0
    break_long_max:        float = 30.0
    auto_calibrate_roi:    bool  = False
    soak_monitor:          bool  = False
    soak_sample_interval:  float = 60.0
    soak_memory_warn_mb:   float = 1200.0
    gm_detector:           bool  = False
    gm_action:             str   = "pause"
    gm_scan_interval:      float = 5.0
    chat_responder:        bool  = False
    chat_response_delay_min: float = 3.0
    chat_response_delay_max: float = 8.0
    resume_waypoint_index: int   = 0
    arduino_enabled:       bool  = False
    arduino_port:          str   = "auto"
    pico_enabled:          bool  = False
    pico_port:             str   = "auto"

    def validate(self) -> None:
        if not 0 <= self.heal_hp_pct <= 100:
            raise ValueError("heal_hp_pct must be between 0 and 100")
        if not 0 <= self.heal_emergency_pct <= 100:
            raise ValueError("heal_emergency_pct must be between 0 and 100")
        if self.heal_hp_pct > 0 and self.heal_emergency_pct >= self.heal_hp_pct:
            raise ValueError("heal_emergency_pct must be less than heal_hp_pct")
        if not 0 <= self.mana_threshold_pct <= 100:
            raise ValueError("mana_threshold_pct must be between 0 and 100")
        if self.start_delay < 0:
            raise ValueError("start_delay must be >= 0")
        if self.step_interval <= 0:
            raise ValueError("step_interval must be > 0")
        if self.watchdog_timeout < 0:
            raise ValueError("watchdog_timeout must be >= 0")
        if not 0 <= self.jitter_pct <= 1:
            raise ValueError("jitter_pct must be between 0 and 1")

    def save(self, path: str | Path = SESSION_CONFIG_FILE) -> None:
        target = Path(path)
        target.write_text(json.dumps(dataclasses.asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path = SESSION_CONFIG_FILE) -> SessionConfig:
        target = Path(path)
        if not target.exists():
            return cls()

        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("session config must be a JSON object")

        valid_fields = {field.name for field in dataclasses.fields(cls)}
        filtered = {key: value for key, value in data.items() if key in valid_fields}
        cfg = cls(**filtered)
        cfg.validate()
        return cfg


class BotSession:
    """High-level session orchestrator for navigation and subsystems."""

    def __init__(
        self,
        cfg: Optional[SessionConfig] = None,
        *,
        config: Optional[SessionConfig] = None,
        loader: Optional[TibiaMapLoader] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        resolved_cfg = config if config is not None else cfg
        self._cfg = resolved_cfg or SessionConfig()
        self._log_cb: Callable[[str], None] = log_callback or _safe_log
        self._stop_lock = threading.Lock()        # protege: _running (lectura/escritura en start/stop/get/set_running)
        self._stats_lock = threading.Lock()       # protege: _stats dict (routes_completed, heal_fired, mana_fired, loot_events, start_time)
        self._position_lock = threading.Lock()    # protege: _position (Coordinate actual del personaje)
        self._loot_in_progress = threading.Event()  # señaliza que el Looter está activo; bloquea pasos de ruta hasta que termine
        self._event_bus = EventBus()
        self._startup_phase = "init"

        self._ctrl: Optional[Any] = None
        self._raw_ctrl: Optional[InputController] = None
        self._login_fn: Optional[Callable[[], bool]] = None
        self._arduino: Optional[Any] = None
        self._arduino_last_uptime_ms: Optional[int] = None
        self._pico: Optional[Any] = None

        self._loader: Optional[TibiaMapLoader] = loader
        self._navigator: Optional[WaypointNavigator] = None
        self._healer: Optional[AutoHealer] = None
        self._radar: Optional[MinimapRadar] = None
        self._local_reader: Optional[TibiaLocalMinimapReader] = None

        self._obstacle_analyzer: Optional[Any] = None
        self._depot: Optional[DepotManager] = None
        self._looter: Optional[Looter] = None
        self._combat: Optional[CombatManager] = None
        self._condition_monitor: Optional[ConditionMonitor] = None
        self._trade: Optional[TradeManager] = None

        self._frame_watchdog: Optional[FrameWatchdog] = None
        self._death_handler: Optional[DeathHandler] = None
        self._reconnect_handler: Optional[ReconnectHandler] = None
        self._anti_kick: Optional[AntiKick] = None
        self._stuck_det: Optional[StuckDetector] = None

        self._frame_quality: Optional[FrameQualityChecker] = None
        self._pos_resolver: Optional[PositionResolver] = None
        self._pvp_detector: Optional[PvPDetector] = None
        self._inventory_mgr: Optional[InventoryManager] = None
        self._alert_system: Optional[AlertSystem] = None
        self._spawn_mgr: Optional[SpawnManager] = None
        self._session_stats: Optional[HuntingSessionStats] = None
        self._adaptive_roi: Optional[AdaptiveROIDetector] = None
        self._dashboard: Optional[Any] = None
        self._depot_orch: Optional[DepotOrchestrator] = None
        self._break_scheduler: Optional[BreakScheduler] = None
        self._soak_monitor: Optional[SoakMonitor] = None
        self._gm_detector: Optional[GMDetector] = None
        self._chat_responder: Optional[ChatResponder] = None
        self._path_viz: Optional[Any] = None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._ww_thread: Optional[threading.Thread] = None
        self._ww_hwnds: dict[str, int] = {}
        self._last_move_time: float = 0.0
        self._pos_none_since: float = 0.0
        self._executor: Optional[Any] = None
        self._position: Optional[Coordinate] = None
        self._position_from_deadreckon: bool = False
        self._last_monitor_wpt: str = "—"
        self._consecutive_errors: int = 0
        self._frame_getter: Optional[Callable[[], Any]] = None
        self._frame_cache: Optional[FrameCache] = None
        self._stats: dict[str, Any] = {
            "routes_completed": 0,
            "heal_fired": 0,
            "mana_fired": 0,
            "loot_events": 0,
            "start_time": None,
        }

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._get_running()

    @property
    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return dict(self._stats)

    @property
    def config(self) -> SessionConfig:
        return self._cfg

    @property
    def has_healer(self) -> bool:
        """True when an :class:`AutoHealer` has been created for this session."""
        return self._healer is not None

    @property
    def has_navigator(self) -> bool:
        """True when a :class:`Navigator` has been created for this session."""
        return self._navigator is not None

    @property
    def has_loader(self) -> bool:
        """True when a :class:`TibiaMapLoader` is attached to this session."""
        return self._loader is not None

    @property
    def has_combat(self) -> bool:
        """True when a :class:`CombatManager` has been created for this session."""
        return self._combat is not None

    @property
    def has_condition_monitor(self) -> bool:
        """True when a :class:`ConditionMonitor` has been created for this session."""
        return self._condition_monitor is not None

    @property
    def has_trade_manager(self) -> bool:
        """True when a :class:`TradeManager` has been created for this session."""
        return self._trade is not None

    @property
    def has_frame_quality(self) -> bool:
        """True when a :class:`FrameQualityChecker` is active."""
        return self._frame_quality is not None

    @property
    def has_position_resolver(self) -> bool:
        """True when a :class:`PositionResolver` is active."""
        return self._pos_resolver is not None

    @property
    def has_pvp_detector(self) -> bool:
        """True when a :class:`PvPDetector` is active."""
        return self._pvp_detector is not None

    @property
    def has_inventory_manager(self) -> bool:
        """True when an :class:`InventoryManager` is active."""
        return self._inventory_mgr is not None

    @property
    def has_alert_system(self) -> bool:
        """True when an :class:`AlertSystem` is active."""
        return self._alert_system is not None

    @property
    def has_session_stats(self) -> bool:
        """True when a :class:`HuntingSessionStats` is tracking."""
        return self._session_stats is not None

    @property
    def has_spawn_manager(self) -> bool:
        """True when a :class:`SpawnManager` is active."""
        return self._spawn_mgr is not None

    @property
    def has_adaptive_roi(self) -> bool:
        """True when an :class:`AdaptiveROIDetector` is active."""
        return self._adaptive_roi is not None

    @property
    def has_gm_detector(self) -> bool:
        """True when a :class:`GMDetector` is active."""
        return self._gm_detector is not None

    @property
    def has_chat_responder(self) -> bool:
        """True when a :class:`ChatResponder` is active."""
        return self._chat_responder is not None

    @property
    def event_bus(self) -> EventBus:
        """The shared :class:`EventBus` for this session."""
        return self._event_bus

    @property
    def routes_completed(self) -> int:
        """Number of full route loops completed since the session started."""
        with self._stats_lock:
            return int(self._stats["routes_completed"])

    @property
    def has_started(self) -> bool:
        """True when the session has been started at least once (start_time set)."""
        with self._stats_lock:
            return self._stats["start_time"] is not None

    def _get_running(self) -> bool:
        with self._stop_lock:
            return self._running

    def _set_running(self, value: bool) -> None:
        with self._stop_lock:
            self._running = value

    def _get_position(self) -> Optional[Coordinate]:
        with self._position_lock:
            return self._position

    def _set_position(self, value: Optional[Coordinate]) -> None:
        with self._position_lock:
            self._position = value

    def _get_stat(self, key: str, default: Any = None) -> Any:
        with self._stats_lock:
            return self._stats.get(key, default)

    def _replace_stats(self, **updates: Any) -> None:
        with self._stats_lock:
            self._stats.update(updates)

    def _stats_copy(self) -> dict[str, Any]:
        with self._stats_lock:
            return dict(self._stats)

    def _has_cleanup_targets(self) -> bool:
        return any(
            component is not None
            for component in (
                self._executor,
                self._frame_watchdog,
                self._healer,
                self._looter,
                self._combat,
                self._condition_monitor,
                self._death_handler,
                self._reconnect_handler,
                self._anti_kick,
                self._stuck_det,
                self._session_stats,
                self._dashboard,
                self._break_scheduler,
                self._soak_monitor,
                self._gm_detector,
                self._chat_responder,
                self._alert_system,
                self._ctrl,
                self._pico,
                self._thread,
                self._watchdog_thread,
                self._ww_thread,
                self._path_viz,
            )
        )

    def reset_stats(self) -> None:
        """Reset all statistics counters (routes_completed, heals, etc.).

        ``start_time`` is intentionally preserved so that :attr:`has_started`
        remains True and elapsed-time calculations are not lost.
        """
        with self._stats_lock:
            self._stats.update({
                "routes_completed": 0,
                "heal_fired":       0,
                "mana_fired":       0,
                "loot_events":      0,
                # start_time is NOT reset — preserve session origin timestamp
            })

    # ── Frame / position helpers ────────────────────────────────────────────

    def set_frame_getter(self, fn: Callable[[], Any]) -> None:
        """Register an external frame getter ``() -> BGR ndarray | None``.

        Used by the minimap radar and DepotManager when
        ``position_source`` is set to ``'minimap'`` or ``'mss'``.
        Propagated automatically to all sub-components that accept one.

        The raw getter is wrapped in a :class:`FrameCache` (50 ms TTL)
        so that all subsystems share a single capture per cycle.
        """
        self._frame_getter = fn
        self._frame_cache = FrameCache(fn, ttl_ms=50.0)
        cached = self._frame_cache.get_frame
        for component in (
            self._depot,
            self._looter,
            self._combat,
            self._condition_monitor,
            self._healer,
            self._trade,
            self._death_handler,
            self._reconnect_handler,
        ):
            if component is not None and hasattr(component, "set_frame_getter"):
                component.set_frame_getter(cached)

    #: Maximum plausible jump (tiles) between consecutive position reads.
    #: Readings that jump further are rejected as false radar matches.
    _MAX_POS_JUMP: int = 10
    _MAX_POS_MANHATTAN_JUMP: int = 4

    def _update_position(self) -> bool:
        """Read current position from the radar or position resolver and cache it.

        Returns ``True`` when a **fresh** position was obtained, ``False``
        otherwise.  ``self._position`` is only mutated on success so that
        callers that need the *last-known* position can still use it.

        Rejects readings that jump more than ``_MAX_POS_JUMP`` tiles from
        the last known position to guard against false radar matches.
        """
        return update_session_position(session=self, logger=_log)

    def _check_frame_extras(self, frame: Any) -> None:
        """Run periodic PvP and inventory checks on the current frame.

        Called from the main loop after position update.  Each check is
        gated on the module being enabled (non-None).
        """
        check_frame_extras(session=self, frame=frame)

    def _get_real_position(self) -> "Optional[Coordinate]":
        """Return the most up-to-date character position.

        Returns the freshly-read coordinate when the radar (or resolver)
        succeeded, or ``None`` when no fresh reading could be obtained.
        Returning ``None`` lets the :class:`~src.script_executor.ScriptExecutor`
        fall through to its dead-reckoning path instead of comparing against
        a stale cached position and incorrectly deciding the character is
        blocked.
        """
        return get_real_position(update_position_fn=self._update_position, get_position_fn=self._get_position)

    def _set_position_from_executor(self, coord: "Coordinate") -> None:
        """Accept a dead-reckoned position from the executor.

        Keeps ``_position`` in sync so the radar jump guard in
        ``_update_position`` uses an up-to-date baseline instead of
        the stale seed position.

        Sets ``_position_from_deadreckon`` flag so that the next
        radar read can use a widened (or no) hint for reacquisition.
        """
        set_position_from_executor(session=self, coord=coord)

    def _stuck_repath(self) -> bool:
        """Legacy callback — rewind the active executor to the previous node."""
        if self._executor is not None and hasattr(self._executor, "request_replan"):
            return self._executor.request_replan()
        return False

    # ── Utility helpers ──────────────────────────────────────────────────────

    def uptime(self) -> Optional[float]:
        """Seconds elapsed since the session was started, or ``None`` if not running."""
        if not self._get_running():
            return None
        start_time = self._get_stat("start_time")
        if start_time is None:
            return None
        return float(time.time() - start_time)

    def stats_summary(self) -> str:
        """Return a human-readable one-line summary of the current stats."""
        s = self._stats_copy()
        up = self.uptime()
        uptime_str = f"{up:.0f}s" if up is not None else "stopped"
        return (
            f"uptime={uptime_str}  routes={s['routes_completed']}  "
            f"heals={s['heal_fired']}  mana={s['mana_fired']}  "
            f"loot={s['loot_events']}"
        )

    def stats_snapshot(self) -> dict[str, Any]:
        """Return a copy of the current stats dict with extra derived fields."""
        snap = self._stats_copy()
        snap["uptime_secs"]  = self.uptime()
        snap["is_running"]   = self._get_running()
        snap["cycle_count"]  = snap["routes_completed"]
        snap["waypoints_visited"] = snap["loot_events"]  # best available proxy
        if self._break_scheduler is not None:
            snap["break_scheduler"] = self._break_scheduler.stats_snapshot()
        if self._soak_monitor is not None:
            snap["soak_monitor"] = self._soak_monitor.stats_snapshot()
        return snap

    def current_position(self, *, allow_route_seed: bool = False) -> Optional[Coordinate]:
        """Return the best available position for monitor/dashboard consumers."""
        executor = self._executor
        live_pos = self._coerce_coordinate(
            getattr(executor, "_current_pos", None) if executor is not None else None,
        )
        if live_pos is not None:
            self._set_position(live_pos)
            return live_pos

        cached_pos = self._get_position()
        if cached_pos is not None or not allow_route_seed:
            return cached_pos

        seeded_pos = self._route_seed_position()
        if seeded_pos is not None:
            self._set_position(seeded_pos)
        return seeded_pos

    def active_instructions(self) -> list[Any]:
        """Return the executor instruction list, if one is active."""
        executor = self._executor
        if executor is None:
            return []
        instructions = getattr(executor, "_instructions", None) or []
        return list(instructions)

    def monitor_loader(self) -> Optional[TibiaMapLoader]:
        """Return a map loader for monitor rendering, creating one on demand."""
        if self._loader is not None:
            return self._loader
        try:
            self._loader = TibiaMapLoader()
        except Exception:
            _log.debug("monitor loader init failed", exc_info=True)
            return None
        return self._loader

    def set_targeting_enabled(self, enabled: bool) -> None:
        """Pause or resume combat automation for the monitor UI."""
        combat = self._combat
        if combat is None:
            return
        action = "resume" if enabled else "pause"
        getattr(combat, action, lambda: None)()

    def set_walking_enabled(self, enabled: bool) -> None:
        """Pause or resume route walking for the monitor UI."""
        executor = self._executor
        if executor is None:
            return
        executor._walking_paused = not enabled

    def set_looting_enabled(self, enabled: bool) -> None:
        """Pause or resume looting for the monitor UI."""
        looter = self._looter
        if looter is None:
            return
        action = "resume" if enabled else "pause"
        getattr(looter, action, lambda: None)()

    def monitor_snapshot(self) -> dict[str, Any]:
        """Return a UI-oriented snapshot shared by monitor and dashboard."""
        snap = self.stats_snapshot()
        position = self.current_position(allow_route_seed=True)
        snap["uptime_seconds"] = snap.get("uptime_secs")
        snap["route"] = self._monitor_route_name()
        snap["route_name"] = snap["route"]
        snap["position"] = self._coordinate_to_dict(position)
        snap["current_wpt"] = self._monitor_current_wpt(
            is_running=bool(snap.get("is_running")),
        )

        break_info = self._monitor_break_info(snap.get("break_scheduler"))
        if break_info is not None:
            snap["break_info"] = break_info

        soak_mem = self._monitor_soak_mem(snap.get("soak_monitor"))
        if soak_mem is not None:
            snap["soak_mem"] = soak_mem
        return snap

    @staticmethod
    def _coerce_coordinate(value: Any) -> Optional[Coordinate]:
        if value is None:
            return None
        if isinstance(value, Coordinate):
            return value
        if isinstance(value, dict):
            try:
                return Coordinate(
                    x=int(value["x"]),
                    y=int(value["y"]),
                    z=int(value["z"]),
                )
            except Exception:
                return None
        if all(hasattr(value, attr) for attr in ("x", "y", "z")):
            try:
                return Coordinate(
                    x=int(getattr(value, "x")),
                    y=int(getattr(value, "y")),
                    z=int(getattr(value, "z")),
                )
            except Exception:
                return None
        return None

    @staticmethod
    def _coordinate_to_dict(coord: Optional[Coordinate]) -> Optional[dict[str, int]]:
        if coord is None:
            return None
        return {"x": coord.x, "y": coord.y, "z": coord.z}

    def _route_seed_position(self) -> Optional[Coordinate]:
        route_file = getattr(self._cfg, "route_file", "")
        if not route_file:
            return None
        try:
            route_path = _resolve_route(route_file)
        except FileNotFoundError:
            return None
        try:
            route_data = json.loads(route_path.read_text(encoding="utf-8"))
        except Exception:
            _log.debug("monitor route seed read failed", exc_info=True)
            return None
        return self._coerce_coordinate(self._extract_route_start_coord(route_data))

    @staticmethod
    def _extract_route_start_coord(route_data: Any) -> Optional[dict[str, Any]]:
        if not isinstance(route_data, dict):
            return None

        start_coord = route_data.get("_meta", {}).get("start_coord")
        if isinstance(start_coord, dict):
            return start_coord

        start_coord = route_data.get("start")
        if isinstance(start_coord, dict):
            return start_coord

        waypoints = route_data.get("waypoints", [])
        if waypoints and isinstance(waypoints[0], dict):
            return waypoints[0]
        return None

    def _monitor_route_name(self) -> str:
        route_file = getattr(self._cfg, "route_file", "")
        if not route_file:
            return "—"
        return Path(route_file).name

    def _monitor_current_wpt(self, *, is_running: bool) -> str:
        executor = self._executor
        instruction = getattr(executor, "_current_instr", None) if executor is not None else None
        if instruction is None:
            if not is_running:
                self._last_monitor_wpt = "—"
            return self._last_monitor_wpt

        movement_label = self._monitor_movement_wpt(instruction)
        if movement_label is not None:
            self._last_monitor_wpt = movement_label
            return movement_label

        self._last_monitor_wpt = self._monitor_action_wpt(instruction)
        return self._last_monitor_wpt

    @staticmethod
    def _monitor_movement_wpt(instruction: Any) -> Optional[str]:
        coord = getattr(instruction, "coord", None)
        normalized_coord = BotSession._coerce_coordinate(coord)
        if normalized_coord is None:
            return None

        kind = getattr(instruction, "kind", "?")
        return (
            f"[{kind}  "
            f"{normalized_coord.x},"
            f"{normalized_coord.y},"
            f"{normalized_coord.z}]"
        )

    @staticmethod
    def _monitor_action_wpt(instruction: Any) -> str:
        kind = getattr(instruction, "kind", "?")
        action = getattr(instruction, "action", None) or kind
        extra = ""
        if kind == "wait":
            extra = f" {getattr(instruction, 'wait_secs', '')}s"
        elif kind == "goto":
            extra = f" → {getattr(instruction, 'label_jump', '')}"
        elif kind == "label":
            extra = f" :{getattr(instruction, 'label', '')}"
        elif kind == "if_stat":
            extra = (
                f" {getattr(instruction, 'stat', '')}"
                f"{getattr(instruction, 'op', '')}"
                f"{getattr(instruction, 'threshold', '')}"
            )
        elif kind == "use_item":
            extra = f" {getattr(instruction, 'item_name', '')}"
        elif kind == "talk_npc":
            words = getattr(instruction, "words", [])
            extra = f" {words[0]!r}" if words else ""
        return f"[{action}{extra}]"

    @staticmethod
    def _monitor_break_info(break_scheduler: Any) -> Optional[str]:
        if not isinstance(break_scheduler, dict):
            return None
        if break_scheduler.get("on_break"):
            return "⏸ ON BREAK"
        next_break = break_scheduler.get("next_break_in_m", 0)
        breaks_taken = break_scheduler.get("breaks_taken", 0)
        return f"next {next_break:.0f}m | #{breaks_taken}"

    @staticmethod
    def _monitor_soak_mem(soak_monitor: Any) -> Optional[str]:
        if not isinstance(soak_monitor, dict):
            return None
        latest = soak_monitor.get("latest", {})
        rss_mb = latest.get("rss_mb", 0)
        peak_memory = soak_monitor.get("peak_memory_mb", 0)
        if rss_mb:
            return f"{rss_mb:.0f}MB (peak {peak_memory:.0f})"
        if peak_memory:
            return f"peak {peak_memory:.0f}MB"
        return None

    def set_login_fn(self, fn: "Callable[[], bool]") -> None:
        """Register a login callback for the ReconnectHandler.

        The callback must return True when the Tibia login succeeds and
        False (or raise) when it fails.  Call this before ``start()`` or
        at any time while running — the handler accepts updates mid-session.
        """
        if self._reconnect_handler is not None:
            self._reconnect_handler.set_login_fn(fn)
        # Store so it can be wired when start() initialises the handler later
        self._login_fn = fn

    def update_config(self, config: SessionConfig) -> None:
        """Hot-swap the session configuration.

        Takes effect on the *next* start() call.  Does **not** restart a
        currently running session.
        """
        self._cfg = config

    # ── Start / stop ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Connect to the Tibia window, start the healer thread, then begin
        the navigation loop in a background thread.
        """
        startup_start_session(
            session=self,
            set_jitter_fn=set_jitter,
            reset_fatigue_fn=reset_fatigue,
            run_preflight_fn=run_preflight,
            templates_base=Path(__file__).parent.parent / "cache" / "templates",
        )

    def _startup_subsystems(self) -> None:
        """Initialise all subsystems (called from start())."""
        startup_startup_subsystems(session=self)

    def _init_input(self) -> None:
        startup_init_input(
            session=self,
            input_controller_cls=InputController,
            his_available=_HIS_AVAILABLE,
            his_cls=_HIS if _HIS_AVAILABLE else None,
            his_config_path=Path(__file__).resolve().parent.parent / "human_input_system" / "config.yaml",
            arduino_available=_ARDUINO_AVAILABLE,
            arduino_config_cls=_ArduinoConfig if _ARDUINO_AVAILABLE else None,
            arduino_hid_cls=_ArduinoHID if _ARDUINO_AVAILABLE else None,
            pico_available=_PICO_AVAILABLE,
            pico_config_cls=_PicoConfig if _PICO_AVAILABLE else None,
            pico_hid_cls=_PicoHID if _PICO_AVAILABLE else None,
        )

    def _init_navigation(self) -> None:
        startup_init_navigation(session=self, map_loader_cls=TibiaMapLoader, navigator_cls=WaypointNavigator)

    def _init_healer(self) -> None:
        startup_init_healer(session=self, heal_config_cls=HealConfig, auto_healer_cls=AutoHealer)

    def _parse_start_pos(self) -> Optional[Coordinate]:
        if not self._cfg.start_pos:
            return None
        try:
            parts = [int(v.strip()) for v in self._cfg.start_pos.split(",")]
            if len(parts) < 3:
                raise ValueError(f"need X,Y,Z; got {len(parts)} values")
            return Coordinate(x=parts[0], y=parts[1], z=parts[2])
        except Exception as exc:
            _log.error("Invalid start_pos '%s': %s", self._cfg.start_pos, exc, exc_info=exc)
            self._log(f"[!] Invalid start_pos '{self._cfg.start_pos}': {exc}")
            return None

    def _is_start_position_locked(
        self,
        actual: Optional[Coordinate],
        expected: Optional[Coordinate],
    ) -> bool:
        if actual is None or expected is None:
            return False
        if actual.z != expected.z:
            return False
        return actual.manhattan_to(expected) <= self._cfg.startup_position_tolerance

    def _wait_for_start_position_lock(self) -> bool:
        expected = self._parse_start_pos()
        if expected is None or self._cfg.position_source != "minimap" or self._radar is None:
            return True

        if self._is_start_position_locked(self._position, expected):
            self._log(
                f"[S] Start-pos lock OK: actual={self._position} expected={expected} "
                f"tol={self._cfg.startup_position_tolerance}"
            )
            return True

        timeout_s = self._cfg.startup_position_timeout
        deadline = time.monotonic() + timeout_s
        last_seen = self._position
        self._log(
            f"[S] Waiting for start-pos lock: expected={expected} "
            f"tol={self._cfg.startup_position_tolerance} timeout={timeout_s:.1f}s"
        )

        while time.monotonic() <= deadline:
            self._update_position()
            if self._position is not None:
                last_seen = self._position
            if self._is_start_position_locked(self._position, expected):
                self._log(
                    f"[S] Start-pos lock OK: actual={self._position} expected={expected} "
                    f"tol={self._cfg.startup_position_tolerance}"
                )
                return True
            if time.monotonic() >= deadline:
                break
            time.sleep(random.uniform(0.2, 0.45))

        self._log(
            f"[S] Start-pos lock FAILED: expected={expected} last={last_seen} "
            f"tol={self._cfg.startup_position_tolerance} timeout={timeout_s:.1f}s"
        )
        return False

    def _init_capture(self) -> Any:
        _cached = startup_init_capture(
            session=self,
            initialize_capture_pipeline_fn=initialize_capture_pipeline,
            build_frame_getter_fn=_build_frame_getter,
        )

        _radar_ok = False
        if self._cfg.position_source == "minimap" and self._frame_getter is not None:
            if self._loader is None:
                self._loader = TibiaMapLoader(log_fn=self._log)
            self._radar = MinimapRadar(loader=self._loader)
            self._log("MinimapRadar position source enabled.")

            # 4a-cal. Run calibrator FIRST to detect the correct tiles_wide
            # before attempting any radar reads.  The config file may have a
            # stale tiles_wide that doesn't match the current minimap zoom.
            if _cached:
                self._startup_phase = "calibration"
                try:
                    from .minimap_calibrator import MinimapCalibrator
                    _hint_pos = self._parse_start_pos()
                    _cal = MinimapCalibrator(
                        loader=self._loader,
                        floor=_hint_pos.z if _hint_pos else 7,
                        hint=_hint_pos,
                    )
                    _cal_frame = _cached()
                    if _cal_frame is not None:
                        _cr = _cal.calibrate(_cal_frame)
                        if _cr.success:
                            _tw = _cr.config.tiles_wide
                            self._radar._cfg.tiles_wide = _tw
                            self._radar._cfg.roi = _cr.config.roi
                            self._log(f"[S] Calibrator: tw={_tw}, "
                                      f"score={_cr.best_score:.3f}")
                            if _cr.position is not None:
                                self._radar._last_coord = _cr.position
                                self._position = _cr.position
                                self._log(f"[S] Position from calibrator: "
                                          f"{_cr.position}")
                        else:
                            self._log(f"[S] Calibration failed "
                                      f"(score={_cr.best_score:.3f})")
                    else:
                        self._log("[S] Calibration skipped — no frame.")
                except Exception as _ce:
                    _log.error("Minimap calibration error during startup", exc_info=_ce)
                    self._log(f"[S] Calibration error: {_ce}")

            # Seed the radar with --start-pos so the first read uses a
            # constrained search area instead of scanning the full map.
            _seed = self._parse_start_pos()
            if _seed is not None:
                self._position = _seed
                self._radar._last_coord = _seed
                self._log(f"[S] Radar seeded with --start-pos: {_seed}")
            # Startup radar validation: retry a few times since WGC may need
            # a moment before delivering the first frame.
            _seed_pos = self._position  # may be set by --start-pos or calibrator
            for _attempt in range(10):
                self._update_position()
                if self._position is not None and self._position != _seed_pos:
                    break
                if _attempt == 0:
                    _diag_frame = (
                        self._frame_cache.get_frame()
                        if self._frame_cache
                        else self._frame_getter()
                    )
                    if _diag_frame is None:
                        self._log("[S] Radar diag: frame is None")
                    else:
                        self._log(
                            f"[S] Radar diag: frame {_diag_frame.shape[1]}x"
                            f"{_diag_frame.shape[0]}, "
                            f"radar tw={self._radar._cfg.tiles_wide}, "
                            f"stats={self._radar.stats()}"
                        )
                time.sleep(random.uniform(0.2, 0.45))
            if self._position is not None:
                _radar_ok = True
                self._log(f"[S] Radar startup check OK: {self._position}")
            else:
                self._log(
                    "[S] ⚠ Radar startup check FAILED — "
                    "minimap position could not be read. Navigation "
                    "will rely on dead-reckoning!"
                )

        # 4a-zoom. Try I/O keypresses to correct minimap zoom (best-effort).
        if _radar_ok and _cached and self._raw_ctrl is not None and self._raw_ctrl.is_connected():
            self._startup_phase = "zoom_guard"
            try:
                from .minimap_calibrator import ensure_minimap_zoom
                _hint = self._position if self._position else None
                _zr = ensure_minimap_zoom(
                    frame_getter=_cached,
                    ctrl=self._raw_ctrl,
                    loader=self._loader,
                    floor=self._position.z if self._position else 7,
                    hint=_hint,
                    log_fn=self._log,
                )
                if _zr and _zr.success:
                    _tw = _zr.config.tiles_wide
                    self._log(f"[S] Zoom guard: tw={_tw}, "
                              f"score={_zr.best_score:.3f}")
                    # Always apply calibrated tw — the radar works at any
                    # zoom level as long as tiles_wide matches reality.
                    if self._radar is not None:
                        self._radar._cfg.tiles_wide = _tw
                    if _zr.position is not None:
                        if self._radar is not None:
                            self._radar._last_coord = _zr.position
                            self._radar.reset_stats()
                        self._position = _zr.position
                        self._log(f"[S] Position re-seeded from zoom guard: "
                                  f"{_zr.position}")
            except Exception as _ze:
                _log.error("Minimap zoom guard failed during startup", exc_info=_ze)
                self._log(f"[S] Zoom guard failed ({_ze}) — continuing anyway.")

        return _cached

    def _init_optional_subsystems(self, _cached: Any) -> None:
        startup_init_optional_subsystems(
            session=self,
            cached_getter=_cached,
            initialize_optional_subsystems_fn=initialize_optional_subsystems,
            session_events=SessionEvent,
            depot_manager_cls=DepotManager,
            looter_cls=Looter,
            combat_manager_cls=CombatManager,
            combat_config_cls=CombatConfig,
            hpmp_detector_cls=HpMpDetector,
            condition_monitor_cls=ConditionMonitor,
            condition_config_cls=ConditionConfig,
            trade_manager_cls=TradeManager,
            trade_config_cls=TradeConfig,
        )

    def _init_safety_handlers(self, _cached: Any) -> None:
        startup_init_safety_handlers(
            session=self,
            cached_getter=_cached,
            initialize_safety_handlers_fn=initialize_safety_handlers,
            death_handler_cls=DeathHandler,
            death_config_cls=DeathConfig,
            reconnect_handler_cls=ReconnectHandler,
            reconnect_config_cls=ReconnectConfig,
            anti_kick_cls=AntiKick,
            anti_kick_config_cls=AntiKickConfig,
            stuck_detector_cls=StuckDetector,
        )

    def _init_integrated_modules(self) -> None:
        startup_init_integrated_modules(
            session=self,
            initialize_integrated_modules_fn=initialize_integrated_modules,
            frame_quality_checker_cls=FrameQualityChecker,
            position_resolver_cls=PositionResolver,
            position_resolver_config_cls=PositionResolverConfig,
            source_kind_cls=SourceKind,
            tibia_local_minimap_reader_cls=TibiaLocalMinimapReader,
            pvp_detector_cls=PvPDetector,
            pvp_config_cls=PvPConfig,
            pvp_action_cls=PvPAction,
            inventory_manager_cls=InventoryManager,
            inventory_config_cls=InventoryConfig,
            resupply_config_cls=ResupplyConfig,
            depot_orchestrator_cls=DepotOrchestrator,
        )

    def _init_monitoring(self, _cached: Any) -> None:
        startup_init_monitoring(
            session=self,
            cached_getter=_cached,
            initialize_monitoring_fn=initialize_monitoring,
            alert_system_cls=AlertSystem,
            alert_config_cls=AlertConfig,
            session_stats_cls=HuntingSessionStats,
            spawn_manager_cls=SpawnManager,
            adaptive_roi_detector_cls=AdaptiveROIDetector,
            break_scheduler_cls=BreakScheduler,
            break_scheduler_config_cls=BreakSchedulerConfig,
            soak_monitor_cls=SoakMonitor,
            soak_monitor_config_cls=SoakMonitorConfig,
            gm_detector_cls=GMDetector,
            gm_detector_config_cls=GMDetectorConfig,
            gm_action_cls=GMAction,
            chat_responder_cls=ChatResponder,
            chat_responder_config_cls=ChatResponderConfig,
        )

    def _start_threads(self) -> None:
        from src.input_controller import find_window as _ww_find

        startup_start_threads(
            session=self,
            start_session_threads_fn=start_session_threads,
            find_window_fn=_ww_find,
            thread_cls=threading.Thread,
            monotonic_fn=time.monotonic,
            sleep_fn=time.sleep,
            random_uniform_fn=random.uniform,
        )

    # ── Window-state watchdog ────────────────────────────────────────────

    def _window_watchdog_loop(self) -> None:
        """Background loop: restore Tibia / Proyector if minimized.

        Also detects when the Tibia window is closed/destroyed and stops
        the session automatically.
        """
        run_window_watchdog_loop(
            is_running=lambda: self._running,
            window_handles=self._ww_hwnds,
            log_fn=self._log,
            stop_session=self.stop,
            sleep_fn=time.sleep,
            debug_fn=lambda: _log.debug("window watchdog restore failed", exc_info=True),
        )

    def stop(self, *, force_cleanup: bool = False) -> None:
        """Signal the session to stop and wait for the background thread."""
        with self._stop_lock:
            if not self._running:
                if not force_cleanup:
                    return
            self._running = False
        self._log("Stopping session …")

        perform_session_shutdown(
            executor=self._executor,
            stoppable_components=[
                ("frame_watchdog", self._frame_watchdog),
                ("healer", self._healer),
                ("looter", self._looter),
                ("combat", self._combat),
                ("condition_monitor", self._condition_monitor),
                ("death_handler", self._death_handler),
                ("reconnect_handler", self._reconnect_handler),
                ("anti_kick", self._anti_kick),
                ("stuck_detector", self._stuck_det),
                ("session_stats", self._session_stats),
                ("dashboard", self._dashboard),
                ("break_scheduler", self._break_scheduler),
                ("soak_monitor", self._soak_monitor),
                ("gm_detector", self._gm_detector),
                ("chat_responder", self._chat_responder),
                ("alert_system", self._alert_system),
            ],
            ctrl=self._ctrl,
            pico=self._pico,
            main_thread=self._thread,
            watchdog_thread=self._watchdog_thread,
            window_watchdog_thread=self._ww_thread,
            stats=self._stats_copy(),
            session_stats=self._session_stats,
            path_viz=self._path_viz,
            log_fn=self._log,
            save_stats_fn=self._save_stats,
            save_checkpoint_fn=self._save_checkpoint,
        )

    # ── Checkpoint persistence ────────────────────────────────────────────────

    def _save_checkpoint(
        self,
        waypoint_index: int = 0,
        *,
        route_file: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Persist progress for crash recovery / resume."""
        try:
            pos = self._position
            pos = self._get_position()
            ckpt = SessionCheckpoint(
                route_file=route_file or self._cfg.route_file,
                waypoint_index=waypoint_index,
                position_x=pos.x if pos else 0,
                position_y=pos.y if pos else 0,
                position_z=pos.z if pos else 7,
                routes_completed=self._get_stat("routes_completed", 0),
                heal_fired=self._get_stat("heal_fired", 0),
                mana_fired=self._get_stat("mana_fired", 0),
                loot_events=self._get_stat("loot_events", 0),
                uptime_seconds=(
                    time.time() - self._get_stat("start_time")
                    if self._get_stat("start_time")
                    else 0.0
                ),
                extra=extra or {},
            )
            ckpt.save()
        except Exception as exc:
            _log.error("Checkpoint save failed", exc_info=exc)
            self._log(f"  ⚠ checkpoint save failed: {exc}")

    @staticmethod
    def load_checkpoint() -> Optional[SessionCheckpoint]:
        """Load the last saved checkpoint from disk (for resume)."""
        return SessionCheckpoint.load()

    @staticmethod
    def clear_checkpoint() -> None:
        """Delete the checkpoint file (after a clean run)."""
        SessionCheckpoint.clear()

    # ── Navigation helpers ───────────────────────────────────────────────────

    def navigate_to(
        self,
        start: Coordinate,
        end: Coordinate,
        multifloor: bool = False,
    ) -> List[Route]:
        """
        Navigate from *start* to *end*, returning the list of Route objects.

        Parameters
        ----------
        start, end : Coordinate
        multifloor : bool
            Use multifloor Dijkstra routing when floors differ.
        """
        if self._navigator is None:
            _loader = self._loader or TibiaMapLoader(log_fn=self._log)
            self._navigator = WaypointNavigator()
            self._navigator.loader = _loader
        if multifloor or start.z != end.z:
            return self._navigator.navigate_multifloor(start, end)
        route = self._navigator.navigate(start, end)
        return [route]

    def load_waypoints(self, path: str | Path) -> List[Waypoint]:
        """Load waypoints from a JSON file and return the list.

        If *path* does not exist as-is it is looked up under the project's
        ``routes/`` directory automatically.
        """
        p = _resolve_route(path)
        route_identity = str(p)
        route_identity = str(p)
        route_identity = str(p)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        waypoints: List[Waypoint] = []
        items = data if isinstance(data, list) else data.get("waypoints", [])
        for entry in items:
            if isinstance(entry, dict):
                coord = Coordinate(
                    entry.get("x", 0),
                    entry.get("y", 0),
                    entry.get("z", 7),
                )
                wp = Waypoint(
                    name=entry.get("name", ""),
                    coord=coord,
                )
                waypoints.append(wp)
        return waypoints

    # ── Healer shortcut ──────────────────────────────────────────────────────

    def set_hp_mp(self, hp_pct: int, mp_pct: int) -> None:
        """
        Inject HP/MP values directly into the healer (useful for tests or
        semi-manual control).
        """
        if self._healer:
            self._healer._hp_pct = hp_pct
            self._healer._mp_pct = mp_pct

    def force_heal(self) -> bool:
        """Fire the heal hotkey immediately, bypassing the cooldown."""
        return self._healer.force_heal() if self._healer else False

    def force_mana(self) -> bool:
        """Fire the mana hotkey immediately, bypassing the cooldown."""
        return self._healer.force_mana() if self._healer else False

    def run_script(self, path: str | Path) -> None:
        """
        Parse and execute a ``.in`` script file using
        :class:`~src.script_executor.ScriptExecutor`.

        The executor is wired to the session's existing
        ``ctrl``, ``navigator``, ``healer``, ``depot``, and ``frame_getter``.
        If ``SessionConfig.dry_run`` is True, no real input is sent.

        Parameters
        ----------
        path : str or Path
            Path to the ``.in`` script file.

        Raises
        ------
        FileNotFoundError
            When the script file does not exist.
        """
        from .script_parser import ScriptParser
        from .script_executor import ScriptExecutor

        run_session_script(
            path=path,
            config=self._cfg,
            ctrl=self._ctrl,
            navigator=self._navigator,
            healer=self._healer,
            frame_getter=self._frame_getter,
            depot=self._depot,
            combat=self._combat,
            radar=self._radar,
            stuck_detector=self._stuck_det,
            obstacle_analyzer=self._obstacle_analyzer,
            loader=self._loader,
            looter=self._looter,
            get_position=self._get_position,
            set_position=self._set_position,
            get_real_position_fn=self._get_real_position,
            set_position_from_executor_fn=self._set_position_from_executor,
            npc_handler_factory=self._make_npc_handler,
            log_fn=self._log,
            get_executor=lambda: self._executor,
            set_executor=lambda value: setattr(self, "_executor", value),
            get_path_viz=lambda: self._path_viz,
            set_path_viz=lambda value: setattr(self, "_path_viz", value),
            load_checkpoint_fn=self.load_checkpoint,
            save_checkpoint_fn=self._save_checkpoint,
            clear_checkpoint_fn=self.clear_checkpoint,
            align_script_start_index_fn=self._align_script_start_index,
            collect_route_critical_tiles_fn=self._collect_route_critical_tiles,
            resolve_route_fn=_resolve_route,
            parse_file_fn=ScriptParser.parse_file,
            json_script_parser=ScriptParser.from_json_script,
            script_executor_cls=ScriptExecutor,
            force_align=self._position is not None and self._navigator is not None,
        )

    def _align_script_start_index(
        self,
        *,
        instructions: list[Any],
        start_index: int,
    ) -> int:
        return align_script_start_index(
            instructions=instructions,
            start_index=start_index,
            position=self._position,
            navigator=self._navigator,
            log_fn=self._log,
            debug_fn=lambda segment_start, segment_end: _log.debug(
                "auto-align route probe failed: %s -> %s",
                segment_start,
                segment_end,
                exc_info=True,
            ),
        )

    def _collect_route_critical_tiles(
        self,
        instructions: list[Any],
    ) -> set[tuple[int, int, int]]:
        return collect_route_critical_tiles(
            instructions=instructions,
            navigator=self._navigator,
            debug_fn=lambda segment_start, segment_end: _log.debug(
                "critical-tile route probe failed: %s -> %s",
                segment_start,
                segment_end,
                exc_info=True,
            ),
        )

    @staticmethod
    def _script_movement_points(
        instructions: list[Any],
    ) -> list[tuple[int, Coordinate]]:
        return script_movement_points(instructions)

    def open_monitor(self, config: Optional[Any] = None) -> None:
        """
        Open a Tkinter monitor window and block until it is closed.

        Must be called from the **main thread**.

        Parameters
        ----------
        config : MonitorConfig, optional
            Appearance / update-rate overrides.  Defaults are used when
            *None*.
        """
        from .monitor_gui import MonitorGui
        gui = MonitorGui(session=self, config=config)
        # Wire session log → GUI log widget so all messages are visible live.
        _prev_log = self._log_cb
        def _gui_log(msg: str) -> None:
            _prev_log(msg)
            try:
                gui.append_log(msg)
            except Exception as _e:
                _log.debug("GUI append_log failed (ignorado): %s", _e)
        self._log_cb = _gui_log
        try:
            gui.run()
        finally:
            self._log_cb = _prev_log

    # ── Internal ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main navigation loop (runs in background thread)."""
        run_session_loop(
            config=self._cfg,
            inc_routes_fn=lambda: self._inc_stat("routes_completed"),
            event_bus=self._event_bus,
            break_scheduler=self._break_scheduler,
            depot=self._depot,
            depot_orchestrator=self._depot_orch,
            ctrl=self._ctrl,
            frame_getter=self._frame_getter,
            get_position=self._get_position,
            is_running=self._get_running,
            set_running=self._set_running,
            get_consecutive_errors=lambda: self._consecutive_errors,
            set_consecutive_errors=lambda value: setattr(self, "_consecutive_errors", value),
            log_fn=self._log,
            resolve_route_fn=_resolve_route,
            run_script_fn=self.run_script,
            load_waypoints_fn=self.load_waypoints,
            navigate_to_fn=self.navigate_to,
            exec_route_fn=self._exec_route,
            exec_multifloor_fn=self._exec_multifloor,
            save_checkpoint_fn=self._save_checkpoint,
            pause_subsystems=self._pause_subsystems,
            resume_subsystems=self._resume_subsystems,
            jittered_sleep_fn=jittered_sleep,
            format_traceback_fn=traceback.format_exc,
            sleep_fn=time.sleep,
            random_uniform_fn=random.uniform,
            spawn_manager=self._spawn_mgr,
        )

    def _watchdog_loop(self) -> None:
        """Background thread: alert when no movement has occurred for watchdog_timeout seconds.
        Also checks subsystem thread health every cycle (T6).
        """
        run_watchdog_loop(
            timeout=self._cfg.watchdog_timeout,
            is_running=self._get_running,
            sleep_fn=time.sleep,
            check_subsystem_health_fn=self._check_subsystem_health,
            get_position=self._get_position,
            get_pos_none_since=lambda: self._pos_none_since,
            set_pos_none_since=lambda value: setattr(self, "_pos_none_since", value),
            get_last_move_time=lambda: self._last_move_time,
            set_last_move_time=lambda value: setattr(self, "_last_move_time", value),
            event_bus=self._event_bus,
            log_fn=self._log,
            monotonic_fn=time.monotonic,
        )

    def _check_subsystem_health(self) -> None:
        """T6: Verify daemon threads are alive; restart any that crashed."""
        self._arduino_last_uptime_ms = check_subsystem_health(
            healer=self._healer,
            combat=self._combat,
            looter=self._looter,
            death_handler=self._death_handler,
            reconnect_handler=self._reconnect_handler,
            anti_kick=self._anti_kick,
            stuck_detector=self._stuck_det,
            loot_in_progress=self._loot_in_progress,
            arduino=self._arduino,
            arduino_last_uptime_ms=self._arduino_last_uptime_ms,
            event_bus=self._event_bus,
            log_fn=self._log,
            stop_session=self.stop,
        )

    def _exec_route(self, route: Route) -> None:
        """
        Walk each step of a single Route using the InputController.
        Aborts mid-route if stop() is called.

        Uses ``verify_position_changed`` from action_verifier every 3 steps
        to confirm the character actually moved (closed-loop feedback).
        """
        execute_route(
            route=route,
            ctrl=self._ctrl,
            config=self._cfg,
            is_running=self._get_running,
            log_fn=self._log,
            loot_in_progress=self._loot_in_progress,
            set_last_move_time=lambda value: setattr(self, "_last_move_time", value),
            anti_kick=self._anti_kick,
            radar=self._radar,
            frame_getter=self._frame_getter,
            get_position=self._get_position,
            update_position_fn=self._update_position,
            check_frame_extras_fn=self._check_frame_extras,
            monotonic_fn=time.monotonic,
            sleep_fn=time.sleep,
            random_uniform_fn=random.uniform,
            jittered_sleep_fn=jittered_sleep,
            macro_pause_fn=macro_pause,
            verify_position_changed_fn=verify_position_changed,
        )

    # ── Death recovery navigation ────────────────────────────────────────

    def _navigate_back_to(self, target: Any) -> bool:
        """Walk from the current position to *target* (Coordinate).

        Used by DeathHandler to return the character to the pre-death
        location after re-equip.  Returns True on success.
        """
        return navigate_back_to(
            target=target,
            ctrl=self._ctrl,
            log_fn=self._log,
            update_position_fn=self._update_position,
            get_position=self._get_position,
            navigate_to_fn=self.navigate_to,
            exec_route_fn=self._exec_route,
            is_running=self._get_running,
        )

    def _walk_route(self, route: Any) -> bool:
        """Execute a single Route tile-by-tile.  Used by DepotOrchestrator."""
        return walk_route(
            route=route,
            ctrl=self._ctrl,
            exec_route_fn=self._exec_route,
            is_running=self._get_running,
            log_fn=self._log,
        )

    def _click_character_tile(self) -> None:
        """Click on the character's tile (center of game viewport).

        Used after pressing a shovel/rope crosshair hotkey to complete
        the 'use with crosshair' action on the tile the character stands on.
        """
        click_character_tile(
            ctrl=self._ctrl,
            frame_getter=self._frame_getter,
            debug_fn=lambda: _log.debug("click_character_tile frame_getter failed", exc_info=True),
        )

    def _exec_transition(self, transition: FloorTransition) -> None:
        """
        Execute the physical action required to cross a floor transition.

        - ``walk``   → already on the tile; Tibia teleports automatically.
        - ``ladder`` → step onto the ladder tile (no extra key required).
        - ``rope``   → press rope hotkey (``SessionConfig.rope_hotkey_vk``).
        - ``shovel`` → press shovel hotkey (``SessionConfig.shovel_hotkey_vk``).
        - ``use``    → press the use/interact key (falls back to rope_hotkey_vk).

        A short ``transition_delay`` pause lets the server complete the
        floor change before the next A* segment begins.

        After the delay, verifies the z-level actually changed and retries
        once if it didn't.
        """
        execute_transition(
            transition=transition,
            ctrl=self._ctrl,
            config=self._cfg,
            get_position=lambda: self._position,
            radar=self._radar,
            frame_getter=self._frame_getter,
            update_position_fn=self._update_position,
            click_character_tile_fn=self._click_character_tile,
            log_fn=self._log,
            jittered_sleep_fn=jittered_sleep,
        )

    def _exec_multifloor(
        self,
        segments: List[Route],
        start: Coordinate,
        end: Coordinate,
    ) -> None:
        """
        Execute a multi-floor journey: walk each Route segment and fire the
        appropriate floor-transition action between consecutive segments.

        The transition between segment[i] and segment[i+1] is inferred from
        the navigator's TransitionRegistry using the last step of segment[i]
        as the transition entry coordinate.
        """
        _ = start, end
        execute_multifloor(
            segments=segments,
            navigator=self._navigator,
            is_running=lambda: self._running,
            exec_route_fn=self._exec_route,
            exec_transition_fn=self._exec_transition,
            log_fn=self._log,
            transition_delay=self._cfg.transition_delay,
            sleep_fn=time.sleep,
            random_uniform_fn=random.uniform,
        )

    def _inc_stat(self, key: str) -> None:
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + 1

    def _make_npc_handler(self) -> "Optional[Callable[[str, Any], None]]":
        """
        Build an ``npc_handler`` closure for :class:`~src.script_executor.ScriptExecutor`.

        When a ``TradeManager`` is attached (``auto_refill=True`` or
        :attr:`_trade` is not *None*), the handler calls
        :meth:`~src.trade_manager.TradeManager.run_cycle` for trade
        actions (``sell``, ``buy_potions``, ``buy_ammo``).
        The informational actions (``check_supplies``, ``check_ammo``)
        only emit a log message and do not open the trade window.

        Returns ``None`` when no :class:`TradeManager` is wired, which
        causes the executor to log a stub warning (same behaviour as before).
        """
        return build_npc_handler(trade=self._trade, log_fn=self._log)

    def _save_stats(self) -> None:
        """Persist the current stats snapshot to *output/session_stats.json*."""
        try:
            snap = self.stats_snapshot()
            # Convert start_time to ISO string for readability
            if snap.get("start_time") is not None:
                import datetime
                snap["start_time_iso"] = datetime.datetime.fromtimestamp(
                    snap["start_time"]
                ).isoformat()
            _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _tmp = _STATS_FILE.parent / (_STATS_FILE.name + ".tmp")
            with open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(snap, _f, indent=2)
            _tmp.replace(_STATS_FILE)
        except Exception as _e:
            _log.debug("stats snapshot save failed (ignorado): %s", _e)

    def _log(self, msg: str) -> None:
        self._log_cb(f"[S] {msg}")

    # ── Subsystem pause / resume (called by death & reconnect handlers) ───

    def _pause_subsystems(self) -> None:
        """Pause healer, combat, looter and anti-kick during recovery sequences."""
        pause_session_subsystems(
            healer=self._healer,
            combat=self._combat,
            looter=self._looter,
            anti_kick=self._anti_kick,
            log_fn=self._log,
        )

    def _resume_subsystems(self) -> None:
        """Resume healer, combat, looter and anti-kick after recovery sequences."""
        resume_session_subsystems(
            healer=self._healer,
            combat=self._combat,
            looter=self._looter,
            anti_kick=self._anti_kick,
            log_fn=self._log,
        )
