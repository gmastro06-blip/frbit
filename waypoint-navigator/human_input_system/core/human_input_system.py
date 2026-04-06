"""HumanInputSystem — orquestador principal del sistema de humanización."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

from ..config.models import Configuration
from ..config.parser import ConfigurationParser
from .arduino_hid_controller import ArduinoHIDController
from .behavior_simulator import BehaviorSimulator
from .metrics_collector import MetricsCollector
from .mouse_movement_engine import MouseMovementEngine
from .profile_manager import ProfileManager
from .timing_humanizer import TimingHumanizer

_log = logging.getLogger(__name__)


class HumanInputSystem:
    """Middleware transparente entre el bot y el InputController.

    Aplica humanización en capas: timing → comportamiento → mouse → HID.
    Interfaz compatible con ``InputController`` existente.
    """

    def __init__(self, config_path: str, input_controller: Any) -> None:
        self._ic = input_controller  # InputController
        self._config_path = config_path

        # Parse configuration
        self._parser = ConfigurationParser(config_path)
        self._cfg: Configuration = self._parser.parse()

        # Core components
        self._timing = TimingHumanizer(self._cfg.timing)
        self._behavior = BehaviorSimulator(self._cfg.behavior)
        self._mouse = MouseMovementEngine(self._cfg.mouse)

        # Profile manager
        self._profiles = ProfileManager(self._parser)
        self._profiles.load_profiles()
        self._profiles.apply_circadian_adjustments()

        # Arduino HID (optional)
        self._arduino = ArduinoHIDController(self._cfg.arduino, self._ic)
        self._arduino.initialize()

        # Metrics — resolve log_dir relative to config file location
        log_dir = self._cfg.log_directory
        if not os.path.isabs(log_dir):
            log_dir = os.path.join(os.path.dirname(os.path.abspath(config_path)), log_dir)
        os.makedirs(log_dir, exist_ok=True)
        self._metrics = MetricsCollector(log_dir)

        # State
        self._enabled = self._cfg.enable_humanization
        self._last_input_ts = time.monotonic()
        self._afk_thread: Optional[threading.Thread] = None
        self._in_afk = False

        _log.info(
            "[HIS] Inicializado — humanización=%s, arduino=%s, perfil=%s",
            self._enabled,
            self._arduino.is_available(),
            self._cfg.active_profile,
        )

    # ------------------------------------------------------------------
    # Public interface — compatible with InputController
    # ------------------------------------------------------------------

    def press_key(self, vk: int, delay: Optional[float] = None) -> bool:
        """Presiona tecla con humanización completa."""
        self._update_fatigue()

        if not self._enabled:
            return self._ic.press_key(vk, delay)

        # Check for AFK pause (non-blocking, runs in background)
        if not self._in_afk and self._behavior.should_trigger_afk_pause():
            self._start_afk_pause()

        # Error generation
        actual_vk = vk
        had_error = False
        error_type = self._behavior.should_generate_error()
        if error_type:
            had_error = True
            self._metrics.record_error(error_type)
            if error_type == "wrong_key":
                wrong_vk = self._behavior.apply_wrong_key_error_vk(vk)
                if wrong_vk != vk:
                    # Press wrong key, brief pause, then press correct key
                    dur = self._get_key_duration()
                    self._ic.press_key(wrong_vk, dur / 1000.0)
                    time.sleep(self._timing.get_micro_pause() / 1000.0)
            elif error_type == "double_press":
                # Extra press before the real one
                dp_delay = self._behavior.apply_double_press_error() / 1000.0
                dur = self._get_key_duration()
                self._ic.press_key(vk, dur / 1000.0)
                time.sleep(dp_delay)
            elif error_type == "hesitation":
                hes = self._behavior.apply_hesitation_delay() / 1000.0
                time.sleep(hes)

        # Timing — reduced reaction for responsiveness
        fatigue = self._behavior.get_fatigue_level()
        reaction = self._timing.get_micro_pause() / 1000.0
        time.sleep(reaction)

        duration_ms = self._get_key_duration(delay)
        duration_s = duration_ms / 1000.0

        # Execute — always press the CORRECT key
        result = self._execute_key_press(vk, duration_s)

        # Metrics
        self._metrics.record_key_press(
            key=str(vk),
            duration=duration_ms,
            reaction_time=reaction * 1000.0,
            had_error=had_error,
        )
        self._last_input_ts = time.monotonic()
        return result

    def hold_key(self, vk: int, duration: float = 0.3) -> bool:
        """Mantiene tecla pulsada con humanización."""
        self._update_fatigue()

        if not self._enabled:
            return self._ic.hold_key(vk, duration)

        fatigue = self._behavior.get_fatigue_level()
        reaction = self._timing.get_reaction_time(fatigue) / 1000.0
        time.sleep(reaction)

        # Fatigue adjusts hold duration slightly
        adjusted = duration * (1.0 + fatigue * 0.2)
        adjusted = self._timing.add_jitter(adjusted * 1000.0) / 1000.0

        result = self._ic.hold_key(vk, adjusted)
        self._last_input_ts = time.monotonic()
        return result

    def type_text(self, text: str) -> bool:
        """Escribe texto carácter a carácter con timing variable."""
        self._update_fatigue()

        if not self._enabled:
            return self._ic.type_text(text)

        fatigue = self._behavior.get_fatigue_level()
        reaction = self._timing.get_reaction_time(fatigue) / 1000.0
        time.sleep(reaction)

        # Type character by character with humanized pauses
        for ch in text:
            dur = self._timing.get_key_press_duration(fatigue) / 1000.0
            pause = self._timing.get_micro_pause() / 1000.0
            self._ic.press_key(ord(ch.upper()), dur)
            time.sleep(pause)

        self._last_input_ts = time.monotonic()
        return True

    def click(self, x: int, y: int, button: str = "left") -> bool:
        """Click con movimiento de mouse humanizado."""
        self._update_fatigue()

        if not self._enabled:
            return self._ic.click(x, y, button)

        # Check for AFK pause (non-blocking)
        if not self._in_afk and self._behavior.should_trigger_afk_pause():
            self._start_afk_pause()

        # Error: miss-click offset
        target_x, target_y = x, y
        had_error = False
        error_type = self._behavior.should_generate_error()
        if error_type:
            had_error = True
            self._metrics.record_error(error_type)
            if error_type == "miss_click":
                target_x, target_y = self._behavior.apply_miss_click_offset(x, y)
            elif error_type == "hesitation":
                hes = self._behavior.apply_hesitation_delay() / 1000.0
                time.sleep(hes)

        # Micro-pause (not full reaction time — keeps clicks responsive)
        fatigue = self._behavior.get_fatigue_level()
        pause = self._timing.get_micro_pause() / 1000.0
        time.sleep(pause)

        # Execute click via InputController (it handles mouse positioning)
        result = self._ic.click(target_x, target_y, button)

        self._metrics.record_key_press(
            key=f"click_{button}",
            duration=0,
            reaction_time=pause * 1000.0,
            had_error=had_error,
        )
        self._last_input_ts = time.monotonic()
        return result

    def shift_click(self, x: int, y: int) -> bool:
        """Shift+click con humanización."""
        self._update_fatigue()

        if not self._enabled:
            return self._ic.shift_click(x, y)

        fatigue = self._behavior.get_fatigue_level()
        reaction = self._timing.get_reaction_time(fatigue) / 1000.0
        time.sleep(reaction)

        result = self._ic.shift_click(x, y)
        self._last_input_ts = time.monotonic()
        return result

    def click_human(self, x: int, y: int, button: str = "left") -> bool:
        """Click con movimiento de mouse Bézier humanizado."""
        self._update_fatigue()

        if not self._enabled:
            return self._ic.click_human(x, y, button)

        fatigue = self._behavior.get_fatigue_level()
        reaction = self._timing.get_reaction_time(fatigue) / 1000.0
        time.sleep(reaction)

        # Error: miss-click
        target_x, target_y = x, y
        error_type = self._behavior.should_generate_error()
        if error_type == "miss_click":
            target_x, target_y = self._behavior.apply_miss_click_offset(x, y)
            self._metrics.record_error("miss_click")

        result = self._ic.click_human(target_x, target_y, button)
        self._last_input_ts = time.monotonic()
        return result

    def click_absolute(self, x: int, y: int, button: str = "left") -> bool:
        """Click absoluto con humanización."""
        self._update_fatigue()

        if not self._enabled:
            return self._ic.click_absolute(x, y, button)

        fatigue = self._behavior.get_fatigue_level()
        reaction = self._timing.get_reaction_time(fatigue) / 1000.0
        time.sleep(reaction)

        result = self._ic.click_absolute(x, y, button)
        self._last_input_ts = time.monotonic()
        return result

    def move_mouse(self, x: int, y: int, from_pos: Optional[Tuple[int, int]] = None) -> bool:
        """Mueve el mouse con trayectoria Bézier humanizada.

        Si se proporciona *from_pos*, usa esas coordenadas como inicio.
        Si no, usa (0, 0) como referencia (la posición real depende del context).
        """
        self._update_fatigue()

        if not self._enabled:
            # Fallback: click directo (sin movimiento intermedio)
            return True

        start = from_pos or (0, 0)
        fatigue = self._behavior.get_fatigue_level()
        dist = math.hypot(x - start[0], y - start[1])
        total_ms = self._timing.get_movement_duration(dist, fatigue)
        path = self._mouse.generate_full_movement(start, (x, y))

        if not path:
            return True

        # Velocity profile → inter-point delays
        velocity = self._mouse.calculate_velocity_profile(len(path))
        base_delay = (total_ms / len(path)) / 1000.0

        for i, (px, py) in enumerate(path):
            if self._arduino.is_available():
                self._arduino.send_mouse_move(px, py)
            # Else: caller uses the path externally or InputController has
            # its own move function. We primarily record metrics.
            v = velocity[i] if i < len(velocity) else 1.0
            wait = base_delay / max(v, 0.1)
            time.sleep(wait)

        self._metrics.record_mouse_movement(
            start=start, end=(x, y), duration=total_ms, path_length=len(path)
        )
        self._last_input_ts = time.monotonic()
        return True

    # ------------------------------------------------------------------
    # Profile / config management
    # ------------------------------------------------------------------

    def set_profile(self, profile_name: str) -> bool:
        return self._profiles.set_active_profile(profile_name)

    def reload_config(self) -> bool:
        """Recarga configuración YAML sin reiniciar."""
        try:
            self._cfg = self._parser.reload()
            self._timing = TimingHumanizer(self._cfg.timing)
            self._behavior = BehaviorSimulator(self._cfg.behavior)
            self._mouse = MouseMovementEngine(self._cfg.mouse)
            _log.info("[HIS] Configuración recargada")
            return True
        except Exception as exc:
            _log.error(f"[HIS] Error recargando config: {exc}")
            return False

    def get_metrics(self) -> Dict[str, Any]:
        return self._metrics.get_statistics()

    def enable_humanization(self, enabled: bool) -> None:
        self._enabled = enabled
        _log.info(f"[HIS] Humanización {'habilitada' if enabled else 'deshabilitada'}")

    # ------------------------------------------------------------------
    # Passthrough — InputController attributes
    # ------------------------------------------------------------------

    @property
    def hwnd(self) -> Any:
        return self._ic.hwnd

    @property
    def input_method(self) -> str:
        return self._ic.input_method

    @input_method.setter
    def input_method(self, value: str) -> None:
        self._ic.input_method = value

    @property
    def jitter_pct(self) -> float:
        return self._ic.jitter_pct

    @jitter_pct.setter
    def jitter_pct(self, value: float) -> None:
        self._ic.jitter_pct = value

    @property
    def interception_available(self) -> bool:
        return self._ic.interception_available

    def find_target(self) -> Any:
        return self._ic.find_target()

    def is_connected(self) -> bool:
        return self._ic.is_connected()

    def focus_now(self) -> bool:
        return self._ic.focus_now()

    def set_critical_check(self, fn: Any) -> None:
        """Register callback to suppress AFK pauses during combat/danger."""
        self._behavior.set_critical_check(fn)

    def __getattr__(self, name: str) -> Any:
        """Delegate any unknown attribute to the underlying InputController."""
        return getattr(self._ic, name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_fatigue(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_input_ts
        self._behavior.update_fatigue(elapsed)

    def _get_key_duration(self, override: Optional[float] = None) -> float:
        """Retorna duración de key press en ms."""
        if override is not None:
            return self._timing.add_jitter(override * 1000.0)
        fatigue = self._behavior.get_fatigue_level()
        return self._timing.get_key_press_duration(fatigue)

    def _execute_key_press(self, vk: int, duration_s: float) -> bool:
        """Ejecuta key press via Arduino o InputController."""
        if self._arduino.is_available():
            ok = self._arduino.send_key_press(str(vk), duration_s * 1000.0)
            if ok:
                return True
        return self._ic.press_key(vk, duration_s)

    def _start_afk_pause(self) -> None:
        """Lanza pausa AFK en thread separado — NO bloquea healer/combat."""
        if self._in_afk:
            return
        self._in_afk = True
        self._afk_thread = threading.Thread(
            target=self._afk_pause_worker, daemon=True
        )
        self._afk_thread.start()

    def _afk_pause_worker(self) -> None:
        """Worker thread para la pausa AFK."""
        try:
            fatigue_before = self._behavior.get_fatigue_level()
            duration = self._behavior.generate_afk_duration()
            _log.info(
                f"[HIS] Pausa AFK: {duration:.0f}s (fatiga={fatigue_before:.2f})"
            )
            # During AFK, we just suppress humanization effects.
            # The bot keeps running (healer, combat still work via _ic).
            self._enabled = False
            time.sleep(duration)
            self._enabled = True
            self._behavior.reset_fatigue_after_afk()
            self._metrics.record_afk_pause(duration)
            _log.info(
                f"[HIS] AFK terminado (fatiga={self._behavior.get_fatigue_level():.2f})"
            )
        finally:
            self._in_afk = False

    def close(self) -> None:
        """Cierra todos los recursos."""
        self._arduino.close()
        self._metrics.close()
        _log.info("[HIS] Sistema cerrado")
