"""Tests for src.game_data — static game-data loader."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.game_data import GameData, MonsterInfo, SpellInfo


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Create a temporary data/ directory with minimal JSON data files."""
    d = tmp_path / "data"
    d.mkdir()

    monsters = {
        "_meta": {"source": "test"},
        "monsters": [
            {
                "name": "Wasp",
                "hp": 35,
                "exp": 24,
                "armor": 4,
                "speed": 160,
                "max_damage": 22,
                "classification": "Arthropod",
                "abilities": ["Melee 0-20", "Poison 2hp/turn"],
                "damage_taken_pct": {
                    "physical": 100,
                    "fire": 110,
                    "ice": 100,
                    "earth": 0,
                    "energy": 80,
                    "holy": 100,
                    "death": 100,
                },
                "behaviour": {
                    "retargets": True,
                    "runs_at_hp_pct": 0,
                    "pushable": True,
                },
                "loot": ["Gold Coin", "Honeycomb"],
                "locations": ["South of Thais", "Darashia"],
            },
            {
                "name": "Dragon",
                "hp": 1000,
                "exp": 700,
                "armor": 25,
                "speed": 86,
                "max_damage": 430,
                "classification": "Reptile",
                "abilities": ["Melee 0-120", "Fire Wave 0-170", "Fire Ball 0-140"],
                "damage_taken_pct": {
                    "physical": 100,
                    "fire": 0,
                    "ice": 110,
                    "earth": 80,
                    "energy": 100,
                    "holy": 100,
                    "death": 100,
                },
                "behaviour": {
                    "retargets": True,
                    "runs_at_hp_pct": 30,
                    "pushable": False,
                },
                "loot": ["Gold Coin", "Dragon Ham"],
                "locations": ["Darashia Dragon Lair", "Thais Ancient Temple -4"],
            },
            {
                "name": "Rat",
                "hp": 20,
                "exp": 5,
                "armor": 1,
                "speed": 80,
                "max_damage": 8,
                "classification": "Mammal",
                "abilities": ["Melee 0-8"],
                "damage_taken_pct": {"physical": 100, "fire": 110, "earth": 100},
                "behaviour": {"retargets": False, "runs_at_hp_pct": 0},
                "loot": ["Gold Coin", "Cheese"],
                "locations": ["Thais Sewers", "Rookgaard"],
            },
        ],
    }

    spells = {
        "_meta": {"source": "test"},
        "vocation": "Knight",
        "spells": {
            "attack": {
                "instant": [
                    {
                        "name": "Brutal Strike",
                        "words": "exori ico",
                        "level": 16,
                        "mana": 30,
                        "premium": False,
                        "type": "single_target",
                        "element": "physical",
                        "cooldown": 6,
                    },
                    {
                        "name": "Whirlwind Throw",
                        "words": "exori hur",
                        "level": 28,
                        "mana": 40,
                        "premium": True,
                        "type": "ranged_single",
                        "element": "physical",
                        "cooldown": 6,
                    },
                    {
                        "name": "Berserk",
                        "words": "exori",
                        "level": 35,
                        "mana": 115,
                        "premium": True,
                        "type": "aoe",
                        "element": "physical",
                        "cooldown": 4,
                    },
                    {
                        "name": "Front Sweep",
                        "words": "exori min",
                        "level": 70,
                        "mana": 200,
                        "premium": True,
                        "type": "frontal_aoe",
                        "element": "physical",
                        "cooldown": 6,
                    },
                    {
                        "name": "Fierce Berserk",
                        "words": "exori gran",
                        "level": 90,
                        "mana": 340,
                        "premium": True,
                        "type": "aoe",
                        "element": "physical",
                        "cooldown": 6,
                    },
                    {
                        "name": "Annihilation",
                        "words": "exori gran ico",
                        "level": 110,
                        "mana": 300,
                        "premium": True,
                        "type": "single_target",
                        "element": "physical",
                        "cooldown": 30,
                    },
                ]
            },
            "healing": [
                {
                    "name": "Wound Cleansing",
                    "words": "exura ico",
                    "level": 8,
                    "mana": 40,
                    "premium": False,
                    "cooldown": 1,
                },
                {
                    "name": "Intense Wound Cleansing",
                    "words": "exura gran ico",
                    "level": 80,
                    "mana": 200,
                    "premium": True,
                    "cooldown": 600,
                },
            ],
            "support": [
                {
                    "name": "Haste",
                    "words": "utani hur",
                    "level": 14,
                    "mana": 60,
                    "premium": False,
                    "cooldown": 2,
                },
                {
                    "name": "Challenge",
                    "words": "exeta res",
                    "level": 20,
                    "mana": 40,
                    "premium": True,
                    "cooldown": 2,
                },
                {
                    "name": "Blood Rage",
                    "words": "utito tempo",
                    "level": 60,
                    "mana": 290,
                    "premium": True,
                    "cooldown": 2,
                },
            ],
        },
        "combat_rotation": {
            "low_level_8_35": {
                "single_target": ["exori ico"],
                "healing": ["exura ico"],
            },
            "mid_level_35_70": {
                "aoe_3plus": ["exori"],
                "single_target": ["exori ico", "exori hur"],
                "healing": ["exura ico"],
            },
            "high_level_70_200": {
                "aoe_3plus": ["exori gran", "exori min", "exori"],
                "single_target": ["exori gran ico", "exori ico"],
                "healing": ["exura gran ico", "exura ico"],
            },
            "endgame_200plus": {
                "aoe_3plus": ["exori gran", "exori min", "exori"],
                "single_target": ["exori gran ico", "exori ico"],
                "healing": ["exura gran ico"],
            },
        },
    }

    (d / "monsters.json").write_text(json.dumps(monsters), encoding="utf-8")
    (d / "knight_spells.json").write_text(json.dumps(spells), encoding="utf-8")
    return d


