from __future__ import annotations

import json
from typing import Any


def _parse_raw_items(ins: Any) -> list[dict[str, Any]]:
    try:
        raw = json.loads(ins.raw.replace("'", '"'))
        return list(raw.get("items", []))
    except Exception:
        return []


def _send_trade_commands(*, executor: Any, items: list[dict[str, Any]], prefix: str, quantity_fallback: int) -> None:
    for item in items:
        name = str(item.get("name", ""))
        quantity = int(item.get("qty", item.get("quantity", quantity_fallback)))
        if not name:
            continue
        quantity_text = "all" if prefix == "sell" and quantity <= 0 else str(quantity)
        command = f"{prefix} {quantity_text} {name}"
        executor._log(f"[X] {prefix} → NPC: {command!r}")
        executor._say_to_npc(command)
        executor._sleep(1.0)
        executor._log(f"[X] {prefix} → NPC: 'yes'")
        executor._say_to_npc("yes")
        executor._sleep(0.5)


def check_ammo(*, executor: Any, ins: Any) -> str | None:
    executor._log("[X] check_ammo: evaluating ammo status")
    skip = False

    if not getattr(executor, "_has_hunted", False):
        skip = True
        executor._log("[X] check_ammo: first run — skipping buy (assume full)")

    if getattr(executor, "_force_resupply", False):
        skip = False
        executor._force_resupply = False
        executor._log("[X] check_ammo: forced resupply — will buy")

    if skip:
        return "skip_ammo"
    return None


def check_supplies(*, executor: Any, ins: Any) -> str | None:
    executor._log("[X] check_supplies: evaluating supply levels")
    if getattr(executor, "_has_hunted", False):
        executor._log("[X] check_supplies: post-hunt — supplies may be low, continuing")
        return None
    executor._log("[X] check_supplies: pre-hunt — supplies assumed OK, continuing")
    return None


def buy_ammo_chat(*, executor: Any, ins: Any) -> None:
    executor._switch_to_npc_channel()
    items = _parse_raw_items(ins)
    if not items:
        setup = getattr(executor, "_wasp_setup", None)
        if setup and isinstance(setup, dict):
            hunt_config = setup.get("hunt_config", {})
            ammo_name = hunt_config.get("ammo_name", "")
            take_ammo = int(hunt_config.get("take_ammo", 0))
            if ammo_name and take_ammo > 0:
                items = [{"name": ammo_name, "qty": take_ammo}]
    if not items:
        executor._log("[X] ⚠  buy_ammo: no items list — skipping")
        return
    _send_trade_commands(executor=executor, items=items, prefix="buy", quantity_fallback=1)


def buy_potions_chat(*, executor: Any, ins: Any) -> None:
    executor._switch_to_npc_channel()
    items = _parse_raw_items(ins)
    if not items:
        executor._log("[X] ⚠  buy_potions: no items list in instruction")
        return
    _send_trade_commands(executor=executor, items=items, prefix="buy", quantity_fallback=1)


def sell_chat(*, executor: Any, ins: Any) -> None:
    executor._switch_to_npc_channel()
    items = _parse_raw_items(ins)
    if not items:
        executor._log("[X] ⚠  sell: no items list in instruction")
        return
    _send_trade_commands(executor=executor, items=items, prefix="sell", quantity_fallback=0)


def trade_gui_or_chat(*, executor: Any, ins: Any) -> None:
    action = ins.action
    items = executor._parse_trade_items(ins)

    if executor._frame_getter is not None:
        try:
            from src.trade_manager import TradeConfig, TradeManager

            config = TradeConfig.load()
            trade_manager = TradeManager(ctrl=executor._ctrl, config=config, log_fn=executor._log)
            trade_manager.set_frame_getter(executor._frame_getter)

            if trade_manager._detector.is_trade_window_open(executor._frame_getter()):
                executor._log(f"[X] Trade window detected → GUI {action}")
                for item in items:
                    name = str(item.get("name", ""))
                    quantity = int(item.get("qty", item.get("quantity", 0)))
                    if not name:
                        continue
                    if action == "sell":
                        trade_manager.sell_single_item(name, quantity)
                    else:
                        trade_manager.buy_single_item(name, max(quantity, 1))
                return
        except Exception as exc:
            executor._log(f"[X] ⚠  GUI trade failed: {exc}")

    executor._log(f"[X] {action} → chat fallback")
    if action == "sell":
        executor._sell_chat(ins)
    else:
        executor._buy_potions_chat(ins)


def parse_trade_items(*, ins: Any) -> list[dict[str, Any]]:
    return _parse_raw_items(ins)


def verify_npc_dialog(*, executor: Any, verify_dialog_open_fn: Any) -> None:
    if verify_dialog_open_fn is None or executor._frame_getter is None:
        executor._log("[X] ⚠  NPC dialog verify skipped (no verifier/frame)")
        return
    try:
        ok = verify_dialog_open_fn(executor._frame_getter, timeout=2.0)
        if ok:
            executor._log("[X] ✓ NPC dialog detected")
        else:
            executor._log("[X] ⚠  NPC dialog not detected after greeting — continuing anyway")
    except Exception as exc:
        executor._log(f"[X] ⚠  NPC dialog verify error: {exc}")


def click_dialog_option(*, executor: Any, word: str, find_dialog_option_fn: Any) -> bool:
    if find_dialog_option_fn is None or executor._frame_getter is None:
        return False
    try:
        position = find_dialog_option_fn(
            executor._frame_getter,
            word,
            timeout=3.0,
            poll_interval=0.4,
        )
        if position is not None:
            click_x, click_y = position
            executor._log(f"[X] ✓ dialog option {word!r} found at ({click_x},{click_y}) — clicking")
            executor._ctrl.click(click_x, click_y)
            return True
        executor._log(f"[X] ⚠  dialog option {word!r} not found (no blue keyword)")
    except Exception as exc:
        executor._log(f"[X] ⚠  dialog option click error: {exc}")
    return False