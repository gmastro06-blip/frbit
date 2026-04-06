"""
Input Bridge — Minimap con control de inputs en tiempo real.

Escucha WASD / flechas / click en la ventana pygame del minimap
y reenvía las pulsaciones a la ventana de destino (Tibia u otra app).

Modos de entrada:
  --mode intercept   : captura WASD/flechas del teclado, muestra en minimap,
                       reenvía (opcionalmente) a la ventana destino.
  --mode passthrough : igual pero también pasa las teclas al sistema.
  --mode click       : click en el minimap → reenvía click a la ventana destino.

Run:
    python examples/input_bridge.py
    python examples/input_bridge.py --target "Tibia" --mode intercept
    python examples/input_bridge.py --target "Tibia" --dry-run   # sin enviar nada
    python examples/input_bridge.py --list-windows               # lista ventanas abiertas
"""

import sys
import math
import time
import argparse
import threading
import queue
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from enum import Enum

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

# Log a archivo para supervisión en tiempo real
_LOG_PATH = project_root / "output" / "input_bridge.log"
_LOG_PATH.parent.mkdir(exist_ok=True)
_logfile = open(_LOG_PATH, "w", encoding="utf-8", buffering=1)

def _log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _logfile.write(line + "\n")

import pygame
from PIL import Image

from src.models import Coordinate
from src.navigator import WaypointNavigator
from src.map_loader import TibiaMapLoader
from src.input_controller import InputController, Key, find_window, list_windows

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
BOUNDS = {"xMin": 31744, "yMin": 30976}

C_BG     = (14, 14, 22)
C_PANEL  = (22, 22, 34)
C_BORDER = (55, 55, 85)
C_TEXT   = (220, 220, 240)
C_DIM    = (110, 110, 140)
C_ACCENT = (80, 160, 255)
C_GREEN  = (80, 220, 120)
C_YELLOW = (255, 210, 60)
C_RED    = (255, 80, 80)
C_ORANGE = (255, 150, 50)
C_CHAR   = (255, 255, 80)
C_CLICK_COL = (255, 120, 40)
C_SENT   = (100, 255, 160)

DIR_COLORS = {
    "up":    (80, 200, 255),
    "down":  (80, 200, 255),
    "left":  (80, 200, 255),
    "right": (80, 200, 255),
}

ARROW_CHARS = {"up": "↑", "down": "↓", "left": "←", "right": "→"}

# ─────────────────────────────────────────────────────────────────────────────
# Eventos de movimiento
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class MoveEvent:
    direction: str   # up / down / left / right
    source: str      # 'keyboard' | 'click'
    timestamp: float = field(default_factory=time.time)
    sent_to_target: bool = False

@dataclass
class ClickEffect:
    x: float; y: float
    age: float = 0.0; duration: float = 0.5; max_r: float = 16.0
    color: tuple = field(default_factory=lambda: C_CLICK_COL)

    @property
    def alive(self): return self.age < self.duration
    @property
    def t(self): return self.age / self.duration
    @property
    def r(self): return self.max_r * self.t
    @property
    def alpha(self): return int(255 * (1 - self.t))

# ─────────────────────────────────────────────────────────────────────────────
# Mapa en memoria
# ─────────────────────────────────────────────────────────────────────────────
class MapCache:
    def __init__(self, loader: TibiaMapLoader):
        self.loader = loader
        self._cache: Dict[int, Image.Image] = {}

    def get(self, floor: int) -> Image.Image:
        if floor not in self._cache:
            arr = self.loader.get_map_image(floor)
            from PIL import Image as PILImage
            self._cache[floor] = PILImage.fromarray(arr).convert("RGB")
        return self._cache[floor]

    def crop(self, floor: int, cx: int, cy: int, hw: int, hh: int) -> Image.Image:
        img = self.get(floor)
        px, py = cx - BOUNDS["xMin"], cy - BOUNDS["yMin"]
        w, h = img.size
        return img.crop((max(0, px-hw), max(0, py-hh), min(w, px+hw), min(h, py+hh)))

