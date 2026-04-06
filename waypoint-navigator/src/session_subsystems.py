from __future__ import annotations

from typing import Any, Callable


def build_npc_handler(*, trade: Any, log_fn: Callable[[str], None]) -> Callable[[str, Any], None] | None:
    if trade is None:
        return None

    trade_actions = {"sell", "buy_potions", "buy_ammo"}
    info_actions = {"check_supplies", "check_ammo"}

    def _handler(action: str, instruction: Any) -> None:
        _ = instruction
        if action in trade_actions:
            log_fn(f"[S] npc_handler: {action} → TradeManager.run_cycle()")
            trade.run_cycle()
            return
        if action in info_actions:
            log_fn(f"[S] npc_handler: {action} → (informational, no trade)")
            return
        log_fn(f"[S] npc_handler: unrecognised action '{action}' — skipped")

    return _handler


def pause_session_subsystems(
    *,
    healer: Any,
    combat: Any,
    looter: Any,
    anti_kick: Any,
    log_fn: Callable[[str], None],
) -> None:
    for subsystem, action_name, label in (
        (healer, "pause", "healer"),
        (combat, "pause", "combat"),
        (looter, "pause", "looter"),
        (anti_kick, "pause", "anti_kick"),
    ):
        if subsystem is None:
            continue
        try:
            getattr(subsystem, action_name)()
        except Exception as exc:
            log_fn(f"{action_name} {label} failed: {exc}")


def resume_session_subsystems(
    *,
    healer: Any,
    combat: Any,
    looter: Any,
    anti_kick: Any,
    log_fn: Callable[[str], None],
) -> None:
    for subsystem, action_name, label in (
        (healer, "resume", "healer"),
        (combat, "resume", "combat"),
        (looter, "resume", "looter"),
        (anti_kick, "resume", "anti_kick"),
    ):
        if subsystem is None:
            continue
        try:
            getattr(subsystem, action_name)()
        except Exception as exc:
            log_fn(f"{action_name} {label} failed: {exc}")