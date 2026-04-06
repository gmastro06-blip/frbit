"""Session persistence — save/restore bot state across restarts.

Writes a checkpoint file (``output/session_checkpoint.json``) containing the
current waypoint index, position, stats and route file.  On next ``start()``,
the session can resume from the last checkpoint instead of restarting.

The checkpoint is saved:
  * Periodically (every *N* waypoints — configurable)
  * On ``stop()``
  * On unhandled exceptions in the main loop (crash recovery)

Usage from BotSession::

    from .session_persistence import SessionCheckpoint

    ckpt = SessionCheckpoint.load()
    if ckpt and ckpt.route_file == self._cfg.route_file:
        start_index = ckpt.waypoint_index  # resume from here
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("wn")

_CHECKPOINT_FILE = Path(__file__).parent.parent / "output" / "session_checkpoint.json"
_CHECKPOINT_LOCK = threading.Lock()  # prevents concurrent tmp→rename races


@dataclass
class SessionCheckpoint:
    """Serialisable snapshot of bot progress."""

    route_file: str = ""
    waypoint_index: int = 0
    position_x: int = 0
    position_y: int = 0
    position_z: int = 7
    routes_completed: int = 0
    heal_fired: int = 0
    mana_fired: int = 0
    loot_events: int = 0
    uptime_seconds: float = 0.0
    timestamp: float = 0.0
    timestamp_iso: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> None:
        """Atomic write to disk (tmp + rename), serialised with a module lock."""
        dst = path or _CHECKPOINT_FILE
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.timestamp = time.time()
        import datetime
        self.timestamp_iso = datetime.datetime.fromtimestamp(
            self.timestamp
        ).isoformat()

        tmp = dst.parent / (dst.name + ".tmp")
        with _CHECKPOINT_LOCK:
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(asdict(self), f, indent=2, default=str)
                tmp.replace(dst)
            except Exception as exc:
                _log.debug("checkpoint save failed: %s", exc)
                if tmp.exists():
                    tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["SessionCheckpoint"]:
        """Load last checkpoint from disk, or ``None`` if missing/corrupt."""
        dst = path or _CHECKPOINT_FILE
        if not dst.exists():
            return None
        try:
            with open(dst, encoding="utf-8") as f:
                data = json.load(f)
            # Pop unknown fields and put them in extra
            known = {fld.name for fld in cls.__dataclass_fields__.values()}
            extra = {k: v for k, v in data.items() if k not in known}
            filtered = {k: v for k, v in data.items() if k in known}
            if extra and "extra" in known:
                existing_extra = filtered.get("extra", {})
                if isinstance(existing_extra, dict):
                    existing_extra.update(extra)
                    filtered["extra"] = existing_extra
            return cls(**filtered)
        except Exception as exc:
            _log.debug("checkpoint load failed: %s", exc)
            return None

    @classmethod
    def clear(cls, path: Optional[Path] = None) -> None:
        """Delete the checkpoint file (e.g. after a clean run completes)."""
        dst = path or _CHECKPOINT_FILE
        if dst.exists():
            dst.unlink(missing_ok=True)

    def is_stale(self, max_age_seconds: float = 3600.0) -> bool:
        """True if the checkpoint is older than *max_age_seconds*."""
        if self.timestamp <= 0:
            return True
        return (time.time() - self.timestamp) > max_age_seconds

    def matches_route(self, route_file: str) -> bool:
        """True when the checkpoint was saved for the same route file."""
        return self.route_file == route_file
