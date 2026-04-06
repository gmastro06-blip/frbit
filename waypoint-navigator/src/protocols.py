"""
protocols.py
------------
Structural interfaces (PEP 544 Protocols) for Navigator subsystems.

These are *runtime-checkable* Protocols — no existing class needs to
explicitly inherit from them.  Any class that already has the required
methods automatically satisfies the protocol (duck typing).

Usage::

    from src.protocols import FrameConsumer, Stoppable
    assert isinstance(healer, FrameConsumer)  # True if healer.set_frame_getter exists
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class FrameConsumer(Protocol):
    """Subsistema que acepta un callable para obtener frames."""

    def set_frame_getter(
        self,
        getter: Callable[[], Optional[np.ndarray]],
    ) -> None: ...


@runtime_checkable
class Stoppable(Protocol):
    """Subsistema que puede iniciarse y detenerse."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
