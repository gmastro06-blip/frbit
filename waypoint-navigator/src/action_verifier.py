"""Feedback-loop verification for actions.

Each verifier function follows the same pattern:
1. Capture state BEFORE the action.
2. (Caller executes the action.)
3. Call the verifier to confirm the expected effect happened.
4. If not, retry the action up to *max_retries* times.

The ``@with_retry`` decorator wraps any action callable with this pattern.

Usage::

    from src.action_verifier import with_retry, verify_position_changed

    @with_retry(max_attempts=3, verify=verify_position_changed)
    def walk_to(ctrl, dest): ...
"""

from __future__ import annotations

import functools
import logging
import random
import time
from typing import Any, Callable, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from src.minimap_radar import MinimapRadar
    from src.hpmp_detector import HpMpDetector
    from src.models import Coordinate

logger = logging.getLogger("wn.av")

# ---------------------------------------------------------------------------
# Low-level verifiers
# ---------------------------------------------------------------------------

def verify_position_changed(
    radar: "MinimapRadar",
    old_pos: Optional["Coordinate"],
    frame_getter: Callable[[], Optional["np.ndarray"]],
    *,
    timeout: float = 3.0,
    poll_interval: float = 0.25,
) -> bool:
    """Return True if the minimap position differs from *old_pos* within *timeout* seconds.

    Polls periodically so it doesn't burn CPU.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is None:
            time.sleep(poll_interval * random.uniform(0.8, 1.2))
            continue
        hint = old_pos  # use old position as hint for faster matching
        new_pos = radar.read(frame, hint=hint)
        if new_pos is not None and old_pos is not None:
            if (new_pos.x != old_pos.x or new_pos.y != old_pos.y or new_pos.z != old_pos.z):
                return True
        elif new_pos is not None and old_pos is None:
            return True  # went from unknown to known — movement happened
        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return False


def verify_hp_changed(
    detector: "HpMpDetector",
    old_hp: Optional[int],
    frame_getter: Callable[[], Optional["np.ndarray"]],
    *,
    direction: str = "up",
    timeout: float = 2.0,
    poll_interval: float = 0.2,
) -> bool:
    """Return True if HP moved in the expected *direction* ('up' or 'down').

    Uses ``read_bars`` so we get a fresh reading each poll.
    """
    if old_hp is None:
        return False  # cannot verify without baseline

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is None:
            time.sleep(poll_interval * random.uniform(0.8, 1.2))
            continue
        hp, _mp = detector.read_bars(frame)
        if hp is not None:
            if direction == "up" and hp > old_hp:
                return True
            if direction == "down" and hp < old_hp:
                return True
        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return False


def verify_mp_changed(
    detector: "HpMpDetector",
    old_mp: Optional[int],
    frame_getter: Callable[[], Optional["np.ndarray"]],
    *,
    direction: str = "up",
    timeout: float = 2.0,
    poll_interval: float = 0.2,
) -> bool:
    """Return True if MP moved in the expected *direction*."""
    if old_mp is None:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is None:
            time.sleep(poll_interval * random.uniform(0.8, 1.2))
            continue
        _hp, mp = detector.read_bars(frame)
        if mp is not None:
            if direction == "up" and mp > old_mp:
                return True
            if direction == "down" and mp < old_mp:
                return True
        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return False


def verify_target_selected(
    combat_mgr: Any,
    *,
    timeout: float = 2.0,
    poll_interval: float = 0.3,
) -> bool:
    """Return True if the CombatManager reports an active target (``is_in_combat``)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if combat_mgr.is_in_combat:
            return True
        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return False


def verify_dialog_open(
    frame_getter: Callable[[], Optional["np.ndarray"]],
    template: Optional["np.ndarray"] = None,
    *,
    timeout: float = 2.0,
    poll_interval: float = 0.3,
    threshold: float = 0.75,
) -> bool:
    """Return True if an NPC dialog is detected on screen.

    If *template* is provided, uses ``cv2.matchTemplate``.
    Otherwise falls back to a simple heuristic: presence of a mostly-white
    rectangular region in the lower-center of the frame which is where the
    NPC dialog appears in the Tibia client.
    """
    import cv2
    import numpy as np

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is None:
            time.sleep(poll_interval * random.uniform(0.8, 1.2))
            continue

        if template is not None:
            res = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            if res.max() >= threshold:
                return True
        else:
            # Heuristic: NPC dialog is a light-colored box in the centre
            h, w = frame.shape[:2]
            roi = frame[int(h * 0.20):int(h * 0.60), int(w * 0.15):int(w * 0.70)]
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            white_ratio = float(np.count_nonzero(gray > 200)) / max(gray.size, 1)
            if white_ratio > 0.25:
                return True

        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return False


