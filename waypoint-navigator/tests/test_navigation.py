"""
Tests para src/pathfinder.py y src/navigator.py (lógica offline).
Usa arrays de walkability sintéticos — sin descargar mapas.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.models import Coordinate, BOUNDS, Route
from src.pathfinder import AStarPathfinder
from src.script_parser import ScriptParser, Instruction


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_XO = BOUNDS["xMin"]  # x offset para mapear (0,0) pixel → coord
_YO = BOUNDS["yMin"]  # y offset

def _coord(px: int, py: int, z: int = 7) -> Coordinate:
    """Convierte un pixel offset a Coordinate de Tibia."""
    return Coordinate(_XO + px, _YO + py, z)


def _all_walkable(h: int, w: int) -> np.ndarray:
    return np.ones((h, w), dtype=bool)


def _maze(layout: list[str]) -> np.ndarray:
    """
    Crea un array walkability desde un diagrama de texto.
    '.' = walkable, '#' = pared.
    """
    rows = []
    for row in layout:
        rows.append([c != "#" for c in row])
    return np.array(rows, dtype=bool)


# ─────────────────────────────────────────────────────────────────────────────
# AStarPathfinder — tests básicos
# ─────────────────────────────────────────────────────────────────────────────

class TestAStarPathfinder:
    def test_same_point(self):
        """Start = goal → ruta de 1 paso (el propio punto)."""
        walk = _all_walkable(50, 50)
        pf = AStarPathfinder(walk)
        start = goal = _coord(5, 5)
        route = pf.find_path(start, goal)
        assert route.found is True
        assert route.steps[0] == goal

    def test_straight_horizontal(self):
        """Línea recta horizontal de 10 tiles."""
        walk = _all_walkable(20, 20)
        pf = AStarPathfinder(walk)
        start = _coord(0, 5)
        goal  = _coord(9, 5)
        route = pf.find_path(start, goal)
        assert route.found is True
        assert route.steps[-1] == goal
        # Debe ser exactamente 10 pasos (incluye el punto de llegada)
        assert len(route.steps) == 10

    def test_straight_vertical(self):
        walk = _all_walkable(20, 20)
        pf = AStarPathfinder(walk)
        start = _coord(5, 0)
        goal  = _coord(5, 7)
        route = pf.find_path(start, goal)
        assert route.found is True
        assert len(route.steps) == 8

    def test_path_around_wall(self):
        """
        Laberinto simple — la pared fuerza un rodeo.
        Layout (6×8, 0-indexed):
            ........
            ...##...
            ........
            ........
            ........
            ........
        Start = (0,0), Goal = (7,0) → tiene que rodear o ir directo.
        Pero con la pared en fila 1 cols 3-4 el camino más corto sigue
        siendo por la fila 0 (superior a la pared).
        """
        layout = [
            "........",
            "...##...",
            "........",
            "........",
            "........",
            "........",
        ]
        walk = _maze(layout)
        pf = AStarPathfinder(walk)
        start = _coord(0, 0)
        goal  = _coord(7, 0)
        route = pf.find_path(start, goal)
        assert route.found is True
        assert route.steps[-1] == goal

    def test_no_path_blocked(self):
        """Target completamente rodeado de paredes → found=False."""
        layout = [
            "........",
            "..###...",
            "..#.#...",
            "..###...",
            "........",
        ]
        walk = _maze(layout)
        pf = AStarPathfinder(walk)
        start = _coord(0, 0)
        goal  = _coord(3, 2)  # pixel (3,2) = interior del recinto
        route = pf.find_path(start, goal)
        assert route.found is False
        assert route.steps == []

    def test_different_floor_raises(self):
        walk = _all_walkable(20, 20)
        pf   = AStarPathfinder(walk)
        with pytest.raises(ValueError, match="floor"):
            pf.find_path(_coord(0, 0, z=7), _coord(5, 5, z=8))

    def test_out_of_bounds_start(self):
        """Coordenada fuera del array → found=False sin crash."""
        walk = _all_walkable(10, 10)
        pf   = AStarPathfinder(walk)
        # pixel (100,100) está fuera de un array 10×10
        out_start = _coord(100, 100)
        route = pf.find_path(out_start, _coord(5, 5))
        assert route.found is False

    def test_path_does_not_cross_wall(self):
        """Verifica que ningún paso del camino pisa una pared."""
        layout = [
            "..........",
            "....#.....",
            "....#.....",
            "..........",
        ]
        walk = _maze(layout)
        pf   = AStarPathfinder(walk)
        start = _coord(0, 0)
        goal  = _coord(7, 2)
        route = pf.find_path(start, goal)
        assert route.found is True
        for step in route.steps:
            px = step.x - _XO
            py = step.y - _YO
            assert walk[py, px], f"Paso ({px},{py}) cruza una pared"

    def test_steps_are_adjacent(self):
        """Cada par de pasos consecutivos es adyacente (cardinal, distancia 1)."""
        walk = _all_walkable(30, 30)
        pf   = AStarPathfinder(walk)
        start = _coord(0, 0)
        goal  = _coord(15, 15)
        route = pf.find_path(start, goal)
        assert route.found is True
        for a, b in zip(route.steps, route.steps[1:]):
            dx = abs(a.x - b.x)
            dy = abs(a.y - b.y)
            assert dx + dy == 1, f"Paso no cardinal: {a} → {b}"


# ─────────────────────────────────────────────────────────────────────────────
# Route helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteHelpers:
    def test_total_distance_straight(self):
        """n pasos cardinales → distancia Manhattan = n-1."""
        walk = _all_walkable(20, 20)
        pf   = AStarPathfinder(walk)
        start = _coord(0, 0)
        goal  = _coord(0, 9)
        route = pf.find_path(start, goal)
        assert route.found is True
        # Route.distance (si existe) o simplemente len(steps) - 1
        if hasattr(route, "distance"):
            assert abs(route.distance - 9) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# AStarPathfinder — casos borde avanzados
# ─────────────────────────────────────────────────────────────────────────────

class TestAStarEdgeCases:

    def test_start_on_wall_snaps_to_nearest_walkable(self):
        """Start en pared → snap a tile walkable vecino y encuentra ruta."""
        layout = [
            "......",
            "......",
            "###...",
            "......",
        ]
        walk = _maze(layout)
        pf = AStarPathfinder(walk)
        # pixel (0,2) es pared — snap a (0,1) que sí es walkable
        start = _coord(0, 2)
        goal  = _coord(5, 3)
        route = pf.find_path(start, goal)
        assert route.found is True
        # Route prepends the real (non-walkable) start so the walker
        # moves from actual position to snapped tile first.
        assert route.steps[0] == start
        assert route.steps[1] != start

    def test_start_on_wall_with_nonadjacent_snap_returns_not_found(self):
        layout = [
            "#####",
            "#####",
            "#.#..",
            "#####",
            ".....",
        ]
        walk = _maze(layout)
        pf = AStarPathfinder(walk)
        start = _coord(1, 1)
        goal = _coord(4, 3)

        route = pf.find_path(start, goal)

        assert route.found is False

    def test_start_on_wall_fully_surrounded_returns_not_found(self):
        """Start rodeado totalmente de paredes sin vecino walkable → found=False."""
        layout = [
            "###...",
            "###...",
            "###...",
            "......",
        ]
        walk = _maze(layout)
        pf = AStarPathfinder(walk)
        # (1,1) is in the centre of a 3×3 wall block — no walkable within radius 1
        start = _coord(1, 1)
        goal  = _coord(5, 3)
        route = pf.find_path(start, goal)
        assert route.found is False

    def test_goal_on_wall_snaps_to_nearest_walkable(self):
        """Goal on wall → snapped to walkable neighbour, route found."""
        layout = [
            "......",
            "......",
            "...#..",
            "......",
        ]
        walk = _maze(layout)
        pf = AStarPathfinder(walk)
        start = _coord(0, 0)
        goal  = _coord(3, 2)   # wall with walkable neighbours
        route = pf.find_path(start, goal)
        assert route.found is True
        assert route.end == goal
        assert route.steps[-1] == goal

    def test_out_of_bounds_goal_returns_not_found(self):
        """Goal fuera del array → found=False sin crash."""
        walk = _all_walkable(10, 10)
        pf = AStarPathfinder(walk)
        start    = _coord(0, 0)
        out_goal = _coord(500, 500)
        route = pf.find_path(start, out_goal)
        assert route.found is False

    def test_max_nodes_cap_returns_not_found(self):
        """max_nodes=1 en un laberinto grande → found=False por límite de nodos."""
        walk = _all_walkable(100, 100)
        pf = AStarPathfinder(walk, max_nodes=1)
        start = _coord(0, 0)
        goal  = _coord(50, 50)
        route = pf.find_path(start, goal)
        # Con solo 1 nodo expandible y el objetivo lejos, no puede encontrar el camino
        assert route.found is False

    def test_single_tile_grid(self):
        """Grid de 1×1 — start == goal."""
        walk = _all_walkable(1, 1)
        pf = AStarPathfinder(walk)
        pt = _coord(0, 0)
        route = pf.find_path(pt, pt)
        assert route.found is True
        assert route.steps == [pt]

    def test_long_corridor(self):
        """Corredor estrecho (1 de alto, 50 de ancho) → camino óptimo."""
        walk = _all_walkable(1, 50)
        pf = AStarPathfinder(walk)
        start = _coord(0, 0)
        goal  = _coord(49, 0)
        route = pf.find_path(start, goal)
        assert route.found is True
        assert len(route.steps) == 50

    def test_narrow_maze_forces_detour(self):
        """Pared vertical con un único hueco fuerza rodeo."""
        layout = [
            "......",
            ".####.",
            ".#....",
            ".####.",
            "......",
        ]
        walk = _maze(layout)
        pf   = AStarPathfinder(walk)
        start = _coord(0, 2)
        goal  = _coord(5, 2)
        route = pf.find_path(start, goal)
        # El camino existe a través del hueco
        assert route.found is True
        assert route.steps[-1] == goal

    def test_heuristic_is_manhattan_4dir(self):
        """_heuristic returns Manhattan distance in 4-direction mode."""
        walk = _all_walkable(30, 30)
        pf = AStarPathfinder(walk, allow_diagonal=False)
        assert pf._heuristic(0, 0, 3, 4) == pytest.approx(7.0)
        assert pf._heuristic(5, 5, 5, 5) == pytest.approx(0.0)
        assert pf._heuristic(0, 0, 0, 10) == pytest.approx(10.0)

    def test_heuristic_is_octile_8dir(self):
        """_heuristic returns Octile distance in 8-direction mode."""
        import math
        walk = _all_walkable(30, 30)
        pf = AStarPathfinder(walk, allow_diagonal=True)
        dx, dy = 3, 4
        expected = 1.0 * (dx + dy) + (math.sqrt(2) - 2.0) * min(dx, dy)
        assert pf._heuristic(0, 0, dx, dy) == pytest.approx(expected)

    def test_route_start_and_end_match_coords(self):
        """route.start y route.end coinciden con los argumentos."""
        walk = _all_walkable(30, 30)
        pf   = AStarPathfinder(walk)
        start = _coord(2, 2)
        goal  = _coord(10, 10)
        route = pf.find_path(start, goal)
        assert route.start == start
        assert route.end   == goal


# ─────────────────────────────────────────────────────────────────────────────
# Diagonal A* (allow_diagonal=True)
# ─────────────────────────────────────────────────────────────────────────────

class TestAStarDiagonal:

    def test_allow_diagonal_attribute_true(self):
        pf = AStarPathfinder(_all_walkable(10, 10), allow_diagonal=True)
        assert pf.allow_diagonal is True

    def test_default_is_4dir(self):
        pf = AStarPathfinder(_all_walkable(10, 10))
        assert pf.allow_diagonal is False

    def test_diagonal_path_found(self):
        walk = _all_walkable(20, 20)
        pf = AStarPathfinder(walk, allow_diagonal=True)
        route = pf.find_path(_coord(0, 0), _coord(5, 5))
        assert route.found is True
        assert route.steps[-1] == _coord(5, 5)

    def test_diagonal_distance_shorter_than_cardinal(self):
        """8-dir path (sqrt(2)*5) costs less total than 4-dir (10)."""
        import math
        walk = _all_walkable(20, 20)
        pf4 = AStarPathfinder(walk, allow_diagonal=False, path_jitter=0)
        pf8 = AStarPathfinder(walk, allow_diagonal=True, path_jitter=0)
        r4 = pf4.find_path(_coord(0, 0), _coord(5, 5))
        r8 = pf8.find_path(_coord(0, 0), _coord(5, 5))
        assert r8.total_distance < r4.total_distance
        assert r8.total_distance == pytest.approx(5 * math.sqrt(2), rel=1e-3)

    def test_diagonal_fewer_steps_than_cardinal(self):
        """8-dir needs fewer tile visits: 6 instead of 11."""
        walk = _all_walkable(20, 20)
        pf4 = AStarPathfinder(walk, allow_diagonal=False, path_jitter=0)
        pf8 = AStarPathfinder(walk, allow_diagonal=True, path_jitter=0)
        r4 = pf4.find_path(_coord(0, 0), _coord(5, 5))
        r8 = pf8.find_path(_coord(0, 0), _coord(5, 5))
        assert len(r8.steps) < len(r4.steps)

    def test_diagonal_blocked_by_wall_finds_alternate(self):
        """A wall separating two areas forces detour even in 8-dir mode."""
        layout = [
            "......",
            ".####.",
            ".#....",
            ".####.",
            "......",
        ]
        walk = _maze(layout)
        pf = AStarPathfinder(walk, allow_diagonal=True)
        start = _coord(0, 2)
        goal  = _coord(5, 2)
        route = pf.find_path(start, goal)
        assert route.found is True
        assert route.steps[-1] == goal

    def test_corner_cutting_prevented(self):
        """Diagonal move is blocked when both adjacent cardinals are walls."""
        # Grid where only diagonal clip would connect (0,0)->(1,1)
        # but (1,0) and (0,1) are walls.
        walk = np.ones((5, 5), dtype=bool)
        walk[0, 1] = False  # (col=1, row=0)
        walk[1, 0] = False  # (col=0, row=1)
        pf = AStarPathfinder(walk, allow_diagonal=True)
        start = _coord(0, 0)
        goal  = _coord(1, 1)
        # Can reach goal only going around — not direct diagonal clip
        route = pf.find_path(start, goal)
        if route.found:
            # If a path exists it must use more than 1 step (no clip)
            assert len(route.steps) > 2

    def test_not_found_with_diagonal_returns_false(self):
        """Completely surrounded start in 8-dir still returns not found."""
        walk = np.zeros((5, 5), dtype=bool)
        walk[2, 2] = True  # only one walkable tile
        pf = AStarPathfinder(walk, allow_diagonal=True)
        route = pf.find_path(_coord(2, 2), _coord(0, 0))
        assert route.found is False


# ─────────────────────────────────────────────────────────────────────────────
# AStarPathfinder utility methods
# ─────────────────────────────────────────────────────────────────────────────

class TestIsReachable:

    def test_adjacent_tiles_reachable(self):
        pf = AStarPathfinder(np.ones((10, 10), dtype=bool))
        assert pf.is_reachable(_coord(0, 0), _coord(1, 0)) is True

    def test_same_tile_reachable(self):
        pf = AStarPathfinder(np.ones((10, 10), dtype=bool))
        assert pf.is_reachable(_coord(3, 3), _coord(3, 3)) is True

    def test_blocked_tile_not_reachable(self):
        walk = np.ones((10, 10), dtype=bool)
        walk[:, 5] = False   # vertical wall
        pf = AStarPathfinder(walk)
        assert pf.is_reachable(_coord(0, 0), _coord(9, 0)) is False

    def test_returns_bool_type(self):
        pf = AStarPathfinder(np.ones((5, 5), dtype=bool))
        result = pf.is_reachable(_coord(0, 0), _coord(2, 2))
        assert isinstance(result, bool)

    def test_unreachable_surrounded_start(self):
        walk = np.zeros((5, 5), dtype=bool)
        walk[2, 2] = True
        pf = AStarPathfinder(walk)
        assert pf.is_reachable(_coord(2, 2), _coord(0, 0)) is False


class TestCountWalkableTiles:

    def test_all_walkable(self):
        pf = AStarPathfinder(np.ones((10, 10), dtype=bool))
        assert pf.count_walkable_tiles() == 100

    def test_none_walkable(self):
        pf = AStarPathfinder(np.zeros((10, 10), dtype=bool))
        assert pf.count_walkable_tiles() == 0

    def test_half_walkable(self):
        walk = np.zeros((10, 10), dtype=bool)
        walk[:5, :] = True
        pf = AStarPathfinder(walk)
        assert pf.count_walkable_tiles() == 50

    def test_single_tile(self):
        walk = np.zeros((5, 5), dtype=bool)
        walk[2, 2] = True
        pf = AStarPathfinder(walk)
        assert pf.count_walkable_tiles() == 1

    def test_returns_int(self):
        pf = AStarPathfinder(np.ones((3, 3), dtype=bool))
        assert isinstance(pf.count_walkable_tiles(), int)


class TestWalkableNeighbours:

    def test_centre_tile_has_four_neighbours(self):
        pf = AStarPathfinder(np.ones((5, 5), dtype=bool))
        nb = pf.walkable_neighbours(_coord(2, 2))
        assert len(nb) == 4

    def test_corner_tile_has_two_neighbours(self):
        pf = AStarPathfinder(np.ones((5, 5), dtype=bool))
        nb = pf.walkable_neighbours(_coord(0, 0))
        assert len(nb) == 2

    def test_blocked_neighbours_excluded(self):
        walk = np.ones((5, 5), dtype=bool)
        # Block all 4 cardinal neighbours of (2,2)
        walk[1, 2] = walk[3, 2] = walk[2, 1] = walk[2, 3] = False
        pf = AStarPathfinder(walk)
        nb = pf.walkable_neighbours(_coord(2, 2))
        assert nb == []

    def test_neighbours_are_coordinate_objects(self):
        pf = AStarPathfinder(np.ones((5, 5), dtype=bool))
        from src.models import Coordinate
        nb = pf.walkable_neighbours(_coord(2, 2))
        assert all(isinstance(c, Coordinate) for c in nb)

    def test_diagonal_mode_has_up_to_eight_neighbours(self):
        pf = AStarPathfinder(np.ones((5, 5), dtype=bool), allow_diagonal=True)
        nb = pf.walkable_neighbours(_coord(2, 2))
        assert len(nb) == 8


class TestUpdateWalkability:

    def test_path_found_after_wall_removed(self):
        walk = np.ones((10, 10), dtype=bool)
        walk[:, 5] = False   # wall at column 5
        pf = AStarPathfinder(walk)
        assert pf.is_reachable(_coord(0, 0), _coord(9, 0)) is False

        new_walk = np.ones((10, 10), dtype=bool)  # wall removed
        pf.update_walkability(new_walk)
        assert pf.is_reachable(_coord(0, 0), _coord(9, 0)) is True

    def test_path_blocked_after_wall_added(self):
        walk = np.ones((10, 10), dtype=bool)
        pf = AStarPathfinder(walk)
        assert pf.is_reachable(_coord(0, 0), _coord(9, 0)) is True

        new_walk = np.ones((10, 10), dtype=bool)
        new_walk[:, 5] = False
        pf.update_walkability(new_walk)
        assert pf.is_reachable(_coord(0, 0), _coord(9, 0)) is False

    def test_shape_updated(self):
        pf = AStarPathfinder(np.ones((10, 10), dtype=bool))
        pf.update_walkability(np.ones((20, 30), dtype=bool))
        assert pf._h == 20
        assert pf._w == 30

    def test_count_reflects_new_grid(self):
        pf = AStarPathfinder(np.ones((10, 10), dtype=bool))
        pf.update_walkability(np.zeros((10, 10), dtype=bool))
        assert pf.count_walkable_tiles() == 0


# ─────────────────────────────────────────────────────────────────────────────
# walkability_density / path_cost / stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestPathfinderExtras:

    def test_walkability_density_fully_walkable(self):
        pf = AStarPathfinder(_all_walkable(10, 10))
        assert pf.walkability_density() == pytest.approx(1.0)

    def test_walkability_density_none_walkable(self):
        pf = AStarPathfinder(np.zeros((10, 10), dtype=bool))
        assert pf.walkability_density() == pytest.approx(0.0)

    def test_walkability_density_half_walkable(self):
        grid = np.zeros((10, 10), dtype=bool)
        grid[:, :5] = True  # left half walkable
        pf = AStarPathfinder(grid)
        assert pf.walkability_density() == pytest.approx(0.5)

    def test_walkability_density_updates_after_grid_swap(self):
        pf = AStarPathfinder(_all_walkable(10, 10))
        pf.update_walkability(np.zeros((10, 10), dtype=bool))
        assert pf.walkability_density() == pytest.approx(0.0)

    def test_path_cost_not_found_is_inf(self):
        import math
        pf = AStarPathfinder(_all_walkable(5, 5))
        route = Route(start=_coord(0, 0), end=_coord(4, 4), found=False)
        assert pf.path_cost(route) == math.inf

    def test_path_cost_single_node_is_zero(self):
        pf = AStarPathfinder(_all_walkable(5, 5))
        c = _coord(0, 0)
        route = Route(start=c, end=c, found=True, steps=[c])
        assert pf.path_cost(route) == pytest.approx(0.0)

    def test_path_cost_cardinal_three_steps(self):
        pf = AStarPathfinder(_all_walkable(5, 5))
        route = pf.find_path(_coord(0, 0), _coord(3, 0))
        assert route.found
        assert pf.path_cost(route) == pytest.approx(3.0)

    def test_stats_snapshot_returns_dict(self):
        pf = AStarPathfinder(_all_walkable(5, 5))
        assert isinstance(pf.stats_snapshot(), dict)

    def test_stats_snapshot_all_keys(self):
        pf = AStarPathfinder(_all_walkable(5, 5))
        snap = pf.stats_snapshot()
        for key in ("walkable_tiles", "total_tiles", "density",
                    "allow_diagonal", "max_nodes"):
            assert key in snap, f"Missing key: {key}"

    def test_stats_snapshot_values_all_walkable(self):
        pf = AStarPathfinder(_all_walkable(4, 5), max_nodes=500)
        snap = pf.stats_snapshot()
        assert snap["total_tiles"]    == 20
        assert snap["walkable_tiles"] == 20
        assert snap["density"]        == pytest.approx(1.0)
        assert snap["max_nodes"]      == 500
        assert snap["allow_diagonal"] is False

    def test_stats_snapshot_density_after_grid_swap(self):
        pf = AStarPathfinder(_all_walkable(10, 10))
        pf.update_walkability(np.zeros((10, 10), dtype=bool))
        snap = pf.stats_snapshot()
        assert snap["walkable_tiles"] == 0
        assert snap["density"]        == pytest.approx(0.0)


class TestPathfinderGridShape:

    def test_grid_shape_is_tuple(self):
        pf = AStarPathfinder(_all_walkable(5, 8))
        assert isinstance(pf.grid_shape, tuple)

    def test_grid_shape_rows_cols(self):
        pf = AStarPathfinder(_all_walkable(5, 8))
        assert pf.grid_shape == (5, 8)

    def test_grid_shape_square(self):
        pf = AStarPathfinder(_all_walkable(10, 10))
        assert pf.grid_shape == (10, 10)

    def test_grid_shape_updates_after_walkability_swap(self):
        pf = AStarPathfinder(_all_walkable(4, 6))
        pf.update_walkability(np.ones((3, 7), dtype=bool))
        assert pf.grid_shape == (3, 7)


class TestPathfinderTotalTiles:

    def test_total_tiles_is_int(self):
        pf = AStarPathfinder(_all_walkable(4, 5))
        assert isinstance(pf.total_tiles, int)

    def test_total_tiles_value(self):
        pf = AStarPathfinder(_all_walkable(4, 5))
        assert pf.total_tiles == 20

    def test_total_tiles_square(self):
        pf = AStarPathfinder(_all_walkable(10, 10))
        assert pf.total_tiles == 100

    def test_total_tiles_consistent_with_grid_shape(self):
        pf = AStarPathfinder(_all_walkable(6, 7))
        rows, cols = pf.grid_shape
        assert pf.total_tiles == rows * cols

    def test_total_tiles_updates_after_swap(self):
        pf = AStarPathfinder(_all_walkable(4, 5))
        pf.update_walkability(np.ones((3, 3), dtype=bool))
        assert pf.total_tiles == 9


# ─────────────────────────────────────────────────────────────────────────────
# AStarPathfinder.has_diagonal
# ─────────────────────────────────────────────────────────────────────────────

class TestAStarHasDiagonal:

    def test_false_by_default(self):
        pf = AStarPathfinder(_all_walkable(5, 5))
        assert pf.has_diagonal is False

    def test_true_when_diagonal_enabled(self):
        pf = AStarPathfinder(_all_walkable(5, 5), allow_diagonal=True)
        assert pf.has_diagonal is True

    def test_consistent_with_allow_diagonal_field(self):
        for flag in (True, False):
            pf = AStarPathfinder(_all_walkable(4, 4), allow_diagonal=flag)
            assert pf.has_diagonal == pf.allow_diagonal

    def test_returns_bool(self):
        pf = AStarPathfinder(_all_walkable(5, 5))
        assert isinstance(pf.has_diagonal, bool)

    def test_unchanged_after_walkability_swap(self):
        pf = AStarPathfinder(_all_walkable(4, 4), allow_diagonal=True)
        pf.update_walkability(np.ones((6, 6), dtype=bool))
        assert pf.has_diagonal is True


# ─────────────────────────────────────────────────────────────────────────────
# AStarPathfinder.is_empty_grid
# ─────────────────────────────────────────────────────────────────────────────

class TestAStarIsEmptyGrid:

    def test_false_for_normal_grid(self):
        pf = AStarPathfinder(_all_walkable(5, 5))
        assert pf.is_empty_grid is False

    def test_true_for_zero_rows(self):
        pf = AStarPathfinder(np.ones((0, 10), dtype=bool))
        assert pf.is_empty_grid is True

    def test_true_for_zero_cols(self):
        pf = AStarPathfinder(np.ones((10, 0), dtype=bool))
        assert pf.is_empty_grid is True

    def test_false_after_swap_to_non_empty(self):
        pf = AStarPathfinder(np.ones((0, 5), dtype=bool))
        pf.update_walkability(np.ones((5, 5), dtype=bool))
        assert pf.is_empty_grid is False

    def test_returns_bool(self):
        pf = AStarPathfinder(_all_walkable(3, 3))
        assert isinstance(pf.is_empty_grid, bool)
