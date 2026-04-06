"""
AutoHealer
----------
Background thread that reads HP/MP percentages from OBS frames via
HpMpDetector and fires configurable hotkeys when thresholds are crossed.

Priority levels
~~~~~~~~~~~~~~~
  1. ``emergency`` — HP is critically low (< ``hp_emergency_pct``).
     Uses ``emergency_hotkey_vk`` with a short cooldown.
  2. ``heal``      — HP is below ``hp_threshold_pct``.
     Uses ``heal_hotkey_vk`` with ``heal_cooldown`` seconds between uses.
  3. ``mana``      — MP is below ``mp_threshold_pct``.
     Uses ``mana_hotkey_vk`` with ``mana_cooldown`` seconds between uses.

All three checks fire independently every ``check_interval`` seconds.

Hotkey codes
~~~~~~~~~~~~
Common VK codes (Windows):
  0x70 = F1   0x71 = F2   0x72 = F3   0x73 = F4
  0x74 = F5   0x75 = F6   0x1B = ESC

Usage
~~~~~
    from src.healer import AutoHealer, HealConfig
    from src.hpmp_detector import HpMpDetector, HpMpConfig

    healer = AutoHealer(ctrl=input_controller)
    healer.set_frame_getter(lambda: obs_source.get_frame())
    healer.start()
    ...
    healer.stop()

    # Read current values at any time:
    hp_pct, mp_pct = healer.read_stats()

Integration with ScriptExecutor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ScriptExecutor uses healer.read_stats() to evaluate ``if hp < N`` and
    ``if mp < N`` conditions in .in scripts.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Set, Tuple

import numpy as np

from .hpmp_detector import HpMpDetector, HpMpConfig
from .healer_runtime import (
    get_conditions as runtime_get_conditions,
    read_from_frame as runtime_read_from_frame,
    run_loop as runtime_run_loop,
    tick as runtime_tick,
    try_verify_heal as runtime_try_verify_heal,
    try_verify_mana as runtime_try_verify_mana,
)
from .humanizer import jittered_sleep

try:
    from .action_verifier import verify_hp_changed, verify_mp_changed
except ImportError:  # pragma: no cover
    verify_hp_changed = None  # type: ignore
    verify_mp_changed = None  # type: ignore

# ---------------------------------------------------------------------------
from src.config_paths import COMBAT_CONFIG

HEAL_CONFIG_FILE = COMBAT_CONFIG.parent / ("hc.json" if getattr(__import__('sys'), 'frozen', False) else "heal_config.json")


# ---------------------------------------------------------------------------
@dataclass
class HealConfig:
    """
    Thresholds and hotkeys for the auto-healer.

    Percentages (0-100):
        hp_threshold_pct      – heal when HP falls below this
        hp_emergency_pct      – emergency heal threshold (< hp_threshold)
        mp_threshold_pct      – use mana potion when MP falls below this

    Hotkeys (Windows VK codes, 0 = disabled):
        heal_hotkey_vk        – e.g. 0x70 (F1)
        mana_hotkey_vk        – e.g. 0x71 (F2)
        emergency_hotkey_vk   – e.g. 0x72 (F3) — strong heal / strong health potion

    Cooldowns (seconds):
        heal_cooldown         – min gap between heal casts
        mana_cooldown         – min gap between mana uses
        emergency_cooldown    – min gap for emergency casts (shorter)
        utamo_cooldown        – re-cast interval for utamo vita (≈ spell duration)
        haste_cooldown        – re-cast interval for Auto Hur (≈ buff duration)

    Buff casting:
        utamo_hotkey_vk   – VK code for utamo vita (0 = disabled)
        utamo_min_mp_pct  – minimum MP% required before casting utamo
        haste_hotkey_vk   – VK code for haste spell (0 = disabled)

    check_interval : float
        How often (seconds) the healer loop polls HP/MP.
    """

    # Thresholds (percent)
    hp_threshold_pct:   int   = 70
    hp_emergency_pct:   int   = 30
    mp_threshold_pct:   int   = 30
    heal_min_mp_pct:    int   = 5       # skip heal/emergency when MP < this (spell needs mana)

    # Hotkeys (0 = disabled)
    heal_hotkey_vk:     int   = 0x70   # F1
    mana_hotkey_vk:     int   = 0x71   # F2
    emergency_hotkey_vk: int  = 0x72   # F3

    # Cooldowns
    heal_cooldown:      float = 1.0
    mana_cooldown:      float = 1.2
    emergency_cooldown: float = 1.0
    cooldown_jitter:    float = 0.25   # ±25% random variation on cooldown checks

    # ── Buff casting ───────────────────────────────────────────────────────
    # Utamo vita: protective spell cast when MP >= threshold
    utamo_hotkey_vk:  int   = 0       # 0 = disabled
    utamo_min_mp_pct: int   = 95      # cast only when mp >= N%
    utamo_cooldown:   float = 22.0    # re-cast interval (spell lasts ~21 s)

    # Auto Hur: renew haste buff when not active and no combat
    haste_hotkey_vk:  int   = 0       # 0 = disabled
    haste_cooldown:   float = 16.0    # re-cast interval (buff lasts ~15 s)

    # Polling
    check_interval:     float = 0.15

    # HpMpDetector config path (forwarded to detector)
    hpmp_config_path:   str   = ""

    def save(self, path: Path = HEAL_CONFIG_FILE) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2)

    def validate(self) -> None:
        """Raise ``ValueError`` when any field value is out of range.

        Called automatically by :meth:`load` after deserialisation so that malformed
        JSON files are caught early with a clear error message.
        """
        for _f in ("hp_threshold_pct", "hp_emergency_pct", "mp_threshold_pct", "utamo_min_mp_pct"):
            _v = getattr(self, _f)
            if not (0 <= _v <= 100):
                raise ValueError(f"HealConfig.{_f}={_v} must be in [0, 100]")
        for _f in ("heal_cooldown", "mana_cooldown", "emergency_cooldown",
                   "utamo_cooldown", "haste_cooldown", "check_interval"):
            _v = getattr(self, _f)
            if _v < 0:
                raise ValueError(f"HealConfig.{_f}={_v} must be >= 0")
        for _f in ("heal_hotkey_vk", "mana_hotkey_vk", "emergency_hotkey_vk",
                   "utamo_hotkey_vk", "haste_hotkey_vk"):
            _v = getattr(self, _f)
            if not (0 <= _v <= 0xFFFF):
                raise ValueError(f"HealConfig.{_f}=0x{_v:x} must be in [0x0000, 0xFFFF]")
        if not (0.0 <= self.cooldown_jitter < 1.0):
            raise ValueError(
                f"HealConfig.cooldown_jitter={self.cooldown_jitter} must be in [0.0, 1.0)"
            )
        if self.hp_threshold_pct > 0 and self.hp_emergency_pct >= self.hp_threshold_pct:
            raise ValueError(
                f"HealConfig.hp_emergency_pct={self.hp_emergency_pct} "
                f"must be < hp_threshold_pct={self.hp_threshold_pct}"
            )

    @classmethod
    def load(cls, path: Path = HEAL_CONFIG_FILE) -> "HealConfig":
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        obj = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        obj.validate()
        return obj

    @property
    def is_emergency_below_heal(self) -> bool:
        """True when the emergency threshold is strictly lower than the normal heal threshold.

        This is the expected invariant: ``hp_emergency_pct < hp_threshold_pct``.
        """
        return self.hp_emergency_pct < self.hp_threshold_pct

    @property
    def has_emergency_hotkey(self) -> bool:
        """True when an emergency hotkey is configured (``emergency_hotkey_vk != 0``)."""
        return self.emergency_hotkey_vk != 0


# ---------------------------------------------------------------------------
class AutoHealer:
    """
    Background healer thread.

    Parameters
    ----------
    ctrl : InputController
        For firing hotkeys via ``press_key(vk_code)``.
    config : HealConfig, optional
        Healer configuration. Loaded from ``heal_config.json`` if not given.
    frame_getter : callable, optional
        Zero-argument function returning the current OBS frame (BGR ndarray
        or None). If None, the healer cannot read HP/MP values.
    detector : HpMpDetector, optional
        Pre-built detector. One is created from ``HpMpConfig`` defaults if
        not provided.
    """

    def __init__(
        self,
        ctrl: Any,
        config: Optional[HealConfig] = None,
        frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None,
        detector: Optional[HpMpDetector] = None,
        verify_heals: bool = False,
    ) -> None:
        self._ctrl         = ctrl
        self._cfg          = config or HealConfig.load()
        self._frame_getter = frame_getter
        self._detector     = detector or HpMpDetector()
        self._log_cb:      Optional[Callable[[str], None]] = None

        # ── Verification flag ──────────────────────────────────────────────
        # When True and verify_hp_changed/verify_mp_changed are available,
        # the healer checks after each cast whether HP/MP actually moved.
        self._verify_heals = verify_heals
        self._heal_verify_fails: int = 0
        self._mana_verify_fails: int = 0

        # Thread state
        self._running  = False
        self._thread:  Optional[threading.Thread] = None

        # Timestamps of last heal action (for cooldown enforcement).
        # Initialised to -inf so the first tick always passes the cooldown
        # check regardless of system uptime (time.monotonic() epoch).
        self._last_heal:      float = -math.inf
        self._last_mana:      float = -math.inf
        self._last_emergency: float = -math.inf
        self._last_utamo:     float = -math.inf
        self._last_haste:     float = -math.inf

        # Cached readings (written by background thread, read by external code)
        self._hp_pct: float = 100.0
        self._mp_pct: float = 100.0
        self._lock = threading.Lock()

        # Pause flag — loop keeps running but skips _tick() while paused
        self._paused: bool = False

        # Optional callbacks fired when a hotkey is pressed
        self.on_heal:      Optional[Callable[[], None]] = None
        self.on_mana:      Optional[Callable[[], None]] = None
        self.on_emergency: Optional[Callable[[], None]] = None

        # Action counters — incremented each time the hotkey is fired
        self._heals_done:     int = 0
        self._mana_uses:      int = 0
        self._emergency_uses: int = 0
        self._utamo_casts:    int = 0
        self._haste_casts:    int = 0

        # Optional getter: returns set of active condition names
        # e.g. {"poison", "haste", "battle"}
        # Wire this to a ConditionDetector to enable buff-aware casts.
        self._conditions_getter: Optional[Callable[[], Set[str]]] = None

        # Defensive counters (background thread only)
        self._zero_hp_streak: int = 0
        self._read_fail_count: int = 0
        self._no_frame_getter_warned: bool = False  # one-time warning when frame_getter is None
        self._no_conditions_warned: bool = False    # one-time warning when conditions_getter is None

        # HP/MP blind alarm — emitted when detector fails continuously > threshold
        self._event_bus: Optional[Any] = None
        self._blind_alarm_s: float = 30.0   # seconds of consecutive failure → alarm
        self._blind_since: float = 0.0      # monotonic time when blind streak started
        self._blind_alarm_fired: bool = False  # rate-limit: one alarm per blind episode

        # Runtime injections preserved at src.healer module level for tests.
        self._runtime_time = time
        self._runtime_random = random
        self._runtime_verify_hp_changed = verify_hp_changed
        self._runtime_verify_mp_changed = verify_mp_changed

    # -----------------------------------------------------------------------
    # Configuration helpers
    # -----------------------------------------------------------------------

    def set_frame_getter(self, fn: Callable[[], Optional[np.ndarray]]) -> None:
        self._frame_getter = fn

    def set_detector(self, det: HpMpDetector) -> None:
        self._detector = det

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self._log_cb = cb

    def set_event_bus(self, bus: Any) -> None:
        """Register EventBus for ``healer_blind`` / ``healer_recovered`` events."""
        self._event_bus = bus

    def set_conditions_getter(self, fn: Callable[[], Set[str]]) -> None:
        """Register a callable that returns the set of currently active condition names.

        Used by Auto Hur to detect whether the haste buff is still active
        (skipping the re-cast) and to suppress haste during combat.

        Example integration with :class:`~src.condition_monitor.ConditionDetector`::

            detector = ConditionDetector(config)
            healer.set_conditions_getter(
                lambda: detector.detect(frame_getter())
            )
        """
        self._conditions_getter = fn

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the background healer thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("  [H] ✓ module H started")

    def stop(self) -> None:
        """Stop the background healer thread and wait for it to exit."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        self._log("  [H] module H stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def read_stats(self) -> Tuple[float, float]:
        """
        Return the latest ``(hp_pct, mp_pct)`` readings (0.0–100.0).

        Values are updated every ``check_interval`` seconds by the background
        thread. Returns ``(100.0, 100.0)`` if no frame source is set.
        """
        with self._lock:
            return self._hp_pct, self._mp_pct

    def read_hp(self) -> float:
        """Return latest HP percentage (0–100)."""
        with self._lock:
            return self._hp_pct

    def read_mp(self) -> float:
        """Return latest MP percentage (0–100)."""
        with self._lock:
            return self._mp_pct

    def force_heal(self) -> bool:
        """
        Immediately fire the heal hotkey (ignoring cooldown).
        Returns True if the key was sent.
        """
        return self._press_heal(force=True)

    def force_mana(self) -> bool:
        """Immediately fire the mana hotkey (ignoring cooldown)."""
        return self._press_mana(force=True)

    # -----------------------------------------------------------------------
    # Pause / resume
    # -----------------------------------------------------------------------

    def pause(self) -> None:
        """Temporarily suspend HP/MP checks without stopping the thread."""
        self._paused = True
        self._log("  [H] ⏸ module H paused")

    def resume(self) -> None:
        """Resume HP/MP checks after a pause()."""
        self._paused = False
        self._log("  [H] ▶ module H resumed")

    @property
    def is_paused(self) -> bool:
        """True while the healer is paused (thread alive, checks suspended)."""
        return self._paused

    # -----------------------------------------------------------------------
    # Runtime configuration update
    # -----------------------------------------------------------------------

    def update_config(self, config: HealConfig) -> None:
        """
        Hot-swap thresholds/hotkeys/cooldowns without restarting the thread.
        Safe to call from any thread.
        """
        self._cfg = config
        self._log("  [H] ↺ Configuración actualizada")

    # -----------------------------------------------------------------------
    # Cooldown reset
    # -----------------------------------------------------------------------

    def reset_cooldowns(self) -> None:
        """Reset all last-action timestamps so hotkeys can fire immediately."""
        with self._lock:
            self._last_heal      = -math.inf
            self._last_mana      = -math.inf
            self._last_emergency = -math.inf
            self._last_utamo     = -math.inf
            self._last_haste     = -math.inf

    def reset_heal_counts(self) -> None:
        """Zero all action counters (heals_done, mana_uses, emergency_uses)."""
        with self._lock:
            self._heals_done     = 0
            self._mana_uses      = 0
            self._emergency_uses = 0
            self._utamo_casts    = 0
            self._haste_casts    = 0

    # -----------------------------------------------------------------------
    # Counter properties
    # -----------------------------------------------------------------------

    @property
    def heals_done(self) -> int:
        """Number of times the heal hotkey has been fired since last reset."""
        return self._heals_done

    @property
    def mana_uses(self) -> int:
        """Number of times the mana hotkey has been fired since last reset."""
        return self._mana_uses

    @property
    def emergency_uses(self) -> int:
        """Number of times the emergency hotkey has been fired since last reset."""
        return self._emergency_uses

    @property
    def utamo_casts(self) -> int:
        """Number of times the utamo (protective spell) hotkey has been fired."""
        return self._utamo_casts

    @property
    def haste_casts(self) -> int:
        """Number of times the haste (speed buff) hotkey has been fired."""
        return self._haste_casts

    @property
    def heal_verify_fails(self) -> int:
        """Times a heal was fired but HP didn't increase (verify_heals mode)."""
        return self._heal_verify_fails

    @property
    def mana_verify_fails(self) -> int:
        """Times a mana pot was fired but MP didn't increase (verify_heals mode)."""
        return self._mana_verify_fails

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    def stats_snapshot(self) -> dict[str, Any]:
        """
        Return a point-in-time snapshot of all healer state as a plain dict.

        Keys
        ----
        hp_pct, mp_pct, hp_low, mp_low, is_running, is_paused,
        last_heal, last_mana, last_emergency
        """
        with self._lock:
            hp = self._hp_pct
            mp = self._mp_pct
            last_heal = self._last_heal
            last_mana = self._last_mana
            last_emergency = self._last_emergency
            heals_done = self._heals_done
            mana_uses = self._mana_uses
            emergency_uses = self._emergency_uses
            heal_verify_fails = self._heal_verify_fails
            mana_verify_fails = self._mana_verify_fails
        return {
            "hp_pct":          hp,
            "mp_pct":          mp,
            "hp_low":          hp < self._cfg.hp_threshold_pct,
            "mp_low":          mp < self._cfg.mp_threshold_pct,
            "is_running":      self._running,
            "is_paused":       self._paused,
            "last_heal":       last_heal,
            "last_mana":       last_mana,
            "last_emergency":  last_emergency,
            "heals_done":      heals_done,
            "mana_uses":       mana_uses,
            "emergency_uses":  emergency_uses,
            "heal_verify_fails": heal_verify_fails,
            "mana_verify_fails": mana_verify_fails,
        }

    # -----------------------------------------------------------------------
    # Convenience properties
    # -----------------------------------------------------------------------

    @property
    def hp_low(self) -> bool:
        """True when the latest HP reading is below ``hp_threshold_pct``."""
        with self._lock:
            return self._hp_pct < self._cfg.hp_threshold_pct

    @property
    def mp_low(self) -> bool:
        """True when the latest MP reading is below ``mp_threshold_pct``."""
        with self._lock:
            return self._mp_pct < self._cfg.mp_threshold_pct

    @property
    def total_actions(self) -> int:
        """Sum of heals_done + mana_uses + emergency_uses since last reset."""
        return self._heals_done + self._mana_uses + self._emergency_uses

    @property
    def has_frame_getter(self) -> bool:
        """True when a frame getter callable has been registered."""
        return self._frame_getter is not None

    @property
    def has_detector(self) -> bool:
        """True when an HpMpDetector has been registered."""
        return self._detector is not None

    @property
    def has_log_callback(self) -> bool:
        """True when a log callback has been registered via ``set_log_callback``."""
        return self._log_cb is not None

    @property
    def has_used_heal(self) -> bool:
        """True when at least one HP heal has been triggered this session."""
        return self._heals_done > 0

    @property
    def has_used_mana(self) -> bool:
        """True when at least one mana spell has been used this session."""
        return self._mana_uses > 0

    @property
    def has_emergency_uses(self) -> bool:
        """True when at least one emergency heal has been triggered this session."""
        return self._emergency_uses > 0

    @property
    def is_healing_enabled(self) -> bool:
        """True when a heal hotkey is configured (``heal_hotkey_vk != 0``)."""
        return self._cfg.heal_hotkey_vk != 0

    @property
    def is_mana_enabled(self) -> bool:
        """True when a mana hotkey is configured (``mana_hotkey_vk != 0``)."""
        return self._cfg.mana_hotkey_vk != 0

    @property
    def is_emergency_enabled(self) -> bool:
        """True when an emergency hotkey is configured (``emergency_hotkey_vk != 0``)."""
        return self._cfg.emergency_hotkey_vk != 0

    # -----------------------------------------------------------------------
    # Internal loop
    # -----------------------------------------------------------------------

    def _loop(self) -> None:
        runtime_run_loop(self, jittered_sleep_fn=jittered_sleep)

    def _tick(self) -> None:
        runtime_tick(
            self,
            time_module=time,
            random_module=random,
            jittered_sleep_fn=jittered_sleep,
        )

    def _try_verify_heal(self, old_hp: float) -> None:
        runtime_try_verify_heal(self, old_hp, verify_hp_changed_fn=verify_hp_changed)

    def _try_verify_mana(self, old_mp: float) -> None:
        runtime_try_verify_mana(self, old_mp, verify_mp_changed_fn=verify_mp_changed)

    def _get_conditions(self) -> Set[str]:
        return runtime_get_conditions(self)

    def _read_from_frame(self) -> Tuple[float, float]:
        return runtime_read_from_frame(self, time_module=time)

    def _press_heal(self, force: bool = False) -> bool:
        if not self._cfg.heal_hotkey_vk:
            return False
        now = time.monotonic()
        with self._lock:
            if not force and (now - self._last_heal) < self._cfg.heal_cooldown:
                return False
            self._last_heal = now
        self._ctrl.press_key(self._cfg.heal_hotkey_vk)
        with self._lock:
            self._heals_done += 1
        return True

    def _press_mana(self, force: bool = False) -> bool:
        if not self._cfg.mana_hotkey_vk:
            return False
        now = time.monotonic()
        with self._lock:
            if not force and (now - self._last_mana) < self._cfg.mana_cooldown:
                return False
            self._last_mana = now
        self._ctrl.press_key(self._cfg.mana_hotkey_vk)
        with self._lock:
            self._mana_uses += 1
        return True

    def _log(self, msg: str) -> None:
        if self._log_cb:
            self._log_cb(msg)
        else:
            print(msg)


