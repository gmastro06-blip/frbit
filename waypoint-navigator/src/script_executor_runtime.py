from __future__ import annotations

import random
from typing import Any

from .client_actions import use_hotkey_on_current_tile


def execute_script(*, executor: Any, instructions: list[Any], start_index: int, build_labels_fn: Any) -> None:
    executor._running = True
    executor._instructions = instructions
    executor._stop_reason = ""
    labels = build_labels_fn(instructions)
    idx = max(0, min(start_index, max(0, len(instructions) - 1))) if instructions else 0
    executor._resume_instruction_index = None
    executor._current_instruction_index = idx
    executor._last_confirmed_node_index = idx
    total = len(instructions)
    executor._log(f"[X] Running {total} instructions")
    if idx > 0:
        executor._log(f"[X] Resuming script from instruction [{idx}]")
    executor._record_wp_action("script_start", f"Running {total} instructions")

    call_fn = executor._dispatch_override or executor._dispatch
    while executor._running and idx < total:
        executor._current_instruction_index = idx
        instruction = instructions[idx]
        executor._current_instr = instruction
        executor._log(f"[X] [{idx:4d}] {instruction}")
        jump_to = None
        last_exc: Exception | None = None
        for attempt in range(1 + executor._dispatch_retries):
            try:
                jump_to = call_fn(instruction)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt < executor._dispatch_retries and executor._running:
                    delay = executor._dispatch_backoff_base * (2 ** attempt)
                    executor._log(
                        f"[X] ⚠ instruction [{idx}] attempt {attempt + 1}/{1 + executor._dispatch_retries} "
                        f"raised {type(exc).__name__}: {exc} — retry in {delay:.1f}s"
                    )
                    executor._sleep(delay)
        if last_exc is not None:
            executor._log(
                f"[X] ⚠ instruction [{idx}] failed after {1 + executor._dispatch_retries} "
                f"attempts: {last_exc!r} — skipping"
            )
            idx += 1
            continue
        if executor._resume_instruction_index is not None:
            idx = executor._resume_instruction_index
            executor._resume_instruction_index = None
            continue
        if jump_to is not None:
            new_idx = labels.get(jump_to.lower())
            if new_idx is not None:
                idx = new_idx
                continue
            executor._log(f"[X] ⚠  label '{jump_to}' not found — jump ignored")
        idx += 1

    executor._running = False
    executor._record_wp_action("script_end", "Done")
    executor._log("[X] Done")


def dispatch_instruction(*, executor: Any, ins: Any) -> str | None:
    handler = executor._KIND_HANDLERS.get(ins.kind)
    if handler is not None:
        return handler(ins)
    if ins.kind == "action":
        action_handler = executor._ACTION_HANDLERS.get(ins.action, "")
        if action_handler:
            return action_handler(ins)
    if ins.kind not in ("action", "unknown"):
        executor._log(f"[X] unhandled kind={ins.kind!r} — skipping")
    return None


def handle_action_end(*, executor: Any, ins: Any) -> str | None:
    executor._log("[X] action end — stopping")
    executor._stop_reason = "action_end"
    executor._running = False
    return None


def handle_combat_action(*, executor: Any, ins: Any) -> str | None:
    if executor._combat is not None:
        method = getattr(executor._combat, ins.action.replace("combat_", ""), None)
        if method is not None:
            executor._log(f"[X] action {ins.action}")
            if not executor._dry_run:
                try:
                    method()
                except Exception as exc:
                    executor._log(f"[X] ⚠  {ins.action} raised: {exc}")
            return None
    executor._log(
        f"[X] ⚠  {ins.action}: CombatManager not attached — pass combat_manager= to ScriptExecutor"
    )
    return None


