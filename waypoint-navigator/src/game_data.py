"""
GameData — Static game data loader for monsters, spells, and combat helpers.

Loads JSON databases from ``data/`` and provides typed lookup methods
used by :class:`CombatManager`, :class:`AutoHealer`, and scripts.

Usage::

    from src.game_data import GameData

    gd = GameData()
    wasp = gd.get_monster("Wasp")
    assert wasp is not None
    print(wasp["hp"], wasp["exp"])

    spells = gd.get_attack_spells(max_level=35)
    heals  = gd.get_healing_spells(max_level=35)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_log = logging.getLogger("wn.gd2")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── Monster helpers ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MonsterInfo:
    """Immutable snapshot of a monster's wiki stats."""

    name: str
    hp: int
    exp: int
    armor: int
    speed: int
    max_damage: int
    classification: str
    abilities: list[str] = field(default_factory=list)
    damage_taken_pct: dict[str, int] = field(default_factory=dict)
    behaviour: dict[str, Any] = field(default_factory=dict)
    loot: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)

    # Convenience ----------------------------------------------------------
    @property
    def weakest_element(self) -> Optional[str]:
        """Return element with highest damage_taken_pct (>100 = weakness)."""
        if not self.damage_taken_pct:
            return None
        best = max(self.damage_taken_pct, key=self.damage_taken_pct.get)  # type: ignore[arg-type]
        return best if self.damage_taken_pct[best] > 100 else None

    @property
    def immune_elements(self) -> list[str]:
        """Elements the monster is immune to (0 %)."""
        return [e for e, v in self.damage_taken_pct.items() if v == 0]

    @property
    def runs_at_hp(self) -> int:
        """Absolute HP at which the monster starts fleeing, 0 = never."""
        pct = self.behaviour.get("runs_at_hp_pct", 0)
        return int(self.hp * pct / 100)


@dataclass(frozen=True)
class SpellInfo:
    """Lightweight spell descriptor."""

    name: str
    words: str
    level: int
    mana: int
    premium: bool = False
    cooldown: float = 2.0
    element: str = ""
    spell_type: str = ""  # single_target | aoe | frontal_aoe | ranged_single | chain
    description: str = ""


# ── Main class ──────────────────────────────────────────────────────────────


