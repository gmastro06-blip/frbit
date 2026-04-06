"""Tests for storage_detector.py — uses mock frames, no EasyOCR required."""
from __future__ import annotations

import numpy as np
import pytest

from src.storage_detector import StorageDetector, StorageDetectorConfig, classify_title
from src.storage_state import StorageSurface


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blank_frame(w: int = 1920, h: int = 1080) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _frame_with_rect(x: int, y: int, w: int, h: int,
                     bg: int = 40, border: int = 180,
                     frame_w: int = 1920, frame_h: int = 1080) -> np.ndarray:
    """Draw a filled rectangle with a bright border (simulates container window)."""
    frame = np.full((frame_h, frame_w, 3), bg, dtype=np.uint8)
    frame[y:y + h, x:x + w] = bg
    frame[y, x:x + w] = border       # top edge
    frame[y + h - 1, x:x + w] = border  # bottom edge
    frame[y:y + h, x] = border       # left edge
    frame[y:y + h, x + w - 1] = border  # right edge
    return frame


# ---------------------------------------------------------------------------
# classify_title
# ---------------------------------------------------------------------------
class TestClassifyTitle:
    @pytest.mark.parametrize("text,expected", [
        ("depot chest", StorageSurface.DEPOT_CHEST),
        ("Depot Locker", StorageSurface.DEPOT_CHEST),
        ("stash", StorageSurface.STASH),
        ("My Stash", StorageSurface.STASH),
        ("inbox", StorageSurface.INBOX),
        ("Inbox", StorageSurface.INBOX),
        ("store inbox", StorageSurface.STORE_INBOX),
        ("Store Inbox", StorageSurface.STORE_INBOX),
        ("manage containers", StorageSurface.MANAGE_CONTAINERS),
        ("Manage Container", StorageSurface.MANAGE_CONTAINERS),
        ("backpack", StorageSurface.UNKNOWN),
        ("", StorageSurface.UNKNOWN),
        ("loot bag", StorageSurface.UNKNOWN),
    ])
    def test_patterns(self, text, expected):
        assert classify_title(text) == expected

    def test_case_insensitive(self):
        assert classify_title("STASH") == StorageSurface.STASH

    def test_store_inbox_before_inbox(self):
        # "store inbox" should match STORE_INBOX, not INBOX
        assert classify_title("store inbox") == StorageSurface.STORE_INBOX

    def test_partial_match_depot(self):
        assert classify_title("my depot chest") == StorageSurface.DEPOT_CHEST


# ---------------------------------------------------------------------------
# StorageDetector — unit tests with mock frames (no OCR)
# ---------------------------------------------------------------------------
class TestStorageDetectorConfig:
    def test_defaults(self):
        cfg = StorageDetectorConfig()
        assert cfg.ref_width == 1920
        assert cfg.ref_height == 1080
        assert 0.0 < cfg.ocr_min_confidence < 1.0
        assert cfg.max_containers >= 1
        assert 0.0 < cfg.search_right_fraction <= 1.0

    def test_state_ttl_positive(self):
        cfg = StorageDetectorConfig()
        assert cfg.state_ttl_s > 0


class TestStorageDetectorBasic:
    def _detector(self, **kw):
        cfg = StorageDetectorConfig(**kw)
        return StorageDetector(config=cfg)

    def test_returns_unknown_on_blank_frame(self):
        det = self._detector()
        state = det.detect(_blank_frame())
        assert state.surface == StorageSurface.UNKNOWN

    def test_returns_unknown_when_frame_is_none(self):
        det = self._detector()
        state = det.detect(None)
        assert state.surface == StorageSurface.UNKNOWN
        assert state.open_windows == []

    def test_frame_size_stored(self):
        det = self._detector()
        frame = _blank_frame(1280, 720)
        state = det.detect(frame)
        assert state.frame_size == (1280, 720)

    def test_caches_state_within_ttl(self):
        det = self._detector(state_ttl_s=10.0)
        frame = _blank_frame()
        state1 = det.detect(frame)
        state2 = det.detect(frame)
        assert state1 is state2

    def test_invalidate_clears_cache(self):
        det = self._detector(state_ttl_s=10.0)
        frame = _blank_frame()
        state1 = det.detect(frame)
        det.invalidate()
        state2 = det.detect(frame)
        assert state1 is not state2

    def test_frame_getter_called_when_no_frame(self):
        det = self._detector()
        frame = _blank_frame()
        calls = []

        def getter():
            calls.append(1)
            return frame

        det.set_frame_getter(getter)
        det.detect()
        assert len(calls) == 1

    def test_detect_with_rect_returns_state(self):
        det = self._detector()
        frame = _frame_with_rect(1400, 100, 300, 400)
        state = det.detect(frame)
        # We expect at most some windows; the key assertion is it doesn't crash
        # and returns a valid StorageState
        assert state is not None
        assert isinstance(state.open_windows, list)


