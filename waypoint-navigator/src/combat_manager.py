"""
CombatManager
-------------
Detecta monstruos en la Battle List de Tibia mediante template matching
sobre frames capturados por OBS, y envía ataques automáticamente.

Arquitectura:
  - CombatConfig    : ROIs, rutas de templates, hotkeys, umbrales
  - BattleDetector  : template matching sobre el panel de Battle List
  - CombatManager   : hilo de ataque + flee por HP bajo

Setup de templates:
  Añade capturas de pantalla de iconos de monstruo (battle list) en:
    cache/templates/monsters/
  Nombre del fichero = cualquier .png / .jpg (ej: troll.png, goblin.png)
  Cómo obtener templates:
    1. Ejecuta: python examples/diag_hpmp.py --source obs-ws
    2. Guarda una captura con un monstruo en la battle list
    3. Recorta el icono pequeño (~24x24 px) del slot de la battle list
    4. Guárdalo en cache/templates/monsters/

Uso desde auto_walker:
    cm = CombatManager(ctrl, hp_detector)
    cm.set_frame_getter(lambda: motion_detector.get_raw_frame())
    cm.start()
    # ... loop de walk ...
    cm.stop()
"""

from __future__ import annotations

import json
import logging
import random
import time
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import re

from .humanizer import jittered_sleep

_log = logging.getLogger("wn.cm")

import cv2
import numpy as np

from .combat_manager_helpers import (
    cast_spells as helper_cast_spells,
    check_anti_lure as helper_check_anti_lure,
    read_hp_pct as helper_read_hp_pct,
    sort_by_priority as helper_sort_by_priority,
)
from .combat_manager_loop import run_loop as runtime_run_loop

try:
    from .action_verifier import verify_target_selected
except ImportError:  # pragma: no cover
    verify_target_selected = None  # type: ignore

try:
    from .event_bus import EventBus
except ImportError:  # pragma: no cover
    EventBus = None  # type: ignore

# ---------------------------------------------------------------------------
from src.config_paths import COMBAT_CONFIG, TEMPLATES_DIR as _TEMPLATES_DIR

COMBAT_CONFIG_FILE = COMBAT_CONFIG

# Common sidebar / UI labels that EasyOCR picks up from the battle-list
# area.  These are NOT monster names and must be ignored.
_OCR_UI_BLACKLIST: frozenset[str] = frozenset({
    "battle", "battle list", "battlelist", "stop", "prey",
    "loot", "quest", "vip", "skills", "unjustified",
    "sheep", "black sheep", "shop", "depot",
})


