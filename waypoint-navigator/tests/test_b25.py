"""
test_b25 — BattleDetector: non-existent templates directory.

Covers the case where ``templates_dir`` points to a path that does not
exist yet.  ``_load_templates()`` must create the directory, load zero
templates, and ``detect()`` / ``detect_auto()`` must return ``[]``
without raising.

Distinct from ``test_combat.py::TestBattleDetectorNoTemplates`` which
passes a ``tmp_path`` that already exists on disk.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.combat_manager import BattleDetector, CombatConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _frame(w: int = 320, h: int = 240) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _cfg(templates_dir: Path | str, **kwargs) -> CombatConfig:
    return CombatConfig(
        templates_dir=str(templates_dir),
        battle_list_roi=[0, 0, 320, 240],
        ocr_detection=False,
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSlotMatchesNoTemplatesDir:
    """BattleDetector initialised with a templates_dir that does not exist."""

    def test_slot_matches_no_templates_dir(self, tmp_path: Path) -> None:
        """detect() returns [] when templates_dir does not exist on disk."""
        nonexistent = tmp_path / "missing_dir"
        assert not nonexistent.exists()

        cfg = _cfg(nonexistent)
        det = BattleDetector(cfg)

        # _load_templates creates the sub-directory but finds nothing to load
        assert det.template_count == 0
        assert not det.has_templates

        result = det.detect(_frame())
        assert result == []

    def test_detect_auto_no_templates_dir(self, tmp_path: Path) -> None:
        """detect_auto() returns [] when no templates and ocr_detection=False."""
        cfg = _cfg(tmp_path / "also_missing")
        det = BattleDetector(cfg)

        assert det.detect_auto(_frame()) == []

    def test_monsters_subdir_created(self, tmp_path: Path) -> None:
        """_load_templates creates templates_dir/monsters/ when absent."""
        base = tmp_path / "new_templates"
        assert not base.exists()

        BattleDetector(_cfg(base))

        assert (base / "monsters").is_dir()

    def test_reload_from_nonexistent_dir(self, tmp_path: Path) -> None:
        """reload() does not raise when templates_dir was initially missing."""
        cfg = _cfg(tmp_path / "reload_test")
        det = BattleDetector(cfg)
        det.reload()  # must not raise
        assert det.template_count == 0
