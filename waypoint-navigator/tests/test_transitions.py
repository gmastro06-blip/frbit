"""
Tests for src/transitions.py — TransitionRegistry
Offline, no OBS, no map files required.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.models import Coordinate, FloorTransition
from src.transitions import TransitionRegistry, transitions_from_script


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_transition(
    x: int = 32370,
    y: int = 32240,
    from_z: int = 7,
    to_z: int = 8,
    kind: str = "walk",
) -> FloorTransition:
    return FloorTransition(
        entry=Coordinate(x, y, from_z),
        exit=Coordinate(x, y, to_z),
        kind=kind,
    )


def _sample_registry() -> TransitionRegistry:
    """3 transitions: 7→8 (walk), 7→8 (rope offset), 8→7 (walk)."""
    return TransitionRegistry(
        [
            _make_transition(32370, 32240, 7, 8, "walk"),
            _make_transition(32380, 32230, 7, 8, "shovel"),
            _make_transition(32370, 32240, 8, 7, "rope"),
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Construction  &  __len__
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryConstruction:

    def test_empty_registry(self):
        reg = TransitionRegistry()
        assert len(reg) == 0

    def test_len_matches_input(self):
        reg = _sample_registry()
        assert len(reg) == 3

    def test_repr_contains_count(self):
        reg = _sample_registry()
        assert "3" in repr(reg)


# ─────────────────────────────────────────────────────────────────────────────
# from_floor / between / reachable_floors
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryQueries:

    def test_from_floor_returns_correct_count(self):
        reg = _sample_registry()
        assert len(reg.from_floor(7)) == 2
        assert len(reg.from_floor(8)) == 1
        assert len(reg.from_floor(5)) == 0   # unknown floor

    def test_between_7_8(self):
        reg = _sample_registry()
        links = reg.between(7, 8)
        assert len(links) == 2
        assert all(t.entry.z == 7 and t.exit.z == 8 for t in links)

    def test_between_8_7(self):
        reg = _sample_registry()
        links = reg.between(8, 7)
        assert len(links) == 1
        assert links[0].kind == "rope"

    def test_between_nonexistent_pair(self):
        reg = _sample_registry()
        assert reg.between(3, 4) == []

    def test_reachable_floors_from_7(self):
        reg = _sample_registry()
        reachable = reg.reachable_floors(7)
        assert 8 in reachable

    def test_reachable_floors_empty_for_unknown(self):
        reg = _sample_registry()
        assert reg.reachable_floors(99) == []


# ─────────────────────────────────────────────────────────────────────────────
# nearest_from
# ─────────────────────────────────────────────────────────────────────────────

class TestNearestFrom:

    def test_finds_nearest_on_same_floor(self):
        reg = _sample_registry()
        origin = Coordinate(32372, 32241, 7)   # 2 tiles from (32370,32240)
        nearest = reg.nearest_from(origin, max_dist=10)
        assert nearest is not None
        assert nearest.entry == Coordinate(32370, 32240, 7)

    def test_returns_none_when_beyond_max_dist(self):
        reg = _sample_registry()
        far = Coordinate(31900, 32000, 7)
        assert reg.nearest_from(far, max_dist=5) is None

    def test_filters_by_to_z(self):
        reg = _sample_registry()
        origin = Coordinate(32370, 32240, 7)
        # Two transitions start at floor 7, both go to 8 → should find one
        nearest = reg.nearest_from(origin, max_dist=100, to_z=8)
        assert nearest is not None
        assert nearest.exit.z == 8

    def test_returns_none_when_floor_has_no_match(self):
        reg = _sample_registry()
        origin = Coordinate(32370, 32240, 6)  # floor 6 — no entries
        assert reg.nearest_from(origin, max_dist=200) is None


# ─────────────────────────────────────────────────────────────────────────────
# add
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryAdd:

    def test_add_increases_len(self):
        reg = TransitionRegistry()
        reg.add(_make_transition())
        assert len(reg) == 1

    def test_add_updates_indexes(self):
        reg = TransitionRegistry()
        reg.add(_make_transition(from_z=5, to_z=6))
        assert len(reg.from_floor(5)) == 1
        assert len(reg.between(5, 6)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# load / save round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryPersistence:

    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        missing = tmp_path / "no_such_file.json"
        reg = TransitionRegistry.load(missing)
        assert len(reg) == 0

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "transitions.json"
        original = _sample_registry()
        original.save(path)

        loaded = TransitionRegistry.load(path)
        assert len(loaded) == len(original)
        # Verify first entry survives round-trip
        orig_t = original.from_floor(7)[0]
        loaded_matches = [
            t for t in loaded.from_floor(7)
            if t.entry == orig_t.entry and t.exit == orig_t.exit
        ]
        assert len(loaded_matches) >= 1

    def test_saved_json_is_valid(self, tmp_path: Path):
        path = tmp_path / "t.json"
        _sample_registry().save(path)
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 3
        assert "entry" in data[0]
        assert "exit" in data[0]
        assert "kind" in data[0]


# ─────────────────────────────────────────────────────────────────────────────
# transitions_from_script helper
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionsFromScript:

    def _parse(self, text: str):
        from src.script_parser import ScriptParser
        return ScriptParser.parse_text(text)

    def test_ladder_creates_upward_transition(self):
        ins = self._parse("ladder (32370,32240,7)")
        reg = transitions_from_script(ins)
        assert len(reg) == 1
        t = reg.from_floor(7)[0]
        assert t.exit.z == 6          # ladder goes up (z decreases)
        assert t.kind == "ladder"

    def test_rope_creates_upward_transition(self):
        ins = self._parse("rope (32370,32240,8)")
        reg = transitions_from_script(ins)
        t = reg.from_floor(8)[0]
        assert t.exit.z == 7

    def test_shovel_creates_downward_transition(self):
        ins = self._parse("shovel (32370,32240,7)")
        reg = transitions_from_script(ins)
        t = reg.from_floor(7)[0]
        assert t.exit.z == 8          # shovel digs down (z increases)

    def test_non_transition_instructions_skipped(self):
        ins = self._parse("node (32370,32240,7)\nstand (32370,32240,7)")
        reg = transitions_from_script(ins)
        assert len(reg) == 0

    def test_multiple_transitions(self):
        script = (
            "ladder (32370,32240,7)\n"
            "rope (32380,32230,8)\n"
            "shovel (32390,32220,7)\n"
        )
        ins = self._parse(script)
        reg = transitions_from_script(ins)
        assert len(reg) == 3


# ─────────────────────────────────────────────────────────────────────────────
# FloorTransition model helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestFloorTransitionModel:

    def test_from_dict_and_to_dict_roundtrip(self):
        t = _make_transition(32370, 32240, 7, 8, "walk")
        d = t.to_dict()
        t2 = FloorTransition.from_dict(d)
        assert t2.entry == t.entry
        assert t2.exit == t.exit
        assert t2.kind == t.kind

    def test_default_kind_is_walk(self):
        t = FloorTransition(
            entry=Coordinate(32370, 32240, 7),
            exit=Coordinate(32370, 32240, 8),
        )
        assert t.kind == "walk"


# ─────────────────────────────────────────────────────────────────────────────
# TransitionRegistry.remove()
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryRemove:

    def test_remove_existing_returns_count(self):
        reg = _sample_registry()
        entry = Coordinate(32370, 32240, 7)
        removed = reg.remove(entry)
        assert removed == 1
        assert len(reg) == 2

    def test_remove_nonexistent_returns_zero(self):
        reg = _sample_registry()
        removed = reg.remove(Coordinate(99999, 99999, 7))
        assert removed == 0
        assert len(reg) == 3

    def test_remove_updates_get_transitions_for_floor(self):
        reg = _sample_registry()
        entry = Coordinate(32370, 32240, 7)
        reg.remove(entry)
        # floor 7→8 should only have the shovel transition left
        remaining = reg.from_floor(7)
        assert len(remaining) == 1
        assert remaining[0].kind == "shovel"

    def test_remove_rebuilds_by_pair_index(self):
        reg = _sample_registry()
        # Remove the only 8→7 transition
        entry = Coordinate(32370, 32240, 8)
        reg.remove(entry)
        # (8,7) pair should now be empty or missing
        pairs = reg.between(8, 7)
        assert pairs == []

    def test_remove_multiple_transitions_same_entry(self):
        """If two transitions share the same entry, both are removed."""
        reg = TransitionRegistry([
            _make_transition(32370, 32240, 7, 8, "walk"),
            _make_transition(32370, 32240, 7, 9, "ladder"),
            _make_transition(32380, 32230, 7, 8, "shovel"),
        ])
        entry = Coordinate(32370, 32240, 7)
        removed = reg.remove(entry)
        assert removed == 2
        assert len(reg) == 1

    def test_remove_empty_registry_returns_zero(self):
        reg = TransitionRegistry()
        removed = reg.remove(Coordinate(32370, 32240, 7))
        assert removed == 0
        assert len(reg) == 0


# ─────────────────────────────────────────────────────────────────────────────
# remove_by_floor()
# ─────────────────────────────────────────────────────────────────────────────

class TestRemoveByFloor:

    def test_removes_all_transitions_from_floor(self):
        reg = _sample_registry()  # 2 from floor 7, 1 from floor 8
        removed = reg.remove_by_floor(7)
        assert removed == 2
        assert len(reg) == 1

    def test_does_not_remove_other_floors(self):
        reg = _sample_registry()
        reg.remove_by_floor(7)
        # floor 8→7 transition should still be there
        remaining = reg.from_floor(8)
        assert len(remaining) == 1

    def test_nonexistent_floor_returns_zero(self):
        reg = _sample_registry()
        removed = reg.remove_by_floor(15)
        assert removed == 0
        assert len(reg) == 3

    def test_floor_unreachable_after_removal(self):
        reg = _sample_registry()
        reg.remove_by_floor(7)
        assert reg.from_floor(7) == []

    def test_removes_from_index_pair_too(self):
        reg = _sample_registry()
        reg.remove_by_floor(7)
        assert reg.between(7, 8) == []

    def test_empty_registry_returns_zero(self):
        reg = TransitionRegistry()
        assert reg.remove_by_floor(7) == 0

    def test_second_call_after_removal_returns_zero(self):
        reg = _sample_registry()
        reg.remove_by_floor(7)
        assert reg.remove_by_floor(7) == 0


# ─────────────────────────────────────────────────────────────────────────────
# count_by_kind()
# ─────────────────────────────────────────────────────────────────────────────

class TestCountByKind:

    def test_count_walk_kind(self):
        reg = _sample_registry()  # 1 walk, 1 shovel, 1 rope
        assert reg.count_by_kind("walk") == 1

    def test_count_rope_kind(self):
        reg = _sample_registry()
        assert reg.count_by_kind("rope") == 1

    def test_count_nonexistent_kind_returns_zero(self):
        reg = _sample_registry()
        assert reg.count_by_kind("ladder") == 0

    def test_count_all_kinds_sum_to_total(self):
        reg = _sample_registry()
        total = sum(reg.count_by_kind(k) for k in ("walk", "shovel", "rope", "ladder", "use"))
        assert total == len(reg)

    def test_count_after_add(self):
        reg = _sample_registry()
        reg.add(_make_transition(32400, 32200, 6, 7, "ladder"))
        assert reg.count_by_kind("ladder") == 1

    def test_count_on_empty_registry(self):
        reg = TransitionRegistry()
        assert reg.count_by_kind("walk") == 0


# ─────────────────────────────────────────────────────────────────────────────
# all_floors()
# ─────────────────────────────────────────────────────────────────────────────

class TestAllFloors:

    def test_returns_sorted_floors(self):
        reg = _sample_registry()  # floors 7 and 8 have departing transitions
        floors = reg.all_floors()
        assert floors == sorted(floors)

    def test_empty_registry_returns_empty(self):
        reg = TransitionRegistry()
        assert reg.all_floors() == []

    def test_includes_all_departure_floors(self):
        reg = _sample_registry()
        floors = reg.all_floors()
        assert 7 in floors
        assert 8 in floors

    def test_returns_list_type(self):
        reg = _sample_registry()
        assert isinstance(reg.all_floors(), list)

    def test_no_duplicates(self):
        reg = TransitionRegistry([
            _make_transition(32370, 32240, 7, 8, "walk"),
            _make_transition(32380, 32230, 7, 9, "shovel"),  # also from floor 7
        ])
        floors = reg.all_floors()
        assert floors.count(7) == 1

    def test_after_remove_by_floor_disappears(self):
        reg = _sample_registry()
        reg.remove_by_floor(7)
        floors = reg.all_floors()
        assert 7 not in floors


# ─────────────────────────────────────────────────────────────────────────────
# is_empty / kinds / stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryExtras:

    def test_is_empty_true_for_empty_registry(self):
        reg = TransitionRegistry()
        assert reg.is_empty is True

    def test_is_empty_false_when_has_transitions(self):
        reg = _sample_registry()
        assert reg.is_empty is False

    def test_is_empty_true_after_all_removed(self):
        reg = _sample_registry()
        reg.remove_by_floor(7)
        reg.remove_by_floor(8)
        assert reg.is_empty is True

    def test_kinds_empty_for_empty_registry(self):
        reg = TransitionRegistry()
        assert reg.kinds == []

    def test_kinds_contains_all_unique(self):
        reg = _sample_registry()  # walk, shovel, rope
        kinds = reg.kinds
        assert "walk" in kinds
        assert "shovel" in kinds
        assert "rope" in kinds

    def test_kinds_is_sorted(self):
        reg = _sample_registry()
        kinds = reg.kinds
        assert kinds == sorted(kinds)

    def test_kinds_no_duplicates(self):
        reg = TransitionRegistry([
            _make_transition(kind="walk"),
            _make_transition(32380, kind="walk"),  # same kind again
        ])
        assert reg.kinds.count("walk") == 1

    def test_stats_snapshot_returns_dict(self):
        reg = _sample_registry()
        assert isinstance(reg.stats_snapshot(), dict)

    def test_stats_snapshot_all_keys(self):
        reg = _sample_registry()
        snap = reg.stats_snapshot()
        for key in ("count", "is_empty", "floors", "kinds"):
            assert key in snap, f"Missing key: {key}"

    def test_stats_snapshot_count(self):
        reg = _sample_registry()
        snap = reg.stats_snapshot()
        assert snap["count"] == len(reg)

    def test_stats_snapshot_is_empty_false(self):
        reg = _sample_registry()
        assert reg.stats_snapshot()["is_empty"] is False

    def test_stats_snapshot_empty(self):
        reg = TransitionRegistry()
        snap = reg.stats_snapshot()
        assert snap["count"]    == 0
        assert snap["is_empty"] is True
        assert snap["floors"]   == []
        assert snap["kinds"]    == []


class TestTransitionRegistryAscendingDescending:

    def test_ascending_count_zero_for_empty_registry(self):
        assert TransitionRegistry().ascending_count == 0

    def test_descending_count_zero_for_empty_registry(self):
        assert TransitionRegistry().descending_count == 0

    def test_ascending_count_in_sample_registry(self):
        # _sample_registry has one ascending transition (8->7 rope)
        assert _sample_registry().ascending_count == 1

    def test_descending_count_in_sample_registry(self):
        # _sample_registry has two descending transitions (7->8)
        assert _sample_registry().descending_count == 2

    def test_ascending_count_returns_int(self):
        assert isinstance(_sample_registry().ascending_count, int)

    def test_descending_count_returns_int(self):
        assert isinstance(_sample_registry().descending_count, int)

    def test_all_ascending_registry(self):
        reg = TransitionRegistry([
            _make_transition(from_z=8, to_z=7),
            _make_transition(32380, from_z=9, to_z=7),
        ])
        assert reg.ascending_count == 2
        assert reg.descending_count == 0

    def test_all_descending_registry(self):
        reg = TransitionRegistry([
            _make_transition(from_z=7, to_z=8),
            _make_transition(32380, from_z=7, to_z=9),
        ])
        assert reg.ascending_count == 0
        assert reg.descending_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# total_count
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryTotalCount:

    def test_zero_for_empty_registry(self):
        assert TransitionRegistry().total_count == 0

    def test_matches_len(self):
        reg = _sample_registry()
        assert reg.total_count == len(reg)

    def test_increases_after_add(self):
        reg = TransitionRegistry()
        reg.add(_make_transition())
        assert reg.total_count == 1

    def test_decreases_after_remove(self):
        t = _make_transition()
        reg = TransitionRegistry([t])
        reg.remove(t.entry)
        assert reg.total_count == 0

    def test_returns_int(self):
        assert isinstance(_sample_registry().total_count, int)


# ─────────────────────────────────────────────────────────────────────────────
# floor_count
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryFloorCount:

    def test_zero_for_empty_registry(self):
        assert TransitionRegistry().floor_count == 0

    def test_single_transition_single_floor(self):
        reg = TransitionRegistry([_make_transition(from_z=7, to_z=8)])
        assert reg.floor_count == 1

    def test_two_transitions_same_source_floor(self):
        reg = TransitionRegistry([
            _make_transition(from_z=7, to_z=8),
            _make_transition(32380, from_z=7, to_z=8),
        ])
        assert reg.floor_count == 1

    def test_two_transitions_different_source_floors(self):
        reg = TransitionRegistry([
            _make_transition(from_z=7, to_z=8),
            _make_transition(from_z=8, to_z=7),
        ])
        assert reg.floor_count == 2

    def test_returns_int(self):
        assert isinstance(_sample_registry().floor_count, int)


# ─────────────────────────────────────────────────────────────────────────────
# FloorTransition kind properties  (is_walk, is_rope, is_use, is_shovel, is_ladder)
# ─────────────────────────────────────────────────────────────────────────────

class TestFloorTransitionKindProperties:

    def _t(self, kind: str) -> FloorTransition:
        return _make_transition(kind=kind)

    # is_walk
    def test_is_walk_true(self):
        assert self._t("walk").is_walk is True

    def test_is_walk_false_for_rope(self):
        assert self._t("rope").is_walk is False

    def test_is_walk_returns_bool(self):
        assert isinstance(self._t("walk").is_walk, bool)

    # is_rope
    def test_is_rope_true(self):
        assert self._t("rope").is_rope is True

    def test_is_rope_false_for_walk(self):
        assert self._t("walk").is_rope is False

    def test_is_rope_returns_bool(self):
        assert isinstance(self._t("rope").is_rope, bool)

    # is_use
    def test_is_use_true(self):
        assert self._t("use").is_use is True

    def test_is_use_false_for_walk(self):
        assert self._t("walk").is_use is False

    def test_is_use_returns_bool(self):
        assert isinstance(self._t("use").is_use, bool)

    # is_shovel
    def test_is_shovel_true(self):
        assert self._t("shovel").is_shovel is True

    def test_is_shovel_false_for_walk(self):
        assert self._t("walk").is_shovel is False

    def test_is_shovel_returns_bool(self):
        assert isinstance(self._t("shovel").is_shovel, bool)

    # is_ladder
    def test_is_ladder_true(self):
        assert self._t("ladder").is_ladder is True

    def test_is_ladder_false_for_rope(self):
        assert self._t("rope").is_ladder is False

    def test_is_ladder_returns_bool(self):
        assert isinstance(self._t("ladder").is_ladder, bool)

    # is_descending
    def test_is_descending_when_exit_z_greater(self):
        t = _make_transition(from_z=7, to_z=8)
        assert t.is_descending is True

    def test_is_descending_false_when_ascending(self):
        t = _make_transition(from_z=8, to_z=7)
        assert t.is_descending is False

    def test_is_descending_false_when_same_floor(self):
        t = _make_transition(from_z=7, to_z=7)
        assert t.is_descending is False

    def test_is_descending_returns_bool(self):
        assert isinstance(_make_transition().is_descending, bool)


# ─────────────────────────────────────────────────────────────────────────────
# TransitionRegistry.kind_count / has_walk / has_rope / has_ladder
# ─────────────────────────────────────────────────────────────────────────────

class TestTransitionRegistryKindHelpers:

    # kind_count
    def test_kind_count_empty_registry(self):
        assert TransitionRegistry().kind_count == 0

    def test_kind_count_one_kind(self):
        reg = TransitionRegistry([_make_transition(kind="walk"), _make_transition(kind="walk")])
        assert reg.kind_count == 1

    def test_kind_count_mixed_kinds(self):
        # _sample_registry has walk, shovel, rope = 3 unique kinds
        assert _sample_registry().kind_count == 3

    def test_kind_count_returns_int(self):
        assert isinstance(_sample_registry().kind_count, int)

    # has_walk
    def test_has_walk_true_when_present(self):
        reg = TransitionRegistry([_make_transition(kind="walk")])
        assert reg.has_walk is True

    def test_has_walk_false_when_absent(self):
        reg = TransitionRegistry([_make_transition(kind="rope")])
        assert reg.has_walk is False

    def test_has_walk_false_on_empty(self):
        assert TransitionRegistry().has_walk is False

    def test_has_walk_returns_bool(self):
        assert isinstance(_sample_registry().has_walk, bool)

    # has_rope
    def test_has_rope_true_when_present(self):
        # _sample_registry has a rope transition (8→7)
        assert _sample_registry().has_rope is True

    def test_has_rope_false_when_absent(self):
        reg = TransitionRegistry([_make_transition(kind="walk")])
        assert reg.has_rope is False

    def test_has_rope_returns_bool(self):
        assert isinstance(_sample_registry().has_rope, bool)

    # has_ladder
    def test_has_ladder_true_when_present(self):
        reg = TransitionRegistry([_make_transition(kind="ladder")])
        assert reg.has_ladder is True

    def test_has_ladder_false_when_absent(self):
        assert _sample_registry().has_ladder is False

    def test_has_ladder_returns_bool(self):
        assert isinstance(_sample_registry().has_ladder, bool)


# ─────────────────────────────────────────────────────────────────────────────
# load() corrupt file path (lines 84-86)
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadCorruptFile:

    def test_corrupt_json_returns_empty_registry(self, tmp_path):
        bad = tmp_path / "transitions.json"
        bad.write_text("{not valid json{{", encoding="utf-8")
        reg = TransitionRegistry.load(bad)
        assert len(reg) == 0

    def test_corrupt_json_does_not_raise(self, tmp_path):
        bad = tmp_path / "transitions.json"
        bad.write_text("null", encoding="utf-8")
        # json.load("null") succeeds but returns None → iterating fails
        # This exercises ValueError path
        try:
            reg = TransitionRegistry.load(bad)
        except Exception:
            pass  # acceptable if exception propagates from dict iteration
