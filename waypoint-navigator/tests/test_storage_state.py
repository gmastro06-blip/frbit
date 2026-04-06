"""Tests for storage_state.py — pure data model, no mocks needed."""
from __future__ import annotations

import time
import pytest

from src.storage_state import (
    ContainerWindow,
    StorageSurface,
    StorageState,
    StorageTransition,
)


# ---------------------------------------------------------------------------
# StorageSurface
# ---------------------------------------------------------------------------
class TestStorageSurfaceProperties:
    def test_depot_family_includes_depot_chest(self):
        assert StorageSurface.DEPOT_CHEST.is_depot_family

    def test_depot_family_includes_stash(self):
        assert StorageSurface.STASH.is_depot_family

    def test_depot_family_includes_inbox(self):
        assert StorageSurface.INBOX.is_depot_family

    def test_depot_family_includes_store_inbox(self):
        assert StorageSurface.STORE_INBOX.is_depot_family

    def test_depot_family_includes_manage_containers(self):
        assert StorageSurface.MANAGE_CONTAINERS.is_depot_family

    def test_inventory_not_depot_family(self):
        assert not StorageSurface.INVENTORY.is_depot_family

    def test_unknown_not_depot_family(self):
        assert not StorageSurface.UNKNOWN.is_depot_family

    def test_supports_stow_depot_chest(self):
        assert StorageSurface.DEPOT_CHEST.supports_stow

    def test_supports_stow_stash(self):
        assert StorageSurface.STASH.supports_stow

    def test_supports_stow_false_for_inbox(self):
        assert not StorageSurface.INBOX.supports_stow

    def test_is_read_only_inbox(self):
        assert StorageSurface.INBOX.is_read_only

    def test_is_read_only_store_inbox(self):
        assert StorageSurface.STORE_INBOX.is_read_only

    def test_depot_chest_not_read_only(self):
        assert not StorageSurface.DEPOT_CHEST.is_read_only

    def test_stash_not_read_only(self):
        assert not StorageSurface.STASH.is_read_only


# ---------------------------------------------------------------------------
# ContainerWindow
# ---------------------------------------------------------------------------
class TestContainerWindow:
    def _window(self, **kw):
        defaults = dict(roi=(100, 200, 300, 400), title="depot chest",
                        surface=StorageSurface.DEPOT_CHEST)
        defaults.update(kw)
        return ContainerWindow(**defaults)

    def test_x_y_w_h_properties(self):
        w = self._window(roi=(10, 20, 30, 40))
        assert w.x == 10
        assert w.y == 20
        assert w.w == 30
        assert w.h == 40

    def test_center(self):
        w = self._window(roi=(0, 0, 100, 200))
        assert w.center == (50, 100)

    def test_title_bar_roi_height_18(self):
        w = self._window(roi=(50, 60, 200, 300))
        tb = w.title_bar_roi
        assert tb == (50, 60, 200, 18)

    def test_default_confidence_is_1(self):
        w = self._window()
        assert w.confidence == 1.0


# ---------------------------------------------------------------------------
# StorageState
# ---------------------------------------------------------------------------
class TestStorageState:
    def _make(self, surface=StorageSurface.UNKNOWN, windows=None):
        return StorageState(surface=surface, open_windows=windows or [])

    def _window(self, surface):
        return ContainerWindow(
            roi=(0, 0, 100, 100), title=surface.value, surface=surface
        )

    def test_find_returns_matching_window(self):
        w = self._window(StorageSurface.STASH)
        state = self._make(windows=[w])
        assert state.find(StorageSurface.STASH) is w

    def test_find_returns_none_when_absent(self):
        state = self._make()
        assert state.find(StorageSurface.STASH) is None

    def test_has_true_when_present(self):
        w = self._window(StorageSurface.INBOX)
        state = self._make(windows=[w])
        assert state.has(StorageSurface.INBOX)

    def test_has_false_when_absent(self):
        state = self._make()
        assert not state.has(StorageSurface.DEPOT_CHEST)

    def test_is_depot_open_true_when_stash_visible(self):
        w = self._window(StorageSurface.STASH)
        state = self._make(windows=[w])
        assert state.is_depot_open

    def test_is_depot_open_false_when_only_inventory(self):
        w = self._window(StorageSurface.INVENTORY)
        state = self._make(windows=[w])
        assert not state.is_depot_open

    def test_is_stale_when_old(self):
        state = StorageState(
            surface=StorageSurface.UNKNOWN,
            detected_at=time.monotonic() - 10.0,
        )
        assert state.is_stale(max_age_s=1.0)

    def test_not_stale_when_fresh(self):
        state = StorageState(surface=StorageSurface.UNKNOWN)
        assert not state.is_stale(max_age_s=5.0)

    def test_age_s_increases_over_time(self):
        state = StorageState(
            surface=StorageSurface.UNKNOWN,
            detected_at=time.monotonic() - 2.0,
        )
        assert state.age_s >= 2.0

    def test_find_returns_first_match_when_multiple(self):
        w1 = ContainerWindow(roi=(0, 0, 10, 10), title="stash", surface=StorageSurface.STASH)
        w2 = ContainerWindow(roi=(100, 0, 10, 10), title="stash2", surface=StorageSurface.STASH)
        state = self._make(windows=[w1, w2])
        assert state.find(StorageSurface.STASH) is w1

    def test_empty_state_no_depot_open(self):
        state = self._make()
        assert not state.is_depot_open

    def test_frame_size_default(self):
        state = self._make()
        assert state.frame_size == (0, 0)


# ---------------------------------------------------------------------------
# StorageTransition
# ---------------------------------------------------------------------------
class TestStorageTransition:
    def test_fields(self):
        t = StorageTransition(
            from_surface=StorageSurface.DEPOT_CHEST,
            to_surface=StorageSurface.STASH,
            success=True,
            elapsed_s=0.45,
            note="ok",
        )
        assert t.from_surface == StorageSurface.DEPOT_CHEST
        assert t.to_surface == StorageSurface.STASH
        assert t.success is True
        assert t.elapsed_s == pytest.approx(0.45)
        assert t.note == "ok"

    def test_note_defaults_empty(self):
        t = StorageTransition(
            from_surface=StorageSurface.UNKNOWN,
            to_surface=StorageSurface.STASH,
            success=False,
            elapsed_s=1.0,
        )
        assert t.note == ""
