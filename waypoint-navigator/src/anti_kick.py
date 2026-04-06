"""
AntiKick
--------
Prevents the Tibia AFK-kick timer from expiring by periodically sending
innocuous input (mouse jitter, camera rotation, or harmless hotkey presses)
when the idle detected for too long.

Tibia's AFK kick triggers after ~15 minutes of *zero* input.  As long as
the bot is actively walking or healing, anti-kick is unnecessary.  It only
needs to fire during long waits (depot cycles, NPC conversations, etc.).

Integration::

    from src.anti_kick import AntiKick, AntiKickConfig

    ak = AntiKick(ctrl=input_ctrl)
    ak.start()

    # When bot does real input, notify so anti-kick resets its timer:
    ak.notify_activity()

    # When done:
    ak.stop()
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger("wn.ak")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class AntiKickConfig:
    """Tuneable parameters for anti-kick.

    idle_threshold : float
        Seconds of inactivity before anti-kick fires (default 300 s = 5 min).
        Should be well under Tibia's 15-min AFK limit.
    action_interval : float
        Seconds between anti-kick actions once idle (default 60 s).
    method : str
        What to send: ``'mouse_jitter'`` (default), ``'camera_rotate'``,
        ``'hotkey'``, or ``'any'`` (random pick each time).
    camera_hotkey_vk : int
        VK code for camera rotation key (default Home/End, or arrow keys).
    enabled : bool
        Master switch (default True).
    """

    idle_threshold: float = 300.0
    action_interval: float = 60.0
    method: str = "mouse_jitter"
    camera_hotkey_vk: int = 0  # 0 = use mouse jitter only
    enabled: bool = True


# ---------------------------------------------------------------------------
# AntiKick
# ---------------------------------------------------------------------------

class AntiKick:
    """Background thread that prevents AFK kicks by sending periodic micro-inputs.

    Parameters
    ----------
    ctrl : InputController
        For sending mouse movements or hotkeys.
    config : AntiKickConfig, optional
    """

    def __init__(
        self,
        ctrl: Any,
        config: Optional[AntiKickConfig] = None,
    ) -> None:
        self._ctrl = ctrl
        self._cfg = config or AntiKickConfig()
        self._log_cb: Optional[Callable[[str], None]] = None

        # State
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_activity: float = time.monotonic()
        self._actions_sent: int = 0

    # ── Configuration ─────────────────────────────────────────────────────────

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self._log_cb = cb

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        with self._lock:
            self._last_activity = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("[K] Started")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            sent = self._actions_sent
        self._log(f"[K] Stopped — actions={sent}")

    def pause(self) -> None:
        """Temporarily suspend anti-kick without destroying the thread."""
        self._running = False
        self._log("[K] Paused")

    def resume(self) -> None:
        """Resume anti-kick after pause.  Re-creates thread if needed."""
        if self._running:
            return
        with self._lock:
            self._last_activity = time.monotonic()
        # Thread may have exited because _running was set to False.
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name=f"t-{id(self):x}"
            )
            self._thread.start()
        else:
            self._running = True
        self._log("[K] Resumed")

    def notify_activity(self) -> None:
        """Reset the idle timer.  Call whenever the bot sends real input."""
        with self._lock:
            self._last_activity = time.monotonic()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def actions_sent(self) -> int:
        with self._lock:
            return self._actions_sent

    def reset_counters(self) -> None:
        with self._lock:
            self._actions_sent = 0

    def stats_snapshot(self) -> dict[str, Any]:
        with self._lock:
            idle = time.monotonic() - self._last_activity
            return {
                "running": self._running,
                "idle_secs": round(idle, 1),
                "actions_sent": self._actions_sent,
            }

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            if self._cfg.enabled:
                with self._lock:
                    idle = time.monotonic() - self._last_activity
                if idle >= self._cfg.idle_threshold:
                    self._send_anti_kick()
                    with self._lock:
                        self._last_activity = time.monotonic()
            _base = min(self._cfg.action_interval, 5.0)
            if not self._sleep(_base * random.uniform(0.8, 1.3)):
                break

    def _send_anti_kick(self) -> None:
        method = self._cfg.method
        if method == "any":
            method = random.choice(["mouse_jitter", "camera_rotate"])

        try:
            if method == "mouse_jitter":
                self._mouse_jitter()
            elif method == "camera_rotate":
                self._camera_rotate()
            elif method == "hotkey" and self._cfg.camera_hotkey_vk:
                self._ctrl.press_key(self._cfg.camera_hotkey_vk)
            else:
                self._mouse_jitter()

            with self._lock:
                self._actions_sent += 1
                sent = self._actions_sent
            self._log(f"[K] Sent {method} (total={sent})")
        except Exception as exc:
            logger.warning("[K] send error: %s", exc)

    def _mouse_jitter(self) -> None:
        """Send a tiny mouse movement that doesn't affect gameplay."""
        # Try relative mouse move first
        move_fn = getattr(self._ctrl, "move_mouse_relative", None)
        if move_fn is not None:
            dx = random.randint(1, 7) * random.choice([-1, 1])
            dy = random.randint(1, 7) * random.choice([-1, 1])
            move_fn(dx, dy)
            if not self._sleep(random.uniform(0.06, 0.25)):
                return
            # Return close but NOT to the exact same pixel — humans never do
            ret_dx = -dx + random.choice([-1, 0, 1])
            ret_dy = -dy + random.choice([-1, 0, 1])
            move_fn(ret_dx, ret_dy)
        else:
            # Fallback: press a harmless key (ScrollLock toggles nothing visible in Tibia)
            # 0x91 = VK_SCROLL — BattlEye won't flag an occasional ScrollLock
            self._ctrl.press_key(0x91)

    def _camera_rotate(self) -> None:
        """Send camera rotation keys (Ctrl+Left, then Ctrl+Right) to look busy."""
        vk = self._cfg.camera_hotkey_vk
        if vk:
            self._ctrl.press_key(vk)
        else:
            # Use mouse jitter as fallback
            self._mouse_jitter()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sleep(self, secs: float) -> bool:
        return not self._stop_event.wait(max(0.0, secs))

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            self._log_cb(msg)
        else:
            logger.info(msg)
