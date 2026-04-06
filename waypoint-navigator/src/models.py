"""
Data models for Navigator.
Tibia coordinates:
  - x: 31744 .. 34048
  - y: 30976 .. 32768
  - z: 0 (sky) .. 15 (deep underground); floor 7 = ground floor
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Tibia map bounds (from data/bounds.json)
# ---------------------------------------------------------------------------
BOUNDS = {
    "xMin": 31744,
    "xMax": 34048,
    "yMin": 30976,
    "yMax": 32768,
    "zMin": 0,
    "zMax": 15,
}

# Floor 07 = ground floor
GROUND_FLOOR = 7


@dataclass(frozen=True, order=True, slots=True)
class Coordinate:
    """Absolute Tibia world coordinate (x, y, z)."""

    x: int
    y: int
    z: int = GROUND_FLOOR

    # -----------------------------------------------------------------------
    def validate(self) -> None:
        """Raise ValueError if the coordinate is out of bounds."""
        if not (BOUNDS["xMin"] <= self.x <= BOUNDS["xMax"]):
            raise ValueError(f"x={self.x} out of range [{BOUNDS['xMin']}, {BOUNDS['xMax']}]")
        if not (BOUNDS["yMin"] <= self.y <= BOUNDS["yMax"]):
            raise ValueError(f"y={self.y} out of range [{BOUNDS['yMin']}, {BOUNDS['yMax']}]")
        if not (BOUNDS["zMin"] <= self.z <= BOUNDS["zMax"]):
            raise ValueError(f"z={self.z} out of range [{BOUNDS['zMin']}, {BOUNDS['zMax']}]")

    def clamp(self) -> "Coordinate":
        """
        Return a new Coordinate with x, y, z each clamped to the valid
        Tibia map bounds.  Useful when a calculated position might exceed
        the map edges.
        """
        return Coordinate(
            x=max(BOUNDS["xMin"], min(BOUNDS["xMax"], self.x)),
            y=max(BOUNDS["yMin"], min(BOUNDS["yMax"], self.y)),
            z=max(BOUNDS["zMin"], min(BOUNDS["zMax"], self.z)),
        )

    # -----------------------------------------------------------------------
    def distance_to(self, other: "Coordinate") -> float:
        """Chebyshev distance (Tibia pathfinding metric, same-floor only)."""
        if self.z != other.z:
            raise ValueError("distance_to only works on the same floor.")
        return max(abs(self.x - other.x), abs(self.y - other.y))

    def manhattan_to(self, other: "Coordinate") -> int:
        """Manhattan (taxicab) distance — actual cost of 4-directional movement."""
        return abs(self.x - other.x) + abs(self.y - other.y)

    def euclidean_to(self, other: "Coordinate") -> float:
        """Euclidean distance (ignoring floor difference)."""
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    # -----------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Coordinate":
        return cls(x=int(d["x"]), y=int(d["y"]), z=int(d.get("z", GROUND_FLOOR)))

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "z": self.z}

    def __str__(self) -> str:
        return f"({self.x}, {self.y}, floor {self.z})"

    # -----------------------------------------------------------------------
    # Pixel offset helpers (relative to the full floor PNG)
    # Each PNG pixel = 1 Tibia tile
    # -----------------------------------------------------------------------
    def to_pixel(self) -> tuple[int, int]:
        """Return the (px, py) pixel in the floor PNG for this coordinate."""
        px = self.x - BOUNDS["xMin"]
        py = self.y - BOUNDS["yMin"]
        return px, py

    @classmethod
    def from_pixel(cls, px: int, py: int, z: int) -> "Coordinate":
        return cls(x=px + BOUNDS["xMin"], y=py + BOUNDS["yMin"], z=z)

    def offset(self, dx: int, dy: int, dz: int = 0) -> "Coordinate":
        """
        Return a new Coordinate shifted by (*dx*, *dy*, *dz*).

        Example::

            neighbour = coord.offset(1, 0)   # tile to the right
            above     = coord.offset(0, 0, -1)  # one floor up in Tibia numbering
        """
        return Coordinate(x=self.x + dx, y=self.y + dy, z=self.z + dz)

    def is_same_floor(self, other: "Coordinate") -> bool:
        """Return ``True`` if *other* shares the same floor (z-level)."""
        return self.z == other.z

    def on_floor(self, z: int) -> bool:
        """True when this coordinate is on floor *z*.  Convenience alias for ``self.z == z``."""
        return self.z == z

    def is_adjacent_to(self, other: "Coordinate") -> bool:
        """Return ``True`` if *other* is exactly 1 tile away (8-directional,
        same floor).

        A tile is adjacent when it is within Chebyshev distance 1 but not
        the same tile.  Different floors are never considered adjacent.
        """
        if self.z != other.z:
            return False
        dx = abs(self.x - other.x)
        dy = abs(self.y - other.y)
        return max(dx, dy) == 1

    @property
    def is_surface(self) -> bool:
        """True when this coordinate is on the surface ground floor (z == GROUND_FLOOR)."""
        return self.z == GROUND_FLOOR

    @property
    def is_underground(self) -> bool:
        """True when this coordinate is below the surface ground floor (z > GROUND_FLOOR)."""
        return self.z > GROUND_FLOOR


# ---------------------------------------------------------------------------

ICON_NAMES = {
    0x00: "checkmark",
    0x01: "question",
    0x02: "exclamation",
    0x03: "star",
    0x04: "crossmark",
    0x05: "cross",
    0x06: "mouth",
    0x07: "spear",
    0x08: "sword",
    0x09: "flag",
    0x0A: "lock",
    0x0B: "bag",
    0x0C: "skull",
    0x0D: "dollar",
    0x0E: "red_up",
    0x0F: "red_down",
    0x10: "red_right",
    0x11: "red_left",
    0x12: "up",
    0x13: "down",
}


@dataclass(slots=True)
class Waypoint:
    """A named location on the Tibia world map."""

    name: str
    coord: Coordinate
    icon: str = "checkmark"
    description: str = ""

    # -----------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Waypoint":
        coord = Coordinate.from_dict(d)
        icon_raw = d.get("icon", 0)
        icon_name = (
            ICON_NAMES.get(int(icon_raw), str(icon_raw))
            if isinstance(icon_raw, int)
            else str(icon_raw)
        )
        raw_desc = d.get("description", "")
        raw_name = d.get("name", "Unnamed")
        # Original markers.json stores the waypoint label in 'description';
        # custom waypoints saved by to_dict() use 'name' and leave 'description'
        # empty — fall back to 'name' when 'description' is blank.
        return cls(
            name=raw_desc or raw_name,
            coord=coord,
            icon=icon_name,
            description=raw_desc,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "icon": self.icon,
            "description": self.description,
            **self.coord.to_dict(),
        }

    def __lt__(self, other: "Waypoint") -> bool:
        """Enable sorting of Waypoints by name (case-sensitive lexicographic)."""
        return self.name < other.name

    def __str__(self) -> str:
        return f"{self.name} @ {self.coord}"

    def on_floor(self, z: int) -> bool:
        """Return ``True`` if this waypoint is located on floor *z*."""
        return self.coord.z == z

    @property
    def is_default_icon(self) -> bool:
        """True when the waypoint uses the default 'checkmark' icon."""
        return self.icon == "checkmark"


@dataclass(slots=True)
class FloorTransition:
    """
    A tile that transitions between floors (stairs, hole, rope, ladder, etc.)

    When a character steps on (or uses) `entry`, they are moved to `exit`.
    `kind` describes how to traverse: 'walk' (step-on), 'use' (right-click+use),
    'rope' (use rope on hole above), 'shovel' (open dirt with shovel).
    """
    entry: Coordinate          # tile on `from_z` to step/use
    exit:  Coordinate          # resulting position on `to_z`
    kind:  str = "walk"        # walk | use | rope | shovel | ladder

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FloorTransition":
        return cls(
            entry=Coordinate.from_dict(d["entry"]),
            exit=Coordinate.from_dict(d["exit"]),
            kind=d.get("kind", "walk"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"entry": self.entry.to_dict(), "exit": self.exit.to_dict(), "kind": self.kind}

    def __str__(self) -> str:
        return (f"Transition({self.kind}: {self.entry} → {self.exit})")

    @property
    def is_ascending(self) -> bool:
        """True when this transition moves the character *up* (to a lower z-index floor).

        In Tibia, lower z values represent higher floors (ground = 7, underground = 8+,
        upper floors = 6 and below).
        """
        return self.exit.z < self.entry.z

    @property
    def floor_delta(self) -> int:
        """Signed floor change: negative = ascending (exit z < entry z),
        positive = descending (exit z > entry z), 0 = same floor."""
        return self.exit.z - self.entry.z

    @property
    def is_descending(self) -> bool:
        """True when this transition moves the character *down* (to a higher z-index floor)."""
        return self.exit.z > self.entry.z

    @property
    def is_walk(self) -> bool:
        """True when the transition is traversed by stepping on the tile (``kind == 'walk'``)."""
        return self.kind == "walk"

    @property
    def is_rope(self) -> bool:
        """True when the transition requires using a rope (``kind == 'rope'``)."""
        return self.kind == "rope"

    @property
    def is_use(self) -> bool:
        """True when the transition requires right-click-use (``kind == 'use'``)."""
        return self.kind == "use"

    @property
    def is_shovel(self) -> bool:
        """True when the transition requires opening a dirt hole with a shovel (``kind == 'shovel'``)."""
        return self.kind == "shovel"

    @property
    def is_ladder(self) -> bool:
        """True when the transition is a ladder (``kind == 'ladder'``)."""
        return self.kind == "ladder"


@dataclass(slots=True)
class Route:
    """An ordered sequence of coordinates forming a path between two points."""

    start: Coordinate
    end: Coordinate
    steps: List[Coordinate] = field(default_factory=list)
    total_distance: float = 0.0
    found: bool = False

    # -----------------------------------------------------------------------
    @property
    def step_count(self) -> int:
        """Number of steps in the path (0 when not found)."""
        return len(self.steps)

    def summary(self) -> str:
        if not self.found:
            return f"No path found from {self.start} to {self.end}."
        return (
            f"Route: {self.start} → {self.end} | "
            f"{len(self.steps)} steps | ~{self.total_distance:.0f} tiles"
        )

    def reversed(self) -> "Route":
        """
        Return a new Route that is the mirror image of this one:
        start and end are swapped, and the step list is reversed.
        `total_distance` and `found` are preserved unchanged.
        """
        return Route(
            start=self.end,
            end=self.start,
            steps=list(reversed(self.steps)),
            total_distance=self.total_distance,
            found=self.found,
        )

    def contains(self, coord: Coordinate) -> bool:
        """Return ``True`` if *coord* appears anywhere in the step list."""
        return coord in self.steps

    def slice(self, start_idx: int, end_idx: Optional[int] = None) -> "Route":
        """
        Return a sub-route spanning steps[start_idx:end_idx].

        The new Route's ``start`` / ``end`` fields are set to the first and
        last coordinates of the slice (or to the original boundaries when
        the slice is empty).  ``total_distance`` is scaled proportionally.
        ``found`` is True only when both *start_idx* and *end_idx* are in-range
        and at least one step is included.
        """
        if start_idx < 0 or (end_idx is not None and end_idx < 0):
            raise ValueError(
                f"Route.slice indices must be non-negative, got "
                f"start={start_idx}, end={end_idx}"
            )
        sliced = self.steps[start_idx:end_idx]
        if not sliced:
            return Route(
                start=self.start,
                end=self.end,
                steps=[],
                total_distance=0.0,
                found=False,
            )
        n = len(self.steps)
        ratio = len(sliced) / n if n > 0 else 0.0
        return Route(
            start=sliced[0],
            end=sliced[-1],
            steps=sliced,
            total_distance=self.total_distance * ratio,
            found=True,
        )

    @property
    def first(self) -> Optional[Coordinate]:
        """First step in the route, or ``None`` when the step list is empty."""
        return self.steps[0] if self.steps else None

    @property
    def last(self) -> Optional[Coordinate]:
        """Last step in the route, or ``None`` when the step list is empty."""
        return self.steps[-1] if self.steps else None

    @property
    def is_valid(self) -> bool:
        """True when the route was found **and** contains at least one step."""
        return self.found and len(self.steps) > 0

    def contains_floor(self, z: int) -> bool:
        """Return ``True`` when at least one step in the route is on floor *z*."""
        return any(s.z == z for s in self.steps)

    def floor_span(self) -> int:
        """Number of distinct floor levels (z values) visited by this route.

        Returns 0 for an empty step list.
        """
        return len({s.z for s in self.steps})

    @property
    def is_empty(self) -> bool:
        """True when the route contains no steps."""
        return len(self.steps) == 0

    @property
    def distance_per_step(self) -> float:
        """Average distance between consecutive steps; 0.0 for empty routes."""
        return self.total_distance / len(self.steps) if self.steps else 0.0

    @property
    def is_single_floor(self) -> bool:
        """True when all steps share the same floor (z value), or route is empty."""
        zs = {s.z for s in self.steps}
        return len(zs) <= 1