def find_dialog_option(
    frame_getter: Callable[[], Optional["np.ndarray"]],
    keyword: str,
    *,
    timeout: float = 3.0,
    poll_interval: float = 0.4,
    ocr_reader: Any = None,
    debug_save: bool = True,
) -> Optional[Tuple[int, int]]:
    """Find a clickable keyword in the NPC dialog and return ``(x, y)`` in frame coords.

    Tibia's NPC dialog renders clickable keywords (like *trade*, *deposit all*)
    in a distinct blue colour (BGR ≈ 255, 153, 51).  The detection strategy
    isolates those pixels via a colour mask, groups them into word-level
    clusters using connected-components on a morphologically-closed mask,
    and returns the centroid of the best cluster.  No OCR is needed because
    the blue keyword colour is unique to clickable dialog links.
    """
    import cv2
    import numpy as np

    kw_lower = keyword.lower().strip()
    deadline = time.monotonic() + timeout
    _diag_saved = False

    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is None:
            time.sleep(poll_interval * random.uniform(0.8, 1.2))
            continue

        h, w = frame.shape[:2]
        # NPC dialog appears in the centre region of the screen
        y1, y2 = int(h * 0.30), int(h * 0.75)
        x1, x2 = int(w * 0.15), int(w * 0.70)
        roi = frame[y1:y2, x1:x2]

        # ── Blue keyword colour mask ────────────────────────────────
        # Clickable keywords: BGR ≈ (255, 153, 51) = bright blue in RGB
        blue_mask = cv2.inRange(
            roi,
            np.array([180, 60, 0], dtype=np.uint8),
            np.array([255, 220, 110], dtype=np.uint8),
        )

        npx = int(np.count_nonzero(blue_mask))
        if npx < 20:
            time.sleep(poll_interval * random.uniform(0.8, 1.2))
            continue

        # Close small gaps between letters to form word-level blobs
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (8, 4))
        closed = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel)

        n_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(
            closed, connectivity=8,
        )

        # Collect word-sized clusters (skip background label 0)
        clusters: list[Tuple[int, int, int]] = []  # (area, cx_frame, cy_frame)
        for i in range(1, n_labels):
            area = int(stats[i, cv2.CC_STAT_AREA])
            cw = int(stats[i, cv2.CC_STAT_WIDTH])
            ch = int(stats[i, cv2.CC_STAT_HEIGHT])
            if area >= 20 and cw >= 5 and ch >= 3:
                cx = int(centroids[i][0]) + x1
                cy = int(centroids[i][1]) + y1
                clusters.append((area, cx, cy))

        if clusters:
            # Pick the largest cluster (most likely the primary keyword)
            clusters.sort(key=lambda c: c[0], reverse=True)
            _area, cx, cy = clusters[0]
            logger.debug(
                "dialog blue cluster: area=%d center=(%d,%d) total_clusters=%d",
                _area, cx, cy, len(clusters),
            )
            return (cx, cy)

        # ── Diagnostic: save images on first miss ───────────────────
        if debug_save and not _diag_saved:
            _diag_saved = True
            import sys
            if not getattr(sys, 'frozen', False):
                try:
                    import os
                    diag_dir = os.path.join(
                        os.path.dirname(os.path.dirname(__file__)),
                        "output", "diag_npc",
                    )
                    os.makedirs(diag_dir, exist_ok=True)
                    cv2.imwrite(os.path.join(diag_dir, f"frame_{kw_lower}.png"), frame)
                    cv2.imwrite(os.path.join(diag_dir, f"roi_{kw_lower}.png"), roi)
                    cv2.imwrite(os.path.join(diag_dir, f"blue_{kw_lower}.png"), blue_mask)
                    log_path = os.path.join(diag_dir, f"diag_{kw_lower}.txt")
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write(f"keyword: {kw_lower}\nframe: {w}x{h}\n")
                        f.write(f"roi: x={x1}-{x2}, y={y1}-{y2}\n")
                        f.write(f"blue_pixels: {npx}\n")
                        f.write(f"clusters (>20 area): {len(clusters)}\n")
                        for a, ccx, ccy in clusters:
                            f.write(f"  area={a} center=({ccx},{ccy})\n")
                    logger.info("dialog diag saved to %s", diag_dir)
                except Exception:
                    logger.debug("Failed to save dialog diagnostics", exc_info=True)

        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return None


