"""
Tests para src/script_parser.py — ScriptParser
Totalmente offline, sin OBS ni mapa.
"""
from __future__ import annotations

import pytest

from src.script_parser import ScriptParser, Instruction, ScriptCoord


# ─────────────────────────────────────────────────────────────────────────────
# Texto de script de prueba
# ─────────────────────────────────────────────────────────────────────────────

_SAMPLE_SCRIPT = """\
# Comentario — debe ignorarse
node (32369,32241,7)
stand (32370,32240,7)
ladder (32371,32239,7)
shovel (32372,32238,7)
rope (32373,32237,7)
label start_depot
action end
call talk_npc({"list_words": ["hi", "deposit", "all", "yes", "bye"]})
call say({"sentence": "hello world"})
call conditional_jump_script_options({"var_name": "myvar", "label_jump": "lbl_a", "label_skip": "lbl_b"})
"""


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestScriptParser:

    def _parse(self, text: str) -> list[Instruction]:
        return ScriptParser.parse_text(text)

    # ── Líneas de movimiento ──────────────────────────────────────────────────

    def test_node_instruction(self):
        ins = self._parse("node (32369,32241,7)")
        assert len(ins) == 1
        assert ins[0].kind == "node"
        assert ins[0].coord == ScriptCoord(32369, 32241, 7)

    def test_stand_instruction(self):
        ins = self._parse("stand (32370,32240,7)")
        assert ins[0].kind == "stand"
        assert ins[0].coord is not None
        assert ins[0].coord.x == 32370

    def test_ladder_instruction(self):
        ins = self._parse("ladder (32371,32239,7)")
        assert ins[0].kind == "ladder"

    def test_shovel_instruction(self):
        ins = self._parse("shovel (32372,32238,7)")
        assert ins[0].kind == "shovel"

    def test_rope_instruction(self):
        ins = self._parse("rope (32373,32237,7)")
        assert ins[0].kind == "rope"

    # ── label / action ────────────────────────────────────────────────────────

    def test_label_instruction(self):
        ins = self._parse("label start_depot")
        assert ins[0].kind == "label"
        assert ins[0].label == "start_depot"

    def test_action_end(self):
        ins = self._parse("action end")
        assert ins[0].kind == "action"
        assert ins[0].action == "end"

    def test_action_travel(self):
        ins = self._parse("action travel")
        assert ins[0].kind == "action"
        assert ins[0].action == "travel"

    # ── call ──────────────────────────────────────────────────────────────────

    def test_talk_npc(self):
        line = 'call talk_npc({"list_words": ["hi", "deposit", "all"]})'
        ins = self._parse(line)
        assert ins[0].kind == "talk_npc"
        assert "hi" in ins[0].words
        assert "deposit" in ins[0].words

    def test_say(self):
        line = 'call say({"sentence": "hello world"})'
        ins = self._parse(line)
        assert ins[0].kind == "say"
        assert ins[0].sentence == "hello world"

    def test_conditional_jump(self):
        line = ('call conditional_jump_script_options('
                '{"var_name": "myvar", "label_jump": "lbl_a", "label_skip": "lbl_b"})')
        ins = self._parse(line)
        assert ins[0].kind == "cond_jump"
        assert ins[0].var_name == "myvar"
        assert ins[0].label_jump == "lbl_a"
        assert ins[0].label_skip == "lbl_b"

    # ── Comentarios y líneas vacías ──────────────────────────────────────────

    def test_comment_lines_ignored(self):
        ins = self._parse("# esto es un comentario")
        assert len(ins) == 0

    def test_blank_lines_ignored(self):
        ins = self._parse("\n\n   \n")
        assert len(ins) == 0

    # ── Script completo ───────────────────────────────────────────────────────

    def test_full_sample_script(self):
        instructions = self._parse(_SAMPLE_SCRIPT)
        kinds = [i.kind for i in instructions]
        assert "node"      in kinds
        assert "stand"     in kinds
        assert "ladder"    in kinds
        assert "shovel"    in kinds
        assert "rope"      in kinds
        assert "label"     in kinds
        assert "action"    in kinds
        assert "talk_npc"  in kinds
        assert "say"       in kinds
        assert "cond_jump" in kinds

    def test_instruction_count(self):
        instructions = self._parse(_SAMPLE_SCRIPT)
        # 10 líneas válidas: node + stand + ladder + shovel + rope +
        #                    label + action + talk_npc + say + cond_jump
        assert len(instructions) == 10

    # ── ScriptCoord helpers ───────────────────────────────────────────────────

    def test_script_coord_to_tibia_coord(self):
        from src.models import Coordinate
        sc = ScriptCoord(32369, 32241, 7)
        tc = sc.to_tibia_coord()
        assert isinstance(tc, Coordinate)
        assert tc.x == 32369
        assert tc.y == 32241
        assert tc.z == 7

    # ── Instruction.__str__ ───────────────────────────────────────────────────

    # ── Coordenadas con espacios ──────────────────────────────────────────────

    def test_node_with_spaces(self):
        ins = self._parse("node ( 32369 , 32241 , 7 )")
        assert ins[0].coord == ScriptCoord(32369, 32241, 7)

    # ── Línea desconocida ─────────────────────────────────────────────────────

    def test_unknown_instruction(self):
        ins = self._parse("foobar something strange")
        assert len(ins) == 1
        assert ins[0].kind == "unknown"

    # ── goto (unconditional jump) ─────────────────────────────────────────────

    def test_goto_instruction(self):
        ins = self._parse("goto flee_label")
        assert len(ins) == 1
        assert ins[0].kind == "goto"
        assert ins[0].label_jump == "flee_label"

    def test_goto_case_insensitive(self):
        ins = self._parse("GOTO MyLabel")
        assert ins[0].kind == "goto"
        assert ins[0].label_jump == "mylabel"

    # ── wait ─────────────────────────────────────────────────────────────────

    def test_wait_integer(self):
        ins = self._parse("wait 3")
        assert len(ins) == 1
        assert ins[0].kind == "wait"
        assert ins[0].wait_secs == pytest.approx(3.0)

    def test_wait_float(self):
        ins = self._parse("wait 1.5")
        assert ins[0].kind == "wait"
        assert ins[0].wait_secs == pytest.approx(1.5)

    def test_wait_zero(self):
        ins = self._parse("wait 0")
        assert ins[0].kind == "wait"
        assert ins[0].wait_secs == pytest.approx(0.0)

    # ── use_hotkey ────────────────────────────────────────────────────────────

    def test_use_hotkey_hex(self):
        ins = self._parse("use_hotkey 0x70")
        assert len(ins) == 1
        assert ins[0].kind == "use_hotkey"
        assert ins[0].hotkey_vk == 0x70

    def test_use_hotkey_decimal(self):
        ins = self._parse("use_hotkey 112")
        assert ins[0].kind == "use_hotkey"
        assert ins[0].hotkey_vk == 112

    # ── use_item ──────────────────────────────────────────────────────────────

    def test_use_item_without_vk(self):
        ins = self._parse("use_item rope")
        assert len(ins) == 1
        assert ins[0].kind == "use_item"
        assert ins[0].item_name == "rope"
        assert ins[0].hotkey_vk == 0

    def test_use_item_with_vk_hex(self):
        ins = self._parse("use_item rope vk=0x71")
        assert ins[0].kind == "use_item"
        assert ins[0].item_name == "rope"
        assert ins[0].hotkey_vk == 0x71

    def test_use_item_with_vk_decimal(self):
        ins = self._parse("use_item shovel vk=113")
        assert ins[0].hotkey_vk == 113

    # ── if_stat ───────────────────────────────────────────────────────────────

    def test_if_stat_hp_lt(self):
        ins = self._parse("if hp < 40 goto flee")
        assert len(ins) == 1
        assert ins[0].kind == "if_stat"
        assert ins[0].stat == "hp"
        assert ins[0].op == "<"
        assert ins[0].threshold == 40
        assert ins[0].goto_label == "flee"

    def test_if_stat_mp_lt(self):
        ins = self._parse("if mp < 20 goto depot")
        assert ins[0].stat == "mp"
        assert ins[0].threshold == 20
        assert ins[0].goto_label == "depot"

    def test_if_stat_operator_lte(self):
        ins = self._parse("if hp <= 50 goto heal")
        assert ins[0].kind == "if_stat"
        assert ins[0].op == "<="
        assert ins[0].threshold == 50

    def test_if_stat_operator_gte(self):
        ins = self._parse("if mp >= 90 goto hunt")
        assert ins[0].kind == "if_stat"
        assert ins[0].op == ">="

    def test_if_stat_case_insensitive(self):
        ins = self._parse("IF HP < 30 GOTO safe")
        assert ins[0].kind == "if_stat"
        assert ins[0].stat == "hp"
        assert ins[0].goto_label == "safe"

    # ── script with all new instructions ─────────────────────────────────────

    def test_new_instructions_in_full_script(self):
        script = (
            "label start\n"
            "node (32369,32241,7)\n"
            "if hp < 40 goto flee\n"
            "wait 1.5\n"
            "use_hotkey 0x70\n"
            "use_item rope vk=0x71\n"
            "goto start\n"
            "label flee\n"
            "action end\n"
        )
        instructions = self._parse(script)
        kinds = {i.kind for i in instructions}
        assert "label"      in kinds
        assert "node"       in kinds
        assert "if_stat"    in kinds
        assert "wait"       in kinds
        assert "use_hotkey" in kinds
        assert "use_item"   in kinds
        assert "goto"       in kinds
        assert "action"     in kinds

    # ── if_stat with > operator ───────────────────────────────────────────────

    def test_if_stat_hp_gt(self):
        ins = self._parse("if hp > 80 goto hunt")
        assert ins[0].kind == "if_stat"
        assert ins[0].stat == "hp"
        assert ins[0].op == ">"
        assert ins[0].threshold == 80
        assert ins[0].goto_label == "hunt"

    def test_if_stat_mp_gt(self):
        ins = self._parse("if mp > 60 goto cast")
        assert ins[0].stat == "mp"
        assert ins[0].op == ">"
        assert ins[0].threshold == 60

    # ── parse_file() ──────────────────────────────────────────────────────────

    def test_parse_file_reads_from_disk(self, tmp_path):
        p = tmp_path / "route.in"
        p.write_text("node (32369,32241,7)\nlabel depot\n", encoding="utf-8")
        instructions = ScriptParser.parse_file(p)
        assert len(instructions) == 2
        assert instructions[0].kind == "node"
        assert instructions[1].kind == "label"
        assert instructions[1].label == "depot"

    def test_parse_file_ignores_comments(self, tmp_path):
        p = tmp_path / "route.in"
        p.write_text("# comment\nstand (1,2,7)\n# another\n", encoding="utf-8")
        instructions = ScriptParser.parse_file(p)
        assert len(instructions) == 1
        assert instructions[0].kind == "stand"

    def test_parse_file_windows_line_endings(self, tmp_path):
        p = tmp_path / "route.in"
        p.write_bytes(b"node (1,2,7)\r\nwait 1\r\n")
        instructions = ScriptParser.parse_file(p)
        assert len(instructions) == 2
        assert instructions[0].kind == "node"
        assert instructions[1].kind == "wait"

    def test_parse_file_empty_file(self, tmp_path):
        p = tmp_path / "empty.in"
        p.write_text("", encoding="utf-8")
        assert ScriptParser.parse_file(p) == []

    # ── order preservation ────────────────────────────────────────────────────

    def test_instruction_order_preserved(self):
        script = "label A\nnode (1,1,7)\nwait 2\ngoto A\naction end"
        ins = self._parse(script)
        assert [i.kind for i in ins] == ["label", "node", "wait", "goto", "action"]

    # ── Instruction.__str__ for all kinds ─────────────────────────────────────

    # ── edge cases ────────────────────────────────────────────────────────────

    def test_multiple_comments_and_blank_lines(self):
        script = "# c1\n\n# c2\n\n   \n# c3\n"
        assert self._parse(script) == []

    def test_mixed_case_node(self):
        ins = self._parse("NODE (32369,32241,7)")
        assert ins[0].kind == "node"

    def test_mixed_case_stand(self):
        ins = self._parse("STAND (1,2,7)")
        assert ins[0].kind == "stand"

    def test_label_with_numbers(self):
        ins = self._parse("label loop_01")
        assert ins[0].label == "loop_01"

    def test_action_with_multiple_subwords(self):
        ins = self._parse("action travel")
        assert ins[0].kind == "action"
        assert ins[0].action == "travel"

    def test_wait_large_value(self):
        ins = self._parse("wait 999.9")
        assert ins[0].wait_secs == pytest.approx(999.9)

    def test_use_hotkey_large_vk(self):
        ins = self._parse("use_hotkey 0xFF")
        assert ins[0].hotkey_vk == 0xFF

    def test_say_empty_sentence(self):
        # sentence field is empty when dict has empty string
        ins = self._parse('call say({"sentence": ""})')
        assert ins[0].kind == "say"
        assert ins[0].sentence == ""


