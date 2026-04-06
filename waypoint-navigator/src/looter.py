"""
Looter
------
Detecta cadáveres en el viewport de Tibia mediante template matching sobre
frames de OBS, los abre y recoge los ítems según una whitelist.

Pipeline:
  1. notify_kill(corpse_coord, player_getter) → añade el cadáver a la cola
  2. Loop: cuando el jugador está cerca del cadáver…
       a) Calcula posición en pantalla (coord-based o template matching)
       b) Right-click para abrir el menú contextual
       c) Clic en "Open" (offset configurable del menú)
       d) Espera a que se abra el contenedor
       e) Template matching de ítems en la ventana de loot
       f) Shift+click en los ítems de la whitelist
          (o "loot all" para recoger todos)

Setup de templates:
  cache/templates/corpses/   → sprites de cadáveres recortados del viewport
  cache/templates/loot_items/ → iconos de ítems recortados del contenedor

Posición del cadáver en pantalla:
  Al abrir el juego, el personaje ocupa el centro del viewport.
  Si conocemos las coordenadas del jugador y del cadáver, el píxel del cadáver es:
      px = viewport_center_x + (corpse.x - player.x) * tile_size_px
      py = viewport_center_y + (corpse.y - player.y) * tile_size_px
  Configura tile_size_px según el zoom de tu cliente (default 32).
"""

from __future__ import annotations

import json
import random
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .client_actions import quick_loot_target, select_context_menu_entry
from .humanizer import jittered_sleep
from .looter_runtime import (
    open_corpse as runtime_open_corpse,
    pick_items as runtime_pick_items,
    quick_loot_corpse as runtime_quick_loot_corpse,
    run_loop as runtime_run_loop,
    verify_loot as runtime_verify_loot,
)
from src.config_paths import TEMPLATES_DIR as _TEMPLATES_DIR
from src.ui_detection import (
    detect_context_menu,
    detect_container_window,
    find_menu_entry_offset,
    scale_offset_x,
    scale_offset_y,
)

# ---------------------------------------------------------------------------
LOOT_CONFIG_FILE = Path(__file__).parent.parent / "loot_config.json"


# ---------------------------------------------------------------------------
# Visual detection helpers (R3) — re-exported from ui_detection
# ---------------------------------------------------------------------------
__all__ = [
    "detect_context_menu",
    "detect_container_window",
    "find_menu_entry_offset",
]