def handle_depot(*, executor: Any, ins: Any) -> str | None:
    last_walk_ok = getattr(executor, "_last_walk_ok", None)
    if last_walk_ok is False:
        executor._log(
            "[X] ⚠  depot: skipped — preceding walk did not reach destination (character is not at depot spot)"
        )
        return None

    if executor._depot is not None:
        executor._record_wp_action("depot", "run_depot_cycle")
        executor._depot.run_depot_cycle(player_pos=executor._current_pos)
    else:
        executor._log(
            "[X] ⚠  depot: DepotManager not attached — pass depot_manager= to ScriptExecutor"
        )
    return None


def handle_walk_mode(*, executor: Any, ins: Any) -> str | None:
    new_method = "scancode" if ins.action == "walk_keys" else "postmessage"
    executor._log(f"[X] {ins.action} → input_method={new_method}")
    if not executor._dry_run and executor._ctrl is not None:
        executor._ctrl.input_method = new_method
    return None


def handle_chat_toggle(*, executor: Any, ins: Any) -> str | None:
    executor._log(f"[X] {ins.action}")
    if not executor._dry_run and executor._ctrl is not None:
        if ins.action == "chat_on":
            executor._ctrl.press_key(0x0D)
        else:
            executor._ctrl.press_key(0x1B)
        executor._sleep(0.15)
    return None


def handle_npc_action(*, executor: Any, ins: Any) -> str | None:
    executor._log(f"[X] NPC action: {ins.action}")
    executor._record_wp_action("npc_action", ins.action)
    if not executor._dry_run:
        inline_items = []
        if ins.action in ("sell", "buy_potions", "buy_ammo"):
            inline_items = executor._parse_trade_items(ins)

        if inline_items:
            executor._log(f"[X] NPC action: using inline items for {ins.action}")
            if ins.action == "sell":
                executor._sell_chat(ins)
            elif ins.action == "buy_potions":
                executor._buy_potions_chat(ins)
            else:
                executor._buy_ammo_chat(ins)
        elif executor._npc_handler is not None:
            try:
                executor._npc_handler(ins.action, ins)
            except Exception as exc:
                executor._log(f"[X] ⚠  npc_handler raised: {exc}")
        elif ins.action in ("buy_potions", "sell"):
            executor._trade_gui_or_chat(ins)
        elif ins.action == "buy_ammo":
            executor._buy_ammo_chat(ins)
        elif ins.action == "check_ammo":
            jump = executor._check_ammo(ins)
            if jump:
                return jump
        elif ins.action == "check_supplies":
            jump = executor._check_supplies(ins)
            if jump:
                return jump
        else:
            executor._log(
                f"[X] ⚠  {ins.action}: no npc_handler attached — pass npc_handler= to ScriptExecutor (stub, skipped)"
            )
    return None


def handle_check(*, executor: Any, ins: Any) -> str | None:
    hp = executor._read_stat("hp")
    mp = executor._read_stat("mp")
    if hp is not None or mp is not None:
        executor._log(f"[X] check: HP={hp}% MP={mp}%")
        executor._record_wp_action("check", f"HP={hp}% MP={mp}%", meta={"hp": hp, "mp": mp})
    else:
        executor._log("[X] check: no healer attached — stat unavailable")
    return None


def handle_check_time(*, executor: Any, ins: Any) -> str | None:
    if executor._hours_leave and executor._is_leave_time():
        executor._log(
            "[X] check_time → leave time reached "
            f"(hours_leave={executor._hours_leave}) — stopping"
        )
        executor._running = False
    else:
        suffix = (
            f" (hours_leave={executor._hours_leave})"
            if executor._hours_leave
            else " (no hours_leave configured — continuing)"
        )
        executor._log("[X] check_time → not yet leave time" + suffix)
    return None


def handle_wait(*, executor: Any, ins: Any) -> str | None:
    secs = ins.wait_secs if ins.wait_secs > 0 else 1.0
    executor._log(f"[X] wait {secs}s")
    if not executor._dry_run:
        executor._sleep(secs)
    return None


