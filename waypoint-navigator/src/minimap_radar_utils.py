from __future__ import annotations

from typing import Any, Mapping

import numpy as np


def find_char_center(
    crop_bgr: np.ndarray,
    *,
    cv2_module: Any,
    char_search_margin: float,
    char_brightness_min: int,
    char_min_pixels: int,
) -> tuple[float, float]:
    h, w = crop_bgr.shape[:2]
    gray = cv2_module.cvtColor(crop_bgr, cv2_module.COLOR_BGR2GRAY)
    my, mx = int(h * char_search_margin), int(w * char_search_margin)
    roi = gray[my:h - my, mx:w - mx]
    peak = roi.max()
    if peak < char_brightness_min:
        return 0.5, 0.5
    mask = roi == peak
    ys, xs = np.where(mask)
    if len(ys) < char_min_pixels:
        mask = roi > char_brightness_min
        ys, xs = np.where(mask)
    if len(ys) >= 2:
        cy = float(np.mean(ys)) + my
        cx = float(np.mean(xs)) + mx
        return cx / w, cy / h
    return 0.5, 0.5


def is_border_line(
    pixels: np.ndarray,
    *,
    threshold: int,
    border_coverage_min: float,
    border_neutral_coverage: float,
) -> bool:
    if pixels.ndim <= 1:
        dark = pixels < threshold
        return bool(dark.mean() > border_coverage_min and float(pixels.std()) < 30)
    maxc = pixels.max(axis=-1)
    dark = maxc < threshold
    if dark.mean() > border_coverage_min and float(pixels.std()) < 30:
        return True
    minc = pixels.min(axis=-1)
    neutral = (maxc - minc) < 10
    in_range = (maxc >= 100) & (maxc <= 135)
    if (neutral & in_range).mean() > border_neutral_coverage and float(maxc.std()) < 15:
        return True
    return False


def strip_ui_border(
    crop: np.ndarray,
    *,
    max_strip: int,
    is_border_line_fn: Any,
) -> np.ndarray:
    h, w = crop.shape[:2]
    top = 0
    while top < max_strip and top < h and is_border_line_fn(crop[top]):
        top += 1
    bot = h
    while bot > h - max_strip and bot > top and is_border_line_fn(crop[bot - 1]):
        bot -= 1
    left = 0
    while left < max_strip and left < w and is_border_line_fn(crop[:, left]):
        left += 1
    right = w
    while right > w - max_strip and right > left and is_border_line_fn(crop[:, right - 1]):
        right -= 1
    return crop[top:bot, left:right]


def quantize_and_check(
    crop_bgr: np.ndarray,
    *,
    palette: np.ndarray,
    min_fraction: float,
) -> tuple[np.ndarray, bool]:
    h, w = crop_bgr.shape[:2]
    flat = crop_bgr.reshape(-1, 3).astype(np.int16)

    dists = np.abs(flat[:, 0:1] - palette[:, 0])
    np.maximum(dists, np.abs(flat[:, 1:2] - palette[:, 1]), out=dists)
    np.maximum(dists, np.abs(flat[:, 2:3] - palette[:, 2]), out=dists)

    best_idx = dists.argmin(axis=1)
    min_dists = dists[np.arange(len(best_idx)), best_idx]
    is_valid = bool((min_dists <= 25).mean() >= min_fraction)
    return best_idx.astype(np.uint8).reshape(h, w), is_valid


def match_with_hint(
    floor_gray: np.ndarray,
    template: np.ndarray,
    *,
    hint_x: int,
    hint_y: int,
    t_w: int,
    t_h: int,
    padding: int,
    bounds: Mapping[str, int],
    cv2_module: Any,
) -> tuple[np.ndarray, int, int]:
    fh, fw = floor_gray.shape[:2]
    cx = hint_x - bounds["xMin"]
    cy = hint_y - bounds["yMin"]

    x0 = max(0, cx - padding - t_w // 2)
    y0 = max(0, cy - padding - t_h // 2)
    x1 = min(fw, cx + padding + t_w)
    y1 = min(fh, cy + padding + t_h)

    area = floor_gray[y0:y1, x0:x1]
    if area.shape[0] <= t_h or area.shape[1] <= t_w:
        result = cv2_module.matchTemplate(floor_gray, template, cv2_module.TM_CCOEFF_NORMED)
        return result, 0, 0

    result = cv2_module.matchTemplate(area, template, cv2_module.TM_CCOEFF_NORMED)
    return result, x0, y0