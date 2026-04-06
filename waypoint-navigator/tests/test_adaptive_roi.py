"""Tests for AdaptiveROIDetector (Fase 6.5)."""

from __future__ import annotations

from typing import List, Optional
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from src.adaptive_roi import (
    AdaptiveROIConfig,
    AdaptiveROIDetector,
    AnchorTemplate,
    DetectedROI,
    load_anchor,
)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _make_frame(w: int = 400, h: int = 300) -> np.ndarray:
    """Create a dark test frame with some structure."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    # Add a recognisable pattern in a known location
    cv2.rectangle(frame, (100, 50), (140, 70), (0, 255, 0), -1)
    cv2.rectangle(frame, (100, 50), (140, 70), (255, 255, 255), 1)
    return frame


def _make_template_from_frame(frame: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    """Extract a template from a frame region."""
    return frame[y : y + h, x : x + w].copy()


# ── TestAnchorTemplate ───────────────────────────────────────────────────────
class TestAnchorTemplate:
    def test_defaults(self) -> None:
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        a = AnchorTemplate(name="test", image=img)
        assert a.name == "test"
        assert a.offset == (0, 0)
        assert a.expected_size == (0, 0)
        assert a.confidence == 0.70

    def test_custom(self) -> None:
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        a = AnchorTemplate(
            name="hp",
            image=img,
            offset=(5, 3),
            expected_size=(200, 12),
            confidence=0.85,
        )
        assert a.offset == (5, 3)
        assert a.expected_size == (200, 12)
        assert a.confidence == 0.85


# ── TestAdaptiveROIConfig ────────────────────────────────────────────────────
class TestAdaptiveROIConfig:
    def test_defaults(self) -> None:
        cfg = AdaptiveROIConfig()
        assert cfg.reference_width == 1920
        assert cfg.reference_height == 1080
        assert cfg.scale_tolerance == 0.15
        assert cfg.cache_hits == 10

    def test_custom(self) -> None:
        cfg = AdaptiveROIConfig(reference_width=1280, reference_height=720, cache_hits=5)
        assert cfg.reference_width == 1280
        assert cfg.cache_hits == 5


# ── TestDetectedROI ──────────────────────────────────────────────────────────
class TestDetectedROI:
    def test_properties(self) -> None:
        d = DetectedROI(name="hp", roi=[10, 20, 200, 15], confidence=0.95, anchor_pos=(10, 20))
        assert d.x == 10
        assert d.y == 20
        assert d.w == 200
        assert d.h == 15

    def test_crop(self) -> None:
        frame = np.ones((100, 200, 3), dtype=np.uint8) * 128
        frame[20:35, 10:50] = 255  # bright region
        d = DetectedROI(name="hp", roi=[10, 20, 40, 15], confidence=0.9, anchor_pos=(10, 20))
        crop = d.crop(frame)
        assert crop.shape == (15, 40, 3)
        assert crop.mean() == 255


# ── TestAnchorManagement ─────────────────────────────────────────────────────
class TestAnchorManagement:
    def test_register_single(self) -> None:
        det = AdaptiveROIDetector()
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        det.register_anchor(AnchorTemplate("a", img))
        assert det.anchor_count == 1
        assert det.anchor_names == ["a"]

    def test_register_multiple(self) -> None:
        det = AdaptiveROIDetector()
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        det.register_anchors([
            AnchorTemplate("a", img),
            AnchorTemplate("b", img),
            AnchorTemplate("c", img),
        ])
        assert det.anchor_count == 3
        assert det.anchor_names == ["a", "b", "c"]


# ── TestDetection ────────────────────────────────────────────────────────────
class TestDetection:
    def test_detect_known_pattern(self) -> None:
        """Template extracted from frame should match perfectly."""
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        anchor = AnchorTemplate(
            name="green_box",
            image=tmpl,
            offset=(0, 0),
            expected_size=(45, 25),
            confidence=0.90,
        )
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        results = det.detect(frame)
        assert "green_box" in results
        roi = results["green_box"]
        # Match should be near (98, 48)
        assert abs(roi.x - 98) <= 2
        assert abs(roi.y - 48) <= 2
        assert roi.confidence >= 0.90

    def test_detect_no_match(self) -> None:
        """Random template should not match a black frame."""
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        tmpl = np.random.randint(50, 200, (20, 20, 3), dtype=np.uint8)
        anchor = AnchorTemplate(name="random", image=tmpl, confidence=0.95)
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        results = det.detect(frame)
        assert "random" not in results

    def test_detect_empty_frame(self) -> None:
        det = AdaptiveROIDetector()
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        det.register_anchor(AnchorTemplate("a", img))
        results = det.detect(np.empty(0))
        assert results == {}

    def test_detect_none_frame(self) -> None:
        det = AdaptiveROIDetector()
        results = det.detect(None)  # type: ignore[arg-type]
        assert results == {}

    def test_detect_with_offset(self) -> None:
        """Offset shifts ROI origin from anchor match position."""
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        anchor = AnchorTemplate(
            name="shifted",
            image=tmpl,
            offset=(10, 5),
            expected_size=(30, 10),
            confidence=0.85,
        )
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        results = det.detect(frame)
        assert "shifted" in results
        roi = results["shifted"]
        # Anchor matches near (98,48), offset adds (10,5), so ROI near (108, 53)
        assert abs(roi.x - 108) <= 2
        assert abs(roi.y - 53) <= 2

    def test_template_larger_than_frame(self) -> None:
        """Template bigger than frame should gracefully return nothing."""
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        tmpl = np.zeros((50, 50, 3), dtype=np.uint8)
        anchor = AnchorTemplate(name="big", image=tmpl, confidence=0.5)
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        results = det.detect(frame)
        assert "big" not in results

    def test_grayscale_template(self) -> None:
        """Grayscale template should still work."""
        frame = _make_frame()
        # Extract as grayscale
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        tmpl = gray_frame[48:73, 98:143].copy()
        anchor = AnchorTemplate(
            name="gray_box",
            image=tmpl,
            expected_size=(45, 25),
            confidence=0.85,
        )
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        results = det.detect(frame)
        assert "gray_box" in results


# ── TestCache ────────────────────────────────────────────────────────────────
class TestCache:
    def test_cache_populated_after_detect(self) -> None:
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        anchor = AnchorTemplate("box", tmpl, confidence=0.85, expected_size=(45, 25))
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        det.detect(frame)
        assert det.get_roi("box") is not None

    def test_get_roi_list(self) -> None:
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        anchor = AnchorTemplate("box", tmpl, confidence=0.85, expected_size=(45, 25))
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        det.detect(frame)
        roi_list = det.get_roi_list("box")
        assert roi_list is not None
        assert len(roi_list) == 4

    def test_get_roi_unknown_returns_none(self) -> None:
        det = AdaptiveROIDetector()
        assert det.get_roi("unknown") is None
        assert det.get_roi_list("unknown") is None

    def test_cache_hit_count(self) -> None:
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        anchor = AnchorTemplate("box", tmpl, confidence=0.85, expected_size=(45, 25))
        det = AdaptiveROIDetector(AdaptiveROIConfig(cache_hits=3))
        det.register_anchor(anchor)
        # Detect same frame multiple times
        for _ in range(5):
            det.detect(frame)
        assert det.cache_hit_count >= 4  # first detect sets cache, subsequent are hits

    def test_detect_cached_skips_when_stable(self) -> None:
        """After enough cache hits, detect_cached returns cached results."""
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        anchor = AnchorTemplate("box", tmpl, confidence=0.85, expected_size=(45, 25))
        cfg = AdaptiveROIConfig(cache_hits=2)
        det = AdaptiveROIDetector(cfg)
        det.register_anchor(anchor)

        # Build up cache hits
        for _ in range(3):
            det.detect(frame)

        # detect_cached should return cached (skipping re-scan)
        results = det.detect_cached(frame)
        assert "box" in results

    def test_clear_cache(self) -> None:
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        anchor = AnchorTemplate("box", tmpl, confidence=0.85, expected_size=(45, 25))
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        det.detect(frame)
        assert det.cache_hit_count >= 0
        det.clear_cache()
        assert det.cache_hit_count == 0
        assert det.get_roi("box") is None


# ── TestScale ────────────────────────────────────────────────────────────────
class TestScale:
    def test_compute_scale_1080p(self) -> None:
        det = AdaptiveROIDetector()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        sx, sy = det.compute_scale(frame)
        assert sx == pytest.approx(1.0)
        assert sy == pytest.approx(1.0)

    def test_compute_scale_720p(self) -> None:
        det = AdaptiveROIDetector()
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        sx, sy = det.compute_scale(frame)
        assert sx == pytest.approx(1280 / 1920)
        assert sy == pytest.approx(720 / 1080)

    def test_scale_roi(self) -> None:
        det = AdaptiveROIDetector()
        roi = [100, 50, 200, 30]
        scaled = det.scale_roi(roi, 0.5, 0.5)
        assert scaled == [50, 25, 100, 15]

    def test_scale_roi_identity(self) -> None:
        det = AdaptiveROIDetector()
        roi = [100, 50, 200, 30]
        scaled = det.scale_roi(roi, 1.0, 1.0)
        assert scaled == roi


# ── TestStatsSnapshot ────────────────────────────────────────────────────────
class TestStatsSnapshot:
    def test_empty(self) -> None:
        det = AdaptiveROIDetector()
        snap = det.stats_snapshot()
        assert snap["anchor_count"] == 0
        assert snap["cached_rois"] == {}
        assert snap["cache_hits"] == 0

    def test_after_detection(self) -> None:
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        anchor = AnchorTemplate("box", tmpl, confidence=0.85, expected_size=(45, 25))
        det = AdaptiveROIDetector()
        det.register_anchor(anchor)
        det.detect(frame)
        snap = det.stats_snapshot()
        assert snap["anchor_count"] == 1
        assert "box" in snap["cached_rois"]


# ── TestLoadAnchor ───────────────────────────────────────────────────────────
class TestLoadAnchor:
    def test_load_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            load_anchor(tmp_path / "nonexistent.png", "test")

    def test_load_valid_file(self, tmp_path) -> None:
        img = np.zeros((20, 30, 3), dtype=np.uint8)
        path = tmp_path / "anchor.png"
        cv2.imwrite(str(path), img)
        anchor = load_anchor(path, "test_anchor", offset=(5, 5), expected_size=(100, 50))
        assert anchor.name == "test_anchor"
        assert anchor.image.shape[:2] == (20, 30)
        assert anchor.offset == (5, 5)
        assert anchor.expected_size == (100, 50)


# ── TestConfig ───────────────────────────────────────────────────────────────
class TestConfig:
    def test_config_accessible(self) -> None:
        cfg = AdaptiveROIConfig(reference_width=1280)
        det = AdaptiveROIDetector(cfg)
        assert det.config.reference_width == 1280


# ── TestAutoLoadAndFallback ─────────────────────────────────────────────────
class TestAutoLoadAndFallback:
    def test_load_anchors_from_dir_returns_zero_when_missing(self, tmp_path) -> None:
        det = AdaptiveROIDetector()

        with patch("src.adaptive_roi._TEMPLATES_DIR", tmp_path), \
             patch("src.adaptive_roi._ANCHORS_META", tmp_path / "anchors" / "anchors_meta.json"):
            assert det.load_anchors_from_dir() == 0
            assert det.anchor_count == 0

    def test_load_anchors_from_dir_loads_png_and_metadata(self, tmp_path) -> None:
        anchors_dir = tmp_path / "anchors"
        anchors_dir.mkdir()
        image = np.zeros((12, 8, 3), dtype=np.uint8)
        image[:, :] = (10, 20, 30)
        path = anchors_dir / "hp_left.png"
        assert cv2.imwrite(str(path), image)
        meta_path = anchors_dir / "anchors_meta.json"
        meta_path.write_text(
            '{"hp_left": {"offset": [3, 4], "expected_size": [90, 11], "confidence": 0.82}}',
            encoding="utf-8",
        )

        det = AdaptiveROIDetector()
        with patch("src.adaptive_roi._TEMPLATES_DIR", tmp_path), \
             patch("src.adaptive_roi._ANCHORS_META", meta_path):
            loaded = det.load_anchors_from_dir()

        assert loaded == 1
        assert det.anchor_names == ["hp_left"]
        anchor = det._anchors[0]
        assert anchor.offset == (3, 4)
        assert anchor.expected_size == (90, 11)
        assert anchor.confidence == pytest.approx(0.82)

    def test_load_anchors_from_dir_handles_invalid_metadata(self, tmp_path) -> None:
        anchors_dir = tmp_path / "anchors"
        anchors_dir.mkdir()
        image = np.ones((10, 10, 3), dtype=np.uint8)
        assert cv2.imwrite(str(anchors_dir / "minimap.jpg"), image)
        meta_path = anchors_dir / "anchors_meta.json"
        meta_path.write_text("{invalid json", encoding="utf-8")

        det = AdaptiveROIDetector()
        with patch("src.adaptive_roi._TEMPLATES_DIR", tmp_path), \
             patch("src.adaptive_roi._ANCHORS_META", meta_path):
            assert det.load_anchors_from_dir() == 1

        anchor = det._anchors[0]
        assert anchor.offset == (0, 0)
        assert anchor.expected_size == (0, 0)
        assert anchor.confidence == pytest.approx(0.70)

    def test_get_proportional_roi_unknown_returns_none(self) -> None:
        det = AdaptiveROIDetector()
        assert det.get_proportional_roi("unknown", 1920, 1080) is None

    def test_detect_or_fallback_returns_scaled_reference_rois(self) -> None:
        det = AdaptiveROIDetector()
        frame = np.zeros((540, 960, 3), dtype=np.uint8)

        rois = det.detect_or_fallback(frame)

        assert rois["hp_bar"] == [6, 14, 384, 6]
        assert rois["minimap"] == [875, 17, 56, 57]

    def test_detect_or_fallback_anchor_overrides_scaled_roi(self) -> None:
        frame = _make_frame(1920, 1080)
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        det = AdaptiveROIDetector()
        det.register_anchor(
            AnchorTemplate(
                "minimap",
                tmpl,
                offset=(5, 6),
                expected_size=(40, 20),
                confidence=0.85,
            )
        )

        rois = det.detect_or_fallback(frame)

        assert rois["minimap"] != [1750, 35, 113, 115]
        assert abs(rois["minimap"][0] - 103) <= 2
        assert abs(rois["minimap"][1] - 54) <= 2
        assert rois["minimap"][2:] == [40, 20]


# ── TestInternalBranches ────────────────────────────────────────────────────
class TestInternalBranches:
    def test_match_anchor_clamps_negative_offset_to_frame_bounds(self) -> None:
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        det = AdaptiveROIDetector()
        anchor = AnchorTemplate(
            name="clamped",
            image=tmpl,
            offset=(-200, -200),
            expected_size=(400, 400),
            confidence=0.85,
        )

        result = det._match_anchor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), anchor, frame.shape)

        assert result is not None
        assert result.roi == [0, 0, 400, 300]

    def test_match_anchor_returns_none_when_clamped_size_is_empty(self) -> None:
        det = AdaptiveROIDetector()
        gray = np.zeros((40, 40), dtype=np.uint8)
        tmpl = np.zeros((10, 10), dtype=np.uint8)
        anchor = AnchorTemplate(
            name="empty",
            image=tmpl,
            offset=(50, 0),
            expected_size=(10, 10),
            confidence=0.5,
        )

        with patch("cv2.matchTemplate", return_value=np.ones((1, 1), dtype=np.float32)), \
             patch("cv2.minMaxLoc", return_value=(0.0, 1.0, (0, 0), (0, 0))):
            assert det._match_anchor(gray, anchor, gray.shape) is None

    def test_cached_rois_returns_copy(self) -> None:
        frame = _make_frame()
        tmpl = _make_template_from_frame(frame, 98, 48, 45, 25)
        det = AdaptiveROIDetector()
        det.register_anchor(AnchorTemplate("box", tmpl, confidence=0.85, expected_size=(45, 25)))
        det.detect(frame)

        cached = det.cached_rois
        cached.clear()

        assert "box" in det.cached_rois
