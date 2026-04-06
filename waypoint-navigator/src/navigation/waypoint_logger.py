from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Position:
    x: int
    y: int
    z: int


@dataclass
class Waypoint:
    id: int
    position: Position
    action: str = "walk"
    label: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    timestamp: float = 0.0


@dataclass
class PlayerAction:
    timestamp: float
    type: str
    description: str
    position: Optional[Position]
    meta: Dict[str, Any]


class WaypointLogger:
    """
    Guarda waypoints y acciones del jugador y exporta a un JSON
    compatible con un esquema tipo "Tibia Map IO" (JSON estructurado).

    Notas:
    - El esquema es intencionadamente simple y extensible.
    - Si necesitás un esquema exacto de otra herramienta, puedo adaptar
      el exporter a ese formato específico.
    """

    def __init__(self, map_name: str = "route", origin: Optional[Position] = None):
        self.map_name = map_name
        self.origin = origin
        self._lock = threading.Lock()
        self._next_id = 1
        self.waypoints: List[Waypoint] = []
        self.actions: List[PlayerAction] = []

    def add_waypoint(self, x: int, y: int, z: int, *, action: str = "walk",
                     label: Optional[str] = None, params: Optional[Dict[str, Any]] = None,
                     timestamp: Optional[float] = None) -> Waypoint:
        """Añade y devuelve un waypoint (thread-safe)."""
        if timestamp is None:
            timestamp = time.time()

        with self._lock:
            wp = Waypoint(
                id=self._next_id,
                position=Position(x, y, z),
                action=action,
                label=label,
                params=params or {},
                timestamp=timestamp,
            )
            self.waypoints.append(wp)
            self._next_id += 1

        return wp

    def record_action(self, action_type: str, description: str,
                      position: Optional[Position] = None,
                      meta: Optional[Dict[str, Any]] = None,
                      timestamp: Optional[float] = None) -> PlayerAction:
        """Registra una acción del jugador (ej. 'move', 'talk_npc', 'deposit')."""
        if timestamp is None:
            timestamp = time.time()
        entry = PlayerAction(
            timestamp=timestamp,
            type=action_type,
            description=description,
            position=position,
            meta=meta or {},
        )

        with self._lock:
            self.actions.append(entry)

        return entry

    def to_dict(self) -> Dict[str, Any]:
        """Devuelve una estructura dict lista para serializar."""
        with self._lock:
            waypoints = [
                {
                    "id": wp.id,
                    "label": wp.label,
                    "action": wp.action,
                    "params": wp.params or {},
                    "position": {"x": wp.position.x, "y": wp.position.y, "z": wp.position.z},
                    "timestamp": wp.timestamp,
                }
                for wp in self.waypoints
            ]

            actions = [
                {
                    "timestamp": a.timestamp,
                    "type": a.type,
                    "description": a.description,
                    "position": {"x": a.position.x, "y": a.position.y, "z": a.position.z}
                    if a.position
                    else None,
                    "meta": a.meta,
                }
                for a in self.actions
            ]

        result: Dict[str, Any] = {
            "schema_version": 1,
            "map_name": self.map_name,
            "origin": {"x": self.origin.x, "y": self.origin.y, "z": self.origin.z}
            if self.origin
            else None,
            "waypoints": waypoints,
            "actions": actions,
            "generated_at": time.time(),
        }

        return result

    def save_json(self, path: str, *, indent: int = 2) -> None:
        """Guarda la representación JSON en `path`."""
        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)

    def load_json(self, path: str) -> None:
        """Carga un JSON exportado previamente y restaura waypoints/actions.

        Nota: sobrescribe los contenidos actuales.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        with self._lock:
            self.waypoints = []
            self.actions = []
            self._next_id = 1

            for w in data.get("waypoints", []):
                pos = w.get("position", {})
                wp = Waypoint(
                    id=w.get("id", self._next_id),
                    position=Position(int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("z", 0))),
                    action=w.get("action", "walk"),
                    label=w.get("label"),
                    params=w.get("params", {}),
                    timestamp=float(w.get("timestamp", 0.0)),
                )
                self.waypoints.append(wp)
                self._next_id = max(self._next_id, wp.id + 1)

            for a in data.get("actions", []):
                pos = a.get("position")
                position = None
                if pos:
                    position = Position(int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("z", 0)))

                pa = PlayerAction(
                    timestamp=float(a.get("timestamp", time.time())),
                    type=a.get("type", "unknown"),
                    description=a.get("description", ""),
                    position=position,
                    meta=a.get("meta", {}),
                )
                self.actions.append(pa)

    def export_tibia_map_io(self) -> str:
        """Exporta un JSON como string siguiendo el esquema de `to_dict()`."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def export_cloudbot_dict(self) -> Dict[str, Any]:
        """Exporta la ruta y acciones en un esquema compatible con CloudBot-style JSON.

        El formato generado es práctico y suele ser aceptado por herramientas que
        importan rutas/waypoints en JSON. Si necesitás un mapeo exacto a una
        versión concreta de CloudBot, indícamela y adapto el exporter.
        """
        with self._lock:
            waypoints = [
                {
                    "x": wp.position.x,
                    "y": wp.position.y,
                    "z": wp.position.z,
                    "type": wp.action,
                    "label": wp.label,
                    "params": wp.params or {},
                    "id": wp.id,
                }
                for wp in self.waypoints
            ]

            actions = []
            for a in self.actions:
                pos = None
                if a.position:
                    pos = {"x": a.position.x, "y": a.position.y, "z": a.position.z}

                actions.append(
                    {
                        "time": a.timestamp,
                        "type": a.type,
                        "description": a.description,
                        "position": pos,
                        "meta": a.meta,
                    }
                )

        cloudbot = {
            "format": "cloudbot-route",
            "version": 1,
            "name": self.map_name,
            "origin": {"x": self.origin.x, "y": self.origin.y, "z": self.origin.z}
            if self.origin
            else None,
            "waypoints": waypoints,
            "actions": actions,
            "exported_at": time.time(),
        }

        return cloudbot

    def save_cloudbot(self, path: str, *, indent: int = 2) -> None:
        """Guarda el export CloudBot en `path` (JSON)."""
        data = self.export_cloudbot_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)


if __name__ == "__main__":
    # Demo de uso
    logger = WaypointLogger(map_name="thais_route", origin=Position(32347, 32226, 7))
    logger.add_waypoint(32347, 32226, 7, action="start", label="start")
    logger.add_waypoint(32320, 32254, 7, action="walk", label="bank")
    logger.record_action("talk_npc", "deposit all", position=Position(32319, 32256, 7))
    print(logger.export_tibia_map_io())