def handle_label(*, executor: Any, ins: Any) -> str | None:
    if ins.label in ("hunt", "downcave"):
        executor._has_hunted = True
    return None


def handle_goto(*, executor: Any, ins: Any) -> str | None:
    return ins.label_jump


def handle_use_hotkey(*, executor: Any, ins: Any) -> str | None:
    if ins.hotkey_vk:
        executor._log(f"[X] press VK={ins.hotkey_vk:#x}")
        if not executor._dry_run:
            executor._ctrl.press_key(ins.hotkey_vk)
            executor._sleep(0.3)
        executor._record_wp_action(ins.kind, f"press VK={ins.hotkey_vk:#x}", meta={"vk": ins.hotkey_vk})
    return None


def handle_if_stat(*, executor: Any, ins: Any) -> str | None:
    value = executor._read_stat(ins.stat)
    if value is not None:
        op = ins.op
        threshold = ins.threshold
        triggered = (
            (op == "<" and value < threshold)
            or (op == ">" and value > threshold)
            or (op == "<=" and value <= threshold)
            or (op == ">=" and value >= threshold)
        )
        executor._log(
            f"[X] if {ins.stat} {op} {threshold} (actual={value}%) → {'JUMP' if triggered else 'skip'}"
        )
        if triggered:
            return ins.goto_label
    else:
        executor._log(f"[X] ⚠  can't read '{ins.stat}' — condition skipped")
    return None


def handle_cond_jump(*, executor: Any, ins: Any) -> str | None:
    var_name = ins.var_name.lower()
    if var_name in ("hp", "mp"):
        value = executor._read_stat(var_name)
        threshold = ins.threshold if ins.threshold > 0 else 50
        if value is not None and value < threshold:
            return ins.label_jump
        return ins.label_skip or None

    threshold = ins.threshold if ins.threshold > 0 else 9999
    count = executor._item_counter.get(var_name, 0)
    executor._log(f"[X] cond_jump item={var_name!r}  count={count}  threshold={threshold}")
    if count < threshold:
        return ins.label_jump
    return ins.label_skip or None


def handle_say(*, executor: Any, ins: Any) -> str | None:
    if ins.sentence:
        executor._log(f"[X] say {ins.sentence!r}")
        executor._record_wp_action("say", ins.sentence)
        if not executor._dry_run:
            executor._ctrl.press_key(0x0D)
            executor._sleep(0.15)
            executor._ctrl.type_text(ins.sentence)
            executor._ctrl.press_key(0x0D)
            executor._sleep(0.5)
    return None


def handle_talk_npc(*, executor: Any, ins: Any) -> str | None:
    if ins.words:
        executor._record_wp_action("talk_npc", "talk_npc words", meta={"words": ins.words})
        first_word = ins.words[0]
        executor._log(
            f"[X] talk_npc word={first_word!r} (input={getattr(executor._ctrl, 'input_method', '?')})"
        )
        if not executor._dry_run:
            executor._ctrl.press_key(0x0D)
            executor._sleep(0.15)
            executor._ctrl.type_text(first_word)
            executor._ctrl.press_key(0x0D)
            executor._sleep(1.5)
            executor._verify_npc_dialog()
            executor._switch_to_npc_channel()
        for word in ins.words[1:]:
            executor._log(f"[X] talk_npc word={word!r}")
            if not executor._dry_run:
                clicked = executor._click_dialog_option(word)
                if not clicked:
                    executor._log(f"[X] fallback: typing {word!r} in chat")
                    executor._ctrl.press_key(0x0D)
                    executor._sleep(0.15)
                    executor._ctrl.type_text(word)
                    executor._ctrl.press_key(0x0D)
                executor._sleep(1.5)
        executor._log("[X] talk_npc complete")
    return None


def handle_open_door(*, executor: Any, ins: Any) -> str | None:
    if ins.coord:
        executor._open_door(ins.coord.to_tibia_coord())
    return None


