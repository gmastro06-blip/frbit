"""
TransitionRegistry
------------------
Manages floor-to-floor transitions (stairs, holes, ropes, ladders) for
multi-floor pathfinding.

Data format (cache/transitions.json):
    [
        {
            "entry": {"x": 32369, "y": 32241, "z": 7},
            "exit":  {"x": 32369, "y": 32241, "z": 8},
            "kind":  "walk"      // walk | use | rope | shovel | ladder
        },
        ...
    ]

Transition kinds:
    walk    – step onto the tile to descend/ascend (most stairs)
    use     – right-click + use on the tile (manholes, trapdoors)
    rope    – use a rope on the hole tile above (z+1 → z)
    shovel  – use a shovel on the dirt tile (opens a hole)
    ladder  – use a ladder item (up transitions)

Usage:
    reg = TransitionRegistry.load()
    # All transitions from floor 7 → any other floor
    exits = reg.from_floor(7)
    # Transitions between two specific floors
    links = reg.between(7, 8)
    # Nearest transition from a coordinate
    nearest = reg.nearest_from(coord, max_dist=50)
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import Coordinate, FloorTransition

logger = logging.getLogger("wn")

# ---------------------------------------------------------------------------
_DEFAULT_PATH = Path(__file__).parent.parent / "cache" / "transitions.json"


class TransitionRegistry:
    """
    In-memory registry of all known floor transitions.

    Parameters
    ----------
    transitions : list[FloorTransition]
        Loaded transition entries.
    """

    def __init__(self, transitions: Optional[List[FloorTransition]] = None) -> None:
        self._transitions: List[FloorTransition] = transitions or []
        # Index: from_z → list of transitions
        self._by_from: Dict[int, List[FloorTransition]] = {}
        # Index: (from_z, to_z) → list of transitions
        self._by_pair: Dict[Tuple[int, int], List[FloorTransition]] = {}
        for t in self._transitions:
            fz = t.entry.z
            tz = t.exit.z
            self._by_from.setdefault(fz, []).append(t)
            self._by_pair.setdefault((fz, tz), []).append(t)

    # -----------------------------------------------------------------------
    # Factory
    # -----------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path = _DEFAULT_PATH) -> "TransitionRegistry":
        """Load transitions from JSON file. Returns empty registry if missing."""
        if not path.exists():
            return cls([])
        with open(path, encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("Corrupt transitions file %s: %s", path, exc)
                return cls([])
        transitions = [FloorTransition.from_dict(d) for d in data]
        return cls(transitions)

    def save(self, path: Path = _DEFAULT_PATH) -> None:
        """Persist all transitions to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([t.to_dict() for t in self._transitions], fh, indent=2)

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    def add(self, transition: FloorTransition) -> None:
        """Add a transition at runtime."""
        self._transitions.append(transition)
        fz = transition.entry.z
        tz = transition.exit.z
        self._by_from.setdefault(fz, []).append(transition)
        self._by_pair.setdefault((fz, tz), []).append(transition)

    def remove(self, entry: Coordinate) -> int:
        """
        Remove all transitions whose entry coordinate matches *entry*.

        Returns the number of transitions removed.
        """
        to_remove = [t for t in self._transitions if t.entry == entry]
        if not to_remove:
            return 0
        remove_set = set(id(t) for t in to_remove)
        self._transitions = [t for t in self._transitions if id(t) not in remove_set]
        # Rebuild indexes
        self._by_from.clear()
        self._by_pair.clear()
        for t in self._transitions:
            fz = t.entry.z
            tz = t.exit.z
            self._by_from.setdefault(fz, []).append(t)
            self._by_pair.setdefault((fz, tz), []).append(t)
        return len(to_remove)

    def remove_by_floor(self, z: int) -> int:
        """
        Remove every transition whose *entry* is on floor *z*.

        Returns the number of transitions removed.
        """
        to_remove = [t for t in self._transitions if t.entry.z == z]
        if not to_remove:
            return 0
        remove_set = set(id(t) for t in to_remove)
        self._transitions = [t for t in self._transitions if id(t) not in remove_set]
        # Rebuild indexes
        self._by_from.clear()
        self._by_pair.clear()
        for t in self._transitions:
            fz = t.entry.z
            tz = t.exit.z
            self._by_from.setdefault(fz, []).append(t)
            self._by_pair.setdefault((fz, tz), []).append(t)
        return len(to_remove)

    def count_by_kind(self, kind: str) -> int:
        """Return the number of transitions with the given *kind* string."""
        return sum(1 for t in self._transitions if t.kind == kind)

    def all_floors(self) -> List[int]:
        """Sorted list of unique floor numbers that have at least one departing transition."""
        return sorted(self._by_from.keys())

    def from_floor(self, z: int) -> List[FloorTransition]:
        """All transitions whose entry is on floor *z*."""
        return list(self._by_from.get(z, []))

    def between(self, from_z: int, to_z: int) -> List[FloorTransition]:
        """Transitions between exactly from_z → to_z."""
        return list(self._by_pair.get((from_z, to_z), []))

    def reachable_floors(self, from_z: int) -> List[int]:
        """Returns list of floors directly reachable from *from_z*."""
        return sorted(set(tz for (fz, tz) in self._by_pair if fz == from_z))

    def nearest_from(
        self,
        coord: Coordinate,
        max_dist: float = 200.0,
        to_z: Optional[int] = None,
    ) -> Optional[FloorTransition]:
        """
        Find the nearest transition reachable from *coord* (same floor).
        Optionally filter by destination floor *to_z*.
        """
        candidates = self.from_floor(coord.z)
        if to_z is not None:
            candidates = [t for t in candidates if t.exit.z == to_z]
        best: Optional[FloorTransition] = None
        best_d = float("inf")
        for t in candidates:
            d = math.sqrt((t.entry.x - coord.x) ** 2 + (t.entry.y - coord.y) ** 2)
            if d < best_d and d <= max_dist:
                best_d = d
                best = t
        return best

    def __len__(self) -> int:
        return len(self._transitions)

    def __repr__(self) -> str:
        return f"TransitionRegistry({len(self._transitions)} transitions)"

    @property
    def is_empty(self) -> bool:
        """True when the registry contains no transitions."""
        return len(self._transitions) == 0

    @property
    def kinds(self) -> List[str]:
        """Sorted list of unique transition kind strings in this registry."""
        return sorted({t.kind for t in self._transitions})

    def stats_snapshot(self) -> dict[str, Any]:
        """Keys: count, is_empty, floors, kinds."""
        return {
            "count":    len(self._transitions),
            "is_empty": self.is_empty,
            "floors":   self.all_floors(),
            "kinds":    self.kinds,
        }

    @property
    def ascending_count(self) -> int:
        """Number of transitions that move the character *up* (exit z-index < entry z-index)."""
        return sum(1 for t in self._transitions if t.exit.z < t.entry.z)

    @property
    def descending_count(self) -> int:
        """Number of transitions that move the character *down* (exit z-index > entry z-index)."""
        return sum(1 for t in self._transitions if t.exit.z > t.entry.z)

    @property
    def total_count(self) -> int:
        """Total number of transitions in the registry."""
        return len(self._transitions)

    @property
    def floor_count(self) -> int:
        """Number of distinct source floors that have at least one transition."""
        return len(set(self.all_floors()))

    @property
    def kind_count(self) -> int:
        """Number of *distinct* transition kinds present in this registry."""
        return len(self.kinds)

    @property
    def has_walk(self) -> bool:
        """True when at least one ``walk`` transition is registered."""
        return self.count_by_kind("walk") > 0

    @property
    def has_rope(self) -> bool:
        """True when at least one ``rope`` transition is registered."""
        return self.count_by_kind("rope") > 0

    @property
    def has_ladder(self) -> bool:
        """True when at least one ``ladder`` transition is registered."""
        return self.count_by_kind("ladder") > 0