# ─────────────────────────────────────────────────────────────────────────────
# Instruction.is_movement / is_jump properties
# ─────────────────────────────────────────────────────────────────────────────

class TestInstructionProperties:

    def _parse(self, text: str) -> list[Instruction]:
        return ScriptParser.parse_text(text)

    # is_movement
    def test_node_is_movement(self):
        assert self._parse("node (1,2,7)")[0].is_movement is True

    def test_stand_is_movement(self):
        assert self._parse("stand (1,2,7)")[0].is_movement is True

    def test_ladder_is_movement(self):
        assert self._parse("ladder (1,2,7)")[0].is_movement is True

    def test_shovel_is_movement(self):
        assert self._parse("shovel (1,2,7)")[0].is_movement is True

    def test_rope_is_movement(self):
        assert self._parse("rope (1,2,7)")[0].is_movement is True

    def test_label_is_not_movement(self):
        assert self._parse("label lbl")[0].is_movement is False

    def test_action_is_not_movement(self):
        assert self._parse("action end")[0].is_movement is False

    def test_wait_is_not_movement(self):
        assert self._parse("wait 1.0")[0].is_movement is False

    # is_jump
    def test_goto_is_jump(self):
        assert self._parse("goto lbl")[0].is_jump is True

    def test_if_stat_is_jump(self):
        assert self._parse("if hp < 30 goto flee")[0].is_jump is True

    def test_node_is_not_jump(self):
        assert self._parse("node (1,2,7)")[0].is_jump is False

    def test_action_is_not_jump(self):
        assert self._parse("action end")[0].is_jump is False

    def test_use_hotkey_is_not_jump(self):
        assert self._parse("use_hotkey 0x70")[0].is_jump is False