@pytest.fixture()
def gd(data_dir: Path) -> GameData:
    """Return a ready-to-use GameData loaded from temp data."""
    return GameData(data_dir=data_dir).load()


@pytest.fixture()
def empty_gd(tmp_path: Path) -> GameData:
    """GameData pointing at an empty directory."""
    d = tmp_path / "empty"
    d.mkdir()
    return GameData(data_dir=d).load()


# ═══════════════════════════════════════════════════════════════════════════════
# MonsterInfo dataclass
# ═══════════════════════════════════════════════════════════════════════════════


class TestMonsterInfo:
    def test_basic_fields(self):
        m = MonsterInfo(name="Wasp", hp=35, exp=24, armor=4, speed=160,
                        max_damage=22, classification="Arthropod")
        assert m.name == "Wasp"
        assert m.hp == 35
        assert m.exp == 24

    def test_weakest_element_fire(self):
        m = MonsterInfo(
            name="Wasp", hp=35, exp=24, armor=4, speed=160,
            max_damage=22, classification="Arthropod",
            damage_taken_pct={"fire": 110, "earth": 0, "physical": 100},
        )
        assert m.weakest_element == "fire"

    def test_weakest_element_none_when_empty(self):
        m = MonsterInfo(name="X", hp=1, exp=1, armor=0, speed=0,
                        max_damage=0, classification="")
        assert m.weakest_element is None

    def test_weakest_element_none_when_all_100(self):
        m = MonsterInfo(
            name="X", hp=1, exp=1, armor=0, speed=0,
            max_damage=0, classification="",
            damage_taken_pct={"fire": 100, "ice": 100},
        )
        assert m.weakest_element is None

    def test_immune_elements(self):
        m = MonsterInfo(
            name="Dragon", hp=1000, exp=700, armor=25, speed=86,
            max_damage=430, classification="Reptile",
            damage_taken_pct={"fire": 0, "ice": 110, "earth": 80},
        )
        assert m.immune_elements == ["fire"]

    def test_immune_elements_empty(self):
        m = MonsterInfo(name="Rat", hp=20, exp=5, armor=1, speed=80,
                        max_damage=8, classification="Mammal",
                        damage_taken_pct={"physical": 100})
        assert m.immune_elements == []

    def test_runs_at_hp(self):
        m = MonsterInfo(
            name="Dragon", hp=1000, exp=700, armor=25, speed=86,
            max_damage=430, classification="Reptile",
            behaviour={"runs_at_hp_pct": 30},
        )
        assert m.runs_at_hp == 300

    def test_runs_at_hp_zero_when_never(self):
        m = MonsterInfo(name="Wasp", hp=35, exp=24, armor=4, speed=160,
                        max_damage=22, classification="Arthropod",
                        behaviour={"runs_at_hp_pct": 0})
        assert m.runs_at_hp == 0

    def test_frozen(self):
        m = MonsterInfo(name="Wasp", hp=35, exp=24, armor=4, speed=160,
                        max_damage=22, classification="Arthropod")
        with pytest.raises(AttributeError):
            m.hp = 999  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# SpellInfo dataclass
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpellInfo:
    def test_basic(self):
        s = SpellInfo(name="Berserk", words="exori", level=35, mana=115)
        assert s.name == "Berserk"
        assert s.words == "exori"
        assert s.level == 35
        assert s.mana == 115

    def test_defaults(self):
        s = SpellInfo(name="Test", words="test", level=1, mana=10)
        assert s.premium is False
        assert s.cooldown == 2.0
        assert s.element == ""

    def test_frozen(self):
        s = SpellInfo(name="Test", words="test", level=1, mana=10)
        with pytest.raises(AttributeError):
            s.mana = 999  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# GameData — loading
