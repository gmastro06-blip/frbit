"""
ConditionMonitor
----------------
Detecta condiciones de estado (veneno, parálisis, quemadura, etc.) en el
cliente de Tibia analizando los iconos de condición del frame OBS.

Métodos de detección:
  1. Color-based (default): analiza el histograma de color en la ROI donde
     aparecen los iconos de condición. No requiere templates.
  2. Template-based       : compara con recortes de los iconos guardados en
                             cache/templates/conditions/

Respuestas configurables:
  - Hotkey de cura (antídoto, poción, hechizo)
  - Mensajes de log
  - Cooldown entre usos del mismo remedio

Posiciones de iconos de condición en Tibia 1920×1080:
  Los iconos aparecen en el panel derecho, justo a la derecha de los nombres
  de HP/MP, aproximadamente en y=470–490.  Ajusta condition_icons_roi.

Uso:
    cm = ConditionMonitor(ctrl)
    cm.set_frame_getter(lambda: detector.get_raw_frame())
    cm.start()
    ...
    cm.stop()
"""

from __future__ import annotations

import json
import random
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import cv2
import numpy as np

# ---------------------------------------------------------------------------
CONDITION_CONFIG_FILE = Path(__file__).parent.parent / "condition_config.json"

from src.config_paths import TEMPLATES_DIR as _TEMPLATES_DIR


# ---------------------------------------------------------------------------
# Rangos de color HSV para cada condición
# (hmin, hmax, smin, smax, vmin, vmax)
_HSV_RANGES: Dict[str, tuple[int, ...]] = {
    # Veneno — verde oscuro
    "poison":    (40,  80, 80, 255, 50, 220),
    # Parálisis — azul oscuro / morado
    "paralyze":  (100, 140, 80, 255, 50, 200),
    # Quemadura — naranja (hue 8-25 en OpenCV 0-180).
    # NOTA: rango ajustado para no solapar con drunk (26+).
    "burning":   (8,   25, 150, 255, 150, 255),
    # Borracho — amarillo-verde (hue 26-45 para evitar solape con burning)
    "drunk":     (26,  45, 100, 255, 120, 255),
    # Sangrado — rojo intenso / rojo puro (hue 0-5 + wrap 170-180).
    # NOTA: antes usaba hue 0-8 que solapaba con burning (0-20).
    # El icono de bleeding en Tibia es rojo sangre puro.
    "bleeding":  (0,   5,  180, 255, 120, 255),
    # Congelado — azul claro / cian
    "freezing":  (85,  105, 80, 255, 160, 255),
}

# Número mínimo de píxeles del color para confirmar la condición
_MIN_PIXELS: Dict[str, int] = {
    "poison":   6,
    "paralyze": 5,
    "burning":  8,
    "drunk":    6,
    "bleeding": 8,
    "freezing": 6,
}


# ---------------------------------------------------------------------------
@dataclass
class ConditionReaction:
    """
    Reacción automática ante una condición detectada.

    vk       : código VK de la tecla a pulsar (0 = solo loguear, sin acción)
    cooldown : segundos mínimos entre usos
    label    : nombre descriptivo para el log
    """
    condition: str
    vk: int = 0
    cooldown: float = 2.5
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.condition


