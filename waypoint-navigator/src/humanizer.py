"""Timing humanization — anti-pattern detection countermeasures.

Every delay in the bot should go through :func:`jittered_sleep` instead of
bare ``time.sleep``.  This adds Gaussian-distributed random variation to
defeat simple periodicity-based behavioral heuristics.

Features
--------
* **Gaussian jitter** — bell-curve distribution feels more human than uniform.
* **Micro-pauses** — rare random pauses (≈2 % of calls) simulate real-player
  distractions (checking phone, adjusting chair, etc.).
* **Fatigue model** — delays gradually increase over long sessions,
  mimicking human tiredness.  Resets on :func:`reset_fatigue`.

Usage::

    from .humanizer import jittered_sleep, humanize

    jittered_sleep(0.35)              # Gaussian ±15%, fatigue-adjusted
    val = humanize(0.35)              # returns jittered value, no sleep
    jittered_sleep(0.35, pct=0.3)     # override per-call: ±30%
"""

from __future__ import annotations

import random
import threading
import time
from typing import Callable, Optional

# ── Global jitter percentage ------------------------------------------------
JITTER_PCT: float = 0.15
_jitter_lock = threading.Lock()

# ── Micro-pause parameters --------------------------------------------------
_MICRO_PAUSE_PROB: float = 0.006       # 0.6% chance per call (~1 per 5 min of walking)
_MICRO_PAUSE_MIN: float = 0.4          # seconds
_MICRO_PAUSE_MAX: float = 2.2          # seconds

# ── Macro-pause parameters (simulates checking phone, reading chat, etc.) ---
_MACRO_PAUSE_MIN_STEPS: int = 280      # fire after this many steps minimum (~2 min)
_MACRO_PAUSE_MAX_STEPS: int = 720      # fire after this many steps maximum (~5 min)
_MACRO_PAUSE_MIN_S: float = 3.0        # pause duration min
_MACRO_PAUSE_MAX_S: float = 9.0        # pause duration max
_macro_next_at: int = 0                # step counter target for next macro-pause
_macro_step_counter: int = 0           # rolling step counter

# ── Fatigue model ------------------------------------------------------------
_fatigue_start: Optional[float] = None  # monotonic time when session started
_FATIGUE_RAMP_HOURS: float = 4.0       # hours until max fatigue
_FATIGUE_MAX_EXTRA: float = 0.12       # max fractional increase at full fatigue


def set_jitter(pct: float) -> None:
    """Set the global jitter percentage (0.0–1.0)."""
    global JITTER_PCT
    with _jitter_lock:
        JITTER_PCT = max(0.0, min(1.0, pct))


def reset_fatigue() -> None:
    """Reset the fatigue counter (call at session start or after breaks)."""
    global _fatigue_start
    with _jitter_lock:
        _fatigue_start = time.monotonic()


def _fatigue_factor() -> float:
    """Return a multiplier in [1.0, 1.0 + _FATIGUE_MAX_EXTRA]."""
    with _jitter_lock:
        start = _fatigue_start
    if start is None:
        return 1.0
    elapsed_h = (time.monotonic() - start) / 3600.0
    ratio = min(elapsed_h / _FATIGUE_RAMP_HOURS, 1.0)
    return 1.0 + _FATIGUE_MAX_EXTRA * ratio


def humanize(base: float, pct: Optional[float] = None) -> float:
    """Return *base* with Gaussian ±*pct* variation, fatigue-adjusted.

    Uses ``random.gauss`` clipped to ±2σ for a natural bell-curve feel.
    Minimum returned value is 0.001 s.
    """
    with _jitter_lock:
        p = pct if pct is not None else JITTER_PCT
    if p <= 0:
        return max(0.001, base * _fatigue_factor())
    # Gaussian with σ = pct/2 so that ±2σ ≈ ±pct
    sigma = p / 2.0
    delta = random.gauss(0.0, sigma)
    # Clip to ±pct to avoid extreme outliers
    delta = max(-p, min(p, delta))
    result = base * (1.0 + delta) * _fatigue_factor()
    return max(0.001, result)


def micro_pause() -> float:
    """Possibly insert a short random pause simulating human distraction.

    Returns the actual pause duration (0.0 if no pause was triggered).
    """
    if random.random() < _MICRO_PAUSE_PROB:
        pause = random.uniform(_MICRO_PAUSE_MIN, _MICRO_PAUSE_MAX)
        time.sleep(pause)
        return pause
    return 0.0


def macro_pause(log_fn: Optional[Callable[[str], None]] = None) -> float:
    """Occasional long pause (3-9s) simulating player distraction.

    Call once per walker step. Fires every 280-720 steps (~2-5 min at 0.45s/step).
    Returns actual pause duration (0.0 if not triggered this call).
    """
    global _macro_step_counter, _macro_next_at
    with _jitter_lock:
        _macro_step_counter += 1
        if _macro_next_at == 0:
            # Initialise on first call
            _macro_next_at = random.randint(_MACRO_PAUSE_MIN_STEPS, _MACRO_PAUSE_MAX_STEPS)
        if _macro_step_counter < _macro_next_at:
            return 0.0
        # Fire macro-pause
        _macro_step_counter = 0
        _macro_next_at = random.randint(_MACRO_PAUSE_MIN_STEPS, _MACRO_PAUSE_MAX_STEPS)
    pause = random.uniform(_MACRO_PAUSE_MIN_S, _MACRO_PAUSE_MAX_S)
    if log_fn:
        log_fn(f"  [H] macro-pause {pause:.1f}s")
    time.sleep(pause)
    return pause


def jittered_sleep(base: float, pct: Optional[float] = None) -> float:
    """``time.sleep(humanize(base, pct))`` — returns actual total delay.

    Includes a possible micro-pause on top of the jittered delay.
    """
    actual = humanize(base, pct)
    time.sleep(actual)
    actual += micro_pause()
    return actual