def handle_movement(*, executor: Any, ins: Any) -> str | None:
    kind = ins.kind
    if ins.coord:
        dest = ins.coord.to_tibia_coord()
        executor._walk_to(dest, kind)
        if executor._last_walk_ok is False:
            if executor._stop_reason == "resolver_degraded":
                return None
            executor._rewind_to_last_confirmed_node(dest)
            return None
        executor._last_confirmed_node_index = executor._current_instruction_index
        executor._resume_retry_counts.pop(executor._current_instruction_index, None)
        if kind == "rope":
            if executor._rope_vk:
                executor._log(f"[X] use rope (VK={executor._rope_vk:#x})")
                if not executor._dry_run:
                    use_hotkey_on_current_tile(
                        ctrl=executor._ctrl,
                        hotkey_vk=executor._rope_vk,
                        click_character_tile_fn=executor._click_character_tile,
                        sleep_fn=executor._sleep,
                    )
            else:
                executor._log("[X] ⚠ rope_hotkey_vk not configured — skipping rope action")
            if executor._current_pos is not None:
                new_z = executor._current_pos.z - 1
                landed = executor._find_nearest_walkable(executor._current_pos.x, executor._current_pos.y, new_z)
                if landed is not None:
                    executor._current_pos = landed
                else:
                    from .models import Coordinate

                    executor._current_pos = Coordinate(executor._current_pos.x, executor._current_pos.y, new_z)
                executor._log(f"[X] rope ↑ → pos {executor._current_pos}")
                executor._add_wp_waypoint(executor._current_pos, "rope")
        elif kind == "shovel":
            if executor._shovel_vk:
                executor._log(f"[X] use shovel (VK={executor._shovel_vk:#x})")
                if not executor._dry_run:
                    use_hotkey_on_current_tile(
                        ctrl=executor._ctrl,
                        hotkey_vk=executor._shovel_vk,
                        click_character_tile_fn=executor._click_character_tile,
                        sleep_fn=executor._sleep,
                    )
            else:
                executor._log("[X] ⚠ shovel_hotkey_vk not configured — skipping shovel action")
            if executor._current_pos is not None:
                new_z = executor._current_pos.z + 1
                landed = executor._find_nearest_walkable(executor._current_pos.x, executor._current_pos.y, new_z)
                if landed is not None:
                    executor._current_pos = landed
                else:
                    from .models import Coordinate

                    executor._current_pos = Coordinate(executor._current_pos.x, executor._current_pos.y, new_z)
                executor._log(f"[X] shovel ↓ → pos {executor._current_pos}")
                executor._add_wp_waypoint(executor._current_pos, "shovel")
    return None


def rewind_to_last_confirmed_node(*, executor: Any, dest: Any) -> None:
    failed_idx = executor._current_instruction_index
    retry_count = executor._resume_retry_counts.get(failed_idx, 0) + 1
    executor._resume_retry_counts[failed_idx] = retry_count
    resume_idx = min(executor._last_confirmed_node_index, failed_idx)
    if retry_count > executor._RESUME_RETRY_MAX:
        executor._stop_reason = "movement_failed"
        executor._running = False
        executor._resume_instruction_index = None
        executor._log(
            f"[X] ⚠  movement [{failed_idx}] to {dest} failed {retry_count - 1} times — stopping at previous node [{resume_idx}]"
        )
        return
    executor._resume_instruction_index = resume_idx
    executor._log(
        f"[X] movement [{failed_idx}] to {dest} failed — resume from instruction [{resume_idx}] attempt {retry_count}/{executor._RESUME_RETRY_MAX}"
    )


def handle_random_stand(*, executor: Any, ins: Any) -> str | None:
    if not ins.choices:
        return None
    chosen = random.choice(ins.choices)
    dest = chosen.to_tibia_coord()
    executor._log(f"[X] random_stand -> {dest} (1 of {len(ins.choices)} choices)")
    executor._walk_to(dest, "stand")
    return None