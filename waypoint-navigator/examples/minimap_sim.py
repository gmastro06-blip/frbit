"""
Minimap Simulator — ventana en tiempo real con el mapa real de Tibia,
animación de movimiento del personaje y efectos de click sobre el minimap.

Run:
    python examples/minimap_sim.py
    python examples/minimap_sim.py --start "thais depot" --end "thais temple"
    python examples/minimap_sim.py --script "path/to/waypoints.in"

Controles:
    SPACE       pausar / reanudar
    +/-         zoom del minimap  (1x – 12x)
    UP/DOWN     velocidad de simulación
    R           reiniciar ruta
    ESC / Q     salir
"""
import sys
import math
import time
import argparse
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

import pygame
import numpy as np
from PIL import Image

from src.models import Coordinate
from src.navigator import WaypointNavigator
from src.map_loader import TibiaMapLoader

# Bounds del mapa Tibia
BOUNDS = {"xMin": 31744, "yMin": 30976}

# ─────────────────────────────────────────────────────────────────────────────
# Colores
# ─────────────────────────────────────────────────────────────────────────────
C_BG        = (18, 18, 28)
C_PANEL     = (24, 24, 38)
C_BORDER    = (60, 60, 90)
C_TEXT      = (220, 220, 240)
C_DIM       = (120, 120, 150)
C_ACCENT    = (80, 160, 255)
C_GREEN     = (80, 220, 120)
C_YELLOW    = (255, 210, 60)
C_RED       = (255, 80, 80)
C_ORANGE    = (255, 150, 50)
C_PATH      = (80, 160, 255, 160)    # RGBA para la ruta
C_CHAR      = (255, 255, 80)
C_CLICK     = (255, 120, 40)
C_DEST      = (80, 255, 150)

# ─────────────────────────────────────────────────────────────────────────────
# Efectos de click
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ClickEffect:
    px: float           # posición en pantalla (minimap coords)
    py: float
    age: float = 0.0    # segundos desde creación
    duration: float = 0.55
    max_radius: float = 18.0
    color: tuple = field(default_factory=lambda: C_CLICK)

    @property
    def alive(self) -> bool:
        return self.age < self.duration

    @property
    def progress(self) -> float:
        return min(self.age / self.duration, 1.0)

    @property
    def radius(self) -> float:
        return self.max_radius * self.progress

    @property
    def alpha(self) -> int:
        return int(255 * (1.0 - self.progress))

# ─────────────────────────────────────────────────────────────────────────────
# Carga de waypoints del script .in (simplificada — solo nodes con coord)
# ─────────────────────────────────────────────────────────────────────────────
def load_script_nodes(script_path: str) -> List[Coordinate]:
    """Lee un .in y devuelve solo los nodos de movimiento con coordenadas."""
    from src.script_parser import ScriptParser
    instructions = ScriptParser.parse_file(Path(script_path))
    coords = []
    for inst in instructions:
        if inst.coord is not None and inst.kind in ("node", "stand", "ladder", "rope", "shovel"):
            c = inst.coord
            coords.append(Coordinate(c.x, c.y, c.z))
    return coords

# ─────────────────────────────────────────────────────────────────────────────
# Caché de tiles del mapa como superficie pygame
# ─────────────────────────────────────────────────────────────────────────────
class MapTileCache:
    """Mantiene en memoria la imagen PIL del piso cargado (mapa visual)."""

    def __init__(self, loader: TibiaMapLoader):
        self.loader = loader
        self._cache: dict = {}

    def get_floor_image(self, floor: int) -> Image.Image:
        """Devuelve la imagen PIL del mapa visual del piso."""
        if floor not in self._cache:
            # get_map_image devuelve numpy RGBA array
            arr = self.loader.get_map_image(floor)
            self._cache[floor] = Image.fromarray(arr).convert("RGB")
        return self._cache[floor]

    def get_crop(self, floor: int, cx_abs: int, cy_abs: int, half_w: int, half_h: int) -> Image.Image:
        """Recorte centrado en (cx_abs, cy_abs) coordenadas absolutas Tibia."""
        img = self.get_floor_image(floor)
        # Convertir a coords de pixel en PNG
        px = cx_abs - BOUNDS["xMin"]
        py = cy_abs - BOUNDS["yMin"]
        w, h = img.size
        x0 = max(0, px - half_w)
        y0 = max(0, py - half_h)
        x1 = min(w, px + half_w)
        y1 = min(h, py + half_h)
        return img.crop((x0, y0, x1, y1))

