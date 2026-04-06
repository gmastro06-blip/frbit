"""
src/navigation/route_recorder.py - Grabador de rutas en tiempo real.

Graba el movimiento del personaje y acciones especiales (rope, door, ladder,
shovel/hole, etc.) y exporta en formato .in (script_parser) o JSON.

Uso básico::

    from src.navigation.route_recorder import LiveRouteRecorder

    rec = LiveRouteRecorder("mi_ruta")
    rec.start()                         # empieza a escuchar posiciones

    # Cada vez que el bot lee la posición, llama:
    rec.record_position(32100, 31200, 7)

    # En el momento que el jugador usa rope:
    rec.rope(32100, 31201, 6)           # tile donde está la soga, floor destino

    # Guardar
    rec.save_script("routes/mi_ruta.in")
    rec.save_json("routes/mi_ruta.json")

Acciones soportadas (todas toman la coordenada actual)::

    rec.rope(x, y, z)      -> rope  (x,y,z)
    rec.door(x, y, z)      -> door  (x,y,z)
    rec.ladder(x, y, z)    -> ladder(x,y,z)
    rec.shovel(x, y, z)    -> shovel(x,y,z)   # open hole
    rec.stand(x, y, z)     -> stand (x,y,z)   # tile exacto
    rec.label(name)        -> label <name>     # punto de salto
    rec.goto(name)         -> goto  <name>
    rec.wait(seconds)      -> wait  <n>
    rec.action(name)       -> action <name>    # travel, depot, end, …
    rec.undo()             -> elimina la última entrada
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Tipos internos
# ---------------------------------------------------------------------------

@dataclass
class _Entry:
    """Una línea del script grabado."""
    kind: str                              # walk | rope | door | ladder | shovel |
                                           # stand | label | goto | wait | action
    x: Optional[int] = None
    y: Optional[int] = None
    z: Optional[int] = None
    param: Optional[str] = None           # nombre del label/goto/action o segundos de wait
    timestamp: float = field(default_factory=time.time)

    # ---- serialización ------------------------------------------------
    def to_script_line(self) -> str:
        """Devuelve la línea en formato .in."""
        if self.kind in ("walk", "node"):
            return f"node ({self.x},{self.y},{self.z})"
        if self.kind == "stand":
            return f"stand ({self.x},{self.y},{self.z})"
        if self.kind == "rope":
            return f"rope ({self.x},{self.y},{self.z})"
        if self.kind == "door":
            return f"door ({self.x},{self.y},{self.z})"
        if self.kind == "ladder":
            return f"ladder ({self.x},{self.y},{self.z})"
        if self.kind == "shovel":
            return f"shovel ({self.x},{self.y},{self.z})"
        if self.kind == "label":
            return f"label {self.param}"
        if self.kind == "goto":
            return f"goto {self.param}"
        if self.kind == "wait":
            return f"wait {self.param}"
        if self.kind == "action":
            return f"action {self.param}"
        return f"# unknown: {self.kind}"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind, "timestamp": self.timestamp}
        if self.x is not None:
            d["x"] = self.x
            d["y"] = self.y
            d["z"] = self.z
        if self.param is not None:
            d["param"] = self.param
        return d


# ---------------------------------------------------------------------------
# LiveRouteRecorder
# ---------------------------------------------------------------------------

class LiveRouteRecorder:
    """
    Graba una ruta en tiempo real desde la posición del bot.

    Parámetros
    ----------
    name:
        Nombre de la ruta (aparece en el JSON de salida).
    min_distance:
        Distancia mínima (en tiles, Chebyshev) para registrar un nuevo
        waypoint de caminata.  Evita duplicados cuando el personaje se
        queda quieto.  Por defecto 1 (graba cada tile).
    """

    def __init__(self, name: str = "ruta", min_distance: int = 1) -> None:
        self.name = name
        self.min_distance = max(1, min_distance)

        self._lock = threading.Lock()
        self._entries: List[_Entry] = []
        self._last_pos: Optional[Tuple[int, int, int]] = None
        self._running = False
        self._start_time: float = 0.0

    # ── Control ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Marca el inicio de la grabación."""
        with self._lock:
            self._running = True
            self._start_time = time.time()

    def stop(self) -> None:
        """Detiene la grabación (no borra los datos)."""
        with self._lock:
            self._running = False

    def clear(self) -> None:
        """Borra todos los datos grabados."""
        with self._lock:
            self._entries.clear()
            self._last_pos = None

    # ── Posición automática ───────────────────────────────────────────────

    def record_position(self, x: int, y: int, z: int) -> bool:
        """
        Registra la posición actual como waypoint de caminata.

        Solo graba si la distancia respecto al último waypoint grabado
        es >= ``min_distance`` (Chebyshev).  Devuelve True si se grabó.
        """
        with self._lock:
            if not self._running:
                return False
            if self._last_pos is not None:
                lx, ly, lz = self._last_pos
                if lz == z:
                    dist = max(abs(x - lx), abs(y - ly))
                    if dist < self.min_distance:
                        return False
                # Cambio de floor siempre se graba
            self._entries.append(_Entry(kind="node", x=x, y=y, z=z))
            self._last_pos = (x, y, z)
            return True

    # ── Acciones manuales ─────────────────────────────────────────────────

    def rope(self, x: int, y: int, z: int) -> None:
        """Graba un uso de soga (rope) en (x,y,z)."""
        self._add_coord("rope", x, y, z)

    def door(self, x: int, y: int, z: int) -> None:
        """Graba apertura de puerta en (x,y,z)."""
        self._add_coord("door", x, y, z)

    def ladder(self, x: int, y: int, z: int) -> None:
        """Graba uso de escalera/stairs/ladder en (x,y,z)."""
        self._add_coord("ladder", x, y, z)

    def shovel(self, x: int, y: int, z: int) -> None:
        """Graba apertura de agujero con shovel en (x,y,z)."""
        self._add_coord("shovel", x, y, z)

    def stand(self, x: int, y: int, z: int) -> None:
        """Graba un waypoint de tile exacto (stand) en (x,y,z)."""
        self._add_coord("stand", x, y, z)

    def label(self, name: str) -> None:
        """Inserta un punto de salto (label) en la posición actual."""
        with self._lock:
            self._entries.append(_Entry(kind="label", param=name))

    def goto(self, name: str) -> None:
        """Inserta un salto incondicional (goto) a ``name``."""
        with self._lock:
            self._entries.append(_Entry(kind="goto", param=name))

    def wait(self, seconds: float) -> None:
        """Inserta una pausa de ``seconds`` segundos."""
        with self._lock:
            self._entries.append(_Entry(kind="wait", param=str(seconds)))

    def action(self, name: str) -> None:
        """Inserta una acción especial (travel, depot, end, combat_pause…)."""
        with self._lock:
            self._entries.append(_Entry(kind="action", param=name))

    def undo(self) -> Optional[_Entry]:
        """Elimina y devuelve la última entrada grabada."""
        with self._lock:
            if not self._entries:
                return None
            entry = self._entries.pop()
            # Recalcular _last_pos
            self._last_pos = None
            for e in reversed(self._entries):
                if e.x is not None and e.y is not None and e.z is not None:
                    self._last_pos = (e.x, e.y, e.z)
                    break
            return entry

    # ── Consulta ─────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """Número de entradas grabadas."""
        with self._lock:
            return len(self._entries)

    @property
    def current_pos(self) -> Optional[Tuple[int, int, int]]:
        """Última posición grabada, o None."""
        with self._lock:
            return self._last_pos

    def entries(self) -> List[_Entry]:
        """Devuelve una copia de las entradas grabadas."""
        with self._lock:
            return list(self._entries)

    # ── Exportación ───────────────────────────────────────────────────────

    def to_script(self) -> str:
        """
        Genera el contenido del archivo .in.

        El resultado es compatible con ``script_parser.ScriptParser``.
        """
        with self._lock:
            lines = [f"# route: {self.name}", ""]
            for entry in self._entries:
                lines.append(entry.to_script_line())
            lines.append("")
            lines.append("action end")
            return "\n".join(lines)

    def save_script(self, path: str) -> Path:
        """Guarda el script en formato .in en ``path``."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_script(), encoding="utf-8")
        return p

    def to_dict(self) -> Dict[str, Any]:
        """Devuelve la ruta como dict (compatible con WaypointLogger JSON)."""
        with self._lock:
            return {
                "schema_version": 1,
                "map_name": self.name,
                "recorded_at": self._start_time,
                "entries": [e.to_dict() for e in self._entries],
            }

    def save_json(self, path: str) -> Path:
        """Guarda la ruta en JSON en ``path``."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return p

    @classmethod
    def load_json(cls, path: str) -> "LiveRouteRecorder":
        """Carga un JSON guardado con ``save_json()`` y devuelve un recorder."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        rec = cls(name=data.get("map_name", "ruta"))
        rec._start_time = float(data.get("recorded_at", 0.0))

        for d in data.get("entries", []):
            entry = _Entry(
                kind=d["kind"],
                x=d.get("x"),
                y=d.get("y"),
                z=d.get("z"),
                param=d.get("param"),
                timestamp=float(d.get("timestamp", 0.0)),
            )
            rec._entries.append(entry)
            if entry.x is not None and entry.y is not None and entry.z is not None:
                rec._last_pos = (entry.x, entry.y, entry.z)

        return rec

    # ── Privado ───────────────────────────────────────────────────────────

    def _add_coord(self, kind: str, x: int, y: int, z: int) -> None:
        with self._lock:
            self._entries.append(_Entry(kind=kind, x=x, y=y, z=z))
            self._last_pos = (x, y, z)
