"""
storage_navigator.py
--------------------
State-machine navigator: transitions between StorageSurface states using
only hardware input and frame-based detection (BattlEye-safe).

Each transition is a small, testable function that receives:
  - A frame getter
  - An input controller
  - The current StorageState

Supported transitions (from → to):
  any           → DEPOT_CHEST    : walk to chest coord + right-click "Open"
  DEPOT_CHEST   → STASH          : click "Stash" tab on depot container
  DEPOT_CHEST   → INBOX          : click "Inbox" tab
  DEPOT_CHEST   → STORE_INBOX    : click "Store Inbox" tab
  DEPOT_CHEST   → MANAGE_CONTAINERS : click "Manage" button
  any           → UNKNOWN        : close all containers

Transitions not yet implemented are logged and return False.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from .storage_state import ContainerWindow, StorageSurface, StorageState, StorageTransition

# ---------------------------------------------------------------------------
# Tab layout for the depot container in modern Tibia
# Keys: surface the tab navigates to
# Values: approximate (x_offset_from_container_left, y_offset_from_title_bottom)
#         at reference 1920×1080.  Scaled at runtime.
# ---------------------------------------------------------------------------
_DEPOT_TABS: dict[StorageSurface, Tuple[int, int]] = {
    # Calibrated 2026-04-05 — frame 1920x1009, depot roi=(768,745,801,264)
    StorageSurface.STASH:       (1037, 17),
    StorageSurface.INBOX:       (1073, 17),
    StorageSurface.STORE_INBOX: (1073, 17),
}

_MANAGE_BTN_OFFSET: Tuple[int, int] = (1113, 17)   # calibrated 2026-04-05


@dataclass
class StorageNavigatorConfig:
    ref_width: int = 1920
    ref_height: int = 1080
    # Seconds to wait after each click before re-detecting
    click_settle_s: float = 0.35
    # Maximum attempts for a single transition
    max_attempts: int = 3
    # Timeout for a full navigate_to() call
    navigate_timeout_s: float = 8.0
    # When True, log every step
    verbose: bool = False


class StorageNavigator:
    """
    Navigate between storage surfaces using vision + input.

    Example::

        nav = StorageNavigator(detector=detector, ctrl=ctrl,
                               frame_getter=session.get_frame)
        ok = nav.navigate_to(StorageSurface.STASH)
        if ok:
            # operate on stash window
    """

    def __init__(
        self,
        *,
        detector: Any,                              # StorageDetector
        ctrl: Any,                                  # InputController
        frame_getter: Callable[[], Optional[Any]],
        config: Optional[StorageNavigatorConfig] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._detector = detector
        self._ctrl = ctrl
        self._frame_getter = frame_getter
        self._cfg = config or StorageNavigatorConfig()
        self._sleep = sleep_fn
        self._log = log_fn or (lambda msg: None)
        self._history: List[StorageTransition] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def current_state(self) -> StorageState:
        """Fresh detection of the current storage state."""
        self._detector.invalidate()
        return self._detector.detect(self._frame_getter())

    def navigate_to(
        self,
        target: StorageSurface,
        *,
        chest_opener: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Navigate from whatever is currently open to *target*.

        Parameters
        ----------
        target
            Desired storage surface.
        chest_opener
            Callable that clicks the depot chest and returns True when the
            container appears.  Required when target is any depot-family surface
            and the depot is not yet open.  Typically: DepotManager._open_chest.

        Returns True if the target surface is confirmed open after navigation.
        """
        deadline = time.monotonic() + self._cfg.navigate_timeout_s
        t0 = time.monotonic()
        state = self.current_state()

        for attempt in range(1, self._cfg.max_attempts + 1):
            if time.monotonic() > deadline:
                self._log(f"[SN] navigate_to({target.value}): timeout")
                break

            if state.has(target):
                elapsed = time.monotonic() - t0
                self._record(state.surface, target, True, elapsed)
                return True

            if self._cfg.verbose:
                self._log(
                    f"[SN] attempt {attempt}: surface={state.surface.value} → target={target.value}"
                )

            ok = self._step(state, target, chest_opener=chest_opener)
            if not ok:
                self._log(
                    f"[SN] transition step failed (attempt {attempt}/{self._cfg.max_attempts})"
                )
                break

            self._sleep(self._cfg.click_settle_s)
            state = self.current_state()

        elapsed = time.monotonic() - t0
        self._record(state.surface, target, False, elapsed, "failed")
        return False

    @property
    def transition_history(self) -> List[StorageTransition]:
        return list(self._history)

    # ── Transition dispatch ───────────────────────────────────────────────────

    def _step(
        self,
        state: StorageState,
        target: StorageSurface,
        *,
        chest_opener: Optional[Callable[[], bool]],
    ) -> bool:
        """Execute one transition step towards *target*."""
        current = state.surface

        # Need depot open first
        if target.is_depot_family and not state.is_depot_open:
            if chest_opener is None:
                self._log("[SN] depot not open and no chest_opener provided")
                return False
            self._log("[SN] opening depot chest …")
            return chest_opener()

        # Depot is open; navigate to sub-surface via tabs / buttons
        depot_window = state.find(StorageSurface.DEPOT_CHEST)
        if depot_window is None:
            # Any open depot-family window can host the tabs
            for s in (StorageSurface.STASH, StorageSurface.INBOX, StorageSurface.STORE_INBOX):
                depot_window = state.find(s)
                if depot_window is not None:
                    break

        if depot_window is None:
            self._log("[SN] no depot window found to navigate from")
            return False

        if target in _DEPOT_TABS:
            return self._click_tab(depot_window, target)

        if target == StorageSurface.MANAGE_CONTAINERS:
            return self._click_manage(depot_window)

        if target == StorageSurface.DEPOT_CHEST:
            # Already handled by the is_depot_open check above — if we're here,
            # depot is open but not classified as DEPOT_CHEST; accept it.
            return True

        self._log(f"[SN] no transition implemented for {current.value} → {target.value}")
        return False

    def _click_tab(self, window: ContainerWindow, target: StorageSurface) -> bool:
        """Click the tab that switches the depot container to *target*."""
        ox, oy = _DEPOT_TABS[target]
        frame = self._frame_getter()
        sx, sy = self._scale(ox, oy, frame)
        tx = window.x + sx
        ty = window.y + sy
        self._log(f"[SN] clicking {target.value} tab at ({tx},{ty})")
        return bool(self._ctrl.click(tx, ty, button="left"))

    def _click_manage(self, window: ContainerWindow) -> bool:
        """Click the 'Manage Containers' button on the depot window."""
        ox, oy = _MANAGE_BTN_OFFSET
        frame = self._frame_getter()
        sx, sy = self._scale(ox, oy, frame)
        tx = window.x + sx
        ty = window.y + sy
        self._log(f"[SN] clicking Manage Containers at ({tx},{ty})")
        return bool(self._ctrl.click(tx, ty, button="left"))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _scale(
        self, ox: int, oy: int, frame: Optional[Any]
    ) -> Tuple[int, int]:
        """Scale reference offsets to actual frame resolution."""
        if frame is None:
            return ox, oy
        fh, fw = frame.shape[:2]
        return (
            int(ox * fw / self._cfg.ref_width),
            int(oy * fh / self._cfg.ref_height),
        )

    def _record(
        self,
        from_s: StorageSurface,
        to_s: StorageSurface,
        success: bool,
        elapsed: float,
        note: str = "",
    ) -> None:
        self._history.append(
            StorageTransition(
                from_surface=from_s,
                to_surface=to_s,
                success=success,
                elapsed_s=round(elapsed, 3),
                note=note,
            )
        )
        if len(self._history) > 200:
            self._history = self._history[-100:]
