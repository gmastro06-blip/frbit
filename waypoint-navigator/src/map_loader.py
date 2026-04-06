"""
TibiaMapLoader
--------------
Downloads (and caches) floor map PNGs, pathfinding PNGs, and the markers JSON
from https://tibiamaps.github.io/tibia-map-data/.

The path PNG encodes walkability per tile:
  - pure black  (0,   0,   0)   → non-walkable  (0xFF in binary)
  - pure white  (255, 255, 255) → unexplored    (0xFA in binary)
  - any shade of green/gray     → walkable tile  (friction value)

When the path PNG is not pixel-perfect, we detect walkability by checking
whether the pixel is "dark enough" to be a wall.
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import numpy as np
import requests

_logger = logging.getLogger(__name__)

_DOWNLOAD_RETRIES = 3       # total attempts
_DOWNLOAD_TIMEOUT = 10      # seconds per request

from .models import Coordinate, Waypoint, BOUNDS

# ---------------------------------------------------------------------------
BASE_URL = "https://tibiamaps.github.io/tibia-map-data"
FLOOR_IDS = [f"{i:02d}" for i in range(16)]  # "00" .. "15"

CACHE_DIR = Path(__file__).parent.parent / "cache"


# ---------------------------------------------------------------------------

class TibiaMapLoader:
    """
    Lazy-loads Tibia map floors.

    Parameters
    ----------
    cache_dir : Path, optional
        Directory where downloaded files are stored.
    floor : int, optional
        Pre-load a specific floor on construction.
    """

    def __init__(self, cache_dir: Optional[Path] = None, log_fn: Any = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._log = log_fn if log_fn is not None else print

        # floor_id (str) → numpy RGBA array  (H, W, 4)
        self._map_images: Dict[str, np.ndarray] = {}
        # floor_id → 2-D bool array: True = walkable
        self._walkability: Dict[str, np.ndarray] = {}
        # all markers / waypoints loaded from markers.json
        self._waypoints: Optional[List[Waypoint]] = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get_map_image(self, floor: int) -> np.ndarray:
        """Return the RGBA map image for *floor* (0-15) as a numpy array."""
        fid = f"{floor:02d}"
        if fid not in self._map_images:
            self._map_images[fid] = self._load_png(f"floor-{fid}-map.png")
        return self._map_images[fid]

    def get_walkability(self, floor: int) -> np.ndarray:
        """
        Return a 2-D boolean array where True = walkable tile.
        Shape: (map_height, map_width).
        """
        fid = f"{floor:02d}"
        if fid not in self._walkability:
            path_img = self._load_png(f"floor-{fid}-path.png")
            self._walkability[fid] = self._parse_walkability(path_img)
        return self._walkability[fid]

    def is_walkable(self, coord: Coordinate) -> bool:
        """Return True if *coord* is a walkable tile."""
        walkable = self.get_walkability(coord.z)
        px, py = coord.to_pixel()
        h, w = walkable.shape
        if not (0 <= py < h and 0 <= px < w):
            return False
        return bool(walkable[py, px])

    def get_waypoints(self) -> List[Waypoint]:
        """Return all named map markers as Waypoint objects."""
        if self._waypoints is None:
            self._waypoints = self._load_markers()
        return self._waypoints

    def find_waypoints(
        self,
        query: str,
        floor: Optional[int] = None,
    ) -> List[Waypoint]:
        """Search waypoints by name (case-insensitive substring match)."""
        q = query.lower()
        results = [
            wp for wp in self.get_waypoints()
            if q in wp.name.lower()
        ]
        if floor is not None:
            results = [wp for wp in results if wp.coord.z == floor]
        return results

    def get_walkability_region(
        self,
        floor: int,
        x_start: int,
        y_start: int,
        width: int,
        height: int,
    ) -> np.ndarray:
        """Return a walkability sub-array for a rectangular region.

        Clamps both the lower *and* upper bounds of the slice so the returned
        array never exceeds the underlying map dimensions.  Callers that rely
        on a fixed ``(height, width)`` shape will receive a correctly-sized
        (possibly padded) view even at map edges.
        """
        full = self.get_walkability(floor)
        fh, fw = full.shape
        px0 = x_start - BOUNDS["xMin"]
        py0 = y_start - BOUNDS["yMin"]
        r0 = max(py0, 0)
        r1 = min(py0 + height, fh)
        c0 = max(px0, 0)
        c1 = min(px0 + width, fw)
        return full[r0:r1, c0:c1]

    def preload_floor(self, floor: int) -> None:
        """Explicitly download and cache both PNGs for a floor."""
        self.get_map_image(floor)
        self.get_walkability(floor)
        self._log(f"  Floor {floor:02d} preloaded.")

    def floor_loaded(self, floor: int) -> bool:
        """Return True if *floor* walkability data is currently held in memory."""
        return f"{floor:02d}" in self._walkability

    def list_cached_floors(self) -> List[int]:
        """Return sorted list of floor numbers whose walkability is in memory."""
        floors = []
        for fid in self._walkability:
            try:
                floors.append(int(fid))
            except ValueError:
                pass
        return sorted(floors)

    def clear_cache(self) -> None:
        """
        Release all in-memory map data (images + walkability + waypoints).

        On-disk cache files are *not* removed — they will be re-used on the
        next call to get_map_image / get_walkability / get_waypoints.
        """
        self._map_images.clear()
        self._walkability.clear()
        self._waypoints = None

    @property
    def loaded_count(self) -> int:
        """Number of floors whose walkability data is currently in memory."""
        return len(self._walkability)

    def count_waypoints(self, floor: Optional[int] = None) -> int:
        """
        Return the number of available waypoints.

        Parameters
        ----------
        floor : int, optional
            When given, count only waypoints on that floor.  When ``None``
            (default) count all waypoints.
        """
        wps = self.get_waypoints()
        if floor is None:
            return len(wps)
        return sum(1 for wp in wps if wp.coord.z == floor)

    def stats_snapshot(self) -> dict[str, Any]:
        """
        Return a lightweight dict of loader state.

        Keys: ``loaded_count``, ``map_images_count``, ``waypoints_loaded``,
        ``waypoints_count``.
        """
        wp_count = len(self._waypoints) if self._waypoints is not None else None
        return {
            "loaded_count":     self.loaded_count,
            "map_images_count": len(self._map_images),
            "waypoints_loaded": self._waypoints is not None,
            "waypoints_count":  wp_count,
        }

    @property
    def has_waypoints(self) -> bool:
        """True when at least one waypoint has been loaded."""
        return self._waypoints is not None and len(self._waypoints) > 0

    @property
    def has_cached_floors(self) -> bool:
        """True when at least one floor is cached in memory."""
        return self.loaded_count > 0

    @property
    def has_map_images(self) -> bool:
        """True when at least one map image has been loaded."""
        return len(self._map_images) > 0

    @property
    def waypoints_loaded(self) -> bool:
        """True when waypoints have been parsed (even if the list is empty)."""
        return self._waypoints is not None

    @property
    def map_images_count(self) -> int:
        """Number of floor map images currently cached in memory."""
        return len(self._map_images)

    def waypoint_names(self, floor: Optional[int] = None) -> List[str]:
        """Return a sorted list of waypoint names.

        Parameters
        ----------
        floor : int, optional
            When given, return only names of waypoints on that floor.
        """
        wps = self.get_waypoints()
        if floor is not None:
            wps = [wp for wp in wps if wp.coord.z == floor]
        return sorted(wp.name for wp in wps)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _cache_path(self, filename: str) -> Path:
        return self.cache_dir / filename

    def _load_png(self, filename: str) -> np.ndarray:
        """Download a PNG if not cached, then load as numpy RGBA array."""
        from PIL import Image
        import io

        cache_path = self._cache_path(filename)
        if not cache_path.exists():
            url = f"{BASE_URL}/{filename}"
            self._log(f"  Downloading {url} …")
            last_exc: Exception = RuntimeError("no attempts made")
            for attempt in range(_DOWNLOAD_RETRIES):
                try:
                    resp = requests.get(url, timeout=_DOWNLOAD_TIMEOUT)
                    resp.raise_for_status()
                    break
                except (requests.RequestException, OSError) as exc:
                    last_exc = exc
                    if attempt < _DOWNLOAD_RETRIES - 1:
                        wait = 2 ** attempt  # 1s, 2s
                        _logger.warning(
                            "map download failed (attempt %d/%d), retry in %ds: %s",
                            attempt + 1, _DOWNLOAD_RETRIES, wait, exc,
                        )
                        time.sleep(wait)
            else:
                raise last_exc  # all retries exhausted
            # Validate the image before caching.
            try:
                Image.open(io.BytesIO(resp.content)).convert("RGBA")
            except Exception as exc:
                raise ValueError(f"Downloaded PNG is corrupt ({filename}): {exc}") from exc
            cache_path.write_bytes(resp.content)
            self._log(f"  Saved → {cache_path}")

        try:
            img = Image.open(cache_path).convert("RGBA")
        except Exception:
            self._log(f"  Cached PNG corrupt — deleting {cache_path}")
            cache_path.unlink(missing_ok=True)
            raise
        return np.array(img, dtype=np.uint8)

    @staticmethod
    def _parse_walkability(path_img: np.ndarray) -> np.ndarray:
        """
        Convert a path PNG into a boolean walkability map.

        The tibiamaps path PNG colour encoding:
          gray  (R==G==B, 1-254)    → walkable tile (value = friction)
          yellow (R=255, G=255, B=0) → NON-walkable (wall / obstacle / object)
          white  (R=255, G=255, B=255) → unexplored (treated as non-walkable)
          black  (R=0,   G=0,   B=0)   → non-walkable (legacy / safe fallback)
        """
        r = path_img[:, :, 0].astype(np.int32)
        g = path_img[:, :, 1].astype(np.int32)
        b = path_img[:, :, 2].astype(np.int32)

        # Yellow (255,255,0) = the primary wall/obstacle marker in tibiamaps
        is_yellow = (r == 255) & (g == 255) & (b == 0)
        # Pure black = non-walkable (legacy/safe fallback)
        is_black  = (r < 10) & (g < 10) & (b < 10)
        # Pure white = unexplored tiles — treated as non-walkable
        is_white  = (r > 245) & (g > 245) & (b > 245)

        walkable = ~is_yellow & ~is_black & ~is_white
        return np.asarray(walkable)

    def _load_markers(self) -> List[Waypoint]:
        """Download and parse markers.json → List[Waypoint]."""
        cache_path = self._cache_path("markers.json")
        if not cache_path.exists():
            url_cdn = f"https://raw.githubusercontent.com/tibiamaps/tibia-map-data/master/data/markers.json"
            self._log(f"  Downloading markers from {url_cdn} …")
            last_exc: Exception = RuntimeError("no attempts made")
            for attempt in range(_DOWNLOAD_RETRIES):
                try:
                    resp = requests.get(url_cdn, timeout=_DOWNLOAD_TIMEOUT)
                    resp.raise_for_status()
                    break
                except (requests.RequestException, OSError) as exc:
                    last_exc = exc
                    if attempt < _DOWNLOAD_RETRIES - 1:
                        wait = 2 ** attempt  # 1s, 2s
                        _logger.warning(
                            "markers download failed (attempt %d/%d), retry in %ds: %s",
                            attempt + 1, _DOWNLOAD_RETRIES, wait, exc,
                        )
                        time.sleep(wait)
            else:
                raise last_exc  # all retries exhausted
            # Validate JSON before caching.
            try:
                json.loads(resp.content.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"Downloaded markers.json is corrupt: {exc}") from exc
            cache_path.write_bytes(resp.content)

        try:
            with cache_path.open(encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._log(f"  Cached markers.json corrupt — deleting {cache_path}")
            cache_path.unlink(missing_ok=True)
            raise

        waypoints: List[Waypoint] = []
        for item in raw:
            try:
                waypoints.append(Waypoint.from_dict(item))
            except Exception:
                pass  # skip malformed entries

        self._log(f"  Loaded {len(waypoints)} waypoints from markers.json")
        return waypoints

    # -----------------------------------------------------------------------
    # Walkability learning — persist blocked/open overrides across sessions
    # -----------------------------------------------------------------------
    _LEARNED_FILE = "learned_walkability.json"
    #: Hours after which a dynamic block entry expires (default: 4 hours).
    LEARNED_TTL_HOURS: float = 4.0
    #: A single runtime block is too noisy to poison future sessions.
    BLOCKED_CONFIRMATION_THRESHOLD: int = 2
    #: Route-critical tiles need stronger confirmation before poisoning pathing.
    CRITICAL_ROUTE_BLOCKED_CONFIRMATION_THRESHOLD: int = 3
    #: Opened overrides never expire (permanent learning) — set to 0.
    OPENED_TTL_HOURS: float = 0.0

    def save_learned_blocks(
        self,
        blocked: list[tuple[int, int, int]],
        opened: list[tuple[int, int, int]] | None = None,
    ) -> int:
        """Save dynamically discovered blocked/opened tiles to a JSON file.

        Each entry is stored with an ISO-8601 timestamp so that stale
        entries can be pruned on load (TTL-based expiry).

        Parameters
        ----------
        blocked : list of (x, y, z)
            World coordinates of tiles confirmed as non-walkable at runtime.
        opened : list of (x, y, z), optional
            World coordinates of tiles confirmed as walkable at runtime
            (static data said wall, but character walked through).

        Returns the number of entries written.
        """
        import datetime as _dt

        now = _dt.datetime.utcnow().isoformat()
        cache_path = self._cache_path(self._LEARNED_FILE)
        data: dict = {"blocked": [], "opened": []}

        # Merge with existing (preserving timestamps)
        if cache_path.exists():
            try:
                with cache_path.open(encoding="utf-8") as fh:
                    data = json.load(fh)
                # Migrate legacy entries (bare tuples) → timestamped dicts
                data["blocked"] = self._migrate_entries(data.get("blocked", []))
                data["opened"] = self._migrate_entries(data.get("opened", []))
            except Exception:
                pass

        existing_b = {self._entry_key(e) for e in data["blocked"]}
        existing_o = {self._entry_key(e) for e in data["opened"]}
        blocked_by_key = {self._entry_key(e): e for e in data["blocked"]}
        opened_by_key = {self._entry_key(e): e for e in data["opened"]}

        for b in blocked:
            key = tuple(b)
            if key in opened_by_key:
                continue
            if key in blocked_by_key:
                blocked_by_key[key]["ts"] = now
                blocked_by_key[key]["hits"] = self._entry_hits(blocked_by_key[key]) + 1
            else:
                existing_b.add(key)
                entry = {"xyz": list(b), "ts": now, "hits": 1}
                data["blocked"].append(entry)
                blocked_by_key[key] = entry
        if opened:
            for o in opened:
                key = tuple(o)
                if key in blocked_by_key:
                    data["blocked"] = [e for e in data["blocked"] if self._entry_key(e) != key]
                    blocked_by_key.pop(key, None)
                    existing_b.discard(key)
                if key not in existing_o:
                    existing_o.add(key)
                    entry = {"xyz": list(o), "ts": now}
                    data["opened"].append(entry)
                    opened_by_key[key] = entry
                else:
                    opened_by_key[key]["ts"] = now

        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)

        total = len(data["blocked"]) + len(data["opened"])
        self._log(f"  Saved {total} learned walkability entries → {cache_path.name}")
        return total

    def load_learned_blocks(
        self,
        blocked_ttl_hours: float | None = None,
        opened_ttl_hours: float | None = None,
        critical_tiles: Iterable[tuple[int, int, int]] | None = None,
    ) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
        """Load previously learned walkability overrides.

        Entries older than their TTL are pruned automatically.  Opened
        overrides default to permanent (TTL=0 → no expiry).

        Returns (blocked, opened) tuples of world (x, y, z) coordinates.
        """
        import datetime as _dt

        b_ttl = blocked_ttl_hours if blocked_ttl_hours is not None else self.LEARNED_TTL_HOURS
        o_ttl = opened_ttl_hours if opened_ttl_hours is not None else self.OPENED_TTL_HOURS

        cache_path = self._cache_path(self._LEARNED_FILE)
        if not cache_path.exists():
            return [], []
        try:
            with cache_path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            self._log(f"  Failed to load learned walkability: {exc}")
            return [], []

        now = _dt.datetime.utcnow()
        raw_blocked = self._migrate_entries(data.get("blocked", []))
        raw_opened = self._migrate_entries(data.get("opened", []))

        critical_tile_set = set(critical_tiles or ())
        blocked = [
            xyz for xyz in self._filter_by_ttl(raw_blocked, b_ttl, now)
            if self._entry_hits_by_xyz(raw_blocked, xyz) >= self._blocked_confirmation_threshold(
                xyz,
                critical_tile_set,
            )
        ]
        opened = self._filter_by_ttl(raw_opened, o_ttl, now)

        # Prune expired entries from disk
        pruned_b = len(raw_blocked) - len(blocked)
        pruned_o = len(raw_opened) - len(opened)
        if pruned_b > 0 or pruned_o > 0:
            data["blocked"] = [e for e in raw_blocked if self._entry_alive(e, b_ttl, now)]
            data["opened"] = [e for e in raw_opened if self._entry_alive(e, o_ttl, now)]
            try:
                with cache_path.open("w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=1)
                self._log(
                    f"  Pruned {pruned_b} expired blocks + {pruned_o} expired opens"
                )
            except Exception:
                pass

        self._log(
            f"  Loaded {len(blocked)} blocked + {len(opened)} opened "
            f"learned walkability entries"
        )
        return blocked, opened  # type: ignore[return-value]

    def apply_learned_walkability(self) -> int:
        """Load and apply learned walkability overrides to in-memory grids.

        Returns the number of tiles modified.
        """
        return self.apply_learned_walkability_for_tiles()

    def apply_learned_walkability_for_tiles(
        self,
        critical_tiles: Iterable[tuple[int, int, int]] | None = None,
    ) -> int:
        """Apply learned walkability, using a stricter threshold on critical tiles."""
        blocked, opened = self.load_learned_blocks(critical_tiles=critical_tiles)
        count = 0
        for x, y, z in blocked:
            wk = self.get_walkability(z)
            px, py = x - BOUNDS["xMin"], y - BOUNDS["yMin"]
            h, w = wk.shape
            if 0 <= py < h and 0 <= px < w and wk[py, px]:
                wk[py, px] = False
                count += 1
        for x, y, z in opened:
            wk = self.get_walkability(z)
            px, py = x - BOUNDS["xMin"], y - BOUNDS["yMin"]
            h, w = wk.shape
            if 0 <= py < h and 0 <= px < w and not wk[py, px]:
                wk[py, px] = True
                count += 1
        if count:
            self._log(f"  Applied {count} learned walkability overrides to grid")
        return count

    @classmethod
    def _blocked_confirmation_threshold(
        cls,
        xyz: tuple[int, int, int],
        critical_tiles: set[tuple[int, int, int]] | None = None,
    ) -> int:
        if critical_tiles and xyz in critical_tiles:
            return cls.CRITICAL_ROUTE_BLOCKED_CONFIRMATION_THRESHOLD
        return cls.BLOCKED_CONFIRMATION_THRESHOLD

    # ── TTL helpers ──

    @staticmethod
    def _migrate_entries(entries: list) -> list[dict]:
        """Convert legacy bare-tuple entries to ``{xyz, ts}`` dicts."""
        import datetime as _dt
        migrated: list[dict] = []
        epoch = "2020-01-01T00:00:00"  # old entries get a very old timestamp
        for e in entries:
            if isinstance(e, dict) and "xyz" in e:
                e.setdefault("hits", 1)
                migrated.append(e)
            elif isinstance(e, (list, tuple)) and len(e) >= 3:
                migrated.append({"xyz": list(e[:3]), "ts": epoch, "hits": 1})
        return migrated

    @staticmethod
    def _entry_hits(entry: dict) -> int:
        try:
            return max(1, int(entry.get("hits", 1)))
        except Exception:
            return 1

    @classmethod
    def _entry_hits_by_xyz(
        cls,
        entries: list[dict],
        xyz: tuple[int, int, int],
    ) -> int:
        for entry in entries:
            if cls._entry_key(entry) == xyz:
                return cls._entry_hits(entry)
        return 1

    @staticmethod
    def _entry_key(entry: dict | list | tuple) -> tuple:
        if isinstance(entry, dict):
            return tuple(entry["xyz"])
        return tuple(entry[:3])

    @staticmethod
    def _entry_alive(
        entry: dict, ttl_hours: float, now: datetime.datetime
    ) -> bool:
        import datetime as _dt
        if ttl_hours <= 0:
            return True  # TTL disabled → never expires
        ts_str = entry.get("ts", "2020-01-01T00:00:00")
        try:
            ts = _dt.datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            return False  # corrupt timestamp → treat as expired
        return (now - ts).total_seconds() < ttl_hours * 3600

    @staticmethod
    def _filter_by_ttl(
        entries: list[dict], ttl_hours: float, now: datetime.datetime
    ) -> list[tuple[int, int, int]]:
        import datetime as _dt
        result: list[tuple[int, int, int]] = []
        for e in entries:
            if not TibiaMapLoader._entry_alive(e, ttl_hours, now):
                continue
            xyz = e.get("xyz", [])
            if len(xyz) >= 3:
                result.append((int(xyz[0]), int(xyz[1]), int(xyz[2])))
        return result
