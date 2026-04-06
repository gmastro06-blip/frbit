"""MetricsCollector — estadísticas en tiempo real, reportes JSON, log rotation."""

from __future__ import annotations

import json
import logging
import os
import statistics
import threading
import time
from collections import deque
from datetime import datetime, date
from typing import Any, Deque, Dict, List, Tuple

_log = logging.getLogger(__name__)


class MetricsCollector:
    """Recolecta y analiza métricas de inputs generados (thread-safe)."""

    def __init__(self, log_directory: str, max_samples: int = 10_000) -> None:
        self._log_dir = log_directory
        self._max = max_samples
        self._session_start = time.monotonic()
        self._lock = threading.Lock()

        # Deques para datos recientes
        self._reaction_times: Deque[float] = deque(maxlen=max_samples)
        self._key_durations: Deque[float] = deque(maxlen=max_samples)
        self._mouse_durations: Deque[float] = deque(maxlen=max_samples)
        self._mouse_path_lengths: Deque[int] = deque(maxlen=max_samples)
        self._errors: Deque[str] = deque(maxlen=max_samples)
        self._afk_durations: Deque[float] = deque(maxlen=max_samples)
        self._total_inputs: int = 0

        self._current_log_date: date = date.today()
        self._log_file_handle: Any = None

        os.makedirs(log_directory, exist_ok=True)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_key_press(
        self,
        key: str,
        duration: float,
        reaction_time: float,
        had_error: bool,
    ) -> None:
        with self._lock:
            self._reaction_times.append(reaction_time)
            self._key_durations.append(duration)
            self._total_inputs += 1
        self.log_with_timestamp(
            f"KEY key={key} dur={duration:.1f}ms react={reaction_time:.1f}ms err={had_error}"
        )

    def record_mouse_movement(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
        duration: float,
        path_length: int,
    ) -> None:
        with self._lock:
            self._mouse_durations.append(duration)
            self._mouse_path_lengths.append(path_length)
            self._total_inputs += 1
        self.log_with_timestamp(
            f"MOUSE {start}→{end} dur={duration:.1f}ms pts={path_length}"
        )

    def record_error(self, error_type: str) -> None:
        with self._lock:
            self._errors.append(error_type)
        self.log_with_timestamp(f"ERROR type={error_type}")

    def record_afk_pause(self, duration: float) -> None:
        with self._lock:
            self._afk_durations.append(duration)
        self.log_with_timestamp(f"AFK dur={duration:.1f}s")

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_statistics(self) -> Dict[str, Any]:
        with self._lock:
            session_dur = time.monotonic() - self._session_start
            return {
                "reaction_times": self._describe(list(self._reaction_times)),
                "key_press_durations": self._describe(list(self._key_durations)),
                "error_rates": self._error_stats(),
                "mouse_movements": {
                    "avg_duration": self._safe_mean(self._mouse_durations),
                    "avg_path_length": self._safe_mean(self._mouse_path_lengths),
                },
                "afk_pauses": {
                    "count": len(self._afk_durations),
                    "avg_duration": self._safe_mean(self._afk_durations),
                },
                "total_inputs": self._total_inputs,
                "session_duration_seconds": round(session_dur, 1),
            }

    @staticmethod
    def _describe(data: List[float]) -> Dict[str, Any]:
        if not data:
            return {"mean": 0, "median": 0, "std": 0, "min": 0, "max": 0,
                    "percentiles": {"p25": 0, "p50": 0, "p75": 0, "p95": 0}}
        sd = sorted(data)
        n = len(sd)
        return {
            "mean": round(statistics.mean(sd), 2),
            "median": round(statistics.median(sd), 2),
            "std": round(statistics.pstdev(sd), 2) if n > 1 else 0,
            "min": round(sd[0], 2),
            "max": round(sd[-1], 2),
            "percentiles": {
                "p25": round(sd[int(n * 0.25)], 2),
                "p50": round(sd[int(n * 0.50)], 2),
                "p75": round(sd[int(n * 0.75)], 2),
                "p95": round(sd[min(int(n * 0.95), n - 1)], 2),
            },
        }

    def _error_stats(self) -> Dict[str, Any]:
        total = self._total_inputs or 1
        by_type: Dict[str, int] = {}
        for e in self._errors:
            by_type[e] = by_type.get(e, 0) + 1
        return {
            "total": round(len(self._errors) / total, 4),
            "by_type": {k: round(v / total, 4) for k, v in by_type.items()},
        }

    @staticmethod
    def _safe_mean(d: deque) -> float:  # type: ignore[type-arg]
        return round(statistics.mean(d), 2) if d else 0.0

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def generate_report(self, output_path: str) -> None:
        """Escribe estadísticas completas a JSON."""
        stats = self.get_statistics()
        stats["generated_at"] = datetime.now().isoformat()
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        _log.info(f"[Metrics] Reporte generado: {output_path}")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_with_timestamp(self, message: str, level: str = "INFO") -> None:
        with self._lock:
            self.rotate_logs()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            line = f"[{ts}] [{level}] {message}\n"
            fh = self._get_log_handle()
            if fh is not None:
                fh.write(line)
                fh.flush()

    def rotate_logs(self) -> None:
        today = date.today()
        if today != self._current_log_date:
            # Cerrar anterior
            if self._log_file_handle is not None:
                try:
                    self._log_file_handle.close()
                except Exception:
                    pass
                self._log_file_handle = None
            self._current_log_date = today

    def _get_log_handle(self) -> Any:
        if self._log_file_handle is None:
            fname = f"humanizer_{self._current_log_date.isoformat()}.log"
            path = os.path.join(self._log_dir, fname)
            try:
                self._log_file_handle = open(path, "a", encoding="utf-8")
            except OSError as exc:
                _log.error(f"[Metrics] No se puede abrir log: {exc}")
        return self._log_file_handle

    def close(self) -> None:
        if self._log_file_handle is not None:
            try:
                self._log_file_handle.close()
            except Exception:
                pass
            self._log_file_handle = None