# ═══════════════════════════════════════════════════════════════════════════════


class TestGameDataLoading:
    def test_load_returns_self(self, data_dir: Path):
        gd = GameData(data_dir=data_dir)
        result = gd.load()
        assert result is gd

    def test_double_load_safe(self, gd: GameData):
        gd.load()  # second call should be no-op
        assert len(gd.get_all_monsters()) == 3

    def test_empty_dir_no_crash(self, empty_gd: GameData):
        assert empty_gd.get_all_monsters() == []
        assert empty_gd.get_attack_spells() == []

    def test_auto_loads_on_first_query(self, data_dir: Path):
        gd = GameData(data_dir=data_dir)
        # Don't call .load() explicitly — should auto-load
        m = gd.get_monster("Wasp")
        assert m is not None
        assert m.hp == 35


# ═══════════════════════════════════════════════════════════════════════════════
# GameData — monsters
# ═══════════════════════════════════════════════════════════════════════════════


class TestGameDataMonsters:
    def test_get_monster_case_insensitive(self, gd: GameData):
        assert gd.get_monster("wasp") is not None
        assert gd.get_monster("WASP") is not None
        assert gd.get_monster("Wasp") is not None

    def test_get_monster_missing(self, gd: GameData):
        assert gd.get_monster("Demon") is None

    def test_get_all_monsters_sorted_by_exp(self, gd: GameData):
        monsters = gd.get_all_monsters()
        assert len(monsters) == 3
        exps = [m.exp for m in monsters]
        assert exps == sorted(exps)  # ascending

    def test_get_monsters_by_location(self, gd: GameData):
        thais = gd.get_monsters_by_location("thais")
        names = {m.name for m in thais}
        assert "Wasp" in names
        assert "Rat" in names

    def test_get_monsters_by_location_partial(self, gd: GameData):
        darashia = gd.get_monsters_by_location("darashia")
        names = {m.name for m in darashia}
        assert "Wasp" in names
        assert "Dragon" in names

    def test_get_monsters_by_location_no_match(self, gd: GameData):
        assert gd.get_monsters_by_location("fibula") == []

    def test_get_monsters_for_level_range(self, gd: GameData):
        low = gd.get_monsters_for_level_range(min_exp=0, max_exp=25)
        names = {m.name for m in low}
        assert "Wasp" in names
        assert "Rat" in names
        assert "Dragon" not in names

    def test_monster_wasp_details(self, gd: GameData):
        wasp = gd.get_monster("wasp")
        assert wasp is not None
        assert wasp.hp == 35
        assert wasp.exp == 24
        assert wasp.armor == 4
        assert wasp.speed == 160
        assert wasp.weakest_element == "fire"
        assert "earth" in wasp.immune_elements
        assert wasp.runs_at_hp == 0

    def test_monster_dragon_details(self, gd: GameData):
        dragon = gd.get_monster("dragon")
        assert dragon is not None
        assert dragon.hp == 1000
        assert dragon.exp == 700
        assert dragon.weakest_element == "ice"
        assert "fire" in dragon.immune_elements
        assert dragon.runs_at_hp == 300


# ═══════════════════════════════════════════════════════════════════════════════
# GameData — spells
# ═══════════════════════════════════════════════════════════════════════════════


