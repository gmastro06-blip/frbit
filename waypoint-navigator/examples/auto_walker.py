"""
Auto Walker — navega automáticamente de un punto a otro enviando
arrow keys a la ventana de Tibia.

Uso:
    python examples/auto_walker.py --dest "thais depot"
    python examples/auto_walker.py --dest "thais depot" --x 32351 --y 32222
    python examples/auto_walker.py --dest "thais temple" --dry-run
    python examples/auto_walker.py --script "path/to/waypoints.in"  # modo full-bot

Controles ventana:
    SPACE   pausar / reanudar
    ESC     abortar
    R       reiniciar desde el principio
"""

import sys
import json
import math
import time
import random
import argparse
import threading
from pathlib import Path
from typing import Any, List, Optional, Dict
from dataclasses import dataclass, field

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

_LOG_PATH     = project_root / "output" / "auto_walker.log"
_RUNTIME_LOG  = project_root / "output" / "runtime.log"
_FATAL_LOG    = project_root / "output" / "fatal.log"
_LOG_PATH.parent.mkdir(exist_ok=True)
_logfile = open(_LOG_PATH, "w", encoding="utf-8", buffering=1)
_rtfile  = open(_RUNTIME_LOG, "w", encoding="utf-8", buffering=1)

def _log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _logfile.write(line + "\n")

def _append_runtime(entry: dict) -> None:
    """Appends one JSONL record to runtime.log (non-blocking, best-effort)."""
    try:
        _rtfile.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception:
        pass

def _write_fatal(exc: BaseException, tb_str: str) -> None:
    """Writes fatal.log as a single atomic JSON file on unhandled walk error."""
    payload = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "exc_type": type(exc).__name__,
        "exc_msg": str(exc),
        "traceback": tb_str,
    }
    tmp = str(_FATAL_LOG) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as _f:
            json.dump(payload, _f, indent=2)
        import os as _os
        _os.replace(tmp, _FATAL_LOG)
    except Exception:
        pass

import pygame
from PIL import Image

from src.models import Coordinate
from src.navigator import WaypointNavigator
from src.map_loader import TibiaMapLoader
from src.input_controller import InputController, Key, find_window, list_windows
from src.character_detector import CharacterDetector, DetectorConfig
from src.hpmp_detector import HpMpDetector, HpMpConfig
from src.script_parser import ScriptParser, Instruction
try:
    from src.depot_manager import DepotManager, DepotConfig
except ImportError:
    DepotManager = None  # type: ignore
    DepotConfig  = None  # type: ignore
try:
    from src.minimap_radar import MinimapRadar, MinimapConfig as MinimapCfg
except ImportError:
    MinimapRadar = None  # type: ignore
    MinimapCfg   = None  # type: ignore

try:
    from src.combat_manager import CombatManager, CombatConfig
    from src.looter import Looter, LootConfig
    from src.condition_monitor import ConditionMonitor, ConditionConfig
except ImportError:
    CombatManager    = None  # type: ignore
    Looter           = None  # type: ignore
    ConditionMonitor = None  # type: ignore

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
C_DEST   = (80, 255, 150)
C_PATH_DONE = (120, 120, 140, 100)
C_PATH_TODO = (80, 180, 255, 200)

# ─────────────────────────────────────────────────────────────────────────────
# Utilidad: hotkey string → VK code
# ─────────────────────────────────────────────────────────────────────────────
def _hotkey_vk(key_str: str) -> int:
    """Convierte 'f1'-'f12', '1'-'9' → código VK de Windows."""
    k = key_str.lower().strip()
    fkeys = {
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
        "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
        "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    }
    if k in fkeys:
        return fkeys[k]
    if len(k) == 1 and k.isdigit():
        return 0x30 + int(k)
    return 0x70  # fallback F1


# ─────────────────────────────────────────────────────────────────────────────
# Utilidad: dirección entre dos tiles consecutivos
# ─────────────────────────────────────────────────────────────────────────────
def _dir(a: Coordinate, b: Coordinate) -> Optional[str]:
    """Devuelve la dirección cardinal entre dos tiles adyacentes.
    Solo acepta pasos de exactamente 1 tile en una dirección (sin diagonales).
    Retorna None si el paso es diagonal, nulo, o de más de 1 tile.
    """
    dx = b.x - a.x
    dy = b.y - a.y
    if dx == 0 and dy == -1: return "up"
    if dx == 0 and dy ==  1: return "down"
    if dx == -1 and dy == 0: return "left"
    if dx ==  1 and dy == 0: return "right"
    # Cualquier otro caso (diagonal, salto largo, mismo tile) → ignorar
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Verificador de coordenadas reales
# Prioridad: 1) Lectura de memoria del proceso  2) OCR via OBS (fallback)
# ─────────────────────────────────────────────────────────────────────────────
class CoordTracker:
    """
    Lee las coordenadas reales del personaje.

    Estrategia:
      CharacterDetector (EasyOCR) — lee las coordenadas del texto del
      minimapa capturado via OBS WebSocket.
      Requiere calibrar el ROI con: python src/calibrator.py
    """

    def __init__(
        self,
        source: str = "obs-ws",
        obs_host: str = "localhost",
        obs_port: int = 4455,
        obs_password: str = "",
        obs_scene_source: str = "",
        window_title: str = "Tibia",
    ):
        self._window_title   = window_title
        self._source         = source
        self._obs_host       = obs_host
        self._obs_port       = obs_port
        self._obs_password   = obs_password
        self._obs_scene_src  = obs_scene_source

        self._ocr_detector: Optional[CharacterDetector] = None
        self._use_ocr        = False
        self._last_coord: Optional[Coordinate] = None

    def connect(self) -> bool:
        """Intenta conectar via OCR."""
        try:
            cfg = DetectorConfig.load()
            cfg.obs_ws_host     = self._obs_host
            cfg.obs_ws_port     = self._obs_port
            cfg.obs_ws_password = self._obs_password
            if self._obs_scene_src:
                cfg.obs_source = self._obs_scene_src
            det = CharacterDetector(source=self._source, config=cfg)
            det._source.connect()
            pos = det.detect_once()
            self._ocr_detector = det
            self._use_ocr      = True
            if pos is None:
                _log("  [COORDS] OCR conectado pero sin coordenadas — "
                     "calibra el ROI con: python src/calibrator.py --source obs-ws")
            else:
                _log(f"  [COORDS] OCR activo — posición: {pos}")
            return True
        except Exception as exc:
            _log(f"  [COORDS] OCR falló: {exc}")
            return False

    def get_position(self) -> Optional[Coordinate]:
        """Retorna la coordenada actual."""
        coord = None
        if self._use_ocr and self._ocr_detector is not None:
            try:
                coord = self._ocr_detector.detect_once()
            except Exception as _ocr_exc:
                _log(f"  [COORDS] OCR error: {_ocr_exc!r}")
        if coord is not None:
            self._last_coord = coord
        return coord

    def disconnect(self) -> None:
        if self._ocr_detector:
            try:
                self._ocr_detector._source.disconnect()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
class MapCache:
    def __init__(self, loader: TibiaMapLoader):
        self.loader = loader
        self._c: Dict[int, Image.Image] = {}

    def get(self, floor: int) -> Image.Image:
        if floor not in self._c:
            arr = self.loader.get_map_image(floor)
            self._c[floor] = Image.fromarray(arr).convert("RGB")
        return self._c[floor]

    def crop(self, floor: int, cx: int, cy: int, hw: int, hh: int) -> Image.Image:
        img = self.get(floor)
        px, py = cx - BOUNDS["xMin"], cy - BOUNDS["yMin"]
        w, h = img.size
        return img.crop((max(0, px-hw), max(0, py-hh), min(w, px+hw), min(h, py+hh)))

# ─────────────────────────────────────────────────────────────────────────────
# Detector de movimiento por diferencia de píxeles
# ─────────────────────────────────────────────────────────────────────────────
class MotionDetector:
    """
    Verifica si el personaje se movió comparando dos capturas consecutivas.

    Fuentes admitidas:
      "virtual-cam"   → OBS Virtual Camera (cv2.VideoCapture)
      "obs-ws"        → OBS WebSocket v5 (obsws-python)
      "obs-projector" → Captura la ventana Proyector de OBS (sin plugins extra)
    """

    def __init__(
        self,
        obs_source: str = "virtual-cam",
        cam_index:  int  = 0,
        capture_size: int   = 320,
        move_threshold: float = 4.0,
        hwnd: int = 0,          # hwnd de la ventana Tibia (inputs) — NO se usa para captura OBS
        proj_hwnd: int = 0,     # hwnd EXPLÍCITO de la ventana Proyector OBS (monitor 1)
        # obs-ws params (opcionales)
        obs_host: str = "localhost",
        obs_port: int = 4455,
        obs_password: str = "",
        obs_scene_source: str = "",
    ):
        self.obs_source     = obs_source
        self.cam_index      = cam_index
        self.capture_size   = capture_size
        self.move_threshold = move_threshold
        self._prev: Optional[Any]    = None
        self._src: Any      = None   # VirtualCameraSource | OBSWebSocketSource
        self._ok            = False
        self._frozen_count  = 0      # frames idénticos consecutivos
        self._frozen_warned = False
        self._thaw_count    = 0      # frames vivos post-congelamiento → rehabilitar tras 3

        # Inicializar fuente
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _sys.path.insert(0, str(_Path(__file__).parent.parent))

            # ── Proyector OBS (ventana Projector que OBS crea) ───────────────
            # MONITOR 1 (OBS) → captura  |  MONITOR 2 (Tibia) → inputs
            if obs_source == "obs-projector":
                import ctypes as _ct
                # proj_hwnd es la ventana OBS en monitor 1 (NUNCA se mezcla con Tibia hwnd)
                _proj_hwnd = proj_hwnd  # explícito vía --projector-hwnd
                if not _proj_hwnd:
                    _proj_hwnd = self._find_obs_projector()
                if not _proj_hwnd:
                    raise RuntimeError(
                        "Ventana Proyector OBS no encontrada. "
                        "En OBS: clic derecho en la escena → Proyector de escena → Ventana. "
                        "O pasa --projector-hwnd <hwnd_decimal>."
                    )
                from src.frame_capture import MssCapture
                _mss = MssCapture(hwnd=_proj_hwnd)
                _getter = _mss.open()

                import ctypes.wintypes as _wt
                class _ProjAdapter:
                    def __init__(self, getter, mss_inst):
                        self._getter   = getter
                        self._mss_inst = mss_inst
                    def get_frame(self):
                        import cv2 as _cv2
                        f = self._getter()
                        if f is None:
                            return None
                        # mss devuelve BGRA → quitar canal alpha
                        return f[:, :, :3] if f.shape[2] == 4 else f
                    def disconnect(self):
                        try: self._mss_inst.close()
                        except Exception: pass

                self._src = _ProjAdapter(_getter, _mss)
                self._ok  = True
                print(f"  [MOTION] Proyector OBS capturado (hwnd={_proj_hwnd:#010x})")

            # ── OBS Virtual Camera ───────────────────────────────────────
            elif obs_source == "virtual-cam":
                from src.character_detector import VirtualCameraSource
                self._src = VirtualCameraSource(cam_index)
                self._src.connect()
                self._ok = True
            elif obs_source == "obs-ws":
                from src.character_detector import OBSWebSocketSource, DetectorConfig
                cfg = DetectorConfig.load()
                cfg.obs_ws_host     = obs_host
                cfg.obs_ws_port     = obs_port
                cfg.obs_ws_password = obs_password
                if obs_scene_source:
                    cfg.obs_source = obs_scene_source
                # capture_width=640 — screenshot reducido para pixel-diff (más rápido)
                self._src = OBSWebSocketSource(cfg, capture_width=640)
                self._src.connect()
                self._ok = True
        except Exception as exc:
            print(f"  [MOTION] Error iniciando OBS ({obs_source}): {exc}")

    def _capture(self):
        """Lee un frame de OBS y extrae la región central del viewport."""
        if not self._ok or self._src is None:
            return None
        try:
            import numpy as np
            import cv2
            frame = self._src.get_frame()   # BGR uint8
            if frame is None:
                return None
            h, w = frame.shape[:2]
            # Recortar región central del viewport del juego (38% w, 50% h)
            cx = int(w * 0.38); cy = int(h * 0.50)
            half = self.capture_size // 2
            x0 = max(0, cx - half); x1 = min(w, x0 + self.capture_size)
            y0 = max(0, cy - half); y1 = min(h, y0 + self.capture_size)
            crop = frame[y0:y1, x0:x1]
            return crop.astype(np.float32)
        except Exception:
            return None

    def snapshot(self):
        """Captura frame ANTES de enviar la tecla."""
        self._prev = self._capture()

    def check_moved(self) -> str:
        """Captura frame DESPUÉS y compara. Retorna 'MOVED(x)' | 'STUCK(x)' | 'NOREAD'."""
        if self._prev is None:
            return "NOREAD"
        if not self._ok:
            # Si la cámara estuvo congelada, reintenta periódicamente para rehabilitar
            try:
                _f = self._src.get_frame() if self._src else None
                if _f is not None:
                    self._thaw_count += 1
                    if self._thaw_count >= 3:
                        self._ok          = True
                        self._thaw_count  = 0
                        self._frozen_count  = 0
                        self._frozen_warned = False
                        _log("  [MOTION] ✓ Cámara OBS recuperada — verificación reactivada.")
                else:
                    self._thaw_count = 0
            except Exception:
                pass
            return "NOREAD"
        import numpy as np
        after = self._capture()
        if after is None:
            return "NOREAD"
        prev = self._prev
        if prev.shape != after.shape:  # type: ignore[union-attr]
            self._prev = after
            return "NOREAD"
        diff = float(np.mean(np.abs(after.astype(np.int32) - prev.astype(np.int32))))
        # Detección de cámara congelada: frames pixel-exactos repetidos
        if diff == 0.0:
            self._frozen_count += 1
            if self._frozen_count >= 3 and not self._frozen_warned:
                self._frozen_warned = True
                _log("  [MOTION] ⚠ Cámara OBS CONGELADA (diff=0.0 x3) "
                     "\u2014 reinicia Virtual Camera en OBS. "
                     "Verificación de movimiento desactivada.")
                self._ok = False  # desactivar para no bloquear el walker
            self._prev = after
            return "NOREAD"
        self._frozen_count = 0
        self._frozen_warned = False
        self._prev = after
        return f"MOVED({diff:.1f})" if diff >= self.move_threshold else f"STUCK({diff:.1f})"

    def is_stuck(self, status: str) -> bool:
        return status.startswith("STUCK")

    def wait_for_move(self, max_wait: float, min_wait: float = 0.08) -> str:
        """Polling adaptativo: espera hasta que OBS detecte movimiento o se agote max_wait.
        Mantiene el baseline capturado en snapshot() fijo para la primera comprobación,
        luego actualiza frame a frame. Útil para avanzar antes de esperar el interval completo."""
        if not self._ok or self._prev is None:
            time.sleep(max_wait)
            return "NOREAD"
        deadline = time.time() + max_wait
        time.sleep(min_wait)           # mínimo antes de la primera comprobación
        status = "NOREAD"
        while time.time() < deadline:
            status = self.check_moved()
            if not status.startswith("STUCK"):
                return status          # MOVED o NOREAD → avanzar inmediatamente
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(0.07, remaining))
        return status

    @staticmethod
    def _find_obs_projector() -> int:
        """Busca la ventana Proyector de OBS por título y devuelve su hwnd.

        OBS crea ventanas con títulos como:
          'Proyector (Escena) - <nombre>'  (ES)
          'Projector (Scene) - <nombre>'   (EN)
          'OBS - Proyector de vista previa'
          'OBS - Preview Projector'
        Devuelve 0 si no se encuentra.
        """
        try:
            import ctypes
            from src.input_controller import list_windows
            wins = list_windows()
            _keywords = ("projector", "proyector", "preview")
            for w in wins:
                tl = w.title.lower()
                if any(k in tl for k in _keywords):
                    return w.hwnd
        except Exception:
            pass
        return 0

    def get_raw_frame(self):
        """Retorna el frame completo para HP/MP y minimap (sin recorte)."""
        if not self._ok or self._src is None:
            return None
        try:
            return self._src.get_frame()
        except Exception:
            return None

    def disconnect(self) -> None:
        """Libera la fuente OBS al terminar."""
        try:
            if self._src is not None:
                self._src.disconnect()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Comparador de posición real (template matching minimap) vs. posición esperada
