"""Tests for src/frame_quality.py — Fase 6.4 frame quality checker."""

from __future__ import annotations

import numpy as np
import pytest

from src.frame_quality import (
    FrameQuality,
    FrameQualityChecker,
    FrameQualityConfig,
)


@pytest.fixture()
def checker() -> FrameQualityChecker:
    return FrameQualityChecker()


# ===========================================================================
# FrameQualityConfig
# ===========================================================================


class TestFrameQualityConfig:
    def test_defaults(self):
        cfg = FrameQualityConfig()
        assert cfg.black_mean_threshold == 5.0
        assert cfg.blur_laplacian_threshold == 50.0
        assert cfg.expected_width == 0
        assert cfg.expected_height == 0

    def test_custom(self):
        cfg = FrameQualityConfig(black_mean_threshold=10.0, expected_width=1920)
        assert cfg.black_mean_threshold == 10.0
        assert cfg.expected_width == 1920


# ===========================================================================
# is_corrupt
# ===========================================================================


class TestIsCorrupt:
    def test_none_is_corrupt(self, checker):
        assert checker.is_corrupt(None) is True

    def test_empty_is_corrupt(self, checker):
        assert checker.is_corrupt(np.array([])) is True

    def test_1d_is_corrupt(self, checker):
        assert checker.is_corrupt(np.array([1, 2, 3])) is True

    def test_valid_frame_not_corrupt(self, checker):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        assert checker.is_corrupt(frame) is False


# ===========================================================================
# is_black
# ===========================================================================


class TestIsBlack:
    def test_all_zeros_is_black(self, checker):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        assert checker.is_black(frame) is True

    def test_bright_frame_not_black(self, checker):
        frame = np.full((100, 100, 3), 128, dtype=np.uint8)
        assert checker.is_black(frame) is False

    def test_near_black_with_low_brightness(self, checker):
        frame = np.full((100, 100, 3), 3, dtype=np.uint8)
        assert checker.is_black(frame) is True

    def test_custom_threshold(self):
        checker = FrameQualityChecker(FrameQualityConfig(black_mean_threshold=20.0))
        frame = np.full((100, 100, 3), 15, dtype=np.uint8)
        assert checker.is_black(frame) is True

    def test_none_is_black(self, checker):
        assert checker.is_black(None) is True

    def test_empty_is_black(self, checker):
        assert checker.is_black(np.array([])) is True


# ===========================================================================
# is_blurry
# ===========================================================================


class TestIsBlurry:
    def test_uniform_is_blurry(self, checker):
        """Uniform color has zero Laplacian variance → blurry."""
        frame = np.full((100, 100, 3), 128, dtype=np.uint8)
        assert checker.is_blurry(frame) is True

    def test_sharp_edges_not_blurry(self, checker):
        """Checkerboard pattern has high Laplacian variance."""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[::2, ::2] = 255
        frame[1::2, 1::2] = 255
        assert checker.is_blurry(frame) is False

    def test_grayscale_input(self, checker):
        """Should handle 2D grayscale frames."""
        gray = np.zeros((100, 100), dtype=np.uint8)
        gray[::2, ::2] = 255
        assert checker.is_blurry(gray) is False

    def test_none_is_blurry(self, checker):
        assert checker.is_blurry(None) is True


# ===========================================================================
# is_valid_resolution
# ===========================================================================


class TestIsValidResolution:
    def test_no_expectation_always_valid(self, checker):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        assert checker.is_valid_resolution(frame) is True

    def test_correct_resolution(self):
        checker = FrameQualityChecker(
            FrameQualityConfig(expected_width=1920, expected_height=1080)
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        assert checker.is_valid_resolution(frame) is True

    def test_wrong_width(self):
        checker = FrameQualityChecker(
            FrameQualityConfig(expected_width=1920, expected_height=1080)
        )
        frame = np.zeros((1080, 1280, 3), dtype=np.uint8)
        assert checker.is_valid_resolution(frame) is False

    def test_wrong_height(self):
        checker = FrameQualityChecker(
            FrameQualityConfig(expected_width=1920, expected_height=1080)
        )
        frame = np.zeros((720, 1920, 3), dtype=np.uint8)
        assert checker.is_valid_resolution(frame) is False

    def test_override_expected(self, checker):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        assert checker.is_valid_resolution(frame, 1280, 720) is True
        assert checker.is_valid_resolution(frame, 1920, 1080) is False

    def test_only_width_check(self):
        checker = FrameQualityChecker(FrameQualityConfig(expected_width=640))
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        assert checker.is_valid_resolution(frame) is True


# ===========================================================================
# check (combined)
# ===========================================================================


class TestCheck:
    def test_none_returns_corrupt(self, checker):
        assert checker.check(None) == FrameQuality.CORRUPT

    def test_empty_returns_corrupt(self, checker):
        assert checker.check(np.array([])) == FrameQuality.CORRUPT

    def test_black_frame(self, checker):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        assert checker.check(frame) == FrameQuality.BLACK

    def test_wrong_size(self):
        checker = FrameQualityChecker(
            FrameQualityConfig(expected_width=1920, expected_height=1080)
        )
        frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
        assert checker.check(frame) == FrameQuality.WRONG_SIZE

    def test_blurry_frame(self, checker):
        """Uniform non-black frame → blurry."""
        frame = np.full((100, 100, 3), 128, dtype=np.uint8)
        assert checker.check(frame) == FrameQuality.BLURRY

    def test_good_frame(self, checker):
        """Checkerboard has edges, not black → OK."""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[::2, ::2] = 255
        frame[1::2, 1::2] = 255
        assert checker.check(frame) == FrameQuality.OK


# ===========================================================================
# diagnostics
# ===========================================================================


class TestDiagnostics:
    def test_none_diagnostics(self, checker):
        d = checker.diagnostics(None)
        assert d["quality"] == "CORRUPT"
        assert d["is_corrupt"] is True

    def test_normal_frame_diagnostics(self, checker):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[::2, ::2] = 255
        d = checker.diagnostics(frame)
        assert d["width"] == 100
        assert d["height"] == 100
        assert "mean_brightness" in d
        assert "laplacian_variance" in d
        assert d["quality"] == "OK"

    def test_diagnostics_keys(self, checker):
        frame = np.full((50, 50, 3), 128, dtype=np.uint8)
        d = checker.diagnostics(frame)
        expected_keys = {
            "quality", "width", "height", "mean_brightness",
            "laplacian_variance", "is_corrupt", "is_black",
            "is_blurry", "is_valid_resolution",
        }
        assert expected_keys == set(d.keys())


# ===========================================================================
# FrameQuality enum
# ===========================================================================


class TestFrameQualityEnum:
    def test_values_exist(self):
        assert FrameQuality.OK
        assert FrameQuality.BLACK
        assert FrameQuality.BLURRY
        assert FrameQuality.WRONG_SIZE
        assert FrameQuality.CORRUPT

    def test_all_distinct(self):
        vals = [FrameQuality.OK, FrameQuality.BLACK, FrameQuality.BLURRY,
                FrameQuality.WRONG_SIZE, FrameQuality.CORRUPT]
        assert len(set(vals)) == 5


# ===========================================================================
# config property
# ===========================================================================


class TestConfigProperty:
    def test_config_accessible(self, checker):
        assert checker.config is not None
        assert isinstance(checker.config, FrameQualityConfig)
