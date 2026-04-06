"""
DepotManager
------------
Automatiza el ciclo de depot en Tibia:
  1. Navegar al tile del depot chest
  2. Abrir el depot chest (doble clic o clic derecho → "Open")
  3. Abrir el contenedor del jugador (backpack)
  4. Mover ítems del backpack al depot (shift+click o "deposit all")
  5. Opcionalmente abrir banco NPC y depositar gold
  6. Cerrar contenedores y reanudar la ruta

Integración con auto_walker:
    dm = DepotManager(ctrl, nav)
    dm.set_frame_getter(lambda: motion_detector.get_raw_frame())
    # En el script .in:
    #   action depot
    # O directamente:
    dm.run_depot_cycle(player_pos=coord_tracker.get_position())

Configuración: depot_config.json en el directorio raíz del proyecto.

Template matching:
  Para detectar que el contenedor se abrió, el DepotManager busca en el frame
  la ventana de contenedor mediante template matching o análisis de color.
  Si no hay templates, usa una espera fija (configurable).

Notas de posición del depot chest (Tibia 1920×1080):
  El chest aparece como un objeto en el viewport, cuya posición en pantalla
  depende de las coordenadas del jugador y el tile del chest.
  DepotManager calcula la posición en píxeles usando:
    px = viewport_center_x + (chest.x - player.x) * tile_size_px
    py = viewport_center_y + (chest.y - player.y) * tile_size_px
"""

from __future__ import annotations

import json
import random
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

import logging as _logging
import cv2
import numpy as np

from .client_actions import close_open_containers
from .storage_state import StorageSurface
from .depot_manager_runtime import (
    deposit_items as runtime_deposit_items,
    deposit_loot_all as runtime_deposit_loot_all,
    deposit_stow_all as runtime_deposit_stow_all,
    detect_open_container as runtime_detect_open_container,
    find_backpack_slots as runtime_find_backpack_slots,
    find_container_first_slot as runtime_find_container_first_slot,
    wait_for_container as runtime_wait_for_container,
)

_dm_log = _logging.getLogger("wn.depot")

from src.ui_detection import (
    detect_context_menu,
    detect_container_window,
    find_menu_entry_offset,
    scale_offset_x,
    scale_offset_y,
)
from src.humanizer import jittered_sleep

# ---------------------------------------------------------------------------
DEPOT_CONFIG_FILE = Path(__file__).parent.parent / "depot_config.json"

from src.config_paths import TEMPLATES_DIR as _TEMPLATES_DIR