# ─────────────────────────────────────────────────────────────────────────────
class MinimapComparator:
    """
    Compara en tiempo real la posición *esperada* del walker (route[step_idx])
    con la posición *real* detectada por template matching del minimapa de Tibia
    capturado desde OBS.

    Corre en un hilo de fondo (~1 s de intervalo, TM es costoso).
    Requiere MinimapRadar y una callable que devuelva frames BGR de OBS.

    Uso en AutoWalker (se asigna desde main()):
        walker.minimap_cmp = MinimapComparator(radar, detector.get_raw_frame)
        actual = walker.minimap_cmp.actual    # Coordinate | None
        drift  = walker.minimap_cmp.drift_from(expected)  # tiles | None
    """

    COLOR = (0, 230, 255)   # cyan — punto de posición real en el minimapa

    def __init__(
        self,
        radar:         "MinimapRadar",
        frame_getter,           # Callable[[], np.ndarray | None]
        poll_interval: float = 0.3,   # 0.3s para obs-projector (baja latencia)
    ) -> None:
        self._radar        = radar
        self._get_frame    = frame_getter
        self._interval     = poll_interval
        self._actual: Optional[Coordinate] = None
        self._confidence: float = 0.0
        self._last_ts: float    = 0.0
        self._lock  = threading.Lock()
        self._running = True
        self.hint_pos: Optional[Coordinate] = None   # se actualiza en _draw_minimap
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    # ── Propiedades thread-safe ───────────────────────────────────────────
    @property
    def actual(self) -> Optional[Coordinate]:
        with self._lock:
            return self._actual

    @property
    def confidence(self) -> float:
        with self._lock:
            return self._confidence

    @property
    def last_ts(self) -> float:
        with self._lock:
            return self._last_ts

    def drift_from(self, expected: Coordinate) -> Optional[int]:
        """Distancia Manhattan entre posición real y esperada, o None."""
        a = self.actual
        if a is None or a.z != expected.z:
            return None
        return abs(a.x - expected.x) + abs(a.y - expected.y)

    def set_floor(self, floor: int) -> None:
        """Notificar al radar el piso actual para acelerar el match."""
        self._radar.floor = floor

    def stop(self) -> None:
        self._running = False

    # ── Hilo de polling ──────────────────────────────────────────────────
    def _poll_loop(self) -> None:
        while self._running:
            try:
                frame = self._get_frame()
                if frame is not None:
                    hint = self.hint_pos
                    coord = self._radar.read(frame, hint=hint)
                    with self._lock:
                        if coord is not None:
                            self._actual = coord
                            self._last_ts = time.time()
                        total = self._radar._hit_count + self._radar._miss_count
                        self._confidence = (
                            self._radar._hit_count / total if total > 0 else 0.0
                        )
            except Exception:
                pass
            time.sleep(self._interval)


# ─────────────────────────────────────────────────────────────────────────────
# Efecto visual de pulso sobre el tile destino
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class StepEffect:
    x: float; y: float
    age: float = 0.0; dur: float = 0.4
    @property
    def alive(self): return self.age < self.dur
    @property
    def alpha(self): return int(200 * (1 - self.age / self.dur))
    @property
    def r(self): return int(6 + 8 * (self.age / self.dur))