# ---------------------------------------------------------------------------
@dataclass
class CombatConfig:
    """
    Configuración persistente del motor de combate.

    battle_list_roi : [x, y, w, h]
        Posición del panel Battle List en el frame OBS a 1920×1080.
        Se escala si el frame tiene otra resolución.
        Valor por defecto: panel derecho estándar de Tibia 13+ en 1920×1080.

    attack_vk : int
        Código VK de la tecla de ataque.  0 = solo hacer clic en el target
        (Tibia inicia el auto-ataque al seleccionar en la battle list).

    spells : list[dict]
        Lista de hechizos a lanzar en combate.
        Cada entrada: {"vk": 0x71, "min_mp": 60, "cooldown": 2.0, "label": "exori"}
        Se lanza si MP >= min_mp y han pasado cooldown segundos desde el último uso.

    hp_flee_pct : int
        Huir (activar flee_vk) cuando HP% cae por debajo de este valor.  0 = desactivado.
    """

    # ROI de la battle list [x, y, w, h] en píxeles para 1920×1080
    battle_list_roi: List[int] = field(default_factory=lambda: [1699, 480, 210, 400])
    # Directorio de templates (subcarpeta monsters/)
    templates_dir: str = str(_TEMPLATES_DIR)
    # VK de ataque; 0 = solo clic en target
    attack_vk: int = 0
    # Hechizos a lanzar mientras se combate
    spells: List[Dict[str, Any]] = field(default_factory=list)
    # HP% para activar huida (0 = desactivado)
    hp_flee_pct: int = 0
    # VK del hechizo/item de huida (ej. utani hur = speed)
    flee_vk: int = 0
    # Intervalo de verificación (segundos)
    check_interval: float = 0.35
    # Confianza mínima de template matching (0–1)
    confidence: float = 0.65
    # Pausa entre clic en target y envío de hotkey de ataque (s)
    click_to_attack_delay: float = 0.12
    # Intervalo de reselección del mismo target (s); reclick if unchanged for this long
    reselect_interval: float = 3.0
    # Ignorar los N primeros slots de la battle list (NPC, etc.)
    skip_top: int = 0
    # Altura de cada slot en la battle list (px, referencia 1920×1080)
    slot_height: int = 22
    # Resolución de referencia del frame OBS
    ref_width: int = 1920
    ref_height: int = 1080
    # OCR-based detection: use EasyOCR to read monster names when no templates exist
    ocr_detection: bool = False
    # Minimum OCR text confidence (0–1)
    ocr_confidence: float = 0.3

    # ── Fase 5: Combat hardening ──────────────────────────────────────────
    # Monster priority: ordered list ["Wasp", "Bug", "Poison Spider"]
    # Lower index = higher priority.  Unknown monsters get lowest priority.
    monster_priority: List[str] = field(default_factory=list)
    # AoE threshold: use AoE spells when this many+ mobs are in battle list
    aoe_mob_threshold: int = 2
    # Flee mob count: flee also when HP < hp_flee_pct AND mobs >= this (0 = HP-only)
    flee_mob_count: int = 0
    # Anti-lure: max expected monsters in spawn.  If more, emit warning / flee.
    max_expected_mobs: int = 0
    # Anti-lure: action when lure detected ("warn", "flee", "ignore")
    lure_action: str = "warn"
    # Template filter: if non-empty, only load templates whose filename stem
    # (case-insensitive, spaces→underscores) matches an entry in this list.
    # Reduces detect() from O(all_templates) to O(spawn_monsters).
    # Example: ["rat", "cave_rat", "spider"] loads only those 3 templates.
    # Empty list = load all templates (default, backwards-compatible).
    monster_filter: List[str] = field(default_factory=list)

    def validate(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"CombatConfig.confidence={self.confidence} must be in [0.0, 1.0]")
        if not (0.0 <= self.ocr_confidence <= 1.0):
            raise ValueError(f"CombatConfig.ocr_confidence={self.ocr_confidence} must be in [0.0, 1.0]")
        if not (0 <= self.hp_flee_pct <= 100):
            raise ValueError(f"CombatConfig.hp_flee_pct={self.hp_flee_pct} must be in [0, 100]")
        if len(self.battle_list_roi) != 4:
            raise ValueError(
                f"CombatConfig.battle_list_roi must have 4 elements, got {len(self.battle_list_roi)}")
        if any(v < 0 for v in self.battle_list_roi):
            raise ValueError(f"CombatConfig.battle_list_roi values must be non-negative: {self.battle_list_roi}")
        if self.check_interval < 0:
            raise ValueError(f"CombatConfig.check_interval={self.check_interval} must be >= 0")
        if self.skip_top < 0:
            raise ValueError(f"CombatConfig.skip_top={self.skip_top} must be >= 0")
        if self.slot_height <= 0:
            raise ValueError(f"CombatConfig.slot_height={self.slot_height} must be > 0")
        if self.ref_width <= 0:
            raise ValueError(f"CombatConfig.ref_width={self.ref_width} must be > 0")
        if self.ref_height <= 0:
            raise ValueError(f"CombatConfig.ref_height={self.ref_height} must be > 0")
        if self.aoe_mob_threshold < 1:
            raise ValueError(f"CombatConfig.aoe_mob_threshold={self.aoe_mob_threshold} must be >= 1")
        if self.flee_mob_count < 0:
            raise ValueError(f"CombatConfig.flee_mob_count={self.flee_mob_count} must be >= 0")
        if self.max_expected_mobs < 0:
            raise ValueError(f"CombatConfig.max_expected_mobs={self.max_expected_mobs} must be >= 0")
        if self.lure_action not in ("warn", "flee", "ignore"):
            raise ValueError(
                f"CombatConfig.lure_action={self.lure_action!r} must be warn/flee/ignore"
            )
        # VK code range validation (0x00-0xFF or 0 for disabled)
        for vk_field in ("attack_vk", "flee_vk"):
            vk = getattr(self, vk_field)
            if not (0 <= vk <= 0xFF):
                raise ValueError(f"CombatConfig.{vk_field}={vk:#x} must be in [0x00, 0xFF]")
        for i, spell in enumerate(self.spells):
            vk = spell.get("vk", 0)
            if not (0 <= vk <= 0xFF):
                raise ValueError(f"CombatConfig.spells[{i}].vk={vk:#x} must be in [0x00, 0xFF]")

    def save(self, path: Path = COMBAT_CONFIG_FILE) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2)

    @classmethod
    def load(cls, path: Path = COMBAT_CONFIG_FILE) -> "CombatConfig":
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        obj = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        obj.validate()
        return obj