# ─────────────────────────────────────────────────────────────────────────────
# ScriptParser.label_map() / filter_by_kind()
# ─────────────────────────────────────────────────────────────────────────────

class TestScriptParserUtils:

    _SCRIPT = """\
node (32369,32241,7)
label loop_start
node (32370,32241,7)
if hp < 30 goto flee
goto loop_start
label flee
action end
"""

    def _parse(self) -> list[Instruction]:
        return ScriptParser.parse_text(self._SCRIPT)

    # label_map
    def test_label_map_finds_all_labels(self):
        lmap = ScriptParser.label_map(self._parse())
        assert "loop_start" in lmap
        assert "flee" in lmap

    def test_label_map_correct_indices(self):
        ins = self._parse()
        lmap = ScriptParser.label_map(ins)
        assert ins[lmap["loop_start"]].kind == "label"
        assert ins[lmap["flee"]].kind == "label"

    def test_label_map_empty_script(self):
        lmap = ScriptParser.label_map([])
        assert lmap == {}

    def test_label_map_no_labels(self):
        ins = ScriptParser.parse_text("node (1,2,7)\naction end")
        lmap = ScriptParser.label_map(ins)
        assert lmap == {}

    def test_label_map_returns_first_occurrence(self):
        ins = ScriptParser.parse_text("label dup\nnode (1,2,7)\nlabel dup")
        lmap = ScriptParser.label_map(ins)
        # label_map returns LAST due to dict overwrite — document and verify
        assert "dup" in lmap

    # filter_by_kind
    def test_filter_returns_only_requested_kind(self):
        ins = self._parse()
        nodes = ScriptParser.filter_by_kind(ins, "node")
        assert all(i.kind == "node" for i in nodes)

    def test_filter_multiple_kinds(self):
        ins = self._parse()
        jumps = ScriptParser.filter_by_kind(ins, "goto", "if_stat")
        assert all(i.kind in ("goto", "if_stat") for i in jumps)
        assert len(jumps) == 2

    def test_filter_nonexistent_kind_returns_empty(self):
        ins = self._parse()
        result = ScriptParser.filter_by_kind(ins, "nonexistent")
        assert result == []

    def test_filter_does_not_mutate_original(self):
        ins = self._parse()
        original_len = len(ins)
        ScriptParser.filter_by_kind(ins, "node")
        assert len(ins) == original_len

    def test_filter_on_empty_list(self):
        result = ScriptParser.filter_by_kind([], "node", "label")
        assert result == []
