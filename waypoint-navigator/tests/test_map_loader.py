"""
Tests para src/map_loader.py — TibiaMapLoader.
100 % offline: no descarga nada, usa arrays / JSON sintéticos en memoria.
Las funciones que tocan red (_load_png, _load_markers con petición HTTP)
se parchean para devolver datos de prueba construidos localmente.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.map_loader import TibiaMapLoader
from src.models import Coordinate, BOUNDS, Waypoint


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_XO = BOUNDS["xMin"]
_YO = BOUNDS["yMin"]


def _coord(px: int, py: int, z: int = 7) -> Coordinate:
    return Coordinate(_XO + px, _YO + py, z)


def _make_waypoint(z: int = 7) -> Waypoint:
    """Return a minimal Waypoint on the given floor."""
    return Waypoint(name="wp", coord=_coord(0, 0, z))


def _path_png(h: int, w: int, walkable_mask: np.ndarray | None = None) -> np.ndarray:
    """
    Crea un array RGBA simulando un path PNG de tibiamaps.
    Los píxeles walkable = gray (128,128,128,255).
    Los píxeles no walkable = yellow (255,255,0,255).
    walkable_mask: bool array (h×w); True → walkable, False → pared.
    """
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    mask = walkable_mask if walkable_mask is not None else np.ones((h, w), dtype=bool)

    # Walkable → gray
    rgba[mask, 0] = 128
    rgba[mask, 1] = 128
    rgba[mask, 2] = 128
    rgba[mask, 3] = 255

    # Non-walkable → yellow
    rgba[~mask, 0] = 255
    rgba[~mask, 1] = 255
    rgba[~mask, 2] = 0
    rgba[~mask, 3] = 255

    return rgba


def _markers_json(entries: list[dict] | None = None) -> bytes:
    """JSON mínimo de markers compatible con Waypoint.from_dict."""
    default = [
        {"name": "Thais Temple",  "x": 32369, "y": 32241, "z": 7, "type": "temple"},
        {"name": "Thais Depot",   "x": 32361, "y": 32232, "z": 7, "type": "depot"},
        {"name": "Edron Temple",  "x": 33191, "y": 31818, "z": 7, "type": "temple"},
    ]
    return json.dumps(entries or default).encode()


def _loader_with_cache(tmp_path: Path) -> TibiaMapLoader:
    """TibiaMapLoader apuntando a un directorio temporal."""
    return TibiaMapLoader(cache_dir=tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# _parse_walkability — lógica de color
# ─────────────────────────────────────────────────────────────────────────────

class TestParseWalkability:
    """Prueba unitaria de la función estática sin tocar disco ni red."""

    def test_all_walkable_gray(self):
        h, w = 10, 10
        img = np.full((h, w, 4), 128, dtype=np.uint8)
        img[:, :, 3] = 255
        result = TibiaMapLoader._parse_walkability(img)
        assert result.shape == (h, w)
        assert result.all()

    def test_yellow_is_wall(self):
        """Yellow (255,255,0) → non-walkable."""
        img = np.zeros((5, 5, 4), dtype=np.uint8)
        img[:, :, 0] = 255
        img[:, :, 1] = 255
        img[:, :, 2] = 0
        img[:, :, 3] = 255
        result = TibiaMapLoader._parse_walkability(img)
        assert not result.any(), "Todos los píxeles amarillos deben ser non-walkable"

    def test_black_is_wall(self):
        """Negro puro (0,0,0) → non-walkable."""
        img = np.zeros((5, 5, 4), dtype=np.uint8)
        result = TibiaMapLoader._parse_walkability(img)
        assert not result.any()

    def test_white_is_unexplored_nonwalkable(self):
        """Blanco (255,255,255) → unexplored → non-walkable."""
        img = np.full((5, 5, 4), 255, dtype=np.uint8)
        result = TibiaMapLoader._parse_walkability(img)
        assert not result.any()

    def test_mixed_pixels(self):
        """
        Row 0 = gray    (walkable)
        Row 1 = yellow  (wall)
        Row 2 = black   (wall)
        Row 3 = white   (unexplored/wall)
        """
        img = np.zeros((4, 4, 4), dtype=np.uint8)
        # Row 0: gray
        img[0, :, :3] = 128; img[0, :, 3] = 255
        # Row 1: yellow
        img[1, :, 0] = 255; img[1, :, 1] = 255; img[1, :, 2] = 0;   img[1, :, 3] = 255
        # Row 2: black (default = 0)
        # Row 3: white
        img[3, :, :] = 255

        result = TibiaMapLoader._parse_walkability(img)
        assert result[0].all(),   "Row 0 (gray) debería ser walkable"
        assert not result[1].any(),"Row 1 (yellow) debería ser wall"
        assert not result[2].any(),"Row 2 (black)  debería ser wall"
        assert not result[3].any(),"Row 3 (white)  debería ser unexplored/wall"

    def test_output_dtype_is_bool(self):
        img = np.full((4, 4, 4), 128, dtype=np.uint8)
        result = TibiaMapLoader._parse_walkability(img)
        assert result.dtype == bool

    def test_output_shape_matches_input(self):
        img = np.zeros((17, 33, 4), dtype=np.uint8)
        result = TibiaMapLoader._parse_walkability(img)
        assert result.shape == (17, 33)

    def test_gray_values_walkable(self):
        """Valores de gris 10-244 (fuera del umbral negro/blanco/amarillo) → walkable.
        El umbral negro es r<10, blanco r>245, por lo que 1-9 y 246-254 se excluyen.
        """
        img = np.zeros((3, 3, 4), dtype=np.uint8)
        for shade in [10, 64, 128, 192, 200, 244]:
            img[:, :, :3] = shade
            img[:, :, 3] = 255
            result = TibiaMapLoader._parse_walkability(img)
            assert result.all(), f"shade={shade} debería ser walkable"

    def test_partial_walkability(self):
        """La mitad izquierda walkable, la derecha pared."""
        h, w = 10, 20
        mask = np.zeros((h, w), dtype=bool)
        mask[:, :10] = True   # izquierda walkable

        img = _path_png(h, w, walkable_mask=mask)
        result = TibiaMapLoader._parse_walkability(img)
        assert result[:, :10].all(),  "Mitad izquierda debería ser walkable"
        assert not result[:, 10:].any(), "Mitad derecha debería ser wall"


# ─────────────────────────────────────────────────────────────────────────────
# TibiaMapLoader: construcción y cache_dir
# ─────────────────────────────────────────────────────────────────────────────

class TestLoaderConstruction:

    def test_creates_cache_dir(self, tmp_path: Path):
        new_dir = tmp_path / "new_cache"
        assert not new_dir.exists()
        loader = TibiaMapLoader(cache_dir=new_dir)
        assert new_dir.exists()

    def test_initial_state_empty(self, tmp_path: Path):
        loader = _loader_with_cache(tmp_path)
        assert loader._map_images == {}
        assert loader._walkability == {}
        assert loader._waypoints is None


# ─────────────────────────────────────────────────────────────────────────────
# get_walkability — usa PNG sintético guardado en tmp_path
# ─────────────────────────────────────────────────────────────────────────────

class TestGetWalkability:

    def _write_path_png(self, tmp_path: Path, floor: int,
                        mask: np.ndarray) -> None:
        """Escribe un path PNG sintético usando PIL."""
        from PIL import Image
        rgba = _path_png(mask.shape[0], mask.shape[1], walkable_mask=mask)
        img = Image.fromarray(rgba, "RGBA")
        img.save(tmp_path / f"floor-{floor:02d}-path.png")

    def test_returns_2d_bool_array(self, tmp_path: Path):
        h, w = 50, 80
        mask = np.ones((h, w), dtype=bool)
        self._write_path_png(tmp_path, 7, mask)
        loader = _loader_with_cache(tmp_path)
        result = loader.get_walkability(7)
        assert result.ndim == 2
        assert result.dtype == bool

    def test_all_walkable_floor(self, tmp_path: Path):
        h, w = 20, 30
        mask = np.ones((h, w), dtype=bool)
        self._write_path_png(tmp_path, 7, mask)
        loader = _loader_with_cache(tmp_path)
        assert loader.get_walkability(7).all()

    def test_all_wall_floor(self, tmp_path: Path):
        h, w = 20, 30
        mask = np.zeros((h, w), dtype=bool)
        self._write_path_png(tmp_path, 7, mask)
        loader = _loader_with_cache(tmp_path)
        assert not loader.get_walkability(7).any()

    def test_caching_returns_same_object(self, tmp_path: Path):
        h, w = 10, 10
        mask = np.ones((h, w), dtype=bool)
        self._write_path_png(tmp_path, 7, mask)
        loader = _loader_with_cache(tmp_path)
        a = loader.get_walkability(7)
        b = loader.get_walkability(7)
        assert a is b, "Segunda llamada debería devolver el objeto cacheado"

    def test_different_floors_independent(self, tmp_path: Path):
        h, w = 10, 10
        mask_all = np.ones((h, w), dtype=bool)
        mask_none = np.zeros((h, w), dtype=bool)
        self._write_path_png(tmp_path, 7, mask_all)
        self._write_path_png(tmp_path, 8, mask_none)
        loader = _loader_with_cache(tmp_path)
        assert loader.get_walkability(7).all()
        assert not loader.get_walkability(8).any()


# ─────────────────────────────────────────────────────────────────────────────
# is_walkable
# ─────────────────────────────────────────────────────────────────────────────

class TestIsWalkable:

    def _loader_all_walkable(self, tmp_path: Path, h: int = 200,
                             w: int = 200) -> TibiaMapLoader:
        from PIL import Image
        mask = np.ones((h, w), dtype=bool)
        rgba = _path_png(h, w, walkable_mask=mask)
        Image.fromarray(rgba, "RGBA").save(tmp_path / "floor-07-path.png")
        return _loader_with_cache(tmp_path)

    def test_walkable_coord(self, tmp_path: Path):
        loader = self._loader_all_walkable(tmp_path)
        coord = _coord(5, 5, z=7)
        assert loader.is_walkable(coord) is True

    def test_out_of_bounds_coord_returns_false(self, tmp_path: Path):
        loader = self._loader_all_walkable(tmp_path, h=10, w=10)
        # Coordenada cuyo pixel cae fuera del array 10×10
        coord = _coord(500, 500, z=7)
        assert loader.is_walkable(coord) is False

    def test_wall_coord_returns_false(self, tmp_path: Path):
        from PIL import Image
        h, w = 50, 50
        mask = np.ones((h, w), dtype=bool)
        mask[10, 10] = False   # pared en pixel (10, 10)
        rgba = _path_png(h, w, walkable_mask=mask)
        Image.fromarray(rgba, "RGBA").save(tmp_path / "floor-07-path.png")
        loader = _loader_with_cache(tmp_path)
        wall = _coord(10, 10, z=7)
        assert loader.is_walkable(wall) is False


# ─────────────────────────────────────────────────────────────────────────────
# get_walkability_region
# ─────────────────────────────────────────────────────────────────────────────

class TestGetWalkabilityRegion:

    def _loader_checkerboard(self, tmp_path: Path, h: int = 100,
                             w: int = 100) -> TibiaMapLoader:
        from PIL import Image
        mask = np.indices((h, w)).sum(axis=0) % 2 == 0  # tablero de ajedrez
        rgba = _path_png(h, w, walkable_mask=mask)
        Image.fromarray(rgba, "RGBA").save(tmp_path / "floor-07-path.png")
        return _loader_with_cache(tmp_path)

    def test_region_shape_correct(self, tmp_path: Path):
        loader = self._loader_checkerboard(tmp_path)
        region = loader.get_walkability_region(
            floor=7,
            x_start=_XO, y_start=_YO,   # offset 0,0 en píxel
            width=20, height=15,
        )
        assert region.shape == (15, 20)

    def test_region_returns_bool(self, tmp_path: Path):
        loader = self._loader_checkerboard(tmp_path)
        region = loader.get_walkability_region(7, _XO, _YO, 10, 10)
        assert region.dtype == bool

    def test_region_clipped_at_boundary(self, tmp_path: Path):
        """Pedir más píxeles de los disponibles no debe crashear."""
        loader = self._loader_checkerboard(tmp_path, h=50, w=50)
        # Pedir 200×200 sobre un array 50×50 → recibe lo que hay
        region = loader.get_walkability_region(7, _XO, _YO, 200, 200)
        assert region.shape[0] <= 50
        assert region.shape[1] <= 50


# ─────────────────────────────────────────────────────────────────────────────
# get_waypoints / find_waypoints — usa un markers.json sintético en caché
# ─────────────────────────────────────────────────────────────────────────────

class TestWaypoints:

    def _loader_with_markers(self, tmp_path: Path,
                             entries: list[dict] | None = None) -> TibiaMapLoader:
        (tmp_path / "markers.json").write_bytes(_markers_json(entries))
        return _loader_with_cache(tmp_path)

    def test_get_waypoints_returns_list(self, tmp_path: Path):
        loader = self._loader_with_markers(tmp_path)
        wps = loader.get_waypoints()
        assert isinstance(wps, list)
        assert len(wps) == 3

    def test_waypoints_names(self, tmp_path: Path):
        loader = self._loader_with_markers(tmp_path)
        names = {wp.name for wp in loader.get_waypoints()}
        assert "Thais Temple" in names
        assert "Thais Depot" in names

    def test_waypoints_coords(self, tmp_path: Path):
        loader = self._loader_with_markers(tmp_path)
        temple = next(wp for wp in loader.get_waypoints() if "Temple" in wp.name
                      and "Thais" in wp.name)
        assert temple.coord == Coordinate(32369, 32241, 7)

    def test_get_waypoints_caches_result(self, tmp_path: Path):
        loader = self._loader_with_markers(tmp_path)
        a = loader.get_waypoints()
        b = loader.get_waypoints()
        assert a is b

    def test_find_waypoints_by_substring(self, tmp_path: Path):
        loader = self._loader_with_markers(tmp_path)
        results = loader.find_waypoints("thais")
        assert len(results) == 2   # Thais Temple + Thais Depot
        for wp in results:
            assert "thais" in wp.name.lower()

    def test_find_waypoints_case_insensitive(self, tmp_path: Path):
        loader = self._loader_with_markers(tmp_path)
        results_lower = loader.find_waypoints("thais")
        results_upper = loader.find_waypoints("THAIS")
        assert len(results_lower) == len(results_upper)

    def test_find_waypoints_no_match(self, tmp_path: Path):
        loader = self._loader_with_markers(tmp_path)
        results = loader.find_waypoints("xyznonexistent")
        assert results == []

    def test_find_waypoints_floor_filter(self, tmp_path: Path):
        """floor= debe filtrar por z."""
        entries: list[dict] = [
            {"name": "Temple z7",  "x": 32369, "y": 32241, "z": 7,  "type": "temple"},
            {"name": "Temple z8",  "x": 32369, "y": 32241, "z": 8,  "type": "temple"},
            {"name": "Temple z10", "x": 32369, "y": 32241, "z": 10, "type": "temple"},
        ]
        loader = self._loader_with_markers(tmp_path, entries)
        results = loader.find_waypoints("temple", floor=7)
        assert len(results) == 1
        assert results[0].coord.z == 7

    def test_find_waypoints_floor_none_returns_all(self, tmp_path: Path):
        loader = self._loader_with_markers(tmp_path)
        all_wps = loader.find_waypoints("a")   # 'a' matches all entries
        assert len(all_wps) >= 1

    def test_malformed_markers_entry_skipped(self, tmp_path: Path):
        """Entradas malformadas no deben crashear la carga."""
        entries: list[dict] = [
            {"name": "Valid",   "x": 32369, "y": 32241, "z": 7, "type": "temple"},
            {"bad_key": "nope"},   # sin x/y/z
        ]
        loader = self._loader_with_markers(tmp_path, entries)
        wps = loader.get_waypoints()
        # Al menos el válido se cargó
        assert any(wp.name == "Valid" for wp in wps)


# ─────────────────────────────────────────────────────────────────────────────
# preload_floor — verifica que llama a get_map_image y get_walkability
# ─────────────────────────────────────────────────────────────────────────────

class TestPreloadFloor:

    def test_preload_calls_both_loaders(self, tmp_path: Path):
        loader = _loader_with_cache(tmp_path)
        with patch.object(loader, 'get_map_image', return_value=np.zeros((10, 10, 4), dtype=np.uint8)) as mock_image, \
             patch.object(loader, 'get_walkability', return_value=np.ones((10, 10), dtype=bool)) as mock_walk:
            loader.preload_floor(7)
            mock_image.assert_called_once_with(7)
            mock_walk.assert_called_once_with(7)


# ─────────────────────────────────────────────────────────────────────────────
# _parse_walkability — integración con get_walkability (PNG real en disco)
# ─────────────────────────────────────────────────────────────────────────────

class TestParseWalkabilityIntegration:
    """
    Escribe un PNG real con PIL y verifica que get_walkability lo interpreta
    correctamente — prueba de integración completa del pipeline.
    """

    def test_pipeline_walkable_region(self, tmp_path: Path):
        from PIL import Image
        h, w = 30, 40
        # Left half walkable, right half wall
        mask = np.zeros((h, w), dtype=bool)
        mask[:, :20] = True
        rgba = _path_png(h, w, walkable_mask=mask)
        Image.fromarray(rgba, "RGBA").save(tmp_path / "floor-07-path.png")

        loader = _loader_with_cache(tmp_path)
        walk = loader.get_walkability(7)

        assert walk.shape == (h, w)
        assert walk[:, :20].all(),  "Mitad izquierda debería ser walkable"
        assert not walk[:, 20:].any(), "Mitad derecha debería ser wall"

    def test_pipeline_single_walkable_tile(self, tmp_path: Path):
        from PIL import Image
        h, w = 10, 10
        mask = np.zeros((h, w), dtype=bool)
        mask[5, 5] = True   # solo el tile (5,5) es walkable
        rgba = _path_png(h, w, walkable_mask=mask)
        Image.fromarray(rgba, "RGBA").save(tmp_path / "floor-07-path.png")

        loader = _loader_with_cache(tmp_path)
        walk = loader.get_walkability(7)

        total_walkable = walk.sum()
        assert total_walkable == 1
        assert walk[5, 5] is np.bool_(True)


# ─────────────────────────────────────────────────────────────────────────────
# Helper — loader with pre-populated in-memory walkability
# ─────────────────────────────────────────────────────────────────────────────

def _loader_in_memory(floors: list[int]) -> TibiaMapLoader:
    """TibiaMapLoader with _walkability pre-populated; zero disk/network."""
    loader = TibiaMapLoader.__new__(TibiaMapLoader)
    loader._map_images = {}
    loader._walkability = {}
    loader._waypoints = None
    loader.cache_dir = Path(".")  # never actually used
    for z in floors:
        loader._walkability[f"{z:02d}"] = np.ones((10, 10), dtype=bool)
    return loader


# ─────────────────────────────────────────────────────────────────────────────
# floor_loaded()
# ─────────────────────────────────────────────────────────────────────────────

class TestFloorLoaded:

    def test_loaded_floor_returns_true(self):
        loader = _loader_in_memory([7])
        assert loader.floor_loaded(7) is True

    def test_unloaded_floor_returns_false(self):
        loader = _loader_in_memory([7])
        assert loader.floor_loaded(8) is False

    def test_multiple_floors_checked_independently(self):
        loader = _loader_in_memory([6, 7, 8])
        assert loader.floor_loaded(6) is True
        assert loader.floor_loaded(7) is True
        assert loader.floor_loaded(8) is True
        assert loader.floor_loaded(5) is False

    def test_empty_loader_all_return_false(self):
        loader = _loader_in_memory([])
        for z in range(0, 16):
            assert loader.floor_loaded(z) is False

    def test_returns_bool_type(self):
        loader = _loader_in_memory([7])
        assert isinstance(loader.floor_loaded(7), bool)
        assert isinstance(loader.floor_loaded(9), bool)


# ─────────────────────────────────────────────────────────────────────────────
# list_cached_floors()
# ─────────────────────────────────────────────────────────────────────────────

class TestListCachedFloors:

    def test_empty_loader_returns_empty_list(self):
        loader = _loader_in_memory([])
        assert loader.list_cached_floors() == []

    def test_single_floor(self):
        loader = _loader_in_memory([7])
        assert loader.list_cached_floors() == [7]

    def test_multiple_floors_sorted(self):
        loader = _loader_in_memory([9, 5, 7])
        assert loader.list_cached_floors() == [5, 7, 9]

    def test_returns_list_type(self):
        loader = _loader_in_memory([7, 8])
        assert isinstance(loader.list_cached_floors(), list)

    def test_all_16_floors(self):
        loader = _loader_in_memory(list(range(16)))
        assert loader.list_cached_floors() == list(range(16))

    def test_reflects_manual_injection(self):
        loader = _loader_in_memory([7])
        loader._walkability["10"] = np.ones((5, 5), dtype=bool)
        cached = loader.list_cached_floors()
        assert 7 in cached
        assert 10 in cached

    def test_count_matches_injected(self):
        loader = _loader_in_memory([5, 6, 7])
        assert len(loader.list_cached_floors()) == 3


# ─────────────────────────────────────────────────────────────────────────────
# clear_cache()
# ─────────────────────────────────────────────────────────────────────────────

class TestClearCache:

    def test_clear_empties_walkability(self):
        loader = _loader_in_memory([7, 8])
        loader.clear_cache()
        assert loader._walkability == {}

    def test_clear_empties_map_images(self):
        loader = _loader_in_memory([7])
        loader._map_images["07"] = np.zeros((10, 10, 4), dtype=np.uint8)
        loader.clear_cache()
        assert loader._map_images == {}

    def test_clear_resets_waypoints(self):
        loader = _loader_in_memory([])
        loader._waypoints = []   # simulate already-loaded waypoints
        loader.clear_cache()
        assert loader._waypoints is None

    def test_floor_loaded_false_after_clear(self):
        loader = _loader_in_memory([7, 8, 9])
        loader.clear_cache()
        assert loader.floor_loaded(7) is False
        assert loader.floor_loaded(8) is False
        assert loader.floor_loaded(9) is False

    def test_list_cached_floors_empty_after_clear(self):
        loader = _loader_in_memory([5, 6, 7])
        loader.clear_cache()
        assert loader.list_cached_floors() == []

    def test_clear_on_empty_loader_does_not_raise(self):
        loader = _loader_in_memory([])
        loader.clear_cache()   # should not raise
        assert loader.list_cached_floors() == []

    def test_data_can_be_re_injected_after_clear(self):
        loader = _loader_in_memory([7])
        loader.clear_cache()
        loader._walkability["07"] = np.ones((5, 5), dtype=bool)
        assert loader.floor_loaded(7) is True

    def test_clear_is_idempotent(self):
        loader = _loader_in_memory([7])
        loader.clear_cache()
        loader.clear_cache()   # second call must not raise
        assert loader.list_cached_floors() == []


# ─────────────────────────────────────────────────────────────────────────────
# TibiaMapLoader.loaded_count / count_waypoints / stats_snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestLoaderExtras:

    def test_loaded_count_zero_initially(self):
        loader = _loader_in_memory([])
        assert loader.loaded_count == 0

    def test_loaded_count_matches_floors_loaded(self):
        loader = _loader_in_memory([6, 7, 8])
        assert loader.loaded_count == 3

    def test_loaded_count_decreases_after_clear(self):
        loader = _loader_in_memory([7])
        loader.clear_cache()
        assert loader.loaded_count == 0

    def test_count_waypoints_all(self):
        loader = _loader_in_memory([])
        loader._waypoints = [
            _make_waypoint(7),
            _make_waypoint(7),
            _make_waypoint(8),
        ]
        assert loader.count_waypoints() == 3

    def test_count_waypoints_by_floor(self):
        loader = _loader_in_memory([])
        loader._waypoints = [
            _make_waypoint(7),
            _make_waypoint(7),
            _make_waypoint(8),
        ]
        assert loader.count_waypoints(floor=7) == 2
        assert loader.count_waypoints(floor=8) == 1

    def test_count_waypoints_no_match(self):
        loader = _loader_in_memory([])
        loader._waypoints = [_make_waypoint(7)]
        assert loader.count_waypoints(floor=9) == 0

    def test_count_waypoints_empty(self):
        loader = _loader_in_memory([])
        loader._waypoints = []
        assert loader.count_waypoints() == 0

    def test_stats_snapshot_returns_dict(self):
        loader = _loader_in_memory([])
        assert isinstance(loader.stats_snapshot(), dict)

    def test_stats_snapshot_all_keys(self):
        loader = _loader_in_memory([])
        snap = loader.stats_snapshot()
        for key in ("loaded_count", "map_images_count",
                    "waypoints_loaded", "waypoints_count"):
            assert key in snap, f"Missing key: {key}"

    def test_stats_snapshot_initial_state(self):
        loader = _loader_in_memory([])
        snap = loader.stats_snapshot()
        assert snap["loaded_count"]     == 0
        assert snap["map_images_count"] == 0
        assert snap["waypoints_loaded"] is False
        assert snap["waypoints_count"]  is None

    def test_stats_snapshot_after_floor_load(self):
        loader = _loader_in_memory([7, 8])
        snap = loader.stats_snapshot()
        assert snap["loaded_count"] == 2

    def test_stats_snapshot_waypoints_count_after_load(self):
        loader = _loader_in_memory([])
        loader._waypoints = [_make_waypoint(7), _make_waypoint(8)]
        snap = loader.stats_snapshot()
        assert snap["waypoints_loaded"] is True
        assert snap["waypoints_count"]  == 2


class TestLoaderHasWaypoints:

    def test_has_waypoints_false_before_load(self):
        loader = _loader_in_memory([])
        assert loader.has_waypoints is False

    def test_has_waypoints_false_empty_list(self):
        loader = _loader_in_memory([])
        loader._waypoints = []
        assert loader.has_waypoints is False

    def test_has_waypoints_true_when_populated(self):
        loader = _loader_in_memory([])
        loader._waypoints = [_make_waypoint(7)]
        assert loader.has_waypoints is True

    def test_has_waypoints_returns_bool(self):
        loader = _loader_in_memory([])
        assert isinstance(loader.has_waypoints, bool)

    def test_has_waypoints_false_after_clear(self):
        loader = _loader_in_memory([])
        loader._waypoints = [_make_waypoint(7)]
        loader.clear_cache()
        assert loader.has_waypoints is False


class TestLoaderWaypointNames:

    def _make_named_waypoints(self):
        return [
            Waypoint(name="Thais Temple",  coord=_coord(0, 0, 7)),
            Waypoint(name="Thais Depot",   coord=_coord(1, 0, 7)),
            Waypoint(name="Edron Temple",  coord=_coord(0, 0, 8)),
        ]

    def test_returns_list(self):
        loader = _loader_in_memory([])
        loader._waypoints = self._make_named_waypoints()
        assert isinstance(loader.waypoint_names(), list)

    def test_sorted_alphabetically(self):
        loader = _loader_in_memory([])
        loader._waypoints = self._make_named_waypoints()
        names = loader.waypoint_names()
        assert names == sorted(names)

    def test_all_names_returned(self):
        loader = _loader_in_memory([])
        loader._waypoints = self._make_named_waypoints()
        names = loader.waypoint_names()
        assert len(names) == 3

    def test_filter_by_floor(self):
        loader = _loader_in_memory([])
        loader._waypoints = self._make_named_waypoints()
        names = loader.waypoint_names(floor=7)
        assert len(names) == 2
        assert all("Thais" in n for n in names)

    def test_filter_no_match_returns_empty(self):
        loader = _loader_in_memory([])
        loader._waypoints = self._make_named_waypoints()
        assert loader.waypoint_names(floor=9) == []

    def test_empty_waypoints_returns_empty(self):
        loader = _loader_in_memory([])
        loader._waypoints = []
        assert loader.waypoint_names() == []

    def test_names_are_strings(self):
        loader = _loader_in_memory([])
        loader._waypoints = self._make_named_waypoints()
        for name in loader.waypoint_names():
            assert isinstance(name, str)


# ─────────────────────────────────────────────────────────────────────────────
# has_cached_floors
# ─────────────────────────────────────────────────────────────────────────────

class TestLoaderHasCachedFloors:

    def test_false_when_empty(self):
        loader = _loader_in_memory([])
        assert loader.has_cached_floors is False

    def test_true_when_one_floor_cached(self):
        loader = _loader_in_memory([7])
        assert loader.has_cached_floors is True

    def test_true_when_multiple_floors(self):
        loader = _loader_in_memory([6, 7, 8])
        assert loader.has_cached_floors is True

    def test_false_after_clear_cache(self):
        loader = _loader_in_memory([7])
        loader.clear_cache()
        assert loader.has_cached_floors is False

    def test_consistent_with_loaded_count(self):
        loader = _loader_in_memory([7])
        assert loader.has_cached_floors == (loader.loaded_count > 0)


# ─────────────────────────────────────────────────────────────────────────────
# has_map_images
# ─────────────────────────────────────────────────────────────────────────────

class TestLoaderHasMapImages:

    def test_false_when_empty(self):
        loader = _loader_in_memory([])
        assert loader.has_map_images is False

    def test_true_after_adding_map_image(self):
        loader = _loader_in_memory([])
        loader._map_images["07"] = np.zeros((10, 10, 4), dtype=np.uint8)
        assert loader.has_map_images is True

    def test_false_after_clear_cache(self):
        loader = _loader_in_memory([])
        loader._map_images["07"] = np.zeros((10, 10, 4), dtype=np.uint8)
        loader.clear_cache()
        assert loader.has_map_images is False

    def test_returns_bool(self):
        loader = _loader_in_memory([])
        assert isinstance(loader.has_map_images, bool)

    def test_true_with_multiple_images(self):
        loader = _loader_in_memory([])
        loader._map_images["07"] = np.zeros((5, 5, 4), dtype=np.uint8)
        loader._map_images["08"] = np.zeros((5, 5, 4), dtype=np.uint8)
        assert loader.has_map_images is True


# ─────────────────────────────────────────────────────────────────────────────
# waypoints_loaded
# ─────────────────────────────────────────────────────────────────────────────

class TestLoaderWaypointsLoaded:

    def test_false_when_waypoints_is_none(self):
        loader = _loader_in_memory([])
        loader._waypoints = None
        assert loader.waypoints_loaded is False

    def test_true_when_waypoints_is_empty_list(self):
        loader = _loader_in_memory([])
        loader._waypoints = []
        assert loader.waypoints_loaded is True

    def test_true_when_waypoints_have_entries(self):
        loader = _loader_in_memory([])
        loader._waypoints = [Waypoint(name="A", coord=_coord(0, 0, 7))]
        assert loader.waypoints_loaded is True

    def test_false_after_clear_cache(self):
        loader = _loader_in_memory([])
        loader._waypoints = []
        loader.clear_cache()
        assert loader.waypoints_loaded is False

    def test_returns_bool(self):
        loader = _loader_in_memory([])
        assert isinstance(loader.waypoints_loaded, bool)


# ─────────────────────────────────────────────────────────────────────────────
# TibiaMapLoader.map_images_count
# ─────────────────────────────────────────────────────────────────────────────

class TestMapImagesCount:

    def test_zero_when_no_images_loaded(self):
        loader = _loader_in_memory([])
        assert loader.map_images_count == 0

    def test_one_after_adding_single_image(self):
        loader = _loader_in_memory([])
        loader._map_images["07"] = np.zeros((10, 10, 4), dtype=np.uint8)
        assert loader.map_images_count == 1

    def test_two_after_adding_two_images(self):
        loader = _loader_in_memory([])
        loader._map_images["07"] = np.zeros((10, 10, 4), dtype=np.uint8)
        loader._map_images["08"] = np.zeros((10, 10, 4), dtype=np.uint8)
        assert loader.map_images_count == 2

    def test_zero_after_clear_cache(self):
        loader = _loader_in_memory([])
        loader._map_images["07"] = np.zeros((10, 10, 4), dtype=np.uint8)
        loader.clear_cache()
        assert loader.map_images_count == 0

    def test_returns_int(self):
        loader = _loader_in_memory([])
        assert isinstance(loader.map_images_count, int)
