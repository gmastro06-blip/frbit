"""Bézier / spline mouse-movement curves for human-like cursor motion.

Generates a sequence of ``(x, y)`` screen coordinates along a randomised cubic
Bézier curve connecting two points.  The curvature, speed profile, and micro-
jitter are designed to mimic a real human hand on a mouse.

Usage::

    from .mouse_bezier import bezier_path, move_mouse_smooth

    # Just get the points
    points = bezier_path((100, 200), (800, 450), steps=60)

    # Actually move the Windows cursor along the path
    move_mouse_smooth((100, 200), (800, 450))
"""

from __future__ import annotations

import ctypes
import logging
import math
import random
import time
from typing import Callable, List, Optional, Tuple

_log = logging.getLogger("wn.mb")

# ── Win32 API ────────────────────────────────────────────────────────────────
user32 = ctypes.windll.user32

_set_cursor_warned = False


def _set_cursor_pos(x: int, y: int) -> None:
    """Move the real Windows cursor to (x, y) screen coordinates.

    WARNING: SetCursorPos is hookeable by BattlEye via IAT hooks.
    Use an Interception-based ``move_fn`` in production instead.
    """
    global _set_cursor_warned
    if not _set_cursor_warned:
        _log.warning(
            "[ANTI-DETECT] SetCursorPos llamado — ¡DETECTABLE por BattlEye! "
            "Usa move_fn con Interception en producción."
        )
        _set_cursor_warned = True
    user32.SetCursorPos(int(x), int(y))


def _get_cursor_pos() -> Tuple[int, int]:
    """Return current Windows cursor position."""

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


# ── Bézier math ──────────────────────────────────────────────────────────────

def _cubic_bezier(
    t: float,
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    p3: Tuple[float, float],
) -> Tuple[float, float]:
    """Evaluate cubic Bézier at parameter *t* ∈ [0, 1]."""
    u = 1.0 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t
    x = uuu * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + ttt * p3[1]
    return (x, y)


def _random_control_point(
    start: Tuple[float, float],
    end: Tuple[float, float],
    spread: float = 0.4,
) -> Tuple[float, float]:
    """Generate a random control point near the line *start→end*.

    *spread* controls how far the control point can deviate from the
    straight-line path (fraction of the total distance).
    """
    mx = (start[0] + end[0]) / 2.0
    my = (start[1] + end[1]) / 2.0
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy) or 1.0
    # Perpendicular offset
    nx, ny = -dy / dist, dx / dist
    offset = random.gauss(0, spread * dist * 0.5)
    # Also shift along the line for asymmetry
    along = random.uniform(0.2, 0.8)
    cx = start[0] + along * dx + nx * offset
    cy = start[1] + along * dy + ny * offset
    return (cx, cy)


def bezier_path(
    start: Tuple[int, int],
    end: Tuple[int, int],
    steps: Optional[int] = None,
    spread: float = 0.35,
) -> List[Tuple[int, int]]:
    """Return a list of integer ``(x, y)`` coordinates along a Bézier curve.

    Parameters
    ----------
    start, end : (x, y)
        Screen coordinates.
    steps : int | None
        Number of intermediate points.  ``None`` → auto-scale with distance.
    spread : float
        Deviation of control points from the straight line (0.0–1.0).

    Returns
    -------
    List of (x, y) integer coordinates, including *start* and *end*.
    """
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if steps is None:
        # ~1 point per 5 px, clamped to [10, 120]
        steps = max(10, min(120, int(dist / 5)))

    p0 = (float(start[0]), float(start[1]))
    p3 = (float(end[0]), float(end[1]))
    p1 = _random_control_point(p0, p3, spread)
    p2 = _random_control_point(p0, p3, spread)

    path: List[Tuple[int, int]] = []
    for i in range(steps + 1):
        t = i / steps
        # Ease-in / ease-out: slow start, fast middle, slow end
        t = _ease_in_out(t)
        bx, by = _cubic_bezier(t, p0, p1, p2, p3)
        # Micro-jitter (±1 px) to avoid pixel-perfect paths
        jx = random.randint(-1, 1) if 0 < i < steps else 0
        jy = random.randint(-1, 1) if 0 < i < steps else 0
        path.append((int(round(bx)) + jx, int(round(by)) + jy))

    # Ensure exact start & end
    path[0] = (start[0], start[1])
    path[-1] = (end[0], end[1])
    return path


def _ease_in_out(t: float) -> float:
    """Smooth-step ease-in-out: 3t² − 2t³."""
    return t * t * (3.0 - 2.0 * t)


# ── High-level mouse movement ───────────────────────────────────────────────

def move_mouse_smooth(
    start: Tuple[int, int],
    end: Tuple[int, int],
    duration: Optional[float] = None,
    spread: float = 0.35,
    move_fn: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Move the Windows cursor from *start* to *end* along a Bézier curve.

    Parameters
    ----------
    start, end : (x, y)
        Screen pixel coordinates.
    duration : float | None
        Total movement time in seconds.  ``None`` → auto-scale (~0.15–0.6 s).
    spread : float
        Curvature of the path (0.0 = straight line, 0.5 = very curved).
    move_fn : callable | None
        Custom ``(x, y) -> None`` function for cursor positioning.  When
        ``None`` falls back to ``SetCursorPos``.  Pass an Interception-based
        mover to avoid hookable Win32 calls.
    """
    _move = move_fn or _set_cursor_pos
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if duration is None:
        # Scale: short distances ≈ 0.1 s, long distances ≈ 0.5 s
        duration = max(0.08, min(0.55, 0.08 + dist / 2000.0))
        duration *= random.uniform(0.85, 1.15)  # ±15% variation

    points = bezier_path(start, end, spread=spread)
    n = len(points)
    if n < 2:
        _move(end[0], end[1])
        return

    interval = duration / (n - 1)
    _move(start[0], start[1])

    for i in range(1, n):
        t0 = time.perf_counter()
        _move(points[i][0], points[i][1])
        # High-precision sleep (busy-wait for sub-ms accuracy)
        target = t0 + interval
        while time.perf_counter() < target:
            pass

    # Final position guarantee
    _move(end[0], end[1])


def move_mouse_to(
    end: Tuple[int, int],
    duration: Optional[float] = None,
    spread: float = 0.35,
    move_fn: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Move cursor from its current position to *end* along a Bézier curve."""
    start = _get_cursor_pos()
    move_mouse_smooth(start, end, duration=duration, spread=spread, move_fn=move_fn)
