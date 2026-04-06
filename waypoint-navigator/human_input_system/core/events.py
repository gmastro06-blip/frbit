"""Modelos de eventos de input."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class InputEvent:
    """Evento de input base."""

    timestamp: float      # time.time() con microsegundos
    event_type: str       # 'key_press', 'key_release', 'mouse_move', 'mouse_click'

    def to_serial_command(self) -> str:
        raise NotImplementedError


@dataclass
class KeyPressEvent(InputEvent):
    """Evento de presión de tecla."""

    key: str = ""
    duration: float = 0.0   # ms
    had_error: bool = False
    error_type: Optional[str] = None

    def __post_init__(self) -> None:
        self.event_type = "key_press"

    def to_serial_command(self) -> str:
        return f"KEY_PRESS|{self.key}|{int(self.duration)}\n"


@dataclass
class KeyReleaseEvent(InputEvent):
    """Evento de liberación de tecla."""

    key: str = ""

    def __post_init__(self) -> None:
        self.event_type = "key_release"

    def to_serial_command(self) -> str:
        return f"KEY_RELEASE|{self.key}\n"


@dataclass
class MouseMoveEvent(InputEvent):
    """Evento de movimiento de mouse."""

    x: int = 0
    y: int = 0
    relative: bool = False
    path: Optional[List[Tuple[int, int]]] = None

    def __post_init__(self) -> None:
        self.event_type = "mouse_move"

    def to_serial_command(self) -> str:
        rel = "1" if self.relative else "0"
        return f"MOUSE_MOVE|{self.x}|{self.y}|{rel}\n"


@dataclass
class MouseClickEvent(InputEvent):
    """Evento de click de mouse."""

    button: str = "left"   # 'left', 'right', 'middle'
    x: Optional[int] = None
    y: Optional[int] = None

    def __post_init__(self) -> None:
        self.event_type = "mouse_click"

    def to_serial_command(self) -> str:
        return f"MOUSE_CLICK|{self.button}\n"


@dataclass
class AFKPauseEvent:
    """Evento de pausa AFK."""

    start_time: float = 0.0
    duration: float = 0.0      # seconds
    fatigue_before: float = 0.0
    fatigue_after: float = 0.0
