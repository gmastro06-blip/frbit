"""Modelos de configuración y datos para el Human Input System."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TimingConfig:
    """Configuración de timing y delays."""

    reaction_time_mean: float = 220.0   # ms
    reaction_time_std: float = 40.0     # ms
    key_press_duration_mean: float = 80.0   # ms
    key_press_duration_std: float = 15.0    # ms
    micro_pause_mean: float = 25.0      # ms
    micro_pause_std: float = 8.0        # ms

    def validate(self) -> bool:
        return (100 <= self.reaction_time_mean <= 500
                and 10 <= self.reaction_time_std <= 100
                and 30 <= self.key_press_duration_mean <= 200
                and 5 <= self.key_press_duration_std <= 50)


@dataclass
class BehaviorConfig:
    """Configuración de comportamiento y errores."""

    error_rate_base: float = 0.05
    error_probabilities: Dict[str, float] = field(default_factory=lambda: {
        "wrong_key": 0.4,
        "double_press": 0.3,
        "miss_click": 0.2,
        "hesitation": 0.1,
    })
    fatigue_rate_per_hour: float = 0.10
    fatigue_level_initial: float = 0.0
    afk_pause_probability_per_hour: float = 0.3
    afk_min_duration: float = 30.0    # seconds
    afk_max_duration: float = 300.0   # seconds
    enable_circadian_adjustment: bool = True

    def validate(self) -> bool:
        return (0.0 <= self.error_rate_base <= 0.5
                and 0.0 <= self.fatigue_rate_per_hour <= 0.3
                and 0.0 <= self.fatigue_level_initial <= 1.0
                and abs(sum(self.error_probabilities.values()) - 1.0) < 0.01)


@dataclass
class MouseConfig:
    """Configuración de movimiento de mouse."""

    bezier_control_offset_min: float = 0.1   # 10% de distancia
    bezier_control_offset_max: float = 0.3   # 30% de distancia
    micro_movement_std: float = 1.5          # píxeles
    overshoot_probability: float = 0.3
    overshoot_distance_min: float = 5.0      # píxeles
    overshoot_distance_max: float = 15.0     # píxeles
    min_movement_duration: float = 200.0     # ms
    max_movement_duration: float = 2000.0    # ms
    points_per_movement: int = 50

    def validate(self) -> bool:
        return (0.0 <= self.overshoot_probability <= 1.0
                and self.min_movement_duration < self.max_movement_duration)


@dataclass
class ArduinoConfig:
    """Configuración de Arduino HID."""

    enabled: bool = False
    port: Optional[str] = None   # None = auto-detect
    baudrate: int = 115200
    timeout: float = 0.1         # seconds
    retry_attempts: int = 3

    def validate(self) -> bool:
        return self.baudrate > 0 and self.timeout > 0


@dataclass
class PicoConfig:
    """Configuración de Raspberry Pi Pico 2 HID."""

    enabled: bool = False
    port: Optional[str] = None   # None = auto-detect
    baudrate: int = 115200
    timeout: float = 0.1         # seconds
    retry_attempts: int = 3

    def validate(self) -> bool:
        return self.baudrate > 0 and self.timeout > 0


@dataclass
class BehaviorProfile:
    """Perfil de comportamiento completo."""

    name: str
    timing: TimingConfig
    behavior: BehaviorConfig
    mouse: MouseConfig
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "timing": asdict(self.timing),
            "behavior": asdict(self.behavior),
            "mouse": asdict(self.mouse),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BehaviorProfile":
        timing_data = data.get("timing", {})
        behavior_data = data.get("behavior", {})
        mouse_data = data.get("mouse", {})
        return cls(
            name=data.get("name", "custom"),
            description=data.get("description", ""),
            timing=TimingConfig(**{
                k: v for k, v in timing_data.items()
                if k in TimingConfig.__dataclass_fields__
            }),
            behavior=BehaviorConfig(**{
                k: v for k, v in behavior_data.items()
                if k in BehaviorConfig.__dataclass_fields__
            }),
            mouse=MouseConfig(**{
                k: v for k, v in mouse_data.items()
                if k in MouseConfig.__dataclass_fields__
            }),
        )


@dataclass
class Configuration:
    """Configuración completa del sistema."""

    timing: TimingConfig = field(default_factory=TimingConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    mouse: MouseConfig = field(default_factory=MouseConfig)
    arduino: ArduinoConfig = field(default_factory=ArduinoConfig)
    profiles: Dict[str, BehaviorProfile] = field(default_factory=dict)
    active_profile: str = "default"
    log_directory: str = "./logs"
    enable_humanization: bool = True

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.timing.validate():
            errors.append("Invalid timing configuration")
        if not self.behavior.validate():
            errors.append("Invalid behavior configuration")
        if not self.mouse.validate():
            errors.append("Invalid mouse configuration")
        if not self.arduino.validate():
            errors.append("Invalid Arduino configuration")
        if self.active_profile not in self.profiles and self.profiles:
            errors.append(f"Active profile '{self.active_profile}' not found")
        return errors
