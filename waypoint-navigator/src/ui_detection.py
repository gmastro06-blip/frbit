"""
ui_detection — Visual UI element detection helpers
---------------------------------------------------
Frame-diff and edge-detection utilities shared by :mod:`looter` and
:mod:`depot_manager` to locate context menus and container windows
without hard-coded pixel offsets.

All functions are pure (no I/O, no state) and accept/return NumPy arrays
plus simple tuples, so they are easy to test with synthetic frames.
"""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Context-menu detection (frame diff)
# ---------------------------------------------------------------------------

def detect_context_menu(
    frame_before: np.ndarray,
    frame_after: np.ndarray,
    click_x: int,
    click_y: int,
    *,
    search_margin: int = 120,
    min_area: int = 800,
) -> Optional[Tuple[int, int, int, int]]:
    """Detect a context menu that appeared between two frames.

    Computes the absolute difference between *frame_before* and *frame_after*
    around the click point, then finds the largest bright rectangular region.

    Returns ``(x, y, w, h)`` of the menu bounding box, or ``None``.
    """
    if frame_before is None or frame_after is None:
        return None
    h, w = frame_after.shape[:2]
    x1 = max(0, click_x - search_margin)
    y1 = max(0, click_y - 20)
    x2 = min(w, click_x + search_margin)
    y2 = min(h, click_y + search_margin * 2)
    crop_b = frame_before[y1:y2, x1:x2]
    crop_a = frame_after[y1:y2, x1:x2]
    if crop_b.size == 0 or crop_a.size == 0:
        return None

    diff = cv2.absdiff(crop_a, crop_b)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY) if diff.ndim == 3 else diff
    _, mask = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < min_area:
        return None
    bx, by, bw, bh = cv2.boundingRect(largest)
    return (x1 + bx, y1 + by, bw, bh)


# ---------------------------------------------------------------------------
# Context-menu entry location (Sobel edge analysis)
# ---------------------------------------------------------------------------

def find_menu_entry_offset(
    frame: np.ndarray,
    menu_roi: Tuple[int, int, int, int],
    entry_index: int = 0,
) -> Optional[Tuple[int, int]]:
    """Estimate the click position for a specific context-menu entry.

    Tibia context menus have evenly-spaced text entries (~18-22 px apart
    at 1080p).  This function divides the menu into rows by Sobel edge
    analysis and returns the centre of the requested row.

    Parameters
    ----------
    frame : np.ndarray
        Current frame.
    menu_roi : tuple
        ``(x, y, w, h)`` of the detected menu.
    entry_index : int
        0-based index of the entry to click (0 = first = "Open").

    Returns
    -------
    ``(click_x, click_y)`` or ``None``.
    """
    mx, my, mw, mh = menu_roi
    h, w = frame.shape[:2]
    mx = max(0, mx)
    my = max(0, my)
    crop = frame[my: min(my + mh, h), mx: min(mx + mw, w)]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    # Detect horizontal edges (row separators)
    sobel = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    row_energy = np.mean(np.abs(sobel), axis=1)
    # Find peaks in row_energy -> row separators
    threshold = float(np.mean(row_energy) + np.std(row_energy))
    separators: list[int] = []
    in_peak = False
    for i, val in enumerate(row_energy):
        if val > threshold and not in_peak:
            separators.append(i)
            in_peak = True
        elif val <= threshold:
            in_peak = False

    if len(separators) > 1:
        # Rows between separators
        row_centers: list[int] = []
        for i in range(len(separators) - 1):
            center_y = (separators[i] + separators[i + 1]) // 2
            row_centers.append(center_y)
        # Add last row (after last separator)
        if separators[-1] < mh - 5:
            row_centers.append((separators[-1] + mh) // 2)
        # Resolve negative index (e.g. -1 = last entry)
        idx = entry_index if entry_index >= 0 else max(0, len(row_centers) + entry_index)
        if idx < len(row_centers):
            click_y_out = my + row_centers[idx]
            click_x_out = mx + mw // 2
            return (click_x_out, click_y_out)

    # Fallback: assume ~20 px per entry
    entry_height = max(16, mh // max(3, mh // 20))
    # Resolve negative index against estimated row count
    n_rows = max(1, mh // entry_height)
    idx = entry_index if entry_index >= 0 else max(0, n_rows + entry_index)
    click_y_out = my + entry_height // 2 + idx * entry_height
    click_x_out = mx + mw // 2
    if click_y_out < my + mh:
        return (click_x_out, click_y_out)
    return None


# ---------------------------------------------------------------------------
# Container / loot window detection (Canny edge)
# ---------------------------------------------------------------------------

def detect_container_window(
    frame: np.ndarray,
    search_roi: Optional[Tuple[int, int, int, int]] = None,
    *,
    min_width: int = 150,
    min_height: int = 100,
) -> Optional[Tuple[int, int, int, int]]:
    """Detect an open container / loot window in the frame.

    Uses Canny edge detection to find a large rectangular UI panel.
    The Tibia container has a distinctive darker title bar and lighter body
    with item slots.

    Parameters
    ----------
    frame : np.ndarray
        Current frame (BGR).
    search_roi : optional ``(x, y, w, h)``
        Limit search to this region.  ``None`` → right third of the screen.
    min_width, min_height : int
        Minimum dimensions for a valid container.

    Returns
    -------
    ``(x, y, w, h)`` of the detected container, or ``None``.
    """
    if frame is None or frame.size == 0:
        return None

    fh, fw = frame.shape[:2]
    if search_roi is not None:
        rx, ry, rw, rh = search_roi
    else:
        # Default: right third of the screen (where containers typically open)
        rx = fw * 2 // 3
        ry = 0
        rw = fw - rx
        rh = fh

    rx = max(0, min(rx, fw))
    ry = max(0, min(ry, fh))
    crop = frame[ry: min(ry + rh, fh), rx: min(rx + rw, fw)]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best: Optional[Tuple[int, int, int, int]] = None
    best_area = 0

    for cnt in contours:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bw < min_width or bh < min_height:
            continue
        area = bw * bh
        # Prefer containers of moderate aspect ratio (not too wide or narrow)
        aspect = bw / max(bh, 1)
        if 0.3 < aspect < 3.0 and area > best_area:
            best_area = area
            best = (rx + bx, ry + by, bw, bh)

    return best


# ---------------------------------------------------------------------------
# Resolution scaling helpers
# ---------------------------------------------------------------------------

def scale_offset_y(offset_px: int, frame: np.ndarray, ref_height: int = 1080) -> int:
    """Scale a vertical pixel offset from *ref_height* to the actual frame height."""
    if frame is None or frame.size == 0:
        return offset_px
    fh = frame.shape[0]
    return max(1, int(round(offset_px * fh / ref_height)))


def scale_offset_x(offset_px: int, frame: np.ndarray, ref_width: int = 1920) -> int:
    """Scale a horizontal pixel offset from *ref_width* to the actual frame width."""
    if frame is None or frame.size == 0:
        return offset_px
    fw = frame.shape[1]
    return max(1, int(round(offset_px * fw / ref_width)))
