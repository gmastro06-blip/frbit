
"""
Navigator - Tibia map waypoint navigation system.
Based on data from https://github.com/tibiamaps/tibia-map-data
"""

from .models import Coordinate, Waypoint, Route, FloorTransition
from .map_loader import TibiaMapLoader
from .pathfinder import AStarPathfinder
from .navigator import WaypointNavigator
from .visualizer import MapVisualizer
# CoordinateOCR/CharacterDetector deprecated — OCR coords not visible in OTC client
# Module split: detector_config, image_processing, frame_sources, deprecated_ocr
# character_detector.py is now a backwards-compatible re-export shim
from .detector_config import DetectorConfig
from .hpmp_detector import HpMpDetector, HpMpConfig, NumericReading
from .transitions import TransitionRegistry
from .depot_manager import DepotManager, DepotConfig
from .depot_orchestrator import DepotOrchestrator, ResupplyConfig
from .dashboard_server import DashboardServer
from .healer import AutoHealer, HealConfig
from .looter import Looter, LootConfig
from .script_parser import ScriptParser, Instruction
from .script_executor import ScriptExecutor
from .route_validator import RouteJsonSimulator
from .session import BotSession, SessionConfig
from .combat_manager import CombatManager, CombatConfig
from .condition_monitor import ConditionMonitor, ConditionConfig
from .event_bus import EventBus
from .monitor_gui import MonitorGui, MonitorConfig
from .game_data import GameData, MonsterInfo, SpellInfo
from .frame_quality import FrameQuality, FrameQualityChecker, FrameQualityConfig
from .frame_cache import FrameCache
from .frame_watchdog import FrameWatchdog, FrameWatchdogConfig
from .position_resolver import PositionResolver, PositionResolverConfig, SourceKind
from .adaptive_roi import AdaptiveROIDetector, AdaptiveROIConfig, AnchorTemplate, DetectedROI, load_anchor
from .pvp_detector import PvPDetector, PvPConfig, PvPAction, PvPDetection
from .inventory_manager import InventoryManager, InventoryConfig, InventoryStatus, SupplyStatus
from .alert_system import AlertSystem, AlertConfig, LogRotator, LogRotationConfig
from .spawn_manager import SpawnManager, SpawnManagerConfig, SpawnPoint, SpawnStatus
from .session_stats import HuntingSessionStats, SessionStatsConfig
from .break_scheduler import BreakScheduler, BreakSchedulerConfig
from .soak_monitor import SoakMonitor, SoakMonitorConfig
from .obstacle_analyzer import ObstacleAnalyzer, AnalysisResult, TileInfo
from .walkability_overlay import WalkabilityOverlay, OverlayState
from .preflight import run_preflight, PreflightReport, CheckResult, Severity
from .gm_detector import GMDetector, GMDetectorConfig, GMAction, GMDetection
from .chat_responder import ChatResponder, ChatResponderConfig

# ── Centralized logger ──────────────────────────────────────────────────────
import logging as _logging
import os as _os
from typing import Any as _Any