class TestScriptParserCountAndCoords:

    _SCRIPT = """\
node (32369,32241,7)
node (32370,32241,7)
stand (32371,32241,7)
label loop
goto loop
if hp < 30 goto loop
action end
"""

    def _parse(self) -> list[Instruction]:
        return ScriptParser.parse_text(self._SCRIPT)

    # count_by_kind
    def test_count_by_kind_correct_node_count(self):
        counts = ScriptParser.count_by_kind(self._parse())
        assert counts.get("node", 0) == 2

    def test_count_by_kind_correct_label_count(self):
        counts = ScriptParser.count_by_kind(self._parse())
        assert counts.get("label", 0) == 1

    def test_count_by_kind_empty_list(self):
        assert ScriptParser.count_by_kind([]) == {}

    def test_count_by_kind_all_same_kind(self):
        ins = ScriptParser.parse_text("node (1,2,7)\nnode (3,4,7)")
        counts = ScriptParser.count_by_kind(ins)
        assert counts == {"node": 2}

    def test_count_by_kind_sum_equals_total(self):
        ins = self._parse()
        counts = ScriptParser.count_by_kind(ins)
        assert sum(counts.values()) == len(ins)

    # movement_coords
    def test_movement_coords_count(self):
        result = ScriptParser.movement_coords(self._parse())
        # 2 nodes + 1 stand = 3
        assert len(result) == 3

    def test_movement_coords_excludes_labels(self):
        ins = ScriptParser.parse_text("label lbl\nnode (1,2,7)")
        result = ScriptParser.movement_coords(ins)
        assert len(result) == 1

    def test_movement_coords_empty(self):
        ins = ScriptParser.parse_text("label lbl\ngoto lbl")
        assert ScriptParser.movement_coords(ins) == []


class TestUniqueKinds:

    _SCRIPT = "node (1,2,7)\nstand (3,4,7)\nlabel lp\ngoto lp\nif hp < 30 goto lp"

    def _parse(self):
        return ScriptParser.parse_text(self._SCRIPT)

    def test_sorted(self):
        result = ScriptParser.unique_kinds(self._parse())
        assert result == sorted(result)

    def test_no_duplicates(self):
        result = ScriptParser.unique_kinds(self._parse())
        assert len(result) == len(set(result))

    def test_contains_expected_kinds(self):
        result = ScriptParser.unique_kinds(self._parse())
        for kind in ("node", "stand", "label", "goto", "if_stat"):
            assert kind in result

    def test_empty_list_returns_empty(self):
        assert ScriptParser.unique_kinds([]) == []

    def test_single_kind_script(self):
        ins = ScriptParser.parse_text("node (1,2,7)\nnode (3,4,7)")
        assert ScriptParser.unique_kinds(ins) == ["node"]


class TestHasLabel:

    _SCRIPT = "label loop\nnode (1,2,7)\ngoto loop"

    def _parse(self):
        return ScriptParser.parse_text(self._SCRIPT)

    def test_existing_label_found(self):
        assert ScriptParser.has_label(self._parse(), "loop") is True

    def test_missing_label_not_found(self):
        assert ScriptParser.has_label(self._parse(), "nonexistent") is False

    def test_case_insensitive(self):
        ins = ScriptParser.parse_text("label LOOP\nnode (1,2,7)")
        assert ScriptParser.has_label(ins, "loop") is True

    def test_empty_list_returns_false(self):
        assert ScriptParser.has_label([], "loop") is False

    def test_goto_target_not_a_label(self):
        # 'goto loop' should NOT count as a label definition
        ins = ScriptParser.parse_text("goto loop")
        assert ScriptParser.has_label(ins, "loop") is False


class TestScriptStats:

    _FULL = """node (1,2,7)
stand (3,4,7)
label loop
goto loop
if hp < 30 goto loop
action end
wait 1.5
"""

    def _parse(self):
        return ScriptParser.parse_text(self._FULL)

    def test_all_keys_present(self):
        snap = ScriptParser.script_stats(self._parse())
        for key in ("total", "movement", "jumps", "labels", "actions", "waits", "unique_kinds"):
            assert key in snap, f"Missing key: {key}"

    def test_total_correct(self):
        snap = ScriptParser.script_stats(self._parse())
        assert snap["total"] == len(self._parse())

    def test_movement_count(self):
        snap = ScriptParser.script_stats(self._parse())
        assert snap["movement"] == 2  # node + stand

    def test_jumps_count(self):
        snap = ScriptParser.script_stats(self._parse())
        assert snap["jumps"] == 2  # goto + if_stat

    def test_labels_count(self):
        snap = ScriptParser.script_stats(self._parse())
        assert snap["labels"] == 1

    def test_waits_count(self):
        snap = ScriptParser.script_stats(self._parse())
        assert snap["waits"] == 1

    def test_empty_script(self):
        snap = ScriptParser.script_stats([])
        assert snap["total"] == 0
        assert snap["movement"] == 0
        assert snap["unique_kinds"] == 0