# ─────────────────────────────────────────────────────────────────────────────
# Input Bridge principal
# ─────────────────────────────────────────────────────────────────────────────
class InputBridge:
    MM = 420         # tamaño minimap px
    PW = 320         # ancho panel derecho
    WH = 580         # alto ventana

    ZOOMS  = [2, 3, 4, 6, 8, 10]
    SPEEDS = [0.5, 1.0, 2.0, 4.0]

    def __init__(
        self,
        ctrl: InputController,
        nav: WaypointNavigator,
        map_cache: MapCache,
        start: Coordinate,
        dry_run: bool = False,
        mode: str = "intercept",
    ):
        self.ctrl      = ctrl
        self.nav       = nav
        self.cache     = map_cache
        self.pos       = start
        self.dry_run   = dry_run
        self.mode      = mode

        self.floor     = start.z
        self.zoom_idx  = 2       # default 4x
        self.speed_idx = 1
        self.paused    = False
        self._running  = True

        self.moves: List[MoveEvent]      = []      # historial de movimientos
        self.effects: List[ClickEffect]  = []
        self.keys_held: set              = set()   # teclas pygame actualmente pulsadas

        self._event_q: "queue.Queue[MoveEvent]" = queue.Queue()
        self._stats = {"sent": 0, "discarded": 0, "clicks": 0}

    @property
    def zoom(self): return self.ZOOMS[self.zoom_idx]

    # ─────────────────────────────────────────────────────────────────────────
    def _tile_to_screen(self, tx, ty, cx, cy, ox=0, oy=0):
        half = self.MM // 2
        z    = self.zoom
        return (ox + half + int((tx - cx) * z), oy + half + int((ty - cy) * z))

    # ─────────────────────────────────────────────────────────────────────────
    def _screen_to_tile(self, sx, sy, cx, cy, ox=0, oy=0):
        """Convierte coords de pantalla (dentro del minimap) a tile absolutos."""
        half = self.MM // 2
        z    = self.zoom
        tx = cx + (sx - ox - half) / z
        ty = cy + (sy - oy - half) / z
        return int(tx), int(ty)

    # ─────────────────────────────────────────────────────────────────────────
    def _apply_move(self, direction: str, source: str = "keyboard"):
        """Mueve la posición local y envía a la ventana destino."""
        dx, dy = 0, 0
        if direction == "up":    dy = -1
        elif direction == "down":  dy =  1
        elif direction == "left":  dx = -1
        elif direction == "right": dx =  1

        # Mover posición local en el simulador
        self.pos = Coordinate(self.pos.x + dx, self.pos.y + dy, self.pos.z)

        ev = MoveEvent(direction=direction, source=source)

        # Enviar al destino
        if not self.dry_run and self.ctrl.is_connected():
            sent = self.ctrl.move(direction, steps=1, step_delay=0.08)
            ev.sent_to_target = sent
            if sent:
                self._stats["sent"] += 1
                _log(f"SEND  {direction.upper():<5}  → {self.ctrl.target_title}  pos=({self.pos.x},{self.pos.y})")
            else:
                self._stats["discarded"] += 1
                _log(f"FAIL  {direction.upper():<5}  hwnd no disponible")
        elif self.dry_run:
            ev.sent_to_target = False
            self._stats["sent"] += 1   # simulado
            _log(f"DRY   {direction.upper():<5}  pos=({self.pos.x},{self.pos.y})")

        self.moves.append(ev)
        if len(self.moves) > 60:
            self.moves.pop(0)

        # Efecto visual
        self.effects.append(ClickEffect(x=float(self.pos.x), y=float(self.pos.y),
                                         color=DIR_COLORS.get(direction, C_CLICK_COL)))

    # ─────────────────────────────────────────────────────────────────────────
    def _handle_click_on_minimap(self, sx: int, sy: int):
        """Click en el minimap → calcula dirección respecto a posición actual y envía."""
        tx, ty = self._screen_to_tile(sx, sy, self.pos.x, self.pos.y, ox=0)
        dx = tx - self.pos.x
        dy = ty - self.pos.y

        # Determinar dirección dominante
        if abs(dx) >= abs(dy):
            direction = "right" if dx > 0 else "left"
        else:
            direction = "down" if dy > 0 else "up"

        steps = max(1, max(abs(dx), abs(dy)))
        steps = min(steps, 20)   # límite de seguridad

        # Click en la ventana destino (coordenadas relativas al cliente)
        if not self.dry_run and self.ctrl.is_connected():
            rect = self.ctrl.get_window_rect()
            if rect:
                # Aproximar la posición en el cliente de Tibia donde está el tile
                # (centrar en la ventana destino + offset por tile)
                win_w = rect[2] - rect[0]
                win_h = rect[3] - rect[1]
                client_x = win_w  // 2 + dx * 2   # escala aproximada 2px/tile
                client_y = win_h // 2 + dy * 2
                self.ctrl.click(client_x, client_y)
                self._stats["clicks"] += 1
                _log(f"CLICK ({client_x},{client_y}) en ventana → dir={direction} steps={steps}")

        # También mover en el sim
        for _ in range(steps):
            d = "right" if dx > 0 else ("left" if dx < 0 else ("down" if dy > 0 else "up"))
            self._apply_move(d, source="click")

    # ─────────────────────────────────────────────────────────────────────────
    def _draw_minimap(self, surf: pygame.Surface):
        z = self.zoom
        half_t = self.MM // (2 * z) + 2
        cx, cy = self.pos.x, self.pos.y

        # Fondo
        pygame.draw.rect(surf, (8, 8, 14), (0, 0, self.MM, self.WH))

        # Imagen del mapa
        try:
            crop = self.cache.crop(self.floor, cx, cy, half_t, half_t)
            rw, rh = crop.width * z, crop.height * z
            _res = getattr(Image, 'Resampling', Image).LANCZOS
            resized = crop.resize((rw, rh), _res)
            pg_s = pygame.image.fromstring(resized.tobytes(), resized.size, resized.mode)  # type: ignore[arg-type]
            bx = self.MM // 2 - rw // 2
            by = (self.WH - self.MM) // 2 + self.MM // 2 - rh // 2
            surf.blit(pg_s, (bx, by))
        except Exception:
            pass

        mm_y = (self.WH - self.MM) // 2

        # Cruz de referencia
        pygame.draw.line(surf, (*C_BORDER, 80), (0, mm_y + self.MM//2), (self.MM, mm_y + self.MM//2), 1)
        pygame.draw.line(surf, (*C_BORDER, 80), (self.MM//2, mm_y), (self.MM//2, mm_y + self.MM), 1)

        # Efectos de click / movimiento
        for eff in self.effects:
            ex, ey = self._tile_to_screen(eff.x, eff.y, cx, cy, 0, mm_y)
            r = int(eff.r)
            if r > 0:
                s = pygame.Surface((r*2+4, r*2+4), pygame.SRCALPHA)
                pygame.draw.circle(s, (*eff.color, eff.alpha), (r+2, r+2), r, 2)
                surf.blit(s, (ex-r-2, ey-r-2))

        # Personaje
        px, py = self.MM//2, mm_y + self.MM//2
        pulse = 4 + int(2 * math.sin(time.time() * 6))
        pygame.draw.circle(surf, C_CHAR,  (px, py), pulse)
        pygame.draw.circle(surf, (255,255,255), (px, py), pulse, 1)

        # Indicador de dirección si hay tecla pulsada
        for d in self.keys_held:
            arr = {"up": (0,-1), "down": (0,1), "left": (-1,0), "right": (1,0)}.get(d, (0,0))
            ex = px + arr[0] * 20
            ey = py + arr[1] * 20
            pygame.draw.line(surf, C_ACCENT, (px, py), (ex, ey), 3)
            pygame.draw.circle(surf, C_ACCENT, (ex, ey), 5)

        # Borde
        pygame.draw.rect(surf, C_BORDER, (0, mm_y, self.MM, self.MM), 2)

    # ─────────────────────────────────────────────────────────────────────────
    def _draw_panel(self, surf: pygame.Surface, font_lg, font_md, font_sm):
        px = self.MM + 4
        pygame.draw.rect(surf, C_PANEL, (px, 0, self.PW, self.WH))
        pygame.draw.rect(surf, C_BORDER, (px, 0, self.PW, self.WH), 1)

        x = px + 10
        y = 10

        def t(text, col=C_TEXT, f=font_md):
            s = f.render(text, True, col)
            surf.blit(s, (x, y))

        def sep():
            nonlocal y
            y += 4
            pygame.draw.line(surf, C_BORDER, (x, y), (x + self.PW - 20, y), 1)
            y += 6

        t("INPUT BRIDGE", C_ACCENT, font_lg); y += 28
        sep()

        # Estado conexión
        connected = self.ctrl.is_connected()
        target_txt = self.ctrl.target_title
        if self.dry_run:
            t(f"MODO: DRY-RUN", C_YELLOW); y += 18
        elif connected:
            t(f"→ {target_txt[:20]}", C_GREEN); y += 18
        else:
            t(f"[!] {target_txt[:18]} no encontrado", C_RED); y += 18

        mode_col = C_ACCENT if self.mode == "intercept" else C_ORANGE
        t(f"Mode: {self.mode}", mode_col); y += 18
        sep()

        # Posición
        t(f"X {self.pos.x:5d}  Y {self.pos.y:5d}", C_TEXT); y += 18
        t(f"Floor {self.floor:02d}  Zoom {self.zoom}x", C_DIM); y += 20
        sep()

        # Stats
        t("Enviados:", C_DIM, font_sm); y += 14
        t(f"  Teclas: {self._stats['sent']}", C_GREEN, font_sm); y += 13
        t(f"  Clicks: {self._stats['clicks']}", C_GREEN, font_sm); y += 13
        t(f"  Descartados: {self._stats['discarded']}", C_ORANGE, font_sm); y += 16
        sep()

        # Últimas teclas enviadas
        t("Últimas acciones:", C_DIM, font_sm); y += 14
        recent = self.moves[-8:]
        recent_rev = list(reversed(recent))
        for ev in recent_rev:
            arrow  = ARROW_CHARS.get(ev.direction, ev.direction)
            status = "✓" if ev.sent_to_target else ("○" if self.dry_run else "✗")
            src    = "KB" if ev.source == "keyboard" else "CK"
            col    = C_SENT if ev.sent_to_target or self.dry_run else C_ORANGE
            t(f"  {arrow} {ev.direction:<5}  [{src}]  {status}", col, font_sm)
            y += 13
            if y > self.WH - 100:
                break

        # Log del controller
        sep()
        t("Log controller:", C_DIM, font_sm); y += 13
        for line in self.ctrl.get_log(3):
            short = line[-38:] if len(line) > 38 else line
            t(f"  {short}", C_DIM, font_sm); y += 12

        # Controles
        y = self.WH - 80
        sep()
        controls = [
            "WASD / ↑↓←→  mover",
            "+/-  zoom    ↑↓*  speed",
            "Click  enviar click",
            "F  buscar ventana  ESC salir",
        ]
        for c in controls:
            t(c, C_DIM, font_sm); y += 13

    # ─────────────────────────────────────────────────────────────────────────
    def run(self):
        pygame.init()
        W = self.MM + self.PW + 4
        screen = pygame.display.set_mode((W, self.WH))
        pygame.display.set_caption("Input Bridge — Tibia Waypoint Controller")
        clock = pygame.time.Clock()

        # Registrar hwnd propio para que el controller no se apunte a sí mismo
        try:
            self.ctrl._own_hwnd = pygame.display.get_wm_info().get("window")
        except Exception:
            pass

        try:
            font_lg = pygame.font.SysFont("Consolas", 15, bold=True)
            font_md = pygame.font.SysFont("Consolas", 13)
            font_sm = pygame.font.SysFont("Consolas", 11)
        except Exception:
            font_lg = font_md = font_sm = pygame.font.Font(None, 13)

        # Mapa del piso inicial
        _log(f"  Cargando mapa piso {self.floor:02d} …")
        self.cache.get(self.floor)
        _log("  Mapa listo.")
        status_msg = "Ventana abierta"

        # Conectar al destino
        if not self.dry_run:
            w = self.ctrl.find_target()
            if w:
                status_msg = f"Conectado → {w.title}"
            else:
                status_msg = f"Ventana '{self.ctrl.target_title}' no encontrada (dry-run)"

        _log(f"  {status_msg}")
        _log("  WASD/flechas para mover. Click en el minimap para enviar click. ESC para salir.")
        _log(f"  Dry-run: {self.dry_run}")

        last_move_time: Dict[str, float] = {}
        MOVE_REPEAT_DELAY = 0.12   # segundos entre repeticiones al mantener pulsado

        _last = time.time()

        while self._running:
            now = time.time()
            dt  = min(now - _last, 0.1)
            _last = now

            # ── Eventos pygame ──
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self._running = False

                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        self._running = False
                    elif ev.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                        self.zoom_idx = min(self.zoom_idx + 1, len(self.ZOOMS) - 1)
                    elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        self.zoom_idx = max(self.zoom_idx - 1, 0)
                    elif ev.key == pygame.K_f:
                        # Re-buscar ventana destino
                        w = self.ctrl.find_target()
                        _log(f"  Re-scan: {w}")
                    elif ev.key == pygame.K_p:
                        self.paused = not self.paused

                    # Movimiento inmediato al KEYDOWN
                    d = _pygame_key_to_dir(ev.key)
                    if d:
                        self.keys_held.add(d)
                        self._apply_move(d, source="keyboard")
                        last_move_time[d] = now

                elif ev.type == pygame.KEYUP:
                    d = _pygame_key_to_dir(ev.key)
                    if d:
                        self.keys_held.discard(d)

                elif ev.type == pygame.MOUSEBUTTONDOWN:
                    mx, my = ev.pos
                    mm_y = (self.WH - self.MM) // 2
                    if 0 <= mx < self.MM and mm_y <= my < mm_y + self.MM:
                        self._handle_click_on_minimap(mx, my - mm_y)

            # ── Repetición de teclas mantenidas ──
            for d in list(self.keys_held):
                if now - last_move_time.get(d, 0) >= MOVE_REPEAT_DELAY:
                    self._apply_move(d, source="keyboard")
                    last_move_time[d] = now

            # ── Actualizar efectos ──
            for eff in self.effects:
                eff.age += dt
            self.effects = [e for e in self.effects if e.alive]

            # ── Dibujo ──
            screen.fill(C_BG)
            self._draw_minimap(screen)
            self._draw_panel(screen, font_lg, font_md, font_sm)

            # Título superior
            conn_dot = "●" if (self.ctrl.is_connected() or self.dry_run) else "○"
            conn_col = C_GREEN if (self.ctrl.is_connected() or self.dry_run) else C_RED
            dot_surf = font_lg.render(conn_dot, True, conn_col)
            screen.blit(dot_surf, (6, 4))
            title_surf = font_lg.render(
                f"  Floor {self.floor:02d}  |  Zoom {self.zoom}x  |  ({self.pos.x},{self.pos.y})",
                True, C_DIM
            )
            screen.blit(title_surf, (6, 4))

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _pygame_key_to_dir(key: int) -> Optional[str]:
    return {
        pygame.K_UP:    "up",
        pygame.K_DOWN:  "down",
        pygame.K_LEFT:  "left",
        pygame.K_RIGHT: "right",
        pygame.K_w:     "up",
        pygame.K_s:     "down",
        pygame.K_a:     "left",
        pygame.K_d:     "right",
    }.get(key)


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Input Bridge — Minimap Controller")
    ap.add_argument("--target",       default="Tibia",       help="Título (o fragmento) de la ventana destino")
    ap.add_argument("--mode",         default="intercept",   choices=["intercept", "passthrough", "click"],
                    help="Modo de operación")
    ap.add_argument("--move-mode",    default="arrow",       choices=["arrow", "wasd"],
                    help="Tipo de teclas enviadas (arrow keys o WASD)")
    ap.add_argument("--floor",        type=int, default=7,   help="Piso inicial")
    ap.add_argument("--x",            type=int, default=32369)
    ap.add_argument("--y",            type=int, default=32241)
    ap.add_argument("--dry-run",      action="store_true",   help="No enviar inputs reales")
    ap.add_argument("--list-windows", action="store_true",   help="Listar ventanas y salir")
    ap.add_argument("--key-delay",    type=float, default=0.05)
    args = ap.parse_args()

    if args.list_windows:
        print("\nVentanas abiertas:")
        for w in list_windows():
            print(f"  [{w.hwnd:>8}]  {w.title}")
        return

    _log("=" * 60)
    _log("  Input Bridge — iniciando")
    _log("=" * 60)
    _log(f"  Target    : {args.target}")
    _log(f"  Mode      : {args.mode}")
    _log(f"  Move keys : {args.move_mode}")
    _log(f"  Dry-run   : {args.dry_run}")
    _log(f"  Start pos : ({args.x}, {args.y}, floor {args.floor})")

    nav    = WaypointNavigator()
    loader = nav.loader
    cache  = MapCache(loader)
    start  = Coordinate(args.x, args.y, args.floor)

    ctrl = InputController(
        target_title=args.target,
        key_delay=args.key_delay,
        move_mode=args.move_mode,
    )

    bridge = InputBridge(
        ctrl=ctrl,
        nav=nav,
        map_cache=cache,
        start=start,
        dry_run=args.dry_run,
        mode=args.mode,
    )
    bridge.run()


if __name__ == "__main__":
    main()