class TestGameDataSpells:
    def test_attack_spells_total(self, gd: GameData):
        attacks = gd.get_attack_spells()
        assert len(attacks) == 6

    def test_attack_spells_level_filter(self, gd: GameData):
        low = gd.get_attack_spells(max_level=20)
        assert len(low) == 1
        assert low[0].words == "exori ico"

    def test_attack_spells_premium_filter(self, gd: GameData):
        free = gd.get_attack_spells(premium_ok=False)
        assert all(not s.premium for s in free)
        assert len(free) == 1  # only Brutal Strike is free

    def test_healing_spells(self, gd: GameData):
        heals = gd.get_healing_spells()
        assert len(heals) == 2

    def test_healing_spells_level_filter(self, gd: GameData):
        low = gd.get_healing_spells(max_level=20)
        assert len(low) == 1
        assert low[0].words == "exura ico"

    def test_support_spells(self, gd: GameData):
        support = gd.get_support_spells()
        assert len(support) == 3

    def test_support_spells_level_filter(self, gd: GameData):
        low = gd.get_support_spells(max_level=15)
        assert len(low) == 1  # only Haste at lvl 14

    def test_get_spell_by_words(self, gd: GameData):
        s = gd.get_spell_by_words("exori ico")
        assert s is not None
        assert s.name == "Brutal Strike"

    def test_get_spell_by_words_case_insensitive(self, gd: GameData):
        s = gd.get_spell_by_words("EXORI ICO")
        assert s is not None
        assert s.name == "Brutal Strike"

    def test_get_spell_by_words_not_found(self, gd: GameData):
        assert gd.get_spell_by_words("exori flam") is None

    def test_spell_by_words_healing(self, gd: GameData):
        s = gd.get_spell_by_words("exura ico")
        assert s is not None
        assert s.name == "Wound Cleansing"

    def test_spell_by_words_support(self, gd: GameData):
        s = gd.get_spell_by_words("utani hur")
        assert s is not None
        assert s.name == "Haste"


# ═══════════════════════════════════════════════════════════════════════════════
# GameData — combat rotations
# ═══════════════════════════════════════════════════════════════════════════════


