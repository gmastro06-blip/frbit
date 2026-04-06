"""Tests for src/image_processing.py — ImageProcessor."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from src.image_processing import ImageProcessor


def _bgr(h: int = 20, w: int = 30) -> np.ndarray:
    return np.full((h, w, 3), 128, dtype=np.uint8)


def _bgra(h: int = 20, w: int = 30) -> np.ndarray:
    return np.full((h, w, 4), 200, dtype=np.uint8)


def _gray(h: int = 20, w: int = 30) -> np.ndarray:
    return np.full((h, w), 128, dtype=np.uint8)


class TestConstruction:

    def test_default_scale(self):
        ip = ImageProcessor()
        assert ip.scale == 4

    def test_custom_scale(self):
        ip = ImageProcessor(scale=2)
        assert ip.scale == 2


class TestPreprocess:

    def test_bgr_input_returns_ndarray(self):
        ip = ImageProcessor()
        result = ip.preprocess(_bgr())
        assert isinstance(result, np.ndarray)

    def test_bgra_input_returns_ndarray(self):
        ip = ImageProcessor()
        result = ip.preprocess(_bgra())
        assert isinstance(result, np.ndarray)

    def test_gray_input_returns_ndarray(self):
        ip = ImageProcessor()
        result = ip.preprocess(_gray())
        assert isinstance(result, np.ndarray)

    def test_output_is_scaled(self):
        ip = ImageProcessor(scale=2)
        img = _bgr(10, 15)
        result = ip.preprocess(img)
        assert result.shape[0] == 10 * 2
        assert result.shape[1] == 15 * 2

    def test_output_is_binary(self):
        ip = ImageProcessor()
        result = ip.preprocess(_bgr())
        unique = set(result.flatten().tolist())
        assert unique <= {0, 255}

    def test_scale_4_produces_correct_size(self):
        ip = ImageProcessor(scale=4)
        img = _bgr(5, 8)
        result = ip.preprocess(img)
        assert result.shape[0] == 20
        assert result.shape[1] == 32


class TestDebugSave:

    def test_frozen_executable_skips_write(self, tmp_path):
        """In frozen mode, debug_save does nothing."""
        ip = ImageProcessor()
        img = _bgr()
        with patch.object(sys, 'frozen', True, create=True):
            ip.debug_save(img, str(tmp_path / "out.png"))
        assert not (tmp_path / "out.png").exists()

    def test_non_frozen_writes_file(self, tmp_path):
        """In non-frozen mode, debug_save writes the image."""
        ip = ImageProcessor()
        img = ip.preprocess(_bgr(10, 10))
        out = str(tmp_path / "debug.png")
        ip.debug_save(img, out)
        assert Path(out).exists()