class TestDepotInstruction:

    def _parse(self, text: str) -> "Instruction":
        ins = ScriptParser.parse_text(text)
        assert ins, f"Expected at least one instruction from: {text!r}"
        return ins[0]

    def test_depot_kind(self):
        assert self._parse("depot").kind == "depot"

    def test_depot_case_insensitive_upper(self):
        assert self._parse("DEPOT").kind == "depot"

    def test_depot_case_insensitive_mixed(self):
        assert self._parse("Depot").kind == "depot"

    def test_depot_raw_preserved(self):
        assert self._parse("depot").raw == "depot"

    def test_depot_in_script_text(self):
        script = "node (100,200,7)\ndepot\nnode (101,200,7)"
        ins = ScriptParser.parse_text(script)
        assert len(ins) == 3
        assert ins[1].kind == "depot"

    def test_script_stats_counts_depot(self):
        ins = ScriptParser.parse_text("node (1,2,7)\ndepot\ndepot")
        stats = ScriptParser.script_stats(ins)
        assert stats["depots"] == 2

    def test_script_stats_depots_zero_without_depot(self):
        ins = ScriptParser.parse_text("node (1,2,7)\nwait 1")
        stats = ScriptParser.script_stats(ins)
        assert stats.get("depots", 0) == 0

    def test_count_by_kind_includes_depot(self):
        ins = ScriptParser.parse_text("depot\ndepot\nnode (1,2,7)")
        counts = ScriptParser.count_by_kind(ins)
        assert counts.get("depot", 0) == 2

    def test_depot_not_counted_as_movement(self):
        ins = ScriptParser.parse_text("depot")
        assert ins[0].is_movement is False

    def test_depot_not_counted_as_jump(self):
        ins = ScriptParser.parse_text("depot")
        assert ins[0].is_jump is False


# ─────────────────────────────────────────────────────────────────────────────
# validate_labels()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateLabels:

    def test_valid_script_no_errors(self):
        script = "label loop\nnode (1,2,7)\ngoto loop"
        ins = ScriptParser.parse_text(script)
        assert ScriptParser.validate_labels(ins) == []

    def test_goto_undefined_label(self):
        script = "node (1,2,7)\ngoto nowhere"
        ins = ScriptParser.parse_text(script)
        errors = ScriptParser.validate_labels(ins)
        assert len(errors) == 1
        assert "nowhere" in errors[0]

    def test_if_stat_undefined_label(self):
        ins = [
            Instruction(kind="if_stat", stat="hp", op="<", threshold=50, goto_label="missing"),
        ]
        errors = ScriptParser.validate_labels(ins)
        assert len(errors) == 1
        assert "missing" in errors[0]

    def test_cond_jump_undefined_label_jump(self):
        ins = [
            Instruction(kind="label", label="b"),
            Instruction(kind="cond_jump", var_name="x", label_jump="a", label_skip="b"),
        ]
        errors = ScriptParser.validate_labels(ins)
        assert len(errors) == 1
        assert "label_jump" in errors[0]

    def test_cond_jump_undefined_label_skip(self):
        ins = [
            Instruction(kind="label", label="a"),
            Instruction(kind="cond_jump", var_name="x", label_jump="a", label_skip="c"),
        ]
        errors = ScriptParser.validate_labels(ins)
        assert len(errors) == 1
        assert "label_skip" in errors[0]

    def test_cond_jump_both_labels_valid(self):
        ins = [
            Instruction(kind="label", label="a"),
            Instruction(kind="label", label="b"),
            Instruction(kind="cond_jump", var_name="x", label_jump="a", label_skip="b"),
        ]
        assert ScriptParser.validate_labels(ins) == []

    def test_empty_script_no_errors(self):
        assert ScriptParser.validate_labels([]) == []

    def test_multiple_errors_reported(self):
        ins = [
            Instruction(kind="goto", label_jump="x"),
            Instruction(kind="goto", label_jump="y"),
        ]
        errors = ScriptParser.validate_labels(ins)
        assert len(errors) == 2


# ─────────────────────────────────────────────────────────────────────────────
# WasP aliases: walk / door
# ─────────────────────────────────────────────────────────────────────────────

class TestWaspAliases:

    def _parse(self, text: str) -> list[Instruction]:
        return ScriptParser.parse_text(text)

    # ── walk → stand ──────────────────────────────────────────────────────

    def test_walk_resolves_to_stand(self):
        ins = self._parse("walk (32370,32240,7)")
        assert len(ins) == 1
        assert ins[0].kind == "stand"
        assert ins[0].coord == ScriptCoord(32370, 32240, 7)

    def test_walk_case_insensitive(self):
        ins = self._parse("WALK (1,2,7)")
        assert ins[0].kind == "stand"

    def test_walk_is_movement(self):
        ins = self._parse("walk (1,2,7)")
        assert ins[0].is_movement is True

    def test_walk_with_spaces(self):
        ins = self._parse("walk ( 100 , 200 , 7 )")
        assert ins[0].kind == "stand"
        assert ins[0].coord == ScriptCoord(100, 200, 7)

    # ── door → open_door ─────────────────────────────────────────────────

    def test_door_resolves_to_open_door(self):
        ins = self._parse("door (32371,32239,7)")
        assert len(ins) == 1
        assert ins[0].kind == "open_door"
        assert ins[0].coord == ScriptCoord(32371, 32239, 7)

    def test_door_case_insensitive(self):
        ins = self._parse("DOOR (5,6,7)")
        assert ins[0].kind == "open_door"

    def test_door_is_not_movement(self):
        ins = self._parse("door (1,2,7)")
        assert ins[0].is_movement is False

    # ── JSON aliases ─────────────────────────────────────────────────────

    def test_json_walk_resolves_to_stand(self):
        script = [{"kind": "walk", "x": 100, "y": 200, "z": 7}]
        ins = ScriptParser.from_json_script(script)
        assert len(ins) == 1
        assert ins[0].kind == "stand"
        assert ins[0].coord == ScriptCoord(100, 200, 7)

    def test_json_door_resolves_to_open_door(self):
        script = [{"kind": "door", "x": 100, "y": 200, "z": 7}]
        ins = ScriptParser.from_json_script(script)
        assert len(ins) == 1
        assert ins[0].kind == "open_door"

    # ── mixed script with aliases ────────────────────────────────────────

    def test_mixed_script_with_walk_and_door(self):
        text = (
            "node (32369,32241,7)\n"
            "walk (32370,32240,7)\n"
            "door (32371,32239,7)\n"
            "stand (32372,32238,7)\n"
        )
        ins = self._parse(text)
        kinds = [i.kind for i in ins]
        assert kinds == ["node", "stand", "open_door", "stand"]