class TestGameDataRotations:
    def test_suggest_rotation_tier_low(self, gd: GameData):
        assert gd.suggest_rotation_tier(10) == "low_level_8_35"
        assert gd.suggest_rotation_tier(34) == "low_level_8_35"

    def test_suggest_rotation_tier_mid(self, gd: GameData):
        assert gd.suggest_rotation_tier(35) == "mid_level_35_70"
        assert gd.suggest_rotation_tier(69) == "mid_level_35_70"

    def test_suggest_rotation_tier_high(self, gd: GameData):
        assert gd.suggest_rotation_tier(70) == "high_level_70_200"
        assert gd.suggest_rotation_tier(199) == "high_level_70_200"

    def test_suggest_rotation_tier_endgame(self, gd: GameData):
        assert gd.suggest_rotation_tier(200) == "endgame_200plus"
        assert gd.suggest_rotation_tier(500) == "endgame_200plus"

    def test_get_combat_rotation(self, gd: GameData):
        rot = gd.get_combat_rotation("low_level_8_35")
        assert rot is not None
        assert "single_target" in rot
        assert "exori ico" in rot["single_target"]

    def test_get_combat_rotation_missing(self, gd: GameData):
        assert gd.get_combat_rotation("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════════════
# GameData — best-spell helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestGameDataBestSpell:
    def test_best_aoe_low_level(self, gd: GameData):
        # At level 20, no AoE spells available
        s = gd.best_aoe_spell(level=20, available_mana=200)
        assert s is None

    def test_best_aoe_mid_level(self, gd: GameData):
        s = gd.best_aoe_spell(level=40, available_mana=200)
        assert s is not None
        assert s.words == "exori"

    def test_best_aoe_high_level(self, gd: GameData):
        s = gd.best_aoe_spell(level=95, available_mana=400)
        assert s is not None
        assert s.words == "exori gran"  # Fierce Berserk at lvl 90

    def test_best_aoe_mana_limited(self, gd: GameData):
        # Has level for Fierce Berserk (340 mana) but only 200 mana
        s = gd.best_aoe_spell(level=95, available_mana=200)
        assert s is not None
        # Should pick Front Sweep (200 mana, lvl 70) or lower
        assert s.mana <= 200

    def test_best_single_target_low(self, gd: GameData):
        s = gd.best_single_target_spell(level=20, available_mana=50)
        assert s is not None
        assert s.words == "exori ico"

    def test_best_single_target_high(self, gd: GameData):
        s = gd.best_single_target_spell(level=120, available_mana=400)
        assert s is not None
        assert s.words == "exori gran ico"  # Annihilation

    def test_best_single_target_no_mana(self, gd: GameData):
        s = gd.best_single_target_spell(level=120, available_mana=10)
        assert s is None

    def test_best_heal_low_level(self, gd: GameData):
        s = gd.best_heal_spell(level=20, available_mana=50)
        assert s is not None
        assert s.words == "exura ico"

    def test_best_heal_high_level(self, gd: GameData):
        s = gd.best_heal_spell(level=100, available_mana=300)
        assert s is not None
        assert s.words == "exura gran ico"

    def test_best_heal_no_mana(self, gd: GameData):
        s = gd.best_heal_spell(level=100, available_mana=5)
        assert s is None


# ═══════════════════════════════════════════════════════════════════════════════
# GameData — integration with real data files
# ═══════════════════════════════════════════════════════════════════════════════


class TestGameDataRealFiles:
    """Tests that use the actual data/ JSON files shipped with the project."""

    _REAL_DATA = Path(__file__).resolve().parent.parent / "data"

    @pytest.mark.skipif(
        not (_REAL_DATA / "monsters.json").exists(),
        reason="Real data/monsters.json not present",
    )
    def test_load_real_monsters(self):
        gd = GameData(data_dir=self._REAL_DATA).load()
        monsters = gd.get_all_monsters()
        assert len(monsters) >= 10  # should have 12
        wasp = gd.get_monster("Wasp")
        assert wasp is not None
        assert wasp.hp == 35

    @pytest.mark.skipif(
        not (_REAL_DATA / "knight_spells.json").exists(),
        reason="Real data/knight_spells.json not present",
    )
    def test_load_real_spells(self):
        gd = GameData(data_dir=self._REAL_DATA).load()
        attacks = gd.get_attack_spells()
        assert len(attacks) >= 6
        heals = gd.get_healing_spells()
        assert len(heals) >= 2

    @pytest.mark.skipif(
        not (_REAL_DATA / "knight_spells.json").exists(),
        reason="Real data/knight_spells.json not present",
    )
    def test_real_combat_rotations(self):
        gd = GameData(data_dir=self._REAL_DATA).load()
        for tier in ["low_level_8_35", "mid_level_35_70",
                      "high_level_70_200", "endgame_200plus"]:
            rot = gd.get_combat_rotation(tier)
            assert rot is not None, f"Missing rotation: {tier}"


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestGameDataEdgeCases:
    def test_monster_with_no_name_skipped(self, tmp_path: Path):
        d = tmp_path / "edge"
        d.mkdir()
        data = {"monsters": [{"name": "", "hp": 10, "exp": 1}]}
        (d / "monsters.json").write_text(json.dumps(data), encoding="utf-8")
        gd = GameData(data_dir=d).load()
        assert gd.get_all_monsters() == []

    def test_partial_monster_data(self, tmp_path: Path):
        d = tmp_path / "partial"
        d.mkdir()
        data = {"monsters": [{"name": "Blob"}]}
        (d / "monsters.json").write_text(json.dumps(data), encoding="utf-8")
        gd = GameData(data_dir=d).load()
        blob = gd.get_monster("Blob")
        assert blob is not None
        assert blob.hp == 0  # defaults
        assert blob.exp == 0

    def test_missing_spells_sections(self, tmp_path: Path):
        d = tmp_path / "nospells"
        d.mkdir()
        data: dict[str, object] = {"spells": {}}
        (d / "knight_spells.json").write_text(json.dumps(data), encoding="utf-8")
        gd = GameData(data_dir=d).load()
        assert gd.get_attack_spells() == []
        assert gd.get_healing_spells() == []

    def test_spells_fallback_to_generic(self, tmp_path: Path):
        d = tmp_path / "fallback"
        d.mkdir()
        # No knight_spells.json, but spells.json exists
        data = {
            "spells": {
                "attack": {"instant": [
                    {"name": "Strike", "words": "exori", "level": 1, "mana": 10}
                ]},
                "healing": [],
                "support": [],
            }
        }
        (d / "spells.json").write_text(json.dumps(data), encoding="utf-8")
        gd = GameData(data_dir=d).load()
        assert len(gd.get_attack_spells()) == 1