# ---------------------------------------------------------------------------
# Convenience: build registry from script parser instructions
# ---------------------------------------------------------------------------

def transitions_from_script(instructions: list[Any]) -> TransitionRegistry:
    """
    Build a TransitionRegistry from a parsed list of Instruction objects.
    Extracts ladder / rope / shovel instructions as floor transitions.

    *instructions* is the list returned by ScriptParser.parse_file().
    The destination floor is inferred as entry.z ± 1 depending on kind:
      ladder → entry.z - 1  (go up)
      rope   → entry.z - 1  (go up via rope)
      shovel → entry.z + 1  (dig down)
    """
    from .models import Coordinate, FloorTransition

    reg = TransitionRegistry()
    for ins in instructions:
        if ins.kind in ("ladder", "rope", "shovel") and ins.coord is not None:
            c = ins.coord
            entry = Coordinate(c.x, c.y, c.z)
            if ins.kind in ("ladder", "rope"):
                # Going up in Tibia: z decreases
                exit_z = max(0, c.z - 1)
            else:
                # shovel: dig down → z increases
                exit_z = min(15, c.z + 1)
            # Exit position is typically same x/y (Tibia convention)
            exit_coord = Coordinate(c.x, c.y, exit_z)
            reg.add(FloorTransition(entry=entry, exit=exit_coord, kind=ins.kind))
    return reg
