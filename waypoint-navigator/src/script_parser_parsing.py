from __future__ import annotations

import re
from typing import Any, Optional


_COORD_RE = re.compile(r"\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
_LABEL_RE = re.compile(r"^label\s+(\S+)")
_ACTION_RE = re.compile(r"^action\s+(\S+)")
# Compact "if" expression: "hp<40", "mp>=20", etc.
_IF_EXPR_RE = re.compile(r"^(hp|mp)\s*([<>]=?)\s*(\d+)$", re.IGNORECASE)
_CALL_RE = re.compile(r'^call\s+(\w+)\((.+)\)\s*$')
_KW_LIST = re.compile(r'"list_words"\s*:\s*\[([^\]]+)\]')
_KW_SENT = re.compile(r'"sentence"\s*:\s*"([^"]+)"')
_KW_VAR = re.compile(r'"var_name"\s*:\s*"([^"]+)"')
_KW_JUMP = re.compile(r'"label_jump"\s*:\s*"([^"]+)"')
_KW_SKIP = re.compile(r'"label_skip"\s*:\s*"([^"]+)"')
_GOTO_RE = re.compile(r"^goto\s+(\S+)", re.IGNORECASE)
_USE_ITEM_RE = re.compile(r"^use_item\s+(\S+)(?:\s+vk=(\S+))?", re.IGNORECASE)
_USE_HOTKEY_RE = re.compile(r"^use_hotkey\s+(\S+)", re.IGNORECASE)
_WAIT_RE = re.compile(r"^wait\s+([\d.]+)", re.IGNORECASE)
_IF_RE = re.compile(r"^if\s+(hp|mp)\s*([<>]=?)\s*(\d+)\s+goto\s+(\S+)", re.IGNORECASE)
_DEPOT_RE = re.compile(r"^depot\b", re.IGNORECASE)
_JSON_ALIAS = {"walk": "stand", "door": "open_door"}
_TEXT_ALIAS = {"walk": "stand", "door": "open_door"}


def _coord_from_entry(entry: dict[str, Any], coord_cls: Any) -> Optional[Any]:
    if "x" in entry and "y" in entry and "z" in entry:
        return coord_cls(int(entry["x"]), int(entry["y"]), int(entry["z"]))
    # Compact form: "at": [x, y, z]
    at = entry.get("at")
    if isinstance(at, (list, tuple)) and len(at) >= 3:
        return coord_cls(int(at[0]), int(at[1]), int(at[2]))
    return None


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Expand compact shorthand keys to canonical form without mutating the
    original dict.  Supported shorthands:

    ``"at": [x, y, z]``
        Expands to explicit ``"x"``, ``"y"``, ``"z"`` keys.

    ``"if": "hp<40"``  (inside an ``if_stat`` entry, or when ``"kind"`` is absent)
        Expands to ``"stat"``, ``"op"``, ``"threshold"``; sets
        ``"kind": "if_stat"`` when kind is not yet specified.
    """
    if "at" not in entry and "if" not in entry:
        return entry  # fast path — nothing to expand

    entry = dict(entry)  # shallow copy; do not mutate caller's object

    # "at": [x, y, z]  →  "x", "y", "z"
    at = entry.pop("at", None)
    if at is not None and isinstance(at, (list, tuple)) and len(at) >= 3 and "x" not in entry:
        entry["x"] = int(at[0])
        entry["y"] = int(at[1])
        entry["z"] = int(at[2])

    # "if": "hp<40"  →  "kind": "if_stat", "stat": "hp", "op": "<", "threshold": 40
    if_expr = entry.pop("if", None)
    if if_expr is not None:
        if "kind" not in entry:
            entry["kind"] = "if_stat"
        m = _IF_EXPR_RE.match(str(if_expr).strip())
        if m:
            entry.setdefault("stat", m.group(1).lower())
            entry.setdefault("op", m.group(2))
            entry.setdefault("threshold", int(m.group(3)))

    return entry


def _call_entry_to_instruction(*, entry: dict[str, Any], instruction_cls: Any) -> Any:
    func = str(entry.get("func", "")).lower()
    if func == "talk_npc":
        return instruction_cls(kind="talk_npc", words=list(entry.get("words", [])), raw=str(entry))
    if func == "say":
        return instruction_cls(kind="say", sentence=str(entry.get("sentence", "")), raw=str(entry))
    if func in {"conditional_jump_item_count_below", "conditional_jump_script_options"}:
        return instruction_cls(
            kind="cond_jump",
            var_name=str(entry.get("var_name", entry.get("item_name", ""))),
            label_jump=str(entry.get("label_jump", "")).lower(),
            label_skip=str(entry.get("label_skip", "")).lower(),
            threshold=int(entry.get("amount", entry.get("threshold", 0)) or 0),
            raw=str(entry),
        )
    return instruction_cls(kind="unknown", raw=str(entry))


def parse_json_script_entries(
    entries: list[dict[str, Any]],
    *,
    instruction_cls: Any,
    coord_cls: Any,
) -> list[Any]:
    instructions: list[Any] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry = _normalize_entry(raw_entry)
        kind = str(entry.get("kind", "unknown")).lower()
        if kind.startswith("_"):
            continue

        coord = _coord_from_entry(entry, coord_cls)
        kind = _JSON_ALIAS.get(kind, kind)

        if kind in {"node", "stand", "ladder", "shovel", "rope"}:
            instructions.append(
                instruction_cls(kind=kind, coord=coord, hint=str(entry.get("hint", "")), raw=str(entry))
            )
            continue

        if kind == "label":
            instructions.append(
                instruction_cls(kind="label", label=str(entry.get("label", "")).lower(), raw=str(entry))
            )
            continue

        if kind == "goto":
            instructions.append(
                instruction_cls(
                    kind="goto",
                    label_jump=str(entry.get("label", entry.get("label_jump", ""))).lower(),
                    raw=str(entry),
                )
            )
            continue

        if kind == "action":
            instructions.append(instruction_cls(kind="action", action=str(entry.get("action", "")), raw=str(entry)))
            continue

        if kind == "wait":
            instructions.append(
                instruction_cls(
                    kind="wait",
                    wait_secs=float(entry.get("secs", entry.get("wait_secs", 0.0)) or 0.0),
                    raw=str(entry),
                )
            )
            continue

        if kind == "use_item":
            instructions.append(
                instruction_cls(
                    kind="use_item",
                    item_name=str(entry.get("item_name", "")),
                    hotkey_vk=int(entry.get("vk", 0)),
                    raw=str(entry),
                )
            )
            continue

        if kind == "use_hotkey":
            instructions.append(
                instruction_cls(kind="use_hotkey", hotkey_vk=int(entry.get("vk", 0)), raw=str(entry))
            )
            continue

        if kind == "if_stat":
            instructions.append(
                instruction_cls(
                    kind="if_stat",
                    stat=str(entry.get("stat", "hp")).lower(),
                    op=str(entry.get("op", "<")),
                    threshold=int(entry.get("threshold", 0)),
                    goto_label=str(entry.get("goto_label", "")).lower(),
                    raw=str(entry),
                )
            )
            continue

        if kind == "random_stand":
            choices = [
                coord_cls(int(choice["x"]), int(choice["y"]), int(choice["z"]))
                for choice in entry.get("choices", [])
                if "x" in choice and "y" in choice and "z" in choice
            ]
            if choices:
                instructions.append(instruction_cls(kind="random_stand", choices=choices, raw=str(entry)))
            continue

        if kind == "open_door":
            instructions.append(instruction_cls(kind="open_door", coord=coord, raw=str(entry)))
            continue

        if kind == "depot":
            instructions.append(instruction_cls(kind="depot", raw=str(entry)))
            continue

        if kind == "talk_npc":
            instructions.append(instruction_cls(kind="talk_npc", words=list(entry.get("words", [])), raw=str(entry)))
            continue

        if kind == "say":
            instructions.append(instruction_cls(kind="say", sentence=str(entry.get("sentence", "")), raw=str(entry)))
            continue

        if kind == "call":
            instructions.append(_call_entry_to_instruction(entry=entry, instruction_cls=instruction_cls))
            continue

        instructions.append(instruction_cls(kind="unknown", raw=str(entry)))

    return instructions


def _parse_hotkey(vk_str: str) -> int:
    return int(vk_str, 16) if vk_str.startswith("0x") else int(vk_str)


def _parse_call_line(*, line: str, args: str, func: str, instruction_cls: Any) -> Optional[Any]:
    if func == "talk_npc":
        match = _KW_LIST.search(args)
        words = [word.strip().strip('"') for word in match.group(1).split(",")] if match else []
        return instruction_cls(kind="talk_npc", words=words, raw=line)
    if func == "say":
        match = _KW_SENT.search(args)
        sentence = match.group(1) if match else ""
        return instruction_cls(kind="say", sentence=sentence, raw=line)
    if func == "conditional_jump_script_options":
        var_match = _KW_VAR.search(args)
        jump_match = _KW_JUMP.search(args)
        skip_match = _KW_SKIP.search(args)
        return instruction_cls(
            kind="cond_jump",
            var_name=var_match.group(1) if var_match else "",
            label_jump=jump_match.group(1).lower() if jump_match else "",
            label_skip=skip_match.group(1).lower() if skip_match else "",
            raw=line,
        )
    return None


def parse_script_line(line: str, *, instruction_cls: Any, coord_cls: Any) -> Optional[Any]:
    line_lower = line.lower()

    for kind in ("node", "stand", "walk", "ladder", "shovel", "rope", "door"):
        if line_lower.startswith(kind):
            match = _COORD_RE.search(line)
            if match:
                coord = coord_cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                return instruction_cls(kind=_TEXT_ALIAS.get(kind, kind), coord=coord, raw=line)

    match = _LABEL_RE.match(line_lower)
    if match:
        return instruction_cls(kind="label", label=match.group(1), raw=line)

    match = _GOTO_RE.match(line)
    if match:
        return instruction_cls(kind="goto", label_jump=match.group(1).lower(), raw=line)

    match = _ACTION_RE.match(line_lower)
    if match:
        return instruction_cls(kind="action", action=match.group(1), raw=line)

    match = _WAIT_RE.match(line)
    if match:
        return instruction_cls(kind="wait", wait_secs=float(match.group(1)), raw=line)

    match = _USE_HOTKEY_RE.match(line)
    if match:
        return instruction_cls(kind="use_hotkey", hotkey_vk=_parse_hotkey(match.group(1)), raw=line)

    match = _USE_ITEM_RE.match(line)
    if match:
        hotkey = _parse_hotkey(match.group(2)) if match.group(2) else 0
        return instruction_cls(kind="use_item", item_name=match.group(1), hotkey_vk=hotkey, raw=line)

    match = _IF_RE.match(line)
    if match:
        return instruction_cls(
            kind="if_stat",
            stat=match.group(1).lower(),
            op=match.group(2),
            threshold=int(match.group(3)),
            goto_label=match.group(4).lower(),
            raw=line,
        )

    if _DEPOT_RE.match(line):
        return instruction_cls(kind="depot", raw=line)

    match = _CALL_RE.match(line)
    if match:
        parsed = _parse_call_line(
            line=line,
            args=match.group(2),
            func=match.group(1).lower(),
            instruction_cls=instruction_cls,
        )
        if parsed is not None:
            return parsed

    return instruction_cls(kind="unknown", raw=line)