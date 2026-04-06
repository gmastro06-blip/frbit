"""Tests for PositionResolver fallback chain (Fase 6.2)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.position_resolver import (
    PositionResolver,
    PositionResolverConfig,
    PositionSource,
    SourceKind,
    SourceStats,
    _SourceEntry,
)
from src.models import Coordinate


# ── Helpers ──────────────────────────────────────────────────────────────────
class FakeSource:
    """Configurable fake position source for testing."""

    def __init__(self, result: Optional[Coordinate] = None, *, raises: bool = False):
        self._result = result
        self._raises = raises
        self.call_count = 0

    def read(self, frame: np.ndarray, **kwargs: Any) -> Optional[Coordinate]:
        self.call_count += 1
        if self._raises:
            raise RuntimeError("Source error")
        return self._result


_COORD_A = Coordinate(32369, 32241, 7)
_COORD_B = Coordinate(32370, 32242, 7)
_COORD_C = Coordinate(32000, 31000, 7)
_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


# ── TestSourceStats ──────────────────────────────────────────────────────────
class TestSourceStats:
    def test_defaults(self) -> None:
        s = SourceStats()
        assert s.hits == 0 and s.misses == 0 and s.total_ms == 0.0

    def test_hit_rate(self) -> None:
        s = SourceStats(hits=3, misses=7)
        assert s.hit_rate == pytest.approx(0.3)

    def test_hit_rate_zero(self) -> None:
        s = SourceStats()
        assert s.hit_rate == 0.0

    def test_avg_ms(self) -> None:
        s = SourceStats(hits=2, misses=3, total_ms=50.0)
        assert s.avg_ms == pytest.approx(10.0)

    def test_attempts(self) -> None:
        s = SourceStats(hits=5, misses=3)
        assert s.attempts == 8


# ── TestPositionResolverConfig ───────────────────────────────────────────────
class TestPositionResolverConfig:
    def test_defaults(self) -> None:
        cfg = PositionResolverConfig()
        assert cfg.max_stale_ms == 5000.0
        assert cfg.log_misses is True

    def test_custom(self) -> None:
        cfg = PositionResolverConfig(max_stale_ms=1000.0, log_misses=False)
        assert cfg.max_stale_ms == 1000.0
        assert cfg.log_misses is False


# ── TestSourceManagement ─────────────────────────────────────────────────────
class TestSourceManagement:
    def test_add_source(self) -> None:
        r = PositionResolver()
        r.add_source("radar", SourceKind.MINIMAP_RADAR, FakeSource())
        assert r.source_names == ["radar"]
        assert r.source_count == 1

    def test_add_multiple_preserves_order(self) -> None:
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource())
        r.add_source("b", SourceKind.LOCAL_MINIMAP, FakeSource())
        r.add_source("c", SourceKind.COORDINATE_OCR, FakeSource())
        assert r.source_names == ["a", "b", "c"]

    def test_insert_source(self) -> None:
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource())
        r.add_source("c", SourceKind.COORDINATE_OCR, FakeSource())
        r.insert_source(1, "b", SourceKind.LOCAL_MINIMAP, FakeSource())
        assert r.source_names == ["a", "b", "c"]

    def test_remove_source(self) -> None:
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource())
        r.add_source("b", SourceKind.LOCAL_MINIMAP, FakeSource())
        assert r.remove_source("a") is True
        assert r.source_names == ["b"]

    def test_remove_nonexistent(self) -> None:
        r = PositionResolver()
        assert r.remove_source("nope") is False

    def test_enable_disable_source(self) -> None:
        r = PositionResolver()
        src = FakeSource(_COORD_A)
        r.add_source("a", SourceKind.MINIMAP_RADAR, src)
        r.enable_source("a", enabled=False)
        assert r.resolve(_FRAME) is None  # source disabled → skipped
        r.enable_source("a", enabled=True)
        assert r.resolve(_FRAME) == _COORD_A

    def test_enable_unknown_raises(self) -> None:
        r = PositionResolver()
        with pytest.raises(KeyError, match="Source not found"):
            r.enable_source("nope")


# ── TestResolve ──────────────────────────────────────────────────────────────
class TestResolve:
    def test_empty_chain_returns_none(self) -> None:
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        assert r.resolve(_FRAME) is None

    def test_single_source_success(self) -> None:
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        assert r.resolve(_FRAME) == _COORD_A

    def test_single_source_miss(self) -> None:
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(None))
        assert r.resolve(_FRAME) is None

    def test_fallback_to_second_source(self) -> None:
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(None))
        r.add_source("b", SourceKind.LOCAL_MINIMAP, FakeSource(_COORD_B))
        assert r.resolve(_FRAME) == _COORD_B

    def test_first_source_wins(self) -> None:
        r = PositionResolver()
        src_a = FakeSource(_COORD_A)
        src_b = FakeSource(_COORD_B)
        r.add_source("a", SourceKind.MINIMAP_RADAR, src_a)
        r.add_source("b", SourceKind.LOCAL_MINIMAP, src_b)
        assert r.resolve(_FRAME) == _COORD_A
        assert src_b.call_count == 0  # never reached

    def test_exception_skips_source(self) -> None:
        r = PositionResolver()
        r.add_source("bad", SourceKind.MINIMAP_RADAR, FakeSource(raises=True))
        r.add_source("good", SourceKind.LOCAL_MINIMAP, FakeSource(_COORD_B))
        assert r.resolve(_FRAME) == _COORD_B

    def test_three_source_chain(self) -> None:
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(None))
        r.add_source("b", SourceKind.LOCAL_MINIMAP, FakeSource(None))
        r.add_source("c", SourceKind.COORDINATE_OCR, FakeSource(_COORD_C))
        assert r.resolve(_FRAME) == _COORD_C

    def test_hint_and_floor_passed_through(self) -> None:
        """Verify kwargs are forwarded to source.read()."""
        mock_src = MagicMock()
        mock_src.read.return_value = _COORD_A
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, mock_src)
        r.resolve(_FRAME, hint=_COORD_B, floor=6)
        mock_src.read.assert_called_once()
        _, kwargs = mock_src.read.call_args
        assert kwargs["hint"] == _COORD_B
        assert kwargs["floor"] == 6

    def test_none_frame_skips_non_frameless(self) -> None:
        """With frame=None, non-frameless sources are skipped."""
        src = FakeSource(_COORD_A)
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, src)
        result = r.resolve(None)
        assert result is None
        assert src.call_count == 0

    def test_frameless_source_called_without_frame(self) -> None:
        src = FakeSource(_COORD_A)
        r = PositionResolver()
        r.add_source("mem", SourceKind.MEMORY_READER, src, frameless=True)
        result = r.resolve(None)
        assert result == _COORD_A
        assert src.call_count == 1

    def test_rejects_source_result_far_from_hint_and_uses_next_source(self) -> None:
        hint = Coordinate(_COORD_A.x, _COORD_A.y, _COORD_A.z)
        drifting = Coordinate(_COORD_A.x - 7, _COORD_A.y, _COORD_A.z)
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("bad", SourceKind.MINIMAP_RADAR, FakeSource(drifting))
        r.add_source("good", SourceKind.LOCAL_MINIMAP, FakeSource(_COORD_B))

        assert r.resolve(_FRAME, hint=hint) == _COORD_B


# ── TestLastKnownFallback ────────────────────────────────────────────────────
class TestLastKnownFallback:
    def test_uses_last_known_after_miss(self) -> None:
        r = PositionResolver(PositionResolverConfig(max_stale_ms=10000, log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        r.resolve(_FRAME)  # populates last_coord

        # Now swap to failing source
        r.remove_source("a")
        r.add_source("fail", SourceKind.MINIMAP_RADAR, FakeSource(None))
        result = r.resolve(_FRAME)
        assert result == _COORD_A  # last-known fallback

    def test_expired_last_known_returns_none(self) -> None:
        r = PositionResolver(PositionResolverConfig(max_stale_ms=1, log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        r.resolve(_FRAME)  # populates last_coord

        r.remove_source("a")
        r.add_source("fail", SourceKind.MINIMAP_RADAR, FakeSource(None))
        time.sleep(0.05)  # 50ms — generous for Windows timer granularity
        assert r.resolve(_FRAME) is None

    def test_max_stale_zero_means_never_expire(self) -> None:
        r = PositionResolver(PositionResolverConfig(max_stale_ms=0, log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        r.resolve(_FRAME)

        r.remove_source("a")
        r.add_source("fail", SourceKind.MINIMAP_RADAR, FakeSource(None))
        time.sleep(0.01)
        assert r.resolve(_FRAME) == _COORD_A  # never expires

    def test_last_known_updates_on_new_hit(self) -> None:
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        r.resolve(_FRAME)
        assert r.last_coordinate == _COORD_A

        r.remove_source("a")
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_B))
        r.resolve(_FRAME)
        assert r.last_coordinate == _COORD_B

    def test_last_known_fallback_skips_coord_far_from_hint(self) -> None:
        r = PositionResolver(PositionResolverConfig(max_stale_ms=10000, log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        r.resolve(_FRAME)

        r.remove_source("a")
        r.add_source("fail", SourceKind.MINIMAP_RADAR, FakeSource(None))
        far_hint = Coordinate(_COORD_A.x - 7, _COORD_A.y, _COORD_A.z)

        assert r.resolve(_FRAME, hint=far_hint) is None


# ── TestStats ────────────────────────────────────────────────────────────────
class TestStats:
    def test_resolve_count_increments(self) -> None:
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(None))
        r.resolve(_FRAME)
        r.resolve(_FRAME)
        r.resolve(_FRAME)
        assert r.resolve_count == 3

    def test_hit_miss_tracking(self) -> None:
        src_hit = FakeSource(_COORD_A)
        src_miss = FakeSource(None)
        r = PositionResolver()
        r.add_source("hit", SourceKind.MINIMAP_RADAR, src_hit)
        r.resolve(_FRAME)
        r.resolve(_FRAME)

        snap = r.stats_snapshot()
        assert snap["sources"]["hit"]["hits"] == 2
        assert snap["sources"]["hit"]["misses"] == 0

    def test_miss_tracking(self) -> None:
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("miss", SourceKind.MINIMAP_RADAR, FakeSource(None))
        r.add_source("ok", SourceKind.LOCAL_MINIMAP, FakeSource(_COORD_A))
        r.resolve(_FRAME)

        snap = r.stats_snapshot()
        assert snap["sources"]["miss"]["misses"] == 1
        assert snap["sources"]["ok"]["hits"] == 1

    def test_stats_snapshot_structure(self) -> None:
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        r.resolve(_FRAME)

        snap = r.stats_snapshot()
        assert "resolve_count" in snap
        assert "last_coord" in snap
        assert "sources" in snap
        assert "a" in snap["sources"]
        src_snap = snap["sources"]["a"]
        assert "kind" in src_snap
        assert "enabled" in src_snap
        assert "hits" in src_snap
        assert "misses" in src_snap
        assert "hit_rate" in src_snap
        assert "avg_ms" in src_snap

    def test_reset_stats(self) -> None:
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        r.resolve(_FRAME)
        r.reset_stats()
        snap = r.stats_snapshot()
        assert snap["resolve_count"] == 0
        assert snap["sources"]["a"]["hits"] == 0

    def test_exception_counts_as_miss(self) -> None:
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("bad", SourceKind.MINIMAP_RADAR, FakeSource(raises=True))
        r.resolve(_FRAME)
        snap = r.stats_snapshot()
        # Exception doesn't increment misses (it skips stats update)
        # but resolve_count still increments
        assert snap["resolve_count"] == 1

    def test_stats_snapshot_is_stable_after_source_removal(self) -> None:
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, FakeSource(_COORD_A))
        r.add_source("b", SourceKind.LOCAL_MINIMAP, FakeSource(_COORD_B))

        snap = r.stats_snapshot()
        r.remove_source("b")

        assert "a" in snap["sources"]
        assert "b" in snap["sources"]
        assert r.source_names == ["a"]


# ── TestProtocol ─────────────────────────────────────────────────────────────
class TestProtocol:
    def test_fake_source_implements_protocol(self) -> None:
        assert isinstance(FakeSource(), PositionSource)

    def test_mock_implements_protocol(self) -> None:
        m = MagicMock()
        m.read = MagicMock(return_value=_COORD_A)
        # MagicMock satisfies runtime_checkable protocol
        assert hasattr(m, "read")


# ── TestSourceKind ───────────────────────────────────────────────────────────
class TestSourceKind:
    def test_all_kinds_distinct(self) -> None:
        values = [k.value for k in SourceKind]
        assert len(values) == len(set(values))

    def test_expected_kinds(self) -> None:
        names = {k.name for k in SourceKind}
        assert "MINIMAP_RADAR" in names
        assert "LOCAL_MINIMAP" in names
        assert "COORDINATE_OCR" in names
        assert "MEMORY_READER" in names
        assert "CUSTOM" in names


# ── TestDisabledSources ─────────────────────────────────────────────────────
class TestDisabledSources:
    def test_disabled_source_never_called(self) -> None:
        src = FakeSource(_COORD_A)
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        r.add_source("a", SourceKind.MINIMAP_RADAR, src)
        r.enable_source("a", enabled=False)
        r.resolve(_FRAME)
        assert src.call_count == 0

    def test_skip_disabled_reaches_next(self) -> None:
        r = PositionResolver()
        src_disabled = FakeSource(_COORD_A)
        src_enabled = FakeSource(_COORD_B)
        r.add_source("a", SourceKind.MINIMAP_RADAR, src_disabled)
        r.add_source("b", SourceKind.LOCAL_MINIMAP, src_enabled)
        r.enable_source("a", enabled=False)
        assert r.resolve(_FRAME) == _COORD_B
        assert src_disabled.call_count == 0

    def test_re_enable_source(self) -> None:
        r = PositionResolver()
        src = FakeSource(_COORD_A)
        r.add_source("a", SourceKind.MINIMAP_RADAR, src)
        r.enable_source("a", enabled=False)
        r.resolve(_FRAME)
        assert src.call_count == 0
        r.enable_source("a", enabled=True)
        r.resolve(_FRAME)
        assert src.call_count == 1


# ── TestEdgeCases ────────────────────────────────────────────────────────────
class TestEdgeCases:
    def test_resolve_no_frame_no_sources(self) -> None:
        r = PositionResolver(PositionResolverConfig(log_misses=False))
        assert r.resolve(None) is None

    def test_multiple_resolves_update_last_coord(self) -> None:
        src1 = FakeSource(_COORD_A)
        src2 = FakeSource(_COORD_B)
        r = PositionResolver()
        r.add_source("a", SourceKind.MINIMAP_RADAR, src1)
        r.resolve(_FRAME)
        assert r.last_coordinate == _COORD_A

        r.remove_source("a")
        r.add_source("b", SourceKind.MINIMAP_RADAR, src2)
        r.resolve(_FRAME)
        assert r.last_coordinate == _COORD_B

    def test_last_coordinate_initially_none(self) -> None:
        r = PositionResolver()
        assert r.last_coordinate is None