class GameData:
    """Singleton-ish loader for static game data JSON files.

    Parameters
    ----------
    data_dir : Path, optional
        Override the default ``data/`` folder.
    """

    def __init__(self, data_dir: Optional[Path] = None, vocation: str = "knight") -> None:
        self._data_dir = data_dir or _DATA_DIR
        self._vocation = vocation  # M7-fix: vocation-aware spell loading
        self._monsters: Dict[str, MonsterInfo] = {}
        self._spells_attack: List[SpellInfo] = []
        self._spells_healing: List[SpellInfo] = []
        self._spells_support: List[SpellInfo] = []
        self._raw_spells: Dict[str, Any] = {}
        self._raw_monsters: Dict[str, Any] = {}
        self._combat_rotations: Dict[str, Any] = {}
        self._loaded = False

    # ── Public API ────────────────────────────────────────────────────────

    def load(self) -> "GameData":
        """Load all JSON data files.  Safe to call multiple times."""
        if self._loaded:
            return self
        self._load_monsters()
        self._load_spells()
        self._loaded = True
        _log.info(
            "GameData loaded: %d monsters, %d attack / %d healing / %d support spells",
            len(self._monsters),
            len(self._spells_attack),
            len(self._spells_healing),
            len(self._spells_support),
        )
        return self

    # -- Monsters ----------------------------------------------------------

    def get_monster(self, name: str) -> Optional[MonsterInfo]:
        """Lookup a monster by name (case-insensitive)."""
        self._ensure_loaded()
        return self._monsters.get(name.lower())

    def get_all_monsters(self) -> List[MonsterInfo]:
        """Return all loaded monsters sorted by exp ascending."""
        self._ensure_loaded()
        return sorted(self._monsters.values(), key=lambda m: m.exp)

    def get_monsters_by_location(self, location_substr: str) -> List[MonsterInfo]:
        """Return monsters whose location contains *location_substr* (case-insensitive)."""
        self._ensure_loaded()
        loc = location_substr.lower()
        return [
            m for m in self._monsters.values()
            if any(loc in l.lower() for l in m.locations)
        ]

    def get_monsters_for_level_range(
        self, min_exp: int = 0, max_exp: int = 999_999
    ) -> List[MonsterInfo]:
        """Return monsters whose exp is within [min_exp, max_exp]."""
        self._ensure_loaded()
        return sorted(
            [m for m in self._monsters.values() if min_exp <= m.exp <= max_exp],
            key=lambda m: m.exp,
        )

    # -- Spells ------------------------------------------------------------

    def get_attack_spells(
        self, *, max_level: int = 999, premium_ok: bool = True
    ) -> List[SpellInfo]:
        """Return attack spells available at *max_level*."""
        self._ensure_loaded()
        return [
            s for s in self._spells_attack
            if s.level <= max_level and (premium_ok or not s.premium)
        ]

    def get_healing_spells(
        self, *, max_level: int = 999, premium_ok: bool = True
    ) -> List[SpellInfo]:
        """Return healing spells available at *max_level*."""
        self._ensure_loaded()
        return [
            s for s in self._spells_healing
            if s.level <= max_level and (premium_ok or not s.premium)
        ]

    def get_support_spells(
        self, *, max_level: int = 999, premium_ok: bool = True
    ) -> List[SpellInfo]:
        """Return support spells available at *max_level*."""
        self._ensure_loaded()
        return [
            s for s in self._spells_support
            if s.level <= max_level and (premium_ok or not s.premium)
        ]

    def get_spell_by_words(self, words: str) -> Optional[SpellInfo]:
        """Find a spell by its incantation words (case-insensitive)."""
        self._ensure_loaded()
        w = words.lower().strip()
        for pool in (self._spells_attack, self._spells_healing, self._spells_support):
            for s in pool:
                if s.words.lower() == w:
                    return s
        return None

    def get_combat_rotation(self, tier: str) -> Optional[Dict[str, Any]]:
        """Return a pre-built combat rotation dict for *tier*.

        Tiers: ``low_level_8_35``, ``mid_level_35_70``,
        ``high_level_70_200``, ``endgame_200plus``.
        """
        self._ensure_loaded()
        return self._combat_rotations.get(tier)

    def suggest_rotation_tier(self, level: int) -> str:
        """Map a character level to the recommended rotation tier key."""
        if level < 35:
            return "low_level_8_35"
        if level < 70:
            return "mid_level_35_70"
        if level < 200:
            return "high_level_70_200"
        return "endgame_200plus"

    # -- Best-spell helpers ------------------------------------------------

    def best_aoe_spell(self, level: int, available_mana: int) -> Optional[SpellInfo]:
        """Return the strongest AoE spell usable right now."""
        candidates = [
            s for s in self.get_attack_spells(max_level=level)
            if s.spell_type in ("aoe", "frontal_aoe") and s.mana <= available_mana
        ]
        if not candidates:
            return None
        # higher level = stronger
        return max(candidates, key=lambda s: s.level)

    def best_single_target_spell(
        self, level: int, available_mana: int
    ) -> Optional[SpellInfo]:
        """Return the strongest single-target spell usable right now."""
        candidates = [
            s for s in self.get_attack_spells(max_level=level)
            if s.spell_type in ("single_target", "ranged_single") and s.mana <= available_mana
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.level)

    def best_heal_spell(
        self, level: int, available_mana: int
    ) -> Optional[SpellInfo]:
        """Return the strongest instant heal usable right now."""
        candidates = [
            s for s in self.get_healing_spells(max_level=level)
            if s.mana <= available_mana
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.level)

    # ── Internals ─────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _load_monsters(self) -> None:
        path = self._data_dir / "monsters.json"
        if not path.exists():
            _log.warning("monsters.json not found at %s", path)
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        monsters_raw = data.get("monsters", {})

        # Support two formats:
        #  1) dict keyed by name: {"Wasp": {hp: ...}, "Rat": {hp: ...}}
        #  2) list of dicts with "name" field: [{name: "Wasp", hp: ...}, ...]
        if isinstance(monsters_raw, dict):
            items = [(name, vals) for name, vals in monsters_raw.items()]
        elif isinstance(monsters_raw, list):
            items = [(raw.get("name", ""), raw) for raw in monsters_raw]
        else:
            items = []  # pragma: no cover

        for name, raw in items:
            if not name:
                continue
            # Convert structured abilities list to flat strings if needed
            abilities = raw.get("abilities", [])
            if abilities and isinstance(abilities[0], dict):
                abilities = [self._ability_to_str(a) for a in abilities]
            # Normalise behaviour: accept both "runs_at_hp_pct" and "run_at_hp"
            behaviour = dict(raw.get("behaviour", {}))
            if "run_at_hp" in behaviour and "runs_at_hp_pct" not in behaviour:
                hp_val = raw.get("hp", 1) or 1
                run_hp = behaviour["run_at_hp"]
                behaviour["runs_at_hp_pct"] = int(run_hp / hp_val * 100) if run_hp else 0
            # Normalise loot: accept list of strings or list of dicts
            loot_raw = raw.get("loot", [])
            if loot_raw and isinstance(loot_raw[0], dict):
                loot = [l.get("item", l.get("name", str(l))) for l in loot_raw]
            else:
                loot = list(loot_raw)

            info = MonsterInfo(
                name=name,
                hp=raw.get("hp", 0),
                exp=raw.get("exp", 0),
                armor=raw.get("armor", 0),
                speed=raw.get("speed", 0),
                max_damage=raw.get("max_damage", 0),
                classification=raw.get("classification", ""),
                abilities=abilities,
                damage_taken_pct=raw.get("damage_taken_pct", {}),
                behaviour=behaviour,
                loot=loot,
                locations=raw.get("locations", []),
            )
            self._monsters[name.lower()] = info
        self._raw_monsters = data

    @staticmethod
    def _ability_to_str(a: Dict[str, Any]) -> str:
        """Convert a structured ability dict to a readable string."""
        atype = a.get("type", "")
        element = a.get("element", "")
        if atype == "melee":
            return f"Melee {a.get('min', 0)}-{a.get('max', 0)}"
        if atype == "condition":
            return f"{a.get('effect', element)} {a.get('damage_per_turn', 0)}hp/turn"
        if "min" in a and "max" in a:
            return f"{atype} ({element}) {a['min']}-{a['max']}"
        return f"{atype} {element}".strip()

    def _load_spells(self) -> None:
        # M7-fix: try vocation-specific file first, then knight, then generic
        vocation = getattr(self, '_vocation', 'knight')
        path = self._data_dir / f"{vocation}_spells.json"
        if not path.exists():
            path = self._data_dir / "knight_spells.json"
        if not path.exists():
            path = self._data_dir / "spells.json"
        if not path.exists():
            _log.warning("No spell data found in %s", self._data_dir)
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        self._raw_spells = data
        spells_section = data.get("spells", {})

        # Attack spells
        attack = spells_section.get("attack", {})
        for spell_raw in attack.get("instant", []):
            self._spells_attack.append(self._parse_spell(spell_raw, category="attack"))

        # Healing spells
        for spell_raw in spells_section.get("healing", []):
            self._spells_healing.append(self._parse_spell(spell_raw, category="healing"))

        # Support spells
        for spell_raw in spells_section.get("support", []):
            self._spells_support.append(self._parse_spell(spell_raw, category="support"))

        # Combat rotations
        self._combat_rotations = data.get("combat_rotation", {})

    @staticmethod
    def _parse_spell(raw: Dict[str, Any], category: str = "") -> SpellInfo:
        return SpellInfo(
            name=raw.get("name", ""),
            words=raw.get("words", ""),
            level=raw.get("level", 0),
            mana=raw.get("mana", 0),
            premium=raw.get("premium", False),
            cooldown=raw.get("cooldown", 2.0),
            element=raw.get("element", ""),
            spell_type=raw.get("type", ""),
            description=raw.get("description", ""),
        )
