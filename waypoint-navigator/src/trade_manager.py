"""
src/trade_manager.py - NPC Trade automation.

Abre el dialogo de trade con un NPC via teclado, compra y vende items
usando template matching visual en el panel de trade y clicks/tipeo
en las coordenadas de los botones y campos de cantidad.

Pipeline:
  1. open_trade(greet_text)  -> Enter + greet_text + Enter -> espera ventana
  2. execute_sell_list()     -> para cada item en sell_list:
        - template match en item_list_roi (scroll si no visible)
        - click en la fila del item
        - set_quantity(n)    -> click campo + Ctrl+A + type(n)
        - click boton Sell
  3. execute_buy_list()      -> idem con boton Buy + validacion de precio
  4. close_trade()           -> click Cancel o ESC

Setup de templates:
  cache/templates/trade_items/ -> iconos recortados del panel trade
  Nombre del fichero = nombre del item (ej: health_potion.png)
  Como obtener el template:
    1. Abre el trade con el NPC
    2. Captura el frame via examples/capture_templates.py
    3. Recorta el icono del item (32x32 px aprox)
    4. Guarda en cache/templates/trade_items/

Coordenadas de botones y ROIs (1920x1080 cliente Tibia estandar):
  Ajustar via trade_config.json o usando --diag=trade en auto_walker.
"""

from __future__ import annotations

import json
import logging
import re
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.humanizer import jittered_sleep

import cv2
import numpy as np

_log = logging.getLogger("wn")

# ---------------------------------------------------------------------------
from src.config_paths import TRADE_CONFIG, TEMPLATES_DIR as _TEMPLATES_DIR

TRADE_CONFIG_FILE = TRADE_CONFIG

# VK codes necesarios
_VK_CTRL  = 0x11
_VK_A     = 0x41
_VK_ENTER = 0x0D
_VK_ESC   = 0x1B
_VK_DEL   = 0x2E

# Regex para extraer el primer numero entero de un bloque de texto OCR
_PRICE_RE = re.compile(r'(\d[\d,\.]*)')


# ---------------------------------------------------------------------------
@dataclass
class TradeItem:
    """Un item en la lista de compra o venta."""
    name: str                          # nombre del template (sin extension)
    quantity: int = 1                  # cantidad a comprar/vender
    max_price: int = 0                 # 0 = sin limite de precio; solo para buy
    item_pos: Optional[List[int]] = None  # [x, y] fijo en pantalla — salta deteccion


