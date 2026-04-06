from __future__ import annotations

from pathlib import Path

from src.route_validator import RouteJsonSimulator
from src.script_parser import ScriptCoord, ScriptParser


def test_route_json_simulator_valid_script_route(tmp_path: Path) -> None:
    route_path = tmp_path / "route.json"
    route_path.write_text(
        """
        {
          "_meta": {"start_coord": {"x": 32352, "y": 32227, "z": 7}},
          "script": [
            {"kind": "label", "label": "start"},
            {"kind": "node", "x": 32352, "y": 32227, "z": 7},
            {"kind": "stand", "x": 32353, "y": 32228, "z": 7},
            {"kind": "talk_npc", "words": ["hi", "deposit all", "yes"]}
          ]
        }
        """,
        encoding="utf-8",
    )

    simulator = RouteJsonSimulator.from_file(route_path)
    assert simulator.validate_coordinates() == []
    assert simulator.get_coordinate_sequence() == [
        ScriptCoord(32352, 32227, 7),
        ScriptCoord(32352, 32227, 7),
        ScriptCoord(32353, 32228, 7),
    ]
    summary = simulator.get_coordinate_summary()
    assert summary is not None
    assert summary.count == 3
    assert summary.min_x == 32352
    assert summary.max_x == 32353
    assert summary.min_y == 32227
    assert summary.max_y == 32228
    assert summary.min_z == 7
    assert summary.max_z == 7


def test_route_json_simulator_finds_bad_coordinates(tmp_path: Path) -> None:
    route_path = tmp_path / "route.json"
    route_path.write_text(
        """
        {
          "script": [
            {"kind": "node", "x": "32352", "y": 32227, "z": 7},
            {"kind": "stand", "x": 100, "y": 200, "z": 7},
            {"kind": "rope", "x": 32352, "y": 32227}
          ]
        }
        """,
        encoding="utf-8",
    )

    simulator = RouteJsonSimulator.from_file(route_path)
    errors = simulator.validate_coordinates()
    assert any("script[1].coord: x=100 outside typical Tibia range" in error for error in errors)
    assert any("script[2]: movement instruction 'rope' missing coordinates" in error for error in errors)


def test_route_json_simulator_validates_waypoints_list(tmp_path: Path) -> None:
    route_path = tmp_path / "route.json"
    route_path.write_text(
        """
        {
          "waypoints": [
            {"x": 32352, "y": 32227, "z": 7},
            [32353, 32228, 7],
            {"x": 32354, "y": 32229, "z": 7}
          ]
        }
        """,
        encoding="utf-8",
    )

    simulator = RouteJsonSimulator.from_file(route_path)
    assert simulator.validate_coordinates() == []
    assert simulator.get_coordinate_sequence() == [
        ScriptCoord(32352, 32227, 7),
        ScriptCoord(32353, 32228, 7),
        ScriptCoord(32354, 32229, 7),
    ]


def test_wasp_thais_ek_nopvp_route_asset_is_valid() -> None:
    route_path = Path(__file__).resolve().parents[1] / "routes" / "wasp_thais" / "wasp_thais_ek_nopvp.json"

    simulator = RouteJsonSimulator.from_file(route_path)
    instructions = ScriptParser.parse_file(route_path)

    assert simulator.validate_coordinates() == []
    assert any(ins.kind == "cond_jump" for ins in instructions)
    assert any(ins.kind == "action" and ins.action == "buy_potions" for ins in instructions)


def test_wasp_thais_ek_nopvp_live_route_asset_is_valid() -> None:
    route_path = Path(__file__).resolve().parents[1] / "routes" / "wasp_thais" / "wasp_thais_ek_nopvp_live.json"

    simulator = RouteJsonSimulator.from_file(route_path)
    instructions = ScriptParser.parse_file(route_path)

    assert simulator.validate_coordinates() == []
    assert any(ins.kind == "action" and ins.action == "sell" and "vial" in ins.raw for ins in instructions)
    assert any(ins.kind == "action" and ins.action == "buy_potions" and "mana potion" in ins.raw for ins in instructions)
