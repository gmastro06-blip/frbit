"""ProfileManager — gestión de perfiles, transiciones suaves y circadiano."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config.models import (
    BehaviorConfig,
    BehaviorProfile,
    MouseConfig,
    TimingConfig,
)
from ..config.parser import ConfigurationParser

_log = logging.getLogger(__name__)


class ProfileManager:
    """Carga, conmuta y ajusta perfiles de comportamiento."""

    def __init__(self, config_parser: ConfigurationParser) -> None:
        self._parser = config_parser
        self._profiles: Dict[str, BehaviorProfile] = {}
        self._active_profile: Optional[BehaviorProfile] = None
        self._transitioning = False
        self._transition_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Load / create
    # ------------------------------------------------------------------

    def load_profiles(self) -> None:
        """Carga perfiles desde la configuración parseada."""
        cfg = self._parser.parse()
        for name, profile in cfg.profiles.items():
            self._profiles[name] = profile
            _log.debug(f"[ProfileManager] Perfil cargado: {name}")

        # Activar perfil configurado
        active = cfg.active_profile
        if active in self._profiles:
            self._active_profile = self._profiles[active]
        elif self._profiles:
            first = next(iter(self._profiles))
            self._active_profile = self._profiles[first]
            _log.warning(
                f"[ProfileManager] Perfil '{active}' no encontrado, "
                f"usando '{first}'"
            )
        _log.info(
            f"[ProfileManager] {len(self._profiles)} perfiles cargados, "
            f"activo: {self._active_profile.name if self._active_profile else 'ninguno'}"
        )

    def create_custom_profile(self, name: str, params: Dict[str, Any]) -> bool:
        """Crea un perfil personalizado desde diccionario."""
        try:
            profile = BehaviorProfile.from_dict({"name": name, **params})
            self._profiles[name] = profile
            _log.info(f"[ProfileManager] Perfil personalizado creado: {name}")
            return True
        except Exception as exc:
            _log.error(f"[ProfileManager] Error creando perfil '{name}': {exc}")
            return False

    # ------------------------------------------------------------------
    # Profile switching
    # ------------------------------------------------------------------

    def set_active_profile(
        self, profile_name: str, transition_duration: float = 10.0
    ) -> bool:
        """Cambia perfil activo con transición suave (5-15 s)."""
        if profile_name not in self._profiles:
            _log.warning(f"[ProfileManager] Perfil '{profile_name}' no existe")
            return False

        target = self._profiles[profile_name]
        transition_duration = max(5.0, min(15.0, transition_duration))

        if self._active_profile is None:
            self._active_profile = target
            return True

        # Transición en background
        t = threading.Thread(
            target=self._smooth_transition,
            args=(self._active_profile, target, transition_duration),
            daemon=True,
        )
        t.start()
        return True

    def _smooth_transition(
        self,
        source: BehaviorProfile,
        target: BehaviorProfile,
        duration: float,
    ) -> None:
        """Interpola linealmente cada parámetro numérico desde source a target."""
        with self._transition_lock:
            self._transitioning = True
            step_interval = 0.1
            num_steps = max(1, int(duration / step_interval))

            src_dict = source.to_dict()
            tgt_dict = target.to_dict()

            for step in range(1, num_steps + 1):
                t = step / num_steps
                merged = self._interpolate_dicts(src_dict, tgt_dict, t)
                merged["name"] = target.name
                self._active_profile = BehaviorProfile.from_dict(merged)
                time.sleep(step_interval)

            self._active_profile = target
            self._transitioning = False
            _log.info(
                f"[ProfileManager] Transición completada → '{target.name}'"
            )

    @staticmethod
    def _interpolate_dicts(
        src: Dict[str, Any], tgt: Dict[str, Any], t: float
    ) -> Dict[str, Any]:
        """Interpola campos numéricos entre dos dicts recursivamente."""
        result: Dict[str, Any] = {}
        for key in tgt:
            sv = src.get(key)
            tv = tgt[key]
            if isinstance(tv, dict) and isinstance(sv, dict):
                result[key] = ProfileManager._interpolate_dicts(sv, tv, t)
            elif isinstance(tv, (int, float)) and isinstance(sv, (int, float)):
                result[key] = sv + (tv - sv) * t
            else:
                result[key] = tv
        return result

    def get_active_profile(self) -> Optional[BehaviorProfile]:
        return self._active_profile

    def get_profile_parameters(
        self, profile_name: str
    ) -> Optional[Dict[str, Any]]:
        p = self._profiles.get(profile_name)
        return p.to_dict() if p else None

    def list_profiles(self) -> List[str]:
        return list(self._profiles.keys())

    # ------------------------------------------------------------------
    # Circadian adjustments  (Req 10)
    # ------------------------------------------------------------------

    def apply_circadian_adjustments(self) -> None:
        """Ajusta parámetros según hora del día.

        * 23:00-06:00 → noche: fatigue +0.2, reacción +20%
        * 06:00-10:00 → mañana: fatigue +0.1, reacción +8%
        * 10:00-18:00 → día: parámetros normales
        * 18:00-23:00 → tarde: fatigue +0.05
        """
        if self._active_profile is None:
            return

        hour = datetime.now().hour
        base = self._active_profile

        timing_adj: Dict[str, float] = {}
        behavior_adj: Dict[str, float] = {}

        if hour >= 23 or hour < 6:
            behavior_adj["fatigue_level_initial"] = min(
                1.0, base.behavior.fatigue_level_initial + 0.2
            )
            timing_adj["reaction_time_mean"] = base.timing.reaction_time_mean * 1.20
        elif 6 <= hour < 10:
            behavior_adj["fatigue_level_initial"] = min(
                1.0, base.behavior.fatigue_level_initial + 0.1
            )
            timing_adj["reaction_time_mean"] = base.timing.reaction_time_mean * 1.08
        elif 18 <= hour < 23:
            behavior_adj["fatigue_level_initial"] = min(
                1.0, base.behavior.fatigue_level_initial + 0.05
            )

        if timing_adj or behavior_adj:
            d = base.to_dict()
            d["timing"].update(timing_adj)
            d["behavior"].update(behavior_adj)
            self._active_profile = BehaviorProfile.from_dict(d)
            _log.debug(
                f"[ProfileManager] Ajuste circadiano hora={hour}: "
                f"timing={timing_adj}, behavior={behavior_adj}"
            )