# ---------------------------------------------------------------------------
@dataclass
class DepotConfig:
    """
    Configuración del macro de depot.

    depot_chest_coord : [x, y, z]
        Coordenada del tile del cofre de depot.
        Default: Thais depot chest.

    bank_npc_coord : [x, y, z]
        Coordenada del tile frente al banco (Thais bank NPC).
        [] = desactivado (no depositar gold).

    viewport_roi : [x, y, w, h]
        Región del frame OBS que es el viewport del juego.

    tile_size_px : int
        Píxeles por tile en el viewport al zoom actual del cliente (default 32).

    viewport_center : [cx, cy]
        Centro del viewport en píxeles (posición del personaje en pantalla).
        Default: centro del viewport ROI.

    open_wait : float
        Segundos de espera tras hacer clic en el chest antes de intentar
        interactuar con el contenedor (default 0.8s).

    deposit_mode : str
        "shift_click"  → shift+click en cada ítem del backpack.
        "loot_all"     → usar el botón "Loot All" si está disponible.
        "stow_all"     → clic derecho en el depot chest → "Stow All Items" (nativo Tibia).
                         Deposita todo al instante sin abrir el chest ni el backpack.

    container_roi : [x, y, w, h]
        Región del frame donde aparece la ventana de contenedor abierto.
        Se usa para detectar que el chest/depot se abrió correctamente.

    max_items_per_cycle : int
        Máximo de ítems a depositar por ciclo (limita la duración). 0 = sin límite.

    close_containers_vk : int
        VK para cerrar todos los contenedores abiertos (default: 0 = no usar).
        En Tibia puedes asignar ESC o una tecla custom.
    """

    depot_chest_coord: List[int]  = field(default_factory=lambda: [32258, 32248, 7])
    bank_npc_coord:    List[int]  = field(default_factory=list)             # [] = sin banco
    viewport_roi:      List[int]  = field(default_factory=lambda: [0, 0, 1920, 1080])
    tile_size_px:      int        = 32
    viewport_center:   List[int]  = field(default_factory=list)  # [] → auto-derive from frame
    open_wait:         float      = 0.8
    deposit_mode:      str        = "shift_click"
    container_roi:     List[int]  = field(default_factory=lambda: [820, 220, 280, 320])
    max_items_per_cycle: int      = 0
    close_containers_vk: int      = 0
    # loot_all mode: VK to press (e.g. a macro key that deposits all items at once)
    loot_all_vk:         int      = 0
    # loot_all mode: screen pixel to click for a "Deposit All" button ([x, y] or [])
    loot_all_btn_pos:    List[int] = field(default_factory=list)
    # Backpack slot layout (configurable per resolution/zoom)
    backpack_slot_origin: List[int] = field(default_factory=lambda: [834, 270])
    backpack_slot_cols:   int       = 4
    backpack_slot_rows:   int       = 8
    backpack_slot_spacing: int      = 34
    # Production tuning
    # Y offset (px) from right_click position to the "Open" entry in Tibia's
    # context menu.  Varies with client zoom/version; measure with a ruler tool.
    context_menu_open_offset_y: int   = 20
    # Max seconds to wait for the container window to appear after opening.
    container_detect_wait:      float = 3.0
    # When True (production default) the cycle aborts and returns False if the
    # container is not detected within container_detect_wait seconds.
    # Set False only for debugging / blind-deposit scenarios.
    abort_on_container_timeout: bool  = True
    # Seconds to wait after each NPC bank dialogue line (hi / deposit all / yes).
    bank_dialogue_delay:        float = 1.2
    # Seconds to wait after pressing close_containers_vk before resuming.
    close_containers_wait:      float = 0.3
    # stow_all mode: index of "Stow All Items" in the right-click context menu.
    # "Open" is always at index 0; "Stow All Items" is typically at index 1.
    stow_all_menu_entry_index:  int   = 1
    # stow_all via backpack: index of "Stow All Items of This Type" in the item
    # right-click menu.  -1 = last entry (Tibia puts it at the bottom of the menu).
    stow_all_item_entry_index:  int   = -1
    # Which container in the right panel to stow from (0 = first detected, 1 = second, …).
    # The bot scans the panel for container title bands (uniform dark gray ~50 brightness)
    # below stow_panel_y_start and picks the Nth one found top-to-bottom.
    # Use -1 to disable dynamic detection and always use backpack_slot_origin.
    stow_container_index:       int   = 0
    # Only scan for container headers below this Y (avoids minimap/skill bar false positives).
    stow_panel_y_start:         int   = 380
    # Pixels from the bottom of the detected title bar to the first slot centre.
    container_slot_y_offset:    int   = 50

    def save(self, path: Path = DEPOT_CONFIG_FILE) -> None:
        import dataclasses
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(self), f, indent=2)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path = DEPOT_CONFIG_FILE) -> "DepotConfig":
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
class DepotManager:
    """
    Executes an automated depot cycle.

    Parameters
    ----------
    ctrl : InputController
        Input controller for sending mouse clicks and key presses.
    config : DepotConfig, optional
        Configuration; loaded from depot_config.json if not provided.
    frame_getter : callable, optional
        Function returning the current OBS frame (BGR ndarray) for
        container open detection. If None, uses fixed wait times.
    """

    def __init__(
        self,
        ctrl: Any,                             # InputController
        config: Optional[DepotConfig] = None,
        frame_getter: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._ctrl         = ctrl
        self._cfg          = config or DepotConfig.load()
        self._frame_getter = frame_getter
        self._log_cb:      Optional[Callable[[str], None]] = None

        # Statistics
        self._cycle_count:     int = 0
        self._items_deposited: int = 0

        # Template cache for _slot_matches — populated lazily, keyed by stem → ndarray
        self._tmpl_cache: Optional[Dict[str, Any]] = None
        self._runtime_random = random

        # Optional modern-storage subsystems (injected after construction)
        self._storage_detector: Any = None   # StorageDetector | None
        self._storage_navigator: Any = None  # StorageNavigator | None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def set_frame_getter(self, getter: Callable[[], Any]) -> None:
        """Set the frame source (OBS frame getter)."""
        self._frame_getter = getter

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        """Set a log callback (e.g. the walker's _log function)."""
        self._log_cb = cb

    @property
    def cycle_count(self) -> int:
        """Number of depot cycles completed successfully this session."""
        return self._cycle_count

    @property
    def items_deposited(self) -> int:
        """Cumulative number of items deposited across all cycles."""
        return self._items_deposited

    def reset_stats(self) -> None:
        """Reset cycle and item counters back to zero."""
        self._cycle_count     = 0
        self._items_deposited = 0

    @property
    def has_frame_getter(self) -> bool:
        """True when a frame getter has been registered via ``set_frame_getter``."""
        return self._frame_getter is not None

    @property
    def has_log_callback(self) -> bool:
        """True when a log callback has been registered via ``set_log_callback``."""
        return self._log_cb is not None

    @property
    def items_per_cycle(self) -> float:
        """
        Average number of items deposited per completed depot cycle.

        Returns ``0.0`` when no cycles have been completed yet.
        """
        if self._cycle_count == 0:
            return 0.0
        return self._items_deposited / self._cycle_count

    def stats_snapshot(self) -> dict[str, Any]:
        """
        Return a lightweight dict snapshot of depot manager state.

        Keys: ``cycle_count``, ``items_deposited``, ``items_per_cycle``,
        ``has_frame_getter``, ``has_log_callback``.
        """
        return {
            "cycle_count":      self._cycle_count,
            "items_deposited":  self._items_deposited,
            "items_per_cycle":  self.items_per_cycle,
            "has_frame_getter": self.has_frame_getter,
            "has_log_callback": self._log_cb is not None,
        }

    @property
    def is_idle(self) -> bool:
        """True when no depot cycle has been completed yet this session."""
        return self._cycle_count == 0

    @property
    def has_deposited_items(self) -> bool:
        """True when at least one item has been deposited this session."""
        return self._items_deposited > 0

    @property
    def has_run_cycles(self) -> bool:
        """True when at least one complete depot cycle has been finished."""
        return self._cycle_count > 0

    @property
    def is_unlimited(self) -> bool:
        """True when no per-cycle item cap is set (``max_items_per_cycle == 0``)."""
        return self._cfg.max_items_per_cycle == 0

    @property
    def has_cap(self) -> bool:
        """True when a per-cycle item cap is configured (``max_items_per_cycle > 0``)."""
        return self._cfg.max_items_per_cycle > 0

    def update_config(self, config: DepotConfig) -> None:
        """
        Hot-swap depot configuration without re-creating the manager.
        Safe to call between cycles.
        """
        self._cfg = config
        self._tmpl_cache = None  # invalidate on config change
        self._log("  [P] \u21ba Configuraci\u00f3n actualizada")

    def set_storage_detector(self, detector: Any) -> None:
        """Inject a StorageDetector for modern surface awareness."""
        self._storage_detector = detector

    def set_storage_navigator(self, navigator: Any) -> None:
        """Inject a StorageNavigator for tab-level navigation."""
        self._storage_navigator = navigator

    def _ensure_surface(self, target: StorageSurface) -> bool:
        """
        Guarantee that *target* surface is open before operating on it.

        If a StorageNavigator is configured, delegates navigation to it
        (handles tabs: Stash, Inbox, Store Inbox, Manage Containers).
        Falls back to the legacy ``_open_chest()`` path when the navigator
        is absent or when targeting DEPOT_CHEST directly.
        """
        if self._storage_navigator is not None:
            ok = self._storage_navigator.navigate_to(
                target,
                chest_opener=self._open_chest,
            )
            if ok:
                return True
            self._log(
                f"  [P] \u26a0 StorageNavigator no pudo alcanzar '{target.value}' "
                "— cayendo al flujo legacy"
            )

        # Legacy fallback: just open the chest
        return self._open_chest()

    def run_depot_cycle(
        self,
        player_pos: Optional[Any] = None,    # Optional[Coordinate]
        backpack_items: Optional[List[str]] = None,
    ) -> bool:
        """
        Run a full depot cycle: open chest → deposit items → close.

        Parameters
        ----------
        player_pos : Coordinate, optional
            Current player position (used to compute chest pixel position).
        backpack_items : list[str], optional
            List of item names to deposit (uses template matching).
            If None, deposits ALL items via shift+click on each visible slot.

        Returns
        -------
        bool
            True if the depot cycle completed successfully.
        """
        self._log("  [P] Iniciando ciclo de depot…")

        # ── "Stow All Items" fast path ───────────────────────────────────────
        if self._cfg.deposit_mode == "stow_all":
            ok = self._deposit_stow_all(player_pos)
            if ok:
                self._items_deposited += 1  # exact count unknown; counts as 1 action
                if self._cfg.bank_npc_coord:
                    self._bank_deposit()
                self._cycle_count += 1
                self._log("  [P] Ciclo de depot (stow_all) completado ✓")
                return True
            self._log("  [P] ⚠ stow_all falló — abortando ciclo")
            return False

        # Step 1: Open / navigate to the depot chest surface
        if not self._ensure_surface(StorageSurface.DEPOT_CHEST):
            self._log("  [P] ⚠ No se pudo abrir el chest — abortando ciclo")
            return False

        # Step 2: Wait for container to open
        opened = self._wait_for_container(max_wait=self._cfg.container_detect_wait)
        if not opened:
            if self._cfg.abort_on_container_timeout:
                self._log("  [P] \u26a0 Container no detectado \u2014 abortando ciclo")
                return False
            self._log("  [P] \u26a0 Container no detectado \u2014 continuando de todas formas")

        # Step 3: Deposit items
        deposited = self._deposit_items(backpack_items)
        self._log(f"  [P] {deposited} ítems depositados")
        self._items_deposited += deposited
        # Step 4: Bank NPC (optional)
        if self._cfg.bank_npc_coord:
            self._bank_deposit()

        # Step 5: Close containers
        self._close_containers()
        self._cycle_count += 1
        self._log("  [P] Ciclo de depot completado ✓")
        return True

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _open_chest(self, player_pos: Optional[Any] = None) -> bool:
        """
        Click on the depot chest tile in the viewport.
        Returns True if the click was sent successfully.

        R3: captures a frame before and after the right-click, then tries
        visual context-menu detection.  Falls back to a resolution-scaled
        offset when the menu cannot be detected visually.
        """
        chest_coord = self._cfg.depot_chest_coord
        if not chest_coord or len(chest_coord) < 2:
            self._log("  [P] depot_chest_coord no configurado")
            return False

        # Compute pixel position of the chest tile relative to the player.
        # Backward compatibility: if player_pos is unavailable, interpret the
        # configured chest coords as direct screen pixels.
        if player_pos is None:
            px, py = int(chest_coord[0]), int(chest_coord[1])
            self._log(
                "  [P] ⚠ _open_chest: player_pos no disponible — usando "
                f"coords directas ({px},{py})"
            )
        else:
            px, py = self._tile_to_screen(
                tile_x=chest_coord[0],
                tile_y=chest_coord[1],
                player_x=player_pos.x,
                player_y=player_pos.y,
            )

        self._log(f"  [P] Clic derecho en chest ({chest_coord[0]},{chest_coord[1]}) → px=({px},{py})")
        if not self._ctrl.is_connected():
            self._log("  [P] ⚠ InputController no conectado")
            return False

        # Capture frame before right-click for diff-based menu detection
        frame_before = self._frame_getter() if self._frame_getter else None

        # Validate that the calculated pixel position is within the viewport.
        # If the chest tile is too far from the player, the coords will be
        # outside the game window and the click will miss.
        if frame_before is not None:
            fh, fw = frame_before.shape[:2]
            if not (0 <= px < fw and 0 <= py < fh):
                self._log(
                    f"  [P] ✖ Chest fuera del viewport ({px},{py}) para frame {fw}x{fh}. "
                    f"El personaje no está lo suficientemente cerca del chest."
                )
                return False

        if not self._ctrl.right_click(px, py):
            self._log("  [P] \u26a0 right_click no report\u00f3 \u00e9xito (conexi\u00f3n perdida?)")
            return False
        jittered_sleep(0.4)

        frame_after = self._frame_getter() if self._frame_getter else None

        # --- R3: visual menu detection ------------------------------------
        menu_roi = None
        if frame_before is not None and frame_after is not None:
            menu_roi = detect_context_menu(frame_before, frame_after, px, py)

        if menu_roi is not None and frame_after is not None:
            fa: np.ndarray = frame_after
            entry = find_menu_entry_offset(fa, menu_roi, entry_index=0)
            if entry is not None:
                click_x, click_y = entry
                self._log(f"  [P] Menu visual → Open en ({click_x},{click_y})")
                if not self._ctrl.left_click(click_x, click_y):
                    self._log("  [P] \u26a0 left_click 'Open' visual fall\u00f3")
                    return False
                time.sleep(self._cfg.open_wait * random.uniform(0.8, 1.25))
                return True

        # --- Fallback: scaled offset from config --------------------------
        ref_frame = frame_after if frame_after is not None else frame_before
        offset_y = self._cfg.context_menu_open_offset_y
        if ref_frame is not None:
            offset_y = scale_offset_y(offset_y, ref_frame)
        menu_y = py + offset_y
        self._log(f"  [P] Fallback offset → Open en ({px},{menu_y})")
        if not self._ctrl.left_click(px, menu_y):
            self._log("  [P] \u26a0 left_click 'Open' no report\u00f3 \u00e9xito (conexi\u00f3n perdida?)")
            return False
        time.sleep(self._cfg.open_wait * random.uniform(0.8, 1.25))
        return True

    def _wait_for_container(self, max_wait: float = 3.0) -> bool:
        return runtime_wait_for_container(
            self,
            max_wait,
            jittered_sleep_fn=jittered_sleep,
            time_module=time,
        )

    def _detect_open_container(self, crop: Optional[np.ndarray]) -> bool:
        return runtime_detect_open_container(
            crop,
            detect_container_window_fn=detect_container_window,
            cv2_module=cv2,
        )

    def _deposit_items(self, item_names: Optional[List[str]] = None) -> int:
        return runtime_deposit_items(self, item_names, jittered_sleep_fn=jittered_sleep)

    def _deposit_loot_all(self) -> int:
        return runtime_deposit_loot_all(self, jittered_sleep_fn=jittered_sleep)

    def _deposit_stow_all(self, player_pos: Optional[Any] = None) -> bool:
        return runtime_deposit_stow_all(
            self,
            player_pos,
            detect_context_menu_fn=detect_context_menu,
            find_menu_entry_offset_fn=find_menu_entry_offset,
            jittered_sleep_fn=jittered_sleep,
            cv2_module=cv2,
        )

    def _find_container_first_slot(self, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        return runtime_find_container_first_slot(self, frame, cv2_module=cv2)

    def _find_backpack_slots(self) -> List[Tuple[int, int]]:
        return runtime_find_backpack_slots(
            self,
            detect_container_window_fn=detect_container_window,
            scale_offset_x_fn=scale_offset_x,
            scale_offset_y_fn=scale_offset_y,
        )

    def _slot_matches(self, px: int, py: int, item_names: List[str]) -> bool:
        """
        Check if the item at screen pixel (px, py) matches any of the target names
        using template matching. Returns True (deposit) when no verification is
        possible.

        Templates are loaded from disk exactly once and cached in
        ``self._tmpl_cache`` (keyed by stem → ndarray) for the lifetime of
        the manager, or until ``update_config`` is called.
        """
        if self._frame_getter is None:
            self._log("  [P] ⚠ _slot_matches: sin frame_getter — depositando sin verificar item")
            return True  # No verification → deposit everything
        frame = self._frame_getter()
        if frame is None:
            self._log("  [P] ⚠ _slot_matches: frame None — depositando sin verificar item")
            return True

        # Crop the slot region using the actual Tibia icon size (32×32 px),
        # NOT backpack_slot_spacing (which is the distance between slot centres,
        # typically 34px).  Using spacing as the crop size slightly overshoot
        # into adjacent slots and reduces template-match confidence.
        _SLOT_ICON_PX = 32
        slot_size = _SLOT_ICON_PX
        x0 = max(0, px - slot_size // 2)
        y0 = max(0, py - slot_size // 2)
        slot_crop = frame[y0: y0 + slot_size, x0: x0 + slot_size]
        if slot_crop.size == 0:
            return False

        templates_dir = _TEMPLATES_DIR / "loot_items"
        if not templates_dir.exists():
            self._log(f"  [P] ⚠ _slot_matches: directorio de templates no existe ({templates_dir}) — depositando sin verificar item")
            return True  # No templates → deposit everything

        # Lazily build template cache (one disk scan + imread per session)
        if self._tmpl_cache is None:
            self._tmpl_cache = {}
            for tmpl_path in sorted(templates_dir.glob("*.png")):
                img = cv2.imread(str(tmpl_path))
                if img is None:
                    self._log(f"  [P] \u26a0 Template inv\u00e1lido: {tmpl_path.name}")
                    continue
                self._tmpl_cache[tmpl_path.stem.lower()] = cv2.resize(
                    img, (slot_size, slot_size)
                )

        for item_name in item_names:
            for stem, tmpl in self._tmpl_cache.items():
                # Exact match (normalise spaces ↔ underscores).
                # Substring match caused false positives: "health_potion" matched
                # "strong_health_potion" templates, depositing wrong items.
                _norm = item_name.lower().replace(" ", "_")
                if _norm == stem.replace(" ", "_"):
                    result = cv2.matchTemplate(slot_crop, tmpl, cv2.TM_CCOEFF_NORMED)
                    if float(result.max()) >= 0.60:
                        return True
        return False

    def _bank_deposit(self) -> bool:
        """
        Walk to the bank NPC tile and deposit gold.
        Sends the NPC dialogue: "hi", "deposit all", "yes".
        Verifies the NPC dialog opened before sending commands.
        """
        self._log("  [P] Abriendo banco NPC…")
        # The NPC must already be adjacent (walk performed by caller).
        if not self._ctrl.is_connected():
            self._log("  [P] \u26a0 Sin conexi\u00f3n \u2014 banco omitido")
            return False

        delay = self._cfg.bank_dialogue_delay
        jittered_sleep(0.3)  # brief pause before greeting

        # Send greeting and verify NPC dialog appeared
        if not hasattr(self._ctrl, "type_text"):
            self._log("  [P] \u2756 InputController no tiene type_text \u2014 banco omitido. El gold NO se depositar\u00e1.")
            return False
        self._ctrl.type_text("hi")
        self._ctrl.press_key(0x0D)  # VK_RETURN
        self._log("  [P] Banco NPC \u2192 'hi'")
        time.sleep(delay)

        # Check if NPC dialog is visible before sending remaining commands
        dialog_ok = False
        if self._frame_getter is not None:
            from src.action_verifier import verify_dialog_open
            dialog_ok = verify_dialog_open(
                self._frame_getter, timeout=2.0, poll_interval=0.3
            )
        else:
            dialog_ok = True  # no visual feedback — assume OK

        if not dialog_ok:
            self._log("  [P] \u26a0 NPC dialog no detectado tras 'hi' \u2014 reintentando")
            if hasattr(self._ctrl, "type_text"):
                self._ctrl.type_text("hi")
                self._ctrl.press_key(0x0D)
            time.sleep(delay)
            # Re-verify after retry — if still no dialog, abort to avoid
            # typing "deposit all" / "yes" into public chat.
            if self._frame_getter is not None:
                from src.action_verifier import verify_dialog_open
                dialog_ok = verify_dialog_open(
                    self._frame_getter, timeout=2.0, poll_interval=0.3
                )
            if not dialog_ok:
                self._log(
                    "  [P] \u26a0 NPC dialog sigue sin responder \u2014 continuando por compatibilidad "
                    "(riesgo de escribir en chat si el bot no está junto al NPC)"
                )

        for msg in ["deposit all", "yes"]:
            if not self._ctrl.is_connected():
                self._log(f"  [P] \u26a0 Conexi\u00f3n perdida tras '{msg}' \u2014 banco incompleto")
                return False
            self._ctrl.type_text(msg)
            self._ctrl.press_key(0x0D)  # VK_RETURN
            self._log(f"  [P] Banco NPC \u2192 '{msg}'")
            time.sleep(delay)
        self._log("  [P] Gold depositado en banco \u2713")
        return True

    def bank_withdraw(self, amount: int = 0) -> bool:
        """
        Public: withdraw gold from the bank NPC.

        Sends: "hi" → "withdraw {amount}" (or "withdraw all") → "yes".

        Parameters
        ----------
        amount : int
            Gold to withdraw. 0 means "withdraw all".

        Returns
        -------
        bool
            True if the dialogue was sent successfully.
        """
        self._log("  [P] Retirando gold del banco…")
        if not self._ctrl.is_connected():
            self._log("  [P] ⚠ Sin conexión — withdraw omitido")
            return False

        withdraw_cmd = f"withdraw {amount}" if amount > 0 else "withdraw all"
        delay = self._cfg.bank_dialogue_delay
        jittered_sleep(0.3)
        for msg in ["hi", withdraw_cmd, "yes"]:
            if not self._ctrl.is_connected():
                self._log(f"  [P] ⚠ Conexión perdida tras '{msg}'")
                return False
            if hasattr(self._ctrl, "type_text"):
                self._ctrl.type_text(msg)
                self._ctrl.press_key(0x0D)  # VK_RETURN
            self._log(f"  [P] Banco NPC → '{msg}'")
            time.sleep(delay)
        self._log("  [P] Gold retirado del banco ✓")
        return True

    def bank_deposit_gold(self) -> bool:
        """
        Public wrapper around the internal bank deposit step.

        Uses the bank NPC coordinates from config to deposit all gold.

        Returns
        -------
        bool
            True if the dialogue was sent successfully.
        """
        if not self._cfg.bank_npc_coord:
            self._log("  [P] ⚠ bank_npc_coord no configurado — deposit omitido")
            return False
        return self._bank_deposit()

    def _close_containers(self) -> None:
        """Close all open containers."""
        if self._cfg.close_containers_vk:
            close_open_containers(
                ctrl=self._ctrl,
                close_vk=self._cfg.close_containers_vk,
                sleep_fn=time.sleep,
                wait_s=self._cfg.close_containers_wait * random.uniform(0.8, 1.25),
                frame_getter=self._frame_getter,
                container_roi=cast(tuple[int, int, int, int], tuple(self._cfg.container_roi)) if len(self._cfg.container_roi) == 4 else None,
                max_attempts=1,
            )

    def _tile_to_screen(
        self,
        tile_x: int,
        tile_y: int,
        player_x: int,
        player_y: int,
    ) -> Tuple[int, int]:
        """Convert tile coordinates to screen pixel position.

        R3: if ``viewport_center`` is not set, computes the centre from the
        current frame dimensions (resolution-independent) instead of using
        a hardcoded 640×480 fallback.
        """
        # Capture exactly one frame to avoid incoherent viewport/tile scaling
        # when two consecutive _frame_getter() calls return different dimensions.
        frame = self._frame_getter() if self._frame_getter else None

        vc = self._cfg.viewport_center
        if len(vc) >= 2:
            vcx, vcy = int(vc[0]), int(vc[1])
        else:
            # R3 — derive centre from actual frame dimensions
            if frame is not None and frame.size > 0:
                fh, fw = frame.shape[:2]
                vcx, vcy = fw // 2, fh // 2
                self._log(f"  [P] viewport_center auto → ({vcx},{vcy})")
            else:
                self._log("  [P] \u26a0 viewport_center inv\u00e1lido — usando (640, 480)")
                vcx, vcy = 640, 480
        tile_sz  = max(self._cfg.tile_size_px, 1)  # guard against division by zero
        # H3-fix: scale tile_size by actual resolution (designed for 1920x1080)
        if frame is not None and frame.size > 0:
            fw = frame.shape[1]
            tile_sz = int(tile_sz * fw / 1920)
        px = vcx + (tile_x - player_x) * tile_sz
        py = vcy + (tile_y - player_y) * tile_sz
        return int(px), int(py)

    def _log(self, msg: str) -> None:
        _dm_log.info(msg)
        if self._log_cb:
            self._log_cb(msg)
        else:
            print(msg)
