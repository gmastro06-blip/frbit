"""BehaviorSimulator — fatiga progresiva, errores humanos y pausas AFK."""

from __future__ import annotations

import logging
import math
import random
import time
from typing import Callable, Optional, Tuple

from ..config.models import BehaviorConfig
from ..utils.keyboard_layout import KeyboardLayout

_log = logging.getLogger(__name__)


class BehaviorSimulator:
    """Simula comportamientos humanos: fatiga, errores, pausas AFK."""

    def __init__(self, config: BehaviorConfig) -> None:
        self._cfg = config
        self._fatigue_level: float = config.fatigue_level_initial
        self._last_update_ts: float = time.monotonic()
        self._session_start: float = time.monotonic()

        # Callback opcional para verificar situación crítica del bot
        self._critical_check: Optional[Callable[[], bool]] = None

    # ------------------------------------------------------------------
    # Fatigue management  (Req 2)
    # ------------------------------------------------------------------

    def update_fatigue(self, elapsed_seconds: float) -> None:
        """Incrementa fatiga según tiempo transcurrido.

        ``fatigue_rate_per_hour`` se distribuye linealmente por segundo.
        """
        increment = elapsed_seconds * self._cfg.fatigue_rate_per_hour / 3600.0
        self._fatigue_level = min(1.0, self._fatigue_level + increment)
        self._last_update_ts = time.monotonic()

    def get_fatigue_level(self) -> float:
        return self._fatigue_level

    def set_fatigue_level(self, level: float) -> None:
        self._fatigue_level = max(0.0, min(1.0, level))

    # ------------------------------------------------------------------
    # Error generation  (Req 3)
    # ------------------------------------------------------------------

    def should_generate_error(self) -> Optional[str]:
        """Retorna tipo de error a generar o ``None``.

        El error_rate se ajusta por fatiga: ``base * (1 + fatigue)``.
        """
        adjusted_rate = self._cfg.error_rate_base * (1.0 + self._fatigue_level)
        if random.random() >= adjusted_rate:
            return None

        # Seleccionar tipo según probabilidades configuradas
        r = random.random()
        cumulative = 0.0
        for error_type, prob in self._cfg.error_probabilities.items():
            cumulative += prob
            if r < cumulative:
                return error_type
        # Fallback al último tipo
        return list(self._cfg.error_probabilities.keys())[-1]

    def apply_wrong_key_error(self, intended_key: str) -> str:
        """Retorna una tecla adyacente en el teclado QWERTY."""
        return KeyboardLayout.get_random_adjacent(intended_key)

    def apply_wrong_key_error_vk(self, intended_vk: int) -> int:
        """Versión que acepta VK codes (para compatibilidad con InputController)."""
        return KeyboardLayout.get_adjacent_vk(intended_vk)

    def apply_double_press_error(self) -> float:
        """Retorna delay entre pulsaciones para doble-presión (20-80 ms)."""
        return random.uniform(20.0, 80.0)

    def apply_miss_click_offset(self, x: int, y: int) -> Tuple[int, int]:
        """Retorna coordenadas con offset de miss-click (5-25 px)."""
        distance = random.uniform(5.0, 25.0)
        angle = random.uniform(0.0, 2.0 * math.pi)
        dx = int(round(distance * math.cos(angle)))
        dy = int(round(distance * math.sin(angle)))
        return (x + dx, y + dy)

    def apply_hesitation_delay(self) -> float:
        """Retorna delay de hesitación: N(500, 150) → [200, 800] ms."""
        val = random.gauss(500.0, 150.0)
        return max(200.0, min(800.0, val))

    # ------------------------------------------------------------------
    # AFK pauses  (Req 11)
    # ------------------------------------------------------------------

    def should_trigger_afk_pause(self) -> bool:
        """Determina si se debe iniciar una pausa AFK.

        Probabilidad base por hora ajustada por fatiga.
        No se dispara en situación crítica.
        """
        if self.is_in_critical_situation():
            return False

        # Convertimos probabilidad/hora a probabilidad/llamada
        # Asumimos ~1 llamada por input, ~2 inputs/segundo media
        calls_per_hour = 7200.0
        base_prob = self._cfg.afk_pause_probability_per_hour / calls_per_hour
        adjusted = base_prob * (1.0 + self._fatigue_level * 2.0)
        return random.random() < adjusted

    def generate_afk_duration(self) -> float:
        """Genera duración de pausa AFK (log-normal, 30-300 s)."""
        mu = math.log(90.0)
        sigma = 0.8
        val = random.lognormvariate(mu, sigma)
        return max(self._cfg.afk_min_duration,
                   min(self._cfg.afk_max_duration, val))

    def reset_fatigue_after_afk(self) -> None:
        """Resetea fatiga a [0.2, 0.4] post AFK."""
        self._fatigue_level = random.uniform(0.2, 0.4)

    # ------------------------------------------------------------------
    # Critical situation check
    # ------------------------------------------------------------------

    def set_critical_check(self, fn: Callable[[], bool]) -> None:
        """Registra un callback que retorna True si estamos en situación crítica."""
        self._critical_check = fn

    def is_in_critical_situation(self) -> bool:
        """True si el bot está en combate o HP bajo."""
        if self._critical_check is not None:
            try:
                return self._critical_check()
            except Exception:
                return False
        return False