# ---------------------------------------------------------------------------
class BattleDetector:
    """
    Detecta monstruos en el panel de Battle List mediante template matching.

    Devuelve lista de (frame_x, frame_y, confidence, name) donde
    (frame_x, frame_y) es el centro del icono del monstruo en el frame completo
    (coordenadas directamente usables para InputController.click).
    """

    def __init__(self, config: CombatConfig) -> None:
        self._cfg = config
        self._templates: List[Tuple[str, np.ndarray]] = []
        self._ocr_reader: Optional[Any] = None  # cached EasyOCR reader (lazy-init)
        if not self._cfg.ocr_detection:
            self._load_templates()

    def _load_templates(self) -> None:
        """Carga imágenes de templates desde templates_dir/monsters/.

        Si ``monster_filter`` está configurado, solo carga templates cuyos
        nombres (stem, case-insensitive, espacios→guión_bajo) estén en la lista.
        Esto reduce detect() de O(280 templates) a O(spawn monsters).
        """
        tdir = Path(self._cfg.templates_dir) / "monsters"
        tdir.mkdir(parents=True, exist_ok=True)
        # Normalise filter entries: lowercase, spaces to underscores
        _filter: set[str] = {
            n.lower().replace(" ", "_")
            for n in (self._cfg.monster_filter or [])
        }
        self._templates = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
            for path in sorted(tdir.glob(ext)):
                if _filter and path.stem.lower() not in _filter:
                    continue
                img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    self._templates.append((path.stem, img))
        n_loaded = len(self._templates)
        names = [n for n, _ in self._templates]
        if n_loaded:
            _filter_note = f" (filter: {sorted(_filter)})" if _filter else " (all)"
            _log.info("[C] Templates cargados: %d%s — %s", n_loaded, _filter_note, names)
        else:
            _log.warning("[C] Sin templates en %s — Añade recortes de iconos de la battle list.", tdir)

    def reload(self) -> None:
        """Recarga templates desde disco (útil al añadir nuevos sin reiniciar)."""
        self._load_templates()

    @property
    def template_count(self) -> int:
        """Number of monster templates currently loaded."""
        return len(self._templates)

    @property
    def has_templates(self) -> bool:
        """True when at least one monster template image is loaded."""
        return bool(self._templates)

    def _scale_roi(self, frame: np.ndarray) -> Tuple[int, int, int, int]:
        """Escala el ROI de la battle list a la resolución real del frame."""
        h, w = frame.shape[:2]
        rx = w / self._cfg.ref_width
        ry = h / self._cfg.ref_height
        x, y, rw, rh = self._cfg.battle_list_roi
        return int(x * rx), int(y * ry), int(rw * rx), int(rh * ry)

    def detect(self, frame: np.ndarray) -> List[Tuple[int, int, float, str]]:
        """
        Detecta monstruos en la battle list.

        Retorna lista de (frame_x, frame_y, confidence, name) ordenada
        por posición vertical (primero el slot más alto = mayor prioridad).
        Ya aplica skip_top.
        """
        if not self._templates or frame is None:
            return []

        rx, ry, rw, rh = self._scale_roi(frame)
        roi = frame[ry : ry + rh, rx : rx + rw]
        if roi.size == 0:
            return []

        gray_roi = (
            cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi.copy()
        )

        detections: List[Tuple[int, int, float, str]] = []
        seen_y: List[int] = []  # deduplicar slots ya detectados

        for name, tmpl in self._templates:
            if (
                tmpl.shape[0] > gray_roi.shape[0]
                or tmpl.shape[1] > gray_roi.shape[1]
            ):
                continue
            result = cv2.matchTemplate(gray_roi, tmpl, cv2.TM_CCOEFF_NORMED)
            locs = np.where(result >= self._cfg.confidence)
            for pt_y, pt_x in zip(*locs):
                cx = rx + int(pt_x + tmpl.shape[1] / 2)
                cy = ry + int(pt_y + tmpl.shape[0] / 2)
                # Deduplicar: mismo slot = misma franja vertical
                half_slot = self._cfg.slot_height // 2
                if any(abs(cy - sy) < half_slot for sy in seen_y):
                    continue
                conf = float(result[pt_y, pt_x])
                detections.append((cx, cy, conf, name))
                seen_y.append(cy)

        detections.sort(key=lambda d: d[1])  # primero el slot más alto
        return detections[self._cfg.skip_top :]

    def detect_ocr(
        self, frame: np.ndarray
    ) -> List[Tuple[int, int, float, str]]:
        """
        OCR-based monster detection — no templates required.

        Reads the battle list ROI with EasyOCR and returns one detection
        per non-empty text slot.  Slot positions are estimated from the
        ``slot_height`` config value.

        Returns the same tuple format as :meth:`detect`:
        ``(frame_x, frame_y, confidence, name)``.
        """
        if frame is None:
            return []

        if self._ocr_reader is None:
            try:
                import easyocr
                self._ocr_reader = easyocr.Reader(["en"], verbose=False)
            except ImportError:
                return []

        rx, ry, rw, rh = self._scale_roi(frame)
        roi = frame[ry : ry + rh, rx : rx + rw]
        if roi.size == 0:
            return []

        try:
            reader = self._ocr_reader
            results = reader.readtext(roi, detail=1)
        except Exception as exc:
            _log.warning("OCR read failed: %s", exc)
            return []

        roi_h, roi_w = roi.shape[:2]
        detections: List[Tuple[int, int, float, str]] = []
        slot_h = self._cfg.slot_height
        for bbox, text, conf in results:
            text = text.strip()
            if not text or conf < self._cfg.ocr_confidence:
                continue
            # Compute center of the bounding box in ROI-local coordinates
            ys = [pt[1] for pt in bbox]
            xs = [pt[0] for pt in bbox]
            if not ys or not xs:
                continue
            local_cx = int(sum(xs) / len(xs))
            local_cy = int(sum(ys) / len(ys))
            # Reject if local centre falls outside the actual ROI image —
            # EasyOCR can return bboxes that extend beyond the input image.
            if local_cx < 0 or local_cx >= roi_w or local_cy < 0 or local_cy >= roi_h:
                _log.debug(
                    "OCR reject (out-of-ROI): '%s' local=(%d,%d) roi=%dx%d",
                    text, local_cx, local_cy, roi_w, roi_h,
                )
                continue
            # Convert to frame coordinates
            cx = rx + local_cx
            cy = ry + local_cy
            # Estimate slot index and skip top slots
            slot_idx = int((cy - ry) / max(slot_h, 1))
            if slot_idx < self._cfg.skip_top:
                continue
            # Normalise name
            name = re.sub(r"[^a-zA-Z0-9 ']", "", text).strip()
            if not name or len(name) < 3:
                continue
            # Skip known UI labels that OCR picks up from the sidebar
            if name.lower() in _OCR_UI_BLACKLIST:
                continue
            detections.append((cx, cy, float(conf), name))

        detections.sort(key=lambda d: d[1])
        return detections

    def detect_auto(
        self, frame: np.ndarray
    ) -> List[Tuple[int, int, float, str]]:
        """
        Auto-select detection method.

        When ``ocr_detection`` is True, **always** uses :meth:`detect_ocr`
        (OCR is authoritative — templates are 32×32 sprites that do not
        match the ~16 px battle-list icons in live).

        When ``ocr_detection`` is False, uses :meth:`detect` (template
        matching) if templates are loaded, otherwise returns ``[]``.
        """
        if self._cfg.ocr_detection:
            return self.detect_ocr(frame)
        if self.has_templates:
            return self.detect(frame)
        return []

    def debug_save(self, frame: np.ndarray, path: str = "debug_battle_list.png") -> None:
        """Guarda el ROI de la battle list con detecciones marcadas (diagnóstico)."""
        import sys
        if getattr(sys, 'frozen', False):
            return
        rx, ry, rw, rh = self._scale_roi(frame)
        dbg = frame.copy()
        cv2.rectangle(dbg, (rx, ry), (rx + rw, ry + rh), (0, 255, 100), 2)
        for cx, cy, conf, name in self.detect_auto(frame):
            cv2.circle(dbg, (cx, cy), 10, (0, 100, 255), 2)
            cv2.putText(
                dbg, f"{name}:{conf:.2f}", (cx + 12, cy),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 255), 1,
            )
        cv2.imwrite(path, dbg)
        _log.info("[C] Debug guardado en %s", path)