# ---------------------------------------------------------------------------
@dataclass
class LootConfig:
    """Configuración del looter."""

    viewport_roi: List[int] = field(default_factory=lambda: [230, 60, 960, 540])
    tile_size_px: int = 32
    context_menu_offset_y: int = 18
    container_roi: List[int] = field(default_factory=lambda: [1610, 430, 220, 205])
    loot_whitelist: List[str] = field(default_factory=list)
    loot_mode: str = "all"
    max_range_tiles: int = 2
    loot_delay: float = 1.5
    container_settle: float = 0.6
    corpse_confidence: float = 0.6
    item_confidence: float = 0.6
    confidence: float = 0.6
    slot_brightness_threshold: float = 8.0
    container_cols: int = 4
    slot_size_px: int = 34
    ref_width: int = 1920
    ref_height: int = 1009
    quick_loot_menu_offset_y: int = 36
    use_hotkey_quick_loot: bool = True
    stow_all_menu_offset_y: int = 18
    stow_all_container_pos: List[int] = field(default_factory=list)

    @property
    def is_whitelist_mode(self) -> bool:
        return self.loot_mode == "whitelist"

    @property
    def has_whitelist(self) -> bool:
        return bool(self.loot_whitelist)

    @property
    def is_range_limited(self) -> bool:
        return self.max_range_tiles > 0

    def validate(self) -> None:
        if self.loot_mode not in ("all", "whitelist", "quick"):
            raise ValueError(f"LootConfig.loot_mode must be 'all', 'whitelist', or 'quick'")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"LootConfig.confidence must be 0-1, got {self.confidence}")
        if len(self.viewport_roi) != 4:
            raise ValueError("viewport_roi must have 4 elements [x, y, w, h]")
        if any(v < 0 for v in self.viewport_roi):
            raise ValueError("viewport_roi values must be non-negative")
        if len(self.container_roi) != 4:
            raise ValueError("container_roi must have 4 elements [x, y, w, h]")
        if any(v < 0 for v in self.container_roi):
            raise ValueError("container_roi values must be non-negative")
        if self.tile_size_px <= 0:
            raise ValueError(f"tile_size_px must be positive, got {self.tile_size_px}")
        if self.container_cols <= 0:
            raise ValueError(f"container_cols must be positive, got {self.container_cols}")
        if self.slot_size_px <= 0:
            raise ValueError(f"slot_size_px must be positive, got {self.slot_size_px}")
        if not 0.0 <= self.corpse_confidence <= 1.0:
            raise ValueError(f"corpse_confidence must be 0-1, got {self.corpse_confidence}")
        if not 0.0 <= self.item_confidence <= 1.0:
            raise ValueError(f"item_confidence must be 0-1, got {self.item_confidence}")
        if self.max_range_tiles < 0:
            raise ValueError(f"max_range_tiles must be >= 0, got {self.max_range_tiles}")
        if self.ref_width <= 0:
            raise ValueError(f"ref_width must be positive, got {self.ref_width}")
        if self.ref_height <= 0:
            raise ValueError(f"ref_height must be positive, got {self.ref_height}")

    def save(self, path: Path = LOOT_CONFIG_FILE) -> None:
        with open(path, "w", encoding="utf-8") as f:
            j = {k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()}
            json.dump(j, f, indent=2)

    @classmethod
    def load(cls, path: Path = LOOT_CONFIG_FILE) -> "LootConfig":
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        obj = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        obj.validate()
        return obj


# ---------------------------------------------------------------------------
class CorpseDetector:
    """Detecta cadáveres en el viewport mediante template matching."""

    def __init__(self, config: LootConfig) -> None:
        self._cfg = config
        self._templates: List[Tuple[str, np.ndarray]] = []
        self._load_templates()

    def _load_templates(self) -> None:
        tdir = _TEMPLATES_DIR / "corpses"
        tdir.mkdir(parents=True, exist_ok=True)
        self._templates = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
            for path in sorted(tdir.glob(ext)):
                img = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if img is not None:
                    self._templates.append((path.stem, img))
        if self._templates:
            print(f"  [L] Templates de cadáveres: {[n for n,_ in self._templates]}")
        else:
            print(f"  [L] ⚠ Sin templates en {tdir}")
            print(f"  [L]   Añade recortes de sprites de cadáver del viewport.")

    def reload(self) -> None:
        self._load_templates()

    def _scale_roi(self, frame: np.ndarray) -> Tuple[int, int, int, int]:
        h, w = frame.shape[:2]
        rx, ry = w / self._cfg.ref_width, h / self._cfg.ref_height
        x, y, rw, rh = self._cfg.viewport_roi
        return int(x * rx), int(y * ry), int(rw * rx), int(rh * ry)

    def detect(
        self, frame: np.ndarray
    ) -> List[Tuple[int, int, float, str]]:
        if not self._templates or frame is None:
            return []

        rx, ry, rw, rh = self._scale_roi(frame)
        roi = frame[ry : ry + rh, rx : rx + rw]
        if roi.size == 0:
            return []

        # Scale dedup distance by resolution
        h, w = frame.shape[:2]
        scale_x = w / self._cfg.ref_width
        scale_y = h / self._cfg.ref_height
        dedup_dx = int(20 * scale_x)
        dedup_dy = int(20 * scale_y)

        corpse_conf = self._cfg.corpse_confidence
        results: List[Tuple[int, int, float, str]] = []
        seen: List[Tuple[int, int]] = []

        for name, tmpl in self._templates:
            if (
                tmpl.shape[0] > roi.shape[0]
                or tmpl.shape[1] > roi.shape[1]
            ):
                continue
            match = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
            locs = np.where(match >= corpse_conf)
            for pt_y, pt_x in zip(*locs):
                cx = rx + int(pt_x + tmpl.shape[1] / 2)
                cy = ry + int(pt_y + tmpl.shape[0] / 2)
                # Deduplicar con distancia escalada
                if any(
                    abs(cx - sx) < dedup_dx and abs(cy - sy) < dedup_dy
                    for sx, sy in seen
                ):
                    continue
                conf = float(match[pt_y, pt_x])
                results.append((cx, cy, conf, name))
                seen.append((cx, cy))

        return results


