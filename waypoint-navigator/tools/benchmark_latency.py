#!/usr/bin/env python
"""
Latency Benchmark — Measure real end-to-end pipeline timing.

Captures frames and measures each stage of the vision pipeline:
  1. Frame capture (MSS/DXCam)
  2. HP/MP gradient scan
  3. Battle list template matching
  4. Condition color detection
  5. Total end-to-end

Usage
-----
    python tools/benchmark_latency.py --source mss --monitor-idx 2 --iterations 100
    python tools/benchmark_latency.py --source dxcam --iterations 200
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import numpy as np


def _bench(fn: object, n: int) -> list[float]:
    """Run fn() n times and return list of elapsed ms."""
    times: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()  # type: ignore[operator]
        times.append((time.perf_counter() - t0) * 1000.0)
    return times


def _stats(label: str, times: list[float]) -> None:
    if not times:
        print(f"  {label:30s} — no data")
        return
    p50 = statistics.median(times)
    p95 = sorted(times)[int(len(times) * 0.95)]
    p99 = sorted(times)[int(len(times) * 0.99)]
    avg = statistics.mean(times)
    print(
        f"  {label:30s}  avg={avg:6.1f}ms  "
        f"p50={p50:6.1f}ms  p95={p95:6.1f}ms  p99={p99:6.1f}ms  "
        f"min={min(times):5.1f}ms  max={max(times):5.1f}ms"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark vision pipeline latency")
    parser.add_argument("--source", default="mss", help="Frame source")
    parser.add_argument("--monitor-idx", type=int, default=1)
    parser.add_argument("--iterations", "-n", type=int, default=100)
    args = parser.parse_args()

    n = args.iterations
    print(f"\n  Benchmark: {n} iterations with source={args.source}\n")

    # 1. Frame capture
    from frame_capture import build_frame_getter  # type: ignore[import-untyped]
    getter = build_frame_getter(args.source, monitor_idx=args.monitor_idx)

    # Warm up
    for _ in range(5):
        getter()

    capture_times = _bench(getter, n)
    _stats("Frame capture", capture_times)

    # Get a real frame for the remaining benchmarks
    frame = getter()
    if frame is None:
        print("  ERROR: no frame captured")
        return

    print(f"  Frame size: {frame.shape[1]}x{frame.shape[0]}")

    # 2. HP/MP gradient scan
    from hpmp_detector import HpMpDetector  # type: ignore[import-untyped]
    hp_det = HpMpDetector()
    hp_times = _bench(lambda: hp_det.read_bars(frame), n)
    _stats("HP/MP gradient scan", hp_times)

    # 3. Battle list template matching (via BattleDetector directly)
    from src.combat_manager import BattleDetector, CombatConfig  # type: ignore[import-untyped]
    cm = None
    try:
        _det = BattleDetector(CombatConfig())
        if _det.has_templates:
            detect_times = _bench(lambda: _det.detect(frame), n)
            _stats("Battle list detection  ", detect_times)
        else:
            print("  Battle list detection      — skipped (no templates)")
    except Exception as e:
        print(f"  Battle list detection      — skipped ({e})")

    # 4. Condition color detection
    from src.condition_monitor import ConditionDetector, ConditionConfig  # type: ignore[import-untyped]
    cd = ConditionDetector(ConditionConfig())
    cond_times = _bench(lambda: cd.detect(frame), n)
    _stats("Condition color detect", cond_times)

    # 5a. Frame diff hash — OLD approach (full tobytes then slice)
    def _hash_old():
        hash(frame.data.tobytes()[:4096])
    hash_old_times = _bench(_hash_old, n)
    _stats("Hash OLD (full tobytes)", hash_old_times)

    # 5b. Frame diff hash — wrong fix (3D memoryview slice != byte slice)
    def _hash_wrong():
        hash(frame.data[:4096].tobytes())
    hash_wrong_times = _bench(_hash_wrong, n)
    _stats("Hash WRONG (data[:])   ", hash_wrong_times)

    # 5c. Frame diff hash — CORRECT fix (ravel to 1D, then slice bytes)
    def _hash_fixed():
        hash(frame.ravel()[:4096].tobytes())
    hash_fixed_times = _bench(_hash_fixed, n)
    _stats("Hash FIXED (ravel)     ", hash_fixed_times)

    # 6a. Full grayscale conversion (full frame — worst case)
    import cv2
    gray_times = _bench(lambda: cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), n)
    _stats("BGR->Gray full frame   ", gray_times)

    # 6b. Grayscale on minimap ROI only (realistic hot-path size ~119x110)
    mini_roi = frame[27:137, 1740:1859]  # default minimap ROI
    gray_roi_times = _bench(lambda: cv2.cvtColor(mini_roi, cv2.COLOR_BGR2GRAY), n)
    _stats("BGR->Gray minimap ROI  ", gray_roi_times)

    # 8. Minimap palette quantize (hot path — called every radar read)
    from src.minimap_radar import _quantize_and_check  # type: ignore[import-untyped]
    quant_times = _bench(lambda: _quantize_and_check(mini_roi), n)
    _stats("Palette quantize+check ", quant_times)

    # 7. ROI-only crop (simulated)
    roi = frame[28:40, 12:781]  # typical HP bar
    roi_hp_times = _bench(lambda: hp_det.read_bars(frame), n)
    _stats("HP read (full frame)   ", roi_hp_times)

    # Summary
    total_avg = statistics.mean(capture_times) + statistics.mean(hp_times)
    print(f"\n  ===================================================")
    print(f"  END-TO-END (capture + HP/MP):  {total_avg:.1f}ms avg")
    if "detect_times" in dir() and detect_times:  # type: ignore[name-defined]
        total_combat = total_avg + statistics.mean(detect_times)  # type: ignore[name-defined]
        print(f"  WITH COMBAT:                   {total_combat:.1f}ms avg")
    print(f"  ===================================================\n")

    if hasattr(getter, "close"):
        getter.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