_log = _logging.getLogger("wn")
if not _log.handlers:
    from logging.handlers import RotatingFileHandler as _RFH
    class _SafeRFH(_RFH):
        def rotate(self, source: str, dest: str) -> None:
            try:
                super().rotate(source, dest)
            except PermissionError:
                pass

        def doRollover(self) -> None:
            try:
                super().doRollover()
            except PermissionError:
                pass

        def emit(self, record: _Any) -> None:
            try:
                super().emit(record)
            except PermissionError:
                # Windows tests can keep app.log open in parallel.
                pass

        def handleError(self, record: _Any) -> None:
            import sys

            if isinstance(sys.exc_info()[1], PermissionError):
                return
            super().handleError(record)

    _log_dir = _os.path.join(_os.path.dirname(__file__), "..", "output")
    _os.makedirs(_log_dir, exist_ok=True)
    _fh = _SafeRFH(
        _os.path.join(_log_dir, "app.log"),
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    _fh.setFormatter(_logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _log.addHandler(_fh)
    _log.setLevel(_logging.DEBUG)
    _log.propagate = True

# Remove old bot.log if it exists (one-time migration)
_old_log = _os.path.join(_log_dir, "bot.log")
if _os.path.isfile(_old_log):
    try:
        _os.rename(_old_log, _os.path.join(_log_dir, "app.log.old"))
    except OSError:
        pass

__all__ = [
    "Coordinate",
    "Waypoint",
    "Route",
    "FloorTransition",
    "TibiaMapLoader",
    "AStarPathfinder",
    "WaypointNavigator",
    "MapVisualizer",
    "DetectorConfig",
    "HpMpDetector",
    "HpMpConfig",
    "NumericReading",
    "TransitionRegistry",
    "DepotManager",
    "DepotConfig",
    "DepotOrchestrator",
    "ResupplyConfig",
    "DashboardServer",
    "AutoHealer",
    "HealConfig",
    "Looter",
    "LootConfig",
    "ScriptParser",
    "Instruction",
    "ScriptExecutor",
    "RouteJsonSimulator",
    "BotSession",
    "SessionConfig",
    "CombatManager",
    "CombatConfig",
    "ConditionMonitor",
    "ConditionConfig",
    "EventBus",
    "MonitorGui",
    "MonitorConfig",
    "GameData",
    "MonsterInfo",
    "SpellInfo",
    "FrameQuality",
    "FrameQualityChecker",
    "FrameQualityConfig",
    "FrameCache",
    "FrameWatchdog",
    "FrameWatchdogConfig",
    "PositionResolver",
    "PositionResolverConfig",
    "SourceKind",
    "AdaptiveROIDetector",
    "AdaptiveROIConfig",
    "AnchorTemplate",
    "DetectedROI",
    "PvPDetector",
    "PvPConfig",
    "PvPAction",
    "PvPDetection",
    "InventoryManager",
    "InventoryConfig",
    "InventoryStatus",
    "SupplyStatus",
    "AlertSystem",
    "AlertConfig",
    "LogRotator",
    "LogRotationConfig",
    "SpawnManager",
    "SpawnManagerConfig",
    "SpawnPoint",
    "SpawnStatus",
    "HuntingSessionStats",
    "SessionStatsConfig",
    "BreakScheduler",
    "BreakSchedulerConfig",
    "SoakMonitor",
    "SoakMonitorConfig",
    "ObstacleAnalyzer",
    "AnalysisResult",
    "TileInfo",
    "WalkabilityOverlay",
    "OverlayState",
    "run_preflight",
    "PreflightReport",
    "CheckResult",
    "Severity",
    "GMDetector",
    "GMDetectorConfig",
    "GMAction",
    "GMDetection",
    "ChatResponder",
    "ChatResponderConfig",
]


# ── Traceback sanitizer (strip revealing module paths) ──────────────────────
import sys as _sys
import traceback as _tb
import re as _re
from types import TracebackType as _TracebackType

# Map src.module_name → short codes in tracebacks
_MOD_MAP = {
    "src.session": "m.s", "src.combat_manager": "m.cm", "src.healer": "m.hl",
    "src.input_controller": "m.ic", "src.navigator": "m.nv",
    "src.character_detector": "m.cd", "src.condition_monitor": "m.cn",
    "src.death_handler": "m.dh", "src.depot_manager": "m.dm",
    "src.depot_orchestrator": "m.do", "src.frame_capture": "m.fc",
    "src.hpmp_detector": "m.hp", "src.anti_kick": "m.ak",
    "src.break_scheduler": "m.bs", "src.gm_detector": "m.gd",
    "src.chat_responder": "m.cr", "src.reconnect_handler": "m.rh",
    "src.script_executor": "m.se", "src.looter": "m.lt",
    "src.action_verifier": "m.av", "src.event_bus": "m.eb",
    "src.pvp_detector": "m.pd", "src.stuck_detector": "m.sd",
    "src.trade_manager": "m.tm", "src.alert_system": "m.as",
    "src.minimap_radar": "m.mr", "src.mouse_bezier": "m.mb",
    "src.inventory_manager": "m.im", "src.spawn_manager": "m.sp",
    "src.soak_monitor": "m.sk", "src.telemetry": "m.tl",
    "src.walkability_overlay": "m.wo", "src.dashboard_server": "m.ds",
    "src.monitor_gui": "m.mg", "src.game_data": "m.gd2",
    "src.adaptive_roi": "m.ar", "src.humanizer": "m.hz",
}

def _sanitize_tb(text: str) -> str:
    for full, short in _MOD_MAP.items():
        text = text.replace(full, short)
    # Strip absolute paths to src/ — keep only relative filename
    text = _re.sub(r'File ".*[/\\]src[/\\]', 'File "', text)
    return text


if getattr(_sys, 'frozen', False):
    _original_excepthook = _sys.excepthook

    def _safe_excepthook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: _TracebackType | None) -> None:
        lines = _tb.format_exception(exc_type, exc_value, exc_tb)
        sanitized = _sanitize_tb("".join(lines))
        _sys.stderr.write(sanitized)

    _sys.excepthook = _safe_excepthook

    # Also patch threading excepthook for thread exceptions
    import threading as _threading

    _original_threading_excepthook = getattr(_threading, 'excepthook', None)

    def _safe_threading_excepthook(args: _threading.ExceptHookArgs) -> None:
        lines = _tb.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        sanitized = _sanitize_tb("".join(lines))
        _sys.stderr.write(sanitized)

    _threading.excepthook = _safe_threading_excepthook


# ── Runtime class name stripping (production only) ──────────────────────────
if getattr(_sys, 'frozen', False):
    import hashlib as _hashlib
    _CLASS_MAP = {}
    for _cls in [
        BotSession, WaypointNavigator, AutoHealer, CombatManager,
        ConditionMonitor, GMDetector, ChatResponder, Looter,
        HpMpDetector, DepotManager, DepotOrchestrator,
        DashboardServer, BreakScheduler, SoakMonitor, PvPDetector,
        InventoryManager, SpawnManager, ScriptExecutor, EventBus, AlertSystem,
    ]:
        if _cls is None:
            continue
        _short = "C" + _hashlib.md5(_cls.__name__.encode()).hexdigest()[:6]
        _CLASS_MAP[_cls.__name__] = _short
        _cls.__name__ = _short
        _cls.__qualname__ = _short