@dataclass
class ConditionConfig:
    """
    Configuración del monitor de condiciones.

    condition_icons_roi : [x, y, w, h]
        Región del frame donde aparecen los iconos de condición.
        Default: banda horizontal bajo las barras de HP/MP en 1920×1080.

    reactions : list[dict]
        Ejemplo:
        [
            {"condition": "poison",   "vk": 0x71, "cooldown": 3.0, "label": "antídoto F2"},
            {"condition": "paralyze", "vk": 0x72, "cooldown": 4.0, "label": "utani hur F3"}
        ]

    detection_mode : "color" | "template"
        "color"    → análisis HSV (no requiere templates, menos preciso)
        "template" → template matching de iconos (más preciso, requiere setup)

    check_interval : float
        Segundos entre comprobaciones.
    """

    # ROI de los iconos de condición [x, y, w, h] en 1920×1080
    condition_icons_roi: List[int] = field(
        default_factory=lambda: [1709, 462, 200, 30]
    )
    # Reacciones ante condiciones
    reactions: List[Dict[str, Any]] = field(default_factory=list)
    # Modo de detección
    detection_mode: str = "color"
    # Intervalo de verificación (s)
    check_interval: float = 0.5
    # Resolución de referencia del frame OBS
    ref_width: int = 1920
    ref_height: int = 1080
    # Confianza mínima para template matching
    confidence: float = 0.65

    def save(self, path: Path = CONDITION_CONFIG_FILE) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2)

    @classmethod
    def load(cls, path: Path = CONDITION_CONFIG_FILE) -> "ConditionConfig":
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
class ConditionDetector:
    """
    Detecta qué condiciones están activas en un frame dado.
    Retorna un set de nombres de condición: {"poison", "paralyze", ...}
    """

    def __init__(self, config: ConditionConfig) -> None:
        self._cfg = config
        self._templates: Dict[str, np.ndarray] = {}
        if config.detection_mode == "template":
            self._load_templates()

    def _load_templates(self) -> None:
        tdir = _TEMPLATES_DIR / "conditions"
        tdir.mkdir(parents=True, exist_ok=True)
        self._templates = {}
        for ext in ("*.png", "*.jpg", "*.bmp"):
            for path in sorted(tdir.glob(ext)):
                img = cv2.imread(str(path))
                if img is not None:
                    self._templates[path.stem] = img
        if self._templates:
            print(f"  [N] Templates cargados: {list(self._templates.keys())}")
        else:
            print(
                f"  [N] ⚠ Sin templates en {tdir} — "
                f"usando detección por color"
            )

    def _scale_roi(self, frame: np.ndarray) -> tuple[int, int, int, int]:
        h, w = frame.shape[:2]
        rx, ry = w / self._cfg.ref_width, h / self._cfg.ref_height
        x, y, rw, rh = self._cfg.condition_icons_roi
        return int(x * rx), int(y * ry), int(rw * rx), int(rh * ry)

    # ── Detección por color ───────────────────────────────────────────────────

    def _detect_color(self, frame: np.ndarray) -> Set[str]:
        rx, ry, rw, rh = self._scale_roi(frame)
        roi = frame[ry : ry + rh, rx : rx + rw]
        if roi.size == 0:
            return set()

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        found: Set[str] = set()

        for cond, (hmin, hmax, smin, smax, vmin, vmax) in _HSV_RANGES.items():
            lo = np.array([hmin, smin, vmin], dtype=np.uint8)
            hi = np.array([hmax, smax, vmax], dtype=np.uint8)
            mask = cv2.inRange(hsv, lo, hi)

            # Para rojo (bleeding/burning) que cruza los 180°:  hmin puede ser 0
            # y también puede aparecer en 170–180.
            if hmin == 0 and cond in ("bleeding", "burning"):
                lo2 = np.array([170, smin, vmin], dtype=np.uint8)
                hi2 = np.array([180, smax, vmax], dtype=np.uint8)
                mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo2, hi2))

            count = int(np.count_nonzero(mask))
            if count >= _MIN_PIXELS.get(cond, 5):
                found.add(cond)

        return found

    # ── Detección por template ────────────────────────────────────────────────

    def _detect_template(self, frame: np.ndarray) -> Set[str]:
        if not self._templates:
            return self._detect_color(frame)  # fallback

        rx, ry, rw, rh = self._scale_roi(frame)
        roi = frame[ry : ry + rh, rx : rx + rw]
        if roi.size == 0:
            return set()

        found: Set[str] = set()
        for cond, tmpl in self._templates.items():
            if (
                tmpl.shape[0] > roi.shape[0]
                or tmpl.shape[1] > roi.shape[1]
            ):
                continue
            result = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val >= self._cfg.confidence:
                found.add(cond)
        return found

    # ── API pública ───────────────────────────────────────────────────────────

    def detect(self, frame: np.ndarray) -> Set[str]:
        """
        Detecta las condiciones activas en el frame dado.
        Retorna un set de strings: {"poison", "paralyze", ...}
        """
        if self._cfg.detection_mode == "template":
            return self._detect_template(frame)
        return self._detect_color(frame)

    def debug_save(
        self, frame: np.ndarray, path: str = "debug_conditions.png"
    ) -> None:
        """Guarda el ROI de condiciones con anotaciones (diagnóstico)."""
        import sys
        if getattr(sys, 'frozen', False):
            return
        rx, ry, rw, rh = self._scale_roi(frame)
        dbg = frame.copy()
        cv2.rectangle(dbg, (rx, ry), (rx + rw, ry + rh), (0, 255, 200), 2)
        conditions = self.detect(frame)
        label = " | ".join(sorted(conditions)) if conditions else "ninguna"
        cv2.putText(
            dbg, f"Cond: {label}",
            (rx, ry - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1,
        )
        cv2.imwrite(path, dbg)


# ---------------------------------------------------------------------------
class ConditionMonitor:
    """
    Hilo que verifica condiciones activas y ejecuta las reacciones configuradas.

    Cada reacción especifica:
      - qué condición vigilar
      - qué hotkey pulsar cuando se detecte
      - cooldown entre usos (para no pulsar infinitamente)

    Uso mínimo:
        mon = ConditionMonitor(ctrl)
        mon.set_frame_getter(lambda: detector.get_raw_frame())
        # Reacción ante veneno: F2 como antídoto con cooldown de 3 segundos
        mon.add_reaction("poison",   vk=0x71, cooldown=3.0, label="antídoto F2")
        mon.add_reaction("paralyze", vk=0x72, cooldown=4.0, label="utani hur F3")
        mon.start()
    """

    def __init__(
        self,
        ctrl: Any,
        config: Optional[ConditionConfig] = None,
    ) -> None:
        self._ctrl    = ctrl
        self._cfg     = config or ConditionConfig.load()
        self._det     = ConditionDetector(self._cfg)

        # Construir tabla de reacciones desde la config
        self._reactions: Dict[str, ConditionReaction] = {}
        for r in self._cfg.reactions:
            cond = r.get("condition", "")
            if cond:
                self._reactions[cond] = ConditionReaction(
                    condition=cond,
                    vk=int(r.get("vk", 0)),
                    cooldown=float(r.get("cooldown", 2.5)),
                    label=r.get("label", cond),
                )

        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._paused   = False

        # Cooldowns: condition → timestamp del último uso
        self._last_used: Dict[str, float] = {}

        # Estadísticas
        self._reaction_counts: Dict[str, int] = {}
        # Set de condiciones activas en este momento
        self._active_conditions: Set[str] = set()
        # Lock protecting _active_conditions, _last_used, _reaction_counts
        self._lock = threading.Lock()

        # Optional callbacks: fired when a condition is newly detected or clears.
        # Signature: (condition_name: str) -> None
        self.on_condition:       Optional[Callable[[str], None]] = None
        self.on_condition_clear: Optional[Callable[[str], None]] = None

        # Log callback (None → falls back to print)
        self._log_cb: Optional[Callable[[str], None]] = None

    # ── Configuración dinámica ────────────────────────────────────────────────

    def set_frame_getter(
        self, fn: Callable[[], Optional[np.ndarray]]
    ) -> None:
        self._frame_getter = fn

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        """Register a callback for log messages (replaces direct print output)."""
        self._log_cb = cb

    def _log(self, msg: str) -> None:
        if self._log_cb:
            self._log_cb(msg)
        else:
            print(msg)

    def add_reaction(
        self,
        condition: str,
        vk: int = 0,
        cooldown: float = 2.5,
        label: str = "",
    ) -> None:
        """Añade o sobreescribe una reacción en tiempo de ejecución."""
        if not (0 <= vk <= 0xFF):
            raise ValueError(
                f"ConditionMonitor.add_reaction: vk={vk:#x} must be in [0x00, 0xFF]"
            )
        with self._lock:
            self._reactions[condition] = ConditionReaction(
                condition=condition,
                vk=vk,
                cooldown=cooldown,
                label=label or condition,
            )

    def remove_reaction(self, condition: str) -> bool:
        """
        Remove the reaction registered for *condition*.

        Returns True if the reaction existed (and was removed), False if
        there was no reaction for that condition.
        """
        with self._lock:
            if condition in self._reactions:
                del self._reactions[condition]
                return True
        return False

    def list_reactions(self) -> List[str]:
        """Return a sorted list of condition names that have registered reactions."""
        with self._lock:
            return sorted(self._reactions.keys())

    def reset_reaction_counts(self) -> None:
        """Zero all per-condition reaction counters."""
        with self._lock:
            self._reaction_counts.clear()

    @property
    def reaction_counts(self) -> Dict[str, int]:
        """Read-only snapshot of per-condition reaction counts."""
        with self._lock:
            return dict(self._reaction_counts)

    @property
    def is_running(self) -> bool:
        """True while the monitor thread is active."""
        return self._running

    @property
    def is_paused(self) -> bool:
        """True while the monitor loop is paused."""
        return self._paused

    @property
    def active_count(self) -> int:
        """Number of conditions currently active (detected on last frame)."""
        with self._lock:
            return len(self._active_conditions)

    @property
    def total_reactions_fired(self) -> int:
        """Sum of all per-condition reaction counters."""
        with self._lock:
            return sum(self._reaction_counts.values())

    def stats_snapshot(self) -> dict[str, Any]:
        """
        Return a lightweight dict snapshot of monitor state.

        Keys: ``active_conditions``, ``reaction_counts``, ``total_reactions``,
        ``is_running``, ``is_paused``, ``reactions_registered``.
        """
        with self._lock:
            return {
                "active_conditions":   sorted(self._active_conditions),
                "reaction_counts":     dict(self._reaction_counts),
                "total_reactions":     sum(self._reaction_counts.values()),
                "is_running":          self._running,
                "is_paused":           self._paused,
                "reactions_registered": len(self._reactions),
            }

    @property
    def has_frame_getter(self) -> bool:
        """True when a frame-getter callback has been registered."""
        return self._frame_getter is not None

    @property
    def reaction_count(self) -> int:
        """Number of conditions that have a registered reaction callback."""
        with self._lock:
            return len(self._reactions)

    @property
    def has_reactions(self) -> bool:
        """True when at least one reaction callback has been registered."""
        with self._lock:
            return len(self._reactions) > 0

    @property
    def has_fired(self) -> bool:
        """True when at least one reaction has been triggered this session."""
        return self.total_reactions_fired > 0

    @property
    def is_active(self) -> bool:
        """True when at least one condition is currently detected."""
        return self.active_count > 0

    @staticmethod
    def condition_names() -> List[str]:
        """Return a sorted list of all condition names recognisable by the color detector."""
        return sorted(_HSV_RANGES.keys())

    def update_config(self, config: ConditionConfig) -> None:
        """
        Hot-swap the monitor configuration.

        Rebuilds the detector with the new config. Existing reactions
        registered via ``add_reaction`` are preserved; reactions defined
        inside the new config are merged in (config reactions take precedence
        for any condition they define).
        """
        self._cfg = config
        self._det = ConditionDetector(config)
        # Merge reactions from new config (overwrite existing for those conditions)
        with self._lock:
            for r in config.reactions:
                cond = r.get("condition", "")
                if cond:
                    self._reactions[cond] = ConditionReaction(
                        condition=cond,
                        vk=int(r.get("vk", 0)),
                        cooldown=float(r.get("cooldown", 2.5)),
                        label=r.get("label", cond),
                    )

    # ── Control ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("  [N] ✓ Monitor de condiciones iniciado")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        with self._lock:
            if self._reaction_counts:
                self._log(f"  [N] Reacciones: {self._reaction_counts}")
        self._log("  [N] Detenido")

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # ── Estado público ───────────────────────────────────────────────────────

    @property
    def active_conditions(self) -> Set[str]:
        with self._lock:
            return set(self._active_conditions)

    def has_condition(self, condition: str) -> bool:
        with self._lock:
            return condition in self._active_conditions

    # ── Loop principal ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        self._log("  [N] Loop activo — vigilando condiciones…")

        while self._running:
            if self._paused:
                time.sleep(random.uniform(0.08, 0.15))
                continue

            if self._frame_getter is None:
                time.sleep(random.uniform(0.35, 0.65))
                continue

            frame = self._frame_getter()
            if frame is None:
                time.sleep(self._cfg.check_interval * random.uniform(0.8, 1.25))
                continue

            # Detectar condiciones activas
            conditions = self._det.detect(frame)
            with self._lock:
                prev = set(self._active_conditions)
                self._active_conditions = conditions

            # Log cambios
            new_conds  = conditions - prev
            gone_conds = prev - conditions
            for c in new_conds:
                self._log(f"  [N] ⚠ {c.upper()} detectado")
                if self.on_condition is not None:
                    try:
                        self.on_condition(c)
                    except Exception as exc:
                        self._log(f"  [N] on_condition callback error: {exc}")
            for c in gone_conds:
                self._log(f"  [N] ✓ {c} curado / expirado")
                if self.on_condition_clear is not None:
                    try:
                        self.on_condition_clear(c)
                    except Exception as exc:
                        self._log(f"  [N] on_condition_clear callback error: {exc}")

            # Ejecutar reacciones
            now = time.monotonic()
            with self._lock:
                reactions_snapshot = dict(self._reactions)
            for cond in conditions:
                reaction = reactions_snapshot.get(cond)
                if reaction is None or reaction.vk == 0:
                    continue
                with self._lock:
                    last = self._last_used.get(cond, 0.0)
                    if now - last < reaction.cooldown:
                        continue
                if self._ctrl.press_key(reaction.vk):
                    with self._lock:
                        self._last_used[cond] = now
                        self._reaction_counts[cond] = (
                            self._reaction_counts.get(cond, 0) + 1
                        )
                        count = self._reaction_counts[cond]
                    self._log(
                        f"  [N] 💊 {reaction.label} usado "
                        f"(n={count})"
                    )

            time.sleep(self._cfg.check_interval * random.uniform(0.8, 1.25))

        self._log("  [N] Loop terminado")