@dataclass
class TradeConfig:
    """
    Configuracion del motor de trade.

    Todos los ROIs son [x, y, w, h] en pixeles para resolucion 1920x1080.
    Los botones son [x, y] del centro del boton en esa misma resolucion.
    Se escalan automaticamente si el frame tiene otra resolucion.

    window_roi:
        Area donde aparece el panel NPC Trade cuando esta abierto.
        Se usa para detectar si la ventana esta visible (suma de pixeles
        del header = color distinctivo marron/dorado de Tibia).

    item_list_roi:
        Lista scrollable de items del NPC (izquierda del panel).

    qty_field_roi:
        Campo de entrada de cantidad (abajo del panel).

    price_unit_roi:
        Etiqueta con el precio por unidad (encima del boton buy).

    buy_btn_pos, sell_btn_pos, cancel_btn_pos:
        Coordenadas [x, y] de cada boton de accion.

    scroll_pos:
        Coordenadas [x, y] donde se aplica el scroll de la lista de items.

    buy_list, sell_list:
        Items a comprar / vender en cada ciclo.
    """

    # ROIs [x, y, w, h] en 1920x1080
    window_roi:     List[int] = field(default_factory=lambda: [610, 280,  700, 500])
    item_list_roi:  List[int] = field(default_factory=lambda: [620, 320,  460, 350])
    qty_field_roi:  List[int] = field(default_factory=lambda: [900, 693,  120,  28])
    price_unit_roi: List[int] = field(default_factory=lambda: [900, 658,  190,  26])

    # Posiciones de botones [x, y]
    buy_btn_pos:    List[int] = field(default_factory=lambda: [943, 736])
    sell_btn_pos:   List[int] = field(default_factory=lambda: [1015, 736])
    cancel_btn_pos: List[int] = field(default_factory=lambda: [1087, 736])
    scroll_pos:     List[int] = field(default_factory=lambda: [760, 500])

    # Search-field trade approach (no templates needed)
    use_search_field: bool = True
    buy_tab_pos:      List[int] = field(default_factory=lambda: [700, 305])
    sell_tab_pos:     List[int] = field(default_factory=lambda: [850, 305])
    search_field_pos: List[int] = field(default_factory=lambda: [850, 350])
    first_item_pos:   List[int] = field(default_factory=lambda: [850, 400])
    ok_btn_pos:       List[int] = field(default_factory=lambda: [850, 730])

    # Resolucion de referencia (escala automaticamente)
    ref_width:  int = 1920
    ref_height: int = 1080

    # Template matching
    templates_dir: str = str(_TEMPLATES_DIR)
    confidence: float = 0.62

    # Delays (segundos)
    click_delay:    float = 0.12   # pausa entre acciones
    greet_delay:    float = 1.2    # espera tras saludo al NPC
    window_timeout: float = 5.0    # timeout esperando que abra la ventana
    scroll_steps:   int   = 3      # scrolls por intento de busqueda

    # Balance ON — usar oro del banco al comprar (checkbox "Deposit Gold")
    use_balance:          bool      = False
    balance_checkbox_pos: List[int] = field(default_factory=list)  # [x, y] del checkbox

    # Texto de saludo para abrir el trade (configurable por NPC)
    greet_text: str = "trade"

    # Listas de items
    buy_list:  List[Dict[str, Any]] = field(default_factory=list)
    sell_list: List[Dict[str, Any]] = field(default_factory=list)

    # Deteccion de ventana por color (header dorado/marron de Tibia)
    window_header_color_hsv: List[int] = field(
        default_factory=lambda: [20, 100, 150]   # [H, S_min, V_min]
    )
    window_min_pixels: int = 40

    def validate(self) -> None:
        """Raise ``ValueError`` on invalid config values."""
        for name in ("window_roi", "item_list_roi", "qty_field_roi", "price_unit_roi"):
            roi = getattr(self, name)
            if len(roi) != 4:
                raise ValueError(f"{name} must have 4 elements, got {len(roi)}")
            if any(v < 0 for v in roi):
                raise ValueError(f"{name} contains negative values: {roi}")
        _pos_fields = (
            "buy_btn_pos", "sell_btn_pos", "cancel_btn_pos", "scroll_pos",
            "buy_tab_pos", "sell_tab_pos", "search_field_pos",
            "first_item_pos", "ok_btn_pos",
        )
        for name in _pos_fields:
            pos = getattr(self, name)
            if len(pos) != 2:
                raise ValueError(f"{name} must have 2 elements, got {len(pos)}")
            if any(v < 0 for v in pos):
                raise ValueError(f"{name} contains negative values: {pos}")
        if self.ref_width <= 0:
            raise ValueError(f"ref_width must be > 0, got {self.ref_width}")
        if self.ref_height <= 0:
            raise ValueError(f"ref_height must be > 0, got {self.ref_height}")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if self.window_timeout <= 0:
            raise ValueError(f"window_timeout must be > 0, got {self.window_timeout}")
        if self.click_delay < 0:
            raise ValueError(f"click_delay must be >= 0, got {self.click_delay}")
        if self.scroll_steps < 0:
            raise ValueError(f"scroll_steps must be >= 0, got {self.scroll_steps}")

    def save(self, path: Path = TRADE_CONFIG_FILE) -> None:
        import dataclasses, os
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(self), f, indent=2)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path = TRADE_CONFIG_FILE) -> "TradeConfig":
        if not path.exists():
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        cfg.validate()
        return cfg

    def buy_items(self) -> List[TradeItem]:
        return [TradeItem(**i) for i in self.buy_list]

    def sell_items(self) -> List[TradeItem]:
        return [TradeItem(**i) for i in self.sell_list]