# ---------------------------------------------------------------------------
class ItemDetector:
    """
    Detecta ítems dentro de la ventana del contenedor de loot.
    Retorna lista de (frame_x, frame_y, confidence, name).
    """

    def __init__(self, config: LootConfig) -> None:
        self._cfg = config
        self._templates: List[Tuple[str, np.ndarray]] = []
        self._load_templates()

    def _load_templates(self) -> None:
        tdir = _TEMPLATES_DIR / "loot_items"
        tdir.mkdir(parents=True, exist_ok=True)
        self._templates = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
            for path in sorted(tdir.glob(ext)):
                img = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if img is not None:
                    self._templates.append((path.stem, img))
        if self._templates:
            print(f"  [L] Templates de ítems: {[n for n,_ in self._templates]}")

    def reload(self) -> None:
        self._load_templates()

    def _scale_roi(self, frame: np.ndarray) -> Tuple[int, int, int, int]:
        h, w = frame.shape[:2]
        rx, ry = w / self._cfg.ref_width, h / self._cfg.ref_height
        x, y, rw, rh = self._cfg.container_roi
        return int(x * rx), int(y * ry), int(rw * rx), int(rh * ry)

    def detect_whitelist(
        self, frame: np.ndarray
    ) -> List[Tuple[int, int, float, str]]:
        """Template matching de ítems de la whitelist en el contenedor."""
        if not self._templates or frame is None:
            return []

        rx, ry, rw, rh = self._scale_roi(frame)
        roi = frame[ry : ry + rh, rx : rx + rw]
        if roi.size == 0:
            return []

        # Scale dedup distance by resolution
        h, w = frame.shape[:2]
        scale_x = w / self._cfg.ref_width
        scale_y = h / self._cfg.ref_height
        dedup_dx = int(16 * scale_x)
        dedup_dy = int(16 * scale_y)

        item_conf = self._cfg.item_confidence
        results: List[Tuple[int, int, float, str]] = []
        seen: List[Tuple[int, int]] = []

        for name, tmpl in self._templates:
            if self._cfg.loot_whitelist and name not in self._cfg.loot_whitelist:
                continue
            if (
                tmpl.shape[0] > roi.shape[0]
                or tmpl.shape[1] > roi.shape[1]
            ):
                continue
            match = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
            locs = np.where(match >= item_conf)
            for pt_y, pt_x in zip(*locs):
                cx = rx + int(pt_x + tmpl.shape[1] / 2)
                cy = ry + int(pt_y + tmpl.shape[0] / 2)
                if any(abs(cx - sx) < dedup_dx and abs(cy - sy) < dedup_dy for sx, sy in seen):
                    continue
                conf = float(match[pt_y, pt_x])
                results.append((cx, cy, conf, name))
                seen.append((cx, cy))
        return results

    def all_slot_positions(
        self, frame: np.ndarray, max_slots: int = 16
    ) -> List[Tuple[int, int]]:
        """
        Devuelve las posiciones de todos los slots del contenedor
        (para modo loot_mode='all').
        Detecta cuáles tienen contenido buscando píxeles no negros.

        R3: Tries visual container detection to locate the actual container
        window dynamically. Falls back to scaled config ROI.
        """
        # R3: Try visual container detection first
        container = detect_container_window(frame)
        if container is not None:
            rx, ry, rw, rh = container
        else:
            rx, ry, rw, rh = self._scale_roi(frame)

        roi = frame[ry : ry + rh, rx : rx + rw]
        if roi.size == 0:
            return []

        # Scale slot dimensions to match the actual frame resolution so slot
        # positions align correctly when the frame differs from ref_width/height.
        h, w = frame.shape[:2]
        scale_x = w / self._cfg.ref_width
        scale_y = h / self._cfg.ref_height
        slot_w = max(1, int(self._cfg.slot_size_px * scale_x))
        slot_h = max(1, int(self._cfg.slot_size_px * scale_y))
        cols = self._cfg.container_cols
        inset = max(1, int(2 * scale_x))
        inset_end = max(2, int(4 * scale_x))
        positions: List[Tuple[int, int]] = []

        for idx in range(max_slots):
            row = idx // cols
            col = idx % cols
            sx = col * slot_w + inset
            sy = row * slot_h + inset
            ex, ey = min(sx + slot_w - inset_end, rw), min(sy + slot_h - inset_end, rh)
            if ex <= sx or ey <= sy:
                break
            slot_crop = roi[sy:ey, sx:ex]
            if slot_crop.size == 0:
                break
            # Slot ocupado si tiene píxeles no completamente negros
            mean_val = float(np.mean(slot_crop))
            if mean_val > self._cfg.slot_brightness_threshold:
                cx = rx + col * slot_w + slot_w // 2
                cy = ry + row * slot_h + slot_h // 2
                positions.append((cx, cy))

        return positions


