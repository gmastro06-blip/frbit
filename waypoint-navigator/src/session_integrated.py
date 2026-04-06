from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class IntegratedSubsystems:
    path_viz: Any = None
    frame_quality: Any = None
    position_resolver: Any = None
    local_reader: Any = None
    pvp_detector: Any = None
    inventory_manager: Any = None
    depot_orchestrator: Any = None
    position: Any = None


def initialize_integrated_modules(
    *,
    config: Any,
    radar: Any,
    event_bus: Any,
    depot_manager: Any,
    trade_manager: Any,
    navigator: Any,
    ctrl: Any,
    walk_route: Callable[..., Any],
    log_fn: Callable[[str], None],
    current_position: Any,
    frame_quality_checker_cls: Any,
    position_resolver_cls: Any,
    position_resolver_config_cls: Any,
    source_kind_cls: Any,
    tibia_local_minimap_reader_cls: Any,
    pvp_detector_cls: Any,
    pvp_config_cls: Any,
    pvp_action_cls: Any,
    inventory_manager_cls: Any,
    inventory_config_cls: Any,
    resupply_config_cls: Any,
    depot_orchestrator_cls: Any,
) -> IntegratedSubsystems:
    result = IntegratedSubsystems(path_viz=None, position=current_position)

    if config.frame_quality_check:
        result.frame_quality = frame_quality_checker_cls()
        log_fn("FrameQualityChecker enabled.")

    if config.use_position_resolver:
        position_resolver_config = position_resolver_config_cls(
            max_stale_ms=config.position_resolver_stale_ms,
        )
        result.position_resolver = position_resolver_cls(config=position_resolver_config)
        if radar is not None:
            result.position_resolver.add_source(
                "minimap_radar",
                source_kind_cls.MINIMAP_RADAR,
                radar,
            )

        local_reader = tibia_local_minimap_reader_cls(config=radar._cfg if radar else None)
        if local_reader.is_available:
            result.local_reader = local_reader
            log_fn("TibiaLocalMinimapReader available (floor/hint only).")
            if result.position is None:
                hint = local_reader.hint_coordinate()
                if hint is not None:
                    result.position = hint
                    log_fn(f"[S] Bootstrap position (local minimap): {hint}")
        log_fn("PositionResolver enabled.")

    if config.pvp_detector:
        pvp_action_map = {
            "ignore": pvp_action_cls.IGNORE,
            "warn": pvp_action_cls.WARN,
            "pause": pvp_action_cls.PAUSE,
            "flee": pvp_action_cls.FLEE,
            "logout": pvp_action_cls.LOGOUT,
        }
        pvp_config = pvp_config_cls(
            action=pvp_action_map.get(config.pvp_action.lower(), pvp_action_cls.WARN),
        )
        result.pvp_detector = pvp_detector_cls(
            config=pvp_config,
            event_bus=event_bus,
            auto_load=True,
        )
        skull_count = len(pvp_config.skull_templates)
        if skull_count:
            log_fn(f"PvPDetector enabled ({skull_count} skull templates).")
        else:
            log_fn("PvPDetector enabled (color fallback - no skull templates).")

    if config.inventory_check:
        inventory_roi: list[int] = []
        if config.inventory_roi.strip():
            inventory_roi = [int(value.strip()) for value in config.inventory_roi.split(",")]
        inventory_config = inventory_config_cls(
            inventory_roi=inventory_roi if len(inventory_roi) == 4 else [0, 0, 0, 0],
        )
        result.inventory_manager = inventory_manager_cls(
            config=inventory_config,
            event_bus=event_bus,
        )
        log_fn("InventoryManager enabled.")

    if config.depot_after_run and (depot_manager is not None or result.inventory_manager is not None):
        resupply_config = resupply_config_cls(
            enabled=True,
            buy_supplies_after_depot=trade_manager is not None,
        )
        result.depot_orchestrator = depot_orchestrator_cls(
            config=resupply_config,
            depot_manager=depot_manager,
            trade_manager=trade_manager,
            inventory_manager=result.inventory_manager,
            navigator=navigator,
            ctrl=ctrl,
            log_fn=log_fn,
        )
        result.depot_orchestrator.set_walk_fn(walk_route)
        log_fn("DepotOrchestrator enabled (depot + trade + inventory).")

    return result