# ─────────────────────────────────────────────────────────────────────────────
# Auto Walker principal
# ─────────────────────────────────────────────────────────────────────────────
class AutoWalker:
    MM = 420
    PW = 320
    WH = 580
    ZOOMS = [2, 3, 4, 6, 8]

    def __init__(
        self,
        route: List[Coordinate],
        ctrl: InputController,
        cache: MapCache,
        dest_name: str,
        step_interval: float = 0.18,
        dry_run: bool = False,
        detector: Optional["MotionDetector"] = None,
        loop: bool = False,
        loop_count: int = 0,        # 0 = infinito
        loop_mode: str = "pingpong", # "pingpong" | "forward"
        loop_delay: float = 1.0,     # segundos entre iteraciones
        adaptive: bool = False,      # True = avanza en cuanto OBS detecta MOVED
        navigator=None,              # WaypointNavigator para recalibración mid-walk
        recalib_drift: int = 8,      # tiles de drift para relanzar A*; 0 = desactivado
    ):
        self.route         = route
        self._base_route   = list(route)   # copia inmutable para reset
        self.ctrl          = ctrl
        self.cache         = cache
        self.dest_name     = dest_name
        self.step_interval = step_interval
        self.dry_run       = dry_run
        self.detector      = detector
        self.adaptive      = adaptive
        self._max_stuck    = 8   # ajustado para OBS-WS (timing artifacts ocasionales)
        self.coord_tracker: Optional["CoordTracker"] = None  # se asigna desde main()
        self.coord_tol     = 3   # tiles de tolerancia para la verificación de coordenadas
        self.navigator     = navigator   # WaypointNavigator para recalibrar ruta en vuelo
        self.recalib_drift = recalib_drift
        self.loop          = loop
        self.loop_count    = loop_count
        self.loop_mode     = loop_mode
        self.loop_delay    = loop_delay

        self.step_idx      = 0
        self.paused        = False
        self._running      = True
        self._aborted      = False   # True = detenido por stuck/wander, R para reanudar
        self.zoom_idx      = 2
        self.effects: List[StepEffect] = []

        self._stats     = {"sent": 0, "failed": 0, "skipped": 0, "moved": 0, "stuck": 0, "noread": 0, "coord_ok": 0, "coord_drift": 0}
        self._loop_iter = 0    # iteración actual (0 = primera)
        self._walk_done = False
        self._pos_status  = ""
        self._coord_status = ""   # última lectura: "OK(d=0)" | "DRIFT(d=5)" | "?"
        self.recalib_interval: int = 0  # recalibrar A* cada N pasos si hay fuente de posición (0=sólo por drift)

        # ── Auto HP/MP heal ──────────────────────────────────────────────────
        self.healer:      Optional[HpMpDetector] = None
        self.heal_hp_pct: int = 0
        self.heal_vk:     int = 0x70   # F1
        self.mana_mp_pct: int = 0
        self.mana_vk:     int = 0x71   # F2
        self._heal_steps: int = 1      # verificar cada N pasos
        self._heal_settle: float = 0.18  # settle delay post-heal antes del próximo snapshot

        # ── Blacklist de tiles bloqueados por NPCs/muebles no en el PNG ────────────────
        self._stuck_tile_hits: dict  = {}   # (x,y,z) → count de stucks
        self._stuck_tile_limit: int  = 3    # stucks antes de blacklistear el tile

        # ── Pausa de combate / esquive ────────────────────────────────────────
        # Cuando el personaje ataca/esquiva no se mueve aunque la tecla sea válida.
        # Esperamos combat_hold_secs antes de contar como stuck real.
        self.combat_hold_secs:  float = 2.5   # segundos de espera por pausa de combate
        self._max_combat_holds: int   = 6     # máximo de esperas antes de tratar como stuck real
        self._combat_hold_count: int  = 0     # contador para el paso actual
        self._prev_hp_pct: Optional[int] = None   # detectar HP drop → estamos siendo atacados

        # ── Cavebot refs + stats de sesión ────────────────────────────────────
        self._session_start: float = time.time()
        self._combat_mgr: Optional[Any] = None   # asignado desde main()
        self._looter_mgr: Optional[Any] = None   # asignado desde main()
        self.minimap_cmp: Optional["MinimapComparator"] = None  # comparador minimap real-time
        self.afk_jitter: float = 0.0              # anti-AFK: añade ±jitter/2 s a cada paso

    @property
    def pos(self) -> Coordinate:
        i = min(self.step_idx, len(self.route) - 1)
        return self.route[i]

    # ── Corrección de posición cuando el personaje se atasca ─────────────────
    def _resync_route(self, actual: "Coordinate") -> int:
        """Devuelve el índice de ruta más cercano a `actual`.

        Estrategia adaptativa:
          1. Primero busca en una ventana estrecha ±20 alrededor del paso actual.
          2. Si el mejor resultado está a >15 tiles (teletransporte/lag severo),
             amplía la búsqueda a toda la ruta para no quedarse atascado.
        """
        def _scan(start_i: int, end_i: int) -> tuple[int, float]:
            best_i = self.step_idx
            best_d = float("inf")
            for i in range(start_i, end_i):
                if self.route[i].z != actual.z:
                    continue  # distance_to lanza ValueError en pisos distintos
                d = actual.distance_to(self.route[i])
                if d < best_d:
                    best_d = d
                    best_i = i
            return best_i, best_d

        narrow_start = max(0, self.step_idx - 10)
        narrow_end   = min(len(self.route), self.step_idx + 20)
        best_idx, best_dist = _scan(narrow_start, narrow_end)

        # Si el mejor resultado en la ventana estrecha está muy lejos,
        # escanear toda la ruta (cubre teletransportes o lag grave)
        if best_dist > 15:
            full_idx, full_dist = _scan(0, len(self.route))
            if full_dist < best_dist:
                best_idx = full_idx

        return best_idx

    def _get_actual_position(self) -> "tuple[Optional['Coordinate'], str]":
        """Devuelve (posición_real, fuente) usando la mejor fuente disponible.

        Prioridad:
          1. CoordTracker (OCR) — más preciso, requiere calibración ROI.
          2. MinimapComparator (template matching) — no requiere calibración,
             funciona cuando OBS/virtual-cam está activo.
          3. None — sin fuente disponible.
        """
        if self.coord_tracker:
            p = self.coord_tracker.get_position()
            if p is not None:
                return p, "OCR"
        cmp = getattr(self, 'minimap_cmp', None)
        if cmp is not None:
            p = cmp.actual
            if p is not None and cmp.confidence >= 0.35:
                return p, f"MAP({cmp.confidence:.0%})"
        return None, "none"

    def _check_pos_and_recalib(
        self,
        expected: "Coordinate",
        stuck_count_ref: list,
    ) -> "tuple[str, bool]":
        """Compara posición real vs esperada y recalibra A* si el drift supera
        `recalib_drift`. También recalibra si `recalib_interval` está activo y
        se cumple el intervalo de pasos.

        Parámetros
        ----------
        expected        : tile esperado (b en el loop)
        stuck_count_ref : lista de un elemento [stuck_count] para poder
                          resetearlo a 0 cuando se recalibra.

        Devuelve
        --------
        coord_tag  : texto para añadir al log del paso
        recalibrated: True si se recalibró y hay que hacer `break` al loop
        """
        actual, source = self._get_actual_position()
        coord_tag = ""
        if actual is None:
            self._coord_status = "?"
            return "  [POS:?]", False

        if actual.z != expected.z:
            self._coord_status = "?"
            return "  [POS:z?]", False

        dist = int(actual.distance_to(expected))
        _icon = "📍"

        if dist <= self.coord_tol:
            self._stats["coord_ok"] += 1
            self._coord_status = f"OK(d={dist},{source})"
            coord_tag = f"  {_icon}({actual.x},{actual.y})[{source}] d={dist}✓"
        else:
            self._stats["coord_drift"] += 1
            self._coord_status = f"DRIFT(d={dist},{source})"
            coord_tag = (f"  ⚠POS({actual.x},{actual.y})[{source}]"
                         f" esp({expected.x},{expected.y}) d={dist}")

        # -- Recalibrar por drift --
        # FIX #5: Histeresis de 1 tile y mínimo de 10 pasos entre recalibraciones.
        # Sin esto, una fluctuación OCR de ±1 tile puede disparar recalibración
        # en bucle: recalib → step_idx=0 → misma fluctuación → recalib → …
        _steps_since_recalib = self.step_idx - getattr(self, '_last_recalib_step', -999)
        recalib_needed = (
            self.recalib_drift > 0
            and dist > self.recalib_drift + 1   # +1 tile de margen para ruido OCR
            and _steps_since_recalib >= 10       # mínimo 10 pasos entre recalibraciones
        )

        # -- Recalibrar por intervalo periódico --
        if (not recalib_needed
                and self.recalib_interval > 0
                and self.step_idx > 0
                and self.step_idx % self.recalib_interval == 0):
            recalib_needed = True
            _log(f"  [RECALIB] Intervalo {self.recalib_interval} pasos — verificando ruta desde ({actual.x},{actual.y})…")

        if recalib_needed and not self.dry_run:
            if self._recalibrate_route(actual):
                stuck_count_ref[0] = 0
                return coord_tag, True   # caller debe hacer break

        return coord_tag, False

    def _recalibrate_route(self, actual: "Coordinate") -> bool:
        """Recalcula la ruta A* desde la posición real hasta el destino.
        Se dispara cuando el drift OCR supera recalib_drift tiles.
        Intenta single-floor A* primero; si falla, intenta ruta multifloor.
        Devuelve True si la ruta fue recalculada con éxito."""
        if self.navigator is None or self.recalib_drift == 0:
            return False
        dest = self._base_route[-1]   # destino original siempre
        try:
            try:
                result = self.navigator.navigate(actual, dest)
            except ValueError:
                result = None  # cross-floor call — fall through to multifloor
            if result is not None and result.found and result.steps and len(result.steps) > 1:
                self.route        = result.steps
                self._base_route  = list(result.steps)
                self.step_idx     = 0
                self._last_recalib_step = 0  # FIX #5: registrar paso de recalibración
                _log(f"  [RECALIB] Ruta actualizada desde ({actual.x},{actual.y}) "
                     f"-> ({dest.x},{dest.y}): {len(result.steps)-1} pasos")
                return True
            # Single-floor A* failed — try multifloor (e.g. current pos on z=6 walkway)
            segs = self.navigator.navigate_multifloor(actual, dest)
            mf: List[Coordinate] = []
            for seg in segs:
                if seg.found and seg.steps:
                    mf.extend(seg.steps)
            if len(mf) >= 2:
                self.route        = mf
                self._base_route  = list(mf)
                self.step_idx     = 0
                self._last_recalib_step = 0  # FIX #5: registrar paso de recalibración
                _log(f"  [RECALIB] Ruta multifloor desde ({actual.x},{actual.y},{actual.z}): {len(mf)-1} entradas")
                return True
            _log(f"  [RECALIB] A* sin solucion desde ({actual.x},{actual.y}) -- ruta sin cambios")
            return False
        except Exception as _re:
            _log(f"  [RECALIB] Error al recalibrar: {_re}")
            return False

    # ── Detección de pausa por combate / esquive ────────────────────────────
    def _is_combat_pause(self, status: str) -> bool:
        """
        Devuelve True si la ausencia de movimiento se debe probablemente a
        combate (animación de ataque, esquive, knockback) y NO a un obstáculo.

        Tres señales:
        1. CombatManager detecta combate activo (más fiable).
        2. HP bajó > 1% respecto a la lectura anterior (recibimos daño).
        3. diff del pixel > 0.8: hay animación visible pero menor que un tile
           completo (ataque, hechizo, NPC parpadeando, texto flotante).

        Límite: si ya esperamos _max_combat_holds veces, devuelve False para
        forzar el tratamiento normal de stuck y evitar loops infinitos.
        """
        # 1) CombatManager
        if (self._combat_mgr is not None
                and getattr(self._combat_mgr, 'is_in_combat', False)):
            return True

        # 2) Caída de HP (siendo atacado)
        if self.healer is not None and self.detector is not None:
            try:
                frame = self.detector.get_raw_frame()
                if frame is not None:
                    hp, _ = self.healer.read_bars(frame)
                    prev  = self._prev_hp_pct
                    self._prev_hp_pct = hp
                    if prev is not None and hp is not None and hp < prev - 1:   # cayó > 1%
                        return True
            except Exception:
                pass

        # 3) diff de animación: "STUCK(x.x)" — extraer el valor numérico
        try:
            diff_val = float(status.split('(')[1].rstrip(')'))
            if diff_val >= 0.8:    # hay animación visible pero no movimiento de tile
                return True
        except Exception:
            pass

        return False

    # Dirección opuesta — siempre vuelve a un tile ya atravesado (seguro)
    # NOTA: usa los mismos nombres que devuelve _dir() → "up", "down", "left", "right"
    _REVERSE = {"up": "down", "down": "up", "left": "right", "right": "left"}
    # Perpendiculares ordenadas: primer elemento = más natural para esquinas
    _PERP = {
        "up":    ("right", "left"),
        "down":  ("left",  "right"),
        "left":  ("up",    "down"),
        "right": ("down",  "up"),
    }

    def _safe_unstuck(self, blocked_dir: str) -> None:
        """Retrocede un paso por la ruta recorrida y aplica un squeeze lateral
        para escapar de esquinas (cartel + monstruo, NPC esquina, etc.)."""        
        import time as _t
        if self.step_idx > 0:
            back_dir = self._REVERSE.get(blocked_dir, "down")
            _log(f"  [UNSTUCK] Retrocediendo ({back_dir}) a tile seguro…")
            self.ctrl.move(back_dir, steps=1, step_delay=0.05)
            _t.sleep(0.18)
            # Corner-squeeze: empujar un paso lateral tras el retroceso.
            # Ayuda a salir de esquinas con cartel/NPC en un lado y monstruo en otro.
            _sq = self._PERP.get(blocked_dir, ("right", "left"))[0]
            _log(f"  [UNSTUCK] Corner-squeeze ({_sq}) para salir de la esquina…")
            self.ctrl.move(_sq, steps=1, step_delay=0.05)
            _t.sleep(0.12)
            if self.step_idx > 1:
                self.step_idx -= 1
        else:
            # Sin tile previo — dois pasos perpendiculares para mayor separación
            perp = {"up": "right", "down": "left", "left": "up", "right": "down"}
            jdir = perp.get(blocked_dir, "right")
            _log(f"  [UNSTUCK] Sin tile previo — perpendicular ×2 ({jdir})")
            self.ctrl.move(jdir, steps=2, step_delay=0.08)
            _t.sleep(0.15)

    # ── Verificación unificada de movimiento ─────────────────────────────────
    def _verify_movement(
        self,
        pos_before: Optional["Coordinate"],
        expected: "Coordinate",
        pixel_status: str,
    ) -> str:
        """Verifica si el personaje REALMENTE se movió usando todas las fuentes.

        Prioridad de fuentes:
          1. pixel-diff del detector (MOVED/STUCK) — si está vivo
          2. MinimapComparator (template matching) — fuente primaria de posición
          3. OCR (_get_actual_position) — fallback
          4. Sin sensor → BLIND

        Retorna:
          "MOVED(...)"/  → confirmado movimiento
          "STUCK(...)"   → confirmado que NO se movió
          "BLIND"        → sin sensor disponible, no se puede verificar
        """
        # 1. Si pixel-diff está vivo y devolvió algo útil → confiar en él
        if pixel_status.startswith("MOVED") or pixel_status.startswith("STUCK"):
            return pixel_status

        # 2. Fallback a MinimapComparator (template matching) —
        #    La fuente MÁS confiable para posición absoluta
        _mm_pos = self.minimap_cmp.actual if self.minimap_cmp is not None else None
        if _mm_pos is None:
            _mm_pos, _ = self._get_actual_position()

        if _mm_pos is not None:
            # ¿Estamos donde deberíamos?
            _dist_to_expected = int(_mm_pos.distance_to(expected))
            if _dist_to_expected <= self.coord_tol:
                return f"MOVED(MM:d={_dist_to_expected})"

            # ¿Nos movimos respecto a antes?
            if pos_before is not None:
                try:
                    _moved_tiles = pos_before.distance_to(_mm_pos)
                    if _moved_tiles >= 0.5:
                        return f"MOVED(MM:{_moved_tiles:.1f}t)"
                    else:
                        return f"STUCK(MM:d={_dist_to_expected})"
                except (ValueError, AttributeError):
                    pass

            # Tenemos pos actual pero no baseline → comparar vs expected
            return f"STUCK(MM:d={_dist_to_expected})" if _dist_to_expected > self.coord_tol else f"MOVED(MM:ok)"

        # 3. Sin ningún sensor
        return "BLIND"

    def _emergency_retreat(self, steps: int = 5) -> None:
        """Retrocede hasta `steps` pasos por la ruta ya recorrida para alejar
        al personaje de zonas peligrosas antes de abortar."""
        import time as _t
        retreat = min(steps, self.step_idx)
        if retreat == 0:
            return
        _log(f"  [RETREAT] Retrocediendo {retreat} pasos por ruta segura…")
        i = self.step_idx
        for _ in range(retreat):
            if i < 1:
                break
            a = self.route[i]       # tile actual
            b = self.route[i - 1]   # tile anterior (destino de retroceso)
            back = _dir(a, b)       # dirección de tile_actual → tile_anterior
            if back:
                self.ctrl.move(back, steps=1, step_delay=0.08)
                _t.sleep(0.25)
            i -= 1
        self.step_idx = i

    @property
    def zoom(self):
        return self.ZOOMS[self.zoom_idx]

    def _tile_to_screen(self, tx, ty, cx, cy, ox=0, oy=0):
        half = self.MM // 2
        z = self.zoom
        return (ox + half + int((tx - cx) * z), oy + half + int((ty - cy) * z))

    # ── Walker en hilo separado ──────────────────────────────────────────────
    def _walk_loop(self):
        """Envía las teclas a Tibia en un hilo separado."""
        total = len(self.route) - 1
        _log(f"  Iniciando walk: {total} pasos hacia '{self.dest_name}'")
        if self.detector:
            _log("  [MOTION] Verificación de movimiento activa (pixel-diff)")
        _cmp_active = getattr(self, 'minimap_cmp', None) is not None
        if self.coord_tracker and _cmp_active:
            _log("  [COORDS] Verificación de posición activa: OCR + MinimapComparator (doble fuente)")
        elif self.coord_tracker:
            _log("  [COORDS] Verificación de posición activa: OCR")
        elif _cmp_active:
            _log("  [COORDS] Verificación de posición activa: MinimapComparator (template matching)")
        else:
            _log("  [COORDS] ⚠ Sin fuente de posición — asegúrate de que --x/--y son correctos")
            _log(f"  [COORDS]   Posición inicial asumida: ({self.route[0].x},{self.route[0].y}) floor {self.route[0].z}")
        if self.recalib_interval > 0:
            _log(f"  [COORDS] Auto-recalibración cada {self.recalib_interval} pasos activa")
        if self.recalib_drift > 0:
            _log(f"  [COORDS] Recalibración por drift activada: umbral={self.recalib_drift} tiles")
        if self.loop:
            loop_desc = f"∞" if self.loop_count == 0 else str(self.loop_count)
            _log(f"  [LOOP] modo={self.loop_mode}  iteraciones={loop_desc}  delay={self.loop_delay}s")

        stuck_count      = 0
        _last_direction: Optional[str] = None
        MAX_STUCK    = self._max_stuck
        RETRY_STUCK  = 3   # pasos stuck antes de intentar corrección de posición

        while self._running:
            total = len(self.route) - 1

            # ── Recorrer la ruta actual ─────────────────────────────────────
            while self.step_idx < total and self._running:
                if self.paused:
                    time.sleep(0.05)
                    continue

                a = self.route[self.step_idx]
                b = self.route[self.step_idx + 1]
                direction = _dir(a, b)

                # ── Resetear stuck_count al cambiar de dirección ─────────────
                if direction is not None and direction != _last_direction:
                    stuck_count     = 0
                    _last_direction = direction

                if direction is None:
                    self.step_idx += 1
                    self._stats["skipped"] += 1
                    continue

                # Captura posición minimap ANTES del paso (fallback si camera NOREAD)
                _pos_before_step: Optional["Coordinate"] = (
                    self.minimap_cmp.actual
                    if self.minimap_cmp is not None else None
                )
                if self.detector and not self.dry_run:
                    self.detector.snapshot()

                ok = False
                if not self.dry_run and self.ctrl.is_connected():
                    ok = self.ctrl.move(direction, steps=1, step_delay=0.05)
                    if ok:
                        self._stats["sent"] += 1
                    else:
                        self._stats["failed"] += 1
                        _log(f"  FAIL step {self.step_idx} — reintentando…")
                        time.sleep(0.3)
                        continue
                elif self.dry_run:
                    self._stats["sent"] += 1
                    ok = True

                if ok:
                    self.effects.append(StepEffect(x=float(b.x), y=float(b.y)))
                    _append_runtime({
                        "ts": time.time(),
                        "step": self.step_idx,
                        "dir": direction,
                        "x": b.x, "y": b.y, "z": b.z,
                        "ok": True,
                    })
                    self.step_idx += 1

                    if self.detector and not self.dry_run:
                        if self.adaptive:
                            # Modo adaptativo: avanza en cuanto OBS detecta MOVED.
                            # Jitter post-movimiento: añade variabilidad anti-detección
                            # incluso con polling (patrón de llegada impredecible).
                            status = self.detector.wait_for_move(
                                max_wait=self.step_interval * 2.0,
                                min_wait=max(0.06, self.step_interval * 0.3),
                            )
                            if self.afk_jitter > 0 and not status.startswith("STUCK"):
                                time.sleep(random.uniform(0, self.afk_jitter * 0.5))
                        else:
                            _sleep = self.step_interval
                            if self.afk_jitter > 0:
                                _sleep += random.uniform(-self.afk_jitter / 2, self.afk_jitter / 2)
                            time.sleep(max(0.04, _sleep))
                            status = self.detector.check_moved()
                        self._pos_status = status

                        # ── Verificación UNIFICADA: pixel-diff → minimap → BLIND ─
                        status = self._verify_movement(_pos_before_step, b, status)
                        self._pos_status = status

                        if status == "BLIND":
                            # Sin ningún sensor — NO resetear stuck_count
                            self._stats["noread"] = self._stats.get("noread", 0) + 1
                            _sc_ref_blind = [stuck_count]
                            _ctag_blind, _recal_blind = self._check_pos_and_recalib(b, _sc_ref_blind)
                            stuck_count = _sc_ref_blind[0]
                            _log(f"  STEP {self.step_idx:3d}/{total}  {direction.upper():<5}"
                                 f"  ({b.x},{b.y})  ? BLIND{_ctag_blind}")
                            if _recal_blind:
                                break
                            continue

                        # ── Verificación de posición (OCR → MinimapCmp) ──────
                        _sc_ref = [stuck_count]
                        coord_tag, _recalibrated = self._check_pos_and_recalib(b, _sc_ref)
                        stuck_count = _sc_ref[0]
                        if _recalibrated:
                            break  # sale al bucle interno — total se recomputa arriba

                        if status.startswith("STUCK"):
                            # ── ¿Pausa de combate / esquive? ─────────────────
                            if self._combat_hold_count < self._max_combat_holds \
                                    and self._is_combat_pause(status):
                                self._combat_hold_count += 1
                                _reason = (
                                    "combate" if (self._combat_mgr is not None
                                                  and getattr(self._combat_mgr, 'is_in_combat', False))
                                    else "animación/esquive"
                                )
                                _log(f"  STEP {self.step_idx:3d}/{total}  {direction.upper():<5}"
                                     f"  ({b.x},{b.y})"
                                     f"  ⏸ COMBAT-HOLD {self._combat_hold_count}/{self._max_combat_holds}"
                                     f" ({_reason}) — esperando {self.combat_hold_secs:.1f}s…")
                                if self.detector:
                                    self.detector.snapshot()
                                time.sleep(self.combat_hold_secs)
                                # Reintentar el mismo paso sin tocar stuck_count
                                self.step_idx -= 1   # retroceder índice para reenviar la tecla
                                continue

                            # Resetear si superamos el límite de combat-holds
                            self._combat_hold_count = 0
                            self._stats["stuck"] += 1
                            stuck_count += 1
                            _log(f"  STEP {self.step_idx:3d}/{total}  {direction.upper():<5}"
                                 f"  ({b.x},{b.y})  ⚠ {status}{coord_tag}")
                            # FIX #1: Revertir el avance prematuro de step_idx.
                            # step_idx se incrementó en la línea de arriba ANTES de verificar
                            # si el personaje realmente se movió. Si hubo stuck real, el
                            # siguiente ciclo del while leería a=route[B] b=route[C] cuando
                            # el personaje sigue en A. Al decrementar aquí, la próxima
                            # iteración vuelve a leer a=route[A] b=route[B] correctamente.
                            self.step_idx = max(0, self.step_idx - 1)

                            # ── Blacklist: tile bloqueado por NPC/objeto ──
                            _bkey = (b.x, b.y, b.z)
                            self._stuck_tile_hits[_bkey] = self._stuck_tile_hits.get(_bkey, 0) + 1
                            if (self._stuck_tile_hits[_bkey] >= self._stuck_tile_limit
                                    and self.navigator is not None):
                                _log(f"  [BLACKLIST] ({b.x},{b.y}) atascado "
                                     f"{self._stuck_tile_hits[_bkey]}x — marcando y recalculando…")
                                try:
                                    _pf = self.navigator._pathfinders.get(b.z)
                                    if _pf is not None:
                                        _bpx, _bpy = b.to_pixel()
                                        _pf.walkability[_bpy, _bpx] = False
                                    # FIX #4: Usar posición real como origen A* (no teórica).
                                    # Tras Fix #1, step_idx apunta al tile A (posición actual).
                                    # coord_tracker da la posición real; si no, usar route[step_idx].
                                    if self.coord_tracker:
                                        _real = self.coord_tracker.get_position()
                                        _from = _real if _real is not None else self.route[max(0, self.step_idx)]
                                    else:
                                        _from = self.route[max(0, self.step_idx)]
                                    _dest = self._base_route[-1]
                                    _nr   = self.navigator.navigate(_from, _dest)
                                    if _nr.found and len(_nr.steps) > 1:
                                        self.route    = _nr.steps
                                        self.step_idx = 0
                                        stuck_count   = 0
                                        _log(f"  [BLACKLIST] Ruta alternativa: {len(_nr.steps)-1} pasos")
                                        self._safe_unstuck(direction)
                                        break  # sale al bucle externo para recomputar total
                                    else:
                                        # Try multifloor fallback (e.g. on z=6 walkway)
                                        try:
                                            _segs = self.navigator.navigate_multifloor(_from, _dest)
                                            _mf: List[Coordinate] = []
                                            for _s in _segs:
                                                if _s.found and _s.steps:
                                                    _mf.extend(_s.steps)
                                            if len(_mf) >= 2:
                                                self.route    = _mf
                                                self.step_idx = 0
                                                stuck_count   = 0
                                                _log(f"  [BLACKLIST] Ruta multifloor alternativa: {len(_mf)-1} entradas")
                                                self._safe_unstuck(direction)
                                                break
                                        except Exception:
                                            pass
                                        _log("  [BLACKLIST] A* sin alternativa -- continuando")
                                except Exception as _be:
                                    _log(f"  [BLACKLIST] Error: {_be}")

                            # ── Corrección de posición ────────────────────
                            if stuck_count == RETRY_STUCK:
                                corrected = False
                                # 1) Resync por coordenadas OCR
                                if self.coord_tracker:
                                    actual_pos = self.coord_tracker.get_position()
                                    if actual_pos is not None:
                                        new_idx = self._resync_route(actual_pos)
                                        if new_idx != self.step_idx:
                                            _log(f"  [RESYNC] Posición real ({actual_pos.x},{actual_pos.y})"
                                                 f" → paso {self.step_idx} → {new_idx}")
                                            self.step_idx = new_idx
                                            stuck_count   = 0
                                            corrected     = True
                                        else:
                                            _log(f"  [RESYNC] Posición real ({actual_pos.x},{actual_pos.y})"
                                                 f" coincide con paso actual — jiggle")
                                    else:
                                        _log(f"  [RESYNC] OCR sin lectura — ROI no calibrado. "
                                             f"Verifica --x/--y o calibra con: python src/calibrator.py --source obs-ws")
                                else:
                                    _log(f"  [RESYNC] Sin coord_tracker — usar --verify-coords para activar OCR")
                                # 2) Retroceso seguro (no perpendicular aleatoria)
                                if not self.dry_run:
                                    self._safe_unstuck(direction)
                                if corrected:
                                    continue

                            if stuck_count >= MAX_STUCK:
                                # ── Desvío de esquina / bloqueador dinámico ──────
                                # Orden: perpendiculares primero (mejor para esquinas),
                                # luego reversa, luego dirección original.
                                # Cada intento usa 2 pasos para superar la esquina.
                                # Espera previa: da tiempo al monstruo/NPC a moverse.
                                if not self.dry_run:
                                    _log(f"  [WANDER] Esperando 1.5s a que bloqueador dinámico (monstruo/NPC) despeje…")
                                    time.sleep(1.5)
                                    _p1, _p2 = self._PERP.get(direction, ("right", "left"))
                                    _back_w   = self._REVERSE.get(direction, "down")
                                    # perpendiculares → reversa → original (mejor orden para esquinas)
                                    _directions = [_p1, _p2, _back_w, direction]
                                    for _wander_dir in _directions:
                                        _log(f"  [WANDER] Intentando ({_wander_dir}) ×2…")
                                        self.ctrl.move(_wander_dir, steps=2, step_delay=0.10)
                                        time.sleep(0.35)
                                        if self.detector:
                                            _ws = self.detector.check_moved()
                                            if not self.detector.is_stuck(_ws):
                                                _log(f"  [WANDER] ✓ Desvío exitoso ({_wander_dir}) — reanudando ruta")
                                                stuck_count = 0
                                                break
                                    else:
                                        # WANDER fallido — forzar blacklist del tile bloqueado
                                        # y recalcular A* desde posición actual antes de rendirse.
                                        _log(f"  [WANDER] Sin éxito — forzando blacklist y recalculando ruta A*…")
                                        self._emergency_retreat(steps=2)
                                        _bkey2 = (b.x, b.y, b.z)
                                        if self.navigator is not None:
                                            try:
                                                _pf2 = self.navigator._pathfinders.get(b.z)
                                                if _pf2 is not None:
                                                    _bpx2, _bpy2 = b.to_pixel()
                                                    _pf2.walkability[_bpy2, _bpx2] = False
                                                # También bloquear tile vecino en la misma dirección
                                                import numpy as _np2
                                                _nx2 = b.x + (1 if direction == "right" else -1 if direction == "left" else 0)
                                                _ny2 = b.y + (1 if direction == "down"  else -1 if direction == "up"    else 0)
                                                _n2  = type(b)(_nx2, _ny2, b.z) if hasattr(type(b), '__call__') else b
                                                try:
                                                    _npx2, _npy2 = _n2.to_pixel()
                                                    _pf2.walkability[_npy2, _npx2] = False
                                                except Exception:
                                                    pass
                                                # FIX #4: Usar posición real como origen A*.
                                                if self.coord_tracker:
                                                    _real2 = self.coord_tracker.get_position()
                                                    _from2 = _real2 if _real2 is not None else self.route[max(0, self.step_idx)]
                                                else:
                                                    _from2 = self.route[max(0, self.step_idx)]
                                                _dest2  = self._base_route[-1]
                                                _nr2    = self.navigator.navigate(_from2, _dest2)
                                                if _nr2.found and len(_nr2.steps) > 1:
                                                    self.route    = _nr2.steps
                                                    self.step_idx = 0
                                                    stuck_count   = 0
                                                    self._stuck_tile_hits.clear()
                                                    _log(f"  [WANDER] ✓ Ruta alternativa encontrada: {len(_nr2.steps)-1} pasos — continuando")
                                                    break  # sale del for _wander_dir, continúa el while
                                                else:
                                                    # Intentar multifloor
                                                    try:
                                                        _segs2 = self.navigator.navigate_multifloor(_from2, _dest2)
                                                        _mf2: list = []
                                                        for _s2 in _segs2:
                                                            if _s2.found and _s2.steps:
                                                                _mf2.extend(_s2.steps)
                                                        if len(_mf2) >= 2:
                                                            self.route    = _mf2
                                                            self.step_idx = 0
                                                            stuck_count   = 0
                                                            self._stuck_tile_hits.clear()
                                                            _log(f"  [WANDER] ✓ Ruta multifloor alternativa: {len(_mf2)-1} pasos — continuando")
                                                            break
                                                    except Exception:
                                                        pass
                                                    _log(f"  [!] A* sin alternativa tras blacklist — presiona R para reanudar.")
                                                    self._emergency_retreat(steps=3)
                                                    self._aborted = True
                                                    break
                                            except Exception as _we:
                                                _log(f"  [WANDER] Error en recalculo: {_we} — presiona R para reanudar.")
                                                self._aborted = True
                                                break
                                        else:
                                            _log(f"  [!] Sin navigator — presiona R para reanudar.")
                                            self._emergency_retreat(steps=3)
                                            self._aborted = True
                                            break
                                else:
                                    _log(f"  [!] Bloqueado {MAX_STUCK} pasos (dry-run) — presiona R para reanudar.")
                                    self._aborted = True
                                    break
                        else:
                            self._stats["moved"] += 1
                            stuck_count = 0
                            self._combat_hold_count = 0   # movimiento real → reset combat hold
                            _log(f"  STEP {self.step_idx:3d}/{total}  {direction.upper():<5}"
                                 f"  ({b.x},{b.y})  ✓ {status}{coord_tag}")
                    else:
                        # Sin pixel-diff: esperar interval + verificar con minimap
                        _sleep = self.step_interval
                        if self.afk_jitter > 0:
                            _sleep += random.uniform(-self.afk_jitter / 2, self.afk_jitter / 2)
                        time.sleep(max(0.04, _sleep))

                        if not self.dry_run:
                            # Verificación unificada (sin pixel-diff → status="NOREAD")
                            status = self._verify_movement(_pos_before_step, b, "NOREAD")
                            self._pos_status = status

                            if status == "BLIND":
                                # Sin sensores — verificar coord y continuar sin decidir
                                _sc_ref2 = [stuck_count]
                                coord_tag, _recalibrated2 = self._check_pos_and_recalib(b, _sc_ref2)
                                stuck_count = _sc_ref2[0]
                                _log(f"  STEP {self.step_idx:3d}/{total}  {direction.upper():<5}"
                                     f"  ({b.x},{b.y})  ? BLIND{coord_tag}")
                                if _recalibrated2:
                                    break
                            elif status.startswith("STUCK"):
                                stuck_count += 1
                                self._stats["stuck"] += 1
                                _sc_ref2 = [stuck_count]
                                coord_tag, _recalibrated2 = self._check_pos_and_recalib(b, _sc_ref2)
                                stuck_count = _sc_ref2[0]
                                _log(f"  STEP {self.step_idx:3d}/{total}  {direction.upper():<5}"
                                     f"  ({b.x},{b.y})  ⚠ {status}{coord_tag}")
                                if _recalibrated2:
                                    break
                                # Si stuck_count alcanza MAX_STUCK, la lógica de WANDER
                                # del siguiente ciclo lo manejará (ya está implementada arriba)
                            else:
                                stuck_count = 0
                                self._stats["moved"] += 1
                                _sc_ref2 = [stuck_count]
                                coord_tag, _recalibrated2 = self._check_pos_and_recalib(b, _sc_ref2)
                                stuck_count = _sc_ref2[0]
                                _log(f"  STEP {self.step_idx:3d}/{total}  {direction.upper():<5}"
                                     f"  ({b.x},{b.y})  ✓ {status}{coord_tag}")
                                if _recalibrated2:
                                    break
                        else:
                            coord_tag = ""
                            _log(f"  DRY {self.step_idx:3d}/{total}"
                                 f"  {direction.upper():<5}  ({b.x},{b.y}){coord_tag}")

                    # ── Auto HP/MP heal ────────────────────────────────────
                    if (not self.dry_run
                            and self.healer is not None
                            and self.heal_hp_pct > 0
                            and self.step_idx % self._heal_steps == 0):
                        _hframe = (
                            self.detector.get_raw_frame()
                            if self.detector
                            else None
                        )
                        if _hframe is not None:
                            _htag = self.healer.heal_if_needed(
                                _hframe,
                                self.heal_hp_pct, self.ctrl, self.heal_vk,
                                self.mana_mp_pct, self.mana_vk,
                            )
                            if "HEAL" in _htag or "MANA" in _htag:
                                _log(f"              [HP/MP] {_htag}")
                                # Esperar que efectos visuales del heal se asienten
                                # ANTES del siguiente snapshot (evita pixel-diff falso 0)
                                time.sleep(self._heal_settle)

            if not self._running or self._aborted:
                break
            dest = self.route[-1]
            if self.coord_tracker and not self.dry_run:
                actual = self.coord_tracker.get_position()
                if actual is not None and actual.z == dest.z:
                    d = int(actual.distance_to(dest))
                    if d <= self.coord_tol + 2:
                        _log(f"  [DESTINO] ✓ Llegaste a ({dest.x},{dest.y}) — real: ({actual.x},{actual.y}) d={d}")
                    else:
                        _log(f"  [DESTINO] ⚠ Ruta completada pero posición real ({actual.x},{actual.y})"
                             f" dista {d} tiles del destino ({dest.x},{dest.y})")
                else:
                    _log(f"  [DESTINO] Ruta completada → ({dest.x},{dest.y}) — sin lectura OCR")
            else:
                _log(f"  [DESTINO] Ruta completada → ({dest.x},{dest.y})")

            # ── Iteración completada ───────────────────────────────────────
            self._loop_iter += 1
            iter_summary = (f"  ✓ Iter {self._loop_iter} completada."
                            f" Enviados: {self._stats['sent']}  Fallidos: {self._stats['failed']}")
            if self.detector:
                iter_summary += f"  | moved:{self._stats['moved']} stuck:{self._stats['stuck']}"
            _log(iter_summary)

            if not self.loop:
                break

            # Verificar límite de iteraciones
            if self.loop_count > 0 and self._loop_iter >= self.loop_count:
                _log(f"  [LOOP] {self._loop_iter}/{self.loop_count} iteraciones completadas — fin.")
                break

            # Pausar entre iteraciones
            _log(f"  [LOOP] Esperando {self.loop_delay}s antes de iteración {self._loop_iter+1}…")
            t_end = time.time() + self.loop_delay
            while time.time() < t_end and self._running:
                time.sleep(0.05)

            if not self._running or self._aborted:
                break

            # Preparar siguiente iteración
            if self.loop_mode == "pingpong":
                # Invertir la ruta
                self.route = list(reversed(self.route))
                _log(f"  [LOOP] pingpong iter {self._loop_iter+1}: "
                     f"{self.route[0]} → {self.route[-1]}")
            else:
                # forward: siempre start → end (recalcular con A* desde el punto actual)
                self.route = list(self._base_route)
                _log(f"  [LOOP] forward iter {self._loop_iter+1}: "
                     f"{self.route[0]} → {self.route[-1]}")

            self.step_idx = 0
            self.effects.clear()

        self._walk_done = True
        final = (f"  ✓ Walk finalizado. Total iteraciones: {self._loop_iter}"
                 f"  Enviados: {self._stats['sent']}  Fallidos: {self._stats['failed']}")
        if self.detector:
            final += f"  | moved:{self._stats['moved']} stuck:{self._stats['stuck']}"
        if self.coord_tracker:
            final += f"  | pos_ok:{self._stats['coord_ok']} drift:{self._stats['coord_drift']}"
        _log(final)

    # ── Dibujo minimap ───────────────────────────────────────────────────────
    def _draw_minimap(self, surf: pygame.Surface):
        floor = self.pos.z
        z    = self.zoom
        half_t = self.MM // (2 * z) + 2
        cx, cy = self.pos.x, self.pos.y
        mm_y = (self.WH - self.MM) // 2

        pygame.draw.rect(surf, (8, 8, 14), (0, 0, self.MM, self.WH))

        try:
            crop = self.cache.crop(floor, cx, cy, half_t, half_t)
            rw, rh = crop.width * z, crop.height * z
            _resample = getattr(Image, 'Resampling', Image).LANCZOS
            resized = crop.resize((rw, rh), _resample)
            pg_s = pygame.image.fromstring(resized.tobytes(), resized.size, resized.mode)  # type: ignore[arg-type]
            bx = self.MM // 2 - rw // 2
            by = mm_y + self.MM // 2 - rh // 2
            surf.blit(pg_s, (bx, by))
        except Exception:
            pass

        # Ruta (ya recorrida = gris; pendiente = azul)
        route_surf = pygame.Surface((self.MM, self.MM), pygame.SRCALPHA)
        pts_all = [self._tile_to_screen(c.x, c.y, cx, cy, 0, 0) for c in self.route]
        if len(pts_all) >= 2:
            done = pts_all[:self.step_idx + 1]
            todo = pts_all[self.step_idx:]
            if len(done) >= 2:
                pygame.draw.lines(route_surf, (140, 140, 160, 90),  False, done, 1)
            if len(todo) >= 2:
                pygame.draw.lines(route_surf, (80, 180, 255, 200), False, todo, 2)
        surf.blit(route_surf, (0, mm_y))

        # Destino final
        if self.route:
            end = self.route[-1]
            ex, ey = self._tile_to_screen(end.x, end.y, cx, cy, 0, mm_y)
            pygame.draw.circle(surf, C_DEST, (ex, ey), 6)
            pygame.draw.circle(surf, (255, 255, 255), (ex, ey), 6, 1)

        # Efectos de paso
        for eff in self.effects:
            ex, ey = self._tile_to_screen(eff.x, eff.y, cx, cy, 0, mm_y)
            s = pygame.Surface((eff.r*2+4, eff.r*2+4), pygame.SRCALPHA)
            pygame.draw.circle(s, (80, 220, 120, eff.alpha), (eff.r+2, eff.r+2), eff.r, 2)
            surf.blit(s, (ex-eff.r-2, ey-eff.r-2))

        # Personaje (posición esperada — amarillo)
        px, py = self.MM//2, mm_y + self.MM//2
        pulse = 4 + int(2 * math.sin(time.time() * 8))
        pygame.draw.circle(surf, C_CHAR,  (px, py), pulse)
        pygame.draw.circle(surf, (255, 255, 255), (px, py), pulse, 1)

        # Posición real (MinimapComparator — template matching) — punto cian
        if self.minimap_cmp is not None:
            # Actualizar hint + floor para el hilo de polling
            self.minimap_cmp.hint_pos = self.pos
            self.minimap_cmp.set_floor(self.pos.z)
            actual_pos = self.minimap_cmp.actual
            if actual_pos is not None and actual_pos.z == self.pos.z:
                ax, ay = self._tile_to_screen(actual_pos.x, actual_pos.y, cx, cy, 0, mm_y)
                # Dibujar solo si está dentro del recuadro del minimapa
                if 0 <= ax < self.MM and mm_y <= ay < mm_y + self.MM:
                    # Línea tenue de conexión: esperado → real
                    pygame.draw.line(surf, (0, 150, 180), (px, py), (ax, ay), 1)
                    # Punto cian = posición real
                    pygame.draw.circle(surf, (0, 230, 255), (ax, ay), 5)
                    pygame.draw.circle(surf, (255, 255, 255), (ax, ay), 5, 1)

        # Cruz
        pygame.draw.line(surf, (*C_BORDER, 70), (0, mm_y + self.MM//2), (self.MM, mm_y + self.MM//2), 1)
        pygame.draw.line(surf, (*C_BORDER, 70), (self.MM//2, mm_y), (self.MM//2, mm_y + self.MM), 1)
        pygame.draw.rect(surf, C_BORDER, (0, mm_y, self.MM, self.MM), 2)

    # ── Panel derecho ────────────────────────────────────────────────────────
    def _draw_panel(self, surf: pygame.Surface, font_lg, font_md, font_sm):
        px = self.MM + 4
        pygame.draw.rect(surf, C_PANEL, (px, 0, self.PW, self.WH))
        pygame.draw.rect(surf, C_BORDER, (px, 0, self.PW, self.WH), 1)

        x = px + 10
        y = 10

        def t(text, col=C_TEXT, f=font_md):
            surf.blit(f.render(text, True, col), (x, y))

        def sep():
            nonlocal y
            y += 5
            pygame.draw.line(surf, C_BORDER, (x, y), (x + self.PW - 20, y), 1)
            y += 7

        t("AUTO WALKER", C_ACCENT, font_lg);  y += 28
        sep()

        # Destino
        t(f"Destino:", C_DIM, font_sm);  y += 14
        dest_trunc = self.dest_name[:26] if len(self.dest_name) > 26 else self.dest_name
        t(f"  {dest_trunc}", C_GREEN);  y += 18
        sep()

        # Progreso
        total = max(len(self.route) - 1, 1)
        pct   = self.step_idx / total
        t(f"Paso {self.step_idx:3d} / {total}", C_TEXT);  y += 18
        bar_w = self.PW - 22
        pygame.draw.rect(surf, C_BORDER, (x, y, bar_w, 10), 1)
        fill_col = C_GREEN if self._walk_done else C_ACCENT
        pygame.draw.rect(surf, fill_col, (x, y, int(bar_w * pct), 10))
        y += 18

        # Posición
        p = self.pos
        t(f"X {p.x:5d}  Y {p.y:5d}", C_TEXT);  y += 18
        t(f"Floor {p.z:02d}  Zoom {self.zoom}x", C_DIM);  y += 20
        sep()

        # Estado walk
        if self._walk_done:
            if self.loop:
                t("↺ LOOP FINALIZADO", C_GREEN, font_lg);  y += 22
            else:
                t("¡DESTINO ALCANZADO!", C_GREEN, font_lg);  y += 22
        elif self.paused:
            t("[ PAUSADO ]", C_ORANGE, font_lg);  y += 22
        elif self.loop:
            loop_max = f"/{self.loop_count}" if self.loop_count > 0 else "/∞"
            t(f"↺ LOOP {self._loop_iter+1}{loop_max}  {self.loop_mode.upper()}", C_ACCENT, font_lg);  y += 22
        else:
            t("► CAMINANDO…", C_ACCENT, font_lg);  y += 22
        sep()

        # Stats
        connected = self.ctrl.is_connected()
        conn_txt  = f"→ Tibia" if (connected or self.dry_run) else "[!] Sin conexión"
        conn_col  = C_GREEN if (connected or self.dry_run) else C_RED
        t(conn_txt, conn_col);  y += 18
        if self.dry_run:
            t("  [DRY-RUN]", C_YELLOW, font_sm);  y += 14
        t(f"Enviados:  {self._stats['sent']}", C_GREEN, font_sm);  y += 13
        t(f"Fallidos:  {self._stats['failed']}", C_ORANGE if self._stats['failed'] else C_DIM, font_sm);  y += 13
        t(f"Interval:  {self.step_interval*1000:.0f}ms/paso", C_DIM, font_sm);  y += 16
        sep()

        # Estado de movimiento (pixel-diff)
        if self.detector:
            t("MOTION DETECT", C_DIM, font_sm);  y += 13
            st = self._pos_status
            if st.startswith("MOVED"):
                t(f"  {st}", C_GREEN, font_sm);  y += 13
            elif st.startswith("STUCK"):
                t(f"  {st}", C_RED, font_sm);  y += 13
            elif st:
                t(f"  {st}", C_YELLOW, font_sm);  y += 13
            else:
                t("  — esperando —", C_DIM, font_sm);  y += 13
            t(f"  mov:{self._stats['moved']} stuck:{self._stats['stuck']}", C_DIM, font_sm);  y += 13
        if self.coord_tracker:
            t("COORD VERIFY", C_DIM, font_sm);  y += 13
            cs = self._coord_status
            if cs.startswith("OK"):
                t(f"  {cs}", C_GREEN, font_sm);  y += 13
            elif cs.startswith("DRIFT"):
                t(f"  {cs}", C_RED, font_sm);  y += 13
            elif cs == "?":
                t("  sin lectura OCR", C_YELLOW, font_sm);  y += 13
            else:
                t("  — esperando —", C_DIM, font_sm);  y += 13
            t(f"  ok:{self._stats['coord_ok']} drift:{self._stats['coord_drift']}", C_DIM, font_sm);  y += 13
        # ── MinimapComparator: posición real por template matching ──────────
        if self.minimap_cmp is not None:
            t("MINIMAP REAL", C_DIM, font_sm);  y += 13
            actual_mc = self.minimap_cmp.actual
            drift_mc  = self.minimap_cmp.drift_from(self.pos)
            conf_mc   = self.minimap_cmp.confidence
            age_mc    = time.time() - self.minimap_cmp.last_ts
            if actual_mc is None:
                t("  buscando match…", C_YELLOW, font_sm);  y += 13
            else:
                age_col = (C_GREEN if age_mc < 2.0 else
                           C_YELLOW if age_mc < 5.0 else C_RED)
                t(f"  ({actual_mc.x},{actual_mc.y})", (0, 230, 255), font_sm);  y += 13
                drift_col = (C_GREEN if drift_mc is None or drift_mc <= 2 else
                             C_YELLOW if drift_mc <= 6 else C_RED)
                drift_txt = f"  drift={drift_mc}t" if drift_mc is not None else "  drift=-"
                t(drift_txt, drift_col, font_sm);  y += 13
                t(f"  conf={conf_mc*100:.0f}%  {age_mc:.1f}s", age_col, font_sm);  y += 13
            # Leyenda: punto amarillo=esperado, cian=real
            _lx = x
            pygame.draw.circle(surf, C_CHAR, (_lx, y + 5), 4)
            surf.blit(font_sm.render("=esperado", True, C_DIM), (_lx + 8, y))
            pygame.draw.circle(surf, (0, 230, 255), (_lx + 80, y + 5), 4)
            surf.blit(font_sm.render("=real", True, C_DIM), (_lx + 88, y))
            y += 14
        sep()

        # ── Cavebot stats de sesión ──────────────────────────────────────────
        elapsed_s = int(time.time() - self._session_start)
        hh2, mm2, ss2 = elapsed_s // 3600, (elapsed_s % 3600) // 60, elapsed_s % 60
        t("SESION", C_DIM, font_sm);  y += 13
        t(f"  Tiempo: {hh2:02d}:{mm2:02d}:{ss2:02d}", C_TEXT, font_sm);  y += 13
        if self._combat_mgr is not None:
            kills = self._combat_mgr.kills
            hp_pct = self._combat_mgr.last_hp_pct
            kills_h = kills * 3600 / max(elapsed_s, 1)
            t(f"  Kills: {kills}  ({kills_h:.0f}/h)", C_GREEN if kills > 0 else C_DIM, font_sm);  y += 13
            if hp_pct is not None:
                hp_col = C_RED if hp_pct < 30 else (C_YELLOW if hp_pct < 60 else C_GREEN)
                t(f"  HP: {hp_pct}%", hp_col, font_sm);  y += 13
            combat_st = "EN COMBATE" if self._combat_mgr.is_in_combat else "sin target"
            t(f"  {combat_st}", C_ORANGE if self._combat_mgr.is_in_combat else C_DIM, font_sm);  y += 13
        if self._looter_mgr is not None:
            loot_count = getattr(self._looter_mgr, '_items_looted', 0)
            loot_h = loot_count * 3600 / max(elapsed_s, 1)
            t(f"  Loot: {loot_count}  ({loot_h:.0f}/h)", C_YELLOW if loot_count > 0 else C_DIM, font_sm);  y += 13
        if self.afk_jitter > 0:
            t(f"  Anti-AFK: ±{self.afk_jitter*500:.0f}ms jitter", C_DIM, font_sm);  y += 13
        sep()

        # Controles
        y = self.WH - 70
        sep()
        for ctrl_txt in ["SPACE  pausar / reanudar",
                          "+/-    zoom",
                          "R      reanudar / reiniciar",
                          "ESC    abortar"]:
            t(ctrl_txt, C_DIM, font_sm);  y += 13

    # ── Run ──────────────────────────────────────────────────────────────────
    def run(self):
        pygame.init()
        W = self.MM + self.PW + 4
        screen = pygame.display.set_mode((W, self.WH))
        pygame.display.set_caption(f"Auto Walker → {self.dest_name}")
        clock  = pygame.time.Clock()

        # Registrar hwnd propio
        try:
            self.ctrl._own_hwnd = pygame.display.get_wm_info().get("window")
        except Exception:
            pass

        # Cargar mapa
        floor = self.pos.z
        _log(f"  Cargando mapa piso {floor:02d}…")
        self.cache.get(floor)
        _log("  Mapa listo.")

        # Conectar
        if not self.dry_run:
            w = self.ctrl.find_target()
            if w:
                _log(f"  Conectado → {w.title}")
            else:
                _log(f"  ADVERTENCIA: '{self.ctrl.target_title}' no encontrado")

        try:
            font_lg = pygame.font.SysFont("Consolas", 15, bold=True)
            font_md = pygame.font.SysFont("Consolas", 13)
            font_sm = pygame.font.SysFont("Consolas", 11)
        except Exception:
            font_lg = font_md = font_sm = pygame.font.Font(None, 13)

        # Arrancar hilo de walk
        def _walk_thread_safe():
            try:
                self._walk_loop()
            except Exception as _exc:
                import traceback as _tb
                _tb_str = _tb.format_exc()
                _log(f"  [WALK] ❌ Error fatal en hilo de navegación: {_exc!r}")
                _log(_tb_str)
                _write_fatal(_exc, _tb_str)
                self._walk_done = True
                self._running = False

        # _wt[0] permite relanzar el hilo sin perder la referencia
        _wt: list = [None]

        def _spawn_walk():
            if _wt[0] is not None and _wt[0].is_alive():
                return   # ya hay hilo activo
            t = threading.Thread(target=_walk_thread_safe, daemon=True)
            t.start()
            _wt[0] = t

        _spawn_walk()

        _last = time.time()

        while self._running:
            now = time.time()
            dt  = min(now - _last, 0.1)
            _last = now

            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self._running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                        self._running = False
                        _log("  Abortado por el usuario.")
                    elif ev.key == pygame.K_SPACE:
                        self.paused = not self.paused
                        _log("  " + ("PAUSA" if self.paused else "REANUDADO"))
                    elif ev.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                        self.zoom_idx = min(self.zoom_idx + 1, len(self.ZOOMS) - 1)
                    elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        self.zoom_idx = max(self.zoom_idx - 1, 0)
                    elif ev.key == pygame.K_r:
                        # ── Resume inteligente desde posición actual ──────────
                        # Orden de prioridad para detectar la posición real:
                        #  1. CoordTracker (OCR) — más preciso
                        #  2. MinimapComparator (template matching)
                        #  3. self.pos — última coordenada conocida por la ruta
                        _resume_pos = None
                        if self.coord_tracker:
                            _resume_pos = self.coord_tracker.get_position()
                        if _resume_pos is None and self.minimap_cmp is not None:
                            _resume_pos = self.minimap_cmp.actual
                        if _resume_pos is None:
                            _resume_pos = self.pos
                        _new_step = self._resync_route(_resume_pos) if _resume_pos is not None else 0
                        self.step_idx            = _new_step
                        self._aborted            = False
                        self._walk_done          = False
                        self.paused              = False
                        self._stuck_tile_hits.clear()   # limpiar blacklist obsoleta
                        self._combat_hold_count  = 0
                        self.effects.clear()
                        _start_tile = self.route[_new_step]
                        _log(f"  [RESUME] Reanudando desde paso {_new_step} "
                             f"→ ({_start_tile.x},{_start_tile.y},z={_start_tile.z})")
                        _spawn_walk()

            # Actualizar efectos
            for eff in self.effects:
                eff.age += dt
            self.effects = [e for e in self.effects if e.alive]

            # Dibujar
            screen.fill(C_BG)
            self._draw_minimap(screen)
            self._draw_panel(screen, font_lg, font_md, font_sm)

            # Barra de título
            total = max(len(self.route) - 1, 1)
            pct   = self.step_idx / total
            title_s = font_lg.render(
                f"Floor {self.pos.z:02d}  |  {self.step_idx}/{total} pasos  |  {pct*100:.0f}%",
                True, C_DIM
            )
            screen.blit(title_s, (8, 4))

            if self._walk_done:
                if self._aborted:
                    _ab_s = font_lg.render("⚠ DETENIDO — Presiona R para reanudar", True, C_ORANGE)
                    screen.blit(_ab_s, (self.MM//2 - _ab_s.get_width()//2,
                                       (self.WH - self.MM)//2 + self.MM - 28))
                else:
                    end_s = font_lg.render(
                        "↺ LOOP FINALIZADO" if self.loop else "¡DESTINO ALCANZADO!",
                        True, C_GREEN
                    )
                    screen.blit(end_s, (self.MM//2 - end_s.get_width()//2,
                                       (self.WH - self.MM)//2 + self.MM - 28))

            pygame.display.flip()
            clock.tick(60)

        self._running = False  # señal al hilo
        pygame.quit()


# ─────────────────────────────────────────────────────────────────────────────
# Script executor — runs a .in file instruction by instruction
# ─────────────────────────────────────────────────────────────────────────────
class ScriptExecutor:
    """
    Executes a parsed list of Instructions sequentially.

    Supports:
      node / stand          → navigate to coordinate (multi-floor if needed)
      ladder / rope / shovel → walk to tile + execute transition action
      label                 → noop (jump target)
      goto <label>          → unconditional jump
      action end            → stop execution
      action wait           → sleep 1 second
      wait <N>              → sleep N seconds
      use_hotkey <vk>       → press VK key
      use_item <name> vk=N  → press VK key (same as use_hotkey when vk given)
      if hp < N goto <lbl>  → conditional jump on HP %
      if mp < N goto <lbl>  → conditional jump on MP %
      say <text>            → type text in Tibia chat
      talk_npc [words]      → send each word to NPC chat

    Parameters
    ----------
    instructions : list[Instruction]
        Parsed script instructions.
    ctrl : InputController
        Input controller for sending keys/clicks.
    navigator : WaypointNavigator
        For computing A* paths.
    step_interval : float
        Seconds between steps (default 0.18).
    healer : HpMpDetector, optional
        Needed for if_hp / if_mp conditions.
    coord_tracker : CoordTracker, optional
        For position verification.
    dry_run : bool
        If True, only log actions without sending real inputs.
    afk_jitter : float
        Max timing jitter in seconds (0 = disabled).
    """

    def __init__(
        self,
        instructions: List[Instruction],
        ctrl: InputController,
        navigator: "WaypointNavigator",
        step_interval: float = 0.18,
        healer: Optional["HpMpDetector"] = None,
        frame_getter=None,           # Callable[[], np.ndarray|None] — source for HP/MP frames
        coord_tracker: Optional["CoordTracker"] = None,
        depot_manager=None,          # Optional[DepotManager]
        dry_run: bool = False,
        afk_jitter: float = 0.0,
        motion_detector=None,        # Optional[MotionDetector] — para detección de stuck en segmentos
        rope_hotkey_vk: int = 0,     # VK para usar cuerda (0=desactivado)
        shovel_hotkey_vk: int = 0,   # VK para usar pala (0=desactivado)
        viewport_center: tuple = (562, 496),  # Centro del viewport Tibia en px de cliente
        tile_size_px: int = 75,      # Tamaño de tile en píxeles
    ) -> None:
        self._instructions   = instructions
        self._ctrl           = ctrl
        self._nav            = navigator
        self._step_interval  = step_interval
        self._healer         = healer
        self._frame_getter   = frame_getter   # Optional: MotionDetector.get_raw_frame
        self._coord_tracker  = coord_tracker
        self._depot_mgr      = depot_manager
        self._dry_run        = dry_run
        self._afk_jitter     = afk_jitter
        self._motion_detector = motion_detector  # MotionDetector para pixel-diff stuck detection
        self._rope_vk        = rope_hotkey_vk
        self._shovel_vk      = shovel_hotkey_vk
        self._viewport_center = viewport_center
        self._tile_size_px   = tile_size_px
        self._running        = True
        self._current_pos: Optional[Coordinate] = None

        # Build label → index map
        self._labels: Dict[str, int] = {}
        for i, ins in enumerate(instructions):
            if ins.kind == "label":
                self._labels[ins.label.lower()] = i

    def set_start_position(self, pos: Coordinate) -> None:
        self._current_pos = pos

    def run(self) -> None:
        """Execute instructions until end or abort."""
        idx = 0
        total = len(self._instructions)
        _log(f"  [SCRIPT] Ejecutando {total} instrucciones")
        while self._running and idx < total:
            ins = self._instructions[idx]
            _log(f"  [SCRIPT] [{idx:4d}] {ins}")
            jump_to = self._execute(ins)
            if jump_to is not None:
                new_idx = self._labels.get(jump_to.lower())
                if new_idx is not None:
                    idx = new_idx
                    continue
                else:
                    _log(f"  [SCRIPT] ⚠ Label '{jump_to}' no encontrado — ignorando salto")
            idx += 1
        _log("  [SCRIPT] Fin del script")

    def _execute(self, ins: Instruction) -> Optional[str]:
        """
        Execute one instruction.
        Returns a label name to jump to, or None to advance sequentially.
        """
        kind = ins.kind

        # ── Terminar ──────────────────────────────────────────────────────
        if kind == "action" and ins.action == "end":
            _log("  [SCRIPT] action end → terminando")
            self._running = False
            return None

        # ── Macro de depot ────────────────────────────────────────────────
        if kind == "action" and ins.action in ("depot", "deposit"):
            if self._depot_mgr is not None:
                self._depot_mgr.run_depot_cycle(player_pos=self._current_pos)
            else:
                _log("  [SCRIPT] ⚠ action depot/deposit: DepotManager no disponible — agrega --depot")
            return None

        # ── Esperar ───────────────────────────────────────────────────────
        if kind == "wait" or (kind == "action" and ins.action == "wait"):
            secs = ins.wait_secs if ins.wait_secs > 0 else 1.0
            _log(f"  [SCRIPT] Esperando {secs}s…")
            if not self._dry_run:
                time.sleep(secs)
            return None

        # ── Salto incondicional ───────────────────────────────────────────
        if kind == "goto":
            return ins.label_jump

        # ── Noop (label) ──────────────────────────────────────────────────
        if kind == "label":
            return None

        # ── Tecla / ítem ─────────────────────────────────────────────────
        if kind in ("use_hotkey", "use_item") and ins.hotkey_vk:
            _log(f"  [SCRIPT] Presionando VK={ins.hotkey_vk:#x}")
            if not self._dry_run:
                self._ctrl.press_key(ins.hotkey_vk)
                _sleep = 0.3
                if self._afk_jitter > 0:
                    _sleep += random.uniform(0, self._afk_jitter)
                time.sleep(_sleep)
            return None

        # ── Condición HP/MP ───────────────────────────────────────────────
        if kind == "if_stat":
            value = self._read_stat(ins.stat)
            if value is not None:
                triggered = (ins.op == "<" and value < ins.threshold) or \
                            (ins.op == ">" and value > ins.threshold)
                _log(f"  [SCRIPT] if {ins.stat} {ins.op} {ins.threshold}"
                     f"  (actual={value}%) → {'JUMP' if triggered else 'skip'}")
                if triggered:
                    return ins.goto_label
            else:
                _log(f"  [SCRIPT] ⚠ No se pudo leer {ins.stat} — saltando condición")
            return None

        # ── Salto condicional (frbot legacy) ─────────────────────────────
        if kind == "cond_jump":
            # Evaluate var_name as a stat if possible
            value = self._read_stat(ins.var_name)
            if value is not None and value < 50:
                return ins.label_jump
            return ins.label_skip or None

        # ── Hablar ───────────────────────────────────────────────────────
        if kind == "say" and ins.sentence:
            _log(f"  [SCRIPT] Diciendo: {ins.sentence!r}")
            if not self._dry_run:
                self._ctrl.type_text(ins.sentence)
                time.sleep(0.5)
            return None

        if kind == "talk_npc" and ins.words:
            for word in ins.words:
                _log(f"  [SCRIPT] Hablando con NPC: {word!r}")
                if not self._dry_run:
                    self._ctrl.type_text(word)
                    time.sleep(1.0)
            return None

        # ── Navegación ───────────────────────────────────────────────────
        if kind in ("node", "stand", "ladder", "shovel", "rope", "open_door") and ins.coord:
            dest = ins.coord.to_tibia_coord()
            if kind == "open_door":
                self._walk_to(dest, kind)
                # Si A* no llegó al tile de la puerta, hacer un paso extra hacia ella
                if not self._dry_run and self._current_pos is not None:
                    dx = max(-1, min(1, dest.x - self._current_pos.x))
                    dy = max(-1, min(1, dest.y - self._current_pos.y))
                    if dx != 0 or dy != 0:
                        _log(f"  [SCRIPT] Puerta — step-through: move_to_tile({dx},{dy})")
                        self._ctrl.move_to_tile(dx, dy)
                        time.sleep(0.5)
            else:
                self._walk_to(dest, kind)
                # After transition tile: execute the action
                if kind in ("ladder", "rope", "shovel") and not self._dry_run:
                    self._execute_transition_action(kind)
            return None

        # ── Unknown / unhandled ───────────────────────────────────────────
        _log(f"  [SCRIPT] instrucción desconocida ignorada: {ins.raw!r}")
        return None

    def _read_stat(self, stat_name: str) -> Optional[int]:
        """Returns HP% or MP% as int 0-100, or None if unavailable."""
        if self._healer is None or self._frame_getter is None:
            return None
        try:
            frame = self._frame_getter()
            if frame is None:
                return None
            hp_pct, mp_pct = self._healer.read_bars(frame)
            if stat_name == "hp":
                return hp_pct
            if stat_name == "mp":
                return mp_pct
        except Exception as _stat_exc:
            _log(f"  [SCRIPT] ⚠ _read_stat('{stat_name}') error: {_stat_exc!r}")
        return None

    def _walk_to(self, dest: Coordinate, instruction_kind: str) -> None:
        """Walk from current position to dest using A*."""
        if self._current_pos is None:
            # Try to get position from tracker
            if self._coord_tracker:
                self._current_pos = self._coord_tracker.get_position()
            if self._current_pos is None:
                _log(f"  [SCRIPT] ⚠ Posición desconocida — no se puede navegar a {dest}")
                return

        start = self._current_pos
        _log(f"  [SCRIPT] Navegando: {start} → {dest}")

        try:
            result = self._nav.navigate(start, dest)
            if result.found:
                segments = [result]
            else:
                segments = self._nav.navigate_multifloor(start, dest)
        except Exception:
            try:
                segments = self._nav.navigate_multifloor(start, dest)
            except Exception as exc2:
                _log(f"  [SCRIPT] Error calculando ruta: {exc2}")
                return

        for seg in segments:
            if not seg.found or not seg.steps:
                _log(f"  [SCRIPT] Sin ruta para segmento {seg.start} → {seg.end}")
                continue
            ok = self._walk_segment(seg.steps)
            # FIX #2+#3: Si el segmento abortó por MAX_STUCK, intentar recuperar
            # recalculando A* desde la posición real antes de abandonar.
            if not ok:
                real_pos = self._coord_tracker.get_position() if self._coord_tracker else None
                if real_pos is not None and self._nav is not None:
                    _log(f"  [SCRIPT] Recuperando desde posición real ({real_pos.x},{real_pos.y})…")
                    try:
                        _rec = self._nav.navigate(real_pos, dest)
                        if _rec.found and len(_rec.steps) > 1:
                            _log(f"  [SCRIPT] Ruta alternativa: {len(_rec.steps)-1} pasos — reintentando")
                            ok2 = self._walk_segment(_rec.steps)
                            if ok2:
                                self._current_pos = (self._coord_tracker.get_position() if self._coord_tracker else None) or dest
                                return
                    except Exception as _re:
                        _log(f"  [SCRIPT] Error en recuperación: {_re}")
                # Sin recuperación posible: actualizar posición real y salir
                if self._coord_tracker:
                    _pos_abort = self._coord_tracker.get_position()
                    if _pos_abort:
                        self._current_pos = _pos_abort
                _log(f"  [SCRIPT] Segmento abortado sin recuperación — deteniendo ruta.")
                return

        # FIX #3: Solo asignar dest si el walk completó exitosamente.
        # Antes se asignaba siempre, lo que generaba drift cuando el walk fue abortado.
        if self._coord_tracker:
            pos = self._coord_tracker.get_position()
            if pos:
                self._current_pos = pos
            else:
                self._current_pos = dest
        else:
            self._current_pos = dest

    def _walk_segment(self, steps: List[Coordinate]) -> bool:
        """Send arrow keys for each step in the path.
        Distingue entre pausa de combate/esquive (espera sin contar stuck)
        y obstáculo real (retrode + abort tras MAX_STUCK intentos).
        """
        det              = self._motion_detector
        MAX_STUCK        = 8
        RETRY_BACK       = 3   # stucks reales antes de retroceder
        MAX_COMBAT_HOLDS = 6   # esperas de combate antes de tratar como stuck real
        COMBAT_HOLD_SECS = getattr(self, '_combat_hold_secs', 2.5)
        stuck_count      = 0
        combat_hold_count = 0
        i = 0
        while i < len(steps) - 1:
            if not self._running:
                break
            a, b = steps[i], steps[i + 1]
            direction = _dir(a, b)
            if direction is None:
                i += 1
                continue
            _sleep = self._step_interval
            if self._afk_jitter > 0:
                _sleep += random.uniform(-self._afk_jitter / 2, self._afk_jitter / 2)

            if det:
                det.snapshot()

            if not self._dry_run and self._ctrl.is_connected():
                self._ctrl.move(direction, steps=1, step_delay=0.05)

            time.sleep(max(0.04, _sleep))

            if det:
                status = det.check_moved()
                if det.is_stuck(status):
                    # ── ¿Pausa de combate / esquive? ─────────────────────
                    # Heurística: diff > 0.8 → hay animación visible → combate/esquive
                    _is_combat = False
                    try:
                        _diff_val = float(status.split('(')[1].rstrip(')'))
                        _is_combat = _diff_val >= 0.8
                    except Exception:
                        pass

                    if _is_combat and combat_hold_count < MAX_COMBAT_HOLDS:
                        combat_hold_count += 1
                        _log(f"  [SCRIPT] ⏸ COMBAT-HOLD {combat_hold_count}/{MAX_COMBAT_HOLDS}"
                             f" en {direction} → ({b.x},{b.y})  {status}"
                             f" — esperando {COMBAT_HOLD_SECS:.1f}s…")
                        if det:
                            det.snapshot()
                        time.sleep(COMBAT_HOLD_SECS)
                        # Reintentar mismo paso
                        continue

                    # Stuck real — resetear combat hold y contar
                    combat_hold_count = 0
                    stuck_count += 1
                    _log(f"  [SCRIPT] ⚠ STUCK ({stuck_count}/{MAX_STUCK}) en {direction} → ({b.x},{b.y})  {status}")
                    if stuck_count == RETRY_BACK and i > 0:
                        # Retroceder un tile para desatascar
                        back     = {"up": "down", "down": "up", "left": "right", "right": "left"}
                        back_dir = back.get(direction, "down")
                        _log(f"  [SCRIPT] Retrocediendo ({back_dir}) para desatascar…")
                        if not self._dry_run:
                            self._ctrl.move(back_dir, steps=1, step_delay=0.05)
                            time.sleep(0.25)
                    if stuck_count >= MAX_STUCK:
                        _log(f"  [SCRIPT] ✖ Bloqueado {MAX_STUCK} veces en ({b.x},{b.y}) — abortando segmento.")
                        # FIX #2: No matar todo el ejecutor. Devolver False para que _walk_to
                        # pueda intentar recuperación (recalcular A* desde posición real).
                        return False
                    # No avanzar al siguiente paso si hubo stuck
                    continue
                else:
                    if stuck_count > 0 or combat_hold_count > 0:
                        _log(f"  [SCRIPT] ✓ Reanudado tras {'combat-hold' if combat_hold_count else 'stuck'}")
                    stuck_count       = 0
                    combat_hold_count = 0
            i += 1
        return True

    def _execute_transition_action(self, kind: str) -> None:
        """Execute the physical action for a floor transition."""
        import time as _t
        cx, cy = self._viewport_center
        if kind == "ladder":
            _log("  [SCRIPT] Usando escalera (Enter)…")
            self._ctrl.press_key(0x0D)  # Enter — escaleras se usan con Enter en Tibia
            _t.sleep(0.5)
        elif kind == "rope":
            if self._rope_vk:
                _log(f"  [SCRIPT] Usando cuerda (VK={self._rope_vk:#x}) + clic en character tile…")
                self._ctrl.press_key(self._rope_vk)
                _t.sleep(0.3)
                self._ctrl.click(cx, cy)
                _t.sleep(0.8)
                # Actualizar z: rope sube un piso (z disminuye)
                if self._current_pos is not None:
                    self._current_pos = Coordinate(
                        self._current_pos.x,
                        self._current_pos.y,
                        self._current_pos.z - 1,
                    )
            else:
                _log("  [SCRIPT] ⚠ rope: rope_hotkey_vk=0 — agrega --rope-vk para usar cuerda")
        elif kind == "shovel":
            if self._shovel_vk:
                _log(f"  [SCRIPT] Usando pala (VK={self._shovel_vk:#x}) + clic en character tile…")
                self._ctrl.press_key(self._shovel_vk)
                _t.sleep(0.3)
                self._ctrl.click(cx, cy)
                _t.sleep(1.0)
                # Actualizar z: shovel baja un piso (z aumenta)
                if self._current_pos is not None:
                    self._current_pos = Coordinate(
                        self._current_pos.x,
                        self._current_pos.y,
                        self._current_pos.z + 1,
                    )
            else:
                _log("  [SCRIPT] ⚠ shovel: shovel_hotkey_vk=0 — agrega --shovel-vk para usar pala")


# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Auto Walker — navega automáticamente a un destino")
    ap.add_argument("--dest",        default="thais depot",  help="Nombre del destino (waypoint)")
    ap.add_argument("--floor",       type=int, default=7)
    ap.add_argument("--x",           type=int, default=None,  help="Coordenada X inicial")
    ap.add_argument("--y",           type=int, default=None,  help="Coordenada Y inicial")
    ap.add_argument("--script",      default=None,            help="Ruta a un fichero .in de script (activa modo script)")
    ap.add_argument("--target",      default="Tibia",         help="Título de la ventana destino")
    ap.add_argument("--interval",    type=float, default=0.18, help="Segundos entre pasos (default 0.18)")
    ap.add_argument("--dry-run",     action="store_true",     help="No enviar inputs reales")
    ap.add_argument("--move-mode",   default="arrow",         choices=["arrow", "wasd"])
    ap.add_argument("--verify-pos",      action="store_true",     help="Verificar movimiento real via OBS (pixel-diff)")
    ap.add_argument("--minimap-pos",     action="store_true",     dest="minimap_pos",
                                         help="Comparar posición minimap real (template matching) con ruta en tiempo real (punto cian en el visor)")
    ap.add_argument("--minimap-tiles-wide", type=int, default=0, dest="minimap_tiles_wide",
                                         help="Tiles visibles en el minimap (0=usar minimap_config.json; típico=90)")
    ap.add_argument("--move-threshold",  type=float, default=1.5, help="Umbral pixel-diff para detectar movimiento (default: 1.5, bajar a 0.5-1.0 si STUCK con diff bajo)")
    ap.add_argument("--source",      default="virtual-cam",
                                     choices=["virtual-cam", "obs-ws", "obs-projector"],
                                     help="Fuente de captura para verificación de movimiento: "
                                          "virtual-cam (OBS Virtual Camera), "
                                          "obs-ws (OBS WebSocket), "
                                          "obs-projector (ventana Proyector de OBS — "
                                          "clic derecho en escena → Proyector de escena → Ventana). "
                                          "(default: virtual-cam)")
    ap.add_argument("--obs-cam",     type=int, default=0,     help="Índice de la Virtual Camera de OBS (default 0)")
    ap.add_argument("--obs-host",    default="localhost",     help="Host OBS WebSocket (para --source obs-ws)")
    ap.add_argument("--obs-port",    type=int, default=4455,  help="Puerto OBS WebSocket")
    ap.add_argument("--obs-password",default="",              help="Contraseña OBS WebSocket")
    ap.add_argument("--obs-scene-source", default="",         help="Nombre de la fuente OBS (para obs-ws)")
    # ── Setup dual-monitor (Monitor 1=OBS captura / Monitor 2=Tibia inputs) ──
    ap.add_argument("--projector-hwnd", type=int, default=0, dest="projector_hwnd",
                                     help="HWND decimal de la ventana Proyector OBS (monitor 1). "
                                          "Si 0, se auto-detecta por título. "
                                          "Usa: python -c \"from src.input_controller import list_windows; [print(w) for w in list_windows()]\"")
    ap.add_argument("--tibia-hwnd",    type=int, default=0, dest="tibia_hwnd",
                                     help="HWND decimal de la ventana Tibia (monitor 2) para inputs. "
                                          "Si 0, se auto-detecta por --target título.")
    ap.add_argument("--input-method", default="postmessage",   choices=["postmessage", "scancode", "hybrid"],
                                     help="postmessage=background (default) | scancode=hardware | hybrid=hardware+foco restaurado (recomendado)")
    ap.add_argument("--fg-delay",    type=float, default=0.04, help="Pausa (s) tras SetForegroundWindow (solo scancode)")
    ap.add_argument("--start-delay", type=int,   default=5,   help="Segundos de cuenta atrás antes de empezar (útil para poner foco en Tibia)")
    ap.add_argument("--loop",        action="store_true",     help="Activar modo loop continuo")
    ap.add_argument("--loop-count",  type=int, default=0,     help="Número de iteraciones (0=infinito)")
    ap.add_argument("--loop-mode",   default="pingpong",      choices=["pingpong", "forward"],
                                     help="pingpong=A→B→A→B  forward=A→B→A→B (recalcula)")
    ap.add_argument("--loop-delay",  type=float, default=1.0, help="Segundos de pausa entre iteraciones")
    ap.add_argument("--combat-hold-secs", type=float, default=2.5, dest="combat_hold_secs",
                                     help="Segundos de espera cuando se detecta pausa de combate/esquive (default 2.5)")
    ap.add_argument("--max-combat-holds", type=int,   default=6,   dest="max_combat_holds",
                                     help="Máximo de esperas de combate por paso antes de tratar como stuck real (default 6)")
    ap.add_argument("--verify-coords", action="store_true",
                                     help="Verificar posición real via OCR en cada paso (requiere ROI calibrado)")
    ap.add_argument("--coord-tol",   type=int, default=3,
                                     help="Tolerancia en tiles para verificación de coordenadas (default 3)")
    ap.add_argument("--adaptive",    action="store_true",
                                     help="Modo adaptativo: avanza en cuanto OBS detecta movimiento "
                                          "(no espera interval fijo). Reduce tiempo por paso 30-60%%).")
    ap.add_argument("--recalib-drift", type=int, default=8, dest="recalib_drift",
                                     help="Tiles de drift para recalcular ruta A* en vuelo "
                                          "(0=desactivado, default=8). Usa OCR o MinimapComparator.")
    ap.add_argument("--recalib-interval", type=int, default=0, dest="recalib_interval",
                                     help="Recalibrar ruta A* cada N pasos aunque no haya drift "
                                          "(0=desactivado). Requiere --minimap-pos o --verify-coords.")
    # ── Auto-curación HP/MP ────────────────────────────────────────────────
    ap.add_argument("--heal-hp-pct",  type=int, default=0, dest="heal_hp_pct",
                                     help="Curar cuando HP%% < este valor (0=desactivado)")
    ap.add_argument("--heal-hotkey",  default="f1", dest="heal_hotkey",
                                     help="Hotkey de curación: f1-f12 o 1-9 (default f1)")
    ap.add_argument("--mana-mp-pct",  type=int, default=0, dest="mana_mp_pct",
                                     help="Usar poción de maná cuando MP%% < este valor (0=desactivado)")
    ap.add_argument("--mana-hotkey",  default="f2", dest="mana_hotkey",
                                     help="Hotkey de maná: f1-f12 o 1-9 (default f2)")
    ap.add_argument("--heal-steps",   type=int, default=1, dest="heal_steps",
                                     help="Verificar HP/MP cada N pasos (default 1)")
    # ── Cavebot: combate ──────────────────────────────────────────────────
    ap.add_argument("--combat",          action="store_true",
                                         help="Activar combate automático (template matching en battle list)")
    ap.add_argument("--combat-vk",       type=int, default=0, dest="combat_vk",
                                         help="VK de ataque (0=solo clic; Tibia inicia auto-ataque al seleccionar target)")
    ap.add_argument("--flee-hp",         type=int, default=0, dest="flee_hp",
                                         help="Huir cuando HP%% < N (0=desactivado)")
    ap.add_argument("--flee-vk",         type=int, default=0, dest="flee_vk",
                                         help="VK del hechizo/ítem de huida")
    # ── Cavebot: loot ────────────────────────────────────────────────────
    ap.add_argument("--loot",            action="store_true",
                                         help="Activar looter automático")
    ap.add_argument("--loot-mode",       default="all", choices=["all", "whitelist"], dest="loot_mode",
                                         help="all=lootear todo  whitelist=solo ítems con template")
    ap.add_argument("--loot-delay",      type=float, default=1.2, dest="loot_delay",
                                         help="Segundos de espera antes de abrir cadáver (default 1.2)")
    # ── Cavebot: condiciones ─────────────────────────────────────────────
    ap.add_argument("--conditions",      action="store_true",
                                         help="Activar monitor de condiciones (veneno, parálisis, etc.)")
    ap.add_argument("--poison-vk",       type=int, default=0, dest="poison_vk",
                                         help="VK para curar veneno")
    ap.add_argument("--paralyze-vk",     type=int, default=0, dest="paralyze_vk",
                                         help="VK para curar parálisis (ej. utani hur)")
    ap.add_argument("--burning-vk",      type=int, default=0, dest="burning_vk",
                                         help="VK para curar quemadura")
    # ── Anti-AFK ───────────────────────────────────────────────────────
    ap.add_argument("--anti-afk",         action="store_true",
                                         help="Activar variación aleatoria del intervalo de paso (anti-detección)")
    ap.add_argument("--afk-jitter",       type=float, default=0.06, dest="afk_jitter",
                                         help="Jitter máximo (s) en cada paso (default 0.06 = ±30ms)")
    # ── Transiciones de piso (rope / shovel) ──────────────────────────
    ap.add_argument("--rope-vk",     type=lambda x: int(x, 0), default=0, dest="rope_vk",
                                     help="VK hexadecimal del hotkey de cuerda (ej. 0x72 = F3). "
                                          "Requerido para instrucciones 'rope' en script.")
    ap.add_argument("--shovel-vk",   type=lambda x: int(x, 0), default=0, dest="shovel_vk",
                                     help="VK hexadecimal del hotkey de pala (ej. 0x73 = F4). "
                                          "Requerido para instrucciones 'shovel' en script.")
    # ── Depot macro ───────────────────────────────────────────────────
    ap.add_argument("--depot",            action="store_true",
                                         help="Activar macro de depot al final de la ruta (o via 'action depot' en script)")
    # ── Waypoints custom ─────────────────────────────────────────────
    ap.add_argument("--waypoints",        default=None, dest="waypoints",
                                         help="Fichero JSON con waypoints custom adicionales (ej. cache/custom_waypoints.json)")

    args = ap.parse_args()

    _log("=" * 60)
    _log(f"  Auto Walker — destino: '{args.dest}'"
         + (f"  [LOOP {args.loop_mode} x{chr(8734) if args.loop_count==0 else args.loop_count}]" if args.loop else ""))
    if args.anti_afk:
        _log(f"  [ANTI-AFK] Activado — jitter ±{args.afk_jitter*500:.0f}ms por paso")
    _log("=" * 60)

    nav    = WaypointNavigator()
    loader = nav.loader
    cache  = MapCache(loader)

    # ── Cargar waypoints custom ──────────────────────────────────────
    _default_wp = project_root / "cache" / "custom_waypoints.json"
    if _default_wp.exists():
        nav.load_custom_waypoints(_default_wp)
        _log(f"  [WP] {nav._custom_waypoints.__len__()} waypoints custom cargados de cache/custom_waypoints.json")
    if args.waypoints:
        _wp_path = Path(args.waypoints)
        if _wp_path.exists():
            before = len(nav._custom_waypoints)
            nav.load_custom_waypoints(_wp_path)
            _log(f"  [WP] +{len(nav._custom_waypoints)-before} waypoints cargados de {args.waypoints}")
        else:
            _log(f"  [WP] ⚠ Fichero no encontrado: {args.waypoints}")

    # Posición inicial
    if args.x and args.y:
        start = Coordinate(args.x, args.y, args.floor)
        _log(f"  Start (manual): {start}")
    else:
        # Usar primer waypoint que coincida con el destino o posición default Thais
        start = Coordinate(32369, 32241, args.floor)
        _log(f"  Start (default Thais spawn): {start}")

    # Calcular ruta A* desde start hasta el destino
    _log(f"  Buscando waypoint '{args.dest}'…")
    wps = nav.find_waypoints(args.dest, floor=args.floor)
    if not wps:
        # Intentar sin filtro de piso
        wps = nav.find_waypoints(args.dest)
    if not wps:
        _log(f"  [!] Waypoint '{args.dest}' no encontrado. Usando hardcoded Thais Depot.")
        end = Coordinate(32369, 32241, 7)
        dest_label = "Thais Depot (hardcoded)"
    else:
        # Tomar el más cercano al start
        wps_sorted = sorted(wps, key=lambda w: w.coord.distance_to(start))
        best = wps_sorted[0]
        end  = best.coord
        dest_label = best.name
        _log(f"  Waypoint encontrado: {best.name} @ {end}  (dist={end.distance_to(start):.0f})")
        if len(wps) > 1:
            _log(f"  ({len(wps)} coincidencias — usando la más cercana)")

    _log(f"  Calculando ruta A*: {start} --> {end}...")
    result = nav.navigate(start, end)
    if result.found and result.steps:
        route = result.steps
        _log(f"  Ruta A*: {len(route)-1} pasos ({result.total_distance:.1f} tiles)")
    else:
        # A* failed — may be a cross-component multi-floor route (e.g. Venore canal)
        _log("  [!] A* sin resultado — intentando ruta multifloor...")
        try:
            segments = nav.navigate_multifloor(start, end)
            mf_steps: List[Coordinate] = []
            for seg in segments:
                if seg.found and seg.steps:
                    mf_steps.extend(seg.steps)
            if len(mf_steps) >= 2:
                route = mf_steps
                step_count = sum(1 for a, b in zip(route, route[1:])
                                 if (b.x - a.x) != 0 or (b.y - a.y) != 0)
                _log(f"  Ruta multifloor: {len(route)-1} entradas, {step_count} movimientos")
            else:
                _log("  [!] Multifloor sin resultado — ruta directa lineal.")
                route = [start, end]
        except Exception as _mf_exc:
            _log(f"  [!] Multifloor error: {_mf_exc} — ruta directa lineal.")
            route = [start, end]

    _log(f"  Total pasos: {len(route)-1}")
    _log(f"  Start: {route[0]}  ->  End: {route[-1]}")

    # --- Validar que el tile de inicio sea caminable ---
    try:
        wlk = loader.get_walkability(start.z)
        sx, sy = start.to_pixel()
        if not wlk[sy, sx]:
            _log(f"  [!] ADVERTENCIA: El tile de inicio ({start.x},{start.y}) NO es caminable.")
            _log("  [!] Buscando tile caminable mas cercano en radio +-8...")
            best_coord = None
            best_dist  = 9999
            for dy in range(-8, 9):
                for dx in range(-8, 9):
                    nx, ny = sx + dx, sy + dy
                    if 0 <= ny < wlk.shape[0] and 0 <= nx < wlk.shape[1]:
                        if wlk[ny, nx]:
                            d = abs(dx) + abs(dy)
                            if d < best_dist:
                                best_dist  = d
                                best_coord = Coordinate(start.x + dx, start.y + dy, start.z)
            if best_coord:
                _log(f"  [!] Tile caminable mas cercano: ({best_coord.x},{best_coord.y}) d={best_dist}")
                _log(f"  [!] Prueba: --x {best_coord.x} --y {best_coord.y}")
            else:
                _log("  [!] No se encontro tile caminable en radio 8.")
        else:
            _log(f"  [OK] Tile de inicio walkable OK ({start.x},{start.y})")
    except Exception as _we:
        _log(f"  [!] No se pudo validar walkability del tile inicio: {_we}")

    # ── Resolución de ventanas: MONITOR 1=OBS (captura), MONITOR 2=Tibia (inputs) ──────
    _log("")
    _log("  ╔══════════════════════════════════════════════════╗")
    _log("  ║  SETUP DUAL-MONITOR                              ║")
    _log("  ║  Monitor 1 – OBS  → toda la captura/lectura      ║")
    _log("  ║  Monitor 2 – Tibia → todos los inputs/movimiento ║")
    _log("  ╚══════════════════════════════════════════════════╝")

    # Ventana Tibia (monitor 2) — destino de INPUTS
    tibia_hwnd: int = args.tibia_hwnd or 0
    if not tibia_hwnd:
        try:
            import ctypes as _ctypes
            tibia_hwnd = _ctypes.windll.user32.FindWindowW(None, args.target) or 0
            if not tibia_hwnd:
                for _w in list_windows():
                    if "tibia" in _w.title.lower():
                        tibia_hwnd = _w.hwnd
                        _log(f"  [TIBIA]  Ventana inputs auto-detectada: '{_w.title}' hwnd={tibia_hwnd:#010x}")
                        break
            else:
                _log(f"  [TIBIA]  Ventana inputs: '{args.target}' hwnd={tibia_hwnd:#010x}")
        except Exception:
            pass
    else:
        _log(f"  [TIBIA]  Ventana inputs (explícita): hwnd={tibia_hwnd:#010x}")
    if not tibia_hwnd:
        _log("  [TIBIA]  ⚠ Ventana Tibia NO encontrada — inputs broadcast (sin hwnd)")

    ctrl = InputController(
        target_title=args.target,
        key_delay=0.05,
        move_mode=args.move_mode,
        input_method=args.input_method,
        fg_delay=args.fg_delay,
    )
    # Si se encontró hwnd explícito, sobrescribir el hwnd del controller
    if tibia_hwnd and hasattr(ctrl, '_hwnd'):
        ctrl._hwnd = tibia_hwnd

    # Detector de movimiento opcional (pixel-diff)
    # Auto-habilitar si se especificó --source explícitamente (no solo el default)
    _source_was_set = "--source" in sys.argv or "--verify-pos" in sys.argv
    if _source_was_set and not args.verify_pos:
        args.verify_pos = True
        _log("  [MOTION] Auto-habilitando --verify-pos (detectado --source en args)")
    detector = None
    if args.verify_pos:
        # Ventana OBS projector (monitor 1) — fuente de CAPTURA
        # NUNCA mezclar con tibia_hwnd — son monitores distintos
        _proj_hwnd_arg = args.projector_hwnd or 0
        if args.source == "obs-projector":
            if _proj_hwnd_arg:
                _log(f"  [OBS]    Proyector OBS (explícito):   hwnd={_proj_hwnd_arg:#010x}")
            else:
                _log("  [OBS]    Proyector OBS: buscando ventana por título…")
        elif args.source == "virtual-cam":
            _log("  [OBS]    Fuente captura: OBS Virtual Camera")
        elif args.source == "obs-ws":
            _log(f"  [OBS]    Fuente captura: OBS WebSocket {args.obs_host}:{args.obs_port}")

        detector = MotionDetector(
            obs_source=args.source,
            cam_index=args.obs_cam,
            capture_size=320,
            move_threshold=args.move_threshold,
            hwnd=0,              # hwnd Tibia NO se pasa al detector (monitores separados)
            proj_hwnd=_proj_hwnd_arg,  # hwnd OBS proyector (monitor 1)
            obs_host=args.obs_host,
            obs_port=args.obs_port,
            obs_password=args.obs_password,
            obs_scene_source=args.obs_scene_source,
        )
        if detector._ok:
            # Test rápido: captura inicial
            f0 = detector._capture()
            import numpy as _np
            if f0 is not None:
                brightness = float(_np.mean(f0))
                _log(f"  [MOTION] {args.source} listo — brightness={brightness:.1f}")
            else:
                _log(f"  [MOTION] {args.source} conectado pero sin frame — ¿ventana visible?")
                detector = None
        else:
            _log(f"  [MOTION] No se pudo conectar ({args.source}) — detector desactivado")
            detector = None

    # Verificador de coordenadas OCR (opcional)
    coord_tracker = None
    if args.verify_coords:
        coord_source = args.source if args.source == "obs-ws" else "obs-ws"
        _log(f"  [COORDS] Iniciando verificador de coordenadas (fuente: {coord_source})…")
        _log("  [COORDS] Asegúrate de haber calibrado el ROI: python src/calibrator.py")
        ct = CoordTracker(
            source=coord_source,
            obs_host=args.obs_host,
            obs_port=args.obs_port,
            obs_password=args.obs_password,
            obs_scene_source=args.obs_scene_source,
        )
        if ct.connect():
            coord_tracker = ct
        else:
            _log("  [COORDS] No se pudo iniciar — verificación de coords desactivada")

    # ── Auto HP/MP healer ────────────────────────────────────────────────────
    healer = None
    heal_vk = mana_vk = 0
    if args.heal_hp_pct > 0 or args.mana_mp_pct > 0:
        healer   = HpMpDetector()
        heal_vk  = _hotkey_vk(args.heal_hotkey)
        mana_vk  = _hotkey_vk(args.mana_hotkey)
        _log(f"  [HEAL] HP<{args.heal_hp_pct}% → {args.heal_hotkey.upper()}"
             + (f"  |  MP<{args.mana_mp_pct}% → {args.mana_hotkey.upper()}"
                if args.mana_mp_pct > 0 else "") )
        if not args.verify_pos:
            _log("  [HEAL] ⚠ Agrega --verify-pos para que el detector OBS provea frames al healer")

    walker = AutoWalker(
        route=route,
        ctrl=ctrl,
        cache=cache,
        dest_name=dest_label,
        step_interval=args.interval,
        dry_run=args.dry_run,
        detector=detector,
        loop=args.loop,
        loop_count=args.loop_count,
        loop_mode=args.loop_mode,
        loop_delay=args.loop_delay,
        adaptive=args.adaptive,
        navigator=nav,
        recalib_drift=args.recalib_drift,
    )
    if coord_tracker:
        walker.coord_tracker = coord_tracker
        walker.coord_tol     = args.coord_tol
    if healer is not None:
        walker.healer      = healer
        walker.heal_hp_pct = args.heal_hp_pct
        walker.heal_vk     = heal_vk
        walker.mana_mp_pct = args.mana_mp_pct
        walker.mana_vk     = mana_vk
        walker._heal_steps = args.heal_steps
    walker.afk_jitter        = args.afk_jitter if args.anti_afk else 0.0
    walker.combat_hold_secs  = args.combat_hold_secs
    walker._max_combat_holds = args.max_combat_holds
    walker.recalib_interval  = args.recalib_interval
    if args.combat_hold_secs != 2.5 or args.max_combat_holds != 6:
        _log(f"  [COMBAT-HOLD] espera={args.combat_hold_secs:.1f}s  max={args.max_combat_holds} intentos/paso")

    # ── MinimapComparator (template matching en tiempo real) ──────────────────────
    if getattr(args, "minimap_pos", False):
        if MinimapRadar is None:
            _log("  [MINIMAP] ⚠ MinimapRadar no disponible — instala opencv-python")
        elif detector is None:
            _log("  [MINIMAP] ⚠ Agrega --source virtual-cam para activar comparison")
        else:
            _mm_cfg = MinimapCfg.load() if MinimapCfg is not None else None
            if _mm_cfg is not None:
                if args.minimap_tiles_wide > 0:
                    _mm_cfg.tiles_wide = args.minimap_tiles_wide
                _mm_cfg.floor = args.floor
                _mm_radar = MinimapRadar(loader, config=_mm_cfg)
                walker.minimap_cmp = MinimapComparator(_mm_radar, detector.get_raw_frame)
                _log(f"  [MINIMAP] Comparador activo — ROI={_mm_cfg.roi}  tiles_wide={_mm_cfg.tiles_wide}")
                _log("  [MINIMAP] Punto ● amarillo=esperado  ● cian=real (template matching)")
                _log("  [MINIMAP] Si las coords son incorrectas: python src/calibrator.py --mode minimap")
            else:
                _log("  [MINIMAP] ⚠ MinimapConfig no se pudo cargar")

    # ── Cavebot: combate, loot y condiciones ─────────────────────────────
    # Los 3 módulos corren en hilos de fondo y comparten la misma fuente OBS
    # (detector.get_raw_frame) que ya usa el walker para pixel-diff.
    _frame_src = detector   # MotionDetector — None si --verify-pos no está activo

    combat_mgr:    Optional[Any] = None
    looter_mgr:    Optional[Any] = None
    cond_mon:      Optional[Any] = None

    if args.combat and CombatManager is not None:
        _cc = CombatConfig.load()
        _cc.attack_vk  = args.combat_vk
        _cc.hp_flee_pct = args.flee_hp
        _cc.flee_vk    = args.flee_vk
        combat_mgr = CombatManager(ctrl, healer, _cc)
        if _frame_src is not None:
            combat_mgr.set_frame_getter(_frame_src.get_raw_frame)
        elif not args.verify_pos:
            _log("  [COMBAT] ⚠ Agrega --verify-pos para proveer frames OBS al combat manager")
        combat_mgr.start()
        walker._combat_mgr = combat_mgr
        _log(f"  [COMBAT] Combate automático activado")
    elif args.combat and CombatManager is None:
        _log("  [COMBAT] ⚠ src/combat_manager.py no disponible")

    if args.loot and Looter is not None:
        _lc = LootConfig.load()
        _lc.loot_mode  = args.loot_mode
        _lc.loot_delay = args.loot_delay
        looter_mgr = Looter(ctrl, _lc)
        if _frame_src is not None:
            looter_mgr.set_frame_getter(_frame_src.get_raw_frame)
        elif not args.verify_pos:
            _log("  [LOOT] ⚠ Agrega --verify-pos para proveer frames OBS al looter")
        if coord_tracker is not None:
            looter_mgr.set_player_getter(coord_tracker.get_position)
        # Pausar / reanudar el walker mientras se lootea
        looter_mgr.on_loot_start  = lambda: setattr(walker, 'paused', True)
        looter_mgr.on_loot_finish = lambda: setattr(walker, 'paused', False)
        looter_mgr.start()
        walker._looter_mgr = looter_mgr
        _log(f"  [LOOT] Looter automático activado (modo: {_lc.loot_mode})")
    elif args.loot and Looter is None:
        _log("  [LOOT] ⚠ src/looter.py no disponible")

    if args.conditions and ConditionMonitor is not None:
        cond_mon = ConditionMonitor(ctrl)
        if _frame_src is not None:
            cond_mon.set_frame_getter(_frame_src.get_raw_frame)
        elif not args.verify_pos:
            _log("  [COND] ⚠ Agrega --verify-pos para proveer frames OBS al monitor")
        if args.poison_vk:
            cond_mon.add_reaction("poison",   vk=args.poison_vk,   cooldown=3.0, label=f"antídoto (VK={args.poison_vk:#x})")
        if args.paralyze_vk:
            cond_mon.add_reaction("paralyze", vk=args.paralyze_vk, cooldown=4.0, label=f"utani hur (VK={args.paralyze_vk:#x})")
        if args.burning_vk:
            cond_mon.add_reaction("burning",  vk=args.burning_vk,  cooldown=3.0, label=f"cura quemadura (VK={args.burning_vk:#x})")
        cond_mon.start()
        _log("  [COND] Monitor de condiciones activado")
    elif args.conditions and ConditionMonitor is None:
        _log("  [COND] ⚠ src/condition_monitor.py no disponible")

    # ── Cuenta atrás (útil en modo scancode para dar tiempo de poner foco) ──
    delay = args.start_delay
    if args.input_method == "scancode" and delay > 0:
        import sys as _sys
        print(f"\n⚠  SCANCODE mode: haz clic en la ventana de Tibia ahora.", flush=True)
        for i in range(delay, 0, -1):
            print(f"   Iniciando en {i}...", end="\r", flush=True)
            time.sleep(1)
        print("   Iniciando... ¡ya!         ", flush=True)
        print()

    # ── Modo script: reemplaza al walker normal ───────────────────────────
    if getattr(args, "script", None):
        _script_path = Path(args.script)
        if not _script_path.exists():
            _log(f"  [SCRIPT] ⚠ Fichero no encontrado: {_script_path}")
        else:
            _instructions = ScriptParser.parse_file(_script_path)
            _log(f"  [SCRIPT] Cargadas {len(_instructions)} instrucciones de {_script_path.name}")
            _script_depot_mgr = None
            if args.depot and DepotManager is not None:
                _script_depot_mgr = DepotManager(ctrl)
                if detector is not None:
                    _script_depot_mgr.set_frame_getter(detector.get_raw_frame)
            executor = ScriptExecutor(
                instructions=_instructions,
                ctrl=ctrl,
                navigator=nav,
                step_interval=args.interval,
                healer=healer,
                frame_getter=detector.get_raw_frame if detector is not None else None,
                coord_tracker=coord_tracker if args.verify_coords else None,
                depot_manager=_script_depot_mgr,
                dry_run=args.dry_run,
                afk_jitter=args.afk_jitter if args.anti_afk else 0.0,
                motion_detector=detector,   # habilita stuck detection en cada segmento
                rope_hotkey_vk=getattr(args, "rope_vk", 0),
                shovel_hotkey_vk=getattr(args, "shovel_vk", 0),
            )
            if args.x and args.y:
                executor.set_start_position(Coordinate(args.x, args.y, args.floor))
            executor.run()
    else:
        walker.run()
        # ── Depot macro (modo walker normal) ──────────────────────────
        if args.depot and DepotManager is not None:
            _log("  [DEPOT] Iniciando macro de depot post-ruta…")
            _dm = DepotManager(ctrl)
            _dm.set_log_callback(_log)
            if detector is not None:
                _dm.set_frame_getter(detector.get_raw_frame)
            _dm.run_depot_cycle(
                player_pos=coord_tracker.get_position() if coord_tracker else None
            )

    # ── Limpiar módulos de cavebot al salir ──────────────────────────────
    if combat_mgr is not None:
        combat_mgr.stop()
    if looter_mgr is not None:
        looter_mgr.stop()
    if cond_mon is not None:
        cond_mon.stop()
    if walker.minimap_cmp is not None:
        walker.minimap_cmp.stop()


if __name__ == "__main__":
    main()