# ---------------------------------------------------------------------------
@dataclass
class PendingCorpse:
    """Cadáver pendiente de lootear."""
    # Coordenadas del tile del cadáver (None si solo hay posición en frame)
    tile_x: Optional[int]
    tile_y: Optional[int]
    tile_z: Optional[int]
    # Timestamp cuando se añadió (para aplicar loot_delay)
    created_at: float = field(default_factory=time.monotonic)
    # Intentos de loot realizados
    attempts: int = 0
    # Looted con éxito
    done: bool = False


# ---------------------------------------------------------------------------
class Looter:
    """
    Hilo de looting automático.

    Integración con auto_walker:
    ─────────────────────────────
    1. Registra el frame getter (misma fuente OBS que usa el walker):
         looter.set_frame_getter(lambda: detector.get_raw_frame())

    2. Registra el getter de posición del jugador:
         looter.set_player_getter(lambda: coord_tracker.get_position())

    3. Avisa cuando el walker mata un monstruo (o lo da por muerto):
         looter.notify_kill(coord)   # coord = Coordinate del cadáver

    4. Para pausar el walk mientras se lootea usa el callback:
         looter.on_loot_start  = walker.pause
         looter.on_loot_finish = walker.resume

    5. Inicia y detén el hilo:
         looter.start()
         ...
         looter.stop()
    """

    def __init__(
        self,
        ctrl: Any,                       # InputController
        config: Optional[LootConfig] = None,
    ) -> None:
        self._ctrl   = ctrl
        self._cfg    = config or LootConfig.load()
        self._corpse_det = CorpseDetector(self._cfg)
        self._item_det   = ItemDetector(self._cfg)

        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._player_getter: Optional[Callable[[], Any]] = None

        self._pending: List[PendingCorpse] = []
        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Callbacks para pausar/reanudar el walker durante el loot
        self.on_loot_start:  Optional[Callable[[], None]] = None
        self.on_loot_finish: Optional[Callable[[], None]] = None
        # Callback por ítem looteado: (item_name: str, count: int) → None
        # item_name es cada entrada de loot_whitelist; si mode='all' → '__looted__'
        self.on_item_looted: Optional[Callable[[str, int], None]] = None

        # Estadísticas
        self._looted: int = 0
        self._items_picked: int = 0
        # Pausa
        self._paused: bool = False
        # Log
        self._log_cb: Optional[Callable[[str], None]] = None

    # ── Configuración ────────────────────────────────────────────────────────

    def set_frame_getter(
        self, fn: Callable[[], Optional[np.ndarray]]
    ) -> None:
        self._frame_getter = fn

    def set_player_getter(self, fn: Callable[[], Any]) -> None:
        """Función que devuelve la coordenada actual del jugador (Coordinate o None)."""
        self._player_getter = fn

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        """Route all looter log messages through *cb* instead of stdout."""
        self._log_cb = cb

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            self._log_cb(msg)
        else:
            print(msg)

    # ── Control ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("  [L] ✓ Hilo de looter iniciado")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        self._log(
            f"  [L] Detenido — looteados={self._looted} "
            f"ítems={self._items_picked}"
        )

    def pause(self) -> None:
        """Temporarily suspend looting without stopping the thread."""
        self._paused = True

    def resume(self) -> None:
        """Resume looting after a pause."""
        self._paused = False

    @property
    def is_paused(self) -> bool:
        """True while the looter loop is paused."""
        return self._paused

    @property
    def is_running(self) -> bool:
        """True while the looter thread is active."""
        return self._running

    @property
    def has_frame_getter(self) -> bool:
        """True when a frame-getter function has been registered."""
        return self._frame_getter is not None

    @property
    def has_player_getter(self) -> bool:
        """True when a player-position getter has been registered."""
        return self._player_getter is not None

    @property
    def whitelist_count(self) -> int:
        """Number of items currently in the loot whitelist."""
        return len(self._cfg.loot_whitelist)

    def stats_snapshot(self) -> Dict[str, Any]:
        """Return a point-in-time snapshot of looter statistics.

        Keys
        ----
        looted          Number of corpses successfully looted.
        items_picked    Total items picked across all looted corpses.
        pending         Number of corpses still queued (not done).
        is_running      Whether the looter thread is active.
        is_paused       Whether the looter is paused.
        loot_mode       Current loot mode ("all" or "whitelist").
        whitelist_count Number of items in the whitelist.
        """
        return {
            "looted":          self._looted,
            "items_picked":    self._items_picked,
            "pending":         self.pending_count,
            "is_running":      self._running,
            "is_paused":       self._paused,
            "loot_mode":       self._cfg.loot_mode,
            "whitelist_count": self.whitelist_count,
        }

    @property
    def has_pending(self) -> bool:
        """True when there is at least one corpse waiting to be looted."""
        return self.pending_count > 0

    @property
    def has_looted(self) -> bool:
        """True when at least one corpse has been successfully looted."""
        return self._looted > 0

    @property
    def is_whitelist_mode(self) -> bool:
        """True when the loot mode is 'whitelist'."""
        return self._cfg.loot_mode == "whitelist"

    @property
    def looted_count(self) -> int:
        """Total corpses successfully looted since last reset."""
        return self._looted

    @property
    def items_picked_count(self) -> int:
        """Total individual items picked since last reset."""
        return self._items_picked

    @property
    def has_items_picked(self) -> bool:
        """True when at least one item has been picked up."""
        return self._items_picked > 0

    # ── Notificaciones externas ───────────────────────────────────────────────

    _PENDING_MAXSIZE = 20  # max unprocessed corpses before oldest is dropped

    def notify_kill(self, coord: Optional[Any] = None) -> None:
        """
        Registrar un cadáver pendiente de lootear.
        coord : Coordinate del tile del cadáver (opcional; si None solo
                detectamos por template matching en el frame).
        """
        with self._lock:
            if len(self._pending) >= self._PENDING_MAXSIZE:
                # Drop the oldest not-yet-done entry to prevent unbounded growth
                for i, existing in enumerate(self._pending):
                    if not existing.done:
                        self._pending.pop(i)
                        self._log("  [L] pending corpse queue full — dropped oldest unprocessed entry")
                        break
            pc = PendingCorpse(
                tile_x=getattr(coord, "x", None),
                tile_y=getattr(coord, "y", None),
                tile_z=getattr(coord, "z", None),
            )
            self._pending.append(pc)
            self._log(
                f"  [L] Cadáver registrado "
                f"({pc.tile_x},{pc.tile_y}) — pendientes={len(self._pending)}"
            )

    @property
    def stats(self) -> Dict[str, int]:
        return {"looted": self._looted, "items_picked": self._items_picked}

    @property
    def pending_count(self) -> int:
        """Number of corpses still waiting to be looted."""
        with self._lock:
            return sum(1 for p in self._pending if not p.done)

    def clear_pending(self) -> None:
        """Discard all pending corpses (e.g. when changing floors)."""
        with self._lock:
            self._pending.clear()

    def reset_stats(self) -> None:
        """Zero the looted-corpses and items-picked counters."""
        with self._lock:
            self._looted = 0
            self._items_picked = 0

    def update_config(self, config: LootConfig) -> None:
        """
        Hot-swap loot configuration without restarting the thread.
        Rebuilds both detectors with the new config.
        """
        self._cfg = config
        self._corpse_det = CorpseDetector(config)
        self._item_det   = ItemDetector(config)

    # ── Whitelist & mode management ───────────────────────────────────────────

    def set_loot_mode(self, mode: str) -> None:
        """
        Switch between loot modes without restarting the thread.

        *mode* must be ``"all"``, ``"whitelist"``, or ``"quick"``.

        * ``"all"``       – open corpse and shift-click every visible slot.
        * ``"whitelist"`` – open corpse and shift-click only template-matched items.
        * ``"quick"``     – right-click corpse → "Quick Loot" (client transfers loot
                            to the assigned quick-loot containers automatically;
                            no container window is opened by the bot).

        Change takes effect on the very next corpse processed.
        """
        if mode not in ("all", "whitelist", "quick"):
            raise ValueError(
                f"Unknown loot mode: {mode!r}. Use 'all', 'whitelist', or 'quick'."
            )
        self._cfg.loot_mode = mode

    def add_to_whitelist(self, name: str) -> None:
        """
        Append *name* (template stem, case-sensitive) to the loot whitelist.

        Silently ignores duplicates.
        """
        if name not in self._cfg.loot_whitelist:
            self._cfg.loot_whitelist.append(name)

    def remove_from_whitelist(self, name: str) -> bool:
        """
        Remove *name* from the loot whitelist.

        Returns ``True`` if the item was present and removed, ``False`` otherwise.
        """
        if name in self._cfg.loot_whitelist:
            self._cfg.loot_whitelist.remove(name)
            return True
        return False

    def loot_summary(self) -> str:
        """
        One-line human-readable summary of loot statistics.

        Example: ``"looted=3 items=12 pending=1 mode=all"``
        """
        return (
            f"looted={self._looted} "
            f"items={self._items_picked} "
            f"pending={self.pending_count} "
            f"mode={self._cfg.loot_mode}"
        )

    def stow_container(self, frame: Optional[np.ndarray] = None) -> bool:
        """
        Send the "Stow All Items" action on the currently open loot container.

        R3: Uses visual container detection first to find the title bar,
        then visual menu detection for "Stow All Items". Falls back to
        scaled offsets.

        Parameters
        ----------
        frame : ndarray, optional
            Current OBS frame used to scale ROI coordinates.  If *None* and a
            frame-getter is registered, it is fetched automatically.

        Returns
        -------
        bool
            ``True`` if both clicks were delivered successfully.
        """
        if frame is None and self._frame_getter is not None:
            frame = self._frame_getter()

        pos = self._cfg.stow_all_container_pos
        if pos and len(pos) == 2:
            click_x = int(pos[0])
            click_y = int(pos[1])
        else:
            # R3: Try visual container detection first
            if frame is not None:
                h, w = frame.shape[:2]
                scale_x = w / self._cfg.ref_width
                scale_y = h / self._cfg.ref_height
                container = detect_container_window(frame)
                if container is not None:
                    cx, cy, cw, _ch = container
                    click_x = cx + cw // 2
                    click_y = cy + max(3, self._scale_y_offset(6, frame))
                else:
                    # Fallback: scaled config ROI
                    rx, ry, rw, _rh = self._cfg.container_roi
                    click_x = int((rx + rw // 2) * scale_x)
                    click_y = int(ry * scale_y) + self._scale_y_offset(6, frame)
            else:
                rx, ry, rw, _rh = self._cfg.container_roi
                click_x = rx + rw // 2
                click_y = ry + 6

        # R3: Try visual menu detection (select_context_menu_entry handles the right-click)
        result = select_context_menu_entry(
            ctrl=self._ctrl,
            click_x=click_x,
            click_y=click_y,
            entry_index=0,
            fallback_offset_y=self._cfg.stow_all_menu_offset_y,
            frame_getter=self._frame_getter,
            frame_ref=frame,
            detect_context_menu_fn=detect_context_menu,
            find_menu_entry_offset_fn=find_menu_entry_offset,
            sleep_fn=jittered_sleep,
            scale_x_offset_fn=self._scale_x_offset,
            scale_y_offset_fn=self._scale_y_offset,
        )
        if not result.success:
            self._log(
                f"  [L] \u26a0 stow_container: acci\u00f3n fall\u00f3 en ({click_x},{click_y}) [{result.method}]"
            )
            return False
        self._log(f"  [L] Stow All Items en ({click_x},{click_y}) [{result.method}]")
        return True

    def _corpse_screen_pos(
        self, corpse: PendingCorpse, frame: np.ndarray
    ) -> Optional[Tuple[int, int]]:
        """
        Calcula la posici\u00f3n en pantalla del cad\u00e1ver.

        Estrategia (por orden de prioridad):
        1. C\u00e1lculo por coordenadas: si tenemos la coord del cad\u00e1ver y del jugador.
        2. Template matching en el viewport.
        """
        # 1) Cálculo por coordenadas + confirmación visual
        if (
            corpse.tile_x is not None
            and self._player_getter is not None
        ):
            player = self._player_getter()
            if player is not None:
                dx = corpse.tile_x - player.x
                dy = corpse.tile_y - player.y

                # Verificar rango máximo
                if (
                    self._cfg.max_range_tiles > 0
                    and max(abs(dx), abs(dy)) > self._cfg.max_range_tiles
                ):
                    return None  # aún fuera de rango

                h, w = frame.shape[:2]
                scale_x = w / self._cfg.ref_width
                scale_y = h / self._cfg.ref_height
                rx, ry, rw, rh = self._cfg.viewport_roi
                vx = int(rx * scale_x)
                vy = int(ry * scale_y)
                vrw = int(rw * scale_x)
                vrh = int(rh * scale_y)

                cx = vx + vrw // 2 + int(dx * self._cfg.tile_size_px * scale_x)
                cy = vy + vrh // 2 + int(dy * self._cfg.tile_size_px * scale_y)

                # Visual confirmation: check if a corpse sprite exists near the
                # computed pixel position (tolerance = 3 tiles).  If found, snap
                # to the visually detected centre for better accuracy; if not,
                # proceed with the coordinate estimate and log a warning so we
                # can tune templates if necessary.
                detections = self._corpse_det.detect(frame)
                if detections:
                    tol = int(3 * self._cfg.tile_size_px * scale_x)
                    near = [d for d in detections if abs(d[0] - cx) <= tol and abs(d[1] - cy) <= tol]
                    if near:
                        near.sort(key=lambda d: abs(d[0] - cx) + abs(d[1] - cy))
                        vcx, vcy = near[0][0], near[0][1]
                        if (vcx, vcy) != (cx, cy):
                            self._log(
                                f"  [L] coord→({cx},{cy}) corregido a template→({vcx},{vcy})"
                            )
                        return vcx, vcy
                    self._log(
                        f"  [L] ⚠ coord→({cx},{cy}) sin confirmación visual "
                        f"(template no encontrado en radio {tol}px)"
                    )
                return cx, cy

        # 2) Template matching (único camino cuando no hay coordenada)
        detections = self._corpse_det.detect(frame)
        if detections:
            # Tomar el más cercano al centro del viewport
            h, w = frame.shape[:2]
            center_x, center_y = w // 2, h // 2
            detections.sort(
                key=lambda d: abs(d[0] - center_x) + abs(d[1] - center_y)
            )
            cx, cy, _, _ = detections[0]
            return cx, cy

        return None

    def _scale_y_offset(self, offset_px: int, frame: Optional[np.ndarray] = None) -> int:
        """Scale a vertical pixel offset from the reference 1080p to the actual frame height."""
        if frame is None:
            return offset_px
        return scale_offset_y(offset_px, frame, self._cfg.ref_height)

    def _scale_x_offset(self, offset_px: int, frame: Optional[np.ndarray] = None) -> int:
        """Scale a horizontal pixel offset from the reference 1920 to the actual frame width."""
        if frame is None:
            return offset_px
        return scale_offset_x(offset_px, frame, self._cfg.ref_width)

    def _open_corpse(self, cx: int, cy: int) -> bool:
        return runtime_open_corpse(
            self,
            cx,
            cy,
            detect_context_menu_fn=detect_context_menu,
            find_menu_entry_offset_fn=find_menu_entry_offset,
            jittered_sleep_fn=jittered_sleep,
        )

    def _quick_loot_corpse(self, cx: int, cy: int) -> bool:
        return runtime_quick_loot_corpse(
            self,
            cx,
            cy,
            detect_context_menu_fn=detect_context_menu,
            find_menu_entry_offset_fn=find_menu_entry_offset,
            jittered_sleep_fn=jittered_sleep,
        )

    def _quick_loot_hotkey(self, cx: int, cy: int) -> bool:
        """
        Quick loot via Alt+Q keyboard shortcut.

        Moves the mouse cursor over the corpse at (cx, cy) then sends Alt+Q,
        which is Tibia's native Quick Loot hotkey.  This is faster and more
        reliable than navigating the right-click context menu.

        VK_MENU (Alt) = 0x12, Q = 0x51.
        """
        result = quick_loot_target(
            ctrl=self._ctrl,
            click_x=cx,
            click_y=cy,
            use_hotkey=True,
            quick_loot_menu_offset_y=self._cfg.quick_loot_menu_offset_y,
            sleep_fn=jittered_sleep,
        )
        if result.method == "move_mouse":
            self._log(f"  [L] ⚠ Alt+Q: move_mouse({cx},{cy}) falló")
            return False
        if result.success:
            self._log(f"  [L] Alt+Q enviado sobre ({cx},{cy})")
            return True
        self._log(f"  [L] ⚠ Alt+Q: key_combo falló en ({cx},{cy})")
        return False

    def _pick_items(self, frame: np.ndarray) -> Tuple[int, List[str]]:
        return runtime_pick_items(self, frame, jittered_sleep_fn=jittered_sleep)

    def _verify_loot(
        self,
        frame_before: np.ndarray,
        frame_after: np.ndarray,
        expected_picked: int,
    ) -> int:
        return runtime_verify_loot(self, frame_before, frame_after, expected_picked)

    # ── Loop principal ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        runtime_run_loop(
            self,
            jittered_sleep_fn=jittered_sleep,
            time_module=time,
            random_module=random,
        )
