from __future__ import annotations

import argparse
import gc
import os
import sys
from pathlib import Path

import psutil
import pytest


def rss_mb() -> float:
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024


def run_file(test_file: str) -> int:
    args = [
        test_file,
        "-q",
        "-o",
        "addopts=-q --tb=short -p no:warnings --cov=src --cov-report= --cov-fail-under=0",
    ]
    return int(pytest.main(args))


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile pytest RSS growth per file")
    parser.add_argument("files", nargs="+", help="Ordered test files to run")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)

    print(f"PID {os.getpid()} initial_rss_mb={rss_mb():.1f}", flush=True)
    max_rss = rss_mb()
    worst_file = ""

    for index, test_file in enumerate(args.files, start=1):
        before = rss_mb()
        exit_code = run_file(test_file)
        gc.collect()
        after = rss_mb()
        delta = after - before
        if after > max_rss:
            max_rss = after
            worst_file = test_file
        print(
            f"{index:02d} exit={exit_code} before={before:.1f}MB after={after:.1f}MB delta={delta:+.1f}MB file={test_file}",
            flush=True,
        )
        if exit_code != 0:
            print(f"stopping_on_failure file={test_file}", flush=True)
            return exit_code

    print(f"peak_rss_mb={max_rss:.1f} peak_file={worst_file}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())