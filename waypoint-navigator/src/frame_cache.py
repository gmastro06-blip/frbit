"""
frame_cache.py
--------------
Thread-safe frame cache with configurable TTL.

Multiple subsystems (healer ~6.7 Hz, combat ~2.9 Hz, death 1 Hz,
reconnect 0.2 Hz, condition-monitor, etc.) each call ``frame_getter()``
independently, leading to 4-5 redundant screen captures per cycle.

``FrameCache`` wraps the real frame-getter with a time-based cache so
that only **one** real capture happens per TTL window (default 50 ms).

Usage::

    from src.frame_cache import FrameCache

    cache = FrameCache(real_getter, ttl_ms=50)
    healer.set_frame_getter(cache.get_frame)
    combat.set_frame_getter(cache.get_frame)
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np


class FrameCache:
    """Thin caching layer over a ``() -> ndarray | None`` callable.

    Parameters
    ----------
    getter:
        The real frame-capture function (e.g. ``build_frame_getter(...)``).
    ttl_ms:
        Time-to-live in milliseconds.  A cached frame is considered *fresh*
        if fewer than *ttl_ms* ms have elapsed since it was captured.
    diff_threshold:
        Mean absolute pixel difference below which two frames are
        considered identical.  0 disables diff checking.
    """

    __slots__ = (
        "_getter", "_ttl_s", "_lock", "_frame", "_ts",
        "_diff_threshold", "_frame_changed", "_prev_hash",
    )

    def __init__(
        self,
        getter: Callable[[], Optional[np.ndarray]],
        *,
        ttl_ms: float = 50.0,
        diff_threshold: float = 2.0,
    ) -> None:
        self._getter = getter
        self._ttl_s: float = ttl_ms / 1000.0
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._ts: float = 0.0  # monotonic timestamp of last capture
        self._diff_threshold = diff_threshold
        self._frame_changed: bool = True
        self._prev_hash: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_frame(self) -> Optional[np.ndarray]:
        """Return a cached frame if still fresh, otherwise capture a new one.

        Thread-safe: concurrent callers will wait for the single capture
        to complete rather than issuing parallel captures.
        """
        now = time.monotonic()
        with self._lock:
            if (now - self._ts) < self._ttl_s:
                # Safe to return the stored reference: mss creates a new array
                # per capture so consumers hold an independent object.
                # With dxcam (disabled in prod) a reuse-buffer race could occur.
                return self._frame
            t0 = time.monotonic()  # timestamp captured BEFORE getter — TTL starts at capture request time
            frame = self._getter()
            if frame is not None and self._diff_threshold > 0:
                h = hash(frame.ravel()[:4096].tobytes())  # hash first 4096 bytes via 1D view (avoids full-frame copy)
                self._frame_changed = h != self._prev_hash
                self._prev_hash = h
            else:
                self._frame_changed = True
            self._frame = frame
            self._ts = t0
            return frame

    @property
    def frame_changed(self) -> bool:
        """True if the last ``get_frame()`` returned a different frame."""
        return self._frame_changed

    def invalidate(self) -> None:
        """Force the next ``get_frame()`` to perform a real capture."""
        with self._lock:
            self._ts = 0.0
            self._frame = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def ttl_ms(self) -> float:
        """Return the configured TTL in milliseconds."""
        return self._ttl_s * 1000.0

    @ttl_ms.setter
    def ttl_ms(self, value: float) -> None:
        """Adjust TTL at runtime. Thread-safe."""
        with self._lock:
            self._ttl_s = value / 1000.0

    @property
    def raw_getter(self) -> Callable[[], Optional[np.ndarray]]:
        """Return the underlying (unwrapped) frame-getter."""
        return self._getter