def verify_frame_valid(
    frame_getter: Callable[[], Optional["np.ndarray"]],
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.5,
) -> bool:
    """Return True if a non-black, non-None frame can be obtained."""
    import numpy as np

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is not None and frame.size > 0:
            if np.mean(frame) > 5:  # not black
                return True
        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return False


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def with_retry(
    max_attempts: int = 3,
    verify: Optional[Callable[..., bool]] = None,
    delay_between: float = 0.5,
    on_fail: Optional[Callable[[int, Exception | None], None]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that retries an action up to *max_attempts* times.

    Parameters
    ----------
    max_attempts : int
        Total number of attempts (including the first).
    verify : callable, optional
        A callable returning ``True`` if the action succeeded.
        It receives the return value of the wrapped function.
        If ``None``, the action is considered successful if it doesn't raise.
    delay_between : float
        Seconds to wait between retries.
    on_fail : callable, optional
        Called on each failed attempt with ``(attempt_number, exception_or_None)``.

    The decorated function's return value is passed through on success.
    If all attempts fail, the last exception is re-raised, or ``ActionVerificationError``
    is raised if the action ran but verification failed.
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "[retry] %s attempt %d/%d raised %s",
                        fn.__name__, attempt, max_attempts, exc,
                    )
                    if on_fail:
                        on_fail(attempt, exc)
                    if attempt < max_attempts:
                        time.sleep(delay_between * random.uniform(0.8, 1.2))
                    continue

                # Action didn't raise — verify if requested
                if verify is not None:
                    ok = verify(result)
                    if ok:
                        return result
                    logger.warning(
                        "[retry] %s attempt %d/%d: verification failed",
                        fn.__name__, attempt, max_attempts,
                    )
                    if on_fail:
                        on_fail(attempt, None)
                    if attempt < max_attempts:
                        time.sleep(delay_between * random.uniform(0.8, 1.2))
                    continue
                else:
                    return result

            # All attempts exhausted
            if last_exc is not None:
                raise last_exc
            raise ActionVerificationError(
                f"{fn.__name__} failed verification after {max_attempts} attempts"
            )
        return wrapper
    return decorator


class ActionVerificationError(Exception):
    """Raised when an action passes execution but fails post-verification."""


# ---------------------------------------------------------------------------
# High-level helpers for integration with ScriptExecutor
# ---------------------------------------------------------------------------

def make_walk_verifier(
    radar: "MinimapRadar",
    frame_getter: Callable[[], Optional["np.ndarray"]],
    timeout: float = 3.0,
) -> Callable[["Coordinate", "Coordinate"], bool]:
    """Return a callable ``(old_pos, new_pos_expected) -> bool`` for walk verification.

    Checks that the minimap position changed from *old_pos*.
    """
    def _verify(old_pos: "Coordinate", _expected: "Coordinate") -> bool:
        return verify_position_changed(radar, old_pos, frame_getter, timeout=timeout)
    return _verify


def make_heal_verifier(
    detector: "HpMpDetector",
    frame_getter: Callable[[], Optional["np.ndarray"]],
    timeout: float = 2.0,
) -> Callable[[int], bool]:
    """Return a callable ``(old_hp) -> bool`` for heal verification."""
    def _verify(old_hp: int) -> bool:
        return verify_hp_changed(detector, old_hp, frame_getter, direction="up", timeout=timeout)
    return _verify


# ---------------------------------------------------------------------------
# Floor / death verifiers
# ---------------------------------------------------------------------------

def verify_floor_changed(
    radar: "MinimapRadar",
    old_z: int,
    frame_getter: Callable[[], Optional["np.ndarray"]],
    *,
    timeout: float = 3.0,
    poll_interval: float = 0.3,
) -> bool:
    """Return True if the z-level differs from *old_z* within *timeout* seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is None:
            time.sleep(poll_interval * random.uniform(0.8, 1.2))
            continue
        pos = radar.read(frame)
        if pos is not None and pos.z != old_z:
            return True
        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return False


def verify_death_dismissed(
    frame_getter: Callable[[], Optional["np.ndarray"]],
    death_checker: Callable[["np.ndarray"], bool],
    *,
    timeout: float = 3.0,
    poll_interval: float = 0.5,
) -> bool:
    """Return True if the death screen is no longer visible within *timeout* seconds.

    Parameters
    ----------
    death_checker : callable
        A function ``(frame) -> bool`` that returns True when the death screen
        is detected (e.g. ``DeathHandler.check_now``).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = frame_getter()
        if frame is not None and not death_checker(frame):
            return True
        time.sleep(poll_interval * random.uniform(0.8, 1.2))
    return False


# ---------------------------------------------------------------------------
# Character active check
# ---------------------------------------------------------------------------

def verify_char_active(
    frame_getter: Callable[[], Optional["np.ndarray"]],
    *,
    death_checker: Optional[Callable[["np.ndarray"], bool]] = None,
    login_checker: Optional[Callable[["np.ndarray"], bool]] = None,
    hp_reader: Optional[Callable[["np.ndarray"], Optional[int]]] = None,
) -> bool:
    """Return True if the character is active (alive, logged in, responsive).

    Checks (in order):
    1. Frame is available and not black.
    2. Not on the death screen (if *death_checker* provided).
    3. Not on the login/disconnect screen (if *login_checker* provided).
    4. HP is readable and > 0 (if *hp_reader* provided).

    Parameters
    ----------
    frame_getter : callable
        Returns the current game frame (BGR numpy array) or None.
    death_checker : callable, optional
        ``(frame) -> bool`` — returns True when death screen detected.
    login_checker : callable, optional
        ``(frame) -> bool`` — returns True when login/disconnect screen detected.
    hp_reader : callable, optional
        ``(frame) -> Optional[int]`` — returns HP% (0-100) or None.
    """
    import numpy as np

    frame = frame_getter()
    if frame is None or frame.size == 0:
        return False

    # Black frame (window minimized / not rendering)
    if np.mean(frame) < 5:
        return False

    if death_checker is not None and death_checker(frame):
        return False

    if login_checker is not None and login_checker(frame):
        return False

    if hp_reader is not None:
        hp = hp_reader(frame)
        if hp is not None and hp <= 0:
            return False

    return True
