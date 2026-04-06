"""TimingHumanizer — genera delays con distribución gaussiana."""

from __future__ import annotations

import random

from ..config.models import TimingConfig


class TimingHumanizer:
    """Genera delays y duraciones con distribución gaussiana.

    Todos los valores de retorno están en **milisegundos**.
    """

    def __init__(self, config: TimingConfig) -> None:
        self._cfg = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_reaction_time(self, fatigue_level: float = 0.0) -> float:
        """Reaction time: N(220, 40) ajustado por fatiga → [150, 350 + fatigue*200]."""
        base = random.gauss(self._cfg.reaction_time_mean,
                            self._cfg.reaction_time_std)
        adjusted = base * (1.0 + fatigue_level * 0.5)
        lo = 150.0
        hi = 350.0 + fatigue_level * 200.0
        return max(lo, min(hi, adjusted))

    def get_key_press_duration(self, fatigue_level: float = 0.0) -> float:
        """Duración de presión: N(80, 15) ajustado por fatiga → [50, 120 + fatigue*50]."""
        base = random.gauss(self._cfg.key_press_duration_mean,
                            self._cfg.key_press_duration_std)
        adjusted = base * (1.0 + fatigue_level * 0.3)
        lo = 50.0
        hi = 120.0 + fatigue_level * 50.0
        return max(lo, min(hi, adjusted))

    def get_micro_pause(self) -> float:
        """Micro-pausa: N(25, 8) → [10, 50] ms."""
        val = random.gauss(self._cfg.micro_pause_mean,
                           self._cfg.micro_pause_std)
        return max(10.0, min(50.0, val))

    def get_movement_duration(self, distance: float,
                              fatigue_level: float = 0.0) -> float:
        """Duración de movimiento de mouse basada en distancia (Fitts's Law).

        Retorna milisegundos, clamped a [200, 2000].
        """
        base = 200.0 + (distance / 500.0) * 600.0
        varied = random.gauss(base, base * 0.15)
        adjusted = varied * (1.0 + fatigue_level * 0.4)
        return max(200.0, min(2000.0, adjusted))

    def add_jitter(self, base_delay: float) -> float:
        """Agrega jitter gaussiano (σ = 5 % del delay)."""
        jitter = random.gauss(0, base_delay * 0.05)
        return max(1.0, base_delay + jitter)

    def get_correlated_timing(self, fatigue_level: float = 0.0) -> dict[str, float]:
        """Retorna un dict con todos los valores de timing correlacionados.

        Añade un ruido de sesión compartido (σ=0.1) a fatigue_level para que
        todos los valores sean empujados alto o bajo juntos — igual que un
        humano real que en un momento dado está más o menos reactivo en todas
        sus dimensiones.

        Returns
        -------
        dict con claves: ``reaction_time``, ``key_press_duration``,
        ``micro_pause`` — todos en milisegundos.
        """
        session_noise = random.gauss(0.0, 0.1)
        effective_fatigue = max(0.0, fatigue_level + session_noise)
        return {
            "reaction_time":      self.get_reaction_time(effective_fatigue),
            "key_press_duration": self.get_key_press_duration(effective_fatigue),
            "micro_pause":        self.get_micro_pause(),
        }
