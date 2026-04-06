"""
SoakMonitor — Resource and performance telemetry for soak testing.

Continuously tracks CPU, memory, thread count, and frame latency metrics
in a background thread.  Data is written to a rotating log file and
exposed via :meth:`stats_snapshot` for dashboard integration.

Usage::

    from src.soak_monitor import SoakMonitor, SoakMonitorConfig

    monitor = SoakMonitor()
    monitor.start()
    # ... bot runs for hours ...
    print(monitor.stats_snapshot())
    monitor.stop()
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("wn.sk")

_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SoakMonitorConfig:
    """Telemetry sampling configuration.

    sample_interval : float
        Seconds between metric samples (default 30 s).
    log_file : str
        Path to JSON-lines telemetry log (default output/soak_telemetry.jsonl).
    max_log_size_mb : float
        Max log file size before rotation (default 50 MB).
    memory_warn_mb : float
        Emit a warning when RSS exceeds this (default 500 MB).
    enabled : bool
        Master switch.
    """

    sample_interval: float = 30.0
    log_file: str = ""
    max_log_size_mb: float = 50.0
    memory_warn_mb: float = 500.0
    enabled: bool = True


# ---------------------------------------------------------------------------
# SoakMonitor
# ---------------------------------------------------------------------------

class SoakMonitor:
    """Background resource monitor for production soak testing."""

    def __init__(
        self,
        config: Optional[SoakMonitorConfig] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cfg = config or SoakMonitorConfig()
        self._log_fn = log_fn

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Running stats
        self._samples: int = 0
        self._peak_memory_mb: float = 0.0
        self._peak_threads: int = 0
        self._peak_cpu_pct: float = 0.0
        self._warnings: List[str] = []
        self._start_time: Optional[float] = None

        # Latest sample for dashboard
        self._latest: Dict[str, Any] = {}

        # Resolve log file path
        if self._cfg.log_file:
            self._log_path = Path(self._cfg.log_file)
        else:
            self._log_path = _OUTPUT_DIR / "soak_telemetry.jsonl"

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._start_time = time.monotonic()
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"t-{id(self):x}",
        )
        self._thread.start()
        self._log("[I] Telemetry monitor started.")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        self._log(
            f"[I] Monitor stopped — {self._samples} samples, "
            f"peak_mem={self._peak_memory_mb:.1f}MB, "
            f"peak_threads={self._peak_threads}"
        )

    @property
    def is_running(self) -> bool:
        return self._running

    def stats_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "samples": self._samples,
                "peak_memory_mb": round(self._peak_memory_mb, 1),
                "peak_threads": self._peak_threads,
                "peak_cpu_pct": round(self._peak_cpu_pct, 1),
                "warnings_count": len(self._warnings),
                "latest": dict(self._latest),
            }

    # ── Background loop ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Optional psutil — degrade gracefully
        try:
            psutil = importlib.import_module("psutil")
            proc = psutil.Process()
            has_psutil = True
        except ImportError:
            has_psutil = False
            self._log("[I] psutil not installed — limited telemetry.")

        while self._running:
            try:
                sample = self._collect_sample(
                    proc if has_psutil else None,
                    has_psutil,
                )
                self._record(sample)
            except Exception as exc:
                logger.warning("[I] sample error: %s", exc)

            # Sleep in chunks for responsive shutdown
            remaining = self._cfg.sample_interval
            while remaining > 0 and self._running:
                chunk = min(remaining, 2.0)
                time.sleep(chunk)
                remaining -= chunk

    def _collect_sample(
        self,
        proc: Any,
        has_psutil: bool,
    ) -> Dict[str, Any]:
        """Collect one telemetry sample."""
        now = time.time()
        elapsed = time.monotonic() - (self._start_time or time.monotonic())

        sample: Dict[str, Any] = {
            "ts": now,
            "elapsed_h": round(elapsed / 3600, 3),
        }

        if has_psutil and proc is not None:
            try:
                mem = proc.memory_info()
                sample["rss_mb"] = round(mem.rss / 1024 / 1024, 1)
                sample["vms_mb"] = round(mem.vms / 1024 / 1024, 1)
            except Exception:
                sample["rss_mb"] = 0.0
                sample["vms_mb"] = 0.0

            try:
                sample["cpu_pct"] = proc.cpu_percent(interval=0.1)
            except Exception:
                sample["cpu_pct"] = 0.0

            try:
                sample["threads"] = proc.num_threads()
            except Exception:
                sample["threads"] = threading.active_count()

            try:
                _ps = importlib.import_module("psutil")
                sample["system_cpu_pct"] = _ps.cpu_percent(interval=None)
                _vm = _ps.virtual_memory()
                sample["system_mem_pct"] = _vm.percent
            except Exception:
                logger.debug("SoakMonitor failed to collect system-wide psutil stats", exc_info=True)
        else:
            sample["threads"] = threading.active_count()

        return sample

    def _record(self, sample: Dict[str, Any]) -> None:
        """Record a sample: update peaks, write to log, check thresholds."""
        with self._lock:
            self._samples += 1
            self._latest = sample

            rss = sample.get("rss_mb", 0.0)
            if rss > self._peak_memory_mb:
                self._peak_memory_mb = rss

            threads = sample.get("threads", 0)
            if threads > self._peak_threads:
                self._peak_threads = threads

            cpu = sample.get("cpu_pct", 0.0)
            if cpu > self._peak_cpu_pct:
                self._peak_cpu_pct = cpu

        # Memory threshold warning
        if rss > self._cfg.memory_warn_mb:
            msg = f"[I] ⚠ Memory {rss:.0f}MB exceeds threshold {self._cfg.memory_warn_mb:.0f}MB"
            self._log(msg)
            with self._lock:
                self._warnings.append(msg)
                if len(self._warnings) > 200:
                    self._warnings = self._warnings[-200:]

        # Write to JSONL log (with rotation)
        self._write_log_line(sample)

    def _write_log_line(self, sample: Dict[str, Any]) -> None:
        """Append one JSON line to the telemetry log, rotating if needed."""
        try:
            if self._log_path.exists():
                size_mb = self._log_path.stat().st_size / 1024 / 1024
                if size_mb >= self._cfg.max_log_size_mb:
                    rotated = self._log_path.with_suffix(".jsonl.old")
                    # Only keep one rotation — overwrite previous .old
                    if rotated.exists():
                        rotated.unlink()
                    self._log_path.rename(rotated)
                    self._log(f"[I] Log rotated ({size_mb:.1f}MB) → {rotated.name}")

            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(sample) + "\n")
        except Exception as exc:
            logger.warning("[I] log write failed: %s", exc)

    def _log(self, msg: str) -> None:
        if self._log_fn is not None:
            self._log_fn(msg)
        else:
            logger.info(msg)