# ---------------------------------------------------------------------------
class TradeItemDetector:
    """
    Detecta items en el panel de trade usando template matching (cv2).
    Retorna la posicion (frame_x, frame_y) del centro del item encontrado,
    o None si no se encuentra.
    """

    def __init__(
        self,
        config: TradeConfig,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cfg = config
        self._log_fn = log_fn or print
        self._templates: Dict[str, np.ndarray] = {}
        self._ocr_reader: Any = None  # lazy-initialized easyocr.Reader
        self._load_templates()

    def _load_templates(self) -> None:
        tdir = Path(self._cfg.templates_dir) / "trade_items"
        tdir.mkdir(parents=True, exist_ok=True)
        self._templates = {}
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
            for p in sorted(tdir.glob(ext)):
                img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                if img is not None:
                    self._templates[p.stem] = img
        if self._templates:
            self._log_fn(f"  [TRADE] Templates: {sorted(self._templates)}")
        else:
            self._log_fn(f"  [TRADE] Sin templates en {tdir} — agrega iconos de items.")

    def reload(self) -> None:
        self._load_templates()

    def _scale_roi(
        self, frame: np.ndarray, roi: List[int]
    ) -> Tuple[int, int, int, int]:
        h, w = frame.shape[:2]
        rx = w / self._cfg.ref_width
        ry = h / self._cfg.ref_height
        return int(roi[0]*rx), int(roi[1]*ry), int(roi[2]*rx), int(roi[3]*ry)

    def find_item(
        self, frame: np.ndarray, name: str
    ) -> Optional[Tuple[int, int]]:
        """
        Busca el template `name` dentro de item_list_roi.
        Primero intenta template matching; si falla o no hay template,
        usa OCR (EasyOCR) para leer el nombre del item en la lista.
        Retorna (cx, cy) en coordenadas absolutas del frame, o None.
        """
        if frame is None:
            return None

        rx, ry, rw, rh = self._scale_roi(frame, self._cfg.item_list_roi)
        roi = frame[ry: ry + rh, rx: rx + rw]
        if roi.size == 0:
            return None

        # --- Primary: template matching ---
        tmpl = self._templates.get(name)
        if tmpl is not None and tmpl.shape[0] <= roi.shape[0] and tmpl.shape[1] <= roi.shape[1]:
            result = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val >= self._cfg.confidence:
                cx = rx + max_loc[0] + tmpl.shape[1] // 2
                cy = ry + max_loc[1] + tmpl.shape[0] // 2
                return cx, cy

        # --- Fallback: OCR text search ---
        return self._find_item_ocr(roi, name, rx, ry)

    @staticmethod
    def _has_ocr_signal(binary_roi: np.ndarray, *, min_pixels: int = 12) -> bool:
        return int(cv2.countNonZero(binary_roi)) >= min_pixels

    def _find_item_ocr(
        self,
        roi: np.ndarray,
        name: str,
        roi_x: int,
        roi_y: int,
    ) -> Optional[Tuple[int, int]]:
        """
        Fallback: use EasyOCR to find item by name text within the item list ROI.

        Converts the item name (e.g. "health_potion") to a display string
        ("health potion") and does a case-insensitive substring search across
        all OCR detections.  Returns the absolute frame center of the first match.
        """
        try:
            # Enhance contrast for dark Tibia UI text
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, bw = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY)
            if not self._has_ocr_signal(bw):
                return None
            if self._ocr_reader is None:
                import easyocr
                self._ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            results = self._ocr_reader.readtext(bw)
            # Item names in config use underscores; Tibia displays them with spaces
            search_text = name.replace("_", " ").lower()
            for bbox, text, conf in results:
                if search_text in text.lower():
                    # bbox = [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
                    xs = [pt[0] for pt in bbox]
                    ys = [pt[1] for pt in bbox]
                    cx = roi_x + int(sum(xs) / len(xs))
                    cy = roi_y + int(sum(ys) / len(ys))
                    self._log_fn(
                        f"  [TRADE] OCR encontró '{text}' (conf={conf:.2f}) → ({cx},{cy})"
                    )
                    return cx, cy
        except Exception as e:
            self._log_fn(f"  [TRADE] OCR error buscando '{name}': {e}")
        return None

    def is_trade_window_open(self, frame: np.ndarray) -> bool:
        """
        Comprueba si la ventana de trade esta visible detectando el color
        caracteristico del header del panel (tono dorado/marron de Tibia).
        """
        if frame is None:
            return False
        rx, ry, rw, rh = self._scale_roi(frame, self._cfg.window_roi)
        roi = frame[ry: ry + rh, rx: rx + rw]
        if roi.size == 0:
            return False
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        h_target, s_min, v_min = self._cfg.window_header_color_hsv
        mask = cv2.inRange(
            hsv,
            np.array([max(0, h_target - 15), s_min, v_min]),
            np.array([min(179, h_target + 15), 255, 255]),
        )
        return int(mask.sum() / 255) >= self._cfg.window_min_pixels

    def read_price(self, frame: np.ndarray) -> Optional[int]:
        """
        Lee el precio por unidad en price_unit_roi usando thresholding + OCR.
        Requiere easyocr (ya en requirements.txt). Retorna None si falla.
        """
        if frame is None:
            return None
        try:
            rx, ry, rw, rh = self._scale_roi(frame, self._cfg.price_unit_roi)
            roi = frame[ry: ry + rh, rx: rx + rw]
            if roi.size == 0:
                return None
            # Binarizar para mejorar OCR de texto numerico dorado sobre fondo oscuro
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, bw = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
            if not self._has_ocr_signal(bw):
                return None
            # Lazy-import easyocr para no penalizar import del modulo
            # Use cached reader to avoid 1-3s init cost per call
            if self._ocr_reader is None:
                import easyocr
                self._ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            reader = self._ocr_reader
            results = reader.readtext(bw)
            for _, text, _ in results:
                m = _PRICE_RE.search(text.replace(",", "").replace(".", ""))
                if m:
                    return int(m.group(1))
        except Exception:
            _log.debug("read_price OCR failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
class TradeWindowNotFound(RuntimeError):
    """Se lanza cuando la ventana de trade no aparece dentro del timeout."""


class TradeManager:
    """
    Automatiza ciclos de compra y venta con un NPC de Tibia.

    Uso tipico:
        cfg = TradeConfig.load()
        cfg.buy_list  = [{"name": "health_potion", "quantity": 50, "max_price": 120}]
        cfg.sell_list = [{"name": "dead_goblin_ear", "quantity": 0}]   # 0 = all
        tm = TradeManager(ctrl, cfg)
        tm.set_frame_getter(lambda: obs_source.get_frame())
        tm.run_cycle()   # abre trade, vende, compra, cierra

    ctrl debe ser una instancia de InputController con hwnd conectado.
    frame_getter debe devolver un frame BGR uint8 (numpy ndarray) o None.
    """

    def __init__(
        self,
        ctrl: Any,                          # InputController
        config: Optional[TradeConfig] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._ctrl    = ctrl
        self._cfg     = config or TradeConfig()
        self._log_cb: Optional[Callable[[str], None]] = log_fn
        self._frame_getter: Optional[Callable[[], Optional[np.ndarray]]] = None
        self._detector = TradeItemDetector(self._cfg, log_fn=self._log)

        # Estadisticas del ultimo ciclo
        self.last_bought: Dict[str, int] = {}
        self.last_sold:   Dict[str, int] = {}

    def set_log_callback(self, cb: Callable[[str], None]) -> None:
        """Register a log callback (replaces direct print output)."""
        self._log_cb = cb
        # Propagate to detector so template-load messages also route through
        self._detector._log_fn = cb

    def _log(self, msg: str) -> None:
        if self._log_cb:
            self._log_cb(msg)
        else:
            print(msg)

    def set_frame_getter(self, getter: Callable[[], Optional[np.ndarray]]) -> None:
        self._frame_getter = getter

    def _frame(self) -> Optional[np.ndarray]:
        if self._frame_getter is None:
            return None
        try:
            return self._frame_getter()
        except Exception as _e:
            _log.debug("TradeManager frame_getter error (ignorado): %s", _e)
            return None

    def _scale_pos(self, pos: List[int]) -> Tuple[int, int]:
        """Escala coordenadas de 1920x1080 al frame real."""
        frame = self._frame()
        if frame is None:
            return pos[0], pos[1]
        h, w = frame.shape[:2]
        return int(pos[0] * w / self._cfg.ref_width), int(pos[1] * h / self._cfg.ref_height)

    # ── Input helpers ────────────────────────────────────────────────────────

    def _click(self, pos: List[int], button: str = "left") -> None:
        x, y = self._scale_pos(pos)
        self._ctrl.click(x, y, button=button)
        time.sleep(self._cfg.click_delay * random.uniform(0.8, 1.25))

    def _ctrl_a(self) -> None:
        """Selecciona todo en el campo de cantidad (Ctrl+A via PostMessage)."""
        import ctypes
        if not self._ctrl.is_connected():
            return
        hwnd = self._ctrl.hwnd
        WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
        # Always release Ctrl even if an intermediate PostMessageW raises,
        # to avoid leaving Ctrl "stuck" in the game's message queue.
        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, _VK_CTRL, 0)
        try:
            jittered_sleep(0.03)
            ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, _VK_A, 0)
            jittered_sleep(0.03)
            ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, _VK_A, 0)
        finally:
            ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, _VK_CTRL, 0)
        jittered_sleep(0.03)

    def _scroll_list(self, direction: str = "down") -> None:
        """Desplaza la lista de items con la rueda del raton."""
        import ctypes
        if not self._ctrl.is_connected():
            return
        hwnd = self._ctrl.hwnd
        x, y = self._scale_pos(self._cfg.scroll_pos)
        WM_MOUSEWHEEL = 0x020A
        delta = -120 if direction == "down" else 120  # WHEEL_DELTA = 120
        # Pass plain Python ints — PostMessageW expects integer wParam/lParam,
        # not ctypes.c_uint objects (passing the struct can silently invert
        # scroll direction on some ctypes/Windows combinations).
        wparam = ctypes.c_uint(delta << 16).value
        lparam = ctypes.c_uint((y << 16) | (x & 0xFFFF)).value
        for _ in range(self._cfg.scroll_steps):
            ctypes.windll.user32.PostMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
            jittered_sleep(0.06)

    def _set_quantity(self, n: int) -> None:
        """Selecciona el campo de cantidad, borra y escribe n."""
        roi = self._cfg.qty_field_roi
        center = [roi[0] + roi[2] // 2, roi[1] + roi[3] // 2]
        self._click(center)   # click en el centro del campo
        jittered_sleep(0.05)
        self._ctrl_a()                              # Ctrl+A para seleccionar
        self._ctrl.press_key(_VK_DEL)               # borrar
        jittered_sleep(0.05)
        self._ctrl.type_text(str(n))                # escribir cantidad

    # ── Ventana de trade ─────────────────────────────────────────────────────

    def open_trade(self, greet_text: Optional[str] = None) -> bool:
        """
        Saluda al NPC y espera a que se abra el panel de trade.
        Devuelve True si la ventana aparecio dentro del timeout.
        """
        text = greet_text or self._cfg.greet_text
        self._ctrl.press_key(_VK_ENTER)
        jittered_sleep(0.15)
        self._ctrl.type_text(text)
        jittered_sleep(0.05)
        self._ctrl.press_key(_VK_ENTER)
        self._log(f"  [TRADE] Saludo enviado: '{text}'")
        return self.wait_for_window()

    def wait_for_window(self) -> bool:
        """Espera hasta que la ventana de trade sea visible."""
        deadline = time.monotonic() + self._cfg.window_timeout
        while time.monotonic() < deadline:
            frame = self._frame()
            if frame is not None and self._detector.is_trade_window_open(frame):
                self._log("  [TRADE] Ventana de trade detectada")
                return True
            # Use plain sleep (not jittered) so the poll interval is precise
            # and window_timeout is respected within a single poll period.
            time.sleep(0.2)
        self._log(f"  [TRADE] Timeout: ventana no aparecio en {self._cfg.window_timeout}s")
        return False

    def close_trade(self) -> None:
        """Cierra el panel de trade con Cancel o ESC."""
        try:
            self._click(self._cfg.cancel_btn_pos)
        except Exception as _e:
            _log.debug("close_trade cancel-click failed (ignorado): %s", _e)
        jittered_sleep(0.05)
        self._ctrl.press_key(_VK_ESC)
        self._log("  [TRADE] Trade cerrado")

    def switch_tab(self, mode: str) -> None:
        """Click the Buy or Sell tab in the trade window."""
        pos = self._cfg.buy_tab_pos if mode == "buy" else self._cfg.sell_tab_pos
        self._click(pos)
        jittered_sleep(0.2)

    def _search_and_select_item(self, name: str) -> bool:
        """Type item name in the search field and click the first result."""
        # Click search field
        self._click(self._cfg.search_field_pos)
        jittered_sleep(0.1)
        # Select-all and clear
        self._ctrl_a()
        self._ctrl.press_key(_VK_DEL)
        jittered_sleep(0.05)
        # Type item name
        self._ctrl.type_text(name)
        jittered_sleep(0.5)
        # Verify a result appeared before clicking
        frame = self._frame()
        if frame is not None:
            pos = self._detector.find_item(frame, name)
            if pos is None:
                self._log(f"  [TRADE] '{name}' no aparece en la lista tras búsqueda — skip")
                return False
        # Click first item in the filtered list
        self._click(self._cfg.first_item_pos)
        time.sleep(self._cfg.click_delay * random.uniform(0.8, 1.25))
        self._log(f"  [TRADE] Searched: '{name}' → selected first result")
        return True

    def buy_single_item(self, name: str, quantity: int) -> bool:
        """Buy a single item by name and quantity (trade window must be open)."""
        self.switch_tab("buy")
        item = TradeItem(name=name, quantity=quantity)
        ok = self._execute_transaction(item, "buy")
        if ok:
            self.last_bought[name] = quantity
        return ok

    def sell_single_item(self, name: str, quantity: int) -> bool:
        """Sell a single item by name and quantity (trade window must be open)."""
        self.switch_tab("sell")
        item = TradeItem(name=name, quantity=max(quantity, 1))
        ok = self._execute_transaction(item, "sell")
        if ok:
            self.last_sold[name] = quantity
        return ok

    # ── Operaciones de item ─────────────────────────────────────────────────

    def _find_item_with_scroll(
        self, name: str, max_scrolls: int = 5
    ) -> Optional[Tuple[int, int]]:
        """
        Busca el item en la lista visible; si no lo encuentra hace scroll.
        Retorna (x, y) en coordenadas del frame, o None.
        """
        for _ in range(max_scrolls + 1):
            frame = self._frame()
            pos = self._detector.find_item(frame, name) if frame is not None else None
            if pos is not None:
                return pos
            self._scroll_list("down")
            jittered_sleep(0.1)
        return None

    def _execute_transaction(
        self, item: TradeItem, mode: str
    ) -> bool:
        """
        Selecciona un item, fija la cantidad y pulsa Buy o Sell.
        mode: 'buy' | 'sell'
        Returns True si la accion se completo.
        """
        # Seleccionar item
        if item.item_pos:
            # Posicion fija — click directo sin deteccion
            self._click(item.item_pos)
        elif self._cfg.use_search_field:
            if not self._search_and_select_item(item.name):
                return False
        else:
            pos = self._find_item_with_scroll(item.name)
            if pos is None:
                self._log(f"  [TRADE] Item no encontrado: '{item.name}'")
                return False
            self._ctrl.click(pos[0], pos[1])
            time.sleep(self._cfg.click_delay * random.uniform(0.8, 1.25))

        # Validar precio maximo (solo en buy)
        if mode == "buy" and item.max_price > 0:
            frame = self._frame()
            price = self._detector.read_price(frame) if frame is not None else None
            if price is not None and price > item.max_price:
                self._log(
                    f"  [TRADE] '{item.name}' precio {price} > max {item.max_price} — skip"
                )
                return False

        # Cantidad
        qty = item.quantity if item.quantity > 0 else 1
        self._set_quantity(qty)
        time.sleep(self._cfg.click_delay * random.uniform(0.8, 1.25))

        # Balance ON — activar checkbox "Deposit Gold" antes de comprar
        if mode == "buy" and self._cfg.use_balance and self._cfg.balance_checkbox_pos:
            self._click(self._cfg.balance_checkbox_pos)
            self._log("  [TRADE] Balance ON (Deposit Gold) activado")

        # Accion — item_pos fijo siempre usa buy/sell_btn_pos directamente.
        # En modo search_field: buy usa ok_btn_pos (confirm), sell usa sell_btn_pos.
        # ok_btn_pos y buy_btn_pos pueden ser idénticos pero sell_btn_pos es diferente.
        if item.item_pos or not self._cfg.use_search_field:
            btn = self._cfg.buy_btn_pos if mode == "buy" else self._cfg.sell_btn_pos
        elif mode == "buy":
            btn = self._cfg.ok_btn_pos
        else:
            btn = self._cfg.sell_btn_pos
        self._click(btn)
        self._log(f"  [TRADE] {mode.upper()} {qty}x '{item.name}'")
        time.sleep(self._cfg.click_delay * random.uniform(0.8, 1.25))
        return True

    # ── Ciclo principal ──────────────────────────────────────────────────────

    def execute_sell_list(self) -> Dict[str, int]:
        """Vende todos los items de sell_list. Retorna {name: qty} vendidos."""
        self.switch_tab("sell")
        sold: Dict[str, int] = {}
        for item in self._cfg.sell_items():
            self.switch_tab("sell")
            if self._execute_transaction(item, "sell"):
                sold[item.name] = item.quantity
        self.last_sold = sold
        return sold

    def execute_buy_list(self) -> Dict[str, int]:
        """Compra todos los items de buy_list. Retorna {name: qty} comprados."""
        self.switch_tab("buy")
        bought: Dict[str, int] = {}
        for item in self._cfg.buy_items():
            self.switch_tab("buy")
            if self._execute_transaction(item, "buy"):
                bought[item.name] = item.quantity
        self.last_bought = bought
        return bought

    def _assert_window_open(self, phase: str) -> None:
        """Raise TradeWindowNotFound if the window is no longer visible."""
        frame = self._frame()
        if frame is None or not self._detector.is_trade_window_open(frame):
            raise TradeWindowNotFound(
                f"Ventana de trade cerrada inesperadamente antes de la fase '{phase}'"
            )

    def run_cycle(self, greet_text: Optional[str] = None) -> bool:
        """
        Ciclo completo: abre trade -> vende -> compra -> cierra.
        Devuelve True si el ciclo se completo sin errores criticos.
        """
        self.last_bought = {}
        self.last_sold   = {}
        try:
            if not self.open_trade(greet_text):
                raise TradeWindowNotFound("Ventana de trade no encontrada")
            time.sleep(self._cfg.greet_delay * random.uniform(0.8, 1.25))
            self._assert_window_open("sell")
            self.execute_sell_list()
            self._assert_window_open("buy")
            self.execute_buy_list()
            self.close_trade()
            self._log(
                f"  [TRADE] Ciclo OK — comprado: {self.last_bought}  vendido: {self.last_sold}"
            )
            return True
        except TradeWindowNotFound as e:
            self._log(f"  [TRADE] Error: {e}")
            return False
        except Exception as e:
            self._log(f"  [TRADE] Error inesperado: {e!r}")
            try:
                self.close_trade()
            except Exception as _e:
                _log.debug("run_cycle cleanup close_trade failed (ignorado): %s", _e)
            return False
