"""
MinimapCalibrator
-----------------
Auto-detects the minimap ROI and optimal ``tiles_wide`` from a single
game frame, then persists the tuned configuration to ``minimap_config.json``.

The calibrator works in three phases:

1. **ROI detection** — scans the right portion of the frame for a rectangular
   region whose colour distribution matches the Tibia minimap palette.
2. **tiles_wide sweep** — for each candidate ``tiles_wide`` value, builds a
   palette-quantized template, matches it against the reference floor map,
   and picks the value with the highest match score.
3. **Validation** — verifies that the best score exceeds a minimum threshold
   and that the detected position is plausible.

Usage::

    from src.minimap_calibrator import MinimapCalibrator
    cal = MinimapCalibrator()
    result = cal.calibrate(frame_bgr)
    if result.success:
        result.config.save()

CLI::

    python -m src.minimap_calibrator --source wgc --window Proyector
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

import cv2
import numpy as np

if TYPE_CHECKING:
    from .input_controller import InputController

from .minimap_radar import (
    MinimapConfig,
    _CHAR_DOT_RADIUS_FRAC,
    _MINIMAP_PALETTE,
    _REF_H,
    _REF_W,
    _STABLE_PALETTE_INDICES,
    _find_char_center,
    _has_minimap_palette,
    _quantize_to_palette,
    _strip_ui_border,
)
from .map_loader import TibiaMapLoader
from .models import BOUNDS, Coordinate

_log = logging.getLogger("wn.calibrator")

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CalibrationResult:
    """Output of :meth:`MinimapCalibrator.calibrate`."""

    success: bool
    config: MinimapConfig
    #: Best palette-match score achieved during tiles_wide sweep.
    best_score: float = 0.0
    #: Detected world coordinate (if matching succeeded).
    position: Optional[Coordinate] = None
    #: Scores for every tiles_wide candidate tested.
    sweep_scores: List[Tuple[int, float]] = field(default_factory=list)
    #: Diagnostics / human-readable log lines.
    messages: List[str] = field(default_factory=list)
    #: Cropped minimap image (BGR) — useful for visual inspection.
    minimap_crop: Optional[np.ndarray] = None


# ---------------------------------------------------------------------------
# Calibrator
# ---------------------------------------------------------------------------

# Candidate values to sweep — covers all Tibia zoom levels.
# Near 40 = normal zoom, 80-160 = zoomed-out minimap.
_TILES_WIDE_CANDIDATES = (
    list(range(25, 60, 2))       # fine grid around 2x zoom
    + list(range(60, 100, 5))    # medium grid
    + list(range(100, 125, 1))   # fine grid around 1:1 zoom (includes 109 exactly)
    + list(range(125, 210, 10))  # coarse grid for zoomed-out
)

# Minimum acceptable palette-match score to consider a calibration valid.
_MIN_ACCEPT_SCORE = 0.50

# When scanning for ROI, the minimap is always in the right ~15 % of the
# frame and the top ~25 %.
_ROI_SCAN_X_FRAC = 0.15   # search rightmost 15 %
_ROI_SCAN_Y_FRAC = 0.30   # search topmost 30 %

# Minimum palette fraction for a candidate block to be the minimap.
_MIN_PALETTE_FRAC = 0.25


class MinimapCalibrator:
    """Auto-calibrate minimap ROI and tiles_wide from a game frame.

    Parameters
    ----------
    loader : TibiaMapLoader, optional
        Map loader.  Created automatically if not given.
    floor : int
        Floor to match against (default 7 = surface).
    hint : Coordinate, optional
        If known, constrains the match to ±80 tiles around this point,
        which is faster and more accurate.
    """

    def __init__(
        self,
        loader: Optional[TibiaMapLoader] = None,
        floor: int = 7,
        hint: Optional[Coordinate] = None,
        auto_floor: bool = False,
    ) -> None:
        self._loader = loader or TibiaMapLoader(log_fn=_log.debug)
        self._floor = floor
        self._hint = hint
        self._auto_floor = auto_floor
        self._q_floor_cache: dict[int, np.ndarray] = {}

    # ── public API ──────────────────────────────────────────────────────────

    def calibrate(
        self,
        frame: np.ndarray,
        config: Optional[MinimapConfig] = None,
    ) -> CalibrationResult:
        """Run full calibration on *frame* and return results.

        Parameters
        ----------
        frame : np.ndarray
            Full OBS/WGC frame (BGR, any resolution).
        config : MinimapConfig, optional
            Base config to update.  If ``None``, loads the current one.
        """
        cfg = config or MinimapConfig.load()
        msgs: List[str] = []
        fh, fw = frame.shape[:2]
        msgs.append(f"Frame: {fw}×{fh}")

        # Phase 1 — validate / detect ROI
        roi, crop = self._find_minimap_roi(frame, cfg, msgs)
        if crop is None:
            msgs.append("FAIL: could not locate minimap in frame.")
            return CalibrationResult(
                success=False, config=cfg, messages=msgs,
            )

        cfg.roi = list(roi)
        msgs.append(f"ROI: {roi}  →  crop {crop.shape[1]}×{crop.shape[0]}")

        # Phase 2 — sweep tiles_wide (and optionally floors)
        if self._auto_floor:
            best_tw, best_score, best_pos, sweep, best_floor = (
                self._sweep_floors_and_tw(crop, msgs)
            )
            if best_floor is not None:
                self._floor = best_floor
                cfg.floor = best_floor
        else:
            q_floor = self._get_floor_quant(self._floor)
            best_tw, best_score, best_pos, sweep = self._sweep_tiles_wide(
                crop, q_floor, msgs,
            )

        if best_score < _MIN_ACCEPT_SCORE:
            msgs.append(
                f"FAIL: best score {best_score:.4f} < {_MIN_ACCEPT_SCORE} "
                f"(tiles_wide={best_tw})."
            )
            return CalibrationResult(
                success=False,
                config=cfg,
                best_score=best_score,
                sweep_scores=sweep,
                messages=msgs,
                minimap_crop=crop,
            )

        cfg.tiles_wide = best_tw
        msgs.append(
            f"BEST: tiles_wide={best_tw}  score={best_score:.4f}  "
            f"pos={best_pos}"
        )

        return CalibrationResult(
            success=True,
            config=cfg,
            best_score=best_score,
            position=best_pos,
            sweep_scores=sweep,
            messages=msgs,
            minimap_crop=crop,
        )

    # ── Phase 1: ROI detection ──────────────────────────────────────────────

    def _find_minimap_roi(
        self,
        frame: np.ndarray,
        cfg: MinimapConfig,
        msgs: List[str],
    ) -> Tuple[Tuple[int, int, int, int], Optional[np.ndarray]]:
        """Try the existing ROI first; if it fails, scan the frame.

        Returns (roi_xywh, cropped_bgr) or ((0,0,0,0), None).
        """
        # A) Try existing ROI (scaled to current frame)
        crop = self._crop_with_roi(frame, cfg.roi)
        if crop is not None:
            stripped = _strip_ui_border(crop.copy())
            if stripped.shape[0] >= 40 and stripped.shape[1] >= 40:
                if _has_minimap_palette(stripped, _MIN_PALETTE_FRAC):
                    msgs.append("Existing ROI validated OK.")
                    return tuple(cfg.roi), stripped  # type: ignore[return-value]
            msgs.append("Existing ROI failed palette check — scanning.")
        else:
            msgs.append("Existing ROI crop too small — scanning.")

        # B) Scan for minimap in the right part of the frame
        return self._scan_for_minimap(frame, msgs)

    def _crop_with_roi(
        self,
        frame: np.ndarray,
        roi: List[int],
    ) -> Optional[np.ndarray]:
        """Crop *frame* using *roi* (at 1920×1080 reference), returns BGR or None."""
        fh, fw = frame.shape[:2]
        rx, ry, rw, rh = roi
        sx = fw / _REF_W
        sy = fh / _REF_H
        x0 = max(0, int(rx * sx))
        y0 = max(0, int(ry * sy))
        x1 = min(fw, int((rx + rw) * sx))
        y1 = min(fh, int((ry + rh) * sy))
        if x1 - x0 < 30 or y1 - y0 < 30:
            return None
        return frame[y0:y1, x0:x1].copy()

    def _scan_for_minimap(
        self,
        frame: np.ndarray,
        msgs: List[str],
    ) -> Tuple[Tuple[int, int, int, int], Optional[np.ndarray]]:
        """Slide a window over the top-right quadrant to find the minimap."""
        fh, fw = frame.shape[:2]
        sx = fw / _REF_W
        sy = fh / _REF_H

        # Search region (in frame pixels)
        search_x0 = int(fw * (1.0 - _ROI_SCAN_X_FRAC))
        search_y0 = 0
        search_y1 = int(fh * _ROI_SCAN_Y_FRAC)

        best_roi: Tuple[int, int, int, int] = (0, 0, 0, 0)
        best_crop: Optional[np.ndarray] = None
        best_frac = 0.0

        # Try a range of square window sizes (minimap is roughly square)
        for win_size in range(200, 70, -10):
            ws = int(win_size * min(sx, sy))
            if ws < 50:
                continue
            step = max(5, ws // 6)
            for y in range(search_y0, search_y1 - ws + 1, step):
                for x in range(search_x0, fw - ws + 1, step):
                    block = frame[y : y + ws, x : x + ws]
                    stripped = _strip_ui_border(block.copy())
                    if stripped.shape[0] < 40 or stripped.shape[1] < 40:
                        continue
                    frac = self._palette_fraction(stripped)
                    if frac > best_frac and frac >= _MIN_PALETTE_FRAC:
                        best_frac = frac
                        # Convert back to reference 1920×1080 coords
                        ref_x = int(x / sx)
                        ref_y = int(y / sy)
                        ref_w = int(ws / sx)
                        ref_h = int(ws / sy)
                        best_roi = (ref_x, ref_y, ref_w, ref_h)
                        best_crop = stripped

        if best_crop is not None:
            msgs.append(
                f"Scan found minimap: ROI={best_roi} "
                f"palette={best_frac:.1%}"
            )
            return best_roi, best_crop

        return (0, 0, 0, 0), None

    @staticmethod
    def _palette_fraction(crop_bgr: np.ndarray) -> float:
        """Fraction of pixels matching any Tibia palette colour."""
        flat = crop_bgr.reshape(-1, 3).astype(np.int16)
        dists = np.abs(flat[:, None, :] - _MINIMAP_PALETTE[None, :, :]).max(axis=2)
        close = dists.min(axis=1) <= 25
        return float(close.mean())

    # ── Phase 2: tiles_wide sweep ───────────────────────────────────────────

    def _sweep_tiles_wide(
        self,
        crop_bgr: np.ndarray,
        q_floor: np.ndarray,
        msgs: List[str],
    ) -> Tuple[int, float, Optional[Coordinate], List[Tuple[int, float]]]:
        """Try every candidate tiles_wide and return the best.

        Returns (best_tw, best_score, best_position, all_scores).
        """
        q_crop = _quantize_to_palette(crop_bgr)
        mh, mw = crop_bgr.shape[:2]

        # Detect character marker position once (stable for all tw)
        cx_frac, cy_frac = _find_char_center(crop_bgr)

        best_tw = 40
        best_score = 0.0
        best_pos: Optional[Coordinate] = None
        results: List[Tuple[int, float]] = []

        for tw in _TILES_WIDE_CANDIDATES:
            th = max(1, int(mh * tw / mw))
            if th < 5 or tw < 5:
                continue
            # Downscale template
            q_tpl = cv2.resize(q_crop, (tw, th), interpolation=cv2.INTER_NEAREST)

            # Centre mask (centred on detected character dot)
            char_tx = round(tw * cx_frac)
            char_ty = round(th * cy_frac)
            mask = self._make_mask(th, tw, char_ty, char_tx)

            score, loc, ox, oy = self._palette_match(
                q_floor, q_tpl, mask,
            )
            tile_x = BOUNDS["xMin"] + ox + loc[0] + char_tx
            tile_y = BOUNDS["yMin"] + oy + loc[1] + char_ty
            results.append((tw, score))

            if score > best_score:
                best_score = score
                best_tw = tw
                best_pos = Coordinate(tile_x, tile_y, self._floor)

        msgs.append(f"Sweep: tested {len(results)} candidates.")
        # Log top-5
        top5 = sorted(results, key=lambda t: t[1], reverse=True)[:5]
        for tw, sc in top5:
            msgs.append(f"  tw={tw:3d} → {sc:.4f}")

        return best_tw, best_score, best_pos, results

    def _palette_match(
        self,
        q_floor: np.ndarray,
        q_tpl: np.ndarray,
        mask: np.ndarray,
    ) -> Tuple[float, Tuple[int, int], int, int]:
        """Palette-match counting (same algorithm as MinimapRadar).

        When a hint is set, restricts search to ±80 tiles around it.
        """
        th, tw = q_tpl.shape[:2]

        # Constrained or full search
        if self._hint is not None:
            cx = self._hint.x - BOUNDS["xMin"]
            cy = self._hint.y - BOUNDS["yMin"]
            pad = 80
            x0 = max(0, cx - pad - tw // 2)
            y0 = max(0, cy - pad - th // 2)
            x1 = min(q_floor.shape[1], cx + pad + tw)
            y1 = min(q_floor.shape[0], cy + pad + th)
            search = q_floor[y0:y1, x0:x1]
            off_x, off_y = x0, y0
            if search.shape[0] <= th or search.shape[1] <= tw:
                search = q_floor
                off_x, off_y = 0, 0
        else:
            search = q_floor
            off_x, off_y = 0, 0

        rh = search.shape[0] - th + 1
        rw = search.shape[1] - tw + 1
        if rh <= 0 or rw <= 0:
            return 0.0, (0, 0), 0, 0

        match_count = np.zeros((rh, rw), dtype=np.float32)
        n_active = 0.0

        for idx in _STABLE_PALETTE_INDICES:
            t_bin = ((q_tpl == idx) & mask).astype(np.float32)
            s = float(t_bin.sum())
            if s == 0:
                continue
            n_active += s
            f_bin = (search == idx).astype(np.float32)
            match_count += cv2.matchTemplate(f_bin, t_bin, cv2.TM_CCORR)

        if n_active == 0:
            return 0.0, (0, 0), off_x, off_y

        match_count /= n_active
        _, max_val, _, max_loc = cv2.minMaxLoc(match_count)
        return float(max_val), (int(max_loc[0]), int(max_loc[1])), off_x, off_y

    # ── Phase 2b: floor + tw sweep ──────────────────────────────────────────

    def _sweep_floors_and_tw(
        self,
        crop_bgr: np.ndarray,
        msgs: List[str],
    ) -> Tuple[int, float, Optional[Coordinate], List[Tuple[int, float]], Optional[int]]:
        """Sweep floors 0-15 for the one that matches best.

        Returns (best_tw, best_score, best_pos, sweep_scores, best_floor).
        """
        overall_best_tw = 40
        overall_best_score = 0.0
        overall_best_pos: Optional[Coordinate] = None
        overall_best_floor: Optional[int] = None
        overall_sweep: List[Tuple[int, float]] = []

        # Quick scan: test a few representative tw values per floor.
        # Use a 4x downscaled floor to speed up template matching — sufficient
        # for ranking floors (exact position found later in full sweep).
        _quick_tw = [30, 40, 50, 70, 100, 150]
        _QUICK_SCALE = 4
        floor_scores: List[Tuple[int, float]] = []

        # Precompute crop quantization and templates once (independent of floor)
        q_crop = _quantize_to_palette(crop_bgr)
        mh, mw = crop_bgr.shape[:2]
        _tpls_small: List[Tuple[np.ndarray, np.ndarray]] = []
        for tw in _quick_tw:
            th = max(1, int(mh * tw / mw))
            if th < 5 or tw < 5:
                continue
            tw_s = max(1, tw // _QUICK_SCALE)
            th_s = max(1, th // _QUICK_SCALE)
            q_tpl = cv2.resize(q_crop, (tw_s, th_s), interpolation=cv2.INTER_NEAREST)
            mask = self._make_mask(th_s, tw_s)
            _tpls_small.append((q_tpl, mask))

        for fl in range(16):
            qf_full = self._get_floor_quant(fl)
            fh, fw = qf_full.shape[:2]
            qf = cv2.resize(
                qf_full.astype(np.uint8),
                (max(1, fw // _QUICK_SCALE), max(1, fh // _QUICK_SCALE)),
                interpolation=cv2.INTER_NEAREST,
            )
            top_score = 0.0
            for q_tpl, mask in _tpls_small:
                score, _, _, _ = self._palette_match(qf, q_tpl, mask)
                if score > top_score:
                    top_score = score
            floor_scores.append((fl, top_score))

        floor_scores.sort(key=lambda t: t[1], reverse=True)
        msgs.append("Floor quick-scan:")
        for fl, sc in floor_scores[:5]:
            msgs.append(f"  floor {fl:2d}: {sc:.4f}")

        # Full sweep on top-3 floors
        for fl, _ in floor_scores[:3]:
            old_floor = self._floor
            self._floor = fl
            qf = self._get_floor_quant(fl)
            tw, sc, pos, sweep = self._sweep_tiles_wide(crop_bgr, qf, msgs)
            self._floor = old_floor
            if sc > overall_best_score:
                overall_best_score = sc
                overall_best_tw = tw
                overall_best_pos = pos
                overall_best_floor = fl
                overall_sweep = sweep

        if overall_best_floor is not None:
            msgs.append(f"Best floor: {overall_best_floor}")

        return (
            overall_best_tw, overall_best_score, overall_best_pos,
            overall_sweep, overall_best_floor,
        )

    @staticmethod
    def _make_mask(th: int, tw: int,
                   center_y: Optional[int] = None,
                   center_x: Optional[int] = None) -> np.ndarray:
        """Build the centre-dot exclusion mask."""
        mask = np.ones((th, tw), dtype=bool)
        cy = center_y if center_y is not None else th // 2
        cx = center_x if center_x is not None else tw // 2
        r = max(3, int(tw * _CHAR_DOT_RADIUS_FRAC))
        Y, X = np.ogrid[:th, :tw]
        mask = ((X - cx) ** 2 + (Y - cy) ** 2) > r ** 2
        return mask

    # ── helpers ─────────────────────────────────────────────────────────────

    def _get_floor_quant(self, floor: int) -> np.ndarray:
        """Lazy-load the quantized floor map."""
        if floor not in self._q_floor_cache:
            rgba = self._loader.get_map_image(floor)
            bgr = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
            self._q_floor_cache[floor] = _quantize_to_palette(bgr)
        return self._q_floor_cache[floor]


# ---------------------------------------------------------------------------
# Zoom auto-correction
# ---------------------------------------------------------------------------

# Expected tiles_wide range for "normal" Tibia minimap zoom.
# Below → zoomed-in too much (press "O" to zoom out).
# Above → zoomed-out too much (press "I" to zoom in).
# Setting min=100 forces ZoomGuard to always correct back to 1:1 zoom
# (tiles_wide ≈ 109), avoiding mismatches caused by previous sessions
# leaving the game at a narrow zoom (e.g. tw=53 or tw=27).
_ZOOM_TW_MIN = 100
_ZOOM_TW_MAX = 120

# Virtual-key codes for minimap zoom keys in Tibia.
_VK_I = 0x49  # zoom in
_VK_O = 0x4F  # zoom out

# How many zoom presses per correction attempt (Tibia changes ~5 tw per press).
_ZOOM_PRESSES_PER_STEP = 1
_ZOOM_SETTLE_SECS = 1.0  # wait after each press for the client to redraw


def ensure_minimap_zoom(
    frame_getter: Callable[[], Optional[np.ndarray]],
    ctrl: "InputController",
    *,
    loader: Optional[TibiaMapLoader] = None,
    floor: int = 7,
    hint: Optional["Coordinate"] = None,
    max_attempts: int = 10,
    log_fn: Optional[Callable[..., object]] = None,
) -> Optional[CalibrationResult]:
    """Check current minimap zoom and press I/O to correct it if needed.

    Returns the final :class:`CalibrationResult`, or *None* if the frame
    source is unavailable.

    Parameters
    ----------
    frame_getter : callable
        Returns a BGR frame (or None).
    ctrl : InputController
        Used to send I/O keypresses to the game window.
    loader, floor, hint :
        Forwarded to :class:`MinimapCalibrator`.
    max_attempts :
        Safety cap — stop after this many zoom presses even if still wrong.
    log_fn :
        Logging callback (e.g. ``session._log``).
    """
    log = log_fn or _log.info

    frame = frame_getter()
    if frame is None:
        log("[ZoomGuard] No frame available — skipping zoom check.")
        return None

    cal = MinimapCalibrator(loader=loader, floor=floor, hint=hint)
    result = cal.calibrate(frame)

    if not result.success:
        log(f"[ZoomGuard] Calibration failed (score {result.best_score:.3f}) "
            "— cannot determine zoom. Skipping correction.")
        return result

    tw = result.config.tiles_wide
    log(f"[ZoomGuard] Detected tiles_wide={tw} "
        f"(acceptable range {_ZOOM_TW_MIN}–{_ZOOM_TW_MAX})")

    if _ZOOM_TW_MIN <= tw <= _ZOOM_TW_MAX:
        log("[ZoomGuard] Zoom OK — no correction needed.")
        return result

    # Focus the game window so keypresses reach it.
    # Skip for interception/pico — hardware-level input doesn't need focus
    # and calling SetForegroundWindow interferes with window layout.
    _method = getattr(ctrl, "input_method", "")
    if hasattr(ctrl, "focus_now") and _method not in ("interception",):
        ctrl.focus_now()
        time.sleep(0.3)

    presses = 0
    prev_tw = tw
    unchanged_count = 0
    while presses < max_attempts:
        if _ZOOM_TW_MIN <= tw <= _ZOOM_TW_MAX:
            log(f"[ZoomGuard] Zoom corrected after {presses} press(es) → tw={tw}")
            return result

        if tw < _ZOOM_TW_MIN:
            log(f"[ZoomGuard] tw={tw} < {_ZOOM_TW_MIN} — pressing O (zoom out)")
            vk = _VK_O
        else:
            log(f"[ZoomGuard] tw={tw} > {_ZOOM_TW_MAX} — pressing I (zoom in)")
            vk = _VK_I

        for _ in range(_ZOOM_PRESSES_PER_STEP):
            ctrl.press_key(vk)
            presses += 1
        time.sleep(_ZOOM_SETTLE_SECS)

        # Re-capture and re-calibrate
        frame = frame_getter()
        if frame is None:
            log("[ZoomGuard] Lost frame after zoom press — aborting.")
            return result
        result = cal.calibrate(frame)
        if not result.success:
            log(f"[ZoomGuard] Re-calibration failed after press #{presses} — aborting.")
            return result
        tw = result.config.tiles_wide

        # Detect stuck: if tw hasn't changed after 3 consecutive presses,
        # the keys aren't reaching the game — stop wasting presses.
        if abs(tw - prev_tw) < 3:
            unchanged_count += 1
            if unchanged_count >= 3:
                log(f"[ZoomGuard] Zoom unresponsive after {presses} presses "
                    f"(tw stuck at {tw}). Keys may not be reaching the game.")
                break
        else:
            unchanged_count = 0
        prev_tw = tw

    log(f"[ZoomGuard] Could not correct zoom (tw={tw}). "
        "Radar will adapt to current zoom level.")
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    import argparse
    import time

    from .frame_capture import build_frame_getter
    from .input_controller import find_window

    parser = argparse.ArgumentParser(
        description="Auto-calibrate minimap ROI and tiles_wide.",
    )
    parser.add_argument(
        "--source", default="wgc",
        help="Frame source: wgc, obs, mss, printwindow (default: wgc).",
    )
    parser.add_argument(
        "--window", default="Proyector",
        help="Window title for wgc/mss/printwindow (default: Proyector).",
    )
    parser.add_argument(
        "--floor", type=int, default=7,
        help="Floor to match against (default: 7 = surface).",
    )
    parser.add_argument(
        "--auto-floor", action="store_true",
        help="Auto-detect the best floor (slower).",
    )
    parser.add_argument(
        "--hint", default="",
        help="Approximate position x,y (e.g. '32369,32241').",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save the calibrated config to minimap_config.json.",
    )
    parser.add_argument(
        "--frame", default="",
        help="Path to a saved frame PNG instead of live capture.",
    )
    args = parser.parse_args()

    hint: Optional[Coordinate] = None
    if args.hint:
        parts = args.hint.split(",")
        hint = Coordinate(int(parts[0]), int(parts[1]), args.floor)

    # Capture or load frame
    if args.frame:
        frame = cv2.imread(args.frame)
        if frame is None:
            print(f"ERROR: cannot read {args.frame}")
            return
        print(f"Loaded frame: {args.frame}")
    else:
        kwargs: dict = {}
        src = args.source.lower()
        if src in ("wgc", "mss", "printwindow"):
            info = find_window(args.window)
            if info:
                kwargs["hwnd"] = info.hwnd
                print(f"Window '{args.window}' → hwnd={info.hwnd:#x}")
            else:
                print(f"WARNING: window '{args.window}' not found.")
        elif src in ("obs", "virtualcam"):
            kwargs["device_index"] = 0

        getter = build_frame_getter(src, **kwargs)
        time.sleep(0.8)  # warm-up
        frame = None
        for _ in range(15):
            frame = getter()
            if frame is not None:
                break
            time.sleep(0.2)
        getter.close()  # type: ignore[attr-defined]
        if frame is None:
            print("ERROR: no frame captured.")
            return

    fh, fw = frame.shape[:2]
    print(f"Frame: {fw}×{fh}")

    cal = MinimapCalibrator(floor=args.floor, hint=hint, auto_floor=args.auto_floor)
    result = cal.calibrate(frame)

    print()
    for m in result.messages:
        print(f"  {m}")
    print()

    if result.success:
        print(f"  ✓ Calibration OK")
        print(f"    tiles_wide = {result.config.tiles_wide}")
        print(f"    floor      = {result.config.floor}")
        print(f"    ROI        = {result.config.roi}")
        print(f"    score      = {result.best_score:.4f}")
        print(f"    position   = {result.position}")

        if args.save:
            result.config.save()
            print(f"\n    Saved to minimap_config.json")
        else:
            print(f"\n    (use --save to persist)")
    else:
        print("  ✗ Calibration FAILED")
        if result.sweep_scores:
            best = sorted(result.sweep_scores, key=lambda t: t[1], reverse=True)[:3]
            print("    Top candidates:")
            for tw, sc in best:
                print(f"      tw={tw}: {sc:.4f}")

    # Save diagnostic images
    out = Path("output") / "calibration"
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "frame.png"), frame)
    if result.minimap_crop is not None:
        cv2.imwrite(str(out / "minimap_crop.png"), result.minimap_crop)
        up = cv2.resize(
            result.minimap_crop,
            (result.minimap_crop.shape[1] * 4, result.minimap_crop.shape[0] * 4),
            interpolation=cv2.INTER_NEAREST,
        )
        cv2.imwrite(str(out / "minimap_crop_4x.png"), up)
    print(f"\n    Diagnostics saved to {out}/")


if __name__ == "__main__":
    _cli_main()
