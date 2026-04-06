"""
AStarPathfinder
---------------
Pure-Python A* pathfinder that works directly on Tibia map walkability arrays.

Usa solo 4 direcciones cardinales (arriba/abajo/izquierda/derecha) ya que el
botón de teclado solo soporta esas 4 teclas de flecha en Tibia.

El heurístico es la distancia Manhattan:

    h(n) = |n.x - goal.x| + |n.y - goal.y|
"""

from __future__ import annotations

import heapq
import logging
import math
import random
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np

from .models import Coordinate, Route

logger = logging.getLogger(__name__)

# 4-directional neighbours: solo cardinales (sin diagonales)
# 4-directional neighbours (cardinal only)
_NEIGHBOURS_4: List[Tuple[int, int]] = [
             (0, -1),
    (-1,  0),           (1,  0),
             (0,  1),
]

# 8-directional neighbours (cardinal + diagonal)
_NEIGHBOURS_8: List[Tuple[int, int]] = [
    (-1, -1), (0, -1), (1, -1),
    (-1,  0),           (1,  0),
    (-1,  1), (0,  1), (1,  1),
]

_CARD_COST  = 1.0
_DIAG_COST  = math.sqrt(2)  # ~1.4142


# ---------------------------------------------------------------------------

class AStarPathfinder:
    """
    A* pathfinder for a single Tibia floor.

    Parameters
    ----------
    walkability : np.ndarray
        Boolean 2-D array (H×W) where True = walkable.
        Origin (0,0) corresponds to game coordinate (xMin, yMin).
    max_nodes : int
        Safety cap on the number of nodes expanded before giving up.
    allow_diagonal : bool
        If True, diagonal moves are allowed (cost sqrt(2) each).
        Default is False (4-directional keyboard movement).
    """

    def __init__(
        self,
        walkability: np.ndarray,
        max_nodes: int = 50_000,
        allow_diagonal: bool = False,
        path_jitter: float = 0.15,
    ) -> None:
        self.walkability = walkability
        self.max_nodes = max_nodes
        self.allow_diagonal = allow_diagonal
        self.path_jitter = path_jitter
        self._h, self._w = walkability.shape
        self._neighbours = _NEIGHBOURS_8 if allow_diagonal else _NEIGHBOURS_4

    # -----------------------------------------------------------------------
    # Public
    # -----------------------------------------------------------------------

    def find_path(
        self,
        start: Coordinate,
        goal: Coordinate,
    ) -> Route:
        """
        Compute the shortest walkable path from *start* to *goal*.
        Both must be on the same floor.

        Returns a Route with ``found=True`` and a list of Coordinates if a
        path exists, otherwise ``found=False`` with an empty ``steps`` list.
        """
        if start.z != goal.z:
            raise ValueError(
                f"start (z={start.z}) and goal (z={goal.z}) must be on the same floor."
            )

        if start == goal:
            return Route(
                start=start,
                end=goal,
                steps=[start],
                found=True,
                total_distance=0.0,
            )

        sx, sy = start.to_pixel()
        gx, gy = goal.to_pixel()

        if not self._in_bounds(sx, sy):
            return Route(start=start, end=goal, found=False)
        if not self._in_bounds(gx, gy):
            return Route(start=start, end=goal, found=False)

        # If goal is on a non-walkable tile, snap to the nearest walkable
        # neighbour — symmetrical with the start-tile treatment below.
        snapped_goal = False
        goal_snap_distance = 0
        if not self._walkable(gx, gy):
            snapped_g = self._nearest_walkable(gx, gy, goal=(sx, sy))
            if snapped_g is None:
                return Route(start=start, end=goal, found=False)
            goal_snap_distance = abs(snapped_g[0] - gx) + abs(snapped_g[1] - gy)
            if goal_snap_distance != 1:
                return Route(start=start, end=goal, found=False)
            gx, gy = snapped_g
            snapped_goal = True

        # If start is on a non-walkable tile (e.g. NPC tile, door, dynamic
        # object), snap to the nearest walkable neighbour so the character
        # can still be routed out.
        snapped_from_original = False
        snapped_distance = 0
        if not self._walkable(sx, sy):
            snapped = self._nearest_walkable(sx, sy, goal=(gx, gy))
            if snapped is None:
                return Route(start=start, end=goal, found=False)
            snapped_distance = abs(snapped[0] - sx) + abs(snapped[1] - sy)
            if snapped_distance != 1:
                return Route(start=start, end=goal, found=False)
            sx, sy = snapped
            snapped_from_original = True

        # Priority queue: (f, g, (px, py))
        open_heap: List[Tuple[float, float, Tuple[int, int]]] = []
        heapq.heappush(open_heap, (0.0, 0.0, (sx, sy)))

        came_from: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {(sx, sy): None}
        g_score: Dict[Tuple[int, int], float] = {(sx, sy): 0.0}

        nodes_expanded = 0

        while open_heap:
            _, g, current = heapq.heappop(open_heap)

            if current == (gx, gy):
                route = self._reconstruct(came_from, current, start.z, start, goal, g)
                # Prepend the real start so the walker moves from
                # actual position → snapped tile → ... → goal.
                if snapped_from_original and route.steps and route.steps[0] != start:
                    route = Route(
                        start=start,
                        end=goal,
                        steps=[start] + route.steps,
                        total_distance=route.total_distance + snapped_distance,
                        found=True,
                    )
                # Append the real goal when it was snapped to a walkable
                # neighbour so the walker finishes on the requested tile.
                if snapped_goal and route.steps and route.steps[-1] != goal:
                    route = Route(
                        start=route.start,
                        end=goal,
                        steps=route.steps + [goal],
                        total_distance=route.total_distance + goal_snap_distance,
                        found=True,
                    )
                return route

            if g > g_score.get(current, math.inf):
                continue  # stale entry

            nodes_expanded += 1
            if nodes_expanded > self.max_nodes:
                logger.warning(
                    "A* budget exceeded (%d nodes) from %s to %s — "
                    "possible walkability bug or disconnected map region",
                    nodes_expanded, start, goal,
                )
                break

            cx, cy = current

            for dx, dy in self._neighbours:
                nx, ny = cx + dx, cy + dy
                if not self._in_bounds(nx, ny):
                    continue
                if not self._walkable(nx, ny):
                    continue

                # Diagonal moves cost sqrt(2); cardinals cost 1
                move_cost = _DIAG_COST if (dx != 0 and dy != 0) else _CARD_COST

                # Random cost perturbation so repeated runs produce different
                # paths — prevents the bot from walking the exact same tile
                # sequence every loop.  The perturbation is small enough that
                # paths stay near-optimal (within ~15% extra tiles at most).
                if self.path_jitter > 0:
                    move_cost *= 1.0 + random.uniform(0, self.path_jitter)

                # Diagonal corner-cutting check: both adjacent cardinal tiles
                # must be walkable to prevent clipping through wall corners.
                if dx != 0 and dy != 0 and self.allow_diagonal:
                    if not self._walkable(cx + dx, cy) or not self._walkable(cx, cy + dy):
                        continue

                tentative_g = g_score[current] + move_cost
                neighbour = (nx, ny)

                if tentative_g < g_score.get(neighbour, math.inf):
                    came_from[neighbour] = current
                    g_score[neighbour] = tentative_g
                    h = self._heuristic(nx, ny, gx, gy)
                    f = tentative_g + h
                    heapq.heappush(open_heap, (f, tentative_g, neighbour))

        # No path found
        return Route(start=start, end=goal, found=False)

    # -----------------------------------------------------------------------
    # Public utilities
    # -----------------------------------------------------------------------

    def is_reachable(self, start: Coordinate, end: Coordinate) -> bool:
        """
        Return True if a path exists from *start* to *end*.

        Identical to ``find_path(...).found`` but avoids building the full
        step list — the result is discarded after the boolean check.
        """
        return self.find_path(start, end).found

    def count_walkable_tiles(self) -> int:
        """Return the total number of walkable tiles in the current grid."""
        return int(self.walkability.sum())

    def walkable_neighbours(self, coord: Coordinate) -> List[Coordinate]:
        """
        Return the immediately adjacent walkable `Coordinate` objects for
        *coord* using the pathfinder's current movement mode (4- or
        8-directional).

        Useful for adjacency queries without running a full A* search.
        """
        px, py = coord.to_pixel()
        result: List[Coordinate] = []
        for dx, dy in self._neighbours:
            nx, ny = px + dx, py + dy
            if self._in_bounds(nx, ny) and self._walkable(nx, ny):
                result.append(Coordinate.from_pixel(nx, ny, coord.z))
        return result

    def update_walkability(self, walkability: np.ndarray) -> None:
        """
        Hot-swap the walkability grid.

        Call this after a dynamic map update — future ``find_path`` calls
        will use the new grid without needing to create a new pathfinder.

        Parameters
        ----------
        walkability : np.ndarray
            Boolean 2-D array (H×W) where True = walkable.
        """
        self.walkability = walkability
        self._h, self._w = walkability.shape

    def walkability_density(self) -> float:
        """Fraction of tiles that are walkable (0.0 – 1.0)."""
        total = self._h * self._w
        if total == 0:
            return 0.0
        return float(self.count_walkable_tiles() / total)

    def path_cost(self, route: "Route") -> float:
        """
        Estimated movement cost of a completed route.

        Returns ``math.inf`` when ``route.found`` is False or the route has
        fewer than two steps.  Cardinal steps cost 1.0, diagonal steps cost
        ``sqrt(2)``.
        """
        if not route.found or len(route.steps) < 2:
            return math.inf if not route.found else 0.0
        total_cost = 0.0
        for a, b in zip(route.steps, route.steps[1:]):
            ax, ay = a.to_pixel()
            bx, by = b.to_pixel()
            dx, dy = abs(bx - ax), abs(by - ay)
            total_cost += _DIAG_COST if (dx != 0 and dy != 0) else _CARD_COST
        return total_cost

    def stats_snapshot(self) -> dict[str, Any]:
        """Keys: walkable_tiles, total_tiles, density, allow_diagonal, max_nodes."""
        wt = self.count_walkable_tiles()
        total = self._h * self._w
        return {
            "walkable_tiles": wt,
            "total_tiles":    total,
            "density":        wt / total if total else 0.0,
            "allow_diagonal": self.allow_diagonal,
            "max_nodes":      self.max_nodes,
        }

    @property
    def grid_shape(self) -> tuple[int, int]:
        """Shape of the walkability grid as ``(rows, cols)``"""
        return (self._h, self._w)

    @property
    def total_tiles(self) -> int:
        """Total number of tiles in the grid (``rows * cols``)."""
        return int(self._h * self._w)

    @property
    def has_diagonal(self) -> bool:
        """True when 8-directional (diagonal) movement is enabled."""
        return self.allow_diagonal

    @property
    def is_empty_grid(self) -> bool:
        """True when the walkability grid has zero rows or zero columns."""
        return bool(self._h == 0 or self._w == 0)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _in_bounds(self, px: int, py: int) -> bool:
        return cast(bool, 0 <= px < self._w and 0 <= py < self._h)

    def _walkable(self, px: int, py: int) -> bool:
        return bool(self.walkability[py, px])

    def _nearest_walkable(
        self, px: int, py: int, max_radius: int = 5,
        goal: Optional[Tuple[int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """BFS outward from (px, py) to find the closest walkable tile.

        When *goal* is given, among all walkable tiles at the minimum
        BFS distance the one closest to the goal (Manhattan) is preferred.

        Returns ``(nx, ny)`` pixel coords of the nearest walkable tile,
        or ``None`` if nothing walkable exists within *max_radius* tiles.
        """
        from collections import deque

        visited: set[Tuple[int, int]] = {(px, py)}
        queue: deque[Tuple[int, int, int]] = deque([(px, py, 0)])
        candidates: list[Tuple[int, int]] = []
        best_dist: Optional[int] = None

        while queue:
            cx, cy, dist = queue.popleft()
            if dist > max_radius:
                break
            if best_dist is not None and dist > best_dist:
                break
            if self._walkable(cx, cy):
                if best_dist is None:
                    best_dist = dist
                candidates.append((cx, cy))
            for dx, dy in _NEIGHBOURS_4:
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in visited and self._in_bounds(nx, ny):
                    visited.add((nx, ny))
                    queue.append((nx, ny, dist + 1))

        if not candidates:
            return None
        if goal is None or len(candidates) == 1:
            return candidates[0]
        gx, gy = goal
        return min(candidates, key=lambda t: abs(t[0] - gx) + abs(t[1] - gy))

    def _heuristic(self, x1: int, y1: int, x2: int, y2: int) -> float:
        """
        Admissible heuristic:
        - 8-directional: Octile distance  (never over-estimates diagonal moves)
        - 4-directional: Manhattan distance
        """
        dx = abs(x1 - x2)
        dy = abs(y1 - y2)
        if self.allow_diagonal:
            # Octile: move diagonally as far as possible, then straight
            return _CARD_COST * (dx + dy) + (_DIAG_COST - 2 * _CARD_COST) * min(dx, dy)
        return float(dx + dy)

    @staticmethod
    def _reconstruct(
        came_from: Dict[Tuple[int, int], Optional[Tuple[int, int]]],
        current: Tuple[int, int],
        z: int,
        start: Coordinate,
        goal: Coordinate,
        g: float,
    ) -> Route:
        path: List[Coordinate] = []
        node: Optional[Tuple[int, int]] = current
        while node is not None:
            px, py = node
            path.append(Coordinate.from_pixel(px, py, z))
            node = came_from.get(node)
        path.reverse()
        return Route(
            start=start,
            end=goal,
            steps=path,
            total_distance=g,
            found=True,
        )