# ---------------------------------------------------------------------------
@dataclass
class FriendHealConfig:
    """Configuration for healing nearby party members (Exura Sio / Gran Sio).

    Percentages (0-100):
        sio_threshold_pct      – cast Exura Sio when friend HP < this
        gran_sio_threshold_pct – cast Exura Gran Sio when friend HP < this (priority)

    Hotkeys (Windows VK codes, 0 = disabled):
        sio_hotkey_vk       – hotkey for Exura Sio
        gran_sio_hotkey_vk  – hotkey for Exura Gran Sio

    check_interval : float
        Polling interval (seconds).
    """

    # Thresholds (percent)
    sio_threshold_pct:      int   = 70
    gran_sio_threshold_pct: int   = 40

    # Hotkeys (0 = disabled)
    sio_hotkey_vk:          int   = 0
    gran_sio_hotkey_vk:     int   = 0

    # Cooldowns
    sio_cooldown:           float = 1.5
    gran_sio_cooldown:      float = 1.5

    # Polling
    check_interval:         float = 0.2


# ---------------------------------------------------------------------------
class FriendHealer:
    """Background thread that heals a single party member via callbacks.

    Usage::

        cfg = FriendHealConfig(sio_hotkey_vk=0x73, gran_sio_hotkey_vk=0x74)
        healer = FriendHealer(ctrl, cfg)
        healer.set_friend_hp_getter(lambda: detector.friend_hp_pct)
        healer.start()
        # ... later ...
        healer.stop()
    """

    def __init__(
        self,
        ctrl: Any,
        config: Optional[FriendHealConfig] = None,
    ) -> None:
        self._ctrl = ctrl
        self._cfg  = config or FriendHealConfig()
        self._log_cb: Optional[Callable[[str], None]] = None

        # Thread state
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Cooldown timestamps
        self._last_sio:      float = 0.0
        self._last_gran_sio: float = 0.0

        # Action counters
        self._sio_casts:      int = 0
        self._gran_sio_casts: int = 0

        # Getter returning friend HP% (0-100). Wire to detector.
        self._friend_hp_getter: Optional[Callable[[], float]] = None

    # -----------------------------------------------------------------------
    # Configuration helpers
    # -----------------------------------------------------------------------

    def set_friend_hp_getter(self, fn: Callable[[], float]) -> None:
        """Register a callable that returns the party member's current HP%."""
        self._friend_hp_getter = fn

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        self._log_cb = cb

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the background friend-healer thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}"
        )
        self._thread.start()
        self._log("  [FH] ✓ FriendHealer iniciado")

    def stop(self) -> None:
        """Stop the background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        self._log("  [FH] FriendHealer detenido")

    @property
    def sio_casts(self) -> int:
        return self._sio_casts

    @property
    def gran_sio_casts(self) -> int:
        return self._gran_sio_casts

    def reset_counts(self) -> None:
        self._sio_casts = 0
        self._gran_sio_casts = 0

    # -----------------------------------------------------------------------
    # Internal loop
    # -----------------------------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as exc:  # pragma: no cover
                self._log(f"  [FH] Error en _tick: {exc}")
            jittered_sleep(self._cfg.check_interval)

    def _tick(self) -> None:
        if self._friend_hp_getter is None:
            return
        try:
            hp = float(self._friend_hp_getter())
        except Exception:
            return

        now = time.monotonic()

        # Gran Sio takes priority (lower threshold = critical HP)
        if (
            self._cfg.gran_sio_hotkey_vk
            and hp < self._cfg.gran_sio_threshold_pct
            and (now - self._last_gran_sio) >= self._cfg.gran_sio_cooldown
        ):
            self._log(
                f"  [FH] HP amigo={hp:.0f}% < {self._cfg.gran_sio_threshold_pct}%"
                " → exura gran sio"
            )
            self._ctrl.press_key(self._cfg.gran_sio_hotkey_vk)
            self._last_gran_sio = now
            self._gran_sio_casts += 1
            return

        # Regular Sio
        if (
            self._cfg.sio_hotkey_vk
            and hp < self._cfg.sio_threshold_pct
            and (now - self._last_sio) >= self._cfg.sio_cooldown
        ):
            self._log(
                f"  [FH] HP amigo={hp:.0f}% < {self._cfg.sio_threshold_pct}%"
                " → exura sio"
            )
            self._ctrl.press_key(self._cfg.sio_hotkey_vk)
            self._last_sio = now
            self._sio_casts += 1

    def _log(self, msg: str) -> None:
        if self._log_cb:
            self._log_cb(msg)
        else:
            print(msg)