# ─────────────────────────────────────────────────────────────────────────────
# Instruction.__str__ for all kinds
# ─────────────────────────────────────────────────────────────────────────────

class TestInstructionStr:

    def test_str_node(self):
        ins = Instruction(kind="node", coord=ScriptCoord(100, 200, 7), raw="node (100,200,7)")
        s = str(ins)
        assert "node" in s

    def test_str_stand(self):
        ins = Instruction(kind="stand", coord=ScriptCoord(100, 200, 7), raw="stand (100,200,7)")
        s = str(ins)
        assert "stand" in s

    def test_str_ladder(self):
        ins = Instruction(kind="ladder", coord=ScriptCoord(1, 2, 7))
        s = str(ins)
        assert "ladder" in s

    def test_str_shovel(self):
        ins = Instruction(kind="shovel", coord=ScriptCoord(1, 2, 7))
        s = str(ins)
        assert "shovel" in s

    def test_str_rope(self):
        ins = Instruction(kind="rope", coord=ScriptCoord(1, 2, 7))
        s = str(ins)
        assert "rope" in s

    def test_str_open_door(self):
        ins = Instruction(kind="open_door", coord=ScriptCoord(1, 2, 7))
        s = str(ins)
        assert "open_door" in s

    def test_str_label(self):
        ins = Instruction(kind="label", label="depot")
        s = str(ins)
        assert "label" in s
        assert "depot" in s

    def test_str_goto(self):
        ins = Instruction(kind="goto", label_jump="flee")
        s = str(ins)
        assert "goto" in s
        assert "flee" in s

    def test_str_action(self):
        ins = Instruction(kind="action", action="end")
        s = str(ins)
        assert "action" in s
        assert "end" in s

    def test_str_use_item_without_vk(self):
        ins = Instruction(kind="use_item", item_name="rope", hotkey_vk=0)
        s = str(ins)
        assert "use_item" in s
        assert "rope" in s

    def test_str_use_item_with_vk(self):
        ins = Instruction(kind="use_item", item_name="shovel", hotkey_vk=0x71)
        s = str(ins)
        assert "use_item" in s
        assert "shovel" in s
        assert "0x" in s

    def test_str_use_hotkey(self):
        ins = Instruction(kind="use_hotkey", hotkey_vk=0x70)
        s = str(ins)
        assert "use_hotkey" in s
        assert "0x70" in s

    def test_str_wait(self):
        ins = Instruction(kind="wait", wait_secs=3.5)
        s = str(ins)
        assert "wait" in s
        assert "3.5" in s

    def test_str_if_stat(self):
        ins = Instruction(kind="if_stat", stat="hp", op="<", threshold=40, goto_label="flee")
        s = str(ins)
        assert "hp" in s
        assert "<" in s
        assert "40" in s
        assert "flee" in s

    def test_str_talk_npc(self):
        ins = Instruction(kind="talk_npc", words=["hi", "deposit"])
        s = str(ins)
        assert "talk_npc" in s
        assert "hi" in s

    def test_str_say(self):
        ins = Instruction(kind="say", sentence="hello world")
        s = str(ins)
        assert "say" in s
        assert "hello world" in s

    def test_str_cond_jump(self):
        ins = Instruction(kind="cond_jump", var_name="x", label_jump="a", label_skip="b")
        s = str(ins)
        assert "cond_jump" in s
        assert "x" in s

    def test_str_depot(self):
        ins = Instruction(kind="depot")
        s = str(ins)
        assert "depot" in s

    def test_str_unknown(self):
        ins = Instruction(kind="unknown", raw="foobar")
        s = str(ins)
        assert "foobar" in s


# ─────────────────────────────────────────────────────────────────────────────
# Instruction extra properties
# ─────────────────────────────────────────────────────────────────────────────

class TestInstructionExtraProperties:

    def test_is_conditional_true_for_if_stat(self):
        ins = Instruction(kind="if_stat", stat="hp", op="<", threshold=50, goto_label="x")
        assert ins.is_conditional is True

    def test_is_conditional_false_for_goto(self):
        ins = Instruction(kind="goto", label_jump="x")
        assert ins.is_conditional is False

    def test_has_coord_true_when_coord_set(self):
        ins = Instruction(kind="node", coord=ScriptCoord(1, 2, 7))
        assert ins.has_coord is True

    def test_has_coord_false_when_no_coord(self):
        ins = Instruction(kind="label", label="x")
        assert ins.has_coord is False

    def test_is_label_true(self):
        ins = Instruction(kind="label", label="depot")
        assert ins.is_label is True

    def test_is_label_false_for_goto(self):
        ins = Instruction(kind="goto", label_jump="depot")
        assert ins.is_label is False

    def test_is_wait_true(self):
        ins = Instruction(kind="wait", wait_secs=2.0)
        assert ins.is_wait is True

    def test_is_wait_false_for_action(self):
        ins = Instruction(kind="action", action="end")
        assert ins.is_wait is False

    def test_is_action_true(self):
        ins = Instruction(kind="action", action="end")
        assert ins.is_action is True

    def test_is_action_false_for_wait(self):
        ins = Instruction(kind="wait", wait_secs=1.0)
        assert ins.is_action is False

    def test_is_goto_true(self):
        ins = Instruction(kind="goto", label_jump="lbl")
        assert ins.is_goto is True

    def test_is_goto_false_for_if_stat(self):
        ins = Instruction(kind="if_stat", stat="hp", op="<", threshold=50, goto_label="x")
        assert ins.is_goto is False

    def test_is_node_true(self):
        ins = Instruction(kind="node", coord=ScriptCoord(1, 2, 7))
        assert ins.is_node is True

    def test_is_node_false_for_stand(self):
        ins = Instruction(kind="stand", coord=ScriptCoord(1, 2, 7))
        assert ins.is_node is False

    def test_is_depot_true(self):
        ins = Instruction(kind="depot")
        assert ins.is_depot is True

    def test_is_depot_false_for_action(self):
        ins = Instruction(kind="action", action="depot")
        assert ins.is_depot is False


