"""Parser y validador de configuración YAML."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import yaml  # type: ignore[import-untyped]

from .models import (
    ArduinoConfig,
    BehaviorConfig,
    BehaviorProfile,
    Configuration,
    MouseConfig,
    TimingConfig,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rangos válidos para validación
# ---------------------------------------------------------------------------
_VALID_RANGES: Dict[str, tuple[float, float]] = {
    "reaction_time_mean": (100.0, 500.0),
    "reaction_time_std": (10.0, 100.0),
    "key_press_duration_mean": (30.0, 200.0),
    "key_press_duration_std": (5.0, 50.0),
    "micro_pause_mean": (5.0, 100.0),
    "micro_pause_std": (1.0, 40.0),
    "error_rate_base": (0.0, 0.5),
    "fatigue_rate_per_hour": (0.0, 0.3),
    "fatigue_level_initial": (0.0, 1.0),
    "afk_pause_probability_per_hour": (0.0, 2.0),
    "overshoot_probability": (0.0, 1.0),
}


class ConfigurationError(Exception):
    """Error de parsing o validación de configuración."""


class ConfigurationParser:
    """Parser y validador de archivos de configuración YAML."""

    def __init__(self, config_path: str) -> None:
        self._config_path = config_path
        self._last_mtime: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> Configuration:
        """Lee y valida el archivo YAML, retornando un *Configuration*."""
        raw = self._read_yaml()
        raw = self.apply_defaults(raw)
        errors = self.validate_ranges(raw)
        if errors:
            for e in errors:
                _log.warning("[ConfigParser] %s", e)

        timing = self._build_timing(raw.get("timing", {}))
        behavior = self._build_behavior(raw.get("behavior", {}))
        mouse = self._build_mouse(raw.get("mouse", {}))
        arduino = self._build_arduino(raw.get("arduino", {}))
        profiles = self._build_profiles(raw.get("profiles", {}))

        system = raw.get("system", {})
        cfg = Configuration(
            timing=timing,
            behavior=behavior,
            mouse=mouse,
            arduino=arduino,
            profiles=profiles,
            active_profile=system.get("active_profile", "default"),
            log_directory=system.get("log_directory", "./logs"),
            enable_humanization=system.get("enable_humanization", True),
        )

        # Asegurar que siempre exista perfil "default"
        if "default" not in cfg.profiles:
            cfg.profiles["default"] = BehaviorProfile(
                name="default",
                description="Auto-generated default profile",
                timing=TimingConfig(**{
                    k: v for k, v in raw.get("timing", {}).items()
                    if k in TimingConfig.__dataclass_fields__
                }),
                behavior=BehaviorConfig(**{
                    k: v for k, v in raw.get("behavior", {}).items()
                    if k in BehaviorConfig.__dataclass_fields__
                }),
                mouse=MouseConfig(**{
                    k: v for k, v in raw.get("mouse", {}).items()
                    if k in MouseConfig.__dataclass_fields__
                }),
            )

        validation_errors = cfg.validate()
        if validation_errors:
            _log.warning("[ConfigParser] Validation issues: %s", validation_errors)

        return cfg

    def reload(self) -> Configuration:
        """Recarga la configuración desde disco."""
        return self.parse()

    def to_yaml(self, config: Configuration) -> str:
        """Serializa *Configuration* de vuelta a YAML."""
        from dataclasses import asdict

        data: Dict[str, Any] = {
            "timing": asdict(config.timing),
            "behavior": asdict(config.behavior),
            "mouse": asdict(config.mouse),
            "arduino": asdict(config.arduino),
            "profiles": {
                name: prof.to_dict() for name, prof in config.profiles.items()
            },
            "system": {
                "active_profile": config.active_profile,
                "log_directory": config.log_directory,
                "enable_humanization": config.enable_humanization,
            },
        }
        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def validate_ranges(raw: Dict[str, Any]) -> List[str]:
        """Valida parámetros numéricos contra rangos conocidos."""
        errors: List[str] = []
        for section_key in ("timing", "behavior", "mouse"):
            section = raw.get(section_key, {})
            if not isinstance(section, dict):
                continue
            for param, (lo, hi) in _VALID_RANGES.items():
                if param in section:
                    val = section[param]
                    if isinstance(val, (int, float)) and not (lo <= val <= hi):
                        errors.append(
                            f"{section_key}.{param}={val} fuera de rango "
                            f"[{lo}, {hi}]"
                        )

        # error_probabilities deben sumar 1.0
        beh = raw.get("behavior", {})
        probs = beh.get("error_probabilities")
        if isinstance(probs, dict):
            total = sum(probs.values())
            if abs(total - 1.0) > 0.01:
                errors.append(
                    f"behavior.error_probabilities suman {total:.3f}, "
                    "deben sumar 1.0"
                )
        return errors

    @staticmethod
    def apply_defaults(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Aplica valores por defecto para secciones faltantes."""
        from dataclasses import asdict

        defaults: Dict[str, Any] = {
            "timing": asdict(TimingConfig()),
            "behavior": asdict(BehaviorConfig()),
            "mouse": asdict(MouseConfig()),
            "arduino": asdict(ArduinoConfig()),
            "profiles": {},
            "system": {
                "active_profile": "default",
                "log_directory": "./logs",
                "enable_humanization": True,
            },
        }
        for key, default_section in defaults.items():
            if key not in raw:
                raw[key] = default_section
            elif isinstance(default_section, dict) and isinstance(raw[key], dict):
                merged = dict(default_section)
                merged.update(raw[key])
                raw[key] = merged
        return raw

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _read_yaml(self) -> Dict[str, Any]:
        try:
            with open(self._config_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, dict):
                raise ConfigurationError(
                    f"YAML root must be a mapping, got {type(data).__name__}"
                )
            self._last_mtime = os.path.getmtime(self._config_path)
            return data
        except FileNotFoundError:
            _log.warning(
                "[ConfigParser] Archivo no encontrado: %s — usando defaults",
                self._config_path,
            )
            return {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"YAML parse error: {exc}") from exc

    @staticmethod
    def _build_timing(data: Dict[str, Any]) -> TimingConfig:
        fields = {
            k: v for k, v in data.items()
            if k in TimingConfig.__dataclass_fields__
        }
        return TimingConfig(**fields)

    @staticmethod
    def _build_behavior(data: Dict[str, Any]) -> BehaviorConfig:
        fields = {
            k: v for k, v in data.items()
            if k in BehaviorConfig.__dataclass_fields__
        }
        return BehaviorConfig(**fields)

    @staticmethod
    def _build_mouse(data: Dict[str, Any]) -> MouseConfig:
        fields = {
            k: v for k, v in data.items()
            if k in MouseConfig.__dataclass_fields__
        }
        return MouseConfig(**fields)

    @staticmethod
    def _build_arduino(data: Dict[str, Any]) -> ArduinoConfig:
        fields = {
            k: v for k, v in data.items()
            if k in ArduinoConfig.__dataclass_fields__
        }
        return ArduinoConfig(**fields)

    @staticmethod
    def _build_profiles(
        data: Dict[str, Any],
    ) -> Dict[str, BehaviorProfile]:
        profiles: Dict[str, BehaviorProfile] = {}
        if not isinstance(data, dict):
            return profiles
        for name, prof_data in data.items():
            if not isinstance(prof_data, dict):
                continue
            prof_data.setdefault("name", name)
            profiles[name] = BehaviorProfile.from_dict(prof_data)
        return profiles