# ─────────────────────────────────────────────────────────────────────────────
# Simulador principal
# ─────────────────────────────────────────────────────────────────────────────
class MinimapSimulator:
    MINIMAP_SIZE = 400          # píxeles de la ventana del minimap
    HUD_WIDTH    = 300          # ancho panel derecho
    WINDOW_H     = 560

    SPEEDS = [0.5, 1.0, 2.0, 4.0, 8.0]  # pasos / segundo
    ZOOMS  = [1, 2, 3, 4, 6, 8, 10, 12]

    def __init__(self, route: List[Coordinate], nav: WaypointNavigator,
                 tile_cache: MapTileCache, script_nodes: Optional[List[Coordinate]] = None):
        self.route        = route          # ruta A* interpolada
        self.script_nodes = script_nodes or []
        self.nav          = nav
        self.tile_cache   = tile_cache

        self.step_idx   = 0
        self.paused     = False
        self.zoom_idx   = 3                # default zoom=4
        self.speed_idx  = 1               # default 1 paso/s
        self.click_effects: List[ClickEffect] = []
        self._interp_t  = 0.0             # interpolación suave entre pasos
        self._last_tick = time.time()
        self._running   = True

        # Waypoints cercanos (se refresca cada 5 pasos)
        self._nearby_cache: list = []
        self._nearby_step = -99

    # ── posición interpolada del personaje ──
    @property
    def char_pos(self) -> Tuple[float, float]:
        """Posición interpolada (x, y) en coords de tile."""
        if len(self.route) == 0:
            return (0.0, 0.0)
        if self.step_idx >= len(self.route) - 1:
            c = self.route[-1]
            return (float(c.x), float(c.y))
        c0 = self.route[self.step_idx]
        c1 = self.route[self.step_idx + 1]
        t  = self._interp_t
        return (c0.x + (c1.x - c0.x) * t, c0.y + (c1.y - c0.y) * t)

    @property
    def current_coord(self) -> Coordinate:
        idx = min(self.step_idx, len(self.route) - 1)
        return self.route[idx]

    @property
    def zoom(self) -> int:
        return self.ZOOMS[self.zoom_idx]

    @property
    def speed(self) -> float:
        return self.SPEEDS[self.speed_idx]

    # ─────────────────────────────────────────────────────────────────────────
    def _update(self, dt: float):
        if self.paused or self.step_idx >= len(self.route) - 1:
            return

        self._interp_t += dt * self.speed
        if self._interp_t >= 1.0:
            self._interp_t -= 1.0
            self.step_idx += 1
            # Spawn click effect al llegar al siguiente tile
            self._spawn_click()

        # Actualizar efectos
        for eff in self.click_effects:
            eff.age += dt
        self.click_effects = [e for e in self.click_effects if e.alive]

    def _spawn_click(self):
        """Genera un efecto de click en el tile destino en coords de pantalla."""
        # Se convierte después en draw — guardamos en tile coords
        c = self.current_coord
        self.click_effects.append(ClickEffect(px=float(c.x), py=float(c.y)))

    # ─────────────────────────────────────────────────────────────────────────
    def _tile_to_screen(self, tx: float, ty: float, cx: float, cy: float,
                         mm_x: int, mm_y: int) -> Tuple[int, int]:
        """Convierte tile coords a píxeles en pantalla dentro del minimap."""
        z = self.zoom
        half = self.MINIMAP_SIZE // 2
        sx = mm_x + half + int((tx - cx) * z)
        sy = mm_y + half + int((ty - cy) * z)
        return (sx, sy)

    # ─────────────────────────────────────────────────────────────────────────
    def _draw_minimap(self, surf: pygame.Surface, mm_x: int, mm_y: int):
        z    = self.zoom
        size = self.MINIMAP_SIZE
        half_tiles = size // (2 * z) + 2   # tiles visibles en cada dirección

        cx, cy = self.char_pos
        floor   = self.current_coord.z

        # Recorte del mapa real
        try:
            crop = self.tile_cache.get_crop(floor, int(cx), int(cy), half_tiles, half_tiles)
            _resample = getattr(Image, 'Resampling', Image).NEAREST
            crop_resized = crop.resize(
                (crop.width * z, crop.height * z),
                _resample
            )
            mode = crop_resized.mode
            raw  = crop_resized.tobytes()
            pg_surf = pygame.image.fromstring(raw, crop_resized.size, mode)  # type: ignore[arg-type]
            # Centrar en el panel
            blit_x = mm_x + size // 2 - crop_resized.width  // 2
            blit_y = mm_y + size // 2 - crop_resized.height // 2
            surf.blit(pg_surf, (blit_x, blit_y))
        except Exception:
            pass  # Si falla la carga, fondo negro

        # ── Dibujar ruta (línea) ──
        route_surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pts = []
        for c in self.route:
            sx, sy = self._tile_to_screen(c.x, c.y, cx, cy, 0, 0)
            pts.append((sx, sy))

        if len(pts) >= 2:
            # Segmento ya recorrido (gris)
            done_pts = pts[:self.step_idx + 1]
            todo_pts = pts[self.step_idx:]
            if len(done_pts) >= 2:
                pygame.draw.lines(route_surf, (160, 160, 160, 120), False, done_pts, 1)
            if len(todo_pts) >= 2:
                pygame.draw.lines(route_surf, (80, 160, 255, 200), False, todo_pts, 2)

        surf.blit(route_surf, (mm_x, mm_y))

        # ── Destino final ──
        if self.route:
            end = self.route[-1]
            ex, ey = self._tile_to_screen(end.x, end.y, cx, cy, mm_x, mm_y)
            pygame.draw.circle(surf, C_DEST, (ex, ey), 5)
            pygame.draw.circle(surf, C_TEXT, (ex, ey), 5, 1)

        # ── Efectos de click ──
        for eff in self.click_effects:
            ex, ey = self._tile_to_screen(eff.px, eff.py, cx, cy, mm_x, mm_y)  # ya en coords abs
            alpha  = eff.alpha
            r      = int(eff.radius)
            if r > 0:
                click_surf = pygame.Surface((r * 2 + 4, r * 2 + 4), pygame.SRCALPHA)
                pygame.draw.circle(click_surf, (*eff.color, alpha), (r + 2, r + 2), r, 2)
                surf.blit(click_surf, (ex - r - 2, ey - r - 2))

        # ── Personaje (centro) ──
        char_sx = mm_x + size // 2
        char_sy = mm_y + size // 2
        # Pulso animado
        pulse_r = 4 + int(2 * math.sin(time.time() * 6))
        pygame.draw.circle(surf, C_CHAR,   (char_sx, char_sy), pulse_r)
        pygame.draw.circle(surf, (255, 255, 255), (char_sx, char_sy), pulse_r, 1)

        # ── Cruz de referencia ──
        pygame.draw.line(surf, (*C_BORDER, 100), (mm_x, mm_y + size // 2), (mm_x + size, mm_y + size // 2), 1)
        pygame.draw.line(surf, (*C_BORDER, 100), (mm_x + size // 2, mm_y), (mm_x + size // 2, mm_y + size), 1)

        # ── Borde del minimap ──
        pygame.draw.rect(surf, C_BORDER, (mm_x, mm_y, size, size), 2)

    # ─────────────────────────────────────────────────────────────────────────
    def _draw_hud(self, surf: pygame.Surface, font_lg, font_md, font_sm,
                  hud_x: int, hud_y: int):
        w = self.HUD_WIDTH
        h = self.WINDOW_H
        # Fondo panel
        pygame.draw.rect(surf, C_PANEL, (hud_x, hud_y, w, h))
        pygame.draw.rect(surf, C_BORDER, (hud_x, hud_y, w, h), 1)

        x = hud_x + 12
        y = hud_y + 12

        def txt(text, color=C_TEXT, font=font_md, ox=0):
            s = font.render(text, True, color)
            surf.blit(s, (x + ox, y))

        # Título
        txt("MINIMAP SIM", C_ACCENT, font_lg)
        y += 30

        pygame.draw.line(surf, C_BORDER, (x, y), (x + w - 24, y), 1)
        y += 8

        # Coordenadas + piso
        cx, cy = self.char_pos
        coord  = self.current_coord
        txt(f"X: {int(cx):5d}  Y: {int(cy):5d}", C_TEXT)
        y += 20
        txt(f"Floor: {coord.z:02d}", C_YELLOW)
        y += 24

        # Progreso de ruta
        total = max(len(self.route) - 1, 1)
        pct   = self.step_idx / total
        txt(f"Paso  {self.step_idx:3d} / {total}", C_TEXT)
        y += 20
        bar_w = w - 24
        pygame.draw.rect(surf, C_BORDER, (x, y, bar_w, 8), 1)
        pygame.draw.rect(surf, C_ACCENT, (x, y, int(bar_w * pct), 8))
        y += 16

        pygame.draw.line(surf, C_BORDER, (x, y), (x + w - 24, y), 1)
        y += 8

        # Zoom + velocidad
        txt(f"Zoom: {self.zoom}x", C_DIM)
        y += 18
        txt(f"Speed: {self.speed}x", C_DIM)
        y += 18

        # Estado
        state_txt = "[ PAUSA ]" if self.paused else "► CORRIENDO"
        state_col  = C_ORANGE if self.paused else C_GREEN
        txt(state_txt, state_col)
        y += 24

        pygame.draw.line(surf, C_BORDER, (x, y), (x + w - 24, y), 1)
        y += 8

        # Waypoints cercanos
        txt("Waypoints cercanos:", C_DIM, font_sm)
        y += 16

        # Refrescar cada 5 pasos
        if abs(self.step_idx - self._nearby_step) >= 5:
            self._nearby_cache = self.nav.nearest_waypoint(coord, top_n=6)
            self._nearby_step  = self.step_idx

        for wp in self._nearby_cache[:6]:
            # Waypoint tiene wp.coord (Coordinate) y wp.name
            wp_coord = wp.coord if hasattr(wp, "coord") else coord
            dist     = wp_coord.distance_to(coord)
            name     = wp.name if hasattr(wp, "name") else str(wp)
            if len(name) > 22:
                name = name[:20] + "…"
            col = C_YELLOW if dist < 15 else C_TEXT
            txt(f"{name} ({dist:.0f})", col, font_sm)
            y += 15
            if y > hud_y + h - 90:
                break

        y = hud_y + h - 70
        pygame.draw.line(surf, C_BORDER, (x, y), (x + w - 24, y), 1)
        y += 8

        # Controles
        controls = [
            "SPACE  pausa/reanudar",
            "+/-    zoom | ↑↓ speed",
            "R  reiniciar  |  ESC salir",
        ]
        for ctrl in controls:
            txt(ctrl, C_DIM, font_sm)
            y += 14

    # ─────────────────────────────────────────────────────────────────────────
    def run(self):
        pygame.init()
        pygame.display.set_caption("Minimap Simulator — Tibia Waypoint Navigator")

        W = self.MINIMAP_SIZE + self.HUD_WIDTH + 4
        H = self.WINDOW_H
        screen = pygame.display.set_mode((W, H))
        clock  = pygame.time.Clock()

        try:
            font_lg = pygame.font.SysFont("Consolas", 16, bold=True)
            font_md = pygame.font.SysFont("Consolas", 13)
            font_sm = pygame.font.SysFont("Consolas", 11)
        except Exception:
            font_lg = pygame.font.Font(None, 18)
            font_md = pygame.font.Font(None, 14)
            font_sm = pygame.font.Font(None, 12)

        MM_X = 0
        MM_Y = (H - self.MINIMAP_SIZE) // 2
        HUD_X = self.MINIMAP_SIZE + 4

        print("  Ventana abierta. Controles: SPACE=pausa, +/-=zoom, ↑↓=speed, R=reiniciar, ESC=salir")

        while self._running:
            now = time.time()
            dt  = min(now - self._last_tick, 0.1)
            self._last_tick = now

            # ── Eventos ──
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self._running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        self._running = False
                    elif ev.key == pygame.K_SPACE:
                        self.paused = not self.paused
                    elif ev.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                        self.zoom_idx = min(self.zoom_idx + 1, len(self.ZOOMS) - 1)
                    elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        self.zoom_idx = max(self.zoom_idx - 1, 0)
                    elif ev.key == pygame.K_UP:
                        self.speed_idx = min(self.speed_idx + 1, len(self.SPEEDS) - 1)
                    elif ev.key == pygame.K_DOWN:
                        self.speed_idx = max(self.speed_idx - 1, 0)
                    elif ev.key == pygame.K_r:
                        self.step_idx   = 0
                        self._interp_t  = 0.0
                        self.click_effects.clear()
                        self.paused = False

            # ── Update ──
            self._update(dt)

            # ── Draw ──
            screen.fill(C_BG)
            # Fondo del minimap
            pygame.draw.rect(screen, (10, 10, 16), (MM_X, MM_Y, self.MINIMAP_SIZE, self.MINIMAP_SIZE))

            self._draw_minimap(screen, MM_X, MM_Y)
            self._draw_hud(screen, font_lg, font_md, font_sm, HUD_X, 0)

            # Título superior
            title_surf = font_lg.render(
                f"Floor {self.current_coord.z:02d}  |  Zoom {self.zoom}x  |  {len(self.route)-1} pasos totales",
                True, C_DIM
            )
            screen.blit(title_surf, (8, 4))

            # Mensaje fin
            if self.step_idx >= len(self.route) - 1 and len(self.route) > 1:
                end_surf = font_lg.render("¡DESTINO ALCANZADO!", True, C_GREEN)
                screen.blit(end_surf, (self.MINIMAP_SIZE // 2 - end_surf.get_width() // 2 + MM_X,
                                        MM_Y + self.MINIMAP_SIZE - 30))

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Minimap Simulator")
    ap.add_argument("--start",  default="thais depot",  help="Origen (nombre waypoint)")
    ap.add_argument("--end",    default="thais temple", help="Destino (nombre waypoint)")
    ap.add_argument("--floor",  type=int, default=7,    help="Piso (default 7)")
    ap.add_argument("--script", default=None,            help="Ruta a .in para usar sus nodos")
    ap.add_argument("--zoom",   type=int, default=4,     help="Zoom inicial 1-12")
    ap.add_argument("--speed",  type=float, default=1.0, help="Velocidad inicial pasos/s")
    args = ap.parse_args()

    print("=" * 60)
    print("  Minimap Simulator — iniciando")
    print("=" * 60)

    nav    = WaypointNavigator()
    loader: TibiaMapLoader = nav.loader
    cache  = MapTileCache(loader)

    route: List[Coordinate] = []
    script_nodes: List[Coordinate] = []

    if args.script:
        print(f"  Cargando script: {args.script}")
        try:
            script_nodes = load_script_nodes(args.script)
            if len(script_nodes) >= 2:
                # Encadenar A* entre nodos consecutivos del script
                print(f"  {len(script_nodes)} nodos encontrados. Calculando ruta A* completa …")
                floor = script_nodes[0].z
                # Solo segmentar el piso mayoritario para la demo
                floor_nodes = [n for n in script_nodes if n.z == floor]
                if len(floor_nodes) >= 2:
                    for i in range(len(floor_nodes) - 1):
                        seg = nav.navigate(floor_nodes[i], floor_nodes[i + 1])
                        if seg.found:
                            route.extend(seg.steps)
                        else:
                            route.append(floor_nodes[i])
                    if not route:
                        route = floor_nodes
                else:
                    route = script_nodes
            else:
                print("  Pocos nodos — usando ruta por defecto")
        except Exception as e:
            print(f"  Error cargando script: {e}")

    if not route:
        # Ruta por nombre
        print(f"  Calculando ruta: '{args.start}' → '{args.end}' (floor {args.floor})")
        try:
            result = nav.navigate_by_name(args.start, args.end, floor=args.floor)
            route  = result.steps or []
        except Exception:
            pass

        if not route:
            # Hardcoded Thais fallback
            START = Coordinate(32369, 32241, 7)
            END   = Coordinate(32344, 32219, 7)
            print(f"  Fallback: {START} → {END}")
            result = nav.navigate(START, END)
            route  = result.steps or [START, END]

    print(f"  Ruta: {len(route)} pasos | Floor {route[0].z}")

    # Pre-cargar imagen del mapa
    floor = route[0].z
    print(f"  Pre-cargando mapa del piso {floor:02d} …")
    cache.get_floor_image(floor)
    print("  Mapa listo.")

    # Ajustar zoom/speed
    sim = MinimapSimulator(route, nav, cache, script_nodes)
    if args.zoom in sim.ZOOMS:
        sim.zoom_idx = sim.ZOOMS.index(args.zoom)
    if args.speed in sim.SPEEDS:
        sim.speed_idx = sim.SPEEDS.index(args.speed)

    sim.run()


if __name__ == "__main__":
    main()