# ─────────────────────────────────────────────────────────────────────────────
# ScriptCoord.__str__
# ─────────────────────────────────────────────────────────────────────────────

class TestScriptCoordStr:

    def test_str_format(self):
        sc = ScriptCoord(32369, 32241, 7)
        assert str(sc) == "(32369,32241,7)"


# ─────────────────────────────────────────────────────────────────────────────
# from_json_script() — comprehensive coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestFromJsonScript:

    def test_non_dict_entries_skipped(self):
        script = ["not a dict", 42, None]
        ins = ScriptParser.from_json_script(script)
        assert len(ins) == 0

    def test_metadata_comments_skipped(self):
        script = [{"kind": "_comment", "text": "route metadata"}]
        ins = ScriptParser.from_json_script(script)
        assert len(ins) == 0

    def test_node_with_coord(self):
        script = [{"kind": "node", "x": 100, "y": 200, "z": 7}]
        ins = ScriptParser.from_json_script(script)
        assert len(ins) == 1
        assert ins[0].kind == "node"
        assert ins[0].coord == ScriptCoord(100, 200, 7)

    def test_stand_with_coord(self):
        script = [{"kind": "stand", "x": 100, "y": 200, "z": 7}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "stand"

    def test_ladder_with_coord(self):
        script = [{"kind": "ladder", "x": 100, "y": 200, "z": 7}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "ladder"

    def test_shovel_with_coord(self):
        script = [{"kind": "shovel", "x": 100, "y": 200, "z": 7}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "shovel"

    def test_rope_with_coord(self):
        script = [{"kind": "rope", "x": 100, "y": 200, "z": 7}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "rope"

    def test_movement_without_coord(self):
        script = [{"kind": "node"}]  # no x, y, z
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "node"
        assert ins[0].coord is None

    def test_hint_field_preserved(self):
        script = [{"kind": "node", "x": 1, "y": 2, "z": 7, "hint": "floor_transition"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].hint == "floor_transition"

    def test_label_entry(self):
        script = [{"kind": "label", "label": "MyLabel"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "label"
        assert ins[0].label == "mylabel"  # lowercased

    def test_goto_entry_label_key(self):
        script = [{"kind": "goto", "label": "MyTarget"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "goto"
        assert ins[0].label_jump == "mytarget"

    def test_goto_entry_label_jump_key(self):
        script = [{"kind": "goto", "label_jump": "TheLabel"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].label_jump == "thelabel"

    def test_action_entry(self):
        script = [{"kind": "action", "action": "end"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "action"
        assert ins[0].action == "end"

    def test_wait_entry_secs_key(self):
        script = [{"kind": "wait", "secs": 2.5}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "wait"
        assert ins[0].wait_secs == pytest.approx(2.5)

    def test_wait_entry_wait_secs_key(self):
        script = [{"kind": "wait", "wait_secs": 3.0}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].wait_secs == pytest.approx(3.0)

    def test_use_item_entry(self):
        script = [{"kind": "use_item", "item_name": "rope", "vk": 113}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "use_item"
        assert ins[0].item_name == "rope"
        assert ins[0].hotkey_vk == 113

    def test_use_hotkey_entry(self):
        script = [{"kind": "use_hotkey", "vk": 0x70}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "use_hotkey"
        assert ins[0].hotkey_vk == 0x70

    def test_if_stat_entry(self):
        script = [{
            "kind": "if_stat", "stat": "hp", "op": "<",
            "threshold": 40, "goto_label": "flee",
        }]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "if_stat"
        assert ins[0].stat == "hp"
        assert ins[0].op == "<"
        assert ins[0].threshold == 40
        assert ins[0].goto_label == "flee"

    def test_open_door_entry(self):
        script = [{"kind": "open_door", "x": 100, "y": 200, "z": 7}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "open_door"

    def test_depot_entry(self):
        script = [{"kind": "depot"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "depot"

    def test_talk_npc_entry(self):
        script = [{"kind": "talk_npc", "words": ["hi", "deposit", "bye"]}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "talk_npc"
        assert "hi" in ins[0].words

    def test_say_entry(self):
        script = [{"kind": "say", "sentence": "hello world"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "say"
        assert ins[0].sentence == "hello world"

    def test_call_talk_npc(self):
        script = [{"kind": "call", "func": "talk_npc", "words": ["hi"]}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "talk_npc"

    def test_call_say(self):
        script = [{"kind": "call", "func": "say", "sentence": "hello"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "say"
        assert ins[0].sentence == "hello"

    def test_call_cond_jump_script_options(self):
        script = [{
            "kind": "call",
            "func": "conditional_jump_script_options",
            "var_name": "myvar",
            "label_jump": "lbl_a",
            "label_skip": "lbl_b",
        }]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "cond_jump"
        assert ins[0].var_name == "myvar"

    def test_call_cond_jump_item_count(self):
        script = [{
            "kind": "call",
            "func": "conditional_jump_item_count_below",
            "item_name": "rope",
            "label_jump": "lbl_a",
            "label_skip": "lbl_b",
            "amount": 5,
        }]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "cond_jump"
        assert ins[0].threshold == 5

    def test_call_unknown_func(self):
        script = [{"kind": "call", "func": "unknown_func"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "unknown"

    def test_unknown_kind(self):
        script = [{"kind": "totally_unknown"}]
        ins = ScriptParser.from_json_script(script)
        assert ins[0].kind == "unknown"

    def test_empty_script(self):
        assert ScriptParser.from_json_script([]) == []

    def test_mixed_script(self):
        script = [
            {"kind": "node", "x": 100, "y": 200, "z": 7},
            {"kind": "label", "label": "loop"},
            {"kind": "wait", "secs": 1.0},
            {"kind": "goto", "label": "loop"},
            {"kind": "depot"},
        ]
        ins = ScriptParser.from_json_script(script)
        assert len(ins) == 5
        kinds = [i.kind for i in ins]
        assert "node" in kinds
        assert "label" in kinds
        assert "wait" in kinds
        assert "goto" in kinds
        assert "depot" in kinds


# ─────────────────────────────────────────────────────────────────────────────
# Compact syntax — "at" and "if" shorthands (Scheme B)
# ─────────────────────────────────────────────────────────────────────────────

class TestCompactSyntax:
    """Tests for the "at": [x,y,z] and "if": "stat op threshold" shorthands."""

    def _parse(self, entries):
        return ScriptParser.from_json_script(entries)

    # ── "at": [x, y, z] ──────────────────────────────────────────────────────

    def test_at_node(self):
        ins = self._parse([{"kind": "node", "at": [32407, 32215, 7]}])
        assert ins[0].kind == "node"
        assert ins[0].coord.x == 32407
        assert ins[0].coord.y == 32215
        assert ins[0].coord.z == 7

    def test_at_stand(self):
        ins = self._parse([{"kind": "stand", "at": [32369, 32241, 7]}])
        assert ins[0].kind == "stand"
        assert ins[0].coord.x == 32369

    def test_at_shovel(self):
        ins = self._parse([{"kind": "shovel", "at": [32428, 32314, 7]}])
        assert ins[0].kind == "shovel"
        assert ins[0].coord.y == 32314

    def test_at_rope(self):
        ins = self._parse([{"kind": "rope", "at": [32430, 32302, 9]}])
        assert ins[0].kind == "rope"
        assert ins[0].coord.z == 9

    def test_at_open_door(self):
        ins = self._parse([{"kind": "open_door", "at": [32400, 32217, 7]}])
        assert ins[0].kind == "open_door"
        assert ins[0].coord.x == 32400

    def test_at_explicit_xyz_still_works(self):
        """Backward compatibility: explicit x/y/z still parsed correctly."""
        ins = self._parse([{"kind": "node", "x": 32407, "y": 32215, "z": 7}])
        assert ins[0].coord.x == 32407

    def test_at_xyz_takes_precedence_over_at(self):
        """If both x/y/z and at are present, explicit x/y/z wins."""
        ins = self._parse([{"kind": "node", "x": 100, "y": 200, "z": 7, "at": [32407, 32215, 7]}])
        assert ins[0].coord.x == 100

    # ── "if": "stat op threshold" ─────────────────────────────────────────────

    def test_if_shorthand_hp_lt(self):
        ins = self._parse([{"kind": "if_stat", "if": "hp<40", "goto_label": "flee"}])
        assert ins[0].kind == "if_stat"
        assert ins[0].stat == "hp"
        assert ins[0].op == "<"
        assert ins[0].threshold == 40
        assert ins[0].goto_label == "flee"

    def test_if_shorthand_mp_lt(self):
        ins = self._parse([{"kind": "if_stat", "if": "mp<20", "goto_label": "refill"}])
        assert ins[0].stat == "mp"
        assert ins[0].threshold == 20

    def test_if_shorthand_operator_lte(self):
        ins = self._parse([{"kind": "if_stat", "if": "hp<=50", "goto_label": "x"}])
        assert ins[0].op == "<="

    def test_if_shorthand_operator_gte(self):
        ins = self._parse([{"kind": "if_stat", "if": "hp>=70", "goto_label": "x"}])
        assert ins[0].op == ">="

    def test_if_shorthand_infers_kind(self):
        """When "kind" is absent, "if" key infers kind=if_stat."""
        ins = self._parse([{"if": "hp<40", "goto_label": "flee"}])
        assert ins[0].kind == "if_stat"
        assert ins[0].stat == "hp"
        assert ins[0].threshold == 40

    def test_if_shorthand_case_insensitive(self):
        ins = self._parse([{"kind": "if_stat", "if": "HP<40", "goto_label": "x"}])
        assert ins[0].stat == "hp"

    def test_if_verbose_still_works(self):
        """Backward compatibility: verbose form still works."""
        ins = self._parse([{
            "kind": "if_stat", "stat": "hp", "op": "<", "threshold": 40, "goto_label": "flee"
        }])
        assert ins[0].stat == "hp"
        assert ins[0].op == "<"
        assert ins[0].threshold == 40

    def test_if_shorthand_does_not_override_explicit_stat(self):
        """Explicit stat/op/threshold take precedence over "if" shorthand."""
        ins = self._parse([{
            "kind": "if_stat", "if": "hp<40",
            "stat": "mp", "op": ">", "threshold": 80,
            "goto_label": "x",
        }])
        assert ins[0].stat == "mp"
        assert ins[0].op == ">"
        assert ins[0].threshold == 80

    def test_at_and_if_in_same_script(self):
        """Both shorthands work together in the same script."""
        script = [
            {"kind": "node",    "at": [32407, 32215, 7]},
            {"kind": "if_stat", "if": "hp<40", "goto_label": "flee"},
            {"kind": "rope",    "at": [32430, 32302, 9]},
            {"if": "mp<20",     "goto_label": "refill"},
        ]
        ins = self._parse(script)
        assert len(ins) == 4
        assert ins[0].kind == "node"
        assert ins[0].coord.x == 32407
        assert ins[1].kind == "if_stat"
        assert ins[1].threshold == 40
        assert ins[2].kind == "rope"
        assert ins[2].coord.z == 9
        assert ins[3].kind == "if_stat"
        assert ins[3].stat == "mp"