class TestStorageDetectorTemplates:
    def test_register_template_and_match(self):
        det = StorageDetector()
        # Create a simple white-on-dark title sprite for STASH
        title_sprite = np.zeros((18, 80, 3), dtype=np.uint8)
        det.register_title_template("stash", StorageSurface.STASH, title_sprite)
        assert "stash" in det._title_templates

    def test_no_match_below_threshold(self):
        det = StorageDetector()
        # Completely random noise template will not match a blank title bar
        noise = np.random.randint(0, 255, (18, 80, 3), dtype=np.uint8)
        det.register_title_template("noise", StorageSurface.STASH, noise)
        blank_title = np.zeros((18, 80, 3), dtype=np.uint8)
        surface, conf = det._match_template(blank_title)
        assert surface == StorageSurface.UNKNOWN or conf < 0.65


# ---------------------------------------------------------------------------
# Primary surface selection
# ---------------------------------------------------------------------------
class TestPrimarySurface:
    def _window(self, surface):
        from src.storage_state import ContainerWindow
        return ContainerWindow(roi=(0, 0, 100, 100), title=surface.value, surface=surface)

    def test_manage_containers_wins(self):
        from src.storage_detector import StorageDetector
        windows = [
            self._window(StorageSurface.DEPOT_CHEST),
            self._window(StorageSurface.MANAGE_CONTAINERS),
            self._window(StorageSurface.STASH),
        ]
        result = StorageDetector._primary_surface(windows)
        assert result == StorageSurface.MANAGE_CONTAINERS

    def test_stash_over_depot_chest(self):
        from src.storage_detector import StorageDetector
        windows = [
            self._window(StorageSurface.DEPOT_CHEST),
            self._window(StorageSurface.STASH),
        ]
        result = StorageDetector._primary_surface(windows)
        assert result == StorageSurface.STASH

    def test_store_inbox_over_inbox(self):
        from src.storage_detector import StorageDetector
        windows = [
            self._window(StorageSurface.INBOX),
            self._window(StorageSurface.STORE_INBOX),
        ]
        result = StorageDetector._primary_surface(windows)
        assert result == StorageSurface.STORE_INBOX

    def test_empty_list_returns_unknown(self):
        from src.storage_detector import StorageDetector
        assert StorageDetector._primary_surface([]) == StorageSurface.UNKNOWN

    def test_inventory_only(self):
        from src.storage_detector import StorageDetector
        windows = [self._window(StorageSurface.INVENTORY)]
        result = StorageDetector._primary_surface(windows)
        assert result == StorageSurface.INVENTORY


# ---------------------------------------------------------------------------
# IOU deduplication
# ---------------------------------------------------------------------------
class TestCannyBoxDedup:
    def test_overlapping_boxes_deduplicated(self):
        from src.storage_detector import StorageDetector
        det = StorageDetector()
        existing = [(100, 100, 200, 300)]
        # Near-identical box: should be considered overlap
        assert det._overlaps_existing((105, 105, 195, 290), existing, iou_threshold=0.4)

    def test_non_overlapping_boxes_kept(self):
        from src.storage_detector import StorageDetector
        det = StorageDetector()
        existing = [(100, 100, 200, 200)]
        assert not det._overlaps_existing((400, 100, 200, 200), existing, iou_threshold=0.4)
