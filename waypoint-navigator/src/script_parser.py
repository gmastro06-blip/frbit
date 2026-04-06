"""
Parser for the .in waypoint script format used by frbot.

Instruction types:
  node   (x,y,z)           – walk to coord via A*
  stand  (x,y,z)           – walk to exact tile (precise)
  walk   (x,y,z)           – alias for stand (WasP compat)
  door   (x,y,z)           – open door at tile (alias for open_door)
  ladder (x,y,z)           – use ladder/stairs/hole at tile
  shovel (x,y,z)           – use shovel at tile
  rope   (x,y,z)           – use rope at tile
  label  <name>            – define a named jump point
  goto   <name>            – unconditional jump to label
  action <name>            – special action (travel, wait, end, combat_pause,
                             combat_resume, combat_start, combat_stop, …)
  use_item <name> [vk=N]   – use an item by hotkey (vk= hex or dec)
  use_hotkey <vk>          – press a hotkey (VK hex or dec)
  wait <seconds>           – pause execution for N seconds
  if hp < N goto <label>   – jump if HP% < threshold
  if hp > N goto <label>   – jump if HP% > threshold
  if mp < N goto <label>   – jump if MP% < threshold
  if mp > N goto <label>   – jump if MP% > threshold
  depot                    – trigger a DepotManager cycle (empty backpack, restock)
  call talk_npc(…)         – NPC dialogue
  call say(…)              – character speech
  call conditional_jump_script_options(…) – conditional branch
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Any

from .script_parser_parsing import parse_json_script_entries, parse_script_line

if TYPE_CHECKING:
    from src.models import Coordinate


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ScriptCoord:
    x: int
    y: int
    z: int

    def __str__(self) -> str:
        return f"({self.x},{self.y},{self.z})"

    def to_tibia_coord(self) -> "Coordinate":
        from src.models import Coordinate
        return Coordinate(self.x, self.y, self.z)


@dataclass
class Instruction:
    kind: str                       # node | stand | ladder | shovel | rope |
                                    # label | goto | action | use_item |
                                    # use_hotkey | wait | if_stat |
                                       # open_door | talk_npc | say |
                                       # cond_jump | unknown
    coord: Optional[ScriptCoord] = None
    label: str = ""
    action: str = ""
    words: List[str] = field(default_factory=list)
    sentence: str = ""
    var_name: str = ""
    label_jump: str = ""
    label_skip: str = ""
    raw: str = ""
    # use_item / use_hotkey
    item_name: str = ""
    hotkey_vk: int = 0             # VK code (0 = unset)
    # wait
    wait_secs: float = 0.0
    # if_stat
    stat: str = ""                 # 'hp' | 'mp'
    op: str = ""                   # '<' | '>'
    threshold: int = 0
    goto_label: str = ""
    # movement hint (floor_transition | door | '')
    hint: str = ""
    # random_stand: list of candidate coordinates (one chosen per execution)
    choices: List[ScriptCoord] = field(default_factory=list)

    def __str__(self) -> str:
        if self.kind in ("node", "stand", "ladder", "shovel", "rope", "open_door"):
            return f"{self.kind:10s} {self.coord}"
        if self.kind == "label":
            return f"label      [{self.label}]"
        if self.kind == "goto":
            return f"goto       [{self.label_jump}]"
        if self.kind == "action":
            return f"action     {self.action}"
        if self.kind == "use_item":
            vk_s = f" vk={self.hotkey_vk:#x}" if self.hotkey_vk else ""
            return f"use_item   {self.item_name}{vk_s}"
        if self.kind == "use_hotkey":
            return f"use_hotkey {self.hotkey_vk:#x}"
        if self.kind == "wait":
            return f"wait       {self.wait_secs}s"
        if self.kind == "if_stat":
            return (f"if {self.stat} {self.op} {self.threshold} "
                    f"goto [{self.goto_label}]")
        if self.kind == "talk_npc":
            return f"talk_npc   {self.words}"
        if self.kind == "say":
            return f"say        {self.sentence!r}"
        if self.kind == "cond_jump":
            return (f"cond_jump  var={self.var_name} "
                    f"jump={self.label_jump} skip={self.label_skip}")
        if self.kind == "depot":
            return "depot"
        return f"?{self.kind} {self.raw}"

    @property
    def is_movement(self) -> bool:
        """True when the instruction moves the character to a tile."""
        return self.kind in ("node", "stand", "ladder", "shovel", "rope")

    @property
    def is_jump(self) -> bool:
        """True when the instruction is an unconditional or conditional jump."""
        return self.kind in ("goto", "if_stat")

    @property
    def is_conditional(self) -> bool:
        """True when the instruction is a conditional jump (``if_stat``)."""
        return self.kind == "if_stat"

    @property
    def has_coord(self) -> bool:
        """True when the instruction carries a tile coordinate."""
        return self.coord is not None

    @property
    def is_label(self) -> bool:
        """True when the instruction defines a named jump target (``label``)."""
        return self.kind == "label"

    @property
    def is_wait(self) -> bool:
        """True when the instruction pauses execution for ``wait_secs`` seconds."""
        return self.kind == "wait"

    @property
    def is_action(self) -> bool:
        """True when the instruction triggers a named action (``action``)."""
        return self.kind == "action"

    @property
    def is_goto(self) -> bool:
        """True when the instruction is an unconditional jump (``goto``)."""
        return self.kind == "goto"

    @property
    def is_node(self) -> bool:
        """True when the instruction is a standard tile-movement node."""
        return self.kind == "node"

    @property
    def is_depot(self) -> bool:
        """True when the instruction triggers a DepotManager cycle."""
        return self.kind == "depot"


# ── Parser ────────────────────────────────────────────────────────────────────

class ScriptParser:
    """Parse a .in waypoint file into a list of Instructions."""

    @staticmethod
    def parse_file(path: Path) -> List[Instruction]:
        text = path.read_text(encoding="utf-8", errors="replace")
        stripped = text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return ScriptParser.parse_text(text)

            if isinstance(data, dict):
                if isinstance(data.get("script"), list):
                    return ScriptParser.from_json_script(data["script"])
                if isinstance(data.get("entries"), list):
                    return ScriptParser.from_json_script(data["entries"])
            elif isinstance(data, list):
                dict_entries = [entry for entry in data if isinstance(entry, dict)]
                if dict_entries:
                    return ScriptParser.from_json_script(dict_entries)

        return ScriptParser.parse_text(text)

    @staticmethod
    def from_json_script(entries: List[dict[str, Any]]) -> List["Instruction"]:
        """Convert a JSON ``script`` array (from a unified route JSON) to a
        list of :class:`Instruction` objects.

        Each entry must have a ``"kind"`` key matching one of the recognised
        instruction types.  Unknown kinds are stored as ``unknown``.
        """
        return parse_json_script_entries(
            entries,
            instruction_cls=Instruction,
            coord_cls=ScriptCoord,
        )

    @staticmethod
    def parse_text(text: str) -> List[Instruction]:
        instructions: List[Instruction] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            ins = ScriptParser._parse_line(line)
            if ins:
                instructions.append(ins)
        return instructions

    @staticmethod
    def label_map(instructions: List[Instruction]) -> Dict[str, int]:
        """
        Build a mapping from label name -> instruction index for O(1) jumps.

        Only ``label`` instructions are included.  The index is the position
        of the label instruction itself in the list.

        Example
        -------
        >>> ins = ScriptParser.parse_text(script_text)
        >>> lmap = ScriptParser.label_map(ins)
        >>> lmap["loop_start"]   # returns instruction index
        """
        return {
            ins.label: idx
            for idx, ins in enumerate(instructions)
            if ins.kind == "label"
        }

    @staticmethod
    def validate_labels(instructions: List[Instruction]) -> List[str]:
        """Check that every jump target references an existing label.

        Returns a list of error strings (empty means valid).  Inspects:
        - ``goto``      → ``label_jump``
        - ``if_stat``   → ``goto_label``
        - ``cond_jump`` → ``label_jump`` and ``label_skip``
        """
        labels = {ins.label for ins in instructions if ins.kind == "label"}
        errors: List[str] = []
        for idx, ins in enumerate(instructions):
            if ins.kind == "goto" and ins.label_jump not in labels:
                errors.append(
                    f"[{idx}] goto references undefined label '{ins.label_jump}'"
                )
            elif ins.kind == "if_stat" and ins.goto_label not in labels:
                errors.append(
                    f"[{idx}] if_stat references undefined label '{ins.goto_label}'"
                )
            elif ins.kind == "cond_jump":
                if ins.label_jump and ins.label_jump not in labels:
                    errors.append(
                        f"[{idx}] cond_jump label_jump references undefined label '{ins.label_jump}'"
                    )
                if ins.label_skip and ins.label_skip not in labels:
                    errors.append(
                        f"[{idx}] cond_jump label_skip references undefined label '{ins.label_skip}'"
                    )
        return errors

    @staticmethod
    def filter_by_kind(instructions: List[Instruction], *kinds: str) -> List[Instruction]:
        """
        Return a new list containing only instructions whose ``kind`` is in
        *kinds*.  Accepts one or more kind strings.

        Example
        -------
        >>> movements = ScriptParser.filter_by_kind(ins, "node", "stand")
        >>> jumps = ScriptParser.filter_by_kind(ins, "goto", "if_stat")
        """
        kind_set = set(kinds)
        return [i for i in instructions if i.kind in kind_set]

    @staticmethod
    def count_by_kind(instructions: List[Instruction]) -> Dict[str, int]:
        """
        Return a ``{kind: count}`` dict for all instructions in the list.

        Useful for quick profiling of a script (e.g. how many movement nodes,
        how many conditional jumps, etc.).

        Example
        -------
        >>> counts = ScriptParser.count_by_kind(ins)
        >>> counts.get("node", 0)
        15
        """
        result: Dict[str, int] = {}
        for ins in instructions:
            result[ins.kind] = result.get(ins.kind, 0) + 1
        return result

    @staticmethod
    def movement_coords(instructions: List[Instruction]) -> List[ScriptCoord]:
        """
        Extract the :class:`ScriptCoord` from every movement instruction.

        Returns a list of :class:`ScriptCoord` objects in script order,
        skipping any movement instruction that (pathologically) has no coord.

        Example
        -------
        >>> coords = ScriptParser.movement_coords(ins)
        >>> [(c.x, c.y, c.z) for c in coords]
        """
        return [
            ins.coord
            for ins in instructions
            if ins.is_movement and ins.coord is not None
        ]

    @staticmethod
    def unique_kinds(instructions: List[Instruction]) -> List[str]:
        """Return a sorted list of distinct instruction kinds present in *instructions*.

        Example
        -------
        >>> ScriptParser.unique_kinds(ins)
        ['goto', 'label', 'node', 'stand']
        """
        return sorted({ins.kind for ins in instructions})

    @staticmethod
    def has_label(instructions: List[Instruction], name: str) -> bool:
        """Return True when a ``label`` instruction with *name* exists.

        The comparison is case-insensitive, matching how :meth:`parse_text`
        normalises label text.

        Example
        -------
        >>> ScriptParser.has_label(ins, "loop")
        True
        """
        target = name.lower()
        return any(ins.kind == "label" and ins.label == target for ins in instructions)

    @staticmethod
    def script_stats(instructions: List[Instruction]) -> Dict[str, int]:
        """Return a summary dict of key instruction-type counts.

        Keys
        ----
        total           Total number of instructions.
        movement        Instructions that move the character (node/stand/ladder/…).
        jumps           Unconditional + conditional jumps (goto + if_stat).
        labels          Number of label definitions.
        actions         Number of action instructions.
        waits           Number of wait instructions.
        unique_kinds    Number of distinct instruction types.

        Example
        -------
        >>> stats = ScriptParser.script_stats(ins)
        >>> stats["movement"]
        12
        """
        counts = ScriptParser.count_by_kind(instructions)
        return {
            "total":        len(instructions),
            "movement":     sum(counts.get(k, 0) for k in ("node", "stand", "ladder", "shovel", "rope")),
            "jumps":        counts.get("goto", 0) + counts.get("if_stat", 0),
            "labels":       counts.get("label", 0),
            "actions":      counts.get("action", 0),
            "waits":        counts.get("wait", 0),
            "depots":       counts.get("depot", 0),
            "unique_kinds": len(set(ins.kind for ins in instructions)),
        }

    @staticmethod
    def _parse_line(line: str) -> Optional[Instruction]:
        return parse_script_line(line, instruction_cls=Instruction, coord_cls=ScriptCoord)
