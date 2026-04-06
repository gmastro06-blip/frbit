"""Real-time path visualizer — planned A* path vs actual walked path.

Renders a PNG after each waypoint segment showing:
  - Floor map background (grayscale, from tibia-map-data)
  - GREEN dots/lines = planned A* path
  - RED dots = actual dead-reckoned + radar positions
  - YELLOW cross = waypoint targets
  - WHITE text = step numbers and stats

Output: ``output/path_trace/segment_NNN.png`` and ``output/masks/segment_NNN.png``
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

import cv2
import numpy as np

_log = logging.getLogger("wn.pathviz")

# Tibia map bounds — duplicated here to avoid circular imports.
_X_MIN = 31744
_Y_MIN = 30976


@dataclass
class StepRecord:
    """One step in a walk segment."""
    planned_x: int
    planned_y: int
    actual_x: int  # dead-reckoned or radar-confirmed
    actual_y: int
    radar_confirmed: bool
    step_idx: int
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class SegmentMetrics:
    planned_steps: int = 0
    direct_distance: int = 0
    stretch_ratio: float = 1.0


class PathVisualizer:
    """Accumulates planned/actual positions and renders trace images.

    Usage::

        viz = PathVisualizer(map_loader, output_dir=Path("output/path_trace"))
        viz.begin_segment(segment_id=0, dest=(32369, 32236, 7))
        # during walk:
        viz.record_step(planned=(x,y), actual=(x,y), radar_ok=True, idx=i)
        viz.end_segment()  # saves PNG
    """

    # Padding around the bounding box of all points (tiles).
    _PAD = 12
    # Pixels per tile in the output image.
    _SCALE = 8
    _HEADER_HEIGHT = 140
    # Maximum pixels per dimension to prevent runaway allocations.
    _MAX_DIM = 4000

    def __init__(
        self,
        map_loader: Any,
        output_dir: Optional[Path] = None,
        mask_output_dir: Optional[Path] = None,
        floor: int = 7,
    ) -> None:
        self._loader = map_loader
        self._floor = floor
        self._out = output_dir or Path("output/path_trace")
        self._out.mkdir(parents=True, exist_ok=True)
        if mask_output_dir is not None:
            self._mask_out = mask_output_dir
        elif self._out.name == "path_trace":
            self._mask_out = self._out.parent / "masks"
        else:
            self._mask_out = self._out / "masks"
        self._mask_out.mkdir(parents=True, exist_ok=True)

        self._segment_id = 0
        self._dest: Optional[Tuple[int, int, int]] = None
        self._start: Optional[Tuple[int, int]] = None
        self._steps: List[StepRecord] = []
        self._planned_path: List[Tuple[int, int]] = []
        self._blocked_tile: Optional[Tuple[int, int]] = None
        self._first_divergence: Optional[Tuple[int, int]] = None
        self._segment_metrics = SegmentMetrics()
        self._cumulative_img: Optional[np.ndarray] = None
        self._cumulative_mask: Optional[np.ndarray] = None
        self._cum_bounds: Optional[Tuple[int, int, int, int]] = None
        self._saved_segment_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def begin_segment(
        self,
        segment_id: int,
        dest: Optional[Tuple[int, int, int]],
        start: Optional[Tuple[int, int]] = None,
    ) -> None:
        self._segment_id = segment_id
        self._dest = dest
        self._start = start
        self._steps = []
        self._planned_path = []
        self._blocked_tile = None
        self._first_divergence = None
        self._segment_metrics = SegmentMetrics()

    def set_planned_path(self, steps: list[Any]) -> None:
        """Record the full A* planned path (list of Coordinate-like objects)."""
        self._planned_path = [(s.x, s.y) for s in steps]

    def set_segment_metrics(self, *, planned_steps: int, direct_distance: int) -> None:
        safe_direct = max(1, direct_distance)
        self._segment_metrics = SegmentMetrics(
            planned_steps=max(0, planned_steps),
            direct_distance=max(0, direct_distance),
            stretch_ratio=max(1.0, planned_steps / safe_direct) if planned_steps > 0 else 1.0,
        )

    def mark_blocked_tile(self, tile: tuple[int, int]) -> None:
        self._blocked_tile = tile

    def record_step(
        self,
        planned: Tuple[int, int],
        actual: Tuple[int, int],
        radar_ok: bool,
        idx: int,
    ) -> None:
        self._steps.append(StepRecord(
            planned_x=planned[0], planned_y=planned[1],
            actual_x=actual[0], actual_y=actual[1],
            radar_confirmed=radar_ok,
            step_idx=idx,
        ))
        if self._first_divergence is None and planned != actual:
            self._first_divergence = actual

    def end_segment(self) -> Optional[Path]:
        """Render and save the segment trace image. Returns the file path."""
        if not self._steps and not self._planned_path:
            return None
        try:
            img, mask_img = self._render()
            fname = f"seg_{self._segment_id:04d}.png"
            fpath = self._out / fname
            mask_path = self._mask_out / fname
            cv2.imwrite(str(fpath), img)
            cv2.imwrite(str(mask_path), mask_img)
            self._saved_segment_count += 1
            _log.info("PathVisualizer: saved %s", fpath)
            _log.info("PathVisualizer: saved mask %s", mask_path)
            return fpath
        except Exception as exc:
            _log.warning("PathVisualizer render failed: %s", exc)
            return None

    def save_cumulative(self) -> Optional[Path]:
        """Save the cumulative trace across all segments."""
        if self._cumulative_img is None:
            return None
        try:
            img, mask_img = self._compose_canvas(
                self._cumulative_img,
                self._cumulative_mask if self._cumulative_mask is not None else np.zeros_like(self._cumulative_img),
                title="cumulative trace",
                lines=self._build_cumulative_lines(),
            )
            fpath = self._out / "cumulative.png"
            mask_path = self._mask_out / "cumulative.png"
            cv2.imwrite(str(fpath), img)
            if self._cumulative_mask is not None:
                cv2.imwrite(str(mask_path), mask_img)
            _log.info("PathVisualizer: saved cumulative %s", fpath)
            if self._cumulative_mask is not None:
                _log.info("PathVisualizer: saved cumulative mask %s", mask_path)
            return fpath
        except Exception as exc:
            _log.warning("PathVisualizer cumulative save failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _get_bounds(self) -> Tuple[int, int, int, int]:
        """Return (xmin, ymin, xmax, ymax) covering all points."""
        xs: list[int] = []
        ys: list[int] = []
        for s in self._steps:
            xs.extend([s.planned_x, s.actual_x])
            ys.extend([s.planned_y, s.actual_y])
        for px, py in self._planned_path:
            xs.append(px)
            ys.append(py)
        if self._blocked_tile:
            xs.append(self._blocked_tile[0])
            ys.append(self._blocked_tile[1])
        if self._first_divergence:
            xs.append(self._first_divergence[0])
            ys.append(self._first_divergence[1])
        if self._dest:
            xs.append(self._dest[0])
            ys.append(self._dest[1])
        if self._start:
            xs.append(self._start[0])
            ys.append(self._start[1])
        if not xs:
            return (0, 0, 1, 1)
        pad = self._PAD
        return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    def _tile_to_px(
        self, tx: int, ty: int, xmin: int, ymin: int,
    ) -> Tuple[int, int]:
        """Convert tile coord to pixel in the output image."""
        s = self._SCALE
        return ((tx - xmin) * s + s // 2, (ty - ymin) * s + s // 2)

    def _render(self) -> tuple[np.ndarray, np.ndarray]:
        xmin, ymin, xmax, ymax = self._get_bounds()
        raw_w = (xmax - xmin + 1) * self._SCALE
        raw_h = (ymax - ymin + 1) * self._SCALE
        # Down-scale when the canvas would exceed _MAX_DIM.
        if raw_w > self._MAX_DIM or raw_h > self._MAX_DIM:
            scale_factor = min(self._MAX_DIM / raw_w, self._MAX_DIM / raw_h)
            effective_scale = max(1, int(self._SCALE * scale_factor))
        else:
            effective_scale = self._SCALE
        w = (xmax - xmin + 1) * effective_scale
        h = (ymax - ymin + 1) * effective_scale
        s = effective_scale

        # Background: extract floor map region and scale up.
        try:
            floor_img = self._loader.get_map_image(self._floor)
            # Crop the relevant region (tile coords → pixel indices).
            px0 = max(0, xmin - _X_MIN)
            py0 = max(0, ymin - _Y_MIN)
            px1 = min(floor_img.shape[1], xmax - _X_MIN + 1)
            py1 = min(floor_img.shape[0], ymax - _Y_MIN + 1)
            crop = floor_img[py0:py1, px0:px1]
            # Convert RGBA to BGR for OpenCV.
            if crop.shape[2] == 4:
                bg = cv2.cvtColor(crop, cv2.COLOR_RGBA2BGR)
            else:
                bg = crop
            # Scale up to match output size.
            bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_NEAREST)
            # Darken for contrast.
            img = (bg * 0.4).astype(np.uint8)
        except Exception:
            # Fallback: black background.
            img = np.zeros((h, w, 3), dtype=np.uint8)

        mask_img = np.zeros((h, w, 3), dtype=np.uint8)

        # 1. Draw planned A* path (green thin line + dots).
        green = (0, 200, 0)
        mask_planned = (96, 96, 96)
        for i, (px, py) in enumerate(self._planned_path):
            cx, cy = self._tile_to_px(px, py, xmin, ymin)
            if i > 0:
                prev = self._planned_path[i - 1]
                pcx, pcy = self._tile_to_px(prev[0], prev[1], xmin, ymin)
                cv2.line(img, (pcx, pcy), (cx, cy), green, 1)
                cv2.line(mask_img, (pcx, pcy), (cx, cy), mask_planned, 1)
            cv2.circle(img, (cx, cy), max(1, s // 3), green, -1)
            cv2.circle(mask_img, (cx, cy), max(1, s // 3), mask_planned, -1)

        # 2. Draw actual walked path (red line + dots).
        red = (0, 0, 255)
        cyan = (255, 255, 0)  # radar-confirmed = cyan
        mask_actual = (255, 255, 255)
        for i, step in enumerate(self._steps):
            cx, cy = self._tile_to_px(step.actual_x, step.actual_y, xmin, ymin)
            color = cyan if step.radar_confirmed else red
            if i > 0:
                prev_step = self._steps[i - 1]
                pcx, pcy = self._tile_to_px(prev_step.actual_x, prev_step.actual_y, xmin, ymin)
                cv2.line(img, (pcx, pcy), (cx, cy), color, 2)
                cv2.line(mask_img, (pcx, pcy), (cx, cy), mask_actual, 2)
            cv2.circle(img, (cx, cy), max(2, s // 2), color, -1)
            cv2.circle(mask_img, (cx, cy), max(2, s // 2), mask_actual, -1)
            # Step number.
            cv2.putText(
                img, str(step.step_idx),
                (cx + s // 2, cy - s // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 255, 255), 1,
            )

        # 3. Draw waypoint targets (yellow cross).
        yellow = (0, 255, 255)
        if self._dest:
            dx, dy = self._tile_to_px(self._dest[0], self._dest[1], xmin, ymin)
            arm = max(3, s)
            cv2.line(img, (dx - arm, dy), (dx + arm, dy), yellow, 2)
            cv2.line(img, (dx, dy - arm), (dx, dy + arm), yellow, 2)
            cv2.line(mask_img, (dx - arm, dy), (dx + arm, dy), (180, 180, 180), 1)
            cv2.line(mask_img, (dx, dy - arm), (dx, dy + arm), (180, 180, 180), 1)
        if self._start:
            sx, sy = self._tile_to_px(self._start[0], self._start[1], xmin, ymin)
            cv2.drawMarker(img, (sx, sy), (255, 255, 255), cv2.MARKER_DIAMOND, s, 2)
            cv2.drawMarker(mask_img, (sx, sy), (200, 200, 200), cv2.MARKER_DIAMOND, s, 1)

        if self._first_divergence is not None:
            fx, fy = self._tile_to_px(self._first_divergence[0], self._first_divergence[1], xmin, ymin)
            marker_size = max(6, s * 2)
            cv2.drawMarker(img, (fx, fy), (255, 0, 255), cv2.MARKER_TILTED_CROSS, marker_size, 2)
            cv2.drawMarker(mask_img, (fx, fy), (160, 160, 160), cv2.MARKER_TILTED_CROSS, marker_size, 1)

        if self._blocked_tile is not None:
            bx, by = self._tile_to_px(self._blocked_tile[0], self._blocked_tile[1], xmin, ymin)
            marker_size = max(7, s * 2)
            cv2.drawMarker(img, (bx, by), (0, 140, 255), cv2.MARKER_CROSS, marker_size, 2)
            cv2.drawMarker(mask_img, (bx, by), (220, 220, 220), cv2.MARKER_CROSS, marker_size, 1)

        # Update cumulative using the body only, then compose larger annotated outputs.
        self._update_cumulative(img, mask_img, xmin, ymin, xmax, ymax)

        return self._compose_canvas(
            img,
            mask_img,
            title=f"segment #{self._segment_id}",
            lines=self._build_segment_lines(),
        )

    def _build_segment_lines(self) -> list[str]:
        n_radar = sum(1 for st in self._steps if st.radar_confirmed)
        n_total = len(self._steps)
        drift_steps = [
            abs(st.actual_x - st.planned_x) + abs(st.actual_y - st.planned_y)
            for st in self._steps
        ]
        max_drift = max(drift_steps) if drift_steps else 0
        avg_drift = sum(drift_steps) / len(drift_steps) if drift_steps else 0.0
        lines = [
            f"steps={n_total}  radar={n_radar}/{n_total}",
            f"drift: max={max_drift} avg={avg_drift:.1f}",
            (
                "plan: "
                f"{self._segment_metrics.planned_steps} vs direct={self._segment_metrics.direct_distance} "
                f"stretch={self._segment_metrics.stretch_ratio:.1f}x"
            ),
        ]
        if self._dest:
            lines.append(f"dest=({self._dest[0]},{self._dest[1]})")
        if self._first_divergence is not None:
            lines.append(f"diverge=({self._first_divergence[0]},{self._first_divergence[1]})")
        if self._blocked_tile is not None:
            lines.append(f"blocked=({self._blocked_tile[0]},{self._blocked_tile[1]})")
        return lines

    def _build_cumulative_lines(self) -> list[str]:
        bounds = self._cum_bounds
        lines = [f"segments={self._saved_segment_count}"]
        if bounds is not None:
            xmin, ymin, xmax, ymax = bounds
            lines.append(f"bounds=({xmin},{ymin})→({xmax},{ymax})")
        if self._dest:
            lines.append(f"last-dest=({self._dest[0]},{self._dest[1]})")
        if self._first_divergence is not None:
            lines.append(f"last-diverge=({self._first_divergence[0]},{self._first_divergence[1]})")
        if self._blocked_tile is not None:
            lines.append(f"last-blocked=({self._blocked_tile[0]},{self._blocked_tile[1]})")
        return lines

    def _compose_canvas(
        self,
        body_img: np.ndarray,
        body_mask: np.ndarray,
        *,
        title: str,
        lines: list[str],
    ) -> tuple[np.ndarray, np.ndarray]:
        header_h = self._HEADER_HEIGHT
        body_h, body_w = body_img.shape[:2]
        img = np.zeros((body_h + header_h, body_w, 3), dtype=np.uint8)
        mask_img = np.zeros((body_h + header_h, body_w, 3), dtype=np.uint8)
        img[header_h:, :] = body_img
        mask_img[header_h:, :] = body_mask

        cv2.rectangle(img, (0, 0), (body_w - 1, header_h - 1), (24, 24, 24), -1)
        cv2.rectangle(mask_img, (0, 0), (body_w - 1, header_h - 1), (0, 0, 0), -1)

        title_y = 28
        text_scale = 0.6
        text_step = 20
        cv2.putText(img, title, (10, title_y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(mask_img, title, (10, title_y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        for li, text in enumerate(lines):
            y = title_y + 22 + li * text_step
            cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, text_scale, (255, 255, 255), 1)
            cv2.putText(mask_img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, text_scale, (255, 255, 255), 1)

        legend_x = max(10, body_w - 240)
        legend = [
            ("planned (A*)", (0, 200, 0), (96, 96, 96)),
            ("actual (DR)", (0, 0, 255), (255, 255, 255)),
            ("actual (radar)", (255, 255, 0), None),
            ("waypoint", (0, 255, 255), (180, 180, 180)),
            ("1st divergence", (255, 0, 255), (160, 160, 160)),
            ("blocked tile", (0, 140, 255), (220, 220, 220)),
        ]
        for idx, (label, color, mask_color) in enumerate(legend):
            y = 24 + idx * 18
            cv2.putText(img, label, (legend_x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            if mask_color is not None:
                cv2.putText(mask_img, label, (legend_x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, mask_color, 1)

        return img, mask_img

    def _update_cumulative(
        self,
        seg_img: np.ndarray,
        seg_mask: np.ndarray,
        xmin: int, ymin: int, xmax: int, ymax: int,
    ) -> None:
        """Merge segment image into the cumulative trace."""
        if self._cumulative_img is None:
            self._cumulative_img = seg_img.copy()
            self._cumulative_mask = seg_mask.copy()
            self._cum_bounds = (xmin, ymin, xmax, ymax)
            return

        # Expand cumulative if needed.
        cxmin, cymin, cxmax, cymax = self._cum_bounds  # type: ignore[misc]
        new_xmin = min(cxmin, xmin)
        new_ymin = min(cymin, ymin)
        new_xmax = max(cxmax, xmax)
        new_ymax = max(cymax, ymax)
        new_w = (new_xmax - new_xmin + 1) * self._SCALE
        new_h = (new_ymax - new_ymin + 1) * self._SCALE

        # Cap cumulative canvas to prevent runaway memory growth.
        if new_w > self._MAX_DIM or new_h > self._MAX_DIM:
            _log.warning(
                "Cumulative canvas %dx%d exceeds cap — skipping expansion",
                new_w, new_h,
            )
            return

        # Create expanded canvas.
        canvas = np.zeros((new_h, new_w, 3), dtype=np.uint8)

        # Paste old cumulative.
        old = self._cumulative_img
        ox = (cxmin - new_xmin) * self._SCALE
        oy = (cymin - new_ymin) * self._SCALE
        canvas[oy:oy + old.shape[0], ox:ox + old.shape[1]] = old

        mask_canvas = np.zeros((new_h, new_w, 3), dtype=np.uint8)
        old_mask = self._cumulative_mask
        if old_mask is not None:
            mask_canvas[oy:oy + old_mask.shape[0], ox:ox + old_mask.shape[1]] = old_mask

        # Overlay new segment (max blend so paths accumulate).
        sx = (xmin - new_xmin) * self._SCALE
        sy = (ymin - new_ymin) * self._SCALE
        roi = canvas[sy:sy + seg_img.shape[0], sx:sx + seg_img.shape[1]]
        np.maximum(roi, seg_img, out=roi)
        mask_roi = mask_canvas[sy:sy + seg_mask.shape[0], sx:sx + seg_mask.shape[1]]
        np.maximum(mask_roi, seg_mask, out=mask_roi)

        self._cumulative_img = canvas
        self._cumulative_mask = mask_canvas
        self._cum_bounds = (new_xmin, new_ymin, new_xmax, new_ymax)
