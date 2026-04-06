from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .storage_detector import StorageDetector, StorageDetectorConfig
from .storage_navigator import StorageNavigator, StorageNavigatorConfig


@dataclass
class OptionalSubsystems:
    obstacle_analyzer: Any = None
    depot: Any = None
    looter: Any = None
    combat: Any = None
    condition_monitor: Any = None
    trade: Any = None


def initialize_optional_subsystems(
    *,
    config: Any,
    ctrl: Any,
    healer: Any,
    event_bus: Any,
    cached_getter: Any,
    log_fn: Callable[[str], None],
    position_getter: Callable[[], Any],
    loot_in_progress: Any,
    session_events: Any,
    depot_manager_cls: Any,
    looter_cls: Any,
    combat_manager_cls: Any,
    combat_config_cls: Any,
    hpmp_detector_cls: Any,
    condition_monitor_cls: Any,
    condition_config_cls: Any,
    trade_manager_cls: Any,
    trade_config_cls: Any,
) -> OptionalSubsystems:
    if healer is not None:
        if cached_getter:
            healer.set_frame_getter(cached_getter)
        healer.set_event_bus(event_bus)
        if not getattr(healer, "is_running", False):
            healer.start()

    result = OptionalSubsystems()
    log_fn("[S] ObstacleAnalyzer DISABLED (generates false positives in urban areas).")

    if config.depot_after_run:
        result.depot = depot_manager_cls(ctrl=ctrl)
        result.depot.set_log_callback(log_fn)
        if cached_getter:
            result.depot.set_frame_getter(cached_getter)

        # Wire storage-surface awareness (StorageDetector + StorageNavigator).
        # These are injected here so depot_manager stays decoupled from the
        # session.  If either module fails to init, depot falls back gracefully
        # to the legacy _open_chest() path (no navigator configured).
        try:
            _storage_det = StorageDetector(
                config=StorageDetectorConfig(),
                log_fn=log_fn,
            )
            if cached_getter:
                _storage_det.set_frame_getter(cached_getter)

            _storage_nav = StorageNavigator(
                detector=_storage_det,
                ctrl=ctrl,
                frame_getter=cached_getter or (lambda: None),
                config=StorageNavigatorConfig(),
                log_fn=log_fn,
            )
            result.depot.set_storage_detector(_storage_det)
            result.depot.set_storage_navigator(_storage_nav)
            log_fn("StorageDetector + StorageNavigator wired into DepotManager.")
        except Exception as _exc:
            log_fn(f"[!] StorageNavigator init failed ({_exc}) — depot uses legacy path.")

        log_fn("DepotManager created (depot_after_run=True).")

    if config.auto_loot:
        result.looter = looter_cls(ctrl=ctrl)
        result.looter.set_log_callback(log_fn)
        if cached_getter:
            result.looter.set_frame_getter(cached_getter)
        result.looter.set_player_getter(position_getter)
        result.looter.on_loot_start = lambda: loot_in_progress.set()

        def _on_loot_finish() -> None:
            loot_in_progress.clear()
            event_bus.emit(session_events.LOOT_DONE, {})

        result.looter.on_loot_finish = _on_loot_finish
        result.looter.start()
        log_fn("Looter started (auto_loot=True).")

    if config.auto_combat:
        combat_config = combat_config_cls()
        if config.combat_config_file:
            try:
                combat_config = combat_config_cls.load(Path(config.combat_config_file))
                log_fn(f"CombatConfig loaded from {config.combat_config_file}")
            except Exception as config_error:
                log_fn(f"[!] CombatConfig load failed ({config_error}) — using defaults")

        hp_detector: Optional[Any] = None
        try:
            detector = hpmp_detector_cls()
            detector.preload_ocr()
            hp_detector = detector
        except Exception as detector_error:
            log_fn(f"[!] HpMpDetector init failed ({detector_error}) — combat flee/spells-by-MP disabled")

        result.combat = combat_manager_cls(ctrl=ctrl, hp_detector=hp_detector, config=combat_config)
        result.combat.set_log_callback(log_fn)
        if cached_getter:
            result.combat.set_frame_getter(cached_getter)

        def _on_kill() -> None:
            # Use player position for kill-event telemetry (context for where
            # the kill happened); do NOT pass it to the looter as the corpse
            # coordinate — the combat manager tracks pixel positions, not world
            # tiles, so we have no reliable corpse tile here.  notify_kill(None)
            # lets the looter find the corpse via template matching instead of
            # clicking the player's own tile.
            player_coord = position_getter()
            event_bus.emit(session_events.KILL, {"coord": player_coord})
            event_bus.emit("kill", {"coord": player_coord})
            if result.looter is not None:
                result.looter.notify_kill(None)

        result.combat.on_kill = _on_kill
        result.combat.start()
        log_fn("CombatManager started (auto_combat=True).")

    if config.monitor_conditions:
        condition_config = condition_config_cls.load()
        if config.condition_config_file:
            try:
                condition_config = condition_config_cls.load(Path(config.condition_config_file))
                log_fn(f"ConditionConfig loaded from {config.condition_config_file}")
            except Exception as config_error:
                log_fn(f"[!] ConditionConfig load failed ({config_error}) — using defaults")

        result.condition_monitor = condition_monitor_cls(ctrl=ctrl, config=condition_config)
        if cached_getter:
            result.condition_monitor.set_frame_getter(cached_getter)
        result.condition_monitor.on_condition = lambda cond: event_bus.emit(
            session_events.CONDITION, {"condition": cond}
        )
        result.condition_monitor.on_condition_clear = lambda cond: event_bus.emit(
            session_events.CONDITION_CLEAR, {"condition": cond}
        )
        result.condition_monitor.set_log_callback(log_fn)
        result.condition_monitor.start()
        log_fn("ConditionMonitor started (monitor_conditions=True).")

    if config.auto_refill:
        trade_config = trade_config_cls()
        if config.trade_config_file:
            try:
                trade_config = trade_config_cls.load(Path(config.trade_config_file))
                log_fn(f"TradeConfig loaded from {config.trade_config_file}")
            except Exception as config_error:
                log_fn(f"[!] TradeConfig load failed ({config_error}) — using defaults")

        result.trade = trade_manager_cls(ctrl=ctrl, config=trade_config)
        result.trade.set_log_callback(log_fn)
        if cached_getter:
            result.trade.set_frame_getter(cached_getter)
        log_fn("TradeManager ready (auto_refill=True).")

    return result