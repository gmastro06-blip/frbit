"""
HpMpDetector
------------
Lee el porcentaje de HP y MP de las barras de estado del cliente Tibia
a partir de un fotograma capturado por OBS.

Funciona contando píxeles de color en una ROI configurada:
  - HP bar:  píxeles predominantemente ROJOS  (R > G, R > B, R > 100)
  - MP bar:  píxeles predominantemente AZULES (B > R, B > G, B > 100)

Calibración rápida:
  Configura los ROIs en hpmp_config.json o usa el script de diagnóstico:
  python examples/diag_hpmp.py --source obs-ws
  que muestra el frame con los ROIs superpuestos.

Auto-heal integration en auto_walker:
  --heal-hp-pct 70     → usar hotkey de curación cuando HP < 70%
  --heal-hotkey f1     → hotkey de curación (F1-F12 o 1-9)
  --mana-mp-pct 30     → usar hotkey de maná cuando MP < 30%
  --mana-hotkey f2     → hotkey de maná

Notas de posición (Tibia 1920×1080, con barra de stats visible):
  Los valores por defecto son aproximaciones para el cliente oficial.
  Si tus barras están en otro lugar usa el script de diagnóstico.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, List, Tuple

import numpy as np
import cv2

logger = logging.getLogger("wn.hp")
_OUTLIER_RESET = 5  # accept a new baseline after this many consecutive rejections

# ---------------------------------------------------------------------------
from src.config_paths import HPMP_CONFIG

HPMP_CONFIG_FILE = HPMP_CONFIG

# Regex para parsear texto de HP/MP de Tibia: "512 / 850" o solo "512"
_NUMERIC_RE = re.compile(r'(\d+)\s*[/\\]\s*(\d+)')
_SINGLE_RE  = re.compile(r'(\d+)')

# Resolución de referencia para escalar ROI
_REF_W = 1920
_REF_H = 1080

# Umbrales de color para detectar HP (verde) y MP (azul)
# NOTA: este cliente Tibia usa VERDE para HP (G >> R, G >> B).
# Si tu cliente usa ROJO para HP, cambia _HP_G_MIN a _HP_R_MIN y
# ajusta la lógica en _read_bar() correspondientemente.
_HP_G_MIN  = 120   # Canal G debe ser > este valor
_HP_GR_MIN = 40    # G - R debe ser > este valor (evita blancos/amarillos)
_HP_GB_MIN = 40    # G - B debe ser > este valor

_MP_B_MIN  = 100   # Canal B debe ser > este valor
_MP_BR_MIN = 40    # B - R
_MP_BG_MIN = 20    # B - G


# ---------------------------------------------------------------------------
@dataclass
class NumericReading:
    """
    Resultado de una lectura OCR de los valores numéricos de HP/MP.

    Attributes
    ----------
    hp : int or None
        HP actual del personaje.  ``None`` si el OCR no pudo leer el valor.
    mp : int or None
        MP actual.
    hp_max : int or None
        HP máximo (sólo disponible si Tibia muestra "512 / 850").
    mp_max : int or None
        MP máximo.
    timestamp : float
        ``time.monotonic()`` del momento de la lectura.
    """
    hp:     Optional[int] = None
    mp:     Optional[int] = None
    hp_max: Optional[int] = None
    mp_max: Optional[int] = None
    timestamp: float = 0.0

    @property
    def hp_pct(self) -> Optional[float]:
        """HP como porcentaje exacto (0.0–100.0) si hp y hp_max conocidos."""
        if self.hp is not None and self.hp_max:
            return round(self.hp * 100.0 / self.hp_max, 1)
        return None

    @property
    def mp_pct(self) -> Optional[float]:
        """MP como porcentaje exacto (0.0–100.0) si mp y mp_max conocidos."""
        if self.mp is not None and self.mp_max:
            return round(self.mp * 100.0 / self.mp_max, 1)
        return None

    def age(self) -> float:
        """Segundos desde que se realizó la lectura."""
        return time.monotonic() - self.timestamp

    def is_stale(self, max_age: float = 5.0) -> bool:
        """True si han pasado más de *max_age* segundos desde la lectura."""
        return self.age() > max_age


# ---------------------------------------------------------------------------
@dataclass
class HpMpConfig:
    """
    Configuración de posiciones de barra HP/MP para el cliente Tibia.

    hp_roi / mp_roi: [x, y, w, h] en píxeles para resolución 1920×1080.
    Se escalan automáticamente si el frame es de otra resolución.

    Posiciones por defecto: barra lateral de Tibia v13 en 1920×1080.
    Ajustar según tu layout en hpmp_config.json.
    """
    # HP bar (barra roja): barra superior de la UI de Tibia, frame OBS 1920×1080
    hp_roi: List[int] = field(default_factory=lambda: [484, 316, 1376, 9])
    # MP bar (barra azul/lila): justo debajo de HP
    mp_roi: List[int] = field(default_factory=lambda: [374, 328, 1486, 10])
    # Cooldown mínimo entre usos del mismo hotkey (segundos)
    heal_cooldown: float = 1.0
    mana_cooldown: float = 1.0
    # Número de lecturas para suavizar (evita falsos positivos por ruido)
    # ≥3 recomendado para producción; 1 = sin suavizado.
    smoothing: int = 3
    # Máximo cambio aceptable entre lecturas consecutivas (% absolute).
    # Si |new - old| > outlier_threshold, se descarta la lectura nueva
    # y se retorna la anterior. 0 = deshabilitado (acepta todo).
    outlier_threshold: int = 35
    # ── OCR num\u00e9rico (path lento, opcional) ──────────────────────────────────
    # ROI del texto de HP (p.ej. "512 / 850"):  [x, y, w, h] en 1920×1080
    hp_text_roi: List[int] = field(default_factory=lambda: [484, 310, 1376, 20])
    # ROI del texto de MP
    mp_text_roi: List[int] = field(default_factory=lambda: [374, 322, 1486, 20])
    # Confianza m\u00ednima aceptada de EasyOCR (0.0 \u2013 1.0)
    ocr_confidence: float = 0.3
    # Intervalo del lector OCR en background (segundos; 0 = deshabilitado)
    numeric_update_interval: float = 0.0

    def save(self, path: Path = HPMP_CONFIG_FILE) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, indent=2)

    def validate(self) -> None:
        """Raise ValueError if any ROI is invalid."""
        for name in ("hp_roi", "mp_roi", "hp_text_roi", "mp_text_roi"):
            roi = getattr(self, name)
            if len(roi) != 4:
                raise ValueError(f"HpMpConfig.{name} must have 4 elements, got {len(roi)}")
            if any(v < 0 for v in roi):
                raise ValueError(f"HpMpConfig.{name} values must be non-negative: {roi}")

    @classmethod
    def load(cls, path: Path = HPMP_CONFIG_FILE) -> "HpMpConfig":
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        obj = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        obj.validate()
        return obj


# ---------------------------------------------------------------------------
class HpMpDetector:
    """
    Lee HP% y MP% de un frame BGR de OBS.

    Ejemplo de uso:
    ---------------
    >>> det = HpMpDetector()
    >>> frame = obs_source.get_frame()   # numpy BGR
    >>> hp, mp = det.read_bars(frame)
    >>> print(f"HP: {hp}%  MP: {mp}%")

    En el auto-walker, se combina con InputController para curar:
    >>> if hp is not None and hp < 70:
    ...     ctrl.press_key(Key.F1)     # exura ico / poción HP
    """

    def __init__(self, config: Optional[HpMpConfig] = None) -> None:
        self._cfg = config or HpMpConfig.load()
        self._last_heal_t = 0.0
        self._last_mana_t = 0.0
        self._resolution_warned: bool = False
        # ── Canonical lock acquisition order (MUST be respected everywhere) ──
        # To prevent deadlock, threads that need multiple locks MUST acquire
        # them in this exact order, outermost first:
        #   1. _ocr_reader_lock  (outermost — protects EasyOCR initialisation)
        #   2. _numeric_lock     (middle — protects cached OCR result)
        #   3. _history_lock     (innermost — protects smoothing history & last readings)
        # Never acquire a lock that is earlier in this order while holding one
        # that is later (e.g. never acquire _ocr_reader_lock inside _history_lock).
        # ── Lock for history / last-reading state ────────────────────────────
        # Protects _hp_history, _mp_history, _last_hp, _last_mp,
        # _hp_outlier_rejects, _mp_outlier_rejects, _hp_extreme_drops,
        # _hp_confidence, _mp_confidence against concurrent access from
        # the session loop (read_bars) and control threads (reset_history,
        # set_smoothing, stats_snapshot).
        self._history_lock: threading.Lock = threading.Lock()
        # Historial para suavizado — bounded deque(maxlen=60) avoids unbounded growth
        self._hp_history: collections.deque = collections.deque(maxlen=60)
        self._mp_history: collections.deque = collections.deque(maxlen=60)
        # Last valid readings (None until first successful read)
        self._last_hp: Optional[int] = None
        self._last_mp: Optional[int] = None
        # ── Confidence tracking ──────────────────────────────────────────────
        # Ratio of colored columns to total bar width (0.0-1.0).
        # High confidence (> 0.1 for non-zero HP) means the ROI is well-placed.
        self._hp_confidence: float = 0.0
        self._mp_confidence: float = 0.0
        # ── Outlier rejection counters ───────────────────────────────────────
        self._hp_outlier_rejects: int = 0
        self._mp_outlier_rejects: int = 0
        # ── Extreme-drop diagnostics ─────────────────────────────────────────
        # Counts how many times HP dropped > 50 % in a single tick (potential
        # OCR artifact or game-pause glitch).  The reading is still accepted
        # (safety-first) but a warning is logged for monitoring.
        self._hp_extreme_drops: int = 0
        # ── OCR numeric reader state ─────────────────────────────────────────
        # Cached result from background OCR thread
        self._numeric: Optional[NumericReading] = None
        self._numeric_lock: threading.Lock = threading.Lock()
        # Background reader thread
        self._numeric_thread: Optional[threading.Thread] = None
        self._numeric_stop: threading.Event = threading.Event()
        # Lazy EasyOCR reader (None until first OCR call)
        self._ocr_reader: Any = None
        self._ocr_reader_lock: threading.Lock = threading.Lock()  # prevent double-init

    # ── Pre-loading ──────────────────────────────────────────────────────────

    def preload_ocr(self) -> None:
        """Pre-load EasyOCR in a background thread to avoid blocking the first read."""
        def _load() -> None:
            self._get_ocr_reader()
        t = threading.Thread(target=_load, daemon=True, name=f"t-{id(self):x}")
        t.start()

    # ── API pública ──────────────────────────────────────────────────────────
    def _apply_outlier_filter(
        self,
        value: int,
        last_value: int,
        reject_count: int,
        thr: int,
        log_extreme_drop: bool = False,
    ) -> Tuple[Optional[int], int]:
        delta = abs(value - last_value)
        is_drop = (last_value - value) > 0
        if log_extreme_drop and is_drop and delta > 50:
            self._hp_extreme_drops += 1
            logger.warning(
                "[HpMp] Extreme HP drop: %d%% → %d%% "
                "(Δ=%d%%, tick #%d). Possible OCR error "
                "or instant-kill event.",
                last_value, value, delta, self._hp_extreme_drops,
            )
        if thr <= 0:
            return value, 0
        if delta > thr and not is_drop:
            reject_count += 1
            if reject_count >= _OUTLIER_RESET:
                reject_count = 0
            else:
                return None, reject_count
        else:
            reject_count = 0
        return value, reject_count

    def read_bars(
        self,
        frame: np.ndarray,
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Lee HP% y MP% del frame.

        Devuelve
        --------
        (hp_percent, mp_percent)
            Valores 0-100, o None si el ROI es inválido o el color no se detecta.
        """
        hp = self._read_bar(frame, self._cfg.hp_roi, "hp")
        mp = self._read_bar(frame, self._cfg.mp_roi, "mp")

        # ── Outlier rejection + smoothing (guarded by _history_lock) ────────
        # The lock protects history lists and last readings against concurrent
        # access from reset_history / set_smoothing / stats_snapshot.
        thr = self._cfg.outlier_threshold
        with self._history_lock:
            if hp is not None and self._last_hp is not None:
                hp, self._hp_outlier_rejects = self._apply_outlier_filter(
                    hp, self._last_hp, self._hp_outlier_rejects, thr, log_extreme_drop=True
                )
            if mp is not None and self._last_mp is not None:
                mp, self._mp_outlier_rejects = self._apply_outlier_filter(
                    mp, self._last_mp, self._mp_outlier_rejects, thr
                )

            # ── Suavizado (media de las últimas N lecturas) ──────────────
            # deque handles the sliding window automatically via maxlen.
            if self._cfg.smoothing > 1:
                if hp is not None:
                    self._hp_history.append(hp)
                    win = list(self._hp_history)[-self._cfg.smoothing:]
                    hp = sum(win) // len(win)
                if mp is not None:
                    self._mp_history.append(mp)
                    win = list(self._mp_history)[-self._cfg.smoothing:]
                    mp = sum(win) // len(win)

            if hp is not None:
                self._last_hp = hp
            if mp is not None:
                self._last_mp = mp

        return hp, mp

    def read_hp(self, frame: np.ndarray) -> Optional[int]:
        """Lee solo el HP%."""
        return self._read_bar(frame, self._cfg.hp_roi, "hp")

    def read_mp(self, frame: np.ndarray) -> Optional[int]:
        """Lee solo el MP%."""
        return self._read_bar(frame, self._cfg.mp_roi, "mp")

    def heal_if_needed(
        self,
        frame: np.ndarray,
        hp_threshold: int,
        ctrl: Any,                  # InputController
        heal_vk: int,
        mp_threshold: int = 0,
        mana_vk: int     = 0,
    ) -> str:
        """
        Lee HP/MP y aplica el hotkey de curación si es necesario.

        Respeta el cooldown para no spamear hotkeys.

        Devuelve una cadena de diagnóstico, p.ej. "HP:45% HEAL!" o "HP:80% ok".
        """
        hp, mp = self.read_bars(frame)
        now    = time.monotonic()
        actions: List[str] = []

        # ── HP ──────────────────────────────────────────────────────────────
        if hp is None:
            hp_tag = "HP:?"
        else:
            hp_tag = f"HP:{hp}%"
            if (hp < hp_threshold
                    and now - self._last_heal_t >= self._cfg.heal_cooldown):
                ctrl.press_key(heal_vk)
                self._last_heal_t = now
                actions.append("HEAL!")

        # ── MP ──────────────────────────────────────────────────────────────
        if mp_threshold > 0 and mana_vk > 0:
            if mp is None:
                mp_tag = "MP:?"
            else:
                mp_tag = f"MP:{mp}%"
                if (mp < mp_threshold
                        and now - self._last_mana_t >= self._cfg.mana_cooldown):
                    ctrl.press_key(mana_vk)
                    self._last_mana_t = now
                    actions.append("MANA!")
        else:
            mp_tag = f"MP:{mp}%" if mp is not None else "MP:?"

        tag = f"{hp_tag} {mp_tag}"
        if actions:
            tag += " " + " ".join(actions)
        return tag

    def reset_history(self) -> None:
        """
        Clear the smoothing history buffers and the cached last readings.
        Useful when switching characters or scene sources.

        Lock order: _numeric_lock (outer) acquired before _history_lock (inner),
        consistent with the canonical order defined in __init__.
        """
        with self._numeric_lock:
            self._numeric = None
        with self._history_lock:
            self._hp_history.clear()
            self._mp_history.clear()
            self._last_hp = None
            self._last_mp = None
            self._hp_confidence = 0.0
            self._mp_confidence = 0.0
            self._hp_outlier_rejects = 0
            self._mp_outlier_rejects = 0

    @property
    def last_hp(self) -> Optional[int]:
        """Last successfully read HP percentage (0-100), or None if never read."""
        with self._history_lock:
            return self._last_hp

    @property
    def last_mp(self) -> Optional[int]:
        """Last successfully read MP percentage (0-100), or None if never read."""
        with self._history_lock:
            return self._last_mp

    def update_config(self, config: HpMpConfig) -> None:
        """
        Hot-swap the detector configuration.  Clears smoothing history so
        the new ROI/thresholds take effect immediately.
        """
        self._cfg = config
        self.reset_history()

    def set_smoothing(self, n: int) -> None:
        """
        Change the smoothing window size on the fly.

        *n* must be ≥ 1.  Setting ``n=1`` effectively disables smoothing.
        Trims existing history buffers to the new window size.
        """
        if n < 1:
            raise ValueError(f"smoothing must be >= 1, got {n}")
        self._cfg.smoothing = n
        with self._history_lock:
            # Trim to last n entries while keeping them as a deque(maxlen=60)
            trimmed_hp = list(self._hp_history)[-n:]
            trimmed_mp = list(self._mp_history)[-n:]
            self._hp_history.clear()
            self._hp_history.extend(trimmed_hp)
            self._mp_history.clear()
            self._mp_history.extend(trimmed_mp)

    def is_critical(
        self,
        hp_threshold: int,
        mp_threshold: int = 0,
    ) -> bool:
        """
        Return ``True`` when the last cached HP/MP reading is below the given
        threshold(s).

        If a reading has never been made (``last_hp`` / ``last_mp`` is
        ``None``) the check for that bar is treated as *not critical*.

        Parameters
        ----------
        hp_threshold:
            HP% below which the character is considered critical.
        mp_threshold:
            MP% below which to flag as critical (0 disables the MP check).
        """
        hp_crit = self.last_hp is not None and self.last_hp < hp_threshold
        if mp_threshold > 0:
            mp_crit = self.last_mp is not None and self.last_mp < mp_threshold
            return hp_crit or mp_crit
        return hp_crit

    def stats_snapshot(self) -> dict[str, Any]:
        """
        Lightweight dict snapshot useful for logging or UI display.

        Keys: ``last_hp``, ``last_mp``, ``hp_history_len``, ``mp_history_len``,
        ``smoothing``, ``hp_exact``, ``mp_exact``, ``hp_max``, ``mp_max``,
        ``numeric_age_s``, ``numeric_reader_running``, ``hp_confidence``,
        ``mp_confidence``, ``hp_outlier_rejects``, ``mp_outlier_rejects``.
        """
        n = self.numeric
        with self._history_lock:
            return {
                "last_hp":              self._last_hp,
                "last_mp":              self._last_mp,
                "hp_history_len":       len(self._hp_history),
                "mp_history_len":       len(self._mp_history),
                "smoothing":            self._cfg.smoothing,
                "hp_exact":             n.hp     if n else None,
                "mp_exact":             n.mp     if n else None,
                "hp_max":               n.hp_max if n else None,
                "mp_max":               n.mp_max if n else None,
                "numeric_age_s":        round(n.age(), 2) if n else None,
                "numeric_reader_running": self.numeric_reader_running,
                "hp_confidence":        round(self._hp_confidence, 3),
                "mp_confidence":        round(self._mp_confidence, 3),
                "hp_outlier_rejects":   self._hp_outlier_rejects,
                "mp_outlier_rejects":   self._mp_outlier_rejects,
                "outlier_rejects_hp":   self._hp_outlier_rejects,
                "outlier_rejects_mp":   self._mp_outlier_rejects,
                "hp_extreme_drops":     self._hp_extreme_drops,
            }

    @property
    def has_history(self) -> bool:
        """True when at least one HP or MP reading is in the history buffer."""
        with self._history_lock:
            return len(self._hp_history) > 0 or len(self._mp_history) > 0

    @property
    def average_hp(self) -> Optional[float]:
        """Running average of HP readings in the current smoothing window,
        or ``None`` when the history is empty."""
        with self._history_lock:
            if not self._hp_history:
                return None
            return sum(self._hp_history) / len(self._hp_history)

    @property
    def average_mp(self) -> Optional[float]:
        """Running average of MP readings in the current smoothing window,
        or ``None`` when the history is empty."""
        with self._history_lock:
            if not self._mp_history:
                return None
            return sum(self._mp_history) / len(self._mp_history)

    @property
    def hp_ratio(self) -> Optional[float]:
        """Last HP reading normalised to 0.0–1.0 (``last_hp / 100``),
        or ``None`` when no reading is available yet."""
        hp = self.last_hp
        return hp / 100.0 if hp is not None else None

    @property
    def mp_ratio(self) -> Optional[float]:
        """Last MP reading normalised to 0.0–1.0 (``last_mp / 100``),
        or ``None`` when no reading is available yet."""
        mp = self.last_mp
        return mp / 100.0 if mp is not None else None

    @property
    def has_hp(self) -> bool:
        """True when at least one HP reading has been stored."""
        with self._history_lock:
            return self._last_hp is not None

    @property
    def has_mp(self) -> bool:
        """True when at least one MP reading has been stored."""
        with self._history_lock:
            return self._last_mp is not None

    @property
    def hp_history_size(self) -> int:
        """Number of HP readings currently in the smoothing history buffer."""
        with self._history_lock:
            return len(self._hp_history)

    @property
    def mp_history_size(self) -> int:
        """Number of MP readings currently in the smoothing history buffer."""
        with self._history_lock:
            return len(self._mp_history)

    @property
    def has_both(self) -> bool:
        """True when at least one HP **and** one MP reading have been recorded."""
        return self.has_hp and self.has_mp

    @property
    def is_reading_stable(self) -> bool:
        """True when the HP history buffer is full (≥ smoothing window size).

        A full buffer means the running average is no longer influenced by the
        sparse early samples that bias results at startup.
        """
        return self.hp_history_size >= self._cfg.smoothing

    @property
    def warmed_up(self) -> bool:
        """Compatibility alias for callers that expect the detector warm-up state."""
        return self.is_reading_stable

    @property
    def hp_confidence(self) -> float:
        """Proportion of colored columns in the HP ROI on the last read (0.0–1.0).

        A value close to ``last_hp / 100`` indicates the ROI is well-placed.
        A value near 0.0 when ``last_hp`` > 0 suggests the ROI is misaligned.
        """
        with self._history_lock:
            return self._hp_confidence

    @property
    def mp_confidence(self) -> float:
        """Proportion of colored columns in the MP ROI on the last read (0.0–1.0)."""
        with self._history_lock:
            return self._mp_confidence

    @property
    def hp_outlier_rejects(self) -> int:
        """Number of HP readings discarded by the outlier rejection filter."""
        with self._history_lock:
            return self._hp_outlier_rejects

    @property
    def mp_outlier_rejects(self) -> int:
        """Number of MP readings discarded by the outlier rejection filter."""
        with self._history_lock:
            return self._mp_outlier_rejects

    # ── OCR numérico (path lento, ±0 % exacto) ─────────────────────────────

    def read_numeric_hpmp(self, frame: np.ndarray) -> NumericReading:
        """
        Lee los valores exactos de HP y MP del HUD de Tibia usando OCR.

        Este método es el **path lento** (~150-400 ms en CPU).  No debe
        llamarse en el loop principal del healer; usar
        :meth:`start_numeric_reader` para una actualización en background.

        Parámetros
        ----------
        frame : np.ndarray
            Frame BGR del juego (OBS o MSS).

        Returns
        -------
        NumericReading
            Valores exactos de HP/MP y sus máximos cuando el formato es
            ``"512 / 850"``.  Los campos son ``None`` si el OCR falla.
        """
        hp, hp_max = self._ocr_bar_text(frame, self._cfg.hp_text_roi)
        mp, mp_max = self._ocr_bar_text(frame, self._cfg.mp_text_roi)
        return NumericReading(
            hp=hp, mp=mp, hp_max=hp_max, mp_max=mp_max,
            timestamp=time.monotonic(),
        )

    def start_numeric_reader(
        self,
        frame_getter: Callable[[], Optional[np.ndarray]],
        interval: Optional[float] = None,
    ) -> None:
        """
        Inicia un hilo daemon que llama :meth:`read_numeric_hpmp` a
        intervalos regulares y almacena el resultado en caché.

        Accede al resultado con :attr:`numeric`, :attr:`hp_exact`, etc.

        Parámetros
        ----------
        frame_getter : callable
            ``() -> np.ndarray | None`` — fuente de frames.
        interval : float, optional
            Segundos entre lecturas.  Si es ``None`` usa
            ``config.numeric_update_interval`` (por defecto 1.0 s si éste
            también es 0).
        """
        if self._numeric_thread and self._numeric_thread.is_alive():
            return  # ya está corriendo

        update_secs = interval or self._cfg.numeric_update_interval or 1.0
        self._numeric_stop.clear()

        def _loop() -> None:
            while not self._numeric_stop.is_set():
                try:
                    frm = frame_getter()
                    if frm is not None:
                        result = self.read_numeric_hpmp(frm)
                        with self._numeric_lock:
                            self._numeric = result
                except Exception:
                    logger.debug(
                        "HpMpNumericReader: error in OCR read cycle",
                        exc_info=True,
                    )
                # Use timeout so the thread responds promptly to stop() calls
                # even when update_secs is large.  Clamp to max 1.0 s for safety.
                self._numeric_stop.wait(timeout=min(update_secs, 1.0))

        self._numeric_thread = threading.Thread(
            target=_loop, daemon=True, name="HpMpNumericReader"
        )
        self._numeric_thread.start()

    def stop_numeric_reader(self) -> None:
        """Detiene el hilo background OCR iniciado con :meth:`start_numeric_reader`."""
        self._numeric_stop.set()
        if self._numeric_thread:
            self._numeric_thread.join(timeout=2.0)
            self._numeric_thread = None

    @property
    def numeric(self) -> Optional[NumericReading]:
        """Último resultado OCR cacheado por el lector background, o ``None``."""
        with self._numeric_lock:
            return self._numeric

    @property
    def hp_exact(self) -> Optional[int]:
        """HP exacto del último OCR, o ``None`` si no disponible."""
        n = self.numeric
        return n.hp if n is not None else None

    @property
    def mp_exact(self) -> Optional[int]:
        """MP exacto del último OCR, o ``None`` si no disponible."""
        n = self.numeric
        return n.mp if n is not None else None

    @property
    def hp_max(self) -> Optional[int]:
        """HP máximo del último OCR (solo si Tibia muestra ``X / Y``)."""
        n = self.numeric
        return n.hp_max if n is not None else None

    @property
    def mp_max(self) -> Optional[int]:
        """MP máximo del último OCR."""
        n = self.numeric
        return n.mp_max if n is not None else None

    @property
    def hp_pct_exact(self) -> Optional[float]:
        """
        Porcentaje de HP con máxima exactitud disponible:

        * Si el lector background tiene datos frescos (< 5 s) con hp y
          hp_max conocidos → devuelve el % exacto (0.0–100.0) con 1 decimal.
        * Si sólo hay hp (sin hp_max) → devuelve ``last_hp`` de la barra.
        * Si no hay ningún dato → ``None``.
        """
        n = self.numeric
        if n and not n.is_stale(5.0) and n.hp_pct is not None:
            return n.hp_pct
        # Fallback: barra
        return float(self._last_hp) if self._last_hp is not None else None

    @property
    def mp_pct_exact(self) -> Optional[float]:
        """
        Porcentaje de MP con máxima exactitud disponible.

        Igual lógica que :attr:`hp_pct_exact` pero para MP.
        """
        n = self.numeric
        if n and not n.is_stale(5.0) and n.mp_pct is not None:
            return n.mp_pct
        return float(self._last_mp) if self._last_mp is not None else None

    @property
    def numeric_reader_running(self) -> bool:
        """True si el hilo background OCR está activo."""
        return self._numeric_thread is not None and self._numeric_thread.is_alive()

    # ── Diagnóstico ──────────────────────────────────────────────────────────
    def debug_overlay(self, frame: np.ndarray) -> np.ndarray:
        """
        Dibuja los ROIs de HP y MP sobre el frame (copia).
        Útil para verificar visualmente que los ROIs están bien calibrados.
        """
        out  = frame.copy()
        fh, fw = frame.shape[:2]
        sx = fw / _REF_W
        sy = fh / _REF_H

        def _draw(roi: Any, color: Any, label: str) -> None:
            x, y, w, h = roi
            x0 = int(x * sx); y0 = int(y * sy)
            x1 = int((x+w) * sx); y1 = int((y+h) * sy)
            cv2.rectangle(out, (x0, y0), (x1, y1), color, 2)
            cv2.putText(out, label, (x0, max(0, y0-5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        _draw(self._cfg.hp_roi, (0, 0, 255), "HP")
        _draw(self._cfg.mp_roi, (255, 0, 0), "MP")
        return out

    def save_debug_image(
        self,
        frame: np.ndarray,
        path: str = "debug_hpmp.png",
    ) -> None:
        """Guarda el frame con los ROIs de HP y MP dibujados."""
        import sys
        if getattr(sys, 'frozen', False):
            return
        out = self.debug_overlay(frame)
        cv2.imwrite(path, out)

    # ── Auto-calibration ─────────────────────────────────────────────────────

    def auto_calibrate(self, frame: np.ndarray, *, save: bool = False) -> bool:
        """Scan *frame* to auto-detect HP and MP bar positions and update ROIs.

        Looks for the widest horizontal band of HP-colored (saturated, non-blue)
        pixels and MP-colored (blue-dominant) pixels.  Both bars must span at
        least 12.5 % of the frame width to be accepted.

        Parameters
        ----------
        frame:
            Live BGR frame from the game client.
        save:
            If True, persist the detected ROIs to ``hpmp_config.json``.

        Returns
        -------
        bool
            True when both bars were found and ``self._cfg`` was updated.
        """
        if frame is None or frame.ndim < 3 or frame.shape[2] < 3:
            return False

        fh, fw = frame.shape[:2]
        min_w = max(20, fw // 8)  # bar must span ≥ 12.5 % of screen width

        b = frame[:, :, 0].astype(np.int32)
        g = frame[:, :, 1].astype(np.int32)
        r = frame[:, :, 2].astype(np.int32)

        # HP mask: saturated, bright, not blue-dominant (matches _read_bar logic)
        mx_rg = np.maximum(r, g)
        mx    = np.maximum(mx_rg, b)
        mn    = np.minimum(np.minimum(r, g), b)
        hp_mask = ((mx - mn) >= 30) & (mx >= 80) & (mx_rg >= b)

        # MP mask: blue-dominant (matches _read_bar logic)
        mp_mask = (
            (b >= _MP_B_MIN) &
            (b - r >= _MP_BR_MIN) &
            (b - g >= _MP_BG_MIN)
        )

        hp_roi_px = self._detect_bar(hp_mask, min_w)
        mp_roi_px = self._detect_bar(mp_mask, min_w)

        if hp_roi_px is None or mp_roi_px is None:
            logger.warning(
                "[HpMp] auto_calibrate: could not find %s bar in frame",
                "HP" if hp_roi_px is None else "MP",
            )
            return False

        # Convert from actual pixel coords → 1920×1080 reference space
        sx, sy = _REF_W / fw, _REF_H / fh

        def _scale(roi_px: List[int]) -> List[int]:
            x, y, w, h = roi_px
            return [int(x * sx), int(y * sy), max(4, int(w * sx)), max(1, int(h * sy))]

        self._cfg.hp_roi = _scale(hp_roi_px)
        self._cfg.mp_roi = _scale(mp_roi_px)
        self.reset_history()

        logger.info(
            "[HpMp] Auto-calibrated: HP=%s  MP=%s",
            self._cfg.hp_roi, self._cfg.mp_roi,
        )

        if save:
            try:
                self._cfg.save()
            except Exception as exc:
                logger.warning("[HpMp] Could not save calibration: %s", exc)

        return True

    @staticmethod
    def _detect_bar(mask: np.ndarray, min_width: int) -> Optional[List[int]]:
        """Find the widest horizontal bar in *mask* (bool 2D array).

        Returns ``[x, y, w, h]`` in pixel coordinates, or ``None`` when no
        band of sufficient width is found.
        """
        row_counts = mask.sum(axis=1).astype(np.int32)
        candidate_rows = np.where(row_counts >= min_width)[0]
        if len(candidate_rows) == 0:
            return None

        # Group consecutive rows into bands (allow 3-px gaps for antialiasing)
        bands: List[Tuple[int, int]] = []
        y0 = y1 = int(candidate_rows[0])
        for r in candidate_rows[1:]:
            if r <= y1 + 3:
                y1 = int(r)
            else:
                bands.append((y0, y1))
                y0 = y1 = int(r)
        bands.append((y0, y1))

        # Best band = most colored pixels total
        best = max(bands, key=lambda b: int(mask[b[0]: b[1] + 1].sum()))
        y0, y1 = best

        # Horizontal extent of the best band
        band = mask[y0: y1 + 1]
        colored_cols = np.where(band.any(axis=0))[0]
        if len(colored_cols) < min_width:
            return None

        x0 = int(colored_cols[0])
        x1 = int(colored_cols[-1]) + 1
        return [x0, y0, x1 - x0, y1 - y0 + 1]

    # ── Internos ─────────────────────────────────────────────────────────────
    def _read_bar(
        self,
        frame: np.ndarray,
        roi:   List[int],
        kind:  str,    # "hp" | "mp"
    ) -> Optional[int]:
        """
        Recorta el ROI y cuenta píxeles del color correcto.

        Estrategia:
        - Recortar la región de la barra
        - Para HP: píxeles coloreados (saturación alta).  Tibia usa un degradado
          verde→amarillo→naranja→rojo según el % de vida. La porción vacía es
          gris oscuro (≈42,42,42). Detectamos *cualquier* pixel saturado y/o
          brillante que no sea fondo gris.
        - Para MP: píxeles donde B >> R y B >> G  (azul intenso)
        - El porcentaje de fill se estima como la columna más a la derecha
          con al menos 1 píxel de color, relativa al ancho total.
        """
        # Guard: need 3-channel BGR frame
        if frame is None or frame.ndim < 3 or frame.shape[2] < 3:
            return None

        fh, fw = frame.shape[:2]

        # B5: warn once if frame resolution differs from reference
        if not getattr(self, '_resolution_warned', False):
            if fw != _REF_W or fh != _REF_H:
                logger.info(
                    "[HpMp] Frame %dx%d differs from reference %dx%d "
                    "— ROIs will be auto-scaled",
                    fw, fh, _REF_W, _REF_H,
                )
                self._resolution_warned = True

        x, y, w, h = roi

        # Escalar ROI
        sx = fw / _REF_W
        sy = fh / _REF_H
        x0 = max(0, int(x * sx))
        y0 = max(0, int(y * sy))
        x1 = min(fw, int((x+w) * sx))
        y1 = min(fh, int((y+h) * sy))

        if x1 - x0 < 4 or y1 - y0 < 1:
            return None

        bar = frame[y0:y1, x0:x1]          # BGR
        if bar.size == 0:
            return None

        bw = x1 - x0   # ancho real del ROI en píxeles

        # Separar canales BGR
        b_ch = bar[:, :, 0].astype(np.int32)
        g_ch = bar[:, :, 1].astype(np.int32)
        r_ch = bar[:, :, 2].astype(np.int32)

        if kind == "hp":
            # Detect the FULL color gradient of the Tibia HP bar:
            #   Green  (100%): BGR≈(0,190,0)   → G dominant, high saturation
            #   Yellow  (70%): BGR≈(4,158,109)  → G>R, moderate saturation
            #   Orange  (40%): BGR≈(9,152,200)  → R>G, moderate saturation
            #   Red     (25%): BGR≈(47,47,190)  → R dominant, high saturation
            #
            # Empty bar:       BGR≈(42,42,42)   → all channels equal, low value
            # Panel background: BGR≈(68-80,68-80,68-80) → gray, low saturation
            #
            # Strategy: a pixel is "HP colored" if it has enough saturation
            # (difference between max and min channel), enough brightness,
            # AND is NOT blue-dominant (blue = MP bar).
            max_rg = np.maximum(r_ch, g_ch)
            max_ch = np.maximum(max_rg, b_ch)
            min_ch = np.minimum(np.minimum(r_ch, g_ch), b_ch)
            saturation = max_ch - min_ch
            colored = (
                (saturation >= 30) &
                (max_ch >= 80) &
                (max_rg >= b_ch)       # NOT blue-dominant
            )
        else:  # mp
            colored = (
                (b_ch >= _MP_B_MIN) &
                (b_ch - r_ch >= _MP_BR_MIN) &
                (b_ch - g_ch >= _MP_BG_MIN)
            )

        # Columnas que tienen al menos 1 píxel de color
        cols_with_color = np.where(colored.any(axis=0))[0]

        if len(cols_with_color) == 0:
            return 0

        # CORRECTO: usar el número de columnas coloreadas como porcentaje del ancho
        # total del ROI (robusto ante stray pixels y bars parcialmente llenas).
        # El algorithm anterior (cols[-1]/bw) sobreestimaba si había pixels sueltos
        # en el extremo derecho o si el ROI no comenzaba en el borde izquierdo de
        # la barra.
        pct = min(100, round(len(cols_with_color) * 100 / bw))

        # ── Confidence: proportion of colored columns vs bar width ────────
        # Used externally to assess whether the ROI is well-placed.
        conf = len(cols_with_color) / max(1, bw)
        with self._history_lock:
            if kind == "hp":
                self._hp_confidence = conf
            else:
                self._mp_confidence = conf

        return pct

    def _get_ocr_reader(self) -> Any:
        """Inicializa EasyOCR de forma lazy (solo dígitos, sin GPU requerida)."""
        if self._ocr_reader is not None:
            return self._ocr_reader
        with self._ocr_reader_lock:
            # Double-checked locking: re-test inside the lock
            if self._ocr_reader is None:
                try:
                    import easyocr
                    self._ocr_reader = easyocr.Reader(
                        ["en"], gpu=False, verbose=False,
                        # Usar solo el detector de dígitos para mayor velocidad
                        recognizer=True,
                    )
                except ImportError:
                    pass  # easyocr no instalado → OCR silenciosamente deshabilitado
        return self._ocr_reader

    def _preprocess_text_roi(self, frame: np.ndarray, roi: List[int]) -> Optional[np.ndarray]:
        """
        Extrae y preprocesa la ROI de texto para OCR.

        Pipeline: recorte → escala 4× → bilateral → umbral adaptativo.
        Devuelve ``None`` si la ROI es inválida.
        """
        fh, fw = frame.shape[:2]
        sx = fw / _REF_W
        sy = fh / _REF_H
        x, y, w, h = roi
        x0 = max(0, int(x * sx))
        y0 = max(0, int(y * sy))
        x1 = min(fw, int((x + w) * sx))
        y1 = min(fh, int((y + h) * sy))

        if x1 - x0 < 4 or y1 - y0 < 2:
            return None

        crop = frame[y0:y1, x0:x1]

        # Conversión a grises
        if crop.ndim == 3 and crop.shape[2] == 4:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGRA2GRAY)
        else:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # ── Text isolation: Tibia numeric text is white or yellow on dark panel.
        # Mask out pixels below brightness 120 before adaptive threshold so that
        # busy game-world backgrounds (floor tiles, monsters, effects) do not
        # bleed into the binarised image and confuse the digit recogniser.
        _, bright_mask = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY)
        gray = cv2.bitwise_and(gray, bright_mask)

        # Escalar 4× para mejorar legibilidad de dígitos pequeños
        rh, rw = gray.shape
        scaled = cv2.resize(gray, (rw * 4, rh * 4), interpolation=cv2.INTER_CUBIC)

        # Filtro bilateral (preserva bordes de dígitos)
        filtered = cv2.bilateralFilter(scaled, 9, 75, 75)

        # Umbral adaptativo
        binary = cv2.adaptiveThreshold(
            filtered, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=15, C=8,
        )

        # Dilatación leve para fortalecer el trazo de dígitos pequeños
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        return cv2.dilate(binary, kernel, iterations=1)

    def _ocr_bar_text(
        self,
        frame: np.ndarray,
        roi: List[int],
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Lee los números de una barra usando OCR.

        Devuelve ``(current, maximum)`` cuando el texto tiene formato
        ``"512 / 850"``; ``(current, None)`` cuando es solo ``"512"``;
        ``(None, None)`` si el OCR falla o no está instalado.
        """
        reader = self._get_ocr_reader()
        if reader is None:
            return None, None

        img = self._preprocess_text_roi(frame, roi)
        if img is None:
            return None, None

        try:
            results = reader.readtext(
                img,
                allowlist="0123456789/ ",
                detail=1,
                paragraph=False,
            )
        except Exception:
            return None, None

        # Concatenar todas las detecciones con confianza suficiente
        texts = [
            text
            for (_bbox, text, conf) in results
            if conf >= self._cfg.ocr_confidence and text.strip()
        ]
        full_text = " ".join(texts).strip()

        # Intentar formato "current / max"
        m = _NUMERIC_RE.search(full_text)
        if m:
            try:
                return int(m.group(1)), int(m.group(2))
            except ValueError:
                pass

        # Intentar formato de solo el valor actual
        m2 = _SINGLE_RE.search(full_text)
        if m2:
            try:
                return int(m2.group(1)), None
            except ValueError:
                pass

        return None, None