# ---------------------------------------------------------------------------
@dataclass
class TrackedCombatTarget:
    """Metadata for the monster currently considered the active target."""

    name: str
    position: Tuple[int, int]
    acquired_at: float
    last_seen_at: float

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "position": self.position,
            "acquired_at": self.acquired_at,
            "last_seen_at": self.last_seen_at,
        }


# ---------------------------------------------------------------------------
class CombatManager:
    """
    Hilo de combate automático.

    Loop:
    1. Obtiene frame de OBS
    2. Verifica HP%; si < hp_flee_pct → flee
    3. Detecta monstruos en la battle list via template matching
    4. Hace clic en el primer monstruo para seleccionarlo como target
    5. Envía hotkey de ataque (si attack_vk != 0)
    6. Lanza hechizos configurados según MP disponible

    Propiedades de estado:
    - is_in_combat : bool       → hay un target activo
    - kills        : int        → kills confirmados externamente vía notify_kill()
    - last_hp_pct  : int|None   → último HP% leído
    - current_target_name : str|None → nombre del target actualmente seguido
    """

    _GLOBAL_SPELL_CD: float = 1.0  # min gap between any two spell casts
    _ENGAGE_CONFIRM_FRAMES: int = 2  # consecutive detection frames before first attack

    def __init__(
        self,
        ctrl: Any,                           # InputController
        hp_detector: Optional[Any] = None,   # HpMpDetector (opcional, para flee y spells por MP)
        config: Optional[CombatConfig] = None,
        verify_attacks: bool = False,
        event_bus: Optional[Any] = None,     # EventBus (optional, for emitting events)
    ) -> None:
        self._ctrl     = ctrl
        self._hp       = hp_detector
        self._cfg      = config or CombatConfig.load()
        self._detector = BattleDetector(self._cfg)
        self._bus: Optional[Any] = event_bus

        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._paused   = False
        self._lock = threading.Lock()  # protects _kills, _cached_mp_pct, _in_combat, _current_target, _tracked_target, _last_target_result, _attacks_sent, _last_hp_pct, _last_target_time

        # ── Verification ──────────────────────────────────────────────────
        self._verify_attacks = verify_attacks
        self._target_verify_fails: int = 0

        # Estado
        self._current_target: Optional[Tuple[int, int]] = None
        self._tracked_target: Optional[TrackedCombatTarget] = None
        self._last_target_result: Optional[Dict[str, Any]] = None
        self._last_target_time: float = 0.0
        self._in_combat: bool = False
        self._kills: int = 0
        self._attacks_sent: int = 0
        self._last_hp_pct: Optional[int] = None
        self._cached_mp_pct: Optional[int] = None  # M2-fix: explicit init

        # ── Fase 5: per-monster tracking ──────────────────────────────────
        # Names visible in the most recent frame (kept for snapshots/tests).
        self._prev_detection_names: List[str] = []
        # Highest confirmed visible count per monster name while in combat.
        self._tracked_detection_counts: Dict[str, int] = {}
        # Anti-lure warning counter
        self._lure_warnings: int = 0
        # ── Kill confirmation: require N consecutive absent frames ─────────
        # Track how many consecutive frames each monster has been absent.
        # A monster is confirmed dead only after absence_frames_required
        # consecutive frames without it.  Prevents false kills when a
        # monster walks off-screen for 1 frame.
        self._absence_counter: Dict[Tuple[str, int], int] = {}   # (name, slot) → consecutive absent frames
        self._absence_frames_required: int = 3        # need 3 frames to confirm kill
        # Same for "all clear" — battle list empty
        self._empty_frames_streak: int = 0
        # ── Engagement confirmation: consecutive detection frames before first attack
        self._confirm_streak: int = 0

        # Cooldowns de hechizos: vk → timestamp del último lanzamiento
        self._spell_cds: Dict[int, float] = {}
        # Global spell cooldown: only one spell per tick
        self._last_any_spell: float = 0.0
        self._last_attack_vk_time: float = 0.0
        # Log callback (None → print)
        self._log_cb: Optional[Callable[[str], None]] = None
        # on_kill callback: called each time a kill is confirmed (no args)
        self.on_kill: Optional[Callable[[], None]] = None

    # ── Configuración ────────────────────────────────────────────────────────

    def set_frame_getter(
        self, fn: Callable[[], Optional[np.ndarray]]
    ) -> None:
        """
        Registra la función que devuelve el frame OBS actual (numpy BGR).
        Ejemplo: cm.set_frame_getter(lambda: motion_detector.get_raw_frame())
        """
        self._frame_getter = fn

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        """
        Register a callback for log messages.

        When set, ``_log()`` will call *cb* instead of ``print``.
        Useful for routing combat log lines to a GUI or file.
        """
        self._log_cb = cb

    def _log(self, msg: str) -> None:
        """Internal log helper — routes through the registered callback or print."""
        if self._log_cb is not None:
            self._log_cb(msg)
        else:
            print(msg)

    def _emit(self, event: str, data: Any = None) -> None:
        """Emit an event through the EventBus if available."""
        if self._bus is not None:
            try:
                self._bus.emit(event, data)
            except Exception as exc:
                self._log(f"  [C] event bus emit({event}) failed: {exc}")

    # ── Control ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Inicia el loop de combate en un hilo de fondo."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("  [C] ✓ Hilo de combate iniciado")

    def stop(self) -> None:
        """Detiene el loop de combate."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        with self._lock:
            k, a = self._kills, self._attacks_sent
        self._log(
            f"  [C] Detenido — "
            f"kills={k} attacks={a}"
        )

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    @property
    def is_paused(self) -> bool:
        """True while the combat loop is paused."""
        return self._paused

    @property
    def is_running(self) -> bool:
        """True while the combat loop thread is active."""
        return self._running

    # ── Notificaciones externas ───────────────────────────────────────────────

    def notify_kill(self, name: str = "") -> None:
        """
        Llamar desde el walker cuando se confirma que un monstruo murió.
        Incrementa el contador, resetea el target activo y dispara ``on_kill``.
        """
        with self._lock:
            tracked_target = self._tracked_target
            current_target = self._current_target
            self._kills += 1
            self._in_combat = False
            self._current_target = None
            self._tracked_target = None
            target_name = name or (tracked_target.name if tracked_target is not None else "")
            if tracked_target is not None or current_target is not None or target_name:
                self._last_target_result = self._build_target_result(
                    tracked_target=tracked_target,
                    name=target_name,
                    reason="external_notify",
                    position=current_target,
                )
        self._emit("e1", {"name": name})
        if self.on_kill is not None:
            try:
                self.on_kill()
            except Exception as exc:
                self._log(f"  [C] on_kill callback failed: {exc}")

    # ── Estado público ───────────────────────────────────────────────────────

    @property
    def is_in_combat(self) -> bool:
        with self._lock:
            return self._in_combat

    @property
    def kills(self) -> int:
        with self._lock:
            return self._kills

    @property
    def attacks_sent(self) -> int:
        """Total number of attack hotkey presses sent this session."""
        with self._lock:
            return self._attacks_sent

    @property
    def has_kills(self) -> bool:
        """True when at least one monster has been killed this session."""
        with self._lock:
            return self._kills > 0

    @property
    def has_attacked(self) -> bool:
        """True when at least one attack has been sent this session."""
        with self._lock:
            return self._attacks_sent > 0

    @property
    def current_target_name(self) -> Optional[str]:
        with self._lock:
            if self._tracked_target is None:
                return None
            return self._tracked_target.name

    @property
    def last_hp_pct(self) -> Optional[int]:
        with self._lock:
            return self._last_hp_pct

    @property
    def has_spells(self) -> bool:
        """True when at least one spell is configured."""
        return self.spells_count > 0

    @property
    def has_last_hp(self) -> bool:
        """True when an HP reading has been recorded."""
        with self._lock:
            return self._last_hp_pct is not None

    @property
    def target_verify_fails(self) -> int:
        """Times a click was sent but the target wasn't confirmed selected."""
        with self._lock:
            return self._target_verify_fails

    @property
    def last_target_result(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._last_target_result is None:
                return None
            return dict(self._last_target_result)

    def reset_kills(self) -> None:
        """Reset kill and attack counters back to zero."""
        with self._lock:
            self._kills = 0
            self._attacks_sent = 0

    def update_config(self, config: CombatConfig) -> None:
        """
        Hot-swap combat configuration without restarting the thread.
        The detector is rebuilt with the new config.
        """
        self._cfg = config
        self._detector = BattleDetector(config)
        self._log("  [C] ↺ Configuración actualizada")

    def reset_spell_cooldowns(self) -> None:
        """Clear all spell cooldown timestamps so spells can fire immediately."""
        self._spell_cds.clear()

    # ── Spell management ─────────────────────────────────────────────────────

    @property
    def spells_count(self) -> int:
        """Number of spells currently configured."""
        return len(self._cfg.spells)

    def add_spell(self, spell: Dict[str, Any]) -> None:
        """
        Append a spell entry to the active spell list.

        *spell* must be a dict with at least ``vk`` key
        (e.g. ``{"vk": 0x71, "min_mp": 30, "cooldown": 2.0, "label": "exura"}``).
        Change takes effect on the next loop iteration without a restart.
        """
        if "vk" not in spell:
            raise ValueError("spell dict must contain 'vk' key")
        self._cfg.spells.append(spell)

    def remove_spell(self, vk: int) -> bool:
        """
        Remove the first spell entry with the given *vk* code.

        Returns ``True`` if a spell was removed, ``False`` if not found.
        """
        before = len(self._cfg.spells)
        self._cfg.spells = [s for s in self._cfg.spells if int(s.get("vk", 0)) != vk]
        return len(self._cfg.spells) < before

    def spell_vks(self) -> List[int]:
        """Return a list of VK codes for all currently configured spells."""
        return [int(s.get("vk", 0)) for s in self._cfg.spells]

    @property
    def has_frame_getter(self) -> bool:
        """True when a frame getter callable has been registered."""
        return self._frame_getter is not None

    @property
    def has_log_callback(self) -> bool:
        """True when a log-callback has been registered via ``set_log_callback``."""
        return self._log_cb is not None

    @property
    def active_cooldown_count(self) -> int:
        """Number of spells currently on cooldown."""
        return len(self._spell_cds)

    @property
    def has_active_cooldowns(self) -> bool:
        """True when at least one spell is currently on cooldown."""
        return len(self._spell_cds) > 0

    @property
    def lure_warnings(self) -> int:
        """Number of anti-lure warnings triggered this session."""
        return self._lure_warnings

    @property
    def prev_detection_count(self) -> int:
        """Number of monsters detected in the previous frame."""
        return len(self._prev_detection_names)

    def _build_target_result(
        self,
        *,
        tracked_target: Optional[TrackedCombatTarget],
        name: str,
        reason: str,
        position: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, Any]:
        finished_at = time.monotonic()
        target_name = name or (tracked_target.name if tracked_target is not None else "")
        target_position = tracked_target.position if tracked_target is not None else position
        duration_s = 0.0
        if tracked_target is not None:
            duration_s = max(0.0, finished_at - tracked_target.acquired_at)
        return {
            "name": target_name,
            "status": "killed",
            "reason": reason,
            "position": target_position,
            "finished_at": finished_at,
            "duration_s": round(duration_s, 3),
        }

    # ── Snapshot ─────────────────────────────────────────────────────────────

    def stats_snapshot(self) -> Dict[str, Any]:
        """
        Return a lightweight dict snapshot of combat state suitable for
        logging, UI display, or session saving.

        Keys: ``kills``, ``attacks``, ``hp_pct``, ``in_combat``, ``paused``,
        ``spells``, ``active_cooldowns``.
        """
        with self._lock:
            return {
                "kills":            self._kills,
                "attacks":          self._attacks_sent,
                "hp_pct":           self._last_hp_pct,
                "in_combat":        self._in_combat,
                "paused":           self._paused,
                "spells":           len(self._cfg.spells),
                "active_cooldowns": len(self._spell_cds),
                "target_verify_fails": self._target_verify_fails,
                "lure_warnings":    self._lure_warnings,
                "mobs_visible":     len(self._prev_detection_names),
                "current_target_name": self._tracked_target.name if self._tracked_target is not None else None,
                "last_target_result": dict(self._last_target_result) if self._last_target_result is not None else None,
            }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "in_combat":      self._in_combat,
                "kills":          self._kills,
                "attacks":        self._attacks_sent,
                "hp_pct":         self._last_hp_pct,
                "current_target": self._current_target,
                "current_target_name": self._tracked_target.name if self._tracked_target is not None else None,
                "last_target_result": dict(self._last_target_result) if self._last_target_result is not None else None,
            }

    # ── Helpers privados ─────────────────────────────────────────────────────

    def _read_hp_pct(self, frame: np.ndarray) -> Optional[int]:
        return helper_read_hp_pct(self, frame)

    def _cast_spells(self, frame: np.ndarray, mob_count: int = 1) -> None:
        helper_cast_spells(
            self,
            frame,
            mob_count,
            time_module=time,
            random_module=random,
        )

    # ── Priority & AoE helpers ───────────────────────────────────────────────

    def _sort_by_priority(
        self, detections: List[Tuple[int, int, float, str]]
    ) -> List[Tuple[int, int, float, str]]:
        return helper_sort_by_priority(self, detections)

    def _check_anti_lure(
        self, detection_count: int
    ) -> bool:
        return helper_check_anti_lure(self, detection_count)

    # ── Loop principal ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        runtime_run_loop(
            self,
            jittered_sleep_fn=jittered_sleep,
            verify_target_selected_fn=verify_target_selected,
            time_module=time,
            counter_cls=Counter,
        )
