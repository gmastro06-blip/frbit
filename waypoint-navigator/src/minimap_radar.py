"""
MinimapRadar
------------
Detecta la posición del jugador haciendo template matching del minimap
capturado vía OBS contra el mapa de referencia de tibiamaps.io.

Sin leer memoria → compatible con BattlEye.

Método:
  1. Captura el frame de OBS (resolución completa)
  2. Recorta el área del minimapa (ROI configurable)
  3. Convierte a escala de grises y enmascara el punto del personaje
  4. Redimensiona al ratio 1 px/tile del mapa de referencia
  5. Hace cv2.matchTemplate contra el mapa del piso (floor-XX-map.png)
  6. La posición del match → coordenadas XYZ del jugador

Calibración rápida:
  python src/minimap_diag.py --source obs-ws
  (guarda minimap_config.json con los valores correctos)
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import cv2

_log = logging.getLogger("wn.radar")

from .models import Coordinate, BOUNDS
from .map_loader import TibiaMapLoader
from .minimap_radar_utils import (
    find_char_center as runtime_find_char_center,
    is_border_line as runtime_is_border_line,
    match_with_hint as runtime_match_with_hint,
    quantize_and_check as runtime_quantize_and_check,
    strip_ui_border as runtime_strip_ui_border,
)

# ---------------------------------------------------------------------------
from src.config_paths import MINIMAP_CONFIG

MINIMAP_CONFIG_FILE = MINIMAP_CONFIG

# Dimensiones de referencia del frame fuente (para escalar ROI)
_REF_W = 1920
_REF_H = 1080

# Paleta de grises para el personaje blanco/amarillo: siempre se enmascara
_CHAR_DOT_RADIUS_FRAC = 0.08   # radio relativo al ancho del minimap capturado


_CHAR_SEARCH_MARGIN = 0.20    # fraction of minimap edges to ignore when finding character
_CHAR_BRIGHTNESS_MIN = 220    # min brightness for character marker peak detection
_CHAR_MIN_PIXELS = 4          # min bright pixels before falling back to loose threshold
_BORDER_DARK_MAX = 85         # max channel brightness for a dark-border pixel
_BORDER_COVERAGE_MIN = 0.60   # fraction of line pixels that must match border pattern
_BORDER_NEUTRAL_COVERAGE = 0.70  # fraction for light neutral-grey border detection
_TRACKING_MAX_MANHATTAN_JUMP = 10  # tiles; was 3 — too tight for running characters
_HINT_SEARCH_PADDING = 60


# ---------------------------------------------------------------------------
def _find_char_center(crop_bgr: np.ndarray) -> Tuple[float, float]:
    """Detect the character marker (bright cross) in a minimap crop.

    Returns ``(cx_frac, cy_frac)`` — character position as fractions
    of the crop width / height.  Falls back to ``(0.5, 0.5)`` when
    detection fails.

    Uses the **peak-brightness** pixels (value 255 — the white cross)
    rather than a loose >220 threshold, to avoid bias from nearby
    NPC markers and other bright spots.
    """
    return runtime_find_char_center(
        crop_bgr,
        cv2_module=cv2,
        char_search_margin=_CHAR_SEARCH_MARGIN,
        char_brightness_min=_CHAR_BRIGHTNESS_MIN,
        char_min_pixels=_CHAR_MIN_PIXELS,
    )


# ---------------------------------------------------------------------------
def _is_border_line(pixels: np.ndarray, threshold: int = 85) -> bool:
    """Return True if a row/column of BGR pixels is UI border, not map data.

    Detects two kinds of minimap border:
    1. **Dark border** — uniform grey ~70-78, all channels below *threshold*.
    2. **Light top border** — uniform neutral grey ~110-120 with near-zero
       saturation (R≈G≈B within 10) that doesn't match any Tibia palette
       colour.

    A line is flagged when >60 % of its pixels match either pattern and
    the standard deviation is low (homogeneous).
    """
    return runtime_is_border_line(
        pixels,
        threshold=threshold,
        border_coverage_min=_BORDER_COVERAGE_MIN,
        border_neutral_coverage=_BORDER_NEUTRAL_COVERAGE,
    )


def _strip_ui_border(crop: np.ndarray, max_strip: int = 8) -> np.ndarray:
    """Remove dark UI-border rows/cols from all four edges of *crop*.

    At most *max_strip* pixels are removed from each edge to avoid
    eating into actual map data.
    """
    return runtime_strip_ui_border(crop, max_strip=max_strip, is_border_line_fn=_is_border_line)


# Tibia minimap — definitive palette (BGR).  Extracted from all 16 floor PNGs
# of tibia-map-data.  A crop containing the minimap should have ≥20 % of its
# pixels close to one of these colours.
_MINIMAP_PALETTE = np.array([
    [153, 153, 153],  # grey stone floor         (walkable)
    [51,  102, 153],  # dirt / sand / brown       (walkable)
    [0,   204,   0],  # grass / vegetation        (walkable)
    [153, 204, 255],  # sand / beach / light tan  (walkable)
    [255, 255, 255],  # snow / ice                (walkable)
    [102, 102, 102],  # dark grey — cave wall     (non-walkable)
    [153, 102,  51],  # water (blue RGB)          (non-walkable)
    [0,    51, 153],  # dark red-brown / lava     (non-walkable)
    [0,    51, 255],  # building / wall (red RGB) (non-walkable)
    [0,   102,   0],  # mountain / dark green     (non-walkable)
    [0,   102, 255],  # orange obstacle           (non-walkable)
    [102, 255, 153],  # light green (farmland)    (non-walkable)
    [0,     0,   0],  # unexplored (black)        (non-walkable)
    [255, 255, 204],  # light cyan / ice / shoal  (ambiguous)
    [0,   255, 255],  # yellow / stairs / special (ambiguous)
], dtype=np.int16)


def _quantize_and_check(
    crop_bgr: np.ndarray, min_fraction: float = 0.20
) -> "tuple[np.ndarray, bool]":
    """Quantize BGR pixels to palette indices and validate palette presence in one pass.

    Replaces the separate ``_has_minimap_palette`` + ``_quantize_to_palette`` calls
    that each computed the full (N_px, 15, 3) broadcast independently.

    Returns
    -------
    indices : np.ndarray, shape (H, W), dtype uint8
        Each pixel mapped to its nearest Tibia palette index (0–14).
    is_valid : bool
        True when >= *min_fraction* of pixels are within Chebyshev distance 25
        of any palette colour (same criterion as the old ``_has_minimap_palette``).
    """
    return runtime_quantize_and_check(crop_bgr, palette=_MINIMAP_PALETTE, min_fraction=min_fraction)


def _has_minimap_palette(crop_bgr: np.ndarray, min_fraction: float = 0.20) -> bool:
    """Return True if *crop_bgr* contains enough Tibia-palette colours.

    Kept for backwards compatibility with external callers.
    Internally delegates to ``_quantize_and_check``.
    """
    _, is_valid = _quantize_and_check(crop_bgr, min_fraction)
    return is_valid


def _quantize_to_palette(bgr: np.ndarray) -> np.ndarray:
    """Map each BGR pixel to its nearest Tibia palette index (0–14).

    Kept for backwards compatibility with external callers.
    Internally delegates to ``_quantize_and_check``.
    """
    indices, _ = _quantize_and_check(bgr)
    return indices


# Lookup table: palette index → well-separated grayscale value.
# 15 colours × 17 spacing = 0, 17, 34 … 238 — maximises contrast
# for cv2.matchTemplate discrimination.
_PALETTE_GRAY_LUT = np.arange(15, dtype=np.uint8) * np.uint8(17)

# Neutral mask value = mean of palette grayscale (119) to minimise
# bias in TM_CCOEFF_NORMED (which subtracts the mean).
_QUANT_MASK_VALUE = int(_PALETTE_GRAY_LUT.mean())  # 119

# Palette indices that are reliably consistent between in-game minimap
# and tibia-map-data reference floor maps.  Excludes overlays that the
# live client renders differently:
#   idx 4 = white (snow / player-NPC markers)
#   idx 5 = dark grey (building roofs seen from surface)
#   idx 12 = black (wall overlay / unexplored boundary)
_STABLE_PALETTE_INDICES = frozenset({0, 1, 2, 3, 6, 7, 8, 9, 10, 11, 13, 14})


# ---------------------------------------------------------------------------
@dataclass
class MinimapConfig:
    """
    Configuración persistent del lector de minimap.

    roi : [x, y, w, h]
        Posición del widget del minimap en el frame OBS a 1920×1080.
        Se escala automáticamente si el frame tiene otra resolución.
        Valor por defecto: panel derecho de Tibia en resolución 1920×1080.

    tiles_wide : int
        Número de tiles visibles en el ancho del minimap al zoom normal
        de la cámara del cliente (≈ 90 a zoom ×1, ≈ 50 a zoom ×2).

    floor : int
        Piso actual — se actualiza dinámicamente desde el auto-walker.

    confidence : float
        Umbral mínimo de TM_CCOEFF_NORMED para aceptar un match
        (0.0-1.0; suele funcionar bien con 0.35-0.50).

    mask_center : bool
        Enmascarar el punto blanco/amarillo del personaje antes de matching.
    """
    roi:         List[int] = field(default_factory=lambda: [1710, 37, 175, 175])
    
    tiles_wide:  int   = 90
    floor:       int   = 7
    confidence:  float = 0.55
    mask_center: bool  = True
    # Fase 6.1: multi-scale factors for template matching (empty = single scale)
    scale_factors: List[float] = field(default_factory=list)
    # Fase 6.3: separate confidence for TibiaLocalMinimapReader
    local_confidence: float = 0.55
    # ── Anti-drift / anti-false-positive ───────────────────────────────
    # Maximum plausible position change (tiles) between consecutive reads.
    # A walking character moves at most ~8 tiles/second.  With a read
    # interval of ~200 ms that’s ~2 tiles max.  We use a higher value
    # (15) as safety margin for lag / missed frames.  0 = disabled.
    max_jump_tiles: int = 15
    # Number of consecutive frames that must agree on the same approximate
    # position before accepting the coordinate.  1 = accept immediately.
    temporal_smoothing: int = 1

    def save(self, path: Path = MINIMAP_CONFIG_FILE) -> None:
        import os as _os
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2)
        _os.replace(tmp, path)

    def validate(self) -> None:
        """Raise ValueError if ROI is invalid."""
        if len(self.roi) != 4:
            raise ValueError(f"MinimapConfig.roi must have 4 elements, got {len(self.roi)}")
        if any(v < 0 for v in self.roi):
            raise ValueError(f"MinimapConfig.roi values must be non-negative: {self.roi}")

    @classmethod
    def load(cls, path: Path = MINIMAP_CONFIG_FILE) -> "MinimapConfig":
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        obj = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        obj.validate()
        return obj


# ---------------------------------------------------------------------------
class MinimapRadar:
    """
    Lector de posición basado en template-matching del minimap.

    Uso básico
    ----------
    >>> radar = MinimapRadar(loader)
    >>> radar.floor = 7
    >>> coord = radar.read(frame_bgr)   # frame del OBS

    Parámetros
    ----------
    loader : TibiaMapLoader
        Instancia con los mapas descargados de tibiamaps.io.
    config : MinimapConfig, optional
        Configuración; si es None se carga de minimap_config.json.
    """

    def __init__(
        self,
        loader: TibiaMapLoader,
        config: Optional[MinimapConfig] = None,
    ) -> None:
        self._loader = loader
        self._cfg    = config or MinimapConfig.load()
        self._lock = threading.Lock()
        # Cache de floor maps en escala de grises (costoso de generar)
        self._floor_gray: Dict[int, np.ndarray] = {}
        # Cache de floor maps como palette indices (uint8, 0-14)
        self._floor_quant: Dict[int, np.ndarray] = {}
        self._last_coord: Optional[Coordinate] = None
        self._hit_count  = 0
        self._miss_count = 0
        # ── Anti-drift: position jump rejection ────────────────────────
        self._jump_rejects: int = 0
        # Consecutive jump rejects — reset _last_coord after too many to
        # recover from same-floor teleports (death, ladder, rope)
        self._consecutive_jump_rejects: int = 0
        # Temporal smoothing: buffer of recent candidate positions
        self._pos_buffer: List[Coordinate] = []
        # ── Circuit breaker: consecutive match failures ─────────────────
        # Counts consecutive reads that returned None (timeout or miss).
        # After _CB_THRESHOLD failures we return last_coord to avoid
        # blocking callers and log a warning.
        self._consecutive_failures: int = 0
        self._CB_THRESHOLD: int = 5

    # ── Propiedad de acceso rápido ──────────────────────────────────────────
    @property
    def floor(self) -> int:
        return self._cfg.floor

    @floor.setter
    def floor(self, value: int) -> None:
        self._cfg.floor = value

    @property
    def confidence(self) -> float:
        return self._cfg.confidence

    def _reject_tracking_jump(self) -> None:
        with self._lock:
            self._jump_rejects += 1
            self._miss_count += 1
            self._consecutive_jump_rejects += 1
            # After 5 consecutive rejects on the same floor, the character
            # likely teleported (death respawn, ladder, rope) or tracking has
            # drifted badly — reset the anchor so the next valid match can lock on.
            if self._consecutive_jump_rejects >= 5:
                self._last_coord = None
                self._hit_count = 0
                self._consecutive_jump_rejects = 0

    # ── API pública ──────────────────────────────────────────────────────────
    def read(
        self,
        frame: np.ndarray,
        floor: Optional[int] = None,
        hint: Optional[Coordinate] = None,
    ) -> Optional[Coordinate]:
        """
        Lee la posición del jugador a partir de un frame BGR de OBS.

        Uses palette-quantized match counting: for each stable palette
        colour, builds binary masks and counts pixel-wise coincidences
        via ``cv2.matchTemplate(TM_CCORR)``.  This is robust against
        building-roof overlays and anti-aliased boundary pixels that
        break conventional grayscale template matching.

        Parámetros
        ----------
        frame : np.ndarray (H, W, 3) BGR
            Frame completo capturado desde OBS.
        floor : int, optional
            Piso actual (override de self.floor).
        hint : Coordinate, optional
            Coordenada anterior — reduce el área de búsqueda y acelera el match.

        Devuelve
        --------
        Coordinate o None si el match no supera el umbral de confianza.
        """
        fl = floor if floor is not None else self._cfg.floor

        # 1. Recortar ROI del minimapa
        mini_bgr = self._crop_minimap(frame)
        if mini_bgr is None:
            return None

        mh, mw = mini_bgr.shape[:2]

        # 1b. Quantize and validate in one pass (avoids double broadcast computation).
        if mini_bgr.ndim != 3:
            # Grayscale fallback (shouldn't happen in normal operation)
            with self._lock:
                self._miss_count += 1
            return None
        q_crop, palette_ok = _quantize_and_check(mini_bgr)
        if not palette_ok:
            with self._lock:
                self._miss_count += 1
            _log.info("read: palette check FAILED (crop %dx%d)", mw, mh)
            return None

        # 3. Downscale to 1 px/tile
        tiles_w = self._cfg.tiles_wide
        tiles_h = max(1, int(mh * tiles_w / mw))
        q_tpl = cv2.resize(q_crop, (tiles_w, tiles_h),
                           interpolation=cv2.INTER_NEAREST)

        # 3b. Detect character marker position (bright cross in crop)
        cx_frac, cy_frac = _find_char_center(mini_bgr)
        char_tx = round(tiles_w * cx_frac)
        char_ty = round(tiles_h * cy_frac)

        # 4. Centre-dot exclusion mask (boolean)
        mask = np.ones((tiles_h, tiles_w), dtype=bool)
        if self._cfg.mask_center:
            cy, cx = char_ty, char_tx
            r = max(3, int(tiles_w * _CHAR_DOT_RADIUS_FRAC))
            Y, X = np.ogrid[:tiles_h, :tiles_w]
            mask = ((X - cx) ** 2 + (Y - cy) ** 2) > r ** 2

        # 5. Get floor palette indices (cached)
        q_floor = self._get_floor_quant(fl)
        fh_total, fw_total = q_floor.shape[:2]

        if tiles_h >= fh_total or tiles_w >= fw_total:
            with self._lock:
                self._miss_count += 1
            return None

        # 6. Palette match counting (constrained or full, with fallback)
        with self._lock:
            last_coord = self._last_coord
            hit_count = self._hit_count
            consecutive_failures = self._consecutive_failures

        # ── Circuit breaker ────────────────────────────────────────────────
        # After _CB_THRESHOLD consecutive failures, return last known position
        # to avoid blocking callers and give the radar time to recover.
        if consecutive_failures >= self._CB_THRESHOLD:
            _log.warning(
                "minimap: circuit breaker activado (%d fallos consecutivos) — "
                "retornando última posición conocida",
                consecutive_failures,
            )
            return last_coord

        effective_hint = hint if hint is not None else last_coord

        # ── Timeout wrapper for the expensive palette_match call ───────────
        # cv2.matchTemplate on a full floor map can take >2 s on slow hardware.
        # We cap at 2.0 s; on timeout we treat this tick as a miss and
        # increment the failure counter.
        def _do_match() -> Tuple[float, Tuple[int, int], int, int]:
            if effective_hint is not None and effective_hint.z == fl:
                bv, bl, ox, oy = self._palette_match(
                    q_floor, q_tpl, mask, effective_hint,
                    padding=_HINT_SEARCH_PADDING,
                )
                if bv < self._cfg.confidence:
                    _log.info(
                        "read: constrained score %.4f < %.2f — fallback to full-floor",
                        bv, self._cfg.confidence,
                    )
                    bv, bl, ox, oy = self._palette_match(q_floor, q_tpl, mask)
                return bv, bl, ox, oy
            return self._palette_match(q_floor, q_tpl, mask)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(_do_match)
                best_val, best_loc, off_px, off_py = _fut.result(timeout=2.0)
        except concurrent.futures.TimeoutError:
            _log.warning("minimap: palette_match excedió timeout de 2.0 s — tick descartado")
            with self._lock:
                self._miss_count += 1
                self._consecutive_failures += 1
            return None

        if best_val < self._cfg.confidence:
            with self._lock:
                self._miss_count += 1
                self._consecutive_failures += 1
            _log.info(
                "read: match score %.4f < confidence %.2f — rejected",
                best_val, self._cfg.confidence,
            )
            return None

        _log.info("read: HIT score=%.4f loc=%s", best_val, best_loc)

        # 7. Convertir posición del match a coordenadas de tile
        tile_x = BOUNDS["xMin"] + off_px + best_loc[0] + char_tx
        tile_y = BOUNDS["yMin"] + off_py + best_loc[1] + char_ty
        coord  = Coordinate(tile_x, tile_y, fl)

        # 8. Position jump rejection
        # Only apply after 3+ confirmed reads to let matching converge
        # from the initial --start-pos hint.
        # NOTE: _hit_count is incremented AFTER this check so the gate
        # fires at exactly 3 confirmed previous hits (not 2+this one).
        max_jump = self._cfg.max_jump_tiles
        if (
            max_jump > 0
            and hit_count >= 3
            and hint is not None
            and hint.z == fl
        ):
            hint_dx = abs(coord.x - hint.x)
            hint_dy = abs(coord.y - hint.y)
            hint_jump_limit = min(max_jump, _TRACKING_MAX_MANHATTAN_JUMP)
            if (
                hint_dx > max_jump
                or hint_dy > max_jump
                or (hint_dx + hint_dy) > hint_jump_limit
            ):
                _log.warning(
                    "read: tracking jump rejected by hint coord=%s hint=%s d=(%d,%d) manhattan=%d limit=%d",
                    coord,
                    hint,
                    hint_dx,
                    hint_dy,
                    hint_dx + hint_dy,
                    hint_jump_limit,
                )
                self._reject_tracking_jump()
                with self._lock:
                    self._consecutive_failures += 1
                return None

        if (max_jump > 0
            and hit_count >= 3
            and last_coord is not None
            and last_coord.z == fl):
            dx = abs(coord.x - last_coord.x)
            dy = abs(coord.y - last_coord.y)
            manhattan_jump = dx + dy
            jump_limit = min(max_jump, _TRACKING_MAX_MANHATTAN_JUMP)
            if dx > max_jump or dy > max_jump or manhattan_jump > jump_limit:
                _log.warning(
                    "read: tracking jump rejected by last coord=%s last=%s d=(%d,%d) manhattan=%d limit=%d",
                    coord,
                    last_coord,
                    dx,
                    dy,
                    manhattan_jump,
                    jump_limit,
                )
                self._reject_tracking_jump()
                with self._lock:
                    self._consecutive_failures += 1
                return None

        # Successful read — reset failure counter and update state
        with self._lock:
            self._consecutive_jump_rejects = 0
            self._consecutive_failures = 0
            self._hit_count += 1
            self._last_coord = coord
        return coord

    def stats(self) -> str:
        with self._lock:
            total = self._hit_count + self._miss_count
            hit_count = self._hit_count
            miss_count = self._miss_count
        pct = int(hit_count * 100 / total) if total else 0
        return f"[RADAR] hits={hit_count} miss={miss_count} ({pct}%)"

    def reset_stats(self) -> None:
        """Zero the hit, miss, jump-reject, and circuit-breaker counters."""
        with self._lock:
            self._hit_count  = 0
            self._miss_count = 0
            self._jump_rejects = 0
            self._consecutive_jump_rejects = 0
            self._consecutive_failures = 0
            self._pos_buffer.clear()

    @property
    def total_reads(self) -> int:
        """Total number of ``read()`` calls (hits + misses)."""
        with self._lock:
            return self._hit_count + self._miss_count

    @property
    def is_tracking(self) -> bool:
        """
        True when at least one successful coordinate match has been made
        (i.e. ``last_coord`` is not ``None``).
        """
        with self._lock:
            return self._last_coord is not None

    def clear_floor_cache(self) -> None:
        """
        Discard all cached floor-map images (grayscale and quantized).

        The next ``read()`` for any floor will reload from disk.  Useful when
        a new floor PNG has been downloaded or when freeing memory.
        """
        with self._lock:
            self._floor_gray.clear()
            self._floor_quant.clear()

    @property
    def last_coord(self) -> Optional[Coordinate]:
        """The most recent successfully matched coordinate, or None."""
        with self._lock:
            return self._last_coord

    @property
    def hit_rate(self) -> float:
        """
        Fraction of ``read()`` calls that returned a valid coordinate
        (0.0 when no calls have been made).
        """
        with self._lock:
            total = self._hit_count + self._miss_count
            hit_count = self._hit_count
        return hit_count / total if total else 0.0

    @property
    def miss_rate(self) -> float:
        """Fraction of ``read()`` calls that returned ``None`` (complement of :attr:`hit_rate`)."""
        return 1.0 - self.hit_rate

    @property
    def cached_floor_count(self) -> int:
        """Number of floor grayscale images currently held in the cache."""
        with self._lock:
            return len(self._floor_gray)

    @property
    def floors_cached(self) -> List[int]:
        """Sorted list of floor indices whose grayscale map is currently cached."""
        with self._lock:
            return sorted(self._floor_gray.keys())

    @property
    def has_last_coord(self) -> bool:
        """True when a coordinate has been successfully matched at least once."""
        with self._lock:
            return self._last_coord is not None

    @property
    def has_missed(self) -> bool:
        """True when at least one ``read()`` call has failed to find a match."""
        with self._lock:
            return self._miss_count > 0

    @property
    def jump_rejects(self) -> int:
        """Number of positions rejected due to implausible distance jump."""
        with self._lock:
            return self._jump_rejects

    @property
    def has_reads(self) -> bool:
        """True when at least one :meth:`read` call has been attempted."""
        return self.total_reads > 0

    @property
    def hit_count(self) -> int:
        """Total number of successful coordinate matches."""
        with self._lock:
            return self._hit_count

    @property
    def miss_count(self) -> int:
        """Total number of failed coordinate matches."""
        with self._lock:
            return self._miss_count

    def update_config(self, config: MinimapConfig) -> None:
        """
        Hot-swap the radar configuration without re-instantiating.
        Clears the cached floor-gray images so they will be rebuilt with
        the new settings on the next ``read()`` call.
        """
        self._cfg = config
        with self._lock:
            self._floor_gray.clear()
            self._floor_quant.clear()

    def stats_snapshot(self) -> dict[str, Any]:
        """
        Return a lightweight dict of radar statistics suitable for
        logging, UI display, or session saving.

        Keys: ``hits``, ``misses``, ``total``, ``hit_rate``, ``floor``,
        ``last_coord``, ``is_tracking``, ``jump_rejects``.
        """
        with self._lock:
            hits = self._hit_count
            misses = self._miss_count
            last_coord = self._last_coord
            jump_rejects = self._jump_rejects
            consecutive_failures = self._consecutive_failures
        total = hits + misses
        hit_rate = hits / total if total else 0.0
        return {
            "hits":                 hits,
            "misses":               misses,
            "total":                total,
            "hit_rate":             hit_rate,
            "floor":                self._cfg.floor,
            "last_coord":           last_coord,
            "is_tracking":          last_coord is not None,
            "jump_rejects":         jump_rejects,
            "consecutive_failures": consecutive_failures,
        }

    # ── Auxiliares ───────────────────────────────────────────────────────────
    def _crop_minimap(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Recorta el área del minimap del frame OBS, escalando el ROI.

        Automatically strips dark UI-border rows/columns (brightness < 85
        and not a known Tibia palette colour) from the edges.
        """
        fh, fw = frame.shape[:2]
        rx, ry, rw, rh = self._cfg.roi

        # Escalar ROI de referencia (1920×1080) a las dimensiones reales del frame
        sx = fw / _REF_W
        sy = fh / _REF_H
        x0 = max(0, int(rx * sx))
        y0 = max(0, int(ry * sy))
        x1 = min(fw, int((rx + rw) * sx))
        y1 = min(fh, int((ry + rh) * sy))

        if x1 - x0 < 20 or y1 - y0 < 20:
            return None

        crop = frame[y0:y1, x0:x1]

        # Strip dark UI-border rows/cols that don't contain Tibia map colours.
        # Tibia map pixels are saturated or exact grey (153,153,153, etc).
        # The UI border is dark grey ~(72-77) which doesn't match any map colour.
        crop = _strip_ui_border(crop)
        if crop.shape[0] < 20 or crop.shape[1] < 20:
            return None

        return crop

    def _get_floor_gray(self, floor: int) -> np.ndarray:
        """Return cached floor map in palette-quantized grayscale.

        Each pixel is mapped to its nearest Tibia palette index and then
        converted to a well-separated grayscale value (index × 17).
        This preserves colour-categorical information that raw BGR→gray
        conversion destroys (e.g. red-wall ≈ grass ≈ cave in gray).
        """
        with self._lock:
            cached = self._floor_gray.get(floor)
        if cached is not None:
            return cached
        rgba = self._loader.get_map_image(floor)   # H×W×4 RGBA
        bgr  = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
        quant = _quantize_to_palette(bgr)
        floor_gray = _PALETTE_GRAY_LUT[quant]
        with self._lock:
            self._floor_gray[floor] = floor_gray
            return self._floor_gray[floor]

    def _get_floor_quant(self, floor: int) -> np.ndarray:
        """Return cached floor map as palette indices (uint8, values 0–14)."""
        with self._lock:
            cached = self._floor_quant.get(floor)
        if cached is not None:
            return cached
        rgba = self._loader.get_map_image(floor)
        bgr  = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
        quant = _quantize_to_palette(bgr)
        with self._lock:
            self._floor_quant[floor] = quant
            return self._floor_quant[floor]

    def _palette_match(
        self,
        q_floor: np.ndarray,
        q_tpl: np.ndarray,
        mask: np.ndarray,
        hint: Optional[Coordinate] = None,
        padding: int = 60,
    ) -> Tuple[float, Tuple[int, int], int, int]:
        """Palette-based match counting.

        For each stable palette index, builds binary images (template vs
        floor) and uses ``cv2.matchTemplate(TM_CCORR)`` to count
        coincidences.  The ratio of matching pixels to active template
        pixels gives a robust match score in ``[0, 1]``.

        When *hint* is provided the search is restricted to ±padding
        tiles around it; otherwise the full floor map is searched.

        Returns ``(match_ratio, best_loc, offset_x, offset_y)``.
        """
        th, tw = q_tpl.shape[:2]
        fh, fw = q_floor.shape[:2]

        # Constrained search area
        if hint is not None:
            cx = hint.x - BOUNDS["xMin"]
            cy = hint.y - BOUNDS["yMin"]
            x0 = max(0, cx - padding - tw // 2)
            y0 = max(0, cy - padding - th // 2)
            x1 = min(fw, cx + padding + tw)
            y1 = min(fh, cy + padding + th)
            search = q_floor[y0:y1, x0:x1]
            off_x, off_y = x0, y0
            # Fallback to full map if area too small
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

        # Pre-allocate float32 buffers once — avoids 2 × astype() allocations
        # per palette index (was ~275 KB/iter × 12 iters = ~3.3 MB per call).
        t_buf = np.empty((th, tw), dtype=np.float32)
        f_buf = np.empty(search.shape[:2], dtype=np.float32)
        # For small search areas (constrained case) build a set of present
        # palette indices so we can skip empty channels without entering the
        # matchTemplate call.  Skip for large maps to avoid scanning millions
        # of pixels in the full-floor fallback path.
        search_present: Optional[frozenset] = (
            frozenset(np.unique(search).tolist()) if search.size < 200_000 else None
        )

        for idx in _STABLE_PALETTE_INDICES:
            if search_present is not None and idx not in search_present:
                continue
            t_buf[:] = (q_tpl == idx) & mask   # bool → float32, no allocation
            s = float(t_buf.sum())
            if s == 0:
                continue
            n_active += s
            f_buf[:] = search == idx            # bool → float32, no allocation
            match_count += cv2.matchTemplate(f_buf, t_buf, cv2.TM_CCORR)

        if n_active == 0:
            return 0.0, (0, 0), off_x, off_y

        match_count /= n_active
        _, max_val, _, max_loc = cv2.minMaxLoc(match_count)
        return float(max_val), (int(max_loc[0]), int(max_loc[1])), off_x, off_y

    def _match_with_hint(
        self,
        floor_gray: np.ndarray,
        template:   np.ndarray,
        hint:       Coordinate,
        t_w:        int,
        t_h:        int,
        padding:    int = 60,
    ) -> Tuple[np.ndarray, int, int]:
        """
        Busca el template solo en el área ±padding tiles alrededor del hint.
        Mucho más rápido que buscar en el mapa completo.
        Devuelve (result, offset_px, offset_py).
        """
        return runtime_match_with_hint(
            floor_gray,
            template,
            hint_x=hint.x,
            hint_y=hint.y,
            t_w=t_w,
            t_h=t_h,
            padding=padding,
            bounds=BOUNDS,
            cv2_module=cv2,
        )

    # ── Diagnóstico ──────────────────────────────────────────────────────────
    def save_debug_image(
        self,
        frame: np.ndarray,
        output_path: str = "debug_minimap.png",
    ) -> None:
        """Guarda el recorte del minimap para inspección visual."""
        import sys
        if getattr(sys, 'frozen', False):
            return
        crop = self._crop_minimap(frame)
        if crop is not None:
            cv2.imwrite(output_path, crop)
            print(f"  debug saved: {output_path}")
        else:
            print("  ROI out of bounds")


# ---------------------------------------------------------------------------
# Lector de coordenadas usando archivos locales del cliente Tibia
# ---------------------------------------------------------------------------
class TibiaLocalMinimapReader:
    """
    Detecta la posición del jugador sin leer memoria y sin OCR, usando los
    archivos PNG del minimapa que el propio cliente Tibia escribe en disco.

    Enfoque adoptado por la mayoría de herramientas BattlEye-safe
    ---------------------------------------------------------------
    * Lee el archivo ``Minimap_Color_X_Y_Z.png`` **más recientemente
      modificado** → el cliente lo actualiza cuando descubres/visitas un tile.
    * El **nombre del archivo** ya codifica el origen del sector (X, Y, Z).
    * Útil como fuente rápida del **piso actual** (Z) y como **hint de zona**
      para acotar la búsqueda de ``MinimapRadar``.

    Limitación del template-matching contra archivos locales
    ---------------------------------------------------------
    Cuando el personaje está cerca de la frontera entre dos sectores (256 tiles
    cada uno) el minimapa visible en OBS abarca contenido de AMBOS sectores.
    Ningún único archivo local puede contener toda la vista → la correlación es
    baja y el matching falla.  Por eso el método primario de posición exacta
    sigue siendo ``MinimapRadar`` (tibiamaps.io), que trabaja contra el mapa
    completo del piso sin fronteras.

    Uso recomendado
    ---------------
    1. Llamar ``current_floor()`` para obtener el piso actual en <1 ms.
    2. Pasar el resultado como ``floor=`` a ``MinimapRadar.read()``.
    3. Opcionalmente, ``hint_coordinate()`` para acotar la búsqueda espacial.

    Parámetros
    ----------
    config : MinimapConfig, optional
        Configuración de ROI compartida con MinimapRadar.
    minimap_dir : str, optional
        Ruta a la carpeta de minimapas locales. Si es None se usa la ruta
        por defecto de Windows:
        ``%LOCALAPPDATA%\\Tibia\\packages\\Tibia\\minimap``
    confidence : float
        Umbral mínimo de TM_CCOEFF_NORMED para ``read()``.
        Con archivos locales se necesita un umbral más bajo (~0.20) ya que
        la vista puede cruzar fronteras de sector.
    """

    _DEFAULT_DIR = Path.home() / "AppData" / "Local" / "Tibia" / "packages" / "Tibia" / "minimap"

    def __init__(
        self,
        config: Optional[MinimapConfig] = None,
        minimap_dir: Optional[str] = None,
        confidence: float = 0.55,
    ) -> None:
        self._cfg        = config or MinimapConfig.load()
        self._lock = threading.Lock()
        self._dir        = Path(minimap_dir) if minimap_dir else self._DEFAULT_DIR
        # Use config.local_confidence if available, otherwise constructor param
        self._confidence = self._cfg.local_confidence if hasattr(self._cfg, 'local_confidence') else confidence
        self._sector_cache: Dict[str, np.ndarray] = {}   # path → gray_img
        self._last_coord: Optional[Coordinate]    = None
        self._hit_count  = 0
        self._miss_count = 0

    # ── API pública ──────────────────────────────────────────────────────────

    _SECTOR_SIZE = 256  # tiles por sector (1 px = 1 tile en los PNG locales)

    @property
    def is_available(self) -> bool:
        """True si la carpeta de minimapas locales existe."""
        return self._dir.exists()

    # ── API pública ──────────────────────────────────────────────────────────

    def current_floor(self) -> Optional[int]:
        """
        Devuelve el piso (Z) actual leyendo el nombre del archivo PNG más
        recientemente modificado por Tibia.

        Operación <1 ms, no requiere frame ni procesamiento de imagen.
        Es el método más fiable de los tres; úsalo como primer paso.

        Devuelve
        --------
        int (0-15) o None si la carpeta no existe o no hay archivos válidos.
        """
        if not self.is_available:
            return None
        files = sorted(self._dir.glob("Minimap_Color_*.png"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            parts = p.stem.split("_")
            if len(parts) == 5:
                try:
                    return int(parts[4])
                except ValueError:
                    pass
        return None

    def hint_coordinate(self) -> Optional[Coordinate]:
        """
        Devuelve la coordenada *aproximada* del jugador (±128 tiles) leyendo
        el origen del sector más recientemente modificado.

        Sirve como ``hint`` para ``MinimapRadar.read()`` y reduce el área de
        búsqueda de 5 a <0.01 MB2 → acelera significativamente el matching en
        tibiamaps.io.

        Devuelve
        --------
        Coordinate con el centro del sector (sx+128, sy+128, sz) o None.
        """
        if not self.is_available:
            return None
        files = sorted(self._dir.glob("Minimap_Color_*.png"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            parts = p.stem.split("_")
            if len(parts) == 5:
                try:
                    sx, sy, sz = int(parts[2]), int(parts[3]), int(parts[4])
                    return Coordinate(sx + 128, sy + 128, sz)
                except ValueError:
                    pass
        return None

    def read(
        self,
        frame: np.ndarray,
        floor: Optional[int] = None,
    ) -> Optional[Coordinate]:
        """
        Determina la posición exacta del jugador comparando el recorte del
        minimapa OBS contra un **mosaico 3×3 de sectores** locales.

        El mosaico 3×3 (768×768 px) elimina el problema de desbordamiento de
        frontera: la vista del jugador siempre cabe dentro del mosaico central
        aunque el personaje esté en el borde de un sector.

        Parámetros
        ----------
        frame : np.ndarray
            Frame completo capturado desde OBS (BGR, cualquier resolución).
        floor : int, optional
            Piso actual. Si es None se infiere de ``current_floor()``.

        Devuelve
        --------
        Coordinate o None si el match no supera el umbral de confianza.
        """
        if not self.is_available:
            return None

        mini_bgr = self._crop_minimap(frame)
        if mini_bgr is None:
            return None

        if floor is None:
            floor = self.current_floor()
        if floor is None:
            return None

        # Construir índice: (sx, sy) → path para el piso
        sector_index = self._build_sector_index(floor)
        if not sector_index:
            return None

        mh, mw = mini_bgr.shape[:2]
        tiles_w = self._cfg.tiles_wide
        tiles_h = max(1, int(mh * tiles_w / mw))

        # Preparar template (palette-quantized)
        quant_idx = _quantize_to_palette(mini_bgr)
        mini_gray = _PALETTE_GRAY_LUT[quant_idx]
        cy, cx = mh // 2, mw // 2
        r = max(3, int(mw * _CHAR_DOT_RADIUS_FRAC))
        cv2.circle(mini_gray, (cx, cy), r, _QUANT_MASK_VALUE, -1)
        template = cv2.resize(mini_gray, (tiles_w, tiles_h), interpolation=cv2.INTER_NEAREST)
        template = template.astype(np.uint8)

        S = self._SECTOR_SIZE
        best_val: float = -1.0
        best_coord: Optional[Coordinate] = None

        # Para cada sector conocido, construir mosaico 3×3 y buscar
        # C2-fix: restrict to sectors near last known position to avoid O(N)
        all_sectors = sorted(sector_index.keys())
        if self._last_coord is not None:
            S = 256  # sector size in tiles
            lsx = (self._last_coord.x // S) * S
            lsy = (self._last_coord.y // S) * S
            radius = 2  # check up to 2 sectors away
            nearby = [
                (sx, sy) for (sx, sy) in all_sectors
                if abs(sx - lsx) <= radius * S and abs(sy - lsy) <= radius * S
            ]
            # Fallback to all sectors if nearby yields nothing
            if nearby:
                all_sectors = nearby
        sectors_tried: set[tuple[int, int]] = set()
        for (sx, sy) in all_sectors:
            if (sx, sy) in sectors_tried:
                continue
            sectors_tried.add((sx, sy))

            mosaic = self._build_3x3_mosaic(sx, sy, floor, sector_index)
            if mosaic is None:
                continue

            if template.shape[0] >= mosaic.shape[0] or template.shape[1] >= mosaic.shape[1]:
                continue

            result = cv2.matchTemplate(mosaic, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_val:
                best_val = max_val
                # Origin del mosaico en coordenadas de juego
                mosaic_game_x = sx - S  # columna izquierda del mosaico
                mosaic_game_y = sy - S  # fila superior del mosaico
                px = mosaic_game_x + max_loc[0] + tiles_w // 2
                py = mosaic_game_y + max_loc[1] + tiles_h // 2
                best_coord = Coordinate(px, py, floor)

        if best_val < self._confidence:
            with self._lock:
                self._miss_count += 1
            return None

        with self._lock:
            self._hit_count += 1
            self._last_coord = best_coord
        return best_coord

    @property
    def last_coord(self) -> Optional[Coordinate]:
        """Última coordenada detectada con éxito."""
        with self._lock:
            return self._last_coord

    @property
    def hit_rate(self) -> float:
        with self._lock:
            total = self._hit_count + self._miss_count
            hit_count = self._hit_count
        return hit_count / total if total else 0.0

    def stats(self) -> str:
        with self._lock:
            total = self._hit_count + self._miss_count
            hit_count = self._hit_count
            miss_count = self._miss_count
        pct = int(hit_count * 100 / total) if total else 0
        return f"[LocalRadar] hits={hit_count} miss={miss_count} ({pct}%)"

    # ── Auxiliares privados ───────────────────────────────────────────────────

    def _crop_minimap(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Reutiliza la misma lógica de recorte que MinimapRadar."""
        fh, fw = frame.shape[:2]
        rx, ry, rw, rh = self._cfg.roi
        sx_scale = fw / _REF_W
        sy_scale = fh / _REF_H
        x0 = max(0, int(rx * sx_scale))
        y0 = max(0, int(ry * sy_scale))
        x1 = min(fw, int((rx + rw) * sx_scale))
        y1 = min(fh, int((ry + rh) * sy_scale))
        if x1 - x0 < 20 or y1 - y0 < 20:
            return None
        return frame[y0:y1, x0:x1]

    def _build_sector_index(
        self, floor: int
    ) -> Dict[tuple[int, int], Path]:
        """Devuelve un dict {(sx, sy): path} para todos los sectores del piso."""
        index: Dict[tuple[int, int], Path] = {}
        for p in self._dir.glob("Minimap_Color_*.png"):
            parts = p.stem.split("_")
            if len(parts) != 5:
                continue
            try:
                px, py, pz = int(parts[2]), int(parts[3]), int(parts[4])
            except ValueError:
                continue
            if pz == floor:
                index[(px, py)] = p
        return index

    def _build_3x3_mosaic(
        self,
        cx: int,
        cy: int,
        floor: int,
        index: Dict[tuple[int, int], Path],
    ) -> Optional[np.ndarray]:
        """
        Construye un mosaico 3×3 de sectores (768×768 px) centrado en (cx, cy).
        Los sectores ausentes se rellenan con 0 (negro).
        Si el sector central no existe, devuelve None.
        """
        S = self._SECTOR_SIZE
        if (cx, cy) not in index:
            return None

        mosaic = np.zeros((S * 3, S * 3), dtype=np.uint8)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nx, ny = cx + dc * S, cy + dr * S
                path = index.get((nx, ny))
                if path is None:
                    continue
                tile = self._load_sector(path)
                row = dr + 1
                col = dc + 1
                y0, y1 = row * S, row * S + tile.shape[0]
                x0, x1 = col * S, col * S + tile.shape[1]
                mosaic[y0:y1, x0:x1] = tile[:y1 - y0, :x1 - x0]

        return mosaic

    def _find_candidate_files(
        self, floor: Optional[int]
    ) -> List[tuple[Path, int, int, int]]:
        """
        (Compatibilidad) Devuelve todos los archivos para el piso dado.
        Usado internamente; preferir ``_build_sector_index``.
        """
        files = sorted(self._dir.glob("Minimap_Color_*.png"),
                       key=lambda p: p.stat().st_mtime, reverse=True)

        if floor is None:
            for p in files:
                parts = p.stem.split("_")
                if len(parts) == 5:
                    try:
                        floor = int(parts[4])
                        break
                    except ValueError:
                        pass

        result = []
        for p in files:
            parts = p.stem.split("_")
            if len(parts) != 5:
                continue
            try:
                sx, sy, sz = int(parts[2]), int(parts[3]), int(parts[4])
            except ValueError:
                continue
            if floor is not None and sz != floor:
                continue
            result.append((p, sx, sy, sz))
        return result

    def _load_sector(self, path: Path) -> np.ndarray:
        """Load sector PNG into palette-quantized grayscale (cached)."""
        key = str(path)
        with self._lock:
            cached = self._sector_cache.get(key)
        if cached is not None:
            return cached
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            return np.zeros((self._SECTOR_SIZE, self._SECTOR_SIZE), dtype=np.uint8)
        quant = _quantize_to_palette(img)
        sector = _PALETTE_GRAY_LUT[quant]
        with self._lock:
            self._sector_cache[key] = sector
            if len(self._sector_cache) > 20:
                oldest = next(iter(self._sector_cache))
                del self._sector_cache[oldest]
            return self._sector_cache[key]
